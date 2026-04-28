# 第36章：SRE落地——SLA保障与多活容灾

---

## 1. 项目背景

Flink作业上线后，SRE团队需要回答三个灵魂拷问：

1. **作业挂了多久恢复？**——Recovery Time Objective（RTO）
2. **挂了丢多少数据？**——Recovery Point Objective（RPO）
3. **怎么保证不挂？**——Service Level Agreement（SLA）

对于核心交易链路（如风控、计费），SLA要求通常是：
- 可用性 99.99%（年度故障 < 52分钟）
- RTO ≤ 5分钟
- RPO = 0（不丢数据）

要达成这个级别的SLA，单靠一个Flink集群是不够的——需要**多活容灾架构**。

---

## 2. 项目设计

> 场景：上海机房光缆被挖断，Flink集群整体下线。业务停了18分钟——远超SLA。

**大师**：单机房部署的风险就在于——任何级别的机房故障都会导致Flink集群不可用。解决方法是**跨机房双活或主备架构**。

**技术映射：Flink多活容灾的三种模式——① 冷备（Cold Standby）：备机房不运行作业，主挂了手动拉起 ② 温备（Warm Standby）：备机房运行但不消费数据，主挂了从最新Checkpoint拉起 ③ 双活（Active-Active）：两个机房同时运行作业同时消费数据，下游双写。**

**小白**：那双活模式下数据一致性问题怎么解决？两个机房同时写入同一个MySQL表，肯定会有冲突。

**大师**：双活模式不要求两个机房写入同一个存储——通常采用**双写分流 + 下游Merge**：

```
机房A: KafkaA → FlinkA → HBaseA
机房B: KafkaB → FlinkB → HBaseB
下游: 统一查询网关 → Read HBaseA / HBaseB → Merge结果
```

或者**主备模式（Active-Standby）**更常用——机房A是主，机房B是从。主挂后B自动拉起：

```
Kafka (跨机房复制) → 机房A (主) → 写HBase
                    → 机房B (备，从Checkpoint启动)
```

**技术映射：Flink跨机房容灾的关键 = Checkpoint的可移植性。将Checkpoint写入跨机房共享存储（S3/HDFS），备机房从相同的Checkpoint恢复。**

**小胖**：那SLA的量化指标怎么在Flink上监控？我们目前只看了作业状态。

**大师**：需要建立SLA监控体系：

| 指标 | 计算方式 | 告警阈值 | 对应SLA |
|------|---------|---------|---------|
| 作业可用性 | 1 - 非RUNNING时间/总时间 | 99.9% | 可用性 |
| 恢复时间 | 从FAILED到RUNNING的时间 | >5min | RTO |
| 数据延迟 | 最新输出的eventTime - 当前时间 | >60s | 新鲜度 |
| Checkpoint成功间隔 | 连续成功Checkpoint的时间跨度 | >30min | 稳定性 |

---

## 3. 项目实战

### 分步实现

#### 步骤1：作业分级SLA体系

**目标**：按作业重要性分级，设定不同的SLA和告警策略。

```java
package com.flink.column.chapter36.sla;

/**
 * 作业分级体系
 */
public enum JobTier {
    P0("核心交易链路", "99.99%", "5min", 0),
    P1("重要分析", "99.9%", "30min", 1),
    P2("一般计算", "99%", "2h", 2),
    P3("探索性实验", "无SLA", "24h", 3);

    public final String description;
    public final String slaUptime;
    public final String slaRto;
    public final int priority;

    JobTier(String desc, String uptime, String rto, int pri) {
        this.description = desc;
        this.slaUptime = uptime;
        this.slaRto = rto;
        this.priority = pri;
    }
}
```

**分级管理配置**：

```yaml
# deploy-config.yaml
jobs:
  - name: "order-payment-reconciliation"
    tier: P0
    checkpoint:
      interval: 10s
      timeout: 5min
      max-failures: 2
    alert:
      channels: ["dingtalk", "sms", "phone"]
      on: ["job_failed", "checkpoint_failed_more_than_3"]

  - name: "user-behavior-etl"
    tier: P2
    checkpoint:
      interval: 60s
      timeout: 30min
      max-failures: 10
    alert:
      channels: ["email"]
      on: ["job_failed"]
```

#### 步骤2：跨机房主备架构实现

**目标**：配置Flink作业实现跨机房主备故障转移。

```bash
# ========== 架构设计 ==========
# 主机房（sh）：正常运行Flink作业，写Checkpoint到S3
# 备机房（bj）：不运行作业，等待接管

# ========== 关键配置 ==========
# 1. Checkpoint写入跨机房共享存储（S3）
state.checkpoints.dir: s3://shared-bucket/flink-checkpoints
state.backend.incremental: true

# 2. 启用Savepoint自动触发（定时Savepoint到S3）
# 用于主备切换时的状态恢复
# 通过Flink Operator或cronjob每小时触发一次
./bin/flink savepoint <jobId> s3://shared-bucket/flink-savepoints

# ========== 故障切换步骤 ==========

# Step 1: 检测到主机房故障（5次心跳丢失）
# Step 2: 备机房从最新Savepoint启动Flink作业
./bin/flink run -t yarn-application \
  -s s3://shared-bucket/flink-savepoints/savepoint-<latest> \
  -Dhigh-availability.storageDir=s3://shared-bucket/flink-ha \
  -c MainClass /jobs/job.jar

# Step 3: 备机房的Kafka Consumer从最新的Offset开始消费
# （Offset也保存在Savepoint中）

# Step 4: 通知下游切换数据源到备机房的Flink输出
```

#### 步骤3：SLA监控系统

**目标**：建立基于Prometheus + Grafana的SLA监控大屏。

```java
// 自定义SLA计算逻辑
package com.flink.column.chapter36;

import org.apache.flink.api.common.functions.RichMapFunction;
import org.apache.flink.configuration.Configuration;
import org.apache.flink.metrics.Gauge;
import org.apache.flink.streaming.api.datastream.DataStream;
import org.apache.flink.streaming.api.environment.StreamExecutionEnvironment;

/**
 * SLA指标采集：计算数据延迟（data freshness）
 */
public class SLAMetrics {

    public static void main(String[] args) throws Exception {
        StreamExecutionEnvironment env = StreamExecutionEnvironment.getExecutionEnvironment();

        env.socketTextStream("localhost", 9999)
            .map(new RichMapFunction<String, String>() {
                private transient Gauge<Long> dataFreshnessGauge;

                @Override
                public void open(Configuration parameters) {
                    // 数据新鲜度 = 当前时间 - 最新数据的EventTime
                    dataFreshnessGauge = getRuntimeContext()
                            .getMetricGroup()
                            .gauge("data_freshness_ms", () -> {
                                // 假设从State中获取最新数据的eventTime
                                long latestEventTime = getLatestEventTimeFromState();
                                return System.currentTimeMillis() - latestEventTime;
                            });
                }

                private long getLatestEventTimeFromState() {
                    // 实际从ValueState中读取
                    return System.currentTimeMillis();
                }

                @Override
                public String map(String value) throws Exception {
                    return value;
                }
            });

        env.execute("Chapter36-SLAMetrics");
    }
}
```

**Prometheus告警规则**：

```yaml
# prometheus-alerts.yml
groups:
  - name: flink-sla
    rules:
      # P0作业Checkpoint失败超过3次
      - alert: FlinkP0CheckpointFailed
        expr: flink_jobmanager_job_numberOfFailedCheckpoints{job_tier="P0"} > 3
        for: 2m
        labels: { severity: critical }
        annotations: { summary: "P0作业Checkpoint连续失败" }

      # 数据延迟超过60秒
      - alert: FlinkDataFreshnessBreach
        expr: flink_taskmanager_job_task_operator_data_freshness_ms > 60000
        for: 1m
        labels: { severity: warning }
        annotations: { summary: "数据延迟超过SLA阈值" }

      # 作业不可用
      - alert: FlinkJobDown
        expr: flink_jobmanager_job_status{job_tier=~"P0|P1"} != 3
        for: 30s
        labels: { severity: critical }
        annotations: { summary: "P0/P1作业不在运行状态" }
```

#### 步骤4：自动弹性伸缩

**目标**：根据负载自动调整Flink作业的并行度。

```yaml
# Flink Operator支持自动缩放（Flink 1.17+）
# 需要在FlinkDeployment中配置autoScaling
spec:
  flinkConfiguration:
    job.autoscaler.enabled: "true"
    job.autoscaler.scaling.enabled: "true"
    job.autoscaler.stabilization.interval: 5m
    job.autoscaler.metrics.window: 10m
    job.autoscaler.target.utilization: 0.7
    job.autoscaler.target.utilization.boundary: 0.3
```

#### 步骤5：SRE Runbook——故障响应SOP

**目标**：制定标准化的故障处理流程。

```
# Flink作业故障响应SOP

## 1. 告警触发
- P0告警：电话 + 短信 + 钉钉（1分钟内响应）
- P1告警：短信 + 钉钉（5分钟内响应）

## 2. 故障确认
- 检查作业状态（WebUI / REST API）
- 检查TaskManager心跳
- 检查HDFS/S3连通性

## 3. 故障定级
- 单TaskManager故障 → Region Failover自动恢复
- 集群整体故障 → 启动备机房
- Source/Sink故障 → 切换备用通道

## 4. 恢复操作
- 单TaskManager：等待自动恢复（通常<30秒）
- 集群故障：手动从Savepoint恢复
  ./bin/flink run -s <latest-savepoint> ...
- 数据校验：对比Kafka Lag和Checkpoint进度

## 5. 事后复盘
- 根因分析：查看日志和Metrics
- 改进措施：更新Runbook、调整配置
- 报告时间：故障后24小时内输出
```

### 可能遇到的坑

1. **跨机房Checkpoint共享存储的网络延迟**
   - 根因：跨机房访问S3/HDFS的延迟远高于同机房（可能从5ms变成200ms）
   - 解决：使用S3的跨区域复制功能；或部署本地近线存储（NFS + 异步复制）

2. **Active-Active双活模式下数据冲突**
   - 根因：两个Flink作业同时处理同一条数据（如从同一Kafka Topic消费）
   - 解方：每个作业使用不同的Consumer Group；下游做幂等Merge

3. **自动弹性伸缩导致作业频繁Savepoint/恢复**
   - 根因：负载波动频繁，Auto Scaler反复调整并行度，每次调整触发一次Savepoint
   - 解方：增加stabilization.interval（稳定期），配置告警而非自动触发

---

## 4. 项目总结

### SLA保障体系

```
架构层：多机房（主备/双活） + 共享存储
    │
监控层：Prometheus + Grafana + 自定义SLA指标
    │
告警层：分级告警（P0电话/P1短信/P2钉钉）
    │
响应层：SOP Runbook + 自动恢复 + On-Call值班
    │
复盘层：故障报告 + 根因分析 + 改进跟踪
```

### 容灾模式对比

| 模式 | RTO | RPO | 成本 | 复杂度 |
|------|-----|-----|------|--------|
| 冷备 | 数十分钟 | 0（Checkpoint） | 低（备机不下数据） | 低 |
| 温备 | 数分钟 | 0 | 中（备机半负载） | 中 |
| 双活 | 秒级 | 0 | 高（全量资源×2） | 高 |

### 注意事项
- SLA不是越高越好——99.99%比99.9%的运维成本高10倍
- RPO=0需要端到端Exactly-Once，且Sink必须支持幂等
- 弹性伸缩对状态作业影响大——Savepoint + 恢复需要时间，可能导致短暂不可用

### 常见踩坑经验

**案例1：跨机房恢复后Checkpoint一直在PENDING状态**
- 根因：备机房的TaskManager无法访问主机房的Checkpoint存储路径（防火墙策略）
- 解方：使用OSS/S3等跨机房可访问的存储；配置跨账号访问策略

**案例2：弹性伸缩后作业从Savepoint恢复时，Kafka Offset丢失**
- 根因：并行度变化导致Kafka Source的分区分配发生变化
- 解方：确认Flink版本≥1.16（修复了并行度变化时Source State的兼容性问题）

**案例3：P0作业故障时，5分钟内人工恢复不了**
- 根因：人工操作步骤太多（登录 → 查Savepoint → 提交 → 验证）
- 解方：将恢复流程脚本化、自动化（如通过K8S Operator自动触发备集群拉起）

### 优点 & 缺点

| | 多活容灾+SLA保障体系 | 单集群无容灾 |
|------|-----------|-----------|
| **优点1** | 跨机房主备/双活，机房级故障自动切换 | 机房级故障导致整体不可用 |
| **优点2** | SLA分级监控+分级告警，P0作业优先保障 | 所有作业同等对待，核心作业无差异 |
| **优点3** | SOP Runbook标准化故障响应流程 | 故障处理依赖个人经验，风险高 |
| **缺点1** | 成本高——双活需2x资源，温备也需额外开销 | 成本最低 |
| **缺点2** | 架构复杂度高——备机切换、数据一致性挑战 | 简单架构，无需跨机房协调 |

### 适用场景

**典型场景**：
1. 核心交易链路（风控/计费/支付）——必须达到99.99%可用性
2. 需要跨机房容灾的企业——主备/双活架构减少单点故障风险
3. 作业分级管理——P0/P1/P2/P3不同SLA不同告警策略
4. 自动化SRE运维——Runbook + 自动弹性伸缩

**不适用场景**：
1. 非关键业务探索性作业——单集群即可，无需SLA保障
2. 中小团队/初创公司——多活架构运维成本过高

### 思考题

1. Active-Active双活模式下，两个机房各自消费Kafka的不同分区（half of partitions per DC）。如果一个机房挂了，另一个机房如何接管所有分区的消费？Kafka的Consumer Rebalance机制能自动处理吗？

2. SLA监控的"数据新鲜度"指标（当前时间 - 最新数据的eventTime）在什么情况下会虚高或虚低？比如作业没有新数据进来时，这个指标会不断增长——这时候是真的"延迟"还是"正常"？

---

> **完整代码**：本章完整代码请参考附录或访问 https://github.com/flink-column/flink-practitioner  
> **思考题答案**：见附录文件 `appendix-answers.md`
