"""RAG system configuration."""

import os
from dataclasses import dataclass


@dataclass
class RAGConfig:
    """RAG system configuration.
    
    This class manages configuration for the RAG (Retrieval-Augmented Generation)
    knowledge base system, including embedding models, text chunking, and retrieval
    parameters.
    """

    # Embedding model configuration
    embedding_model: str = ""

    # Text chunking configuration
    chunk_size: int = 500
    chunk_overlap: int = 100

    # Retrieval configuration
    top_k: int = 5
    similarity_threshold: float = 0.0

    # Performance configuration
    batch_size: int = 32
    timeout: int = 5
    
    # Rerank configuration
    rerank_model_path: str = ""
    rerank_threshold: float = 0.8

    @classmethod
    def from_env(cls) -> "RAGConfig":
        """Load configuration from environment variables.
        
        Supported environment variables:
        - NANOBOT_EMBEDDING_MODEL: Embedding model name
        - NANOBOT_CHUNK_SIZE: Text chunk size in characters
        - NANOBOT_CHUNK_OVERLAP: Text chunk overlap size in characters
        - NANOBOT_TOP_K: Number of results to return in retrieval
        - NANOBOT_SIMILARITY_THRESHOLD: Minimum similarity score threshold
        - NANOBOT_BATCH_SIZE: Batch size for vectorization
        - NANOBOT_TIMEOUT: Timeout in seconds for operations
        - NANOBOT_RERANK_MODEL_PATH: Path to rerank model
        - NANOBOT_RERANK_THRESHOLD: Rerank threshold (0.0-1.0)
        
        Returns:
            RAGConfig instance with values from environment or defaults
        """
        config = cls()

        # Load embedding model
        if model := os.getenv("NANOBOT_EMBEDDING_MODEL"):
            config.embedding_model = model

        # Load chunking configuration
        if chunk_size := os.getenv("NANOBOT_CHUNK_SIZE"):
            try:
                config.chunk_size = int(chunk_size)
            except ValueError:
                pass  # Use default

        if chunk_overlap := os.getenv("NANOBOT_CHUNK_OVERLAP"):
            try:
                config.chunk_overlap = int(chunk_overlap)
            except ValueError:
                pass  # Use default

        # Load retrieval configuration
        if top_k := os.getenv("NANOBOT_TOP_K"):
            try:
                config.top_k = int(top_k)
            except ValueError:
                pass  # Use default

        if threshold := os.getenv("NANOBOT_SIMILARITY_THRESHOLD"):
            try:
                config.similarity_threshold = float(threshold)
            except ValueError:
                pass  # Use default

        # Load performance configuration
        if batch_size := os.getenv("NANOBOT_BATCH_SIZE"):
            try:
                config.batch_size = int(batch_size)
            except ValueError:
                pass  # Use default

        if timeout := os.getenv("NANOBOT_TIMEOUT"):
            try:
                config.timeout = int(timeout)
            except ValueError:
                pass  # Use default

        # Load rerank configuration
        if rerank_model_path := os.getenv("NANOBOT_RERANK_MODEL_PATH"):
            config.rerank_model_path = rerank_model_path

        if rerank_threshold := os.getenv("NANOBOT_RERANK_THRESHOLD"):
            try:
                config.rerank_threshold = float(rerank_threshold)
            except ValueError:
                pass  # Use default

        return config

    def validate(self) -> bool:
        """Validate configuration parameters.
        
        Returns:
            True if configuration is valid, False otherwise
        """
        # Validate chunk size
        if self.chunk_size <= 0:
            return False

        # Validate chunk overlap
        if self.chunk_overlap < 0 or self.chunk_overlap >= self.chunk_size:
            return False

        # Validate top_k
        if self.top_k <= 0:
            return False

        # Validate similarity threshold
        if self.similarity_threshold < 0.0 or self.similarity_threshold > 1.0:
            return False

        # Validate batch size
        if self.batch_size <= 0:
            return False

        # Validate timeout
        if self.timeout <= 0:
            return False

        # Validate rerank threshold
        if self.rerank_threshold < 0.0 or self.rerank_threshold > 1.0:
            return False

        return True
