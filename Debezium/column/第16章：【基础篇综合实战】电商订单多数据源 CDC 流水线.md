# 第16章：【基础篇综合实战】电商订单多数据源 CDC 流水线

## 1. 项目背景

章一鸣是某中型电商公司的架构师。随着公司业务增长，团队从单一 MySQL 扩展到 MySQL（订单交易）+ PostgreSQL（用户画像）+ MongoDB（用户行为日志）三种数据源。BI 部门要求构建实时数据大屏（显示 GMV、订单量、用户活跃度），算法团队需要用户行为数据做实时推荐模型更新，财务部门需要订单数据实时入仓做对账。

一鸣面临的挑战是——如何用一个统一的 CDC 平台同时捕获三种数据库的变更，输出到统一的 Kafka 总线，让下游各个团队按需订阅？如果为每种数据库分别写一套同步脚本，维护成本和故障率都高得令人难以接受。

这一章我们将带着一鸣的需求，用基础篇学到的全部知识，搭建一条**三数据源 → Kafka → 下游多消费者的完整 CDC 流水线**。这是基础篇的终章检验。

### 需求拆解

| 需求 | 技术方案 | 对应章节 |
|------|---------|---------|
| MySQL 订单表实时同步 | MySQL Connector + Avro | 第3、11章 |
| PostgreSQL 用户画像同步 | PG Connector + Avro | 第8、11章 |
| MongoDB 行为日志同步 | MongoDB Connector + JSON | 第9章 |
| 消息格式统一 | ExtractNewRecordState SMT | 第10章 |
| 安全合规（敏感字段脱敏） | ReplaceField SMT | 第10章 |
| 可视化管理和监控 | Debezium UI + Shell 脚本 | 第12、15章 |
| 环境一键部署 | Docker Compose | 第2章 |

---

## 2. 项目设计——三人对话

**（项目 kickoff 会议，白板上画满了架构图）**

**一鸣（大师角色）**："今天我们要做一个真实的电商平台 CDC 方案。背景是三个数据源，需要同步给 BI、算法、财务三个下游团队。小胖，你先来设计 Connector 怎么部署。"

**小胖**："三个数据源 = 三个 Connector。订单表用 MySQL Connector，用户画像用 PG Connector，行为日志用 MongoDB Connector。每个 Connector 一个独立的配置 JSON。"

**小白**："有三个问题——第一，三个 Connector 共用一套 Kafka Connect 集群吗？如果某个 Connector 出问题会不会影响其他？第二，下游的 BI 团队只想看订单数据，怎么保证他们订阅到的 Topic 只有订单相关的？第三，用户画像表里有手机号和身份证号，算法团队不应该看到这些字段，怎么过滤？"

**大师**："小白考虑得很周全。一个一个来——"
- **共用一个 Connect 集群**：可以。Kafka Connect 的 Worker 会自动分配 Connector 和 Task，单个 Connector 的故障不会影响其他。但要注意 `database.server.id` 不能冲突。
- **Topic 隔离**：用不同的 `topic.prefix` 区分三个数据源——`ecommerce.orders.*`、`ecommerce.profiles.*`、`ecommerce.behavior.*`。下游团队只需订阅对应的 Topic 前缀。
- **敏感字段过滤**：在 PG Connector 上配置 ReplaceField SMT，排除 `phone`、`id_number` 字段。算法团队收到的消息中不会包含这些字段。

**小胖**："那 Avro 序列化要用吗？三个 Connector 都要配吗？"

**大师**："推荐都用 Avro + Schema Registry。好处是：第一，不同团队消费的是强类型数据，不需要猜字段类型；第二，如果 DBA 改了表结构，Schema 的兼容性检查会阻止不兼容的变更；第三，从我们每天的订单量来看，Avro 能节省 80% 以上的消息体积。"

**小白**："这个方案有一个潜在的坑——MongoDB 是 Schema-less 的，和 Avro 的强 Schema 不是矛盾吗？"

**大师**："没错。所以我们 MongoDB Connector 继续用 JSON Converter，MySQL 和 PG 用 Avro。MongoDB 消息的 `after` 字段本身是一个字符串（JSON 字符串），所以外层 Schema 不受影响。"

**大师**（在白板上画出最终架构）：

```
┌──────────┐   ┌──────────────┐   ┌───────────┐
│  MySQL   │   │ PostgreSQL   │   │ MongoDB   │
│ (orders) │   │  (profiles)  │   │(behavior) │
└────┬─────┘   └──────┬───────┘   └─────┬─────┘
     │                │                 │
     ▼                ▼                 ▼
┌──────────────────────────────────────────────┐
│           Kafka Connect (Debezium)           │
│  ┌──────────┐ ┌──────────┐ ┌─────────────┐  │
│  │  MySQL   │ │    PG    │ │   MongoDB   │  │
│  │ Connector│ │ Connector│ │  Connector  │  │
│  │ + Avro   │ │ + Avro   │ │  + JSON     │  │
│  │ + SMT    │ │ + SMT    │ │  + SMT      │  │
│  └────┬─────┘ └────┬─────┘ └──────┬──────┘  │
└───────┼────────────┼──────────────┼──────────┘
        │            │              │
        ▼            ▼              ▼
┌──────────────────────────────────────────────┐
│              Apache Kafka                    │
│  Topic: ecom.orders│profiles│behavior       │
└────────┬─────────┬──────────┬───────────────┘
         │         │          │
    ┌────┴──┐ ┌────┴──┐ ┌────┴──┐
    │  BI   │ │ 算法  │ │ 财务  │
    │ 团队  │ │ 团队  │ │ 团队  │
    └───────┘ └───────┘ └───────┘
```

---

## 3. 项目实战

### 环境准备

我们将基于第2章的 docker-compose.yml 扩展，新增 PG 和 MongoDB。

```bash
cd ~/debezium-lab

# 确认已有容器运行
docker ps --format "table {{.Names}}\t{{.Status}}"
# 预期：mysql, postgres, mongodb, zookeeper, kafka, connect, schema-registry, debezium-ui

# 确保 Plugin 齐全
ls plugins/
# 预期：debezium-connector-mysql/  debezium-connector-postgres/  debezium-connector-mongodb/

# 重启 Connect 以加载所有插件
docker restart connect && sleep 30
```

### 步骤1：部署 MySQL Connector（订单数据）

```bash
cat > connector-mysql-orders.json << 'EOF'
{
  "name": "ecommerce-orders-mysql",
  "config": {
    "connector.class": "io.debezium.connector.mysql.MySqlConnector",
    "database.hostname": "mysql",
    "database.port": "3306",
    "database.user": "debezium",
    "database.password": "dbz1234",
    "database.server.id": "184161",
    "topic.prefix": "ecom_orders",
    "database.include.list": "inventory",
    "table.include.list": "inventory.orders",
    "schema.history.internal.kafka.bootstrap.servers": "kafka:9092",
    "schema.history.internal.kafka.topic": "schema-changes.ecom-orders",
    "snapshot.mode": "initial",
    "snapshot.locking.mode": "minimal",

    "key.converter": "io.confluent.connect.avro.AvroConverter",
    "key.converter.schema.registry.url": "http://schema-registry:8081",
    "value.converter": "io.confluent.connect.avro.AvroConverter",
    "value.converter.schema.registry.url": "http://schema-registry:8081",

    "transforms": "unwrap",
    "transforms.unwrap.type": "io.debezium.transforms.ExtractNewRecordState",
    "transforms.unwrap.delete.handling.mode": "rewrite"
  }
}
EOF

curl -X POST http://localhost:8083/connectors \
  -H "Content-Type: application/json" \
  -d @connector-mysql-orders.json
```

### 步骤2：部署 PostgreSQL Connector（用户画像）

```bash
cat > connector-pg-profiles.json << 'EOF'
{
  "name": "ecommerce-profiles-pg",
  "config": {
    "connector.class": "io.debezium.connector.postgres.PostgresConnector",
    "database.hostname": "postgres",
    "database.port": "5432",
    "database.user": "debezium_user",
    "database.password": "dbz1234",
    "database.dbname": "user_profile",
    "topic.prefix": "ecom_profiles",
    "table.include.list": "public.users",
    "plugin.name": "pgoutput",
    "publication.name": "dbz_publication",
    "publication.autocreate.mode": "filtered",
    "slot.name": "debezium_ecom_profiles",
    "snapshot.mode": "initial",

    "key.converter": "io.confluent.connect.avro.AvroConverter",
    "key.converter.schema.registry.url": "http://schema-registry:8081",
    "value.converter": "io.confluent.connect.avro.AvroConverter",
    "value.converter.schema.registry.url": "http://schema-registry:8081",

    "transforms": "unwrap,dropSensitive",
    "transforms.unwrap.type": "io.debezium.transforms.ExtractNewRecordState",
    "transforms.unwrap.delete.handling.mode": "rewrite",
    "transforms.dropSensitive.type": "org.apache.kafka.connect.transforms.ReplaceField$Value",
    "transforms.dropSensitive.exclude": "phone"
  }
}
EOF

curl -X POST http://localhost:8083/connectors \
  -H "Content-Type: application/json" \
  -d @connector-pg-profiles.json
```

### 步骤3：部署 MongoDB Connector（行为日志）

```bash
cat > connector-mongo-behavior.json << 'EOF'
{
  "name": "ecommerce-behavior-mongo",
  "config": {
    "connector.class": "io.debezium.connector.mongodb.MongoDbConnector",
    "mongodb.connection.string": "mongodb://debezium:dbz1234@mongodb:27017/?replicaSet=rs0",
    "topic.prefix": "ecom_behavior",
    "database.include.list": "analytics",
    "collection.include.list": "analytics.user_behavior",
    "snapshot.mode": "initial",
    "capture.mode": "change_streams_update_full",

    "key.converter": "org.apache.kafka.connect.json.JsonConverter",
    "value.converter": "org.apache.kafka.connect.json.JsonConverter",

    "transforms": "unwrap",
    "transforms.unwrap.type": "io.debezium.transforms.ExtractNewRecordState",
    "transforms.unwrap.delete.handling.mode": "rewrite"
  }
}
EOF

curl -X POST http://localhost:8083/connectors \
  -H "Content-Type: application/json" \
  -d @connector-mongo-behavior.json
```

### 步骤4：验证三个 Connector 都正常运行

```bash
# 一键检查
for name in ecommerce-orders-mysql ecommerce-profiles-pg ecommerce-behavior-mongo; do
    state=$(curl -s http://localhost:8083/connectors/$name/status | python3 -c "import sys,json; print(json.load(sys.stdin)['connector']['state'])")
    echo "[$name] State: $state"
done

# 预期输出：
# [ecommerce-orders-mysql] State: RUNNING
# [ecommerce-profiles-pg] State: RUNNING
# [ecommerce-behavior-mongo] State: RUNNING
```

### 步骤5：全链路数据验证

**目标**：在三个数据源中分别执行 DML，验证 Kafka Topic 中收到对应的 Change Event。

```bash
# 终端1：同时消费三个 Topic
docker exec kafka kafka-avro-console-consumer --bootstrap-server localhost:9092 \
  --topic ecom_orders.inventory.orders \
  --property schema.registry.url=http://schema-registry:8081 &

docker exec kafka kafka-avro-console-consumer --bootstrap-server localhost:9092 \
  --topic ecom_profiles.public.users \
  --property schema.registry.url=http://schema-registry:8081 &

docker exec kafka kafka-console-consumer --bootstrap-server localhost:9092 \
  --topic ecom_behavior.analytics.user_behavior &

# 终端2：执行数据变更
# MySQL - INSERT 订单
docker exec mysql mysql -uroot -proot1234 inventory -e "INSERT INTO orders (customer_id, product_name, quantity, price, status) VALUES (9001, 'MacBook Pro', 1, 14999.00, 'pending');"

# PG - UPDATE 用户画像
docker exec postgres psql -U postgres -d user_profile -c "UPDATE users SET preferences = preferences || '{\"vip\":true}'::jsonb WHERE username='alice';"

# MongoDB - INSERT 行为日志
docker exec mongodb mongosh --quiet --eval 'use analytics; db.user_behavior.insertOne({user_id:"u999", action:"purchase", product_id:"p-macbook", amount:14999.00, timestamp:new Date()})'
```

**验证清单**：

| 数据源 | 操作 | Topic | 预期行为 |
|--------|------|-------|---------|
| MySQL | INSERT | ecom_orders.inventory.orders | Avro 消息，op="c" |
| PG | UPDATE | ecom_profiles.public.users | Avro 消息，phone 字段被排除 |
| MongoDB | INSERT | ecom_behavior.analytics.user_behavior | JSON 消息，after 为 JSON 字符串 |

### 步骤6：模拟故障与恢复

```bash
# 故障1：模拟 Connector FAILED
curl -X PUT http://localhost:8083/connectors/ecommerce-orders-mysql/config \
  -H "Content-Type: application/json" \
  -d '{ "database.password": "wrong_password" }'

sleep 10
curl http://localhost:8083/connectors/ecommerce-orders-mysql/status | python3 -m json.tool
# 预期：state=FAILED

# 恢复：修正密码 + 重启
curl -X PUT http://localhost:8083/connectors/ecommerce-orders-mysql/config \
  -H "Content-Type: application/json" \
  -d '{ "database.password": "dbz1234" }'

curl -X POST http://localhost:8083/connectors/ecommerce-orders-mysql/restart
sleep 10
curl http://localhost:8083/connectors/ecommerce-orders-mysql/status | python3 -c "import sys,json; print(json.load(sys.stdin)['connector']['state'])"
# 预期：RUNNING
```

---

## 4. 项目总结

### 优点 & 缺点

| 维度 | 本方案 | 传统自研同步 |
|------|--------|-------------|
| 部署时间 | ★★★★★ < 30 分钟 | ★★☆☆☆ 数天到数周 |
| 多数据源支持 | ★★★★★ 一套方案 | ★☆☆☆☆ 每源一套 |
| Schema 管理 | ★★★★★ Avro + Registry | ★☆☆☆☆ 手动维护 |
| 运维自动化 | ★★★★☆ Shell + REST API | ★★☆☆☆ 依赖人工 |
| 消息格式统一性 | ★★★★☆ SMT 统一 | ★☆☆☆☆ 各自不同 |
| 成本 | ★★★★☆ 开源免费 | - |

### 适用场景

1. **电商中台**：订单 + 用户 + 行为日志三流合一
2. **金融数据同步**：交易 + 账户 + 风控的三源实时同步
3. **IoT 数据采集**：设备状态（MySQL）+ 设备配置（PG）+ 事件日志（MongoDB）
4. **SaaS 多租户**：每个租户独立的 MySQL DB → 统一 Kafka 总线
5. **合规审计**：DML 操作全量记录到 Kafka → 写入审计系统

### 注意事项

- **三个 Connector 的 `database.server.id` 和 `slot.name` 必须全局唯一**
- **PG 的 Replication Slot 需定期检查**：如果 Connector 停止消费，Slot 会导致 WAL 磁盘膨胀
- **MongoDB 的 Oplog 大小限制**：高峰期写入速度不能超过 Oplog 窗口

### 思考题

1. 本方案中三个 Connector 共用了一套 Kafka Connect 集群。如果某个 Connector 因为 binlog 消费过慢导致内存队列爆满（OutOfMemoryError），是否会影响其他两个 Connector？你会如何设计资源隔离策略？

2. 如果下游 BI 团队需要同时消费订单（MySQL Avro 格式）和用户画像（PG Avro 格式）两个 Topic，并通过订单的 user_id 关联用户信息，他们应该用什么技术方案来实现这个关联？在 Flink 和 ksqlDB 中分别如何实现？

**（第15章思考题答案）**

1. 自动恢复策略：① 第一次检测到 FAILED → 重试 `POST /restart`；② 如果 2 分钟内再次 FAILED → 重试一次，记录告警；③ 如果 5 分钟内第 3 次 FAILED → 停止自动恢复，发送 P0 告警（包含完整 trace），人工介入。边界条件：连续重启间隔逐步增加（指数退避：1s, 5s, 30s），避免频繁重启消耗 Worker 资源；区分"快速失败"（如权限错误）和"环境问题"（如网络抖动），前者不自动重试。

2. `connect-offsets` Topic 必须使用 `cleanup.policy=compact` 而不是 `delete`，这样只有每个 Key 的最新 Value 被保留，历史版本被压缩但不删除。如果用了 `delete` 策略，offset 数据会在 `retention.ms` 到期后被删除，导致 Connector 重启后找不到 offset，触发 `snapshot.mode=when_needed` 重新全量快照——在千万级表的场景下这可能是灾难性的。

---

> **最终交付物**：本项目实战产出一份可复现的 docker-compose.yml + 3 个 Connector JSON 配置 + 验证脚本 + 运维检查脚本，可作为团队 CDC 基础设施的"新手工具箱"。
>
> **推广提示**：基础篇到此结束。建议在团队内部做一次 1 小时的 workshop，让每个成员亲手跑通这套三数据源 CDC 流水线，作为进入中级篇的"通关测验"。
