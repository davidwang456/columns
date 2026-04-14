# 第 18 章：Easy RAG —— 最快上线的检索增强

## 1. 项目背景

检索增强生成（RAG）要解决的核心矛盾是：**大模型不知道你私域文档里的细节**，但把所有文档塞进提示词既不现实也烧钱。典型管线包括：加载文档 → 切分 → 向量化 → 写入向量库 → 查询时检索相关片段 → 把片段与用户问题一起交给模型回答。若在第一节课就把每一步都讲透，初学者容易被「切分策略」「嵌入维度」「索引类型」淹没。

LangChain4j 的 **`langchain4j-easy-rag` 思路**（教程里称「Easy RAG」）把「摄取（ingest）」封装在一行调用中：**你不先成为 NLP 专家，也能得到一个能读本地文件并回答问题的助理**。示例代码见：

- `langchain4j-examples/rag-examples/src/main/java/_1_easy/Easy_RAG_Example.java`

本章在「Naive RAG」「低层 Naive」之前出现，是**从业务结果倒推技术债**的最佳入口：先看到可运行的问答，再逐层打开黑盒。

## 2. 项目设计：大师与小白的对话

**小白**：文档里说 Easy RAG 有「魔法」，我是不是该担心看不到数据怎样被切开？

**大师**：你该担心的是**业务目标**有没有被满足。Easy RAG 刻意把解析、切分、嵌入藏在 `EmbeddingStoreIngestor` 的默认路径里。等你需要审计与调优时，再去翻「低层」示例和 `EmbeddingStoreIngestor` 的配置。

**小白**：`InMemoryEmbeddingStore` 是不是只能做玩具？

**大师**：在单测、PoC、小规模内测，它是最省基础设施成本的方案。**没有外部向量库**时，它让你把注意力放在「召回是否相关、提示词是否合理」。上线前有数据量、持久化与高可用诉求时，再换 pgvector、Milvus、OpenSearch 等（第六部分）。

**小白**：`Assistant` 是个接口，`AiServices` 帮我生成了什么？

**大师**：LangChain4j 用 **`AiServices.builder(Assistant.class)`** 为接口生成实现：把 **`chatModel` + `chatMemory` + `contentRetriever`** 绑成一条可调用链。对用户方法的一次调用，内部会经历「检索→拼上下文→调用模型」。

**小白**：`contentRetriever` 和「向量搜索」是什么关系？

**大师**：`ContentRetriever` 是**更高一层**的抽象：返回「模型应该阅读的文本片段集合」。背后可以是向量相似度，也可以是混合检索。Easy 样例使用 `EmbeddingStoreContentRetriever.from(embeddingStore)`，把向量库包装成「内容提供者」。

**小白**：如果我不想用 OpenAI 的嵌入怎么办？

**大师**：Easy 路径里 `EmbeddingStoreIngestor.ingest` 会使用**默认嵌入模型**配置（依赖 classpath 上与模块默认策略）。要换嵌入模型，就需要显式配置 ingestor 或换「非 Easy」管线 —— 这正是第 19～20 章存在的原因。

**小白**：RAG 会不会把机密文件「泄」到提示里？

**大师**：**会**，如果你把不该检索到的片段召回出来。权限模型要在「数据来源、元数据过滤、embedding 分区」多层做 —— Easy RAG 不管企业 ACL，你需要在第 24、25 章与第六部分补强。

**小白**：用户对话里会记住什么？

**大师**：示例里配置了 **`MessageWindowChatMemory.withMaxMessages(10)`**，只保留最近若干条对话轮次，防止上下文无限膨胀。持久化会话则回到第 13 章。

**小白**：我该怎样衡量这一章学得好不好？

**大师**：换一批你自己的 `documents/*.txt`，问 3 个**只有文中才有答案**的问题与一个**文中没有**的问题，看模型是「忠实引用」还是「胡编」——后者会引出检索失败与提示策略问题。

## 3. 项目实战：主代码片段

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

### 优点

- **极短路径产出可演示的私域问答**，利于跨部门对齐需求。
- **抽象层次合理**：`ContentRetriever` 把「检索细节」与「对话接口」解耦。
- **默认 ingest** 降低心智负担，让团队先交付价值再偿还技术债。

### 缺点

- **可控性较弱**：对切分尺寸、嵌入模型、召回数量的细粒度控制需要跳出 Easy 模式。
- **内存向量库不适配大规模文档**：单机内存与启动时间会成为瓶颈。
- **默认可重复性**：若无固定随机种子、文档版本与嵌入模型版本，跨环境对比结果可能漂移。

### 适用场景

- 内部分享、工作坊、一日 Hackathon。
- 在引入外部向量库前的**逻辑验证**。

### 注意事项

- **版权与隐私**：加载前完成数据分级与脱敏。
- **评估指标**：至少准备一组「问题-期望片段」对照表，而不是主观觉得「好像很智能」。
- **与对话记忆叠加**：过多轮闲聊可能稀释检索信号，必要时在路由层判断「本轮是否需要 RAG」（见第 26 章）。

### 常见踩坑

1. **文档编码与路径**：`FileSystemDocumentLoader` 在 Windows 与类 UNIX 路径差异下易踩坑，建议统一用 `Path` 与资源目录约定。
2. **误以为 Easy RAG = 生产方案**：上线需替换 `InMemoryEmbeddingStore`、补充观测与回滚策略。
3. **忽略空检索**：若没有任何片段超过相似度阈值，模型仍可能「自信」回答 —— 需在提示词里显式要求「若无依据则拒答」。

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
