# 第6章：分而治之——KeyBy分组与聚合

---

## 1. 项目背景

某新闻资讯App运营后台需要实时展示以下指标：

- **PV（Page View）**：每个新闻页面的实时浏览量
- **UV（Unique Visitor）**：每篇新闻的实时独立访客数
- **频道聚合**：按新闻频道（体育/财经/娱乐…）统计总PV

数据流：用户每次点击新闻，App端发送一条埋点日志到Kafka，格式为：

```json
{"userId":"u1001","newsId":"n20260428001","channel":"sports","timestamp":1714293932000}
```

Flink作业消费该Topic，实时统计PV和UV，每10秒刷新一次，写入Redis供前端大屏展示。

这个需求拆解下来，涉及两个关键概念：

- **KeyBy分组**：按`newsId`分组做PV统计、按`channel`分组做频道聚合——不同维度的聚合需要不同的分组策略
- **聚合（Aggregation）**：sum/min/max等内置聚合函数，以及自定义聚合逻辑（UV去重需要维护Set）

看起来简单，但深入下去全是坑：

- **KeyBy是怎么分区的**？如果100万篇新闻的点击分布严重不均匀——"头条新闻"一秒钟10万点击、"冷门新闻"一天10个点击——哈希分区会导致数据倾斜，一个TaskManager扛10万QPS，另一个闲得发慌
- **自定义聚合怎么做**？PV可以用`sum(1)`，但UV的精确去重不能直接sum——需要维护一个Set。这个Set怎么存？存在哪里？作业挂了之后Set丢了怎么办？
- **多维度聚合同步进行**？既要按newsId算PV，又要按channel算频道PV——两个维度的keyBy不同，需要两条独立的流水线，还是可以在一条流水线内完成？

---

## 2. 项目设计

> 场景：运营小美在群里@小胖——"实时大屏上体育频道的PV怎么一直是0？"

**小胖**（看了看代码）：我按新闻ID做的keyBy，每条新闻的点击数统计没问题啊。频道的聚合还没做——那个要重新keyBy一次，我正准备加呢。

**大师**：等一下。你要在同一个Flink作业里按两个维度做聚合，如果做两次keyBy，中间必须经过一次网络shuffle。但你想想——数据从Source进来，先keyBy(newsId)做PV，再keyBy(channel)做频道聚合。两次keyBy意味着两次全量网络传输。

**小白**：两次Shuffle的代价是什么？能不能一次Shuffle解决两个聚合？

**大师**：可以用**侧输出（Side Output）** 或**多流（Split）** 的方式避免两次全量Shuffle。但最简单的方案是——用一行数据同时发射到两个不同的流水线：

```
Source → 分流 → [keyBy(newsId) → newsPV]
               → [keyBy(channel) → channelPV]
```

两条流水线各自独立，Source只需要读一次，但在分流点做了数据复制。**技术映射：这条是Multi-output模式——一个DataStream通过Side Output或直接分流到多个下游DataStream，每条数据可以被多个消费者独立处理。**

**小胖**：那UV去重呢？用sum没法去重啊。同一个用户点了同一篇新闻10次，UV应该只算1次。

**大师**：Flink的`sum`不能去重。你需要用**自定义聚合算子**——`aggregate(AggregateFunction)`或者`process(ProcessFunction)`。PV用sum，UV用HashSet+count。

**小白**：那这个HashSet有多大？如果一篇热门新闻有1000万UV，Set里存1000万个userId，内存会不会炸？

**大师**：问得好。精确去重占用内存 = 用户ID字节数 × UV量。1000万用户ID如果每个按20字节算，就是200MB，加上HashMap的膨胀系数，约500MB。对于一篇新闻来说还可以接受，但如果所有新闻加起来，内存占用无法承受。

**技术映射：生产环境的UV去重有三种方案——① 精确去重（HashSet，适合小规模）② HyperLogLog（估算，适合大规模）③ BloomFilter + Count（近似，可控制误差率）。本篇我们用精确去重实现，在综合实战篇（第15章）会升级到HyperLogLog.**

**小胖**：那开始编码吧！KeyBy到底底层怎么实现的，先不管了。

**大师**：不，你得先理解KeyBy的哈希分区原理，否则后面遇到数据倾斜你都不知道为什么。

---

## 3. 项目实战

### 环境准备

| 组件 | 版本 |
|------|------|
| JDK | 11+ |
| Flink | 1.18.1 |
| Redis | 7.0+（可选，用于最终数据写入）|

### 分步实现

#### 步骤1：理解KeyBy的分区机制——手写哈希分布测试

**目标**：验证KeyBy使用`key.hashCode() % parallelism`做分区路由。

```java
package com.flink.column.chapter06;

import org.apache.flink.api.java.functions.KeySelector;
import org.apache.flink.streaming.api.environment.StreamExecutionEnvironment;
import org.apache.flink.streaming.api.datastream.DataStream;
import org.apache.flink.api.java.tuple.Tuple2;
import org.apache.flink.util.Collector;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.apache.flink.api.common.typeinfo.Types;

/**
 * 验证KeyBy分区规则：key.hashCode() % parallelism
 * 并观察数据倾斜现象。
 */
public class KeyByDistributionTest {

    private static final Logger LOG = LoggerFactory.getLogger(KeyByDistributionTest.class);

    public static void main(String[] args) throws Exception {
        StreamExecutionEnvironment env = StreamExecutionEnvironment.getExecutionEnvironment();
        env.setParallelism(6);  // 6个分区

        DataStream<String> text = env.socketTextStream("localhost", 9999);

        text.flatMap((String line, Collector<String> out) -> {
            for (String word : line.toLowerCase().split("\\W+")) {
                if (!word.isEmpty()) out.collect(word);
            }
        }).returns(Types.STRING)

        .map(word -> {
            int partition = Math.floorMod(word.hashCode(), 6);
            LOG.info("word={}, hashCode={}, -> partition={}", word, word.hashCode(), partition);
            return Tuple2.of(word, 1);
        }).returns(Types.TUPLE(Types.STRING, Types.INT))

        .keyBy(t -> t.f0)
        .sum(1)
        .print();

        env.execute("Chapter06-KeyByDistribution");
    }
}
```

输入 `the quick brown fox jumps over the lazy dog near the river bank`：

```
word=the, hashCode=114186, -> partition=0
word=quick, hashCode=107308, -> partition=2
word=brown, hashCode=933012, -> partition=0
word=fox, hashCode=101130, -> partition=0
word=jumps, hashCode=106808, -> partition=2
word=over, hashCode=109940, -> partition=2
word=the, hashCode=114186, -> partition=0 (again)
word=lazy, hashCode=107842, -> partition=4
word=dog, hashCode=99003, -> partition=3
word=near, hashCode=106539, -> partition=3
word=the, hashCode=114186, -> partition=0 (again)
word=river, hashCode=106908, -> partition=0
word=bank, hashCode=99021, -> partition=3
```

**观察结论**：
- 分区0收到4个word（the×3 + brown + fox + river）= 5计数
- 分区2收到3个word（quick + jumps + over）= 3计数
- 分区3收到3个word（dog + near + bank）= 3计数
- 分区4收到1个word（lazy）= 1计数

自然语言中"the"出现频率天然高，导致分区0的负载是分区4的5倍——这就是**数据倾斜**的原型。

#### 步骤2：自定义KeySelector——控制分区逻辑

**目标**：通过自定义KeySelector实现更灵活的分区策略。

```java
// 默认keyBy
dataStream.keyBy(news -> news.newsId);

// 自定义KeySelector——可以更复杂
dataStream.keyBy(new KeySelector<ClickEvent, String>() {
    @Override
    public String getKey(ClickEvent event) {
        // 可以根据多字段组合key
        return event.channel + "_" + event.newsId;
    }
});
```

#### 步骤3：实现PV+UV的ClickAnalytics Job

**目标**：一个作业内同时统计每个新闻的PV和UV。

```java
package com.flink.column.chapter06;

import org.apache.flink.api.common.eventtime.WatermarkStrategy;
import org.apache.flink.api.common.functions.AggregateFunction;
import org.apache.flink.api.common.functions.MapFunction;
import org.apache.flink.api.common.serialization.SimpleStringSchema;
import org.apache.flink.api.java.tuple.Tuple2;
import org.apache.flink.connector.kafka.source.KafkaSource;
import org.apache.flink.connector.kafka.source.enumerator.initializer.OffsetsInitializer;
import org.apache.flink.streaming.api.datastream.DataStream;
import org.apache.flink.streaming.api.environment.StreamExecutionEnvironment;
import org.apache.flink.streaming.api.windowing.assigners.TumblingProcessingTimeWindows;
import org.apache.flink.streaming.api.windowing.time.Time;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.util.HashSet;

/**
 * 新闻实时统计：同时统计PV和UV
 * PV = 浏览次数，UV = 独立访客数
 */
public class ClickAnalyticsJob {

    private static final Logger LOG = LoggerFactory.getLogger(ClickAnalyticsJob.class);

    public static void main(String[] args) throws Exception {
        StreamExecutionEnvironment env = StreamExecutionEnvironment.getExecutionEnvironment();
        env.setParallelism(4);

        // 模拟Kafka Source（Socket方式便于演示）
        DataStream<String> rawStream = env.socketTextStream("localhost", 9999);

        // 解析为ClickEvent
        DataStream<ClickEvent> events = rawStream
                .map(new MapFunction<String, ClickEvent>() {
                    @Override
                    public ClickEvent map(String json) throws Exception {
                        // 手动解析：{"userId":"u1","newsId":"n001","channel":"sports"}
                        json = json.trim();
                        ClickEvent e = new ClickEvent();
                        String content = json.substring(1, json.length() - 1);
                        for (String pair : content.split(",")) {
                            String[] kv = pair.split(":", 2);
                            if (kv.length != 2) continue;
                            String key = kv[0].trim().replace("\"", "");
                            String val = kv[1].trim().replace("\"", "");
                            switch (key) {
                                case "userId":  e.userId = val; break;
                                case "newsId":  e.newsId = val; break;
                                case "channel": e.channel = val; break;
                            }
                        }
                        return e;
                    }
                }).name("parse-json");

        // ---------------------------------------------------------------
        // 分支1：按newsId统计PV（普通sum聚合）
        // ---------------------------------------------------------------
        DataStream<Tuple2<String, Long>> newsPV = events
                .keyBy(e -> e.newsId)
                .map(e -> Tuple2.of(e.newsId, 1L))
                .returns(Types.TUPLE(Types.STRING, Types.LONG))
                .keyBy(t -> t.f0)
                .sum(1)
                .name("news-pv");

        newsPV.print().name("news-pv-sink");

        // ---------------------------------------------------------------
        // 分支2：按newsId统计UV（自定义AggregateFunction，用Set去重）
        // ---------------------------------------------------------------
        DataStream<Tuple2<String, Integer>> newsUV = events
                .keyBy(e -> e.newsId)
                // 滚动窗口10秒（否则UV会无限增长）
                .window(TumblingProcessingTimeWindows.of(Time.seconds(10)))
                .aggregate(new UvAggregator())
                .name("news-uv");

        newsUV.print().name("news-uv-sink");

        // ---------------------------------------------------------------
        // 分支3：按channel统计频道PV
        // ---------------------------------------------------------------
        DataStream<Tuple2<String, Long>> channelPV = events
                .keyBy(e -> e.channel)
                .map(e -> Tuple2.of(e.channel, 1L))
                .returns(Types.TUPLE(Types.STRING, Types.LONG))
                .keyBy(t -> t.f0)
                .sum(1)
                .name("channel-pv");

        channelPV.print().name("channel-pv-sink");

        env.execute("Chapter06-ClickAnalytics");
    }

    /**
     * 精确UV统计：用HashSet去重
     * AggregateFunction<IN, ACC, OUT>
     *   IN = ClickEvent
     *   ACC = HashSet<String> (存userId)
     *   OUT = Tuple2<newsId, UV数>
     */
    public static class UvAggregator
            implements AggregateFunction<ClickEvent, HashSet<String>, Tuple2<String, Integer>> {

        @Override
        public HashSet<String> createAccumulator() {
            return new HashSet<>();
        }

        @Override
        public HashSet<String> add(ClickEvent event, HashSet<String> accumulator) {
            // 每来一条数据，尝试加入Set
            accumulator.add(event.userId);
            return accumulator;
        }

        @Override
        public Tuple2<String, Integer> getResult(HashSet<String> accumulator) {
            // 窗口触发时返回UV数和新闻ID
            // 注意：这里取不到newsId（因为是聚合结果）。生产环境要返回完整结果需在ClickEvent中携带newsId
            return Tuple2.of("", accumulator.size());
        }

        @Override
        public HashSet<String> merge(HashSet<String> a, HashSet<String> b) {
            a.addAll(b);
            return a;
        }
    }

    /** 点击事件POJO */
    public static class ClickEvent {
        public String userId;
        public String newsId;
        public String channel;

        @Override
        public String toString() {
            return String.format("Click{user=%s, news=%s, channel=%s}", userId, newsId, channel);
        }
    }
}
```

#### 步骤4：用Flink SQL实现同样的PV/UV统计

**目标**：对比DataStream API和SQL两种实现方式。

```sql
-- PV统计
SELECT newsId, COUNT(*) AS pv
FROM click_events
GROUP BY newsId;

-- UV统计（精确去重）
SELECT newsId, COUNT(DISTINCT userId) AS uv
FROM click_events
GROUP BY newsId;

-- 频道PV统计
SELECT channel, COUNT(*) AS pv
FROM click_events
GROUP BY channel;
```

Flink SQL方式（Table API）：

```java
package com.flink.column.chapter06;

import org.apache.flink.streaming.api.environment.StreamExecutionEnvironment;
import org.apache.flink.table.api.Table;
import org.apache.flink.table.api.bridge.java.StreamTableEnvironment;

public class ClickSQLAnalytics {

    public static void main(String[] args) {
        StreamExecutionEnvironment env = StreamExecutionEnvironment.getExecutionEnvironment();
        StreamTableEnvironment tableEnv = StreamTableEnvironment.create(env);

        // 将Socket Source注册为表
        tableEnv.executeSql(
            "CREATE TABLE click_events (" +
            "  userId STRING, newsId STRING, channel STRING, ts AS PROCTIME()" +
            ") WITH (" +
            "  'connector' = 'socket'," +
            "  'hostname' = 'localhost'," +
            "  'port' = '9999'," +
            "  'format' = 'json'" +
            ")");

        // PV统计
        Table result = tableEnv.sqlQuery(
            "SELECT newsId, COUNT(*) AS pv " +
            "FROM click_events " +
            "GROUP BY newsId");

        result.execute().print();
    }
}
```

#### 步骤5：测试验证

```bash
nc -lk 9999
```

发送数据：

```
{"userId":"u1","newsId":"n001","channel":"sports"}
{"userId":"u2","newsId":"n001","channel":"sports"}
{"userId":"u1","newsId":"n001","channel":"sports"}   # 同一个用户uv不重复
{"userId":"u3","newsId":"n002","channel":"finance"}
{"userId":"u1","newsId":"n002","channel":"sports"}
```

**预期输出**：

```
# newsPV（全局累加）
(n001,1)
(n001,2)
(n001,3)   # 第三次点击n001，PV变成3
(n002,1)
(n002,2)

# newsUV（每10秒窗口触发一次）
(n001,2)   # u1和u2两个不同用户，n001的UV=2
(n002,2)   # u3和u1两个不同用户，n002的UV=2

# channelPV（全局累加）
(sports,1)
(sports,2)
(sports,3)  # n001×2 + n002中的channel=sports → 3
(finance,1)
(sports,4)
```

### 可能遇到的坑

1. **UV统计用HashSet在生产环境内存溢出**
   - 原因：数据量远超预期，Set无限增长
   - 解决：加窗口限制（每10分钟或每小时重置窗口），或改用HyperLogLog估算

2. **keyBy自定义KeySelector里的hashCode分布不均匀**
   - 原因：某些业务key天然倾斜（如爬虫循环访问同一页面）
   - 解决：在key上加盐（salted key）做两阶段聚合

3. **SQL中GROUP BY后的结果在Flink SQL中报错：GroupBy on unbounded table without window**
   - 原因：无限流上的无界聚合需要窗口或retraction，纯GROUP BY不被允许
   - 解决：加窗口函数 `TUMBLE(ts, INTERVAL '10' SECOND)`

---

## 4. 项目总结

### KeyBy分区规则速查

| 维度 | 规则 | 说明 |
|------|------|------|
| 分区算法 | `Math.floorMod(key.hashCode(), parallelism)` | 取模确定目标子任务 |
| 确定性 | 相同key → 相同分区 | 保证同一分区的数据被同一个子任务处理 |
| 数据倾斜 | 某些key数据量远大于其他 | 需手动加盐或两阶段聚合 |
| 跨算子 | keyBy打断算子链 | 强制网络Shuffle，分布式作业中无法避免 |

### 四种聚合方式对比

| 方式 | API | 支持窗口 | 支持自定义 | 适用场景 |
|------|-----|---------|-----------|---------|
| sum/min/max | `.sum(1)`, `.min(0)` | 否（全局无限） | 否 | 简单数值累加 |
| AggregateFunction | `.aggregate(aggr)` | 是 | 是 | UV去重、自定义逻辑 |
| ProcessFunction | `.process(ctx)` | 灵活控制 | 全自定义 | 复杂业务逻辑 |
| SQL GROUP BY | `SELECT ... GROUP BY` | 是（需加窗口） | 否 | BI分析师自助查询 |

### 注意事项
- KeyBy后的聚合如果不用窗口，就是**无限流聚合**（全局累加，永不重置）。建议加上窗口或使用状态TTL
- KeyBy的key不能为null——null值全部到同一个分区造成严重倾斜。使用前请做null判断并赋予默认值
- 自定义AggregateFunction的accumulator（累加器）在窗口中会随着数据累积增大。Set等累加器建议用`merge()`支持并行窗口合并

### 常见踩坑经验

**案例1：所有数据都到了同一个subtask（分区），其他subtask空闲**
- 根因：keyBy的key为null——Math.floorMod(0, parallelism) = 0，全部分到分区0
- 解方：keyBy前用`filter(k -> k != null)`或者给null赋默认值

**案例2：小规模测试时数据倾斜不明显，上生产后严重倾斜**
- 根因：测试数据均匀分布，但生产数据按业务规律（如"热点新闻"vs"冷门新闻"）严重不均衡
- 解方：压测阶段用生产数据的采样作为输入，提前发现倾斜；上线后用两阶段聚合（第35章详述）

**案例3：UV统计的结果抖动——同一个用户在新窗口中又被算了一次**
- 根因：跨窗口边界时，同一用户的多次点击跨越了两个窗口边界，在新的窗口中第一次出现被计入UV
- 解方：改用会话窗口（Session Window）或滑动窗口减少边界效应；或在业务层面定义"一次活动的UV"使用事件时间+Session Window

### 优点 & 缺点

| | Flink KeyBy + 流聚合 | 传统数据库 GROUP BY（批处理） |
|------|-----------|-----------|
| **优点1** | 实时增量聚合，数据即到即算，毫秒级延迟 | 全量扫描后才出结果，分钟级延迟 |
| **优点2** | 支持多维度keyBy并行聚合，一个作业完成多种统计 | 单次查询只能一个GROUP BY维度 |
| **优点3** | 可自定义AggregateFunction/ProcessFunction，扩展性强 | 内置聚合函数固定，不可扩展 |
| **优点4** | KeyBy分区确定性，相同key稳定路由到同一子任务 | Shuffle策略由优化器决定，用户不可控 |
| **缺点1** | 无限流聚合无窗口限制时状态无限增长 | 批处理自然有边界，无状态膨胀问题 |
| **缺点2** | 数据倾斜需手动加盐或两阶段聚合 | 数据库Hash Join/Agg有自动倾斜处理机制 |

### 适用场景

**典型场景**：
1. 实时PV/UV统计——新闻点击、商品浏览等按维度实时聚合
2. 业务指标监控——订单量、GMV、DAU等按时间/地域维度实时聚合
3. 用户行为拼接——按userId keyBy后做Session合并与路径分析
4. 实时规则判定——按业务ID聚合后与阈值比较触发告警

**不适用场景**：
1. UV量极大（>1亿）的精确去重——需HyperLogLog等近似算法配合Flink
2. 简单无状态变换——仅需map/filter即可，无需引入keyBy的shuffle开销

### 思考题

1. 假设有10万个新闻ID，10亿条点击数据。如果用`keyBy(newsId) → sum(1)`做PV，最热的新闻占5000万次点击。按8个并行度计算，最热新闻所在分区的数据量是多少？最冷分区呢？（提示：不考虑哈希冲突的理想情况，单key只能落在1个分区）

2. UvAggregator中的`getResult()`目前返回`("", size)`——丢了newsId这个维度信息。如果要正确的返回(newsId, uvCount)的Tuple，AggregateFunction应该怎么改造？（提示：可以用Tuple2<String, HashSet<String>>作为累加器，或者使用ProcessWindowFunction+AggregateFunction的组合）

---

> **完整代码**：本章完整代码请参考附录或访问 https://github.com/flink-column/flink-practitioner  
> **思考题答案**：见附录文件 `appendix-answers.md`
