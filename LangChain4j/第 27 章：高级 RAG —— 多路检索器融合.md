# 第 27 章：高级 RAG —— 多路检索器融合

## 1. 项目背景

### 业务场景（拟真）

企业的数据不止存在于一个向量库里。**商品信息在 ES 中做了关键词索引**，**用户订单在 SQL 数据库里**，**政策文档在向量库中做了语义索引**。用户问「这个手机跟 iPhone 比怎么样」——理想情况下应该从 ES 拿商品规格、从向量库拿评测对比文章、甚至联网搜索最新价格。**多路检索器融合** 就是让多个 `ContentRetriever` 并行工作、结果合并后再传给 LLM。

### 痛点放大

仅靠单一向量库无法覆盖所有数据源的类型差异。简单拼接多路结果会导致：上下文超长、结果重复、一路超时拖垮全请求。

## 2. 项目设计：小胖、小白与大师的对话

**小胖**：多路融合是不是就像豆瓣、IMDb 和本地硬盘一起搜电影，然后把结果摞在一起给我看？

**大师**：很准确。但融合比「摞在一起」复杂——你可能需要做三件事：① **去重**——同一个电影在豆瓣和 IMDb 都有，不能出现两次；② **重排**——向量库的结果按语义相似度排、ES 的结果按关键词匹配度排、SQL 的结果按时间排——三种排序分数不可比较，需要用 RRF（倒数排名融合）等方法统一排序；③ **修剪**——总结果可能远超上下文窗口，需要合并或取舍。

**小白**：如果其中一路超时了——比如 SQL 检索跑了 3 秒还没返回——是不是整个请求就卡死了？

**大师**：**不能卡死。** 每一路都应该有独立的超时控制和熔断机制。一路超时了，它返回空结果，其他路的正常返回仍然可以用。并在最终的回答里提示用户「部分信息来源暂时不可用」。这要求你为每一路配置独立的超时时间、熔断器，且融合策略要能处理「部分成功」的场景。**技术映射**：**融合前的最后一道关卡必须是每路独立的租户过滤（第 25 章）——其中一路漏做了过滤，就可能跨租户泄露数据**。

---

## 3. 项目实战

### 环境准备

```bash
cd langchain4j-examples/rag-examples
export OPENAI_API_KEY="sk-your-key-here"
```

### 分步实现

#### 步骤 1：并行多路检索

```java
import java.util.concurrent.*;

// 定义线程池
ExecutorService executor = Executors.newFixedThreadPool(3);

// 定义多路检索任务
Callable<List<String>> vectorSearch = () -> {
    // 向量检索
    List<EmbeddingMatch<TextSegment>> matches = 
        embeddingStore.findRelevant(queryEmbed, 5);
    return matches.stream()
        .map(m -> "[vector] " + m.embedded().text())
        .collect(Collectors.toList());
};

Callable<List<String>> keywordSearch = () -> {
    // ES 关键词检索（伪代码）
    // return esClient.search(query, "products_index");
    return List.of("[keyword] matching result 1", "[keyword] matching result 2");
};

Callable<List<String>> sqlSearch = () -> {
    // SQL 检索（伪代码）
    // return jdbcTemplate.query("SELECT ...", ...);
    return List.of("[sql] DB result from orders table");
};

// 并行执行
List<Future<List<String>>> futures = executor.invokeAll(
    List.of(vectorSearch, keywordSearch, sqlSearch),
    2, TimeUnit.SECONDS  // 整体超时 2 秒
);

// 收集结果
List<String> allResults = new ArrayList<>();
for (Future<List<String>> f : futures) {
    try {
        allResults.addAll(f.get(500, TimeUnit.MILLISECONDS));
    } catch (Exception e) {
        System.out.println("One route failed: " + e.getMessage());
        // 不影响其他路的结果
    }
}

System.out.println("Total results from all routes: " + allResults.size());
allResults.forEach(System.out::println);
```

#### 步骤 2：RRF 融合排序

```java
// RRF（倒数排名融合）伪代码
// 每路的排名结果，按 rank 计算 RRF 分数
public double rrfScore(int rank, int k) {
    return 1.0 / (k + rank);  // k 通常取 60
}

// 假设：
// 向量路排名：docA(1), docB(2), docC(3)
// 关键词路排名：docB(1), docD(2), docA(3)
// docA: 1/61 + 1/63 ≈ 0.032   ← 融合排名第一
// docB: 1/62 + 1/61 ≈ 0.032   ← 非常接近
```

### 可能遇到的坑

| 坑 | 表现 | 解法 |
|----|------|------|
| 简单拼接不融合 | 上下文超长、结果重复 | 用 RRF 去重重排 |
| 一路超时不处理 | 全路等慢的那一路 | 每路独立超时 |
| 未做租户过滤 | 跨源泄露 | 每路各自执行 filter |

### 测试验证

```bash
# 关闭一路依赖，验证优雅降级
# 断言来源列表与陈述不矛盾
```

### 完整代码清单

`_07_Advanced_RAG_Multiple_Retrievers_Example.java`

---

## 4. 项目总结

### 优点与缺点

| 维度 | 多路融合 | 单路向量 | 手工拼 SQL |
|------|---------|---------|-----------|
| 召回覆盖 | 高（多源互补） | 中 | 高 |
| 复杂度 | 高（多路运维） | 低 | 中 |
| 典型缺点 | 故障面大 | 异构数据弱 | 难维护 |

### 适用场景

- 企业搜索中台（向量+ES+SQL）
- 迁移期新旧索引并存

### 不适用场景

- 单一数据源已足够
- 无法接受额外延迟的场景

### 常见踩坑

1. **简单拼接** → 重复与超长上下文
2. **一路失败未捕获** → 整个检索空响应
3. **未记录每路来源** → 无法排查问题

### 进阶思考题

1. RRF 与加权分数融合的 A/B 测试设计？
2. CRM JSON 行错误当长文本时的摘要化边界？
