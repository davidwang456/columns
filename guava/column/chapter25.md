# 第 25 章：EventBus 解耦事件驱动流程

## 1 项目背景

在一家日均订单量超过百万的电商平台中，订单中心是整个交易链路的核心枢纽。一笔订单从用户点击"提交订单"开始，其生命周期会经历"已创建→已支付→已出库→已发货→已签收→已完成"等多个状态节点。每当订单状态发生变更，周边系统都需要做出响应：支付成功后库存系统要扣减库存，物流系统要生成运单，营销系统要发放积分与优惠券，数据中心要更新实时看板，风控系统要扫描异常行为，客服系统要同步工单状态。

在早期的实现中，这一切都是在 `OrderService` 中以硬编码的方式直接串行调用。`paySuccess()` 方法里依次调用 `stockService.deduct()`、`logisticsService.createWaybill()`、`promotionService.grantPoints()`、`dataService.report()`……代码像一根越滚越粗的麻绳，紧紧捆绑着订单核心逻辑与周边业务。当运营团队提出"支付成功后需要触发发票自动开具"时，研发同学不得不修改订单核心代码并重新发版；当物流接口响应变慢时，支付回调接口的整体 RT（响应时间）跟着飙升，甚至出现超时；当某个下游系统因为升级短暂不可用时，订单状态机里抛出的异常直接拖垮了主流程，导致用户明明付款成功，页面却显示"支付失败"。

工程师们逐渐意识到：订单中心只应该负责"宣布状态变更了这一事实"，而不应该关心"谁需要响应以及如何响应"。发布者与订阅者之间需要一面"透明的墙"——这正是事件驱动架构要解决的问题。本章将深入讲解 Guava 提供的 EventBus（同步/异步事件总线），它以轻量级、零外部依赖、基于注解的优雅方式，实现了进程内模块间的解耦与事件广播。

## 2 项目设计

**小胖**："我算是看明白了！咱们订单系统现在就像一家网红火锅店的收银台，顾客付完账后，收银员不仅要喊一嗓子'二号桌结完了'让服务员去收拾，还得亲自跑到后厨催单、跑到门口叫号、跑到仓库拿饮料——累得跟陀螺似的！"

**大师**："你这比喻还真贴切。收银台的本职是'收钱'，但现在却被迫串行地处理一系列本不该由它直接负责的事情。那如果我们在店里装一套广播系统呢？收银员只管对着话筒说一句'二号桌已结账'，后厨、保洁、叫号员谁关心这件事谁就听着，各司其职，互不干扰。"

**技术映射**：EventBus 的本质就是进程内的"广播系统"——发布者只管 `post(event)`，订阅者通过 `@Subscribe` 声明自己关心什么事件，双方通过事件类型自动匹配，彻底解耦。

**小白**："等等，直接调方法不是最简单直接吗？`orderService.pay()` 里一行一行往下调，IDE 里点一下就能跳转到下游逻辑，调试也直观。引入 EventBus 这种中间层，会不会是过度设计？而且调试的时候事件在总线里'转了一道'，链路追踪岂不是变麻烦了？"

**大师**："直接调用的确简单，但它隐含的假设是'调用方必须知道被调用方的存在、接口和顺序'。我们用火锅店类比：如果收银台直接跑到后厨递单子，那后厨换了位置、增加了新窗口、或者某天需要同时给外卖平台派单，收银台是不是都得重新适应？EventBus 相当于在店里装了一个'公共告示栏'——收银员贴一张'二号桌已结账'的便签，后厨、保洁、外卖调度员各自来看自己关心的内容。新增一个'发票开具员'，他只需要在告示栏前挂个钩子（`@Subscribe`），收银员完全不需要改代码。"

**小白**："有道理。但我还有个疑问：如果这些响应操作里有些是耗时的，比如生成物流面单需要调用第三方 API，那放在同步 EventBus 里岂不是会阻塞主流程？"

**大师**："问得好！这正是 Guava 提供 `AsyncEventBus` 的原因。同步 EventBus 适合轻量级、必须立即完成的旁路逻辑，比如内存中的状态更新或缓存失效；而 `AsyncEventBus` 背后挂一个线程池，把事件投递到订阅者方法的动作变成异步的。你可以把它想象成火锅店的'智能分拣机器人'：收银员把订单往传送带上一放，机器人自动把单子分给后厨、保洁、外卖站，收银员不用站在原地等。"

**技术映射**：`EventBus` 是同步阻塞投递，订阅者跑在发布者线程上；`AsyncEventBus` 是异步非阻塞投递，订阅者跑在指定线程池上。两者适用场景不同，不可混用。

**小胖**："那异步听起来全是好处啊！咱们全换成 AsyncEventBus 得了？"

**小白**："不可能全是好处。异步了之后就涉及线程安全、事件顺序、异常处理、线程池资源耗尽等问题。比如订单状态从'已支付'到'已发货'，如果两个事件异步乱序执行，库存系统和物流系统看到的状态会不会自相矛盾？再比如某个订阅者抛了异常，其他订阅者还能收到事件吗？事件如果没人订阅，是静默丢弃还是能给个提醒？"

**大师**："小白这一串追问非常到位。先说顺序：如果你需要严格保序，可以给 `AsyncEventBus` 配一个单线程的 `Executor`，就像快递分拣中心只开一条流水线，包裹按到达顺序依次处理；如果能接受乱序但追求吞吐，就用多线程池。再说异常：在同步 EventBus 中，某个订阅者抛异常会阻断同事件后续的订阅者——就像广播喊'着火了'，第一个人听到后吓得把广播喇叭砸了，后面的人就听不到了。因此生产环境强烈建议每个 `@Subscribe` 方法内部自行 try-catch。对于无人订阅的事件，Guava 提供了 `DeadEvent` 机制，你可以专门写一个 `@Subscribe public void handle(DeadEvent event)` 来做兜底告警或日志记录。"

**技术映射**：EventBus 默认不对异常做隔离，也不做重试；`DeadEvent` 是框架提供的兜底机制，用于捕获未被消费的事件，避免事件在静默中消失。

**小胖**："那如果同一个事件有七八个订阅者，会不会像抢红包一样互相拖累？比如物流模块卡住了，积分模块是不是也得干等着？"

**大师**："同步 EventBus 确实会串行执行所有匹配的订阅者方法，前一个卡住了后一个就得排队。这既是缺点也是设计上的约束——它告诉你'进程内事件总线不适合做重量级、高耗时的分布式协调'。如果某个订阅者真的很重，要么把它拆出去用消息队列（MQ），要么把它挂在 `AsyncEventBus` 上，让重逻辑跑在独立线程里。另外，如果订阅者方法本身是线程安全的，可以用 `@AllowConcurrentEvents` 注解允许 EventBus 在多线程环境下并发调用它，但这要求你的订阅者实现必须处理好并发问题。"

**小白**："最后一个问题：事件对象本身在设计上有讲究吗？"

**大师**："事件类强烈建议设计成不可变对象（Immutable Event）。因为事件在总线中传递时，如果被某个订阅者偷偷改了字段，后续订阅者看到的就是'脏数据'，排查起来极其困难。用 `final` 字段、只提供 Getter、通过构造函数一次性传入所有属性，是最稳妥的做法。"

**技术映射**：事件对象作为发布者与订阅者之间的"契约"，应当不可变、无歧义、自描述；任何 mutable 事件都会在多订阅者场景下埋下时隐时现的 Bug。

## 3 项目实战

### 环境准备

本实战基于 Guava 32.1.3-jre 版本，JDK 8 及以上即可运行。若使用 Maven，在 `pom.xml` 中添加：

```xml
<dependency>
    <groupId>com.google.guava</groupId>
    <artifactId>guava</artifactId>
    <version>32.1.3-jre</version>
</dependency>
```

### 步骤 1：定义不可变事件类

订单状态变更是核心业务事件，所有下游系统都围绕它展开。我们将其设计为不可变对象。

**步骤目标**：建立发布者与订阅者之间的"通用语言"。

```java
import com.google.common.base.MoreObjects;

public final class OrderStatusChangedEvent {
    private final String orderId;
    private final String userId;
    private final String oldStatus;
    private final String newStatus;
    private final long timestamp;

    public OrderStatusChangedEvent(String orderId, String userId,
                                   String oldStatus, String newStatus) {
        this.orderId = orderId;
        this.userId = userId;
        this.oldStatus = oldStatus;
        this.newStatus = newStatus;
        this.timestamp = System.currentTimeMillis();
    }

    public String getOrderId() { return orderId; }
    public String getUserId() { return userId; }
    public String getOldStatus() { return oldStatus; }
    public String getNewStatus() { return newStatus; }
    public long getTimestamp() { return timestamp; }

    @Override
    public String toString() {
        return MoreObjects.toStringHelper(this)
                .add("orderId", orderId)
                .add("newStatus", newStatus)
                .toString();
    }
}
```

**坑点**：事件类没有实现 `toString()` 会导致日志输出对象哈希地址，排查问题时极其不便；缺少 `final` 修饰字段可能被订阅者反射篡改。

### 步骤 2：创建同步 EventBus 与核心订阅者

库存扣减和积分发放是支付成功后的核心旁路逻辑，必须立即执行且失败需要感知。

**步骤目标**：掌握 `@Subscribe` 注解的使用方式和注册机制。

```java
import com.google.common.eventbus.Subscribe;
import com.google.common.eventbus.EventBus;

public class StockListener {
    @Subscribe
    public void onOrderPaid(OrderStatusChangedEvent event) {
        if (!"PAID".equals(event.getNewStatus())) {
            return;
        }
        try {
            System.out.println("[库存] 扣减库存, orderId=" + event.getOrderId());
        } catch (Exception e) {
            // 生产环境应记录日志并告警，不要抛出
            System.err.println("[库存] 扣减失败: " + e.getMessage());
        }
    }
}

public class PointsListener {
    @Subscribe
    public void onOrderPaid(OrderStatusChangedEvent event) {
        if (!"PAID".equals(event.getNewStatus())) {
            return;
        }
        System.out.println("[积分] 发放积分, userId=" + event.getUserId());
    }
}
```

**坑点**：`@Subscribe` 方法必须是 `public`、返回 `void`、且只有一个参数；如果写成 `private` 或带返回值，Guava 会静默忽略，不抛任何异常，导致事件"消失"。

### 步骤 3：创建 AsyncEventBus 处理耗时任务

短信通知和物流下单涉及外部 HTTP 调用，适合异步处理。

**步骤目标**：理解同步与异步 EventBus 的混合使用策略。

```java
import com.google.common.eventbus.AsyncEventBus;
import java.util.concurrent.Executors;
import java.util.concurrent.ThreadFactory;
import java.util.concurrent.atomic.AtomicInteger;

public class AsyncListeners {
    private final AsyncEventBus asyncEventBus;

    public AsyncListeners() {
        ThreadFactory factory = new ThreadFactory() {
            private final AtomicInteger counter = new AtomicInteger(0);
            @Override
            public Thread newThread(Runnable r) {
                Thread t = new Thread(r, "eventbus-" + counter.incrementAndGet());
                t.setDaemon(true);
                return t;
            }
        };
        this.asyncEventBus = new AsyncEventBus(
                Executors.newFixedThreadPool(4, factory),
                (exception, context) ->
                    System.err.println("[异步异常] " + context.getEvent()
                        + ", subscriber=" + context.getSubscriberMethod()
                        + ", error=" + exception.getMessage())
        );
    }

    public AsyncEventBus getBus() { return asyncEventBus; }

    public static class SmsListener {
        @Subscribe
        public void onOrderPaid(OrderStatusChangedEvent event) {
            if (!"PAID".equals(event.getNewStatus())) return;
            System.out.println("[短信] 发送支付成功短信, userId=" + event.getUserId()
                    + ", thread=" + Thread.currentThread().getName());
        }
    }

    public static class LogisticsListener {
        @Subscribe
        public void onOrderPaid(OrderStatusChangedEvent event) {
            if (!"PAID".equals(event.getNewStatus())) return;
            System.out.println("[物流] 生成运单, orderId=" + event.getOrderId()
                    + ", thread=" + Thread.currentThread().getName());
        }
    }
}
```

**坑点**：`AsyncEventBus` 的构造器可以传入 `SubscriberExceptionHandler`，强烈建议自定义，否则订阅者抛出的异常会被吞掉；另外线程池必须显式指定拒绝策略，否则队列满时默认抛异常可能导致事件丢失。

### 步骤 4：异常兜底与 DeadEvent 处理

**步骤目标**：建立事件未消费的监控能力。

```java
import com.google.common.eventbus.DeadEvent;

public class DeadEventListener {
    @Subscribe
    public void handleDeadEvent(DeadEvent event) {
        System.err.println("[DeadEvent] 无人订阅的事件: " + event.getEvent()
                + ", 来源: " + event.getSource().getClass().getName());
    }
}
```

**坑点**：`DeadEvent` 只能捕获在当前总线实例上无匹配订阅者的事件；如果事件类型匹配错了（比如 post 了一个 `String` 但订阅者监听 `Integer`），依然会进入 DeadEvent。

### 步骤 5：测试验证

**步骤目标**：运行完整链路并观察输出。

```java
public class EventBusDemo {
    public static void main(String[] args) throws Exception {
        // 1. 同步总线：库存 + 积分
        EventBus syncBus = new EventBus("order-sync-bus");
        syncBus.register(new StockListener());
        syncBus.register(new PointsListener());
        syncBus.register(new DeadEventListener());

        // 2. 异步总线：短信 + 物流
        AsyncListeners asyncSetup = new AsyncListeners();
        AsyncEventBus asyncBus = asyncSetup.getBus();
        asyncBus.register(new AsyncListeners.SmsListener());
        asyncBus.register(new AsyncListeners.LogisticsListener());
        asyncBus.register(new DeadEventListener());

        // 3. 模拟订单支付成功
        OrderStatusChangedEvent event = new OrderStatusChangedEvent(
                "ORD-20241225-001", "U10086", "CREATED", "PAID");

        System.out.println("===== 同步事件总线 =====");
        syncBus.post(event);

        System.out.println("===== 异步事件总线 =====");
        asyncBus.post(event);

        // 等待异步任务执行完
        Thread.sleep(1000);

        // 4. 投递一个无人订阅的事件
        System.out.println("===== 测试 DeadEvent =====");
        syncBus.post("这是一个字符串事件，没有订阅者");

        Thread.sleep(500);
    }
}
```

**运行结果**：

```
===== 同步事件总线 =====
[库存] 扣减库存, orderId=ORD-20241225-001
[积分] 发放积分, userId=U10086
===== 异步事件总线 =====
===== 测试 DeadEvent =====
[短信] 发送支付成功短信, userId=U10086, thread=eventbus-1
[物流] 生成运单, orderId=ORD-20241225-001, thread=eventbus-2
[DeadEvent] 无人订阅的事件: 这是一个字符串事件，没有订阅者, 来源: com.google.common.eventbus.EventBus
```

注意异步总线的输出可能穿插在 DeadEvent 之后，这是正常的线程调度现象。

### 完整代码清单

为了方便读者一键运行，以下是合并后的完整可运行代码（请将四个类及 `EventBusDemo` 放在同一包下，或调整为默认包即可直接编译运行）。核心依赖仅为 `guava-32.1.3-jre.jar`。

完整代码已在上述各步骤中给出，实际工程中建议按以下结构组织：

- `event/` —— 存放 `OrderStatusChangedEvent`
- `listener/` —— 存放 `StockListener`、`PointsListener`、`SmsListener`、`LogisticsListener`、`DeadEventListener`
- `config/` —— 封装 `EventBus` 与 `AsyncEventBus` 的初始化和注册逻辑
- `service/` —— `OrderService` 负责业务逻辑，内部调用 `eventBus.post()`

### 测试验证

除了手工运行 `main` 方法观察日志外，还应编写单元测试覆盖以下场景：

1. **正常订阅**：post 事件后所有匹配的订阅者都被调用。
2. **类型不匹配**：post 子类事件时，父类事件的订阅者是否被触发（答案是会，EventBus 按事件类型的类层次匹配）。
3. **异常隔离**：某个订阅者抛异常后，验证后续订阅者是否仍被执行（同步模式下默认不隔离，需自行 try-catch）。
4. **未订阅兜底**：验证 `DeadEvent` 是否被正确触发。

## 4 项目总结

### 优缺点对比

| 维度 | Guava EventBus | Spring ApplicationEvent | 消息队列（MQ） |
|------|---------------|------------------------|---------------|
| 依赖成本 | 仅 Guava 包，零外部依赖 | 依赖 Spring 框架 | 需部署 Broker，运维成本高 |
| 通信范围 | 单 JVM 进程内 | 单 JVM 进程内（或 Spring Cloud Stream 扩展） | 跨进程、跨机房 |
| 持久化 | 不支持，进程重启即丢失 | 不支持 | 支持磁盘持久化 |
| 事务支持 | 无，与主线程同事务或异步即 fire-and-forget | 支持 `@TransactionalEventListener` | 支持分布式事务（如 RocketMQ） |
| 性能 | 极高，内存级方法反射调用 | 高 | 网络开销 + 序列化开销 |
| 异常/重试 | 无内置重试，异常可阻断后续订阅者 | 支持事务阶段监听和异常传播 | 完善的重试、死信队列 |
| 动态扩容 | 不支持跨机器 | 不支持跨机器 | 天然分布式，可水平扩展 |
| 调试难度 | 低，单进程内可直接断点 | 低 | 高，需追踪消息轨迹 |

### 适用场景

- 单体应用或单个微服务内部的模块解耦，如订单域内的状态变更广播。
- 需要极低延迟、无网络开销的本地事件分发。
- 作为引入 MQ 之前的过渡方案，验证事件驱动模型是否合理。
- 工具类库、插件系统中"通知机制"的实现，如配置中心刷新后的本地缓存失效广播。

### 不适用场景

- 需要跨服务、跨机器通信的分布式事件（应直接选用 MQ）。
- 对消息可靠性有强要求，不允许丢失（进程崩溃即丢失）。
- 需要严格的事务一致性，要求"事件投递与数据库操作要么都成功要么都回滚"。
- 需要消息回溯、延时投递、消息排序等高级语义。

### 注意事项

1. **事件类必须不可变**：所有字段用 `final` 修饰，只暴露 Getter，避免订阅者之间的隐性数据污染。
2. **订阅方法签名要合规**：`public void` + 单参数 + `@Subscribe`，缺一不可；Guava 对不合规的方法是静默忽略。
3. **异常必须内部消化**：每个 `@Subscribe` 方法都应包裹 try-catch，否则同步模式下会直接抛给 `post()` 的调用方，或阻断后续订阅者。
4. **内存泄漏风险**：动态创建的订阅者对象如果调用了 `register()` 但未调用 `unregister()`，会被 EventBus 强引用而无法 GC，长生命周期的总线尤其要注意。
5. **线程模型要显式选择**：同步还是异步应在设计阶段明确，不要在业务代码中混用导致线程安全问题。

### 3 个生产踩坑案例

**案例 1：内存泄漏拖垮服务**
某团队在用户会话中动态创建 `SessionEventListener` 并注册到全局 EventBus，但会话销毁时忘记 `unregister()`。随着用户量增长，老年代堆积了数百万个监听器对象，最终导致 Full GC 卡顿。解决方案：在对象生命周期结束时显式注销，或使用弱引用包装（需自行扩展）。

**案例 2：异常阻断导致积分未发放**
某次上线后，库存监听器的下游 RPC 接口超时抛异常。由于该 `@Subscribe` 方法没有 try-catch，同步 EventBus 将异常抛给了调用方，并阻断了后续积分发放监听器的执行。用户支付成功后库存扣了，积分却没到账。解决方案：所有订阅者内部必须自行捕获异常并转日志/告警。

**案例 3：异步线程池满导致事件"人间蒸发"**
某业务使用 `Executors.newFixedThreadPool(10)` 构造 `AsyncEventBus`，未指定拒绝策略。大促期间事件量暴增，线程池队列被打满，默认的 `ThreadPoolExecutor.AbortPolicy` 直接抛异常，且由于未自定义 `SubscriberExceptionHandler`，异常被吞掉，大量短信和物流通知没有发出。解决方案：自定义带缓冲的线程池 + 自定义 `SubscriberExceptionHandler` 做告警 + 监控线程池队列堆积。

### 2 道思考题

1. **如果业务要求"支付成功后的库存扣减、积分发放、短信通知必须严格按顺序执行"，你应该如何配置 EventBus？若允许并行但要求三者全部完成后才能返回给用户，又应如何设计？**

2. **EventBus 基于反射调用订阅者方法，这在高 QPS 场景下是否存在性能瓶颈？如果有，你会如何优化或替代？（提示：可以从缓存 Method 对象、使用 LambdaMetafactory、或迁移到编译时代码生成等角度思考）**

### 推广计划提示

EventBus 适合作为团队内部"事件驱动设计"的启蒙工具。建议在以下节点推广：

- **技术分享会**：以"从一锅粥到广播站"为主题，用 30 分钟演示订单场景的重构前后对比。
- **代码评审**：在评审中发现"一个方法里串行调用超过 3 个无关下游模块"时，建议引入 EventBus 进行解耦。
- **渐进式落地**：先在非核心链路（如数据上报、缓存失效）试点，积累异常处理和监控经验后，再推广到订单状态机等核心链路。
- **配套规范**：制定团队内部的《EventBus 使用规范》，明确事件类命名约定（以 `Event` 结尾）、订阅者命名约定（以 `Listener` 结尾）、以及强制 try-catch 的代码模板。
