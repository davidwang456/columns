# 第19章：OpenFeign 与 RestTemplate 调用保护

## 1 项目背景

第 17-18 章把 Sentinel 规则持久化到了 Nacos，规则管理终于上了正轨。但运维团队很快发现了一个新的盲区：订单服务通过 OpenFeign 调用库存服务，当库存服务变慢或异常时，订单服务的线程池迅速被耗尽——但 Sentinel 的限流规则只在订单服务的 Controller 层生效，Feign 调用完全不受保护。

监控显示，库存服务的数据库出现慢查询时，库存查询从 10ms 变成 2000ms。订单服务有 200 个线程等待 Feign 调用返回，导致所有 Controller 层的请求（包括不需要库存的"查询订单状态"接口）全部超时。这本质上是一个"级联故障"——库存服务的故障穿越了 Feign 调用边界，直接拖垮了上游订单服务。

复盘时开发团队意识到，基础篇只保护了 HTTP 入口（Controller），但微服务间的远程调用（Feign/RestTemplate）同样需要保护。而且 Feign 的保护有特殊之处：在调用方（Consumer）和被调用方（Provider）都需要配置规则，但两者的职责不同——Provider 侧是"保护自己不被流量打爆"，Consumer 侧是"保护自己不被下游拖垮"。

## 2 项目设计

**小胖**（看着线程池监控）："库存服务只是变慢了，为什么订单服务整个都挂了？Feign 调用没超时设置吗？"

**大师**："Feign 有超时，但不够。当积压了 200 个 Feign 调用，即使每个超时 1 秒，200 个线程也要等 1 秒。在这 1 秒内，新的 Controller 请求没有可用线程，全被拒绝了。"

**小白**："所以需要在 Feign 调用层加 Sentinel 保护。当发现下游慢了，就直接熔断，不等待超时。"

**大师**："对。Sentinel 对 Feign 的支持是通过 `feign.sentinel.enabled=true` 开启的。开启后，每个 Feign 接口方法自动成为一个 Sentinel 资源，资源名是 `接口全限定名#方法名(参数类型)`。"

**小胖**："那 fallback 怎么配？Feign 默认的 fallback 是抛异常还是返回 null？"

**大师**："推荐用 `fallbackFactory` 而非 `fallback`。因为 `fallbackFactory` 可以拿到异常原因，你可以在日志中记录是超时、限流还是业务异常，方便排查。"

**小白**："那 RestTemplate 呢？我们项目中还有些老代码用的是 RestTemplate。"

**大师**："RestTemplate 需要手动用 `@SentinelRestTemplate` 注解包装，或者用 Sentinel 提供的 `SentinelRestTemplate` 拦截器。个人建议新项目统一用 OpenFeign，老项目的 RestTemplate 逐步迁移。"

**小胖**："大师，Feign 的 fallbackFactory 和 @SentinelResource 的 blockHandler 有什么区别？我感觉都是兜底处理。"

**大师**："区别很大。Feign 的 fallbackFactory 是在远程调用失败时触发——包括 Sentinel 限流、熔断、超时、连接拒绝。@SentinelResource 的 blockHandler 只在 Sentinel 阻断（BlockException）时触发，不处理业务异常。两者的触发范围不同：fallbackFactory 更宽泛，blockHandler 更精确。"

**小白**："那 Feign 调用链中，如果 Provider 返回了限流结果（429），Consumer 的 Sentinel 会把这次调用算作'成功'还是'异常'？"

**大师**："这取决于 HTTP 状态码的处理方式。Sentinel 默认不根据 HTTP 状态码判断——只要 HTTP 请求完成（没有抛 IOException），就算成功。所以 Provider 返回 429，Consumer 的熔断规则不会把它计为异常。如果你需要把特定状态码（如 429、503）也计入熔断统计，需要在 fallbackFactory 中主动抛异常。"

**小胖**："还有——Feign 方法的资源名是自动生成的，格式好长。如果我想自定义资源名怎么办？"

**大师**："两种方案。一是直接关闭 `feign.sentinel.enabled`，手动在调用 Feign 的业务代码外层加 `SphU.entry("自定义名称")`。二是使用 Sentinel 的 `SentinelFeign.Builder` 传入自定义的资源名解析器。第一种简单但有侵入性，第二种需要更多配置。"

**技术映射**：
- Feign Sentinel 集成原理：`SentinelFeign` 通过 `Feign.builder()` 的 `InvocationHandlerFactory` 在 Feign 的调用链中插入 Sentinel 拦截逻辑。每个 Feign 方法调用都会被 `SphU.entry()` 保护。
- 资源名格式：`GET:http://inventory-service/inventory/query?skuId={0}` 或 `inventory-service#queryStock(String)`。
- `feign.sentinel.enabled`：必须显式设置为 true（在部分 Spring Cloud 版本中默认为 false）。
- RestTemplate：`@SentinelRestTemplate` 注解默认会在 RestTemplate 的所有 HTTP 调用前后插入 Sentinel 保护。

## 3 项目实战

### 3.1 环境准备

`pom.xml` 加入 OpenFeign + Sentinel 依赖：

```xml
<dependency>
    <groupId>org.springframework.cloud</groupId>
    <artifactId>spring-cloud-starter-openfeign</artifactId>
</dependency>
<dependency>
    <groupId>com.alibaba.cloud</groupId>
    <artifactId>spring-cloud-starter-alibaba-sentinel</artifactId>
</dependency>
```

`application.yml`：

```yaml
feign:
  sentinel:
    enabled: true  # 必须显式开启！默认是 false
  client:
    config:
      default:
        connectTimeout: 1000
        readTimeout: 3000
```

### 3.2 分步实现

**步骤一：Feign 客户端定义与 FallbackFactory**

```java
@FeignClient(
    name = "inventory-service",
    url = "http://localhost:8082",
    fallbackFactory = InventoryClientFallbackFactory.class
)
public interface InventoryClient {

    @GetMapping("/inventory/query")
    Result<Integer> queryStock(@RequestParam("skuId") String skuId);

    @PostMapping("/inventory/deduct")
    Result<Boolean> deductStock(@RequestParam("skuId") String skuId,
                                 @RequestParam("quantity") Integer quantity);
}

@Component
@Slf4j
public class InventoryClientFallbackFactory
        implements FallbackFactory<InventoryClient> {

    @Override
    public InventoryClient create(Throwable cause) {
        // 记录异常原因，方便排查
        String reason = cause instanceof DegradeException ? "熔断降级"
                : cause instanceof FlowException ? "限流"
                : cause instanceof TimeoutException ? "超时"
                : "未知异常:" + cause.getClass().getSimpleName();
        log.error("库存服务调用降级, 原因: {}", reason, cause);

        return new InventoryClient() {
            @Override
            public Result<Integer> queryStock(String skuId) {
                return Result.error("FALLBACK",
                    "库存服务暂不可用（" + reason + "）");
            }

            @Override
            public Result<Boolean> deductStock(String skuId, Integer quantity) {
                return Result.error("FALLBACK",
                    "扣减库存失败，订单将异步处理（" + reason + "）");
            }
        };
    }
}
```

**步骤二：在 Provider 侧（库存服务）配置限流**

库存服务保护自己的接口不被订单服务打爆：

```java
// 库存服务 Controller
@RestController
public class InventoryController {

    @GetMapping("/inventory/query")
    @SentinelResource(value = "inventoryQuery", blockHandler = "queryBlock")
    public Result<Integer> queryStock(@RequestParam String skuId) {
        return Result.success(100);
    }

    public Result<Integer> queryBlock(String skuId, BlockException e) {
        return Result.error("FLOW_LIMIT", "库存查询请求过多");
    }
}
```

库存服务 Nacos 规则配置：对 `inventoryQuery` 配 QPS=200 的流控。

**步骤三：在 Consumer 侧（订单服务）配置熔断**

```java
@Component
public class ConsumerDegradeConfig {

    @PostConstruct
    public void initFeignDegradeRules() {
        List<DegradeRule> rules = new ArrayList<>();

        // 对 Feign 调用 queryStock 方法的熔断规则
        DegradeRule slowRule = new DegradeRule()
                .setResource("GET:http://inventory-service/inventory/query")
                .setGrade(RuleConstant.DEGRADE_GRADE_RT)
                .setCount(100)                  // maxAllowedRt = 100ms
                .setSlowRatioThreshold(0.5)     // 慢调用 > 50% 触发
                .setMinRequestAmount(5)
                .setStatIntervalMs(10000)
                .setTimeWindow(10);

        rules.add(slowRule);
        DegradeRuleManager.loadRules(rules);
    }
}
```

**验证步骤**：

1. 启动订单服务和库存服务
2. 在库存服务中注入 500ms 延迟
3. 用 JMeter 对订单服务下单接口压测（30 QPS）
4. 观察：10 秒内 Feign 调用的慢调用比例 > 50% → 熔断打开 → 订单服务收到 fallbackFactory 返回的降级结果
5. 关闭延迟注入 → 等待 10 秒熔断窗口结束 → 半开探测通过 → 恢复

**步骤四：RestTemplate 的 Sentinel 保护**

老项目中的 RestTemplate 改造：

```java
@Configuration
public class RestTemplateConfig {

    @Bean
    @SentinelRestTemplate(
        blockHandler = "handleBlock",
        blockHandlerClass = SentinelBlockHandler.class,
        fallback = "handleFallback",
        fallbackClass = SentinelBlockHandler.class
    )
    public RestTemplate restTemplate() {
        return new RestTemplate();
    }
}

public class SentinelBlockHandler {
    // blockHandler：被 Sentinel 限流时调用
    public static ClientHttpResponse handleBlock(HttpRequest request,
                                                  byte[] body,
                                                  ClientHttpRequestExecution execution,
                                                  BlockException ex) {
        return new SentinelClientHttpResponse(
            "{\"code\":\"FLOW_LIMIT\",\"msg\":\"RestTemplate 调用被限流\"}");
    }

    // fallback：业务异常时调用
    public static ClientHttpResponse handleFallback(HttpRequest request,
                                                     byte[] body,
                                                     ClientHttpRequestExecution execution,
                                                     BlockException ex) {
        return new SentinelClientHttpResponse(
            "{\"code\":\"FALLBACK\",\"msg\":\"RestTemplate 调用降级\"}");
    }
}
```

**步骤五：远程调用异常与 Sentinel 阻断的响应规范**

统一 Feign/RestTemplate 异常响应格式：

```json
// Sentinel 阻断
{
  "code": "SENTINEL_BLOCK",
  "msg": "调用库存服务被限流",
  "type": "FLOW_EXCEPTION",
  "resource": "GET:http://inventory-service/inventory/query",
  "timestamp": 1717257600000
}

// 业务异常
{
  "code": "BIZ_ERROR",
  "msg": "库存不足",
  "detail": "SKU_001 实际库存 5, 请求 10"
}

// 超时
{
  "code": "TIMEOUT",
  "msg": "调用库存服务超时",
  "costMs": 3005
}
```

**踩坑记录**：

1. **Feign fallback 和熔断 fallbackFactory 二选一**：如果用 `fallbackFactory`，不要同时用 `fallback`，否则可能冲突。
2. **Feign 接口上加 `@SentinelResource` 无效**：Feign 有自己独立的 Sentinel 拦截机制，在 Feign 接口方法上加 `@SentinelResource` 注解不会生效。
3. **Feign 超时与 Sentinel 熔断的配合**：`feign.client.config.default.readTimeout` 应该大于 Sentinel 的 `maxAllowedRt`。否则请求可能在 Sentinel 判断"慢"之前就超时了。
4. **Provider 和 Consumer 的双重保护**：如果 Provider 做了限流（QPS=200），Consumer 做了熔断（异常 > 30%），当 Provider 限流拒绝请求时，Consumer 看到的异常会成为熔断统计的一部分——注意不要让两层保护相互干扰。

**步骤六：Feign 调用链的可观测性增强**

在 fallbackFactory 中记录 traceId，关联调用链：

```java
@Component
@Slf4j
public class InventoryClientFallbackFactory
        implements FallbackFactory<InventoryClient> {

    @Override
    public InventoryClient create(Throwable cause) {
        return new InventoryClient() {
            @Override
            public Result<Integer> queryStock(String skuId) {
                // 从 MDC 中获取 traceId（sleuth/skywalking 自动注入）
                String traceId = MDC.get("traceId");
                String reason = classifyException(cause);

                log.error("[Feign Fallback] traceId={}, skuId={}, reason={}",
                    traceId, skuId, reason, cause);

                // 上报降级事件到 Prometheus
                Counter.builder("sentinel_feign_fallback_total")
                    .tag("client", "inventory-service")
                    .tag("method", "queryStock")
                    .tag("reason", reason)
                    .register(MeterRegistry.globalRegistry())
                    .increment();

                return Result.error("FALLBACK", "库存服务暂不可用");
            }
        };
    }

    private String classifyException(Throwable cause) {
        if (cause instanceof DegradeException) return "degrade";
        if (cause instanceof FlowException) return "flow";
        if (cause instanceof TimeoutException) return "timeout";
        if (cause instanceof ConnectException) return "connect_refused";
        return "unknown";
    }
}
```

**步骤七：RestTemplate 逐步迁移到 OpenFeign 的对照表**

| 原 RestTemplate 调用 | OpenFeign 等价写法 | 迁移成本 |
|---------------------|-------------------|---------|
| `restTemplate.getForObject(url, String.class)` | `@GetMapping` + `String` 返回值 | 低 |
| `restTemplate.postForEntity(url, request, Response.class)` | `@PostMapping` + `@RequestBody` | 低 |
| `restTemplate.exchange(url, HttpMethod.PUT, entity, Void.class)` | `@PutMapping` | 低 |
| 动态 URL（`restTemplate.getForObject(baseUrl + path)`） | 需改为固定 URL 或多 Feign Client | 中 |
| 自定义 Header 透传 | 需要 `RequestInterceptor` | 中 |
| RestTemplate 拦截器逻辑 | 需迁移到 Feign `RequestInterceptor` | 高 |

## 4 项目总结

### 4.1 优点与缺点

| 方案 | 优点 | 缺点 |
|------|------|------|
| Feign + Sentinel fallbackFactory | 自动保护所有 Feign 调用，可获取异常原因 | 需在 application.yml 中显式开启 |
| Feign + @SentinelResource | 不适用（Feign 有自己的拦截机制） | 不能代替 fallbackFactory |
| RestTemplate + @SentinelRestTemplate | 快速改造老代码 | 每个 RestTemplate Bean 要单独配置 |
| 编程式保护 | 完全控制 | 代码侵入性大 |

### 4.2 适用场景

- Feign + fallbackFactory 是微服务间调用的首选方案
- @SentinelRestTemplate 适用于无法迁移到 Feign 的老项目
- 编程式保护适用于非标准 HTTP 调用（如 WebSocket、gRPC）

### 4.3 注意事项

1. `feign.sentinel.enabled=true` 必须显式配置，不要依赖默认值。
2. Feign 资源名格式较长，在 Dashboard 中查找时可以用搜索功能。
3. 不要同时依赖 Provider 限流和 Consumer 熔断来保护同一链路——两者职责不同，应分层配置。

### 4.4 Feign Sentinel 资源名格式速查

| Feign 方法签名 | Sentinel 资源名 | Dashboard 显示名 |
|--------------|---------------|-----------------|
| `String queryStock(String skuId)` | `GET:http://inventory-service/inventory/query` | 同左 |
| `Boolean deductStock(String skuId, Integer qty)` | `POST:http://inventory-service/inventory/deduct` | 同左 |
| 带占位符的 URL | `GET:http://inventory-service/inventory/query?skuId={0}` | 同左 |

### 4.5 Provider vs Consumer 保护的角色对比

| 角色 | 保护目标 | 用什么规则 | 典型阈值 |
|------|---------|-----------|---------|
| Provider | 保护自己不被流量打爆 | FlowRule (QPS) | 压测拐点 × 0.8 |
| Provider | 保护自己不被慢调用拖垮 | SystemRule (CPU/RT) | CPU 80% |
| Consumer | 保护自己不被下游拖垮 | DegradeRule (慢调用/异常) | maxAllowedRt=100ms |
| Consumer | 保护自己的线程池 | FlowRule (线程数) | 连接池大小 × 0.8 |
| Consumer | 兜底返回合理降级结果 | FallbackFactory | 区分每种异常 |

### 4.4 常见踩坑经验

| 故障现象 | 根因 | 解决方案 |
|---------|------|---------|
| Feign 调用没有被 Sentinel 拦截 | `feign.sentinel.enabled` 未开启 | 显式设置为 true |
| fallbackFactory 不触发 | Feign 超时早于 Sentinel 统计窗口 | 调整 Feign readTimeout > Sentinel maxAllowedRt |
| Provider 限流导致 Consumer 频繁熔断 | Consumer 把 Provider 的限流拒绝当成了故障 | Consumer 熔断指标应排除 Provider 的 429 响应码 |

### 4.5 思考题

1. 如果 Feign 的 fallbackFactory 返回了降级结果，上游 Controller 怎么区分"是库存真的不足"还是"库存服务熔断了"？
2. 同一个 Feign 接口有 5 个方法（queryStock、deductStock 等），你可以为每个方法配置不同的熔断规则吗？怎么做？

### 4.6 推广计划

- **开发团队**：所有 Feign Client 必须配置 fallbackFactory，并在其中打印异常类型。
- **测试团队**：针对 Feign 调用设计"下游延迟→熔断触发→降级返回"的端到端测试用例。
- **运维团队**：在 Nacos 中为每个 Feign Client 维护独立的熔断规则。
