# 第18章：Flink实时任务调度与常驻任务管理

> **定位**：深入理解DolphinScheduler对Flink流式任务的调度原理，掌握常驻任务生命周期管理与savepoint恢复机制。
> **核心内容**：Flink task类型配置、常驻任务RUNNING态管理、savepoint触发与恢复、YARN/K8s集群联动、监控与告警策略。
> **实战目标**：为大麦电商搭建"实时异常检测→会话归因→ClickHouse写入"的Flink流处理调度链路，实现故障自愈与优雅停启。

---

## 1. 项目背景

大麦电商的日活用户突破800万，每秒产生约5000条用户行为事件。业务方提了四个实时处理需求：(1) 从Kafka消费用户点击流原始事件；(2) 实时会话归因——将同一用户30分钟内的行为事件聚合为一个Session；(3) 实时异常检测——识别刷单、欺诈下单等异常模式；(4) 每5分钟一次窗口聚合，将指标写入ClickHouse供运营Dashboard消费。

数据团队已经用Flink写好了流处理Job——`RealtimeAnomalyDetector`。但目前的上线方式相当原始：开发小哥每次SSH到Flink集群跳板机，手动执行`flink run -d anomaly-detector.jar`，然后打开Flink Web UI盯着Job是否RUNNING。这带来四个痛点：(1) Flink集群重启后，Job不会自动拉起——凌晨3点YARN做了资源回收，第二天早上一看Dashboard全空白；(2) 与批处理调度体系割裂——每日凌晨的T+1批处理报表和实时流处理是两套系统，数据对账需要人工介入；(3) 没有统一告警——Flink Job挂了没人知道，直到运营在群里@技术负责人"看板又没数据了"；(4) 运行时配置（并行度、savepoint目录、TaskManager内存）散落在Wiki、聊天记录和开发小哥的笔记本里，无法版本化管理。

CTO的疑问直击要害："我们已经在用DolphinScheduler管所有批处理任务了，能不能把Flink流处理也统一纳管进去？但Flink任务一跑就是几个月不退出，DS的工作流实例会一直卡在'运行中'——这个机制真的适合流处理吗？"

---

## 2. 项目设计——剧本式交锋对话

**场景**：技术分享室，白板上画着一半批处理DAG、一半Flink流处理拓扑。小胖刚从Flink Web UI切回来，一脸困惑。

---

**小胖**（嘴里嚼着薯片，指着屏幕上的Flink Web UI）：

> "Flink不是自带Web UI吗？Job提交、cancel、savepoint全都有。直接在上面操作不就完了，为啥非要绕一道DS？这不是多此一举嘛！你看，绿色RUNNING、蓝色FINISHED、红色FAILED，一目了然。DS那个笨重的界面，提交个JAR包还要先上传到资源中心——直接`flink run`不香吗？"

---

**小白**（眉头紧锁，在本子上快速画了一个时间轴）：

> "胖哥你先别急。Flink Web UI是好用，但我有四个问题。第一，Flink Job跑了一个月突然挂了——Flink UI会弹个红色小框告诉你'Transitioned to FAILED'，但它不会自动重启。DS能自动重新提交吗？第二，savepoint怎么管？Flink的cancel+savpoint是两步操作，DS怎么封装成'暂停'和'恢复'？第三，DS的定时调度是每分钟触发一次，但Flink常驻任务一跑就是几个月——定时触发还有意义吗？工作流实例一直RUNNING着，DS的系统资源会不会被拖垮？第四，如果YARN上Flink集群本身挂了，DS能不能感知？还是说只能等DS的Worker发现任务超时？"

---

**大师**（从口袋里掏出一个智能家居遥控器放在桌上）：

> "好问题。你们看看这个遥控器和你手机上的智能家居App——遥控器只能控制一台电视，智能家居App却能同时管理电视、空调、灯光、窗帘，还能设定联动规则：'如果门锁被打开，玄关灯自动亮起'；'如果烟雾传感器报警，空调自动关闭'。Flink Web UI就是遥控器——它的控制域仅限于一个Flink集群上的Job操作。DS就是智能家居App——它不直接控制Flink，而是在更高一层做统筹。"

> "小胖你问'为什么不直接用Flink Web UI'——因为你的系统不是一个孤立的Flink Job。假设凌晨3点这个`RealtimeAnomalyDetector`挂了，你要做什么？①自动从savepoint重启Flink Job；②如果重启3次还失败，发钉钉告警给值班；③同时，通知下游那个每小时运行一次的'数据对账'Shell脚本暂停执行，因为上游实时数据断了。这三件事Flink Web UI一件也做不了——它的边界就到'Job FAILED'为止。DS可以。"

> **技术映射**：DS管理Flink任务是"元调度"（Meta-Scheduling）——DS不参与Flink内部的Task调度，而是在更高的编排层做生命周期管理（提交、监控、失败重试、savepoint触发、级联通知）。Flink Web UI是Job级管理工具，DS是平台级编排引擎。

**小胖**（放下薯片，若有所思）：

> "那常驻任务一直RUNNING，DS的定时调度不就形同虚设了吗？我设了个Cron'每天2点执行'，但Flink Job第一次提交后就一直在跑，第二天2点DS再触发一次——不就提交了两个同样的Job？"

---

**大师**（在白板上画了两条平行的时间线）：

> "这是Flink任务和批处理任务最本质的区别。批处理任务的生命周期：**START → RUNNING → SUCCESS**，每次定时触发都产生一个新的实例，一个'短周期'循环。Flink常驻任务的生命周期：**START → RUNNING → RUNNING → RUNNING……**，它不会主动走向SUCCESS。DS对这个差异的处理方式是——**Flink任务节点只有一次提交，后续DS进入'监控模式'**。"

> "具体来说：DS在第一次Cron触发时向YARN/K8s提交Flink Job，拿到Application ID后直接进入RUNNING_EXECUTION状态。之后的Cron触发周期，DS不会再重复提交——它会检查对应Application ID的Flink Job是否仍然RUNNING。如果是，就继续等待；如果发现FAILED，就触发失败重试策略；如果你手动暂停，就触发savepoint+cancel。换句话说，**定时调度的角色从'到点启动'变成了'到点巡检'**。"

> **技术映射**：DS内部用`t_ds_process_instance`表的`state`字段区分——批处理任务到达终态（SUCCESS/FAILURE）后实例结束；Flink任务的实例在`RUNNING_EXECUTION`状态下持久存在，直到用户手动Kill或Job异常退出。Master的`FailoverService`会定期扫描运行中的Flink任务关联的YARN/K8s Job状态，实现心跳检测。

---

**小白**（推了推眼镜，追问最棘手的部分）：

> "savepoint的配合呢？DS的'暂停'和'恢复暂停'对应到Flink是什么动作？还有失败重试的时候，怎么保证从最新的savepoint恢复而不是重新开始——那可是要丢数据的。"

---

**大师**（拿起白板笔，在Flink那一半画了一个状态机）：

> "问得精准。DS做了三层契约封装："

> "**暂停操作** = `flink stop --savepointPath hdfs:///flink/savepoints/xxx <jobId>` + 等待savepoint完成 + 记录savepoint路径到DS元数据库。DS不是直接`flink cancel`（那会丢状态），而是先触发savepoint，等它成功后再cancel。你可以在Workflow实例页点'暂停'，也可以配置'超时自动暂停'。"

> "**恢复暂停** = 从元数据库读取上次记录的savepoint路径 + `flink run -s <savepointPath> jar`。注意这里不是`flink resume`——因为Job已被cancel，resume不适用。DS自动帮你拼接了`-s`参数，用户无感知。"

> "**失败重试** = DS检测到Flink Job异常退出后，先从元数据库查最近的savepoint路径，然后重新提交`flink run -s <savepointPath>`。这里有两个关键配置：`失败重试次数`和`失败重试间隔`。如果savepoint不存在（比如刚启动1秒就挂了），DS会退回到无状态启动并发出'无savepoint可恢复'的告警。"

> **技术映射**：savepoint路径存储在`t_ds_task_instance`扩展字段中。DS的Flink Task Plugin在提交命令时动态拼接`-s`或`--fromSavepoint`参数。这一逻辑在`FlinkTaskExecutionContext`中构造，不依赖Flink的HA配置。

**小胖**（恍然大悟，一拍大腿）：

> "我懂了！那什么场景应该交给DS管Flink，什么场景不该？总不能开发的时候每改一行代码都去DS上提交一次吧？"

**大师**：

> "说对了——**开发迭代阶段不该用DS**。你在IDE里改完代码，打好新的JAR包，要立刻验证效果——这时候用`flink run`最快，DS只会拖慢你。DS的价值在**生产运维阶段**。另外，Flink SQL的即席查询，应该走Flink自带的SQL Client或Zeppelin——DS的任务节点不适合做交互式分析。"

> "给你三条判断标准：①这个Flink Job需要和别的任务（Shell、SQL、Spark）编排吗？②需要自动故障恢复和告警吗？③需要多环境（开发/测试/生产）统一管理运行时配置吗？三个YES就用DS，两个NO就先用Flink原生工具。"

---

## 3. 项目实战

### 环境准备

- DolphinScheduler 3.x 集群模式已部署（至少1个Master + 2个Worker）
- YARN集群（Hadoop 3.x）或Kubernetes集群可用
- Flink 1.15+已安装在所有Worker节点上，且`$FLINK_HOME/bin`在PATH中
- HDFS可用，用于存储checkpoint和savepoint
- Kafka集群已运行，topic `orders`已创建

在Worker节点上验证Flink和YARN可用：

```bash
# 所有Worker节点执行
flink --version                # 确认Flink CLI可用
yarn application -list         # 确认YARN连接正常
hdfs dfs -mkdir -p /flink/savepoints /flink/checkpoints
hdfs dfs -chmod 777 /flink/savepoints /flink/checkpoints
```

---

### Step 1：准备Flink流处理Job（10分钟）

编写实时异常检测Job并打包上传：

```java
// RealtimeAnomalyDetector.java
package com.damai.flink;

import org.apache.flink.api.common.eventtime.WatermarkStrategy;
import org.apache.flink.api.common.serialization.SimpleStringSchema;
import org.apache.flink.connector.kafka.source.KafkaSource;
import org.apache.flink.connector.kafka.source.enumerator.initializer.OffsetsInitializer;
import org.apache.flink.streaming.api.datastream.DataStream;
import org.apache.flink.streaming.api.environment.StreamExecutionEnvironment;
import org.apache.flink.streaming.api.functions.ProcessFunction;
import org.apache.flink.util.Collector;

public class RealtimeAnomalyDetector {
    public static void main(String[] args) throws Exception {
        StreamExecutionEnvironment env = StreamExecutionEnvironment.getExecutionEnvironment();
        // 每分钟一次checkpoint，保证故障后状态可恢复
        env.enableCheckpointing(60000);
        // 状态后端设为HDFS，checkpoint自动存储
        env.setStateBackend(new org.apache.flink.runtime.state.hashmap.HashMapStateBackend());
        env.getCheckpointConfig().setCheckpointStorage("hdfs:///flink/checkpoints");

        KafkaSource<String> source = KafkaSource.<String>builder()
            .setBootstrapServers("kafka-broker:9092")
            .setTopics("orders")
            .setGroupId("anomaly-detector")
            .setStartingOffsets(OffsetsInitializer.latest())
            .setValueOnlyDeserializer(new SimpleStringSchema())
            .build();

        DataStream<String> stream = env.fromSource(
            source, WatermarkStrategy.noWatermarks(), "Kafka-Orders-Source");

        stream.process(new ProcessFunction<String, String>() {
            @Override
            public void processElement(String value, Context ctx, Collector<String> out) {
                // 异常检测逻辑：解析订单 -> 特征提取 -> 规则判定
                // 模拟处理：将正常订单加标签，异常订单发告警
                if (value.contains("fraud")) {
                    out.collect("ALERT:" + value);
                } else {
                    out.collect("NORMAL:" + value);
                }
            }
        }).print(); // 实际生产中替换为ClickHouse Sink

        env.execute("RealtimeAnomalyDetector");
    }
}
```

Maven打包后上传至DS资源中心：

```bash
mvn clean package -DskipTests
# 在DS控制台 → 资源中心 → /DaMai/flink_jars/ → 上传anomaly-detector-1.0.jar
```

---

### Step 2：配置Flink数据源（3分钟）

**数据源中心** → 创建数据源：

| 配置项 | 值 |
|--------|-----|
| 数据源名称 | Flink-YARN-Cluster |
| 类型 | FLINK |
| 部署模式 | application |
| YARN RM地址 | yarn-rm:8088 |
| HDFS地址 | hdfs://namenode:8020 |
| Flink主目录 | /opt/flink |

---

### Step 3：创建Flink任务节点（5分钟）

在`engineering_platform`项目中创建工作流 `realtime_anomaly_pipeline`，拖入FLINK节点 `anomaly_detector`：

| 配置项 | 值 | 说明 |
|--------|-----|------|
| 程序类型 | JAVA | 支持JAVA/SCALA/PYTHON |
| 主函数的Class | com.damai.flink.RealtimeAnomalyDetector | 全限定类名 |
| 主程序包 | resource://DaMai/flink_jars/anomaly-detector-1.0.jar | 从资源中心引用 |
| 部署方式 | cluster | 运行在YARN per-job模式 |
| Slot数量 | 4 | 每个TaskManager的slot数 |
| TaskManager内存 | 2G | taskmanager.memory.process.size |
| JobManager内存 | 1G | jobmanager.memory.process.size |
| 自定义参数 | -Dstate.savepoints.dir=hdfs:///flink/savepoints | savepoint存储目录 |

**高级配置**（展开"高级"折叠面板）：

| 配置项 | 值 |
|--------|-----|
| savepoint目录 | hdfs:///flink/savepoints/anomaly_detector |
| 执行savepoint | true |
| Flink版本 | >=1.15.0 |

---

### Step 4：设计常驻任务的工作流DAG（10分钟）

常驻任务最棘手的部分是——Flink节点一直RUNNING会阻塞DAG中所有下游节点。大麦电商的实际情况是：Flink任务提交后，还需要执行一次性的ClickHouse建表操作。

**方案对比**：

```
# 方案A：并行分支（推荐，适合一次性和常驻任务混杂的场景）
Flink "anomaly_detector" ──→ (永久RUNNING)
                              ↓
Shell "init_ch_tables"   ──→ (一次性成功即结束)

# 方案B：拆分为两个工作流（推荐，职责清晰）
Workflow 1 "init_infra":  Shell(建表) → SQL(初始化元数据)  → 只跑一次
Workflow 2 "realtime_jobs": Flink(异常检测) → Flink(会话归因) → 提交后监控

# 方案C：SubProcess + 条件分支（适合复杂初始化逻辑）
Flink "anomaly_detector" → 永久RUNNING
SubProcess "init_routine" → (包含建表、预热缓存、数据修复等)
```

本项目选择**方案A**——在`realtime_anomaly_pipeline`工作流中，Flink节点和Shell节点并行执行：

```
                    ┌─→ Flink "anomaly_detector" (常驻)
start ──→ parallel ─┤
                    └─→ Shell "init_clickhouse_table" (一次性)
```

DS画布操作步骤：
1. 拖入FLINK节点 `anomaly_detector`
2. 拖入Shell节点 `init_clickhouse_table`：
```bash
#!/bin/bash
# 初始化ClickHouse表结构
clickhouse-client -h ch-host --port 9000 -u default --password '${ch_pwd}' << 'EOF'
CREATE DATABASE IF NOT EXISTS damai_realtime;
CREATE TABLE IF NOT EXISTS damai_realtime.anomaly_events (
    event_time DateTime,
    user_id UInt64,
    order_id String,
    anomaly_type String,
    score Float32,
    detail String
) ENGINE = MergeTree()
PARTITION BY toYYYYMMDD(event_time)
ORDER BY (user_id, event_time);
EOF
echo "ClickHouse表初始化完成"
```

3. 将两个节点通过"并行分支"连接——两个节点各自独立运行，互不阻塞。

---

### Step 5：配置工作流参数与定时调度（3分钟）

工作流全局配置：

| 配置项 | 值 | 说明 |
|--------|-----|------|
| 失败策略 | 结束 | Flink节点失败则整条流程标记失败 |
| 通知策略 | 失败发 | |
| 告警组 | engineering_alert_group | |
| 超时告警 | 0（不设超时） | 常驻任务不能设超时，否则会被误杀 |
| Worker分组 | etl_heavy | |
| 定时调度 | `0 0 2 * * ? *` | 每天凌晨2点触发（首次提交+后续巡检） |

**关键提醒**：对于常驻任务，定时Cron的角色从"定时启动"变为"定时巡检"。如果凌晨2点DS发现Flink Job已在运行中，则跳过提交直接在日志中输出`[INFO] Flink Job [RealtimeAnomalyDetector] is already RUNNING, skipping re-submit`。

全局参数：

| 参数名 | 值 | 方向 |
|--------|-----|------|
| kafka_broker | kafka-broker:9092 | IN |
| ch_host | clickhouse-host | IN |
| ch_pwd | ${CH_PASSWORD}（引用脱敏后的系统参数） | IN |

---

### Step 6：验证首次提交（5分钟）

手动触发工作流 → 观察执行过程：

**① 任务实例日志**（DS控制台 → 工作流实例 → 点击Flink节点 → "查看日志"）：

```
[INFO] 2025-01-15 02:00:01 - Starting Flink task [anomaly_detector]
[INFO] 2025-01-15 02:00:02 - Flink deploy mode: cluster
[INFO] 2025-01-15 02:00:03 - Building flink run command:
  flink run -m yarn-cluster \
    -yjm 1024 -ytm 2048 \
    -ys 4 \
    -Dstate.savepoints.dir=hdfs:///flink/savepoints \
    -c com.damai.flink.RealtimeAnomalyDetector \
    /tmp/ds/resources/anomaly-detector-1.0.jar
[INFO] 2025-01-15 02:00:15 - Submitted Flink application: application_1705300000000_0001
[INFO] 2025-01-15 02:00:18 - Flink Job ID: c7a8b9d0e1f2a3b4c5d6e7f8a9b0c1d2
[INFO] 2025-01-15 02:00:20 - Flink Job status: RUNNING
```

**② YARN确认**：

```bash
yarn application -list | grep anomaly
# application_1705300000000_0001  RealtimeAnomalyDetector  FLINK  default  RUNNING
```

**③ DS工作流实例状态**：

- 工作流实例状态：`RUNNING_EXECUTION`（不是SUCCESS，这是关键区别）
- Flink任务节点状态：`RUNNING_EXECUTION`
- Shell任务节点状态：`SUCCESS`（已完成并退出）
- 工作流不会自动结束，将持续保持在RUNNING_EXECUTION状态

**④ Flink Web UI确认**：

访问 `http://yarn-rm:8088/proxy/application_1705300000000_0001/`，查看Job运行中、Slot分配正常、Checkpoint持续完成。

---

### Step 7：测试savepoint与故障恢复（10分钟）

**场景1：模拟Flink Job异常崩溃**

```bash
# 在YARN上强制kill Flink应用
yarn application -kill application_1705300000000_0001
```

DS检测行为：
1. Worker心跳检测到YARN Application状态变为`FINISHED/KILLED`
2. DS将该任务实例标记为`FAILURE`
3. 触发失败重试策略（`失败重试次数=3`，`失败重试间隔=60s`）
4. 第一次重试：拼接`-s hdfs:///flink/savepoints/<latest-savepoint-id>`重新提交
5. DS日志输出：`[INFO] Restarting Flink job from savepoint: hdfs:///flink/savepoints/savepoint-c7a8b9-abc123`
6. Flink从savepoint恢复到cancel前的状态，从Kafka断点继续消费

**场景2：手动暂停与恢复**

DS工作流实例页 → 点击 **"暂停"** 按钮：

DS操作序列：
1. 向Flink发送`flink stop --savepointPath hdfs:///flink/savepoints/anomaly_detector <jobId>`命令
2. 等待savepoint创建完成（最长等待5分钟，可配置）
3. savepoint路径写入元数据库`t_ds_task_instance`扩展字段
4. cancel Flink Job
5. DS工作流实例状态变为`PAUSE`

恢复操作 → 点击 **"恢复暂停"**：
1. 从元数据库读取最近savepoint路径
2. 重新构建`flink run -s <savepointPath>`命令
3. 提交新Job，从savepoint恢复
4. 工作流实例状态变回`RUNNING_EXECUTION`

> **踩坑记录**：如果savepoint目录权限不足（Worker用户无HDFS写权限），savepoint创建会静默失败，暂停操作会在5分钟超时后报错。务必在部署前验证`hdfs dfs -put /tmp/test.txt hdfs:///flink/savepoints/`能否成功。

---

### Step 8：构建完整的实时流处理链路（15分钟）

创建新的完整工作流 `realtime_pipeline_v2`，编排三个Flink常驻任务：

```
┌─────────────────────────────────────────────────────┐
│              realtime_pipeline_v2                    │
│                                                     │
│  Flink "kafka_ingestion"                            │
│    (Kafka→清洗→Kafka输出Topic)                       │
│         │                                           │
│         ↓                                           │
│  Flink "sessionizer"                                │
│    (30分钟窗口会话归因→Kafka输出)                     │
│         │                                           │
│         ↓                                           │
│  Flink "anomaly_detector"                           │
│    (异常模式识别→ClickHouse)                          │
│                                                     │
│  三个节点全部常驻RUNNING，形成流水线                   │
└─────────────────────────────────────────────────────┘
```

关键配置要点：

| 节点 | 部署模式 | Slot | TM内存 | 启动顺序 |
|------|----------|------|--------|----------|
| kafka_ingestion | cluster | 4 | 2G | 第一个 |
| sessionizer | cluster | 8 | 4G | 依赖kafka_ingestion的RUNNING状态 |
| anomaly_detector | cluster | 4 | 2G | 依赖sessionizer的RUNNING状态 |

**依赖配置技巧**：DS中Flink节点的依赖判定不是"上游SUCCESS"（因为上游永远不会SUCCESS），DS内部做了特殊处理——当上游Flink节点进入`RUNNING_EXECUTION`状态后，即视为"依赖满足"，下游Flink节点可以开始提交。

---

### Step 9：配置告警与心跳检测（5分钟）

Flink常驻任务需要特殊的告警策略：

**DS自带告警**（失败自动通知）：

| 触发条件 | 告警内容 | 渠道 |
|----------|----------|------|
| Flink任务从RUNNING→FAILURE | 任务名称 + 失败时间 + 退出码 + savepoint路径 | 钉钉+邮件 |
| 重试3次仍失败 | 放弃重试 + 建议人工介入 + 最近savepoint路径 | 钉钉+电话 |
| savepoint创建失败 | savepoint路径 + HDFS权限异常 | 钉钉 |

**心跳丢失检测**（通过Shell任务辅助实现）：

创建一个独立的Shell任务 `flink_heartbeat_monitor`，每10分钟运行一次：

```bash
#!/bin/bash
# 检查Flink Job心跳
APP_ID="application_1705300000000_0001"
STATUS=$(yarn application -status ${APP_ID} 2>/dev/null | grep "State" | awk '{print $NF}')

if [[ "${STATUS}" != "RUNNING" ]]; then
    echo "[ALERT] Flink Job ${APP_ID} is NOT RUNNING! Current state: ${STATUS}"
    echo "::set-output name=FLINK_ALIVE::false"
    exit 1
fi

# 检查最近的checkpoint是否完成
# 通过Flink REST API查询
FLINK_URL="http://yarn-rm:8088/proxy/${APP_ID}"
LAST_CHECKPOINT=$(curl -s "${FLINK_URL}/jobs/overview" | grep -o '"lastCheckpointTime":[0-9]*' | cut -d: -f2)
NOW=$(date +%s%3N)
DIFF=$((NOW - LAST_CHECKPOINT))

if [[ ${DIFF} -gt 180000 ]]; then
    echo "[ALERT] Last checkpoint was ${DIFF}ms ago (> 3 minutes)"
    echo "::set-output name=FLINK_ALIVE::false"
    exit 1
fi

echo "Flink Job healthy. Last checkpoint: ${DIFF}ms ago"
echo "::set-output name=FLINK_ALIVE::true"
```

将此Shell任务设置为独立的定时工作流，Cron表达式`0 */10 * * * ? *`（每10分钟执行），失败时发送P1告警。

---

### Step 10：常见踩坑与最佳实践

**坑1：Flink Job名冲突**

如果在同一个Flink集群中用相同JobName提交两个Job，Flink会拒绝第二个提交，报错`A job with the name 'xxx' already exists`。DS的资源中心JAR包引用不会自动处理命名冲突——需要确保每个任务节点的Job Name唯一。建议命名规范：`项目名_模块名_环境`，如`damai_anomaly_detector_prod`。

**坑2：savepoint权限陷阱**

HDFS savepoint目录的写权限必须授予DS Worker进程的运行用户（而非Flink集群用户）。验证方法：

```bash
sudo -u ds_worker hdfs dfs -touchz hdfs:///flink/savepoints/test
```

**坑3：TaskManager内存配置误区**

很多人以为`-ytm 2048`就是TaskManager的总内存，但Flink 1.10+的内存模型更复杂：`taskmanager.memory.process.size`才是总内存（含JVM元空间、框架堆、任务堆、网络缓冲、托管内存和JVM开销）。建议用`-Dtaskmanager.memory.process.size=2048m`替代`-ytm 2048`，避免实际可用堆远小于预期。

**坑4：Batch Flink混用Stream Flink**

Flink SQL的batch模式Job执行完会自动退出（像正常批处理任务一样），工作流实例能走到SUCCESS。而Stream模式Job不会退出。在DS中创建Flink节点时，务必确认你写的是Stream Job还是Batch Job——同一个节点类型的生命周期完全不同。Batch模式的Flink节点可以放心放在DAG中间作为普通节点使用；Stream模式必须在设计时考虑RUNNING阻塞问题。

**坑5：YARN Session Timeout**

如果使用YARN Session模式（而非per-job模式），Flink集群有idle超时设置。当所有Job都停止后，Session会在`yarn.session.timeout.secs`后自动释放。此时DS如果尝试提交新Job会失败。建议：生产环境使用per-job或application模式，避免session超时干扰。

---

## 4. 项目总结

### Flink任务管理的三种方案对比

| 维度 | Flink Web UI | Apache DolphinScheduler | Ververica Platform |
|------|-------------|------------------------|-------------------|
| **Job生命周期管理** | 手动提交/取消 | 自动化提交+监控+重试 | 全自动（K8s原生） |
| **savepoint管理** | 手动触发、需记路径 | 自动触发、路径存入元数据库 | 自动（Stateful Upgrades） |
| **多Job编排** | 不支持 | 原生DAG编排+Flink节点 | 仅Flink SQL支持 |
| **失败恢复** | 无（手动重跑） | 自动重试+savepoint恢复 | 自动（从最新checkpoint） |
| **统一告警** | 无 | 12种渠道+多级策略 | 基础告警 |
| **批流混合编排** | 不支持 | 同一DAG中混用 | 不支持 |
| **成本** | 免费（随Flink分发） | 免费（Apache 2.0） | 商业许可 |
| **学习门槛** | 低 | 中 | 低（UI友好） |

### 适用场景 vs 不适用场景

**适合用DS管理Flink的3个典型场景：**

1. **生产环境常驻流处理任务**：7×24运行、需要自动故障恢复、需要与其他批处理任务（数据对账、报表）联动的场景。
2. **多租户Flink Job管理**：企业内有多个团队各跑自己的Flink Job，需要权限隔离、资源配额、统一监控。
3. **Flink批流一体调度**：同一个项目中既有Flink Batch ETL（每日凌晨），又有Flink Streaming（实时），需要用同一个平台管理。

**不适合用DS管理Flink的2个场景：**

1. **开发调试阶段**：频繁修改代码、频繁提交测试——DS的上传JAR→配置节点→提交的流程太慢，直接用`flink run`更高效。
2. **Flink SQL即席查询**：数据分析师临时写一条Flink SQL验证想法——不应在DS中创建长期任务节点，应该用Flink SQL Client或Zeppelin。

### 两个关键设计原则

1. **常驻任务≠永远不下线**。即使Flink Job理论上可以跑几个月，你仍然要在设计时考虑"当它需要下线时怎么办"。savepoint目录权限、恢复流程、数据断流时间窗口这三件事必须在任务上线前验证完成。

2. **心跳检测不能只靠DS**。DS能检测Flink Job的退出，但不能检测Flink Job的"假RUNNING"——JobManager还活着但TaskManager全挂了、Kafka消费已停滞。必须配合外部心跳监控（Step 9的Shell脚本或Prometheus指标采集）做语义健康检查。

### 注意事项

- **版本兼容**：DS 3.1.x支持Flink 1.12到1.17，使用前确认Worker节点的Flink版本与DS兼容矩阵匹配。
- **安全边界**：Flink任务以DS Worker进程的用户身份运行——如果Worker用户是`root`，Flink Job也会以`root`运行，存在安全风险。务必为Worker创建专用的非root用户。
- **资源配置**：常驻Flink Job长期占用YARN/K8s资源，需要与集群管理员协商资源配额（YARN Queue或K8s Namespace）。避免一个Flink Job吃光队列导致其他批处理任务无法提交。

### 思考题

1. 大麦电商的`RealtimeAnomalyDetector`在深夜2:00-3:00因上游Kafka集群计划内维护而出现数据断流。DS的心跳检测Shell脚本检测到checkpoint在3分钟内未完成，判定Flink Job异常并发出告警。但实际上Flink Job本身没有故障，只是Kafka无数据。请问如何区分"Flink Job故障"和"上游数据断流"这两种情况？请设计一个改进版的心跳检测方案。

2. 某天凌晨，`anomaly_detector`因TaskManager内存溢出（OOM）被YARN KILL。DS触发失败重试，从最近一个savepoint恢复。但发现这个savepoint是3小时前创建的——3小时的实时数据全部丢失。请问：(1) 为什么savepoint是3小时前的？(2) 如何缩小checkpoint/savepoint的数据丢失窗口？(3) Flink的`端到端精确一次`（exactly-once）语义在这个场景下能否保证数据不丢失？

---

> **本章完成时间建议**：阅读30分钟 + 动手实践90分钟（Step 1-5基础配置30分钟 + Step 6-9验证调试40分钟 + Step 10复盘20分钟）
> **本章关键词**：Flink流处理、常驻任务、RUNNING_EXECUTION、savepoint恢复、YARN集成、故障自愈、心跳检测、批流一体
> **源码关联**：`dolphinscheduler-task-plugin/dolphinscheduler-task-flink/src/main/java/org/apache/dolphinscheduler/plugin/task/flink/FlinkTask.java`（Flink任务提交与状态跟踪的核心逻辑）

---

*上一章：第17章《Spark任务调度与大数据批处理》*
*下一章预告：第19章《K8s任务调度与云原生编排》——你将学会在K8s集群中提交和管理容器化任务，实现弹性伸缩与资源隔离。*
