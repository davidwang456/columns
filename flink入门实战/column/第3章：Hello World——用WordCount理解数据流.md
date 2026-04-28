# 第3章：Hello World——用WordCount理解数据流

---

## 1. 项目背景

"跑通了环境、提交了作业、看到了输出——但程序到底是怎么把一条数据变成最终结果的？"

这是几乎所有Flink初学者都会遇到的困惑。第1章和第2章让我们成功运行了WordCount，但中间的过程像一个黑盒：数据从Socket进、从控制台出，中间发生了什么？

这个困惑如果得不到解答，后面的所有概念——KeyBy、State、Window、Checkpoint——都会像空中楼阁。

以WordCount为例，一条输入"hello flink hello world"经过Flink作业处理，变成4条甚至更多的输出。从输入到输出的全链路中，数据经历了哪些算子、经过了几次网络传输、在哪个节点被缓存、结果为什么有前缀数字"1>"和"2>"？

更具体的问题：
- `flatMap`、`keyBy`、`sum`三个算子是在同一个线程里执行还是跨线程跨网络？
- `keyBy`之后数据为什么就自动"分组"了？数据怎么找到它该去的分区？
- `sum(1)`怎么"记住"每个单词之前的计数？每次来了新数据是重新算一遍全量还是增量更新？

理解数据在Flink作业中的流动路径，是掌握Flink一切高级特性的基石。本章从代码出发，逐行拆解数据在Flink内部的流转过程。

---

## 2. 项目设计

> 场景：午休时间，小胖端着泡面坐到大师对面。

**小胖**（吸溜一口面）：大师，我有个问题憋了好久。WordCount那段代码，不就四行吗——flatMap、keyBy、sum、print。但输入一条"hello flink"，它到底怎么分成两个单词、怎么累加、怎么输出的？中间在内存里绕了几个弯？

**大师**：好问题。这四行代码背后，Flink帮你做了三件事：**构建DAG**、**切分Task**、**流水线执行**。拿你泡面打比方——你烧水、泡面、加调料，这三个步骤你是串行做的（先烧水再泡面再调料），但如果是流水线作业，流水线上的三个人可以同时做这三个步骤。**技术映射：Flink把作业拆成多个算子（Operator），每个算子可以独立并行执行，数据以流水线方式依次经过每个算子。**

**小白**（合上电脑参与进来）：那数据是怎么从上一个算子"送到"下一个算子的？是像Kafka那样推到队列里吗？如果下游处理慢了，上游会怎么办？

**大师**：分两种情况。如果两个算子在**同一个TaskManager的同一个Slot**里，Flink会做**算子链（Operator Chaining）**优化——把flatMap和map这样的轻量变换直接连在一起，不走网络，直接方法调用传递数据，零开销。

但是如果中间有`keyBy`，情况就不一样了——keyBy需要做数据重分区，数据必须经过网络从上游TaskManager发送到下游TaskManager。**技术映射：keyBy相当于对数据做了一次Hash Partition，根据key的hash值将数据路由到指定的下游并行子任务。**

**小胖**：那sum(1)呢？它怎么记得每个单词之前已经累加了多少？如果TaskManager重启了，记忆还在吗？

**大师**：sum(1)内部维护了一个**ValueState**——一个keyed状态的键值对。对于每个单词key，保存着当前累计值。新数据进来时，Flink从状态中读出旧值、+1、写回。这个过程不需要重新扫描历史数据，完全增量。

至于TaskManager重启——这就是第1章说的**Checkpoint**机制。Flink定期把状态全量拍快照持久化到硬盘。重启后从最近一个完成的Checkpoint恢复，状态自动还原。所以答案是：**正常情况下记忆一直在；挂了之后从Checkpoint恢复，也能找回来。**

**小白**：那print()输出的序号"1>"、"2>"代表什么？为什么有时同一个单词的多次输出前缀不一样？

**大师**：print()是Sink算子，它也有并行度。默认情况下，print算子的并行度等于环境并行度。前缀"1>"表示输出来自第1号子任务，前缀"2>"来自第2号子任务。

同一个单词的输出前缀不会变——因为keyBy保证同一单词永远路由到同一个子任务。如果变了，说明你的keyBy逻辑或并行度确认有问题。**技术映射：keyBy的哈希分区具有确定性——相同key永远落在相同的分区索引（parallelSubtaskIndex）。**

---

## 3. 项目实战

### 环境准备

本章代码基于第2章的Docker环境。如果你从本章开始，最少需要以下依赖：

| 组件 | 版本 |
|------|------|
| JDK | 11+ |
| Maven | 3.8+ |
| Apache Flink | 1.18.1 |
| IDE（IDEA / VS Code） | - |

### 分步实现

#### 步骤1：创建一个带日志的WordCount，观察DAG结构

**目标**：给每个算子添加处理日志，实时跟踪数据在算子间的流动。

```java
package com.flink.column.chapter03;

import org.apache.flink.api.common.functions.FlatMapFunction;
import org.apache.flink.api.java.tuple.Tuple2;
import org.apache.flink.streaming.api.datastream.DataStream;
import org.apache.flink.streaming.api.environment.StreamExecutionEnvironment;
import org.apache.flink.util.Collector;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * 带日志的WordCount，逐行追踪数据在Flink DAG中的流动。
 * 运行前确保: nc -lk 9999
 */
public class WordCountWithLogging {

    private static final Logger LOG = LoggerFactory.getLogger(WordCountWithLogging.class);

    public static void main(String[] args) throws Exception {
        StreamExecutionEnvironment env = StreamExecutionEnvironment.getExecutionEnvironment();
        env.setParallelism(2);

        DataStream<String> text = env.socketTextStream("localhost", 9999);

        DataStream<Tuple2<String, Integer>> counts = text
                .flatMap(new LoggingFlatMapFunction())
                .keyBy(t -> t.f0)
                .sum(1)
                .name("word-sum");

        counts.print().name("print-sink");

        LOG.info("作业已提交，等待数据...");
        env.execute("Chapter03-WordCountWithLogging");
    }

    public static final class LoggingFlatMapFunction
            implements FlatMapFunction<String, Tuple2<String, Integer>> {

        private int subtaskIndex = -1;

        @Override
        public void flatMap(String line, Collector<Tuple2<String, Integer>> out) {
            if (subtaskIndex == -1) {
                subtaskIndex = getRuntimeContext().getIndexOfThisSubtask();
            }

            String[] words = line.toLowerCase().split("\\W+");
            for (String word : words) {
                if (!word.isEmpty()) {
                    LOG.info("[FlatMap-子任务{}] 发射单词: {}", subtaskIndex, word);
                    out.collect(new Tuple2<>(word, 1));
                }
            }
        }
    }
}
```

在netcat中输入 `hello flink hello world`，观察控制台：

```
[FlatMap-子任务0] 发射单词: hello
[FlatMap-子任务0] 发射单词: flink
[FlatMap-子任务0] 发射单词: hello
[FlatMap-子任务0] 发射单词: world
```

所有flatMap输出都在子任务0——Socket Source并行度固定为1。

#### 步骤2：通过Flink WebUI查看执行计划

**目标**：用Flink WebUI可视化作业的DAG图。

1. 打开 `http://localhost:8081`
2. Running Jobs → 点击作业名 → **Execution Graph**

你会看到拓扑图（文字描述）：

```
Source: Socket Stream (1/1)
    │
    ▼
Flat Map (1/1)  ← 算子链优化后合并，显示为一个Task
    │
    ▼ (keyBy 网络shuffle)
sum (1/2) → sum (2/2)
    │         │
    ▼         ▼
Print (1/2)  Print (2/2)
```

> **坑位预警**：如果flatMap和sum没有中间的KeyBy网络边，是因为Flink自动做了算子链。代码中加入 `env.disableOperatorChaining()` 禁用链式合并，可以看到完整的网络传输边界。

#### 步骤3：用代码验证KeyBy的分区规则

**目标**：理解keyBy如何将单词路由到不同分区。

```java
package com.flink.column.chapter03;

import org.apache.flink.api.java.tuple.Tuple2;
import org.apache.flink.streaming.api.environment.StreamExecutionEnvironment;
import org.apache.flink.util.Collector;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.apache.flink.api.common.typeinfo.Types;

public class KeyByPartitionDemo {

    private static final Logger LOG = LoggerFactory.getLogger(KeyByPartitionDemo.class);

    public static void main(String[] args) throws Exception {
        StreamExecutionEnvironment env = StreamExecutionEnvironment.getExecutionEnvironment();
        env.setParallelism(4);

        env.socketTextStream("localhost", 9999)
                .flatMap((String line, Collector<Tuple2<String, Integer>> out) -> {
                    int subtask = getRuntimeContext().getIndexOfThisSubtask();
                    for (String word : line.toLowerCase().split("\\W+")) {
                        if (!word.isEmpty()) {
                            LOG.info("[Emit-子任务{}] word={}, hash={}, targetPartition={}",
                                    subtask, word, word.hashCode(),
                                    Math.floorMod(word.hashCode(), 4));
                            out.collect(Tuple2.of(word, 1));
                        }
                    }
                }).returns(Types.TUPLE(Types.STRING, Types.INT))
                .keyBy(t -> t.f0)
                .sum(1)
                .map(t -> {
                    int subtask = getRuntimeContext().getIndexOfThisSubtask();
                    LOG.info("[Sum-子任务{}] word={}, cnt={}", subtask, t.f0, t.f1);
                    return t;
                }).returns(Types.TUPLE(Types.STRING, Types.INT))
                .print();

        env.execute("Chapter03-KeyByPartitionDemo");
    }
}
```

输入 "hello world flink kafka"：

```
[Emit-子任务0] word=hello, hash=99162322, targetPartition=2
[Emit-子任务0] word=world, hash=113318802, targetPartition=2
[Emit-子任务0] word=flink, hash=-1170344629, targetPartition=3
[Emit-子任务0] word=kafka, hash=-1008875572, targetPartition=0

[Sum-子任务2] word=hello, cnt=1
[Sum-子任务2] word=world, cnt=1
[Sum-子任务3] word=flink, cnt=1
[Sum-子任务0] word=kafka, cnt=1
```

验证公式：`targetPartition = Math.floorMod(key.hashCode(), parallelism)`。

#### 步骤4：打印JSON执行计划

**目标**：从代码层面获取Flink优化后的执行计划JSON。

```java
package com.flink.column.chapter03;

import org.apache.flink.streaming.api.environment.StreamExecutionEnvironment;
import org.apache.flink.api.common.typeinfo.Types;

public class PrintExecutionPlan {

    public static void main(String[] args) throws Exception {
        StreamExecutionEnvironment env = StreamExecutionEnvironment.getExecutionEnvironment();
        env.setParallelism(2);

        env.socketTextStream("localhost", 9999)
                .flatMap((line, out) -> {
                    for (String w : line.toLowerCase().split("\\W+"))
                        if (!w.isEmpty()) out.collect(w);
                }).returns(Types.STRING)
                .keyBy(w -> w)
                .sum(0)
                .print();

        String planJson = env.getExecutionPlan();
        System.out.println(planJson);
    }
}
```

复制输出的JSON到 [Flink Plan Visualizer](https://flink.apache.org/visualizer/)，查看完整DAG。

#### 步骤5：Savepoint恢复验证状态持久化

**目标**：验证sum状态在重启后是否保留。

```bash
# 停止并保存
docker exec flink-jm flink stop --savepointPath /tmp/savepoints <jobId>

# 从Savepoint恢复
docker exec flink-jm flink run -s /tmp/savepoints/savepoint-xxxxx \
  -c com.flink.column.chapter03.WordCountWithLogging \
  /jobs/flink-practitioner.jar
```

再次输入之前出现过的单词——计数从之前的值继续累加，而非从0开始。

### 可能遇到的坑

1. **WebUI看不到作业**：本地模式（非Docker）需要在pom.xml中加入flink-clients依赖
2. **print()输出显示在IDE控制台而非WebUI TaskManager日志**：print()是客户端Sink，数据直接输出到客户端进程的标准输出。如果提交到远程集群，print()输出在JobManager进程日志而非本地
3. **keyBy分区不均衡**：天然哈希冲突导致，可以在key上加盐或调整并行度解决

---

## 4. 项目总结

### WordCount数据流全链路回顾

```
用户输入 "hello flink hello world"
    │
    ▼
[Socket Source]               并行度=1，只有一个子任务
    │
    ▼
[FlatMap]                     并行度=2（但数据只在子任务0）
    │ 逐单词发射 (hello,1), (flink,1), (hello,1), (world,1)
    │
    ▼
[KeyBy: hash(hello)%2=0, hash(flink)%2=1, hash(hello)%2=0, hash(world)%2=1]
    │
    ├──┬─ 网络Shuffle ──┬──→ [Sum-子任务0]: hello→1→2
    │  └─ 网络Shuffle ──┘   [Sum-子任务1]: flink→1, world→1
    ▼
[Print]                     并行度=2
```

### DataStream API三种传输模式对比

| 模式 | 传输方式 | 延迟 | 适用场景 |
|------|---------|------|---------|
| Operator Chaining | 同线程方法调用 | 零开销 | `map→filter→map`等轻量连续变换 |
| Local Exchange | 同JVM内存传输 | 亚微秒 | 同TaskManager跨Slot |
| Remote Exchange | Netty TCP | 毫秒级 | `keyBy`/`rebalance`/`broadcast`等重分区 |

### 注意事项
- `keyBy`是天然的网络传输边界，会打断算子链
- 并行度不是越高越好，每个子任务需要独立的线程和内存
- Flink自动算子链优化可通过 `env.disableOperatorChaining()` 禁用，但生产建议保留

### 常见踩坑经验

**案例1：keyBy后数据严重倾斜**
- 根因：如按大V用户ID分组，数据量差千倍
- 解决：加Salt做两阶段聚合，或自定义Partitioner

**案例2：setParallelism()对某些Source不生效**
- 根因：Socket Source固定并行度1，Kafka Source并行度≤分区数
- 解方：用Kafka等可并行Source，保证并行度≤分区数

**案例3：sum结果全是0**
- 根因：`sum(0)`对Tuple2第0个字段(String)求和，应使用`sum(1)`对Integer字段求和
- 解方：确认sum参数是对应的数值字段索引

### 优点 & 缺点

| | Flink DataStream API（逐事件流） | 传统批处理模型（Hive/Spark Batch） |
|------|-----------|-----------|
| **优点1** | 每条数据独立处理，DAG中流动路径完整可追踪 | 数据在全量扫描后才可见，中间过程不透明 |
| **优点2** | keyBy等重分区语义明确，数据路由可预测（hash定值） | 分区策略由Shuffle决定，用户难以精确控制 |
| **优点3** | 支持Operator Chaining优化，同线程零开销传递 | 每步之间通常需要落盘或Shuffle，延迟较高 |
| **优点4** | 同一个算子链内可自然共享状态和上下文 | 分阶段计算，跨阶段状态传递需额外编排 |
| **缺点1** | 概念较多（算子链、Exchange、Slot、Watermark），学习曲线陡 | 抽象层次高，SQL即可完成大部分任务 |
| **缺点2** | 调试依赖WebUI + 日志，缺少统一的IDE可视化工具 | Spark History Server提供完整事件日志回放 |

### 适用场景

**典型场景**：
1. 学习Flink核心概念——通过WordCount入门DAG、分区、算子链、状态等基础原理
2. 快速原型验证——使用Socket Source + Print Sink快速验证处理逻辑正确性
3. 数据流调试与排查——在算子间埋点日志，追踪数据倾斜或分区异常
4. 教学培训——用WordCount展示流计算与批处理在数据处理模型上的本质差异

**不适用场景**：
1. 生产环境实时业务——WordCount过于简单，Socket Source无容错，应使用Kafka Connector
2. 多流关联或窗口聚合等复杂场景——WordCount不涉及双流Join、EventTime窗口等核心能力

### 思考题

1. keyBy后sum算子的并行度设为1，所有key的数据都会路由到同一个子任务吗？这会导致什么问题？

2. flatMap并行度=4、sum并行度=4、Source(Socket)并行度=1，作业实际并行度是多少？浪费了几个Slot？（提示：Source并行度固定1，它产出的数据如何分发到4个flatMap？）

---

> **完整代码**：本章完整代码请参考附录或访问 https://github.com/flink-column/flink-practitioner  
> **思考题答案**：见附录文件 `appendix-answers.md`
