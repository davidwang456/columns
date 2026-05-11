# 第24章：监控体系与Prometheus+Grafana集成

> **定位**：为DolphinScheduler构建三层可观测体系——基础设施层、中间件层、应用业务层，基于RED方法量化调度健康度。
> **核心内容**：Actuator指标暴露、Prometheus抓取配置、PromQL关键查询、Grafana五排Dashboard设计、P1/P2/P3告警规则、AlertManager路由、SLO定义。
> **实战目标**：从零搭建PagerDuty+微信双通道告警的完整监控体系，让调度集群"开口说话"。

---

## 1. 项目背景

大麦电商的DolphinScheduler集群上线两个月后，连续发生了三次事故。第一次：凌晨ETL高峰时段，Master节点的命令队列积压突破10000条，下游工作流被推迟了整整3小时，直到运营部门投诉"报表怎么还没出"才被发现——运维同学翻了半小时日志才知道是Master处理不过来。第二次：一台Worker节点的`/tmp`目录被日志撑满，挂载在上面的所有任务静默失败了4小时，没有一个人感知到——因为失败的任务被DS自动重试了多次，UI上只是状态一直"运行中"，没有任何告警弹窗。第三次：API Server出现JVM内存泄漏，接口响应从正常的50ms飙升到30秒，全公司都在抱怨"DS怎么这么慢"，但实际上Worker跑得好好的，只是API前端卡住了。

运维总监老马把安全帽往桌上一拍："三次事故，三次都是用户投诉了我们才知道。我们的监控呢？Grafana大屏呢？PagerDuty呢？"团队面面相觑——调度器虽然跑得欢，但内部的命令队列深度、任务失败率、API延迟、节点资源水位这些关键指标，一条都没有暴露出来。运维团队对DS内部状态完全"失明"。

本质症结在于：**DolphinScheduler自带了健康检查端点和Actuator指标，但团队从未将它们接入可观测体系**。Prometheus部署了、Grafana也跑着，但只配了基础的node_exporter——看得见CPU和内存，看不见调度业务。本章的目标就是填补这个断层：把DS的Master/Worker/API/Alert四个服务的业务指标接入Prometheus，设计一套能覆盖RED（Rate/Error/Duration）三要素的监控Dashboard，并分层定义P1~P3告警规则。

---

## 2. 项目设计——剧本式交锋对话

周一上午，老马下了死命令："本周内，我要在Grafana上看到DS的完整监控大屏，任何关键指标异常5分钟内必须通知到PagerDuty。"小胖、小白和大师三人围在Grafana空荡荡的Dashboard前，开始设计监控方案。

**小胖**（自信满满，一边点开Grafana的Dashboard Import界面）：
> "监控不就是看CPU和内存嘛！Prometheus配个node_exporter，Grafana拉个现成的Node Exporter Full dashboard，十分钟搞定。这Dashboard连磁盘IO和网络吞吐量都有，多专业！"

**小白**（按下小胖要点击导入的手）：
> "等一下——你看的这个dashboard只能看机器指标，DS自己的业务指标在哪里？比如Master当前正在执行多少个工作流？命令队列积压了多少条？每个Worker的任务成功率是多少？API的P99延迟有没有恶化？这些指标不在node_exporter里，但它们才是衡量调度系统健康度的真正核心。还有，告警阈值设多少合适——命令队列超过100条就该告警还是超过1000条？任务成功率99%算正常还是异常？没有历史基线就拍脑袋设阈值，要么被告警淹没，要么出了事还不知道。"

**小胖**（挠头，把Grafana界面关了）：
> "那……我能看到DS的进程还活着不就行了吗？健康检查端点总该管用吧？"

**大师**（从工位起身，在白板上画出三层架构图）：
> "小胖说的是黑盒监控——只看活着没活着。我们需要的是白盒监控——从系统内部暴露信息。我把DS的监控分三层："

> "**第一层：基础设施层。**CPU、内存、磁盘、网络——node_exporter能搞定，这是基础。**第二层：中间件层。**ZK的ZNODE数量和延迟、MySQL的连接数和慢查询、JVM的GC频率和堆内存占比——这些JMX指标通过JMX Exporter或Actuator暴露。**第三层：应用业务层。**这才是DS监控的真正难点——Master的command.queue.size、Worker的task.success.count、API的http.request.latency.p99——这些指标是DS自己的`dolphinscheduler-meter`模块产生的，只有接入了Prometheus才能看到。"

> "咱们按照Google SRE的**RED方法**来设计监控指标体系：Rate（速率）——每秒提交多少工作流、任务；Error（错误率）——失败的百分比是多少；Duration（延迟）——P50/P90/P99任务执行耗时。这三项覆盖了调度系统的核心健康面。"

**技术映射**：DolphinScheduler基于Spring Boot构建，集成了Micrometer指标库。通过`dolphinscheduler-meter`模块，DS将Master/Worker/API的核心业务指标注册到MeterRegistry中，并暴露为Prometheus兼容格式。在`application.yaml`中启用`management.endpoints.web.exposure.include=prometheus`后，每个DS服务都会在`/actuator/prometheus`端点上提供完整的指标数据——包括JVM指标（内存、GC、线程）、HTTP请求指标、以及DS自定义的业务指标。

**小白**（在笔记本上快速记录，画了一个矩阵）：
> "那我理解了——Prometheus定期去每个DS节点的`/actuator/prometheus`端点拉取数据，Grafana用PromQL查询展示，AlertManager根据规则触发告警。但我还有三个实际问题：第一，Worker集群如果有5台节点，Grafana上一张图要同时展示5条线，图会不会糊成一团？第二，Prometheus是Pull模式，如果Worker节点在防火墙后面、Prometheus主动拉不到怎么办？第三，告警规则的`for`时间设多长——像CommandQueueHigh这种，是不是应该用运行中的工作流数（Workflow Executing Count）来消除抖动？"

**大师**（赞许地点头）：
> "三个问题都切中要害。第一个Dashboard设计问题——我会把关键指标分成5排，第1排Overview整体数字，第2~4排分别展示Master/Worker/API的时间序列图，第5排是基础设施面板。同一张图里多条线用不同颜色区分，关键指标只显示Top3和最差节点，避免视觉噪声。"

> "第二个网络问题——如果Worker在隔离VPC里，有两个方案：方案A，在VPC内部署一个Prometheus实例，再通过Remote Write推送到核心Prometheus；方案B，用Pushgateway，Worker主动推送指标到网关，Prometheus再从网关拉取。方案A更推荐，Pushgateway只适合临时任务场景。第三个抖动问题——`for: 5m`的意思是'这个条件持续成立5分钟才触发告警'，正好能过滤掉瞬时抖动。真正的诀窍是：在配置告警规则之前，先用Grafana观察指标至少一周，摸清正常波动的基线和峰谷规律——比如每天凌晨3点的ETL高峰期间CPU跑满80%是正常的，此时告警就是误报。"

**小胖**（若有所思，把之前的Node Exporter Dashboard关了）：
> "所以告警阈值不是拍脑袋的，而是从历史数据里'长'出来的。运维也不是配置完就完事，要先观测、再定义、再消费告警。那我先用一周摸基线，回头再设阈值。"

---

## 3. 项目实战

### Step 1：启用DS服务的Spring Boot Actuator指标

在每个DS服务的`application.yaml`中添加以下配置。以Master为例：

```yaml
# Master application.yaml
management:
  endpoints:
    web:
      exposure:
        include: health,info,metrics,prometheus
  metrics:
    export:
      prometheus:
        enabled: true
    tags:
      application: dolphinscheduler-master
  endpoint:
    health:
      show-details: always
```

Worker、API Server、Alert Server同理，只需将`dolphinscheduler-master`分别替换为`dolphinscheduler-worker`、`dolphinscheduler-api`、`dolphinscheduler-alert`。重启服务后，访问`http://<host>:<port>/actuator/prometheus`，应能看到包含`dolphinscheduler_`前缀和`jvm_`前缀的大量指标行。

### Step 2：部署Prometheus并配置抓取

编写`prometheus.yml`配置文件：

```yaml
global:
  scrape_interval: 30s
  evaluation_interval: 30s

scrape_configs:
  - job_name: 'dolphinscheduler-master'
    static_configs:
      - targets: ['master-01:5679', 'master-02:5679']
    metrics_path: '/actuator/prometheus'

  - job_name: 'dolphinscheduler-worker'
    static_configs:
      - targets:
          - 'worker-01:1235'
          - 'worker-02:1235'
          - 'worker-03:1235'
    metrics_path: '/actuator/prometheus'

  - job_name: 'dolphinscheduler-api'
    static_configs:
      - targets: ['api-01:12345', 'api-02:12345']
    metrics_path: '/actuator/prometheus'

  - job_name: 'dolphinscheduler-alert'
    static_configs:
      - targets: ['alert-01:1278']
    metrics_path: '/actuator/prometheus'
```

> **要点**：`scrape_interval: 30s`是生产环境的常规选择——太短（如5s）会给DS API端点和JVM带来过大的指标采集压力；太长（如5min）则无法及时发现瞬时毛刺。另外，Worker和API的端口号必须与DS实际配置一致（Worker默认actuator端口1235，API默认12345）。

### Step 3：设计核心PromQL查询

以下PromQL查询是Grafana Dashboard和告警规则的基础组件：

```promql
# === Master指标 ===

# 命令队列深度（当前值）
sum(master_command_queue_size)

# 工作流提交速率（过去5分钟平均，条/秒）
rate(master_workflow_submit_total[5m])

# 工作流执行中数量（当前值）
master_workflow_executing_count

# Master是否为Leader（1=Leader，0=Follower）
master_leader_status

# 工作流成功率（过去5分钟）
sum(rate(master_workflow_success_total[5m])) /
sum(rate(master_workflow_submit_total[5m])) * 100

# === Worker指标 ===

# 任务执行速率（过去5分钟，条/秒）
rate(worker_task_submit_total[5m])

# 任务失败率（百分比）
sum(rate(worker_task_failure_total[5m])) /
sum(rate(worker_task_submit_total[5m])) * 100

# 任务执行时间P99（秒）
histogram_quantile(0.99,
  rate(worker_task_execution_seconds_bucket[5m]))

# 任务执行时间P50
histogram_quantile(0.50,
  rate(worker_task_execution_seconds_bucket[5m]))

# Worker活跃线程数
worker_thread_pool_active_threads

# === API指标 ===

# API请求速率（条/秒）
rate(http_server_requests_seconds_count{uri=~"/dolphinscheduler/.*"}[5m])

# API P99延迟（秒）
histogram_quantile(0.99,
  rate(http_server_requests_seconds_bucket{uri=~"/dolphinscheduler/.*"}[5m]))

# API 5xx错误率（百分比）
sum(rate(http_server_requests_seconds_count{uri=~"/dolphinscheduler/.*",
    status=~"5.."}[5m])) /
sum(rate(http_server_requests_seconds_count{uri=~"/dolphinscheduler/.*"}[5m])) * 100

# === 基础设施指标（node_exporter） ===

# CPU使用率（按实例）
100 - (avg by(instance) (rate(node_cpu_seconds_total{mode="idle"}[5m])) * 100)

# 磁盘使用率
100 - (node_filesystem_avail_bytes{fstype!~"tmpfs|fuse.*"} /
       node_filesystem_size_bytes{fstype!~"tmpfs|fuse.*"} * 100)

# JVM堆内存使用率
sum(jvm_memory_used_bytes{area="heap"}) /
sum(jvm_memory_max_bytes{area="heap"}) * 100
```

### Step 4：搭建Grafana五排监控Dashboard

按以下分区在Grafana中创建Dashboard，每个面板的数据源均指向Prometheus：

**第1排 — Overview总览（Stat面板）**：
- 当前活跃工作流数：`master_workflow_executing_count`
- 命令队列深度：`sum(master_command_queue_size)`
- 过去1小时任务成功率：`sum(rate(worker_task_success_total[1h])) / sum(rate(worker_task_submit_total[1h])) * 100`
- 健康Worker节点数：`count(up{job="dolphinscheduler-worker"} == 1)`
- Master Leader健康：`sum(master_leader_status) == 1`（显示healthy/warning）

**第2排 — Master面板（折线图）**：
- 命令队列深度随时间变化
- 工作流提交速率（5分钟滑动窗口）
- Master Leader状态变化（状态时间轴图）

**第3排 — Worker面板**：
- 各Worker任务执行速率（堆叠柱状图，按instance分组）
- 各Worker任务失败率（折线图，按instance分组）
- 任务执行时间分布（Heatmap热力图，Y轴按P50/P90/P99分桶）

**第4排 — API面板**：
- 请求速率折线图
- P50/P90/P99延迟折线图（三条线同一面板）
- 4xx/5xx错误率折线图

**第5排 — 基础设施面板**：
- 各节点CPU使用率
- 各节点内存使用率（含JVM堆占比）
- 各节点磁盘使用率

> **要点**：Dashboard上每个面板标注`$datasource`变量，支持一键切换Prometheus数据源。为折线图设置合理的Y轴范围（避免自动缩放夸大微小波动），所有百分比面板上限设为100%。

### Step 5：定义分层告警规则

创建`prometheus_rules.yml`，并在`prometheus.yml`中通过`rule_files: ["prometheus_rules.yml"]`引用：

```yaml
groups:
  - name: dolphinscheduler_critical
    rules:
      # P1: Master命令队列深度 > 100 持续5分钟
      - alert: MasterCommandQueueHigh
        expr: master_command_queue_size > 100
        for: 5m
        labels:
          severity: critical
          component: master
        annotations:
          summary: "Master命令队列严重积压"
          description: "Master {{ $labels.instance }} 命令队列深度={{ $value }}，超过阈值100，工作流可能存在大面积延迟"

      # P1: 任务失败率 > 5% 持续10分钟
      - alert: HighTaskFailureRate
        expr: |
          sum(rate(worker_task_failure_total[5m])) /
          sum(rate(worker_task_submit_total[5m])) > 0.05
        for: 10m
        labels:
          severity: critical
          component: worker
        annotations:
          summary: "Worker任务失败率超过5%"
          description: "过去10分钟任务失败率={{ $value | humanizePercentage }}，需立即排查"

      # P1: Master Leader丢失
      - alert: MasterLeaderLost
        expr: sum(master_leader_status) == 0
        for: 2m
        labels:
          severity: critical
          component: master
        annotations:
          summary: "Master集群失去Leader"
          description: "所有Master节点均非Leader状态，调度功能已中断，请立即介入"

  - name: dolphinscheduler_warning
    rules:
      # P2: Worker CPU > 90% 持续15分钟
      - alert: WorkerHighCPU
        expr: |
          100 - (avg by(instance) (rate(node_cpu_seconds_total{mode="idle"}[5m])) * 100) > 90
        for: 15m
        labels:
          severity: warning
          component: worker
        annotations:
          summary: "Worker节点CPU持续高负载"
          description: "{{ $labels.instance }} CPU使用率={{ $value }}%，持续15分钟，可能影响任务调度"

      # P2: API P99延迟 > 5秒 持续5分钟
      - alert: APISlowResponse
        expr: |
          histogram_quantile(0.99,
            rate(http_server_requests_seconds_bucket{uri=~"/dolphinscheduler/.*"}[5m])) > 5
        for: 5m
        labels:
          severity: warning
          component: api
        annotations:
          summary: "API P99延迟超过5秒"
          description: "API {{ $labels.instance }} P99延迟={{ $value }}秒，用户体验严重受影响"

      # P2: JVM堆内存使用 > 85% 持续10分钟
      - alert: JVMMemoryHigh
        expr: |
          sum(jvm_memory_used_bytes{area="heap"}) /
          sum(jvm_memory_max_bytes{area="heap"}) > 0.85
        for: 10m
        labels:
          severity: warning
          component: jvm
        annotations:
          summary: "JVM堆内存使用率超过85%"
          description: "{{ $labels.instance }} 堆内存已达{{ $value | humanizePercentage }}，存在OOM风险"

  - name: dolphinscheduler_info
    rules:
      # P3: Worker磁盘 > 85% 持续30分钟
      - alert: WorkerDiskHigh
        expr: |
          (1 - node_filesystem_avail_bytes{fstype!~"tmpfs|fuse.*"} /
           node_filesystem_size_bytes{fstype!~"tmpfs|fuse.*"}) * 100 > 85
        for: 30m
        labels:
          severity: info
          component: infrastructure
        annotations:
          summary: "Worker节点磁盘使用率超过85%"
          description: "{{ $labels.instance }} 挂载点{{ $labels.mountpoint }} 磁盘使用率={{ $value }}%，请清理日志或扩容"
```

### Step 6：配置AlertManager告警路由

```yaml
# alertmanager.yml
global:
  resolve_timeout: 5m

route:
  group_by: ['alertname', 'severity']
  group_wait: 30s
  group_interval: 5m
  repeat_interval: 4h
  receiver: 'wechat-default'
  routes:
    - match:
        severity: critical
      receiver: 'pagerduty-oncall'
      continue: true
    - match:
        severity: warning
      receiver: 'wechat-ops'
    - match:
        severity: info
      receiver: 'wechat-ops'

receivers:
  - name: 'pagerduty-oncall'
    pagerduty_configs:
      - routing_key: '<PAGERDUTY_ROUTING_KEY>'
        severity: critical
        description: '{{ .CommonAnnotations.description }}'

  - name: 'wechat-ops'
    wechat_configs:
      - corp_id: '<WECHAT_CORP_ID>'
        agent_id: '<WECHAT_AGENT_ID>'
        api_secret: '<WECHAT_API_SECRET>'
        to_user: '@all'
        message: |
          【{{ .CommonLabels.severity | toUpper }}】{{ .CommonAnnotations.summary }}
          详情: {{ .CommonAnnotations.description }}
```

> **要点**：`group_interval: 5m`确保同类告警聚合发送，避免告警风暴；`repeat_interval: 4h`防止同一告警反复轰炸on-call人员。P1（critical）走PagerDuty电话告警，P2/P3走企业微信群通知。

### Step 7：定义SLO（Service Level Objectives）

| 服务 | 指标 | SLO目标 | 测量窗口 |
|------|------|---------|----------|
| API | 可用性 | ≥99.9% | 30天滚动 |
| API | P99延迟 | <1秒 | 7天 |
| Master | 命令处理延迟 | <30秒 | 1天 |
| Worker | 任务成功率 | ≥99.5% | 7天 |
| Worker | 单任务最大延迟 | <15分钟 | 每天P99 |
| 整体 | 工作流准时率 | ≥99% | 30天 |

在Grafana中为每个SLO创建对应的PromQL查询面板，标注目标线（Threshold），运维团队每日巡检。

### Step 8：构建"运维速查"Dashboard（Operations Runbook）

额外创建一个独立Dashboard，聚合运维排障中最常用的信息：
- 所有Master/Worker/API节点健康状态一览（UP/DOWN指示灯）
- `/actuator/health`端点实时状态
- 最近10条告警历史列表
- 一键跳转链接：各节点Kibana日志、Prometheus Alert列表、ZooKeeper Znode浏览器
- On-call值班表（静态Text面板手动维护）与服务重启脚本路径

### Step 9：生产环境易踩的六个坑

1. **抓取间隔过频**：Prometheus 5s抓一次，Worker在每个抓取周期内都需要计算histogram分桶和汇总，100+个指标在高并发下会产生显著的CPU开销。推荐30s。
2. **指标保留未配置**：Prometheus默认保留15天，如果不加`--storage.tsdb.retention.time=30d`，两周后的基线数据就会被清除，导致同比环比分析无法进行。
3. **无基线就告警**："CPU>90%"这种一刀切规则，在每天凌晨大批量ETL期间是常态。必须先观察指标一周，摸清峰谷模式，再分别设置不同时段的阈值（或使用同环比异常检测替代固定阈值）。
4. **缺少JMX/JVM指标**：Prometheus的JVM指标（jvm_memory_used_bytes、jvm_gc_pause_seconds）是排查内存泄漏和GC停顿的唯一线索。如果Application中漏配了Actuator，出问题时只能靠`jstack`和`jmap`——效率差一个数量级。
5. **防火墙阻断采集**：Worker和Master之间的内网端口经常被安全组策略遗漏。部署Prometheus后务必在Prometheus UI的Targets页面确认所有endpoint的State列都是UP——只要有一个DOWN，就说明防火墙或端口未放行。
6. **告警静默风暴**：某次网络闪断导致所有Worker被Prometheus标记为DOWN，瞬间触发8条P1告警涌入PagerDuty。解决方案：在AlertManager中设置`group_by: ['alertname']`对同类告警聚合，配置`group_wait: 30s`缓冲时间，避免瞬时波动"炸醒"整个on-call团队。

---

## 4. 项目总结

### 监控成熟度模型

应用运维监控通常经历五个阶段：**Level 0（无监控）**——零指标、零告警，全靠用户投诉发现故障，大麦团队最初的"失明"状态正属此级。**Level 1（黑盒探活）**——只有健康检查和存活心跳，能回答"服务挂了没"，但无法回答"为什么会挂"。**Level 2（RED指标）**——采集Rate、Error、Duration三类白盒指标，具备基础Dashboard和固定阈值告警，本章的交付物落在此级。**Level 3（分布式追踪）**——通过TraceID串联跨Master/Worker/API的请求链路，定位耗时瓶颈在哪个服务内部。**Level 4（预测式运维）**——基于历史指标训练异常检测模型，在指标偏离基线但尚未触及阈值时提前预警，实现机器比人先"嗅到"故障味。

DolphinScheduler当前的开箱监控能力大致在Level 1.5——提供了Actuator端点和`dolphinscheduler-meter`模块的基础指标，但缺少开箱即用的Dashboard模板和告警规则库，需要团队按本章方法补齐到Level 2。

### 不同角色的Dashboard需求

- **运维工程师**：需要第5排基础设施面板 + Worker节点状态矩阵，第一时间定位是哪台机器出了问题。
- **数据工程师**：需要第2~3排Master和Worker面板，关注工作流吞吐量、任务失败率和执行耗时，判断调度是否在预期时间内完成。
- **管理者**：只需第1排Overview + SLO达标仪表盘，5秒内知道"系统现在健康吗"。
- **建议**：在Grafana中按角色创建三个独立的Dashboard文件夹，各自配备对应的告警通知渠道。

### 三条生产告警实录

1. **凌晨ETL期间命令队列突增**：某天凌晨3:15，`MasterCommandQueueHigh`告警触发，命令队列深度飙升至850。on-call同学登录Master节点发现ZK的一个临时节点因网络抖动未正确释放，导致Master一直尝试向不存在的Worker分配任务。重启Master后恢复，全程从告警到恢复仅8分钟，远快于此前依赖用户投诉的3小时。

2. **任务失败率异常跳变**：周五下午，`HighTaskFailureRate`告警PagerDuty呼叫，任务失败率从平日的0.3%跳变到12%。排查后发现DBA临时修改了数据源密码却未同步更新DS的数据源中心配置，导致大量SQL任务鉴权失败。15分钟内修复，避免了整晚的数据产出中断。

3. **Worker磁盘悄悄逼近满载**：`WorkerDiskHigh`告警在工作时间触发，一台Worker节点的日志目录使用率从75%持续上升到88%。运维同学登录一看，发现`dolphinscheduler-worker.log`单文件已到20GB——原因是DS的日志滚动策略配置不当。清理日志并调整`logback.xml`后恢复。如果没有这条告警，按增长速率推算，再过6小时磁盘就会100%满载，届时整台Worker的所有任务都将静默失败。

### 思考题

1. 本章定义的告警规则中，哪些属于"领先指标"（leading indicator，在故障发生之前就能预警），哪些属于"滞后指标"（lagging indicator，故障已经发生才能检测到）？请分类并思考如何增加更多领先指标。

2. 如果你们的DS集群规模从5台Worker扩展到50台，当前的Prometheus单实例部署会遇到哪些瓶颈？你会如何设计分层联邦（Federation）架构来解决水平扩展问题？
