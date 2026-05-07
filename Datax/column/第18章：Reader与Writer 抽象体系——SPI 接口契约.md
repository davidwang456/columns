# 第18章：Reader/Writer 抽象体系——SPI 接口契约

## 1. 项目背景

数据平台组的小刘接到一个需求：开发一个从 REST API 读取数据的新 Reader 插件。他打开 DataX 源码，面对 `Reader` 抽象类和其中的 `Job`、`Task` 内部类，以及 `AbstractJobPlugin`、`AbstractTaskPlugin` 这些中间层——五层继承结构让他一头雾水。他随便选了个方法覆盖，结果插件的 `init()` 被调用了两次，`destroy()` 根本没被调用。

找到 TL 一问，才知道他混淆了 `AbstractJobPlugin.init()` 和 `JobContainer.init()`——前者是插件级别的初始化（每个 Reader/Writer 实例调一次），后者是 JobContainer 级别的初始化（整个 Job 调一次）。DataX 的插件 SPI 接口有严格的契约：6 个生命周期方法、4 个必须实现的内部类、3 个接口约定。如果不能准确理解每个方法的调用时机和职责，开发的插件就是一颗定时炸弹。

本章从 `common/spi/Reader.java` 和 `Writer.java` 出发，完整梳理 Reader/Writer 插件的接口契约，并实现一个最小化的"空 Reader 插件"来验证你对生命周期的理解。

## 2. 项目设计——剧本式交锋对话

**（代码评审会，小刘的 MR 被 TL 打了回来）**

**小刘**：（困惑）我的 Reader 插件 init() 方法被调用了两次！是不是 DataX 的 bug？

**大师**：（打开 Reader.java 源码）不是因为 DataX 的继承树是五层的：

```
Reader (定义 Job/Task 内部类)
  └── Reader.Job extends AbstractJobPlugin
        └── AbstractJobPlugin extends AbstractPlugin
  └── Reader.Task extends AbstractTaskPlugin
        └── AbstractTaskPlugin extends AbstractPlugin
```

`Reader.Job` 和 `Reader.Task` 是两个独立的类——它们各自走自己的生命周期。JobContainer 先调用 `readerJob.init()`，然后调用 `readerJob.split()` 产出 N 个 Task 配置。TaskGroupContainer 拿到 Task 配置后，**重新 new N 个 `Reader.Task` 实例**，每个 Task 走 `task.init()` → `task.prepare()` → `task.startRead()`。

所以你看到的"两次"其实是—— `Reader.Job.init()` 被调用一次，然后每个 `Reader.Task.init()` 也各被调用一次。如果你的代码把逻辑写在了公共的 `init()` 方法里而没有区分 Job 和 Task，就会出现逻辑重复。

**技术映射**：Reader.Job vs Reader.Task = 建筑设计师 vs 施工队。设计师（Job）只看图纸一次，设计出 N 张施工单（split 产出 Task 配置）。施工队（Task）每个工地派一支队伍，每支队伍各干各的活。

**小胖**：（吃着薯片）那为啥不直接在 Job 里就把数据读了？还非得拆个 Task 出来？

**大师**：（摇头）因为 Job 是单线程的。如果 10 亿行数据在 Job 里串行读，10 小时都读不完。拆成 N 个 Task，每个 Task 独立线程读一小段，20 个 Task 并行跑，30 分钟搞定。

**小白**：（追问）那 RecordSender 和 RecordReceiver 接口又是干什么的？

**大师**：这两个是 Reader 和 Writer 之间的"握手协议"。

```
Reader.Task.startRead(RecordSender sender) {
    while (hasMoreData()) {
        Record record = sender.createRecord();
        record.addColumn(...);
        sender.sendToWriter(record);
    }
    sender.terminate(); // 告诉 Writer "我发完了"
}

Writer.Task.startWrite(RecordReceiver receiver) {
    Record record;
    while ((record = receiver.getFromReader()) != null) {
        // 如果是 TerminateRecord，说明 Reader 发完了
        // 否则，正常写入
        writeToDB(record);
    }
}
```

Reader 永远不看 RecordReceiver，Writer 永远不看 RecordSender——它们之间唯一的通信介质就是 Channel。这个设计把 Reader 和 Writer 彻底解耦。

## 3. 项目实战

### 3.1 步骤一：完整生命周期图

```
JobContainer 阶段:
  Reader.Job.init(config)          ← 解析插件配置
  Reader.Job.preCheck()            ← 验证配置合法性
  Reader.Job.prepare()             ← 全局准备（建连接池）
  Reader.Job.split(adviceNumber)   ← 切分Task ← 核心方法
  Reader.Job.post()                ← 全局清理
  Reader.Job.destroy()             ← 释放资源

TaskGroupContainer 阶段:
  Reader.Task.init(config)         ← 每个Task独立初始化
  Reader.Task.prepare()            ← 每个Task独立准备
  Reader.Task.startRead(sender)    ← 开始读数据 ← 核心方法
    └── while(有数据) { sender.sendToWriter(record); }
    └── sender.terminate();
  Reader.Task.post()               ← 每个Task独立清理
  Reader.Task.destroy()            ← 释放Task资源
```

### 3.2 步骤二：实现最小化 Reader 插件

**目标**：理解 Reader 插件的开发框架，用最少代码实现一个能跑的空 Reader。

**项目结构**：

```
minimal-reader/
├── pom.xml
├── src/main/java/com/example/MinimalReader.java
└── src/main/resources/plugin.json
```

**pom.xml**：

```xml
<project>
    <modelVersion>4.0.0</modelVersion>
    <groupId>com.example</groupId>
    <artifactId>minimal-reader</artifactId>
    <version>1.0.0</version>
    
    <dependencies>
        <dependency>
            <groupId>com.alibaba.datax</groupId>
            <artifactId>datax-common</artifactId>
            <version>0.0.1</version>
            <scope>provided</scope>  <!-- 由 DataX 提供 -->
        </dependency>
    </dependencies>
</project>
```

**plugin.json**：

```json
{
    "name": "minimalreader",
    "class": "com.example.MinimalReader",
    "description": "A minimal reader for educational purposes",
    "developer": "tutorial"
}
```

**MinimalReader.java**：

```java
package com.example;

import com.alibaba.datax.common.element.Record;
import com.alibaba.datax.common.element.StringColumn;
import com.alibaba.datax.common.plugin.RecordSender;
import com.alibaba.datax.common.plugin.TaskPluginCollector;
import com.alibaba.datax.common.spi.Reader;
import com.alibaba.datax.common.util.Configuration;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.util.ArrayList;
import java.util.List;

public class MinimalReader extends Reader {
    private static final Logger LOG = LoggerFactory.getLogger(MinimalReader.class);

    /**
     * Job 级别：负责解析配置 + 切分 Task
     */
    public static class Job extends Reader.Job {
        @Override
        public void init() {
            LOG.info("=== MinimalReader.Job.init() ===");
            // 从配置中读取参数
            String message = this.getPluginJobConf().getString("message", "Hello DataX");
            int recordCount = this.getPluginJobConf().getInt("recordCount", 10);
            LOG.info("message={}, recordCount={}", message, recordCount);
        }

        @Override
        public void preCheck() {
            LOG.info("=== MinimalReader.Job.preCheck() ===");
            // 验证必填参数
            this.getPluginJobConf().getNecessaryValue("message", 
                MinimalReaderErrorCode.REQUIRED_VALUE);
        }

        @Override
        public void prepare() {
            LOG.info("=== MinimalReader.Job.prepare() ===");
            // 全局准备（本示例无操作）
        }

        @Override
        public List<Configuration> split(int adviceNumber) {
            LOG.info("=== MinimalReader.Job.split(adviceNumber={}) ===", adviceNumber);
            
            // 根据 adviceNumber 生成 N 个 Task 配置
            List<Configuration> taskConfigs = new ArrayList<>();
            for (int i = 0; i < adviceNumber; i++) {
                Configuration taskConfig = this.getPluginJobConf().clone();
                taskConfig.set("taskId", i);  // 为每个 Task 打上标记
                taskConfigs.add(taskConfig);
            }
            
            LOG.info("Split into {} tasks", taskConfigs.size());
            return taskConfigs;
        }

        @Override
        public void post() {
            LOG.info("=== MinimalReader.Job.post() ===");
        }

        @Override
        public void destroy() {
            LOG.info("=== MinimalReader.Job.destroy() ===");
        }
    }

    /**
     * Task 级别：负责实际的"读数据→发数据"
     */
    public static class Task extends Reader.Task {
        private String message;
        private int recordCount;

        @Override
        public void init() {
            LOG.info("=== MinimalReader.Task.init() taskId={} ===", 
                this.getTaskId());
            this.message = this.getPluginJobConf().getString("message");
            this.recordCount = this.getPluginJobConf().getInt("recordCount");
        }

        @Override
        public void prepare() {
            LOG.info("=== MinimalReader.Task.prepare() ===");
        }

        @Override
        public void startRead(RecordSender recordSender) {
            LOG.info("=== MinimalReader.Task.startRead() ===");
            
            for (int i = 0; i < recordCount; i++) {
                // 1. 创建一条空 Record
                Record record = recordSender.createRecord();
                
                // 2. 添加数据列
                record.addColumn(new StringColumn(message + " - " + i));
                
                // 3. 发送给 Writer（经由 Channel）
                recordSender.sendToWriter(record);
            }
            
            // 4. 发送结束标记
            recordSender.flush();
            recordSender.terminate();
            
            LOG.info("Sent {} records", recordCount);
        }

        @Override
        public void post() {
            LOG.info("=== MinimalReader.Task.post() ===");
        }

        @Override
        public void destroy() {
            LOG.info("=== MinimalReader.Task.destroy() ===");
        }
    }
}
```

**错误码定义**（配套的 ErrorCode 类）：

```java
package com.example;

import com.alibaba.datax.common.spi.ErrorCode;

public enum MinimalReaderErrorCode implements ErrorCode {
    REQUIRED_VALUE("MinimalReader-01", "缺少必填参数[{0}]");

    private final String code;
    private final String description;

    MinimalReaderErrorCode(String code, String desc) {
        this.code = code;
        this.description = desc;
    }

    @Override
    public String getCode() { return code; }

    @Override
    public String getDescription() { return description; }
}
```

### 3.3 步骤三：部署和测试插件

**部署**：

```bash
# 编译
cd minimal-reader
mvn clean package

# 部署到 DataX 插件目录
cp target/minimal-reader-1.0.0.jar $DATAX_HOME/plugin/reader/minimalreader/
cp src/main/resources/plugin.json $DATAX_HOME/plugin/reader/minimalreader/
```

**测试 Job 配置**：

```json
{
    "job": {
        "content": [{
            "reader": {
                "name": "minimalreader",
                "parameter": {
                    "message": "Hello from minimal reader",
                    "recordCount": 25
                }
            },
            "writer": {
                "name": "streamwriter",
                "parameter": {
                    "print": true
                }
            }
        }],
        "setting": {
            "speed": {"channel": 5}
        }
    }
}
```

**预期输出**：

```
=== MinimalReader.Job.init() ===
message=Hello from minimal reader, recordCount=25
=== MinimalReader.Job.preCheck() ===
=== MinimalReader.Job.prepare() ===
=== MinimalReader.Job.split(adviceNumber=5) ===
Split into 5 tasks
=== MinimalReader.Task.init() taskId=0 ===
=== MinimalReader.Task.prepare() ===
=== MinimalReader.Task.startRead() ===
Sent 25 records
...
=== MinimalReader.Task.destroy() ===
=== MinimalReader.Job.post() ===
=== MinimalReader.Job.destroy() ===
```

### 3.4 步骤四：Writer 侧对应接口

```java
public static class Task extends Writer.Task {
    @Override
    public void startWrite(RecordReceiver recordReceiver) {
        Record record;
        while ((record = recordReceiver.getFromReader()) != null) {
            if (record instanceof TerminateRecord) {
                break;  // Reader 发完了
            }
            // 写入目标端
            writeToTarget(record);
        }
        recordReceiver.shutdown();
    }
}
```

### 3.5 可能遇到的坑及解决方法

**坑1：忘记调用 sender.terminate()**

Writer 的 `getFromReader()` 会永远阻塞等待（因为 Channel 永远不会收到 terminat 标记）。Writer 线程永远不会结束，Job 状态永远是 RUNNING。

**坑2：在 Job.init() 中创建连接导致序列化失败**

`Reader.Job` 实例化后，`split()` 产出的 Task 配置会被序列化/反序列化传递到 TaskGroupContainer。如果在 Job 中放了不可序列化的对象（如 JDBC Connection），会抛 `NotSerializableException`。

解决：连接只创建在 Task 中。

**坑3：混淆 Task 和 Job 的配置对象**

`Reader.Job.init()` 中 `this.getPluginJobConf()` 返回的是整个 Job 的配置。`split()` 需要 clone 后修改再返回。如果直接返回同一个 `Configuration` 对象引用，所有 Task 共享同一个配置对象——修改其中一个会影响另一个。

## 4. 项目总结

### 4.1 Reader/Writer 接口契约速查

| 方法 | 调用者 | 调用次数 | 职责 | 典型操作 |
|------|--------|---------|------|---------|
| `Job.init()` | JobContainer | 1 | 解析全量配置 | 读取 column/table/jdbcUrl |
| `Job.preCheck()` | JobContainer | 1 | 验证配置权限 | 测试 JDBC 连接是否可达 |
| `Job.prepare()` | JobContainer | 1 | 全局准备 | 执行 preSql |
| `Job.split(N)` | JobContainer | 1 | 切分 Task | 查 MIN/MAX，等距切分 |
| `Job.post()` | JobContainer | 1 | 全局清理 | 执行 postSql |
| `Job.destroy()` | JobContainer | 1 | 释放资源 | 关闭连接池 |
| `Task.init()` | TaskGroupContainer | N(每个Task一次) | 初始化 Task 配置 | 读取本 Task 的 querySql |
| `Task.prepare()` | TaskGroupContainer | N | 准备 Task 资源 | 建立 JDBC 连接 |
| `Task.startRead/Write()` | Runner | N | 执行读写 | **核心业务逻辑** |
| `Task.post()` | Runner | N | 清理 Task | 提交事务 |
| `Task.destroy()` | Runner | N | 释放 Task 资源 | 关闭连接 |

### 4.2 优点

1. **职责清晰**：Job 管切分、Task 管执行——各司其职
2. **生命周期完整**：init→prepare→execute→post→destroy 五步，覆盖所有资源管理需求
3. **1:1 配对**：Reader-Task 和 Writer-Task 一一对应，通过 Channel 解耦
4. **插件隔离**：每个插件实现自己的 lifecycle，互不影响
5. **易于测试**：可以单独测试 `split()` 的返回值，不需要完整运行

### 4.3 缺点

1. **继承层次深**：5 层继承 + 内部类，新人容易迷失
2. **无状态保留**：Task 之间完全隔离，无法共享计算结果
3. **split() 只能返回一次**：不支持增量切分（先切一批，跑完再切下一批）
4. **异常处理模板化不够**：每个插件需要自己写 try-catch-destroy
5. **缺少异步支持**：startRead 是同步方法，不支持异步 IO

### 4.4 注意事项

1. Job 阶段的配置是全局的，不要放入"每个 Task 不同的参数"（如 WHERE 子句的具体值）
2. `sendToWriter()` 可能阻塞（Channel 满了），不要在主循环中做耗时操作
3. 务必在 startRead 结束时调用 `sender.terminate()`
4. destroy() 方法中必须释放所有资源（JDBC 连接、文件句柄、网络连接）
5. Task 之间不要共享可变状态（成员变量只在单个 Task 生命周期内有效）

### 4.5 思考题

1. 如果 `Reader.Job.split()` 返回 30 个 Task 配置，但 Writer.Job.split() 只返回了 20 个 Task 配置，merge 引擎会如何处理？
2. `RecordSender.createRecord()` 每次调用都是创建新的 Record 对象吗？如果 100 万行数据就要 new 100 万个 Record，如何优化？（提示：看 DefaultRecord 的池化机制）

（答案见附录）
