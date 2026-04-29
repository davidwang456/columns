# 第34章 SchemaOperator与分布式协调

## 1 项目背景

### 业务场景：多并行度下的DDL同步一致性

第20章我们学习了Schema Evolution的5种模式，但有一个问题没有深入回答：**当Flink CDC作业的并行度=4时，一个DDL变更事件（如ALTER TABLE ADD COLUMN）如何被所有4个并行Subtask正确处理？**

如果DDL只在Subtask-0上处理了，但Subtask-1/2/3没有处理——那么后续的DML事件到了Subtask-1时，Schema不匹配，作业崩溃。

Flink CDC的`SchemaOperator`正是为了解决这个"分布式Schema一致性"问题。

### SchemaOperator在Pipeline中的位置

```
Source (并行度=4)
  ↓ DML事件 + DDL事件（所有Subtask都可能收到）
PreTransform (并行度=4)
  ↓
PostTransform (并行度=4)
  ↓
Partitioning (KeyBy TableId)
  ↓ 同表数据进入同一个Subtask
SchemaOperator (Coordination模式)
  ├── SchemaCoordinator (在Subtask-0上运行)
  │   ├── 接收所有Subtask发来的SchemaChangeEvent
  │   ├── 调用SchemaDerivator推导新Schema
  │   ├── 广播推导结果给所有Subtask
  │   └── 调用MetadataApplier下发DDL到Sink
  └── SchemaSubtask (在Subtask-0,-1,-2,-3上运行)
      ├── 转发DDL事件到Coordinator
      └── 接收Coordinator广播的结果更新本地Schema
```

---

## 2 项目设计 · 三人交锋对话

**小胖**（费解）：SchemaOperator有点复杂。为啥不是每个Subtask各自处理自己的DDL事件？这样就不需要Coordinator了。

**大师**：好问题！想象一下：并行度=4，Source的4个Subtask各自监控不同的Chunk。突然MySQL执行了`ALTER TABLE orders ADD COLUMN discount DECIMAL(5,2)`，这个DDL事件从Binlog中产生后——**它会发给哪个Subtask？**

答案是：**可能发给任意一个Subtask**（取决于DDL在Partitioning之前的随机分配）。如果DDL发给了Subtask-2，而Subtask-0/1/3不知道这个DDL，当新的Binlog事件包含`discount`字段到达Subtask-0时，Subtask-0的Schema还是旧的——它尝试解析新事件时就会崩溃。

**所以需要一个Coordinator来保证所有Subtask都收到并处理了DDL变更。**

**技术映射**：SchemaOperator的Coordinator像"项目晨会"——如果一个人（一个Subtask）知道了一个重要变更（DDL），他需要在晨会上告诉大家（广播Coordinator），然后Coordinator通知所有人（广播所有Subtask）。

**小白**：那Coordinator是怎么保证"所有Subtask都处理完了"才继续的？如果某个Subtask一直不响应怎么办？

**大师**：SchemaOperator使用**同步屏障（Barrier）**机制来保证一致性。流程如下：

```
1. DDL事件到达Subtask-2
2. Subtask-2 → 发送SchemaChangeRequest到Coordinator
3. Coordinator收到请求 → 调用SchemaDerivator推导新Schema
4. Coordinator → 发送Barrier到所有Subtask（"等待当前数据都处理完"）
5. 所有Subtask处理完Barrier前的事件 → 回复ACK
6. Coordinator确认所有Subtask都ACK了
7. Coordinator → 广播SchemaChangeResult（新Schema）到所有Subtask
8. 所有Subtask更新本地Schema → 回复Apply ACK
9. Coordinator确认所有Subtask都应用了新Schema
10. 继续处理后续DML事件
```

这确保了DDL前后的数据不会因为Schema不匹配而崩溃。

**小白**：那如果Sink不支持DDL（比如ES不支持RENAME COLUMN）呢？

**大师**：这时Coordinator会收到Sink的`MetadataApplier.applySchemaChange()`返回的失败信息。根据`schema.change.behavior`的配置：
- `EVOLVE`：忽略Sink失败，继续处理数据（新列写不进去但作业不崩）
- `TRY_EVOLVE`：尝试其他方式（如跳过该DDL）
- `EXCEPTION`：抛出异常，作业失败

---

## 3 项目实战

### 分步实现

#### 步骤1：阅读SchemaOperator源码

```java
// 源码路径:
// flink-cdc-runtime/src/main/java/org/apache/flink/cdc/runtime/operators/
//   schema/regular/SchemaOperator.java

public class SchemaOperator extends DataSinkWriterOperator {

    private SchemaCoordinator schemaCoordinator;

    @Override
    public void processElement(Event event) throws Exception {
        if (event instanceof SchemaChangeEvent) {
            // DDL事件 → 发给Coordinator处理
            processSchemaChange((SchemaChangeEvent) event);
        } else if (event instanceof DataChangeEvent) {
            // DML事件 → 使用当前Schema写入Sink
            processDataChange((DataChangeEvent) event);
        }
    }

    private void processSchemaChange(SchemaChangeEvent changeEvent) {
        // 1. 发送请求到Coordinator
        SchemaChangeRequest request = new SchemaChangeRequest(
            changeEvent.tableId(), changeEvent);
        schemaCoordinator.handleSchemaChange(request);
        
        // 2. 等待Coordinator返回结果
        //    （内部使用Barrier同步）
        SchemaChangeResult result = waitForResult(request.getRequestId());
        
        // 3. 根据结果更新本地Schema
        if (result.isSuccess()) {
            schemaRegistry.applySchemaChange(changeEvent);
        } else {
            handleFailedSchemaChange(result.getError());
        }
    }
}
```

#### 步骤2：阅读SchemaCoordinator的协调逻辑

```java
// 源码路径:
// flink-cdc-runtime/.../schema/regular/SchemaCoordinator.java

public class SchemaCoordinator {
    
    private final SchemaRegistry schemaRegistry;
    private final MetadataApplier metadataApplier;

    public SchemaChangeResult handleSchemaChange(SchemaChangeRequest request) {
        SchemaChangeEvent changeEvent = request.getSchemaChangeEvent();
        
        // 1. 推导新Schema（从旧Schema + DDL事件 → 新Schema）
        SchemaDerivator.Result deriveResult = SchemaDerivator.derive(
            schemaRegistry.getLatestSchema(request.getTableId()),
            changeEvent
        );
        
        if (deriveResult.hasConflict()) {
            // Schema冲突（如删除不存在的列）
            return SchemaChangeResult.failed(deriveResult.getConflictReason());
        }
        
        // 2. 获取新Schema
        Schema newSchema = deriveResult.getNewSchema();
        
        // 3. 应用DDL到Sink（通过MetadataApplier）
        try {
            metadataApplier.applySchemaChange(changeEvent);
        } catch (Exception e) {
            // Sink应用失败 → 根据behavior配置决定是否继续
            if (shouldFailOnSinkFailure()) {
                throw e;
            }
            // 否则，继续处理数据（新列可能在Sink不可见）
        }
        
        // 4. 更新Schema Registry
        schemaRegistry.registerSchema(request.getTableId(), newSchema);
        
        // 5. 广播新Schema给所有Subtask
        return SchemaChangeResult.success(newSchema);
    }
}
```

#### 步骤3：跟踪SchemaDerivator推导算法

```java
// 源码路径:
// flink-cdc-runtime/.../schema/regular/SchemaDerivator.java

public class SchemaDerivator {

    /**
     * 从旧Schema + SchemaChangeEvent推导新Schema
     * 
     * 例如:
     *   旧Schema: [id INT, name VARCHAR]
     *   DDL: ADD COLUMN age INT
     *   新Schema: [id INT, name VARCHAR, age INT]
     */
    public static Result derive(Schema oldSchema, SchemaChangeEvent changeEvent) {
        if (changeEvent instanceof AddColumnEvent) {
            // 新增列：在旧Schema末尾追加新列
            return deriveAddColumn(oldSchema, (AddColumnEvent) changeEvent);
        } else if (changeEvent instanceof DropColumnEvent) {
            // 删除列：从旧Schema中移除列
            return deriveDropColumn(oldSchema, (DropColumnEvent) changeEvent);
        } else if (changeEvent instanceof AlterColumnTypeEvent) {
            // 修改列类型：更新对应列的类型
            return deriveAlterColumnType(oldSchema, (AlterColumnTypeEvent) changeEvent);
        } else if (changeEvent instanceof RenameColumnEvent) {
            // 重命名列：更新列名
            return deriveRenameColumn(oldSchema, (RenameColumnEvent) changeEvent);
        } else if (changeEvent instanceof CreateTableEvent) {
            // 新建表：直接使用新Schema
            return deriveCreateTable((CreateTableEvent) changeEvent);
        } else if (changeEvent instanceof DropTableEvent) {
            // 删除表：清空Schema
            return deriveDropTable(oldSchema, (DropTableEvent) changeEvent);
        }
        throw new UnsupportedOperationException("Unsupported SchemaChangeEvent: "
            + changeEvent.getClass());
    }

    private static Result deriveAddColumn(Schema oldSchema, AddColumnEvent event) {
        Schema.Builder builder = Schema.newBuilder();
        // 1. 复制所有旧列
        for (Column oldColumn : oldSchema.getColumns()) {
            builder.column(oldColumn);
        }
        // 2. 追加新列
        for (ColumnWithPosition newColumn : event.getAddedColumns()) {
            builder.column(newColumn.getColumn().getName(), newColumn.getColumn().getType());
        }
        return Result.success(builder.build());
    }
}
```

#### 步骤4：配置SchemaOperator调试模式

```yaml
pipeline:
  name: Schema Evolution Demo
  schema.change.behavior: EVOLVE
  # 开启Schema Operator调试日志
  taskmanager.env:
    log.level: DEBUG
    logger.schema.name: org.apache.flink.cdc.runtime.operators.schema
```

**DEBUG日志示例：**
```
2024-01-15 10:00:00,123 [SchemaCoordinator] Received SchemaChangeRequest:
  table=shop.orders, event=AddColumn[discount DECIMAL(5,2)]

2024-01-15 10:00:00,124 [SchemaDerivator] Deriving new schema:
  Old schema: [id INT, order_id VARCHAR, ..., status VARCHAR]
  DDL: ADD COLUMN discount DECIMAL(5,2)
  New schema: [id INT, order_id VARCHAR, ..., status VARCHAR, discount DECIMAL(5,2)]

2024-01-15 10:00:00,125 [SchemaCoordinator] Broadcasting schema change to all subtasks...
2024-01-15 10:00:00,130 [SchemaCoordinator] All 4 subtasks acknowledged schema change
```

#### 常见陷坑及解决方法

| 问题 | 原因 | 解决方法 |
|------|------|---------|
| SchemaOperator Coordinator RPC超时 | 并行Subtask多，DDL处理慢 | 增加`schema-operator.rpc-timeout`配置 |
| SchemaDerivator推导失败（不支持的DDL） | 某些DDL类型未覆盖 | 设置`schema.change.behavior=LENIENT`允许忽略 |
| 广播后部分Subtask未ACK | 该Subtask正在处理大事务 | DDL在事务完成后才处理，等待时间较长 |
| DDL和DML乱序到达 | Binlog中DDL和DML的顺序不能保证 | SchemaOperator通过Barrier机制强制DDL在DML之前生效 |

---

## 4 项目总结

### SchemaOperator核心类图

```
SchemaOperator (DataSinkWriterOperator的子类)
│
├── SchemaCoordinator (在Subtask-0上)
│   ├── SchemaRegistry：存储所有表的最新Schema
│   ├── SchemaManager：管理Schema版本
│   ├── SchemaDerivator：推导新Schema
│   └── MetadataApplier：应用DDL到Sink
│
├── SchemaSubtask (所有Subtask)
│   ├── 本地Schema缓存（SchemaSnapshot）
│   └── DDL/DML事件处理
│
└── SchemaEvolutionClient (Sink侧)
    └── 接收Coordinator广播的Schema变更
```

### Schema变更的同步屏障机制

| 步骤 | 描述 | 作用 |
|------|------|------|
| 1. DDL到达 | SchemaChangeEvent到达某个Subtask | 触发Schema变更流程 |
| 2. 请求Coordinator | Subtask向Coordinator发送SchemaChangeRequest | 统一入口 |
| 3. 推导新Schema | SchemaDerivator计算新旧Schema差异 | 确定变更内容 |
| 4. 应用DDL到Sink | MetadataApplier调用Sink执行DDL | 同步到目标系统 |
| 5. 广播Barrier | Coordinator发送Barrier到所有Subtask | 对齐所有Subtask |
| 6. 应用新Schema | 所有Subtask更新本地Schema | 保证一致性 |
| 7. ACK回复 | 所有Subtask回复ACK | 确认完成 |

### 思考题

1. **进阶题①**：Flink CDC的SchemaOperator在Regular模式和Distributed模式下分别如何工作？Regular模式下Coordinator在Subtask-0上运行，如果Subtask-0故障，Coordinator怎么办？Hint：查看`SchemaOperator.translateRegular()`和`translateDistributed()`的源码差异。

2. **进阶题②**：`SchemaDerivator`在推导`AlterColumnTypeEvent`时，如果新类型与旧类型不兼容（如VARCHAR→INT且现有值无法转换），推导结果是什么？SchemaOperator会直接报错还是尝试某种类型的兼容转换？Hint：查看`DataTypeRoot`中的类型兼容性规则。

---

> **下一章预告**：第35章「Transform表达式编译与Janino」——深入Flink CDC的表达式编译引擎。剖析`TransformExpressionCompiler`如何将SQL表达式编译为Java字节码，`PreTransformOperator`/`PostTransformOperator`的执行链路。
