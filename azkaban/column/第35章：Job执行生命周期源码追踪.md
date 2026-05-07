# 第35章：Job执行生命周期源码追踪

## 1. 项目背景

### 业务场景

开发团队接到一个奇怪的问题：某个command类型的Job明明执行成功了（exit code = 0），但Azkaban显示为FAILED。而且这个故障只在"凌晨2点自动调度触发"时发生，手动执行一切正常。

排查源码后发现：Azkaban 3.x版本在解析`command.n`时，如果命令编号中间有"空洞"（如command.1存在但command.2缺失），会跳过后续所有命令。凌晨调度的Flow参数覆盖了部分命令行，导致command.3在参数替换后变成空行，被当作"命令结束标记"，后续的命令全部未执行。

### 痛点放大

不理解Job生命周期时：
- 无法解释"exit 0但显示FAILED"的悖论
- command类型和spark类型的生命周期完全不同，混用会出问题
- 无法实现自定义Job类型
- 排查Job执行问题只能看日志，不知道内部状态流转

## 2. 项目设计——剧本式交锋对话

**小胖**：大师，我写的Job明明exit 0了，Azkaban却判它失败！日志里最后一行的确输出了SUCCESS，但后面来了个[ERROR]……

**大师**：让我看看日志……找到了——你的Job最后一行是`sleep 60 && echo done`，但这个`sleep 60`被Azkaban的线程中断了。Azkaban JobRunner在检测到Flow被cancel时，会强制中断子进程。但这里的"中断"表现为exit code 143（SIGTERM），而你的command chain把exit 143误判为失败。

**小白**：那Azkaban的Job执行生命周期到底分几个阶段？

**大师**：

```
Job执行生命周期：

[READY] → FlowRunner发现依赖已满足
    ↓
[QUEUED] → 等待JobRunner线程池空闲
    ↓
[RUNNING] → JobRunner线程开始执行
    ↓  → 阶段1: setupJob()
    ↓  → 阶段2: runJob()      ← 核心：调用具体JobType的run()
    ↓      ├── ProcessJob: Runtime.exec() → Process.waitFor()
    ↓      └── SparkJob: spark-submit → YarnClient轮询
    ↓  → 阶段3: postProcess()
    ↓
[SUCCEEDED] / [FAILED] / [KILLED]
```

关键在于**阶段2**——不同JobType的run()实现完全不同。

### 技术映射总结

- **Process.waitFor()** = 等待快递签收（不等完不会走）
- **YarnClient轮询** = 物流追踪网页（每30秒刷新看货到哪了）
- **JobTypeManager** = 万能工具箱（选择正确的工具解决不同的问题）

## 3. 项目实战

### 3.1 核心源码

#### 步骤1：JobRunner主循环

```java
// JobRunner.java —— 简化核心逻辑
public class JobRunner extends EventHandler implements Runnable {
    
    @Override
    public void run() {
        try {
            // 阶段1: setup
            node.setStatus(Status.RUNNING);
            setupJobProperties();
            createWorkingDirectory();
            
            // 阶段2: 执行核心逻辑
            doRun();
            
            // 阶段3: 判断结果
            if (job.getStatus() == Status.SUCCEEDED) {
                node.setStatus(Status.SUCCEEDED);
            } else if (retryCount < maxRetries) {
                node.setStatus(Status.READY);  // 重新排队重试
                retryCount++;
            } else {
                node.setStatus(Status.FAILED);
            }
        } catch (Exception e) {
            node.setStatus(Status.FAILED);
            logger.error("Job execution failed", e);
        }
    }
    
    private void doRun() throws Exception {
        // 通过JobTypeManager获取具体Job实现
        Job job = jobTypeManager.buildJob(
            node.getJobType(),  // "command" / "spark" / ...
            jobProps
        );
        job.run();
    }
}
```

#### 步骤2：ProcessJob实现（command类型）

```java
// ProcessJob.java —— command类型执行核心
public class ProcessJob extends AbstractProcessJob {
    
    @Override
    public void run() throws Exception {
        // 1. 构建命令行
        List<String> commands = getCommandList();  // 提取command + command.1~n
        String workingDir = getWorkingDirectory();
        
        // 2. 拼接所有命令
        String fullCommand = String.join("\n", commands);
        
        // 3. 创建进程
        ProcessBuilder builder = new ProcessBuilder("/bin/bash", "-c", fullCommand);
        builder.directory(new File(workingDir));
        builder.redirectErrorStream(true);
        Process process = builder.start();
        
        // 4. 读取输出（异步写入日志）
        Thread outputThread = pipeOutput(process.getInputStream(), logger);
        
        // 5. 等待进程结束（阻塞！）
        int exitCode = process.waitFor();
        outputThread.join();
        
        // 6. 判断退出码
        if (exitCode != 0) {
            throw new Exception("Process exited with code " + exitCode);
        }
    }
    
    private List<String> getCommandList() {
        List<String> commands = new ArrayList<>();
        // 提取 command（无编号）
        String baseCmd = prop.get("command");
        if (baseCmd != null) commands.add(baseCmd);
        
        // 提取 command.1, command.2, ...
        int i = 1;
        while (true) {
            String cmd = prop.get("command." + i);
            if (cmd == null || cmd.trim().isEmpty()) break;
            commands.add(cmd);
            i++;
        }
        return commands;
    }
}
```

#### 步骤3：自定义Job Type实现

```java
// 自定义Job类型：版本检查Job
package com.company.azkaban.jobtype;

import azkaban.jobExecutor.AbstractProcessJob;
import azkaban.utils.Props;

public class VersionCheckJob extends AbstractProcessJob {
    
    public VersionCheckJob(String jobId, Props sysProps, Props jobProps, Logger log) {
        super(jobId, sysProps, jobProps, log);
    }
    
    @Override
    public void run() throws Exception {
        String targetVersion = jobProps.getString("spark.required.version");
        String clusterVersion = detectClusterSparkVersion();
        
        info("Required Spark version: " + targetVersion);
        info("Cluster Spark version: " + clusterVersion);
        
        if (!isCompatible(targetVersion, clusterVersion)) {
            throw new Exception(String.format(
                "Version mismatch! Required: %s, Cluster: %s",
                targetVersion, clusterVersion
            ));
        }
        info("Version check passed");
    }
    
    private String detectClusterSparkVersion() throws Exception {
        Process process = Runtime.getRuntime().exec("spark-submit --version 2>&1");
        BufferedReader reader = new BufferedReader(
            new InputStreamReader(process.getInputStream())
        );
        String line;
        while ((line = reader.readLine()) != null) {
            if (line.contains("version")) {
                return line.split("version")[1].trim();
            }
        }
        return "unknown";
    }
    
    private boolean isCompatible(String required, String actual) {
        // 主版本号一致即可
        String reqMajor = required.split("\\.")[0];
        String actMajor = actual.split("\\.")[0];
        return reqMajor.equals(actMajor);
    }
}
```

**注册自定义Job类型**：

```properties
# plugins/jobtypes/commonprivate.properties
# 注册自定义Job类型
azkaban.jobtype.plugin.classes=com.company.azkaban.jobtype.VersionCheckJob
```

```bash
# .job文件引用
type=versioncheck
spark.required.version=3.2.1
```

### 3.2 测试验证

```java
@Test
public void testVersionCheckJob() throws Exception {
    Props props = new Props();
    props.put("spark.required.version", "3.2.1");
    
    VersionCheckJob job = new VersionCheckJob("test", new Props(), props, logger);
    job.run();  // 验证是否能检测Spark版本
}
```

## 4. 项目总结

掌握Job生命周期是自定义JobType、排查执行异常的基础。核心要点：
- JobRunner状态流转：READY→RUNNING→SUCCEEDED/FAILED
- ProcessJob在`waitFor()`处阻塞直到子进程退出
- 自定义JobType只需实现`AbstractProcessJob.run()`方法

### 思考题

1. 如何在JobRunner中实现"Job优先级"——高优Job优先获取线程池资源？
2. ProcessJob的`waitFor()`在子进程卡死时会永久阻塞。如何实现超时+强制Kill机制？
