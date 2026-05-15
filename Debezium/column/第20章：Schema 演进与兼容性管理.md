# 第20章：Schema 演进与兼容性管理

## 1. 项目背景

凌晨 2:13，DBA 为了支持新的促销功能，给 `orders` 表加了一个 `tax_rate DECIMAL(5,2) NOT NULL` 字段。由于时间紧迫，没有走变更审批流程，也没有通知开发团队。凌晨 2:15，Debezium Connector 自动检测到 DDL 变更，向 Schema Registry 注册了新的 Avro Schema（v2）。凌晨 2:16，下游 6 个微服务的 Avro 反序列化全炸了——因为新增的 `tax_rate` 字段被标记为 `required`（NOT NULL），影响到了所有使用 v1 Schema 编译的消费者。消费端报 `org.apache.avro.AvroTypeException: Found tax_rate, expecting null`。

这就是 Schema 演进在生产环境的最大噩梦——一个看似无害的 DDL，因为兼容性策略不当，引发全链路雪崩。

在本章中，你将掌握 Debezium + Avro + Schema Registry 的 Schema 演进完整流程。我们将从兼容性策略的选择开始，逐步深入 Schema 变更的自动化处理、安全操作实践以及应急恢复方案。

### 痛点放大

Schema 变更的四大灾难场景：

- **DDL 兼容性违规**：ADD COLUMN NOT NULL（无默认值）→ 新 Schema 包含必填字段 → 旧消费者无法处理 → 全链路反序列化失败
- **字段重命名**：MySQL 的 `RENAME COLUMN` 在 binlog 中 = DROP + ADD，Schema Registry 看到的是"删了一个字段 + 加了一个字段"，如果不及时通知下游，旧消费者读取新消息时会发现字段不存在
- **类型变更不兼容**：`ALTER COLUMN type FROM INT TO VARCHAR`——虽然 MySQL 做了隐式转换，但 Schema Registry 看到的是"字段类型从 int 变成了 string"，违反兼容性规则
- **Schema History 丢失**：`schema-changes.xxx` Topic 因误配置被清理 → Connector 无法恢复 DDL 历史 → 重建后无法正确处理新的 DDL

## 2. 项目设计——三人对话

**（周二早会，运维群 99+ 条未读消息）**

**小胖**："大师，昨晚 DBA 加了个字段，今早 6 个微服务全崩了！Schema Registry 的兼容性检查难道不是用来防止这种事故的吗？"

**大师**："Schema Registry 的兼容性检查确实能拦截不兼容的变更——但前提是**你配置了正确的兼容性策略**。默认的全局策略是 `BACKWARD`，但它有一个前提——新增字段必须是 optional（带 DEFAULT 或 NULL）。如果 DBA 加了 `NOT NULL` 且没带 DEFAULT，在 BACKWARD 策略下是可以成功的——因为 BACKWARD 只关心'新 Schema 能否读旧数据'，而不关心'旧 Schema 能否读新数据'。"

**小白**："四种兼容策略的区别我一直没完全搞懂。大师你能用一个'书的版本'来比喻吗？"

**大师**："好——想象你写了一本书《CDC 实战》：

- `BACKWARD`（向后兼容）：新版（v2）增加了一章，但删了一章。**新读者**可以读旧版（因为删的章节可选），但**旧读者**读新版会缺内容（因为新章节对旧读者不可见）。→ 适合：新增 optional 字段、删字段
- `FORWARD`（向前兼容）：新版增加了一章。**旧读者**可以读新版（多了不认识的章节跳过即可），但**新读者**读旧版会发现缺章节。→ 适合：新增字段、删除 optional 字段
- `FULL`（完全兼容）：**无论新旧版本读者**都能读彼此的书。新增的章节必须是可选的（optional），删除的章节必须是从未被引用的。→ 推荐生产
- `NONE`：不检查兼容性——任何改动都允许，**但消费者随时可能炸**。

**技术映射**：FULL 兼容性 = REST API 的"只增不减"原则——只新增 optional 字段（`default=null`），只删除从未被客户端使用的字段，修改字段类型视为"新增字段 + 标记旧字段 deprecated"。

**小胖**："那昨晚的事故，怎么用 FULL 策略来避免？"

**大师**："在 FULL 策略下，DBA 的 `ADD COLUMN tax_rate DECIMAL(5,2) NOT NULL`（无 DEFAULT）会被 Schema Registry 拦截——因为新字段是 required，旧消费者读到新消息会因为缺少这个必填字段而报错。正确的 DDL 应该是分两步："

```sql
-- Step 1：加字段，带 DEFAULT（安全）
ALTER TABLE orders ADD COLUMN tax_rate DECIMAL(5,2) DEFAULT 0.00;

-- Step 2：数据回填（业务需要时才做，不影响 Schema 兼容性）
UPDATE orders SET tax_rate = 0.08 WHERE tax_rate = 0.00;

-- Step 3（可选）：如果需要 NOT NULL，先确保所有历史数据都有默认值
-- 然后再 ALTER COLUMN SET NOT NULL（FULL 兼容性检查会通过，因为字段已经存在）
```

**小白**："如果 DBA 需要删一个字段怎么办？FULL 策略下能直接 `DROP COLUMN` 吗？"

**大师**："不能。FULL 策略下直接 DROP COLUMN 会被拦截——因为旧消费者已经依赖这个字段。标准流程是：① 先标记字段为 deprecated（业务停止写入）；② 等待足够长的时间（确保所有旧消费者都已升级）；③ 再执行 DROP COLUMN。如果等不及，可以临时调整兼容性策略为 `NONE`，执行完再改回来——但这是**生产中的危险操作**，需要计划维护窗口。"

---

## 3. 项目实战

### 环境准备

```bash
# 确认 Schema Registry 运行中
curl http://localhost:8081/subjects
# 设置全局兼容性为 FULL（最安全的生产策略）
curl -X PUT http://localhost:8081/config \
  -H "Content-Type: application/json" \
  -d '{"compatibility": "FULL"}'

curl http://localhost:8081/config | python3 -m json.tool
# 预期："compatibility": "FULL"
```

### 步骤1：安全的 DDL 操作——新增带 DEFAULT 的字段

**目标**：使用 `ADD COLUMN ... DEFAULT` 执行兼容性检查通过的 DDL。

```bash
# 确认有 Avro Connector 在运行（复用第11章的 avro-orders-connector）
curl http://localhost:8083/connectors/avro-orders-connector/status
# 如果不存在，参考第11章重新创建

# Step 1：安全的新增列（带 DEFAULT）
docker exec mysql mysql -uroot -proot1234 inventory \
  -e "ALTER TABLE orders ADD COLUMN shipping_fee DECIMAL(10,2) DEFAULT 0.00;"

# Step 2：插入一条带新字段的数据，验证 Schema 自动升级
docker exec mysql mysql -uroot -proot1234 inventory \
  -e "INSERT INTO orders (customer_id, product_name, quantity, price, status, shipping_fee) VALUES (9001, 'Safe DDL Test', 1, 100.00, 'pending', 15.00);"

# Step 3：检查 Schema Registry 版本变化
sleep 10
curl http://localhost:8081/subjects/avro_orders.inventory.orders-value/versions | python3 -m json.tool
# 预期：[1, 2] → v2 自动创建

# Step 4：消费新旧版本消息，确认同时可读
docker exec kafka kafka-avro-console-consumer --bootstrap-server localhost:9092 \
  --topic avro_orders.inventory.orders --from-beginning --max-messages 2 \
  --property schema.registry.url=http://schema-registry:8081
# 预期：v1（无 shipping_fee）和 v2（有 shipping_fee）都能正确反序列化
```

### 步骤2：模拟不兼容的 DDL（被 Schema Registry 拦截）

**目标**：直接加 `NOT NULL` 无 DEFAULT 字段，观察 Schema Registry 的拦截行为。

```bash
# 尝试执行不兼容的 DDL
docker exec mysql mysql -uroot -proot1234 inventory \
  -e "ALTER TABLE orders ADD COLUMN dangerous_col VARCHAR(100) NOT NULL;" 2>&1

sleep 10

# 检查 Connector 日志——应该有兼容性冲突警告
docker logs connect 2>&1 | grep -A 5 "not compatible\|Schema being registered"
# 预期：如果日志显示兼容性冲突，说明 Schema Registry 阻止了不兼容的 Schema 注册

# 如果 Schema Registry 阻止了（理想情况），需要恢复：
# docker exec mysql mysql -uroot -proot1234 inventory -e "ALTER TABLE orders DROP COLUMN dangerous_col;"
```

**注意**：Schema Registry 拦截的是 Avro Schema 级别的兼容性问题，DDL 本身在 MySQL 中会成功执行。如果 Connector 检测到不兼容的 Schema 变更，它可能标记为 FAILED 或跳过注册。

### 步骤3：Schema Version 的紧急回滚

**目标**：如果不兼容的 Schema 已经注册，如何回滚到之前的版本。

```bash
# 查看某个 Subject 的所有版本
curl http://localhost:8081/subjects/avro_orders.inventory.orders-value/versions | python3 -m json.tool

# 回滚兼容性——将全局兼容性临时改为 NONE 以允许删除不兼容的版本
curl -X PUT http://localhost:8081/config \
  -H "Content-Type: application/json" \
  -d '{"compatibility": "NONE"}'

# 删除最新的问题版本（软删除）
# 注意：Schema Registry 不允许物理删除版本，只能设置兼容性为 NONE
# 正确的做法：恢复 MySQL DDL + 重启 Connector，让旧 Schema 继续生产

# 删除问题列
docker exec mysql mysql -uroot -proot1234 inventory \
  -e "ALTER TABLE orders DROP COLUMN IF EXISTS dangerous_col;" 2>/dev/null

# 重启 Connector → 重新注册 Schema（没有问题列，兼容性恢复）
curl -X POST http://localhost:8083/connectors/avro-orders-connector/restart

# 恢复全局兼容性策略
curl -X PUT http://localhost:8081/config \
  -H "Content-Type: application/json" \
  -d '{"compatibility": "FULL"}'
```

### 步骤4：Schema History Topic 的监控

**目标**：确保 Schema History 数据安全，不被误删。

```bash
# 查看 Schema History Topic 的状态
docker exec kafka kafka-topics --bootstrap-server localhost:9092 --describe \
  --topic schema-changes.avro

# 检查数据保留策略——必须使用 compact，不是 delete
docker exec kafka kafka-configs --bootstrap-server localhost:9092 \
  --entity-type topics --entity-name schema-changes.avro --describe

# 查看 Schema History 中的 DDL 历史
docker exec kafka kafka-console-consumer --bootstrap-server localhost:9092 \
  --topic schema-changes.avro --from-beginning --max-messages 3 --timeout-ms 5000 | python3 -m json.tool
```

### 步骤5：团队 DDL 变更 SOP

```bash
# 使用 Liquibase 或 Flyway 的 hook 集成 Schema Registry 兼容性检查
# 以下是概念性的流程脚本

cat > ddl_preflight_check.sh << 'EOF'
#!/bin/bash
# DDL 预检脚本——在执行 DDL 之前评估对 Schema Registry 的影响

SCHEMA_REGISTRY="http://localhost:8081"
SUBJECT="avro_orders.inventory.orders-value"

# 1. 获取当前最新版本
CURRENT=$(curl -s "${SCHEMA_REGISTRY}/subjects/${SUBJECT}/versions/latest")
echo "Current Schema version: ${CURRENT}"

# 2. 模拟新 Schema（需人工提供 DDL 后的表结构）
echo "正在检查 DDL 变更的兼容性..."

# 3. 调用 Schema Registry 的兼容性检查 API
curl -X POST "${SCHEMA_REGISTRY}/compatibility/subjects/${SUBJECT}/versions/latest" \
  -H "Content-Type: application/vnd.schemaregistry.v1+json" \
  -d '{"schema": "$(cat new_schema.avsc)"}'

if [ $? -eq 0 ]; then
    echo "✅ DDL 兼容性检查通过"
else
    echo "❌ DDL 不兼容——请在执行 DDL 前修复兼容性问题"
    exit 1
fi
EOF
```

### 可能遇到的坑及解决方法

| 坑 | 现象 | 根因 | 解决 |
|----|------|------|------|
| ADD COLUMN 后消费者炸了 | `AvroTypeException` | 字段 NOT NULL 无 DEFAULT → 不兼容 | 改为 `DEFAULT 0` 或 `DEFAULT ''` |
| MODIFY COLUMN 被拦截 | Connector FAILED | 类型变更不兼容（INT→VARCHAR） | 分步操作：新增列 → 数据迁移 → 删除旧列 |
| Schema Registry _schemas Topic 被删 | 所有消费者反序列化失败 | cleanup.policy=delete | 设置 `cleanup.policy=compact` |
| DROP COLUMN 后旧消费者失败 | 旧消费者报 `FieldNotFound` | FULL 策略下不能直接删列 | 先标记 deprecated，等所有消费者升级后再删 |

---

## 4. 项目总结

### 优点 & 缺点

| 维度 | FULL 兼容策略 | BACKWARD | NONE |
|------|-------------|----------|------|
| 新增 optional 字段 | ✅ 安全 | ✅ 安全 | ✅ 但不安全 |
| 新增 required 字段 | ❌ 拦截 | ⚠️ 不会被拦截 | ✅ 但不安全 |
| DROP 字段 | ❌ 拦截 | ✅ 不拦截 | ✅ 但不安全 |
| MODIFY 字段类型 | ❌ 拦截 | ⚠️ 部分 | ✅ 但不安全 |
| 生产可用性 | ★★★★★ | ★★★★☆ | ★☆☆☆☆ |

### 适用场景

1. **多团队协作**：上下游团队独立升级，FULL 策略保证互不影响
2. **频繁 DDL 的业务系统**：电商/金融的核心业务库，DDL 变更频繁
3. **合规审计**：Schema 变更历史完整可追溯
4. **CI/CD 集成**：DDL 变更纳入流水线，不合规则自动失败
5. **微服务解耦**：每个微服务独立消费 Topic，FULL 策略保证各自的 Schema 版本兼容

### 不适用场景

1. **敏捷原型开发**：Schema 频繁变化，NONE 模式减少摩擦
2. **一次性的数据迁移**：临时同步管道，不需要长期维护

### 注意事项

- **兼容性策略一旦设为 FULL，就不能轻易调低**——调低后可能有不兼容的 Schema 被注册，消费者可能会崩溃
- **`_schemas` Topic 的生命周期管理**：必须使用 `compact` 策略，replication factor ≥ 3
- **MySQL 的 DDL 和 PG 的 DDL 行为不同**：MySQL DDL 不在事务内，PG DDL 在事务内——影响到 Schema History 的记录时机

### 常见踩坑经验

1. **"我以为 MODIFY COLUMN 只是改了列的属性，Schema 不会变"**——实际上 Debezium 会为一个 MODIFY COLUMN 注册新的 Schema 版本，因为列的 nullability/precision 变化都反映在 Avro Schema 中。
2. **"RENAME COLUMN 导致 Schema 版本跳跃了两个版本"**——MySQL 的 RENAME COLUMN 在 binlog 中 = DROP 旧列 + ADD 新列，Schema Registry 看到的是两次变更。如果旧消费者还引用旧列名，会出现反序列化错误。
3. **"生产误删了一个列，通过备份恢复，Connector Schema 却报错了"**——因为 Schema History 中的版本是递增的，恢复/回滚的 DDL 会被视为新的 Schema 版本，导致全局 Schema 版本序列错误。这种情况下需要手动重建 Schema History。

### 思考题

1. 如果一个表已经积累了 5 个 Schema 版本（v1-v5），一个新消费者从 Kafka offset 0 开始消费（最早的消息对应 v1 Schema）。Schema Registry 的兼容性检查是按什么维度判定的？消费者如何知道每条消息应该用哪个版本的 Schema 来反序列化？

2. 在 FULL 兼容策略下，能否实现 `ALTER TABLE orders MODIFY COLUMN price DECIMAL(12,4)`（从 DECIMAL(10,2) 扩展到更大精度）？如果能，为什么？如果不能，应如何分步实现？

**（第19章思考题答案）**

1. Cast SMT 不能新增字段，只能在已有字段上做类型转换。替代方案：① 编写自定义 SMT，接收 `created_at` 的 Long 值，生成新的 `created_at_iso` 字符串字段并追加到消息中；② 在 ExtractNewRecordState 之后用 HoistField 复制字段（HoistField 不支持字段复制）；最实际的方案是在下游消费端做转换。

2. 正确顺序的 SMT 链配置（注意 Filter 必须在 Unwrap 之前，因为 Filter 依赖 `value.op`）：
```json
{"transforms": "filter,unwrap,castTime,filterFields,setKey",
 "transforms.filter.type": "io.debezium.transforms.Filter",
 "transforms.filter.language": "jsr223.jexl",
 "transforms.filter.condition": "value.after.event_type != 'test'",
 "transforms.unwrap.type": "io.debezium.transforms.ExtractNewRecordState",
 "transforms.castTime.type": "org.apache.kafka.connect.transforms.Cast$Value",
 "transforms.castTime.spec": "created_at:string",
 "transforms.filterFields.type": "org.apache.kafka.connect.transforms.ReplaceField$Value",
 "transforms.filterFields.exclude": "internal_notes,user_phone",
 "transforms.setKey.type": "org.apache.kafka.connect.transforms.ValueToKey",
 "transforms.setKey.fields": "user_id"}
```

---

> **推广提示**：将本章的 DDL 预检脚本集成到 DBA 的变更管理工具中（如 Liquibase 的 `preConditions` 或 Flyway 的 `callback`）。在团队的开发规范中明确："任何涉及 CDC 表的 DDL，必须先通过 Schema Registry 兼容性预检，否则变更工单不予审批。"
