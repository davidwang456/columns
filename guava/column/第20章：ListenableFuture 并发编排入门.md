# 第 20 章：ListenableFuture 并发编排入门

## 1 项目背景

在微服务架构的订单查询系统中，工程师小马遇到了性能瓶颈。一个订单详情页面需要调用 5 个下游服务：用户信息、商品信息、库存状态、物流信息、优惠信息。如果串行调用，总延迟是 5 个服务延迟之和，平均响应时间达到 800ms，用户体验很差。

团队尝试用 `CompletableFuture` 做并行调用，但遇到了回调地狱——5 个服务有多种依赖关系，有的可以并行，有的需要等前面结果，代码很快就变得难以理解和维护。

**业务场景**：多服务并行调用、异步流水线、超时控制、结果聚合等需要并发编排的场景。

**痛点放大**：
- **串行调用慢**：响应时间是各服务延迟之和。
- **回调地狱**：嵌套回调代码难以阅读和维护。
- **异常处理复杂**：多个并发任务中一个失败如何处理？
- **超时控制困难**：不同服务需要不同的超时策略。
- **结果聚合麻烦**：多个异步结果如何合并？

如果没有强大的异步编排工具，微服务的性能优势将难以发挥。

**技术映射**：Guava 的 `ListenableFuture` 提供了增强的 Future 接口，支持回调注册、组合变换、链式编排，配合 `Futures` 工具类可以实现复杂的异步流程控制。

---

## 2 项目设计

**场景**：性能优化评审会，讨论订单查询优化方案。

---

**小胖**：（看着调用链路图）"我说，这串行调用也太慢了吧！5 个服务一个一个调，加起来 800ms。这不就跟食堂一个一个窗口排队打饭一样吗？明明可以同时排好几个队！"

**小白**：（点头）"对，需要并行化。但并行化后问题来了——怎么知道所有请求都完成了？怎么把结果合并？一个失败了怎么办？"

**大师**：（在白板上画时序图）"Guava 的 `ListenableFuture` 解决了这些问题。看这段对比：

```java
// 传统 Future：阻塞等待
Future<User> userFuture = executor.submit(() -> getUser(userId));
User user = userFuture.get();  // 阻塞！
Future<Order> orderFuture = executor.submit(() -> getOrder(user, orderId));

// ListenableFuture：回调驱动
ListenableFuture<User> userFuture = executor.submit(() -> getUser(userId));
Futures.addCallback(userFuture, new FutureCallback<User>() {
    @Override
    public void onSuccess(User user) {
        // 获取到用户后，继续获取订单
        getOrderAsync(user, orderId);
    }
    @Override
    public void onFailure(Throwable t) {
        // 错误处理
        handleError(t);
    }
}, executor);
```

**技术映射**：`ListenableFuture` 就像是'异步任务的传声筒'——任务完成时它会主动通知你，而不是让你傻等着。"

**小胖**："那怎么把多个并行请求的结果合并？"

**小白**："用 `Futures.allAsList` 或 `Futures.successfulAsList`：

```java
ListenableFuture<User> userFuture = getUserAsync(userId);
ListenableFuture<Product> productFuture = getProductAsync(productId);
ListenableFuture<Inventory> inventoryFuture = getInventoryAsync(productId);

// 等待所有完成
ListenableFuture<List<Object>> all = Futures.allAsList(
    userFuture, productFuture, inventoryFuture
);

// 或者转换结果
ListenableFuture<OrderDetail> detailFuture = Futures.transform(
    all,
    results -> {
        User user = (User) results.get(0);
        Product product = (Product) results.get(1);
        Inventory inventory = (Inventory) results.get(2);
        return new OrderDetail(user, product, inventory);
    },
    executor
);
```

**大师**："还可以用 `Futures.transform` 做链式变换：

```java
ListenableFuture<String> future = executor.submit(() -> fetchFromServiceA());

ListenableFuture<Integer> transformed = Futures.transform(
    future,
    result -> Integer.parseInt(result),  // String -> Integer
    executor
);

ListenableFuture<Boolean> filtered = Futures.transform(
    transformed,
    value -> value > 100,  // 判断
    executor
);
```

**技术映射**：`ListenableFuture` 就像是'异步流水线'——你可以把多个处理步骤串联起来，数据自动流转，不需要在中间阻塞等待。"

**小胖**："那超时怎么控制？"

**小白**："用 `Futures.withTimeout`：

```java
ListenableFuture<User> userFuture = getUserAsync(userId);

// 3 秒超时，超时时返回默认值
ListenableFuture<User> withTimeout = Futures.withTimeout(
    userFuture,
    3, TimeUnit.SECONDS,
    scheduledExecutor,
    () -> User.EMPTY  // 超时默认值
);
```

或者更严格的超时（取消原任务）：

```java
ScheduledExecutorService scheduler = Executors.newScheduledThreadPool(1);
scheduler.schedule(() -> userFuture.cancel(true), 3, TimeUnit.SECONDS);
```

**大师**："但要注意 `ListenableFuture` 和 Java 8 `CompletableFuture` 的关系。Guava 的 `ListenableFuture` 出现更早，API 设计更成熟，但 `CompletableFuture` 是 Java 标准。新项目可以优先用 `CompletableFuture`，但理解 Guava 的设计有助于处理遗留代码。

**技术映射**：`ListenableFuture` 是 Java 异步编程的'前辈'，它的设计思想影响了后来的 `CompletableFuture`，学习它能帮你建立正确的异步编程思维模型。"

---

## 3 项目实战

### 环境准备

```xml
<dependency>
    <groupId>com.google.guava</groupId>
    <artifactId>guava</artifactId>
    <version>33.0.0-jre</version>
</dependency>
```

### 分步实现：订单查询并行化

**步骤目标**：用 `ListenableFuture` 构建并行的订单查询系统。

**代码实现**：

```java
package com.example.guava.demo;

import com.google.common.util.concurrent.*;

import java.util.*;
import java.util.concurrent.*;

/**
 * 订单查询并行化 - 使用 ListenableFuture
 */
public class OrderQueryService {

    private final ExecutorService executor = Executors.newFixedThreadPool(10);
    private final ScheduledExecutorService scheduler = Executors.newScheduledThreadPool(2);
    
    // 模拟下游服务
    private final UserService userService = new UserService();
    private final ProductService productService = new ProductService();
    private final InventoryService inventoryService = new InventoryService();
    private final LogisticsService logisticsService = new LogisticsService();
    private final PromotionService promotionService = new PromotionService();

    /**
     * 串行查询（对比用）
     */
    public OrderDetail queryOrderSerial(String orderId) throws Exception {
        long start = System.currentTimeMillis();
        
        Order order = getOrder(orderId);
        User user = userService.getUser(order.getUserId());
        Product product = productService.getProduct(order.getProductId());
        Inventory inventory = inventoryService.getInventory(order.getProductId());
        Logistics logistics = logisticsService.getLogistics(orderId);
        Promotion promotion = promotionService.getPromotion(orderId);
        
        long elapsed = System.currentTimeMillis() - start;
        System.out.println("串行查询耗时: " + elapsed + " ms");
        
        return new OrderDetail(order, user, product, inventory, logistics, promotion);
    }

    /**
     * 并行查询（无依赖）
     */
    public ListenableFuture<OrderDetail> queryOrderParallel(String orderId) {
        long start = System.currentTimeMillis();
        
        // 1. 先获取订单基本信息
        Order order = getOrder(orderId);
        
        // 2. 并行查询 5 个下游服务
        ListenableFuture<User> userFuture = 
            Futures.withTimeout(
                submit(() -> userService.getUser(order.getUserId())),
                200, TimeUnit.MILLISECONDS,
                scheduler,
                () -> User.empty()
            );
        
        ListenableFuture<Product> productFuture = 
            submit(() -> productService.getProduct(order.getProductId()));
        
        ListenableFuture<Inventory> inventoryFuture = 
            submit(() -> inventoryService.getInventory(order.getProductId()));
        
        ListenableFuture<Logistics> logisticsFuture = 
            submit(() -> logisticsService.getLogistics(orderId));
        
        ListenableFuture<Promotion> promotionFuture = 
            submit(() -> promotionService.getPromotion(orderId));
        
        // 3. 等待所有完成并合并结果
        ListenableFuture<List<Object>> allFutures = Futures.allAsList(
            userFuture, productFuture, inventoryFuture, logisticsFuture, promotionFuture
        );
        
        return Futures.transform(allFutures, results -> {
            long elapsed = System.currentTimeMillis() - start;
            System.out.println("并行查询耗时: " + elapsed + " ms");
            
            return new OrderDetail(
                order,
                (User) results.get(0),
                (Product) results.get(1),
                (Inventory) results.get(2),
                (Logistics) results.get(3),
                (Promotion) results.get(4)
            );
        }, executor);
    }

    /**
     * 有依赖关系的查询（链式）
     */
    public ListenableFuture<OrderDetail> queryOrderWithDependency(String orderId) {
        long start = System.currentTimeMillis();
        
        // 第一步：获取订单
        ListenableFuture<Order> orderFuture = submit(() -> getOrder(orderId));
        
        // 第二步：基于订单获取用户（依赖订单）
        ListenableFuture<User> userFuture = Futures.transformAsync(
            orderFuture,
            order -> submit(() -> userService.getUser(order.getUserId())),
            executor
        );
        
        // 第三步：并行获取其他信息（依赖订单）
        ListenableFuture<Product> productFuture = Futures.transformAsync(
            orderFuture,
            order -> submit(() -> productService.getProduct(order.getProductId())),
            executor
        );
        
        // 合并所有结果
        ListenableFuture<List<Object>> all = Futures.allAsList(
            orderFuture, userFuture, productFuture
        );
        
        return Futures.transform(all, results -> {
            long elapsed = System.currentTimeMillis() - start;
            System.out.println("依赖查询耗时: " + elapsed + " ms");
            
            return new OrderDetail(
                (Order) results.get(0),
                (User) results.get(1),
                (Product) results.get(2),
                null, null, null
            );
        }, executor);
    }

    /**
     * 部分失败容忍（successfulAsList）
     */
    public ListenableFuture<List<Optional<Object>>> queryWithPartialFailure() {
        ListenableFuture<String> future1 = submit(() -> "Success1");
        ListenableFuture<String> future2 = submit(() -> { throw new RuntimeException("Fail2"); });
        ListenableFuture<String> future3 = submit(() -> "Success3");
        
        // successfulAsList：失败返回 null，不抛异常
        return Futures.successfulAsList(future1, future2, future3);
    }

    private <T> ListenableFuture<T> submit(Callable<T> callable) {
        return Futures.submit(callable, executor);
    }

    private Order getOrder(String orderId) {
        simulateDelay(10);
        return new Order(orderId, "USER_001", "PROD_001");
    }

    private void simulateDelay(int ms) {
        try {
            Thread.sleep(ms);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        }
    }

    // ========== 领域模型 ==========
    public static class Order {
        private final String id;
        private final String userId;
        private final String productId;
        
        public Order(String id, String userId, String productId) {
            this.id = id;
            this.userId = userId;
            this.productId = productId;
        }
        public String getId() { return id; }
        public String getUserId() { return userId; }
        public String getProductId() { return productId; }
    }

    public static class User {
        private final String id;
        private final String name;
        public User(String id, String name) { this.id = id; this.name = name; }
        public static User empty() { return new User("", "未知用户"); }
    }

    public static class Product { 
        private final String id; 
        public Product(String id) { this.id = id; }
    }
    public static class Inventory { 
        private final String productId; 
        public Inventory(String productId) { this.productId = productId; }
    }
    public static class Logistics { 
        private final String orderId; 
        public Logistics(String orderId) { this.orderId = orderId; }
    }
    public static class Promotion { 
        private final String orderId; 
        public Promotion(String orderId) { this.orderId = orderId; }
    }

    public static class OrderDetail {
        private final Order order;
        private final User user;
        private final Product product;
        private final Inventory inventory;
        private final Logistics logistics;
        private final Promotion promotion;
        
        public OrderDetail(Order order, User user, Product product,
                          Inventory inventory, Logistics logistics, Promotion promotion) {
            this.order = order;
            this.user = user;
            this.product = product;
            this.inventory = inventory;
            this.logistics = logistics;
            this.promotion = promotion;
        }
        
        @Override
        public String toString() {
            return "OrderDetail{order=" + order.getId() + ", user=" + user.name + "}";
        }
    }

    // 模拟服务
    private static class UserService {
        User getUser(String id) {
            simulateDelay(100);
            return new User(id, "User-" + id);
        }
    }
    private static class ProductService {
        Product getProduct(String id) {
            simulateDelay(150);
            return new Product(id);
        }
    }
    private static class InventoryService {
        Inventory getInventory(String productId) {
            simulateDelay(80);
            return new Inventory(productId);
        }
    }
    private static class LogisticsService {
        Logistics getLogistics(String orderId) {
            simulateDelay(200);
            return new Logistics(orderId);
        }
    }
    private static class PromotionService {
        Promotion getPromotion(String orderId) {
            simulateDelay(120);
            return new Promotion(orderId);
        }
    }

    private static void simulateDelay(int ms) {
        try {
            Thread.sleep(ms);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        }
    }

    // ========== 测试入口 ==========
    public static void main(String[] args) throws Exception {
        OrderQueryService service = new OrderQueryService();
        String orderId = "ORDER_001";

        System.out.println("=== 串行查询 ===");
        OrderDetail serial = service.queryOrderSerial(orderId);
        System.out.println("结果: " + serial);
        System.out.println();

        System.out.println("=== 并行查询 ===");
        ListenableFuture<OrderDetail> parallel = service.queryOrderParallel(orderId);
        OrderDetail parallelResult = parallel.get();
        System.out.println("结果: " + parallelResult);
        System.out.println();

        System.out.println("=== 依赖查询 ===");
        ListenableFuture<OrderDetail> dep = service.queryOrderWithDependency(orderId);
        OrderDetail depResult = dep.get();
        System.out.println("结果: " + depResult);
        System.out.println();

        System.out.println("=== 部分失败容忍 ===");
        ListenableFuture<List<Optional<Object>>> partial = service.queryWithPartialFailure();
        List<Optional<Object>> results = partial.get();
        results.forEach(r -> System.out.println("结果: " + r.orElse(null)));

        service.executor.shutdown();
        service.scheduler.shutdown();
    }
}
```

### 测试验证

```java
package com.example.guava.demo;

import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

import java.util.List;
import java.util.Optional;
import java.util.concurrent.TimeUnit;

public class OrderQueryServiceTest {

    private OrderQueryService service;

    @BeforeEach
    public void setUp() {
        service = new OrderQueryService();
    }

    @Test
    public void testParallelFasterThanSerial() throws Exception {
        // 注意：这只是一个概念验证，实际测试需要考虑线程调度的不确定性
        
        String orderId = "TEST_001";
        
        // 串行查询
        long serialStart = System.currentTimeMillis();
        service.queryOrderSerial(orderId);
        long serialTime = System.currentTimeMillis() - serialStart;
        
        // 并行查询
        long parallelStart = System.currentTimeMillis();
        var future = service.queryOrderParallel(orderId);
        future.get(5, TimeUnit.SECONDS);
        long parallelTime = System.currentTimeMillis() - parallelStart;
        
        // 并行应该更快（至少快 2 倍）
        System.out.println("Serial: " + serialTime + " ms, Parallel: " + parallelTime + " ms");
        assertTrue(parallelTime < serialTime / 2);
    }

    @Test
    public void testQueryOrderParallel() throws Exception {
        var future = service.queryOrderParallel("ORDER_001");
        OrderQueryService.OrderDetail result = future.get(5, TimeUnit.SECONDS);
        
        assertNotNull(result);
        assertNotNull(service.toString());  // 验证对象构造成功
    }

    @Test
    public void testQueryWithDependency() throws Exception {
        var future = service.queryOrderWithDependency("ORDER_002");
        OrderQueryService.OrderDetail result = future.get(5, TimeUnit.SECONDS);
        
        assertNotNull(result);
    }

    @Test
    public void testPartialFailure() throws Exception {
        var future = service.queryWithPartialFailure();
        List<Optional<Object>> results = future.get(5, TimeUnit.SECONDS);
        
        assertEquals(3, results.size());
        assertTrue(results.get(0).isPresent());  // Success1
        assertFalse(results.get(1).isPresent()); // Fail2 -> null
        assertTrue(results.get(2).isPresent());  // Success3
    }

    @Test
    public void testTimeout() throws Exception {
        // 测试带超时的查询
        // 由于 UserService 延迟 100ms，超时 200ms 应该足够
        var future = service.queryOrderParallel("ORDER_003");
        OrderQueryService.OrderDetail result = future.get(5, TimeUnit.SECONDS);
        assertNotNull(result);
    }
}
```

### 可能遇到的坑及解决方法

| 坑点 | 现象 | 解决方法 |
|------|------|----------|
| 回调中阻塞 | `onSuccess` 中调用 `get()` 导致死锁 | 使用 `transform` 链式处理 |
| 线程池耗尽 | 大量并发请求提交失败 | 使用有界队列 + 拒绝策略 |
| 异常被吞 | `transform` 中异常未传播 | 检查 `ExecutionException` |
| 超时不准确 | `withTimeout` 精度问题 | 预留缓冲时间 |

---

## 4 项目总结

### 核心操作速查表

| 操作 | 方法 | 说明 |
|------|------|------|
| 注册回调 | `Futures.addCallback` | 成功/失败处理 |
| 链式转换 | `Futures.transform` | 同步变换 |
| 异步链式 | `Futures.transformAsync` | 异步变换 |
| 等待全部 | `Futures.allAsList` | 全部完成，任一失败则失败 |
| 容忍失败 | `Futures.successfulAsList` | 全部完成，失败返回 null |
| 超时控制 | `Futures.withTimeout` | 超时返回默认值 |
| 提交任务 | `Futures.submit` | 创建 ListenableFuture |

### 与 CompletableFuture 对比

| 特性 | ListenableFuture | CompletableFuture |
|------|------------------|-------------------|
| API 丰富度 | ★★★★ 丰富 | ★★★★★ 更丰富 |
| 链式语法 | ★★★ 函数式 | ★★★★★ 流畅 |
| 异常处理 | ★★★★ 完善 | ★★★★★ 更完善 |
| 标准兼容 | ★★★ Guava 特有 | ★★★★★ Java 标准 |
| 遗留代码 | ★★★★★ 广泛使用 | ★★★ 需迁移 |

### 适用场景

1. **遗留系统维护**：已有 Guava 代码库
2. **复杂依赖关系**：多阶段、有条件、有依赖的异步流程
3. **部分失败容忍**：某些服务失败仍可返回部分结果
4. **超时精细控制**：不同阶段不同超时策略

### 与 Java 8+ 的协作

```java
// ListenableFuture -> CompletableFuture
ListenableFuture<T> listenableFuture = ...;
CompletableFuture<T> completableFuture = 
    listenableFutureToCompletableFuture(listenableFuture);

// CompletableFuture -> ListenableFuture (Guava 28+)
CompletableFuture<T> cf = ...;
ListenableFuture<T> lf = Futures.submit(() -> cf.get(), executor);
```

### 思考题答案（第 19 章思考题 1）

> **问题**：如何设计缓存分析工具识别热点和冷数据？

**答案**：结合 `RemovalListener` 和 `CacheStats`：

```java
// 1. 统计访问频次（用外部计数器）
ConcurrentHashMap<String, AtomicLong> accessCount = new ConcurrentHashMap<>();

// 2. 在获取时记录
cache.get(key);
accessCount.incrementAndGet(key);

// 3. 分析时排序
accessCount.entrySet().stream()
    .sorted(Map.Entry.<String, AtomicLong>comparingByValue().reversed())
    .limit(100)  // Top 100 热点
    .collect(toList());

// 4. 被快速淘汰的可能是冷数据
removalListener = (notification) -> {
    if (notification.getCause() == RemovalCause.SIZE) {
        // 记录刚进入就被淘汰的数据
        coldDataCandidates.add(notification.getKey());
    }
};
```

### 新思考题

1. 如何设计一个 ListenableFuture 的调试工具，可视化异步任务执行流程？
2. 比较 Guava `Futures` 和 RxJava 在异步编排上的设计差异。

### 推广计划提示

**开发**：
- 新项目优先用 `CompletableFuture`
- 维护遗留 Guava 代码时理解 `ListenableFuture` 设计
- 复杂编排场景考虑 RxJava/Reactor

**测试**：
- 验证超时生效
- 测试部分失败场景
- 检查线程池使用情况

**运维**：
- 监控异步任务队列堆积
- 设置超时告警
- 关注线程池健康度
