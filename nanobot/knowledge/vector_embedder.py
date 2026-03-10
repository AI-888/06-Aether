"""Text vectorization using local embedding models."""
from typing import List

from loguru import logger
from sentence_transformers import SentenceTransformer


class EmbeddingModelError(Exception):
    """Embedding model error."""

    def __init__(self, model_name: str, message: str):
        super().__init__(
            f"Embedding 模型 '{model_name}' 加载失败: {message}\n"
            f"请检查:\n"
            f"1. 模型文件是否存在\n"
            f"2. 模型路径配置是否正确\n"
            f"3. 系统内存是否充足"
        )


class VectorEmbedder:
    """文本向量化器，使用本地 Embedding 模型."""

    def __init__(self, model_name: str):
        """初始化向量化器.
        
        Args:
            model_name: sentence-transformers 模型名称
            
        Raises:
            EmbeddingModelError: 模型加载失败时抛出
        """
        self.model_name = model_name
        self.model = None
        self._load_model()

    def _load_model(self) -> None:
        """加载 Embedding 模型.
        
        Raises:
            EmbeddingModelError: 模型加载失败时抛出
        """
        try:
            logger.info(f"正在加载 Embedding 模型: {self.model_name}")
            self.model = SentenceTransformer(self.model_name)
            logger.info(f"Embedding 模型加载成功: {self.model_name}")
        except Exception as e:
            logger.error(f"Embedding 模型加载失败: {self.model_name}, 错误: {str(e)}")
            raise EmbeddingModelError(self.model_name, str(e))

    def embed_text(self, text: str) -> List[float]:
        """向量化单个文本.
        
        Args:
            text: 输入文本
            
        Returns:
            文本向量
            
        Raises:
            EmbeddingModelError: 向量化失败时抛出
        """
        if not text or not text.strip():
            logger.warning("尝试向量化空文本，返回零向量")
            # Return zero vector with correct dimensions
            return [0.0] * self.model.get_sentence_embedding_dimension()

        try:
            embedding = self.model.encode(text, convert_to_numpy=True)
            return embedding.tolist()
        except Exception as e:
            logger.error(f"文本向量化失败: {str(e)}")
            raise EmbeddingModelError(self.model_name, f"向量化失败: {str(e)}")

    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        """批量向量化文本.
        
        Args:
            texts: 文本列表
            
        Returns:
            向量列表
            
        Raises:
            EmbeddingModelError: 向量化失败时抛出
        """
        if not texts:
            logger.warning("尝试向量化空文本列表，返回空列表")
            return []

        # Filter out empty texts and keep track of indices
        non_empty_texts = []
        non_empty_indices = []
        for i, text in enumerate(texts):
            if text and text.strip():
                non_empty_texts.append(text)
                non_empty_indices.append(i)

        if not non_empty_texts:
            logger.warning("所有文本都为空，返回零向量列表")
            dim = self.model.get_sentence_embedding_dimension()
            return [[0.0] * dim for _ in texts]

        try:
            # Encode non-empty texts
            embeddings = self.model.encode(non_empty_texts, convert_to_numpy=True)

            # Reconstruct full list with zero vectors for empty texts
            result = []
            dim = self.model.get_sentence_embedding_dimension()
            non_empty_idx = 0

            for i in range(len(texts)):
                if i in non_empty_indices:
                    result.append(embeddings[non_empty_idx].tolist())
                    non_empty_idx += 1
                else:
                    result.append([0.0] * dim)

            return result
        except Exception as e:
            logger.error(f"批量文本向量化失败: {str(e)}")
            raise EmbeddingModelError(self.model_name, f"批量向量化失败: {str(e)}")

    def get_embedding_dimension(self) -> int:
        """获取向量维度.
        
        Returns:
            向量维度
        """
        return self.model.get_sentence_embedding_dimension()
