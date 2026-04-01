# 第26章 缓存与内存：Heap、文件缓存与断路器

## 背景

Elasticsearch 是一个内存密集型系统。搜索时需要在内存中维护倒排索引的元数据、缓存热点查询结果、保持 fielddata 供聚合使用；写入时需要 Indexing Buffer 暂存文档。与此同时，Lucene 底层大量依赖操作系统的 Page Cache 来加速磁盘读取。如果对内存分配理解不清，轻则缓存命中率低、查询延迟飙升，重则触发 `CircuitBreakingException` 甚至 OOM 导致节点宕机。

本章将从 JVM Heap 的整体规划讲起，逐一拆解 ES 内部的三大缓存、文件系统缓存的角色，以及断路器（Circuit Breaker）这道最后的内存防线。

## 本章目标

1. 理解 ES 内存架构全景：JVM Heap、Off-Heap、OS Page Cache 三层关系
2. 掌握 Heap 内部各区域的默认配比与调优原则
3. 深入 Node Query Cache、Request Cache、Fielddata Cache 的工作机制与失效策略
4. 理解文件系统缓存对搜索性能的决定性作用
5. 掌握断路器体系的配置与 `CircuitBreakingException` 排查方法
6. 区分 `doc_values` 与 `fielddata` 的使用场景

---

## 1. ES 内存架构全景

ES 节点的内存使用分为三个层次：

### 1.1 JVM Heap

JVM Heap 是 ES 进程直接管理的内存区域，用于存放 Java 对象。核心原则：

- **不超过物理内存的 50%**：剩余留给文件系统缓存
- **不超过 31GB**：超过此阈值 JVM 无法使用 Compressed Oops（压缩指针），实际可用内存反而可能减少
- 推荐配置：`-Xms` 与 `-Xmx` 设置为相同值，避免堆动态伸缩带来的 GC 开销

```yaml
# jvm.options 推荐配置（64GB 物理内存的节点）
-Xms30g
-Xmx30g
```

### 1.2 Off-Heap 内存

JVM 进程还会使用堆外内存：

- **Direct Buffers**：网络传输层（Netty）使用的 ByteBuffer
- **MappedByteBuffer**：Lucene 通过 `mmap` 映射的段文件
- **JVM 元数据**：Class Metadata、Thread Stack 等

这部分通常不需要手动调优，但在排查内存问题时需要考虑。

### 1.3 OS Page Cache

操作系统会将磁盘文件缓存到空闲内存中。Lucene 的段文件（`.fdt`、`.tim`、`.tip`、`.dvd` 等）大量依赖 Page Cache 来实现接近内存的读取速度。这也是为什么推荐 Heap 不超过 50% 的根本原因——要把足够的内存留给操作系统。

> **经验法则**：一台 64GB 内存的节点，分配 30GB 给 Heap，剩余 34GB 中大部分会被操作系统用作 Page Cache。如果节点上的索引数据总量小于 34GB，几乎所有段文件都能常驻内存。

---

## 2. Heap 内部分配

JVM Heap 内部被 ES 的各个子系统瓜分，主要包括：

| 区域 | 默认比例/大小 | 用途 |
|------|-------------|------|
| Node Query Cache | Heap 的 10% | 缓存 filter 子句结果（bitset） |
| Request Cache | Heap 的 1% | 缓存整个分片级搜索结果 |
| Fielddata Cache | 无限制（危险！） | 缓存 text 字段的 fielddata |
| Indexing Buffer | Heap 的 10% | 暂存待写入的文档 |
| Segment Memory | 动态 | 倒排索引元数据（Terms Index 等） |
| 其他 | 动态 | 聚合中间结果、脚本执行、集群状态等 |

### 2.1 Node Query Cache（10%）

```
indices.queries.cache.size: 10%   # 默认值
```

- 缓存粒度：segment 级别的 filter bitset
- 适用条件：segment 内文档数 > 10000 且占总文档数 > 3%
- 淘汰策略：LRU

### 2.2 Request Cache（1%）

```
indices.requests.cache.size: 1%   # 默认值
```

- 缓存粒度：整个分片级请求的响应
- 适用条件：`size=0` 的请求（纯聚合/count）
- 失效时机：分片 refresh 后自动失效

### 2.3 Fielddata Cache（无限制）

```
indices.fielddata.cache.size: unbounded   # 默认无限制
```

这是最容易引发内存问题的区域。当对 `text` 字段执行聚合或排序时，ES 需要将整个字段的所有 term 加载到内存中构建正排结构。

### 2.4 Indexing Buffer（10%）

```
indices.memory.index_buffer_size: 10%   # 默认值
```

写入时文档先进入 Indexing Buffer，积累到一定量后 flush 到 segment。写入密集型集群可适当调大。

---

## 3. 三大缓存详解

### 3.1 Node Query Cache

**缓存什么**：`bool` 查询中的 `filter` 子句、`term` 查询、`range` 查询等不计算相关性评分的子查询结果。缓存内容是一个 bitset，标记哪些文档匹配。

**何时命中**：
- 相同的 filter 子查询在相同的 segment 上再次执行
- segment 未发生 merge（merge 后旧 segment 消失，缓存失效）

**何时失效**：
- segment 被 merge 后缓存自动清除
- LRU 淘汰（内存不足时）
- 手动清除

**监控方式**：

```bash
# 查看各节点的 Query Cache 统计
curl -u $ES_USER:$ES_PASS "$ES_URL/_nodes/stats/indices/query_cache?pretty"
```

关注指标：`hit_count`、`miss_count`、`evictions`（驱逐次数高说明缓存不够用）。

### 3.2 Request Cache

**缓存什么**：分片级别的完整搜索响应。仅缓存 `size=0`（不返回文档内容）的请求——即纯聚合或 `_count` 类请求。

**何时命中**：
- 完全相同的请求体发送到相同的分片
- 该分片自上次缓存后未发生 refresh

**何时失效**：
- 分片执行 refresh 后，对应的所有 Request Cache 条目自动失效
- 写入频繁的索引几乎无法利用此缓存

**最佳场景**：只读或极少更新的历史索引。

```bash
# 手动启用 Request Cache（默认对 size=0 的请求已启用）
curl -u $ES_USER:$ES_PASS -X POST "$ES_URL/my_index/_search?request_cache=true" \
  -H 'Content-Type: application/json' -d'{
  "size": 0,
  "aggs": { "status_count": { "terms": { "field": "status" } } }
}'
```

### 3.3 Fielddata Cache

**缓存什么**：`text` 字段的正排数据结构。当你对 `text` 字段执行排序、聚合或在脚本中访问时触发加载。

**为什么危险**：
- `text` 字段经过分词，一个字段值可能产生大量 term
- 一次加载就是整个 segment 的全量数据
- 默认无上限，可能吃掉大量 Heap

**正确做法**：使用 `doc_values`（见第 5 节）代替 fielddata。

```bash
# 查看各字段的 fielddata 占用
curl -u $ES_USER:$ES_PASS "$ES_URL/_cat/fielddata?v&s=size:desc"
```

---

## 4. 文件系统缓存的重要性

Lucene 的高性能很大程度上依赖 OS Page Cache。以下是关键理解：

### 4.1 为什么如此重要

- Lucene 段文件一旦写入就不可变，天然适合缓存
- 搜索时的倒排索引查找、doc_values 读取都是磁盘 I/O 操作
- 如果段文件在 Page Cache 中，读取延迟从毫秒级降至微秒级

### 4.2 优化建议

1. **Heap 不要过大**：留足空间给 Page Cache
2. **SSD 是必须的**：即使 Page Cache 未命中，SSD 的随机读也远优于 HDD
3. **避免 swap**：`bootstrap.memory_lock: true` 锁定内存，或关闭 swap
4. **force merge 只读索引**：减少 segment 数量，提高 Page Cache 利用率

```bash
# 锁定内存确认
curl -u $ES_USER:$ES_PASS "$ES_URL/_nodes?filter_path=**.mlockall&pretty"
```

### 4.3 监控 Page Cache

Linux 下可以通过以下命令观察 Page Cache 使用情况：

```bash
# 查看文件系统缓存（Linux）
free -h
# Buff/Cache 列即为 Page Cache + Buffer Cache
```

---

## 5. doc_values vs fielddata

这两者都是正排数据结构，但实现方式完全不同：

| 维度 | doc_values | fielddata |
|------|-----------|-----------|
| 存储位置 | 磁盘（利用 Page Cache） | JVM Heap |
| 适用字段类型 | `keyword`、`numeric`、`date`、`ip` 等 | `text` |
| 构建时机 | 索引时构建 | 首次聚合/排序时动态构建 |
| 内存压力 | 低（OS 管理） | 高（占用 Heap） |
| 默认启用 | 是 | 否（需手动开启） |

**最佳实践**：

- 需要对字符串聚合/排序时，使用 `keyword` 类型（自带 doc_values）
- 如果必须对 `text` 字段聚合，添加 `.keyword` 子字段
- 极少数场景（如需要对分词后的 term 聚合）才开启 fielddata

```json
{
  "mappings": {
    "properties": {
      "title": {
        "type": "text",
        "fields": {
          "keyword": { "type": "keyword" }
        }
      }
    }
  }
}
```

---

## 6. 断路器体系

断路器（Circuit Breaker）是 ES 防止 OOM 的最后一道防线。当某个操作预估将使用超过限制的内存时，ES 会拒绝该操作并抛出 `CircuitBreakingException`。

### 6.1 断路器层级

| 断路器 | 配置项 | 默认值 | 说明 |
|--------|-------|--------|------|
| 总断路器 | `indices.breaker.total.limit` | 70% of Heap | 所有断路器的总上限 |
| Request | `indices.breaker.request.limit` | 60% of Heap | 单个请求的内存限制 |
| Fielddata | `indices.breaker.fielddata.limit` | 40% of Heap | fielddata 加载上限 |
| In-flight | `network.breaker.inflight_requests.limit` | 100% of Heap | 传输层正在处理的请求 |

### 6.2 断路器类型

- **`memory`**（默认）：基于实际内存使用量追踪，更精确
- **`noop`**：不做任何限制（不推荐用于生产环境）

```bash
# 查看各断路器当前状态
curl -u $ES_USER:$ES_PASS "$ES_URL/_nodes/stats/breaker?pretty"
```

返回值中关注：
- `limit_size_in_bytes`：上限
- `estimated_size_in_bytes`：当前预估使用量
- `tripped`：已触发次数

### 6.3 动态调整断路器

```bash
# 调大 request 断路器（临时，集群级别）
curl -u $ES_USER:$ES_PASS -X PUT "$ES_URL/_cluster/settings" \
  -H 'Content-Type: application/json' -d'{
  "transient": {
    "indices.breaker.request.limit": "70%"
  }
}'
```

---

## 7. CircuitBreakingException 排查

当你遇到 `CircuitBreakingException` 时，按以下步骤排查：

### 7.1 确认是哪个断路器触发

```bash
# 查看所有断路器状态
curl -u $ES_USER:$ES_PASS "$ES_URL/_nodes/stats/breaker?pretty"
```

- `parent` tripped → 总内存超限
- `request` tripped → 单个请求太大（深度聚合、大 `size` 翻页）
- `fielddata` tripped → text 字段 fielddata 加载过多

### 7.2 常见原因与解决方案

| 原因 | 解决方案 |
|------|---------|
| 对 text 字段做聚合导致 fielddata 暴涨 | 改用 keyword 子字段 + doc_values |
| 深度分页（`from` + `size` > 10000） | 改用 `search_after` 或 `scroll` |
| 聚合桶数过多（高基数字段） | 限制 `size` 参数，使用 `composite` 聚合分页 |
| 并发大查询过多 | 降低并发，使用协调节点分离读写 |
| Heap 分配过小 | 在 31GB 限制内适当调大 Heap |

### 7.3 预防措施

```bash
# 限制聚合桶数量（集群级别）
curl -u $ES_USER:$ES_PASS -X PUT "$ES_URL/_cluster/settings" \
  -H 'Content-Type: application/json' -d'{
  "persistent": {
    "search.max_buckets": 10000
  }
}'
```

---

## 8. 实战（curl）

### 8.1 全面查看节点内存状态

```bash
# JVM 内存 + 断路器 + 索引级缓存
curl -u $ES_USER:$ES_PASS "$ES_URL/_nodes/stats/jvm,breaker,indices?pretty"
```

### 8.2 查看各缓存命中率

```bash
# Query Cache 统计
curl -u $ES_USER:$ES_PASS "$ES_URL/_nodes/stats/indices/query_cache?pretty"

# Request Cache 统计
curl -u $ES_USER:$ES_PASS "$ES_URL/_nodes/stats/indices/request_cache?pretty"

# Fielddata 统计
curl -u $ES_USER:$ES_PASS "$ES_URL/_nodes/stats/indices/fielddata?pretty"
```

### 8.3 查看 fielddata 按字段分布

```bash
curl -u $ES_USER:$ES_PASS "$ES_URL/_cat/fielddata?v&s=size:desc"
```

### 8.4 清除缓存

```bash
# 清除所有缓存（谨慎使用）
curl -u $ES_USER:$ES_PASS -X POST "$ES_URL/_cache/clear?pretty"

# 只清除 Query Cache
curl -u $ES_USER:$ES_PASS -X POST "$ES_URL/_cache/clear?query=true&pretty"

# 只清除 Request Cache
curl -u $ES_USER:$ES_PASS -X POST "$ES_URL/_cache/clear?request=true&pretty"

# 只清除 Fielddata Cache
curl -u $ES_USER:$ES_PASS -X POST "$ES_URL/_cache/clear?fielddata=true&pretty"

# 清除指定索引的缓存
curl -u $ES_USER:$ES_PASS -X POST "$ES_URL/my_index/_cache/clear?pretty"
```

### 8.5 断路器状态检查

```bash
curl -u $ES_USER:$ES_PASS "$ES_URL/_nodes/stats/breaker?pretty"
```

---

## 9. 实战（Java SDK）

### 9.1 查看节点内存统计

```java
NodesStatsResponse response = client.nodes().stats(s -> s
    .metric("jvm", "breaker", "indices")
);

for (var entry : response.nodes().entrySet()) {
    String nodeName = entry.getValue().name();
    var jvm = entry.getValue().jvm();

    System.out.printf("节点: %s%n", nodeName);
    System.out.printf("  Heap 已用: %s / %s (%.1f%%)%n",
        jvm.mem().heapUsed(),
        jvm.mem().heapMax(),
        jvm.mem().heapUsedPercent());

    var queryCache = entry.getValue().indices().queryCache();
    System.out.printf("  Query Cache 命中/未命中: %d / %d%n",
        queryCache.hitCount(), queryCache.missCount());

    var requestCache = entry.getValue().indices().requestCache();
    System.out.printf("  Request Cache 命中/未命中: %d / %d%n",
        requestCache.hitCount(), requestCache.missCount());
}
```

### 9.2 查看断路器状态

```java
NodesStatsResponse response = client.nodes().stats(s -> s
    .metric("breaker")
);

for (var entry : response.nodes().entrySet()) {
    var breakers = entry.getValue().breakers();
    for (var breaker : breakers.entrySet()) {
        System.out.printf("断路器 [%s]: 当前 %s / 上限 %s, 触发次数: %d%n",
            breaker.getKey(),
            breaker.getValue().estimatedSize(),
            breaker.getValue().limitSize(),
            breaker.getValue().tripped());
    }
}
```

### 9.3 清除缓存

```java
ClearCacheResponse response = client.indices().clearCache(c -> c
    .index("my_index")
    .query(true)
    .request(true)
    .fielddata(true)
);

System.out.printf("缓存清除完成，成功分片数: %d%n",
    response.shards().successful());
```

---

## 总结

| 概念 | 要点 |
|------|------|
| Heap 规划 | ≤ 50% 物理内存，≤ 31GB，Xms = Xmx |
| Node Query Cache | 缓存 filter bitset，segment merge 后失效，默认 10% |
| Request Cache | 缓存 size=0 的分片级响应，refresh 后失效，默认 1% |
| Fielddata Cache | 仅用于 text 字段聚合，极其消耗内存，应避免使用 |
| doc_values | 磁盘存储 + Page Cache，keyword/numeric 默认启用 |
| 文件系统缓存 | Lucene 性能的关键，需要留足物理内存 |
| 断路器 | 防 OOM 最后防线，total/request/fielddata/inflight 四层 |
| CircuitBreakingException | 先确认哪个断路器触发，再针对性优化 |

---

## 练习题

1. 一台 128GB 内存的节点，你会分配多少 Heap？为什么不能设置为 64GB？
2. 为什么 Request Cache 在写入频繁的索引上几乎无效？如何在日志索引的历史分区上充分利用它？
3. 某节点频繁触发 `fielddata` 断路器，`_cat/fielddata` 显示 `message` 字段（text 类型）占用最大。请描述你的修复方案。
4. 执行 `_nodes/stats/breaker` 发现 `request` 断路器 `tripped` 值持续增长，但 `fielddata` 正常。最可能的原因是什么？
5. 解释为什么 `keyword` 字段的聚合不会触发 fielddata 断路器，而 `text` 字段的聚合可能会。
