"""Shared factory for ChromaKnowledgeStore singleton instances."""

from pathlib import Path
from threading import Lock
from typing import Any

from loguru import logger

from nanobot.config.loader import load_config
from nanobot.knowledge.rag_config import RAGConfig
from nanobot.knowledge.store import ChromaKnowledgeStore

_STORE_CACHE: dict[str, ChromaKnowledgeStore] = {}
_STORE_LOCK = Lock()


def build_rag_config(cfg: Any) -> RAGConfig:
    """Build RAG config from app config object."""
    rag_config = RAGConfig()

    # 从config.json的agents.defaults中读取RAG配置
    if hasattr(cfg, "agents") and hasattr(cfg.agents, "defaults"):
        defaults = cfg.agents.defaults
        if hasattr(defaults, "embedding_model"):
            rag_config.embedding_model = defaults.embedding_model
        if hasattr(defaults, "chunk_size"):
            rag_config.chunk_size = defaults.chunk_size
        if hasattr(defaults, "chunk_overlap"):
            rag_config.chunk_overlap = defaults.chunk_overlap
        if hasattr(defaults, "top_k"):
            rag_config.top_k = defaults.top_k
        if hasattr(defaults, "similarity_threshold"):
            rag_config.similarity_threshold = defaults.similarity_threshold
        if hasattr(defaults, "batch_size"):
            rag_config.batch_size = defaults.batch_size
        if hasattr(defaults, "timeout"):
            rag_config.timeout = defaults.timeout

    # 从rerank配置中读取
    if hasattr(cfg, "rerank"):
        if hasattr(cfg.rerank, "model_path") and cfg.rerank.model_path:
            rag_config.rerank_model_path = cfg.rerank.model_path
        if hasattr(cfg.rerank, "threshold") and cfg.rerank.threshold > 0:
            rag_config.rerank_threshold = cfg.rerank.threshold

    return rag_config


def get_chroma_store(workspace: Path | None = None, cfg: Any | None = None) -> ChromaKnowledgeStore:
    """
    Get ChromaKnowledgeStore singleton by workspace.

    The store is initialized only once for each resolved workspace path.
    """
    if workspace is not None:
        ws = workspace.expanduser().resolve()
        cache_key = str(ws)
        cached = _STORE_CACHE.get(cache_key)
        if cached is not None:
            return cached
    else:
        ws = None

    with _STORE_LOCK:
        if ws is None:
            cfg_obj = cfg or load_config()
            ws = Path(cfg_obj.workspace_path).expanduser().resolve()
            cache_key = str(ws)
            cached = _STORE_CACHE.get(cache_key)
            if cached is not None:
                return cached
        else:
            cfg_obj = cfg or load_config()
            cache_key = str(ws)
            cached = _STORE_CACHE.get(cache_key)
            if cached is not None:
                return cached

        rag_config = build_rag_config(cfg_obj)
        store = ChromaKnowledgeStore(ws, rag_config)
        _STORE_CACHE[cache_key] = store
        logger.info(f"[KNOWLEDGE] ♻️ ChromaKnowledgeStore initialized once for workspace: {ws}")
        return store
