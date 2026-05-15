# 第1章：Sentinel 术语全景与流量防护工作原理

## 1 项目背景

某电商公司的"新品秒杀"活动即将上线，订单服务团队压力巨大。往年双十一，系统在峰值流量下多次出现 CPU 飙升、接口超时、数据库连接池耗尽等问题，最严重的一次导致整个订单服务宕机 17 分钟，直接损失超过 200 万元。运维团队在复盘时发现：虽然前端做了验证码和排队，但后端没有任何流量防护措施——单个用户可以用脚本在 1 秒内发起上百次下单请求，库存服务的慢查询也会拖垮整个调用链。

开发经理在会上拍板："这次秒杀活动，订单服务必须引入流量治理能力。限流、熔断、降级这三个能力，至少要落地两个。"

团队面临的核心问题是：面对 Sentinel、Hystrix、Resilience4j、Guava RateLimiter 等多个选择，到底用哪一个？Sentinel 的"资源"指的是什么？"熔断"和"降级"有什么区别？Dashboard 是必须的吗？这些术语如果不统一，团队沟通成本极高，甚至可能导致规则配置错误引发线上事故。

本章作为专栏开篇，将统一 Sentinel 的核心术语体系，用架构图展示其工作流程，帮你建立从"不知道 Sentinel 是什么"到"能画出流量防护架构图"的第一块认知基石。

## 2 项目设计

**小胖**（啃着薯片）："大师，咱们要做限流对吧？这不就跟食堂打饭排队一样吗？门口放个保安，一次放 10 个人进去，简单粗暴！"

**大师**："你这个比喻挺好，但食堂排队只解决了'入口人数控制'。你有没有想过，万一某个窗口的红烧肉师傅突然肚子疼走了，排队的人是不是全堵在窗口了？后面打青菜的人也被影响了。"

**小白**（放下手中的书）："这就是级联故障。上游依赖出问题，请求堆积导致线程池耗尽，最终整个服务不可用。"

**大师**："没错。Sentinel 做的事情，就是把'保安放人'（限流）、'检测红烧肉窗口是否正常'（熔断）、'红烧肉没了换回锅肉'（降级）这三件事系统性地做出来。"

**小胖**："等等，限流和熔断不就是一回事吗？都是不让请求进去。"

**小白**："不一样的。限流是'你正常我也控制数量'，熔断是'你不正常我暂时切断'。限流是被调用方主动保护自己，熔断是调用方检测到下游异常后主动断开。"

**大师**："说得对。Sentinel 里的概念我帮你们整理一下。"

**技术映射 1**：
- **资源（Resource）**：食堂里的"打饭窗口"，是 Sentinel 要保护的东西。可以是接口、方法、甚至一段代码。
- **Entry**：排到你的时候拿到的"入场券"，离开时必须归还（exit），否则后面的人进不来。
- **Context**：你今天从哪个门进来、经过哪些路线，这就是调用上下文。
- **Slot**：食堂里每道关卡（量体温、查人数、看菜品），每个 Slot 做一件事，串起来就是 Slot Chain。

**小白**："那 Node 和 ClusterNode 呢？我翻源码看到这两个概念。"

**大师**："好问题。想想食堂的统计员——DefaultNode 统计某个窗口从不同路线来的人流，ClusterNode 统计某个窗口的总人流，不区分路线。"

**小胖**（挠头）："等等，什么叫'从不同路线来的人流'？食堂不就一个门口吗？"

**小白**："不对。你可以从正门进，也可以从侧门进，或者从外卖通道进来——虽然最终都是打饭，但来源不同，走的路线也不同。在 Sentinel 里，同一个资源可能被不同的上层入口调用，DefaultNode 按入口区分，ClusterNode 汇总所有入口。"

**大师**："没错。举个例子：`/order/create` 这个资源，可能被网关层的 `/api/order` 调用，也可能被定时任务 `OrderCleanupJob` 调用。DefaultNode 能区分这两种来源的流量——你可以对来自定时任务的调用配更高的阈值，对来自用户端的调用配更低的阈值。"

**小胖**："那 Rule 就是保安手里的规则手册了？QPS 不超过 10，线程数不超过 5..."

**大师**："是的。BlockException 就是保安拦住你时给的理由，Fallback 是拦住后给你的补偿方案（比如发优惠券让你稍后再来）。Dashboard 就是食堂监控室，能看到所有窗口的实时状态。"

**小胖**（突然想到一个问题）："大师，那如果我又想按 QPS 限流，又想按线程数限流——两个规则能同时配在同一个资源上吗？以谁为准？"

**大师**："能同时配，它们是 OR 关系——任一条件满足就拦截。QPS 超了或者线程数超了，都会触发。所以同时配的时候要注意：线程数阈值不要设得太低，否则 QPS 还没到就被线程数拦截了。"

**小白**："我对比过 Hystrix，它线程池隔离很重，而且已经停止维护了。Resilience4j 很轻量但缺控制台。Guava RateLimiter 只是单机令牌桶。"

**大师**："这正是我选 Sentinel 的原因。它的 Slot Chain 架构可以插拔扩展，Dashboard 提供可视化管控，支持 QPS/线程数/预热/排队等多种流控效果，还能做热点参数限流和系统自适应保护。"

**小白**："那 Sentinel 的统计精度怎么样？我听说滑动窗口统计有误差。"

**大师**："Sentinel 用的是固定时间窗口的滑动窗口，默认 1 秒一个窗口、共 60 个窗口。精度在 ±10% 左右，对于流量防护场景完全够用。如果你需要精确到每条请求的计数器，那就得用 Redis 等外部存储了——但性能开销会大很多。Sentinel 的设计哲学是'高性能优先，容忍一定误差'。"

**技术映射 2**：
- Sentinel 工作流程可概括为：**定义资源 → 采集统计 → 规则判断 → 放行或阻断 → 兜底处理**。
- 每次请求进入 `SphU.entry("资源名")` 时，Sentinel 创建 Entry，沿 Slot Chain 逐个执行：NodeSelectorSlot 构建调用树 → ClusterBuilderSlot 创建集群节点 → StatisticSlot 记录指标 → FlowSlot/DegradeSlot/SystemSlot 做规则校验 → 通过则执行业务逻辑，阻断则抛 BlockException。

```
flowchart LR
    Client[客户端请求] --> Resource[被保护的资源]
    Resource --> Entry[SphU Entry]
    Entry --> SlotChain[Processor Slot Chain]
    SlotChain --> Statistic[StatisticSlot 指标统计]
    SlotChain --> RuleCheck[Flow/Degrade/Param/System 规则校验]
    RuleCheck -->|Pass| Business[业务逻辑]
    RuleCheck -->|Block| Fallback[BlockHandler / Fallback]
    Dashboard[Sentinel Dashboard] --> RuleSource[规则数据源]
    RuleSource --> RuleCheck
    MetricLog[指标日志] --> Dashboard
    Statistic --> MetricLog
```

## 3 项目实战

### 3.1 环境准备

| 组件 | 版本 | 用途 |
|------|------|------|
| JDK | 17 | 运行环境 |
| Maven | 3.8+ | 构建工具 |
| Spring Boot | 3.2.x | 应用框架 |
| Sentinel Core | 1.8.6 | 核心限流库 |
| JMeter | 5.6 | 压测工具 |

创建 Spring Boot 项目，添加依赖：

```xml
<!-- pom.xml -->
<dependencies>
    <dependency>
        <groupId>org.springframework.boot</groupId>
        <artifactId>spring-boot-starter-web</artifactId>
    </dependency>
    <dependency>
        <groupId>com.alibaba.csp</groupId>
        <artifactId>sentinel-core</artifactId>
        <version>1.8.6</version>
    </dependency>
</dependencies>
```

### 3.2 分步实现

**步骤一：创建订单接口并手工接入 Sentinel**

编写一个最简 Controller，用 `SphU.entry()` 手动保护接口：

```java
@RestController
public class OrderController {

    @GetMapping("/order/create")
    public String createOrder(@RequestParam String skuId) {
        // 1. 定义资源并进入
        Entry entry = null;
        try {
            entry = SphU.entry("createOrder");
            // 2. 业务逻辑：模拟耗时 50ms
            Thread.sleep(50);
            return "订单创建成功, skuId=" + skuId;
        } catch (BlockException e) {
            // 3. 被限流/熔断后的兜底处理
            return "系统繁忙，请稍后再试！";
        } catch (Exception e) {
            return "业务异常：" + e.getMessage();
        } finally {
            // 4. 必须 exit，否则统计出错
            if (entry != null) {
                entry.exit();
            }
        }
    }
}
```

**步骤二：加载流控规则**

在 `main` 方法或 `@PostConstruct` 中初始化规则：

```java
@SpringBootApplication
public class OrderApplication {
    public static void main(String[] args) {
        SpringApplication.run(OrderApplication.class, args);
        initFlowRules();
    }

    private static void initFlowRules() {
        List<FlowRule> rules = new ArrayList<>();
        FlowRule rule = new FlowRule();
        rule.setResource("createOrder");   // 资源名，与 entry 一致
        rule.setGrade(RuleConstant.FLOW_GRADE_QPS); // QPS 限流模式
        rule.setCount(2);                  // 阈值：每秒最多 2 个请求
        rules.add(rule);
        FlowRuleManager.loadRules(rules);
    }
}
```

**步骤三：验证正常请求**

启动应用，用 curl 快速请求 3 次：

```bash
$ curl http://localhost:8080/order/create?skuId=1001
订单创建成功, skuId=1001
$ curl http://localhost:8080/order/create?skuId=1001
订单创建成功, skuId=1001
$ curl http://localhost:8080/order/create?skuId=1001
系统繁忙，请稍后再试！
```

第三次请求被拦截，返回了 BlockException 的兜底文案。

**步骤四：JMeter 压测验证**

创建 JMeter 测试计划：线程组（10 线程，循环 10 次）→ HTTP 请求 `/order/create?skuId=1001` → 查看结果树和聚合报告。

启动压测后，聚合报告显示：100 次请求中约 80% 被拒绝（取决于机器性能），Throughput 被压制在 2/sec 左右。

**步骤五：验证 QPS 与线程数规则同时生效**

在 `initFlowRules()` 中添加线程数规则：

```java
private static void initFlowRules() {
    List<FlowRule> rules = new ArrayList<>();

    FlowRule qpsRule = new FlowRule();
    qpsRule.setResource("createOrder");
    qpsRule.setGrade(RuleConstant.FLOW_GRADE_QPS);
    qpsRule.setCount(2);
    rules.add(qpsRule);

    FlowRule threadRule = new FlowRule();
    threadRule.setResource("createOrder");
    threadRule.setGrade(RuleConstant.FLOW_GRADE_THREAD);
    threadRule.setCount(3);  // 最多 3 个并发线程
    rules.add(threadRule);

    FlowRuleManager.loadRules(rules);
}
```

验证——在 Controller 中增加慢接口：

```java
@GetMapping("/order/create-slow")
public String createOrderSlow(@RequestParam String skuId) {
    try (Entry entry = SphU.entry("createOrder")) {
        Thread.sleep(2000); // 模拟慢业务，占用线程
        return "订单创建成功, skuId=" + skuId;
    } catch (BlockException e) {
        return "系统繁忙，请稍后再试！（限流/线程数超限）";
    } catch (Exception e) {
        return "业务异常：" + e.getMessage();
    }
}
```

用 JMeter 5 个线程并发访问 `/order/create-slow`，前 3 个进入业务逻辑，后 2 个被线程数规则拦截——即使 QPS 还没到 2。

**步骤六：对比 RuleConstant 中的流控效果**

| 常量 | 值 | 含义 | 触发条件 |
|------|---|------|---------|
| `FLOW_GRADE_QPS` | 1 | QPS 限流 | 每秒请求数超过 count |
| `FLOW_GRADE_THREAD` | 0 | 线程数限流 | 当前并发执行的线程数超过 count |
| `CONTROL_BEHAVIOR_DEFAULT` | 0 | 快速失败 | 直接抛 FlowException |
| `CONTROL_BEHAVIOR_WARM_UP` | 1 | 预热 | 逐步提升阈值到 count |
| `CONTROL_BEHAVIOR_RATE_LIMITER` | 2 | 匀速排队 | 请求排队等待，平滑通过 |

**踩坑记录**：
1. **资源名不一致**：`SphU.entry("createOrder")` 与 `rule.setResource("createOrder")` 必须完全一致（包括大小写），否则规则不生效。
2. **忘记 exit()**：如果在 finally 中不调用 `entry.exit()`，Sentinel 统计窗口中的数据不会正确回收，后续请求可能被"误杀"。
3. **规则未加载**：代码初始化规则要在首次请求前完成，如果用了懒加载，首次请求可能不会被拦截。
4. **QPS 与线程数同时配置**：两者为 OR 关系，注意线程数阈值不能低于正常业务的并发度，否则会误杀。

### 3.3 手动绘制 Sentinel 架构图

建议在本地用 draw.io 或直接在纸上画出以下组件关系：

```
请求 ──> SphU.entry("资源名")
              │
              ▼
        ContextUtil.enter()
              │
              ▼
        SlotChain 责任链
         ├── NodeSelectorSlot    (构建调用树节点)
         ├── ClusterBuilderSlot  (创建/获取 ClusterNode)
         ├── LogSlot             (记录 Block 日志)
         ├── StatisticSlot       (滑动窗口统计)
         ├── AuthoritySlot       (黑白名单)
         ├── SystemSlot          (系统自适应)
         ├── FlowSlot            (流控规则)
         ├── DegradeSlot         (熔断规则)
         └── ParamFlowSlot       (热点参数)
              │
              ▼
        RuleCheck ──(通过)──> 业务逻辑 ──> entry.exit()
              │
            (阻断)
              ▼
         BlockException ──> Fallback/BlockHandler
```

## 4 项目总结

### 4.1 优点与缺点

| 维度 | Sentinel | Hystrix | Resilience4j | Guava RateLimiter |
|------|----------|---------|-------------|-------------------|
| 架构设计 | Slot Chain 责任链，可插拔 | 线程池/信号量隔离 | 函数式组合 | 单机令牌桶 |
| 流控效果 | QPS/线程/WarmUp/排队 | 信号量+超时 | RateLimiter/Bulkhead | 平滑突发/预热 |
| 熔断策略 | 慢调用/异常比例/异常数 | 异常比例 | 慢调用/异常比例 | 不支持 |
| 控制台 | Dashboard 可视化 | Dashboard（已停维） | 无 | 无 |
| 维护状态 | 活跃 | 停维 | 活跃 | 活跃 |
| 热点参数限流 | 支持 | 不支持 | 不支持 | 不支持 |
| 系统自适应保护 | 支持 | 不支持 | 不支持 | 不支持 |

### 4.2 适用场景

- **高并发网关/API 入口**：QPS 限流 + Warm Up 保护后端服务"热身"
- **微服务间调用**：OpenFeign/Dubbo 集成，熔断防止故障扩散
- **秒杀/大促**：热点参数限流保护热门商品接口
- **多租户 SaaS 平台**：按租户 AppId 做授权规则 + 资源隔离
- **不适用场景**：纯异步事件驱动架构（需额外适配）；对延迟极度敏感的金融交易链路（滑动窗口统计本身有纳秒级开销，需压测评估）

### 4.3 注意事项

1. **规则持久化**：代码直接 `loadRules` 的规则在应用重启后会丢失，生产环境务必配合 Nacos/Apollo 做动态规则。
2. **资源粒度**：粒度太细（如按 userId 建资源）会导致 Node 数量膨胀，引起内存问题。
3. **版本兼容**：Sentinel 1.8.x 与 Spring Cloud Alibaba 2023.x 配合使用时需确认适配关系。

### 4.4 常见踩坑经验

| 故障现象 | 根因 | 解决方案 |
|---------|------|---------|
| 流量打满后服务直接 503 | 未引入 Sentinel，网关裸奔 | 接入 Sentinel + Gateway Adapter |
| 规则配置后未生效 | 资源名拼写不一致 | 统一用常量定义资源名 |
| Dashboard 看不到应用 | 客户端未配置 `csp.sentinel.dashboard.server` | 检查 application.yml 配置 |
| 同时配 QPS 和线程数造成大量误杀 | 线程数阈值设太低，QPS 还没到就被拦截 | 线程数阈值 > 正常业务并发度 × 1.5 |
| 滑动窗口统计的 pass 数不等于实际通过数 | 窗口边界效应导致统计偏差 | 观察 5 秒以上窗口的平均值，而非单秒数据 |

### 4.5 Sentinel 规则决策快速指南

| 你的需求 | 用什么规则 | 关键配置 |
|---------|-----------|---------|
| 保护接口不被流量打爆 | FlowRule (QPS) | count=拐点QPS×0.8 |
| 防止单个慢请求拖垮线程池 | FlowRule (线程数) | count=连接池大小×0.8 |
| 下游服务出问题自动切断 | DegradeRule | timeWindow≤30s |
| 某个商品太火爆需要控制 | ParamFlowRule | paramIdx=0, count=50 |
| 防止机器过载崩溃 | SystemRule | highestCpuUsage=0.8 |
| 只允许内部服务调用 | AuthorityRule | strategy=白名单, limitApp=internal-gateway |

### 4.6 核心术语速查表

| 术语 | 英文 | 类比 | 一句话解释 |
|------|------|------|-----------|
| 资源 | Resource | 食堂窗口 | Sentinel 保护的目标 |
| Entry | Entry | 入场券 | 进入资源的凭证，必须 exit |
| 上下文 | Context | 行走路线 | 一次调用链的上下文信息 |
| Slot | Slot | 关卡 | 责任链中的一环，负责一个判断 |
| 流控 | Flow Control | 限流 | 控制资源访问速率 |
| 熔断 | Circuit Breaking | 保险丝 | 下游异常时自动切断调用 |
| 降级 | Fallback | 备用方案 | 阻断后的兜底逻辑 |
| 热点 | Hotspot | 最热门窗口 | 被频繁访问的某个参数值 |

### 4.7 思考题

1. 如果一个资源同时配置了流控规则（QPS=10）和熔断规则（异常比例>50%），当 QPS 达到 12 时，请求会被谁拦截？为什么？
2. Sentinel 的 StatisticSlot 在 Slot Chain 中位于 FlowSlot 之前还是之后？这个顺序为什么重要？

**提示**：答案将在第 2 章环境搭建验证和第 33 章源码剖析中揭晓。

### 4.8 推广计划

- **开发团队**：全体必读，统一术语理解。建议结合团队内部 Wiki 建立"Sentinel 术语词典"。
- **测试团队**：重点理解 BlockException 的触发条件，为后续压测和验收用例设计打基础。
- **运维团队**：了解 Sentinel 的整体架构，后续配合搭建 Dashboard 和监控体系。
