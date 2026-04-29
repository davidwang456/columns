# 第5章 玩转Flink CDC SQL

## 1 项目背景

### 业务场景：运营团队的自助数据查询平台

公司运营团队想做一个**自助订单分析平台**——运营同学不需要提SQL工单给数据开发团队，自己就能在界面上选择时间范围、订单状态、商品品类等条件，实时查询最新的订单数据。而且，这些数据必须是**实时的**：运营经理刚在后台审批了一个退款订单，查询结果里必须立刻反映出来。

之前，运营团队的做法是：
1. 运营在页面上点击"查询"
2. 后端直接查询MySQL主库
3. 返回结果给前端

但上线后发现，运营团队**高频的即席查询**（每天几千次）直接导致了MySQL主库的CPU飙升到90%以上，影响了核心交易链路的正常订单写入。

显然，直接查主库不可行。但如果把数据同步到分析型数据库（如ClickHouse、Doris）又涉及复杂的ETL开发和维护。

**有没有一种方案，既不需要写复杂的ETL代码，又能让运营团队直接用标准SQL查询实时数据？**

### 痛点放大

| 方案 | 实时性 | 开发成本 | 对源库影响 | SQL支持 |
|------|-------|---------|-----------|--------|
| 直接查MySQL主库 | ✅ 实时 | 0（直接查） | ❌ 压垮主库 | ✅ 完整 |
| 定时ETL到分析库 | ❌ 分钟级延迟 | 高（需要写DataX/Sqoop脚本） | 中（定时全量查询） | ✅ 完整 |
| 业务双写MySQL+分析库 | ✅ | 极高（改代码+发版） | 低 | ✅ |
| **Flink CDC + Flink SQL** | ✅ 秒级 | **低（5行DDL定义Source）** | 极低（Binlog监听） | ✅ 支持标准SQL + 窗口函数 |

Flink SQL在这里扮演了关键角色——运营团队只需要在Flink SQL CLI中定义一张"映射表"（Source Table），就能像查询普通表一样查询MySQL的实时变更数据。这个过程不需要写Java代码，纯SQL操作。

### Flink CDC SQL运行架构

```
┌─────────────┐     ┌────────────────────────────────────────┐
│ Flink SQL   │     │          Flink SQL Engine              │
│ CLIENT      │     │                                        │
│ ┌─────────┐ │     │ ┌────────────┐  ┌──────────────────┐   │
│ │ CREATE  │─┼──►  │ │ SQL Parser │─►│ Optimizer       │   │
│ │ TABLE   │ │     │ │ (Calcite)  │  │ (HepPlanner /   │   │
│ │ orders  │ │     │ └────────────┘  │  VolcanPlanner)  │   │
│ │ ...     │ │     │                 └────────┬─────────┘   │
│ │ SELECT  │ │     │                          │              │
│ │ * FROM  │ │     │                          ▼              │
│ │ orders  │ │     │ ┌───────────────────────────────────┐  │
│ └─────────┘ │     │ │    Flink CDC Table Source         │  │
│             │     │ │  mysql-cdc connector (动态表)     │  │
└─────────────┘     │ └───────────────────────────────────┘  │
                    │                                        │
                    │ ┌───────────────────────────────────┐  │
                    │ │    Flink Runtime (DataStream)      │  │
                    │ │  MySqlSource → execute SQL logic   │  │
                    │ └───────────────────────────────────┘  │
                    └────────────────────────────────────────┘
```

---

## 2 项目设计 · 三人交锋对话

**小胖**（喝着快乐水）：第4章我跟着写了30行Java代码，跑了CDC程序，挺爽的。但我听说还有更简单的方式——Flink SQL，说5行SQL就能搞定CDC？真的假的？

**大师**：真的。让我们直接对比一下：

**DataStream API版（~30行）：**
```java
MySqlSource<String> source = MySqlSource.<String>builder()
    .hostname("localhost").port(3306)
    .databaseList("shop").tableList("shop.orders")
    .username("cdc_user").password("cdc_pass")
    .deserializer(new JsonDebeziumDeserializationSchema())
    .startupOptions(StartupOptions.initial())
    .build();
env.fromSource(source, ...).print();
```

**SQL API版（5行SQL）：**
```sql
CREATE TABLE orders (
    order_id STRING,
    user_id INT,
    product STRING,
    amount DECIMAL(10,2),
    status STRING,
    PRIMARY KEY (order_id) NOT ENFORCED
) WITH (
    'connector' = 'mysql-cdc',
    'hostname' = 'localhost',
    'port' = '3306',
    'username' = 'cdc_user',
    'password' = 'cdc_pass',
    'database-name' = 'shop',
    'table-name' = 'orders'
);

SELECT * FROM orders;
```

**小胖**：哇真香！SQL读写全表都是自动的，连JSON解析都省了！那我啥时候用DataStream，啥时候用SQL呢？

**大师**：这个问题问得好。我们来看看各自的适用边界：

**SQL API 适合：**
- 简单CDC转发（Source → Sink透传）
- 标准过滤/投影（WHERE + SELECT列裁剪）
- 维表Join（CDC事实表关联MySQL维表）
- 窗口聚合（ORDER BY event_time GROUP BY TUMBLE）

**DataStream API 适合：**
- 复杂业务逻辑（多流Join、自定义状态、定时器）
- 特殊类型处理（GEOMETRY、JSON字段嵌套解析）
- 需要精确控制Checkpoint的行为
- 需要接入Flink CDC未覆盖的自定义连接器

**小白**（若有所思）：我注意到SQL的`CREATE TABLE`语法里有个`PRIMARY KEY ... NOT ENFORCED`。NOT ENFORCED是什么意思？Flink在CDC场景下会校验主键吗？

**大师**：这是个很好的细节问题。`NOT ENFORCED`的意思是：**Flink不会校验主键的唯一性**。在CDC场景中，主键有两个用途：
1. **去重**：当收到同一主键的UPDATE/DELETE事件时，知道要更新/删除哪行数据
2. **Upsert写入**：给下游Sink一个语义保证——可以通过主键覆盖

之所以加`NOT ENFORCED`而不是`ENFORCED`，是因为Flink CDC的数据来自MySQL，主键唯一性已经由MySQL保证了。Flink再去校验一遍是额外的性能开销，没有必要。所以Flink说"我信任你，我不校验，但你得保证传上来的数据主键确实是唯一的"。

**小白**：那如果是`CREATE TABLE`的时候漏掉了某个字段怎么办？比如orders表有10个字段，但我只定义了5个？CDC还能正常工作吗？

**大师**：可以工作！Flink CDC SQL的Schema是**读时定义（Schema-on-Read）** ——
- 你定义的字段从MySQL读取
- 没定义的字段被忽略
- 如果你定义了MySQL中不存在的字段，运行时会报错

但这里有一个陷阱——如果你少定义了`status`字段，那么当订单状态从PAID变为SHIPPED时，Flink根本不知道status变了。更危险的是，`DELETE`事件可能依赖完整主键。所以建议：**在DDL中列出所有你关心的字段**，不一定要全部，但必须包含主键。

**技术映射**：SQL DDL中的字段列表就像"快递收件人列表"——你只填了你关心的收件人（字段），快递员（CDC）不会把其他人的包裹（未定义字段）塞给你，但你也不会收到其他人的信息。

**小胖**（摩拳擦掌）：那Flink SQL的"动态表"是什么概念？MySQL的orders表是一个静态表，到了Flink怎么就变成"动态"的了？

**大师**：这是理解Flink SQL中最核心也最容易混淆的概念。**Flink中的表不是"存数据的容器"，而是"不断变化的数据流"**。传统数据库的表是"快照"（某一时刻的数据状态），而Flink的表是"变更日志"（所有变更事件的合集）。

举个例子，MySQL的orders表：
```
时间点1: [order_1, order_2]          ← 初始状态
时间点2: [order_1, order_2, order_3] ← INSERT order_3 → 追加一行
时间点3: [order_1(已修改), order_2]   ← UPDATE order_1 → 修改一行
```

Flink SQL的"动态表"把每个变更都看作一个事件：
```
INSERT(order_3) → 追加事件
UPDATE(order_1) → 更新事件（包含前值和后值）
```

当你`SELECT * FROM orders`时，Flink输出的是**变更事件流**，而不是一张静态快照。如果要把变更流变成"类MySQL快照"的效果，需要用`SELECT ... FROM ... GROUP BY`做聚合，或者用CDC专用的Changelog Stream模式。

---

## 3 项目实战

### 环境准备

**前置条件：**
- 第3章的Docker Compose环境（MySQL + Flink）已启动
- MySQL的shop.orders表已创建且有数据
- Flink Web UI可用（http://localhost:8081）

**关键的JAR依赖（必须放在Flink的lib目录下）：**
```bash
# 1. Flink CDC MySQL连接器（含Debezium）
curl -o lib/flink-sql-connector-mysql-cdc-3.0.0.jar \
  https://repo1.maven.org/maven2/org/apache/flink/flink-sql-connector-mysql-cdc/3.0.0/flink-sql-connector-mysql-cdc-3.0.0.jar

# 2. MySQL JDBC驱动
curl -o lib/mysql-connector-java-8.0.33.jar \
  https://repo1.maven.org/maven2/mysql/mysql-connector-java/8.0.33/mysql-connector-java-8.0.33.jar

# 3. 复制到Flink容器
docker cp lib/flink-sql-connector-mysql-cdc-3.0.0.jar flink-jm-cdc:/opt/flink/lib/
docker cp lib/mysql-connector-java-8.0.33.jar flink-jm-cdc:/opt/flink/lib/
# TaskManager也需要同样的JAR
docker cp lib/flink-sql-connector-mysql-cdc-3.0.0.jar flink-tm-cdc:/opt/flink/lib/
docker cp lib/mysql-connector-java-8.0.33.jar flink-tm-cdc:/opt/flink/lib/

# 重启Flink集群加载新JAR
docker restart flink-jm-cdc flink-tm-cdc
```

### 分步实现

#### 步骤1：启动Flink SQL CLI并定义CDC Source表

```bash
# 进入Flink容器
docker exec -it flink-jm-cdc /bin/bash

# 启动SQL CLI
./bin/sql-client.sh
```

在SQL CLI中执行：

```sql
-- 1. 设置结果模式为table（或changelog），默认table模式类似MySQL的输出
SET 'sql-client.execution.result-mode' = 'table';

-- 2. 设置作业名称（在Flink Web UI上识别）
SET 'pipeline.name' = 'Flink CDC Orders Monitor';

-- 3. 创建CDC Source表——这是核心！
-- 注意：这里的column定义必须与MySQL真实表结构匹配（不一定要全部列出）
CREATE TABLE orders_cdc (
    id              INT,
    order_id        STRING,
    user_id         INT,
    product         STRING,
    amount          DECIMAL(10, 2),
    status          STRING,
    create_time     TIMESTAMP(3),
    update_time     TIMESTAMP(3),
    -- CDC的元数据字段（事件操作类型、Binlog位置等）
    -- 这些不是MySQL表中的字段，而是Flink CDC自动提供的元数据
    proc_time       AS PROCTIME(),                         -- 处理时间（processing time）
    PRIMARY KEY (id) NOT ENFORCED                          -- 主键定义（用于去重）
) WITH (
    'connector'            = 'mysql-cdc',                  -- 连接器类型
    'hostname'             = 'mysql',                      -- Docker网络内使用服务名
    'port'                 = '3306',
    'username'             = 'cdc_user',
    'password'             = 'cdc_pass',
    'database-name'        = 'shop',
    'table-name'           = 'orders',
    'server-time-zone'     = 'Asia/Shanghai',             -- 时区
    'scan.startup.mode'    = 'initial',                    -- initial=全量+增量, latest-offset=仅增量
    'debezium.snapshot.mode' = 'initial'                  -- Debezium快照模式（透传参数）
);
```

#### 步骤2：执行实时查询

```sql
-- 查询所有订单（实时变更流）
SELECT * FROM orders_cdc;
```

**预期输出：**

在执行上述查询后，Flink SQL CLI会立即输出MySQL中的全量数据（初始快照），然后等待新变更：

```
+----+----+--------------+---------+--------------+---------+-------------------------+
| op | id |    order_id  | user_id |   product    | amount  |   create_time           |
+----+----+--------------+---------+--------------+---------+-------------------------+
| +I | 1  | ORD20240101001|    1    | iPhone 15    | 6999.00 | 2024-01-01 10:00:00.000 |
| +I | 2  | ORD20240101002|    2    | AirPods Pro  | 1999.00 | 2024-01-01 10:01:00.000 |
| +I | 3  | ORD20240101003|    3    | MacBook Air  | 8999.00 | 2024-01-01 10:02:00.000 |
```

`+I` 表示INSERT操作。在另一个终端中执行MySQL变更：

```sql
-- 插入新订单
INSERT INTO orders VALUES (4, 'ORD20240101004', 4, 'iPad Pro', 7999.00, 'PENDING', NOW(), NOW());
```

SQL CLI自动刷新：
```
| +I | 4  | ORD20240101004|    4    | iPad Pro     | 7999.00 | 2024-01-01 10:05:00.000 |
```

#### 步骤3：使用Changelog Stream模式观察CDC事件类型

```sql
-- 切换到changelog模式（会显示每条变更的操作类型）
SET 'sql-client.execution.result-mode' = 'changelog';

-- 重新查询
SELECT * FROM orders_cdc;
```

现在再执行UPDATE和DELETE观察输出：

```sql
UPDATE orders SET status = 'PAID' WHERE order_id = 'ORD20240101004';
DELETE FROM orders WHERE order_id = 'ORD20240101003';
```

**Changelog输出：**
```
+I(1, iPhone 15, 6999.00)
+I(2, AirPods Pro, 1999.00)
+I(3, MacBook Air, 8999.00)
+I(4, iPad Pro, 7999.00)
-U(4, iPad Pro, 7999.00)  ← 更新前状态
+U(4, iPad Pro, 7999.00)  ← 更新后状态
-D(3, MacBook Air, 8999.00)  ← 删除操作
```

这里的`+I`/`-U`/`+U`/`-D`对应Flink的RowKind枚举：INSERT、UPDATE_BEFORE、UPDATE_AFTER、DELETE。

#### 步骤4：实时聚合查询——统计各种状态的订单数

```sql
-- 实时统计各状态订单数和金额汇总
-- 这是一个持续更新的查询结果
SELECT
    status,
    COUNT(*) AS order_count,
    SUM(amount) AS total_amount
FROM orders_cdc
GROUP BY status;
```

**预期输出（实时变动的聚合结果）：**

| status | order_count | total_amount |
|--------|------------|-------------|
| PAID | 1 | 6999.00 |
| SHIPPED | 1 | 1999.00 |
| DONE | 1 | 8999.00 |
| PENDING | 1 | 7999.00 |

当MySQL中的订单状态变化时，这个聚合结果会自动更新——不需要手动触发，不需要刷新页面。

#### 步骤5：带窗口的实时分析——每分钟的新增订单量

```sql
-- 每分钟统计新增订单数和金额
SELECT
    TUMBLE_END(proc_time, INTERVAL '1' MINUTE) AS window_end,
    COUNT(*) AS new_orders,
    SUM(amount) AS total_amount
FROM orders_cdc
GROUP BY TUMBLE(proc_time, INTERVAL '1' MINUTE);
```

这里使用`PROCTIME()`（处理时间）做滚动窗口，每1分钟统计一次新增订单量。

#### 步骤6：通过Java代码提交Flink SQL作业

除了在SQL CLI中交互式运行，我们也可以像第4章一样，通过Java代码提交SQL作业：

```java
package com.example;

import org.apache.flink.streaming.api.environment.StreamExecutionEnvironment;
import org.apache.flink.table.api.bridge.java.StreamTableEnvironment;

/**
 * 通过Java代码执行Flink CDC SQL作业
 * 功能：从MySQL CDC读取订单，按状态聚合实时写入控制台
 */
public class FlinkCDCWithSql {

    public static void main(String[] args) throws Exception {
        // 1. 创建Flink流处理环境
        StreamExecutionEnvironment env = StreamExecutionEnvironment.getExecutionEnvironment();
        env.enableCheckpointing(5000);
        env.setParallelism(1);

        // 2. 创建Table环境
        StreamTableEnvironment tableEnv = StreamTableEnvironment.create(env);

        // 3. 执行DDL：创建CDC Source表
        // 语法与SQL CLI完全一致
        tableEnv.executeSql(
            "CREATE TABLE orders_cdc (" +
            "    id INT, " +
            "    order_id STRING, " +
            "    user_id INT, " +
            "    product STRING, " +
            "    amount DECIMAL(10, 2), " +
            "    status STRING, " +
            "    create_time TIMESTAMP(3), " +
            "    update_time TIMESTAMP(3), " +
            "    PRIMARY KEY (id) NOT ENFORCED " +
            ") WITH (" +
            "    'connector' = 'mysql-cdc', " +
            "    'hostname' = 'localhost', " +
            "    'port' = '3306', " +
            "    'username' = 'cdc_user', " +
            "    'password' = 'cdc_pass', " +
            "    'database-name' = 'shop', " +
            "    'table-name' = 'orders', " +
            "    'scan.startup.mode' = 'initial'" +
            ")"
        );

        // 4. 执行SQL查询：实时聚合各状态订单数
        tableEnv.executeSql(
            "SELECT status, COUNT(*) AS order_count, SUM(amount) AS total_amount " +
            "FROM orders_cdc " +
            "GROUP BY status"
        ).print();

        // 5. 触发执行
        env.execute("Flink CDC SQL Demo");
    }
}
```

#### 常见陷坑及解决方法

| 问题 | 原因 | 解决方法 |
|------|------|---------|
| `Table is not an append-only table, use console output instead` | `GROUP BY`后输出Changelog流，print()不支持 | 使用`executeSql(...).print()`代替`toRetractStream` |
| `Connector 'mysql-cdc' not found` | JAR包未放到Flink的lib目录 | 确认`flink-sql-connector-mysql-cdc.jar`在`/opt/flink/lib/`中 |
| `Caused by: java.sql.SQLException: Access denied` | 密码或账号错误 | 确认Docker容器内使用服务名`mysql`而不是`localhost`连接 |
| DDL中字段类型不匹配 | Flink类型映射与MySQL类型不完全一致 | 查阅类型映射表（如`BIGINT UNSIGNED`需要映射为`DECIMAL(20)` |
| 长时间无输出 | 没有新数据写入MySQL | 手动执行INSERT触发新事件 |
| SQL CLI报错看不到完整堆栈 | SQL CLI日志级别为WARN | 在SQL CLI中执行`SET 'log.level' = 'DEBUG';` |

---

## 4 项目总结

### 优点 & 缺点

**Flink CDC SQL API的优势：**
1. **极低成本**：5行DDL定义Source表，不需要写任何Java代码
2. **标准SQL**：运营/分析师可以直接用他们熟悉的SQL进行实时查询
3. **自动Schema映射**：不再需要手动解析JSON或实现`DeserializationSchema`
4. **生态兼容**：与Flink SQL的完整生态系统（各种UDF、连接器、Catalog）无缝配合
5. **动态表语义**：`GROUP BY`、`JOIN`、`窗口函数`等操作自动转换为Changelog流

**SQL API的局限：**
1. **计算能力受限**：复杂的状态逻辑（如自定义窗口触发策略）不易用SQL表达
2. **Schema Evolution自动化不足**：表结构变更后需要手动修改DDL
3. **性能瓶颈**：SQL优化器无法像手写DataStream那样精确控制执行计划
4. **调试困难**：SQL执行计划是黑盒，难以定位性能问题

### API选择决策树

```
需要CDC吗？
├── 是
│   ├── 只需要简单过滤/投影/聚合？
│   │   ├── 是 → SQL API（推荐）
│   │   └── 否
│   │       ├── 需要复杂状态逻辑/多流Join/自定义窗口？
│   │       │   ├── 是 → DataStream API
│   │       │   └── 否 → SQL API + UDF
│   ├── 需要标准化的数据管道（Source→Transform→Sink）？
│   │   └── 是 → Pipeline YAML API
│   └── 团队大部分是SQL背景？
│       └── 是 → SQL API
└── 否 → 考虑普通Flink Source
```

### 注意事项

1. **类型映射**：MySQL的`BIGINT UNSIGNED`在Flink中要映射为`DECIMAL(20)`，否则数值溢出。`TINYINT(1)`映射为`BOOLEAN`，`YEAR`映射为`INT`。
2. **主键必要性**：如果MySQL表没有主键，Flink CDC SQL无法支持UPDATE/DELETE操作的正确语义。此时建议至少定义一个唯一索引列作为主键。
3. **时区一致性**：MySQL的`server-time-zone`必须和Flink的`table.local-time-zone`一致，否则时间字段可能出现偏差。
4. **SQL CLI限制**：SQL CLI是交互式工具，不适合生产环境。生产环境应将SQL通过Java代码或Flink Gateway提交。

### 常见踩坑经验

**故障案例1：Flink SQL CDC聚合结果与MySQL直接查不一致**
- **现象**：`SELECT status, COUNT(*) FROM orders_cdc GROUP BY status` 的结果和MySQL的`SELECT status, COUNT(*) FROM orders GROUP BY status`不一致
- **根因**：Flink SQL是全量快照+增量流的不断累加结果。如果作业重启过，聚合的状态是从`initial`模式的最新快照开始累积的，而不是从0开始。ORDER状态变更只会改变聚合的旧值和新值，不会直接"覆写"结果
- **解决方案**：理解Flink的Retract模式——`GROUP BY`的结果是"一种累加器"，每次变更输出的是"撤回旧值 + 推送新值"。需要对比理解`+U`和`-U`的含义

**故障案例2：Scan startup mode配置不生效**
- **现象**：`'scan.startup.mode' = 'latest-offset'`配置后，仍然全量读取了MySQL的历史数据
- **根因**：Flink CDC 2.4之后，`scan.startup.mode`选项被`StartupOptions`对象替代。在SQL DDL中，如果同时配置了`debezium.snapshot.mode`，后者会覆盖前者的行为
- **解决方案**：只设置`scan.startup.mode`，不要同时设置`debezium.snapshot.mode`。两者是同一功能在不同层的配置，同时设置会导致冲突

**故障案例3：Flink SQL CDC作业在Checkpoint后出现重复数据**
- **现象**：Kafka Sink中出现了少量重复的CDC事件（数据量<0.01%）
- **根因**：Flink的Exactly-Once保证在Source端是通过"在Checkpoint中记录Binlog位置"实现的。但如果MySQL的数据被多个Source task消费（server-id冲突），会导致同一个Binlog事件被多个task读到
- **解决方案**：确保每个Flink作业使用唯一的server-id范围。不要在同一个MySQL实例上运行两个相同tableList的CDC作业

### 思考题

1. **进阶题①**：在Flink SQL CDC中，`CREATE TABLE`定义的`PRIMARY KEY`对于`UPDATE`和`DELETE`事件的处理有什么影响？如果一个表没有主键，执行`UPDATE`操作时Flink会怎么输出Changelog？提示：查看`RowKind`的`UPDATE_BEFORE`和`UPDATE_AFTER`。

2. **进阶题②**：Flink SQL中执行`SELECT * FROM orders_cdc`时，Flink会在Flink Web UI上生成一个作业，包含Source和Sink两个算子。请问这个作业的并行度是由什么决定的？如果MySQL的orders表有1000万行数据，Flink会串行读完还是并行读完？提示：查看`MySqlSource`的并行度配置逻辑。

---

> **下一章预告**：第6章「MySQL Binlog深度解析与CDC配置」——在已经可以跑通CDC程序的基础上，我们将深入MySQL Binlog的底层原理：ROW/STATEMENT/MIXED三种格式的区别、GTID的全局事务标识、server-id冲突的根因分析和解决方案，以及`scan.startup.mode`五种模式的选型指南。
