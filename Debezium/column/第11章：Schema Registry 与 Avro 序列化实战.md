# 第11章：Schema Registry 与 Avro 序列化实战

## 1. 项目背景

"大师，我们的 Kafka 集群磁盘又报警了——每天的 CDC 消息体积高达 500GB，JSON 格式太占空间了！而且上周 DBA 给 orders 表加了个 `comment` 字段，下游 3 个微服务的反序列化全炸了..."

这是无数团队从"能用"到"好用"的必经之路。JSON 虽然人眼可读，但在生产环境下有三个硬伤：一是体积大（字符串字段名在每条消息中重复，比如 `"customer_id":` 这个前缀在 1 亿条消息中出现了 1 亿次）；二是无 Schema 约束（下游靠"猜"字段类型，字段改名、新增、删除都可能导致消费端崩溃）；三是解析慢（JSON 是文本格式，需要逐字符解析，而 Avro 是二进制格式，直接按偏移读取字段值）。

**Schema Registry + Avro 是解决这三个问题的标准方案**。Schema Registry 集中管理消息的 Schema 版本，实现上下游的数据契约；Avro 将消息序列化为紧凑的二进制格式，体积只有 JSON 的 30%-50%。本章将部署 Schema Registry，将 Debezium 从 JSON 切换到 Avro，并通过实验验证体积和性能的差异。

### 痛点放大

纯 JSON 模式下的三大痛点场景：
- **Schema 变更雪崩**：DBA 加一个字段 → Debezium 自动产出新的 Schema → 下游 5 个团队 10 个服务的反序列化全挂 → 凌晨 2 点全员 oncall
- **带宽成本失控**：每天 500GB CDC 消息 → Kafka 磁盘费用每月数万 → 如果跨机房 MirrorMaker 复制，网络成本翻倍
- **类型安全缺失**：下游以为 `price` 是 Double，实际 Debezium 当 `decimal.handling.mode=precise` 发的是 base64 编码的 bytes，线上金额计算全错

---

## 2. 项目设计——三人对话

**（小胖端着一杯奶茶，看着 Grafana 上的磁盘使用曲线）**

**小胖**："大师，磁盘使用率每天涨 3%，再这样下去再过两周 Kafka 就满了。有没有办法压缩消息体积？"

**大师**："这就是 Schema Registry + Avro 要解决的问题。先讲为什么 JSON 体积大——每条 JSON 消息都重复写字段名，而 Avro 是**Schema 分离存储**的。Schema 只存在 Registry 里一次，消息中只存数据值，不存字段名。就像你把一张表格的标题行（字段名）贴在墙上只写一次，后面每行数据只写值，不用再写列名。"

**小白**："那 Avro 的消息怎么知道对应哪份 Schema？每条消息都要通过 HTTP 调 Registry 去查吗？"

**大师**："好问题。Avro 在每条消息的头部嵌入了 4 个或 5 个字节的 **Schema ID**。消费者从 Kafka 读到消息后，根据这个 ID 去 Registry 缓存中查找对应的 Schema，然后反序列化。实际上消费者会在本地维护一个 Schema Cache，不需要每次 HTTP 调用。"

**小白**："那 Schema 变更呢？如果下游消费者缓存里的还是旧 Schema，读到新 Schema 的消息怎么办？"

**大师**："这里有三层保护——"

```
Schema 兼容性三角：
     BACKWARD (向后兼容)
       新 Schema 可以读旧数据
       适用：删字段 / 加可选字段
              │
    ┌─────────┼─────────┐
    │         │         │
FORWARD    FULL    NONE
旧 Schema  双向兼容  无检查
可以读新   (推荐生产)
数据
```

**大师**："推荐生产环境用 `FULL` 兼容策略。这样不管是先升级生产者（Connector 产出新 Schema）还是先升级消费者，都不会出现反序列化失败。代价是 Schema 变更只能做加法（加可选字段），不能做减法（删字段只能标记为 deprecated 但不能真删）。"

**小胖**："回到我的磁盘问题——Avro 能省多少空间？"

**大师**："拿 orders 表举例——"

| 消息格式 | 单条大小 | 1 亿条总大小 | 节约比例 |
|---------|---------|-------------|---------|
| JSON（带 schema） | ~800 bytes | ~80 GB | - |
| JSON（ExtractNewRecordState 后） | ~350 bytes | ~35 GB | 56% |
| Avro（ExtractNewRecordState 后） | ~120 bytes | ~12 GB | **85%** |

**小胖**："从 80GB 到 12GB，省了 85%！值得一试！"

---

## 3. 项目实战

### 环境准备

Schema Registry 已在第 2 章的 docker-compose.yml 中预装了（`confluentinc/cp-schema-registry:7.6.0`），确认可用。

```bash
curl http://localhost:8081/subjects
# 预期输出：[]

# 确认 Connect 已加载 Avro Converter
docker exec connect ls /usr/share/java/kafka/ | grep avro
# 预期：有 avro 相关的 jar
```

### 步骤1：注册带 Avro 的 MySQL Connector

**目标**：将 Connector 从 JSON Converter 切换到 Avro Converter。

```bash
# 先清理旧的 Connector（如有）
curl -X DELETE http://localhost:8083/connectors/smt-sensitive-connector 2>/dev/null

# 创建 Avro 模式的 Connector
curl -X POST http://localhost:8083/connectors \
  -H "Content-Type: application/json" \
  -d '{
    "name": "avro-orders-connector",
    "config": {
      "connector.class": "io.debezium.connector.mysql.MySqlConnector",
      "database.hostname": "mysql",
      "database.port": "3306",
      "database.user": "debezium",
      "database.password": "dbz1234",
      "database.server.id": "184111",
      "topic.prefix": "avro_orders",
      "table.include.list": "inventory.orders",
      "schema.history.internal.kafka.bootstrap.servers": "kafka:9092",
      "schema.history.internal.kafka.topic": "schema-changes.avro",
      "snapshot.mode": "initial",

      "key.converter": "io.confluent.connect.avro.AvroConverter",
      "key.converter.schema.registry.url": "http://schema-registry:8081",
      "value.converter": "io.confluent.connect.avro.AvroConverter",
      "value.converter.schema.registry.url": "http://schema-registry:8081",

      "transforms": "unwrap",
      "transforms.unwrap.type": "io.debezium.transforms.ExtractNewRecordState",
      "transforms.unwrap.delete.handling.mode": "rewrite"
    }
  }'
```

**关键配置**：
- `key.converter` + `value.converter`：必须同时指定为 `AvroConverter`
- `schema.registry.url`：指向 Schema Registry 的地址（容器内网络用 `schema-registry:8081`）
- `transforms=unwrap`：拍平消息后再转 Avro

### 步骤2：验证 Schema Registry 中的 Schema 注册

```bash
# 查看所有注册的 Subject
curl http://localhost:8081/subjects | python3 -m json.tool

# 预期输出：
# [
#   "avro_orders.inventory.orders-key",
#   "avro_orders.inventory.orders-value"
# ]

# 查看某个 Subject 的所有 Schema 版本
curl http://localhost:8081/subjects/avro_orders.inventory.orders-value/versions | python3 -m json.tool
# 预期：[1]

# 查看 Schema 详情（版本 1）
curl http://localhost:8081/subjects/avro_orders.inventory.orders-value/versions/1 | python3 -m json.tool
```

**Schema 内容节选**（Avro 格式）：
```json
{
  "subject": "avro_orders.inventory.orders-value",
  "version": 1,
  "id": 1,
  "schema": "{\"type\":\"record\",\"name\":\"Value\",\"fields\":[{\"name\":\"id\",\"type\":\"int\"},{\"name\":\"customer_id\",\"type\":\"int\"},...]}"
}
```

### 步骤3：用 Avro Console Consumer 消费验证

```bash
# 下载 Avro Console Consumer（Confluent 工具包）
# 由于我们用的是 cp-kafka 镜像，avro-console-consumer 已内置
docker exec schema-registry kafka-avro-console-consumer \
  --bootstrap-server kafka:9092 \
  --topic avro_orders.inventory.orders \
  --from-beginning \
  --max-messages 3 \
  --property schema.registry.url=http://localhost:8081
```

**预期输出**（消息内容被反序列化为可读 JSON）：
```json
{"id":1,"customer_id":1001,"product_name":"iPhone 15","quantity":1,"price":7999.0,"status":"completed"}
```

### 步骤4：验证 Schema 演进——新增字段测试

```bash
# 在 MySQL 中新增一个字段
docker exec mysql mysql -uroot -proot1234 inventory -e "
  ALTER TABLE orders ADD COLUMN discount DECIMAL(10,2) DEFAULT 0.00;
"

# 插入一条带新字段的数据
docker exec mysql mysql -uroot -proot1234 inventory -e "
  INSERT INTO orders (customer_id, product_name, quantity, price, status, discount)
  VALUES (6001, 'Test Discount', 1, 100.00, 'pending', 10.00);
"

# 查看 Schema Registry 中的版本变化
curl http://localhost:8081/subjects/avro_orders.inventory.orders-value/versions | python3 -m json.tool
# 预期：[1, 2] → ALTER TABLE 后自动注册了新版本

# 查看全局兼容性配置
curl http://localhost:8081/config | python3 -m json.tool
# 确认 "compatibilityLevel": "BACKWARD"
```

### 步骤5：JSON vs Avro 体积对比实验

**目标**：用同样的 1000 条数据对比 JSON 和 Avro 的消息体积。

```bash
# 插入 1000 条测试数据
docker exec mysql mysql -uroot -proot1234 inventory << 'SQL'
DELIMITER //
CREATE PROCEDURE insert_test_data()
BEGIN
    DECLARE i INT DEFAULT 1;
    WHILE i <= 1000 DO
        INSERT INTO orders (customer_id, product_name, quantity, price, status, discount)
        VALUES (FLOOR(7000+RAND()*1000), CONCAT('Product_', i), FLOOR(1+RAND()*5), ROUND(RAND()*1000,2), 'completed', 0.00);
        SET i = i + 1;
    END WHILE;
END //
DELIMITER ;
CALL insert_test_data();
DROP PROCEDURE insert_test_data;
SQL

# 创建对应的 JSON 模式 Connector 用于对比
curl -X POST http://localhost:8083/connectors \
  -H "Content-Type: application/json" \
  -d '{
    "name": "json-orders-compare",
    "config": {
      "connector.class": "io.debezium.connector.mysql.MySqlConnector",
      "database.hostname": "mysql",
      "database.port": "3306",
      "database.user": "debezium",
      "database.password": "dbz1234",
      "database.server.id": "184112",
      "topic.prefix": "json_orders",
      "table.include.list": "inventory.orders",
      "schema.history.internal.kafka.bootstrap.servers": "kafka:9092",
      "schema.history.internal.kafka.topic": "schema-changes.json-compare",
      "snapshot.mode": "schema_only",
      "key.converter": "org.apache.kafka.connect.json.JsonConverter",
      "value.converter": "org.apache.kafka.connect.json.JsonConverter",
      "transforms": "unwrap",
      "transforms.unwrap.type": "io.debezium.transforms.ExtractNewRecordState",
      "transforms.unwrap.delete.handling.mode": "rewrite"
    }
  }'

# 对比两个 Topic 的消息量
echo "=== Avro Topic 消息数量 ==="
docker exec kafka kafka-run-class kafka.tools.GetOffsetShell \
  --broker-list localhost:9092 --topic avro_orders.inventory.orders --time -1
# 预期：avro_orders.inventory.orders:{partition}:{offset}

echo "=== JSON Topic 消息数量 ==="
docker exec kafka kafka-run-class kafka.tools.GetOffsetShell \
  --broker-list localhost:9092 --topic json_orders.inventory.orders --time -1

# 查看 Topic 的日志大小
docker exec kafka kafka-log-dirs \
  --bootstrap-server localhost:9092 \
  --describe \
  --topic-list avro_orders.inventory.orders,json_orders.inventory.orders
# 对比 Size 列
```

### 可能遇到的坑及解决方法

| 坑 | 现象 | 根因 | 解决 |
|----|------|------|------|
| `Unknown magic byte` | Consumer 消费 Avro 消息报错 | Consumer 未使用 AvroDeserializer | 使用 `kafka-avro-console-consumer`，不是 `kafka-console-consumer` |
| Avro Converter 未找到 | Connector FAILED | plugin.path 中没有 avro-converter 的 jar | confluentinc/cp-kafka-connect 镜像已内置 |
| Schema 不兼容 | 新增字段后 Connector 报错 | 全局兼容策略为 BACKWARD，新增 required 字段 | 将新增字段设为 optional（default=null） |
| Schema Registry 不可达 | Connector 启动超时 | 网络不通或地址错误 | `docker exec connect curl http://schema-registry:8081` 验证连通性 |

---

## 4. 项目总结

### 优点 & 缺点

| 维度 | Avro + Schema Registry | JSON | Protobuf |
|------|----------------------|------|----------|
| 消息体积 | ★★★★★ 120 bytes | ★★☆☆☆ 800 bytes | ★★★★★ 100 bytes |
| Schema 管理 | ★★★★★ 自动注册 + 兼容性检查 | ★☆☆☆☆ 无 | ★★★★☆ protobuf lint |
| 下游开发体验 | ★★★★★ 生成强类型对象 | ★★★☆☆ JSON.parse | ★★★★★ 生成强类型对象 |
| 内存占用 | ★★★★☆ 解码快 | ★★★☆☆ 大量 string 分配 | ★★★★★ 极低 |
| 附加组件 | Schema Registry | 无 | 无 |
| 跨语言支持 | ★★★★★ 所有语言 | ★★★★★ 所有语言 | ★★★★★ 所有语言 |

### 适用场景

1. **生产级 CDC 管道**：100GB+/天的消息量，Avro 省下的带宽足以覆盖 Schema Registry 的维护成本
2. **多团队协作**：上下游通过 Schema Registry 的兼容性检查建立"数据契约"，避免半夜 oncall
3. **跨机房数据复制**：MirrorMaker 复制 CDC 数据时，Avro 的紧凑体积意味着显著降低跨机房网络成本
4. **存储成本敏感**：Kafka Topic 需要保留 7-30 天数据，Avro 降低 80%+ 存储费用
5. **强类型消费**：Java/Go/Python 从Registry 生成代码，编译期类型检查消除运行时字段拼写错误

### 注意事项

- **Schema Registry 是高可用关键组件**：生产环境至少部署 2 个实例，备份 `_schemas` Topic
- **兼容性策略不可逆**：从 `BACKWARD` 改为 `NONE` 再改回来，可能存在不兼容的 Schema，导致存量消费者异常
- **Avro 消息人类不可读**：排查问题时需要 `kafka-avro-console-consumer` 或专门的调试工具，不能直接用 `kafka-console-consumer`

### 思考题

1. 如果 Connector 使用 Avro 序列化，但下游 Consumer 使用 JSON 反序列化，会发生什么？在什么条件下这种混用是可行的？

2. Schema Registry 的 `_schemas` Topic 存储了所有历史 Schema，如果这个 Topic 的数据被误删了，对线上 CDC 管道有什么影响？如何恢复？

**（第10章思考题答案）**

1. SMT 链配置：
```json
{
  "transforms": "filter,unwrap,cleanFields,setKey",
  "transforms.filter.type": "io.debezium.transforms.Filter",
  "transforms.filter.language": "jsr223.jexl",
  "transforms.filter.condition": "value.after.status != 'cancelled'",
  "transforms.unwrap.type": "io.debezium.transforms.ExtractNewRecordState",
  "transforms.unwrap.delete.handling.mode": "rewrite",
  "transforms.cleanFields.type": "org.apache.kafka.connect.transforms.ReplaceField$Value",
  "transforms.cleanFields.exclude": "__db,__table",
  "transforms.setKey.type": "org.apache.kafka.connect.transforms.ValueToKey",
  "transforms.setKey.fields": "id"
}
```
注意顺序：Filter 必须在 Unwrap 之前（因为 Filter 依赖 `value.after.status`），Unwrap 在中间（拍平消息），ReplaceField 和 ValueToKey 在 Unwrap 之后（字段名已变为 after 中的字段）。

2. 不合适——ExtractNewRecordState 默认只保留 `after` 字段（DELETE 事件加 `__deleted` 标记）。如果需要 before 和 after 对比，建议：① 不使用 ExtractNewRecordState，下游直接读取完整 Change Event 的 `before` 和 `after`；② 或者通过 `delete.handling.mode=rewrite` + 自定义字段保留 before，但 Extractor 无法原生返回 before。

---

> **推广提示**：架构师应在技术选型阶段就确定 Avro + Schema Registry 为标准方案，避免"先用 JSON 再迁移"带来的双倍工作量。运维团队需将 Schema Registry 的健康状态纳入告警体系（HTTP 200 检查 + `_schemas` Topic 的 ISR 状态监控）。
