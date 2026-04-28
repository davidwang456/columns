# 第21章：双流Join全集——Regular/Interval/Window Join

---

## 1. 项目背景

某电商平台需要实时匹配"订单流"和"支付流"——用户在App上下单后，可能立即支付，也可能稍后支付。Flink需要将同一订单的"下单事件"和"支付事件"关联起来，计算出"从下单到支付的平均耗时"。

这就是经典的**双流Join**问题：

- **订单流**（Order Stream）：`{orderId, userId, amount, createTime}`
- **支付流**（Payment Stream）：`{orderId, payAmount, payTime, status}`

关系型数据库中，两个表JOIN是标准操作。但在流计算中，两个无限的流要如何JOIN？数据是陆续到达的——订单来了但支付可能还没到，支付到了但订单可能还没来？

Flink提供了三种双流Join方式：

| Join类型 | 时间约束 | 状态需求 | 适用场景 |
|---------|---------|---------|---------|
| **Window Join** | 两条流在同一个时间窗口内 | 窗口内的数据缓存在状态 | 同时间段内的关联（如"同一分钟内的下单和支付"） |
| **Interval Join** | 一条流的事件时间在另一条流的指定时间范围内 | 时间范围内的数据缓存在状态 | 有固定时间差关联（如"下单后1小时内支付"） |
| **Regular Join** | 无时间约束（全局无限） | 所有历史数据（需TTL） | 任意时间关联（如"订单完成后任何时候的售后事件"） |

---

## 2. 项目设计

> 场景：小胖写的订单支付关联作业，跑了一天后发现很多订单没匹配上——支付已经完成了但关联的结果始终没输出。

**小胖**：我用的是Window Join，窗口10分钟。但有些用户是下单后过了半小时才支付的——早就出了窗口，匹配不上。

**大师**：Window Join要求两条流的数据必须在同一个窗口边界内。如果时间差大于窗口大小，数据就不在同一个窗口——匹配不上。

**小白**：那就用Interval Join？可以指定"下单后1小时内支付的订单"为关联范围。

**大师**：对的。Interval Join不需要两条流在同一个窗口——只需要两条流的时间差在指定范围内（如上界1小时、下界0）。Flink会将上界范围内的数据缓存在状态中。

**技术映射：Interval Join = 以一条流的事件为基准，在另一条流中"回看"一段时间内的数据。本质上是一个有界的State Lookup。**

**小胖**：那Regular Join呢？它没有时间窗口，是不是可以关联任意时间的订单和支付？

**大师**：Regular Join在SQL中就是标准的JOIN。在流上，它需要将**两侧的所有历史数据**保留在状态中——因为任何时间到达的任意一条流数据都可能与另一侧的历史数据匹配。如果不设TTL，这个状态会无限增长。

**技术映射：Regular Join = 两侧状态持久化所有历史数据。本质上是一个无限增长的MapState<TKey, List<TValue>>。一定要设TTL，否则OOM。**

**小白**：那我应该怎么选？

**大师**：三条原则：
- 如果两条流的时间差有业务上限（如"支付必须在下单后24小时内完成"）→ **Interval Join**
- 如果两条流的数据需要在同一个时间段内关联（如"每分钟的广告曝光和点击"）→ **Window Join**
- 如果关联没有时间限制（如"永久查找订单的退款记录"）→ **Regular Join（务必设TTL）**

---

## 3. 项目实战

### 分步实现

#### 步骤1：Window Join——同一分钟内的下单和支付

**目标**：使用Tumbling Window Join，统计每分钟成功匹配的订单对。

```java
package com.flink.column.chapter21;

import org.apache.flink.api.common.eventtime.WatermarkStrategy;
import org.apache.flink.api.java.tuple.Tuple3;
import org.apache.flink.api.java.tuple.Tuple4;
import org.apache.flink.streaming.api.datastream.DataStream;
import org.apache.flink.streaming.api.environment.StreamExecutionEnvironment;
import org.apache.flink.streaming.api.functions.co.ProcessJoinFunction;
import org.apache.flink.streaming.api.windowing.assigners.TumblingEventTimeWindows;
import org.apache.flink.streaming.api.windowing.time.Time;
import org.apache.flink.util.Collector;
import java.time.Duration;

/**
 * Window Join：同一个1分钟窗口内的订单流和支付流关联
 * 输入(order): <orderId>,<userId>,<amount>,<eventTime>
 * 输入(payment): <orderId>,<payAmount>,<payTime>
 */
public class WindowJoinDemo {

    public static void main(String[] args) throws Exception {
        // 这里使用DataStream API的CoGroup（不是SQL）
        // DataStream API中Window Join通过join() + where() + equalTo() + window()
        // 但需要两条流的类型一致——生产环境建议用SQL的Window Join（更简洁）
        // 下面展示SQL方式的Window Join
        
        // SQL方式更推荐——见步骤3
    }
}
```

> **说明**：DataStream API的Window Join要求两条流的类型相同（通过`DataStream.join()`），使用不够灵活。生产环境建议用SQL方式（见步骤3）。

#### 步骤2：Interval Join——下单后1小时内的支付匹配

**目标**：使用Interval Join关联订单流和支付流，匹配"下单后1小时内支付的订单"。

```java
package com.flink.column.chapter21;

import org.apache.flink.api.common.eventtime.WatermarkStrategy;
import org.apache.flink.api.java.tuple.Tuple2;
import org.apache.flink.streaming.api.datastream.DataStream;
import org.apache.flink.streaming.api.environment.StreamExecutionEnvironment;
import org.apache.flink.streaming.api.functions.co.ProcessJoinFunction;
import org.apache.flink.streaming.api.windowing.time.Time;
import org.apache.flink.util.Collector;
import java.time.Duration;

/**
 * Interval Join：订单流（左）+ 支付流（右）
 * 关联条件：order.eventTime <= payment.eventTime <= order.eventTime + 1小时
 * 
 * 输入(order): <orderId>,<amount>,<eventTime>
 * 输入(payment): <orderId>,<payAmount>,<status>,<eventTime>
 */
public class IntervalJoinDemo {

    public static void main(String[] args) throws Exception {
        StreamExecutionEnvironment env = StreamExecutionEnvironment.getExecutionEnvironment();
        env.setParallelism(1);

        // ========== 订单流 ==========
        DataStream<OrderEvent> orders = env.socketTextStream("localhost", 9998)
                .map(line -> {
                    String[] p = line.split(",");
                    return new OrderEvent(p[0], p[1], Double.parseDouble(p[2]),
                            Long.parseLong(p[3]));
                })
                .assignTimestampsAndWatermarks(
                        WatermarkStrategy.<OrderEvent>forBoundedOutOfOrderness(
                                        Duration.ofSeconds(5))
                                .withTimestampAssigner((e, ts) -> e.eventTime)
                );

        // ========== 支付流 ==========
        DataStream<PaymentEvent> payments = env.socketTextStream("localhost", 9997)
                .map(line -> {
                    String[] p = line.split(",");
                    return new PaymentEvent(p[0], Double.parseDouble(p[1]),
                            p[2], Long.parseLong(p[3]));
                })
                .assignTimestampsAndWatermarks(
                        WatermarkStrategy.<PaymentEvent>forBoundedOutOfOrderness(
                                        Duration.ofSeconds(5))
                                .withTimestampAssigner((e, ts) -> e.eventTime)
                );

        // ========== Interval Join ==========
        // 左流(orders)的key = orderId，右流(payments)的key = orderId
        // 时间范围：payment.eventTime在 [order.eventTime, order.eventTime + 1小时] 内
        DataStream<String> joined = orders
                .keyBy(o -> o.orderId)
                .intervalJoin(payments.keyBy(p -> p.orderId))
                .between(Time.seconds(0), Time.hours(1))    // 时间范围
                .upperBoundExclusive()                       // 排除上界（可选）
                .process(new ProcessJoinFunction<OrderEvent, PaymentEvent, String>() {

                    @Override
                    public void processElement(
                            OrderEvent order,
                            PaymentEvent payment,
                            Context ctx,
                            Collector<String> out) {

                        long cost = payment.eventTime - order.eventTime;
                        out.collect(String.format(
                                "[匹配成功] 订单=%s, 金额=%.2f, 支付状态=%s, " +
                                "下单→支付耗时=%dms",
                                order.orderId, order.amount, payment.status, cost));
                    }
                });

        joined.print();

        env.execute("Chapter21-IntervalJoin");
    }

    public static class OrderEvent {
        public String orderId, userId;
        public double amount;
        public long eventTime;
        public OrderEvent() {}
        public OrderEvent(String oid, String uid, double amt, long ts) {
            this.orderId = oid; this.userId = uid; this.amount = amt; this.eventTime = ts;
        }
    }

    public static class PaymentEvent {
        public String orderId, status;
        public double payAmount;
        public long eventTime;
        public PaymentEvent() {}
        public PaymentEvent(String oid, double amt, String st, long ts) {
            this.orderId = oid; this.payAmount = amt; this.status = st; this.eventTime = ts;
        }
    }
}
```

**测试时需要打开两个Socket端口**：

```bash
# 终端1（订单流，端口9998）
nc -lk 9998
ORD001,u1,100.00,1000
ORD002,u2,200.00,2000

# 终端2（支付流，端口9997）
nc -lk 9997
ORD001,100.00,SUCCESS,5000        # 下单后4秒支付 → 匹配
ORD002,200.00,SUCCESS,7200000     # 2小时后支付 → 超出1小时 → 不匹配
ORD003,50.00,SUCCESS,3000          # 订单不存在 → 不匹配（孤立的支付）
```

**预期输出**：

```
[匹配成功] 订单=ORD001, 金额=100.00, 支付状态=SUCCESS, 下单→支付耗时=4000ms
```

#### 步骤3：SQL方式——Regular Join + Window Join

**目标**：在Flink SQL中实现三种Join，语法更简洁。

```sql
-- ========== 1. Regular Join（全局关联，无限状态） ==========
-- 注意：必须是equi-join（等值连接），必须设置状态TTL
INSERT INTO matched_orders
SELECT o.orderId, o.userId, o.amount, p.payAmount, p.status
FROM orders o
JOIN payments p ON o.orderId = p.orderId;

-- 状态TTL配置（避免无限增长）
SET table.exec.state.ttl = 24h;

-- ========== 2. Window Join（滚动窗口+时间条件） ==========
INSERT INTO window_matched
SELECT o.orderId, o.amount, p.payAmount,
       TUMBLE_END(o.rowtime, INTERVAL '1' MINUTE) AS window_end
FROM orders o, payments p
WHERE o.orderId = p.orderId
  AND o.rowtime BETWEEN p.rowtime - INTERVAL '1' MINUTE
                    AND p.rowtime + INTERVAL '1' MINUTE
  AND TUMBLE(o.rowtime, INTERVAL '1' MINUTE) = TUMBLE(p.rowtime, INTERVAL '1' MINUTE);

-- ========== 3. Interval Join（SQL中通过时间条件实现） ==========
INSERT INTO interval_matched
SELECT o.orderId, o.amount, p.payAmount,
       (p.rowtime - o.rowtime) AS cost_ms
FROM orders o, payments p
WHERE o.orderId = p.orderId
  AND p.rowtime BETWEEN o.rowtime
                    AND o.rowtime + INTERVAL '1' HOUR;
```

#### 步骤4：双流Join的完整Java+SQL集成

**目标**：在Table API中注册Kafka表，执行Interval Join查询。

```java
package com.flink.column.chapter21;

import org.apache.flink.streaming.api.environment.StreamExecutionEnvironment;
import org.apache.flink.table.api.bridge.java.StreamTableEnvironment;
import org.apache.flink.table.api.Table;

public class SQLIntervalJoin {

    public static void main(String[] args) {
        StreamExecutionEnvironment env = StreamExecutionEnvironment.getExecutionEnvironment();
        StreamTableEnvironment tableEnv = StreamTableEnvironment.create(env);

        // 注册订单表
        tableEnv.executeSql(
            "CREATE TABLE orders (" +
            "  orderId STRING, userId STRING, amount DOUBLE, " +
            "  eventTime BIGINT, " +
            "  ts AS TO_TIMESTAMP_LTZ(eventTime, 3), " +
            "  WATERMARK FOR ts AS ts - INTERVAL '5' SECOND" +
            ") WITH (" +
            "  'connector' = 'kafka', 'topic' = 'order-topic', " +
            "  'properties.bootstrap.servers' = 'kafka:9092', 'format' = 'json' " +
            ")");

        // 注册支付表
        tableEnv.executeSql(
            "CREATE TABLE payments (" +
            "  orderId STRING, payAmount DOUBLE, status STRING, " +
            "  eventTime BIGINT, " +
            "  ts AS TO_TIMESTAMP_LTZ(eventTime, 3), " +
            "  WATERMARK FOR ts AS ts - INTERVAL '5' SECOND" +
            ") WITH (" +
            "  'connector' = 'kafka', 'topic' = 'payment-topic', " +
            "  'properties.bootstrap.servers' = 'kafka:9092', 'format' = 'json' " +
            ")");

        // Interval Join
        Table result = tableEnv.sqlQuery(
            "SELECT o.orderId, o.amount, p.payAmount, " +
            "  (p.ts - o.ts) AS cost_interval " +
            "FROM orders o, payments p " +
            "WHERE o.orderId = p.orderId " +
            "  AND p.ts BETWEEN o.ts AND o.ts + INTERVAL '1' HOUR");

        tableEnv.toDataStream(result).print();

        try {
            env.execute("Chapter21-SQLIntervalJoin");
        } catch (Exception e) {
            e.printStackTrace();
        }
    }
}
```

### 可能遇到的坑

1. **Interval Join的`between()`上下界单位不同导致结果空白**
   - 根因：`between(Time.seconds(0), Time.hours(1))`中第二个参数虽然传了`Time.hours(1)`，但底层实现是毫秒。不同Time类的转换要小心
   - 解决：统一使用毫秒：`between(Time.milliseconds(0), Time.milliseconds(3600000))`

2. **Regular Join状态无限增长OutOfMemoryError**
   - 根因：没有设置TTL。所有历史数据全量缓存在State中
   - 解方：SET table.exec.state.ttl=24h；或使用Interval Join代替

3. **Window Join结果为空——两条流的时间戳相差很小但不在同一窗口**
   - 根因：两条流的Watermark不同步。如果order流Watermark推进快，payment流慢，order数据在窗口触发时payment数据还没到
   - 解方：确保两条流的Watermark策略一致；或改用Interval Join

---

## 4. 项目总结

### 三种Join对比

| 维度 | Window Join | Interval Join | Regular Join |
|------|------------|--------------|-------------|
| 时间约束 | 同一窗口 | 时间范围 | 无 |
| 状态持有时间 | 窗口大小 | 上界-下界 | 无限（需TTL） |
| 输出时机 | 窗口触发 | 匹配时立即 | 匹配时立即 |
| 数据类型要求 | 两条流类型部分一致 | 类型可不同 | 类型可不同 |
| 推荐实现方式 | SQL | DataStream API / SQL | SQL（需TTL） |

### 选择决策

```
关联是否有时间限制？
├── 有固定时间窗口（如"每分钟"）→ Window Join
├── 有相对时间范围（如"支付在订单后的1小时内"）→ Interval Join
└── 无时间限制（如"永久查询历史退款"）→ Regular Join + TTL
```

### 注意事项
- Interval Join需要两条流都有Watermark策略——没有EventTime的流不能做Interval Join
- 双流Join的性能瓶颈在于**State的存储和查询**。大状态的Join建议使用RocksDB State Backend
- SQL中的Regular Join会触发**Retraction机制**——同一主键的新数据到来，会先发送撤回消息（-U），再发送新结果（+U）

### 常见踩坑经验

**案例1：Interval Join匹配到的结果比预期少**
- 根因：支付的时间比订单早（`payment.eventTime < order.eventTime`），但`between`的下界是0——不允许payment在order之前
- 解方：根据业务调整下界为负数：`between(Time.hours(-1), Time.hours(1))`

**案例2：SQL Window Join报错"Both tables must have the same window"**
- 根因：SQL Window Join要求两侧窗口定义完全相同（包括窗口类型、大小、步长）
- 解方：确认两侧的GROUP BY窗口函数完全一致

**案例3：Regular Join的输出结果突然变多（翻倍）**
- 根因：上下游有数据回撤（Retraction）。新数据到达时修正了历史结果
- 解方：理解Retraction机制，在Sink端处理-U/+U消息

### 优点 & 缺点

| | Flink双流Join（Window/Interval/Regular） | 批处理数据库Join（两表全量关联） |
|------|-----------|-----------|
| **优点1** | 实时增量Join，数据即到即关联，毫秒级延迟 | 全量扫描后才出结果，分钟级延迟 |
| **优点2** | Interval Join仅缓存时间范围内数据，状态可控 | 需要全量数据参与Join |
| **优点3** | Regular Join可关联任意时间到达的两侧数据 | 数据必须在同一批快照中 |
| **缺点1** | Window Join要求两侧严格同窗口，超出即失配 | SQL WHERE条件灵活，任意时间差关联 |
| **缺点2** | Regular Join状态无限增长，必须设TTL否则OOM | 批处理自然有边界 |
| **缺点3** | 三种Join各有约束，选错模型导致关联不上 | 一个JOIN语句覆盖所有场景 |

### 适用场景

**典型场景**：
1. 订单支付实时匹配——Interval Join按"下单后1小时内支付"关联
2. 广告曝光点击归因——Window Join按"同一分钟内曝光和点击"关联
3. 用户注册后行为分析——Regular Join关联注册事件和后续所有行为
4. 实时宽表拼接——多流Join构建完整的交易宽表

**不适用场景**：
1. 关联时间差超过TTL限制的历史数据——需离线批处理完成
2. 无EventTime的数据流——Interval Join和Window Join均需EventTime

### 思考题

1. Interval Join中，`between(Time.seconds(0), Time.hours(1))`——如果支付迟到了整整1小时（刚好等于上界），会匹配上吗？`upperBoundExclusive()`的作用是什么？

2. 假设订单流和支付流关联时，一个订单可能有多笔支付（例如部分退款、多次付款）。Window Join和Interval Join各自如何处理一对多的情况？哪个能正确处理这种场景？

---

> **完整代码**：本章完整代码请参考附录或访问 https://github.com/flink-column/flink-practitioner  
> **思考题答案**：见附录文件 `appendix-answers.md`
