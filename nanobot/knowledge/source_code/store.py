"""源代码 RAG 向量存储。

使用独立的 ChromaDB 实例和 CodeBERT 模型，与现有知识库完全隔离。
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Optional

from loguru import logger


# ChromaDB 集合名称前缀
_COLLECTION_PREFIX = "source_code_"

# 源代码向量数据库子目录
_SOURCE_CODE_DB_DIR = "source_code_db"


class SourceCodeRAGStore:
    """源代码 RAG 向量存储。

    - 独立 ChromaDB 客户端（数据库目录 ``workspace/knowledge/source_code_db/``）
    - 独立 CodeBERT 模型加载
    - 集合按领域命名：``source_code_{domain}``
    - 模型加载失败时优雅降级，不影响现有知识库
    """

    def __init__(
        self,
        knowledge_dir: Path,
        model_name: str = "microsoft/codebert-base",
    ):
        """初始化源代码 RAG Store。

        Args:
            knowledge_dir: 知识库根目录，如 ``workspace/knowledge/``
            model_name: CodeBERT 模型名称或本地路径
        """
        self._knowledge_dir = knowledge_dir
        self._db_dir = knowledge_dir / _SOURCE_CODE_DB_DIR
        self._model_name = model_name

        self._chroma_client = None
        self._model = None
        self._tokenizer = None
        self._embedding_dim: int = 768  # CodeBERT 默认维度

        self._init_chroma()
        self._init_model()

    # ------------------------------------------------------------------
    # 初始化
    # ------------------------------------------------------------------

    def _init_chroma(self) -> None:
        """初始化独立的 ChromaDB 客户端。"""
        try:
            import chromadb
            from chromadb.config import Settings

            self._db_dir.mkdir(parents=True, exist_ok=True)
            self._chroma_client = chromadb.Client(Settings(
                chroma_db_impl="duckdb+parquet",
                persist_directory=str(self._db_dir),
                anonymized_telemetry=False,
            ))
            logger.info(f"[SourceCodeRAG] ChromaDB 初始化成功: {self._db_dir}")
        except Exception as exc:
            logger.error(f"[SourceCodeRAG] ChromaDB 初始化失败: {exc}")
            # 尝试使用简单内存模式作为回退
            try:
                import chromadb
                self._chroma_client = chromadb.Client()
                logger.warning("[SourceCodeRAG] 回退到内存模式 ChromaDB")
            except Exception as exc2:
                logger.error(f"[SourceCodeRAG] ChromaDB 完全不可用: {exc2}")
                self._chroma_client = None

    def _init_model(self) -> None:
        """初始化 CodeBERT 模型（独立于现有 SentenceTransformer）。"""
        try:
            from transformers import AutoModel, AutoTokenizer
            import torch

            logger.info(f"[SourceCodeRAG] 正在加载 CodeBERT 模型: {self._model_name}")
            self._tokenizer = AutoTokenizer.from_pretrained(self._model_name)
            self._model = AutoModel.from_pretrained(self._model_name)
            self._model.eval()

            # 获取实际嵌入维度
            self._embedding_dim = self._model.config.hidden_size
            logger.info(f"[SourceCodeRAG] CodeBERT 模型加载成功 (dim={self._embedding_dim})")
        except Exception as exc:
            logger.error(f"[SourceCodeRAG] CodeBERT 模型加载失败: {exc}")
            logger.warning("[SourceCodeRAG] 源代码 RAG 将不可用，但不影响现有知识库")
            self._model = None
            self._tokenizer = None

    # ------------------------------------------------------------------
    # 集合管理
    # ------------------------------------------------------------------

    def _get_collection_name(self, domain: str) -> str:
        """生成集合名称。"""
        return f"{_COLLECTION_PREFIX}{domain}"

    def get_or_create_collection(self, domain: str):
        """获取或创建领域对应的 ChromaDB 集合。"""
        if not self._chroma_client:
            raise RuntimeError("ChromaDB 客户端未初始化")

        collection_name = self._get_collection_name(domain)
        return self._chroma_client.get_or_create_collection(
            name=collection_name,
            metadata={"domain": domain, "type": "source_code"},
        )

    def delete_collection(self, domain: str) -> bool:
        """删除指定领域的集合。"""
        if not self._chroma_client:
            return False
        try:
            collection_name = self._get_collection_name(domain)
            self._chroma_client.delete_collection(name=collection_name)
            logger.info(f"[SourceCodeRAG] 已删除集合: {collection_name}")
            return True
        except Exception as exc:
            logger.error(f"[SourceCodeRAG] 删除集合失败: {exc}")
            return False

    def collection_exists(self, domain: str) -> bool:
        """检查领域集合是否存在。"""
        if not self._chroma_client:
            return False
        try:
            collection_name = self._get_collection_name(domain)
            collections = self._chroma_client.list_collections()
            return any(c.name == collection_name for c in collections)
        except Exception:
            return False

    def list_collections(self) -> list[str]:
        """列出所有源代码集合对应的领域名。"""
        if not self._chroma_client:
            return []
        try:
            collections = self._chroma_client.list_collections()
            domains = []
            for c in collections:
                if c.name.startswith(_COLLECTION_PREFIX):
                    domains.append(c.name[len(_COLLECTION_PREFIX):])
            return domains
        except Exception:
            return []

    # ------------------------------------------------------------------
    # 向量化
    # ------------------------------------------------------------------

    def embed_text(self, text: str) -> list[float]:
        """使用 CodeBERT 将文本编码为向量。"""
        if not self._model or not self._tokenizer:
            raise RuntimeError("CodeBERT 模型未加载")

        import torch

        inputs = self._tokenizer(
            text,
            return_tensors="pt",
            truncation=True,
            max_length=512,
            padding=True,
        )
        with torch.no_grad():
            outputs = self._model(**inputs)
        # 使用 [CLS] token 的输出作为文本表示
        cls_embedding = outputs.last_hidden_state[:, 0, :].squeeze()
        return cls_embedding.tolist()

    def embed_batch(self, texts: list[str], batch_size: int = 32) -> list[list[float]]:
        """批量将文本编码为向量。"""
        if not self._model or not self._tokenizer:
            raise RuntimeError("CodeBERT 模型未加载")

        import torch

        all_embeddings = []
        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]
            inputs = self._tokenizer(
                batch,
                return_tensors="pt",
                truncation=True,
                max_length=512,
                padding=True,
            )
            with torch.no_grad():
                outputs = self._model(**inputs)
            cls_embeddings = outputs.last_hidden_state[:, 0, :]
            all_embeddings.extend(cls_embeddings.tolist())
        return all_embeddings

    # ------------------------------------------------------------------
    # 存储 & 检索
    # ------------------------------------------------------------------

    def add_chunks(
        self,
        domain: str,
        chunks: list[dict[str, Any]],
    ) -> int:
        """将代码分块向量化后存入 ChromaDB。

        Args:
            domain: 领域名
            chunks: 分块列表，每个元素为 dict，包含 ``content`` 和元数据字段

        Returns:
            成功存入的分块数量
        """
        if not self._chroma_client or not self._model:
            logger.error("[SourceCodeRAG] Store 未就绪，无法添加分块")
            return 0

        collection = self.get_or_create_collection(domain)

        # 提取文本内容
        texts = [c["content"] for c in chunks]
        if not texts:
            return 0

        # 批量向量化
        embeddings = self.embed_batch(texts)

        # 准备 ChromaDB 数据
        ids = []
        documents = []
        metadatas = []
        for idx, chunk in enumerate(chunks):
            # 生成唯一 ID
            content_hash = hashlib.md5(chunk["content"].encode()).hexdigest()[:12]
            doc_id = f"{domain}_{chunk.get('file_path', 'unknown')}_{idx}_{content_hash}"
            ids.append(doc_id)
            documents.append(chunk["content"])
            metadata = {
                "domain": domain,
                "file_path": chunk.get("file_path", ""),
                "filename": chunk.get("filename", ""),
                "language": chunk.get("language", ""),
                "chunk_index": chunk.get("chunk_index", idx),
                "total_chunks": chunk.get("total_chunks", len(chunks)),
                "node_type": chunk.get("node_type", "unknown"),
            }
            metadatas.append(metadata)

        # 存入 ChromaDB
        try:
            collection.add(
                ids=ids,
                embeddings=embeddings,
                documents=documents,
                metadatas=metadatas,
            )
            logger.info(f"[SourceCodeRAG] 领域 '{domain}' 成功存入 {len(ids)} 个分块")
            return len(ids)
        except Exception as exc:
            logger.error(f"[SourceCodeRAG] 存入 ChromaDB 失败: {exc}")
            return 0

    def search(
        self,
        query: str,
        domain: Optional[str] = None,
        top_k: int = 3,
    ) -> list[dict[str, Any]]:
        """语义检索源代码分块。

        Args:
            query: 查询文本
            domain: 领域名（可选，为 None 时搜索全部领域）
            top_k: 返回的最大结果数

        Returns:
            检索结果列表，每项包含 content、metadata、score
        """
        if not self._chroma_client or not self._model:
            logger.warning("[SourceCodeRAG] Store 未就绪，返回空结果")
            return []

        query_embedding = self.embed_text(query)

        domains_to_search = [domain] if domain else self.list_collections()
        if not domains_to_search:
            return []

        all_results: list[dict[str, Any]] = []

        for d in domains_to_search:
            try:
                if not self.collection_exists(d):
                    continue
                collection = self.get_or_create_collection(d)
                results = collection.query(
                    query_embeddings=[query_embedding],
                    n_results=min(top_k, collection.count()) if collection.count() > 0 else top_k,
                    include=["documents", "metadatas", "distances"],
                )

                if results and results["documents"] and results["documents"][0]:
                    docs = results["documents"][0]
                    metas = results["metadatas"][0] if results["metadatas"] else [{}] * len(docs)
                    dists = results["distances"][0] if results["distances"] else [0.0] * len(docs)

                    for doc, meta, dist in zip(docs, metas, dists):
                        all_results.append({
                            "content": doc,
                            "metadata": meta,
                            "score": 1.0 - dist,  # ChromaDB 距离转相似度
                            "domain": d,
                        })
            except Exception as exc:
                logger.error(f"[SourceCodeRAG] 检索领域 '{d}' 失败: {exc}")

        # 按相似度排序并截取 top_k
        all_results.sort(key=lambda x: x["score"], reverse=True)
        return all_results[:top_k]

    # ------------------------------------------------------------------
    # 辅助方法
    # ------------------------------------------------------------------

    @property
    def is_ready(self) -> bool:
        """检查 Store 是否可用。"""
        return self._chroma_client is not None and self._model is not None

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def embedding_dim(self) -> int:
        return self._embedding_dim

    def get_domain_stats(self, domain: str) -> dict[str, Any]:
        """获取指定领域的统计信息。"""
        if not self._chroma_client:
            return {"domain": domain, "chunk_count": 0, "exists": False}

        try:
            if not self.collection_exists(domain):
                return {"domain": domain, "chunk_count": 0, "exists": False}

            collection = self.get_or_create_collection(domain)
            return {
                "domain": domain,
                "chunk_count": collection.count(),
                "exists": True,
            }
        except Exception:
            return {"domain": domain, "chunk_count": 0, "exists": False}

    def persist(self) -> None:
        """持久化 ChromaDB 数据。"""
        if self._chroma_client:
            try:
                self._chroma_client.persist()
                logger.debug("[SourceCodeRAG] ChromaDB 数据已持久化")
            except Exception as exc:
                logger.error(f"[SourceCodeRAG] ChromaDB 持久化失败: {exc}")
