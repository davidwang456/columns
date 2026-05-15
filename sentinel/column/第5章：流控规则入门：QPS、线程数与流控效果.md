# 第5章：流控规则入门：QPS、线程数与流控效果

## 1 项目背景

第 4 章我们接入了 `@SentinelResource` 注解，也配了一条 QPS=2 的流控规则，订单接口终于不是"裸奔"状态了。但好景不长，第一次内部压测就暴露了一堆问题：

小胖发现，每次刚启动服务时前几秒压测结果波动巨大——第一秒通过了 50 个请求，第二秒才被压到 2 个。他以为是 Sentinel 有 Bug，实际是因为流量突然涌入时 Sentinel 需要几秒时间来"预热"统计窗口。

测试团队的同事反馈：把 QPS 从 10 调到 100 之后，服务 CPU 飙到 90%，但实际 Throughput 只有 65——因为线程全堵在数据库连接池等资源上了。这时候线程数限流比 QPS 限流更合适，但团队没人知道怎么选。

更严重的是，运维团队在一次突发流量中观察到：Sentinel 配置的 QPS 阈值是 5000，但服务在 QPS 达到 3000 时就直接 GC 频繁导致不可用了。根因是服务内部有数十个下游依赖，每个请求会调用 3-5 个远程服务——QPS 限流只能控制入口流量，控制不了实际的系统负载。

这些问题都指向一个结论：**流控不是简单的"设个 QPS 数字"就完事了**。你需要理解 QPS 与线程数的本质区别，需要知道直接拒绝、预热、匀速排队三种控制效果各适用于什么场景，还需要结合服务的实际资源消耗来选择合适的流控模式。

## 2 项目设计

**小胖**（看着 JMeter 的压测报告）："大师，为什么我配置 QPS=10，但实际测起来通过了 14 个请求？Sentinel 的计数器不准吗？"

**大师**："不是不准，是统计窗口的问题。Sentinel 用滑动窗口统计，默认窗口长度是 1 秒，分成 2 个格子（每个 500ms）。当 QPS 阈值设为 10 时，Sentinel 看的是'上一个完整窗口 + 当前窗口'的总数。如果格子刚好在窗口中切换，可能会出现短暂的'过冲'。"

**小白**："这就是滑动窗口的精度问题？如果要完全精准，必须用令牌桶算法？"

**大师**："令牌桶的 RateLimiterController 就是 Sentinel 的'匀速排队'模式。它严格按固定速率发令牌，请求拿到令牌才能通过。但代价是请求可能排队等待，延迟增加。"

**小胖**："那 QPS 限流和线程数限流到底怎么选？我测试发现，QPS=10 的时候 CPU 是 30%，线程数=5 的时候 CPU 才 15%。"

**大师**："这就是关键区别。QPS 限流看的是'每秒放行多少个请求'，不管每个请求要多久。线程数限流看的是'同时有多少个请求在处理'。如果你的请求耗时很长（比如 1 秒），设置 QPS=10，那实际上可能同时有 10 个线程在处理。但如果下游服务超时，请求处理时间会从 100ms 变到 5 秒，同一时间堆积的线程数会暴增。"

**小白**："所以线程数限流本质上是保护下游资源（数据库连接、线程池），而 QPS 限流是保护自己的 CPU？"

**大师**："对。我给你一个决策口诀：**如果你的服务是 CPU 密集型（计算多、耗内存），用 QPS 限流；如果服务是 IO 密集型（调下游、读数据库），用线程数限流。** 不确定时，先用 QPS 限流把流量压到一个稳定水位，再用线程数限流做兜底保护。"

**小胖**："那 Warm Up 预热模式呢？我看到有个 `controlBehavior` 参数。"

**大师**："预热模式解决的是'冷启动'问题。服务的 JIT 编译、连接池初始化、缓存预热需要时间。刚启动时并发能力只有阈值的 1/3（默认冷加载因子是 3），然后慢慢恢复到满能力。秒杀活动前的'系统热身'就是这个道理。"

**小胖**："那能不能同时用两种效果？比如'刚启动用 Warm Up，稳定后用匀速排队'？"

**大师**："不能。一个 FlowRule 只能选一种 `controlBehavior`。但你可以用两条规则叠加：一条 Warm Up 规则（count=300, warmUpPeriodSec=30），一条匀速排队规则（count=500, maxQueueingTimeMs=200）。注意——两条规则同时生效时，第一个不通过的拦截请求。实际上你不会想叠加同类型规则，因为阈值会冲突。更常见的做法是：Warm Up 配合系统保护规则兜底。"

**小白**："那如果我想实现'忙时限流 100 QPS，闲时放开到 500 QPS'——这需要动态调整阈值？"

**大师**："对。这超出了单一 FlowRule 的能力，需要外部系统协作。你可以在定时任务中根据当前 CPU 使用率或业务高峰时段动态更新规则。比如 8:00-22:00 阈值设 100，深夜设 500。可以用 Nacos + 定时任务推送，或者 Sentinel 的 Datasource 扩展实现自适应。"

**小胖**："那能不能同时用两种效果？比如'刚启动用 Warm Up，稳定后用匀速排队'？"

**大师**："不能。一个 FlowRule 只能选一种 `controlBehavior`。但你可以用两条规则叠加：一条 Warm Up 规则（count=300, warmUpPeriodSec=30），一条匀速排队规则（count=500, maxQueueingTimeMs=200）。注意——两条规则同时生效时，第一个不通过的拦截请求。实际上你不会想叠加同类型规则，因为阈值会冲突。更常见的做法是：Warm Up 配合系统保护规则兜底。"

**小白**："那如果我想实现'忙时限流 100 QPS，闲时放开到 500 QPS'——这需要动态调整阈值？"

**大师**："对。这超出了单一 FlowRule 的能力，需要外部系统协作。你可以在定时任务中根据当前 CPU 使用率或业务高峰时段动态更新规则。比如 8:00-22:00 阈值设 100，深夜设 500。可以用 Nacos + 定时任务推送，或者 Sentinel 的 Datasource 扩展实现自适应。"

**技术映射**：

- **FlowRule 核心字段**：
  - `resource`：资源名，必填。
  - `grade`：限流阈值类型，`FLOW_GRADE_QPS`（0）或 `FLOW_GRADE_THREAD`（1）。
  - `count`：阈值，QPS 限流时表示每秒最大通过数，线程数限流时表示最大并发线程数。
  - `strategy`：流控模式，`STRATEGY_DIRECT`（直接）、`STRATEGY_RELATE`（关联）、`STRATEGY_CHAIN`（链路）。
  - `controlBehavior`：流控效果，`CONTROL_BEHAVIOR_DEFAULT`（直接拒绝）、`CONTROL_BEHAVIOR_WARM_UP`（预热）、`CONTROL_BEHAVIOR_RATE_LIMITER`（匀速排队）。
  - `warmUpPeriodSec`：预热时长（秒），仅 Warm Up 模式有效。
  - `maxQueueingTimeMs`：最大排队等待时间（毫秒），仅匀速排队模式有效。

## 3 项目实战

### 3.1 环境准备

沿用之前项目结构。提前准备好 JMeter 压测脚本或使用 `wrk`/`hey` 等工具。

### 3.2 分步实现

**步骤一：对比 QPS 限流与线程数限流**

创建两套规则：

```java
@Component
public class FlowRuleConfig {

    @PostConstruct
    public void initRules() {
        List<FlowRule> rules = new ArrayList<>();

        // 规则 1：QPS 限流 —— 保护 CPU 资源
        FlowRule qpsRule = new FlowRule("createOrder")
                .setGrade(RuleConstant.FLOW_GRADE_QPS)
                .setCount(10)                                    // 每秒最多 10 个
                .setControlBehavior(RuleConstant.CONTROL_BEHAVIOR_DEFAULT);
        rules.add(qpsRule);

        // 规则 2：线程数限流 —— 保护数据库连接池（模拟用同一个资源名，实际应用分开）
        // 此处为了方便展示，单独定义一个资源做线程数限流
        FlowRule threadRule = new FlowRule("createOrderSlow")
                .setGrade(RuleConstant.FLOW_GRADE_THREAD)
                .setCount(5)                                     // 最多同时 5 个线程处理
                .setControlBehavior(RuleConstant.CONTROL_BEHAVIOR_DEFAULT);
        rules.add(threadRule);

        FlowRuleManager.loadRules(rules);
    }
}
```

编写两个对比的 Controller 方法：

```java
@RestController
public class FlowCompareController {

    // QPS 限流：请求处理很快（50ms）
    @GetMapping("/order/qps-test")
    @SentinelResource(value = "createOrder", blockHandler = "blockHandler")
    public String qpsTest() throws InterruptedException {
        Thread.sleep(50);   // 模拟快速业务
        return "QPS 模式通过";
    }

    // 线程数限流：请求处理很慢（2 秒）
    @GetMapping("/order/thread-test")
    @SentinelResource(value = "createOrderSlow", blockHandler = "blockHandler")
    public String threadTest() throws InterruptedException {
        Thread.sleep(2000); // 模拟慢速业务（如调用第三方支付）
        return "线程数模式通过";
    }

    public String blockHandler(BlockException e) {
        return "被限流: " + e.getClass().getSimpleName();
    }
}
```

**验证**：用 JMeter 分别对两个接口以 20 线程并发压测，观察：

- `/order/qps-test`：每秒通过量约 10，但因为处理快（50ms），线程得以快速释放，实际同时处理线程数远低于 20。
- `/order/thread-test`：同时只有 5 个线程在处理，即使请求量再大，其余请求立即被拒绝。

**步骤二：体验三种流控效果**

配置三套不同 controlBehavior 的规则：

```java
// 直接拒绝（默认）
FlowRule directRule = new FlowRule("directTest")
        .setGrade(RuleConstant.FLOW_GRADE_QPS)
        .setCount(5)
        .setControlBehavior(RuleConstant.CONTROL_BEHAVIOR_DEFAULT);

// 预热模式（Warm Up）
FlowRule warmUpRule = new FlowRule("warmUpTest")
        .setGrade(RuleConstant.FLOW_GRADE_QPS)
        .setCount(20)                                        // 稳定后阈值
        .setControlBehavior(RuleConstant.CONTROL_BEHAVIOR_WARM_UP)
        .setWarmUpPeriodSec(10);                             // 10 秒预热期

// 匀速排队（Rate Limiter）
FlowRule rateLimitRule = new FlowRule("rateLimitTest")
        .setGrade(RuleConstant.FLOW_GRADE_QPS)
        .setCount(5)                                         // 每秒 5 个 = 每 200ms 一个
        .setControlBehavior(RuleConstant.CONTROL_BEHAVIOR_RATE_LIMITER)
        .setMaxQueueingTimeMs(500);                          // 最多排队 500ms
```

编写对应的测试接口：

```java
@GetMapping("/flow/direct")
@SentinelResource(value = "directTest", blockHandler = "blockHandler")
public String directMode() {
    return "直接拒绝模式";
}

@GetMapping("/flow/warmup")
@SentinelResource(value = "warmUpTest", blockHandler = "blockHandler")
public String warmUpMode() {
    return "预热模式（10秒预热期）";
}

@GetMapping("/flow/ratelimit")
@SentinelResource(value = "rateLimitTest", blockHandler = "blockHandler")
public String rateLimitMode() {
    return "匀速排队模式（每200ms放一个）";
}
```

**验证方法**：

1. **直接拒绝**：用 `ab -n 20 -c 10 http://localhost:8090/flow/direct` 压测，超过 5 QPS 的请求立即返回 BlockException。

2. **预热模式**：重启服务后立即用 JMeter 持续压测 `/flow/warmup`，观察前 10 秒的 Throughput 从 ~6.7 QPS（20/3）逐渐上升到 20 QPS。可以用 Sentinel 日志确认：

```bash
# 监控 ~/logs/csp/sentinel-record.log
tail -f ~/logs/csp/sentinel-record.log | grep warmUpTest
```

3. **匀速排队**：用 `curl` 在 1 秒内快速发 10 个请求：

```bash
for i in {1..10}; do curl -w "\n时间: %{time_total}s\n" -s http://localhost:8090/flow/ratelimit & done
```

观察每个请求的响应时间：前 5 个在 200ms 内返回，第 6-7 个需要排队等待（约 200ms 间隔），超过 500ms 没拿到令牌的直接被拒绝。

**步骤三：使用 Dashboard 动态配置规则**

比代码配置更直观的方式：

1. 进入 Dashboard → 流控规则 → 新增。
2. 资源名填写 `createOrder`，阈值类型选 QPS，阈值填 10。
3. 流控模式选"直接"，流控效果分别切换"快速失败""Warm Up""排队等待"。
4. 每种效果配置后用 JMeter 快速压测验证。

**步骤四：容量评估与阈值推导**

本章实战的重点不是配一个数字，而是知道这个数字怎么算出来的：

```text
假设：
- 单台 4C8G 服务器
- 单次请求平均耗时 200ms（含下游调用）
- 目标 CPU 使用率 < 60%
- 压测数据：纯 CPU 消耗下每个请求约 5ms CPU 时间

推导：
- CPU 时间上限：4 核 × 1000ms × 60% = 2400ms CPU 时间/秒
- 最大 QPS（纯 CPU）：2400 / 5 = 480 QPS
- 考虑 IO 等待 + GC 开销：480 × 70% = 336 QPS
- 保守值：336 × 80% = 268 QPS
- 安全阈值建议：250 QPS（保留余量）
- 线程数限流建议：250 QPS × 200ms / 1000 = 50 并发线程（取 40 作为安全值）
```

**步骤五：预热效果验证——观察 Sentinel 内部日志**

```bash
# 终端 1：启动服务后立即监控日志
tail -f ~/logs/csp/sentinel-record.log | grep "warmUpTest"

# 终端 2：持续压测
wrk -t4 -c10 -d30s http://localhost:8090/flow/warmup

# 日志中会看到类似输出：
# [INFO] warmUpTest threshold cold start: 6.7 QPS (coldFactor=3)
# [INFO] warmUpTest threshold warming: 10.0 QPS (3s elapsed)
# [INFO] warmUpTest threshold stable: 20.0 QPS (warm up complete)
```

如果日志中始终停留在 coldFactor 阈值不增长，检查 `warmUpPeriodSec` 是否设置正确。

**踩坑记录**：

1. **QPS 过冲**：由于滑动窗口的统计延迟，短时间内可能出现 QPS 超出阈值 ~20% 的情况。这是正常的，Sentinel 不会在微秒级精确限流。如果要求极其精确，用匀速排队模式。

2. **Warm Up 的冷加载因子**：默认是 3，即冷启动时阈值 = count / 3。如果设置 count=300，冷启动时只有 100 QPS。这个值可以通过 `WarmUpController` 源码中的 `coldFactor` 调整（需要自定义 Slot 或反射修改）。

3. **匀速排队与超时**：`maxQueueingTimeMs` 表示请求最多排队等多久。如果设为 0，几乎等同于直接拒绝。建议根据接口的 P99 延迟来设定：排队时间 + 处理时间 < 接口 SLO。

4. **线程数限流与容器线程池**：如果 Tomcat 容器线程池只有 200 个线程，但你设置线程数限流为 300，那限制根本不会触发——因为容器的 200 线程先限制了并发。线程数限流的 count 应该小于容器线程池大小。

## 4 项目总结

### 4.1 优点与缺点

| 流控模式 | 适用场景 | 优点 | 缺点 |
|---------|---------|------|------|
| QPS 直接拒绝 | CPU 密集型，对延迟敏感 | 简单直接，响应快 | 无法保护下游慢资源 |
| QPS 预热 | 刚启动的服务、缓存冷启动 | 防止冷启动崩溃 | 预热期内可能容量不足 |
| QPS 匀速排队 | 定时任务、削峰填谷 | 流量平滑，不丢请求 | 排队增加延迟 |
| 线程数限流 | IO 密集型、数据库连接保护 | 精确控制并发 | 依赖实际处理耗时 |

### 4.2 适用场景

- **QPS 直接拒绝**：秒杀下单、验证码发送、短信接口等对延迟敏感且处理快的接口
- **QPS 预热**：每天第一次大流量接入（早高峰），或新发布的服务需要 JIT 预热
- **QPS 匀速排队**：和外部有固定速率 SLA 的 API、定时批处理任务
- **线程数限流**：涉及数据库连接池、Redis 连接池、第三方支付等 IO 密集型场景
- **不适用**：毫秒级以下延迟要求（滑动窗口开销 ~1μs 可忽略）；异步非阻塞场景（需用 `SphU.asyncEntry`）

### 4.3 注意事项

1. QPS 阈值不是越高越好——高于服务实际处理能力会导致请求堆积，RT 升高。
2. 线程数限流 + 流控效果是互斥的：线程数限流不支持 Warm Up 和匀速排队。
3. Dashboard 配置的规则是内存态的，重启丢失，生产环境务必配合 Nacos/Apollo 做持久化。
4. 不要在同一资源上同时配置代码加载规则和 Dashboard 规则，可能互相覆盖或导致规则集合不确定。

### 4.4 流控效果选型决策表

| 场景特征 | 推荐流控效果 | 关键参数 | 注意事项 |
|---------|------------|---------|---------|
| 对延迟敏感（如验证码发送） | 直接拒绝 | count=QPS 上限 | 超阈值立即拒绝，RT 最低 |
| 服务刚启动/发布后 | Warm Up | count=满阈值, warmUpPeriodSec=10~30 | 冷启动期内容量为 count/3 |
| 需削峰填谷（如定时任务） | 匀速排队 | count=速率, maxQueueingTimeMs=200~500 | 排队增加延迟，需容忍 |
| IO 密集型（数据库/Redis） | 线程数限流 | count=最大并发线程 | 不支持 Warm Up/排队 |
| CPU 密集型（计算/编码） | QPS + 直接拒绝 | count=CPU 核数 × 系数 | 简单可靠 |

### 4.5 常见踩坑经验

| 故障现象 | 根因 | 解决方案 |
|---------|------|---------|
| 压测 QPS 超过阈值 | 滑动窗口统计误差 + 多线程竞争 | 接受 10-20% 误差，或改用匀速排队 |
| 预热期内服务被流量打挂 | count 设置过高，冷加载因子 3 仍不足 | 降低 count 或延长预热时间 |
| 匀速排队模式大量超时 | maxQueueingTimeMs 设置过短 | 根据接口 P99 调整排队时间 |
| 线程数限流实际不生效 | 容器线程池大小限制了实际并发数 | 线程数限流阈值 < 容器线程池大小 |

### 4.6 思考题

1. 为什么 Sentinel 的线程数限流不支持 Warm Up 和匀速排队模式？（提示：思考线程数限流的统计机制）
2. 如果服务部署在 Kubernetes 中，Pod 的 CPU Limit 是 2 核，但实际使用只有 0.5 核。你用 QPS 限流还是线程数限流更合理？为什么？

### 4.7 推广计划

- **开发团队**：制定"流控阈值选择指南"——规定不同类型的接口应使用哪种流控模式。
- **测试团队**：对每种流控模式设计标准压测用例（预热验证、匀速排队验证、线程数限制验证）。
- **运维团队**：结合监控系统（Prometheus）的 CPU/线程数指标，在压测后给出阈值建议。
