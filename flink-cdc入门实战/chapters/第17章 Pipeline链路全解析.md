# 第17章 Pipeline链路全解析

## 1 项目背景

### 业务场景：Pipeline作业延迟激增，怎么定位瓶颈

第16章的Pipeline YAML上线后运行稳定，但某天订单同步延迟突然从200ms飙升至30秒。运维同学打开Flink Web UI，看到Source → PreTransform → PostTransform → SchemaOperator → Sink这条链路中，某个算子发生了反压。

但Pipeline YAML是一个黑盒——你不知道每个阶段具体做了什么，不知道哪个阶段消耗了最多时间，无法准确定位瓶颈。

**理解Pipeline内部的拓扑结构**是解决这类问题的关键。本章深入`FlinkPipelineComposer`的源码，揭开Pipeline从YAML到DataStream拓扑的全过程。

### Pipeline六阶段拓扑图

```
YAML文件
    │
    ▼
┌──────────────────────────────────────────────────────────────────┐
│ FlinkPipelineComposer.translate()                                │
│                                                                  │
│  1. DataSourceTranslator                                         │
│     └── Source → DataStream<Event>                               │
│                                                                  │
│  2. TransformTranslator.translatePreTransform()                  │
│     └── DataStream<Event> → PreTransformOperator                │
│         (投影: 列裁剪、计算列; 过滤: 行过滤)                       │
│                                                                  │
│  3. TransformTranslator.translatePostTransform()                 │
│     └── PreTransform → PostTransformOperator                    │
│         (Schema元数据对齐: 补充Schema信息供后续使用)               │
│                                                                  │
│  4. PartitioningTranslator                                       │
│     └── PostTransform → EventPartitioner                        │
│         (按表名/分区键重新分区，保证同表数据进入同Subtask)          │
│                                                                  │
│  5. SchemaOperatorTranslator                                     │
│     └── PartitionedStream → SchemaOperator                      │
│         (Schema变更处理: 列新增/删除/类型修改)                    │
│                                                                  │
│  6. DataSinkTranslator                                           │
│     └── SchemaOperator → DataSinkWriterOperator                 │
│         (写入目标系统: Kafka/MySQL/Iceberg等)                    │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
    │
    ▼
Flink DataStream作业 → 提交到集群执行
```

---

## 2 项目设计 · 三人交锋对话

**小胖**（看着拓扑图）：6个阶段！我以前以为Pipeline就是Source→Sink完事了，没想到中间有这么多环节。每个阶段到底是干啥的？

**大师**：我们以一条CDC数据的"旅程"来说明：

```sql
-- 源数据：MySQL shop.orders_full表的一条INSERT
INSERT INTO orders_full VALUES (6, 'ORD006', 1001, 'iPhone 15', 6999.00, 'PAID', ...);
```

**Step 1 - Source阶段：** MySQL Binlog → `DataChangeEvent`
Binlog的ROW事件被Debezium解析后，Source算子输出一个`DataChangeEvent`对象。

**Step 2 - PreTransform阶段：** 投影和过滤
如果配置了`projection: "id, order_id, amount / 100 AS amount"`和`filter: "status = 'PAID'"`，这个阶段只保留id、order_id、amount三列，过滤掉非PAID行。

**Step 3 - PostTransform阶段：** Schema对齐
将投影后的Schema信息（字段名、类型、顺序）绑定到事件上，供SchemaOperator使用。

**Step 4 - Partitioning阶段：** 数据分区
按`TableId`或自定义分区键，将所有`orders`表的数据发送到同一个Subtask。保证同一张表的写操作不会被并行破坏顺序。

**Step 5 - SchemaOperator阶段：** Schema变更处理
如果MySQL执行了`ALTER TABLE orders ADD COLUMN discount DECIMAL(5,2)`，这个阶段负责将Schema变更传播到下游Sink。

**Step 6 - Sink阶段：** 写入目标
最终的数据写入Kafka/Iceberg等。

**小白**：为什么PreTransform和PostTransform分开？不能合成一个阶段吗？

**大师**：这是Flink CDC Pipeline设计的精华。两阶段Transform解决了"先过滤还是先投影"的问题——**PreTransform先做投影和过滤（减少数据量），PostTransform再做Schema对齐（为SchemaOperator准备数据）**。

把它们分开的好处是：如果先过滤掉了大量数据，投影计算量自然减少，性能更优。

但如果合在一个算子中，逻辑上也是可以的——只是不利于分区。因为在PreTransform（过滤投影）之后，数据可能需要在不同表的分区之间重新分配，这个分区操作需要独立的算子来实现。

**技术映射**：PreTransform就像"快递分拣中心的粗分——先按城市分（过滤），再按小区分（投影）"。PostTransform像"贴上最后一公里的配送标签（Schema对齐）"。

**小白**：那`EventPartitioner`的分区键可以自定义吗？我注意到Pipeline的transform块有`partition-keys`配置。

**大师**：可以！默认分区键是`TableId`（表名），保证同一张表的数据到同一个Subtask。但你可以通过`partition-keys`自定义：

```yaml
transform:
  - source-table: shop.orders_full
    partition-keys: user_id    # 按用户ID分区
```

这样做的目的是**保障同一用户的数据顺序**——如果你做的是"用户维度的实时聚合"，需要同一个用户的所有变更进入同一个处理单元。

---

## 3 项目实战

### 环境准备

**需要Flink CDC源码（可选，用于源码追踪）：**
- `flink-cdc-composer/src/main/java/org/apache/flink/cdc/composer/flink/FlinkPipelineComposer.java`
- `flink-cdc-composer/src/main/java/org/apache/flink/cdc/composer/flink/translator/DataSourceTranslator.java`
- `flink-cdc-composer/src/main/java/org/apache/flink/cdc/composer/flink/translator/TransformTranslator.java`

### 分步实现

#### 步骤1：观察Pipeline作业的Operator链

```bash
# 1. 提交一个简单的Pipeline作业
flink-cdc.sh pipeline-full.yaml --use-mini-cluster

# 2. 打开Flink Web UI (http://localhost:8081)
# 点击作业 → Show Plan

# 3. 观察作业图
# 预期看到如下Operator链：
#
# Source: MySQL CDC[source] → ①
#   ↓
# PreTransform (projection+filter)[preTransform] → ②
#   ↓  (数据量减少——投影切掉了列，过滤掉了行)
# PostTransform (schema alignment)[postTransform] → ③
#   ↓
# Partitioning (keyBy tableId)[partitioning] → ④
#   ↓
# SchemaOperator (schema evolution)[schemaOperator] → ⑤
#   ↓
# Sink: Kafka[sink] → ⑥
```

#### 步骤2：配置不同分区模式观察拓扑变化

创建两个Pipeline对比：

**pipeline-pk-partition.yaml**（按主键分区）：
```yaml
transform:
  - source-table: shop.orders_full
    projection: "id, order_id, user_id, amount, status"
    partition-keys: user_id
```

对比**pipeline-default.yaml**（默认分区）：
```yaml
# 不指定partition-keys
```

**观察差异：** 在Flink Web UI上看到两种Pipeline的KeyBy分区方式不同——默认按`TableId`，自定义按`user_id`。

#### 步骤3：调试模式——在Pipeline中插入日志

```yaml
transform:
  - source-table: shop.orders_full
    projection: "id, order_id, user_id, amount, status"
    filter: "status = 'PAID'"
```

设置Flink日志级别为DEBUG查看Transform的执行过程：
```bash
# 在flink-conf.yaml或flink-cdc.yaml中添加
taskmanager.env:
  log.level: DEBUG
  logger.pipeline.name: org.apache.flink.cdc.runtime.operators.transform
```

**日志示例：**
```
2024-01-15 10:00:00,123 DEBUG PreTransformOperator - Start transform event
  source: shop.orders_full, op: INSERT
  before: null
  after: RecordData{id=6, order_id=ORD006, user_id=1001, ..., status=PAID}

2024-01-15 10:00:00,124 DEBUG PreTransformOperator - After projection:
  RecordData{id=6, order_id=ORD006, user_id=1001, amount=69.99, status=PAID}
  (只保留了5列，amount从6999变为69.99)

2024-01-15 10:00:00,124 DEBUG PreTransformOperator - After filter:
  status=PAID → 通过过滤，事件继续传递
```

#### 步骤4：自定义Pipeline——使用Java代码构建Pipeline

Pipeline YAML最终是通过Java代码的`PipelineDef`构建的。你也可以直接通过Java代码编程式构建：

```java
package com.example;

import org.apache.flink.cdc.common.pipeline.PipelineOptions;
import org.apache.flink.cdc.composer.PipelineDefinition;
import org.apache.flink.cdc.composer.PipelineExecution;
import org.apache.flink.cdc.composer.flink.FlinkPipelineComposer;

/**
 * 编程式构建Pipeline（对应YAML配置的Java版本）
 */
public class ProgrammaticPipeline {

    public static void main(String[] args) throws Exception {
        // 1. 构建Pipeline定义
        PipelineDefinition definition = PipelineDefinition.builder()
            .source(
                PipelineDefinition.SourceDef.builder()
                    .type("mysql")
                    .hostname("localhost")
                    .port(3306)
                    .tables("shop.orders_full")
                    .build()
            )
            .sink(
                PipelineDefinition.SinkDef.builder()
                    .type("kafka")
                    .property("bootstrap.servers", "localhost:9092")
                    .build()
            )
            .pipelineConfig(
                PipelineOptions.builder()
                    .setName("Programmatic Pipeline")
                    .setParallelism(2)
                    .build()
            )
            .build();

        // 2. 使用FlinkPipelineComposer执行
        FlinkPipelineComposer composer = FlinkPipelineComposer.ofMiniCluster();
        PipelineExecution execution = composer.compose(definition);
        execution.run();
    }
}
```

#### 步骤5：性能Profiling——定位Pipeline瓶颈

```java
import org.apache.flink.api.common.functions.RichMapFunction;
import org.apache.flink.metrics.Gauge;

/**
 * Pipeline延迟Profiling——在每个算子前后记录时间戳
 */
public class LatencyProbeMapper extends RichMapFunction<String, String> {

    private transient long lastLatencyMs;

    @Override
    public void open(Configuration parameters) {
        getRuntimeContext().getMetricGroup()
            .gauge("pipeline_latency_ms",
                (Gauge<Long>) () -> lastLatencyMs);
    }

    @Override
    public String map(String value) throws Exception {
        // 假设JSON中包含CDC事件时间戳
        long eventTs = extractTimestamp(value);
        long now = System.currentTimeMillis();
        lastLatencyMs = now - eventTs;
        return value;
    }

    private long extractTimestamp(String json) {
        // 从JSON的source.ts_ms字段提取事件时间
        String tsStr = json.replaceAll(
            ".*\"ts_ms\"\\s*:\\s*(\\d+).*", "$1");
        return Long.parseLong(tsStr);
    }
}
```

#### 常见陷坑及解决方法

| 问题 | 原因 | 解决方法 |
|------|------|---------|
| PostTransform阶段数据量异常 | Schema对齐时发现不匹配的Schema | 调整`schema.change.behavior`策略 |
| Partitioning后数据倾斜 | 分区键选择不当导致数据分布不均 | 检查`partition-keys`选择，使用分布均匀的列 |
| SchemaOperator处理过慢 | DDL变更频繁导致Schema Registry频繁更新 | 设置`schema.change.behavior=IGNORE`或`LENIENT` |
| 多个Sink写入同一个表 | Route配置了多条规则指向同一目标 | 检查Route规则，确保没有冲突 |
| Pipeline拓扑中有许多隐式Connection | 并行度大于1时，KeyBy需要网络Shuffle | 数据网络传输是正常行为，但要注意网络带宽 |

---

## 4 项目总结

### Pipeline六阶段总结

| 阶段 | Operator | 核心功能 | 对数据的影响 | 是否可配置 |
|------|---------|---------|-------------|-----------|
| **Source** | 各DataSource | 读取底层数据源 | 产出原始Event | ✅ Source类型和参数 |
| **PreTransform** | PreTransformOperator | 投影 + 过滤 | 减少列数、减少行数 | ✅ transform.projection/filter |
| **PostTransform** | PostTransformOperator | Schema对齐 | 补充Schema元数据 | 自动（无需配置） |
| **Partitioning** | EventPartitioner | 数据分区 | 重新分布数据 | ✅ transform.partition-keys |
| **SchemaOperator** | SchemaOperator | Schema变更处理 | 处理DDL变更 | ✅ pipeline.schema.change.behavior |
| **Sink** | DataSinkWriterOperator | 写入目标系统 | 持久化数据 | ✅ Sink类型和参数 |

### 性能优化建议

1. **Early Filtering**：在PreTransform中尽早过滤掉不需要的数据，减少后续阶段的处理量。
2. **分区键选择**：选择分布均匀的列作为分区键，避免数据倾斜导致某些Subtask成为瓶颈。
3. **减少不必要的Schema Evolution**：如果不关心DDL变更，设置`schema.change.behavior=IGNORE`可以省掉SchemaOperator的处理开销。
4. **并行度匹配**：Source的并行度应与Chunk数匹配，Sink的并行度应与目标系统的分区数匹配。

### 常见踩坑经验

**故障案例1：Pipeline作业图显示"chain被中断"**
- **现象**：Web UI上看到Source和Transform之间是红色虚线（表示chain中断）
- **根因**：KeyBy操作（Partitioning）导致算子链被切断，因为KeyBy需要网络Shuffle
- **解决方案**：这是正常行为。如果不想被切断，可以设置`pipeline.operator-chaining=false`观察完整Flow

**故障案例2：PostTransform后的数据量比PreTransform前还大**
- **现象**：PreTransform过滤了50%的数据，但PostTransform输出的记录数不减反增
- **根因**：PostTransform阶段处理的是Schema元数据对齐，可能会因为Schema不匹配生成额外的"修正事件"
- **解决方案**：检查Schema Evolution配置，设置`EVOLVE`模式并验证Schema是否一致

**故障案例3：SchemaOperator Coordinator超时**
- **现象**：Pipeline作业报错`SchemaOperator RPC timeout`
- **根因**：SchemaOperator的Coordinator在`SchemaCoordinator`中通过RPC协调所有并行实例的Schema状态，当集群负载高时RPC超时
- **解决方案**：增加`schema-operator.rpc-timeout`配置（默认1小时），或降低Schema变更频率

### 思考题

1. **进阶题①**：Flink CDC Pipeline的SchemaOperator有regular和distributed两种模式。这两种模式在拓扑编排上有什么区别？什么情况下你会选择distributed模式？提示：查看`SchemaOperator.translateRegular()`和`SchemaOperator.translateDistributed()`的源码。

2. **进阶题②**：在Pipeline拓扑中，如果Source的并行度=4，但Sink的并行度=2，Partitioning阶段需要做数据重整（rebalance）。这个过程中如果使用`keyBy(TableId)`，是否所有数据都需要网络传输？有没有可能利用Flink的LocalChannel避免不必要的网络传输？

---

> **下一章预告**：第18章「增量快照原理与调优（FLIP-27）」——作为Flink CDC的核心竞争力之一，增量快照算法实现了无锁的全量数据读取。本章深入`IncrementalSource`的源码，剖析快照分片（SnapshotSplit）和增量流（StreamSplit）的水位对齐算法。
