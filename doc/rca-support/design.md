# 技术设计文档：根因分析（RCA）支持

## 文档信息

| 项目 | 内容 |
|------|------|
| 项目名称 | Aether/Nanobot RCA 支持 |
| 版本 | 1.0 |
| 创建日期 | 2026-03-18 |
| 状态 | 草稿 |
| 依赖需求文档 | [requirements.md](requirements.md) |

---

## 1. 概述

### 1.1 设计目标

在现有 Nanobot 框架的 Agent 单轮交互架构基础上，构建一套基于 SLM 的结构化 RCA（根因分析）执行引擎。核心思路是：**将复杂的多步骤排障逻辑编码到 YAML Skill 中，由引擎按步骤驱动 SLM 执行**，而非依赖 SLM 自身的长程规划能力。

### 1.2 核心设计原则

1. **Skill 驱动执行**：排障流程由结构化 YAML Skill 定义，引擎按步骤调度，SLM 仅负责单步推理
2. **最小上下文注入**：每步仅注入当前 prompt + 前置输出引用，避免 Token 浪费
3. **单轮 SLM 调用**：与现有框架保持一致，每个 LLM 步骤为一次独立的单轮调用
4. **插件化扩展**：新增 Skill/数据源不修改核心引擎代码
5. **安全第一**：所有生成的命令经白名单校验后才执行

### 1.3 与现有系统的关系

```
现有 Nanobot 框架                         新增 RCA 模块
┌────────────────────────┐              ┌──────────────────────────┐
│  AgentLoop             │              │  RCA Engine              │
│  ├── ContextBuilder    │              │  ├── SkillParser         │
│  ├── ToolRegistry      │◄─── 复用 ───│  ├── StepExecutor        │
│  ├── SubagentManager   │              │  ├── RootCauseEvaluator  │
│  └── SessionManager    │              │  └── ReportGenerator     │
├────────────────────────┤              ├──────────────────────────┤
│  Skills (Markdown)     │              │  RCA Skills (YAML)       │
│  ├── SkillsLoader      │◄─── 扩展 ───│  ├── RCASkillLoader      │
│  └── SKILL*.md         │              │  └── *.yaml              │
├────────────────────────┤              ├──────────────────────────┤
│  Knowledge (RAG)       │              │  Skill RAG 索引          │
│  ├── ChromaKnowledgeStore│◄── 复用 ──│  └── Skill 向量检索      │
│  └── IntentRoutingStore│              │                          │
├────────────────────────┤              ├──────────────────────────┤
│  Providers (LiteLLM)   │◄─── 复用 ───│  SLM 推理调用            │
│  └── LLMProvider       │              │                          │
└────────────────────────┘              └──────────────────────────┘
```

---

## 2. 系统架构

### 2.1 整体架构图

```
                          ┌──────────────┐
                          │  故障告警输入  │
                          └──────┬───────┘
                                 │
                                 ▼
                    ┌────────────────────────┐
                    │   RCA 路由控制器        │
                    │   (RCARouter)          │
                    │   - 故障类型识别        │
                    │   - Agent 调度          │
                    └────────────┬───────────┘
                                 │
                    ┌────────────▼───────────┐
                    │   Skill 检索 (RAG)      │
                    │   - 向量检索 Top-1 Skill │
                    │   - 复用 IntentRoutingStore │
                    └────────────┬───────────┘
                                 │
                    ┌────────────▼───────────┐
                    │   RCA 执行引擎          │
                    │   (RCAEngine)          │
                    │                        │
                    │   ┌──────────────────┐  │
                    │   │  Skill 解析器     │  │
                    │   │  (SkillParser)   │  │
                    │   └────────┬─────────┘  │
                    │            │             │
                    │   ┌────────▼─────────┐  │
                    │   │  步骤执行器       │  │
                    │   │  (StepExecutor)  │  │
                    │   │  ┌─────────────┐ │  │
                    │   │  │ LLM Step    │ │  │
                    │   │  │ Tool Step   │ │  │
                    │   │  │ RCD Step    │ │  │
                    │   │  └─────────────┘ │  │
                    │   └────────┬─────────┘  │
                    │            │             │
                    │   ┌────────▼─────────┐  │
                    │   │  安全校验层       │  │
                    │   │  (SecurityGuard) │  │
                    │   └────────┬─────────┘  │
                    │            │             │
                    │   ┌────────▼─────────┐  │
                    │   │  报告生成器       │  │
                    │   │  (ReportGenerator)│  │
                    │   └──────────────────┘  │
                    └─────────────────────────┘
                                 │
                    ┌────────────▼───────────┐
                    │   输出: RCA 报告        │
                    │   - JSON 结构化格式     │
                    │   - Markdown 可读格式   │
                    └────────────────────────┘
```

### 2.2 模块职责一览

| 模块 | 职责 | 新增/复用 |
|------|------|-----------|
| `RCARouter` | 根据故障类型路由到对应 Skill 或专用 Agent | 新增 |
| `RCASkillLoader` | 加载、校验、热更新 YAML Skill 文件 | 新增 |
| `SkillParser` | 解析 YAML Skill 为可执行步骤序列 | 新增 |
| `StepExecutor` | 按步骤类型（llm/tool/root_cause_definition）执行 | 新增 |
| `RCAEngine` | 编排整体 RCA 执行流程，管理步骤上下文 | 新增 |
| `SecurityGuard` | 命令白名单校验、沙箱检查 | 新增 |
| `ReportGenerator` | 生成 JSON / Markdown 格式 RCA 报告 | 新增 |
| `AuditLogger` | 记录执行轨迹与审计日志 | 新增 |
| `ToolRegistry` | 注册和执行工具调用 | 复用 |
| `LLMProvider` | SLM 推理调用 | 复用 |
| `IntentRoutingStore` | Skill 向量检索 | 复用扩展 |
| `ChromaKnowledgeStore` | 知识库存储与检索 | 复用 |

---

## 3. 详细设计

### 3.1 结构化 RCA Skill 格式（对应需求 1）

#### 3.1.1 Skill YAML Schema 定义

```python
# nanobot/rca/schema.py

from dataclasses import dataclass, field
from typing import Any
from enum import Enum


class StepType(str, Enum):
    """Skill 步骤类型枚举"""
    LLM = "llm"
    TOOL = "tool"
    ROOT_CAUSE_DEFINITION = "root_cause_definition"


@dataclass
class OutputSchema:
    """步骤输出字段定义"""
    fields: dict[str, str]  # {field_name: type_string}


@dataclass
class RootCauseRule:
    """根因匹配规则"""
    when: dict[str, str]       # 匹配条件，支持比较运算符
    root_cause: str            # 根因描述
    solution: str              # 修复建议


@dataclass
class SkillStep:
    """Skill 执行步骤"""
    id: str                                        # 步骤唯一标识
    type: StepType                                 # 步骤类型
    prompt: str | None = None                      # LLM 提示词模板（type=llm）
    tool: str | None = None                        # 工具名称（type=tool）
    input: dict[str, Any] | None = None            # 工具输入参数（type=tool）
    input_from: list[str] | None = None            # 前置步骤输出引用
    output_schema: OutputSchema | None = None       # 输出字段声明
    logic: list[RootCauseRule] | None = None        # 根因规则（type=root_cause_definition）


@dataclass
class RCASkill:
    """完整的 RCA Skill 定义"""
    name: str                          # 技能名称
    version: str                       # 版本号
    description: str                   # 技能描述
    type: str                          # 技能类型（workflow）
    input_schema: dict[str, str]       # 输入参数定义
    steps: list[SkillStep]             # 步骤列表

    # 运行时元数据（非 YAML 字段）
    file_path: str | None = None       # 源文件路径
    loaded_at: str | None = None       # 加载时间
```

#### 3.1.2 校验规则

| 校验项 | 规则 | 错误级别 |
|--------|------|----------|
| 顶层字段完整性 | `name`, `version`, `description`, `type`, `steps` 必需 | ERROR |
| 步骤 ID 唯一性 | 所有步骤的 `id` 不能重复 | ERROR |
| 步骤类型合法性 | `type` 必须为 `llm` / `tool` / `root_cause_definition` 之一 | ERROR |
| LLM 步骤必需字段 | `type=llm` 时必须包含 `prompt` | ERROR |
| Tool 步骤必需字段 | `type=tool` 时必须包含 `tool` | ERROR |
| RCD 步骤必需字段 | `type=root_cause_definition` 时必须包含 `logic` | ERROR |
| `input_from` 引用有效性 | 引用格式 `<step_id>.<field_name>` 中的 `step_id` 必须指向已定义的前置步骤 | ERROR |
| 模板变量一致性 | `prompt` 中的 `{{变量名}}` 必须能从 `input_schema` 或 `input_from` 中解析 | WARNING |

---

### 3.2 RCA Skill 加载与管理（对应需求 3）

#### 3.2.1 RCASkillLoader 设计

```python
# nanobot/rca/loader.py

class RCASkillLoader:
    """
    RCA Skill 文件加载器
    
    职责：
    1. 从指定目录加载 YAML Skill 文件
    2. 格式校验与解析
    3. 文件变更监听与热加载
    4. 注册到 RAG 向量库
    """
    
    def __init__(self, 
                 skill_dir: Path,
                 intent_routing_store: IntentRoutingStore | None = None):
        self.skill_dir = skill_dir
        self.intent_store = intent_routing_store
        self._skills: dict[str, RCASkill] = {}   # name -> RCASkill
        self._watcher: FileWatcher | None = None
    
    def load_all(self) -> int:
        """加载目录中所有 YAML Skill 文件，返回成功数量"""
    
    def load_file(self, path: Path) -> RCASkill | None:
        """加载并校验单个 YAML 文件"""
    
    def validate(self, raw: dict) -> list[str]:
        """校验 YAML 内容，返回错误列表"""
    
    def get_skill(self, name: str) -> RCASkill | None:
        """按名称获取已加载的 Skill"""
    
    def list_skills(self) -> list[dict[str, str]]:
        """列出所有已加载 Skill 的摘要信息"""
    
    def start_watcher(self) -> None:
        """启动文件监听，实现热加载"""
    
    def stop_watcher(self) -> None:
        """停止文件监听"""
    
    def _register_to_rag(self, skill: RCASkill) -> None:
        """将 Skill 注册到 RAG 向量库供检索"""
```

#### 3.2.2 热加载机制

```
文件系统事件
    │
    ├── 新增 .yaml ──→ load_file() ──→ validate() ──→ 成功: 注册到内存 + RAG
    │                                                 失败: 记录错误日志
    ├── 修改 .yaml ──→ load_file() ──→ validate() ──→ 成功: 更新内存 + RAG
    │                                                 失败: 保留旧版本
    └── 删除 .yaml ──→ 从内存移除 + 从 RAG 移除
```

**实现方案**：使用 `watchdog` 库监听文件系统变更事件，在后台线程中处理，不阻塞主事件循环。

#### 3.2.3 Skill 目录结构

```
~/.nanobot/workspace/rca_skills/
├── rocketmq_troubleshoot.yaml
├── kubernetes_pod_crash.yaml
├── mysql_slow_query.yaml
└── redis_memory_overflow.yaml
```

---

### 3.3 分步交互式执行引擎（对应需求 2）

#### 3.3.1 RCAEngine 核心流程

```python
# nanobot/rca/engine.py

class RCAEngine:
    """
    RCA 分步执行引擎
    
    按 Skill YAML 中定义的 steps 列表顺序，逐步执行排障工作流。
    每个 LLM 步骤为独立的单轮 SLM 调用，与 Nanobot 现有架构一致。
    """
    
    def __init__(self,
                 provider: LLMProvider,
                 tool_registry: ToolRegistry,
                 security_guard: SecurityGuard,
                 audit_logger: AuditLogger,
                 model: str | None = None):
        self.provider = provider
        self.tools = tool_registry
        self.security = security_guard
        self.audit = audit_logger
        self.model = model
    
    async def execute(self, 
                      skill: RCASkill, 
                      inputs: dict[str, Any],
                      stream_callback: Callable | None = None
                      ) -> RCAReport:
        """
        执行完整的 RCA Skill 工作流
        
        Args:
            skill: 已解析的 RCA Skill 对象
            inputs: 外部输入参数（对应 input_schema）
            stream_callback: 流式回调（可选）
            
        Returns:
            RCAReport: 完整的 RCA 报告
        """
```

#### 3.3.2 执行流程时序图

```
┌──────┐  ┌──────────┐  ┌──────────────┐  ┌───────────┐  ┌──────────────┐
│Client│  │RCARouter  │  │RCAEngine     │  │StepExecutor│  │LLMProvider  │
└──┬───┘  └────┬─────┘  └──────┬───────┘  └─────┬─────┘  └──────┬──────┘
   │           │               │                 │               │
   │ 故障告警  │               │                 │               │
   │──────────>│               │                 │               │
   │           │ RAG检索Skill  │                 │               │
   │           │──────────────>│                 │               │
   │           │               │ 初始化上下文     │               │
   │           │               │────────────────>│               │
   │           │               │                 │               │
   │           │               │ ─── Step 1: LLM ───            │
   │           │               │                 │  构建prompt    │
   │           │               │                 │──────────────>│
   │           │               │                 │  SLM推理结果   │
   │           │               │                 │<──────────────│
   │           │               │                 │ 解析+校验输出  │
   │           │               │                 │               │
   │           │               │ ─── Step 2: Tool ──            │
   │           │               │                 │ 执行工具调用   │
   │           │               │                 │──────────┐    │
   │           │               │                 │<─────────┘    │
   │           │               │                 │               │
   │           │               │ ─── Step 3: RCD ───            │
   │           │               │                 │ 规则匹配       │
   │           │               │                 │──────────┐    │
   │           │               │                 │<─────────┘    │
   │           │               │                 │               │
   │           │               │ ─── Step 4: LLM (conclusion) ──│
   │           │               │                 │──────────────>│
   │           │               │                 │<──────────────│
   │           │               │                 │               │
   │           │               │ 生成RCA报告      │               │
   │           │               │<────────────────│               │
   │  返回报告  │               │                 │               │
   │<──────────│───────────────│                 │               │
```

#### 3.3.3 步骤上下文管理

```python
# nanobot/rca/context.py

class StepContext:
    """
    步骤执行上下文
    
    管理各步骤的输出数据，支持 input_from 引用解析。
    """
    
    def __init__(self, inputs: dict[str, Any]):
        self._inputs = inputs              # 外部输入 (input_schema)
        self._outputs: dict[str, dict[str, Any]] = {}  # step_id -> {field: value}
        self._traces: list[StepTrace] = []  # 执行轨迹
    
    def set_output(self, step_id: str, output: dict[str, Any]) -> None:
        """存储步骤输出"""
    
    def resolve_input_from(self, refs: list[str]) -> dict[str, Any]:
        """
        解析 input_from 引用
        
        格式: ["step_id.field_name", ...]
        返回: {field_name: value, ...}
        """
    
    def resolve_template(self, template: str, extra_vars: dict[str, Any] | None = None) -> str:
        """
        渲染 prompt 模板
        
        将 {{变量名}} 替换为实际值
        优先从 extra_vars 和 input_from 解析结果获取，
        其次从 _inputs (外部输入) 获取。
        """
    
    def add_trace(self, trace: StepTrace) -> None:
        """记录步骤执行轨迹"""
    
    def get_all_traces(self) -> list[StepTrace]:
        """获取完整执行轨迹"""


@dataclass
class StepTrace:
    """步骤执行轨迹记录"""
    step_id: str
    step_type: str
    start_time: float
    end_time: float
    input_data: dict[str, Any]
    output_data: dict[str, Any]
    status: str          # "success" | "error" | "skipped"
    error_message: str | None = None
```

#### 3.3.4 三种步骤类型的执行逻辑

**LLM 步骤执行**：

```python
async def _execute_llm_step(self, step: SkillStep, ctx: StepContext) -> dict[str, Any]:
    """
    执行 LLM 类型步骤
    
    流程:
    1. 从 input_from 解析前置步骤输出
    2. 渲染 prompt 模板（替换 {{变量名}}）
    3. 构建单轮 SLM 调用消息
    4. 调用 LLMProvider.chat()
    5. 解析 SLM 返回的 JSON 结果
    6. 校验输出是否匹配 output_schema
    7. 存入 StepContext
    """
    # 1. 解析引用
    extra_vars = {}
    if step.input_from:
        extra_vars = ctx.resolve_input_from(step.input_from)
    
    # 2. 渲染 prompt
    prompt = ctx.resolve_template(step.prompt, extra_vars)
    
    # 3. 构建消息（最小上下文，仅当前步骤）
    messages = [
        {"role": "system", "content": "你是一个运维诊断助手，请严格按照要求的JSON格式输出结果。"},
        {"role": "user", "content": prompt}
    ]
    
    # 4. 单轮 SLM 调用
    response = await self.provider.chat(
        messages=messages,
        model=self.model,
        purpose="rca_step"
    )
    
    # 5-6. 解析并校验
    output = self._parse_json_output(response.content)
    self._validate_output(output, step.output_schema)
    
    # 7. 存入上下文
    ctx.set_output(step.id, output)
    return output
```

**Tool 步骤执行**：

```python
async def _execute_tool_step(self, step: SkillStep, ctx: StepContext) -> dict[str, Any]:
    """
    执行 Tool 类型步骤
    
    流程:
    1. 安全校验（白名单检查）
    2. 通过 ToolRegistry 执行工具调用
    3. 解析返回结果
    4. 按 output_schema 存入 StepContext
    """
    # 1. 安全校验
    self.security.validate_tool_call(step.tool, step.input)
    
    # 2. 执行工具
    result = await self.tools.execute(step.tool, step.input or {})
    
    # 3-4. 解析并存储
    output = self._parse_tool_output(result, step.output_schema)
    ctx.set_output(step.id, output)
    return output
```

**Root Cause Definition 步骤执行**：

```python
async def _execute_rcd_step(self, step: SkillStep, ctx: StepContext) -> dict[str, Any]:
    """
    执行 Root Cause Definition 类型步骤
    
    流程:
    1. 遍历 logic 列表中的匹配规则
    2. 将每条规则的 when 条件与前置步骤输出进行匹配
    3. 支持比较运算符（如 ">90"）
    4. 命中规则的 root_cause 和 solution 作为输出
    """
    matched_root_cause = None
    matched_solution = None
    
    for rule in step.logic:
        if self._match_rule(rule.when, ctx):
            matched_root_cause = rule.root_cause
            matched_solution = rule.solution
            break  # 首条命中即停止
    
    output = {
        "root_cause": matched_root_cause or "未能匹配到已知根因",
        "solution": matched_solution or "建议人工介入排查"
    }
    
    ctx.set_output(step.id, output)
    return output
```

---

### 3.4 Skill RAG 检索（对应需求 2 验收标准 1）

#### 3.4.1 设计方案

复用现有 `IntentRoutingStore` 的 skills 检索能力，新增 RCA Skill 的 collection：

```python
RCA_SKILLS_COLLECTION = "rca_skills"
```

**索引内容**：将 Skill 的 `name`、`description`、`steps` 描述文本拼接后向量化存储。

**检索流程**：
1. 接收故障描述文本
2. 通过向量检索获取 Top-1 最相似的 RCA Skill
3. 从 `RCASkillLoader` 内存缓存中加载完整 Skill 对象
4. 交给 `RCAEngine` 执行

#### 3.4.2 与现有 IntentRoutingStore 的集成

```python
# 扩展 IntentRoutingStore，新增 RCA Skill 索引方法

def init_rca_skills_index(self, rca_loader: RCASkillLoader) -> int:
    """构建 RCA Skill 向量索引"""
    collection = self._get_or_create(self.skills_client, RCA_SKILLS_COLLECTION)
    
    for skill in rca_loader.list_skills():
        # 拼接可检索文本
        doc_text = f"{skill['name']}: {skill['description']}"
        # 向量化并写入
        ...
    
    return count

def search_rca_skill(self, query: str, limit: int = 1) -> list[dict]:
    """根据故障描述检索最匹配的 RCA Skill"""
    return self._query_collection(
        collection=self._get_or_create(self.skills_client, RCA_SKILLS_COLLECTION),
        query=query,
        limit=limit,
        operation="rca_skill_search"
    )
```

---

### 3.5 多 Agent 协作（对应需求 4）

#### 3.5.1 路由控制器设计

```python
# nanobot/rca/router.py

class RCARouter:
    """
    RCA 路由控制器
    
    根据故障类型调度对应的专用分析 Agent 或 Skill。
    """
    
    # 故障维度 -> Agent 映射
    AGENT_ROUTES = {
        "log": "log_analysis_agent",
        "metric": "metric_analysis_agent",
        "trace": "trace_analysis_agent",
    }
    
    def __init__(self,
                 skill_loader: RCASkillLoader,
                 intent_store: IntentRoutingStore,
                 subagent_manager: SubagentManager):
        self.skill_loader = skill_loader
        self.intent_store = intent_store
        self.subagents = subagent_manager
    
    async def route(self, fault_input: FaultInput) -> RCAReport:
        """
        路由故障到合适的处理器
        
        1. RAG 检索匹配 Skill
        2. 如果有匹配的 Skill → 使用 RCAEngine 执行
        3. 如果需要多维度分析 → 调度多个专用 Agent
        4. 汇聚结果生成统一报告
        """
```

#### 3.5.2 报告汇聚

```python
@dataclass
class RCAReport:
    """RCA 报告结构"""
    fault_summary: str              # 故障摘要
    root_cause: str                 # 根因判断
    confidence: float               # 置信度 (0.0 - 1.0)
    execution_traces: list[StepTrace]  # 执行步骤轨迹
    recommendations: list[str]      # 建议修复操作
    
    # 元数据
    skill_name: str | None = None
    skill_version: str | None = None
    start_time: float = 0
    end_time: float = 0
    
    def to_json(self) -> str:
        """输出结构化 JSON 格式"""
    
    def to_markdown(self) -> str:
        """输出人类可读 Markdown 格式"""
```

---

### 3.6 安全性与审计（对应需求 6）

#### 3.6.1 SecurityGuard 设计

```python
# nanobot/rca/security.py

class SecurityGuard:
    """
    安全校验层
    
    对所有由 SLM 生成或 Skill 定义的命令进行白名单校验。
    """
    
    # 默认白名单命令
    DEFAULT_WHITELIST = {
        "check_disk_usage",
        "check_memory",
        "check_cpu",
        "kubectl_get_pods",
        "kubectl_exec_log",
        "knowledge_search",
    }
    
    # 黑名单关键词
    BLACKLIST_PATTERNS = [
        r"rm\s+(-rf?|--recursive)",
        r"shutdown",
        r"reboot",
        r"mkfs",
        r"dd\s+if=",
        r":\(\)\{",   # fork bomb
    ]
    
    def validate_tool_call(self, tool_name: str, params: dict | None) -> None:
        """
        校验工具调用是否安全
        
        Raises:
            SecurityViolationError: 不安全的工具调用
        """
    
    def validate_command(self, command: str) -> None:
        """
        校验 shell 命令是否安全
        
        Raises:
            SecurityViolationError: 包含危险命令
        """
```

#### 3.6.2 AuditLogger 设计

```python
# nanobot/rca/audit.py

class AuditLogger:
    """
    审计日志记录器
    
    记录 RCA 执行过程中的每个步骤，支持事后审计与回放。
    """
    
    def __init__(self, log_dir: Path):
        self.log_dir = log_dir
    
    def log_step(self, 
                 session_id: str,
                 step_id: str,
                 step_type: str,
                 command: str | None,
                 input_data: dict,
                 output_data: dict,
                 status: str,
                 duration: float) -> None:
        """记录步骤执行日志"""
    
    def log_security_event(self,
                           session_id: str,
                           event_type: str,
                           details: dict) -> None:
        """记录安全事件（拒绝、告警等）"""
    
    def get_session_log(self, session_id: str) -> list[dict]:
        """获取完整会话日志，用于审计回放"""
```

---

### 3.7 性能设计（对应需求 5）

| 优化点 | 实现方案 | 预期效果 |
|--------|----------|----------|
| 最小上下文注入 | 每个 LLM 步骤仅注入 prompt + input_from 引用的必要数据 | Token 消耗减少 80%+ |
| RAG 预筛选 | 向量检索 Top-1 Skill，避免 SLM 广泛搜索 | 毫秒级 Skill 匹配 |
| Skill 内存缓存 | 加载后缓存到 `_skills` 字典，避免重复 IO | 零延迟 Skill 获取 |
| 热加载隔离 | 后台线程处理文件变更，不阻塞执行中的 RCA 任务 | 零停机更新 |
| 模型量化支持 | 通过 LiteLLM 接入量化模型（GGUF/Ollama） | 硬件资源占用降低 |

---

## 4. 新增代码目录结构

```
nanobot/
├── rca/                          # 新增 RCA 模块
│   ├── __init__.py
│   ├── schema.py                 # Skill 数据结构定义
│   ├── loader.py                 # RCA Skill 加载器
│   ├── parser.py                 # Skill YAML 解析与校验
│   ├── engine.py                 # RCA 分步执行引擎
│   ├── context.py                # 步骤上下文管理
│   ├── router.py                 # RCA 路由控制器
│   ├── security.py               # 安全校验层
│   ├── audit.py                  # 审计日志
│   ├── report.py                 # 报告生成器
│   └── evaluator.py              # 根因规则匹配器
├── agent/
│   └── tools/
│       └── rca_trigger.py        # 新增: RCA 触发工具（注册到 ToolRegistry）
└── knowledge/
    └── intent_routing_store.py   # 扩展: 新增 RCA Skill 索引方法
```

---

## 5. 配置扩展

### 5.1 新增配置项

```python
# nanobot/config/schema.py 扩展

class RCAConfig(BaseModel):
    """RCA 功能配置"""
    enabled: bool = False                           # 是否启用 RCA 功能
    skill_dir: str = "~/.nanobot/workspace/rca_skills"  # Skill 文件目录
    model: str = ""                                 # RCA 专用 SLM 模型（为空时使用默认模型）
    hot_reload: bool = True                         # 是否启用热加载
    max_step_timeout: int = 30                      # 单步骤超时时间（秒）
    max_total_timeout: int = 300                    # 整体超时时间（秒）
    security_whitelist: list[str] = []              # 额外的工具白名单
    audit_log_dir: str = "~/.nanobot/workspace/rca_audit"  # 审计日志目录

# Config 根配置新增
class Config(BaseSettings):
    rca: RCAConfig = Field(default_factory=RCAConfig)
```

### 5.2 配置示例

```yaml
# config.yaml
rca:
  enabled: true
  skill_dir: "~/.nanobot/workspace/rca_skills"
  model: "ollama/qwen2.5:7b"
  hot_reload: true
  max_step_timeout: 30
  max_total_timeout: 300
  security_whitelist:
    - "check_rocketmq_status"
    - "check_redis_info"
  audit_log_dir: "~/.nanobot/workspace/rca_audit"
```

---

## 6. 错误处理策略

### 6.1 步骤级错误处理

| 错误类型 | 处理策略 | 说明 |
|----------|----------|------|
| LLM 调用超时 | 重试 1 次，仍失败则标记步骤为 `error` 并终止流程 | 记录超时上下文快照 |
| LLM 输出格式错误 | 重试 1 次（附加格式约束提示），仍失败则终止 | JSON 解析失败场景 |
| 工具调用失败 | 标记步骤为 `error`，终止流程 | 记录工具名和错误信息 |
| 安全校验拒绝 | 立即终止流程，通知操作人员 | 记录拒绝详情到审计日志 |
| input_from 引用缺失 | 终止流程 | 前置步骤输出不完整 |
| 根因规则未命中 | 使用默认回退输出，继续执行 | "未能匹配到已知根因" |

### 6.2 全局错误处理

```python
class RCAExecutionError(Exception):
    """RCA 执行错误基类"""
    def __init__(self, step_id: str, reason: str, context_snapshot: dict):
        self.step_id = step_id
        self.reason = reason
        self.context_snapshot = context_snapshot
```

---

## 7. 可扩展性设计（对应需求 7）

### 7.1 新增 Skill

开发者只需在 `rca_skills/` 目录下放置新的 YAML 文件，系统自动热加载，无需修改代码。

### 7.2 新增数据源

通过实现 `Tool` 基类并注册到 `ToolRegistry`，即可在 Skill 的 `type: tool` 步骤中使用：

```python
class EBPFDataTool(Tool):
    """eBPF 数据采集工具"""
    
    @property
    def name(self) -> str:
        return "ebpf_collect"
    
    # ... 实现 execute 方法
```

### 7.3 新增步骤类型（预留）

当前支持 `llm` / `tool` / `root_cause_definition` 三种步骤类型。未来可通过 `StepExecutor` 的策略模式扩展新类型，例如 `condition`（条件分支）或 `parallel`（并行执行）。

---

## 8. 附录

### 8.1 关键依赖

| 依赖 | 版本 | 用途 |
|------|------|------|
| `pyyaml` | >=6.0 | YAML 文件解析 |
| `watchdog` | >=3.0 | 文件系统监听（热加载） |
| `chromadb` | 已有 | 向量数据库（RAG） |
| `litellm` | 已有 | 多 LLM Provider 统一接入 |

### 8.2 Prometheus 指标扩展

| 指标名 | 类型 | 说明 |
|--------|------|------|
| `aether_rca_execution_duration_seconds` | Histogram | RCA 完整执行耗时 |
| `aether_rca_step_duration_seconds` | Histogram | 单步骤执行耗时（按类型标签） |
| `aether_rca_execution_total` | Counter | RCA 执行总次数（按状态标签） |
| `aether_rca_skill_match_total` | Counter | Skill 检索匹配次数 |
| `aether_rca_security_reject_total` | Counter | 安全拒绝次数 |
