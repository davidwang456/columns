# 第 22 章：Service 框架管理后台任务生命周期

## 1 项目背景

### 1.1 业务场景

某中型电商平台"好货通"的后台架构中，运行着近二十个后台任务：每日凌晨的订单数据同步到数据仓库、每五分钟的缓存预热、每小时一次的日志归档、持续运行的消息队列消费器，以及异常订单的自动巡检程序。这些任务分布在不同的模块中，有的用 `ScheduledExecutorService` 实现，有的直接 `new Thread()` 启动，还有的在 Spring 的 `@Scheduled` 注解里各管各的。

随着业务增长，系统管理员老王越来越头疼。大促期间需要临时关闭部分非核心任务以释放资源，但光是梳理清楚哪些线程在跑、该怎么安全停止，就要花上大半天。更棘手的是，某个数据同步任务因为停止时未关闭数据库连接，导致连接池耗尽，进而拖垮了订单查询接口，最终引发了 P1 级故障。

### 1.2 痛点放大

没有统一生命周期管理框架时，后台任务系统会逐渐演变成"线程丛林"：

**启动失控**：某新手开发在代码里直接 `thread.start()`，没有状态标记，上游系统重启后重复启动同一个任务，造成数据重复同步。更隐蔽的是，任务启动顺序没有约束，日志清理跑在数据同步之前，把还没来得及同步的日志文件删掉了。

**停止灾难**：`Runtime.getRuntime().addShutdownHook` 里塞了七八个清理逻辑，有的抛异常导致后续钩子无法执行；某个缓存预热服务在 `finally` 块里释放资源，但线程被强制中断时根本没有走到 `finally`。最痛苦的是优雅关闭——运维发完停止命令后，不知道任务到底停没停，只能盯着日志猜。

**状态黑盒**：领导问"现在数据同步服务是什么状态"，开发只能回答"应该跑着吧"。没有统一的状态查询接口，健康检查无从谈起，监控系统里一堆自定义的 `isRunning` 标志位，有的用布尔值，有的用原子类，标准五花八门。

**异常孤岛**：某个定时任务抛了未捕获异常，线程默默退出，没有任何告警，直到下游系统发现数据断流才后知后觉。没有失败状态、没有自动重试机制，任务就像大海里的孤舟，沉了都没人知道。

这些问题的根源在于：后台任务不仅需要"跑起来"，更需要被"看得见、管得住、停得了"。Guava 的 `Service` 框架正是为解决这类问题而生——它用状态机模型把后台任务的生命周期从"野生放养"变成"正规军管理"。

## 2 项目设计

**小胖**：（嚼着薯片凑过来）"老王又在群里骂人了，说那个数据同步任务停了一个小时还在跑，最后只能杀进程！后台任务不就是 `new Thread().start()` 的事吗，咋就这么难管呢？"

**小白**：（推了推眼镜）"直接起线程当然简单，但你怎么知道它现在是什么状态？启动成功了吗？停止的时候资源释放完了吗？如果启动就抛异常，调用方怎么感知？"

**小胖**："额……看日志？"

**小白**："大促的时候几十上百个任务，你一个个看日志？而且直接停线程可能导致数据写到一半，就像你写了一半的文档直接拔电源。"

**大师**：（端起茶杯）"你们说的其实是同一个问题的两面。后台任务表面上看是'跑一段代码'，本质上却是一个有状态的服务——它要从 NEW 到 STARTING 再到 RUNNING，停止时从 RUNNING 到 STOPPING 再到 TERMINATED，中间还可能进入 FAILED。没有统一的状态机，就像没有红绿灯的十字路口，迟早要撞车。"

> **技术映射**：`Service` 状态机把后台任务从"无状态脚本"提升为"有状态服务"，让启动、运行、停止的每一步都可观测、可拦截、可回滚。

**小胖**："听起来挺玄乎的。那 Guava 的 `Service` 到底长啥样？是不是又要写一堆接口实现？"

**小白**："我看过源码，`Service` 是个接口，里面定义了 `startAsync`、`stopAsync`、`awaitRunning` 这些方法。但我想问的是——我们有定时任务、有常驻线程、有触发式执行，难道每种都要自己实现这个接口吗？"

**大师**："好问题。Guava 提供了三个'开箱即用'的抽象基类，正好对应你们系统的三类任务：

- **`AbstractScheduledService`**：给定时任务用的，比如你们每五分钟一次的缓存预热。你只需要实现 `runOneIteration()` 写业务逻辑，再返回一个 `Scheduler` 告诉它'多久跑一次'，框架自动帮你管理调度线程和生命周期。
- **`AbstractIdleService`**：给'闲时执行、忙时等待'的任务用。它内部维护了一个执行线程，你需要实现 `startUp()` 和 `run()` 以及 `shutDown()`，框架保证 `startUp` 成功后才进入 `run`，停止时先中断线程再执行 `shutDown`。
- **`AbstractExecutionThreadService`**：给需要独占线程的常驻服务用，比如消息队列消费器。它比 `AbstractIdleService` 更灵活，你可以完全控制线程的 `run()` 逻辑，框架只负责生命周期外壳。

打个比方：`AbstractScheduledService` 像是自动报时的闹钟，到点就响；`AbstractIdleService` 像是值守的保安，没事站着、有事处理、下班交接；`AbstractExecutionThreadService` 像是定制工位的流水线，框架搭好厂房，你来设计工序。"

> **技术映射**：三种抽象基类覆盖后台任务的三大原型——定时触发、闲时触发、常驻独占，让不同场景都能获得统一的生命周期外壳，而不必重复编写状态机代码。

**小胖**："哦！那我们系统里十几个任务，难道要逐个 `startAsync`、`stopAsync` 去点？"

**小白**："这就是我想追问的——单个 `Service` 有了状态机，多个 `Service` 之间的依赖和批量管理怎么办？比如数据同步必须先于日志清理启动，停止时反过来。还有，某个任务 FAILED 了，其他关联任务要不要联动停止？"

**大师**："Guava 提供了 `ServiceManager`，它就是服务编排的'总调度室'。你把所有 `Service` 丢进去，调用 `startAsync().awaitHealthy()` 就能批量启动并等待全部进入 RUNNING；停止时用 `stopAsync().awaitStopped()` 统一优雅关闭。它还能查询整体健康状态、监听状态变化事件，甚至告诉你哪些服务启动失败了。

关于状态机，记住这张图：

```
NEW → STARTING → RUNNING → STOPPING → TERMINATED
         |           |           |
         └───────────┴───────────┘
                     |
                  FAILED
```

每个状态转换都触发 `Listener`，你可以注册回调来打日志、发告警、联动其他服务。`FAILED` 状态尤其重要——它把'意外死亡'变成'可观测事件'，而不是无声无息的线程消失。"

> **技术映射**：`ServiceManager` 将单个服务的"原子状态机"扩展为服务集群的"编排状态机"，实现批量启动、健康聚合、失败传播和有序关闭。

## 3 项目实战

### 3.1 环境准备

本项目基于 Maven 构建，核心依赖如下：

```xml
<dependency>
    <groupId>com.google.guava</groupId>
    <artifactId>guava</artifactId>
    <version>32.1.3-jre</version>
</dependency>
```

确保 JDK 版本为 8 或以上。创建 Maven 项目后，在 `src/main/java/com/example/service/` 目录下编写示例代码。

### 3.2 分步实现

**步骤 1：实现定时数据同步服务（AbstractScheduledService）**

目标：每 10 秒执行一次模拟的数据同步，启动时初始化连接，停止时关闭连接。

```java
package com.example.service;

import com.google.common.util.concurrent.AbstractScheduledService;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;

public class DataSyncService extends AbstractScheduledService {
    private final AtomicInteger syncCount = new AtomicInteger(0);

    @Override
    protected void startUp() {
        System.out.println("[DataSync] 正在初始化数据库连接...");
    }

    @Override
    protected void runOneIteration() {
        int count = syncCount.incrementAndGet();
        System.out.println("[DataSync] 第 " + count + " 次数据同步完成");
    }

    @Override
    protected void shutDown() {
        System.out.println("[DataSync] 正在关闭数据库连接...");
    }

    @Override
    protected Scheduler scheduler() {
        return Scheduler.newFixedDelaySchedule(0, 10, TimeUnit.SECONDS);
    }
}
```

**运行结果预期**：

```
[DataSync] 正在初始化数据库连接...
[DataSync] 第 1 次数据同步完成
[DataSync] 第 2 次数据同步完成
[DataSync] 正在关闭数据库连接...
```

**坑点**：`startUp()` 和 `shutDown()` 的异常都会导致服务直接进入 `FAILED` 状态。如果在 `startUp()` 里抛异常，`runOneIteration` 永远不会执行。务必确保这两个方法的异常被妥善处理，或在 `ServiceManager` 层面监听 `FAILED` 事件做补偿。

**步骤 2：实现缓存预热服务（AbstractIdleService）**

目标：服务启动后在一个独立线程中持续预热缓存，收到停止信号后优雅退出。

```java
package com.example.service;

import com.google.common.util.concurrent.AbstractIdleService;
import java.util.concurrent.atomic.AtomicBoolean;

public class CacheWarmupService extends AbstractIdleService {
    private final AtomicBoolean running = new AtomicBoolean(true);

    @Override
    protected void startUp() {
        System.out.println("[CacheWarmup] 加载热点数据到本地缓存...");
    }

    @Override
    protected void run() {
        while (running.get()) {
            System.out.println("[CacheWarmup] 扫描数据库更新缓存...");
            try {
                Thread.sleep(5000);
            } catch (InterruptedException e) {
                System.out.println("[CacheWarmup] 收到中断信号，准备退出...");
                Thread.currentThread().interrupt();
                break;
            }
        }
    }

    @Override
    protected void shutDown() {
        System.out.println("[CacheWarmup] 通知运行线程停止...");
        running.set(false);
    }
}
```

**运行结果预期**：

```
[CacheWarmup] 加载热点数据到本地缓存...
[CacheWarmup] 扫描数据库更新缓存...
[CacheWarmup] 扫描数据库更新缓存...
[CacheWarmup] 收到中断信号，准备退出...
[CacheWarmup] 通知运行线程停止...
```

**坑点**：`AbstractIdleService` 在停止时会先中断执行线程，然后调用 `shutDown()`。如果你的 `run()` 方法不处理 `InterruptedException`，线程可能无法及时响应停止信号，导致 `awaitStopped()` 超时。上例中 `shutDown()` 里设置 `running = false` 是双重保险，但要注意 `shutDown` 和线程中断的时序关系——不要在这两个地方做互相依赖的清理逻辑。

**步骤 3：实现消息消费服务（AbstractExecutionThreadService）**

目标：模拟一个持续消费消息队列的服务，展示对线程和生命周期的完全控制。

```java
package com.example.service;

import com.google.common.util.concurrent.AbstractExecutionThreadService;
import java.util.concurrent.BlockingQueue;
import java.util.concurrent.LinkedBlockingQueue;
import java.util.concurrent.atomic.AtomicInteger;

public class MessageConsumerService extends AbstractExecutionThreadService {
    private final BlockingQueue<String> queue = new LinkedBlockingQueue<>();
    private final AtomicInteger consumeCount = new AtomicInteger(0);

    public void submitMessage(String msg) {
        queue.offer(msg);
    }

    @Override
    protected void startUp() {
        System.out.println("[MessageConsumer] 连接到消息队列 Broker...");
    }

    @Override
    protected void run() {
        while (isRunning()) {
            try {
                String msg = queue.take();
                int count = consumeCount.incrementAndGet();
                System.out.println("[MessageConsumer] 消费消息: " + msg + " (累计: " + count + ")");
            } catch (InterruptedException e) {
                System.out.println("[MessageConsumer] 消费线程被中断");
                Thread.currentThread().interrupt();
                break;
            }
        }
    }

    @Override
    protected void shutDown() {
        System.out.println("[MessageConsumer] 断开 Broker 连接，剩余消息: " + queue.size());
    }

    @Override
    protected String serviceName() {
        return "MessageConsumerService";
    }
}
```

**运行结果预期**：

```
[MessageConsumer] 连接到消息队列 Broker...
[MessageConsumer] 消费消息: ORDER-001 (累计: 1)
[MessageConsumer] 消费消息: ORDER-002 (累计: 2)
[MessageConsumer] 消费线程被中断
[MessageConsumer] 断开 Broker 连接，剩余消息: 0
```

**坑点**：`AbstractExecutionThreadService` 的 `run()` 方法里可以用 `isRunning()` 来判断是否应该继续循环，这比自定义标志位更可靠，因为框架会在调用 `stopAsync()` 后把内部状态置为停止。但要注意 `queue.take()` 这类阻塞操作会捕获中断异常，处理完后要立即 `break` 或检查 `isRunning()`，否则可能进入空转。

**步骤 4：使用 ServiceManager 统一管理**

目标：将上述三个服务纳入统一管控，实现批量启动、健康监控和优雅停止。

```java
package com.example.service;

import com.google.common.util.concurrent.Service;
import com.google.common.util.concurrent.ServiceManager;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.TimeoutException;

public class ServiceFrameworkDemo {
    public static void main(String[] args) throws TimeoutException {
        DataSyncService dataSync = new DataSyncService();
        CacheWarmupService cacheWarmup = new CacheWarmupService();
        MessageConsumerService messageConsumer = new MessageConsumerService();

        ServiceManager serviceManager = new ServiceManager(
            java.util.Arrays.asList(dataSync, cacheWarmup, messageConsumer)
        );

        // 监听所有服务的状态变化
        serviceManager.addListener(new ServiceManager.Listener() {
            @Override
            public void healthy() {
                System.out.println("[ServiceManager] 所有服务已健康运行！");
            }

            @Override
            public void stopped() {
                System.out.println("[ServiceManager] 所有服务已停止。");
            }

            @Override
            public void failure(Service service) {
                System.err.println("[ServiceManager] 服务失败: " + service);
            }
        });

        // 批量启动，最多等待 30 秒
        System.out.println("=== 启动所有服务 ===");
        serviceManager.startAsync().awaitHealthy(30, TimeUnit.SECONDS);

        // 模拟业务运行期间向消息队列投递消息
        messageConsumer.submitMessage("ORDER-001");
        messageConsumer.submitMessage("ORDER-002");

        // 让服务跑一会儿
        try {
            Thread.sleep(15000);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        }

        // 查询健康状态
        System.out.println("=== 健康检查 ===");
        System.out.println("是否全部健康: " + serviceManager.isHealthy());
        System.out.println("服务状态快照: " + serviceManager.servicesByState());

        // 批量优雅停止，最多等待 30 秒
        System.out.println("=== 停止所有服务 ===");
        serviceManager.stopAsync().awaitStopped(30, TimeUnit.SECONDS);

        System.out.println("=== 最终状态 ===");
        System.out.println("服务状态快照: " + serviceManager.servicesByState());
    }
}
```

**运行结果预期**：

```
=== 启动所有服务 ===
[DataSync] 正在初始化数据库连接...
[CacheWarmup] 加载热点数据到本地缓存...
[CacheWarmup] 扫描数据库更新缓存...
[MessageConsumer] 连接到消息队列 Broker...
[DataSync] 第 1 次数据同步完成
[MessageConsumer] 消费消息: ORDER-001 (累计: 1)
[MessageConsumer] 消费消息: ORDER-002 (累计: 2)
[ServiceManager] 所有服务已健康运行！
[CacheWarmup] 扫描数据库更新缓存...
[DataSync] 第 2 次数据同步完成
[CacheWarmup] 扫描数据库更新缓存...
[DataSync] 第 3 次数据同步完成
=== 健康检查 ===
是否全部健康: true
服务状态快照: {RUNNING=[MessageConsumerService, DataSyncService, CacheWarmupService]}
=== 停止所有服务 ===
[DataSync] 正在关闭数据库连接...
[CacheWarmup] 收到中断信号，准备退出...
[CacheWarmup] 通知运行线程停止...
[MessageConsumer] 消费线程被中断
[MessageConsumer] 断开 Broker 连接，剩余消息: 0
[ServiceManager] 所有服务已停止。
=== 最终状态 ===
服务状态快照: {TERMINATED=[MessageConsumerService, DataSyncService, CacheWarmupService]}
```

**坑点**：`awaitHealthy` 和 `awaitStopped` 都有超时版本，强烈建议在生产环境使用带超时的版本。如果某个服务的 `startUp()` 死锁或阻塞，`awaitHealthy()` 无参版本会永远等待，导致系统无法完成启动流程。此外，`ServiceManager` 的 `stopAsync()` 并不会强制终止线程，它只是触发状态转换并中断线程，真正的停止依赖于各个服务对中断信号的响应。

### 3.3 完整代码清单

上述四个类构成了完整的示例工程：

- `DataSyncService.java`：基于 `AbstractScheduledService` 的定时同步服务
- `CacheWarmupService.java`：基于 `AbstractIdleService` 的缓存预热服务
- `MessageConsumerService.java`：基于 `AbstractExecutionThreadService` 的消息消费服务
- `ServiceFrameworkDemo.java`：`ServiceManager` 统一编排入口

编译运行命令：

```bash
mvn compile exec:java -Dexec.mainClass="com.example.service.ServiceFrameworkDemo"
```

### 3.4 测试验证

可以通过 JUnit 测试来验证 `Service` 的状态转换是否符合预期：

```java
package com.example.service;

import com.google.common.util.concurrent.Service;
import org.junit.Test;
import static org.junit.Assert.*;

public class ServiceStateTest {

    @Test
    public void testScheduledServiceLifecycle() {
        DataSyncService service = new DataSyncService();
        assertEquals(Service.State.NEW, service.state());

        service.startAsync();
        service.awaitRunning();
        assertEquals(Service.State.RUNNING, service.state());

        service.stopAsync();
        service.awaitTerminated();
        assertEquals(Service.State.TERMINATED, service.state());
    }

    @Test
    public void testIdleServiceLifecycle() {
        CacheWarmupService service = new CacheWarmupService();
        service.startAsync().awaitRunning();
        assertTrue(service.isRunning());

        service.stopAsync().awaitTerminated();
        assertFalse(service.isRunning());
    }

    @Test
    public void testManagerHealthy() throws Exception {
        ServiceManager manager = new ServiceManager(
            java.util.Arrays.asList(new DataSyncService(), new CacheWarmupService())
        );
        manager.startAsync().awaitHealthy(5, java.util.concurrent.TimeUnit.SECONDS);
        assertTrue(manager.isHealthy());
        manager.stopAsync().awaitStopped(5, java.util.concurrent.TimeUnit.SECONDS);
        assertFalse(manager.isHealthy());
    }
}
```

## 4 项目总结

### 4.1 优缺点对比

| 维度 | 使用 Guava Service 框架 | 自行管理线程/线程池 |
|------|------------------------|-------------------|
| 状态可见性 | 统一状态机（NEW/RUNNING/STOPPING/TERMINATED/FAILED），可随时查询 | 需自行维护标志位，标准混乱 |
| 启动/停止规范 | 强制 `startUp` → `run` → `shutDown` 模板，资源初始化与释放成对出现 | 依赖开发者自觉，容易遗漏 |
| 批量管理 | `ServiceManager` 支持批量启动、停止、健康聚合 | 需自行实现遍历和异常处理 |
| 失败处理 | FAILED 状态可监听、可追踪，失败不静默 | 线程异常退出后无统一感知机制 |
| 学习成本 | 需要理解状态机和三种基类的适用场景 | 起线程零成本，但后续维护成本高 |
| 灵活性 | 对线程模型有一定约束（如 `AbstractIdleService` 单线程） | 完全自由，但自由也意味着失控 |

### 4.2 适用/不适用场景

**适用场景**：
1. **后台任务集群**：系统中有 3 个以上独立后台服务，需要统一启停和健康检查。
2. **资源敏感型服务**：服务的启动和停止伴随资源分配与释放（连接池、文件句柄、临时目录），需要成对管理。
3. **定时/周期任务**：需要固定频率或固定延迟执行的任务，且对执行时机有生命周期要求。

**不适用场景**：
1. **极简单任务**：整个系统只有一个后台线程，引入 `Service` 框架属于过度设计。
2. **高度定制线程模型**：需要自己维护复杂线程池、工作窃取队列，`Service` 的抽象反而成为约束。
3. **短暂异步操作**：只是一次性的异步回调（如发送通知邮件），不需要长期运行的服务语义。

### 4.3 注意事项

1. **不要混用 `stopAsync` 和强制中断**：`stopAsync()` 是优雅停止的信号，如果服务内部不响应中断，`awaitStopped` 会超时。此时不要直接调用底层线程的 `stop()`（已废弃），而应该检查 `shutDown()` 是否有阻塞操作。
2. **`startUp` 异常会直接 FAILED**：如果资源初始化失败，服务不会进入 RUNNING，而是 FAILED。调用方务必处理这种情况，或在 `ServiceManager.Listener` 里做失败告警。
3. **`ServiceManager` 不会自动重试**：某个服务进入 FAILED 后不会自动重启，需要业务层监听后手动 `startAsync()` 或做熔断降级。

### 4.4 生产踩坑案例

**案例 1：启动死锁导致系统无法就绪**

某团队在 `startUp()` 里调用了一个外部 HTTP 接口加载配置，且使用了同步阻塞调用。大促期间该接口超时，`startUp()` 阻塞，`awaitHealthy()` 永远等不到全部服务 RUNNING，导致整个应用启动流程卡死。解决：将外部依赖的加载移到构造函数或懒加载，`startUp()` 只做轻量级初始化；同时 `awaitHealthy` 必须带超时，超时后触发告警而非无限等待。

**案例 2：`shutDown` 里的阻塞 I/O 拖垮关闭流程**

一个日志清理服务在 `shutDown()` 里同步 flush 数据到远程 NFS，某次网络抖动导致 flush 阻塞了 10 分钟。运维发完停止命令后以为服务已停，直接做了发布替换，结果新旧进程同时写文件导致数据损坏。解决：`shutDown()` 里只做内存状态清理和信号发送，重 I/O 操作应异步化或设置严格超时。

**案例 3：忽略 `FAILED` 状态导致"僵尸服务"**

某消息消费服务在处理消息时抛了未捕获异常，`AbstractExecutionThreadService` 的 `run()` 方法退出，服务进入 FAILED。但开发者没有注册 `ServiceManager.Listener` 的 `failure` 回调，监控系统只查了 `isHealthy()`，而 `FAILED` 在 `ServiceManager` 层面会被视为不健康。更隐蔽的是，如果不用 `ServiceManager`，单个 `Service` FAILED 后调用方如果继续往里面提交任务，会造成数据丢失。解决：必须注册 `failure` 监听，FAILED 后要么重启服务，要么将流量切换到备用通道。

### 4.5 思考题

1. **设计题**：如果系统中有一个任务必须在另外两个任务成功启动后才能启动（服务依赖），`ServiceManager` 原生并不支持依赖编排，你会如何基于 Guava Service 实现启动顺序控制？请画出时序图并给出关键代码片段。

2. **排错题**：某 `AbstractScheduledService` 在运行数天后无故停止，`ServiceManager` 显示其为 FAILED，但日志中没有异常堆栈。请列出至少三种可能的原因，并说明如何通过增强监控来定位根因。

### 4.6 推广计划提示

将 Guava Service 框架引入现有系统时，建议分三步走：

1. **试点改造**：选择一个边界清晰、影响可控的后台任务（如日志归档）进行改造，对比改造前后的启动/停止耗时和问题发生率。
2. **规范沉淀**：制定《后台服务生命周期开发规范》，明确三种基类的选型决策树（定时选 `AbstractScheduledService`、常驻选 `AbstractExecutionThreadService`、简单轮询选 `AbstractIdleService`），并封装公司内部的基础抽象层。
3. **平台化**：基于 `ServiceManager` 开发内部服务治理面板，对接公司现有的监控和告警系统，实现"一键启停、状态可视、失败自动通知"的后台任务 PaaS 层。
