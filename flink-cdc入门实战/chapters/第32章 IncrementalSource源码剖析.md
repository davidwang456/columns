# 第32章 IncrementalSource源码剖析

## 1 项目背景

### 业务场景：高性能CDC读取的内核实现

第18、19章我们从使用层面了解了增量快照。现在深入底层——`IncrementalSource`是Flink CDC所有JDBC-based连接器（MySQL、PostgreSQL、Oracle）的**统一基础框架**。理解它能让你：
- 真正掌握Flink CDC的性能上限
- 能够调试Chunk切分、水位对齐等核心算法
- 为开发自定义Source打下基础

### IncrementalSource三件套架构

```
IncrementalSource (FLIP-27 Source)
│
├── SplitEnumerator
│   ├── 创建: createEnumerator()
│   ├── 分配: handleSplitRequest()
│   ├── 检查点: snapshotState()
│   └── 恢复: restoreState()
│
├── SourceReader
│   ├── 创建: createReader()
│   ├── 拉取: pollNext()
│   ├── 快照: snapshotState()
│   └── 通知: notifyCheckpointComplete()
│
└── SplitReader (实际IO操作)
    ├── 快照读取: SnapshotSplitReader
    └── 增量读取: StreamSplitReader
```

---

## 2 项目设计 · 三人交锋对话

**小胖**（困惑）：`IncrementalSource`在`flink-cdc-base`模块中——它到底是啥？是所有JDBC Source的父类？

**大师**：`IncrementalSource`是一个**抽象基类**，在`flink-connector-mysql-cdc/flink-cdc-base/src/main/java/org/apache/flink/cdc/connectors/base/source/IncrementalSource.java`中定义。

它实现了FLIP-27的`Source`接口，提供了增量快照的通用框架：

```java
public abstract class IncrementalSource<T, C extends SourceConfig>
    implements Source<T, IncrementalSplit<T>, IncrementalSourceEnumState> {

    // 每个子类（MySQL/PostgreSQL/Oracle）需要实现的方法
    protected abstract C createSourceConfig(SourceConfig.Factory configFactory);
    protected abstract SplitReader<IncrementalSplit<T>, IncrementalSplit<T>> createSplitReader(...);
    protected abstract SplitEnumerator<IncrementalSplit<T>, IncrementalSourceEnumState> createEnumerator(...);
}
```

每个数据库的Source（`MySqlSource`、`PostgresSource`、`OracleSource`）继承自`IncrementalSource`，实现数据库特有的读取逻辑。

**小白**：那`IncrementalSplit`是什么？它的内部结构是怎么样的？

**大师**：`IncrementalSplit`是增量快照的核心数据模型——表示"一个可以被并行读取的数据分片"：

```java
public class IncrementalSplit<T> implements SourceSplit {
    // 1. Split的唯一标识（用于恢复和追踪）
    private final String splitId;
    
    // 2. 数据分片类型
    private final SplitType splitType;  
    // SNAPSHOT = 全量快照分片
    // STREAM = 增量流分片
    
    // 3. 数据源标识
    private final TableId tableId;
    
    // 4. 拆分键范围（仅SNAPSHOT类型有）
    private final Serializable[] splitStart;
    private final Serializable[] splitEnd;
    
    // 5. 高水位标记（全量读取时的Binlog位点）
    private final Offset highWatermark;
    
    // 6. 序列化/反序列化
    public byte[] serialize();
    public static IncrementalSplit<T> deserialize(byte[] bytes);
}
```

一个SnapshotSplit对应一个`SELECT * FROM table WHERE id BETWEEN start AND end`。一个StreamSplit对应"从某个Binlog位点开始的所有增量变更"。

**技术映射**：Split就像"快递订单"——SplitId是订单号，SplitType是快递类型（标准件/加急件），splitStart/End是取件范围，highWatermark是"最后取件时间"。

---

## 3 项目实战

### 分步实现

#### 步骤1：阅读IncrementalSource核心源码

```java
// 源码路径:
// flink-cdc-base/src/main/java/org/apache/flink/cdc/connectors/base/source/IncrementalSource.java

public abstract class IncrementalSource<T, C extends SourceConfig>
    implements Source<T, IncrementalSplit<T>, IncrementalSourceEnumState> {

    @Override
    public SplitEnumerator<IncrementalSplit<T>, IncrementalSourceEnumState> createEnumerator(
            SplitEnumeratorContext<IncrementalSplit<T>> context) {
        // 1. 创建SourceConfig（含数据库连接信息）
        C sourceConfig = createSourceConfig(configFactory);
        
        // 2. 创建分片切分器
        ChunkSplitter chunkSplitter = createChunkSplitter(sourceConfig, context);
        
        // 3. 创建Split分配器
        IncrementalSplitAssigner assigner = new IncrementalSplitAssigner(
            chunkSplitter, sourceConfig.getStartupMode(), ...);
        
        // 4. 返回Enumerator
        return new IncrementalSplitEnumerator(context, assigner, ...);
    }

    @Override
    public SourceReader<T, IncrementalSplit<T>> createReader(
            SourceReaderContext context) {
        // 1. 创建实际的SplitReader（IO操作的核心）
        SplitReader<IncrementalSplit<T>, IncrementalSplit<T>> splitReader
            = createSplitReader(context);
        
        // 2. 创建RecordEmitter（事件输出）
        SourceRecordEmitter<T> recordEmitter = createRecordEmitter(context);
        
        // 3. 返回SourceReader
        return new IncrementalSourceReader<>(context, splitReader, recordEmitter, ...);
    }
}
```

#### 步骤2：跟踪SplitEnumerator的分片分配算法

```java
// 源码路径:
// .../base/source/enumerator/IncrementalSplitEnumerator.java

public class IncrementalSplitEnumerator
    implements SplitEnumerator<IncrementalSplit<?>, IncrementalSourceEnumState> {

    private final IncrementalSplitAssigner assigner;

    @Override
    public void handleSplitRequest(int subtaskId, String requesterHostname) {
        // 当一个Reader来要任务时...
        Optional<IncrementalSplit<?>> split = assigner.getNext();
        if (split.isPresent()) {
            // 有Split → 分配给该Reader
            context.assignSplit(split.get(), subtaskId);
        } else if (assigner.noMoreSplits()) {
            // 所有Split已分配完 → 通知Reader没有更多任务
            context.signalNoMoreSplits(subtaskId);
        }
        // 没有可用Split但还有未分配 → 等待
    }

    @Override
    public IncrementalSourceEnumState snapshotState() throws Exception {
        // Checkpoint时保存当前分配状态
        return new IncrementalSourceEnumState(
            assigner.snapshotState(),  // 分配器的持久化状态
            remainingSplits            // 尚未分配的Split列表
        );
    }

    @Override
    public void restoreState(IncrementalSourceEnumState state) throws Exception {
        // 从Checkpoint恢复
        assigner.restoreState(state.getAssignerState());
        remainingSplits.addAll(state.getRemainingSplits());
    }
}
```

#### 步骤3：SplitReader的核心读取逻辑

```java
// 源码路径:
// .../mysql/source/reader/MySqlSplitReader.java

public class MySqlSplitReader
    implements SplitReader<IncrementalSplit<RowData>, IncrementalSplit<RowData>> {

    private SnapshotSplitReader snapshotSplitReader;
    private BinlogSplitReader binlogSplitReader;
    private SplitType currentSplitType;

    @Override
    public RecordsBySplits<IncrementalSplit<RowData>> fetch() throws IOException {
        if (currentSplitType == SplitType.SNAPSHOT) {
            // 全量快照读取：通过JDBC SELECT读取Chunk数据
            return snapshotSplitReader.fetch();
        } else {
            // 增量流读取：通过Debezium读取Binlog变更
            return binlogSplitReader.fetch();
        }
    }

    @Override
    public void handleSplitsChanges(SplitsChange<IncrementalSplit<RowData>> splitsChange) {
        IncrementalSplit<RowData> split = splitsChange.splits().get(0);
        currentSplitType = split.getSplitType();
        
        if (currentSplitType == SplitType.SNAPSHOT) {
            // 收到SnapshotSplit → 初始化JDBC读此Chunk
            snapshotSplitReader = new SnapshotSplitReader();
            snapshotSplitReader.configure(databaseConfig, split);
        } else {
            // 收到StreamSplit → 初始化Debezium读Binlog
            binlogSplitReader = new BinlogSplitReader();
            binlogSplitReader.configure(databaseConfig, split);
        }
    }
}
```

#### 步骤4：观察水位对齐的核心逻辑

```java
// 源码路径: .../mysql/source/split/MySqlHybridSplitAssigner.java

// 水位对齐——全量快照和增量Binlog的衔接点
private Offset calculateHighWatermark() {
    // 从所有完成的SnapshotSplit中，取最小的Binlog位点
    // 因为每个Chunk在读取完成时都会记录当时的Binlog位置
    // 最小的位点 = "所有Chunk都能安全衔接的点"
    Offset watermark = null;
    for (MySqlSnapshotSplit split : finishedSnapshotSplits) {
        Offset splitWatermark = split.getHighWatermark();
        if (watermark == null || splitWatermark.compareTo(watermark) < 0) {
            watermark = splitWatermark;
        }
    }
    return watermark;
    // 从该位点开始的Binlog事件包含了所有Chunk读取期间的增量数据
}
```

#### 步骤5：通过E2E测试验证增量快照行为

```bash
# 运行MySqlHybridSplitAssignerTest
mvn test -pl flink-connector-mysql-cdc \
  -Dtest=MySqlHybridSplitAssignerTest \
  -DfailIfNoTests=false

# 运行MySqlChunkSplitterTest
mvn test -pl flink-connector-mysql-cdc \
  -Dtest=MySqlChunkSplitterTest
```

**测试输出示例：**
```
[INFO] MySqlChunkSplitterTest - 测试等距切分
  → 表: orders(1000000行), chunk.size=2000
  → 生成了500个Chunk
  → 各Chunk的行数标准差: 23.5 (均匀分布)
  → 验证通过

[INFO] MySqlHybridSplitAssignerTest - 测试Snapshot→Stream切换
  → SnapshotSplit: 500个(已分配)
  → 全部完成后: noMoreSplits=true
  → 创建StreamSplit: highWatermark=mysql-bin.000042:12345
  → 切换成功
```

#### 常见陷坑及解决方法

| 问题 | 原因 | 解决方法 |
|------|------|---------|
| IncrementalSource编译找不到类 | 模块依赖顺序不对 | 先编译`flink-cdc-base`再编译`flink-connector-mysql-cdc` |
| SplitEnumerator.restoreState()反序列化失败 | EnumChkT状态格式变化（跨版本升级） | 删除旧Checkpoint后重新启动作业 |
| 水位对齐算法导致数据空洞 | 某个Chunk的highWatermark比实际Binlog位置早 | 检查`compareTo`方法是否正确处理了GTID和不带GTID的对比 |

---

## 4 项目总结

### IncrementalSource核心类关系图

```
Source<T, SplitT, EnumChkT>
    ↑
IncrementalSource<T, C> (抽象基类)
    ↑
MySqlSource<T>  PostgresSource<T>  OracleSource<T>
    │
    ├── MySqlSplitEnumerator
    │   └── MySqlHybridSplitAssigner
    │       ├── MySqlSnapshotSplitAssigner
    │       └── MySqlStreamSplitAssigner
    │
    └── MySqlSourceReader
        └── MySqlSplitReader
            ├── SnapshotSplitReader (读取存量数据)
            └── BinlogSplitReader (读取增量数据)
```

### 增量快照的三种Split类型

| Split类型 | 用途 | 来源 | 并行度 | 读取方式 |
|-----------|------|------|--------|---------|
| SnapshotSplit | 全量数据分块 | ChunkSplitter切分 | N（最大并行） | JDBC SELECT |
| StreamSplit | 增量变更 | 所有Snapshot完成后 | 1（单线程） | Debezium Binlog |
| 混合模式 | Snapshot+Stream | HybridAssigner切换 | N→1 | SELECT→Binlog |

### 思考题

1. **进阶题①**：`IncrementalSource`的`SplitEnumerator`在`handleSplitRequest()`中，如果所有Reader都在请求Split但已经没有SnapshotSplit了，此时HybridSplitAssigner会创建StreamSplit吗？StreamSplit是给一个Reader还是所有Reader？提示：查看`MySqlHybridSplitAssigner.getNext()`的实现。

2. **进阶题②**：在水位对齐算法中，一个Chunk的`highWatermark`记录的是该Chunk读取开始时的Binlog位点，还是结束时的位点？如果Chunk1的highWatermark=position_A，Chunk2的highWatermark=position_B（A<B），最终水位线=min(A,B)=A。这意味着从位点A开始的Binlog事件会被增量流重复读取吗？请解释水位对齐如何避免数据重复。

---

> **下一章预告**：第33章「Debezium引擎集成源码分析」——Debezium是Flink CDC连接MySQL的底层引擎。本章将剖析`DebeziumSourceFunction`的CheckpointedFunction实现、Handover线程安全交付机制、FlinkOffsetBackingStore状态持久化等核心源码。
