# 第22章 多表Broadcast与宽表合并

## 1 项目背景

### 业务场景：订单宽表的实时构建

数据分析团队需要一张**订单宽表**，包含订单信息、商品信息、用户信息、支付信息——而不是分散在4张MySQL表中。传统的做法是离线ETL（每天凌晨跑Spark SQL JOIN），但离线宽表的延迟至少是T+1，无法满足实时看板的需求。

**实时宽表合并** 的挑战：
1. 数据来自不同表，到达Flink CDC的时间不同（order先到，user后到）
2. 有些是维表（用户信息、商品信息），变化不频繁
3. 有些是事实表（订单信息），变化频繁
4. JOIN时如果关联的数据还没到，需要用"等待-超时"策略

### 宽表合并模式

```
┌───── 事实表（频繁变更）─────┐
│  orders: order_id, user_id,  │
│  product_id, amount, status  │
└────────────┬─────────────────┘
             │ JOIN
┌───── 维表1（低频变更）──────┐
│  users: user_id, username,   │  ← 通过Broadcast分发到所有Task
│  level, phone                │
└────────────┬─────────────────┘
             │ JOIN
┌───── 维表2（低频变更）──────┐
│  products: product_id, name, │  ← 通过Broadcast分发到所有Task
│  category, price             │
└────────────┬─────────────────┘
             │
             ▼
┌──────────────────────────────┐
│  宽表: order_id, user_name,   │
│  product_name, amount, status │
│  user_level, category        │
└──────────────────────────────┘
```

---

## 2 项目设计 · 三人交锋对话

**小胖**（挠头）：多个CDC流JOIN……这和在Flink SQL里做`SELECT * FROM A JOIN B ON A.id = B.id`有啥区别？

**大师**：Flink SQL的`JOIN`确实可以做宽表合并，但CDC场景有三个特殊的挑战：

**挑战1：流的无限性**
两个CDC流都是无限的。`A JOIN B`在Flink SQL中默认是"regular join"——A的每条记录需要和B的所有记录匹配。B一更新，之前JOIN过的A的所有行都需要重新输出（更新）。这会导致"JOIN爆炸"——更新一条维表数据，可能导致宽表中引用了该维表的所有行都更新一遍。

**挑战2：事件到达顺序**
假设订单先到、用户信息后到——这时JOIN时用户信息还没到，宽表这一行应该等还是输出null？

**挑战3：维表变更的传播**
用户表把"张三"改成了"李四"，已经生成的宽表行需要更新。"张三" -> "李四"的变更需要传播到宽表中所有引用该用户的行。

**技术映射**：实时宽表JOIN像"拼积木时说明书在另一张桌子上"——你拿到的积木（订单）到了，但说明书（用户信息）还没拿到。你是等说明书到了再拼（等待Join），还是先凭记忆拼上再说（先输出null再Update）？Flink提供了不同的JOIN策略来应对。

**小白**：那维表（users, products）通过Broadcast分发到所有Task是什么原理？

**大师**：Broadcast是Flink的一种特殊数据分发机制。默认情况下，数据按KeyBy分区，每个Subtask只处理部分数据。但**维表数据需要被所有Subtask访问**（因为任何订单都可能关联到任何用户）。

```
Broadcast分发：
  users表CDC流 → 复制N份 → 发给所有并行Subtask

每个Subtask维护一个本地Map：
  Map<user_id, UserInfo>  ← 通过Broadcast更新
  Map<product_id, ProductInfo>  ← 通过Broadcast更新

当order事件到达时，直接从本地Map查找：
  UserInfo user = userMap.get(order.user_id);
  ProductInfo product = productMap.get(order.product_id);
```

这种方式的优点是：
- 维表查询零网络开销（本地内存查找）
- 维表变更实时生效（CDC更新Map）
- 不会因为维表JOIN产生反压

缺点是：
- 维表数据量不能太大（每台TaskManager都存一份完整副本）
- 维表更新频率不能太高（每次更新都要Broadcast到所有Task）

---

## 3 项目实战

### 分步实现

#### 步骤1：创建多张源表

```sql
USE shop;

-- 订单表（事实表）
CREATE TABLE orders_fact (
    order_id    VARCHAR(64) PRIMARY KEY,
    user_id     INT NOT NULL,
    product_id  INT NOT NULL,
    amount      DECIMAL(10,2),
    status      VARCHAR(32),
    order_time  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 用户表（维表）
CREATE TABLE users_dim (
    user_id     INT PRIMARY KEY,
    username    VARCHAR(64),
    level       VARCHAR(32),
    register_time TIMESTAMP
);

-- 商品表（维表）
CREATE TABLE products_dim (
    product_id  INT PRIMARY KEY,
    product_name VARCHAR(128),
    category    VARCHAR(64),
    price       DECIMAL(10,2)
);

-- 插入数据
INSERT INTO users_dim VALUES
(1, 'Alice', 'VIP', '2023-01-01'),
(2, 'Bob', 'Normal', '2023-06-01');

INSERT INTO products_dim VALUES
(101, 'iPhone 15', '手机', 6999.00),
(102, 'MacBook Air', '笔记本', 8999.00);

INSERT INTO orders_fact VALUES
('ORD001', 1, 101, 6999.00, 'PAID', NOW()),
('ORD002', 1, 102, 8999.00, 'PENDING', NOW()),
('ORD003', 2, 101, 6999.00, 'PAID', NOW());
```

#### 步骤2：DataStream API实现多流Join宽表

```java
package com.example;

import org.apache.flink.api.common.state.MapState;
import org.apache.flink.api.common.state.MapStateDescriptor;
import org.apache.flink.api.common.typeinfo.Types;
import org.apache.flink.configuration.Configuration;
import org.apache.flink.streaming.api.datastream.DataStream;
import org.apache.flink.streaming.api.environment.StreamExecutionEnvironment;
import org.apache.flink.streaming.api.functions.co.KeyedBroadcastProcessFunction;
import org.apache.flink.util.Collector;

/**
 * 多流Join：订单事实表 + 用户维表（Broadcast）→ 宽表
 */
public class WideTableJoin {

    public static void main(String[] args) throws Exception {
        StreamExecutionEnvironment env = StreamExecutionEnvironment.getExecutionEnvironment();
        env.enableCheckpointing(5000);
        env.setParallelism(2);

        // 省略：ordersStream, usersStream的定义（使用对应CDC Source）
        // 这里假设已经从CDC Source获取了对应的流

        DataStream<String> ordersStream = ...; // 订单事实流
        DataStream<String> usersStream = ...;  // 用户维表流

        // 维表Broadcast State描述符
        MapStateDescriptor<Integer, String> userStateDesc =
            new MapStateDescriptor<>(
                "users_dim",           // State名称
                Types.INT,             // Key: user_id
                Types.STRING           // Value: 用户信息JSON
            );

        // 将维表流Broadcast
        org.apache.flink.streaming.api.datastream.BroadcastStream<String> broadcastUsers =
            usersStream.broadcast(userStateDesc);

        // 订单流与Broadcast维表连接
        DataStream<String> wideStream = ordersStream
            .keyBy(order -> extractUserId(order))
            .connect(broadcastUsers)
            .process(new OrderUserJoinFunction());

        wideStream.print();

        env.execute("Wide Table Join Demo");
    }

    /**
     * 订单流 + Broadcast维表 的Join处理函数
     */
    public static class OrderUserJoinFunction
        extends KeyedBroadcastProcessFunction<Integer, String, String, String> {

        private transient MapStateDescriptor<Integer, String> userStateDesc;

        @Override
        public void open(Configuration parameters) {
            userStateDesc = new MapStateDescriptor<>(
                "users_dim", Types.INT, Types.STRING);
        }

        @Override
        public void processElement(String orderJson, ReadOnlyContext ctx,
                                   Collector<String> out) throws Exception {
            // 从Broadcast State读取用户信息
            int userId = extractUserId(orderJson);
            String userJson = ctx.getBroadcastState(userStateDesc).get(userId);

            String wideRow;
            if (userJson != null) {
                wideRow = mergeOrderAndUser(orderJson, userJson);
            } else {
                // 用户信息还没到，先用占位符
                wideRow = orderJson.replace("}", ",\"username\":\"UNKNOWN\"}");
            }
            out.collect(wideRow);
        }

        @Override
        public void processBroadcastElement(String userJson, Context ctx,
                                            Collector<String> out) throws Exception {
            // 维表变更：更新Broadcast State
            int userId = extractUserId(userJson);
            ctx.getBroadcastState(userStateDesc).put(userId, userJson);
        }
    }

    private static int extractUserId(String json) {
        return Integer.parseInt(
            json.replaceAll(".*\"user_id\":(\\d+).*", "$1"));
    }

    private static String mergeOrderAndUser(String order, String user) {
        // 简化：将用户的username字段合并到order的JSON中
        String username = user.replaceAll(
            ".*\"username\":\"([^\"]+)\".*", "$1");
        return order.replace("}",
            ",\"username\":\"" + username + "\"}");
    }
}
```

#### 步骤3：Pipeline YAML实现多表合并

Flink CDC Pipeline API目前不直接支持多流Join。但可以通过**自定义Event处理**或**Flink SQL的JOIN**实现。

使用Flink SQL CDC实现宽表JOIN：

```sql
-- 创建CDC Source表
CREATE TABLE orders_fact (
    order_id STRING PRIMARY KEY NOT ENFORCED,
    user_id INT,
    product_id INT,
    amount DECIMAL(10,2),
    status STRING,
    order_time TIMESTAMP(3)
) WITH (
    'connector' = 'mysql-cdc',
    'hostname' = 'localhost', 'port' = '3306',
    'username' = 'cdc_user', 'password' = 'cdc_pass',
    'database-name' = 'shop', 'table-name' = 'orders_fact'
);

CREATE TABLE users_dim (
    user_id INT PRIMARY KEY NOT ENFORCED,
    username STRING,
    level STRING,
    register_time TIMESTAMP(3)
) WITH (
    'connector' = 'mysql-cdc',
    'hostname' = 'localhost', 'port' = '3306',
    'username' = 'cdc_user', 'password' = 'cdc_pass',
    'database-name' = 'shop', 'table-name' = 'users_dim'
);

CREATE TABLE products_dim (
    product_id INT PRIMARY KEY NOT ENFORCED,
    product_name STRING,
    category STRING,
    price DECIMAL(10,2)
) WITH (
    'connector' = 'mysql-cdc',
    'hostname' = 'localhost', 'port' = '3306',
    'username' = 'cdc_user', 'password' = 'cdc_pass',
    'database-name' = 'shop', 'table-name' = 'products_dim'
);

-- 宽表JOIN（Regular Join）
-- 注意：这会产生Changelog流，需要支持UPDATE的Sink
INSERT INTO wide_table_sink
SELECT
    o.order_id,
    o.amount,
    o.status,
    u.username,
    u.level,
    p.product_name,
    p.category
FROM orders_fact o
LEFT JOIN users_dim u ON o.user_id = u.user_id
LEFT JOIN products_dim p ON o.product_id = p.product_id;
```

#### 步骤4：验证宽表合并结果

```bash
# 在MySQL中执行变更
docker exec mysql-cdc mysql -uroot -proot123 -e "
USE shop;

-- 查看当前宽表结果
-- 预期输出：
-- ORD001 | Alice | VIP | iPhone 15 | 6999.00 | PAID
-- ORD002 | Alice | VIP | MacBook Air | 8999.00 | PENDING
-- ORD003 | Bob | Normal | iPhone 15 | 6999.00 | PAID

-- 修改维表（更改用户名）
UPDATE users_dim SET username = 'Alice_VIP' WHERE user_id = 1;

-- 预期：宽表中user_id=1对应的行的username自动更新为Alice_VIP
"
```

#### 常见陷坑及解决方法

| 问题 | 原因 | 解决方法 |
|------|------|---------|
| Broadcast维表导致OOM | 维表数据量太大（>TaskManager可用内存） | 减少Broadcast数据量，或使用KeyedLookupJoin（如JDBC Connector） |
| LEFT JOIN中维表数据迟到 | 订单已经输出了，用户信息才到 | 使用`Flink SQL`的`lookup` join或自定义延迟等待逻辑 |
| 维表UPDATE产生大量宽表更新 | 维表数据频繁变更，宽表需要级联更新 | 评估维表更新频率，低频维表（如用户信息日更新1次）适合Broadcast |
| 多个维表合并时JOIN顺序影响性能 | 大维表JOIN小维表 vs 小维表JOIN大维表 | 小维表Broadcast + 大维表按Key分区 |

---

## 4 项目总结

### 宽表合并策略对比

| 策略 | 延迟 | 维表数据量限制 | 维表更新传播 | 实现复杂度 |
|------|------|--------------|------------|-----------|
| Broadcast Join | 实时 | 小（<500MB/TM） | ✅ 自动更新 | 中 |
| Lookup Join (JDBC) | 实时（每次查询） | 无限制 | ✅ 每次查数据库 | 低 |
| Flink SQL Regular Join | 实时 | 无限制 | ✅ 自动更新 | 低 |
| 离线ETL (Spark) | T+1 | 无限制 | ❌ 非实时 | 低 |

### 注意事项

1. **维表大小**：Broadcast State存储在TaskManager堆内存中，维表总大小不能超过每个TaskManager的可用堆内存（留出30%余量）。
2. **更新频率**：如果维表每秒变更1000次以上，Broadcast Join会导致网络风暴（每次变更都要发给所有Task）。考虑改用Lookup Join。
3. **NULL处理**：维表数据迟到时，先输出NULL占位还是等待？Streaming Join中的等待可能导致Buffer无限增长。

### 常见踩坑经验

**故障案例1：Broadcast State维表更新导致Checkpoint超大**
- **现象**：Checkpoint大小从10MB涨到2GB
- **根因**：Broadcast State中存储的维表数据在Checkpoint时被完整序列化保存
- **解决方案**：降低维表Checkpoint频率（增大Checkpoint间隔），或使用RocksDB状态后端

**故障案例2：Flink SQL JOIN维表时数据不一致**
- **现象**：左侧订单JOIN右侧用户，但username显示为用户在"3小时前"的值
- **根因**：Flink SQL的Regular Join在Flink内部维护了左右两侧的State。如果State的TTL（`table.exec.state.ttl`）配置为3小时，超过TTL的数据被清理后，JOIN只能基于当时State中的数据
- **解决方案**：设置合适的`table.exec.state.ttl`，或使用Lookup Join（每次都查最新数据）

**故障案例3：多流Join时Sink频繁执行UPDATE**
- **现象**：目标库（如ES）的写入TPS飙升10倍
- **根因**：维表的一个UPDATE触发了宽表中所有关联行的UPDATE，下游Sink收到大量更新请求
- **解决方案**：对于低频更新的维表，在Transform阶段去重——只有当维表的"宽表相关字段"发生变化时才触发宽表更新

### 思考题

1. **进阶题①**：Flink SQL CDC中，`LEFT JOIN`维表的Regular Join和`FOR SYSTEM_TIME AS OF`的Temporal Join有什么区别？对于"维表实时更新"的场景，哪个更适合？为什么？

2. **进阶题②**：在Broadcast Join宽表合并方案中，如果维表数据超过了TaskManager内存，有哪些替代方案？Redis缓存维表 + Flink AsyncIO查询是否可行？比较两种方案的优缺点。

---

> **下一章预告**：第23章「数据湖集成：Flink CDC + Apache Iceberg」——CDC数据入湖是当前数据架构的热点。你将学会如何把MySQL的实时变更数据写入Apache Iceberg表，利用Iceberg的ACID特性保证数据一致性，并实现Time Travel查询和历史版本回溯。
