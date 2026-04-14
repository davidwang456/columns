# 第 30 章：EmbeddingStore —— 抽象与相似度

## 1. 项目背景

`EmbeddingStore<TextSegment>` 是 RAG **写入与查询** 的枢纽：保存 **向量**、**原文片段**、**metadata**，查询时按 **相似度**返回 **`EmbeddingMatch`** 列表。`InMemoryEmbeddingStore` **仅限开发/小规模**；生产常用 **pgvector、Milvus、OpenSearch** 等（各自独立模块）。

相似度 **度量**（余弦、点积、L2）与 **索引结构**（HNSW、IVF）由具体后端决定；LangChain4j 抽象层确保 **业务代码**主要面对 **「add / findRelevant」** 语义。

## 2. 项目设计：大师与小白的对话

**小白**：内存和远端 store 接口真一样吗？

**大师**：**调用形态**一致；**一致性、持久化、HA** 由后端解决。

**小白**：为何要 metadata？

**大师**：**过滤、溯源、删除** 都靠它；仅有向量不够。

**小白**：同一 segment 更新呢？

**大师**：定义 **业务主键**（文档版本+偏移）；更新策略 **先删后加** 或 **upsert**（视后端）。

**小白**：多租户隔离？

**大师**：**独立 collection** 或 **metadata 强制过滤**；后者需 **严格测试**防漏。

**小白**：向量维度不一致会怎样？

**大师**：**立刻失败**——属于 **配置错误**而非软降级。

## 3. 项目实战：主代码片段

> **场景入戏**：`InMemoryEmbeddingStore` 是 **宿舍抽屉**——演示够用，一断电 **全没**；**pgvector / Milvus** 是 **租仓库**：要 **合同（连接串）、门牌（collection/index）、层高（维度）**——接口相同，**运维剧本**完全不同。

复习 `InMemoryEmbeddingStore` 使用（Easy/Naive 示例均有）：

```java
EmbeddingStore<TextSegment> embeddingStore = new InMemoryEmbeddingStore<>();
// embeddingStore.add(id, embedding, textSegment);
// List<EmbeddingMatch<TextSegment>> matches = embeddingStore.findRelevant(queryEmbedding, maxResults);
```

打开并对比（任选其二，以仓库为准）：

- [`PgVectorEmbeddingStoreExample.java`](../../langchain4j-examples/pgvector-example/src/main/java/PgVectorEmbeddingStoreExample.java)  
- [`MilvusEmbeddingStoreExample.java`](../../langchain4j-examples/milvus-example/src/main/java/MilvusEmbeddingStoreExample.java)

抄一页笔记：**JDBC URI / host-port**、**索引或 collection 名**、**向量维度** 与 **`EmbeddingModel` 是否同表**。

#### 挖深一层

- **带 metadata 过滤**时，对照 [`PgVectorEmbeddingStoreWithMetadataExample.java`](../../langchain4j-examples/pgvector-example/src/main/java/PgVectorEmbeddingStoreWithMetadataExample.java) —— **DB 侧索引**与 **应用 filter** 谁慢，第 25 章会继续打结。

### 延伸案例（情景演练）：从内存库迁到 pgvector 的一周

**背景**：某 B2B SaaS 先用 `InMemoryEmbeddingStore` 在内部分享会上演示「政策问答」，产品大为认可；第二周就要 **小流量内测**。团队周一在预发环境切换 **`langchain4j-pgvector`**，沿用 **1536 维**嵌入，却忘了在 **Flyway** 里给 `metadata->>'tenantId'` 建 **BTREE/GIN** 索引，导致 **带过滤** 的 `findRelevant` **P95 从 40ms 飙到 2s**。**处置**：**(1)** DBA 补索引；**(2)** 应用侧加 **查询日志** 打印 filter 指纹；**(3)** 压测脚本按 **租户维度** 分段。

**后续教训**：周三又发现 **旧内存环境与 PG 环境的 segment id 策略不同**，回放 **黄金问答集** 时 **hit@5 跌了 8%**。根因是 **切分参数**在两条管线不一致（内存库测试时用了默认，PG 重建时换了 `chunkSize`）。团队在 **ingest 元数据**里写入 **`splitterProfile=v2`**，并把评测集与之一并 **版本化**。这个故事适合作为 **「EmbeddingStore 不只是换驱动」** 的复盘分享：接口相同，**运维与评测契约** 必须一起迁移。

## 4. 项目总结

### 优点

- **统一抽象**减少换库重写。**缺点**：高级索引参数需读 **各后端文档**。

### 适用场景

- 任意 **语义检索**后台。

### 注意事项

- **备份**与 **恢复演练**。  
- **监控**查询延迟与 **召回条数**分布。

### 常见踩坑

1. **开发用内存、生产才换库** 未提前压测。  
2. **filter** 与 **索引字段**不一致导致 **全表扫**。  
3. **无容量规划**向量爆盘。

---

### 本期给测试 / 运维的检查清单

**测试**：对 `findRelevant` **契约测试**（topK、filter）；**迁移**脚本测试。  
**运维**：**磁盘/GPU/索引段**监控；**慢查询**日志。

### 附录

类：`InMemoryEmbeddingStore`。接口：`EmbeddingStore`。示例：`pgvector-example`、`milvus-example`。
