# 第25章：可观测性三件套——Metrics+Logging+Tracing

---

## 1. 项目背景

某公司的Flink生产集群上运行着30+个作业。运维团队经常面临这些问题：

- 凌晨3点告警说"作业失败"——但重启后就恢复了。为什么会挂？不知道。
- 作业吞吐从10万/秒降到100/秒——查了半小时发现是Redis慢查询导致反压——但为什么没有提前告警？
- 某个作业的Checkpoint经常超时——但检查所有参数都正常。是HDFS抖动还是网络问题？

这些问题的共性是：**Flink作业运行像一个黑盒——你不知道内部发生了什么，直到出了事故才能看到后果**。

**可观测性（Observability）** 就是把这个黑盒变成透明的。核心三件套：

| 组件 | 作用 | Flink对应 |
|------|------|-----------|
| **Metrics** | 数值化监控指标 | Flink Metric System + Reporter |
| **Logging** | 事件日志 | SLF4J + Log4j2 |
| **Tracing** | 请求全链路追踪 | 自定义 + 第三方集成 |

---

## 2. 项目设计

> 场景：运维同事拿着手机冲进研发区——"作业挂了，但我不知道什么时候挂的、为什么挂的，救救我！"

**大师**：你除了重启之前看过Metrics吗？Checkpoint失败次数、Heap使用率、GC次数、反压比——这些数据在Flink WebUI上都有，但你们没有配置持久化和告警。

**小胖**：我看了WebUI，但它是"当前"的快照，挂了之后就看不到了。我要的是挂了之后还能查历史Metrics。

**大师**：所以需要把Metrics上报到外部时序数据库（Prometheus/InfluxDB/Grafana），历史数据持久化。

**技术映射：Flink Metric Reporter = 将Flink内部的监控指标推送到外部存储。支持Prometheus、JMX、Slf4j等。Prometheus + Grafana是最主流的方案。**

**小白**：那Logging呢？Flink作业的日志分散在几十个TaskManager上，每次查日志都要kubectl exec进容器？或者去YARN上翻Logs？

**大师**：生产环境必须做**集中式日志管理**。ELK（Elasticsearch + Logstash + Kibana）或EFK（Fluentd替代Logstash）。Flink使用Log4j2，配置SocketAppender或Filebeat将日志发送到集中式日志平台。

**技术映射：集中式日志 = Flink的logs目录下的日志文件通过Filebeat/Fluentd采集 → Kafka → Elasticsearch → Kibana可视化。可以在Kibana中按jobId/taskId/subtaskIndex搜索。**

**小胖**：Tracing这东西我在微服务里见过——每个请求有一个Trace ID。Flink里也有吗？

**大师**：Flink没有内置的分布式追踪。但如果你想追踪"一条数据从Kafka进来到Sink出去的全链路"，可以手动注入Trace ID——在Source生成一个唯一的traceId，在算子间传递，在关键节点打印日志或输出Metrics。

---

## 3. 项目实战

### 分步实现

#### 步骤1：配置Prometheus Metric Reporter

**目标**：将Flink Metrics上报到Prometheus。

```properties
# flink-conf.yaml
# ========== Prometheus Reporter ==========
metrics.reporter.prom.factory.class: org.apache.flink.metrics.prometheus.PrometheusReporterFactory
metrics.reporter.prom.port: 9250-9260
metrics.reporter.prom.scope.variables.excludes: job_id
metrics.reporter.prom.filter.out: "^.*(rocksdb).*$"
```

**Maven依赖**：

```xml
<dependency>
    <groupId>org.apache.flink</groupId>
    <artifactId>flink-metrics-prometheus</artifactId>
    <version>${flink.version}</version>
</dependency>
```

```bash
# 重启Flink集群后，每个TaskManager暴露一个Metrics端口
# JobManager: 9250, TaskManager: 9251, 9252, ...
curl http://taskmanager:9251/metrics | head -20
```

#### 步骤2：关键监控指标清单

**目标**：了解哪些指标必须监控、阈值多少。

```java
// ========== 作业级别 ==========
// 作业状态（RUNNING/FAILED/RESTARTING）
// 告警：非RUNNING状态 > 30秒 → 告警

// ========== 吞吐指标 ==========
flink_taskmanager_job_task_numRecordsOutPerSecond    // 每秒输出记录数
flink_taskmanager_job_task_numRecordsInPerSecond     // 每秒输入记录数
// 告警：输出 < 历史均值的20% → 告警

// ========== Checkpoint指标 ==========
flink_jobmanager_job_numberOfFailedCheckpoints       // 失败数
flink_jobmanager_job_lastCheckpointDuration          // 最近一次耗时(ms)
// 告警：失败数连续≥3次 或 耗时 > 5分钟 → 告警

// ========== 反压指标 ==========
flink_taskmanager_job_task_isBackPressured           // 是否反压（布尔）
flink_taskmanager_job_task_busyTimeMsPerSecond        // 每秒忙碌时间(ms)
// 告警：busyTime > 800ms持续5分钟 → 告警

// ========== JVM指标 ==========
flink_taskmanager_job_task_Status_JVM_CPU_Load        // CPU使用率
flink_taskmanager_job_task_Status_JVM_GC_Time          // GC耗时
flink_taskmanager_job_task_Status_JVM_Heap_Used        // 堆使用量
// 告警：Heap > 85% → 告警；GC time > 20% → 告警

// ========== 自定义指标 ==========
// 通过RichFunction的MetricGroup暴露业务指标
// 如：每分钟处理订单数、黑名单命中率、延迟分布
```

#### 步骤3：集中的日志收集（EFK）

**目标**：配置Flink日志通过Fluentd发送到Elasticsearch。

```properties
# log4j-console.properties（在Flink conf目录）
# 增加Socket Appender发送到Logstash/Fluentd

# Socket Appender（JSON格式）
appender.socket.name = SocketAppender
appender.socket.type = Socket
appender.socket.port = 4560
appender.socket.host = logstash-host
appender.socket.layout.type = JSONLayout
appender.socket.layout.compact = true
appender.socket.layout.eventEol = true
appender.socket.layout.properties = true
appender.socket.filter.threshold.type = ThresholdFilter
appender.socket.filter.threshold.level = INFO

rootLogger.level = INFO
rootLogger.appenderRef.socket.ref = SocketAppender
```

#### 步骤4：自定义Metrics——业务级指标

**目标**：在代码中暴露业务指标，用于告警和监控。

```java
package com.flink.column.chapter25;

import org.apache.flink.api.common.functions.RichMapFunction;
import org.apache.flink.configuration.Configuration;
import org.apache.flink.metrics.Counter;
import org.apache.flink.metrics.Gauge;
import org.apache.flink.metrics.Histogram;
import org.apache.flink.metrics.Meter;
import org.apache.flink.streaming.api.datastream.DataStream;
import org.apache.flink.streaming.api.environment.StreamExecutionEnvironment;
import org.apache.flink.dropwizard.metrics.DropwizardHistogramWrapper;
import com.codahale.metrics.SlidingWindowReservoir;

/**
 * 自定义业务指标：监控处理延迟和错误率
 */
public class CustomMetricsDemo {

    public static void main(String[] args) throws Exception {
        StreamExecutionEnvironment env = StreamExecutionEnvironment.getExecutionEnvironment();
        env.setParallelism(2);

        DataStream<String> source = env.socketTextStream("localhost", 9999);

        source.map(new RichMapFunction<String, String>() {

            private Counter recordCounter;
            private Counter errorCounter;
            private Meter throughputMeter;
            private Histogram latencyHistogram;
            private transient long lastRecordTime;

            @Override
            public void open(Configuration parameters) {
                // 1. Counter（累计值）
                recordCounter = getRuntimeContext()
                        .getMetricGroup().counter("record_count");
                
                errorCounter = getRuntimeContext()
                        .getMetricGroup().counter("error_count");

                // 2. Meter（速率）
                throughputMeter = getRuntimeContext()
                        .getMetricGroup().meter("throughput");

                // 3. Histogram（分布——用Dropwizard实现）
                com.codahale.metrics.Histogram dropwizardHistogram =
                        new com.codahale.metrics.Histogram(
                                new SlidingWindowReservoir(1000));
                latencyHistogram = getRuntimeContext()
                        .getMetricGroup().histogram("process_latency",
                                new DropwizardHistogramWrapper(dropwizardHistogram));

                // 4. Gauge（瞬时值）
                getRuntimeContext().getMetricGroup()
                        .gauge("last_processing_time_ms", (Gauge<Long>) () ->
                            System.currentTimeMillis() - lastRecordTime);

                lastRecordTime = System.currentTimeMillis();
            }

            @Override
            public String map(String value) throws Exception {
                long start = System.nanoTime();
                recordCounter.inc();
                throughputMeter.markEvent();

                try {
                    // 模拟处理
                    String result = "processed: " + value.toUpperCase();
                    long latency = (System.nanoTime() - start) / 1_000_000;
                    latencyHistogram.update(latency);
                    lastRecordTime = System.currentTimeMillis();
                    return result;
                } catch (Exception e) {
                    errorCounter.inc();
                    throw e;
                }
            }
        }).print();

        env.execute("Chapter25-CustomMetrics");
    }
}
```

在Prometheus中可以看到自定义指标：

```
# HELP flink_taskmanager_job_task_operator_record_count
# TYPE flink_taskmanager_job_task_operator_record_count counter
flink_taskmanager_job_task_operator_record_count{task_name="Map"} 10423

# HELP flink_taskmanager_job_task_operator_process_latency
flink_taskmanager_job_task_operator_process_latency{quantile="0.5"} 12.0
flink_taskmanager_job_task_operator_process_latency{quantile="0.95"} 45.0
flink_taskmanager_job_task_operator_process_latency{quantile="0.99"} 128.0
```

#### 步骤5：Grafana Dashboard配置

**目标**：创建Flink监控大屏。

```json
// Grafana Dashboard JSON（简略版）
{
  "title": "Flink作业监控",
  "panels": [
    {
      "title": "吞吐(条/秒)",
      "type": "graph",
      "targets": [
        {
          "expr": "rate(flink_taskmanager_job_task_numRecordsOutPerSecond[1m])",
          "legendFormat": "{{task_name}}"
        }
      ]
    },
    {
      "title": "Checkpoint耗时",
      "type": "graph",
      "targets": [
        {
          "expr": "flink_jobmanager_job_lastCheckpointDuration",
          "legendFormat": "耗时"
        }
      ]
    },
    {
      "title": "反压比例",
      "type": "heatmap",
      "targets": [
        {
          "expr": "flink_taskmanager_job_task_busyTimeMsPerSecond / 1000",
          "legendFormat": "{{task_name}}"
        }
      ]
    }
  ]
}
```

### 可能遇到的坑

1. **Prometheus Reporter端口冲突**
   - 根因：多个TaskManager在同一节点时，Prometheus端口（9250-9260）可能被占满
   - 解方：使用Docker/K8S动态端口分配；或每个JM/TM指定独立端口

2. **自定义Metrics过多导致Prometheus OOM**
   - 根因：每个key、每个累加器的唯一标签组合太多
   - 解方：限制自定义Metric的标签基数；使用`filter.out`排除不需要的metrics

3. **日志量过大导致Elasticsearch存储暴涨**
   - 根因：Flink的DEBUG日志也发送到集中式日志
   - 解方：只发送WARN及以上级别的日志到集中式；INFO日志保留在本地文件

---

## 4. 项目总结

### 可观测性三件套

| 组件 | 工具 | 核心价值 | 告警配置 |
|------|------|---------|---------|
| Metrics | Prometheus + Grafana | 数值趋势 | Checkpoint失败次数、反压比例 |
| Logging | ELK/EFK | 事件回溯 | ERROR日志出现频率 |
| Tracing | 自定义TraceId | 全链路追踪 | 单条数据延迟 > 阈值 |

### 必须监控的指标Top 10

1. 作业状态（RUNNING/FAILED）
2. Checkpoint完成时间 & 失败次数
3. 吞吐（recordsIn/Out per second）
4. 反压比（busyTimeMs / 1000）
5. Kafka Lag
6. JVM Heap使用率
7. GC次数 & GC耗时
8. TaskManager CPU使用率
9. RocksDB读写延迟
10. 自定义业务指标（如错误率）

### 注意事项
- Metrics和Logging都有性能开销——不要全量开启所有指标，按需开启
- 自定义Metrics的Gauge不要做重量级计算——它每次被Prometheus拉取时都执行
- 日志中的敏感信息（用户ID、IP）需要脱敏后再写入集中式日志

### 常见踩坑经验

**案例1：Prometheus拉取Metrics返回空数据但端口能连上**
- 根因：Prometheus的`scrape_interval` > Flink Metrics的刷新间隔
- 解方：配置prometheus的`scrape_interval: 15s`；Flink侧`metrics.reporter.prom.interval: 30 SECONDS`

**案例2：Kibana中搜不到某个TaskManager的日志**
- 根因：该TM的日志文件切割（log rotation）后Filebeat没有读取新文件
- 解方：检查Filebeat的`close_inactive`配置；确认日志文件的inode没有变化（Docker场景下常见）

**案例3：自定义Histogram在Prometheus中只看到中位数（0.5 quantile），看不到P99**
- 根因：DropwizardHistogramWrapper只计算了部分分位数
- 解方：使用Flink内置Histogram或配置`dropwizardHistogramReservoir`参数

### 优点 & 缺点

| | Flink可观测体系（Prometheus+ELK+自定义Metrics） | 裸Flink WebUI（无外部集成） |
|------|-----------|-----------|
| **优点1** | Metrics持久化到Prometheus，历史趋势可查 | WebUI只显示当前快照，失败后不可追溯 |
| **优点2** | 集中式日志（ELK/EFK），跨TaskManager统一搜索 | 日志分散在各节点，排查需逐个登录 |
| **优点3** | Grafana大屏可视化，自定义告警规则 | 纯人工轮询WebUI，无法自动告警 |
| **优点4** | 自定义业务指标暴露到Prometheus，监控精细化 | 仅有Flink内置指标 |
| **缺点1** | 部署维护复杂——需要搭建Prometheus/Grafana/ELK栈 | 开箱即用，零部署成本 |
| **缺点2** | 过多自定义指标导致Prometheus OOM | 无额外存储开销 |

### 适用场景

**典型场景**：
1. 生产环境全链路监控——Metrics+日志+告警三位一体
2. 故障根因分析——结合Checkpoint耗时、GC、反压的历史趋势定位根因
3. 容量规划——查看吞吐和资源的历史峰值，指导扩缩容
4. SLA保障——Checkpoint完成率、作业可用性等SLA指标持续监控

**不适用场景**：
1. 单机开发测试——WebUI已足够，搭建ELK过于浪费
2. 短期临时作业——运行数小时的作业，Prometheus存储开销不划算

### 思考题

1. Metrics中Counter和Meter的区别是什么？Counter用来监控"累计处理了多少条数据"，Meter用来监控"当前处理速率"——如果我只想看"1分钟的速率"，可以用Counter自己算吗？怎么算？

2. 你发现某个作业的Checkpoint有时耗时1分钟有时5分钟——你想找到"Checkpoint慢了"时集群发生了什么。你会看哪些Metrics？哪些日志？如果需要做根因分析，应该先看什么再看什么？

---

> **完整代码**：本章完整代码请参考附录或访问 https://github.com/flink-column/flink-practitioner  
> **思考题答案**：见附录文件 `appendix-answers.md`
