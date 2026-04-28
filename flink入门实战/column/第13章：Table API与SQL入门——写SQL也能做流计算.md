# 第13章：Table API与SQL入门——写SQL也能做流计算

---

## 1. 项目背景

某电商BI团队每天需要从实时数据流中查询：每个品类过去1小时的GMV（成交额）Top 10 品类、所有分类的成交额占比、实时GMV与昨日同期的对比。

BI分析师们精通SQL，但不懂Java。难道每提一个新需求都要找开发团队写DataStream代码、编译、打包、部署？一次至少等3天。

Flink的 **Table API & SQL** 正是为解决这个问题而生。它允许你用标准SQL直接操作流数据，无需编写Java代码。更重要的是：

- **流批统一**：同一张表可以同时作为流和批来查询——开发阶段按批测试，上线后自动切换为流模式
- **自动优化**：Flink SQL优化器（基于Apache Calcite）会自动选择执行计划，很多场景下比手写DataStream API的性能更好
- **CDC一键入湖**：`CREATE TABLE ... WITH ('connector'='kafka')` 一行定义表，下一行就可以 `SELECT * FROM`——不需要写任何Source代码

但Flink SQL也有它的边界和陷阱：动态表的概念（与传统数据库的静态表完全不同）、Retraction机制（流上的UPDATE/DELETE如何映射到SQL结果）、状态无限膨胀（无界流上的GROUP BY需要谨慎）。

---

## 2. 项目设计

> 场景：BI分析师小美拿着10个SQL需求来找技术团队，小胖一看——都是简单的GROUP BY + 窗口。

**小胖**：这些需求写DataStream API太烦了——每个都要建POJO、写MapFunction、定义Window。能不能写SQL解决？

**大师**：Flink SQL就是干这个的。而且你说反了——**能用SQL解决的问题，不要用DataStream API**。SQL经过Calcite优化器生成执行计划，很多场景比自己手写operator更高效。**技术映射：Flink SQL底层最终还是编译为DataStream作业。但SQL层做了大量优化（谓词下推、投影消除、子查询去关联等）。**

**小白**：那SQL怎么处理流上的"动态数据"？传统SQL查的是静态表，但流上的数据是源源不断来的，一个GROUP BY count(*)的结果每秒都在变。

**大师**：Flink SQL引入了一个关键概念——**动态表（Dynamic Table）**。

- 静态表：查询执行时，表内容固定不变（如MySQL表）
- 动态表：表的内容随时间持续变化（每条流数据到达，表就多一行/更新一行）

Flink SQL将流映射为动态表，对动态表的查询持续产生"更新日志"（changelog stream）。**技术映射：动态表 = 流数据的SQL视角。每条新数据到达，动态表就更新一次，SQL结果也会连续更新输出。**

**小胖**：那UPDATE和DELETE在流上怎么表现？流只有追加数据，没有修改或删除啊。

**大师**：这就是**Retraction（撤回）机制**。Flink SQL处理有界聚合时，上游数据的变更（如订单状态从"创建"变成"完成"）会产生两条记录：先发送一条DELETE（撤回前一个结果），再发送一条INSERT（新结果）。下游Sink必须支持撤回——如果不支持（比如Kafka），结果中会有多余的"中间状态"数据。

**技术映射：Retraction = 流上的"反更新"。用 -1 的计数或带正负号的累加器来表达"之前的结果不再正确"。** 

**小白**：那性能呢？SQL写的聚合会不会比DataStream慢？

**大师**：对于简单聚合，SQL和DataStream性能差异在5%以内。对于复杂多表Join，SQL优化器常常做出比程序员更好的执行计划。**但有三种情况建议不要用SQL：① 需要自定义State（如复杂的数据结构）② 需要精确控制数据路由（自定义Partitioner）③ 极端性能优化场景（手写operator能压榨最后10%的吞吐）。**

---

## 3. 项目实战

### 环境准备

```xml
<dependency>
    <groupId>org.apache.flink</groupId>
    <artifactId>flink-table-api-java-bridge</artifactId>
    <version>${flink.version}</version>
</dependency>
<dependency>
    <groupId>org.apache.flink</groupId>
    <artifactId>flink-table-planner-loader</artifactId>
    <version>${flink.version}</version>
</dependency>
```

### 分步实现

#### 步骤1：在DataStream项目中集成Flink Table API

**目标**：创建StreamTableEnvironment，将DataStream注册为Table，然后用SQL查询。

```java
package com.flink.column.chapter13;

import org.apache.flink.api.common.eventtime.WatermarkStrategy;
import org.apache.flink.api.java.tuple.Tuple3;
import org.apache.flink.streaming.api.datastream.DataStream;
import org.apache.flink.streaming.api.environment.StreamExecutionEnvironment;
import org.apache.flink.table.api.Table;
import org.apache.flink.table.api.bridge.java.StreamTableEnvironment;
import org.apache.flink.types.Row;
import java.time.Duration;

import static org.apache.flink.table.api.Expressions.$;

/**
 * Table API + SQL入门：将流注册为表，用SQL查询实时GMV
 * 输入: <category>,<amount>,<timestamp>
 */
public class TableAPIDemo {

    public static void main(String[] args) throws Exception {
        StreamExecutionEnvironment env = StreamExecutionEnvironment.getExecutionEnvironment();
        env.setParallelism(2);
        StreamTableEnvironment tableEnv = StreamTableEnvironment.create(env);

        DataStream<String> source = env.socketTextStream("localhost", 9999);

        // 将DataStream转换为Tuple3并加上Watermark
        DataStream<Tuple3<String, Double, Long>> data = source
                .map(line -> {
                    String[] p = line.split(",");
                    return Tuple3.of(p[0], Double.parseDouble(p[1]), Long.parseLong(p[2]));
                })
                .returns(Types.TUPLE(Types.STRING, Types.DOUBLE, Types.LONG))
                .assignTimestampsAndWatermarks(
                        WatermarkStrategy.<Tuple3<String, Double, Long>>forBoundedOutOfOrderness(
                                        Duration.ofSeconds(5))
                                .withTimestampAssigner((event, ts) -> event.f2)
                );

        // ========== 方式1：仅用Table API ==========
        // 将DataStream注册为Table（不注册临时表名，直接使用table对象）
        Table ordersTable = tableEnv.fromDataStream(
                data,
                $("category"),    // f0 映射为 category
                $("amount"),      // f1 映射为 amount
                $("eventTime").rowtime()  // f2 映射为事件时间属性
        );

        // Table API方式查询
        Table categoryGmv = ordersTable
                .groupBy($("category"))
                .select(
                        $("category"),
                        $("amount").sum().as("total_gmv")
                );

        // 打印结果
        categoryGmv.execute().print();

        env.execute("Chapter13-TableAPIDemo");
    }
}
```

#### 步骤2：纯SQL方式——注册表后写SELECT

**目标**：用DDL注册Kafka表，用SELECT做实时聚合（与DataStream解耦）。

```java
// 方法2：纯SQL
tableEnv.executeSql(
    "CREATE TABLE orders (" +
    "  category STRING," +
    "  amount DOUBLE," +
    "  eventTime BIGINT," +
    "  ts AS TO_TIMESTAMP_LTZ(eventTime, 3)," +   // 转TIMESTAMP
    "  WATERMARK FOR ts AS ts - INTERVAL '5' SECOND" +
    ") WITH (" +
    "  'connector' = 'kafka'," +
    "  'topic' = 'order-topic'," +
    "  'properties.bootstrap.servers' = 'kafka:9092'," +
    "  'format' = 'json'," +
    "  'scan.startup.mode' = 'latest-offset'" +
    ")");

// 查询：每小时每品类的GMV
Table result = tableEnv.sqlQuery(
    "SELECT " +
    "  category, " +
    "  SUM(amount) AS total_gmv, " +
    "  TUMBLE_END(ts, INTERVAL '1' HOUR) AS window_end " +
    "FROM orders " +
    "GROUP BY " +
    "  category, " +
    "  TUMBLE(ts, INTERVAL '1' HOUR)");

result.execute().print();
```

#### 步骤3：退化的GROUP BY——不加窗口的无限聚合

**目标**：理解无界流上不加窗口的GROUP BY会产生无限增长的状态。

```sql
-- ❌ 危险！无限聚合（无窗口）
SELECT category, SUM(amount) AS total_gmv
FROM orders
GROUP BY category;

-- 这个查询没有窗口，状态会无限增长——每个category从未出现到永远都保留一个累加值
-- 如果category基数很大（如几百万个），状态很快撑爆内存
```

**正确做法**：始终加窗口或设置状态TTL。

```sql
-- ✅ 加窗口
SELECT category, SUM(amount), TUMBLE_END(ts, INTERVAL '1' HOUR)
FROM orders
GROUP BY category, TUMBLE(ts, INTERVAL '1' HOUR);

-- 或者全局加状态TTL配置
tableEnv.getConfig().setIdleStateRetention(Duration.ofHours(24));
```

#### 步骤4：窗口聚合——SQL中的三种窗口

**目标**：用SQL实现滚动/滑动/会话窗口。

```sql
-- 滚动窗口: 每1小时
SELECT category, SUM(amount),
       TUMBLE_START(ts, INTERVAL '1' HOUR) AS win_start,
       TUMBLE_END(ts, INTERVAL '1' HOUR) AS win_end
FROM orders
GROUP BY category, TUMBLE(ts, INTERVAL '1' HOUR);

-- 滑动窗口: 每5分钟统计过去15分钟
SELECT category, SUM(amount),
       HOP_START(ts, INTERVAL '5' MINUTE, INTERVAL '15' MINUTE) AS win_start,
       HOP_END(ts, INTERVAL '5' MINUTE, INTERVAL '15' MINUTE) AS win_end
FROM orders
GROUP BY category, HOP(ts, INTERVAL '5' MINUTE, INTERVAL '15' MINUTE);

-- 会话窗口: 10分钟gap
SELECT category, SUM(amount),
       SESSION_START(ts, INTERVAL '10' MINUTE) AS win_start,
       SESSION_END(ts, INTERVAL '10' MINUTE) AS win_end
FROM orders
GROUP BY category, SESSION(ts, INTERVAL '10' MINUTE);
```

#### 步骤5：理解Retraction——观察SQL结果的撤回记录

**目标**：在控制台中观察Flink的Retraction消息。

```sql
-- 对相同的category不断追加新的amount
-- Flink SQL会输出：
-- +I (category_1, 100.0)  ← INSERT
-- -U (category_1, 100.0)  ← UPDATE_BEFORE (撤回旧值)
-- +U (category_1, 250.0)  ← UPDATE_AFTER (新值)
```

在代码中设置 `tableEnv.executeSql(...)` 方式输出到Print，会看到 `-U` / `+U` / `-D` 前缀的记录，分别表示撤回、更新、删除操作。

#### 步骤6：在Flink SQL Client中交互式查询

**目标**：使用Flink SQL Client做流式SQL查询（无需写Java代码）。

```bash
# 1. 启动Flink集群（Docker环境）
docker exec -it flink-jm sql-client.sh

# 2. 创建Kafka表
Flink SQL> CREATE TABLE page_views (
  user_id STRING,
  page_id STRING,
  view_time BIGINT,
  ts AS TO_TIMESTAMP_LTZ(view_time, 3),
  WATERMARK FOR ts AS ts - INTERVAL '5' SECOND
) WITH (
  'connector' = 'kafka',
  'topic' = 'page-view',
  'properties.bootstrap.servers' = 'kafka:9092',
  'format' = 'json'
);

# 3. 实时查询
Flink SQL> SELECT page_id, COUNT(*) AS pv, COUNT(DISTINCT user_id) AS uv
FROM page_views
GROUP BY page_id, TUMBLE(ts, INTERVAL '10' SECOND);
```

### 可能遇到的坑

1. **Flink SQL报错：GroupBy on unbounded table without window**
   - 根因：无限流上不能做无界GROUP BY（状态无限膨胀）
   - 解决：加上窗口函数；或者设置`tableEnv.getConfig().setIdleStateRetention()`

2. **Retraction导致下游Sink（如ElasticSearch）数据混乱**
   - 根因：ES不支持Flink SQL的Changelog Stream模式（INSERT/UPDATE/DELETE的标记）
   - 解方：使用Upsert模式Sink，或确保SQL输出最终结果时已经通过窗口做了"最终"聚合

3. **Flink SQL的时间戳字段解析错误**
   - 根因：Flink SQL中的`TIMESTAMP`需要特定的格式。BIGINT时间戳要用`TO_TIMESTAMP_LTZ(eventTime, 3)`转换
   - 解方：BIGINT毫秒 → `TO_TIMESTAMP_LTZ(ts, 3)`；STRING → `TO_TIMESTAMP(str, 'yyyy-MM-dd HH:mm:ss')`

---

## 4. 项目总结

### Flink SQL vs DataStream API

| 维度 | Flink SQL | DataStream API |
|------|-----------|---------------|
| 开发效率 | 高（写SQL即可） | 低（需要Java/Scala） |
| 性能 | 中等（优化器自动优化） | 高（可精细控制） |
| 复杂状态 | 有限（仅有聚合状态） | 任意（Value/List/Map State） |
| 窗口 | 内置三种窗口 | 内置+自定义Trigger |
| 学习成本 | 低（熟悉SQL即可） | 高 |
| 维护成本 | 低（SQL易读） | 高 |

### 三种使用方式

| 方式 | 说明 | 适用场景 |
|------|------|---------|
| Table API | Java/Scala中链式调用 | 需要Table API和DataStream API混用 |
| SQL | 字符串SQL查询 | BI分析师、快速原型 |
| SQL Client | 交互式CLI | 运维排查、数据探查 |

### 注意事项
- 不要在Flink SQL中做无界GROUP BY——必须有窗口或状态TTL
- Flink SQL的时间属性列（PROCTIME / ROWTIME）不能来自普通字段——必须是计算列或元数据列
- 使用 `SET` 命令可以动态调整SQL Client配置（如 `SET execution.runtime-mode = batch` 切换批模式）

### 常见踩坑经验

**案例1：Flink SQL查询返回空结果但DataStream有数据**
- 根因：Watermark没有触发窗口。SQL中的WATERMARK定义必须与查询中的窗口类型一致
- 解方：检查DDL中WATERMARK列与SELECT中使用的窗口函数是否匹配

**案例2：UNION ALL两个流，结果不完整或重复**
- 根因：两个流的并行度不一致导致UNION ALL的数据分布不均匀
- 解方：确认两个流的并行度一致，或者使用`SET 'table.exec.resource.default-parallelism' = '4'`统一并行度

**案例3：INSERT INTO MySQL时大批量写入超时**
- 根因：Flink SQL的JDBC Sink默认批量插入，但这个批量和Checkpoint配合时容易超时
- 解方：使用`SET 'table.exec.sink.upsert-materialize' = 'none'`减少中间状态

### 优点 & 缺点

| | Flink SQL / Table API | DataStream API（Java手写） |
|------|-----------|-----------|
| **优点1** | 开发效率高——写SQL即可，BI分析师也能自助查询 | 需Java编码、编译、打包、部署 |
| **优点2** | Calcite优化器自动选择执行计划 | 需手写算子链、手动优化 |
| **优点3** | DDL定义Source/Sink一行搞定，零代码对接外部系统 | 需手动配置Connector、序列化器等 |
| **优点4** | 流批一体——同一SQL可切换批/流执行模式 | 流/批API分离，需写两套代码 |
| **缺点1** | 复杂状态逻辑无法表达——依赖自定义UDF | ValueState/ListState/MapState任意组合 |
| **缺点2** | 性能调优空间有限——优化器决策不可控 | 可精细控制每条数据的处理路径 |
| **缺点3** | Retraction机制对下游Sink有约束（需支持changelog） | 数据完全自主可控，无Retraction约束 |

### 适用场景

**典型场景**：
1. BI分析师自助查询——用SQL实时探查Kafka流数据，无需开发介入
2. 简单ETL入湖——用DDL+INSERT INTO实现Kafka→HDFS/MySQL直传
3. 窗口聚合报表——小时/天级窗口聚合，SQL简洁表达
4. CDC数据同步——用Flink CDC Connector实现MySQL→Kafka同步

**不适用场景**：
1. 复杂有状态业务逻辑——需要自定义State结构、定时器、ProcessFunction的场景
2. 极端性能优化——需要手动控制算子链、内存布局、序列化的高吞吐场景

### 思考题

1. 下面的SQL查询运行在无限流上，它会产生什么结果？状态会无限增长吗？如果不加窗口，Flink SQL内部是如何处理这个查询的？
```sql
SELECT userId, COUNT(*) AS login_count
FROM login_events
GROUP BY userId;
```

2. 动态表（Dynamic Table）的概念在什么场景下会导致"结果与预期不符"？比如你用一个带Retraction的SQL查询输出到Kafka Topic，下游消费这个Topic的应用会看到什么模式的数据？

---

> **完整代码**：本章完整代码请参考附录或访问 https://github.com/flink-column/flink-practitioner  
> **思考题答案**：见附录文件 `appendix-answers.md`
