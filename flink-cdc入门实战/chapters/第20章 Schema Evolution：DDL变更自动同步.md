# 第20章 Schema Evolution：DDL变更自动同步

## 1 项目背景

### 业务场景：源表加了新列，下游系统崩了

某电商公司使用Flink CDC将MySQL订单数据同步到Elasticsearch。某天DBA执行了一个"看起来无害"的DDL：

```sql
ALTER TABLE orders ADD COLUMN discount DECIMAL(5,2) DEFAULT 0.00;
```

结果Flink CDC作业立即报错崩溃！原因是：Flink CDC还按旧的Schema解析Binlog事件，但新的Binlog事件中多了一个`discount`字段，反序列化失败。

更糟糕的是，该DDL前的数据已经写入ES，但DDL后的数据无法写入——ES索引中还没有`discount`字段。最终造成了数据断层：**截止时间点前的数据在ES中，之后的数据全丢了**。

**Schema Evolution（Schema演化）** 就是解决这类"源库表结构变更后，CDC作业如何自动适配"的问题。

### Schema变更类型

```
DDL类型                     影响                        处理难度
┌──────────────┐    ┌─────────────────────┐    ┌──────────────────┐
│ ADD COLUMN   │    │ 数据结构扩展       │    │ 低（只需新增列） │
├──────────────┤    ├─────────────────────┤    ├──────────────────┤
│ DROP COLUMN  │    │ 数据结构收缩       │    │ 中（可能丢数据） │
├──────────────┤    ├─────────────────────┤    ├──────────────────┤
│ MODIFY COLUMN│    │ 数据类型变更       │    │ 高（兼容性检查） │
├──────────────┤    ├─────────────────────┤    ├──────────────────┤
│ RENAME COLUMN│    │ 列名变更           │    │ 高（映射关系断） │
├──────────────┤    ├─────────────────────┤    ├──────────────────┤
│ DROP TABLE   │    │ 表删除             │    │ 中（下游需响应） │
└──────────────┘    └─────────────────────┘    └──────────────────┘
```

---

## 2 项目设计 · 三人交锋对话

**小胖**（后怕）：幸好我还没在生产里遇到这个坑！那Flink CDC到底怎么处理表结构变化的？难道每次改表都要重新启动作业？

**大师**：Flink CDC 3.x提供了5种DDL处理策略，通过`schema.change.behavior`配置：

```yaml
pipeline:
  schema.change.behavior: EVOLVE   # 可选: IGNORE / LENIENT / TRY_EVOLVE / EVOLVE / EXCEPTION
```

**逐个解释：**

**1. IGNORE（忽略）**——鸵鸟策略
DDL事件完全不处理，也不转发到下游。但数据会继续按旧Schema读取。
- 如果ADD COLUMN：新列的数据丢失
- 如果DROP COLUMN：DEBEZIUM用旧Schema解析新事件，可能崩溃
- **风险最高，不推荐**

**2. LENIENT（宽容）**——保险策略
DDL事件不处理，但允许数据中的新字段"通过"
- 遇到有额外字段的事件时，不会崩溃，只是忽略该字段
- **适合：确定下游不需要Schema变更的场景**

**3. TRY_EVOLVE（尝试演化）**——半自动
Flink CDC尝试将Schema变更应用到下游。如果下游不支持或失败，则继续运行但不应用
- **适合：下游Sink支持部分Schema变更的场景**

**4. EVOLVE（演化）**——全自动（推荐）
Flink CDC自动将DDL事件解析为`SchemaChangeEvent`（AddColumnEvent/DropColumnEvent等），通过SchemaOperator协调后转发到Sink。Sink（如Iceberg、Kafka）自动应用表结构变更
- **适合：大多数生产场景**

**5. EXCEPTION（异常）**——保守策略
遇到任何DDL变更都直接让作业失败。需要人工介入处理
- **适合：金融、合规等不允许自动结构变更的场景**

**小白**：那EVOLVE模式下，Schema变更事件是怎么从MySQL传递到Sink的？中间经历了什么处理？

**大师**：EVOLVE模式的完整链路如下：

```
MySQL: ALTER TABLE orders ADD COLUMN discount DECIMAL(5,2)
  │ Binlog记录DDL
  ▼
Debezium: 解析Binlog → 识别为DDL事件
  │
  ▼
MySqlSource: 发出SchemaChangeEvent（CreateTableEvent/AddColumnEvent等）
  │
  ▼
PreTransformOperator: DDL事件透传（不处理）
  │
  ▼
PostTransformOperator: DDL事件透传
  │
  ▼
SchemaOperator: 核心处理环节！
  ├── SchemaRegistry: 记录最新的表Schema
  ├── SchemaManager: 管理所有表的Schema版本
  ├── SchemaDerivator: 根据原始Schema + 变更事件 → 推导新Schema
  └── 转发SchemaChangeEvent到下游
  │
  ▼
DataSinkWriterOperator → MetadataApplier.applySchemaChange()
  ├── Kafka Sink: 更新Schema Registry
  ├── Iceberg Sink: 自动演进Table Schema
  └── MySQL Sink: 执行ALTER TABLE
```

**技术映射**：Schema Operator就像"房屋改造的总监理"——当业主（DBA）提出要"砸掉这堵墙（DROP COLUMN）"时，总监不直接施工，而是检查这堵墙是不是承重墙（兼容性检查），然后给施工队（Sink）下发改造图纸（SchemaChangeEvent）。

**小白**：那如果Sink不支持某些DDL呢？比如MySQL的InnoDB不允许DROP COLUMN时存在外键约束？

**大师**：这就是`TRY_EVOLVE`和`EVOLVE`的区别——`EVOLVE`不会检查Sink是否能成功应用变更，而是"发送变更指令，如果失败作业不会崩溃"（Sink的异常不会导致Source失败）。`TRY_EVOLVE`会更细致地检查兼容性。

但对于一些特殊的DDL（如`DROP TABLE`），建议不要使用`EVOLVE`自动同步——万一不小心`DROP TABLE orders`，下游的所有表也会被删除，这是一个连锁灾难。

这种情况下，建议使用`EXCEPTION`模式+人工审批流程：DBA执行DDL，Flink CDC作业进入FAILED状态，恢复前需要人工验证。

---

## 3 项目实战

### 分步实现

#### 步骤1：演示五种Schema变更行为

创建测试Pipeline YAML文件：

```yaml
# pipeline-evolution-demo.yaml
source:
  type: mysql
  hostname: localhost
  port: 3306
  username: cdc_user
  password: cdc_pass
  tables: shop.orders

sink:
  type: values                    # 控制台输出（便于观察）

pipeline:
  name: Schema Evolution Demo
  parallelism: 1
  schema.change.behavior: EVOLVE  # 切换此配置测试不同行为
```

**提交并执行DDL：**

```bash
# 1. 启动Pipeline
flink-cdc.sh pipeline-evolution-demo.yaml --use-mini-cluster

# 2. 在MySQL中执行ADD COLUMN
docker exec mysql-cdc mysql -uroot -proot123 -e "
USE shop;
ALTER TABLE orders ADD COLUMN discount DECIMAL(5,2) DEFAULT 0.00;
INSERT INTO orders (order_id, user_id, product, amount, status, discount) 
VALUES ('ORD_SCHEMA_TEST', 9999, 'Schema Test', 100.00, 'PAID', 10.00);
"
```

**EVOLVE模式下的输出：**
```
[INFO] SchemaChangeEvent received: AddColumnEvent{table=TableId(shop.orders), addedColumns=[discount DECIMAL(5,2)]}

[INFO] Applying schema change to sink: ADD COLUMN discount DECIMAL(5,2)

[INFO] DataChangeEvent: 
  +I {id=1002, order_id=ORD_SCHEMA_TEST, amount=100.00, status=PAID, discount=10.00}
  ↑ discount列被正确处理！
```

**EXCEPTION模式下的输出：**
```
[ERROR] Schema change detected but schema.change.behavior=EXCEPTION
[ERROR] Failing job due to DDL: ALTER TABLE shop.orders ADD COLUMN discount DECIMAL(5,2)
[ERROR] Job is FAILED. Manual intervention required.
```

#### 步骤2：处理不同DDL类型的SchemaChangeEvent

编写自定义Schema变更处理器：

```java
package com.example;

import org.apache.flink.cdc.common.event.*;
import org.apache.flink.cdc.common.types.DataType;
import org.apache.flink.streaming.api.functions.ProcessFunction;
import org.apache.flink.util.Collector;

/**
 * Schema变更事件分类处理器
 * 用于审计日志或自定义Schema变更响应
 */
public class SchemaChangeHandler extends ProcessFunction<Event, Event> {

    @Override
    public void processElement(Event event, Context ctx, Collector<Event> out) {
        if (event instanceof SchemaChangeEvent) {
            handleSchemaChange((SchemaChangeEvent) event);
            // 可选：转发DDL事件到下游
            // out.collect(event);
        } else {
            // 普通DataChangeEvent直接透传
            out.collect(event);
        }
    }

    private void handleSchemaChange(SchemaChangeEvent event) {
        if (event instanceof AddColumnEvent) {
            AddColumnEvent add = (AddColumnEvent) event;
            TableId tableId = add.tableId();
            for (AddColumnEvent.ColumnWithPosition col : add.getAddedColumns()) {
                String colName = col.getColumn().getName();
                DataType colType = col.getColumn().getType();
                System.out.printf("[EVOLVE] 表 %s 新增列: %s (%s)%n",
                    tableId.identifier(), colName, colType);
            }
        } else if (event instanceof DropColumnEvent) {
            DropColumnEvent drop = (DropColumnEvent) event;
            System.out.printf("[EVOLVE] 表 %s 删除列: %s%n",
                drop.tableId().identifier(),
                drop.getDroppedColumnNames());
        } else if (event instanceof AlterColumnTypeEvent) {
            AlterColumnTypeEvent alter = (AlterColumnTypeEvent) event;
            System.out.printf("[EVOLVE] 表 %s 修改列类型: %s%n",
                alter.tableId().identifier(),
                alter.getTypeChanges());
        } else if (event instanceof RenameColumnEvent) {
            RenameColumnEvent rename = (RenameColumnEvent) event;
            System.out.printf("[EVOLVE] 表 %s 重命名列: %s → %s%n",
                rename.tableId().identifier(),
                rename.getOldColumnName(),
                rename.getNewColumnName());
        } else if (event instanceof DropTableEvent) {
            System.out.println("[CRITICAL] 表被删除！请确认是否预期行为");
        } else if (event instanceof TruncateTableEvent) {
            System.out.println("[WARN] 表被清空！");
        }
    }
}
```

#### 步骤3：使用Pipeline YAML的Schema Evolution配置

```yaml
pipeline:
  name: Production CDC Pipeline
  parallelism: 2
  schema.change.behavior: EVOLVE

  # Schema变更行为补充配置
  schema-operator.rpc-timeout: 1 h          # Schema Operator RPC超时

  # Schema变更路由（可选）
  schema.change.route:
    - source-table: shop.orders
      # 只允许ADD COLUMN，其他DDL拒绝
      allowed-changes: ADD_COLUMN
    - source-table: shop.*
      allowed-changes: ADD_COLUMN, ALTER_COLUMN_TYPE

  # Schema变更告警（Event上报到Kafka）
  schema.change.alert.topic: schema_change_events
```

#### 步骤4：测试DDL兼容性问题

**场景：ALTER COLUMN类型——不兼容的变更**

```sql
-- 将VARCHAR列改为INT：从字符串到整数的转换
ALTER TABLE orders MODIFY COLUMN status INT;
```

**Flink CDC处理：**
```
ERROR: SchemaChangeEvent{AlterColumnTypeEvent: 
  table=TableId(shop.orders), 
  changes=[status: VARCHAR(32) → INT]}

WARN: Type change from VARCHAR to INT may cause data loss
      → 原始值'PAID'无法转换为INT
      → 该变更可能导致Binlog解析失败
```

#### 步骤5：Schema Evolution综合测试脚本

```bash
#!/bin/bash
# Schema Evolution 测试脚本

echo "=== 测试1: ADD COLUMN ==="
docker exec mysql-cdc mysql -uroot -proot123 -e "
  ALTER TABLE shop.orders ADD COLUMN test_col1 VARCHAR(64) DEFAULT 'test';
  INSERT INTO shop.orders (order_id, user_id, product, amount, status) 
  VALUES ('ORD_TEST_EVOLVE', 999, 'Test', 100, 'NEW');
"
sleep 2
echo "检查Flink输出是否包含test_col1"

echo "=== 测试2: DROP COLUMN ==="
docker exec mysql-cdc mysql -uroot -proot123 -e "
  ALTER TABLE shop.orders DROP COLUMN test_col1;
"
sleep 2
echo "检查作业是否正常运行"

echo "=== 测试3: MODIFY COLUMN (兼容) ==="
docker exec mysql-cdc mysql -uroot -proot123 -e "
  ALTER TABLE shop.orders MODIFY COLUMN amount DECIMAL(12,2);
"
sleep 2
echo "检查amount精度是否变化"

echo "=== 测试4: RENAME COLUMN ==="
docker exec mysql-cdc mysql -uroot -proot123 -e "
  ALTER TABLE shop.orders RENAME COLUMN internal_remark TO remark;
"
sleep 2
echo "检查作业是否报错"
```

#### 常见陷坑及解决方法

| 问题 | 原因 | 解决方法 |
|------|------|---------|
| ADD COLUMN时新列有NOT NULL约束但无默认值 | DBZ解析时无法为已有行提供该列值 | DDL中使用`DEFAULT`子句，或设置`ALTER TABLE ... ALTER COLUMN ... SET DEFAULT` |
| MODIFY COLUMN缩小字段长度导致数据截断 | 下游Sink无法应用缩小操作 | 使用`LENIENT`模式允许Sink失败继续运行，或手动处理截断 |
| DROP COLUMN后上游事件数据与下游Schema不匹配 | Sink已经被删除了列但上游还在发该列 | 确保DDL在Source和Sink间的传播顺序一致 |
| DDL事件未触发SchemaChangeEvent | 使用的Flink CDC版本不支持DDL捕获（2.x） | 升级到3.x，或在DataStream API中使用`includeSchemaChanges(true)` |
| Schema Operator RPC超时 | 集群负载高导致RPC通信延迟 | 增大`schema-operator.rpc-timeout` |

---

## 4 项目总结

### Schema变更处理模式对比

| 模式 | DDL传递 | Schema变更应用 | 适用场景 | 风险等级 |
|------|---------|---------------|---------|---------|
| IGNORE | ❌ | ❌ | 下游不关心Schema | 🔴高（数据可能丢失） |
| LENIENT | ❌ | ❌（忽略新字段） | 下游可接受字段缺失 | 🟡中 |
| TRY_EVOLVE | ✅（尝试） | ⚠️（尝试，失败可忽略） | 半自动化控制 | 🟡中 |
| **EVOLVE** | **✅** | **✅** | **推荐生产场景** | **🟢低** |
| EXCEPTION | ❌（作业失败） | ❌ | 金融/合规等严格场景 | 🟢低（需人工） |

### Schema变更兼容性矩阵

| DDL类型 | MySQL | Kafka | Iceberg | Paimon | Doris | ES |
|---------|-------|-------|---------|--------|-------|----|
| ADD COLUMN | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| DROP COLUMN | ✅ | ⚠️ | ✅ | ✅ | ❌ | ❌ |
| MODIFY COLUMN类型 | ✅ | ⚠️ | ✅ | ✅ | ⚠️ | ❌ |
| RENAME COLUMN | ✅ | ⚠️ | ⚠️ | ⚠️ | ❌ | ❌ |
| DROP TABLE | ✅ | ✅ | ✅ | ✅ | ❌ | ❌ |

### 生产建议

1. **默认使用EVOLVE**：大多数场景下，EVOLVE是最安全的选择。
2. **DDL审批流程**：对于DROP COLUMN、DROP TABLE等破坏性操作，设置审批流程，防止自动化同步导致的数据灾难。
3. **Schema兼容性测试**：在测试环境中先验证DDL对CDC作业的影响，再在生产中执行。
4. **监控Schema变更频率**：如果一天内DDL超过10次，说明Schema设计不稳定，需要和DBA团队沟通。

### 常见踩坑经验

**故障案例1：ALTER TABLE导致作业无限循环**
- **现象**：执行`ALTER TABLE orders ADD INDEX idx_status (status)`后，Flink CDC作业不断重启
- **根因**：DDL被Debezium正确解析，但Flink CDC误将`CREATE INDEX`当作SchemaChangeEvent处理，在应用变更时失败，触发Checkpoint恢复，恢复后又重新读到这个DDL事件，形成死循环
- **解决方案**：升级到Flink CDC 3.0.2+（修复了INDEX DDL误判），或配置`debezium.ddl.filter`排除非数据结构的DDL

**故障案例2：EVOLVE模式下ADD COLUMN后数据仍然为空**
- **现象**：新增列的DEFAULT值在Flink CDC中不生效，所有新行的该列值为null
- **根因**：MySQL的`DEFAULT`值是在MySQL端生成的，Binlog事件中不包含DEFAULT值。Debezium解析时，如果该列有NOT NULL+DEFAULT才会在事件中体现
- **解决方案**：在Binlog事件中，如果列没有NOT NULL约束，即使有DEFAULT值也不会出现在事件中。需要在Flink Transform中处理：`COALESCE(discount, 0.00)`或`IFNULL(discount, 0.00)`

**故障案例3：多个DDL并行执行导致Schema冲突**
- **现象**：DBA在MySQL上快速执行了3个DDL：
  ```sql
  ALTER TABLE orders ADD COLUMN a INT;
  ALTER TABLE orders ADD COLUMN b INT;
  ALTER TABLE orders ADD COLUMN c INT;
  ```
  Flink CDC作业报错"Schema version conflict"
- **根因**：三个DDL到达SchemaOperator的顺序可能因网络延迟而乱序，导致SchemaManager在应用第二个AddColumnEvent时发现当前的Schema版本与预期不一致
- **解决方案**：逐个执行DDL（间隔>1秒），或升级到支持DDL排序的Flink CDC版本

### 思考题

1. **进阶题①**：在Flink CDC的`SchemaOperator`中，`SchemaRegistry`和`SchemaManager`分别负责什么职责？当多个并行Source SubTask同时发出SchemaChangeEvent时，`SchemaOperator`如何保证Schema变更事件的处理顺序？提示：查看`SchemaCoordinator`的协调机制。

2. **进阶题②**：假设MySQL源表中执行了`ALTER TABLE orders DROP COLUMN discount`，但下游Kafka的Schema Registry中仍然保留了`discount`列。此时Binlog中订单数据不再包含`discount`字段，Flink CDC的`DebeziumDeserializationSchema`会如何处理？如果使用自定义的`DeserializationSchema`（解析为固定Java POJO），会报错还是静默忽略？

---

> **下一章预告**：第21章「高级数据转换：UDF与表达式编译」——在基础Projection/Filter之上，深入Flink CDC的UDF框架。你将学会编写自定义函数（脱敏、格式化、复杂计算），并理解Flink CDC如何通过Janino编译器将SQL表达式编译为Java字节码。
