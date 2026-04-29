# 第14章 监控初探：日志、Metrics与Flink Web UI

## 1 项目背景

### 业务场景：CDC作业异常了，怎么定位问题？

Flink CDC作业上线一周后，业务方反馈："今天下午的订单同步好像慢了15分钟"。运维同学打开Flink Web UI，面对一堆指标——`Records Received`、`Records Sent`、`Backpressure Status`、`Checkpoint Duration`——完全不知道从哪看起。

缺乏可观测性的CDC作业就像"黑盒运行"——你知道它在跑，但不知道它跑得好不好。当问题发生时（延迟增大、吞吐下降、数据丢失），唯一能做的就是重启作业。

本章从Flink Web UI的基础指标出发，建立Flink CDC作业的可观测性体系。

### Flink CDC作业的监控维度

```
┌────────────────────────────────────────────────────────┐
│                 Flink CDC 可观测性体系                    │
│                                                         │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌────────┐ │
│  │ 延迟指标  │  │ 吞吐指标  │  │ 资源指标  │  │ 稳定性 │ │
│  │          │  │          │  │          │  │        │ │
│  │·延迟(ms) │  │·记录/秒  │  │·CPU使用率 │  │·CP成功 │ │
│  │·积压     │  │·字节/秒  │  │·内存使用  │  │·CP耗时 │ │
│  │·Lag     │  │·事件/秒  │  │·网络IO   │  │·重启次数│ │
│  └──────────┘  └──────────┘  └──────────┘  └────────┘ │
└────────────────────────────────────────────────────────┘
```

---

## 2 项目设计 · 三人交锋对话

**小胖**（挠头）：Flink Web UI打开后花花绿绿的，我就看得懂一个"Running"——作业在跑就行。出问题了我就重启，反正有Checkpoint嘛。

**大师**：这就像开车只看得懂"仪表盘亮不亮"——车能走就行。但你不知道油还剩多少（积压）、发动机温度（Checkpoint耗时）、转速是否异常（反压）。

Flink Web UI上的关键指标可以分为5类：

**1. 作业状态指标：**
- `状态: RUNNING / FAILED / CANCELLED` —— 是否正常运行
- `重启次数: N` —— 如果>3次说明有持续异常
- `运行时间: 5d 12h` —— 稳定运行时间

**2. 吞吐指标：**
- `numRecordsInPerSecond` —— 每秒接收记录数
- `numRecordsOutPerSecond` —— 每秒发送记录数
- `numBytesInPerSecond` / `numBytesOutPerSecond` —— 网络吞吐

**3. 延迟指标：**
- `currentFetchDelay` —— Source读取当前Binlog位置的滞后（仅CDC作业）
- `lag` —— Source端积压的未处理数据量
- `Watermark`延迟 —— 事件时间处理下的水位线滞后

**4. 反压指标：**
- `Backpressure Status: OK / LOW / HIGH` —— 反压等级
- `inPoolUsage` —— 网络Buffer占用率，>0.8表示反压

**5. Checkpoint指标：**
- `duration` —— Checkpoint耗时
- `stateSize` —— Checkpoint状态大小
- `alignedProcessingTime` —— 对齐耗时

**小白**：那我怎么把Flink CDC的"当前Binlog延迟"（currentFetchDelay）可视化出来？总不能天天盯着Web UI看吧？

**大师**：Flink提供了**Metrics Reporter**机制，可以把各种指标上报到外部监控系统。最常用的是Prometheus Reporter。

配置步骤：
1. 将`flink-metrics-prometheus-1.20.3.jar`放到Flink的lib目录下
2. 在`flink-conf.yaml`中配置Prometheus Reporter
3. Prometheus定期拉取Flink的Metrics端点
4. Grafana配置Prometheus数据源，创建可视化大盘

但这里有个关键点——Flink CDC的`currentFetchDelay`不是默认Flink Metric，它是Flink CDC的**自定义Metric**。你需要确保Flink CDC连接器的JAR包中包含了这个Metric定义。

**技术映射**：Flink Metrics像"汽车的OBD接口"——Flink暴露了所有可观测的数据，但你需要一个读码器（Prometheus Reporter）和一个显示屏（Grafana）才能看到。

---

## 3 项目实战

### 环境准备

**Flink配置添加Prometheus Reporter：**

```yaml
# conf/flink-conf.yaml
metrics.reporter.prom.factory.class: org.apache.flink.metrics.prometheus.PrometheusReporterFactory
metrics.reporter.prom.port: 9250-9260
metrics.system.scope: operator
metrics.reporter.prom.filter.variables: ";"
metrics.reporter.prom.scope.variables.excludes: task_id;attempt;subtask_index
```

**Docker Compose新增Prometheus + Grafana：**
```yaml
prometheus:
  image: prom/prometheus:latest
  container_name: prometheus-cdc
  ports:
    - "9090:9090"
  volumes:
    - ./conf/prometheus/prometheus.yml:/etc/prometheus/prometheus.yml
  networks:
    - flink-cdc-net

grafana:
  image: grafana/grafana:latest
  container_name: grafana-cdc
  ports:
    - "3000:3000"
  environment:
    - GF_SECURITY_ADMIN_PASSWORD=admin
  networks:
    - flink-cdc-net
```

**Prometheus配置：**
```yaml
# conf/prometheus/prometheus.yml
scrape_configs:
  - job_name: 'flink'
    static_configs:
      - targets: ['jobmanager:9250', 'taskmanager:9250']
    metrics_path: '/'
```

### 分步实现

#### 步骤1：Flink Web UI指标解读（关键训练）

打开 `http://localhost:8081`，选择你的CDC作业，分三步定位问题：

**第一步：看概述页**
```
Job Overview:
  Status: RUNNING                  ← 最重要的——不是RUNNING就是有问题
  Restarts: 0                      ← 重启次数>1说明不稳定
  Duration: 2d 14h 30m             ← 运行时间
  Total Duration of Checkpoints:   ← 最近Checkpoint耗时
    Last: 842ms                    ← 正常（< 1秒）
    Average: 756ms                 ← 正常（< 1秒）
  Number of Failed Checkpoints: 0  ← 失败Checkpoint数
  Latest Failed Checkpoint: N/A    ← 有记录说明Checkpoint异常
```

**第二步：看算子级别指标**

点击任意算子（如Source算子），查看Detail：
```
numRecordsIn: 12,345,678,900       ← 处理的总记录数
numRecordsOut: 12,345,678,900      ← 输出应与输入接近
numRecordsInPerSecond: 23,456      ← 当前每秒输入速率
numRecordsOutPerSecond: 23,400     ← 当前每秒输出速率，略低于输入正常
currentFetchDelay: 123             ← Source的Binlog延迟（毫秒）
backPressuredTimeMsPerSecond: 0    ← 反压时间（毫秒/秒）
```

**第三步：反压定位**

点击"Backpressure"标签：
```
Backpressure Status:
  Source: MySQL CDC → OK           ← Source无反压
  Map → Sink: Print to Std. Out    ← LOW (反压轻微)
                                   ← HIGH (反压严重，需要关注)
```

**反压的典型传播路径：**
```
Source (OK) → Transform (OK) → Sink (HIGH)
                                └── Sink写入慢导致反压一直传到Source
                                    → Source被迫暂停读取 → 延迟上升
                                    → 最终可能导致Checkpoint超时 → 作业失败
```

#### 步骤2：自定义Metric——监控CDC事件的操作类型分布

```java
package com.example;

import org.apache.flink.api.common.functions.RichMapFunction;
import org.apache.flink.configuration.Configuration;
import org.apache.flink.metrics.Counter;
import org.apache.flink.metrics.Meter;
import org.apache.flink.metrics.MeterView;

/**
 * 自定义Metric监控：统计INSERT/UPDATE/DELETE事件的比例
 */
public class CdcEventMetricsMapper extends RichMapFunction<String, String> {

    private transient Counter insertCounter;
    private transient Counter updateCounter;
    private transient Counter deleteCounter;
    private transient Meter throughputMeter;

    @Override
    public void open(Configuration parameters) {
        // 注册计数器——在Flink Web UI和Prometheus中可见
        insertCounter = getRuntimeContext()
            .getMetricGroup()
            .counter("cdc_insert_count");

        updateCounter = getRuntimeContext()
            .getMetricGroup()
            .counter("cdc_update_count");

        deleteCounter = getRuntimeContext()
            .getMetricGroup()
            .counter("cdc_delete_count");

        // 注册吞吐率Meter
        throughputMeter = getRuntimeContext()
            .getMetricGroup()
            .meter("cdc_throughput", new MeterView(60)); // 60秒滑动窗口
    }

    @Override
    public String map(String json) throws Exception {
        throughputMeter.markEvent(); // 记录一条事件

        if (json.contains("\"op\":\"c\"") || json.contains("\"op\":\"r\"")) {
            insertCounter.inc();
        } else if (json.contains("\"op\":\"u\"")) {
            updateCounter.inc();
        } else if (json.contains("\"op\":\"d\"")) {
            deleteCounter.inc();
        }
        return json;
    }
}
```

**使用方式：**
```java
DataStream<String> cdcStream = env.fromSource(...);
cdcStream.map(new CdcEventMetricsMapper()).print();
```

**在Prometheus中查到的指标：**
```
flink_taskmanager_job_task_operator_cdc_insert_count{...} 12345
flink_taskmanager_job_task_operator_cdc_update_count{...} 6789
flink_taskmanager_job_task_operator_cdc_delete_count{...} 123
flink_taskmanager_job_task_operator_cdc_throughput{...} 23456
```

#### 步骤3：配置Flink CDC的日志级别

Flink CDC的日志通常调试时需要更详细的信息：

```yaml
# conf/log4j.properties
# Flink CDC内部Debezium引擎的日志（调试用）
logger.debezium = DEBUG, file
logger.debezium.name = io.debezium

# MySQL Binlog连接器日志
logger.mysql_cdc = DEBUG, file
logger.mysql_cdc.name = org.apache.flink.cdc.connectors.mysql

# Schema History日志
logger.schema_history = DEBUG, file
logger.schema_history.name = io.debezium.relational.history

# 文件Appender统一日志级别
rootLogger.level = INFO
```

#### 步骤4：CDC作业核心监控SQL（基于Flink Web UI）

```sql
-- 这些不是真正的SQL，而是你在Web UI上应该问自己的问题

-- 问题1：Source的瞬时读数是多少？
-- 查看: numRecordsInPerSecond (Source算子)
-- 如果突然降到0→检查MySQL连接受否中断

-- 问题2：反压是否严重？
-- 查看: backPressuredTimeMsPerSecond (每个算子)
-- 如果>100ms/秒 → 需要关注

-- 问题3：Checkpoint是否超时？
-- 查看: Checkpoints页→Duration列
-- 如果接近timeout设置值 → 检查状态大小和反压

-- 问题4：Binlog延迟是多少？
-- 查看: currentFetchDelay (Source算子)
-- 如果持续>1000ms → Source读取落后了

-- 问题5：有没有OOM风险？
-- 查看: TaskManager → Metrics → Heap / NonHeap内存使用
-- 如果使用率持续>85% → 需要增加内存
```

#### 常见陷坑及解决方法

| 问题 | 原因 | 解决方法 |
|------|------|---------|
| Web UI上Metrics不显示 | Metric Scope配置错误或未注册 | 检查`metrics.system.scope`配置，确认Metric注册成功 |
| Prometheus拉取不到数据 | Flink Prometheus Reporter端口未暴露 | 确保Prometheus的`sacrape_configs`中的targets地址可访问 |
| currentFetchDelay一直为0 | 启动模式为latest，刚开始读取 | 等Source开始读取后延迟正常显示 |
| backPressure状态标HIGH但不影响吞吐 | 短时的Buffer背压，很快自动恢复 | 观察是否持续HIGH超过30秒 |
| Checkpoint失败次数增长 | 反压导致Barrier对齐超时 | 开启Unaligned Checkpoint或增大Checkpoint超时 |

---

## 4 项目总结

### Flink CDC可观测性体系

| 类别 | 指标 | 获取方式 | 正常值 | 告警阈值 |
|------|------|---------|-------|---------|
| 作业状态 | 作业状态 | Web UI | RUNNING | != RUNNING |
| | 重启次数 | Web UI | 0 | >3次/小时 |
| 吞吐 | 每秒输入记录数 | Web UI / Prometheus | 根据数据量 | 突然降为0 |
| | 网络吞吐（字节/秒） | Web UI | 与预期一致 | 突然降为0 |
| 延迟 | currentFetchDelay | Web UI Source Metrics | < 100ms | > 10000ms (10秒) |
| | numRecordsIn vs Out差值 | Web UI | 接近 | 差异>10% |
| 反压 | backPressuredTimeMsPerSecond | Web UI | 0 | > 500ms/s持续5分钟 |
| Checkpoint | Duration | Web UI Checkpoints | < 5秒 | > timeout的80% |
| | Failed Count | Web UI Checkpoints | 0 | > 3个连续失败 |
| 资源 | Heap Memory Used | Web UI TaskManager | < 70% | > 85% |
| | RocksDB写入速率 | Prometheus | 稳定 | 突发飙升 |

### 注意事项

1. **Metric覆盖度**：Flink自带的Metrics不够，需要添加Flink CDC的自定义Metrics（currentFetchDelay等）和业务逻辑Metrics（INSERT/UPDATE/DELETE比例）。
2. **日志轮转**：长时间运行的CDC作业会产生大量日志，配置`log4j.appender.file.MaxFileSize=100MB`和`MaxBackupIndex=10`。
3. **报警分级**：Checkpoint失败触发P0告警（立即响应），反压高触发P1告警（30分钟内响应），延迟增加触发P2告警（2小时响应）。

### 常见踩坑经验

**故障案例1：currentFetchDelay持续增长但无告警**
- **现象**：运维发现时currentFetchDelay已增长到15分钟，但无告警
- **根因**：只监控了作业状态（RUNNING），没有监控延迟指标
- **解决方案**：配置Prometheus告警规则`flink_taskmanager_job_task_operator_currentFetchDelay > 10000`，延迟>10秒告警

**故障案例2：RocksDB L0层文件堆积导致Checkpoint变慢**
- **现象**：Checkpoint耗时从1秒逐渐增长到3分钟
- **根因**：RocksDB的L0层SST文件数不断积累，Compaction速度跟不上写入速度
- **解决方案**：调整RocksDB参数`state.backend.rocksdb.thread.num=4`增加Compaction线程，或`level0_slowdown_writes_trigger=30`

**故障案例3：Grafana大盘上所有数据点为0**
- **现象**：Prometheus可以查到数据，Grafana上全是0
- **根因**：Grafana的时间范围和Prometheus的Bucket设置不匹配，或使用了错误的PromQL聚合函数（应该用`rate()`而非`increase()`）
- **解决方案**：使用`rate(flink_taskmanager_job_task_operator_numRecordsInPerSecond[1m])`查询每1分钟的平均速率

### 思考题

1. **进阶题①**：在Flink CDC作业中，如果Source的`currentFetchDelay`增加到5000ms，但Transform和Sink的反压为OK，问题出在哪里？应该如何定位？提示：考虑MySQL的Binlog dump线程性能、网络带宽、server-id冲突等。

2. **进阶题②**：Flink的`rate()`和`increase()`在PromQL中的区别是什么？对于CDC场景的吞吐监控（每秒记录数），应该使用哪个函数？如果使用`sum()`聚合多个并行subtask的指标，应该注意什么？

---

> **下一章预告**：第15章「综合实战：MySQL实时数据双写」——学完基础篇后的一次综合实战，整合前面的所有知识，完成一个生产级别的实时数据双写系统：MySQL订单数据→同时写入Kafka（事件驱动）+ MySQL备库（灾备），包含全量初始化、增量续接、监控告警的全流程。
