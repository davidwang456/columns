# 第21章 高级数据转换：UDF与表达式编译

## 1 项目背景

### 业务场景：CDC数据需要复杂的业务转换

第10章的基础Projection和Filter满足不了所有需求。以下是几个真实场景：

1. **数据脱敏**：手机号`13800138001`需要变成`138****8001`
2. **格式转换**：MySQL的`TINYINT(1)`的`0/1`需要变成`是/否`
3. **JSON解析**：MySQL的JSON列`ext_info: {"coupon":"DISCOUNT50", "source":"APP"}`需要提取内部字段
4. **外部查询**：通过用户ID查询Redis获取用户等级，拼接到CDC事件中
5. **复杂计算**：`CASE WHEN amount > 10000 THEN '高价值' WHEN amount > 1000 THEN '中价值' ELSE '低价值' END`

基础Projection不支持这些操作，需要**UDF（用户自定义函数）**或**表达式编译器**。

### UDF在Flink CDC中的位置

```
Pipeline YAML Transform:
  projection: "id, order_id, phone_mask(phone) AS phone"  ← UDF调用
  filter: "order_status_name(status) = '已支付'"          ← UDF调用

编译过程:
  TransformExpressionCompiler (Calcite SQL解析)
    ↓
  JaninoCompiler (Java源码生成 + 编译)
    ↓
  运行时加载 (ClassLoader)
    ↓
  PreTransformOperator (执行编译后的代码)
```

---

## 2 项目设计 · 三人交锋对话

**小胖**（好奇）：UDF不就是写个Java函数，然后在YAML里调用吗？那我写个`public String hello(String name) { return "Hello " + name; }`就行？

**大师**：基本思路正确，但Flink CDC的UDF有几个约束：

1. **函数必须是确定性的（Deterministic）**：同样的输入必须得到同样的输出。因为Checkpoint恢复时会重放事件，非确定性函数会导致结果不一致。
2. **不能有状态**：UDF实例可能被多个事件复用，类成员变量需要在`open()`中初始化。
3. **不能有副作用**：不能在UDF中写数据库、发HTTP请求。

```java
// ✅ 好的UDF：无状态，纯函数
public class HelloUDF {
    public String eval(String name) {
        return "Hello " + name;
    }
}

// ❌ 坏的UDF：有副作用（每次调用都扣减余额）
public class EvilUDF {
    public String eval(String name) {
        deductFromDatabase(name); // 每次调用都改数据库
        return "Hello " + name;
    }
}
```

**小白**：那UDF的`eval`方法名字可以自定义吗？我看到有的UDF叫`eval`，有的叫`evaluate`。

**大师**：Flink CDC的UDF框架要求方法名必须是**`eval`**。一个UDF类可以有多个重载的`eval`方法：

```java
public class FormatUDF {
    // 重载1：处理字符串
    public String eval(String input) { ... }
    // 重载2：处理数字
    public String eval(Long input) { ... }
    // 重载3：处理null
    public String eval() { return "N/A"; }
}
```

Flink CDC的表达式编译器（基于Janino）会根据调用时的参数类型自动选择匹配的重载版本。

**小胖**：那表达式编译（Expression Compilation）又是什么？和UDF有啥区别？

**大师**：表达式编译是"内置的UDF"。你在YAML的`projection`或者`filter`中写的表达式——如`amount / 100`、`UPPER(product)`、`CASE WHEN status = 'PAID' THEN '已支付' END`——它们不需要你写Java代码，而是由Flink CDC的`TransformExpressionCompiler`在运行时**编译成Java字节码**。

编译流程：
```
SQL表达式: "UPPER(product) || ' - ' || status"
    ↓ Calcite SQL Parser
AST (抽象语法树)
    ↓ TransformExpressionCompiler
Java源码:
  public class CompiledExpression {
    public Object eval(RowData row) {
      return UPPER(row.getString("product")) 
        + " - " + row.getString("status");
    }
  }
    ↓ JaninoCompiler
Java字节码 (.class)
    ↓ ClassLoader加载
可执行的对象
```

**技术映射**：UDF像"外卖"——别人做好的菜，你直接点（引入外部函数）。表达式编译像"你自己做菜"——用厨房里的食材（DataTypes/Operators）现场做（编译执行）。

---

## 3 项目实战

### 环境准备

Flink CDC的UDF不需要额外依赖，只需实现`UserDefinedFunction`接口（可选）。

### 分步实现

#### 步骤1：编写第一个UDF

```java
package com.example.udf;

/**
 * 手机号脱敏UDF：13800138001 → 138****8001
 * Flink CDC的UDF命名规范：
 *   - 类名任意（但要有意义）
 *   - 方法名必须是 eval
 *   - 可以有多个重载
 */
public class PhoneMask {

    public String eval(String phone) {
        if (phone == null || phone.length() < 7) {
            return phone;
        }
        return phone.substring(0, 3)
            + "****"
            + phone.substring(phone.length() - 4);
    }

    // 重载：处理Long类型的手机号
    public String eval(Long phone) {
        return eval(String.valueOf(phone));
    }
}
```

#### 步骤2：编写带生命周期和配置的UDF

```java
package com.example.udf;

import org.apache.flink.cdc.common.udf.UserDefinedFunction;
import org.apache.flink.configuration.Configuration;

/**
 * 金额格式化UDF：分转元 + 货币符号
 * 演示UDF生命周期方法
 */
public class AmountFormat extends UserDefinedFunction {

    private String currencySymbol;

    @Override
    public void open(Configuration parameters) {
        // 从配置中读取货币符号（全局配置）
        this.currencySymbol = parameters.getString(
            "currency.symbol", "¥");
    }

    // 分为单位的金额转为指定格式
    public String eval(Long amountCents) {
        if (amountCents == null) return "N/A";
        double yuan = amountCents / 100.0;
        return currencySymbol + String.format("%.2f", yuan);
    }

    // DECIMAL类型的分转元
    public String eval(java.math.BigDecimal amountCents) {
        if (amountCents == null) return "N/A";
        java.math.BigDecimal yuan = amountCents
            .divide(java.math.BigDecimal.valueOf(100));
        return currencySymbol + yuan.setScale(2);
    }

    @Override
    public void close() {
        // 资源清理（可选）
    }
}
```

#### 步骤3：配置UDF到Pipeline YAML

```yaml
# UDF定义
udfs:
  - function-name: phone_mask
    class-name: com.example.udf.PhoneMask
    jar-file: /opt/flink/lib/cdc-udf-demo.jar   # UDF的JAR包

  - function-name: amount_format
    class-name: com.example.udf.AmountFormat
    # 传递UDF自定义参数（在open中读取）
    config:
      currency.symbol: "¥"

  - function-name: status_name
    class-name: com.example.udf.OrderStatusName

# Transform中使用UDF
transform:
  - source-table: shop.orders_full
    projection: |
      id,
      order_id,
      phone_mask(phone) AS phone,                 # UDF: 脱敏
      amount_format(amount_cents) AS amount,      # UDF: 格式化金额
      status_name(status) AS status_name,          # UDF: 状态名称
      user_id
    filter: "status_name(status) IN ('已支付', '已发货')"
```

#### 步骤4：复杂表达式——不使用UDF，直接使用SQL表达式

Flink CDC的内置表达式可以处理很多场景，不需要写UDF：

```yaml
transform:
  - source-table: shop.orders_full
    projection: |
      id,
      order_id,
      -- 条件表达式
      CASE 
        WHEN amount >= 1000000 THEN '高价值'
        WHEN amount >= 100000 THEN '中价值'
        ELSE '普通'
      END AS order_level,
      -- 字符串函数
      UPPER(product) AS product_upper,
      -- 字符串连接
      CONCAT(order_id, '-', status) AS order_status_key,
      -- 时间提取
      EXTRACT(YEAR FROM create_time) AS year,
      -- NULL处理
      COALESCE(internal_remark, '无备注') AS remark
    filter: "status = 'PAID' AND amount > 0"
```

#### 步骤5：UDF的单元测试

```java
package com.example.udf;

import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

/**
 * UDF单元测试——确保函数行为正确
 */
class PhoneMaskTest {

    private final PhoneMask mask = new PhoneMask();

    @Test
    void testNormalPhone() {
        assertEquals("138****8001", mask.eval("13800138001"));
    }

    @Test
    void testShortPhone() {
        // 长度<7的手机号：原样返回
        assertEquals("12345", mask.eval("12345"));
    }

    @Test
    void testNullPhone() {
        assertNull(mask.eval((String) null));
    }

    @Test
    void testLongType() {
        assertEquals("138****8001", mask.eval(13800138001L));
    }

    @Test
    void testInternationalPhone() {
        // 国际手机号（带国家码）
        assertEquals("+86****8001", mask.eval("+8613800138001"));
    }
}
```

**Maven测试依赖：**
```xml
<dependency>
    <groupId>org.junit.jupiter</groupId>
    <artifactId>junit-jupiter</artifactId>
    <version>5.10.0</version>
    <scope>test</scope>
</dependency>
```

#### 步骤6：引擎级UDF——实现复杂的业务逻辑

```java
package com.example.udf;

import org.apache.flink.cdc.common.udf.UserDefinedFunction;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.JsonNode;

/**
 * JSON字段提取UDF：从JSON字符串中提取指定path的值
 * 
 * 使用示例:
 *   json_extract(ext_info, 'coupon') AS coupon
 */
public class JsonExtract extends UserDefinedFunction {

    private transient ObjectMapper mapper;

    @Override
    public void open(Configuration parameters) {
        mapper = new ObjectMapper();
    }

    public String eval(String jsonStr, String path) {
        if (jsonStr == null || path == null) return null;
        try {
            JsonNode root = mapper.readTree(jsonStr);
            JsonNode node = root.get(path);
            return node != null ? node.asText() : null;
        } catch (Exception e) {
            return null;  // JSON解析失败时返回null
        }
    }
}
```

**YAML中使用：**
```yaml
transform:
  - source-table: shop.orders_full
    projection: |
      id,
      order_id,
      json_extract(ext_info, 'coupon') AS coupon_code,
      json_extract(ext_info, 'source') AS order_source
```

#### 常见陷坑及解决方法

| 问题 | 原因 | 解决方法 |
|------|------|---------|
| `Function 'xxx' not registered` | UDF类未正确配置或JAR未加载 | 检查`udfs`配置的`class-name`和`jar-file`路径 |
| `Method 'eval' with parameter types (...) not found` | 参数类型不匹配，没有合适的重载方法 | 添加对应参数类型的`eval`重载 |
| UDF中使用了Flink未提供的类 | Janino编译器的ClassLoader范围受限 | 只使用`java.lang`和Flink提供的类，避免第三方依赖 |
| UDF的`open`方法未被调用 | 没有继承`UserDefinedFunction`基类 | 继承`UserDefinedFunction`并重写`open` |
| UDF在并行子任务中的静态变量冲突 | 使用`static`变量导致多线程问题 | 使用实例变量（非static），每个Task实例独立创建UDF |

---

## 4 项目总结

### UDF vs 内置表达式

| 对比维度 | 内置表达式 | UDF |
|---------|-----------|-----|
| 定义方式 | SQL语法直接写 | Java代码编写 |
| 灵活性 | 受限于SQL支持的函数 | 任意Java逻辑 |
| 性能 | 高（编译为字节码） | 高（编译为字节码） |
| 外部依赖 | 不需要 | 可能需要（如Jackson JSON解析） |
| 测试难度 | 低（运行即可验证） | 中（需要单元测试） |
| 适用场景 | 简单转换、过滤 | 复杂逻辑、外部查询、脱敏 |

### UDF开发最佳实践

1. **纯函数原则**：相同的输入永远产生相同的输出
2. **无状态设计**：不用static成员变量存状态（除非是只读配置）
3. **NULL安全**：所有UDF的第一个检查都应该是参数是否为null
4. **重载完整**：为所有可能的输入类型提供`eval`重载（String、Long、BigDecimal等）
5. **性能意识**：UDF中避免创建大对象、避免正则表达式预编译

### 常见踩坑经验

**故障案例1：UDF导致反压——JSON解析成为瓶颈**
- **现象**：UDF中有`ObjectMapper.readTree()`调用，每秒处理5万条数据时，Source出现反压
- **根因**：每次调用都创建新的`ObjectMapper`实例（创建成本高）和解析JSON树（CPU密集）
- **解决方案**：将`ObjectMapper`设为实例变量（在`open`中初始化一次），或改用流式JSON解析（`JsonParser`）

**故障案例2：UDF中使用SimpleDateFormat导致数据错乱**
- **现象**：偶尔出现日期解析错误，且错误在不同时间点不同
- **根因**：`SimpleDateFormat`是非线程安全的，在Flink多线程环境下被多个事件同时使用导致错乱
- **解决方案**：使用`DateTimeFormatter`（线程安全），或在`eval`方法内部创建新的`SimpleDateFormat`实例

**故障案例3：UDF编译通过但运行时抛出ClassNotFoundException**
- **现象**：UDF中使用了第三方库（如Apache Commons），但在Flink运行时找不到
- **根因**：UDF的JAR包虽然被提交了，但依赖的第三方库未包含在JAR中
- **解决方案**：使用`maven-shade-plugin`将依赖打包到UDF JAR中（fat jar），或确保第三方JAR也在Flink的lib目录中

### 思考题

1. **进阶题①**：Flink CDC的`TransformExpressionCompiler`使用Calcite解析SQL表达式，然后通过Janino编译为Java字节码。Janino是一个轻量级的Java编译器，它和Javac的主要区别是什么？为什么Flink CDC选择Janino而不是Javac？

2. **进阶题②**：在UDF中，如果需要做"将金额从RMB转换为USD"的实时汇率换算，你认为应该怎么做？提示：考虑外部API查询的延迟和可靠性问题。Flink CDC UDF中是否可以使用`AsyncIO`（异步IO）来查询外部服务？

---

> **下一章预告**：第22章「多表Broadcast与宽表合并」——如果多张源表（如order + order_item + user）合并为一张宽表，Flink CDC如何实现？本章将探讨多流Join、维表关联和宽表构建策略。
