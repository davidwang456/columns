# 第27章 线程池与背压：Rejections 限流保护

## 背景

Elasticsearch 节点内部并非"一个线程干所有事"。搜索、写入、管理、刷新、合并……不同类型的操作由不同的线程池承担，每个线程池都有独立的线程数和等待队列。当请求速率超过线程池的处理能力时，队列会逐渐填满。队列满后新请求将被**拒绝（Rejected）**，客户端收到 HTTP 429 `TOO_MANY_REQUESTS` 错误。

Rejection 并不意味着集群"坏了"——它是一种**背压（Back Pressure）**机制，防止节点被无限量的请求压垮。但频繁的 Rejection 说明系统已经过载，需要从查询优化、写入节流、架构拆分等多个维度进行治理。

## 本章目标

1. 理解 ES 线程池体系的分类与默认配置
2. 掌握 `fixed` 与 `scaling` 两种线程池类型的区别
3. 理解 Rejection 的触发机制与监控方式
4. 建立 Rejection 排查的标准流程
5. 掌握客户端背压策略（指数退避、限流、熔断）
6. 了解服务端保护手段（并发分片请求限制、协调节点分离）

---

## 1. ES 线程池体系

ES 为不同类型的操作配置了独立的线程池，实现资源隔离。以下是核心线程池一览：

### 1.1 主要线程池

| 线程池名称 | 类型 | 线程数（默认） | 队列大小（默认） | 用途 |
|-----------|------|---------------|-----------------|------|
| `search` | fixed | CPU * 3/2 + 1 | 1000 | 搜索请求（Query + Fetch） |
| `write` | fixed | CPU + 1 | 10000 | 索引、更新、删除、Bulk |
| `get` | fixed | CPU + 1 | 1000 | 实时 Get 请求 |
| `analyze` | fixed | 1 | 16 | 文本分析请求 |
| `management` | scaling | 1 ~ 5 | — | 集群管理操作 |
| `flush` | scaling | 1 ~ min(5, CPU/2) | — | Flush 操作 |
| `refresh` | scaling | 1 ~ min(10, CPU/2) | — | Refresh 操作 |
| `force_merge` | fixed | max(1, CPU/8) | — | Force Merge |
| `snapshot` | scaling | 1 ~ min(5, CPU/2) | — | 快照/恢复 |
| `warmer` | scaling | 1 ~ min(5, CPU/2) | — | 段预热 |
| `fetch_shard_started` | scaling | 1 ~ 2*CPU | — | 分片启动时的元数据获取 |
| `fetch_shard_store` | scaling | 1 ~ 2*CPU | — | 分片存储信息获取 |

> **注意**：以上线程数的 `CPU` 指的是 `os.availableProcessors`，可通过 `node.processors` 手动覆盖。

### 1.2 线程池配置示例

```yaml
# elasticsearch.yml（通常不建议修改默认值）
thread_pool:
  search:
    size: 25
    queue_size: 2000
  write:
    size: 12
    queue_size: 20000
```

> **最佳实践**：绝大多数场景下不需要修改线程池配置。调线程池是"最后的手段"，优先优化查询和写入本身。

---

## 2. 线程池类型：fixed vs scaling

### 2.1 fixed 类型

- 线程数固定，由 `size` 参数指定
- 有一个有界等待队列（`queue_size`）
- 当所有线程忙碌且队列满时，新任务被拒绝
- 适用于需要严格背压的场景：`search`、`write`、`get`

工作流程：

```
请求到达 → 有空闲线程？ → 是 → 立即执行
                          ↓ 否
                 队列有空位？ → 是 → 进入队列等待
                               ↓ 否
                         → Rejected（429）
```

### 2.2 scaling 类型

- 线程数可动态伸缩，范围由 `core`（最小）和 `max`（最大）指定
- 无有界队列（任务直接分配给线程）
- 空闲线程超过 `keep_alive` 时间后回收
- 适用于突发但不持续的管理类操作：`management`、`flush`、`refresh`

---

## 3. Rejection 机制

### 3.1 触发条件

Rejection 发生在 **fixed** 类型的线程池中：

```
Rejection = 所有线程忙碌 AND 等待队列已满
```

被拒绝的请求：
- REST 请求返回 HTTP 429 `TOO_MANY_REQUESTS`
- 传输层请求返回 `EsRejectedExecutionException`

### 3.2 Rejection 是累计值

`_nodes/stats` 中的 `rejected` 字段是**节点启动以来的累计值**，而非当前值。监控时必须关注**增量**：

```
当前 rejected 值 - 上次采集时的 rejected 值 = 时间窗口内的新增 Rejection 数
```

### 3.3 示例：search 线程池满载场景

假设一个 8 核节点：
- `search` 线程数 = 8 * 3/2 + 1 = 13
- `search` 队列大小 = 1000

当 13 个线程全忙、1000 个请求在排队时，第 1014 个搜索请求将被 Rejected。

---

## 4. 监控线程池

### 4.1 使用 _cat/thread_pool

```bash
# 查看所有线程池状态
curl -u $ES_USER:$ES_PASS "$ES_URL/_cat/thread_pool?v&h=node_name,name,active,queue,rejected,size,type&s=rejected:desc"
```

输出示例：

```
node_name  name   active queue rejected size type
node-1     search     10   200       42   13 fixed
node-1     write       8    50        3    9 fixed
node-2     search      5    10        0   13 fixed
```

- `active`：正在执行的线程数
- `queue`：等待队列中的任务数
- `rejected`：累计 Rejection 数

### 4.2 使用 _nodes/stats

```bash
# 详细的线程池统计
curl -u $ES_USER:$ES_PASS "$ES_URL/_nodes/stats/thread_pool?pretty"
```

### 4.3 只看特定线程池

```bash
# 只看 search 和 write
curl -u $ES_USER:$ES_PASS "$ES_URL/_cat/thread_pool/search,write?v&h=node_name,name,active,queue,rejected"
```

### 4.4 监控脚本思路

```bash
# 每10秒采集一次 rejected 增量
while true; do
  curl -s -u $ES_USER:$ES_PASS "$ES_URL/_cat/thread_pool/search,write?h=node_name,name,rejected"
  echo "---"
  sleep 10
done
```

---

## 5. Rejection 排查流程

当发现 Rejection 持续增长时，按以下步骤排查：

### 5.1 第一步：确认是哪个线程池

```bash
curl -u $ES_USER:$ES_PASS "$ES_URL/_cat/thread_pool?v&h=node_name,name,rejected&s=rejected:desc"
```

### 5.2 第二步：根据线程池类型对症下药

**search Rejection**：

| 检查项 | 操作 |
|--------|------|
| 慢查询 | 检查慢日志，优化查询（减少深度聚合、避免 wildcard 前缀查询） |
| 分片过多 | 减少分片数，合并小索引 |
| 并发过高 | 降低客户端并发，启用搜索节流 |
| 节点不足 | 水平扩展搜索节点 |

**write Rejection**：

| 检查项 | 操作 |
|--------|------|
| Bulk 请求过大 | 控制在 5-15MB / 请求 |
| 写入并发过高 | 客户端限流，减少并发 Bulk 线程 |
| merge 太慢 | 检查磁盘 I/O，考虑 SSD |
| refresh 过于频繁 | 批量导入时设置 `refresh_interval: -1` |

### 5.3 第三步：检查系统资源

```bash
# CPU 使用率
curl -u $ES_USER:$ES_PASS "$ES_URL/_nodes/stats/os?filter_path=**.cpu&pretty"

# 磁盘 I/O
curl -u $ES_USER:$ES_PASS "$ES_URL/_nodes/stats/fs?pretty"
```

---

## 6. 客户端背压策略

服务端的 Rejection 是"通知"，客户端需要正确响应这个信号。

### 6.1 指数退避重试

收到 429 后不要立即重试，而是等待递增的时间间隔：

```
第1次重试：等待 100ms
第2次重试：等待 200ms
第3次重试：等待 400ms
第4次重试：等待 800ms
...
最大等待：如 30s
最大重试次数：如 5 次
```

### 6.2 Bulk 请求的部分重试

Bulk API 返回的结果中，每个操作有独立的 `status`。收到 429 时只需重试失败的操作：

```json
{
  "items": [
    { "index": { "_id": "1", "status": 201 } },
    { "index": { "_id": "2", "status": 429 } },
    { "index": { "_id": "3", "status": 201 } }
  ]
}
```

只重试 `_id: 2`，而不是整个 Bulk 请求。

### 6.3 客户端限流

在应用层主动控制发送速率：

- **并发控制**：限制同时进行的 Bulk 请求数（如最多 3 个并发）
- **速率限制**：使用令牌桶或漏桶算法控制每秒请求数
- **自适应限流**：根据 Rejection 频率动态调整发送速率

### 6.4 熔断器模式

当连续 N 次收到 429 时，暂停发送一段时间（如 30 秒），让集群恢复后再继续。

---

## 7. 服务端保护手段

### 7.1 search.max_concurrent_shard_requests

限制单个搜索请求同时查询的分片数：

```bash
# 默认值为 5（每个节点最多同时查询 5 个分片）
curl -u $ES_USER:$ES_PASS -X PUT "$ES_URL/_cluster/settings" \
  -H 'Content-Type: application/json' -d'{
  "persistent": {
    "search.default_allow_partial_results": true
  }
}'
```

单个搜索请求发到协调节点后，不会一次性向所有分片发查询，而是分批执行：

```bash
# 在搜索请求中指定
curl -u $ES_USER:$ES_PASS "$ES_URL/my_index/_search?max_concurrent_shard_requests=3" \
  -H 'Content-Type: application/json' -d'{
  "query": { "match": { "title": "elasticsearch" } }
}'
```

### 7.2 协调节点分离

将协调节点（Coordinating-only Node）独立部署，使其不承担数据存储和写入职责：

```yaml
# elasticsearch.yml（协调节点配置）
node.roles: []
```

协调节点的好处：
- 聚合结果合并在协调节点完成，不影响数据节点
- 搜索线程池的 Rejection 集中在协调节点，数据节点更稳定
- 便于独立扩展搜索能力

### 7.3 写入速率限制

```bash
# 限制单节点的恢复/合并速率（间接保护写入）
curl -u $ES_USER:$ES_PASS -X PUT "$ES_URL/_cluster/settings" \
  -H 'Content-Type: application/json' -d'{
  "persistent": {
    "indices.recovery.max_bytes_per_sec": "100mb"
  }
}'
```

---

## 8. 实战（curl）

### 8.1 全面监控线程池状态

```bash
# 概览所有线程池（按 rejected 降序）
curl -u $ES_USER:$ES_PASS "$ES_URL/_cat/thread_pool?v&h=node_name,name,active,queue,rejected,size,type&s=rejected:desc"

# 只看搜索和写入
curl -u $ES_USER:$ES_PASS "$ES_URL/_cat/thread_pool/search,write?v&h=node_name,name,active,queue,rejected"
```

### 8.2 详细的线程池统计

```bash
curl -u $ES_USER:$ES_PASS "$ES_URL/_nodes/stats/thread_pool?pretty"
```

### 8.3 检查热点节点

```bash
# 找出 Rejection 最多的节点
curl -u $ES_USER:$ES_PASS "$ES_URL/_cat/thread_pool/search?v&h=node_name,active,queue,rejected&s=rejected:desc"
```

### 8.4 模拟写入 Rejection

```bash
# 高并发批量写入（注意：仅在测试环境使用）
for i in $(seq 1 100); do
  curl -s -u $ES_USER:$ES_PASS -X POST "$ES_URL/_bulk" \
    -H 'Content-Type: application/x-ndjson' --data-binary @bulk_data.ndjson &
done
wait

# 检查 write rejection
curl -u $ES_USER:$ES_PASS "$ES_URL/_cat/thread_pool/write?v&h=node_name,active,queue,rejected"
```

### 8.5 检查系统资源配合排查

```bash
# CPU 使用率
curl -u $ES_USER:$ES_PASS "$ES_URL/_nodes/stats/os?filter_path=**.cpu&pretty"

# JVM GC 情况（长时间 GC 会阻塞线程）
curl -u $ES_USER:$ES_PASS "$ES_URL/_nodes/stats/jvm?filter_path=**.gc&pretty"

# 当前活跃任务
curl -u $ES_USER:$ES_PASS "$ES_URL/_cat/tasks?v&s=running_time:desc"
```

---

## 9. 实战（Java SDK）

### 9.1 查看线程池状态

```java
NodesStatsResponse response = client.nodes().stats(s -> s
    .metric("thread_pool")
);

for (var entry : response.nodes().entrySet()) {
    String nodeName = entry.getValue().name();
    var threadPools = entry.getValue().threadPool();

    for (var pool : List.of("search", "write", "get")) {
        var stats = threadPools.get(pool);
        if (stats != null) {
            System.out.printf("节点 %s [%s]: 活跃=%d, 队列=%d, 拒绝=%d%n",
                nodeName, pool,
                stats.active(), stats.queue(), stats.rejected());
        }
    }
}
```

### 9.2 带指数退避的 Bulk 写入

```java
public void bulkWithBackoff(List<BulkOperation> operations) throws Exception {
    int maxRetries = 5;
    long waitMs = 100;
    List<BulkOperation> pending = new ArrayList<>(operations);

    for (int attempt = 0; attempt < maxRetries && !pending.isEmpty(); attempt++) {
        if (attempt > 0) {
            System.out.printf("第 %d 次重试，等待 %dms，剩余 %d 条%n",
                attempt, waitMs, pending.size());
            Thread.sleep(waitMs);
            waitMs *= 2;
        }

        BulkResponse response = client.bulk(b -> b.operations(pending));

        if (!response.errors()) {
            System.out.println("全部写入成功");
            return;
        }

        List<BulkOperation> retryOps = new ArrayList<>();
        for (int i = 0; i < response.items().size(); i++) {
            var item = response.items().get(i);
            if (item.status() == 429) {
                retryOps.add(pending.get(i));
            }
        }
        pending = retryOps;
    }

    if (!pending.isEmpty()) {
        throw new RuntimeException("经过 " + maxRetries + " 次重试仍有 "
            + pending.size() + " 条失败");
    }
}
```

### 9.3 监控 Rejection 增量

```java
public Map<String, Long> getSearchRejections() throws Exception {
    NodesStatsResponse response = client.nodes().stats(s -> s
        .metric("thread_pool")
    );

    Map<String, Long> rejections = new HashMap<>();
    for (var entry : response.nodes().entrySet()) {
        String nodeName = entry.getValue().name();
        var search = entry.getValue().threadPool().get("search");
        if (search != null) {
            rejections.put(nodeName, search.rejected());
        }
    }
    return rejections;
}
```

---

## 总结

| 概念 | 要点 |
|------|------|
| 线程池隔离 | 不同操作使用独立线程池，互不干扰 |
| fixed 类型 | 固定线程数 + 有界队列，满则 Reject |
| scaling 类型 | 动态伸缩线程数，用于非关键路径操作 |
| search 线程池 | CPU * 3/2 + 1，队列 1000 |
| write 线程池 | CPU + 1，队列 10000 |
| Rejection = 429 | 正常的背压信号，不是故障 |
| rejected 是累计值 | 监控时必须看增量，不是绝对值 |
| 排查顺序 | 确认线程池 → 对症优化 → 检查系统资源 |
| 客户端背压 | 指数退避 + 部分重试 + 限流 + 熔断 |
| 服务端保护 | 控制并发分片请求 + 协调节点分离 |

---

## 练习题

1. 一个 16 核节点的 `search` 线程池默认有多少个线程？队列能容纳多少个等待请求？
2. 你观察到 `write` 线程池的 `rejected` 从 100 增长到 500，这意味着什么？应该先检查什么？
3. 为什么 Bulk 请求收到 429 时不应该重试整个 Bulk，而只重试失败的操作？
4. 解释协调节点分离如何缓解数据节点的 `search` Rejection 问题。
5. 设计一个客户端指数退避策略：初始等待 200ms，最大等待 10s，最多重试 4 次。写出每次等待的时间序列。
