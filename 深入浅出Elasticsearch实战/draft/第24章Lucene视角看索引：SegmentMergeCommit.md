

# 第24章 Lucene 视角看索引：Segment、Merge、Commit

# 背景

前面的章节一直在 Elasticsearch 的抽象层面讨论索引、分片、写入链路。但 Elasticsearch 的搜索与存储引擎是 Apache Lucene——每个分片本质上就是一个独立的 Lucene Index。理解 Lucene 层面的 Segment、Merge、Commit 机制，才能真正解释"为什么删除不释放磁盘空间""为什么 force_merge 能提升查询性能""Refresh 和 Flush 到底有什么区别"等高频问题。本章将带你走进 Lucene 内部，从物理文件到数据结构，建立一套完整的底层认知。

## 本章目标

- 理解 ES 分片与 Lucene Index 的映射关系，掌握 Segment 与 Commit Point 的物理含义。
- 掌握倒排索引三层结构（FST → Term Dictionary → Posting List）与 Doc Values 列式存储。
- 理解 Segment 不可变性设计的动机与好处。
- 掌握 TieredMergePolicy 的合并策略与关键调优参数。
- 区分 Lucene Commit、ES Refresh、ES Flush 三者的语义，理解 NRT 原理。
- 学会使用 `_cat/segments`、`_stats`、`force_merge` 观察与调优段信息。

---

## 1. Lucene 索引的物理结构

### 1.1 分片 = Lucene Index

Elasticsearch 的每个分片（Shard）在底层就是一个完整的 Lucene Index。一个 Lucene Index 由多个 **Segment** 和一个 **Commit Point** 组成：

```
ES Index (e.g. "products")
  ├── Shard 0  ─→  Lucene Index
  │                  ├── Segment_0  (不可变)
  │                  ├── Segment_1  (不可变)
  │                  ├── Segment_2  (不可变)
  │                  └── segments_N  (Commit Point, 记录当前活跃段列表)
  ├── Shard 1  ─→  Lucene Index
  │                  ├── Segment_0
  │                  └── segments_N
  └── ...
```

**Commit Point**（文件名为 `segments_N`，N 为递增的 generation 编号）记录了当前 Lucene Index 中所有有效 Segment 的元数据。Lucene 打开索引时，就是读取最新的 `segments_N` 来确定需要加载哪些段。

### 1.2 Segment 的文件组成

每个 Segment 由一组文件构成，各文件承担不同的存储职责：

| 扩展名 | 内容 | 用途 |
|--------|------|------|
| `.tim` / `.tip` | Term Dictionary / Term Index | 倒排索引的词典与前缀索引（FST） |
| `.doc` | Frequencies & Skip Data | 词频及跳表，用于评分与快速跳转 |
| `.pos` | Positions | 词项在文档中的位置，支持短语查询 |
| `.pay` | Payloads & Offsets | 自定义负载与偏移量，用于高亮等 |
| `.dvd` / `.dvm` | Doc Values Data / Meta | 列式存储，支持排序、聚合、脚本 |
| `.fdt` / `.fdx` | Stored Fields Data / Index | 原始字段存储，`_source` 就在这里 |
| `.si` / `.fnm` | Segment Info / Field Infos | 段元数据与字段属性 |
| `.liv` | Live Docs | 标记已删除文档的 bit set |
| `.nvd` / `.nvm` | Norms | 字段长度归一化因子，影响评分 |

一个拥有 10 个 Segment 的分片，磁盘上可能存在上百个文件。这也是为什么 `ulimit -n`（文件描述符限制）在 ES 集群中非常重要。

---

## 2. Segment 不可变性

### 2.1 核心规则

Segment 一旦写入磁盘就**永远不会被修改**。所有"变更"都通过以下方式实现：

- **新增文档**：写入一个新的 Segment。
- **删除文档**：在 `.liv` 文件中将该文档标记为已删除，原 Segment 不变。
- **更新文档**：先在旧 Segment 中标记删除，再在新 Segment 中写入新版本。

```
时间线：
  t1: 写入 doc1, doc2       → Segment_0 [doc1, doc2]
  t2: 写入 doc3             → Segment_1 [doc3]
  t3: 删除 doc1             → Segment_0.liv 标记 doc1 已删除
  t4: 更新 doc2 (v2)        → Segment_0.liv 标记 doc2 已删除
                               Segment_2 [doc2_v2]
```

已删除的文档仍然占据磁盘空间，直到 **Merge** 时才会被真正物理清除。

### 2.2 不可变性的好处

| 好处 | 说明 |
|------|------|
| 无锁并发读 | 读操作无需加锁，多线程可安全并发访问同一 Segment |
| 缓存友好 | Segment 内容不变，OS 文件系统缓存（page cache）命中率极高 |
| 压缩高效 | 不可变数据可使用更激进的压缩算法，无需预留修改空间 |
| 预计算优化 | 倒排索引的 Skip List、FST 等结构一次构建后不再变化 |

不可变性是 Lucene 高性能的基石。代价是删除不即时释放空间、更新本质是删除+重写，需要 Merge 来回收。

---

## 3. 倒排索引结构

倒排索引是 Lucene 全文检索的核心。它的查找路径分为三层：

```
查询 "elasticsearch"
        │
        ▼
  ┌─────────────────┐
  │  Term Index     │  FST (Finite State Transducer)
  │  内存中的前缀索引 │  快速定位 Term Dictionary 的磁盘块位置
  └────────┬────────┘
           ▼
  ┌─────────────────┐
  │ Term Dictionary │  .tim 文件
  │ 有序词典        │  存储所有 Term，按字典序排列
  └────────┬────────┘
           ▼
  ┌─────────────────┐
  │  Posting List   │  .doc / .pos 文件
  │  倒排链          │  记录每个 Term 对应的文档 ID 列表
  └─────────────────┘
```

### 3.1 Term Index（FST）

FST 是一种极度压缩的有限状态转换器，驻留在内存中。它不存储完整的 Term，而是存储 Term 的前缀到 Term Dictionary 磁盘块偏移量的映射。通过 FST，Lucene 可以在 O(term_length) 时间内定位到目标 Term 所在的磁盘块，避免全量扫描词典。

### 3.2 Term Dictionary

`.tim` 文件按字典序存储所有 Term，并按块（block）组织。每个块内的 Term 使用前缀压缩，大幅减少存储空间。查找时先通过 FST 跳到目标块，再在块内二分查找。

### 3.3 Posting List

Posting List 记录了包含某个 Term 的所有文档 ID（doc_id），以及词频（term frequency）、位置（position）等信息。为了支持高效的布尔查询合并（AND/OR），Posting List 内部使用 **Skip List**（跳表），使得交集/并集操作可以快速跳过不相关的文档区间。

```
Term "error" 的 Posting List（简化）：

  doc_ids: [3, 15, 42, 78, 103, 256, 512, ...]
                ↑ skip        ↑ skip
  Skip List 允许直接跳到 >= 目标值的位置，
  在 AND 操作中避免逐个遍历
```

---

## 4. Doc Values：列式存储

### 4.1 什么是 Doc Values

倒排索引擅长的是"给定 Term，找文档"。但排序、聚合、脚本需要的是"给定文档，取字段值"——这正是 **Doc Values** 的设计目标。

Doc Values 采用**列式存储**：同一字段的所有文档值连续存放在磁盘上（`.dvd` 文件），并通过 `.dvm` 文件记录元数据。查询时可以顺序读取整列数据，对 CPU 缓存和磁盘 IO 都非常友好。

```
行式存储（_source）：                 列式存储（Doc Values）：
  doc0: {price:10, cat:"A"}           price列:  [10, 25, 8, 30, ...]
  doc1: {price:25, cat:"B"}           cat列:    ["A","B","A","C", ...]
  doc2: {price:8,  cat:"A"}
  doc3: {price:30, cat:"C"}
```

### 4.2 Doc Values vs fielddata

| 维度 | Doc Values | fielddata |
|------|-----------|-----------|
| 存储位置 | 磁盘（OS page cache） | JVM 堆内存 |
| 构建时机 | 索引时写入 | 首次查询时加载 |
| 内存压力 | 低，由 OS 管理 | 高，占用堆内存，可能 OOM |
| 适用字段 | keyword / numeric / date / ip / geo | text（需显式开启） |
| 默认状态 | 所有非 text 字段默认启用 | 默认关闭（text 字段） |

**最佳实践**：永远不要对 `text` 字段开启 fielddata 用于聚合。正确做法是使用 `text` + `keyword` 多字段映射，对 keyword 子字段做聚合。

### 4.3 禁用 Doc Values

对于只用于过滤、不需要排序/聚合的 keyword 字段，可以在 mapping 中设置 `"doc_values": false` 节省磁盘。禁用后该字段无法用于排序、聚合、脚本中的 `doc['field']` 访问。

---

## 5. Merge 详解

### 5.1 为什么需要 Merge

随着不断写入，Segment 数量持续增长。每次查询需要遍历所有 Segment 并合并结果。Segment 越多：

- 查询延迟越高（需要合并更多结果集）
- 文件描述符消耗越大
- 已删除文档持续占据磁盘空间

Merge 的核心任务：将多个小 Segment 合并为更少的大 Segment，同时物理清除已删除文档。

### 5.2 TieredMergePolicy

Elasticsearch 默认使用 Lucene 的 **TieredMergePolicy**。其核心思想是按段大小分层选择合并候选：

```
合并选择逻辑（简化）：

1. 将所有 Segment 按大小降序排列
2. 跳过大于 max_merged_segment (默认 5GB) 的段
3. 从剩余段中找到"得分最优"的一组段进行合并
   - 得分综合考虑：段大小均匀性、删除文档比例、段数量
4. 每次合并最多 max_merge_at_once (默认 10) 个段
5. 目标是将每层的段数保持在 segments_per_tier (默认 10) 以内
```

### 5.3 合并过程

```
合并前：
  Segment_0 (50MB, 20% deleted)
  Segment_1 (40MB)
  Segment_2 (30MB)
  Segment_3 (35MB)

    ┌──────────────────────┐
    │  Merge 线程工作       │
    │  1. 选择候选段        │
    │  2. 读取所有活跃文档   │  ← 跳过已删除文档
    │  3. 写入新的段        │
    │  4. 更新 Commit Point │
    │  5. 删除旧段文件      │
    └──────────────────────┘

合并后：
  Segment_4 (125MB, 0% deleted)  ← 旧段被替换
```

### 5.4 Merge 对性能的影响

| 影响 | 说明 |
|------|------|
| IO 竞争 | Merge 是 IO 密集操作，与正常搜索和写入争抢磁盘带宽 |
| Indexing Throttling | 当 Merge 跟不上写入速度时，ES 会自动限流写入 |
| CPU 消耗 | 合并过程需要解压、重新编码、压缩数据 |
| 临时磁盘翻倍 | 合并期间新旧段同时存在，需要额外磁盘空间 |

### 5.5 关键调优参数

```json
PUT my_index/_settings
{
  "index.merge.policy.max_merged_segment": "5gb",
  "index.merge.policy.segments_per_tier": 10,
  "index.merge.policy.max_merge_at_once": 10,
  "index.merge.policy.deletes_pct_allowed": 20.0
}
```

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `max_merged_segment` | 5gb | 合并后段的最大体积，超过此值的段不参与自动合并 |
| `segments_per_tier` | 10 | 每层允许的段数，值越小合并越频繁 |
| `max_merge_at_once` | 10 | 单次合并最多包含的段数 |
| `deletes_pct_allowed` | 20.0 | 已删除文档占比超过此阈值时优先合并 |

---

## 6. Commit 与 Flush

### 6.1 Lucene Commit 的含义

Lucene 的 Commit 操作包含两个关键步骤：

1. **写入 Commit Point**：生成新的 `segments_N` 文件，记录当前所有活跃 Segment。
2. **fsync**：将所有新 Segment 文件和 Commit Point 强制同步到磁盘。

Commit 之后，即使进程崩溃或断电，数据也不会丢失——因为所有内容已物理持久化。

### 6.2 ES Flush = Lucene Commit + 清理 Translog

在 Elasticsearch 层面，Flush 操作就是触发一次 Lucene Commit，然后清理已提交的 Translog 记录：

```
ES Flush 流程：
  1. 将内存缓冲区中待写入的数据刷入新 Segment
  2. 执行 Lucene Commit（写 segments_N + fsync）
  3. 删除已包含在 Commit 中的 Translog 条目
```

### 6.3 ES Refresh vs Flush vs Lucene Commit

| 操作 | 做了什么 | 数据安全性 | 性能开销 |
|------|---------|-----------|---------|
| **Refresh** | 将内存缓冲区写为新 Segment，打开新 Searcher | 不安全（未 fsync） | 低 |
| **Flush** | Refresh + Lucene Commit + 清理 Translog | 安全（已 fsync） | 高 |
| **Lucene Commit** | 写 segments_N + fsync 所有新文件 | 安全 | 高 |

Refresh 让数据可搜索但不保证持久化；Translog 在 Flush 之前承担持久化职责。这就是 ES 能做到"近实时搜索"同时保证数据安全的核心设计。

---

## 7. 近实时搜索（NRT）

### 7.1 NRT 原理

传统 Lucene 搜索要求先执行完整 Commit 才能看到新数据。Lucene 2.9 引入了 **Near-Real-Time（NRT）** 机制：`IndexWriter.getReader()` 可以直接从内存缓冲区获取一个包含未提交数据的 `IndexReader`，无需 fsync。

```
传统流程：  写入 → Commit (fsync) → 打开 Reader → 可搜索
NRT 流程：  写入 → getReader()    → 可搜索（无需 fsync）
```

### 7.2 ES Refresh 基于 NRT

Elasticsearch 的 Refresh 就是利用 NRT：每秒（默认 `refresh_interval: 1s`）调用一次 `IndexWriter.getReader()`，得到新的 `DirectoryReader` 打开给搜索线程。这就是"1 秒延迟的近实时搜索"的由来。

关键点：Refresh 不执行 fsync，所以开销远低于 Flush。但如果进程崩溃，未 Flush 的数据依赖 Translog 重放恢复。

---

## 8. 段观察与调优

### 8.1 _cat/segments：查看段详情

```
GET _cat/segments/my_index?v&s=segment&h=index,shard,prirep,segment,generation,docs.count,docs.deleted,size,size.memory
```

各字段含义：

| 字段 | 说明 |
|------|------|
| `shard` / `prirep` | 分片编号 / `p`=主分片 `r`=副本 |
| `segment` / `generation` | 段名称（如 `_0`, `_1a`）/ generation 编号 |
| `docs.count` / `docs.deleted` | 活跃文档数 / 已标记删除的文档数 |
| `size` / `size.memory` | 段的磁盘大小 / 堆内存占用（FST、Norms 等） |

### 8.2 _stats/merge：查看合并统计

```
GET my_index/_stats/merge
```

重点关注：

- `merges.total`：总合并次数
- `merges.total_time_in_millis`：总合并耗时
- `merges.current`：当前正在进行的合并数

### 8.3 force_merge：手动强制合并

`force_merge` 将段数合并到指定数量。**仅适用于只读索引**（如已 Rollover 的时序索引、ILM Warm/Cold 阶段的索引）：

```
POST my_index/_forcemerge?max_num_segments=1
```

**注意**：对仍在写入的索引执行 force_merge 会导致大小段反复合并；操作期间需要约等于索引大小的额外磁盘空间；可通过 `wait_for_completion=false` 改为异步执行。

---

## 总结

| 概念 | 要点 |
|------|------|
| Segment | 不可变的数据单元，一个分片包含多个 Segment |
| 不可变性 | 写入→新段，删除→标记，更新→标记+新写；带来无锁读和缓存友好 |
| 倒排索引 | Term Index (FST) → Term Dictionary → Posting List (Skip List) |
| Doc Values | 列式存储，用于排序/聚合/脚本，与 fielddata 互斥 |
| Merge | TieredMergePolicy 按大小分层合并，清除已删除文档，回收空间 |
| Commit/Flush | Lucene Commit = 写 segments_N + fsync；ES Flush = Commit + 清 Translog |
| NRT | IndexWriter.getReader() 不需 Commit 即可搜索新数据；Refresh 基于 NRT |
| force_merge | 仅用于只读索引，减少段数提升查询性能 |

---

## 练习题

1. 删除一条文档后，磁盘空间为什么没有立即释放？什么时候才会真正释放？
2. 解释 Refresh 和 Flush 的区别：哪个让数据可搜索？哪个保证数据持久化？
3. 为什么不建议对仍在写入的索引执行 `force_merge`？
4. Term Index (FST) 驻留在什么位置？它存储的是完整的 Term 还是前缀？
5. Doc Values 和 fielddata 的核心区别是什么？在什么场景下必须使用 fielddata？
6. 一个分片有 50 个 Segment，每个 100MB，`max_merged_segment` 设为 5GB。TieredMergePolicy 最终大约会合并成几个段？
7. 如何通过 `_cat/segments` 判断一个索引是否有大量待清除的已删除文档？

---

## 实战（curl）

```bash
# ---------- 创建索引（单分片，便于观察段）----------
curl -s -u "$ES_USER:$ES_PASS" -X PUT "$ES_URL/segment_demo" -H 'Content-Type: application/json' -d '{
  "settings": { "number_of_shards": 1, "number_of_replicas": 0, "refresh_interval": "1s" },
  "mappings": { "properties": {
    "title": { "type": "text" }, "category": { "type": "keyword" }, "price": { "type": "float" }
  }}
}'

# ---------- 第一批写入 ----------
curl -s -u "$ES_USER:$ES_PASS" -X POST "$ES_URL/_bulk" -H 'Content-Type: application/x-ndjson' -d '
{"index":{"_index":"segment_demo","_id":"1"}}
{"title":"Elasticsearch 实战","category":"tech","price":59.9}
{"index":{"_index":"segment_demo","_id":"2"}}
{"title":"Lucene 原理剖析","category":"tech","price":49.0}
{"index":{"_index":"segment_demo","_id":"3"}}
{"title":"数据库系统概念","category":"database","price":89.0}
'
sleep 2

# ---------- 第二批写入（产生新 Segment）----------
curl -s -u "$ES_USER:$ES_PASS" -X POST "$ES_URL/_bulk" -H 'Content-Type: application/x-ndjson' -d '
{"index":{"_index":"segment_demo","_id":"4"}}
{"title":"分布式系统设计","category":"system","price":75.0}
{"index":{"_index":"segment_demo","_id":"5"}}
{"title":"深入理解 JVM","category":"tech","price":68.0}
'
curl -s -u "$ES_USER:$ES_PASS" -X POST "$ES_URL/segment_demo/_refresh"

# ---------- 查看段信息 ----------
curl -s -u "$ES_USER:$ES_PASS" "$ES_URL/_cat/segments/segment_demo?v&h=index,shard,prirep,segment,generation,docs.count,docs.deleted,size"

# ---------- 删除文档，观察 docs.deleted ----------
curl -s -u "$ES_USER:$ES_PASS" -X DELETE "$ES_URL/segment_demo/_doc/1"
curl -s -u "$ES_USER:$ES_PASS" -X POST "$ES_URL/segment_demo/_refresh"
curl -s -u "$ES_USER:$ES_PASS" "$ES_URL/_cat/segments/segment_demo?v&h=index,shard,prirep,segment,generation,docs.count,docs.deleted,size"

# ---------- 更新文档，观察段变化 ----------
curl -s -u "$ES_USER:$ES_PASS" -X POST "$ES_URL/segment_demo/_update/2" -H 'Content-Type: application/json' -d '{ "doc": { "price": 55.0 } }'
curl -s -u "$ES_USER:$ES_PASS" -X POST "$ES_URL/segment_demo/_refresh"
curl -s -u "$ES_USER:$ES_PASS" "$ES_URL/_cat/segments/segment_demo?v&h=index,shard,prirep,segment,generation,docs.count,docs.deleted,size"

# ---------- 查看 Merge 统计 ----------
curl -s -u "$ES_USER:$ES_PASS" "$ES_URL/segment_demo/_stats/merge?filter_path=_all.primaries.merges"

# ---------- force_merge（设为只读后执行）----------
curl -s -u "$ES_USER:$ES_PASS" -X PUT "$ES_URL/segment_demo/_settings" -H 'Content-Type: application/json' -d '{ "index.blocks.write": true }'
curl -s -u "$ES_USER:$ES_PASS" -X POST "$ES_URL/segment_demo/_forcemerge?max_num_segments=1"
curl -s -u "$ES_USER:$ES_PASS" "$ES_URL/_cat/segments/segment_demo?v&h=index,shard,prirep,segment,generation,docs.count,docs.deleted,size"

# ---------- 查看综合统计 ----------
curl -s -u "$ES_USER:$ES_PASS" "$ES_URL/segment_demo/_stats/refresh,flush,merge?filter_path=_all.primaries"

# ---------- 清理 ----------
curl -s -u "$ES_USER:$ES_PASS" -X DELETE "$ES_URL/segment_demo"
```

---

## 实战（Java SDK）

```java
// ---------- 创建索引 ----------
client.indices().create(c -> c.index("segment_demo")
    .settings(s -> s.numberOfShards("1").numberOfReplicas("0").refreshInterval(t -> t.time("1s")))
    .mappings(m -> m
        .properties("title", p -> p.text(tx -> tx))
        .properties("category", p -> p.keyword(k -> k))
        .properties("price", p -> p.float_(f -> f))));

// ---------- 分两批写入，产生多个 Segment ----------
client.bulk(b -> b
    .operations(op -> op.index(i -> i.index("segment_demo").id("1")
        .document(Map.of("title", "Elasticsearch 实战", "category", "tech", "price", 59.9))))
    .operations(op -> op.index(i -> i.index("segment_demo").id("2")
        .document(Map.of("title", "Lucene 原理剖析", "category", "tech", "price", 49.0))))
    .operations(op -> op.index(i -> i.index("segment_demo").id("3")
        .document(Map.of("title", "数据库系统概念", "category", "database", "price", 89.0)))));
client.indices().refresh(r -> r.index("segment_demo"));

client.bulk(b -> b
    .operations(op -> op.index(i -> i.index("segment_demo").id("4")
        .document(Map.of("title", "分布式系统设计", "category", "system", "price", 75.0))))
    .operations(op -> op.index(i -> i.index("segment_demo").id("5")
        .document(Map.of("title", "深入理解 JVM", "category", "tech", "price", 68.0)))));
client.indices().refresh(r -> r.index("segment_demo"));

// ---------- 查看段信息 ----------
client.cat().segments(s -> s.index("segment_demo")).forEach(seg ->
    System.out.println("segment=" + seg.segment() + ", docs=" + seg.docsCount()
        + ", deleted=" + seg.docsDeleted() + ", size=" + seg.size()));

// ---------- 删除 + 更新，观察 docs.deleted 和新段产生 ----------
client.delete(d -> d.index("segment_demo").id("1"));
client.update(u -> u.index("segment_demo").id("2").doc(Map.of("price", 55.0)), Map.class);
client.indices().refresh(r -> r.index("segment_demo"));

System.out.println("=== After delete & update ===");
client.cat().segments(s -> s.index("segment_demo")).forEach(seg ->
    System.out.println("segment=" + seg.segment() + ", docs=" + seg.docsCount()
        + ", deleted=" + seg.docsDeleted()));

// ---------- Merge 统计 ----------
var merges = client.indices().stats(s -> s.index("segment_demo").metric("merge")).primaries().merges();
System.out.println("Merge total: " + merges.total() + ", time: " + merges.totalTimeInMillis() + "ms");

// ---------- force_merge（设为只读后执行）----------
client.indices().putSettings(p -> p.index("segment_demo").settings(s -> s.blocksWrite(true)));
client.indices().forcemerge(f -> f.index("segment_demo").maxNumSegments(1L));

System.out.println("=== After force_merge ===");
client.cat().segments(s -> s.index("segment_demo")).forEach(seg ->
    System.out.println("segment=" + seg.segment() + ", docs=" + seg.docsCount()
        + ", deleted=" + seg.docsDeleted() + ", size=" + seg.size()));

// ---------- 综合统计 ----------
var pri = client.indices().stats(s -> s.index("segment_demo")
    .metric("refresh", "flush", "merge")).primaries();
System.out.println("Refresh: " + pri.refresh().total()
    + ", Flush: " + pri.flush().total()
    + ", Merge: " + pri.merges().total());

// ---------- 清理 ----------
client.indices().delete(d -> d.index("segment_demo"));
```
