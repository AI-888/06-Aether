# RocketMQ知识库功能

本模块提供了内置的RocketMQ知识库功能，包含丰富的RocketMQ相关知识，包括故障排查指南、配置指南、最佳实践和诊断检查器。

## 功能特性

- **自动初始化**: 当知识库为空时自动加载内置RocketMQ知识
- **分类管理**: 支持故障排查、配置指南、最佳实践、诊断工具等分类
- **智能搜索**: 支持按关键词、标签、分类进行搜索
- **扩展性**: 支持添加自定义知识条目

## 使用方法

### 1. 自动初始化

当您首次使用知识库时，系统会自动初始化内置的RocketMQ知识：

```python
from nanobot.knowledge.store import KnowledgeStore

# 创建知识库实例
store = KnowledgeStore(workspace_path)
# 系统会自动初始化RocketMQ知识
```

### 2. 手动初始化

如果需要手动初始化RocketMQ知识：

```python
from nanobot.knowledge import initialize_rocketmq_knowledge

# 手动初始化RocketMQ知识
count = initialize_rocketmq_knowledge(workspace_path)
print(f"初始化了 {count} 条RocketMQ知识")
```

### 3. 使用RocketMQ知识管理器

```python
from nanobot.knowledge.store import KnowledgeStore, DomainKnowledgeManager

# 创建知识库和RocketMQ管理器
store = KnowledgeStore(workspace_path)
rocketmq_manager = DomainKnowledgeManager(store, "rocketmq")

# 搜索故障排查指南
troubleshooting_results = rocketmq_manager.search_troubleshooting(query="发送失败")
for item in troubleshooting_results:
    print(f"标题: {item.title}")
    print(f"内容: {item.content[:100]}...")
    print(f"标签: {', '.join(item.tags)}")
    print("---")

# 搜索配置指南
config_results = rocketmq_manager.search_configuration()

# 获取所有诊断检查器
checkers = rocketmq_manager.get_all_checkers()
```

### 4. 添加自定义知识

```python
# 添加故障排查指南
item_id = rocketmq_manager.add_troubleshooting_guide(
    title="自定义故障排查指南",
    content="详细的问题描述和解决方案...",
    tags=["自定义", "故障排查"]
)

# 添加配置指南
item_id = rocketmq_manager.add_configuration_guide(
    title="自定义配置指南",
    content="配置参数说明...",
    tags=["自定义", "配置"]
)

# 添加最佳实践
item_id = rocketmq_manager.add_best_practice(
    title="自定义最佳实践",
    content="最佳实践说明...",
    tags=["自定义", "最佳实践"]
)

# 添加诊断检查器
item_id = rocketmq_manager.add_checker_info(
    checker_name="CUSTOM_CHECKER",
    description="自定义检查器描述",
    usage="使用场景说明",
    admin_api="getCustomInfo()",
    tags=["自定义", "检查器"]
)
```

## 内置知识内容

### 故障排查指南

- 消息发送失败排查指南
- 消费者组订阅一致性异常排查
- 消息堆积问题排查

### 配置指南

- RocketMQ Broker核心配置参数
- NameServer配置最佳实践

### 最佳实践

- RocketMQ消息设计最佳实践
- 消费者组设计最佳实践

### 诊断检查器

- TOPIC_VALIDITY: Topic有效性检查器
- CONSUMER_GROUP_VALIDITY: 消费者组有效性检查器
- BROKER_HEALTH: Broker健康状态检查器

## 工具集成

知识库功能已集成到Agent工具系统中，可以通过以下工具使用：

- `knowledge_search`: 搜索知识库
- `knowledge_add`: 添加新知识
- `knowledge_domain`: 领域专用知识管理
- `knowledge_export`: 导出知识库

## 测试

运行测试脚本验证功能：

```bash
python test_rocketmq_knowledge.py
```

## 扩展

您可以轻松扩展此系统以支持其他领域的知识管理：

1. 创建新的领域初始化器（参考`rocketmq_init.py`）
2. 在`store.py`中添加相应的自动初始化逻辑
3. 使用`DomainKnowledgeManager`管理特定领域的知识