

# 第13章 写入链路：Bulk、Refresh、Translog、Segment

# 背景

写入性能优化不是只调一个参数。Bulk、Refresh、Translog、Flush、Segment Merge 是同一条链路上的五个环节，必须整体理解才能避免"提了吞吐却拖慢查询"或"保了实时性却压垮磁盘 IO"。

## 本章目标

- 掌握一条文档从客户端到可搜索的完整生命周期。
- 理解 Refresh、Flush、Translog 三者的区别与协作。
- 学会根据业务场景调整 `refresh_interval`、`translog.durability`、merge 策略等关键参数。
- 掌握 Bulk API 的正确用法，避免常见的批量写入陷阱。

---

## 1. 写入链路全景

一条文档从客户端提交到可被搜索，经历以下阶段：

```
客户端
  │
  ▼
协调节点（Coordinating Node）
  │  路由：根据 _routing 或 _id 哈希确定目标分片
  ▼
主分片（Primary Shard）
  │  1. 写入 Lucene 内存缓冲区（IndexWriter buffer）
  │  2. 写入 Translog（事务日志）
  │  3. 返回确认给协调节点
  ▼
副本分片（Replica Shard）
  │  并行复制：同样执行 1→2→3
  ▼
客户端收到成功响应
  │
  │  ──── 此时文档已持久化（translog），但还不可搜索 ────
  │
  ▼
Refresh（默认每 1 秒）
  │  将内存缓冲区写入新的 Lucene Segment
  │  打开新的 Searcher → 文档变为可搜索
  ▼
Flush（自动触发）
  │  执行 Lucene Commit，将 Segment 持久化到磁盘
  │  清理已提交的 Translog
  ▼
Segment Merge（后台持续）
  │  将多个小 Segment 合并为更大的 Segment
  │  清除标记删除的文档，释放磁盘空间
```

理解这条链路后，你会发现：**Refresh 解决可见性，Translog 解决持久性，Flush 解决恢复效率，Merge 解决查询性能和磁盘空间**。

---

## 2. Bulk API：批量写入的正确姿势

### 2.1 为什么必须用 Bulk

单条 Index 请求的开销：HTTP 连接建立 → 请求解析 → 分片路由 → 写入 → 响应序列化 → 网络传输。如果写 10000 条文档，这些开销重复 10000 次。

Bulk 的核心优势：

- **网络合并**：一次 HTTP 请求发送多条操作。
- **分片分组**：`TransportBulkAction` 会按目标 `ShardId` 将操作分组，每组构建一个 `BulkShardRequest` 发送到对应分片，减少分片间的网络往返。
- **统一 Refresh 策略**：`refresh` 参数作用于整个 Bulk 请求，不支持单条设置。

### 2.2 Bulk 内部流程

```
BulkRequest（N 条操作）
  │
  ▼  TransportBulkAction
按 ShardId 分组
  │
  ├── ShardId-A: [item-0, item-3, item-7]  → BulkShardRequest-A
  ├── ShardId-B: [item-1, item-4]          → BulkShardRequest-B
  └── ShardId-C: [item-2, item-5, item-6]  → BulkShardRequest-C
        │
        ▼  TransportShardBulkAction
  主分片上逐条执行（while 循环）
        │  每条操作独立成功/失败
        ▼
  复制到副本分片
        │
        ▼
  合并所有 BulkShardResponse → BulkResponse
```

**关键语义：部分失败**

- Bulk 请求不是原子的。10 条操作中如果第 3 条因为 mapping 冲突失败，其余 9 条仍然会成功。
- 调用方**必须**检查 `BulkResponse.hasFailures()` 并逐条处理 `BulkItemResponse`。

### 2.3 Bulk 最佳实践


| 维度      | 建议                                 | 原因                                    |
| ------- | ---------------------------------- | ------------------------------------- |
| 批大小     | 1000~~5000 条/批（或 5~~15 MB）         | 太小浪费网络，太大增加单批失败成本和内存压力                |
| 并发度     | 2~4 个并发 Bulk 线程                    | 与分片数和节点数匹配，避免线程池打满                    |
| 文档 ID   | 初始导入时不指定 `_id`（自动生成）               | 省去版本检查（get-before-index），吞吐提升约 15~25% |
| 错误处理    | 逐条检查失败，对 429（rejected）重试           | 部分失败不应丢弃整批成功数据                        |
| refresh | 初始导入用 `refresh_interval: -1`，导完再开启 | 避免导入期间频繁 refresh 浪费资源                 |
| routing | 合理设计 routing 减少分片扇出                | 默认按 `_id` 路由，自定义 routing 可让相关文档落在同一分片 |


### 2.4 Bulk 的 refresh 参数


| 值          | 行为                      |
| ---------- | ----------------------- |
| 不设置（默认）    | 等待下一次定时 refresh 后可搜索    |
| `true`     | 请求返回前立即 refresh 涉及的分片   |
| `wait_for` | 请求阻塞直到下一次 refresh 完成后返回 |
| `false`    | 等同于不设置                  |


`wait_for` 比 `true` 更温和——它不会触发额外的 refresh，而是等待定时 refresh 自然发生。多个 `wait_for` 请求会合并等待同一次 refresh。

---

## 3. Refresh：可见性的开关

### 3.1 Refresh 做了什么

Refresh 的本质是 **打开一个新的 Lucene Searcher**：

1. 将 IndexWriter 内存缓冲区中的数据写入一个新的 **Segment**（此时 Segment 在文件系统缓存中，尚未 fsync）。
2. 打开一个指向新 Segment 的 Searcher。
3. 后续的搜索请求使用新 Searcher，因此可以看到刚写入的文档。

**Refresh 不是持久化操作**——它不会执行 fsync，如果此时断电，数据不会丢失，因为有 Translog 保底。

### 3.2 核心配置


| 参数                        | 默认值   | 说明                               |
| ------------------------- | ----- | -------------------------------- |
| `index.refresh_interval`  | `1s`  | Refresh 间隔。设为 `-1` 可关闭定时 Refresh |
| `index.search.idle.after` | `30s` | 分片在无搜索请求超过该时间后进入"搜索空闲"状态         |


### 3.3 Search Idle 优化

当一个分片在 `index.search.idle.after`（默认 30s）内没有收到任何搜索请求时，ES 会跳过该分片的定时 Refresh——直到下一次搜索请求到来时才触发 Refresh。

这个优化对**写多读少的索引**（如日志索引）非常有意义：大量只有写入没有查询的分片不会浪费 CPU 做无人消费的 Refresh。

前提条件：`index.refresh_interval` 未被**显式设置**（使用默认值即可触发此优化）。如果你手动设置了 `refresh_interval: "1s"`，即使和默认值相同，Search Idle 优化也不会生效。

### 3.4 Refresh 调优场景


| 场景          | 建议                                          |
| ----------- | ------------------------------------------- |
| 初始数据导入      | `refresh_interval: "-1"`，导入完成后恢复并手动 refresh |
| 近实时搜索（默认）   | 保持 `1s` 默认值                                 |
| 日志/监控（写多读少） | 不显式设置（利用 Search Idle），或设为 `30s`/`60s`       |
| 高写入吞吐优先     | `5s` ~ `30s`，减少 refresh 频率                  |


---

## 4. Translog：持久性的保障

### 4.1 Translog 的角色

Lucene Commit（即 Flush）是一个昂贵的操作——它需要将所有 Segment 持久化到磁盘。ES 不可能在每次写入后都做 Lucene Commit。

Translog 的设计思路和数据库的 WAL（Write-Ahead Log）一样：

1. 每次写入操作（index/delete/update）在被 Lucene IndexWriter 处理后，**同时写入 Translog**。
2. 确认响应返回给客户端前，确保 Translog 已持久化（`durability: request`）。
3. 如果进程崩溃，恢复时重放 Translog 中未 Commit 的操作。

### 4.2 核心配置


| 参数                                    | 默认值       | 说明                              |
| ------------------------------------- | --------- | ------------------------------- |
| `index.translog.durability`           | `request` | 每次写请求后 fsync Translog           |
| `index.translog.sync_interval`        | `5s`      | `async` 模式下的 fsync 间隔（最小 100ms） |
| `index.translog.flush_threshold_size` | `10GB`    | Translog 超过此大小触发 Flush          |


### 4.3 durability 的两种模式

`**request`（默认，推荐生产使用）：**

- 每次 index/delete/bulk 操作完成后，在返回响应前，对主分片和所有副本分片的 Translog 执行 fsync。
- **零数据丢失**：任何已确认的写入都已持久化到磁盘。
- 代价：每次写入都有一次 fsync 的磁盘 IO。

`**async`（高吞吐场景的折中选择）：**

- Translog 仅按 `sync_interval`（默认 5 秒）定期 fsync。
- **可能丢失最近一个 sync_interval 内的数据**。
- 适合：日志、监控等可容忍少量数据丢失、追求极致写入吞吐的场景。

```json
PUT /my_logs/_settings
{
  "index.translog.durability": "async",
  "index.translog.sync_interval": "5s"
}
```

### 4.4 Translog 大小保护

ES 会确保 **Translog 不超过磁盘总容量的 1%**。即使 `flush_threshold_size` 设为 10GB，在小磁盘节点上实际触发 Flush 的阈值会被自动下调到磁盘容量的 1%（下限约 10MB）。

---

## 5. Flush：Translog → Lucene Commit

### 5.1 Flush 做了什么

Flush = **Lucene Commit** + **开启新的 Translog 代**：

1. 调用 Lucene IndexWriter 的 `commit()`，将所有 Segment 持久化（fsync）到磁盘。
2. 开启新的 Translog generation，旧的已提交的 Translog 文件可以安全删除。

Flush 之后，即使进程崩溃，也不需要重放 Translog——因为数据已经在 Lucene 的 Commit Point 中。

### 5.2 Flush vs Refresh


| 维度    | Refresh        | Flush                        |
| ----- | -------------- | ---------------------------- |
| 目的    | 让新文档可搜索        | 持久化 Segment + 清理 Translog    |
| 操作    | 打开新 Searcher   | Lucene Commit + 新 Translog 代 |
| 频率    | 高（默认 1s）       | 低（Translog 满/定时触发）           |
| 开销    | 轻量             | 较重（涉及 fsync）                 |
| 数据可见性 | Refresh 后可搜索   | Flush 不直接影响可见性               |
| 数据持久性 | Refresh 不保证持久化 | Flush 后数据完全持久化               |


### 5.3 Flush 触发条件

Flush 主要由以下条件自动触发：

- **Translog 大小**超过 `flush_threshold_size`（默认 10GB，受磁盘 1% 上限）。
- **Translog 年龄**超过 `flush_threshold_age`（默认 1 分钟）。
- Merge 产生的 Segment 大小超过 `index.flush_after_merge`（默认 512MB）。
- 手动调用 `_flush` API。

一般不需要手动 Flush，ES 的自动 Flush 机制足够可靠。

---

## 6. Segment 与 Merge：查询性能的基石

### 6.1 Segment 的本质

Lucene Index 由多个 **Segment** 组成。每个 Segment 是一个**不可变**的倒排索引——一旦写入就不会被修改。

- **写入**：新文档写入新 Segment。
- **删除**：旧文档不是从 Segment 中物理删除，而是在 `.del` 文件中标记为已删除。
- **更新**：先标记旧文档为删除，再在新 Segment 中写入新版本。

### 6.2 为什么需要 Merge

每次 Refresh 都会生成一个新的 Segment。如果不合并，Segment 数量会持续增长，导致：

- **查询变慢**：搜索时需要遍历所有 Segment。
- **文件句柄耗尽**：每个 Segment 对应多个文件。
- **磁盘浪费**：标记删除的文档仍然占用空间，只有 Merge 才能真正回收。

### 6.3 Merge 的工作方式

ES 使用 Lucene 的 **TieredMergePolicy**（普通索引）或 **LogByteSizeMergePolicy**（时序索引）在后台持续合并 Segment：

1. 后台线程持续监控 Segment 状态。
2. 选择若干小 Segment 合并为一个大 Segment。
3. 合并过程中物理删除标记为删除的文档。
4. 合并完成后切换引用到新 Segment，删除旧 Segment 文件。

### 6.4 Merge 的关键参数


| 参数                                       | 默认值                     | 说明                                   |
| ---------------------------------------- | ----------------------- | ------------------------------------ |
| `index.merge.scheduler.max_thread_count` | `max(1, min(4, CPU/2))` | 单分片并发 merge 线程数。SSD 用默认值，HDD 建议设 `1` |
| `index.merge.policy.max_merged_segment`  | 节点级 `5GB`               | 合并后 Segment 的最大大小上限                  |
| `index.merge.policy.segments_per_tier`   | `10.0`                  | 每层允许的 Segment 数量（越大合并越少但 Segment 越多） |
| `index.merge.policy.max_merge_at_once`   | `10`                    | 一次合并操作最多合并的 Segment 数                |
| `index.merge.policy.floor_segment`       | `2mb`                   | 小于此大小的 Segment 会被优先合并                |
| `index.merge.policy.deletes_pct_allowed` | `20.0`                  | 允许的删除文档比例，超过会触发 merge                |


### 6.5 Merge 的性能影响

Merge 是一把双刃剑：

- **好处**：减少 Segment 数量、回收删除文档的磁盘空间、提升查询性能。
- **代价**：消耗磁盘 IO 和 CPU，可能导致写入延迟抖动。

ES 通过 **IO 限速（auto-throttling）** 平衡 Merge 与写入/查询的资源竞争。当 Merge 跟不上写入时，ES 会**限速索引操作**（indexing throttling），确保 Merge 追上进度。

### 6.6 磁盘水位保护


| 参数                                               | 默认值     | 说明                     |
| ------------------------------------------------ | ------- | ---------------------- |
| `indices.merge.disk.watermark.high`              | `95%`   | 磁盘使用超过此比例时停止调度新的 Merge |
| `indices.merge.disk.watermark.high.max_headroom` | `100GB` | 百分比模式下的最大剩余空间上限        |
| `indices.merge.disk.check_interval`              | `5s`    | 磁盘空间检查间隔               |


---

## 7. Force Merge：手动触发段合并

### 7.1 适用场景

**只对不再写入的索引使用 Force Merge**——如按天滚动的日志索引、已关闭写入的历史数据索引。

```bash
POST /my_logs_2024-01/_forcemerge?max_num_segments=1
```

`max_num_segments=1` 将所有 Segment 合并为一个，最大化查询性能。

### 7.2 不适用场景

**不要对正在写入的索引 Force Merge**：

- Force Merge 会生成非常大的 Segment，后续的正常 Merge 难以处理。
- 正在写入时 Force Merge 会造成严重的资源竞争。

---

## 8. 写入性能调优清单

### 8.1 初始数据导入（批量灌数据）

```json
PUT /my_index/_settings
{
  "index.refresh_interval": "-1",
  "index.number_of_replicas": 0
}
```

导入完成后恢复：

```json
PUT /my_index/_settings
{
  "index.refresh_interval": "1s",
  "index.number_of_replicas": 1
}

POST /my_index/_refresh
POST /my_index/_forcemerge?max_num_segments=5
```

### 8.2 日志/监控场景（高吞吐、低实时性要求）

```json
PUT /my_logs/_settings
{
  "index.translog.durability": "async",
  "index.translog.sync_interval": "5s",
  "index.refresh_interval": "30s"
}
```

### 8.3 业务搜索场景（平衡吞吐与实时性）

保持默认配置即可，ES 的默认值已经是非常好的平衡点：

- `refresh_interval: 1s` — 近实时搜索
- `translog.durability: request` — 零数据丢失
- Merge 自动调度

### 8.4 调优决策矩阵


| 维度                    | 初始导入       | 日志/监控       | 业务搜索          |
| --------------------- | ---------- | ----------- | ------------- |
| `refresh_interval`    | `-1`       | `30s`~`60s` | `1s`（默认）      |
| `translog.durability` | `request`  | `async`     | `request`（默认） |
| `number_of_replicas`  | `0`        | `1`         | `1`（默认）       |
| Bulk 批大小              | 5000~10000 | 1000~5000   | 500~2000      |
| 导入后 Force Merge       | 是          | 否（滚动索引自然合并） | 否             |


---

## 9. 写入链路的可观测性

### 9.1 关键监控指标

```bash
# 索引级指标：refresh/flush/merge 次数和耗时
GET /my_index/_stats/refresh,flush,merge?pretty

# 节点级线程池：bulk/write 队列和拒绝数
GET /_cat/thread_pool/write?v&h=name,node_name,active,queue,rejected

# Segment 数量和大小
GET /_cat/segments/my_index?v&h=index,shard,segment,docs.count,size

# Translog 统计
GET /my_index/_stats/translog?pretty
```

### 9.2 关键告警指标


| 指标                                   | 含义               | 告警阈值建议                    |
| ------------------------------------ | ---------------- | ------------------------- |
| `write` 线程池 `rejected`               | 写入请求被拒绝          | > 0 即告警                   |
| `refresh.total_time` 增长率             | Refresh 耗时趋势     | P99 > 500ms               |
| `merges.total_time` 增长率              | Merge 耗时趋势       | 持续增长需关注                   |
| `translog.uncommitted_size_in_bytes` | 未提交的 Translog 大小 | 接近 `flush_threshold_size` |
| Segment 数量                           | 每个分片的 Segment 数  | 单分片 > 50 个需关注             |


---

# 总结

- 写入链路五个环节各司其职：**Bulk 合并网络开销 → Translog 保障持久性 → Refresh 提供可见性 → Flush 执行持久化 → Merge 优化查询性能**。
- Refresh 和 Flush 是两个完全不同的概念——前者解决"搜不到"，后者解决"恢复慢"。
- `translog.durability` 是吞吐与数据安全的核心权衡点——生产业务用 `request`，日志场景可用 `async`。
- Bulk 的部分失败语义是最常被忽略的坑——必须逐条检查 `BulkItemResponse`。
- 初始导入时关闭 refresh + 去副本是最简单有效的吞吐优化。
- Force Merge 只对不再写入的索引使用。

---

## 练习题

1. 比较 Bulk 批大小为 100、1000、5000 时的写入吞吐（docs/s），找到最优批大小。
2. 分别设置 `refresh_interval` 为 `1s`、`30s`、`-1`，写入相同数量的文档后对比：写入耗时、Segment 数量、查询可见性延迟。
3. 将 `translog.durability` 从 `request` 切换为 `async`，压测写入吞吐差异。
4. 对一个已停写的索引执行 `_forcemerge?max_num_segments=1`，对比前后的 Segment 数量和查询延迟。
5. 使用 `_stats/refresh,flush,merge,translog` 分析一次写入高峰下的延迟波动原因。

---

## 实战（curl）

### 创建测试索引

```bash
curl -u "$ES_USER:$ES_PASS" -X PUT "$ES_URL/write_demo" \
  -H "Content-Type: application/json" \
  -d '{
    "settings": {
      "number_of_shards": 1,
      "number_of_replicas": 0,
      "refresh_interval": "1s"
    },
    "mappings": { "properties": {
      "title":    { "type": "text" },
      "category": { "type": "keyword" },
      "price":    { "type": "double" },
      "ts":       { "type": "date" }
    }}
  }'
```

### Bulk 写入

```bash
curl -u "$ES_USER:$ES_PASS" -X POST "$ES_URL/write_demo/_bulk" \
  -H "Content-Type: application/x-ndjson" \
  -d '{"index":{"_id":"1"}}
{"title":"Elasticsearch实战","category":"book","price":99.0,"ts":"2025-01-01T00:00:00Z"}
{"index":{"_id":"2"}}
{"title":"Lucene原理与实践","category":"book","price":79.0,"ts":"2025-01-02T00:00:00Z"}
{"index":{"_id":"3"}}
{"title":"机械键盘","category":"peripheral","price":599.0,"ts":"2025-01-03T00:00:00Z"}
{"index":{"_id":"4"}}
{"title":"显示器支架","category":"accessory","price":199.0,"ts":"2025-01-04T00:00:00Z"}
{"index":{"_id":"5"}}
{"title":"降噪耳机","category":"peripheral","price":1299.0,"ts":"2025-01-05T00:00:00Z"}
'
```

### 观察 Refresh / Flush / Merge / Translog 统计

```bash
# Refresh 统计
curl -u "$ES_USER:$ES_PASS" "$ES_URL/write_demo/_stats/refresh?pretty&filter_path=_all.primaries.refresh"

# Flush 统计
curl -u "$ES_USER:$ES_PASS" "$ES_URL/write_demo/_stats/flush?pretty&filter_path=_all.primaries.flush"

# Merge 统计
curl -u "$ES_USER:$ES_PASS" "$ES_URL/write_demo/_stats/merge?pretty&filter_path=_all.primaries.merges"

# Translog 统计
curl -u "$ES_USER:$ES_PASS" "$ES_URL/write_demo/_stats/translog?pretty&filter_path=_all.primaries.translog"

# Segment 数量和大小
curl -u "$ES_USER:$ES_PASS" "$ES_URL/_cat/segments/write_demo?v&h=index,shard,segment,docs.count,size,committed"
```

### 调整 Refresh Interval

```bash
# 关闭定时 Refresh（初始导入场景）
curl -u "$ES_USER:$ES_PASS" -X PUT "$ES_URL/write_demo/_settings" \
  -H "Content-Type: application/json" \
  -d '{ "index.refresh_interval": "-1" }'

# 大批量写入...

# 恢复并手动触发 Refresh
curl -u "$ES_USER:$ES_PASS" -X PUT "$ES_URL/write_demo/_settings" \
  -H "Content-Type: application/json" \
  -d '{ "index.refresh_interval": "1s" }'

curl -u "$ES_USER:$ES_PASS" -X POST "$ES_URL/write_demo/_refresh"
```

### 调整 Translog Durability

```bash
# 切换为 async 模式（日志场景）
curl -u "$ES_USER:$ES_PASS" -X PUT "$ES_URL/write_demo/_settings" \
  -H "Content-Type: application/json" \
  -d '{
    "index.translog.durability": "async",
    "index.translog.sync_interval": "5s"
  }'

# 恢复为 request 模式
curl -u "$ES_USER:$ES_PASS" -X PUT "$ES_URL/write_demo/_settings" \
  -H "Content-Type: application/json" \
  -d '{ "index.translog.durability": "request" }'
```

### Force Merge（仅对停写索引）

```bash
# 先设为只读
curl -u "$ES_USER:$ES_PASS" -X PUT "$ES_URL/write_demo/_settings" \
  -H "Content-Type: application/json" \
  -d '{ "index.blocks.write": true }'

# Force Merge
curl -u "$ES_USER:$ES_PASS" -X POST "$ES_URL/write_demo/_forcemerge?max_num_segments=1&pretty"

# 对比 Segment 数量
curl -u "$ES_USER:$ES_PASS" "$ES_URL/_cat/segments/write_demo?v&h=index,shard,segment,docs.count,size"

# 解除只读
curl -u "$ES_USER:$ES_PASS" -X PUT "$ES_URL/write_demo/_settings" \
  -H "Content-Type: application/json" \
  -d '{ "index.blocks.write": false }'
```

### 线程池监控

```bash
# 查看 write 线程池状态
curl -u "$ES_USER:$ES_PASS" "$ES_URL/_cat/thread_pool/write?v&h=name,node_name,active,queue,rejected"

# 查看 merge 线程池
curl -u "$ES_USER:$ES_PASS" "$ES_URL/_cat/thread_pool/merge?v&h=name,node_name,active,queue,rejected"
```

### 清理

```bash
curl -u "$ES_USER:$ES_PASS" -X DELETE "$ES_URL/write_demo"
```

---

## 实战（Java SDK）

```java
// ---------- Bulk 写入（带错误检查）----------
var bulkReq = new BulkRequest.Builder();
for (int i = 1; i <= 100; i++) {
    int idx = i;
    bulkReq.operations(op -> op.index(io -> io
        .index("write_demo")
        .id(String.valueOf(idx))
        .document(Map.of(
            "title", "商品-" + idx,
            "category", "cat-" + (idx % 5),
            "price", 10.0 * idx,
            "ts", "2025-01-01T00:00:00Z"))));
}

var bulkResp = client.bulk(bulkReq.build());

if (bulkResp.errors()) {
    bulkResp.items().stream()
        .filter(item -> item.error() != null)
        .forEach(item -> System.err.println(
            "Failed item " + item.id() + ": " + item.error().reason()));
} else {
    System.out.println("All " + bulkResp.items().size() + " items succeeded, took: " + bulkResp.took() + "ms");
}

// ---------- 调整 refresh_interval ----------
client.indices().putSettings(p -> p.index("write_demo")
    .settings(s -> s.refreshInterval(t -> t.time("30s"))));

// ---------- 调整 translog durability ----------
client.indices().putSettings(p -> p.index("write_demo")
    .settings(s -> s.translog(tl -> tl.durability("async").syncInterval(t -> t.time("5s")))));

// ---------- 查看索引统计 ----------
var stats = client.indices().stats(s -> s.index("write_demo")
    .metric("refresh", "flush", "merge", "translog"));

var primaries = stats.primaries();
System.out.println("Refresh count: " + primaries.refresh().total());
System.out.println("Flush count: " + primaries.flush().total());
System.out.println("Merge count: " + primaries.merges().total());
System.out.println("Translog ops: " + primaries.translog().operations());
System.out.println("Translog uncommitted size: " + primaries.translog().uncommittedSizeInBytes() + " bytes");

// ---------- 手动 Refresh ----------
client.indices().refresh(r -> r.index("write_demo"));

// ---------- Force Merge（仅对停写索引）----------
client.indices().putSettings(p -> p.index("write_demo")
    .settings(s -> s.blocksWrite(true)));
client.indices().forcemerge(f -> f.index("write_demo").maxNumSegments(1L));
client.indices().putSettings(p -> p.index("write_demo")
    .settings(s -> s.blocksWrite(false)));

// ---------- 清理 ----------
client.indices().delete(d -> d.index("write_demo"));
```

