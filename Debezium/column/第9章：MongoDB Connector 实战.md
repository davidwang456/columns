# 第9章：MongoDB Connector 实战

## 1. 项目背景

"用户行为日志存储在 MongoDB 里，算法团队需要实时消费这些行为数据来做推荐模型更新。"——这个需求让小胖陷入了新的困境。MongoDB 是一个文档型 NoSQL 数据库，没有固定 Schema（Schema-less），没有传统的"行"和"列"概念，更谈不上 binlog 或 WAL。它的变更捕获机制基于 **Oplog（操作日志）**，这是 MongoDB Replica Set 内部用来做主从同步的日志机制。

Debezium MongoDB Connector 正是通过伪装成一个"影子从节点"（Secondary），连接到 Replica Set 中消费 Oplog 来获取变更数据。但这也意味着一个硬性前提——MongoDB 必须是 Replica Set 模式，单节点 Standalone 模式不支持 Oplog，也就无法使用 Debezium。

本章将从零搭建 MongoDB Replica Set，注册 Connector，并处理文档型数据的变更事件。你将看到嵌套 JSON 文档、数组字段、甚至是文档结构变更如何在 Kafka 消息中呈现。

### 痛点放大

- **Replica Set 门槛**：生产环境通常已经是 RS 模式，但开发和测试经常用 Standalone 无法接入
- **Schema-less 的挑战**：同一条 MongoDB 集合中，不同文档可能有完全不同的字段。Debezium 如何在 Schema Registry 中表示这种"无 Schema"的数据？
- **Oplog 大小限制**：Oplog 是固定大小的 Capped Collection，写入量大时旧数据被覆盖，导致 Connector 追不上
- **嵌套文档更新**：MongoDB 的 `$set`、`$unset`、`$push` 等操作，和关系型数据库的 UPDATE 语义完全不同

---

## 2. 项目设计——三人对话

**（小胖在调试一个新搭建的 MongoDB 环境）**

**小胖**："大师，MySQL 有 binlog，PG 有 WAL，MongoDB 总该简单点吧？我搭了个单节点 MongoDB，Connector 直接报错说 'Oplog is not available'..."

**大师**："MongoDB 的变更捕获完全依赖 Oplog。Oplog 是 Replica Set 的一个 Capped Collection（`local.oplog.rs`），记录了所有数据的增删改操作，主节点用 Oplog 把变更同步给从节点。Debezium Connector 的原理就是——伪装成一个 Secondary 节点，订阅 Oplog 的变化。"

**小白**："伪装成 Secondary？那它不需要真的存储数据副本吗？只是读 Oplog 不需要写数据吧？"

**大师**："对，它不需要存储数据。Connector 向 Primary 发送 `isMaster` 命令获取 RS 拓扑，然后连接 Oplog 并持续读取。它就像一个隐形的影子——能看到所有操作，但不参与数据复制。"

**小胖**："那如果我的 MongoDB 只有一个节点怎么办？开发环境不可能搭一套 Replica Set 吧？"

**大师**："有一个取巧的办法——**单节点 Replica Set**。用 `rs.initiate()` 启动一个只有一个成员的 Replica Set，MongoDB 仍然会创建 Oplog。虽然这不符合生产最佳实践，但对于开发和测试是完全可行的。我们在实战中就用这种方式。"

**小白**："我更好奇的是——MongoDB 的文档结构是 Schema-less 的，比如 users 集合中，第一个文档有 `{name, email, age}` 三个字段，第二个文档突然多了个 `{name, email, age, address: {city, street}}`。这种动态 Schema 在 Change Event 里怎么表示？"

**大师**："这正是 MongoDB Connector 最特殊的地方。"大师在白板上画道——

```
MongoDB Change Event 结构：
{
  "schema": { ... },     ← 描述字段结构
  "payload": {
    "before": null,
    "after": "{"_id": 1, "name": "Alice", ...}",  ← 注意：这是个 JSON 字符串！
    "source": {
      "connector": "mongodb",
      "rs": "rs0",
      "db": "analytics",
      "collection": "user_behavior",
      "ord": 1,
      "ts_ms": 1700100000000,
      "snapshot": "false"
    },
    "op": "c",
    "ts_ms": 1700100000000
  }
}
```

**大师**："注意 `after` 字段——在 MySQL/PG Connector 中，after 是一个 **struct 结构体**（有确定的字段名和类型），但在 MongoDB Connector 中，after 是一个 **JSON 字符串**。Debezium 不对文档内容做任何结构推断，直接把整个 BSON 文档转成 JSON 字符串塞进 Change Event。这样做的好处是灵活性极高，代价是下游消费者需要自行解析这个 JSON 字符串。"

**技术映射**：`after` = 一个 JSON 编码的黑盒子。Debezium 承诺了外层的消息结构（schema + payload），但对内层的文档内容不做任何约束——这就是 MongoDB Schema-less 的 CDC 哲学。

**小胖**："那 Schema Registry 怎么办？如果每一条数据的内容都不同，Schema Registry 能管理吗？"

**大师**："Schema Registry 只管理外层的 Schema（固定的 `schema` + `payload` 结构），内层的 JSON 字符串被视为一个 `string` 类型的字段，所以 Schema Registry 中这个字段的 Avro 类型就是 `"type": "string"`。下游消费者拿到这个 string 后，自行 JSON.parse 即可。"

---

## 3. 项目实战

### 环境准备

在 docker-compose.yml 中添加 MongoDB 容器。

```bash
cd ~/debezium-lab

cat >> docker-compose.yml << 'EOF'
  mongodb:
    image: mongo:7.0
    container_name: mongodb
    ports:
      - "27017:27017"
    command: ["mongod", "--replSet", "rs0", "--bind_ip_all"]
    healthcheck:
      test: echo 'db.runCommand("ping").ok' | mongosh --quiet
      interval: 10s
      timeout: 5s
      retries: 5
EOF

docker compose up -d mongodb
sleep 15

# 初始化单节点 Replica Set
docker exec -it mongodb mongosh --eval 'rs.initiate({_id: "rs0", members: [{_id: 0, host: "mongodb:27017"}]})'

# 验证 Replica Set 状态
docker exec mongodb mongosh --eval 'rs.status().ok'
# 预期输出：1

# 插入测试数据
docker exec mongodb mongosh << 'EOF'
use analytics

db.user_behavior.insertMany([
  {
    user_id: "u001",
    action: "view",
    product_id: "p100",
    metadata: { source: "app", duration_seconds: 45 },
    timestamp: new Date()
  },
  {
    user_id: "u002",
    action: "purchase",
    product_id: "p200",
    amount: 299.99,
    metadata: { source: "web", coupon: "SAVE20" },
    timestamp: new Date()
  },
  {
    user_id: "u001",
    action: "search",
    query: "wireless headphones",
    results_count: 128,
    timestamp: new Date()
  }
])

// 创建 debezium 账号
use admin
db.createUser({
  user: "debezium",
  pwd: "dbz1234",
  roles: [
    { role: "read", db: "admin" },
    { role: "readAnyDatabase", db: "admin" }
  ]
})
EOF
```

### 步骤1：下载 MongoDB Connector 插件

```bash
cd ~/debezium-lab/plugins

wget https://repo1.maven.org/maven2/io/debezium/debezium-connector-mongodb/2.7.1.Final/debezium-connector-mongodb-2.7.1.Final-plugin.tar.gz
tar -xzf debezium-connector-mongodb-2.7.1.Final-plugin.tar.gz

# 重启 Connect
docker restart connect
sleep 30

# 验证 MongoDB Connector 已加载
curl http://localhost:8083/connector-plugins | python3 -c "import sys,json;[print(p['class']) for p in json.load(sys.stdin) if 'mongodb' in p['class']]"
# 预期：io.debezium.connector.mongodb.MongoDbConnector
```

### 步骤2：注册 MongoDB Connector

```bash
curl -X POST http://localhost:8083/connectors \
  -H "Content-Type: application/json" \
  -d '{
    "name": "mongo-behavior-connector",
    "config": {
      "connector.class": "io.debezium.connector.mongodb.MongoDbConnector",
      "mongodb.connection.string": "mongodb://debezium:dbz1234@mongodb:27017/?replicaSet=rs0",
      "topic.prefix": "mongo_analytics",
      "database.include.list": "analytics",
      "collection.include.list": "analytics.user_behavior",
      "snapshot.mode": "initial",
      "capture.mode": "change_streams_update_full",
      "heartbeat.interval.ms": "10000"
    }
  }'
```

**MongoDB 特有参数解析**：

| 参数 | 作用 |
|------|------|
| `mongodb.connection.string` | MongoDB 连接 URI，包含 replicaSet 参数 |
| `capture.mode` | `change_streams_update_full` = 变更流模式下，UPDATE 事件的 after 包含完整文档 |
| `collection.include.list` | 过滤集合，格式 `db.collection` |

### 步骤3：验证 MongoDB CDC 数据流

```bash
# 消费快照数据
docker exec kafka kafka-console-consumer --bootstrap-server localhost:9092 \
  --topic mongo_analytics.analytics.user_behavior --from-beginning --max-messages 3
```

**观察 MongoDB Change Event 的特点**：

```json
{
  "schema": {
    "type": "struct",
    "fields": [
      {"field": "after", "type": "string", "optional": true},
      {"field": "patch", "type": "string", "optional": true},
      {"field": "source": { ... }},
      {"field": "op", "type": "string"},
      {"field": "ts_ms", "type": "int64"}
    ]
  },
  "payload": {
    "after": "{\"_id\": {\"$oid\": \"6622a...\"}, \"user_id\": \"u001\", \"action\": \"view\", ...}",
    "patch": null,
    "source": {
      "connector": "mongodb",
      "rs": "rs0",
      "db": "analytics",
      "collection": "user_behavior"
    },
    "op": "r",
    "ts_ms": 1700100000000
  }
}
```

**关键差异**：
- `after` 是一个 JSON 字符串（BsonDocument → JSON）
- `_id` 使用 MongoDB Extended JSON 格式：`{"$oid": "6622a..."}`
- 事件中多了一个 `patch` 字段（用于部分更新，Change Streams 模式下使用）

### 步骤4：实时 DML 操作验证

```bash
# 终端1：启动实时消费
docker exec kafka kafka-console-consumer --bootstrap-server localhost:9092 \
  --topic mongo_analytics.analytics.user_behavior

# 终端2：MongoDB 实时操作
docker exec -it mongodb mongosh

use analytics

// INSERT
db.user_behavior.insertOne({
  user_id: "u003",
  action: "add_to_cart",
  product_id: "p300",
  quantity: 2,
  timestamp: new Date()
})

// UPDATE - 使用为 $set
db.user_behavior.updateOne(
  { user_id: "u003" },
  { $set: { quantity: 3, "metadata.coupon": "NEWYEAR" } }
)

// 嵌套文档更新 $push
db.user_behavior.updateOne(
  { user_id: "u001" },
  { $push: { tags: "electronics" } }
)

// DELETE
db.user_behavior.deleteOne({ user_id: "u003" })

exit
EOF
```

**终端1 收到的消息关键观察**：
- INSERT 事件：`op="c"`, `after` 包含完整 JSON 文档字符串
- UPDATE 事件：`op="u"`, `patch` 字段包含了变更的 diff（Change Streams 模式下）
- DELETE 事件：`op="d"`, `after=null`, `before` 非 null

### 可能遇到的坑及解决方法

| 坑 | 现象 | 根因与解决 |
|----|------|-----------|
| 单节点 MongoDB 报错 | `Oplog is not available` | 单节点需用 `rs.initiate()` 初始化 RS，或者改用 `capture.mode=change_streams` |
| 嵌套文档无法识别为 struct | after 是 JSON 字符串 | MongoDB 文档无固定 Schema，after 一定为字符串，需下游 JSON.parse |
| Oplog 追不上 | `Behind by X seconds` | ① 增大 Oplog size（`oplogSizeMB`）；② 减少不必要集合的同步 |
| Change Streams 模式下的 `patch` | UPDATE 的 after 是 partial 的 | 设置 `capture.mode=change_streams_update_full` 获取完整文档 |
| 认证失败 | `Authentication failed` | MongoDB 账号需 `readAnyDatabase` 角色的 `admin` 数据库权限（不是每个库单独的账号） |

---

## 4. 项目总结

### 优点 & 缺点（MongoDB Connector vs MySQL Connector）

| 维度 | MongoDB Connector | MySQL Connector |
|------|------------------|----------------|
| 捕获机制 | Oplog / Change Streams | binlog |
| Schema 管理 | after 为 JSON 字符串 | after 为强类型 struct |
| 部署前提 | Replica Set | 开启 binlog |
| 嵌套文档支持 | 原生 JSON | 通过 JSON 列类型 |
| 下游解析复杂度 | 需要二次 JSON.parse | 直接使用 struct 字段 |
| Change Streams 支持 | ✅（MongoDB 3.6+） | ❌ |

### 适用场景

1. **用户行为日志**：Web/App 行为数据的实时采集和推荐模型更新
2. **IoT 时序数据**：设备状态的实时变更推送到检测系统
3. **内容管理**：博客、电商商品等无固定 Schema 的文档变更
4. **日志聚合**：多服务日志通过 MongoDB → Kafka → ELK 实时聚合
5. **移动端同步**：MongoDB 的 Change Streams 是离线优先应用的核心同步机制

### 注意事项

- **Oplog 大小**：生产环境建议设置 `oplogSizeMB` 至少为 `(高峰写入速率 MB/s × 3600)`，即至少能覆盖 1 小时的写入量
- **Change Streams 版本要求**：MongoDB 3.6+ 支持 Change Streams，但 `updateLookup`（获取完整 UPDATE 文档）需要 MongoDB 4.0+
- **_id 的 Extended JSON 格式**：下游消费者需要用 extended JSON parser（如 MongoDB 的 `EJSON.parse()`）来解析 `{"$oid": "..."}` 格式

### 思考题

1. MongoDB 的更新操作中，`$set`、`$unset`、`$push`、`$pull` 在 Oplog 中分别对应什么记录格式？如果用 `capture.mode=change_streams`，这些操作在 Change Event 的 `patch` 字段中如何表示？

2. 假设你在一个 Replica Set 中有 3 个节点（1 Primary + 2 Secondary），Debezium 默认连接 Primary 的 Oplog。如果 Primary 宕机发生 Failover，Connector 如何自动切换到新 Primary？这个过程中会丢失数据吗？

**（第8章思考题答案）**

1. 如果 `max_wal_senders=5` 已满，新建的 Connector 会收到 PG 报错 `FATAL: remaining connection slots are reserved for non-replication superuser connections`，Connector 状态为 FAILED。预防措施：① `max_wal_senders` 至少为 `max_replication_slots + 2`（给 pg_basebackup 留空间）；② 监控 `pg_stat_replication` 视图中的 active 连接数，设置告警；③ 及时清理不活动的 Replication Slot（`pg_drop_replication_slot`）。

2. Slot 的 `active=false` 但 Connector RUNNING 的可能原因：① Connector 连接到了不同的 PG 实例（Slot 名相同但实例不同）；② Kafka Connect Task 的 `poll.interval.ms` 导致 Connector 当前不在消费周期内，短暂断连（心跳间隔）；③ PG 认为连接已经断开但 Connector 还没检测到（TCP 半开连接）。排查：`SELECT * FROM pg_stat_replication` 查看当前实际活跃的 WAL sender 连接，对比 Connector 日志中的连接状态。

---

> **推广提示**：对于使用 MongoDB 的团队，建议在部署文档中明确注明开发环境的 MongoDB 也必须以单节点 Replica Set 方式启动（`--replSet rs0`），避免团队成员在 Standalone 模式下调试 CDC 失败。DBA 应将 Oplog 大小监控纳入日常巡检。
