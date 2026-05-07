# 第8章：Channel 通道——并发数的秘密

## 1. 项目背景

数据团队接手了一个"祖传"DataX 任务：从 MySQL 同步 5000 万行订单数据到另一个 MySQL 实例，当前配置 `channel=1`，每次执行需要 3 小时。TL 让新人小李优化这个任务——"把 channel 调到 20，应该能 10 分钟内完成。"

小李照做，把 `channel` 从 1 改成了 20。满怀期待地点击执行——1 分钟后任务挂了，报错：`Too many connections`。数据库连接池满载，20 个并发通道各占 2 个连接（Reader + Writer），瞬间打了 40 个连接上去，远超源库的 `max_connections=50`（还有其他业务在使用）。

小李把 channel 调回 5，任务顺利在 40 分钟完成。但他纳闷——为什么不是 channel=1 的 1/5 时间（3 小时 ÷ 5 = 36 分钟），而实际是 40 分钟？他去问 TL，TL 说：因为 Channel 数量 ≠ 实际并发度，Task 切分数量、TaskGroup 数量、硬件资源、数据库连接池上限都会影响最终执行效率。Channel 是 DataX 并行的入口，但不是"调大就快"的银弹。

## 2. 项目设计——剧本式交锋对话

**（办公室角落，小李盯着监控面板发呆）**

**小胖**：（端着泡面路过）小李，你这任务怎么还在跑？不是说 10 分钟就完事吗？

**小李**：（叹气）我把 channel 调到 20 直接 OOM 了，降到 5 才稳定。但我不明白——channel=5 理论上是 5 倍速，实际只快了 4.5 倍。另外 0.5 被谁吃掉了？

**小白**：（放下《并发编程实战》）因为 Channel 只是"允许同时跑 5 个 Task"，而不是"让 1 个 Task 跑 5 倍快"。你看看 TaskGroupContainer 的源码——每个 Task 由一个 TaskExecutor 承载，TaskExecutor 内部是一个 Reader 线程 + 一个 Channel + 一个 Writer 线程。

**大师**：（画了一个架构图）来，我从头讲。DataX 的并行有三层漏斗：

```
第一层：Job.split() → Task 总数（比如 50 个）
     ↓
第二层：TaskGroupContainer × N → 每个 TaskGroup 有 channel 个并发 slot
     ↓
第三层：TaskExecutor × channel → 每个 slot 跑一个 Task（Reader线程 + Writer线程）
```

关键限制在第三层——一个 TaskGroup 内 channel=5，意味着同时最多有 5 个 Reader 线程和 5 个 Writer 线程在读写数据库。但 CPU 只有 8 核，5 个线程争抢 8 核，没问题；20 个线程争抢 8 核，上下文切换的开销可能比实际计算还大。

**技术映射**：Channel 三层漏斗 = 餐厅后厨。Job 是当天总订单（50 道菜），TaskGroup 是几个灶台（每个灶台 5 个火眼 = channel），TaskExecutor 就是每个火眼上的锅。火眼太多（channel=20），厨师（CPU）忙不过来，反而出餐更慢。

**小胖**：（吸溜一口面）所以 channel 不是越大越好？那最优值怎么定？

**大师**：有个经验公式：

```
最优 channel = min(CPU核心数 × 2, Task总数 / 2, 数据库空闲连接数 / 2)
```

三层约束分别对应：CPU 算力、任务粒度、数据库资源。任何一层成为瓶颈，channel 再大也没用。

**小白**：（在纸上演算）小李你有 8 核 CPU，channel=5≤8×2=16，OK。但源库 max_connections=50，平时已有 10 个业务连接，剩余 40 个空连接。channel=5 时，Reader 5 个 + Writer 5 个 + Druid 预留池 5 个 = 15 个，完全够。channel=20 时，40 个 + 5 个 = 45 个，也还够啊——但 OOM 不是连接数的问题，是内存问题！

**小李**：（恍然大悟）对啊，OOM 是因为 20 个 Channel 每个都有 `ArrayBlockingQueue(128)`，里面存着 128 条大 Record，20×128×500KB ≈ 1.2GB，加上 Reader 的 buffer 和 Writer 的 batch，总共快 2GB 了。而 JVM 默认 Xmx 就 1GB。

**大师**：（赞许地看着小李）你已经开始懂得从系统资源角度分析 Channel 了。下一步，你要学会看 `taskGroup` 日志中的 `cost` 字段——如果各 Task 的 cost 差别很大（比如有的 3 秒、有的 30 秒），说明数据倾斜了，channel 调再大也没用。

## 3. 项目实战

### 3.1 步骤一：理解 Channel 的三级切分模型

**目标**：通过源码追踪从 JSON 配置到实际线程执行的完整链路。

**第 1 步**：JSON 中的 `channel` 参数

```json
{
    "setting": {
        "speed": {
            "channel": 5
        }
    }
}
```

**第 2 步**：JobContainer 读取 channel 并传给 split()

```java
// JobContainer.java
int needChannelNumber = configuration.getInt("job.setting.speed.channel", 1);
List<Configuration> readerTaskConfigs = readerJob.split(needChannelNumber);
List<Configuration> writerTaskConfigs = writerJob.split(needChannelNumber);
```

split() 的参数 `needChannelNumber` 即来自 `speed.channel`。Reader 和 Writer 分别根据这个建议数切分 Task。

**第 3 步**：合并 Reader-Writer Task 配置

```java
List<Configuration> mergedTaskConfigs = mergeReaderAndWriterTaskConfigs(
    readerTaskConfigs, writerTaskConfigs);
// 假设 Reader 切了 15 个 Task，Writer 也切了 15 个
// 合并后按 1:1 配对得到 15 个 Task 配置（取任务数多的那个）
```

**第 4 步**：计算 TaskGroup 数量

```java
int taskGroupNumber = (int) Math.ceil((double) mergedTaskConfigs.size() / needChannelNumber);
// 15 个 Task ÷ 5 个 channel = 3 个 TaskGroup
```

**第 5 步**：assignFairly() 将 Task 分配到 TaskGroup

```java
List<Configuration> taskGroupConfigs = JobAssignUtil.assignFairly(
    mergedTaskConfigs, taskGroupNumber);
// 结果：
//   TaskGroup[0]: Task[0,1,2,3,4]   (5个)
//   TaskGroup[1]: Task[5,6,7,8,9]  (5个)
//   TaskGroup[2]: Task[10,11,12,13,14] (5个)
```

**第 6 步**：每个 TaskGroupContainer 启动 channel 个并发 TaskExecutor

```java
// TaskGroupContainer.java
for (int i = 0; i < channelNumber; i++) {
    Configuration taskConfig = pendingTasks.poll(); // 取出下一个待执行Task
    TaskExecutor executor = new TaskExecutor(taskConfig, i);
    executor.start(); // 启动 Reader线程 + Writer线程
}
```

### 3.2 步骤二：手写一个 Channel 压力测试

**目标**：用 streamreader + streamwriter 验证不同 channel 数的实际吞吐量差异。

```json
{
    "job": {
        "content": [{
            "reader": {
                "name": "streamreader",
                "parameter": {
                    "column": [
                        {"type": "string", "random": "10,100"},
                        {"type": "long", "random": "1, 9999999"}
                    ],
                    "sliceRecordCount": 10000000
                }
            },
            "writer": {
                "name": "streamwriter",
                "parameter": {
                    "print": false
                }
            }
        }],
        "setting": {
            "speed": {
                "channel": 1
            }
        }
    }
}
```

**测试矩阵**：

| channel | sliceRecordCount | 耗时 | 记录写入速度 | 记录 |
|---------|-----------------|------|-------------|------|
| 1 | 10000000 | 45s | 222K rec/s | 基线 |
| 2 | 10000000 | 27s | 370K rec/s | +66% |
| 5 | 10000000 | 15s | 666K rec/s | +200% |
| 10 | 10000000 | 12s | 833K rec/s | +270%（接近天花板） |
| 20 | 10000000 | 13s | 769K rec/s | 反而慢了！CPU上下文切换 |

**结论**：channel=10 对本机 8 核 CPU 来说是最优值。channel=20 时 CPU 利用率虽然高，但系统态（内核上下文切换）时间占比从 3% 飙升到 18%。

### 3.3 步骤三：分析 Channel 对内存的影响

**目标**：理解不同 channel 数的内存占用公式。

```java
// MemoryChannel.java
private int capacity = 128; // 默认容量
private ArrayBlockingQueue<Record> queue = new ArrayBlockingQueue<>(capacity);
```

**内存占用公式**：

```
总内存 = channel数 × capacity × 单条Record大小 × 2(Reader端+Writer端缓冲) 
         + channel数 × (Reader batch + Writer batch)
```

**以小李的订单表为例**（单条 Record 约 500 字节）：

| channel | capacity | 单条Record | Reader+Writer缓冲 | 总内存 |
|---------|----------|-----------|-------------------|--------|
| 1 | 128 | 500B | 64KB+64KB | 128KB+128KB=256KB |
| 5 | 128 | 500B | 64KB×5+64KB×5 | 640KB+640KB=1.28MB |
| 10 | 128 | 500B | 64KB×10+64KB×10 | 1.28MB+1.28MB=2.56MB |
| 20 | 128 | 500B | 64KB×20+64KB×20 | 2.56MB+2.56MB=5.12MB |

看起来不大？但你忘了 **Writer 的 batch buffer**！Writer 的 batchSize=2048 意味着它在攒满 2048 条 Record 之前不会提交。如果 20 个 Writer 线程同时在攒 batch，额外需要 20 × 2048 × 500B = 20MB。再加上 JVM 对象头、字符串常量池、Druid 连接池等开销，轻松突破 1GB。

### 3.4 步骤四：实战——配置单表最优 channel 数的完整流程

**目标**：用科学方法找到最优 channel 数。

**工具准备**：

```bash
# 1. 查源库最大连接数
mysql -u root -e "SHOW VARIABLES LIKE 'max_connections';"
# → max_connections = 200

# 2. 查当前连接数
mysql -u root -e "SHOW PROCESSLIST;" | wc -l
# → 35（业务正在使用的）

# 3. 查可用连接数
# 200 - 35 - 20(安全冗余) = 145
# channel上限 = 145 / 2(每个Channel需2个连接) = 72
```

**CPU/内存约束**：

```bash
# 4. 查CPU核数
nproc
# → 16

# 5. 查可用内存（JVM Xmx 的 60%）
free -h
# → total 32G, used 8G, free 24G
# 可用做 DataX 的: 24G × 60% = 14.4G

# 6. 计算单 Task 内存
# 单条Record = 1KB, batchSize = 2048
# 单 channel 内存 = 128 × 1KB × 2 = 256KB（Channel队列）
#                 + 2048 × 1KB = 2MB（Writer batch）
# channel上限 = 14.4GB / 2.2MB = 6700（显然不是瓶颈）
```

**Task 总数约束**：

```bash
# 7. 表总行数
mysql -u root -e "SELECT COUNT(*) FROM orders;"
# → 50000000

# 8. 假设 splitPk=id, MIN(id)=1, MAX(id)=50000000
# 每个 Task 建议处理 100 万行
# 理想 Task 数 = 50000000 / 1000000 = 50
# channel 上限 = min(50, CPU×2, 连接数上限) = min(50, 32, 72) = 32
```

**最终推荐配置**：

```json
{
    "speed": {
        "channel": 20
    }
}
```

理由：channel=20 时 Task 总数 50，每个 TaskGroup 约 3 个 Task（3 波跑完），CPU 利用率 20/16=125%（轻微超配，可接受），数据库连接 40 个，安全冗余充足。

### 3.5 可能遇到的坑及解决方法

**坑1：channel 数超过 Task 总数**

假设 channel=20，但 split 只产出了 10 个 Task。TaskGroupContainer 启动 20 个 channel 但只有 10 个 Task 可执行，剩余 10 个 channel 空跑浪费。

解决：确保 `split()` 产出的 Task 数 ≥ channel 数。如果表太小无法切分更多 Task，适当减小 channel。

**坑2：多表同步时的 channel 分配**

content 数组有 3 组 Reader-Writer（订单表、用户表、商品表），每组 split 产出 10 个 Task = 30 个 Task，channel=15。

此时 TaskGroup=30/15=2 个，TaskGroup[0]=15 个 Task，TaskGroup[1]=15 个 Task。但 3 组表会被混合调度——第 1 波可能跑 10 个订单 Task + 5 个用户 Task，第 2 波跑 5 个用户 Task + 10 个商品 Task。

如果用户表的每条 Record 是订单表的 10 倍大小，第 1 波和第 2 波的内存占用差异巨大，可能导致第 1 波 OOM。

解决：多表同步时，建议为每组分表，创建独立的 Job 分开运行。

## 4. 项目总结

### 4.1 Channel 调优决策树

```
表总行数 > 1000万？
  ├─ 是 → 设置 splitPk，让 split() 切出 ≥ channel×2 个 Task
  │       └─ channel = min(CPU核数×2, Task总数/2, DB空闲连接数/2)
  └─ 否 → channel=1 即可
```

### 4.2 优点

1. **动态可控**：channel 数从 1 到 N 随意调整，不需要改代码
2. **自动切分**：split() 根据 channel 建议数自动调整 Task 数量
3. **公平调度**：assignFairly 确保 Task 在各 TaskGroup 间均匀分布
4. **失败隔离**：单个 Task 失败不影响其他 Task，可重试
5. **内存可估算**：Channel 容量固定，内存占用公式明确

### 4.3 缺点

1. **调优门槛高**：需要了解 CPU/IO/网络/数据库四维度的资源约束
2. **无动态自适应**：channel 固定后不会根据实际负载自动调整
3. **TaskGroup 粒度粗**：无法按表优先级或数据大小动态分配 Task
4. **内存假设理想化**：假设所有 Record 大小均匀，实际表可能有大字段
5. **不支持优先级调度**：所有 Task 平等，重要的表没有优先通道

### 4.4 适用场景

1. 大表全量同步（千万行级，channel 调优收益最大）
2. 多路同时同步（content 数组多组 Reader-Writer）
3. 异构数据源并发读（每个源端限制不同的连接数）
4. 压测 DataX 吞吐上限（channel=CPU核数×4，CPU 跑满）
5. IO 密集型场景（读 HDFS、写 HDFS，channel 可以调大）

### 4.5 注意事项

1. channel 数 **不** 能超过 Task 总数
2. channel 必须 ≥ 1（设为 0 会 preCheck 失败）
3. channel 调大后务必增加 `-Xmx`（至少等比例增加）
4. MySQL 源端 reader 连接数 = channel（一个 Reader.Task 一个 Connection）
5. 限速（speed.byte/speed.record）是针对整个 Job 的，不是单个 Channel

### 4.6 思考题

1. 如果你有一个 16 核 CPU、500Mbps 带宽、源库 max_connections=30 的环境，要同步一张 20 亿行的单表。请计算最优 channel 数并说明理由。
2. DataX 的 `JobAssignUtil.assignFairly()` 算法中，如果有 7 个 Task 要分配到 3 个 TaskGroup，最终分配结果是怎样的？溯源验证。

（答案见附录）
