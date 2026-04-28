# 第29章：JobManager源码剖析——调度与容错核心

---

## 1. 项目背景

一位资深开发者问："当一个Flink作业提交后，JobManager内部到底发生了什么？"

从`env.execute("MyJob")`到作业真正在TaskManager上运行，JobManager内部走过了这些步骤：

1. **Client端**：构建StreamGraph → JobGraph
2. **JobManager端**：JobGraph → ExecutionGraph → 物理调度 → 部署Task

理解JobManager的内部架构，对于排查以下问题至关重要：
- 作业提交后卡在"Resource Pending"——是YARN资源不够还是JobManager调度器卡住了？
- 并行度扩容为什么需要Savepoint？ExecutionGraph怎么重新切分Task？
- 作业挂了重启——JobManager如何决定哪些Task需要重新部署？什么是Failover Strategy？

---

## 2. 项目设计

> 场景：小胖的作业提交后状态一直是"Resource Pending"，等了20分钟都不变。

**大师**：Resource Pending不代表YARN没给资源——可能是JobManager内部的调度器卡住了。先看Flink WebUI，点一下"Resource Pending"标签旁边的"详细"——它会显示哪些Slot没分配到。

**小胖**：显示"SLOT_REQUEST_PENDING"——有4个Task Slot一直在请求但没分配到。

**大师**：这说明TaskManager已经启动了，但JobManager的SlotPool和ResourceManager之间的Slot分配没有完成。这往往是因为TaskManager的心跳延迟导致Slot被标记为不可用。

**技术映射：JobManager的调度核心 = SchedulerNG + SlotPool + ExecutionGraph。SchedulerNG决定"哪个Task跑在哪个Slot上"，SlotPool管理已分配的Slot资源，ExecutionGraph维护作业的完整执行视图。**

**小白**：那ExecutionGraph和JobGraph到底是什么关系？

**大师**：简单说：

- **StreamGraph**：用户代码生成的初始DAG（每个算子一个节点）
- **JobGraph**：StreamGraph经过优化（算子链合并）后生成的作业图——每个节点对应一个Task
- **ExecutionGraph**：JobGraph的并行化版本——每个Task被展开为多个并行子任务（ExecutionVertex），加上中间的中间结果分区（IntermediateResultPartition）

**技术映射：JobGraph是"逻辑上的作业图"，ExecutionGraph是"物理执行时的展开图"。JobGraph的1个节点 → ExecutionGraph的N个ExecutionVertex（N=并行度）。**

---

## 3. 项目实战

### 分步实现

#### 步骤1：从源码理解StreamGraph → JobGraph → ExecutionGraph的转换

**目标**：通过源码断点跟踪，理解三者的关系。

```java
// ========== 关键步骤1：env.execute() ==========
// StreamExecutionEnvironment.execute()
// 1. 获取StreamGraph
// 2. 将StreamGraph转换为JobGraph
// 3. 提交JobGraph到集群

// ========== 关键步骤2：StreamGraph构建 ==========
// org.apache.flink.streaming.api.graph.StreamGraphGenerator
// 遍历所有的Transformation（SourceTransformation, OneInputTransformation...）
// 为每个Transformation创建StreamNode
// 在StreamEdge中记录"forward/partition/rebalance"等分区策略

// ========== 关键步骤3：JobGraph转换 ==========
// org.apache.flink.client.StreamGraphTranslator
// 合并可以chain的StreamNode（算子链优化）
// 将合并后的节点封装为JobVertex
// 计算中间结果集（IntermediateDataSet）
```

**代码验证**：打印StreamGraph和JobGraph的JSON表示。

```java
package com.flink.column.chapter29;

import org.apache.flink.streaming.api.environment.StreamExecutionEnvironment;
import org.apache.flink.streaming.api.graph.StreamGraph;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;

public class PrintGraphPlan {

    public static void main(String[] args) throws Exception {
        StreamExecutionEnvironment env = StreamExecutionEnvironment.getExecutionEnvironment();
        env.setParallelism(2);

        env.socketTextStream("localhost", 9999)
            .flatMap((line, out) -> {
                for (String w : line.split("\\s")) out.collect(w);
            })
            .keyBy(w -> w)
            .sum(0)
            .print();

        // 打印StreamGraph JSON
        StreamGraph streamGraph = env.getStreamGraph();
        System.out.println("=== StreamGraph ===");
        System.out.println(streamGraph.getStreamingPlanAsJSON());

        // 打印JobGraph JSON（需要enableCheckpointing）
        env.enableCheckpointing(10000);
        System.out.println("=== ExecutionPlan ===");
        System.out.println(env.getExecutionPlan());

        env.execute("Chapter29-GraphPlanDemo");
    }
}
```

#### 步骤2：ExecutionGraph内部结构分析

**目标**：理解ExecutionGraph的数据结构和调度流程。

核心类分析：

```
// ========== JobGraph层 ==========
JobGraph
  ├── JobVertex[]           // 每个算子链合并后的节点
  │     ├── JobVertexID    // 唯一标识（与UID对应）
  │     ├── parallelism     // 并行度
  │     └── IntermediateDataSet[]  // 输出
  └── JobEdge              // JobVertex之间的边

// ========== ExecutionGraph层 ==========
ExecutionGraph
  ├── ExecutionJobVertex[]  // JobVertex的展开
  │     ├── JobVertexID
  │     └── ExecutionVertex[]  // N个（N=并行度）
  │           ├── ExecutionVertexID
  │           └── Execution[]  // 每次执行尝试
  │                 ├── attemptNumber
  │                 ├── state (CREATED/SCHEDULED/DEPLOYING/RUNNING/FINISHED/FAILED)
  │                 └── TaskManagerLocation
  └── IntermediateResult   // 中间结果
        └── IntermediateResultPartition[]
              └── consumingExecutionVertex  // 下游ExecutionVertex
```

#### 步骤3：Failover策略源码分析

**目标**：理解Flink的不同故障恢复策略。

```java
// ========== Failover策略 ==========
// 在flink-conf.yaml中配置
// jobmanager.execution.failover-strategy: region

// 三种策略：
// 1. full（全部重启）：简单但代价大——整个作业重启
// 2. region（按Region重启，推荐）：只重启受故障影响的Region
// 3. individual（仅重启失败的Task）：细粒度但状态一致性难保证

// Region划分规则：
// Region = 一组通过blocking edge（非流水线边）连接的算子
// 流水线边（pipelined）上的算子属于同一个Region
// KeyBy/Rebalance会创建流水线边

// 源码位置：
// org.apache.flink.runtime.executiongraph.failover.flip1
//   ├── RestartAllStrategy
//   ├── RestartIndividualStrategy
//   └── RestartPipelinedRegionStrategy (default since 1.14)
```

#### 步骤4：Slot管理机制

**目标**：理解JobManager如何管理Task Slot。

```java
// SlotManager（JobManager端）
// 管理所有TaskManager上报的Slot
// Slot = TaskManager上的一个"执行单元"（1个线程）

// 调度流程：
// 1. SchedulerNG请求Slot
// 2. SlotManager在SlotPool中查找空闲Slot
// 3. 如果找到：分配Slot，部署Task
// 4. 如果没找到：向ResourceManager请求新的TaskManager
// 5. ResourceManager向YARN/K8S申请Container

// 关键配置：
// cluster.evenly-spread-out-slots: true  // Slot均匀分布在TM上
// slot.idle.timeout: 50000                // 空闲Slot保留时间
```

#### 步骤5：通过JMX监控JobManager内部状态

**目标**：通过JMX暴露JobManager的内部指标。

```java
// 在启动参数中添加JMX
// -Dcom.sun.management.jmxremote
// -Dcom.sun.management.jmxremote.port=9010
// -Dcom.sun.management.jmxremote.authenticate=false
// -Dcom.sun.management.jmxremote.ssl=false

// 然后使用JConsole或JMX客户端连接，查看：
// org.apache.flink.management.jmx:type=JobManager
//   - numRunningJobs
//   - numFinishedJobs
//   - numFailedJobs
//   - numCanceledJobs
//   - waitingForResources
```

### 可能遇到的坑

1. **ExecutionGraph中的Execution状态与WebUI不一致**
   - 根因：Execution状态变化是异步的，WebUI的REST API采样间隔导致短暂的不一致
   - 解决：查看TaskManager日志获取准确的状态

2. **Region Failover策略下部分算子没有恢复**
   - 根因：如果故障算子的Region不包含Source，Source不会回退——数据可能跳变
   - 解决：全量重启或手动触发Savepoint恢复

3. **StreamGraph的chain优化过度，导致调试困难**
   - 根因：多个算子被链在一起，日志显示为同一个Operator名
   - 解决：`env.disableOperatorChaining()`或在关键算子后调用`disableChaining()`

---

## 4. 项目总结

### 作业图的三层结构

```
┌─────────────────────────────────────────────────┐
│  StreamGraph      逻辑计划（用户代码映射）         │
│  ┌─ Source ─ flatMap ─ keyBy/sum ─ print ─┐     │
│  └─────────────────────────────────────────┘    │
│                       ↓ 算子链优化               │
│  JobGraph          优化后逻辑计划（Task粒度）     │
│  ┌─ Source+flatMap ─ keyBy/sum ─ print ────┐   │
│  └──────────────────────────────────────────┘  │
│                       ↓ 并行化展开               │
│  ExecutionGraph     物理执行计划（子任务粒度）   │
│  ┌─ Src(1/2) Src(2/2) ─聚合(1/4)...(4/4)─┐   │
│  └──────────────────────────────────────────┘  │
└─────────────────────────────────────────────────┘
```

### JobManager核心组件

| 组件 | 职责 | 关键类 |
|------|------|--------|
| Dispatcher | 接收作业提交、创建JobManager | Dispatcher.java |
| JobMaster | 管理单个作业的生命周期 | JobMaster.java |
| SchedulerNG | 调度ExecutionVertex到Slot | SchedulerBase.java |
| SlotPool | 管理已分配的Slot资源 | SlotPoolImpl.java |
| CheckpointCoordinator | 协调Checkpoint/Barrier | CheckpointCoordinator.java |

### 注意事项
- JobManager是状态存储的——它持有ExecutionGraph和Checkpoint元数据。JM内存不足时作业会失败
- Region Failover策略下，一个算子的失败不会重启整个作业——但Source不会回退，可能导致短暂数据空洞

### 常见踩坑经验

**案例1：JobManager OOM频繁**
- 根因：ExecutionGraph太大（并行度10万级，每个子任务的Execution状态全部在JM内存中）
- 解方：降低并行度；使用FineGrainedResource管理减小ExecutionGraph内存占用

**案例2：Task部署很快但Execution一直显示SCHEDULED**
- 根因：Operator的Chain信息在JobGraph中错误，导致Flink认为Task之间存在blocking边
- 解方：检查算子链配置；使用`startNewChain()`强制打断链

**案例3：从Savepoint恢复后所有Task都部署成功但作业卡在INITIALIZING**
- 根因：状态恢复时RocksDB加载SST文件耗时过长
- 解方：增大`taskmanager.memory.managed.size`；使用RocksDB的`max_open_files=-1`

### 优点 & 缺点

| | Flink JobManager（分布式调度+容错） | 单机引擎（如Storm Nimbus或自研调度） |
|------|-----------|-----------|
| **优点1** | 三层图结构（StreamGraph→JobGraph→ExecutionGraph）层次清晰，支持增量展开 | 调度器扁平化设计，大规模作业图管理能力弱 |
| **优点2** | Region Failover策略精准，只重启受影响算子 | 全量重启，故障恢复时间长 |
| **优点3** | SlotPool + ResourceManager双层资源调度，弹性伸缩 | 资源管理往往与调度耦合，扩展性差 |
| **缺点1** | JM单点——HA需额外配置ZooKeeper/K8S | 无单点或自带HA |
| **缺点2** | ExecutionGraph全量在JM内存中，高并行度时JM OOM | 调度器可分布式存储作业元数据 |

### 适用场景

**典型场景**：
1. 深入理解Flink作业提交流程——StreamGraph→JobGraph→ExecutionGraph的转换
2. 排查作业调度卡顿（Resource Pending/SCHEDULED状态）——理解SchedulerNG和SlotPool机制
3. 故障恢复策略选择——根据业务容忍度选择full/region/individual failover
4. 大规模集群性能调优——理解JM内存瓶颈，合理设置并行度和槽位数

**不适用场景**：
1. 日常业务开发——业务开发者通常不需要直接操作JM源码
2. 简单无状态作业——无需深入理解调度细节，默认配置即可

### 思考题

1. 如果作业的算子链合并后只有一个JobVertex（Source+Map+Filter全链在一起），它的并行度=10，ExecutionGraph中有几个ExecutionVertex？如果TaskManager有5个Slot，每个Slot能部署几个ExecutionVertex？

2. Region Failover策略下，如果一个非Source算子的状态损坏（比如RocksDB的SST文件损坏），Flink能否只恢复这个算子所在的Region？如果不能完整的恢复结果，你会怎么处理？（提示：非Source算子的恢复需要上游Source回放数据——但Region Failover不回放）

---

> **完整代码**：本章完整代码请参考附录或访问 https://github.com/flink-column/flink-practitioner  
> **思考题答案**：见附录文件 `appendix-answers.md`
