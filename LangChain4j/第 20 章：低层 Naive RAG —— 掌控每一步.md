# 第 20 章：低层 Naive RAG —— 掌控每一步

## 1. 项目背景

性能团队发现 embed 占 P95 的 70%+，合规要求每条 segment 带业务 metadata 可审计。需要把 ingest 每一步摊在桌上：何时解析、切分、生成 Embedding、写入 store，以便断点续传与批处理对接（Spark/Flink）。

教程文件：`rag-examples/src/main/java/_4_low_level/_01_Low_Level_Naive_RAG_Example.java`

## 2. 项目设计：小胖、小白与大师的对话

**小胖**：低层是不是跑得更快？

**小白**：低层性能更好吗？何时必须下到低层？会不会样板太多变成复制粘贴地狱？

**大师**：低层**不必然更快**——性能来自批处理、并行、索引、正确分段。低层给你的是可插入优化的能力。必须下低层的场景：自定义 metadata、替换 splitter、每步耗时观测、离线 Spark。样板多就沉淀内部 SDK。**技术映射**：**低层 = 白盒可插入优化，不等于自动快**。

## 3. 项目实战

### 步骤 1：细粒度分段与耗时统计

```java
long t0 = System.nanoTime();

// 1. 加载
Document document = loadDocument(toPath("documents/"), new TextDocumentParser());
log("Load", t0);

// 2. 切分
DocumentSplitter splitter = DocumentSplitters.recursive(300, 30);
List<TextSegment> segments = splitter.split(document);
log("Split", t0);
System.out.println("Segments: " + segments.size());

// 3. 嵌入
EmbeddingModel embeddingModel = new BgeSmallEnV15QuantizedEmbeddingModel();
List<Embedding> embeddings = embeddingModel.embedAll(segments).content();
log("Embed", t0);

// 4. 写入
EmbeddingStore<TextSegment> store = new InMemoryEmbeddingStore<>();
store.addAll(embeddings, segments);
log("Store", t0);
```

```java
static void log(String stage, long start) {
    long ms = (System.nanoTime() - start) / 1_000_000;
    System.out.println(stage + ": " + ms + "ms");
}
```

### 步骤 2：自定义 metadata

```java
// 为每个 segment 添加 metadata
for (int i = 0; i < segments.size(); i++) {
    TextSegment segment = segments.get(i);
    segment.metadata().put("docId", document.metadata().getString("id"));
    segment.metadata().put("chunkIndex", String.valueOf(i));
    segment.metadata().put("version", "1.0");
}
```

### 步骤 3：幂等批处理去重

```java
// 用 docId + chunkIndex 判重
Set<String> existingIds = new HashSet<>();
// 查询已存在的 ID ...
List<TextSegment> newSegments = segments.stream()
    .filter(s -> !existingIds.contains(
        s.metadata().getString("docId") + "_" + s.metadata().getString("chunkIndex")))
    .collect(Collectors.toList());
```

### 闯关任务

| 难度 | 动手 | 过关标准 |
|------|------|----------|
| ★ | 在哪步最耗时？ | 发现瓶颈（通常 embed > 70%） |
| ★★ | 同一文档跑两次 segment 数是否一致 | 检查随机性或并发问题 |
| ★★★ | 并行 embed 后 batch 大小调优 | 找到最优线程数 |

### 可能遇到的坑

| 坑 | 表现 | 解法 |
|----|------|------|
| 并行 embed 限流 | QPS 过高被拒 | 加限速和队列 |
| 日志包含分段正文 | 磁盘空间暴涨 | 截断+脱敏 |
| 只在 embeddingStoreIngestor 默认路径 | 无法优化热点 | 低层 + 内部 SDK |

### 测试验证

```bash
# 分段契约：段数范围、段长范围
# 批任务：重试与死信队列
```

### 完整代码清单

[`_01_Low_Level_Naive_RAG_Example.java`](../../langchain4j-examples/rag-examples/src/main/java/_4_low_level/_01_Low_Level_Naive_RAG_Example.java)

## 4. 项目总结

### 优点与缺点

| 维度 | 低层手写 | ingestor 默认 | Naive |
|------|---------|-------------|-------|
| 可观测 | 最高 | 低 | 高 |
| 样板量 | 多 | 少 | 中 |
| 灵活度 | 最高 | 低 | 中 |

### 适用 / 不适用场景

**适用**：PoC 后性能调优、合规审计、离线重建索引、Spark/Flink 衔接。

**不适用**：团队无平台能力沉淀——低层代码会腐烂。

### 常见踩坑

1. 并行导致评测不可重复
2. 分段边界切在表格行内 → 语义破碎
3. 只盯 LLM 不盯 embed 队列积压

### 进阶思考题

1. 如何将 ingest 元数据（如 splitterProfile=v2）与黄金集版本绑定？
2. 双写重建索引时如何做蓝绿切换？

### 推广计划

| 角色 | 建议阅读顺序 | 协作要点 |
|------|-------------|----------|
| 开发 | 第 19 章 → 本章 | 内部 SDK 化 |
| SRE | Profiling | embed CPU vs 写入 QPS 分限流 |
| 数据 | 批任务 | 独立扩容 |

### 检查清单

- **测试**：分段结果契约测试（段数/段长范围）
- **运维**：批任务独立扩容；embed CPU 与向量库写入 QPS 分开限流

### 附录

| 模块 | 说明 |
|------|------|
| `langchain4j` | `DocumentSplitter`、`EmbeddingStore` |
| `langchain4j-core` | `Embedding`、`TextSegment` |

推荐阅读：`_01_Low_Level_Naive_RAG_Example.java`，与 `Naive_RAG_Example` diff。
