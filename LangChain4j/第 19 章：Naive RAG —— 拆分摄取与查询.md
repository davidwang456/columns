# 第 19 章：Naive RAG —— 拆分摄取与查询

## 1. 项目背景

### 业务场景（拟真）

团队已通过 Easy RAG 做出 Demo，但 **无法解释**「答错时错在切分、嵌入还是模型」。需要在 **可教学、可调试** 的显式管线上迭代：**加载 → 切分 → 嵌入 → 写入向量库 → topK 检索 → 拼进提示**，暂不引入 **重排、路由、压缩** 等高级策略。

### 痛点放大

「Naive」在此 **非贬义**，而是 **经典六步可观测**。若 **永远停在 Easy 黑箱**：**性能** 无法定位 embed 瓶颈；**一致性** 无法钉 **索引版本**；**可维护性** 上排障靠猜。`Naive_RAG_Example.java` 显式使用 `TextDocumentParser`、`DocumentSplitters`、`EmbeddingModel`、`InMemoryEmbeddingStore` 等——**牺牲行数，换旋钮**。

## 2. 项目设计：小胖、小白与大师的对话

**小胖**：Naive 是不是「菜」的意思？上线就用它行吗？

**小白**：Naive 能当 **MVP** 吗？为啥示例里 **Chat 走云、embed 走本地 ONNX**？**ingest 能离线跑吗？**

**大师**：Naive 是 **可调试基线**；用户量与合规上来要补 **元数据、鉴权、混合检索**。示例展示 **解耦**：聊天与嵌入可 **不同部署** 以省费/满足数据主权。**ingest 应能离线批处理**，在线仅 query。**技术映射**：**Naive = 显式六步，非功能全集**。

**小胖**：topK 取多少？咋知道「检索错了」？

**大师**：产品试验常 **3～8**；要看 **片段长度与窗口**。判断错片段：**记录召回文本与分数**（调试）+ **黄金问答集**回归。**技术映射**：**评测要盯召回，不只最终自然语言**。

---

## 3. 项目实战

### 环境准备

- [`Naive_RAG_Example.java`](../../langchain4j-examples/rag-examples/src/main/java/_2_naive/Naive_RAG_Example.java)；本地 ONNX embed 依赖与文档路径。

### 分步实现

阅读 `createAssistant` 链：**ChatModel → loadDocument → parser → splitter → embedAll → embeddingStore → EmbeddingStoreContentRetriever → AiServices**，在纸上画 **七个方框**。

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

| 对比 | 你能在日志里看到什么？ | Easy RAG 同款信息从哪来？ |
|------|------------------------|----------------------------|
| splitter | 自己设 | 默认/ingestor 内部 |
| embed 模型 | `BgeSmall...` 显式 | classpath 默认 |

**闯关**：调长 `chunk` 问跨段问题；**只 embed 一半** 观察胡编 → **负样本**。

### 测试验证

- snapshot **召回列表**（脱敏）与 **黄金集** top@k；**空召回** 断言应用提示。

### 完整代码清单

[`Naive_RAG_Example.java`](../../langchain4j-examples/rag-examples/src/main/java/_2_naive/Naive_RAG_Example.java)。

---

## 4. 项目总结

### 优点与缺点（与同类做法对比）

| 维度 | Naive 显式管线 | Easy RAG | 低层 RAG（第 20 章） |
|------|----------------|----------|----------------------|
| 可调试性 | 高 | 低 | 最高 |
| 样板代码 | 多 | 少 | 最多 |
| 口语检索 | 脆弱 | 同左 | 同左 |
| 典型缺点 | 无高级策略 | 黑箱 | 维护成本 |

### 适用场景

- 内部培训、首次 **向量库接现网** 前验证。

### 不适用场景

- **亿级文档、强 ACL**——须后续章节与生产向量库。

### 注意事项

- **嵌入与聊天模型语言对齐**；**索引版本** 进发布单。

### 常见踩坑经验（生产向根因）

1. **忘记 embed / 重复 embed** 未去重。  
2. **切分太小/太大** → 丢上下文或噪声。  
3. **评测只看最终回答** 不看召回片段。

### 进阶思考题

1. 同一文档 **两次 ingest** segment 数不一致时，如何 **定位随机性或并发**？  
2. 何时引入 **第 21 章压缩** 而非调大 chunk？

### 推广计划提示（多部门）

| 角色 | 建议阅读顺序 | 协作要点 |
|------|----------------|----------|
| **开发** | 第 18 章 → 本章 → 第 20 章 | **七框图** 必画 |
| **测试** | 召回 snapshot | **空召回** 断言 |
| **运维** | 批索引配额 | **向量库磁盘/内存** |

---

### 本期给测试 / 运维的检查清单

**测试**：snapshot **召回列表**（脱敏）与 **黄金集**比对 top@k；对 **空召回**断言应用提示。  
**运维**：批索引 **资源配额**；**向量库磁盘**或 **内存**阈值。

### 附录：相关 Maven 模块与源码类

| 模块 | 说明 |
|------|------|
| `langchain4j` | `DocumentSplitter`、`EmbeddingStoreIngestor` |
| `embeddings/*` | 如 `BgeSmallEnV15QuantizedEmbeddingModel` |

推荐阅读：`Naive_RAG_Example.java` 顶部注释、`EmbeddingStoreContentRetriever`。
