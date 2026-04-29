# 第 30 章：EmbeddingStore —— 抽象与相似度

## 1. 项目背景

### 业务场景（拟真）

`EmbeddingStore<TextSegment>` 是 RAG 的存储与查询枢纽。它保存向量、原文、metadata，查询时返回 `EmbeddingMatch`。开发阶段用 `InMemoryEmbeddingStore`，生产阶段换 pgvector 或 Milvus——**接口一致，运维剧本完全不同**。

### 痛点放大

最常见的坑是：开发时用内存库一切正常，一换到 pgvector 就发现 P95 从 5ms 飙到 200ms。不是因为 pgvector 不好，而是因为没有做压测、没有为 metadata 建立索引、没有限制 `findRelevant` 的 maxResults。另一个经典错误：换了 embedding 模型但忘了改 `EmbeddingStore` 的维度配置——向量维度不匹配直接启动报错。

## 2. 项目设计：小胖、小白与大师的对话

**小胖**：InMemoryEmbeddingStore 像宿舍的抽屉——随手放东西方便，但一断电全没了。pgvector 就像在外面租了个专业仓库？

**大师**：这个比喻很形象。宿舍抽屉（内存库）的好处是：**随取随用，零运维**。坏处是：容量取决于宿舍大小（JVM 堆）、断电就丢失、不能跟隔壁宿舍共享。专业仓库（pgvector）的好处是：容量大、有备份、可以多人共享；代价是：你得办卡（配连接串）、知道仓库的开放时间（运维 SLA）、定期检查货架有没有塌（索引维护）。好在 LangChain4j 的接口统一了这两者——你的业务代码始终调用 `embeddingStore.findRelevant()`，不管后端是抽屉还是仓库。

**小白**：向量维度配错了会出现什么——比如 embedding 模型输出 384 维，但 store 配了 768 维？

**大师**：**不是悄悄降级，而是直接启动报错。** 这反而是好事——比默默用错误维度跑了好几天、数据全废了才发现要好得多。框架会在 `add()` 或 `findRelevant()` 时检查维度是否匹配，不匹配立刻抛异常。所以换 embedding 模型时必须同步重建索引和更新 store 的维度配置。**技术映射**：**EmbeddingStore 的接口契约跨所有后端实现一致，但每个后端的运维特性（备份、扩缩容、compaction）完全不同——选型时不要只看 API，要对比 Runbook**。

---

## 3. 项目实战

### 环境准备

```bash
# pgvector（需要 Docker）
docker run -d --name pgvector -e POSTGRES_PASSWORD=test \
  -p 5432:5432 pgvector/pgvector:pg16

# Milvus（需要 Docker）
docker run -d --name milvus -p 19530:19530 milvusdb/milvus:latest
```

### 分步实现

#### 步骤 1：InMemoryEmbeddingStore 基本用法

```java
EmbeddingStore<TextSegment> store = new InMemoryEmbeddingStore<>();

// 添加
String segmentId = store.add(embedding, TextSegment.from("Return policy: 30 days"));
System.out.println("Added segment: " + segmentId);

// 查询
List<EmbeddingMatch<TextSegment>> matches = store.findRelevant(queryEmbedding, 3);
for (EmbeddingMatch<TextSegment> m : matches) {
    System.out.println("score=" + m.score() + " text=" + m.embedded().text());
}

// 删除
store.remove(segmentId);
```

#### 步骤 2：切换到 pgvector

```java
EmbeddingStore<TextSegment> pgStore = PgVectorEmbeddingStore.builder()
        .host("localhost")
        .port(5432)
        .database("vectordb")
        .user("postgres")
        .password("test")
        .table("embeddings")
        .dimension(384)
        .createTable(true)  // 自动建表
        .build();

// 调用形态与 InMemoryEmbeddingStore 完全一致
pgStore.add(embedding, TextSegment.from("Return policy: 30 days"));
List<EmbeddingMatch<TextSegment>> matches = pgStore.findRelevant(queryEmbedding, 3);
```

#### 步骤 3：性能压力测试

```java
long t0 = System.nanoTime();
int testRuns = 100;
for (int i = 0; i < testRuns; i++) {
    store.findRelevant(queryEmbedding, 5);
}
long totalMs = (System.nanoTime() - t0) / 1_000_000;
System.out.println("P99 (approx): " + (totalMs / testRuns) + "ms per query");
```

### 可能遇到的坑

| 坑 | 表现 | 解法 |
|----|------|------|
| 开发内存、生产才换库 | P95 暴雷 | 上线前用 testcontainers 做集成测 |
| filter 字段无索引 | 全表扫描 | 确保 metadata 建索引 |
| 维度不一致 | 启动报错 | 换模型必须重建索引 |

### 测试验证

```bash
# Testcontainers 在 CI 中启动 pgvector 做集成测试
# 对比 InMemory 和 pgvector 的 findRelevant 结果一致性
```

### 完整代码清单

`pgvector-example`、`milvus-example`、`InMemoryEmbeddingStore`

---

## 4. 项目总结

### 优点与缺点

| 维度 | 统一 EmbeddingStore 抽象 | 直连各 SDK | 仅内存库 |
|------|-------------------------|-----------|---------|
| 换库成本 | 低（一行配置） | 高（重写代码） | 不适用生产 |
| 高级参数 | 各后端文档 | 全暴露 | 无 |
| 典型缺点 | 特性矩阵需查文档 | 重复代码 | 无 HA |

### 适用场景

- 需从 PoC 平滑过渡到生产的 RAG 项目
- 同一应用需支持多种向量库

### 不适用场景

- 无向量检索需求
- 强依赖特定向量库独有功能

### 常见踩坑

1. **开发内存、生产换库未压测** → P95 暴雷
2. **filter 与索引字段不一致** → 全表扫
3. **无容量规划** → 向量爆盘

### 进阶思考题

1. Flyway 给 `metadata->>'tenantId'` 建索引的前后对比 SQL？
2. 切分参数不一致导致 hit@5 下跌，如何版本化管理？

### 推广计划提示

| 角色 | 建议阅读顺序 | 协作要点 |
|------|-------------|----------|
| 开发 | 第 29 章 → 本章 → 第 32 章 | 评测契约随库迁移 |
| DBA | pgvector | GIN/BTREE 索引与查询计划 |
| 运维 | 磁盘/GPU/Compaction | 慢查询日志 |