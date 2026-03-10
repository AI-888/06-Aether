# 设计文档：A/B/C 路由 + Tools/Skills/Knowledge 三库检索

## 1. 总体设计
在现有 Web 流程上新增“检索前置路由层”：
- A -> 知识库检索并返回（现有）。
- B -> tools 索引检索 top2 -> 系统补充上下文 -> AgentLoop。
- C -> skills 索引检索 top2 -> 系统补充上下文 -> AgentLoop。

核心原则：
- 三库隔离（不同 `chroma_dir`）。
- top2 不 rerank。
- 仅使用 ToolRegistry 已注册工具 + MCP 静态配置作为工具语料源。

## 2. 存储与目录设计
建议目录：
- 知识库：`<workspace>/knowledge/chroma_db`（现有）
- 工具库：`<workspace>/tools_index/chroma_db`（新增）
- Skills 库：`<workspace>/skills_index/chroma_db`（新增）

建议 collection：
- `knowledge_<domain>`（现有）
- `ops_tools`（新增）
- `skills_docs`（新增）

## 3. 数据模型设计
### 3.1 tools 索引文档（ops_tools）
- `id`: `tool:<name>` / `mcp:<server>:<tool>`
- `content`: 可检索自然语言描述（名称、用途、参数摘要、示例）
- `metadata`:
  - `source`: `registry_tool | mcp_static`
  - `tool_name`
  - `server_name`（MCP）
  - `schema_json`（可选）

### 3.2 skills 索引文档（skills_docs）
- `id`: `skill:<name>:<chunk_index>`
- `content`: `SKILL.md` 文本切块
- `metadata`:
  - `skill_name`
  - `source`: `workspace | builtin`
  - `path`
  - `chunk_index`

## 4. 初始化设计
## 4.1 初始化触发点
在 Web 资源初始化阶段执行（如 `initialize_webui_resources`）：
1. 初始化 tools 索引（registry + mcp static）
2. 初始化 skills 索引

## 4.2 tools 索引初始化
输入：
- `AgentLoop.tools.get_definitions()`
- `config.mcp.servers`（enabled 项）

处理：
- 转换为可检索文本 -> 写入 `ops_tools` collection
- 支持幂等更新（按 `id` upsert）

## 4.3 skills 索引初始化
输入：
- `SkillsLoader.list_skills(filter_unavailable=False)`
- `SkillsLoader.load_skill(name)`

处理：
- 去 frontmatter 后切块 -> upsert 到 `skills_docs`

## 5. 运行时路由设计
## 5.1 意图分类
`classify_user_intent` 返回 `A | B | C`。  
必须同步代码校验：
- 接受 `A/B/C`
- 非法值默认 `A`

## 5.2 A 路由（知识问答）
沿用现有 `process_qa_intent`，不进入 B/C 检索流程。

## 5.3 B 路由（运维操作）
1. 查询 `ops_tools` collection，取 top2。
2. 组装 `retrieval_context`（命中工具、参数、用途）。
3. 以系统补充上下文注入后调用 `agent_loop.process_direct`。

## 5.4 C 路由（故障排障）
1. 查询 `skills_docs` collection，取 top2。
2. 组装 `retrieval_context`（命中 skill 摘要、关键步骤）。
3. 以系统补充上下文注入后调用 `agent_loop.process_direct`。

## 6. 系统补充上下文注入方案
推荐新增参数：
- `AgentLoop.process_direct(..., extra_system_context: str | None = None)`
- 传递给 `ContextBuilder.build_messages(..., extra_system_context=...)`
- 在系统 prompt 末尾追加：
  - `# Retrieved Context`
  - B/C 对应 top2 文本

优点：
- 不污染用户原始输入。
- 可控长度、便于日志追踪。

## 7. 检索策略
- 向量召回：`top_k = 2`
- 不使用 rerank。
- 建议保留后续配置扩展位（默认关闭）。

## 8. 关键改动文件（建议）
- `nanobot/web/web.py`
  - 修复 A/B/C 校验与分支
  - 新增 B/C 检索分支
- `nanobot/knowledge/`（新增）
  - `ops_skills_store_factory.py`（或扩展现有 store_factory 支持多库）
  - `ops_skills_initializer.py`
  - `ops_skills_retriever.py`
- `nanobot/agent/loop.py`
  - `process_direct` 增加 `extra_system_context`
- `nanobot/agent/context.py`
  - `build_messages` 支持追加系统补充上下文

## 9. 日志与可观测性
建议新增日志字段：
- `intent`: A/B/C
- `retrieval_index`: knowledge / ops_tools / skills_docs
- `retrieval_topk`: 2
- `retrieval_hits`: 命中 id/title
- `extra_context_chars`: 注入长度

## 10. 失败策略
- B/C 检索失败：写警告日志，降级为无补充上下文直接进 loop。
- A 知识库失败：保持当前错误处理行为。
- 初始化失败：不阻塞服务启动，记录错误并在请求时重试初始化（可选）。
