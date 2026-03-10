# 需求文档

## 简介

本文档定义了将 nanobot 项目的知识库系统从基于 JSON 文件的存储改造为基于 Chroma 向量数据库的 RAG（检索增强生成）系统的需求。该系统将支持自动向量化、智能检索和增量更新，使 Agent 能够自动获取相关领域知识而无需手动传入上下文。

## 术语表

- **System**: RAG 知识库系统
- **Chroma**: 开源向量数据库，用于存储和检索向量化的知识条目
- **Embedding**: 文本向量化过程，将文本转换为高维向量表示
- **Chunk**: 文本分块，将长文本分割为较小的语义单元
- **KnowledgeItem**: 知识条目数据结构，包含领域、分类、标题、内容等字段
- **KnowledgeStore**: 知识库存储管理类
- **DomainKnowledgeManager**: 领域知识管理器
- **RAG**: 检索增强生成（Retrieval-Augmented Generation）
- **Vector_Database**: 向量数据库，存储文本的向量表示
- **Similarity_Search**: 相似度搜索，基于向量距离查找相关内容
- **Workspace**: nanobot 的工作空间目录
- **Knowledge_Version**: 知识库版本号，用于检测内容变更

## 需求

### 需求 1: Chroma 向量数据库集成

**用户故事**: 作为系统架构师，我希望使用 Chroma 向量数据库替代 JSON 文件存储，以便支持高效的语义检索。

#### 验收标准

1. THE System SHALL 使用 Chroma 作为向量数据库后端存储知识条目
2. WHEN System 启动时，THE System SHALL 初始化 Chroma 客户端并连接到本地持久化存储
3. THE System SHALL 将 Chroma 数据库文件存储在 workspace/knowledge/chroma_db 目录下
4. WHEN Chroma 数据库不存在时，THE System SHALL 自动创建新的数据库实例
5. THE System SHALL 为每个领域（domain）创建独立的 Chroma 集合（collection）

### 需求 2: 知识库初始化与版本控制

**用户故事**: 作为开发者，我希望系统在启动时智能检测知识库状态，只在必要时进行初始化或更新，以提高启动速度。

#### 验收标准

1. WHEN System 启动时，THE System SHALL 检查 Chroma 数据库是否已初始化
2. WHEN Chroma 数据库未初始化时，THE System SHALL 加载所有现有知识条目并初始化向量数据库
3. WHEN 知识库版本号发生变化时，THE System SHALL 重新初始化向量数据库
4. THE System SHALL 在 init_status.json 文件中记录每个领域的初始化状态和版本号
5. WHEN 知识库已初始化且版本号未变化时，THE System SHALL 跳过初始化过程
6. THE System SHALL 在初始化完成后输出初始化统计信息（条目数量、耗时等）

### 需求 3: 文本分块与向量化

**用户故事**: 作为系统开发者，我希望系统能够智能地将长文本分块并向量化，以提高检索质量和效率。

#### 验收标准

1. WHEN 知识条目内容超过 1000 字符时，THE System SHALL 将内容分割为多个语义块
2. THE System SHALL 使用递归字符分割策略，优先在段落、句子边界处分块
3. THE System SHALL 为每个文本块保留原始知识条目的元数据（id、domain、category、title、tags）
4. THE System SHALL 使用 Embedding 模型将文本块转换为向量表示
5. THE System SHALL 使用本地 Embedding 模型进行文本向量化
6. THE System SHALL 在向量化失败时记录错误并继续处理其他条目

### 需求 4: 语义检索功能

**用户故事**: 作为 Agent，我希望能够通过自然语言查询自动检索相关知识，而无需手动指定领域或分类。

#### 验收标准

1. WHEN Agent 发起查询时，THE System SHALL 将查询文本向量化
2. THE System SHALL 在 Chroma 数据库中执行相似度搜索
3. THE System SHALL 返回相似度最高的前 K 个知识块（默认 K=5）
4. THE System SHALL 支持按领域（domain）过滤检索结果
5. THE System SHALL 支持按分类（category）过滤检索结果
6. THE System SHALL 支持按标签（tags）过滤检索结果
7. THE System SHALL 返回每个检索结果的相似度分数
8. THE System SHALL 按相似度分数降序排列检索结果

### 需求 5: 增量更新支持

**用户故事**: 作为用户，我希望在添加新知识时系统能够自动更新向量数据库，而无需重新初始化整个知识库。

#### 验收标准

1. WHEN 新知识条目被添加时，THE System SHALL 自动将其向量化并添加到 Chroma 数据库
2. WHEN 知识条目被更新时，THE System SHALL 删除旧的向量并添加新的向量
3. WHEN 知识条目被删除时，THE System SHALL 从 Chroma 数据库中删除对应的向量
4. THE System SHALL 在增量更新时保持 Chroma 数据库的一致性
5. THE System SHALL 在增量更新失败时回滚操作并记录错误

### 需求 6: API 兼容性

**用户故事**: 作为现有代码的维护者，我希望新的 RAG 系统能够保持与现有 API 的兼容性，以最小化代码变更。

#### 验收标准

1. THE System SHALL 保持 KnowledgeStore 类的公共接口不变
2. THE System SHALL 保持 DomainKnowledgeManager 类的公共接口不变
3. THE System SHALL 在 search_knowledge 方法中集成 RAG 检索功能
4. WHEN 调用 search_knowledge 且提供 query 参数时，THE System SHALL 使用 RAG 语义检索
5. WHEN 调用 search_knowledge 且未提供 query 参数时，THE System SHALL 使用基于元数据的过滤检索

### 需求 7: 配置管理

**用户故事**: 作为系统管理员，我希望能够灵活配置 RAG 系统的参数，以适应不同的使用场景。

#### 验收标准

1. THE System SHALL 支持通过环境变量配置本地 Embedding 模型路径
2. THE System SHALL 支持通过配置文件设置文本分块大小（默认 1000 字符）
3. THE System SHALL 支持通过配置文件设置分块重叠大小（默认 200 字符）
4. THE System SHALL 支持通过配置文件设置检索结果数量（默认 5 条）
5. THE System SHALL 在配置无效时使用默认值并记录警告

### 需求 8: 错误处理与日志

**用户故事**: 作为运维人员，我希望系统能够优雅地处理错误并提供详细的日志信息，以便快速定位问题。

#### 验收标准

1. WHEN Chroma 数据库连接失败时，THE System SHALL 抛出异常并提示用户检查 Chroma 配置
2. WHEN Embedding 模型不可用时，THE System SHALL 抛出异常并提示用户检查模型配置
3. WHEN 向量化单个条目失败时，THE System SHALL 记录警告并继续处理其他条目
4. THE System SHALL 使用结构化日志记录所有关键操作（初始化、检索、更新）
5. THE System SHALL 在日志中包含操作耗时信息
6. THE System SHALL 在发生错误时提供清晰的错误消息和建议的解决方案

### 需求 9: 性能优化

**用户故事**: 作为用户，我希望系统能够快速响应查询请求，提供流畅的使用体验。

#### 验收标准

1. THE System SHALL 在启动时异步初始化 Chroma 数据库，不阻塞主线程
2. THE System SHALL 使用批量向量化减少 API 调用次数
3. THE System SHALL 缓存 Embedding 模型实例以避免重复初始化
4. WHEN 检索结果数量小于请求数量时，THE System SHALL 返回所有可用结果
5. THE System SHALL 在检索时设置合理的超时时间（默认 5 秒）

### 需求 10: 测试与验证

**用户故事**: 作为质量保证工程师，我希望系统提供完善的测试覆盖，确保功能正确性和稳定性。

#### 验收标准

1. THE System SHALL 提供单元测试覆盖所有核心功能
2. THE System SHALL 提供集成测试验证 Chroma 数据库集成
3. THE System SHALL 提供端到端测试验证完整的 RAG 工作流程
4. THE System SHALL 提供性能测试验证检索响应时间
5. THE System SHALL 提供测试工具验证向量化质量（相似文本应有高相似度）
