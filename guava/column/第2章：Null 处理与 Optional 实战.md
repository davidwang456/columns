# 第 2 章：Null 处理与 Optional 实战

## 1 项目背景

在电商平台的订单查询系统中，工程师小张最近遇到了一个棘手的线上问题。一个看似简单的订单详情查询接口，竟然在生产环境多次抛出 `NullPointerException`，导致用户看到 500 错误页面，客服投诉量激增。

追查日志发现，问题出在一个多层嵌套的调用链上：`getOrder()` 返回 null → 调用 `getOrder().getUser()` → 调用 `getUser().getAddress()` → 调用 `getAddress().getZipCode()`。空指针可能在任何一层抛出，日志却只是简单地打印了栈顶信息，根本无法定位到底是哪一层返回了 null。

**业务场景**：订单详情查询需要串联订单、用户、地址、优惠券等多个数据源，任何一环缺失都不能阻断整个流程，但也不能用默认值欺骗用户。

**痛点放大**：
- **NullPointerException 成为悬顶之剑**：每行代码都可能抛出 NPE，防御性代码占据 30% 以上的行数。
- **无法区分"有意的空"与"无意的空"**：null 可能代表"该订单没有优惠"，也可能代表"查询出错了"，调用方无法分辨。
- **代码可读性崩溃**：为了防止 NPE，代码里充斥着 `if (obj != null)` 的嵌套地狱，业务逻辑被淹没在防御代码中。
- **排查成本极高**：一旦出问题，需要在多层调用链中逐级排查 null 来源。

如果没有一种显式的空值表达方式，系统的可维护性将持续恶化。

**技术映射**：Guava 的 `Optional<T>` 借鉴了函数式编程思想，强制开发者显式处理空值情况，与 Java 8 的 `java.util.Optional` 理念一致，但 Guava 版本兼容 Java 6+。

---

## 2 项目设计

**场景**：技术部复盘会上，讨论如何根治 NPE 问题。

---

**小胖**：（抱着笔记本电脑）"我说，这 NPE 问题太烦人了！我写个查询接口，光判断 null 就写了十几行，代码跟俄罗斯套娃似的，一层套一层。这不就跟去食堂吃饭，得先确认食堂开门、窗口有饭、饭没卖完一样吗？"

**小白**：（皱眉）"你那比喻虽然糙，但问题说到了。关键是 `null` 本身没有语义。比如 `findOrder()` 返回 null，到底是订单不存在，还是查询出错了，还是用户没权限？调用方根本不知道。"

**大师**：（在白板上画了个图）"这就是问题核心。`null` 是 Java 设计上的历史包袱，它违反了**失败优先**原则。Guava 提供的 `Optional<T>` 是一个容器对象，里面要么有值（`Present`），要么为空（`Absent`），强迫你做出选择。

**技术映射**：`Optional` 就像是外卖包装盒——要么里面真有饭盒（Present），要么就是个空盒子（Absent），你不能假装里面有饭，必须打开确认了才能吃。"

**小胖**："那用 `Optional` 我得怎么改代码？是不是每个返回值都包一层？"

**小白**："不只如此。你还要思考：这个值**可能为空是正常情况**，还是**不应该为空，空了就报错**？Guava 的 `Optional` 给了你三种处理方式：`
- `or(default)`：为空时返回默认值
- `orNull()`：为空时返回 null（向后兼容）
- `get()`：为空时抛异常（快速失败）

**大师**："最重要的是**API 设计规范**。如果方法可能返回空值，就应该返回 `Optional<T>` 而不是 `T`。这样调用方一看签名就知道要处理空的情况。

**技术映射**：`Optional` 是一种**编译期约束**，把运行时的 NPE 风险转化为编译期的强制处理，降低了遗漏处理空值的概率。"

**小胖**："那如果我已经有一堆老代码返回 null，怎么迁移？"

**小白**："Guava 提供了 `Optional.fromNullable(T nullable)`，可以把可能为 null 的值包成 `Optional`。还有一个坑需要注意——`Optional` 本身不能为 null！如果你返回 `Optional`，调用方又传给另一个方法，那个方法拿到 null 的 `Optional` 一样会 NPE。

**大师**："最佳实践是：
1. 方法返回值用 `Optional<T>` 代替 `T` 或 `null`
2. 方法内部用 `Optional.of(T)` 包装非空值，用 `Optional.absent()` 表示空
3. 不要用 `Optional` 做字段、集合元素或序列化对象（它设计初衷不是干这个的）
4. 不要为了链式调用滥用 `Optional`，Java 8 Stream 的 `filter/map` 更合适

**技术映射**：`Optional` 是接口契约的一部分，改变了"无值"的表达语义，从隐式变为显式。"

**小胖**："懂了，就是让 null 无处遁形，逼我正面面对它！"

**大师**："对。而且 Guava 还有一个配套工具 `Preconditions.checkNotNull`，配合 `Optional` 使用，可以构建完整的空值防御体系：
- 输入参数：用 `checkNotNull` 确保不为空
- 输出结果：用 `Optional` 让调用方处理空值

**技术映射**：这套组合拳把空值风险控制在边界上，核心业务逻辑可以专注于业务本身，而不是防御性代码。"

---

## 3 项目实战

### 环境准备

沿用第 1 章的 Maven 配置：

```xml
<dependency>
    <groupId>com.google.guava</groupId>
    <artifactId>guava</artifactId>
    <version>33.0.0-jre</version>
</dependency>
```

### 分步实现：订单查询空值安全重构

**步骤目标**：用 `Optional` 重构一个多层嵌套的订单查询服务，消除 NPE 风险。

**代码实现**：

```java
package com.example.guava.demo;

import com.google.common.base.Optional;
import com.google.common.base.Preconditions;

/**
 * 订单查询服务 - 使用 Guava Optional 重构
 */
public class OrderQueryService {

    // ========== 领域模型 ==========
    public static class Order {
        private final String orderId;
        private final Optional<User> user;  // 订单可能没有关联用户（匿名订单）
        private final Optional<Coupon> coupon;  // 订单可能没有使用优惠券

        public Order(String orderId, User user, Coupon coupon) {
            this.orderId = Preconditions.checkNotNull(orderId);
            this.user = Optional.fromNullable(user);
            this.coupon = Optional.fromNullable(coupon);
        }

        public String getOrderId() { return orderId; }
        public Optional<User> getUser() { return user; }
        public Optional<Coupon> getCoupon() { return coupon; }
    }

    public static class User {
        private final String userId;
        private final Optional<Address> address;  // 用户可能没有设置地址

        public User(String userId, Address address) {
            this.userId = Preconditions.checkNotNull(userId);
            this.address = Optional.fromNullable(address);
        }

        public String getUserId() { return userId; }
        public Optional<Address> getAddress() { return address; }
    }

    public static class Address {
        private final String city;
        private final Optional<String> zipCode;  // 某些地区可能没有邮编

        public Address(String city, String zipCode) {
            this.city = Preconditions.checkNotNull(city);
            this.zipCode = Optional.fromNullable(zipCode);
        }

        public String getCity() { return city; }
        public Optional<String> getZipCode() { return zipCode; }
    }

    public static class Coupon {
        private final String couponCode;
        private final double discount;

        public Coupon(String couponCode, double discount) {
            this.couponCode = Preconditions.checkNotNull(couponCode);
            this.discount = discount;
        }

        public String getCouponCode() { return couponCode; }
        public double getDiscount() { return discount; }
    }

    // ========== 重构前：NPE 高危代码 ==========
    public String getZipCodeLegacy(String orderId) {
        Order order = findOrder(orderId);
        return order.getUser().getAddress().getZipCode();  // NPE 高危！
    }

    // ========== 重构后：使用 Optional 安全链式调用 ==========
    public Optional<String> getZipCodeSafe(String orderId) {
        return findOrder(orderId)
            .flatMap(Order::getUser)           // 尝试获取用户
            .flatMap(User::getAddress)         // 尝试获取地址
            .flatMap(Address::getZipCode);     // 尝试获取邮编
    }

    // ========== 业务场景：计算订单折扣后价格 ==========
    public double calculateFinalPrice(String orderId, double originalPrice) {
        Order order = findOrder(orderId).orNull();
        if (order == null) {
            throw new NotFoundException("Order not found: " + orderId);
        }

        // 安全获取优惠券折扣，没有则默认为 0
        double discount = order.getCoupon()
            .transform(Coupon::getDiscount)
            .or(0.0);

        return originalPrice * (1 - discount);
    }

    // ========== 业务场景：获取配送信息（必须有地址才能配送）==========
    public DeliveryInfo getDeliveryInfo(String orderId) {
        Order order = findOrder(orderId)
            .orNull();
        
        Preconditions.checkNotNull(order, "Order not found: %s", orderId);

        Address address = order.getUser()
            .flatMap(User::getAddress)
            .orNull();
        
        Preconditions.checkNotNull(address, 
            "Cannot deliver - no address for order: %s", orderId);

        return new DeliveryInfo(
            address.getCity(),
            address.getZipCode().or("N/A")
        );
    }

    // ========== 模拟数据源 ==========
    private Optional<Order> findOrder(String orderId) {
        // 模拟数据库查询，某些订单不存在
        if ("ORDER_001".equals(orderId)) {
            return Optional.of(new Order(orderId, 
                new User("USER_001", new Address("北京", "100000")),
                new Coupon("SAVE10", 0.1)));
        } else if ("ORDER_002".equals(orderId)) {
            // 匿名订单，没有用户
            return Optional.of(new Order(orderId, null, null));
        } else if ("ORDER_003".equals(orderId)) {
            // 有用户但没有地址
            return Optional.of(new Order(orderId, 
                new User("USER_002", null),
                null));
        }
        return Optional.absent();
    }

    // ========== 辅助类 ==========
    public static class NotFoundException extends RuntimeException {
        public NotFoundException(String message) { super(message); }
    }

    public static class DeliveryInfo {
        public final String city;
        public final String zipCode;
        public DeliveryInfo(String city, String zipCode) {
            this.city = city;
            this.zipCode = zipCode;
        }
    }
}
```

### 测试验证

```java
package com.example.guava.demo;

import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

public class OrderQueryServiceTest {

    private final OrderQueryService service = new OrderQueryService();

    @Test
    public void testGetZipCode_safeChain() {
        // 完整链路都存在
        Optional<String> zipCode = service.getZipCodeSafe("ORDER_001");
        assertTrue(zipCode.isPresent());
        assertEquals("100000", zipCode.get());
    }

    @Test
    public void testGetZipCode_orderNotFound() {
        // 订单不存在
        Optional<String> zipCode = service.getZipCodeSafe("ORDER_999");
        assertFalse(zipCode.isPresent());
    }

    @Test
    public void testGetZipCode_noUser() {
        // 订单存在但没有用户（匿名订单）
        Optional<String> zipCode = service.getZipCodeSafe("ORDER_002");
        assertFalse(zipCode.isPresent());
    }

    @Test
    public void testGetZipCode_noAddress() {
        // 有用户但没有地址
        Optional<String> zipCode = service.getZipCodeSafe("ORDER_003");
        assertFalse(zipCode.isPresent());
    }

    @Test
    public void testCalculatePrice_withCoupon() {
        double price = service.calculateFinalPrice("ORDER_001", 100.0);
        assertEquals(90.0, price, 0.01);  // 9折
    }

    @Test
    public void testCalculatePrice_withoutCoupon() {
        double price = service.calculateFinalPrice("ORDER_002", 100.0);
        assertEquals(100.0, price, 0.01);  // 无折扣
    }

    @Test
    public void testDeliveryInfo_success() {
        OrderQueryService.DeliveryInfo info = service.getDeliveryInfo("ORDER_001");
        assertEquals("北京", info.city);
        assertEquals("100000", info.zipCode);
    }

    @Test
    public void testDeliveryInfo_orderNotFound() {
        assertThrows(OrderQueryService.NotFoundException.class, () -> {
            service.getDeliveryInfo("ORDER_999");
        });
    }

    @Test
    public void testDeliveryInfo_noAddress() {
        assertThrows(NullPointerException.class, () -> {
            service.getDeliveryInfo("ORDER_003");
        });
    }
}
```

### 可能遇到的坑及解决方法

| 坑点 | 现象 | 解决方法 |
|------|------|----------|
| `Optional` 本身为 null | 调用方返回 null 的 Optional 导致 NPE | 返回 `Optional.absent()` 而非 null |
| `get()` 在空值时抛异常 | 未判断 `isPresent()` 直接调用 `get()` | 优先用 `or(default)` 或 `orNull()` |
| 滥用 `Optional` 做字段 | 序列化/反序列化问题 | 字段用 `@Nullable` 注解，方法返回用 `Optional` |
| 与 Java 8 Optional 混用 | 类型不兼容 | 统一使用 Guava 或 Java 8 版本，不要混用 |

---

## 4 项目总结

### 优缺点对比

| 维度 | Guava Optional | null 传统方式 | Java 8 Optional |
|------|----------------|---------------|-----------------|
| 空值显式性 | ★★★★★ 强迫处理 | ★ 隐式风险 | ★★★★★ 强迫处理 |
| 链式调用 | ★★★★ `transform`/`flatMap` | ★ 嵌套地狱 | ★★★★★ Stream 集成 |
| API 丰富度 | ★★★★ 功能完整 | ★ 无 | ★★★★ 功能完整 |
| 兼容性 | ★★★★★ Java 6+ | ★★★★★ 原生 | ★★★ Java 8+ |
| 性能 | ★★★★ 微小对象开销 | ★★★★★ 无 | ★★★★ 微小对象开销 |

### 适用场景

1. **方法返回值**：可能为空的情况必须显式声明
2. **数据转换链**：多层可能为空的属性访问
3. **配置读取**：某些配置项可能缺失
4. **缓存查询**：缓存命中与否的不确定结果
5. **第三方接口**：返回值可靠性未知的外部调用

### 不适用场景

1. **集合元素**：集合本身可以表达空（空集合）
2. **方法参数**：用 `Preconditions.checkNotNull` 更合适
3. **序列化字段**：`Optional` 不是 POJO 的合适字段类型
4. **性能极度敏感场景**：每个 Optional 对象都有微小开销

### 生产踩坑案例

**案例 1：Optional 做 DTO 字段导致 JSON 序列化异常**
```java
// 坑：Jackson 默认无法序列化 Guava Optional
public class UserDTO {
    private Optional<String> phone;  // 问题！
}
```
解决：配置 Jackson 模块，或在序列化前转为普通值。

**案例 2：返回 null 的 Optional**
```java
// 坑：方法返回 null 而非 Optional.absent()
public Optional<Order> findOrder(String id) {
    return null;  // 调用方会 NPE！
}
```
解决：启用静态分析工具（如 NullAway）检测。

**案例 3：混淆 Guava 和 Java 8 Optional**
```java
// 坑：混用导致类型不兼容
import com.google.common.base.Optional;  // Guava
// 与 java.util.Optional 不能互转
```
解决：项目统一使用一种，通过 Checkstyle 强制。

### 思考题答案（第 1 章思考题 1）

> **问题**：`Preconditions.checkNotNull` 和 Java 8 的 `Objects.requireNonNull` 有什么区别？

**答案**：
1. **异常类型不同**：Guava 的 `checkNotNull` 抛 `NullPointerException`，Java 8 的 `requireNonNull` 可以自定义异常消息
2. **返回值不同**：Guava 返回被检查的对象（支持链式），Java 8 返回 void
3. **使用场景**：Guava 适合 `this.field = checkNotNull(param)` 的链式赋值，Java 8 适合独立检查
4. **兼容性**：Guava 支持 Java 6+，Java 8 API 需要 Java 8+

### 新思考题

1. 在什么场景下应该选择 `or(default)` 而不是 `orNull()`？请举例说明。
2. 如果团队同时存在返回 null 和返回 Optional 的老代码，如何设计一个兼容层逐步迁移？

### 推广计划提示

**开发**：
- Code Review 中要求：可能为空的返回值必须使用 `Optional`
- 老代码逐步重构，优先从 DAO 层开始

**测试**：
- 重点测试 `Optional.absent()` 分支的边界情况
- 验证空值链式调用的短路行为

**运维**：
- 监控 NPE 发生率，观察引入 Optional 后的改善效果
