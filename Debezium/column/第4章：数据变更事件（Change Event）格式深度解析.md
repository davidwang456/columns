# 第4章：数据变更事件（Change Event）格式深度解析

## 1. 项目背景

小李是下游微服务的开发工程师，他的任务是消费 Debezium 写入 Kafka 的消息，将订单数据同步到 Elasticsearch 搜索引擎。他在上一章轻松消费到了数据，但当看到消息内容时傻眼了——

```json
{"schema":{...},"payload":{"before":null,"after":{"id":5,...},"source":{"snapshot":"false",...},"op":"c","ts_ms":1700101234567}}
```

"这 `op` 字段的 `c`、`r`、`u`、`d` 分别是什么意思？`before` 什么时候为 null？`source.snapshot` 为 true 和 false 代表什么？如果一条 `UPDATE` 同时变更了主键，消息长什么样？JSON 里还嵌套了 `schema` 字段，会不会浪费大量带宽？"

不彻底搞懂 Change Event 的格式，就像猫读报纸——看了个热闹。在生产环境中，消费端代码需要基于 `op` 字段做不同的业务逻辑（INSERT → 创建索引、UPDATE → 更新索引、DELETE → 删除索引），如果对消息结构的理解有丝毫偏差，就可能导致数据不一致的严重事故。

本章将对一条 Change Event 的每个字段进行"逐字节解读"，从 `schema` 到 `payload`，从 `before` 到 `after`，从 `source` 到 `transaction`，让你成为读懂 Debezium 消息的"翻译官"。

### 痛点放大

消费端代码的错误理解导致的典型事故：

- **误判空值**：`before=null` 在 INSERT 场景下正常，但在 UPDATE 场景下出现则意味着 `binlog_row_image=MINIMAL` 导致丢失了更新前的行数据，消费端误以为这是 INSERT，造成 ES 索引中出现重复文档。
- **主键变更处理错误**：用户更名导致 `user_id` 变更，Debezium 生成一条 DELETE + 一条 INSERT，消费端如果把 DELETE 当真删除了下游的记录，而不是映射为 UPDATE，数据就丢了。
- **schema 字段被忽略**：许多新手消费端直接 `JSON.parse(payload.after)`，忽略了 `schema` 中关于字段类型和是否可空的声明，导致字段类型变更后消费端反序列化失败。

---

## 2. 项目设计——三人对话

**（下午技术分享会，白板前）**

**小胖**："大师，Change Event 到底长啥样啊？我每次看 Kafka 里的消息都要眯着眼睛找半天。有没有一个标准模板？"

**大师**（在白板上画）："问得好。Change Event 是一个嵌套 JSON，分为三层——最外层的 `schema` 和 `payload`，`payload` 内部再分为 `before`、`after`、`source`、`op`、`ts_ms`、`transaction`。浓缩一下就是：**谁变了 + 变成啥了 + 在哪变的 + 什么时候变的**。"

**小胖**："哦！那就是：`op` 告诉你'谁变了'（增/删/改），`before` + `after` 告诉你'变成啥了'，`source` 告诉你'在哪变的'（库/表/位点），`ts_ms` 告诉你'什么时候变的'。"

**大师**："精确。那我们先把这六个核心字段的定义贴在白板上——"

```
┌─────────── Change Event ───────────┐
│ schema: { 字段的类型声明 }           │
│ payload: {                         │
│   before: 变更前的行数据 (或 null)    │
│   after:  变更后的行数据 (或 null)    │
│   source: {                        │
│     connector: "mysql",            │
│     db:        "inventory",        │
│     table:     "orders",           │
│     file:      "mysql-bin.000003", │
│     pos:       45678,              │
│     snapshot:  false,              │
│     ts_ms:     1700100000000       │
│   },                               │
│   op:     "c" | "u" | "d" | "r",  │
│   ts_ms:  事件生成时间戳             │
│ }                                  │
└────────────────────────────────────┘
```

**小白**："大师，`op` 的取值分别代表什么？能不能列一张完整的对照表？"

**大师**："当然——"

| op 值 | 含义 | before | after | 触发场景 |
|-------|------|--------|-------|---------|
| `c` | Create (INSERT) | null | 新插入的行 | INSERT INTO |
| `u` | Update (UPDATE) | 修改前的行 | 修改后的行 | UPDATE ... SET |
| `d` | Delete (DELETE) | 被删除的行 | null | DELETE FROM |
| `r` | Read (Snapshot) | null | 快照读取的行 | 全量快照阶段 |
| `t` | Truncate | null | null | TRUNCATE TABLE |
| `m` | Message | null | null | 自定义消息（极少使用） |

**小白**："那主键变更呢？MySQL 的 `UPDATE SET id=6 WHERE id=5` 会生成什么消息？"

**大师**："这是最微妙的场景。从数据库的角度，这是一条 UPDATE。但从下游消费者角度，'主键变了'等同于'旧记录消失 + 新记录出现'。Debezium 的处理方式是：生成两条事件——**一条 `op='d'`（删除旧主键记录）+ 一条 `op='c'`（插入新主键记录）**。这两条事件的 `source.ts_ms` 相同，表示它们属于同一次主键变更。"

**小胖**："等会儿，那 `before` 的 null 到底有几种含义？你刚才说 INSERT 的 `before` 是 null，DELETE 的 `before` 可能有值...我就怕代码里把该有值的当 null 处理了。"

**大师**："`before` 的 null 有三种语义，这是很多生产事故的根源——"

| before 值 | 语义 | 说明 |
|-----------|------|------|
| `null` | 不适用 | INSERT 操作，没有'修改前'一说 |
| `null` | 不可用 | `binlog_row_image=MINIMAL` 导致未记录完整 before 行 |
| `{完整行数据}` | 可用 | UPDATE/DELETE 操作，记录了变更前的完整行 |

**大师**："技术映射：**这些 op 值和 null 语义是 Debezium 的'数据契约'的核心部分**。下游消费者不看这张表就直接写代码，就像拆盲盒——可能拆到惊喜，更可能拆到惊吓。"

**小胖**："那 `source.snapshot` 字段呢？我看有的消息 `snapshot: true`，有的 `snapshot: false`。"

**大师**："`source.snapshot=last` 表示这是一条来自增量快照的数据（中级篇讲），`true` 表示来自初始快照，`false` 表示来自实时流式 CDC。这个字段的价值在于——消费端可以根据它决定是否触发全量重建逻辑（比如 Elasticsearch 的 reindex）还是增量更新。"

---

## 3. 项目实战

### 环境准备

沿用第3章的环境，确认 Connector 状态为 RUNNING。

```bash
curl http://localhost:8083/connectors/inventory-connector/status | python3 -m json.tool
```

### 步骤1：捕获并拆解 INSERT 事件的完整结构

**目标**：完整解析一条 INSERT 操作的 Change Event，标注每个字段的含义。

```bash
# 终端1：启动 Consumer
docker exec kafka kafka-console-consumer \
  --bootstrap-server localhost:9092 \
  --topic dbserver1.inventory.orders

# 终端2：执行 INSERT
docker exec mysql mysql -uroot -proot1234 inventory \
  -e "INSERT INTO orders (customer_id, product_name, quantity, price, status) VALUES (2001, 'iPad Pro', 1, 6299.00, 'pending');"
```

**终端1 收到的消息（格式化后）**：

```json
{
  "schema": {
    "type": "struct",
    "fields": [
      {"type": "int32", "optional": false, "field": "id"},
      {"type": "int32", "optional": false, "field": "customer_id"},
      {"type": "string", "optional": false, "field": "product_name"},
      {"type": "int32", "optional": true,  "field": "quantity"},
      {"type": "double", "optional": true,  "field": "price"},
      {"type": "string", "optional": false, "field": "status"}
    ],
    "optional": false,
    "name": "dbserver1.inventory.orders.Value"
  },
  "payload": {
    "before": null,
    "after": {
      "id": 5,
      "customer_id": 2001,
      "product_name": "iPad Pro",
      "quantity": 1,
      "price": 6299.0,
      "status": "pending"
    },
    "source": {
      "version": "2.7.1.Final",
      "connector": "mysql",
      "name": "dbserver1",
      "ts_ms": 1700101234567,
      "snapshot": "false",
      "db": "inventory",
      "sequence": null,
      "table": "orders",
      "server_id": 1,
      "gtid": "f3b3c7e4-1234-5678-9abc-def012345678:50",
      "file": "mysql-bin.000003",
      "pos": 56789,
      "row": 0,
      "thread": 42,
      "query": null
    },
    "op": "c",
    "ts_ms": 1700101234567,
    "transaction": null
  }
}
```

**逐字段解读**：

| 字段路径 | 值 | 说明 |
|----------|---|------|
| `payload.op` | `"c"` | Create 操作（INSERT） |
| `payload.before` | `null` | INSERT 无"修改前"数据 |
| `payload.after.id` | `5` | MySQL 自增主键，记录了 auto_increment 的值 |
| `payload.source.snapshot` | `"false"` | 这不是快照数据，是实时流式 CDC |
| `payload.source.gtid` | `"f3b3c7e4...:50"` | 全局事务 ID，可用于精确跟踪事务边界 |
| `payload.source.file` | `"mysql-bin.000003"` | binlog 文件名 |
| `payload.source.pos` | `56789` | 该事件在 binlog 文件中的字节偏移 |
| `payload.source.row` | `0` | 该事务中的第几条行变更（0-based） |
| `payload.ts_ms` | `1700101234567` | 事件生成时间（毫秒级 Unix 时间戳） |

### 步骤2：捕获 UPDATE 事件，对比 before 与 after

**目标**：理解 UPDATE 操作中 `before` 和 `after` 的差异。

```bash
# 终端2：执行 UPDATE
docker exec mysql mysql -uroot -proot1234 inventory \
  -e "UPDATE orders SET status='shipped', quantity=2 WHERE id=5;"
```

**终端1 收到的消息**：

```json
{
  "payload": {
    "before": {
      "id": 5,
      "customer_id": 2001,
      "product_name": "iPad Pro",
      "quantity": 1,
      "price": 6299.0,
      "status": "pending"
    },
    "after": {
      "id": 5,
      "customer_id": 2001,
      "product_name": "iPad Pro",
      "quantity": 2,
      "price": 6299.0,
      "status": "shipped"
    },
    "op": "u",
    "ts_ms": 1700102234567
  }
}
```

**关键观察**：
- `before.quantity=1` → `after.quantity=2`：只有发生了变更的列有差异
- `before.price=6299.0` → `after.price=6299.0`：未变更的列在 before 和 after 中相同
- 注意：如果 `binlog_row_image=FULL`，before 和 after 都包含完整行；如果是 `MINIMAL`，before 只包含主键列，after 只包含变更列

### 步骤3：捕获 DELETE 事件

```bash
# 终端2：执行 DELETE
docker exec mysql mysql -uroot -proot1234 inventory \
  -e "DELETE FROM orders WHERE id=5;"
```

**终端1 收到的消息**：

```json
{
  "payload": {
    "before": {
      "id": 5,
      "customer_id": 2001,
      "product_name": "iPad Pro",
      "quantity": 2,
      "price": 6299.0,
      "status": "shipped"
    },
    "after": null,
    "op": "d",
    "ts_ms": 1700103234567
  }
}
```

**关键观察**：
- `op="d"` 且 `after=null`：DELETE 操作的标志组合
- `before` 包含了被删除的完整行数据——这对下游审计系统至关重要

### 步骤4：主键变更的特殊场景

**目标**：验证主键变更时 Debezium 生成 DELETE + INSERT 两条事件。

```bash
# 终端2：INSERT 一条测试数据，然后变更其主键
docker exec mysql mysql -uroot -proot1234 inventory \
  -e "
    INSERT INTO orders (id, customer_id, product_name, quantity, price, status)
    VALUES (100, 2001, 'Test Product', 1, 99.00, 'pending');
    UPDATE orders SET id=101 WHERE id=100;
  "
```

**终端1 收到的消息（两条，先后到达）**：

```json
// 第一条：DELETE（原主键 100）
{"payload":{"before":{"id":100,"customer_id":2001,"product_name":"Test Product",...},"after":null,"op":"d",...}}
// 第二条：CREATE（新主键 101）
{"payload":{"before":null,"after":{"id":101,"customer_id":2001,"product_name":"Test Product",...},"op":"c",...}}
```

**消费端代码注意事项**：当读到 `op="d"` 和 `op="c"` 两条事件且 `source.ts_ms` 相同时，应识别为主键变更，在下游执行 UPDATE 而非 DELETE + INSERT。

### 步骤5：解析 source 字段——数据库运维的显微镜

**目标**：从 source 字段中提取运维关键信息。

```bash
# 写一个简单的 Python 脚本解析 source 字段
cat > parse_source.py << 'EOF'
import json
import sys

event = json.loads(sys.stdin.read())
src = event["payload"]["source"]

print(f"数据库: {src['db']}")
print(f"表名:   {src['table']}")
print(f"操作:   {event['payload']['op']}")
print(f"binlog: {src['file']}:{src['pos']}")
print(f"GTID:   {src.get('gtid', 'N/A')}")
print(f"是否快照: {src['snapshot']}")
print(f"事件时间: {event['payload']['ts_ms']}")
print(f"server_id: {src['server_id']}")
EOF

# 用管道传入一条事件消息测试
echo '{"payload":{"source":{"db":"inventory","table":"orders","file":"mysql-bin.000003","pos":45678,"gtid":"xxx:51","snapshot":"false","server_id":1},"op":"u","ts_ms":1700100000000}}' | python3 parse_source.py
```

---

## 4. 项目总结

### 优点 & 缺点

| 维度 | Debezium JSON 格式 | Protocol Buffers | 自定义 Flat JSON |
|------|-------------------|-----------------|-----------------|
| 可读性 | ★★★★★ 人眼可读 | ★★★☆☆ 需工具 | ★★★★★ 精简但缺上下文 |
| 自描述性 | ★★★★★ Schema + Payload | ★★★☆☆ 需要 proto 文件 | ☆☆☆☆☆ 完全无 Schema |
| 体积 | ★★★☆☆ 较大（schema 冗余） | ★★★★★ 最紧凑 | ★★★★☆ 紧凑 |
| 解析性能 | ★★★☆☆ JSON 解析较慢 | ★★★★★ 直接反序列化 | ★★★★★ 简单映射 |
| 版本兼容性 | ★★★★★ 自带 Schema | ★★★★★ proto 兼容机制 | ★☆☆☆☆ 无版本管理 |

### 适用场景

1. **实时数据同步到 Elasticsearch**：基于 `op` 字段决定 `_doc` 的 create/update/delete 操作
2. **审计日志**：`before` 和 `after` 的完整记录是天然的审计数据
3. **数据一致性校验**：`source.file:pos` 可用于 binlog 位点对账
4. **缓存失效**：读 `after` 字段直接更新 Redis 缓存
5. **实时宽表构建**：基于 `source.db.table` 路由到 Flink 的不同 Join 算子

### 注意事项

- **`binlog_row_image=FULL`**：生产环境强烈推荐，否则 UPDATE 的 `before` 可能只有主键列
- **消息体积**：JSON 格式的 Change Event 相比 Avro 体积大约 60%，高吞吐场景建议切换到 Avro（第11章讲）
- **时间字段语义**：`payload.ts_ms` 是 Kafka Connect 端生成事件的时间，`source.ts_ms` 是 binlog 中记录的时间，两者可能相差毫秒到秒级

### 常见踩坑经验

1. **"为什么我的 UPDATE 事件 before 只有 id 字段？"**——根因是 `binlog_row_image=MINIMAL`，改为 `FULL` 即可
2. **"DELETE 事件在消费端被重复消费导致索引错误删除"**——消费端需要基于 `source.gtid` 或 `source.file:pos` 做幂等去重
3. **"TIME 类型的字段在 JSON 中和 MySQL 中显示不一致"**——这是 `time.precision.mode` 参数决定的，默认 `adaptive` 模式会根据精度自动选择格式，建议显式设置 `connect` 模式以保持一致性

### 思考题

1. 如何根据 `source.file` 和 `source.pos` 判断两条 Change Event 在 binlog 中的先后顺序？如果两条事件的 `file` 不同（如 `mysql-bin.000003` 和 `mysql-bin.000004`），如何比较？

2. 下游消费端如果要构建一张"订单状态变更历史表"，应该如何处理 UPDATE 事件的 `before` 和 `after`？如果只关注 `status` 列的变更，如何判断一条 UPDATE 确实修改了 `status` 列？

**（第3章思考题答案）**

1. 1 亿行表的初始快照耗时取决于两个因素：行大小（每行字节数）和网络吞吐。以每行 1KB、快照 fetch.size=2000、Kafka 吞吐 50MB/s 计算，大约需要 `1亿行 × 1KB ÷ 50MB/s = 2000秒 ≈ 33分钟`。加速手段：增大 `snapshot.fetch.size`（默认 2000，可调至 10000）、增大 `max.batch.size`（默认 2048，可调至 8192）。但过大可能 OOM，需要结合 JVM 堆内存调整。
2. 快照期间执行 ALTER TABLE 是最危险的场景。已快照完成的 5000 万行使用旧 Schema（无 discount 列），未快照的 5000 万行使用新 Schema（有 discount 列）。Schema Registry 中会有两个 Schema 版本（v1 和 v2），取决于 `schema.compatibility` 策略（BACKWARD/FORWARD）。如果使用 BACKWARD 兼容，新 Schema 中的 discount 列必须设为 optional（`"default": null`），否则消费者在反序列化旧消息时会失败。

---

> **推广提示**：测试团队应基于本章的 Change Event 格式编写自动化契约测试，验证 Debezium 产出的消息格式是否符合下游消费者的预期。开发团队应将本章的 `parse_source.py` 扩展为生产级的消息解析工具，集成到监控告警体系。
