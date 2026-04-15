# 第 3 章：架构鸟瞰 —— core、主模块与集成层

## 1. 项目背景

### 业务场景（拟真）

平台组要把 LangChain4j 纳入公司 **标准技术栈**，架构评审会上需要回答：**「报错时该看哪个模块？」「实验特性能不能进核心交易？」** 新人从 [`langchain4j/pom.xml`](../../langchain4j/pom.xml) 打开聚合工程，面对 **数十个子模块**，若分不清 **core / 主 artifact / provider / 向量库 / 横切能力**，排障会变成 **全局 grep**，而不是 **分层定位**。

### 痛点放大

[`langchain4j/pom.xml`](../../langchain4j/pom.xml) 聚合了 **语言与嵌入模型提供商**、**向量存储**、**HTTP 客户端**、**文档加载与解析**、**实验与 Agentic**、**可观测与 Guardrails** 等。没有分层心智时：**性能**问题可能被误判为「模型慢」而实际是 **HTTP 客户端连接池**；**一致性**上 BOM 与 **leaf 模块**混引导致 **`NoSuchMethodError`**；**可维护性**上业务 war 同时塞进 **实验模块与护栏**，发布节奏被拖死。

抽象层可粗分为：

- **`langchain4j-core`**：接口与领域模型（消息、嵌入、检索请求、Query 变换器等），尽量避免拉具体厂商依赖。  
- **`langchain4j`（主 artifact）**：`AiServices`、默认内存与工具编排等「开箱组合」。  
- **`*-open-ai`、`*-ollama`、`*-vertex-*`**：具体模型与供应商协议适配。  
- **`langchain4j-pgvector`、`langchain4j-milvus`…**：向量库存储后端。  
- **`langchain4j-mcp`、`langchain4j-guardrails`…**：横切能力与扩展协议。

```text
业务代码 ──► langchain4j（AiServices 等）──► langchain4j-core（接口）
                │                                ▲
                └── provider / http-client / embedding-store 实现
```

## 2. 项目设计：小胖、小白与大师的对话

**小胖**：这仓库模块比商场楼层还多，我就写个聊天，为啥不能「一个 jar 搞定」？

**小白**：我最该先读哪个模块？**core 为啥不直接依赖 OkHttp？** `integration-tests` 和平时引的依赖啥关系？

**大师**：应用开发先 **`langchain4j` + provider**；平台再下钻 **`langchain4j-core`** 与 **HTTP 客户端**。core **不绑 OkHttp** 是为了 **最小传递依赖**，具体 HTTP 栈由 `langchain4j-http-client-*` 注入，避免绑架用户栈。**技术映射**：**core = 契约层，http-client-* = 可替换传输**。

**小胖**：那「实验模块」能随便加进我们支付服务吗？

**小白**：补充：**周五晚高峰 `NoSuchMethodError` 堆栈在 openai 包里**，第一反应是不是该骂库有 bug？

**大师**：应先查 **BOM 是否混版本**、是否 **SNAPSHOT**、厂商响应是否变更、**native 镜像**是否缺 **SPI/反射**——分层画在纸上，通常 **30 分钟内** 收窄到一层。实验模块应在 **独立服务** 试点并 **钉版本**，别当全公司隐形父 POM。**integration-tests** 是 **多模块组合回归**，学兼容矩阵用，不必第一周啃。**技术映射**：**排障顺序 = BOM → 传输层 → provider 适配 → 业务**。

**小胖**：懂了，就像外卖 App：**core 是菜单标准**，**各店是 provider**，**骑手公司是 http-client**——别在火锅店里点披萨还怪平台。

**小白**：若 **`guardrails` 与 `mcp` 同时接**，部署上怎么切？

**大师**：护栏可作 **稳定共享库** 进主服务；**MCP Server** 建议 **独立进程**，宿主用客户端连——**依赖隔离 + 运维边界**（证书、扩缩）。**技术映射**：**进程边界 = 变更频率与合规等级**。

---

## 3. 项目实战

### 环境准备

- 本地已克隆 [`langchain4j`](../../langchain4j) 仓库；IDE 可折叠 Maven 模块树。  
- 可选：JDK 与官方文档站点，用于对照 artifact 说明。

### 分步任务

| 步骤 | 目标（一句话） | 操作与产出 |
|------|----------------|------------|
| 1 | 建立「模块地图」 | 打开 [`langchain4j/pom.xml`](../../langchain4j/pom.xml)，折叠 `<modules>`，用 **三种颜色** 标注：**模型提供商**、**向量存储**、**横切（http / observation / guardrails）** |
| 2 | 建立个人 cheat sheet | 从 [`README`](../../langchain4j/README.md) 各抄 **1 个** 关心的 `artifactId` |
| 3 | 画请求路径 | 草稿纸画箭头：`HTTPS` → `Controller` → `AiServices/ChatModel` →（可选）`EmbeddingStore` → `ChatModel`，**故意画错一箭**，第 4 章再修 |

**可能遇到的坑**：把 **aggregator 整体** 依赖进业务 → **fat jar 与冲突**；**解法**：只引 **需要的 leaf + BOM**。

**深度一问**：`langchain4j-core` 里为何尽量不出现 OkHttp 依赖？（提示：**最小传递依赖**、**SPI**。）

### 延伸案例（情景演练）

假设客服系统在星期五晚高峰抛出 **`NoSuchMethodError`**，堆栈落在 `dev.langchain4j.model.openai`。一线若只喊「库有 bug」，会忽略 **分层**：**core** 定义接口，**open-ai** 编解码，**http-client-*** 管连接。复盘依次问：**(1)** BOM；(2) SNAPSHOT；(3) 厂商响应变更；(4) **native 镜像**反射配置。

某金融科技想把 **`langchain4j-guardrails`** 与 **`langchain4j-mcp`** 同塞一个 **war**，导致发布被实验特性拖住。调整：**护栏**进主服务；**MCP** 独立进程 **Out-of-process**。

### 测试验证

- 能向同事口述：**core / langchain4j 主模块 / provider** 各解决什么问题。  
- 画一张 **部署拓扑**：哪些 jar 同 JVM，哪些是 **sidecar**。

### 完整代码清单

本章无独立示例类；源码阅读入口：根 [`pom.xml`](../../langchain4j/pom.xml)、`langchain4j-core` 包根、`AiServices.java`（预习）。

---

## 4. 项目总结

### 优点与缺点（与同类做法对比）

| 维度 | 多模块 + core 分离 | 单胖 jar 全家桶 | 自研胶水封装 |
|------|---------------------|-----------------|--------------|
| 边界清晰度 | 高 | 低 | 视团队 |
| 新人导航成本 | 中（需地图） | 低 | 高 |
| 与厂商演进 | 换 provider 模块为主 | 易全量升级 | 维护成本高 |
| 典型缺点 | 模块名需记忆 | 传递依赖难控 | 无社区对齐 |

### 适用场景

- 架构评审、技术选型、**平台组封装前**的现状分析。  
- 需要向 **管理层**解释「为何不是单一依赖坐标」时。

### 不适用场景

- **个人脚本级**一次性调用、且永不扩展——可直接用最薄封装。  
- **完全不想理解模块**且拒绝 BOM——不适合企业级治理。

### 注意事项

- **别把 aggregator 整体依赖进业务**——只引 leaf + BOM。  
- **关注实验模块稳定性标注**，避免 silent breaking change。

### 常见踩坑经验（生产向根因）

1. **在业务模块直接依赖 `integration-tests`** → 测试代码进生产路径。  
2. **误以为 core 含 OpenAI 实现** → 编译或运行期缺类。  
3. **复制别人 `pom` 一大坨依赖** → 版本分叉与 **`NoSuchMethodError`**。

### 进阶思考题

1. 若 **native-image** 下 `ServiceLoader` 未加载某 HTTP 客户端实现，你会从 **哪一层**开始加 `reflect-config`？（提示：http-client 模块与 SPI。）  
2. **护栏稳定、MCP 实验** 时，如何用 **发布列车** 解耦版本？（提示：独立制品与 API 契约。）

### 推广计划提示（多部门）

| 角色 | 建议阅读顺序 | 协作要点 |
|------|----------------|----------|
| **开发** | 本章 → 第 4、12 章 | 新需求 **只引必要 leaf** |
| **架构** | 本章 + BOM 策略 | 输出 **受支持组合表**（JDK × Boot × BOM） |
| **运维** | 本章 + 第 9 章 | 向量库与模型 HTTP **分探针**，勿混为单一健康检查 |

---

### 本期给测试 / 运维的检查清单

**测试**：要求架构组输出 **「受支持组合表」**（JDK × Spring Boot × LangChain4j BOM），纳入兼容性测试矩阵。  
**运维**：对 **向量库与模型 HTTP 端点** 分别建可用性探针，避免混为单一「应用健康」Endpoint。

### 附录：相关模块与源码入口

推荐阅读文件：根 [`pom.xml`](../../langchain4j/pom.xml)、`langchain4j-core` 包根、`AiServices.java`（预习）。
