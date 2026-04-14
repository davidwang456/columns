# 第 19 章：Naive RAG —— 拆分摄取与查询

## 1. 项目背景

「Naive」在此不是贬义，而是说：**不做查询扩展、重排、路由**等高级策略，只遵循经典六步：加载文档 → 切分 → 嵌入 → 写入向量库 → 用查询向量做 topK → 把片段拼进提示。这样做的价值是 **可教学、可调试**：当回答不对时，你能判断是 **切分太碎**、**嵌入不对**、还是 **LLM 不听话**。

`Naive_RAG_Example.java`（`langchain4j-examples/rag-examples/src/main/java/_2_naive/Naive_RAG_Example.java`）在文件头注释中逐步写了心智模型，并显式使用 `TextDocumentParser`、`DocumentSplitters`、`EmbeddingModel`、`InMemoryEmbeddingStore` 等类型。相对 Easy RAG，你 **牺牲了行数**，换到了 **可调旋钮**。

## 2. 项目设计：大师与小白的对话

**小白**：Naive 是不是上线就用它？

**大师**：可作为 **MVP**；用户量与合规上来后，要补 **元数据、鉴权、混合检索**（第三～五部分）。

**小白**：为什么要同时出现 OpenAI chat 和本地 ONNX embed？

**大师**：示例要展示 **解耦**：聊天走云、嵌入可本地化以 **省费/省延迟**。真实企业要按 **数据主权** 选择。

**小白**：ingest 能在离线跑吗？

**大师**：**应能**。生产常见 **批量任务** 写向量库，在线仅 query。

**小白**：topK 取多少？

**大师**：从产品试验起：**3～8** 常见；要看片段长度与模型窗口。

**小白**：如何判断「检索到了错的片段」？

**大师**：**记录每次召回的文本与分数**（调试模式），用 **黄金问答集**回归。

## 3. 项目实战：主代码片段

> **场景入戏**：Naive RAG 像 **自己洗菜、切菜、炒菜**——手累，但你知道 **哪一步盐放多了**；Easy RAG 像 **外卖**，快，但 **厨师黑箱**。

阅读 [`Naive_RAG_Example.java`](../../langchain4j-examples/rag-examples/src/main/java/_2_naive/Naive_RAG_Example.java) 的 `createAssistant`：**ChatModel → loadDocument → parser → splitter → embedAll → embeddingStore → EmbeddingStoreContentRetriever → AiServices** ——在纸上画 **七个方框**，缺一框就 **答错题**。

片段骨架（与仓库理念一致，细节以源码为准）：

```java
Document document = loadDocument(toPath(documentPath), new TextDocumentParser());
DocumentSplitter splitter = DocumentSplitters.recursive(...);
EmbeddingModel embeddingModel = new BgeSmallEnV15QuantizedEmbeddingModel();
EmbeddingStore<TextSegment> embeddingStore = new InMemoryEmbeddingStore<>();

// embed segments, add to store ...

ContentRetriever contentRetriever = EmbeddingStoreContentRetriever.from(embeddingStore);

return AiServices.builder(Assistant.class)
        .chatModel(chatModel)
        .chatMemory(MessageWindowChatMemory.withMaxMessages(10))
        .contentRetriever(contentRetriever)
        .build();
```

#### 深度对比（表格作业）

| 步骤 | 你能在日志里看到什么？ | Easy RAG 同款信息从哪来？ |
|------|------------------------|----------------------------|
| splitter 参数 | 自己设 | 默认/ingestor 内部 |
| embed 模型 | `BgeSmall...` 显式 | classpath 默认 |

#### 闯关

- **★** 调长 `chunk`，问需要 **跨段** 才能答的问题。  
- **★★★** 故意 **只 embed 一半文档**（注释掉循环），看模型如何 **胡编** ——记为 **负样本**。

## 4. 项目总结

### 优点

- **完全显式**，利于新人建立 **RAG 数据流**概念。  
- 易于添加 **日志**定位瓶颈。

### 缺点

- **样板代码**多。  
- 「Naive」检索对 **口语化提问**脆弱。

### 适用场景

- 内部培训、代码评审 demo。  
- 首次把 **向量库**接进现网前的 **技术验证**。

### 注意事项

- **嵌入模型** 与 **聊天模型** 语言对齐。  
- **索引版本**记录进发布单。

### 常见踩坑

1. **忘记 embed** 或 **重复 embed** 未去重。  
2. **切分太小** → 丢上下文；**太大** → 噪声多。  
3. **评测只看最终自然语言**不看召回片段。

---

### 本期给测试 / 运维的检查清单

**测试：**：snapshot **召回列表**（脱敏）与 **黄金集**比对 ing top@k；对 **空召回**断言应用提示。  
**运维**：批索引 **资源配额**；**向量库磁盘**或 **内存**阈值。

### 附录：相关 Maven 模块与源码类

| 模块 | 说明 |
|------|------|
| `langchain4j` | `DocumentSplitter`、`EmbeddingStoreIngestor`（对比 ingest 差异） |
| `embeddings/*` | 如 `BgeSmallEnV15QuantizedEmbeddingModel` |

推荐阅读：`Naive_RAG_Example.java` 顶部注释、`EmbeddingStoreContentRetriever`。
