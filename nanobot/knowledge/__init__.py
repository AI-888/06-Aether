"""Knowledge base module for storing and retrieving domain-specific knowledge."""

from .rag_config import RAGConfig
from .rocketmq_init import RocketMQKnowledgeInitializer, initialize_rocketmq_knowledge
from .store import KnowledgeStore, ChromaKnowledgeStore, DomainKnowledgeManager
from .vector_embedder import VectorEmbedder, EmbeddingModelError

__all__ = [
    "KnowledgeStore",  # 默认使用 ChromaKnowledgeStore
    "ChromaKnowledgeStore",  # 向量数据库存储
    "DomainKnowledgeManager",
    "RocketMQKnowledgeInitializer",
    "initialize_rocketmq_knowledge",
    "RAGConfig",
    "VectorEmbedder",
    "EmbeddingModelError",
]
