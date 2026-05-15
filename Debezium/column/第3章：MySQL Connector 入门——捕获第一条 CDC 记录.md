# 第3章：MySQL Connector 入门——捕获第一条 CDC 记录

## 1. 项目背景

"亲爱的用户，您的订单已发货——"每当你在电商 App 下完单，物流系统、库存系统、财务系统都需要立刻感知到这条新的订单数据。传统做法是业务代码里写一段逻辑：插入订单的同时调用库存系统的 RPC 接口扣减库存。看似完美，但一旦库存系统挂了，重试逻辑、补偿机制、数据对账...整个链路就变成了技术债的泥潭。

更好的方式是：业务代码只负责把订单写入数据库，剩下的"通知下游"工作交给 Debezium。当 `INSERT INTO orders` 执行后，MySQL 的 binlog 记录了这条变更，Debezium Connector 捕获到它，Kafka 广播给所有订阅者——库存系统自动扣库存、物流系统自动生成运单、财务系统自动记流水。业务代码一行额外的逻辑都不需要写，系统间的耦合被解耦为事件订阅。

这就是本章的目标：注册你的第一个 MySQL Connector，在 MySQL 中执行一条 INSERT，然后在 Kafka Consumer 中实时看到这条变更。

### 痛点放大

在没有 Debezium 的情况下，实现"订单创建后通知库存系统"的常见路线和问题：

- **路线A（应用层同步调用）**：`createOrder()` → `inventoryClient.deductStock()`。库存系统故障直接导致下单失败，这是经典的"分布式事务"困境。
- **路线B（应用层异步消息）**：`createOrder()` → `kafkaTemplate.send("order-topic", order)`。当业务复杂时，需要确保"数据库写入成功"和"消息发送成功"两个操作的原子性，否则会导致消息丢失或重复。
- **路线C（数据库触发器 + 定时任务）**：`AFTER INSERT trigger` → `写入 event_log 表` → `定时扫表发消息`。触发器影响数据库性能，定时任务延迟不可控。

Debezium 的方案解决了路径B的原子性问题——binlog 写入和数据变更是一体的，只要数据写入了数据库，binlog 就一定会记录，Debezium 就一定能捕获。

---

## 2. 项目设计——三人对话

**（周一早会，工位上）**

**小胖**："大师，我上周末把 Docker Compose 环境跑起来了，6 个容器全绿！但接下来怎么让 MySQL 的数据流到 Kafka 里，我还是不知道怎么下手。"

**大师**："恭喜你迈出了第一步。现在我给你讲一个故事——你开了一家奶茶店，每天打烊后要把当天的销售记录（MySQL 中的 orders 表）抄到账本（Kafka Topic）里。你的奶茶店有 100 种饮品，但你现在只想抄'波霸奶茶'的销售记录。你怎么做？"

**小胖**："我找个小本本（Connector），在上面写上：只关注 orders 这张表（table.include.list），从今天的第一笔开始抄（snapshot.mode），抄到哪里了做个记号（offset），抄完了继续看柜台有没有新单（streaming）。"

**大师**："完全正确。这就是注册 MySQL Connector 时需要告诉它的三件事：监听谁、从哪开始、数据写到哪。"

**小白**（突然抬头）："等等，我有个疑问。如果我在 Connector 启动的瞬间，订单表还在不停地被写入，会不会丢数据？比如快照读到第 1000 行的时候，正好有个 `INSERT` 写入了第 1001 行？"

**大师**："这个问题问到点子上了。Debezium 的快照过程有个很巧妙的设计——**全局读锁 + binlog 位点快照**。快照开始前，Connector 会先记录当前 binlog 的位点（比如 mysql-bin.000003:45678），然后开始读全表数据。快照期间新写入的数据（包括刚才说的第 1001 行）会被 binlog 记录下来，等快照完成后，Connector 会从刚才记录的位点开始消费 binlog。这样，快照期间的新数据一个都不会丢。"

**小白**："这相当于——先把门关上（加锁），给此刻的人都拍张照（全量快照），然后开门让新来的人进来（解锁），同时在新来的人额头上盖个章（binlog 记录），最后对着照片一个一个核对有没有章（binlog 消费去重）。"

**大师**（笑）："你这个比喻比我那个还好。不过注意，`snapshot.locking.mode` 可以控制用的是全局锁（`FLUSH TABLES WITH READ LOCK`）还是表锁，甚至可以用 `minimal` 模式只锁很短时间。我们马上就用 `minimal`。"

**小胖**："那我来总结一下流程表：第一步，通过 REST API 向 Kafka Connect 注册 MySQL Connector，告诉它数据库地址、用户名密码、监控哪些表。第二步，Connector 启动后先跑快照，把 orders 表现有的 3 条数据变成 3 条 Kafka 消息。第三步，快照跑完后自动切换到 streaming 模式，之后我在 MySQL 里做任何增删改，Kafka 里都会实时出现对应的消息。对吗？"

**大师**："完全正确。记住这个三段式流程——注册 → 快照 → 流式。现在我们来实战。"

**技术映射**：Connector 注册（POST /connectors）= 告诉 Kafka Connect "我要监听这个数据库"；快照（Snapshot）= MySQL 现有数据的批量导出；流式（Streaming）= 增量数据实时捕获；Offset = Connector 读到 binlog 的第几行，方便断点续传。

---

## 3. 项目实战

### 环境准备

确认上一章的 Docker Compose 环境已全部启动且健康。

```bash
# 前置检查
docker ps --format "table {{.Names}}\t{{.Status}}"
# 预期输出：7 个容器均为 Up
```

### 步骤1：注册 MySQL Connector

**目标**：通过 REST API 注册第一个 Debezium MySQL Connector。

```bash
# 发送 POST 请求到 Kafka Connect REST API
curl -X POST http://localhost:8083/connectors \
  -H "Content-Type: application/json" \
  -d '{
    "name": "inventory-connector",
    "config": {
      "connector.class": "io.debezium.connector.mysql.MySqlConnector",
      "database.hostname": "mysql",
      "database.port": "3306",
      "database.user": "debezium",
      "database.password": "dbz1234",
      "database.server.id": "184054",
      "topic.prefix": "dbserver1",
      "database.include.list": "inventory",
      "table.include.list": "inventory.orders",
      "schema.history.internal.kafka.bootstrap.servers": "kafka:9092",
      "schema.history.internal.kafka.topic": "schema-changes.inventory",
      "snapshot.mode": "initial",
      "snapshot.locking.mode": "minimal",
      "decimal.handling.mode": "double"
    }
  }'
```

**参数说明**：

| 参数 | 作用 | 为什么这么配 |
|------|------|-------------|
| `connector.class` | 指定使用 MySQL Connector | 固定值，告诉 Kafka Connect 加载哪个 Connector 实现 |
| `database.server.id` | MySQL binlog slave 的唯一 ID | 每个 Connector 必须不同，避免 binlog 协议层面的冲突 |
| `topic.prefix` | Kafka Topic 前缀 | 所有 Topic 都会加上这个前缀，如 `dbserver1.inventory.orders` |
| `snapshot.mode=initial` | 首次启动时执行全量快照 | 适合新建的 CDC 链路 |
| `snapshot.locking.mode=minimal` | 只在读取表结构时加锁 | 减少锁持有时间，对生产影响最小 |

**预期输出**：
```json
{
  "name": "inventory-connector",
  "config": { ... },
  "tasks": [{"connector": "inventory-connector", "task": 0}],
  "type": "source"
}
```

### 步骤2：验证快照阶段

**目标**：确认 orders 表中的 3 条初始数据已被捕获为 Kafka 消息。

```bash
# 查看 Kafka Topic 列表
docker exec kafka kafka-topics --bootstrap-server localhost:9092 --list

# 预期输出（Debezium 自动创建了 Topic）：
# connect-configs
# connect-offsets
# connect-statuses
# dbserver1.inventory.orders
# schema-changes.inventory

# 消费 dbserver1.inventory.orders Topic（从最早的消息开始）
docker exec kafka kafka-console-consumer \
  --bootstrap-server localhost:9092 \
  --topic dbserver1.inventory.orders \
  --from-beginning \
  --max-messages 3
```

**预期输出**（3 条消息，对应 orders 表的 3 行初始数据）：

```json
{
  "schema": {...},
  "payload": {
    "before": null,
    "after": {
      "id": 1,
      "customer_id": 1001,
      "product_name": "iPhone 15",
      "quantity": 1,
      "price": 7999.0,
      "status": "completed"
    },
    "source": {
      "version": "2.7.1.Final",
      "connector": "mysql",
      "name": "dbserver1",
      "ts_ms": 1700000000000,
      "snapshot": "true",
      "db": "inventory",
      "table": "orders",
      "server_id": 1,
      "file": "mysql-bin.000003",
      "pos": 1234,
      "row": 0
    },
    "op": "r",
    "ts_ms": 1700000000000
  }
}
```

**消息结构解析**（后续第4章将深度展开）：
- `op: "r"` = 快照读取（read），表示这条数据来自全量快照
- `before: null` = 因为是 INSERT 型数据，没有"修改前"的值
- `source.snapshot: "true"` = 标记这是一条快照阶段的数据
- `source.file & pos` = binlog 文件名和位点（offset）

### 步骤3：验证流式 CDC 阶段

**目标**：在 MySQL 中执行实时 DML 操作，在 Kafka 中秒级收到变更事件。

```bash
# 终端1：开始实时消费（不指定 --from-beginning，只消费最新消息）
docker exec kafka kafka-console-consumer \
  --bootstrap-server localhost:9092 \
  --topic dbserver1.inventory.orders
```

```bash
# 终端2：在 MySQL 中执行 DML 操作
docker exec -it mysql mysql -uroot -proot1234 inventory

# INSERT 操作
INSERT INTO orders (customer_id, product_name, quantity, price, status)
VALUES (1004, 'Apple Watch', 1, 3499.00, 'pending');

# UPDATE 操作
UPDATE orders SET status = 'completed' WHERE id = 4;

# DELETE 操作
DELETE FROM orders WHERE id = 4;
```

**终端1 预期输出**（每次 DML 操作后立刻出现一条 Kafka 消息）：

```json
// INSERT 事件
{"payload":{"before":null,"after":{"id":4,"customer_id":1004,...},"op":"c",...}}

// UPDATE 事件
{"payload":{"before":{"id":4,"status":"pending",...},"after":{"id":4,"status":"completed",...},"op":"u",...}}

// DELETE 事件
{"payload":{"before":{"id":4,...},"after":null,"op":"d",...}}
```

### 步骤4：确认 Connector 状态

**目标**：学会查看 Connector 和 Task 的运行状态。

```bash
# 查看所有 Connector
curl http://localhost:8083/connectors
# 预期输出：["inventory-connector"]

# 查看 Connector 状态
curl http://localhost:8083/connectors/inventory-connector/status | python3 -m json.tool

# 预期输出：
{
  "name": "inventory-connector",
  "connector": {
    "state": "RUNNING",
    "worker_id": "connect:8083"
  },
  "tasks": [{
    "id": 0,
    "state": "RUNNING",
    "worker_id": "connect:8083"
  }],
  "type": "source"
}
```

**关键状态值**：
- `RUNNING`：正常运行，一切 OK
- `PAUSED`：已被暂停，等待手动恢复
- `FAILED`：已故障，需要查看日志排查
- `UNASSIGNED`：刚创建，尚未被分配到 Worker

### 可能遇到的坑及解决方法

| 坑 | 现象 | 根因 | 解决方法 |
|----|------|------|---------|
| `table.include.list` 不生效 | Kafka 中收到所有表的变更 | `table.include.list` 拼写错误或被覆盖 | 检查配置 JSON 中参数名是否正确（注意下划线位置） |
| 快照阶段无数据 | Kafka 中有 Topic 但无消息 | Connector 还未完成快照 | `GET /connectors/inventory-connector/status` 查看 task state 是否为 `RUNNING` |
| `Access denied` | Connector 状态 FAILED | debezium 用户缺少 REPLICATION 权限 | 在 MySQL 中 `SHOW GRANTS FOR 'debezium'@'%'` 检查权限 |
| server_id 重复 | `A slave with the same server_uuid/server_id` 错误 | `database.server.id` 与其他 slave 冲突 | 更换为唯一值（如 Unix 时间戳后 6 位） |

---

## 4. 项目总结

### 优点 & 缺点

| 维度 | Debezium MySQL Connector | Canal (阿里) | Maxwell |
|------|------------------------|--------------|---------|
| 多数据库支持 | ★★★★★ 支持多种数据库 | ★☆☆☆☆ 仅 MySQL | ★☆☆☆☆ 仅 MySQL |
| 标准化消息格式 | ★★★★★ Kafka Connect 原生 | ★★★☆☆ 自定义 Protobuf | ★★★☆☆ 自定义 JSON |
| Schema 管理 | ★★★★★ Schema Registry | ★★☆☆☆ 无 | ★☆☆☆☆ 无 |
| 活跃度 & 社区 | ★★★★★ 非常活跃 | ★★★★☆ 活跃 | ★★☆☆☆ 逐渐沉寂 |
| 部署复杂度 | ★★★☆☆ 依赖 Kafka | ★★★★☆ 独立进程 | ★★★★★ 独立进程，极简 |
| MySQL DDL 支持 | ★★★★★ 自动追踪 | ★★★★☆ 部分支持 | ★★★☆☆ 部分支持 |

### 适用场景

1. **交易系统解耦**：订单创建后自动触发库存、物流、通知等下游服务
2. **多系统数据同步**：ERP、CRM、WMS 等异构系统间的数据实时同步
3. **审计与合规**：完整记录所有数据变更历史，满足 FDA/SOC2 等合规要求
4. **实时数据同步到数仓**：OLTP → OLAP 的实时 ETL 管道
5. **缓存自动刷新**：MySQL 数据更新后自动同步到 Redis 缓存

### 注意事项

- **binlog_format=ROW**：这是硬性要求，如果用 STATEMENT 或 MIXED，Debezium 无法解析出具体变更的行数据
- **binlog_row_image=FULL**：推荐使用 FULL，确保 UPDATE 的 before 字段包含完整行数据
- **`database.server.id` 全局唯一**：每个 Connector 配置不同的 server.id，避免 MySQL binlog 协议的 slave id 冲突

### 常见踩坑经验

1. **"快照跑了一半，磁盘满了"**：快照按表维度依次处理，大表全量快照可能产生数 GB 的 Kafka 消息。建议为 `.snapshot.` 前缀的 Topic 设置较短的数据保留时间（`retention.ms`）。
2. **"UPDATE 操作在 Kafka 中只看到了 after，没看到 before"**：这是 `binlog_row_image=MINIMAL` 导致的。MINIMAL 模式只记录变更的列，不记录整行。修改为 `FULL` 或 `NOBLOB`。
3. **"MySQL 重启后 Connector 无法恢复"**：检查 `schema.history.internal.kafka.topic` 是否配置正确且 Kafka 中该 Topic 数据未被清理。

### 思考题

1. 如果 orders 表有 1 亿行数据，`snapshot.mode=initial` 的快照大概需要多久？你可以用什么参数加速快照过程？（提示：`snapshot.fetch.size` 和 `max.batch.size`）

2. 快照期间，如果对 orders 表执行了 `ALTER TABLE orders ADD COLUMN discount DECIMAL(10,2)`，已经完成快照的 5000 万行数据和未完成的 5000 万行数据会分别以什么 Schema 写入 Kafka？Schema Registry 如何处理这个变更？

**（第2章思考题答案）**

1. 如果 `KAFKA_OFFSETS_TOPIC_REPLICATION_FACTOR=3` 且只有单节点 Kafka Broker，启动会失败。因为 replication factor 不能超过 ISR（In-Sync Replicas）中的 broker 数量，单节点的 ISR 最多为 1，所以 replication factor 不能大于 1。Kafka 会在创建 Topic 时校验这个规则。
2. 切换到 Avro Converter 需要：① 将 `CONNECT_KEY_CONVERTER` 和 `CONNECT_VALUE_CONVERTER` 改为 `io.confluent.connect.avro.AvroConverter`；② 添加 `CONNECT_KEY_CONVERTER_SCHEMA_REGISTRY_URL` 和 `CONNECT_VALUE_CONVERTER_SCHEMA_REGISTRY_URL` 指向 Schema Registry；③ 确保 Schema Registry 容器已启动且 Kafka 可达。

---

> **推广提示**：建议运维团队将本章的 Connector POST JSON 模板化为配置文件，纳入配置管理（如 Git + Jenkins），避免每次手动 curl 粘贴。开发团队可将 kafka-console-consumer 消费确认步骤加入 CI 流水线作为冒烟测试。
