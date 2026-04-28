# 第26章：Flink SQL进阶——窗口TopN/去重/多流Join

---

## 1. 项目背景

第13章我们学习了Flink SQL的基础：创建表、SELECT查询、窗口聚合。但在生产环境中，Flink SQL需要处理更复杂的场景——这些场景在DataStream API中需要大量样板代码，但在SQL中往往一行就能解决。

典型的进阶SQL需求：

1. **窗口TopN**："每5分钟统计过去1小时内成交额最高的10个商品"
2. **多维度去重统计**："统计每个频道的独立访客数（UV），精确去重和近似去重都要"
3. **多流Join**："订单流+支付流+物流流三流关联"
4. **维表Join**："在实时流中关联MySQL中的商品维表信息"

Flink SQL的优化器（基于Apache Calcite）对于上述场景有成熟的优化策略——自动进行谓词下推、投影消除、TopN优化（使用RocksDB的排序能力）。很多时候，手写DataStream API的性能不及SQL优化器生成的执行计划。

---

## 2. 项目设计

> 场景：BI分析师小美需要"每分钟更新过去1小时的热门商品Top10"，她用SQL写了一个窗口子查询。

**小美**：我写了个SQL——按1小时窗口算每个商品的成交额，然后用ORDER BY LIMIT 10取Top10。但运行起来报错说"Rank over unbounded table not supported"。

**大师**：流上的无限排序（ORDER BY + LIMIT）不支持——因为数据源源不断，排序结果永远在变。但窗口内的排序是支持的——因为窗口的结束时间决定了排序的边界。

**技术映射：Flink SQL中的TopN = 带窗口的ROW_NUMBER() OVER(PARTITION BY window_end ORDER BY amount DESC) WHERE rownum <= N。窗口提供了"有限边界"，排序才有意义。**

**小白**：那去重呢？count(DISTINCT userId)在流上怎么实现的？这个状态会不会无限增长？

**大师**：Flink SQL去重有两种方式：

- **精确去重**（`COUNT(DISTINCT userId)`）：内部维护一个Set。状态大小 = 用户基数。如果用户基数大（千万级），改用近似去重。
- **近似去重**：使用HyperLogLog算法——固定内存（约12KB），误差约2%。SQL中需自定义UDAF。

**技术映射：精确去重 = 全量Set，精确但费内存；近似去重 = HLL/Count-Min Sketch，省内存但有一定误差。**

**小胖**：那维表Join呢？比如实时流关联MySQL的商品名称——这个在Flink里怎么做？

**大师**：Flink SQL的`LOOKUP JOIN`——流中的数据过来时，实时查询外部系统（Redis/MySQL/HBase）获取维表数据。Flink会对维表做缓存（默认10秒），减少外部查询次数。

**技术映射：LOOKUP JOIN = 流表 + 维表的"点查"关联。Flink在每个TM中缓存维表数据，按TTL刷新。本质上是AsyncIO的SQL化实现。**

---

## 3. 项目实战

### 分步实现

#### 步骤1：窗口TopN——过去1小时热门商品Top10

**目标**：使用SQL的ROW_NUMBER + 窗口实现TopN。

```sql
-- 1. 内层窗口：统计每个商品在1小时滑窗内成交额
-- 2. 外层：取每个窗口的Top 10

-- DDL：订单表（Kafka）
CREATE TABLE orders (
    productId STRING,
    category STRING,
    amount DOUBLE,
    eventTime BIGINT,
    ts AS TO_TIMESTAMP_LTZ(eventTime, 3),
    WATERMARK FOR ts AS ts - INTERVAL '10' SECOND
) WITH (
    'connector' = 'kafka',
    'topic' = 'order-topic',
    'properties.bootstrap.servers' = 'kafka:9092',
    'format' = 'json'
);

-- TopN查询
SELECT productId, amount, window_end, rownum
FROM (
    SELECT
        productId,
        SUM(amount) AS amount,
        HOP_END(ts, INTERVAL '1' MINUTE, INTERVAL '1' HOUR) AS window_end,
        ROW_NUMBER() OVER (
            PARTITION BY HOP_END(ts, INTERVAL '1' MINUTE, INTERVAL '1' HOUR)
            ORDER BY SUM(amount) DESC
        ) AS rownum
    FROM orders
    GROUP BY
        productId,
        HOP(ts, INTERVAL '1' MINUTE, INTERVAL '1' HOUR)
)
WHERE rownum <= 10;
```

**性能说明**：
- 滑动窗口 1hour/1min 有60个重叠窗口。加上TopN（每个窗口保留10条），状态大小 ≈ 60 × 商品数 × 10
- 如果商品基数很大（>10万），可以使用`MiniBatch`聚合优化

```bash
# 开启MiniBatch优化（减少状态访问次数）
SET table.exec.mini-batch.enabled = true;
SET table.exec.mini-batch.allow-latency = 3s;
SET table.exec.mini-batch.size = 5000;
```

#### 步骤2：精确去重 vs 近似去重

**目标**：实现UV统计的两种方式并对比。

```sql
-- ========== 精确去重 ==========
SELECT
    channel,
    COUNT(DISTINCT userId) AS exact_uv,
    TUMBLE_END(ts, INTERVAL '1' HOUR) AS window_end
FROM page_views
GROUP BY
    channel,
    TUMBLE(ts, INTERVAL '1' HOUR);

-- ========== 近似去重（HyperLogLog UDAF） ==========
-- 需要注册一个HLL UDAF
CREATE TEMPORARY SYSTEM FUNCTION hll_count AS 'com.flink.column.chapter26.HllCountFunction';

SELECT
    channel,
    hll_count(userId) AS approx_uv,
    TUMBLE_END(ts, INTERVAL '1' HOUR) AS window_end
FROM page_views
GROUP BY
    channel,
    TUMBLE(ts, INTERVAL '1' HOUR);
```

**HLL UDAF实现**（使用Apache DataSketches库）：

```java
package com.flink.column.chapter26;

import org.apache.flink.table.functions.AggregateFunction;
import org.apache.datasketches.hll.HllSketch;

public class HllCountFunction extends AggregateFunction<Long, HllCountFunction.HllAccum> {

    public static class HllAccum {
        public HllSketch sketch = new HllSketch(12); // 12 = 4096字节，误差~2%
    }

    @Override
    public HllAccum createAccumulator() {
        return new HllAccum();
    }

    public void accumulate(HllAccum acc, String value) {
        if (value != null) acc.sketch.update(value);
    }

    public void merge(HllAccum acc, Iterable<HllAccum> it) {
        for (HllAccum other : it) {
            acc.sketch.merge(other.sketch);
        }
    }

    @Override
    public Long getValue(HllAccum acc) {
        return acc.sketch.getEstimate();
    }
}
```

**精确vs近似对比**：

| 维度 | 精确COUNT DISTINCT | HyperLogLog |
|------|-------------------|-------------|
| 内存/状态（百万UV） | ~100MB | ~12KB |
| 误差率 | 0% | ~2% |
| 适用场景 | UV < 100万 | UV > 100万 |
| 支持合并 | 是（正常Retraction） | 是（HLL merge） |

#### 步骤3：多流Join——三流关联

**目标**：订单流 + 支付流 + 物流流，三流关联输出完整订单轨迹。

```sql
-- 三流Interval Join
SELECT
    o.orderId,
    o.amount,
    p.payAmount,
    l.logisticsStatus,
    o.ts AS order_time,
    p.ts AS pay_time,
    l.ts AS logistics_time
FROM orders o
JOIN payments p
    ON o.orderId = p.orderId
    AND p.ts BETWEEN o.ts AND o.ts + INTERVAL '1' HOUR
JOIN logistics l
    ON o.orderId = l.orderId
    AND l.ts BETWEEN o.ts AND o.ts + INTERVAL '24' HOUR;
```

#### 步骤4：维表Join（LOOKUP JOIN）——关联MySQL商品维表

**目标**：在实时流中查找MySQL商品表获取商品名称。

```sql
-- 1. 创建Kafka实时流表
CREATE TABLE orders (
    productId STRING,
    amount DOUBLE,
    ts TIMESTAMP(3) METADATA FROM 'timestamp',
    WATERMARK FOR ts AS ts - INTERVAL '5' SECOND
) WITH (
    'connector' = 'kafka',
    'topic' = 'order-topic',
    'properties.bootstrap.servers' = 'kafka:9092',
    'format' = 'json'
);

-- 2. 创建MySQL维表
CREATE TABLE products (
    productId STRING,
    productName STRING,
    category STRING,
    price DOUBLE,
    PRIMARY KEY (productId) NOT ENFORCED
) WITH (
    'connector' = 'jdbc',
    'url' = 'jdbc:mysql://mysql:3306/flink_demo',
    'table-name' = 'products',
    'driver' = 'com.mysql.cj.jdbc.Driver',
    'username' = 'root',
    'password' = 'flink123',
    'lookup.cache.max-rows' = 10000,   -- 缓存最多1万条
    'lookup.cache.ttl' = 60             -- 缓存60秒
);

-- 3. LOOKUP JOIN
SELECT
    o.productId,
    p.productName,
    p.category,
    o.amount,
    o.ts
FROM orders AS o
JOIN products FOR SYSTEM_TIME AS OF o.proctime AS p
    ON o.productId = p.productId;
```

> **注意**：维表Join使用`FOR SYSTEM_TIME AS OF o.proctime`语法——表示在流数据到达时（processing time）去查找维表的最新版本。

### 可能遇到的坑

1. **窗口TopN结果为空——但数据确实有**
   - 根因：ROW_NUMBER的PARTITION BY字段与窗口函数不匹配
   - 解决：确保PARTITION BY的字段覆盖了HOP_END的表达式（别名引用）

2. **COUNT(DISTINCT)状态无限增长导致OOM**
   - 根因：高基数去重（如数千万UV）的全量Set撑爆内存
   - 解方：使用HyperLogLog近似去重；或缩短窗口大小减少基数

3. **LOOKUP JOIN在MySQL维表数据变更后不更新**
   - 根因：维表缓存（lookup.cache.ttl）未过期，Flink一直返回旧数据
   - 解方：调小ttl（如10秒）；或关闭缓存（`lookup.cache.max-rows=0`），但会显著增加MySQL查询压力

---

## 4. 项目总结

### SQL进阶查询速查

| 场景 | 核心语法 | 关键点 |
|------|---------|-------|
| 窗口TopN | `ROW_NUMBER() OVER(PARTITION BY window_end ORDER BY agg DESC) WHERE rn ≤ N` | 窗口给了排序边界 |
| 精确去重 | `COUNT(DISTINCT col)` | 适合小基数 |
| 近似去重 | HLL UDAF | 适合大基数 |
| 三流Join | 多个JOIN + Interval条件 | EventTime + 时间范围 |
| 维表Join | `FOR SYSTEM_TIME AS OF` + JDBC Connector | 缓存策略 |

### 性能优化建议

- **MiniBatch**：减少状态访问次数，适用于聚合查询
- **局部聚合**：开启`table.optimizer.agg-phase-strategy=TWO_PHASE`
- **Shuffle优化**：`table.optimizer.distinct-agg.split.enabled=true`（将COUNT DISTINCT拆分为两阶段聚合）
- **并行度**：`SET 'parallelism.default' = '8'`

### 注意事项
- SQL中的窗口TopN性能劣于DataStream API的TopN——但胜在开发效率和可维护性
- LOOKUP JOIN的缓存策略要根据维表变更频率调整——频繁变更用短TTL，不常变更用长TTL
- 多流Join时，尽量用Interval Join（有时间范围约束），避免Regular Join（无限状态）

### 常见踩坑经验

**案例1：LOOKUP Join MySQL超时，作业短暂反压后恢复**
- 根因：MySQL维表突然有慢查询（全表扫描），导致Flink的JDBC连接超时
- 解方：在MySQL侧为productId创建索引；减小lookup.cache.max-rows降低查询频率

**案例2：COUNT DISTINCT的结果与DataStream API不一致**
- 根因：Flink SQL的COUNT DISTINCT在Retraction时可能产生中间结果
- 解方：使用`COUNT(DISTINCT userId)`替代精确值；或窗口内一次性输出最终结果

**案例3：TopN查询在数据量激增时输出延迟**
- 根因：TopN需要在窗口结束时对所有商品排序，如果商品基数大（10万+），排序是CPU密集操作
- 解方：使用Nested TopN（先将每个商品的聚合值计算出来，再统一排序）；或减少窗口大小

### 优点 & 缺点

| | Flink SQL进阶查询（TopN/去重/多流Join） | DataStream API手写实现 |
|------|-----------|-----------|
| **优点1** | 开发效率极高——窗口TopN+去重+Join一行SQL | 每类需求需编写大量Java代码 |
| **优点2** | LOOKUP Join维表自动缓存，无需手写AsyncIO | 需自行实现异步维表关联 |
| **优点3** | Calcite优化器自动做谓词下推、投影消除 | 手动优化，依赖开发者经验 |
| **缺点1** | 复杂状态逻辑受限——COUNT DISTINCT状态膨胀不可控 | 可精细控制状态大小和TTL |
| **缺点2** | TopN排序是CPU密集，窗口内全量排序开销大 | DataStream手动TopN可做增量维护 |

### 适用场景

**典型场景**：
1. 实时排行榜——窗口TopN统计每5分钟热门商品Top10
2. UV精确/近似统计——COUNT DISTINCT与HLL UDAF配合使用
3. 多流关联宽表——订单+支付+物流三流Interval Join
4. 维表实时补全——LOOKUP Join关联MySQL商品名称/分类

**不适用场景**：
1. 状态基数极大（>1亿去重）——COUNT DISTINCT的Set撑爆内存，需DataStream API+外部存储
2. 自定义窗口Trigger逻辑——SQL窗口触发时间固定，无法实现动态触发

### 思考题

1. 维表Join与AsyncIO都用于"关联外部数据"。在SQL场景下，如果维表Redis的QPS上限是5万/秒，而Flink作业的吞吐是10万条/秒，你会怎么做？（提示：增大lookup.cache.max-rows；或对维表做分片）

2. 窗口TopN中使用了`ROW_NUMBER() OVER(PARTITION BY window_end ORDER BY amount DESC)`。如果改成`ORDER BY amount ASC`（取末尾），在性能和逻辑上有什么不同？

---

> **完整代码**：本章完整代码请参考附录或访问 https://github.com/flink-column/flink-practitioner  
> **思考题答案**：见附录文件 `appendix-answers.md`
