"""Knowledge base storage system for domain-specific knowledge."""

import json
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any

import chromadb
from chromadb.config import Settings
from loguru import logger

# 添加CrossEncoder相关导入
try:
    from sentence_transformers import CrossEncoder
    import torch

    CROSS_ENCODER_AVAILABLE = True
except ImportError as e:
    CROSS_ENCODER_AVAILABLE = False
    logger.warning(f"sentence_transformers 库未安装，CrossEncoder 重排序功能将不可用: {e}")

from nanobot.utils.helpers import ensure_dir
from nanobot.metrics import (
    RAG_QUERY_DURATION,
    RAG_EMBEDDING_DURATION,
    RAG_QUERY_RESULTS_COUNT,
    RAG_QUERY_TOTAL,
    RERANK_DURATION,
)
from .rag_config import RAGConfig
from .text_chunker import TextChunker
from .vector_embedder import VectorEmbedder


class RAGKnowledgeError(Exception):
    """RAG 知识库系统基础异常."""
    pass


class ChromaConnectionError(RAGKnowledgeError):
    """Chroma 连接错误."""

    def __init__(self, message: str):
        super().__init__(
            f"Chroma 数据库连接失败: {message}\n"
            f"请检查:\n"
            f"1. Chroma 服务是否正常运行\n"
            f"2. 数据库路径是否有读写权限\n"
            f"3. 磁盘空间是否充足"
        )


@dataclass
class KnowledgeItem:
    """Knowledge item data structure."""
    id: str
    domain: str  # e.g., "rocketmq", "kubernetes", "github"
    category: str  # e.g., "troubleshooting", "configuration", "best_practices"
    title: str
    content: str
    tags: List[str]
    created_at: str
    updated_at: str
    source: str = "user"  # "user" or "system"
    priority: int = 1  # 1-5, higher is more important

    # 新增文档预览相关字段
    source_url: str = ""  # 原文档链接
    file_path: str = ""  # 本地文件路径
    full_content: str = ""  # 完整文档内容
    preview_available: bool = False  # 是否可预览

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "KnowledgeItem":
        """Create from dictionary."""
        return cls(**data)


class ChromaKnowledgeStore:
    """基于 Chroma 的知识库存储系统."""

    def __init__(self, workspace: Path, config: Optional[RAGConfig] = None):
        """初始化知识库.

        Args:
            workspace: 工作空间路径
            config: RAG 配置

        Raises:
            ChromaConnectionError: Chroma 数据库连接失败时抛出
            EmbeddingModelError: Embedding 模型加载失败时抛出
            RuntimeError: CrossEncoder 模型初始化失败时抛出
        """
        import time
        start_time = time.time()

        self.workspace = workspace
        self.config = config or RAGConfig()
        self.knowledge_dir = ensure_dir(workspace / "knowledge")
        self.chroma_dir = ensure_dir(self.knowledge_dir / "chroma_db")
        self.init_status_file = self.knowledge_dir / "init_status.json"

        logger.info("🏗️  开始初始化 RAG 知识库 Chroma")
        logger.info(f"   - 工作空间: {workspace}")
        logger.info(f"   - 知识库目录: {self.knowledge_dir}")
        logger.info(f"   - Chroma 数据库: {self.chroma_dir}")

        # 初始化组件
        logger.info("🔧 初始化 RAG 知识库组件...")
        logger.info(f"   - 向量化模型: {self.config.embedding_model}")
        logger.info(f"   - 分块大小: {self.config.chunk_size}")
        logger.info(f"   - 分块重叠: {self.config.chunk_overlap}")

        self.embedder = VectorEmbedder(self.config.embedding_model)
        self.chunker = TextChunker(
            chunk_size=self.config.chunk_size,
            chunk_overlap=self.config.chunk_overlap,
            smart_chunking=False,  # 启用智能分割
            preserve_structure=False  # 保持文档结构
        )
        self.chroma_client = None
        self._init_chroma()
        self._init_status: Dict[str, Any] = {}
        self._load_init_status()

        # 初始化CrossEncoder重排序模型
        self.cross_encoder = None
        self._init_cross_encoder()

        elapsed = time.time() - start_time
        logger.info(f"✅ RAG 知识库Chroma初始化完成，总耗时: {elapsed:.2f} 秒")
        logger.info("📚 内置知识库将在首次使用时自动初始化")

    def _init_chroma(self) -> None:
        """初始化 Chroma 客户端.

        Raises:
            ChromaConnectionError: Chroma 数据库连接失败时抛出
        """
        try:
            logger.info(f"初始化 Chroma 持久化客户端: {self.chroma_dir}")
            self.chroma_client = chromadb.PersistentClient(
                path=str(self.chroma_dir),
                settings=Settings(
                    anonymized_telemetry=False,
                    allow_reset=True
                )
            )
            logger.info("Chroma 客户端初始化成功")
        except Exception as e:
            logger.error(f"Chroma 客户端初始化失败: {str(e)}", exc_info=True)
            raise ChromaConnectionError(str(e))

    def _get_or_create_collection(self, domain: str):
        """获取或创建 Chroma 集合.

        Args:
            domain: 领域名称

        Returns:
            Chroma 集合对象

        Raises:
            ChromaConnectionError: 集合创建失败时抛出
        """
        collection_name = f"knowledge_{domain}"

        try:
            # 尝试获取现有集合
            logger.debug(f"🔍 尝试获取现有集合: {collection_name}")
            collection = self.chroma_client.get_collection(name=collection_name)

            # 获取集合统计信息
            collection_count = collection.count()
            logger.info(f"✅ 获取现有集合成功: {collection_name}")
            logger.info(f"   - 集合数量: {collection_count}")
            logger.info(f"   - 创建时间: {collection.metadata.get('created_at', '未知')}")

            return collection
        except Exception:
            # 集合不存在，创建新集合
            try:
                logger.info(f"🏗️  创建新集合: {collection_name}")
                collection = self.chroma_client.create_collection(
                    name=collection_name,
                    metadata={
                        "domain": domain,
                        "created_at": datetime.now().isoformat(),
                        "description": f"{domain} 知识库集合"
                    }
                )
                logger.info(f"✅ 集合创建成功: {collection_name}")
                logger.info(f"   - 领域: {domain}")
                logger.info(f"   - 创建时间: {collection.metadata.get('created_at', '未知')}")
                return collection
            except Exception as e:
                logger.error(f"❌ 集合创建失败: {collection_name}, 错误: {str(e)}", exc_info=True)
                raise ChromaConnectionError(f"创建集合失败: {str(e)}")

    def _load_init_status(self) -> None:
        """加载初始化状态文件."""
        if self.init_status_file.exists():
            try:
                with open(self.init_status_file, 'r', encoding='utf-8') as f:
                    self._init_status = json.load(f)
                logger.info(
                    f"✅ 初始化状态文件加载成功: {self.init_status_file}. 内容: {json.dumps(self._init_status, ensure_ascii=False, indent=2)}")
                logger.debug(f"   - 文件内容: {json.dumps(self._init_status, ensure_ascii=False)}")
            except (json.JSONDecodeError, KeyError) as e:
                logger.warning(f"⚠️ 初始化状态文件加载失败: {str(e)}")
                self._init_status = {}
        else:
            logger.info(f"📝 初始化状态文件不存在: {self.init_status_file}")
            logger.info("   创建新的初始化状态")
            self._init_status = {}

    def _init_cross_encoder(self) -> None:
        """初始化CrossEncoder重排序模型."""
        if not CROSS_ENCODER_AVAILABLE:
            logger.warning("⚠️  CrossEncoder 不可用，跳过初始化")
            return

        # 从RAGConfig中获取重排序模型路径
        model_path = self.config.rerank_model_path

        # 如果没有配置模型路径，跳过初始化
        if not model_path:
            logger.error("ℹ️  未配置 CrossEncoder 模型路径，跳过重排序功能")
            raise FileNotFoundError(f"CrossEncoder 未配置模型路径 {model_path}")

        try:
            logger.info("🔧 初始化 CrossEncoder 重排序模型...")
            logger.info(f"   - 模型: 本地模型")
            logger.info(f"   - 路径: {model_path}")

            # 检查模型文件是否存在
            from pathlib import Path
            if not Path(model_path).exists():
                raise FileNotFoundError(f"CrossEncoder 模型文件不存在: {model_path}")

            # 加载本地模型
            self.cross_encoder = CrossEncoder(
                model_path,
                device="cuda" if torch.cuda.is_available() else "cpu",
                max_length=512
            )

            logger.info("✅ CrossEncoder 模型初始化成功")
            logger.info(f"   - 设备: {'CUDA' if torch.cuda.is_available() else 'CPU'}")

        except Exception as e:
            error_msg = f"❌ CrossEncoder 模型初始化失败: {str(e)}"
            logger.error(error_msg)
            logger.error(f"   - 模型路径: {model_path}")
            logger.error(f"   - 错误类型: {type(e).__name__}")

            # 抛出异常，终止服务启动
            raise RuntimeError(f"CrossEncoder 模型初始化失败，服务启动终止: {str(e)}") from e

    def _rerank_results(self, query: str, results: List[Dict]) -> List[Dict]:
        """使用CrossEncoder对搜索结果进行重排序.

        Args:
            query: 查询文本
            results: 原始搜索结果列表

        Returns:
            重排序后的结果列表，包含rerank_score字段
        """
        if not self.cross_encoder or not results:
            return results

        try:
            logger.info("🔍 开始使用 CrossEncoder 进行重排序...")
            start_time = datetime.now()

            # 准备重排序的输入对
            pairs = [(query, result['document']) for result in results]

            # 获取重排序分数
            scores = self.cross_encoder.predict(pairs)

            # 将分数直接转换为百分制（0-100）
            # CrossEncoder的输出通常在-10到10之间，我们使用sigmoid函数转换为0-100
            import math
            scaled_scores = [1 / (1 + math.exp(-score)) * 100 for score in scores]

            # 从RAGConfig中获取重排序阈值
            rerank_threshold = getattr(self.config, 'rerank_threshold', 60.0)  # 默认阈值60分

            # 过滤并更新结果列表
            filtered_results = []
            for i, score in enumerate(scaled_scores):
                original_score = results[i].get('similarity_score', 0)

                # 只保留超过阈值的结果
                if score >= rerank_threshold:
                    results[i]['rerank_score'] = score
                    results[i]['original_score'] = original_score
                    filtered_results.append(results[i])

            # 按重排序分数降序排序
            filtered_results.sort(key=lambda x: x['rerank_score'], reverse=True)

            # 更新results为过滤后的结果
            results = filtered_results

            elapsed = (datetime.now() - start_time).total_seconds()
            logger.info(f"✅ 重排序完成，耗时: {elapsed:.3f}秒")

            # Prometheus: 记录重排序耗时
            RERANK_DURATION.labels(status="success").observe(elapsed)
            logger.info(
                f"   - 原始结果数: {len([r for r in results if 'rerank_score' in r]) + len([r for r in results if 'rerank_score' not in r])}")
            logger.info(f"   - 过滤后结果数: {len(results)} (阈值: {rerank_threshold}分)")

            # 记录前3个结果的得分
            for i, result in enumerate(results[:3], 1):
                logger.info(
                    f"   {i}. {result['metadata'].get('title', '')[:50]} (重排序得分: {result['rerank_score']:.2f}, 原始得分: {result['original_score']:.4f})")

            return results
        except Exception as e:
            logger.error(f"❌ 重排序失败: {str(e)}")
            # Prometheus: 记录重排序失败
            RERANK_DURATION.labels(status="error").observe(0)
            return results

    def _save_init_status(self) -> None:
        """保存初始化状态到文件."""
        try:
            # 确保目录存在
            self.init_status_file.parent.mkdir(parents=True, exist_ok=True)

            with open(self.init_status_file, 'w', encoding='utf-8') as f:
                json.dump(self._init_status, f, indent=2, ensure_ascii=False)

            logger.info(
                f"✅ 初始化状态已保存: {self.init_status_file}, 内容：{json.dumps(self._init_status, ensure_ascii=False, indent=2)}")

        except Exception as e:
            logger.error(f"❌ 保存初始化状态失败: {str(e)}", exc_info=True)
            logger.error(f"   文件路径: {self.init_status_file}")
            logger.error(f"   状态内容: {json.dumps(self._init_status, ensure_ascii=False)}")

    def _should_reinitialize(self, domain: str) -> bool:
        """判断是否需要重新初始化.

        Args:
            domain: 领域名称

        Returns:
            是否需要重新初始化
        """
        # 首先检查初始化状态文件是否存在
        if not self.init_status_file.exists():
            logger.info(f"🔍 检查领域 '{domain}' 的初始化状态:")
            logger.info(f"   - 初始化状态文件不存在: {self.init_status_file}")
            logger.info(f"✅ 决策: 领域 '{domain}' 需要初始化（文件不存在）")
            return True

        # 文件存在，检查该领域的初始化状态
        status = self._init_status.get(domain, {})

        logger.info(f"🔍 检查领域 '{domain}' 的初始化状态:")
        logger.info(f"   - 初始化状态文件: {self.init_status_file}")
        logger.info(f"   - 当前状态: {status.get('initialized_at', '未初始化')}")

        # 如果从未初始化，需要初始化
        if not status.get("initialized_at"):
            logger.info(f"✅ 决策: 领域 '{domain}' 从未初始化，需要初始化")
            return True

        # 已经初始化过，跳过初始化
        logger.info(f"✅ 决策: 领域 '{domain}' 已初始化，跳过初始化")
        return False
        return False

    def _auto_initialize_builtin_knowledge(self) -> None:
        """自动初始化内置知识."""
        import time
        start_time = time.time()

        logger.info("🚀 开始自动初始化内置知识库")
        logger.info("📊 检查内置知识模块可用性...")

        # 初始化 RocketMQ 知识
        self._initialize_rocketmq_knowledge()

        elapsed = time.time() - start_time

        # 统计初始化结果
        rocketmq_status = self._init_status.get("rocketmq", {})
        rocketmq_items = rocketmq_status.get("item_count", 0)
        rocketmq_chunks = rocketmq_status.get("chunk_count", 0)

        logger.info("✅ 内置知识库初始化完成:")
        logger.info(f"   - RocketMQ 知识条目: {rocketmq_items}")
        logger.info(f"   - RocketMQ 向量化分块: {rocketmq_chunks}")
        logger.info(f"   - 总耗时: {elapsed:.2f} 秒")

        if rocketmq_items == 0:
            logger.warning("⚠️  RocketMQ 知识库为空，可能需要检查知识文件路径")
        else:
            logger.info("🎉 内置知识库已准备就绪，可以开始使用")

    def _initialize_rocketmq_knowledge(self) -> None:
        """初始化 RocketMQ 知识，支持版本控制和向量化."""
        try:
            from .rocketmq_init import RocketMQKnowledgeInitializer, ROCKETMQ_KNOWLEDGE_VERSION

            logger.info(f"🔍 检查 RocketMQ 知识库状态...")
            logger.info(f"   - 初始化状态文件: {self.init_status_file}")
            logger.info(f"   - 文件存在: {self.init_status_file.exists()}")
            logger.info(f"   - 当前版本: {ROCKETMQ_KNOWLEDGE_VERSION}")

            # 检查是否需要重新初始化
            needs_reinit = self._should_reinitialize("rocketmq")

            if needs_reinit:
                logger.info(f"🔄 需要重新初始化 RocketMQ 知识库")

                import time
                start_time = time.time()

                logger.info(f"🚀 开始初始化 RocketMQ 知识库")

                # 如果需要重新初始化，先清空现有集合
                try:
                    self.chroma_client.delete_collection(f"knowledge_rocketmq")
                    logger.info("🗑️  已删除旧的 RocketMQ 集合")
                except Exception:
                    logger.info("ℹ️  RocketMQ 集合不存在，无需删除")

                # 初始化 RocketMQ 知识
                logger.info("📚 正在加载 RocketMQ 知识内容...")
                initializer = RocketMQKnowledgeInitializer(self)
                item_count, chunk_count = initializer.initialize()

                elapsed = time.time() - start_time

                # 更新初始化状态
                self._init_status["rocketmq"] = {
                    "initialized_at": datetime.now().isoformat(),
                    "item_count": item_count,
                    "chunk_count": chunk_count,
                    "last_check": datetime.now().isoformat(),
                    "elapsed_seconds": round(elapsed, 2)
                }
                self._save_init_status()

                logger.info("✅ RocketMQ 知识库初始化完成:")
                logger.info(f"   - 知识条目数: {item_count}")
                logger.info(f"   - 向量化分块数: {chunk_count}")
                logger.info(f"   - 耗时: {elapsed:.2f} 秒")
                logger.info(f"   - 状态文件: {self.init_status_file}")

                print(
                    f"✅ 初始化 {item_count} 个 RocketMQ 知识条目，"
                    f"{chunk_count} 个文本块，"
                    f"耗时 {elapsed:.2f} 秒"
                )
            else:
                # 已经初始化，只更新检查时间
                self._init_status["rocketmq"]["last_check"] = datetime.now().isoformat()
                self._save_init_status()

                status = self._init_status.get("rocketmq", {})
                item_count = status.get("item_count", 0)
                chunk_count = status.get("chunk_count", 0)

                logger.info(f"✅ RocketMQ 知识库已初始化")
                logger.info(f"   - 现有知识条目数: {item_count}")
                logger.info(f"   - 现有向量化分块数: {chunk_count}")
                logger.info(f"   - 状态文件: {self.init_status_file}")

        except ImportError:
            logger.warning("⚠️  RocketMQ 知识模块不可用，跳过初始化")
        except Exception as e:
            logger.error(f"❌ 初始化 RocketMQ 知识失败: {str(e)}", exc_info=True)
            print(f"⚠️ 初始化 RocketMQ 知识失败: {e}")

    def add_knowledge(
            self,
            domain: str,
            category: str,
            title: str,
            content: str,
            tags: List[str] = None,
            source: str = "user",
            priority: int = 1,
            source_url: str = "",
            file_path: str = "",
            preview_available: bool = True
    ) -> str:
        """添加知识条目.

        Args:
            domain: 领域
            category: 分类
            title: 标题
            content: 内容
            tags: 标签列表
            source: 来源
            priority: 优先级
            source_url: 原文档链接
            file_path: 本地文件路径
            preview_available: 是否可预览

        Returns:
            知识条目 ID
        """
        # 1. 创建 KnowledgeItem
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S%f")
        item_id = f"{domain}_{timestamp}"

        if tags is None:
            tags = []

        # 准备元数据
        metadata = {
            "item_id": item_id,
            "domain": domain,
            "category": category,
            "title": title,
            "tags": tags,
            "source": source,
            "priority": priority,
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat(),
            # 文档预览相关字段
            "source_url": source_url,
            "file_path": file_path,
            "preview_available": preview_available
        }

        try:
            # 2. 文本分块
            chunks = self.chunker.chunk_text(content, metadata)

            if not chunks:
                logger.warning(f"知识条目 {item_id} 分块后为空，跳过")
                return item_id

            # 3. 批量向量化
            chunk_texts = [chunk["text"] for chunk in chunks]
            try:
                embeddings = self.embedder.embed_batch(chunk_texts)
            except Exception as e:
                logger.error(f"知识条目 {item_id} 向量化失败: {str(e)}")
                raise

            # 4. 存储到 Chroma 集合
            collection = self._get_or_create_collection(domain)

            # 准备批量插入的数据
            ids = []
            documents = []
            metadatas = []
            embeddings_list = []

            for i, (chunk, embedding) in enumerate(zip(chunks, embeddings)):
                chunk_id = f"{item_id}_chunk_{i}"
                ids.append(chunk_id)
                documents.append(chunk["text"])
                metadatas.append(chunk["metadata"])
                embeddings_list.append(embedding)

            # 批量插入到 Chroma
            collection.add(
                ids=ids,
                documents=documents,
                metadatas=metadatas,
                embeddings=embeddings_list
            )

            logger.info(
                f"知识条目 {item_id} 已添加: {len(chunks)} 个分块"
            )

            return item_id

        except Exception as e:
            logger.error(
                f"添加知识条目 {item_id} 失败: {str(e)}",
                exc_info=True
            )
            raise

    def search_knowledge(
            self,
            query: str = None,
            domain: str = None,
            category: str = None,
            tags: List[str] = None,
            top_k: int = None,
            return_scores: bool = False
    ) -> List[KnowledgeItem]:
        """搜索知识条目.

        Args:
            query: 查询文本（用于语义检索，可选）
            domain: 领域过滤
            category: 分类过滤
            tags: 标签过滤
            top_k: 返回结果数量
            return_scores: 是否返回包含得分的结果

        Returns:
            知识条目列表，按相似度分数降序排列（语义检索）或按创建时间排序（元数据过滤）
            如果 return_scores 为 True，则返回 (知识条目列表, 得分列表) 的元组
        """

        # 使用配置的默认值或参数指定的值
        if top_k is None:
            top_k = self.config.top_k

        # 如果没有提供 query，使用基于元数据的过滤检索（需求 6.5）
        if not query:
            logger.info(
                f"[KNOWLEDGE_STORE] 🔍 执行元数据过滤检索: domain={domain}, category={category}, tags={tags}, top_k={top_k}")
            return self._search_by_metadata(domain, category, tags, top_k)

        # 有 query 参数时，使用 RAG 语义检索（需求 6.4）
        logger.info(f"[KNOWLEDGE_STORE] 🔍 开始语义检索:")
        logger.info(f"[KNOWLEDGE_STORE]   - Query: '{query}'")
        logger.info(f"[KNOWLEDGE_STORE]   - Domain: {domain}")
        logger.info(f"[KNOWLEDGE_STORE]   - Category: {category}")
        logger.info(f"[KNOWLEDGE_STORE]   - Tags: {tags}")
        logger.info(f"[KNOWLEDGE_STORE]   - Top K: {top_k}")

        try:
            # 1. 向量化查询文本
            start_time = datetime.now()
            logger.info(f"[KNOWLEDGE_STORE] 🧮 开始向量化查询文本...")
            query_vector = self.embedder.embed_text(query)
            vectorize_time = (datetime.now() - start_time).total_seconds()
            logger.info(
                f"[KNOWLEDGE_STORE] ✅ 查询向量化完成，耗时: {vectorize_time:.3f}秒，向量维度: {len(query_vector)}")

            # Prometheus: 记录向量化耗时
            RAG_EMBEDDING_DURATION.labels(operation="semantic_search").observe(vectorize_time)

            # 2. 构建元数据过滤条件
            where_filter = {}
            if category:
                where_filter["category"] = category
            if tags:
                # Chroma 支持 $in 操作符进行标签过滤
                # 但由于 tags 存储为字符串列表，我们需要检查是否有任何标签匹配
                where_filter["tags"] = {"$in": tags}

            # 3. 确定要搜索的集合
            collections_to_search = []
            if domain:
                # 只搜索指定领域
                try:
                    collection = self._get_or_create_collection(domain)
                    collections_to_search.append((domain, collection))
                except Exception as e:
                    logger.warning(f"获取领域 '{domain}' 的集合失败: {str(e)}")
            else:
                # 搜索所有领域
                try:
                    all_collections = self.chroma_client.list_collections()
                    for coll_info in all_collections:
                        coll_name = coll_info.name
                        if coll_name.startswith("knowledge_"):
                            domain_name = coll_name.replace("knowledge_", "")
                            try:
                                collection = self.chroma_client.get_collection(coll_name)
                                collections_to_search.append((domain_name, collection))
                            except Exception as e:
                                logger.warning(f"获取集合 '{coll_name}' 失败: {str(e)}")
                except Exception as e:
                    logger.error(f"列出集合失败: {str(e)}")
                    return []

            if not collections_to_search:
                logger.warning("[KNOWLEDGE_STORE] ⚠️  没有可搜索的集合")
                return []

            logger.info(f"[KNOWLEDGE_STORE] 📚 将在 {len(collections_to_search)} 个集合中搜索")

            # 4. 在所有相关集合中执行相似度搜索
            all_results = []
            search_start = datetime.now()

            for domain_name, collection in collections_to_search:
                try:
                    # 执行 Chroma 查询
                    results = collection.query(
                        query_embeddings=[query_vector],
                        n_results=top_k,
                        where=where_filter if where_filter else None,
                        include=["documents", "metadatas", "distances"]
                    )

                    # 处理查询结果
                    if results and results["ids"] and len(results["ids"][0]) > 0:
                        for i in range(len(results["ids"][0])):
                            chunk_id = results["ids"][0][i]
                            document = results["documents"][0][i]
                            metadata = results["metadatas"][0][i]
                            distance = results["distances"][0][i]

                            # 将距离转换为相似度分数 (距离越小，相似度越高)
                            # Chroma 使用 L2 距离，我们将其转换为 0-1 的相似度分数
                            # similarity = 1 / (1 + distance)
                            similarity_score = 1.0 / (1.0 + distance)

                            # 过滤低于阈值的结果
                            if similarity_score < self.config.similarity_threshold:
                                continue

                            all_results.append({
                                "chunk_id": chunk_id,
                                "document": document,
                                "metadata": metadata,
                                "similarity_score": similarity_score,
                                "domain": domain_name
                            })

                except Exception as e:
                    logger.warning(f"在领域 '{domain_name}' 中搜索失败: {str(e)}")
                    continue

            search_time = (datetime.now() - search_start).total_seconds()
            logger.info(
                f"[KNOWLEDGE_STORE] 🔎 相似度搜索完成，耗时: {search_time:.3f}秒，找到 {len(all_results)} 个分块结果")

            # Prometheus: 记录向量搜索耗时和结果数
            search_domain_label = domain or "all"
            RAG_QUERY_DURATION.labels(operation="semantic_search", domain=search_domain_label, status="success").observe(search_time)
            RAG_QUERY_RESULTS_COUNT.labels(operation="semantic_search", domain=search_domain_label).observe(len(all_results))

            # 5. 按相似度分数降序排序
            all_results.sort(key=lambda x: x["similarity_score"], reverse=True)

            # 6. 限制返回结果数量
            all_results = all_results[:top_k]

            # 8. 使用CrossEncoder进行重排序
            reranked_results = self._rerank_results(query, all_results)

            # 9. 重构为 KnowledgeItem 对象
            knowledge_items = []
            seen_item_ids = set()  # 用于去重（同一知识条目的不同分块）

            for result in reranked_results:
                metadata = result["metadata"]
                item_id = metadata.get("item_id")

                # 如果已经添加过这个知识条目，跳过（避免重复）
                if item_id in seen_item_ids:
                    continue
                seen_item_ids.add(item_id)

                # 创建 KnowledgeItem
                try:
                    knowledge_item = KnowledgeItem(
                        id=item_id,
                        domain=metadata.get("domain", result["domain"]),
                        category=metadata.get("category", ""),
                        title=metadata.get("title", ""),
                        content=result["document"],  # 使用分块的内容
                        tags=metadata.get("tags", []),
                        created_at=metadata.get("created_at", ""),
                        updated_at=metadata.get("updated_at", ""),
                        source=metadata.get("source", "user"),
                        priority=metadata.get("priority", 1),
                        # 文档预览相关字段
                        source_url=metadata.get("source_url", ""),
                        file_path=metadata.get("file_path", ""),
                        preview_available=metadata.get("preview_available", False)
                    )

                    # 添加相似度分数和重排序分数（作为额外属性）
                    # 注意：KnowledgeItem 是 dataclass，我们需要动态添加属性
                    knowledge_item_dict = knowledge_item.to_dict()
                    knowledge_item_dict["similarity_score"] = result.get("similarity_score", 0)
                    knowledge_item_dict["rerank_score"] = result.get("rerank_score", 0)
                    knowledge_item_dict["chunk_index"] = metadata.get("chunk_index", 0)

                    # 重新创建带有额外字段的对象
                    # 由于 KnowledgeItem 不支持额外字段，我们直接返回原对象
                    # 并在日志中记录分数
                    knowledge_items.append(knowledge_item)

                    logger.debug(
                        f"添加结果: id={item_id}, title={metadata.get('title', '')[:30]}, "
                        f"similarity={result.get('similarity_score', 0):.4f}, "
                        f"rerank_score={result.get('rerank_score', 0):.2f}"
                    )

                except Exception as e:
                    logger.warning(f"重构 KnowledgeItem 失败: {str(e)}")
                    continue

            total_time = (datetime.now() - start_time).total_seconds()
            logger.info(f"[KNOWLEDGE_STORE] ✅ 语义检索完成:")
            logger.info(f"[KNOWLEDGE_STORE]   - 返回结果数: {len(knowledge_items)}")
            logger.info(f"[KNOWLEDGE_STORE]   - 总耗时: {total_time:.3f}秒")

            # Prometheus: 记录语义检索总计数
            RAG_QUERY_TOTAL.labels(operation="semantic_search", domain=domain or "all", status="success").inc()

            # 记录前3个结果的标题和分数
            for i, item in enumerate(knowledge_items[:3], 1):
                result = reranked_results[i - 1] if i - 1 < len(reranked_results) else {}
                similarity_score = result.get("similarity_score", 0)
                rerank_score = result.get("rerank_score", 0)
                logger.info(
                    f"[KNOWLEDGE_STORE]   {i}. {item.title[:50]} (相似度: {similarity_score:.4f}, 重排序得分: {rerank_score:.2f})")

            if return_scores:
                # 构建得分列表
                scores = []
                for result in reranked_results:
                    scores.append({
                        "similarity_score": result.get("similarity_score", 0),
                        "rerank_score": result.get("rerank_score", 0)
                    })
                return knowledge_items, scores
            else:
                return knowledge_items

        except Exception as e:
            logger.error(f"语义检索失败: {str(e)}", exc_info=True)
            # Prometheus: 记录语义检索失败
            RAG_QUERY_TOTAL.labels(operation="semantic_search", domain=domain or "all", status="error").inc()
            return []

    def _search_by_metadata(
            self,
            domain: str = None,
            category: str = None,
            tags: List[str] = None,
            top_k: int = None
    ) -> List[KnowledgeItem]:
        """基于元数据的过滤检索（不使用语义搜索）.
        
        当没有提供 query 参数时使用此方法（需求 6.5）。
        
        Args:
            domain: 领域过滤
            category: 分类过滤
            tags: 标签过滤
            top_k: 返回结果数量
            
        Returns:
            知识条目列表，按创建时间降序排列
        """
        try:
            # 构建元数据过滤条件
            where_filter = {}
            if category:
                where_filter["category"] = category
            if tags:
                where_filter["tags"] = {"$in": tags}

            # 确定要搜索的集合
            collections_to_search = []
            if domain:
                # 只搜索指定领域
                try:
                    collection = self._get_or_create_collection(domain)
                    collections_to_search.append((domain, collection))
                except Exception as e:
                    logger.warning(f"获取领域 '{domain}' 的集合失败: {str(e)}")
            else:
                # 搜索所有领域
                try:
                    all_collections = self.chroma_client.list_collections()
                    for coll_info in all_collections:
                        coll_name = coll_info.name
                        if coll_name.startswith("knowledge_"):
                            domain_name = coll_name.replace("knowledge_", "")
                            try:
                                collection = self.chroma_client.get_collection(coll_name)
                                collections_to_search.append((domain_name, collection))
                            except Exception as e:
                                logger.warning(f"获取集合 '{coll_name}' 失败: {str(e)}")
                except Exception as e:
                    logger.error(f"列出集合失败: {str(e)}")
                    return []

            if not collections_to_search:
                logger.warning("没有可搜索的集合")
                return []

            # 在所有相关集合中执行元数据过滤
            all_results = []

            for domain_name, collection in collections_to_search:
                try:
                    # 使用 Chroma 的 get 方法进行元数据过滤
                    results = collection.get(
                        where=where_filter if where_filter else None,
                        limit=top_k if top_k else 1000,  # 设置一个合理的上限
                        include=["documents", "metadatas"]
                    )

                    # 处理查询结果
                    if results and results["ids"]:
                        for i in range(len(results["ids"])):
                            chunk_id = results["ids"][i]
                            document = results["documents"][i]
                            metadata = results["metadatas"][i]

                            all_results.append({
                                "chunk_id": chunk_id,
                                "document": document,
                                "metadata": metadata,
                                "domain": domain_name
                            })

                except Exception as e:
                    logger.warning(f"在领域 '{domain_name}' 中搜索失败: {str(e)}")
                    continue

            logger.debug(f"元数据过滤完成，找到 {len(all_results)} 个结果")

            # 按创建时间降序排序
            all_results.sort(
                key=lambda x: x["metadata"].get("created_at", ""),
                reverse=True
            )

            # 限制返回结果数量
            if top_k:
                all_results = all_results[:top_k]

            # 重构为 KnowledgeItem 对象
            knowledge_items = []
            seen_item_ids = set()  # 用于去重（同一知识条目的不同分块）

            for result in all_results:
                metadata = result["metadata"]
                item_id = metadata.get("item_id")

                # 如果已经添加过这个知识条目，跳过（避免重复）
                if item_id in seen_item_ids:
                    continue
                seen_item_ids.add(item_id)

                # 创建 KnowledgeItem
                try:
                    knowledge_item = KnowledgeItem(
                        id=item_id,
                        domain=metadata.get("domain", result["domain"]),
                        category=metadata.get("category", ""),
                        title=metadata.get("title", ""),
                        content=result["document"],  # 使用分块的内容
                        tags=metadata.get("tags", []),
                        created_at=metadata.get("created_at", ""),
                        updated_at=metadata.get("updated_at", ""),
                        source=metadata.get("source", "user"),
                        priority=metadata.get("priority", 1),
                        # 文档预览相关字段
                        source_url=metadata.get("source_url", ""),
                        file_path=metadata.get("file_path", ""),
                        preview_available=metadata.get("preview_available", False)
                    )

                    knowledge_items.append(knowledge_item)

                    logger.debug(
                        f"添加结果: id={item_id}, title={metadata.get('title', '')[:30]}"
                    )

                except Exception as e:
                    logger.warning(f"重构 KnowledgeItem 失败: {str(e)}")
                    continue

            logger.info(f"元数据过滤检索完成: 返回 {len(knowledge_items)} 个结果")

            return knowledge_items

        except Exception as e:
            logger.error(f"元数据过滤检索失败: {str(e)}", exc_info=True)
            return []

    def update_knowledge(self, item_id: str, **kwargs) -> bool:
        """更新知识条目.
        
        根据需求 5.2，更新知识条目时需要：
        1. 删除旧的向量数据
        2. 更新内容并重新向量化
        3. 存储新的向量数据
        
        Args:
            item_id: 知识条目 ID
            **kwargs: 要更新的字段（title, content, tags, category, priority）
            
        Returns:
            是否更新成功
        """
        logger.info(f"开始更新知识条目: {item_id}")

        try:
            # 1. 首先查找该知识条目所属的领域
            # 通过遍历所有集合查找包含该 item_id 的集合
            domain = None
            old_metadata = None

            try:
                all_collections = self.chroma_client.list_collections()
                for coll_info in all_collections:
                    coll_name = coll_info.name
                    if coll_name.startswith("knowledge_"):
                        try:
                            collection = self.chroma_client.get_collection(coll_name)
                            # 查询该集合中是否有该 item_id 的分块
                            results = collection.get(
                                where={"item_id": item_id},
                                limit=1
                            )

                            if results and results["ids"] and len(results["ids"]) > 0:
                                domain = coll_name.replace("knowledge_", "")
                                old_metadata = results["metadatas"][0]
                                logger.info(f"找到知识条目 {item_id} 在领域 {domain}")
                                break
                        except Exception as e:
                            logger.warning(f"查询集合 {coll_name} 失败: {str(e)}")
                            continue
            except Exception as e:
                logger.error(f"列出集合失败: {str(e)}")
                return False

            if not domain or not old_metadata:
                logger.warning(f"知识条目 {item_id} 不存在")
                return False

            # 2. 删除旧的向量数据
            collection = self._get_or_create_collection(domain)

            # 查找所有属于该 item_id 的分块
            old_chunks = collection.get(
                where={"item_id": item_id}
            )

            if old_chunks and old_chunks["ids"]:
                chunk_ids = old_chunks["ids"]
                collection.delete(ids=chunk_ids)
                logger.info(f"删除了 {len(chunk_ids)} 个旧的向量分块")
            else:
                logger.warning(f"未找到知识条目 {item_id} 的旧向量数据")

            # 3. 准备更新后的元数据
            # 合并旧元数据和新的更新字段
            updated_metadata = old_metadata.copy()

            # 允许更新的字段
            allowed_fields = ['title', 'content', 'tags', 'category', 'priority']
            for key, value in kwargs.items():
                if key in allowed_fields:
                    updated_metadata[key] = value

            # 更新时间戳
            updated_metadata["updated_at"] = datetime.now().isoformat()

            # 4. 获取更新后的内容（如果没有提供新内容，使用旧内容）
            # 注意：旧的 content 不在 metadata 中，需要从 documents 中获取
            if "content" in kwargs:
                new_content = kwargs["content"]
            else:
                # 如果没有提供新内容，从旧分块中重建内容
                if old_chunks and old_chunks["documents"]:
                    # 将所有分块的文本合并
                    new_content = " ".join(old_chunks["documents"])
                else:
                    logger.error(f"无法获取知识条目 {item_id} 的内容")
                    return False

            # 5. 重新分块和向量化
            # 准备用于分块的元数据（不包含 chunk_index 和 total_chunks）
            chunk_metadata = {
                "item_id": item_id,
                "domain": updated_metadata.get("domain", domain),
                "category": updated_metadata.get("category", ""),
                "title": updated_metadata.get("title", ""),
                "tags": updated_metadata.get("tags", []),
                "source": updated_metadata.get("source", "user"),
                "priority": updated_metadata.get("priority", 1),
                "created_at": updated_metadata.get("created_at", ""),
                "updated_at": updated_metadata["updated_at"]
            }

            # 文本分块
            chunks = self.chunker.chunk_text(new_content, chunk_metadata)

            if not chunks:
                logger.warning(f"知识条目 {item_id} 更新后分块为空")
                return False

            # 6. 批量向量化
            chunk_texts = [chunk["text"] for chunk in chunks]
            try:
                embeddings = self.embedder.embed_batch(chunk_texts)
            except Exception as e:
                logger.error(f"知识条目 {item_id} 重新向量化失败: {str(e)}")
                raise

            # 7. 存储新的向量数据
            # 准备批量插入的数据
            ids = []
            documents = []
            metadatas = []
            embeddings_list = []

            for i, (chunk, embedding) in enumerate(zip(chunks, embeddings)):
                chunk_id = f"{item_id}_chunk_{i}"
                ids.append(chunk_id)
                documents.append(chunk["text"])
                metadatas.append(chunk["metadata"])
                embeddings_list.append(embedding)

            # 批量插入到 Chroma
            collection.add(
                ids=ids,
                documents=documents,
                metadatas=metadatas,
                embeddings=embeddings_list
            )

            logger.info(
                f"知识条目 {item_id} 更新成功: {len(chunks)} 个新分块"
            )

            return True

        except Exception as e:
            logger.error(
                f"更新知识条目 {item_id} 失败: {str(e)}",
                exc_info=True
            )
            return False

    def delete_knowledge(self, item_id: str) -> bool:
        """删除知识条目.

        根据需求 5.3，删除知识条目时需要：
        从 Chroma 集合中删除所有相关分块

        Args:
            item_id: 知识条目 ID

        Returns:
            是否删除成功
        """
        logger.info(f"开始删除知识条目: {item_id}")

        try:
            # 1. 查找该知识条目所属的领域
            # 通过遍历所有集合查找包含该 item_id 的集合
            domain = None

            try:
                all_collections = self.chroma_client.list_collections()
                for coll_info in all_collections:
                    coll_name = coll_info.name
                    if coll_name.startswith("knowledge_"):
                        try:
                            collection = self.chroma_client.get_collection(coll_name)
                            # 查询该集合中是否有该 item_id 的分块
                            results = collection.get(
                                where={"item_id": item_id},
                                limit=1
                            )

                            if results and results["ids"] and len(results["ids"]) > 0:
                                domain = coll_name.replace("knowledge_", "")
                                logger.info(f"找到知识条目 {item_id} 在领域 {domain}")
                                break
                        except Exception as e:
                            logger.warning(f"查询集合 {coll_name} 失败: {str(e)}")
                            continue
            except Exception as e:
                logger.error(f"列出集合失败: {str(e)}")
                return False

            if not domain:
                logger.warning(f"知识条目 {item_id} 不存在")
                return False

            # 2. 删除所有相关分块
            collection = self._get_or_create_collection(domain)

            # 查找所有属于该 item_id 的分块
            chunks = collection.get(
                where={"item_id": item_id}
            )

            if chunks and chunks["ids"]:
                chunk_ids = chunks["ids"]
                collection.delete(ids=chunk_ids)
                logger.info(f"成功删除知识条目 {item_id} 的 {len(chunk_ids)} 个分块")
                return True
            else:
                logger.warning(f"未找到知识条目 {item_id} 的分块数据")
                return False

        except Exception as e:
            logger.error(
                f"删除知识条目 {item_id} 失败: {str(e)}",
                exc_info=True
            )
            return False

    def get_domains(self) -> List[str]:
        """获取所有领域列表.
        
        Returns:
            领域列表
        """
        try:
            # 获取所有集合
            collections = self.chroma_client.list_collections()

            # 从集合名称中提取领域名称
            # 集合名称格式: knowledge_{domain}
            domains = []
            for collection in collections:
                if collection.name.startswith("knowledge_"):
                    domain = collection.name[len("knowledge_"):]
                    domains.append(domain)

            return sorted(domains)
        except Exception as e:
            logger.error(f"获取领域列表失败: {str(e)}", exc_info=True)
            return []

    def get_categories(self, domain: str = None) -> List[str]:
        """获取分类列表.
        
        Args:
            domain: 领域过滤
            
        Returns:
            分类列表
        """
        try:
            categories = set()

            if domain:
                # 获取指定领域的分类
                collection = self._get_or_create_collection(domain)
                results = collection.get()

                if results and results["metadatas"]:
                    for metadata in results["metadatas"]:
                        if "category" in metadata:
                            categories.add(metadata["category"])
            else:
                # 获取所有领域的分类
                domains = self.get_domains()
                for d in domains:
                    collection = self._get_or_create_collection(d)
                    results = collection.get()

                    if results and results["metadatas"]:
                        for metadata in results["metadatas"]:
                            if "category" in metadata:
                                categories.add(metadata["category"])

            return sorted(list(categories))
        except Exception as e:
            logger.error(f"获取分类列表失败: {str(e)}", exc_info=True)
            return []

    def get_tags(self, domain: str = None) -> List[str]:
        """获取标签列表.
        
        Args:
            domain: 领域过滤
            
        Returns:
            标签列表
        """
        try:
            tags = set()

            if domain:
                # 获取指定领域的标签
                collection = self._get_or_create_collection(domain)
                results = collection.get()

                if results and results["metadatas"]:
                    for metadata in results["metadatas"]:
                        if "tags" in metadata and metadata["tags"]:
                            # tags 可能是列表
                            if isinstance(metadata["tags"], list):
                                tags.update(metadata["tags"])
                            else:
                                tags.add(metadata["tags"])
            else:
                # 获取所有领域的标签
                domains = self.get_domains()
                for d in domains:
                    collection = self._get_or_create_collection(d)
                    results = collection.get()

                    if results and results["metadatas"]:
                        for metadata in results["metadatas"]:
                            if "tags" in metadata and metadata["tags"]:
                                # tags 可能是列表
                                if isinstance(metadata["tags"], list):
                                    tags.update(metadata["tags"])
                                else:
                                    tags.add(metadata["tags"])

            return sorted(list(tags))
        except Exception as e:
            logger.error(f"获取标签列表失败: {str(e)}", exc_info=True)
            return []

    def export_knowledge(self, domain: str = None) -> Dict[str, Any]:
        """导出知识为 JSON 格式.

        Args:
            domain: 领域过滤（可选）

        Returns:
            包含导出时间和知识条目列表的字典
        """
        try:
            knowledge_items = []
            seen_item_ids = set()  # 用于去重（同一知识条目的不同分块）

            # 确定要导出的领域
            domains_to_export = [domain] if domain else self.get_domains()

            for d in domains_to_export:
                try:
                    collection = self._get_or_create_collection(d)
                    results = collection.get(
                        include=["documents", "metadatas"]
                    )

                    if results and results["ids"]:
                        # 按 item_id 分组，合并同一知识条目的所有分块
                        item_chunks = {}
                        for i in range(len(results["ids"])):
                            metadata = results["metadatas"][i]
                            document = results["documents"][i]
                            item_id = metadata.get("item_id")

                            if not item_id:
                                continue

                            if item_id not in item_chunks:
                                item_chunks[item_id] = {
                                    "metadata": metadata,
                                    "chunks": []
                                }

                            # 添加分块内容
                            chunk_index = metadata.get("chunk_index", 0)
                            item_chunks[item_id]["chunks"].append({
                                "index": chunk_index,
                                "text": document
                            })

                        # 重构完整的知识条目
                        for item_id, data in item_chunks.items():
                            if item_id in seen_item_ids:
                                continue
                            seen_item_ids.add(item_id)

                            metadata = data["metadata"]

                            # 按 chunk_index 排序并合并内容
                            sorted_chunks = sorted(data["chunks"], key=lambda x: x["index"])
                            full_content = " ".join(chunk["text"] for chunk in sorted_chunks)

                            # 创建 KnowledgeItem
                            knowledge_item = KnowledgeItem(
                                id=item_id,
                                domain=metadata.get("domain", d),
                                category=metadata.get("category", ""),
                                title=metadata.get("title", ""),
                                content=full_content,
                                tags=metadata.get("tags", []),
                                created_at=metadata.get("created_at", ""),
                                updated_at=metadata.get("updated_at", ""),
                                source=metadata.get("source", "user"),
                                priority=metadata.get("priority", 1),
                                # 文档预览相关字段
                                source_url=metadata.get("source_url", ""),
                                file_path=metadata.get("file_path", ""),
                                preview_available=metadata.get("preview_available", False)
                            )

                            knowledge_items.append(knowledge_item.to_dict())

                except Exception as e:
                    logger.warning(f"导出领域 '{d}' 的知识失败: {str(e)}")
                    continue

            logger.info(f"成功导出 {len(knowledge_items)} 个知识条目")

            return {
                "exported_at": datetime.now().isoformat(),
                "knowledge_items": knowledge_items
            }

        except Exception as e:
            logger.error(f"导出知识失败: {str(e)}", exc_info=True)
            return {
                "exported_at": datetime.now().isoformat(),
                "knowledge_items": []
            }


class DomainKnowledgeManager:
    """Specialized knowledge manager for specific domains."""

    def __init__(self, knowledge_store: "ChromaKnowledgeStore", domain: str):
        self.store = knowledge_store
        self.domain = domain

    def add_troubleshooting_guide(self, title: str, content: str, tags: List[str] = None) -> str:
        """Add a troubleshooting guide for the domain."""
        if tags is None:
            tags = ["troubleshooting"]
        else:
            tags.append("troubleshooting")

        return self.store.add_knowledge(
            domain=self.domain,
            category="troubleshooting",
            title=title,
            content=content,
            tags=tags,
            priority=3
        )

    def add_configuration_guide(self, title: str, content: str, tags: List[str] = None) -> str:
        """Add a configuration guide for the domain."""
        if tags is None:
            tags = ["configuration"]
        else:
            tags.append("configuration")

        return self.store.add_knowledge(
            domain=self.domain,
            category="configuration",
            title=title,
            content=content,
            tags=tags,
            priority=2
        )

    def add_best_practice(self, title: str, content: str, tags: List[str] = None) -> str:
        """Add a best practice for the domain."""
        if tags is None:
            tags = ["best_practices"]
        else:
            tags.append("best_practices")

        return self.store.add_knowledge(
            domain=self.domain,
            category="best_practices",
            title=title,
            content=content,
            tags=tags,
            priority=4
        )

    def add_checker_info(self, checker_name: str, description: str, usage: str,
                         admin_api: str = None, tags: List[str] = None) -> str:
        """Add checker information for the domain."""
        if tags is None:
            tags = ["checker", "diagnostic"]
        else:
            tags.extend(["checker", "diagnostic"])

        content = f"""## {checker_name}

**描述**: {description}

**使用场景**: {usage}

"""

        if admin_api:
            content += f"**Admin API**: {admin_api}\n\n"

        return self.store.add_knowledge(
            domain=self.domain,
            category="diagnostic_tools",
            title=f"检查器: {checker_name}",
            content=content,
            tags=tags,
            priority=3
        )

    def search_troubleshooting(self, query: str = None, tags: List[str] = None) -> List[KnowledgeItem]:
        """Search troubleshooting guides for the domain."""
        return self.store.search_knowledge(
            query=query,
            domain=self.domain,
            category="troubleshooting",
            tags=tags
        )

    def search_configuration(self, query: str = None, tags: List[str] = None) -> List[KnowledgeItem]:
        """Search configuration guides for the domain."""
        return self.store.search_knowledge(
            query=query,
            domain=self.domain,
            category="configuration",
            tags=tags
        )

    def search_checkers(self, query: str = None) -> List[KnowledgeItem]:
        """Search diagnostic checkers for the domain."""
        return self.store.search_knowledge(
            query=query,
            domain=self.domain,
            category="diagnostic_tools",
            tags=["checker"]
        )

    def get_all_checkers(self) -> List[KnowledgeItem]:
        """Get all diagnostic checkers for the domain."""
        return self.search_checkers()

    def get_common_issues(self) -> List[KnowledgeItem]:
        """Get common issues for the domain."""
        return self.store.search_knowledge(
            domain=self.domain,
            tags=["common", "issue"]
        )

    def export_domain_knowledge(self) -> Dict[str, Any]:
        """Export all knowledge for the domain."""
        return self.store.export_knowledge(domain=self.domain)


# 默认使用 ChromaKnowledgeStore（支持 RAG 向量检索）
KnowledgeStore = ChromaKnowledgeStore
