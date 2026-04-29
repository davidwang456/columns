# 第7章 深入理解Event模型

## 1 项目背景

### 业务场景：审计日志系统需要区分DDL和DML事件

假设你是公司的数据平台开发者，合规部门要求：**所有对敏感数据表的变更操作都必须记录审计日志**，包括：
- 谁在什么时间修改了哪条数据（DML事件：INSERT/UPDATE/DELETE）
- 谁在什么时间修改了表结构（DDL事件：ADD COLUMN、ALTER COLUMN等）

公司有数百张业务表，你不能为每张表单独写处理逻辑。需要一个**统一的事件处理框架**——能够将所有数据变更和Schema变更事件统一建模、分类处理。

Flink CDC给每一条来自数据库的变更都建模为**事件（Event）**。理解了事件模型，就理解了Flink CDC的数据核心。

### Flink CDC事件体系架构

```
                    ┌────────────┐
                    │   Event    │ (接口)
                    └─────┬──────┘
                          │
            ┌─────────────┴─────────────┐
            │                           │
    ┌───────┴───────┐          ┌────────┴────────┐
    │ DataChangeEvent│          │SchemaChangeEvent│
    │ (数据变更事件)  │          │ (Schema变更事件) │
    └───────┬───────┘          └────────┬────────┘
            │                           │
    ┌───────┴─────────┐      ┌──────────┴──────────┐
    │ OperationType   │      │                     │
    │ ├─ INSERT       │      │ CreateTableEvent    │
    │ ├─ UPDATE       │      │ DropTableEvent      │
    │ ├─ DELETE       │      │ AddColumnEvent      │
    │ └─ REPLACE      │      │ DropColumnEvent     │
    │                  │      │ RenameColumnEvent   │
    │ + before/after   │      │ AlterColumnTypeEvent│
    │ + TableId        │      │ AlterTableComment   │
    │ + source metadata│      │ TruncateTableEvent  │
    └──────────────────┘      └─────────────────────┘
```

---

## 2 项目设计 · 三人交锋对话

**小胖**（翻着文档）：Event模型……我就想知道，Flink CDC从MySQL读出来的东西，到我手上时到底是什么格式？JSON？对象？还是啥？

**大师**：这是个根本问题。Flink CDC目前提供了**三层事件抽象**：

**第一层——底层：Debezium SourceRecord**（Kafka Connect格式）
最底层的事件格式，`Struct`对象。就是你在第2章看到的那些JSON事件的Java表示。

**第二层——Flink CDC专用Event接口**（`org.apache.flink.cdc.common.event`）
Flink CDC 3.x引入了自己的事件模型，包括`DataChangeEvent`和`SchemaChangeEvent`。这一层把Debezium的通用格式转换成了Flink CDC专用的、类型安全的事件对象。

**第三层——用户层**（`DebeziumDeserializationSchema`或自定义）
最终用户通过反序列化器看到的格式——JSON字符串或自定义Java POJO。

**小胖**：那我应该关注哪一层？

**大师**：对于日常开发，你只需要关注第三层——通过`DebeziumDeserializationSchema`定义你想要的输出格式。但如果你想做：
- 自定义Schema Evolution处理
- 基于事件类型的分流
- 审计日志系统（区分DDL和DML）

那你必须理解第二层的`DataChangeEvent`和`SchemaChangeEvent`。

**小白**（翻开源码）：我看了`DataChangeEvent`的源码，它里面包含`tableId`、`operationType`、`before`和`after`四个核心字段。`before`和`after`是`RecordData`类型。但`SchemaChangeEvent`有一堆子类——`CreateTableEvent`、`AddColumnEvent`、`DropColumnEvent`……为什么要设计这么多子类？

**大师**：因为DDL的操作类型比DML复杂得多。一个`INSERT`事件只需要说"哪个表、哪行数据、什么值"。但一个`ALTER TABLE ADD COLUMN`事件需要告诉系统：
1. 哪个表被修改了（`TableId`）
2. 新增了什么列（列名、数据类型、默认值、是否可为NULL）
3. 新增列在表中的位置

不同类型的DDL需要不同的结构化信息。如果用统一的结构来表示，要么丢失信息（信息量不足），要么字段太多太复杂（90%的字段为null）。

**技术映射**：这就像快递系统的"包裹事件"——普通包裹（DataChangeEvent）只需要"发货地、目的地、物品清单"。而"海关申报单"（SchemaChangeEvent）则需要"物品材质、原产地、估价、HS编码"等不同信息——不同类型的包裹有不同的信息需求。

**小白**：那`TableId`呢？我看到它由`namespace`、`schemaName`、`tableName`三部分组成。`namespace`是"库名"的意思吗？为什么不用`databaseName`？

**大师**：`TableId`的设计考虑了多种数据库的命名空间差异：

| 数据库 | namespace | schemaName | tableName |
|-------|-----------|-----------|-----------|
| MySQL | `default` | `shop` | `orders` |
| PostgreSQL | `default` | `public` | `orders` |
| Oracle | `default` | `SCOTT` | `EMP` |
| SQL Server | `default` | `dbo` | `orders` |

MySQL的`database`概念对应`TableId`的`schemaName`，`namespace`用于支持未来多命名空间的场景。这种设计保证了Flink CDC在多数据库之间切换时，`TableId`的统一性。

---

## 3 项目实战

### 环境准备

使用第3章的Docker环境，添加对Flink CDC event包的依赖。

**Maven依赖：**
```xml
<dependency>
    <groupId>org.apache.flink</groupId>
    <artifactId>flink-cdc-common</artifactId>
    <version>3.0.0</version>
</dependency>
```

### 分步实现

#### 步骤1：创建带多种数据类型的测试表

```sql
USE shop;

CREATE TABLE product_catalog (
    id          INT             NOT NULL AUTO_INCREMENT,
    name        VARCHAR(128)    NOT NULL COMMENT '商品名',
    category    VARCHAR(64)     DEFAULT NULL COMMENT '分类',
    price       DECIMAL(10,2)   NOT NULL COMMENT '价格',
    stock       INT             NOT NULL DEFAULT 0 COMMENT '库存',
    description TEXT            COMMENT '描述',
    is_active   TINYINT(1)      DEFAULT 1 COMMENT '是否上架',
    create_time TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

INSERT INTO product_catalog VALUES
(1, 'iPhone 15', 'Phone', 6999.00, 100, '最新款智能手机', 1, NOW()),
(2, 'MacBook Air', 'Laptop', 8999.00, 50, '轻薄笔记本', 1, NOW());
```

#### 步骤2：解析DataChangeEvent结构

编写一个反序列化器，将Debezium的SourceRecord解析为结构化对象：

```java
package com.example;

import org.apache.flink.api.common.typeinfo.TypeInformation;
import org.apache.flink.cdc.common.event.DataChangeEvent;
import org.apache.flink.cdc.common.event.TableId;
import org.apache.flink.cdc.common.types.DataType;
import org.apache.flink.cdc.common.types.DataTypes;
import org.apache.flink.cdc.common.data.RecordData;
import org.apache.flink.cdc.common.data.GenericRecordData;
import org.apache.flink.cdc.debezium.DebeziumDeserializationSchema;
import org.apache.flink.util.Collector;
import org.apache.kafka.connect.source.SourceRecord;
import org.apache.kafka.connect.data.Struct;
import org.apache.kafka.connect.data.Field;

/**
 * 将CDC事件解析为DataChangeEvent + SchemaChangeEvent
 * 演示Flink CDC事件模型的内部结构
 */
public class EventDebugDeserializer implements DebeziumDeserializationSchema<String> {

    @Override
    public void deserialize(SourceRecord record, Collector<String> out) {
        StringBuilder sb = new StringBuilder();

        // 1. 提取事件中的value（Kafka Connect Struct）
        Struct value = (Struct) record.value();
        if (value == null) {
            out.collect("[TOMBSTONE] 空事件（DELETE后的墓碑消息）");
            return;
        }

        // 2. 获取操作类型
        String op = value.getString("op"); // c=CREATE, u=UPDATE, d=DELETE, r=READ
        String opDesc;
        switch (op) {
            case "c": opDesc = "CREATE (INSERT)"; break;
            case "u": opDesc = "UPDATE"; break;
            case "d": opDesc = "DELETE"; break;
            case "r": opDesc = "READ (快照)"; break;
            default:  opDesc = "UNKNOWN: " + op;
        }
        sb.append("=== 事件类型: ").append(opDesc).append(" ===\n");

        // 3. 获取Source元信息
        Struct source = value.getStruct("source");
        sb.append("数据库: ").append(source.getString("db")).append("\n");
        sb.append("表名: ").append(source.getString("table")).append("\n");
        sb.append("Binlog位置: ").append(source.getString("file"))
          .append(":").append(source.getInt64("pos")).append("\n");
        sb.append("GTID: ").append(source.getString("gtid")).append("\n");
        sb.append("是否快照: ").append(source.getBoolean("snapshot")).append("\n");
        sb.append("事件时间戳: ").append(value.getInt64("ts_ms")).append("ms\n");

        // 4. 提取before和after数据
        Struct before = value.getStruct("before");
        Struct after = value.getStruct("after");

        if (before != null) {
            sb.append("--- BEFORE (修改前) ---\n");
            for (Field field : before.schema().fields()) {
                sb.append("  ").append(field.name()).append(": ")
                  .append(before.get(field)).append("\n");
            }
        }

        if (after != null) {
            sb.append("--- AFTER (修改后) ---\n");
            for (Field field : after.schema().fields()) {
                sb.append("  ").append(field.name()).append(": ")
                  .append(after.get(field)).append("\n");
            }
        }

        // 5. 如果是DDL事件（SchemaChangeEvent）
        // 注意：DDL事件通过SchemaChangeEvent结构表达，本示例简化处理
        if ("c".equals(op) || "u".equals(op)) {
            sb.append("--- 变更摘要 ---\n");
            sb.append("操作: ").append(opDesc).append("\n");
            if (before != null && after != null) {
                // 对比前后差异
                for (Field field : before.schema().fields()) {
                    Object beforeVal = before.get(field);
                    Object afterVal = after.get(field);
                    if (!java.util.Objects.equals(beforeVal, afterVal)) {
                        sb.append("  列 ").append(field.name())
                          .append(": ").append(beforeVal)
                          .append(" → ").append(afterVal).append("\n");
                    }
                }
            }
        }

        out.collect(sb.toString());
    }

    @Override
    public TypeInformation<String> getProducedType() {
        return TypeInformation.of(String.class);
    }
}
```

**使用方式：**
```java
MySqlSource<String> source = MySqlSource.<String>builder()
    .deserializer(new EventDebugDeserializer())
    // ... 其他配置
    .build();
```

**MySQL执行变更后的预期输出：**

```
=== 事件类型: CREATE (INSERT) ===
数据库: shop
表名: orders
Binlog位置: mysql-bin.000003:12345
GTID: a2b3c4d5-e6f7:48
是否快照: false
事件时间戳: 1714377601000ms
--- AFTER (修改后) ---
  id: 1002
  order_id: ORD20240101005
  user_id: 5
  product: Apple Watch
  amount: 3299.00
  status: PENDING
--- 变更摘要 ---
操作: CREATE (INSERT)
```

```
=== 事件类型: UPDATE ===
--- BEFORE (修改前) ---
  id: 1002
  status: PENDING
--- AFTER (修改后) ---
  id: 1002
  status: PAID
--- 变更摘要 ---
操作: UPDATE
  列 status: PENDING → PAID
```

#### 步骤3：使用Flink CDC Event API处理Schema变更

```java
package com.example;

import org.apache.flink.cdc.common.event.*;

/**
 * 演示如何判断事件类型并进行分类处理
 * 适用于审计日志、多路转发等场景
 */
public class EventClassifier {

    /**
     * 根据事件类型进行分类处理
     */
    public static String classify(Event event) {
        if (event instanceof DataChangeEvent) {
            DataChangeEvent dataEvent = (DataChangeEvent) event;
            TableId tableId = dataEvent.tableId();
            OperationType opType = dataEvent.op();

            // 按操作类型分流
            switch (opType) {
                case INSERT:
                    return String.format("[DML-INSERT] 表 %s 新增数据",
                        tableId.identifier());
                case UPDATE:
                    return String.format("[DML-UPDATE] 表 %s 更新数据",
                        tableId.identifier());
                case DELETE:
                    return String.format("[DML-DELETE] 表 %s 删除数据",
                        tableId.identifier());
                case REPLACE:
                    return String.format("[DML-REPLACE] 表 %s 替换数据",
                        tableId.identifier());
                default:
                    return "[DML-UNKNOWN] 未知操作类型";
            }

        } else if (event instanceof SchemaChangeEvent) {
            // Schema变更事件有多种子类型
            SchemaChangeEvent schemaEvent = (SchemaChangeEvent) event;
            if (event instanceof CreateTableEvent) {
                return "[DDL-CREATE] 创建了新表";
            } else if (event instanceof AddColumnEvent) {
                AddColumnEvent addEvent = (AddColumnEvent) event;
                return String.format("[DDL-ADD-COLUMN] 表 %s 新增 %d 列",
                    addEvent.tableId().identifier(),
                    addEvent.getAddedColumns().size());
            } else if (event instanceof DropColumnEvent) {
                return "[DDL-DROP-COLUMN] 删除了列";
            } else if (event instanceof AlterColumnTypeEvent) {
                return "[DDL-ALTER-COLUMN] 修改列类型";
            } else if (event instanceof RenameColumnEvent) {
                return "[DDL-RENAME] 重命名列";
            } else if (event instanceof DropTableEvent) {
                return "[DDL-DROP-TABLE] 删除了表";
            } else if (event instanceof TruncateTableEvent) {
                return "[DDL-TRUNCATE] 清空了表";
            } else {
                return "[DDL-UNKNOWN] 未知Schema变更";
            }
        } else {
            return "[UNKNOWN] 未知事件类型";
        }
    }
}
```

#### 步骤4：实战——构建审计日志流

将CDC事件按类型分流为DML和DDL两个流，分别处理后输出：

```java
package com.example;

import org.apache.flink.streaming.api.datastream.DataStream;
import org.apache.flink.streaming.api.environment.StreamExecutionEnvironment;
import org.apache.flink.cdc.connectors.mysql.source.MySqlSource;
import org.apache.flink.cdc.connectors.mysql.table.StartupOptions;
import org.apache.flink.cdc.debezium.JsonDebeziumDeserializationSchema;

/**
 * 审计日志系统：将DML和DDL事件分流处理
 * DML事件 → 审计日志（记录数据变更）
 * DDL事件 → Schema变更历史（记录表结构变更）
 */
public class AuditLogPipeline {

    public static void main(String[] args) throws Exception {
        StreamExecutionEnvironment env = StreamExecutionEnvironment.getExecutionEnvironment();
        env.enableCheckpointing(5000);

        MySqlSource<String> source = MySqlSource.<String>builder()
            .hostname("localhost").port(3306)
            .databaseList("shop")
            .tableList("shop.orders", "shop.product_catalog")
            .username("cdc_user").password("cdc_pass")
            .deserializer(new JsonDebeziumDeserializationSchema())
            .startupOptions(StartupOptions.latest())
            .build();

        DataStream<String> cdcStream = env.fromSource(source,
            org.apache.flink.api.common.eventtime.WatermarkStrategy.noWatermarks(),
            "MySQL CDC Source");

        // 分流：根据JSON中的op字段判断操作类型
        DataStream<String> auditLog = cdcStream.filter(
            json -> json.contains("\"op\":\"c\"")  // INSERT（包含全量快照的READ）
                || json.contains("\"op\":\"u\"")    // UPDATE
                || json.contains("\"op\":\"d\"")    // DELETE
        );

        DataStream<String> schemaHistory = cdcStream.filter(
            json -> json.contains("\"op\":\"c\"")    // DDL也用op=c表示
        );

        // 审计日志写入审计专用Kafka Topic
        auditLog.print("AUDIT>> ");

        // Schema变更记录到单独的日志
        schemaHistory.print("SCHEMA>> ");

        env.execute("Audit Log Pipeline");
    }
}
```

#### 常见陷坑及解决方法

| 问题 | 原因 | 解决方法 |
|------|------|---------|
| `before`字段在INSERT事件中为null | INSERT前没有数据，这是正常行为 | 处理时判断是否为null |
| `after`字段在DELETE事件中为null | DELETE后的数据不存在，正常行为 | 处理时做null检查 |
| DDL事件在DataStream API中捕获不到 | 默认`JsonDebeziumDeserializationSchema`只处理DML | 使用`DebeziumDeserializationSchema`的`includeSchemaChanges`配置 |
| `Struct`类型转换失败`ClassCastException` | 事件格式非预期（可能是tombstone） | 执行`value instanceof Struct`类型检查 |

---

## 4 项目总结

### DataChangeEvent vs SchemaChangeEvent对比

| 维度 | DataChangeEvent | SchemaChangeEvent |
|------|----------------|-------------------|
| **触发时机** | INSERT/UPDATE/DELETE | CREATE/ALTER/DROP TABLE |
| **包含数据** | 行的before和after值 | 列定义、数据类型、约束 |
| **对下游影响** | 修改数据内容 | 修改数据结构 |
| **处理频率** | 高（每秒成百上千次） | 低（偶尔DDL操作） |
| **典型处理** | 写入Kafka/数据湖 | 更新Schema Registry |
| **Flink CDC类型** | `DataChangeEvent` | `CreateTableEvent`, `AddColumnEvent`等 |

### 事件处理最佳实践

1. **始终处理null**：`before`和`after`都可能为null，代码中必须做null保护
2. **区分Snapshot和Stream**：通过`source.snapshot`字段判断是来自全量快照还是增量Binlog，两者处理方式可能不同
3. **DDL先于DML**：如果接收到`AddColumnEvent`，后续的`DataChangeEvent`已经包含新列数据。需要保证DDL先被处理
4. **TableId作为路由键**：在多表同步场景中，使用`TableId`作为路由key保证同一张表的事件进入同一个处理单元

### 常见踩坑经验

**故障案例1：DELETE事件触发UPDATE处理逻辑**
- **现象**：DELETE数据被错误地当作UPDATE更新到目标库
- **根因**：代码中只判断了`after`不为null就当作UPDATE，但DELETE事件只有`before`，`after`为null
- **解决方案**：必须检查`op`字段区分操作类型，不能仅凭`after != null`判断

**故障案例2：全量快照的READ事件包含了变更后的数据**
- **现象**：`op=r`（READ）的事件中`after`包含当前快照的值，`before`为null。如果当作INSERT处理，会和增量INSERT混淆
- **根因**：`r`=READ（快照读取），`c`=CREATE（增量INSERT），两者数据格式完全一样，但来源不同
- **解决方案**：根据`source.snapshot`区分快照和增量事件。快照数据需要去重逻辑，增量数据可直接写入

**故障案例3：SchemaChangeEvent在DataStream API中丢失**
- **现象**：执行`ALTER TABLE`后，Flink CDC继续输出DML事件但没有输出DDL事件
- **根因**：`JsonDebeziumDeserializationSchema`默认只输出DML事件，忽略DDL
- **解决方案**：将`includeSchemaChanges`设置为true，或使用`DataChangeEvent`/`SchemaChangeEvent`驱动的自定义Deserializer

### 思考题

1. **进阶题①**：假设需要实现一个"CDC事件重放系统"——能够将过去7天的Binlog事件重新发送到一个新的Kafka Topic。你认为应该使用`DataChangeEvent`还是`SourceRecord`作为重放的最小单元？两者的序列化、反序列化有何不同？

2. **进阶题②**：`SchemaChangeEvent`的多个子类（`AddColumnEvent`, `AlterColumnTypeEvent`等）在Flink CDC 3.x中是如何被序列化到Operator之间传输的？提示：查看`flink-cdc-runtime`模块中的`SchemaChangeEventSerializer`实现。

---

> **下一章预告**：第8章「Flink CDC数据源配置大全」——从MySQL扩展到PostgreSQL、Oracle、MongoDB、SQL Server，你将学会每种数据源的特有配置参数、常见陷阱和最佳实践。
