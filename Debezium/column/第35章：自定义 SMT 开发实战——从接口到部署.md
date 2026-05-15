# 第35章：自定义 SMT 开发实战——从接口到部署

## 1. 项目背景

安全团队在上一章提出了数据脱敏的需求，而数据分析团队又提出了一个新的需求："我们做用户行为分析只需要 `event_type`、`user_id` 和 `event_data` 这三个字段。其他的 20 多个字段（`created_at`、`updated_at`、`internal_notes`、`source_ip` 等）对我们全是噪音，不仅浪费带宽，还增加了下游 Spark/Flink 的解析开销。能不能在 Connector 发消息之前，把消息瘦身到只剩我们需要的 3 个字段？"

内置的 ReplaceField 可以做字段过滤（`include`/`exclude`），Cast 可以做类型转换，但这些都是在已有字段上做"减法"或"变形"。如果需求是"把字段 A 和字段 B 的值拼接成新字段 C"、"把 Long 型时间戳转成 ISO 8601 格式的字符串"、"对手机号做中间 4 位遮盖"——这些都超出了内置 SMT 的能力范围。

**自定义 SMT** 正是填补这个空白。本章将从零实现一个 `PhoneMaskTransform`，完整覆盖编码→打包→SPI 注册→部署→Connector 引用→单元测试的全流程。这个流程是标准化的——无论你开发什么自定义 SMT（脱敏、加密、格式转换、字段拼接），只需要替换 `apply()` 方法中的业务逻辑即可。

## 2. 项目设计——三人对话

**小胖**："大师，我看了 Kafka Connect 的 Transformation 接口，就三个方法——`apply()`、`configure()`、`config()`。但 apply() 里面怎么拿到 Change Event 的字段值呢？原始事件是 `{schema, payload}` 还是已经拍平过的 `{id, name, phone}`？"

**大师**："这取决于你的 SMT 放在 SMT 链的哪个位置。如果是放在 ExtractNewRecordState **之前**——你操作的是 `{schema:{...}, payload:{before:{...}, after:{...}}}` 结构，需要用 `((Struct) record.value()).getStruct("payload").getStruct("after").getString("phone")` 来读取字段。如果是放在 ExtractNewRecordState **之后**——消息已经拍平了，直接用 `((Struct) record.value()).getString("phone")` 即可。**强烈推荐放在 Unwrap 之后**——代码简单，且能和 ReplaceField/Cast 等内置 SMT 共享顺序。"

**小白**："那 `configure()` 方法的参数是从哪来的？就是 Connector JSON 配置里的 `transforms.xxx.field: phone` 这样传进来的？"

**大师**："对。Kafka Connect 框架会自动把 `transforms.<alias>.<key>` 格式的配置项解析成 `Map<String, String>` 传给 `configure()`。比如这样配——"

```json
{
  "transforms": "maskPhone",
  "transforms.maskPhone.type": "com.example.PhoneMaskTransform",
  "transforms.maskPhone.field": "phone"
}
```

框架会提取 `maskPhone` 对应的所有配置 → 去掉前缀 → `{field: "phone", type: "com.example.PhoneMaskTransform"}` → 传给 `configure()`。

**小胖**："那 `config()` 方法返回的 ConfigDef 是什么用途？如果不写会怎样？"

**大师**："ConfigDef 是给 Kafka Connect 的 REST API 做参数校验和文档生成的。它告诉框架'这个 SMT 有哪些配置项、各自是什么类型、是否必填、有没有默认值'。如果你不写 ConfigDef，SMT 也能工作——但 Connector 启动时不会对配置做校验，比如你拼错了参数名（`filed` 而不是 `field`），SMT 不会报错，静默地读到一个 null，手机号脱敏不会生效——**这就是最隐蔽的 bug**。"

## 3. 项目实战

### 步骤1：Maven 项目结构

```xml
<?xml version="1.0" encoding="UTF-8"?>
<project>
    <modelVersion>4.0.0</modelVersion>
    <groupId>com.example.debezium</groupId>
    <artifactId>debezium-smt-phone-mask</artifactId>
    <version>1.0.0</version>
    <packaging>jar</packaging>
    
    <properties>
        <maven.compiler.source>17</maven.compiler.source>
        <maven.compiler.target>17</maven.compiler.target>
        <kafka.version>3.6.0</kafka.version>
    </properties>
    
    <dependencies>
        <dependency>
            <groupId>org.apache.kafka</groupId>
            <artifactId>connect-api</artifactId>
            <version>${kafka.version}</version>
            <scope>provided</scope>
        </dependency>
        <dependency>
            <groupId>org.apache.kafka</groupId>
            <artifactId>connect-transforms</artifactId>
            <version>${kafka.version}</version>
            <scope>provided</scope>
        </dependency>
        
        <!-- 测试依赖 -->
        <dependency>
            <groupId>junit</groupId>
            <artifactId>junit</artifactId>
            <version>4.13.2</version>
            <scope>test</scope>
        </dependency>
    </dependencies>
    
    <build>
        <plugins>
            <plugin>
                <groupId>org.apache.maven.plugins</groupId>
                <artifactId>maven-shade-plugin</artifactId>
                <version>3.5.0</version>
                <executions>
                    <execution>
                        <phase>package</phase>
                        <goals><goal>shade</goal></goals>
                    </execution>
                </executions>
            </plugin>
        </plugins>
    </build>
</project>
```

### 步骤2：核心实现（完整版）

```java
package com.example.debezium.smt;

import org.apache.kafka.common.config.ConfigDef;
import org.apache.kafka.common.config.ConfigException;
import org.apache.kafka.connect.connector.ConnectRecord;
import org.apache.kafka.connect.data.Struct;
import org.apache.kafka.connect.errors.DataException;
import org.apache.kafka.connect.transforms.Transformation;
import org.apache.kafka.connect.transforms.util.SimpleConfig;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.util.Map;

/**
 * 手机号脱敏 SMT
 * 将 11 位手机号的中间 4 位替换为 ****
 * 示例: 13812345678 → 138****5678
 *      +8613812345678 → +86138****5678
 */
public class PhoneMaskTransform<R extends ConnectRecord<R>> 
        implements Transformation<R> {
    
    private static final Logger LOGGER = LoggerFactory.getLogger(PhoneMaskTransform.class);
    
    private static final String FIELD_CONFIG = "field";
    private String fieldName;
    
    @Override
    public R apply(R record) {
        // 1. 获取 value (ExtractNewRecordState 之后是拍平的结构体)
        final Struct value = operateValue(record);
        if (value == null) {
            LOGGER.trace("Record value is null, skipping");
            return record;
        }
        
        // 2. 获取目标字段的值
        String phone;
        try {
            phone = value.getString(fieldName);
        } catch (DataException e) {
            // 字段不存在或类型不是 String → 静默跳过（可能是其他表的消息）
            LOGGER.trace("Field '{}' not found or not a String in this record, skipping", fieldName);
            return record;
        }
        
        // 3. 执行脱敏
        if (phone != null && !phone.isEmpty()) {
            String masked = maskPhone(phone);
            if (!masked.equals(phone)) {
                value.put(fieldName, masked);
                LOGGER.debug("Masked phone field '{}': {} → {}", fieldName, phone, masked);
            }
        }
        
        return record;
    }
    
    /**
     * 脱敏核心逻辑
     */
    private String maskPhone(String phone) {
        // 标准 11 位手机号: 13812345678
        if (phone.length() == 11 && phone.matches("\\d{11}")) {
            return phone.substring(0, 3) + "****" + phone.substring(7);
        }
        // 带国际区号: +8613812345678
        if (phone.length() == 14 && phone.startsWith("+86") 
                && phone.substring(3).matches("\\d{11}")) {
            return phone.substring(0, 6) + "****" + phone.substring(10);
        }
        // 其他格式：不处理（可能不是手机号）
        LOGGER.debug("Phone field '{}' value does not match expected format: {}", fieldName, phone);
        return phone;
    }
    
    // 兼容 ExtractNewRecordState 前后的两种 value 结构
    private Struct operateValue(R record) {
        Struct value = (Struct) record.value();
        if (value == null) return null;
        // 如果 value 中还有 "after" 字段 → Unwrap 之前 → 从 after 中取
        if (value.schema().field("after") != null) {
            return value.getStruct("after");
        }
        return value;  // Unwrap 之后 → value 本身就是拍平的数据
    }
    
    @Override
    public ConfigDef config() {
        return new ConfigDef()
            .define(FIELD_CONFIG, ConfigDef.Type.STRING, ConfigDef.Importance.HIGH,
                "The phone number field to mask (e.g. 'phone', 'contact_phone', 'mobile')");
    }
    
    @Override
    public void configure(Map<String, ?> configs) {
        SimpleConfig config = new SimpleConfig(config(), configs);
        this.fieldName = config.getString(FIELD_CONFIG);
        if (fieldName == null || fieldName.isEmpty()) {
            throw new ConfigException("'field' configuration is required for PhoneMaskTransform");
        }
        LOGGER.info("PhoneMaskTransform configured to mask field: {}", fieldName);
    }
    
    @Override
    public void close() {
        // No resources to close
    }
}
```

### 步骤3：SPI 注册 + 打包

```
# src/main/resources/META-INF/services/org.apache.kafka.connect.transforms.Transformation
com.example.debezium.smt.PhoneMaskTransform
```

```bash
mvn clean package -DskipTests
# 产出: target/debezium-smt-phone-mask-1.0.0.jar
ls -lh target/debezium-smt-phone-mask-1.0.0.jar
```

### 步骤4：部署到 Kafka Connect

```bash
cp target/debezium-smt-phone-mask-1.0.0.jar ~/debezium-lab/plugins/
docker restart connect
sleep 20

# 验证 SMT 已加载
curl -s http://localhost:8083/connector-plugins | python3 -c "
import sys, json
plugins = json.load(sys.stdin)
for p in plugins:
    if 'PhoneMaskTransform' in p.get('class', ''):
        print('✅ Loaded:', p['class'])"
```

### 步骤5：Connector 中引用 + 验证

```json
{
  "name": "smt-phone-mask-test",
  "config": {
    "...": "...",
    "transforms": "unwrap,maskPhone,dropFields,setKey",
    "transforms.unwrap.type": "io.debezium.transforms.ExtractNewRecordState",
    "transforms.maskPhone.type": "com.example.debezium.smt.PhoneMaskTransform",
    "transforms.maskPhone.field": "phone",
    "transforms.dropFields.type": "org.apache.kafka.connect.transforms.ReplaceField$Value",
    "transforms.dropFields.exclude": "__db,__table,__deleted",
    "transforms.setKey.type": "org.apache.kafka.connect.transforms.ValueToKey",
    "transforms.setKey.fields": "user_id"
  }
}
```

### 步骤6：单元测试

```java
import org.junit.Test;
import static org.junit.Assert.*;

import org.apache.kafka.connect.data.Schema;
import org.apache.kafka.connect.data.SchemaBuilder;
import org.apache.kafka.connect.data.Struct;
import org.apache.kafka.connect.source.SourceRecord;

import java.util.HashMap;
import java.util.Map;

public class PhoneMaskTransformTest {
    
    @Test
    public void testStandardPhoneNumber() {
        PhoneMaskTransform<SourceRecord> transform = new PhoneMaskTransform<>();
        Map<String, String> config = new HashMap<>();
        config.put("field", "phone");
        transform.configure(config);
        
        // 构造拍平后的消息 value
        Schema valueSchema = SchemaBuilder.struct()
            .field("id", Schema.INT32_SCHEMA)
            .field("name", Schema.STRING_SCHEMA)
            .field("phone", Schema.STRING_SCHEMA)
            .build();
        Struct value = new Struct(valueSchema)
            .put("id", 1)
            .put("name", "Alice")
            .put("phone", "13812345678");
        
        SourceRecord input = new SourceRecord(
            null, null, "test", 0, null, null, null, null, value);
        
        SourceRecord output = transform.apply(input);
        Struct result = (Struct) output.value();
        
        assertEquals("138****5678", result.getString("phone"));
        assertEquals("Alice", result.getString("name"));  // 其他字段不受影响
    }
    
    @Test
    public void testInternationalPhoneNumber() {
        PhoneMaskTransform<SourceRecord> transform = new PhoneMaskTransform<>();
        transform.configure(Map.of("field", "phone"));
        
        Schema schema = SchemaBuilder.struct()
            .field("phone", Schema.STRING_SCHEMA).build();
        Struct value = new Struct(schema).put("phone", "+8613812345678");
        
        SourceRecord output = transform.apply(
            new SourceRecord(null, null, "test", 0, null, null, null, null, value));
        assertEquals("+86138****5678", ((Struct) output.value()).getString("phone"));
    }
    
    @Test
    public void testNullPhone() {
        PhoneMaskTransform<SourceRecord> transform = new PhoneMaskTransform<>();
        transform.configure(Map.of("field", "phone"));
        
        Schema schema = SchemaBuilder.struct()
            .field("phone", Schema.OPTIONAL_STRING_SCHEMA).build();
        Struct value = new Struct(schema).put("phone", null);
        
        SourceRecord output = transform.apply(
            new SourceRecord(null, null, "test", 0, null, null, null, null, value));
        assertNull(((Struct) output.value()).getString("phone"));
        // 应该不抛异常，静默通过
    }
}
```

## 4. 项目总结

| 开发阶段 | 操作 | 耗时 |
|---------|------|------|
| 编码 (apply) | 实现核心脱敏逻辑 | 20 分钟 |
| SPI 注册 | META-INF/services 文件一行 | 1 分钟 |
| Maven 打包 | `mvn clean package` | 30 秒 |
| 部署 | 复制 JAR → restart Connect | 2 分钟 |
| 配置引用 | 在 Connector JSON 加 transforms 块 | 1 分钟 |
| 单元测试 | 3 个测试用例覆盖正常/国际号/null | 10 分钟 |

### 思考题

1. SMT 设计中有一个原则：不要在 `apply()` 中发起 IO 操作（HTTP 调用、数据库查询）。为什么？如果需要在 SMT 中调用外部 KMS（Key Management Service）解密密钥——这个设计违背了这个原则吗？如果改成在 `configure()` 中预加载密钥到内存，是否更合适？

2. 当前 PhoneMaskTransform 只支持配置单个字段名。如果一张表有 `phone`、`contact_phone`、`emergency_phone` 三个手机号字段都需要脱敏——如何设计配置项以支持多字段？提示：参考 ReplaceField 的 `exclude` 参数支持逗号分隔的字段列表。

**（第34章思考题答案）**

1. 延迟下限 = 轮询间隔（如 1 分钟）+ binlog 写入延迟（如 100ms）+ 网络延迟。边缘场景：① 无法捕获 ROLLBACK（事务回滚的数据在轮询时已经不在表中）；② 无法捕获物理 DELETE（行已删除，无法通过 `WHERE updated_at >` 查到）；③ 无法保证顺序性（两个事务 A 和 B 的提交时间戳可能相同或交错）；④ 大表轮询会造成数据库读压力。

2. 使用 `snapshot.locking.mode=none`（不持锁）+ `tidb_snapshot` 历史时间点快照——设置 `database.initial.statements=SET @@tidb_snapshot = NOW()`，让快照阶段的所有 SELECT 都读同一个时间点的 MVCC 快照。然后在快照开始前记录该时间点对应的 binlog 位点（通过 TiCDC 的时间戳→位点映射查询），快照完成后从该位点开始 streaming。这样即使不用全局读锁，也能保证快照一致性和无缝衔接。

---

> **推广提示**：将自定义 SMT 的 Maven 项目模板存入团队的 Maven Archetype 仓库（`mvn archetype:create-from-project`）。新 SMT 开发时用 Archetype 生成骨架，只需要实现 `apply()` 中的业务逻辑。团队应维护一个内部 SMT 库：手机号脱敏、身份证脱敏、AES 加密、时间格式转换、JSON 字段提取——存入 Maven 私有仓库，所有 Connector 通过配置引用即可使用。
