# 第21章：TaskGroupContainer 源码——并发调度与 TaskExecutor

## 1. 项目背景

运维团队发现一个神秘现象：一个 MySQL → MySQL 的 DataX 任务，配置了 `channel=10`，split 产出了 20 个 Task。理论上前 10 个 Task 跑完后，后 10 个应该立刻跟上。但实际监控显示——前 10 个 Task 在 5 分钟内全部完成，然后沉默了整整 2 分钟，后 10 个 Task 才开始执行。

排查日志发现——问题不是出在 Task 本身，而是出在 `TaskGroupContainer` 的 Task 分发机制上。第 1 个 TaskGroup（10 个 channel slot）跑完后，需要等待 `StandAloneScheduler` 的监控循环检测到它结束，再启动第 2 个 TaskGroup。这个监控循环的默认间隔是 5 秒——但网络抖动和线程竞争让实际的检测延迟达到了 2 分钟。

本章深入 `TaskGroupContainer` 和内部类 `TaskExecutor` 的源码，理解一个 Task 从配置对象变成两个线程（ReaderRunner + WriterRunner）的完整过程，并掌握 failover 重试机制的触发条件。

## 2. 项目设计——剧本式交锋对话

**（运维班，小周盯着监控面板上的空白期发呆）**

**小周**：这 2 分钟的空白期是怎么回事？CPU、IO、网络全闲着，DataX 在睡觉吗？

**大师**：它不是在睡觉，是在等——等 Scheduler 的下一轮监控循环。看源码，`StandAloneScheduler` 的主循环是这样的：

```java
while (!allTaskGroupsDone) {
    // 检查所有 TaskGroup 的状态
    for (TaskGroupContainer tg : taskGroups) {
        if (tg.isFinished()) {
            allDoneCount++;
        }
    }
    if (allDoneCount < taskGroups.size()) {
        Thread.sleep(5000);  // ← 5秒检查周期
    }
}
```

但为什么实际是 2 分钟而不是 5 秒？因为还有线程 join 的等待时间——主线程在等待 TaskGroup 的线程执行完毕。如果某个 TaskGroup 里有线程还没 join 回来，主监控循环就一直卡着。

**技术映射**：Scheduler 监控循环 = 校园巴士。它每 5 分钟（5 秒）来一趟站台，看有没有人要上车。"等车"的时间看起来是 5 分钟，但如果站台上有故障（线程卡住），你等 2 小时也上不了车。

**小胖**：那 TaskGroupContainer 是怎么并发执行 Task 的？是每来一个 Task 就 new 一个线程？

**大师**：不是，它用的是有界线程池。`channelNumber=N` 意味着 TaskGroupContainer 内部最多同时有 N 个 `TaskExecutor` 在运行。每个 TaskExecutor 包含一个 Channel + 一个 ReaderRunner 线程 + 一个 WriterRunner 线程。

**小白**：那如果 Task 失败了怎么办？会不会整个 Job 都挂？

**大师**：不是。Task 级别的失败不会立刻传染给 Job，而是先做**重试**。TaskGroupContainer 有一个 `taskMaxRetryTimes` 参数（从 Job 配置读取），默认通常是 3。如果 TaskExecutor 执行失败，它会检查当前重试次数是否小于上限：
- 小于上限 → 重新创建 TaskExecutor，从头开始跑这个 Task
- 等于上限 → 标记 Task 为 FAILED，TaskGroup 也标记 FAILED

当任意一个 TaskGroup 标记为 FAILED，Scheduler 会触发 Job 级别的失败。

## 3. 项目实战

### 3.1 步骤一：TaskGroupContainer 启动流程

```java
// TaskGroupContainer.java
public class TaskGroupContainer extends AbstractContainer {
    private int channelNumber;              // 并发槽位数
    private int taskMaxRetryTimes;          // Task 重试上限
    private List<Configuration> taskConfigs; // 待执行的 Task 配置列表
    private LinkedBlockingDeque<Configuration> pendingTasks; // 待执行队列
    
    public void start() {
        this.channelNumber = this.configuration.getInt("channel", 5);
        this.taskMaxRetryTimes = configuration.getInt("taskMaxRetryTimes", 3);
        
        // 将 Task 配置列表装入待执行队列
        this.pendingTasks = new LinkedBlockingDeque<>(this.taskConfigs);
        
        // 创建 channelNumber 个 slot，每个 slot 拉一个 Task 执行
        List<Future<?>> futures = new ArrayList<>();
        for (int i = 0; i < channelNumber; i++) {
            Future<?> future = executorService.submit(new Runnable() {
                @Override
                public void run() {
                    executeNextTask();
                }
            });
            futures.add(future);
        }
        
        // 等待所有 slot 执行完毕
        for (Future<?> future : futures) {
            try {
                future.get();
            } catch (ExecutionException e) {
                // 处理 Task 失败
                handleFailure(e);
            }
        }
    }
    
    private void executeNextTask() {
        while (true) {
            Configuration taskConfig = pendingTasks.poll(); // 取出下一个 Task
            if (taskConfig == null) break; // 队列为空，所有 Task 执行完毕
            
            int retryCount = 0;
            while (retryCount <= taskMaxRetryTimes) {
                TaskExecutor executor = new TaskExecutor(taskConfig);
                try {
                    executor.start(); // 启动 Reader线程 + Writer线程
                    break; // 执行成功，跳出重试循环
                } catch (Exception e) {
                    retryCount++;
                    if (retryCount > taskMaxRetryTimes) {
                        throw new RuntimeException("Task failed after " + retryCount + " retries");
                    }
                    LOG.warn("Task failed, retry {}/{}", retryCount, taskMaxRetryTimes);
                }
            }
        }
    }
}
```

### 3.2 步骤二：TaskExecutor 内部结构

```java
// TaskGroupContainer.java (内部类)
class TaskExecutor {
    private Configuration taskConfig;
    private Channel channel;
    
    public void start() {
        // 1. 创建 Channel（每个 Task 独立的 Channel）
        this.channel = new MemoryChannel(this.channelCapacity); // 默认 capacity=128
        
        // 2. 创建 Record 交换器
        RecordExchanger exchanger = new BufferedRecordExchanger(this.channel);
        
        // 3. 创建 Reader 和 Writer Task 实例
        Reader.Task readerTask = createReaderTask(taskConfig);
        Writer.Task writerTask = createWriterTask(taskConfig);
        
        // 4. 创建 Runner（包装 Task + Channel）
        ReaderRunner readerRunner = new ReaderRunner(readerTask, exchanger.getSender());
        WriterRunner writerRunner = new WriterRunner(writerTask, exchanger.getReceiver());
        
        // 5. 启动两个线程
        Thread readerThread = new Thread(readerRunner, "reader-" + taskId);
        Thread writerThread = new Thread(writerRunner, "writer-" + taskId);
        
        readerThread.start();
        writerThread.start();
        
        // 6. 等待两个线程结束
        readerThread.join();
        writerThread.join();
        
        // 7. 检查两个线程的退出状态
        if (readerRunner.getException() != null) {
            throw new RuntimeException("Reader failed", readerRunner.getException());
        }
        if (writerRunner.getException() != null) {
            throw new RuntimeException("Writer failed", writerRunner.getException());
        }
    }
}
```

**关键设计点**：
- 一个 TaskExecutor 内有且只有一个 Channel — 1:1 的 Reader-Writer 绑定
- Reader 和 Writer 是**两个独立的线程** — 通过 Channel 的阻塞队列隐式同步
- join() 的顺序是 readerThread 先于 writerThread — 确保 Reader 发完数据后 Writer 还能继续写

### 3.3 步骤三：ReaderRunner 和 WriterRunner

```java
// ReaderRunner.java
public class ReaderRunner extends AbstractRunner {
    private Reader.Task task;
    private RecordSender recordSender;
    
    @Override
    public void run() {
        try {
            task.init();         // 1. 初始化（读取本Task的querySql）
            task.prepare();      // 2. 准备（建立JDBC连接）
            task.startRead(recordSender); // 3. 读数据（★核心）
            task.post();         // 4. 后处理
        } catch (Throwable t) {
            this.exception = t;  // 记录异常（不直接抛出，留给TaskExecutor检查）
        } finally {
            try { task.destroy(); } catch (Throwable ignored) {}
        }
    }
}

// WriterRunner.java — 对称结构
public class WriterRunner extends AbstractRunner {
    private Writer.Task task;
    private RecordReceiver recordReceiver;
    
    @Override
    public void run() {
        try {
            task.init();
            task.prepare();
            task.startWrite(recordReceiver);  // ★核心
            task.post();
        } catch (Throwable t) {
            this.exception = t;
        } finally {
            try { task.destroy(); } catch (Throwable ignored) {}
        }
    }
}
```

### 3.4 步骤四：增强日志——追踪各阶段耗时

在 TaskExecutor 中添加耗时统计：

```java
public void start() {
    long t0 = System.currentTimeMillis();
    
    // ... 创建 Channel、Reader、Writer ...
    
    long t1 = System.currentTimeMillis();
    readerThread.start();
    writerThread.start();
    
    readerThread.join();
    long t2 = System.currentTimeMillis();
    writerThread.join();
    long t3 = System.currentTimeMillis();
    
    LOG.info("Task[{}] timing: init={}ms, read={}ms, write+cleanup={}ms, total={}ms",
        taskId, t1-t0, t2-t1, t3-t2, t3-t0);
}
```

**输出示例**：

```
Task[0] timing: init=120ms, read=45230ms, write+cleanup=8900ms, total=54250ms
Task[1] timing: init=115ms, read=44800ms, write+cleanup=9100ms, total=54015ms
Task[2] timing: init=130ms, read=125000ms, write+cleanup=21000ms, total=146130ms  ← 慢 Task!
Task[3] timing: init=118ms, read=45100ms, write+cleanup=8800ms, total=54018ms
```

Task[2] 的 read 阶段 125 秒 vs 其他Task 45 秒 — 数据倾斜证据。

### 3.5 步骤五：failover 重试验证

在测试环境中构造一个必然失败的 Task（向一个不存在的表写入）：

```java
// 验证 failover 行为
// 预期：Task 失败后最多重试 3 次，3 次后 TaskGroup 标记失败
```

**日志验证**：

```
[taskGroup-0] WARN  TaskGroupContainer - Task[5] failed: Table 'xxx' doesn't exist, retrying 1/3
[taskGroup-0] WARN  TaskGroupContainer - Task[5] failed: Table 'xxx' doesn't exist, retrying 2/3
[taskGroup-0] WARN  TaskGroupContainer - Task[5] failed: Table 'xxx' doesn't exist, retrying 3/3
[taskGroup-0] ERROR TaskGroupContainer - Task[5] failed after 3 retries, marking TaskGroup as FAILED
```

### 3.6 可能遇到的坑及解决方法

**坑1：writerThread.join() 永远不返回**

Writer 的 getFromReader() 在 Channel 为空时阻塞（BlockingQueue.take()）。如果 Reader 线程异常退出而忘了调用 `sender.terminate()`，Writer 就永远阻塞。

解决：在 TaskExecutor 中加超时等待：
```java
writerThread.join(600_000); // 最多等 10 分钟
if (writerThread.isAlive()) {
    writerThread.interrupt();
    LOG.error("Writer thread timed out, force interrupt");
}
```

**坑2：Task 重试时的资源泄漏**

重试时直接 new 新的 TaskExecutor，但旧的 TaskExecutor 的 JDBC Connection 可能没有正确关闭。

解决：failover 前确保 `destroy()` 被调用（即使前一次执行抛异常）。

## 4. 项目总结

### 4.1 TaskGroupContainer 关键参数

| 参数 | 来源 | 默认值 | 含义 |
|------|------|--------|------|
| channelNumber | JSON `speed.channel` | 1 | 同一 TaskGroup 最大并发 Task 数 |
| taskMaxRetryTimes | JSON 或代码硬编码 | 3 | Task 失败最大重试次数 |
| channelCapacity | MemoryChannel 配置 | 128 | Channel 队列容量 |
| TaskGroup 数量 | ceil(Task总数/channelNumber) | - | 分配到的 TaskGroup 数 |

### 4.2 优点

1. **并发隔离**：每个 TaskExecutor 独立的 Channel，不会相互阻塞
2. **异常隔离**：单个 Task 失败不影响同组其他 Task
3. **自动重试**：可配置的 failover 次数
4. **双线程协作**：Reader 和 Writer 异步并行，Channel 做缓冲区
5. **资源可控制**：channelNumber 硬限并发数

### 4.3 缺点

1. **无优先级队列**：pendingTasks 是 FIFO，重要 Task 不能插队
2. **failover 全量重跑**：Task 失败后从头跑，不能断点续传
3. **监控粒度粗**：5 秒监控间隔导致短任务浪费等待时间
4. **线程 join 无超时**：死循环/阻塞导致线程无法结束
5. **TaskGroup 之间无通信**：Group 间无法协调负载

### 4.4 注意事项

1. channelNumber 决定 TaskGroup 的"并发窗口"，不是"总共执行"的 Task 数
2. Task 失败重试仅限当前 TaskGroup，不会跨 TaskGroup 重分配
3. TaskExecutor 的 start() 方法是同步的——调用者线程会被 join() 阻塞
4. Reader 线程和 Writer 线程的 join 顺序是先 Reader 后 Writer
5. 确保每个 Task 的 destroy() 是幂等的

### 4.5 思考题

1. 如果 10 个channel 的 TaskGroupContainer 中有 1 个 Task 执行时间特别长，其他 9 个 slot 空着等它还是可以继续从 pendingTasks 取新 Task？
2. 现有 failover 机制在 Task 失败后完全重新执行（重新 split、重新读取）。如何实现"断点续传"——仅重新执行 Writer 部分而保留已读取的 Record？

（答案见附录）
