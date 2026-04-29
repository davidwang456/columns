# 第 23 章：高级 RAG —— 重排序（Re-ranking）

## 1. 项目背景

### 业务场景（拟真）

向量检索用的是 **bi-encoder** 模型——把 query 和文档分别编码成向量，然后算余弦相似度。这种模式快（可以处理上百万文档），但对 **细粒度的语义相关性** 判断不够：比如 query 是「苹果笔记本和 Windows 笔记本的区别」，向量检索可能召回一篇讲「苹果的营养价值」的文章——因为「苹果」这个词的向量很接近。**重排序（Re-ranking）** 用更强的 cross-encoder 模型（把 query 和文档一起送入模型打分）对 topK 候选重新排序，提升最终传给生成模型的质量。

### 痛点放大

代价是额外的延迟与费用。两阶段架构是工程取舍：bi-encoder 负责「海选」（快、能覆盖大量候选），cross-encoder 负责「精选」（慢、但准）。如果只用 bi-encoder 的 top1 直接给 LLM——相关性误差较大。如果要求 cross-encoder 扫描全库——不可接受。

## 2. 项目设计：小胖、小白与大师的对话

**小胖**：为什么不用最强大的那个模型直接做检索？绕一圈搞两阶段不是多此一举吗？

**大师**：因为「最强大的模型」——cross-encoder——的计算复杂度是 O(query × doc)，让它在全库上跑一遍，就相当于把几十万篇文档的每一篇都和你的问题放一起让模型打分——你这是想用显微镜来找掉在操场上的钥匙。两阶段不是技术妥协，是工程最优解：bi-encoder（显微镜变望远镜）快速定位钥匙大概率在哪个区域，cross-encoder（望远镜变显微镜）在这个小区域里精细搜索。

**小白**：有多少个候选该送进 reranker（K 值）、最后保留几个（N 值）？怎么确定这两个数？

**大师**：K 和 N 通过 **压测 + 三维权衡** 来确定：**Recall@N vs P99 延迟 vs 账单**。过程是：选 3-5 个不同的 (K, N) 组合——比如 (10,3)、(20,3)、(20,5)、(50,5)——用你的黄金评测集跑一遍，看哪个组合 Recall@N 够高、同时 P99 延迟在 SLA 范围内、额外 token 费用在预算内。通常的经验值是 K=20、N=3 作为一个不错的起点。**技术映射**：**rerank 降级策略不是备选方案，是 SLA 的一部分——没有降级的 rerank，一次超时就让整个检索颗粒无收；至少要实现「rerank 超时/失败时降级到 bi-encoder 原始排序」**。

---

## 3. 项目实战

### 环境准备

```bash
cd langchain4j-examples/rag-examples
# 需要引入 reranker 依赖（视具体实现而定）
```

### 分步实现

#### 步骤 1：在 Naive RAG 管线中加入 Reranker

```java
// 假设已有显式管线（第 19 章）
List<EmbeddingMatch<TextSegment>> candidates = 
    embeddingStore.findRelevant(queryEmbedding, 20);  // K=20 粗召回

System.out.println("Before rerank - Top 5:");
for (int i = 0; i < 5; i++) {
    System.out.println((i+1) + ". score=" + candidates.get(i).score() 
        + " " + candidates.get(i).embedded().text().substring(0, 60));
}

// 用 reranker 精排（伪代码，实际实现依赖具体 reranker 模块）
// Reranker reranker = new CrossEncoderReranker("rerank-model");
// List<EmbeddingMatch<TextSegment>> reranked = reranker.rerank(query, candidates);
// List<EmbeddingMatch<TextSegment>> topN = reranked.subList(0, 3);  // N=3 保留

System.out.println("\nAfter rerank - Top 3:");
// topN 的顺序应与原始向量排序不同——因为 cross-encoder 重新打分了
```

#### 步骤 2：估算总延迟

```java
long t0 = System.nanoTime();

// Stage 1: embed query
Embedding queryEmbed = embeddingModel.embed(query).content();
long t1 = System.nanoTime();

// Stage 2: 粗召回
List<EmbeddingMatch<TextSegment>> candidates = 
    embeddingStore.findRelevant(queryEmbed, 20);
long t2 = System.nanoTime();

// Stage 3: rerank（实际调用）
// List<EmbeddingMatch<TextSegment>> reranked = reranker.rerank(query, candidates);
long t3 = System.nanoTime();  // 假设 rerank 已执行

// Stage 4: LLM 回答
// String answer = chatModel.chat(promptWithContext);
long t4 = System.nanoTime();

System.out.println("Timing breakdown:");
System.out.println("  embed query:  " + (t1-t0)/1_000_000 + "ms");
System.out.println("  vector search: " + (t2-t1)/1_000_000 + "ms");
System.out.println("  rerank:       " + (t3-t2)/1_000_000 + "ms");
System.out.println("  llm answer:   " + (t4-t3)/1_000_000 + "ms");
System.out.println("  TOTAL:        " + (t4-t0)/1_000_000 + "ms");
```

#### 步骤 3：降级策略实现

```java
// 如果 reranker 超时或异常，降级使用向量原始排序
List<EmbeddingMatch<TextSegment>> finalResults;
try {
    // List<EmbeddingMatch<TextSegment>> reranked = reranker.rerank(query, candidates);
    // finalResults = reranked.subList(0, 3);
} catch (Exception e) {
    System.out.println("Rerank failed, using vector original order: " + e.getMessage());
    finalResults = candidates.subList(0, 3);  // 退回到向量排序
}
```

### 可能遇到的坑

| 坑 | 表现 | 解法 |
|----|------|------|
| 粗召回过小 | rerank 无力回天（最佳文档不在候选集中） | K 值至少 10-20 |
| 未设超时 | rerank 慢请求拖垮整体 SLA | rerank 设独立超时 |
| 语言不一致 | 英文 reranker 排中文文档 | 用多语言 reranker |

### 测试验证

```bash
# 注入 rerank 超时，验证降级到向量排序
# 对比有无 rerank 时的 Recall@N
# 预期：rerank 版本的 Recall@N 更高，但 P99 延迟增加
```

### 完整代码清单

`_03_Advanced_RAG_with_ReRanking_Example.java`

---

## 4. 项目总结

### 优点与缺点

| 维度 | Re-ranking | 仅向量 topN | 超大 cross-encoder 全库 |
|------|-----------|-----------|----------------------|
| 相关性 | 高（cross-encoder 精准打分） | 中（bi-encoder 近似度） | 理论最高 |
| 延迟/成本 | 中（额外一次推理） | 低 | 不可接受 |
| 典型缺点 | 需要额外维护 rerank 模型 | 细粒度语义差 | 完全不实用 |

### 适用场景

- 高精度知识问答（法条、规格、医疗信息）
- 召回候选中有大量「看起来像但实际不是」的噪声

### 不适用场景

- 低延迟要求 < 500ms（rerank 增加至少 100-500ms）
- 向量召回质量已经足够的场景

### 常见踩坑

1. **粗召回过小** → rerank 里根本没有正确文档
2. **未设超时** → rerank 慢请求拖垮整条链路
3. **语言不匹配** → 排序结果不可信

### 进阶思考题

1. 离线黄金集提升微弱时（如 Recall@K 只提升 0.5%），是否还值得上线 rerank？上线决策树？
2. reranker 与 generator 同厂商 vs 异构厂商的故障隔离策略？

### 推广计划提示

| 角色 | 建议阅读顺序 | 协作要点 |
|------|-------------|----------|
| 开发 | 第 19 章 → 本章 | K/N 压测、降级逻辑 |
| 运维 | 单独配额/熔断 | rerank 降级告警 |
| 测试 | 超时回退 | 性能基线（P95 额外延迟） |