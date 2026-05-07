# 第38章：可观测性——Metrics 采集、监控大盘与告警设计

## 1. 项目背景

某互联网金融公司的 DataX 同步平台上线半年，每日运行 200+ 个同步任务，涵盖 MySQL→Hive、MongoDB→ClickHouse、Oracle→MySQL 等多条链路。运维小周的日常工作陷入一个循环：业务方在群里 @他说"今天的报表数据又没出来"，小周登录服务器 `grep "ERROR" datax_*.log`，找到失败的任务，手动重跑。

这种"人工感知、手动响应"的模式在某个周一凌晨彻底崩溃——一个 MySQL 源库的从库出现网络延迟，导致 12 个依赖该源的 DataX 任务全部失败，但小周直到早上 8 点半上班看钉钉消息才发现。此时核心决策已经用了 6 个小时的"脏数据"（实际只是 12 个源表中的 2 张表同步成功，其余 10 张表数据还停在三天前）。

CTO 在复盘会上总结了三个"看不见"：
1. **看不见任务状态**——不知道当前有多少任务在跑、多少成功、多少失败
2. **看不见性能瓶颈**——单个 Task 为什么慢？是源端读慢还是目标端写慢？Channel 利用率多少？
3. **看不见数据质量**——脏数据率有没有超过容忍阈值？某张表的数据量是不是"断崖式下跌"？

本章从可观测性三支柱（Metrics、Logging、Tracing）切入，重点聚焦 DataX 的关键 Metrics 指标定义、采集暴露方案、Grafana 监控大盘设计以及生产级告警规则。最终手把手搭建一套 PerfTrace→Prometheus→Grafana 的完整监控栈，让 DataX 从"黑盒"变"白盒"。

## 2. 项目设计——剧本式交锋对话

**（周二下午，故障复盘会，小周拿着打印出来的 grep 日志厚厚一叠）**

**小胖**：（小声嘀咕）我觉得 DataX 自带的日志挺全的呀——每条记录读了多少、写了多少、花了多少时间，这不都打印出来了？为什么还要搞监控？

**小白**：（拿起小周那叠 50 页的日志翻了翻）你看，昨天 200 个任务的日志加起来 500MB。你能在 30 秒内回答"昨天所有任务的脏数据率超过 1% 的有几个"吗？你能在 1 分钟内找出"过去 7 天耗时的 P99 增长趋势"吗？文本日志的价值是**事后排查**，但我们现在需要的是**实时感知**和**趋势分析**。

**大师**：（在投影上打开一张图）可观测性有三个支柱，我们今天把它们和 DataX 全对上：

- **Metrics（指标）**：数值化的测量数据，比如"QPS = 56,000 rec/s"、"Task 平均耗时 = 8 分钟"。用于实时监控大盘和告警。
- **Logging（日志）**：事件记录，比如"2026-05-06 03:15:42 Task-7 failed: Connection refused"。用于故障排查和问题回溯。
- **Tracing（链路追踪）**：一次 Job 从 Engine.entry() 到 Channel push/pull 的完整调用链路。DataX 没有原生 Tracing 支持，但可以通过在关键方法中植入 TraceID 实现。

**技术映射**：可观测性三支柱 = 体检报告的三张单子。Metrics 是"血压/心率/体温"（实时数值），Logging 是"病历本"（发生了什么），Tracing 是"心电图/脑电波"（全过程记录）。

---

**小胖**：（掰手指）那 DataX 具体有哪些关键指标？

**大师**：我按四个维度梳理：

**维度一：同步量指标**
- `totalRecords`：总记录数
- `totalBytes`：总字节数
- `averageSpeed`：平均 QPS（rec/s）
- `byteSpeed`：平均吞吐（MB/s）
- `peakSpeed`：峰值 QPS（1 秒内最大处理记录数）

**维度二：延迟指标**
- `jobTotalTime`：Job 总耗时
- `taskAvgTime`：所有 Task 的平均耗时
- `taskP99Time`：Task 耗时的 P99 分位
- `taskMaxTime`：最慢 Task 的耗时（木桶短板）
- `channelWaitTime`：Channel 的 push/pull 等待时间（反映消费速度是否匹配生产速度）

**维度三：错误指标**
- `dirtyRecordCount`：脏数据行数
- `dirtyRecordPercent`：脏数据率（%）
- `failedTaskCount`：失败 Task 数
- `retryCount`：重试次数
- `errorCategory`：错误类型分布（连接类 / 类型转换 / 超时 / OOM）

**维度四：资源指标**
- `jvmHeapUsed` / `jvmHeapMax`：JVM 堆使用/最大值
- `gcCollectionCount` / `gcCollectionTime`：GC 次数和耗时
- `channelBufferUsed`：Channel 缓冲区的占用率
- `dbConnectionActive`：活跃数据库连接数

**小白**：（快速在笔记本上记录）这些指标怎么从 DataX 里采集出来？DataX 本身没有 Metrics 暴露接口吧？

**大师**：这正是核心挑战。DataX 的统计信息有两层：
1. **Job 级别**：在 JobContainer.schedule() 完成后，`JobContainerCommunicator` 汇总所有 TaskGroup 的 Communication 对象，生成最终的统计数据——但这些数据只打印到 stdout，没有暴露为 Metrics。
2. **Task 级别**：PerfTrace 提供了一个轻量级的性能跟踪框架（`PerfTrace.getInstance().trace(key, action)`），可以用来记录关键操作的耗时，但默认也只是打印日志。

我们的方案是——**在 DataX 中埋点将 Communication 数据暴露为 Metrics，推送到 Pushgateway，再由 Prometheus 拉取**。

**技术映射**：Metrics 采集 = 给工厂的每台机器装传感器。PerfTrace = 传感器探头，Prometheus = 中控室的数据采集器，Grafana = 中控室大屏幕。

---

**小胖**：（挠头）Prometheus、Pushgateway、Grafana……这不是要搭一堆东西吗？有没有简单方案？

**大师**：（在白板上画出两条路径）

**方案 A（轻量）**：Filebeat（采集 stdout 日志） → Elasticsearch（存储结构化日志） → Grafana（可视化）
- 优点：不改 DataX 代码，只接入日志管道
- 缺点：日志→指标有延迟（Filebeat 采集周期通常 5~10 秒），实时性差

**方案 B（深度）**：DataX 内埋点 → PerfTrace → Pushgateway → Prometheus → Grafana → AlertManager
- 优点：实时性强（Pushgateway 推模式，秒级），指标维度丰富
- 缺点：需要修改 DataX 源码（或开发 Metrics Reporter 插件）

**方案 B 核心代码——在 JobContainer 中暴露 Metrics**：

```java
// 在 JobContainer.schedule() 的 finally 块中，向 Pushgateway 推送指标
public void schedule() {
    try {
        // ... 现有调度逻辑 ...
    } finally {
        // 汇总 Communication
        Communication finalComm = containerCommunicator.collect();
        
        // 推送到 Pushgateway
        MetricsReporter.push(new DataXJobMetrics()
            .jobId(jobId)
            .totalRecords(finalComm.getLongCounter(CommunicationTool.READ_SUCCEED_RECORDS))
            .totalBytes(finalComm.getLongCounter(CommunicationTool.READ_SUCCEED_BYTES))
            .averageSpeed(finalComm.getLongCounter(CommunicationTool.READ_SUCCEED_RECORDS) 
                          / (finalComm.getLongCounter(CommunicationTool.WRITE_RECEIVED_RECORDS)))
            .taskP99Time(calculateTaskP99())
            .dirtyRecords(finalComm.getLongCounter(CommunicationTool.READ_FAIL_RECORDS))
            .jobTotalTime(System.currentTimeMillis() - startTime)
            .jvmHeapUsed(Runtime.getRuntime().totalMemory() - Runtime.getRuntime().freeMemory())
        );
    }
}
```

**小白**：（追问）那告警规则怎么设计？什么情况应该告警、什么情况不需要？

**大师**：告警设计的黄金法则是——**告警必须可操作（Actionable）**。如果一个告警你看到了也只能说"哦，知道了"而不能做任何事，那就是噪音。基于这个原则，我推荐三条核心告警规则：

**规则1：连续 3 次 Job 失败**
```
表达式: datax_job_failures{job_id!=""}[15m] >= 3
级别: P1（紧急）
行动: 钉钉 @运维，自动触发该 Job 的依赖 Job 暂停
```

**规则2：单 Task 耗时超过平均耗时的 5 倍**
```
表达式: datax_task_max_duration_seconds / datax_task_avg_duration_seconds > 5
级别: P2（警告）
行动: 钉钉通知，可能是数据倾斜或目标端写入瓶颈
```

**规则3：脏数据率 > 1%**
```
表达式: datax_dirty_record_percent > 1
级别: P2（警告）
行动: 钉钉通知 + 自动标记该批次数据为"需人工校验"
```

**技术映射**：告警 = 火灾报警器。规则 1 是"整栋楼失火"（拉响全楼警报），规则 2 是"某个房间烟雾浓度异常"（过去检查但不用疏散），规则 3 是"空气质量超标"（标记并通知，但生产线不停）。

## 3. 项目实战

### 3.1 步骤一：部署 Prometheus + Grafana + Pushgateway 监控栈

**目标**：用 Docker Compose 一键部署监控基础设施。

**Docker Compose 文件**（`docker-compose-monitoring.yml`）：

```yaml
version: '3.8'
services:
  pushgateway:
    image: prom/pushgateway:v1.7.0
    container_name: pushgateway
    ports:
      - "9091:9091"
    restart: unless-stopped

  prometheus:
    image: prom/prometheus:v2.50.0
    container_name: prometheus
    ports:
      - "9090:9090"
    volumes:
      - ./prometheus.yml:/etc/prometheus/prometheus.yml
      - prometheus_data:/prometheus
    command:
      - '--config.file=/etc/prometheus/prometheus.yml'
      - '--storage.tsdb.retention.time=30d'
    restart: unless-stopped

  grafana:
    image: grafana/grafana:10.3.0
    container_name: grafana
    ports:
      - "3000:3000"
    environment:
      - GF_SECURITY_ADMIN_PASSWORD=admin123
      - GF_INSTALL_PLUGINS=grafana-piechart-panel
    volumes:
      - grafana_data:/var/lib/grafana
    restart: unless-stopped

volumes:
  prometheus_data:
  grafana_data:
```

**Prometheus 配置**（`prometheus.yml`）：

```yaml
global:
  scrape_interval: 15s
  evaluation_interval: 15s

scrape_configs:
  - job_name: 'pushgateway'
    honor_labels: true
    static_configs:
      - targets: ['pushgateway:9091']

  - job_name: 'prometheus'
    static_configs:
      - targets: ['localhost:9090']
```

```powershell
# 启动监控栈
docker-compose -f docker-compose-monitoring.yml up -d

# 验证各组件
curl http://localhost:9090/api/v1/status/config    # Prometheus
curl http://localhost:9091/metrics                  # Pushgateway
curl http://localhost:3000/api/health               # Grafana
```

### 3.2 步骤二：开发 DataX Metrics Reporter 插件

**目标**：开发一个 DataX 的 Metrics Reporter，在 Job 完成时自动向 Pushgateway 推送指标。

**Metrics Reporter Maven 项目结构**：

```
datax-metrics-reporter/
├── pom.xml
├── plugin.json
└── src/main/java/com/example/datax/metrics/
    ├── MetricsReporter.java        # 核心推送逻辑
    ├── DataXJobHook.java           # 钩子，在 JobContainer.destroy() 中触发
    └── format/
        └── PrometheusFormatter.java # 指标格式化
```

**核心代码——MetricsReporter.java**：

```java
package com.example.datax.metrics;

import io.prometheus.client.*;
import io.prometheus.client.exporter.PushGateway;
import java.io.IOException;
import java.util.Map;

public class MetricsReporter {
    
    private static final String PUSHGATEWAY_URL = 
        System.getProperty("datax.metrics.pushgateway", "http://localhost:9091");
    
    private static final PushGateway pushGateway = new PushGateway(PUSHGATEWAY_URL);
    
    // 定义 Prometheus 指标
    private static final Gauge jobTotalRecords = Gauge.build()
        .name("datax_job_total_records")
        .help("Total records processed by the DataX job")
        .labelNames("job_id", "reader_plugin", "writer_plugin")
        .register();
    
    private static final Gauge jobTotalBytes = Gauge.build()
        .name("datax_job_total_bytes")
        .help("Total bytes processed by the DataX job")
        .labelNames("job_id")
        .register();
    
    private static final Gauge jobDurationSeconds = Gauge.build()
        .name("datax_job_duration_seconds")
        .help("Total duration of the DataX job in seconds")
        .labelNames("job_id")
        .register();
    
    private static final Gauge jobAverageSpeed = Gauge.build()
        .name("datax_job_average_speed_rec_per_sec")
        .help("Average records per second")
        .labelNames("job_id")
        .register();
    
    private static final Gauge taskCount = Gauge.build()
        .name("datax_task_total")
        .help("Total number of tasks in the job")
        .labelNames("job_id")
        .register();
    
    private static final Gauge taskFailedCount = Gauge.build()
        .name("datax_task_failed_total")
        .help("Number of failed tasks")
        .labelNames("job_id")
        .register();
    
    private static final Gauge taskAvgDurationSeconds = Gauge.build()
        .name("datax_task_avg_duration_seconds")
        .help("Average task duration in seconds")
        .labelNames("job_id")
        .register();
    
    private static final Gauge taskP99DurationSeconds = Gauge.build()
        .name("datax_task_p99_duration_seconds")
        .help("P99 task duration in seconds")
        .labelNames("job_id")
        .register();
    
    private static final Gauge dirtyRecordsTotal = Gauge.build()
        .name("datax_dirty_records_total")
        .help("Total number of dirty records")
        .labelNames("job_id")
        .register();
    
    private static final Gauge dirtyRecordPercent = Gauge.build()
        .name("datax_dirty_record_percent")
        .help("Percentage of dirty records")
        .labelNames("job_id")
        .register();
    
    private static final Gauge jvmHeapUsedBytes = Gauge.build()
        .name("datax_jvm_heap_used_bytes")
        .help("JVM heap memory used")
        .labelNames("job_id")
        .register();
    
    private static final Gauge channelUtilizationPercent = Gauge.build()
        .name("datax_channel_utilization_percent")
        .help("Channel buffer utilization percentage")
        .labelNames("job_id")
        .register();
    
    /**
     * 在 Job 完成时调用，将所有指标推送到 Pushgateway
     */
    public static void reportJobMetrics(Map<String, Object> jobMetrics) {
        String jobId = (String) jobMetrics.get("jobId");
        String readerPlugin = (String) jobMetrics.getOrDefault("readerPlugin", "unknown");
        String writerPlugin = (String) jobMetrics.getOrDefault("writerPlugin", "unknown");
        
        // 设置指标值
        safeSet(jobTotalRecords, jobId, readerPlugin, writerPlugin, 
                (Long) jobMetrics.get("totalRecords"));
        safeSet(jobTotalBytes, jobId, (Long) jobMetrics.get("totalBytes"));
        safeSet(jobDurationSeconds, jobId, 
                ((Long) jobMetrics.get("jobDurationMs")) / 1000.0);
        safeSet(jobAverageSpeed, jobId, (Long) jobMetrics.get("averageSpeed"));
        safeSet(taskCount, jobId, (Integer) jobMetrics.get("taskCount"));
        safeSet(taskFailedCount, jobId, (Integer) jobMetrics.get("failedTaskCount"));
        safeSet(taskAvgDurationSeconds, jobId, 
                ((Long) jobMetrics.get("taskAvgDurationMs")) / 1000.0);
        safeSet(taskP99DurationSeconds, jobId, 
                ((Long) jobMetrics.get("taskP99DurationMs")) / 1000.0);
        safeSet(dirtyRecordsTotal, jobId, (Long) jobMetrics.get("dirtyRecords"));
        safeSet(dirtyRecordPercent, jobId, 
                (Double) jobMetrics.get("dirtyRecordPercent"));
        safeSet(jvmHeapUsedBytes, jobId, (Long) jobMetrics.get("jvmHeapUsed"));
        safeSet(channelUtilizationPercent, jobId, 
                (Double) jobMetrics.get("channelUtilization"));
        
        // 推送到 Pushgateway（job 维度，用 jobId 区分）
        try {
            pushGateway.pushAdd(CollectorRegistry.defaultRegistry, 
                "datax_job_" + jobId, 
                Map.of("job_id", jobId, "instance", getHostname()));
        } catch (IOException e) {
            System.err.println("Failed to push metrics to Pushgateway: " + e.getMessage());
        }
    }
    
    private static void safeSet(Gauge gauge, String jobId, double value) {
        gauge.labels(jobId).set(value);
    }
    
    private static void safeSet(Gauge gauge, String jobId, String reader, 
                                 String writer, double value) {
        gauge.labels(jobId, reader, writer).set(value);
    }
    
    private static String getHostname() {
        try {
            return java.net.InetAddress.getLocalHost().getHostName();
        } catch (Exception e) {
            return "unknown";
        }
    }
}
```

### 3.3 步骤三：Grafana 监控大盘设计

**目标**：设计三个 Grafana Dashboard——总览大盘、单任务详情、性能瓶颈分析。

**Dashboard 1：DataX 同步总览（Overview）**

```
┌─────────────────────────────────────────────────────────────┐
│  DataX Sync Overview                    [Last 24h] [Refresh]│
├───────────────┬───────────────┬───────────────┬─────────────┤
│ Jobs Today    │ Success Rate  │ Avg Duration  │ Data Volume │
│     237       │    98.7%      │    12m 34s    │   3.2 TB    │
├───────────────┴───────────────┴───────────────┴─────────────┤
│ Job Status Distribution (Pie)                               │
│   ████████ 195 Success  ██ 23 Running  █ 12 Failed  █ 7 Idle│
├─────────────────────────────────────────────────────────────┤
│ Hourly QPS Trend (Line)                                     │
│   ╱╲  ╱╲                                                 │
│  ╱  ╲╱  ╲╱╲    Peak: 156,000 rec/s @ 03:15               │
│ ╱          ╲                                              │
├─────────────────────────────────────────────────────────────┤
│ Top 10 Slowest Jobs (Bar)                                   │
│ ████████████████████ orders_full_sync    48m 15s            │
│ ████████████████    logs_incremental     32m 08s            │
│ ███████████         users_sync           22m 41s            │
└─────────────────────────────────────────────────────────────┘
```

**Grafana 面板 JSON 配置（关键面板提取）**：

```json
{
  "dashboard": {
    "title": "DataX Sync Overview",
    "panels": [
      {
        "title": "Jobs Today",
        "type": "stat",
        "targets": [{
          "expr": "count(datax_job_total_records)"
        }],
        "fieldConfig": { "defaults": { "thresholds": { "steps": [
          {"color": "green", "value": 0}
        ]}}}
      },
      {
        "title": "Success Rate",
        "type": "stat",
        "targets": [{
          "expr": "100 - (count(datax_task_failed_total > 0) / count(datax_job_total_records) * 100)"
        }],
        "fieldConfig": { "defaults": { "thresholds": { "steps": [
          {"color": "red", "value": 0},
          {"color": "yellow", "value": 95},
          {"color": "green", "value": 99}
        ]}, "unit": "percent"}}
      },
      {
        "title": "Job Status Distribution",
        "type": "piechart",
        "targets": [{
          "expr": "count(datax_job_total_records) by (status)"
        }]
      },
      {
        "title": "Hourly QPS Trend",
        "type": "graph",
        "targets": [{
          "expr": "rate(datax_job_total_records[1h])",
          "legendFormat": "{{job_id}}"
        }]
      },
      {
        "title": "Top 10 Slowest Jobs",
        "type": "bargauge",
        "targets": [{
          "expr": "topk(10, datax_job_duration_seconds)",
          "legendFormat": "{{job_id}}"
        }]
      }
    ]
  }
}
```

**Dashboard 2：单任务 QPS 与延迟详情**

```
┌─────────────────────────────────────────────────────────────┐
│  Job Detail: orders_full_sync            [Last execution]   │
├───────────────┬───────────────┬───────────────┬─────────────┤
│ Total Records │ Total Bytes   │ Duration      │ QPS         │
│  58,230,000   │   22.4 GB     │   8m 33s      │ 113,436/s   │
├─────────────────────────────────────────────────────────────┤
│ QPS Over Time (Line)                                        │
│   ███████████████████████████████████████  steady           │
│              Peak: 127,800 rec/s                            │
├─────────────────────────────────────────────────────────────┤
│ Task Duration Distribution (Heatmap)                        │
│  Task-01  ████ 4.2m                                         │
│  Task-02  █████ 5.1m                                        │
│  ...                                                        │
│  Task-24  ██████████ 9.8m  ← 最慢 Task                      │
├─────────────────────────────────────────────────────────────┤
│ Channel Utilization (Gauge)                                 │
│  ████████████████████████████ 89.2%                         │
├─────────────────────────────────────────────────────────────┤
│ Dirty Records Trend                                         │
│  0 → 0 → 3 → 5 → 5  Total: 5  Percent: 0.000009%          │
└─────────────────────────────────────────────────────────────┘
```

**Dashboard 3：资源与 Channel 利用率**

```
┌─────────────────────────────────────────────────────────────┐
│  JVM Heap Memory (Line)                                     │
│  ████████████████░░░░  Used: 10.2GB / Max: 12GB (85%)     │
│  GC Pauses: avg 48ms, max 312ms                             │
├─────────────────────────────────────────────────────────────┤
│  Channel Buffer Utilization (Percent)                       │
│  ch-01 ████████████████████ 95%                             │
│  ch-12 ████████████████████ 92%                             │
│  ch-24 ████████████████████ 97% → 瓶颈!                      │
├─────────────────────────────────────────────────────────────┤
│  DB Connection Pool (Active / Idle)                         │
│  Reader Pool: ████████░░ 18/32                              │
│  Writer Pool: ████████████ 28/32                            │
└─────────────────────────────────────────────────────────────┘
```

### 3.4 步骤四：配置 Prometheus 告警规则

**目标**：基于 Prometheus AlertManager 实现三级告警（P1/P2/P3）。

**告警规则文件**（`prometheus-alerts.yml`）：

```yaml
groups:
  - name: datax_alerts
    interval: 30s
    rules:
      # === P1 级别：紧急告警 ===
      
      - alert: DataXJobConsecutiveFailures
        expr: |
          increase(datax_task_failed_total[15m]) >= 3
        for: 5m
        labels:
          severity: P1
          component: datax
        annotations:
          summary: "DataX Job {{ $labels.job_id }} 连续3次失败"
          description: "Job {{ $labels.job_id }} 在过去15分钟内失败了 {{ $value }} 次，需要立即介入处理。"
          action: "检查源端连接、目标端连接、数据格式错误；必要时暂时停止该Job的定时调度。"

      - alert: DataXJobQueueBacklog
        expr: |
          (count by (job_id) (datax_job_total_records > 0) > 0) 
          - (count by (job_id) (datax_job_duration_seconds > 0) > 0) > 20
        for: 10m
        labels:
          severity: P1
          component: datax
        annotations:
          summary: "DataX 任务积压超过20个"
          description: "当前有 {{ $value }} 个任务排队中，可能存在系统瓶颈。"
          action: "检查 Worker 节点资源、数据库连接数上限、调度器状态。"

      # === P2 级别：警告 ===

      - alert: DataXTaskSlowTask
        expr: |
          datax_task_p99_duration_seconds / datax_task_avg_duration_seconds > 5
        for: 5m
        labels:
          severity: P2
          component: datax
        annotations:
          summary: "DataX Job {{ $labels.job_id }} 存在超慢Task（P99/AVG > 5x）"
          description: "Job {{ $labels.job_id }} 的Task P99耗时 {{ $value }} 倍于平均值，可能存在数据倾斜。"
          action: "检查该Job的splitPk分布、目标端写入性能、是否有单一Task拉取了过多数据。"

      - alert: DataXDirtyRecordHighRate
        expr: |
          datax_dirty_record_percent > 1
        for: 3m
        labels:
          severity: P2
          component: datax
        annotations:
          summary: "DataX Job {{ $labels.job_id }} 脏数据率超过1%"
          description: "当前脏数据率为 {{ $value }}%，已超过阈值1%。"
          action: "检查源数据格式、类型映射、是否字段新增/删除导致空值；确认后调整errorLimit或修复数据。"

      - alert: DataXHighChannelUtilization
        expr: |
          datax_channel_utilization_percent > 95
        for: 10m
        labels:
          severity: P2
          component: datax
        annotations:
          summary: "Channel 利用率持续 > 95%，可能成为瓶颈"
          description: "Channel {{ $labels.channel_id }} 利用率 {{ $value }}%，建议增加channel数或提高限速。"

      # === P3 级别：提示 ===

      - alert: DataXJobDurationIncrease
        expr: |
          (datax_job_duration_seconds - datax_job_duration_seconds offset 7d) 
          / datax_job_duration_seconds offset 7d > 0.5
        for: 1h
        labels:
          severity: P3
          component: datax
        annotations:
          summary: "DataX Job {{ $labels.job_id }} 耗时较7天前增长50%+"
          description: "可能原因：数据量自然增长、源端/目标端性能退化、硬件资源争抢。"
          action: "评估是否需要增加channel数或升级硬件。"

  - name: datax_infra_alerts
    interval: 30s
    rules:
      - alert: DataXJVMHighMemoryUsage
        expr: |
          datax_jvm_heap_used_bytes / datax_jvm_heap_max_bytes > 0.9
        for: 5m
        labels:
          severity: P2
          component: jvm
        annotations:
          summary: "DataX JVM 堆内存使用率超过90%"
          description: "当前使用率 {{ $value | humanizePercentage }}，可能很快 OOM。"
          action: "考虑增大 -Xmx、减少 channel 数、或减小 batchSize。"
```

### 3.5 步骤五：AlertManager 告警配置与钉钉集成

**目标**：配置 AlertManager，将 Prometheus 告警自动推送到钉钉群。

**AlertManager 配置**（`alertmanager.yml`）：

```yaml
global:
  resolve_timeout: 5m

route:
  receiver: 'default-receiver'
  group_by: ['alertname', 'severity']
  group_wait: 10s
  group_interval: 30s
  repeat_interval: 1h
  routes:
    - match:
        severity: P1
      receiver: 'dingtalk-P1'
      repeat_interval: 5m
    - match:
        severity: P2
      receiver: 'dingtalk-P2'
      repeat_interval: 15m

receivers:
  - name: 'dingtalk-P1'
    webhook_configs:
      - url: 'http://webhook-adapter:8080/dingtalk/P1'
        send_resolved: true
        
  - name: 'dingtalk-P2'
    webhook_configs:
      - url: 'http://webhook-adapter:8080/dingtalk/P2'
        send_resolved: true

  - name: 'default-receiver'
    webhook_configs:
      - url: 'http://webhook-adapter:8080/dingtalk/default'
```

**钉钉 Webhook 适配器**（`dingtalk-webhook.py`，Flask 轻量服务）：

```python
from flask import Flask, request
import requests, json, time, hmac, hashlib, base64, urllib.parse

app = Flask(__name__)

# 钉钉机器人配置
DINGTALK_CONFIGS = {
    "P1": {
        "webhook": "https://oapi.dingtalk.com/robot/send?access_token=xxx",
        "secret": "SEC_P1_xxx",
        "at_mobiles": ["13800138000", "13900139000"],
        "is_at_all": False
    },
    "P2": {
        "webhook": "https://oapi.dingtalk.com/robot/send?access_token=yyy",
        "secret": "SEC_P2_yyy",
        "at_mobiles": [],
        "is_at_all": False
    },
}

def send_dingtalk(level, payload):
    config = DINGTALK_CONFIGS.get(level, DINGTALK_CONFIGS["P2"])
    
    # 构建钉钉 Markdown 消息
    alerts = payload.get("alerts", [])
    alert_texts = []
    for alert in alerts:
        status = alert["status"]
        name = alert["labels"]["alertname"]
        summary = alert["annotations"].get("summary", name)
        desc = alert["annotations"].get("description", "")
        action = alert["annotations"].get("action", "")
        emoji = "🔥" if level == "P1" else "⚠️" if level == "P2" else "ℹ️"
        alert_texts.append(
            f"{emoji} **[{status.upper()}] {summary}**\n\n"
            f"{desc}\n\n"
            f"> 行动建议：{action}"
        )
    
    markdown_text = "\n\n---\n\n".join(alert_texts)
    
    data = {
        "msgtype": "markdown",
        "markdown": {
            "title": f"DataX {level} 告警",
            "text": markdown_text
        },
        "at": {
            "atMobiles": config["at_mobiles"],
            "isAtAll": config["is_at_all"]
        }
    }
    
    # 钉钉加签
    timestamp = str(round(time.time() * 1000))
    secret_enc = config["secret"]
    string_to_sign = f"{timestamp}\n{config['secret']}"
    hmac_code = hmac.new(secret_enc.encode(), string_to_sign.encode(), hashlib.sha256).digest()
    sign = urllib.parse.quote_plus(base64.b64encode(hmac_code))
    
    webhook_url = f"{config['webhook']}&timestamp={timestamp}&sign={sign}"
    requests.post(webhook_url, json=data)

@app.route('/dingtalk/<level>', methods=['POST'])
def dingtalk_webhook(level):
    payload = request.json
    send_dingtalk(level, payload)
    return 'OK', 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080)
```

### 3.6 可能遇到的坑及解决方法

**坑1：Pushgateway 指标堆积**

Pushgateway 不会自动删除过期指标——如果 DataX 任务每天数千次，JobId 不断新增，Pushgateway 中的旧 Job 指标永远堆积。

```
解决: 在 Pushgateway 启动参数中加 --persistence.interval=0（不持久化）
      或在 MetricsReporter 中，Job 完成后 5 分钟自动 DELETE 该 Job 的指标:
        pushGateway.delete("datax_job_" + jobId)
```

**坑2：Prometheus 抓取频率过高导致 Pushgateway 压力大**

默认 15s 抓取一次，如果有 500+ JobId 的指标，每次抓取数据量较大。

```
解决: 调整 scrape_interval 为 30s
      使用 Prometheus 的 relabel_config 过滤掉 7 天前的旧指标
```

**坑3：Grafana 面板中 `rate()` 函数的行为误区**

`rate(datax_job_total_records[5m])` 是"过去 5 分钟的平均每秒增长率"，但如果这是一个 Gauge（而非 Counter），rate 函数计算结果不正确。

```
解决: 对于 Job 级别的一次性指标（如 totalRecords），使用 Gauge 类型，直接 show 值，不要用 rate()
      对于需要 rate 的指标（如瞬时 QPS），使用 Counter 类型，持续 inc()
```

## 4. 项目总结

### 4.1 监控栈方案对比

| 维度 | Filebeat+ES+Kibana | PerfTrace+Prometheus+Grafana | 自研 Agent |
|------|-------------------|------------------------------|-----------|
| DataX 改造成本 | 零（只采日志） | 低（埋 50 行） | 高（需开发 Agent） |
| 实时性 | 中（~10s） | 高（~1s） | 高 |
| 指标丰富度 | 低（需正则解析） | 高（任意自定义） | 最高 |
| 运维成本 | 中（ELK 栈） | 中（P+G 栈） | 高（自维护） |
| 适用规模 | 小型（< 50 任务） | 中型（50~500 任务） | 大型（500+） |

### 4.2 核心告警规则总结

| 告警 | 表达式（简述） | 级别 | 响应 |
|------|--------------|------|------|
| 连续 3 次失败 | 15min 内失败 >= 3 次 | P1 | 立即介入、暂停调度 |
| 超慢 Task | P99 耗时 / avg > 5x | P2 | 检查数据倾斜 |
| 脏数据超标 | 脏数据率 > 1% | P2 | 标记需校验 |
| Channel 满 | 利用率 > 95% | P2 | 增加 channel |
| 内存告警 | 堆使用率 > 90% | P2 | 扩容或降 channel |
| 耗时增长 | 较 7 天前增 50% | P3 | 周期性评估 |

### 4.3 优点

1. **从黑盒到白盒**：DataX 关键指标（QPS、延迟、脏数据率、资源利用率）全量可视化
2. **告警可操作**：每条告警规则都有明确的响应动作（检查什么、怎么修）
3. **Grafana 大盘专业化**：总览→单任务→资源分析三级下钻，从"有问题"到"定位问题"一步到位
4. **钉钉集成**：P1 级告警直接 @运维手机号，5 分钟内响应

### 4.4 缺点

1. **Pushgateway 不是理想方案**：Prometheus 设计为"拉模式"，Pushgateway 是妥协（只能用于临时 Job）。对于常驻的 DataX Service + Worker 架构，更好的方案是各 Worker 暴露 HTTP `/metrics` 端点让 Prometheus 来拉
2. **PerfTrace 埋点侵入**：需要在 DataX 源码中多次调用 `PerfTrace.getInstance().trace()`，破坏了核心代码的整洁性
3. **没有调用链追踪**：目前只能看到"Task-7 慢了 5 分钟"，但看不到慢在 Reader 读取、Channel 传输还是 Writer 写入阶段——需要更细粒度的 Span 埋点
4. **大量 JobId 导致指标基数爆炸**：每天数百个 JobId，Prometheus 的时序数据库会迅速膨胀

### 4.5 思考题

1. DataX 的 PerfTrace 原生只支持打印日志，不暴露 Metrics。如果要求你不修改 DataX 源码，仅通过 JMX MBean 暴露 JVM 级别的指标（堆内存、GC、线程数），你会选择哪个工具？（提示：JMX Exporter、Jolokia）
2. 假设 1000 个 Task 并发运行时，Prometheus 每 15 秒抓取一次所有指标——如果单次抓取耗时 20 秒（超过 scrape_interval），会发生什么？如何解决？

（答案见附录）
