"""源代码 RAG 子模块 —— 完全独立于现有知识库体系。

本模块提供源代码的向量化索引、语义检索等能力，
使用独立的 CodeBERT 模型和 ChromaDB 数据库实例。
"""

try:
    from nanobot.knowledge.source_code.store import SourceCodeRAGStore
    from nanobot.knowledge.source_code.init_status import SourceCodeInitStatus
except ImportError:
    SourceCodeRAGStore = None  # type: ignore[assignment,misc]
    SourceCodeInitStatus = None  # type: ignore[assignment,misc]

__all__ = [
    "SourceCodeRAGStore",
    "SourceCodeInitStatus",
]
