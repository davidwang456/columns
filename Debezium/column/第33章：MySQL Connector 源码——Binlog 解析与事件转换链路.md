# 第33章：MySQL Connector 源码——Binlog 解析与事件转换链路

## 1. 项目背景

"大师救命！我们上周把 MySQL 从 8.0.33 升级到 8.0.35 后，Debezium Connector 突然抛了一个 `Unknown event type 39` 的异常。我看官网 changelog 发现 8.0.35 确实新增了一个 `TRANSACTION_PAYLOAD` 类型的 binlog event。我搜遍全网只有 Debezium 的 GitHub Issue #5678 提到这个问题，但还没有发布修复版本。我能不能自己改源码先把这个问题绕过？"

这是拥有源码能力的典型价值场景——当 Connector 版本滞后于数据库版本，或者 Connector 对某个特定的数据库行为处理不当时，只有看懂了 MySQL Connector 源码，才能快速定位问题并在数小时内完成 Hotfix，而不需要等待官方发布新版本（可能数周后）。

本章将深入 MySQL Connector 四个核心类的源码——**BinaryLogClient 连接与事件订阅**、**EventDeserializer 二进制反序列化**、**GtidSet 全局事务 ID 管理**、以及**类型转换机制**。学完本章，你不仅能定位 `Unknown event type` 这类问题，还能理解 Connector 如何处理 MySQL 特有的复杂类型（DATETIME/TIME/DECIMAL/JSON）。

### MySQL Connector 依赖的 binlog 解析库

Debezium MySQL Connector 底层不是自己实现 binlog 协议解析，而是依赖开源库 `mysql-binlog-connector-java`（也称作 `binlog-client`），它负责与 MySQL 建立 replication 协议连接、接收 binlog event 的二进制数据。Debezium 在它的基础上做了两层封装：**EventDeserializer（反序列化）和 RecordMaker（标准化转换）**。

```
MySQL binlog 网络字节流
  ↓
mysql-binlog-connector-java (BinaryLogClient)  ← 处理 MySQL 复制协议
  ↓
EventDeserializer.deserialize()                ← Debezium 第一层：bytes→结构化
  ↓
RecordMaker.makeRecord()                       ← Debezium 第二层：结构化→SourceRecord
  ↓
Kafka Producer
```

## 2. 项目设计——三人对话

**小胖**："大师，我在 EventDeserializer 里看到了一个 super long 的 switch-case——大概有 30 多种 event type，什么 `WRITE_ROWS`、`UPDATE_ROWS`、`DELETE_ROWS`、`TABLE_MAP`、`QUERY`、`FORMAT_DESCRIPTION`...为什么有这么多类型？"

**大师**："因为 MySQL binlog 不光记录数据变更，还记录了很多元数据事件。分成三类：

- **数据事件**（有行数据）：`WRITE_ROWS`(INSERT)、`UPDATE_ROWS`、`DELETE_ROWS`——这三种是 Connector 最关心的
- **元数据事件**：`TABLE_MAP`（记录表结构映射，binlog 中用 table_id 而非表名引用表）、`QUERY`（DDL 操作如 ALTER TABLE）、`FORMAT_DESCRIPTION`（binlog 文件头）
- **控制事件**：`ROTATE`（binlog 文件切换）、`GTID`（GTID 事务开始标记）、`XID`（事务提交标记）"

**小白**："那 `TRANSACTION_PAYLOAD` type 39 是哪一类？为什么 Debezium 不认识它？"

**大师**："它是 MySQL 8.0.35 新增的——用来在 binlog 中携带事务级别的附加信息。因为它不属于数据事件也没有行数据，所以 EventDeserializer 的 switch-case 中没有对应的处理分支。直接抛异常是因为 switch 走到了 default 分支。解法很简单——在 default 分支中改为 `LOGGER.info("Skipping unknown event type: {}", type)`，忽略之而非抛异常。"

**小胖**："那类型转换呢？我看到 `convertBinlogValue()` 方法里对 DATETIME、TIME、DECIMAL 这些类型做了特殊的二进制解析。为什么不直接用 JDBC 的方式读？"

**大师**："因为 binlog 中存储的是 MySQL 的**内部二进制格式**，不是 SQL 字符串。比如：

- `DATETIME` 在 binlog 中是 **5 字节 packed 格式**——1 字节存符号+世纪，4 字节存年月日时分秒
- `DECIMAL(10,2)` 在 binlog 中是 **二进制补码格式**——每 9 位十进制数字压缩为 4 字节
- `JSON` 在 binlog 中是 **二进制 JSON（BSON-like）**——需要解析内部的 type/offset/length 结构

如果 Connector 用 JDBC 读（SELECT *），它拿到的是 MySQL 自动转换后的字符串。但 binlog 中的原始字节必须自己解析——这就是 `convertBinlogValue()` 存在的意义。"

## 3. 源码实战

### 3.1 源码结构导航

```
debezium-connector-mysql/src/main/java/io/debezium/connector/mysql/
├── MySqlConnector.java            # Connector 注册入口
├── MySqlConnectorTask.java        # Task 入口 → 调用 StreamingChangeEventSource
├── MySqlStreamingChangeEventSource.java  # ★ Streaming 主循环
│   ├── BinaryLogClient client
│   ├── registerEventListener()
│   └── handleEvent(event) → deserialize → makeRecord
│
├── EventDeserializer.java         # ★★ 核心1: binlog bytes → RowChangedEvent
│   ├── deserialize(Event event) → List<RowChangedEvent>
│   └── convertBinlogValue(Object raw, ColumnType colType) → Java Object
│       - INT → Integer (4 bytes little-endian)
│       - DATETIME → LocalDateTime (5 bytes packed)
│       - DECIMAL → BigDecimal (binary-coded decimal)
│       - JSON → String (binary JSON parsed)
│
├── GtidSet.java                   # ★★ 核心2: GTID 集合追踪
│   ├── Map<String(UUID), IntervalSet>
│   ├── add(String gtid)
│   └── isContainedWithin(GtidSet other)
│
├── RecordMaker.java               # RowChangedEvent → SourceRecord
│   └── makeRecord(RowChangedEvent) → SourceRecord
│
├── SnapshotChangeEventSource.java # 全量快照逻辑
└── MySqlConnection.java           # JDBC 连接封装
```

### 3.2 BinaryLogClient —— 连接与事件订阅

```java
// MySqlStreamingChangeEventSource.execute()
public void execute(ChangeEventSourceContext context) {
    // 1. 创建 binlog 客户端
    BinaryLogClient client = new BinaryLogClient(hostname, port, user, password);
    
    // 2. 从 offset 恢复读取位置
    if (offsetContext.hasGtid()) {
        client.setGtidSet(offsetContext.gtidSet());       // GTID 模式: "uuid:1-500"
    } else {
        client.setBinlogFilename(offsetContext.binlogFile());  // 传统模式: "mysql-bin.000003"
        client.setBinlogPosition(offsetContext.binlogPosition()); // 位点: 12345
    }
    
    // 3. 保证 MySQL 不主动断开空闲连接
    client.setKeepAlive(true);
    client.setKeepAliveInterval(60000);  // 每 60s 发心跳
    
    // 4. 注册事件监听器——这是核心
    client.registerEventListener(event -> {
        EventType type = event.getHeader().getEventType();
        
        // TABLE_MAP: 记录表结构映射（binlog 用 table_id 引用表，此处建立映射）
        if (type == EventType.TABLE_MAP) {
            tableSchemaCache.processTableMap(event);
            return;
        }
        
        // 数据事件: 有行数据的 INSERT/UPDATE/DELETE
        if (EventType.isRowMutation(type)) {
            List<RowChangedEvent> rows = eventDeserializer.deserialize(event);
            for (RowChangedEvent row : rows) {
                SourceRecord record = recordMaker.makeRecord(row);
                eventQueue.offer(record);
            }
        }
        
        // GTID: GTID 事务标记
        if (type == EventType.GTID) {
            String gtid = extractGtid(event);
            gtidSet.add(gtid);
            offsetContext.setGtid(gtid);
        }
    });
    
    // 5. 连接并开始持续消费（阻塞直到连接断开或 Connector 停止）
    client.connect();
}
```

### 3.3 EventDeserializer —— 二进制→结构化行数据

```java
// EventDeserializer.deserialize()
public List<RowChangedEvent> deserialize(Event event) {
    EventType type = event.getHeader().getEventType();
    WriteRowsEventData data = (WriteRowsEventData) event.getData();  // INSERT
    
    // 从 TableMap 缓存中获取表结构
    long tableId = data.getTableId();
    Table table = tableSchemaCache.getTable(tableId);
    if (table == null) {
        LOGGER.warn("No TableMap for tableId={}, skipping", tableId);
        return Collections.emptyList();
    }
    
    List<RowChangedEvent> result = new ArrayList<>();
    List<Object[]> rows = data.getRows();
    
    for (int rowIndex = 0; rowIndex < rows.size(); rowIndex++) {
        Object[] rawRow = rows.get(rowIndex);  // 每列的值是原始对象(Object)
        Map<String, Object> columnValues = new LinkedHashMap<>();
        
        for (int colIndex = 0; colIndex < table.columns().size(); colIndex++) {
            Column column = table.columns().get(colIndex);
            Object rawValue = rawRow[colIndex];  // binlog 原始值
            
            // ★ 最关键的类型转换
            Object convertedValue = convertBinlogValue(rawValue, column.type());
            columnValues.put(column.name(), convertedValue);
        }
        
        result.add(new RowChangedEvent(
            table.id(),           // TableId
            type,                 // WRITE_ROWS / UPDATE_ROWS / DELETE_ROWS
            columnValues          // {colName → convertedValue}
        ));
    }
    return result;
}

// 类型转换示例
private Object convertBinlogValue(Object raw, ColumnType type) {
    if (raw == null) return null;
    
    switch (type) {
        case INT:
            return ((Integer) raw);  // 4 bytes → Integer
        case BIGINT:
            return ((Long) raw);     // 8 bytes → Long
        case DATETIME:
        case TIMESTAMP:
            // 5 bytes packed → LocalDateTime
            // byte[0]: sign+century, byte[1-4]: year-month-day hour-minute-second
            return unpackDatetime((byte[]) raw);
        case DECIMAL:
            // binary-coded decimal → BigDecimal
            return unpackDecimal((byte[]) raw, precision, scale);
        case JSON:
            // binary JSON → JSON String
            return parseBinaryJson((byte[]) raw);
        case ENUM:
        case SET:
            return ((String) raw);  // Enum/Set 在 binlog 中是索引，转为字符串
        default:
            LOGGER.debug("Unknown column type: {}", type);
            return raw;  // 原样返回，让下游 Serializer 处理
    }
}
```

### 3.4 GtidSet —— 全局事务 ID 追踪

```java
public class GtidSet {
    // 数据结构: Map<"UUID字符串", IntervalSet{区间链表}>
    // 例如: {"f3b3c7e4-1234-5678-9abc-def012345678" → [1-500, 600-800]}
    private final Map<String, IntervalSet> intervals = new HashMap<>();
    
    // 解析 "f3b3c7e4-...:1-500" 格式
    public void add(String gtid) {
        int colonIndex = gtid.lastIndexOf(':');
        String sourceId = gtid.substring(0, colonIndex);  // "f3b3c7e4-..."
        long transactionId = Long.parseLong(gtid.substring(colonIndex + 1));  // 501
        
        intervals.computeIfAbsent(sourceId, k -> new IntervalSet())
                 .add(transactionId);
    }
    
    // 判断当前 GTID Set 中的所有事务是否都已被 other 覆盖
    // → 用于判断 binlog 是否已包含该表的所有变更
    // → 如果 return true，Connector 可以安全地从当前位点继续
    public boolean isContainedWithin(GtidSet other) {
        for (Map.Entry<String, IntervalSet> entry : intervals.entrySet()) {
            IntervalSet otherSet = other.intervals.get(entry.getKey());
            if (otherSet == null || !entry.getValue().isContainedWithin(otherSet)) {
                return false;
            }
        }
        return true;
    }
}
```

### 3.5 Hotfix: 处理 MySQL 8.0.35 新增的 TRANSACTION_PAYLOAD

```java
// 在 EventDeserializer.deserialize() 的 switch-case 中添加
switch (type) {
    case WRITE_ROWS:  case UPDATE_ROWS:  case DELETE_ROWS:
        // 正常处理
        break;
    case TRANSACTION_PAYLOAD:  // type 39 (MySQL 8.0.35+)
        LOGGER.info("Skipping TRANSACTION_PAYLOAD event (MySQL 8.0.35+), not yet supported");
        return Collections.emptyList();  // 忽略，不抛异常
    default:
        LOGGER.info("Skipping unknown event type: {} (ordinal={})", type, type.ordinal());
        return Collections.emptyList();
}
```

## 4. 项目总结

| 核心类 | 职责 | 关键方法 |
|-------|------|---------|
| `MySqlStreamingChangeEventSource` | binlog 连接 + 事件主循环 | `execute()`, `handleEvent()` |
| `EventDeserializer` | binlog bytes → RowChangedEvent | `deserialize()`, `convertBinlogValue()` |
| `GtidSet` | GTID 集合管理与事务边界追踪 | `add(gtid)`, `isContainedWithin(other)` |
| `RecordMaker` | RowChangedEvent → SourceRecord | `makeRecord(event)` |
| `SnapshotChangeEventSource` | 全量快照逻辑 | `execute()` → `SELECT * FROM table` |

### 思考题

1. `convertBinlogValue()` 中如果遇到一个 MySQL 新版本才支持的 ColumnType（当前 Debezium 版本未实现），应该 crash 整个 Connector（fail-fast），还是 skip 该字段并记录告警（fail-safe）？两种策略各适用于什么场景？

2. GTID 在 MySQL MHA/MGR 主从切换后，新 Master 的 GTID UUID 部分会改变。旧的 offset 中存的是旧 Master 的 GTID，Connector 如何判断新 Master 的 GTID 是否覆盖了旧 GTID 的位点？如果不覆盖，会发生什么？

---

> **推广提示**：每次 MySQL 大版本升级前，拉取 Debezium Connector 源码中 `EventType` 和 `ColumnType` 枚举，与 MySQL 官方 changelog 的 "New binlog event types" 对比——确保新类型已被处理或至少被安全忽略。将一致性校验纳入升级检查清单。
