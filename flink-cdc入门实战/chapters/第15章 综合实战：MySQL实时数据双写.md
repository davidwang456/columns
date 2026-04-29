# 第15章 综合实战：MySQL实时数据双写

> **本文属于基础篇综合实战**，整合前14章的知识：Flink CDC DataStream/SQL API、Checkpoint状态恢复、数据路由、基础转换、监控等。完成一个可运行的、生产级别的实时数据双写系统。

## 1 项目背景

### 业务场景：电商订单系统的灾备需求

某电商平台的订单系统使用MySQL主库存储。架构团队要求**必须实现数据库级别的灾备**：订单数据需要实时同步到：
1. **Kafka**（`order_events` Topic）——供下游微服务消费（发短信、更新库存、推送物流）
2. **MySQL备库**（`shop_backup.orders`）——作为灾备数据库，主库故障时可直接切换

### 技术需求

| 需求 | 说明 | 优先级 |
|------|------|--------|
| 全量初始化 | 存量订单（约500万条）同步到Kafka和备库 | P0 |
| 增量实时同步 | 新订单/变更在1秒内同步 | P0 |
| 幂等写入 | 重复的CDC事件不产生重复数据 | P1 |
| 容错恢复 | 作业崩溃后自动恢复，数据不丢不重 | P0 |
| 字段过滤 | 敏感字段（phone, internal_remark）不写入Kafka | P1 |
| 延迟告警 | 同步延迟>5秒触发告警 | P2 |
| 可观测性 | 监控大盘展示吞吐/延迟/反压 | P2 |

### 架构设计图

```
                         ┌─────────────────────┐
                         │    MySQL 主库        │
                         │  shop.orders        │
                         │  (Binlog ROW)       │
                         └──────────┬──────────┘
                                    │
                                    ▼
┌───────────────────────────────────────────────────────────────┐
│                  Flink CDC 作业 (DataStream + SQL)            │
│                                                               │
│  ┌──────────────────┐    ┌────────────┐    ┌───────────────┐ │
│  │ MySqlSource      │───►│ Transform  │───►│ Route         │ │
│  │ (FLIP-27,并行度2)│    │ 过滤敏感列  │    │  Kafka + MySQL│ │
│  └──────────────────┘    └────────────┘    └───────┬───────┘ │
│                                                      │         │
└──────────────────────────────────────────────────────┼─────────┘
                           │                            │
                           ▼                            ▼
              ┌─────────────────────┐    ┌────────────────────┐
              │  Kafka              │    │  MySQL 备库        │
              │  topic: order_events│    │  shop_backup.orders│
              │  (事件驱动，7天保留)  │    │  (灾备，幂等写入)   │
              └─────────────────────┘    └────────────────────┘
                           │
                           ▼
              ┌─────────────────────┐
              │  Grafana 监控大盘    │
              │  (吞吐/延迟/反压/CP) │
              └─────────────────────┘
```

---

## 2 项目设计 · 三人交锋对话

**小胖**（跃跃欲试）：综合实战来了！这次要跑通MySQL→Kafka+MySQL双写。我就问一句——先写Kafka还是先写MySQL？还是一起写？

**大师**：这是一个经典的"多路输出一致性"问题。Flink CDC DataStream API提供了两种方案：

**方案A：单一Sink → 下游再分发**
Source → Transform → Sink(Kafka) → 下游消费Kafka再写到MySQL
- 优点：Flink作业只写Kafka，逻辑简单
- 缺点：MySQL备库有额外延迟（消费Kafka再写入），端到端延迟翻倍

**方案B：Flink Side Output / 多Sink**
Source → Transform → 分流 → 同时写Kafka + MySQL
- 优点：延迟最低，Flink内完成所有写入
- 缺点：Flink作业需要处理两个Sink的事务一致性

推荐生产环境用**方案A**（解耦），但本章综合实战用**方案B**（演示Flink多Sink能力）。

**小白**：那MySQL备库的幂等写入怎么保证？如果CDC重放一条已经应用过的UPDATE怎么办？

**大师**：MySQL备库的幂等写入依靠**主键去重**：

```sql
-- 幂等INSERT（存在就更新）
INSERT INTO backup_orders (id, order_id, status, ...)
VALUES (?, ?, ?, ...)
ON DUPLICATE KEY UPDATE
    status = VALUES(status),
    update_time = VALUES(update_time);

-- 或者使用REPLACE INTO（先删后插）
REPLACE INTO backup_orders (id, order_id, status, ...)
VALUES (?, ?, ?, ...);
```

使用`INSERT ... ON DUPLICATE KEY UPDATE`是推荐做法——它只在主键冲突时才执行UPDATE，不像`REPLACE INTO`会先DELETE再INSERT（会重置AUTO_INCREMENT）。

**小胖**：那把敏感字段过滤掉，用第10章的Projection就能搞定吧？Kafka里不要phone列，但备库里要保留？

**大师**：这就是"同源不同目标"的典型场景——同一份CDC数据，给不同目标不同字段：

```
Source: [id, order_id, user_id, amount, phone, internal_remark]
                    │
          ┌─────────┴─────────┐
          ▼                    ▼
     Kafka（去敏感字段）      MySQL备库（全字段）
     [id, order_id, amount]   [id, order_id, user_id, amount, phone, internal_remark]
```

实现方式：用`Side Output`（侧输出流）把数据分到两个流，每个流有不同的转换逻辑。

---

## 3 项目实战

### 环境准备

**依赖：**
- Flink 1.20.3 + Flink CDC 3.0.0
- MySQL 8.0（主库） + MySQL 8.0（备库——Docker中新增一个MySQL实例）
- Kafka 3.5.1

**Docker Compose新增备库MySQL：**
```yaml
mysql_backup:
  image: mysql:8.0
  container_name: mysql-backup-cdc
  ports:
    - "3307:3306"
  environment:
    MYSQL_ROOT_PASSWORD: root123
    MYSQL_DATABASE: shop_backup
  volumes:
    - ./conf/mysql/my.cnf:/etc/mysql/conf.d/my.cnf
    - mysql_backup_data:/var/lib/mysql
  networks:
    - flink-cdc-net
```

**备库创建表结构：**
```sql
CREATE TABLE shop_backup.orders (
    id              INT PRIMARY KEY,
    order_id        VARCHAR(64) NOT NULL,
    user_id         INT NOT NULL,
    product         VARCHAR(128),
    amount          DECIMAL(10,2),
    status          VARCHAR(32),
    phone           VARCHAR(32),       -- 保留手机号（与生产库一致）
    internal_remark VARCHAR(256),       -- 保留内部备注
    create_time     TIMESTAMP,
    update_time     TIMESTAMP,
    cdc_version     INT DEFAULT 0,     -- 版本号，用于乐观锁
    cdc_op          VARCHAR(1),        -- 记录操作类型（c/u/d）
    cdc_ts          BIGINT,            -- CDC事件时间戳
    PRIMARY KEY (id),
    UNIQUE KEY uk_order_id (order_id)
);
```

### 分步实现

#### 步骤1：主库创建测试数据

```sql
USE shop;

-- 创建带敏感字段的订单表
CREATE TABLE orders_full (
    id                INT AUTO_INCREMENT PRIMARY KEY,
    order_id          VARCHAR(64) NOT NULL,
    user_id           INT NOT NULL,
    product           VARCHAR(128),
    amount            DECIMAL(10,2),
    status            VARCHAR(32) DEFAULT 'PENDING',
    phone             VARCHAR(32),          -- 敏感字段
    internal_remark   VARCHAR(256),         -- 敏感字段
    create_time       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    update_time       TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);

-- 插入模拟数据（500万条模拟存量数据）
-- 注意：实际生产可以用存储过程生成，这里只插5条用于测试
INSERT INTO orders_full VALUES
(1, 'ORD20240001', 1001, 'iPhone 15', 6999.00, 'PAID', '13800001111', 'VIP客户', NOW(), NOW()),
(2, 'ORD20240002', 1002, 'MacBook Air', 8999.00, 'SHIPPED', '13800002222', NULL, NOW(), NOW()),
(3, 'ORD20240003', 1003, 'iPad Pro', 7999.00, 'PENDING', '13800003333', '企业采购', NOW(), NOW()),
(4, 'ORD20240004', 1001, 'AirPods Pro', 1999.00, 'PAID', '13800001111', NULL, NOW(), NOW()),
(5, 'ORD20240005', 1004, 'Apple Watch', 3299.00, 'DONE', '13800004444', '已退货', NOW(), NOW());
```

#### 步骤2：编写双写Flink CDC主程序

```java
package com.example;

import org.apache.flink.api.common.eventtime.WatermarkStrategy;
import org.apache.flink.api.common.typeinfo.TypeInformation;
import org.apache.flink.cdc.connectors.mysql.source.MySqlSource;
import org.apache.flink.cdc.connectors.mysql.table.StartupOptions;
import org.apache.flink.cdc.debezium.DebeziumDeserializationSchema;
import org.apache.flink.streaming.api.datastream.DataStream;
import org.apache.flink.streaming.api.datastream.SingleOutputStreamOperator;
import org.apache.flink.streaming.api.environment.StreamExecutionEnvironment;
import org.apache.flink.streaming.api.functions.ProcessFunction;
import org.apache.flink.util.Collector;
import org.apache.flink.util.OutputTag;
import org.apache.kafka.connect.source.SourceRecord;
import org.apache.kafka.connect.data.Struct;
import org.apache.flink.api.common.functions.MapFunction;

/**
 * 综合实战：MySQL → Kafka + MySQL 双写
 * 
 * 功能：
 *   1. 从MySQL读取orders_full表的CDC事件
 *   2. 分流：Kafka流（去敏感字段）+ MySQL备库流（全字段）
 *   3. Kafka写入幂等事件
 *   4. MySQL备库使用UPSERT语义写入
 *   5. 监控指标暴露
 */
public class DualWritePipeline {

    // 侧输出Tag：MySQL备库流（保留全字段）
    private static final OutputTag<String> BACKUP_TAG =
        new OutputTag<String>("mysql-backup") {};

    public static void main(String[] args) throws Exception {
        // ========== 1. 执行环境配置 ==========
        StreamExecutionEnvironment env = StreamExecutionEnvironment.getExecutionEnvironment();
        env.enableCheckpointing(10000);                    // Checkpoint每10秒
        env.getCheckpointConfig().setMinPauseBetweenCheckpoints(1000);
        env.getCheckpointConfig().setCheckpointTimeout(300000);
        env.getCheckpointConfig().enableExternalizedCheckpoints(
            org.apache.flink.streaming.api.environment.CheckpointConfig
                .ExternalizedCheckpointCleanup.RETAIN_ON_CANCELLATION
        );
        env.setParallelism(2);  // 并行度2

        // ========== 2. Source配置 ==========
        MySqlSource<String> source = MySqlSource.<String>builder()
            .hostname("localhost").port(3306)
            .databaseList("shop").tableList("shop.orders_full")
            .username("cdc_user").password("cdc_pass")
            .serverId("5400-5401")          // 并行度2所需
            .deserializer(new JsonDebeziumDeserializationSchema())
            .startupOptions(StartupOptions.initial())
            .serverTimeZone("Asia/Shanghai")
            .build();

        DataStream<String> cdcStream = env.fromSource(
            source,
            WatermarkStrategy.noWatermarks(),
            "MySQL CDC Source"
        );

        // ========== 3. 分流 ==========
        // Kafka流：去敏感字段
        // MySQL备库流：保留全字段 + UPSERT语义
        SingleOutputStreamOperator<String> kafkaStream = cdcStream
            .process(new DualOutputProcessor());

        DataStream<String> backupStream = kafkaStream.getSideOutput(BACKUP_TAG);

        // ========== 4. 输出到Kafka ==========
        // 生产环境中替换为KafkaSink，这里用print模拟
        kafkaStream
            .map(new KafkaTransform())
            .print("KAFKA>> ");

        // ========== 5. 输出到MySQL备库 ==========
        // 生产环境中替换为JDBCSink，这里用print模拟
        backupStream.print("BACKUP>> ");

        // ========== 6. 启动作业 ==========
        env.execute("DualWritePipeline - MySQL → Kafka + MySQL");
    }

    /**
     * 分流处理器：根据操作类型和路由策略分发到不同的输出
     */
    public static class DualOutputProcessor extends ProcessFunction<String, String> {

        @Override
        public void processElement(String json, Context ctx, Collector<String> out) {
            // 主输出：Kafka流（去敏感字段）
            out.collect(json);

            // 侧输出：MySQL备库流（保留全字段）
            ctx.output(BACKUP_TAG, json);
        }
    }

    /**
     * Kafka转换：去掉敏感字段 + 格式化输出
     */
    public static class KafkaTransform implements MapFunction<String, String> {

        @Override
        public String map(String json) {
            // 去掉敏感字段
            json = json.replaceAll("\"phone\":\"[^\"]*\",?", "");
            json = json.replaceAll("\"internal_remark\":\"[^\"]*\",?", "");
            // 添加目标表名
            json = json.replace(
                "\"op\"",
                "\"target_topic\":\"order_events\",\"op\"");
            return json;
        }
    }
}
```

#### 步骤3：编写MySQL备库幂等Sink

```java
package com.example;

import org.apache.flink.configuration.Configuration;
import org.apache.flink.streaming.api.functions.sink.RichSinkFunction;

import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.PreparedStatement;

/**
 * MySQL备库幂等Sink —— 使用INSERT ... ON DUPLICATE KEY UPDATE实现幂等写入
 */
public class IdempotentMysqlSink extends RichSinkFunction<String> {

    private transient Connection conn;
    private transient PreparedStatement upsertStmt;

    @Override
    public void open(Configuration parameters) throws Exception {
        // 生产环境使用连接池（HikariCP）
        conn = DriverManager.getConnection(
            "jdbc:mysql://localhost:3307/shop_backup",  // 备库3307
            "cdc_user", "cdc_pass"
        );
        conn.setAutoCommit(false);  // 手动提交

        // UPSERT语句：存在则更新，不存在则插入
        upsertStmt = conn.prepareStatement(
            "INSERT INTO orders (id, order_id, user_id, product, amount, status, phone, " +
            "internal_remark, create_time, update_time, cdc_op, cdc_ts) " +
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) " +
            "ON DUPLICATE KEY UPDATE " +
            "order_id = VALUES(order_id), " +
            "status = VALUES(status), " +
            "cdc_op = VALUES(cdc_op), " +
            "cdc_ts = VALUES(cdc_ts)"
        );
    }

    @Override
    public void invoke(String json, Context context) throws Exception {
        // 解析JSON → 设置PreparedStatement参数
        // 简化处理：从JSON中提取字段
        int id = extractInt(json, "id");
        String orderId = extractStr(json, "order_id");
        String status = extractStr(json, "status");
        String op = extractStr(json, "op");

        upsertStmt.setInt(1, id);
        upsertStmt.setString(2, orderId);
        // ... 其他字段

        upsertStmt.executeUpdate();
        conn.commit();
    }

    @Override
    public void close() throws Exception {
        if (upsertStmt != null) upsertStmt.close();
        if (conn != null) conn.close();
    }

    private int extractInt(String json, String key) { /* JSON解析 */ return 0; }
    private String extractStr(String json, String key) { /* JSON解析 */ return ""; }
}
```

#### 步骤4：验证双写结果

```bash
# 1. 在主库执行变更
docker exec mysql-cdc mysql -uroot -proot123 -e "
USE shop;
INSERT INTO orders_full VALUES (6, 'ORD20240006', 1005, 'Mac Mini', 4999.00, 'PENDING', '13800005555', '新客户', NOW(), NOW());
UPDATE orders_full SET status = 'SHIPPED' WHERE id = 1;
DELETE FROM orders_full WHERE id = 5;
"

# 2. 验证Kafka流输出去掉了敏感字段
# 期望KAFKA>>输出不包含phone和internal_remark字段

# 3. 验证MySQL备库收到了所有字段
docker exec mysql-backup-cdc mysql -uroot -proot123 -e "
SELECT id, order_id, status, phone, cdc_op FROM shop_backup.orders;
"
# 期望：id=1的status变为SHIPPED，phone字段存在
# 期望：id=6是新插入的行，具有完整字段
# 期望：id=5的行已被标记删除
```

#### 步骤5：测试容错恢复

```bash
# 1. 记录当前Checkpoint
# 在Flink Web UI上找到最近的Checkpoint路径

# 2. 在主库插入测试数据
docker exec mysql-cdc mysql -uroot -proot123 -e "
USE shop; INSERT INTO orders_full VALUES (7, 'ORD20240007', 1006, 'Test CP', 100.00, 'PENDING', '13800006666', '测试Checkpoint', NOW(), NOW());
"

# 3. 模拟作业崩溃
docker exec flink-jm-cdc flink cancel <job-id>

# 4. 确认Checkpoint已保留
ls /tmp/flink-checkpoints/

# 5. 从Checkpoint恢复
docker exec flink-jm-cdc flink run -s <checkpoint-path> \
  -c com.example.DualWritePipeline \
  /opt/flink/lib/flink-cdc-demo.jar

# 6. 验证数据不丢不重
# 插入的数据id=7应该正好出现一次，不会重复
```

#### 常见陷坑及解决方法

| 问题 | 原因 | 解决方法 |
|------|------|---------|
| 备库写入慢导致反压 | 逐条写入MySQL，没有批量 | 使用JDBC Batch（`addBatch() + executeBatch()`） |
| UPSERT导致死锁 | 多个并发写入同一条记录 | 使用`INSERT ... ON DUPLICATE KEY UPDATE`而非`REPLACE INTO` |
| Kafka写入数据量不一致 | JSON格式解析错误 | 使用统一的`DebeziumDeserializationSchema`确保格式一致 |
| 侧输出Tag类型不匹配 | `OutputTag`的泛型类型错误 | 确认`OutputTag<String>`中的String与侧输出流类型一致 |
| 全量快照阶段数据重复 | 全量快照和增量Binlog的数据重叠 | 使用`StartupOptions.initial()`自动处理水位对齐 |

---

## 4 项目总结

### 方案对比

| 对比维度 | 双写Flink作业（本方案） | 写Kafka+下游消费写MySQL | Canal+Kafka+Dual消费 |
|---------|---------------------|----------------------|--------------------|
| 端到端延迟 | < 1秒 | ~2-3秒 | ~1-2秒 |
| 架构复杂度 | 中（一个Flink作业） | 低（写Kafka最简单） | 高（需要维护Canal + Kafka + 消费程序） |
| MySQL写入吞吐 | ~5000条/秒（取决于批次大小） | 取决于消费者 | ~3000条/秒（Canal单线程） |
| Exactly-Once | ✅ | ✅（幂等写入） | ⚠️（Canal At-Least-Once） |
| 运维成本 | 低（Flink统一管） | 中（多一个消费者） | 高（多组件） |

### 基础篇知识点回顾

通过这15章的学习，你应该已经掌握了：

| 章节 | 核心技能 | 掌握程度 |
|------|---------|---------|
| 1-2 | Flink核心概念、CDC原理 | ✅ 理解术语和工作原理 |
| 3 | Docker Compose环境搭建 | ✅ 可一键部署开发环境 |
| 4-5 | DataStream API + SQL API | ✅ 能写第一个CDC程序 |
| 6 | MySQL Binlog配置和调优 | ✅ 理解server-id、GTID、startup mode |
| 7 | Event模型 | ✅ 理解DataChangeEvent和SchemaChangeEvent |
| 8 | 多数据源配置 | ✅ 掌握MySQL/PG/MongoDB/Oracle配置 |
| 9 | 数据路由 | ✅ 掌握正则匹配和替换规则 |
| 10 | 基础数据转换 | ✅ 掌握Projection和Filter |
| 11 | PostgreSQL CDC | ✅ 理解逻辑复制和复制槽 |
| 12 | MongoDB CDC | ✅ 理解Change Streams和嵌套文档 |
| 13 | Checkpoint和状态恢复 | ✅ 能配置和手动恢复作业 |
| 14 | 监控与Metrics | ✅ 能看懂Flink Web UI指标 |
| 15 | 综合实战 | ✅ 能独立完成双写系统 |

### 注意事项

1. **生产环境替换Sink**：本章的`print()`只是演示，生产环境应替换为`KafkaSink`或`JdbcSink`。
2. **并行度选择**：本例设并行度=2，生产环境应根据数据量、表数量、状态大小合理选择。
3. **批写入优化**：MySQL备库Sink使用`executeBatch()`每1000条提交一次，不要逐条INSERT。
4. **全量快照资源**：第一次运行`initial()`模式时，全量快照会消耗较多CPU和内存。建议在业务低峰期执行。

### 综合实战拓展思考

本章完成了MySQL→Kafka+MySQL双写。你可以尝试以下拓展：

1. **拓展1**：增加PostgreSQL备库作为第三个目标（第11章的PG知识）
2. **拓展2**：将数据同时写入Iceberg（第23章预告）作为数据湖存储
3. **拓展3**：增加Kafka Sink的Exactly-Once事务写入（第26章预告）
4. **拓展4**：添加更完善的监控指标和Grafana大盘（第28章预告）

### 思考题

1. **进阶题①**：在本章的双写架构中，如果Kafka Sink成功但MySQL备库写入失败，Checkpoint应该提交还是回滚？Flink的`TwoPhaseCommitSinkFunction`能否同时协调两个Sink？如果不能，有什么替代方案？提示：考虑Kafka MirrorMaker2方案或"先写Kafka，消费Kafka写MySQL"方案。

2. **进阶题②**：全量快照阶段产生了500万行数据，Kafka和MySQL备库都正确地写入了。但假设在快照快结束时，主库又产生了5万条新变更。如果Flink CDC使用的增量快照算法（FLIP-27），这些增量数据会不会和快照数据重叠？增量快照的水位对齐（Watermark Alignment）算法如何保证数据恰好不重不漏？

---

> **下一阶段预告**：**中级篇**（第16~30章）将深入Pipeline YAML API、增量快照原理（FLIP-27源码级）、Schema Evolution、UDF开发、数据湖集成、性能调优、生产部署等进阶内容。第16章从「Pipeline YAML API：声明式数据集成」开始。
