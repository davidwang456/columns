# 第 16 章：ChatWithDocuments —— 对话 + 检索入门

## 1. 项目背景

在深入 `rag-examples` 目录前，`ChatWithDocuments` 示例把「**助理既能聊天，又能查私域文档**」收缩为一条清晰的 **`AiServices`** 装配路径：核心是给 Assistant **content retriever**。它 bridges **第 12 章 AiServices** 与 **第五部分系统化 RAG**：让你先理解「**多了一个依赖组件**」，再进入 ingest、切分、向量库选型。

示例文件：`langchain4j-examples/tutorials/src/main/java/_12_ChatWithDocumentsExamples.java`（若版本调整请以仓库为准）。本章强调：**ChatWithDocuments 不是独立魔法类名**，而是「**模式名称**」——具体 API 以示例内 `AiServices.builder(...).contentRetriever(...)` 为准。

## 2. 项目设计：大师与小白的对话

**小白**：这和 Easy RAG 有什么界限？

**大师**：**目标体验相近**；差别在 **透明度**：Easy RAG 把 ingest 藏在 `EmbeddingStoreIngestor` 默认里，本示例更倾向 **让你看到管线拼装**（以仓库实现为准）。

**小白**：我能指定 topK 吗？

**大师**：通常在 **`EmbeddingStoreContentRetriever` 的 builder** 或等价工厂方法中；请读示例代码与当前 Javadoc。

**小白**：对话记忆会让检索变差吗？

**大师**：**会话噪声**可能稀释相关性——需要 **查询重写**（第 21 章）或 **分离检索 query**。

**小白**：只想对某些意图启用文档怎么办？

**大师**：在 **路由层**决定 `ContentRetriever` 是否参与（第 26 章「跳过检索」呼应）。

**小白**：用户体验上要不要展示「引用片段」？

**大师**：B 端常要 **来源列表**；要实现需 **Return sources** 模式（第 24 章）。

## 3. 项目实战：主代码片段

> **场景入戏**：`contentRetriever` 是 **图书馆员**（帮你翻内部纸），`chatModel` 是 **电台主持人**（把纸念顺）。**不要让主持人假装自己是 ERP**——实时库存请走 **Tool**。

请打开 [`_12_ChatWithDocumentsExamples.java`](../../langchain4j-examples/tutorials/src/main/java/_12_ChatWithDocumentsExamples.java)，定位（以仓库为准）：

```java
// AiServices.builder(Assistant.class)
//     .chatModel(...)
//     .contentRetriever(...)
//     .build();
```

#### 三方对照表（家庭作业）

| 维度 | ChatWithDocuments 本示例 | Easy RAG（第 18 章） | Naive（第 19 章） |
|------|-------------------------|----------------------|-------------------|
| ingest 谁负责 | 读源码确认 | `EmbeddingStoreIngestor` 一行 | 手写管线 |
| 适合培训谁 | 已会 `AiServices` 的人 | **最快产出 demo** | **要调参的人** |

**任务：** 写出三类业务问题：  
（1）仅需模型常识；（2）必须命中 wiki；（3）需结合实时状态——并标注 **是否走检索 / 是否走工具**。

### 延伸案例（情景演练）：产品文档 + 实时库存的「双线」

**场景**：消费电子品牌的 **官网助理** 同时要回答 **保修条款**（静态 PDF 入库）与 **当前SKU库存**（实时系统）。团队在 **`ChatWithDocuments` 模式** 上增加 **`@Tool querySku(String sku)`**：**检索仍只负责政策与 FAQ**，库存 **绝不** 期望从向量库「猜」出来。

**插曲**：上线初用户问「**我的序列号能不能保修**」，模型 **先从文档里推断**「一般一年」，却 **未** 调 **`lookupWarranty(serial)`** 工具——因为 **提示里文档摘要过强**。修复：**(1)** 在系统提示写清 **顺序**：涉及 **序列号/金额** 必须 **先工具后自由发挥**；**(2)** 对 **工具失败**显式展示。**该案例说明**：`contentRetriever` 解决「**有什么文字**」，**工具**解决「**系统真相**」，二者 **不可混层**。

## 4. 项目总结

### 优点

- **门槛平滑**：仍在 `AiServices` 心智模型内。  
- 方便从 tutorial **过渡到 rag-examples**。

### 缺点

- **命名易误解** 为单一类。  
- **进阶调优**仍需 RAG 专章。

### 适用场景

- 产品原型与 **培训演示**。  
- 团队对齐「对话 + 知识」需求。

### 注意事项

- **空库**体验与 **提示文案**。  
- **隐私**：用户问题是否可泄露给向量库日志。

### 常见踩坑

1. **ingest 未做**却期望能答文档内事实。  
2. **检索片段未展示**却要求用户信任答案。  
3. **超大文档**未分页预览导致调试困难。

---

### 本期给测试 / 运维的检查清单

**测试**：对 **无索引** / **坏索引** / **精确命中** 三态设计用例；验证 **引用块**与 UI 一致性。  
**运维**：监控 **检索耗时占比**；单独对 **ingest 批任务**设资源配额。

### 附录：相关 Maven 模块与源码类

| 模块 | 说明 |
|------|------|
| `langchain4j` | `AiServices`、`EmbeddingStoreContentRetriever` |

推荐阅读：`_12_ChatWithDocumentsExamples.java` → 衔接 `Easy_RAG_Example.java`。
