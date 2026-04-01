

# 第01章 Elasticsearch 到底解决什么问题

# 背景

很多团队在业务增长后，会同时遇到两个痛点：数据"能存但难搜"、日志"有很多但难查"。这时 Elasticsearch（以下简称 ES）往往被引入，但如果不了解它的边界，很容易出现"用对了工具却放错了位置"。在动手部署第一个集群之前，必须先想清楚三件事：为什么用、什么时候用、什么时候不用。

## 本章目标

- 理解 ES 的三大核心价值：全文检索、近实时分析、分布式扩展。
- 掌握倒排索引的基本原理，理解它为什么比关系数据库的 `LIKE` 快。
- 明确 ES 不适合的场景，避免错误选型导致的系统风险。
- 建立一套可复用的选型决策框架和常见架构模式认知。

---

## 1. ES 的核心价值：快、准、可扩展的检索与分析

### 1.1 全文检索：倒排索引驱动的关键词查找

ES 基于 Apache Lucene 构建，底层依赖**倒排索引（Inverted Index）**而非逐行扫描。它先把文档中的文本拆分为词项（Term），再为每个词项建立"词项 -> 文档 ID 列表"的映射，查询时直接通过词项定位文档，而不是遍历所有记录。

典型场景：

- **商品搜索**：用户输入"轻薄笔记本 16G"，ES 可以把这句话拆分为多个词项，在毫秒级返回包含这些词项的商品，并按相关性评分排序。
- **知识库检索**：企业内部几十万篇文档，按关键词定位到段落级别，支持高亮显示命中片段。
- **日志检索**：每天数十 GB 的应用日志，按错误码、异常类名、关键词快速定位异常发生的上下文。

这些场景的共同特点是：数据量大、查询模式以"关键词匹配 + 相关性排序"为主，正是 ES 最擅长的领域。

### 1.2 近实时分析：边写入边统计

ES 不只是"搜索引擎"，它还内置了强大的聚合（Aggregation）能力。你可以在写入数据的同时对其做多维度的统计分析，而不需要像传统数据仓库那样先离线跑批再出报表。

常见分析场景：

- 按时间窗口统计错误日志的数量趋势（时间序列分析）。
- 按地区、渠道统计订单分布（多维聚合）。
- 按接口名称统计响应时间的 P50/P99 分位数（指标监控）。

ES 的 `refresh_interval` 默认为 1 秒，即写入后最多 1 秒即可被搜索到，这就是"近实时（Near Real-Time, NRT）"的含义。对于绝大多数分析场景，秒级延迟完全可以接受。

### 1.3 分布式扩展：分片与副本的横向扩容

ES 从设计之初就是分布式的。一个索引可以被切分为多个**分片（Shard）**，每个分片可以有若干**副本（Replica）**，分布在集群的不同节点上。

这带来三个直接收益：

- **水平扩容**：数据量从 GB 增长到 TB 乃至 PB 时，可以通过增加节点来分摊存储和计算压力，无需重构应用。
- **高可用**：副本分片分布在不同节点上，某个节点宕机后副本自动提升为主分片，服务不中断。
- **并行查询**：搜索请求会并行发送到所有相关分片，各分片独立计算后汇总结果，充分利用集群算力。

---

## 2. 倒排索引原理：为什么比 MySQL LIKE 快

### 2.1 正向索引的困境

关系数据库（如 MySQL）使用 B+ Tree 索引加速查询。对于精确匹配（`WHERE id = 123`）和前缀匹配（`WHERE name LIKE '张%'`），B+ Tree 非常高效。但面对全文搜索场景，它有三个致命问题：

- **左模糊无法走索引**：`WHERE title LIKE '%笔记本%'` 会导致全表扫描，因为 B+ Tree 只能按前缀定位。当表数据达到千万级，一次查询可能需要几秒甚至几十秒。
- **无相关性评分**：数据库只能回答"有没有"，无法回答"哪个更匹配"。用户搜"轻薄笔记本"，数据库无法区分标题里包含全部关键词和只包含部分关键词的记录。
- **无分词能力**：数据库不理解自然语言。"北京大学"和"北京的大学"在数据库看来是完全不同的字符串。

正向索引的数据组织方式是"文档 ID -> 文档内容"：

```
文档 1 → "轻薄笔记本 16G 银色"
文档 2 → "游戏笔记本 32G 黑色"
文档 3 → "轻薄平板 8G 银色"
```

要找包含"轻薄"的文档，必须逐条扫描每个文档的内容。

### 2.2 倒排索引的思路

倒排索引把关系反转：**不是从文档找词，而是从词找文档**。

以上面三条商品文档为例，ES 会先对文本做分词，然后为每个词项构建一张倒排表：

```
词项(Term)     → 文档 ID 列表(Posting List)
─────────────────────────────────────────────
轻薄           → [文档1, 文档3]
笔记本         → [文档1, 文档2]
游戏           → [文档2]
平板           → [文档3]
16G            → [文档1]
32G            → [文档2]
8G             → [文档3]
银色           → [文档1, 文档3]
黑色           → [文档2]
```

当用户搜索"轻薄 笔记本"时，ES 只需要：

1. 在倒排表中查找"轻薄" -> 得到 [文档1, 文档3]。
2. 查找"笔记本" -> 得到 [文档1, 文档2]。
3. 对两个列表做交集 -> [文档1]。

整个过程不需要扫描任何文档的原文，时间复杂度远低于全表扫描。

### 2.3 倒排索引还存了什么

倒排表中存储的不仅仅是文档 ID 列表，还包括：

| 存储项 | 作用 |
| --- | --- |
| 词频 TF（Term Frequency） | 某个词在该文档中出现了几次，用于相关性评分 |
| 位置 Position | 词项在文档中的第几个位置，用于短语查询（phrase query） |
| 偏移 Offset | 词项在原文中的字符起止位置，用于搜索结果高亮 |

正是这些额外信息，让 ES 不仅能回答"哪些文档包含这个词"，还能回答"哪个文档最相关"、"关键词在原文中出现在哪里"。

---

## 3. ES 不适合的场景

ES 很强，但不是万能数据库。以下四类场景不应该用 ES 作为主力方案：

### 3.1 强事务 ACID 场景

ES 不支持事务。如果你的业务要求"要么全部成功、要么全部回滚"，例如银行转账（A 账户扣款和 B 账户加款必须原子完成）、库存扣减（超卖不可接受），那么必须使用支持 ACID 的关系型数据库。ES 的写入是"尽力而为"的最终一致性模型，不提供跨文档的事务保证。

### 3.2 复杂联表 JOIN

ES 的关联能力非常有限。它没有 MySQL 那样的 `JOIN` 语句，虽然支持 `nested` 和 `parent-child`，但性能远不如关系数据库的多表关联。在 ES 中处理关联关系的常见做法是**反范式（Denormalization）**——把关联数据冗余到一条文档中。这换来了查询速度，但写入和数据一致性的维护成本会显著增加。

如果你的查询模式是"5 张表 JOIN 后再聚合"，请留在关系数据库或 OLAP 引擎中。

### 3.3 高频小字段更新

ES 中的更新操作本质上是"删除旧文档 + 写入新文档"。Lucene 的 Segment 是不可变的，更新一个字段意味着整条文档都要重新索引。如果你的业务是"每秒更新同一条记录的某个计数器几百次"，每次更新都会产生一个新版本的文档，旧版本被标记删除，等待 Merge 回收。这种模式下 ES 的写入放大非常严重，远不如关系数据库的原地更新（in-place update）高效。

### 3.4 作为唯一数据源（Source of Truth）

ES 是**近实时**而非**实时**系统——写入后默认最多 1 秒才可搜索。在极端情况下（节点故障、`translog.durability` 设为 `async`），可能丢失少量最近写入的数据。此外 ES 没有严格的模式约束（Schema Enforcement），不像关系数据库那样有外键、唯一约束等数据完整性保障。

最佳实践是把关系数据库作为 Source of Truth，ES 作为检索和分析的**派生副本**。即使 ES 数据损坏或丢失，也可以从主库重建。

---

## 4. 选型决策框架：四个问题判断法

在决定是否引入 ES 之前，按顺序回答以下四个问题：

| 序号 | 问题 | 倾向 |
| --- | --- | --- |
| 1 | 核心诉求是否为"搜索体验"或"检索速度"？ | 是 -> ES 高匹配 |
| 2 | 是否需要对大量数据做实时过滤、排序、聚合？ | 是 -> ES 高匹配 |
| 3 | 数据量增长后是否需要横向扩展？ | 是 -> ES 高匹配 |
| 4 | 能否接受"近实时"（秒级延迟）而非"强一致立即可见"？ | 是 -> ES 可用 |

如果前 3 个回答"是"，且第 4 个可以接受，ES 通常是高匹配方案。如果第 4 个回答"不能接受"，需要在 ES 前面加一层主库保障一致性，或者重新考虑方案。

---

## 5. 常见架构模式

### 5.1 主库 + ES 检索副本

最常见的生产架构。事务写入走关系数据库（MySQL/PostgreSQL），通过 CDC（Change Data Capture）或消息队列将变更同步到 ES，前端的搜索请求直接查 ES。

```
                           写入
用户/应用  ──────────────────────────→  MySQL / PostgreSQL
    │                                      │
    │  搜索请求                              │ CDC / MQ 同步
    │                                      ▼
    └──────────────────────────→  Elasticsearch
                                       │
                                       ▼
                                   搜索结果返回
```

这种分层的好处：事务问题交给主库，搜索性能交给 ES，职责清晰，系统稳定性高。即使 ES 出现故障，核心业务数据仍然安全存储在主库中。

### 5.2 日志分析：ELK/EFK 栈

日志场景不需要主库，日志本身就是可丢失的（或者有备份）。典型链路：

```
应用日志文件                                  Kibana
    │                                        ▲
    ▼                                        │  可视化查询
Filebeat ──→ Logstash / Ingest Pipeline ──→ ES
 (采集)         (解析/过滤/富化)             (存储/检索)
```

Filebeat 负责轻量级采集，Logstash 或 ES 自带的 Ingest Pipeline 负责日志解析和字段提取，ES 负责存储和检索，Kibana 提供可视化仪表盘。这就是经典的 ELK（Elasticsearch + Logstash + Kibana）栈。

### 5.3 指标监控场景

与日志类似，但数据模型为时间序列指标（CPU 使用率、请求延迟、错误率等）。Metricbeat 或 Prometheus Remote Write 将指标写入 ES，结合 Kibana 的 Lens 做实时仪表盘。ES 8.x 引入的 TSDB（Time Series Data Streams）针对此场景做了存储和查询优化。

---

## 6. ES 与其他技术对比

在做技术选型时，经常需要把 ES 和其他存储引擎做对比。下表从五个维度总结核心差异：

| 维度 | Elasticsearch | MySQL (RDBMS) | 向量数据库 (Milvus等) | OLAP (ClickHouse等) |
| --- | --- | --- | --- | --- |
| 主要查询类型 | 全文检索 + 聚合分析 | 精确点查 + 事务操作 | 语义向量近邻检索 (ANN) | 海量数据列式聚合分析 |
| 数据一致性 | 近实时（最终一致） | 强一致性（ACID） | 最终一致性 | 最终一致性 |
| 典型场景 | 站内搜索、日志分析、监控 | 订单管理、金融账务、用户系统 | 图片检索、LLM RAG、推荐 | BI 报表、海量事件分析 |
| 横向扩展性 | 原生分布式，加节点即扩容 | 分库分表复杂，垂直为主 | 原生分布式 | 原生分布式 |
| 更新代价 | 高（删除+重写整条文档） | 低（原地更新） | 中 | 低（追加写入，批量合并） |

关键结论：**没有一个系统能通吃所有场景**。ES 最擅长的是"搜索 + 分析"，而不是"事务"或"纯粹的海量列式聚合"。选型时应根据核心查询模式决定主力引擎，再用同步机制串联多个系统。

---

## 7. 存储放大：数据在 ES 中会膨胀

同样一份原始数据写入 ES 后，磁盘占用通常比原始 JSON 大得多。这是因为 ES 为了支撑不同的查询能力，会维护多份数据结构：

| 数据结构 | 用途 | 是否可关闭 |
| --- | --- | --- |
| 倒排索引（Inverted Index） | 全文检索、Term 查询 | 不建议关闭 |
| 正排索引（Doc Values） | 排序、聚合、脚本访问字段值 | 可关闭（丢失排序和聚合能力） |
| `_source` 字段 | 存储原始 JSON，用于返回搜索结果 | 可关闭（丢失原文返回和 reindex 能力） |

粗略估算公式：

```
磁盘占用 ≈ 原始数据大小 x (1 + 副本数) x 膨胀系数
```

膨胀系数通常在 **1.5 ~ 3.0** 之间，取决于字段类型、分词粒度和是否启用 `_source`。例如 1GB 的原始 JSON，在 1 主 1 副的配置下，实际磁盘占用约为 `1GB x 2 x 2.0 = 4GB`。

在容量规划阶段必须把这个膨胀算进去，否则磁盘会比预期更快耗尽。

---

# 总结

- ES 的核心价值是**全文检索、近实时分析、分布式扩展**，三者缺一不可地构成了它的生态位。
- 倒排索引是 ES 快速检索的物理基础，它把"逐行扫描"变成了"按词项查表"。
- ES 不适合强事务、复杂 JOIN、高频小更新、作为唯一数据源这四类场景。
- 最稳妥的架构是"主库做事务，ES 做检索分析"，通过 CDC/MQ 同步。
- 存储放大是容量规划时必须考虑的因素。

一句话总结：**把 ES 放在它最擅长的位置，你会得到极高性价比；放错位置，维护成本会快速上升。**

---

## 练习题

1. 从你们当前系统里选 2 个"适合 ES"的功能点，并说明你看重的 ES 能力（全文检索 / 聚合分析 / 分布式扩展）。
2. 再选 2 个"不适合 ES"的功能点，说明为什么更适合关系型数据库（事务需求 / JOIN 需求 / 更新模式）。
3. 某外卖平台需要根据"菜品名称"搜索商家，同时按配送距离排序，且每秒有 5000 次写入。你会将检索需求放在 MySQL 还是 ES 中？请给出理由并画出数据流草图。

---

## 实战（curl）

### 查看集群基本信息

```bash
curl -u "$ES_USER:$ES_PASS" "$ES_URL/"
```

返回结果中包含集群名称、版本号、`cluster_uuid` 等信息，用于确认集群可达和版本一致。

### 查看集群健康状态

```bash
curl -u "$ES_USER:$ES_PASS" "$ES_URL/_cluster/health?pretty"
```

关注 `status` 字段：`green`（所有分片正常）、`yellow`（副本未分配）、`red`（主分片缺失）。

### 查看节点列表

```bash
curl -u "$ES_USER:$ES_PASS" "$ES_URL/_cat/nodes?v&h=name,ip,role,heap.percent,disk.used_percent"
```

### 创建索引并定义 Mapping

```bash
curl -u "$ES_USER:$ES_PASS" -X PUT "$ES_URL/ch01_products" \
  -H "Content-Type: application/json" \
  -d '{
    "settings": {
      "number_of_shards": 1,
      "number_of_replicas": 0
    },
    "mappings": { "properties": {
      "title":    { "type": "text", "analyzer": "standard" },
      "brand":    { "type": "keyword" },
      "price":    { "type": "double" },
      "sales":    { "type": "integer" },
      "desc":     { "type": "text" }
    }}
  }'
```

### 写入示例文档

```bash
curl -u "$ES_USER:$ES_PASS" -X POST "$ES_URL/ch01_products/_bulk?refresh=wait_for" \
  -H "Content-Type: application/x-ndjson" \
  -d '{"index":{"_id":"1"}}
{"title":"轻薄笔记本 16G 银色","brand":"BrandA","price":5999.0,"sales":1200,"desc":"超轻薄设计，适合商务出行"}
{"index":{"_id":"2"}}
{"title":"游戏笔记本 32G 黑色","brand":"BrandB","price":8999.0,"sales":800,"desc":"高性能独显，畅玩大型游戏"}
{"index":{"_id":"3"}}
{"title":"轻薄平板 8G 银色","brand":"BrandA","price":3999.0,"sales":2000,"desc":"轻巧便携，影音娱乐首选"}
'
```

### 全文搜索体验

```bash
curl -u "$ES_USER:$ES_PASS" -X POST "$ES_URL/ch01_products/_search?pretty" \
  -H "Content-Type: application/json" \
  -d '{
    "query": {
      "match": { "title": "轻薄笔记本" }
    },
    "highlight": {
      "fields": { "title": {} }
    }
  }'
```

观察返回结果中的 `_score`（相关性评分）和 `highlight`（高亮片段），体会 ES 相比数据库 `LIKE` 查询的优势。

### 聚合分析体验

```bash
curl -u "$ES_USER:$ES_PASS" -X POST "$ES_URL/ch01_products/_search?pretty" \
  -H "Content-Type: application/json" \
  -d '{
    "size": 0,
    "aggs": {
      "brand_distribution": {
        "terms": { "field": "brand" }
      },
      "avg_price": {
        "avg": { "field": "price" }
      },
      "price_ranges": {
        "range": {
          "field": "price",
          "ranges": [
            { "to": 5000 },
            { "from": 5000, "to": 8000 },
            { "from": 8000 }
          ]
        }
      }
    }
  }'
```

这个请求同时完成了三个聚合：按品牌分桶、计算平均价格、按价格区间分组。在关系数据库中，类似功能需要写多条 SQL 或者复杂的 `CASE WHEN`。

### 清理

```bash
curl -u "$ES_USER:$ES_PASS" -X DELETE "$ES_URL/ch01_products"
```

---

## 实战（Java SDK）

```java
// ---------- 连接并查询集群信息 ----------
var info = client.info();
System.out.println("Cluster: " + info.clusterName());
System.out.println("Version: " + info.version().number());

// ---------- 创建索引 ----------
client.indices().create(c -> c.index("ch01_products")
    .settings(s -> s.numberOfShards("1").numberOfReplicas("0"))
    .mappings(m -> m
        .properties("title", p -> p.text(t -> t.analyzer("standard")))
        .properties("brand", p -> p.keyword(k -> k))
        .properties("price", p -> p.double_(d -> d))
        .properties("sales", p -> p.integer(i -> i))
        .properties("desc", p -> p.text(t -> t))));

// ---------- Bulk 写入 ----------
var bulkResp = client.bulk(b -> b.index("ch01_products").refresh(Refresh.WaitFor)
    .operations(op -> op.index(i -> i.id("1").document(Map.of(
        "title", "轻薄笔记本 16G 银色", "brand", "BrandA",
        "price", 5999.0, "sales", 1200, "desc", "超轻薄设计，适合商务出行"))))
    .operations(op -> op.index(i -> i.id("2").document(Map.of(
        "title", "游戏笔记本 32G 黑色", "brand", "BrandB",
        "price", 8999.0, "sales", 800, "desc", "高性能独显，畅玩大型游戏"))))
    .operations(op -> op.index(i -> i.id("3").document(Map.of(
        "title", "轻薄平板 8G 银色", "brand", "BrandA",
        "price", 3999.0, "sales", 2000, "desc", "轻巧便携，影音娱乐首选")))));

if (bulkResp.errors()) {
    bulkResp.items().stream()
        .filter(item -> item.error() != null)
        .forEach(item -> System.err.println("Failed: " + item.id() + " - " + item.error().reason()));
} else {
    System.out.println("Bulk write succeeded: " + bulkResp.items().size() + " docs");
}

// ---------- 搜索 ----------
var searchResp = client.search(s -> s.index("ch01_products")
    .query(q -> q.match(m -> m.field("title").query("轻薄笔记本")))
    .highlight(h -> h.fields("title", f -> f)),
    Map.class);

System.out.println("Total hits: " + searchResp.hits().total().value());
searchResp.hits().hits().forEach(hit -> {
    System.out.println("ID=" + hit.id() + ", score=" + hit.score());
    System.out.println("  highlight: " + hit.highlight().get("title"));
});

// ---------- 聚合 ----------
var aggResp = client.search(s -> s.index("ch01_products").size(0)
    .aggregations("brand_dist", a -> a.terms(t -> t.field("brand")))
    .aggregations("avg_price", a -> a.avg(av -> av.field("price"))),
    Map.class);

aggResp.aggregations().get("brand_dist").sterms().buckets().array()
    .forEach(b -> System.out.println("Brand: " + b.key().stringValue() + ", count: " + b.docCount()));
System.out.println("Avg price: " + aggResp.aggregations().get("avg_price").avg().value());

// ---------- 清理 ----------
client.indices().delete(d -> d.index("ch01_products"));
```
