# 第29章：流式数据同步——StreamReader与Writer 与测试技巧

## 1. 项目背景

某金融科技团队完成了 DataX 在 5 个生产数据管道上的部署——MySQL → Hive、Oracle → PostgreSQL、MongoDB → MySQL 等均已稳定运行。但当架构师要求出具一份"各插件的读写性能基线报告"时，整个团队陷入了困境。

问题的根源在于：要测试 MySQL Reader 的极限读性能，需要目标环境有一张足够大的源表（10 亿行）。但生产环境的 MySQL 没有这么大的空闲表，运维也不允许在线上做压测。而要测试 MySQL Writer 的极限写性能，又需要有一个足够快的 Reader 作为数据源（否则瓶颈在 Reader 侧，无法评估 Writer 的真实上限）。

DataX 提供了两个被广泛忽视的内置插件来解决这个痛点：**StreamReader** 和 **StreamWriter**。StreamReader 是一个"数据工厂"——它不需要连接任何外部系统，在内存中按配置生成指定数量和格式的 Record。StreamWriter 是一个"数据黑洞"——它接收 Record 后直接丢弃，不落地任何存储。两者的组合可以：
- 单独测试 Writer 的上限：StreamReader（生成数据）→ 目标 Writer（写目标端）
- 单独测试 Reader 的上限：源 Reader（读源端）→ StreamWriter（丢弃数据）
- 做纯框架性能测试：StreamReader → StreamWriter（衡量 DataX 框架自身的吞吐上限）

本章通过系统化的基线测试方法论，从 Stream 插件原理讲起，逐步演示如何用 StreamReader/Writer 构建性能测试矩阵，并通过实战案例生成一份可交付的性能基线报告。

## 2. 项目设计——剧本式交锋对话

**（运维监控室，墙上挂着三块大屏——生产监控、DataX 任务状态、压测图形）**

**小胖**：（满头大汗）老大，MySQL Writer 的写入速度到底能到多少？我给 MySQL Reader 配了 channel=20，结果 Reader 每秒输出 8000 条，Writer 这边也在吃 8000 条——我分不清是 Reader 瓶颈还是 Writer 瓶颈！

**小白**：（在白板上画了两个背靠背的箭头）这就是经典的对端干扰——两个未知系统对测，瓶颈可能在任一端。你需要的是**解耦测试**。

**大师**：（从抽屉里拿出两块写着"StreamReader"和"StreamWriter"的牌牌）DataX 内置了两个特殊的"无状态插件"：

**StreamReader**：配置驱动，你指定列数、列类型、生成行数，它在内存中直接构造 Record，不受任何外部系统的性能限制。

```json
{
    "reader": {
        "name": "streamreader",
        "parameter": {
            "column": [
                {"type": "long", "random": "1, 100000000"},
                {"type": "string", "random": "10, 50"},
                {"type": "date", "random": "2023-01-01 00:00:00, 2025-12-31 23:59:59"}
            ],
            "sliceRecordCount": 1000000
        }
    }
}
```

**StreamWriter**：接收 Record 后直接丢弃，零 IO 开销，是纯"黑洞"。

```json
{
    "writer": {
        "name": "streamwriter",
        "parameter": {
            "print": false
        }
    }
}
```

**技术映射**：StreamReader = 自来水厂的加压泵（按需制造水流）。StreamWriter = 下水道（水来了就放走，不存不堵）。传统的"MySQL Reader → MySQL Writer"对测 = 两个水缸互相倒水——你分不清是哪个缸漏水。正确的做法是先测泵（StreamReader → MySQL Writer = 只测 Writer），再测排水（MySQL Reader → StreamWriter = 只测 Reader）。

**小胖**：（恍然大悟）所以我应该先跑 StreamReader → MySQL Writer，不断调大生成速度，直到 Writer 的 QPS 不再增长——那个拐点就是 Writer 的极限？

**大师**：精确。这就叫**边际递减测试法**——当 channel 从 1 增到 2 时 QPS 翻倍，但从 8 增到 16 时 QPS 只涨了 10%，说明已接近目标端瓶颈。测试矩阵如下：

| 测试场景 | Reader | Writer | 目的 |
|---------|--------|--------|------|
| 测 MySQL Writer | Stream | MySQL | 排除 Reader 干扰 |
| 测 MySQL Reader | MySQL | Stream | 排除 Writer 干扰 |
| 测框架上限 | Stream | Stream | CPU/内存/Channel 瓶颈 |
| 测整链路 | MySQL | MySQL | 真实业务场景 |

**小白**：（追问）StreamReader 生成的数据是真实随机的吗？会不会因为是随机数据而掩盖了某些性能问题？

**大师**：好问题。StreamReader 的随机数据确实有两个局限：

1. **数据不可复现**：每次运行生成不同的值，无法做回归对比
2. **数据类型单一**：所有 LongColumn 的值都在指定范围内随机，但真实数据有长尾分布（如 order_id 密集区间 vs 稀疏区间）

解决办法是：先用 Stream 做快速摸底（5 分钟出结果），再用真实数据样本来做精确校准。Stream 是粗筛工具，不是精确测量工具。

**小胖**：那 `sliceRecordCount` 是每个 Task 生成的记录数吗？

**大师**：对。`sliceRecordCount = 1000000` 意味着每个 Task 生成 100 万条。如果你配了 channel=10，总生成量 = 10 × 100 万 = 1000 万条。

**但是**——这里有一个坑。`sliceRecordCount` 是**每个 Task** 的量，不是总量。如果你用 `querySql` 模式指定了多个 querySql，每个 querySql 分别对应一个 Task。StreamReader 没有 querySql 模式，它自动生成 Task，Task 数和 channel 数一致。

## 3. 项目实战

### 3.1 步骤一：Stream 基础实验——DataX 框架自身吞吐上限

**目标**：用 StreamReader → StreamWriter 测试纯 DataX 框架（不含外部 IO）的最大吞吐量。

**配置**（`stream_baseline.json`）：

```json
{
    "job": {
        "content": [{
            "reader": {
                "name": "streamreader",
                "parameter": {
                    "column": [
                        {"type": "long", "random": "1, 100000000"},
                        {"type": "string", "random": "10, 50"},
                        {"type": "double", "random": "0.0, 10000.0"},
                        {"type": "date", "random": "2024-01-01 00:00:00, 2024-12-31 23:59:59"},
                        {"type": "bool", "random": "0, 1"}
                    ],
                    "sliceRecordCount": 500000
                }
            },
            "writer": {
                "name": "streamwriter",
                "parameter": {"print": false}
            }
        }],
        "setting": {
            "speed": {"channel": 1}
        }
    }
}
```

**执行命令**：

```powershell
python datax.py stream_baseline.json
```

**日志输出**：

```
2025-01-15 14:30:00.001 - StreamReader starts generating records
2025-01-15 14:30:05.234 - Task taskId=0 finished. Read: 500000, Write: 500000
2025-01-15 14:30:05.235 - Job finished.
Total time: 5.234s
Average speed: 95523rec/s
Total bytes: 75,834,212 bytes (72.3 MB)
Average byte speed: 14,486,712 bytes/s (13.8 MB/s)
```

**逐步调大 channel 找到框架瓶颈**：

| channel | 总记录数 | 耗时(s) | QPS(rec/s) | 吞吐(MB/s) | 备注 |
|---------|---------|---------|-----------|-----------|------|
| 1 | 50万 | 5.2 | 96,154 | 13.8 | 基准 |
| 2 | 100万 | 5.4 | 185,185 | 26.5 | 近线性增长 |
| 4 | 200万 | 5.8 | 344,828 | 49.5 | 仍有增长 |
| 8 | 400万 | 6.5 | 615,385 | 88.2 | 增长放缓 |
| 16 | 800万 | 9.2 | 869,565 | 124.5 | CPU几乎100% |
| 32 | 1600万 | 18.1 | 883,978 | 126.3 | **框架上限** |

**结论**：在测试机（8C16G）上，DataX 框架自身的吞吐上限约为 **88 万 rec/s（126 MB/s）**，瓶颈在 CPU（Channel 内 Record 的序列化/反序列化 + MemoryChannel 的 push/pull 锁竞争）。

### 3.2 步骤二：压测 MySQL Writer——使用 StreamReader 作为无限数据源

**目标**：用 StreamReader 持续生成数据，压测 MySQL 的写入极限。

**配置**（`stream_to_mysql_bench.json`）：

```json
{
    "job": {
        "content": [{
            "reader": {
                "name": "streamreader",
                "parameter": {
                    "column": [
                        {"type": "long",    "random": "1, 100000000"},
                        {"type": "string",  "random": "1, 50"},
                        {"type": "double",  "random": "0.00, 99999.99"},
                        {"type": "date",    "random": "2024-01-01 00:00:00, 2024-12-31 23:59:59"},
                        {"type": "long",    "random": "0, 1"}
                    ],
                    "sliceRecordCount": 2000000
                }
            },
            "writer": {
                "name": "mysqlwriter",
                "parameter": {
                    "username": "root",
                    "password": "root",
                    "writeMode": "insert",
                    "column": ["id", "name", "amount", "create_time", "status"],
                    "session": [
                        "SET autocommit=0",
                        "SET unique_checks=0",
                        "SET foreign_key_checks=0"
                    ],
                    "batchSize": 2048,
                    "preSql": [
                        "CREATE TABLE IF NOT EXISTS bench_writer_test ("
                        + "id BIGINT, name VARCHAR(50), amount DECIMAL(10,2), "
                        + "create_time DATETIME, status TINYINT, "
                        + "PRIMARY KEY (id)"
                        + ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4",
                        "TRUNCATE TABLE bench_writer_test"
                    ],
                    "connection": [{
                        "table": ["bench_writer_test"],
                        "jdbcUrl": ["jdbc:mysql://localhost:3306/test?useSSL=false&rewriteBatchedStatements=true"]
                    }]
                }
            }
        }],
        "setting": {
            "speed": {"channel": 4}
        }
    }
}
```

**逐步调优 batchSize 找到最佳写入速度**：

| batchSize | channel | QPS(rec/s) | 备注 |
|-----------|---------|-----------|------|
| 512 | 4 | 45,231 | 基准 |
| 1024 | 4 | 78,450 | +73% |
| 2048 | 4 | 112,302 | +43% |
| 4096 | 4 | 128,765 | +15% |
| 8192 | 4 | 120,112 | 开始下降（GC压力） |
| 2048 | 8 | 145,223 | 增加并发 |
| 2048 | 16 | 152,008 | **MySQL Writer 上限** |

**关键发现**：
1. batchSize=2048 是吞吐拐点，再大增益递减
2. channel 从 8 增到 16 几乎零增益 → MySQL 瓶颈在 InnoDB 的 redo log 刷盘
3. `rewriteBatchedStatements=true` 对吞吐提升约 30%

### 3.3 步骤三：压测 MySQL Reader——使用 StreamWriter 作为数据黑洞

**目标**：用 MySQL Reader 读取大表，StreamWriter 丢弃数据，纯粹测试读取速度。

**前提**：创建一张 1000 万行的测试表。

```sql
CREATE TABLE bench_reader_test (
    id BIGINT PRIMARY KEY,
    name VARCHAR(50),
    amount DECIMAL(10,2),
    create_time DATETIME,
    status TINYINT,
    INDEX idx_status (status)
) ENGINE=InnoDB;

-- 用存储过程灌入 1000 万行
DELIMITER $$
CREATE PROCEDURE gen_reader_data(IN total INT)
BEGIN
    DECLARE i INT DEFAULT 1;
    WHILE i <= total DO
        INSERT INTO bench_reader_test VALUES (
            i,
            CONCAT('user_', FLOOR(1 + RAND() * 500000)),
            ROUND(RAND() * 100000, 2),
            DATE_ADD('2024-01-01', INTERVAL FLOOR(RAND() * 365) DAY),
            FLOOR(RAND() * 4)
        );
        SET i = i + 1;
        IF i % 10000 = 0 THEN COMMIT; END IF;
    END WHILE;
END$$
DELIMITER ;

CALL gen_reader_data(10000000);
```

**DataX 配置**（`mysql_to_stream_bench.json`）：

```json
{
    "job": {
        "content": [{
            "reader": {
                "name": "mysqlreader",
                "parameter": {
                    "username": "root",
                    "password": "root",
                    "column": ["id", "name", "amount", "create_time", "status"],
                    "splitPk": "id",
                    "connection": [{
                        "querySql": [],
                        "table": ["bench_reader_test"],
                        "jdbcUrl": ["jdbc:mysql://localhost:3306/test?useSSL=false&useCursorFetch=true"]
                    }]
                }
            },
            "writer": {
                "name": "streamwriter",
                "parameter": {"print": false}
            }
        }],
        "setting": {
            "speed": {"channel": 8}
        }
    }
}
```

**逐步调优 fetchSize 找到最佳读取速度**：

| fetchSize | channel | QPS(rec/s) | 备注 |
|-----------|---------|-----------|------|
| 默认(0) | 8 | 89,200 | 逐行拉取，极慢 |
| 1000 | 8 | 156,400 | +75% |
| 5000 | 8 | 210,300 | +34% |
| Integer.MIN_VALUE | 8 | 245,800 | **流式读取，最快** |
| Integer.MIN_VALUE | 16 | 312,100 | +27% |
| Integer.MIN_VALUE | 32 | 345,200 | **MySQL Reader 上限** |

**关键发现**：
1. `fetchSize=Integer.MIN_VALUE`（-2147483648）触发 MySQL JDBC 的流式读取模式——ResultSet 逐行从服务端拉取，不一次性加载到客户端内存
2. 流式读取 + channel=32 达到 34.5 万 rec/s，受限于 MySQL 服务端的网络带宽和查询线程数

### 3.4 步骤四：模拟真实业务数据的 StreamReader 配置

**目标**：用 StreamReader 生成符合业务特征的数据（带倾斜分布、关联关系），而非纯随机。

**带数据倾斜的生成配置**：

```json
{
    "reader": {
        "name": "streamreader",
        "parameter": {
            "column": [
                {
                    "type": "long",
                    "random": "1, 1000000",
                    "comment": "user_id: 均匀分布"
                },
                {
                    "type": "string",
                    "value": "click,scroll,purchase,login,logout,share",
                    "comment": "action: 6 种离散值"
                },
                {
                    "type": "long",
                    "random": "1, 100",
                    "comment": "item_id: 少量商品"
                },
                {
                    "type": "double",
                    "random": "0.01, 99.99",
                    "comment": "amount: 交易金额"
                },
                {
                    "type": "date",
                    "random": "2025-01-15 08:00:00, 2025-01-15 23:59:59",
                    "comment": "ts: 集中在一天内"
                },
                {
                    "type": "bool",
                    "random": "0, 1",
                    "comment": "paid: 是否支付"
                },
                {
                    "type": "string",
                    "random": "32, 32",
                    "comment": "md5: 模拟哈希值"
                }
            ],
            "sliceRecordCount": 500000
        }
    }
}
```

**StreamReader 支持的 column 配置参数**：

| 参数 | 说明 | 示例 |
|------|------|------|
| `type` | 列类型 | `long`, `string`, `double`, `date`, `bool`, `bytes` |
| `value` | 固定值列表 | `"click,scroll,purchase"` → 随机取一个 |
| `random` | 随机范围 | `"1, 100"` → 1~100 之间的随机数 |
| `dateFormat` | 日期格式 | `"yyyy-MM-dd HH:mm:ss"` |

**注意**：
- `string` 类型的 `random` 格式是 `"minLen, maxLen"`（最小长度和最大长度）
- `long` / `double` 类型的 `random` 格式是 `"min, max"`（最小值和最大值）
- `date` 类型的 `random` 格式是两个日期字符串

### 3.5 步骤五：性能基线报告的生成模板

**目标**：汇总所有测试结果，生成一份结构化的性能基线报告。

```markdown
# DataX 性能基线报告
## 环境信息
- CPU: Intel Xeon E5-2680 v4 @ 2.40GHz (8核16线程)
- 内存: 32GB DDR4
- 磁盘: NVMe SSD 1TB
- JVM: OpenJDK 11, -Xms2g -Xmx8g -XX:+UseG1GC
- MySQL: 8.0.35, InnoDB, buffer_pool=16GB
- DataX 版本: 3.0.x

## 框架极限（Stream→Stream）
| channel | QPS(rec/s) | 吞吐(MB/s) | CPU(%) | 内存(GB) |
|---------|-----------|-----------|--------|---------|
| 1 | 96,154 | 13.8 | 25 | 1.2 |
| 4 | 344,828 | 49.5 | 60 | 2.4 |
| 8 | 615,385 | 88.2 | 92 | 4.1 |
| 16 | 869,565 | 124.5 | 99 | 7.8 |
| 32 | 883,978 | 126.3 | 100 | 14.2 |

## MySQL Writer 极限（Stream→MySQL）
| batchSize | channel | QPS(rec/s) | MySQL CPU(%) |
|-----------|---------|-----------|-------------|
| 2048 | 4 | 112,302 | 45 |
| 2048 | 8 | 145,223 | 68 |
| 2048 | 16 | 152,008 | 85 |
| 4096 | 16 | 148,500 | 88 |

**MySQL Writer 瓶颈**: 15.2 万 rec/s，受限于 InnoDB redo log 刷盘速度。
**推荐配置**: batchSize=2048, channel=8-16 (根据 MySQL CPU 调整)

## MySQL Reader 极限（MySQL→Stream）
| fetchSize | channel | QPS(rec/s) |
|-----------|---------|-----------|
| Integer.MIN_VALUE | 8 | 245,800 |
| Integer.MIN_VALUE | 16 | 312,100 |
| Integer.MIN_VALUE | 32 | 345,200 |

**MySQL Reader 瓶颈**: 34.5 万 rec/s，受限于 MySQL 网络带宽。
**推荐配置**: fetchSize=Integer.MIN_VALUE (启用流式读取)
```

### 3.6 可能遇到的坑及解决方法

**坑1：StreamWriter 的 `print: true` 导致性能暴跌**

`print: true` 会将每条 Record 打印到 stdout（每条约 200 字节），在 10 万 rec/s 时相当于每秒往 stdout 写 20MB。stdout 的写入速度远低于 MemoryChannel，会成为吞吐瓶颈。

**解决方案**：压测时设 `print: false`，只有在调试单条 Record 时才设为 `true` 且 `sliceRecordCount: 10`。

**坑2：StreamReader 生成的 Record 大小恒定**

`random: "10, 50"` 生成长度 10~50 的字符串，但 Column 层面记录的 `byteSize` 是固定的，内存占用比"真实数据中的长 VARCHAR 字段"低。这会导致 StreamReader → MySQL Writer 压测出来的 QPS 偏高。

**解决方案**：用 `random: "50, 200"` 生成大字段（如模拟 JSON 或 TEXT 列），更贴近真实场景。

**坑3：batchSize 调太大导致 OOM**

`batchSize=65535` 时，每个 PreparedStatement 的 batch 缓存了 65535 条 Record 的字节缓冲区。如果每条 Record 100 字节，一个 batch 就是 6.5MB。4 个 channel × 2（Reader+Writer 缓冲）= 52MB，加上 JVM 堆本身 → OOM。

**经验值**：batchSize × 单条字节数 × channel × 3（安全系数）< Xmx 的 50%。

## 4. 项目总结

### 4.1 StreamReader/StreamWriter 能力矩阵

| 测试场景 | 组合 | 测什么 | 排除干扰 |
|---------|------|--------|---------|
| 框架极限 | Stream→Stream | DataX 引擎 + Channel 吞吐 | 外部IO |
| Writer 极限 | Stream→目标Writer | 目标端写入性能 | 源端读取 |
| Reader 极限 | 源Reader→Stream | 源端读取性能 | 目标端写入 |
| 端到端 | 源Reader→目标Writer | 整链路性能 | 无 |
| 随机压测 | Stream→目标Writer | 目标端极端写入 | 源端 |
| 回归对比 | 固定配置→Stream | 版本升级/配置变更影响 | 数据差异 |

### 4.2 优点

1. **零外部依赖**：StreamReader 不需要源表，StreamWriter 不需要目标表，随时可用
2. **解耦测试**：精确识别瓶颈在 Reader 还是 Writer
3. **可复现**：固定 `value` 或 `seed` 参数（如果支持），可做回归对比
4. **快速摸底**：5 分钟即可完成一轮性能测试

### 4.3 缺点

1. **数据不真**：随机数据无法模拟真实数据的分布特征（热点、长尾、大字段）
2. **无 I/O 模拟**：StreamReader 不读磁盘、不建网络连接，测不了 I/O 竞争
3. **无数据校验**：StreamWriter 丢弃数据，无法做写入验证
4. **单机局限**：只测单机性能，无法模拟分布式环境

### 4.4 适用场景

1. 新插件/新目标端的性能验收
2. 硬件升级前后的性能对比（如 SSD vs NVMe）
3. 调优参数（batchSize/channel/fetchSize）时的快速迭代
4. 版本升级的回归性能验证
5. 生产上线前的容量规划

### 4.5 不适用场景

1. 精确的端到端延迟测量（需真实业务数据）
2. 网络延迟模拟（Stream 测不出跨机房延迟）
3. 数据质量问题测试（随机数据没有脏数据）
4. 分布式场景测试（Stream 只在单 JVM 内运行）

### 4.6 注意事项

1. `sliceRecordCount` 是每个 Task 的量，总记录数 = `sliceRecordCount × channel`（或实际 Task 数）
2. StreamWriter 的 `print` 设 `true` 会严重拖慢性能，压测时务必 `false`
3. StreamReader 的 `random` 范围越大，数据分布越散，内存占用越低（但测试的"代表性"也越低）
4. 不要用 Stream→Stream 的 QPS 作为业务的"理想值"——真实场景有网络 I/O、磁盘 I/O、锁竞争等额外开销

### 4.7 思考题

1. 如果你用 StreamReader → MySQL Writer 测得 Writer 瓶颈在 15 万 rec/s，但实际 MySQL Reader → MySQL Writer 只有 8 万 rec/s。请分析除 Writer 之外的瓶颈可能在哪里？如何用 Stream 工具链定位？
2. 假设你需要测试 DataX 在"大字段"场景下的性能（如每行包含一个 1MB 的 BLOB 列），StreamReader 能否生成这种数据？如果可以应该怎么配？

（答案见附录）
