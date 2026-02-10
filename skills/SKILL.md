---
name: "rocketmq-diagnosis"
description: "诊断RocketMQ消息发送失败和消费者问题。当用户询问消息诊断、消费者组问题或RocketMQ故障排除时调用。"
---

# RocketMQ诊断技能

此技能为RocketMQ消息系统提供全面的诊断能力，包括消息发送失败、消费者组问题以及各种检查。

## 概述

RocketMQ诊断系统使用有限状态机（FSM）来管理诊断流程，包含多个检查器来验证消息系统的不同方面。每个检查器使用特定的admin
API方法来收集诊断信息。

## 诊断检查器

### 1. 消息轨迹检查器

- **类型**: `MESSAGE_TRACE`
- **目的**: 追踪消息在系统中的流转
- **Admin API**: `queryMessageByTopicAndKey(groupId, "RMQ_SYS_TRACE_TOPIC", messageId)`
- **使用场景**: 调查消息投递问题或跟踪消息生命周期

### 2. 主题有效性检查器

- **类型**: `TOPIC_VALIDITY`
- **目的**: 验证主题的存在和配置
- **Admin API**: `getTopicInfoHistory(groupId, topic, startTime, endTime)`
- **使用场景**: 验证主题配置或排查主题相关问题

### 3. 消费者组有效性检查器

- **类型**: `CONSUMER_GROUP_VALIDITY`
- **目的**: 验证消费者组的存在和状态
- **Admin API**: `getConsumerGroupInfoHistory(groupId, clusterName, consumerGroup, startTime, endTime)`
- **使用场景**: 验证消费者组配置或排查消费者组问题

### 4. 消息重试等待检查器

- **类型**: `MESSAGE_RETRY_AWAIT`
- **目的**: 检查消息是否在重试队列中等待
- **Admin API**:
    - `getMessageInfo(groupId, topic, messageId)`
    - `getMessageInfo(groupId, "SCHEDULE_TOPIC_XXXX", messageId)`
    - `getBrokerName(groupId, socketAddress)`
- **使用场景**: 调查消息重试延迟或消息未被消费

### 5. 消息延迟检查器

- **类型**: `MESSAGE_LAG`
- **目的**: 检查消息消费延迟
- **Admin API**:
    - `getLatestMessage(groupId, topic, messageId)`
    - `getBrokerName(groupId, socketAddress)`
    - `getConsumeOffset(ConsumeOffsetParam)`
- **使用场景**: 调查消息消费延迟或滞后

### 6. 消费者部分延迟检查器

- **类型**: `CONSUMER_PARTIAL_LAG`
- **目的**: 识别部分消费者延迟问题
- **Admin API**: `fetchLastMessages(groupId, status)`
- **使用场景**: 当部分消费者延迟而其他消费者正常时

### 7. 消费者组一致性检查器

- **类型**: `CONSUME_GROUP_SUBSCRIPTION_CONSISTENCY`
- **目的**: 检查消费者组订阅一致性
- **Admin API**: `getSubscriptionTable()` (来自ConsumerConnection)
- **使用场景**: 当消费者订阅不一致时

### 8. 消费者消息队列平衡检查器

- **类型**: `CONSUMER_MESSAGE_QUEUE_BALANCE`
- **目的**: 验证消费者之间的消息队列平衡
- **Admin API**: `getRunningInfos()` (来自ConsumerGroupSnapshot)
- **使用场景**: 当消息队列分布不均时

## 巡检任务

### 消费者组巡检

#### 客户端版本一致性

- **目的**: 确保所有消费者客户端使用相同版本
- **Admin API**: `getRunningInfos()` 来自ConsumerGroupSnapshot
- **使用场景**: 遇到版本相关的兼容性问题时

#### 客户端消息缓存数量

- **目的**: 监控每个客户端的消息缓存数量
- **Admin API**: `getRunningInfos()` 来自ConsumerGroupSnapshot
- **使用场景**: 调查高内存使用或消息处理缓慢时

#### 消息消费耗时

- **目的**: 跟踪消息消费时间
- **Admin API**: `getRunningInfos()` 来自ConsumerGroupSnapshot
- **使用场景**: 调查消息处理缓慢时

#### 消息消费失败数量

- **目的**: 监控消息消费失败
- **Admin API**: `getRunningInfos()` 来自ConsumerGroupSnapshot
- **使用场景**: 遇到高消息失败率时

## MQAdminService方法

### 消息操作

- `getMessageInfo(groupId, topic, messageId)` - 获取特定消息信息
- `getLatestMessage(groupId, topic, messageId)` - 获取最新消息
- `queryMessageByTopicAndKey(groupId, topic, key)` - 按主题和键查询消息

### 元数据操作

- `getBrokerName(groupId, socketAddress)` - 从套接字地址获取Broker名称
- `getConsumeOffset(ConsumeOffsetParam)` - 获取消费偏移量
- `getTopicInfoHistory(groupId, topic, startTime, endTime)` - 获取主题历史信息
- `getConsumerGroupInfoHistory(groupId, clusterName, consumerGroup, startTime, endTime)` - 获取消费者组历史信息

## 诊断流程

1. **上下文收集**: 收集诊断上下文（groupId、topic、messageId、consumerGroup等）
2. **检查器执行**: 根据诊断类型执行相关检查器
3. **Admin API调用**: 每个检查器调用相应的admin API方法
4. **结果分析**: 分析结果并确定诊断状态
5. **报告生成**: 生成综合诊断报告

## 常见诊断场景

### 消息未被消费

1. 检查消息轨迹以验证消息投递
2. 检查消费者组有效性
3. 检查消息延迟
4. 检查消费者部分延迟
5. 检查消费者队列平衡

### 消息发送失败

1. 检查主题有效性
2. 检查消息轨迹
3. 检查Broker状态
4. 检查网络连接

### 消费者问题

1. 检查消费者组有效性
2. 检查订阅一致性
3. 检查队列平衡
4. 检查消费者版本一致性
5. 检查消息消费耗时

## 检查说明

- 检查器直接使用 rocketmq mcp 调用
- 基于巡检的检查器使用预先收集的巡检数据
- 结果包括状态（PASS/FAIL/UNKNOWN）和详细信息
- 检查器可以链接在一起进行综合诊断

