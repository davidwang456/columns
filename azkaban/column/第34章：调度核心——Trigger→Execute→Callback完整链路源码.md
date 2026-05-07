# 第34章：调度核心——Trigger→Execute→Callback完整链路源码

## 1. 项目背景

### 业务场景

运维团队在排查"凌晨2点调度偶尔不触发"的问题时，发现一个令人困惑的现象：Azkaban的日志显示Cron触发了，MySQL的`execution_flows`表中也有记录，但Flow没有被分配给Executor。状态一直停留在`PREPARING`。

深入排查后发现：Web Server的ExecutorManager在选择Executor时，过滤器`CpuStatus`因为Executor的心跳数据未及时更新，错误地认为所有Executor都已满载（CPU > 80%），导致Flow分配失败。

### 痛点放大

不掌握调度核心链路时：
- "调度触发了但没执行"这类问题无法定位根因
- 无法判断瓶颈在Quartz层、Executor选择层还是RPC调用层
- 不理解状态流转机制，看到`PREPARING`、`DISPATCHING`等中间状态不知其含义

## 2. 项目设计——剧本式交锋对话

**小胖**（对着PREPARING状态的Flow发呆）：大师，这个Flow在凌晨2点触发了，但现在都上午10点了还卡在PREPARING状态！Quartz日志显示触发成功了……

**大师**：这就是你需要理解的核心链路。Azkaban的Flow从触发到执行完毕，经历了5个状态：

```
PREPARING → DISPATCHING → RUNNING → SUCCEEDED/FAILED
    ↑          ↑            ↑           ↑
  Web收到    正在选择     Executor    最终结果
  触发请求    Executor    执行中
```

你的Flow卡在`PREPARING`，说明在"DISPATCHING"这一步就失败了——Web Server找不到一个可用的Executor来承接这个Flow。

**小白**：Web Server是怎么选择Executor的？

**大师**：Executor选择走的是"过滤器链 + 比较器"模式：

```java
// ExecutorManager中
List<Executor> candidates = getAllActiveExecutors();

// 过滤器链（排除不合格的）
for (ExecutorFilter filter : filters) {
    candidates = filter.filter(candidates);
}

// 比较器（选出最优的）
candidates.sort(comparator);
return candidates.get(0);
```

默认的过滤器是`StaticRemainingFlowSize`和`CpuStatus`，比较器是`NumberOfAssignedFlowComparator`。你的情况很可能是`CpuStatus`过滤器把健康的Executor误判为不可用了。

### 技术映射总结

- **DISPATCHING** = 调度室正在找空闲工人（还没找到，先等着）
- **过滤器链** = 面试流程（HR筛选→技术面→终面，每层过滤掉不合格的人）
- **心跳数据延迟** = 工人打卡机延迟（工人已经在干了，打卡记录还没更新）

## 3. 项目实战

### 3.1 核心链路源码

#### 步骤1：调度触发入口

```java
// TriggerManager.java —— 调度触发的第一站
public class TriggerManager {
    
    public void start() {
        // Quartz Scheduler触发时回调AzkabanQuartzJob.execute()
        quartzScheduler.start();
    }
}

// AzkabanQuartzJob.java —— Quartz回调
public class AzkabanQuartzJob implements Job {
    @Override
    public void execute(JobExecutionContext context) {
        // 1. 提取调度ID
        int scheduleId = Integer.parseInt(context.getTrigger().getJobKey().getName());
        
        // 2. 通过scheduleId获取Flow信息
        Flow flow = scheduleManager.getFlow(scheduleId);
        
        // 3. 调用ExecutorManager提交Flow
        executorManager.submitExecutableFlow(flow, context.getFireTime().getTime());
    }
}
```

#### 步骤2：Flow提交与分配

```java
// ExecutorManager.java —— Flow提交核心
public class ExecutorManager {
    
    public String submitExecutableFlow(ExecutableFlow flow, long submitTime) {
        // 1. 状态: PREPARING
        flow.setStatus(Status.PREPARING);
        flow.setSubmitTime(submitTime);
        executorLoader.uploadExecutableFlow(flow);
        
        // 2. 选择Executor
        Executor executor = selectExecutor(flow);
        
        if (executor == null) {
            // 没有可用Executor，保持PREPARING状态，等待重试
            logger.warn("No available executor for flow {}", flow.getExecutionId());
            return null;
        }
        
        // 3. 状态: DISPATCHING
        flow.setStatus(Status.DISPATCHING);
        executorLoader.updateExecutableFlow(flow);
        
        // 4. 通过RPC将Flow分发给选中的Executor
        dispatchFlow(executor, flow);
        
        return flow.getExecutionId();
    }
    
    private Executor selectExecutor(ExecutableFlow flow) {
        List<Executor> executors = getActiveExecutors();
        
        // 过滤器链
        for (ExecutorFilter filter : executorFilters) {
            executors = filter.filter(executors, flow);
            if (executors.isEmpty()) {
                return null;
            }
        }
        
        // 比较器排序
        executors.sort(executorComparator);
        return executors.get(0);
    }
}
```

#### 步骤3：RPC分发

```java
// ExecutorApiGateway.java —— RPC调用
public void callWithExecutableFlow(Executor executor, ExecutableFlow flow) {
    String host = executor.getHost();
    int port = executor.getPort();
    
    // HTTP POST到 Executor的 /executor 端点
    Map<String, String> params = new HashMap<>();
    params.put("action", "execute");
    params.put("execid", String.valueOf(flow.getExecutionId()));
    
    httpClient.httpPost("http://" + host + ":" + port + "/executor", params);
}
```

#### 步骤4：Executor接收并执行

```java
// FlowRunnerManager.java —— Executor侧接收
public void submitFlow(int execId) {
    ExecutableFlow flow = executorLoader.fetchExecutableFlow(execId);
    
    // 状态: RUNNING
    flow.setStatus(Status.RUNNING);
    executorLoader.updateExecutableFlow(flow);
    
    // 启动FlowRunner线程
    FlowRunner runner = new FlowRunner(flow, this.executorLoader);
    flowRunnerPool.submit(runner);
}
```

### 3.2 测试验证

```bash
# 调试模式下观察完整链路
# 1. 在ExecutorManager.submitExecutableFlow处设置断点
# 2. 手动执行一个Flow
# 3. 单步跟踪完整触发→执行链路
```

## 4. 项目总结

理解Trigger→Execute→Callback链路是掌握Azkaban调度内核的关键。核心状态流转：`PREPARING → DISPATCHING → RUNNING → SUCCEEDED/FAILED`，理解每个状态的触发条件和停留时间，就能快速定位90%的调度问题。

### 思考题

1. 如果要在ExecutorManager中添加一个新的过滤器"MemoryUsage"（根据Executor内存使用率过滤），需要修改哪些类？
2. Flow的callback机制是如何实现的？Executor执行完毕后，结果如何回传给Web Server？
