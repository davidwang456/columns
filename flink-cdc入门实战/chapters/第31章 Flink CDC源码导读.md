# 第31章 Flink CDC源码导读

## 1 项目背景

### 业务场景：遇到Bug需要读源码排查

生产环境的Flink CDC 3.0作业在同步过程中遇到了一个奇怪的Bug——**增量快照阶段卡在99%永不完成**。官方文档中没有匹配的解决方案，Flink CDC的社区Issue中也没有类似案例。

这时唯一的选择就是：**读源码**。通过阅读Flink CDC的`HybridSplitAssigner`源码，发现Chunk切换时的水位对齐算法有个边界条件处理Bug。

本章开始高级篇的源码之旅——建立一个对Flink CDC项目结构、核心模块、关键入口的"认知地图"。

### Flink CDC 3.x模块依赖关系

```
flink-cdc-parent (根POM)
├── flink-cdc-common           ← 核心抽象层
│   ├── event                  Event接口（DataChangeEvent/SchemaChangeEvent）
│   ├── source                 DataSource接口
│   ├── sink                   DataSink接口
│   ├── types                  类型系统（DataTypes/DataTypeRoot）
│   └── pipeline               Pipeline配置（PipelineOptions）
│
├── flink-cdc-runtime          ← 运行时
│   ├── serializer             事件序列化器
│   ├── operators              算子（PreTransform/PostTransform/SchemaOperator）
│   ├── partitioning           分区器
│   └── parser                 表达式解析器（Calcite + Janino）
│
├── flink-cdc-composer         ← Pipeline编排
│   ├── flink/FlinkPipelineComposer   核心编排器
│   └── translator             Source/Transform/SchemaOperator/Sink翻译器
│
├── flink-cdc-cli              ← CLI入口
│   ├── CliFrontend            主入口
│   └── YamlPipelineDefinitionParser  YAML解析器
│
├── flink-cdc-connect          ← 连接器
│   ├── flink-cdc-source-connectors   Source连接器
│   │   ├── flink-connector-mysql-cdc
│   │   ├── flink-connector-postgres-cdc
│   │   ├── flink-connector-mongodb-cdc
│   │   └── flink-cdc-base           FLIP-27基础实现（IncrementalSource）
│   └── flink-cdc-pipeline-connectors Pipeline连接器
│       ├── flink-cdc-pipeline-connector-mysql
│       ├── flink-cdc-pipeline-connector-kafka
│       ├── flink-cdc-pipeline-connector-iceberg
│       └── flink-cdc-pipeline-connector-doris
│
├── flink-cdc-e2e-tests        ← 端到端测试
├── flink-cdc-dist             ← 发布包
└── flink-cdc-pipeline-udf-examples ← UDF示例
```

---

## 2 项目设计 · 三人交锋对话

**小胖**（看着源码目录）：10个模块……这比我预想的复杂多了。从哪开始读啊？

**大师**：读源码不能从头到尾读完（那是写书），要根据目的选择入口：

**入口A — 理解Pipeline执行流程：**
```
YAML → CliFrontend → YamlPipelineDefinitionParser
→ FlinkPipelineComposer.translate()
→ DataSourceTranslator → TransformTranslator → SchemaOperatorTranslator → DataSinkTranslator
→ DataStream API → 提交集群
```

**入口B — 理解增量快照（FLIP-27）：**
```
MySqlSource → IncrementalSource (FLIP-27 Source接口)
→ MySqlSplitEnumerator (管理Split分配)
→ MySqlSourceReader (处理Split读取)
→ MySqlChunkSplitter (Chunk切分)
→ HybridSplitAssigner (SnapshotSplit + StreamSplit切换)
```

**入口C — 理解Schema Evolution：**
```
SchemaOperator → SchemaCoordinator → SchemaRegistry
→ SchemaDerivator (推导新Schema)
→ MetadataApplier (应用到Sink)
```

**小白**：我想重点看MySqlSource的实现——它到底是怎么从MySQL读数据的？FLIP-27的Source接口怎么实现的？

**大师**：`MySqlSource`实现了FLIP-27的`Source`接口——这是Flink 1.12引入的新Source API，取代了传统的`SourceFunction`。

核心类关系：
```
Source<SplitT, EnumChkT>
├── createEnumerator() → SplitEnumerator  ← 分配Split
└── createReader() → SourceReader        ← 消费Split

MySqlSource<SplitT, EnumChkT>
├── SplitEnumerator: MySqlSplitEnumerator
│   └── assigner: HybridSplitAssigner
│       ├── SnapshotSplitAssigner (全量阶段)
│       └── StreamSplitAssigner (增量阶段)
└── SourceReader: MySqlSourceReader
    ├── splitReader: MySqlSplitReader
    │   ├── SnapshotSplitReader (读取Chunk)
    │   └── BinlogSplitReader (读取Binlog)
    └── recordEmitter: MySqlRecordEmitter (输出Event)
```

**技术映射**：MySqlSource的FLIP-27架构就像"外卖配送系统"——Enumerator是调度中心（决定哪个骑手去哪个商家取餐），Reader是骑手（实际执行取餐和配送）。增量快照阶段，调度中心先派所有骑手去不同商家（并行取餐=Chunk读取），再到高峰期切换到单骑手送所有订单（单线程Binlog读取）。

---

## 3 项目实战

### 分步实现

#### 步骤1：搭建源码编译环境

```bash
# 1. 克隆Flink CDC源码
git clone https://github.com/apache/flink-cdc.git
cd flink-cdc

# 2. 切换到目标版本（这里使用3.0.0）
git checkout release-3.0.0

# 3. 编译（跳过测试，节省时间）
mvn clean install -DskipTests -Dfast

# 4. 导入IntelliJ IDEA
# File → Open → 选择flink-cdc目录 → 等待Maven索引完成

# 5. 配置IDEA运行配置
# 在MySqlSourceExampleTest中右键运行
# VM Options: -Dtest.containers.docker.image=mysql:8.0
```

#### 步骤2：阅读MySqlSource的核心初始化流程

```java
// 关键源码位置: 
// flink-cdc-connect/flink-cdc-source-connectors/flink-connector-mysql-cdc
//   src/main/java/org/apache/flink/cdc/connectors/mysql/source/MySqlSource.java

public class MySqlSource<T> implements Source<T, MySqlSplit, MySqlSourceEnumState> {

    @Override
    public SplitEnumerator<MySqlSplit, MySqlSourceEnumState> createEnumerator(
            SplitEnumeratorContext<MySqlSplit> context) {
        
        // 1. 创建ChunkSplitter（切分器）
        MySqlChunkSplitter chunkSplitter = createChunkSplitter(context);
        
        // 2. 创建SplitAssigner（分片分配器）
        // HybridSplitAssigner = SnapshotSplitAssigner + StreamSplitAssigner
        HybridSplitAssigner assigner = new HybridSplitAssigner(
            context, chunkSplitter, ...);
        
        // 3. 创建Enumerator
        return new MySqlSplitEnumerator(context, assigner, ...);
    }

    @Override
    public SourceReader<T, MySqlSplit> createReader(
            SourceReaderContext context) {
        // 1. 创建SplitReader（实际读取器）
        MySqlSplitReader splitReader = new MySqlSplitReader(...);
        
        // 2. 创建RecordEmitter（事件发射器）
        MySqlRecordEmitter<T> emitter = new MySqlRecordEmitter<>(
            deserializationSchema, ...);
        
        // 3. 创建SourceReader
        return new MySqlSourceReader<>(
            splitReader, emitter, context, ...);
    }
}
```

#### 步骤3：跟踪HybridSplitAssigner的Split切换逻辑

```java
// 关键源码:
// .../mysql/source/split/MySqlHybridSplitAssigner.java

public class MySqlHybridSplitAssigner implements MySqlSplitAssigner {

    private final MySqlSnapshotSplitAssigner snapshotAssigner;
    private MySqlStreamSplitAssigner streamAssigner;
    
    @Override
    public Optional<MySqlSplit> getNext() {
        if (snapshotAssigner.noMoreSplits()) {
            // 全量分片全部读完 → 切换到增量流
            if (streamAssigner == null) {
                // 创建增量流分配器
                streamAssigner = new MySqlStreamSplitAssigner(...);
                // 记录所有SnapshotSplit的结束位点
                // 取最小值作为HighWatermark
                for (MySqlSnapshotSplit split : finishedSplits) {
                    highWatermark = min(highWatermark, split.getHighWatermark());
                }
                streamAssigner.setStartPosition(highWatermark);
            }
            return streamAssigner.getNext();
        } else {
            // 还有快照分片 → 继续分配
            return snapshotAssigner.getNext();
        }
    }
}
```

#### 步骤4：启动源码调试——设置断点

在IDE中设置断点并运行测试：

```bash
# 在IDEA中打开MySqlSourceExampleTest
# 在以下关键位置设置断点：

# 断点1: MySqlHybridSplitAssigner.getNext() 
#   → 观察Split从Snapshot切换到Stream

# 断点2: MySqlSourceReader.start() 
#   → 观察SourceReader初始化

# 断点3: MySqlRecordEmitter.emitRecord()
#   → 观察每一条CDC事件被发射出来

# 断点4: FlinkPipelineComposer.translate()
#   → 观察Pipeline YAML到DataStream的翻译
```

**调试后你能得到的问答：**
- Q: `MySqlSource`的并行度怎么决定Chunk数量？
- A: Chunk数量 = CEIL(总行数 / chunk.size)，并行度决定同时读取多少Chunk
- Q: 增量快照什么时候切换到增量流？
- A: 所有SnapshotSplit全部完成后

#### 常见陷坑及解决方法

| 问题 | 原因 | 解决方法 |
|------|------|---------|
| 源码编译失败（找不到依赖） | 先编译父POM再编译子模块 | 按顺序：common→runtime→composer→cli→connect |
| 测试用例需要Docker | MySqlSourceExampleTest使用Testcontainers | 确保Docker Desktop运行中 |
| 找不到类定义 | IDE未正确加载源码模块 | 在IDEA中执行`mvn idea:idea`或重新导入Maven项目 |
| 断点无法命中 | 编译的class和源码版本不匹配 | 清理重新编译：`mvn clean install -DskipTests` |

---

## 4 项目总结

### 源码阅读推荐路径

```
新手上路（1-2天）:
  1. MySqlSource.java —— Source接口
  2. MySqlSourceReader.java —— 如何读取
  3. FlinkPipelineComposer.java —— Pipeline怎么编排

进阶深入（2-3天）:
  4. IncrementalSource.java —— FLIP-27基础
  5. MySqlChunkSplitter.java —— Chunk切分
  6. SchemaOperator.java —— Schema协调
  7. PreTransformOperator.java —— 转换执行

源码专家（3-5天）:
  8. DebeziumSourceFunction.java —— Debezium集成
  9. TransformExpressionCompiler.java —— 表达式编译
  10. EventSerializer.java —— 序列化
  11. DataSourceTranslator.java —— Source翻译器
```

### 开发环境配置清单

```
□ JDK 11
□ Maven 3.8+
□ IntelliJ IDEA (推荐Ultimate)
□ Docker Desktop（运行E2E测试）
□ Lombok插件（Flink CDC部分类使用Lombok）
□ Google Java Format插件（Flink CDC使用Spotless格式化）
```

### 思考题

1. **进阶题①**：阅读`FlinkPipelineComposer.translate()`源码，说出Pipeline YAML从解析到Flink DataStream拓扑的完整翻译过程。哪些环节可能抛出<code>CompositionException</code>？

2. **进阶题②**：`MySqlSource`实现了`Source<T, MySqlSplit, MySqlSourceEnumState>`。第三个泛型参数`MySqlSourceEnumState`代表什么？它是如何被序列化和Checkpoint的？提示：查看`MySqlSourceEnumStateSerializer`的实现。

---

> **下一章预告**：第32章「IncrementalSource源码剖析」——深入FLIP-27的`IncrementalSource`基础框架，剖析`MySqlHybridSplitAssigner`的分片分配算法和`BinlogSplitReader`的增量读取实现。
