# 第28章：可观测性体系——Prometheus + Grafana + 分布式追踪

## 1 项目背景

某电商平台的数据中台团队管理着 300+ 条 Dag、日均 8 万次 TaskInstance 调度、覆盖 40 个下游系统。某天下午 4 点，CTO 在管理群发了一条消息："为什么今天的 GMV 大屏数据还是昨天下午的？"

运维组长立刻登录 Airflow Web UI 排查——触目惊心的景象：58 条 Dag 处于 `running` 状态但已经卡了 40 分钟以上，12 条 Dag 的 Task 批量处于 `queued` 状态，Celery Worker 的 CPU 全部打满。更致命的是，团队无法回答 CTO 最关心的问题：

1. **这些问题是从什么时候开始的？**——没有趋势图，只能靠人肉翻 TaskInstance 表。
2. **根因是什么？是 Celery Broker 堵塞还是数据库连接池耗尽？**——没有组件级指标，靠猜。
3. **为什么告警没有触发？**——告警规则只配了"任务失败发钉钉"，任务卡在 running 并不是"失败"，所以没有任何通知。

这个场景暴露了典型的可观测性缺口。Airflow 本身是一个分布式系统——Scheduler、Worker、Dag Processor、Triggerer、Metadata DB、Celery Broker 等多个组件在不同进程中运行。**单点查看 Web UI 或查询数据库，就像闭着眼睛摸大象**——你只能摸到一个局部，永远看不到全貌。

一个成熟的可观测性体系需要覆盖"三大信号"（Three Pillars of Observability）：

| 信号 | 回答的问题 | Airflow 中的载体 | 工具链 |
|------|-----------|----------------|--------|
| **Metrics（指标）** | "系统现在健康吗？" | StatsD → 调度吞吐、任务状态、Pool 槽位 | Prometheus + Grafana |
| **Traces（追踪）** | "一个请求经历了哪些组件？" | OpenTelemetry → Task 全生命周期 | Jaeger / Grafana Tempo |
| **Logs（日志）** | "某个时刻发生了什么？" | 任务日志 + 组件日志 | Fluentd → ES → Kibana |

本章将围绕这三根支柱，构建一套生产级 Airflow 可观测性体系：从 StatsD 指标暴露到 Prometheus 抓取，从 Grafana 大盘设计到核心告警规则，再到 OpenTelemetry 分布式追踪与 ELK 日志聚合。

> **一句话总结**：如果你只能看到 Dag 的"成功/失败"，那么你只在监控"结果"；可观测性体系让你监控"过程"——调度延迟、排队时间、资源饱和度、链路瓶颈，这些才是线上问题的真正根因。

---

## 2 项目设计

**小胖**（盯着 Web UI 发呆）："大师，我看 Airflow 自带那个 DagRun 的甘特图（Gantt Chart）不是挺好看的吗？每个任务跑了多久一目了然，为什么还要搞 Prometheus + Grafana 这么重的东西？"

**大师**："你还记得上周五下午的事故吗？GMV 大屏延迟 6 小时那个。"

**小胖**："记得啊，后来复盘发现是一个 Hive 任务跑了 3 小时把 Celery 队列堵死了。"

**大师**："那你是在第几分钟发现队列开始堵塞的？"

**小胖**："呃……事故复盘的时候翻数据库才确认的，大概是 14:15 左右。"

**大师**："这就是问题所在——你靠的是事后翻数据库，而不是事前看趋势。Grafana 大盘可以让你看到一条`队列深度`的实时曲线，在它从 5 涨到 10 的时候就收到告警，而不是等到队列堆满 200 个任务才去救火。甘特图看的是单个 DagRun 的历史，Grafana 看的是整个集群的脉搏。**一个看树木，一个看森林。**"

**小白**："但是 Metrics、Traces、Logs 三套体系是不是太复杂了？小团队有必要全上吗？"

**大师**："好问题。我们的原则是——**按成熟度渐进演进**。"

```
Level 0: 裸奔      → 只在 Web UI 看状态
Level 1: 告警      → 回调函数发钉钉/邮件（第 14 章已实现）
Level 2: 指标      → Prometheus + Grafana，掌握集群宏观健康度
Level 3: 追踪      → OpenTelemetry，定位跨组件延迟瓶颈
Level 4: 全态      → Metrics + Traces + Logs 三合一，TraceID 串联
```

**大师**："对于日均 100+ Dag 的团队，Level 2 是投入产出比最高的——Prometheus + Grafana 的部署成本极低，但能回答 80% 的线上问题。Level 3 以上是在你有跨服务调用链（比如 Task 执行过程中调了 5 个微服务）时才需要。"

**小白**："那 Airflow 本身的指标是怎么暴露出来的？它又不是一个 Web 服务，怎么让 Prometheus 抓？"

**大师**："Airflow 用的是 **StatsD 协议**。你可以把 StatsD 想象成一个'指标邮局'——Airflow 的各个组件（Scheduler、Dag Processor、Worker）不断往这个邮局寄明信片（UDP datagram），每张明信片上写着：`airflow.ti.success:1|c`（意为'task 成功次数 +1，类型是 counter'）。StatsD 服务端收到后，计算成每秒速率、百分位数等，再暴露给 Prometheus 抓取。"

**小胖**："那 StatsD 和 `airflow-prometheus-exporter` 是什么关系？我搜到有个项目叫 `apache-airflow-providers-prometheus`。"

**大师**："两条路径，各有优劣。"

| 方案 | 原理 | 优点 | 缺点 |
|------|------|------|------|
| **StatsD Exporter** | 部署 `prom/statsd-exporter` 独立进程，监听 UDP:8125，翻译 StatsD → Prometheus 格式 | 不侵入 Airflow 代码；所有 StatsD 客户端通用；UDP 无网络开销 | 需要额外进程；UDP 丢包风险 |
| **Prometheus Provider** | 在 Airflow 内部安装 Provider，直接暴露 `/metrics` HTTP 端点 | 单一端口；不丢指标；与 Airflow 同生命周期 | 侵入 Airflow 容器；额外 HTTP 端口；需要 Provider 维护 |

**大师**："生产环境我推荐路径一——StatsD Exporter 独立部署。因为：第一，独立进程的故障隔离不会影响 Airflow 核心调度；第二，UDP 丢包率在容器网络中极低（< 0.01%），对趋势监控影响可忽略；第三，你未来的 Kafka Connector、Flink 任务也可以往同一个 StatsD Exporter 发指标，统一收口。"

**小胖**："那 Grafana 大盘要怎么设计？总不能把所有指标堆在一个页面里吧？"

**大师**："用两个经典方法论——**RED 方法**（面向请求/任务）、**USE 方法**（面向资源/组件）。我把 Airflow 监控分为四块大盘。"

**大师铺开一张纸画出表：**

**大盘一：Airflow Overview（集群概览）—— RED 方法**

| 面板 | PromQL 查询 | 含义 |
|------|------------|------|
| 任务执行速率 | `rate(airflow_ti_success_total[5m]) + rate(airflow_ti_failure_total[5m])` | 每分钟完成多少任务 |
| 任务失败率 | `rate(airflow_ti_failure_total[5m]) / (rate(airflow_ti_success_total[5m]) + rate(airflow_ti_failure_total[5m])) * 100` | 失败比例，> 5% 需关注 |
| 任务 P95 执行时长 | `histogram_quantile(0.95, rate(airflow_ti_duration_seconds_bucket[5m]))` | 95% 的任务多久跑完 |
| DagRun 状态分布 | `sum by (state) (airflow_dagrun_state)` | running/success/failed 的数量分布 |

**大盘二：Scheduler Health（调度器健康）—— USE 方法**

| 面板 | PromQL 查询 | 含义 |
|------|------------|------|
| Scheduler 心跳延迟 | `time() - airflow_scheduler_heartbeat` | 距上次心跳的秒数，>30s 为异常 |
| Dag 解析耗时 P95 | `histogram_quantile(0.95, rate(airflow_dag_processing_total_parse_time_seconds_bucket[5m]))` | 单 Dag 解析耗时 |
| Scheduler 循环耗时 | `rate(airflow_scheduler_critical_section_duration_seconds_sum[5m]) / rate(airflow_scheduler_critical_section_duration_seconds_count[5m])` | 每个调度循环平均耗时 |
| 队列中等待任务数 | `airflow_scheduler_tasks_queued` | 当前排队中的 TaskInstance 数量 |

**大盘三：Pool & Resources（资源水位）—— USE 方法**

| 面板 | PromQL 查询 | 含义 |
|------|------------|------|
| Pool 槽位占用率 | `(airflow_pool_used_slots / airflow_pool_total_slots) * 100` | 按 pool 分组，观察哪些 Pool 接近打满 |
| Celery 队列深度 | `airflow_celery_queue_length` | Broker 中待消费的消息数 |
| Worker 数变化 | `count(airflow_celery_workers)` | 在线 Worker 数量，突降说明 Worker 崩溃 |

**大盘四：Dag Details（Dag 维度下钻）—— RED 方法**

| 面板 | PromQL 查询 | 含义 |
|------|------------|------|
| Top 10 失败 Dag | `topk(10, rate(airflow_ti_failure_total[1h]))` | 按 Dag 聚合，找到"故障王" |
| Top 10 慢 Dag | `topk(10, histogram_quantile(0.95, rate(airflow_ti_duration_seconds_bucket[1h])))` | 耗时最长的 Dag |
| Dag 成功趋势 | `rate(airflow_ti_success_total{dag_id=~"$dag_id"}[5m])` | 单个 Dag 在 Grafana 变量中选中 |

**小白**："那告警规则呢？总不能指标一抖动就告警吧？"

**大师**："告警设计的核心是**信号比噪音**。每条告警收到后，接收者能否在 3 分钟内做出明确的行动决策？如果能，就是有效告警；如果不能（比如'CPU 超过 80%'——然后呢？重启？加机器？没人知道），就是噪音。"

**小白**："所以核心告警有多少条？"

**大师**："——**5 条就够了**。覆盖最常见的 5 种故障模式。"

| # | 告警名 | PromQL 规则 | 阈值 | 影响 |
|---|--------|------------|------|------|
| 1 | **Dag 失败率过高** | `rate(airflow_ti_failure_total[5m]) / rate(airflow_ti_success_total[5m] + airflow_ti_failure_total[5m]) > 0.05` | > 5% | 下游数据延迟、报表缺失 |
| 2 | **任务排队时间过长** | `avg(airflow_scheduler_tasks_queued) > 600` | > 10min | Worker 不足或 Broker 堵塞 |
| 3 | **Scheduler 心跳超时** | `time() - airflow_scheduler_heartbeat > 120` | > 120s | 调度器可能宕机，无人创建 DagRun |
| 4 | **Pool 槽位耗尽** | `(airflow_pool_used_slots / airflow_pool_total_slots) > 0.9` | > 90% | 该 Pool 的任务将无法调度 |
| 5 | **Dag 解析耗时过长** | `histogram_quantile(0.95, rate(airflow_dag_processing_total_parse_time_seconds_bucket[5m])) > 30` | P95 > 30s | Dag 文件可能有性能问题或死循环 |

> **技术映射**：StatsD = 社区邮筒（各组件往里面投明信片）；Prometheus = 邮局分拣机器（定时去邮筒收信、分类、计数）；Grafana = 邮局大厅的数据大屏；告警规则 = 保安巡逻发现异常立刻吹哨；OpenTelemetry = GPS 追踪器（贴在每个包裹上，全程记录经过了哪个分拣中心、耗时多久）。

---

## 3 项目实战

### 3.1 环境准备

**目标**：使用 Docker Compose 快速搭建 StatsD + Prometheus + Grafana 监控栈，与 Airflow 对接。

**核心组件版本**：

| 组件 | 镜像 | 版本 | 用途 |
|------|------|------|------|
| Airflow | `apache/airflow` | 3.x | 被监控目标 |
| StatsD Exporter | `prom/statsd-exporter` | v0.26.1 | StatsD → Prometheus 翻译层 |
| Prometheus | `prom/prometheus` | v2.52.0 | 指标抓取与存储 |
| Grafana | `grafana/grafana` | 10.4.0 | 可视化大盘与告警 |

### 3.2 配置 Airflow StatsD 暴露

**步骤目标**：启用 Airflow 的 StatsD 指标上报。

在 `airflow.cfg` 中添加：

```ini
[metrics]
# 启用 StatsD
statsd_on = True
statsd_host = statsd-exporter
statsd_port = 8125
statsd_prefix = airflow

# 可选：指标发送间隔（秒）
metrics_allow_list = scheduler,ti,dag_processing,pool
```

**Airflow 3.x 内置 StatsD 指标清单**（关键子集）：

| 指标类 | 指标名 | 类型 | 含义 |
|--------|--------|------|------|
| `scheduler` | `airflow.scheduler.tasks.running` | Gauge | 当前 running 的任务数 |
| `scheduler` | `airflow.scheduler.tasks.scheduled` | Gauge | 当前 queued 的任务数 |
| `scheduler` | `airflow.scheduler.heartbeat` | Gauge | 调度器心跳时间戳 |
| `scheduler` | `airflow.scheduler.critical_section_duration` | Timer | 调度循环关键段耗时 |
| `ti` | `airflow.ti.start.{dag_id}.{task_id}` | Counter | task 开始执行 |
| `ti` | `airflow.ti.finish.{dag_id}.{task_id}.{state}` | Counter | task 结束 + 最终状态 |
| `ti` | `airflow.ti.failure.{dag_id}.{task_id}` | Counter | task 失败 |
| `ti` | `airflow.ti.success.{dag_id}.{task_id}` | Counter | task 成功 |
| `ti` | `airflow.ti.duration.{dag_id}.{task_id}` | Timer | task 执行耗时 |
| `dag_processing` | `airflow.dag_processing.total_parse_time` | Timer | Dag 解析总耗时 |
| `dag_processing` | `airflow.dag_processing.last_duration.{file_name}` | Gauge | 单个 Dag 文件解析耗时 |
| `pool` | `airflow.pool.open_slots.{pool_name}` | Gauge | Pool 可用槽位数 |
| `pool` | `airflow.pool.used_slots.{pool_name}` | Gauge | Pool 已占用槽位数 |

这些指标覆盖了 Airflow 核心组件的健康状态。如果在 Web UI 中看不到这些指标，可以用 `nc -lu 8125` 在本机监听 UDP 包来确认 Airflow 是否在正常发送。

### 3.3 配置 StatsD Exporter

**步骤目标**：将 StatsD 指标翻译为 Prometheus 可抓取的格式。

创建 `statsd/statsd.conf`：

```yaml
# StatsD → Prometheus 指标映射配置
mappings:
  # 任务状态计数器 -> counter 类型
  - match: "airflow.ti.success.*.*"
    name: "airflow_ti_success_total"
    match_metric_type: counter
    labels:
      dag_id: "$1"
      task_id: "$2"

  - match: "airflow.ti.failure.*.*"
    name: "airflow_ti_failure_total"
    match_metric_type: counter
    labels:
      dag_id: "$1"
      task_id: "$2"

  # 任务执行耗时 -> histogram
  - match: "airflow.ti.duration.*.*"
    name: "airflow_ti_duration_seconds"
    match_metric_type: timer
    labels:
      dag_id: "$1"
      task_id: "$2"
    timer_type: histogram
    buckets: [1, 5, 10, 30, 60, 120, 300, 600, 1800, 3600]

  # 调度器心跳 -> 减法计算延迟
  - match: "airflow.scheduler.heartbeat"
    name: "airflow_scheduler_heartbeat"
    match_metric_type: gauge

  # 调度循环耗时 -> summary
  - match: "airflow.scheduler.critical_section_duration"
    name: "airflow_scheduler_critical_section_duration_seconds"
    match_metric_type: timer
    timer_type: summary

  # Pool 槽位 -> gauge（保留 pool_name 标签）
  - match: "airflow.pool.used_slots.*"
    name: "airflow_pool_used_slots"
    match_metric_type: gauge
    labels:
      pool_name: "$1"

  - match: "airflow.pool.open_slots.*"
    name: "airflow_pool_open_slots"
    match_metric_type: gauge
    labels:
      pool_name: "$1"

  # 全局匹配兜底——所有 metrics 都保留原始名
  - match: ".*"
    match_type: regex
    name: "airflow_${0}"
    match_metric_type: gauge
```

**常见踩坑**：StatsD Exporter 的 `mappings` 是**从上到下匹配**，第一个命中即停止。所以务必把精确匹配放在前面，通配规则 `.*` 放在最后。如果反向放，所有指标都会被兜底规则捕获，前面的规则永不生效。

### 3.4 配置 Prometheus

**步骤目标**：让 Prometheus 定时抓取 StatsD Exporter 的 `/metrics` 端点并加载告警规则。

创建 `prometheus/prometheus.yml`：

```yaml
global:
  scrape_interval: 15s        # 每 15 秒抓取一次
  evaluation_interval: 15s    # 每 15 秒评估一次告警规则
  external_labels:
    cluster: 'airflow-production'
    env: 'prod'

# 加载告警规则
rule_files:
  - "/etc/prometheus/airflow_alerts.yml"

# 抓取目标
scrape_configs:
  # StatsD Exporter
  - job_name: 'airflow-statsd'
    static_configs:
      - targets: ['statsd-exporter:9102']
        labels:
          component: 'airflow'

  # （可选）Airflow 自身的 Prometheus Exporter
  - job_name: 'airflow-direct'
    static_configs:
      - targets: ['airflow-webserver:8080']
        metrics_path: '/metrics'
```

### 3.5 配置 5 条核心告警规则

**步骤目标**：在 Prometheus 中定义生产级的告警规则。

创建 `alert_rules/airflow_alerts.yml`：

```yaml
groups:
  - name: airflow_critical
    interval: 30s
    rules:
      # ===== 规则 1：Dag 失败率过高 =====
      - alert: AirflowHighDagFailureRate
        expr: |
          (
            rate(airflow_ti_failure_total[5m])
            /
            (
              rate(airflow_ti_success_total[5m])
              + rate(airflow_ti_failure_total[5m])
            )
          ) > 0.05
        for: 5m
        labels:
          severity: critical
          team: data-platform
        annotations:
          summary: "Airflow 任务失败率超过 5%"
          description: |
            过去 5 分钟任务失败率为 {{ $value | humanizePercentage }}。
            当前失败数：{{ range query "rate(airflow_ti_failure_total[5m])" }}
              {{ . | value | printf "%.2f" }}/s
            {{ end }}
          runbook_url: "https://wiki.internal/airflow/runbooks/high-failure-rate"

      # ===== 规则 2：任务排队时间 > 10 分钟 =====
      - alert: AirflowTaskQueueLongWait
        expr: |
          avg(airflow_scheduler_tasks_scheduled) > 600
        for: 10m
        labels:
          severity: warning
          team: data-platform
        annotations:
          summary: "Airflow 任务排队等待时间超过 10 分钟"
          description: |
            当前排队任务数：{{ $value }}。
            请检查 Worker 数量、Celery Broker 队列深度、Pool 槽位是否打满。
          runbook_url: "https://wiki.internal/airflow/runbooks/long-queue"

      # ===== 规则 3：Scheduler 心跳超时 =====
      - alert: AirflowSchedulerHeartbeatMissing
        expr: |
          (time() - airflow_scheduler_heartbeat) > 120
        for: 2m
        labels:
          severity: critical
          team: data-platform
        annotations:
          summary: "Airflow Scheduler 心跳超时（> 120 秒）"
          description: |
            调度器已 {{ $value }} 秒未发送心跳。
            可能原因：调度器进程崩溃、Metadata DB 连接中断、或机器高负载导致调度循环阻塞。
          runbook_url: "https://wiki.internal/airflow/runbooks/scheduler-down"

      # ===== 规则 4：Pool 槽位耗尽 =====
      - alert: AirflowPoolSlotsExhausted
        expr: |
          (
            airflow_pool_used_slots
            /
            (airflow_pool_used_slots + airflow_pool_open_slots)
          ) > 0.9
        for: 10m
        labels:
          severity: warning
          team: data-platform
        annotations:
          summary: "Airflow Pool {{ $labels.pool_name }} 槽位使用率超过 90%"
          description: |
            Pool `{{ $labels.pool_name }}` 槽位使用率 {{ $value | humanizePercentage }}。
            已用 {{ range query (printf "airflow_pool_used_slots{pool_name=\"%s\"}" $labels.pool_name) }}{{ . | value }}{{ end }}
            可用 {{ range query (printf "airflow_pool_open_slots{pool_name=\"%s\"}" $labels.pool_name) }}{{ . | value }}{{ end }}
          runbook_url: "https://wiki.internal/airflow/runbooks/pool-exhausted"

      # ===== 规则 5：Dag 解析耗时过长 =====
      - alert: AirflowDagParsingSlow
        expr: |
          histogram_quantile(
            0.95,
            rate(airflow_dag_processing_total_parse_time_seconds_bucket[15m])
          ) > 30
        for: 15m
        labels:
          severity: warning
          team: data-platform
        annotations:
          summary: "Airflow Dag 解析 P95 耗时超过 30 秒"
          description: |
            Dag 文件解析 P95 耗时 {{ $value }} 秒。
            请检查是否有 Dag 文件包含阻塞的顶层 import 或复杂的动态生成逻辑。
          runbook_url: "https://wiki.internal/airflow/runbooks/slow-dag-parsing"
```

**告警规则设计原则**：

1. **`for` 持续时间不可省略**：每个规则都带 `for` 子句（2-15 分钟），避免因瞬时抖动（如部署重启）产生的假告警。
2. **`runbook_url` 必填**：每条告警必须携带一个故障处理手册的链接，否则告警来了值班人员不知道做什么，等同于噪音。
3. **`annotations` 提供上下文**：描述字段中包含当前值、对照组、排查方向，让接收者不需要再打开 Grafana 就能初步判断。

### 3.6 配置 Grafana 大盘

**步骤目标**：创建 Airflow 核心监控大盘，连接 Prometheus 数据源。

**Grafana 数据源配置**（`grafana/datasources/prometheus.yaml`）：

```yaml
apiVersion: 1
datasources:
  - name: Prometheus
    type: prometheus
    url: http://prometheus:9090
    access: proxy
    isDefault: true
    editable: false
```

**启动监控栈**：

```bash
docker compose -f observability/docker-compose.monitoring.yaml up -d
```

**验证各组件**：

```bash
# 1. 验证 StatsD Exporter 正在接收 Airflow 指标
curl -s http://localhost:9102/metrics | grep airflow_ti_success_total

# 2. 验证 Prometheus 抓取正常（检查 target 状态）
curl -s http://localhost:9090/api/v1/targets | jq '.data.activeTargets[] | {job: .labels.job, health: .health}'

# 3. 验证 Prometheus 中有时序数据
curl -s "http://localhost:9090/api/v1/query?query=airflow_ti_success_total" | jq '.data.result | length'

# 4. 验证 Grafana 可访问
curl -s -o /dev/null -w "%{http_code}" http://localhost:3000

# 5. 手动触发一条告警规则验证
curl -s "http://localhost:9090/api/v1/query?query=ALERTS{alertname=\"AirflowHighDagFailureRate\"}" | jq '.'
```

**Grafana 大盘 JSON 模板**（核心面板节选）：

你可以手动在 Grafana UI 中创建，也可以导入 JSON 模板。以下是"Airflow 总览大盘"中关键面板的 JSON 片段：

```json
{
  "dashboard": {
    "title": "Airflow - Cluster Overview",
    "panels": [
      {
        "id": 1,
        "title": "Task Execution Rate (per second)",
        "type": "graph",
        "targets": [
          {
            "expr": "sum(rate(airflow_ti_success_total[5m])) + sum(rate(airflow_ti_failure_total[5m]))",
            "legendFormat": "Total Executions"
          },
          {
            "expr": "sum(rate(airflow_ti_success_total[5m]))",
            "legendFormat": "Success"
          },
          {
            "expr": "sum(rate(airflow_ti_failure_total[5m]))",
            "legendFormat": "Failure"
          }
        ],
        "fieldConfig": {
          "defaults": { "unit": "reqps" }
        }
      },
      {
        "id": 2,
        "title": "Task Failure Rate (%)",
        "type": "stat",
        "targets": [
          {
            "expr": "sum(rate(airflow_ti_failure_total[5m])) / (sum(rate(airflow_ti_success_total[5m])) + sum(rate(airflow_ti_failure_total[5m]))) * 100"
          }
        ],
        "fieldConfig": {
          "defaults": { "unit": "percent", "thresholds": { "steps": [
            { "value": 0, "color": "green" },
            { "value": 2, "color": "yellow" },
            { "value": 5, "color": "red" }
          ]}}
        }
      },
      {
        "id": 3,
        "title": "Task Duration P50 / P95 / P99",
        "type": "graph",
        "targets": [
          {
            "expr": "histogram_quantile(0.50, rate(airflow_ti_duration_seconds_bucket[5m]))",
            "legendFormat": "P50"
          },
          {
            "expr": "histogram_quantile(0.95, rate(airflow_ti_duration_seconds_bucket[5m]))",
            "legendFormat": "P95"
          },
          {
            "expr": "histogram_quantile(0.99, rate(airflow_ti_duration_seconds_bucket[5m]))",
            "legendFormat": "P99"
          }
        ],
        "fieldConfig": {
          "defaults": { "unit": "s" }
        }
      }
    ],
    "templating": {
      "list": [
        {
          "name": "dag_id",
          "type": "query",
          "datasource": "Prometheus",
          "query": "label_values(airflow_ti_duration_seconds_bucket, dag_id)",
          "multi": true,
          "includeAll": true
        }
      ]
    }
  }
}
```

**Grafana 告警通道配置**：在 Grafana 中配置 Contact Points → 添加钉钉/Slack Webhook，然后在 Notification Policies 中将 `severity=critical` 的路由到钉钉群，`severity=warning` 的路由到企业微信群。

### 3.7 分布式追踪：OpenTelemetry 集成

**步骤目标**：在 Task 执行过程中注入 TraceID，实现跨组件的全链路追踪。

当你的 Task 不只是执行 Python 脚本，而是调用多个微服务（如 Spark Submit API → Hive Metastore → Kafka Producer）时，就需要分布式追踪来定位"哪个环节慢了"。

**在 Airflow Task 中启用 OpenTelemetry**：

```python
"""
在 Airflow Dag 中启用分布式追踪（需安装 opentelemetry 包）
"""
from datetime import datetime, timedelta
from airflow.sdk import DAG
from airflow.sdk.operators.python import task

# OpenTelemetry 导入（需要 pip install opentelemetry-api opentelemetry-sdk opentelemetry-exporter-otlp）
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

# 初始化 Tracer（通常在 Airflow Worker 启动时全局初始化一次）
def init_tracer(service_name: str = "airflow-worker"):
    """初始化 OpenTelemetry Tracer，导出到 Jaeger/Grafana Tempo"""
    provider = TracerProvider()
    exporter = OTLPSpanExporter(
        endpoint="http://jaeger-collector:4317",  # OTLP gRPC 端点
        insecure=True,
    )
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    return trace.get_tracer(service_name)


# 在 Task 中创建 Span
@task
def traced_etl_step(dag_id: str, task_id: str, run_id: str, **context):
    """带 TraceID 的 ETL 处理任务"""
    tracer = trace.get_tracer(__name__)

    # 从 Airflow context 中获取 run_id 作为 TraceID 的一部分
    # 这样可以实现 Airflow DagRun → Task Spans 的关联
    with tracer.start_as_current_span(
        f"{dag_id}.{task_id}",
        attributes={
            "airflow.dag_id": dag_id,
            "airflow.task_id": task_id,
            "airflow.run_id": run_id,
            "airflow.logical_date": str(context["logical_date"]),
        },
        kind=trace.SpanKind.INTERNAL,
    ) as span:
        # --- 实际的业务逻辑 ---
        span.add_event("开始查询 MySQL")
        # ... MySQL 查询 ...
        span.add_event("MySQL 查询完成", attributes={"rows": 15000})

        span.add_event("开始写入 Kafka")
        # ... Kafka 写入 ...
        span.add_event("Kafka 写入完成", attributes={"messages": 15000})

        # 将 TraceID 注入 XCom，供下游 Task 和外部系统传递
        carrier = {}
        TraceContextTextMapPropagator().inject(carrier)
        span.set_attribute("trace_id", span.get_span_context().trace_id)

        return {"status": "ok", "trace_carrier": carrier}


# 跨 Task 传递 Trace 上下文
@task
def downstream_task(upstream_result: dict, **context):
    """下游任务：从上游携带的 Trace 上下文中继续追踪"""
    # 从 XCom 中提取上游的 trace carrier
    carrier = upstream_result.get("trace_carrier", {})
    ctx = TraceContextTextMapPropagator().extract(carrier)

    tracer = trace.get_tracer(__name__)
    with tracer.start_as_current_span("downstream_processing", context=ctx) as span:
        span.add_event("开始下游处理")
        # ... 业务逻辑 ...
        span.add_event("下游处理完成")


with DAG(
    dag_id="traced_etl_demo",
    schedule="@daily",
    start_date=datetime(2025, 1, 1),
    catchup=False,
) as dag:
    step1 = traced_etl_step(dag_id="traced_etl_demo", task_id="step1", run_id="{{ run_id }}")
    step2 = downstream_task(step1)

    step1 >> step2
```

**分布式追踪架构图**：

```
┌──────────────────────────────────────────────┐
│                   Jaeger UI                   │
│            http://jaeger:16686               │
└──────────────────┬───────────────────────────┘
                   │ 查询 Trace
┌──────────────────┴───────────────────────────┐
│              Jaeger Collector                 │
│            OTLP gRPC :4317                    │
└──────────────────┬───────────────────────────┘
                   │
    ┌──────────────┼──────────────┐
    │              │              │
┌───┴───┐    ┌────┴────┐   ┌─────┴─────┐
│Worker │    │ Worker  │   │  Worker   │
│TraceID│    │ TraceID │   │  TraceID  │
│  A-B  │    │  A-C    │   │   A-D     │
└───────┘    └─────────┘   └───────────┘
    │              │              │
    └──────────────┼──────────────┘
                   │
  DagRun run_id → TraceID 关联
```

**OpenTelemetry 与 Airflow 的集成要点**：

1. **TraceID = DagRun run_id 的映射**：在 Span 属性中保存 `airflow.run_id`，这样在 Jaeger 中搜 `airflow.run_id=scheduled__2025-01-15T00:00:00` 就能看到该 DagRun 的完整调用链。
2. **跨 Task 传递**：通过 XCom 传递 `trace_carrier`，下游 Task 使用 `extract()` 恢复上下文。但注意 XCom 大小限制（默认 48KB），carrier 通常只有几百字节，不成问题。
3. **导出后端兼容性**：OTLP 协议是开放的——同一套代码可以导出到 Jaeger、Grafana Tempo、Datadog APM、Dynatrace 等任何兼容 OTLP 的后端。

### 3.8 日志聚合：Fluentd → Elasticsearch → Kibana

**步骤目标**：将 Airflow 各组件的分散日志统一收集到 ELK 栈中，通过 TraceID 串联。

**Fluentd 配置思路**（`fluentd/conf/fluent.conf`）：

```xml
<source>
  @type tail
  path /opt/airflow/logs/**/*.log
  pos_file /var/log/td-agent/airflow-log.pos
  tag airflow.*
  <parse>
    @type regexp
    # 解析 Airflow 日志格式：[2025-01-15 00:00:00,123] {taskinstance.py:456} INFO - message
    expression /^\[(?<timestamp>[^\]]+)\]\s+\{(?<module>[^}]+)\}\s+(?<level>[A-Z]+)\s+-\s+(?<message>.*)$/
  </parse>
</source>

<match airflow.**>
  @type elasticsearch
  host elasticsearch
  port 9200
  index_name airflow-logs-%Y.%m.%d
  include_tag_key true
  tag_key @log_name
  <buffer>
    @type file
    path /var/log/td-agent/buffer/airflow
    flush_interval 10s
  </buffer>
</match>
```

**在 Kibana 中通过 TraceID 串联日志**：当 Task 使用 OpenTelemetry 时，可以在日志中打印 TraceID，然后在 Kibana Discover 页面过滤：`trace_id:"a1b2c3d4e5f6"`——这样就能将同一次 DagRun 的所有组件日志串联展示。

### 3.9 完整 Docker Compose 文件

将以上所有组件编入 `observability/docker-compose.monitoring.yaml`：

```yaml
version: "3.8"

services:
  # ========== StatsD Exporter ==========
  statsd-exporter:
    image: prom/statsd-exporter:v0.26.1
    container_name: statsd-exporter
    ports:
      - "9102:9102"
      - "8125:8125/udp"
    volumes:
      - ./statsd/statsd.conf:/etc/statsd-exporter/statsd.conf:ro
    command:
      - "--statsd.listen-udp=:8125"
      - "--statsd.mapping-config=/etc/statsd-exporter/statsd.conf"
    restart: unless-stopped
    networks:
      - monitoring

  # ========== Prometheus ==========
  prometheus:
    image: prom/prometheus:v2.52.0
    container_name: prometheus
    ports:
      - "9090:9090"
    volumes:
      - ./prometheus/prometheus.yml:/etc/prometheus/prometheus.yml:ro
      - ./alert_rules/airflow_alerts.yml:/etc/prometheus/airflow_alerts.yml:ro
      - prometheus_data:/prometheus
    command:
      - "--config.file=/etc/prometheus/prometheus.yml"
      - "--storage.tsdb.path=/prometheus"
      - "--web.enable-lifecycle"
      - "--storage.tsdb.retention.time=30d"
    restart: unless-stopped
    networks:
      - monitoring

  # ========== Grafana ==========
  grafana:
    image: grafana/grafana:10.4.0
    container_name: grafana
    ports:
      - "3000:3000"
    environment:
      - GF_SECURITY_ADMIN_USER=admin
      - GF_SECURITY_ADMIN_PASSWORD=admin
      - GF_INSTALL_PLUGINS=grafana-clock-panel
    volumes:
      - ./grafana/datasources/prometheus.yaml:/etc/grafana/provisioning/datasources/prometheus.yaml:ro
      - ./grafana/dashboards/:/etc/grafana/provisioning/dashboards/:ro
      - grafana_data:/var/lib/grafana
    restart: unless-stopped
    networks:
      - monitoring
    depends_on:
      - prometheus

  # ========== Jaeger（可选：分布式追踪后端） ==========
  jaeger:
    image: jaegertracing/all-in-one:1.57
    container_name: jaeger
    ports:
      - "16686:16686"   # Jaeger UI
      - "4317:4317"     # OTLP gRPC（OpenTelemetry 数据入口）
      - "4318:4318"     # OTLP HTTP
    environment:
      - COLLECTOR_OTLP_ENABLED=true
    restart: unless-stopped
    networks:
      - monitoring

networks:
  monitoring:
    driver: bridge

volumes:
  prometheus_data:
  grafana_data:
```

**启动命令**：

```bash
# 启动完整监控栈
docker compose -f observability/docker-compose.monitoring.yaml up -d

# 验证
curl http://localhost:9102/metrics | head -20     # StatsD Exporter
curl http://localhost:9090/targets                 # Prometheus Targets
curl http://localhost:3000                         # Grafana
curl http://localhost:16686                        # Jaeger UI

# （可选）在 Airflow 配置中启用 OpenTelemetry
export AIRFLOW__METRICS__STATSD_ON=True
export AIRFLOW__METRICS__STATSD_HOST=statsd-exporter
export AIRFLOW__METRICS__STATSD_PORT=8125
```

---

## 4 项目总结

### 4.1 监控方案对比

| 维度 | Web UI 原生监控 | StatsD + Prometheus + Grafana | ELK 日志栈 | OpenTelemetry 全链路 |
|------|----------------|-------------------------------|-----------|---------------------|
| **部署复杂度** | 零（内置） | 低（3 个容器） | 高（ES 集群需调优） | 中（需改造 Task 代码） |
| **实时性** | 按需查看（手动刷新） | 15s 抓取间隔 | 秒级（tail） | 秒级 |
| **历史趋势** | 无 | Prometheus 30 天保留 | ES 可按年保留 | 取决于后端 |
| **告警能力** | 无（除非配 Callback） | 原生 PromQL + AlertManager | Kibana Alerting（付费功能） | 无（需配合 Metrics） |
| **多组件关联** | 无 | 通过 label 串联 | 通过 TraceID 串联 | 原生 Span 树 |
| **适合场景** | 快速排查单个 Task | 集群宏观监控 + 趋势分析 | 事后审计 + 根因分析 | 跨服务延迟瓶颈定位 |

### 4.2 RED + USE 方法论速查表

| 方法 | 面向 | Rate（速率） | Errors（错误） | Duration（耗时） | Utilization（利用率） | Saturation（饱和度） |
|------|------|-------------|---------------|-----------------|----------------------|---------------------|
| **RED** | 任务 | `rate(ti_success_total[5m])` | `rate(ti_failure_total[5m])` | `histogram_quantile(.95, ti_duration)` | — | — |
| **USE** | 调度器 | — | `heartbeat` 缺失 | `critical_section_duration` | CPU/Mem | 队列深度 |
| **USE** | Pool | — | slot 分配失败 | — | `used_slots / total_slots` | 排队任务数 |
| **USE** | Celery | — | Worker 离线 | 任务执行延迟 | Worker 数 | Broker 消息堆积 |

### 4.3 注意事项

1. **StatsD UDP 网络分区**：StatsD 走 UDP，默认无重试。如果 StatsD Exporter 和 Airflow 不在同一子网，UDP 丢包率可能升高。处理方案：将 StatsD Exporter 作为 Sidecar 与 Airflow Scheduler 部署在同一 Pod/容器中，走 localhost 通信。
2. **Prometheus 存储容量**：按每 15s 抓一波，每条指标约 2KB 估算。Airflow 300 条 Dag × 每个 Task 5 条指标 × P99 200 个活跃 Task = 3000 条时序，每天约 345MB。30 天保留约 10GB。建议给 Prometheus 分配 50GB 卷以留余量。
3. **Grafana Dashboard 版本管理**：手动在 UI 里拖拽面板不容易追溯变更。建议将 Dashboard JSON 纳入 Git 仓库，通过 Grafana Provisioning 自动加载。每次修改先改 JSON 文件，再通过 CI 部署。
4. **OpenTelemetry 性能开销**：每次 Span 创建约 0.5μs，BatchSpanProcessor 异步导出对任务执行延迟影响 < 1%。但如果 Task 是高频短任务（< 10ms），建议用 `SimpleSpanProcessor` 或关闭追踪以避免不必要的开销。
5. **告警静默窗口**：在固定维护窗口（如每周日 02:00-04:00）需要 Quiet Hours——否则 Dag 下线维护会触发大量"心跳超时"告警。在 Grafana Alerting 中配置 `Notification Policies > Mute Timings`。

### 4.4 常见踩坑经验

| # | 故障案例 | 根因 | 解决方案 |
|---|---------|------|---------|
| 1 | Grafana 面板显示"无数据"，但 Airflow 确实在跑任务 | StatsD Exporter 的 `mapping` 未正确匹配指标名。Airflow 发的 `airflow.ti.success.my_dag.my_task` 与配置中的正则不匹配 | 用 `nc -lu 8125` 先抓取原始 UDP 包，确认指标名格式后再写 mapping；启动 StatsD Exporter 时加 `--log.level=debug` 查看映射日志 |
| 2 | Prometheus 中 `airflow_ti_success_total` 一直为 0，但 `airflow_ti_failure_total` 有值 | Airflow 的 `metrics_allow_list` 中未包含 `ti`，导致 success 指标被过滤 | 检查 `airflow.cfg` 中 `[metrics]` 节的 `metrics_allow_list`，改为 `metrics_allow_list = scheduler,ti,dag_processing,pool`（移除过滤） |
| 3 | 告警"Pool 槽位耗尽"频繁误报，实际 Pool 使用率只有 60% | `airflow.pool.used_slots.*` 统计了 `occupied_states`（包括 deferred 状态），deferrable sensor 占槽不释放但实际不耗资源 | 将 Pool 的 `include_deferred = False`，或在告警规则中加 `{include_deferred="false"}` 过滤 |

### 4.5 思考题

1. 假设你的 Airflow 集群配置了 Prometheus + Grafana，凌晨 3 点收到"Scheduler 心跳超时"告警。你登录服务器后发现 Scheduler 进程还在，但 CPU 100%。你该如何用 PromQL 在 30 秒内确定是哪个环节导致 CPU 飙升？（提示：`rate(airflow_dag_processing_total_parse_time_seconds_sum[5m])` / `rate(airflow_scheduler_critical_section_duration_seconds_sum[5m])`）

2. 你的团队有 3 个业务线（广告、推荐、风控），每个业务线独立维护 Dag。在 Grafana 中，如何让各团队只看自己 Dag 的指标而不互相干扰？在告警规则中如何实现按业务线分通道推送（广告团队 → 企业微信，推荐团队 → 钉钉，风控团队 → PagerDuty）？

*（提示：利用 Grafana 的 Folder Permissions + Teams 机制；Prometheus AlertManager 的 `routes` 按 label 分叉。）*

---

> **本章完成**：你已经构建了 Airflow 集群的完整可观测性体系。下一章将探讨多环境管理与 GitOps 实践——如何将监控大盘和告警规则也纳入 Git 版本控制，实现"监控即代码"（Monitoring as Code）。
