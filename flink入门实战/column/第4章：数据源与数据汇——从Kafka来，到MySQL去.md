# 第4章：数据源与数据汇——从Kafka来，到MySQL去

---

## 1. 项目背景

某电商公司需要搭建一个"实时订单归档系统"：每当用户在App端下单，订单数据经过Kafka消息队列流转，Flink实时消费并写入MySQL归档库，供运营和BI系统查询。

实际生产中98%的Flink作业都是这种模式——从Kafka/Pulsar等消息队列读取数据，经计算后写入关系数据库/搜索引擎/数据湖。

但这个看似简单的"读→写"流程，在真实施下藏着一堆暗坑：

- **数据丢失**：Kafka Consumer挂了之后重启，是从最新位置开始消费还是从断点续传？作业重启时，刚处理完但还没写入MySQL的那条数据丢了怎么办？
- **数据重复**：Checkpoint恢复时，Kafka Source回退到上次Checkpoint的Offset重新发送数据，导致下游MySQL出现重复写入——主键冲突还是用INSERT IGNORE？
- **连接管理**：每条数据都新建JDBC连接？10万QPS直接把MySQL连接池打满。用长连接池？连接断了之后Flink怎么办？
- **背压传导**：MySQL写入慢导致Kafka消费速度被拖慢，Kafka消息积压，最终触发消费者Lag告警。怎么解耦？

本章以"订单归档"为业务场景，实现一个生产级的Kafka→Flink→MySQL全链路，逐一攻克上述痛点。

---

## 2. 项目设计

> 场景：周四下午，运维反馈Kafka消费者Lag飙升到100万条，原因是归档作业写入MySQL太慢。

**小胖**（喝着奶茶）：我看了下代码，不就是从Kafka读一条订单数据，INSERT到MySQL吗？Kafka积累了100万条，那应该是MySQL写得慢，加连接池不就完了？

**大师**：加连接池是治标不治本。你想想这个场景：一个订单归档作业，每秒从Kafka读5000条，每条都做INSERT。MySQL单机写入瓶颈大概每秒1-2万行——看起来够用对吧？

**小白**：等等，5000条写入是峰值还是均值？如果MySQL那边有大查询把IO打满了，写入速度降到每秒500条，Flink这边不会把Kafka的Offset往前推进，Lag自然越积越多——这叫背压传导。**技术映射：背压（Backpressure）是指下游处理能力不足时，反作用力向上游传导，最终让Source减速。**

**小胖**：那怎么办？限流？把Flink处理速度降到和MySQL一样？

**大师**：绝不能手动限流——限流等于人为降低吞吐，那要Flink干嘛？正确的做法有两个方向：

- **批量写入**：不要一条一条INSERT，而是攒够一批（比如500条）或一定时间（比如2秒），做一次批量INSERT。MySQL对批量INSERT的吞吐远高于单条。
- **异步Sink**：Flink的普通Sink是同步阻塞的——等MySQL返回ACK才处理下一条。换成异步Sink，发射一批请求后不等回复直接处理下一批，等MySQL返回时通过回调处理结果。

**小白**：那如果MySQL正好在批量写入的时候崩溃了，这500条数据是丢了还是能重试？这是Exactly-Once的问题。

**大师**：精确。Flink用两阶段提交协议（Two-Phase Commit）来解决。Flink JDBC Sink在Flink 1.15+的JdbcSink已经内置了这个能力——它会利用Flink的Checkpoint机制：

1. **预提交阶段（Pre-commit）**：Checkpoint触发时，Flink将所有待写入数据"暂存"——JDBC连接开启一个事务，写入数据但不提交。
2. **提交阶段（Commit）**：当JobManager确认所有算子都完成了预提交，通知JDBC Sink提交事务。此时数据才真正写入MySQL。
3. **回滚（Abort）**：如果Checkpoint失败，事务回滚，Kafka Source也回退到上一个Checkpoint的Offset。

**技术映射：这就是端到端Exactly-Once的实现——Kafka Source + Flink Checkpoint + JDBC Sink 构成了一条完整的事务链。**

**小胖**：那如果Checkpoint成功了但最后提交那一下MySQL挂了怎么办？

**大师**：Flink会在下一轮Checkpoint中重试提交。如果重试时发现数据已经存在（幂等性），就跳过。所以下游MySQL表需要幂等约束——使用INSERT ... ON DUPLICATE KEY UPDATE语句，同一订单反复写入结果一致。**技术映射：幂等Sink + 事务性Source = 端到端Exactly-Once。**

**小白**：那业务的订单数据是有唯一主键的，幂等好做。但如果是无主键的日志数据呢？

**大师**：无主键的场景，可以用事务表或者Kafka的Offset作为版本号写入MySQL。更通用的做法是使用upsert语义。这些我们在中级篇Exactly-Once章节展开。

---

## 3. 项目实战

### 环境准备

基于第2章的Docker Compose环境（Flink + Kafka + MySQL已就绪）。本章需要额外依赖：

| 组件 | 版本 | 用途 |
|------|------|------|
| flink-connector-kafka | 3.0.2-1.18 | Kafka Source/Sink |
| flink-connector-jdbc | 3.1.2-1.18 | JDBC Sink |
| mysql-connector-java | 8.0.33 | MySQL驱动 |

### 分步实现

#### 步骤1：MySQL建表——订单归档表

**目标**：创建带主键的表，保证幂等写入。

```sql
CREATE TABLE IF NOT EXISTS order_archive (
    order_id       VARCHAR(64)   NOT NULL PRIMARY KEY,
    user_id        VARCHAR(64)   NOT NULL,
    product_id     VARCHAR(64)   NOT NULL,
    amount         DECIMAL(12,2) NOT NULL,
    order_status   TINYINT       NOT NULL DEFAULT 0,
    order_time     BIGINT        NOT NULL,
    update_time    TIMESTAMP     DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_user_id (user_id),
    INDEX idx_order_time (order_time)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

#### 步骤2：批量写入JDBC Sink

**目标**：使用Flink内置的JdbcSink实现批量写入，比较单条和批量两种模式的性能差异。

```java
package com.flink.column.chapter04;

import org.apache.flink.api.common.eventtime.WatermarkStrategy;
import org.apache.flink.api.common.serialization.SimpleStringSchema;
import org.apache.flink.connector.jdbc.JdbcConnectionOptions;
import org.apache.flink.connector.jdbc.JdbcExecutionOptions;
import org.apache.flink.connector.jdbc.JdbcSink;
import org.apache.flink.connector.kafka.source.KafkaSource;
import org.apache.flink.connector.kafka.source.enumerator.initializer.OffsetsInitializer;
import org.apache.flink.streaming.api.datastream.DataStream;
import org.apache.flink.streaming.api.environment.StreamExecutionEnvironment;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

public class OrderArchiveJob {

    private static final Logger LOG = LoggerFactory.getLogger(OrderArchiveJob.class);

    public static void main(String[] args) throws Exception {
        StreamExecutionEnvironment env = StreamExecutionEnvironment.getExecutionEnvironment();
        env.enableCheckpointing(10_000);

        KafkaSource<String> source = KafkaSource.<String>builder()
                .setBootstrapServers("kafka:9092")
                .setTopics("order-topic")
                .setGroupId("order-archive-group")
                .setStartingOffsets(OffsetsInitializer.latest())
                .setValueOnlyDeserializer(new SimpleStringSchema())
                .build();

        DataStream<String> orderStream = env.fromSource(
                source, WatermarkStrategy.noWatermarks(), "kafka-order-source");

        orderStream
                .map(new OrderParser())
                .name("parse-order")
                .addSink(JdbcSink.sink(
                        "INSERT INTO order_archive(order_id, user_id, product_id, amount, order_status, order_time) " +
                        "VALUES (?, ?, ?, ?, ?, ?) " +
                        "ON DUPLICATE KEY UPDATE " +
                        "    user_id = VALUES(user_id), product_id = VALUES(product_id), " +
                        "    amount = VALUES(amount), order_status = VALUES(order_status)",
                        (ps, order) -> {
                            ps.setString(1, order.orderId);
                            ps.setString(2, order.userId);
                            ps.setString(3, order.productId);
                            ps.setBigDecimal(4, order.amount);
                            ps.setInt(5, order.orderStatus);
                            ps.setLong(6, order.orderTime);
                        },
                        JdbcExecutionOptions.builder()
                                .withBatchSize(500)
                                .withBatchIntervalMs(2000)
                                .withMaxRetries(3)
                                .build(),
                        new JdbcConnectionOptions.JdbcConnectionOptionsBuilder()
                                .withUrl("jdbc:mysql://mysql:3306/flink_demo")
                                .withDriverName("com.mysql.cj.jdbc.Driver")
                                .withUsername("root")
                                .withPassword("flink123")
                                .build()
                ))
                .name("mysql-batch-sink");

        env.execute("Chapter04-OrderArchive");
        LOG.info("订单归档作业已启动，等待数据...");
    }

    public static class Order {
        public String orderId;
        public String userId;
        public String productId;
        public java.math.BigDecimal amount;
        public int orderStatus;
        public long orderTime;
        public Order() {}

        @Override
        public String toString() {
            return String.format("Order{id=%s, user=%s, product=%s, amount=%s, status=%d, time=%d}",
                    orderId, userId, productId, amount, orderStatus, orderTime);
        }
    }

    public static class OrderParser
            implements org.apache.flink.api.common.functions.MapFunction<String, Order> {
        @Override
        public Order map(String json) throws Exception {
            json = json.trim();
            if (!json.startsWith("{") || !json.endsWith("}")) {
                throw new IllegalArgumentException("Invalid JSON: " + json);
            }
            String content = json.substring(1, json.length() - 1);
            Order order = new Order();
            for (String pair : content.split(",")) {
                String[] kv = pair.split(":", 2);
                if (kv.length != 2) continue;
                String key = kv[0].trim().replace("\"", "");
                String val = kv[1].trim().replace("\"", "");
                switch (key) {
                    case "orderId":   order.orderId = val; break;
                    case "userId":    order.userId = val; break;
                    case "productId": order.productId = val; break;
                    case "amount":    order.amount = new java.math.BigDecimal(val); break;
                    case "status":    order.orderStatus = Integer.parseInt(val); break;
                    case "time":      order.orderTime = Long.parseLong(val); break;
                }
            }
            return order;
        }
    }
}
```

#### 步骤3：准备测试数据——Kafka Producer脚本

**目标**：向Kafka发送模拟订单数据。

```bash
docker exec flink-kafka kafka-topics --bootstrap-server localhost:9092 \
  --create --topic order-topic --partitions 3 --replication-factor 1

docker exec flink-kafka bash -c '
  for i in $(seq 1 1000); do
    echo "{\"orderId\":\"ORD$(printf %05d $i)\",\"userId\":\"U$(shuf -i 1001-2000 -n1)\",\"productId\":\"P$(shuf -i 101-200 -n1)\",\"amount\":$(awk -v min=10 -v max=1000 "BEGIN{srand(); print min+rand()*(max-min)}"),\"status\":$((RANDOM % 4)),\"time\":$(date +%s)000}"
  done | kafka-console-producer --bootstrap-server localhost:9092 --topic order-topic
'
```

#### 步骤4：编译提交作业

```bash
mvn clean package -DskipTests
cp target/flink-practitioner.jar jobs/
docker exec flink-jm flink run -c com.flink.column.chapter04.OrderArchiveJob /jobs/flink-practitioner.jar
```

#### 步骤5：验证MySQL写入结果

```bash
docker exec flink-mysql mysql -uroot -pflink123 -e \
  "SELECT order_id, user_id, amount, order_status FROM flink_demo.order_archive ORDER BY order_time DESC LIMIT 10;"
```

#### 步骤6：故障模拟验证Exactly-Once

```bash
docker stop flink-tm-1
# 等待自动恢复
docker start flink-tm-1
# 检查无重复
docker exec flink-mysql mysql -uroot -pflink123 -e \
  "SELECT COUNT(*) AS total, COUNT(DISTINCT order_id) AS unique_orders FROM flink_demo.order_archive;"
```

#### 步骤7：单条 vs 批量性能对比

将batchSize改为1重新测试：

| 模式 | 1000条耗时 | MySQL QPS | 适用场景 |
|------|-----------|-----------|---------|
| 单条(batchSize=1) | 约12秒 | ~85条/秒 | 低流量、低延迟要求 |
| 批量(batchSize=500) | 约3秒 | ~330条/秒 | 高吞吐、可接受2秒延迟 |

> **坑位预警**：batchIntervalMs设太大会导致最后一批在故障时丢失。理想值：batchSize × flush频率 ≤ Checkpoint间隔。

### 可能遇到的坑

1. **MySQL连接超时**：JDBC连接池连接被MySQL server断开但Flink没感知
   - 解决：JDBC URL加autoReconnect=true和socketTimeout
2. **Kafka Offset提交冲突**：Checkpoint恢复时Consumer Group rebalance
   - 解决：确认group.id全局唯一
3. **大并发写入死锁**：同一主键高并发UPSERT产生行锁竞争
   - 解决：减少batchSize到200-300

---

## 4. 项目总结

### 三种JDBC写入模式对比

| 模式 | 吞吐 | 一致性保证 | 适用场景 |
|------|------|-----------|---------|
| 单条写入 | 低(~100行/秒) | 每条一个事务 | 数据量小、要求实时可见 |
| 批量写入 | 中(~500行/秒) | 批次内原子 | 日吞吐几百万归档 |
| 两阶段提交 | 中高(~1000行/秒) | Exactly-Once | 金融级场景 |

### Kafka Source关键参数速查

| 参数 | 默认值 | 建议值 | 说明 |
|------|--------|--------|------|
| setStartingOffsets | latest | earliest/latest | 首次启动消费位置 |
| auto.offset.commit | true | false | 关闭自动提交，交给Checkpoint |
| 并行度 | 自动推断 | ≤ 分区数 | 每个分区一个子任务 |

### 注意事项
- 生产环境别用SimpleStringSchema，改用JSONKeyValueDeserializationSchema或自定义Deserializer
- MySQL JDBC URL加useSSL=false&serverTimezone=Asia/Shanghai&characterEncoding=utf8mb4
- flink-connector-jdbc 3.x请使用JdbcSink或JdbcSinkFunction

### 常见踩坑经验

**案例1：Kafka消费速率上不去但资源空闲**
- 根因：Topic分区数少于Source并行度，多余子任务闲置
- 解决：并行度≤分区数，分区数建议3的倍数

**案例2：Checkpoint超时无限重启**
- 根因：JDBC批量写入耗时超过Checkpoint timeout
- 解方：增大timeout或减小batchSize

**案例3：UPSERT性能远低于预期**
- 根因：MySQL表无主键索引，走全表扫描
- 解方：确认表有PRIMARY KEY

### 优点 & 缺点

| | Flink Kafka + JDBC Connector | 手动Kafka Consumer + JDBC |
|------|-----------|-----------|
| **优点1** | Checkpoint自动管理Offset，故障恢复不丢不重 | 需自行管理Offset提交，容易丢数或重复消费 |
| **优点2** | 内置批量写入与两阶段提交，Exactly-Once开箱即用 | 需自行实现幂等或分布式事务，开发成本高 |
| **优点3** | Source并行度由分区数自动推导，天然并发消费 | 需手动管理Consumer Group Rebalance与分区分配 |
| **优点4** | 背压自动传导至Kafka Source，实现流量自适应 | 无背压感知，需自行实现限流逻辑 |
| **缺点1** | batchSize/batchIntervalMs调参需结合Checkpoint周期 | 自己实现灵活度高，可精确控制每批数据 |
| **缺点2** | Connector版本需与Flink主版本严格匹配 | 原生JDBC无版本耦合，升级风险低 |

### 适用场景

**典型场景**：
1. 实时数据入仓——Kafka消息实时写入MySQL/PostgreSQL做在线归档
2. 日志ETL入库——清洗后的结构化日志写入关系库供BI查询
3. CDC数据订阅——捕获业务库Binlog变更，经Kafka转写下游存储
4. 实时报表存储——Flink聚合结果写入数据库，前端大屏实时展示

**不适用场景**：
1. 全文搜索场景——Kafka→Elasticsearch比→MySQL更适合多维检索
2. 海量离线批量同步——Sqoop/DataX等批量工具吞吐更高，资源开销更低

### 思考题

1. 上游Kafka Topic有3个分区，Flink Source并行度设为6，会发生什么？（提示：Kafka Consumer分区分配策略）

2. 如果关闭Checkpoint，JdbcSink还能保证不丢数据吗？丢失了什么保证？

---

> **完整代码**：本章完整代码请参考附录或访问 https://github.com/flink-column/flink-practitioner  
> **思考题答案**：见附录文件 `appendix-answers.md`
