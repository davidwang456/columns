# 第18章：高级 Topic 路由——按表/按库/按操作类型分流

## 1. 项目背景

某支付平台的 CTO 在季度安全审查中发现一个严重问题：`audit_logs` 表记录了所有用户的敏感操作（密码修改、大额转账），但这些数据和其他业务数据（`transactions`、`accounts`）混在同一个 Kafka Topic 中。安全团队要求："审计日志必须写入独立的 `topic.audit`，只能被安全部门订阅，且需要单独的 ACL 权限控制。"但数据库层面是将三张表的 CDC 放在同一个 Connector 下管理的。

同时业务团队也有了个性化需求——他们只关心 `transactions` 表的 INSERT 和 UPDATE，不想要 DELETE 事件（因为退款操作是用冲账而非物理删除）。但 Debezium 默认把所有操作类型都发送到同一个 Topic。

**这就是高级 Topic 路由要解决的问题**。基础篇学过了 RegexRouter（基于 Topic 名做正则替换），但面对"按 `op` 类型 + 按表名 + 按字段值"的三维条件路由，RegexRouter 显然不够。本章将掌握 **ContentBasedRouter** 和 **ExtractTopic SMT** 两个进阶工具，实现基于消息内容的精准路由分发。

### 痛点放大

无高级路由时的典型问题：
- **安全隔离失败**：N 个表的数据汇入同一个 Topic → 任何一个订阅者都能看到所有数据 → GDPR 合规风险
- **Topic 粒度冲突**：A 团队想要按表分 Topic（每表一个 Topic），B 团队想要按操作分（INSERT 和 DELETE 分开），一个 Connector 无法同时满足
- **下游过滤算力浪费**：每 10000 条消息中有 2000 条 DELETE 不需要同步到 ES，但下游不得不全部消费再 filter——浪费网络、CPU和内存

### 技术方案对比

| 路由方式 | 实现层 | 粒度 | 能否感知消息内容 |
|---------|--------|------|-----------------|
| `topic.prefix` | Connector 配置 | Connector 级 | ❌ |
| `RouteTopic` | SMT | 表级 | ❌ |
| `RegexRouter` | SMT | 正则匹配 Topic 名 | ❌（只看 Topic 名） |
| `ContentBasedRouter` | SMT | 消息内容级 | ✅ 可感知 op/字段值 |
| `ExtractTopic` | SMT | 字段值级 | ✅ 从字段值动态提取 Topic 名 |

---

## 2. 项目设计——三人对话

**（周一早会后，小胖被安全团队叫去会议室）**

**小胖**："大师救命！安全审计说我们的 audit_logs 数据和 transactions 混在一个 Topic 里，这是违规的。但他们又不让我把 Connector 拆成两个——因为审计数据需要和交易数据保持同样的 latency。这怎么办啊？"

**大师**："不用拆 Connector。用 **ContentBasedRouter** 来实现'同一个 Connector 输出到不同 Topic'。打个比方——你开了一个快递服务站（Connector），同一个分拣员（Task）收到包裹后，不是一股脑全放一个货架（Topic），而是根据包裹上的标签（op 字段、表名字段）把它们分到不同的货架。"

**小胖**："那就是在消息还没发到 Kafka 之前，先看一眼消息内容，决定发到哪个 Topic？这 RegexRouter 不行吧？它只能根据 Topic 名做替换。"

**大师**："对。RegexRouter 是'看信封'——根据 Topic 名这个'收件地址'做正则替换。ContentBasedRouter 是'拆信封看信'——根据消息内部的 op、source.table、after.status 等字段做条件判断。"

**小白**（合上笔记本）："但大师，如果我要同时按表名和操作类型做路由呢？比如 `audit_logs` 表的所有操作去 `topic.audit`，`transactions` 表的 DELETE 去 `topic.transactions.deleted`，其余去 `topic.transactions`。这种'三维条件'能用 ContentBasedRouter 搞定吗？"

**大师**："可以，ContentBasedRouter 支持用 JEXL（Java Expression Language）表达式做复杂条件判断。就像写 if-else 一样——"

```groovy
// JEXL 表达式示例（ContentBasedRouter 的 topic.expression）
if (value.source.table == 'audit_logs') {
    return 'topic.audit';
} else if (value.op == 'd') {
    return topic + '.deleted';  // topic 是原始 Topic 名变量
} else {
    return topic;
}
```

**小白**："JEXL 和 Groovy 有什么区别？我看到第10章的 Filter SMT 用的是 Groovy。"

**大师**："这就是很多人踩的坑。Debezium 2.5+ 默认不打包 Groovy 的运行时依赖（因为安全漏洞和体积问题），用 Groovy 会报 `NoClassDefFoundError`。**强列推荐用 JEXL**——它是纯 Java 实现，性能比 Groovy 高 10 倍+，而且不需要额外下载 Jar 包。"

**技术映射**：RegexRouter = 机场行李传送带——只根据行李标签（Topic 名）分拣；ContentBasedRouter = 海关安检机——扫描行李内容（消息字段）决定是否分流到特殊通道。

**小胖**："那我还有一个需求——我怎么把 `audit_logs` 表的 Topic 权限和 `transactions` 表的 Topic 权限分开？Kafka ACL 能做到吗？"

**大师**："当然可以。路由到不同 Topic 后，用 Kafka ACL 为每个 Topic 设置不同的读写权限。比如 `topic.audit` 只有安全团队能 Subscribe，`topic.transactions` 是所有业务团队能 Subscribe。ACL 的粒度可以精确到 `Topic + 操作类型（READ/WRITE） + 用户/Group`。"

---

## 3. 项目实战

### 环境准备

沿用第17章环境。在 MySQL 中创建测试表。

```bash
# 创建模拟的三张表
docker exec mysql mysql -uroot -proot1234 inventory << 'SQL'
CREATE TABLE IF NOT EXISTS audit_logs (
    id INT PRIMARY KEY AUTO_INCREMENT,
    user_id INT, action VARCHAR(100),
    detail TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
INSERT INTO audit_logs (user_id, action, detail) VALUES (1, 'login', 'User logged in'), (2, 'transfer', 'Transfer 5000 to account 3');

CREATE TABLE IF NOT EXISTS transactions (
    id INT PRIMARY KEY AUTO_INCREMENT,
    from_account INT, to_account INT,
    amount DECIMAL(10,2), status VARCHAR(50),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
INSERT INTO transactions (from_account, to_account, amount, status) VALUES (100, 200, 5000, 'completed');
SQL
```

### 步骤1：ContentBasedRouter（JEXL 语言）——按 op 类型路由

**目标**：INSERT 事件发到 Topic `pay.transactions.created`，UPDATE 发到 `pay.transactions.updated`，DELETE 发到 `pay.transactions.deleted`。

```bash
curl -X POST http://localhost:8083/connectors \
  -H "Content-Type: application/json" \
  -d '{
    "name": "route-by-operation",
    "config": {
      "connector.class": "io.debezium.connector.mysql.MySqlConnector",
      "database.hostname": "mysql",
      "database.port": "3306",
      "database.user": "debezium",
      "database.password": "dbz1234",
      "database.server.id": "184181",
      "topic.prefix": "pay",
      "database.include.list": "inventory",
      "table.include.list": "inventory.transactions",
      "schema.history.internal.kafka.bootstrap.servers": "kafka:9092",
      "schema.history.internal.kafka.topic": "schema-changes.route-op",
      "snapshot.mode": "initial",
      "transforms": "routeByOp",
      "transforms.routeByOp.type": "io.debezium.transforms.ContentBasedRouter",
      "transforms.routeByOp.language": "jsr223.jexl",
      "transforms.routeByOp.topic.expression": "value.op == ''c'' ? topic + ''.created'' : value.op == ''u'' ? topic + ''.updated'' : value.op == ''d'' ? topic + ''.deleted'' : topic"
    }
  }'

# 等待快照完成
sleep 20

# 检查生成的 Topic
docker exec kafka kafka-topics --bootstrap-server localhost:9092 --list | grep "pay.inventory.transactions"
# 预期输出：
# pay.inventory.transactions.created (快照数据 op="r"，会被归类到哪个分支取决于表达式，可加条件处理)
```

**JEXL 表达式说明**：`value` 是整个 Change Event 的值对象，`value.op` 是操作类型字段。`topic` 是 ContentBasedRouter 自动注入的变量，值为原始 Topic 名（如 `pay.inventory.transactions`）。

### 步骤2：ContentBasedRouter + 表名条件——审计数据分流

**目标**：`audit_logs` 表的全部操作路由到 `topic.audit`，`transactions` 表的操作路由到 `topic.transactions`。

```bash
curl -X DELETE http://localhost:8083/connectors/route-by-operation 2>/dev/null

curl -X POST http://localhost:8083/connectors \
  -H "Content-Type: application/json" \
  -d '{
    "name": "route-audit-separate",
    "config": {
      "connector.class": "io.debezium.connector.mysql.MySqlConnector",
      "database.hostname": "mysql",
      "database.port": "3306",
      "database.user": "debezium",
      "database.password": "dbz1234",
      "database.server.id": "184182",
      "topic.prefix": "pay",
      "database.include.list": "inventory",
      "table.include.list": "inventory.transactions,inventory.audit_logs",
      "schema.history.internal.kafka.bootstrap.servers": "kafka:9092",
      "schema.history.internal.kafka.topic": "schema-changes.route-audit",
      "snapshot.mode": "initial",
      "transforms": "routeByTable",
      "transforms.routeByTable.type": "io.debezium.transforms.ContentBasedRouter",
      "transforms.routeByTable.language": "jsr223.jexl",
      "transforms.routeByTable.topic.expression": "value.source.table == ''audit_logs'' ? ''topic.audit'' : value.source.table == ''transactions'' ? ''topic.transactions'' : topic"
    }
  }'

sleep 20
docker exec kafka kafka-topics --bootstrap-server localhost:9092 --list | grep "topic\."
# 预期输出：
# topic.audit
# topic.transactions
```

**验证路由正确性**：
```bash
# 验证 audit_logs 的快照数据到了 topic.audit
docker exec kafka kafka-console-consumer --bootstrap-server localhost:9092 \
  --topic topic.audit --from-beginning --max-messages 2
# 预期：audit_logs 表的 2 条数据

# 验证 transactions 的快照数据到了 topic.transactions
docker exec kafka kafka-console-consumer --bootstrap-server localhost:9092 \
  --topic topic.transactions --from-beginning --max-messages 1
# 预期：transactions 表的 1 条数据
```

### 步骤3：三维组合路由——表名 + 操作类型 + 字段值

**目标**：
- `audit_logs` 表 → `topic.audit`（全部操作）
- `transactions` 表，DELETE 操作 → `topic.transactions.deleted`
- `transactions` 表，status='failed' → `topic.transactions.failed`
- 其余 → `topic.transactions`

```bash
curl -X DELETE http://localhost:8083/connectors/route-audit-separate 2>/dev/null

curl -X POST http://localhost:8083/connectors \
  -H "Content-Type: application/json" \
  -d '{
    "name": "route-advanced",
    "config": {
      "connector.class": "io.debezium.connector.mysql.MySqlConnector",
      "database.hostname": "mysql",
      "database.port": "3306",
      "database.user": "debezium",
      "database.password": "dbz1234",
      "database.server.id": "184183",
      "topic.prefix": "pay",
      "database.include.list": "inventory",
      "table.include.list": "inventory.transactions,inventory.audit_logs",
      "schema.history.internal.kafka.bootstrap.servers": "kafka:9092",
      "schema.history.internal.kafka.topic": "schema-changes.route-adv",
      "snapshot.mode": "initial",
      "transforms": "advancedRoute",
      "transforms.advancedRoute.type": "io.debezium.transforms.ContentBasedRouter",
      "transforms.advancedRoute.language": "jsr223.jexl",
      "transforms.advancedRoute.topic.expression": "value.source.table == ''audit_logs'' ? ''topic.audit'' : value.op == ''d'' ? ''topic.transactions.deleted'' : (value.after != null && value.after.status == ''failed'') ? ''topic.transactions.failed'' : ''topic.transactions''"
    }
  }'
```

**JEXL 表达式踩坑提示**：JEXL 中字符串比较必须使用双等号 `==`，单等号 `=` 是赋值。JSON 中字符串需要转义单引号 `''audit_logs''` 表示字符串字面量 `'audit_logs'`。

### 步骤4：实时验证三维路由

```bash
# 执行各种 DML 操作验证路由
docker exec mysql mysql -uroot -proot1234 inventory << 'SQL'
-- INSET 到 audit_logs → 预期到 topic.audit
INSERT INTO audit_logs (user_id, action, detail) VALUES (3, 'password_change', 'Changed password');

-- INSERT 正常交易 → 预期到 topic.transactions
INSERT INTO transactions (from_account, to_account, amount, status) VALUES (101, 202, 1000, 'pending');

-- INSERT 失败交易 → 预期到 topic.transactions.failed
INSERT INTO transactions (from_account, to_account, amount, status) VALUES (102, 203, 500, 'failed');

-- DELETE 交易 → 预期到 topic.transactions.deleted
DELETE FROM transactions WHERE id=1;
SQL

# 依次验证四个 Topic
echo "=== topic.audit ===" && docker exec kafka kafka-console-consumer --bootstrap-server localhost:9092 --topic topic.audit --max-messages 1 --timeout-ms 5000 2>/dev/null
echo "=== topic.transactions ===" && docker exec kafka kafka-console-consumer --bootstrap-server localhost:9092 --topic topic.transactions --max-messages 1 --timeout-ms 5000 2>/dev/null
echo "=== topic.transactions.failed ===" && docker exec kafka kafka-console-consumer --bootstrap-server localhost:9092 --topic topic.transactions.failed --max-messages 1 --timeout-ms 5000 2>/dev/null
echo "=== topic.transactions.deleted ===" && docker exec kafka kafka-console-consumer --bootstrap-server localhost:9092 --topic topic.transactions.deleted --max-messages 1 --timeout-ms 5000 2>/dev/null
```

### 步骤5：Kafka ACL 按 Topic 粒度授权

```bash
# 为不同 Topic 设置 ACL
# topic.audit → 只有 security-group 可读
docker exec kafka kafka-acls --bootstrap-server localhost:9092 --add \
  --allow-principal User:security-team \
  --operation Read --topic topic.audit --group '*'

# topic.transactions → 所有业务组可读
docker exec kafka kafka-acls --bootstrap-server localhost:9092 --add \
  --allow-principal User:business-team \
  --operation Read --topic "topic.transactions*" --group '*'

# 验证 ACL
docker exec kafka kafka-acls --bootstrap-server localhost:9092 --list --topic topic.audit
```

### 可能遇到的坑及解决方法

| 坑 | 现象 | 根因 | 解决 |
|----|------|------|------|
| JEXL 表达式报错 | Connector FAILED，`Failed to evaluate expression` | JSON 中引号未正确转义 | `''string''` 表示单引号包裹的字符串字面量 |
| Groovy 类找不到 | `NoClassDefFoundError: groovy/lang/...` | Debezium 2.5+ 默认不含 Groovy | 改用 `language: "jsr223.jexl"` |
| `value.after` 为 null | DELETE 事件中 after=null，表达式引用 after.status 报错 | DELETE 事件的 after 永远是 null | 先用 `value.after != null &&` 做空值守卫 |
| 快照事件 op='r' 未被路由 | 快照数据去了原 Topic 而不是目标 Topic | 表达式中未处理 `value.op == 'r'` 的情况 | 添加 `|| value.op == 'r'` 到对应分支 |

---

## 4. 项目总结

### 优点 & 缺点（高级路由 vs 默认路由）

| 维度 | ContentBasedRouter | RegexRouter | 默认（不路由） |
|------|-------------------|------------|--------------|
| 路由精度 | ★★★★★ 消息内容级 | ★★★☆☆ Topic 名正则 | ★☆☆☆☆ 无法定制 |
| 安全隔离 | ★★★★★ Topic 级 ACL | ★★★☆☆ 有限 | ☆☆☆☆☆ 无隔离 |
| 性能开销 | ★★★★☆ 低（纯内存判断） | ★★★★★ 极低 | ★★★★★ 无 |
| 配置复杂度 | ★★★☆☆ 需写 JEXL 表达式 | ★★★★☆ 简单正则 | ★★★★★ 零配置 |
| 运维可观测性 | ★★★☆☆ 需要日志跟踪 | ★★★★★ 规则清晰 | ★★★★★ 无额外复杂度 |

### 适用场景

1. **安全审计隔离**：审计日志单独 Topic + 独立 ACL，满足 GDPR 和 SOC2 合规
2. **大表 DELETE 分流**：DELETE 事件写入单独 Topic，设置短保留期（如 1 天），减少存储成本
3. **多租户 SaaS**：按租户 ID 路由到不同 Topic，实现物理级数据隔离
4. **死信队列（DLQ）**：将处理失败的事件路由到 `topic.dlq`，不影响正常业务
5. **冷热数据分离**：高频表的数据用独立 Topic + 更多分区，低频表的用统一 Topic

### 不适用场景

1. **路由规则 > 10 条**：JEXL 表达式臃肿且难以维护，建议改用 Kafka Streams 做复杂路由
2. **需要事后修改路由规则**：修改 expression 后 Connector 需要重启，已发送的消息不受影响

### 注意事项

- **JEXL 的三元表达式嵌套不要超过 3 层**，超过则建议拆分为多个 SMT 或改用 Groovy 脚本
- **`value.source.table` 的值是 Debezium 内部的逻辑表名**，不是 MySQL 物理表名。经过 `topic.prefix` 转换后可能不同
- **分区键的一致性**：如果路由到不同 Topic，默认的分区键仍然是原表主键。跨 Topic 的有序性无法保证

### 常见踩坑经验

1. **"JEXL 表达式中引用了 after.field，DELETE 事件直接报错"**——DELETE 的 after 是 null。必须加守卫条件：`value.after != null && value.after.status == 'xxx'`。
2. **"SQL Server 和 Oracle Connector 的 `value.source.table` 大小写与 MySQL 不同"**——SQL Server 会保留原始大小写，可能和 `table.include.list` 中的大小写不一致。建议表达式用 `value.source.table.toUpperCase()` 做统一大小写转换。
3. **"开启 ContentBasedRouter 后，快照数据也有 source.table 字段，但 op 是 'r' 而不是 'c'"**——需要单独处理快照事件的路由，否则快照数据可能被默认路由规则错分。

### 思考题

1. 如果你有一个 Connector 同时监控 50 张表，需要其中 30 张表路由到 `topic.a`，另外 20 张表路由到 `topic.b`。用 ContentBasedRouter 的 JEXL 表达式如何实现？如果表数量增长到 500 张（路由规则 300 条），JEXL 表达式是否还适用？不适用时有替代方案吗？

2. 如果将同一个表的不同操作类型路由到不同 Topic（INSERT→topic.a, UPDATE→topic.b, DELETE→topic.c），但下游需要按主键保证有序性（如 id=100 的 INSERT 必须在 UPDATE 之前处理），Kafka 的跨 Topic 消费能否保证？如果不能，有哪些替代方案？

**（第17章思考题答案）**

1. 开启 ExtractNewRecordState 后，`transaction` 字段仍然存在，位于拍平后消息的顶层。但外层信封中的 `status`（BEGIN/END）会被移除。消费者代码中通过 `payload.transaction.id` 读取事务 ID，通过 `payload.transaction.total_order` 和 `data_collection_order` 判断事务完整性，而不是依赖 status。

2. 在 Kafka Streams 中通过 `GlobalKTable` 实现跨 Topic Join——将 3 个 Topic 各自消费为 KStream，Join 时基于 `transaction.id` 做 grouping，当收到 `total_order == data_collection_order` 且所有表事件凑齐后，统一提交到下游。Flink 方案：使用 `REGISTER TEMPORARY TABLE` 将 3 个 Topic 分别注册为临时表，通过 `INTERVAL JOIN` 基于 `transaction.id` 做窗口关联，设置合适的 `table.exec.state.ttl` 防止状态无限增长。

---

> **推广提示**：架构团队应制定团队的 Topic 命名规范文档（如 `{env}.{domain}.{entity}.{operation}` 四级命名），运维团队在 Code Review 中检查所有新 Connector 的 ContentBasedRouter 表达式是否有正确的 `after != null` 空值守卫。安全团队应将 `topic.audit` 类敏感 Topic 的 ACL 配置纳入自动化部署流水线。
