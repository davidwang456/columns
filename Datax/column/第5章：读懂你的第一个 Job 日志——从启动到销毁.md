# 第5章：读懂你的第一个 Job 日志——从启动到销毁

## 1. 项目背景

凌晨 3 点，运维工程师老李被告警电话惊醒——生产环境的 DataX 任务失败了。老李登录服务器，打开任务日志，面对 500+ 行的输出瞬间头大：

```
[main] INFO  Engine -
[main] INFO  JobContainer - jobContainer starts job.
[main] INFO  JobContainer - job [preCheck] phase starts.
[main] INFO  JobContainer - job [preCheck] phase ends.
...
[taskGroup-0] ERROR CommonRdbmsWriter$Task - Exception when batch insert:
java.sql.BatchUpdateException: Data too long for column 'remark' at row 1
```

老李凭经验知道这是某个 `remark` 字段超长，但他需要找出是哪个 Task 的哪一行、源端数据是什么、为什么超长。在海量日志中精确定位问题，靠的是对 DataX 日志格式和生命周期的深刻理解。

DataX 的日志不是"一团乱麻"，而是严格按照 9 步生命周期输出的结构化信息流。每一条日志都有其特定的位置和含义。本章用一次 100 万行的真实 MySQL → MySQL 同步任务，带你逐行解读从 Engine 启动到 Task 销毁的完整日志链路，形成一份可以贴在显示器旁的《DataX 日志速查卡》。

## 2. 项目设计——剧本式交锋对话

**（运维监控室，大屏幕上闪烁着红色告警）**

**小胖**：（急急忙忙冲进来）老李，半夜那个任务又挂了？怎么回事啊？

**老李**：日志太长了，500 多行，我还在找。你看这——"脏数据超过限制"，但到底是哪条数据脏了？

**小胖**：（凑近屏幕）这日志跟天书一样，密密麻麻的。要是能像看电视剧一样，知道"现在是第一集，讲的是 preCheck 阶段"就好了。

**小白**：（不紧不慢地打开自己的笔记本）其实 DataX 的日志是严格按照生命周期输出的。你只要记住 9 个阶段——preCheck、preHandle、init、prepare、split、schedule、post、postHandle、destroy——每个阶段都在日志里有清晰的开头和结尾。

**大师**：（放下手里的咖啡杯）小白说得对。老李你看，日志里有这个规律：

1. `job [preCheck] phase starts.` 和 `job [preCheck] phase ends.` 之间的内容，就是 preCheck 阶段在做的事——主要是校验 JSON 配置的合法性。
2. `job [init] phase starts.` 和 `job [init] phase ends.` 之间，是插件加载的过程。如果你看到 `reader name: [mysqlreader]` 这行，说明插件加载成功了。
3. 最关键的 `job [split] phase` 会输出 Task 数量——"Reader split success, total task num: 20"，你要记下来。
4. `job [schedule] phase` 就是 Task 并发执行的阶段，最长的也是这段。你看 `taskId=0` 到 `taskId=19` 的日志交替出现，说明 20 个 Task 在并发跑。

**技术映射**：DataX 生命周期日志 = 航班起飞流程。preCheck = 安检（查 JSON 是否合法），init = 登机（加载插件），split = 分配座位（切分 Task），schedule = 飞行中（执行数据同步），post = 降落（收尾清理）。

**老李**：那具体每个 Task 的日志怎么看？我现在只知道任务失败了，但不知道哪个 Task 失败的。

**大师**：看 Task 日志的关键字——每个 `[taskGroup-X]` 前缀对应一个 TaskGroup，每个 `taskId=Y` 对应一个具体的 Task。报错的那行是 `[taskGroup-0] ERROR CommonRdbmsWriter$Task`，意味着 TaskGroup 0 中的某个 Writer 线程出错了。往前翻 3-5 行，你能看到它在处理哪些数据。

**小白**：（补充道）还有任务结束时的统计摘要，藏在 `[schedule] phase ends.` 前面：

```
任务启动时刻     : 2026-05-06 03:00:00
任务结束时刻     : 2026-05-06 03:12:30
任务总计耗时     : 720s
任务平均流量     : 1.38MB/s
记录写入速度     : 1389rec/s
读出记录总数     : 1000000
读写失败总数     : 0
```

这 7 行就是任务的"体检报告"。`平均流量 < speed.byte`、`记录写入速度 < speed.record` 说明限速在生效。`读写失败总数 > 0` 表示有脏数据。

**小胖**：（突然灵光一闪）哎，那是不是可以写个 Python 脚本，自动解析日志，把失败 Task 的堆栈和上下文提取出来？

**大师**：非常好的想法。生产环境推荐这么做——用 Filebeat 采集 DataX 日志到 ELK，通过 Logstash 的 Grok 模式提取关键字段，然后在 Kibana 里直接搜索 error 关键字。这个在第 28 章会详细讲。

## 3. 项目实战

### 3.1 环境准备

- MySQL 8.0 实例 ×2（源端 + 目标端）
- DataX 编译完成（参考第 2 章）
- 测试数据生成脚本

### 3.2 步骤一：生成 100 万行测试数据

**目标**：在源端 MySQL 中创建一张有 100 万行的表，模拟真实同步场景。

```sql
-- 源端 MySQL: 创建测试表
CREATE TABLE source_orders (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    user_name VARCHAR(50),
    amount DECIMAL(10,2),
    status TINYINT,
    remark VARCHAR(200),
    create_time DATETIME DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB;

-- 用存储过程插入 100 万行
DELIMITER $$
CREATE PROCEDURE generate_orders(IN num INT)
BEGIN
    DECLARE i INT DEFAULT 1;
    WHILE i <= num DO
        INSERT INTO source_orders (user_name, amount, status, remark, create_time)
        VALUES (
            CONCAT('user_', FLOOR(RAND() * 10000)),
            ROUND(RAND() * 10000, 2),
            FLOOR(RAND() * 5),
            CONCAT('订单备注-', i),
            DATE_ADD('2026-01-01', INTERVAL FLOOR(RAND() * 155) DAY)
        );
        SET i = i + 1;
    END WHILE;
END$$
DELIMITER ;

CALL generate_orders(1000000);
```

目标端创建相同的表结构：

```sql
CREATE TABLE target_orders LIKE source_orders;
```

### 3.3 步骤二：编写同步 Job 配置

**目标**：配置一个完整的 MySQL → MySQL 同步任务。

```json
{
    "job": {
        "content": [{
            "reader": {
                "name": "mysqlreader",
                "parameter": {
                    "username": "root",
                    "password": "root123",
                    "column": ["id", "user_name", "amount", "status", "remark", "create_time"],
                    "splitPk": "id",
                    "connection": [{
                        "table": ["source_orders"],
                        "jdbcUrl": ["jdbc:mysql://localhost:3306/source_db"]
                    }]
                }
            },
            "writer": {
                "name": "mysqlwriter",
                "parameter": {
                    "username": "root",
                    "password": "root123",
                    "column": ["id", "user_name", "amount", "status", "remark", "create_time"],
                    "writeMode": "insert",
                    "preSql": ["TRUNCATE TABLE target_orders"],
                    "batchSize": 2048,
                    "connection": [{
                        "table": ["target_orders"],
                        "jdbcUrl": ["jdbc:mysql://localhost:3306/target_db"]
                    }]
                }
            }
        }],
        "setting": {
            "speed": {
                "channel": 10
            },
            "errorLimit": {
                "record": 0,
                "percentage": 0
            }
        }
    }
}
```

### 3.4 步骤三：运行任务并逐段解读日志

```bash
python bin/datax.py D:/tmp/jobs/mysql2mysql.json
```

---

**第 0 段：Engine 启动（3-5 行）**

```
2026-05-06 15:00:00.001 [main] INFO  Engine - the job params is :
{... 打印完整的 JSON 配置 ...}
```

关键信息：打印了解析后的 Configuration 对象完整内容。如果打印出来的配置和你的 JSON 不一样，说明 ConfigParser 做了某些默认值填充或类型转换。

---

**第 1 段：preCheck 阶段（3-5 行）**

```
2026-05-06 15:00:00.100 [main] INFO  JobContainer - jobContainer starts job.
2026-05-06 15:00:00.200 [main] INFO  JobContainer - reader name: [mysqlreader]
2026-05-06 15:00:00.200 [main] INFO  JobContainer - writer name: [mysqlwriter]
2026-05-06 15:00:00.300 [main] INFO  JobContainer - job [preCheck] phase starts.
2026-05-06 15:00:00.400 [main] INFO  JobContainer - job [preCheck] phase ends.
```

preCheck 阶段做了什么：
1. 验证 `channel` 参数 > 0
2. 验证 `reader.name` 和 `writer.name` 非空
3. 验证 content 数组非空
4. 分发 preCheck 到 Reader 和 Writer 插件（如验证 JDBC 连接是否可达）

如果 preCheck 失败，日志会显示：
```
ERROR JobContainer - 作业配置校验失败
Caused by: java.lang.IllegalArgumentException: speed.channel must > 0
```

---

**第 2 段：init 阶段（10-20 行）**

```
2026-05-06 15:00:00.500 [main] INFO  JobContainer - job [init] phase starts.
2026-05-06 15:00:01.100 [main] INFO  LoadUtil - load plugin: [mysqlreader]
2026-05-06 15:00:01.200 [main] INFO  LoadUtil - load plugin: [mysqlwriter]
2026-05-06 15:00:02.000 [main] INFO  JobContainer - job [init] phase ends.
```

init 阶段的核心操作：LoadUtil 扫描 `plugin/reader/mysqlreader/` 目录，读取 `plugin.json`，反射实例化 `MysqlReader` 类，并通过 ClassLoaderSwapper 设置隔离的类加载器。

如果看到 `ClassNotFoundException`，意味着 plugin.json 中声明的类名与实际 JAR 中的类不匹配。

---

**第 3 段：prepare 阶段（2-5 行）**

```
2026-05-06 15:00:02.100 [main] INFO  JobContainer - job [prepare] phase starts.
2026-05-06 15:00:02.200 [main] INFO  JobContainer - job [prepare] phase ends.
```

prepare 阶段主要执行全局准备工作：
- Writer 执行 preSql（如 `TRUNCATE TABLE target_orders`）
- Reader 执行 prepare（如建立连接池）
- 如果 preSql 中有 DDL 语句失败，这里会第一时间报错

---

**第 4 段：split 阶段（核心，10-20 行）**

```
2026-05-06 15:00:02.300 [main] INFO  JobContainer - job [split] phase starts.
2026-05-06 15:00:03.400 [main] INFO  MysqlReader$Job - split starts, adviceNumber: 10
2026-05-06 15:00:03.500 [main] INFO  MysqlReader$Job - query MIN(id)=1, MAX(id)=1000000
2026-05-06 15:00:03.600 [main] INFO  MysqlReader$Job - split into 20 slices. (每个slice约50000行)
2026-05-06 15:00:03.800 [main] INFO  JobContainer - merge reader/writer tasks: total 20 tasks.
2026-05-06 15:00:03.900 [main] INFO  JobContainer - assign 20 tasks to 2 taskGroups.
2026-05-06 15:00:03.950 [main] INFO  JobAssignUtil - taskGroup[0]: 10 tasks, taskGroup[1]: 10 tasks
2026-05-06 15:00:03.999 [main] INFO  JobContainer - job [split] phase ends.
```

split 阶段是最重要的日志段，包含关键决策信息：

1. **adviceNumber=10**：Engine 建议切分为 10 个 Task
2. **实际切分为 20 个**：Reader 的 split() 方法可以在 adviceNumber 基础上自行调整（通常是整数倍）
3. **taskGroup 分配**：20 个 Task 均分到 2 个 TaskGroup，每组 10 个

调优提示：如果 `split into X slices` 显示某个 slice 的数据行数远大于其他 slice，说明存在数据倾斜。

---

**第 5 段：schedule 阶段（最大段，数百行）**

```
2026-05-06 15:00:04.000 [main] INFO  JobContainer - job [schedule] phase starts.

2026-05-06 15:00:04.100 [taskGroup-0] INFO  TaskGroupContainer - start taskGroup[0], channel=10
2026-05-06 15:00:04.100 [taskGroup-1] INFO  TaskGroupContainer - start taskGroup[1], channel=10

2026-05-06 15:00:04.300 [taskGroup-0] INFO  TaskGroupContainer - taskId=0 start, 
    reader=[mysqlreader, ip=127.0.0.1, jdbcUrl=jdbc:mysql://...], 
    writer=[mysqlwriter, ip=127.0.0.1, jdbcUrl=jdbc:mysql://...]
2026-05-06 15:00:04.350 [taskGroup-0] INFO  TaskGroupContainer - taskId=1 start, ...
...（taskId=0~9 在 taskGroup-0 中逐个启动）
...（taskId=10~19 在 taskGroup-1 中逐个启动）

2026-05-06 15:01:00.000 [taskGroup-0] INFO  ReaderRunner - taskId=3 read 52480 records, 13.2MB, cost 55s
2026-05-06 15:01:00.100 [taskGroup-0] INFO  WriterRunner - taskId=3 write 52480 records, 13.2MB, cost 56s

2026-05-06 15:02:00.000 [taskGroup-1] INFO  ReaderRunner - taskId=15 read 50120 records, 12.8MB, cost 115s
2026-05-06 15:02:00.100 [taskGroup-1] INFO  WriterRunner - taskId=15 write 50120 records, 12.8MB, cost 116s
...

2026-05-06 15:02:30.000 [main] INFO  JobContainer - 
    Percentage of schedule:  100%|████████████| 20/20 tasks completed.
```

Task 日志解读要点：
- `[taskGroup-X]` 标识哪个 TaskGroup 在输出日志
- `cost` 表示该 Task 实际耗时，偏差大的 Task 可能是数据倾斜
- 如果所有 Task 的 `cost` 都在一个相近范围（如 50-60s），说明分片均匀

---

**第 6 段：统计摘要（最关键的 7 行）**

```
2026-05-06 15:02:30.500 [main] INFO  JobContainer -
任务启动时刻     : 2026-05-06 15:00:04
任务结束时刻     : 2026-05-06 15:02:30
任务总计耗时     : 146s
任务平均流量     : 1.82MB/s
记录写入速度     : 6849rec/s
读出记录总数     : 1000000
读写失败总数     : 0
```

**公式推导**：
- 平均流量 = 总字节数 ÷ 耗时 = 266MB ÷ 146s ≈ 1.82MB/s
- 记录写入速度 = 1000000 ÷ 146s ≈ 6849rec/s
- 每个 Channel 的平均 QPS = 6849 ÷ 10 = 684.9 rec/s

---

**第 7 段：post & destroy（3-8 行）**

```
2026-05-06 15:02:30.600 [main] INFO  JobContainer - job [schedule] phase ends.
2026-05-06 15:02:30.700 [main] INFO  JobContainer - job [post] phase starts.
2026-05-06 15:02:30.800 [main] INFO  JobContainer - execute postSql: []
2026-05-06 15:02:30.900 [main] INFO  JobContainer - job [post] phase ends.
2026-05-06 15:02:31.000 [main] INFO  JobContainer - job [postHandle] phase starts.
2026-05-06 15:02:31.100 [main] INFO  JobContainer - job [postHandle] phase ends.
2026-05-06 15:02:31.200 [main] INFO  JobContainer - job [destroy] phase starts.
2026-05-06 15:02:31.300 [main] INFO  JobContainer - job [destroy] phase ends.
2026-05-06 15:02:31.400 [main] INFO  Engine - job completed. exit code: 0
```

最后一行 `exit code: 0` 表示任务成功。如果失败，`exit code` 会是 1 或其他非 0 值。

### 3.5 步骤四：Logback 日志配置调优

**目标**：自定义 DataX 的日志输出行为。

DataX 使用 Logback 作为日志框架，配置文件位于 `conf/logback.xml`：

```xml
<configuration>
    <!-- 控制台输出 -->
    <appender name="STDOUT" class="ch.qos.logback.core.ConsoleAppender">
        <encoder>
            <pattern>%d{yyyy-MM-dd HH:mm:ss.SSS} [%thread] %-5level %logger{36} - %msg%n</pattern>
        </encoder>
    </appender>

    <!-- 文件输出（按天滚动，保留7天） -->
    <appender name="FILE" class="ch.qos.logback.core.rolling.RollingFileAppender">
        <file>log/datax.log</file>
        <rollingPolicy class="ch.qos.logback.core.rolling.TimeBasedRollingPolicy">
            <fileNamePattern>log/datax.%d{yyyy-MM-dd}.log</fileNamePattern>
            <maxHistory>7</maxHistory>
        </rollingPolicy>
        <encoder>
            <pattern>%d{yyyy-MM-dd HH:mm:ss.SSS} [%thread] %-5level %logger{36} - %msg%n</pattern>
        </encoder>
    </appender>

    <!-- 框架日志级别 -->
    <logger name="com.alibaba.datax" level="INFO"/>

    <!-- 插件日志级别（调成 DEBUG 可看 SQL 执行细节） -->
    <logger name="com.alibaba.datax.plugin" level="INFO"/>

    <root level="INFO">
        <appender-ref ref="STDOUT"/>
        <appender-ref ref="FILE"/>
    </root>
</configuration>
```

**调试模式**：将 `com.alibaba.datax.plugin` 的 level 改为 `DEBUG`，可以看到每条 SQL 的完整内容：

```
[DEBUG] CommonRdbmsReader$Task - execute sql: SELECT * FROM source_orders WHERE id >= 1 AND id < 50001
```

### 3.6 步骤五：常见异常日志模式

**模式 1：脏数据超限**

```
ERROR AbstractTaskPluginCollector - 脏数据: 
{"record":[{"columnName":"amount","type":"DOUBLE","value":"abc"}],
"exception":"java.lang.NumberFormatException: For input string: \"abc\"",
"errorMsg":"无法将值[abc]转换为DoubleColumn"}
...
ERROR JobContainer - 脏数据记录数[1]超过限制[0]
```

**定位方法**：向上翻日志找 `columnName` 和 `value`，确定是哪一列、什么值导致的。通常需要去源端检查数据质量。

**模式 2：OOM**

```
Exception in thread "taskGroup-0" java.lang.OutOfMemoryError: Java heap space
        at com.alibaba.datax.core.transport.channel.MemoryChannel.push(MemoryChannel.java:45)
```

**定位方法**：查看 `MemoryChannel` 的 capacity（默认 128）和当前 Record 大小。计算：`channel × capacity × recordSize × 2` 是否超过 `-Xmx`。

**模式 3：连接超时**

```
ERROR CommonRdbmsReader$Job - 连接数据库失败
Caused by: com.mysql.cj.jdbc.exceptions.CommunicationsException: Communications link failure
The last packet sent successfully to the server was 0 milliseconds ago.
```

**定位方法**：检查防火墙策略、MySQL `wait_timeout` 参数、JDBC URL 中的 `connectTimeout`。

### 3.7 可能遇到的坑及解决方法

**坑1：日志量爆炸**

100 万行数据 + channel=10 + batchSize=1024 会产生数千行日志。

解决：将 `com.alibaba.datax.plugin` 日志级别保持 INFO，仅在排查问题时改为 DEBUG。

**坑2：多线程日志交织**

TaskGroup 的日志是交错输出的，难以追踪单个 Task 的完整日志。

解决：用 grep 过滤特定 taskId：
```bash
grep "taskId=3" datax.log
```

**坑3：日志中文乱码**

解决：在 `logback.xml` 的 encoder 中指定 UTF-8 编码。或运行时添加 JVM 参数 `-Dfile.encoding=UTF-8`。

## 4. 项目总结

### 4.1 DataX 日志速查卡

| 日志关键字 | 所属阶段 | 含义 |
|-----------|---------|------|
| `job params is :` | Engine | 打印完整 JSON 配置 |
| `reader name:` / `writer name:` | preCheck | 确认加载的插件 |
| `preCheck phase starts` | preCheck | 开始配置校验 |
| `load plugin:` | init | 加载插件 Class |
| `split starts, adviceNumber:` | split | 开始 Task 切分 |
| `MIN(id)=` / `MAX(id)=` | split | 主键范围 |
| `split into X slices` | split | 切分结果（Task 总数） |
| `assign X tasks to Y taskGroups` | split | TaskGroup 分配结果 |
| `start taskGroup[X], channel=Y` | schedule | TaskGroup 启动 |
| `taskId=X start` | schedule | Task 开始执行 |
| `read X records, X.XMB, cost Xs` | schedule | Reader 完成统计 |
| `write X records, X.XMB, cost Xs` | schedule | Writer 完成统计 |
| `任务总计耗时` | schedule | 整体耗时 |
| `任务平均流量` | schedule | 平均字节速度 |
| `记录写入速度` | schedule | 平均记录速度 |
| `读写失败总数` | schedule | 脏记录总数 |
| `execute postSql` | post | 执行 postSql |
| `job completed. exit code: 0` | Engine | 成功结束 |

### 4.2 优点

1. **结构化清晰**：9 步生命周期严格对应日志段，出问题能快速定位阶段
2. **统计摘要完整**：7 行摘要信息包含了性能评估所需的所有关键指标
3. **打印完整配置**：启动时打印 Configuration 对象，方便追溯"实际用了什么参数"
4. **Task 级别统计**：每个 Task 完成后有 read/write 统计，能看出哪个 Task 慢
5. **Logback 可配置**：支持文件滚动、日志级别动态调整

### 4.3 缺点

1. **无 TraceID**：默认 没有全链路 TraceID，跨 Task 追踪困难
2. **线程日志交织**：多 Task 并行执行时日志交叠，难读
3. **堆栈信息冗余**：异常日志打印完整堆栈，但通常前 3 行就够
4. **无 JSON 格式输出**：默认纯文本，不便于 Logstash 解析
5. **脏数据日志不完整**：只打印前 10 条脏数据，超出部分被截断

### 4.4 日志故障排查 SOP

1. 看到 `exit code: 1` → 搜索 `ERROR` 关键字
2. 看到 `ClassNotFoundException` → 检查 plugin.json 和 JAR 一致性
3. 看到 `脏数据超过限制` → 向上翻找具体的 column + value
4. 看到 `OutOfMemoryError` → 降低 channel × capacity × batchSize
5. 看到 `Communications link failure` → 检查防火墙和 MySQL wait_timeout

### 4.5 思考题

1. DataX 的 Task 日志中 `cost` 是从哪个时刻开始计时的？它和 `任务总计耗时` 的时间差反映了什么？
2. 如果 channel=10，但只有一个 TaskGroup（`assignFairly` 后只有 1 个 TaskGroup），这说明了什么？（提示：查看 Task 总数和 channel 数的关系）

（答案见附录）
