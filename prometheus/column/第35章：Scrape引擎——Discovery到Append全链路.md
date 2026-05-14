# 第35章：Scrape引擎——Discovery到Append全链路

## 一、项目背景

大促前夕，某电商公司运维团队按照预案，将所有微服务实例数翻倍扩容——从5000个Pod一口气扩到10000个。扩容完成后，Prometheus的Targets页面逐渐被一片红色淹没，"context deadline exceeded"的报错刷了屏。与此同时，Prometheus服务器的CPU使用率从日常的30%直冲到90%，内存也在缓慢爬升。

运维小胖的第一反应是："scrape_interval设的是15s，10000个target串行采集的话，每个target只有1.5ms，这不是开玩笑吗？" 但大师立刻纠正了他——Prometheus并不是单线程串行采集的。真正的架构是三层并发模型：多个scrape pool（按job_name划分），每个pool内多个scrape loop（按target划分），每个loop跑在独立的goroutine里。不同pool之间并行，同一pool内不同target之间也并行（受限速器约束）。

但问题不止于此。比"怎么采"更前置的问题是"采谁"——10000个Pod是动态变化的（扩缩容、滚动更新、故障迁移），Prometheus怎么知道该采哪些target？答案在Discovery Manager里。它管理着Kubernetes、Consul、File等多种SD provider，定期（或事件驱动）生成全量target快照，通过channel发送给Scrape Manager。Scrape Manager负责把target分配到对应的scrape pool和scrape loop，并管理这些loop的生老病死。

还有一个容易被忽略的机制：Staleness（陈旧标记）。当一个target挂掉后，它上次采集到的metrics不会立刻从查询结果中消失——Prometheus会在TSDB中写入特殊的StaleNaN标记，经过5分钟后旧数据才彻底"退休"。这个设计保证了查询结果不会因为短暂的采集失败而出现数据断层。

本章将带你深入Scrape引擎的三大子系统：**Discovery Manager（发现）→ Scrape Manager（调度）→ Scrape Loop（采集+追加）**，从全链路角度理解一条metric是如何从exporter流动到TSDB的。

---

## 二、剧本式交锋对话

**小胖**（盯着Prometheus页面上满屏的红色报错）：
"大师，我这10000个target，scrape_interval 15秒，单线程串行的话每个target才1.5毫秒，根本来不及啊！到底Scrape引擎内部是怎么调度这些target的？"

**大师**（放下咖啡杯）：
"你先别急，Prometheus可不是单线程模式。它的Scrape引擎是三段式的——Discovery Manager负责发现target有哪些，Scrape Manager负责把target分配给goroutine去采集，每个goroutine里跑一个Scrape Loop做实际的HTTP抓取和解析。三个层级，层层解耦。"

**小白**（翻开代码）：
"那第一步Discovery Manager具体怎么工作的？Kubernetes里Pod变了它怎么知道？"

**大师**：
"Discovery Manager内心其实是个'定时广播器'。它下面挂着多个SD provider——每个provider都跑着独立的goroutine去watch自己的数据源。比如Kubernetes的SD provider内部启动了pod/node/service三个informer，一旦watch到API变化，就以事件方式把变更的target group发送出来。但Discovery Manager不是把零散的变更直接传给下游的——它定时收集所有provider的全量target列表，合并后一次性广播给Scrape Manager。这样做的好处是减少下游的抖动，Scrape Manager拿到的永远是一个完整的'当前世界'快照。"

**小胖**（恍然大悟）：
"哦，所以不是'Pod A上线了就立刻通知Scrape Manager去采'，而是Discovery Manager等到下一个同步周期，把新上线的Pod A和已有的9999个Pod一起打包发给Scrape Manager？"

**大师**：
"对，正是这样。接下来Scrape Manager收到这个快照后，按job_name拆分——每个job_name对应一个scrape pool。比如你有 `api-server` 和 `cache-service` 两个job，就是两个pool，它们的采集完全独立、完全并发。再往下，scrape pool内部按target的labels做hash，把hash值映射到scrape loop。关键设计是：**同一个target永远落在同一个loop里**——这样上一次采集的状态（比如哪些series出现过）可以复用，Staleness机制也需要这个保证。"

**小白**：
"那Scrape Loop拿到target之后具体做什么？"

**大师**：
"核心就四步循环：第一，每隔scrape_interval发起HTTP GET到 `/metrics`，带上 `User-Agent: Prometheus/2.55.0` 和 `Accept: text/plain;version=0.0.4` 两个header，设置scrape_timeout超时；第二，拿到响应body后，用textparse逐行解析Prometheus文本格式，提取出metric name、labels、timestamp、value；第三，compare——上一次scrape出现过的metric这次没有了？那就标记为stale；第四，通过Head Appender把解析好的sample写入TSDB（包括Head内存块和WAL）。"

**小胖**（追问）：
"Staleness到底怎么实现的？target挂掉后，旧数据什么时候彻底消失？"

**大师**：
"这是Prometheus里很精妙的设计。每个scrape loop在内存里维护一个 `lastScrapeSeries` 集合，记录上一次scrape出现的所有series。这次scrape结束后，遍历这个集合——如果某个series这次没出现（scrape循环里没再遇到），就往TSDB追加一个StaleNaN值。查询引擎遇到StaleNaN时，会认为这个series在该时间点后'不再活跃'。注意，旧数据不会立刻消失——Prometheus默认允许在5分钟内查询到陈旧数据，超过5分钟后才彻底不可见。这个5分钟是硬编码的，没法配置。"

**小胖**：
"那回到我最初的问题——10000个target，goroutine调度开销会不会很大？"

**大师**：
"每个target一个scrape loop，一个loop一个goroutine，10000个target就是10000个goroutine。Go的goroutine虽然轻量（初始栈只有2KB），但一万个goroutine的调度、上下文切换依然有开销。更要命的是，每个loop独立计算下一次采集时间，而且加了随机jitter偏移——这是为了防止所有target刚好同时采集造成的'惊群效应'。但如果你的CPU已经90%了，说明goroutine调度和textparse的CPU消耗已经饱和，这时候该考虑分层Prometheus（federation）或者垂直扩容了。"

---

## 三、项目实战

### 环境准备
- Prometheus源码（推荐2.55.x版本），重点关注三个核心文件：`scrape/manager.go`、`scrape/scrape.go`、`discovery/manager.go`
- Go 1.22+编译环境，能通过 `go build ./cmd/prometheus/` 编译调试

### 步骤1：理解Discovery Manager

打开 `discovery/manager.go`，核心结构体如下：

```go
type Manager struct {
    providers []discovery.Provider  // SD providers列表
    syncCh    chan map[string][]*targetgroup.Group  // 同步通道
    targets   map[string][]*targetgroup.Group
}

func (m *Manager) Run() error {
    go m.sender()  // 定期发送sync信号
    for range m.syncCh {
        allTargets := m.allGroups()  // 收集所有provider的target list
        select {
        case m.updater <- allTargets:  // 通过updater channel发送给ScrapeManager
        }
    }
}
```

Discovery Manager的本质是一个"定时广播器"——每隔一段时间（取决于provider类型，K8s约30s），收集所有SD provider的全量target列表并合并，通过updater channel发送给Scrape Manager。它不是主动推送增量变更，而是定时全量同步。每个SD provider都有自己独立的goroutine：

```go
// 以K8s为例（discovery/kubernetes/kubernetes.go）
func (d *Discovery) Run(ctx context.Context, ch chan<- []*targetgroup.Group) {
    go d.runNodeInformer(ctx, ch)
    go d.runPodInformer(ctx, ch)
    go d.runServiceInformer(ctx, ch)
    // informer Event → 生成targetgroup → 发送给Manager
}
```

三个informer分别watch Node、Pod、Service的变化，任何K8s资源变更都会触发targetgroup的重新生成。

### 步骤2：理解Scrape Manager的调度

打开 `scrape/manager.go`：

```go
type Manager struct {
    scrapePools map[string]*scrapePool   // job_name → scrape pool
}

func (m *Manager) Run(tsets <-chan map[string][]*targetgroup.Group) {
    for ts := range tsets {
        m.reload(ts)
    }
}

func (m *Manager) reload(t map[string][]*targetgroup.Group) {
    for jobName, groups := range t {
        if pool, ok := m.scrapePools[jobName]; ok {
            pool.Sync(groups)
        } else {
            pool = newScrapePool(cfg, app)
            pool.Sync(groups)
            m.scrapePools[jobName] = pool
        }
    }
}

type scrapePool struct {
    loops map[uint64]*scrapeLoop  // hash → scrapeLoop
}

func (sp *scrapePool) Sync(groups []*targetgroup.Group) {
    for _, group := range groups {
        for _, t := range group.Targets {
            hash := t.hash()  // 对target的labels计算hash
            if loop, ok := sp.loops[hash]; ok {
                loop.Update(t)  // 更新已有loop
            } else {
                loop = newScrapeLoop(t)
                sp.loops[hash] = loop
                go loop.run()  // 启动新goroutine采集
            }
        }
    }
    for hash, loop := range sp.loops {
        if !activeTargets[hash] {
            loop.stop()
            delete(sp.loops, hash)
        }
    }
}
```

两个关键设计点：
1. **每个job_name一个scrapePool**——不同job的采集完全独立且并发，互不影响
2. **每个target一个scrapeLoop**——同一job内多个target也可以并发采集（共享限速器），hash保证同一target始终落在同一loop

### 步骤3：Scrape Loop核心采集流程

打开 `scrape/scrape.go`，核心循环：

```go
func (sl *scrapeLoop) run(interval, timeout time.Duration) {
    ticker := time.NewTicker(interval)
    for {
        select {
        case <-ticker.C:
            body, contentType, scrapeErr := sl.scrape()       // HTTP fetch
            samples, scrapeErr := sl.append(body, contentType) // Text parse
            sl.report(start, duration, scrapeErr)              // Health check
        }
    }
}

func (sl *scrapeLoop) append(b []byte, contentType string) (total, added int, err error) {
    var parser textparse.Parser
    for parser.Next() {
        metricName, labels, timestamp, value := parser.At()
        ref, err := sl.appender.Append(0, labels, timestamp, value)
    }
}
```

采集的关键细节：
- HTTP请求的User-Agent为 `Prometheus/2.55.0`，Accept header为 `text/plain;version=0.0.4`
- 每个scrape loop独立计算下一次采集时间，引入随机jitter偏移防止惊群效应
- 解析时遇到非法行（格式错误）会跳过并记录错误，不会中断整个scrape
- HTTP fetch默认不压缩（不设Accept-Encoding: gzip），大量指标时body会很大

### 步骤4：Staleness机制源码

```go
func (sl *scrapeLoop) addStaleMarkers(app storage.Appender) {
    for _, series := range sl.lastScrapeSeries {
        if !series.seenThisScrape {
            app.Append(series.ref, series.lset,
                sl.lastScrapeTime+1, chunkenc.StaleNaN)
        }
    }
}
```

当一个metric在上一次scrape中出现但这次没出现时，Prometheus会向TSDB追加一个StaleNaN值（特殊的NaN浮点数）。查询引擎遇到StaleNaN时，认为该series在该时间点后不再活跃，在5分钟内逐渐从查询结果中消失。时间线示例：

```
T0:  scrape → metric{a="1"} = 100
T1:  scrape → metric{a="1"} 消失 → TSDB写入 StaleNaN
T1+5m: 查询 → metric{a="1"} 不再返回（stale period过期）
```

注意：StaleNaN在WAL中也是正常写入的，WAL回放时会看到大量NaN值——这是预期行为，不是数据损坏。

### 步骤5：添加自定义scrape指标

在scrape loop中添加Histogram监控scrape body大小：

```go
var scrapeBodySize = prometheus.NewHistogramVec(
    prometheus.HistogramOpts{
        Name:    "prometheus_scrape_body_size_bytes",
        Help:    "Size of scrape response body",
        Buckets: prometheus.ExponentialBuckets(100, 10, 8),
    },
    []string{"job", "instance"},
)

func (sl *scrapeLoop) scrape() ([]byte, string, error) {
    resp, err := sl.client.Do(req)
    // ...读取body
    scrapeBodySize.WithLabelValues(
        sl.labels.Get("job"),
        sl.labels.Get("instance"),
    ).Observe(float64(len(body)))
    // ...
}
```

这样可以监控每个exporter的scrape body大小变化——body突然增大说明有新指标加入，可用于变更检测。

### 可能遇到的坑

1. **Jitter是随机的**：不同target的采集时间会自然错开，这是设计意图——防止所有target同时采集引发的"惊群效应"
2. **默认不压缩**：HTTP fetch时不带 `Accept-Encoding: gzip`，大量指标时body体积会很大，需留意网络带宽
3. **StaleNaN写入WAL**：WAL回放时会看到很多NaN值，这不是数据损坏，而是Staleness机制的正常表现
4. **Goroutine数量 = target数量**：10000个target = 10000个goroutine，Go的goroutine虽然轻量但调度仍有成本
5. **scrape_pool的loop map是普通map**：在Sync时是单goroutine操作，不存在并发写的问题

### 测试验证

- **观察Discovery**：启动后查看日志 `level=info msg="Starting provider" provider=kubernetes` 确认SD provider已启动
- **观察调度**：在Prometheus Web UI → Status → Targets页面，可以看到每个target的scrape loop状态（最后一次采集耗时、健康状态）
- **验证Staleness**：手动kill一个exporter，在PromQL中查询该exporter的 `up` 指标，观察从1→0的变化过程，再等5分钟后查询其他指标看是否消失
- **对比性能**：添加一个新exporter后，观察Discovery Manager自动发现 → Targets页面出现新条目 → 指标数据开始在Graph中展示的完整时间差（通常约30-60秒）

---

## 四、项目总结

### Scrape引擎全链路图

```
┌──────────────┐    全量快照     ┌──────────────┐    hash分片    ┌─────────────┐   HTTP GET   ┌──────────┐
│  Discovery   │ ──────────────→ │   Scrape     │ ────────────→ │  Scrape     │ ──────────→ │  /metrics│
│  Manager     │   (syncCh)     │   Manager    │  (syncGroups) │  Loop       │  (scrape)   │  endpoint│
│              │                │              │               │             │             │          │
│ ├─K8s SD     │                │ ├─scrapePool │               │ ├─HTTPfetch │             └──────────┘
│ ├─Consul SD  │                │ │  ├─loop1   │               │ ├─textParse │                  │
│ └─File SD    │                │ │  ├─loop2   │               │ ├─staleness │                  │
└──────────────┘                │ │  └─loopN   │               │ └─append ──→ ┌──────────┐      │
                                └──────────────┘               └─────────────┘ │   TSDB   │←─────┘
                                                                               │ ├─Head   │
                                                                               │ └─WAL    │
                                                                               └──────────┘
```

### 关键源码文件索引

| 文件 | 核心内容 | 关键结构/函数 |
|------|---------|-------------|
| `discovery/manager.go` | Discovery Manager主逻辑 | `Manager.Run()`, `Manager.allGroups()` |
| `scrape/manager.go` | Scrape Manager调度逻辑 | `Manager.reload()`, `scrapePool.Sync()` |
| `scrape/scrape.go` | Scrape Loop采集核心 | `scrapeLoop.run()`, `scrapeLoop.append()`, `addStaleMarkers()` |
| `discovery/kubernetes/kubernetes.go` | K8s SD provider | `Discovery.Run()`, informer回调 |
| `tsdb/head.go` | TSDB Head写入 | `HeadAppender.Append()` |

### 性能调优参数

| 参数 | 默认值 | 说明 | 调优建议 |
|------|--------|------|---------|
| `scrape_interval` | 15s | 采集间隔 | 不低于5s，否则CPU开销显著增加 |
| `scrape_timeout` | 10s | 单次采集超时 | 应小于scrape_interval，建议设为interval的80% |
| `body_size_limit` | 0（不限制） | 单次采集body上限 | 大量指标时建议设置（如10MB），防止OOM |
| `max_scrape_size` | 0（不限制） | scrape response最大字节数 | 配合body_size_limit使用 |
| `sample_limit` | 0（不限制） | 单次采集sample数上限 | 防止单个exporter产生过多指标 |

### 适用场景
- 理解Prometheus采集延迟的来源（SD同步周期 + scrape间隔 + 解析耗时）
- 调优大规模采集场景下的性能（goroutine数量、内存使用、CPU开销）
- 定位scrape timeout问题的根因（是网络超时、exporter响应慢还是解析瓶颈）
- 自定义指标写入逻辑（如过滤特定metric、添加额外label等）

### 注意事项
- 不要设置过短的scrape_interval（<5s会显著增加CPU开销，且数据量爆炸）
- body_size_limit默认0（不限制），在大量exporter有大量指标时应主动设置
- Staleness周期固定在5分钟（源码硬编码，不可配置），设计查询告警时需考虑这个延迟
- scrape timeout必须小于scrape interval，否则会出现"一次采集还没结束，下一次已经触发"的追尾现象

### 常见踩坑经验

1. **Goroutine数量爆炸**：某团队用Docker SD监控5000个容器，加上多个job，goroutine数量突破15000，Go scheduler压力导致P90采集延迟从50ms膨胀到2s。解决：合并job减少scrape pool数量，升级Prometheus到3.x使用更高效的采集模型。

2. **scrape_timeout设太短**：某业务将scrape_timeout设为3s，但新上线的exporter返回12000行metrics，textparse耗时约4s，导致Targets页面频繁红色。解决：要么增加timeout，要么优化exporter减少指标数量。

3. **StaleNaN污染查询**：在一次大规模故障中，几百个target同时挂掉，产生了大量StaleNaN。后续查询 `rate(http_requests_total[5m])` 时发现大量NaN值导致聚合异常。解决：查询时加 `or on() vector(0)` 将NaN替换为0，或使用 `max_over_time` 过滤NaN。

### 思考题

1. **Scrape Manager如何保证同一个target的指标不会因为"被两个scrape loop同时采集"而出现重复？**

2. **如果要实现"根据exporter的响应时间动态调整scrape_interval"——响应快的多采、响应慢的少采——源码需要改动哪些地方？**
