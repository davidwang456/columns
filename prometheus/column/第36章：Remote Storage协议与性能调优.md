# 第36章：Remote Storage协议与性能调优

> 远程存储是Prometheus生产落地绕不开的一环——本地TSDB只能存几周到几个月的数据，而长期趋势分析、跨集群聚合、冷数据审计都需要远端存储。本章聚焦Remote Write/Read的内部机制与实战调优。

---

## 1. 项目背景

某视频流媒体公司已将Prometheus的Remote Write对接到了ClickHouse做长期存储。他们的方案是通过clickhouse-prometheus适配器，将Prometheus推送的protobuf-write请求转换为ClickHouse的批量INSERT。高峰期每秒写入量高达50万samples，跑了三个月一切正常。

然而某次大版本上线后，运维同学在Grafana上突然发现——最近5分钟的监控数据完全是空的。排查链路走下来：Grafana → Prometheus → Remote Read → ClickHouse，数据是有的，但时间戳全都滞后了5分钟多。顺着链路深挖，问题卡在了Remote Write队列：ClickHouse那端的写入吞吐从原本的50万/秒掉到了约10万/秒（索引合并耗时激增），Prometheus这边Queue Manager的队列疯狂积压，samples在队列里排队等待发送。运维团队临时把`max_shards`从10调到了30，但内存用量立刻翻倍，险些触发OOM。

这个案例折射出一个核心痛点——Remote Write的队列机制本质是一个"生产-消费"模型。Prometheus从WAL中实时读取新sample，推入内存队列（生产者），多个shard goroutine从队列取数据组batch发送到远端存储（消费者）。在这个模型里，**capacity（队列容量）、max_shards（并发数）、max_samples_per_send（批次大小）**三者存在精妙的平衡关系，任何一个参数失配都可能导致延迟飙升、数据丢弃甚至OOM。

除此之外，实践者还面临一系列深度问题：snappy压缩在Remote Write场景下的压缩比到底如何？和zstd相比适用场景有何不同？WAL Watcher这个常被忽略的组件是如何保证数据不丢失的？Remote Read的streaming模式为何是防止OOM的关键设计？不同远端存储（VictoriaMetrics、ClickHouse、Thanos Receive）作为接收方的性能差异有多大？

本章将围绕这些问题，从源码机制到生产调优，系统性地展开。

---

## 2. 剧本式交锋对话

**小胖**（运维工程师，刚处理完队列积压问题）：大师，这次把我折腾惨了。我就把`max_shards`从10改到30，Prometheus内存直接飙到8G，还差点OOM。shard不就是个goroutine吗，怎么会吃这么多内存？

**大师**（放下手里的咖啡杯）：你这个理解有偏差。每个shard确实是个goroutine，但问题不在goroutine本身——每个shard对应一个独立的**发送队列**。`capacity`定义的是每个shard队列的最大容量。你把`max_shards`从10调到30，如果`capacity`设的是100000，那就多了20个队列，每个都可能塞满数据。按照每个sample约50字节估算，光队列就多占用了：20 × 100000 × 50 bytes ≈ 100MB。这只是直接的队列开销，还没算protobuf序列化的临时内存。

**小白**（刚入职的SRE，在一旁认真做笔记）：等等大师，我一直没搞明白capacity和max_samples_per_send的关系。文档说capacity=10000是"最多缓存10000个samples"，可我看到日志里一批就发出去5000个，那不就剩5000了？

**大师**：你抓住了关键点。capacity听起来像总容量，但实际上它决定的是**每个shard队列的缓冲深度**。max_samples_per_send是一次取多少组成一个batch。所以每个shard最多能积压的批次数量 = capacity ÷ max_samples_per_send。

假设capacity=10000，max_samples_per_send=5000，那每个shard最多积2批。注意这里的capacity是**近似值**——Queue Manager用的是一个环形缓冲区模型，实际的容量可能略有差异。

**小胖**：我还有个困惑。这次故障时我看到了min_backoff和max_backoff两个参数，跟队列积压有什么关系？

**大师**：问得好。这是退避策略的核心。当某个shard向远端发送失败时，它不会马上疯狂重试，而是做一个**指数退避**——第一次失败等min_backoff（比如30ms），第二次等60ms，第三次等120ms，以此类推，但最多不会超过max_backoff（比如5s）。你想想，如果远端存储已经撑不住了，你还用10个shard同步疯狂重试，等于给它雪上加霜。

但反过来，如果max_backoff设得太大（比如从默认5s改成60s），一旦远端恢复，每个shard要等很久才重试，队列积压根本消化不动。

**小白**：那WAL Watcher又是什么角色？我翻代码老看到它。

**大师**：这是Prometheus Remote Write高可靠性的根基。你想，Prometheus本地存储有Head Block在内存里管理最新数据。但Remote Write不能直接从Head Block读——因为Head Block是通过倒排索引管理的，数据分布在各种哈希表中，读效率低且与压缩、落盘逻辑耦合。

WAL Watcher的职责是：**独立监听WAL文件的追加写入**——每当有新的sample append到WAL，Watcher立即读到并推入Remote Write队列。这意味着它走的是WAL路径而非Head Block的内存路径，同时也保证了：即使Prometheus重启，WAL中未发送的数据也能被Watcher重新读取并补发。这就是Remote Write"at-least-once"语义的保证——可能重复发，但绝不会丢。

**小胖**：那压缩呢？我们现在Remote Write用snappy，跨机房传输带宽还是有点紧张。换zstd会不会更好？

**大师**：这是一个经典的压缩速度与压缩比的权衡。snappy的设计哲学是"够快就好"——压缩比约3:1，CPU开销不到5%，特别适合内网低延迟场景。你在内网带宽充足的场景下用snappy是正确的。

zstd在level 3时能达到约5:1的压缩比，但CPU开销约10%。如果你是跨DC、带宽宝贵，那zstd更合适。不过目前Prometheus 2.45+的Remote Write默认只支持snappy，zstd的支持在某些fork版本和VictoriaMetrics中已有实现。顺便提一句，VictoriaMetrics团队做过实测：50万sample/s的写入场景下，zstd比snappy多消耗约8%的CPU，但网络传输量减少约40%，这个tradeoff值得你在跨机房场景下考虑。

**小白**：最后一个问题——Remote Read为什么要用streaming？不streaming会怎样？

**大师**：来，想象一个场景：你查30天的`node_cpu_seconds_total`，这个指标假设每15秒采集一次，一台机器每天就有5760个sample，30天就是17万条，100台机器就是1700万条。Protobuf序列化后约3GB。如果不streaming，Prometheus需要把这3GB全部加载到内存再解析——OOM几乎是必然的。

Remote Read的HTTP streaming模式下（基于chunked transfer encoding），客户端发出请求后，服务器分批发回数据，客户端逐chunk解析。内存占用始终维持在chunk级别的buffer，也就几MB。这就是为什么streaming对Remote Read而言不是锦上添花，而是必需设计。

---

## 3. 项目实战

### 步骤1：深入Remote Write Queue Manager源码

Queue Manager是Remote Write的心脏，源码位于`storage/remote/queue_manager.go`。让我们拆解其核心结构：

```go
type QueueManager struct {
    queues     []*queue            // shard队列数组
    shards     *shards             // shard管理器
    samplesIn  *ewmaRate           // 入队速率（EWMA指数加权移动平均）
    samplesOut *ewmaRate           // 出队速率
    samplesDropped *ewmaRate       // 丢弃速率
}

// 每个shard的核心循环——一个goroutine一条命
func (s *shards) runShard(i int) {
    for {
        batch := s.queues[i].batch()           // 从队列取一批sample
        s.sendBatchWithBackoff(batch)           // HTTP发送，失败退避重试
    }
}
```

每个shard是一个独立的goroutine，它的工作循环极其简洁：**取数据→组batch→HTTP发送→失败则退避重试→循环**。这套模型中，关键配置如何影响内存？

- `capacity = 10000`：每个shard队列最多缓存10000个samples
- `max_samples_per_send = 5000`：每批最多5000个samples
- `max_shards = 30`：最多30个并发发送goroutine
- 直接内存占用 ≈ `max_shards × capacity × 50bytes` = 30 × 10000 × 50 ≈ 15MB

看起来15MB不大，但真正的内存杀手不是这个——是**WAL积压**。当远端写入变慢、队列被塞满后，WAL中的新sample无法被Watcher及时消费，WAL文件不断增长。WAL中的数据是完整sample副本，这才是可能撑爆内存的元凶。

### 步骤2：Queue参数调优实操

根据写入速率调优队列参数，有一条经验公式：

```
avg_samples_per_second = 每秒写入的samples数量
remote_write_latency    = 远端存储的平均写入延迟（秒）

建议配置：
max_shards >= avg_samples_per_second × remote_write_latency / max_samples_per_send
capacity   = max_shards × max_samples_per_send × 2   （2倍缓冲，应对突发）
```

**实战调优示例**：

场景1——QPS=5万sample/s，远端延迟=50ms：
- `max_shards ≥ 50000 × 0.05 / 5000 = 0.5` → 理论1个shard就够
- 实际建议设5个shard（冗余 + 应对流量突发）
- `capacity = 5 × 5000 × 2 = 50000`

场景2——QPS=50万sample/s，远端延迟=100ms：
- `max_shards ≥ 500000 × 0.1 / 5000 = 10` → 至少10个shard
- `capacity = 10 × 5000 × 2 = 100000`
- 这是高吞吐场景，还需注意`max_backoff`不宜过大，否则故障恢复后消化太慢

**监控队列健康的核心PromQL**：

```promql
# 1. 队列积压量：入队速率 - 出队速率，持续为正说明有积压
rate(prometheus_remote_storage_samples_total[5m])
- rate(prometheus_remote_storage_sent_samples_total[5m])

# 2. 是否有sample被丢弃（>0 立刻告警）
rate(prometheus_remote_storage_samples_dropped_total[5m])

# 3. 队列最高时间戳延迟（秒）——数值越大说明积压越严重
time() - prometheus_remote_storage_queue_highest_sent_timestamp_seconds

# 4. 远端写入失败率
rate(prometheus_remote_storage_failed_samples_total[5m])
/
rate(prometheus_remote_storage_samples_total[5m])

# 5. 当前活跃shard数
prometheus_remote_storage_shards

# 6. 每个shard的队列占用
prometheus_remote_storage_queue_highest_sent_timestamp_seconds
- prometheus_remote_storage_queue_lowest_sent_timestamp_seconds
```

第3条是告警的金指标——当 `time() - highest_timestamp > 60` 时说明有超过1分钟的数据还没发出去，需要立即排查。

### 步骤3：压缩协议对比实验

Prometheus Remote Write协议基于Protobuf，HTTP Body使用snappy压缩（默认）。我们可以设计一个对比实验来评估压缩效果：

**实验设计**：用相同的数据源（10万条housekeeping指标，包含大量重复label），分别测算snappy和zstd（level 3）在Remote Write场景下的表现。

| 维度 | Snappy（默认） | Zstd（level 3） |
|------|---------------|-----------------|
| 压缩比 | ~3:1 | ~5:1 |
| CPU开销 | < 5% | ~10% |
| 500KB压缩耗时 | ~0.3ms | ~1.2ms |
| 适合场景 | 内网，带宽充足，延迟敏感 | 跨DC/云，带宽珍贵 |
| Prometheus支持 | 内置默认 | 部分版本/ZSTD fork |

在VictoriaMetrics中（VM默认同时支持snappy和zstd作为接收端），如果你在Prometheus端配置了zstd编码，VM侧会自动识别Content-Encoding并解压。目前Prometheus官方主线默认只启用了snappy，zstd的支持可通过编译时build tag开启或使用VictoriaMetrics团队维护的fork版本。

**结论**：内网Gbps带宽场景，snappy足够；跨机房/按流量计费的公有云环境，zstd能节省约40%的带宽成本。

### 步骤4：Remote Read的Streaming模式

代码位于`storage/remote/read.go`：

```go
func (c *Client) Read(ctx context.Context,
    query *storage.SelectHints, ms ...*labels.Matcher) (storage.SeriesSet, error) {

    // 构造ReadRequest（Protobuf）
    req := &LabeledQueries{...}

    // 发送HTTP POST → 接收streaming response
    resp, err := c.httpClient.Do(req)

    // 逐chunk解析（不是一次性全部读入内存！）
    stream := newStreamReader(resp.Body)
    return stream, nil
}
```

这里的核心设计是`newStreamReader`——它不是`ioutil.ReadAll()`那样一次性吞下整个response body，而是基于`bufio.Reader`逐chunk读取。服务器端返回`Transfer-Encoding: chunked`，每个chunk是一组protobuf-encoded的timeseries数据。客户端一边读一边解析，上层调用者通过`SeriesSet`接口逐条消费，完全不需要等待所有数据返回完毕。

**生产案例**：某监控平台查询"过去30天所有节点的内存使用率"，7天窗口的`node_memory_MemAvailable_bytes`（5000台机器，15s采集间隔）。数据量约为：5000 × (30 × 24 × 3600 / 15) × 50 bytes ≈ 43GB。如果没有streaming，一次性加载到内存意味着Prometheus进程直接OOM。streaming模式下，每读完一个chunk（约256KB）就释放，峰值内存占用可以控制在100MB以内。

### 步骤5：Remote Write多副本配置

生产环境常见的需求是：一份数据同时写VictoriaMetrics（用于热查询面板）和ClickHouse（用于冷数据分析审计）。Remote Write天然支持多目标：

```yaml
remote_write:
  # 目标1：VictoriaMetrics —— 只存基础设施指标，热查询
  - url: 'http://victoriametrics:8428/api/v1/write'
    queue_config:
      capacity: 50000
      max_shards: 20
      max_samples_per_send: 5000
      min_backoff: 30ms
      max_backoff: 5s
    write_relabel_configs:
      - source_labels: [__name__]
        regex: 'node_.*|up|container_.*'
        action: keep

  # 目标2：ClickHouse适配器 —— 全量存储，冷数据审计
  - url: 'http://clickhouse-adapter:9201/write'
    queue_config:
      capacity: 100000
      max_shards: 30
      max_samples_per_send: 10000
      min_backoff: 50ms
      max_backoff: 10s
    write_relabel_configs:
      - source_labels: [__name__]
        regex: '.+'
        action: keep
```

**关键点**：两个Remote Write目标完全独立——各自的队列、各自的shard、各自的backoff策略。即使ClickHouse挂掉，VictoriaMetrics那条链路完全不受影响。这是Remote Write架构的重要优势。

**可能遇到的坑**：

1. **远端不可达时max_backoff过大**：每个shard按指数退避等待，如果max_backoff=60s，恢复后最坏要等60s才开始重试。积压消化需要的时间 = 积压量 ÷ (max_shards × max_samples_per_send × 每秒发送次数)。建议max_backoff不要超过10s。

2. **batch_send_deadline设置失当**：设太短（如1s），会频繁发送小批次，HTTP overhead占比高，效率低；设太长（如60s），数据在队列里待太久，延迟增大。建议与`max_samples_per_send`配合，使得在latency窗口内能填满一个batch。经验值5s-10s。

3. **capacity太小→高峰期丢数据**：如果写入峰值超过capacity×max_shards的缓冲能力，sample会被直接丢弃（`samplesDropped`递增）。实际中建议capacity至少为最大突发量的1.5-2倍。

4. **Remote Read返回空不fallback**：如果远端查询返回空结果，Prometheus**不会**自动回退到本地TSDB查询。这是设计选择——Remote Read的语义是"如果配置了远端，就认为远端是数据权威源"。需要组合查询需要自己在查询层（如Thanos Query）做union。

### 测试验证

验证方式一——观察队列延迟：

```promql
# 应该始终 < 30（秒），表示队列没有超过30秒的积压
time() - prometheus_remote_storage_highest_timestamp_in_seconds
```

验证方式二——断开网络模拟故障：
- 用iptables临时DROP所有去往远端存储的流量
- 观察Prometheus日志出现`remote write failed`和`retrying`
- 观察`samplesDropped`是否上报
- 恢复网络后，观察`highest_timestamp_in_seconds`逐步追平`time()`

验证方式三——压力测试：使用`avalanche`工具生成大量metrics，配合`remote_storage_samples_total`指标观察Queue Manager的吞吐极限，确认参数调优效果。

---

## 4. 项目总结

### 队列参数调优决策流程图

```
估算写入速率(sample/s) → 测量远端写入延迟(s)
    │
    ├→ max_shards ≥ rate × latency / max_samples_per_send
    │     └→ 取整后加20%-50%冗余
    │
    ├→ capacity = max_shards × max_samples_per_send × 2
    │     └→ 2倍缓冲应对突发
    │
    └→ 验证：min_backoff设为30ms-100ms, max_backoff ≤ 10s
          └→ 观察积压指标，持续为0则参数合理
```

### 必须监控的6个Remote Write指标

| 指标 | 含义 | 告警阈值建议 |
|------|------|-------------|
| `prometheus_remote_storage_highest_timestamp_in_seconds` | 队列中最新的sample时间戳 | `time() - 该值 > 60` 告警 |
| `prometheus_remote_storage_samples_dropped_total` | 丢弃量 | `rate > 0` 立刻告警 |
| `prometheus_remote_storage_failed_samples_total` | 发送失败量 | `rate > 0` 持续5分钟告警 |
| `prometheus_remote_storage_queue_highest_sent_timestamp_seconds` | 最高已发送时间戳 | 与入队时间戳对比判断积压 |
| `prometheus_remote_storage_samples_total` | 入队总量 | 对比发送量判断积压 |
| `prometheus_remote_storage_shards` | 当前活跃shard数 | 接近max_shards时关注 |

### Snappy vs Zstd 选择决策表

| 场景 | 推荐 | 理由 |
|------|------|------|
| 内网同机房，带宽 ≥ 1Gbps | Snappy | CPU开销低，延迟最优 |
| 跨公网/专线，带宽有限 | Zstd (level 3) | 压缩比高，省带宽成本 |
| 极高吞吐（>100万sample/s） | Snappy | CPU先于带宽成为瓶颈 |
| 时序数据含大量重复label | Zstd | 字典压缩优势明显 |
| Prometheus官方标准部署 | Snappy | 原生支持，兼容性最好 |

### 适用场景

- **大规模写入**（>10万sample/s）：单本地TSDB难以支撑，需Remote Write分流
- **跨DC远程写入**：结合zstd压缩降低带宽成本，配合合理的backoff策略应对网络抖动
- **多副本分发**：一份数据同时发给热查询存储和冷分析存储，write_relabel_configs精准过滤
- **长期冷存储**：Prometheus本地保留30天热数据，远端保留1年以上

### 注意事项

- Remote Write是**异步**的——写入成功后sample不会立刻出现在远端，队列缓冲带来秒级延迟是常态
- `max_shards`不是越多越好——每个shard有goroutine开销、HTTP连接开销、内存开销，远端存储也可能因连接数过多而性能下降
- `write_relabel_configs`是强大的过滤工具，但正则匹配耗CPU，大的metric基数下建议用精确的前缀匹配而非`.*`
- Remote Write的"at-least-once"语义意味着重启或网络闪断时可能产生重复数据，远端存储需具备幂等写入能力

### 常见踩坑经验

**坑1：capacity太小导致高峰期大量丢数据。** 某团队将capacity设为5000，平时QPS=3万正常，但每天整点有定时任务产生的5倍流量尖峰，队列瞬间填满，`samplesDropped`居高不下。解决：capacity调至50000，配合max_shards=10，尖峰被2倍缓冲吸收。

**坑2：Remote Write堵塞导致Prometheus OOM。** 远端ClickHouse索引合并时写入变慢，队列反压到WAL，WAL文件增长到60GB，耗尽磁盘和内存。解决：设置`max_backoff`+监控积压告警，在积压超过阈值时自动降低采集频率（通过Prometheus的`scrape_interval`动态调整）。

**坑3：shard太多导致远端连接数爆炸。** 30个Prometheus实例，每个max_shards=30，远端VictoriaMetrics同时承受900个TCP连接，`vm_concurrent_insert_capacity`被打满。解决：在VictoriaMetrics层配置`-maxConcurrentInserts`合理值，或引入一层中间代理合并连接。

### 思考题

1. **如果Remote Storage的写入能力固定为10万sample/s，Prometheus的写入峰值是20万sample/s，如何设计队列参数保证数据不丢失？**  
   提示：思考队列缓冲如何平滑尖峰，以及是否需要引入采样/降级策略。

2. **Remote Read + 本地TSDB的组合查询，Prometheus如何决定去哪个数据源查？**  
   提示：研究`read_recent`和`required_matchers`配置项的作用，以及查询路由器（fanout）的工作机制。
