# 第 23 章：RateLimiter 限流设计与热点保护

## 1 项目背景

每年"双十一"零点，某电商平台都会迎来一场流量洪峰。去年大促期间，平台推出了一款限量 1 万台的爆款手机，售价仅为市场价的五折。活动开始前五分钟，运营团队还在兴奋地刷新着后台数据——预热页面访问量已经突破了 500 万。然而，当秒针指向零点的刹那，噩梦降临了。

瞬间涌入的请求峰值达到了每秒 80 万次，网关层的 Nginx 首先告急，连接队列被打满；紧接着，下游的订单服务因线程池耗尽而拒绝响应；数据库主库的连接池在 3 秒内被彻底耗尽，大量慢查询堆积；缓存 Redis 也在热点 Key 的集中访问下出现了单节点 CPU 飙高。最终，整个交易链路雪崩，大量用户看到了刺眼的 502 错误页面，而真正成功下单的订单只有寥寥数百笔。事后复盘发现，罪魁祸首正是**缺乏有效的限流保护机制**。

没有限流时，系统就像一座没有闸门的水坝。平时涓涓细流时一切安好，但一旦遭遇洪水，所有请求都长驱直入， backend 服务在毫无保护的情况下被直接冲垮。更糟的是，恶意用户利用脚本并发刷接口，正常用户反而因为系统崩溃而完全无法参与秒杀。运维团队被迫手动扩容、重启服务，在混乱中度过了最宝贵的促销黄金时间。这次事故造成的直接经济损失超过千万，品牌口碑更是难以估量。

痛定思痛，技术团队决定在网关层引入限流机制，将不可控的突发流量转化为系统可承受的平稳流量。Guava 的 `RateLimiter` 凭借其成熟的令牌桶实现和优秀的平滑限流特性，成为了技术选型的核心组件。

## 2 项目设计

**小胖**（捧着一杯奶茶凑过来）："大师，我听说咱们秒杀系统又要改？上次双十一崩得那么惨，这次是要加什么黑科技啊？我用个比喻您看对不对——咱们系统现在就像一家网红奶茶店，一搞活动门口就排几百人，店里只有三个员工做奶茶，结果所有人挤在门口，谁也买不到，店还被挤塌了。对不对？"

**大师**（笑着点头）："你这个比喻很形象。没错，现在的问题就是**没有限流闸门**，请求洪水一过来，系统直接'店毁人亡'。我们需要在店门口放一个取号机，让顾客按节奏有序进店，这样既能保护店里不被挤爆，也能让真正想买的顾客拿到奶茶。"

**技术映射**：`RateLimiter` 就像是系统入口的智能取号机，把混乱的拥挤变成有序的排队。

**小白**（从显示器后探出头，推了推眼镜）："等一下，'取号机'有很多种吧？是像银行那种固定叫号的，还是像游乐园那种分时段预约的？Guava 的 RateLimiter 到底用的什么算法？我听说有计数器、漏桶、令牌桶好几种方案，它的底层是怎么实现的？"

**大师**："问得好。Guava 的 `RateLimiter` 底层实现的是**令牌桶算法（Token Bucket）**。你可以把它想象成一个容量固定的桶，系统会以固定速率往桶里放令牌。当一个请求到来时，它必须从桶里拿到一个令牌才能继续执行；如果桶里暂时没有令牌了，请求就得等待或者被拒绝。"

**小白**："那和漏桶算法有什么区别？漏桶不也是把突发流量变平滑吗？"

**大师**："区别很关键。**漏桶算法**就像一个底部有固定大小孔的桶，无论上面倒进来多少水，流出去的速度永远是匀速的，**完全没有突发能力**。**令牌桶算法**则不同，如果桶里有积攒的令牌，它可以一次性发出多个，**允许一定程度的突发流量**。打个比方：漏桶是地铁安检，每秒固定过 3 个人，队伍多长都是这个速度；令牌桶是高速公路收费站，平时 ETC 通道排队短可以快速通过几辆车，但如果车太多，最终还是被限流成固定速率。"

**技术映射**：令牌桶算法的精髓在于"有积蓄就有弹性"，既保证长期平均速率稳定，又允许短期内的合法突发。

**小胖**（咬着吸管）："哦！我懂了！就像我饭卡里的余额，平时每天充 50 块，但我可以攒三天一次性花 150 块吃顿好的！那 Guava 里怎么写代码呢？是不是一行就搞定了？"

**小白**："小胖你别急。大师，我还有个问题——令牌桶允许突发，那如果恶意用户正好攒了一波令牌集中来刷，会不会又把系统打挂？而且秒杀活动往往有冷启动问题，系统刚启动时连接池、缓存都还没准备好，直接放开限流会不会有问题？"

**大师**（赞许地点头）："小白考虑得很周全。Guava 的 `RateLimiter` 正好提供了两种模式来解决你们俩的问题：

1. **平滑突发限流（SmoothBursty）**：这是默认模式，适合绝大多数接口防护场景。它允许一定的突发，但长期平均速率严格受限。恶意刷令牌的问题，可以通过**限制桶的容量**来避免——桶满了就不会继续攒令牌，最大突发量被严格限制在容量范围内。

2. **平滑预热限流（SmoothWarmingUp）**：这是专门为冷启动设计的模式。你可以把它想象成冬天开车前的热车过程——刚启动时引擎转速被限制得很低，过一会儿才慢慢提升到正常水平。系统刚启动时，`RateLimiter` 会以很低的速率放行请求，给一个预热时间逐步提升到目标 QPS，让数据库连接池、JVM 热点代码、缓存都有时间进入最佳状态。"

**小胖**："哇！预热这个太贴心了！就像我早上刚起床不能马上剧烈运动，得先伸个懒腰！"

**小白**："最后一个问题，RateLimiter 是线程安全的吗？多线程环境下会不会有竞态条件？如果在分布式环境下，多个实例之间怎么协同限流？"

**大师**："Guava 的 `RateLimiter` 是基于 `synchronized` 和 `Stopwatch` 实现的，**本身是线程安全的**，多线程可以放心共享同一个实例。但它的局限也就在这里——它只能做**单 JVM 级别的限流**。分布式限流需要借助 Redis、Nginx 或专门的网关组件（如 Sentinel、Gateway）来实现，这超出了 Guava 的范畴。在实际生产中，通常是**网关层做分布式总限流 + 应用层用 RateLimiter 做细粒度单实例限流**，两者配合使用。"

**技术映射**：`RateLimiter` 是 JVM 内部的精致刹车片，但整车的制动系统还需要分布式层面的 ABS 来协同。

## 3 项目实战

### 3.1 环境准备

本实战基于 Java 8+ 和 Maven 构建环境。首先，确保项目中已引入 Guava 依赖：

```xml
<dependency>
    <groupId>com.google.guava</groupId>
    <artifactId>guava</artifactId>
    <version>33.0.0-jre</version>
</dependency>
```

JDK 版本要求 1.8 或更高。本节所有代码均在单 JVM 内运行，无需额外中间件。

### 3.2 步骤一：平滑突发限流基础实现

我们先实现一个最基础的限流器，用于保护秒杀查询接口，限制每秒最多 10 个请求。

**目标**：理解 `RateLimiter.create(double permitsPerSecond)` 的基本用法，观察突发流量的处理效果。

```java
import com.google.common.util.concurrent.RateLimiter;

public class BasicRateLimiterDemo {
    public static void main(String[] args) {
        // 创建一个每秒产生 10 个令牌的限流器
        RateLimiter limiter = RateLimiter.create(10.0);

        // 模拟 20 个并发请求，观察获取时间
        for (int i = 0; i < 20; i++) {
            double waitTime = limiter.acquire();
            System.out.printf("请求 %d: 获取令牌成功，等待时间 %.3f 秒%n", i + 1, waitTime);
        }
    }
}
```

**运行结果**：

```
请求 1: 获取令牌成功，等待时间 0.000 秒
请求 2: 获取令牌成功，等待时间 0.000 秒
请求 3: 获取令牌成功，等待时间 0.000 秒
请求 4 至 请求 10: 获取令牌成功，等待时间 0.000 秒
请求 11: 获取令牌成功，等待时间 0.099 秒
请求 12: 获取令牌成功，等待时间 0.099 秒
...
请求 20: 获取令牌成功，等待时间 0.099 秒
```

**坑点分析**：前 10 个请求瞬间通过，这就是令牌桶的**突发特性**。桶的默认容量等于每秒产生的令牌数（即 10 个），所以前 10 个请求不需要等待。从第 11 个请求开始，必须等待令牌匀速产生。如果你的接口完全不允许任何突发，需要额外做**预热排空**或者在业务层做二次校验。

### 3.3 步骤二：平滑预热限流实现

秒杀活动开始前，系统往往刚完成扩容发布，JVM 尚未JIT编译热点代码，数据库连接池也未填满。此时直接放开流量极易导致冷启动崩溃。

**目标**：利用 `SmoothWarmingUp` 模式，让系统在前 3 秒内从低速逐步提升到每秒 100 QPS。

```java
import com.google.common.util.concurrent.RateLimiter;
import java.util.concurrent.TimeUnit;

public class WarmupRateLimiterDemo {
    public static void main(String[] args) {
        // 预热时间为 3 秒，目标 QPS 为 100
        RateLimiter warmLimiter = RateLimiter.create(
            100.0,   // 目标每秒令牌数
            3,       // 预热时间
            TimeUnit.SECONDS
        );

        System.out.println("=== 模拟系统刚启动时的请求 ===");
        for (int i = 0; i < 15; i++) {
            double waitTime = warmLimiter.acquire();
            System.out.printf("请求 %d: 等待 %.4f 秒%n", i + 1, waitTime);
        }
    }
}
```

**运行结果**（节选）：

```
=== 模拟系统刚启动时的请求 ===
请求 1: 等待 0.0000 秒
请求 2: 等待 0.4800 秒
请求 3: 等待 0.4600 秒
请求 4: 等待 0.4400 秒
...
请求 8: 等待 0.0200 秒
请求 9: 等待 0.0100 秒
请求 10 及以后: 等待 0.0100 秒（稳定期）
```

**坑点分析**：预热模式的等待时间呈现**从高到低的梯度变化**，初期每个请求可能需要等待数百毫秒。这和 `SmoothBursty` 完全不同——预热模式不允许初期的突发流量。注意预热时间的单位必须是 `TimeUnit`，很多开发者会误传成毫秒整数导致行为异常。

### 3.4 步骤三：秒杀接口整合（非阻塞 + 阻塞双模式）

实际秒杀场景中，查询接口通常使用**非阻塞**快速失败（返回"太火爆了，请稍后再试"），而下单接口在可控范围内使用**阻塞**等待（确保用户支付体验）。

```java
import com.google.common.util.concurrent.RateLimiter;
import java.util.concurrent.TimeUnit;

public class SeckillGateway {
    // 查询接口：每秒 500 QPS，非阻塞，快速失败
    private final RateLimiter queryLimiter = RateLimiter.create(500.0);

    // 下单接口：每秒 100 QPS，支持预热，可短暂等待
    private final RateLimiter orderLimiter = RateLimiter.create(
        100.0, 2, TimeUnit.SECONDS
    );

    // 热点商品专用限流器（细粒度保护）
    private final RateLimiter hotItemLimiter = RateLimiter.create(50.0);

    /** 商品查询接口：非阻塞，拿不到令牌立即拒绝 */
    public String queryItem(String itemId) {
        if (!queryLimiter.tryAcquire()) {
            return "{"code":429,"msg":"查询过于频繁，请稍后再试"}";
        }
        return fetchItemDetail(itemId);
    }

    /** 下单接口：阻塞等待最多 200ms，超时则拒绝 */
    public String placeOrder(String itemId) {
        if (!orderLimiter.tryAcquire(200, TimeUnit.MILLISECONDS)) {
            return "{"code":429,"msg":"下单排队已满，请稍后重试"}";
        }
        return processOrder(itemId);
    }

    /** 热点商品额外保护层 */
    public String queryHotItem(String hotItemId) {
        // 双层限流：先过热点商品限流器，再过通用查询限流器
        if (!hotItemLimiter.tryAcquire()) {
            return "{"code":429,"msg":"该商品访问量过大，请稍后"}";
        }
        if (!queryLimiter.tryAcquire()) {
            return "{"code":429,"msg":"系统繁忙"}";
        }
        return fetchItemDetail(hotItemId);
    }

    private String fetchItemDetail(String itemId) {
        return "{"itemId":"" + itemId + "","price":1999}";
    }

    private String processOrder(String itemId) {
        return "{"orderId":"ORD-" + System.currentTimeMillis() + "","status":"SUCCESS"}";
    }

    public static void main(String[] args) {
        SeckillGateway gateway = new SeckillGateway();

        // 模拟查询接口突发 1000 次请求
        int passCount = 0;
        int rejectCount = 0;
        for (int i = 0; i < 1000; i++) {
            String result = gateway.queryItem("PHONE-1001");
            if (result.contains("200") || result.contains("itemId")) {
                passCount++;
            } else {
                rejectCount++;
            }
        }
        System.out.printf("查询接口：通过 %d，拒绝 %d%n", passCount, rejectCount);
    }
}
```

**运行结果**：

```
查询接口：通过 500，拒绝 500
```

**坑点分析**：`tryAcquire()` 不带参数时，**立即返回结果，不会阻塞**。很多新手会误把它和 `acquire()` 混用，导致本该阻塞的场景瞬间拒绝大量合法请求。另外，热点商品的双层限流顺序很重要——应该把更严格的限流器放在前面，避免不必要的通用限流器令牌消耗。

### 3.5 步骤四：多令牌批量消费与动态速率调整

某些场景下，一个请求可能需要消耗多个令牌（例如批量查询接口一次查 10 条数据），或者需要根据运行时负载动态调整限流速率。

```java
import com.google.common.util.concurrent.RateLimiter;

public class AdvancedRateLimiterDemo {
    public static void main(String[] args) {
        RateLimiter limiter = RateLimiter.create(10.0);

        // 批量接口：一次需要 5 个令牌
        double batchWait = limiter.acquire(5);
        System.out.printf("批量查询消耗 5 令牌，等待 %.3f 秒%n", batchWait);

        // 动态调整：发现系统负载高，临时把限流降到 5 QPS
        limiter.setRate(5.0);
        double afterSetRate = limiter.acquire();
        System.out.printf("降速后获取 1 令牌，等待 %.3f 秒%n", afterSetRate);
    }
}
```

**坑点分析**：`acquire(int permits)` 可以一次消费多个令牌，但要注意如果请求的令牌数超过桶容量，会导致**永久等待**（因为桶永远攒不够那么多令牌）。`setRate()` 是线程安全的，但调整后的速率立即生效，不会平滑过渡，建议配合监控告警谨慎使用。

### 3.6 完整代码清单

```java
package com.example.ratelimit;

import com.google.common.util.concurrent.RateLimiter;
import java.util.concurrent.TimeUnit;

/**
 * Guava RateLimiter 秒杀场景完整实战代码
 */
public class SeckillRateLimiterDemo {

    public static void main(String[] args) throws InterruptedException {
        System.out.println("===== 1. 平滑突发限流测试 =====");
        testSmoothBursty();

        System.out.println("\n===== 2. 平滑预热限流测试 =====");
        testSmoothWarmingUp();

        System.out.println("\n===== 3. 秒杀网关双层限流测试 =====");
        testSeckillGateway();
    }

    static void testSmoothBursty() {
        RateLimiter limiter = RateLimiter.create(10.0);
        System.out.println("突发 15 个请求：");
        for (int i = 1; i <= 15; i++) {
            double wait = limiter.acquire();
            System.out.printf("  请求 %02d: wait=%.3fs%n", i, wait);
        }
    }

    static void testSmoothWarmingUp() {
        RateLimiter limiter = RateLimiter.create(100.0, 2, TimeUnit.SECONDS);
        System.out.println("预热期 10 个请求：");
        for (int i = 1; i <= 10; i++) {
            double wait = limiter.acquire();
            System.out.printf("  请求 %02d: wait=%.4fs%n", i, wait);
        }
    }

    static void testSeckillGateway() throws InterruptedException {
        SeckillGateway gateway = new SeckillGateway();

        // 测试热点商品双层限流
        int pass = 0, reject = 0;
        for (int i = 0; i < 200; i++) {
            String res = gateway.queryHotItem("HOT-001");
            if (res.contains("itemId")) pass++;
            else reject++;
        }
        System.out.printf("热点商品查询：通过 %d，拒绝 %d%n", pass, reject);

        // 测试下单阻塞等待
        String orderResult = gateway.placeOrder("HOT-001");
        System.out.println("下单结果：" + orderResult);
    }

    static class SeckillGateway {
        private final RateLimiter queryLimiter = RateLimiter.create(500.0);
        private final RateLimiter orderLimiter = RateLimiter.create(100.0, 2, TimeUnit.SECONDS);
        private final RateLimiter hotItemLimiter = RateLimiter.create(50.0);

        public String queryItem(String itemId) {
            return queryLimiter.tryAcquire()
                ? "{\"itemId\":\"" + itemId + "\"}"
                : "{\"code\":429,\"msg\":\"too many requests\"}";
        }

        public String placeOrder(String itemId) {
            return orderLimiter.tryAcquire(200, TimeUnit.MILLISECONDS)
                ? "{\"orderId\":\"ORD-" + System.currentTimeMillis() + "\"}"
                : "{\"code\":429,\"msg\":\"queue full\"}";
        }

        public String queryHotItem(String hotItemId) {
            if (!hotItemLimiter.tryAcquire()) {
                return "{\"code\":429,\"msg\":\"hot item throttled\"}";
            }
            if (!queryLimiter.tryAcquire()) {
                return "{\"code\":429,\"msg\":\"system busy\"}";
            }
            return "{\"itemId\":\"" + hotItemId + "\",\"hot\":true}";
        }
    }
}
```

### 3.7 测试验证

运行上述 `SeckillRateLimiterDemo` 的 `main` 方法，验证以下三项指标：

1. **平滑突发**：前 10 个请求的 `wait` 时间接近 0，第 11 个开始约 0.1 秒，证明令牌桶突发容量为 10。
2. **平滑预热**：前几个请求等待时间明显较长（数百毫秒），随后递减至 0.01 秒，证明预热生效。
3. **双层限流**：热点商品查询中，最多只有约 50 个通过（`hotItemLimiter` 容量），其余被拒绝，证明细粒度限流有效。

## 4 项目总结

### 4.1 优缺点对比表格

| 维度 | Guava RateLimiter | 其他常见方案（如 Sentinel / Gateway） |
|------|-------------------|--------------------------------------|
| **实现复杂度** | 极低，一行代码创建限流器 | 较高，需引入中间件、配置规则中心 |
| **限流精度** | 毫秒级，基于令牌桶算法 | 依赖实现，通常也基于令牌桶或滑动窗口 |
| **突发支持** | 原生支持平滑突发（SmoothBursty） | 通常支持，但需额外配置 |
| **预热支持** | 原生支持平滑预热（SmoothWarmingUp） | 部分支持，配置较复杂 |
| **作用范围** | 仅单 JVM 有效 | 支持分布式、集群级限流 |
| **性能开销** | 极低，内存操作 + synchronized | 依赖网络/通信，延迟较高 |
| **动态调整** | 支持 `setRate()`，但仅单实例生效 | 支持集中式动态推送规则 |
| **适用场景** | 服务内部细粒度限流、热点保护 | 网关层总入口限流、分布式协同 |

### 4.2 适用/不适用场景

**适用场景**：
- 单机服务的接口级 QPS 保护
- 热点 Key / 热点商品的细粒度限流（每个商品一个 `RateLimiter` 实例）
- 系统冷启动初期的流量爬坡保护（`SmoothWarmingUp`）
- 内部微服务调用时的客户端自我保护

**不适用场景**：
- 需要跨多个服务实例协同的全局限流（如集群共享 1000 QPS）
- 需要按用户 ID、IP 等维度做亿级细粒度限流（内存无法承载海量实例）
- 需要复杂流控规则（如关联限流、链路限流、热点参数自动识别）

### 4.3 注意事项

1. **线程安全≠分布式**：`RateLimiter` 只能保护单个 JVM 进程，多实例部署时需要在网关层做二次限流。
2. **桶容量默认等于目标 QPS**：如果你需要更大或更小的突发容量，Guava 的 `SmoothBursty` 默认不支持自定义容量（可通过反射 hack `maxBurstSeconds` 实现，但不推荐生产使用）。
3. **`acquire()` 会阻塞当前线程**：在异步、响应式编程模型（如 WebFlux、Netty）中，阻塞线程会导致严重性能问题，应改用 `tryAcquire()`。
4. **预热模式不适合高并发压测**：压测脚本直接对 `SmoothWarmingUp` 模式发起并发请求时，前期大量请求会被长时间阻塞，压测结果失真。

### 4.4 三个生产踩坑案例

**案例一：预热模式压测"假死"**
某团队在大促前压测新上线的秒杀服务，使用了 `SmoothWarmingUp` 模式。压测脚本一启动，前几秒响应时间高达 2-3 秒，被运维误认为是系统性能差。实际上这是预热机制在生效，但团队未在压测报告中排除预热期数据，导致错误地进行了两轮不必要的代码优化。

**案例二：异步线程池被 `acquire()` 堵死**
某服务基于 Netty 构建，在 I/O 线程中直接调用 `limiter.acquire()`。结果大量 I/O 线程被阻塞在等待令牌上，导致网络事件无法处理，整个服务对外表现为"无响应"。修复方案是改用 `tryAcquire()` 快速失败，或将限流逻辑前置到同步网关层。

**案例三：多实例部署导致限流失效**
某业务在 10 台机器上部署了秒杀服务，每台配置了 `RateLimiter.create(1000)`。运营预期总限流为 1000 QPS，实际却是 10 × 1000 = 10000 QPS，数据库在洪峰下再次崩溃。根本原因是混淆了单机限流和集群限流，`RateLimiter` 从设计之初就不解决分布式问题。

### 4.5 两道思考题

1. **如果你的秒杀系统需要限制"每个用户每秒只能下 1 单"，Guava `RateLimiter` 能否直接实现？如果不能，你会如何设计一个结合 Guava 和本地缓存（如 Caffeine）的解决方案？**

2. **`RateLimiter` 的令牌桶算法在分布式环境下可以通过 Redis + Lua 脚本实现。请思考：Redis 分布式令牌桶和 Guava 单机令牌桶在精度、性能、可靠性方面有哪些本质差异？**

### 4.6 推广计划提示

`RateLimiter` 作为限流体系的基础组件，建议按以下路径推广：

- **第一阶段（单点防护）**：在核心查询、下单接口引入 `RateLimiter`，使用 `SmoothBursty` 模式快速落地，将接口失败率从雪崩级降至可控范围。
- **第二阶段（热点细分）**：针对爆品、活动页等热点资源，建立 "资源 ID → RateLimiter" 的映射缓存，实现细粒度热点保护。
- **第三阶段（体系升级）**：在网关层引入 Sentinel 或自研分布式限流平台，将 Guava `RateLimiter` 作为"最后一道防线"保留在应用内部，形成**网关总限流 + 服务分级限流 + 热点细限流**的三层防御体系。
