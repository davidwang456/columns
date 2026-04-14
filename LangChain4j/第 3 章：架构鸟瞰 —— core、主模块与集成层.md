# 第 3 章：架构鸟瞰 —— core、主模块与集成层

## 1. 项目背景

[`langchain4j/pom.xml`](../../langchain4j/pom.xml) 聚合了 **数十个子模块**，可粗分为：**语言与嵌入模型提供商**、**向量存储**、**HTTP 客户端**、**文档加载与解析**、**实验与 Agentic**、**可观测与 Guardrails**。理解分层的目的，是让你在报错时 **快速判断「是我配置错」还是「该去某模块提 issue」**。

抽象层大致为：

- **`langchain4j-core`**：接口与领域模型（消息、嵌入、检索请求、Query 变换器等），尽量避免拉具体厂商依赖。  
- **`langchain4j`（主 artifact）**：`AiServices`、默认内存与工具编排等「开箱组合」。  
- **`*-open-ai`、`*-ollama`、`*-vertex-*`**：具体模型与供应商协议适配。  
- **`langchain4j-pgvector`、`langchain4j-milvus`…**：向量库存储后端。  
- **`langchain4j-mcp`、`langchain4j-guardrails`…**：横切能力与扩展协议。

## 2. 项目设计：大师与小白的对话

**小白**：我最该先读哪个模块？

**大师**：取决于角色：应用开发先 **`langchain4j` + provider**；平台/中间件再下钻 **`langchain4j-core`** 与 **HTTP 客户端**。

**小白**：为什么 core 不直接依赖 OkHttp？

**大师**：保持 **最小依赖集**；具体 HTTP 栈由 `langchain4j-http-client-*` 注入，避免绑架用户栈。

**小白**：`integration-tests` 和普通模块什么关系？

**大师**：多为 **多模块组合的长期回归**，阅读可学「官方认为的兼容矩阵」，但不必第一周就看。

**小白**：实验模块我能随便引吗？

**大师**：在 **独立服务** 中试点，并 **显式版本钉死**；不要作为上百个团队共享的「隐形父 POM」。

## 3. 项目实战：主代码片段

> **场景入戏**：把 Aggregator 想成 **宜家平面图**：**core** 是螺丝规格（接口），**langchain4j 主模块**是成品家具（`AiServices`），**`*-open-ai`** 是不同产地的电动螺丝刀。你走错区域，买的桌腿和桌面**对不上丝**——那叫 **`NoSuchMethodError`**。

#### 寻宝任务（建议 25 分钟内完成）

1. 在 IDE 打开 [`langchain4j/pom.xml`](../../langchain4j/pom.xml)，折叠 `<modules>`，用 **三种颜色**高亮：**模型提供商**、**向量存储**、**横切（http / observation / guardrails）**。  
2. 从 [`README`](../../langchain4j/README.md) 或文档站 **各抄 1 个** 你关心的 `artifactId`（例如 `langchain4j-open-ai`、`langchain4j-pgvector`），贴在个人 cheat sheet。  
3. 在草稿纸画 **箭头**：`HTTPS 入站` → `Controller` → `AiServices/ChatModel` →（可选）`EmbeddingStore` → `ChatModel` —— **故意画错一箭**，第四章再修。

**深度一问**：`langchain4j-core` 里 **为何尽量不出现 OkHttp 依赖**？（提示：**最小传递依赖**、**SPI**。）

### 延伸案例（情景演练）：一次生产报错的「分层定位」

假设客服系统在星期五晚高峰抛出 **`java.lang.NoSuchMethodError`**，堆栈顶层落在 `dev.langchain4j.model.openai` 包内。一线同学若第一反应是「LangChain4j 有 bug」，往往会忽略 **分层**：**core** 只定义接口，**open-ai** 实现 HTTP 编解码，**http-client-*** 负责连接。复盘时应依次问：**(1)** BOM 是否混版本；**(2)** 是否某环境 **SNAPSHOT**；**(3)** 厂商 API **响应字段**是否突发变更导致适配器 **不兼容**；**(4)** 是否在 **native 镜像**里少了 **反射/服务加载**配置。把四层画在纸上，通常能在 **30 分钟内** 收窄到 **一层**。

再举一个 **架构视角** 的案例：某金融科技公司想把 **`langchain4j-guardrails`** 与 **`langchain4j-mcp`** 同时接入，却放在 **同一个业务 war** 里，导致 **发布周期**被「实验模块」拖住。调整方案是：**护栏作为稳定共享库** 进主服务；**MCP Server** 独立进程，以 **Out-of-process** 形式被宿主通过 **客户端**连接——这既符合 **依赖隔离**，也符合 **运维边界**（独立扩缩、独立证书）。你可以用本段练习画 **部署拓扑图**：哪些 jar 落在 **同一 JVM**，哪些是 **sidecar**。

## 4. 项目总结

### 优点

- **清晰边界** 利于大规模团队协作与代码审阅。  
- **SPI**（如 `AiServicesFactory`）保留扩展点（第 38 章）。

### 缺点

- **新人导航成本** 高；需配合本教程与官方文档站点。  
- **模块名与能力** 需记忆，短期依赖 cheat sheet。

### 适用场景

- 架构评审、技术选型汇报、**平台组做封装**前的现状分析。

### 注意事项

- **别把 aggregator 整体依赖进业务**——只引需要的 leaf 模块 + BOM。  
- **关注实验模块的稳定性标注**，避免silent breaking change。

### 常见踩坑

1. **在业务模块直接依赖 `integration-tests`**。  
2. **误以为 core 包含 OpenAI 实现** 而导致编译失败。  
3. **复制别人 `pom` 的一大坨依赖** 而不理解用途。

---

### 本期给测试 / 运维的检查清单

**测试**：要求架构组输出 **「受支持组合表」**（JDK × Spring Boot × LangChain4j BOM），纳入兼容性测试矩阵。  
**运维**：对 **向量库与模型 HTTP 端点** 分别建可用性探针，避免混为单一「应用健康」Endpoint。

### 附录：相关模块与源码入口

推荐阅读文件：根 [`pom.xml`](../../langchain4j/pom.xml)、`langchain4j-core` 包根、`AiServices.java`（预习）。
