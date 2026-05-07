# 第14章：Transformer 入门——substr/pad/replace/filter 四大变换

## 1. 项目背景

某零售公司的数据同步流水线每日凌晨自动运行，将 MySQL 业务库的 `customer` 表同步到数据仓库。但数据安全部门提出了合规要求：
1. 手机号 `phone` 字段必须脱敏（中间 4 位替换为 `****`）
2. 测试账户（`user_name` 包含 `test_` 前缀）不进入数据仓库
3. 产品编码 `product_code` 不足 10 位的左补 0 到 10 位
4. 邮箱域名必须统一为小写
5. 注册时间 `register_time` 从 Unix 时间戳转为 `yyyy-MM-dd` 格式

运营团队之前用 Python 脚本做这些处理——先全量导出 CSV → Python 清洗 → 再导入数据仓库。这"三段式"流程费时费力，且 Python 脚本和导出逻辑分开维护，字段变更时需要改两处。

DataX 的 Transformer 机制可以在数据传输链路中直接完成字段级清洗，无需额外的中间文件。它支持 6 种内置变换：`dx_substr`（截取）、`dx_pad`（补齐）、`dx_replace`（替换）、`dx_filter`（过滤）、`dx_digest`（摘要）、`dx_groovy`（自定义脚本）。本章带你掌握前四种最常用的内置 Transformer。

## 2. 项目设计——剧本式交锋对话

**（数据安全评审会，产品经理和安全工程师正在争论）**

**小胖**：（困惑）这数据清洗为啥非要放在同步环节？我们不是有个专门的 Python 清洗脚本吗？

**安全工程师**：因为中间文件有泄露风险！你先把原始手机号导出到 CSV，Python 再读 CSV 做脱敏——这个 CSV 文件可能被任何人复制走。

**小白**：（快速翻文档）DataX 的 Transformer 就是解决这个的——它在 Reader 和 Channel 之间拦截每条 Record，在数据进入 Channel 之前就完成脱敏/过滤/格式化。最终写入目标端的已经是脱敏后的数据，不存在中间泄漏环节。

**大师**：（打开架构图）Transformer 的插入位置有两个选择：

```
位置1: Reader → Transformer → Channel → Writer  （在push之前变换）
位置2: Reader → Channel → Transformer → Writer  （在pull之后变换）
```

两种位置的效果一样，但位置 1 可以更早过滤数据——如果 `dx_filter` 判定不需要某行，它根本不会进入 Channel，节省了 Channel 的内存。

**技术映射**：Transformer = 流水线上的质检员。位置 1 是在入库前检查（源头拦截），位置 2 是在出库后检查（末端拦截）。对于过滤型 Transformer，越早拦截越省内存。

**小胖**：那我有个疑问——dx_filter 过滤掉的数据算脏数据吗？会不会触发 errorLimit？

**大师**：不算。`dx_filter` 是主动丢弃（业务逻辑），不是被动失败（数据质量问题）。它内部的实现是 `return null`，Transformer 执行层看到 null 就把这条 Record 丢弃，不走脏数据计数器。

**小白**：（追问）那 Transformer 的执行时机呢？它是在 split 的 Task 里还是 JobContainer 里？

**大师**：在 Task 的 ReaderRunner 里。每次 `startRead()` 循环产出 Record 后，先经过 Transformer 链（可能有多个 Transformer 顺序执行），然后再 `sendToWriter()`。所以 Transformer 的计算开销是算在每个 Task 的 `cost` 里的。

**小胖**：那 dx_groovy 和另外五个内置 Transformer 有什么区别？

**大师**：本质区别在于**代码编译时机**。dx_substr、dx_pad、dx_replace、dx_filter、dx_digest 这 5 个是 Java 硬编码的固定逻辑，在 DataX 启动时就加载了。dx_groovy 是运行时动态编译的——DataX 把你的 Groovy 脚本字符串传给 GroovyShell，每次处理一条 Record 都要执行一次脚本。这带来了灵活性——你可以写任意逻辑，但也带来了性能开销——每条 Record 都多了一次 Groovy 方法调用。

在 100 万条数据量下，dx_substr 耗时约 0.5 秒，dx_groovy 耗时约 5 秒，差 10 倍。所以能用内置 Transformer 解决的问题，别用 dx_groovy。

## 3. 项目实战

### 3.1 步骤一：dx_substr——字符串截取

**目标**：从 `phone` 字段中提取前 3 位和后 4 位（脱敏）。

**参数说明**：

| 参数 | 类型 | 说明 | 示例 |
|------|------|------|------|
| `columnIndex` | int | 要截取的列序号（从 0 开始） | 1 |
| `paras` | array | `[起始索引, 结束索引)` | `[2, 5]` 截取第2~4个字符 |

```json
{
    "transformer": [
        {
            "name": "dx_substr",
            "parameter": {
                "columnIndex": 1,
                "paras": ["0", "3"]
            }
        }
    ]
}
```

**效果**：

| 原始 phone | 截取 [0,3) | 结果 |
|-----------|-----------|------|
| "13912345678" | 前 3 位 | "139" |
| "13898765432" | 前 3 位 | "138" |

**组合多个 Transformer**（先截取前缀 + 拼接星号）：

```json
{
    "transformer": [
        {
            "name": "dx_substr",
            "parameter": {
                "columnIndex": 1,
                "paras": ["0", "3"]
            }
        }
    ]
}
```

这个截取后 `phone` 从 "13912345678" 变为 "139"——丢失了后半部分。实际上脱敏需要保留前缀+星号+后缀，这超出了 dx_substr 的能力，需要 dx_groovy 或 dx_replace。

### 3.2 步骤二：dx_replace——正则替换（真正的脱敏方案）

**目标**：用正则将手机号中间 4 位替换为 `****`。

| 参数 | 类型 | 说明 |
|------|------|------|
| `columnIndex` | int | 操作的列序号 |
| `paras` | array | `[正则表达式, 替换字符串]` |

```json
{
    "transformer": [
        {
            "name": "dx_replace",
            "parameter": {
                "columnIndex": 1,
                "paras": ["(\\d{3})\\d{4}(\\d{4})", "$1****$2"]
            }
        }
    ]
}
```

**效果**：

| 原始 phone | 替换后 |
|-----------|--------|
| "13912345678" | "139****5678" |
| "13898765432" | "138****5432" |

**邮箱域名统一小写**：

```json
{
    "name": "dx_replace",
    "parameter": {
        "columnIndex": 2,
        "paras": ["([a-zA-Z0-9._%+-]+)@([a-zA-Z0-9.-]+)", ""],
        "hint": "替换为空 = 不做修改，实际需求改为dx_groovy转小写"
    }
}
```

实际上 `dx_replace` 不能做大小写转换（它只是正则替换）。邮件地址转小写需要 dx_groovy，这体现了内置 Transformer 的能力边界。

### 3.3 步骤三：dx_pad——字符串补齐

**目标**：产品编码 `product_code` 不足 10 位的左补 0 到 10 位。

| 参数 | 类型 | 说明 |
|------|------|------|
| `columnIndex` | int | 操作的列序号 |
| `paras` | array | `[目标长度, "填补字符", 方向(1=左补|2=右补|3=居中补)]` |

```json
{
    "transformer": [
        {
            "name": "dx_pad",
            "parameter": {
                "columnIndex": 3,
                "paras": ["10", "0", "1"]
            }
        }
    ]
}
```

**效果**：

| 原始 product_code | 补齐后 |
|------------------|--------|
| "A123" | "000000A123" |
| "B45678" | "0000B45678" |
| "C1234567890" | "C1234567890"（超长不截断） |

**注意**：如果原始字符串长度超过目标长度，dx_pad **不会截断**，保持原值。如果需要截断，先用 dx_substr 截断再用 dx_pad 补齐。

### 3.4 步骤四：dx_filter——行级过滤

**目标**：过滤掉测试账户（`user_name` 包含 `test_` 前缀）和金额小于 1 元的记录。

| 参数 | 类型 | 说明 |
|------|------|------|
| `columnIndex` | int | 判断的列序号 |
| `paras` | array | `[比较运算符, 比较值]` |

**支持的操作符**：`=`、`!=`、`>`、`<`、`>=`、`<=`、`like`（模糊匹配）、`not like`

```json
{
    "transformer": [
        {
            "name": "dx_filter",
            "parameter": {
                "columnIndex": 0,
                "paras": ["not like", "test_%"]
            }
        },
        {
            "name": "dx_filter",
            "parameter": {
                "columnIndex": 2,
                "paras": [">=", "1.00"]
            }
        }
    ]
}
```

**执行逻辑**：
1. 第一个 filter：`user_name NOT LIKE 'test_%'` → 匹配的保留，不匹配的丢弃
2. 第二个 filter：`amount >= 1.00` → 满足的保留，不满足的丢弃
3. 两个 filter 是 AND 关系——只有同时通过两个过滤器的 Record 才会进入 Channel

**效果**：

| user_name | amount | 结果 |
|-----------|--------|------|
| "张三" | 100.00 | ✓ 通过 |
| "test_李四" | 200.00 | ✗ 第一个 filter 过滤 |
| "王五" | 0.50 | ✗ 第二个 filter 过滤 |
| "test_赵六" | 0.01 | ✗ 两者都过滤 |

### 3.5 步骤五：完整的多层 Transformer 组合实战

**目标**：一个 Job 中同时使用 4 种 Transformer，实现需求的全部数据清洗。

```json
{
    "job": {
        "content": [{
            "reader": {
                "name": "mysqlreader",
                "parameter": {
                    "column": ["user_name", "phone", "email", "product_code", "amount", "register_time"],
                    "connection": [{
                        "table": ["customer"],
                        "jdbcUrl": ["jdbc:mysql://..."]
                    }]
                }
            },
            "writer": {
                "name": "mysqlwriter",
                "parameter": {
                    "column": ["user_name", "phone", "email", "product_code", "amount", "register_time"],
                    "writeMode": "insert",
                    "connection": [{
                        "table": ["customer_clean"],
                        "jdbcUrl": ["jdbc:mysql://..."]
                    }]
                }
            },
            "transformer": [
                {
                    "name": "dx_filter",
                    "parameter": {
                        "columnIndex": 0,
                        "paras": ["not like", "test_%"]
                    }
                },
                {
                    "name": "dx_replace",
                    "parameter": {
                        "columnIndex": 1,
                        "paras": ["(\\d{3})\\d{4}(\\d{4})", "$1****$2"]
                    }
                },
                {
                    "name": "dx_pad",
                    "parameter": {
                        "columnIndex": 3,
                        "paras": ["10", "0", "1"]
                    }
                },
                {
                    "name": "dx_groovy",
                    "parameter": {
                        "code": "import com.alibaba.datax.common.element.*;\n"
                                + "// 邮箱转小写\n"
                                + "String email = record.getColumn(2).asString();\n"
                                + "if (email != null) {\n"
                                + "    record.setColumn(2, new StringColumn(email.toLowerCase()));\n"
                                + "}\n"
                                + "// register_time 从Unix时间戳转日期字符串\n"
                                + "Long ts = record.getColumn(5).asLong();\n"
                                + "if (ts != null) {\n"
                                + "    java.text.SimpleDateFormat sdf = new java.text.SimpleDateFormat('yyyy-MM-dd');\n"
                                + "    record.setColumn(5, new StringColumn(sdf.format(new java.util.Date(ts * 1000))));\n"
                                + "}\n"
                                + "return record;"
                    }
                }
            ]
        }],
        "setting": {
            "speed": {"channel": 5}
        }
    }
}
```

**Transformer 执行顺序**：
1. dx_filter → 过滤测试账户和 1 元以下订单
2. dx_replace → 手机号脱敏
3. dx_pad → 产品编码补齐
4. dx_groovy → 邮箱小写 + 时间戳转换

**重要**：Transformer 数组中的顺序就是执行顺序。如果先执行 dx_groovy 再执行 dx_replace，dx_replace 可能覆盖 dx_groovy 的修改。

### 3.6 可能遇到的坑及解决方法

**坑1：dx_filter 操作符拼写错误**

`"not like"` 中间必须有空格，写成 `"notlike"` 会抛异常。同理 `">="` 不能写成 `"=>"`。

**坑2：Transfomer 修改后的列类型与 Writer 期望不符**

如果 Writer 的 `column.type` 声明为 `DECIMAL`，但你在 Transformer 中把 DoubleColumn 改成了 StringColumn，Writer 写入时会报类型不匹配。

解决：在配置中保持 Writer 的 column 类型与 Transformer 输出的类型一致。或使用 Writer 的 `column` 字段指定目标类型，让 Writer 自行转换。

**坑3：dx_pad 和 dx_substr 混合使用时的顺序**

需求：截取字符串前 6 位 → 补齐到 10 位。

正确顺序：
```json
"transformer": [
    {"name": "dx_substr", "parameter": {"columnIndex": 0, "paras": ["0", "6"]}},
    {"name": "dx_pad",    "parameter": {"columnIndex": 0, "paras": ["10", "0", "1"]}}
]
```

如果先 pad 再 subtr——substr 会把 pad 的结果又截掉。

**坑4：dx_groovy 脚本中的空指针**

如果某行 phone 字段为 null，`record.getColumn(1).asString()` 会返回 null。Groovy 脚本中一定要加 null 判断，否则 `null.replaceAll(...)` 会抛 NPE，导致该行被标记为脏数据。

## 4. 项目总结

### 4.1 内置 Transformer 速查表

| Transformer | 功能 | 参数格式 | 典型场景 |
|------------|------|---------|---------|
| dx_substr | 截取 | `[起始索引, 结束索引)` | 提取身份证前 6 位（地域码） |
| dx_pad | 补齐 | `[长度, "填充符", 方向]` | 订单编号左补 0 |
| dx_replace | 替换 | `[正则, 替换值]` | 手机号脱敏、敏感词过滤 |
| dx_filter | 过滤 | `[操作符, 阈值]` | 过滤脏数据、测试数据 |
| dx_digest | 摘要 | `[算法, 列索引...]` | MD5/SHA 加密敏感字段 |
| dx_groovy | 自定义 | `{code: "脚本"}` | 复杂多字段联合变换 |

### 4.2 优点

1. **无中间文件**：数据在 Channel 中直接变换，不落盘，无安全风险
2. **声明式配置**：JSON 配置即文档，一目了然
3. **组合能力强**：多个 Transformer 顺序执行，灵活组合
4. **内置即高效**：Java 硬编码的 Transformer 比 Groovy 快 10 倍
5. **过滤在 Channel 前**：dx_filter 丢弃的数据不进入 Channel，节省内存

### 4.3 缺点

1. **无跨行变换能力**：Transformer 只能处理单行，不能做"前一行+当前行"的计算
2. **dx_replace 不支持大小写转换**：`$1.toLowerCase()` 这样的替换语法不支持
3. **dx_filter 只能单条件**：不能组合 `column0 > 100 AND column1 != "test"`（需多个 filter）
4. **调试困难**：Transformer 失败只会标记为脏数据，不会有详细的列级日志
5. **dx_groovy 性能差**：每条 Record 都执行一次 Groovy 脚本，大量使用时建议转为内置 Transformer

### 4.4 适用场景

1. 数据脱敏（手机号、身份证、银行卡号）
2. 测试数据过滤（test_ 前缀、金额 < 1 元）
3. 字段格式化（日期格式统一、编码补齐）
4. 字符集规范化（全角转半角、大小写统一）
5. 简单数据清洗（去除首尾空格、替换特殊字符）

### 4.5 不适用场景

1. 跨行计算（如"当前行 = 上一行 + 1"）
2. 复杂 ETL 逻辑（如 join 多表、分组聚合）
3. 实时数据质量校验（需要外部规则引擎）

### 4.6 注意事项

1. Transformer 在 Task 线程中执行，多个 Task 间完全隔离
2. `columnIndex` 从 0 开始，不是从 1 开始
3. dx_filter 丢弃的数据不计入脏数据，不触发 errorLimit
4. Transformer 执行顺序与 JSON 数组顺序一致
5. 多个 filter 之间是 AND 关系，不是 OR

### 4.7 思考题

1. 如果要将手机号从"13912345678"脱敏为"139****5678"，需要保留前缀 3 位和后缀 4 位，中间的用星号替换。用 dx_substr + dx_replace 的组合能否实现？还是必须用 dx_groovy？
2. dx_filter 的 `paras: [">=","100"]` 中的 `"100"` 是字符串比较还是数值比较？如果是字符串比较，"9" 和 "100" 的大小关系是什么？

（答案见附录）
