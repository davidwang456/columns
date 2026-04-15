# 第 20 章：低层 Naive RAG —— 掌控每一步

## 1. 项目背景

### 业务场景（拟真）

性能团队发现 **embed 占 P95 70%+**，合规要求 **每条 segment 带业务 metadata** 可审计；需要把 **ingest 每一步**摊在桌上：**何时解析、切分、生成 Embedding、写入 store**，以便 **断点与批处理对接**（Spark/Flink）。

### 痛点放大

第 19 章已显式，**低层示例**进一步 **细粒度 API 顺序**（以 `_01_Low_Level_Naive_RAG_Example.java` 为准）。若 **只会用 EmbeddingStoreIngestor 默认**：**优化**无法插入批并行；**审计**无法逐段打标签。代价是 **样板膨胀**——需 **组织级 ingest SDK** 收敛。

## 2. 项目设计：小胖、小白与大师的对话

**小胖**：低层是不是跑得更快？

**小白**：低层 = **性能更好** 吗？**何时必须下到低层**？团队会不会 **复制粘贴爆炸**？

**大师**：**不必然更快**——性能来自 **批处理、并行、索引、正确分段**；低层是 **可插入优化**。必须低层时：**自定义 metadata**、**替换 splitter**、**观测每步耗时**、**离线 Spark 作业**。**样板多** → 沉淀 **内部 SDK**。**技术映射**：**低层 = 白盒，不等于自动快**。

**小胖**：并行 embed 要注意啥？

**小白**：低层和 **`EmbeddingStoreIngestor`** 冲突吗？**日志会爆炸吗？**

**大师**：**限速、重试、配额**；**非确定性顺序** 要写入评测策略。两条路线：**ingestor 默认封装** vs **手写全控**——**二选一文档化**。日志对 **分段正文** **截断+脱敏**，仅 debug 全文。**技术映射**：**幂等批处理 = 重建索引生命线**。

---

## 3. 项目实战

### 环境准备

- [`_01_Low_Level_Naive_RAG_Example.java`](../../langchain4j-examples/rag-examples/src/main/java/_4_low_level/_01_Low_Level_Naive_RAG_Example.java)。

### 分步实现

纸笔绘制：`Document → segments → embeddings → store`。

```java
long t0 = System.nanoTime();
// 每个阶段后:
// log.info("stage=X ms={}", (System.nanoTime()-t0)/1_000_000);
```

- embed **>70%** → 评估 **GPU/ONNX/批大小**；**parser 慢** → 先换 **PDF/Tika**（第 31 章）。  
- **趣味**：同一文档跑两次 **segment 数是否一致**——不一致＝**随机/并发** 记入 SRE 手册。

### 测试验证

- 分段 **契约**（段数、段长范围）；批任务 **重试与死信**。

### 完整代码清单

[`_01_Low_Level_Naive_RAG_Example.java`](../../langchain4j-examples/rag-examples/src/main/java/_4_low_level/_01_Low_Level_Naive_RAG_Example.java)，与 `Naive_RAG_Example` diff。

---

## 4. 项目总结

### 优点与缺点（与同类做法对比）

| 维度 | 低层手写 | ingestor 默认 | Naive（第 19 章） |
|------|----------|----------------|-------------------|
| 可观测 | 最高 | 低 | 高 |
| 样板量 | 多 | 少 | 中 |
| 灵活度 | 最高 | 低 | 中 |
| 典型缺点 | 新人慢 | 难优化热点 | 介于中间 |

### 适用场景

- PoC 后 **性能调优**、**合规审计**、**离线重建索引**、与 **Spark/Flink** 衔接。

### 不适用场景

- **团队无平台能力沉淀**——低层代码会腐烂。

### 注意事项

- **版本化**：分段参数与嵌入模型写入 **索引元信息**；**幂等** 去重。

### 常见踩坑经验（生产向根因）

1. **并行**导致 **评测不可重复**。  
2. **分段边界** 切在表格行内 → 语义破碎。  
3. **只盯 LLM** 不盯 **embed 队列积压**。

### 进阶思考题

1. 如何将 **ingest 元数据**（如 `splitterProfile=v2`）与 **黄金集版本** 绑定？  
2. **双写** 重建索引时如何做 **蓝绿切换**？

### 推广计划提示（多部门）

| 角色 | 建议阅读顺序 | 协作要点 |
|------|----------------|----------|
| **开发** | 第 19 章 → 本章 | **组织级 SDK** |
| **SRE** | Profiling 插头 | **embed CPU vs 向量写入 QPS** 分限流 |
| **数据** | 批任务 | **独立扩容** |

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
