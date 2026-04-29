# 第35章 Transform表达式编译与Janino

## 1 项目背景

### 业务场景：CDC数据转换的性能瓶颈

第21章我们实现了UDF和复杂表达式。但在生产环境中，一个包含20个投影列的Transform配置，每秒处理5万条数据时，出现了**明显的性能下降**。

为什么？因为Transform表达式在运行时需要被解析、编译、执行。如果执行效率低，整个Pipeline的吞吐都会受影响。

理解**表达式编译引擎**——Flink CDC如何将SQL表达式编译为Java字节码，以及Janino编译器在其中的角色——是优化Transform性能的关键。

### Transform表达式编译架构

```
SQL表达式: "UPPER(product) || ' - ' || CAST(amount / 100 AS DECIMAL(10,2))"
    │
    ▼
Calcite SQL Parser（解析SQL语法树）
    │
    ▼
TransformExpressionCompiler（Flink CDC的转换编译器）
    │   └── 生成Java源代码字符串
    ▼
JaninoCompiler（轻量级Java源码 → 字节码编译器）
    │   └── 编译为Java字节码 (class文件)
    ▼
CompiledExpression（可执行的对象）
    │   └── ClassLoader加载到Flink JVM
    ▼
PreTransformOperator（执行编译后的表达式）
    │   └── 对每条CDC事件调用compiledExpression.eval(row)
    ▼
输出转换后的CDC事件
```

---

## 2 项目设计 · 三人交锋对话

**小胖**（惊讶）：Flink CDC在运行时编译Java代码？我以为Java代码都是写好编译好的class文件。这运行时编译安全吗？

**大师**：Janino是一个轻量级的Java编译器——它和Javac的主要区别是：Janino不生成.class文件到磁盘，而是直接在内存中编译为Java字节码，通过ClassLoader加载。

在Flink CDC中，表达式编译的流程是：

```java
// 1. 接收SQL表达式
String expression = "UPPER(product) || ' - ' || status";

// 2. TransformExpressionCompiler生成Java源码
String javaSource = 
  "public class GeneratedExpression implements CompiledExpression {\n" +
  "  public Object eval(RowData row) {\n" +
  "    return StringFunction.upper(row.getString(\"product\"))\n" +
  "           + \" - \" + row.getString(\"status\");\n" +
  "  }\n" +
  "}";

// 3. Janino编泽器将源码编译为字节码
Class<?> compiledClass = JaninoCompiler.compile(javaSource);

// 4. 反射创建实例
CompiledExpression compiled = 
  (CompiledExpression) compiledClass.newInstance();

// 5. 对每条CDC事件执行
for (RowData row : cdcEvents) {
    Object result = compiled.eval(row);
}
```

**安全性**：编译的代码只包含CDC配置中的表达式，不会执行任意代码。YAML配置控制编译内容。

**小白**：那为什么要用Janino而不是Javac？Javac更标准吧？

**大师**：Janino相比Javac有三个核心优势：

| 对比维度 | Janino | Javac |
|---------|-------|-------|
| 启动时间 | 毫秒级（无需启动完整编译器） | 秒级（需要启动javac进程） |
| 内存消耗 | 轻量（几个MB） | 重（几十MB） |
| 代码生成 | 内存中直接生成字节码 | 需要写文件再编译 |
| 依赖JDK | 不需要 | 需要JDK（不是JRE） |
| 适用场景 | 运行时频繁编译小段代码 | 开发时编译完整项目 |

Flink CDC的表达式通常很短（几行），每提交一次作业编译一次。Janino的毫秒级编译时间正合适。如果用Javac，每次作业提交前还得确保Flink容器里有JDK。

**技术映射**：Janino和Javac的区别就像"做汉堡"——Janino是微波炉加热半成品（编译短表达式很快），Javac是从头开始和面、烤面包（编译完整项目）。

---

## 3 项目实战

### 分步实现

#### 步骤1：PreTransformOperator核心源码

```java
// 源码路径:
// flink-cdc-runtime/.../transform/PreTransformOperator.java

public class PreTransformOperator extends ProcessOperator<Event, Event> {

    private final Map<TableId, TransformRule> transformRules;
    private transient CompiledTransform compiledTransform;

    @Override
    public void open() throws Exception {
        // 编译Transform规则（仅一次）
        // 将YAML中的projection和filter编译为Java字节码
        TransformExpressionCompiler compiler = new TransformExpressionCompiler();
        compiledTransform = compiler.compile(transformRules);
        
        // 编译后的Transform包含：
        // - projectionExpr: 编译后的投影表达式
        // - filterExpr: 编译后的过滤表达式
    }

    @Override
    public void processElement(Event event, Context context, Collector<Event> out) {
        if (event instanceof DataChangeEvent) {
            DataChangeEvent dataEvent = (DataChangeEvent) event;
            
            // 1. 执行filter表达式 → true保留，false丢弃
            if (!compiledTransform.filter(dataEvent)) {
                return; // 过滤掉
            }
            
            // 2. 执行projection表达式 → 返回投影后的新Event
            Event projectedEvent = compiledTransform.project(dataEvent);
            
            // 3. 输出投影后的事件
            out.collect(projectedEvent);
        } else {
            // SchemaChangeEvent直接透传
            out.collect(event);
        }
    }
}
```

#### 步骤2：TransformExpressionCompiler编译过程

```java
// 源码路径:
// flink-cdc-runtime/.../transform/TransformExpressionCompiler.java

public class TransformExpressionCompiler {

    public CompiledTransform compile(TransformRule rule) throws Exception {
        // 1. 解析SQL表达式（使用Calcite SQL Parser）
        // projection: "id, order_id, UPPER(product) AS product, amount / 100 AS amount_yuan"
        // filter: "status = 'PAID'"
        SqlNode parsedProjection = parser.parse(rule.getProjection());
        SqlNode parsedFilter = parser.parse(rule.getFilter());

        // 2. 生成Java源码字符串
        String javaCode = generateJavaCode(parsedProjection, parsedFilter, rule);

        // 3. 使用Janino编译为字节码
        JaninoCompiler janino = new JaninoCompiler();
        Class<?> compiledClass = janino.compile(
            "CompiledTransform_" + rule.getTableId().replace('.', '_'),
            javaCode
        );

        // 4. 实例化编译后的Transform
        return (CompiledTransform) compiledClass.newInstance();
    }

    private String generateJavaCode(SqlNode projection, SqlNode filter, 
                                     TransformRule rule) {
        StringBuilder code = new StringBuilder();
        code.append("import org.apache.flink.cdc.runtime.operators.transform.*;\n");
        code.append("public class CompiledTransform_").append(rule.getSanitizedTableId());
        code.append(" implements CompiledTransform {\n");

        // eval方法体：对RowData执行projection和filter
        code.append("  public RowData eval(RowData row) {\n");

        // 生成filter代码
        if (rule.hasFilter()) {
            code.append("    if (!(").append(convertToJava(rule.getFilter()))
                .append(")) return null;\n");
        }

        // 生成projection代码
        code.append("    return new RowData(\n");
        for (String column : rule.getProjectionColumns()) {
            code.append("      ").append(convertToJava(column)).append(",\n");
        }
        code.append("    );\n");
        code.append("  }\n");
        code.append("}\n");

        return code.toString();
    }

    /**
     * 将SQL表达式转换为Java表达式
     * 
     * SQL: "UPPER(product)"  →  Java: "org.apache.flink.cdc.common.utils.StringUtils.upper(row.getString("product"))"
     * SQL: "amount / 100"    →  Java: "row.getDecimal("amount").divide(new BigDecimal(100))"
     * SQL: "status = 'PAID'" →  Java: "row.getString("status").equals("PAID")"
     */
    private String convertToJava(String sqlExpression) {
        // 用Calcite的SqlToJavaConverter将SQL AST转为Java表达式
        // ...
    }
}
```

#### 步骤3：JaninoCompiler源码

```java
// 源码路径:
// flink-cdc-runtime/.../parser/JaninoCompiler.java

public class JaninoCompiler {

    private final ClassLoader parentClassLoader;

    public JaninoCompiler() {
        this.parentClassLoader = Thread.currentThread()
            .getContextClassLoader();
    }

    /**
     * 将Java源码编译为字节码
     * 
     * @param className 生成的类名
     * @param javaCode  Java源码字符串
     * @return 编译后的Class对象
     */
    public Class<?> compile(String className, String javaCode) 
            throws CompileException {
        
        // 1. 设置编译器的ClassLoader（保证能用Flink和CDC的类）
        SimpleCompiler compiler = new SimpleCompiler();
        compiler.setParentClassLoader(parentClassLoader);
        
        // 2. 设置源码
        compiler.cook(javaCode);
        
        // 3. 加载编译后的类
        // Janino在内存中完成编译，不写.class文件到磁盘
        return compiler.getClassLoader().loadClass(className);
    }

    /**
     * 编译并缓存（避免重复编译相同的表达式）
     */
    private final Map<String, Class<?>> compiledCache = new HashMap<>();
    
    public Class<?> compileCached(String className, String javaCode) 
            throws CompileException {
        if (!compiledCache.containsKey(className)) {
            compiledCache.put(className, compile(className, javaCode));
        }
        return compiledCache.get(className);
    }
}
```

#### 步骤4：观察编译后的Java代码

在IDE中设置断点在`GenerateJavaCode`方法后，观察生成的Java代码内容：

**输入：** 
```yaml
projection: "id, order_id, UPPER(product) AS product_upper, amount / 100 AS amount"
filter: "status = 'PAID'"
```

**编译生成的Java代码：**
```java
public class CompiledTransform_shop_orders implements CompiledTransform {

    public RowData eval(RowData row) {
        // Filter: status = 'PAID'
        if (!(row.getString("status").equals("PAID"))) {
            return null;
        }
        
        // Projection: id, order_id, UPPER(product), amount / 100
        return new RowData(
            row.getInt("id"),
            row.getString("order_id"),
            StringUtils.upper(row.getString("product")),
            row.getDecimal("amount").divide(BigDecimal.valueOf(100))
        );
    }
}
```

#### 步骤5：性能对比——编译执行 vs 解释执行

```java
/**
 * 编译执行 vs 解释执行性能对比
 * 
 * 场景: 100万条CDC数据，执行"amount / 100 AS amount_yuan"
 * 
 * 解释执行（每行解析）:
 *   每次eval时都解析表达式字符串 → 100万次解析 → 耗时: 5000ms
 *   
 * 编译执行（Janino）:
 *   第一次编译为字节码（10ms）
 *   后续每次eval直接调用编译后的Java方法 → 耗时: 200ms
 *   
 * 性能提升: 25倍
 */
public class TransformPerformanceCompare {
    
    // 解释执行
    public static Object interpret(RowData row) {
        // 每行都要解析 "amount / 100" → 建立AST → 求值
        // 开销大
    }
    
    // 编译执行
    public static Object compiled(RowData row) {
        // 直接执行编译好的字节码
        // 开销≈原生Java代码
    }
}
```

#### 常见陷坑及解决方法

| 问题 | 原因 | 解决方法 |
|------|------|---------|
| Janino编译失败（Syntax Error） | 表达式语法错误 | 使用Flink SQL CLI先验证表达式语法 |
| Janino编译后类加载失败 | 生成的Java类依赖Flink内部API，ClassLoader不匹配 | 设置正确的`parentClassLoader` |
| 表达式中的NULL处理不当 | SQL中NULL的传播规则，NULL参与任何运算结果都是NULL | 使用`COALESCE(column, default)`处理NULL |
| Janino不支持某些Java语法 | Janino比Javac支持的语法子集小 | 避免使用Lambda、泛型方法引用等Java高级语法 |

---

## 4 项目总结

### Flink CDC表达式编译的完整链路

```
YAML配置: projection/filter
    │
Calcite SQL Parser
    │  把SQL表达式解析为AST（抽象语法树）
    │  如: UPPER(product) → SqlCall(SqlFunction.UPPER, SqlIdentifier(product))
    ▼
TransformExpressionCompiler
    │  把AST转换为Java源码字符串
    │  如: StringUtils.upper(row.getString("product"))
    ▼
JaninoCompiler
    │  编译Java源码为字节码
    │  内部使用codehaus.janino.SimpleCompiler
    ▼
CompiledTransform 实例
    │  通过ClassLoader加载
    │  对每条CDC数据调用eval(RowData)
    ▼
PreTransformOperator / PostTransformOperator
```

### Transform性能优化建议

1. **预编译规则**：Transform规则在Operator的`open()`方法中编译一次，编译后重复使用，不要每行数据都编译
2. **表达式简化**：越简单的表达式生成越短的Java代码，编译越快
3. **避免重复计算**：同一个表达式在projection和filter中出现两次时，编译器应该复用计算结果
4. **缓存编译结果**：对同一张表的多个Split共用一个Transform实例

### 常见踩坑经验

**故障案例1：Projection中的复杂Case When导致编译失败**
- **现象**：包含多层嵌套的`CASE WHEN ... WHEN ... THEN ... ELSE ... END`导致Janino编译时报错
- **根因**：Janino对复杂的条件表达式有内存限制，嵌套太深时编译内存不足
- **解决方案**：简化表达式，或将复杂逻辑拆分为多个UDF

**故障案例2：编译后的Transform缓存不足导致OOM**
- **现象**：监控到TaskManager的非堆内存（Metaspace）持续增长
- **根因**：每张表的每个Schema版本都生成了一个新的Transform类，旧的类没有被卸载
- **解决方案**：限制Transform规则的数量和Schema版本的保留数

**故障案例3：Janino不支持String类中的formatted()方法**
- **现象**：Transform中使用`String.formatted()`编译时报错
- **根因**：Janino对Java 15+的新API支持有限
- **解决方案**：使用`String.format()`替代，或避免使用Java高版本API

### 思考题

1. **进阶题①**：Janino编译器的`SimpleCompiler.cook()`方法内部是如何将Java源码编译为字节码的？它和Javac的`javac.tools.JavaCompiler`在实现上有什么本质区别？Hint：Janino不生成AST，直接生成字节码。

2. **进阶题②**：在`TransformExpressionCompiler`中，如果一个projection表达式引用了表中不存在的列（如YAML写的`xxx_column`但表中没有），这个错误是在编译期发现还是在运行期发现？推测编译器和执行器的错误处理逻辑。

---

> **下一章预告**：第36章「自定义Connector开发（上）：DataSource」——高级篇的自定义扩展能力。你将学习如何开发一个自定义的DataSource连接器（如自定义File Source或自定义API CDC Source），包括SPI注册、MetadataAccessor实现、FlinkSourceProvider集成。
