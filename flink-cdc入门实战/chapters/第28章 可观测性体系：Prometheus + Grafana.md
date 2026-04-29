# 第28章 可观测性体系：Prometheus + Grafana

## 1 项目背景

### 业务场景：CDC作业"黑盒运行"的焦虑

Flink CDC作业上线后，运维团队面临三个焦虑：
1. **睡着了不知道**——作业已经反压（Backpressure）飙升15分钟了，但没有任何告警
2. **出了问题不知道根源**——是Source读太慢？还是下游Kafka写不了？
3. **故障恢复了不知道**——Checkpoint从失败恢复到正常了，但团队没人知道

第14章我们建立了基础的Web UI监控，但Web UI的监控有两个致命缺陷：**不持久化**（重启后历史指标消失）、**不告警**（不会发短信/钉钉）。

本章搭建基于**Prometheus + Grafana**的Flink CDC生产级监控体系。

### 监控架构

```
Flink CDC作业
    │
    ├─ flink-metrics-prometheus (Reporter)
    │   └─ HTTP端口暴露指标 (localhost:9250)
    │
    ▼
Prometheus (抓取指标)
    │
    ├─ 告警规则 → Alertmanager → 钉钉/企业微信/PagerDuty
    │
    ▼
Grafana (可视化)
    │
    ├─ CDC作业概览大盘
    ├─ CDC延迟追踪大盘
    └─ CDC资源消耗大盘
```

---

## 2 项目设计 · 三人交锋对话

**小胖**（疑惑）：第14章不是已经用Flink Web UI看Metrics了吗？为啥还要搞Prometheus + Grafana？

**大师**：Flink Web UI的Metrics只能看"当前时刻"的快照，不能看"过去24小时"的趋势。而且Web UI挂了（比如JobManager重启）所有的历史指标就没了。

Prometheus + Grafana解决了三个核心问题：
1. **指标持久化**：Prometheus将指标存储在TSDB中，可以查询任何历史时间点
2. **多维度告警**：PromQL可以写复杂的告警规则（如"延迟连续3分钟超过5秒"）
3. **自定义Dashboard**：Grafana可以将Source/Sink/Checkpoint/Binlog延迟放在同一个大屏上

**小白**：那Flink CDC有哪些独有的指标值得监控？我注意到Flink Web UI上的Source算子有`currentFetchDelay`，Prometheus能抓到吗？

**大师**：Flink CDC自定义的Metrics在Prometheus中也能获取到，前提是：
1. Flink 开启了Prometheus Reporter
2. Flink CDC连接器的JAR包中包含这些Metric注册代码

**关键监控指标清单：**

| 类别 | 指标名 | PromQL示例 | 说明 |
|------|-------|-----------|------|
| Source | `currentFetchDelay` | `flink_task_..._currentFetchDelay` | Binlog当前读取延迟 |
| Source | `numRecordsInPerSecond` | `rate(flink_task_..._numRecordsInPerSecond[1m])` | Source读取速率 |
| Sink | `numRecordsOutPerSecond` | `rate(flink_task_..._numRecordsOutPerSecond[1m])` | Sink写入速率 |
| 反压 | `backPressuredTimeMsPerSecond` | `flink_task_..._backPressuredTimeMsPerSecond` | 每秒反压毫秒数 |
| Checkpoint | `lastCheckpointDuration` | `flink_job_..._lastCheckpointDuration` | 最近Checkpoint耗时 |
| Checkpoint | `numberOfFailedCheckpoints` | `flink_job_..._numberOfFailedCheckpoints` | 失败Checkpoint数 |
| 资源 | `Status.JVM.Memory.Heap.Used` | `flink_task_..._Status.JVM.Memory.Heap.Used` | 堆内存使用量 |

**技术映射**：Flink Web UI像"车仪表盘"——你开车时看速度、油量。Prometheus+Grafana像"行车记录仪 + 4S店后台"——不仅实时看，还能追溯上周的驾驶行为、远程诊断故障。

---

## 3 项目实战

### 环境准备

**Docker Compose扩展（Prometheus + Grafana）：**
```yaml
prometheus:
  image: prom/prometheus:v2.45.0
  container_name: prometheus-cdc
  ports:
    - "9090:9090"
  volumes:
    - ./conf/prometheus/prometheus.yml:/etc/prometheus/prometheus.yml
    - prometheus_data:/prometheus
  networks:
    - flink-cdc-net

grafana:
  image: grafana/grafana:10.2.0
  container_name: grafana-cdc
  ports:
    - "3000:3000"
  environment:
    GF_SECURITY_ADMIN_PASSWORD: admin
  volumes:
    - grafana_data:/var/lib/grafana
  networks:
    - flink-cdc-net
```

**Prometheus配置：**
```yaml
# conf/prometheus/prometheus.yml
global:
  scrape_interval: 15s
  evaluation_interval: 15s

scrape_configs:
  - job_name: 'flink-cdc'
    scrape_interval: 10s
    metrics_path: '/'
    static_configs:
      - targets:
        - 'jobmanager:9250'           # JM的Prometheus端口
        - 'taskmanager:9250'          # TM的Prometheus端口
    # 过滤不需要的指标（减少存储量）
    metric_relabel_configs:
      - source_labels: [__name__]
        regex: 'flink_jobmanager_job_*|flink_taskmanager_job_*'
        action: keep
```

### 分步实现

#### 步骤1：配置Flink Prometheus Reporter

```yaml
# flink-conf.yaml（挂载到Flink容器）
metrics.reporter.prom.factory.class: org.apache.flink.metrics.prometheus.PrometheusReporterFactory
metrics.reporter.prom.port: 9250
metrics.reporter.prom.scope.variables.excludes: task_id;attempt;subtask_index

# CDC作业维度标签（Prometheus Label）
metrics.job.name.prefix: cdc_pipeline_
metrics.latency.interval: 10000
metrics.system.scope: operator
```

**重启Flink集群加载配置**（将flink-metrics-prometheus JAR放入lib目录）。

#### 步骤2：验证Prometheus抓取到Flink指标

```bash
# 1. 检查Prometheus Target状态
curl http://localhost:9090/api/v1/targets | grep flink
# 预期: state="UP"

# 2. 查询Flink CDC指标
curl 'http://localhost:9090/api/v1/query?query=flink_taskmanager_job_task_operator_numRecordsInPerSecond'
# 预期: 返回当前Source每秒读取的记录数

# 3. 查询currentFetchDelay（Flink CDC特有指标）
curl 'http://localhost:9090/api/v1/query?query=flink_taskmanager_job_task_operator_currentFetchDelay'
# 预期: 返回Source的Binlog延迟（毫秒）
```

#### 步骤3：自定义Metric——发送到Prometheus

```java
package com.example;

import org.apache.flink.api.common.functions.RichMapFunction;
import org.apache.flink.configuration.Configuration;
import org.apache.flink.metrics.Counter;
import org.apache.flink.metrics.Histogram;
import org.apache.flink.metrics.SimpleCounter;
import org.apache.flink.metrics.util.SlidingWindowHistogram;

/**
 * CDC业务指标上报——事件大小分布、操作类型比例
 */
public class BusinessMetricsMapper extends RichMapFunction<String, String> {

    // 计数器
    private Counter eventSizeTotal;
    private Counter eventCount;
    
    // 直方图——事件大小的分布
    private Histogram eventSizeHistogram;

    @Override
    public void open(Configuration parameters) {
        // 事件大小累加器
        eventSizeTotal = getRuntimeContext()
            .getMetricGroup()
            .counter("cdc_event_bytes_total");
        
        eventCount = getRuntimeContext()
            .getMetricGroup()
            .counter("cdc_event_count");

        // 滑动窗口直方图（最近120秒的事件大小分布）
        eventSizeHistogram = getRuntimeContext()
            .getMetricGroup()
            .histogram("cdc_event_bytes_histogram",
                new SlidingWindowHistogram(120));
    }

    @Override
    public String map(String value) throws Exception {
        int bytes = value.getBytes().length;
        eventSizeTotal.inc(bytes);
        eventCount.inc();
        eventSizeHistogram.update(bytes);
        return value;
    }
}
```

**在Prometheus中查询自定义指标：**
```promql
# 事件总字节数
flink_taskmanager_job_task_operator_cdc_event_bytes_total

# 平均事件大小（字节/事件）
rate(flink_taskmanager_job_task_operator_cdc_event_bytes_total[1m])
/ 
rate(flink_taskmanager_job_task_operator_cdc_event_count[1m])
```

#### 步骤4：配置Prometheus告警规则

```yaml
# conf/prometheus/alert-rules.yml
groups:
  - name: flink-cdc-alerts
    rules:
      # 告警1：Binlog延迟 > 10秒
      - alert: CdcHighLatency
        expr: flink_taskmanager_job_task_operator_currentFetchDelay > 10000
        for: 2m
        labels:
          severity: critical
        annotations:
          summary: "Flink CDC Binlog延迟超过10秒"
          description: "Source算子 {{ $labels.task_id }} 延迟 = {{ $value }}ms"

      # 告警2：Checkpoint连续失败3次
      - alert: CdcCheckpointFailures
        expr: rate(flink_jobmanager_job_numberOfFailedCheckpoints[5m]) > 0.01
        for: 3m
        labels:
          severity: critical
        annotations:
          summary: "Flink CDC Checkpoint持续失败"
          description: "过去5分钟Checkpoint失败率 = {{ $value }}"

      # 告警3：Source积压严重
      - alert: CdcBackpressureHigh
        expr: flink_taskmanager_job_task_operator_backPressuredTimeMsPerSecond > 800
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "Flink CDC算子反压严重"
          description: "算子 {{ $labels.operator_name }} 反压持续5分钟 > 800ms/s"

      # 告警4：TaskManager堆内存使用率 > 85%
      - alert: CdcHighMemoryUsage
        expr: (flink_taskmanager_Status_JVM_Memory_Heap_Used / flink_taskmanager_Status_JVM_Memory_Heap_Max) > 0.85
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "TaskManager堆内存使用率过高"
          description: "实例 {{ $labels.instance }} 堆内存使用率 = {{ $value | humanizePercentage }}"
```

#### 步骤5：创建Grafana Dashboard

从Grafana导入Flink Dashboard模板或手动创建：

```json
{
  "dashboard": {
    "title": "Flink CDC 作业监控",
    "panels": [
      {
        "title": "Source吞吐 (记录/秒)",
        "type": "graph",
        "targets": [{
          "expr": "rate(flink_taskmanager_job_task_operator_numRecordsInPerSecond{operator_name=~'.*Source.*'}[1m])",
          "legendFormat": "Source: {{operator_name}}"
        }]
      },
      {
        "title": "Binlog延迟 (ms)",
        "type": "graph",
        "targets": [{
          "expr": "flink_taskmanager_job_task_operator_currentFetchDelay",
          "legendFormat": "延迟: {{task_id}}"
        }],
        "yaxis": { "min": 0 }
      },
      {
        "title": "Checkpoint耗时",
        "type": "graph",
        "targets": [{
          "expr": "flink_jobmanager_job_lastCheckpointDuration",
          "legendFormat": "CP耗时"
        }]
      },
      {
        "title": "反压状态",
        "type": "graph",
        "targets": [{
          "expr": "flink_taskmanager_job_task_operator_backPressuredTimeMsPerSecond",
          "legendFormat": "{{operator_name}}反压"
        }]
      }
    ]
  }
}
```

#### 常见陷坑及解决方法

| 问题 | 原因 | 解决方法 |
|------|------|---------|
| Prometheus中Flink Target为DOWN | Flink Prometheus Reporter端口未暴露或未配置 | 检查`metrics.reporter.prom.port`配置，确认Docker端口映射 |
| Grafana上数据点为0 | PromQL聚合函数使用不当 | 使用`rate()`（速率）而非`increase()`（增量） |
| `currentFetchDelay`无法查到 | Flink CDC版本不支持（2.x没有该指标） | 升级到Flink CDC 3.x，或使用自定义Metric |
| 告警触发后一直不恢复 | 告警规则的`for`参数与恢复条件不匹配 | 检查告警的`for`持续时间，确保指标低于阈值后能自动恢复 |

---

## 4 项目总结

### 监控体系对比

| 监控方案 | 持久化 | 告警 | 趋势分析 | 部署复杂度 |
|---------|-------|------|---------|-----------|
| Flink Web UI | ❌ | ❌ | ❌ | 0（内置） |
| Prometheus + Grafana | ✅ 30天 | ✅ | ✅ | 中 |
| 第三方监控（Datadog/NewRelic） | ✅ | ✅ | ✅ | 高（付费） |
| 自研监控 | ✅ | ✅ | ✅ | 高 |

### 推荐告警阈值汇总

| 告警项 | 表达式 | Warning | Critical |
|--------|-------|---------|---------|
| Binlog延迟 | `currentFetchDelay` | > 5s持续2分钟 | > 30s持续1分钟 |
| Checkpoint失败 | `numberOfFailedCheckpoints` | > 3次/15分钟 | > 5次/15分钟 |
| 反压 | `backPressuredTimeMsPerSecond` | > 500ms/秒持续5分钟 | > 900ms/秒持续2分钟 |
| 堆内存使用率 | `Heap.Used / Heap.Max` | > 75% | > 85% |
| 数据吞吐波动 | `numRecordsInPerSecond` | 下降 > 50%对比1小时前 | 下降 > 90% |

### 常见踩坑经验

**故障案例1：Prometheus抓取Flink指标时出现大量重复系列**
- **现象**：Prometheus中每个指标对应数百个Series（series基数爆炸）
- **根因**：Flink的子任务标签（`subtask_index`、`task_id`、`attempt`）导致同一个逻辑指标有几十个独立的Series
- **解决方案**：在Prometheus配置中使用`metric_relabel_configs`排除高基数标签，或在Flink配置中设置`metrics.reporter.prom.scope.variables.excludes`

**故障案例2：Grafana告警触发频繁但非真实问题**
- **现象**：`currentFetchDelay`每隔几小时就短时触发告警，但实际上是全量快照和增量切换时的正常波动
- **根因**：未设置告警的`for`持续时间，导致指标一次抖动就触发告警
- **解决方案**：所有告警规则添加`for: 2m`（持续2分钟才触发），过滤短时抖动

**故障案例3：自定义Metric在Prometheus中找不到**
- **现象**：代码中注册了`cdc_event_bytes_total`，但Prometheus中找不到
- **根因**：Metric的Scope（作用域）配置不正确，导致Metrics被分组到看不见的路径下
- **解决方案**：在`flink-conf.yaml`中设置`metrics.system.scope: operator`，并在Grafana中使用通配符查询`{__name__=~".*cdc_event.*"}`

### 思考题

1. **进阶题①**：Flink CDC作业的`currentFetchDelay`在什么情况下可能显示为0（即使实际上有延迟）？提示：考虑`latest-offset`启动模式和Checkpoint恢复后的初始状态。

2. **进阶题②**：在Prometheus中，`rate()`和`irate()`函数有什么区别？对于Flink CDC的吞吐监控（每秒记录数），应该使用哪个函数？如果使用`avg_over_time()`对`numRecordsInPerSecond`做1小时平均，能反映什么问题？

---

> **下一章预告**：第29章「生产级部署：Kubernetes与YARN」——从开发环境到生产环境，Flink CDC的部署模式如何选择？本章将对比Session/Application/Per-Job三种模式，实战K8s Application模式提交和YARN Application模式部署。
