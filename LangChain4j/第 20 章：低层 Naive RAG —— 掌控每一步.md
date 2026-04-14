# 第 20 章：低层 Naive RAG —— 掌控每一步

## 1. 项目背景

在第 19 章的 Naive RAG 中，你已经看到「显式管线」的优势；**低层示例**进一步把 **ingest 的每一步**摊在桌面上：何时解析、何时切分、何时生成 `Embedding`、何时写入 `EmbeddingStore`。这样做的目的，是在 Performance 或 **合规审计** 需要「**逐步验尸**」时，你能精确地在 **某一阶段打断点**。

文件路径：`langchain4j-examples/rag-examples/src/main/java/_4_low_level/_01_Low_Level_Naive_RAG_Example.java`。与 Easy RAG 相比，它牺牲简洁换取 **白盒**；与 Naive 相比，它通常 **更细粒度**地展示 API 调用顺序（以仓库为准）。

## 2. 项目设计：大师与小白的对话

**小白**：低层是不是代表「性能更好」？

**大师**：**不必然**。性能来自 **批处理、并行、向量库索引** 与 **正确的分段**；低层只是让你更容易插入这些优化。

**小白**：什么时候必须下到低层？

**大师**：当你需要 **自定义 metadata**、**替换 splitter**、**观测每一步耗时**、或将嵌入任务 **拆成离线 Spark 作业** 时。

**小白**：低层会不会让团队复制粘贴大量样板？

**大师**：会。应在内部沉淀 **「组织级 ingest SDK」**：公开少数参数，隐藏样板。

**小白**：并行嵌入要注意什么？

**大师**：**限速**、**重试**、**配额**；并对 **非确定性顺序**做好评测（同文档多次 ingest 的 id 策略）。

**小白**：调试时日志会不会爆炸？

**大师**：对 **分段正文**日志要 **截断** 与 **脱敏**；仅 debug 打印全文。

**小白**：低层与 `EmbeddingStoreIngestor` 冲突吗？

**大师**：它是两条路线：**ingestor 封装默认** vs **手写管道全控**。团队二选一并文档化。

## 3. 项目实战：主代码片段

> **场景入戏**：低层 RAG 像 **公开厨房**——每个锅 (`splitter`)、每把刀 (`parser`) 都摆在台上，**食客（你）能看见油烟往哪飘**；最适合 **性能工程师**和 **和法务一起盯 metadata** 的人。

请你本地打开 [`_01_Low_Level_Naive_RAG_Example.java`](../../langchain4j-examples/rag-examples/src/main/java/_4_low_level/_01_Low_Level_Naive_RAG_Example.java)，按函数顺序 **用纸笔** 画出 pipeline：`Document → segments → embeddings → store`。

#### Profiling 插头（复制粘贴后记得删）

```java
long t0 = System.nanoTime();
// 每个阶段后:
// log.info("stage=X ms={}", (System.nanoTime()-t0)/1_000_000);
```

#### 深度读数

- 若 **embed** 吃掉 **>70%** 时间：评估 **GPU/ONNX**、**批大小**、或 **是否该上云端 embed**。  
- 若 **parser** 很慢：别想向量了，先换 **Tika/PDF** 管线（第 31 章）。  
- **趣味挑战**：给同一文档跑两次，比较 **segment 数量是否完全一致**——不一致＝有 **随机性**或 **并发**，要写入 **SRE 手册**。

## 4. 项目总结

### 优点

- **逐步可观测**，问题定位快。  
- 最灵活地插入 **业务元数据**。

### 缺点

- **样板多**，需要团队抽取复用。  
- 新人 **上手慢**。

### 适用场景

- PoC 后 **性能调优**、**合规审计**、**离线重建索引**。  
- 与 **Spark/Flink** 批处理衔接。

### 注意事项

- **版本化**：分段参数与嵌入模型版本写入 **索引元信息**。  
- **幂等**：重复跑批不应 **double write** 除非去抖。

### 常见踩坑

1. **并行**导致 **评测不可重复**。  
2. **分段边界**落在表格行内，召回语义破碎。  
3. **监控只盯 LLM** 不盯 **embed 队列积压**。

---

### 本期给测试 / 运维的检查清单

**测试**：对分段结果做 **契约测试**（段数范围、段长范围）；批处理任务做 **重试与死信队列**校验。  
**运维**：批任务 **独立扩容**；对 **embed CPU** 与 **向量库写入 QPS** 分开限流。

### 附录：相关 Maven 模块与源码类

| 模块 | 说明 |
|------|------|
| `langchain4j` | `DocumentSplitter`、`EmbeddingStore` |
| `langchain4j-core` | `Embedding`、`TextSegment` |

推荐阅读：`_01_Low_Level_Naive_RAG_Example.java`，并与 `Naive_RAG_Example` diff。
