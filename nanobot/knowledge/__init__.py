"""Knowledge base module for storing and retrieving domain-specific knowledge."""

from .rag_config import RAGConfig
from .vector_embedder import VectorEmbedder, EmbeddingModelError

try:
    from .store import KnowledgeStore, ChromaKnowledgeStore, DomainKnowledgeManager
    from .rocketmq_init import RocketMQKnowledgeInitializer, initialize_rocketmq_knowledge
except ImportError:
    KnowledgeStore = None  # type: ignore[assignment,misc]
    ChromaKnowledgeStore = None  # type: ignore[assignment,misc]
    DomainKnowledgeManager = None  # type: ignore[assignment,misc]
    RocketMQKnowledgeInitializer = None  # type: ignore[assignment,misc]
    initialize_rocketmq_knowledge = None  # type: ignore[assignment,misc]

__all__ = [
    "KnowledgeStore",
    "ChromaKnowledgeStore",
    "DomainKnowledgeManager",
    "RocketMQKnowledgeInitializer",
    "initialize_rocketmq_knowledge",
    "RAGConfig",
    "VectorEmbedder",
    "EmbeddingModelError",
]
