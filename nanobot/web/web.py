"""Web interface for nanobot with intent classification."""

from pathlib import Path
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, Response
from loguru import logger

from nanobot.agent import AgentLoop
from nanobot.config import Config
from nanobot.knowledge.intent_routing_store import get_intent_routing_store, IntentRoutingStore
from nanobot.knowledge.store_factory import get_chroma_store
from nanobot.providers import LLMProvider


def diagnose_knowledge_base(workspace_path: Path) -> dict:
    """诊断知识库状态."""
    try:
        # 检查知识库目录
        knowledge_dir = workspace_path / "knowledge"
        chroma_dir = knowledge_dir / "chroma_db"

        status = {
            "available": False,
            "knowledge_dir_exists": knowledge_dir.exists(),
            "chroma_dir_exists": chroma_dir.exists(),
            "total_collections": 0,
            "total_documents": 0,
            "error": None
        }

        if not knowledge_dir.exists():
            status["error"] = "知识库目录不存在"
            return status

        # 尝试初始化ChromaKnowledgeStore
        try:
            store = get_chroma_store(workspace_path)
            status["available"] = True

            # 获取集合信息
            collections = store.chroma_client.list_collections()
            status["total_collections"] = len(collections)

            # 计算总文档数
            total_docs = 0
            for collection in collections:
                try:
                    count = collection.count()
                    total_docs += count
                except:
                    pass
            status["total_documents"] = total_docs

        except Exception as e:
            status["error"] = f"ChromaKnowledgeStore初始化失败: {str(e)}"

    except ImportError as e:
        status = {
            "available": False,
            "error": f"知识库模块导入失败: {str(e)}"
        }
    except Exception as e:
        status = {
            "available": False,
            "error": f"知识库诊断失败: {str(e)}"
        }

    return status


class ConnectionManager:
    """Manage WebSocket connections."""

    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)

    async def send_personal_message(self, message: str, websocket: WebSocket):
        await websocket.send_text(message)

    async def broadcast(self, message: str):
        for connection in self.active_connections:
            await connection.send_text(message)


# Create FastAPI application
web_app = FastAPI(
    title="nanobot Web UI",
    description="Web interface for nanobot"
)

# Create connection manager instance
manager = ConnectionManager()

# Global instances for provider and agent_loop
provider: LLMProvider = None
agent_loop: AgentLoop = None
config: Config = None
intent_routing_store: IntentRoutingStore = None


# ---------------------------------------------------------------------------
# Prometheus metrics endpoint
# ---------------------------------------------------------------------------
@web_app.get("/metrics")
async def prometheus_metrics():
    """暴露 Prometheus 指标端点，供 Prometheus server 抓取。"""
    from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
    # 导入 metrics 模块确保所有指标已注册
    import nanobot.metrics  # noqa: F401
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


def initialize_webui_resources():
    """Initialize resources for webui."""
    global provider, agent_loop, config, intent_routing_store
    from nanobot.config.loader import load_config
    from nanobot.bus.queue import MessageBus
    from nanobot.agent.loop import AgentLoop
    from nanobot.providers.litellm_provider import LiteLLMProvider

    bus = MessageBus()

    config = load_config()

    # Create provider from config
    p = config.get_provider()
    model = config.agents.defaults.model
    if not (p and p.api_key) and not model.startswith("bedrock/"):
        return False

    provider = LiteLLMProvider(
        api_key=p.api_key if p else None,
        api_base=config.get_api_base(),
        default_model=model,
        extra_headers=p.extra_headers if p else None,
        provider_name=config.get_provider_name(),
    )

    agent_loop = AgentLoop(
        bus=bus,
        provider=provider,
        workspace=config.workspace_path,
        brave_api_key=config.tools.web.search.api_key or None,
        exec_config=config.tools.exec,
        restrict_to_workspace=config.tools.restrict_to_workspace,
    )

    # 诊断知识库状态
    knowledge_status = diagnose_knowledge_base(config.workspace_path)
    logger.info(f"[WEB] 📚 知识库状态: {knowledge_status}")

    # 初始化意图路由向量库（tools/skills）
    try:
        intent_routing_store = get_intent_routing_store(config.workspace_path, config)
        tools_count = intent_routing_store.init_tools_index(
            tool_schemas=agent_loop.tools.get_definitions(),
            mcp_servers=config.mcp.servers,
        )
        skills_count = intent_routing_store.init_skills_index(agent_loop.context.skills)
        logger.info(
            f"[WEB] 🧭 意图路由索引初始化完成: tools_docs={tools_count}, skills_chunks={skills_count}"
        )
    except Exception as e:
        logger.error(f"[WEB] ❌ 意图路由索引初始化失败: {e}")

    # 启动定时指标打印（每3秒写入日志，按日期自动分割）
    try:
        from nanobot.metrics import start_metrics_logging
        start_metrics_logging(log_dir="logs", interval_seconds=3)
        logger.info("[WEB] 📊 定时指标打印已启动（间隔3秒，日志目录: logs/）")
    except Exception as e:
        logger.warning(f"[WEB] ⚠️ 定时指标打印启动失败: {e}")

    return True


def load_html_template(template_name: str) -> str:
    """Load HTML template from file."""
    template_path = Path(__file__).parent / "templates" / template_name
    try:
        with open(template_path, 'r', encoding='utf-8') as f:
            return f.read()
    except FileNotFoundError:
        return f"<html><body><h1>Template not found: {template_name}</h1></body></html>"
    except Exception as e:
        return f"<html><body><h1>Error loading template: {str(e)}</h1></body></html>"


@web_app.get("/")
async def get():
    """Serve the Web UI homepage."""
    html_content = load_html_template("index.html")
    return HTMLResponse(content=html_content)


@web_app.get("/api/knowledge/preview")
async def preview_knowledge_item(item_id: str = None, source_url: str = None, file_path: str = None):
    """Preview knowledge item content."""
    try:
        from nanobot.config.loader import load_config
        import os

        config = load_config()
        store = get_chroma_store(config.workspace_path, cfg=config)

        # 根据提供的参数获取文档内容
        if item_id:
            # 通过item_id获取知识条目的完整内容
            full_content = await get_full_document_content(store, item_id)
            if full_content:
                return {
                    "status": "success",
                    "message": "文档预览成功",
                    "item_id": item_id,
                    "content": full_content["content"],
                    "metadata": {
                        "source": "knowledge_base",
                        "title": full_content.get("title", ""),
                        "domain": full_content.get("domain", ""),
                        "category": full_content.get("category", ""),
                        "tags": full_content.get("tags", []),
                        "created_at": full_content.get("created_at", ""),
                        "source_url": full_content.get("source_url", ""),
                        "file_path": full_content.get("file_path", ""),
                        "preview_available": True
                    }
                }
            else:
                return {
                    "status": "error",
                    "message": f"未找到ID为 {item_id} 的知识条目"
                }

        elif source_url:
            # 通过URL获取文档内容
            try:
                # 这里可以实现URL内容抓取，暂时返回模拟内容
                return {
                    "status": "success",
                    "message": "URL文档预览成功",
                    "source_url": source_url,
                    "content": f"URL文档内容预览:\n\n来源: {source_url}\n\n注意：URL内容抓取功能需要进一步实现，当前显示的是模拟内容。",
                    "metadata": {
                        "source": "url",
                        "preview_available": True
                    }
                }
            except Exception as e:
                return {
                    "status": "error",
                    "message": f"获取URL内容失败: {str(e)}"
                }

        elif file_path:
            # 通过文件路径获取文档内容
            try:
                # 安全检查：确保文件路径在工作空间内
                workspace_path = str(config.workspace_path)
                abs_file_path = os.path.abspath(file_path)

                if not abs_file_path.startswith(workspace_path):
                    return {
                        "status": "error",
                        "message": "文件路径超出工作空间范围，拒绝访问"
                    }

                if not os.path.exists(abs_file_path):
                    return {
                        "status": "error",
                        "message": f"文件不存在: {file_path}"
                    }

                # 读取文件内容
                with open(abs_file_path, 'r', encoding='utf-8') as f:
                    content = f.read()

                return {
                    "status": "success",
                    "message": "文件预览成功",
                    "file_path": file_path,
                    "content": content,
                    "metadata": {
                        "source": "file",
                        "file_size": os.path.getsize(abs_file_path),
                        "preview_available": True
                    }
                }
            except UnicodeDecodeError:
                return {
                    "status": "error",
                    "message": "文件编码不支持，无法预览"
                }
            except Exception as e:
                return {
                    "status": "error",
                    "message": f"读取文件失败: {str(e)}"
                }
        else:
            return {
                "status": "error",
                "message": "请提供item_id、source_url或file_path参数"
            }

    except Exception as e:
        return {
            "status": "error",
            "message": f"文档预览失败: {str(e)}"
        }


async def get_full_document_content(store, item_id: str):
    """获取知识条目的完整文档内容."""
    try:
        # 查找该知识条目所属的领域
        domain = None
        all_collections = store.chroma_client.list_collections()

        for coll_info in all_collections:
            coll_name = coll_info.name
            if coll_name.startswith("knowledge_"):
                try:
                    collection = store.chroma_client.get_collection(coll_name)
                    # 查询该集合中是否有该 item_id 的分块
                    results = collection.get(
                        where={"item_id": item_id},
                        include=["documents", "metadatas"]
                    )

                    if results and results["ids"] and len(results["ids"]) > 0:
                        domain = coll_name.replace("knowledge_", "")
                        break
                except Exception as e:
                    logger.warning(f"查询集合 {coll_name} 失败: {str(e)}")
                    continue

        if not domain:
            return None

        # 获取该知识条目的所有分块
        collection = store.chroma_client.get_collection(f"knowledge_{domain}")
        chunks = collection.get(
            where={"item_id": item_id},
            include=["documents", "metadatas"]
        )

        if not chunks or not chunks["ids"]:
            return None

        # 按 chunk_index 排序并合并内容
        chunk_data = []
        metadata = None

        for i in range(len(chunks["ids"])):
            chunk_metadata = chunks["metadatas"][i]
            chunk_document = chunks["documents"][i]
            chunk_index = chunk_metadata.get("chunk_index", 0)

            chunk_data.append({
                "index": chunk_index,
                "text": chunk_document,
                "metadata": chunk_metadata
            })

            # 使用第一个分块的元数据作为整体元数据
            if metadata is None:
                metadata = chunk_metadata

        # 按索引排序
        chunk_data.sort(key=lambda x: x["index"])

        # 合并所有分块的文本
        full_content = " ".join(chunk["text"] for chunk in chunk_data)

        return {
            "content": full_content,
            "title": metadata.get("title", ""),
            "domain": metadata.get("domain", ""),
            "category": metadata.get("category", ""),
            "tags": metadata.get("tags", []),
            "created_at": metadata.get("created_at", ""),
            "updated_at": metadata.get("updated_at", ""),
            "source_url": metadata.get("source_url", ""),
            "file_path": metadata.get("file_path", ""),
            "source": metadata.get("source", ""),
            "priority": metadata.get("priority", 1)
        }

    except Exception as e:
        logger.error(f"获取完整文档内容失败: {str(e)}")
        return None


@web_app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """Handle WebSocket connections with real-time streaming."""
    await manager.connect(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            # Process user message with real-time streaming
            await process_user_message_streaming(data, websocket)
    except WebSocketDisconnect:
        manager.disconnect(websocket)


async def classify_user_intent(user_input: str, websocket: WebSocket) -> str:
    """
    使用LLM对用户意图进行分类
    
    Args:
        user_input: 用户输入
        websocket: WebSocket连接
        
    Returns:
        'A' 表示知识问答，'B' 表示运维操作，'C' 表示故障排查
    """
    intent_prompt = f"""
    你是一个意图分类器。

    任务：根据用户问题判断意图，只输出一个字母：

    A = 知识问答
    概念、原理、配置、参数、教程、文档、对比、选型

    B = 运维查询
    查询当前系统状态，例如 pod、日志、集群状态、运行情况

    C = 故障排查
    系统报错、异常、超时、连接失败、消息积压等问题

    示例：

    Q: rocketmq broker 是什么
    A

    Q: kafka 和 rocketmq 区别
    A

    Q: 查看 rocketmq broker pod
    B

    Q: 查询 broker 日志
    B

    Q: rocketmq 消息积压怎么办
    C

    Q: broker 报 timeout 错误
    C

    用户问题：
    {user_input}

    只输出 A、B 或 C：
    """

    try:
        await websocket.send_text("🧠 正在识别用户意图...\n")

        # 使用全局的provider进行意图分类
        if not provider:
            await websocket.send_text("⚠️ LLM服务未初始化，跳过意图识别\n")
            return "A"  # 默认为问答类

        # 调用LLM进行意图分类
        response = await provider.chat(
            messages=[{"role": "user", "content": intent_prompt}],
            model=config.agents.defaults.model,
            max_tokens=1000,  # 只需要返回A/B/C
            temperature=0.1,  # 低温度确保稳定输出
            purpose="intent_classification",
        )

        intent = response.content.strip().upper()

        # 验证返回结果
        if intent not in ['A', 'B', 'C']:
            await websocket.send_text(f"⚠️ 意图识别结果异常: {intent}，默认为问答类\n")
            return "A"

        intent_type = "问答类"
        if intent == "B":
            intent_type = "运维操作类"
        if intent == "C":
            intent_type = "排障类"

        await websocket.send_text(f"✅ 用户意图识别: {intent_type} ({intent})\n\n")

        return intent

    except Exception as e:
        logger.error(f"意图识别失败: {e}")
        await websocket.send_text(f"⚠️ 意图识别失败: {str(e)}，默认为问答类(A)\n")
        return "A"  # 出错时默认回退到 A


async def process_user_message_streaming(user_input: str, websocket: WebSocket):
    """Process user message with real-time streaming output."""
    import time

    start_time = time.time()

    # Check if provider and agent_loop are initialized
    if not provider or not agent_loop:
        await websocket.send_text("Error: Web UI resources not initialized. Please restart the server.")
        return

    # Send initial processing message
    await websocket.send_text("🤖 AI Agent is processing your request...\n\n")

    # 第一步：用户意图识别
    user_intent = await classify_user_intent(user_input, websocket)

    # 根据意图决定处理流程
    if user_intent == "A":
        # 问答类：查询知识库
        await process_qa_intent(user_input, websocket, start_time)
    elif user_intent == "B":
        # 运维操作：查 tools 索引 top2，作为系统补充上下文进入 loop
        await process_ops_intent(user_input, websocket, start_time)
    elif user_intent == "C":
        # 故障排查：查 skills 索引 top2，作为系统补充上下文进入 loop
        await process_troubleshooting_intent(user_input, websocket, start_time)
    else:
        # 非法值默认 A
        await websocket.send_text(f"⚠️ 意图值非法: {user_intent}，默认按 A 问答类处理\n")
        await process_qa_intent(user_input, websocket, start_time)


def _build_retrieval_context(title: str, results: list[dict], limit: int = 2) -> str | None:
    if not results:
        return None
    top = results[:limit]
    lines = [f"# {title}", "以下是与当前问题最相关的检索结果（top2）："]
    for i, item in enumerate(top, 1):
        meta = item.get("metadata", {}) or {}
        dist = item.get("distance")
        rerank_score = item.get("rerank_score")
        doc = (item.get("document") or "").strip()
        if len(doc) > 1200:
            doc = doc[:1200] + "\n...[内容已截断]"
        lines.append(
            f"\n## {i}. {meta.get('tool_name') or meta.get('skill_name') or item.get('id', '')}\n"
            f"- source: {meta.get('source', '')}\n"
            f"- distance: {dist}\n"
            f"- rerank_score: {rerank_score}\n"
            f"- content:\n{doc}"
        )
    lines.append("\n请优先参考上述结果进行后续推理和工具调用。")
    return "\n".join(lines)


def _rerank_route_candidates(query: str, results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """使用 CrossEncoder 对候选结果重排序，失败时回退距离排序。"""
    if not results:
        return []

    model_path = getattr(getattr(config, "rerank", None), "model_path", "") if config else ""
    if not model_path:
        fallback = sorted(results, key=lambda x: x.get("distance") if x.get("distance") is not None else 1e9)
        for item in fallback:
            dist = item.get("distance")
            item["rerank_score"] = 0.0 if dist is None else float(max(0.0, 1.0 - float(dist)))
        return fallback

    try:
        import math
        from sentence_transformers import CrossEncoder

        reranker = CrossEncoder(model_path)
        pairs = [(query, (item.get("document") or "")) for item in results]
        raw_scores = reranker.predict(pairs)
        for i, score in enumerate(raw_scores):
            rerank_score = float(1 / (1 + math.exp(-float(score))) * 100)
            results[i]["rerank_score"] = rerank_score

        results.sort(key=lambda x: x.get("rerank_score", 0.0), reverse=True)
        return results
    except Exception as e:
        logger.warning(f"[WEB] ops intent rerank failed, fallback to distance sort: {e}")
        fallback = sorted(results, key=lambda x: x.get("distance") if x.get("distance") is not None else 1e9)
        for item in fallback:
            dist = item.get("distance")
            item["rerank_score"] = 0.0 if dist is None else float(max(0.0, 1.0 - float(dist)))
        return fallback


async def process_ops_intent(user_input: str, websocket: WebSocket, start_time: float):
    """处理运维操作意图：tools/skills 联合检索并重排后进入 loop。"""
    import json
    import time

    def _build_ops_preview_items(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
        preview_items: list[dict[str, Any]] = []
        seen_keys: set[str] = set()

        for item in results:
            meta = item.get("metadata", {}) or {}
            skill_name = meta.get("skill_name") or ""
            tool_name = meta.get("tool_name") or ""
            server_name = meta.get("server_name") or ""

            # skills 命中优先展示文件预览
            file_path = meta.get("path") or ""
            if file_path:
                try:
                    fp = Path(file_path).expanduser()
                    if not fp.is_absolute() and config and getattr(config, "workspace_path", None):
                        fp = Path(config.workspace_path) / fp
                    file_path = str(fp.resolve())
                except Exception:
                    file_path = str(file_path)

                dedupe_key = f"file::{file_path}"
                if dedupe_key not in seen_keys:
                    preview_items.append(
                        {
                            "type": "file",
                            "id": file_path,
                            "label": f"📁 预览Skill文件: {skill_name or 'unknown'}",
                            "path": file_path,
                        }
                    )
                    seen_keys.add(dedupe_key)
                continue

            # MCP 工具命中回退展示服务 URL 预览
            server_url = meta.get("server_url") or ""
            if server_url:
                dedupe_key = f"url::{server_url}"
                if dedupe_key not in seen_keys:
                    preview_items.append(
                        {
                            "type": "url",
                            "id": server_url,
                            "label": f"🌐 预览MCP服务: {server_name or tool_name or 'mcp'}",
                            "url": server_url,
                        }
                    )
                    seen_keys.add(dedupe_key)

        return preview_items

    if not intent_routing_store:
        await websocket.send_text("⚠️ tools/skills 索引未初始化，直接进入执行流程。\n")
        return await process_troubleshooting_intent(
            user_input,
            websocket,
            start_time,
            additional_context=None,
            use_skills_retrieval=False,
        )

    tools_results: list[dict[str, Any]] = []
    skills_results: list[dict[str, Any]] = []

    await websocket.send_text("🧰 正在检索工具能力库（top2）...\n")
    try:
        tools_results = intent_routing_store.search_tools(user_input, limit=2)
        await websocket.send_text(f"✅ tools 检索完成，命中 {len(tools_results)} 条\n")
    except Exception as e:
        await websocket.send_text(f"⚠️ tools 检索失败: {str(e)}\n")

    await websocket.send_text("🛠️ 正在检索 skills 库（top2）...\n")
    try:
        skills_results = intent_routing_store.search_skills(user_input, limit=2)
        await websocket.send_text(f"✅ skills 检索完成，命中 {len(skills_results)} 条\n")
    except Exception as e:
        await websocket.send_text(f"⚠️ skills 检索失败: {str(e)}\n")

    merged_results = (tools_results or []) + (skills_results or [])
    if merged_results:
        reranked_results = _rerank_route_candidates(user_input, merged_results)
        additional_context = _build_retrieval_context("Ops/Skills Retrieval Context", reranked_results, limit=2)

        top2_results = reranked_results[:2]
        preview_items = _build_ops_preview_items(top2_results)
        retrieval_markdown = _build_retrieval_context("Ops/Skills Retrieval Preview", top2_results, limit=2) or ""

        knowledge_message = {
            "type": "stream_chunk",
            "content_type": "knowledge",
            "content": f"tools={len(tools_results)}，skills={len(skills_results)}，重排后展示 top2",
            "knowledge_status": "success",
            "knowledge_count": len(top2_results),
            "knowledge_result": retrieval_markdown,
            "preview_items": preview_items,
            "timestamp": time.time(),
            "duration_from_start": round(time.time() - start_time, 3),
        }
        await websocket.send_text(json.dumps(knowledge_message, ensure_ascii=False))

        await websocket.send_text(
            f"✅ 联合检索完成，tools={len(tools_results)}，skills={len(skills_results)}，重排后取 top2\n\n"
        )
    else:
        additional_context = None
        await websocket.send_text("⚠️ tools/skills 均未命中，直接进入执行流程。\n")

        knowledge_message = {
            "type": "stream_chunk",
            "content_type": "knowledge",
            "content": "tools/skills 均未命中",
            "knowledge_status": "no_results",
            "knowledge_count": 0,
            "knowledge_result": "",
            "preview_items": [],
            "timestamp": time.time(),
            "duration_from_start": round(time.time() - start_time, 3),
        }
        await websocket.send_text(json.dumps(knowledge_message, ensure_ascii=False))

    return await process_troubleshooting_intent(
        user_input,
        websocket,
        start_time,
        additional_context=additional_context,
        use_skills_retrieval=False,
    )


async def process_qa_intent(user_input: str, websocket: WebSocket, start_time: float):
    """处理问答类意图：优先查询知识库"""
    import time
    import json
    from nanobot.config.loader import load_config

    try:
        config = load_config()
        store = get_chroma_store(config.workspace_path, cfg=config)
    except RuntimeError as e:
        # CrossEncoder 初始化失败
        error_msg = f"❌ 知识库初始化失败: {str(e)}\n\n服务启动终止，请检查 CrossEncoder 模型配置。\n"
        await websocket.send_text(error_msg)
        # 关闭WebSocket连接
        await websocket.close(code=1011, reason="CrossEncoder initialization failed")
        return
    except Exception as e:
        # 其他初始化错误
        error_msg = f"❌ 知识库初始化失败: {str(e)}\n\n"
        await websocket.send_text(error_msg)
        return

    # 发送知识库查询开始信息
    await websocket.send_text("📚 正在查询知识库...\n")

    # 搜索知识库，返回得分
    search_result = store.search_knowledge(query=user_input, return_scores=True)

    # 检查返回值类型
    if isinstance(search_result, tuple) and len(search_result) == 2:
        knowledge_results, scores = search_result
    else:
        knowledge_results = search_result
        scores = []

    # 问答类处理：有结果就返回，没结果回答"不知道"
    if knowledge_results and scores:
        # 获取重排序得分最高的结果
        top_score = scores[0].get('rerank_score', 0)

        await websocket.send_text(f"✅ 知识库查询完成，找到 {len(knowledge_results)} 个结果\n")
        await websocket.send_text(f"📊 最高重排序得分: {top_score:.2f}\n\n")

        # 格式化知识库结果，包含预览信息
        top_item = knowledge_results[0]

        # 添加预览信息
        preview_links = []

        # 检查文档链接
        if hasattr(top_item, 'source_url') and top_item.source_url:
            preview_links.append(f"📄 文档链接: {top_item.source_url}")

        # 检查文件路径
        if hasattr(top_item, 'file_path') and top_item.file_path:
            preview_links.append(f"📁 文件路径: {top_item.file_path}")

        # 检查是否可预览
        if hasattr(top_item, 'preview_available') and top_item.preview_available:
            preview_links.append("🔍 支持预览")

        # 添加知识条目ID用于预览
        if hasattr(top_item, 'id') and top_item.id:
            preview_links.append(f"🆔 条目ID: {top_item.id}")

        preview_info = ""
        if preview_links:
            preview_info = f"\n**预览信息**: {' | '.join(preview_links)}"

        # 从原文文件读取内容，并交给模型格式化为 Markdown（失败时回退）
        source_content = (top_item.content or "").strip()
        if hasattr(top_item, 'file_path') and top_item.file_path:
            try:
                raw_path = Path(top_item.file_path).expanduser().resolve()
                if raw_path.is_file():
                    source_content = raw_path.read_text(encoding='utf-8')
            except Exception as e:
                logger.error(f"读取 top_item.file_path 失败，回退到知识库存储内容: {e}")

        source_for_llm = source_content
        if len(source_for_llm) > 12000:
            source_for_llm = source_for_llm[:12000] + "\n...[内容已截断]"

        # 直接使用原文内容（不再做模型格式化）
        formatted_result = f"""### 1. {top_item.title}
**Domain**: {top_item.domain} | **Category**: {top_item.category} | **Priority**: {top_item.priority}
**Tags**: {', '.join(top_item.tags)}
**Created**: {top_item.created_at[:10]}{preview_info}

{source_for_llm}

---
"""

        # 构建预览项目数组（去重逻辑：相同文件只显示一个预览按钮）
        preview_items = []
        seen_files = set()  # 用于去重

        # 优先级1：文件路径预览（如果有本地文件路径）
        if hasattr(top_item, 'file_path') and top_item.file_path:
            file_key = top_item.file_path
            if file_key not in seen_files:
                preview_items.append({
                    'type': 'file',
                    'id': top_item.file_path,
                    'label': '📁 预览文件内容',
                    'path': top_item.file_path
                })
                seen_files.add(file_key)

        # 优先级2：文档链接预览（如果没有本地文件路径，但有URL）
        elif hasattr(top_item, 'source_url') and top_item.source_url:
            url_key = top_item.source_url
            if url_key not in seen_files:
                preview_items.append({
                    'type': 'url',
                    'id': top_item.source_url,
                    'label': '📄 预览文档链接',
                    'url': top_item.source_url
                })
                seen_files.add(url_key)

        # 优先级3：知识条目内容预览（如果既没有文件路径也没有URL，但可预览）
        elif hasattr(top_item, 'id') and top_item.id and hasattr(top_item,
                                                                 'preview_available') and top_item.preview_available:
            item_key = f"item_{top_item.id}"
            if item_key not in seen_files:
                preview_items.append({
                    'type': 'item',
                    'id': top_item.id,
                    'label': '🔍 预览完整内容',
                    'item_id': top_item.id
                })
                seen_files.add(item_key)

        # 通过JSON格式发送知识库结果，这样前端可以解析预览信息
        knowledge_message = {
            'type': 'stream_chunk',
            'content_type': 'knowledge',
            'content': f"找到 {len(knowledge_results)} 个结果，最高得分: {top_score:.2f}",
            'knowledge_status': 'success',
            'knowledge_count': len(knowledge_results),
            'knowledge_result': formatted_result,
            'preview_items': preview_items,  # 新增预览项目数组
            'timestamp': time.time(),
            'duration_from_start': round(time.time() - start_time, 3)
        }

        await websocket.send_text(json.dumps(knowledge_message, ensure_ascii=False))

        # 问答类：将知识库原文输入模型，生成 Markdown 格式答案
        await websocket.send_text("🤖 正在基于知识库原文生成答案...\n")

        # 取前3条，控制输入长度
        top_items = knowledge_results[:3]
        context_blocks = []
        for idx, item in enumerate(top_items, 1):
            content = (item.content or "").strip()
            if len(content) > 4000:
                content = content[:4000] + "\n...[内容已截断]"
            context_blocks.append(
                f"[资料{idx}] 标题: {item.title}\n"
                f"领域: {item.domain} | 分类: {item.category}\n"
                f"标签: {', '.join(item.tags)}\n"
                f"原文:\n{content}"
            )

        qa_prompt = (
                "你是RocketMQ知识助手。请严格基于给定原文回答用户问题，不要编造。\n"
                "输出要求：\n"
                "1. 使用 Markdown 输出\n"
                "2. 包含以下结构：\n"
                "   - `## 结论`\n"
                "   - `## 关键依据`\n"
                "   - `## 建议操作`\n"
                "   - `## 建议执行工具`\n"
                "3. 如果原文无法回答，明确写出“原文未提供足够信息”。\n"
                f"\n用户问题：{user_input}\n\n"
                "知识库原文：\n"
                + "\n\n---\n\n".join(context_blocks)
        )

        answer_markdown = None
        try:
            if provider:
                llm_resp = await provider.chat(
                    messages=[{"role": "user", "content": qa_prompt}],
                    model=config.agents.defaults.model,
                    max_tokens=1200,
                    temperature=0.2,
                    purpose="qa_answer",
                )
                answer_markdown = (llm_resp.content or "").strip()
        except Exception as e:
            logger.warning(f"知识库问答模型生成失败，回退原文输出: {e}")

        await websocket.send_text("📚 知识库答案：\n")
        if answer_markdown:
            await websocket.send_text(answer_markdown + "\n\n")
        else:
            # 兜底：模型失败时返回Top1原文
            await websocket.send_text(f"{knowledge_results[0].content}\n\n")

        # 发送处理时间
        end_time = time.time()
        total_processing_time = round(end_time - start_time, 1)
        await websocket.send_text(f"\n---\n*总耗时: {total_processing_time}秒*\n")

        # 发送处理完成状态消息，让前端按钮可以点击
        completion_message = {
            'type': 'stream_chunk',
            'content_type': 'completion',
            'content': '处理完成',
            'is_completed': True,
            'timestamp': end_time,
            'duration_from_start': total_processing_time
        }
        await websocket.send_text(json.dumps(completion_message, ensure_ascii=False))

        return
    else:
        # 问答类：没有找到知识库结果，回答"不知道"
        await websocket.send_text("📭 知识库中没有找到相关结果\n\n")
        await websocket.send_text("🤖 抱歉，我在知识库中没有找到相关信息，无法回答您的问题。\n\n")

        # 发送处理时间
        end_time = time.time()
        total_processing_time = round(end_time - start_time, 1)
        await websocket.send_text(f"\n---\n*总耗时: {total_processing_time}秒*\n")

        # 发送处理完成状态消息，让前端按钮可以点击
        completion_message = {
            'type': 'stream_chunk',
            'content_type': 'completion',
            'content': '处理完成',
            'is_completed': True,
            'timestamp': end_time,
            'duration_from_start': total_processing_time
        }
        await websocket.send_text(json.dumps(completion_message, ensure_ascii=False))

        return


async def process_troubleshooting_intent(
        user_input: str,
        websocket: WebSocket,
        start_time: float,
        additional_context: str | None = None,
        use_skills_retrieval: bool = True,
):
    """处理排查类意图：可带系统补充上下文进入 loop。"""

    import time
    import json

    # C 类默认先查 skills 索引；B 类可通过 use_skills_retrieval=False 禁用
    if use_skills_retrieval and additional_context is None and intent_routing_store:

        await websocket.send_text("🛠️ 正在检索 skills 库（top2）...\n")
        try:
            skill_hits = intent_routing_store.search_skills(user_input, limit=2)
            additional_context = _build_retrieval_context("Troubleshooting Skill Retrieval Context", skill_hits,
                                                          limit=2)
            await websocket.send_text(f"✅ skills 检索完成，命中 {len(skill_hits)} 条\n\n")
        except Exception as e:
            await websocket.send_text(f"⚠️ skills 检索失败: {str(e)}，继续执行。\n")

    await websocket.send_text("🔧 检测到排查/执行类问题，进入AI分析...\n\n")

    # Record LLM start time
    llm_start_time = time.time()

    # 设置流式回调函数
    async def stream_callback(context_info: dict):
        """流式输出回调函数，按类型分类显示内容，并统计每次返回的耗时"""
        content = context_info.get('content', '')

        # 获取回调时间和计算耗时
        callback_time = time.time()
        current_duration = round(callback_time - start_time, 3)

        # 获取迭代计数
        iteration_count = context_info.get('iteration_count', 0)

        # 根据内容类型进行分类处理
        content_type = 'text'  # 默认为文本类型
        enhanced_content = content

        # 检测是否为工具调用
        if context_info.get('is_tool_call') or 'tool_name' in context_info:
            content_type = 'tool'
            tool_name = context_info.get('tool_name', '')
            tool_status = context_info.get('tool_status', '')

            if tool_status == 'start':
                enhanced_content = f"🔧 调用工具: {tool_name}"
            elif tool_status == 'success':
                enhanced_content = f"✅ 工具执行成功: {tool_name}"
            elif tool_status == 'error':
                enhanced_content = f"❌ 工具执行失败: {tool_name}"
            else:
                enhanced_content = content

        # 检测是否为推理过程
        elif context_info.get('is_reasoning'):
            content_type = 'reasoning'
            enhanced_content = f"🤔 {content}"

        # 检测是否为知识库查询
        elif context_info.get('is_knowledge_query'):
            content_type = 'knowledge'
            enhanced_content = f"📚 {content}"

        # 检测是否为最终答案
        elif context_info.get('is_final_answer'):
            content_type = 'final_answer'
            enhanced_content = f"💡 {content}"

        # 检测是否为迭代开始
        elif context_info.get('is_iteration_start'):
            content_type = 'iteration'
            enhanced_content = f"🔄 第 {iteration_count} 轮思考: {content}"

        # 构建消息数据
        message_data = {
            'type': 'stream_chunk',
            'content_type': content_type,
            'content': enhanced_content,
            'is_reasoning': context_info.get('is_reasoning', False),
            'is_tool_call': content_type == 'tool' or context_info.get('is_tool_call', False),
            'is_final_answer': context_info.get('is_final_answer', False),
            'is_iteration_start': context_info.get('is_iteration_start', False),
            'timestamp': callback_time,
            'duration_from_start': current_duration,
            'iteration_count': iteration_count,
        }

        # 如果是工具调用，添加工具名称和状态信息
        if content_type == 'tool':
            message_data['tool_name'] = context_info.get('tool_name', '')
            message_data['tool_status'] = context_info.get('tool_status', '')
            message_data['tool_duration'] = context_info.get('tool_duration', 0)
            message_data['tool_result'] = context_info.get('tool_result', '')
            message_data['tool_error'] = context_info.get('tool_error', '')
            message_data['tool_args'] = context_info.get('tool_args')

        # 如果是知识库查询，添加知识库相关信息
        if content_type == 'knowledge':
            message_data['knowledge_status'] = context_info.get('knowledge_status', '')
            message_data['knowledge_domain'] = context_info.get('knowledge_domain', '')
            message_data['knowledge_query'] = context_info.get('knowledge_query', '')
            message_data['knowledge_count'] = context_info.get('knowledge_count', 0)
            message_data['knowledge_result'] = context_info.get('knowledge_result', '')

        await websocket.send_text(json.dumps(message_data, ensure_ascii=False))

    # 为agent_loop设置流式回调
    agent_loop.stream_callback = stream_callback

    # Process with streaming output
    response = await agent_loop.process_direct(
        user_input,
        session_key="cli:webui",
        additional_context=additional_context,
        disable_auto_kb=True,
    )

    # Record LLM end time
    llm_end_time = time.time()
    llm_execution_time = round(llm_end_time - llm_start_time, 1)

    # Send the actual response (如果流式输出已经发送了内容，这里可能不需要再发送)
    if response and response.strip():
        # 检查是否已经通过流式输出发送了内容
        # 如果没有流式输出，则发送完整响应
        await websocket.send_text("\n" + response)
    elif not response:
        await websocket.send_text("No response from agent.")

    end_time = time.time()
    total_processing_time = round(end_time - start_time, 1)

    # Send processing times
    await websocket.send_text(f"\n---\n*总耗时: {total_processing_time}秒 | LLM执行耗时: {llm_execution_time}秒*")

    # 发送处理完成状态消息，让前端按钮可以点击
    completion_message = {
        'type': 'stream_chunk',
        'content_type': 'completion',
        'content': '处理完成',
        'is_completed': True,
        'timestamp': end_time,
        'duration_from_start': total_processing_time
    }
    await websocket.send_text(json.dumps(completion_message, ensure_ascii=False))

    # 发送处理完成状态消息，让前端按钮可以点击
    completion_message = {
        'type': 'stream_chunk',
        'content_type': 'completion',
        'content': '处理完成',
        'is_completed': True,
        'timestamp': end_time,
        'duration_from_start': total_processing_time
    }
    await websocket.send_text(json.dumps(completion_message, ensure_ascii=False))


async def process_user_message(user_input: str) -> str:
    """Process user message using nanobot's AgentLoop."""
    import time

    start_time = time.time()

    # Check if provider and agent_loop are initialized
    if not provider or not agent_loop:
        return "Error: Web UI resources not initialized. Please restart the server."

    # Record LLM start time
    llm_start_time = time.time()

    response = await agent_loop.process_direct(user_input, session_key="cli:webui")

    # Record LLM end time
    llm_end_time = time.time()
    llm_execution_time = round(llm_end_time - llm_start_time, 1)

    end_time = time.time()
    total_processing_time = round(end_time - start_time, 1)

    if response:
        return f"{response}\n\n---\n*总耗时: {total_processing_time}秒 | LLM执行耗时: {llm_execution_time}秒*"
    else:
        return f"No response from agent.\n\n---\n*总耗时: {total_processing_time}秒 | LLM执行耗时: {llm_execution_time}秒*"


@web_app.post("/api/chat")
async def chat_endpoint(message: dict):
    """Handle chat API requests."""
    user_input = message.get("message", "")
    if not user_input:
        return {"error": "No message provided"}

    response = await process_user_message(user_input)
    return {"response": response}
