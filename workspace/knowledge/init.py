#!/usr/bin/env python3
"""测试知识库初始化的脚本，验证文件路径是否正确保存"""

import asyncio

from nanobot.config.loader import load_config
from nanobot.knowledge.rag_config import RAGConfig
from nanobot.knowledge.rocketmq_init import RocketMQKnowledgeInitializer
from nanobot.knowledge.store import ChromaKnowledgeStore


async def init():
    """测试知识库初始化，验证文件路径是否正确保存"""
    print("🧪 开始知识库初始化...")

    # 加载配置
    config = load_config()
    rag_config = RAGConfig.from_env()

    if hasattr(config.agents, 'defaults'):
        defaults = config.agents.defaults
        if hasattr(defaults, 'embedding_model'):
            rag_config.embedding_model = defaults.embedding_model
        if hasattr(defaults, 'chunk_size'):
            rag_config.chunk_size = defaults.chunk_size
        if hasattr(defaults, 'chunk_overlap'):
            rag_config.chunk_overlap = defaults.chunk_overlap
        if hasattr(defaults, 'top_k'):
            rag_config.top_k = defaults.top_k
        if hasattr(defaults, 'similarity_threshold'):
            rag_config.similarity_threshold = defaults.similarity_threshold
        if hasattr(defaults, 'batch_size'):
            rag_config.batch_size = defaults.batch_size
        if hasattr(defaults, 'timeout'):
            rag_config.timeout = defaults.timeout
        if hasattr(defaults, 'rerank_model_path'):
            rag_config.rerank_model_path = defaults.rerank_model_path
        if hasattr(defaults, 'rerank_threshold'):
            rag_config.rerank_threshold = defaults.rerank_threshold

    if config.rerank.model_path:
        rag_config.rerank_model_path = config.rerank.model_path
    if config.rerank.threshold > 0:
        rag_config.rerank_threshold = config.rerank.threshold

    # 创建知识库存储
    store = ChromaKnowledgeStore(config.workspace_path, rag_config)

    print("📚 初始化RocketMQ知识库...")

    # 初始化知识库
    initializer = RocketMQKnowledgeInitializer(store)
    count = initializer.initialize()

    print(f"✅ 知识库初始化完成，共处理 {count} 个条目")

    print("\n🎯 测试完成！")


if __name__ == "__main__":
    asyncio.run(init())
