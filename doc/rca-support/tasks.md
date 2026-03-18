# 实现计划：根因分析（RCA）支持

## 概述

本实现计划在现有 Nanobot 框架基础上，构建基于 SLM 的结构化 RCA 执行引擎。核心是将复杂的多步骤排障逻辑编码到 YAML Skill 中，由引擎按步骤驱动 SLM 执行。实现将分为以下几个阶段：数据模型定义、核心引擎开发、安全与审计、集成测试。

## 前置依赖

- 现有 Nanobot Agent Loop（单轮交互模式）
- ChromaKnowledgeStore / IntentRoutingStore（RAG 向量检索）
- ToolRegistry（工具注册与执行）
- LLMProvider / LiteLLM（SLM 推理调用）

## 任务

- [x] 1. 安装依赖和项目配置
  - 在 pyproject.toml 中添加 `pyyaml`、`watchdog` 依赖
  - 在 `nanobot/config/schema.py` 中新增 `RCAConfig` 配置类
  - 在 `Config` 根配置中注册 `rca` 配置段
  - 创建 `nanobot/rca/` 包目录和 `__init__.py`
  - _需求: 1、3、5、7_

- [x] 2. 实现 Skill 数据模型定义（对应需求 1）
  - [x] 2.1 实现 `nanobot/rca/schema.py`
    - 定义 `StepType` 枚举（`llm` / `tool` / `root_cause_definition`）
    - 定义 `OutputSchema` 数据类
    - 定义 `RootCauseRule` 数据类（`when`、`root_cause`、`solution`）
    - 定义 `SkillStep` 数据类（`id`、`type`、`prompt`、`tool`、`input`、`input_from`、`output_schema`、`logic`）
    - 定义 `RCASkill` 数据类（`name`、`version`、`description`、`type`、`input_schema`、`steps`）
    - _需求: 1.1, 1.2, 1.3, 1.5_

  - [x] 2.2 实现 YAML 解析与校验 `nanobot/rca/parser.py`
    - 实现 `parse_yaml(raw: dict) -> RCASkill` 方法
    - 实现 `validate(raw: dict) -> list[str]` 校验方法
    - 校验规则：顶层字段完整性、步骤 ID 唯一性、步骤类型合法性
    - 校验规则：LLM 步骤必需 `prompt`、Tool 步骤必需 `tool`、RCD 步骤必需 `logic`
    - 校验规则：`input_from` 引用有效性（引用的 step_id 必须是前置步骤）
    - 校验规则：模板变量一致性（WARNING 级别）
    - _需求: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6_

  - [x] 2.3 为 Schema 和 Parser 编写单元测试
    - 测试合法 YAML 解析成功
    - 测试缺少必需字段时校验失败
    - 测试步骤 ID 重复时校验失败
    - 测试非法步骤类型校验失败
    - 测试 `input_from` 引用无效时校验失败
    - 测试 Demo Skill（rocketmq_troubleshoot）完整解析
    - _需求: 1.1 ~ 1.6_

- [x] 3. 实现 Skill 加载与管理（对应需求 3）
  - [x] 3.1 实现 `nanobot/rca/loader.py` - RCASkillLoader
    - 实现 `load_all()` 方法：扫描目录加载所有 `.yaml` 文件
    - 实现 `load_file(path)` 方法：加载并校验单个文件
    - 实现 `get_skill(name)` 方法：按名称获取已加载 Skill
    - 实现 `list_skills()` 方法：列出所有已加载 Skill 摘要
    - 实现 `_register_to_rag(skill)` 方法：将 Skill 注册到 RAG 向量库
    - 使用内存字典缓存已加载的 Skill（`_skills: dict[str, RCASkill]`）
    - _需求: 3.1, 3.2, 3.4, 3.5_

  - [x] 3.2 实现文件监听热加载
    - 使用 `watchdog` 库实现 `start_watcher()` 和 `stop_watcher()`
    - 处理新增、修改、删除事件
    - 修改事件：校验通过后更新内存缓存 + RAG 索引；失败则保留旧版本
    - 删除事件：从内存缓存和 RAG 索引中移除
    - 确保热加载不影响正在执行的 RCA 任务（读写锁或 Copy-on-Write）
    - _需求: 3.3, 7.1, 7.3_

  - [x] 3.3 为 Skill 加载器编写单元测试
    - 测试加载单个合法 YAML 文件
    - 测试加载目录中所有 YAML 文件
    - 测试校验失败文件跳过并记录错误日志
    - 测试 `get_skill` 和 `list_skills` 方法
    - 测试热加载：新增文件后自动加载
    - 测试热加载：修改文件后自动更新
    - 测试热加载：删除文件后自动移除
    - _需求: 3.1 ~ 3.5_

- [x] 4. 检查点 — 确保 Skill 模型和加载器测试通过
  - 运行所有 Task 2、3 相关的单元测试
  - 确保 Demo Skill YAML 可以完整加载和校验
  - 如有问题请向用户提问

- [x] 5. 实现步骤上下文管理（对应需求 2.8）
  - [x] 5.1 实现 `nanobot/rca/context.py` - StepContext
    - 实现 `__init__(inputs)` 初始化外部输入
    - 实现 `set_output(step_id, output)` 存储步骤输出
    - 实现 `resolve_input_from(refs)` 解析 `input_from` 引用
      - 支持格式：`<step_id>.<field_name>`
      - 返回 `{field_name: value}` 字典
    - 实现 `resolve_template(template, extra_vars)` 渲染 prompt 模板
      - 替换 `{{变量名}}` 为实际值
      - 优先从 `extra_vars` → `input_from` 结果 → 外部输入 `_inputs` 中获取
    - 实现 `add_trace(trace)` 和 `get_all_traces()` 记录执行轨迹
    - _需求: 2.2, 2.3, 2.8_

  - [x] 5.2 实现 `StepTrace` 数据类
    - 字段：`step_id`、`step_type`、`start_time`、`end_time`、`input_data`、`output_data`、`status`、`error_message`
    - _需求: 2.6, 2.7_

  - [x] 5.3 为上下文管理编写单元测试
    - 测试外部输入初始化
    - 测试 `set_output` 和 `resolve_input_from` 引用解析
    - 测试 `resolve_template` 模板渲染（含 `{{变量名}}` 替换）
    - 测试引用缺失时抛出异常
    - 测试执行轨迹记录
    - _需求: 2.2, 2.3, 2.6, 2.7, 2.8_

- [x] 6. 实现分步执行引擎（对应需求 2）
  - [x] 6.1 实现 `nanobot/rca/engine.py` - RCAEngine
    - 实现 `__init__(provider, tool_registry, security_guard, audit_logger, model)`
    - 实现 `execute(skill, inputs, stream_callback)` 主方法：
      - 初始化 StepContext
      - 按 `steps` 列表顺序遍历执行每个步骤
      - 每步执行前记录 trace 开始时间
      - 每步执行后记录 trace 结束时间和输出
      - 全部步骤完成后汇总生成 RCAReport
    - _需求: 2.1, 2.5, 2.6_

  - [x] 6.2 实现 LLM 步骤执行 `_execute_llm_step()`
    - 从 `input_from` 解析前置步骤输出
    - 渲染 prompt 模板
    - 构建最小上下文消息（仅当前 prompt，禁止加载完整 Skill 文档）
    - 调用 `LLMProvider.chat()` 单轮推理
    - 解析 SLM 返回的 JSON 结果
    - 校验输出是否匹配 `output_schema`
    - 存入 StepContext
    - _需求: 2.2, 2.8_

  - [x] 6.3 实现 Tool 步骤执行 `_execute_tool_step()`
    - 通过 SecurityGuard 校验工具调用安全性
    - 通过 ToolRegistry 执行工具调用
    - 解析返回结果并按 `output_schema` 存入 StepContext
    - _需求: 2.3_

  - [x] 6.4 实现 Root Cause Definition 步骤执行 `_execute_rcd_step()`
    - 遍历 `logic` 列表
    - 将 `when` 条件（键值对形式）与前置步骤输出匹配
    - 支持比较运算符（如 `">90"`）
    - 命中规则输出 `root_cause` 和 `solution`
    - 无命中时输出默认回退值
    - _需求: 2.4_

  - [x] 6.5 实现 JSON 输出解析和校验
    - 实现 `_parse_json_output(content)` 从 SLM 文本回复中提取 JSON
    - 实现 `_validate_output(output, schema)` 校验字段和类型
    - 处理 SLM 输出格式不规范的降级策略（重试 1 次附加格式约束提示）
    - _需求: 2.2_

  - [x] 6.6 实现错误处理
    - LLM 调用超时：重试 1 次，仍失败则记录上下文快照并终止
    - LLM 输出格式错误：重试 1 次（附加格式约束），仍失败则终止
    - 工具调用失败：标记步骤为 error 并终止
    - `input_from` 引用缺失：终止并记录错误
    - 安全校验拒绝：立即终止并通知
    - 定义 `RCAExecutionError` 异常类
    - _需求: 2.7_

  - [x] 6.7 为执行引擎编写单元测试
    - 测试完整 Skill 工作流执行（extract_error → classify_problem → disk_check → determine_root_cause → conclusion）
    - 测试 LLM 步骤的 prompt 渲染和 SLM 调用
    - 测试 Tool 步骤的工具调用
    - 测试 RCD 步骤的规则匹配（命中 / 未命中）
    - 测试比较运算符匹配（`">90"`, `"<10"` 等）
    - 测试 `input_from` 跨步骤引用
    - 测试超时和格式错误的重试机制
    - 测试安全校验拒绝终止流程
    - 使用 Mock 模拟 LLMProvider 和 ToolRegistry
    - _需求: 2.1 ~ 2.8_

- [x] 7. 检查点 — 确保执行引擎核心逻辑测试通过
  - 运行所有 Task 5、6 相关的单元测试
  - 使用 Demo Skill YAML + Mock LLM 进行端到端验证
  - 如有问题请向用户提问

- [x] 8. 实现安全校验层（对应需求 6）
  - [x] 8.1 实现 `nanobot/rca/security.py` - SecurityGuard
    - 定义默认工具白名单（`check_disk_usage`、`kubectl_get_pods` 等）
    - 定义危险命令黑名单正则（`rm -rf`、`shutdown`、`reboot`、`mkfs` 等）
    - 实现 `validate_tool_call(tool_name, params)` 方法
    - 实现 `validate_command(command)` 方法
    - 支持从配置加载额外白名单 (`RCAConfig.security_whitelist`)
    - 定义 `SecurityViolationError` 异常
    - _需求: 6.1, 6.3_

  - [x] 8.2 为安全校验编写单元测试
    - 测试白名单内工具通过校验
    - 测试白名单外工具被拒绝
    - 测试危险命令被拒绝（`rm -rf /`、`shutdown` 等）
    - 测试配置额外白名单后通过校验
    - _需求: 6.1, 6.3_

- [x] 9. 实现审计日志（对应需求 6）
  - [x] 9.1 实现 `nanobot/rca/audit.py` - AuditLogger
    - 实现 `log_step()` 记录步骤执行日志（时间戳、步骤 ID、命令、结果、SLM 输入/输出）
    - 实现 `log_security_event()` 记录安全事件
    - 实现 `get_session_log()` 获取完整会话日志
    - 日志以 JSON Lines 格式持久化到 `rca_audit/` 目录
    - _需求: 6.2, 6.4_

  - [x] 9.2 为审计日志编写单元测试
    - 测试步骤日志记录和读取
    - 测试安全事件日志记录
    - 测试会话日志完整性（按 session_id 查询）
    - _需求: 6.2, 6.4_

- [x] 10. 实现 RCA 报告生成器（对应需求 4）
  - [x] 10.1 实现 `nanobot/rca/report.py` - ReportGenerator 和 RCAReport
    - 定义 `RCAReport` 数据类：`fault_summary`、`root_cause`、`confidence`、`execution_traces`、`recommendations`、`skill_name`、`skill_version`、`start_time`、`end_time`
    - 实现 `to_json()` 方法：输出结构化 JSON 格式
    - 实现 `to_markdown()` 方法：输出人类可读 Markdown 格式
    - 实现 `ReportGenerator.generate(ctx, skill)` 从 StepContext 汇总生成报告
    - _需求: 4.2, 4.4, 4.5_

  - [x] 10.2 为报告生成器编写单元测试
    - 测试 JSON 输出包含所有必需字段
    - 测试 Markdown 输出格式正确
    - 测试从 StepContext 汇总生成报告
    - _需求: 4.2, 4.5_

- [x] 11. 实现 Skill RAG 检索集成（对应需求 2.1）
  - [x] 11.1 扩展 IntentRoutingStore
    - 新增 `RCA_SKILLS_COLLECTION` 集合
    - 实现 `init_rca_skills_index(rca_loader)` 方法：构建 Skill 向量索引
    - 实现 `search_rca_skill(query, limit=1)` 方法：检索 Top-1 匹配 Skill
    - 索引内容：拼接 Skill 的 `name` + `description` + steps 描述文本
    - _需求: 2.1, 5.2_

  - [x] 11.2 为 Skill RAG 检索编写单元测试
    - 测试索引构建成功
    - 测试根据故障描述检索到匹配 Skill
    - 测试无匹配时返回空列表
    - _需求: 2.1, 5.2_

- [x] 12. 实现 RCA 路由控制器（对应需求 4）
  - [x] 12.1 实现 `nanobot/rca/router.py` - RCARouter
    - 实现 `route(fault_input)` 方法：
      - 调用 `search_rca_skill()` 进行 RAG 检索匹配
      - 如果匹配到 Skill → 使用 RCAEngine 执行
      - 如果需要多维度分析 → 调度多个专用 Agent（预留接口）
    - 定义 `FaultInput` 数据类（故障类型、故障描述、附加数据）
    - _需求: 4.1, 4.2_

  - [x] 12.2 为路由控制器编写单元测试
    - 测试匹配到 Skill 时正确路由
    - 测试未匹配到 Skill 时的降级处理
    - _需求: 4.1_

- [x] 13. 检查点 — 确保所有模块单元测试通过
  - 运行全部单元测试
  - 确保所有模块独立功能正常
  - 如有问题请向用户提问

- [x] 14. 实现系统集成
  - [x] 14.1 实现 RCA 触发工具 `nanobot/agent/tools/rca_trigger.py`
    - 继承 `Tool` 基类
    - 实现 `name="rca_analyze"`，`description` 和 `parameters`
    - 在 `execute()` 中调用 RCARouter 触发 RCA 流程
    - _需求: 2.1, 7.2_

  - [x] 14.2 在 AgentLoop 中注册 RCA 工具
    - 在 `_register_default_tools()` 中根据 `RCAConfig.enabled` 条件注册
    - 初始化 RCASkillLoader 并启动文件监听
    - 初始化 RCAEngine、SecurityGuard、AuditLogger
    - _需求: 3.3, 7.1_

  - [x] 14.3 配置与启动集成
    - 在 `nanobot/cli/commands.py` 或启动流程中读取 RCA 配置
    - 创建 RCA Skill 目录（如不存在）
    - 在服务启动时加载所有 Skill 并构建 RAG 索引
    - 在服务停止时关闭文件监听
    - _需求: 3.1, 3.3, 7.1_

  - [x] 14.4 为系统集成编写单元测试
    - 测试 RCA 工具注册到 ToolRegistry
    - 测试 RCA 配置 `enabled=false` 时不注册
    - 测试启动时 Skill 自动加载
    - _需求: 7.1, 7.2_

- [x] 15. 端到端集成测试
  - [x] 15.1 编写端到端测试
    - 使用 Demo Skill YAML（rocketmq_troubleshoot）
    - Mock LLMProvider 返回预期 JSON 输出
    - Mock ToolRegistry 返回 disk_usage 结果
    - 验证完整流程：加载 Skill → RAG 检索 → 引擎执行 → 报告生成
    - 验证 JSON 和 Markdown 报告内容正确
    - 验证审计日志完整性
    - _需求: 1 ~ 7 全覆盖_

  - [x] 15.2 编写性能测试
    - 测试 Skill 加载时间（100 个 YAML 文件）
    - 测试 RAG 检索响应时间（< 100ms）
    - 测试单步 SLM 调用耗时记录
    - 测试整体 RCA 流程耗时
    - _需求: 5.1, 5.2, 5.4_

  - [x] 15.3 编写安全测试
    - 测试注入危险命令到 Skill YAML 被拒绝
    - 测试 SLM 生成危险命令被拒绝
    - 测试安全拒绝后审计日志记录
    - _需求: 6.1, 6.2, 6.3_

- [x] 16. Prometheus 指标扩展
  - [x] 16.1 在 `nanobot/metrics.py` 中新增 RCA 指标
    - `aether_rca_execution_duration_seconds`（Histogram）：RCA 完整执行耗时
    - `aether_rca_step_duration_seconds`（Histogram）：单步骤执行耗时（按类型标签）
    - `aether_rca_execution_total`（Counter）：RCA 执行总次数（按状态标签）
    - `aether_rca_skill_match_total`（Counter）：Skill 检索匹配次数
    - `aether_rca_security_reject_total`（Counter）：安全拒绝次数
    - _需求: 5_

  - [x] 16.2 在 RCAEngine 中埋点指标上报
    - 在 `execute()` 方法中记录整体耗时和状态
    - 在每个步骤执行中记录步骤耗时
    - 在 SecurityGuard 中记录拒绝次数
    - _需求: 5_

- [x] 17. 创建示例 Skill 文件
  - [x] 17.1 创建 RocketMQ 排障 Skill
    - 在 `~/.nanobot/workspace/rca_skills/` 目录下创建 `rocketmq_troubleshoot.yaml`
    - 内容为需求文档中的 Demo Skill YAML
    - _需求: 1_

  - [x] 17.2 创建 Kubernetes Pod Crash 排障 Skill（可选）
    - 包含日志提取、状态检查、根因判断、结论生成步骤
    - 验证系统可扩展性
    - _需求: 7.3_

- [x] 18. 最终检查点 — 确保全部测试通过
  - 运行所有单元测试
  - 运行所有集成测试和端到端测试
  - 运行性能测试和安全测试
  - 确认 RCA 功能完整可用
  - 验证热加载功能正常
  - 验证审计日志完整
  - 如有问题请向用户提问

## 文件清单

### 新增文件

| 文件路径 | 说明 | 对应任务 |
|----------|------|----------|
| `nanobot/rca/__init__.py` | RCA 模块入口 | Task 1 |
| `nanobot/rca/schema.py` | Skill 数据模型定义 | Task 2.1 |
| `nanobot/rca/parser.py` | YAML 解析与校验 | Task 2.2 |
| `nanobot/rca/loader.py` | Skill 加载器（含热加载） | Task 3.1, 3.2 |
| `nanobot/rca/context.py` | 步骤上下文管理 | Task 5.1, 5.2 |
| `nanobot/rca/engine.py` | 分步执行引擎 | Task 6.1 ~ 6.6 |
| `nanobot/rca/security.py` | 安全校验层 | Task 8.1 |
| `nanobot/rca/audit.py` | 审计日志 | Task 9.1 |
| `nanobot/rca/report.py` | 报告生成器 | Task 10.1 |
| `nanobot/rca/router.py` | 路由控制器 | Task 12.1 |
| `nanobot/rca/evaluator.py` | 根因规则匹配器（可内联到 engine） | Task 6.4 |
| `nanobot/agent/tools/rca_trigger.py` | RCA 触发工具 | Task 14.1 |

### 修改文件

| 文件路径 | 修改内容 | 对应任务 |
|----------|----------|----------|
| `pyproject.toml` | 添加 `pyyaml`、`watchdog` 依赖 | Task 1 |
| `nanobot/config/schema.py` | 新增 `RCAConfig`，`Config` 中注册 `rca` | Task 1 |
| `nanobot/agent/loop.py` | 在 `_register_default_tools` 中条件注册 RCA 工具 | Task 14.2 |
| `nanobot/knowledge/intent_routing_store.py` | 新增 RCA Skill 索引和检索方法 | Task 11.1 |
| `nanobot/metrics.py` | 新增 RCA 相关 Prometheus 指标 | Task 16.1 |

## 注意事项

- 每个任务都引用了具体的需求编号，确保可追溯性
- 检查点任务（Task 4、7、13、18）确保增量验证，及早发现问题
- 所有 LLM 步骤严格遵循单轮调用模式，与现有 Nanobot 架构一致
- 安全校验是硬性要求，不可跳过或降级
- 热加载需要考虑并发安全（正在执行的 Skill 不受文件更新影响）
- Task 17.2 为可选任务，用于验证系统可扩展性
