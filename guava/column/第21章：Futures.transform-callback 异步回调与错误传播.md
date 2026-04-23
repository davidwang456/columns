# 第 21 章：Futures.transform/callback 异步回调与错误传播

## 1 项目背景

每年的"双十一"大促，都是电商平台订单履约系统的极限压力测试。今年的"闪电购"活动中台团队遇到一件头疼的事：为了在零点抢购时支撑每秒十万级的下单请求，他们将原本同步串行的订单创建流程改造成了异步架构——查询商品详情、校验库存、计算促销价格、预占物流单号，四个步骤全部通过 `ListenableFuture` 下发到不同的线程池并行或链式执行。

改造上线后，吞吐量确实提升了三倍，但紧接着凌晨两点的事故复盘会上，团队发现对账系统显示少了近三千笔订单的库存扣减记录。排查日志时，工程师们傻眼了：库存服务的 RPC 调用在大促高峰时出现了大量 `SocketTimeoutException`，但这些异常既没有打印 ERROR 日志，也没有触发库存兜底策略，而是像泥牛入海般消失了。订单状态一直卡在"处理中"，直到超时才被强制关闭。

**痛点放大**：

- **异常被吞**：异步回调中没有正确注册错误处理逻辑，线程池的默认 `UncaughtExceptionHandler` 对异常视而不见，导致问题难以排查。工程师只能在茫茫日志中根据时间戳人肉 grep，效率极低。
- **回调嵌套**：为了把"查商品→查库存→算价格→建订单"串起来，代码写成了典型的"箭头形状"嵌套，四层回调缩进超过四十个字符，代码可读性和可维护性急剧下降，新人接手时直呼"看不懂"。
- **错误恢复困难**：库存服务偶发抖动时，系统缺乏优雅的降级机制。团队本想实现"主库查询失败则查备用总仓"的逻辑，但在层层嵌套的 Future 中，异常捕获和二次异步调用的组合让人无从下手。
- **取消传播失效**：用户在前端主动取消订单后，后台已经发出去的库存查询和物流预占请求仍在继续执行，白白消耗下游服务的配额和线程资源。
- **调试困难**：异步堆栈与业务逻辑割裂，当 `ExecutionException` 最终被抛到主线程时，原始的调用上下文已经丢失，定位根因如同大海捞针。

**技术映射**：Guava 的 `Futures` 工具类提供了系统化的解决方案——`FutureCallback` 让异步结果"有处可说"，`transform` 将嵌套结构展平为链式流水线，`catching`/`catchingAsync` 则为特定异常装上"安全气囊"，而整个 Future 链的取消传播机制能确保资源被及时释放。

---

## 2 项目设计

**小胖**："上周大促，库存服务一挂，订单系统就跟没事人一样继续跑，最后对账差了三千单！异步代码出错咋就跟哑巴吃黄连似的，堆栈都看不到？"

**小白**："等等，你用的是 `Future.get()` 阻塞等待，还是自己写 `addListener` 注册的回调？如果是后者，回调里有没有 try-catch？还有，异常到底是被 `ExecutionException` 包了一层，还是根本就没传到主线程？"

**大师**："小胖这个比喻很贴切。异步任务抛出的异常，会被 `ExecutionException` 包一层。如果你只调 `future.get()` 却不 catch，异常确实会往上抛；但更多时候，大家用 `addListener` 自己写回调，一旦回调里没做 try-catch，异常就被线程池的 `UncaughtExceptionHandler` 吞掉了——而很多线程池的默认 Handler 啥也不干。Guava 提供了 `FutureCallback`，把成功和失败拆成两个回调函数 `onSuccess` 和 `onFailure`，让错误'有处可说'，再也不会'失联'。"

**技术映射**：`FutureCallback` 的 `onFailure(Throwable)` 就是专门为异步错误开辟的'快速通道'，它把 `ExecutionException` 自动解包，将原始异常直接暴露给业务代码处理。

**小胖**："那我懂了，就是把成功和失败分两个门走。可我还是头疼——查商品、查库存、算价格、建订单，四个异步步骤我写了四层嵌套，代码像个俄罗斯套娃，这谁维护得了啊？"

**小白**："你这四层嵌套，每次都在新线程里切来切去，线程开销不大吗？而且如果第二步'查库存'抛了异常，第三、四步的回调还会不会执行？如果会执行，那岂不是用脏数据去创建订单了？"

**大师**："好问题。Guava 的 `Futures.transform` 就是来解决套娃问题的。你可以把它想象成工厂流水线：上一道工序的产出，自动成为下一道工序的原料，不需要工人（代码）在中间搬来搬去。至于异常，一旦某个环节失败，后续的 `transform` 不会执行，失败的 Future 会直接往下传，直到有人用 `catching` 或 `catchingAsync` 接住它。这就像流水线上某个工位出了次品，后续工位自动停机，等质检员来处理，而不是继续用次品组装。"

**技术映射**：`Futures.transform` 实现了函数式风格的 Future 组合（Future Composition），把嵌套结构展平为链式结构，本质上是 Monad 的 map 操作，让异步数据流像同步代码一样线性阅读。

**小胖**："停机等质检……那如果库存服务只是'偶尔咳嗽'，我不想让整个订单死掉，能不能有个备胎方案？比如库存查不到，就默认从总仓发货？"

**小白**："这就是降级策略了。但我想追问边界：你是想捕获所有异常，还是只捕获 `IOException` 这类网络异常？如果代码里出现了 `NullPointerException`，你也降级的话，会不会把真正的 Bug 藏起来了？还有，如果降级本身也需要再发一次异步请求——比如查备用仓库——用同步的 `catching` 能做到吗？"

**大师**："小白问到点子上了。`Futures.catching` 和 `Futures.catchingAsync` 允许你指定要捕获的异常类型，比如只捕获 `ServiceUnavailableException` 或 `IOException`。这就好比安全气囊——它只在特定力度的碰撞下弹出，不会因为你轻轻蹭了一下墙就炸开。`catching` 是同步恢复，直接返回一个静态默认值；`catchingAsync` 是异步恢复，你可以在里面再发起一次备用服务的调用，返回一个新的 `ListenableFuture`。Guava 早期版本还有个 `withFallback`，但在 19.0 之后被 `catchingAsync` 取代了，因为后者更灵活，能处理需要再次异步请求的场景。"

**技术映射**：`catching` 是'fail-fast + recover'的同步回退，适合返回本地缓存或默认值；`catchingAsync` 是'fail-over'的异步回退，支持二次远程调用，适合主备切换场景。

**小胖**："还有个事儿，用户点了取消订单，我能不能把已经发出去的那一堆查询请求也一起掐了？不然白浪费资源。"

**大师**："这就是取消传播。通过 `transform` 或 `catchingAsync` 返回的新 Future，如果你调用它的 `cancel(true)`，Guava 会自动尝试取消底层的原始 Future。就像你按了电梯的总停按钮，整个流水线的电源都会向源头回溯断开。不过要注意，`cancel(true)` 只能中断支持中断的线程；对于真正的网络请求，还需要配合 HTTP 客户端的取消机制才能彻底释放连接。"

**技术映射**：Guava Future 链的取消传播构成了一个'依赖图'，取消操作会沿着 Future 的引用链向源头回溯，避免'孤儿任务'继续消耗资源。

---

## 3 项目实战

### 环境准备

- **JDK**：8 或更高版本
- **构建工具**：Maven 3.6+
- **核心依赖**：Guava 33.x（JRE 版本）
- **测试框架**：JUnit 4.13.2

在 `pom.xml` 中添加：

```xml
<dependency>
    <groupId>com.google.guava</groupId>
    <artifactId>guava</artifactId>
    <version>33.2.1-jre</version>
</dependency>
<dependency>
    <groupId>junit</groupId>
    <artifactId>junit</artifactId>
    <version>4.13.2</version>
    <scope>test</scope>
</dependency>
```

### 步骤 1：创建 ListenableFuture 并注册回调（FutureCallback）

**步骤目标**：让异步结果"有处可说"，无论成功或失败都能被业务代码感知。

```java
import com.google.common.util.concurrent.*;
import java.util.concurrent.*;

public class Step1Callback {
    public static void main(String[] args) throws Exception {
        // 用 listeningDecorator 包装普通线程池，使其返回 ListenableFuture
        ListeningExecutorService executor = MoreExecutors.listeningDecorator(
            Executors.newFixedThreadPool(4)
        );

        // 提交异步任务
        ListenableFuture<String> future = executor.submit(() -> {
            Thread.sleep(100);
            return "商品详情：iPhone 15 Pro";
        });

        // 注册 FutureCallback，分离成功与失败处理
        Futures.addCallback(future, new FutureCallback<String>() {
            @Override
            public void onSuccess(String result) {
                System.out.println("[成功] " + result);
            }
            @Override
            public void onFailure(Throwable t) {
                System.err.println("[失败] " + t.getClass().getSimpleName() + ": " + t.getMessage());
            }
        }, executor);

        Thread.sleep(500); // 等待回调执行
        executor.shutdown();
    }
}
```

**运行结果说明**：正常执行时，控制台输出 `[成功] 商品详情：iPhone 15 Pro`；如果提交的任务抛异常（如将 `Thread.sleep` 换成抛 `RuntimeException`），则会输出 `[失败] ...`。

**可能遇到的坑**：
- **坑**：如果第三个参数传入 `MoreExecutors.directExecutor()`，回调会在完成该 Future 的线程里**同步执行**。如果 `onSuccess` 或 `onFailure` 里有耗时操作，会阻塞该线程，导致其他监听器延迟触发。
- **解**：生产环境中，除非回调逻辑极其轻量（如只修改一个原子变量），否则务必使用独立的业务线程池。

### 步骤 2：链式异步转换（Futures.transform）

**步骤目标**：把多个异步步骤串成流水线，消除"回调地狱"。

```java
public class Step2Transform {
    static class Product { String sku; Product(String s) { this.sku = s; } }
    static class Stock { int qty; Stock(int q) { this.qty = q; } }
    static class Price { double amount; Price(double a) { this.amount = a; } }

    public static void main(String[] args) throws Exception {
        ListeningExecutorService executor = MoreExecutors.listeningDecorator(
            Executors.newFixedThreadPool(4)
        );

        // 第 1 步：查询商品
        ListenableFuture<Product> productFuture = executor.submit(() -> new Product("SKU-1001"));

        // 第 2 步：基于商品查询库存（transform 自动将上一步结果传入）
        ListenableFuture<Stock> stockFuture = Futures.transform(
            productFuture,
            product -> {
                System.out.println("根据 " + product.sku + " 查询库存...");
                return new Stock(100);
            },
            executor
        );

        // 第 3 步：基于库存计算价格
        ListenableFuture<Price> priceFuture = Futures.transform(
            stockFuture,
            stock -> {
                System.out.println("根据库存 " + stock.qty + " 计算价格...");
                return new Price(stock.qty * 0.99);
            },
            executor
        );

        System.out.println("最终价格: " + priceFuture.get().amount);
        executor.shutdown();
    }
}
```

**运行结果说明**：三步自动串联，控制台依次打印查询信息，最终输出 `最终价格: 99.0`。代码扁平，没有任何嵌套。

**可能遇到的坑**：
- **坑**：`transform` 的 `Function` 里如果抛异常，异常会被捕获并包装进返回的 Future，**不会**抛到当前线程。如果下游忘记处理，可能直到调用 `.get()` 时才发现。
- **解**：在 `Function` 内部做好前置参数校验；在最终 `.get()` 处统一捕获 `ExecutionException` 并解包。

### 步骤 3：异常捕获与同步降级（Futures.catching）

**步骤目标**：给特定异常装上"安全气囊"，发生预期故障时返回默认值。

```java
import com.google.common.base.Function;
import java.io.IOException;

public class Step3Catching {
    static class Stock { int qty; Stock(int q) { this.qty = q; } }

    public static void main(String[] args) throws Exception {
        ListeningExecutorService executor = MoreExecutors.listeningDecorator(
            Executors.newFixedThreadPool(4)
        );

        // 模拟一个可能失败的异步任务
        ListenableFuture<Stock> riskyFuture = executor.submit(() -> {
            if (System.currentTimeMillis() % 2 == 0) {
                throw new IOException("库存服务超时 [模拟异常]");
            }
            return new Stock(100);
        });

        // 仅捕获 IOException，返回默认库存（保底库存）
        ListenableFuture<Stock> safeFuture = Futures.catching(
            riskyFuture,
            IOException.class,
            new Function<IOException, Stock>() {
                @Override
                public Stock apply(IOException ex) {
                    System.out.println("[降级] 库存服务异常，使用默认库存: " + ex.getMessage());
                    return new Stock(10); // 默认保底库存
                }
            },
            executor
        );

        Stock stock = safeFuture.get();
        System.out.println("最终库存数量: " + stock.qty);
        executor.shutdown();
    }
}
```

**运行结果说明**：如果 `riskyFuture` 抛出 `IOException`，控制台打印降级信息，最终库存为 `10`；如果未抛异常，则保持原始结果。如果是 `NullPointerException` 等其他异常，则继续向上传播，不会被吞掉。

**可能遇到的坑**：
- **坑**：有些开发者为了"保险"，用 `Throwable.class` 或 `Exception.class` 作为捕获类型。这会把 `NullPointerException`、`OutOfMemoryError` 也一并吞掉，隐藏真正的编程错误，导致线上 Bug 难以定位。
- **解**：始终精确捕获你**预期**的异常类型（如 `IOException`、`ServiceUnavailableException`），让未预期的异常快速失败。

