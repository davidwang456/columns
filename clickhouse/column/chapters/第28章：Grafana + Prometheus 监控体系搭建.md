# 第28章：Grafana + Prometheus 监控体系搭建

> **版本**：ClickHouse 24.x+ LTS
> **定位**：中级篇核心章节。从零搭建 ClickHouse 生产级监控体系，覆盖指标暴露、采集、可视化、告警全链路。
> **前置阅读**：第15章（查询分析器与 query_log 诊断）、第26章（性能调优）
> **预计阅读**：35 分钟 | **实战耗时**：60 分钟

---

## 1. 项目背景

某电商平台数据组的 ClickHouse 集群已经平稳运行了三个月。8 台物理机组成的双副本集群，每天入库 12 亿行订单埋点数据，支撑 30 多个运营看板的实时查询。一切看起来岁月静好，直到上周二下午两点零七分。

那天的故障复盘会上，运维主管老周把日志时间线投影到屏幕上："14:07，运营同事在钉钉群反馈报表打不开了。我当时第一反应是——数据库挂了？SSH 到所有节点挨个登录，一台台敲 `htop`、`df -h`、`system.metrics`，查到第四台才发现问题：Merge 积压了 600 多个任务，CPU IOwait 涨到 70%，整整拖了 30 分钟才定位到根因——前一天的物化视图级联触发了合并风暴。"

会议室沉默了。不是因为故障本身——数据系统出点岔子谁都理解。真正让团队难受的是：30 分钟才定位到问题。每一秒都是裸奔，没有任何仪表盘能一眼看到集群健康状态，没有任何告警能在队列积压超过阈值时自动通知。

"你们现在怎么知道集群是正常还是不正常？"CTO 问了一个灵魂问题。老周愣了一下，说了实话："靠经验——每天早上看一下 query_log 里的慢查询数量，偶尔跑一下 `system.mutations` 看看有没有卡住。周末没人值班，我们靠祈祷。"

这是一支优秀团队的通病：开发阶段注意力全在功能和性能上，监控是最后一块拼图，也是最容易被无限期推迟的那块。可没有监控的 ClickHouse，就像没有仪表盘的汽车——你不知道引擎温度、不知道油箱余量、不知道车速快慢，只能凭感觉开，等冒烟了再停车。

团队需要三样东西：(1) 一个能实时反映集群全貌的 Grafana 仪表盘，一眼看清 QPS / 延迟 / 磁盘 / 复制状态；(2) 一套主动告警体系，Merge 积压超过 100 条、磁盘使用超过 85%、副本延迟超过 60 秒——自动通知到钉钉/飞书/邮件；(3) 历史趋势分析能力，能回溯过去一周、一个月的指标曲线，支撑容量规划和扩容决策。

Prometheus + Grafana 是云原生监控的事实标准。ClickHouse 自带 Prometheus 协议兼容的指标端点（端口 9363），零插件、零改造即可接入。本章将带你从零搭建这套体系，让集群从"裸奔"进入"可视化驾驶舱"时代。

---

## 2. 项目设计：剧本式交锋对话

下午三点，运维间里三台显示器亮着。小胖正对着 Grafana 的空面板发愣，大师端着保温杯走进来，小白抱着笔记本跟在后面。

**小胖**（头也不抬）："监控有啥难的？Grafana 画几个图不就完事了？磁盘满了我邮件能收到就行。Prometheus 又是啥？多一层中间件多一个故障点，直接让 ClickHouse 往 Grafana 里写数据不行吗？"

**大师**把保温杯往桌上一放，笑了："小胖，你这种思路叫'推模式'——数据源主动往监控系统里推数据。但 Grafana 只是个画图工具，它不存时序数据。你需要一个时序数据库专门存指标，而且你的集群是 8 台机器——谁来保证 8 台机器的指标都被准时采集到？谁来处理采集失败了重试？谁来把磁盘使用率从 0.82 算成百分比？这些脏活累活，就是 Prometheus 干的。它的本质是：**定时去拉（pull）每台机器的指标 → 存到本地时序库 → 支持 PromQL 灵活查询和告警规则运算。** 至于 Grafana，它就是 Prometheus 的脸——把 PromQL 的结果画成好看的图表。"

**技术映射 #1**：监控架构选型——Prometheus 采用 Pull 模型，每隔 N 秒向目标的 `/metrics` 端点发起 HTTP 请求，拉取指标数据。Pull 模型的优势在于：采集端（Prometheus）掌握节奏，目标端（ClickHouse）只需暴露一个 Web 端点即可，不关心谁在采集、什么时候采集，架构解耦。相比之下，推模型（如 Graphite）需要业务代码主动上报，侵入性强且容易丢数据。

**小白**翻着 ClickHouse 文档，插话："ClickHouse 确实有个 `prometheus` 配置段，开在 9363 端口。但我 `curl` 了一下，里面有两三百个指标——`ClickHouseMetrics_Query`、`ClickHouseAsyncMetrics_ReplicasMaxAbsoluteDelay`、`ClickHouseProfileEvents_FailedQuery`……这么多指标，我该关注哪些？总不能全画到 Grafana 上吧？"

**大师**点头："问得好。这叫'指标过载'——Prometheus 不怕指标多，但人的注意力有限。你需要在几百个指标里挑出真正影响服务质量的'黄金信号'。"

大师在白板上写下四个词：

```
┌─ Rate（速率）── QPS / 写入行数 / 网络吞吐
├─ Errors（错误）── 失败查询数 / 副本异常数 / Mutation 失败数
├─ Duration（时延）── P50 / P95 / P99 查询延迟
└─ Saturation（饱和度）── 内存使用 / 磁盘使用 / Merge 积压
```

"这四条就是 Google SRE 经典中的 RED 方法的扩展——**USE 方法论关注资源（Utilization, Saturation, Errors），RED 方法论关注服务质量（Rate, Errors, Duration），合在一起才是完整的监控视角。** 具体到 ClickHouse，我把最重要的指标挑出来列个清单。"

大师敲开终端，打了一张表：

| 分类 | 指标名（Prometheus 格式） | 含义 | 告警阈值参考 |
|------|--------------------------|------|-------------|
| **QPS** | `ClickHouseProfileEvents_Query` | 累计查询数，用 `rate()` 算 QPS | 无固定阈值，关注突降 |
| **延迟** | `ClickHouseAsyncMetrics_QueryLatency_quantile_0_95` | P95 查询延迟（秒） | > 5s 需关注 |
| **延迟** | `ClickHouseAsyncMetrics_QueryLatency_quantile_0_99` | P99 查询延迟（秒） | > 10s 需告警 |
| **错误** | `ClickHouseProfileEvents_FailedQuery` | 失败查询累计数 | `rate() > 10/min` |
| **内存** | `ClickHouseMetrics_MemoryTracking` | 当前内存使用量（字节） | > 物理内存 85% |
| **Merge** | `ClickHouseMetrics_BackgroundPoolTask` | 当前后台 Merge/Mutation 任务数 | > 100 告警 |
| **磁盘** | `ClickHouseAsyncMetrics_DiskTotal_default` | 默认磁盘总容量 | — |
| **磁盘** | `ClickHouseAsyncMetrics_DiskAvailable_default` | 默认磁盘可用容量 | 可用 < 15% 告警 |
| **副本延迟** | `ClickHouseAsyncMetrics_ReplicasMaxAbsoluteDelay` | 最大副本绝对延迟（秒） | > 60s 告警 |
| **连接数** | `ClickHouseMetrics_TCPConnection` | 当前 TCP 连接数 | 关注突增 |

**小胖**凑近了看："等一下，`BackgroundPoolTask` 是什么？跟 Merge 有什么关系？"

**大师**："ClickHouse 的 Merge、Mutation 和数据移动任务都投递到一个共享的后台线程池里。`BackgroundPoolTask` 就是这个池子里**正在等待和正在执行的任务总数**。正常运行时这个数字在 0-20 之间波动。一旦飙到 100+，意味着新数据写入的速度超过了后台合并的速度——大量小 Part 堆积，查询时 ClickHouse 要打开海量文件，延迟会指数级上升。这就是我在复盘会上说的'合并风暴'——这个指标就是风暴的预警雷达。"

**小白**追问："但这些指标每次重启 ClickHouse 都会归零——`ProfileEvents` 和 `Metrics` 都是内存里的累计值。如果我想看昨天凌晨三点的内存使用峰值，怎么看？"

**大师**："这就是 `system.asynchronous_metric_log` 的用武之地。它是 ClickHouse 的'历史指标日志'——每隔一段时间（默认 1 秒），把当前所有指标快照写入到一张 MergeTree 表中持久化。你在 `config.xml` 里打开 `<asynchronous_metric_log>` 配置块之后，所有 `system.metrics` 和 `system.events` 里的值都会以时间戳为序存下来。这样 Prometheus 存最近 30 天的实时数据，`asynchronous_metric_log` 提供超过 Prometheus 保留期的历史回溯能力——两者互补。"

大师继续补充："还有一个常见陷阱——9363 端口的指标是**单节点**的。你 8 台机器集群，Prometheus 要分别去每台的 9363 拉数据。在 Grafana 里画大盘的时候，如果只想看集群的聚合指标——比如'所有节点中最大的副本延迟'，用 PromQL 的 `max()` 函数聚合即可。如果想逐台机器对比——比如看各节点内存使用量，可以用 `instance` 标签分组。"

**小胖**挠头："那就是说监控其实分两层？一层是机器本身的——CPU 内存 磁盘 网络；一层是 ClickHouse 自己的——QPS 延迟 Merge 复制。"

**大师**："完全正确。这就是 **Node Exporter + ClickHouse 端点双采集**的经典架构。"

```
┌─ Node Exporter (9100) ──── CPU / 内存 / 磁盘 / 网络 ─ 每个节点部署
├─ ClickHouse (9363)     ── QPS / 延迟 / Merge / 复制 ─ 每个节点部署
├─ Prometheus ────────── 统一拉取、存储、告警计算
└─ Grafana ───────────── 统一可视化、面板编排
```

"Node Exporter 是 Prometheus 生态里的'机器体检仪'，独立于 ClickHouse 运行，采集 OS 级别的指标。为什么需要它？因为当 ClickHouse 挂掉的时候，光靠 ClickHouse 自己的指标你啥也看不到——需要靠 Node Exporter 来判断是 CPU 打满了、内存 OOM 了、还是磁盘 IO 吃光了。这两层指标合在一起，才是完整的故障排查棋盘。"

---

## 3. 项目实战

### 环境准备

用 Docker Compose 一键拉起 ClickHouse + Prometheus + Grafana：

```yaml
# docker-compose.yml
version: '3.8'
services:
  clickhouse:
    image: clickhouse/clickhouse-server:24
    container_name: ch28-clickhouse
    ports:
      - "8123:8123"   # HTTP 接口
      - "9000:9000"   # Native 协议
      - "9363:9363"   # Prometheus 指标端点
    volumes:
      - ./clickhouse/config.d:/etc/clickhouse-server/config.d
      - ./clickhouse/data:/var/lib/clickhouse
    ulimits:
      nofile:
        soft: 262144
        hard: 262144

  node-exporter:
    image: prom/node-exporter:latest
    container_name: ch28-node-exporter
    ports:
      - "9100:9100"
    command:
      - '--collector.filesystem.ignored-mount-points=^/(sys|proc|dev|run)($$|/)'

  prometheus:
    image: prom/prometheus:latest
    container_name: ch28-prometheus
    ports:
      - "9090:9090"
    volumes:
      - ./prometheus/prometheus.yml:/etc/prometheus/prometheus.yml
      - ./prometheus/rules.yml:/etc/prometheus/rules.yml
      - ./prometheus/data:/prometheus
    command:
      - '--config.file=/etc/prometheus/prometheus.yml'
      - '--storage.tsdb.path=/prometheus'
      - '--web.console.libraries=/usr/share/prometheus/console_libraries'
      - '--web.console.templates=/usr/share/prometheus/consoles'

  grafana:
    image: grafana/grafana:latest
    container_name: ch28-grafana
    ports:
      - "3000:3000"
    environment:
      - GF_INSTALL_PLUGINS=vertamedia-clickhouse-datasource
      - GF_SECURITY_ADMIN_PASSWORD=admin
    volumes:
      - ./grafana/dashboards:/etc/grafana/provisioning/dashboards
      - ./grafana/datasources:/etc/grafana/provisioning/datasources
      - ./grafana/data:/var/lib/grafana
```

```bash
# 创建目录结构
mkdir -p prometheus clickhouse/config.d grafana/{dashboards,datasources,data}

# 启动全套服务
docker-compose up -d

# 验证各服务
curl http://localhost:8123          # ClickHouse HTTP OK
curl http://localhost:9363/metrics  # Prometheus 指标端点正常
curl http://localhost:9090/-/healthy # Prometheus 健康
curl http://localhost:3000/api/health # Grafana 健康
```

### Step 1: 启用 ClickHouse Prometheus 端点

在 ClickHouse 的 `config.d` 目录下添加 Prometheus 配置：

```xml
<!-- clickhouse/config.d/prometheus.xml -->
<clickhouse>
    <prometheus>
        <endpoint>/metrics</endpoint>
        <port>9363</port>
        <metrics>true</metrics>
        <events>true</events>
        <asynchronous_metrics>true</asynchronous_metrics>
    </prometheus>
</clickhouse>
```

四个关键开关说明：
- `<metrics>true</metrics>` — 暴露 `system.metrics` 中的即时值指标（内存使用、连接数、后台任务等）
- `<events>true</events>` — 暴露 `system.events` 中的累计事件（查询总数、读写字节数等）
- `<asynchronous_metrics>true</asynchronous_metrics>` — 暴露后台异步计算指标（副本延迟、磁盘容量、查询延迟分位数等），这些指标更新频率低但计算成本高

重启 ClickHouse 后验证：

```bash
curl -s http://localhost:9363/metrics | head -30
```

典型输出示例：

```
# HELP ClickHouseMetrics_Query Number of executing queries
# TYPE ClickHouseMetrics_Query gauge
ClickHouseMetrics_Query{event="SelectQuery"} 2
ClickHouseMetrics_Query{event="InsertQuery"} 1
# HELP ClickHouseMetrics_MemoryTracking Total memory tracking
ClickHouseMetrics_MemoryTracking{} 1234567890
# HELP ClickHouseAsyncMetrics_ReplicasMaxAbsoluteDelay Max replica delay
ClickHouseAsyncMetrics_ReplicasMaxAbsoluteDelay{} 5.2
# HELP ClickHouseAsyncMetrics_QueryLatency Query latency quantiles
ClickHouseAsyncMetrics_QueryLatency{quantile="0.5"} 0.012
ClickHouseAsyncMetrics_QueryLatency{quantile="0.95"} 0.35
ClickHouseAsyncMetrics_QueryLatency{quantile="0.99"} 2.1
# HELP ClickHouseProfileEvents_Query Total query count
ClickHouseProfileEvents_Query{} 12345
```

> **注意**：`ClickHouseProfileEvents_*` 开头的指标是**累计值**（Counter 类型），在 PromQL 中必须用 `rate()` 函数计算每秒速率才有意义。`ClickHouseMetrics_*` 和 `ClickHouseAsyncMetrics_*` 是**即时值**（Gauge 类型），可以直接使用。

### Step 2: Prometheus 采集配置

```yaml
# prometheus/prometheus.yml
global:
  scrape_interval: 15s      # 每 15 秒采集一次指标
  evaluation_interval: 15s  # 每 15 秒计算一次告警规则
  external_labels:
    cluster: 'clickhouse-prod'
    env: 'production'

rule_files:
  - 'rules.yml'

scrape_configs:
  # 采集 ClickHouse 自身指标
  - job_name: 'clickhouse'
    static_configs:
      - targets: ['ch28-clickhouse:9363']
        labels:
          service: 'clickhouse'
          host_type: 'clickhouse-node'

  # 采集主机级指标（Node Exporter）
  - job_name: 'node-exporter'
    static_configs:
      - targets: ['ch28-node-exporter:9100']
        labels:
          service: 'node'
          host_type: 'clickhouse-host'

  # Prometheus 自身的健康监控
  - job_name: 'prometheus'
    static_configs:
      - targets: ['localhost:9090']
```

关键参数说明：
- `scrape_interval: 15s` — 生产环境经典值。太短（<10s）会增加 Prometheus 内存和 CPU 开销，尤其是当 ClickHouse 暴露 300+ 指标时；太长（>60s）则监控灵敏度不足，延迟告警可能滞后几分钟
- `external_labels` — 为所有指标附加集群标识，多集群部署时防止指标混淆

重启 Prometheus 后，访问 `http://localhost:9090/targets` 确认所有采集目标状态为绿色 **UP**。

### Step 3: 配置告警规则

```yaml
# prometheus/rules.yml
groups:
  - name: clickhouse_critical
    interval: 30s
    rules:
      # 磁盘使用率 > 85%
      - alert: ClickHouseDiskAlmostFull
        expr: |
          (1 - ClickHouseAsyncMetrics_DiskAvailable_default 
              / ClickHouseAsyncMetrics_DiskTotal_default) > 0.85
        for: 5m
        labels:
          severity: critical
          team: data-infra
        annotations:
          summary: "ClickHouse 磁盘使用率超过 85%"
          description: >
            节点 {{ $labels.instance }} 磁盘使用率已达到 
            {{ $value | humanizePercentage }}，当前可用空间
            {{ printf "ClickHouseAsyncMetrics_DiskAvailable_default" | query | first | value | humanize1024 }}。
            请立即清理旧数据或扩容磁盘。

      # 副本延迟 > 60 秒
      - alert: ClickHouseReplicationLagHigh
        expr: ClickHouseAsyncMetrics_ReplicasMaxAbsoluteDelay > 60
        for: 5m
        labels:
          severity: critical
          team: data-infra
        annotations:
          summary: "ClickHouse 副本复制延迟超过 60 秒"
          description: >
            节点 {{ $labels.instance }} 最大副本延迟为 {{ $value }} 秒。
            请检查网络连接和 ZooKeeper/Keeper 状态。

  - name: clickhouse_warning
    interval: 30s
    rules:
      # Merge/Mutation 积压 > 100
      - alert: ClickHouseMergeBacklogHigh
        expr: ClickHouseMetrics_BackgroundPoolTask > 100
        for: 10m
        labels:
          severity: warning
          team: data-infra
        annotations:
          summary: "ClickHouse Merge 任务积压超过 100"
          description: >
            节点 {{ $labels.instance }} 后台任务积压数为 {{ $value }}。
            可能原因：高频写入、大量 Mutation、磁盘 IO 瓶颈。

      # 内存使用 > 200GB
      - alert: ClickHouseHighMemoryUsage
        expr: ClickHouseMetrics_MemoryTracking / 1024 / 1024 / 1024 > 200
        for: 5m
        labels:
          severity: warning
          team: data-infra
        annotations:
          summary: "ClickHouse 内存使用超过 200GB"
          description: >
            节点 {{ $labels.instance }} 当前内存使用 {{ $value }}GB。
            请检查是否有大查询正在运行。

      # 查询错误率 > 10/min
      - alert: ClickHouseQueryErrorRateHigh
        expr: rate(ClickHouseProfileEvents_FailedQuery[5m]) * 60 > 10
        for: 5m
        labels:
          severity: warning
          team: data-infra
        annotations:
          summary: "ClickHouse 查询错误率超过每分钟 10 次"
          description: >
            节点 {{ $labels.instance }} 过去 5 分钟内每分钟平均失败查询 
            {{ $value }} 次。

      # 查询 P99 延迟 > 10 秒
      - alert: ClickHouseQueryLatencyP99High
        expr: ClickHouseAsyncMetrics_QueryLatency{quantile="0.99"} > 10
        for: 10m
        labels:
          severity: warning
          team: data-infra
        annotations:
          summary: "ClickHouse P99 查询延迟超过 10 秒"
          description: "节点 {{ $labels.instance }} P99 延迟 {{ $value }} 秒。"
```

告警规则设计的几条原则：
1. **`for` 子句防止抖动**：`for: 5m` 意味着指标必须**持续**超过阈值 5 分钟才会触发告警，避免短暂的尖峰造成告警风暴
2. **`severity` 分级**：critical 触发即时通知（电话/PagerDuty），warning 触发群消息（钉钉/飞书/Slack），info 仅记录不通知
3. **`annotations` 必须包含可操作信息**：好的告警描述让接收者不看仪表盘就能判断严重程度和下一步动作

### Step 4: Grafana 仪表盘设计

Grafana 启动后登录 `http://localhost:3000`（用户名 `admin`、密码 `admin`），首先配置 Prometheus 数据源：`Configuration → Data Sources → Add data source → Prometheus`，URL 填 `http://prometheus:9090`。

以下设计 **7 个核心面板**，覆盖集群健康 → 查询性能 → 存储状态 → 复制健康全链路：

**仪表盘 JSON 可直接通过 Grafana 导入**，下面给出每个面板的 PromQL/SQL 和可视化建议：

#### Panel 1: 集群 QPS（集群概览）

```promql
# PromQL — 每秒查询速率
sum(rate(ClickHouseProfileEvents_Query[1m]))
```

- 图表类型：**Time series**（折线图）
- 配色：梯度蓝色填充区域，突出趋势
- Legend：`{{`instance`}}` 按节点分线

#### Panel 2: 查询延迟分位数（Query Performance）

```promql
# P50
ClickHouseAsyncMetrics_QueryLatency{quantile="0.5"}
# P95
ClickHouseAsyncMetrics_QueryLatency{quantile="0.95"}
# P99
ClickHouseAsyncMetrics_QueryLatency{quantile="0.99"}
```

- 图表类型：**Time series**，三条线分别代表 P50 / P95 / P99，颜色由浅到深
- Y 轴单位：`seconds (s)`
- 添加阈值线：P95 > 5s 显示为黄色虚线，P99 > 10s 显示为红色虚线

#### Panel 3: Merge/Mutation 积压（存储状态）

```promql
# 当前积压任务数
ClickHouseMetrics_BackgroundPoolTask
```

- 图表类型：**Stat**（数值卡片）+ **Time series** 双层展示
- 颜色阈值：0-50 绿色，50-100 黄色，100+ 红色
- 卡片值设为最新值（`last()`），折线图展示最近 6 小时趋势

#### Panel 4: 内存使用（存储状态）

```promql
# 内存使用量（GB）
ClickHouseMetrics_MemoryTracking / 1024 / 1024 / 1024
```

- 图表类型：**Gauge**（仪表盘），上下限设为 0 和物理内存总量
- 阈值：0-70% 绿色，70-85% 黄色，85-100% 红色

如果启用 Node Exporter，还可以叠加主机内存数据：

```promql
# 主机总内存（GB）
node_memory_MemTotal_bytes{job="node-exporter"} / 1024 / 1024 / 1024
# 主机可用内存（GB）
node_memory_MemAvailable_bytes{job="node-exporter"} / 1024 / 1024 / 1024
```

#### Panel 5: 磁盘使用率（存储状态）

```promql
# 磁盘使用率（百分比）
(1 - ClickHouseAsyncMetrics_DiskAvailable_default 
    / ClickHouseAsyncMetrics_DiskTotal_default) * 100
```

- 图表类型：**Gauge** 或 **Bar gauge**（堆叠条）
- 阈值：85% 黄色警告线，95% 红色危险线

#### Panel 6: 副本延迟（Replication Health）

```promql
# 最大副本绝对延迟（秒）
ClickHouseAsyncMetrics_ReplicasMaxAbsoluteDelay
```

- 图表类型：**Time series**
- Y 轴：对数坐标（`log scale`），因为延迟从毫秒到分钟跨越多个数量级
- 阈值叠加：60 秒红色虚线

#### Panel 7: Top 10 慢查询（Query Detail）

这个面板不通过 Prometheus，而是直接查询 ClickHouse 的 `system.query_log`。先在 Grafana 中安装并配置 **Altinity ClickHouse 数据源插件**（已在 `docker-compose.yml` 中通过环境变量自动安装），然后添加 ClickHouse 数据源，URL 填 `http://ch28-clickhouse:8123`。

```sql
SELECT
    query_start_time AS time,
    formatReadableTimeDelta(query_duration_ms / 1000) AS duration,
    substring(query, 1, 80) AS query_preview,
    read_rows,
    read_bytes,
    user
FROM system.query_log
WHERE type = 'QueryFinish'
  AND query_duration_ms > 5000
  AND query_start_time >= now() - INTERVAL 1 HOUR
ORDER BY query_duration_ms DESC
LIMIT 10
```

- 图表类型：**Table**（表格面板）
- 列格式：`duration` 设为字符串，`read_rows` 和 `read_bytes` 开启数字缩写
- 适用场景：每天早上打开看一眼昨晚的慢查询，针对性优化

### Step 5: 异步指标日志 — 历史趋势回溯

Prometheus 默认保留 15-30 天的数据，更久远的历史指标查询需要 `asynchronous_metric_log` 补齐：

```xml
<!-- 在 config.d 中新增 async_metric_log.xml -->
<clickhouse>
    <asynchronous_metric_log>
        <database>system</database>
        <table>asynchronous_metric_log</table>
        <flush_interval_milliseconds>60000</flush_interval_milliseconds>
        <ttl>event_time + INTERVAL 90 DAY</ttl>
    </asynchronous_metric_log>
</clickhouse>
```

参数说明：
- `flush_interval_milliseconds` — 内存缓冲区刷入磁盘的频率。默认 1000ms（1秒），生产环境建议 60000ms（1分钟），减少小文件写入
- `<ttl>` — 数据自动清理策略，这里设 90 天自动删除，防止磁盘被打满

配置生效后查询历史趋势：

```sql
-- 查看过去 24 小时每秒 QPS 变化
SELECT
    event_time,
    avg(value) AS avg_value
FROM system.asynchronous_metric_log
WHERE metric_name = 'Query'
  AND event_time >= now() - INTERVAL 1 DAY
GROUP BY event_time
ORDER BY event_time;

-- 查看过去 7 天每天最大内存使用量
SELECT
    toDate(event_time) AS day,
    formatReadableSize(max(value)) AS peak_memory
FROM system.asynchronous_metric_log
WHERE metric_name = 'MemoryTracking'
  AND event_time >= now() - INTERVAL 7 DAY
GROUP BY day
ORDER BY day;
```

> **对比**：Prometheus 解决"最近 15 天，每 15 秒一个数据点"的实时监控问题；`asynchronous_metric_log` 解决"最近 90 天，每分钟一个数据点"的历史分析问题。两者是互补关系，不是替代关系。

### Step 6: 告警通知配置

告警规则计算完成后，怎么把通知发出去？需要配置 **AlertManager**：

```yaml
# alertmanager/alertmanager.yml
global:
  resolve_timeout: 5m

route:
  group_by: ['alertname', 'severity']
  group_wait: 10s          # 收到第一个告警后等 10 秒，收集同组其他告警一起发送
  group_interval: 10s      # 同组新告警的发送间隔
  repeat_interval: 1h      # 未恢复的告警每小时重复提醒一次
  receiver: 'default'
  routes:
    - match:
        severity: critical
      receiver: 'pagerduty'
      repeat_interval: 30m
    - match:
        severity: warning
      receiver: 'dingtalk-webhook'

receivers:
  - name: 'default'
    webhook_configs:
      - url: 'http://your-webhook/slack'

  - name: 'dingtalk-webhook'
    webhook_configs:
      - url: 'https://oapi.dingtalk.com/robot/send?access_token=YOUR_TOKEN'
        send_resolved: true  # 告警恢复后也发送通知

  - name: 'pagerduty'
    pagerduty_configs:
      - routing_key: 'YOUR_PAGERDUTY_KEY'
```

`group_by: ['alertname', 'severity']` 是关键——它防止告警风暴。假设 8 台 ClickHouse 节点同时磁盘满了，如果没有 `group_by`，你会收到 8 条独立告警；加上 `group_by` 后，8 条合并成 **1 条通知**，内容里列出所有受影响节点。

### 测试验证

逐个验证监控链路：

```bash
# 1. 验证 ClickHouse 端点
curl -s http://localhost:9363/metrics | grep ClickHouseMetrics_MemoryTracking

# 2. 验证 Prometheus 采集
curl -s http://localhost:9090/api/v1/query?query=ClickHouseMetrics_MemoryTracking | jq '.data.result'

# 3. 验证告警规则加载
curl -s http://localhost:9090/api/v1/rules | jq '.data.groups[].name'

# 4. 模拟高内存场景，验证告警触发
# 在 ClickHouse 中执行一条大内存查询
docker exec ch28-clickhouse clickhouse-client -q "
  SELECT sum(number * number) FROM numbers(500000000)
  SETTINGS max_memory_usage = 300000000000  -- 允许 300GB
"
# 稍后访问 http://localhost:9090/alerts 查看告警状态

# 5. 访问 Grafana
echo "Grafana: http://localhost:3000 (admin/admin)"
echo "Prometheus: http://localhost:9090"
echo "ClickHouse Metrics: http://localhost:9363/metrics"
```

---

## 4. 项目总结

### 监控指标分层

一个完整的 ClickHouse 监控体系应当覆盖四个层次：

| 层次 | 关注指标 | 采集工具 | 数据源 |
|------|---------|---------|--------|
| **基础设施层** | CPU / 内存 / 磁盘 IO / 网络吞吐 | Node Exporter (9100) | Prometheus |
| **引擎层** | QPS / P95 延迟 / 错误率 / Merge 积压 / 内存使用 | ClickHouse (9363) | Prometheus |
| **查询层** | 慢查询详情 / 查询模式分析 / 异常用户 | system.query_log | Grafana + ClickHouse 数据源 |
| **业务层** | GMV / 订单数 / 活跃用户（自定义指标） | 自定义写入 `system.metrics` | Prometheus / Grafana |

### RED 方法在 ClickHouse 监控中的落地

- **R（Rate）**：`rate(ClickHouseProfileEvents_Query[1m])` → QPS；`rate(ClickHouseProfileEvents_InsertedRows[1m])` → 写入速率
- **E（Errors）**：`rate(ClickHouseProfileEvents_FailedQuery[5m])` → 查询错误率；`ClickHouseAsyncMetrics_ReplicasMaxAbsoluteDelay > 60` → 复制异常
- **D（Duration）**：`ClickHouseAsyncMetrics_QueryLatency{quantile="0.95"}` / `{quantile="0.99"}` → 延迟分位数

### 适用场景

- **生产集群日常巡检**：每天打开 Grafana 仪表盘，30 秒扫完 QPS / 延迟 / 磁盘 / Merge 四大核心面板
- **容量规划**：通过 Prometheus 的 `range query` 分析过去 30 天的磁盘增长趋势，预估扩容时间点；通过 `asynchronous_metric_log` 做季度、年度趋势分析
- **故障应急响应**：告警触发 → AlertManager 推送通知 → 值班人员打开 Grafana 对应面板定位根因（是慢查询拖垮？是 Merge 风暴？还是磁盘满了？）
- **上线前后对比**：新版本上线前采集 1 周基线数据，上线后对比 P95 延迟和 CPU 使用率，量化版本升级效果

### 注意事项

1. **Prometheus 采集间隔不要设置太短**。当 ClickHouse 节点数超过 20 台、每台暴露 300+ 指标时，15s 的采集间隔意味着 Prometheus 每 15 秒要处理 6000+ 条指标，对内存和 CPU 都是压力。如果集群规模较大，考虑调整为 30s 或 60s，或者启用 `metric_relabel_configs` 过滤掉不需要的指标。

2. **告警阈值需要按环境调优**。`BackgroundPoolTask > 100` 这条规则在写入量 1000 行/秒的环境下是合理的，但在写入量 10 万行/秒的集群里可能常年触发。每个集群上线监控的第一周是阈值校准期——观察指标的正常波动范围，把告警线划在"正常峰值 × 1.5"的位置。

3. **ClickHouse 指标端点是单节点粒度**，Prometheus 用 `instance` 标签区分。设计 Grafana 面板时必须考虑是展示单节点（用 `instance` 变量选择器）还是集群聚合（用 `sum/max/min/avg` 聚合函数）。

4. **`asynchronous_metric_log` 会对 ClickHouse 自身产生额外负载**。它本身是一张 MergeTree 表，每秒钟产生一行 INSERT 就是一次写入操作——后台 Merge 也在消耗这台机器的资源。对于资源紧张的节点，考虑将 `flush_interval_milliseconds` 从 1000ms 调整到 30000ms 或更高。

### 常见踩坑经验

1. **Prometheus 内存不足**。ClickHouse 暴露的指标数量庞大（300+），加上高基数标签（如 `query_id`、`table_name`），Prometheus 的时序内存指数级增长。解决方法：(a) 在 `prometheus.yml` 中用 `metric_relabel_configs` 的 `drop` 动作过滤掉不需要的指标；(b) 用 `labeldrop` 删除高基数标签；(c) 缩短 `--storage.tsdb.retention.time` 保留时间。

2. **Grafana ClickHouse 数据源插件版本不兼容**。Altinity 和 Vertamedia 两款插件经常因为新版 ClickHouse API 变更而报错。官方推荐优先用 Altinity 插件（更新频率更高），并且在 `docker-compose.yml` 中锁定插件版本号：`GF_INSTALL_PLUGINS=vertamedia-clickhouse-datasource:2.5.3`。

3. **告警规则太敏感导致告警风暴**。一条 `BackgroundPoolTask` 刚触碰到 101 就发告警，5 分钟后掉回 98——告警恢复——再过 3 分钟涨到 102——又告警。这叫"告警抖动"。解决方法：增大 `for` 子句的持续时间（如从 5m 改为 15m），同时适当提高阈值（如从 100 改为 200）。

4. **磁盘使用率告警忽略了多磁盘场景**。`DiskAvailable_default` 只反映名为 `default` 的存储策略的磁盘。如果你的 ClickHouse 配置了多种存储策略（如 `hot` SSD + `cold` HDD），需要按策略名分别监控：`ClickHouseAsyncMetrics_DiskAvailable_{policy_name}`。

### 思考题

1. RED 方法（Rate, Errors, Duration）在 ClickHouse 监控中如何落地？尝试为你自己的 ClickHouse 集群设计三张告警卡片，分别对应 RED 三个维度。每个维度的阈值如何根据业务场景设定？（提示：一个实时看板集群和一个离线 ETL 集群的 P95 延迟容忍度完全不同）

2. 如何设计一套 SLI/SLO 体系来衡量 ClickHouse 集群的服务质量？以"查询成功率 ≥ 99.9%"和"P99 查询延迟 ≤ 3s"为例，计算你需要定义哪些 SLI（Service Level Indicator），以及如何用 PromQL 实现这些 SLI 的持续计算和告警。

---

*下一章预告：分布式表与集群拓扑——横向扩展的秘密。*
