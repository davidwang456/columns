# 第13章：Web 场景实战：保护电商下单链路

## 1 项目背景

第 1-12 章我们把 Sentinel 的每种能力单独学了一遍——限流、熔断、热点参数、系统保护、授权规则、日志分析。但在真实项目中，这些能力是组合使用的。

我们以一个**电商下单链路**为例。一个典型的电商下单流程涉及多个环节：

```
用户点击"立即购买" → 校验库存 → 创建订单 → 扣减库存 → 发起支付
```

每个环节都面临不同的风险：
- **校验库存**：所有用户都要调，流量大，需要 QPS 限流
- **创建订单**：核心写操作，需要防刷（热点用户限流） + 防下游超时（熔断）
- **扣减库存**：涉及事务，需要线程数限流（保护数据库连接池）
- **发起支付**：外部第三方接口，不稳定的依赖，需要主动熔断 + 降级返回

而且还要考虑"连带影响"——库存服务变慢拖慢创建订单、支付回调流量大影响订单查询——需要关联流控和链路流控。

更重要的是，用户体验层面需要做"优雅降级"：不要直接返回 500 错误，而是返回"排队中，请稍后查看订单状态"这样的友好提示。

本章将从零搭建一个完整的最小电商下单 Demo，组合运用前 12 章学到的所有 Sentinel 规则类型。

## 2 项目设计

**小胖**（看着需求文档）："这个下单流程有 5 个步骤，我应该给每个步骤都加一个 @SentinelResource 吗？"

**大师**："是的，但要分层。入口 Controller 用注解做限流提示，内部 Service 用注解或 API 做熔断兜底。记住一个原则：**入口管流量，内部管故障**。"

**小白**："我刚配了库存服务的熔断规则，但发现熔断触发后，订单还是被创建了（只是库存扣减失败了）。这样数据不一致怎么办？"

**大师**："这就是降级策略的问题。熔断不等于业务回滚。你需要区分两种降级：一是只读操作的降级（返回缓存数据或默认值），二是写操作的降级（返回'操作已排队，稍后处理'，并配合消息队列做最终一致性）。本章会展示这两种策略。"

**小胖**："那用户体验怎么设计？总不能用户看到'FlowException'就懵了吧。"

**大师**："用户体验分三层：限流时返回'排队中'并给个排队号，熔断时返回'系统繁忙'并自动重试，异常时返回'订单处理中'并通过消息通知。这比直接抛 500 好得多。"

**技术映射**：
- **规则分层设计**：Controller 层配 QPS + 授权规则（管入口）→ Service 层配熔断 + 热点参数（管依赖）→ 全局配系统保护（管整机）。
- **降级分层策略**：blockHandler 处理"被限流/熔断"时的临时替代 → fallback 处理"业务异常"时的兜底 → 全局异常处理器兜底"未预期的错误"。
- **幂等性考虑**：在 blockHandler/fallback 中创建排队记录时，必须用 orderId 做幂等键，防止重试时重复创建。

## 3 项目实战

### 3.1 环境准备

项目结构：

```
order-service/
├── controller/
│   └── OrderController.java      # 入口 Controller
├── service/
│   ├── OrderService.java         # 下单编排服务
│   ├── InventoryService.java     # 库存服务（调用下游）
│   └── PaymentService.java      # 支付服务
├── fallback/
│   └── OrderFallbackHandler.java # 统一降级处理
├── rule/
│   └── SentinelRuleConfig.java   # 规则配置
└── model/
    └── OrderRequest.java
```

### 3.2 分步实现

**步骤一：下单链路 Controller 层**

```java
@RestController
@RequestMapping("/order")
public class OrderController {

    @Autowired
    private OrderService orderService;

    /**
     * 下单入口 —— 配 QPS 限流 + 来源授权
     */
    @PostMapping("/create")
    @SentinelResource(
        value = "createOrder",
        blockHandler = "createOrderBlock",
        fallback = "createOrderFallback"
    )
    public Result<OrderResponse> createOrder(@RequestBody OrderRequest request) {
        // 委托给 Service 层处理
        OrderResponse response = orderService.createOrder(request);
        return Result.success(response);
    }

    /**
     * 限流/熔断时的处理
     */
    public Result<OrderResponse> createOrderBlock(OrderRequest request,
                                                   BlockException e) {
        log.warn("下单被 Sentinel 拦截, userId={}, skuId={}, exception={}",
                 request.getUserId(), request.getSkuId(), e.getClass().getSimpleName());

        // 区分不同类型的阻断，返回不同提示
        if (e instanceof FlowException) {
            // 限流：返回排队号
            String queueId = orderService.enqueue(request);
            return Result.error("FLOW_LIMIT",
                "当前下单人数过多，您已进入排队（排队号：" + queueId + "），请稍后查看订单状态");
        } else if (e instanceof DegradeException) {
            // 熔断：提示系统繁忙
            return Result.error("SERVICE_BUSY",
                "系统繁忙，您的订单稍后将自动处理，请勿重复提交");
        } else if (e instanceof ParamFlowException) {
            // 热点参数（用户维度）：提示操作频繁
            return Result.error("USER_LIMIT",
                "您的操作过于频繁，请 30 秒后再试");
        }
        return Result.error("BLOCK", "系统繁忙，请稍后再试");
    }

    /**
     * 业务异常时的兜底
     */
    public Result<OrderResponse> createOrderFallback(OrderRequest request,
                                                      Throwable t) {
        log.error("下单业务异常, userId={}, skuId={}",
                  request.getUserId(), request.getSkuId(), t);
        return Result.error("BIZ_ERROR", "下单失败，请稍后重试");
    }
}
```

**步骤二：Service 层编排逻辑**

```java
@Service
public class OrderService {

    @Autowired
    private InventoryService inventoryService;
    @Autowired
    private PaymentService paymentService;

    /**
     * 下单编排：校验库存 → 创建订单 → 扣减库存 → 发起支付
     */
    public OrderResponse createOrder(OrderRequest request) {
        // 1. 校验库存（内部调用 inventoryService，有独立熔断保护）
        int stock = inventoryService.queryStock(request.getSkuId());
        if (stock < request.getQuantity()) {
            throw new BizException("库存不足");
        }

        // 2. 创建订单记录（本地事务）
        String orderId = createOrderRecord(request);

        // 3. 扣减库存（内部调用，有独立熔断 + 线程数限流）
        boolean deducted = inventoryService.deductStock(
            request.getSkuId(), request.getQuantity());
        if (!deducted) {
            // 扣减失败，标记订单为"待处理"
            markOrderPending(orderId);
            throw new BizException("库存扣减失败，订单 " + orderId + " 已标记待处理");
        }

        // 4. 发起支付（异步，不阻塞返回）
        paymentService.createPaymentAsync(orderId, request);

        return new OrderResponse(orderId, "ORDER_CREATED");
    }

    private String createOrderRecord(OrderRequest request) {
        // 模拟数据库写入
        return "ORD_" + UUID.randomUUID().toString().substring(0, 8);
    }

    private void markOrderPending(String orderId) {
        log.warn("订单 {} 标记为待处理", orderId);
    }

    public String enqueue(OrderRequest request) {
        // 生成排队号
        return "QUEUE_" + System.currentTimeMillis();
    }
}
```

**步骤三：库存服务（带熔断保护）**

```java
@Service
public class InventoryService {

    /**
     * 查询库存 —— 配慢调用熔断 + 热点参数限流
     */
    @SentinelResource(
        value = "queryInventory",
        fallback = "queryInventoryFallback"
    )
    public int queryStock(String skuId) {
        // 模拟远程调用库存服务
        // 实际项目中是 Feign 或 RestTemplate 调用
        simulateRemoteCall(20);
        return 100; // 模拟返回库存
    }

    public int queryInventoryFallback(String skuId, Throwable t) {
        // 降级：返回缓存的库存数据或默认值
        log.warn("查询库存降级, skuId={}", skuId, t);
        return -1; // -1 表示库存未知，业务层据此决定是否放行
    }

    /**
     * 扣减库存 —— 配线程数限流（保护数据库连接池）
     */
    @SentinelResource(
        value = "deductInventory",
        blockHandler = "deductInventoryBlock"
    )
    public boolean deductStock(String skuId, int quantity) {
        simulateRemoteCall(100); // 模拟较慢的写操作
        return true;
    }

    public boolean deductInventoryBlock(String skuId, int quantity,
                                         BlockException e) {
        log.warn("扣减库存被限流, skuId={}, qty={}", skuId, quantity);
        return false; // 返回 false，上游标记订单为待处理
    }

    private void simulateRemoteCall(int delayMs) {
        try {
            Thread.sleep(delayMs);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        }
    }
}
```

**步骤四：统一规则配置**

```java
@Component
public class SentinelRuleConfig {

    @PostConstruct
    public void initAllRules() {
        initFlowRules();
        initDegradeRules();
        initParamFlowRules();
        initAuthorityRules();
    }

    private void initFlowRules() {
        List<FlowRule> rules = new ArrayList<>();

        // 下单入口：QPS 限流 50
        rules.add(new FlowRule("createOrder")
                .setGrade(RuleConstant.FLOW_GRADE_QPS).setCount(50));

        // 扣减库存：线程数限流 10（保护连接池）
        rules.add(new FlowRule("deductInventory")
                .setGrade(RuleConstant.FLOW_GRADE_THREAD).setCount(10));

        // 关联流控：支付回调 QPS 高时限制订单查询
        rules.add(new FlowRule("queryOrder")
                .setGrade(RuleConstant.FLOW_GRADE_QPS).setCount(20)
                .setStrategy(RuleConstant.STRATEGY_RELATE)
                .setRefResource("paymentCallback"));

        FlowRuleManager.loadRules(rules);
    }

    private void initDegradeRules() {
        List<DegradeRule> rules = new ArrayList<>();

        // 查询库存：慢调用比例熔断（RT > 50ms 且 >50% 时熔断 10 秒）
        rules.add(new DegradeRule("queryInventory")
                .setGrade(RuleConstant.DEGRADE_GRADE_RT)
                .setCount(50)              // maxAllowedRt = 50ms
                .setSlowRatioThreshold(0.5)
                .setMinRequestAmount(5)
                .setStatIntervalMs(10000)
                .setTimeWindow(10));

        // 发起支付：异常比例熔断
        rules.add(new DegradeRule("createPayment")
                .setGrade(RuleConstant.DEGRADE_GRADE_EXCEPTION_RATIO)
                .setCount(0.3)
                .setMinRequestAmount(5)
                .setStatIntervalMs(10000)
                .setTimeWindow(15));

        DegradeRuleManager.loadRules(rules);
    }

    private void initParamFlowRules() {
        List<ParamFlowRule> rules = new ArrayList<>();

        // 热点用户限流：按 userId（索引 0）限流，默认 5 QPS
        ParamFlowRule userRule = new ParamFlowRule("createOrder")
                .setParamIdx(0).setCount(5);

        // VIP 用户例外：QPS 可以到 20
        ParamFlowItem vipUser = new ParamFlowItem()
                .setObject("VIP_USER_001")
                .setClassType(String.class.getName())
                .setCount(20);
        userRule.setParamFlowItemList(Collections.singletonList(vipUser));
        rules.add(userRule);

        ParamFlowRuleManager.loadRules(rules);
    }

    private void initAuthorityRules() {
        List<AuthorityRule> rules = new ArrayList<>();

        // 管理接口白名单
        AuthorityRule adminRule = new AuthorityRule();
        adminRule.setResource("adminOperation");
        adminRule.setStrategy(RuleConstant.AUTHORITY_WHITE);
        adminRule.setLimitApp("admin-app,internal-gateway");
        rules.add(adminRule);

        AuthorityRuleManager.loadRules(rules);
    }
}
```

**步骤五：JMeter 压测验证**

创建 JMeter 测试计划结构：

```
Test Plan: 电商下单全链路压测
├── Thread Group 1: 正常用户下单 (50 threads, ramp-up 10s, loop forever, 持续 60s)
│   └── HTTP Request: POST /order/create
│       └── JSON Body: {"userId":"NORMAL_${__threadNum}","skuId":"SKU_${__Random(1,10)}","quantity":1}
│
├── Thread Group 2: VIP 用户下单 (10 threads)
│   └── HTTP Request: POST /order/create
│       └── JSON Body: {"userId":"VIP_USER_001","skuId":"VIP_SKU","quantity":1}
│
├── Thread Group 3: 支付回调流量 (30 threads, 在压测中段启动)
│   └── HTTP Request: POST /pay/callback
│
└── Listeners:
    ├── View Results Tree
    ├── Aggregate Report
    ├── Response Time Graph
    └── Assertion Results
```

压测验收标准：

| 验收项 | 标准 | 验证方法 |
|-------|------|---------|
| 下单入口 QPS 不超过 50 | 聚合报告 Throughput < 50 | JMeter 聚合报告 |
| VIP 用户 QPS 可达 20 | VIP 线程组 Throughput > 15 | 单独查看 VIP 线程组 |
| 支付回调高时订单查询被限制 | 支付回调启动后，订单查询 Throughput 下降 | Dashboard 实时监控 |
| 熔断后能自动恢复 | 停止异常注入后 10 秒内恢复 | Dashboard 熔断状态页面 |
| 限流拦截返回友好提示 | 超过阈值后 response body 包含"排队" | Response Assertion |
| 业务异常返回兜底 | 库存不足时返回"库存不足"而非 500 | Response Assertion |

**踩坑记录**：

1. **规则加载顺序**：如果规则加载晚于第一次请求，第一个请求可能绕过规则。建议 `eager=true` 或使用 `@PostConstruct` 确保规则提前加载。
2. **多规则同时生效的冲突**：一个资源配了 5 种规则，可能出现"本来想限流，结果先触发了熔断"。按优先级排序：系统保护 > 授权 > 热点参数 > 流控 > 熔断。实际优先级取决于 Slot Chain 中的执行顺序。
3. **降级逻辑中的幂等性**：blockHandler 中创建了排队记录，如果用户重试就会创建多条。务必用 userId + skuId + 时间窗口做幂等键。

## 4 项目总结

### 4.1 规则组合最佳实践

| 规则类型 | 建议配置位置 | 适用接口类型 | 阈值推导方式 |
|---------|------------|-------------|------------|
| QPS 限流 | Controller | 所有 HTTP 入口 | 压测拐点 QPS × 80% |
| 线程数限流 | Service（含 IO 操作） | 数据库写操作、支付调用 | 连接池大小 × 80% |
| 慢调用熔断 | Service（远程调用） | Feign/Dubbo/HTTP 远程调用 | P99 RT × 2 |
| 异常比例熔断 | Service（外部依赖） | 第三方 API | 根据 SLA 反推 |
| 热点参数限流 | Controller（含业务参数） | 商品详情、用户操作 | Top10 热点参数的日均 QPS × 2 |
| 系统保护 | 全局 | 所有入口流量 | CPU 80% / Load = Cores × 0.8 |
| 授权规则 | Controller（管理接口） | 管理后台、回调接口 | 按来源名单 |

### 4.2 思考题

1. 如果库存服务同时配了慢调用熔断和异常比例熔断，当库存服务频繁超时（慢调用 100%）但从不报错（异常比例 0%），哪个规则会先触发？
2. 在下单链路中，如果"扣减库存"被线程数限流拒绝了（blockHandler 返回 false），上游订单已经创建了——这个订单就是"待处理"状态。如何设计一个后台 Job 自动处理这些待处理订单？需要考虑哪些边界条件？

### 4.3 推广计划

- **开发团队**：以本章的订单 Demo 为模板，为项目中的核心链路搭建类似的 Sentinel 保护方案。
- **测试团队**：基于本章的 JMeter 脚本扩展为全链路压测方案，覆盖所有规则类型的验证。
- **运维团队**：在本章的基础上增加 Prometheus 监控和 Nacos 动态规则，为中级篇做准备。
