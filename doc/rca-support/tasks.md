# 实现计划：根因分析（RCA）支持

## 概述

本实现计划基于优化后的 SLM + Agent 架构，构建确定性运维执行框架。核心变更：
- Skill 分为 **Atomic Skill** 和 **SOP Skill** 两类
- 意图识别简化为 **A/D 两级**，执行采用**两阶段模式**（规则匹配优先 → LLM 分类备用）
- LLM 仅用于分类和总结，**不参与根因推理和步骤生成**
- 步骤类型为 `skill`/`llm`/`tool`/`root_cause_definition` 四种（当前版本不支持 `python` 代码执行）
- 数据流必须显式声明，支持 `input_from` 和 `{{stepId.field}}` 模板映射

## 前置依赖

- 现有 Nanobot Agent Loop（单轮交互模式）
- ChromaKnowledgeStore / IntentRoutingStore（RAG 向量检索）
- ToolRegistry（工具注册与执行）
- LLMProvider / LiteLLM（SLM 推理调用）

## 任务

### Phase 1：基础架构与数据模型

- [ ] 1. 安装依赖和项目配置
  - 在 pyproject.toml 中添加 `pyyaml`、`watchdog` 依赖
  - 在 `nanobot/config/schema.py` 中新增 `RCAConfig` 配置类（含 `intent_rules` 规则配置）
  - 在 `Config` 根配置中注册 `rca` 配置段
  - 创建 `nanobot/rca/` 包目录和 `__init__.py`
  - _需求: 1、4、6、9_

- [ ] 2. 实现 Skill 数据模型定义（对应需求 2）
  - [ ] 2.1 实现 `nanobot/rca/schema.py`
    - 定义 `SkillType` 枚举（`atomic` / `sop`）
    - 定义 `StepType` 枚举（`skill` / `llm` / `tool` / `root_cause_definition`）
    - 定义 `OutputSchema` 数据类
    - 定义 `RootCauseRule` 数据类
    - 定义 `SkillStep` 数据类（新增 `skill` 字段）
    - 定义 `AtomicSkill` 数据类（`output_schema` 必需）
    - 定义 `SOPSkill` 数据类（含 `steps` 列表）
    - _需求: 2.1, 2.2, 2.3_

  - [ ] 2.2 实现 YAML 解析与校验 `nanobot/rca/parser.py`
    - 实现 `parse_yaml(raw: dict) -> AtomicSkill | SOPSkill`，按 `type` 字段区分
    - Atomic Skill 校验：`output_schema` 必需且非空
    - SOP Skill 校验：步骤 ID 唯一性、步骤类型合法性
    - SOP 步骤校验：`skill` 步骤需 `skill` 字段、`llm` 步骤需 `prompt` 字段
    - 校验 `input`/`input_from` 引用有效性（引用的 step_id 必须是前置步骤）
    - 校验 `{{变量名}}` 模板变量可解析性（WARNING 级别）
    - _需求: 2.1, 2.2, 2.3_

  - [ ] 2.3 为 Schema 和 Parser 编写单元测试
    - 测试 Atomic Skill YAML 解析成功
    - 测试 SOP Skill YAML 解析成功
    - 测试 Atomic Skill 缺少 output_schema 时校验失败
    - 测试步骤类型非法时校验失败
    - 测试 `{{stepId.field}}` 引用前置步骤有效性
    - _需求: 2.1, 2.2, 2.3_

### Phase 2：意图识别与规则匹配

- [ ] 3. 实现意图分类器（对应需求 1）
  - [ ] 3.1 实现 `nanobot/rca/rule_engine.py` - RuleMatchEngine
    - 实现 `load_rules(rules_config)` 加载关键词/正则规则
    - 实现 `match(query)` 方法：匹配查询到 Skill（毫秒级响应）
    - 支持配置化新增规则，无需修改代码
    - _需求: 1.2_

  - [ ] 3.2 实现 `nanobot/rca/intent.py` - IntentClassifier
    - 实现 A/D 两级意图分类
    - A 类：知识问答 → 查询知识库回答
    - D 类：操作/排查请求 → 统一进入 Skill 执行流程
      - D 类子分类 simple（简单操作）：搜索原子 Skill，提取工具后进入 LLM loop
      - D 类子分类 complex（复杂操作/RCA分析）：搜索 SOP Skill，执行分步诊断
    - 阶段一：调用 RuleMatchEngine 进行规则匹配
    - 阶段二：调用 LLM 从预定义 Skill 列表中选择（或返回 "unsupported"）
    - LLM Classify Prompt 设计：仅输出 skill name 或 "unsupported"
    - _需求: 1.1, 1.2_

  - [ ] 3.3 为意图分类编写单元测试
    - 测试规则命中时直接返回 Skill
    - 测试规则未命中时走 LLM 分类
    - 测试 LLM 返回 "unsupported" 时正确处理
    - 测试 A 类（知识问答）意图识别
    - _需求: 1.1, 1.2_

- [ ] 4. 检查点 — 确保数据模型和意图分类测试通过
  - 运行所有 Task 1~3 相关的单元测试
  - 确保 Atomic/SOP Skill YAML 可以完整加载和校验
  - 确保规则匹配和 LLM 分类工作正常

### Phase 3：Skill 加载与管理

- [ ] 5. 实现 Skill 加载与管理（对应需求 4）
  - [ ] 5.1 实现 `nanobot/rca/loader.py` - RCASkillLoader
    - 实现 `load_all()`：扫描目录加载 YAML，按 `type` 区分 Atomic/SOP
    - 实现 `load_file(path)`：加载并校验单个文件
    - 实现 `get_atomic_skill(name)`：获取 Atomic Skill（含 output_schema）
    - 实现 `get_sop_skill(name)`：获取 SOP Skill
    - 实现 `list_skills()`：返回所有 Skill 摘要（含 type、output_schema）
    - Atomic Skill 加载时强制校验 `output_schema` 非空
    - _需求: 4.1, 4.2, 4.4, 4.6_

  - [ ] 5.2 实现文件监听热加载
    - 使用 `watchdog` 库监听文件变更
    - 新增/修改/删除事件处理
    - 热加载不影响正在执行的 RCA 任务
    - _需求: 4.3, 9.1_

  - [ ] 5.3 为 Skill 加载器编写单元测试
    - 测试 Atomic Skill 和 SOP Skill 分别加载
    - 测试 `get_atomic_skill` 返回 output_schema
    - 测试热加载功能
    - _需求: 4.1 ~ 4.6_

### Phase 4：执行引擎

- [ ] 6. 实现步骤上下文管理（对应需求 3）
  - [ ] 6.1 实现 `nanobot/rca/context.py` - StepContext
    - 实现 `set_output(step_id, output)` 存储步骤输出
    - 实现 `resolve_input_from(refs)` 解析 `input_from` 引用
    - **新增** `resolve_input_template(input_map)` 解析 `{{stepId.field}}` 模板映射
    - 实现 `resolve_template(template, extra_vars)` 渲染 prompt 模板
    - 实现 `StepTrace` 数据类
    - _需求: 3.2, 3.3, 3.4_

  - [ ] 6.2 为上下文管理编写单元测试
    - 测试 `resolve_input_template` 解析 `{{step1.pods}}`
    - 测试 `resolve_input_from` 解析 `step1.pods`
    - 测试嵌套引用和多输入场景
    - 测试引用缺失时抛出异常
    - _需求: 3.2, 3.3, 3.4_

- [ ] 7. 实现分步执行引擎（对应需求 3）
  - [ ] 7.1 实现 `nanobot/rca/engine.py` - RCAEngine
    - 初始化依赖：provider、tool_registry、security_guard、audit_logger、**skill_loader**
    - 实现 `execute(skill: SOPSkill, inputs, stream_callback)` 主方法
    - 按 `steps` 列表顺序遍历执行，记录 trace
    - _需求: 3.1, 3.6_

  - [ ] 7.2 实现 **Skill 步骤执行** `_execute_skill_step()`（新增）
    - 从 `skill_loader.get_atomic_skill(step.skill)` 获取 Atomic Skill 定义
    - 解析 `input` 模板，替换 `{{}}` 变量
    - **Atomic Skill 底层执行路径**：
      - Atomic Skill YAML 中需声明 `tool` 字段，指定底层调用的 ToolRegistry 工具名
      - 执行时通过 `ToolRegistry.execute(tool_name, resolved_input)` 调用工具
      - 即：Atomic Skill 是对 Tool 的"结构化封装"，本身不含执行逻辑
      - 执行链路：`SOP step(type:skill)` → `SkillLoader 查找 AtomicSkill` → `读取 tool 字段` → `ToolRegistry.execute()` → `按 output_schema 收集输出`
    - 按 Atomic Skill 的 `output_schema` 校验和收集输出
    - 输出存入 StepContext，供后续步骤通过 `{{stepId.field}}` 引用
    - _需求: 3.2_

  - [ ] 7.3 实现 LLM 步骤执行 `_execute_llm_step()`
    - 支持 `input` 模板映射（`{{stepId.field}}`）和 `input_from` 两种引用
    - 构建最小上下文消息，单轮 SLM 调用
    - 解析 JSON 并校验 `output_schema`
    - _需求: 3.3_

  - [ ] 7.4 实现 Tool 步骤执行 `_execute_tool_step()`
    - 安全校验 + 工具调用
    - _需求: 3.4_

  - [ ] 7.5 实现 Root Cause Definition 步骤执行 `_execute_rcd_step()`
    - 遍历 `logic` 规则列表，支持比较运算符
    - _需求: 3.5_

  - [ ] 7.6 实现错误处理
    - 各类错误场景处理：超时重试、格式错误重试、安全拒绝终止
    - 定义 `RCAExecutionError` 异常类
    - _需求: 3.6_

  - [ ] 7.7 为执行引擎编写单元测试
    - 测试完整 SOP Skill 流程：skill → llm → skill（多步骤编排）
    - 测试 Skill 步骤正确调用 Atomic Skill 并获取 output_schema 定义的输出
    - 测试 Atomic Skill 底层通过 ToolRegistry 执行工具调用
    - 测试 `{{stepId.field}}` 跨步骤数据传递
    - 测试错误处理和重试机制
    - 使用 Mock 模拟 LLMProvider、ToolRegistry、SkillLoader
    - _需求: 3.1 ~ 3.6_

- [ ] 8. 检查点 — 确保执行引擎测试通过
  - 运行所有 Task 6、7 相关的单元测试
  - 使用 Atomic + SOP Skill YAML + Mock 进行端到端验证

### Phase 5：安全、审计与报告

- [ ] 9. 实现安全校验层（对应需求 7）
  - [ ] 9.1 实现 `nanobot/rca/security.py` - SecurityGuard
    - 默认工具白名单 + 危险命令黑名单
    - _需求: 7.1, 7.3_

  - [ ] 9.2 为安全校验编写单元测试
    - 测试白名单通过/拒绝
    - 测试危险命令拒绝
    - _需求: 7.1, 7.3_

- [ ] 10. 实现审计日志（对应需求 7）
  - [ ] 10.1 实现 `nanobot/rca/audit.py` - AuditLogger
    - JSON Lines 格式持久化
    - _需求: 7.2, 7.4_

  - [ ] 10.2 为审计日志编写单元测试
    - _需求: 7.2, 7.4_

- [ ] 11. 实现 RCA 报告生成器
  - [ ] 11.1 实现 `nanobot/rca/report.py` - RCAReport + ReportGenerator
    - JSON 输出 + Markdown 输出
    - 从 StepContext 汇总生成报告
    - _需求: 相关需求_

  - [ ] 11.2 为报告生成器编写单元测试

### Phase 6：RAG 检索与路由集成

- [ ] 12. 实现 Skill RAG 检索集成
  - [ ] 12.1 扩展 IntentRoutingStore
    - 新增 RCA Skill 向量索引
    - 实现 `search_rca_skill(query)` 检索 Top-1 Skill
    - _需求: 6.4_

  - [ ] 12.2 为 RAG 检索编写单元测试

- [ ] 13. 实现 RCA 路由控制器
  - [ ] 13.1 实现完整路由流程
    - 接收请求 → IntentClassifier 分类 → 规则匹配/LLM 分类 → RCAEngine 执行
    - A 类直接 LLM 回答，D 类走 Skill 执行，unsupported 直接拒绝
    - _需求: 1.1, 1.2_

  - [ ] 13.2 为路由控制器编写单元测试

- [ ] 14. 检查点 — 确保所有模块单元测试通过

### Phase 7：系统集成

- [ ] 15. 实现系统集成
  - [ ] 15.1 实现 RCA 触发工具 `nanobot/agent/tools/rca_trigger.py`
    - _需求: 9.2_

  - [ ] 15.2 在 AgentLoop 中注册 RCA 工具
    - 初始化 SkillLoader（区分 Atomic/SOP）、RuleMatchEngine、IntentClassifier
    - _需求: 4.3, 9.1_

  - [ ] 15.3 配置与启动集成
    - 读取 `intent_rules` 配置加载规则
    - 创建 Skill 目录，加载所有 Skill 并构建 RAG 索引
    - _需求: 4.1, 9.1, 9.4_

  - [ ] 15.4 为系统集成编写单元测试

### Phase 8：端到端测试与指标

- [ ] 16. 端到端集成测试
  - [ ] 16.1 编写端到端测试
    - 测试完整流程：用户请求 → 意图分类 → 规则匹配 → Skill 执行 → 报告生成
    - 测试规则匹配路径和 LLM 分类路径
    - 测试 "unsupported" 直接拒绝路径
    - 验证 Atomic Skill output_schema 在 SOP 步骤中被正确引用
    - _需求: 1 ~ 10 全覆盖_

  - [ ] 16.2 编写性能测试
    - 测试规则匹配响应时间（< 1ms）
    - 测试 Skill 加载时间
    - _需求: 6_

  - [ ] 16.3 编写安全测试
    - 测试工具白名单校验
    - 测试危险命令拒绝
    - _需求: 7_

- [ ] 17. Prometheus 指标扩展
  - [ ] 17.1 新增 RCA 指标
    - `aether_rca_execution_duration_seconds`
    - `aether_rca_step_duration_seconds`
    - `aether_rca_execution_total`
    - `aether_rca_intent_classify_total`（区分 rule/llm 方法）
    - `aether_rca_security_reject_total`
    - _需求: 6_

  - [ ] 17.2 在 RCAEngine 中埋点指标上报

- [ ] 18. 创建示例 Skill 文件
  - [ ] 18.1 创建 Atomic Skill 示例
    - `SKILL-Atomic01-get_pods.yaml`（含 output_schema）
    - `SKILL-Atomic02-get_logs.yaml`（含 output_schema）

  - [ ] 18.2 创建 SOP Skill 示例
    - `SKILL-Sop01-check_pod_health.yaml`（编排 Atomic Skill + LLM 总结）
    - `SKILL-Sop02-analyze_logs.yaml`

- [ ] 19. 最终检查点
  - 运行所有单元测试、集成测试、端到端测试
  - 确认规则匹配 → Skill 执行 → 报告生成完整链路
  - 确认 "unsupported" 拒绝链路
  - 验证热加载功能
  - 验证审计日志完整

---

## 文件清单

### 新增文件

| 文件路径 | 说明 | 对应任务 |
|----------|------|----------|
| `nanobot/rca/__init__.py` | RCA 模块入口 | Task 1 |
| `nanobot/rca/schema.py` | Skill 数据模型（AtomicSkill + SOPSkill） | Task 2.1 |
| `nanobot/rca/parser.py` | YAML 解析与校验（区分 Atomic/SOP） | Task 2.2 |
| `nanobot/rca/rule_engine.py` | 规则匹配引擎（关键词/正则） | Task 3.1 |
| `nanobot/rca/intent.py` | 意图分类器（A/D 两级 + 两阶段） | Task 3.2 |
| `nanobot/rca/loader.py` | Skill 加载器（区分 Atomic/SOP，含热加载） | Task 5.1, 5.2 |
| `nanobot/rca/context.py` | 步骤上下文管理（含 `{{}}` 模板解析） | Task 6.1 |
| `nanobot/rca/engine.py` | 分步执行引擎（4种步骤类型） | Task 7.1 ~ 7.6 |
| `nanobot/rca/security.py` | 安全校验层（工具白名单 + 命令黑名单） | Task 9.1 |
| `nanobot/rca/audit.py` | 审计日志 | Task 10.1 |
| `nanobot/rca/report.py` | 报告生成器 | Task 11.1 |
| `nanobot/rca/evaluator.py` | 根因规则匹配器 | Task 7.6 |
| `nanobot/agent/tools/rca_trigger.py` | RCA 触发工具 | Task 15.1 |

### 修改文件

| 文件路径 | 修改内容 | 对应任务 |
|----------|----------|----------|
| `pyproject.toml` | 添加 `pyyaml`、`watchdog` 依赖 | Task 1 |
| `nanobot/config/schema.py` | 新增 `RCAConfig`（含 `intent_rules`） | Task 1 |
| `nanobot/agent/loop.py` | 注册 RCA 工具，初始化 IntentClassifier/RuleMatchEngine | Task 15.2 |
| `nanobot/knowledge/intent_routing_store.py` | 新增 RCA Skill 索引和检索方法 | Task 12.1 |
| `nanobot/web/web.py` | A/D 两级分类 + D 类子分类（simple/complex） | Task 3.2 |
| `nanobot/metrics.py` | 新增 RCA 相关 Prometheus 指标 | Task 17.1 |

---

## 关键变更对照（vs 旧版 tasks.md）

| 维度 | 旧版 | 新版 |
|------|------|------|
| Skill 类型 | 单一 `workflow` | Atomic + SOP 两类 |
| 步骤类型 | `llm/tool/root_cause_definition` | `skill/llm/tool/root_cause_definition`（当前版本不支持 `python`） |
| 意图识别 | 依赖 RAG 检索 | A/D 两级分类 + 两阶段（规则优先+LLM备用） |
| 数据引用 | 仅 `input_from` | `input_from` + `input: {{stepId.field}}` 模板映射 |
| LLM 用途 | 步骤内参与推理 | 仅分类 + 总结，不参与根因推理 |
| 新增模块 | 无 | `rule_engine.py`、`intent.py` |
| Schema | `RCASkill` 单一类 | `AtomicSkill` + `SOPSkill` 两个类 |
| 安全 | 工具白名单 | 工具白名单 + 危险命令黑名单 |
| 匹配失败 | 降级/回退 | 直接拒绝（"unsupported"） |

## 注意事项

- 所有 Atomic Skill **必须定义 `output_schema`**，且字段名长期稳定
- SOP Skill 内部数据引用必须**显式、静态、可验证**
- LLM 在整个系统中的使用被严格限定：仅 A 类问答、D 类意图分类、D 类子分类（simple/complex）、SOP 内的总结步骤
- 规则匹配是首选路径（毫秒级），LLM 分类是备用路径
- **无回退、无升级、无探索循环**：匹配失败 = 直接拒绝
- 当前版本不支持 `python` 步骤类型（代码执行），未来版本可能扩展
- 热加载需考虑并发安全（正在执行的 Skill 不受文件更新影响）
