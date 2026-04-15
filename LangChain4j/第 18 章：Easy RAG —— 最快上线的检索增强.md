# 第 18 章：Easy RAG —— 最快上线的检索增强

## 1. 项目背景

### 业务场景（拟真）

业务部门要在 **一周内** 看到「**上传产品说明 txt，就能问出参数**」的 Demo，架构组来不及搭 **独立向量库集群**。检索增强生成（RAG）的核心矛盾是：**模型不知道私域细节**，但 **全量塞进提示** 不现实且烧钱。

### 痛点放大

若第一节课就讲透 **切分、嵌入维度、索引类型**，初学者易被淹没；若 **没有可运行闭环**，评审会停留在 **PPT**。LangChain4j 的 **Easy RAG** 把 **ingest** 收进 `EmbeddingStoreIngestor` 默认路径：**先交付可演示问答，再偿还技术债**。示例：

- `langchain4j-examples/rag-examples/src/main/java/_1_easy/Easy_RAG_Example.java`

## 2. 项目设计：小胖、小白与大师的对话

**小胖**：Easy RAG 像火锅自助——肉自己端，锅帮你切？

**小白**：文档说 Easy 有「魔法」——**看不到数据怎么切开** 要不要慌？`InMemoryEmbeddingStore` 只能玩具吗？

**大师**：先担心 **业务目标** 是否满足。Easy 把 **解析/切分/嵌入** 藏在 `EmbeddingStoreIngestor` 默认里；要审计再打开第 20 章与源码。**内存库** 适合单测、PoC、小规模内测；**无外部向量库** 时聚焦「召回与提示」——上线再换 pgvector/Milvus（第六部分）。**技术映射**：**Easy = 默认管线，非生产终态**。

**小胖**：`Assistant` 接口谁实现？问一句中间到底几步？

**小白**：`contentRetriever` 和 **向量搜索** 啥关系？**换嵌入模型** 咋办？**机密会不会泄进提示**？

**大师**：`AiServices.builder(Assistant.class)` 生成实现，绑定 **`chatModel` + `chatMemory` + `contentRetriever`**，调用链是 **检索 → 拼上下文 → 模型**。`ContentRetriever` 是更高抽象；Easy 用 `EmbeddingStoreContentRetriever.from(embeddingStore)`。换嵌入要 **显式配置 ingestor** 或走第 19～20 章。**机密召回** 依赖 **ACL、元数据过滤、分区**（第 24、25 章）——Easy **不管企业权限**。**技术映射**：**ContentRetriever = 片段集合；背后可是向量或混合检索**。

**小胖**：对话会记住啥？咋验收学没学会？

**大师**：示例 **`MessageWindowChatMemory.withMaxMessages(10)`**；持久化见第 13 章。自测：换自有 `documents/*.txt`，**3 个仅文中有答案** + **1 个文中无**——看 **忠实引用 vs 胡编**。**技术映射**：**评测集 = 检索失败探测器**。

## 3. 项目实战

### 环境准备

- `langchain4j-examples/rag-examples` 可构建；准备 `documents/*.txt` 与有效模型 Key（或本地兼容端点）。

### 分步实现（主代码片段）

> **场景入戏**：Easy RAG = **火锅自助**：肉（文档）你自己端，`EmbeddingStoreIngestor` 像**神奇汤底**帮你切好、腌好、下锅；你只管烫（提问）。**代价**是：你不知道汤底里放了啥香料——要调口味得换第 20 章「低层」。

关键结构（节选自有注释的仓库文件）：

```java
// 加载本地文本
List<Document> documents = loadDocuments(toPath("documents/"), glob("*.txt"));

Assistant assistant = AiServices.builder(Assistant.class)
        .chatModel(CHAT_MODEL)
        .chatMemory(MessageWindowChatMemory.withMaxMessages(10))
        .contentRetriever(createContentRetriever(documents))
        .build();

startConversationWith(assistant);
```

`createContentRetriever` 的核心如下：

```java
private static ContentRetriever createContentRetriever(List<Document> documents) {

    InMemoryEmbeddingStore<TextSegment> embeddingStore = new InMemoryEmbeddingStore<>();

    EmbeddingStoreIngestor.ingest(documents, embeddingStore);

    return EmbeddingStoreContentRetriever.from(embeddingStore);
}
```

**仓库锚点**：[`Easy_RAG_Example.java`](../../langchain4j-examples/rag-examples/src/main/java/_1_easy/Easy_RAG_Example.java)（注意包名 `_1_easy` 与 `shared` 工具类）。

#### 读代码路线（像玩解谜）

1. **`shared.Assistant`**：有哪些注解会把 **系统人设** 或 **RAG 行为**钉死？  
2. **`EmbeddingStoreIngestor.ingest`**：点进源码，回答「**默认 splitter / embed 是谁**」——答不上来＝承认**魔法黑箱**存在。  
3. **`startConversationWith`**：用户问题 **从键盘进模型之前**，中间插了 **几次检索**？（自行 `println` 或断点）

#### 闯关试验（★★★ 给勇敢者）

| 关卡 | 操作 | 通过标准 |
|------|------|----------|
| ★ | 改 `documents/*.txt` 一句事实，重启对话问同样问题 | 回答跟着变——证明 **检索真在工作** |
| ★★ | 塞两段 **互相矛盾** 条款 | 看模型 **选边站** 还是 **和稀泥**——记下来当产品风险 |
| ★★★ | 把整个目录改成 **中文条款**再用英文问 | 观察 **跨语言检索**短板（为换多语言 embed 埋伏笔） |

**进阶试验（保留）：**

- 增加与删除 `documents/` 下文件，观察回答是否随之变化。
- 为矛盾段落写一行 **人工规则**：「若检索到冲突，回答请只列条款编号不做价值判断」——体会 **提示兜底**。

### 与后续章节的接口关系

- **`ContentRetriever`**：在第 21～27 章会被替换为带压缩、路由、重排序的组件或由多个检索器组合。
- **`EmbeddingStoreIngestor`**：第 20 章「低层」会拆出显式 `DocumentSplitter`、`EmbeddingModel` 注入。
- **`AiServices`**：第 12、16 章已铺垫，本章是其在 RAG 场景下的「主战场」之一。

## 4. 项目总结

### 优点与缺点（与同类做法对比）

| 维度 | Easy RAG | Naive/低层 RAG（第 19～20 章） | 仅 Chat 无检索 |
|------|----------|-------------------------------|----------------|
| 上线速度 | 极快 | 中 | 最快 |
| 可控性 | 弱 | 强 | 无检索 |
| 基础设施 | 可仅内存库 | 可外置向量库 | 最少 |
| 典型缺点 | 黑箱默认、难审计 | 实现成本高 | 无私域事实 |

**文字补充（优点）**：**极短路径演示私域问答**；`ContentRetriever` **解耦**检索与对话；**默认 ingest** 降心智负担。

**文字补充（缺点）**：**细粒度调参**需跳出 Easy；**内存库** 不适配大规模；**默认可重复性** 依赖文档与嵌入版本钉死。

### 适用场景

- 内部分享、工作坊、Hackathon；引入外置向量库前的 **逻辑验证**。

### 不适用场景

- **强 ACL、合规分区**——须第 24、25、32 章与数据工程，不单靠 Easy。  
- **亿级文档**——须分布式向量库与 ingest 流水线。

### 注意事项

- **版权与隐私**；**评估指标**（问题-期望片段对照）；**多轮闲聊稀释检索**（第 26 章路由）。

### 常见踩坑经验（生产向根因）

1. **路径/编码**（Windows vs UNIX）导致加载失败。  
2. **误以为 Easy = 生产**——须换外置库、观测、回滚。  
3. **空检索** 仍胡编——提示须 **无依据则拒答**。

### 进阶思考题

1. 如何将 **文档版本号** 与 **embedding 模型版本** 打进 **同一条审计日志**？  
2. **跨语言问答**（中文档、英问题）时 Easy 默认 embed 的短板？（提示：第 29 章。）

### 推广计划提示（多部门）

| 角色 | 建议阅读顺序 | 协作要点 |
|------|----------------|----------|
| **开发** | 第 16 章 → 本章 → 第 19 章 | **技术债清单**：切分、embed、召回 |
| **产品** | 本章 | **矛盾条款** 与 **拒答** 体验 |
| **运维** | 本章 + 第 32 章 | **ingest 峰值**、未来 **索引重建** |

---

### 本期给测试 / 运维的检查清单

**测试**：构造「文档有答案」「文档无答案」「多文档冲突答案」三类用例；对 ContentRetriever 做契约测试（返回片段条数上限、metadata 字段）；在 CI 用极小 fixture 文档跑通嵌入与检索（可选用固定向量维度的假模型以省成本）。

**运维**：记录嵌入批任务的 CPU/内存峰值；为将来切换到外置向量库预留连接串、凭据轮换与索引重建 Runbook；监控 `chatModel` 调用延迟与向量检索耗时的占比，避免只盯着最终一句话延迟。

### 附录：相关 Maven 模块与源码类

| 模块 | 说明 |
|------|------|
| `langchain4j` | `AiServices`、`InMemoryEmbeddingStore` |
| `langchain4j-easy-rag` | 简化摄取与默认管线 |
| `langchain4j-core` | `ContentRetriever`、`EmbeddingStore` 抽象 |

推荐阅读：`Easy_RAG_Example.java`、`EmbeddingStoreIngestor`、`EmbeddingStoreContentRetriever`、`AiServices`。
