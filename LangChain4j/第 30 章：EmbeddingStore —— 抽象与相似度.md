# 第 30 章：EmbeddingStore —— 抽象与相似度

## 1. 项目背景

### 业务场景（拟真）

`EmbeddingStore<TextSegment>` 是 RAG **写入与查询** 枢纽：保存 **向量、原文、metadata**，查询返回 **`EmbeddingMatch`**。PoC 用 `InMemoryEmbeddingStore`；生产用 **pgvector、Milvus、OpenSearch** 等——**运维剧本** 不同，**接口** 统一。

### 痛点放大

**开发内存、生产才换库** 未压测 → **P95 暴雷**；**metadata 过滤** 无 DB 索引 → **全表扫**；**向量维度** 与 `EmbeddingModel` 不一致 → **立刻失败**。相似度 **度量与索引结构**（HNSW、IVF）由后端决定。

## 2. 项目设计：小胖、小白与大师的对话

**小胖**：内存库像宿舍抽屉，pgvector 像租仓库？

**小白**：**内存和远端接口真一样吗**？**metadata 为啥重要**？**多租户隔离**？

**大师**：**调用形态**一致；**HA/持久化** 后端负责。**metadata** 用于 **过滤、溯源、删除**。**独立 collection** 或 **强制 metadata 过滤**——后者须 **严格测试** 防漏。**技术映射**：**维度不一致 = 配置错误硬失败**。

**小胖**：同一 segment 更新呢？

**小白**：**向量维度不一致** 会怎样？

**大师**：**业务主键**（文档版本+偏移）；**先删后加或 upsert**。维度错 **立刻失败**——非软降级。**技术映射**：**EmbeddingStore 契约跨后端一致**。

---

## 3. 项目实战

### 环境准备

- `InMemoryEmbeddingStore`（Easy/Naive 示例）；任选 [`PgVectorEmbeddingStoreExample.java`](../../langchain4j-examples/pgvector-example/src/main/java/PgVectorEmbeddingStoreExample.java)、[`MilvusEmbeddingStoreExample.java`](../../langchain4j-examples/milvus-example/src/main/java/MilvusEmbeddingStoreExample.java)。

### 分步任务

```java
EmbeddingStore<TextSegment> embeddingStore = new InMemoryEmbeddingStore<>();
// embeddingStore.add(id, embedding, textSegment);
// List<EmbeddingMatch<TextSegment>> matches = embeddingStore.findRelevant(queryEmbedding, maxResults);
```

抄笔记：**JDBC URI、索引/collection、维度** 与 **`EmbeddingModel` 同表**。

**延伸**：metadata 过滤对照 [`PgVectorEmbeddingStoreWithMetadataExample.java`](../../langchain4j-examples/pgvector-example/src/main/java/PgVectorEmbeddingStoreWithMetadataExample.java)；**tenantId** 索引缺失 → P95 飙升（第 25 章）。

### 测试验证

- `findRelevant` **契约**（topK、filter）；**迁移** 脚本测试。

### 完整代码清单

`pgvector-example`、`milvus-example`；类 `InMemoryEmbeddingStore`、接口 `EmbeddingStore`。

---

## 4. 项目总结

### 优点与缺点（与同类做法对比）

| 维度 | 统一 EmbeddingStore 抽象 | 直连各 SDK | 仅内存库 |
|------|--------------------------|------------|----------|
| 换库成本 | 低 | 高 | 不适用生产 |
| 高级参数 | 读各后端文档 | 全暴露 | 无 |
| 典型缺点 | 特性矩阵 | 重复代码 | 无 HA |

### 适用场景

- 任意 **语义检索** 后台；**接口相同、运维不同** 的迁移。

### 不适用场景

- **无向量需求**——不必引入。

### 注意事项

- **备份恢复演练**；**监控** 延迟与召回条数分布。

### 常见踩坑经验（生产向根因）

1. **开发内存、生产换库** 未压测。  
2. **filter 与索引字段** 不一致 → 全表扫。  
3. **无容量规划** 向量爆盘。

### 进阶思考题

1. **Flyway** 给 `metadata->>'tenantId'` 建索引的前后对比？  
2. **切分参数** 不一致导致 **hit@5 下跌** 如何 **版本化**？

### 推广计划提示（多部门）

| 角色 | 建议阅读顺序 | 协作要点 |
|------|----------------|----------|
| **开发** | 第 29 章 → 本章 → 第 32 章 | **评测契约随库迁移** |
| **DBA** | pgvector | **GIN/BTREE** 与查询计划 |
| **运维** | 磁盘/GPU/Compaction | **慢查询** |

---

### 本期给测试 / 运维的检查清单

**测试**：对 `findRelevant` **契约测试**（topK、filter）；**迁移**脚本测试。  
**运维**：**磁盘/GPU/索引段**监控；**慢查询**日志。

### 附录

类：`InMemoryEmbeddingStore`。接口：`EmbeddingStore`。示例：`pgvector-example`、`milvus-example`。
