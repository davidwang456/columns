# 第 5 章：Strings 工具集快速提效（Joiner/Splitter/CaseFormat）

## 1 项目背景

在数据同步平台的配置中心，工程师小陈遇到了一个头疼的问题。系统需要解析来自多个数据源的 CSV 文件，字段之间用逗号分隔，但字段内容里也可能包含逗号。他写了一个复杂的正则表达式来处理这种情况，结果上线后还是出现了数据错位。

另一个场景是数据库配置表里的字段命名：有的是下划线格式 `user_name`，有的是驼峰格式 `userName`，还有的是全大写 `USER_NAME`。每次做 ORM 映射都要手动写转换逻辑，代码里到处都是 `replaceAll` 和 `toLowerCase` 的组合。

**业务场景**：配置解析、数据导入导出、字段映射、日志拼接等字符串操作密集型任务。

**痛点放大**：
- **字符串拼接繁琐**：用 `+` 连接多个字段，代码冗长且低效。
- **split 行为不符合预期**：JDK 的 `String.split` 会丢弃尾部空字符串，处理 CSV 时容易丢数据。
- **空值处理复杂**：拼接时要逐个判断是否为 null。
- **格式转换样板代码**：下划线/驼峰/大写之间的转换到处复制粘贴。
- **正则表达式难以维护**：简单的字符串处理用正则，可读性差还容易出错。

如果没有一套简洁强大的字符串工具，这类任务将消耗大量开发时间。

**技术映射**：Guava 的 `Strings` 工具类提供了 `Joiner`（拼接）、`Splitter`（分割）、`CaseFormat`（命名格式转换）等工具，让字符串操作从"拼凑"变成"声明式"。

---

## 2 项目设计

**场景**：数据团队周会，讨论配置解析工具选型。

---

**小胖**：（看着满屏的 `replaceAll`）"我说，这字符串处理也太啰嗦了！我就想把一个 List 用逗号拼起来，还得先判断空、判 null，最后还得去掉最后一个逗号。这不就跟食堂阿姨数零钱一样，一块五毛都得数清楚？"

**小白**：（点头）"而且 JDK 的 `String.split` 有个大坑——它会丢弃尾部的空字符串。比如 `"a,b,".split(",")` 只返回 `{"a", "b"}`，那个空字符串被吃了！解析 CSV 时这会导致列错位。"

**大师**：（在白板上写代码）"Guava 的 `Joiner` 和 `Splitter` 就是解决这些痛点的。看这段对比：

```java
// 传统写法：null 处理噩梦
StringBuilder sb = new StringBuilder();
for (String s : list) {
    if (s != null && !s.isEmpty()) {
        if (sb.length() > 0) sb.append(",");
        sb.append(s);
    }
}
String result = sb.toString();

// Guava 写法：一行搞定
String result = Joiner.on(",").skipNulls().join(list);
```

**技术映射**：`Joiner` 就像是自动组装的流水线，你只需要指定分隔符和空值处理策略，剩下的它帮你搞定。"

**小胖**："那 `Splitter` 呢？能处理那个尾部空字符串的问题吗？"

**小白**："不仅能处理，还更强大。Guava 的 `Splitter` 是**不可变且可复用**的——你定义好规则后可以多次使用。而且它有专门的 `trimResults()` 和 `omitEmptyStrings()`：

```java
// 保留尾部空值，trim 每个结果，跳过空字符串
Splitter splitter = Splitter.on(',')
    .trimResults()
    .omitEmptyStrings();

List<String> parts = splitter.splitToList("a, b, , c,");
// 结果: ["a", "b", "c"]
```

**大师**："还有 `CaseFormat`，专门处理命名格式转换。这在 ORM 映射时特别有用：

```java
// 数据库字段名 -> Java 属性名
String javaName = CaseFormat.LOWER_UNDERSCORE
    .to(CaseFormat.LOWER_CAMEL, "user_name");
// 结果: "userName"

// Java 常量 -> 数据库字段名
String dbColumn = CaseFormat.UPPER_UNDERSCORE
    .to(CaseFormat.LOWER_UNDERSCORE, "MAX_RETRY_COUNT");
// 结果: "max_retry_count"
```

**技术映射**：`CaseFormat` 把命名格式从'字符串操作'变成'格式声明'，转换逻辑一目了然。"

**小胖**："那 `Strings` 这个类本身还有什么用？"

**小白**："`Strings` 提供了几个实用方法：
- `isNullOrEmpty(String)`：null 和空字符串一起判断
- `nullToEmpty(String)`：null 转为空字符串
- `emptyToNull(String)`：空字符串转为 null
- `padStart/End(String, int, char)`：补齐长度
- `repeat(String, int)`：重复字符串

**大师**："要注意 `Joiner` 和 `Splitter` 的一个关键区别：`Joiner` 只能处理**Map/List/数组/迭代器**，而 `Splitter` 返回的是**Iterable**。如果你需要 `List`，要调用 `splitToList()`。

**技术映射**：Guava 字符串工具的设计理念是"一次配置，多次使用"，创建好的 Joiner/Splitter 可以安全地多线程复用。"

**小胖**："那如果我想用正则分割呢？"

**大师**："`Splitter` 支持正则，但**不建议**用正则做简单分割。正则分割的性能比字符分割差很多。只有当分隔符确实需要模式匹配时才用 `Splitter.onPattern()`。

**技术映射**：性能敏感场景，优先用 `Splitter.on(char)` 而非 `Splitter.onPattern(regex)`。"

---

## 3 项目实战

### 环境准备

```xml
<dependency>
    <groupId>com.google.guava</groupId>
    <artifactId>guava</artifactId>
    <version>33.0.0-jre</version>
</dependency>
```

### 分步实现：数据同步配置解析器

**步骤目标**：用 `Joiner`、`Splitter`、`CaseFormat` 构建一个 CSV 配置解析和字段映射工具。

**代码实现**：

```java
package com.example.guava.demo;

import com.google.common.base.*;

import java.util.*;
import java.util.stream.Collectors;

/**
 * 数据同步配置解析器
 */
public class DataSyncConfigParser {

    // 预定义的 Splitter，复用以提高性能
    private static final Splitter CSV_SPLITTER = Splitter.on(',')
        .trimResults()
        .omitEmptyStrings();

    private static final Splitter KEY_VALUE_SPLITTER = Splitter.on('=')
        .trimResults()
        .limit(2);

    // 预定义的 Joiner
    private static final Joiner COMMA_JOINER = Joiner.on(',').skipNulls();
    private static final Joiner AND_JOINER = Joiner.on(" AND ").skipNulls();
    private static final Joiner.MapJoiner QUERY_JOINER = Joiner.on('&')
        .withKeyValueSeparator("=")
        .useForNull("");

    /**
     * 解析 CSV 行（支持空值和 trim）
     */
    public List<String> parseCsvLine(String line) {
        if (Strings.isNullOrEmpty(line)) {
            return Collections.emptyList();
        }
        return CSV_SPLITTER.splitToList(line);
    }

    /**
     * 解析 key=value,key=value 格式的配置
     */
    public Map<String, String> parseKeyValuePairs(String config) {
        if (Strings.isNullOrEmpty(config)) {
            return Collections.emptyMap();
        }

        return Splitter.on(',')
            .trimResults()
            .omitEmptyStrings()
            .splitToList(config)
            .stream()
            .map(KEY_VALUE_SPLITTER::splitToList)
            .filter(parts -> parts.size() == 2)
            .collect(Collectors.toMap(
                parts -> parts.get(0),
                parts -> parts.get(1),
                (v1, v2) -> v2  // 处理重复 key
            ));
    }

    /**
     * 将数据库字段名转换为 Java 属性名
     */
    public String toJavaPropertyName(String dbColumnName) {
        if (Strings.isNullOrEmpty(dbColumnName)) {
            return "";
        }
        return CaseFormat.LOWER_UNDERSCORE
            .to(CaseFormat.LOWER_CAMEL, dbColumnName);
    }

    /**
     * 将 Java 属性名转换为数据库字段名
     */
    public String toDbColumnName(String javaPropertyName) {
        if (Strings.isNullOrEmpty(javaPropertyName)) {
            return "";
        }
        return CaseFormat.LOWER_CAMEL
            .to(CaseFormat.LOWER_UNDERSCORE, javaPropertyName);
    }

    /**
     * 将常量名转换为配置键名
     */
    public String toConfigKey(String constantName) {
        if (Strings.isNullOrEmpty(constantName)) {
            return "";
        }
        return CaseFormat.UPPER_UNDERSCORE
            .to(CaseFormat.LOWER_HYPHEN, constantName);
    }

    /**
     * 拼接查询条件（SQL WHERE 子句）
     */
    public String buildWhereClause(List<String> conditions) {
        if (conditions == null || conditions.isEmpty()) {
            return "";
        }
        return AND_JOINER.join(conditions);
    }

    /**
     * 拼接查询字符串
     */
    public String buildQueryString(Map<String, String> params) {
        if (params == null || params.isEmpty()) {
            return "";
        }
        return QUERY_JOINER.join(params);
    }

    /**
     * 用指定长度补齐字符串
     */
    public String padOrderNo(String orderNo, int minLength) {
        if (orderNo == null) {
            orderNo = "";
        }
        return Strings.padStart(orderNo, minLength, '0');
    }

    /**
     * 生成重复分隔线
     */
    public String generateSeparator(int length) {
        return Strings.repeat("-", length);
    }

    /**
     * 将可能为 null 的字符串转为空字符串
     */
    public String nullToEmpty(String str) {
        return Strings.nullToEmpty(str);
    }

    /**
     * 批量转换数据库字段名为 Java 属性名
     */
    public Map<String, String> convertColumnMappings(List<String> dbColumns) {
        Map<String, String> mappings = new HashMap<>();
        for (String column : dbColumns) {
            if (!Strings.isNullOrEmpty(column)) {
                mappings.put(column, toJavaPropertyName(column));
            }
        }
        return mappings;
    }
}
```

### 测试验证

```java
package com.example.guava.demo;

import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

import java.util.Arrays;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

public class DataSyncConfigParserTest {

    private final DataSyncConfigParser parser = new DataSyncConfigParser();

    @Test
    public void testParseCsvLine() {
        List<String> result = parser.parseCsvLine("a, b, , c,");
        assertEquals(Arrays.asList("a", "b", "c"), result);
    }

    @Test
    public void testParseCsvLine_emptyInput() {
        List<String> result = parser.parseCsvLine("");
        assertTrue(result.isEmpty());
    }

    @Test
    public void testParseCsvLine_nullInput() {
        List<String> result = parser.parseCsvLine(null);
        assertTrue(result.isEmpty());
    }

    @Test
    public void testParseKeyValuePairs() {
        Map<String, String> result = parser.parseKeyValuePairs(
            "host=localhost, port=3306, user=admin"
        );
        assertEquals("localhost", result.get("host"));
        assertEquals("3306", result.get("port"));
        assertEquals("admin", result.get("user"));
    }

    @Test
    public void testToJavaPropertyName() {
        assertEquals("userName", parser.toJavaPropertyName("user_name"));
        assertEquals("orderItemList", parser.toJavaPropertyName("order_item_list"));
        assertEquals("id", parser.toJavaPropertyName("id"));
    }

    @Test
    public void testToDbColumnName() {
        assertEquals("user_name", parser.toDbColumnName("userName"));
        assertEquals("order_item_list", parser.toDbColumnName("orderItemList"));
    }

    @Test
    public void testToConfigKey() {
        assertEquals("max-retry-count", parser.toConfigKey("MAX_RETRY_COUNT"));
        assertEquals("database-url", parser.toConfigKey("DATABASE_URL"));
    }

    @Test
    public void testBuildWhereClause() {
        List<String> conditions = Arrays.asList(
            "status = 'ACTIVE'",
            "created_at > '2024-01-01'",
            null,
            "amount > 100"
        );
        String result = parser.buildWhereClause(conditions);
        assertEquals("status = 'ACTIVE' AND created_at > '2024-01-01' AND amount > 100", result);
    }

    @Test
    public void testBuildQueryString() {
        Map<String, String> params = new HashMap<>();
        params.put("page", "1");
        params.put("size", "10");
        params.put("keyword", null);  // null 会被转为空字符串
        
        String result = parser.buildQueryString(params);
        assertTrue(result.contains("page=1"));
        assertTrue(result.contains("size=10"));
        assertTrue(result.contains("keyword="));
    }

    @Test
    public void testPadOrderNo() {
        assertEquals("00042", parser.padOrderNo("42", 5));
        assertEquals("12345", parser.padOrderNo("12345", 5));
        assertEquals("00000", parser.padOrderNo(null, 5));
    }

    @Test
    public void testGenerateSeparator() {
        assertEquals("-----", parser.generateSeparator(5));
        assertEquals("", parser.generateSeparator(0));
    }

    @Test
    public void testNullToEmpty() {
        assertEquals("", parser.nullToEmpty(null));
        assertEquals("hello", parser.nullToEmpty("hello"));
    }

    @Test
    public void testConvertColumnMappings() {
        List<String> columns = Arrays.asList("user_name", "order_id", "created_at");
        Map<String, String> mappings = parser.convertColumnMappings(columns);
        
        assertEquals("userName", mappings.get("user_name"));
        assertEquals("orderId", mappings.get("order_id"));
        assertEquals("createdAt", mappings.get("created_at"));
    }
}
```

### 可能遇到的坑及解决方法

| 坑点 | 现象 | 解决方法 |
|------|------|----------|
| `Splitter` 返回 Iterable 而非 List | 多次遍历导致重复分割 | 调用 `splitToList()` 缓存结果 |
| `Joiner` 遇到 null 未设置策略 | 抛出 NullPointerException | 使用 `skipNulls()` 或 `useForNull()` |
| 正则分割性能差 | 大数据量处理慢 | 简单分隔符用 `Splitter.on(char)` |
| `CaseFormat` 输入格式不匹配 | 转换结果异常 | 确保输入符合声明的格式 |

---

## 4 项目总结

### 优缺点对比

| 维度 | Guava Strings | JDK String | Apache Commons |
|------|---------------|------------|----------------|
| 拼接功能 | ★★★★★ Joiner 强大 | ★ StringJoiner | ★★★★ StringUtils |
| 分割功能 | ★★★★★ 灵活可控 | ★★ split 有坑 | ★★★ StringUtils |
| 格式转换 | ★★★★★ CaseFormat | ★ 无 | ★ 无 |
| null 处理 | ★★★★★ 完善 | ★★ 需手动 | ★★★★ 完善 |
| 可读性 | ★★★★★ 声明式 | ★★★ 命令式 | ★★★★ 方法丰富 |

### 适用场景

1. **CSV/配置解析**：`Splitter` 的 trim 和空值处理
2. **SQL 动态拼接**：`Joiner` 构建 IN 子句和 WHERE 条件
3. **ORM 字段映射**：`CaseFormat` 转换命名风格
4. **日志格式化**：`Joiner` 拼接多字段日志
5. **URL 参数拼接**：`MapJoiner` 构建查询字符串

### 不适用场景

1. **复杂正则匹配**：用 `Pattern` 和 `Matcher`
2. **国际化字符串处理**：用 ICU4J 库
3. **HTML/XML 转义**：用专用转义库

### 生产踩坑案例

**案例 1：未正确处理 null 导致 NPE**
```java
// 坑：未设置 null 处理策略
Joiner.on(",").join(list);  // list 中有 null 时 NPE
```
解决：使用 `skipNulls()` 或 `useForNull("N/A")`。

**案例 2：Splitter 多次遍历性能问题**
```java
// 坑：每次遍历都重新分割
for (String part : splitter.split(line)) { ... }
for (String part : splitter.split(line)) { ... }  // 重复分割！
```
解决：调用 `splitToList()` 缓存结果。

**案例 3：CaseFormat 输入格式错误**
```java
// 坑：输入实际是驼峰，但声明为下划线
CaseFormat.LOWER_UNDERSCORE.to(..., "userName");  // 结果错误
```
解决：确认输入字符串的实际格式。

### 思考题答案（第 4 章思考题 1）

> **问题**：在领域模型中，哪些字段应该参与 `equals` 比较？

**答案**：
1. **应该包含**：业务唯一标识（如 orderId）、不可变属性、影响业务等价性的字段
2. **不应该包含**：自动生成的主键（如数据库自增ID）、时间戳、临时状态、计算字段

**举例**：订单的 `equals` 应该比较订单号，而不是比较自增 ID 或创建时间。

### 新思考题

1. `Splitter` 的 `limit(n)` 方法在什么场景下有用？请举例说明。
2. 如果要实现一个支持转义字符的 CSV 解析器（如 `"a,b","c"` 解析为 `[