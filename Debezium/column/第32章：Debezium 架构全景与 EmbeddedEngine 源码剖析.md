# 第32章：Debezium 架构全景与 EmbeddedEngine 源码剖析

## 1. 项目背景

"大师，我们团队最近接了一个项目——基于 Debezium 核心框架开发一套自定义的 Source Connector，用来接入公司自研的分布式 KV 数据库。需求文档、接口设计、测试计划全写好了，但到了'编码'这一步——我们发现自己完全不知道 Debezium 内部是怎么运转的。"

这是一位架构师在 Debezium 社区邮件列表中的真实提问，也是很多想从 Debezium **使用者**升级为**构建者**的团队的共同瓶颈。

具体而言，他们的困惑集中在四个层面：

1. **生命周期不清晰**：一条 binlog 事件从被 MySQL 捕获，到最终写入 Kafka Topic，中间经历了哪些阶段？EmbeddedEngine 是怎么启动、运行、停止的？
2. **组件协作看不懂**：ChangeEventSourceCoordinator 怎么判断"应该先跑 Snapshot 还是直接进入 Streaming"？RecordMaker 怎么把数据库的原始行数据变成 Kafka Connect 的 SourceRecord？SchemaHistory 和 OffsetContext 这两个"记忆"模块各自负责什么？
3. **模块边界迷失**：`debezium-core` 和 `debezium-connector-mysql` 的边界在哪里？哪些逻辑是公共的（可以复用的），哪些是每个数据库特有的（需要重新实现的）？
4. **调试困难**：源码 clone 下来后，`debezium-core` 模块有上百个类，不知道该从哪个文件开始看。

### 痛点放大

没有源码心智模型的四大困境：

| 困境 | 具体表现 | 后果 |
|------|---------|------|
| 入口迷失 | 打开 IntelliJ 后在上百个类中随机翻阅 | 看完一天只理解了几个工具类 |
| 组件割裂 | 理解了 RecordMaker 但不知道它被谁调用 | 无法串联完整数据流 |
| 边界模糊 | 不知道该复用 core 的哪些类，该在 connector 模块中重写哪些 | 要么过度复用导致耦合，要么重复造轮子 |
| 调试无力 | 只知道在日志中找 ERROR，不知道在哪个类的哪个方法打断点 | Bug 排查耗时数小时 |

Debezium 的源码看似庞大（核心模块 `debezium-core` 就有 300+ 个类），但其内部真正关键的类只有 6 个，其他都是辅助工具类或特定数据类型的处理类。一旦你理解了这 6 个核心类的协作方式，整个框架就通了。

### Debezium 模块全景

```
debezium (根项目 - Maven 多模块)
│
├── debezium-core/             ← 核心引擎（本章重点）
│   ├── embedded/EmbeddedEngine.java         ★ 总入口：管理 Connector 生命周期
│   ├── pipeline/ChangeEventSourceCoordinator.java  ★ 调度器：Snapshot/Streaming 决策
│   ├── pipeline/RecordMaker.java             ★ 翻译官：DB 行 → SourceRecord
│   ├── pipeline/EventDispatcher.java         ★ 分发器：事件 → SMT → Producer
│   ├── pipeline/spi/OffsetContext.java       ★ 书签：读取/更新/持久化 offset
│   ├── pipeline/spi/SchemaHistory.java       ★ 版本控制：DDL 历史记录与回溯
│   └── transforms/              ← SMT 链实现（ExtractNewRecordState 等）
│
├── debezium-connector-mysql/   ← MySQL 适配器
│   └── MySqlStreamingChangeEventSource.java  ← binlog 消费
│   └── EventDeserializer.java              ← binlog bytes → RowChangedEvent
│
├── debezium-connector-postgres/← PG 适配器
├── debezium-connector-mongodb/ ← MongoDB 适配器
├── debezium-connector-sqlserver/← SQL Server 适配器
├── debezium-connector-oracle/  ← Oracle 适配器 (LogMiner/XStream)
├── debezium-server/            ← 独立进程模式（不依赖 Kafka）
└── debezium-storage/           ← offset/schema 存储后端抽象
```

## 2. 项目设计——三人对话

**（周一下午，小胖抱着一份刚从 GitLab 打印出来的 Debezium 源码，足足 300 多页）**

**小胖**："大师，我已经 git clone 了 Debezium 源码，用 IntelliJ 打开了。但我在 `debezium-core` 模块里翻了 20 分钟——几百个类、几千行代码，完全不知道该从哪个文件开始看。EmbeddedEngine 就有 800 多行，ChangeEventSourceCoordinator 更长..."

**大师**："源码阅读最大的敌人不是代码难懂，而是**没有导航地图**。我给你一张'十字路口'图——Debezium 内部真正关键的类只有 6 个。其他都是辅助——工具类、序列化器、特定数据类型的处理器。你只要理解这 6 个核心类的协作方式，整个框架就通了。"

**小白**（放下手中的笔记本）："哪 6 个？快告诉我！"

**大师**（在白板上边画边说）：

```
EmbeddedEngine.run() ─────────────── 引擎的总入口
    │
    ├─ Phase 1: 加载记忆
    │   ├─ OffsetContext.loadOffsets()
    │   │    ← 从 Kafka Topic "connect-offsets" 加载上次读到哪了
    │   │    ← 格式: {server: "dbserver1", file: "mysql-bin.000003", pos: 12345}
    │   │        或 {server: "dbserver1", gtid: "uuid:1-500"}
    │   │
    │   └─ SchemaHistory.recover()
    │        ← 从 Kafka Topic "schema-changes.xxx" 恢复所有 DDL 历史
    │        ← 包含: CREATE TABLE, ALTER TABLE ADD COLUMN, ...
    │
    ├─ Phase 2: 决策 Snapshot or Streaming ← ★ 最关键的决策点
    │   │
    │   └─ ChangeEventSourceCoordinator.start()
    │        │
    │        ├─ snapshotter.shouldSnapshot(offsetContext)?
    │        │   ├─ 判断条件 1: offset == null → 新 Connector，从未跑过
    │        │   ├─ 判断条件 2: offset.isSnapshotRunning() → 上次快照中断
    │        │   └─ 判断条件 3: snapshotMode.requiresSnapshot()
    │        │
    │        ├─ YES → executeSnapshotPhase()
    │        │         ├─ 加锁(FLUSH TABLES WITH READ LOCK 或 minimal)
    │        │         ├─ 记录当前 binlog 位点为水印
    │        │         ├─ SELECT * FROM table ORDER BY id (全表读取)
    │        │         ├─ 逐行转为 SourceRecord → 投递 Kafka
    │        │         └─ 解锁 → 标记 snapshot_completed=true
    │        │
    │        └─ 无论是否跑了快照，最终都进入 ↓
    │
    └─ Phase 3: Streaming 主循环
         │
         ├─ executeStreamingPhase()
         │    ├─ MySQL: BinaryLogClient 连接 binlog → 注册 EventListener
         │    ├─ PG:   创建 Replication Slot → pgoutput 插件
         │    └─ MongoDB: Change Streams / Oplog tailing
         │
         └─ while (running):
              events = coordinator.poll()          ← 从数据库拉变更事件
              for each event:
                record = RecordMaker.make(event)   ← ★ 数据库行 → SourceRecord
                record = applySMT(record)          ← SMT 链逐个处理
                producer.send(record)              ← 投递到 Kafka
              offsetContext.flush()                ← 定期保存 offset 到 Kafka
```

**小胖**："原来整个引擎的核心就是这四个阶段——加载记忆 → 决策 → 拉数据 → 转换/变形/投递！这个结构其实很像一个 ETL Pipeline。"

**大师**："对！技术映射：EmbeddedEngine = 一条全自动的**ETL 流水线**。Extract（从数据库 binlog/WAL 提取原始变更）→ Transform（SMT 链按顺序变形消息）→ Load（投递到 Kafka Topic）。Offset 和 SchemaHistory 就是这条流水线的'记忆'——Offset 记住'上次做到哪了'，SchemaHistory 记住'表结构在整个生命周期中怎么变的'。"

**小白**："大师，我还有一个问题——SchemaHistory 和 Offset 各自存在 Kafka 的哪个 Topic？数据格式是什么样的？"

**大师**："好问题。Offset 存在 `connect-offsets` Topic（由 `CONNECT_OFFSET_STORAGE_TOPIC` 配置指定），数据格式类似：

```json
// Key: ["inventory-connector", {"server":"dbserver1"}]
// Value:
{
  "transaction_id": null,
  "ts_sec": 1700100000,
  "file": "mysql-bin.000003",
  "pos": 456789,
  "gtids": "f3b3c7e4-1234-5678-9abc-def012345678:1-500",
  "snapshot_completed": true,     ← ★ 标记快照是否已完成
  "restart_from_snapshot": false
}
```

SchemaHistory 存在 Connector 专属的 `schema-changes.xxx` Topic（由 `schema.history.internal.kafka.topic` 配置指定），数据格式类似：

```json
{
  "source": {
    "server": "dbserver1",
    "file": "mysql-bin.000002",
    "pos": 7890
  },
  "databaseName": "inventory",
  "ddl": "ALTER TABLE orders ADD COLUMN discount DECIMAL(10,2) DEFAULT 0.00",
  "tableChanges": [{
    "type": "ALTER",
    "id": "inventory.orders",
    "table": { "columns": [ ... ] }
  }]
}
```

两个 Topic 都必须使用 `cleanup.policy=compact`——保证每条 Key 的最新 Value 永远不被删除，历史版本被压缩但 Key 不丢失。"

**小胖**："那 ChangeEventSourceCoordinator 的 `shouldSnapshot()` 具体怎么判断？三条件我懂了，但能看看实际代码吗？"

**大师**（在笔记本上打出关键代码）：

```java
// SnapshotterService 的决策逻辑
public Snapshotter getSnapshotter() {
    // 1. 用户强制指定了 snapshot.mode 配置
    if (snapshotMode == SnapshotMode.INITIAL) {
        return new InitialSnapshotter();       // 首次启动必定快照
    } else if (snapshotMode == SnapshotMode.WHEN_NEEDED) {
        return new WhenNeededSnapshotter();    // offset 无效时才快照
    } else if (snapshotMode == SnapshotMode.NEVER) {
        return new NeverSnapshotter();         // 永远不快照
    } else if (snapshotMode == SnapshotMode.SCHEMA_ONLY) {
        return new SchemaOnlySnapshotter();    // 只读表结构
    }
}

// InitialSnapshotter 的判断
public boolean shouldSnapshot(OffsetContext offset) {
    return offset == null || !offset.isSnapshotCompleted();
    // offset 为空(新Connector) 或 上次快照未完成 → 需要快照
}

// WhenNeededSnapshotter 的判断
public boolean shouldSnapshot(OffsetContext offset) {
    return offset == null;  // 只有 offset 完全丢失时才快照
}
```

**小白**："等一下——offset 也有'快照是否完成'的标记？那如果 offset 丢失了但 SchemaHistory 还在，快照能恢复吗？"

**大师**："这正是 SchemaHistory 和 Offset 的关联点。如果 offset 丢失了（`connect-offsets` Topic 被误删），但 SchemaHistory 还在——Connector 会从 SchemaHistory 重建表结构，但不知道数据同步到了哪里。此时 `snapshot.mode=when_needed` 会检测到 offset 为空 → 触发全量快照。如果 `snapshot.mode=never` → Connector FAILED，因为它不知道从哪开始 streaming。"

**技术映射总结**：Offset = 书签（告诉你读到 binlog 的哪一行），SchemaHistory = 字典（告诉你每个历史时刻的表结构是什么样的），ChangeEventSourceCoordinator = 阅读策略（决定先翻一遍整本书还是直接读最新一章），RecordMaker = 翻译官（把数据库的"方言"翻译成 Kafka 的"普通话"）。

---

## 3. 源码实战——四步追踪法

### 步骤1：获取源码并建立工作区

```bash
git clone https://github.com/debezium/debezium.git
cd debezium && git checkout v2.7.1.Final

# IntelliJ IDEA: File → Open → 选择 pom.xml → 等待 Maven 加载所有模块
# 建议至少加载：debezium-core, debezium-connector-mysql
# 如果只想看核心引擎，右键 debezium-core → Load/Unload Modules → 只保留 core
```

### 步骤2：追踪 EmbeddedEngine.run() 的完整生命周期

```java
// debezium-core/src/main/java/io/debezium/embedded/EmbeddedEngine.java
public class EmbeddedEngine implements Runnable {
    
    private final AtomicBoolean running = new AtomicBoolean(false);
    
    @Override
    public void run() {
        if (!running.compareAndSet(false, true)) {
            LOGGER.warn("Engine already running");
            return;
        }
        
        try {
            // ═══════ Phase 1: 加载记忆 ═══════
            LOGGER.info("Loading offsets from Kafka...");
            final OffsetContext offsetContext = OffsetContext.load(
                config,                                    // Connector 配置
                this.offsetStorageReader,                  // Kafka connect-offsets Topic 读取器
                this.connectorConfig.getConnectorName()   // Connector 名称
            );
            LOGGER.info("Loaded offset: {}", offsetContext);
            
            LOGGER.info("Loading schema history from Kafka...");
            final SchemaHistory schemaHistory = SchemaHistory.recover(
                config,
                this.schemaHistoryStorageReader,           // Kafka schema-changes Topic 读取器
                offsetContext
            );
            LOGGER.info("Recovered DDL history: {} events", schemaHistory.size());
            
            // ═══════ Phase 2: 决策并启动事件源 ═══════
            LOGGER.info("Starting ChangeEventSourceCoordinator...");
            final ChangeEventSourceCoordinator coordinator = 
                new ChangeEventSourceCoordinator(
                    offsetContext,
                    schemaHistory,
                    config,
                    this.connectorConfig
                );
            
            // coordinator.start() 内部会：
            // 1. 判断是否需要 snapshot → 如果需要，先执行全量/增量快照
            // 2. 启动 streaming source（连接 binlog/WAL/Oplog）
            coordinator.start();
            
            // ═══════ Phase 3: 主循环 ═══════
            LOGGER.info("Entering main streaming loop");
            while (running.get()) {
                try {
                    // 3a. 从数据库拉变更事件（阻塞等待）
                    List<DataChangeEvent> events = coordinator.poll();
                    if (events.isEmpty()) {
                        continue;  // 无新事件，自旋等待
                    }
                    
                    LOGGER.debug("Polled {} events from streaming source", events.size());
                    
                    for (DataChangeEvent event : events) {
                        // 3b. 数据库行 → SourceRecord（最关键的转换）
                        SourceRecord record = this.recordMaker.makeRecord(event);
                        
                        // 3c. SMT Chain 逐个处理
                        //     Transformations 是在 Connector 配置中通过 "transforms" 指定的
                        for (Transformation<SourceRecord> transformation : this.transformations) {
                            try {
                                record = transformation.apply(record);
                                if (record == null) {
                                    // SMT 返回 null → 表示该事件被过滤掉(Drop)
                                    LOGGER.trace("Event dropped by SMT: {}", transformation.getClass().getSimpleName());
                                    break;
                                }
                            } catch (Exception e) {
                                // SMT 处理失败 → 根据 errors.tolerance 配置决定行为
                                handleTransformationError(event, transformation, e);
                            }
                        }
                        
                        if (record != null) {
                            // 3d. 投递到 Kafka
                            this.producer.send(record, (metadata, exception) -> {
                                if (exception != null) {
                                    LOGGER.error("Failed to send record to Kafka", exception);
                                    this.errorHandler.handle(exception);
                                }
                            });
                        }
                    }
                    
                    // ═══════ Phase 4: 定期保存 offset ═══════
                    if (shouldFlushOffset()) {
                        offsetContext.flush();  // 写入 Kafka connect-offsets Topic
                        LOGGER.debug("Offset flushed: {}", offsetContext);
                    }
                    
                } catch (InterruptedException e) {
                    Thread.currentThread().interrupt();
                    LOGGER.info("Engine interrupted, shutting down");
                    break;
                } catch (Exception e) {
                    LOGGER.error("Unexpected error in streaming loop", e);
                    // 根据 error handling 策略决定是否重试或退出
                    if (!shouldRetry(e)) {
                        throw e;
                    }
                }
            }
            
        } catch (Throwable t) {
            LOGGER.error("Engine terminated with error", t);
            this.errorHandler.setError(t);
        } finally {
            LOGGER.info("Engine stopped");
            this.running.set(false);
        }
    }
}
```

### 步骤3：追踪一条 MySQL UPDATE 事件的完整代码路径

```
MySQL binlog network bytes
  │
  ▼
[1] MySqlStreamingChangeEventSource.receiveEvent()
    文件: debezium-connector-mysql/src/main/java/io/debezium/connector/mysql/
          MySqlStreamingChangeEventSource.java
    作用: BinaryLogClient 收到 binlog event 的回调入口
    
  │
  ▼
[2] EventDeserializer.deserialize(Event event) → List<RowChangedEvent>
    文件: debezium-connector-mysql/src/main/java/io/debezium/connector/mysql/
          EventDeserializer.java
    作用: 将 binlog 二进制数据(RowEvent)反序列化为结构化的 RowChangedEvent
          RowChangedEvent { 
            TableId table,              // 表标识
            EventType eventType,        // UPDATE_ROWS
            Map<String, Object> before, // {"id":100, "status":"pending"}
            Map<String, Object> after   // {"id":100, "status":"shipped"}
          }
    
  │
  ▼
[3] RecordMaker.makeRecord(RowChangedEvent) → SourceRecord
    文件: debezium-core/src/main/java/io/debezium/pipeline/RecordMaker.java
    作用: 将数据库行变更转为 Kafka Connect 的标准 SourceRecord
          SourceRecord {
            topic: "dbserver1.inventory.orders",
            key: Struct { id: 100 },
            value: Struct {
              before: {id:100, status:"pending"},
              after:  {id:100, status:"shipped"},
              source: {db:"inventory", table:"orders", file:"mysql-bin.000003", pos:456789},
              op: "u",
              ts_ms: 1700100000000
            }
          }
    
  │
  ▼
[4] SMT Chain 逐个处理
    Transformation.apply(record)
    例如: ExtractNewRecordState → SourceRecord 的 value 变为拍平的结构体
    
  │
  ▼
[5] KafkaProducer.send(SourceRecord)
    最终投递到 Kafka Topic: "dbserver1.inventory.orders"
```

### 步骤4：在关键节点插入调试日志，编译自己的 Trace 版本

**目标**：在 EventDeserializer 和 RecordMaker 的关键方法中插入日志，追踪完整的事件处理流程。

```java
// Step 1: 在 EventDeserializer.deserialize() 中插入
@Override
public List<RowChangedEvent> deserialize(Event event) {
    EventType eventType = event.getHeader().getEventType();
    
    LOGGER.info("[TRACE] Deserializing binlog event: type={}, db={}, table={}, timestamp={}",
        eventType,
        event.getData().getDatabase(),
        event.getData().getTable(),
        event.getHeader().getTimestamp());
    
    List<RowChangedEvent> rows = super.deserialize(event);
    
    LOGGER.info("[TRACE] Deserialized {} row(s) from event type={}",
        rows.size(), eventType);
    
    return rows;
}

// Step 2: 在 RecordMaker.makeRecord() 返回处插入
public SourceRecord makeRecord(RowChangedEvent event) {
    SourceRecord record = buildRecord(event);
    
    LOGGER.info("[TRACE] Created SourceRecord: topic={}, op={}, key={}, valueSize={}",
        record.topic(),
        extractOp(event),
        record.key(),
        estimateSize(record.value()));
    
    return record;
}

// Step 3: 在 SMT 链中插入
for (Transformation<SourceRecord> t : transformations) {
    long start = System.nanoTime();
    record = t.apply(record);
    long elapsed = System.nanoTime() - start;
    LOGGER.info("[TRACE] SMT '{}' applied in {}μs, result={}", 
        t.getClass().getSimpleName(), elapsed / 1000, record != null ? "pass" : "drop");
}
```

```bash
# 编译带有调试日志的版本
mvn clean install -pl debezium-connector-mysql -am -DskipTests -Dmaven.javadoc.skip=true

# 替换 Connector 插件 JAR
cp debezium-connector-mysql/target/debezium-connector-mysql-2.7.1.Final-plugin.tar.gz ~/debezium-lab/plugins/
cd ~/debezium-lab/plugins && tar -xzf debezium-connector-mysql-2.7.1.Final-plugin.tar.gz
docker restart connect

# 观察日志中的 [TRACE] 标记
docker logs connect -f 2>&1 | grep "TRACE"
```

### 步骤5：用 IDE 断点调试完整链路

```bash
# 1. 在 IntelliJ 中设置远程调试
# Run → Edit Configurations → Remote JVM Debug → Port 5005

# 2. 修改 docker-compose.yml 中 Connect 的启动参数，添加调试端口
environment:
  KAFKA_DEBUG: "y"
  KAFKA_DEBUG_PORT: "5005"
ports:
  - "5005:5005"

# 3. 在 IDEA 中对关键方法打断点：
#    - EmbeddedEngine.run() 的第一行
#    - ChangeEventSourceCoordinator.start() 
#    - RecordMaker.makeRecord() 的返回语句
#    - EventDeserializer.deserialize() 的开始处

# 4. 触发一次 DML 操作，观察断点命中
docker exec mysql mysql -uroot -proot1234 inventory -e "INSERT INTO orders (customer_id, product_name, quantity, price, status) VALUES (1, 'Debug Test', 1, 10, 'debug');"
```

### 可能遇到的坑及解决方法

| 坑 | 现象 | 根因 | 解决方法 |
|----|------|------|---------|
| IDE 中类文件显示为反编译版本 | 代码不可编辑，没有注释 | Maven 依赖 scope=provided 未正确识别 | `mvn install -DskipTests` 将模块安装到本地仓库 |
| 调试时变量不可见 | 显示 "this is not available" | JVM 优化掉了局部变量 | 编译时加 `-Dmaven.compiler.debug=true` |
| 修改源码后 Connector 未加载新版本 | 日志仍是旧行为 | `plugin.path` 中的 JAR 未替换 | 确认 `cp target/*.jar ~/debezium-lab/plugins/` 后重启 Connect |
| `mvn install` 失败 | `Could not resolve dependencies` | Maven 中央仓库访问问题 | 配置国内镜像: `~/.m2/settings.xml` 中添加阿里云镜像 |

---

## 4. 项目总结

### 六大核心组件速查表

| 核心类 | 文件路径 | 职责 | 一句话概括 | 何时需要深入 |
|-------|---------|------|-----------|------------|
| `EmbeddedEngine` | `core/.../embedded/` | 引擎入口，管理全生命周期 | "总开关" | 理解启动流程、自定义启动参数 |
| `ChangeEventSourceCoordinator` | `core/.../pipeline/` | Snapshot/Streaming 决策与调度 | "交通指挥" | 自定义 Snapshot 策略、优化快照行为 |
| `RecordMaker` | `core/.../pipeline/` | 数据库行 → Kafka SourceRecord | "翻译官" | 自定义数据类型映射、事件格式 |
| `OffsetContext` | `core/.../pipeline/spi/` | offset 读取/更新/持久化到 Kafka | "书签" | 自定义 offset 格式、从非 Kafka 存储恢复 |
| `SchemaHistory` | `core/.../pipeline/spi/` | DDL 历史记录与 Schema 回溯 | "版本控制" | 处理复杂 DDL 场景、Schema 恢复 |
| `EventDispatcher` | `core/.../pipeline/` | 事件分发：Record → SMT → Producer | "邮件分拣" | 自定义 SMT 链顺序、错误处理策略 |

### 优点 & 缺点（源码架构设计）

| 维度 | Debezium 源码架构 | 评价 |
|------|------------------|------|
| 模块化 | 核心(core) + 各数据库适配器(connector-xxx) | ★★★★★ 清晰分层 |
| 可扩展性 | SPI 机制 + 接口抽象 + 工厂模式 | ★★★★★ 新增数据库只需实现 4 个接口 |
| 可读性 | 命名规范、注释完整、日志详尽 | ★★★★☆ 核心类较复杂但注释充分 |
| 测试覆盖 | 大量单元测试 + 集成测试 + 嵌入式测试 | ★★★★★ 测试可读性强 |

### 源码阅读推荐路线

```
第 1 站: EmbeddedEngine.java        (理解整体生命周期)
  └─> run() 方法的 Phase 1-4

第 2 站: ChangeEventSourceCoordinator.java  (理解调度核心)
  └─> start() → shouldSnapshot() → Snapshot or Streaming

第 3 站: RecordMaker.java            (理解最关键的转换逻辑)
  └─> makeRecord() → DB row → SourceRecord

第 4 站: 具体 Connector 的 StreamingChangeEventSource
  └─> MySQL: MySqlStreamingChangeEventSource.receiveEvent()
  └─> PG: PostgresStreamingChangeEventSource.execute()
  └─> MongoDB: MongoDbStreamingChangeEventSource.execute()

第 5 站: OffsetContext 和 SchemaHistory 的实现类
  └─> 理解 offset 和 DDL 历史的存续机制
```

### 思考题

1. 如果 Connector 在 streaming 阶段 crash 了（如 binlog 解析异常），`EmbeddedEngine.run()` 重启后从哪行代码重新开始执行？offset 从哪加载？如果 offset 也丢失（connect-offsets Topic 被误删），且 `snapshot.mode=never`，会发生什么？

2. 快照阶段完成 80%（读了 80 万行/共 100 万行）时 Connector 所在的 Pod 被 K8s OOMKilled。重启后快照是从头开始还是从 80% 继续？需要什么条件才能实现断点续传？提示：增量快照按 chunk 操作与全量快照的行为不同。

**（第31章思考题答案）**

1. `spec.pause: true` 后 Connector 配置仍存在于 `connect-configs` Topic，offset 完全保留在 `connect-offsets` Topic 中，Task 状态为 PAUSED。恢复后从暂停位点继续。`kubectl delete kafkaconnector` 后 Connector 配置从 `connect-configs` 删除，但 offset 仍保留在 `connect-offsets` Topic 中。如果同名重建 Connector，复用 offset；如果不再重建，offset 在 compact 策略下不会丢失。

2. Strimzi Operator 通过 `strimzi.io/cluster` label 将 KafkaConnector 资源关联到特定的 KafkaConnect 资源。多个 Connector 可关联同一个 Connect 集群，Operator 自动管理它们之间的 Task 分配和 Restart 顺序。每个 Connector 的配置独立存储在 `connect-configs` Topic 的各自 Key 下。

---

> **推广提示**：将本章的架构图（6 核心类 + 4 阶段生命周期 + 源码阅读路线）打印为 A3 海报贴于团队技术角——新人 Onboarding 第一天就从这张图开始建立 Debezium 源码的心智模型。建议在团队 Wiki 中维护一份"源码阅读笔记"——每位成员在深入某个模块后记录关键发现，积累为团队的内部知识库。
