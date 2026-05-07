# 第19章：JobContainer 源码深度剖析——从 Job 到 Task 的完整链路

## 1. 项目背景

某大数据团队发现一个诡异的现象：一个配置了 `channel=10` 的 MySQL → HDFS 同步任务，预期 Task 数是 10，但实际日志显示 `split into 30 tasks`。30 个 Task 被分配到了 6 个 TaskGroup，但 `speed.channel=10` 是按 Job 级别生效的——6 个 TaskGroup 中总共只允许 10 个 Task 并发。

这导致了"一半 Task 在等，一半 Channel 在空转"的资源浪费。运维想知道这个数字是怎么算出来的——为什么 Reader 切了 30 个而不是 10 个？为什么是 6 个 TaskGroup 而不是 3 个？

答案藏在 `JobContainer` 的源码里。本章从 `Engine.main()` 入口出发，逐行拆解 `JobContainer` 的 9 步生命周期——preCheck、preHandle、init、prepare、split、schedule、post、postHandle、destroy——用一次完整的任务执行追踪，让你看清从一段 JSON 到 N 个线程读写的每一个决策细节。

## 2. 项目设计——剧本式交锋对话

**（深夜，运维监控室，小明的屏幕上滚动着 DataX 的源码）**

**小明**：（揉着眼睛）我看了两个小时源码，终于搞清楚为什么切了 30 个 Task 而不是 10 个——问题在 `Reader.Job.split(adviceNumber)` 这个方法！

**小胖**：（凑过来）adviceNumber 不就是 channel 吗？10 个 channel → 建议切 10 个 Task，很合理啊。

**小明**：（摇头）不，adviceNumber 只是个**建议值**。具体每个 Reader 的 split 实现可以不听——MySQL Reader 会在 adviceNumber 基础上乘以 `splitFactor`，如果 adviceNumber=10、splitFactor=3，那就是 30 个 Task。目的是防止单个 Task 的数据量太大导致 OOM。

**大师**：（竖起大拇指）而且还有一个更隐蔽的点——**Writer 也会参与 Task 数的决策**。如果 Reader 切了 30 个，Writer 只切了 20 个，最终合并时按 1:1 取**较多**的那个——30 个 Task。多余的 10 个 Writer Task 会自动缩小数据范围适应 Reader。

**技术映射**：split 协商 = 拼车组队。Reader 带了 30 个人（Task），Writer 带了 20 个人。最终组了 30 对——前 20 对 Reader+Writer 各出 1 人，后 10 对只有 Reader 出人，Writer 复用已有的 20 人中的 10 人来拼。

**小白**：（翻开源码）我注意到了另一个细节——`mergeReaderAndWriterTaskConfigs()` 不只是简单的取 max，它还会把 Reader 和 Writer 的配置**深度合并**。比如每个 Task 配置中既包含 `reader.parameter` 也包含 `writer.parameter`，这样 TaskGroupContainer 拿到一个 Config 就能同时启动 Reader 和 Writer。

## 3. 项目实战

### 3.1 步骤一：Engine 入口

```java
// Engine.java
public static void entry(final String[] args) throws Throwable {
    // 1. 解析命令行参数
    String jobPath = parseJobPath(args);   // -job /path/to/job.json
    String mode = parseMode(args);         // -mode standalone|local|distributed
    String jobId = parseJobId(args);       // -jobid 可选
    
    // 2. 解析 JSON 配置 → Configuration 对象
    Configuration configuration = ConfigParser.parse(jobPath);
    configuration.set("job.mode", mode);
    if (jobId != null) configuration.set("job.jobId", jobId);
    
    // 3. 启动引擎
    Engine engine = new Engine();
    engine.start(configuration);
}

public void start(Configuration configuration) {
    // 根据运行模式选择容器
    if ("standalone".equalsIgnoreCase(mode)) {
        JobContainer container = new JobContainer(configuration);
        container.start();
    }
}
```

### 3.2 步骤二：JobContainer 9 步生命周期

```java
// JobContainer.java
public class JobContainer extends AbstractContainer {
    
    public void start() {
        LOG.info("jobContainer starts job.");
        
        // Step 1: preCheck — 配置校验
        this.preCheck();
        
        // Step 2: preHandle — 预处理器（如 Schema 处理插件）
        this.preHandle();
        
        // Step 3: init — 加载插件
        this.init();
        
        // Step 4: prepare — 全局准备
        this.prepare();
        
        // Step 5: split — 切分 Task（★核心）
        this.split();
        
        // Step 6: schedule — 调度执行（★核心）
        this.schedule();
        
        // Step 7: post — 全局清理
        this.post();
        
        // Step 8: postHandle — 后处理器
        this.postHandle();
        
        // Step 9: invokeHooks — 生命周期钩子
        this.invokeHooks();
        
        // Step 10: destroy
        this.destroy();
    }
}
```

### 3.3 步骤三：init ——插件加载

```java
private void init() {
    // 获取 Reader 和 Writer 的配置
    Configuration readerConfig = this.configuration.get("job.content[0].reader");
    String readerName = readerConfig.getString("name");  // "mysqlreader"
    
    Configuration writerConfig = this.configuration.get("job.content[0].writer");
    String writerName = writerConfig.getString("name");  // "mysqlwriter"
    
    // 通过 LoadUtil 加载插件类，实例化 Job 对象
    Class<?> readerClass = LoadUtil.loadPluginClass(PluginType.READER, readerName);
    this.readerJob = (Reader.Job) readerClass.getDeclaredConstructor().newInstance();
    this.readerJob.setPluginJobConf(readerConfig.get("parameter"));
    
    Class<?> writerClass = LoadUtil.loadPluginClass(PluginType.WRITER, writerName);
    this.writerJob = (Writer.Job) writerClass.getDeclaredConstructor().newInstance();
    this.writerJob.setPluginJobConf(writerConfig.get("parameter"));
    
    // 执行 Job 级别的 init
    this.readerJob.init();
    this.writerJob.init();
}
```

### 3.4 步骤四：split ——Task 切分（★核心方法）

```java
private void split() {
    // 1. 获取 channel 建议数
    int adviceNumber = this.configuration.getInt("job.setting.speed.channel", 1);
    // 例如: channel=10 → adviceNumber=10
    
    // 2. 调用 Reader.Job.split(adviceNumber) → 返回 List<Configuration>
    List<Configuration> readerTaskConfigs = this.readerJob.split(adviceNumber);
    LOG.info("Reader split into {} tasks", readerTaskConfigs.size());
    // 例如: mysqlreader 返回 30 个 Task（在 adviceNumber 基础上乘以了 splitFactor）
    
    // 3. 调用 Writer.Job.split(adviceNumber) → 返回 List<Configuration>
    List<Configuration> writerTaskConfigs = this.writerJob.split(adviceNumber);
    LOG.info("Writer split into {} tasks", writerTaskConfigs.size());
    // 例如: mysqlwriter 返回 10 个 Task
    
    // 4. 合并 Reader 和 Writer 的 Task 配置（1:1 配对）
    List<Configuration> mergedTaskConfigs = mergeReaderAndWriterTaskConfigs(
        readerTaskConfigs, writerTaskConfigs);
    LOG.info("Merged into {} tasks", mergedTaskConfigs.size());
    // 取 max(30, 10) = 30 个 Task
    
    // 5. 计算 TaskGroup 数量
    int needChannelNumber = this.configuration.getInt("job.setting.speed.channel");
    int taskGroupNumber = (int) Math.ceil(
        (double) mergedTaskConfigs.size() / needChannelNumber);
    LOG.info("TaskGroup number: {}", taskGroupNumber);
    // 30 / 10 = 3 个 TaskGroup
    
    // 6. 将 Task 分配到 TaskGroup
    this.taskGroupConfigs = JobAssignUtil.assignFairly(
        mergedTaskConfigs, taskGroupNumber);
}
```

**merge 逻辑**：

```java
private List<Configuration> mergeReaderAndWriterTaskConfigs(
        List<Configuration> readerConfigs, 
        List<Configuration> writerConfigs) {
    
    int taskNumber = Math.max(readerConfigs.size(), writerConfigs.size());
    List<Configuration> merged = new ArrayList<>(taskNumber);
    
    for (int i = 0; i < taskNumber; i++) {
        Configuration mergedConfig = Configuration.newDefault();
        
        // 取第 i 个 Reader Task 配置（如果超出索引，取最后一个）
        Configuration readerConfig = readerConfigs.get(
            Math.min(i, readerConfigs.size() - 1));
        mergedConfig.set("reader", readerConfig);
        
        // 取第 i 个 Writer Task 配置（如果超出索引，取最后一个）
        Configuration writerConfig = writerConfigs.get(
            Math.min(i, writerConfigs.size() - 1));
        mergedConfig.set("writer", writerConfig);
        
        merged.add(mergedConfig);
    }
    
    return merged;
}
```

### 3.5 步骤五：schedule ——调度执行

```java
private void schedule() {
    // 1. 创建 Scheduler
    AbstractScheduler scheduler;
    if ("standalone".equals(mode)) {
        scheduler = new StandAloneScheduler(this.containerCommunicator);
    }
    
    // 2. 启动所有 TaskGroup
    scheduler.schedule(this.taskGroupConfigs);
    
    // 3. 监控进度，收集统计
    while (!scheduler.isFinished()) {
        Communication report = this.containerCommunicator.collect();
        LOG.info("Progress: {}/{} tasks completed, speed: {}/s", ...);
        Thread.sleep(5000);
    }
    
    // 4. 检查脏数据
    ErrorRecordChecker.checkErrorRecord(report, this.configuration);
    
    // 5. 打印统计摘要
    LOG.info("任务总计耗时    : {}", taskElapsedSeconds + "s");
    LOG.info("任务平均流量    : {}", avgByteSpeed + "B/s");
    LOG.info("记录写入速度    : {}", avgRecordSpeed + "rec/s");
    LOG.info("读出记录总数    : {}", totalReadRecords);
    LOG.info("读写失败总数    : {}", totalFailedRecords);
}
```

### 3.6 步骤六：调试验证——打印完整链路日志

在 `JobContainer` 的每个步骤加入增强日志：

```java
// 在 JobContainer 源码中插入
private void split() {
    long start = System.currentTimeMillis();
    LOG.info("========== SPLIT PHASE START ==========");
    
    int adviceNumber = this.configuration.getInt("job.setting.speed.channel", 1);
    LOG.info("adviceNumber (from speed.channel): {}", adviceNumber);
    
    List<Configuration> readerConfigs = this.readerJob.split(adviceNumber);
    LOG.info("Reader split result: {} tasks", readerConfigs.size());
    LOG.info("Reader task sizes: {}", readerConfigs.stream()
        .map(c -> c.getString("querySql"))
        .collect(Collectors.toList()));
    
    List<Configuration> writerConfigs = this.writerJob.split(adviceNumber);
    LOG.info("Writer split result: {} tasks", writerConfigs.size());
    
    List<Configuration> merged = mergeReaderAndWriterTaskConfigs(readerConfigs, writerConfigs);
    LOG.info("After merge: {} tasks", merged.size());
    
    int tgNumber = (int) Math.ceil((double) merged.size() / adviceNumber);
    LOG.info("TaskGroup number: {} ({} tasks / {} channels)", 
        tgNumber, merged.size(), adviceNumber);
    
    LOG.info("========== SPLIT PHASE END ({}ms) =========", 
        System.currentTimeMillis() - start);
}
```

**完整输出示例**：

```
=========== SPLIT PHASE START ===========
adviceNumber (from speed.channel): 10
Reader split result: 30 tasks
Reader task sizes: [
  querySql=SELECT * FROM orders WHERE id >= 1 AND id < 3333334,
  querySql=SELECT * FROM orders WHERE id >= 3333334 AND id < 6666667,
  ...
]
Writer split result: 10 tasks
After merge: 30 tasks
TaskGroup number: 3 (30 tasks / 10 channels)
=========== SPLIT PHASE END (1250ms) ===========
```

## 4. 项目总结

### 4.1 JobContainer 决策链总结

```
channel=10 (用户配置)
  ↓
adviceNumber=10 (传给 split)
  ↓
Reader.split(10) → 30 Tasks (MySQL Reader 内部 ×3 倍)
Writer.split(10) → 10 Tasks
  ↓
merge → 30 Tasks (取 max)
  ↓
TaskGroup 数 = ceil(30/10) = 3
  ↓
assignFairly → TaskGroup[0]=10, TaskGroup[1]=10, TaskGroup[2]=10
  ↓
每个 TaskGroup 最多同时跑 10 个 Task（channel=10 限制）
  ↓
TaskGroup[0] 10 个 Task 跑完 → 释放 channel → TaskGroup[1] 10 个 Task 开始 → ...
```

### 4.2 优点

1. **职责分离**：preCheck/init/split/schedule 各阶段独立，出问题可精准定位
2. **弹性切分**：Reader 可自主决定 Task 数（不受 channel 精确约束）
3. **公平调度**：assignFairly 确保 Task 均匀分布
4. **进度报告**：schedule 阶段 5 秒一次进度日志，可见整体进度
5. **可扩展**：支持 preHandler/postHandler 钩子，方便系统集成

### 4.3 缺点

1. **assumption 硬编码**：MySQL Reader 的 splitFactor 写死在代码里（×3），不支持配置
2. **不支持动态调整**：schedule 运行中无法改 channel 数或限速值
3. **无优先级**：所有 Task 平等，不能"重要的表先跑"
4. **内存假设固定**：capacity=128 对所有 Task 一样，不管行大小

### 4.4 注意事项

1. `channel` ≠ `Task 数`，Task 数通常 ≥ channel 数
2. 如果 Task 数 < channel 数 → 部分 channel slot 闲置
3. merge 时 Reader 配置的 "reader" key 和 Writer 配置的 "writer" key 分开存储
4. TaskGroup 的 channel 限制是"同时执行"，不是"总共执行"

### 4.5 思考题

1. MySQL Reader 的 splitFactor 是 3 倍，为什么不是 2 倍或 5 倍？这个值是如何确定的？
2. `assignFairly` 算法如果有 7 个 Task 和 3 个 TaskGroup，分配结果是什么？请写出伪代码并验证。

（答案见附录）
