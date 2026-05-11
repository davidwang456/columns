# 第32章：Worker源码——任务执行器深度剖析

> **关键词**：WorkerServer、TaskExecuteProcessor、AbstractTaskExecutor、TaskChannel、ShellTask、进程管理、资源下载、心跳上报、日志流式传输、RPC通信

---

## 一、项目背景

大麦（Damai）数据团队遇到了一个令人困惑的Bug：一个运行了几个月的Python任务突然报错——`ModuleNotFoundError: pandas`。奇怪的是，pandas明明全局安装在所有Worker节点上，手动SSH到每台机器执行`python3 -c "import pandas"`都正常。难道DS运行任务用的不是系统Python？

4小时排查后真相大白：Worker在每个任务执行前会创建一个独立的工作目录（如`/tmp/dolphinscheduler/exec/process/123/456/`），并在该目录下启动进程。关键在于，Worker设置了独立的`PYTHONPATH`环境变量——它没有继承系统的`/usr/local/lib/python3.8/site-packages`，而是指向了自有的Python依赖路径。运维在某次升级DS时覆盖了`dolphinscheduler_env.sh`，将`PYTHON_HOME`配到了一个不含pandas的精简Python路径下。

修复简单——在`dolphinscheduler_env.sh`中正确配置`PYTHON_HOME`即可。但团队反思：如果不理解Worker内部如何创建进程、如何设置环境变量、如何管理工作目录，就会陷入"盲调"——只能对着日志猜原因。正如运维老张所说："我们一直把Worker当黑盒用——提交任务，拿结果。是时候掀开这个黑盒看看里面到底做了什么。"

Worker是调度系统中与"执行"最近的一环。Master负责调度决策——决定什么任务在什么时间执行；Worker负责在物理节点上落地执行——接管资源、拉起进程、捕获日志、上报结果。它就像一个操作系统里的进程管理器，只不过管理的是一个个数据任务。更深一层看，Worker的质量直接决定了调度系统的"最后一公里"——调度决策再完美，如果Worker在执行环节掉链子，一切都是白费。本章将带你深入到Worker的Java源码中，理解它如何启动、如何接收任务、如何执行Shell脚本、如何管理进程生命周期——让你从"盲调"进阶为"源码级诊断"。

---

## 二、项目设计——剧本式交锋对话

周一晨会刚结束，小胖端着第三杯咖啡晃进工位，大师和小白已经围在显示器前。

**小胖**（打了个哈欠）："Worker不就是fork一个进程跑脚本嘛！Shell就是`/bin/bash -c`，Python就是`/usr/bin/python3`，跟我终端里手动跑一样啊。Runtime.getRuntime().exec() 一行搞定。这有什么好深挖的？"

**大师**（把自己笔记本推到小胖面前）："那你看看这个——上周那个Python任务报`ModuleNotFoundError`，系统明明装了pandas，为什么Worker这边说找不到？"

小胖盯着日志皱眉。

**小白**（在白板上画了几笔）："不只是这个问题。我还有几个疑问——第一，资源中心上传的Shell脚本和JAR包，下载到Worker的哪个目录？第二，参数替换是在Master端做还是Worker端做？比如`${biz_date}`是在哪一步被替换成实际日期的？"

小胖插嘴："肯定是Master做完再发给Worker啊。"

**小白**（摇头）："不对。我看过Master的源码，Master传下来的taskParams里参数还是原始格式——`${biz_date}`这类占位符还在。说明替换发生在Worker端。"

**大师**（赞许地点头）："没错，小白说对了。继续。"

**小白**："第三，Shell脚本的stdout和stderr怎么实时显示在UI上？进程输出是逐行被捕获的，还是等进程结束了一次性传回去？第四，如果一个Worker在执行任务时宕机了，Master怎么发现？正在跑的任务会不会被重复执行？"

**大师**："问得好。按顺序来。我先用一个比喻讲清楚Worker的角色。"

大师站起身，拿起白板笔。

**大师**："你可以把Worker想象成一个**代工厂**。Master是甲方，下订单（TaskDispatchCommand），Worker是工厂，完成整个制造过程。"

| 代工厂步骤 | Worker映射 | 技术实现 |
|---|---|---|
| 收到订单 | 接收TaskDispatchCommand | RPC反序列化 |
| 备料 | 下载资源文件 | 从Storage下载到本地工作目录 |
| 换上工装 | 切换租户用户 | setRunAsUser() |
| 按工艺要求操作 | 参数替换 | `${参数名}` → 实际值 |
| 在指定工位生产 | 在工作目录执行 | `ProcessBuilder.directory(workDir)` |
| 每一步都拍照 | 实时日志采集 | stdout/stderr → 数据库 → UI |
| 汇报完成 | 结果上报Master | RPC回传TaskExecuteResultCommand |

**大师**："如果工厂突然断电——也就是Worker宕机——由于Worker在ZooKeeper注册的是**临时节点（Ephemeral Node）**，ZK收不到心跳就会自动删除节点，Master监听到节点删除事件就知道这个Worker挂了，会把该Worker上所有未完成的任务重新分配给其他健康Worker。"

**小胖**（恍然大悟）："原来ZK临时节点还有这作用！那心跳里不止有心跳本身吧？"

**大师**："心跳同时上报Worker的负载信息——CPU使用率、内存使用率、磁盘使用率、当前活跃任务数。Master在做任务分发时会根据负载选择最空闲的Worker。这就像物流系统给各个仓库派单，会优先派给库存压力最小的那个。"

**小白**："那回到日志的问题——UI上是怎么看到'实时'日志的？"

**大师**："Worker在启动进程后会开启两个线程——一个读stdout，一个读stderr。每读一行就做两件事：写到本地日志文件，同时推到数据库/存储层。前端通过轮询接口拉取增量日志。虽然不是WebSocket推，但逐行写入+前端定时轮询，观感上就是'实时'的。这就是**观察者模式的异步实现**。"

**小白**（合上笔记本）："我还有一个问题——如果任务超时了，Worker是直接`kill -9`吗？被kill的Shell子进程怎么办？比如Shell脚本里调用了Spark或者Hive，这些子进程也会被自动清理吗？"

**大师**："好问题，这是进程管理的核心痛点。Worker通过`Process.destroyForcibly()`来终止超时任务，它本质上是向操作系统发送SIGTERM信号。但问题来了：`destroyForcibly()`只杀子进程本身——也就是`/bin/bash -c "..."`这个进程。如果Shell脚本内部又fork出了Spark进程（它们属于不同的进程组），Worker是管不了的。这些孤儿进程会一直运行，直到自然结束或被集群管理员手动杀掉。"

**小胖**（紧张地放下咖啡）："所以我们去年那个凌晨3点CPU飙到100%的告警，就是因为一堆Spark进程没被清理掉？"

**大师**："正是。那时候我们在Shell任务里写的是`spark-submit --master yarn &`，Worker到时间杀了bash，但spark-submit变成了孤儿进程继续在YARN上跑。后来我们改成了两步走：第一，超时时先调`destroy()`给进程一个优雅退出的机会；第二，配置环境变量`PYTHON_HOME`和`HADOOP_CONF_DIR`时，Worker会在进程启动前记录PID，确保真正需要强杀时能追踪到整个进程树。"

**技术映射**：进程管理比表面看起来复杂得多——不是简单的fork+wait，而是涉及进程组、信号处理、资源清理的系统级问题。Worker的`AbstractTaskExecutor`里关于超时和取消的逻辑，本质上是在跟Linux的进程模型做斗争。

---

## 三、项目实战

### Step 1：WorkerServer启动流程

Worker是一个Spring Boot应用，通过`@PostConstruct`完成初始化：

```java
// WorkerServer.java
public class WorkerServer implements IStoppable {

    public static void main(String[] args) {
        SpringApplication.run(WorkerServer.class, args);
    }

    @PostConstruct
    public void init() {
        // ① 加载所有TaskPlugin（Shell、SQL、Spark、Python等）
        taskPluginManager.installPlugin();

        // ② 向ZooKeeper注册临时节点
        registryClient.register(
            "/dolphinscheduler/worker/" + host + ":" + port
        );

        // ③ 启动心跳线程（默认每10秒上报一次负载）
        heartbeatThread.start();

        // ④ 启动RPC Server，监听Master发来的任务分发指令
        rpcServer.start();
    }
}
```

四个关键步骤缺一不可。其中`installPlugin()`会扫描classpath下所有实现了`TaskChannel`接口的类，存入`Map<String, TaskChannel>`——这一步决定了Worker能执行哪些类型的任务。

### Step 2：TaskExecuteProcessor——接收任务并分发到线程池

Worker通过RPC接收Master发来的`TaskDispatchCommand`，核心处理逻辑在`TaskExecuteProcessor`中：

```java
@Component
public class TaskExecuteProcessor {

    private final ThreadPoolExecutor taskExecutor;  // 核心线程池

    public void process(TaskDispatchCommand dispatch) {
        // ① 根据任务类型找到对应的TaskChannel
        TaskChannel channel = taskPluginManager.getChannel(
            dispatch.getTaskType()
        );

        // ② 构建任务上下文（含参数、资源、工作目录等）
        TaskChannelContext context = TaskChannelContext.builder()
            .taskInstanceId(dispatch.getTaskInstanceId())
            .processInstanceId(dispatch.getProcessInstanceId())
            .taskParams(dispatch.getTaskParams())
            .resources(dispatch.getResources())
            .workDir(buildWorkDir(dispatch))
            .build();

        // ③ 通过TaskChannel创建Task实例（工厂模式）
        Task task = channel.createTask(context);

        // ④ 提交到线程池异步执行
        taskExecutor.submit(() -> {
            try {
                task.run();
                reportTaskResult(task, Status.SUCCESS);
            } catch (Exception e) {
                logger.error("Task execution failed", e);
                reportTaskResult(task, Status.FAILURE);
            }
        });
    }
}
```

这里的关键设计是**工厂模式+异步执行**：`TaskChannel`是工厂，根据任务类型生产对应的`Task`子类实例（ShellTask、SQLTask、SparkTask等），然后通过线程池解耦接收线程和执行线程，确保RPC处理不会被长时间阻塞。

### Step 3：AbstractTaskExecutor——任务生命周期的状态机

所有任务类型都继承自`AbstractTaskExecutor`，它定义了一个严格的状态机：

```
INIT → SUBMITTED → RUNNING → PREPARE → EXECUTE → SUCCESS/FAILURE
```

```java
// AbstractTaskExecutor.java（精简）
public abstract class AbstractTaskExecutor {
    protected enum State {
        INIT, SUBMITTED, RUNNING, PREPARE, EXECUTE, SUCCESS, FAILURE
    }

    public final void run() {
        try {
            setState(State.SUBMITTED);
            setState(State.RUNNING);

            // ① 准备阶段：下载资源、创建目录、设置环境
            prepare();

            // ② 执行阶段：各子类实现具体任务逻辑
            Status result = execute();
            setState(result == Status.SUCCESS ? State.SUCCESS : State.FAILURE);

        } catch (Exception e) {
            setState(State.FAILURE);
        }
    }

    protected abstract Status execute();  // 子类实现
}
```

**Template Method模式**在此体现：`run()`定义骨架（准备→执行→终态），`execute()`留给子类重写。这使得ShellTask只需关注"怎么执行Shell命令"，而不需要关心状态管理、资源下载、日志上报等通用逻辑。

### Step 4：ShellTask.execute()——最常用任务的完整执行流程

Shell任务是DS中使用频率最高的任务类型，理解它就理解了Worker执行各类任务的核心模式：

```java
public class ShellTask extends AbstractTask {

    @Override
    protected Status execute() {
        String rawCmd = taskParams.get("rawScript");

        // ① 参数替换：${biz_date} → 2024-01-15
        String command = substituteParams(rawCmd);

        // ② 创建工作目录
        String workDir = getWorkingDirectory();
        Files.createDirectories(Path.of(workDir));

        // ③ 构建进程
        ProcessBuilder pb = new ProcessBuilder("/bin/bash", "-c", command);
        pb.directory(new File(workDir));

        // ④ 设置环境变量（关键！）
        Map<String, String> env = pb.environment();
        env.put("PYTHON_HOME", config.getPythonHome());
        env.put("HADOOP_HOME", config.getHadoopHome());
        taskParams.forEach((k, v) -> env.put(k, String.valueOf(v)));

        // ⑤ 启动进程
        Process process = pb.start();

        // ⑥ 异步采集stdout/stderr（日志实时回传）
        Thread outReader = streamToLog(process.getInputStream(), "STDOUT");
        Thread errReader = streamToLog(process.getErrorStream(), "STDERR");

        // ⑦ 等待进程结束（带超时）
        boolean finished = process.waitFor(timeout, TimeUnit.SECONDS);
        if (!finished) {
            process.destroyForcibly();  // 超时强杀
            outReader.join();
            errReader.join();
            return Status.FAILURE;
        }

        int exitCode = process.exitValue();
        outReader.join();  // 等待日志线程读完全部输出
        errReader.join();

        return exitCode == 0 ? Status.SUCCESS : Status.FAILURE;
    }

    private String substituteParams(String rawCmd) {
        for (Map.Entry<String, String> entry : taskParams.entrySet()) {
            rawCmd = rawCmd.replace(
                "${" + entry.getKey() + "}", entry.getValue()
            );
        }
        return rawCmd;
    }
}
```

从⑷可以看出：`PYTHON_HOME`和`HADOOP_HOME`等环境变量是在Worker端显式设置的。这就是为什么如果`dolphinscheduler_env.sh`配置错误，Worker会找不到pandas——它根本不继承系统环境，而是重新构建了一个隔离的环境。

### Step 5：日志流式传输——UI实时日志的技术实现

```java
private Thread streamToLog(InputStream stream, String type) {
    Thread t = new Thread(() -> {
        try (BufferedReader reader =
             new BufferedReader(new InputStreamReader(stream))) {
            String line;
            while ((line = reader.readLine()) != null) {
                // 写本地日志文件
                logWriter.append(type + ": " + line + "\n");

                // 写入数据库（前端轮询此表）
                taskLogDao.append(taskInstanceId, line);

                // 通知Master有新的日志行
                notifyLogUpdate(taskInstanceId, line);
            }
        } catch (IOException e) {
            logger.error("Log streaming failed", e);
        }
    });
    t.setDaemon(true);
    t.start();
    return t;
}
```

逐行读取+即时写入数据库，前端通过每隔1-2秒轮询获取增量日志——"准实时"的观感由此而来。

### Step 6：资源下载——从Storage到本地工作目录

```java
private void downloadResources(List<ResourceInfo> resources) {
    String workDir = workingDirectory + "/resources/";
    Files.createDirectories(Path.of(workDir));

    for (ResourceInfo res : resources) {
        // "resource://shell/clean.sh" → "shell/clean.sh"
        String relativePath = res.getPath()
            .replace("resource://", "");

        storageOperate.download(
            relativePath,
            workDir + res.getFileName(),
            true  // overwrite
        );
        logger.info("Downloaded: {} → {}", relativePath, workDir);
    }
}
```

资源中心上传的文件存储在S3/HDFS/Local等后端。Worker在`prepare()`阶段下载到`/tmp/dolphinscheduler/exec/process/{processId}/{taskId}/resources/`目录，保证任务的执行与资源中心解耦。

### Step 7：Worker心跳与负载上报

```java
public class WorkerHeartbeatTask implements Runnable {
    @Override
    public void run() {
        while (!Thread.currentThread().isInterrupted()) {
            WorkerLoad load = WorkerLoad.builder()
                .cpuUsage(OSUtils.cpuUsage())
                .memoryUsage(OSUtils.memoryUsage())
                .diskUsage(OSUtils.diskUsage())
                .activeTaskCount(taskExecutor.getActiveCount())
                .maxTaskCount(taskExecutor.getMaximumPoolSize())
                .build();

            // 更新ZK节点的data（负载信息）
            registryClient.update(
                "/dolphinscheduler/worker/" + host + ":" + port,
                load.toByteArray()
            );

            Thread.sleep(heartbeatInterval);  // 默认10秒
        }
    }
}
```

Master通过监听ZK节点变化获取各Worker的实时负载，当`activeTaskCount >= maxTaskCount`或CPU/内存超过阈值时，Master会跳过该Worker，将任务分发给其他节点。

### Step 8：结果上报Master

```java
private void reportTaskResult(Task task, Status status) {
    TaskExecuteResultCommand result = TaskExecuteResultCommand.newBuilder()
        .setTaskInstanceId(task.getTaskInstanceId())
        .setProcessInstanceId(task.getProcessInstanceId())
        .setStatus(status.getValue())
        .setExitCode(task.getExitCode())
        .setLogPath(task.getLogPath())
        .setAppIds(task.getAppIds())  // Spark/Flink的YARN AppId
        .build();

    rpcClient.send(masterAddress, result);
}
```

### Step 9：关键Worker配置

```yaml
worker:
  exec.threads: 100                # 最大并发任务数
  heartbeat.interval: 10           # 心跳间隔（秒）
  task.max-cpu-load-avg: 2.0       # CPU负载阈值
  task.max-memory-usage: 0.8       # 内存使用率阈值
  fetch.thread.num: 5              # RPC消息处理线程数
  host.weight: 100                 # Worker权重（影响分发优先级）
```

### Step 10：从源码发现的常见问题

| 问题 | 根源 | 解决 |
|---|---|---|
| 进程泄漏 | timeout只杀死bash，grandchildren继续运行 | 使用`Process.destroyForcibly()`并递归杀子进程 |
| 工作目录堆积 | `/tmp`下旧任务目录未清理 | 配置定时清理策略或使用`task.removeCleanTask.execute=true` |
| UI看不到日志 | 日志采集线程异常退出（uncaught exception） | 捕获异常+重连机制 |
| 资源下载失败 | 文件名含特殊字符导致路径解析错误 | URL编码处理 |
| 内存估算不准 | 每任务进程+日志缓冲区≈50-200MB | 100并发 = 5-20GB内存需求 |

---

## 四、项目总结

**Worker的核心设计模式**：
- **生产者-消费者模式**：RPC线程接收任务→任务队列→线程池消费执行。这种解耦设计保证Master发任务的速度不受Worker执行速度影响，避免了背压（backpressure）。
- **观察者模式**：日志采集线程→逐行推送→UI轮询消费。不是简单的写完再读，而是生产（进程输出）和消费（UI展示）异步并行。
- **模板方法模式**：`AbstractTaskExecutor.run()`定义生命周期骨架——初始化、资源准备、执行、结果上报——`execute()`由子类实现。这使得33+种任务类型共享同一套生命周期管理，新增任务类型只需实现一个方法。
- **工厂模式**：`TaskChannel.createTask()`是工厂接口，根据任务类型字符串创建对应的Task实例——"SHELL"→ShellTask, "SPARK"→SparkTask——实现了类型到实现的映射解耦。

这些模式共同构成了一个高内聚、低耦合的插件化执行引擎。理解了Worker的模式，就理解了DS可扩展性的根基。

**Shell vs Python vs Spark执行差异**：

| 任务类型 | 执行方式 | 进程管理 | 日志来源 |
|---|---|---|---|
| Shell | `ProcessBuilder` → `/bin/bash -c` | 进程退出码 | stdout/stderr流 |
| Python | `ProcessBuilder` → `python3 script.py` | 进程退出码 | stdout/stderr流 |
| Spark | `spark-submit`命令提交到YARN/K8s | 轮询YARN状态 | YARN日志聚合 |

**Worker资源容量规划公式**：
```
单节点并发上限 = min(
    threads配置值,
    可用内存 / 单任务内存(≈200MB),
    (CPU核数 × 0.8) / 单任务CPU需求
)
```

**三个源自生产线的Worker故障**：
1. **Python路径事故**（本章开头）：`PYTHON_HOME`配置错误 → 全局pip包不可见 → 参数替换阶段丢失关键环境变量
2. **磁盘写满**：工作目录未配置自动清理 → `/tmp`被1000+历史任务目录占满 → 新任务创建目录失败
3. **进程泄漏导致OOM**：Shell任务超时kill了bash但子进程group未清理 → 200+僵尸进程累积 → Worker OOM被Kubernetes重启

**思考题**：
1. Worker线程池达到饱和时，新来的任务是在RPC层排队，还是在Worker内部排队？如果在RPC层排队，对Master的分发策略有什么影响？
2. 如果Shell脚本中调用了Hive或Spark任务（即嵌套提交），Worker应该如何传递YARN认证凭证？`dolphinscheduler_env.sh`中的`HADOOP_CONF_DIR`起到什么作用？

---
