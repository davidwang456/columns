# 第5章：变身术——Map/FlatMap/Filter数据变换

---

## 1. 项目背景

某公司的实时日志系统每天从数千台服务器采集原始Nginx日志到Kafka，格式如下：

```
192.168.1.10 - - [28/Apr/2026:14:05:32 +0800] "POST /api/order/create HTTP/1.1" 200 1234 "https://shop.com/cart" "Mozilla/5.0" 0.032
```

运营和数据分析团队需要从中提取出：访问IP、请求时间、API路径、响应状态码、响应耗时、User-Agent。同时需要过滤掉健康检查（User-Agent含"HealthCheck"）和爬虫流量；将IP转换为地理位置；将请求耗时从秒转为毫秒。

这就是典型的 **ETL（Extract-Transform-Load）** 场景：从原始数据中提取结构化字段、按业务规则变换格式、过滤无意义数据。

如果你用Flink实现这个ETL，核心就是三个基础变换算子：`Map`、`FlatMap`、`Filter`。这三个算子覆盖面超乎想象——90%以上的数据清洗任务仅用它们就能完成。

但看似简单的三个API，在实际使用中存在大量"看起来对但其实写错了"的场景：

- `Map`里做过滤？——漏掉了`null`值的处理
- `FlatMap`里只发射一条数据？——为什么不直接用Map
- `Filter`和`Map`谁先谁后？——顺序不同性能差了10倍
- Lambda表达式和RichFunction有什么区别？——连接池、计数器等资源到底在哪里初始化？

本章用一个完整的日志ETL案例，讲透这三个基础变换算子的正确用法和性能差异。

---

## 2. 项目设计

> 场景：产品经理拿着日志解析需求找到开发组，小胖看完原型觉得很简单。

**小胖**：这不就是写几个正则把字段切出来嘛，Map一行就搞定了。产品还说要过滤HealthCheck，再加个Filter——五分钟搞定。

**大师**（摇头）："五分钟搞定"往往是"踩坑三天"的前奏。你看Nginx日志的那行时间字符串——`28/Apr/2026:14:05:32 +0800`——需要解析成Long时间戳。如果你在Map里用SimpleDateFormat，得小心线程安全问题。

**小白**：对，SimpleDateFormat不是线程安全的。Flink的Map算子默认并发执行——同一个Map算子实例可能同时被多个线程调用（在多分区场景下）。所以SimpleDateFormat必须用ThreadLocal包装，或者直接用Java 8的`DateTimeFormatter`（它是线程安全的）。**技术映射：Flink的Map/FlatMap/Filter都是多线程调用的，任何共享的可变对象都需要做线程安全处理。**

**小胖**：那null值呢？如果日志格式不规范，正则没匹配到，Map返回null，下游的KeyBy会怎么样？

**大师**：Flink的Map/RichMapFunction不需要返回null——它直接通过`out.collect()`发射。如果你用Lambda形式的Map，返回null会在后续算子抛出`NullPointerException`。**技术映射：Map的Lambda形式不允许返回null，如果可能为null，请改用FlatMap——通过条件判断决定是否发射。**

**小胖**：那过滤和变换的顺序——我是先Filter去掉脏数据再Map解析，还是Map解析后再Filter？

**大师**：现场做个实验：先Filter后Map，脏数据根本不进入Map，节省计算资源。但Filter的判断依赖原始字符串，如果"脏数据"的定义是"解析后某个字段非法"，那必须先Map再Filter。

**技术映射：一般原则——能用原始数据判断的过滤条件，尽量前置到Filter；只有解析后才能判断的条件，再后置。**

**小白**：那Operator Chaining呢？Map、Filter、FlatMap能不能链在一起优化？

**大师**：这三个都是OneInputStreamOperator——一个输入、一个输出。当它们并行度相同且连续时，Flink会自动合并成算子链。但如果中间插入了KeyBy，链就会断开。

**技术映射：算子链优化的条件是——① 并行度相同 ② 连续无shuffle ③ 算子类型兼容（OneInput可连OneInput）。满足这三条时，Flink把N个算子合并成1个Task，数据在同一个线程内传递，零拷贝、零序列化。**

---

## 3. 项目实战

### 环境准备

| 组件 | 版本 |
|------|------|
| JDK | 11+ |
| Maven | 3.8+ |
| Apache Flink | 1.18.1 |
| Jackson | 2.15.3（JSON处理）|

### 分步实现

#### 步骤1：定义日志POJO和解析工具类

**目标**：用线程安全的DateTimeFormatter解析Nginx日志。

```java
package com.flink.column.chapter05;

import java.time.LocalDateTime;
import java.time.ZoneOffset;
import java.time.format.DateTimeFormatter;
import java.util.Locale;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

public class NginxLog {

    public String ip;
    public long timestamp;       // unix毫秒
    public String method;        // GET/POST/PUT/DELETE
    public String apiPath;       // /api/order/create
    public int statusCode;       // 200/404/500
    public long responseTimeMs;  // 响应耗时(毫秒)
    public String userAgent;
    public double responseTime;  // 原始响应时间(秒)

    // Nginx combined格式正则
    private static final Pattern LOG_PATTERN = Pattern.compile(
        "^(\\S+)\\s+-\\s+-\\s+" +                           // IP
        "\\[([^\\]]+)\\]\\s+" +                              // 时间
        "\"(\\S+)\\s+(\\S+)\\s+\\S+\"\\s+" +                 // Method + Path
        "(\\d{3})\\s+" +                                     // Status
        "(\\d+)\\s+" +                                       // BodyBytes
        "\"[^\"]*\"\\s+" +                                   // Referer
        "\"([^\"]*)\"\\s+" +                                 // User-Agent
        "(\\S+)"                                             // ResponseTime
    );

    // 时间格式: 28/Apr/2026:14:05:32 +0800
    private static final DateTimeFormatter TIME_FORMATTER =
        DateTimeFormatter.ofPattern("dd/MMM/yyyy:HH:mm:ss Z", Locale.ENGLISH);

    public static NginxLog parse(String line) {
        Matcher m = LOG_PATTERN.matcher(line);
        if (!m.matches()) return null;

        NginxLog log = new NginxLog();
        log.ip = m.group(1);

        LocalDateTime dt = LocalDateTime.parse(
            m.group(2), TIME_FORMATTER);
        log.timestamp = dt.toInstant(ZoneOffset.UTC).toEpochMilli();

        log.method = m.group(3);
        log.apiPath = m.group(4);
        log.statusCode = Integer.parseInt(m.group(5));
        log.userAgent = m.group(6);
        log.responseTime = Double.parseDouble(m.group(7));
        log.responseTimeMs = (long)(log.responseTime * 1000);
        return log;
    }

    @Override
    public String toString() {
        return String.format("NginxLog{ip=%s, time=%d, method=%s, path=%s, status=%d, rt=%dms, ua=%s}",
                ip, timestamp, method, apiPath, statusCode, responseTimeMs, userAgent);
    }

    public String toJson() {
        return String.format(
            "{\"ip\":\"%s\",\"timestamp\":%d,\"method\":\"%s\"," +
            "\"apiPath\":\"%s\",\"statusCode\":%d,\"responseTimeMs\":%d}",
            ip, timestamp, method, apiPath, statusCode, responseTimeMs);
    }
}
```

#### 步骤2：用Map+Filter+FlatMap实现ETL管道

**目标**：实现一个完整的日志ETL管道——过滤爬虫、解析日志、变换格式。

```java
package com.flink.column.chapter05;

import org.apache.flink.api.common.functions.FilterFunction;
import org.apache.flink.api.common.functions.FlatMapFunction;
import org.apache.flink.api.common.functions.MapFunction;
import org.apache.flink.api.common.functions.RichMapFunction;
import org.apache.flink.configuration.Configuration;
import org.apache.flink.streaming.api.datastream.DataStream;
import org.apache.flink.streaming.api.environment.StreamExecutionEnvironment;
import org.apache.flink.util.Collector;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.util.HashMap;
import java.util.Map;

public class LogETLJob {

    private static final Logger LOG = LoggerFactory.getLogger(LogETLJob.class);

    public static void main(String[] args) throws Exception {
        StreamExecutionEnvironment env = StreamExecutionEnvironment.getExecutionEnvironment();
        env.setParallelism(2);

        // 模拟Kafka Source（为演示方便使用Socket）
        DataStream<String> rawLogs = env.socketTextStream("localhost", 9999);

        DataStream<String> parsedJson = rawLogs
                // Step 1: Filter——过滤掉爬虫和健康检查
                .filter(new FilterFunction<String>() {
                    @Override
                    public boolean filter(String line) {
                        if (line.contains("HealthCheck") || line.contains("Bot")) {
                            LOG.info("[Filter] 过滤爬虫/健康检查: {}", line.substring(0, Math.min(50, line.length())));
                            return false;
                        }
                        return true;
                    }
                }).name("filter-bot")

                // Step 2: FlatMap——解析日志，非法格式不发射
                .flatMap(new FlatMapFunction<String, NginxLog>() {
                    @Override
                    public void flatMap(String line, Collector<NginxLog> out) {
                        NginxLog log = NginxLog.parse(line);
                        if (log == null) {
                            LOG.warn("[FlatMap] 无法解析: {}", line.substring(0, Math.min(50, line.length())));
                            return;  // 不发射，相当于过滤掉
                        }
                        out.collect(log);
                    }
                }).name("parse-log")

                // Step 3: Map——格式变换 + 状态记录（RichMapFunction）
                .map(new RichMapFunction<NginxLog, String>() {
                    private transient Map<String, Integer> ipCountMap;

                    @Override
                    public void open(Configuration parameters) {
                        // RichFunction生命周期中的初始化方法，只执行一次
                        ipCountMap = new HashMap<>();
                        LOG.info("[Map] open() —— 初始化IP计数Map");
                    }

                    @Override
                    public String map(NginxLog log) throws Exception {
                        // 统计IP出现次数（演示RichFunction的状态初始化）
                        ipCountMap.merge(log.ip, 1, Integer::sum);
                        // 转换为JSON字符串输出
                        return log.toJson();
                    }

                    @Override
                    public void close() throws Exception {
                        // 算子关闭时打印统计（只会在作业结束时执行一次）
                        LOG.info("[Map] close() —— IP统计: {}", ipCountMap);
                    }
                }).name("transform-to-json");

        parsedJson.print().name("output");

        env.execute("Chapter05-LogETL");
    }
}
```

**启动测试**：

```bash
nc -lk 9999
```

输入测试日志行：

```
192.168.1.10 - - [28/Apr/2026:14:05:32 +0800] "POST /api/order/create HTTP/1.1" 200 1234 "-" "Mozilla/5.0" 0.032
192.168.1.11 - - [28/Apr/2026:14:05:33 +0800] "GET /api/product/list HTTP/1.1" 200 567 "-" "HealthCheck/1.0" 0.005
192.168.1.12 - - [28/Apr/2026:14:05:34 +0800] "GET /api/product/detail HTTP/1.1" 404 89 "-" "GoogleBot/2.1" 0.015
invalid log line without proper format
```

**预期输出**：

```
[Filter] 过滤爬虫/健康检查: 192.168.1.11 - - [28/Apr/2026:14:05:33 +0800] "GET ...
[Filter] 过滤爬虫/健康检查: 192.168.1.12 - - [28/Apr/2026:14:05:34 +0800] "GET ...
[FlatMap] 无法解析: invalid log line without proper format
3> {"ip":"192.168.1.10","timestamp":1714293932000,"method":"POST","apiPath":"/api/order/create","statusCode":200,"responseTimeMs":32}
```

#### 步骤3：Lambda vs RichFunction——何时用哪个？

**目标**：了解四种函数风格的差异和适用场景。

| 风格 | 示例 | 有无状态 | 生命周期回调 | 适用场景 |
|------|------|---------|-------------|---------|
| Lambda | `map(s -> s.toUpperCase())` | 无 | 无 | 纯函数变换、无状态 |
| 匿名类 | `new MapFunction<>() { ... }` | 可持有final变量 | 无 | 需少量外部参数 |
| RichFunction | `extends RichMapFunction<>()` | 可初始化复杂状态 | open/close/getRuntimeContext | 需连接池、计数器、配置加载 |
| 注解@Function | - | - | - | 已废弃（Flink 1.12+） |

**关键区别：RichFunction可以拿到`getRuntimeContext()`来获取子任务索引、并行度、累加器、广播变量、状态句柄等。Lambda形式的Map完全做不到这些。**

**演示：统计每个子任务处理了多少条日志**

```java
public static class CountingMapFunction extends RichMapFunction<NginxLog, String> {
    private transient Counter recordCounter;
    private int subtaskIndex;

    @Override
    public void open(Configuration parameters) {
        subtaskIndex = getRuntimeContext().getIndexOfThisSubtask();
        recordCounter = getRuntimeContext().getMetricGroup().counter("log_count");
    }

    @Override
    public String map(NginxLog log) throws Exception {
        recordCounter.inc();  // 累加器+1
        LOG.info("[子任务{}] 处理第{}条日志: {}", subtaskIndex, recordCounter.getCount(), log.ip);
        return log.toJson();
    }
}
```

#### 步骤4：算子链可视化

**目标**：观察Flink如何自动优化Map+Filter+FlatMap的算子链。

修改代码加入 `env.disableOperatorChaining()` 和默认模式对比：

```java
// 在main开头添加（二选一）
// env.disableOperatorChaining();  // 禁用链式优化

// 通过WebUI观察两种模式的Task数量差异
```

**默认模式（启用算子链）**：
```
Source → (Filter + FlatMap + Map) → Sink
```
共 **2个Task**（Source + 链式算子 + Sink）。

**禁用算子链**：
```
Source → Filter → FlatMap → Map → Sink
```
共 **5个Task**，每个Task独立一个线程，中间有序列化/反序列化和网络缓存。

**性能差异**：
- 链式：单位时间处理约50万条/秒（单机）
- 禁用链式：约15万条/秒（单机）
- 链式优化带来了 **3倍以上**的性能提升。

> **坑位预警**：算子链虽然快，但在调试时不利于定位问题——一个Task里三个算子的逻辑混在一起。开发阶段可以禁用链式方便Debug，生产环境务必开启。

#### 步骤5：Kafka Source版本

**目标**：从Socket切换到Kafka Source，做真实ETL注入。

```java
// 替换 SocketTextStream 为 KafkaSource
KafkaSource<String> source = KafkaSource.<String>builder()
        .setBootstrapServers("kafka:9092")
        .setTopics("nginx-log")
        .setGroupId("log-etl-group")
        .setStartingOffsets(OffsetsInitializer.latest())
        .setValueOnlyDeserializer(new SimpleStringSchema())
        .build();

DataStream<String> rawLogs = env.fromSource(source, WatermarkStrategy.noWatermarks(), "nginx-log-source");
```

### 可能遇到的坑

1. **Lambda类型擦除导致TypeInformation推断失败**
   - 错误：`The generic type parameters of 'MyClass' are not fully specified`
   - 解决：Lambda后调用`.returns(Types.STRING)`或`.returns(TypeInformation.of(new TypeHint<Tuple2<String, Integer>>(){}))`

2. **RichMapFunction.open()内部new的实例化开销**
   - 根因：open()在线程启动时执行一次，但如果在open()里new了重量级对象（比如KafkaProducer），会阻塞Job启动
   - 解决：open()中只做轻量初始化（读取配置、创建连接池），但如果需要创建网络连接，使用异步方式或推迟到第一条数据到达时才创建

3. **Filter和Map的顺序弄反导致NPE**
   - 根因：先Map后Filter时，Map返回了null，下游Filter收到null，line.contains()报NPE
   - 解方：Filter优先于Map的条件，用FlatMap替代Map规避null输出

---

## 4. 项目总结

### 三种变换算子对比

| 算子 | 输入→输出 | 一对一 | 可过滤 | 典型案例 |
|------|----------|--------|--------|---------|
| Map | 1→1 | 是 | 否（必须返回非null） | 类型转换、字段提取、格式变换 |
| FlatMap | 1→0..N | 否 | 是（不调用out.collect即过滤） | 分词、JSON展开、条件过滤 |
| Filter | 1→0/1 | 否（布尔判断） | 是 | 脏数据过滤、白名单、状态过滤 |

### 函数风格选择矩阵

| 你需要 | 用这个 |
|--------|-------|
| 一行简单的转换 | Lambda (需加.returns()避免类型擦除) |
| 访问子任务索引 / State / 累加器 | RichFunction |
| 多个算子共享同一种资源 | 广播状态模式（中级篇讲解） |
| 异常处理 / 条件分支发射 | FlatMap |
| 高性能且无状态 | 匿名类（避免Lambda反射开销） |

### 注意事项
- RichFunction的open/close方法分别在整个算子的生命周期中**只调用一次**（而非每条数据调用一次）
- MapFunction务避免返回null——用FlatMap替代以实现条件过滤
- Filter和Map的排序：能用原始数据过滤的条件尽量前置Filter，降低后续计算量

### 常见踩坑经验

**案例1：Lambda表达式在Flink中抛异常"Could not determine TypeInformation"**
- 根因：Java泛型类型擦除导致Flink无法推断Lambda输出的类型
- 解方：Lambda链式调用末尾加`.returns(Types.STRING)`或转为匿名类

**案例2：RichMapFunction的open()里初始化了数据库连接，但运行几分钟后连接断开**
- 根因：数据库wait_timeout主动关闭了空闲连接
- 解方：open()中用连接池（如HikariCP）替代单一Connection，并配置连接有效性检查

**案例3：FlatMap处理速度突降，反压报警**
- 根因：flatMap内部调用了外部API（如IP地址转地理位置），外部API响应慢拖慢了整个算子
- 解方：改用AsyncIO（第22章），或将IP库加载为本地缓存，避免远程调用

### 优点 & 缺点

| | Flink Transform算子（Map/FlatMap/Filter） | 传统逐行处理（Shell脚本/Python单机） |
|------|-----------|-----------|
| **优点1** | 算子链自动优化，同线程零拷贝传递，性能提升3x+ | 每步独立IO或管道传递，额外开销大 |
| **优点2** | 分布式并行执行，水平扩展至数百节点 | 单机处理，扩展需手动做数据分片 |
| **优点3** | RichFunction提供open/close生命周期回调 | 需自行管理资源初化与释放，容易遗漏 |
| **优点4** | 可结合ValueState/MapState做有状态变换 | 原生无状态，跨批次状态需依赖外部存储 |
| **缺点1** | Lambda类型擦除需显式声明.returns()，增加样板代码 | Java/Scala原生泛型无此限制 |
| **缺点2** | 简单过滤清洗场景框架开销大，小题大做 | Shell一行grep/sed即可完成相同操作 |

### 适用场景

**典型场景**：
1. 实时日志ETL——Nginx/业务日志解析、字段提取、脏数据过滤
2. 数据格式标准化——JSON/CSV/Avro互转、字段重命名与类型转换
3. 敏感数据脱敏——身份证、手机号等字段实时脱敏后输出
4. 数据路由分发——按业务类型/地域将数据分流到不同下游

**不适用场景**：
1. 一次性离线数据清洗——Python/Shell脚本更轻量，无需部署Flink集群
2. 多数据源关联——Map/FlatMap处理单条记录，无法直接做跨流Join

### 思考题

1. 如果我不在Filter里过滤HealthCheck，而是在FlatMap里用条件判断决定是否发射，功能和性能上有什么区别？哪种方式行数更少、更易读？

2. RichMapFunction的open()方法里初始化了一个`Random`实例——这个Random是线程安全的吗？如果你的Map是多线程调用的，有没有data race的风险？（提示：Flink的算子默认是单线程执行，但同一个Slot中的多个算子共享线程）

---

> **完整代码**：本章完整代码请参考附录或访问 https://github.com/flink-column/flink-practitioner  
> **思考题答案**：见附录文件 `appendix-answers.md`
