# 第29章：VictoriaMetrics——高性能时序数据库

## 一、项目背景

公司的Prometheus集群已经监控了2000+台物理机、5000+个微服务实例，日均采集指标量突破8000万条。随着监控规模持续膨胀，Prometheus本地TSDB的瓶颈越来越尖锐，运维团队每周都要处理至少两次Prometheus OOM告警。

**内存吃紧是最大的痛。** 当前单实例内存占用高达64GB，即便分配了96GB的物理内存，在Compaction高峰期依然会触发OOM Kill。究其原因，Prometheus将活跃series的全部索引——包括label name、label value、倒排索引——完整加载到内存中。按照每个series约3-5KB的索引开销计算，300万个active series就需要12-15GB纯索引内存，再加上查询缓存、WAL缓冲区和Go runtime本身的开销，64GB内存捉襟见肘丝毫不出意外。

**查询延迟也令人头疼。** P99查询延迟已经突破10秒，Grafana面板加载时常出现超时，告警评估也受到影响。Prometheus的查询引擎需要遍历内存索引来定位数据块，当series数量到达百万级时，即便有倒排索引，涉及多个label筛选的查询仍然需要扫描大量索引条目。更要命的是，一个大范围查询（例如7天的`rate()`计算）会触发数十乃至上百个数据块的读取和合并操作，磁盘I/O瞬间被打满。

**磁盘I/O和Compaction的恶性循环。** TSDB的Compaction过程需要将内存中的chunk刷盘、合并重叠数据块、删除过期数据。当写入速率超过300K samples/s时，Compaction造成的CPU飙升会导致新数据写入变慢，反过来又增加了内存积压。运维团队发现，官方推荐的单实例上限（约100万active series）已经被突破了两倍，必须寻求高性能替代或补充方案。

**VictoriaMetrics（简称VM）** 正是在这种背景下进入选型视野的。它是Prometheus生态中最受欢迎的高性能时序数据库，诞生于CloudFlare内部对大规模监控场景的极致优化需求。VM在架构上做了三个关键选择：第一，用mmap将索引映射到磁盘文件而非全量加载到内存，使内存占用约为Prometheus的1/7；第二，重新设计索引结构，引入倒排索引缓存和查询结果缓存，使查询速度提升约10倍；第三，原生支持PromQL（同时扩展了MetricsQL），可以直接作为Prometheus的Remote Write目标，迁移成本极低。此外，VM提供单机版（开箱即用，类似Prometheus的部署体验）和集群版（vminsert/vmselect/vmstorage三层分离、支持水平扩展）两种模式，既足够轻量也足够强悍。

## 二、剧本式交锋对话

**小胖**（抓耳挠腮地敲着键盘）：“大师！我们Prometheus又OOM了！这周都第三次了！我刚才看到VictoriaMetrics的GitHub仓库，Star都快1万了，它真能解决我们的内存问题吗？它和Prometheus到底有什么不一样？”

**大师**（端着保温杯，不急不缓）：“VM和Prometheus最大的区别在于内存模型。Prometheus把活跃series的索引全部放在堆内存里，你300万series，索引就要吃掉十几GB。VM用的是mmap——把索引文件映射到虚拟地址空间，由操作系统的page cache来决定哪些部分真正驻留在物理内存中。常用的索引页自然会被缓存，不常用的就被换出，内存利用率高得多。实测下来，同规模数据VM的内存占用只有Prometheus的七分之一左右。”

**小白**（凑过来插话）：“那查询性能呢？我们现在Grafana打开一个面板要等10秒，老板天天问是不是监控系统挂了。”

**大师**：“VM查询快，快在三个层面。第一，索引结构更高效——VM用了一种类似LSM-Tree的结构组织倒排索引，配合分段索引，命中的索引块更少。第二，倒排索引缓存——高频查询的索引路径被缓存，避免重复遍历。第三，查询结果缓存——对完全相同的时间范围查询，VM直接返回缓存结果，这在Grafana多人同时打开同一面板时效果尤其明显。综合下来，同环境下VM的查询延迟大约是Prometheus的十分之一。”

**小胖**（若有所思）：“那我还听说VM有单机版和集群版，它们有什么区别？不会又像Thanos那样搭一堆组件吧？”

**大师**：“不一样。VM单机版就是所有功能合一——采集（如果代替Prometheus）、写入、查询、存储都在一个进程里，部署和Prometheus一样简单。集群版拆成三个组件：vminsert负责接收写入请求，用一致性哈希把数据分片到多个vmstorage节点；vmselect负责接收查询请求，并发查询所有vmstorage节点然后聚合结果；vmstorage则是纯存储节点，彼此之间不通信，依赖上层分片策略保证数据不重复。扩容时直接加vmstorage节点就行。”

**小白**（眼睛一亮）：“那它和Thanos比怎么样？我们之前调研过Thanos。”

**大师**：“各有所长。VM的查询性能明显优于Thanos，因为VM从底层设计就是为高性能而生的。Thanos则胜在Prometheus兼容性更好——它的Sidecar模式是对Prometheus的无侵入式扩展，去重能力（通过replica label）也更成熟。简单说：如果你追求极致查询速度和更低资源消耗，选VM；如果你需要与Prometheus官网标准100%兼容、或者需要全局视图去重，选Thanos。另外MetricsQL是VM的一大王牌，它完全兼容PromQL，同时扩展了很多实用函数——比如`rollup_rate`能自动处理Prometheus抓取间隔不均匀带来的数据重复问题，你用过就知道有多省心。”

**小胖**：“那数据怎么进VM呢？要重写所有抓取逻辑吗？”

**大师**：“完全不用。两种方式：第一种，Prometheus加一行Remote Write配置，数据就自动转发到VM了，Prometheus照样工作，VM当远程存储；第二种，用vmagent代替Prometheus的scrape模块——vmagent兼容Prometheus的`prometheus.yml`配置文件，零迁移成本。vmagent更省资源，内存只有Prometheus的十分之一，而且可以把一份数据同时写到多个后端，比如同时写VM集群版和一个备份的Prometheus。不过要注意，vmagent只负责采集和转发，不做查询和告警评估——告警和Recording Rules需要保留Prometheus Server或者加入vmalert组件。”

## 三、项目实战

### 环境准备

本次实战需要在服务器上安装Docker和Docker Compose，搭建一套完整的VictoriaMetrics测试环境，包含Prometheus数据源、VM单机版和VM集群版三种形态。建议至少分配8GB内存和100GB可用磁盘空间。

### 步骤1：单机版VictoriaMetrics快速部署

创建项目目录并编写Docker Compose文件：

```yaml
# docker-compose.yml - 单机版
version: '3.8'
services:
  victoriametrics:
    image: victoriametrics/victoria-metrics:latest
    ports:
      - '8428:8428'
    volumes:
      - vm_data:/victoria-metrics-data
    command:
      - '-storageDataPath=/victoria-metrics-data'
      - '-retentionPeriod=12'
      - '-httpListenAddr=:8428'
      - '-search.maxUniqueTimeseries=300000'
      - '-memory.allowedPercent=60'

volumes:
  vm_data:
```

```bash
# 启动单机版VM
docker compose up -d
```

启动后验证VM自身状态：

```bash
# 检查VM健康状态
curl http://localhost:8428/health

# 查询VM自带的metrics（确认Query接口正常）
curl http://localhost:8428/api/v1/query?query=up

# 查看VM的运行时信息（版本、内存使用、数据点量等）
curl http://localhost:8428/metrics | grep -E "vm_app_version|vm_rows"
```

关键参数说明：
- `-retentionPeriod=12`：数据保留12个月，单位是月，也可以用`1d`、`2y`等形式
- `-search.maxUniqueTimeseries=300000`：限制单次查询扫描的最大unique series数，防止超大查询打爆内存
- `-memory.allowedPercent=60`：VM允许使用的物理内存上限占系统总内存的百分比

### 步骤2：配置Prometheus Remote Write到VM

在现有Prometheus的配置文件中添加Remote Write目标：

```yaml
# prometheus.yml
global:
  scrape_interval: 15s
  evaluation_interval: 15s

scrape_configs:
  - job_name: 'node'
    static_configs:
      - targets: ['localhost:9100']

remote_write:
  - url: 'http://victoriametrics:8428/api/v1/write'
    queue_config:
      max_samples_per_send: 10000
      capacity: 20000
      max_shards: 20
```

重启Prometheus后，等待1-2分钟让数据同步到VM。验证数据一致性：

```bash
# 在VM中查询与Prometheus相同的数据
curl 'http://localhost:8428/api/v1/query?query=up'

# 对比series数量
# Prometheus:
curl 'http://localhost:9090/api/v1/query?query=count(up)'
# VM:
curl 'http://localhost:8428/api/v1/query?query=count(up)'

# 两者返回结果应一致
```

`queue_config`参数调优要点：
- `max_samples_per_send`：每次发送的最大样本数，网络快可以设大一点（默认5000）
- `capacity`：内存中队列的最大样本数，按`samples_per_second * 2`估算
- `max_shards`：最大并行发送协程数，Remote Write慢时可以加大

### 步骤3：集群版VictoriaMetrics部署

当单机VM的存储或写入吞吐成为瓶颈时，需要部署集群版：

```yaml
# docker-compose-cluster.yml - 集群版
version: '3.8'
services:
  vminsert:
    image: victoriametrics/vminsert:latest
    ports:
      - '8480:8480'
    command:
      - '-storageNode=vmstorage-1:8400'
      - '-storageNode=vmstorage-2:8400'
    depends_on:
      - vmstorage-1
      - vmstorage-2

  vmselect:
    image: victoriametrics/vmselect:latest
    ports:
      - '8481:8481'
    command:
      - '-storageNode=vmstorage-1:8401'
      - '-storageNode=vmstorage-2:8401'
    depends_on:
      - vmstorage-1
      - vmstorage-2

  vmstorage-1:
    image: victoriametrics/vmstorage:latest
    ports:
      - '8482:8482'
      - '8400:8400'
      - '8401:8401'
    volumes:
      - vm_storage_1:/storage
    command:
      - '-storageDataPath=/storage'
      - '-retentionPeriod=36'

  vmstorage-2:
    image: victoriametrics/vmstorage:latest
    ports:
      - '8483:8482'
    volumes:
      - vm_storage_2:/storage
    command:
      - '-storageDataPath=/storage'
      - '-retentionPeriod=36'

volumes:
  vm_storage_1:
  vm_storage_2:
```

```bash
# 启动集群版
docker compose -f docker-compose-cluster.yml up -d
```

集群架构核心要点：
- **vminsert**接收写入请求，对每条时序数据按`metric name + labels`计算hash，取模分发到对应的vmstorage节点（一致性哈希），确保同一series始终落在同一节点
- **vmselect**收到查询请求后，并发向所有vmstorage节点发起子查询，然后将各节点返回的结果聚合（merge/sort/deduplicate），返回给客户端
- **vmstorage**节点之间完全独立，不通信、不复制、不共享数据——数据分布完全依赖vminsert的分片策略
- 写入API路径：`http://vminsert:8480/insert/0/prometheus/api/v1/write`（注意中间的`/insert/0/prometheus/`前缀）
- 查询API路径：`http://vmselect:8481/select/0/prometheus/api/v1/query?query=up`（注意`/select/0/prometheus/`前缀）

验证集群版工作状态：

```bash
# 通过vminsert写入测试数据（先启动一个测试exporter）
# 然后用vmselect查询
curl 'http://localhost:8481/select/0/prometheus/api/v1/query?query=up'

# 检查各vmstorage节点的数据分布
curl 'http://localhost:8482/metrics' | grep vm_rows
curl 'http://localhost:8483/metrics' | grep vm_rows
```

### 步骤4：MetricsQL实战——超越PromQL的能力

VictoriaMetrics的MetricsQL完全兼容PromQL，同时提供了大量扩展函数，大幅简化日常查询：

```promql
# === 标准PromQL（VM完全兼容）===
rate(node_cpu_seconds_total{mode="idle"}[5m])

# === rollup系列：自动处理数据去重和counter reset ===
# rollup_rate自动检测并处理Prometheus抓取导致的重复数据点
rollup_rate(node_cpu_seconds_total{mode="idle"}[5m])

# rollup_increase：比increase更精确，自动处理counter reset
rollup_increase(http_requests_total[1h])

# === topk系列：直接针对聚合函数取TopN ===
# 找出CPU使用率最高的5个实例
topk_max(5, avg(rate(node_cpu_seconds_total{mode!="idle"}[5m])) by (instance))

# 找出内存剩余最少的3台机器
topk_min(3, node_memory_MemAvailable_bytes)

# 找出平均响应时间最长的前10个API
topk_avg(10, rate(http_request_duration_seconds_sum[5m]) / rate(http_request_duration_seconds_count[5m]))

# === 高级时间操作 ===
# 查询当前值与前1天的变化率（%）
(node_memory_MemAvailable_bytes - node_memory_MemAvailable_bytes offset 1d) 
  / node_memory_MemAvailable_bytes offset 1d * 100

# === duration_over_time：计算条件满足的时长 ===
# 过去1小时内CPU使用率超过80%的持续时间（秒）
duration_over_time((node_cpu_seconds_total{mode="idle"} < 20)[1h:15s])

# === 灵活的label操作 ===
# 重命名label
label_set(metric_name, "env", "production")
# 删除label
label_del(metric_name, "pod")
# 复制label值到新label
label_copy(node_cpu_seconds_total, "instance", "hostname")

# === max_over_time/min_over_time：时间窗口内的极值 ===
max_over_time(node_cpu_seconds_total{mode="idle"}[1h])
min_over_time(node_memory_MemAvailable_bytes[6h])
```

MetricsQL亮点速查：

| 功能类别 | 函数 | 解决的问题 |
|---------|------|-----------|
| 数据去重 | `rollup_rate` / `rollup_increase` | 自动处理Prometheus抓取间隔不均匀导致的重复数据点 |
| TopN查询 | `topk_max` / `topk_min` / `topk_avg` | 直接在聚合函数结果上取TopN，无需子查询 |
| 持续时间 | `duration_over_time` | 计算某个条件在指定时间窗口内满足的持续时长 |
| Label操作 | `label_set` / `label_del` / `label_copy` | 在查询时动态修改label，无需修改采集配置 |
| 时间窗口极值 | `max_over_time` / `min_over_time` | 获取时间范围内原始数据的最大值/最小值 |

### 步骤5：使用vmagent替代Prometheus采集

vmagent是一个专注于指标采集和转发的轻量级组件，可以完全替代Prometheus的scrape模块：

```yaml
# docker-compose-vmagent.yml
version: '3.8'
services:
  vmagent:
    image: victoriametrics/vmagent:latest
    ports:
      - '8429:8429'
    volumes:
      - ./prometheus.yml:/etc/prometheus/prometheus.yml
      - vmagent_data:/vmagentdata
    command:
      - '-promscrape.config=/etc/prometheus/prometheus.yml'
      - '-remoteWrite.url=http://victoriametrics:8428/api/v1/write'
      - '-remoteWrite.url=http://vminsert:8480/insert/0/prometheus/api/v1/write'

volumes:
  vmagent_data:
```

vmagent的核心优势：
- **极省资源**：内存占用约为Prometheus的1/10，因为它不维护TSDB索引，只做采集缓冲和转发
- **多后端写入**：可以将一份采集数据同时写入多个Remote Storage（如VM集群版 + 备份Prometheus），`-remoteWrite.url`参数可以指定多个
- **配置兼容**：直接复用Prometheus的`prometheus.yml`中的`scrape_configs`配置，零迁移成本
- **明确边界**：vmagent只负责采集和转发，不提供PromQL查询和告警评估——告警规则和Recording Rules需要保留Prometheus Server或加入vmalert组件

### 常见踩坑提示

1. **PromQL兼容性非100%**：`predict_linear`、`holt_winters`等函数的返回结果可能与Prometheus有细微差异，切换前务必在测试环境对比验证你的核心查询和告警规则
2. **集群扩容不自动rebalance**：添加新vmstorage节点后，已有数据不会自动迁移到新节点，历史数据仍留在旧节点上。新写入的数据才会按哈希策略分配到新节点
3. **vmagent不支持告警规则**：如果需要Recording Rules（预计算指标）和Alerting Rules（告警评估），必须额外部署vmalert组件或保留Prometheus Server
4. **单机版磁盘规划**：`retentionPeriod`设置较大（如12个月）时，需确保磁盘空间充足。粗略估算：每100万active series、15秒间隔、保留1个月约需200-300GB磁盘，集群版通过分片可以有效分摊

### 完整验证

```bash
# 1. 确认VM单机版中的series数量与Prometheus一致
curl 'http://localhost:8428/api/v1/query?query=count(up)'

# 2. 确认集群版查询正常
curl 'http://localhost:8481/select/0/prometheus/api/v1/query?query=up'

# 3. 对比VM和Prometheus的查询响应时间（1天数据）
time curl -s -o /dev/null 'http://localhost:9090/api/v1/query_range?query=rate(node_cpu_seconds_total{mode="idle"}[5m])&start=1d-ago&end=now&step=1m'
time curl -s -o /dev/null 'http://localhost:8428/api/v1/query_range?query=rate(node_cpu_seconds_total{mode="idle"}[5m])&start=1d-ago&end=now&step=1m'

# 4. 检查vmagent的采集和转发状态
curl http://localhost:8429/metrics | grep -E "promscrape|remotewrite"
```

## 四、项目总结

### VictoriaMetrics vs Prometheus vs Thanos 对比

| 维度 | Prometheus | VictoriaMetrics | Thanos |
|------|-----------|----------------|--------|
| 内存占用 | 高（全索引在堆内存） | 低（mmap，约1/7） | 中（Sidecar额外开销） |
| 查询速度 | 基准 | 快约10倍 | 依赖底层Prometheus |
| 磁盘I/O | Compaction时压力大 | 优化后的合并策略 | Sidecar上传对象存储有额外开销 |
| 集群支持 | 无（需Federation） | 原生集群（三层分离） | 需搭配多种组件 |
| PromQL兼容性 | 100% | 约95%（部分函数有差异） | 约99% |
| 部署复杂度 | 低（单二进制） | 低（单机）/ 中（集群） | 高（多组件协调） |
| 长期存储 | 本地磁盘，扩展受限 | 本地磁盘，集群可扩展 | 对象存储（S3/GCS），成本低 |

### vmagent vs Prometheus Scrape 对比

| 维度 | Prometheus Scrape | vmagent |
|------|-------------------|---------|
| 内存占用 | 高（含TSDB索引） | 低（约1/10） |
| 查询能力 | 完整PromQL | 不支持查询 |
| 告警评估 | 原生支持 | 不支持（需vmalert） |
| 多后端写入 | 需额外组件 | 原生支持（写多份） |
| 配置文件 | prometheus.yml | 100%兼容prometheus.yml |

### MetricsQL亮点功能速查

| 函数 | 用途 | 对应PromQL |
|------|------|------------|
| `rollup_rate` | 去重后计算rate | `rate`（但不处理重复点） |
| `rollup_increase` | 去重后计算increase | `increase`（但不处理重复点） |
| `topk_max(k, expr)` | 取聚合后最大值TopK | 需多层子查询实现 |
| `duration_over_time` | 条件持续时长 | 无直接等价函数 |
| `label_set/del/copy` | 动态label操作 | 需relabel_config |
| `max_over_time` | 时间窗口内最大值 | `max_over_time`（VM也支持） |

### 适用场景

- **大规模监控**：百万级active series场景下，VM的内存和查询性能优势显著
- **长期存储兼高性能查询**：需要保留数月至数年的数据，同时要求秒级查询响应
- **多集群数据汇聚**：通过vmagent可将多个Kubernetes集群或数据中心的指标统一写入VM集群
- **一份数据多后端分发**：vmagent支持将采集数据同时写入多个Remote Storage

### 不适用场景

- **团队技术栈单一**：不想引入新组件、只愿维护Prometheus的团队
- **100% PromQL标准兼容要求**：对predict_linear等少数函数的精度差异零容忍的场景
- **复杂告警评估**：需要大量Recording Rules和Alerting Rules，又不想额外部署vmalert
- **极小规模监控**：几十台机器的场景用Prometheus单机足够，引入VM属于过度设计

### 注意事项

1. **API路径差异**：VM单机版的写入API为`/api/v1/write`，集群版为`/insert/0/prometheus/api/v1/write`，查询API同理。做Remote Write配置时务必区分清楚
2. **集群扩容不自动rebalance**：建议在规划集群容量时预留20-30%的余量，避免频繁扩容导致数据分布不均。如需rebalance，官方建议用vmctl工具做数据迁移
3. **MetricsQL与PromQL的函数差异要测试验证**：建议抽取10-20条核心查询和告警规则，在VM上逐一测试，确认结果与Prometheus一致后再全量切换

### 常见踩坑经验

**案例一：集群版扩容后数据分布不均。** 某团队将vmstorage从3节点扩展到5节点，一个月后发现前3个节点的磁盘使用率达到85%，新节点只有30%。原因是历史数据不会自动rebalance，而新写入的数据恰好集中在旧节点命中的hash范围。解决方案：初期规划容量时预留足够的节点数，或者在扩容前用vmctl将部分数据手动迁移到新节点。

**案例二：Remote Write丢数据。** Prometheus配置Remote Write到VM后，发现VM中缺少部分时段的数据。排查发现`queue_config`中`max_shards`设置过小（默认仅5），当网络抖动或写入QPS突增时，队列积压导致丢数据。解决方案：将`max_shards`调至20，`capacity`调至20000，`max_samples_per_send`调至10000，同时监控`prometheus_remote_storage_samples_failed_total`指标。

**案例三：MetricsQL的rollup_rate行为差异导致告警误报。** 一条CPU使用率告警规则使用`rate()`在Prometheus上从未触发，切换到VM上的`rollup_rate()`后却频繁触警。原因是VM的`rollup_rate`对重复数据点有去重逻辑，在某些场景下计算出的rate值略高于Prometheus的`rate()`。解决方案：告警规则的阈值需根据VM的实际计算结果重新校准，不要直接复用原Prometheus的阈值。

### 思考题

1. **VictoriaMetrics集群版中，如果一个vmstorage节点宕机，查询会返回什么结果？数据会丢失吗？**
   查询不会失败，vmselect会收到宕机节点的错误响应，并将其从当前查询中排除——最终返回的是其他健康节点的聚合结果（部分数据）。因宕机导致的缺失数据能否恢复取决于数据写入策略：如果Remote Write配置了多副本（vmagent同时写多份或使用了vminsert的复制功能），数据可以从其他节点恢复；否则，在宕机期间该节点负责的那部分数据将永久丢失。因此，生产环境建议为vmstorage节点配置RAID或使用VM Enterprise版的复制功能。

2. **如何将VictoriaMetrics作为Prometheus的Remote Read后端，同时保留Prometheus本地TSDB作为热数据存储？**
   Prometheus 2.x版本原生支持Remote Read/Write双接口。配置方法：在Prometheus的`prometheus.yml`中同时配置`remote_read`和`remote_write`。Prometheus本地保留较短的热数据（如2小时），Remote Write将完整数据发送到VM。Remote Read配置让Prometheus能在查询时自动从VM拉取较早的历史数据。这样既保留了Prometheus本地TSDB的低延迟查询（热数据），又利用了VM的长期存储和高效查询能力（冷数据）。需要注意：Remote Read会增加查询延迟（多一次网络往返），建议在Grafana中配置不同数据源分别查询热数据和冷数据，而非依赖Prometheus的自动fallback机制。
