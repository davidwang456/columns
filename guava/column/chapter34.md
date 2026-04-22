# 第 34 章：ListenableFuture 执行模型与线程池隔离策略

## 1 项目背景

在异步框架核心设计中，架构师小周需要优化 ListenableFuture 的执行效率。发现回调执行和任务执行混用同一线程池，导致相互影响，需要设计隔离策略。

## 2 项目设计

**大师**："ListenableFuture 执行模型：

```java
// 任务执行 Executor（用于 Future 计算）
ExecutorService computeExecutor = Executors.newFixedThreadPool(10);

// 回调执行 Executor（用于 listener/callback）
ExecutorService callbackExecutor = Executors.newSingleThreadExecutor();

// 分离防止：回调阻塞影响新任务提交

// 内置优化：同一个 Executor 内链式调用避免线程切换
Futures.transform(future, func, executor);  // 可能同线程执行
```

**技术映射**：线程池隔离就像是'高速收费站'——ETC 和人工通道分开，避免相互排队。"

## 3 项目实战

```java
public class AsyncFramework {
    // CPU 密集型任务池
    private final ExecutorService cpuPool = Executors.newFixedThreadPool(
        Runtime.getRuntime().availableProcessors(),
        new ThreadFactoryBuilder().setNameFormat("compute-%d").build()
    );
    
    // IO 密集型任务池
    private final ExecutorService ioPool = Executors.newCachedThreadPool(
        new ThreadFactoryBuilder().setNameFormat("io-%d").build()
    );
    
    // 回调执行池（串行防止竞争）
    private final Executor callbackPool = Executors.newSingleThreadExecutor(
        new ThreadFactoryBuilder().setNameFormat("callback-%d").build()
    );
    
    public <T, R> ListenableFuture<R> asyncCompute(ListenableFuture<T> input, 
                                                     Function<T, R> mapper) {
        // CPU 计算使用固定线程池
        return Futures.transform(input, mapper::apply, cpuPool);
    }
    
    public <T> ListenableFuture<T> asyncIO(Callable<T> ioTask) {
        // IO 使用可扩展线程池
        return Futures.submit(ioTask, ioPool);
    }
    
    public <T> void addCallback(ListenableFuture<T> future, 
                                 FutureCallback<T> callback) {
        // 回调使用独立线程池，防止阻塞计算
        Futures.addCallback(future, callback, callbackPool);
    }
}

// 线程池监控
public class ExecutorMonitor {
    public void monitor(ThreadPoolExecutor executor) {
        System.out.println("Active: " + executor.getActiveCount());
        System.out.println("Queue: " + executor.getQueue().size());
        System.out.println("Completed: " + executor.getCompletedTaskCount());
    }
}
```

## 4 项目总结

### 线程池隔离策略

| 池类型 | 用途 | 配置 |
|--------|------|------|
| 计算池 | CPU 密集型 | 固定大小 = CPU 核心 |
| IO 池 | IO 密集型 | 可扩展，较大 |
| 回调池 | 轻量回调 | 单线程或较小固定 |
| 调度池 | 定时任务 | 独立单线程 |

### 最佳实践

1. 避免在回调中做耗时操作
2. 链式操作优先同线程
3. 监控线程池饱和情况
