---
name: rocketmq
description: apache rocketmq排障助手 (no API key required).
homepage: https://rocketmq.apache.org
metadata: {"nanobot":{"emoji":"🌤️","requires":{"bins":["curl"]}}}
---

# 一、技能概述

本技能适配 Apache RocketMQ ，为AI Agent提供多类细分异常排查能力，覆盖消息发送、消费、堆积、连接等全场景，可自动识别异常问题、定位核心原因，并输出标准化处理建议，助力快速解决
RocketMQ 生产/消费异常。

核心适配场景：RocketMQ 消息发送失败、消费失败、消息堆积、客户端连接异常、权限认证异常等各类常见问题，基于 RocketMQ
原生自助排查流程，实现标准化、可执行的排查逻辑，每个技能聚焦一类异常，提升排查精准度。

# 二、核心检查器汇总表（统一整理）

以下为所有检查器的统一汇总，各细分技能中仅引用检查器名称，详细信息可参考本表，确保检查器定义统一、引用规范：

| 检查器名称                                  | 功能描述           | 所属分组                                             | 关联技能                       |
|----------------------------------------|----------------|--------------------------------------------------|----------------------------|
| TOPIC_VALIDITY                         | Topic 有效性检查    | TOPIC_CHECK, INPUT_PARAM                         | 消息发送异常诊断、服务端异常诊断           |
| CONSUMER_GROUP_VALIDITY                | 消费者组有效性检查      | CONSUMER_GROUP_CHECK, INPUT_PARAM                | 消息消费异常诊断                   |
| CONSUME_GROUP_SUBSCRIPTION_CONSISTENCY | 消费者组订阅一致性检查    | CLIENT_CONFIG                                    | 消息消费异常诊断                   |
| MESSAGE_CONSUMER_GROUP_VISIBILITY      | 消息对消费者组的可见性检查  | CLIENT_UP_TIME                                   | 消息消费异常诊断、网络与连接异常诊断         |
| MESSAGE_TRACE                          | 消息链路追踪检查       | MESSAGE_TRACE                                    | 消息链路与有效性诊断                 |
| MESSAGE_VALIDITY                       | 消息有效性检查        | INPUT_PARAM                                      | 消息发送异常诊断、消息链路与有效性诊断        |
| RETRY_MESSAGE_LAG                      | 重试消息积压检查       | RETRY_MESSAGE_CHECK                              | 消息堆积异常诊断                   |
| MESSAGE_LAG                            | 消息积压检查         | MESSAGE_LAG_CHECK                                | 消息堆积异常诊断                   |
| MESSAGE_RETRY_AWAIT                    | 消息重试等待检查       | RETRY_MESSAGE_CHECK                              | 消息堆积异常诊断                   |
| CONSUMER_PARTIAL_LAG                   | 消费者部分积压检查      | CONSUMER_REASON_ANALYSIS                         | 消息堆积异常诊断                   |
| CONSUMER_ALL_LAG_RESULT                | 消费者全部积压结果      | -                                                | 消息堆积异常诊断                   |
| CONSUMER_MESSAGE_QUEUE_BALANCE         | 消费者队列负载均衡检查    | CONSUMER_GROUP_CHECK, QUEUE_CONSUMER_MATCH       | 消息堆积异常诊断                   |
| TOPIC_ROUTE_CONSISTENCY                | Topic 路由一致性检查  | TOPIC_CHECK, INPUT_PARAM                         | 消息发送异常诊断、网络与连接异常诊断、服务端异常诊断 |
| TOPIC_ACROSS_MULTIPLE_CLUSTER          | Topic 跨集群检查    | TOPIC_CHECK                                      | 消息发送异常诊断、服务端异常诊断           |
| TOPIC_MESSAGE_BALANCE                  | Topic 消息分布均衡检查 | TOPIC_CHECK, QUEUE_MESSAGE_DISTRIBUTION_ANALYSIS | 消息发送异常诊断、消息堆积异常诊断          |
| MESSAGE_SUBSCRIPTION_CONSISTENCY       | 消息订阅一致性检查      | CLIENT_CONFIG                                    | 消息消费异常诊断                   |

## 诊断检查器详细说明

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

## 检查说明

- 检查器直接使用 rocketmq mcp 调用
- 基于巡检的检查器使用预先收集的巡检数据
- 结果包括状态（PASS/FAIL/UNKNOWN）和详细信息
- 检查器可以链接在一起进行综合诊断

# 三、细分异常排查技能（按异常类型拆分）

## 技能1：日志通用异常诊断

### 1.1 技能目标

通过解析用户上传的 .log 格式日志文件（RocketMQ
原生日志，含客户端、Broker、NameServer日志），识别日志中未收录格式、通用异常等非特定场景异常，输出基础处理指引，为后续精准排查提供支撑。

### 1.2 操作流程（AI Agent 可执行步骤）

1. 引导用户获取 RocketMQ 日志文件（客户端日志、Broker日志、NameServer日志均可），确保日志文件以 .log 为扩展名。

2. 指引用户上传目标日志文件（提醒：文件大小不超过64MB），提交诊断请求。

3. 告知用户耐心等待几分钟，待诊断任务执行完成后，查看诊断报告结果；若需稍后查看，可在诊断历史中找到对应任务，查看详情。

4. AI Agent 解析诊断报告中的通用异常信息，匹配下方对照表，输出结构化排查结果及处理步骤，若识别到特定场景异常，自动跳转至对应细分技能。

### 1.3 通用日志异常-处理建议对照表

| 异常问题        | 核心原因               | 处理建议                            |
|-------------|--------------------|---------------------------------|
| 日志中存在未收录的日志 | 诊断系统未覆盖该类日志格式或异常类型 | 建议查阅 RocketMQ 官方文档，或提交社区issue咨询 |

### 1.4 关联检查器

无专属检查器，主要用于基础日志格式校验，识别未覆盖异常，引导后续排查（检查器详细信息参考「二、核心检查器汇总表」）。

## 技能2：消息消费异常诊断

### 2.1 技能目标

聚焦消息消费全流程异常，包括消费失败、消费确认失败、订阅关系异常、无在线消费端等场景，通过日志解析、服务端校验，定位消费端、服务端或配置层面的核心原因，输出精准处理建议。

### 2.2 操作流程（AI Agent 可执行步骤）

1. 引导用户上传相关日志文件（优先消费端日志、Broker日志），明确异常现象（如消费失败、无消费记录等）。

2. 若日志无法明确原因，引导用户选择需排查的 RocketMQ 节点及时间范围，补充执行后端服务诊断。

3. AI Agent 调用关联检查器，解析日志及服务端诊断结果，匹配异常类型，输出结构化排查结果。

### 2.3 消费异常-处理建议对照表

| 异常问题         | 核心原因                          | 处理建议                                                                           |
|--------------|-------------------------------|--------------------------------------------------------------------------------|
| 消费异常         | 消费端代码逻辑错误、消费配置异常              | 检查消费端代码（如消费逻辑、异常捕获），排查消费配置，无法定位可查阅官方文档                                         |
| 消费确认失败       | 网络异常、后端服务（Broker）异常、消费端确认逻辑错误 | 1. 若伴随网络/后端服务异常，参考对应异常处理建议；2. 检查消费端确认逻辑（如手动确认、自动确认配置）；3. 无法解决可查阅官方文档           |
| 无在线消费端       | 消费端进程未启动、消费端与Broker连接异常       | 1. 检查消费端进程是否正常启动，排查消费端启动日志；2. 检查消费端与Broker的连接配置，确保网络连通；3. 通过mqadmin工具查看消费端在线状态 |
| 订阅关系不一致      | 同一消费Group下，不同客户端的订阅规则不一致      | 1. 检查同一Group下所有消费端的订阅配置，确保订阅规则一致；2. 参考 RocketMQ 官方「订阅关系（Subscription）」规范       |
| 订阅关系不存在      | 消费端未配置订阅关系、消费端离线、网络异常         | 1. 检查消费端是否在线，排查网络连通性；2. 检查消费端订阅关系配置是否正确、完整                                     |
| 消费group信息未找到 | 客户端订阅时使用的Group未创建，或配置错误       | 1. 检查Group是否已创建，确认客户端订阅时使用的Group名称配置正确；2. 若未创建，通过mqadmin工具或代码创建对应Group         |

### 2.4 关联检查器

CONSUME_GROUP_SUBSCRIPTION_CONSISTENCY、MESSAGE_CONSUMER_GROUP_VISIBILITY、CONSUMER_GROUP_VALIDITY、MESSAGE_SUBSCRIPTION_CONSISTENCY（检查器详细信息参考「二、核心检查器汇总表」）

## 技能3：消息发送异常诊断

### 3.1 技能目标

聚焦消息发送全流程异常，包括发送失败、获取路由失败、消息参数非法、定时消息异常等场景，通过日志解析、消息有效性校验，定位发送端、服务端或配置层面的核心原因，输出精准处理建议。

### 3.2 操作流程（AI Agent 可执行步骤）

1. 引导用户上传相关日志文件（优先发送端日志、NameServer日志），明确异常现象（如发送超时、发送失败等）。

2. 若日志提示Topic、路由相关异常，引导用户选择需排查的 RocketMQ 节点，补充执行后端服务诊断。

3. AI Agent 调用关联检查器，解析日志及服务端诊断结果，匹配异常类型，输出结构化排查结果。

### 3.3 发送异常-处理建议对照表

| 异常问题      | 核心原因                                             | 处理建议                                                                      |
|-----------|--------------------------------------------------|---------------------------------------------------------------------------|
| 消息发送失败    | Topic资源未创建、网络异常、后端服务（Broker/NameServer）异常、消息参数非法 | 1. 检查Topic资源是否已创建；2. 若伴随网络/后端服务异常，参考对应异常处理建议；3. 检查消息参数是否合法；4. 无法解决可查阅官方文档 |
| 获取路由失败    | Topic资源未创建、网络异常、后端服务（Broker/NameServer）异常        | 1. 检查Topic资源是否已创建；2. 若伴随网络/后端服务异常，参考对应异常处理建议；3. 无法解决可查阅官方文档或社区咨询          |
| topic未找到  | 客户端发送/订阅的Topic未创建，或配置错误                          | 1. 检查Topic是否已创建，通过mqadmin工具或官方命令查询；2. 若不存在则创建，确保客户端Topic配置正确              |
| 非法的定时消息时长 | 定时消息时长超出 RocketMQ 版本支持的配额限制                      | 1. 调整定时消息时长，符合当前 RocketMQ 版本的配额限制；2. 若配额不足，升级 RocketMQ 版本                 |
| 非法的消息属性   | 消息属性存在冲突、属性配置不符合 RocketMQ 规范                     | 根据错误日志，检查消息属性冲突问题，参考 RocketMQ 官方「消息内部属性」规范配置                              |
| 消息类型不匹配   | Topic配置的消息类型与代码中发送/消费的消息类型不一致                    | 检查Topic配置的消息类型，确保与代码中实际发送/消费的消息类型一致，参考官方Topic配置文档                         |
| 请求码不支持    | 客户端发送的请求类型不被Broker支持（如code 320不支持批量消息）           | 调整请求类型，避免使用Broker不支持的请求码（如避免批量消息），参考官方请求类型规范                              |

### 3.4 关联检查器

TOPIC_VALIDITY、MESSAGE_VALIDITY、TOPIC_ROUTE_CONSISTENCY、TOPIC_ACROSS_MULTIPLE_CLUSTER、TOPIC_MESSAGE_BALANCE（检查器详细信息参考「二、核心检查器汇总表」）

## 技能4：消息堆积异常诊断

### 4.1 技能目标

聚焦消息堆积相关异常，包括正常队列堆积、重试队列堆积、部分消费者堆积等场景，通过日志解析、堆积情况校验，定位消费速度、负载均衡或配置层面的核心原因，输出精准处理建议。

### 4.2 操作流程（AI Agent 可执行步骤）

1. 引导用户上传消费端、Broker日志，明确堆积场景（正常堆积、重试堆积、部分堆积等）。

2. 引导用户选择需排查的 RocketMQ 节点、Topic及时间范围，补充执行后端服务诊断和Topic访问拓扑查询，确认消费端在线状态及负载情况。

3. AI Agent 调用关联检查器，解析日志、服务端诊断及拓扑查询结果，匹配堆积类型，输出结构化排查结果。

### 4.3 堆积异常-处理建议对照表

| 异常问题    | 核心原因                      | 处理建议                                                                     |
|---------|---------------------------|--------------------------------------------------------------------------|
| 本地消息缓存满 | 消费端处理速度过慢，缓存无法及时清理        | 优化消费端逻辑，提升消费处理速度，减少缓存堆积，可考虑增加消费端实例数                                      |
| 触发服务端限流 | Broker节点生产/消费TPS达到阈值，资源不足 | 1. 查看Broker节点监控，确认生产/消费TPS水位及被限流请求次数；2. 优化Broker配置、扩容Broker节点，或调整生产/消费速率 |

### 4.4 关联检查器

MESSAGE_LAG、RETRY_MESSAGE_LAG、MESSAGE_RETRY_AWAIT、CONSUMER_PARTIAL_LAG、CONSUMER_ALL_LAG_RESULT、CONSUMER_MESSAGE_QUEUE_BALANCE、TOPIC_MESSAGE_BALANCE（检查器详细信息参考「二、核心检查器汇总表」）

## 技能5：网络与连接异常诊断

### 5.1 技能目标

聚焦网络连通性、客户端连接相关异常，包括网络异常、心跳发送失败、客户端下线失败、本地请求丢失等场景，通过日志解析、服务端状态校验，定位网络、服务端或客户端层面的核心原因，输出精准处理建议。

### 5.2 操作流程（AI Agent 可执行步骤）

1. 引导用户上传客户端、Broker日志，明确异常现象（如连接超时、心跳失败等）。

2. 引导用户排查客户端与Broker/NameServer之间的网络连通性，同时选择需排查的 RocketMQ 节点，补充执行后端服务诊断，确认服务端运行状态。

3. AI Agent 调用关联检查器，解析日志及服务端诊断结果，匹配异常类型，输出结构化排查结果。

### 5.3 网络与连接异常-处理建议对照表

| 异常问题    | 核心原因                                            | 处理建议                                                                   |
|---------|-------------------------------------------------|------------------------------------------------------------------------|
| 网络异常    | 客户端与Broker/NameServer网络连通性异常、网络波动               | 排查客户端与Broker/NameServer之间的网络连通性，检查防火墙、网络路由配置，修复网络问题                    |
| 心跳发送失败  | Topic/Group资源未创建、网络异常、后端服务（Broker/NameServer）异常 | 1. 检查Topic、Group资源是否已创建；2. 若伴随网络/后端服务异常，参考对应异常处理建议；3. 无法解决可查阅官方文档或社区咨询 |
| 客户端下线失败 | 网络异常、后端服务（Broker）异常、应用进程异常终止                    | 1. 若伴随网络/后端服务异常，参考对应异常处理建议；2. 若发生在应用进程终止时，可忽略该异常                       |
| 本地请求丢失  | 客户端异常卡住（如发生Full GC）、网络问题导致重复传输                  | 1. 排查客户端是否发生Full GC，优化JVM配置；2. 若为网络偶发重复传输，可忽略该异常，排查网络稳定性               |

### 5.4 关联检查器

MESSAGE_CONSUMER_GROUP_VISIBILITY、TOPIC_ROUTE_CONSISTENCY（检查器详细信息参考「二、核心检查器汇总表」）

## 技能6：服务端异常诊断

### 6.1 技能目标

聚焦 RocketMQ 服务端（Broker/NameServer）异常，包括节点故障、服务端宕机、服务端更新等场景，通过服务端状态诊断、日志解析，定位服务端运维或部署层面的核心原因，输出精准处理建议。

### 6.2 操作流程（AI Agent 可执行步骤）

1. 引导用户选择需排查的 RocketMQ 节点（Broker/NameServer），明确节点标识。

2. 指引用户选择诊断的时间范围（需覆盖异常发生时段），提交诊断请求。

3. 告知用户耐心等待几分钟，待任务执行完成后，查看诊断报告结果；若需补充信息，引导用户上传服务端日志。

4. AI Agent 解析诊断报告及日志信息，匹配异常类型，输出结构化排查结果及处理步骤。

### 6.3 服务端异常-处理建议对照表

| 检查项     | 异常问题                                  | 核心原因                                           | 处理建议                                                |
|---------|---------------------------------------|------------------------------------------------|-----------------------------------------------------|
| 节点检查    | 节点不存在                                 | 用户输入的节点标识错误、节点未部署                              | 检查节点标识配置是否正确，确认该Broker/NameServer节点已部署并正常注册         |
| 节点检查    | 节点不在运行状态                              | 节点进程被停止、节点异常故障（如宕机、配置错误）                       | 检查节点进程状态，查看节点日志排查故障，重启节点或修复配置问题                     |
| Topic检查 | topic不存在                              | 用户选择的Topic未创建                                  | 通过mqadmin工具或官方命令，创建对应Topic，确保Topic配置正确并同步至所有节点      |
| 服务端检查   | 服务端机器宕机                               | RocketMQ 节点所在物理服务器/虚拟机故障                       | 该异常可能导致短时间网络报错，属于正常运维范围，可忽略；若报错持续，重启服务器并恢复节点进程      |
| 服务端检查   | 服务端发布更新                               | RocketMQ 节点进行版本更新、配置调整等运维操作                    | 该异常可能导致秒级别网络闪断，客户端日志可见短时间报错，可忽略；若更新失败，回滚版本并排查运维操作问题 |
| 后端服务异常  | RocketMQ Broker/NameServer集群异常、运维操作影响 | 检查Broker/NameServer进程状态、日志，排查集群部署问题，参考官方集群运维文档 |                                                     |

# 四、巡检任务与常见诊断场景

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

## 诊断流程说明

RocketMQ诊断系统使用有限状态机（FSM）来管理诊断流程，包含多个检查器来验证消息系统的不同方面。每个检查器使用特定的admin
API方法来收集诊断信息。

### 诊断流程步骤

1. **上下文收集**: 收集诊断上下文（groupId、topic、messageId、consumerGroup等）
2. **检查器执行**: 根据诊断类型执行相关检查器
3. **Admin API调用**: 每个检查器调用相应的admin API方法
4. **结果分析**: 分析结果并确定诊断状态
5. **报告生成**: 生成综合诊断报告

### 检查器调用方式

- 检查器直接使用 rocketmq mcp 调用
- 基于巡检的检查器使用预先收集的巡检数据
- 结果包括状态（PASS/FAIL/UNKNOWN）和详细信息
- 检查器可以链接在一起进行综合诊断


