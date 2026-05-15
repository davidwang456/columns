# 第4章：Spring Boot 自动接入与注解保护

## 1 项目背景

第 3 章的手动编程式 API 让团队掌握了 Sentinel 的底层机制，但在实际项目中，业务代码有 200 多个 Controller 方法、80 多个 Feign Client 接口，如果每个方法都手写 `SphU.entry()` + try-catch + finally，代码量至少增加 3 倍，而且极易出现"某个新接口忘记加保护"的风险。

测试团队在回归测试中发现了一个严重问题：订单服务的"查询库存"接口没有接入 Sentinel，在大促压测中这个接口被打爆，拖慢了整个订单服务的响应速度。追溯代码发现，开发人员只是在 Controller 中写了 `@SentinelResource`，但 blockHandler 写错了参数签名（漏掉了 BlockException 参数），导致注解静默失效，Sentinel 不报错、不提示，整个接口实际处于"裸奔"状态。

运维团队也反馈：线上经常出现 `BlockException` 被全局异常处理器捕获，返回了 500 错误，而不是友好的"系统繁忙，请稍后重试"。排查发现是因为项目使用了 `@ControllerAdvice` 统一异常处理，而 Sentinel 的 BlockException 被当作普通 RuntimeException 处理了。

这些问题的根源在于：**团队对 Sentinel 的 Spring 自动接入机制理解不够**。`sentinel-spring-webmvc-adapter` 与 `spring-cloud-starter-alibaba-sentinel` 有什么不同？`@SentinelResource` 的 blockHandler 和 fallback 有什么区别？注解失效时如何排查？

本章将从 Spring Boot 自动配置原理出发，深入讲解注解式接入的最佳实践，帮你避开 90% 的接入坑。

## 2 项目设计

**小胖**（敲着键盘）："大师，我发现一个问题！我在 Service 层的 `deductStock()` 方法上加了 `@SentinelResource`，但限流不生效。我在 Controller 调用这个方法，规则明明已经配了！"

**大师**："你介不介意看一下这个 Service 是怎么被调用的？"

**小胖**："就是在 Controller 里 `this.stockService.deductStock()` 啊，有问题吗？"

**小白**："我猜是 Spring AOP 的锅。`@SentinelResource` 是在 Bean 的代理对象上生效的，如果你在同一个类里调用自己的方法，不会走代理。"

**大师**："小白说得对。Spring AOP 默认使用 JDK 动态代理或 CGLIB 代理，只有通过代理对象调用的方法才会被拦截。同类内调用（self-invocation）绕过了代理，注解自然不生效。"

**小胖**："那怎么办？给每个 Service 都注入自己？"

**大师**："方案有好几种，我给你排个优先级。第一优先：把 `@SentinelResource` 放在 Controller 层，不要放在 Service 层，因为 Controller 本身就是入口。第二优先：如果确实需要保护 Service，确保通过注入的 Bean 调用，而不是 this 调用。第三优先：用编程式 API 直接写 `SphU.entry()`。"

**小白**："那 `sentinel-spring-webmvc-adapter` 和 `spring-cloud-starter-alibaba-sentinel` 呢？我看有些旧项目用的是前者，新项目用的是后者。"

**大师**："`sentinel-spring-webmvc-adapter` 是 Sentinel 官方提供的 Spring MVC 适配器，它通过注册一个 `SentinelWebInterceptor` 拦截所有 Web 请求。资源名默认就是 URL 路径（如 `/order/create`），你不需要手动加任何注解，所有 HTTP 接口自动被保护。但缺点是：它只能拦截 Controller 层，Service 层和内部调用管不了。"

**小胖**："那 `spring-cloud-starter-alibaba-sentinel` 呢？"

**大师**："它是 Spring Cloud Alibaba 生态的'全家桶'成员。它不仅包含了 Web MVC 的自动保护，还自动关联了 Feign、RestTemplate、Reactor 等组件。资源名默认也是 URL，但你用 `@SentinelResource` 可以自定义。"

**小白**："所以我应该这么理解：MVC Adapter 只管 Web 层自动保护，Spring Cloud Alibaba Starter 是 Web 层 + Feign + 注解的集合？"

**大师**："没错。而且还有一个重要区别：MVC Adapter 需要在 `WebMvcConfigurer` 中手动注册拦截器；而 Spring Cloud Alibaba Starter 通过自动配置类 `SentinelWebAutoConfiguration` 自动完成，只要引入依赖就行。"

**技术映射**：
- `@SentinelResource` 注解的核心属性：
  - `value`：资源名，必填，对应规则中的 resource 字段。
  - `blockHandler`：限流/熔断触发时执行的方法名。要求与原方法在同一个类中，返回值相同，参数多一个 `BlockException`。
  - `fallback`：业务异常（非 BlockException）时的兜底方法名。参数可多一个 `Throwable`，返回值相同。
  - `blockHandlerClass` / `fallbackClass`：当处理逻辑放在单独的工具类中时指定。
- blockHandler 和 fallback 的区别：blockHandler 处理 Sentinel 阻断（BlockException），fallback 处理业务异常（如 RuntimeException）。两者可以共存：blockHandler 执行时 fallback 不执行，fallback 执行时说明没有被 Sentinel 阻断而是业务自身出错了。

## 3 项目实战

### 3.1 环境准备

在 `pom.xml` 中引入 Spring Cloud Alibaba Sentinel Starter：

```xml
<dependency>
    <groupId>com.alibaba.cloud</groupId>
    <artifactId>spring-cloud-starter-alibaba-sentinel</artifactId>
    <version>2023.0.1.0</version>
</dependency>
```

`application.yml` 配置：

```yaml
spring:
  application:
    name: order-service
  cloud:
    sentinel:
      transport:
        dashboard: localhost:8080
        port: 8719
      eager: true
      web-context-unify: false  # 关闭 Web 上下文收敛，第 6 章详讲
```

### 3.2 分步实现

**步骤一：Controller 层注解接入（推荐方式）**

```java
@RestController
@RequestMapping("/order")
public class OrderController {

    @Autowired
    private OrderService orderService;

    /**
     * 下单接口：Sentinel 保护
     * blockHandler：限流时返回友好提示
     * fallback：业务异常时返回兜底数据
     */
    @GetMapping("/create")
    @SentinelResource(
        value = "createOrder",
        blockHandler = "createOrderBlock",
        fallback = "createOrderFallback"
    )
    public Result<String> createOrder(@RequestParam String skuId,
                                       @RequestParam Integer quantity) {
        // 业务逻辑：调用 Service
        String orderId = orderService.createOrder(skuId, quantity);
        return Result.success(orderId);
    }

    /**
     * BlockHandler：限流/熔断时的兜底
     * 参数签名要求：原方法参数 + BlockException（必须放在最后）
     * 返回值：与原方法一致
     */
    public Result<String> createOrderBlock(String skuId, Integer quantity,
                                            BlockException e) {
        log.warn("下单被限流, skuId={}, rule={}", skuId, e.getRule());
        return Result.error("SYSTEM_BUSY", "系统繁忙，请稍后再试");
    }

    /**
     * Fallback：业务异常时的兜底
     * 参数签名：原方法参数 + Throwable（可选）
     */
    public Result<String> createOrderFallback(String skuId, Integer quantity,
                                               Throwable t) {
        log.error("下单异常, skuId={}", skuId, t);
        return Result.error("BIZ_ERROR", "下单失败，请重试");
    }
}
```

**步骤二：Service 层注解接入（需注意代理问题）**

```java
@Service
public class StockService {

    // ❌ 错误写法：同类调用注解不生效
    public void checkAndDeduct(String skuId, int qty) {
        // 这里直接调 this.deductStock，不会走代理！
        deductStock(skuId, qty);
    }

    @SentinelResource(value = "deductStock", blockHandler = "deductBlock")
    public void deductStock(String skuId, int qty) {
        // 扣减库存逻辑
    }

    public void deductBlock(String skuId, int qty, BlockException e) {
        throw new BizException("库存扣减繁忙，请稍后重试");
    }

    // ✅ 正确写法：注入自身 Bean
    @Autowired
    @Lazy
    private StockService self;

    public void checkAndDeductCorrect(String skuId, int qty) {
        self.deductStock(skuId, qty);  // 通过代理调用
    }
}
```

**步骤三：Feign Client 注解接入**

```java
@FeignClient(
    name = "inventory-service",
    url = "http://localhost:8082",
    fallbackFactory = InventoryFallbackFactory.class  // 推荐使用 fallbackFactory
)
public interface InventoryClient {

    @GetMapping("/inventory/query")
    Result<Integer> queryStock(@RequestParam String skuId);

    @PostMapping("/inventory/deduct")
    Result<Boolean> deductStock(@RequestParam String skuId,
                                 @RequestParam Integer quantity);
}

@Component
public class InventoryFallbackFactory implements FallbackFactory<InventoryClient> {
    @Override
    public InventoryClient create(Throwable cause) {
        return new InventoryClient() {
            @Override
            public Result<Integer> queryStock(String skuId) {
                log.error("查询库存降级, skuId={}, cause={}", skuId, cause.getMessage());
                return Result.error("FALLBACK", "库存服务暂不可用，显示默认库存");
            }

            @Override
            public Result<Boolean> deductStock(String skuId, Integer quantity) {
                log.error("扣减库存降级, skuId={}", skuId, cause.getMessage());
                return Result.error("FALLBACK", "扣减库存失败，订单已记录待处理");
            }
        };
    }
}
```

**步骤四：自定义全局 BlockException 处理**

避免 BlockException 被全局异常处理器当作普通异常返回 500：

```java
@Component
public class SentinelBlockHandler implements BlockExceptionHandler {

    @Override
    public void handle(HttpServletRequest request, HttpServletResponse response,
                       BlockException e) throws Exception {
        response.setStatus(429);  // Too Many Requests
        response.setContentType("application/json;charset=UTF-8");

        Result<?> result;
        if (e instanceof FlowException) {
            result = Result.error("FLOW_LIMIT", "请求太频繁，请稍后再试");
        } else if (e instanceof DegradeException) {
            result = Result.error("DEGRADE", "服务暂不可用，已自动降级");
        } else if (e instanceof ParamFlowException) {
            result = Result.error("HOT_PARAM", "该商品访问过热，请稍后再试");
        } else if (e instanceof SystemBlockException) {
            result = Result.error("SYSTEM_BUSY", "系统繁忙，请稍后再试");
        } else if (e instanceof AuthorityException) {
            result = Result.error("FORBIDDEN", "无权访问该资源");
        } else {
            result = Result.error("BLOCK", "请求被拦截");
        }

        response.getWriter().write(JSON.toJSONString(result));
    }
}
```

**步骤五：验证注解生效**

运行以下测试脚本：

```bash
#!/bin/bash
echo "=== 1. 正常下单 ==="
curl -s "http://localhost:8090/order/create?skuId=SKU001&quantity=1"
# 期望: {"code":"SUCCESS","data":"ORDER_xxx"}

echo "=== 2. 触发限流（连续请求） ==="
for i in {1..10}; do
  curl -s "http://localhost:8090/order/create?skuId=SKU001&quantity=1" &
done
wait
# 期望: 部分返回 {"code":"SYSTEM_BUSY","msg":"系统繁忙，请稍后再试"}

echo "=== 3. 触发业务异常 ==="
curl -s "http://localhost:8090/order/create?skuId=&quantity=-1"
# 期望: {"code":"BIZ_ERROR","msg":"下单失败，请重试"} 或参数校验错误

echo "=== 4. 库存服务降级（需先停掉 inventory-service） ==="
curl -s "http://localhost:8090/order/create?skuId=SKU001&quantity=1"
# 期望: Feign 调用失败后走 fallbackFactory
```

**常见坑及解决方案**：

| 问题 | 原因 | 解决 |
|------|------|------|
| 注解不生效 | 同类调用 or blockHandler 签名错误 | 注入自身 Bean；严格按签名定义 |
| blockHandler 未执行 | 全局异常处理器先 catch 了 BlockException | 自定义 BlockExceptionHandler |
| fallback 覆盖了 blockHandler | fallback 同时捕获了 BlockException | 确保 blockHandler 和 fallback 分开定义 |
| Web 自动保护的资源名与规则不匹配 | Spring Cloud Alibaba 自动生成的资源名是 URL 路径 | 确认资源名一致，或用 `@SentinelResource` 自定义 |
| Feign fallback 不触发 | 未开启 `feign.sentinel.enabled=true` | 在配置中显式开启 |

## 4 项目总结

### 4.1 优点与缺点

| 维度 | @SentinelResource 注解 | Web MVC 自动保护 | 编程式 SphU API |
|------|----------------------|-----------------|----------------|
| 接入成本 | 低（一个注解） | 极低（零代码） | 中（需手写 try-catch） |
| 灵活性 | 高（可自定义资源名和兜底） | 低（资源名=URL） | 极高 |
| 可控性 | 中（注解限制在方法级） | 低（所有接口统一处理） | 极高 |
| 出错风险 | 中（签名错误静默失效） | 低 | 低（编译期可发现） |
| BlockHandler/Fallback | 支持 | 不支持（走全局处理器） | 手动实现 |
| Feign 适配 | 支持 | 不支持 | 需手动 |

### 4.2 适用场景

- **@SentinelResource**：需要自定义资源名、需要区分 BlockHandler 和 Fallback 的场景
- **Web MVC 自动保护**：快速接入、对资源名无特殊要求的 HTTP 接口
- **Feign FallbackFactory**：微服务间调用保护（推荐，能获取异常原因）
- **混合使用**：入口层（Controller）用注解，内部核心逻辑用编程式 API
- **不适用**：WebFlux 响应式场景（需引入 `sentinel-spring-webflux-adapter`）

### 4.3 注意事项

1. **blockHandler 方法签名**：必须与原始方法在同一个类（除非指定 `blockHandlerClass`），参数列表为原始参数 + BlockException，返回值相同。
2. **fallback 方法签名**：参数列表为原始参数 + Throwable（可选），返回值相同。注意 fallback 不应捕获 BlockException（由 blockHandler 处理）。
3. **Spring Cloud Alibaba 版本**：2023.x 对应 Spring Boot 3.x，需确认 Sentinel 兼容性。
4. **Feign 熔断启用**：需在配置中显式设置 `feign.sentinel.enabled=true`（部分版本默认 false）。

### 4.4 常见踩坑经验

| 故障现象 | 根因 | 解决方案 |
|---------|------|---------|
| 运维发现线上接口无保护 | 新接口漏加注解 | 自定义注解 + 编译期检查或架构约束 |
| 大促时 fallback 执行了但 blockHandler 没执行 | blockHandler 签名多了一个空格导致匹配失败 | 使用常量定义方法名，静态检查 |
| Sentinel 报错但不影响业务 | Spring Cloud Alibaba 的 Sentinel 默认不阻断 | 确认 `spring.cloud.sentinel.filter.enabled=true` |
| Feign 降级后一直不起 | Feign 超时配置与 Sentinel 超时冲突 | Feign connectTimeout 应大于 Sentinel RT 阈值 |

### 4.5 思考题

1. 如果同一个方法上同时配置了 `blockHandler` 和 `fallback`，当 BlockException 发生时，哪个会被执行？当 RuntimeException 发生时呢？
2. `sentinel-spring-webmvc-adapter` 自动保护的资源名是 `/order/create` 这样的 URL，而 `@SentinelResource(value="createOrder")` 自定义的资源名是业务名。如果两套机制同时启用，哪个优先生效？

### 4.6 推广计划

- **开发团队**：建立团队内部的 @SentinelResource 使用规范，包括统一资源命名、BlockHandler 模板、Fallback 模板。
- **测试团队**：增加注解签名校验的自动化测试（反射扫描 blockHandler 签名是否匹配）。
- **运维团队**：熟悉 Feign FallbackFactory 的降级行为，在服务异常时能快速判断是限流还是下游故障。
