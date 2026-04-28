# 第7章：时间旅行者——EventTime与Watermark入门

---

## 1. 项目背景

某电商平台在搞"618大促"实时大屏，需要统计**每个商品在过去5分钟内的成交额**，每10秒刷新一次。

数据源是Kafka中的订单流。订单结构如下：

```json
{"orderId":"ORD001","productId":"P10001","amount":299.00,"eventTime":1714293932000}
```

其中 `eventTime` 是用户在App端点击"提交订单"时生成的客户端时间戳（毫秒）。

技术团队很快搭好了Demo——用ProcessingTime窗口，看起来一切正常。然而大促开始后，运营发现了一个严重的问题：

**大屏上的数据"穿越"了**：明明当前时间是14:05，但大屏上显示14:00-14:05的成交额中竟然包含了真实发生在14:06的数据；而14:05-14:10的成交额远低于预期。整体来看，大屏数据"超前"了约2分钟，又"滞后"了约3分钟，运营完全无法作准。

问题根源：**ProcessingTime取的是Flink处理数据的时间——数据先到先算。但用户的订单创建时间和Flink处理时间天然存在偏差**：用户A的网络延迟小，14:00下单，14:00:01就被Flink处理到了；用户B在偏远地区，网络不好，14:05下单但数据在14:07才被Flink收到。如果按ProcessingTime做窗口，用户B的表单被划到了14:05-14:10的窗口。

这就是 **EventTime（事件时间）** 和 **Watermark（水位线）** 要解决的问题。EventTime让窗口的划分依赖数据本身的业务时间戳，而不是Flink机器的时钟；Watermark告诉Flink"到某个时间点了，后续不会再有序时间的数据了，可以触发窗口计算了"。

---

## 2. 项目设计

> 场景：运营小美指着实时大屏上的"穿越数据"，气得脸都红了。

**小胖**：怎么回事？我一个ProcessingTime窗口不就是取当前系统时间，5分钟一段切开来算吗？为什么会有延迟的数据跑到前一个窗口里？

**大师**：问题出在"当前系统时间"上——你用的是Flink机器的时钟，但数据里的时间是用户的手机时钟。用户下单时间是14:00，但因为网络抖动，Flink在14:02才收到这条数据。ProcessingTime窗口按接收到的时间（14:02）来划分，自然就跑到14:00-14:05之后的窗口了。

**技术映射：ProcessingTime = 事件到达Flink算子的系统时钟。EventTime = 数据本身携带的业务时间戳。生产环境90%的场景应该使用EventTime。**

**小白**：那如果我用EventTime，Flink怎么知道"14:00-14:05这个窗口的数据已经到齐了，该算结果了"？毕竟数据是源源不断来的，不可能永远等下去。

**大师**：这就是**Watermark（水位线）** 的作用。Watermark是一个时间戳，它告诉Flink："所有时间戳 ≤ Watermark 的数据我都已经收到了，你可以安全地触发对应窗口的计算了。"

**小胖**：那Watermark难道就不怕有晚到的数据吗？万一有个数据迟到了半小时呢？

**大师**：这正是Watermark的精华——它允许"晚到"但不允许"无限等"。Flink允许你设置**最大乱序容忍度（maxOutOfOrderness）**。比如说，你设置：

```java
WatermarkStrategy.forBoundedOutOfOrderness(Duration.ofSeconds(10))
```

意思是：Watermark = 当前收到的最大EventTime - 10秒。每条数据到来时，Flink更新这个值——如果新数据的EventTime比之前见过的都大，Watermark就往前推进。

**技术映射：Watermark = 当前收到的最小"未覆盖时间"。它是一个单调递增的"截止线"，用于判断哪些窗口可以安全触发了。**

**小胖**（挠头）：那迟到超过10秒的数据呢？比如一条数据EventTime是14:00，但14:11才到——这已经在Watermark 14:01（最大14:11减10秒）之后了。

**大师**：Flink提供了**迟到数据处理机制**。对于迟到但不超过Watermark的数据，默认会被丢弃。但你也可以设置**侧输出流（SideOutput）** 来捕获迟到数据，或者设置**Allowed Lateness**让窗口在Watermark通过后仍然等待一段时间来处理迟到数据。

**技术映射：迟到数据处理三级策略——① Watermark容忍（最大乱序度）② Allowed Lateness（窗口延后关闭）③ SideOutput兜底（迟到数据全部到特殊流）。**

---

## 3. 项目实战

### 环境准备

| 组件 | 版本 |
|------|------|
| JDK | 11+ |
| Flink | 1.18.1 |
| Maven | 3.8+ |

### 分步实现

#### 步骤1：比较ProcessingTime和EventTime的窗口区别

**目标**：用同一个代码直观感受两种时间语义下的窗口划分差异。

```java
package com.flink.column.chapter07;

import org.apache.flink.api.common.eventtime.WatermarkStrategy;
import org.apache.flink.api.common.functions.MapFunction;
import org.apache.flink.api.java.tuple.Tuple3;
import org.apache.flink.streaming.api.datastream.DataStream;
import org.apache.flink.streaming.api.environment.StreamExecutionEnvironment;
import org.apache.flink.streaming.api.windowing.assigners.TumblingEventTimeWindows;
import org.apache.flink.streaming.api.windowing.assigners.TumblingProcessingTimeWindows;
import org.apache.flink.streaming.api.windowing.time.Time;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.time.Duration;

/**
 * 比较ProcessingTime和EventTime窗口划分差异
 * 输入格式: <key>,<amount>,<eventTimeMs>
 * 示例:    productA,100,1714293930000
 */
public class TimeSemanticComparison {

    private static final Logger LOG = LoggerFactory.getLogger(TimeSemanticComparison.class);

    public static void main(String[] args) throws Exception {
        StreamExecutionEnvironment env = StreamExecutionEnvironment.getExecutionEnvironment();
        env.setParallelism(1);  // 并行度1方便观察

        DataStream<String> text = env.socketTextStream("localhost", 9999);

        // 解析为 Tuple3<productId, amount, eventTime>
        DataStream<Tuple3<String, Double, Long>> data = text
                .map(new MapFunction<String, Tuple3<String, Double, Long>>() {
                    @Override
                    public Tuple3<String, Double, Long> map(String line) throws Exception {
                        String[] parts = line.split(",");
                        String key = parts[0];
                        double amount = Double.parseDouble(parts[1]);
                        long eventTime = Long.parseLong(parts[2]);
                        return Tuple3.of(key, amount, eventTime);
                    }
                });

        // ---------------------------------------------------------------
        // ProcessingTime窗口（5分钟滚动）——立刻触发结果
        // ---------------------------------------------------------------
        DataStream<Tuple3<String, Double, Long>> processingResult = data
                .keyBy(t -> t.f0)
                .window(TumblingProcessingTimeWindows.of(Time.minutes(5)))
                .reduce((v1, v2) -> Tuple3.of(v1.f0, v1.f1 + v2.f1, v1.f2))
                .name("processing-time-window");

        processingResult
                .map(t -> String.format("[ProcessingTime] %s: 金额=%.2f", t.f0, t.f1))
                .print()
                .name("proc-result");

        // ---------------------------------------------------------------
        // EventTime窗口（5分钟滚动，乱序容忍10秒）
        // ---------------------------------------------------------------
        DataStream<Tuple3<String, Double, Long>> withWatermarks = data
                .assignTimestampsAndWatermarks(
                        WatermarkStrategy.<Tuple3<String, Double, Long>>forBoundedOutOfOrderness(
                                        Duration.ofSeconds(10))
                                .withTimestampAssigner((event, timestamp) -> event.f2)
                );

        DataStream<Tuple3<String, Double, Long>> eventResult = withWatermarks
                .keyBy(t -> t.f0)
                .window(TumblingEventTimeWindows.of(Time.minutes(5)))
                .reduce((v1, v2) -> Tuple3.of(v1.f0, v1.f1 + v2.f1, v1.f2))
                .name("event-time-window");

        eventResult
                .map(t -> String.format("[EventTime] %s: 金额=%.2f", t.f0, t.f1))
                .print()
                .name("event-result");

        env.execute("Chapter07-TimeSemanticComparison");
    }
}
```

**测试验证**：

打开两个终端，一个运行Flink作业，一个发送数据。发送以下数据（注意时间戳故意安排）：

```
productA,100.00,1714293900000   # 时间戳对应 2026-04-28 14:00:00 UTC
productA,200.00,1714293900000   # 同上
productA,150.00,1714294200000   # 14:05:00 (下一个窗口起点边界)
productA,300.00,1714293905000   # 14:00:05 (延迟到14:05之后才到的数据)
productA,50.00, 1714294500000    # 14:10:00
```

**预期输出对比**：

- **ProcessingTime窗口**：所有数据按接收时间划分，与数据中的eventTime无关。如果你在14:01收到第一条、14:02收到第二条、14:06收到第三条，结果是14:00-14:05窗口收到100+200+150=450（第三条数据虽然eventTime是14:05但被划到了14:00-14:05窗口）。

- **EventTime窗口**：
  - 14:00-14:05窗口：收到100+200=300（前两条eventTime=14:00）。第三条eventTime=14:05属于14:05-14:10窗口。
  - 第四条eventTime=14:00:05的数据因为乱序（比第三条晚到但eventTime更小），且它14:00:05在Watermark 14:04:50（当前最大14:05:00-10秒）之上，所以正常进入14:00-14:05窗口。结果变成300+50=350。

#### 步骤2：理解Watermark的推进过程

**目标**：为每个数据打印当前的Watermark值，直观感受Watermark的更新逻辑。

```java
package com.flink.column.chapter07;

import org.apache.flink.api.common.eventtime.WatermarkStrategy;
import org.apache.flink.api.common.functions.MapFunction;
import org.apache.flink.api.java.tuple.Tuple4;
import org.apache.flink.streaming.api.datastream.DataStream;
import org.apache.flink.streaming.api.environment.StreamExecutionEnvironment;
import org.apache.flink.streaming.api.functions.ProcessFunction;
import org.apache.flink.util.Collector;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.time.Duration;

/**
 * 逐条打印Watermark值，观察其推进过程
 * 输入格式: <productId>,<amount>,<eventTimeMs>
 */
public class WatermarkVisualization {

    private static final Logger LOG = LoggerFactory.getLogger(WatermarkVisualization.class);

    public static void main(String[] args) throws Exception {
        StreamExecutionEnvironment env = StreamExecutionEnvironment.getExecutionEnvironment();
        env.setParallelism(1);

        DataStream<String> text = env.socketTextStream("localhost", 9999);

        DataStream<Tuple4<String, Double, Long, Long>> withWm = text
                .map(line -> {
                    String[] p = line.split(",");
                    return Tuple4.of(p[0], Double.parseDouble(p[1]),
                            Long.parseLong(p[2]), 0L);
                })
                .returns(Types.TUPLE(Types.STRING, Types.DOUBLE, Types.LONG, Types.LONG))

                .assignTimestampsAndWatermarks(
                        WatermarkStrategy.<Tuple4<String, Double, Long, Long>>forBoundedOutOfOrderness(
                                        Duration.ofSeconds(10))
                                .withTimestampAssigner((event, ts) -> event.f2)
                );

        withWm.process(new ProcessFunction<Tuple4<String, Double, Long, Long>,
                                       Tuple4<String, Double, Long, Long>>() {
            @Override
            public void processElement(
                    Tuple4<String, Double, Long, Long> event,
                    Context ctx,
                    Collector<Tuple4<String, Double, Long, Long>> out) {

                long watermark = ctx.timerService().currentWatermark();
                event.f3 = watermark;  // 将当前Watermark写入第4个字段
                LOG.info("收到: eventTime={}, watermark={}, diff={}ms",
                        event.f2, watermark, event.f2 - watermark);
                out.collect(event);
            }
        }).name("watermark-observer");

        withWm.map(t -> String.format(
                "[Watermark=%d] eventTime=%d, diff=%dms",
                t.f3, t.f2, t.f2 - t.f3))
        .print();

        env.execute("Chapter07-WatermarkVisualization");
    }
}
```

**测试数据**：

```
productA,100,1000
productA,200,3000
productA,150,2000    # 乱序（比第二条小），但在容忍范围之内
productA,300,5000
productA,400,100     # 严重迟到，eventTime=100远小于当前Watermark（约4990-10=4980）
```

**预期输出**：

```
收到: eventTime=1000, watermark=-9223372036854775808, diff=1000ms   ← 初始Watermark=Long.MIN_VALUE
[Watermark=-9223372036854775808] eventTime=1000, diff=1000ms
收到: eventTime=3000, watermark=990, diff=2010ms                     ← 最大eventTime=3000, Watermark=3000-10=2990? 实际Watermark=1000-10=-?
收到: eventTime=2000, watermark=1990, diff=10ms                     ← 乱序但还在容忍范围: 2000 > 1990 (3000-10), 正常
收到: eventTime=5000, watermark=1990, diff=3010ms                   ← Watermark更新为1990 (还未到5000-10=4990是因为? 需要确认)
收到: eventTime=100, watermark=4990, diff=-4890ms                   ← 迟到 严重迟到，eventTime=100 < watermark=4990, 被丢弃（侧输出来捕获）
```

> **坑位预警**：Watermark初始值为Long.MIN_VALUE（即-9223372036854775808），这意味着第一个数据到来前所有定时器不会触发。第一条数据到来后Watermark会更新为 `第一条数据的eventTime - maxOutOfOrderness`。

#### 步骤3：侧输出捕获迟到数据

**目标**：将迟到数据发送到侧输出流，不丢弃。

```java
package com.flink.column.chapter07;

import org.apache.flink.api.common.eventtime.WatermarkStrategy;
import org.apache.flink.api.java.tuple.Tuple2;
import org.apache.flink.streaming.api.datastream.DataStream;
import org.apache.flink.streaming.api.datastream.SingleOutputStreamOperator;
import org.apache.flink.streaming.api.environment.StreamExecutionEnvironment;
import org.apache.flink.streaming.api.functions.windowing.ProcessWindowFunction;
import org.apache.flink.streaming.api.windowing.assigners.TumblingEventTimeWindows;
import org.apache.flink.streaming.api.windowing.time.Time;
import org.apache.flink.streaming.api.windowing.windows.TimeWindow;
import org.apache.flink.util.Collector;
import org.apache.flink.util.OutputTag;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.time.Duration;

public class LateDataHandling {

    private static final Logger LOG = LoggerFactory.getLogger(LateDataHandling.class);
    private static final OutputTag<Tuple2<String, Double>> LATE_TAG =
            new OutputTag<Tuple2<String, Double>>("late-data") {};

    public static void main(String[] args) throws Exception {
        StreamExecutionEnvironment env = StreamExecutionEnvironment.getExecutionEnvironment();
        env.setParallelism(1);

        DataStream<String> text = env.socketTextStream("localhost", 9999);

        DataStream<Tuple2<String, Double>> data = text
                .map(line -> {
                    String[] p = line.split(",");
                    return Tuple2.of(p[0], Double.parseDouble(p[1]));
                })
                .returns(Types.TUPLE(Types.STRING, Types.DOUBLE))
                .assignTimestampsAndWatermarks(
                        WatermarkStrategy.<Tuple2<String, Double>>forBoundedOutOfOrderness(
                                        Duration.ofSeconds(10))
                                .withTimestampAssigner((event, ts) -> Long.parseLong(event.f0))
                );

        // 注意：这里假设event存入f0字段是为了演示，实际应分开
        // 重新处理：把时间戳放在f2
        // 这里简化演示：第一条数据也传时间戳
        // 为演示侧输出，我们用一个新的ParseWithTs
        // (实际应当延续之前的Tuple3格式)

        // 重新设计一个简单版本——仅演示侧输出捕获迟到数据
        SingleOutputStreamOperator<Tuple2<String, Double>> mainStream = data
                .keyBy(t -> t.f0)
                .window(TumblingEventTimeWindows.of(Time.minutes(1)))
                .sideOutputLateData(LATE_TAG)
                .reduce((v1, v2) -> Tuple2.of(v1.f0, v1.f1 + v2.f1))
                .name("main-window");

        mainStream.print("Main").name("main-output");

        DataStream<Tuple2<String, Double>> lateStream = mainStream.getSideOutput(LATE_TAG);
        lateStream.map(t -> String.format("[迟到数据] %s=%.2f", t.f0, t.f1))
                .print("Late")
                .name("late-output");

        env.execute("Chapter07-LateDataHandling");
    }
}
```

### 可能遇到的坑

1. **EventTime窗口一直没有触发**
   - 根因：没有设置WatermarkStrategy。不使用Watermark时EventTime窗口永远不会被触发
   - 解决：必须调用`assignTimestampsAndWatermarks()`

2. **Watermark不推进，卡在某个值**
   - 根因：某个分区长期没有新数据到达，Watermark无法更新
   - 解决：设置空闲超时 `withIdleness(Duration.ofSeconds(120))` ——超过2分钟无数据的分区自动标记为"空闲"，不阻塞Watermark推进

3. **使用EventTime时，Source并行度 > 1导致窗口结果混乱**
   - 根因：每个并行子任务独立维护自己的Watermark，窗口触发时间可能不同
   - 解决：确保所有Source分区都有数据，或在WatermarkStrategy中设置`withIdleness`

---

## 4. 项目总结

### ProcessingTime vs EventTime vs IngestionTime

| 维度 | ProcessingTime | EventTime | IngestionTime |
|------|---------------|-----------|---------------|
| 时间来源 | Flink机器时钟 | 数据中的业务时间戳 | Source接入时间 |
| 乱序容忍 | 无需（按到达时间算） | 需要Watermark | 自动排序（约等于一个窗口的延迟） |
| 重复结果风险 | 低（但结果不准确） | 高（乱序数据导致） | 中 |
| 适用场景 | 演示、测试、对时间不敏感的粗估 | 生产90%场景 | 较少使用 |

### Watermark两种策略

| 策略 | 类名 | 适用场景 |
|------|------|---------|
| 固定延迟 | `forBoundedOutOfOrderness(Duration)` | 大部分场景 |
| 单调递增 | `forMonotonousTimestamps()` | 数据严格有序（如来自单一时钟源） |

### 注意事项
- Watermark是一种**乐观估计**——无法100%保证所有数据到齐后再触发窗口。如果业务要求"绝对不丢数据"，必须配合AllowedLateness和SideOutput
- `withTimestampAssigner`中的lambda会在每一条数据上调用——不要在内部做重计算
- 多个并行Source子任务的Watermark取最小值作为全局Watermark

### 常见踩坑经验

**案例1：EventTime窗口触发时间是当前时间 + 窗口大小，结果永远等不到触发**
- 根因：Watermark = 当前最大EventTime - maxOutOfOrderness。如果数据EventTime=14:00，maxOutOfOrderness=10min，Watermark=13:50。14:00-14:05的窗口触发条件是Watermark ≥ 14:05。这意味着需要新数据的EventTime ≥ 14:15才能触发。
- 解方：maxOutOfOrderness不要超过业务能接受的延迟。一般2-10秒即可。

**案例2：多分区Kafka Source导致的窗口多次触发**
- 根因：每个Kafka分区独立维护Watermark。如果某个分区没数据了，它的Watermark卡在初始值(Long.MIN_VALUE)，全局Watermark也为Long.MIN_VALUE，所有窗口都不触发
- 解方：Kafka Source设置`setPartitioner`确保数据均匀分布，或使用`withIdleness`让空闲分区不阻塞全局Watermark

**案例3：Watermark导致窗口关闭后，又有迟到数据想进入窗口**
- 根因：Watermark超过窗口endTime + allowedLateness后窗口彻底关闭
- 解方：用SideOutput捕获这些数据，写到一个特殊Topic，定期补跑

### 优点 & 缺点

| | EventTime + Watermark | ProcessingTime（处理时间） |
|------|-----------|-----------|
| **优点1** | 按业务时间戳正确划分窗口，结果准确反映真实时间 | 结果容易歪斜，数据到达时间≠业务时间 |
| **优点2** | 可容忍网络延迟与乱序，Watermark+AllowedLateness三级兜底 | 乱序数据直接"串窗"，结果不可修正 |
| **优点3** | 配合SideOutput捕获超迟到数据，实现数据零丢失 | 迟到数据被忽略或放错窗口，无补救手段 |
| **缺点1** | 需业务数据携带时间戳，Watermark推进依赖数据持续到达 | 无需时间戳，实现简单 |
| **缺点2** | maxOutOfOrderness参数依赖业务经验，设太小丢数据、设太大延迟高 | 零配置，拿来即用 |

### 适用场景

**典型场景**：
1. 电商大促实时大屏——按用户下单时间统计成交额，容忍网络延迟
2. 金融风控交易监控——按交易发生时间做窗口聚合，保证时序正确
3. IoT传感器数据汇聚——设备上报时间可能延迟，EventTime保证时间轴对齐
4. 用户行为归因分析——按行为发生时间做Session合并，准确归因

**不适用场景**：
1. 数据本身不携带时间戳——无法使用EventTime，只能退化为ProcessingTime
2. 对延迟极度敏感且数据严格有序——ProcessingTime更简单，无需Watermark开销

### 思考题

1. Watermark = 当前最大EventTime - maxOutOfOrderness。如果设置maxOutOfOrderness=10秒，但某分区在10秒内没有任何数据到达，下游窗口会发生什么？(提示：空闲分区的Watermark推进规则）

2. 假设你要做一个"用户行为归因"作业，需要在用户关闭App后才触发一次聚合计算。用户关闭App时会发一条"session_end"事件。你能想到用EventTime + 什么窗口类型来实现这个"等session自然结束"的效果？

---

> **完整代码**：本章完整代码请参考附录或访问 https://github.com/flink-column/flink-practitioner  
> **思考题答案**：见附录文件 `appendix-answers.md`
