# 第1章：Flink是什么——从批到流的演进之路

---

## 1. 项目背景

凌晨两点，某电商公司的数据仓库值班群里炸了锅。业务运营在群里连发三条消息："为什么活动大屏上的GMV已经15分钟没刷新了？""老板就在现场，数据不动让他怎么指挥？""技术部能不能给个准话？"

值班DBA紧急排查：原来半小时前，用于离线统计的Hive小时级任务因为上游日志延迟，直接断掉了。ETL重跑一次需要40分钟，而活动只剩最后两小时。运营总监丢下一句话："你们这套系统，数据出来的时候活动都结束了，还有什么意义？"

这不是个例。**批量计算（Batch Processing）** 在数据延迟面前天然存在短板：数据先攒着，攒够了统一算。这种模式下，"实时"永远是一个伪命题——10分钟出一批数据叫"准实时"，可业务需要的是**秒级**。

与此同时，架构组注意到另一类需求暴涨：风控系统需要在用户下单瞬间判定是否为欺诈订单；推荐系统需要根据用户在App上最近10秒的点击行为立即调整推荐策略；物流调度需要实时感知骑手位置，动态匹配新订单。这些场景都有一个共同特点——**数据是连续不断产生的，处理也必须连续不断地进行**。

这就是 **流计算（Stream Processing）** 的用武之地。在众多流计算引擎中，Apache Flink凭借真正意义上的"流批一体"架构脱颖而出：它从设计之初就以流为核心，将批处理视为"有界流"的特例。这套理念让它在延迟、吞吐和一致性之间找到了工程上优雅的平衡点。

本章作为开篇，我们将从零建立对流计算和Flink核心概念的认知——理解Flink是什么、它如何工作、以及为什么它是当下实时计算的事实标准。

---

## 2. 项目设计

> 场景：周一上午，技术部晨会刚结束，三个工程师在茶水间聊了起来。

**小胖**（嚼着面包）：哎，我昨天刷到个招聘JD，满屏都是Flink。这玩意到底是个啥？跟咱们用的Spark有啥区别？不就是个做计算的框架嘛——食堂打饭，窗口多就快呗？

**小白**（放下咖啡杯）：你这比喻太糙了。食堂打饭是"先到先得"，那是无状态的服务。Flink处理的数据是有状态的，而且数据是源源不断来的。你想想，统计每分钟的成交额，你得记住上一分钟到哪了吧？窗口到没到边界你得知道吧？"队头阻塞了怎么办"——如果上游数据卡在某个分区不动了，整个统计结果就歪了。

**大师**（端着保温杯走过来）：问得好。你们可以把流计算想象成一条高速公路收费站。

传统批处理就像"每天凌晨统一查账"——把一天的过车记录倒进系统，算总数、算高峰。问题是账查出来的时候天都亮了，拥堵早就发生了。

Flink流计算是"每个收费站实时计数"——每过一辆车就加一，你随时打开监控大屏都能看到"过去5分钟通过车辆数"。**技术映射：这就是"有状态流处理"，Flink内部用State来存储中间计算结果，每次新事件到达时增量更新，而不需要重新扫描全量数据。**

**小白**：那我继续问。每过一辆车计数一次——如果计数这活儿干到一半，机器挂了怎么办？重启之后是从0重新开始，还是接着数？这关系到数据准确性。

**大师**：这正是Flink最精妙的设计之一——**Checkpoint机制**。每隔一段时间（比如10秒），Flink会给所有算子拍一张"快照"，把当前计数、窗口状态、数据源消费到的位置（Offset）全部持久化到硬盘。

好比你在收费站每清点完100辆车就贴个便签"已清点到第100辆"。万一突然停电，恢复供电后翻到最新便签，从第101辆接着数，不丢、不重。**技术映射：这叫"Exactly-Once语义"——Flink通过Chandy-Lamport分布式快照算法，在毫秒级开销下实现端到端的精确一次保证。**

**小胖**：等等，我有点晕。你又提窗口、又提状态、又提Checkpoint——这仨到底啥关系？能不能用我听得懂的话再说一遍？

**大师**：好，用煎饼摊来打比方——

- **状态（State）**：你摊煎饼的案板上放着的面糊桶、鸡蛋计数器。它是你当前工作的"记忆"——今天已经用了多少面糊、打了多少鸡蛋。
- **窗口（Window）**：你每摊完10张饼，就把这10张的数据汇总一次（比如"这批用了几个鸡蛋"），这10张就是一个窗口。窗口把无限流切成了有限段。
- **Checkpoint**：你每摊完50张饼，就掏出手机拍个照：面糊还剩多少、鸡蛋用了多少、做到第几张了。手机里的照片就是Checkpoint——万一你突然闹肚子跑了，隔壁老王看着照片就能接着干。

**技术映射——Flink作业 = 煎饼摊：State = 增量业务数据；Window = 聚合的边界；Checkpoint = 故障恢复的存档点。**

**小白**：那Flink的架构呢？我看官方文档又是JobManager又是TaskManager的——这两个是什么角色？

**大师**：回到收费站场景。假设全省有100个收费站，每个收费站的收费员只管自己那一条道的计数。但省交通厅需要一个"总控中心"来分配任务：告诉哪个收费站统计哪个路段、每5分钟汇报一次汇总数据、哪个收费员请假了找人顶班。

- **JobManager**：就是"省交通厅总控中心"。它负责接收你提交的Flink作业（Jar包），画出这个作业的"执行计划"（ExecutionGraph），把任务分配到各个TaskManager上，并协调Checkpoint的触发。一个集群只有一个活跃的JobManager。
- **TaskManager**：就是"每个收费站的收费员"。它真正干活——接收数据、执行算子逻辑、维护State。一个集群可以有多个TaskManager，每个TaskManager内部再细分**Task Slot**（收费站的每个收费窗口）。

```
                     ┌─────────────────────────────┐
                     │        JobManager            │
                     │  调度 | Checkpoint协调 | HA  │
                     └──────────┬───────────────────┘
                                │
              ┌─────────────────┼─────────────────┐
              │                 │                 │
     ┌────────▼────────┐ ┌──────▼──────┐ ┌───────▼───────┐
     │  TaskManager 1   │ │TaskManager 2│ │ TaskManager N  │
     │  ┌────┐ ┌────┐  │ │ ┌────┐ ┌───┐│ │ ┌────┐ ┌────┐ │
     │  │Slot│ │Slot│  │ │ │Slot│ │   ││ │ │Slot│ │Slot│ │
     │  │ 1  │ │ 2  │  │ │ │ 3  │ │ 4 ││ │ │N-1│ │ N  │ │
     │  └────┘ └────┘  │ │ └────┘ └───┘│ │ └────┘ └────┘ │
     └─────────────────┘ └─────────────┘ └───────────────┘
```

**小胖**：我懂了！JobManager是大脑、TaskManager是手脚、Slot是手指头——手指头越多，并行的活儿越多。那Flink和Spark到底怎么选？

**大师**：抓住一个本质区别就够。

| 维度 | Spark（批优先） | Flink（流优先） |
|------|----------------|----------------|
| 设计哲学 | 批处理是原生，流是"微批模拟" | 流处理是原生，批是"有界流的特例" |
| 延迟 | 亚秒级（Structed Streaming） | 毫秒级 |
| 状态管理 | 有限，依赖外部存储 | 内建State Backend，支持增量Checkpoint |
| 窗口 | 基于处理时间为主 | EventTime原生支持，Watermark机制成熟 |

一句话：**如果老板要的是"30分钟后出报表"，Spark够用；如果老板要的是"发生异常30毫秒内告警"，只能上Flink。**

**小胖**：明白了！那我们下午搭个环境跑一跑？光说不练假把式。

**大师**：走，去我工位，十分钟把你跑通第一个Flink作业。

---

## 3. 项目实战

### 环境准备

| 组件 | 版本 | 用途 |
|------|------|------|
| JDK | 11 | 运行环境 |
| Maven | 3.8+ | 构建工具 |
| Apache Flink | 1.18.1 | 流计算引擎 |
| IntelliJ IDEA | 2023+ | 开发IDE |
| Windows WSL2 / Linux | Ubuntu 20.04+ | 操作系统 |

> **坑位预警**：Windows用户请确保WSL2已安装并设为默认，使用PowerShell运行`wsl --install`。Flink在Windows PowerShell原生运行会有路径问题。

### 分步实现

#### 步骤1：创建Maven项目

**目标**：搭建Flink DataStream API的最小化项目骨架。

在IDEA中创建Maven项目，修改`pom.xml`：

```xml
<project xmlns="http://maven.apache.org/POM/4.0.0"
         xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
         xsi:schemaLocation="http://maven.apache.org/POM/4.0.0
         http://maven.apache.org/xsd/maven-4.0.0.xsd">
    <modelVersion>4.0.0</modelVersion>

    <groupId>com.flink.column</groupId>
    <artifactId>flink-practitioner</artifactId>
    <version>1.0-SNAPSHOT</version>

    <properties>
        <maven.compiler.source>11</maven.compiler.source>
        <maven.compiler.target>11</maven.compiler.target>
        <flink.version>1.18.1</flink.version>
        <scala.binary.version>2.12</scala.binary.version>
    </properties>

    <dependencies>
        <!-- Flink DataStream API -->
        <dependency>
            <groupId>org.apache.flink</groupId>
            <artifactId>flink-streaming-java</artifactId>
            <version>${flink.version}</version>
        </dependency>
        <!-- Flink Client (本地运行时) -->
        <dependency>
            <groupId>org.apache.flink</groupId>
            <artifactId>flink-clients</artifactId>
            <version>${flink.version}</version>
        </dependency>
        <!-- 日志框架 -->
        <dependency>
            <groupId>org.slf4j</groupId>
            <artifactId>slf4j-simple</artifactId>
            <version>2.0.9</version>
        </dependency>
    </dependencies>

    <build>
        <plugins>
            <plugin>
                <groupId>org.apache.maven.plugins</groupId>
                <artifactId>maven-shade-plugin</artifactId>
                <version>3.5.0</version>
                <executions>
                    <execution>
                        <phase>package</phase>
                        <goals><goal>shade</goal></goals>
                    </execution>
                </executions>
            </plugin>
        </plugins>
    </build>
</project>
```

#### 步骤2：编写你的第一个Flink作业——"Hello Flink, 即时报文解析"

**目标**：从Socket接收数据，实时统计每秒收到的报文数量。

创建`com.flink.column.chapter01.SocketWordCount.java`：

```java
package com.flink.column.chapter01;

import org.apache.flink.api.common.functions.FlatMapFunction;
import org.apache.flink.api.java.tuple.Tuple2;
import org.apache.flink.streaming.api.datastream.DataStream;
import org.apache.flink.streaming.api.environment.StreamExecutionEnvironment;
import org.apache.flink.util.Collector;

/**
 * 第1章入门作业：从Socket读取文本，按空白字符拆分单词，实时统计词频。
 * 运行方式：
 * 1. 先启动 netcat: nc -lk 9999
 * 2. 再运行本类的 main 方法
 * 3. 在 netcat 终端输入任意文本
 */
public class SocketWordCount {

    public static void main(String[] args) throws Exception {
        // 1. 创建流执行环境 —— 这是所有Flink作业的入口
        final StreamExecutionEnvironment env =
                StreamExecutionEnvironment.getExecutionEnvironment();

        // 2. 从Socket读取数据源 —— 生产环境换为Kafka Source即可
        DataStream<String> text = env.socketTextStream("localhost", 9999);

        // 3. 数据变换：切词 → 组装(word, 1) → 按word分组 → 求和
        DataStream<Tuple2<String, Integer>> counts = text
                .flatMap(new Tokenizer())   // 将每行文本拆成单个单词
                .keyBy(value -> value.f0)   // 按单词分组 (f0 = 单词本身)
                .sum(1);                     // 对第2个字段(f1 = 计数)求和

        // 4. 输出到控制台
        counts.print();

        // 5. 提交作业执行（会阻塞直到作业被取消）
        env.execute("Chapter01-SocketWordCount");
    }

    /**
     * Tokenizer: 将一行文本按空白字符切分为单词，每个单词发射一个 (word, 1) 元组。
     */
    public static final class Tokenizer
            implements FlatMapFunction<String, Tuple2<String, Integer>> {

        @Override
        public void flatMap(String line, Collector<Tuple2<String, Integer>> out) {
            // 按非单词字符拆分
            String[] words = line.toLowerCase().split("\\W+");
            for (String word : words) {
                if (!word.isEmpty()) {
                    out.collect(new Tuple2<>(word, 1));
                }
            }
        }
    }
}
```

#### 步骤3：启动Netcat模拟数据源

**目标**：用netcat工具在本机启动一个TCP Socket服务端，模拟实时日志流。

打开终端（Linux/Mac/WSL），执行：

```bash
nc -lk 9999
```

终端会进入阻塞等待状态，此时你就可以输入任意文本，Flink作业会实时读取。

> **Windows用户注意**：如果系统没有nc命令，可以在WSL中执行，或下载[Nmap](https://nmap.org)（附带ncat.exe），使用`ncat -lk 9999`替代。

#### 步骤4：运行Flink作业

**目标**：在IDE中运行main方法，观察控制台输出。

在IDEA中右键`SocketWordCount.main()` → Run。如果一切正常，控制台会输出类似：

```
[INFO ] Loading configuration property: jobmanager.rpc.address, localhost
[INFO ] Loading configuration property: taskmanager.numberOfTaskSlots, 1
...
2> (hello,1)
2> (flink,1)
2> (hello,2)
1> (world,1)
```

然后在netcat终端输入：

```
hello flink hello world
hello flink
```

观察Flink控制台，你会看到实时汇总结果：

```
2> (hello,1)       # 第一个"hello"到达
2> (flink,1)       # 第一个"flink"到达
2> (hello,2)       # 第二个"hello"到达，结果更新为2
1> (world,1)
2> (hello,3)       # 第三行又有一个"hello"，累加到3
2> (flink,2)       # "flink"也累加到2
```

> **输出前缀"2>"和"1>"**代表不同并行子任务的编号。当前默认并行度为CPU核心数。

#### 步骤5：理解代码中的核心概念

回顾代码中的五个步骤，每一个对应Flink作业的生命周期：

| 步骤 | 代码 | 对应概念 |
|------|------|---------|
| 创建环境 | `StreamExecutionEnvironment` | 作业的入口，决定运行模式（本地/集群） |
| 定义Source | `socketTextStream(...)` | 数据从哪里来 |
| 定义Transformation | `flatMap → keyBy → sum` | 数据如何变换 |
| 定义Sink | `print()` | 结果往哪去 |
| 提交执行 | `env.execute(...)` | 触发DAG构建与调度 |

#### 可能遇到的坑

1. **端口占用**：netcat启动失败，提示"Address already in use"。
   - 解决：`lsof -i :9999`（Linux/Mac）查看占用进程，或换一个端口。
2. **Flink作业连不上Socket**：Job启动时netcat还没开启。
   - 解决：先启动netcat，再启动Flink作业。
3. **控制台没有输出**：maven没有正确下载Flink依赖。
   - 解决：检查网络，IDE中点"Maven → Reload All Projects"，看dependencies是否完整。
4. **IDEA无法识别`Tuple2`**：没引入Flink的Java API依赖。
   - 解决：检查`pom.xml`中`flink-streaming-java`是否已添加。

### 测试验证

在netcat中输入以下测试数据，验证窗口效果：

```
# 输入这些
apple banana apple cherry banana
apple durian
```

**预期输出**：

```
(apple,1)
(banana,1)
(apple,2)
(cherry,1)
(banana,2)
(apple,3)
(durian,1)
```

如果结果与预期一致，恭喜你——你已经成功运行了第一个Flink流计算作业！

---

## 4. 项目总结

### 优点 & 缺点

| | Flink 流计算 | Spark Structured Streaming（对比） |
|------|-------------|----------------------------------|
| **优点1** | 亚毫秒级延迟，真正逐事件处理 | 微批模式，延迟通常100ms+ |
| **优点2** | EventTime语义完善，Watermark机制成熟 | EventTime支持较弱，窗口语义不如Flink灵活 |
| **优点3** | 有状态计算原生支持，Checkpoint/Savepoint机制完善 | 状态管理依赖外部，断点续传复杂度高 |
| **优点4** | Table API + DataStream API + SQL三层API统一 | SQL层较强，但DataStream API功能不如Flink |
| **优点5** | 流批一体——同一套代码可处理实时和离线 | 流批API分离，维护两套逻辑 |
| **缺点1** | 社区迭代快，大版本API不完全向后兼容 | Spark版本升级相对平滑 |
| **缺点2** | 部署与运维复杂度较高（JobManager HA、State Backend选型等） | 生态更成熟，EMR/DataProc等托管服务更完善 |
| **缺点3** | Python API（PyFlink）成熟度不如Java/Scala | PySpark生态更成熟、第三方库更丰富 |

### 适用场景

**典型场景（强烈推荐）**：
1. 实时数据大屏（GMV、DAU、订单量等核心指标分钟级刷新）
2. 实时风控/反欺诈（毫秒级判定交易风险，联动拦截）
3. 实时推荐（用户行为流实时更新特征向量）
4. 日志ETL/数据清洗（Kafka日志实时解析、标准化入湖）
5. 实时数仓（CDC实时捕获业务库变更，分钟级刷新数仓宽表）

**不适用场景**：
1. 单纯T+1离线报表——Hive/Spark SQL更简单、成本更低
2. 数据量极小（日增<10MB）且无实时性要求——过度设计，引入运维复杂度

### 注意事项
- **版本选择**：建议使用Flink 1.17或1.18 LTS版本，1.16以下已停止维护
- **JDK版本**：Flink 1.18要求JDK 11，JDK 8不再支持
- **日志框架冲突**：Flink自带log4j2，项目中避免引入冲突的SLF4J绑定

### 常见踩坑经验

**案例1：Flink作业提交后TaskManager不断重启**
- 根因：State Backend配置为RocksDB但本地磁盘空间不足
- 解决：检查`taskmanager.tmp.dirs`配置路径，预留足够的磁盘空间（建议不少于50GB）

**案例2：生产环境Socket Source挂掉导致整个作业雪崩**
- 根因：Socket是单点，没有任何容错能力
- 解方：生产环境绝对不能用Socket Source——应替换为Kafka（自带多分区+Offset可回溯）、Pulsar等消息队列

**案例3：checkpoint一直处于IN_PROGRESS无法完成**
- 根因：反压导致Barrier无法对齐，超时后作业Failover
- 解决：增加并行度、调整`execution.checkpointing.timeout`参数、排查下游慢Sink

### 思考题

1. 在代码`keyBy(value -> value.f0)`中，为什么KeyBy之后才能做`sum(1)`？如果去掉KeyBy，直接用`sum(1)`会发生什么？请从Flink的"分区"和"聚合"的关系角度思考。（提示：下章讲解分区策略时揭晓答案）

2. `env.execute("Chapter01-SocketWordCount")`这行代码是阻塞调用，执行后程序不会退出。如果我把execute放在try-finally中，finally里能执行到吗？这背后反映了Flink作业的什么运行特性？

> 答案将在第2章"环境搭建三分钟"中揭晓。
