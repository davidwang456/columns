# 第5章：Connector 配置参数全解与最佳实践

## 1. 项目背景

凌晨 2 点，运维的小张被报警叫醒——"Kafka Connect 的 disk 使用率达到 95%"。他翻遍日志，发现是某个 Connector 在没有 `table.include.list` 过滤的情况下，把一个 MySQL 实例的全部 200 张表的数据都捕获到了 Kafka，其中大部分是日志表、临时表，根本不需要同步。仅仅因为少配了一个参数，一晚上就灌入了几个 TB 的垃圾数据。

这不是孤例。根据 Debezium 社区统计，60% 以上的生产故障都源于**配置错误**——`table.include.list` 没配、`database.server.id` 冲突、`snapshot.mode` 选错、`decimal.handling.mode` 导致金额精度丢失...Debezium 的 Connector 配置参数多达 50+ 个，而且参数之间还存在隐式的依赖关系：比如 `snapshot.mode=never` 时，如果 offset 丢失，Connector 就会停在 FAILED 状态，因为它不知道从哪开始读。

本章的目标：对 30+ 核心参数进行逐一拆解，建立一张"配置参数决策矩阵"，让你在配置每个 Connector 时清楚知道——这个参数是干什么的、默认值是什么、什么场景下需要改、改大了会有什么代价。

### 痛点放大

配置错误导致的典型事故：

- **`decimal.handling.mode=precise` vs `=double`**：前者用 Avro 的 `bytes` 类型表示 BigDecimal（保留精确精度但不直观），后者转为 `double`（有精度损失）。金融场景下如果选了 `double`，0.1 + 0.2 = 0.30000000000000004 这种精度问题会让财务系统报警。
- **`snapshot.mode=initial` 在生产环境误用**：如果数据库已有 1 亿行数据，initial 会全量快照，产生海量 Kafka 消息，可能导致 Broker 磁盘写满。
- **`max.queue.size` 设置过小**：内存队列满后 Connector 的 poll 会阻塞，导致 binlog 消费延迟急剧增加，下游数据新鲜度从秒级退化到分钟级。

---

## 2. 项目设计——三人对话

**（会议室，墙上贴着"配置即代码"的海报）**

**小胖**："大师，我已经成功捕获第一条 CDC 记录了，但配置里那么多参数快把我搞晕了。你能不能帮我归归类？哪些是必须配的？哪些是默认就行不用管的？哪些是生产环境一定要斟酌的？"

**大师**："好，我把 Connector 的配置参数分为五大类，每类有一个核心问题驱动——"

```
┌─ Connector 配置五维模型 ──────────────────┐
│                                           │
│  1. 连接类：连什么？                        │
│     hostname / port / user / password      │
│                                           │
│  2. 过滤类：关心哪些数据？                    │
│     include.list / exclude.list            │
│                                           │
│  3. 行为类：怎么做？                        │
│     snapshot.mode / locking.mode / ...     │
│                                           │
│  4. 性能类：多快？                          │
│     batch.size / queue.size / poll.ms      │
│                                           │
│  5. 转换类：数据怎么变形？                    │
│     decimal.mode / time.mode / converters  │
│                                           │
└───────────────────────────────────────────┘
```

**小白**："大师，5 个维度大概多少参数？我心算一下这配置量有点恐怖。"

**大师**："MySQL Connector 的有效参数大约 50 个，但真正需要你关注的约 30 个。剩下的用默认值就行。我们先从每个维度的'必配三兄弟'开始——"

**（1）连接类必配参数**

| 参数 | 默认值 | 生产建议 | 为什么 |
|------|--------|---------|--------|
| `database.hostname` | 无 | 写 IP 或 hostname | 生产环境不要用 localhost |
| `database.user` | 无 | 专用账号 `debezium_connector` | 不要用 root |
| `database.server.id` | 随机 | **手动指定唯一值** | 每个 Connector 不同，binlog slave id |

**（2）过滤类必配参数**

| 参数 | 默认值 | 生产建议 | 为什么 |
|------|--------|---------|--------|
| `table.include.list` | 无（全库） | **必填！** | 不加这个参数 = 全库所有表都同步 |
| `column.exclude.list` | 无 | 排除敏感列（密码、手机号） | GDPR/等保合规 |
| `database.include.list` | 无 | 生产按库隔离 | 多租户部署的基础 |

**（3）行为类必配参数**

| 参数 | 默认值 | 生产建议 | 为什么 |
|------|--------|---------|--------|
| `snapshot.mode` | `initial` | 新链路用 `initial`，重建用 `when_needed` | 避免重复全量快照 |
| `snapshot.locking.mode` | `minimal` | 生产用 `minimal` | 减少锁表时间 |
| `decimal.handling.mode` | `precise` | **金融/电商保留 `precise`** | 精度不能丢 |

**（4）性能类必配参数**

| 参数 | 默认值 | 生产建议 | 为什么 |
|------|--------|---------|--------|
| `max.queue.size` | 8192 | 高吞吐调至 32768 | 防止背压 |
| `max.batch.size` | 2048 | 大表调至 8192 | 减少 Kafka 请求次数 |
| `poll.interval.ms` | 500 | 低延迟场景调至 100 | 减少空等待 |

**（5）转换类必配参数**

| 参数 | 默认值 | 生产建议 | 为什么 |
|------|--------|---------|--------|
| `decimal.handling.mode` | `precise` | 同上 | 防止金额精度丢失 |
| `time.precision.mode` | `adaptive` | 统一用 `connect` | 避免格式不一致 |
| `event.deserialization.failure.handling.mode` | `fail` | 生产建议 `warn` | 避免单条坏数据阻塞全链路 |

**小胖**："等会儿，你说 `snapshot.mode` 有那么多选项，我到现在只用了 `initial`。其他几种模式什么时候用？"

**大师**："这是最容易踩的坑。我给你画一张决策图——"

```
offset 存在？
├── 否 → snapshot.mode = initial
│        从零开始：全量快照 + 流式 CDC
│        场景：新建的 CDC 链路
│
├── 是 → offset 指向的表还在？
│       ├── 是 → 直接从 offset 继续
│       │         不需要重跑快照，节约时间
│       │         场景：Connector 重启、维护后恢复
│       │
│       └── 否 → snapshot.mode = when_needed
│                  offset 丢失了，但还有部分数据在新表里
│                  场景：表被 drop 后重建，Schema 变了
│
不能锁表？
└── snapshot.mode = never
    完全不跑快照，只监听增量
    场景：已有其他方式完成全量同步，只需要增量
```

**小白**："那 `max.queue.size` 和 `max.batch.size` 的关系是什么？都是 size，哪个影响延迟？哪个影响吞吐？"

**大师**："这一对参数经常被混淆。`max.queue.size` 是 Connector 内部的内存队列容量——数据库的变更事件先放在这个队列里，然后由生产者线程批量取出发到 Kafka。队列满了，读取 binlog 的线程就会暂停。`max.batch.size` 是生产者在一次 `send()` 调用中最多打包多少条事件发给 Kafka。"

**技术映射**：
- `max.queue.size` = 仓库的库容（存多少货），影响的是**背压容忍度**
- `max.batch.size` = 每批装车的货量（一车拉多少），影响的是**吞吐效率**

**大师**（补充）："还有一个隐藏关系——如果你把 `max.batch.size` 调到 8192，但 `max.queue.size` 还是 8192，那么队列装满一批刚好就满了，没有多余的空间去吸收生产端的突发流量。所以经验值是 `max.queue.size >= max.batch.size × 2`。"

**小胖**："我懂了！管仓库的哲学——库容要大于每批出货量的两倍，留点弹性空间。"

---

## 3. 项目实战

### 环境准备

沿用第3-4章的环境，当前 `inventory-connector` 仍在运行。

```bash
curl http://localhost:8083/connectors/inventory-connector/status | python3 -m json.tool | grep state
# 预期输出："state": "RUNNING"
```

### 步骤1：构建一张测试表并插入大量数据来验证性能参数

**目标**：通过增大 `max.batch.size` 和 `snapshot.fetch.size` 观察快照速度的变化。

```bash
# 在 MySQL 中创建一张大表（50 万行）用于测试快照性能
docker exec mysql mysql -uroot -proot1234 inventory << 'SQL'
DROP TABLE IF EXISTS orders_big;
CREATE TABLE orders_big (
    id INT PRIMARY KEY AUTO_INCREMENT,
    customer_id INT NOT NULL,
    order_data TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
-- 用存储过程插入 50 万行
DELIMITER //
CREATE PROCEDURE insert_bulk_data()
BEGIN
    DECLARE i INT DEFAULT 1;
    WHILE i <= 500000 DO
        INSERT INTO orders_big (customer_id, order_data)
        VALUES (FLOOR(1000+RAND()*9000), REPEAT('x', 200));
        SET i = i + 1;
    END WHILE;
END //
DELIMITER ;
CALL insert_bulk_data();
DROP PROCEDURE insert_bulk_data;
SQL

# 验证行数
docker exec mysql mysql -uroot -proot1234 inventory -e "SELECT COUNT(*) FROM orders_big;"
# 预期输出：500000
```

### 步骤2：创建 Connector 配置对比实验

**目标**：对比默认配置 vs 调优配置的快照耗时。

```bash
# ---- 实验A：默认配置 ----
curl -X POST http://localhost:8083/connectors \
  -H "Content-Type: application/json" \
  -d '{
    "name": "orders-big-default",
    "config": {
      "connector.class": "io.debezium.connector.mysql.MySqlConnector",
      "database.hostname": "mysql",
      "database.port": "3306",
      "database.user": "debezium",
      "database.password": "dbz1234",
      "database.server.id": "184055",
      "topic.prefix": "dbserver2",
      "table.include.list": "inventory.orders_big",
      "schema.history.internal.kafka.bootstrap.servers": "kafka:9092",
      "schema.history.internal.kafka.topic": "schema-changes.inventory2",
      "snapshot.mode": "initial",
      "snapshot.fetch.size": 2000,
      "max.batch.size": 2048,
      "max.queue.size": 8192,
      "decimal.handling.mode": "double"
    }
  }'

# 记录时间，观察快照完成时间
watch -n 1 "curl -s http://localhost:8083/connectors/orders-big-default/status | python3 -c 'import sys,json; d=json.load(sys.stdin); print(d[\"tasks\"][0][\"state\"])' 2>/dev/null"
# 等到任务状态从 RUNNING（快照中）变为 RUNNING（流式中），记录耗时
```

```bash
# 清理后实验
curl -X DELETE http://localhost:8083/connectors/orders-big-default

# ---- 实验B：调优配置 ----
# 修改 connetor 名和几个性能参数
# snapshot.fetch.size: 2000 → 20000
# max.batch.size: 2048 → 8192
# max.queue.size: 8192 → 32768

curl -X POST http://localhost:8083/connectors \
  -H "Content-Type: application/json" \
  -d '{
    "name": "orders-big-tuned",
    "config": {
      "connector.class": "io.debezium.connector.mysql.MySqlConnector",
      "database.hostname": "mysql",
      "database.port": "3306",
      "database.user": "debezium",
      "database.password": "dbz1234",
      "database.server.id": "184056",
      "topic.prefix": "dbserver3",
      "table.include.list": "inventory.orders_big",
      "schema.history.internal.kafka.bootstrap.servers": "kafka:9092",
      "schema.history.internal.kafka.topic": "schema-changes.inventory3",
      "snapshot.mode": "initial",
      "snapshot.fetch.size": 20000,
      "max.batch.size": 8192,
      "max.queue.size": 32768,
      "decimal.handling.mode": "double"
    }
  }'

# 再次计时，对比两次的快照耗时
```

**预期结果**（50 万行测试数据参考值）：

| 配置 | snapshot.fetch.size | max.batch.size | 快照耗时 |
|------|-------------------|----------------|---------|
| 默认 | 2000 | 2048 | ~90 秒 |
| 调优 | 20000 | 8192 | ~25 秒 |

### 步骤3：验证 `snapshot.mode` 的几种行为

**目标**：理解 `initial` vs `when_needed` vs `never` 的行为差异。

```bash
# 场景1：snapshot.mode=initial（全新 Connector）
# → 执行全量快照 + 启动流式 CDC
# → 适合：新建 CDC 链路

# 场景2：snapshot.mode=when_needed
# 先正常创建一个 connector，跑完快照，拿到 offset
# 然后 DELETE connector，再重建同名的 connector 但 snapshot.mode=when_needed
# 观察是否跳过快照（因为 offset 还在 Kafka 的 connect-offsets Topic 中）

curl -X POST http://localhost:8083/connectors \
  -H "Content-Type: application/json" \
  -d '{
    "name": "orders-when-needed-test",
    "config": {
      "connector.class": "io.debezium.connector.mysql.MySqlConnector",
      "database.hostname": "mysql",
      "database.port": "3306",
      "database.user": "debezium",
      "database.password": "dbz1234",
      "database.server.id": "184057",
      "topic.prefix": "dbserver4",
      "table.include.list": "inventory.orders",
      "schema.history.internal.kafka.bootstrap.servers": "kafka:9092",
      "schema.history.internal.kafka.topic": "schema-changes.inventory4",
      "snapshot.mode": "initial"
    }
  }'

sleep 30  # 等待快照完成
curl -X DELETE http://localhost:8083/connectors/orders-when-needed-test

# 重建 connector，但 snapshot.mode 改为 when_needed
curl -X POST http://localhost:8083/connectors \
  -H "Content-Type: application/json" \
  -d '{
    "name": "orders-when-needed-test",
    "config": {
      "connector.class": "io.debezium.connector.mysql.MySqlConnector",
      "database.hostname": "mysql",
      "database.port": "3306",
      "database.user": "debezium",
      "database.password": "dbz1234",
      "database.server.id": "184057",
      "topic.prefix": "dbserver4",
      "table.include.list": "inventory.orders",
      "schema.history.internal.kafka.bootstrap.servers": "kafka:9092",
      "schema.history.internal.kafka.topic": "schema-changes.inventory4",
      "snapshot.mode": "when_needed"
    }
  }'

# 查看日志，确认 Connector 跳过了快照阶段
docker logs connect 2>&1 | grep -i "snapshot.*not needed\|Skipping snapshot"
```

### 步骤4：`decimal.handling.mode` 的精度验证

**目标**：验证 `precise` vs `double` 模式在小数金额场景下的差异。

```bash
# 创建一张带 DECIMAL 金额字段的表
docker exec mysql mysql -uroot -proot1234 inventory << 'SQL'
CREATE TABLE IF NOT EXISTS payments (
    id INT PRIMARY KEY AUTO_INCREMENT,
    amount DECIMAL(18,4) NOT NULL,
    tax_rate DECIMAL(5,4) NOT NULL DEFAULT 0.0600
);
INSERT INTO payments (amount, tax_rate) VALUES (9999.9999, 0.0600);
SQL
```

```bash
# Connector A：decimal.handling.mode=precise
curl -X POST http://localhost:8083/connectors \
  -H "Content-Type: application/json" \
  -d '{
    "name": "payment-precise",
    "config": {
      "connector.class": "io.debezium.connector.mysql.MySqlConnector",
      "database.hostname": "mysql",
      "database.port": "3306",
      "database.user": "debezium",
      "database.password": "dbz1234",
      "database.server.id": "184058",
      "topic.prefix": "dbserver5",
      "table.include.list": "inventory.payments",
      "schema.history.internal.kafka.bootstrap.servers": "kafka:9092",
      "schema.history.internal.kafka.topic": "schema-changes.inventory5",
      "snapshot.mode": "initial",
      "decimal.handling.mode": "precise"
    }
  }'

# 消费结果（precise 模式下金额以字符串形式呈现，保证精度）
docker exec kafka kafka-console-consumer --bootstrap-server localhost:9092 \
  --topic dbserver5.inventory.payments --from-beginning --max-messages 1
# 预期：amount 字段格式类似 "PES8/?"（base64 编码的 BigDecimal），或 JSON 中的字符串

# 清理后实验
curl -X DELETE http://localhost:8083/connectors/payment-precise

# Connector B：decimal.handling.mode=double
curl -X POST http://localhost:8083/connectors \
  -H "Content-Type: application/json" \
  -d '{
    "name": "payment-double",
    "config": {
      ...同上，但 "decimal.handling.mode": "double"
    }
  }'
# 消费结果（double 模式下直接是浮点数）
# 预期：amount: 9999.9999（直接是 double 数字）
```

**结论**：金融计算必须用 `precise`，通用业务可以用 `double` 简化下游解析。

### 可能遇到的坑及解决方法

| 坑 | 现象 | 根因与解决 |
|----|------|-----------|
| `decimal.handling.mode=precise` 下游解析困难 | Avro 中 amount 变成了 bytes 类型 | 下游使用 `BigDecimal` 反序列化，或通过 SMT 将其转为 string |
| `snapshot.mode=initial` 重复快照 | 每次重启都全量快照 | Connector 名变了，导致 offset 被识别为新 Connector。保持 Connector 名不变 |
| `max.queue.size` 过小 | StreamingLag 周期性尖峰 | 队列满时 poll 暂停 → 恢复后一次性大量消费 → Lag 形成波浪线。调大 queue.size |
| `snapshot.locking.mode` 未显式设置 | MySQL 全局读锁导致业务延迟 | 默认 `minimal` 较安全，但某些版本可能行为不同，建议显式配置 |

---

## 4. 项目总结

### 优点 & 缺点

| 维度 | Debezium 配置管理 | Canal 配置管理 | Maxwell 配置管理 |
|------|------------------|---------------|-----------------|
| 参数数量 | ★★★☆☆ 50+ | ★★★★☆ 30+ | ★★★★★ ~15 |
| 配置粒度 | ★★★★★ 精细到列级 | ★★★☆☆ 表级 | ★★☆☆☆ 较粗 |
| 文档质量 | ★★★★★ 官方文档详尽 | ★★★☆☆ 部分中文社区 | ★★★★☆ 简洁清晰 |
| 动态重载 | ★★★★☆ 部分参数支持 online update | ★★☆☆☆ 需重启 | ★★☆☆☆ 需重启 |
| 配置模板化 | ★★★★★ JSON/REST API 天然适合 | ★★★☆☆ 原生配置文件 | ★★★☆☆ 原生配置文件 |

### 适用场景

- **高精度金融场景**：`decimal.handling.mode=precise` + `binlog_row_image=FULL`
- **大规模数据同步**：`snapshot.fetch.size=20000` + `max.batch.size=8192` + `max.queue.size=32768`
- **新建 CDC 链路**：`snapshot.mode=initial` + `table.include.list` 明确指定
- **多租户部署**：`database.include.list` 按租户隔离 + `topic.prefix` 区分
- **敏感数据保护**：`column.exclude.list` 排除密码、身份证号等字段

### 注意事项

- **配置变更是幂等的**：对同一个 Connector 执行 PUT 更新配置，只需要传改动的字段，未传的字段保持原值
- **`table.include.list` 语法**：格式为 `db.table`，多个用逗号分隔，支持通配符 `db.inventory.*`
- **`database.server.id` 冲突排查**：如果 Connector 启动报 `A slave with the same server_uuid/server_id`，更换 `database.server.id`

### 常见踩坑经验

1. **"配置了 table.include.list，Topic 里还是有其他表的数据"**——检查是否配了 `column.include.list` 而不是 `table.include.list`，这两个参数名非常相似
2. **"snapshot.mode=schema_only 以为会跳过快照，结果还是等了很久"**——`schema_only` 只读表结构不读数据，但会创建 Schema 并写入 Schema Registry，对于表数量多的库仍需等待
3. **"Connector RESTART 后快照重新跑了一遍"**——如果删除了 Connector 重新创建（POST），需要保持 `name` 字段不变才能复用 offset。一旦 `name` 变了，offset 无法关联，就会触发新的全量快照

### 思考题

1. 一个 MySQL 实例上有 100 个库，每个库 50 张表。如果你只需要监控其中的 3 个库，通过 `database.include.list` 过滤有效还是通过 `table.include.list` 过滤有效？从资源消耗角度分析。（提示：Connector 在启动时需要读取所有可见数据库的 schema）

2. `decimal.handling.mode=precise` 和 `double` 各有利弊。假设你负责一个电商系统，订单金额字段需要精确到分，你会选哪种模式？请列出 3 个选择该模式的理由。

**（第4章思考题答案）**

1. 判断优先级：先比较 `file` 的序号（`mysql-bin.000003` vs `mysql-bin.000004`，序号越大越新），序号相同时比较 `pos`（position 越大越新）。注意 binlog 文件会在达到 `max_binlog_size`（默认 1GB）时自动轮转，也可能通过 `FLUSH LOGS` 命令手动轮转。消费端代码示例：如果 `(event1.file < event2.file) OR (event1.file == event2.file AND event1.pos < event2.pos)`，则 event1 早于 event2。

2. 构建状态变更历史表的方法是：消费每条 UPDATE 事件，如果 `before.status != after.status`，则插入一条历史记录（包含旧状态、新状态、变更时间戳）。判断 UPDATE 是否修改了 status 列：比较 `before` 和 `after` 中的 `status` 字段值是否相同。注意：如果 `binlog_row_image=FULL`，before 和 after 都包含所有列，直接比较即可；如果 `binlog_row_image=MINIMAL`，before 只含主键，after 只含变更列，此时 after 中 status 出现则说明被修改了（否则 after 中不包含 status 列）。

---

> **推广提示**：建议架构师团队基于本章的"五维模型"制作一份标准化的 Connector 配置模板（含参数说明和推荐值），存入团队 Wiki 或内部开发者门户。运维团队可使用本章的知识建立 Connector 配置变更的 Code Review 流程——任何参数变更必须经过 Reviewer 确认并记录变更原因。
