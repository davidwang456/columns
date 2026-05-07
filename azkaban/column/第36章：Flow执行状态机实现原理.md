# 第36章：Flow执行状态机实现原理

## 1. 项目背景

### 业务场景

数据团队在排查一次"Flow永远不结束"的故障时发现：Flow中有两个Job的状态是KILLED，第三个Job的状态是RUNNING，而整个Flow的状态是RUNNING——已经持续了12小时。

正常的逻辑应该是：当所有Job都达到终态（SUCCEEDED/FAILED/KILLED），Flow应该进入终态。但这个Flow似乎永远读不到那两个KILLED Job的完成信号。追踪源码后发现，Azkaban的状态机实现中存在一个竞态条件：JobRunner在更新Job状态为KILLED的同时，FlowRunner刚好在读取所有Job的状态——读取到了一个"过渡状态"，导致不一致。

### 痛点放大

不理解状态机时：
- 无法诊断"Flow不结束""状态僵死"等灵异问题
- 不知道状态转换的原子性保证
- 在并发场景下修改状态机代码容易引入更难排查的Bug

## 2. 项目设计——剧本式交锋对话

**小胖**：大师，这个Flow跑了12小时了，状态还是RUNNING！但里面所有Job都已经是SUCCEEDED或KILLED了。Flow不读完吗？

**大师**：这就是状态机问题。Azkaban的Flow状态不简单等于"所有Job状态的汇总"——它有自己的状态机，依赖Job状态的变更事件来驱动转换。

**小白**：状态机具体怎么工作？

**大师**（画图）：

```
Flow状态机：
                           ┌──────────────────┐
                           │      READY        │ ← Flow被提交，等待被分配
                           └────────┬─────────┘
                                    │ Executor分配成功
                                    ▼
                    ┌───────────────┴──────────────┐
                    │         RUNNING               │
                    │  (所有Job按DAG拓扑序执行)       │
                    └───┬─────────┬───────────┬─────┘
                        │         │           │
              所有Job成功│    部分Job失败 │ 用户手动取消
                        ▼         ▼           ▼
                   ┌────────┐ ┌────────┐ ┌────────┐
                   │SUCCEEDED│ │ FAILED │ │ KILLED │
                   └────────┘ └────────┘ └────────┘
```

状态转换由FlowRunner的**事件处理循环**驱动——JobRunner在Job状态变更时发出事件，FlowRunner收到事件后重新评估Flow的整体状态。

**小胖**：那竞态条件是怎么产生的？

**大师**：问题在`Flow.pauseFlow()`和`Flow.resumeFlow()`的实现上。JobRunner更新Job状态时，可能同时有多个线程在操作——一个JobRunner在写，FlowRunner在读，竞态就产生了。

### 技术映射总结

- **状态机** = 交通信号灯（每种状态有明确的转换规则）
- **事件驱动** = 消防警报按钮（按了之后自动触发一连串响应）
- **竞态条件** = 两个人同时写一个共享文档（谁先谁后决定最终结果）

## 3. 项目实战

### 3.1 核心源码

#### 步骤1：Flow状态机

```java
// Flow.java —— Flow状态机核心
public class Flow {
    private volatile Status status = Status.READY;
    private final Map<String, Node> nodes;
    
    // 状态转换方法（线程安全）
    public synchronized boolean setStatus(Status newStatus) {
        Status oldStatus = this.status;
        
        // 检查状态转换是否合法
        if (!isValidTransition(oldStatus, newStatus)) {
            logger.warn("Invalid transition: {} → {}", oldStatus, newStatus);
            return false;
        }
        
        this.status = newStatus;
        logger.info("Flow status: {} → {}", oldStatus, newStatus);
        return true;
    }
    
    private boolean isValidTransition(Status from, Status to) {
        switch (from) {
            case READY:
                return to == Status.RUNNING || to == Status.KILLED;
            case RUNNING:
                return to == Status.SUCCEEDED || to == Status.FAILED 
                    || to == Status.KILLED || to == Status.PAUSED;
            case PAUSED:
                return to == Status.RUNNING || to == Status.KILLED;
            default:
                return false;  // 终态不可再转换
        }
    }
    
    // 检查Flow是否完成
    public boolean isFlowFinished() {
        synchronized (this) {
            if (status == Status.SUCCEEDED || 
                status == Status.FAILED || 
                status == Status.KILLED) {
                return true;
            }
            
            // 额外检查：所有节点是否都已经到终态
            for (Node node : nodes.values()) {
                if (!node.getStatus().isFinished()) {
                    return false;
                }
            }
            return true;
        }
    }
}
```

#### 步骤2：FlowRunner事件循环

```java
// FlowRunner.java —— FlowRunner主事件循环
public class FlowRunner extends EventHandler implements Runnable {
    
    @Override
    public void run() {
        try {
            flow.setStatus(Status.RUNNING);
            
            // 主循环：持续处理Job状态变更事件
            while (!flow.isFlowFinished()) {
                // 1. 获取就绪的Job
                Set<String> readyJobs = flow.getReadyJobs();
                
                // 2. 提交就绪的Job
                for (String jobId : readyJobs) {
                    try {
                        submitJob(jobId);
                    } catch (Exception e) {
                        flow.getNode(jobId).setStatus(Status.FAILED);
                    }
                }
                
                // 3. 等待下一个Job状态变更事件（阻塞）
                Event event = waitForNextEvent(timeout);
                if (event != null) {
                    handleJobStatusChange(event);
                }
            }
            
            // 4. 所有Job完成，确定Flow终态
            determineFinalStatus();
            
        } catch (Exception e) {
            flow.setStatus(Status.FAILED);
            logger.error("Flow execution failed", e);
        }
    }
    
    private void handleJobStatusChange(Event event) {
        String jobId = event.getData().get("jobId");
        Status jobStatus = Status.valueOf(event.getData().get("status"));
        
        logger.info("Job {} status changed to {}", jobId, jobStatus);
        
        // 检查失败策略
        if (jobStatus == Status.FAILED) {
            String failureAction = flow.getFailureAction();
            if ("cancel".equals(failureAction)) {
                // 一个Job失败，立即取消整个Flow
                killAllRunningJobs();
                flow.setStatus(Status.KILLED);
            }
        }
    }
    
    private void determineFinalStatus() {
        for (Node node : flow.getNodes()) {
            if (node.getStatus() == Status.FAILED) {
                flow.setStatus(Status.FAILED);
                return;
            }
            if (node.getStatus() == Status.KILLED) {
                flow.setStatus(Status.KILLED);
                return;
            }
        }
        flow.setStatus(Status.SUCCEEDED);
    }
}
```

#### 步骤3：事件机制

```java
// EventHandler.java —— 事件处理基类
public class EventHandler {
    private final BlockingQueue<Event> eventQueue = new LinkedBlockingQueue<>();
    
    public void fireEvent(Event event) {
        eventQueue.add(event);
    }
    
    protected Event waitForNextEvent(long timeoutMillis) {
        return eventQueue.poll(timeoutMillis, TimeUnit.MILLISECONDS);
    }
}
```

### 3.2 测试验证

```java
@Test
public void testStateTransitions() {
    Flow flow = new Flow("test", new Props());
    
    // 合法转换
    assertTrue(flow.setStatus(Status.RUNNING));
    assertTrue(flow.setStatus(Status.SUCCEEDED));
    
    // 非法转换
    assertFalse(flow.setStatus(Status.READY));  // 从SUCCEEDED不能回到READY
}
```

## 4. 项目总结

理解Flow状态机是排查"Flow僵死""状态不切换"等问题的关键。核心要点：
- Flow状态由FlowRunner的事件循环驱动
- Job状态变更通过BlockingQueue事件传递
- 状态转换有明确的合法路径，非法转换会被拒绝
- 并发安全通过`synchronized`和`volatile`保证

### 思考题

1. 当前的状态机在多个Job同时失败时可能存在事件丢失问题。如何改进事件机制来保证事件不丢失？
2. 如果要支持"Flow暂停→恢复"功能（PAUSED状态），需要修改哪些类和哪些状态转换规则？
