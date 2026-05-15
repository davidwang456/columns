# 第14章：Debezium Server——轻量级独立进程模式

## 1. 项目背景

"大师，我们团队只有 5 个人，想用 Debezium 做数据同步，但老板说 Kafka 太重了——又要维护 Zookeeper，又要管理 Broker，还要监控 Topic 分区。有没有不依赖 Kafka 的轻量方案？"

这是很多中小团队的真实困境。Debezium 的标准模式（Kafka Connect）需要部署一整套 Kafka 集群，这对于只需要同步几张表的小团队来说，运维成本太高。而且某些场景下，下游不是 Kafka 消费者，而是一个 HTTP 服务、Redis 缓存、或者云服务（AWS Kinesis、Google Pub/Sub）。

**Debezium Server** 正是为这些场景设计的。它是一个独立的 Java 进程，内嵌了 Debezium Engine，可以直接把数据库的 CDC 变更事件推送到多种 Sink（HTTP、Redis Streams、Apache Pulsar、AWS Kinesis、Google Cloud Pub/Sub、Azure Event Hubs、NATS Streaming 等）。它不需要 Kafka Broker、不需要 Zookeeper、不需要 Schema Registry——一个 JAR 包 + 一份配置文件就能跑。

### 痛点放大

标准 Kafka Connect 方案在小团队面前的障碍：
- **运维成本**：Kafka 三节点集群 + Zookeeper 三节点 = 6 个进程，仅部署就要半天
- **学习曲线**：除了 Debezium，还得学 Kafka Topic、Partition、Consumer Group、Offset、Rebalance
- **资源开销**：Kafka 集群至少需要 4-8GB 内存，对于小规模场景是"牛刀杀鸡"
- **网络复杂度**：Kafka Broker 之间、Broker ↔ Zookeeper、Connect ↔ Broker，网络拓扑复杂

---

## 2. 项目设计——三人对话

**（周五下午，小胖在研究一个 GitHub 上的 Debezium Server demo）**

**小胖**："大师，我看 Debezium 官网说还有一个叫 Server 的东西，不需要 Kafka 就能跑。这是怎么做到的？不通过 Kafka Connect 怎么把 CDC 数据发出去？"

**大师**："好问题。Debezium Server 本质上就是把 Kafka Connect 的 Source 部分（Connector + Task）抽出来，嵌到一个独立进程里，然后把原来需要 Kafka 的中间环节替换成直接推送给外部 Sink。"

```
Kafka Connect 模式 vs Debezium Server 模式：

Kafka Connect:
  MySQL → Connector → Kafka Connect → Kafka Broker → Consumer

Debezium Server:
  MySQL → Connector → Debezium Engine → [Sink Adapter] → 外部系统
                                                 ├→ HTTP (REST API)
                                                 ├→ Redis Stream
                                                 ├→ Apache Pulsar
                                                 ├→ Google Pub/Sub
                                                 └→ AWS Kinesis
```

**小白**："那 Debezium Server 是怎么管理 offset 的？没有 Kafka 的话，offset 存哪？"

**大师**："可以存在本地文件，也可以存在 Redis。配置项叫 `debezium.source.offset.storage`——比如 `debezium.source.offset.storage=org.apache.kafka.connect.storage.FileOffsetBackingStore` 就是存在本地文件，路径配 `offset.storage.file.filename=/tmp/offsets.dat`。如果担心单点丢失，可以存在 Redis 或数据库中。"

**小白**："那如果 Server 进程挂了，重启后能找到 offset 吗？"

**大师**："只要 offset 存储的位置没有被清理，就能找到。文件模式的 offset 存在本地 dat 文件中，进程重启后从文件读取 offset 继续。不过要注意——如果换了机器（容器重启到新节点），文件丢失会导致 offset 丢失，触发重做快照。所以生产环境推荐用 Redis 或数据库做 offset 存储。"

**小胖**："那 Sink 呢？Debezium Server 支持哪些输出？我目前最想要的是 HTTP 输出——变更数据直接 POST 到我们后端的 Node.js 服务。"

**大师**："Debezium Server 内置了多种 Sink 适配器。最轻量的是 HTTP Client Sink——配置如下："

```properties
# Sink：HTTP 直推
debezium.sink.type=http
debezium.sink.http.url=http://my-service:8080/api/cdc-events
debezium.sink.http.timeout=10000
debezium.sink.http.retries=3
```

**技术映射**：Debezium Server = 一个"浓缩版的 Kafka Connect"。它把 Kafka Connect 的 Source、Offset、Transform 全部保留，只是把 Sink 从"写入 Kafka"换成了"直接推送到外部系统"。

---

## 3. 项目实战

### 环境准备

```bash
cd ~/debezium-lab
mkdir debezium-server
cd debezium-server

# 下载 Debezium Server
wget https://repo1.maven.org/maven2/io/debezium/debezium-server-dist/2.7.1.Final/debezium-server-dist-2.7.1.Final.tar.gz
tar -xzf debezium-server-dist-2.7.1.Final.tar.gz

ls -la
# 预期：conf/  lib/  run.sh  README.md
```

### 步骤1：配置 Debezium Server（HTTP Sink 模式）

**目标**：将 MySQL 的 orders 表变更通过 HTTP 推送到本地的一个 Webhook 接收端。

首先，启动一个简单的 HTTP 接收端来验证：

```bash
# 在另一个终端启动简单的 HTTP 接收端（用 Python）
python3 -c "
from http.server import HTTPServer, BaseHTTPRequestHandler
import json

class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers['Content-Length'])
        body = self.rfile.read(length)
        event = json.loads(body)
        print(f'Received: {json.dumps(event, indent=2)[:500]}')
        self.send_response(200)
        self.end_headers()

server = HTTPServer(('0.0.0.0', 9999), Handler)
print('Webhook server listening on :9999')
server.serve_forever()
" &
```

配置 Debezium Server：

```bash
cat > conf/application.properties << 'EOF'
# ---- Debezium Source (MySQL Connector) ----
debezium.source.connector.class=io.debezium.connector.mysql.MySqlConnector
debezium.source.database.hostname=localhost
debezium.source.database.port=3306
debezium.source.database.user=debezium
debezium.source.database.password=dbz1234
debezium.source.database.server.id=184141
debezium.source.topic.prefix=server_test
debezium.source.table.include.list=inventory.orders
debezium.source.schema.history.internal=io.debezium.storage.kafka.history.KafkaSchemaHistory
debezium.source.schema.history.internal.kafka.bootstrap.servers=localhost:9092
debezium.source.schema.history.internal.kafka.topic=schema-changes.server
debezium.source.snapshot.mode=initial
debezium.source.offset.storage=org.apache.kafka.connect.storage.FileOffsetBackingStore
debezium.source.offset.storage.file.filename=data/offsets.dat
debezium.source.offset.flush.interval.ms=10000

# ---- SMT 配置 ----
debezium.transforms=unwrap
debezium.transforms.unwrap.type=io.debezium.transforms.ExtractNewRecordState
debezium.transforms.unwrap.delete.handling.mode=rewrite

# ---- Sink (HTTP) ----
debezium.sink.type=http
debezium.sink.http.url=http://localhost:9999
debezium.sink.http.timeout=10000
debezium.sink.http.retries=3

# ---- 格式配置 ----
debezium.format.key=json
debezium.format.value=json
EOF
```

### 步骤2：启动 Debezium Server

```bash
mkdir -p data

# 启动（前台模式，方便观察日志）
bash run.sh

# 预期日志输出：
# Starting Debezium Server
# EmbeddedEngine initialized
# Snapshot step 6 - Exporting data from table 'inventory.orders'
# Snapshot completed
# Started streaming
```

### 步骤3：验证 HTTP Sink 数据流

```bash
# 在 MySQL 中插入数据
docker exec mysql mysql -uroot -proot1234 inventory \
  -e "INSERT INTO orders (customer_id, product_name, quantity, price, status) VALUES (8001, 'Server Test', 1, 199.00, 'pending');"

# 观察 Webhook 接收端终端的输出
# 预期输出：JSON 格式的 Change Event（已通过 SMT 拍平）
```

### 步骤4：配置 Redis Stream Sink（替代 HTTP）

**目标**：将 CDC 数据推送到 Redis Stream 而不是 HTTP。

```bash
# 首先在 docker-compose 中加一个 Redis（或本地安装）
# 在 Docker 之外部署 Redis：
docker run -d --name redis-sink -p 6379:6379 redis:7-alpine

# 修改 application.properties 的 Sink 部分
cat > conf/application.properties << 'EOF'
# ...（Source 部分保持不变，同上）...

# ---- Sink (Redis Stream) ----
debezium.sink.type=redis
debezium.sink.redis.address=localhost:6379
debezium.sink.redis.message.format=extended
debezium.sink.redis.wait.enabled=true
debezium.sink.redis.wait.timeout.ms=20000
деbezium.sink.redis.retry.initial.delay.ms=300
debezium.sink.redis.retry.max.delay.ms=10000
EOF

# 重启 Debezium Server
bash run.sh &

# Redis 端验证
docker exec redis-sink redis-cli XREAD COUNT 5 STREAMS server_test.inventory.orders 0
# 预期：看到 CDC 变更事件
```

### 步骤5：offset 恢复验证

**目标**：确认 Debezium Server 重启后能从 offset 继续。

```bash
# 插入一条数据
docker exec mysql mysql -uroot -proot1234 inventory \
  -e "INSERT INTO orders (customer_id, product_name, quantity, price, status) VALUES (8002, 'Recovery Test', 1, 299.00, 'pending');"

# 查看 offset 文件
cat data/offsets.dat | python3 -m json.tool
# 预期：看到 file、pos、gtids 等信息

# 停止 Server（Ctrl+C）
# 重启 Server
bash run.sh &

# 插入另一条数据——Server 应该从 offset 继续消费，不重跑快照
docker exec mysql mysql -uroot -proot1234 inventory \
  -e "INSERT INTO orders (customer_id, product_name, quantity, price, status) VALUES (8003, 'Recovery Test 2', 1, 399.00, 'pending');"
```

### 可能遇到的坑及解决方法

| 坑 | 现象 | 根因与解决 |
|----|------|-----------|
| offset 文件路径没有写权限 | Server 启动报错 | `mkdir -p data && chmod 777 data` |
| HTTP Sink 接收端不可达 | Server 不断重试 | `debezium.sink.http.retries=0` 可关闭重试，快速 FAIL |
| Schema History 仍然需要 Kafka | 报错 "Kafka is not available" | Debezium Server 2.5+ 支持 File Schema History，替代 Kafka：`schema.history.internal=io.debezium.storage.file.history.FileSchemaHistory` |
| Redis Cluster 模式不支持 | 连接失败 | 当前版本的 Redis Sink 仅支持单节点 Redis |

---

## 4. 项目总结

### 优点 & 缺点（Debezium Server vs Kafka Connect）

| 维度 | Debezium Server | Kafka Connect (标准) |
|------|----------------|---------------------|
| 部署复杂度 | ★★★★★ 单 JAR 包 | ★★★☆☆ 需 Kafka 集群 |
| 资源占用 | 256MB-512MB | Kafka 集群 4GB+ |
| 高可用 | ★★☆☆☆ 需自行保障 | ★★★★★ 天然集群 HA |
| Sink 多样性 | HTTP/Redis/Pulsar/Kinesis | Kafka（再推下游） |
| offset 管理 | 文件/Redis | Kafka Topic(内置) |
| 适用规模 | < 5 张表 | 无限制 |

### 适用场景

1. **微服务内嵌**：Java 服务通过 Debezium Engine 直接消费 binlog/WAL，无需外部 Kafka
2. **边缘 / IoT**：资源受限的边缘设备上收集数据库变更
3. **Serverless 环境**：作为 AWS Lambda 或 Cloud Run 的 Sidecar
4. **Redis 缓存更新**：MySQL 变更 → Redis Stream → 缓存刷新
5. **数据同步到云服务**：Kinesis、Pub/Sub 等无需自建 Kafka

### 不适用场景

1. **大规模多租户**：> 10 张表、多团队协作，应使用标准 Kafka Connect
2. **需要消息持久化和重放**：HTTP Sink 失败后消息丢失，Kafka 保证持久化

### 注意事项

- **offset 持久化是最大风险点**：文件模式在容器重启时容易丢失，生产建议用 Redis
- **Debezium Server 默认单线程**：只能处理一个 Connector + 一个 Task
- **HTTP Sink 的幂等性**：Debezium Server 不保证 exactly-once，下游需自行做好幂等

### 思考题

1. 如果使用 Debezium Server 的 HTTP Sink，在网络抖动时可能重复推送同一条 Change Event。下游服务如何设计幂等逻辑来避免数据重复？请给出 3 种方案。

2. Debezium Server 的 offset 存储在文件中，如果使用 Docker 部署且容器崩溃重建（文件系统丢失），如何恢复？有哪些方案可以避免 offset 丢失？

**（第13章思考题答案）**

1. `/restart` 是 Connector 级的重启（会重启所有 Task），`/tasks/{id}/restart` 是单个 Task 的重启。Task 级重启适用于：只有一个 Task FAILED（其他 Task 正常）、不需要重新加载配置的场景。Connector 级重启适用于：配置修改后需要生效、所有 Task 都需要重启的场景。注意：Connector 级重启会短暂中断所有 Task。

2. 利用 Kafka Connect 的 `config.storage.topic` 和 REST API 实现：① 编写一个 LISTENER 服务，定期轮询 `GET /connectors` 收集所有 Connector 状态；② 当检测到 FAILED 时，调用 `POST /connectors/{name}/restart` 尝试自动恢复；③ 通过 SMTP 或 webhook 发送通知。或者直接使用 Kafka Connect 的 `errors.tolerance` 和 `errors.deadletterqueue` 配置将失败消息路由到 DLQ，配合外部监控。

---

> **推广提示**：对于小型团队或快速原型验证场景，建议先从 Debezium Server 开始（降低学习成本和部署门槛），验证业务价值后再迁移到标准 Kafka Connect 方案。迁移路径：Server → Kafka Connect（同一个 Connector 配置 JSON 可以直接复用）。
