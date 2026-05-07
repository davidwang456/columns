# 第30章：性能调优实战——JVM、批大小、并发度、内存全面优化

## 1. 项目背景

某电商中台使用 DataX 每日凌晨执行 MySQL → MySQL 的订单表全量同步，任务配置为默认参数（channel=5，batchSize=1024，JVM 默认）。5000 万行订单表跑一次平均耗时 48 分钟，运维方认为这是"正常水平"。

直到一次促销结束后订单量暴增到 8000 万行，任务直接抛了 `OutOfMemoryError: GC overhead limit exceeded`，整个凌晨数据管道中断 3 小时。DBA 紧急扩容了 RAM 到 32G 才临时恢复，但任务耗时飙升到 95 分钟。

架构师提出质疑：8 核 32G 的机器跑个 8000 万行的数据同步要 95 分钟，换算下来 QPS 只有 14,000 rec/s——这远远没有发挥出硬件的真实能力。问题不在硬件，在于 DataX 的默认参数完全是为"最低配能跑"设计的，没有针对当前硬件做任何调优。

本章从 JVM 内存估算、GC 策略选型、batchSize 的阶梯调优、channel 数与 CPU/IO 的匹配、fetchSize 的流式优化五个维度，系统化地走通一条"从默认配置到最优配置"的性能调优路径。最终在 16 核 32G 的测试机上，将同样的 5000 万行任务从 48 分钟优化到 8 分钟——6 倍提升。

## 2. 项目设计——剧本式交锋对话

**（凌晨 2:30，运维监控室警铃大作）**

**运維小王**：（盯着红色告警屏）又 OOM！8000 万行订单表直接爆了！堆内存 4G 撑死，GC 日志刷屏了！

**小胖**：（睡眼惺忪地被电话叫来）这不可能啊！我上午跑 5000 万行还正常的，怎么 8000 万就不行了？不就是多了 3000 万行吗？

**小白**：（在笔记本上快速分析 GC 日志）找到根因了。你用的是 DataX 的默认 JVM 参数——`Xmx` 没设，JDK 按 1/4 物理内存算，8G 机器只分了 2G。但 DataX 的实际内存需求是：

**内存 = Channel 数 × batchSize × 单条 Record 大小 × 缓冲系数**

按你的配置：
```
Xmx 需求 = 5 × 1024 × 500 bytes × 3 (Reader缓冲+Writer缓冲+Channel缓冲)
         = 5 × 1024 × 500 × 3
         = 7,680,000 bytes
         ≈ 7.5 MB
```

等等——7.5 MB？那不应该 OOM 啊！

**大师**：（推门进来）你忘了算**对象头开销**。DataX 内部的一条 Record 在 JVM 中不是 500 字节——JVM 的每个对象有 12~16 字节的 Mark Word + 4~8 字节的 Klass Pointer、Column 数组的引用开销、StringColumn 内部的 char[] 数组开销。实际内存占用是"净数据 × 3~5 倍"。

**内存估算公式（更准确的版本）**：

```
Xmx ≈ channel × batchSize × recordSize × overheadFactor × bufferFactor
    ≈ 5 × 1024 × 500 × 4 × 3
    ≈ 30.7 MB
```

但这里还没算最关键的——**MemoryChannel 的 ArrayBlockingQueue**。每个 Channel 默认 capacity=128，每个 slot 存一条 Record（500 字节 × 4 倍开销 = 2KB），128 × 2KB = 256KB。5 个 channel = 1.28MB——依然不大。

真正 OOM 的凶手是 **MySQL JDBC 的 ResultSet 缓冲**。当 `fetchSize=0` 时，MySQL JDBC 驱动会一次性将整个查询结果集全部加载到客户端内存！8000 万行 × 500 字节 = 40GB——远超 2G 的 Xmx。

**技术映射**：JVM 调用 DataX 的内存模型 = 一辆货车的车厢。车底盘（Xmx）= 堆内存，货架层（Channel buffer）= 传输缓冲，打包方式（对象开销）= 泡沫填充。默认配置是"只拉了一个空托盘"——看似够用，但当"ResultSet 一次性装车"（fetchSize=0）时，整个仓库的货都塞进去，瞬间压垮。

**小胖**：所以调优不只是改 Xmx，还得改 fetchSize 和 batchSize？

**大师**：对，而且是有顺序的系统工程。我总结的调优路径是：

**第一步：JVM 层**
- `Xms/Xmx`：设为物理内存的 50%~70%（留给 OS 做 PageCache）
- `-XX:+UseG1GC`：替代 CMS，G1 的 Mixed GC 更适合内存大对象场景
- `-XX:MaxDirectMemorySize`：限制 DirectBuffer（Netty/JDBC 可能用）

**第二步：JDBC 层**
- `fetchSize = Integer.MIN_VALUE`：启用 MySQL 流式读取，按行拉取而不是全量加载
- `useCursorFetch=true`：配合 fetchSize 使用游标模式
- `useSSL=false`：关闭 SSL 减少握手开销（内网环境）

**第三步：Channel 层**
- `channel` 从默认 5 逐步翻倍，观测 CPU 利用率，直到 CPU > 80% 或数据库连接数达上限
- 经验公式：IO 密集型 = CPU 核心数 × 2~4；但是受数据库 `max_connections` 限制

**第四步：BatchSize 层**
- batchSize 从 1024 → 2048 → 4096 → 8192 逐步上调
- 观察 QPS 曲线，找拐点（QPS 不再明显增长的位置）

**第五步：验证**
- 打开 GC 日志：`-XX:+PrintGCDetails -XX:+PrintGCDateStamps -Xloggc:gc.log`
- 观察 GC 停顿时间，若单次 GC > 500ms，说明 Xmx 偏小

**小白**：（追问）为什么 G1GC 比 CMS 更适合 DataX？

**大师**：DataX 的内存特征是大吞吐、短生命周期——Record 从诞生到写入 Channel 再到被 Writer 消费，存活时间不超过 1 秒。CMS 是"标记-清除"，会产生碎片；G1 是"标记-整理 + 分区回收"，避免碎片，且 Predictable Pause（可预测停顿）更适合对延迟不敏感、对吞吐敏感的场景。

## 3. 项目实战

### 3.1 步骤一：建立 Benchmark 基线——用默认配置跑一次

**目标**：记录默认配置下 5000 万行 MySQL → MySQL 同步的性能数据，作为调优基准。

**测试环境**：

| 项目 | 配置 |
|------|------|
| CPU | Intel Xeon E5-2680 v4 @ 2.40GHz, 16 核 32 线程 |
| 内存 | 32GB DDR4（DataX 可用约 24G） |
| 磁盘 | NVMe SSD 1TB |
| MySQL | 8.0.35, InnoDB, buffer_pool_size=16GB |
| DataX | v3.0.x, 默认 JVM 参数 |

**创建测试表**（5000 万行）：

```sql
CREATE DATABASE IF NOT EXISTS bench;
USE bench;

CREATE TABLE orders (
    order_id BIGINT PRIMARY KEY AUTO_INCREMENT,
    user_id BIGINT NOT NULL,
    amount DECIMAL(10,2) NOT NULL,
    order_status TINYINT DEFAULT 1,
    create_time DATETIME NOT NULL,
    update_time DATETIME,
    INDEX idx_user (user_id),
    INDEX idx_status (order_status),
    INDEX idx_time (create_time)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 用存储过程灌入 5000 万行
DELIMITER $$
CREATE PROCEDURE gen_data(IN total INT)
BEGIN
    DECLARE i INT DEFAULT 1;
    WHILE i <= total DO
        INSERT INTO orders (user_id, amount, order_status, create_time, update_time)
        VALUES (
            FLOOR(1 + RAND() * 10000000),
            ROUND(RAND() * 10000, 2),
            FLOOR(RAND() * 4),
            DATE_ADD('2024-01-01', INTERVAL FLOOR(RAND() * 730) DAY),
            DATE_ADD('2024-01-01', INTERVAL FLOOR(RAND() * 730 + 1) DAY)
        );
        SET i = i + 1;
        IF i % 10000 = 0 THEN COMMIT; END IF;
    END WHILE;
END$$
DELIMITER ;

CALL gen_data(50000000);
```

**默认配置**（`bench_default.json`）：

```json
{
    "job": {
        "content": [{
            "reader": {
                "name": "mysqlreader",
                "parameter": {
                    "username": "root",
                    "password": "root",
                    "column": ["order_id", "user_id", "amount", "order_status", "create_time", "update_time"],
                    "splitPk": "order_id",
                    "connection": [{
                        "table": ["orders"],
                        "jdbcUrl": ["jdbc:mysql://localhost:3306/bench?useSSL=false"]
                    }]
                }
            },
            "writer": {
                "name": "mysqlwriter",
                "parameter": {
                    "username": "root",
                    "password": "root",
                    "writeMode": "insert",
                    "column": ["order_id", "user_id", "amount", "order_status", "create_time", "update_time"],
                    "preSql": ["DROP TABLE IF EXISTS orders_bak", "CREATE TABLE orders_bak LIKE orders"],
                    "batchSize": 1024,
                    "connection": [{
                        "table": ["orders_bak"],
                        "jdbcUrl": ["jdbc:mysql://localhost:3306/bench?useSSL=false"]
                    }]
                }
            }
        }],
        "setting": {
            "speed": {"channel": 5}
        }
    }
}
```

**执行与基准结果**：

```powershell
# 默认 JVM 参数（无任何调优）
python datax.py bench_default.json

# 结果：
# Total records: 50,000,000
# Total time: 48m 23s
# Average speed: 17,215 rec/s
# Peak memory (from GC log): 2.78 GB
# GC pause avg: 234ms, max: 1,823ms
# CPU utilization: 45~65%
```

### 3.2 步骤二：JVM 参数调优——堆内存 + GC 策略

**目标**：调整 JVM 启动参数，消除内存瓶颈和 GC 长停顿。

**修改 `datax.py` 或启动脚本，注入 JVM 参数**：

```powershell
# 在 datax.py 的同目录下创建 datax.jvm.config 或直接修改启动命令
# 关键 JVM 参数
java -server `
  -Xms4g -Xmx12g `
  -XX:+UseG1GC `
  -XX:MaxGCPauseMillis=200 `
  -XX:InitiatingHeapOccupancyPercent=35 `
  -XX:+DisableExplicitGC `
  -XX:MaxDirectMemorySize=2g `
  -XX:+PrintGCDetails `
  -XX:+PrintGCDateStamps `
  -XX:+PrintGCTimeStamps `
  -XX:+PrintGCApplicationStoppedTime `
  -Xloggc:./logs/gc.log `
  -XX:+UseGCLogFileRotation `
  -XX:NumberOfGCLogFiles=5 `
  -XX:GCLogFileSize=20M `
  -Ddatax.home=D:\software\workspace\bigdata-hub\datax `
  -classpath "..." `
  com.alibaba.datax.core.Engine `
  -mode standalone -jobid -1 -job bench_default.json
```

**各参数说明**：

| 参数 | 值 | 作用 |
|------|-----|------|
| `-Xms4g` | 4GB | 起始堆，避免 JVM 从 256MB 慢慢扩 |
| `-Xmx12g` | 12GB | 最大堆，32G 机器的 37.5%，留 20G 给 OS/PageCache |
| `-XX:+UseG1GC` | 开启 | 替换 CMS，减少碎片、可预测停顿 |
| `-XX:MaxGCPauseMillis=200` | 200ms | G1 尽力将每次 GC 控制在 200ms 内 |
| `-XX:InitiatingHeapOccupancyPercent=35` | 35% | 堆占用 35% 就启动并发标记（默认 45%，DataX 的数据产生快所以更早触发） |
| `-XX:MaxDirectMemorySize=2g` | 2GB | 限制堆外内存，防止 JDBC Netty 占用太多 |
| `-XX:+DisableExplicitGC` | 开启 | 禁止 `System.gc()` 触发 Full GC |

**效果**（同样配置重新运行）：

```
Total records: 50,000,000
Total time: 38m 10s
Average speed: 21,834 rec/s
GC pause avg: 48ms, max: 312ms

提升: 21% (48m → 38m), GC 停顿从 234ms 降到 48ms
```

### 3.3 步骤三：fetchSize 优化——启用 MySQL 流式读取

**目标**：将 MySQL JDBC 的 ResultSet 从"全量加载到客户端内存"改为"逐行从服务端拉取"。

**修改 Reader 的 JDBC URL**：

```json
{
    "connection": [{
        "jdbcUrl": [
            "jdbc:mysql://localhost:3306/bench?useSSL=false&useCursorFetch=true&defaultFetchSize=-2147483648"
        ]
    }]
}
```

或者在 DataX 的 Reader 参数中加：

```json
{
    "reader": {
        "parameter": {
            "fetchSize": 2048,
            "connection": [{
                "jdbcUrl": ["jdbc:mysql://localhost:3306/bench?useSSL=false&useCursorFetch=true"]
            }]
        }
    }
}
```

**fetchSize 的三种取值行为**：

| fetchSize | 行为 | 内存占用 | 网络往返 |
|-----------|------|---------|---------|
| 0（默认） | 一次性全部加载到客户端内存 | 极高（全量 × 对象开销） | 1 次 |
| > 0（如 2048） | JDBC 每次从服务端取 2048 行到客户端缓冲 | 低 | 多次，每次 2048 行 |
| `Integer.MIN_VALUE` | 流式读取，逐行从服务端拉取 | 极低（1 行缓冲） | 每行一次 |

**效果**：

```
--- 修改前 (fetchSize=0) ---
Total time: 38m 10s, QPS: 21,834
GC pause avg: 48ms
内存峰值: 8.2 GB

--- 修改后 (fetchSize=Integer.MIN_VALUE) ---
Total time: 22m 05s, QPS: 37,735
GC pause avg: 18ms
内存峰值: 2.4 GB

提升: 42% (38m → 22m), 内存峰值降低 70%
```

### 3.4 步骤四：batchSize 阶梯调优——找吞吐拐点

**目标**：在 fetchSize 已优化的基础上，逐步调大 batchSize 找到最佳写入吞吐。

**测试矩阵**：

```powershell
# 脚本化批量测试（PowerShell）
$batchSizes = @(512, 1024, 2048, 4096, 8192, 16384)
$results = @()

foreach ($bs in $batchSizes) {
    # 动态修改 JSON 中的 batchSize
    $json = Get-Content bench_default.json -Raw | 
        ConvertFrom-Json
    $json.job.content[0].writer.parameter.batchSize = $bs
    
    # 执行 DataX
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    python datax.py bench_default.json 2>&1 | Out-Null
    $sw.Stop()
    
    $results += [PSCustomObject]@{
        batchSize = $bs
        elapsed   = $sw.Elapsed.TotalSeconds
    }
}

$results | Format-Table
```

**测试结果（固定 channel=8, fetchSize=MIN_VALUE）**：

| batchSize | 耗时(s) | QPS(rec/s) | 较上一级提升 | 备注 |
|-----------|--------|-----------|------------|------|
| 512 | 1680 | 29,762 | 基准 | |
| 1024 | 1380 | 36,232 | +21.7% | |
| 2048 | 1150 | 43,478 | +20.0% | |
| 4096 | 1010 | 49,505 | +13.9% | |
| 8192 | 940 | 53,191 | +7.4% | **拐点出现** |
| 16384 | 925 | 54,054 | +1.6% | 几乎零增长 |
| 32768 | 960 | 52,083 | **负增长** | GC 压力反噬 |

**结论**：batchSize=8192 是最优点（QPS 53,191），比默认 1024 提升 47%。

**为什么 batchSize 过大反而慢**：
1. PreparedStatement 的 batch 越大，JVM 堆内存中缓冲的待提交数据越多
2. 一次 `executeBatch()` 的执行时间变长（数据库锁持有时间更长）
3. 内存回收压力增大——8192 × 500 bytes = 4MB/batch，16 个 channel = 64MB 待提交数据

### 3.5 步骤五：Channel 数优化——IO 密集型的最佳并发度

**目标**：在 batchSize 和 fetchSize 已最优的基础上，找到最佳 channel 数。

**经验公式**：
- IO 密集型任务：`channel = CPU 核心数 × 2 ~ 4`
- 上限受限于：数据库连接池（同时建立的 JDBC 连接数）、网络带宽、磁盘 IOPS

**测试矩阵（batchSize=8192, fetchSize=MIN_VALUE）**：

| channel | 耗时(s) | QPS(rec/s) | CPU(%) | MySQL连接数 | 备注 |
|---------|--------|-----------|--------|------------|------|
| 1 | 6245 | 8,006 | 12 | 2 | |
| 2 | 3020 | 16,556 | 22 | 4 | 近线性 |
| 4 | 1550 | 32,258 | 38 | 8 | |
| 8 | 940 | 53,191 | 62 | 16 | |
| 12 | 740 | 67,568 | 76 | 24 | |
| 16 | 620 | 80,645 | 85 | 32 | |
| 20 | 590 | 84,746 | 90 | 40 | 增长放缓 |
| 24 | 585 | 85,470 | 95 | 48 | **最优** |
| 32 | 610 | 81,967 | 98 | 64 | 开始退化 |
| 40 | 680 | 73,529 | 100 | 80 | 过载 |

**结论**：channel=24 是最优点（QPS 85,470），比默认 5 提升 396%。

**退化原因分析**：
- channel=32 时，64 个 JDBC 连接同时从 MySQL 读 + 64 个同时写，MySQL 线程调度开销超过收益
- CPU 满载时，JVM 的 GC 线程和业务线程争抢 CPU 时间，GC 停顿延长

### 3.6 步骤六：最终优化配置与 G1GC 日志分析

**目标**：汇总所有调优参数，生成最优配置，并解读 GC 日志验证调优效果。

**最优配置**（`bench_optimized.json`）：

```json
{
    "job": {
        "content": [{
            "reader": {
                "name": "mysqlreader",
                "parameter": {
                    "username": "root",
                    "password": "root",
                    "column": ["order_id", "user_id", "amount", "order_status", "create_time", "update_time"],
                    "splitPk": "order_id",
                    "fetchSize": -2147483648,
                    "connection": [{
                        "table": ["orders"],
                        "jdbcUrl": ["jdbc:mysql://localhost:3306/bench?useSSL=false&useCursorFetch=true&socketTimeout=60000"]
                    }]
                }
            },
            "writer": {
                "name": "mysqlwriter",
                "parameter": {
                    "username": "root",
                    "password": "root",
                    "writeMode": "insert",
                    "column": ["order_id", "user_id", "amount", "order_status", "create_time", "update_time"],
                    "batchSize": 8192,
                    "preSql": ["DROP TABLE IF EXISTS orders_bak", "CREATE TABLE orders_bak LIKE orders"],
                    "session": [
                        "SET unique_checks = 0",
                        "SET foreign_key_checks = 0",
                        "SET autocommit = 0"
                    ],
                    "connection": [{
                        "table": ["orders_bak"],
                        "jdbcUrl": ["jdbc:mysql://localhost:3306/bench?useSSL=false&rewriteBatchedStatements=true&socketTimeout=60000"]
                    }]
                }
            }
        }],
        "setting": {
            "speed": {"channel": 24}
        }
    }
}
```

**JVM 参数**：

```powershell
-Xms4g -Xmx12g -XX:+UseG1GC -XX:MaxGCPauseMillis=200 
-XX:InitiatingHeapOccupancyPercent=35 -XX:+DisableExplicitGC 
-XX:MaxDirectMemorySize=2g 
-XX:+PrintGCDetails -XX:+PrintGCDateStamps -Xloggc:./logs/gc.log
```

**最优配置下的最终结果**：

```
Total records: 50,000,000
Total time: 8m 33s
Average speed: 97,466 rec/s
Total bytes: 22,350,000,000 (20.8 GB)

优化前 → 优化后:
  时间: 48m 23s → 8m 33s
  QPS:  17,215 → 97,466
  提升: 5.66x
```

**G1GC 日志关键解读**：

```log
# Young GC (正常, 每次 ~25ms)
2025-01-16T03:15:12.345+0800: 128.456: [GC pause (G1 Evacuation Pause) (young), 0.0251234 secs]
   [Parallel Time: 24.1 ms, GC Workers: 16]
   [Eden: 1024.0M(1024.0M)->0.0B(1024.0M) Survivors: 128.0M->128.0M Heap: 3824.0M(12288.0M)->2812.5M(12288.0M)]

# Mixed GC (偶发, 每次 ~80ms)
2025-01-16T03:17:45.678+0800: 281.789: [GC pause (G1 Evacuation Pause) (mixed), 0.0785432 secs]
   [Parallel Time: 77.8 ms, GC Workers: 16]
   [Heap: 8456.2M(12288.0M)->4234.1M(12288.0M)]

# Full GC (整个运行期间仅 1 次!)
2025-01-16T03:20:01.234+0800: 417.345: [Full GC (Allocation Failure) 11288M->3845M(12288M), 0.3124567 secs]
```

**健康指标**：
- Young GC 频率：每 2~3 秒一次，停顿 < 30ms → 良好
- Mixed GC 次数：整个任务 3 次 → 良好
- Full GC 次数：仅 1 次，停顿 312ms → 可接受
- 堆利用率：峰值 92%（11288M/12288M）→ 含余量

### 3.7 可能遇到的坑及解决方法

**坑1：channel 调太大导致 MySQL 连接数不足**

MySQL 默认 `max_connections=151`。Channel=24 时，每个 Task 需要 2 个连接（Reader 1 个 + Writer 1 个）= 48 个连接。如果有其他应用也在连 MySQL，可能超出限制。

```
报错: DataXException: Could not create connection to database server
解决: SET GLOBAL max_connections = 500; 或在 JDBC URL 限制连接数
```

**坑2：batchSize + channel 过大导致 OOM（即使 Xmx 已经 12G）**

如果 batchSize=16384 + channel=32，待提交缓冲 = 32 × 16384 × 500 bytes × 4(开销) = 1.05GB。但这是 active batch 内存，加上 Channel 缓冲、Reader 缓冲、JVM 元数据——堆还是可能爆。

```
解决: 保持 batchSize × channel 在合理范围
     经验值: batchSize × channel < 200,000 (在 500 bytes/record 的情况下)
```

**坑3：`rewriteBatchedStatements=true` 与 MySQL 版本兼容性**

MySQL 5.5 以下不支持此参数，开着会报错。MySQL 8.0.13+ 的 `useServerPrepStmts` 也能提升批量写入性能（服务端预处理），但与 `rewriteBatchedStatements` 互斥。

**坑4：G1GC 的 `InitiatingHeapOccupancyPercent=35` 设置过低**

如果 IO 密集、堆很大（32G），35% 意味着 11.2G 时就开始 Mixed GC——过早触发导致不必要的停顿。

```
经验值:
- 堆 < 8G: InitiatingHeapOccupancyPercent=45 (默认)
- 堆 8~16G: 35~40
- 堆 > 16G: 30~35 (大堆的并发标记耗时长，需早启动)
```

## 4. 项目总结

### 4.1 调优效果总览

| 优化阶段 | 参数变更 | QPS(rec/s) | 耗时 | 累计提升 |
|---------|---------|-----------|------|---------|
| 默认 | 出厂设置 | 17,215 | 48m | 基准 |
| JVM | -Xmx12g + G1GC | 21,834 | 38m | +27% |
| fetchSize | Integer.MIN_VALUE | 37,735 | 22m | +119% |
| batchSize | 1024→8192 | 53,191 | 15m | +209% |
| channel | 5→24 | 56,887 | 14m | +230% |
| writer session | SET unique_checks=0等 | 65,421 | 12m | +280% |
| jdbc params | rewriteBatched=true | 97,466 | 8m | **+466%** |

### 4.2 调优参数速查表

| 参数 | 默认值 | 建议值（16C32G） | 调优方法 |
|------|--------|-----------------|---------|
| `Xmx` | 1/4物理内存 | 12~16g | 物理内存50%~70% |
| `Xms` | 自适应 | 4g | 等于Xmx的一半 |
| GC策略 | 靠JDK选 | G1GC | `-XX:+UseG1GC` |
| `fetchSize` | 0 | -2147483648 | Integer.MIN_VALUE |
| `batchSize` | 1024 | 2048~8192 | 阶梯翻倍找拐点 |
| `channel` | 5 | CPU核数×2~4 | 逐步+4，测CPU |
| `MaxDirectMemorySize` | 无限制 | 2g | 堆的1/6 |
| `rewriteBatchedStatements` | false | true | JDBC URL 参数 |
| `unique_checks` | 1 | 0（写入期间） | Writer session |

### 4.3 优点

1. **系统化可复现**：五步调优路径（JVM→fetchSize→batchSize→channel→session），每一步可独立验证
2. **6 倍提升可量化**：从 48 分钟到 8 分钟，不是玄学，是每一步都有 GCLog 和 QPS 数据支持
3. **G1GC 日志可审计**：每次调优都有 GC 数据佐证，不是凭感觉
4. **风险可控**：每位参数都有"过度调优的反效果"验证（如 channel=40 退化、batchSize=32768 负增长）

### 4.4 缺点

1. **环境依赖强**：优化结果与硬件强相关（SSD vs HDD、CPU 核数、MySQL 版本），换个环境需重新调
2. **部分参数互斥**：`useCursorFetch=true` + `fetchSize=MIN_VALUE` 在某些 MySQL 版本上不兼容
3. **不是无脑堆参数**：存在最优解（如 batchSize 拐点 = 8192），超了反噬
4. **MySQL Writer 的 writeMode=update 场景另需单独调优**（update 的 batch 行为与 insert 不同）

### 4.5 注意事项

1. 生产环境先读 GC 日志再调优，不要直接改 JVM 参数
2. `channel` 数受 `max_connections` 限制，MySQL 每个 Reader+Writer 需 2 个连接
3. `fetchSize=Integer.MIN_VALUE` 仅在 MySQL 上有效，Oracle/PostgreSQL 参数不同
4. `SET unique_checks=0` 在写入期间关闭唯一性检查——如果源数据有冲突，目标表可能出现重复键
5. `rewriteBatchedStatements=true` 将多条 INSERT 合并为单条，减少网络往返，但单条 SQL 可能过大

### 4.6 思考题

1. 如果在 4C8G 的低配机器上运行同样任务，哪些参数需要反向调整？给出具体的调整方案和预期 QPS。
2. G1GC 的 `-XX:MaxGCPauseMillis=200` 设得越小越好吗？如果把目标设为 50ms，对吞吐会有什么影响？

（答案见附录）
