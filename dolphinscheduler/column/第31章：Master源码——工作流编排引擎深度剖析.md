# 第31章：Master源码——工作流编排引擎深度剖析

## 1. 项目背景

大麦的DolphinScheduler集群发生了一起诡异事故：凌晨两点，200多个工作流同时触发，Master节点CPU瞬间飙到100%，30个工作流长期卡在RUNNING状态不推进，但翻看Worker日志却发现对应任务早已执行完毕。排查了两天，定位到根源问题——Master把90%的CPU时间花在了反复遍历DAG状态图上，根本没有真正去做任务分发。本质上是一个"忙等待"问题：`WorkflowExecuteRunnable`在`findReadyTasks()`中使用了`Thread.sleep(100)`轮询而非事件驱动。

这个故障暴露了一个事实：团队使用DS十个月，Master始终是一个黑盒。"知道它管调度，但不清楚怎么管"——这是大多数使用者的真实状态。Master作为工作流编排引擎，是整个调度系统的"大脑"，它负责从`t_ds_command`表消费命令、构建DAG依赖图、向Worker分发任务、处理任务回调、管理工作流状态机、以及协调故障恢复。这些逻辑一旦出错或是性能退化，直接影响整个集群的工作流调度。只有深入源码理解其运行模型，才能在生产环境中做好容量规划、性能调优与故障诊断。

## 2. 项目设计——剧本式交锋对话

**小胖抬起头，嘴里还咬着面包：**"Master不就是个while循环嘛！伪代码三行搞定——从数据库`t_ds_command`表轮询，读到命令就封装成RPC请求扔给Worker，Worker执行完回个callback，Master改下`t_ds_task_instance`的状态。三十行伪代码足够说清楚，有什么好看的？"

**小白摇了摇头：**"那你解释一下——第一，DAG怎么写？上千个任务节点，有串行有并行，Master怎么判断某个任务的所有上游依赖都已完成、可以执行了？它用什么数据结构维护拓扑关系、怎么检测环路？第二，Worker宕机了怎么办？它上面正在跑的任务谁来接管？你怎么确保不会丢了任务也不重复执行？第三，同一时间只有一个Leader Master在工作，你怎么保证ZooKeeper里不会因为网络分区出现双Leader？"

**大师放下茶杯，右手在空中划了一个弧形：**"把Master想象成交响乐指挥——他面前摆着完整的乐谱，也就是DAG定义，知道哪个乐手在什么时候应该演奏，哪个声部需要等前面的音节收尾了再进来。当某个乐手突然离场，也就是Worker宕机，指挥要立刻在谱面上标记中断位置，找到替补重新起拍。而那根指挥棒，就是ZooKeeper的临时顺序节点选举——任何时候舞台上只能有一个总指挥。"

"回过头看小胖的while循环——方向没错，但维度太浅了。Master的核心远不止循环本身，而在于循环内部传递的四个关键环节：**启动注册**（Spring Boot + ZK注册 + 选举Leader）、**命令消费**（MasterSchedulerBootstrap轮询`t_ds_command`、线程池控制消费速率）、**DAG状态机驱动**（WorkflowExecuteRunnable构建拓扑、检测就绪节点、事件驱动推进）、**任务分发与故障恢复**（Worker路由选择、Protobuf RPC、心跳超时接管）。这四个环节缺一个理解，你排查问题时就像戴着墨镜走夜路。"

**小胖似懂非懂：**"那线程池满了怎么办？锁竞争呢？"

**大师笑道：**"这就是设计精妙之处——Master的线程池上限`master.exec.threads`默认100，当同时运行的workflow超过这个数时，Master不会拒绝新命令，而是停止消费——命令留在`t_ds_command`表中排队。这是一种天然的反压机制：DB本身就是队列。但这也意味着，如果平均工作流执行时间过长，`t_ds_command`会积压，下次轮询才能消费。你那天凌晨的故障，就是积压+忙等待叠加出的雪崩效应。"

## 3. 项目实战

### Step 1: MasterServer启动——从main()到ZK注册

```java
// dolphinscheduler-master/src/main/java/org/apache/dolphinscheduler/server/master/MasterServer.java
@SpringBootApplication
public class MasterServer implements IStoppable {

    public static void main(String[] args) {
        // 1. 解析命令行参数（端口、配置文件路径）
        // 2. 启动Spring Boot，嵌入Jetty（非Tomcat）
        SpringApplication.run(MasterServer.class, args);
    }

    @Override
    public void stop(String cause) {
        // 优雅停机：通知所有运行中工作流暂停
        // 从ZK删除临时节点
        // 关闭线程池
    }
}
```

启动后，Master在ZooKeeper注册一个临时节点`/dolphinscheduler/master/{host:port}`，同时创建顺序临时节点参与Leader选举。只有选为Leader的Master才会进入命令消费主循环；Follower节点空转监听，仅在Leader宕机时参与新一轮选举。

### Step 2: MasterSchedulerBootstrap——调度心跳

```java
@Component
public class MasterSchedulerBootstrap {

    @Scheduled(fixedDelay = 1000) // 每秒轮询一次
    public void run() {
        if (!isLeader()) {
            return; // 非Leader直接返回
        }

        // 1. 从DB查就绪命令（按优先级降序、创建时间升序）
        List<Command> commands = commandDao.queryReadyCommands(100);

        // 2. 逐个消费
        for (Command cmd : commands) {
            if (workflowThreadPool.getActiveCount() >= maxThreads) {
                break; // 线程池满，停止消费，命令留在DB
            }

            WorkflowExecuteRunnable runnable = new WorkflowExecuteRunnable(
                cmd.getProcessDefinitionCode(),
                cmd.getProcessInstanceId()
            );
            workflowThreadPool.submit(runnable);
            commandDao.delete(cmd.getId());
        }

        // 3. 检查其他Master宕机留下的孤儿工作流
        failoverCoordinator.checkAndRecover();
    }
}
```

对应SQL：
```sql
SELECT * FROM t_ds_command
WHERE state = 0
ORDER BY priority DESC, create_time ASC
LIMIT 100;
```

这是整个Master的"呼吸节拍"。`fixedDelay=1000`意味着一轮消费结束后等待1秒再开始下一轮。线程池满时`break`保证了不会无限制创建Runnable，是一种简单的背压策略——数据库表天然做了缓冲队列。

### Step 3: WorkflowExecuteRunnable——DAG状态机引擎

```java
public class WorkflowExecuteRunnable implements Runnable {

    private final ProcessInstance processInstance;
    private final DAG<String, TaskNode, TaskRelation> dag;
    private WorkflowState currentState = WorkflowState.SUBMITTED;

    @Override
    public void run() {
        try {
            // 1. 从工作流定义构建DAG
            buildDag();
            updateState(WorkflowState.RUNNING_EXECUTION);

            // 2. 主循环：事件驱动推进
            while (!isFinished()) {
                // 查找所有上游已完成的就绪任务
                List<TaskNode> readyTasks = findReadyTasks();

                for (TaskNode task : readyTasks) {
                    submitTask(task); // 分发到Worker
                }

                // 事件等待（非忙等）
                waitForTaskEvents();

                // 处理完成/失败的任务
                processCompletedTasks();

                if (checkPauseSignal()) {
                    updateState(WorkflowState.PAUSE);
                    return;
                }
            }

            updateState(isAllSuccess() ? WorkflowState.SUCCESS : WorkflowState.FAILURE);

        } catch (Exception e) {
            updateState(WorkflowState.FAILURE);
        }
    }

    private List<TaskNode> findReadyTasks() {
        return dag.getBeginNodes().stream()
            .filter(node -> node.getState() == null || node.getState().isReady())
            .filter(node -> dag.getPreviousNodes(node).stream()
                .allMatch(prev -> prev.getState() == TaskState.SUCCESS))
            .collect(Collectors.toList());
    }
}
```

状态机流转路径：
```
SUBMITTED → READY_PAUSE → READY_STOP → RUNNING_EXECUTION
    → PAUSE / STOP / SUCCESS / FAILURE
```

关键设计：`waitForTaskEvents()`是事件驱动而非忙等——Worker执行完任务后通过Netty RPC发送`TaskExecuteResultCommand`，Master的`TaskEventProcessor`收到后唤醒对应`WorkflowExecuteRunnable`。这就是DS和早期竞品的核心区别：事件驱动保证Master CPU不空转。

### Step 4: DAG底层实现——拓扑排序与环路检测

```java
public class DAG<Node, NodeInfo, EdgeInfo> {

    private final Map<Node, NodeInfo> nodesMap;       // 所有节点
    private final Map<Node, List<Node>> edgesMap;     // 邻接表
    private final Map<Node, Integer> inDegree;        // 入度表

    public boolean addEdge(Node from, Node to) {
        if (hasCycle()) return false;  // DAG约束：严禁环路
        edgesMap.get(from).add(to);
        inDegree.merge(to, 1, Integer::sum);
        return true;
    }

    // 拓扑排序检测环路
    private boolean hasCycle() {
        return topologicalSort().size() != nodesMap.size();
    }

    // 获取入度为0的起始节点
    public List<Node> getBeginNodes() {
        return nodesMap.keySet().stream()
            .filter(n -> inDegree.get(n) == 0)
            .collect(Collectors.toList());
    }
}
```

这是工作流引擎的数据结构基石。`inDegree`为0的节点就是"就绪节点"——所有上游依赖都已满足。每次任务完成后递减下游节点的入度，为新就绪节点触达Worker分发。

### Step 5: 任务分发——选Worker与RPC投递

```java
private void submitTask(TaskNode taskNode) {
    // 从目标Worker组中按负载均衡选择Worker
    WorkerAddress worker = selectWorker(taskNode.getWorkerGroup());

    TaskDispatchCommand dispatch = TaskDispatchCommand.newBuilder()
        .setTaskInstanceId(taskNode.getTaskInstanceId())
        .setTaskType(taskNode.getTaskType())
        .setTaskParams(taskNode.getTaskParams())
        .build();

    rpcClient.send(worker, dispatch); // Netty Protobuf RPC
    taskNode.setState(TaskState.DISPATCHED);
}
```

Worker处理完成后返回`TaskExecuteResultCommand`，Master写入`t_ds_task_instance`并唤醒对应`WorkflowExecuteRunnable`继续推进DAG。

### Step 6: Master故障恢复——孤儿工作流接管

```java
@Component
public class MasterFailoverCoordinator {

    public void checkAndRecover() {
        // 1. 查出所有RUNNING状态的工作流实例
        List<ProcessInstance> running = processInstanceDao.findRunning();

        for (ProcessInstance pi : running) {
            String masterHost = pi.getHost();
            // 2. 检查原Master是否还活着
            if (!isHostAlive(masterHost)) {
                recoverWorkflow(pi); // 重新提交到线程池
            }
        }
    }

    private void recoverWorkflow(ProcessInstance pi) {
        // 新Master从DB重建DAG状态
        WorkflowExecuteRunnable runnable = new WorkflowExecuteRunnable(
            pi.getProcessDefinitionCode(), pi.getId()
        );
        workflowThreadPool.submit(runnable);
    }
}
```

这是分布式调度的关键保障。当Master A宕机，ZK临时节点消失，Master B当选新Leader。`MasterFailoverCoordinator`扫描`t_ds_process_instance`表中`host=Master_A`的RUNNING实例，重新构建DAG并从数据库中恢复任务状态，继续调度。

### Step 7: 全链路日志注入——追踪一次完整调度

```java
logger.info("[TRACE] 命令消费: workflowId={}, priority={}",
    cmd.getProcessDefinitionId(), cmd.getPriority());
logger.info("[TRACE] DAG构建: 节点数={}, 边数={}",
    dag.getNodesCount(), dag.getEdgesCount());
logger.info("[TRACE] 就绪任务: {}",
    readyTasks.stream().map(TaskNode::getName).toList());
logger.info("[TRACE] 分发任务 {} 至Worker {}",
    taskNode.getName(), worker.getAddress());
logger.info("[TRACE] 任务 {} 完成: 状态={}, 耗时={}ms",
    taskNode.getName(), result.getStatus(), result.getDuration());
logger.info("[TRACE] 工作流 {} 结束: 状态={}, 总耗时={}ms",
    processInstance.getName(), currentState, totalDuration);
```

把这六行日志注入`WorkflowExecuteRunnable`的关键路径上，就能追踪一次完整调度从命令消费到最终状态的所有生命周期事件。

### Step 8: Master核心配置调优

```yaml
master:
  exec:
    threads: 50           # 并发工作流数上限（建议 CPU核数 × 2）
  fetch-command-num: 100  # 每次轮询最大命令数
  dispatch-task-num: 50   # 单次分发任务数上限
  max-heartbeat-interval: 10s  # Worker心跳超时阈值
```

调优公式：`master.exec.threads` ≥ `预期并发工作流数 × 平均工作流执行时间(秒) ÷ 60`。太小则命令在DB积压，太大则Master内存膨胀（每个Runnable持有一份完整DAG）。

### Step 9: 源码分析揭示的常见坑

1. **命令消费瓶颈**：`LIMIT 100`意味着积压500条时最少需要5轮轮询才能全部消费，峰值流量下延迟可达5秒以上。
2. **DAG内存占用**：每个`WorkflowExecuteRunnable`将完整DAG加载到内存，含1000+任务节点的大型工作流会显著占用堆空间。
3. **缺乏Worker侧反压**：Master持续分发不检查Worker队列深度，导致任务在Worker端堆积。
4. **ZK会话超时**：默认8秒，意味着Leader宕机后最少8秒才能完成故障转移——这是DS的高可用冷启动下限。

### Step 10: 远程调试技巧

```bash
# Master启动JVM参数加入
-agentlib:jdwp=transport=dt_socket,server=y,suspend=n,address=5005

# IntelliJ: Run → Attach to Process → localhost:5005
# 关键断点:
# MasterSchedulerBootstrap.run()        — 观察命令轮询
# WorkflowExecuteRunnable.run()         — 工作流启动入口
# WorkflowExecuteRunnable.submitTask()  — 任务分发瞬间
# TaskEventProcessor.process()          — Worker回调处理
```

## 4. 项目总结

透过Master源码，我们看到了四种经典设计模式的工程化落地：

- **事件循环**：`MasterSchedulerBootstrap`每秒轮询命令，形成调度系统的心跳节拍。
- **状态机**：`WorkflowExecuteRunnable`定义了SUBMITTED到SUCCESS/FAILURE的完整生命周期，每个状态变换都有明确的前置条件。
- **Leader/Follower**：通过ZK临时顺序节点实现，保证同一时刻只有一个Master在消费命令和执行工作流。
- **生产者-消费者**：`t_ds_command`表天然充当了命令队列，线程池控制消费者并发度。

与同类系统对比：Airflow的Scheduler采用单进程轮询模型，大规模DAG下解析延迟显著；Azkaban的Executor将DAG解析和任务执行耦合在一起，资源利用率偏低。DS的Master-Worker分离架构在水平扩展性上更优，但代价是需要维护ZooKeeper集群和RPC通信层。

三个源自源码层面的经典线上问题解法：

1. **CPU 100%但无明显业务操作**：检查`findReadyTasks()`是否存在忙等轮询，考虑升级事件驱动回调的等待机制。
2. **命令积压但线程池未满**：检查`commandDao.queryReadyCommands`的SQL性能，`t_ds_command`表是否缺少`(state, priority, create_time)`联合索引。
3. **Worker任务已执行但Master不更新状态**：Debug `TaskEventProcessor.process()`回调链路，检查Protobuf反序列化是否因版本不一致而静默丢弃消息。

**反思题**：

1. 如果Master线程池并发度上限从100改为1000，系统会在什么场景下先遇到内存瓶颈而非CPU瓶颈？请结合`WorkflowExecuteRunnable`持有的DAG数据结构分析。
2. `MasterFailoverCoordinator`恢复孤儿工作流时，如何保证不会重复执行那些在原Master上已分发但尚未完成的Worker任务？提示：考虑任务实例的幂等性检查机制。
