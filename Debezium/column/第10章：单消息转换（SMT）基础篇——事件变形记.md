# 第10章：单消息转换（SMT）基础篇——事件变形记

## 1. 项目背景

"大师，下游的数据分析团队抱怨说我们的 Kafka 消息太'重'了——外层包着 `schema` 和 `payload`，里面嵌套着 `before`、`after`、`source`，他们只想要 `after` 里面的数据，其他的都不需要。他们让我在代码里帮他们过滤..."

小胖的烦恼非常典型。Debezium 产出的 Change Event 结构丰富而完整，但并非所有下游消费者都需要全部字段。一个数据分析团队可能只需要 `after.id + after.price + after.status` 三个字段；一个缓存服务可能只需要 `after` 的全部内容来更新 Redis；一个审计系统则除了 `after` 还要 `before` 和 `source.db`。

如果不加处理，每个下游消费者都要各自解析、过滤、变形，这导致两个问题：一是代码重复，不同团队重复写同样的"从 Kafka 消息里提取 after"的代码；二是下游无法利用 Kafka 的分区有序性（因为分区键默认是表主键，但下游需要按 `user_id` 分区）。

**SMT（Single Message Transformation，单消息转换）** 就是解决这些问题的瑞士军刀。它在 Connector 投递消息到 Kafka **之前**，对每条消息执行一次变形操作，下游无感知。本章将掌握最常用的 5 个 SMT：ExtractNewRecordState（拍平）、SetSchemaMetadata（改 Schema 名）、Flatten（扁平化）、Cast（类型转换）、HeaderFrom（消息头提取）。

### 痛点放大

没有 SMT 的典型问题：
- **下游解析冗余**：每个消费者都要解析 `payload.after`，100 个消费者写了 100 遍相同的解析代码
- **带宽浪费**：`schema` 字段在每条消息中重复（JSON 模式下），对于高频更新的表，一天下来浪费的带宽可能达 GB 级
- **分区键不合理**：默认按 `id` 分区，但下游需要按 `user_id` 做聚合，导致 Flink 算子上的 key skew
- **安全泄露风险**：敏感字段（密码、手机号）随消息发给所有下游消费者，某些消费者不应该看到这些字段

---

## 2. 项目设计——三人对话

**（周一早会，小胖对着 JIRA 上的一排需求发呆）**

**小胖**："大师，我手上有三个需求同时压过来——数据分析团队要 after 数据，缓存团队要 after 但不要 source，安全团队要我删掉消息里的手机号字段。这三个需求在代码里改太累了，有没有办法在消息发出去之前就搞定？"

**大师**："SMT 就是干这个的。你可以把 SMT 想象成一条流水线——Change Event 在产品路上经过一个个工位，每个工位（SMT）做一件事：第一工位拆包装（ExtractNewRecordState），第二工位贴标签（SetSchemaMetadata），第三工位质检过滤（Filter），最后打包发货（投递到 Kafka Topic）。"

**小白**："那一个 Connector 可以串多个 SMT 吗？串的顺序重要吗？"

**大师**："可以串，而且**顺序至关重要**！比如你先用 `Filter` 过滤掉 DELETE 事件，再用 `ExtractNewRecordState` 拍平消息。但如果你先 `ExtractNewRecordState` 拍平了消息，`op` 字段就被移除了，`Filter` 就找不到过滤条件了。"

```
SMT 链式执行的正确姿势：
┌──────────────┐    ┌──────────────┐    ┌──────────────┐
│   原始事件    │───▶│  SMT 1:      │───▶│  SMT 2:      │───▶ Kafka
│              │    │  Filter      │    │  Extract     │    Topic
│ {schema:{},  │    │  (按 op 过滤) │    │  NewRecord   │
│  payload:{   │    │              │    │  State       │
│   before:..  │    └──────────────┘    └──────────────┘
│   after:..   │
│   op:"d"}}   │
└──────────────┘

警告：如果先 Extract，op 字段就没了，Filter 无法工作！
```

**小胖**："那到底 SMT 怎么配置？我看上一章讲 Topic 路由时好像用了个叫 'transforms' 的参数？"

**大师**："对，SMT 都通过 JSON 中的 `transforms` 字段配置。格式是这样——"

```json
{
  "config": {
    "transforms": "step1,step2,step3",
    "transforms.step1.type": "类的完整包名",
    "transforms.step1.参数字段": "参数值",
    "transforms.step2.type": "...",
    ...
  }
}
```

**大师**："每个 SMT 必须有 `type` 字段指定实现类。多个 SMT 之间用逗号分隔，执行顺序就是你在 `transforms` 里写的顺序。SMT 之间的数据流向是——step1 的输出 = step2 的输入。"

**小白**："那我们现在掌握最常用的 5 个 SMT 就够了，对吧？"

**大师**："对，我们先从最核心的 `ExtractNewRecordState` 开始——"

### SMT #1：ExtractNewRecordState（拍平）

**作用**：去掉 Change Event 的外层 `schema` + `payload` 包装，提取 `after` 字段作为消息体。

**配置**：
```json
{
  "transforms": "unwrap",
  "transforms.unwrap.type": "io.debezium.transforms.ExtractNewRecordState",
  "transforms.unwrap.drop.tombstones": "true",
  "transforms.unwrap.delete.handling.mode": "rewrite"
}
```

**效果**：
- 转换前：`{"schema":{...}, "payload":{"before":..., "after":{"id":1, "name":"Alice"}}}`
- 转换后：`{"id":1, "name":"Alice"}`

### SMT #2：Filter（过滤）

**作用**：按条件丢弃不符合条件的 Change Event。

```json
{
  "transforms": "filterOut",
  "transforms.filterOut.type": "io.debezium.transforms.Filter",
  "transforms.filterOut.language": "jsr223.groovy",
  "transforms.filterOut.condition": "value.op == 'd' || value.after.status == 'deleted'"
}
```

### SMT #3：ValueToKey（提取分区键）

**作用**：从消息字段提取 Kafka 消息 Key，控制分区。

```json
{
  "transforms": "setKey",
  "transforms.setKey.type": "org.apache.kafka.connect.transforms.ValueToKey",
  "transforms.setKey.fields": "user_id"
}
```

### SMT #4：HoistField（字段提升）

**作用**：将嵌套字段提升为顶层字段。

```json
{
  "transforms": "hoist",
  "transforms.hoist.type": "org.apache.kafka.connect.transforms.HoistField$Value",
  "transforms.hoist.field": "after"
}
```

### SMT #5：ReplaceField（字段过滤/重命名）

**作用**：只保留/排除特定字段。

```json
{
  "transforms": "dropFields",
  "transforms.dropFields.type": "org.apache.kafka.connect.transforms.ReplaceField$Value",
  "transforms.dropFields.exclude": "password,ssn,phone"
}
```

---

## 3. 项目实战

### 步骤1：ExtractNewRecordState——去掉外层包装

**目标**：将 Change Event 从 `{schema, payload}` 格式转换为纯 `after` 字段。

```bash
# 创建 Connector，配置 ExtractNewRecordState
curl -X POST http://localhost:8083/connectors \
  -H "Content-Type: application/json" \
  -d '{
    "name": "smt-unwrap-connector",
    "config": {
      "connector.class": "io.debezium.connector.mysql.MySqlConnector",
      "database.hostname": "mysql",
      "database.port": "3306",
      "database.user": "debezium",
      "database.password": "dbz1234",
      "database.server.id": "184101",
      "topic.prefix": "smt_unwrap",
      "table.include.list": "inventory.orders",
      "schema.history.internal.kafka.bootstrap.servers": "kafka:9092",
      "schema.history.internal.kafka.topic": "schema-changes.smt-unwrap",
      "snapshot.mode": "initial",
      "transforms": "unwrap",
      "transforms.unwrap.type": "io.debezium.transforms.ExtractNewRecordState",
      "transforms.unwrap.drop.tombstones": "false",
      "transforms.unwrap.delete.handling.mode": "rewrite"
    }
  }'

# 消费并对比——不配置 SMT vs 配置 ExtractNewRecordState 的消息差异
docker exec mysql mysql -uroot -proot1234 inventory -e "
  INSERT INTO orders (customer_id, product_name, quantity, price, status) VALUES (5001, 'SMT Test', 1, 99.00, 'pending');
"

# 消费 smt_unwrap.inventory.orders Topic
docker exec kafka kafka-console-consumer --bootstrap-server localhost:9092 \
  --topic smt_unwrap.inventory.orders --max-messages 1
```

**预期输出**（对比）：
```json
// 无 SMT 时的消息：
{"schema":{...},"payload":{"before":null,"after":{"id":6,"customer_id":5001,...},"op":"c",...}}

// 有 ExtractNewRecordState 后的消息：
{"id":6,"customer_id":5001,"product_name":"SMT Test","quantity":1,"price":99.0,"status":"pending","__db":"inventory","__table":"orders","__deleted":"false"}
```

**关键变化**：
- 最外层 `schema` + `payload` 消失
- `after` 的内容被提升为顶层
- 自动添加了 `__db`、`__table`、`__deleted` 三个元数据字段
- DELETE 事件会添加 `__deleted: "true"` 的墓碑标记

### 步骤2：ReplaceField——排除敏感字段

**目标**：通过 SMT 在消息投递前排除 password、phone 等敏感字段。

```bash
# 先在 MySQL 中添加带敏感字段的表
docker exec mysql mysql -uroot -proot1234 inventory << 'SQL'
CREATE TABLE IF NOT EXISTS user_accounts (
    id INT PRIMARY KEY AUTO_INCREMENT,
    username VARCHAR(100) NOT NULL,
    email VARCHAR(255),
    password VARCHAR(255) NOT NULL,
    phone VARCHAR(20),
    ssn VARCHAR(20),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
INSERT INTO user_accounts (username, email, password, phone, ssn) VALUES
('john_doe', 'john@example.com', 'hashed_pw_123', '13800138000', '123-45-6789');
SQL

# 创建 Connector，使用 ExtractNewRecordState + ReplaceField 链
curl -X DELETE http://localhost:8083/connectors/smt-unwrap-connector 2>/dev/null

curl -X POST http://localhost:8083/connectors \
  -H "Content-Type: application/json" \
  -d '{
    "name": "smt-sensitive-connector",
    "config": {
      "connector.class": "io.debezium.connector.mysql.MySqlConnector",
      "database.hostname": "mysql",
      "database.port": "3306",
      "database.user": "debezium",
      "database.password": "dbz1234",
      "database.server.id": "184102",
      "topic.prefix": "smt_sensitive",
      "table.include.list": "inventory.user_accounts",
      "schema.history.internal.kafka.bootstrap.servers": "kafka:9092",
      "schema.history.internal.kafka.topic": "schema-changes.smt-sensitive",
      "snapshot.mode": "initial",
      "transforms": "unwrap,dropSensitive",
      "transforms.unwrap.type": "io.debezium.transforms.ExtractNewRecordState",
      "transforms.unwrap.delete.handling.mode": "rewrite",
      "transforms.dropSensitive.type": "org.apache.kafka.connect.transforms.ReplaceField$Value",
      "transforms.dropSensitive.exclude": "password,ssn"
    }
  }'

# 消费验证——确认 password 和 ssn 不在消息中
docker exec kafka kafka-console-consumer --bootstrap-server localhost:9092 \
  --topic smt_sensitive.inventory.user_accounts --from-beginning --max-messages 1
# 预期：消息中只有 id, username, email, phone，不包含 password 和 ssn
```

### 步骤3：ValueToKey + ExtractNewRecordState——自定义分区键

**目标**：按 `customer_id` 分区，确保同一客户的数据有序消费。

```bash
curl -X POST http://localhost:8083/connectors \
  -H "Content-Type: application/json" \
  -d '{
    "name": "smt-partition-connector",
    "config": {
      "connector.class": "io.debezium.connector.mysql.MySqlConnector",
      "database.hostname": "mysql",
      "database.port": "3306",
      "database.user": "debezium",
      "database.password": "dbz1234",
      "database.server.id": "184103",
      "topic.prefix": "smt_partition",
      "table.include.list": "inventory.orders",
      "schema.history.internal.kafka.bootstrap.servers": "kafka:9092",
      "schema.history.internal.kafka.topic": "schema-changes.smt-partition",
      "snapshot.mode": "initial",
      "transforms": "setKey,unwrap",
      "transforms.setKey.type": "org.apache.kafka.connect.transforms.ValueToKey",
      "transforms.setKey.fields": "customer_id",
      "transforms.unwrap.type": "io.debezium.transforms.ExtractNewRecordState",
      "transforms.unwrap.delete.handling.mode": "rewrite"
    }
  }'

# 注意 SMT 顺序：先 setKey（设置 Key），再 unwrap（拍平消息）
# 因为 unwrap 后消息结构变了，ValueToKey 的 fields 引用也需要调整
```

### 步骤4：SMT 顺序的"踩坑实验"

**目标**：验证 SMT 顺序错误时的行为。

```bash
# 错误顺序：先 unwrap 再 filter
curl -X POST http://localhost:8083/connectors \
  -H "Content-Type: application/json" \
  -d '{
    "name": "smt-order-test",
    "config": {
      "connector.class": "io.debezium.connector.mysql.MySqlConnector",
      "database.hostname": "mysql",
      "database.port": "3306",
      "database.user": "debezium",
      "database.password": "dbz1234",
      "database.server.id": "184104",
      "topic.prefix": "smt_order",
      "table.include.list": "inventory.orders",
      "schema.history.internal.kafka.bootstrap.servers": "kafka:9092",
      "schema.history.internal.kafka.topic": "schema-changes.smt-order",
      "snapshot.mode": "initial",
      "transforms": "unwrap,filterDel",
      "transforms.unwrap.type": "io.debezium.transforms.ExtractNewRecordState",
      "transforms.unwrap.delete.handling.mode": "rewrite",
      "transforms.filterDel.type": "io.debezium.transforms.Filter",
      "transforms.filterDel.language": "jsr223.groovy",
      "transforms.filterDel.condition": "value.op == 'd'"
    }
  }'

# 执行一个 DELETE 操作
docker exec mysql mysql -uroot -proot1234 inventory -e "DELETE FROM orders WHERE id=(SELECT MAX(id) FROM orders);"
# 观察 Connector 日志——因为 unwrap 后 op 字段已经不在消息中，Filter 可能报错或无效
```

### 可能遇到的坑及解决方法

| 坑 | 现象 | 根因与解决 |
|----|------|-----------|
| SMT 顺序错误 | 预期过滤/变形不生效 | 按执行顺序调整 `transforms` 列表 |
| ExtractNewRecordState 后字段名变化 | ReplaceField 排除字段不生效 | Unwrap 后字段名是 after 中的字段名，确认拼写一致 |
| Filter 需要 Groovy/JEXL | Connector 启动报 ClassNotFound | Debezium 2.7 默认不打包 Groovy，改用 JEXL：`transforms.xxx.language=jsr223.jexl` |
| `__deleted` 字段影响下游插入 | 下游把 `__deleted: "false"` 当成了数据列 | 使用 ReplaceField 排除 `__deleted` 和 `__db` `__table` |

---

## 4. 项目总结

### 优点 & 缺点

| SMT | 优点 | 缺点 |
|-----|------|------|
| ExtractNewRecordState | 简化下游解析，减少 60%+ 消息体积 | DELETE 事件处理需额外配置 `delete.handling.mode` |
| ReplaceField | 字段级精确控制 | 黑白名单不能同时使用 include + exclude |
| ValueToKey | 自定义分区键，提升下游有序性 | 修改分区键后需要重新分配分区 |
| Filter | 按条件精准丢弃 | Groovy/JEXL 表达式调试困难 |
| HoistField | 简化嵌套结构 | 一次只提升一个字段，多层嵌套需多次调用 |

### 适用场景

1. **下游消费者为 REST API 服务**：ExtractNewRecordState 产出纯 JSON，无需解析 Debezium 格式
2. **安全合规**：ReplaceField 自动排除敏感字段，符合 GDPR/等保
3. **Flink 流处理**：ValueToKey 按 `user_id` 分区，确保同一用户的事件在 Flink 中有序
4. **数据清洗**：Filter 丢弃软删除记录（`status='deleted'`），减少下游处理压力
5. **日志类数据**：HoistField 将 error_log 的内部字段提升，方便 ELK 索引

### 注意事项

- **SMT 执行在 Connector 的 Source Task 线程内**：如果 SMT 逻辑过重（如网络调用、复杂计算），会拖慢 CDC 速度
- **SMT 是单消息的**：无法做"GROUP BY"类的聚合操作，只能对每条消息独立变形
- **SMT 链最大长度**：虽然没有硬性限制，但超过 5 个 SMT 时应考虑是否适合使用 Kafka Streams 替代

### 思考题

1. 写一个 SMT 链配置，实现：对 orders 表的 Change Event，① 过滤掉 status='cancelled' 的记录；② 拍平消息（ExtractNewRecordState）；③ 删除 __db 和 __table 字段；④ 按 order_id 分区。

2. 如果下游需要同时消费 `before` 和 `after`（比如做前后对比），使用 ExtractNewRecordState 还合适吗？如果不合适，有什么替代方案？

**（第9章思考题答案）**

1. MongoDB 的 Oplog 操作对应关系：`$set` → `{op:"u", o:{$set:{field:value}}}`；`$unset` → `{op:"u", o:{$unset:{field:1}}}`；`$push` → `{op:"u", o:{$set:{"array.field": value}}}`；`$pull` → `{op:"u", o:{$set:{"array.field": removed_value}}}`。Change Streams 模式下，这些操作在 `patch` 字段中以 `updateDescription` 格式表示：`{"updatedFields": {"field": "new_value"}, "removedFields": ["old_field"]}`。

2. Primary 宕机后，Replica Set 自动选举新 Primary（通常 < 12 秒）。Connector 的 MongoDB 驱动会检测到连接断开，重新执行 `isMaster` 发现新 Primary，自动重连到新 Primary 的 Oplog。这个过程中 Connector 通过 offset 中记录的 `resume_token`（Change Streams 模式）或 `ts`（Oplog 模式）恢复到断开点。不会丢失数据——因为 Oplog 在所有 RS 节点之间是同步的（通过 Raft 协议保证），新 Primary 的 Oplog 包含了所有已提交的操作。

---

> **推广提示**：建议架构团队在制定数据契约时，同意使用 ExtractNewRecordState 作为标准 SMT，减少下游团队的解析成本。安全团队应制定 SMT 字段脱敏规范，将 ReplaceField 排除敏感字段作为所有 Connector 的硬性要求。
