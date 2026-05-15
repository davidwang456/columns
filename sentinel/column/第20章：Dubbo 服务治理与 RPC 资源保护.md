# 第20章：Dubbo 服务治理与 RPC 资源保护

## 1 项目背景

第 19 章我们用 Feign + Sentinel 保护了微服务间的 HTTP 调用，但团队中还有一个重要的 RPC 通信组件——Dubbo。订单服务通过 Dubbo 调用库存服务的库存查询和扣减接口，通过 Dubbo 调用支付服务的退款接口。

Dubbo 场景下的 Sentinel 保护有自己的特殊性：

- Dubbo 是异步 RPC 调用，不像 HTTP 是同步的。如果 Provider 处理慢，Consumer 端的线程不会像 HTTP 那样一直阻塞等待（Dubbo 底层是 Netty NIO），而是通过 Future/Callback 异步获取结果。这意味着传统的"线程数限流"在 Dubbo 场景下的含义与 HTTP 不同。
- Dubbo 的 Provider 可能会被多个 Consumer 同时调用，某一天订单服务突然发布了一个 Bug 版本，疯狂调用库存查询接口——Provider 需要有自我保护能力。
- Dubbo 的资源命名是自动生成的——`接口全名:方法名(参数类型)`——不像 HTTP 那样可以用 `@SentinelResource` 自定义资源名。团队需要理解这个命名规则才能配置正确的规则。

还有一个痛点是：Dubbo 的异常、超时、线程池耗尽问题会以 Dubbo 异常（`RpcException`）的形式抛给 Consumer，Sentinel 如何区分"正常的 Dubbo 超时"和"需要熔断的服务故障"？

## 2 项目设计

**小胖**（打开 Dubbo 监控面板）："大师，库存服务 Dubbo Provider 的线程池快满了，但订单服务那边没反应——Consumer 线程数正常，CPU 也正常。这跟 HTTP 场景不一样啊。"

**大师**："因为 Dubbo 底层是 Netty NIO，Consumer 发请求后不会阻塞线程，而是注册一个回调。所以 Consumer 的线程数看起来正常，但 Provider 的线程池已经在排队了。"

**小白**："那 Sentinel 在 Dubbo 场景下应该重点保护哪一边？Provider 还是 Consumer？"

**大师**："两边都保护，但策略不同。Provider 侧重点防止资源耗尽——用线程数限流保护 Dubbo 业务线程池。Consumer 侧重点防止故障扩散——用熔断保护，当 Provider 异常比例过高时快速失败，不要让消费者一直等。"

**小胖**："那 Dubbo 的资源名是什么？我规则该怎么配？"

**大师**："看日志。Dubbo 的资源名格式是：`com.xxx.InventoryService:queryStock(java.lang.String)`。其中 Provider 的资源名和 Consumer 的资源名是一样的，Sentinel 通过 `EntryType` 区分：Provider 的请求 type 是 IN（入口），Consumer 的请求 type 是 OUT（出口）。"

**小白**："那我可以在 Provider 侧只限制入口请求？就像系统保护只对入口生效？"

**大师**："对。更妙的是，如果你在 Provider 侧做 QPS 限流，被拒绝的请求会在 Consumer 侧抛出 `FlowException`（封装在 `RpcException` 中）。Consumer 的熔断规则可以基于这个异常的频率来判断是否要熔断 Provider。"

**小胖**："那 Dubbo 的线程池满了怎么办？Provider 线程池满了应该直接拒绝吧，不能让请求堆积。"

**大师**："Dubbo 线程池满时默认策略是 `AbortPolicy`——直接抛 `RejectedExecutionException`，被包装成 `RpcException`。Sentinel 的异常比例熔断可以基于此触发。但更好的做法是：在 Provider 侧设置 Sentinel 线程数限流，阈值设为 Dubbo 线程池大小的 80%（比如线程池 200，限流 160），这样还剩 40 个线程用于处理健康检查和 Dashboard 通信。"

**小白**："Dubbo 3.0 引入了 Triple 协议（基于 gRPC），Sentinel 的 Dubbo Adapter 支持 Triple 吗？"

**大师**："`sentinel-apache-dubbo3-adapter` 完整支持 Triple 协议。资源名格式和 Dubbo 2.x 一样：`接口全名:方法名(参数类型)`。但要注意 Triple 基于 HTTP/2，可能会有流式调用（Streaming），这时候默认的 entry 机制不适用——需要手动用 `SphU.entry()` 包裹流式处理的每个消息。"

**小胖**："我们线上有 Dubbo 泛化调用场景——网关透传请求。泛化调用的资源名是什么？"

**大师**："泛化调用时资源名是 `接口全名:$invoke(java.lang.String, java.lang.String[], java.lang.Object[])`。但你很难针对泛化调用做细粒度限流——所有方法共用一个资源名。解决方案是：在网关上对泛化调用做第一层 QPS 限流（按下游接口名），然后在 Provider 侧做第二层线程数限流。"

**小白**："异常比例熔断时，怎么区分'Provider 正常超时'和'Provider 故障'？超时和异常在 Dubbo 中是不同的。"
  
**大师**："关键在于 Sentinel 的 `recordException` 方法。默认情况下，Sentinel Dubbo Adapter 把 `RpcException` 都视为 error。你可以自定义 fallback 来决定哪些异常计入熔断统计。比如：超时类异常（`TimeoutException`）不计入、业务异常（自定义 `BizException`）不计入，只把服务不可用类异常（`RemotingException`、连接拒绝等）计入熔断统计。这能显著降低误熔断。"

**小胖**："多个 Consumer 调用同一个 Provider 的同一接口，Consumer 侧的熔断状态是共享的还是独立的？"

**大师**："Sentinel 的熔断器是按资源名 + 调用方（`limitApp`）维度独立管理的。默认情况下，同一个资源名只有一个熔断器，所有 Consumer 共享。如果你想对不同 Consumer 独立熔断，需要在规则中指定 `limitApp` 为具体的应用名——这样每个 Consumer 有独立的熔断器。"

## 3 项目实战

### 3.1 环境准备

`pom.xml`：

```xml
<dependency>
    <groupId>com.alibaba.csp</groupId>
    <artifactId>sentinel-apache-dubbo3-adapter</artifactId>
    <version>1.8.6</version>
</dependency>
<dependency>
    <groupId>org.apache.dubbo</groupId>
    <artifactId>dubbo-spring-boot-starter</artifactId>
    <version>3.2.14</version>
</dependency>
```

### 3.2 分步实现

**步骤一：Dubbo Provider 端限流保护**

```java
// 库存服务 Provider
@DubboService(version = "1.0.0")
public class InventoryServiceImpl implements InventoryService {

    @Override
    public int queryStock(String skuId) {
        // Sentinel Dubbo Adapter 自动创建资源：
        // 资源名: com.example.InventoryService:queryStock(java.lang.String)
        // EntryType: IN (Provider 侧)
        simulateWork(50);
        return 100;
    }

    @Override
    public boolean deductStock(String skuId, int quantity) {
        simulateWork(200);  // 写操作更慢
        return true;
    }

    private void simulateWork(int ms) {
        try { Thread.sleep(ms); } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        }
    }
}
```

Provider 侧规则配置（Nacos）：

```json
[
  {
    "resource": "com.example.InventoryService:queryStock(java.lang.String)",
    "limitApp": "default",
    "grade": 1,
    "count": 200,
    "strategy": 0,
    "controlBehavior": 0
  },
  {
    "resource": "com.example.InventoryService:deductStock(java.lang.String,int)",
    "limitApp": "default",
    "grade": 0,
    "count": 30,
    "strategy": 0,
    "controlBehavior": 0
  }
]
```

**步骤二：Dubbo Consumer 端熔断保护**

```java
// 订单服务 Consumer
@RestController
public class OrderDubboController {

    @DubboReference(version = "1.0.0", timeout = 3000)
    private InventoryService inventoryService;

    @GetMapping("/order/dubbo/create")
    @SentinelResource(value = "dubboCreateOrder", blockHandler = "blockHandler")
    public String createOrder(@RequestParam String skuId) {
        // 调用 Dubbo 服务
        // Sentinel Dubbo Adapter 自动保护此调用：
        // 资源名: com.example.InventoryService:queryStock(java.lang.String)
        // EntryType: OUT (Consumer 侧)
        int stock = inventoryService.queryStock(skuId);
        return "下单成功, 库存: " + stock;
    }

    public String blockHandler(String skuId, BlockException e) {
        return "下单失败: " + e.getClass().getSimpleName();
    }
}
```

Consumer 侧熔断规则：

```json
[
  {
    "resource": "com.example.InventoryService:queryStock(java.lang.String)",
    "grade": 1,
    "count": 0.3,
    "timeWindow": 15,
    "minRequestAmount": 5,
    "statIntervalMs": 10000
  }
]
```

**步骤三：自定义 Dubbo 异常处理**

Sentinel 的 Dubbo Adapter 默认将所有 `RpcException` 及其子类视为 Sentinel BlockException 的同级异常。我们可以自定义行为：

```java
@Configuration
public class DubboSentinelConfig {

    @PostConstruct
    public void init() {
        // 注册自定义的 Dubbo fallback
        DubboFallbackRegistry.setProviderFallback((invoker, invocation, ex) -> {
            // Provider 侧被 Sentinel 限流后的处理
            if (ex instanceof FlowException) {
                return AsyncRpcResult.newDefaultAsyncResult(
                    "Provider 限流，请稍后重试", invocation);
            }
            // 其他异常走 Dubbo 默认处理
            return null;
        });
    }
}
```

**步骤四：验证 Provider 限流 → Consumer 感知**

验证步骤：

1. 启动库存服务（Provider）和订单服务（Consumer）
2. 在 Provider Nacos 中配 `queryStock` QPS=10
3. 用 JMeter 以 50 线程并发压测订单服务的 `/order/dubbo/create`
4. 观察 Consumer 日志：

```log
# Consumer 侧
[Sentinel Dubbo Consumer] Blocked by Provider flow control:
    resource=com.example.InventoryService:queryStock(java.lang.String)
    exception=FlowException
```

5. 当异常比例超过 30% 时，Consumer 熔断器打开 → 后续请求直接走 fallback，不再实际调用 Provider。

**步骤五：Dubbo 异步调用的 Sentinel 保护**

```java
// 异步调用场景
@DubboReference(version = "1.0.0", async = true)
private InventoryService asyncInventoryService;

@GetMapping("/order/dubbo/async")
public String createOrderAsync(@RequestParam String skuId) {
    // 异步调用
    asyncInventoryService.queryStock(skuId);
    Future<Integer> future = RpcContext.getContext().getFuture();

    // Sentinel 的 Dubbo Adapter 对异步调用同样生效
    // 但如果需要精确控制，可以手动使用 SphU.asyncEntry()
    try {
        AsyncEntry entry = SphU.asyncEntry("dubboAsyncQuery");
        future.whenComplete((result, ex) -> {
            try {
                if (ex != null) {
                    // 记录异常
                }
            } finally {
                entry.exit();
            }
        });
    } catch (BlockException e) {
        return "异步调用被限流";
    }

    return "异步下单成功";
}
```

**踩坑记录**：

1. **Dubbo 版本与 Sentinel Adapter 匹配**：`sentinel-apache-dubbo3-adapter` 适用于 Dubbo 3.x，如果是 Dubbo 2.7.x 应使用 `sentinel-apache-dubbo-adapter`。
2. **资源名太长无法在 Dashboard 显示**：Dubbo 自动生成的资源名超过限制时会被截断。建议在 Nacos 中配置规则，而不是在 Dashboard。
3. **Provider 和 Consumer 资源名相同但行为不同**：这可能导致混淆——在 Dashboard 中看到同一个资源的流量，分不清是 Provider 侧还是 Consumer 侧。解决方案：在规则命名时加上前缀标识。
4. **Dubbo 泛化调用**：GenericService 调用方式下，资源名格式不同，需要注意规则匹配。
5. **线程池拒绝与限流混淆**：Dubbo Provider 线程池满了会抛 `RejectedExecutionException`，被 Sentinel 统计为异常。这可能导致熔断器误触发——把正常的线程池过载当成服务故障。解决方式：在线程数限流中预留 20% 余量（限流阈值 = 线程池大小 × 0.8）。

**步骤六：Dubbo Provider 线程池监控 + 自适应保护**

```java
@Component
public class DubboThreadPoolMonitor {

    @Scheduled(fixedRate = 5000)
    public void monitorThreadPool() {
        // 获取 Dubbo 线程池状态
        for (ThreadPoolExecutor executor : 
             ExtensionLoader.getExtensionLoader(ThreadPool.class)
                .getSupportedExtensionInstances()) {
            
            int active = executor.getActiveCount();
            int poolSize = executor.getPoolSize();
            int queueSize = executor.getQueue().size();
            
            double usageRate = (double) active / poolSize;
            
            // 线程池使用率 > 70% → 触发 Sentinel 线程数限流
            if (usageRate > 0.7) {
                log.warn("Dubbo 线程池使用率 {}% (active={}/{})，建议限流",
                    String.format("%.1f", usageRate * 100), active, poolSize);
            }
            
            // 队列堆积 → 接近限流阈值，应自动降低 Sentinel 线程限流数
            if (queueSize > poolSize * 0.5) {
                log.error("Dubbo 队列堆积 {} > 线程池 50%，需要紧急扩容", queueSize);
            }
        }
    }
}
```

**步骤七：Consumer 端异常分类处理（避免误熔断）**

```java
@Configuration
public class DubboExceptionClassifier implements DubboFallback {

    @Override
    public AsyncRpcResult handle(Invoker<?> invoker, Invocation invocation, 
                                  BlockException ex) {
        // Sentinel 限流/熔断触发的 BlockException → 直接返回 fallback
        if (ex instanceof FlowException) {
            return AsyncRpcResult.newDefaultAsyncResult(
                "Provider 限流，请稍后重试", invocation);
        }
        if (ex instanceof DegradeException) {
            return AsyncRpcResult.newDefaultAsyncResult(
                "Provider 已熔断，请稍后重试", invocation);
        }
        return null;
    }

    // 异常类型 → 是否计入熔断统计
    public static boolean shouldCountAsError(Throwable ex) {
        // 超时不统计 — 这是网络/配置问题，不是服务故障
        if (ex instanceof TimeoutException) return false;
        // 业务异常不统计 — 这是正常业务流程
        if (ex instanceof BizException) return false;
        // 限流不统计 — 这是保护机制正常工作
        if (ex instanceof FlowException) return false;
        // 服务端真正异常（RemotingException、连接拒绝等）→ 统计
        return true;
    }
}
```

**步骤八：Dubbo Triple 协议（gRPC）场景适配**

```java
// Triple 协议流式调用的 Sentinel 保护
@DubboService(version = "1.0.0", protocol = "tri")
public class TripleInventoryService implements InventoryService {

    @Override
    public StreamObserver<Integer> queryStockBatch(
            StreamObserver<StockRequest> requestObserver) {
        
        // 流式调用无法被自动保护，需手动包裹
        return new StreamObserver<StockRequest>() {
            @Override
            public void onNext(StockRequest request) {
                Entry entry = null;
                try {
                    entry = SphU.entry(
                        "com.example.InventoryService:queryStockBatch");
                    // 处理消息
                } catch (BlockException e) {
                    // 流式调用被限流时，结束流
                    requestObserver.onError(e);
                } finally {
                    if (entry != null) entry.exit();
                }
            }
            @Override
            public void onError(Throwable t) {}
            @Override
            public void onCompleted() {
                requestObserver.onCompleted();
            }
        };
    }
}
```

**步骤九：多 Consumer 独立熔断配置**

```json
[
  {
    "resource": "com.example.InventoryService:queryStock(java.lang.String)",
    "limitApp": "order-service",
    "grade": 1,
    "count": 0.3,
    "timeWindow": 15,
    "minRequestAmount": 5
  },
  {
    "resource": "com.example.InventoryService:queryStock(java.lang.String)",
    "limitApp": "cart-service",
    "grade": 1,
    "count": 0.1,
    "timeWindow": 30,
    "minRequestAmount": 10,
    "comment": "cart-service 对异常更敏感，熔断阈值更低"
  }
]
```

**步骤十：Dubbo 调用链路追踪集成**

```java
// Dubbo 调用链加上 Sentinel 上下文，便于日志关联
@Component
public class DubboSentinelTracingFilter implements Filter {
    
    private static final int SENTINEL_FILTER_ORDER = -100000;

    @Override
    public Result invoke(Invoker<?> invoker, Invocation invocation) 
            throws RpcException {
        
        String resourceName = invoker.getInterface().getName() 
            + ":" + invocation.getMethodName()
            + "(" + String.join(",", invocation.getParameterTypes()) + ")";
        
        ContextUtil.enter(resourceName, invoker.getUrl().getApplication());
        
        // 将 traceId 注入 Sentinel 上下文
        String traceId = RpcContext.getContext().getAttachment("traceId");
        if (traceId != null) {
            ContextUtil.getContext().setOrigin(traceId);
        }
        
        try {
            return invoker.invoke(invocation);
        } finally {
            ContextUtil.exit();
        }
    }
}
```

**踩坑记录（续）**：

5. **线程池拒绝与限流混淆**：Dubbo Provider 线程池满了会抛 `RejectedExecutionException`，被 Sentinel 统计为异常。这可能导致熔断器误触发——把正常的线程池过载当成服务故障。解决方式：在线程数限流中预留 20% 余量（限流阈值 = 线程池大小 × 0.8）。
6. **Dubbo 附属调用**：`RpcContext.getContext().getFuture()` 需要在方法返回前获取，之后上下文会被清除。
7. **跨版本协议的 Sentinel 兼容**：Dubbo 3.x 默认使用 Triple 协议，但同时也支持 dubbo:// 协议。两种协议 Sentienl 都支持，但资源名相同。

## 4 项目总结

### 4.1 优点与缺点

| 维度 | Dubbo Sentinel Adapter | 手动编程式 | Feign Sentinel |
|------|----------------------|-----------|---------------|
| 接入成本 | 极低（引入依赖即可） | 高 | 低 |
| 资源命名 | 自动（接口名:方法名） | 自定义 | 自动（HTTP 方法:URL） |
| Provider/Consumer 区分 | 通过 EntryType 自动区分 | 手动控制 | N/A（HTTP 无 Provider/Consumer 概念） |
| 异步支持 | 支持（需用 asyncEntry） | 支持 | 不支持 |
| 泛化调用支持 | 有限 | 灵活 | N/A |
| 流式调用支持 | 需手动 SphU.entry() | 支持 | 不支持 |
| 线程池联动 | 不支持自动联动 | 可自定义实现 | N/A |
| 多注册中心 | 自动适配 | 手动配置 | 不支持 |

### 4.2 Provider vs Consumer 保护策略对比

| 策略 | Provider 侧 | Consumer 侧 |
|------|-----------|-----------|
| **QPS 限流** | ✅ 直接限流（推荐） | ⚠️ 不推荐（每个 Consumer 需独立控制） |
| **线程数限流** | ✅ 保护 Dubbo 业务线程池 | ❌ 无意义（NIO，不阻塞线程） |
| **慢调用熔断** | ❌ 不适用 | ✅ 感知 Provider 响应变慢 |
| **异常比例熔断** | ❌ 不适用 | ✅ 感知 Provider 故障率 |
| **系统保护** | ✅ 全局保护（CPU/Load） | ❌ 不适用 |
| **热点参数限流** | ✅ 按参数限流 | ❌ Consumer 侧无参数语义 |

### 4.3 适用场景

- Dubbo Provider 保护：线程数限流 + 系统保护
- Dubbo Consumer 保护：慢调用熔断 + 异常比例熔断
- Dubbo 异步调用：`SphU.asyncEntry()` 手动保护
- Dubbo 泛化调用：手动 API 接入
- Dubbo Triple 流式调用：`SphU.entry()` 逐消息保护

### 4.4 Dubbo + Sentinel 故障识别矩阵

| Consumer 观察到的现象 | Provider 可能的原因 | Sentinel 应对策略 |
|---------------------|-------------------|------------------|
| `FlowException` | Provider 侧触发 QPS/线程数限流 | Consumer 侧异常比例熔断 + fallback |
| `DegradeException` | Consumer 侧熔断器打开 | 直接走 fallback，不调用 Provider |
| `TimeoutException` | Provider 处理慢或网络问题 | Consumer 慢调用比例熔断（不计入异常统计） |
| `RemotingException` | Provider 宕机或网络不通 | Consumer 异常比例熔断 |
| `RejectedExecutionException` | Provider 线程池满 | Provider 线程数限流预防 |
| `BizException`（业务异常） | Provider 正常处理异常 | 不计入 Sentinel 统计 |

### 4.5 Dubbo Provider 容量规划速查表

| 线程池大小 | 推荐 Sentinel 线程限流 | 预估支撑 QPS (RT=100ms) |
|-----------|----------------------|----------------------|
| 100 | 80 | ~800 QPS |
| 200 | 160 | ~1600 QPS |
| 500 | 400 | ~4000 QPS |
| 1000 | 800 | ~8000 QPS |

> 建议：线程数限流 = 线程池大小 × 0.8，留 20% 余量给 Health Check 和 Dashboard 通信。

### 4.6 注意事项

1. Dubbo Provider 和 Consumer 的资源名是相同的，但 Sentinel 通过 EntryType 区分。Provider 限流不影响 Consumer 自身的限流判断。
2. Dubbo 的超时配置（`timeout=3000`）应该大于 Sentinel 的慢调用阈值（`maxAllowedRt`）。
3. Dubbo 线程池满时抛出 `RpcException`，Sentinel 的异常比例熔断可以基于此触发。
4. Consumer 不要在 Dubbo Sentinel Filter 之外再手动创建 entry —— 避免 double-entry 导致资源泄漏。

### 4.7 思考题

1. Dubbo Provider 被 Sentinel 限流拒绝的请求，在 Consumer 侧会收到什么异常？如果 Consumer 配置了异常比例熔断规则，这些拒绝会被统计为异常吗？
2. 如果 Dubbo Provider 部署了 3 个实例，每个实例配了 QPS=100。当 Consumer 负载均衡地调用这 3 个实例时，Consumer 的实际可用 QPS 是多少？如何保证整体 300 QPS 的阈值不会被打破？（提示：思考集群流控，第 22 章）

### 4.8 推广计划

- **开发团队**：所有 Dubbo Provider 必须接入 Sentinel 做基础保护，Consumer 侧的核心调用必须有熔断兜底。
- **测试团队**：设计 Dubbo 场景的压测方案，模拟 Provider 异常/慢/超时场景验证熔断。
- **运维团队**：关注 Dubbo Provider 的线程池指标，在接近 Sentinel 线程数限流阈值时提前告警。
