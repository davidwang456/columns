# 第26章：Remote Storage——远程读写协议

## 一、项目背景

某金融科技公司运维团队最近被合规部门找上了门——银保监会明确要求所有监控数据至少保留3年，以备审计追溯。团队的Prometheus集群已经稳定运行了一年，本地TSDB的retention时间最初只设了15天，现在二话不说直接改成3年（`--storage.tsdb.retention.time=1095d`）。结果呢？两个问题扑面而来：

第一是**磁盘成本**。SSD上3年的时序数据膨胀到了几十TB，采购成本直线上升，管理层看了报价单脸都绿了。第二是**查询性能**。Prometheus本地TSDB的查询引擎在面对3年的时间跨度时，需要扫描海量block文件的索引，一次大范围查询能把CPU打满好几分钟，Grafana大盘直接转圈圈。

运维团队深入研究后发现，这其实不是配置问题，而是设计哲学的问题。Prometheus的本地TSDB天生就是为"热数据"优化的——官方建议的保留周期是**2周到2个月**，它的压缩算法、索引结构、查询路径都假设数据量可控。把3年的数据硬塞进本地TSDB，就像让一辆F1赛车去拉集装箱——引擎是好引擎，但场景不对。

问题的另一面是**多集群数据汇聚**。公司有3个K8s集群分别在华东、华南和新加坡，每个集群各跑一套Prometheus。运维想做一个全局的可用性大盘，却发现数据散落在四个地方，跨Prometheus做聚合查询是根本不可能的。即便用Federation把数据聚合过来，也只是拉取聚合结果而非原始数据，灵活性大打折扣。

这些痛点最终指向了同一个解决方案——**Remote Storage（远程存储）**。Prometheus从2.x版本起就内置了一套远程读写协议，本质上是它的"存储抽象层"：通过Protobuf协议将采集到的时序数据异步推送到外部存储系统（VictoriaMetrics、Thanos Receive、InfluxDB、ClickHouse、Cortex等），查询时则可以从本地TSDB和远程存储共同拉取数据。本地保留热数据保证实时查询，远程保留全量历史数据满足合规和跨集群分析需求，这才是Prometheus在生产环境中的正确打开方式。

## 二、剧本式交锋对话

**小胖**（刚从合规部门的邮件里抬起头）：大师，我把Prometheus的retention调成3年，结果磁盘三天就吃掉了400GB，查询一个月的`node_cpu_usage` PromQL直接把Prometheus搞OOM重启了。这东西是不是不能这么用啊？

**大师**：你终于发现了一个关键事实——Prometheus的本地TSDB不是为长期存储设计的。它的block压缩算法假设的是几周的数据量，你让它存3年，index文件比数据还大。正确的做法是让Prometheus只保留15到30天的热数据，其余的全量数据通过**Remote Write**推到专用的远程存储里去。

**小胖**：Remote Write？听着像是Prometheus主动把数据"吐"出去？

**大师**：没错。它的完整链路是这样的：Prometheus每15秒从target抓取数据 → 先写入本地WAL和TSDB → 同时异步推入**Remote Write Queue** → Queue内部按**shard**并发分发 → 每个shard把数据打包成snappy压缩的Protobuf格式（WriteRequest，内含timeseries+labels+samples） → 通过HTTP POST推到远程存储的`/api/v1/write`端点。记住，Remote Write是异步的——数据先落地WAL再进队列，即使远程挂了也不会立刻丢数据。

**小白**（刚入职的新人运维）：那如果远程存储暂时挂了，队列满了怎么办？数据会在Prometheus内存里一直堆积吗？

**大师**：好问题，这正是Remote Write最容易踩的坑。队列的容量由`capacity`参数控制（默认10000个samples），一旦攒满，新的sample就会被**直接丢弃**！同时，并发发送的shard在失败后会进入**退避重试（backoff）**——先等`min_backoff`（默认30ms），失败一次翻倍，直到`max_backoff`（默认10s）。这期间队列在不断积压，如果远程存储长时间不可用，Prometheus会因为WAL膨胀和内存积压一起炸掉。所以生产环境一定要监控`prometheus_remote_storage_failed_samples_total`这个指标。

**小白**：那Remote Read呢？Prometheus收到查询请求时，怎么知道该问本地TSDB还是远程存储？

**大师**：取决于`read_recent`这个参数。设为`true`时，Prometheus会**完全绕过本地TSDB**，所有查询都走远程——适合远程存储比本地还快的情况。设为`false`时，会出现一个有趣的**分层查询**：查询时间范围超过本地retention的部分走Remote Read，没超过的部分走本地TSDB。Prometheus Querier会把两边返回的`prompb.ReadResponse`在内存里合并、排序，然后返回给客户端。关键点：**Prometheus不会对本地和远程的数据做去重**，它假设两边的时间范围是不重叠的。如果你本地存了15天的数据，远程也存了从今天开始的，那查询最近1小时的数据会出现两份结果，这是个大坑。

**小胖**：这不就是Federation吗？都是把数据聚合到一起啊。

**大师**：本质完全不同。Federation是"拉取聚合结果"——A Prometheus从B Prometheus拉取已经聚合过的指标（比如`rate()`后的结果），数据丢失了大量原始信息。Remote Write是"推送原始数据"——A Prometheus把采集到的raw samples完整推到远程存储，你可以在远程侧做任何聚合和计算。举个例子：如果你用Federation拉了一个`node_cpu_usage`的avg，你永远无法在全局范围重新算p99分位值；但如果走Remote Write，原始samples全在远程库里，想怎么算都行。Federation适合做分级告警聚合，Remote Storage适合做全局数据湖。

**小胖**：那什么样的存储系统适合做Remote Storage？

**大师**：四个特征：**高吞吐写入**（Prometheus每秒可能产生百万级samples）、**列式存储**（时序数据天然适合列存，压缩率高）、**高效的时间范围查询**（这些系统本质上都是时序数据库）、**支持PromQL或兼容接口**。VictoriaMetrics是最热门的选择——单机版就能顶住百万samples/s的写入吞吐，而且完全兼容PromQL。ClickHouse则适合做深度分析，它不直接支持PromQL，但可以用SQL做任意复杂的聚合。

## 三、项目实战

### 环境准备

- Prometheus 2.x 运行中（作为数据源）
- Docker 环境
- VictoriaMetrics 单机版作为 Remote Storage 接收端

### 步骤1：启动 VictoriaMetrics

```bash
# Docker启动VictoriaMetrics单机版
docker run -d --name victoriametrics \
  -p 8428:8428 \
  -v victoria_data:/victoria-metrics-data \
  victoriametrics/victoria-metrics:latest \
  -storageDataPath=/victoria-metrics-data \
  -retentionPeriod=36
```

关键参数说明：
- `-retentionPeriod=36`：数据保留36个月（3年），按月为单位
- `-p 8428:8428`：VictoriaMetrics默认端口，Remote Write接收端路径为 `/api/v1/write`
- 数据卷挂载到宿主机，避免容器重启丢失数据

验证服务启动成功：

```bash
# 检查health接口
curl http://localhost:8428/health

# 查看VictoriaMetrics自身指标
curl http://localhost:8428/metrics | head -20
```

### 步骤2：配置 Prometheus Remote Write

编辑 `prometheus.yml`，添加以下配置块：

```yaml
remote_write:
  - url: 'http://victoriametrics:8428/api/v1/write'
    # 队列配置（关键！）
    queue_config:
      capacity: 10000              # 队列最大容量（samples）
      max_shards: 30               # 最大并发发送shard数
      min_shards: 1                # 最小并发数
      max_samples_per_send: 5000   # 每批发多少samples
      batch_send_deadline: 10s     # 批次最大等待时间
      min_backoff: 30ms            # 最小重试等待
      max_backoff: 10s             # 最大重试等待
      retry_on_http_429: true      # 触发限流时重试

    # 只发送特定标签的数据（过滤规则）
    write_relabel_configs:
      - source_labels: [__name__]
        regex: 'node_.*|up'
        action: keep

    # 远程写入的metadata配置
    metadata_config:
      send: true
      send_interval: 1m
```

**队列参数逐行解读**：

| 参数 | 作用 | 调优思路 |
|------|------|----------|
| `capacity` | 队列能堆积多少samples，满了就丢弃 | 太小则高频丢数据，太大则OOM风险高。根据`每秒samples数 × 远程最大不可用时间`估算 |
| `max_shards` | 并发HTTP连接数，每个shard独立发送 | 越大吞吐越高，但每个shard有独立buffer，内存占用=shards×max_samples_per_send×sample_size。建议从4开始逐步上调 |
| `min_shards` | 空闲时的最小并发数 | 保持1即可，Prometheus会自动根据队列积压上调shard数 |
| `max_samples_per_send` | 每批打包多少samples | 越大单次请求效率越高，但请求延迟也越大。5000-10000是合理范围 |
| `batch_send_deadline` | 批次未满时强制发送的超时 | 数据量小的时候防止sample在队列里"囤积"太久 |
| `min_backoff` / `max_backoff` | 失败后的退避等待范围 | 避免远程故障时疯狂重试打挂远程存储。保持默认值即可 |
| `retry_on_http_429` | 是否在收到限流响应时重试 | 必须开，否则远程一限流就丢数据 |

`write_relabel_configs` 示例中只保留了 `node_*` 和 `up` 两类指标，其他指标（如业务自定义metrics）不会被写入远程存储。注意：**过滤规则一旦配错，本地数据过期后远程也查不到，数据就永久丢失了。**

### 步骤3：配置 Prometheus Remote Read（可选）

```yaml
remote_read:
  - url: 'http://victoriametrics:8428/api/v1/read'
    read_recent: false
    required_matchers:
      job: 'node'
```

参数解释：
- `read_recent: false`：查询超过本地retention（如15天）的数据时才走远程；小于15天的走本地TSDB。这是**分层查询**模式。
- `read_recent: true`：所有查询都走远程，本地TSDB不参与。适合远程存储性能足够好的场景。
- `required_matchers`：只从远程读取 `job=node` 的数据，减少不必要的数据传输。

### 步骤4：验证数据流向

重启Prometheus后，用以下命令检查Remote Write队列状态：

```bash
# 查看WAL回放状态
curl -s http://localhost:9090/api/v1/status/walreplay | jq '.data'

# 查看Remote Write目标状态
curl -s http://localhost:9090/api/v1/status/runtimeinfo | jq '.data'
```

关键监控指标（在Prometheus Web UI中查询）：

| 指标 | 含义 |
|------|------|
| `prometheus_remote_storage_highest_timestamp_in_seconds` | 最新发送成功的时间戳 |
| `prometheus_remote_storage_queue_highest_sent_timestamp_seconds` | 队列中最高已发送时间戳 |
| `prometheus_remote_storage_samples_total` | 总发送samples数 |
| `prometheus_remote_storage_sent_bytes_total` | 总发送字节数 |
| `prometheus_remote_storage_failed_samples_total` | 发送失败的samples数 |
| `prometheus_remote_storage_retried_samples_total` | 重试的samples数 |

**健康检查PromQL**：

```promql
# 数据延迟（应<30s）
prometheus_remote_storage_highest_timestamp_in_seconds - time()

# 失败samples应始终为0
prometheus_remote_storage_failed_samples_total

# 新增失败samples速率（最近5分钟应无变化）
rate(prometheus_remote_storage_failed_samples_total[5m])
```

在 VictoriaMetrics 侧验证数据：

```bash
# 查询VictoriaMetrics中已接收的指标总数
curl -s 'http://localhost:8428/api/v1/series?match[]={__name__=~".%2B"}' | jq '.data | length'

# 查询特定指标的最新值
curl -s 'http://localhost:8428/api/v1/query?query=up' | jq '.data.result'
```

### 步骤5：对比查询性能

```promql
# 查询最近1小时的数据（走本地TSDB，响应应<200ms）
rate(node_cpu_seconds_total{mode="idle"}[5m])

# 查询3天前的数据（走Remote Read）
rate(node_cpu_seconds_total{mode="idle"}[5m] offset 3d)
```

记录两条查询的响应时间差异。典型结果：本地查询毫秒级，远程查询百毫秒到秒级（取决于远程存储性能和网络延迟）。

### 可能遇到的坑

**坑1：队列堵满导致数据丢失**
- 现象：`prometheus_remote_storage_failed_samples_total`持续增长
- 原因：`capacity`太小或远程存储写入太慢
- 解决：增大`capacity`和`max_shards`，或减少`max_samples_per_send`降低单次请求压力

**坑2：远程存储长时间不可用导致Prometheus OOM**
- 现象：Prometheus内存使用持续飙升
- 原因：WAL持续增长、队列堆积、backoff期间样品在内存中堆积，三者叠加
- 解决：为远程存储部署高可用集群；适当降低`capacity`让旧数据优先被丢弃而非撑爆内存

**坑3：write_relabel_configs过滤规则过严**
- 现象：远程查询某些指标返回空结果
- 原因：过滤规则的`regex`匹配范围过窄，重要指标被漏掉
- 解决：先用`promtool test rules`校验relabel规则的正确性

**坑4：VictoriaMetrics和Prometheus的PromQL差异**
- 现象：同一查询在两边的结果不同
- 典型差异：`predict_linear`的精度、某些聚合函数的NaN处理行为
- 解决：生产前对关键查询做双端验证

## 四、项目总结

### Remote Write队列调优指南

根据数据量级别提供推荐配置：

| 数据量（samples/s） | capacity | max_shards | max_samples_per_send | 说明 |
|---------------------|----------|------------|----------------------|------|
| < 1万 | 5000 | 4 | 2000 | 小规模，默认配置即可 |
| 1万 ~ 10万 | 20000 | 10 | 5000 | 中等规模，适度增大 |
| 10万 ~ 50万 | 50000 | 20 | 8000 | 大规模，需关注内存 |
| > 50万 | 100000 | 30 | 10000 | 超大规模，建议水平扩展Prometheus |

核心公式：`capacity ≥ 每秒samples × 远程存储最大可容忍不可用秒数`。

### Remote Storage 方案对比

| 特性 | VictoriaMetrics | Thanos Receive | Cortex | ClickHouse |
|------|----------------|----------------|--------|------------|
| 部署复杂度 | 低（单二进制） | 中（需对象存储） | 高（微服务架构） | 中（需ZK/集群） |
| 写入吞吐 | 极高（百万级/s） | 高 | 高 | 高 |
| PromQL兼容 | 完全兼容 | 完全兼容 | 完全兼容 | 不兼容（SQL） |
| 压缩率 | 极高（比Prometheus高7x） | 中 | 中 | 高（列存） |
| 查询延迟 | 低 | 中 | 中 | 低（OLAP优化） |
| 适用场景 | 中小团队、单机长期存储 | 大规模、需要全局视图 | 超大规模SaaS平台 | 深度数据分析 |

### 适用与不适用场景

**适用场景**：
- 合规/审计要求长期保留监控数据（1年以上）
- 多集群数据汇聚与全局分析
- 降低本地SSD存储成本（本地只留热数据，远程用廉价HDD）
- 跨Prometheus实例的统一查询入口

**不适用场景**：
- 对查询延迟要求在100ms以内的实时告警大盘（走Remote Read的延迟不可控）
- 只需2周以内数据的短周期监控（直接调大本地retention更简单）
- 网络极不稳定的环境（Remote Write依赖稳定的网络连接）

### 常见踩坑经验

**案例1：max_shards太大导致远程存储过载**。某团队将`max_shards`设到200，结果VictoriaMetrics每秒钟被200个并发HTTP连接轰炸，写入延迟飙到5秒。定位方法：查看远程存储的`/metrics`端点中`vm_hourly_series_limit`相关指标。解决：逐步下调shard数，用`rate(prometheus_remote_storage_highest_timestamp_in_seconds[1m])`验证吞吐未下降。

**案例2：capacity太小导致本地数据丢失**。某团队设置的`capacity=500`，某次远程存储维护3小时，队列在10秒内就满了，之后所有新数据被丢弃。3小时的数据出现真空期，审计时对不上账。解决：按照"日均samples × 维护窗口时长"重新计算capacity，并配置告警规则 `prometheus_remote_storage_dropped_samples_total > 0`。

**案例3：Remote Read timeout导致Grafana大盘空白**。某团队配置了Remote Read但没调超时参数，查询1年前的数据时因为远程存储扫描范围太大，返回超时，Grafana面板直接白屏。解决：在`remote_read`配置中添加`remote_timeout: 2m`，并在Grafana面板中设置合理的查询时间范围。

### 思考题

1. Prometheus本地TSDB保留15天，Remote Storage保留3年。当用户在Grafana中查询30天前的数据时，Prometheus如何判断应该将查询路由到Remote Read而不是本地TSDB？

2. 如果需要将同一份监控数据同时写入VictoriaMetrics（用于实时查询）和InfluxDB（用于数据部门做离线分析），如何在`prometheus.yml`中配置？两个Remote Write目标之间是串行还是并行发送？如果其中一个写入失败，会影响另一个吗？

---

*本章详细介绍了Prometheus Remote Storage的架构原理、配置实践与运维要点。下一章我们将深入Thanos，探讨如何基于对象存储构建高可用的全局监控视图。*
