#!/usr/bin/env python3
"""
RocketMQ Knowledge Base Demo

This script demonstrates how to use the knowledge base system to store and retrieve RocketMQ knowledge.
"""

import json
from pathlib import Path
from nanobot.knowledge.store import ChromaKnowledgeStore, DomainKnowledgeManager
from nanobot.knowledge.rag_config import RAGConfig
from nanobot.config.loader import load_config


def main():
    """Demo the RocketMQ knowledge base functionality."""
    
    # Initialize knowledge store with proper configuration
    workspace = Path("workspace")
    
    # 创建 RAGConfig 并从配置文件加载配置
    rag_config = RAGConfig()
    
    # 从config.json的agents.defaults中读取RAG配置
    try:
        config = load_config()
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
        # 从rerank配置中读取
        if hasattr(config, 'rerank'):
            if hasattr(config.rerank, 'model_path') and config.rerank.model_path:
                rag_config.rerank_model_path = config.rerank.model_path
            if hasattr(config.rerank, 'threshold') and config.rerank.threshold > 0:
                rag_config.rerank_threshold = config.rerank.threshold
    except Exception:
        pass  # 使用默认配置
    
    store = ChromaKnowledgeStore(workspace, rag_config)
    rocketmq_manager = DomainKnowledgeManager(store, "rocketmq")
    
    print("=== RocketMQ Knowledge Base Demo ===\n")
    
    # Add some RocketMQ troubleshooting guides
    print("1. Adding RocketMQ troubleshooting guides...")
    
    # Add message sending failure troubleshooting
    troubleshooting_id = rocketmq_manager.add_troubleshooting_guide(
        title="消息发送失败排查指南",
        content="""## 消息发送失败常见原因及排查步骤

### 1. Topic资源未创建
- **症状**: 发送消息时提示"topic not found"
- **排查步骤**:
  1. 使用mqadmin工具检查Topic是否存在: `mqadmin topicList -n localhost:9876`
  2. 如果不存在，创建Topic: `mqadmin updateTopic -n localhost:9876 -t your_topic -c DefaultCluster`
  3. 检查客户端Topic配置是否正确

### 2. 网络连接异常
- **症状**: 连接超时、网络不可达
- **排查步骤**:
  1. 检查客户端与NameServer/Broker的网络连通性
  2. 验证防火墙配置
  3. 检查DNS解析

### 3. Broker服务异常
- **症状**: Broker进程异常、服务不可用
- **排查步骤**:
  1. 检查Broker进程状态
  2. 查看Broker日志文件
  3. 检查磁盘空间和内存使用情况
""",
        tags=["发送失败", "网络异常", "Broker异常", "常见问题"]
    )
    print(f"✓ 添加消息发送失败排查指南 (ID: {troubleshooting_id})")
    
    # Add consumer group consistency troubleshooting
    consumer_group_id = rocketmq_manager.add_troubleshooting_guide(
        title="消费者组订阅一致性异常排查",
        content="""## 消费者组订阅一致性异常

### 问题描述
同一消费者组下不同客户端的订阅规则不一致，导致消息分发异常。

### 排查步骤
1. **检查订阅配置**:
   - 验证同一Group下所有消费端的订阅Topic是否一致
   - 检查订阅表达式是否相同

2. **使用Admin API检查**:
   ```bash
   mqadmin getSubConnection -g your_consumer_group
   ```

3. **查看消费者连接信息**:
   - 检查ConsumerConnection中的subscriptionTable
   - 确保所有消费者订阅关系一致

### 解决方案
1. 统一消费者组内所有客户端的订阅配置
2. 重启不一致的消费者客户端
3. 使用动态订阅时确保配置同步
""",
        tags=["消费者组", "订阅一致性", "配置异常"]
    )
    print(f"✓ 添加消费者组订阅一致性排查指南 (ID: {consumer_group_id})")
    
    # Add configuration guides
    print("\n2. Adding RocketMQ configuration guides...")
    
    config_id = rocketmq_manager.add_configuration_guide(
        title="RocketMQ Broker核心配置参数",
        content="""## Broker核心配置参数详解

### 基础配置
- `brokerClusterName`: 集群名称
- `brokerName`: Broker名称
- `brokerId`: 0表示Master，>0表示Slave

### 存储配置
- `storePathRootDir`: 存储根目录
- `mapedFileSizeCommitLog`: CommitLog文件大小，默认1GB
- `mapedFileSizeConsumeQueue`: ConsumeQueue文件大小，默认300W条

### 网络配置
- `listenPort`: Broker监听端口，默认10911
- `haListenPort`: HA监听端口，默认10912
- `sendMessageThreadPoolNums`: 发送消息线程数

### 性能调优参数
- `flushDiskType`: 刷盘方式，SYNC_FLUSH(同步)或ASYNC_FLUSH(异步)
- `flushIntervalCommitLog`: CommitLog刷盘间隔
- `flushCommitLogTimed`: 定时刷盘开关
""",
        tags=["Broker配置", "性能调优", "核心参数"]
    )
    print(f"✓ 添加Broker配置指南 (ID: {config_id})")
    
    # Add diagnostic checkers
    print("\n3. Adding RocketMQ diagnostic checkers...")
    
    checker_id = rocketmq_manager.add_checker_info(
        checker_name="TOPIC_VALIDITY",
        description="Topic有效性检查器",
        usage="验证Topic的存在和配置，用于排查消息发送失败问题",
        admin_api="getTopicInfoHistory(groupId, topic, startTime, endTime)",
        tags=["Topic检查", "发送异常", "路由验证"]
    )
    print(f"✓ 添加Topic有效性检查器 (ID: {checker_id})")
    
    consumer_checker_id = rocketmq_manager.add_checker_info(
        checker_name="CONSUMER_GROUP_VALIDITY",
        description="消费者组有效性检查器",
        usage="验证消费者组的存在和状态，用于排查消息消费异常",
        admin_api="getConsumerGroupInfoHistory(groupId, clusterName, consumerGroup, startTime, endTime)",
        tags=["消费者组", "消费异常", "状态检查"]
    )
    print(f"✓ 添加消费者组有效性检查器 (ID: {consumer_checker_id})")
    
    # Search and display knowledge
    print("\n4. Searching RocketMQ knowledge...")
    
    # Search troubleshooting guides
    troubleshooting_results = rocketmq_manager.search_troubleshooting(query="发送失败")
    print(f"\n找到 {len(troubleshooting_results)} 个与'发送失败'相关的排查指南:")
    for i, item in enumerate(troubleshooting_results, 1):
        print(f"{i}. {item.title}")
        print(f"   标签: {', '.join(item.tags)}")
    
    # Search all checkers
    checkers = rocketmq_manager.get_all_checkers()
    print(f"\n系统中共有 {len(checkers)} 个诊断检查器:")
    for i, item in enumerate(checkers, 1):
        print(f"{i}. {item.title}")
    
    # Export knowledge
    print("\n5. Exporting RocketMQ knowledge...")
    export_data = rocketmq_manager.export_rocketmq_knowledge()
    
    # Save export to file
    export_file = workspace / "knowledge" / "rocketmq_export.json"
    with open(export_file, 'w', encoding='utf-8') as f:
        json.dump(export_data, f, indent=2, ensure_ascii=False)
    print(f"✓ 知识库已导出到: {export_file}")
    
    # Display statistics
    print("\n=== 知识库统计 ===")
    domains = store.get_domains()
    print(f"支持的领域: {', '.join(domains)}")
    
    rocketmq_items = store.search_knowledge(domain="rocketmq")
    print(f"RocketMQ知识条目: {len(rocketmq_items)} 个")
    
    categories = store.get_categories(domain="rocketmq")
    print(f"RocketMQ分类: {', '.join(categories)}")
    
    print("\n=== Demo完成 ===")
    print("现在您可以使用以下工具来访问这些知识:")
    print("- knowledge_search: 搜索知识库")
    print("- knowledge_add: 添加新知识")
    print("- knowledge_rocketmq: RocketMQ专用知识管理")
    print("- knowledge_export: 导出知识库")


if __name__ == "__main__":
    main()