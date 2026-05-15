# 第19章：高级 SMT 实战——字段过滤、类型转换与表达式路由

## 1. 项目背景

数据分析团队拿到了 CDC 产出的 Kafka 数据后，发现三个问题让他们无法直接使用：
1. **时间类型不兼容**：MySQL 的 `DATETIME` 到了 Kafka 里变成了 13 位的 Long 型时间戳（epoch millis），而他们的 BI 工具只接受 ISO 8601 格式的字符串（`"2024-05-14T15:30:00.000Z"`）
2. **敏感字段泄露**：`internal_notes` 字段记录了运营团队的内部沟通备注（如"该用户投诉过 3 次，建议重点监控"），这些信息不应该暴露给数据分析团队。
3. **合流后拆分困难**：多张表的数据汇到了同一个 Topic，数据分析团队需要在消费端根据 `source.table` 字段手动路由到不同表——这导致 Spark/Flink 的 ETL 作业需要额外写表路由逻辑。

数据安全团队也发来了最后通牒——用户手机号必须做脱敏处理（如 `138****5678`），否则不予通过安全审计。这些需求用基础篇的单个 SMT 无法满足——Cast 只能转类型但不能过滤字段，ReplaceField 只能过滤字段但不能转类型，ValueToKey 只能改分区键但不能做字段脱敏。

**本章的核心价值**：掌握 SMT 的**链式组合**——将多个简单的 SMT 串成一个"流水线"，在消息发出之前完成全部的清洗、转换、脱敏、分区工作。

## 2. 项目设计——三人对话

**（周五下午，小胖对着数据分析团队发来的 8 条需求发呆）**

**小胖**："大师，数据分析团队来了个'八条需求'，你看——时间转格式、删敏感字段、手机号脱敏、浮点数保留两位小数、提取 user_id 做分区键...这么多需求，靠单个 SMT 根本搞不定啊！"

**大师**："单个 SMT 当然搞不定。但你可以把多个 SMT 串成一条流水线——这就是 SMT 链。每个 SMT 专职做一件事：第一个负责转时间类型（Cast），第二个负责过滤字段（ReplaceField），第三个负责脱敏（自定义 SMT），第四个负责设置分区键（ValueToKey）。记住顺序口诀：**先过滤、再拍平、后变形、最后分区**。"

**小白**："这个顺序为什么这么重要？我把 Cast 放在 ReplaceField 前面和后面有什么不同？"

**大师**："经典的反例——如果你先用 ReplaceField 删掉了 `created_at` 字段，再用 Cast 去转换 `created_at` 的类型，Cast 会找不到这个字段而报错。SMT 链的正确顺序应该是满足'前一个 SMT 的输出是后一个 SMT 的输入'——"

```
正确的 SMT 链顺序：
原始 Change Event（schema + payload）
  → Filter（丢弃不需要的事件）
  → ExtractNewRecordState（拍平，提取 after）
  → Cast（类型转换——此时字段名已变为 after 中的字段）
  → ReplaceField（字段过滤/排除）
  → 自定义 SMT（手机号脱敏/字段加密）
  → ValueToKey（提取分区键）
  → Kafka Topic
```

**技术映射**：SMT 链 = 汽车装配流水线。第一个工位检查零件（Filter），第二个工位拆包装（ExtractNewRecordState），第三个工位修正规格（Cast），第四个工位去掉多余配件（ReplaceField），第五个工位遮盖敏感标识（脱敏 SMT），最后一个工位贴物流标签（ValueToKey）。

**小胖**："但是大师，Cast SMT 只能转类型，不能做复杂的格式转换吧？比如我要把 epoch millis 转成 ISO 8601 字符串——Cast 做不到吧？"

**大师**："对，Cast 只支持简单的类型映射——`int8/int16/int32/int64/float32/float64/boolean/string`。复杂的格式转换（如 timestamp → ISO 8601 字符串）需要借助**自定义 SMT**。我来写一个 `TimestampToStringTransform`，把 Long 型时间戳转成 `"2024-05-14T15:30:00.000Z"` 格式。"

**小白**："那如果我要同时做'转格式 + 修改时区'呢？比如 MySQL 存的是 UTC 时间，下游需要北京时间（UTC+8）？"

**大师**："自定义 SMT 里处理。给你一个通用的设计——在 SMT 的 `configure()` 方法中读取配置项 `timezone` 和 `format`，然后在 `apply()` 中做对应转换。这样同一个 SMT 类可以适应多种时区和格式需求。"

---

## 3. 项目实战

### 环境准备

```bash
docker exec mysql mysql -uroot -proot1234 inventory << 'SQL'
CREATE TABLE IF NOT EXISTS analytics_events (
    id INT PRIMARY KEY AUTO_INCREMENT,
    event_type VARCHAR(50) NOT NULL,
    user_id INT NOT NULL,
    event_data JSON,
    internal_notes VARCHAR(500),
    user_phone VARCHAR(20),
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);
INSERT INTO analytics_events (event_type, user_id, event_data, internal_notes, user_phone) VALUES
('page_view', 1001, '{"url":"/home","duration":30}', 'User from campaign A, high-value', '13812345678'),
('purchase', 1002, '{"order_id":555,"amount":199.99}', 'Suspected fraud - monitor closely', '13987654321'),
('page_view', 1001, '{"url":"/product/42","duration":120}', 'Interested in electronics', '13812345678');
SQL
```

### 步骤1：SMT 链完整部署——五步变形流水线

**目标**：将一条原始 Change Event 经过 5 个 SMT 处理后，输出下游可直接使用的"干净" JSON。

```bash
curl -X POST http://localhost:8083/connectors \
  -H "Content-Type: application/json" \
  -d '{
    "name": "adv-smt-chain-connector",
    "config": {
      "connector.class": "io.debezium.connector.mysql.MySqlConnector",
      "database.hostname": "mysql",
      "database.port": "3306",
      "database.user": "debezium",
      "database.password": "dbz1234",
      "database.server.id": "184191",
      "topic.prefix": "analytics",
      "table.include.list": "inventory.analytics_events",
      "schema.history.internal.kafka.bootstrap.servers": "kafka:9092",
      "schema.history.internal.kafka.topic": "schema-changes.adv-smt",
      "snapshot.mode": "initial",

      "transforms": "unwrap,castTime,filterFields,setKey",
      
      "transforms.unwrap.type": "io.debezium.transforms.ExtractNewRecordState",
      "transforms.unwrap.delete.handling.mode": "rewrite",
      
      "transforms.castTime.type": "org.apache.kafka.connect.transforms.Cast$Value",
      "transforms.castTime.spec": "created_at:string,updated_at:string",
      
      "transforms.filterFields.type": "org.apache.kafka.connect.transforms.ReplaceField$Value",
      "transforms.filterFields.exclude": "internal_notes,__db,__table,__deleted",
      
      "transforms.setKey.type": "org.apache.kafka.connect.transforms.ValueToKey",
      "transforms.setKey.fields": "user_id"
    }
  }'

sleep 20
```

### 步骤2：验证 SMT 链的输出——逐层对比

**目标**：消费一条消息，确认 5 层 SMT 都生效。

```bash
# 消费快照数据并对比
docker exec kafka kafka-console-consumer --bootstrap-server localhost:9092 \
  --topic analytics.inventory.analytics_events --from-beginning --max-messages 1 | python3 -m json.tool
```

**预期输出与验证清单**：

```json
{
  "id": 1,
  "event_type": "page_view",
  "user_id": 1001,
  "event_data": "{\"url\":\"/home\",\"duration\":30}",
  "user_phone": "13812345678",
  "created_at": "2024-05-14T15:30:00.000Z",
  "updated_at": "2024-05-14T15:35:00.000Z"
}
```

**验证清单**：

| SMT 步骤 | 验证内容 | 预期结果 |
|---------|---------|---------|
| unwrap | 消息已拍平 | ✅ 无外层 schema + payload 包装 |
| castTime | `created_at` 已转为 string | ✅ `"2024-05-14..."` 而非 `1715600000000` |
| filterFields | 无 `internal_notes` 字段 | ✅ 消息中不包含该字段 |
| filterFields | 无 `__db` 和 `__table` | ✅ 元数据字段已清除 |
| setKey | Kafka 消息 Key 为 `user_id` | ✅ 同一 user 的事件进入同一分区 |

### 步骤3：手机号脱敏实战（Cast 无法做到的定制需求）

**目标**：`user_phone` 字段中间 4 位脱敏（`13812345678` → `138****5678`），不能在代码里处理，必须在 SMT 中完成。

这是 Cast 和 ReplaceField 都做不到的定制需求——需要写一个轻量级的自定义 SMT。

```bash
# 在 plugin.path 中创建一个自定义 SMT 类（Java）
cat > /tmp/PhoneMaskTransform.java << 'JAVAEOF'
package com.example.debezium.smt;

import org.apache.kafka.common.config.ConfigDef;
import org.apache.kafka.connect.connector.ConnectRecord;
import org.apache.kafka.connect.data.Struct;
import org.apache.kafka.connect.transforms.Transformation;
import java.util.Map;

public class PhoneMaskTransform<R extends ConnectRecord<R>> implements Transformation<R> {
    private String fieldName;

    public R apply(R record) {
        Struct value = (Struct) record.value();
        if (value == null) return record;
        String phone = value.getString(fieldName);
        if (phone != null && phone.length() == 11) {
            value.put(fieldName, phone.substring(0, 3) + "****" + phone.substring(7));
        }
        return record;
    }

    public ConfigDef config() { return new ConfigDef(); }
    public void configure(Map<String, ?> c) { this.fieldName = (String) c.get("field"); }
    public void close() {}
}
JAVAEOF

# 简化方案：由于 Java 编译需要 Maven 环境，在实验中我们用 Python 脚本模拟
# 在实际部署中，编译为 JAR 放入 plugin.path 即可使用
```

**Connector 配置中引用**（编译为 JAR 后的配置）：

```json
{
  "transforms": "unwrap,maskPhone,filterFields,setKey",
  "transforms.maskPhone.type": "com.example.debezium.smt.PhoneMaskTransform",
  "transforms.maskPhone.field": "user_phone"
}
```

### 步骤4：使用 HoistField 处理 JSON 嵌套字段

**目标**：`event_data` 是一个 JSON 字符串，下游需要把它展开为顶层字段。但 SMT 不能直接操作 JSON 字符串——先不展开，直接用 ExtractField 提取。

```json
{
  "transforms": "unwrap,extractEventData",
  "transforms.unwrap.type": "io.debezium.transforms.ExtractNewRecordState",
  "transforms.extractEventData.type": "org.apache.kafka.connect.transforms.ExtractField$Value",
  "transforms.extractEventData.field": "event_data"
}
```

此时 Kafka 消息变为纯 JSON 字符串 `{"url":"/home","duration":30}`，下游可直接 JSON.parse。

### 可能遇到的坑及解决方法

| 坑 | 现象 | 根因 | 解决 |
|----|------|------|------|
| Cast 找不到字段 | `Field not found: created_at` | Unwrap 之前的字段是 `after.created_at`，Cast 字段引用需在 Unwrap 之后 | Cast 必须放在 Unwrap **之后** |
| ReplaceField include 和 exclude 同时使用 | Connector 启动报错 | Kafka Connect 不允许同时使用 include 和 exclude | 只能选其一 |
| Cast 多个字段格式错误 | 部分字段未转换 | `spec` 格式中字段之间用逗号分隔，不能有空格 | `field1:type1,field2:type2` |
| 自定义 SMT 在 Unwrap 之前引用 after 字段 | NullPointerException | Unwrap 之前消息是 `{schema, payload:{before,after}}` 结构 | 自定义 SMT 放 Unwrap 之后 |

---

## 4. 项目总结

### 优点 & 缺点（高级 SMT 链 vs 基础 SMT vs 下游处理）

| 维度 | SMT 链（Connector 端） | 下游消费端处理 | 基础单 SMT |
|------|---------------------|---------------|-----------|
| 时效性 | ★★★★★ 消息发送前 | ★★☆☆☆ 需额外拉取 | ★★★★☆ |
| 代码复用 | ★★★★☆ 所有消费者受益 | ★★☆☆☆ 每个团队重复写 | ★★★☆☆ |
| 灵活性 | ★★★☆☆ 受 SMT 接口限制 | ★★★★★ 完全自由 | ★★☆☆☆ |
| 维护成本 | ★★★☆☆ 需 Compile + 部署 JAR | ★★★★☆ 代码仓库管理 | ★★★★☆ |

### 适用场景

1. **多团队共享 CDC 数据**：一次 SMT 配置，所有下游消费者受益
2. **安全合规**：敏感字段脱敏在消息投递前完成，符合"数据最小化"原则
3. **下游异构**：Java 团队用 Avro、Python 团队用 JSON，通过 SMT 统一格式
4. **低延迟场景**：SMT 处理在内存中，不增加端到端延迟
5. **数据瘦身**：减少 60% 的消息体积，降低 Kafka 存储和网络成本

### 注意事项

- **SMT 链长度建议 ≤ 5**：超过 5 个 SMT 时，建议考虑将部分逻辑移到 Kafka Streams 或 Flink
- **SMT 不应做 IO 操作**：不要在 SMT 中发起 HTTP 调用或数据库查询——会严重拖慢 CDC 管道
- **Cast 不支持自定义格式**：如需 `Decimal → "¥199.99"` 的转换，必须用自定义 SMT

### 常见踩坑经验

1. **"我以为 Cast 会把 Long 型 timestamp 转为可读的日期字符串，结果还是 Long"**——Cast 的 string 转换只是 `toString()`，不会格式化。需要自定义 SMT 或下游处理。
2. **"ReplaceField exclude 了 internal_notes 后，下游消费者报 FieldNotFound"**——说明下游消费者代码直接 `getString("internal_notes")`，应改用 `getString("internal_notes")` 带 null 检查。
3. **"SMT 链中有一个报错，整个消息被丢弃"**——这是默认行为（`errors.tolerance=none`）。可在 Connector 配置中设置 `errors.tolerance=all` 让 SMT 错误只记录日志不丢弃消息。

### 思考题

1. 如果下游需要 `created_at` 保留为 Long 型（epoch millis），但额外新增一个 `created_at_iso` 字段为 ISO 8601 格式的字符串，SMT 链能否实现？如果能，如何实现？如果不能，替代方案是什么？

2. 编写一条 SMT 链配置，实现以下需求：① 过滤掉 `event_type='test'` 的测试数据（Filter SMT）；② 拍平（ExtractNewRecordState）；③ 将 `created_at` 转为 string（Cast）；④ 排除 `internal_notes` 和 `user_phone`（ReplaceField）；⑤ 按 `user_id` 分区（ValueToKey）。请按正确顺序写出完整的 JSON 配置。

**（第18章思考题答案）**

1. 30 张表路由到 `topic.a` 的 JEXL 表达式：可以使用字符串包含判断 `value.source.table.matches('^(table1|table2|table3|...)$')`——但 30 个表名拼成正则需要借助脚本生成。500 张表时 JEXL 表达式不再适用。替代方案：① 将路由表存储在外部 CSM/配置文件，通过自定义 SMT 读取后决策；② 使用 Kafka Streams 的 `branch()` 方法做路由；③ 拆分多个 Connector（例如按表的首字母分组，每个 Connector 一个正则规则）。

2. 跨 Topic 无法保证有序性——Topic A 的 partition 0 和 Topic B 的 partition 0 是独立的分区，Kafka 不做跨 Topic 的顺序保证。替代方案：① 不使用跨 Topic 路由，改为同一 Topic 内部按 key 分区（基于 `table_name + id`），所有操作类型在同一分区内有序；② 下游使用 Flink 的 watermark 机制基于 `source.ts_ms` 做事件时间排序。

---

> **推广提示**：建议基础架构团队开发一个'内部 SMT 库'——将团队的通用需求（手机号脱敏、时间格式化、金额单位转换等）沉淀为可复用的自定义 SMT 类，统一维护在 Maven 私有仓库中。新 Connector 只需在配置中引用即可，零编码实现数据安全合规。
