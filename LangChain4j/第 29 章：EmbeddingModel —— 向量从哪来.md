# 第 29 章：EmbeddingModel —— 向量从哪来

## 1. 项目背景

**Embedding（嵌入）** 把文本变为 **高维向量**，使语义相近的句子在几何距离上接近。RAG 的 **召回质量上限** 很大程度上由嵌入模型决定：语言域（中英混合）、领域（法律/代码/客服）、**向量维度**、**是否量化**，都会影响 **精度与吞吐**。Aggregator 下 `embeddings/langchain4j-embeddings-*` 提供多种 **可嵌入 JAR 的本地模型**（如 MiniLM、BGE 系列量化版），云端亦可用各 provider 的 `EmbeddingModel` 实现。

在第 19 章 `Naive_RAG_Example` 中你已见过 **`BgeSmallEnV15QuantizedEmbeddingModel`** 一类构造：它强调 **本地 CPU 上跑 embed**，减轻调用云 API 的成本与合规压力。

## 2. 项目设计：大师与小白的对话

**小白**：聊天模型能直接嵌入吗？

**大师**：一般 **不行**；嵌入是 **单独的模型家族**（也有多任务大模型但部署形态不同）。

**小白**：换嵌入模型要重建索引吗？

**大师**：**要**（除非做专门的迁移映射，一般不划算）。视为 **破坏性变更**，要 **蓝绿索引**。

**小白**：量化和全精度怎么选？

**大师**：**量化**省内存、加速推理，**略损召回**；可用 **离线对比 hit@k** 决策。

**小白**：批量嵌入如何吞吐？

**大师**：**批大小**、**线程池**、**ONNX runtime** 参数；注意 **CPU pinning** 在容器内。

**小白**：多语言文档怎么办？

**大师**：选 **多语言向量模型** 或 **分语言分索引** 后 **融合**。

**小白**：合规角度？

**大师**：**文本是否出境**到第三方 embed API；本地模型可 **数据主权** 友好。

## 3. 项目实战：主代码片段

> **场景入戏**：嵌入模型是你的 **「母语口音」**：换模型就像整队配音从 **BBC** 换成 **方言综艺**——**语义空间**变了，旧索引 **不翻译**直接查 = **指鹿为马**。故：**存与搜**必须 **同一模型 + 版本号入 metadata**（第 24 章伏笔）。

在 [`langchain4j/pom.xml`](../../langchain4j/pom.xml) 中浏览 `embeddings/` 下模块列表，任选两个：

1. 记录 **artifact 名** 与 **适用语言**（读模块 README 或类 Javadoc）。  
2. 在 [`Naive_RAG_Example.java`](../../langchain4j-examples/rag-examples/src/main/java/_2_naive/Naive_RAG_Example.java) 中替换 `EmbeddingModel` 实现（若版本兼容），对同一查询比较 **top3 片段是否变化**（定性）。

| 闯关 | 任务 |
|------|------|
| ★ | 打印当前 **`Embedding.name()` / 维度`**，贴在 README。 |
| ★★ | 同句查询 **中英各一句**，主观对比 **top3 是否「懂语境」**。 |
| ★★★ | 思考：**embedding 与 chat 不同厂商**时，幻觉风险在哪？写 **一行**结论给架构评审。 |

**伪代码 — 批量嵌入：**

```java
// List<TextSegment> segments = ...
// List<Embedding> embeddings = embeddingModel.embedAll(segments).content();
// for (...) embeddingStore.add(embeddingId, embedding, segment);
```

具体 API 以当前 `EmbeddingModel` 为准。

## 4. 项目总结

### 优点

- 本地模型 **成本可控**、**延迟稳定**。  
- **可离线**批处理。

### 缺点

- **运维**需关注 **CPU/内存**与 **模型文件分发**。  
- **版本升级**需 **重建索引**流程。

### 适用场景

- 大批量 **历史文档索引**。  
- **数据不出域**的政企环境。

### 注意事项

- **维度一致性**：存与搜必须用 **同一模型版本**。  
- **文本预处理**（空白、全角）与训练时一致。

### 常见踩坑

1. **中英文混用错选纯英文模型**。  
2. **embed 与 chat 语言不一致** 导致答案飘。  
3. **无版本号**记录了模型导致 **无法复现**线上问题。

---

### 本期给测试 / 运维的检查清单

**测试**：黄金集 **向量相似度阈值**回归；嵌入 **批量任务** 的 **幂等键**。  
**运维**：镜像内 **模型文件缓存层**；**CPU limit** 与 **HPA** 对齐批任务。

### 附录：相关 Maven 模块与源码类

| 模块 | 说明 |
|------|------|
| `embeddings/langchain4j-embeddings-*` | 本地 ONNX 等实现 |
| `langchain4j-core` | `EmbeddingModel`、`Embedding` |

推荐阅读：各 embed 模块 `README`、`Naive_RAG_Example` 中的 `EmbeddingModel` 构造。
