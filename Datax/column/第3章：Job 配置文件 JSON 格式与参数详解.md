# 第3章：Job 配置文件 JSON 格式与参数详解

## 1. 项目背景

数据平台组的实习生小张花了一周时间写出了第一版 MySQL → HDFS 同步配置，兴冲冲提交到 GitLab。Code Review 时却被 TL 打了回来——他的 JSON 文件里有 5 处致命问题：`job` 写成了 `Job`（大小写错误）、`parameter` 里面多了一个尾随逗号（JSON 标准不允许）、`channel` 值写成了字符串 `"5"` 而非数字 `5`、`connection` 数组包了一层多余的对象、以及把 `speed.byte` 的单位理解成了 MB 而非 Byte。

这不是个例。在实际生产中，约 40% 的 DataX 任务失败根源是 JSON 配置错误。更隐蔽的问题是配置"能跑但不对"——比如 `speed.record` 设置为 100000，用户以为是 10 万条/秒，实际 DataX 是记录数/秒直接生效，结果在一个百万级任务中，限速过严导致本该 10 分钟的任务跑了 2 小时没人发现。

本章带你逐字逐句拆解 DataX JSON 配置的语法规则、参数含义和校验机制，让你从"手写 JSON 全靠蒙"升级到"一张配置表走天下"。

## 2. 项目设计——剧本式交锋对话

**（下午茶时间，小胖端着奶茶过来）**

**小胖**：小张，你的 MR 又被打了？我看看——咦，不就一个 JSON 吗，能有啥坑？

**小张**：（一脸郁闷）你看这个，我写了 `"channel":"5"`，TL 说必须写 `"channel":5`，不加引号。这不都是 5 吗？

**小白**：（放下手中的书）这就是 JSON 的严格类型检查。`"5"` 是字符串 "5"，`5` 是数字 5。DataX 内部用 Java 的 `Integer.parseInt()` 解析 channel 参数，传字符串进去直接抛 `NumberFormatException`。我第一周也在这上面栽了。

**大师**：（走过了，在白板上画了一个 JSON 结构树）来，我们彻底把 DataX 的 JSON 配置搞清楚。这就像你点外卖——你得告诉平台三个东西：你从哪家店订（Reader）、送到哪（Writer）、以及一些配送要求（Setting）。JSON 就是这订单的格式。

**技术映射**：JSON 配置 = 外卖订单。Reader = 餐厅名称和菜品（源端配置），Writer = 收货地址（目标端配置），Setting = 配送要求（速度、容错）。

**小胖**：那我有个疑问——`job.content` 是个数组，意味着一个 Job 可以有多个 Reader-Writer 对？那不是可以在一个任务里同时同步多个表？

**大师**：（赞许地点头）小胖这次问到点子上了！是的，一个 Job 的 content 数组可以有多个元素，每个元素是一组 Reader-Writer 配对。比如你可以同时把 MySQL 的订单表和用户表同步到 HDFS 的不同路径。但需要注意——这些 Reader-Writer 对共享同一个 speed 限速，而不是各自独立限速。

**小白**：（追问）那 `job.setting` 里的 `speed.channel` 是什么意思？跟 `content` 里的每个 pair 是什么关系？

**大师**：好问题。`speed.channel` 是 Job 级别的总并发数。如果有 2 个 content 元素，每个 split 出 5 个 Task，一共 10 个 Task，那么 channel=5 意味着同时只有 5 个 Task 在运行。Task 总数 10 个，并发 5 个，分两波执行。

**小胖**：（挠头）那 `speed.byte` 和 `speed.record` 又是干啥用的？不就是跑得快慢吗，搞两个参数是不是多余？

**大师**：完全不多余，各有各的用途。`speed.byte` 是每秒多少字节，主要用于保护网络带宽——比如你的 MySQL 在另一个机房，带宽只有 100Mbps，设 `speed.byte=10485760`（10MB/s）就是占了 80% 带宽，留 20% 给业务。`speed.record` 是每秒多少条记录，主要用于保护数据库——比如每行 200 字节，10 万行/秒就是 20MB/s，但如果每行只有 10 字节，10 万行/秒才 1MB/s。所以 record 限速更关注数据库的 TPS 承载能力。

**技术映射**：byte 限速 = 高速公路限速（保护路基），record 限速 = 单车道限行（保护收费站）。

**小白**：如果两个限速都设了，取哪个？

**大师**：取更严格的那个。给你一个公式：`实际执行速度 = min(speed.byte/单条记录平均大小, speed.record)`。

**小张**：那 `errorLimit` 又是什么？

**大师**：容错的最后一道防线。`errorLimit.record` 表示最多容忍多少条脏记录，设为 0 意味着零容忍——只要有一条数据出错（比如类型转换失败），整个 Job 就终止。`errorLimit.percentage` 表示最多容忍百分之多少的脏数据，按脏记录数除以总记录数计算。两个条件只要触发了任意一个，Job 就会失败。

## 3. 项目实战

### 3.1 JSON 配置完整结构

```json
{
    "job": {
        "content": [
            {
                "reader": { ... },
                "writer": { ... }
            }
        ],
        "setting": {
            "speed": {
                "channel": 5,
                "byte": 10485760,
                "record": 100000
            },
            "errorLimit": {
                "record": 1000,
                "percentage": 0.02
            }
        }
    }
}
```

### 3.2 步骤一：解析 content 段（Reader + Writer 双重契约）

**目标**：理解 Reader 和 Writer 的 JSON 通用结构。

Reader 和 Writer 共用同一套 JSON Schema：

```json
{
    "name": "插件名称",
    "parameter": {
        "插件特定参数": "..."
    }
}
```

示例——MySQL Reader 完整配置：

```json
{
    "reader": {
        "name": "mysqlreader",
        "parameter": {
            "username": "root",
            "password": "123456",
            "column": ["id", "name", "age", "create_time"],
            "splitPk": "id",
            "connection": [
                {
                    "table": ["user"],
                    "jdbcUrl": ["jdbc:mysql://127.0.0.1:3306/test"]
                }
            ],
            "where": "create_time >= '2026-01-01'"
        }
    }
}
```

常见陷阱：`connection` 是一个数组，每个元素包含 `table`（数组）和 `jdbcUrl`（数组）。这意味着一个 Reader 可以同时读多个表、连接多个数据库实例（负载均衡）。

```json
"connection": [
    {
        "table": ["orders", "order_detail"],
        "jdbcUrl": [
            "jdbc:mysql://db1:3306/shop",
            "jdbc:mysql://db2:3306/shop"
        ]
    }
]
```

上面的配置会把 `db1` 和 `db2` 的 `orders`、`order_detail` 两张表都读出来（共 4 组查询）。

### 3.3 步骤二：解析 setting 段（Speed + ErrorLimit）

**目标**：掌握限速和容错参数的精确配置。

**speed 参数速查表**：

| 参数 | 类型 | 默认值 | 含义 | 建议范围 |
|------|------|--------|------|---------|
| `channel` | int | 1 | 并发通道数 | 1~CPU核数×4 |
| `byte` | long | -1(不限速) | 每秒字节数上限 | 1048576(1MB)~1073741824(1GB) |
| `record` | long | -1(不限速) | 每秒记录数上限 | 1000~1000000 |

channel 为 0 时的特殊处理：JobContainer.preCheck() 会校验 channel 必须 > 0，否则直接抛异常。

**限速效果组合测试**：

| 配置 | 预期效果 | 验证方法 |
|------|---------|---------|
| `channel=1, byte=-1, record=-1` | 不限速，单线程全速跑 | 看日志中的"平均流量" |
| `channel=5, byte=10485760` | 5 个 Channel 共享 10MB/s 总带宽 | 日志中"平均流量"接近 10MB/s |
| `channel=5, record=50000` | 5 个 Channel 共享 5 万条/秒 | 日志中"记录写入速度"接近 50000rec/s |
| `channel=5, byte=10485760, record=100000` | 取两者较严值 | 实际速度为 min(10MB/行大小, 10万条/s) |

**errorLimit 参数**：

| 参数 | 类型 | 默认值 | 含义 |
|------|------|--------|------|
| `record` | int | 0 | 最大容忍脏记录数 |
| `percentage` | double | 0 | 最大容忍脏数据百分比（0~1） |

```json
"errorLimit": {
    "record": 0,
    "percentage": 0
}
```

以上配置意味着零容忍——任何一条脏数据都会导致 Job 失败。

### 3.4 步骤三：参数占位符与动态注入

**目标**：学会用 `-p` 参数实现配置模板化。

在 JSON 中嵌入 `${key}` 占位符：

```json
{
    "reader": {
        "name": "mysqlreader",
        "parameter": {
            "username": "${db_username}",
            "password": "${db_password}",
            "connection": [{
                "table": ["${table_name}"],
                "jdbcUrl": ["jdbc:mysql://${db_host}:${db_port}/${db_name}"]
            }],
            "where": "dt = '${bizdate}'"
        }
    }
}
```

运行时通过 `-p` 传入实际值：

```bash
python bin/datax.py job.json \
  -p "-Ddb_username=root \
      -Ddb_password=123456 \
      -Ddb_host=10.0.1.100 \
      -Ddb_port=3306 \
      -Ddb_name=shop \
      -Dtable_name=orders \
      -Dbizdate=2026-05-06"
```

**占位符替换原理**：ConfigParser 在解析 JSON 时，递归扫描所有字符串值，发现 `${key}` 就从 `-p` 传入的 map 中查找替换。如果找不到对应 key，**不会报错**，而是保留 `${key}` 原字符串——这可能导致 SQL 中的 `WHERE dt = '${bizdate}'` 因为拼不上值而查出空数据。

**最佳实践**：在配置模板中加入 "required keys" 注释，配合自动化脚本做占位符完整性校验。

```bash
# check_placeholders.sh
grep -oP '\$\{\w+\}' job.json | sort -u
```

### 3.5 步骤四：配置校验与预检查

**目标**：在真正执行前发现配置错误。

方式 1——运行时自带 preCheck：

DataX 的 JobContainer 在 split 之前会执行 `preCheck()`，验证：
- `job.content` 非空
- `speed.channel` > 0
- `reader.name` 和 `writer.name` 非空
- 必填参数存在性（由各插件自定义）

方式 2——JSON Schema 手动校验（推荐加入 CI/CD）：

```python
import json
import jsonschema

# DataX Job Schema (简版)
job_schema = {
    "type": "object",
    "properties": {
        "job": {
            "type": "object",
            "properties": {
                "content": {
                    "type": "array",
                    "minItems": 1,
                    "items": {
                        "type": "object",
                        "required": ["reader", "writer"],
                        "properties": {
                            "reader": {
                                "type": "object",
                                "required": ["name", "parameter"]
                            },
                            "writer": {
                                "type": "object",
                                "required": ["name", "parameter"]
                            }
                        }
                    }
                },
                "setting": {
                    "type": "object",
                    "required": ["speed"],
                    "properties": {
                        "speed": {
                            "type": "object",
                            "required": ["channel"],
                            "properties": {
                                "channel": {"type": "integer", "minimum": 1},
                                "byte": {"type": "integer"},
                                "record": {"type": "integer"}
                            }
                        }
                    }
                }
            },
            "required": ["content", "setting"]
        }
    },
    "required": ["job"]
}

with open("job.json") as f:
    config = json.load(f)

jsonschema.validate(config, job_schema)
print("JSON 配置校验通过")
```

### 3.6 可能遇到的坑及解决方法

**坑1：JSON 尾随逗号**

```json
// 错误——最后一个字段后不能有逗号
{
    "channel": 5,  ← 多了一个逗号！
}

// 正确
{
    "channel": 5
}
```

解决：使用 JSON Lint 校验工具，或在 VS Code 中安装 JSON 插件自动提示。

**坑2：connection 数组嵌套**

错误写法：
```json
"connection": [{
    "table": "orders",    ← 应该是数组 ["orders"]
    "jdbcUrl": "jdbc:mysql://..."  ← 应该是数组
}]
```

正确写法：
```json
"connection": [{
    "table": ["orders"],
    "jdbcUrl": ["jdbc:mysql://..."]
}]
```

**坑3：byte 单位误区**

DataX 的 `speed.byte` 单位是**字节（Byte）**，不是 KB 或 MB。

| 目标限速 | 配置值 | 计算公式 |
|---------|--------|---------|
| 1 KB/s | 1024 | 1 × 1024 |
| 1 MB/s | 1048576 | 1 × 1024 × 1024 |
| 10 MB/s | 10485760 | 10 × 1024 × 1024 |
| 100 MB/s | 104857600 | 100 × 1024 × 1024 |

**坑4：密码明文泄露**

生产环境禁止在 JSON 中硬编码密码。推荐做法：

```json
"password": "${DB_PASSWORD}"
```

配合 CI/CD 系统从密钥管理服务（Vault/KMS）注入。

## 4. 项目总结

### 4.1 JSON 配置核心规则

| 规则 | 说明 |
|------|------|
| 大小写敏感 | `job` ≠ `Job`，`reader` ≠ `Reader` |
| 数字不加引号 | `"channel":5` 不是 `"channel":"5"` |
| 数组不加外层对象 | `"table":["orders"]` 不是 `"table":{"name":"orders"}` |
| 不允许尾随逗号 | 最后一个字段后不能有逗号 |
| 不能有注释 | JSON 标准不支持注释，别写 `//` |
| 密码必须参数化 | 用 `${KEY}` 占位符，禁止明文 |

### 4.2 优点

1. **JSON 通用性**：所有主流语言的 CI/CD 工具都原生支持 JSON，容易做模板化和校验
2. **参数注入灵活**：`-p` 参数支持运行时动态替换，一套模板覆盖多环境
3. **声明式配置**：用户只需描述"做什么"，不需要写代码描述"怎么做"
4. **content 数组支持多路同步**：一个 Job 同时同步多张表，减少调度开销
5. **错误阈值机制**：不是一刀切的"失败即停止"，允许一定比例的数据质量问题

### 4.3 缺点

1. **无注释能力**：复杂的 WHERE 条件和字段映射难以维护，缺少解释性注释
2. **密码风险**：JSON 明文存储，缺乏原生的加密支持
3. **无模板继承**：不能像 YAML 那样用 `<<:` 锚点复用公共配置段
4. **动态性弱**：不支持条件判断和循环，复杂的多表配置必须借助外部脚本生成 JSON
5. **错误信息不友好**：JSON 解析报错通常只提示"第 N 行语法错误"，不指明具体违规字段

### 4.4 适用场景

1. 单次全量数据迁移（配置简单，一劳永逸）
2. 固定模式的增量同步（WHERE + 参数化日期）
3. 与调度系统集成（JSON 模板 + 外部注入）
4. 快速原型验证（手写 JSON 比写代码快）
5. 环境间配置传递（通过参数区分 dev/stg/prod）

### 4.5 不适用场景

1. 数千张表的批量同步（JSON 数量爆炸，需要自动化生成脚本）
2. 实时流式同步（DataX 是批处理架构）

### 4.6 思考题

1. 如果 `speed.byte` 设为 0，`speed.record` 设为 0，DataX 会怎么处理？是无限速还是报错？（提示：查看 Channel.statPush() 源码中的限速判断逻辑）
2. content 数组中如果有 3 组 Reader-Writer，每组 split 出 5 个 Task，但 `speed.channel=10`，此时 Task 的调度顺序是怎样的？（提示：查看 JobAssignUtil.assignFairly() 的实现）

（答案见附录）
