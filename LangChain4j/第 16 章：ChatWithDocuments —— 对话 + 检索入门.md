# 第 16 章：ChatWithDocuments —— 对话 + 检索入门

## 1. 项目背景

### 业务场景（拟真）

在深入 `rag-examples` 前，`ChatWithDocuments` 模式把「**助理既能聊天，又能查私域文档**」收缩为一条 **`AiServices` 装配路径**：给 Assistant 配置 **`contentRetriever`**。它衔接 **第 12 章 AiServices** 与 **第五部分 RAG**：先理解「**多一个依赖组件**」，再学 ingest、切分、向量库。

### 痛点放大

若 **混淆「文档知识」与「实时系统状态」**：用户问库存，模型从 **PDF 里猜**——**事实错误**；若 **不做来源展示**：用户 **无法信任**；若 **ingest 未做** 却期望答文档事实——**空库幻觉**。**ChatWithDocuments** 不是单一魔法类名，而是 **模式名称**；API 以 `_12_ChatWithDocumentsExamples.java` 为准。

## 2. 项目设计：小胖、小白与大师的对话

**小胖**：这跟第 18 章 Easy RAG 是不是重复？

**小白**：和 **Easy RAG** 界限？能指定 **topK** 吗？**记忆会让检索变差吗？**

**大师**：体验相近；差别在 **透明度**——本示例更倾向 **让你看到管线拼装**（以仓库为准）。topK 通常在 **`EmbeddingStoreContentRetriever` builder**（读 Javadoc）。**会话噪声** 可能稀释相关性——需 **查询重写**（第 21 章）或 **分离检索 query**。**技术映射**：**检索 query ≠ 闲聊全文**。

**小胖**：库存这种实时数能从文档里搜吗？

**小白**：**只对某些意图启用文档**？要不要展示 **引用**？

**大师**：**实时库存走 Tool**，向量库只放 **政策/FAQ**（见本章延伸案例）。意图路由决定是否挂载 `ContentRetriever`（第 26 章）。B 端常要 **来源列表**（第 24 章）。**技术映射**：**contentRetriever = 静态文本证据；Tool = 系统真相**。

---

## 3. 项目实战

### 环境准备

- [`_12_ChatWithDocumentsExamples.java`](../../langchain4j-examples/tutorials/src/main/java/_12_ChatWithDocumentsExamples.java)。

### 分步任务

定位并理解：

```java
// AiServices.builder(Assistant.class)
//     .chatModel(...)
//     .contentRetriever(...)
//     .build();
```

| 维度 | ChatWithDocuments | Easy RAG（第 18 章） | Naive（第 19 章） |
|------|-------------------|----------------------|-------------------|
| ingest | 读源码确认 | `EmbeddingStoreIngestor` 默认 | 手写管线 |

**作业**：写三类问题：（1）仅需常识；（2）必须命中 wiki；（3）需实时状态——标注 **检索/工具**。

**延伸案例**：保修条款（文档）+ SKU 库存（工具）；**序列号保修** 必须先 **工具** 再回答——避免 **文档摘要过强** 跳过工具。

### 测试验证

- **无索引 / 坏索引 / 精确命中** 三态；**引用块** 与 UI 一致。

### 完整代码清单

[`_12_ChatWithDocumentsExamples.java`](../../langchain4j-examples/tutorials/src/main/java/_12_ChatWithDocumentsExamples.java)。

---

## 4. 项目总结

### 优点与缺点（与同类做法对比）

| 维度 | ChatWithDocuments 模式 | 仅 ChatModel | Easy RAG 一行 ingest |
|------|------------------------|--------------|----------------------|
| 心智负担 | 中（仍 AiServices） | 低 | 低 |
| 透明度 | 较高 | 无 RAG | 较低（默认黑箱） |
| 进阶调优 | 需 RAG 专章 | 不适用 | 需后续章 |

### 适用场景

- 原型、培训、团队对齐「对话 + 知识」。

### 不适用场景

- **已确定要上生产级分区 ACL**——需直接按 **第 24、25、32 章** 设计，不单靠本模式。

### 注意事项

- **空库** 提示文案；**隐私**（用户问题是否进向量库日志）。

### 常见踩坑经验（生产向根因）

1. **ingest 未做** 却期望文档事实。  
2. **无引用** 却要求用户信任。  
3. **超大文档** 未分页预览 → 调试困难。

### 进阶思考题

1. 何时把 **ContentRetriever** 换成 **多路融合**（第 27 章）？  
2. **会话轮次多** 时如何 **压缩查询**（第 21 章）避免噪声？

### 推广计划提示（多部门）

| 角色 | 建议阅读顺序 | 协作要点 |
|------|----------------|----------|
| **开发** | 第 12 章 → 本章 → 第 18 章 | **文档 vs 工具** 分层 |
| **产品** | 本章 + 第 24 章 | **来源展示** |
| **运维** | 检索耗时占比 | **ingest 批任务** 配额 |

---

### 本期给测试 / 运维的检查清单

**测试**：对 **无索引** / **坏索引** / **精确命中** 三态设计用例；验证 **引用块**与 UI 一致性。  
**运维**：监控 **检索耗时占比**；单独对 **ingest 批任务**设资源配额。

### 附录：相关 Maven 模块与源码类

| 模块 | 说明 |
|------|------|
| `langchain4j` | `AiServices`、`EmbeddingStoreContentRetriever` |

推荐阅读：`_12_ChatWithDocumentsExamples.java` → 衔接 `Easy_RAG_Example.java`。
