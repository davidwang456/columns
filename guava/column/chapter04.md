# 第 4 章：Objects、MoreObjects 与基础对象方法增强

## 1 项目背景

在分布式电商系统的订单服务中，工程师小李负责排查一个诡异的 BUG：两个看起来完全相同的订单对象，在 HashMap 中却找不到。追查发现，问题出在 `equals()` 和 `hashCode()` 的实现上——订单类继承了父类的这两个方法，但父类只比较了订单 ID，而子类新增的用户信息字段却被忽略了。

更麻烦的是，当小李在日志中打印订单对象时，得到的是一串看不懂的内存地址：`Order@6a6824be`，根本无法快速定位问题。他不得不写了一个专门的 toString 工具类来格式化输出。

**业务场景**：订单、用户、商品等核心领域对象需要在集合中存储、比较、序列化，且需要友好的日志输出用于排查问题。

**痛点放大**：
- **equals/hashCode 实现不一致**：只重写一个方法，导致集合行为异常。
- **字段遗漏**：新增字段后忘记更新这两个方法，导致比较逻辑错误。
- **空值处理复杂**：比较时遇到 null 字段需要层层防御。
- **toString 输出不可读**：默认输出类名+哈希码，排查问题困难。
- **样板代码冗余**：每个 POJO 都要写几十行的 equals/hashCode/toString。

如果没有一套简洁可靠的对象方法实现方案，核心领域对象的正确性将无法保证。

**技术映射**：Guava 的 `Objects` 和 `MoreObjects` 提供了一系列静态方法，用于简化 `equals()`、`hashCode()` 和 `toString()` 的实现，同时正确处理 null 值。

---

## 2 项目设计

**场景**：技术分享会，讨论 POJO 最佳实践。

---

**小胖**：（看着满屏的 equals 代码）"我说，这 `equals` 也太啰嗦了吧！先判空，再判类型，再强转，再逐个字段比较，万一漏了一个字段就出事。这不就跟食堂阿姨数数一样，菜打多了打少了都容易乱？"

**小白**：（苦笑）"而且 `hashCode` 还得跟 `equals` 保持一致，这是 Java 的契约。很多人只写 `equals` 不写 `hashCode`，或者反过来，导致 `HashMap` 行为诡异。"

**大师**：（展示两段代码对比）"你们看，Guava 的 `Objects` 把这个过程简化了：

```java
// 传统写法：30+ 行
@Override
public boolean equals(Object o) {
    if (this == o) return true;
    if (o == null || getClass() != o.getClass()) return false;
    Order order = (Order) o;
    return Double.compare(order.amount, amount) == 0 &&
           Objects.equals(orderId, order.orderId) &&
           Objects.equals(userId, order.userId) &&
           Objects.equals(createTime, order.createTime);
}

// Guava 写法：5 行
@Override
public boolean equals(Object o) {
    if (this == o) return true;
    if (!(o instanceof Order)) return false;
    Order other = (Order) o;
    return Objects.equal(orderId, other.orderId)
        && Objects.equal(userId, other.userId)
        && Objects.equal(amount, other.amount)
        && Objects.equal(createTime, other.createTime);
}
```

**技术映射**：`Objects.equal(a, b)` 帮你处理了 null 的情况，不需要写 `a != null && a.equals(b)` 这种啰嗦代码。"

**小胖**："那 `hashCode` 呢？之前我听说 `Objects.hashCode` 有个什么坑？"

**小白**："你说得对。Guava 的 `Objects.hashCode(Object...)` 在旧版本中有性能问题——它创建临时数组。Java 7 的 `Objects.hash()` 也有同样问题。如果性能敏感，应该手动计算：

```java
// 高性能写法
@Override
public int hashCode() {
    int result = orderId != null ? orderId.hashCode() : 0;
    result = 31 * result + (userId != null ? userId.hashCode() : 0);
    result = 31 * result + Double.hashCode(amount);
    return result;
}
```

但 Guava 还有一个更好的工具 `MoreObjects`，专门处理 toString。"

**大师**："对，`MoreObjects.toStringHelper()` 是神器。看这个对比：

```java
// 传统 toString：手写拼接，容易错
@Override
public String toString() {
    return "Order{" +
           "orderId='" + orderId + '\'' +
           ", userId='" + userId + '\'' +
           ", amount=" + amount +
           ", createTime=" + createTime +
           '}';
}

// Guava 写法：自动处理 null，可读性好
@Override
public String toString() {
    return MoreObjects.toStringHelper(this)
        .omitNullValues()  // 自动跳过 null 字段
        .add("orderId", orderId)
        .add("userId", userId)
        .add("amount", amount)
        .add("createTime", createTime)
        .toString();
}
```

**技术映射**：`toStringHelper` 把 toString 从'字符串拼接'变成'字段声明'，降低了出错概率，输出也更易读。"

**小胖**："那 `MoreObjects` 还有什么别的功能？"

**大师**："还有一个 `firstNonNull(T first, T second)`，用于提供默认值：

```java
// 如果 displayName 为空，使用 userName
String name = MoreObjects.firstNonNull(displayName, userName);
```

但要注意，现代 Java 项目更推荐使用 Lombok 的 `@Data` 或 `@Value` 注解来自动生成这些方法。Guava 的价值在于：
1. 不能引入 Lombok 的老项目
2. 需要自定义逻辑的场景
3. Android 项目（Lombok 支持有限）

**技术映射**：Guava 的对象工具是'手动写法'时代的解决方案，现代项目可以优先评估 Lombok，但理解 Guava 的原理对调试和维护仍有价值。"

**小胖**："懂了！就是说 Guava 是保底方案，但新项目可以考虑更现代的方案。"

**小白**："还有一个细节——Guava 18+ 之后 `Objects` 的很多方法被标记为 `@Deprecated`，推荐用 Java 7 的 `java.util.Objects` 替代。但 `MoreObjects` 仍然是 Guava 独有且推荐的。"

**大师**："对，这是 Guava 的设计哲学——当 JDK 提供了同等功能时，Guava 会逐步废弃自己的实现，推动生态向前。"

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

### 分步实现：订单领域模型重构

**步骤目标**：用 `Objects` 和 `MoreObjects` 实现订单、用户、商品领域模型的 equals/hashCode/toString。

**代码实现**：

```java
package com.example.guava.demo;

import com.google.common.base.MoreObjects;
import com.google.common.base.Objects;

import java.math.BigDecimal;
import java.time.LocalDateTime;

/**
 * 订单领域模型 - 使用 Guava 实现对象方法
 */
public class Order {
    private Long orderId;
    private Long userId;
    private String orderNo;
    private BigDecimal totalAmount;
    private OrderStatus status;
    private LocalDateTime createTime;
    private LocalDateTime payTime;  // 可能为 null
    private Address address;  // 可能为 null

    public enum OrderStatus {
        PENDING_PAYMENT, PAID, SHIPPED, COMPLETED, CANCELLED
    }

    // ========== 构造器和 getter/setter 省略 ==========
    public Order() {}

    public Order(Long orderId, Long userId, String orderNo, 
                 BigDecimal totalAmount, OrderStatus status) {
        this.orderId = orderId;
        this.userId = userId;
        this.orderNo = orderNo;
        this.totalAmount = totalAmount;
        this.status = status;
        this.createTime = LocalDateTime.now();
    }

    // Getters and setters...
    public Long getOrderId() { return orderId; }
    public void setOrderId(Long orderId) { this.orderId = orderId; }
    public Long getUserId() { return userId; }
    public void setUserId(Long userId) { this.userId = userId; }
    public String getOrderNo() { return orderNo; }
    public void setOrderNo(String orderNo) { this.orderNo = orderNo; }
    public BigDecimal getTotalAmount() { return totalAmount; }
    public void setTotalAmount(BigDecimal totalAmount) { this.totalAmount = totalAmount; }
    public OrderStatus getStatus() { return status; }
    public void setStatus(OrderStatus status) { this.status = status; }
    public LocalDateTime getCreateTime() { return createTime; }
    public void setCreateTime(LocalDateTime createTime) { this.createTime = createTime; }
    public LocalDateTime getPayTime() { return payTime; }
    public void setPayTime(LocalDateTime payTime) { this.payTime = payTime; }
    public Address getAddress() { return address; }
    public void setAddress(Address address) { this.address = address; }

    // ========== 使用 Guava Objects 实现 equals ==========
    @Override
    public boolean equals(Object o) {
        if (this == o) return true;
        if (!(o instanceof Order)) return false;
        Order other = (Order) o;
        return Objects.equal(orderId, other.orderId)
            && Objects.equal(userId, other.userId)
            && Objects.equal(orderNo, other.orderNo)
            && Objects.equal(totalAmount, other.totalAmount)
            && Objects.equal(status, other.status)
            && Objects.equal(createTime, other.createTime)
            && Objects.equal(payTime, other.payTime)
            && Objects.equal(address, other.address);
    }

    // ========== hashCode 实现（高性能版本）==========
    @Override
    public int hashCode() {
        int result = orderId != null ? orderId.hashCode() : 0;
        result = 31 * result + (userId != null ? userId.hashCode() : 0);
        result = 31 * result + (orderNo != null ? orderNo.hashCode() : 0);
        result = 31 * result + (totalAmount != null ? totalAmount.hashCode() : 0);
        result = 31 * result + (status != null ? status.hashCode() : 0);
        result = 31 * result + (createTime != null ? createTime.hashCode() : 0);
        result = 31 * result + (payTime != null ? payTime.hashCode() : 0);
        result = 31 * result + (address != null ? address.hashCode() : 0);
        return result;
    }

    // ========== 使用 MoreObjects.toStringHelper 实现 toString ==========
    @Override
    public String toString() {
        return MoreObjects.toStringHelper(this)
            .omitNullValues()  // 跳过 null 字段，输出更简洁
            .add("orderId", orderId)
            .add("orderNo", orderNo)
            .add("userId", userId)
            .add("status", status)
            .add("totalAmount", totalAmount)
            .add("createTime", createTime)
            .add("payTime", payTime)  // 未支付时为 null，会被 omit
            .add("address", address)  // 可能为 null
            .toString();
    }

    // ========== 使用 firstNonNull 提供默认值 ==========
    public String getDisplayAddress() {
        return MoreObjects.firstNonNull(
            address != null ? address.getFullAddress() : null,
            "地址未填写"
        );
    }
}

/**
 * 地址子对象
 */
class Address {
    private String province;
    private String city;
    private String district;
    private String detail;
    private String zipCode;

    public Address(String province, String city, String district, 
                   String detail, String zipCode) {
        this.province = province;
        this.city = city;
        this.district = district;
        this.detail = detail;
        this.zipCode = zipCode;
    }

    public String getFullAddress() {
        return String.format("%s%s%s%s", province, city, district, detail);
    }

    @Override
    public boolean equals(Object o) {
        if (this == o) return true;
        if (!(o instanceof Address)) return false;
        Address other = (Address) o;
        return Objects.equal(province, other.province)
            && Objects.equal(city, other.city)
            && Objects.equal(district, other.district)
            && Objects.equal(detail, other.detail)
            && Objects.equal(zipCode, other.zipCode);
    }

    @Override
    public int hashCode() {
        return Objects.hashCode(province, city, district, detail, zipCode);
    }

    @Override
    public String toString() {
        return MoreObjects.toStringHelper(this)
            .add("province", province)
            .add("city", city)
            .add("district", district)
            .add("detail", detail)
            .add("zipCode", zipCode)
            .toString();
    }
}
```

### 测试验证

```java
package com.example.guava.demo;

import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

import java.math.BigDecimal;
import java.util.HashMap;
import java.util.HashSet;

public class OrderTest {

    @Test
    public void testEquals_sameObject() {
        Order order = createOrder();
        assertEquals(order, order);
    }

    @Test
    public void testEquals_equalObjects() {
        Order order1 = createOrder();
        Order order2 = createOrder();
        assertEquals(order1, order2);
    }

    @Test
    public void testEquals_differentFields() {
        Order order1 = createOrder();
        Order order2 = createOrder();
        order2.setTotalAmount(new BigDecimal("200.00"));
        assertNotEquals(order1, order2);
    }

    @Test
    public void testEquals_withNull() {
        Order order1 = createOrder();
        order1.setPayTime(null);
        Order order2 = createOrder();
        order2.setPayTime(null);
        assertEquals(order1, order2);
    }

    @Test
    public void testHashCode_consistency() {
        Order order = createOrder();
        int hash1 = order.hashCode();
        int hash2 = order.hashCode();
        assertEquals(hash1, hash2);
    }

    @Test
    public void testHashCode_equalObjects() {
        Order order1 = createOrder();
        Order order2 = createOrder();
        assertEquals(order1.hashCode(), order2.hashCode());
    }

    @Test
    public void testHashCode_inHashSet() {
        Order order1 = createOrder();
        Order order2 = createOrder();
        
        HashSet<Order> set = new HashSet<>();
        set.add(order1);
        assertTrue(set.contains(order2));
    }

    @Test
    public void testToString_format() {
        Order order = createOrder();
        String str = order.toString();
        
        assertTrue(str.contains("Order{"));
        assertTrue(str.contains("orderId=1"));
        assertTrue(str.contains("orderNo=ORDER001"));
        assertTrue(str.contains("status=PENDING_PAYMENT"));
        // payTime 为 null，应该被 omit
        assertFalse(str.contains("payTime"));
    }

    @Test
    public void testToString_withNullAddress() {
        Order order = createOrder();
        order.setAddress(null);
        
        String str = order.toString();
        assertFalse(str.contains("address"));  // null 被 omit
    }

    @Test
    public void testDisplayAddress_withAddress() {
        Order order = createOrder();
        order.setAddress(new Address("北京", "北京市", "朝阳区", "xxx街道", "100000"));
        
        assertEquals("北京北京市朝阳区xxx街道", order.getDisplayAddress());
    }

    @Test
    public void testDisplayAddress_withoutAddress() {
        Order order = createOrder();
        order.setAddress(null);
        
        assertEquals("地址未填写", order.getDisplayAddress());
    }

    private Order createOrder() {
        return new Order(
            1L, 
            100L, 
            "ORDER001", 
            new BigDecimal("100.00"),
            Order.OrderStatus.PENDING_PAYMENT
        );
    }
}
```

### 可能遇到的坑及解决方法

| 坑点 | 现象 | 解决方法 |
|------|------|----------|
| `Objects.hashCode` 性能问题 | 创建临时数组，大量对象时 GC 压力 | 性能敏感场景手动计算 hashCode |
| `omitNullValues` 隐藏重要信息 | 排查时看不到 null 字段 | 开发环境禁用 omit，生产启用 |
| 继承类 equals 问题 | 子类与父类比较结果异常 | 使用 `getClass()` 而非 `instanceof` 或统一父类处理 |
| 浮点数比较 | BigDecimal 等值但不同精度 | 使用 `compareTo` 而非 `equals` |

---

## 4 项目总结

### 优缺点对比

| 维度 | Guava Objects/MoreObjects | 手工实现 | Lombok @Data |
|------|---------------------------|----------|--------------|
| 代码量 | ★★★★ 简洁 | ★ 冗长 | ★★★★★ 无代码 |
| null 处理 | ★★★★★ 自动 | ★★★ 需手动 | ★★★★★ 自动 |
| 可读性 | ★★★★★ 意图清晰 | ★★★ 容易出错 | ★★★★★ 无干扰 |
| 灵活性 | ★★★★ 可控 | ★★★★★ 完全可控 | ★★ 注解控制 |
| 性能 | ★★★★ 良好 | ★★★★★ 最佳 | ★★★★ 良好 |
| 工具链依赖 | ★★★★★ 仅 Guava | ★★★★★ 无 | ★★ 需插件支持 |

### 适用场景

1. **不能使用 Lombok 的项目**：如某些银行、国企环境
2. **Android 开发**：Lombok 支持有限
3. **需要自定义逻辑**：如 toString 中需要格式化字段
4. **遗留项目改造**：逐步引入 Guava，而非一次性引入 Lombok
5. **学习目的**：理解 equals/hashCode/toString 的原理

### 不适用场景

1. **现代 Spring 项目**：优先使用 Lombok 的 `@Data` 或 `@Value`
2. **Kotlin 项目**：使用 data class
3. **记录类（Java 16+）**：使用 `record` 关键字

### 生产踩坑案例

**案例 1：BigDecimal 等值但不同精度**
```java
Order o1 = new Order(); o1.setAmount(new BigDecimal("100.0"));
Order o2 = new Order(); o2.setAmount(new BigDecimal("100.00"));
// equals 返回 false！
```
解决：金额比较使用 `compareTo` 而非 `equals`，或在 set 时统一精度。

**案例 2：继承层次中的 equals**
```java
// 子类与父类用 instanceof 比较，对称性被破坏
if (!(o instanceof Order)) return false;  // 子类 OrderVip 会出问题
```
解决：使用 `getClass() != o.getClass()` 或统一父类处理逻辑。

**案例 3：toString 在日志中泄露敏感信息**
```java
MoreObjects.toStringHelper(this)
    .add("password", password)  // 问题！
```
解决：敏感字段不要加入 toString，或用掩码处理。

### 思考题答案（第 3 章思考题 1）

> **问题**：`checkArgument` 和 `checkState` 的区别是什么？

**答案**：
- `checkArgument`：检查**方法参数**的有效性，抛 `IllegalArgumentException`，用于输入验证
- `checkState`：检查**对象状态**的有效性，抛 `IllegalStateException`，用于状态验证

**举例**：
- `checkArgument(amount > 0, "Amount must be positive")` —— 参数错误，调用方传错了
- `checkState(!closed, "Connection is closed")` —— 状态错误，对象当前处于不正确状态

### 新思考题

1. 在领域模型中，哪些字段应该参与 `equals` 比较？是否所有字段都要包含？请举例说明。
2. 如果使用 Lombok 的 `@Data`，如何自定义某个字段的 toString 格式（如日期格式化输出）？

### 推广计划提示

**开发**：
- 存量 POJO 逐步重构，优先从核心领域对象开始
- 新代码优先评估 Lombok，条件不允许则用 Guava

**测试**：
- 验证 equals 的对称性、传递性、一致性
- 测试 hashCode 在集合中的正确行为

**运维**：
- 在日志系统中配置 Order/User 等核心对象的格式化输出
- 监控对象序列化/反序列化异常
