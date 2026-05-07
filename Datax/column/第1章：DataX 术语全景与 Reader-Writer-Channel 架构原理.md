# 第1章：DataX 术语全景与 Reader-Writer-Channel 架构原理

## 1. 项目背景

某互联网电商公司每天早上运营团队需要从 MySQL 订单库中导出前一天的交易数据，同步到 HDFS 数据湖供算法团队做推荐模型训练，同时还要把清洗后的统计结果回写到 MySQL 报表库。目前这个流程靠三个运维工程师手工执行：凌晨 2 点用 sqoop 拉 MySQL → HDFS，凌晨 4 点跑 Hive SQL 清洗，凌晨 6 点用 sqoop 再灌回 MySQL。数据量从半年前的 50 万行增长到现在的 500 万行，执行时间从 20 分钟拖延到 3 小时，还经常因为字段类型不匹配导致任务中断。

痛点逐层放大：第一，sqoop 依赖 MapReduce，每次启动都有 YARN 调度开销，对于中小数据量来说"杀鸡用牛刀"；第二，MySQL → HDFS → MySQL 的三段式链路缺乏统一编排，一个环节失败需要全部重跑；第三，当数据源从单一的 MySQL 扩展到 MongoDB 日志和 PostgreSQL 用户库时，运维需要维护三套不同的同步脚本。技术负责人意识到：他们需要的不是一个更重的 Hadoop 工具链，而是一个轻量、可插拔、支持异构数据源的数据同步引擎——这正是 DataX 的设计初衷。

## 2. 项目设计——剧本式交锋对话

**（周五下午，会议室，白板上画满了各种数据源的图标）**

**小胖**：（啃着一块巧克力）大师，这数据同步的需求听着就跟食堂打饭一样啊——MySQL 是一号窗口，HDFS 是二号窗口，把一号窗口的菜端到二号窗口不就行了？为啥要搞那么复杂？

**小白**：（眉头紧锁）不光是这样。现在有三号窗口 MongoDB，四号窗口 PostgreSQL。明天可能还有五号窗口 ClickHouse。你让运维给每对窗口写一套搬运脚本，迟早会出乱子。而且 sqoop 每次都起 MapReduce，等 YARN 调度完，黄花菜都凉了。

**大师**：（笑着在白板上画了一个三层的架构图）小胖的比喻很形象，但问题在于你不可能给每对窗口都雇一个专人。DataX 的思路是这样的——（边画边讲）它把整个同步过程抽象成三层：最上层是 Job，代表一次完整的同步任务，比如"把 MySQL 订单表同步到 HDFS"；中间层是 Task，代表切分后的最小执行单元；底层是 TaskGroup，是一组 Task 的容器，负责管理并发。

**技术映射**：Job-Task-TaskGroup 三级模型 = 一个搬仓库的工程。Job 是整个搬家项目，Task 是把家具拆成 N 个箱子，TaskGroup 是每辆货车（限载 5 个箱子）。

**小胖**：哦哦，所以是把一个大活拆成小活，多辆车一起运？（擦了擦嘴）那这个 Channel 又是什么角色？我看文档里反复提 Reader-Writer-Channel 三个东西。

**大师**：问得好。Channel 是 Reader 和 Writer 之间的管道。Reader 负责从源端读数据，Writer 负责往目标端写数据，它们之间通过 Channel 传递数据——而且是 1:1 配对的。一个 Reader-Task 对应一个 Writer-Task，中间夹一个 Channel。

**小白**：1:1 配对？（追问）也就是说，如果源端切分了 10 个 Task、目标端切分了 10 个 Task，就会创建 10 对 Reader-Writer，每对用一根 Channel 管道通信。那不是浪费吗？能不能一对多发散？

**大师**：（摇头）1:1 设计是刻意为之的。你想想，如果一个 Reader 要给多个 Writer 发数据，首先要有路由逻辑——哪条记录发给哪个 Writer？这就引入了分布式事务的复杂度。DataX 的选择是"切分即绑定"：源端切出来的第 3 个 Task 的数据，一定由第 3 个 Writer-Task 写入，不需要路由，不需要协调。这个设计牺牲了灵活性，换来了极致的简单和性能。

**技术映射**：Channel 1:1 对等模型 = 一对一专线快递，不需要中转站分拣，直接从 A 仓到 B 仓。

**小胖**：那万一 Reader 读得飞快，Writer 写得慢，Channel 会不会撑爆？

**大师**：（点头）这就是流控机制的核心。Channel 内部有一个叫 MemoryChannel 的实现，底层是一个 `ArrayBlockingQueue`，默认容量 128。当队列满了，Reader 的 push 操作就会阻塞；当队列空了，Writer 的 pull 操作就会阻塞。同时还有两种限速策略：`speed.byte` 按字节数限速，`speed.record` 按记录数限速。相当于给管道装了两个水阀，不管谁快谁慢，最终流速由水阀决定。

**小白**：（在本子上记着）那如果 Channel 容量设太小，是不是就像高速公路收费站——车一多就堵？设太大又像空荡荡的八车道，浪费内存？

**大师**：正是。这个参数需要结合实际场景调优，后续章节会详细讲。

## 3. 项目实战

### 3.1 环境准备

本章不需要实际运行代码，重点是理解架构。准备如下材料：

- 白板或 Draw.io（画架构图）
- DataX 源码（从 GitHub 拉取：`git clone https://github.com/alibaba/DataX.git`）
- IDE（IntelliJ IDEA），用于浏览核心类

### 3.2 绘制 DataX 架构图

**步骤目标**：将 DataX 的架构分为 4 层，逐层理解并绘制。

---

**第一层：用户接口层**

用户通过 JSON 文件描述同步任务，然后调用 Python 启动脚本：

```bash
python datax.py /path/to/job.json
```

Python 脚本 `bin/datax.py` 本质上做了三件事：
1. 校验 Java 环境（JAVA_HOME）
2. 组装 JVM 启动参数（-Xms、-Xmx、classpath）
3. 调用 `java -jar datax.jar`，入口类为 `Engine`

---

**第二层：框架引擎层（Engine → JobContainer）**

打开源码 `core/src/main/java/com/alibaba/datax/core/Engine.java`，核心入口：

```java
public static void entry(final String[] args) throws Throwable {
    // 1. 解析命令行参数：-job、-jobid、-mode
    Options options = new Options();
    options.addOption("job", true, "Job config.");
    options.addOption("jobid", true, "Job unique id.");
    options.addOption("mode", true, "Running mode: standalone/local/distributed");
    
    CommandLineParser parser = new BasicParser();
    CommandLine cl = parser.parse(options, args);
    
    // 2. 解析JSON配置文件
    String jobPath = cl.getOptionValue("job");
    Configuration configuration = ConfigParser.parse(jobPath);
    
    // 3. 启动JobContainer
    configuration.set("job.mode", cl.getOptionValue("mode", "standalone"));
    Engine engine = new Engine();
    engine.start(configuration);
}
```

JobContainer 是 DataX 的大脑，位于 `core/src/main/java/com/alibaba/datax/core/job/JobContainer.java`，负责 9 步生命周期：

```
preCheck() → preHandle() → init() → prepare() → split() → schedule() → post() → postHandle() → destroy()
```

各阶段职责：
| 阶段 | 职责 |
|------|------|
| preCheck | 验证JSON配置合法性（必填字段、类型校验） |
| preHandle | 预处理器（如 Schema 处理） |
| init | 通过 LoadUtil 加载 Reader.Job 和 Writer.Job 插件类 |
| prepare | 全局准备（如 DROP 目标表、preSql） |
| split | 调用 Reader.Job.split(N) 和 Writer.Job.split(N) 生成 Task 列表 |
| schedule | 将 Task 分配到 TaskGroup，启动并发执行 |
| post | 全局清理（如 postSql、索引重建） |
| postHandle | 后处理器 |
| destroy | 释放插件资源 |

---

**第三层：任务调度层（TaskGroupContainer + Scheduler）**

schedule() 阶段做了两件事：
1. 将 Task 分配到 TaskGroup（`JobAssignUtil.assignFairly()`）
2. 交个 Scheduler 启动所有 TaskGroup

```java
// JobContainer.schedule() 核心逻辑
List<Configuration> taskConfigs = mergeReaderAndWriterTaskConfigs(
    readerTaskConfigs, writerTaskConfigs);
    
List<Configuration> taskGroupConfigs = JobAssignUtil.assignFairly(
    taskConfigs, needChannelNumber, channelsPerTaskGroup);

AbstractScheduler scheduler = new StandAloneScheduler(containerCommunicator);
scheduler.schedule(taskGroupConfigs);
```

TaskGroupContainer 的核心数据结构：

```java
public class TaskGroupContainer extends AbstractContainer {
    private Configuration configuration;
    private int channelNumber;  // 当前TaskGroup的并发Channel数
    private int taskMaxRetryTimes; // Task失败重试次数
    private List<Configuration> taskConfigs;  // 待执行Task列表
    
    // 内部类：一个Task的执行器
    class TaskExecutor {
        private Channel channel;                    // 数据传输管道
        private ReaderRunner readerRunner;          // Reader执行线程
        private WriterRunner writerRunner;          // Writer执行线程
    }
}
```

---

**第四层：插件层（Reader/Writer + Channel）**

以 MySQL Reader 插件为例，目录结构：

```
plugin/reader/mysqlreader/
├── plugin.json              # 插件注册文件
├── mysqlreader-0.0.1.jar    # 插件代码
└── libs/                    # 依赖JAR
    ├── mysql-connector-java-5.1.47.jar
    └── ...
```

plugin.json 契约内容：

```json
{
    "name": "mysqlreader",
    "class": "com.alibaba.datax.plugin.reader.mysqlreader.MysqlReader",
    "description": "use for MySQL database",
    "developer": "alibaba"
}
```

Reader 插件层次结构：

```
Reader (common/spi)
  └── Reader.Job (内部类): 负责Job级别的切分逻辑
        └── init(), preCheck(), prepare(), split(int), post(), destroy()
  └── Reader.Task (内部类): 负责Task级别的数据读取
        └── init(), prepare(), startRead(RecordSender), post(), destroy()
```

### 3.3 架构全景图（文字版）

```
┌──────────────────────────────────────────────────────────────────┐
│                        用户层                                    │
│  datax.py job.json  →  Engine.main(args)                        │
└──────────────────────────────────────────────────────────────────┘
                               │
                               ▼
┌──────────────────────────────────────────────────────────────────┐
│                      JobContainer (core)                         │
│  preCheck → init (加载插件) → split (切分Task) → schedule (调度) │
└──────────────────────────────────────────────────────────────────┘
                               │
                    ┌──────────┼──────────┐
                    ▼          ▼          ▼
              ┌──────────┐ ┌──────────┐ ┌──────────┐
              │TaskGroup1│ │TaskGroup2│ │TaskGroup3│  (core)
              │ channel=5│ │ channel=5│ │ channel=5│
              └──────────┘ └──────────┘ └──────────┘
                    │
         ┌──────────┼──────────┐  × channelNumber
         ▼          ▼          ▼
    ┌───────┐  ┌───────┐  ┌───────┐
    │Task 1 │  │Task 2 │  │Task 3 │
    │R→C→W  │  │R→C→W  │  │R→C→W  │   R=Reader, C=Channel, W=Writer
    └───────┘  └───────┘  └───────┘
```

### 3.4 核心术语速查表

| 术语 | 英文 | 含义 |
|------|------|------|
| Job | Job | 一次完整的数据同步任务 |
| Task | Task | 拆分后的最小执行单元，一个 Task = 一对 Reader+Writer |
| TaskGroup | TaskGroup | 一组 Task 的容器，有并发 Channel 数限制 |
| Channel | Channel | Reader 与 Writer 之间的有界数据传输管道 |
| Reader | Reader | 数据读取插件，从源端读取数据 |
| Writer | Writer | 数据写入插件，向目标端写入数据 |
| Transformer | Transformer | 数据变换插件，在 Channel 两端对数据做清洗转换 |
| Record | Record | 一行数据，包含多个 Column |
| Column | Column | 一个字段值，有 6 种子类型 |
| RecordSender | RecordSender | Reader 端接口，用于向 Channel 发送 Record |
| RecordReceiver | RecordReceiver | Writer 端接口，用于从 Channel 接收 Record |

### 3.5 可能遇到的坑及解决方法

**坑1：看不懂架构图分层**

解决方法：用"搬家"类比理解。Job = 搬家项目，Task = 一个箱子，TaskGroup = 一辆货车（限载 N 箱），Channel = 货车上的传送带，Reader = 搬上车的工人，Writer = 搬下车的工人。

**坑2：混淆 Job 和 Task 的概念**

解决方法：在源码中打断点观察。JobContainer.split() 方法调用后，输出的 `List<Configuration>` 就是 Task 列表，每个元素都是独立的子配置。

---

## 4. 项目总结

### 4.1 DataX vs 同类技术对比

| 维度 | DataX | Sqoop | Flink CDC | Kettle |
|------|-------|-------|-----------|--------|
| 架构 | 框架+插件，单机多线程 | MapReduce，依赖Hadoop | 分布式流处理，依赖Flink集群 | 单机/集群，图形化GUI |
| 数据量 | 适合GB~TB级别 | 适合TB~PB级别 | 适合实时增量 | 适合中小数据量 |
| 部署复杂度 | 极低（解压即用） | 需要Hadoop集群 | 需要Flink集群 | 中等 |
| 扩展性 | 插件式，需写Java | 需写MR | 需写Flink Job | 插件/脚本 |
| 实时性 | 批处理 | 批处理 | 实时 | 批处理 |
| 学习成本 | 低（JSON配置） | 中 | 高 | 中（GUI拖拽） |

### 4.2 优点

1. **极简部署**：解压后只要 JDK 就能跑，没有外部服务依赖
2. **插件化设计**：Reader/Writer 插件独立，ClassLoader 隔离，开发新插件不影响核心框架
3. **流控机制**：Channel 级别的字节和记录双限速，保护源端和目标端不被拖垮
4. **容错能力**：脏数据记录、失败重试、错误阈值，生产环境必备
5. **性能可观**：单机多线程并发，合理配置下轻松达到 10 万+ QPS

### 4.3 缺点

1. **无增量同步**：DataX 只做全量，增量需要配合时间戳 WHERE 条件或外部调度系统
2. **无分布式调度**：默认 standalone 模式是单进程，需要自行搭建分布式环境
3. **无实时能力**：批处理架构，不支持 CDC 实时捕获变更
4. **错误处理粗糙**：单条脏数据会导致整个 batch 重试，影响效率
5. **社区活跃度下降**：阿里已将重心转向 Seatunnel 等新一代工具

### 4.4 适用场景

1. MySQL 全量/增量数据迁移到 HDFS/Hive 数据仓库
2. 异构数据库之间的数据同步（MySQL ↔ Oracle ↔ PostgreSQL）
3. 日志型数据（MongoDB、HBase）定期导出到关系型数据库做报表
4. 云上云下数据迁移（本地 MySQL → 云 RDS）
5. 作为 ETL 管道的"搬运工"环节，配合调度系统使用

### 4.5 注意事项

1. **JSON 格式严格**：多一个逗号、少一个引号都会导致解析失败，建议用 JSON Schema 校验
2. **JDK 版本**：建议使用 JDK 8，部分插件对 JDK 11 兼容性不佳
3. **内存规划**：DataX 是内存密集型应用，需要根据数据量提前规划 JVM 堆大小
4. **splitPk 选择**：分片键必须是有序的数值或字符串类型，否则切分失败
5. **Channel 数不是越大越好**：超过 CPU 核数 4 倍后，上下文切换开销反而拖累性能

### 4.6 思考题

1. DataX 的 Channel 为什么采用 1:1 配对，而不是 1:N 或 N:M？如果设计成 1:N 模式，需要解决哪些额外问题？
2. 如果 JobContainer 的 split() 方法返回了 100 个 Task，但只有 3 个 TaskGroup（每个 channel=5），那么这 100 个 Task 是如何调度执行的？请画出时序图。

（答案见附录）
