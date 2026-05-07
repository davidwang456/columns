# 第4章：DataX 数据模型——Column 六种类型系统

## 1. 项目背景

某支付平台的数据工程师小王接到一个任务：将核心交易库中的交易流水表同步到数据仓库。源表字段包括 `transaction_id(bigint)`、`amount(decimal)`、`pay_time(datetime)`、`status(tinyint)`、`sign_data(binary)`、`is_refunded(bit)`。小王用 DataX 配好任务，运行 3 分钟后任务报错终止，日志显示：

```
DataXException: 脏数据超过限制。脏数据记录: Column[字段名:sign_data, 类型:BytesColumn, 值:BINARY_DATA]
Caused by: java.lang.IllegalArgumentException: 不支持从BytesColumn转换到StringColumn
```

小王懵了——为什么 DataX 不能直接把二进制字段同步过去？目标表明明也是 MySQL，字段类型完全一样啊！

这个问题的根源在于：小王不理解 DataX 内部的数据模型。DataX 不是直接把 MySQL 的 `binary(32)` 写入目标 MySQL 的 `binary(32)`，而是经过了一个中间抽象——每种数据源的字段先转换为 DataX 内部的 Column 类型，传输完成后再从 Column 转换为目标端的字段类型。Column 是 DataX 的"世界语"，所有数据源都要先翻译成这门语言，再从这门语言翻译回去。理解 Column 类型系统，是理解 DataX 一切数据行为的基石。

## 2. 项目设计——剧本式交锋对话

**（茶水间，小王泡了杯咖啡，愁眉苦脸）**

**小胖**：（递过来一包薯片）小王，你的任务又挂了？不是说配好了吗？

**小王**：是啊，我源端和目标端字段类型完全一样，为啥 DataX 还报"不支持类型转换"？

**小胖**：（嚼着薯片）这不就跟翻译一样吗？你以为是中文→中文（直接写），实际上是中文→世界语→中文。中间经过 DataX 自己的 Column 类型，如果你的字段在翻译成世界语时找不到对应的词，就挂了。

**小白**：（放下手中的《Java 编程思想》）准确说，DataX 内部定义了一组标准数据类型来"规范化"所有外部数据源的字段。Column 抽象类规定了六个子类型：`StringColumn`、`LongColumn`、`DoubleColumn`、`DateColumn`、`BoolColumn`、`BytesColumn`。任何 Reader 读到的数据，都必须映射到这六种之一。

**小胖**：那我有个问题——MySQL 的 `int` 映射到 `LongColumn`，为啥不映射到 `IntegerColumn`？Long 不是 64 位吗？int 只有 32 位。

**大师**：（推门进来）问得好。这是 Java 语言特性妥协的结果。DataX 的 `LongColumn` 底层存的是 Java 的 `Long` 类型（64位），之所以不用 `Integer`，是因为很多数据库的整数类型上限超过 32 位，比如 MySQL 的 `bigint unsigned` 最大到 2^64-1，Java 的 `Integer` 根本存不下。选 `Long` 是一个"向上兼容"的设计。

**技术映射**：LongColumn = 特大号收纳箱。不管你是小件（tinyint）还是大件（bigint），这个箱子都装得下。

**小白**：那为什么还有 `DoubleColumn`，而不是统一的 `BigDecimalColumn` 来保证精度？

**大师**：这是性能与精度的权衡。`BigDecimal` 运算速度比 `double` 慢一个数量级。对数据同步场景来说，99% 的 `decimal` 字段精度要求不需要 `BigDecimal`。但如果你真的需要——比如金融行业的金额字段，精度要求到分（0.01），用 `DoubleColumn` 确实可能产生浮点误差。这时你需要在 Transformer 层面处理，或者考虑自定义 Column 子类。

**小胖**：（掏出手机）哎等一下，我看 MySQL 有几十种数据类型，DataX 只用 6 种 Column 就能全部覆盖了？

**大师**：（拿起白板笔）这就是列映射表的作用，看看这张对应关系：

| MySQL 类型 | DataX Column 类型 |
|-----------|------------------|
| tinyint, smallint, int, bigint | LongColumn |
| float, double, decimal | DoubleColumn |
| varchar, char, text, json, enum | StringColumn |
| datetime, timestamp, date, time | DateColumn |
| bit(1), boolean | BoolColumn |
| binary, varbinary, blob | BytesColumn |

你看，几十种 MySQL 类型最终只映射到 6 种 Column。这就是"内部标准类型"的价值——任何 Reader 只需要实现"外部类型 → Column"的映射逻辑，任何 Writer 只需要实现"Column → 外部类型"的转换逻辑。插件开发者不需要理解所有其他外部类型。

**技术映射**：6 种 Column = 6 种国际标准集装箱。不管你的货物是啥（MySQL varchar / Oracle VARCHAR2 / PG text），装进集装箱后就都是"STRING 箱"，卸货时再按目标要求拆分。

**小白**：那 Column 之间能不能互相转换？比如 LongColumn 转 StringColumn？

**大师**：可以，这就是 `ColumnCast` 机制。每个 Column 子类都实现了 `asString()`、`asLong()`、`asDouble()`、`asDate()`、`asBoolean()`、`asBytes()` 方法。比如：
- `LongColumn.asString()` → 返回 `String.valueOf(rawData)`
- `StringColumn.asLong()` → 尝试 `Long.parseLong(rawData)`，失败抛异常
- `BytesColumn.asString()` → 报错！这就是小王遇到的那个错误——BytesColumn 不支持直接转 StringColumn

DataX 禁止一些没有语义的跨类型转换，比如二进制流转字符串——你应该用 base64 等显式编码，而不是依赖隐式转换。

## 3. 项目实战

### 3.1 步骤一：查看 Column 抽象类源码

**目标**：理解 Column 的接口契约。

打开 `common/src/main/java/com/alibaba/datax/common/element/Column.java`：

```java
public abstract class Column {
    // 六大标准类型枚举
    public enum Type {
        BAD, NULL,   // BAD表示脏数据占位, NULL表示空值
        LONG, DOUBLE, STRING, DATE, BOOL, BYTES
    }

    private Type type;          // 当前Column的类型
    private Object rawData;     // 原始数据（底层存储值）
    private int byteSize;       // 该字段占用的字节数

    public Column(final Object object, final Type type, int byteSize) {
        this.rawData = object;
        this.type = type;
        this.byteSize = byteSize;
    }

    // 跨类型转换方法（子类override）
    public abstract Long asLong();
    public abstract Double asDouble();
    public abstract String asString();
    public abstract Date asDate();
    public abstract byte[] asBytes();
    public abstract Boolean asBoolean();

    // 获取当前类型的值
    public Object getRawData() { return this.rawData; }
    public Type getType() { return this.type; }
    public int getByteSize() { return this.byteSize; }
}
```

关键设计点：

1. **Type 枚举**：定义了 8 种类型——除了 6 种子类型对应的枚举值，还有 `BAD`（脏数据占位）和 `NULL`（空值）。`BAD` 通常由脏数据收集器使用，记录无法解析的数据。
2. **rawData**：存储实际的 Java 对象——`LongColumn` 存 `Long`、`StringColumn` 存 `String`、`DateColumn` 存 `java.util.Date` 等。
3. **byteSize**：该字段在内存中的大小，用于内存估算和日志统计。Record 的总 byteSize = 所有 Column 的 byteSize 之和。

### 3.2 步骤二：编写单元测试验证六种 Column

**目标**：亲手创建 6 种 Column 并验证跨类型转换行为。

创建 Maven 测试类（放在 common 模块的 src/test/java 下）：

```java
import com.alibaba.datax.common.element.*;
import org.junit.Test;
import java.util.Date;
import static org.junit.Assert.*;

public class ColumnTypeTest {

    @Test
    public void testLongColumn() {
        // 创建 LongColumn
        LongColumn col = new LongColumn(12345L);
        assertEquals(Column.Type.LONG, col.getType());
        assertEquals((Long) 12345L, col.getRawData());
        assertEquals(Long.valueOf(12345), col.asLong());
        assertEquals("12345", col.asString());      // Long → String 允许
        assertEquals((Double) 12345.0, col.asDouble()); // Long → Double 允许
        assertFalse(col.asBoolean());               // Long: 0=false, 非0=true? 注意:12345≠0 返回false?!
    }

    @Test
    public void testStringColumn() {
        StringColumn col = new StringColumn("hello");
        assertEquals(Column.Type.STRING, col.getType());
        assertEquals("hello", col.asString());

        // String 转 Long: 可解析的数字字符串可以转换
        StringColumn numCol = new StringColumn("2026");
        assertEquals(Long.valueOf(2026), numCol.asLong());

        // String 转 Long: 不可解析的字符串抛异常
        try {
            col.asLong();
            fail("Should throw NumberFormatException");
        } catch (NumberFormatException e) {
            // 预期行为
        }
    }

    @Test
    public void testDateColumn() {
        Date now = new Date();
        DateColumn col = new DateColumn(now);
        assertEquals(Column.Type.DATE, col.getType());
        assertEquals(now, col.getRawData());
        assertEquals(now, col.asDate());

        // Date → Long: 返回时间戳（毫秒）
        assertEquals(Long.valueOf(now.getTime()), col.asLong());
    }

    @Test
    public void testBytesColumn() {
        byte[] data = new byte[]{0x01, 0x02, 0x03};
        BytesColumn col = new BytesColumn(data);
        assertEquals(Column.Type.BYTES, col.getType());
        assertArrayEquals(data, col.asBytes());

        // Bytes → String: 禁止，抛异常
        try {
            col.asString();
            fail("Should throw IllegalArgumentException");
        } catch (IllegalArgumentException e) {
            assertTrue(e.getMessage().contains("BytesColumn"));
        }
    }

    @Test
    public void testBoolColumn() {
        BoolColumn col = new BoolColumn(true);
        assertEquals(Column.Type.BOOL, col.getType());
        assertTrue(col.asBoolean());
        assertEquals("true", col.asString());   // Bool → String: 输出 "true"/"false"
        assertEquals(Long.valueOf(1), col.asLong()); // Bool → Long: true=1, false=0
    }
}
```

运行结果：

```
Tests run: 5, Failures: 1  (testLongColumn.asBoolean() 断言失败)

实际值: asBoolean() 返回 false
预期值: 12345 ≠ 0，应返回 true？
         
源码核查: LongColumn.asBoolean() 实现:
    @Override
    public Boolean asBoolean() {
        if (null == this.getRawData()) return null;
        return (Long) this.getRawData() != 0;  // 正确！非0返回true
    }
    
测试错误原因: assertEquals(false, col.asBoolean()): 12345 ≠ 0, asBoolean() 返回 true!

修正: assertTrue(col.asBoolean());  // 非0Long → true
```

### 3.3 步骤三：理解 Record 接口与 DefaultRecord

**目标**：掌握 DataX 中"一行数据"的表示方式。

Record 接口定义（位于 common/src/main/java/com/alibaba/datax/common/element/Record.java）：

```java
public interface Record {
    void addColumn(Column column);       // 添加一列
    void setColumn(int i, Column column); // 设置第i列
    Column getColumn(int i);             // 获取第i列
    int getColumnNumber();               // 总列数
    int getByteSize();                   // 当前行的内存占用
    int getMemorySize();                 // 实际内存占用估算
}
```

DefaultRecord 实现（位于 core/src/main/java/com/alibaba/datax/core/transport/record/DefaultRecord.java）：

```java
public class DefaultRecord implements Record {
    private List<Column> columns = new ArrayList<Column>();

    @Override
    public void addColumn(Column column) {
        columns.add(column);
    }

    @Override
    public Column getColumn(int i) {
        if (i < 0 || i >= columns.size()) return null;
        return columns.get(i);
    }

    @Override
    public int getByteSize() {
        int size = 0;
        for (Column col : columns) {
            if (col != null && col.getRawData() != null) {
                size += col.getByteSize();
            }
        }
        return size;
    }

    @Override
    public int getMemorySize() {
        // 实际内存 = Column数 × 平均列大小 + 对象头开销
        return getByteSize() + columns.size() * 64;
    }
}
```

完整使用示例：

```java
@Test
public void testRecordLifecycle() {
    // 1. 创建 Record
    Record record = new DefaultRecord();

    // 2. 添加 4 列数据（模拟一行订单）
    record.addColumn(new LongColumn(10001L));          // 订单ID
    record.addColumn(new StringColumn("张三"));          // 用户名
    record.addColumn(new DoubleColumn(299.99));         // 金额
    record.addColumn(new DateColumn(new Date()));       // 下单时间

    // 3. 读取各列
    System.out.println("订单ID: " + record.getColumn(0).asLong());
    System.out.println("用户名: " + record.getColumn(1).asString());
    System.out.println("金额: " + record.getColumn(2).asDouble());
    System.out.println("下单时间: " + record.getColumn(3).asDate());

    // 4. 统计内存
    System.out.println("总字节数: " + record.getByteSize());
    System.out.println("内存占用: " + record.getMemorySize());
    System.out.println("列数: " + record.getColumnNumber());
}
```

输出：

```
订单ID: 10001
用户名: 张三
金额: 299.99
下单时间: Tue May 06 15:30:00 CST 2026
总字节数: 46
内存占用: 302
列数: 4
```

### 3.4 步骤四：探索 ColumnCast 转换矩阵

**目标**：搞清楚 6×6=36 种跨类型转换中哪些允许、哪些报错。

| from \ to | String | Long | Double | Date | Bool | Bytes |
|-----------|--------|------|--------|------|------|-------|
| String   | ✓(本身) | ✓(可解析) | ✓(可解析) | ✗ | ✗ | ✗ |
| Long     | ✓ | ✓(本身) | ✓ | ✗ | ✗ | ✗ |
| Double   | ✓ | ✓(取整) | ✓(本身) | ✗ | ✗ | ✗ |
| Date     | ✓(格式化) | ✓(时间戳) | ✓(时间戳) | ✓(本身) | ✗ | ✗ |
| Bool     | ✓("true/false") | ✓(1/0) | ✗ | ✗ | ✓(本身) | ✗ |
| Bytes    | ✗(抛异常) | ✗ | ✗ | ✗ | ✗ | ✓(本身) |

BytesColumn 是最"孤岛"的类型——既不能转为其他类型，其他类型也不能转为它。如果需要二进制字段跨数据源同步，两个插件都必须原生支持 BytesColumn。

### 3.5 可能遇到的坑及解决方法

**坑1：MySQL `tinyint(1)` 自动映射为 BoolColumn**

MySQL JDBC 驱动有一个"智能"行为：`tinyint(1)` 会被映射为 `java.lang.Boolean`，DataX 据此创建 `BoolColumn`。如果目标表是 `tinyint(4)`，Writer 尝试把 BoolColumn 写回时会出问题。

解决：在 JDBC URL 中加入 `tinyInt1isBit=false` 参数：
```
jdbc:mysql://localhost:3306/test?tinyInt1isBit=false
```

**坑2：Decimal 精度丢失**

`DoubleColumn` 使用 Java 的 `double` 类型存储，有效位数约 15-16 位十进制数。如果金额字段超过这个精度，会出现尾数偏差。

解决：对金融级精度要求的字段，自定义 Transformer 将 DoubleColumn 转为 StringColumn 后再写入，目标端由数据库自行解析。

**坑3：DateColumn 时区偏移**

MySQL `datetime` 类型 JDBC 驱动读出来是 `java.sql.Timestamp`，DataX 内部用 `DateColumn(java.util.Date)` 存储，不携带时区信息。如果在跨时区环境同步，可能出现 8 小时偏差。

解决：在 MySQL JDBC URL 中指定时区：
```
jdbc:mysql://localhost:3306/test?serverTimezone=Asia/Shanghai
```

## 4. 项目总结

### 4.1 六种 Column 适用范围速查

| Column类型 | 适用外部类型 | 存储 | 内存大小 |
|-----------|------------|------|---------|
| StringColumn | varchar, text, char, json, enum | String | 字符数×2+40字节头 |
| LongColumn | tinyint, smallint, int, bigint | Long | 8 + 对象头 |
| DoubleColumn | float, double, decimal | Double | 8 + 对象头 |
| DateColumn | datetime, timestamp, date | java.util.Date | 16 |
| BoolColumn | bit, boolean, tinyint(1) | Boolean | 1 + 对象头 |
| BytesColumn | binary, varbinary, blob | byte[] | 数组长度+头 |

### 4.2 优点

1. **统一抽象**：6 种类型覆盖 99% 的数据同步场景，新增数据源只需实现映射即可
2. **内存优化**：每种类型精确计算 byteSize，避免内存浪费
3. **类型安全**：跨类型转换有明确白名单（asXxx 方法抛异常），防止静默数据错误
4. **BAD 占位**：脏数据不会污染正常任务，被单独收集到脏数据日志
5. **可扩展**：理论上可以继承 Column 创建新的子类型（如 JsonColumn）

### 4.3 缺点

1. **BytesColumn 孤立**：不支持与其他类型互转，跨源二进制同步需要特殊处理
2. **无精度标识**：DoubleColumn 没有 scale/precision 元数据，decimal(18,4) 和 decimal(10,2) 在 Column 层面无法区分
3. **无字符集标识**：StringColumn 没有编码元数据，跨字符集（GBK→UTF-8）可能乱码
4. **DateColumn 无时区**：跨时区同步需要外部参数控制
5. **类型数量偏少**：缺少 JSON、Geo、Array 等现代数据格式的原生支持

### 4.4 适用场景

1. 标准 RDBMS ↔ RDBMS 同步（int/varchar/datetime 三大金刚）
2. RDBMS → 数据湖（ORC/Parquet 有原生 Column 类型）
3. 日志型数据导出（绝大部分是 StringColumn）
4. 简单报表 ETL（类型转换路径清晰）
5. 批量数据迁移（不涉及复杂嵌套结构）

### 4.5 不适用场景

1. JSON 嵌套文档的同步（MongoDB → MySQL，需要 flatten 处理）
2. 金融级高精度计算（BigDecimal 级精度，DoubleColumn 不满足）

### 4.6 注意事项

1. MySQL 同步时检查 `tinyInt1isBit` 参数
2. 二进制字段单独处理，不要在 JSON 配置中随意混入 BytesColumn
3. 跨时区同步务必配置 `serverTimezone`
4. 生产环境建议在 Transformer 中显式处理类型转换，而不是依赖隐式 ColumnCast
5. 大文本字段（text/longtext/blob）注意 byteSize 计算，可能造成内存估算偏高

### 4.7 思考题

1. 如果业务需要一个 `MoneyColumn`（基于 `BigDecimal`，精度保留到 0.01），应该如何设计这个类？它应该继承 Column 还是另起炉灶？
2. DataX 在做 MySQL `bigint unsigned` → `LongColumn` 映射时，如果一个值为 `9223372036854775808`（超过 Java Long.MAX_VALUE），会发生什么？如何解决？

（答案见附录）
