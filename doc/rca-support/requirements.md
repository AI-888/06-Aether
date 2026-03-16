# 需求文档：根因分析（RCA）支持

## 引言

### 背景

传统基于大语言模型（LLM）的 Agent 在根因分析（RCA）中表现出强大的推理能力，但存在高延迟、高成本及隐私风险。本项目旨在在现有 Aether/Nanobot 框架基础上，构建一套基于小语言模型（SLM）的智能运维 Agent 系统，通过引入结构化的 Skill（技能）机制、RAG（检索增强生成）按需加载策略以及多步交互执行流程，在保证低延迟和低成本的前提下，实现高效的自动化故障诊断与根因分析。

### 目标

- 实现 SLM 在复杂多步骤排障任务中的有效规划与执行
- 建立与传统规则引擎及 LLM Agent 的差异化优势
- 设计兼容社区标准（如 skill-creator）的 Skill 复用机制
- 确保系统在低延迟要求下的高性能运行

### 与现有系统的关系

本功能在现有 Nanobot 框架（`nanobot/agent/`、`nanobot/skills/`、`nanobot/knowledge/`）基础上扩展，新增：
- 结构化 RCA Skill 格式（YAML）
- 分步上下文注入执行引擎
- Skill 加载与管理（支持 YAML 格式热加载）
- RCA 报告生成能力

---

## 需求

### 需求 1：结构化 RCA Skill 格式定义

**用户故事：** 作为一名运维工程师，我希望能够以结构化 **YAML** 格式定义排障 Skill，以便 SLM 能够像执行工作流一样精准执行，减少幻觉并提升可解释性。

**格式约定：** Skill 文件统一使用 **YAML** 格式（`.yaml` 后缀）。

#### 验收标准

1. WHEN 运维工程师创建 RCA Skill 时 THEN 系统 SHALL 支持包含以下顶层字段的 YAML 结构：`skill` 根节点下包含 `name`（技能名称）、`version`（版本号）、`description`（技能描述）、`type`（技能类型，如 workflow）、`input_schema`（输入参数定义）、`steps`（步骤列表）。
2. WHEN 定义 Skill 步骤时 THEN 每个步骤 SHALL 包含：`id`（步骤唯一标识）、`type`（步骤类型：llm/tool/root_cause_definition）；WHEN `type` 为 `llm` 时 SHALL 包含 `prompt`（提示词模板，支持 `{{变量名}}` 模板变量）；WHEN `type` 为 `tool` 时 SHALL 包含 `tool`（工具名称）和 `input`（工具输入参数）。
3. WHEN 定义步骤的输出时 THEN 每个步骤 SHALL 通过 `output_schema` 声明输出字段及其类型，后续步骤可通过 `input_from` 引用前置步骤的输出（格式：`<step_id>.<field_name>`）。
4. WHEN 定义步骤的流转关系时 THEN 系统 SHALL 按照 `steps` 列表中的定义顺序依次执行各步骤，无需显式指定流转字段。
5. WHEN Skill 需要根因确定步骤时 THEN 系统 SHALL 支持 `type: root_cause_definition` 类型的步骤，通过 `logic` 列表定义多条匹配规则，每条规则包含 `when`（匹配条件，键值对形式）、`root_cause`（根因描述）、`solution`（修复建议）。
6. WHEN Skill 需要生成最终结论时 THEN 系统 SHALL 支持在 `steps` 列表中定义 `type: llm` 的总结步骤，通过 `input_from` 引用前置步骤（包括 root_cause_definition 步骤）的输出，由 LLM 生成最终诊断报告。
7. WHEN 系统加载 Skill 文件时 THEN 系统 SHALL 支持热加载，无需重启服务即可生效。

#### Demo Skill YAML

以下是一个完整的 RCA Skill 示例，用于诊断 RocketMQ 问题：

```yaml
skill:
  name: rocketmq_troubleshoot
  version: 1.0

  description: Diagnose RocketMQ issues from logs

  type: workflow

  input_schema:
    log_text: string

  steps:

    - id: extract_error
      type: llm

      prompt: |
        从日志中提取关键错误信息

        {{log_text}}

        输出JSON:
        {
          "error_message":"",
          "error_module":""
        }

      output_schema:
        error_message: string
        error_module: string


    - id: classify_problem
      type: llm

      input_from:
        - extract_error.error_message

      prompt: |
        根据错误信息判断RocketMQ问题类型

        error_message: {{error_message}}

        输出JSON:
        {
          "problem_type":""
        }

      output_schema:
        problem_type: string


    - id: disk_check
      type: tool
      tool: check_disk_usage

      input:
        path: /data/rocketmq

      output_schema:
        disk_usage: number


    - id: determine_root_cause
      type: root_cause_definition

      logic:

        - when:
            problem_type: "磁盘空间不足"
            disk_usage: ">90"

          root_cause: "RocketMQ broker disk full"

          solution: |
            清理 /data/rocketmq/commitlog
            或扩容磁盘


        - when:
            problem_type: "nameserver连接异常"

          root_cause: |
            Broker cannot connect to nameserver

          solution: |
            检查 nameserver 地址
            检查端口 9876



    - id: conclusion
      type: llm

      input_from:
        - determine_root_cause.root_cause
        - determine_root_cause.solution
        - classify_problem.problem_type

      prompt: |
        根据诊断结果生成最终报告

        problem_type: {{problem_type}}
        root_cause: {{root_cause}}
        solution: {{solution}}

        输出:
        {
          "summary":"",
          "recommendation":""
        }
```

---

### 需求 2：基于 Skill 的分步交互式执行引擎（FR-01）

**用户故事：** 作为一名 SLM 推理引擎，我希望按照 Skill YAML 中定义的 `steps` 列表顺序，逐步执行排障工作流，以便在不依赖长程规划能力的前提下完成复杂的多步骤故障诊断。

#### 验收标准

1. WHEN 故障告警触发 RCA 流程时 THEN 系统 SHALL 通过 RAG 检索匹配当前故障场景的 Top-1 Skill，加载该 Skill 的 `input_schema` 绑定输入参数，并从 `steps` 列表的第一个步骤开始执行。
2. WHEN 当前步骤 `type` 为 `llm` 时 THEN 系统 SHALL 执行以下流程：
   - 若该步骤定义了 `input_from`，则从前置步骤输出中提取对应字段（格式：`<step_id>.<field_name>`）注入上下文
   - 将 `prompt` 模板中的 `{{变量名}}` 替换为实际数据（来自 `input_schema` 的外部输入或 `input_from` 引用的前置步骤输出）
   - 将替换后的 prompt 提交给 SLM 推理
   - 解析 SLM 返回结果，校验其是否符合 `output_schema` 定义的字段和类型
   - 将校验通过的结果存入步骤输出上下文，供后续步骤通过 `input_from` 引用
3. WHEN 当前步骤 `type` 为 `tool` 时 THEN 系统 SHALL 调用 `tool` 字段指定的外部工具，传入 `input` 中定义的参数，收集返回结果并按 `output_schema` 存入步骤输出上下文。
4. WHEN 当前步骤 `type` 为 `root_cause_definition` 时 THEN 系统 SHALL 遍历 `logic` 列表，将每条规则的 `when` 条件（键值对形式，支持比较运算符如 `">90"`）与前置步骤的输出进行匹配，命中的规则 SHALL 将其 `root_cause` 和 `solution` 作为该步骤的输出。
5. WHEN 当前步骤执行完成时 THEN 系统 SHALL 自动按 `steps` 列表顺序流转到下一个步骤，继续执行工作流。
6. WHEN `steps` 列表中的最后一个步骤（如 `conclusion`）执行完成时 THEN 系统 SHALL 认定工作流执行完毕，汇总全部步骤的执行轨迹和输出，生成结构化的 RCA 报告。
7. IF 某步骤执行超时或失败 THEN 系统 SHALL 记录失败信息（包含步骤 `id`、失败原因、已执行的上下文快照），并根据错误处理策略决定是重试、跳过还是终止流程。
8. WHEN 执行 `type: llm` 的步骤时 THEN 系统 SHALL 仅注入当前步骤的 `prompt` 和通过 `input_from` 引用的必要前置输出（禁止一次性加载整个 Skill 文档的所有步骤到 SLM 上下文中），以最小化 Token 消耗。

---

### 需求 3：Skill 加载与管理（FR-02）

**用户故事：** 作为一名平台维护者，我希望系统能够加载和管理已有的结构化 Skill 文件（YAML 格式），以便 SLM 状态机引擎直接执行。Skill 的编译过程（如从 Markdown 转换为结构化格式）属于离线处理，不在本项目范围内。

#### 验收标准

1. WHEN 系统启动或检测到 Skill 目录变更时 THEN 系统 SHALL 从指定目录加载 **YAML 格式** 的 Skill 文件。
2. WHEN 加载 Skill 文件时 THEN 系统 SHALL 对文件进行**格式校验**（必需字段完整性、步骤结构合法性等），校验失败的文件 SHALL 跳过并记录错误日志。
3. WHEN 新增或更新 Skill 文件时 THEN 系统 SHALL 支持**热加载**，无需重启服务即可生效。
4. WHEN 用户查询 Skill 列表时 THEN 系统 SHALL 提供查询接口，支持按 `skill_id`、`name`、`applicable_scenarios` 等条件检索。
5. WHEN Skill 文件加载成功时 THEN 系统 SHALL 自动将其注册到 RAG 向量库，供后续检索匹配使用。

#### 不在范围内

- Markdown → YAML 的编译功能（离线处理，与本项目无关）
- 在线 LLM 解析 Markdown 的降级方案

---

### 需求 4：根因分析能力与多 Agent 协作（FR-03）

**用户故事：** 作为一名运维平台，我希望通过多个专用 SLM Agent 协作分析不同维度的数据（日志、指标、链路），以便实现比单一 LLM 更高效、更确定性的根因归因。

#### 验收标准

1. WHEN 触发根因分析时 THEN 路由控制器 SHALL 根据故障类型调度对应的专用分析 Agent（如：日志分析 Agent、指标分析 Agent）。
2. WHEN 各专用 Agent 完成分析后 THEN 系统 SHALL 汇聚各 Agent 的分析结论，生成统一的根因分析报告，报告中 SHALL 包含：故障摘要、根因判断、置信度、执行步骤轨迹、建议修复操作。
3. WHEN 系统集成 eBPF 数据源时 THEN 系统 SHALL 能够获取内核级信号（如 CPU 节流、锁竞争），并将其作为分析维度注入 Skill 执行上下文。
4. IF 根因分析基于 Skill 中的专家知识图谱 THEN 系统 SHALL 输出可解释的推理链路（"验证"而非"猜测"），每个结论 SHALL 对应具体的执行步骤和观测数据。
5. WHEN 生成 RCA 报告时 THEN 系统 SHALL 支持将报告以结构化 JSON 和人类可读 Markdown 两种格式输出。

---

### 需求 5：性能与低延迟保障（FR-04）

**用户故事：** 作为一名运维平台，我希望系统在执行根因分析时保持低延迟，以便满足生产环境的实时响应需求。

#### 验收标准

1. WHEN 执行分步上下文注入时 THEN 系统 SHALL 仅注入当前步骤的 Instruction 和必要的历史摘要，预计减少 80% 以上的上下文 Token 消耗（相比一次性加载整个 Skill）。
2. WHEN 系统进行 Skill 检索时 THEN RAG 预筛选 SHALL 在 SLM 介入前通过向量检索快速锁定 Top-1 Skill，避免 SLM 进行无效的广泛搜索。
3. IF 系统部署 SLM 时 THEN 系统 SHALL 支持接入量化模型（如 BitNet b1.58 或 GGUF 格式量化模型），以实现在有限硬件资源上的高效推理。
4. WHEN 系统运行时 THEN Skill 热加载 SHALL 在不重启服务的情况下完成，加载延迟 SHALL 不影响正在执行的 RCA 任务。

---

### 需求 6：安全性与审计（NFR）

**用户故事：** 作为一名安全合规负责人，我希望所有由 SLM 生成的执行命令都经过安全校验，并保留完整的审计日志，以便防止危险操作并满足合规要求。

#### 验收标准

1. WHEN SLM 生成执行命令时 THEN 系统 SHALL 在实际执行前对命令进行沙箱校验或白名单过滤，拒绝执行不在白名单中的危险命令（如 `rm -rf`、`shutdown` 等）。
2. WHEN Skill 执行过程中的每个步骤完成时 THEN 系统 SHALL 记录包含以下信息的审计日志：时间戳、步骤 ID、执行命令、执行结果、SLM 推理输入/输出。
3. IF 命令被安全策略拒绝 THEN 系统 SHALL 记录拒绝原因并通知操作人员，同时终止当前 RCA 流程。
4. WHEN 系统运行时 THEN 所有 Skill 执行轨迹 SHALL 可追溯，支持事后审计与回放。

---

### 需求 7：可扩展性（NFR）

**用户故事：** 作为一名平台开发者，我希望系统支持插件化接入新的数据源和 Skill，以便在不修改核心代码的情况下扩展系统能力。

#### 验收标准

1. WHEN 新增 Skill 文件到指定目录时 THEN 系统 SHALL 自动热加载该 Skill，无需重启服务。
2. WHEN 接入新的监控数据源（如新的 Metrics/Logs/Traces 中间件）时 THEN 系统 SHALL 通过插件化接口完成集成，不需要修改 RCA 核心执行引擎代码。
3. WHEN 系统扩展新的 Skill 场景时 THEN 新增 Skill SHALL 不影响现有 Skill 的正常执行。

---

## 数据流设计

```
故障告警
  │
  ▼
RAG 检索器 ──→ 返回候选 Skill 列表
  │
  ▼
路由控制器 ──→ 选中最佳 Skill
  │
  ▼
Skill 解析器 ──→ 提取 Step 1 指令
  │
  ▼
SLM 推理引擎 ──→ 输出 Action
  │
  ▼
执行器/工具调用 ──→ 执行结果
  │
  ▼
结果判断
  ├── 成功/需下一步 ──→ 更新上下文: 注入 Step N+1 ──→ 返回 SLM 推理引擎
  └── 完成 ──→ 生成 RCA 报告

外部生态:
Community Skills (Markdown) ──[离线编译]──→ 结构化 Skill 库 (YAML)
```

---

## 附录：关键术语表

| 术语 | 说明 |
|------|------|
| SLM | Small Language Model，参数量较小（通常 <10B），经量化处理，专用于特定任务的高效模型 |
| Skill | 封装了特定运维场景排障逻辑的可执行单元，本系统中特指结构化 YAML 格式 |
| RCA | Root Cause Analysis，根因分析 |
| BitNet | 微软提出的 1-bit 神经网络架构，旨在极致压缩模型体积并提升推理速度 |
| eBPF | 一种在内核中运行沙盒程序的技术，用于高效、安全地采集系统底层数据 |
| RAG | Retrieval-Augmented Generation，检索增强生成 |
| EARS | Easy Approach to Requirements Syntax，简易需求语法格式 |
