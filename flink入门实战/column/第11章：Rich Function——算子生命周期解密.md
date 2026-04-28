# 第11章：Rich Function——算子生命周期解密

---

## 1. 项目背景

在前面章节的实战中，我们已经反复使用了`MapFunction`、`FlatMapFunction`、`FilterFunction`。但你有没有发现一个共同的问题：**在这些函数里，不能初始化连接池、不能读取配置文件、不能统计处理条数**。

比如这个需求：

> 从Kafka读取用户行为日志，每处理10000条就打印一次"已处理10000条"的进度。同时，需要在作业启动时从外部配置中心加载一个"黑名单"（被屏蔽的用户列表），在运行时过滤来自黑名单用户的行为。

如果用普通的`MapFunction`：

```java
// ❌ 不行——没有open()方法，没地方初始化
map(new MapFunction<String, String>() {
    @Override
    public String map(String value) {
        // 连个计数器都不知道放哪初始化
    }
});
```

你不能在构造函数里初始化黑名单——构造函数在算子反序列化时调用，此时还不知道算子运行在哪个子任务上、并行度是多少、怎么访问RuntimeContext。

也不能在map方法里每次调用都加载黑名单——每次处理都从远程配置中心拉数据，性能直接爆炸。

**Rich Function**正是为解决这些问题而生。它提供了一套完整的**生命周期回调**：
- `open(Configuration parameters)`：算子初始化时调用一次，适合做所有"一次性准备"
- `close()`：算子关闭时调用一次，适合做资源清理
- `getRuntimeContext()`：获取运行时上下文，包括子任务索引、累加器、状态句柄、广播变量等

---

## 2. 项目设计

> 场景：小胖想统计自己写的MapFunction一共处理了多少条数据。

**小胖**：我写了个MapFunction，map方法里放了个计数器++，但算子重启后计数器清0了。我想到一个办法——用static变量，这样所有实例共享一个计数器。

**大师**：stop！static变量在多TaskManager环境下是灾难——不同JVM进程的static不共享。而且static变量不在Checkpoint管理范围内，重启就丢。

**小白**：那用Flink的State来存计数器应该可以吧？ValueState<Long>类型。

**大师**：State确实可以持久化计数器，但如果只是想知道"这个算子总共处理了多少条"这种运行时指标，不需要持久化——用**累加器（Accumulator）** 就够了。

**技术映射：RichFunction提供三项基础能力——① open/close生命周期管理 ② getRuntimeContext获取运行时信息 ③ 累加器和MetricGroup用于监控。State用于持久化业务数据，Accumulator用于运行时统计指标。**

**小胖**：那open()和构造函数有什么区别？都在什么时候调用？

**大师**：看Flink算子初始化的完整顺序：

1. **反射构造**：Flink JobManager通过反射创建函数实例（调用无参构造器）
2. **序列化传输**：函数实例被序列化后发送到TaskManager
3. **反序列化重建**：TaskManager反序列化重建函数实例
4. **setRuntimeContext**：Flink注入RuntimeContext
5. **open()调用**：初始化完成，开始处理数据前调用open

所以open()是"一切准备就绪后你最可靠的初始化入口"。构造函数内不要做任何依赖RuntimeContext的操作——因为那时RuntimeContext还不存在。**技术映射：构造函数阶段：RuntimeContext未就绪；open阶段：一切就绪。**

**小白**：open之后，多个并行子任务的open是同时调用的吗？

**大师**：是的。如果作业并行度=4，4个子任务的open方法在4个Task线程中独立调用。你可以在open中通过`getRuntimeContext().getIndexOfThisSubtask()`知道自己属于哪个子任务——这在初始化分片资源时非常有用。

---

## 3. 项目实战

### 分步实现

#### 步骤1：RichMapFunction完整生命周期演示

**目标**：演示RichMapFunction中open、map、close的执行顺序和上下文访问。

```java
package com.flink.column.chapter11;

import org.apache.flink.api.common.functions.RichMapFunction;
import org.apache.flink.api.common.state.ValueState;
import org.apache.flink.api.common.state.ValueStateDescriptor;
import org.apache.flink.api.common.state.StateTtlConfig;
import org.apache.flink.api.common.time.Time;
import org.apache.flink.configuration.Configuration;
import org.apache.flink.metrics.Counter;
import org.apache.flink.streaming.api.datastream.DataStream;
import org.apache.flink.streaming.api.environment.StreamExecutionEnvironment;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * RichFunction生命周期演示：open → map(每条数据) → close
 * 演示：累加器、MetricGroup、RuntimeContext、State
 */
public class RichFunctionLifecycle {

    private static final Logger LOG = LoggerFactory.getLogger(RichFunctionLifecycle.class);

    public static void main(String[] args) throws Exception {
        StreamExecutionEnvironment env = StreamExecutionEnvironment.getExecutionEnvironment();
        env.setParallelism(2);

        DataStream<String> text = env.socketTextStream("localhost", 9999);

        text.map(new RichMapFunction<String, String>() {

            // ============== 状态与累加器 ==============
            private transient ValueState<Long> totalProcessed;  // 持久化计数
            private transient Counter recordCounter;            // 运行指标计数（不需要持久化）
            private int subtaskIndex;                            // 子任务索引

            // ============== 阶段1: open() ==============
            @Override
            public void open(Configuration parameters) {
                subtaskIndex = getRuntimeContext().getIndexOfThisSubtask();
                int parallelism = getRuntimeContext().getNumberOfParallelSubtasks();

                LOG.info("[子任务{}/{}] open() 被调用 —— 初始化资源",
                        subtaskIndex, parallelism);

                // 1. 初始化State（持久化计数器，TTL 1天）
                ValueStateDescriptor<Long> desc = new ValueStateDescriptor<>(
                        "total-count", Long.class);
                desc.enableTimeToLive(StateTtlConfig.newBuilder(Time.days(1)).build());
                totalProcessed = getRuntimeContext().getState(desc);

                // 2. 初始化MetricGroup累加器
                recordCounter = getRuntimeContext()
                        .getMetricGroup()
                        .counter("record_count");

                // 3. 模拟加载外部资源（如黑名单、配置等）
                LOG.info("[子任务{}/{}] open() 完成，已加载资源", subtaskIndex, parallelism);
            }

            // ============== 阶段2: map() ==============
            @Override
            public String map(String value) throws Exception {
                // 1. 累加器递增（运行时指标，不持久化）
                recordCounter.inc();

                // 2. State递增（持久化，纳入Checkpoint）
                Long count = totalProcessed.value();
                if (count == null) count = 0L;
                totalProcessed.update(count + 1);

                // 3. 每处理100条打一次进度日志
                if (count % 100 == 0) {
                    LOG.info("[子任务{}/{}] 累计处理 {} 条 (累加器={})",
                            subtaskIndex,
                            getRuntimeContext().getNumberOfParallelSubtasks(),
                            count + 1,
                            recordCounter.getCount());
                }

                return String.format("[子任务%d] %s (累计%d条)",
                        subtaskIndex, value.toUpperCase(), count + 1);
            }

            // ============== 阶段3: close() ==============
            @Override
            public void close() throws Exception {
                LOG.info("[子任务{}/{}] close() 被调用 —— 释放资源, 累加器={}",
                        subtaskIndex,
                        getRuntimeContext().getNumberOfParallelSubtasks(),
                        recordCounter.getCount());
            }

        }).print();

        env.execute("Chapter11-RichFunctionLifecycle");
    }
}
```

**测试验证**：

```bash
nc -lk 9999
```

输入几行数据，观察输出：

```
[子任务0/2] open() 被调用 —— 初始化资源
[子任务0/2] open() 完成，已加载资源
[子任务1/2] open() 被调用 —— 初始化资源
[子任务1/2] open() 完成，已加载资源
3> [子任务0] HELLO (累计1条)
2> [子任务1] WORLD (累计1条)
3> [子任务0] FLINK (累计2条)
...
# 按 Ctrl+C 停止作业时看到
[子任务0/2] close() 被调用 —— 释放资源, 累加器=3
[子任务1/2] close() 被调用 —— 释放资源, 累加器=2
```

#### 步骤2：RichFlatMapFunction——带黑名单过滤的日志处理

**目标**：模拟真实需求——从外部加载黑名单，在flatMap中过滤。

```java
package com.flink.column.chapter11;

import org.apache.flink.api.common.functions.RichFlatMapFunction;
import org.apache.flink.api.common.state.MapState;
import org.apache.flink.api.common.state.MapStateDescriptor;
import org.apache.flink.api.common.state.StateTtlConfig;
import org.apache.flink.api.common.time.Time;
import org.apache.flink.configuration.Configuration;
import org.apache.flink.streaming.api.datastream.DataStream;
import org.apache.flink.streaming.api.environment.StreamExecutionEnvironment;
import org.apache.flink.util.Collector;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.util.Arrays;
import java.util.HashSet;
import java.util.Set;

/**
 * 使用RichFlatMapFunction实现：
 * 1. open时加载黑名单（模拟从配置文件加载）
 * 2. map时过滤黑名单用户
 * 3. 运行时动态更新黑名单（通过State持久化）
 */
public class RichFlatMapBlackList {

    private static final Logger LOG = LoggerFactory.getLogger(RichFlatMapBlackList.class);

    public static void main(String[] args) throws Exception {
        StreamExecutionEnvironment env = StreamExecutionEnvironment.getExecutionEnvironment();
        env.setParallelism(2);

        DataStream<String> text = env.socketTextStream("localhost", 9999);

        text.keyBy(line -> line.split(",")[0])  // 按userId keyBy
            .flatMap(new BlackListFilter())
            .print();

        env.execute("Chapter11-BlackListFilter");
    }

    public static class BlackListFilter
            extends RichFlatMapFunction<String, String> {

        // 运行时可变黑名单（持久化，在MapState中存储）
        private transient MapState<String, Boolean> blackListState;
        // 初始黑名单（从配置文件加载）——open时初始化
        private final Set<String> initialBlackList = new HashSet<>();

        @Override
        public void open(Configuration parameters) {
            int subtask = getRuntimeContext().getIndexOfThisSubtask();

            // 模拟从配置中心加载初始黑名单（实际生产可从Redis/配置中心读取）
            initialBlackList.add("user_black_001");
            initialBlackList.add("user_black_002");
            // 也可以读取环境变量或配置文件
            String extra = parameters.getString("blacklist.extra", "");
            if (!extra.isEmpty()) {
                initialBlackList.addAll(Arrays.asList(extra.split(",")));
            }

            LOG.info("[子任务{}] 黑名单加载完成: {}个用户", subtask, initialBlackList.size());

            // MapState：动态增删黑名单（持久化）
            MapStateDescriptor<String, Boolean> desc = new MapStateDescriptor<>(
                    "blacklist", String.class, Boolean.class);
            desc.enableTimeToLive(StateTtlConfig.newBuilder(Time.days(30)).build());
            blackListState = getRuntimeContext().getMapState(desc);

            // 将初始黑名单写入State
            for (String userId : initialBlackList) {
                blackListState.put(userId, true);
            }
        }

        @Override
        public void flatMap(String line, Collector<String> out) throws Exception {
            // 输入: userId,logContent,timestamp
            String[] parts = line.split(",");
            if (parts.length < 2) return;

            String userId = parts[0];
            String logContent = parts[1];

            // 查黑名单——如果在黑名单中，不输出
            Boolean isBlack = blackListState.get(userId);
            if (isBlack != null && isBlack) {
                LOG.debug("[子任务{}] 黑名单用户: {}, 已过滤", 
                        getRuntimeContext().getIndexOfThisSubtask(), userId);
                return;
            }

            // 放行
            out.collect(String.format("[放行] userId=%s, content=%s", userId, logContent));
        }

        @Override
        public void close() throws Exception {
            LOG.info("[子任务{}] close() —— 黑名单最终大小: {}", 
                    getRuntimeContext().getIndexOfThisSubtask(),
                    blackListState.keys().spliterator().estimateSize());
        }
    }
}
```

**测试数据**：

```
user_normal,visit_page_home,1000
user_black_001,fraud_attempt,2000
user_normal,click_product,3000
user_black_002,login_failed,4000
```

**预期输出**：

```
[放行] userId=user_normal, content=visit_page_home
[放行] userId=user_normal, content=click_product
```

#### 步骤3：RichFilterFunction——动态规则过滤

**目标**：用RichFunction配合广播状态（Broadcast State）实现动态规则变更。

```java
// 提示：广播状态将在中级篇详细展开。这里仅给出模式示意
// BroadcastStream + RichFilterFunction 可以实现运行时动态更新过滤规则

// 在RichFilterFunction中，通过 getBroadcastState(desc) 访问广播进来的规则
// 当规则流有新数据时，自动更新广播状态，Filter函数立即生效

// 生产案例：风控规则引擎的核心模式
```

#### 步骤4：通过MetricGroup暴露自定义指标

**目标**：在RichFunction中自定义监控指标，集成到Prometheus/Grafana。

```java
// 以下指标可通过Flink的Metric Reporter暴露给外部监控系统

// 1. Counter（计数器）
Counter cnt = getRuntimeContext().getMetricGroup().counter("my_counter");
cnt.inc();     // +1
cnt.inc(10L);  // +10

// 2. Meter（速率，每秒处理数）
Meter meter = getRuntimeContext().getMetricGroup().meter("my_meter", new MeterView(60));
meter.markEvent();        // 记录1个事件
meter.markEvent(100);     // 记录100个事件

// 3. Gauge（瞬时值）
getRuntimeContext().getMetricGroup().gauge("my_gauge", (Gauge<Integer>) () -> currentValue);

// 4. Histogram（分布）
Histogram histogram = getRuntimeContext().getMetricGroup().histogram("my_histogram", 
        new DescriptiveStatisticsHistogram(1000));
histogram.update(value);  // 记录一个样本点
```

### 可能遇到的坑

1. **open()里new了重量级对象（如KafkaProducer），但作业还没运行就OOM了**
   - 根因：所有算子同时初始化，多个子任务open()同时创建大量连接
   - 解决：在open()中使用连接池（避免每个子任务都创建独立连接），或者延迟到map()时通过单例懒加载

2. **getRuntimeContext()在构造函数中调用报NPE**
   - 根因：RuntimeContext在构造函数之后才注入
   - 解方：把所有依赖RuntimeContext的初始化移入open()

3. **close()没有执行——作业被kill -9强杀或TaskManager崩溃**
   - 根因：close()只在正常停止时调用，非正常退出不会执行
   - 解方：不要在close()里放"必须执行"的操作（如flush缓存）。对这些操作，Checkpoint的snapshotState()才是可靠的回调

---

## 4. 项目总结

### RichFunction vs 普通Function

| 能力 | 普通Function | RichFunction |
|------|-------------|-------------|
| open/close生命周期 | ❌ | ✅ |
| getRuntimeContext | ❌ | ✅ |
| 访问State | ❌（需额外包装） | ✅ 原生支持 |
| MetricGroup/累加器 | ❌ | ✅ |
| 广播状态 | ❌ | ✅ |
| 使用复杂度 | 低 | 中 |

### 四种RichFunction

| 函数 | 输入→输出 | 场景 |
|------|----------|------|
| RichMapFunction<IN, OUT> | 1→1 | 数据变换+状态+指标 |
| RichFlatMapFunction<IN, OUT> | 1→0..N | 过滤+变换+条件分支 |
| RichFilterFunction<IN> | 1→Boolean | 带状态的过滤逻辑 |
| RichCoMapFunction<IN1, IN2, OUT> | 双流合并 | 双流Connect场景 |

### 生命周期方法调用顺序

```
构造函数()
  │
  ▼
setRuntimeContext()   ← Flink内部调用
  │
  ▼
open(config)          ← 开发者实现：初始化
  │
  ▼
map/flatMap/filter() ← 每条数据调用
  │
  ▼
close()               ← 开发者实现：清理
```

### 注意事项
- open/close分别在算子每个并行实例中调用一次——不是全局一次
- open()中的异常会被Flink捕获并触发作业失败——所以不要在open()里抛出非预期异常
- 不要将不可序列化的对象存为RichFunction的成员变量（如Socket、Thread）

### 常见踩坑经验

**案例1：RichMapFunction中open()加载了数据库连接，但连接在运行几小时后断开**
- 根因：数据库server的wait_timeout关闭了空闲连接
- 解方：改用连接池（HikariCP/Druid），配置validationQuery心跳；或对每个map调用做连接有效性检查

**案例2：累加器counter的值与WebUI显示不一致**
- 根因：`getRuntimeContext().getMetricGroup().counter()` 创建的是Flink内置累加器，其值通过Metric Reporter上报。如果Metric Reporter配置了采样或聚合（如取平均），显示值可能与本地累加不等
- 解方：在WebUI的Task Managers → Metrics标签查确认原始值；检查Metric Reporter配置

**案例3：open()内注册的定时器在close()后仍被触发**
- 根因：Timer是基于Keyed State注册的，会被持久化。作业重启后Timer从State恢复并继续触发
- 解方：如果注册的是"一次性"定时器，触发后立即删除；如果作业生命周期结束，使用Savepoint停止时Timer也会被持久化，再恢复时会重新触发

### 优点 & 缺点

| | RichFunction（RichMapFunction等） | 普通Function（MapFunction等） |
|------|-----------|-----------|
| **优点1** | 提供open/close生命周期回调，资源管理有始有终 | 无生命周期回调，需在map内做资源检查 |
| **优点2** | 通过getRuntimeContext访问子任务索引、并行度、State、累加器 | 无法获取运行时上下文，无State、无计数器 |
| **优点3** | 支持累加器和MetricGroup，便于集成监控系统 | 无法暴露自定义指标到Flink WebUI/Prometheus |
| **优点4** | 可结合广播状态实现运行时动态规则变更 | 无广播状态支持，规则变更需重启作业 |
| **缺点1** | 代码量更大，需要实现open/close等方法 | 轻量简洁，Lambda一行搞定 |
| **缺点2** | open中初始化重量级资源易导致TaskManager OOM | 无初始化逻辑，资源开销最小 |

### 适用场景

**典型场景**：
1. 需要加载外部资源——在open中初始化数据库连接池、加载IP库/黑名单等
2. 需要访问运行时上下文——获取子任务索引、并行度、累加器
3. 需要自定义监控指标——通过MetricGroup暴露Counter/Meter/Gauge
4. 需要结合State做有状态变换——ValueState/ListState/MapState等

**不适用场景**：
1. 纯无状态简单变换——Lambda表达式更简洁，无需引入RichFunction
2. 无需运行时上下文的简单过滤——普通FilterFunction即可满足

### 思考题

1. 假设有一个RichMapFunction，在open()中创建了一个`new Random(seed)`作为成员变量。这个Random实例是线程安全的吗？如果该算子的多个map()被多个线程并发调用，会出现什么问题？（提示：Flink默认每个算子的并行实例是单线程的，但这与算子链情况有关）

2. 在BlackListFilter的例子中，blackListState用了MapState<String, Boolean>。如果之后需要支持"按黑名单类别过滤"（如level-1放行、level-2记录日志、level-3拦截），State结构应该怎么改造？

---

> **完整代码**：本章完整代码请参考附录或访问 https://github.com/flink-column/flink-practitioner  
> **思考题答案**：见附录文件 `appendix-answers.md`
