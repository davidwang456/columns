# 第27章：Savepoint——作业不停机升级与状态迁移

---

## 1. 项目背景

某实时数仓Flink作业每天处理50亿条数据，状态大小500GB。一次业务逻辑变更，需要更新Flink作业——但停机意味着数据断层，老的状态与新逻辑不兼容。

挑战在于：如何在**不停机**的情况下，完成代码升级并保留已有状态？

手动操作流程是：
1. 触发Savepoint（停机保存状态）
2. 部署新版本代码
3. 从Savepoint恢复

但这里有很多细节陷阱：
- 如果新的State结构与旧的兼容吗？`ValueState<Integer>` 改为 `ValueState<Long>` 能恢复吗？
- 如果新增了一个State，旧Savepoint中没有这个State会怎样？
- 如果启用了新的Operator或删除了旧Operator，状态如何映射？
- 如果升级包含不可逆的Schema变更（如合并两个State为一个），怎么办？

---

## 2. 项目设计

> 场景：小胖需要升级订单归档作业——增加新的聚合维度，但不想停机。

**小胖**：我用Savepoint停了作业，改了代码，从Savepoint恢复——结果报错了："State migration failed: Cannot restore new state from old state"。

**大师**：你改了State的类型？比如之前存的OrderState的字段变了？

**小胖**：对，我给OrderState加了一个新的字段"promotionId"。

**技术映射：Flink Savepoint状态迁移有两种方式——① 自动兼容（字段新增/删除/类型兼容）② 手动Compatibility（通过StateMigrationLambda或自定义序列化器）。简单的情况（新增字段）Flink可以自动处理，但复杂变更需要手动介入。**

**小白**：那Savepoint和Checkpoint到底是什么关系？我看WebUI上它们在一个页面。

**大师**：底层的快照格式完全一样。区别在于：

- **Checkpoint**：自动周期触发，Flink自动管理生命周期（自动清理旧的）
- **Savepoint**：手动触发，持久保留（不会自动删除），用于版本升级

**技术映射：Savepoint = 带有业务语义的Checkpoint。两者底层都是相同的"状态快照"，但Savepoint有用户指定的路径和保存策略。**

**小胖**：不停机升级到底怎么做？我每次都要停作业。

**大师**：Flink 1.15+支持**State Processor API + 不停机Savepoint**。操作流程是：

1. 新启一个版本的Flink作业（同一个Source + 新的Sink，或不同的消费者组Group）
2. 在Source端从旧作业的Kafka Offset + 快照位置开始消费
3. 验证新作业处理结果正确
4. 切换流量——停止旧作业，让新作业接管

但更常用的方案是**双跑+切换**——新版本作业和老版本同时运行一段时间，验证一致后切换。这需要Kafka的Consumer Group隔离。

---

## 3. 项目实战

### 分步实现

#### 步骤1：触发Savepoint

**目标**：掌握触发Savepoint的三种方式。

```bash
# 方式1: CLI自动生成路径
./bin/flink savepoint <jobId>
# 输出: Savepoint completed. Path: file:///tmp/flink-savepoints/savepoint-<id>

# 方式2: 指定路径
./bin/flink savepoint <jobId> hdfs://namenode:8020/flink-savepoints

# 方式3: 停止作业同时触发Savepoint（最常用）
./bin/flink stop --savepointPath hdfs://namenode:8020/flink-savepoints <jobId>

# --drain: 停止时也停止Source（清空剩余的Source数据）
./bin/flink stop --savepointPath hdfs://... <jobId> --drain

# 方式4: 通过REST API
curl -X POST "http://localhost:8081/jobs/<jobId>/savepoints" \
  -H "Content-Type: application/json" \
  -d '{"target-directory": "hdfs://namenode:8020/flink-savepoints"}'
```

**Savepoint目录结构**：

```
hdfs://namenode:8020/flink-savepoints/
  └── savepoint-<id>/
      ├── _metadata        # 元数据（算子UID -> State映射）
      ├── <operatorId1>/
      │   ├── <stateName1> (0-<parallelism-1>)
      │   └── <stateName2>
      └── <operatorId2>/
          └── ...
```

#### 步骤2：从Savepoint恢复

**目标**：从Savepoint恢复作业，保留所有历史状态。

```bash
# 恢复（指定Savepoint路径）
./bin/flink run -s hdfs://namenode:8020/flink-savepoints/savepoint-<id> \
  -c MainClass \
  /jobs/job.jar

# 恢复时跳过不可恢复的状态（如删除了算子）
./bin/flink run -s hdfs://.../savepoint-<id> \
  --allowNonRestoredState \
  -c MainClass /jobs/job.jar

# 恢复并修改并行度
./bin/flink run -s hdfs://.../savepoint-<id> -p 16 \
  -c MainClass /jobs/job.jar
```

#### 步骤3：状态兼容性——新增状态字段

**目标**：在升级代码时新增State字段，验证自动兼容。

**场景**：旧代码有一个`ValueState<Integer>`，新代码新增了一个`MapState<String, Boolean>`。

```java
// 旧代码
ValueStateDescriptor<Integer> desc = new ValueStateDescriptor<>("user-count", Integer.class);
ValueState<Integer> state = getRuntimeContext().getState(desc);

// 新代码——新增State
ValueStateDescriptor<Integer> oldDesc = new ValueStateDescriptor<>("user-count", Integer.class);
ValueState<Integer> state = getRuntimeContext().getState(oldDesc);

MapStateDescriptor<String, Boolean> newDesc = new MapStateDescriptor<>(
        "user-flags", String.class, Boolean.class);
MapState<String, Boolean> newState = getRuntimeContext().getMapState(newDesc);
```

**Flink自动兼容的行为**：
- Savepoint中不存在的State → 初始化为空（`value()`返回null）
- Savepoint中存在的新代码中对应的State → 正常恢复
- Savepoint中存在但新代码中已删除的State → 被忽略（除非使用`--allowNonRestoredState`，则严格模式要求所有Savepoint中的状态都有对应的算子）

#### 步骤4：状态迁移——UID的重要性

**目标**：理解算子UID在Savepoint恢复中的关键作用。

```java
// ========== 注意：所有算子必须指定UID！ ==========
// Savepoint通过算子UID来匹配状态
// 如果UID变了，Flink不知道旧的状态对应哪个新算子，恢复失败

// ✅ 正确——显式指定UID
dataStream
    .map(new MyFunction()).uid("my-map-function").name("my-map")
    .keyBy(...)
    .flatMap(new MyFlatMap()).uid("my-flatmap").name("my-flatmap");

// ❌ 错误——没有UID，Flink自动生成。重新编译后UID就变了，状态恢复不了
dataStream
    .map(new MyFunction())
    .keyBy(...)
    .flatMap(new MyFlatMap());
```

#### 步骤5：使用State Processor API读取Savepoint数据

**目标**：在不影响运行作业的前提下，读取Savepoint中的数据用于分析。

```java
// Flink State Processor API（flink-state-processor-api模块）
// 可以将Savepoint读为一个DataSet，进行离线分析

// 1. 读取Savepoint
SavepointReader savepoint = SavepointReader
    .read(env, "hdfs://.../savepoint-<id>", new HashMapStateBackend());

// 2. 读取指定UID算子的状态
DataSet<Integer> userCounts = savepoint
    .readKeyedState("my-map-function", new MyStateReaderFunction());

// 3. 对状态数据做分析
userCounts.filter(count -> count > 1000)
          .print();

// 4. 作业完成后执行
env.execute("ReadSavepointAnalysis");
```

#### 步骤6：不停机升级——双跑方案

**目标**：零停机完成版本升级。

```bash
# Step 1: 启动新版本作业（使用新的Consumer Group，消费同样的Kafka Topic）
#         新作业不写Sink，只把结果写到Kafka新Topic用于验证
./bin/flink run -c NewVersionJob \
  -Dkafka.consumer.group.id=job-new-group \
  /jobs/new-job.jar

# Step 2: 验证新作业输出与旧作业一致（通过Kafka双Topic对比）

# Step 3: 触发旧作业Savepoint并停止
./bin/flink stop --savepointPath hdfs://.../savepoints <oldJobId>

# Step 4: 从Savepoint重启新版本作业（现在新作业接管所有流量）
./bin/flink run -s hdfs://.../savepoint-<id> \
  -c NewVersionJob \
  -Dkafka.consumer.group.id=job-prod-group \
  /jobs/new-job.jar
```

### 可能遇到的坑

1. **从Savepoint恢复时并行度变更报错**
   - 根因：Reduce/Aggregate等非Replicable算子不支持并行度变化。Savepoint中的状态按旧并行度分区，新并行度无法对应
   - 解决：仅对KeyedStream的算子（keyBy后的算子）变更并行度——它们支持状态的rebalance

2. **Savepoint恢复提示"State was not found"**
   - 根因：算子UID变了。重构代码时一不小心改了UID
   - 解方：始终使用显式的`.uid("...")`，不要依赖自动生成的UID

3. **Savepoint文件很大（TB级）恢复耗时数小时**
   - 根因：RocksDB增量Checkpoint只有最近的SST，但Savepoint是全量
   - 解方：Savepoint通常在作业空闲时触发；使用`trigger savepoint`定时调度

---

## 4. 项目总结

### Savepoint vs Checkpoint

| 维度 | Checkpoint | Savepoint |
|------|-----------|-----------|
| 触发 | 自动周期 | 手动 |
| 生命周期 | Flink自动管理 | 用户手动管理 |
| 用途 | 故障恢复 | 版本升级、作业迁移 |
| 存储 | 增量/全量（配为增量） | 全量 |
| 恢复时UID匹配 | N/A（同一作业） | 严格要求UID匹配 |
| 物理格式 | 与Savepoint相同 | 与Checkpoint相同 |

### Savepoint最佳实践

- **所有算子显式设置`uid()`**：这是Savepoint恢复的前提
- **定期触发Savepoint**：在版本升级前触发一次确保有最新的快照
- **清理旧Savepoint**：Savepoint不会自动清理，需要定期检查删除
- **测试Savepoint恢复**：在测试环境定期测试从Savepoint恢复——确保状态兼容

### 优点 & 缺点

| | Savepoint（手动状态快照） | Checkpoint（自动周期性快照） |
|------|-----------|-----------|
| **优点1** | 持久保留——版本升级/回滚的核心依赖 | 自动触发、自动清理，运维成本低 |
| **优点2** | 支持全量状态迁移——可跨集群/跨版本恢复 | 仅用于同版本故障恢复 |
| **优点3** | 与Operator UID绑定——显式控制状态映射 | 无需UID，自动匹配 |
| **缺点1** | 手动触发——运维操作，需人工关注 | 自动管理，无需人工干预 |
| **缺点2** | 全量快照——大状态Savepoint耗时久 | 支持增量Checkpoint，传输量小 |
| **缺点3** | 需要手动清理——容易遗留过期文件 | 自动清理旧Checkpoint |

### 适用场景

**典型场景**：
1. 作业版本升级——Savepoint保留旧状态，新代码从Savepoint恢复
2. 并行度变更——Savepoint支持keyed state的rebalance
3. 作业迁移——跨集群/跨项目作业迁移时Savepoint携带全量状态
4. 代码回滚——新版本上线后发现Bug，用旧Savepoint回退

**不适用场景**：
1. 自动故障恢复——应由Checkpoint自动完成，Savepoint不适用于此
2. 频繁变更的作业——每次Savepoint全量快照，大状态场景成本高

### 常见踩坑经验

**案例1：Flink 1.15升级到1.18，从旧Savepoint恢复失败**
- 根因：Flink大版本间的State序列化格式可能不兼容
- 解方：逐版本升级（1.15→1.16→1.17→1.18），每个版本触发一次Savepoint；或使用`--allowNonRestoredState`规避

**案例2：从Savepoint恢复后Kafka Offset不匹配——数据重复消费**
- 根因：Savepoint中的Kafka Source Offset与Flink算子状态不匹配
- 解方：确保Savepoint是在作业正常运行且Checkpoint成功之后触发的；使用`flink stop --savepointPath`（会先做一次Checkpoint再Savepoint）

**案例3：Savepoint恢复后作业正常运行但数据断层（missing data）**
- 根因：从Savepoint恢复到作业正常消费之间有"空白窗口"
- 解方：使用`--drain`参数停止Source，确保Savepoint时Source已经完全消费了所有已接收的数据

### 思考题

1. 算子UID为什么如此重要？如果两个不同的作业共享同一个UID（如Copy-Paste代码），从Savepoint恢复时会怎样？

2. 如果你需要将一个作业从并行度16迁移到并行度8，使用Savepoint恢复时还需要注意什么？什么类型的算子支持并行度变更，什么类型不支持？（提示：KeyedState和OperatorState的区别）

---

> **完整代码**：本章完整代码请参考附录或访问 https://github.com/flink-column/flink-practitioner  
> **思考题答案**：见附录文件 `appendix-answers.md`
