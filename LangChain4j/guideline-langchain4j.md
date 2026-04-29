# LangChain4j 由浅入深专栏大纲

> **版本**：LangChain4j ≥ 0.35.0（以 [langchain4j/README.md](../../langchain4j/README.md) 及 BOM 版本为准）
> **面向人群**：开发、测试、运维、架构师
> **总章节**：38 章（基础篇 13 章 / 中级篇 13 章 / 高级篇 12 章）
> **每章独立成文件，字数 3000-5000 字**

---

## 专栏定位

以 LangChain4j 开源仓库源码与 [langchain4j-examples](../../langchain4j-examples) 示例为骨架，从「为什么需要统一抽象」到「ChatModel / AiServices / RAG / Tools / Agent / MCP」全链路贯通。每一章均采用 **「业务痛点 → 三人剧本对话（小胖·小白·大师）→ 代码实战 → 项目总结」** 的四段式结构，兼顾趣味性、实战性与深度。

专栏的终极目标是让 Java 团队 **不必切换到 Python 生态也能落地 LLM 应用**：从单机 Hello World 到生产级 RAG + Agent，从 Maven BOM 治理到可观测性与护栏，形成可复制的工程方法论。

---

## 章节体例（四段结构）

本专栏所有章节统一遵循 [template.md](template.md) 规范，每章包含以下四段：

| 段落 | 作用 | 内容要点 |
|------|------|----------|
| **1. 项目背景** | 引人入门 | 真实或拟真业务场景，痛点放大（性能 / 一致性 / 可维护性），可配流程图 |
| **2. 项目设计** | 三角色剧本式对话 | **小胖**（生活化比喻，开球引出话题）→ **小白**（追问原理、边界、风险、备选方案）→ **大师**（讲透选型与约束，每轮末尾输出「技术映射」）；话轮循环 2～3 次覆盖核心概念 |
| **3. 项目实战** | 动手最小闭环 | 环境准备（依赖 / BOM / 版本），分步实现（步骤目标 + 带注释代码 + 运行结果 + 坑与解法），测试验证（JUnit / curl），完整代码清单指向仓库路径 |
| **4. 项目总结** | 沉淀与推广 | 优点 & 缺点对比表，适用场景 / 不适用场景，注意事项，生产踩坑（典型故障 + 根因），进阶思考题（2 道），跨部门推广计划（开发 / 运维 / 测试协作表），检查清单 |

---

## 三角色说明

| 角色 | 性格标签 | 职责 | 代表话风 |
|------|----------|------|----------|
| **小胖** | 爱吃爱玩、不求甚解 | 用生活化比喻抛出话题，引发讨论 | “这不就跟食堂打饭排队一样吗？为啥要搞那么复杂？” |
| **小白** | 喜静、喜深入 | 追问原理、边界条件、风险、备选方案 | “那如果队头阻塞了怎么办？有没有比这更轻量的方案？” |
| **大师** | 资深技术 Leader | 讲透业务约束与选型，由浅入深打比方 | “你可以把连接池想象成银行柜台——开几个窗口既要满足客流，又不能浪费人力。” |

---

## 阅读路线建议

| 角色 | 建议阅读顺序 | 重点章节 |
|------|-------------|---------|
| 新人开发 / 测试 | 基础篇全读 → 中级篇选读 | 第 1～13 章 |
| 核心开发 / 运维 | 基础篇速读 → 中级篇精读 → 高级篇选读 | 第 14～26、27～38 章 |
| 架构师 / 资深开发 | 高级篇为主线，按需回溯中级篇 | 第 27～38 章，辅以 14～26 章 |

---

# 基础篇（第 1～13 章）

> **核心目标**：建立 LLM 集成核心概念，掌握单机 ChatModel、PromptTemplate、流式、ChatMemory 与 AiServices，形成第一个可演示闭环。
> **源码关联**：`langchain4j-core` 接口层、`langchain4j-open-ai` 适配层、`langchain4j-examples/tutorials` 教程文件。

---

## 第 1 章：Java 生态中的 LLM 集成 —— 为何需要 LangChain4j

- **定位**：专栏总览与开篇，建立「统一抽象」的心智模型。
- **核心内容**：
  - Java 团队在 LLM 落地中的典型困境（多模型 SDK 分散、胶水代码爆炸、观测不一致）
  - LangChain4j 的核心价值：Unified APIs（ChatModel / EmbeddingStore 等）与 Comprehensive Toolbox
  - 与网关、Python 中台、直连 SDK 的边界划分
  - 入门词汇表：ChatModel、ContentRetriever、Tool、AiServices、BOM
- **实战目标**：阅读 README 提炼 15 字核心定义；在本地仓库定位 `_00_HelloWorld.java`；手写一份「某场景是否需要 LLM」的决策表格。
- **源码锚点**：[`langchain4j/README.md`](../../langchain4j/README.md)、`langchain4j-aggregator`。

---

## 第 2 章：环境、依赖与 BOM —— 从 Maven 到第一个响应

- **定位**：建立可重复构建与依赖治理的企业习惯。
- **核心内容**：
  - `langchain4j-bom` 的正确用法（`import` scope vs parent POM）
  - `dependency:tree` 自检与版本冲突排查
  - Gradle `platform` / `enforcedPlatform` 对齐
  - API Key 的配置分层（环境变量 / K8s Secret / Vault）
  - 多模块应用中的 BOM 职责归属
- **实战目标**：在 `tutorials/pom.xml` 中定位 BOM import；运行 `dependency:tree` 验证无版本冲突；编写密钥读取伪代码（不做入库）。
- **源码锚点**：[`tutorials/pom.xml`](../../langchain4j-examples/tutorials/pom.xml)、`ApiKeys.java`。

---

## 第 3 章：架构鸟瞰 —— core、主模块与集成层

- **定位**：建立「分层排障」的模块地图。
- **核心内容**：
  - 模块分层：`langchain4j-core`（契约）→ `langchain4j`（组合）→ provider 实现 → HTTP 客户端
  - 各子模块职责：模型提供方、向量存储、文档解析、横切能力（Guardrails / MCP）
  - 实验模块的稳定性标注与引入原则
  - `integration-tests` 的定位（兼容矩阵回归）
- **实战目标**：用三种颜色标注 `pom.xml` 模块树；画一条请求路径（HTTPS → Controller → AiServices/ChatModel → 可选 EmbeddingStore → ChatModel）；口述 core / 主模块 / provider 三者的职责界限。
- **源码锚点**：[`langchain4j/pom.xml`](../../langchain4j/pom.xml)、`langchain4j-core`、`AiServices.java`。

---

## 第 4 章：ChatModel 初体验 —— Hello World

- **定位**：基础篇的最小闭环——密钥合法、网络可达、模型名写对（★ 深度样章）。
- **核心内容**：
  - `ChatModel` 接口与 `OpenAiChatModel.builder()` 构造
  - `model.chat(String)` 的语法糖本质（背后是 `UserMessage` → 模型 → 文本）
  - 依赖方向：业务面向 `ChatModel`（core），实现类在 provider 模块
  - 从 Hello World 到生产的差距（连接池、超时、观测）
- **实战目标**：IDE 跑通 `_00_HelloWorld`；故意填错 Key 学会读 401 错误；打 fat-jar 模拟 Classpath 地狱。
- **源码锚点**：[`_00_HelloWorld.java`](../../langchain4j-examples/tutorials/src/main/java/_00_HelloWorld.java)、`ChatModel.java`。

---

## 第 5 章：模型参数 —— 温度、超时与日志

- **定位**：把厂商 API 的「旋钮」变成可审阅的强类型配置。
- **核心内容**：
  - temperature、top_p、maxTokens、timeout 的 Builder 映射
  - temperature ≠ 事实性保证：低温度更确定但不等于更正确
  - `logRequests(true)` 的生产禁忌（PII 泄露）
  - 双层超时（客户端超时 vs 网关超时）的排查顺序
- **实战目标**：固定 prompt 在 temperature 0.0 / 0.7 各跑 3 次对比输出；把 timeout 改成 1ms 记录异常；用 `ChatModelListener` 记录延迟与 token 用量。
- **源码锚点**：[`_01_ModelParameters.java`](../../langchain4j-examples/tutorials/src/main/java/_01_ModelParameters.java)。

---

## 第 6 章：PromptTemplate 与结构化提示

- **定位**：让提示词从字符串拼接变成可 Git 管理的资产。
- **核心内容**：
  - `PromptTemplate` + Map：`{{变量}}` 占位与 `apply` 渲染
  - `@StructuredPrompt` + `StructuredPromptProcessor`：以类字段为变量的强类型风格
  - 提示注入的类比（与 SQL 注入相似但更灵活）
  - 模板的版本治理：Git（审查 + diff）vs DB（运营即时变更）
- **实战目标**：故意漏掉 Map 中的 key 观察异常；在模板顶部追加固定合规句；用外置 `.txt` + CI 校验占位符一致性。
- **源码锚点**：[`_03_PromptTemplate.java`](../../langchain4j-examples/tutorials/src/main/java/_03_PromptTemplate.java)。

---

## 第 7 章：流式输出与 Token 流

- **定位**：从同步阻塞到打字机体验，理解 TTFT。
- **核心内容**：
  - `StreamingChatModel` + `StreamingChatResponseHandler` 生命周期
  - `onPartialResponse`（展示） vs `onCompleteResponse`（持久化）的职责分离
  - `CompletableFuture.join()` 只在 main 里用的原因
  - 网关对 SSE 的三大坑：缓冲、超时、HTTP/2 流控
- **实战目标**：在 `onPartialResponse` 中统计 chunk 数；对比 buffered vs 真流式的 `curl -N` 行为；注入错误观察 `onError` 链路。
- **源码锚点**：[`_04_Streaming.java`](../../langchain4j-examples/tutorials/src/main/java/_04_Streaming.java)。

---

## 第 8 章：多模态一瞥 —— 图像模型

- **定位**：把图像生成纳入与聊天一致的 BOM 治理和接口抽象。
- **核心内容**：
  - `ImageModel` 接口与 `OpenAiImageModel.builder()`
  - 图像任务的关键差异：临时 URL 生命周期、计费 SKU、内容审核
  - CI 中 Mock `ImageModel` 的策略（不每次真调 DALL·E）
  - 异步任务队列：图像生成不应阻塞用户请求线程
- **实战目标**：同 prompt 运行两次观察 URL 变化；下载图像并计算 SHA-256 再决定是否入库；检查 Response 中的 `revised_prompt`。
- **源码锚点**：[`_02_OpenAiImageModelExamples.java`](../../langchain4j-examples/tutorials/src/main/java/_02_OpenAiImageModelExamples.java)。

---

## 第 9 章：HTTP 客户端、超时与容错

- **定位**：企业网络现实与 LLM 优雅抽象的接缝。
- **核心内容**：
  - `langchain4j-http-client-*`（JDK / Apache / OkHttp）的选择矩阵
  - 429 / 5xx 的重试策略：Retry-After + 指数退避 + 抖动 + 最大重试预算
  - 超时 vs 断路器的职责区分
  - 内网代理、TLS、MITM 证书的排查链条
- **实战目标**：建立 Javadoc 速查表（http / timeout / proxy 方法）；区分 DNS / TLS / 401 三种错误的堆栈特征；伪代码讨论 TrustStore 归属。
- **源码锚点**：`OpenAiChatModel` Builder、各 `langchain4j-http-client-*` 模块。

---

## 第 10 章：ChatMemory —— 会话如何在内存中折叠

- **定位**：多轮对话的「记性」与「健忘」都是需要设计的。
- **核心内容**：
  - `MessageWindowChatMemory`（条数窗口） vs `TokenWindowChatMemory`（token 窗口）
  - SystemMessage 被挤掉的风险与 TokenWindow 的优势
  - 单例 ChatMemory 导致的「串话」事故
  - 工具大 JSON 瞬间撑爆窗口的排查与解决
- **实战目标**：maxTokens 从 1000 改到 50 观察截断行为；超长 system + 短 user 测试边界；脱敏打印每次 `messages()` 的行数。
- **源码锚点**：[`_05_Memory.java`](../../langchain4j-examples/tutorials/src/main/java/_05_Memory.java)。

---

## 第 11 章：Few-shot —— 用示例约束输出

- **定位**：不微调模型也能控制格式与边界的「软示范」艺术。
- **核心内容**：
  - Few-shot 与系统规则的互补关系（硬约束 vs 软示范）
  - 正例与负例的选取策略：覆盖最怕的错误路径
  - 样例脱敏与合成数据（真实客户原话禁止入仓）
  - 从 Few-shot 升级到结构化输出 / 工具的决策时机
- **实战目标**：构建 `Action:` 解析型路由样例；删一半负例观察分类偏乐观；多语言样例对比 token 与效果。
- **源码锚点**：[`_06_FewShot.java`](../../langchain4j-examples/tutorials/src/main/java/_06_FewShot.java)。

---

## 第 12 章：AiServices —— 接口即「智能服务」

- **定位**：把 LLM 调用变成熟悉的 Java 接口声明，但清醒知道它不是魔法 RPC。
- **核心内容**：
  - `AiServices.builder(Assistant.class)` 的代理生成与织入机制
  - `@SystemMessage` / `@UserMessage` 的变量绑定与 `{{var}}` 映射
  - 多方法接口的提示污染风险与拆分原则
  - 与手写 `ChatModel` 的边界：简单对话 → AiServices；复杂编排 → 显式状态机
- **实战目标**：Ctrl+F `AiServices.builder` 圈出最小配置；标注「删掉 memory / tools / retriever 后系统还能工作吗」；理解共享 Bean 与 ChatMemory 串话的关联。
- **源码锚点**：[`_08_AIServiceExamples.java`](../../langchain4j-examples/tutorials/src/main/java/_08_AIServiceExamples.java)、`Assistant.java`（Spring）。

---

## 第 13 章：用户级持久记忆 —— ChatMemoryStore

- **定位**：从进程内记忆到分布式持久化，解决容灾、合规与多实例扩容。
- **核心内容**：
  - `ChatMemoryStore` 接口与 DB/Redis 适配
  - `memoryId` 的安全来源（必须来自 JWT/Session，不可客户端伪造）
  - GDPR 场景下的删除流程：DB + 向量库 + 对象存储三处清理
  - 并发写冲突的解决（乐观锁 / 单 writer 队列）
- **实战目标**：在 SecurityContext 中取 memoryId 而非 URL 参数；画拓扑图（HTTP → Filter → Service(memoryId) → AiServices → Store）；设计越权 403 用例。
- **源码锚点**：[`_09_ServiceWithPersistentMemoryForEachUserExample.java`](../../langchain4j-examples/tutorials/src/main/java/_09_ServiceWithPersistentMemoryForEachUserExample.java)。

---

# 中级篇（第 14～26 章）

> **核心目标**：掌握工具调用、结构化输出、RAG 管线（Easy → Naive → Low-level → Advanced）的架构设计与性能调优。
> **源码关联**：`langchain4j`（AiServices / Tools / ContentRetriever）、`langchain4j-examples/rag-examples`、`langchain4j-core`（EmbeddingStore / QueryTransformer）。

---

## 第 14 章：Tools —— 让模型「动手」

- **定位**：模型决策、Java 执行——用工具把模型从「嘴炮」升级为「行动者」。
- **核心内容**：
  - `@Tool` 注解 + `AiServices.tools()` 的装配链
  - tool calling 的生命周期：模型决定调用 → 解析参数 → Java 执行 → 结果交回模型
  - 工具的安全三原则：最小权限、租户白名单、每次调用审计日志
  - 错误面信息泄露防范（不返回全栈、不暴露内网 IP）
- **实战目标**：在 `@Tool` 方法中 println 观察调用时机；构造一个抛异常的工具看返回给模型的信息形态；用非法 UUID 参数验证参数校验。
- **源码锚点**：[`_10_ServiceWithToolsExample.java`](../../langchain4j-examples/tutorials/src/main/java/_10_ServiceWithToolsExample.java)、`AssistantTools.java`（Spring）。

---

## 第 15 章：动态工具注册

- **定位**：SaaS 多租户场景下的「按需暴露」——模型只看它能调用的工具。
- **核心内容**：
  - 动态 != 不安全：代码仍在 JVM，动态的只是「暴露哪些描述」
  - 分层路由策略：先暴露 2～3 个高频工具，再惰性展开
  - AB 实验：按 user/tenant hash 切工具包
  - 会话世代号：避免旧句柄指向已卸载的工具
- **实战目标**：定位运行时决定工具列表的 Builder 方法；设计 PolicyEngine 的单点修改位置；每次请求打印工具 schema 哈希用于观测。
- **源码锚点**：[`_11_ServiceWithDynamicToolsExample.java`](../../langchain4j-examples/tutorials/src/main/java/_11_ServiceWithDynamicToolsExample.java)。

---

## 第 16 章：ChatWithDocuments —— 对话 + 检索入门

- **定位**：AiServices + ContentRetriever = 一条装配路径搞定「聊天 + 知识库」。
- **核心内容**：
  - `ContentRetriever` 在 AiServices 中的装配位置
  - 检索 query ≠ 闲聊全文：会话噪声如何稀释相关性
  - 检索 vs 工具的「事实源」分界：静态文档走检索，实时库存走工具
  - topK 与会话窗口的交互
- **实战目标**：写三类问题（常识 / 必须命中文档 / 需实时状态）；标注哪些应走检索、哪些应走工具；对比 Easy RAG 的透明度差异。
- **源码锚点**：[`_12_ChatWithDocumentsExamples.java`](../../langchain4j-examples/tutorials/src/main/java/_12_ChatWithDocumentsExamples.java)。

---

## 第 17 章：结构化输出与业务类型映射

- **定位**：把模型释放的散文拉回 Java 类型系统。
- **核心内容**：
  - `PojoOutputParser`、`StringListOutputParser`、`EnumSetOutputParser`
  - 与厂商 `response_format: json_object` 的组合策略
  - 有限次重试 + 修复提示 + 降级人工队列的完整链路
  - enum 映射失败的保护：UNKNOWN 兜底 / Optional
- **实战目标**：多一行废话测试解析器脆弱性；合法性 JSON 缺字段测默认值；中英文数字混排测 locale 陷阱。
- **源码锚点**：[`service/output/` 包](../../langchain4j/langchain4j/src/main/java/dev/langchain4j/service/output/)。

---

## 第 18 章：Easy RAG —— 最快上线的检索增强

- **定位**：一周内看到「上传文档就能问」的可演示闭环（★ 深度样章）。
- **核心内容**：
  - `EmbeddingStoreIngestor` 默认管线（解析 / 切分 / 嵌入）
  - `InMemoryEmbeddingStore` 的适用边界（PoC / 单测 / 小规模）
  - `AiServices` + `ContentRetriever` + `ChatMemory` 的 RAG 形态
  - 从 Easy RAG 升级到生产 RAG 的技术债清单
- **实战目标**：加载 `documents/*.txt` 并跑通问答闭环；改为矛盾段落观察模型选边站；中文文档 + 英文提问检验跨语言检索。
- **源码锚点**：[`Easy_RAG_Example.java`](../../langchain4j-examples/rag-examples/src/main/java/_1_easy/Easy_RAG_Example.java)。

---

## 第 19 章：Naive RAG —— 拆分摄取与查询

- **定位**：打开 Easy RAG 的黑箱，显式理解经典六步。
- **核心内容**：
  - 加载 → 解析 → 切分 → 嵌入 → 写入向量库 → topK 检索 → 拼进提示
  - `DocumentSplitter`、`EmbeddingModel`、`EmbeddingStore` 的显式注入
  - Chat 走云、embed 走本地 ONNX 的解耦策略
  - 评测 RAG 必须先盯 Recall@K 再盯生成质量
- **实战目标**：纸笔画出七个方框连线；调大 chunk 大小问跨段问题；只 embed 一半文档观察胡编。
- **源码锚点**：[`Naive_RAG_Example.java`](../../langchain4j-examples/rag-examples/src/main/java/_2_naive/Naive_RAG_Example.java)。

---

## 第 20 章：低层 Naive RAG —— 掌控每一步

- **定位**：为性能剖析和合规审计打开每一道门。
- **核心内容**：
  - 逐步 API：何时解析、切分、生成 Embedding、写入 store
  - 并行 embed 的限速、重试与配额策略
  - 与 `EmbeddingStoreIngestor` 默认封装的关系（二选一文档化）
  - 幂等批处理 = 重建索引生命线
- **实战目标**：在每一步插入 `System.nanoTime()` 计时；对比同一文档两次 ingest 的 segment 数是否一致；评估 embed 耗时 > 70% 时的优化方向。
- **源码锚点**：[`_01_Low_Level_Naive_RAG_Example.java`](../../langchain4j-examples/rag-examples/src/main/java/_4_low_level/_01_Low_Level_Naive_RAG_Example.java)。

---

## 第 21 章：高级 RAG —— 查询压缩（Query Compression）

- **定位**：长邮件 / 工单场景——压缩掉噪声，保留检索意图。
- **核心内容**：
  - `CompressingQueryTransformer` 或 `QueryTransformer` 的装配点
  - 压缩 ≠ 重写：压缩去噪缩句，重写同义扩展
  - 风险治理：压缩丢掉否定词 → 规则校验 → 回退原文
  - 压缩的 ROI：Recall@k vs 额外 token vs 延迟
- **实战目标**：定位 `QueryTransformer` 在管线中的装配位置；300 字噪声 vs 50 字精准对同文档集比较召回片段 ID；测试压缩输出空 / 极短 / 超长时的降级路径。
- **源码锚点**：[`_01_Advanced_RAG_with_Query_Compression_Example.java`](../../langchain4j-examples/rag-examples/src/main/java/_3_advanced/_01_Advanced_RAG_with_Query_Compression_Example.java)。

---

## 第 22 章：高级 RAG —— 查询路由（Query Routing）

- **定位**：多知识域的企业搜索——决定去哪找，降低噪声。
- **核心内容**：
  - `QueryRouter` / `LanguageModelQueryRouter` 的决策节点
  - 路由 vs 多路融合的差别（互斥选择 vs 并行合并）
  - 从规则到 LLM 路由器的升级路径
  - 路由与元数据过滤的双重校验安全保障
- **实战目标**：列出每个路由目标对应的 `ContentRetriever`；梳理 5 个业务域的路由信号；设计低置信度时的澄清 / 安全默认策略。
- **源码锚点**：[`_02_Advanced_RAG_with_Query_Routing_Example.java`](../../langchain4j-examples/rag-examples/src/main/java/_3_advanced/_02_Advanced_RAG_with_Query_Routing_Example.java)。

---

## 第 23 章：高级 RAG —— 重排序（Re-ranking）

- **定位**：粗召回 + 精排序 = 可扩展性与精准度的工程最优解。
- **核心内容**：
  - 两阶段架构：bi-encoder 粗召回 → cross-encoder 精排
  - K（粗召数量）与 N（精排保留数）的压测定标
  - Reranker 降级策略：超时/异常 → 回退向量序
  - 与 MMR 的协同：先 MMR 去重再 cross-encoder 排相关性
- **实战目标**：搭建 `T_total ≈ T_embed + T_vector + T_rerank + T_llm` 估算模型；粗召回减半观察质量断崖；注入 rerank 超时断言回退行为。
- **源码锚点**：[`_03_Advanced_RAG_with_ReRanking_Example.java`](../../langchain4j-examples/rag-examples/src/main/java/_3_advanced/_03_Advanced_RAG_with_ReRanking_Example.java)。

---

## 第 24 章：高级 RAG —— 元数据与溯源（Metadata & Sources）

- **定位**：金融 / 医疗 / 政务场景——回答必须能审计到源文件。
- **核心内容**：
  - ingest 时写入 metadata（docId / version / tenantId）
  - 返回 sources 列表：同一批 `EmbeddingMatch` 进提示 + API 响应
  - metadata 的「指针原则」：短键 + 外联 ID，不存全文副本
  - 溯源链接失效的预案：健康检查 + 备用纯文本引用
- **实战目标**：设计至少 5 个 metadata 字段并标注谁生成 / 谁维护；端到端断言 source id 属于授权集合；链接 403 时不泄露存在性细节。
- **源码锚点**：[`_04_Advanced_RAG_with_Metadata_Example.java`](../../langchain4j-examples/rag-examples/src/main/java/_3_advanced/_04_Advanced_RAG_with_Metadata_Example.java)、[`_09_Advanced_RAG_Return_Sources_Example.java`](../../langchain4j-examples/rag-examples/src/main/java/_3_advanced/_09_Advanced_RAG_Return_Sources_Example.java)。

---

## 第 25 章：高级 RAG —— 元数据过滤（Metadata Filtering）

- **定位**：多租户安全的最后一道数据库级硬约束。
- **核心内容**：
  - `store.embedding.filter` 的可组合表达式（`And` / `Or` / `Not`）
  - 下推数据库 vs 内存过滤（成本 + 泄露风险对比）
  - filter 只能从服务端认证上下文推导（绝不从客户端 JSON）
  - SQL 字符串 filter 解析器与注入防护
- **实战目标**：圈出 `And(...)` 口述成 WHERE；改写成参数化 SQL 伪代码；固定用例矩阵（同 query 不同租户 + 篡改 header）。
- **源码锚点**：[`_05_Advanced_RAG_with_Metadata_Filtering_Examples.java`](../../langchain4j-examples/rag-examples/src/main/java/_3_advanced/_05_Advanced_RAG_with_Metadata_Filtering_Examples.java)。

---

## 第 26 章：高级 RAG —— 何时跳过检索（Skip Retrieval）

- **定位**：闲聊不走档案室——降低不必要的向量检索成本。
- **核心内容**：
  - 跳过检索 vs 查询路由：二元决策 vs 多库多选
  - 从关键词规则到轻量二分类的演进路径
  - 核心风险：漏掉表面上闲聊实则含业务的问题
  - 监控：跳过率突增 / 突降 + 满意度关联
- **实战目标**：标注 10 句中文的 skip / no-skip；构造「闲聊里藏政策号码」对抗用例；设计用户强制检索按钮。
- **源码锚点**：[`_06_Advanced_RAG_Skip_Retrieval_Example.java`](../../langchain4j-examples/rag-examples/src/main/java/_3_advanced/_06_Advanced_RAG_Skip_Retrieval_Example.java)。

---

# 高级篇（第 27～38 章）

> **核心目标**：掌握 Embedding 与数据工程、运行时集成（Spring / Quarkus）、可观测性与 Guardrails、MCP 协议、Agentic 模式、实验特性与源码贡献。
> **源码关联**：`langchain4j-core`（Embedding / ContentRetriever）、`langchain4j-spring-*` / `quarkus-langchain4j`、`langchain4j-mcp`、`langchain4j-agentic*`、`langchain4j-experimental-*`。

---

## 第 27 章：高级 RAG —— 多路检索器融合

- **定位**：企业搜索中台——向量 + ES + SQL 并行检索再合并。
- **核心内容**：
  - 多个 `ContentRetriever` 并行拉取 → 融合 / 重排 / LLM 二次筛选
  - 融合算法对比：RRF、加权归一化、拼集后 rerank
  - 部分失败的优雅降级：一路超时不等于全路瘫痪
  - 每路必须独立做租户过滤后再融合
- **实战目标**：伪代码 `CompletableFuture.allOf(...).thenApply(merge)`；关闭一路依赖看降级行为；写一页混合检索决策表。
- **源码锚点**：[`_07_Advanced_RAG_Multiple_Retrievers_Example.java`](../../langchain4j-examples/rag-examples/src/main/java/_3_advanced/_07_Advanced_RAG_Multiple_Retrievers_Example.java)。

---

## 第 28 章：联网搜索与 SQL 数据库检索（合并章）

- **定位**：混合检索的最大风险面——每路有独立的安全、合规与运维问题。
- **核心内容**：
  - `WebSearchEngine` 接入：白名单域名、二次校验、来源标注
  - SQL 检索器：参数化查询、只读账号、行级安全（RLS）
  - 事实源仲裁规则：内部 DB > 向量库 > 网络摘要
  - 联网的合规红线：爬虫 ToS、跨境数据、独立审计
- **实战目标**：SQL 注入套件测试；伪造网络结果对抗；写一页混合检索决策表（意图 → 首选源 → 失败回退）。
- **源码锚点**：[`_08_Advanced_RAG_Web_Search_Example.java`](../../langchain4j-examples/rag-examples/src/main/java/_3_advanced/_08_Advanced_RAG_Web_Search_Example.java)、[`_10_Advanced_RAG_SQL_Database_Retreiver_Example.java`](../../langchain4j-examples/rag-examples/src/main/java/_3_advanced/_10_Advanced_RAG_SQL_Database_Retreiver_Example.java)。

---

## 第 29 章：EmbeddingModel —— 向量从哪来

- **定位**：RAG 召回上限的决定因素——选错嵌入模型等于地基打歪。
- **核心内容**：
  - `EmbeddingModel` 接口与本地 ONNX / 云端 API 的抉择
  - 换模型 = 全量重建索引（蓝绿切换）
  - 量化 vs 全精度：离线 hit@k 决策
  - embedding 版本号写入 segment metadata
- **实战目标**：选两个 embed 模块比较同 query 的 top3；打印 Embedding 维度；中英各一句主观对比相似度。
- **源码锚点**：`embeddings/langchain4j-embeddings-*`、[`Naive_RAG_Example`](../../langchain4j-examples/rag-examples/src/main/java/_2_naive/Naive_RAG_Example.java)。

---

## 第 30 章：EmbeddingStore —— 抽象与相似度

- **定位**：写入与查询的枢纽——调用形态一致，运维剧本完全不同。
- **核心内容**：
  - `EmbeddingStore<TextSegment>` 的统一契约（add / findRelevant / removeAll）
  - 开发用内存库、生产换 pgvector / Milvus 的迁移策略
  - 向量维度不一致 = 启动硬失败（非软降级）
  - metadata 过滤的索引依赖（无索引 → 全表扫）
- **实战目标**：对比 InMemoryEmbeddingStore 与 pgvector 的 P99；设计 metadata 过滤对应的索引字段。
- **源码锚点**：`InMemoryEmbeddingStore`、`pgvector-example`、`milvus-example`。

---

## 第 31 章：文档加载与解析链路

- **定位**：RAG 天花板——解析质量差，模型再强也白搭。
- **核心内容**：
  - `DocumentLoader`（拉取字节）→ `DocumentParser`（转成 Document）
  - 常见格式适配：PDF（PdfBox / Tika）、HTML（Playwright）、加密 Office
  - 为什么不能用 `Files.readString()` 应付一切
  - 安全前端：杀毒、禁宏、沙箱解析 + 入库前最小字符数门禁
- **实战目标**：用 `TextDocumentParser` 换 PDF/Tika 并对比依赖树；设计「解析后字符数折线图」上线门禁。
- **源码锚点**：[`document-loaders/*`](../../langchain4j/document-loaders/)、[`document-parsers/*`](../../langchain4j/document-parsers/)。

---

## 第 32 章：生产级向量库选型

- **定位**：AI 中台基建决策——不是比 feature list，是比同套评测集的 Recall@K 和 P99。
- **核心内容**：
  - pgvector vs Milvus vs ES 的适用场景矩阵
  - PoC 方法：同一 EmbeddingModel + 同一查询集 + 四个维度的对比
  - TCO + 合规 vs 纯技术指标的权衡
  - 团队熟悉度与 on-call 负担——人的因素往往更决定性
- **实战目标**：启动 Docker 依赖跑 Testcontainers 集成测；制作一页选型对比表（含团队熟悉度 1-5 分）。
- **源码锚点**：[`pgvector-example`](../../langchain4j-examples/pgvector-example)、[`milvus-example`](../../langchain4j-examples/milvus-example)。

---

## 第 33 章：数据更新与索引漂移

- **定位**：文档变了，答案不能还是旧的——索引新鲜度治理。
- **核心内容**：
  - 增量更新：以业务主键 hash 判是否需要重嵌入
  - 全量重建的蓝绿切换 + 别名回滚
  - 漂移发现：定时评测 hit@k、用户点踩、法务主动触发
  - 最终一致 + outbox 模式的异步更新管道
- **实战目标**：起草 SOP（文档更新 → 队列 → 解析 → 嵌入 → 新 segment → 旧失效）；设计 DLQ 重试机制；写版本戳端到端断言。
- **源码锚点**：结合第 19～20 章 ingest 代码与所选向量库运维手册。

---

## 第 34 章：Spring Boot 与 Quarkus 等运行时集成

- **定位**：从 main 到容器——把 LLM 能力纳入 DI、配置外置、健康检查与指标（★ 深度样章）。
- **核心内容**：
  - Spring：`@AiService` 注解 + `AssistantConfiguration`（prototype 记忆 + Listener）
  - Quarkus：声明式 AI 服务 + JAX-RS Resource + native-image 注意点
  - 双 ChatModel Bean（快/慢）的 `@Qualifier` 方案
  - SSE 过网关的排查清单（缓冲、超时、HTTP/2）
- **实战目标**：跑通 Spring Boot 的 `/assistant` 和 `/streamingAssistant` 端点；用 `curl -N` 验证流式；配 `MyChatModelListener` 统一打点。
- **源码锚点**：[`spring-boot-example`](../../langchain4j-examples/spring-boot-example)、[`quarkus-example`](../../langchain4j-examples/quarkus-example)。

---

## 第 35 章：端到端场景 —— 客服 Agent

- **定位**：融会贯通初中级知识——一个能聊天、能查订单、能退款的真实 Agent。
- **核心内容**：
  - `CustomerSupportAgent` 的系统提示 + `BookingTools` 组合
  - Agent 与纯 RAG 的本质差异：能行动，故障面也更大
  - 人工接管的触发机制（`escalateToHuman` + 置信度阈值）
  - 集成测试的 JudgeModel（LLM as Judge）与成本控制
- **实战目标**：画时序图（User HTTP → Controller → Agent → ChatModel/Tools/Memory）；测试 `BookingNotFoundException` 的 HTTP 映射。
- **源码锚点**：[`customer-support-agent-example`](../../langchain4j-examples/customer-support-agent-example)。

---

## 第 36 章：可观测性与 Guardrails（合并章）

- **定位**：多慢、多贵、错在哪 + 该不该回——运维 + 风控同页落地。
- **核心内容**：
  - 必采指标：P95/P99、token、工具率、空检索率、429/5xx、解析失败率
  - `ChatModelListener` 作为统一观测切面
  - Guardrails：入站清洗 + 出站审查 + 高风险人工
  - 护栏误杀的申诉机制与灰度上线
- **实战目标**：列出 Micrometer/OTel tags；草拟入站 / 出站策略表；用对抗样本集定期跑护栏。
- **源码锚点**：[`MyChatModelListener.java`](../../langchain4j-examples/spring-boot-example/src/main/java/dev/langchain4j/example/aiservice/MyChatModelListener.java)、`langchain4j-guardrails`。

---

## 第 37 章：MCP —— 与外部系统的标准工具接口

- **定位**：USB-C 式的 AI 控制平面——跨进程、跨语言的工具互操作标准。
- **核心内容**：
  - MCP 与 `@Tool` 的关系：互补（同 JVM vs 跨进程）
  - Docker 版 MCP 的容器隔离优势
  - 安全原则：最小权限 + scope token + 审计日志
  - 危险操作的白名单与拒绝设计
- **实战目标**：跑通 `mcp-example` 或 `mcp-github-example`；列 allow/deny 清单。
- **源码锚点**：[`mcp-example`](../../langchain4j-examples/mcp-example)、[`mcp-github-example`](../../langchain4j-examples/mcp-github-example)。

---

## 第 38 章：Agentic、实验特性与源码贡献（合并章）

- **定位**：前沿能力 + 社区参与——从使用者到贡献者的最后一跃。
- **核心内容**：
  - Agentic 模式：多步规划 / 自主循环，预算控制与人工闸
  - 实验模块的上生产条件：锁版本、降级方案、接受 API 变更
  - 源码阅读路线：`AiServices` → `DefaultAiServices` → HTTP/Tools 自顶向下
  - 贡献四步走：文档 → 最小复现 → 测试 → PR（小步提交）
- **实战目标**：fork 仓库 sync upstream；找到一条 good first issue 并写理解笔记；设计 SNAPSHOT 回滚预案。
- **源码锚点**：[`AiServices.java`](../../langchain4j/langchain4j/src/main/java/dev/langchain4j/service/AiServices.java)、`CONTRIBUTING.md`。

---

# 附录与资源

## 附录 A：源码阅读路线图

按模块自顶向下阅读，由浅入深：

1. **入门**：`langchain4j-core` → `ChatModel`、`ChatMessage`、`Embedding` 接口
2. **组件**：`langchain4j` → `AiServices`、`DefaultAiServices`、`ContentRetriever`、`ChatMemory`
3. **适配层**：`langchain4j-open-ai` 等 provider → `OpenAiChatModel`、请求构造与响应解析
4. **横切**：`langchain4j-http-client-*`、`langchain4j-mcp`、`langchain4j-guardrails`
5. **实验**：`langchain4j-agentic*`、`experimental/*`

## 附录 B：环境与调试指南

- **JDK**：17+（建议 21 LTS）
- **构建**：Maven（含 wrapper）或 Gradle
- **常用命令**：
  - `mvn -q dependency:tree -Dincludes=dev.langchain4j`：审计 LangChain4j 依赖版本
  - `mvn -pl <模块名> -am`：只编译相关模块
- **调试技巧**：
  - 在 `ChatModelListener` 中拦截请求 / 响应（调试与脱敏）
  - 在 `StreamingChatResponseHandler.onPartialResponse` 中 `count++` 了解 chunk 数
  - `dependency:list` 对比生产与开发环境的差异

## 附录 C：推荐工具链

- **测试**：JUnit 5、Mockito、WireMock、Testcontainers
- **观测**：Micrometer、OpenTelemetry、Grafana、Prometheus
- **向量库**：pgvector、Milvus（Testcontainers 集成测）
- **容器**：Docker、Kubernetes（用于向量库和 MCP Server 的隔离部署）
- **API 调用**：`curl -N`（SSE 流式验证）、Postman

## 附录 D：思考题参考答案索引

- 基础篇思考题答案：见各章末尾或下一章开篇，核心答案线索已在各章「进阶思考题」中标注指向的章节号。
- 中级篇思考题答案：同上，关键思考题在相关后续章节中自然展开（如第 16 章思考题指向第 21 章压缩、第 24 章来源）。
- 高级篇思考题答案：涉及跨章组合（如 MCP + 动态工具），答案在最相关的章节末尾。

---

> **版权声明**：本专栏基于 LangChain4j 开源仓库（Apache 2.0 License）编写，代码引用与章节内容均为原创教学目的。示例代码路径均以本地仓库 [`langchain4j`](../../langchain4j) 与 [`langchain4j-examples`](../../langchain4j-examples) 为准。
