# 第9章：状态保鲜——ValueState与ListState初探

---

## 1. 项目背景

某电商平台需要实现"订单状态机跟踪"功能。订单在整个生命周期中会有多个状态变更事件：

```
订单创建(0) → 已支付(1) → 已发货(2) → 已完成(3)
                              → 已取消(-1) → 退款中(-2) → 已退款(-3)
```

每条状态变更事件格式：

```json
{"orderId":"ORD001","fromStatus":1,"toStatus":2,"timestamp":1714293932000}
```

Flink需要实时跟踪每个订单的**当前状态**，并且在订单到达"已完成"时，统计从创建到完成的**全链路耗时**。

这里最核心的问题是：**Flink需要"记住"每个订单的当前状态和关联的时间信息**。如果在Map里写：

```java
Map<String, OrderState> stateMap = new HashMap<>();  // ❌ 大忌！
```

这条代码在Flink里会出大问题——（1）HashMap是本地内存变量，TaskManager重启就丢了；（2）多个并行子任务各自持有自己的Map，状态不共享；（3）这个Map不在Checkpoint管理范围内，重启作业时全部丢失。

这就是 **Flink State（状态）** 要解决的问题。Flink提供了多种类型的状态存储，它们：

- 由Flink框架**统一管理**，自动纳入Checkpoint
- 支持**扩容**——并行度变化时自动rebalance
- 通过**State Backend**选择存储介质（内存 / RocksDB / 文件系统）
- 提供**TTL（Time-To-Live）** 自动清理过期状态

---

## 2. 项目设计

> 场景：小胖实现了订单状态跟踪，用了一个全局HashMap，作业一重启数据全没了。

**大师**：我看看你的代码……`static Map<String, OrderState> stateMap = new HashMap<>()`——换我20年前的Java Web项目可以，但在Flink里这是灾难。

**小胖**：为啥？Map存内存里重启当然会丢啊——重启之后再从Kafka消费补回来不就行了？

**大师**：第一，Flink默认的处理速度数万条/秒，如果每次重启都要"从头消费把历史数据全吃一遍"重建状态，启动时间可能长达几个小时。第二，你的Map不在Checkpoint里——Flink做故障恢复时，从Checkpoint恢复的是Kafka的Offset和你Map里的状态。但你的Map没被Checkpoint管理，恢复后Map是空的，Kafka Offset却恢复到了旧Offset——数据对不上，结果全错。

**技术映射：Flink State = 有状态算子中的"记忆"。它由Flink统一管理、自动Checkpoint、自动恢复。不使用Flink State而是自己维护全局变量，等于绕过了Flink的容错机制。**

**小白**：那Flink State有哪些类型？ValueState、ListState、MapState、ReducingState这些有什么区别？

**大师**：按你需要的场景来选——

- **ValueState<T>**：存一个单一值，比如"订单的当前状态"。适用于keyBy后的每个key只需要存一个值。
- **ListState<T>**：存一个列表，比如"用户最近10次浏览记录"。适用于需要追加数据但不支持随机访问的地方。
- **MapState<K, V>**：存一个Map，比如"用户到商品ID的收藏映射"。适用于需要按子key查询的场景。
- **ReducingState<T>** / **AggregatingState<T>**：自动聚合的累加器，比如"当前累计成交额"。

**小胖**：那状态大小有限制吗？全部存内存里会不会爆？

**大师**：取决于你的**State Backend**选型：
- **HashMapStateBackend**：状态全部在JVM堆内存中。读写快（纳秒级），但受堆大小限制。
- **RocksDBStateBackend**：状态存在RocksDB（嵌入式LSM-Tree KV数据库）中，可以超过JVM堆大小，但读写有序列化开销（微秒级）。

**技术映射：状态存储 = 权衡延迟 vs 容量。堆内快但小，RocksDB慢但大。生产环境状态超过1GB建议用RocksDB。**

**小白**：状态越积越大怎么办？比如订单完成之后的旧状态，我留着没用但一直占内存。

**大师**：设置**State TTL**。Flink 1.8+支持对keyed state设置TTL：

```java
StateTtlConfig ttlConfig = StateTtlConfig
    .newBuilder(Time.days(7))
    .setUpdateType(StateTtlConfig.UpdateType.OnCreateAndWrite)
    .setStateVisibility(StateTtlConfig.StateVisibility.NeverReturnExpired)
    .build();
ValueStateDescriptor<OrderState> descriptor = new ValueStateDescriptor<>("order-state", OrderState.class);
descriptor.enableTimeToLive(ttlConfig);
```

设置了TTL后，Flink会在状态过期后自动清理（在后台增量清理，避免STW）。

---

## 3. 项目实战

### 分步实现

#### 步骤1：ValueState——订单状态机跟踪

**目标**：用ValueState跟踪每个订单的当前状态，从创建跟踪到完成。

```java
package com.flink.column.chapter09;

import org.apache.flink.api.common.eventtime.WatermarkStrategy;
import org.apache.flink.api.common.functions.RichMapFunction;
import org.apache.flink.api.common.state.StateTtlConfig;
import org.apache.flink.api.common.state.ValueState;
import org.apache.flink.api.common.state.ValueStateDescriptor;
import org.apache.flink.api.common.time.Time;
import org.apache.flink.api.java.tuple.Tuple2;
import org.apache.flink.configuration.Configuration;
import org.apache.flink.streaming.api.datastream.DataStream;
import org.apache.flink.streaming.api.environment.StreamExecutionEnvironment;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import java.time.Duration;

/**
 * 订单状态机跟踪：使用ValueState记录每个订单的当前状态
 * 输入: <orderId>,<fromStatus>,<toStatus>,<timestamp>
 * 输出: 订单状态变更日志，以及订单完成时输出全链路耗时
 */
public class OrderStateMachine {

    private static final Logger LOG = LoggerFactory.getLogger(OrderStateMachine.class);

    public static void main(String[] args) throws Exception {
        StreamExecutionEnvironment env = StreamExecutionEnvironment.getExecutionEnvironment();
        env.setParallelism(2);

        DataStream<String> text = env.socketTextStream("localhost", 9999);

        DataStream<String> result = text
                .map(line -> {
                    String[] p = line.split(",");
                    return new OrderEvent(p[0],
                            Integer.parseInt(p[1]),
                            Integer.parseInt(p[2]),
                            Long.parseLong(p[3]));
                })
                .keyBy(e -> e.orderId)
                .map(new StateMachineFunction())
                .name("state-machine");

        result.print();

        env.execute("Chapter09-OrderStateMachine");
    }

    public static class OrderEvent {
        public String orderId;
        public int fromStatus;
        public int toStatus;
        public long timestamp;

        public OrderEvent(String orderId, int from, int to, long ts) {
            this.orderId = orderId; this.fromStatus = from;
            this.toStatus = to; this.timestamp = ts;
        }
    }

    /**
     * 订单状态机：用ValueState存储当前状态
     */
    public static class StateMachineFunction
            extends RichMapFunction<OrderEvent, String> {

        private transient ValueState<Integer> currentStatus;
        private transient ValueState<Long> createTime;

        @Override
        public void open(Configuration parameters) {
            // 当前状态（存活2天）
            StateTtlConfig ttl = StateTtlConfig.newBuilder(Time.days(2))
                    .setUpdateType(StateTtlConfig.UpdateType.OnCreateAndWrite)
                    .build();

            ValueStateDescriptor<Integer> statusDesc =
                    new ValueStateDescriptor<>("current-status", Integer.class);
            statusDesc.enableTimeToLive(ttl);
            currentStatus = getRuntimeContext().getState(statusDesc);

            ValueStateDescriptor<Long> createDesc =
                    new ValueStateDescriptor<>("create-time", Long.class);
            createDesc.enableTimeToLive(ttl);
            createTime = getRuntimeContext().getState(createDesc);
        }

        @Override
        public String map(OrderEvent event) throws Exception {
            Integer storedStatus = currentStatus.value();
            Long storedCreateTime = createTime.value();

            // 校验状态转移合法性
            if (storedStatus != null && storedStatus != event.fromStatus) {
                return String.format("[非法转移] 订单=%s, 当前状态=%d, 收到转移=%d→%d",
                        event.orderId, storedStatus, event.fromStatus, event.toStatus);
            }

            // 如果是订单创建（没有已有状态）
            if (storedStatus == null) {
                createTime.update(event.timestamp);
            }

            // 更新当前状态
            currentStatus.update(event.toStatus);

            // 如果订单已完成，计算全链路耗时
            if (event.toStatus == 3) {
                long cost = event.timestamp - storedCreateTime;
                return String.format("[订单完成] 订单=%s, 耗时=%dms, 状态流转: %d→%d",
                        event.orderId, cost, event.fromStatus, event.toStatus);
            }

            return String.format("[状态变更] 订单=%s: %d→%d, 耗时=%dms(累计)",
                    event.orderId, event.fromStatus, event.toStatus,
                    event.timestamp - (storedCreateTime != null ? storedCreateTime : event.timestamp));
        }
    }
}
```

**测试数据**：

```
ORD001,0,1,1000
ORD001,1,2,5000
ORD001,2,3,20000
ORD002,0,1,3000
ORD002,1,2,8000
ORD002,2,-1,15000
```

**预期输出**：

```
[状态变更] 订单=ORD001: 0→1, 耗时=0ms(累计)
[状态变更] 订单=ORD001: 1→2, 耗时=4000ms(累计)
[订单完成] 订单=ORD001, 耗时=19000ms, 状态流转: 2→3
[状态变更] 订单=ORD002: 0→1, 耗时=0ms(累计)
[状态变更] 订单=ORD002: 1→2, 耗时=5000ms(累计)
[状态变更] 订单=ORD002: 2→-1, 耗时=12000ms(累计)
```

#### 步骤2：ListState——用户最近浏览记录

**目标**：用ListState存储每个用户最近浏览的5篇新闻，超出上限自动丢弃最早。

```java
package com.flink.column.chapter09;

import org.apache.flink.api.common.functions.RichFlatMapFunction;
import org.apache.flink.api.common.state.ListState;
import org.apache.flink.api.common.state.ListStateDescriptor;
import org.apache.flink.api.common.state.StateTtlConfig;
import org.apache.flink.api.common.time.Time;
import org.apache.flink.configuration.Configuration;
import org.apache.flink.streaming.api.datastream.DataStream;
import org.apache.flink.streaming.api.environment.StreamExecutionEnvironment;
import org.apache.flink.util.Collector;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import java.util.ArrayList;
import java.util.List;

/**
 * 用户最近浏览记录：ListState存储每个用户最近5条浏览历史
 * 输入: <userId>,<newsId>,<timestamp>
 */
public class RecentBrowseHistory {

    private static final Logger LOG = LoggerFactory.getLogger(RecentBrowseHistory.class);
    private static final int MAX_HISTORY = 5;

    public static void main(String[] args) throws Exception {
        StreamExecutionEnvironment env = StreamExecutionEnvironment.getExecutionEnvironment();
        env.setParallelism(2);

        DataStream<String> text = env.socketTextStream("localhost", 9999);

        text.map(line -> {
            String[] p = line.split(",");
            return new BrowseEvent(p[0], p[1], Long.parseLong(p[2]));
        })
        .keyBy(e -> e.userId)
        .flatMap(new HistoryFunction())
        .print();

        env.execute("Chapter09-RecentBrowseHistory");
    }

    public static class BrowseEvent {
        public String userId, newsId;
        public long timestamp;
        public BrowseEvent(String u, String n, long t) { this.userId = u; this.newsId = n; this.timestamp = t; }
    }

    public static class HistoryFunction
            extends RichFlatMapFunction<BrowseEvent, String> {

        private transient ListState<String> historyState;

        @Override
        public void open(Configuration parameters) {
            ListStateDescriptor<String> desc = new ListStateDescriptor<>(
                    "browse-history", String.class);
            // TTL 7天
            StateTtlConfig ttl = StateTtlConfig.newBuilder(Time.days(7)).build();
            desc.enableTimeToLive(ttl);
            historyState = getRuntimeContext().getListState(desc);
        }

        @Override
        public void flatMap(BrowseEvent event, Collector<String> out) throws Exception {
            // 添加到浏览记录（追加到List末尾）
            historyState.add(event.newsId);

            // 读取所有记录，检查是否超出上限
            List<String> all = new ArrayList<>();
            historyState.get().forEach(all::add);

            // 如果超出上限，丢弃最旧的
            while (all.size() > MAX_HISTORY) {
                all.remove(0);  // 移除最早（List第一个）
            }

            // 重新写入
            historyState.update(all);

            out.collect(String.format("[用户=%s] 最近浏览(%d): %s",
                    event.userId, all.size(), String.join(" → ", all)));
        }
    }
}
```

**测试数据**：

```
u1,n001,1000
u1,n002,2000
u1,n003,3000
u1,n004,4000
u1,n005,5000
u1,n006,6000  # 超出上限，n001被丢弃
u2,n101,1000
```

**预期输出**：

```
[用户=u1] 最近浏览(1): n001
[用户=u1] 最近浏览(2): n001 → n002
[用户=u1] 最近浏览(3): n001 → n002 → n003
[用户=u1] 最近浏览(4): n001 → n002 → n003 → n004
[用户=u1] 最近浏览(5): n001 → n002 → n003 → n004 → n005
[用户=u1] 最近浏览(5): n002 → n003 → n004 → n005 → n006
[用户=u2] 最近浏览(1): n101
```

#### 步骤3：MapState——用户收藏课程映射

**目标**：用MapState存储"用户ID → 收藏课程ID列表"的映射关系。

```java
// MapState示例（展示核心API）
public static class FavoriteFunction extends RichMapFunction<FavoriteEvent, String> {
    private transient MapState<String, Boolean> favorites;

    @Override
    public void open(Configuration parameters) {
        MapStateDescriptor<String, Boolean> desc = new MapStateDescriptor<>(
                "favorites", String.class, Boolean.class);
        favorites = getRuntimeContext().getMapState(desc);
    }

    @Override
    public String map(FavoriteEvent event) throws Exception {
        if (event.action.equals("add")) {
            favorites.put(event.courseId, true);
        } else if (event.action.equals("remove")) {
            favorites.remove(event.courseId);
        }
        return String.format("用户=%s 收藏数=%d", event.userId, getSize());
    }

    private int getSize() throws Exception {
        int count = 0;
        for (Boolean v : favorites.values()) {
            if (v) count++;
        }
        return count;
    }
}
```

#### 步骤4：通过WebUI查看状态大小

**目标**：在Flink WebUI中观察作业的状态占用。

1. 运行订单状态机作业，输入几十条不同订单的数据
2. 打开Flink WebUI → Running Jobs → 点击作业 → **Metrics** 标签
3. 观察 `current-status` 状态的大小：

| Metric | 说明 |
|--------|------|
| `<operator>.state.name.current-status.current` | 状态中存储的记录数 |
| `<operator>.state.name.current-status.size` | 状态大小（字节） |
| `<operator>.state.name.current-status.sizePerEntry` | 平均每条状态大小 |

> **坑位预警**：如果状态大小持续增长不见收敛，说明State TTL没有生效或配置不当。在主函数中加入 `env.setStateBackend(new HashMapStateBackend())` 再观察。

### 可能遇到的坑

1. **ListState.get()返回的Iterable只能遍历一次**
   - 根因：ListState底层存储是异步的，多次get()可能产生不同快照
   - 解决：`List<String> all = new ArrayList<>(); historyState.get().forEach(all::add);` 一次性拷贝到ArrayList

2. **ValueState在未写入时返回null**
   - 根因：初始值为null——设计如此
   - 解决：用`value() != null`判断后再使用；或使用 `ValueStateDescriptor` 的 `defaultValue`（但需谨慎使用，可能导致隐式状态膨胀）

3. **RocksDB State Backend下ValueState读取性能慢**
   - 根因：RocksDB每次状态读写都需要序列化/反序列化
   - 解决：使用 `HashMapStateBackend` 替换（当状态量 < 1GB时）；或批量读写减少RocksDB访问次数

---

## 4. 项目总结

### State类型选择矩阵

| 状态类型 | 数据结构 | 典型容量 | 随机访问 | 适用场景 |
|---------|---------|---------|---------|---------|
| ValueState<T> | 单值 | <1KB/key | - | 当前状态、累加器 |
| ListState<T> | 有序列表 | <100条/key | 遍历 | 最近记录、操作日志 |
| MapState<K,V> | KV映射 | <10000条/key | 是（按K查询） | 配置映射、关系映射 |
| ReducingState<T> | 聚合值 | <1KB/key | - | 持续聚合（sum/min/max） |
| AggregatingState<IN,ACC,OUT> | 自定义累加器 | 自定义 | - | 复杂聚合（如去重计数） |

### State Backend选型

| Backend | 存储位置 | 读写延迟 | 容量上限 | 适用场景 |
|---------|---------|---------|---------|---------|
| HashMap | JVM堆 | <1μs | <堆大小(通常<32GB) | 小状态、高频读写 |
| RocksDB | 本地磁盘(LSM-Tree) | 10-100μs | 磁盘容量 | 大状态(GB~TB级) |

### 注意事项
- 所有State必须在`open()`方法中创建Descriptor并获取句柄，不允许在map/flatMap等方法中动态创建State
- State的key是`keyBy()`中选择的key——同一个key共享同一个State实例
- 不要将不可序列化的对象存入State（如Socket、Thread、Connection等）

### 常见踩坑经验

**案例1：ValueState中存了POJO，修改字段后State没更新**
- 根因：ValueState存的是对象引用，修改POJO字段后没有调用`update()`——Flink不知道状态变了
- 解方：修改后用`state.update(modifiedObj)`显式更新

**案例2：ListState不断增大到OOM**
- 根因：ListState中的元素只增不减，没有设置TTL或主动清理
- 解方：设置TTL(`StateTtlConfig`)；在每次写入后检查并裁剪列表大小

**案例3：MapState在RocksDB模式下遍历慢**
- 根因：MapState在RocksDB中底层是一个prefix的多个key-value，`iterator()`会被序列化为RocksDB Iterator扫描
- 解方：避免对MapState做全量遍历；使用`entries()`/`keys()`/`values()`的iterator时记得及时close

### 优点 & 缺点

| | Flink State（ValueState/ListState/MapState） | 本地HashMap/ArrayList自行管理 |
|------|-----------|-----------|
| **优点1** | 自动纳入Checkpoint，故障恢复零数据丢失 | 重启后全丢，靠Kafka重放重建需数小时 |
| **优点2** | 支持State TTL自动清理过期数据，防OOM | 需手动管理淘汰策略，容易内存泄漏 |
| **优点3** | RocksDB Backend可支持GB~TB级状态 | 受限于JVM堆大小，超过即OOM |
| **优点4** | 并行度变更时自动Rebalance | 扩容时需编写复杂的自定义迁移逻辑 |
| **缺点1** | 读写有序列化开销（特别是RocksDB模式下） | 直接堆内内存操作，读写零开销 |
| **缺点2** | 只能在KeyedStream上使用（keyBy后），使用范围受限 | 任何位置都可使用，无限制 |

### 适用场景

**典型场景**：
1. 订单状态机——ValueState跟踪每个订单的当前状态与创建时间
2. 用户最近浏览记录——ListState存储每个用户最近N条操作历史
3. 用户收藏/配置映射——MapState维护用户维度KV关系
4. 实时累计器——ReducingState/AggregatingState做持续聚合

**不适用场景**：
1. 简单无状态变换——引入State增加复杂度，纯Map/FlatMap即可完成
2. 需跨Task共享状态——Flink State是key隔离的，跨分区状态共享需用广播状态或外部存储

### 思考题

1. 在订单状态机中，如果订单完成(状态=3)后，我们希望保留这个状态7天用于数据分析，但7天后自动删除——State TTL应该怎么配置？OnCreateAndWrite和OnReadAndWrite有什么区别？

2. ListState是"追加末尾"的数据结构。如果要实现"最近5条浏览记录"，用ListState需要每次读取全量列表再裁剪——这是O(n)的。你能想到更高效的方式吗？（提示：可以考虑使用MapState<Long, String>用时间戳做key，或者使用环形缓冲区+外部存储）

---

> **完整代码**：本章完整代码请参考附录或访问 https://github.com/flink-column/flink-practitioner  
> **思考题答案**：见附录文件 `appendix-answers.md`

---

> **附录**：第3章思考题答案
> 1. keyBy后sum并行度=1：所有key的数据路由到同一个子任务(分区0)，单点处理瓶颈。即使上游有N个flatMap并行，数据全集中到1个sum子任务。
> 2. Socket Source并行度固定1，数据分发到flatMap(并行度4)时发生了rebalance（轮询分发）。实际作业的Task：Source(1) + flatMap(4) + sum(4) + print(4) = 需要13个Slot，但只有Source的并行度被限制了。
