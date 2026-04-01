

# 第12章 查询性能工具箱：慢日志、Profile、Explain

# 背景

线上慢查询不可怕，可怕的是"慢了但不知道慢在哪里"。本章把慢日志、Profile、Explain 串成一条标准排查链路，让你从"凭感觉优化"转为"证据驱动优化"。

## 本章目标

- 掌握搜索慢日志的配置方式，能在生产环境快速捕获慢查询。
- 学会阅读 Profile API 的输出结构，定位查询中的耗时瓶颈。
- 理解 Explain API 的评分拆解，解释"为什么这条文档排在前面（或后面）"。
- 建立"发现 → 定位 → 优化 → 验证"的标准排查流程。

---

## 1. 三个工具的定位


| 工具            | 解决什么问题               | 作用范围          | 开销          |
| ------------- | -------------------- | ------------- | ----------- |
| 慢日志（Slow Log） | "是谁慢了？" — 发现慢查询      | 每个分片独立记录，持续运行 | 极低（仅记日志）    |
| Profile API   | "慢在哪一步？" — 定位耗时阶段    | 单次请求维度，按需开启   | 较高（不应在生产常开） |
| Explain API   | "为什么这样排序？" — 解释单文档评分 | 单文档维度         | 低           |


三者形成互补链路：**慢日志发现问题 → Profile 定位瓶颈 → Explain 解释评分异常**。

---

## 2. 慢日志（Slow Log）

### 2.1 工作原理

搜索慢日志在 **每个分片** 上独立记录。Elasticsearch 的搜索分为两个阶段——Query 阶段（查询匹配+评分）和 Fetch 阶段（取回文档内容），慢日志为这两个阶段分别提供独立的阈值配置。

内部实现上，`SearchSlowLog` 类实现了 `SearchOperationListener` 接口，在每次 Query/Fetch 阶段执行完成后，对比耗时与预设阈值，超过阈值则写入日志。日志 logger 名分别为 `index.search.slowlog.query` 和 `index.search.slowlog.fetch`。

### 2.2 配置参数

**所有阈值默认为 `-1`（关闭）**，需要手动开启：


| 参数                                           | 含义                            |
| -------------------------------------------- | ----------------------------- |
| `index.search.slowlog.threshold.query.warn`  | Query 阶段超过该时间记为 WARN          |
| `index.search.slowlog.threshold.query.info`  | Query 阶段超过该时间记为 INFO          |
| `index.search.slowlog.threshold.query.debug` | Query 阶段超过该时间记为 DEBUG         |
| `index.search.slowlog.threshold.query.trace` | Query 阶段超过该时间记为 TRACE         |
| `index.search.slowlog.threshold.fetch.warn`  | Fetch 阶段超过该时间记为 WARN          |
| `index.search.slowlog.threshold.fetch.info`  | Fetch 阶段超过该时间记为 INFO          |
| `index.search.slowlog.threshold.fetch.debug` | Fetch 阶段超过该时间记为 DEBUG         |
| `index.search.slowlog.threshold.fetch.trace` | Fetch 阶段超过该时间记为 TRACE         |
| `index.search.slowlog.include.user`          | 是否在日志中包含触发请求的用户信息（默认 `false`） |


**推荐的生产起步配置：**

```json
{
  "index.search.slowlog.threshold.query.warn": "10s",
  "index.search.slowlog.threshold.query.info": "5s",
  "index.search.slowlog.threshold.query.debug": "2s",
  "index.search.slowlog.threshold.query.trace": "500ms",
  "index.search.slowlog.threshold.fetch.warn": "1s",
  "index.search.slowlog.threshold.fetch.info": "800ms",
  "index.search.slowlog.threshold.fetch.debug": "500ms",
  "index.search.slowlog.threshold.fetch.trace": "200ms"
}
```

### 2.3 慢日志是索引级动态设置

慢日志通过 `_settings` API 动态设置，**无需重启节点**：

```bash
PUT /my_index/_settings
{
  "index.search.slowlog.threshold.query.warn": "10s",
  "index.search.slowlog.threshold.query.info": "5s"
}
```

关闭慢日志只需将阈值重置为 `-1`：

```bash
PUT /my_index/_settings
{
  "index.search.slowlog.threshold.query.warn": "-1",
  "index.search.slowlog.threshold.query.info": "-1"
}
```

### 2.4 如何阅读慢日志

慢日志默认输出到 `<cluster_name>_index_search_slowlog.log` 文件（路径由 `log4j2.properties` 中 `index.search.slowlog` logger 配置决定）。典型的一条慢日志条目包含以下关键信息：

- **分片标识**：`[index_name][shard_id]`
- **耗时**：`took[5.2s]` / `took_millis[5200]`
- **搜索类型**：`search_type[QUERY_THEN_FETCH]`
- **总分片数与命中数**：`total_shards[5]`, `total_hits[12345]`
- **查询体**：`source[{"query":{"match":{"name":"笔记本"}}}]`

阅读要点：

1. **关注 `took` 时间**：区分是 Query 阶段慢还是 Fetch 阶段慢。
2. **关注 `total_hits`**：命中量过大通常意味着查询条件过宽。
3. **关注 `source`**：提取出查询体后可以直接用 Profile API 进一步分析。

### 2.5 索引慢日志（Indexing Slow Log）

除了搜索，索引操作也有慢日志：


| 参数                                                             | 含义                                    |
| -------------------------------------------------------------- | ------------------------------------- |
| `index.indexing.slowlog.threshold.index.warn/info/debug/trace` | 索引操作耗时阈值                              |
| `index.indexing.slowlog.source`                                | 记录 `_source` 的字符数（默认 1000，`false` 关闭） |
| `index.indexing.slowlog.reformat`                              | 是否将 `_source` 压缩为单行（默认 `true`）        |


索引慢日志对诊断写入瓶颈非常有用——如果某些文档因为复杂的 ingest pipeline 或超大 `_source` 导致索引变慢，它能帮你快速定位。

---

## 3. Profile API

### 3.1 使用方式

在任意 `_search` 请求中加入 `"profile": true` 即可开启：

```json
GET /my_index/_search
{
  "profile": true,
  "query": {
    "match": { "name": "笔记本" }
  }
}
```

加上 `?human=true` 可获得人类可读的时间格式（如 `"time": "391.9ms"`）。

### 3.2 响应结构全景

Profile 响应按分片维度组织，每个分片包含以下部分：

```
profile.shards[]
├── id                    # 分片标识：[nodeID][indexName][shardID]
├── searches[]            # 搜索执行（通常1个，global agg 会增加）
│   ├── query[]           # 查询树（Lucene 层面的查询分解）
│   │   ├── type          # Lucene 查询类型（BooleanQuery, TermQuery 等）
│   │   ├── description   # Lucene 解释文本
│   │   ├── time_in_nanos # 该查询（含子查询）总耗时
│   │   ├── breakdown     # 各阶段详细耗时
│   │   └── children[]    # 子查询（递归结构）
│   ├── rewrite_time      # 查询重写总耗时（纳秒）
│   └── collector[]       # Collector 树
│       ├── name          # Collector 类名
│       ├── reason        # 用途描述
│       └── time_in_nanos # 耗时
├── aggregations[]        # 聚合 Profile
│   ├── type              # 聚合器类名
│   ├── description       # 聚合名
│   ├── breakdown         # 各阶段耗时
│   └── children[]        # 子聚合
└── fetch                 # Fetch 阶段 Profile
    ├── breakdown         # load_stored_fields, load_source 等
    └── children[]        # FetchSourcePhase, FetchFieldsPhase 等
```

### 3.3 Query 部分：breakdown 关键指标

`breakdown` 是 Profile 的核心，它暴露了 Lucene 内部的底层操作计时：


| 指标              | 含义                       | 优化提示                       |
| --------------- | ------------------------ | -------------------------- |
| `create_weight` | 为查询创建 Weight 对象（含统计信息收集） | 耗时高说明查询初始化昂贵               |
| `build_scorer`  | 构建评分迭代器                  | 与缓存、查询复杂度相关                |
| `next_doc`      | 遍历到下一个匹配文档               | 耗时高说明匹配文档量大                |
| `advance`       | 跳跃到指定文档（比 next_doc 更底层）  | conjunction（must）查询的主要消耗   |
| `score`         | 计算单个文档的得分                | 自定义评分（script_score 等）会增加此项 |
| `match`         | 二阶段验证（如短语查询验证词序）         | 仅部分查询使用，通常为 0              |
| `*_count`       | 对应方法的调用次数                | 可用于判断查询的选择性                |


**实战阅读技巧：**

1. 先看顶层 `time_in_nanos`，找到最耗时的查询节点。
2. 进入 `breakdown`，看是 `create_weight` 大（初始化慢）还是 `next_doc/advance` 大（遍历慢）还是 `score` 大（评分慢）。
3. 对比 `*_count`：如果 `next_doc_count` 非常大，说明匹配文档太多，需要收紧查询条件或加 filter。

### 3.4 Collector 部分

Collector 负责协调文档的遍历、评分和收集。常见的 Collector reason：


| reason               | 含义                        |
| -------------------- | ------------------------- |
| `search_top_hits`    | 默认的评分排序收集器                |
| `search_count`       | `size: 0` 时仅计数            |
| `search_query_phase` | 查询阶段总协调器（含 top hits + 聚合） |
| `search_timeout`     | 启用 `timeout` 参数时的超时包装器    |
| `aggregation`        | 聚合收集器                     |
| `global_aggregation` | 全局聚合（执行独立的 match_all）     |


Collector 的时间与 Query 树的时间是**独立统计、互相不包含**的。

### 3.5 Aggregation Profile

聚合 Profile 的 breakdown 结构与 Query 不同：


| 指标                     | 含义                  |
| ---------------------- | ------------------- |
| `initialize`           | 聚合器初始化              |
| `build_leaf_collector` | 每个 segment 上创建叶子收集器 |
| `collect`              | 收集文档（主要耗时通常在这里）     |
| `build_aggregation`    | 构建聚合结果              |
| `post_collection`      | 收集完成后的清理            |
| `reduce`               | 归约阶段（目前总是 0，预留）     |


如果 `collect` 耗时占比极高，说明参与聚合的文档量过大——考虑加 filter 缩小范围或换用 `sampler` 聚合。

### 3.6 Fetch Profile

Fetch Profile 显示取回文档内容的耗时分解：


| 指标                   | 含义              |
| -------------------- | --------------- |
| `load_stored_fields` | 加载存储字段（通常是主要耗时） |
| `load_source`        | 加载 `_source`    |
| `next_reader`        | 每个 segment 的初始化 |


children 中常见的子阶段：

- `FetchSourcePhase`：提取 `_source` 内容
- `FetchFieldsPhase`：提取 `fields` 指定的字段
- `StoredFieldsPhase`：处理 stored fields

如果 `load_stored_fields` 耗时很长，可能是文档 `_source` 过大。可通过 `_source` filtering 或 `stored_fields: ["_none_"]` 减少 fetch 开销。

### 3.7 Profile 的局限性

Profile API 是一个调试工具，有几个重要的局限：

1. **有显著开销**：Profile 会对 `collect`、`advance`、`next_doc` 等高频方法插入计时，不应在生产环境常开。
2. **不测量网络延迟**：Profile 只度量分片内的执行时间，不包含网络传输、队列等待、协调节点合并等耗时。
3. **可能禁用某些 Lucene 优化**：部分查询在开启 Profile 后会因为插桩而无法使用快速路径，导致 Profile 时间 > 实际时间。
4. **输出格式不稳定**：尤其是 `debug` 部分，在不同版本之间可能变化。

---

## 4. Explain API

### 4.1 使用方式

`_explain` 针对**单个文档**，返回该文档在给定查询下的详细评分拆解：

```
GET /my_index/_explain/{doc_id}
{
  "query": {
    "match": { "name": "笔记本" }
  }
}
```

### 4.2 响应结构

```json
{
  "_index": "my_index",
  "_id": "p1",
  "matched": true,
  "explanation": {
    "value": 1.2345,
    "description": "weight(name:笔记本 in 0) [PerFieldSimilarity], result of:",
    "details": [
      {
        "value": 1.2345,
        "description": "score(freq=1.0), computed as boost * idf * tf from:",
        "details": [
          { "value": 2.2, "description": "boost" },
          { "value": 0.6931, "description": "idf, computed as ..." },
          { "value": 0.8096, "description": "tf, computed as freq / (freq + k1 * (1 - b + b * dl / avgdl)) ..." }
        ]
      }
    ]
  }
}
```

### 4.3 如何阅读 Explain 输出

Explain 的输出是一棵**递归的评分树**，每个节点包含：

- `value`：该节点的得分值
- `description`：得分的文字描述
- `details`：子节点列表

对于默认的 BM25 相似度模型，关键因子：


| 因子              | 含义                   | 可调手段               |
| --------------- | -------------------- | ------------------ |
| `tf`（词频）        | 查询词在文档中出现的频率         | 文档内容决定，不可直接调       |
| `idf`（逆文档频率）    | 查询词在整个索引中的稀有度        | 数据分布决定             |
| `boost`         | 查询时指定的权重加成           | 可在查询中设置 `boost` 参数 |
| `dl`（文档长度）      | 字段的 token 数量         | 长文本 tf 会被稀释        |
| `avgdl`（平均文档长度） | 该字段在整个索引中的平均 token 数 | 数据分布决定             |


### 4.4 Explain 的典型使用场景

1. **"为什么这条文档没有命中？"** — `matched: false` 时，Explain 会告诉你是哪个子条件不满足。
2. **"为什么文档 A 排在文档 B 前面？"** — 分别 Explain 两条文档，对比各项因子差异。
3. **"boost 调了没效果？"** — 通过 Explain 确认 boost 是否被正确应用。
4. **调试自定义评分** — `function_score`、`script_score` 等复杂评分逻辑的实际得分拆解。

### 4.5 Explain 与 Profile 的区别


| 维度   | Profile   | Explain    |
| ---- | --------- | ---------- |
| 关注点  | 耗时（性能）    | 得分（相关性）    |
| 粒度   | 整个查询的所有分片 | 单个文档       |
| 开销   | 较高        | 低          |
| 典型问题 | "查询为什么慢？" | "排序为什么不对？" |


---

## 5. 标准排查流程

```
第一步：发现
│  开启慢日志，积累一段时间后筛出高频/高耗时查询
│
第二步：定位
│  取出慢查询的 query body，用 Profile API 分析
│  ├── 看 query 树：哪个子查询最耗时？
│  ├── 看 breakdown：慢在初始化（create_weight）还是遍历（next_doc/advance）还是评分（score）？
│  ├── 看 collector：有没有不必要的全局聚合？
│  └── 看 fetch：_source 是否过大？
│
第三步：诊断
│  结合 mapping 和查询结构，找出根因
│  ├── text 字段被用于精确匹配？→ 加 keyword 子字段
│  ├── 结构化条件在 must 中？→ 移到 filter（跳过评分+利用缓存）
│  ├── 深分页？→ 换 search_after
│  ├── 高基数聚合？→ 加 filter 缩小范围 / 用 composite 分批
│  └── 自定义脚本评分？→ 简化或预计算
│
第四步：优化 & 验证
│  修改后再次 Profile，对比前后 time_in_nanos
│  记录优化前后的延迟基线
```

---

## 6. 常见优化方向与对策

### 6.1 filter vs must

`bool.must` 会计算评分，`bool.filter` 不计算评分且结果可被缓存：

```json
{
  "query": {
    "bool": {
      "must": [
        { "match": { "name": "笔记本" } }
      ],
      "filter": [
        { "term": { "status": "active" } },
        { "range": { "price": { "gte": 1000, "lte": 5000 } } }
      ]
    }
  }
}
```

将不需要参与评分的条件从 `must` 移到 `filter`，是最常见且收益最大的优化。

### 6.2 避免昂贵查询

以下查询类型通常比较昂贵：

- **前缀通配符**：`wildcard` 以 `*` 开头（`*book`），无法利用倒排索引前缀。
- **正则表达式**：`regexp` 在每个 segment 上遍历所有 term。
- **大范围 fuzzy**：`fuzziness: "AUTO"` 在高基数字段上会展开大量候选词。
- **脚本评分**：`script_score` 对每个匹配文档执行脚本。
- **深层嵌套 nested 查询**：每层 nested 增加一次 Block Join。

### 6.3 控制返回数据量

- 使用 `_source` filtering 只返回需要的字段。
- 如果只需要计数，设置 `size: 0`。
- 如果只需要判断是否命中，使用 `terminate_after: 1`。

### 6.4 聚合优化

- 为聚合加前置 filter 缩小数据范围。
- 高基数 `terms` 聚合考虑使用 `composite` 聚合分批获取。
- 避免对 `text` 字段直接做聚合（应使用 keyword 子字段）。

---

## 7. 进阶实践

### 7.1 建立查询基线

在测试环境中，对核心查询建立性能基线：

```bash
for i in $(seq 1 100); do
  curl -s -o /dev/null -w "%{time_total}\n" \
    -u "$ES_USER:$ES_PASS" \
    -X POST "$ES_URL/$IDX/_search" \
    -H "Content-Type: application/json" \
    -d '{"query":{"match":{"name":"笔记本"}}}'
done | awk '{sum+=$1; count++} END {print "avg:", sum/count, "s"}'
```

### 7.2 优化前后对比模板

每次优化记录以下信息：


| 项目                    | 优化前                         | 优化后                            |
| --------------------- | --------------------------- | ------------------------------ |
| 查询描述                  | match on text field in must | 拆分为 must(match) + filter(term) |
| Profile time_in_nanos | 15,000,000                  | 3,200,000                      |
| P99 延迟                | 850ms                       | 180ms                          |
| 变更内容                  | -                           | 将 status 条件移入 filter           |


### 7.3 批量关闭慢日志

当诊断完成后，批量关闭所有索引的慢日志：

```bash
curl -u "$ES_USER:$ES_PASS" -X PUT "$ES_URL/_all/_settings" \
  -H "Content-Type: application/json" \
  -d '{
    "index.search.slowlog.threshold.query.warn": "-1",
    "index.search.slowlog.threshold.query.info": "-1",
    "index.search.slowlog.threshold.query.debug": "-1",
    "index.search.slowlog.threshold.query.trace": "-1",
    "index.search.slowlog.threshold.fetch.warn": "-1",
    "index.search.slowlog.threshold.fetch.info": "-1",
    "index.search.slowlog.threshold.fetch.debug": "-1",
    "index.search.slowlog.threshold.fetch.trace": "-1"
  }'
```

---

# 总结

- **慢日志**是生产环境的第一道防线——成本极低，建议核心索引长期开启 WARN 级别。
- **Profile API** 是精准定位瓶颈的利器，但有开销，仅在排查时使用。重点关注 `breakdown` 中的 `create_weight`、`next_doc`/`advance`、`score` 占比。
- **Explain API** 解决的是"为什么排序不对"而非"为什么慢"——理解 BM25 的 tf/idf/boost/dl 因子是用好 Explain 的前提。
- 没有可观测就没有真正的优化。工具链越规范，团队排障效率越高。

---

## 练习题

1. 为测试索引开启搜索慢日志（Query WARN=2s, INFO=1s; Fetch WARN=500ms），执行几条查询后找到慢日志文件，解读一条日志条目。
2. 对一条 `bool` 查询开启 Profile，找出最耗时的子查询，说明耗时集中在 breakdown 的哪个指标。
3. 用 Explain 对比两条文档的评分差异，解释为什么文档 A 排在文档 B 前面。
4. 将一条"所有条件都在 must 中"的查询重构为"match 在 must，结构化条件在 filter"，对比 Profile 前后的 `time_in_nanos` 变化。
5. 构造一个包含聚合的查询，用 Profile 分析聚合的 `collect` 耗时，尝试加 filter 后再次对比。

---

## 实战（curl）

### 准备测试数据

```bash
# 创建索引
curl -u "$ES_USER:$ES_PASS" -X PUT "$ES_URL/perf_demo" \
  -H "Content-Type: application/json" \
  -d '{
    "mappings": { "properties": {
      "name":     { "type": "text", "fields": { "keyword": { "type": "keyword" } } },
      "category": { "type": "keyword" },
      "price":    { "type": "double" },
      "status":   { "type": "keyword" }
    }}
  }'

# 批量插入
curl -u "$ES_USER:$ES_PASS" -X POST "$ES_URL/perf_demo/_bulk?refresh=wait_for" \
  -H "Content-Type: application/json" \
  -d '
{"index":{"_id":"p1"}}
{"name":"高性能笔记本电脑","category":"electronics","price":6999,"status":"active"}
{"index":{"_id":"p2"}}
{"name":"轻薄笔记本","category":"electronics","price":4999,"status":"active"}
{"index":{"_id":"p3"}}
{"name":"机械键盘","category":"peripherals","price":599,"status":"inactive"}
{"index":{"_id":"p4"}}
{"name":"笔记本支架","category":"accessories","price":199,"status":"active"}
'
```

### 开启慢日志

```bash
curl -u "$ES_USER:$ES_PASS" -X PUT "$ES_URL/perf_demo/_settings" \
  -H "Content-Type: application/json" \
  -d '{
    "index.search.slowlog.threshold.query.warn": "10s",
    "index.search.slowlog.threshold.query.info": "5s",
    "index.search.slowlog.threshold.query.debug": "2s",
    "index.search.slowlog.threshold.query.trace": "0ms",
    "index.search.slowlog.threshold.fetch.warn": "1s",
    "index.search.slowlog.threshold.fetch.trace": "0ms",
    "index.search.slowlog.include.user": true
  }'
```

### Profile 查询分析

```bash
# 1) 基础 Profile：观察 query 树和 breakdown
curl -u "$ES_USER:$ES_PASS" -X POST "$ES_URL/perf_demo/_search?pretty&human=true" \
  -H "Content-Type: application/json" \
  -d '{
    "profile": true,
    "query": {
      "bool": {
        "must": [
          { "match": { "name": "笔记本" } },
          { "term": { "status": "active" } },
          { "range": { "price": { "gte": 1000 } } }
        ]
      }
    }
  }'

# 2) 优化版：结构化条件移入 filter
curl -u "$ES_USER:$ES_PASS" -X POST "$ES_URL/perf_demo/_search?pretty&human=true" \
  -H "Content-Type: application/json" \
  -d '{
    "profile": true,
    "query": {
      "bool": {
        "must": [
          { "match": { "name": "笔记本" } }
        ],
        "filter": [
          { "term": { "status": "active" } },
          { "range": { "price": { "gte": 1000 } } }
        ]
      }
    }
  }'
```

### Profile 聚合分析

```bash
curl -u "$ES_USER:$ES_PASS" -X POST "$ES_URL/perf_demo/_search?pretty&human=true" \
  -H "Content-Type: application/json" \
  -d '{
    "profile": true,
    "size": 0,
    "query": { "match": { "name": "笔记本" } },
    "aggs": {
      "by_category": {
        "terms": { "field": "category" },
        "aggs": {
          "avg_price": { "avg": { "field": "price" } }
        }
      }
    }
  }'
```

### Explain 单文档评分

```bash
# Explain：为什么 p1 排在前面？
curl -u "$ES_USER:$ES_PASS" -X GET "$ES_URL/perf_demo/_explain/p1?pretty" \
  -H "Content-Type: application/json" \
  -d '{ "query": { "match": { "name": "笔记本" } } }'

# Explain：对比 p4 的评分
curl -u "$ES_USER:$ES_PASS" -X GET "$ES_URL/perf_demo/_explain/p4?pretty" \
  -H "Content-Type: application/json" \
  -d '{ "query": { "match": { "name": "笔记本" } } }'

# Explain：文档为什么没命中？
curl -u "$ES_USER:$ES_PASS" -X GET "$ES_URL/perf_demo/_explain/p3?pretty" \
  -H "Content-Type: application/json" \
  -d '{
    "query": {
      "bool": {
        "must": [
          { "match": { "name": "笔记本" } },
          { "term": { "status": "active" } }
        ]
      }
    }
  }'
```

### 关闭慢日志 & 清理

```bash
# 关闭慢日志
curl -u "$ES_USER:$ES_PASS" -X PUT "$ES_URL/perf_demo/_settings" \
  -H "Content-Type: application/json" \
  -d '{
    "index.search.slowlog.threshold.query.warn": "-1",
    "index.search.slowlog.threshold.query.info": "-1",
    "index.search.slowlog.threshold.query.debug": "-1",
    "index.search.slowlog.threshold.query.trace": "-1",
    "index.search.slowlog.threshold.fetch.warn": "-1",
    "index.search.slowlog.threshold.fetch.trace": "-1"
  }'

# 清理索引
curl -u "$ES_USER:$ES_PASS" -X DELETE "$ES_URL/perf_demo"
```

---

## 实战（Java SDK）

```java
// ---------- Profile 查询 ----------
var profileResp = client.search(s -> s.index("perf_demo")
    .profile(true)
    .query(q -> q.bool(b -> b
        .must(m -> m.match(mt -> mt.field("name").query("笔记本")))
        .filter(f -> f.term(t -> t.field("status").value("active")))
        .filter(f -> f.range(r -> r.number(n -> n.field("price").gte(1000d)))))),
    Map.class);

if (profileResp.profile() != null) {
    profileResp.profile().shards().forEach(shard -> {
        shard.searches().forEach(search -> {
            search.query().forEach(pq -> {
                System.out.println("Query type: " + pq.type());
                System.out.println("  time_in_nanos: " + pq.timeInNanos());
                System.out.println("  breakdown: " + pq.breakdown());
            });
        });
    });
}

// ---------- Explain 单文档 ----------
var explainResp = client.explain(e -> e
        .index("perf_demo")
        .id("p1")
        .query(q -> q.match(m -> m.field("name").query("笔记本"))),
    Map.class);

System.out.println("matched: " + explainResp.matched());
if (explainResp.explanation() != null) {
    System.out.println("score: " + explainResp.explanation().value());
    System.out.println("description: " + explainResp.explanation().description());
    explainResp.explanation().details().forEach(detail ->
        System.out.println("  " + detail.description() + " = " + detail.value()));
}

// ---------- Profile 聚合 ----------
var aggProfileResp = client.search(s -> s.index("perf_demo")
    .profile(true)
    .size(0)
    .query(q -> q.match(m -> m.field("name").query("笔记本")))
    .aggregations("by_category", a -> a
        .terms(t -> t.field("category"))
        .aggregations("avg_price", sub -> sub.avg(avg -> avg.field("price")))),
    Map.class);

if (aggProfileResp.profile() != null) {
    aggProfileResp.profile().shards().forEach(shard ->
        shard.aggregations().forEach(agg -> {
            System.out.println("Agg type: " + agg.type());
            System.out.println("  description: " + agg.description());
            System.out.println("  time_in_nanos: " + agg.timeInNanos());
            System.out.println("  breakdown: " + agg.breakdown());
        }));
}
```

