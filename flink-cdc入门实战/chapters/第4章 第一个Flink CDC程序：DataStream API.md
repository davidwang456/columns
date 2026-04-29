# 第4章 第一个Flink CDC程序：DataStream API

## 1 项目背景

### 业务场景：订单实时监控大屏

你是公司电商平台的数据开发工程师，业务方（运营团队）提出了一个需求：**在订单监控大屏上实时显示每一笔新订单的信息**——包括下单用户、商品、金额、支付状态——数据延迟不超过1秒。

运营团队不仅要看当前订单，还需要看到每笔订单的**状态流转**（待支付 → 已支付 → 已发货 → 已完成），追踪关键指标的变化。更棘手的是，订单系统由6个微服务组成，订单数据分散在多个MySQL表中。

运营同学想得很简单："就在数据库旁边加个监控不就行了？数据变了大屏跟着变呗。"

可实际上，实现这个需求面临以下挑战：
1. 订单表每秒有数千次写入，轮询查询会压垮数据库
2. 订单状态流转需要捕捉每次UPDATE操作
3. 需要记录变更的"前值"和"后值"来追踪状态变化
4. 历史数据（存量订单）需要一次性全量加载

这些需求恰好是CDC的天然战场。而Flink CDC的DataStream API是上手最快的入口，你只需要几十行代码就能实现从MySQL Binlog实时抓取变更。

### 痛点放大

没有CDC技术时的备选方案：

| 方案 | 延迟 | 对数据库影响 | 复杂度 | 能否捕获DELETE |
|------|------|------------|--------|---------------|
| `SELECT * FROM orders WHERE update_time > ?` 轮询 | 秒~分钟级 | 极高（频繁扫描） | 低 | ❌（已被删除） |
| MySQL Trigger写入日志表 | 实时 | 中（触发器影响写入性能） | 中 | ✅ |
| 业务代码双写消息队列 | 实时 | 无 | 极高（每个业务模块都要改） | ✅ |
| Flink CDC (DataStream API) | 毫秒级 | 极低（模拟Slave读取Binlog） | 低 | ✅ |

### Flink CDC DataStream API架构

```
┌───────────────────────────────────────────────────────────┐
│                    Flink CDC作业 (JAR提交)                  │
│                                                           │
│  ┌──────────────────────────────────────────────────┐    │
│  │ MySqlSource<String> (FLIP-27 Source)              │    │
│  │  ├─ SnapshotSplitReader (全量快照并行读取)         │    │
│  │  └─ BinlogSplitReader (增量Binlog实时读取)         │    │
│  └──────────────┬───────────────────────────────────┘    │
│                 │ DataStream<Event>                       │
│                 ▼                                          │
│  ┌──────────────────────────────────────────────────┐    │
│  │ Transformation Pipeline                          │    │
│  │  ├─ map() → 解析Debezium事件                     │    │
│  │  ├─ filter() → 过滤特定操作类型                  │    │
│  │  └─ keyBy()/reduce() → 实时聚合                 │    │
│  └──────────────┬───────────────────────────────────┘    │
│                 │                                         │
│                 ▼                                         │
│  ┌──────────────────────────────────────────────────┐    │
│  │ Sink: print() / Kafka / JDBC / 自定义            │    │
│  └──────────────────────────────────────────────────┘    │
│                                                           │
│  Parallelism = 4（并行度）                                │
│  Checkpoint = 5s（容错）                                  │
└───────────────────────────────────────────────────────────┘
```

---

## 2 项目设计 · 三人交锋对话

**小胖**（兴奋地搓手）：终于要写代码了！第1章听的JobManager、TaskManager、Checkpoint这些概念都快忘了。我就想问一句——写个Flink CDC程序，到底要几行代码？

**大师**（笑）：你猜猜看？

**小胖**：我猜至少100行吧？要连接数据库、配置同步规则、写处理逻辑……怎么也得搞半天。

**大师**：那你可能要跌破眼镜了——**Flink CDC DataStream API的核心代码不超过30行**。真正让你花时间的不是写代码，而是理解这30行背后的东西：`MySqlSource`怎么配置的、`DebeziumDeserializationSchema`干了什么、全量快照和增量Binlog怎么切换的。

**小白**（推眼镜）：说到`MySqlSource`，我翻过它的源码——它实现的是FLIP-27的`Source`接口，支持并行读取。但我不理解的是：MySQL的Binlog是单线程的顺序流，Flink CDC怎么做到并行读取的？Binlog怎么分片？

**大师**：这是关键问题。Flink CDC的并行度体现在**全量快照阶段**，而不是增量流阶段。全过程分两个阶段：

**第一阶段——全量快照（Snapshot Phase），并行度为N：**
- `MySqlChunkSplitter`将大表按主键范围切分为N个Chunk（分片）
- N个Reader线程**并行**读取各自的Chunk，读取时记录当前Binlog位点
- 每个Chunk读取完成后记录一个"高水位标记"

**第二阶段——增量流（Stream Phase），并行度为1：**
- 所有Reader切换到单线程模式，读取Binlog
- 通过"水位对齐"算法，补上全量读取期间产生的增量数据
- 最终合并成时间一致的完整视图

**技术映射**：这就像搬家——全量快照阶段是"多个人同时搬不同的房间（并行）"，增量流阶段是"一个人留守等快递（单线程）"，水位对齐是"快递到了之后分拣到对应的房间"。

**小白**：那`DebeziumDeserializationSchema`呢？我看到有`JsonDebeziumDeserializationSchema`和自定义的Java对象反序列化，哪种更推荐？

**大师**：两种各有优劣：

| 方案 | 优势 | 劣势 |
|------|------|------|
| JSON格式 | 开箱即用、调试方便、Schema灵活 | 字符串解析有性能开销、类型信息丢失 |
| 自定义Java对象 | 类型安全、性能好、可直接用字段 | 需要写更多的映射代码 |

**推荐方案**：开始学习时用JSON格式，可以直观看到事件结构。生产环境用自定义Java对象，配合`RowData`或`DataChangeEvent`等结构化API。

**小胖**（举手）：我还有个问题！我如果先跑全量快照把几百万条历史数据读出来，然后又更新了一些数据，这些更新是在快照之后还是之前？会不会乱？

**大师**：这个问题问得好——这就是分布式系统里经典的**"快照一致性"**问题。Flink CDC的"水位对齐算法"正是为此设计：

1. 每个Chunk读完后，记录一个ChunkEnd事件（包含当前Binlog位点）
2. 所有Chunk都读完后，取所有ChunkEnd事件中**最小的Binlog位点**作为"水位线"（Watermark）
3. 增量流从该水位线开始读取Binlog，补上所有Chunk之间丢失的变更
4. 最终输出的数据集在时间上是**一致性快照**

这个过程不需要锁表，不需要`FLUSH TABLES WITH READ LOCK`。这是Flink CDC相比Canal、Maxwell等工具的核心优势。

---

## 3 项目实战

### 环境准备

**前置条件：**
- 第3章的Docker Compose环境已启动
- MySQL中已有shop.orders表
- Maven项目已配置flink-connector-mysql-cdc依赖

**Maven依赖（重点）：**
```xml
<dependencies>
    <!-- Flink Streaming API -->
    <dependency>
        <groupId>org.apache.flink</groupId>
        <artifactId>flink-streaming-java</artifactId>
        <version>1.20.3</version>
        <scope>provided</scope>
    </dependency>

    <!-- Flink CDC MySQL连接器（包含Debezium和所有传递依赖） -->
    <dependency>
        <groupId>org.apache.flink</groupId>
        <artifactId>flink-connector-mysql-cdc</artifactId>
        <version>3.0.0</version>
    </dependency>

    <!-- 如果使用Flink SQL方式，还需要Table API -->
    <dependency>
        <groupId>org.apache.flink</groupId>
        <artifactId>flink-table-api-java-bridge</artifactId>
        <version>1.20.3</version>
        <scope>provided</scope>
    </dependency>
</dependencies>
```

### 分步实现

#### 步骤1：创建MySQL测试表并准备数据

连接MySQL并创建测试表：

```sql
-- 进入MySQL容器
docker exec -it mysql-cdc mysql -uroot -proot123

-- 使用shop数据库
USE shop;

-- 创建订单表
CREATE TABLE orders (
    id          INT             NOT NULL AUTO_INCREMENT,
    order_id    VARCHAR(64)     NOT NULL COMMENT '订单号',
    user_id     INT             NOT NULL COMMENT '用户ID',
    product     VARCHAR(128)    NOT NULL COMMENT '商品名称',
    amount      DECIMAL(10,2)   NOT NULL COMMENT '金额',
    status      VARCHAR(32)     NOT NULL DEFAULT 'PENDING' COMMENT '状态:PENDING/PAID/SHIPPED/DONE',
    create_time TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP,
    update_time TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uk_order_id (order_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 插入初始数据（模拟存量订单）
INSERT INTO orders (order_id, user_id, product, amount, status) VALUES
('ORD20240101001', 1, 'iPhone 15', 6999.00, 'PAID'),
('ORD20240101002', 2, 'AirPods Pro', 1999.00, 'SHIPPED'),
('ORD20240101003', 3, 'MacBook Air', 8999.00, 'DONE');
```

#### 步骤2：编写第一个Flink CDC程序——JSON格式输出

新建 `FlinkCDCJsonDemo.java`：

```java
package com.example;

import org.apache.flink.api.common.eventtime.WatermarkStrategy;
import org.apache.flink.cdc.connectors.mysql.source.MySqlSource;
import org.apache.flink.cdc.connectors.mysql.table.StartupOptions;
import org.apache.flink.cdc.debezium.JsonDebeziumDeserializationSchema;
import org.apache.flink.streaming.api.datastream.DataStreamSource;
import org.apache.flink.streaming.api.environment.StreamExecutionEnvironment;

/**
 * 第一个Flink CDC程序 —— 使用DataStream API + JSON反序列化
 * 功能：从MySQL读取订单表的实时变更，打印到控制台
 *
 * 运行方式：
 *   IDEA中直接运行（需要provided依赖设为compile）
 *   或 mvn package → 提交到Flink集群
 */
public class FlinkCDCJsonDemo {

    public static void main(String[] args) throws Exception {
        // 1. 创建Flink流处理执行环境
        StreamExecutionEnvironment env = StreamExecutionEnvironment.getExecutionEnvironment();

        // 2. 开启Checkpoint，保证Exactly-Once语义
        // 间隔5秒，超时10分钟，保留最近3个Checkpoint
        env.enableCheckpointing(5000);
        env.getCheckpointConfig().setMinPauseBetweenCheckpoints(500);
        env.getCheckpointConfig().setCheckpointTimeout(600000);
        env.getCheckpointConfig().setMaxConcurrentCheckpoints(1);

        // 3. 构建MySQL CDC Source
        // 这是整个程序的核心——MySqlSource用于连接MySQL并读取Binlog变更
        MySqlSource<String> mySqlSource = MySqlSource.<String>builder()
            .hostname("localhost")         // MySQL主机地址
            .port(3306)                    // MySQL端口
            .databaseList("shop")          // 监控的数据库（支持正则）
            .tableList("shop.orders")      // 监控的表（格式: db.table）
            .username("cdc_user")          // CDC专用账号
            .password("cdc_pass")
            // 反序列化器：将Debezium ChangeEvent转为JSON字符串
            .deserializer(new JsonDebeziumDeserializationSchema())
            // 启动选项：initial = 先全量快照再增量流
            .startupOptions(StartupOptions.initial())
            .serverTimeZone("Asia/Shanghai")
            .build();

        // 4. 将Source接入Flink DataStream
        DataStreamSource<String> mysqlSource = env.fromSource(
            mySqlSource,
            WatermarkStrategy.noWatermarks(), // CDC数据不需要Watermark
            "MySQL CDC Source"
        );

        // 5. 打印到控制台（相当于Sink）
        // 生产环境中通常会替换为Kafka Sink或JDBC Sink
        mysqlSource.print();

        // 6. 提交执行
        env.execute("Flink CDC - Orders Monitor");
    }
}
```

#### 步骤3：运行并观察输出

在IDE中直接运行`main`方法（确保第3章的Docker环境已启动）。

**预期输出（全量快照阶段）：**

```json
{"before":null,"after":{"id":1,"order_id":"ORD20240101001","user_id":1,"product":"iPhone 15","amount":6999.00,"status":"PAID",...},"op":"r","source":{"db":"shop","table":"orders","snapshot":true,...},...}
{"before":null,"after":{"id":2,"order_id":"ORD20240101002","user_id":2,"product":"AirPods Pro","amount":1999.00,"status":"SHIPPED",...},"op":"r","source":{"db":"shop","table":"orders","snapshot":true,...},...}
{"before":null,"after":{"id":3,"order_id":"ORD20240101003","user_id":3,"product":"MacBook Air","amount":8999.00,"status":"DONE",...},"op":"r","source":{"db":"shop","table":"orders","snapshot":true,...},...}
```

注意 `op: "r"` 和 `source.snapshot: true` —— 这表示数据来自全量快照阶段（Snapshot Phase）。"r"代表"READ"。

**然后执行增量变更，观察输出**：

打开另一个终端，在MySQL中执行：
```sql
-- INSERT新订单
INSERT INTO orders (order_id, user_id, product, amount, status) VALUES
('ORD20240101004', 4, 'iPad Pro', 7999.00, 'PENDING');

-- UPDATE订单状态
UPDATE orders SET status = 'SHIPPED' WHERE order_id = 'ORD20240101001';
```

**预期输出（增量流阶段）：**

```json
{"before":null,"after":{"id":4,"order_id":"ORD20240101004",...,"status":"PENDING",...},"op":"c","source":{"db":"shop","table":"orders","snapshot":false,...},...}
{"before":{"id":1,...,"status":"PAID",...},"after":{"id":1,...,"status":"SHIPPED",...},"op":"u","source":{"db":"shop","table":"orders","snapshot":false,...},...}
```

注意 `op: "c"` (INSERT) 和 `op: "u"` (UPDATE)，且 `source.snapshot: false` —— 数据来自增量Binlog。

#### 步骤4：自定义反序列化——解析为结构化Java对象

JSON字符串在正式业务中不实用，我们可以实现`DebeziumDeserializationSchema`接口，将CDC事件解析为Java POJO：

```java
package com.example;

import org.apache.flink.api.common.typeinfo.TypeInformation;
import org.apache.flink.cdc.debezium.DebeziumDeserializationSchema;
import org.apache.flink.util.Collector;
import org.apache.kafka.connect.source.SourceRecord;
import org.apache.kafka.connect.data.Struct;

/**
 * 自定义CDC反序列化器：将Debezium的SourceRecord解析为OrderEvent对象
 */
public class OrderDebeziumDeserializer implements DebeziumDeserializationSchema<OrderEvent> {

    @Override
    public void deserialize(SourceRecord record, Collector<OrderEvent> out) {
        // 1. 获取事件中的value（Kafka Connect Struct格式）
        Struct value = (Struct) record.value();
        if (value == null) return; // DELETE事件的after为null

        // 2. 获取操作类型
        String op = value.getString("op");

        // 3. 获取after数据（变更后的行）
        Struct after = value.getStruct("after");
        if (after == null) {
            // DELETE操作时after为null，记录before供审计
            return;
        }

        // 4. 映射为Java POJO
        OrderEvent event = new OrderEvent();
        event.op = op;
        event.id = after.getInt32("id");
        event.orderId = after.getString("order_id");
        event.userId = after.getInt32("user_id");
        event.product = after.getString("product");
        event.amount = after.getFloat64("amount");
        event.status = after.getString("status");

        out.collect(event);
    }

    @Override
    public TypeInformation<OrderEvent> getProducedType() {
        return TypeInformation.of(OrderEvent.class);
    }
}

// POJO类（必须有默认构造器和public字段）
public class OrderEvent {
    public String op;
    public Integer id;
    public String orderId;
    public Integer userId;
    public String product;
    public Double amount;
    public String status;

    @Override
    public String toString() {
        return String.format("[%s] id=%d, order=%s, user=%d, product=%s, amount=%.2f, status=%s",
            op, id, orderId, userId, product, amount, status);
    }
}
```

**使用方式**：将`JsonDebeziumDeserializationSchema()`替换为`new OrderDebeziumDeserializer()`。

**预期输出：**
```
[c] id=4, order=ORD20240101004, user=4, product=iPad Pro, amount=7999.00, status=PENDING
[u] id=1, order=ORD20240101001, user=1, product=iPhone 15, amount=6999.00, status=SHIPPED
```

#### 步骤5：在Flink集群上运行（非IDE模式）

```bash
# 1. 打包（跳过测试）
mvn clean package -DskipTests

# 2. 将JAR包复制到Flink容器中
docker cp target/flink-cdc-demo-1.0-SNAPSHOT.jar flink-jm-cdc:/opt/flink/

# 3. 通过Flink Web UI提交（浏览器打开 http://localhost:8081）
# 点击"Submit New Job" → 选择JAR → 指定Entry Class → Submit

# 4. 或者通过CLI提交
docker exec flink-jm-cdc flink run \
  -d \
  -c com.example.FlinkCDCJsonDemo \
  /opt/flink/flink-cdc-demo-1.0-SNAPSHOT.jar
```

#### 常见陷坑及解决方法

| 问题 | 原因 | 解决方法 |
|------|------|---------|
| 运行后没有任何输出 | MySQL Binlog未开启或格式不是ROW | 检查`log_bin=ON`、`binlog_format=ROW` |
| `Caused by: com.mysql.cj.exceptions.CJException: Access denied` | CDC用户权限不足 | 执行授权SQL：`GRANT SELECT, RELOAD, ... ON *.* TO 'cdc_user'@'%'` |
| 快照阶段能读到数据，增量阶段无输出 | `startupOptions`配置为`initial`但无新写入 | 手动在MySQL中执行INSERT/UPDATE/DELETE |
| `java.lang.NoClassDefFoundError: io/debezium/connector/mysql/MySqlConnector` | 缺少Flink CDC JAR包依赖 | 检查`pom.xml`是否包含`flink-connector-mysql-cdc`依赖 |
| 全量快照完成后作业无响应 | Chunk切分遇到NULL值主键 | 升级到Flink CDC 3.0+，或确保主键列为NOT NULL |
| JSON字符串过长被截断 | print()默认输出截断 | 使用`printToErr()`或自定义Sink |

---

## 4 项目总结

### 优点 & 缺点

**Flink CDC DataStream API的优势：**
1. **类型安全**：通过自定义`DebeziumDeserializationSchema`获得强类型的Java对象
2. **灵活度高**：可以自由使用Flink的map/filter/keyBy/reduce/window等所有算子
3. **并行快照**：全量数据读取支持N路并发，处理大表比Canal等单线程方案快数倍
4. **Exactly-Once**：Checkpoint机制保证端到端数据不丢不重
5. **低侵入**：模拟MySQL Slave读取Binlog，源库零配置（只要开启Binlog）

**DataStream API的局限：**
1. **代码较多**：相比SQL API需要更多编码，反序列化、状态管理都需要手动实现
2. **Schema变更处理复杂**：DDL事件需要手动解析和转发
3. **调试困难**：反序列化错误通常需要在运行时观察，编译期难以发现

### API适用场景对比

| 对比维度 | DataStream API | SQL API | Pipeline YAML API |
|---------|---------------|---------|-------------------|
| **上手难度** | 中 | 低 | 低 |
| **灵活性** | 极高（任意算子组合） | 中（限于SQL语义） | 中（限于YAML定义） |
| **类型安全** | 是（Java POJO） | 否（RowData） | 否（Event泛型） |
| **Schema Evolution** | 需手动处理 | 自动（部分） | 自动 |
| **适合场景** | 复杂ETL、实时聚合 | 简单过滤、CDC转发 | 标准数据集成管道 |

### 注意事项

1. **StartupOptions选择**：开发时用`latest()`（只读增量），集成测试时用`initial()`（全量+增量），生产环境首次上线用`initial()`、恢复用`latest()`。
2. **并行度与server-id**：`MySqlSource`的并行度不能超过server-id范围大小。如果设置并行度=4，需要`server-id`配置为`5400-5403`（覆盖4个ID）。
3. **tableList格式**：必须是`db.table`的格式，表名大小写取决于MySQL的`lower_case_table_names`设置。
4. **Checkpoint目录**：生产环境必须配置外部状态后端（HDFS/S3），否则作业重启后状态丢失。

### 常见踩坑经验

**故障案例1：全量快照完成后作业报错"SplitReader failed"**
- **现象**：全量快照正常完成，切换到增量Binlog读取时报错
- **根因**：MySQL的`binlog_row_image`设置为`MINIMAL`（默认是FULL），导致UPDATE事件只记录被修改的列，而不是完整行记录
- **解决方案**：执行`SET GLOBAL binlog_row_image = FULL;`（需要SUPER权限），或在Docker Compose的my.cnf中配置

**故障案例2：增量数据延迟随时间越来越大**
- **现象**：Flink Web UI看到Source算子的"Current Fetch Delay"不断增长
- **根因**：Sink端写入速度跟不上，产生反压（Backpressure），Source被迫减速
- **解决方案**：
  1. 先定位反压源头：Sink还是中间算子？
  2. 增加Sink并行度，或优化Sink写入逻辑（批量写入代替逐条写入）
  3. 如果Sink无法优化，在Source和Sink之间加Kafka做缓冲

**故障案例3：Flink CDC作业在K8s上频繁重启（OOMKilled）**
- **现象**：TaskManager Pod被Kubernetes OOM Killer杀死，状态为OOMKilled
- **根因**：Flink CDC的全量快照阶段需要额外的堆内存来缓存Chunk数据，默认内存分配不足
- **解决方案**：
  1. 调小Chunk Size：`scan.incremental.snapshot.chunk.size=1000`（默认8096）
  2. 增加TaskManager内存：`taskmanager.memory.process.size: 4g`
  3. 开启RocksDB状态后端，减少堆内存压力

### 思考题

1. **进阶题①**：在第4章的代码中，如果MySqlSource的并行度设置为4，Flink CDC会创建4个Source sub-task。在全量快照阶段，4个task分别读取不同的Chunk。请问：在切换到增量流阶段后，几个task在运行？为什么？提示：查看源码中`HybridSplitAssigner`的实现逻辑。

2. **进阶题②**：自定义`OrderDebeziumDeserializer`解析`after`字段时，如果订单表新增了一个`discount`列，老的解析器会报错还是静默忽略？如何设计一个能够"自动兼容新增字段"的反序列化方案？

---

> **下一章预告**：第5章「玩转Flink CDC SQL」——如果说DataStream API是"灵活的手动挡"，那SQL API就是"省力的自动挡"。你将学会如何用5行SQL语句实现MySQL CDC，以及如何在Flink SQL CLI中交互式查询数据库的实时变更。
