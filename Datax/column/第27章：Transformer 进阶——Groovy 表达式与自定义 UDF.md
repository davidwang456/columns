# 第27章：Transformer 进阶——Groovy 表达式与自定义 UDF

## 1. 项目背景

某游戏公司的用户数据管道需要每天凌晨将 MySQL 的 `player` 表同步到数据仓库。但由于业务快速发展，数据处理需求每周都在变——上月要求手机号脱敏 + 注册日期格式化，本周又新增"根据设备类型字符串自动推断设备类别（iOS/Android/Other）""邮箱域名统一转小写""姓名字段去除首尾空格"。

安全工程师发现这些需求无法用内置 Transformer 完成——`dx_replace` 不会大小写转换，`dx_substr` 不会条件判断。之前在同步管道外加了一层 Python 脚本处理，但脚本和 DataX 配置分开维护，字段序号变更时经常出现"DX 配了第 3 列但 Python 脚本处理的是旧的第 4 列"的问题。

运营团队需要一个终极方案：在 DataX 的 Transformer 环节就能写任意逻辑，且所有逻辑集中在一处配置。这恰好是 `dx_groovy` 的定位——它允许在 JSON 配置中嵌入 Groovy 脚本，在数据流过 Channel 之前或之后执行任意 Java 代码。一条 Groovy 脚本可以同时完成截取、替换、条件分支、类型转换等多重操作。

本章深入 `dx_groovy` 的实现原理，剖析 `GroovyTransformer.java` 的执行上下文与变量注入机制，通过 5 个逐步进阶的实战案例掌握单条脚本完成多字段联合变换的能力，同时对比 Groovy 与内置 Transformer 的性能差异并给出选型建议。

## 2. 项目设计——剧本式交锋对话

**（数据管道评审会，产品经理拿着最新的 PRD 冲进来）**

**产品经理**：需求又加了——用户头像 URL 如果是 `http://` 开头，改成 `https://`；设备类型字符串 `iPhone14,3` 要提取出 `iOS`；注册平台的 `wechat_mini` / `apple_store` / `huawei_store` 要映射为 `WECHAT/IOS/ANDROID`！

**小胖**：（当场崩溃）又来！上周刚加了 4 个清洗规则，这周又来 3 个！Python 脚本已经 200 行了，字段序号都对不齐了！

**小白**：（冷静翻文档）其实可以用 dx_groovy。它是 DataX 的万能利器——在 JSON 里内嵌一段 Groovy 脚本，JVM 运行时动态编译执行，可以写任意 Java 代码。

**大师**：（递给小胖一瓶可乐，打开投影仪）dx_groovy 的本质是"把一段 Java 代码塞进 JSON"。DataX 启动时，`GroovyTransformer` 从配置中取出 `code` 字段，传给 `GroovyShell.evaluate()`。GroovyShell 在运行时将脚本编译成 JVM 字节码，然后执行。

但这里有个关键——**Groovy 脚本可以访问两个预置变量**：

| 变量名 | 类型 | 说明 |
|-------|------|------|
| `record` | `Record` | 当前行数据对象，`.getColumn(i)` 获取列，`.setColumn(i, col)` 修改列 |
| `Column` | `Class` | `com.alibaba.datax.common.element.Column` 类引用，用于 `new StringColumn(...)` |

有了这两个变量，你可以在脚本里做任何事：读取第 i 列的值 → 用 Java 逻辑处理 → 写回第 j 列。

**技术映射**：内置 Transformer = 食堂套餐（A套餐=dx_substr，B套餐=dx_replace）。dx_groovy = 自助厨房——给你灶台（GroovyShell）、食材（record/Column）、调料（JDK API），你想炒什么炒什么。

**小胖**：那既然 dx_groovy 这么强，为啥还需要内置 Transformer？

**大师**：（在白板上画了一条性能曲线）

```
场景: 100 万行数据，每行做字符串截取
dx_substr: 0.5 秒 → 原生 Java 代码，直接执行
dx_groovy: 5 秒 → GroovyShell 动态编译 + 反射调用

差距: 10 倍！
```

Groovy 的每次调用都需要通过 `GroovyShell` 的脚本引擎层——它比原生 Java 多了方法查找、类型适配、闭包解析等开销。在 100 万行数据下，10 倍差距意味着多等 4.5 秒；在 1 亿行下就是 7.5 分钟。

所以原则是：**能用内置 Transformer 解决的问题，不用 dx_groovy；但当内置无力（条件分支、多字段联合、类型推断），果断用 dx_groovy。**

**小白**：还有个重要的细节——TransformerRegistry 的注册机制是什么？怎么确定我的 Groovy 脚本能正常执行？

**大师**：代码位置：`core/src/main/java/com/alibaba/datax/core/transport/transformer/TransformerRegistry.java`

```java
public class TransformerRegistry {
    private static Map<String, Transformer> registry = new HashMap<>();
    
    static {
        registry.put("dx_substr", new SubstrTransformer());
        registry.put("dx_pad", new PadTransformer());
        registry.put("dx_replace", new ReplaceTransformer());
        registry.put("dx_filter", new FilterTransformer());
        registry.put("dx_groovy", new GroovyTransformer());
        // ...
    }
    
    public static Transformer get(String name) {
        return registry.get(name);
    }
}
```

这是一个静态注册表——所有 Transformer 在类加载阶段就完成了注册。dx_groovy 在这里只是一个键值对 `("dx_groovy", new GroovyTransformer())`。

**小胖**：（眼睛一亮）那我懂了——我可以仿照这个写自己的 Transformer？比如 `dx_my_logic`？

**大师**：对！这就是下一章会深入的自定义 Transformer 开发。但今天我们先掌握 dx_groovy 的四项核心能力：单字段变换、多字段组合、条件分支、类型替换。

## 3. 项目实战

### 3.1 步骤一：最简单的 dx_groovy——手机号脱敏

**目标**：用一条 Groovy 脚本将手机号中间 4 位替换为 `****`。

```json
{
    "transformer": [{
        "name": "dx_groovy",
        "parameter": {
            "code": "import com.alibaba.datax.common.element.*;\n"
                    + "String phone = record.getColumn(1).asString();\n"
                    + "if (phone != null && phone.length() >= 11) {\n"
                    + "    String masked = phone.replaceAll('(\\\\d{3})\\\\d{4}(\\\\d{4})', '$1****$2');\n"
                    + "    record.setColumn(1, new StringColumn(masked));\n"
                    + "}\n"
                    + "return record;"
        }
    }]
}
```

**脚本逐行解析**：

```groovy
// 第1行：导入 DataX 的 Column 类族（StringColumn, LongColumn 等）
import com.alibaba.datax.common.element.*;

// 第2行：读取第 1 列（索引从 0 开始，phone 是第 2 列所以 index=1）
String phone = record.getColumn(1).asString();

// 第3行：安全判空（永远不要假设字段非空！）
if (phone != null && phone.length() >= 11) {
    // 第4行：正则替换中间4位为****
    String masked = phone.replaceAll('(\\d{3})\\d{4}(\\d{4})', '$1****$2');
    // 第5行：将修改后的值写回第 1 列
    record.setColumn(1, new StringColumn(masked));
}

// 最后一行：必须 return record，否则该行被丢弃！
return record;
```

**关键提示**：
- Groovy 中正则的 `\d` 需要写成 `\\d`（JSON 转义一层 + Groovy 转义一层）
- `return record;` **不可省略**——若不返回 record 返回 null，该行会被当作 `dx_filter` 过滤掉
- `new StringColumn(...)` 中用了 `.element.StringColumn`，需要用 `import` 导入

### 3.2 步骤二：多字段联合变换——邮箱小写 + 日期格式化 + 去空格

**目标**：在一条脚本中完成三个字段的变换：email 转小写、register_date 从 `yyyyMMdd` 转 `yyyy-MM-dd`、user_name 去首尾空格。

```json
{
    "transformer": [{
        "name": "dx_groovy",
        "parameter": {
            "code": "import com.alibaba.datax.common.element.*;\n"
                    + "import java.text.SimpleDateFormat;\n"
                    + "// 1. 邮箱转小写（第 2 列，index=2）\n"
                    + "String email = record.getColumn(2).asString();\n"
                    + "if (email != null) {\n"
                    + "    record.setColumn(2, new StringColumn(email.toLowerCase()));\n"
                    + "}\n"
                    + "// 2. 日期格式转换（第 3 列，index=3）\n"
                    + "String rawDate = record.getColumn(3).asString();\n"
                    + "if (rawDate != null && rawDate.length() == 8) {\n"
                    + "    SimpleDateFormat inFmt = new SimpleDateFormat('yyyyMMdd');\n"
                    + "    SimpleDateFormat outFmt = new SimpleDateFormat('yyyy-MM-dd');\n"
                    + "    try {\n"
                    + "        java.util.Date d = inFmt.parse(rawDate);\n"
                    + "        record.setColumn(3, new StringColumn(outFmt.format(d)));\n"
                    + "    } catch (Exception e) {\n"
                    + "        // 格式异常，保留原值\n"
                    + "    }\n"
                    + "}\n"
                    + "// 3. 去首尾空格（第 0 列，index=0）\n"
                    + "String userName = record.getColumn(0).asString();\n"
                    + "if (userName != null) {\n"
                    + "    record.setColumn(0, new StringColumn(userName.trim()));\n"
                    + "}\n"
                    + "return record;"
        }
    }]
}
```

**效果对比**：

| 原始字段 | 变换前 | 变换后 |
|---------|--------|--------|
| user_name | " 张三 " | "张三" |
| email | "Zhang@GAME.com" | "zhang@game.com" |
| register_date | "20250101" | "2025-01-01" |

### 3.3 步骤三：条件分支——设备类型字符串推断

**目标**：根据系统记录的设备类型原始字符串，推断出标准化设备类别。

**场景**：`device_model` 列存储原始设备型号如 "iPhone14,3"、"SM-G9980"、"HUAWEI-ANA-AN00"，需要新增一个 `device_type` 列（APPEND 到 record 末尾）。

```json
{
    "transformer": [{
        "name": "dx_groovy",
        "parameter": {
            "code": "import com.alibaba.datax.common.element.*;\n"
                    + "String model = record.getColumn(4).asString();\n"
                    + "String deviceType = 'Other';\n"
                    + "if (model != null) {\n"
                    + "    if (model.startsWith('iPhone') || model.startsWith('iPad')) {\n"
                    + "        deviceType = 'iOS';\n"
                    + "    } else if (model.startsWith('SM-') || model.startsWith('GT-')) {\n"
                    + "        deviceType = 'Android';\n"
                    + "    } else if (model.startsWith('HUAWEI') || model.startsWith('HONOR')) {\n"
                    + "        deviceType = 'Android';\n"
                    + "    } else if (model.startsWith('Mac') || model.contains('MacBook')) {\n"
                    + "        deviceType = 'macOS';\n"
                    + "    }\n"
                    + "}\n"
                    + "// 追加新列到 record 末尾\n"
                    + "record.addColumn(new StringColumn(deviceType));\n"
                    + "return record;"
        }
    }]
}
```

**关键细节**：`record.addColumn()` 是往已有列的末尾追加一列。Writer 配置的 `column` 数量应当比 Reader 多一列来容纳这个新增列。

**Writer 配置适配**（新增的 `device_type` 列）：

```json
{
    "writer": {
        "parameter": {
            "column": ["user_name", "phone", "email", "register_date", "device_model", "device_type"]
        }
    }
}
```

### 3.4 步骤四：类型替换——时间戳 LongColumn 转格式化 StringColumn

**目标**：`create_time` 列在 MySQL 中为 `DATETIME`，Reader 映射为 `DateColumn`。需要在 Groovy 中将 `DateColumn` 转为指定格式的 `StringColumn`。

```json
{
    "transformer": [{
        "name": "dx_groovy",
        "parameter": {
            "code": "import com.alibaba.datax.common.element.*;\n"
                    + "import java.text.SimpleDateFormat;\n"
                    + "Column col = record.getColumn(5);\n"
                    + "if (col != null && col.getRawData() != null) {\n"
                    + "    java.util.Date dt = col.asDate();\n"
                    + "    if (dt != null) {\n"
                    + "        SimpleDateFormat sdf = new SimpleDateFormat('yyyy-MM-dd HH:mm:ss');\n"
                    + "        record.setColumn(5, new StringColumn(sdf.format(dt)));\n"
                    + "    }\n"
                    + "}\n"
                    + "return record;"
        }
    }]
}
```

**Column 类型判断的通用模板**：

```groovy
Column col = record.getColumn(i);
if (col instanceof LongColumn) {
    long val = col.asLong();
    // 处理 LongColumn
} else if (col instanceof DoubleColumn) {
    double val = col.asDouble();
    // 处理 DoubleColumn
} else if (col instanceof StringColumn) {
    String val = col.asString();
    // 处理 StringColumn
} else if (col instanceof DateColumn) {
    java.util.Date val = col.asDate();
    // 处理 DateColumn
} else if (col instanceof BoolColumn) {
    boolean val = col.asBoolean();
    // 处理 BoolColumn
}
```

### 3.5 步骤五：完整实战——玩家表同步的全链路 Groovy 清洗

**目标**：将所有需求集中到一条 Groovy 脚本，完成 `player` 表同步的全部清洗逻辑。

**完整配置**（`groovy_full_job.json`）：

```json
{
    "job": {
        "content": [{
            "reader": {
                "name": "mysqlreader",
                "parameter": {
                    "username": "root",
                    "password": "root",
                    "column": [
                        "player_name", "phone", "email", "register_date",
                        "device_model", "create_time", "platform"
                    ],
                    "connection": [{
                        "table": ["player"],
                        "jdbcUrl": ["jdbc:mysql://localhost:3306/game_db?useSSL=false"]
                    }]
                }
            },
            "writer": {
                "name": "mysqlwriter",
                "parameter": {
                    "writeMode": "insert",
                    "column": [
                        "player_name", "phone", "email", "register_date",
                        "device_model", "create_time", "device_type", "platform_code"
                    ],
                    "connection": [{
                        "table": ["player_dw"],
                        "jdbcUrl": ["jdbc:mysql://localhost:3306/dw_db?useSSL=false"]
                    }]
                }
            },
            "transformer": [{
                "name": "dx_groovy",
                "parameter": {
                    "code": "import com.alibaba.datax.common.element.*;\n"
                            + "import java.text.SimpleDateFormat;\n"
                            + "// === 1. 姓名去空格 ===\n"
                            + "String name = record.getColumn(0).asString();\n"
                            + "if (name != null) record.setColumn(0, new StringColumn(name.trim()));\n"
                            + "// === 2. 手机号脱敏 ===\n"
                            + "String phone = record.getColumn(1).asString();\n"
                            + "if (phone != null && phone.length() >= 11) {\n"
                            + "    record.setColumn(1, new StringColumn(\n"
                            + "        phone.replaceAll('(\\\\d{3})\\\\d{4}(\\\\d{4})', '$1****$2')));\n"
                            + "}\n"
                            + "// === 3. 邮箱转小写 ===\n"
                            + "String email = record.getColumn(2).asString();\n"
                            + "if (email != null) record.setColumn(2, new StringColumn(email.toLowerCase()));\n"
                            + "// === 4. 注册日期格式化 ===\n"
                            + "String rawDt = record.getColumn(3).asString();\n"
                            + "if (rawDt != null && rawDt.length() == 8) {\n"
                            + "    try {\n"
                            + "        SimpleDateFormat inFmt = new SimpleDateFormat('yyyyMMdd');\n"
                            + "        SimpleDateFormat outFmt = new SimpleDateFormat('yyyy-MM-dd');\n"
                            + "        record.setColumn(3, new StringColumn(outFmt.format(inFmt.parse(rawDt))));\n"
                            + "    } catch (Exception ignored) {}\n"
                            + "}\n"
                            + "// === 5. 设备类型推断 ===\n"
                            + "String model = record.getColumn(4).asString();\n"
                            + "String devType = 'Other';\n"
                            + "if (model != null) {\n"
                            + "    if (model.startsWith('iPhone') || model.startsWith('iPad')) devType = 'iOS';\n"
                            + "    else if (model.startsWith('SM-') || model.startsWith('GT-') || model.startsWith('HUAWEI')) devType = 'Android';\n"
                            + "    else if (model.startsWith('Mac')) devType = 'macOS';\n"
                            + "}\n"
                            + "record.addColumn(new StringColumn(devType));\n"
                            + "// === 6. 平台代码映射 ===\n"
                            + "String plat = record.getColumn(6).asString();\n"
                            + "String platCode = 'UNKNOWN';\n"
                            + "if (plat != null) {\n"
                            + "    switch(plat) {\n"
                            + "        case 'wechat_mini': platCode = 'WECHAT'; break;\n"
                            + "        case 'apple_store': platCode = 'IOS'; break;\n"
                            + "        case 'huawei_store': platCode = 'ANDROID'; break;\n"
                            + "        case 'xiaomi_store': platCode = 'ANDROID'; break;\n"
                            + "        case 'web': platCode = 'WEB'; break;\n"
                            + "    }\n"
                            + "}\n"
                            + "record.addColumn(new StringColumn(platCode));\n"
                            + "return record;"
                }
            }]
        }],
        "setting": {
            "speed": {"channel": 5}
        }
    }
}
```

**运行命令**：

```powershell
python datax.py groovy_full_job.json
```

**日志验证**（关键片段）：

```
GroovyTransformer evaluate - 输入 Record: [ 张三 , 13912345678, Zhang@GAME.com, 20250101, iPhone14,3, 2025-03-15 10:30:00, wechat_mini]
GroovyTransformer evaluate - 输出 Record: [张三, 139****5678, zhang@game.com, 2025-01-01, iPhone14,3, 2025-03-15 10:30:00, iOS, WECHAT]
```

### 3.6 可能遇到的坑及解决方法

**坑1：Groovy 脚本中的 JSON 转义地狱**

JSON 要求 `\` 转义，Groovy 也要求 `\` 转义。写正则 `(\d{3})\d{4}(\d{4})` 时需要：
- 在 JSON 中：每个 `\` 写成 `\\`
- 在 Groovy 字符串中：正则的 `\d` 写成 `\\d`

最终在 JSON 中表现为 `\\\\d`。**建议**：尽量用 Java 方法替代正则（如 `phone.substring(0,3) + "****" + phone.substring(7)`）。

**坑2：`import` 语句的类找不到**

Groovy 脚本中的 `import` 依赖 GroovyShell 的 ClassLoader。如果用了非 JDK 标准库的类，必须确保该类已经在 DataX 的 classpath 中。建议只用 `java.*`、`com.alibaba.datax.common.element.*` 标准库。

**坑3：Groovy 脚本中的 null 传播**

```groovy
record.getColumn(2).asString().toLowerCase()  // 危险！
// 如果 getColumn(2) 返回 null → NPE
// 如果 asString() 返回 null → NPE
```

**正确写法**：
```groovy
String email = record.getColumn(2)?.asString();
if (email != null) { ... }
```

Groovy 的 `?.` 安全导航操作符可以阻止 null 传播。

**坑4：Groovy 内存泄漏**

每次执行 `new GroovyShell().evaluate(script)` 都会创建一个新的类——如果一个 Job 处理 1 亿行，就创建了 1 亿个类，Metaspace 会被撑满。DataX 的 GroovyTransformer 已经做了缓存——同一个脚本只编译一次，但如果你在 `code` 中使用了随机生成的字符串拼接，缓存会失效。

## 4. 项目总结

### 4.1 dx_groovy vs 内置 Transformer 对比

| 维度 | dx_groovy | 内置 Transformer (dx_substr等) |
|------|-----------|-------------------------------|
| 灵活性 | 极高（任意Java代码） | 低（只支持固定操作） |
| 性能 | 慢（每条Record一次GroovyShell调用） | 快（原生Java编译） |
| 多字段联合 | 支持 | 不支持 |
| 条件分支 | 支持（if/else/switch） | 不支持（仅filter） |
| 类型转换 | 支持（new StringColumn/LongColumn） | 不支持 |
| 调试难度 | 高（运行时错误难定位） | 低（语法错误在配置校验捕获） |
| 安全风险 | 高（脚本可能执行任意代码） | 低 |

### 4.2 优点

1. **万能性**：一条 Groovy 脚本 = 多个内置 Transformer 的组合，还支持条件分支和类型转换
2. **集中管理**：所有清洗逻辑在一处 JSON 配置中，不再分散在 Python 脚本和数据管道之间
3. **灵活扩展**：可以调用 JDK 的任何 API（`SimpleDateFormat`、正则、字符串处理），无需等待 DataX 官方扩展
4. **即时生效**：修改 Groovy 脚本后重新运行任务即可，无需重新编译 DataX

### 4.3 缺点

1. **性能开销**：每条 Record 都经过 GroovyShell 的 evaluate 调用，比内置 Transformer 慢约 10 倍
2. **调试困难**：Groovy 脚本中的语法错误只能在运行时暴露，没有编译期检查
3. **JSON 转义复杂**：多层转义让简单的正则表达式变得难以阅读
4. **内存风险**：Groovy 脚本中的每个类实例都会占用 Metaspace，大量不同脚本可能触发 OOM
5. **安全边界**：Groovy 脚本可以执行任意代码（包括 `System.exit()`），需要审计

### 4.4 适用场景

1. 需求频繁变动、内置 Transformer 无法覆盖的复杂清洗逻辑
2. 多字段联合变换（如字段合并、根据 A 列计算 B 列）
3. 条件分支逻辑（如根据状态值决定转换策略）
4. 类型转换（如 DateColumn→StringColumn、LongColumn→StringColumn）
5. 数据量 < 500 万行（超过则优先考虑自定义 Java Transformer）

### 4.5 不适用场景

1. 数据量 > 5000 万行（Groovy 性能开销叠加成不可接受的延迟）
2. 简单字符串操作（截取、补齐、替换）——用内置 Transformer
3. 跨行计算（需要"上一行"的值）——Groovy Transformer 只能看到当前 Record

### 4.6 注意事项

1. Groovy 脚本中 `return record;` 不可省略，否则视为过滤
2. `import` 的类必须已在 DataX classpath 中
3. 脚本中的 null 检查必须覆盖所有 `getColumn(i)` 返回值
4. JSON 中的正则 `\d` 需要写成 `\\\\d`（四重转义）
5. GroovyTransformer 对重复脚本做了缓存，避免重复编译

### 4.7 思考题

1. 如果在 Groovy 脚本中调用 `System.gc()` 会发生什么？DataX 是否能拦截这类危险操作？
2. 假设你需要对 1 亿行数据执行手机号脱敏，用 dx_replace 和 dx_groovy 的性能差异有多大（给出定量估算）？在什么情况下 dx_groovy 反而更划算？

（答案见附录）
