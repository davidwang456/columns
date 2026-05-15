# 第7章：Topic 路由与命名策略——多库多表数据分流

## 1. 项目背景

"大师，我们生产环境有 6 个业务数据库，每个库上跑 30-50 张表。我用 Debezium 把这些表都同步到了 Kafka，结果下游团队投诉说 Topic 多得眼花缭乱，每个 `dbserver1.order_db.orders` 这种命名又臭又长。更麻烦的是，财务团队只需要 orders 表和 payments 表的数据，但他们不得不订阅所有 Topic 然后自己过滤..."

小胖遇到的问题暴露了 Debezium 默认 Topic 命名策略的局限性。默认情况下，Debezium 会给每张表自动创建一个 Topic，命名为 `<topic.prefix>.<database_name>.<table_name>`。这种"一表一 Topic"的策略在小规模场景下很直观，但当表数量增长到 100+ 时，Topic 的数量膨胀导致两个严重问题：一是 Kafka 的分区管理开销过大（每个 Topic 最少 1 个分区，1000 个 Topic = 1000 个分区元数据）；二是下游消费者难以按照业务语义订阅（比如"订阅所有订单相关表"），只能用前缀匹配。

本章将掌握 Debezium 的 Topic 路由能力——从默认命名到自定义映射，从单 Topic 到多 Topic 分流，让你在多库多表的场景下自如地组织数据流。

### 痛点放大

默认 Topic 路由策略引发的问题：

- **Topic 爆炸**：200 张表 → 200 个 Topic → Kafka 元数据管理压力大 → Zookeeper/Broker 内存飙升
- **命名混乱**：`dbserver1` 这种前缀无法表达业务语义，下游团队不知道它代表的是哪个系统
- **无法按业务维度订阅**：算法团队需要所有"用户行为"相关的变更，但 user_actions、user_clicks、user_views 分布在不同的 Topic 上，无法用一个订阅规则覆盖
- **安全隔离困难**：如果 A 项目和 B 项目共用同一个 Kafka 集群，A 的数据可能被 B 看到——Topic 级别的 ACL 控制粒度太粗

---

## 2. 项目设计——三人对话

**（午饭后，小胖在工位上闷闷不乐）**

**小胖**："大师，Topic 命名能不能改一下？上个项目我们的 Topic 有 200 多个，查找 Topic 就像在垃圾堆里找一只袜子..."

**大师**："当然可以。Debezium 有四种 Topic 路由策略，你可以按需组合——"

```
Topic 路由策略矩阵

┌────────────────┬───────────────────┬─────────────────┐
│  策略           │  说明              │  粒度            │
├────────────────┼───────────────────┼─────────────────┤
│ 1. 默认路由     │ server.db.table   │ 表级             │
│ 2. 自定义前缀   │ topic.prefix=xxx  │ Connector 级     │
│ 3. tableTopic   │ 手动映射表→Topic   │ 表级             │
│ 4. RegexRouter  │ 正则替换 Topic 名  │ 规则级            │
└────────────────┴───────────────────┴─────────────────┘
```

**小白**："大师，如果我 6 个库的订单表都叫 `orders`，能把它们都路由到 `topic.orders.all` 这一个 Topic 里吗？"

**大师**："这正是 `tableTopic` 或 `RerouteRouter` 的典型场景。`tableTopic` 是一种声明式映射——在配置里写：`topic.consumers.orders: orders_all`，表示把 inventory 库里 consumers 库里的 orders 表映射到 `orders_all` 这个 Topic。多个库的 orders 表都可以指向同一个 Topic，Kafka 里就会出现所有 orders 表的变更数据。"

**小胖**："那下游怎么区分哪条消息来自哪个数据库？"

**大师**："每个 Change Event 的 `source.db` 字段记录了它来自哪个数据库，`source.server` 字段记录了它来自哪个服务器。下游消费时可以根据这两个字段做进一步路由。不过要注意——如果把多库的数据混入同一个 Topic，Topic 的分区键（partition key）的设计就变得非常关键。默认分区键是主键，但如果两个库的 id 可能重复，就会导致同 id 但不同库的数据被分到同一个分区，打破了分区内的局部有序性。"

**小胖**（挠头）："分区...有序性...分我不太懂。能不能举个我听得懂的例子？"

**大师**："好，我们把 Topic 想象成商场的服务台，分区就是服务窗口。默认规则是'同一个顾客（同一个 id）去同一个窗口'。但如果顾客小明（id=1001）同时出现在商场 A 座（database=inventory）和 B 座（database=warehouse），系统认为 'id=1001' 是同一个顾客，把两条消息都塞给 3 号窗口。但商场 A 座的消息和商场 B 座的消息其实没关系，它们被放在同一个窗口里，导致 3 号窗口的排队混乱了。"

**大师**："技术映射：如果要用多库合一 Topic，建议自定义分区键为 `{database}.{table}.{id}`，确保不同库的相同 id 不会产生分区冲突。可以通过 SMT 的 `ValueToKey` 来提取组合主键。"

**小白**："回到你说的四种路由方式，我感觉 1+2 比较简单，3+4 需要一定的正则和配置能力。能不能给我们一个实战的渐进式路线？"

**大师**："好。我们用三层递进："

```
Level 1：修改 Topic 前缀（简单）
   topic.prefix=my_project_prod
   → Topic 名：my_project_prod.inventory.orders
   价值：给不同项目/环境打上语义标签

Level 2：按表名映射（中等）
   topic.inventory.orders=orders_business
   → Topic 名：my_project_prod.orders_business
   价值：简化 Topic 名，统一业务语义

Level 3：正则路由 + 条件路由（高级）
   ContentBasedRouter：按 op 类型分流
   RegexRouter：按正则匹配重命名
   价值：精细化流量编排
```

---

## 3. 项目实战

### 环境准备

```bash
docker ps --format "{{.Names}}" | grep -E "mysql|connect|kafka"
# 预期：mysql, connect, kafka 都在运行
```

### 步骤1：自定义 topic.prefix

**目标**：用有语义的 topic.prefix 替代默认的 dbserver1。

```bash
# 场景：生产环境的订单系统，topic.prefix 应体现业务含义
curl -X POST http://localhost:8083/connectors \
  -H "Content-Type: application/json" \
  -d '{
    "name": "orders-prod-connector",
    "config": {
      "connector.class": "io.debezium.connector.mysql.MySqlConnector",
      "database.hostname": "mysql",
      "database.port": "3306",
      "database.user": "debezium",
      "database.password": "dbz1234",
      "database.server.id": "184071",
      "topic.prefix": "production-orders",
      "database.include.list": "inventory",
      "table.include.list": "inventory.orders",
      "schema.history.internal.kafka.bootstrap.servers": "kafka:9092",
      "schema.history.internal.kafka.topic": "schema-changes.orders",
      "snapshot.mode": "initial"
    }
  }'

# 查看自动创建的 Topic
docker exec kafka kafka-topics --bootstrap-server localhost:9092 --list | grep production
# 预期输出：
# production-orders.inventory.orders
```

**Topic 名解析**：`production-orders.inventory.orders` = `<topic.prefix>.<database>.<table>`

### 步骤2：使用自定义 Topic 名称（tableTopic）

**目标**：将主题名 `production-orders.inventory.orders` 简化映射为 `business.orders`。

```bash
# 先删除前面的 connector
curl -X DELETE http://localhost:8083/connectors/orders-prod-connector

# 创建新 connector，使用 transforms 配置自定义 Topic
curl -X POST http://localhost:8083/connectors \
  -H "Content-Type: application/json" \
  -d '{
    "name": "orders-custom-topic",
    "config": {
      "connector.class": "io.debezium.connector.mysql.MySqlConnector",
      "database.hostname": "mysql",
      "database.port": "3306",
      "database.user": "debezium",
      "database.password": "dbz1234",
      "database.server.id": "184072",
      "topic.prefix": "prod",
      "database.include.list": "inventory",
      "table.include.list": "inventory.orders,inventory.products",
      "schema.history.internal.kafka.bootstrap.servers": "kafka:9092",
      "schema.history.internal.kafka.topic": "schema-changes.custom-topic",
      "snapshot.mode": "initial",
      "transforms": "route",
      "transforms.route.type": "org.apache.kafka.connect.transforms.RegexRouter",
      "transforms.route.regex": "prod\\.inventory\\.orders",
      "transforms.route.replacement": "business.orders",
      "transforms.route.regex2": "prod\\.inventory\\.products",
      "transforms.route.replacement2": "business.products"
    }
  }'

# 查看 Topic 列表
docker exec kafka kafka-topics --bootstrap-server localhost:9092 --list | grep business
# 预期输出：
# business.orders
# business.products
```

### 步骤3：按操作类型分流——INSERT 和 DELETE 走不同 Topic

**目标**：insert 事件路由到 `business.orders.insert`，delete 事件路由到 `business.orders.delete`。

```bash
curl -X DELETE http://localhost:8083/connectors/orders-custom-topic

curl -X POST http://localhost:8083/connectors \
  -H "Content-Type: application/json" \
  -d '{
    "name": "orders-route-by-op",
    "config": {
      "connector.class": "io.debezium.connector.mysql.MySqlConnector",
      "database.hostname": "mysql",
      "database.port": "3306",
      "database.user": "debezium",
      "database.password": "dbz1234",
      "database.server.id": "184073",
      "topic.prefix": "prod",
      "database.include.list": "inventory",
      "table.include.list": "inventory.orders",
      "schema.history.internal.kafka.bootstrap.servers": "kafka:9092",
      "schema.history.internal.kafka.topic": "schema-changes.route-by-op",
      "snapshot.mode": "initial",
      "transforms": "routeByOp",
      "transforms.routeByOp.type": "io.debezium.transforms.ContentBasedRouter",
      "transforms.routeByOp.language": "jsr223.groovy",
      "transforms.routeByOp.topic.expression": "value.op == 'c' ? 'business.orders.insert' : value.op == 'd' ? 'business.orders.delete' : 'business.orders.update'"
    }
  }'

# 测试验证
docker exec mysql mysql -uroot -proot1234 inventory -e "
  INSERT INTO orders (customer_id, product_name, quantity, price, status) VALUES (3001, 'Test A', 1, 100, 'pending');
  UPDATE orders SET status='done' WHERE id=(SELECT MAX(id) FROM orders);
  DELETE FROM orders WHERE id=(SELECT MAX(id) FROM orders);
"

# 验证三个 Topic 中的数据
docker exec kafka kafka-topics --bootstrap-server localhost:9092 --list | grep "business.orders"
# 预期输出：
# business.orders.insert
# business.orders.update
# business.orders.delete
```

### 步骤4：多库多表合流到一个 Topic

**目标**：多张不同结构的表路由到同一个 Topic，下游基于 source.db.table 字段区分。

```bash
# 先在 MySQL 中创建两个数据库各一张表
docker exec mysql mysql -uroot -proot1234 << 'SQL'
CREATE DATABASE IF NOT EXISTS warehouse;
USE warehouse;
CREATE TABLE IF NOT EXISTS shipments (
    id INT PRIMARY KEY AUTO_INCREMENT,
    order_id INT NOT NULL,
    tracking_number VARCHAR(100),
    shipped_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
INSERT INTO warehouse.shipments (order_id, tracking_number) VALUES (1, 'SF1234567890');

USE inventory;
-- orders 表已存在，插入一条
INSERT INTO orders (customer_id, product_name, quantity, price, status) VALUES (4001, 'Test B', 1, 200, 'pending');
SQL

# 创建 Connector，将 orders 和 shipments 都路由到 topic.operations
curl -X POST http://localhost:8083/connectors \
  -H "Content-Type: application/json" \
  -d '{
    "name": "multi-table-merge",
    "config": {
      "connector.class": "io.debezium.connector.mysql.MySqlConnector",
      "database.hostname": "mysql",
      "database.port": "3306",
      "database.user": "debezium",
      "database.password": "dbz1234",
      "database.server.id": "184074",
      "topic.prefix": "prod",
      "database.include.list": "inventory,warehouse",
      "table.include.list": "inventory.orders,warehouse.shipments",
      "schema.history.internal.kafka.bootstrap.servers": "kafka:9092",
      "schema.history.internal.kafka.topic": "schema-changes.multi-merge",
      "snapshot.mode": "initial",
      "transforms": "mergeTopic",
      "transforms.mergeTopic.type": "org.apache.kafka.connect.transforms.RegexRouter",
      "transforms.mergeTopic.regex": "prod\\.(inventory|warehouse)\\.(.*)",
      "transforms.mergeTopic.replacement": "operations.all"
    }
  }'

# 消费验证——两种表的数据在同一个 Topic 中
docker exec kafka kafka-console-consumer --bootstrap-server localhost:9092 \
  --topic operations.all --from-beginning --max-messages 2
# 预期：能看到一条 orders 数据和一条 shipments 数据，source 字段中 db/table 不同
```

### 可能遇到的坑及解决方法

| 坑 | 现象 | 解决 |
|----|------|------|
| RegexRouter 正则不生效 | Topic 名没变 | 检查 regex 中特殊字符是否转义正确（`.` 需写为 `\\.`） |
| ContentBasedRouter 需要 Groovy | Connector 启动失败，报 `NoClassDefFoundError: groovy/...` | Debezium 2.7 默认不打包 Groovy，改用 JEXL 语言：`language: "jsr223.jexl"` |
| 两表合流后消费端解析错误 | 下游反序列化失败 | 不同表的 Schema 不同，合并 Topic 后需用 Schema Registry 的 Multi-Subject 策略 |

---

## 4. 项目总结

### 优点 & 缺点

| 方案 | 优点 | 缺点 |
|------|------|------|
| 默认命名 | 零配置，开箱即用 | Topic 数量爆炸，无业务语义 |
| 自定义 prefix | 配置简单，语义化 | 粒度粗，无法做表级区别 |
| RegexRouter | 灵活，支持正则批量映射 | 正则维护成本高，出问题难排查 |
| ContentBasedRouter | 按内容动态路由，最灵活 | 需要 Groovy/JEXL 表达式的运行时开销 |

### 适用场景

1. **多租户 SaaS**：`topic.prefix=tenant_{tenantId}`，每个租户一套独立 Topic
2. **多环境隔离**：`topic.prefix=dev` / `staging` / `prod` 区分环境
3. **业务维度统一**：各部门按业务线订阅（`business.orders.*`、`business.users.*`）
4. **安全审计**：敏感操作（DELETE）单独 Topic，配置更严格的访问控制
5. **合流分析**：多库同构数据合流到同一 Topic，方便 Flink 进行实时宽表 Join

### 注意事项

- **RegexRouter 的顺序**：先匹配 regex → 命中则替换为 replacement，不命中则保持原 Topic 名。如果你需要按条件路由不同的 Topic，用 ContentBasedRouter
- **分区键的设计**：多表合流后，默认分区键仍是表主键。如果需要保证跨表的有序性（如按 `order_id` 分区），通过 `message.key.columns` 指定分区键

### 思考题

1. 一个 Connector 同时监控 `inventory.orders` 和 `inventory.payments` 两张表，目标是将 orders 发送到 `topic.common`，payments 发送到 `topic.finance`。请写出对应的 RegexRouter 或 ContentBasedRouter 配置。

2. 如果两个表的 Topic 被合并到同一个 Topic，且两张表的主键 ID 存在重复（如 orders.id=100 和 payments.id=100），下游 Flink 按 ID 做 Upsert 到 ClickHouse 会出现什么问题？如何解决？

**（第6章思考题答案）**

1. 快照完成后 80% 时宕机，重启后通常能从 80% 继续——因为 offset 中记录了 `row` 字段（当前读到第几行）。但以下情况会从头开始：① `connect-offsets` Topic 中的数据被清理或过期了；② Connector 名字变了（导致找不到原有 offset）；③ `snapshot.mode` 被改为 `initial_only` 然后改回其他模式，offset 被覆盖。

2. initial 快照完成后自动切换到 streaming 模式，Connector 保持 RUNNING；initial_only 快照完成后 Connector 直接 STOPPED（任务状态变为 STOPPED 或自动删除）。另外，initial_only 完成后不会在 offset 中标记 `snapshot_completed=true`（因为不需要切换），所以如果删除重建，还会重新触发快照。

---

> **推广提示**：建议架构团队制定团队级的 Topic 命名规范——`{env}.{domain}.{entity}.{operation}` 四级命名 + 明文文档。运维团队将 RegexRouter 配置模板化，避免开发人员每次手写正则。
