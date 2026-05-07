# 第18章：Executor Server执行引擎源码分析

## 1. 项目背景

### 业务场景

运维团队发现一个奇怪的现象：Executor Server的内存每隔几天就会缓慢增长，直到超过`-Xmx`上限触发OOM。重启后一切正常，但过几天又重复。

查看JVM内存快照发现，大量`java.lang.Thread`对象没有被回收。进一步分析发现，每当一个Spark Job提交到Yarn后，Azkaban的JobRunner线程就应该退出了——但实际上它一直停留在WAITING状态，等待Yarn回调。随着越来越多的Spark Job提交，WAITING线程累积增加，最终把内存撑爆。

### 痛点放大

不理解Executor引擎内部机制时：

1. **资源泄漏难定位**：不知道线程是怎么创建和回收的，只能靠重启治标
2. **性能调优无方向**：不知道`executor.max.threads`、`flow.threads`等参数的确切含义
3. **定制需求无法实现**：想给Executor加点监控埋点，但不知道从哪里入手

## 2. 项目设计——剧本式交锋对话

**小胖**（盯着JVM监控图）：大师，Executor内存又涨到90%了！看这曲线，每天涨一点，像计时炸弹一样，7天准时爆炸。

**大师**：这是典型的"线程泄漏"。我给你讲一下Executor的内部线程模型，你就知道问题出在哪了。

**小白**：线程模型？Executor不就是用线程池执行Job吗？还能多复杂？

**大师**（画图）：Executor内部有4个关键的线程/线程池角色：

```
Executor线程模型：
┌─────────────────────────────────────────────────────┐
│ Executor主线程                                       │
│   ├── HeartBeatThread       心跳上报（定期向Web报活） │
│   ├── FlowRunnerThreadPool   执行Flow的线程池          │
│   │   ├── FlowRunner-1                               │
│   │   └── FlowRunner-2                               │
│   ├── JobRunnerThreadPool    执行单个Job的线程池       │
│   │   ├── JobRunner-1  → command.sh                  │
│   │   ├── JobRunner-2  → spark-submit                │
│   │   └── JobRunner-3  → hadoop jar                  │
│   └── CleanerThread          清理过期Job的线程         │
└─────────────────────────────────────────────────────┘
```

**小胖**：所以我的问题是——JobRunner-2执行了`spark-submit`后，spark-submit命令本身就退出了（返回0），但JobRunner线程没有回收！它还在等什么？

**大师**：问题出在`spark-submit`的`--deploy-mode client`模式。这个模式下，spark-submit命令会一直阻塞等待Spark作业完成。但如果Yarn集群不稳定，连接断开了但spark-submit进程还在等——JobRunner就会一直WAITING。

更糟的是，Azkaban的JobRunner没有"最大执行时间"的强制中断机制（默认情况下），所以这个线程永远等下去。

**小白**：那`executor.max.threads`和`flow.threads`到底控制什么？

**大师**：
- `executor.flow.threads`（默认30）：控制同时执行多少个**Flow**。一个Flow可能包含50个Job，但它的FlowRunner只占用一个线程来编排。
- `executor.max.threads`（默认50）：控制同时执行多少个**JobRunner线程**。

如果两个都满了，新的请求会在队列中等待或拒绝。

**小胖**：那Job是怎么被创建和销毁的？我想知道完整生命周期。

**大师**（在白板上详细画出）：

```
Job生命周期：
┌──────────┐
│  READY   │ ← Flow开始时，依赖已满足的Job进入READY
└────┬─────┘
     │ FlowRunner分配线程
     ▼
┌───────────┐
│  RUNNING  │ ← JobRunner执行Job的实际逻辑
└─────┬─────┘
      │ 脚本执行完成
      ├── exit 0 → ┌───────────┐
      │            │ SUCCEEDED │
      │            └───────────┘
      │
      ├── exit ≠0 → ┌────────┐
      │             │ FAILED │ → 检查retries配置 → 重新READY或永久FAILED
      │             └────────┘
      │
      └── kill信号 → ┌────────┐
                     │ KILLED │
                     └────────┘
```

**小白**：那从源码角度看，Executor收到Web的`executeFlow`调用后，具体做了什么？

**大师**：简化后的核心链路：

```
1. ExecutorServlet 收到HTTP请求
2. ExecutorManager.activateFlows(flowId) 
3. FlowRunnerManager.submitFlow(flow) 
4. FlowRunner.run() —— 在线程池中启动
5. FlowRunner.executionFlow() —— 解析DAG，按拓扑序提交Job
6. 对每个READY Job → JobRunnerManager.submitJob(job)
7. JobRunner.run() —— 在线程池中启动
8. JobRunner.runJob() —— 调用具体JobType的run方法
9. [command类型] → ProcessJob.run() → Runtime.exec() → Process.waitFor()
```

理解了这条链路，你就知道线程是在第4步和第7步创建的，在第8步的`waitFor()`被阻塞。

### 技术映射总结

- **FlowRunner** = 项目经理（拿到项目计划书，安排谁先做、谁后做）
- **JobRunner** = 一线工人（具体执行一个任务，完成后汇报结果）
- **线程泄漏** = 工人在车间门口等人传话，但那人已下班了（线程WAITING等一个永远不会发生的回调）
- **Process.waitFor()** = 工人站在流水线旁等最后一个零件出来才离开

## 3. 项目实战

### 3.1 环境准备

- Azkaban源码已下载（第2章编译环境）
- IDE（IntelliJ IDEA）已配置

### 3.2 分步实现

#### 步骤1：探索源码目录结构

**目标**：了解Executor相关源码的位置。

```
azkaban-exec-server/src/main/java/azkaban/execapp/
├── AzkabanExecutorServer.java   # Executor启动入口
├── FlowRunnerManager.java       # FlowRunner管理器
├── FlowRunner.java              # Flow执行器
├── JobRunner.java               # Job执行器
├── event/                       # 事件通知
├── job/                         # Job执行相关
├── metric/                      # 监控指标
└── trigger/                     # 触发器
```

#### 步骤2：FlowRunner源码走读

**目标**：理解Flow执行的核心逻辑。

```java
// FlowRunner.java 核心流程简化版
public class FlowRunner extends EventHandler implements Runnable {
    
    @Override
    public void run() {
        try {
            // 1. 初始化：解析DAG依赖
            prepare();
            
            // 2. 主循环：持续提交READY状态的Job
            while (!flow.isFinished()) {
                // 获取所有依赖已满足的Job
                Set<String> readyJobs = flow.getReadyJobs();
                
                for (String jobId : readyJobs) {
                    // 提交Job到JobRunner线程池
                    JobRunner jobRunner = createJobRunner(flow.getJob(jobId));
                    jobRunnerManager.submitJobRunner(jobRunner);
                }
                
                // 3. 等待Job执行结果
                waitForNextJob();
            }
            
            // 4. 完成：更新Flow最终状态
            finalizeFlow();
        } catch (Exception e) {
            // 处理异常
            handleException(e);
        }
    }
}
```

**关键数据结构**：

```java
// Flow.java —— DAG状态管理
public class Flow {
    private Map<String, Node> nodes;   // 所有节点
    private String status;              // Flow当前状态
    
    // 获取所有依赖已满足的Job
    public Set<String> getReadyJobs() {
        return nodes.values().stream()
            .filter(node -> node.getStatus() == Status.READY)
            .map(Node::getId)
            .collect(Collectors.toSet());
    }
}
```

#### 步骤3：JobRunner源码走读

**目标**：理解单个Job的执行机制。

```java
// JobRunner.java 核心流程简化版
public class JobRunner extends EventHandler implements Runnable {
    
    @Override
    public void run() {
        try {
            // 1. 状态：READY → RUNNING
            node.setStatus(Status.RUNNING);
            
            // 2. 创建Job类型实例（通过JobTypeManager）
            Job job = jobTypeManager.buildJobExecutor(
                node.getJobType(),  // "command" / "spark" / "hadoopJava"
                props
            );
            
            // 3. 执行Job（这里会阻塞直到Job完成）
            job.run();  // ProcessJob.run() → Runtime.exec() → Process.waitFor()
            
            // 4. 根据退出码判断状态
            if (job.getStatus() == Status.SUCCEEDED) {
                node.setStatus(Status.SUCCEEDED);
            } else {
                // 检查重试
                if (retryCount < maxRetries) {
                    node.setStatus(Status.READY);  // 重新排队
                    retryCount++;
                } else {
                    node.setStatus(Status.FAILED);
                }
            }
        } catch (Exception e) {
            node.setStatus(Status.FAILED);
        }
    }
}
```

#### 步骤4：Job超时控制增强（自定义）

**目标**：给JobRunner添加超时强制中断机制。

```java
// 在JobRunner.java中增加超时控制
public class JobRunner extends EventHandler implements Runnable {
    
    private static final long DEFAULT_JOB_TIMEOUT_MS = 3600_000;  // 1小时
    
    @Override
    public void run() {
        ExecutorService executor = Executors.newSingleThreadExecutor();
        Future<?> future = executor.submit(() -> {
            try {
                job.run();  // 实际的Job执行逻辑
            } catch (Exception e) {
                handleException(e);
            }
        });
        
        try {
            // 从配置中获取超时时间
            long timeout = props.getLong("job.timeout.ms", DEFAULT_JOB_TIMEOUT_MS);
            future.get(timeout, TimeUnit.MILLISECONDS);
        } catch (TimeoutException e) {
            logger.warn("Job {} exceeded timeout limit {}ms, force killing", 
                        node.getId(), timeout);
            future.cancel(true);
            job.cancel();  // 强制终止子进程
            node.setStatus(Status.KILLED);
        }
    }
}
```

#### 步骤5：线程泄漏修复

**目标**：针对Spark client模式的线程泄漏问题，实现主动中断。

```java
// 修改ProcessJob中的waitFor逻辑
public class ProcessJob extends AbstractProcessJob {
    
    @Override
    public void run() throws Exception {
        Process process = buildProcess();
        
        // 检查是否为Spark Client模式，如果是则监控Application状态
        if (isSparkClientMode()) {
            runWithSparkMonitoring(process);
        } else {
            // 标准模式：设置总体超时
            if (!process.waitFor(jobTimeout, TimeUnit.MILLISECONDS)) {
                process.destroyForcibly();
                throw new JobTimeoutException("Job timed out");
            }
        }
        
        // 检查退出码
        if (process.exitValue() != 0) {
            throw new JobFailedException("Exit code: " + process.exitValue());
        }
    }
    
    private void runWithSparkMonitoring(Process process) {
        String appId = extractApplicationId(process);
        // 使用YarnClient轮询监控Application状态
        while (process.isAlive()) {
            Thread.sleep(30_000);  // 30秒轮询
            ApplicationReport report = yarnClient.getApplicationReport(appId);
            if (report.getFinalState() != null) {
                // Yarn Application已完成，强制终止spark-submit进程
                process.destroy();
                break;
            }
        }
    }
}
```

### 3.3 测试验证

```java
// 单元测试验证JobRunner的超时机制
@Test
public void testJobTimeout() {
    // 创建一个模拟的慢Job
    Props jobProps = new Props();
    jobProps.put("type", "command");
    jobProps.put("command", "sleep 120");  // 执行120秒
    jobProps.put("job.timeout.ms", "10000");  // 但只允许10秒
    
    JobRunner runner = new JobRunner(jobProps, node);
    runner.run();
    
    assertEquals(Status.KILLED, node.getStatus());
    // 验证总执行时间不超过15秒
}
```

## 4. 项目总结

### Executor引擎关键参数

| 参数 | 默认值 | 含义 | 调优建议 |
|------|--------|------|---------|
| executor.flow.threads | 30 | 同时执行的Flow数 | 根据日均Flow数*并行度调整 |
| executor.max.threads | 50 | 同时执行的JobRunner数 | ≤CPU核心数*4 |
| flow.max.running | 30 | 单个Executor最多运行的Flow | 用于多Executor负载控制 |
| job.max.running | - | 单个Flow最多并行的Job数 | 防止DAG扇出过猛 |

### 适用场景

- **适用**：需要理解引擎行为做调优、排查线程/内存泄漏、定制Job执行逻辑
- **不适用**：简单的调度使用（不需要深入源码）、无Java基础的运维

### 注意事项

- 修改Executor源码后需重新编译整个Executor包
- 线程泄漏修复后要经过充分的压力测试验证
- `Process.waitFor()`是阻塞调用，确保有超时保护

### 常见踩坑经验

1. **Process.waitFor()死锁**：子进程输出量很大时，stdout管道满导致子进程阻塞，JobRunner又等子进程——经典死锁。解决：使用ProcessBuilder并redirect输出。
2. **Gradle编译缓存**：修改源码后编译不生效，是Gradle缓存导致。用`./gradlew clean build`清除缓存。
3. **类加载冲突**：自定义JobType与Azkaban自带的类冲突。使用独立的`plugins/jobtypes`目录。

### 思考题

1. 如何对Executor的JobRunner池实现"优先级队列"——让高优Flow的Job优先获取线程资源？
2. Executor宕机重启后，之前分配给它的Flow还在RUNNING状态。如何设计一个"Flow恢复"机制，让新的Executor接管这些遗留任务？
