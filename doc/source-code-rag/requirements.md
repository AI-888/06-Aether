# 源代码 RAG — 需求文档

## 引言

本功能旨在为现有系统增加 **源代码 RAG（Retrieval-Augmented Generation）** 能力。通过将业务源代码按领域（domain）进行向量化索引，并提供内置工具支持语义检索源代码片段，使 LLM 在执行任务时能够获取上下文精准的代码信息。该能力以 **Skill 的形式** 被调用，不与 RCA 流程自动集成。

### 背景

当前系统已有完整的 RAG 知识库体系（基于 ChromaDB + SentenceTransformer + TextChunker），用于存储运维知识文档（如 `rocketmq_init.py` 中的 Markdown 知识初始化）。**源代码 RAG 功能采用完全独立的技术栈和架构，与现有知识库系统互不影响**：

| 维度 | 现有知识库 | 源代码 RAG（本功能） |
|------|-----------|---------------------|
| 向量化模型 | SentenceTransformer | CodeBERT（`microsoft/codebert-base`） |
| 分块策略 | TextChunker（文本分块） | Tree-sitter（AST 语法树分块） |
| 数据库目录 | `workspace/knowledge/chroma_db/` | `workspace/knowledge/source_code_db/` |
| 初始化状态 | `workspace/knowledge/init_status.json` | `workspace/knowledge/source_code_init_status.json` |
| 代码模块 | `nanobot/knowledge/store.py` | `nanobot/knowledge/source_code/`（独立子模块） |
| 数据内容 | 运维知识文档（Markdown） | 业务源代码文件 |

关键设计原则：

- **源代码获取方式**：支持两种方式获取源代码——①本地目录（直接从 `workspace/src/` 读取）；②Git 拉取（通过配置文件指定远程仓库地址，自动 clone/pull 到 `workspace/src/` 下对应领域目录）
- **源代码目录**：`workspace/src/`，按领域组织（如 `workspace/src/领域1/`、`workspace/src/领域2/`）
- **Git 配置文件**：`workspace/src/source_repos.yaml`，用于声明各领域对应的 Git 仓库地址、分支、子目录等信息
- **向量化索引目录**：`workspace/knowledge/source_code_db/` 下，独立于现有知识库
- **初始化状态管理**：使用独立的 `source_code_init_status.json` 文件，不与现有 `init_status.json` 共享
- **使用方式**：以内置工具的形式提供源代码检索能力，供 Skill 按需调用
- **代码分块**：使用 **Tree-sitter** 进行 AST 级别的结构化分块，精确在函数/类/方法等代码单元边界处切割
- **向量化模型**：使用 **CodeBERT**（`microsoft/codebert-base`）作为代码语义向量化模型，相比通用文本嵌入模型更适合理解代码语义
- **源代码 RAG 管理**：提供一个独立的 HTML 管理页面，用于对领域源代码及 RAG 向量库进行全生命周期的可视化管理，包括领域概览、添加/删除源码、删除/重新初始化 RAG 库、Git 配置管理、操作日志等
- **前端架构**：管理页面采用与现有系统一致的纯 HTML + CSS + JavaScript 技术栈（无前端框架），HTML 模板放置在 `nanobot/web/templates/` 目录下，后端 API 定义在 `nanobot/web/web.py` 中

---

## 需求

### 需求 1：源代码扫描与解析

**用户故事：** 作为一名运维工程师，我希望系统能自动扫描 `workspace/src/` 下的各领域源代码目录，解析出代码文件的内容和元数据，以便后续进行向量化索引。

#### 验收标准

1. WHEN 系统启动或用户触发初始化 THEN 系统 SHALL 先检查是否存在 Git 配置文件（`workspace/src/source_repos.yaml`），如存在则先执行 Git 拉取流程（见需求 6），然后再自动扫描 `workspace/src/` 目录，识别所有子目录作为独立领域（domain）
2. WHEN 扫描到领域子目录 THEN 系统 SHALL 递归遍历该目录下的所有代码文件（支持 `.py`、`.java`、`.go`、`.js`、`.ts`、`.yaml`、`.yml`、`.json`、`.xml`、`.sh`、`.sql`、`.conf`、`.properties`、`.c`、`.cpp`、`.h` 等常见格式）
3. WHEN 解析代码文件 THEN 系统 SHALL 提取以下元数据：文件路径、文件名、所属领域、编程语言、文件大小
4. IF 文件大小超过 100KB THEN 系统 SHALL 记录警告日志但仍然对该文件进行初始化处理
5. IF 源代码目录 `workspace/src/` 不存在 THEN 系统 SHALL 记录警告日志并跳过源代码 RAG 初始化，不影响其他功能正常运行

### 需求 2：源代码分块与向量化

**用户故事：** 作为一名运维工程师，我希望源代码能被基于语法树智能分块并使用代码专用模型向量化存储，以便后续通过语义检索快速找到相关代码片段。

#### 验收标准

1. WHEN 代码文件被解析后 THEN 系统 SHALL 使用 **Tree-sitter** 对源代码进行 AST（抽象语法树）解析，基于语法结构在函数、类、方法等代码单元边界处进行智能分块
2. IF Tree-sitter 不支持某种编程语言（如 `.conf`、`.properties` 等非编程语言配置文件）THEN 系统 SHALL 回退到按固定行数分块的策略（默认每块 50 行，重叠 10 行）
3. WHEN 分块完成 THEN 系统 SHALL 使用 **CodeBERT**（`microsoft/codebert-base`）模型对每个代码分块进行向量化编码
4. WHEN 向量化完成 THEN 系统 SHALL 将向量数据存入**独立的** ChromaDB 数据库（路径：`workspace/knowledge/source_code_db/`），集合名称按领域命名为 `source_code_{领域名}`
5. WHEN 存储代码分块 THEN 系统 SHALL 在元数据中保留：文件路径（file_path）、领域（domain）、编程语言（language）、文件名（filename）、分块索引（chunk_index）、总分块数（total_chunks）、AST 节点类型（node_type，如 function_definition、class_definition 等）
6. WHEN 向量化过程中出现错误 THEN 系统 SHALL 记录错误日志，跳过该文件继续处理其他文件，并在最终汇总中报告失败数量

### 需求 3：初始化状态管理

**用户故事：** 作为一名运维工程师，我希望系统能记住哪些领域的源代码已经被索引过，避免每次启动都重复处理，同时支持在代码更新后重新索引。

#### 验收标准

1. WHEN 某领域的源代码索引完成 THEN 系统 SHALL 在**独立的** `source_code_init_status.json` 文件中记录该领域的初始化状态，包括：初始化时间（initialized_at）、文件数量（file_count）、分块数量（chunk_count）、领域名（domain）、来源方式（source_type：`local` 或 `git`）、Git 提交哈希（git_commit_hash，仅当来源为 git 时记录）
2. WHEN 系统启动时检测到某领域已初始化 THEN 系统 SHALL 跳过该领域的源代码索引，直接使用已有的向量数据
3. WHEN 系统启动时检测到某 Git 领域已初始化，但远程仓库有新提交（commit hash 不同）THEN 系统 SHALL 自动执行 git pull 并重新索引该领域
4. WHEN 用户调用强制重新初始化接口 THEN 系统 SHALL 清除对应领域的初始化状态和向量数据，重新执行完整的扫描、分块和向量化流程
5. IF 初始化状态文件损坏或不可读 THEN 系统 SHALL 将所有领域视为未初始化，重新执行索引

### 需求 4：内置源代码检索工具

**用户故事：** 作为一名运维工程师，我希望系统提供一个内置的独立工具，能够通过语义查询检索相关源代码片段，以便在 Skill 中按需调用来辅助排障和分析。

#### 验收标准

1. WHEN 工具被注册 THEN 系统 SHALL 提供一个名为 `search_source_code` 的内置工具，支持参数：查询文本（query）、领域名（domain，可选）、返回数量（top_k，默认 3）
2. WHEN 调用该工具 THEN 系统 SHALL 使用 **CodeBERT** 模型将查询文本编码为向量，在指定领域（或全部领域）的**独立源代码向量数据库**中执行语义相似度搜索
3. WHEN 检索到结果 THEN 系统 SHALL 返回包含以下信息的结果列表：代码片段内容、文件路径、所属领域、编程语言、相似度分数、AST 节点类型
4. IF 指定领域的源代码索引不存在 THEN 系统 SHALL 返回空列表并记录警告日志
5. WHEN 检索结果可用且 CrossEncoder 重排序模型已加载 THEN 系统 SHALL 对结果进行重排序以提升精度
6. WHEN 该工具被注册到工具注册表 THEN 系统 SHALL 将其与现有的 `kubectl_get_pods`、`kubectl_query_log` 等内置工具以相同方式注册，确保 Skill 可通过工具调用机制使用

### 需求 5：架构隔离

**用户故事：** 作为一名系统开发者，我希望源代码 RAG 的初始化、查询等全部逻辑与现有知识库系统完全分离，以便两套系统互不影响，可以独立演进和维护。

#### 验收标准

1. WHEN 源代码 RAG 模块被实现 THEN 系统 SHALL 将其放置在独立的子模块目录 `nanobot/knowledge/source_code/` 下，不修改现有 `nanobot/knowledge/store.py`、`nanobot/knowledge/rocketmq_init.py` 等已有文件
2. WHEN 源代码 RAG 初始化 ChromaDB 连接 THEN 系统 SHALL 使用独立的数据库目录 `workspace/knowledge/source_code_db/`，不与现有知识库的 `workspace/knowledge/chroma_db/` 共享同一个 ChromaDB 实例
3. WHEN 源代码 RAG 管理初始化状态 THEN 系统 SHALL 使用独立的状态文件 `workspace/knowledge/source_code_init_status.json`，不读写现有的 `workspace/knowledge/init_status.json`
4. WHEN 源代码 RAG 加载向量化模型 THEN 系统 SHALL 独立加载 CodeBERT 模型实例，不影响现有知识库使用的 SentenceTransformer 模型
5. IF 源代码 RAG 模块初始化失败（如 CodeBERT 模型下载失败、Tree-sitter 语言包缺失等）THEN 系统 SHALL 记录错误日志并优雅降级，不影响现有知识库的正常运行
6. IF 现有知识库模块初始化失败 THEN 源代码 RAG 模块 SHALL 不受影响，仍能正常工作
7. WHEN 源代码 RAG 模块对外暴露接口 THEN 系统 SHALL 通过独立的类（如 `SourceCodeRAGStore`）提供服务，不继承或依赖现有的 `ChromaKnowledgeStore` 类

### 需求 6：Git 源代码拉取

**用户故事：** 作为一名运维工程师，我希望能够通过配置文件声明各领域源代码对应的 Git 仓库地址，系统在初始化时自动拉取最新代码，以便我不需要手动下载和管理源代码文件。

#### 验收标准

1. WHEN 系统检测到 `workspace/src/source_repos.yaml` 配置文件存在 THEN 系统 SHALL 解析该配置文件，获取各领域对应的 Git 仓库信息
2. WHEN 配置文件格式正确 THEN 系统 SHALL 支持以下配置项：
   - `repo_url`（必填）：Git 仓库地址，支持 HTTPS 和 SSH 协议
   - `branch`（可选，默认 `main`）：要拉取的分支名
   - `sub_directory`（可选）：仓库中需要索引的子目录路径，不指定则索引整个仓库
   - `domain_name`（必填）：该仓库对应的领域名称，决定源代码存放在 `workspace/src/{domain_name}/` 下
3. WHEN 某领域的本地目录 `workspace/src/{domain_name}/` 尚不存在 THEN 系统 SHALL 执行 `git clone` 将远程仓库克隆到该目录
4. WHEN 某领域的本地目录已存在且为有效的 Git 仓库 THEN 系统 SHALL 执行 `git pull` 拉取最新代码
5. IF Git 拉取过程中发生错误（如网络不可达、认证失败、分支不存在等）THEN 系统 SHALL 记录错误日志，跳过该领域的拉取操作，并继续处理其他领域。若本地已有该领域的旧代码，则使用旧代码继续索引
6. IF 配置文件 `source_repos.yaml` 不存在 THEN 系统 SHALL 跳过 Git 拉取流程，仅使用 `workspace/src/` 下已有的本地目录进行扫描和索引
7. IF 配置文件格式错误或不可解析 THEN 系统 SHALL 记录错误日志并跳过 Git 拉取流程，回退到仅使用本地目录
8. WHEN 配置文件中同时存在本地目录和 Git 仓库的领域 THEN 系统 SHALL 同时支持两种方式共存——Git 拉取的领域和手动放置的领域互不影响
9. WHEN Git 拉取操作执行超时 THEN 系统 SHALL 在可配置的超时时间（默认 300 秒）后终止该操作，记录超时日志并继续处理后续领域

#### 配置文件示例

以下按使用场景分组给出 `source_repos.yaml` 的全部写法示例。所有示例可组合写入同一份配置文件中。

##### 示例 1：字段参考总览

```yaml
# workspace/src/source_repos.yaml
# ────────────────────────────────────────────────────────
# 字段说明
# ────────────────────────────────────────────────────────
# repos          (必填) 仓库配置列表
#   - domain_name    (必填) 领域名称，仅允许英文字母、数字、连字符、下划线
#                          决定源代码存放在 workspace/src/{domain_name}/
#   - repo_url       (必填) Git 仓库地址，支持 HTTPS 和 SSH 协议
#   - branch         (可选) 要拉取的分支名，默认 "main"
#   - sub_directory   (可选) 仓库中需要索引的子目录路径，不指定则索引整个仓库
# ────────────────────────────────────────────────────────
```

##### 示例 2：HTTPS 协议 + 指定分支 + 子目录

```yaml
# 场景：中间件团队的 RocketMQ 仓库，仅索引 src/main 子目录
# 克隆到：workspace/src/rocketmq/
# 索引范围：workspace/src/rocketmq/src/main/ 下的所有代码文件
repos:
  - domain_name: "rocketmq"
    repo_url: "https://git.example.com/middleware/rocketmq.git"
    branch: "master"
    sub_directory: "src/main"
```

##### 示例 3：SSH 协议 + Release 分支

```yaml
# 场景：使用 SSH 密钥认证拉取支付服务的指定发布分支
# 克隆到：workspace/src/payment-service/
# 索引范围：整个仓库（未指定 sub_directory）
repos:
  - domain_name: "payment-service"
    repo_url: "git@git.example.com:business/payment.git"
    branch: "release/v2.0"
```

##### 示例 4：全部使用默认值（最简配置）

```yaml
# 场景：只需指定领域名和仓库地址，其他全部走默认值
# branch 默认使用 main，sub_directory 默认索引整个仓库
repos:
  - domain_name: "gateway"
    repo_url: "https://git.example.com/infra/gateway.git"
```

##### 示例 5：Monorepo 拆分为多领域

```yaml
# 场景：一个大型单体仓库（monorepo）按子目录拆分为多个独立领域
# 三个 domain 共享同一个仓库地址，但各自索引不同子目录
repos:
  - domain_name: "order-service"
    repo_url: "https://git.example.com/platform/monorepo.git"
    branch: "main"
    sub_directory: "services/order"

  - domain_name: "user-service"
    repo_url: "https://git.example.com/platform/monorepo.git"
    branch: "main"
    sub_directory: "services/user"

  - domain_name: "common-lib"
    repo_url: "https://git.example.com/platform/monorepo.git"
    branch: "main"
    sub_directory: "libs/common"
```

##### 示例 6：特性分支 / Git Tag

```yaml
# 场景：索引某个特性分支上的代码（用于对比排障）
repos:
  - domain_name: "feature-new-auth"
    repo_url: "https://git.example.com/platform/auth-service.git"
    branch: "feature/new-auth-flow"

# 场景：索引某个发布 Tag 对应的代码快照
  - domain_name: "auth-v1.5.0"
    repo_url: "https://git.example.com/platform/auth-service.git"
    branch: "v1.5.0"
```

##### 示例 7：深层子目录路径

```yaml
# 场景：仓库目录层级较深，只需索引某个深层子目录
# 索引范围：仅 src/main/java/com/example/core/ 下的代码
repos:
  - domain_name: "trade-core"
    repo_url: "https://git.example.com/business/trade-platform.git"
    branch: "master"
    sub_directory: "src/main/java/com/example/core"
```

##### 示例 8：Git 仓库与本地目录共存（完整配置）

```yaml
# 场景：部分领域通过 Git 自动拉取，部分领域由用户手动放置在本地目录
#
# Git 管理的领域（声明在此配置文件中）：
#   - rocketmq, payment-service, gateway
# 本地手动管理的领域（无需在此配置，直接放到 workspace/src/ 下即可）：
#   - workspace/src/internal-scripts/   ← 手动放置的运维脚本
#   - workspace/src/custom-rules/       ← 手动放置的规则配置
#
# 两种方式互不影响，系统会同时扫描和索引所有领域

repos:
  - domain_name: "rocketmq"
    repo_url: "https://git.example.com/middleware/rocketmq.git"
    branch: "master"
    sub_directory: "src/main"

  - domain_name: "payment-service"
    repo_url: "git@git.example.com:business/payment.git"
    branch: "release/v2.0"

  - domain_name: "gateway"
    repo_url: "https://git.example.com/infra/gateway.git"
    # branch 默认 main，sub_directory 默认整个仓库
```

##### 示例 9：空配置文件 / 配置文件不存在

```yaml
# 情况 A：配置文件存在但 repos 列表为空
# 效果：跳过 Git 拉取，仅使用 workspace/src/ 下已有的本地目录
repos: []

# 情况 B：配置文件 workspace/src/source_repos.yaml 完全不存在
# 效果：系统自动跳过 Git 拉取流程，仅扫描 workspace/src/ 下已有目录
# （无需做任何操作，系统会记录 INFO 日志并正常继续）
```

##### 示例 10：包含认证信息的仓库地址

```yaml
# 场景：私有仓库需要通过 Token 认证（HTTPS 方式）
repos:
  - domain_name: "private-service"
    repo_url: "https://oauth2:glpat-xxxxxxxxxxxxxxxxxxxx@git.example.com/private/service.git"
    branch: "main"

# 场景：通过 SSH 协议 + 自定义端口访问
  - domain_name: "legacy-system"
    repo_url: "ssh://git@git.example.com:2222/legacy/system.git"
    branch: "master"
    sub_directory: "src"
```

##### 示例 11：综合生产环境完整配置

```yaml
# workspace/src/source_repos.yaml
# ────────────────────────────────────────────────────────
# 生产环境完整配置示例
# 包含多种协议、多种分支策略、monorepo 拆分、深层子目录等
# ────────────────────────────────────────────────────────

repos:
  # ── 中间件 ──
  - domain_name: "rocketmq"
    repo_url: "https://git.example.com/middleware/rocketmq.git"
    branch: "master"
    sub_directory: "src/main"

  - domain_name: "redis-proxy"
    repo_url: "https://git.example.com/middleware/redis-proxy.git"
    branch: "main"

  # ── 核心业务（SSH 协议）──
  - domain_name: "payment-service"
    repo_url: "git@git.example.com:business/payment.git"
    branch: "release/v2.0"

  - domain_name: "risk-engine"
    repo_url: "git@git.example.com:business/risk-engine.git"
    branch: "main"
    sub_directory: "engine/src"

  # ── 基础设施 ──
  - domain_name: "gateway"
    repo_url: "https://git.example.com/infra/gateway.git"

  - domain_name: "config-center"
    repo_url: "https://git.example.com/infra/config-center.git"
    branch: "main"
    sub_directory: "server/src/main/java"

  # ── Monorepo 拆分 ──
  - domain_name: "platform-order"
    repo_url: "https://git.example.com/platform/monorepo.git"
    branch: "main"
    sub_directory: "services/order"

  - domain_name: "platform-user"
    repo_url: "https://git.example.com/platform/monorepo.git"
    branch: "main"
    sub_directory: "services/user"

  - domain_name: "platform-common"
    repo_url: "https://git.example.com/platform/monorepo.git"
    branch: "main"
    sub_directory: "libs/common"

  # ── 特定版本快照（用于历史排障）──
  - domain_name: "payment-v1.8"
    repo_url: "git@git.example.com:business/payment.git"
    branch: "v1.8.3"

# ────────────────────────────────────────────────────────
# 以下本地领域无需在此配置，直接放置到 workspace/src/ 即可：
#   - workspace/src/internal-scripts/    运维脚本（手动管理）
#   - workspace/src/custom-rules/        告警规则（手动管理）
# ────────────────────────────────────────────────────────
```

### 需求 7：源代码 RAG 管理页面

**用户故事：** 作为一名运维工程师，我希望通过一个直观美观的 Web 管理页面，对已索引的源代码及其 RAG 向量库进行全生命周期的可视化管理（包括查看概览、添加/删除源码、删除/重新初始化 RAG 库、管理 Git 配置），以便在源代码变更、领域调整或数据异常时能够方便快捷地操作，保持索引数据的准确性和可控性。

#### 7.1 页面入口与整体布局

1. WHEN 用户访问 `/source-code` 路由 THEN 系统 SHALL 加载 `source_code.html` 模板页面，该页面采用与现有 `skill.html` 一致的技术栈（纯 HTML + CSS + JavaScript，无前端框架）
2. WHEN 管理页面加载完成 THEN 页面 SHALL 展示以下核心区域：
   - **顶部导航栏**：包含页面标题"源代码 RAG 管理"、返回主页链接、全局操作按钮（全部重新初始化）
   - **领域概览仪表盘**：以卡片网格形式展示所有已识别的领域及其状态概要
   - **领域详情面板**：点击某个领域卡片后展开，显示该领域的详细信息和操作区
3. WHEN 页面加载时 THEN 页面 SHALL 自动调用后端 API 获取所有领域的状态数据并渲染

#### 7.2 领域概览仪表盘

1. WHEN 领域数据加载完成 THEN 页面 SHALL 以响应式网格卡片形式展示每个领域，每张卡片包含：
   - 领域名称（domain name）
   - 状态徽章（已初始化 / 未初始化 / 初始化中 / 错误）
   - 来源类型图标（本地目录 📁 / Git 仓库 🔗）
   - 文件数量和分块数量的简要统计
   - 最后初始化时间
2. WHEN 领域状态为"已初始化" THEN 卡片 SHALL 显示绿色状态徽章和完整的统计信息
3. WHEN 领域状态为"未初始化" THEN 卡片 SHALL 显示灰色状态徽章并提示"尚未索引"
4. WHEN 领域状态为"初始化中" THEN 卡片 SHALL 显示蓝色状态徽章和旋转加载动画，并实时显示进度（如"正在处理 32/128 文件"）
5. WHEN 领域状态为"错误" THEN 卡片 SHALL 显示红色状态徽章和错误摘要信息
6. WHEN 页面顶部 THEN 页面 SHALL 显示全局统计摘要条，包含：总领域数、已初始化领域数、总文件数、总分块数

#### 7.3 领域管理操作

##### 7.3.1 添加新领域

1. WHEN 用户点击"添加领域"按钮 THEN 页面 SHALL 弹出模态对话框，包含以下表单字段：
   - 领域名称（必填，文本输入，仅允许英文字母、数字、连字符、下划线）
   - 来源类型（单选：本地目录 / Git 仓库）
   - 若选择 Git 仓库：显示仓库地址（必填）、分支名（可选，默认 main）、子目录（可选）输入框
2. WHEN 用户提交添加表单 THEN 页面 SHALL 调用后端 API 创建领域，成功后刷新仪表盘并自动开始初始化流程
3. IF 领域名称已存在 THEN 页面 SHALL 在表单中显示红色错误提示"该领域名称已存在"
4. IF 提交的表单数据不合法 THEN 页面 SHALL 进行前端校验并高亮错误字段

##### 7.3.2 删除源码

1. WHEN 用户在领域详情面板中点击"删除源码"按钮 THEN 页面 SHALL 弹出确认对话框，提供两种删除模式：
   - **删除整个领域**：删除该领域下全部源代码文件及其 RAG 索引数据
   - **按模式删除**：输入 glob 模式（如 `*.py`、`src/main/**`）仅删除匹配的文件及其对应索引
2. WHEN 用户确认删除操作 THEN 页面 SHALL 调用后端 API 执行删除，并显示带有旋转动画的"删除中..."状态
3. WHEN 删除完成 THEN 页面 SHALL 展示操作结果摘要弹窗，包含：删除文件数、清除分块数
4. IF 删除操作导致领域下无任何文件 THEN 页面 SHALL 自动从仪表盘中将该领域卡片更新为"未初始化"状态

##### 7.3.3 删除 RAG 库

1. WHEN 用户在领域详情面板中点击"删除 RAG 库"按钮 THEN 页面 SHALL 弹出确认对话框，明确提示"仅删除向量化索引数据，不删除源代码文件"
2. WHEN 用户确认 THEN 页面 SHALL 调用后端 API 删除该领域的 ChromaDB 集合和初始化状态
3. WHEN 删除完成 THEN 页面 SHALL 将该领域卡片状态更新为"未初始化"，并展示操作结果摘要

##### 7.3.4 重新初始化 RAG 库

1. WHEN 用户在领域详情面板中点击"重新初始化"按钮 THEN 页面 SHALL 弹出确认对话框，提示"将先删除现有索引，再重新执行完整的扫描、分块和向量化"
2. WHEN 用户确认 THEN 页面 SHALL 调用后端 API 触发重新初始化，领域卡片状态立即切换为"初始化中"
3. WHEN 重新初始化正在进行时 THEN 页面 SHALL 通过 WebSocket 接收实时进度信息，在领域卡片上展示进度条和当前处理状态（如"正在分块: utils.py"）
4. WHEN 重新初始化完成 THEN 页面 SHALL 自动更新领域卡片的统计信息和状态徽章，并展示操作结果摘要

##### 7.3.5 全局操作

1. WHEN 用户点击顶部导航栏的"全部重新初始化"按钮 THEN 页面 SHALL 弹出二次确认对话框，警告"此操作将重新初始化所有领域的 RAG 库，可能耗时较长"
2. WHEN 用户确认全局重新初始化 THEN 页面 SHALL 调用后端 API，所有领域卡片同时切换为"初始化中"状态，各自独立展示进度
3. IF 全局操作过程中某个领域失败 THEN 页面 SHALL 将该领域卡片标记为"错误"状态，其他领域不受影响

#### 7.4 源码文件浏览器

1. WHEN 用户在领域详情面板中切换到"文件"标签页 THEN 页面 SHALL 以树形结构展示该领域 `workspace/src/{domain}/` 下的文件目录树
2. WHEN 展示文件树 THEN 页面 SHALL 对每个文件显示：文件名、文件大小、编程语言图标、是否已被索引的状态标记
3. WHEN 用户点击某个文件节点 THEN 页面 SHALL 在右侧面板中显示该文件的代码预览（带语法高亮，只读模式，最多展示前 200 行）
4. WHEN 用户在文件浏览器中搜索 THEN 页面 SHALL 支持按文件名模糊搜索，实时过滤文件树

#### 7.5 操作日志

1. WHEN 用户在领域详情面板中切换到"日志"标签页 THEN 页面 SHALL 展示该领域的操作历史记录列表
2. WHEN 展示操作日志 THEN 页面 SHALL 对每条记录显示：操作类型（添加/删除/初始化/重新初始化）、操作时间、操作参数、操作结果（成功/失败/部分成功）、详情摘要
3. WHEN 日志记录较多 THEN 页面 SHALL 支持分页加载（每页 20 条）和按操作类型筛选

#### 7.6 实时状态通知

1. WHEN 后端有初始化进度更新 THEN 页面 SHALL 通过 WebSocket 实时接收并更新对应领域卡片的进度信息
2. WHEN 管理操作完成（成功或失败）THEN 页面 SHALL 在右下角显示 Toast 通知，成功为绿色、失败为红色、警告为橙色，通知 5 秒后自动消失
3. WHEN 页面与后端 WebSocket 连接断开 THEN 页面 SHALL 显示连接断开警告条，并自动尝试重连（最多 5 次，间隔 3 秒递增）

#### 7.7 页面视觉设计要求

1. WHEN 页面被渲染 THEN 页面 SHALL 遵循以下视觉规范：
   - 整体风格与现有 `index.html`、`skill.html` 保持一致（字体、配色、间距）
   - 使用 CSS 变量统一管理主题色（主色 #667eea、辅色 #764ba2 等）
   - 所有卡片和容器具有柔和圆角（border-radius: 12px）和微妙阴影
   - 按钮和卡片具有平滑的 hover 过渡动画（transition: 0.3s ease）
   - 使用 CSS Grid 实现响应式卡片布局，适配不同屏幕宽度
   - 模态对话框带有背景模糊遮罩（backdrop-filter: blur）和入场/退场动画
   - 进度条使用渐变色填充和条纹动画
   - 状态徽章使用与状态语义一致的颜色（绿/灰/蓝/红）
2. WHEN 用户操作时 THEN 页面 SHALL 对所有交互提供即时视觉反馈（按钮点击涟漪效果、加载骨架屏、状态过渡动画等）

### 需求 8：源代码 RAG 管理后端 API

**用户故事：** 作为一名系统开发者，我希望后端提供一套完整的 RESTful API 来支撑管理页面的所有操作，并通过 WebSocket 推送实时进度。

#### 验收标准

##### 8.1 领域概览 API

1. WHEN 前端调用 `GET /api/source-code/domains` THEN 后端 SHALL 返回所有已识别领域的列表，每个领域包含：领域名、初始化状态、来源类型、文件数、分块数、最后初始化时间、Git 信息（如有）
2. WHEN 前端调用 `GET /api/source-code/domains/{domain}` THEN 后端 SHALL 返回指定领域的详细信息，包括文件列表、索引统计、Git 配置等

##### 8.2 领域管理 API

1. WHEN 前端调用 `POST /api/source-code/domains` 并提交领域创建参数 THEN 后端 SHALL 创建领域目录和（如有）执行 Git clone，返回创建结果
2. WHEN 前端调用 `DELETE /api/source-code/domains/{domain}` THEN 后端 SHALL 删除该领域的源代码文件和 RAG 索引数据
3. WHEN 前端调用 `DELETE /api/source-code/domains/{domain}/files` 并附带 file_pattern 参数 THEN 后端 SHALL 按 glob 模式删除匹配的文件及其索引
4. WHEN 前端调用 `DELETE /api/source-code/domains/{domain}/rag` THEN 后端 SHALL 仅删除该领域的 RAG 向量库（保留源码文件）
5. WHEN 前端调用 `POST /api/source-code/domains/{domain}/reinit` THEN 后端 SHALL 触发该领域的重新初始化流程
6. WHEN 前端调用 `POST /api/source-code/reinit-all` THEN 后端 SHALL 触发所有领域的重新初始化

##### 8.3 文件浏览 API

1. WHEN 前端调用 `GET /api/source-code/domains/{domain}/files` THEN 后端 SHALL 返回该领域的文件目录树结构（JSON 格式，包含文件名、大小、类型、是否已索引）
2. WHEN 前端调用 `GET /api/source-code/domains/{domain}/files/{file_path}` THEN 后端 SHALL 返回该文件的内容（限制最大 200 行）和元数据

##### 8.4 操作日志 API

1. WHEN 前端调用 `GET /api/source-code/domains/{domain}/logs` THEN 后端 SHALL 返回该领域的操作历史记录，支持分页（page、page_size 参数）和按操作类型筛选（type 参数）
2. WHEN 任意管理 API 被调用时 THEN 后端 SHALL 自动记录操作审计日志，包括：操作类型、操作时间、目标领域、操作参数、操作结果

##### 8.5 WebSocket 实时进度

1. WHEN 初始化或重新初始化流程开始 THEN 后端 SHALL 通过 WebSocket（路径 `/ws/source-code`）推送实时进度消息，消息格式为：
   ```json
   {
     "type": "progress",
     "domain": "rocketmq",
     "stage": "chunking",
     "current": 32,
     "total": 128,
     "current_file": "broker/BrokerController.java",
     "message": "正在分块: BrokerController.java"
   }
   ```
2. WHEN 操作完成 THEN 后端 SHALL 推送完成消息：
   ```json
   {
     "type": "complete",
     "domain": "rocketmq",
     "result": {
       "file_count": 128,
       "chunk_count": 1024,
       "duration_seconds": 45.2
     }
   }
   ```
3. WHEN 操作失败 THEN 后端 SHALL 推送错误消息：
   ```json
   {
     "type": "error",
     "domain": "rocketmq",
     "error": "Tree-sitter parse error: unsupported language"
   }
   ```

##### 8.6 并发控制

1. IF 管理操作与正在进行的初始化或检索操作冲突（如正在索引某领域时尝试删除该领域的 RAG 库）THEN 后端 SHALL 返回 HTTP 409 Conflict 状态码和冲突提示信息
2. WHEN 多个管理操作并发请求同一领域时 THEN 后端 SHALL 使用领域级锁机制确保同一领域同一时刻只有一个写操作在执行