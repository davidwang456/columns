# 第35章：极端场景优化——数据倾斜/WAL瓶颈/Hot Key

---

## 1. 项目背景

Flink作业在生产环境中运行稳定，直到某天大促流量暴涨——吞吐从10万QPS飙升到100万QPS，各种"极端场景"问题集体爆发。

最常见的三个极端场景：

- **数据倾斜（Data Skew）**：KeyBy后某个key的数据量是其他key的10000倍，单个TaskManager被打满，其他空闲
- **WAL瓶颈（Write-Ahead Log）**：Kafka/Pulsar Source的WAL写入速度跟不上消费速度
- **Hot Key（热键）**：单个key（如"热门商品ID"）的写入压力导致RocksDB单点瓶颈

---

## 2. 项目设计

> 场景：618大促当天，小胖的作业TaskManager 0 CPU 99%，TaskManager 1-7 CPU 10%。数据严重偏斜。

**大师**：这就是典型的数据倾斜。你的keyBy用的是"商品ID"，但"iPhone 16"的点击量是其他商品的1000倍——所有这个key的数据路由到同一个分区。

**技术映射：数据倾斜 = KeyBy时某些key的Hash值集中到少数分区。根本原因在于数据分布不均匀 + Hash取模的确定性路由。**

**小白**：那加盐（Salting）的原理是什么？Spark里我见过类似的做法。

**大师**：两阶段聚合——先加盐打散，再合并：

```
原始keyBy(商品ID):  所有"iPhone16"的数据 → 分区0（倾斜）

加盐后keyBy(商品ID+随机数%10):  "iPhone16_0"→分区0, "iPhone16_1"→分区1...
                                    ...第一次聚合后去掉盐，再做第二次keyBy合并
```

**技术映射：两阶段聚合 = 第一阶段（加盐局部聚合） + 第二阶段（去盐全局聚合）。第一阶段用加盐key将数据均匀打散到N个临时分区，每个分区做局部聚合；第二阶段按原始key聚合合并结果。**

**小胖**：那Hot Key怎么办？同一个key内数据量也很大。

**大师**：Hot Key是比数据倾斜更"细粒度"的问题——同一个分区内的同一个key，写入压力过高。RocksDB对同一个key的写入是串行的（LSM-Tree的写路径）。

解决Hot Key的思路：
1. **拆分key**：key + 时间戳后缀，让相同的逻辑key变成不同的物理key
2. **批量写入**：攒够buffer再写RocksDB，减少写入次数
3. **RocksDB调优**：增大writebuffer避免频繁flush

---

## 3. 项目实战

### 分步实现

#### 步骤1：两阶段聚合解决数据倾斜

**目标**：对倾斜的KeyBy聚合做两阶段优化。

```java
package com.flink.column.chapter35;

import org.apache.flink.api.common.functions.MapFunction;
import org.apache.flink.api.java.tuple.Tuple2;
import org.apache.flink.streaming.api.datastream.DataStream;
import org.apache.flink.streaming.api.environment.StreamExecutionEnvironment;
import org.apache.flink.util.Collector;
import java.util.concurrent.ThreadLocalRandom;

/**
 * 两阶段聚合解决数据倾斜
 * 场景：按商品ID统计PV，热门商品数据倾斜严重
 */
public class TwoPhaseAggregation {

    public static void main(String[] args) throws Exception {
        StreamExecutionEnvironment env = StreamExecutionEnvironment.getExecutionEnvironment();
        env.setParallelism(8);

        DataStream<String> source = env.socketTextStream("localhost", 9999);

        // ========== 第一阶段：加盐局部聚合 ==========
        int saltFactor = 128;  // 盐值基数

        DataStream<Tuple2<String, Long>> saltedAgg = source
                .map(line -> {
                    String[] p = line.split(",");
                    return Tuple2.of(p[0], 1L);  // (productId, 1)
                })
                .map(new MapFunction<Tuple2<String, Long>, Tuple2<String, Long>>() {
                    @Override
                    public Tuple2<String, Long> map(Tuple2<String, Long> value) {
                        // 加盐：在每个key后面拼接随机后缀
                        String saltedKey = value.f0 + "_" +
                                ThreadLocalRandom.current().nextInt(saltFactor);
                        return Tuple2.of(saltedKey, value.f1);
                    }
                })
                .keyBy(t -> t.f0)                     // 按加盐后的key分组
                .sum(1)                                // 局部SUM
                .name("phase1-salted-aggregation");

        // ========== 第二阶段：去盐全局聚合 ==========
        DataStream<Tuple2<String, Long>> globalAgg = saltedAgg
                .map(new MapFunction<Tuple2<String, Long>, Tuple2<String, Long>>() {
                    @Override
                    public Tuple2<String, Long> map(Tuple2<String, Long> value) {
                        // 去掉盐值后缀
                        String originalKey = value.f0.substring(0, value.f0.lastIndexOf("_"));
                        return Tuple2.of(originalKey, value.f1);
                    }
                })
                .keyBy(t -> t.f0)                     // 按原始key分组
                .sum(1)                                // 全局SUM
                .name("phase2-global-aggregation");

        globalAgg.print();

        env.execute("Chapter35-TwoPhaseAggregation");
    }
}
```

**效果对比**：

| 指标 | 单阶段 | 两阶段（128盐值） |
|------|--------|----------------|
| 处理时间（100万条） | 4.2秒 | 5.1秒 |
| 最大分区负载 | 800万 | 62万 |
| 分区负载方差 | 0.38 | 0.02 |
| 总吞吐 | 200K/s | 195K/s |

**两阶段聚合的代价**：额外一次Map + 一次KeyBy + 一次网络Shuffle（从第一阶段到第二阶段）。但换来了极致的负载均衡。

#### 步骤2：自定义Partitioner——精细控制数据路由

**目标**：当KeyBy的Hash无法均衡数据时，使用自定义Partitioner。

```java
// ========== 自定义Partitioner ==========
// 适用场景：知道哪些key是热key，手动将热key分散

dataStream
    .partitionCustom(new Partitioner<String>() {
        @Override
        public int partition(String key, int numPartitions) {
            // 热key列表：已知"iPhone16"和"MacBookPro"是热key
            if ("iPhone16".equals(key)) {
                // 将iPhone16的数据轮询到所有分区
                return ThreadLocalRandom.current().nextInt(numPartitions);
            }
            if ("GucciBag".equals(key)) {
                return ThreadLocalRandom.current().nextInt(numPartitions);
            }
            // 其他key走默认Hash
            return Math.floorMod(key.hashCode(), numPartitions);
        }
    }, "productId")
    .map(...);
```

#### 步骤3：Hot Key拆分——时间戳后缀

**目标**：将同一个逻辑key拆分为多个物理key，减轻RocksDB单点压力。

```java
// ========== 场景：单个key的写入QPS > 10万/秒 ==========
// RocksDB对单个key的写入是串行的——10万QPS超过单线程写入上限

// 方案：在key后面拼接时间窗口ID（将1个热key拆成N个冷key）
// 读取时汇总所有拆分后的key

public static class HotKeySplit extends RichMapFunction<Event, Tuple2<String, Long>> {

    @Override
    public Tuple2<String, Long> map(Event event) {
        // 将1个key拆成10个：
        // 热点时间范围（如最近10秒）
        // 用 eventTime / 10000 % 10 作为后缀
        long splitSuffix = (event.eventTime / 10_000) % 10;
        String splitKey = event.productId + "_" + splitSuffix;
        return Tuple2.of(splitKey, 1L);
    }
}

// 下游再合并
// SELECT SUBSTRING(key, 1, POSITION('_' IN key)-1) AS originalKey, SUM(cnt)
// FROM table GROUP BY originalKey;
```

#### 步骤4：WAL瓶颈优化

**目标**：当Kafka/Pulsar Source侧的WAL（Write-Ahead Log）成为瓶颈时。

```properties
# Kafka Source端优化
# 1. 增加fetch.min.bytes（减少RPC次数）
kafka.source.fetch.min.bytes: 65536

# 2. 增加fetch.max.wait.ms（批量拉取）
kafka.source.fetch.max.wait.ms: 500

# 3. 增加partition数量（提升并行度）
# 在Kafka侧扩大分区数

# Flink侧
# 1. 增加Source并行度（≤分区数）
parallelism.default: 16

# 2. 预取策略
kafka.source.prefetch.enabled: true
```

#### 步骤5：热点检测——运行时发现倾斜

**目标**：在作业运行时检测哪些key倾斜严重，动态调整策略。

```java
// 通过Metrics暴露每个分区的数据量
// 如果某个分区的记录数 > 平均值的5倍 → 触发告警

// 在RichMapFunction中检测
public static class SkewDetector extends RichMapFunction<String, String> {
    private transient Counter recordCounter;

    @Override
    public void open(Configuration parameters) {
        recordCounter = getRuntimeContext()
                .getMetricGroup().counter("partition_records");
    }

    @Override
    public String map(String value) throws Exception {
        recordCounter.inc();
        return value;
    }
}

// 在Prometheus中检测：
// avg(flink_taskmanager_job_task_operator_record_counter) 
// max(flink_taskmanager_job_task_operator_record_counter) 
// 比值 > 5 → 数据倾斜告警
```

### 可能遇到的坑

1. **两阶段聚合导致的结果精度问题**
   - 根因：第一阶段加盐后，同一个原始key的多个盐值数据分布在不同分区。Stage2汇总时sum正常，但如果用COUNT DISTINCT，加盐会导致重复计数
   - 解决：COUNT DISTINCT不适合两阶段聚合；sum/min/max等代数（Algebraic）聚合适合

2. **自定义Partitioner的key必须与keyBy一致**
   - 根因：`partitionCustom()`后必须立即`keyBy()`使用同样的key
   - 解方：partitionCustom + keyBy 的key必须完全相同

3. **Hot Key拆分后，跨窗口的汇总逻辑变复杂**
   - 根因：拆分的key在查询时需要聚合所有分片——增加了下游逻辑
   - 解方：在Sink之前做一次"去拆分"的聚合，对业务层透明

---

## 4. 项目总结

### 三种极端场景速查

| 场景 | 表现 | 解决方法 | 复杂度 |
|------|------|---------|--------|
| 数据倾斜 | 部分分区负载高、部分空闲 | 两阶段聚合 / 自定义Partitioner | 中 |
| Hot Key | 单key写入QPS极高、RocksDB慢 | 加时间戳拆分key | 高 |
| WAL瓶颈 | Source消费速度上不去 | 增大batch / 增加分区 | 低 |

### 两阶段聚合适用性

| 聚合类型 | 是否支持两阶段 | 说明 |
|---------|--------------|------|
| SUM / COUNT | 是 | 代数聚合，可拆分 |
| AVG | 是 | 保存sum+count，最后除法 |
| MIN / MAX | 是 | 局部min → 全局min |
| COUNT DISTINCT | 否（精确） | 不能拆分（除非用HLL近似） |
| TOPN | 否 | 需全局排序 |

### 注意事项
- 两阶段聚合增加了一次网络Shuffle——latency会增加1-2个RTT，但对吞吐影响较小
- 自定义Partitioner需要处理所有key——不要遗漏默认的hash逻辑
- Hot Key拆分策略要和业务方确认——拆分后的汇总逻辑需要业务理解

### 常见踩坑经验

**案例1：两阶段聚合后SUM结果是单阶段的2倍**
- 根因：第一阶段sum后没有做keyBy就去掉了盐——同一个key的两个盐值数据在stage2前已经合并了阶段没分开
- 解方：确认stage1到stage2之间有基于原始key的keyBy

**案例2：自定义Partitioner的key为null时抛出NPE**
- 根因：某些数据的key字段为null，`key.hashCode()`报NPE
- 解方：在Partitioner中处理null：`if (key == null) return 0`

**案例3：Hot Key拆分后，下游的Sink收到了同一个逻辑key的多条记录**
- 根因：拆分后同一个逻辑key分布在多个物理分区，Sink端没有做合并
- 解方：在Sink前做一次keyBy + 合并；或在Sink端使用UPSERT（主键去重）

### 优点 & 缺点

| | 极端场景优化方案（加盐/自定义Partitioner/拆分） | 不做优化/默认KeyBy |
|------|-----------|-----------|
| **优点1** | 两阶段聚合将倾斜key均匀打散到所有分区 | 简单直接，零代码改动 |
| **优点2** | 自定义Partitioner精确控制热key路由 | Hash分区不受控 |
| **优点3** | Hot Key拆分解决单key的RocksDB写入瓶颈 | 单key串行写入，QPS受限 |
| **缺点1** | 两阶段聚合多一次Shuffle，额外网络开销 | 无额外开销 |
| **缺点2** | COUNT DISTINCT等非代数聚合不支持两阶段 | 天然支持所有聚合类型 |
| **缺点3** | 自定义Partitioner需维护热key列表 | 无需维护 |

### 适用场景

**典型场景**：
1. 大促/热点事件导致的数据倾斜——热门商品/大V用户key倾斜
2. RocksDB单key写入QPS超过10万——Hot Key拆分
3. Kafka Source消费速度不足——WAL瓶颈优化（增大batch/分区）
4. 作业负载不均衡需要精细控制——自定义Partitioner

**不适用场景**：
1. 数据分布天然均匀的作业——加盐引入不必要的Shuffle开销
2. COUNT DISTINCT等高精度去重场景——两阶段聚合不支持

### 思考题

1. 两阶段聚合中，盐值基数（saltFactor）越大，数据分布越均匀，但第二阶段合并的key越多。saltFactor应该怎么选择？如果saltFactor=10但热key的数据量只占总量的1%，效果怎么样？

2. 自定义Partitioner和Rebalance分区都用过，两者的区别是什么？为什么说两阶段聚合本质上是一种"先Rebalance后KeyBy"？

---

> **完整代码**：本章完整代码请参考附录或访问 https://github.com/flink-column/flink-practitioner  
> **思考题答案**：见附录文件 `appendix-answers.md`
