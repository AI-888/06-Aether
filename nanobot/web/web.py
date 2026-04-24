from __future__ import annotations
"""Web interface for nanobot with intent classification."""

from pathlib import Path
from typing import Any
import os

import asyncio
import json

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, UploadFile, File

from fastapi.responses import HTMLResponse, Response
from loguru import logger
import uuid

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


class WebSocketMessageQueue:
    """基于内存队列的 WebSocket 消息发送器（生产-消费模式）。

    生产端：业务代码调用 send_text() 将消息放入 asyncio.Queue。
    消费端：一个独立的异步协程从队列读取消息，通过真正的 WebSocket 推送到前端。

    好处：
    - 解耦消息生产与 WebSocket 物理发送
    - 避免在同一个协程中频繁 await send，降低阻塞风险
    - 便于后续扩展（如消息合并、限流、缓冲等）
    """

    def __init__(self, websocket: WebSocket):
        self._websocket = websocket
        self._queue: asyncio.Queue[str | None] = asyncio.Queue()
        self._consumer_task: asyncio.Task | None = None

    async def send_text(self, message: str) -> None:
        """生产端：将消息放入队列（替代直接 websocket.send_text）。"""
        await self._queue.put(message)

    async def start_consumer(self) -> None:
        """启动消费者协程，从队列读取消息并通过 WebSocket 发送到前端。"""
        self._consumer_task = asyncio.create_task(self._consume())

    async def _consume(self) -> None:
        """消费者主循环：持续从队列取消息并发送。"""
        try:
            while True:
                message = await self._queue.get()
                if message is None:
                    # 收到哨兵值，退出消费者
                    break
                try:
                    await self._websocket.send_text(message)
                except Exception as e:
                    logger.warning(f"[WebSocketMessageQueue] 消息发送失败: {e}")
                    break
        except asyncio.CancelledError:
            pass

    async def stop(self) -> None:
        """停止消费者：发送哨兵值并等待协程退出。"""
        logger.info(f"[WebSocketMessageQueue] 停止消费者")
        await self._queue.put(None)
        if self._consumer_task:
            try:
                await self._consumer_task
            except asyncio.CancelledError:
                pass

    async def close(self, code: int = 1000, reason: str = "") -> None:
        """关闭 WebSocket 连接（透传到底层 WebSocket）。"""
        await self.stop()
        await self._websocket.close(code=code, reason=reason)


# Create FastAPI application
web_app = FastAPI(
    title="nanobot Web UI",
    description="Web interface for nanobot"
)

# Create connection manager instance
manager = ConnectionManager()

# 注册源代码 RAG 管理 API 路由
try:
    from nanobot.web.source_code_api import router as source_code_router
    web_app.include_router(source_code_router)
except ImportError as _sc_err:
    logger.warning(f"[WEB] 源代码 RAG API 加载失败: {_sc_err}")

# Global instances for provider and agent_loop
provider: LLMProvider = None
agent_loop: AgentLoop = None
config: Config = None
intent_routing_store: IntentRoutingStore = None

# 上传文件配置
MAX_UPLOAD_SIZE_BYTES = 100 * 1024 * 1024  # 100MB
UPLOAD_SUBDIR = ".web_uploads"

# Skill 向量索引定时重建配置
SKILL_INDEX_REBUILD_INTERVAL_SECONDS = max(
    60,
    int(os.getenv("NANOBOT_SKILL_INDEX_REBUILD_INTERVAL_SECONDS", "1800")),
)
skill_index_rebuild_task: asyncio.Task | None = None

# 系统命令注册表（前端自动补全与命令执行共享）
SYSTEM_COMMANDS: list[dict[str, str]] = [
    {
        "command": "/update_skill",
        "description": "重建 Skill 向量索引",
    },
]


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


def rebuild_skill_vector_index(trigger: str = "manual") -> tuple[bool, str]:
    """重建 Skill 向量索引（tools + skills）。"""
    global intent_routing_store

    if not config or not agent_loop:
        return False, "Web UI 资源未初始化，无法重建 Skill 向量索引"

    try:
        if not intent_routing_store:
            intent_routing_store = get_intent_routing_store(config.workspace_path, config)

        tools_count = intent_routing_store.init_tools_index(
            tool_schemas=agent_loop.tools.get_definitions(),
            mcp_servers=config.mcp.servers,
        )

        from nanobot.rca.loader import RCASkillLoader
        skill_loader = RCASkillLoader(
            skill_dir=config.rca.skill_dir,
            intent_routing_store=intent_routing_store,
        )
        loaded_count = skill_loader.load_all()
        skills_count = intent_routing_store.init_skills_index(skill_loader)

        msg = (
            f"Skill 向量索引重建完成(trigger={trigger}): "
            f"tools={tools_count}, skills={skills_count}, loaded={loaded_count}"
        )
        logger.info(f"[WEB] 🧭 {msg}")
        return True, msg
    except Exception as e:
        err = f"Skill 向量索引重建失败(trigger={trigger}): {e}"
        logger.error(f"[WEB] ❌ {err}")
        return False, err


async def _skill_index_rebuild_loop() -> None:
    """后台定时重建 Skill 向量索引。"""
    logger.info(
        f"[WEB] ⏰ Skill 向量索引定时任务已启动，间隔={SKILL_INDEX_REBUILD_INTERVAL_SECONDS}s"
    )
    try:
        while True:
            await asyncio.sleep(SKILL_INDEX_REBUILD_INTERVAL_SECONDS)
            await asyncio.to_thread(rebuild_skill_vector_index, "timer")
    except asyncio.CancelledError:
        logger.info("[WEB] ⏹️ Skill 向量索引定时任务已停止")
        raise


def _start_skill_index_rebuild_task() -> None:
    """在事件循环中启动定时重建任务（重复调用幂等）。"""
    global skill_index_rebuild_task

    if skill_index_rebuild_task and not skill_index_rebuild_task.done():
        return

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return

    skill_index_rebuild_task = loop.create_task(_skill_index_rebuild_loop())


async def _stop_skill_index_rebuild_task() -> None:
    """停止定时重建任务。"""
    global skill_index_rebuild_task

    if skill_index_rebuild_task and not skill_index_rebuild_task.done():
        skill_index_rebuild_task.cancel()
        try:
            await skill_index_rebuild_task
        except asyncio.CancelledError:
            pass
    skill_index_rebuild_task = None


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
    ok, msg = rebuild_skill_vector_index(trigger="startup")
    if not ok:
        logger.error(f"[WEB] ❌ 意图路由索引初始化失败: {msg}")

    # 启动定时指标打印（每3秒写入日志，按日期自动分割）
    try:
        from nanobot.metrics import start_metrics_logging
        start_metrics_logging(log_dir="logs", interval_seconds=3)
        logger.info("[WEB] 📊 定时指标打印已启动（间隔3秒，日志目录: logs/）")
    except Exception as e:
        logger.warning(f"[WEB] ⚠️ 定时指标打印启动失败: {e}")

    # 启动 Skill 向量索引定时重建任务
    _start_skill_index_rebuild_task()

    return True


async def process_agent_command(command_text: str, ws_queue: WebSocketMessageQueue) -> None:
    """处理以 / 开头的 agent 命令。"""
    import time

    raw = (command_text or "").strip()
    if not raw.startswith("/"):
        return

    command = raw.split()[0].lower()
    command_set = {item.get("command", "").strip().lower() for item in SYSTEM_COMMANDS}

    if command == "/update_skill":
        await ws_queue.send_text("🧭 检测到命令 /update_skill，正在重建 Skill 向量索引...\n")
        ok, msg = await asyncio.to_thread(rebuild_skill_vector_index, "command:/update_skill")
        if ok:
            await ws_queue.send_text(f"✅ {msg}\n\n")
        else:
            await ws_queue.send_text(f"❌ {msg}\n\n")
    elif command in command_set:
        await ws_queue.send_text(f"⚠️ 命令 {command} 暂未实现\n\n")
    else:
        available = ", ".join(sorted(command_set)) if command_set else "(无)"
        await ws_queue.send_text(f"⚠️ 未知命令: {command}（可用命令: {available}）\n\n")

    completion_message = {
        "type": "stream_chunk",
        "content_type": "completion",
        "content": "处理完成",
        "is_completed": True,
        "timestamp": time.time(),
    }
    await ws_queue.send_text(json.dumps(completion_message, ensure_ascii=False))


@web_app.on_event("startup")
async def _web_app_startup() -> None:
    """Web 应用启动后启动后台定时任务。"""
    _start_skill_index_rebuild_task()


@web_app.on_event("shutdown")
async def _web_app_shutdown() -> None:
    """Web 应用退出前停止后台定时任务。"""
    await _stop_skill_index_rebuild_task()


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


@web_app.get("/api/system-commands")
async def get_system_commands():
    """获取系统命令列表（用于前端自动补全）。"""
    return {
        "success": True,
        "commands": SYSTEM_COMMANDS,
    }


@web_app.post("/api/upload")
async def upload_file(file: UploadFile = File(...)):

    """上传文件到工作空间并返回保存路径（最大 100MB）。"""
    try:
        if config and config.workspace_path:
            workspace_path = Path(config.workspace_path).expanduser().resolve()
        else:
            workspace_path = Path.home() / ".nanobot" / "workspace"
            workspace_path.mkdir(parents=True, exist_ok=True)

        upload_dir = (workspace_path / UPLOAD_SUBDIR).resolve()
        upload_dir.mkdir(parents=True, exist_ok=True)

        original_name = Path(file.filename or "upload.bin").name
        safe_suffix = Path(original_name).suffix
        saved_name = f"{uuid.uuid4().hex}{safe_suffix}"
        saved_path = (upload_dir / saved_name).resolve()

        total_size = 0
        with open(saved_path, "wb") as out_f:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                total_size += len(chunk)
                if total_size > MAX_UPLOAD_SIZE_BYTES:
                    out_f.close()
                    try:
                        saved_path.unlink(missing_ok=True)
                    except Exception:
                        pass
                    return {
                        "success": False,
                        "message": "文件大小超过限制（最大100MB）"
                    }
                out_f.write(chunk)

        await file.close()

        return {
            "success": True,
            "message": "上传成功",
            "file_path": str(saved_path),
            "file_name": original_name,
            "size": total_size,
        }
    except Exception as e:
        logger.error(f"[WEB] 上传文件失败: {e}")
        return {
            "success": False,
            "message": f"上传失败: {str(e)}"
        }


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

    current_task: asyncio.Task | None = None
    current_ws_queue: WebSocketMessageQueue | None = None

    async def _cancel_current_task() -> None:
        nonlocal current_task, current_ws_queue
        if current_task and not current_task.done():
            logger.info("[WEB] 收到终止请求，正在取消当前任务")
            current_task.cancel()
            try:
                await current_task
            except asyncio.CancelledError:
                logger.info("[WEB] 当前任务已取消")
            except Exception as e:
                logger.warning(f"[WEB] 取消任务后等待结束异常: {e}")

        try:
            await websocket.send_text(json.dumps({
                "type": "abort_ack",
                "content": "已终止当前请求"
            }, ensure_ascii=False))
        except Exception as e:
            logger.debug(f"[WEB] 发送终止确认失败: {e}")

        current_task = None
        current_ws_queue = None

    try:
        while True:
            data = await websocket.receive_text()
            logger.info(f"[WEB][user_input] 原始输入: {data!r}")

            # 控制消息（如终止）优先处理
            control_type = None
            maybe_json = None
            try:
                maybe_json = json.loads(data)
                if isinstance(maybe_json, dict):
                    control_type = maybe_json.get("type")
            except Exception:
                pass

            if control_type == "abort":
                await _cancel_current_task()
                continue

            # 如已有任务在执行，拒绝并发新任务
            if current_task and not current_task.done():
                try:
                    if current_ws_queue:
                        await current_ws_queue.send_text(json.dumps({
                            "type": "warn",
                            "content": "当前请求仍在处理中，请先终止后再发送新请求"
                        }, ensure_ascii=False))
                except Exception as e:
                    logger.debug(f"[WEB] 发送并发请求告警失败: {e}")
                continue

            user_input = ""
            uploaded_file_path = ""
            raw_user_input = ""

            if isinstance(maybe_json, dict):
                raw_user_input = str(maybe_json.get("message", ""))
                user_input = raw_user_input.strip()
                uploaded_file_path = str(maybe_json.get("file_path", "")).strip()
            else:
                raw_user_input = str(data)
                user_input = raw_user_input.strip()

            if not user_input:
                try:
                    await websocket.send_text(json.dumps({
                        "type": "warn",
                        "content": "消息不能为空"
                    }, ensure_ascii=False))
                except Exception:
                    pass
                continue

            # 创建消息队列实例（生产-消费模式）
            ws_queue = WebSocketMessageQueue(websocket)
            await ws_queue.start_consumer()
            current_ws_queue = ws_queue

            async def _runner(user_input: str, queue: WebSocketMessageQueue, file_path: str | None):
                try:
                    # 业务代码通过 ws_queue.send_text() 投递消息到队列
                    await process_user_message_streaming(user_input, queue, file_path=file_path)
                except asyncio.CancelledError:
                    logger.info("[WEB] 流式处理任务被取消")
                    raise
                finally:
                    # 确保消费者协程正常退出
                    await queue.stop()

            async def _runner_command(command_text: str, queue: WebSocketMessageQueue):
                try:
                    await process_agent_command(command_text, queue)
                except asyncio.CancelledError:
                    logger.info("[WEB] 命令处理任务被取消")
                    raise
                finally:
                    await queue.stop()

            if user_input.startswith("/"):
                current_task = asyncio.create_task(_runner_command(user_input, ws_queue))
            else:
                current_task = asyncio.create_task(_runner(user_input, ws_queue, uploaded_file_path or None))

    except WebSocketDisconnect:
        await _cancel_current_task()
        manager.disconnect(websocket)


async def classify_user_intent(user_input: str, ws_queue: WebSocketMessageQueue) -> str:
    """
    使用LLM对用户意图进行分类（A/D 两级分类）

    Args:
        user_input: 用户输入
        ws_queue: WebSocket消息队列

    Returns:
        'A' 表示知识问答，'D' 表示操作/排查请求
    """
    # 从 config 读取提示词，若未配置则使用内置默认值
    _intent_prompt_lines = config.agents.defaults.intent_classification_prompt
    if _intent_prompt_lines:
        intent_prompt = "\n".join(_intent_prompt_lines).replace("{user_input}", user_input)
    else:
        intent_prompt = f"""
    你是一个意图分类器。

    任务：根据用户问题判断意图，只输出一个字母：

    A = 知识问答
    概念、原理、配置、参数、教程、文档、对比、选型等纯知识类问题

    D = 操作/排查请求
    查询系统状态、查看 pod/日志/集群、运维操作、故障排查、报错分析、异常诊断等需要实际操作或分步排查的问题

    示例：

    Q: rocketmq broker 是什么
    A

    Q: kafka 和 rocketmq 区别
    A

    Q: rocketmq 消息类型有哪些
    A

    Q: 查看 rocketmq broker pod
    D

    Q: 查询 broker 日志
    D

    Q: rocketmq 消息积压怎么办
    D

    Q: broker 报 timeout 错误
    D

    Q: 帮我重启 broker
    D

    用户问题：
    {user_input}

    只输出 A 或 D：
    """

    try:
        await ws_queue.send_text("🧠 正在识别用户意图...\n")

        # 使用全局的provider进行意图分类
        if not provider:
            await ws_queue.send_text("⚠️ LLM服务未初始化，跳过意图识别\n")
            return "A"  # 默认为问答类

        # 调用LLM进行意图分类
        response = await provider.chat(
            messages=[{"role": "user", "content": intent_prompt}],
            model=config.agents.defaults.model,
            max_tokens=1000,  # 只需要返回 A/D
            temperature=0.1,  # 低温度确保稳定输出
            purpose="intent_classification",
        )

        intent = response.content.strip().upper()

        # 验证返回结果
        if intent not in ['A', 'D']:
            await ws_queue.send_text(f"⚠️ 意图识别结果异常: {intent}，默认为问答类\n")
            return "A"

        intent_type = "问答类" if intent == "A" else "操作/排查类"

        await ws_queue.send_text(f"✅ 用户意图识别: {intent_type} ({intent})\n\n")

        return intent

    except Exception as e:
        logger.error(f"意图识别失败: {e}")
        await ws_queue.send_text(f"⚠️ 意图识别失败: {str(e)}，默认为问答类(A)\n")
        return "A"  # 出错时默认回退到 A


async def process_user_message_streaming(
    user_input: str,
    ws_queue: WebSocketMessageQueue,
    file_path: str | None = None,
):
    """Process user message with real-time streaming output."""
    import time

    start_time = time.time()

    # Check if provider and agent_loop are initialized
    if not provider or not agent_loop:
        await ws_queue.send_text("Error: Web UI resources not initialized. Please restart the server.")
        return

    # Send initial processing message
    await ws_queue.send_text("🤖 AI Agent is processing your request...\n\n")

    # 第一步：用户意图识别
    user_intent = await classify_user_intent(user_input, ws_queue)

    # 根据意图决定处理流程（A/D 两级分类）
    if user_intent == "A":
        # A 类：知识问答 → 查询知识库
        await process_qa_intent(user_input, ws_queue, start_time)
    elif user_intent == "D":
        # D 类：操作/排查请求 → 统一进入 Skill 执行流程
        # D 类内部子分类：简单操作 → 搜索原子 Skill；复杂操作（RCA分析）→ 搜索 SOP Skill
        sub_type = await classify_d_sub_type(user_input, ws_queue)
        if sub_type == "simple":
            # 简单操作：搜索原子 Skill（查 tools 索引），提取工具后进入 LLM loop
            await process_ops_intent(user_input, ws_queue, start_time, file_path=file_path)
        else:
            # 复杂操作（RCA分析）：搜索 SOP Skill，执行分步诊断
            await process_troubleshooting_intent(user_input, ws_queue, start_time, file_path=file_path)
    else:
        # 非法值默认 A
        await ws_queue.send_text(f"⚠️ 意图值非法: {user_intent}，默认按 A 问答类处理\n")
        await process_qa_intent(user_input, ws_queue, start_time)


async def classify_d_sub_type(user_input: str, ws_queue: WebSocketMessageQueue) -> str:
    """
    D 类意图的子分类：区分简单操作和复杂操作（RCA分析）

    Args:
        user_input: 用户输入
        ws_queue: WebSocket消息队列

    Returns:
        'simple' 表示简单操作（查状态/执行命令），'complex' 表示复杂操作（故障排查/RCA分析）
    """
    # 从 config 读取提示词，若未配置则使用内置默认值
    _complexity_prompt_lines = config.agents.defaults.complexity_classification_prompt
    if _complexity_prompt_lines:
        sub_type_prompt = "\n".join(_complexity_prompt_lines).replace("{user_input}", user_input)
    else:
        sub_type_prompt = f"""
    你是一个运维操作分类器。用户的问题已被识别为操作/排查类，现在需要进一步判断操作的复杂程度。

    任务：根据用户问题判断是简单操作还是复杂操作，只输出一个词：

    simple = 简单操作
    查看状态、获取信息、执行单个命令、查询 pod/日志/配置/指标等

    complex = 复杂操作（RCA分析）
    故障排查、异常分析、根因定位、多步骤诊断、消息积压分析、性能问题排查等

    示例：

    Q: 查看 rocketmq broker pod
    simple

    Q: 查询 broker 日志
    simple

    Q: 查看集群节点状态
    simple

    Q: rocketmq 消息积压怎么办
    complex

    Q: broker 报 timeout 错误怎么排查
    complex

    Q: 集群性能下降的原因分析
    complex

    Q: 消费者连接不上怎么处理
    complex

    用户问题：
    {user_input}

    只输出 simple 或 complex：
    """

    try:
        await ws_queue.send_text("🔍 正在判断操作复杂度...\n")

        if not provider:
            await ws_queue.send_text("⚠️ LLM服务未初始化，默认为简单操作\n")
            return "simple"

        response = await provider.chat(
            messages=[{"role": "user", "content": sub_type_prompt}],
            model=config.agents.defaults.model,
            max_tokens=100,
            temperature=0.1,
            purpose="d_sub_classification",
        )

        sub_type = response.content.strip().lower()

        # 验证返回结果
        if sub_type not in ['simple', 'complex']:
            await ws_queue.send_text(f"⚠️ 子分类结果异常: {sub_type}，默认为简单操作\n")
            return "simple"

        sub_type_label = "简单操作" if sub_type == "simple" else "复杂操作（RCA分析）"
        await ws_queue.send_text(f"✅ 操作类型: {sub_type_label}\n\n")

        return sub_type

    except Exception as e:
        logger.error(f"D类子分类失败: {e}")
        await ws_queue.send_text(f"⚠️ 操作类型判断失败: {str(e)}，默认为简单操作\n")
        return "simple"


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


async def process_ops_intent(
    user_input: str,
    ws_queue: WebSocketMessageQueue,
    start_time: float,
    file_path: str | None = None,
):

    """处理 D 类简单操作意图：检索 Skill → rerank top1 → 提取工具 → 筛选已注册工具 → LLM 决策。

    D 类子分类为 simple 时调用此函数。

    流程：
    1. 在统一 skills 向量库中检索匹配的 Skill（YAML 格式）
    2. rerank 取 top1
    3. 从 top1 Skill 的 steps 中提取 type=tool 的步骤引用的工具名
    4. 在全部已注册工具中筛选匹配的工具
    5. 将 Skill 内容作为上下文 + 筛选后的工具列表喂给 LLM
    6. LLM 自主决策是否调用工具、调用哪个工具
    """
    import json
    import time
    from nanobot.rca.loader import RCASkillLoader

    if not intent_routing_store:
        await ws_queue.send_text("❌ Skill 索引未初始化，无法执行该操作。\n")
        await ws_queue.send_text(json.dumps({
            "type": "stream_chunk",
            "content_type": "knowledge",
            "content": "Skill 索引未初始化，无法执行操作",
            "knowledge_status": "error",
            "knowledge_count": 0,
            "knowledge_result": "",
            "preview_items": [],
            "timestamp": time.time(),
        }, ensure_ascii=False))
        completion_message = {
            'type': 'stream_chunk',
            'content_type': 'completion',
            'content': '处理完成',
            'is_completed': True,
            'timestamp': time.time(),
        }
        await ws_queue.send_text(json.dumps(completion_message, ensure_ascii=False))
        return

    # ── Step 1: 通知前端开始检索 Skill 库 ──
    await ws_queue.send_text(json.dumps({
        "type": "stream_chunk",
        "content_type": "knowledge",
        "content": "正在检索运维 Skill 库...",
        "knowledge_status": "start",
        "knowledge_count": 0,
        "knowledge_result": "",
        "preview_items": [],
        "timestamp": time.time(),
    }, ensure_ascii=False))

    skill_results: list[dict[str, Any]] = []
    try:
        skill_results = intent_routing_store.search_skills(user_input, limit=4)
    except Exception as e:
        logger.warning(f"[WEB] skill 检索失败: {e}")
        await ws_queue.send_text(json.dumps({
            "type": "stream_chunk",
            "content_type": "knowledge",
            "content": f"Skill 检索失败: {str(e)}",
            "knowledge_status": "error",
            "knowledge_count": 0,
            "knowledge_result": "",
            "preview_items": [],
            "timestamp": time.time(),
        }, ensure_ascii=False))

    if not skill_results:
        await ws_queue.send_text(json.dumps({
            "type": "stream_chunk",
            "content_type": "knowledge",
            "content": "未检索到匹配的运维 Skill",
            "knowledge_status": "no_results",
            "knowledge_count": 0,
            "knowledge_result": "",
            "preview_items": [],
            "timestamp": time.time(),
        }, ensure_ascii=False))
        await ws_queue.send_text("🔧 未匹配到运维 Skill，无法执行该操作。\n\n")
        completion_message = {
            'type': 'stream_chunk',
            'content_type': 'completion',
            'content': '处理完成',
            'is_completed': True,
            'timestamp': time.time(),
        }
        await ws_queue.send_text(json.dumps(completion_message, ensure_ascii=False))
        return

    # ── Step 2: 通知前端检索结果（rerank 前） ──
    raw_preview_md = _build_retrieval_context("Skill 检索原始结果", skill_results, limit=len(skill_results)) or ""
    await ws_queue.send_text(json.dumps({
        "type": "stream_chunk",
        "content_type": "knowledge",
        "content": f"Skill 检索完成，命中 {len(skill_results)} 条，正在重排序...",
        "knowledge_status": "searching",
        "knowledge_count": len(skill_results),
        "knowledge_result": raw_preview_md,
        "preview_items": [],
        "timestamp": time.time(),
    }, ensure_ascii=False))

    # ── Step 3a: 创建 skill_loader（filter 和后续步骤共用） ──
    skill_loader = RCASkillLoader(skill_dir=config.rca.skill_dir)
    skill_loader.load_all()

    # ── Step 3b: 过滤被 SOP 包含的 Atomic Skill ──
    from nanobot.rca.skill_filter import filter_redundant_atomic_skills
    filtered_results = filter_redundant_atomic_skills(skill_results, skill_loader)
    logger.info(
        f"[WEB] Skill filter: {len(skill_results)} → {len(filtered_results)} "
        f"(移除 {len(skill_results) - len(filtered_results)} 个冗余 Atomic)"
    )

    # ── Step 3c: 条件 rerank ──
    if len(filtered_results) >= 2:
        reranked_results = _rerank_route_candidates(user_input, filtered_results)
        top1 = reranked_results[0] if reranked_results else None
    else:
        # 只剩 1 条或 0 条，跳过 rerank
        top1 = filtered_results[0] if filtered_results else None

    if not top1:
        await ws_queue.send_text(json.dumps({
            "type": "stream_chunk",
            "content_type": "knowledge",
            "content": "过滤/Rerank 后无有效结果，无法执行该操作",
            "knowledge_status": "no_results",
            "knowledge_count": 0,
            "knowledge_result": "",
            "preview_items": [],
            "timestamp": time.time(),
        }, ensure_ascii=False))
        await ws_queue.send_text("🔧 Skill 过滤/重排序后无有效结果，无法执行该操作。\n\n")
        completion_message = {
            'type': 'stream_chunk',
            'content_type': 'completion',
            'content': '处理完成',
            'is_completed': True,
            'timestamp': time.time(),
        }
        await ws_queue.send_text(json.dumps(completion_message, ensure_ascii=False))
        return

    top1_meta = top1.get("metadata", {}) or {}
    matched_skill_name = top1_meta.get("skill_name", "")
    retrieval_markdown = _build_retrieval_context("Ops Skill Retrieval (Reranked Top1)", [top1], limit=1) or ""

    # ── Step 4: 获取 Skill 对象，按类型分发执行 ──
    skill_obj = None
    try:
        skill_obj = skill_loader.get_skill(matched_skill_name)
    except Exception as e:
        logger.warning(f"[WEB] 获取 Skill '{matched_skill_name}' 失败: {e}")

    if skill_obj and hasattr(skill_obj, "steps") and skill_obj.steps:
        # ── SOP Skill：通过 RCA Engine 逐步执行 ──
        logger.info(
            f"[WEB] Skill '{matched_skill_name}' 是 SOP Skill "
            f"({len(skill_obj.steps)} 步)，走 RCA Engine 逐步执行"
        )

        # 通知前端 rerank 结果
        await ws_queue.send_text(json.dumps({
            "type": "stream_chunk",
            "content_type": "knowledge",
            "content": f"重排序完成，最佳匹配 SOP Skill: {matched_skill_name}",
            "knowledge_status": "success",
            "knowledge_count": 1,
            "knowledge_result": retrieval_markdown,
            "preview_items": [],
            "timestamp": time.time(),
        }, ensure_ascii=False))

        await ws_queue.send_text(
            f"🔧 匹配到运维 SOP Skill: **{matched_skill_name}**"
            f"（共 {len(skill_obj.steps)} 步），开始逐步执行...\n\n"
        )

        try:
            report = await _execute_rca_skill(
                matched_skill_name,
                user_input,
                ws_queue,
                skill_loader=skill_loader,
                file_path=file_path,
            )

            if report:
                # 发送 RCA 报告到前端
                report_md = report.to_markdown()
                await ws_queue.send_text(report_md + "\n")
            else:
                # Skill 执行失败，直接返回前端失败信息
                await ws_queue.send_text("❌ SOP Skill 执行失败，请检查 Skill 配置或联系管理员。\n\n")

        except Exception as e:
            logger.error(f"[WEB] SOP Skill 执行异常: {e}")
            await ws_queue.send_text(
                f"❌ SOP Skill 执行异常: {str(e)}\n\n"
            )

        # 发送完成状态
        completion_message = {
            'type': 'stream_chunk',
            'content_type': 'completion',
            'content': '处理完成',
            'is_completed': True,
            'timestamp': time.time(),
        }
        await ws_queue.send_text(json.dumps(completion_message, ensure_ascii=False))
        return

    # ── Atomic Skill / 无 steps 的 Skill：走 LLM loop 路径 ──
    skill_tool_names: list[str] = []
    if skill_obj and hasattr(skill_obj, "tool") and skill_obj.tool:
        skill_tool_names.append(skill_obj.tool)
    logger.info(f"[WEB] Skill '{matched_skill_name}' 引用的工具: {skill_tool_names}")

    # ── Step 5: 在全部已注册工具中筛选匹配的工具 ──
    registered_tool_names = agent_loop.tools.tool_names if agent_loop else []
    filtered_tool_names: list[str] = [
        tn for tn in skill_tool_names if tn in registered_tool_names
    ]
    unregistered_tools = [tn for tn in skill_tool_names if tn not in registered_tool_names]

    if unregistered_tools:
        logger.warning(f"[WEB] Skill 引用了未注册的工具: {unregistered_tools}")

    logger.info(f"[WEB] 最终筛选的已注册工具列表: {filtered_tool_names}")

    # ── Step 6: 通知前端 rerank 结果（含工具提取信息） ──
    tools_info = f"，提取工具: {filtered_tool_names}" if filtered_tool_names else "，未提取到可用工具"
    await ws_queue.send_text(json.dumps({
        "type": "stream_chunk",
        "content_type": "knowledge",
        "content": f"重排序完成，最佳匹配 Skill: {matched_skill_name}{tools_info}",
        "knowledge_status": "success",
        "knowledge_count": 1,
        "knowledge_result": retrieval_markdown,
        "preview_items": [],
        "timestamp": time.time(),
    }, ensure_ascii=False))

    # ── Step 7: 构建 Skill 上下文，传入 LLM loop ──
    skill_context_parts = [f"# 运维 Skill 参考: {matched_skill_name}"]
    if skill_obj:
        skill_context_parts.append(f"描述: {skill_obj.description}")
        skill_context_parts.append(f"类型: {skill_obj.type}")
        if hasattr(skill_obj, "tool") and skill_obj.tool:
            skill_context_parts.append(f"\n## 绑定工具: {skill_obj.tool}")
        if filtered_tool_names:
            skill_context_parts.append(f"\n## 可用工具: {', '.join(filtered_tool_names)}")
            if len(filtered_tool_names) == 1:
                tool_name = filtered_tool_names[0]
                skill_context_parts.append(
                    f"\n## 【重要指令】\n"
                    f"系统已为你匹配到精确的运维工具 `{tool_name}`。\n"
                    f"你**必须立即调用** `{tool_name}` 工具来执行用户请求。\n"
                    f"请从用户问题中提取工具所需的参数，然后直接发起 tool_call。\n"
                    f"**禁止**跳过工具调用而直接用文本回答。"
                )
            else:
                skill_context_parts.append(
                    "请根据用户问题和以上 Skill 参考，自主判断是否需要调用工具以及调用哪个工具。"
                )
    if file_path:
        skill_context_parts.append(
            "\n## 用户上传文件\n"
            f"用户已上传文件，路径: {file_path}\n"
            "当需要读取用户上传内容时，请优先使用该路径作为输入上下文。"
        )
    additional_context = "\n".join(skill_context_parts)

    # 根据工具数量选择不同的日志文案
    if filtered_tool_names and len(filtered_tool_names) >= 1:
        await ws_queue.send_text(
            f"🔧 匹配到运维 Skill: **{matched_skill_name}**"
            f"，准备调用工具 `{filtered_tool_names[0]}` ...\n\n"
        )
    else:
        await ws_queue.send_text(
            f"🔧 匹配到运维 Skill: **{matched_skill_name}**"
            f"（可用工具: {', '.join(filtered_tool_names) if filtered_tool_names else '无'}），"
            f"进入 AI 分析...\n\n"
        )

    return await _run_agent_loop(
        user_input,
        ws_queue,
        additional_context=additional_context,
        tool_names_filter=filtered_tool_names or None,
        file_path=file_path,
    )


async def process_qa_intent(user_input: str, ws_queue: WebSocketMessageQueue, start_time: float):
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
        await ws_queue.send_text(error_msg)
        # 关闭WebSocket连接
        await ws_queue.close(code=1011, reason="CrossEncoder initialization failed")
        return
    except Exception as e:
        # 其他初始化错误
        error_msg = f"❌ 知识库初始化失败: {str(e)}\n\n"
        await ws_queue.send_text(error_msg)
        return

    # 发送知识库查询开始信息
    await ws_queue.send_text("📚 正在查询知识库...\n")

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

        await ws_queue.send_text(f"✅ 知识库查询完成，找到 {len(knowledge_results)} 个结果\n")
        await ws_queue.send_text(f"📊 最高重排序得分: {top_score:.2f}\n\n")

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
        }

        await ws_queue.send_text(json.dumps(knowledge_message, ensure_ascii=False))

        # 问答类：将知识库原文输入模型，生成 Markdown 格式答案
        await ws_queue.send_text("🤖 正在基于知识库原文生成答案...\n")

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

        await ws_queue.send_text("📚 知识库答案：\n")
        if answer_markdown:
            await ws_queue.send_text(answer_markdown + "\n\n")
        else:
            # 兜底：模型失败时返回Top1原文
            await ws_queue.send_text(f"{knowledge_results[0].content}\n\n")

        # 发送处理完成状态消息，让前端按钮可以点击
        end_time = time.time()
        completion_message = {
            'type': 'stream_chunk',
            'content_type': 'completion',
            'content': '处理完成',
            'is_completed': True,
            'timestamp': end_time,
        }
        await ws_queue.send_text(json.dumps(completion_message, ensure_ascii=False))

        return
    else:
        # 问答类：没有找到知识库结果，回答"不知道"
        await ws_queue.send_text("📭 知识库中没有找到相关结果\n\n")
        await ws_queue.send_text("🤖 抱歉，我在知识库中没有找到相关信息，无法回答您的问题。\n\n")

        # 发送处理完成状态消息，让前端按钮可以点击
        end_time = time.time()
        completion_message = {
            'type': 'stream_chunk',
            'content_type': 'completion',
            'content': '处理完成',
            'is_completed': True,
            'timestamp': end_time,
        }
        await ws_queue.send_text(json.dumps(completion_message, ensure_ascii=False))

        return


async def process_troubleshooting_intent(
        user_input: str,
        ws_queue: WebSocketMessageQueue,
        start_time: float,
        file_path: str | None = None,
):

    """处理 D 类复杂操作意图：搜索 SOP Skill → rerank top1 → RCA Engine 执行。

    D 类子分类为 complex 时调用此函数，执行 SOP Skill 分步诊断流程。
    """

    import time
    import json

    # ─── complex 类排障流程：搜索 SOP Skill → rerank → RCA Engine ───

    if not intent_routing_store:
        await ws_queue.send_text("❌ Skill 索引未初始化，无法执行排障诊断。\n")
        await ws_queue.send_text(json.dumps({
            "type": "stream_chunk",
            "content_type": "knowledge",
            "content": "Skill 索引未初始化，无法执行排障诊断",
            "knowledge_status": "error",
            "knowledge_count": 0,
            "knowledge_result": "",
            "preview_items": [],
            "timestamp": time.time(),
        }, ensure_ascii=False))
        completion_message = {
            'type': 'stream_chunk',
            'content_type': 'completion',
            'content': '处理完成',
            'is_completed': True,
            'timestamp': time.time(),
        }
        await ws_queue.send_text(json.dumps(completion_message, ensure_ascii=False))
        return

    # Step 1: 通知前端开始检索 Skill 库
    await ws_queue.send_text(json.dumps({
        "type": "stream_chunk",
        "content_type": "knowledge",
        "content": "正在检索排障 Skill 库...",
        "knowledge_status": "start",
        "knowledge_count": 0,
        "knowledge_result": "",
        "preview_items": [],
        "timestamp": time.time(),
    }, ensure_ascii=False))

    skill_results: list[dict[str, Any]] = []
    try:
        skill_results = intent_routing_store.search_skills(user_input, limit=4)
    except Exception as e:
        logger.warning(f"[WEB] skill 检索失败: {e}")
        await ws_queue.send_text(json.dumps({
            "type": "stream_chunk",
            "content_type": "knowledge",
            "content": f"Skill 检索失败: {str(e)}",
            "knowledge_status": "error",
            "knowledge_count": 0,
            "knowledge_result": "",
            "preview_items": [],
            "timestamp": time.time(),
        }, ensure_ascii=False))

    if not skill_results:
        await ws_queue.send_text(json.dumps({
            "type": "stream_chunk",
            "content_type": "knowledge",
            "content": "未检索到匹配的排障 Skill",
            "knowledge_status": "no_results",
            "knowledge_count": 0,
            "knowledge_result": "",
            "preview_items": [],
            "timestamp": time.time(),
        }, ensure_ascii=False))
        await ws_queue.send_text("🔧 未匹配到排障 Skill，无法执行排障诊断。\n\n")
        completion_message = {
            'type': 'stream_chunk',
            'content_type': 'completion',
            'content': '处理完成',
            'is_completed': True,
            'timestamp': time.time(),
        }
        await ws_queue.send_text(json.dumps(completion_message, ensure_ascii=False))
        return

    # Step 2: 通知前端检索结果（rerank 前）
    raw_preview_md = _build_retrieval_context("Skill 检索原始结果", skill_results, limit=len(skill_results)) or ""
    await ws_queue.send_text(json.dumps({
        "type": "stream_chunk",
        "content_type": "knowledge",
        "content": f"Skill 检索完成，命中 {len(skill_results)} 条，正在重排序...",
        "knowledge_status": "searching",
        "knowledge_count": len(skill_results),
        "knowledge_result": raw_preview_md,
        "preview_items": [],
        "timestamp": time.time(),
    }, ensure_ascii=False))

    # Step 3a: 过滤被 SOP 包含的 Atomic Skill
    from nanobot.rca.loader import RCASkillLoader
    from nanobot.rca.skill_filter import filter_redundant_atomic_skills
    skill_loader = RCASkillLoader(skill_dir=config.rca.skill_dir)
    skill_loader.load_all()
    filtered_results = filter_redundant_atomic_skills(skill_results, skill_loader)
    logger.info(
        f"[WEB] Skill filter: {len(skill_results)} → {len(filtered_results)} "
        f"(移除 {len(skill_results) - len(filtered_results)} 个冗余 Atomic)"
    )

    # Step 3b: 条件 rerank
    if len(filtered_results) >= 2:
        reranked_results = _rerank_route_candidates(user_input, filtered_results)
        top1 = reranked_results[0] if reranked_results else None
    else:
        # 只剩 1 条或 0 条，跳过 rerank
        top1 = filtered_results[0] if filtered_results else None

    if not top1:
        await ws_queue.send_text(json.dumps({
            "type": "stream_chunk",
            "content_type": "knowledge",
            "content": "Rerank 后无有效结果，无法执行排障诊断",
            "knowledge_status": "no_results",
            "knowledge_count": 0,
            "knowledge_result": "",
            "preview_items": [],
            "timestamp": time.time(),
        }, ensure_ascii=False))
        await ws_queue.send_text("🔧 Skill 重排序后无有效结果，无法执行排障诊断。\n\n")
        completion_message = {
            'type': 'stream_chunk',
            'content_type': 'completion',
            'content': '处理完成',
            'is_completed': True,
            'timestamp': time.time(),
        }
        await ws_queue.send_text(json.dumps(completion_message, ensure_ascii=False))
        return

    top1_meta = top1.get("metadata", {}) or {}
    matched_skill_name = top1_meta.get("skill_name", "")
    retrieval_markdown = _build_retrieval_context("Skill Retrieval (Reranked Top1)", [top1], limit=1) or ""

    await ws_queue.send_text(json.dumps({
        "type": "stream_chunk",
        "content_type": "knowledge",
        "content": f"重排序完成，最佳匹配 Skill: {matched_skill_name}（共检索 {len(skill_results)} 条）",
        "knowledge_status": "success",
        "knowledge_count": 1,
        "knowledge_result": retrieval_markdown,
        "preview_items": [],
        "timestamp": time.time(),
    }, ensure_ascii=False))

    # Step 4: 使用 RCA Engine 执行匹配到的 Skill
    await ws_queue.send_text(f"🔧 匹配到排障 Skill: **{matched_skill_name}**，开始执行分步诊断...\n\n")

    try:
        report = await _execute_rca_skill(
            matched_skill_name,
            user_input,
            ws_queue,
            file_path=file_path,
        )

        if report:
            # 发送 RCA 报告到前端
            report_md = report.to_markdown()
            await ws_queue.send_text(report_md + "\n")
        else:
            # Skill 执行失败，直接返回前端失败信息
            await ws_queue.send_text("❌ Skill 执行失败，请检查 Skill 配置或联系管理员。\n\n")

    except Exception as e:
        logger.error(f"[WEB] RCA Skill 执行异常: {e}")
        await ws_queue.send_text(f"❌ Skill 执行异常: {str(e)}\n\n")

    end_time = time.time()

    # 发送处理完成状态消息
    completion_message = {
        'type': 'stream_chunk',
        'content_type': 'completion',
        'content': '处理完成',
        'is_completed': True,
        'timestamp': end_time,
    }
    await ws_queue.send_text(json.dumps(completion_message, ensure_ascii=False))


async def _execute_rca_skill(
    skill_name: str,
    user_input: str,
    ws_queue: WebSocketMessageQueue,
    skill_loader: "RCASkillLoader | None" = None,
    file_path: str | None = None,
) -> "RCAReport | None":

    """加载并执行指定的 RCA Skill。

    Args:
        skill_name: Skill 名称
        user_input: 用户原始输入（作为 Skill 的 description 输入）
        ws_queue: WebSocket消息队列，用于流式通知
        skill_loader: 外部已初始化的 RCASkillLoader（可选，不传则内部新建）

    Returns:
        RCA 报告，执行失败返回 None
    """
    import json
    import time

    try:
        from nanobot.rca.loader import RCASkillLoader
        from nanobot.rca.engine import RCAEngine
        from nanobot.rca.audit import AuditLogger
        from nanobot.rca.security import SecurityGuard
        from nanobot.rca.report import RCAReport
        from nanobot.rca.router import RCARouter

        # 复用外部传入的 skill_loader，或新建
        if skill_loader is None:
            skill_loader = RCASkillLoader(skill_dir=config.rca.skill_dir)
            skill_loader.load_all()

        skill = skill_loader.get_skill(skill_name)

        if not skill:
            logger.warning(f"[WEB] Skill '{skill_name}' 未找到")
            return None

        # 初始化 RCA Engine 依赖
        security = SecurityGuard(extra_whitelist=config.rca.security_whitelist)
        audit = AuditLogger(log_dir=config.rca.audit_log_dir)

        engine = RCAEngine(
            provider=provider,
            tool_registry=agent_loop.tools,
            security_guard=security,
            audit_logger=audit,
            skill_loader=skill_loader,
            model=config.rca.model or config.agents.defaults.model,
            max_step_timeout=config.rca.max_step_timeout,
            max_total_timeout=config.rca.max_total_timeout,
        )

        # 构建输入：对 Skill 的 input_schema 中未提供的字段，按类型填充默认空值
        # 避免设置 None 导致工具参数校验失败（如 namespace should be string）
        inputs: dict = {}
        for key, schema_type in skill.input_schema.items():
            inputs[key] = RCARouter._get_default_value_by_schema_type(schema_type)

        logger.debug(
            f"[WEB][user_input追踪] _execute_rca_skill 构建的 inputs={inputs!r}, "
            f"skill.input_schema={skill.input_schema!r}"
        )

        # 定义流式回调：将每一步的执行状态（开始/完成/失败）实时通知前端
        async def rca_stream_callback(step_id: str, output: dict):
            # ── DEBUG: 打印原始 output 的所有 key 及值类型 ──
            logger.debug(
                f"[RCA_STREAM][{step_id}] output keys: {list(output.keys())}"
            )
            for k, v in output.items():
                v_preview = repr(v)[:200] if not k.startswith("_") else repr(v)[:80]
                logger.debug(
                    f"[RCA_STREAM][{step_id}]   key={k!r}, type={type(v).__name__}, "
                    f"value_preview={v_preview}"
                )

            status = output.get("_status", "completed")
            step_index = output.get("_step_index", 0)
            total_steps = output.get("_total_steps", 0)
            step_type = output.get("_step_type", "")
            duration = output.get("_duration", 0)
            commands = output.get("_commands", [])
            error_msg = output.get("_error", "")
            step_desc = output.get("_step_description", "")

            step_label = f"[{step_index}/{total_steps}] {step_id}"

            if status == "start":
                # ── 步骤开始 ──
                logger.debug(
                    f"[RCA_STREAM][{step_id}] status=start, "
                    f"step_index={step_index}/{total_steps}, type={step_type}"
                )
                step_msg = {
                    "type": "stream_chunk",
                    "content_type": "tool",
                    "content": f"🔄 RCA 步骤 {step_label} 开始执行",
                    "is_tool_call": True,
                    "tool_name": step_label,
                    "tool_status": "start",
                    "tool_args": {
                        "step_type": step_type,
                        "command": step_desc,
                    },
                    "rca_step_index": step_index,
                    "rca_total_steps": total_steps,
                    "rca_step_type": step_type,
                    "timestamp": time.time(),
                }
                await ws_queue.send_text(json.dumps(step_msg, ensure_ascii=False))
                return
            elif status == "error":
                # ── 步骤失败 ──
                logger.debug(
                    f"[RCA_STREAM][{step_id}] status=error, "
                    f"step_index={step_index}/{total_steps}, error={error_msg!r}"
                )
                step_msg = {
                    "type": "stream_chunk",
                    "content_type": "tool",
                    "content": f"❌ RCA 步骤 {step_label} 执行失败",
                    "is_tool_call": True,
                    "tool_name": step_label,
                    "tool_status": "error",
                    "tool_error": error_msg,
                    "rca_step_index": step_index,
                    "rca_total_steps": total_steps,
                    "rca_step_type": step_type,
                    "rca_duration": round(duration, 2) if duration else 0,
                    "timestamp": time.time(),
                }
                await ws_queue.send_text(json.dumps(step_msg, ensure_ascii=False))
                return
            else:
                # ── 步骤完成 ──
                # 过滤掉内部元数据字段（以 _ 开头），只保留业务输出
                business_output = {
                    k: v for k, v in output.items() if not k.startswith("_")
                }

                # ── DEBUG: 打印 business_output 的 key ──
                logger.debug(
                    f"[RCA_STREAM][{step_id}] business_output keys: "
                    f"{list(business_output.keys())}"
                )
                for k, v in business_output.items():
                    v_preview = repr(v)[:200]
                    logger.debug(
                        f"[RCA_STREAM][{step_id}]   biz key={k!r}, "
                        f"type={type(v).__name__}, preview={v_preview}"
                    )

                # 获取完整结果文本（不做截断）
                raw_result = business_output.get("result", "")
                logger.debug(
                    f"[RCA_STREAM][{step_id}] raw_result type={type(raw_result).__name__}, "
                    f"truthy={bool(raw_result)}, is_str={isinstance(raw_result, str)}, "
                    f"preview={repr(raw_result)[:300]}"
                )

                if raw_result and isinstance(raw_result, str):
                    full_result = raw_result
                else:
                    full_result = json.dumps(
                        business_output, ensure_ascii=False, default=str
                    )

                logger.debug(
                    f"[RCA_STREAM][{step_id}] full_result len={len(full_result)}, "
                    f"lines={full_result.count(chr(10))+1}, "
                    f"first_200_chars={full_result[:200]!r}"
                )

                # 构建命令JSON数组字符串（前端按每行展示）
                command_json = json.dumps(commands or [], ensure_ascii=False)

                # 为当前步骤生成唯一 chunk_id，前端用它来关联追加内容
                chunk_id = f"rca-chunk-{step_index}-{int(time.time()*1000)}"

                # 先发送 completed 消息（不含 tool_result，前端创建卡片骨架）
                step_msg = {
                    "type": "stream_chunk",
                    "content_type": "tool",
                    "content": f"✅ RCA 步骤 {step_label} 执行完成",
                    "is_tool_call": True,
                    "tool_name": step_label,
                    "tool_status": "completed",
                    "tool_result": "",
                    "tool_args": {
                        "step_type": step_type,
                        "command": command_json,
                    },
                    "rca_step_index": step_index,
                    "rca_total_steps": total_steps,
                    "rca_step_type": step_type,
                    "rca_duration": round(duration, 2) if duration else 0,
                    "rca_chunk_id": chunk_id,
                    "timestamp": time.time(),
                }
                await ws_queue.send_text(json.dumps(step_msg, ensure_ascii=False))

                # 将完整结果按每 10 行分块，逐块推送（不截断）
                if full_result:
                    lines = full_result.split("\n")
                    chunk_size = 10
                    total_chunks = (len(lines) + chunk_size - 1) // chunk_size
                    logger.debug(
                        f"[RCA_STREAM][{step_id}] 分块推送: "
                        f"total_lines={len(lines)}, chunk_size={chunk_size}, "
                        f"total_chunks={total_chunks}, chunk_id={chunk_id}"
                    )
                    for i in range(0, len(lines), chunk_size):
                        chunk_text = "\n".join(lines[i:i + chunk_size])
                        chunk_idx = i // chunk_size + 1
                        logger.debug(
                            f"[RCA_STREAM][{step_id}] 发送 chunk {chunk_idx}/{total_chunks}, "
                            f"len={len(chunk_text)}"
                        )
                        chunk_msg = {
                            "type": "stream_chunk",
                            "content_type": "tool",
                            "tool_status": "result_chunk",
                            "rca_chunk_id": chunk_id,
                            "rca_step_index": step_index,
                            "tool_result_chunk": chunk_text,
                            "timestamp": time.time(),
                        }
                        await ws_queue.send_text(json.dumps(chunk_msg, ensure_ascii=False))

                # 发送结果结束标记
                done_msg = {
                    "type": "stream_chunk",
                    "content_type": "tool",
                    "tool_status": "result_done",
                    "rca_chunk_id": chunk_id,
                    "rca_step_index": step_index,
                    "timestamp": time.time(),
                }
                await ws_queue.send_text(json.dumps(done_msg, ensure_ascii=False))
                return

        # 执行 Skill
        logger.debug(
            f"[WEB][user_input追踪] _execute_rca_skill → engine.execute, "
            f"user_input={user_input!r}"
        )
        # 使用 tracker 追踪所有异步回调 task，确保 chunk 全部发送完再返回
        callback_tracker = _AsyncCallbackTracker()

        execution_context = {"user_input": user_input}
        if file_path:
            execution_context["file_path"] = file_path

        report = await engine.execute(
            skill=skill,
            inputs=inputs,
            stream_callback=lambda step_id, output: callback_tracker.fire(
                rca_stream_callback, step_id, output
            ),
            session_id="web:ui",
            context=execution_context,
        )

        # 等待所有异步回调完成（chunk 全部发送到 WebSocket）
        await callback_tracker.wait_all()
        logger.debug("[RCA_STREAM] 所有异步回调 task 已完成，可安全发送 completion")

        return report

    except Exception as e:
        logger.error(f"[WEB] RCA Skill 执行失败: {e}")
        return None


class _AsyncCallbackTracker:
    """追踪所有通过 _sync_to_async_callback 创建的异步 task，
    确保在 engine.execute() 返回后能 await 所有未完成的 task。
    解决 fire-and-forget 导致 chunk 消息被 completion 消息抢先的问题。
    """

    def __init__(self):
        self._tasks: list[asyncio.Task] = []

    def fire(self, async_fn, *args):
        """同步入口：创建 task 并记录到列表。"""
        try:
            loop = asyncio.get_running_loop()
            task = loop.create_task(async_fn(*args))
            self._tasks.append(task)
        except RuntimeError:
            asyncio.run(async_fn(*args))

    async def wait_all(self):
        """等待所有已创建的 task 完成（在发送 completion 之前调用）。"""
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
            self._tasks.clear()


async def _run_agent_loop(
    user_input: str,
    ws_queue: WebSocketMessageQueue,
    additional_context: str | None = None,
    tool_names_filter: list[str] | None = None,
    file_path: str | None = None,
):

    """通用的 agent loop 执行入口（用于 D 类简单操作和复杂操作回退）。"""
    import time
    import json

    # 设置流式回调函数
    async def stream_callback(context_info: dict):
        """流式输出回调函数，按类型分类显示内容"""
        content = context_info.get('content', '')

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
            'iteration_count': iteration_count,
        }

        # 如果是工具调用，添加工具名称和状态信息
        if content_type == 'tool':
            message_data['tool_name'] = context_info.get('tool_name', '')
            message_data['tool_status'] = context_info.get('tool_status', '')
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

        await ws_queue.send_text(json.dumps(message_data, ensure_ascii=False))

    # 为agent_loop设置流式回调
    agent_loop.stream_callback = stream_callback

    merged_context = additional_context
    if file_path:
        upload_context = (
            "## 用户上传文件\n"
            f"用户上传文件路径: {file_path}\n"
            "若请求与该文件相关，请将其作为主要上下文进行分析。"
        )
        merged_context = f"{additional_context}\n\n{upload_context}" if additional_context else upload_context

    # Process with streaming output
    response = await agent_loop.process_direct(
        user_input,
        session_key="cli:webui",
        additional_context=merged_context,
        disable_auto_kb=True,
        tool_names_filter=tool_names_filter,
    )

    # Send the actual response
    if response and response.strip():
        await ws_queue.send_text("\n" + response)
    elif not response:
        await ws_queue.send_text("No response from agent.")

    end_time = time.time()

    # 发送处理完成状态消息
    completion_message = {
        'type': 'stream_chunk',
        'content_type': 'completion',
        'content': '处理完成',
        'is_completed': True,
        'timestamp': end_time,
    }
    await ws_queue.send_text(json.dumps(completion_message, ensure_ascii=False))


async def process_user_message(user_input: str) -> str:
    """Process user message using nanobot's AgentLoop."""

    # Check if provider and agent_loop are initialized
    if not provider or not agent_loop:
        return "Error: Web UI resources not initialized. Please restart the server."

    response = await agent_loop.process_direct(user_input, session_key="cli:webui")

    if response:
        return response
    else:
        return "No response from agent."


@web_app.post("/api/chat")
async def chat_endpoint(message: dict):
    """Handle chat API requests."""
    user_input = message.get("message", "")
    if not user_input:
        return {"error": "No message provided"}

    response = await process_user_message(user_input)
    return {"response": response}


