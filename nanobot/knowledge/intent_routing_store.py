"""Intent routing vector stores for ops tools and skills."""

from __future__ import annotations

import asyncio
import json
import re
import time
from datetime import datetime
from pathlib import Path
from threading import Lock, Thread
from typing import Any

from mcp import ClientSession
from mcp.client.sse import sse_client

import chromadb
from chromadb.config import Settings
from loguru import logger

from nanobot.agent.skills import SkillsLoader
from nanobot.knowledge.rag_config import RAGConfig
from nanobot.knowledge.text_chunker import TextChunker
from nanobot.knowledge.vector_embedder import VectorEmbedder
from nanobot.utils.helpers import ensure_dir
from nanobot.metrics import (
    RAG_QUERY_DURATION,
    RAG_EMBEDDING_DURATION,
    RAG_QUERY_RESULTS_COUNT,
    RAG_QUERY_TOTAL,
)

TOOLS_COLLECTION = "ops_tools"
SKILLS_COLLECTION = "skills_docs"
RCA_SKILLS_COLLECTION = "rca_skills"


def _strip_frontmatter(content: str) -> str:
    if content.startswith("---"):
        m = re.match(r"^---\n.*?\n---\n", content, re.DOTALL)
        if m:
            return content[m.end():].strip()
    return content


def _extract_mcp_tool_schema_fields(tool_def: dict[str, Any]) -> tuple[str, str, Any]:
    """Extract MCP tool name/description/input schema from flexible tool definition formats."""
    # 兼容 OpenAI function schema: {"type":"function", "function": {...}}
    fn = tool_def.get("function") if isinstance(tool_def, dict) else None
    if isinstance(fn, dict):
        return (
            str(fn.get("name", "") or ""),
            str(fn.get("description", "") or ""),
            fn.get("parameters", {}),
        )

    # 兼容 MCP toolSpec schema: {"toolName": "...", "toolSpec": {...}}
    tool_spec = tool_def.get("toolSpec") if isinstance(tool_def, dict) else None
    if isinstance(tool_spec, dict):
        name = str(tool_def.get("toolName") or tool_spec.get("name") or "")
        desc = str(tool_spec.get("description") or "")
        params = tool_spec.get("inputSchema", tool_spec.get("parameters", {}))
        return name, desc, params

    # 兼容扁平 schema: {"name": "...", "description": "...", "inputSchema": {...}}
    name = str(tool_def.get("name", "") or "") if isinstance(tool_def, dict) else ""
    desc = str(tool_def.get("description", "") or "") if isinstance(tool_def, dict) else ""
    params = tool_def.get("inputSchema", tool_def.get("parameters", {})) if isinstance(tool_def, dict) else {}
    return name, desc, params


def _read_cfg(server_cfg: Any, key: str, default: Any = None) -> Any:
    if isinstance(server_cfg, dict):
        return server_cfg.get(key, default)
    return getattr(server_cfg, key, default)


def _join_server_url(base_url: str, path: str) -> str:
    base = (base_url or "").strip()
    if not base:
        return ""
    p = (path or "").strip()
    if not p:
        return base
    return f"{base.rstrip('/')}" + (p if p.startswith("/") else f"/{p}")


def _extract_tools_from_list_tools_result(result: Any) -> list[dict[str, Any]]:
    if result is None:
        return []

    if hasattr(result, "model_dump"):
        result = result.model_dump()

    if hasattr(result, "tools"):
        tools = getattr(result, "tools", None)
        if isinstance(tools, list):
            return [t.model_dump() if hasattr(t, "model_dump") else t for t in tools if isinstance(t, dict) or hasattr(t, "model_dump")]

    if isinstance(result, dict):
        tools = result.get("tools")
        if isinstance(tools, list):
            return [t for t in tools if isinstance(t, dict)]

    return []


async def _fetch_mcp_tools_from_server_async(
    base_url: str,
    auth_token: str = "",
    timeout: int = 10,
) -> list[dict[str, Any]]:
    headers = {"Authorization": f"Bearer {auth_token}"} if auth_token else {}

    async with asyncio.timeout(timeout):
        async with sse_client(base_url, headers=headers) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                result = await session.list_tools()
                return _extract_tools_from_list_tools_result(result)


def _run_async_blocking(coro: Any) -> Any:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    holder: dict[str, Any] = {}
    errors: dict[str, Exception] = {}

    def _runner() -> None:
        try:
            holder["value"] = asyncio.run(coro)
        except Exception as e:
            errors["error"] = e

    thread = Thread(target=_runner, daemon=True)
    thread.start()
    thread.join()

    if "error" in errors:
        raise errors["error"]

    return holder.get("value")


def _fetch_mcp_tools_from_server(base_url: str, auth_token: str = "", timeout: int = 10) -> list[dict[str, Any]]:
    """Call MCP server by mcp SSE client and return full tool definitions."""
    if not base_url:
        return []

    try:
        tools = _run_async_blocking(
            _fetch_mcp_tools_from_server_async(
                base_url=base_url,
                auth_token=auth_token,
                timeout=timeout,
            )
        )
        return tools if isinstance(tools, list) else []
    except TimeoutError as e:
        logger.debug(f"[ROUTING] MCP tools/list timeout for {base_url}: {e}")
    except Exception as e:
        logger.warning(f"[ROUTING] MCP tools/list unexpected error for {base_url}: {e}")

    return []


class IntentRoutingStore:

    """Separate vector stores for ops/tools and skills retrieval."""

    def __init__(self, workspace: Path, config: Any):
        self.workspace = workspace
        self.config = config
        self.rag_config = self._build_rag_config(config)
        self.chunker = TextChunker(
            chunk_size=self.rag_config.chunk_size,
            chunk_overlap=self.rag_config.chunk_overlap,
        )
        self.embedder = VectorEmbedder(self.rag_config.embedding_model)

        self.tools_dir = ensure_dir(workspace / "tools_index")
        self.skills_dir = ensure_dir(workspace / "skills_index")
        self.tools_chroma_dir = ensure_dir(self.tools_dir / "chroma_db")
        self.skills_chroma_dir = ensure_dir(self.skills_dir / "chroma_db")

        self.tools_client = chromadb.PersistentClient(
            path=str(self.tools_chroma_dir),
            settings=Settings(anonymized_telemetry=False, allow_reset=True),
        )
        self.skills_client = chromadb.PersistentClient(
            path=str(self.skills_chroma_dir),
            settings=Settings(anonymized_telemetry=False, allow_reset=True),
        )

        # skills 初始化状态文件
        self.skills_init_status_file = self.skills_dir / "init_status.json"

    @staticmethod
    def _build_rag_config(cfg: Any) -> RAGConfig:
        # 如果传入的本身就是 RAGConfig，直接使用
        if isinstance(cfg, RAGConfig):
            return cfg
        rag = RAGConfig()
        if hasattr(cfg, "agents") and hasattr(cfg.agents, "defaults"):
            d = cfg.agents.defaults
            for attr in ("embedding_model", "chunk_size", "chunk_overlap",
                         "top_k", "similarity_threshold", "batch_size", "timeout"):
                if hasattr(d, attr):
                    setattr(rag, attr, getattr(d, attr))
        return rag

    def _get_or_create(self, client: chromadb.ClientAPI, name: str):
        try:
            return client.get_collection(name=name)
        except Exception:
            return client.create_collection(name=name)

    def init_tools_index(self, tool_schemas: list[dict[str, Any]], mcp_servers: dict[str, Any] | None = None) -> int:
        """Build/refresh tools collection from ToolRegistry schemas and MCP servers full tools."""
        collection = self._get_or_create(self.tools_client, TOOLS_COLLECTION)

        docs: list[str] = []
        ids: list[str] = []
        metas: list[dict[str, Any]] = []

        for schema in tool_schemas:
            fn = schema.get("function", {})
            name = fn.get("name", "")
            if not name:
                continue
            desc = fn.get("description", "")
            params = fn.get("parameters", {})
            doc = (
                f"tool_name: {name}\n"
                f"description: {desc}\n"
                f"parameters: {params}\n"
                f"usage: 使用该工具完成运维查询、执行、读取或写入任务。"
            )
            docs.append(doc)
            ids.append(f"tool::{name}")
            metas.append({"source": "registry_tool", "tool_name": name})

        for server_name, server_cfg in (mcp_servers or {}).items():
            if not _read_cfg(server_cfg, "enabled", False):
                continue

            server_url = str(_read_cfg(server_cfg, "server_url", "") or "")
            auth_token = str(_read_cfg(server_cfg, "auth_token", "") or "")

            # 优先从 MCP list-tools 端点动态拉取，确保入库“全部工具定义”
            server_tools = _fetch_mcp_tools_from_server(base_url=server_url, auth_token=auth_token)

            # 动态拉取失败时，回退到本地配置中可能携带的 tools 字段
            if not server_tools:
                server_tools = (
                    _read_cfg(server_cfg, "tools", None)
                    or _read_cfg(server_cfg, "tool_specs", None)
                    or []
                )
                if server_tools:
                    logger.warning(
                        f"[ROUTING] MCP tools/list unavailable for {server_name}, fallback to local configured tools"
                    )

            if not server_tools:
                # 无工具清单时保留 server 级描述，便于检索命中到 MCP 入口
                doc = (
                    f"mcp_server: {server_name}\n"
                    f"server_url: {server_url}\n"
                    f"tool_name: use_mcp_tool\n"
                    f"description: 通过 MCP 服务 {server_name} 调用其提供的工具能力。"
                )
                ids.append(f"mcp::{server_name}::use_mcp_tool")
                docs.append(doc)
                metas.append({"source": "mcp_server", "server_name": server_name, "tool_name": "use_mcp_tool"})
                continue

            for tool_def in server_tools:
                tool_name, tool_desc, tool_params = _extract_mcp_tool_schema_fields(tool_def)
                if not tool_name:
                    logger.debug(f"[ROUTING] discovered MCP tool without valid name on server {server_name}, skipped")
                    continue

                logger.debug(f"[ROUTING] discovered MCP tool: server={server_name}, tool={tool_name}")

                doc = (
                    f"mcp_server: {server_name}\n"
                    f"server_url: {server_url}\n"
                    f"tool_name: {tool_name}\n"
                    f"description: {tool_desc}\n"
                    f"parameters: {tool_params}\n"
                    f"usage: 通过 MCP 服务调用该工具完成外部系统查询或操作。"
                )
                ids.append(f"mcp::{server_name}::{tool_name}")
                docs.append(doc)
                metas.append(
                    {
                        "source": "mcp_tool",
                        "server_name": server_name,
                        "tool_name": tool_name,
                        "server_url": server_url,
                    }
                )

        if not docs:
            logger.warning("[ROUTING] tools index has no docs to index")
            return 0

        embeddings = self.embedder.embed_batch(docs)
        collection.upsert(ids=ids, documents=docs, metadatas=metas, embeddings=embeddings)
        logger.info(f"[ROUTING] tools index initialized: {len(docs)} docs")
        return len(docs)

    def init_skills_index(self, skills_loader: SkillsLoader) -> int:
        """Build/refresh skills collection from SKILL.md content.

        如果 init_status.json 已存在，则跳过初始化直接返回。
        """
        # 已初始化则跳过
        if self.skills_init_status_file.exists():
            try:
                status = json.loads(self.skills_init_status_file.read_text(encoding="utf-8"))
                chunk_count = status.get("chunk_count", 0)
                initialized_at = status.get("initialized_at", "")
                logger.info(f"[ROUTING] ✅ skills 索引已初始化，跳过本次初始化")
                logger.info(f"   - 向量化分块数: {chunk_count}")
                logger.info(f"   - 初始化时间: {initialized_at}")
                return chunk_count
            except Exception as e:
                logger.warning(f"[ROUTING] 读取 skills 初始化状态文件失败: {e}，将重新初始化")

        logger.info("[ROUTING] 🚀 开始初始化 skills 索引...")
        start_time = time.time()

        collection = self._get_or_create(self.skills_client, SKILLS_COLLECTION)
        docs: list[str] = []
        ids: list[str] = []
        metas: list[dict[str, Any]] = []

        skills = skills_loader.list_skills(filter_unavailable=False)
        for skill in skills:
            skill_name = skill["name"]
            skill_path = skill["path"]
            raw = skills_loader.load_skill(skill_name) or ""
            content = _strip_frontmatter(raw)
            if not content.strip():
                logger.info(f"[ROUTING] 📄 skill 文件: {skill_path}  (内容为空，跳过)")
                continue
            chunks = self.chunker.chunk_text(
                content,
                metadata={"skill_name": skill_name, "path": skill_path, "source": skill["source"]},
            )
            valid_chunks = []
            for chunk in chunks:
                text = chunk["text"].replace("CHUNK_BOUNDARY", "").strip()
                if not text:
                    continue
                meta = chunk["metadata"]
                idx = int(meta.get("chunk_index", 0))
                doc_id = f"skill::{skill_name}::{idx}"
                ids.append(doc_id)
                docs.append(text)
                metas.append(
                    {
                        "source": "skill",
                        "skill_name": skill_name,
                        "path": meta.get("path", ""),
                        "skill_source": meta.get("source", ""),
                        "chunk_index": idx,
                    }
                )
                valid_chunks.append((idx, text))

            # 打印文件名和分块摘要
            logger.info(f"[ROUTING] 📄 skill 文件: {skill_path}  共 {len(valid_chunks)} 个分块")
            for idx, text in valid_chunks:
                preview = text[:80].replace("\n", " ")
                logger.info(f"   [{idx}] {preview}...")

        if not docs:
            logger.warning("[ROUTING] skills index has no docs to index")
            return 0

        embeddings = self.embedder.embed_batch(docs)
        collection.upsert(ids=ids, documents=docs, metadatas=metas, embeddings=embeddings)

        elapsed = time.time() - start_time

        # 写入初始化状态文件
        try:
            status = {
                "initialized_at": datetime.now().isoformat(),
                "chunk_count": len(docs),
                "skill_count": len(skills),
                "elapsed_seconds": round(elapsed, 2),
            }
            self.skills_init_status_file.write_text(
                json.dumps(status, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            logger.warning(f"[ROUTING] 保存 skills 初始化状态文件失败: {e}")

        logger.info(f"[ROUTING] ✅ skills 索引初始化完成:")
        logger.info(f"   - skill 数量: {len(skills)}")
        logger.info(f"   - 向量化分块数: {len(docs)}")
        logger.info(f"   - 耗时: {elapsed:.2f} 秒")
        return len(docs)

    def search_tools(self, query: str, limit: int = 2) -> list[dict[str, Any]]:
        collection = self._get_or_create(self.tools_client, TOOLS_COLLECTION)
        return self._query_collection(collection, query, limit, operation="intent_routing_tools")

    def search_skills(self, query: str, limit: int = 2) -> list[dict[str, Any]]:
        collection = self._get_or_create(self.skills_client, SKILLS_COLLECTION)
        return self._query_collection(collection, query, limit, operation="intent_routing_skills")

    # ----- RCA Skill 检索方法 -----

    def init_rca_skills_index(self, rca_loader: Any) -> int:
        """构建 RCA Skill 向量索引。

        将所有已加载的 RCA Skill 的名称、描述和步骤信息向量化存储，
        供后续检索匹配使用。

        Args:
            rca_loader: RCASkillLoader 实例

        Returns:
            成功索引的 Skill 数量
        """
        collection = self._get_or_create(self.skills_client, RCA_SKILLS_COLLECTION)

        docs: list[str] = []
        ids: list[str] = []
        metas: list[dict[str, Any]] = []

        skills = rca_loader.list_skills()
        for skill_info in skills:
            name = skill_info.get("name", "")
            if not name:
                continue

            description = skill_info.get("description", "")
            steps_count = skill_info.get("steps_count", "0")

            # 获取完整 Skill 对象以拼接步骤描述
            skill_obj = rca_loader.get_skill(name)
            steps_desc = ""
            if skill_obj and hasattr(skill_obj, "steps"):
                steps_desc = " → ".join(
                    f"{s.id}({s.type.value})" for s in skill_obj.steps
                )

            doc_text = (
                f"RCA Skill: {name}\n"
                f"描述: {description}\n"
                f"类型: {skill_info.get('type', 'workflow')}\n"
                f"步骤({steps_count}): {steps_desc}"
            )

            ids.append(f"rca_skill::{name}")
            docs.append(doc_text)
            metas.append({
                "source": "rca_skill",
                "skill_name": name,
                "version": skill_info.get("version", ""),
                "file_path": skill_info.get("file_path", ""),
            })

        if not docs:
            logger.info("[ROUTING] RCA skills 索引无文档可索引")
            return 0

        embeddings = self.embedder.embed_batch(docs)
        collection.upsert(ids=ids, documents=docs, metadatas=metas, embeddings=embeddings)
        logger.info(f"[ROUTING] ✅ RCA skills 索引已构建: {len(docs)} 个 Skill")
        return len(docs)

    def register_rca_skill(self, skill_name: str, doc_text: str) -> None:
        """注册单个 RCA Skill 到向量索引。

        用于热加载时增量更新索引。

        Args:
            skill_name: Skill 名称
            doc_text: Skill 的可检索文本描述
        """
        collection = self._get_or_create(self.skills_client, RCA_SKILLS_COLLECTION)
        embeddings = self.embedder.embed_batch([doc_text])
        collection.upsert(
            ids=[f"rca_skill::{skill_name}"],
            documents=[doc_text],
            metadatas=[{"source": "rca_skill", "skill_name": skill_name}],
            embeddings=embeddings,
        )
        logger.debug(f"[ROUTING] RCA Skill '{skill_name}' 已注册到向量索引")

    def remove_rca_skill(self, skill_name: str) -> None:
        """从向量索引中移除 RCA Skill。

        Args:
            skill_name: Skill 名称
        """
        try:
            collection = self._get_or_create(self.skills_client, RCA_SKILLS_COLLECTION)
            collection.delete(ids=[f"rca_skill::{skill_name}"])
            logger.debug(f"[ROUTING] RCA Skill '{skill_name}' 已从向量索引移除")
        except Exception as e:
            logger.warning(f"[ROUTING] 移除 RCA Skill '{skill_name}' 索引失败: {e}")

    def search_rca_skill(self, query: str, limit: int = 1) -> list[dict[str, Any]]:
        """根据故障描述检索最匹配的 RCA Skill。

        Args:
            query: 故障描述文本
            limit: 返回结果数量上限

        Returns:
            匹配结果列表，每项包含 document、metadata、distance
        """
        collection = self._get_or_create(self.skills_client, RCA_SKILLS_COLLECTION)
        return self._query_collection(
            collection, query, limit, operation="rca_skill_search"
        )

    def _query_collection(self, collection: Any, query: str, limit: int, operation: str = "intent_routing") -> list[dict[str, Any]]:
        query_start = time.time()
        emb = self.embedder.embed_text(query)
        embed_duration = time.time() - query_start

        # Prometheus: 记录向量化耗时
        RAG_EMBEDDING_DURATION.labels(operation=operation).observe(embed_duration)

        search_start = time.time()
        res = collection.query(
            query_embeddings=[emb],
            n_results=max(1, limit),
            include=["documents", "metadatas", "distances"],
        )
        search_duration = time.time() - search_start

        docs = (res.get("documents") or [[]])[0]
        metas = (res.get("metadatas") or [[]])[0]
        dists = (res.get("distances") or [[]])[0]
        ids = (res.get("ids") or [[]])[0]
        results: list[dict[str, Any]] = []
        for i in range(min(len(docs), len(metas))):
            results.append(
                {
                    "id": ids[i] if i < len(ids) else "",
                    "document": docs[i],
                    "metadata": metas[i] or {},
                    "distance": dists[i] if i < len(dists) else None,
                }
            )

        total_duration = time.time() - query_start

        # Prometheus: 记录向量数据库查询耗时、结果数和计数
        RAG_QUERY_DURATION.labels(operation=operation, domain="intent_routing", status="success").observe(search_duration)
        RAG_QUERY_RESULTS_COUNT.labels(operation=operation, domain="intent_routing").observe(len(results))
        RAG_QUERY_TOTAL.labels(operation=operation, domain="intent_routing", status="success").inc()

        return results


_CACHE: dict[str, IntentRoutingStore] = {}
_LOCK = Lock()


def get_intent_routing_store(workspace: Path, config: Any) -> IntentRoutingStore:
    key = str(workspace.expanduser().resolve())
    cached = _CACHE.get(key)
    if cached is not None:
        return cached
    with _LOCK:
        cached = _CACHE.get(key)
        if cached is not None:
            return cached
        store = IntentRoutingStore(workspace, config)
        _CACHE[key] = store
        return store