"""内置源代码检索工具。

注册为 Agent 的内置工具，支持通过语义查询检索相关源代码片段。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from loguru import logger

from nanobot.agent.tools.base import Tool


class SourceCodeSearchTool(Tool):
    """源代码语义检索工具。

    通过 CodeBERT 编码查询 → ChromaDB 语义搜索，
    在源代码向量数据库中检索相关代码片段。
    """

    @property
    def name(self) -> str:
        return "search_source_code"

    @property
    def description(self) -> str:
        return (
            "Search the source code knowledge base using semantic similarity. "
            "Use this to find relevant code snippets, functions, classes, "
            "or configuration files across indexed source code domains. "
            "Supports searching within a specific domain or across all domains."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query describing the code you're looking for "
                                   "(e.g., 'message queue consumer initialization', "
                                   "'database connection pool configuration')",
                },
                "domain": {
                    "type": "string",
                    "description": "Optional: specific domain to search in "
                                   "(e.g., 'rocketmq', 'payment-service'). "
                                   "If not specified, searches across all domains.",
                },
                "top_k": {
                    "type": "integer",
                    "description": "Number of results to return (default: 3, max: 10)",
                    "minimum": 1,
                    "maximum": 10,
                    "default": 3,
                },
            },
            "required": ["query"],
        }

    async def execute(
        self,
        query: str,
        domain: Optional[str] = None,
        top_k: int = 3,
    ) -> dict[str, Any]:
        """执行源代码检索。"""
        try:
            from nanobot.config.loader import load_config

            config = load_config()
            workspace = Path(config.agents.defaults.workspace)

            # 延迟导入以避免循环依赖
            from nanobot.knowledge.source_code.store import SourceCodeRAGStore

            knowledge_dir = workspace / "knowledge"
            store = SourceCodeRAGStore(knowledge_dir=knowledge_dir)

            if not store.is_ready:
                return self.make_result(
                    "源代码 RAG 存储未就绪（CodeBERT 模型或 ChromaDB 不可用）。"
                    "请检查模型配置和依赖。"
                )

            logger.info(f"[SourceCodeSearch] 🔍 查询: {query}, 领域: {domain or '全部'}, top_k: {top_k}")

            results = store.search(
                query=query,
                domain=domain,
                top_k=top_k,
            )

            if not results:
                msg = f"未找到与查询 '{query}' 相关的源代码"
                if domain:
                    msg += f"（领域: {domain}）"
                msg += "。请确认源代码已被索引，或尝试调整查询关键词。"
                logger.info(f"[SourceCodeSearch] ⚠️ {msg}")
                return self.make_result(msg)

            # 格式化结果
            formatted = []
            for i, item in enumerate(results, 1):
                meta = item.get("metadata", {})
                formatted.append(
                    f"### {i}. {meta.get('file_path', 'unknown')}\n"
                    f"**领域**: {item.get('domain', 'unknown')} | "
                    f"**语言**: {meta.get('language', 'unknown')} | "
                    f"**相似度**: {item.get('score', 0):.3f} | "
                    f"**节点类型**: {meta.get('node_type', 'unknown')}\n\n"
                    f"```{meta.get('language', '')}\n"
                    f"{item.get('content', '')}\n"
                    f"```\n"
                )

            result_text = (
                f"找到 {len(results)} 个相关源代码片段:\n\n"
                + "\n---\n".join(formatted)
            )

            logger.info(f"[SourceCodeSearch] ✅ 返回 {len(results)} 个结果")
            return self.make_result(result_text)

        except Exception as exc:
            error_msg = f"源代码检索失败: {str(exc)}"
            logger.error(f"[SourceCodeSearch] ❌ {error_msg}")
            return self.make_result(error_msg)
