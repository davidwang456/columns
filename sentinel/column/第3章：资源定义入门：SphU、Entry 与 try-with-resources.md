# 第3章：资源定义入门：SphU、Entry 与 try-with-resources

## 1 项目背景

第 2 章我们用 `@SentinelResource` 注解快速实现了流控，一行注解就搞定了。但小胖在 Code Review 时被大师问住了："如果不用 Spring，纯 Java 项目怎么接入？如果我想保护的不是 HTTP 接口，而是一段业务逻辑（比如发送短信、扣减积分），注解能行吗？"

更严重的是，测试团队发现了一个 Bug：`@SentinelResource` 注解在 Service 层方法上不生效。排查了很久才发现是因为 Spring AOP 的代理机制——同一个类内部方法调用不会触发代理，AOP 拦截失效。而用 `SphU.entry()` 手动编程式接入完全不受此限制。

再看一个真实故障：某支付系统用注解方式保护了下单接口，但因为 `blockHandler` 方法签名写错了一个参数类型，编译通过但运行时无论怎么触发限流都不走兜底逻辑。最终导致大促时大量用户看到的是 500 错误而非友好的"系统繁忙"提示。

这些问题都指向一个核心能力：**理解 Sentinel 底层 API 的资源定义方式**。`SphU.entry()` 是 Sentinel 一切能力的入口，理解它的生命周期、上下文传播和异常边界，才能在任何场景（Web/非 Web、注解/编程、同步/异步）下正确接入 Sentinel。

## 2 项目设计

**小胖**（挠头）："大师，`@SentinelResource` 不是挺好吗？一行注解省好多代码。为啥还要学底层 API？"

**大师**："你见过哪个开车的人只学自动挡、不学科目二的？底层 API 就是 Sentinel 的'手动挡'，你搞懂它，以后不管什么路况都能开。"

**小白**："我确实在源码里看到 `SphU.entry()` 返回一个 `Entry`，这个东西必须 `exit()` 吗？不 `exit()` 会怎样？"

**大师**："好问题。Entry 本质上是一张'通行证'。想象你去游乐园玩，进门时领证，出门时还证。如果你不还，统计系统就会认为你一直待在园里——对应到 Sentinel，就是并发线程数一直不减少，后续的线程数限流会误判。"

**小胖**："那如果业务代码抛异常了，exit 还会执行吗？"

**大师**："这就是为什么必须用 try-finally。Sentinel 用 `Entry.exit()` 通知 Slot Chain'这个请求完成了'，包括更新统计指标、释放 Context。如果漏掉 exit，会导致线程数指标只增不减，最终触发线程数限流误拦截。"

**小白**："那 Context 呢？我看源码里 `ContextUtil.enter()` 和 `SphU.entry()` 是两个独立调用。"

**大师**："Context 是'调用链的身份证'，记录你从哪个入口进来、经过哪些节点。在 Web 场景下，Sentinel 的 Web MVC Adapter 会自动帮你创建 Context。但在非 Web 的纯 Java 应用中，比如定时任务、MQ 消费者，你必须手动调用 `ContextUtil.enter()`。"

**小胖**："那资源名怎么设计？我看到有些系统用 URL 作为资源名，`/api/v1/order/create`，有些用业务名 `createOrder`，哪个更好？"

**小白**："我倾向于业务命名。URL 可能会随版本变动（v1→v2），但业务动作`创建订单`是稳定的。而且同一个 URL 的 GET 和 POST 应该分开保护，它们对后端压力完全不同。"

**大师**："你们都说得对。资源维度的选择取决于你想保护什么。如果是网关层统一下发限流，用 URL 维度即可；如果是对业务精确控制，用业务动作维度。我建议的策略是：**网关层用路由维度，服务层用业务维度，同一接口的不同 HTTP 方法分开做资源**。"

**技术映射**：
- `SphU.entry(resourceName)`：向 Sentinel 申请进入一个受保护资源，返回 Entry 对象。
- `Entry.exit()`：标记当前资源调用结束，更新统计指标。必须放在 finally 块中。
- `ContextUtil.enter(contextName, origin)`：创建/进入调用上下文，定义入口资源和调用来源。
- BlockException 是 Sentinel 所有阻断异常的父类，其子类包括 FlowException（流控）、DegradeException（熔断）、ParamFlowException（热点参数）、SystemBlockException（系统保护）、AuthorityException（授权）。
- 区别于业务异常（RuntimeException 等），BlockException 应该被单独 catch 并返回"流量管控提示"而非"系统错误"。

## 3 项目实战

### 3.1 环境准备

沿用第 2 章的项目结构。增加纯 Java 测试模块，不依赖 Spring。添加依赖：

```xml
<dependency>
    <groupId>com.alibaba.csp</groupId>
    <artifactId>sentinel-core</artifactId>
    <version>1.8.6</version>
</dependency>
<dependency>
    <groupId>junit</groupId>
    <artifactId>junit</artifactId>
    <version>4.13.2</version>
    <scope>test</scope>
</dependency>
```

### 3.2 分步实现

**步骤一：创建多资源订单服务**

编写三个业务方法，分别保护"创建订单""查询订单""取消订单"：

```java
public class OrderService {

    private static final String CREATE_ORDER = "createOrder";
    private static final String QUERY_ORDER = "queryOrder";
    private static final String CANCEL_ORDER = "cancelOrder";

    static {
        initRules();
    }

    private static void initRules() {
        List<FlowRule> rules = new ArrayList<>();
        // 创建订单：QPS 限制为 3
        FlowRule createRule = new FlowRule(CREATE_ORDER)
                .setGrade(RuleConstant.FLOW_GRADE_QPS)
                .setCount(3);
        // 查询订单：QPS 限制为 10
        FlowRule queryRule = new FlowRule(QUERY_ORDER)
                .setGrade(RuleConstant.FLOW_GRADE_QPS)
                .setCount(10);
        // 取消订单：线程数限制为 2
        FlowRule cancelRule = new FlowRule(CANCEL_ORDER)
                .setGrade(RuleConstant.FLOW_GRADE_THREAD)
                .setCount(2);
        rules.addAll(Arrays.asList(createRule, queryRule, cancelRule));
        FlowRuleManager.loadRules(rules);
    }

    public String createOrder(String skuId) {
        Entry entry = null;
        try {
            entry = SphU.entry(CREATE_ORDER);
            // 模拟业务逻辑，耗时 100ms
            Thread.sleep(100);
            return "订单创建成功: " + skuId;
        } catch (BlockException e) {
            return "创建订单被限流: " + e.getClass().getSimpleName();
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            return "业务处理被中断";
        } finally {
            if (entry != null) {
                entry.exit();
            }
        }
    }

    public String queryOrder(String orderId) {
        // 使用 try-with-resources 简化写法（推荐！）
        try (Entry entry = SphU.entry(QUERY_ORDER)) {
            Thread.sleep(30);
            return "订单详情: " + orderId;
        } catch (BlockException e) {
            return "查询订单被限流";
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            return "查询被中断";
        }
    }

    public String cancelOrder(String orderId) {
        Entry entry = null;
        try {
            entry = SphU.entry(CANCEL_ORDER);
            // 模拟复杂取消逻辑，耗时 500ms，占用线程
            Thread.sleep(500);
            return "订单已取消: " + orderId;
        } catch (BlockException e) {
            return "取消订单被限流";
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            return "取消被中断";
        } finally {
            if (entry != null) {
                entry.exit();
            }
        }
    }
}
```

**步骤二：手动管理 Context 的入口方法**

创建一个定时任务模拟的入口方法，展示非 Web 场景下的 Context 使用：

```java
public class SchedulerJob {

    public void processOrder(String orderId) {
        // 1. 手动创建调用上下文
        ContextUtil.enter("scheduler_context", "batch-job");

        try {
            // 2. 内部调用多个受保护资源
            String result = queryOrder(orderId);
            System.out.println(result);

            if (/* 某些条件 */) {
                String cancelResult = cancelOrder(orderId);
                System.out.println(cancelResult);
            }
        } finally {
            // 3. 必须清理 Context，防止内存泄漏
            ContextUtil.exit();
        }
    }
}
```

**步骤三：编写单元测试验证正常放行和限流阻断**

```java
@Test
public void testNormalFlow() {
    OrderService service = new OrderService();
    // 正常调用
    String result = service.createOrder("SKU_001");
    assertTrue(result.contains("成功"));
}

@Test
public void testBlockFlow() {
    OrderService service = new OrderService();
    int successCount = 0, blockCount = 0;
    // 快速连续调用 10 次，QPS 限制为 3
    for (int i = 0; i < 10; i++) {
        String result = service.createOrder("SKU_" + i);
        if (result.contains("成功")) {
            successCount++;
        } else if (result.contains("限流")) {
            blockCount++;
        }
    }
    // 断言：应该有一部分被限流
    assertTrue("应该至少有一次成功", successCount > 0);
    assertTrue("应该至少有一次被限流", blockCount > 0);
    System.out.println("成功: " + successCount + ", 限流: " + blockCount);
}

@Test
public void testThreadLimit() throws InterruptedException {
    OrderService service = new OrderService();
    ExecutorService executor = Executors.newFixedThreadPool(4);
    CountDownLatch latch = new CountDownLatch(4);
    AtomicInteger blockCount = new AtomicInteger(0);
    AtomicInteger passCount = new AtomicInteger(0);

    for (int i = 0; i < 4; i++) {
        executor.submit(() -> {
            String result = service.cancelOrder("ORDER_" + Thread.currentThread().getName());
            if (result.contains("被限流")) {
                blockCount.incrementAndGet();
            } else {
                passCount.incrementAndGet();
            }
            latch.countDown();
        });
    }
    latch.await(5, TimeUnit.SECONDS);
    executor.shutdown();

    // 线程数限制为 2，4 个并发至少 2 个被限流
    assertTrue("通过数应该为 2", passCount.get() == 2);
    assertTrue("阻断数应该为 2", blockCount.get() == 2);
}

@Test
public void testExceptionBoundary() {
    // 验证 BlockException 不会与业务异常混淆
    OrderService service = new OrderService();
    // 正常情况下返回业务结果
    String normalResult = service.createOrder("SKU_X");
    assertNotNull(normalResult);

    // BlockException 单独处理，不会抛出到外层
    for (int i = 0; i < 20; i++) {
        String result = service.createOrder("SKU_" + i);
        // 不会有异常抛出，所有情况都被内部 catch 处理
        assertNotNull(result);
    }
}
```

**步骤四：验证 try-with-resources 写法**

Sentinel 的 `Entry` 实现了 `AutoCloseable` 接口，支持 try-with-resources 语法。对比两种写法：

```java
// 传统写法（容易漏 finally）
Entry entry = null;
try {
    entry = SphU.entry("myResource");
    // do business
} catch (BlockException e) {
    // handle block
} finally {
    if (entry != null) {        // 别忘了判空！
        entry.exit();           // 别忘了 exit！
    }
}

// try-with-resources 写法（推荐，Java 7+）
try (Entry entry = SphU.entry("myResource")) {
    // do business
} catch (BlockException e) {
    // handle block
    // exit() 自动调用，不会遗漏
}
```

**踩坑记录**：

1. **Context 泄漏**：在非 Web 场景中使用 `ContextUtil.enter()` 后，如果忘记调用 `ContextUtil.exit()`，会导致 Context 对象持续占用内存不释放，最终 OOM。在生产中曾有一个定时任务每 10 秒调用一次但没有 exit，8 小时后服务内存溢出。

2. **线程池场景的 Entry 生命周期**：如果在 `Runnable` 中获取 Entry，务必确保 finally 块的 exit 在同一个线程内执行。不能在主线程获取 entry、在线程池中 exit——这样统计会完全错乱。

3. **BlockException 与其他异常的 catch 顺序**：BlockException 继承自 RuntimeException，如果先 catch RuntimeException 再 catch BlockException，BlockException 永远不会被单独捕获。正确的 catch 顺序是：BlockException → 业务异常 → 通用异常。

4. **资源名使用变量还是字面量**：强烈建议将资源名定义为常量（`private static final String`），避免多处硬编码导致不一致。

## 4 项目总结

### 4.1 优点与缺点

| 维度 | 编程式 API (SphU) | 注解式 (@SentinelResource) | AOP 切面 |
|------|-----------------|---------------------------|----------|
| 灵活性 | 极高，任意代码块可保护 | 中等，只能保护方法 | 高，但需自定义切点 |
| 代码侵入性 | 高，需修改业务代码 | 低，一个注解搞定 | 低 |
| 漏保护风险 | 低（显式调用） | 中（可能忘加注解） | 中 |
| Context 管理 | 手动控制 | 框架自动（Web 场景） | 手动或框架 |
| 调试难度 | 容易，直接断点 | 较难，需理解 AOP 代理 | 较难 |
| 适用场景 | 非 Web 应用、核心业务逻辑 | Web 接口、Feign Client | 通用场景 |

### 4.2 适用场景

- **编程式 API**：MQ 消费者、定时任务、非 Spring 应用、需要保护代码块（非方法）粒度
- **注解式**：Spring Boot/Spring Cloud 的 Controller、Service、Feign Client
- **两种混用**：Controller 用注解快速接入，核心 Service 用 API 精确控制 exit 时机
- **不适用**：异步回调场景（CompletableFuture），entry 获取和 exit 可能不在同一线程

### 4.3 注意事项

1. `Entry.exit()` 必须和 `SphU.entry()` 在同一个线程调用，否则统计失效。
2. try-with-resources 写法要求 `Entry` 实现了 `AutoCloseable`，Sentinel 1.6+ 已支持。
3. 资源名区分大小写，`"createOrder"` 与 `"CreateOrder"` 是不同的资源。
4. Context 名字应该具有业务含义，方便日志追踪。

### 4.4 常见踩坑经验

| 故障现象 | 根因 | 解决方案 |
|---------|------|---------|
| 线程数限流始终不触发 | 忘记调用 `entry.exit()`，线程数一直不减少 | 检查所有 `SphU.entry()` 是否有对应 `exit()` |
| Context 内存泄漏 | 循环中 `ContextUtil.enter()` 但未 `exit()` | 用 try-finally 确保 exit 调用 |
| 非 Web 场景资源不统计 | 未手动创建 Context | 在入口处调用 `ContextUtil.enter()` |
| `@SentinelResource` 在同类调用不生效 | Spring AOP 代理限制 | 改为注入自身 Bean 调用或使用编程式 API |

### 4.5 思考题

1. 如果在一个请求处理中多次调用 `SphU.entry("createOrder")`，每次都有对应的 `exit()`，Sentinel 会统计为几次调用？线程数会增加多少？
2. 为什么 Sentinel 的 Entry 设计为 `AutoCloseable`，而 Context 没有？Context 的 exit 机制与 Entry 的 exit 有什么本质区别？

### 4.6 推广计划

- **开发团队**：强制要求所有非 Web 入口（MQ 消费者、定时任务、异步任务）使用编程式 API + try-with-resources 写法。
- **测试团队**：针对编程式 API 和注解式分别设计测试用例，覆盖 Context 泄漏、异常边界等场景。
- **Code Review 规范**：Checklist 中加入"所有 SphU.entry() 是否有对应的 try-with-resources 或 finally exit()"。
