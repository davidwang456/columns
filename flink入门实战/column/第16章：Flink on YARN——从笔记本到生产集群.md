# 第16章：Flink on YARN——从笔记本到生产集群

---

## 1. 项目背景

你在本地IDE中开发的Flink作业跑通了，但接下来要面对一个现实问题：**怎么部署到生产集群？**

生产环境的Hadoop/YARN集群上有数十个大数据作业在运行：Hive ETL、Spark任务、MapReduce……你的Flink作业需要兼容这个生态——共享YARN资源队列、使用HDFS做Checkpoint存储、认证Kerberos、提交到指定的YARN队列。

Flink on YARN是当前国内使用最广泛的Flink部署模式（约占60%以上），它利用YARN的资源管理能力来调度Flink作业的TaskManager。

Flink on YARN提供了三种提交模式：

| 模式 | 说明 | 典型使用场景 |
|------|------|-------------|
| **Session模式** | 先启动一个常驻的Flink集群(JM+TM)，然后向这个集群提交多个作业 | 开发测试、小作业共享资源 |
| **Per-Job模式** | 每个作业启动一个独立的Flink集群，作业完成后自动释放YARN资源 | 生产作业（推荐） |
| **Application模式(1.11+)** | 作业的main方法在YARN的ApplicationMaster中运行，分离客户端依赖 | 生产作业（最新推荐） |

---

## 2. 项目设计

> 场景：小胖在本地跑通了作业，要在公司的Hadoop集群部署，结果不知道怎么下手。

**小胖**：我在本地用env.execute()提交，但在生产上怎么搞？把jar包扔到服务器上用java -jar跑？

**大师**（笑）：Flink的`env.execute()`只在开发环境本地运行用。生产环境有专门的提交工具——`flink run`命令行。你的jar包提交到YARN上，YARN负责申请资源、启动JobManager和TaskManager容器。

**技术映射：flink run 提交作业到YARN的三步——① 客户端解析jar中的Flink配置 ② 通过YARN Client申请Container启动JobManager ③ JobManager根据作业并行度向YARN申请TaskManager Container。**

**小白**：Session、Per-Job、Application三种模式什么区别？我在网上看完更糊涂了。

**大师**：核心区别在于**生命周期**和**隔离性**：

**Session模式**：先有集群，后提交作业。JobManager常驻运行，多个作业共享TaskManager资源。好处是启动快（不用每次申请YARN资源），坏处是作业之间互相影响——一个作业的OOM可能打死其他作业的TaskManager。

**Per-Job模式**：每个作业独立申请YARN资源。启动稍慢（每次需要申请Container），但隔离性好。一个作业挂了不影响其他作业。

**Application模式**：Per-Job的进化版——main方法在YARN的ApplicationMaster里执行。好处是不需要客户端Flink环境，客户端只需要一个jar包和一行命令。

**技术映射：从资源隔离角度——Session < Per-Job < Application。生产敏感作业推荐Application模式。**

**小胖**：那Checkpoint存哪里？我本地开发用的file:///tmp，生产上不可能。

**大师**：生产环境Checkpoint存储在HDFS上。你需要将Flink的State Backend配置为RocksDB（大状态），Checkpoint存储路径为HDFS路径。

**技术映射：HDFS上的Checkpoint目录结构——<basePath>/<jobId>/chk-<id>/_metadata。同一个jobId的Checkpoint增量存储在同一个目录下，可以追溯到任何历史版本。**

---

## 3. 项目实战

### 环境准备

| 组件 | 版本 | 说明 |
|------|------|------|
| Hadoop YARN | 3.3.6+ | 资源管理 |
| HDFS | 3.3.6+ | 分布式文件系统（Checkpoint存储）|
| Apache Flink | 1.18.1 | 计算引擎 |
| flink-shaded-hadoop | 2.10.2 | Hadoop兼容包 |

### 分步实现

#### 步骤1：配置Flink on YARN依赖

**目标**：确保Flink能正确识别Hadoop配置并提交到YARN。

```bash
# 1. 设置HADOOP_CLASSPATH（非常重要！）
export HADOOP_CLASSPATH=`hadoop classpath`

# 2. 确认Flink能识别YARN
./bin/flink run-application -t yarn-application \
  -p 4 \
  -D yarn.application.name="MyFlinkJob" \
  -Dyarn.application.queue="default" \
  /path/to/your-job.jar
```

**坑位预警**：如果不设置`HADOOP_CLASSPATH`，会报错：

```
Exception in thread "main" org.apache.flink.configuration.IllegalConfigurationException:
The number of requested Virtual Cores exceeds the number of available Virtual Cores.
```

#### 步骤2：Session模式部署

**目标**：启动一个Session集群，向其中提交多个作业。

```bash
# Step 1: 启动Session集群（1个JobManager + 2个TaskManager，每个TM 4 Slot）
./bin/yarn-session.sh \
  -n 2 \
  -s 4 \
  -jm 2048 \
  -tm 4096 \
  -nm "flink-session-cluster" \
  -d    # detached模式（后台运行）

# 输出：
# 2026-04-28 14:00:00,000 INFO  org.apache.flink.yarn.YarnClusterDescriptor  -
# YARN cluster with applicationId application_1714293932_0001 started.
# JobManager Web Interface: http://yarn-proxy:8088/proxy/application_1714293932_0001/

# Step 2: 向Session集群提交作业
./bin/flink run -t yarn-session \
  -Dyarn.application.id=application_1714293932_0001 \
  -c com.flink.column.chapter15.RealtimeDashboardJob \
  /jobs/flink-practitioner.jar
```

**Session模式下WebUI访问**：
```
http://<yarn-proxy>:8088/proxy/application_<appId>/
```

#### 步骤3：Per-Job模式部署（推荐）

**目标**：每个作业独立启动YARN集群，作业完成自动释放资源。

```bash
# Per-Job模式
./bin/flink run -t yarn-per-job \
  -p 8 \
  -jm 4096m \
  -tm 8192m \
  -Dyarn.application.name="OrderDashboard" \
  -Dyarn.application.queue="production" \
  -Dtaskmanager.numberOfTaskSlots=4 \
  -Dstate.backend=rocksdb \
  -Dstate.checkpoints.dir=hdfs://hadoop-nn:8020/flink-checkpoints \
  -Dexecution.checkpointing.interval=30s \
  -c com.flink.column.chapter15.RealtimeDashboardJob \
  /jobs/flink-practitioner.jar
```

**关键参数说明**：

| 参数 | 值 | 说明 |
|------|----|------|
| `-t yarn-per-job` | - | 提交模式 |
| `-p` | 8 | 作业并行度 |
| `-jm` | 4096m | JobManager内存 |
| `-tm` | 8192m | 每个TaskManager内存 |
| `-Dyarn.application.queue` | production | YARN队列 |
| `-Dstate.backend` | rocksdb | 状态后端 |
| `-Dstate.checkpoints.dir` | hdfs://... | Checkpoint存储路径 |

#### 步骤4：Application模式部署（最新推荐）

**目标**：使用Application模式，main方法在YARN AM中执行。

```bash
# Application模式——不需要本地Flink集群，不需要yarn-session
./bin/flink run-application -t yarn-application \
  -p 8 \
  -jm 4096m \
  -tm 8192m \
  -Dyarn.application.name="OrderDashboard" \
  -Dstate.backend=rocksdb \
  -Dstate.checkpoints.dir=hdfs://hadoop-nn:8020/flink-checkpoints \
  -c com.flink.column.chapter15.RealtimeDashboardJob \
  /jobs/flink-practitioner.jar
```

**与Per-Job的核心区别**：
- Per-Job：客户端JVM执行main方法，生成JobGraph，上传到YARN的JobManager
- Application：客户端只上传jar包，main方法在YARN AM的JVM中执行

#### 步骤5：配置高可用（HA）

**目标**：JobManager挂掉后自动恢复，不依赖单点。

```properties
# flink-conf.yaml
# ========== 高可用配置 ==========
high-availability: zookeeper
high-availability.zookeeper.quorum: zk1:2181,zk2:2181,zk3:2181
high-availability.storageDir: hdfs://hadoop-nn:8020/flink-ha

# ========== JobManager HA ==========
jobmanager.rpc.address: 0.0.0.0
jobmanager.memory.process.size: 4096m

# ========== TaskManager ==========
taskmanager.numberOfTaskSlots: 4
taskmanager.memory.process.size: 8192m

# ========== Checkpoint ==========
state.backend: rocksdb
state.checkpoints.dir: hdfs://hadoop-nn:8020/flink-checkpoints
execution.checkpointing.interval: 30000
state.backend.incremental: true
```

**提交HA作业**：

```bash
./bin/flink run-application -t yarn-application \
  -Dhigh-availability=zookeeper \
  -Dhigh-availability.zookeeper.quorum="zk1:2181,zk2:2181,zk3:2181" \
  -Dhigh-availability.storageDir="hdfs://hadoop-nn:8020/flink-ha" \
  -c MainClass /jobs/job.jar
```

#### 步骤6：运维命令速查

```bash
# 列出YARN上的Flink作业
./bin/flink list -t yarn-per-job -Dyarn.application.id=<appId>

# 列出所有运行中的YARN Flink任务
yarn application -list | grep -i flink

# 停止作业（保留Checkpoint）
./bin/flink cancel -t yarn-per-job <jobId>

# 从Savepoint恢复
./bin/flink run -t yarn-per-job -s hdfs://.../savepoint-xxxxx \
  -c MainClass /jobs/job.jar

# 查看JobManager日志
yarn logs -applicationId <appId> -containerId <containerId>

# 强制杀死YARN应用
yarn application -kill <appId>
```

#### 步骤7：生产环境最佳实践配置

```properties
# ========== 资源配置（按数据量估算） ==========
# 每秒10万条数据，每条1KB，状态大小500GB
# 建议配置：
taskmanager.numberOfTaskSlots: 8
parallelism.default: 64
jobmanager.memory.process.size: 8192m       # 8GB JM
taskmanager.memory.process.size: 16384m     # 16GB TM
taskmanager.memory.managed.size: 8192m      # 8GB 托管内存（RocksDB用）

# ========== RocksDB优化 ==========
state.backend: rocksdb
state.backend.incremental: true
state.backend.rocksdb.memory.managed: true
state.backend.rocksdb.compaction.level.max-size-level-base: 512mb

# ========== 网络与反压 ==========
taskmanager.network.memory.min: 512mb
taskmanager.network.memory.max: 1gb
taskmanager.network.memory.buffer-debloat.enabled: true
```

### 可能遇到的坑

1. **"YARN application failed to start"——ResourceManager无法分配Container**
   - 根因：`yarn.scheduler.minimum-allocation-mb` > flink配置的TM内存；或YARN队列资源不足
   - 解决：检查YARN配置`yarn.scheduler.minimum-allocation-mb`（通常1GB）；检查队列剩余资源

2. **HDFS权限拒绝：Permission denied: user=flink**
   - 根因：Flink提交作业的Linux用户没有HDFS对应路径的写权限
   - 解方：创建HDFS目录并授权：`hdfs dfs -chmod 777 /flink-checkpoints`

3. **Session模式下多个作业TaskManager资源争抢**
   - 根因：Session集群的TM资源是固定的，多个作业共享。当总需求Slot > 可用Slot时，作业卡在"Resource Pending"状态
   - 解方：使用Per-Job或Application模式，每个作业独立资源

---

## 4. 项目总结

### 三种部署模式对比

| 维度 | Session | Per-Job | Application |
|------|---------|---------|-------------|
| 集群生命周期 | 长期运行（手动停止） | 跟随作业 | 跟随作业 |
| 资源隔离 | 差（共享TM） | 好 | 好 |
| 启动速度 | 快（集群已存在） | 慢（每次申请资源） | 慢（每次申请资源） |
| 客户端依赖 | 需要Flink安装 | 需要Flink安装 | 不需要（仅jar和配置） |
| 多个作业 | 支持提交多个 | 每个作业独立集群 | 每个作业独立集群 |
| 适用场景 | 开发测试/小作业 | 生产作业 | 生产作业（推荐） |

### 参数调优核心原则

- **Slot数量 = 并行度 × 算子链数量**（不要超过CPU核数×2）
- **TM内存** = 堆内内存 + 堆外内存（网络） + 托管内存（RocksDB）
- **JM内存** = 约1-2GB + 0.5GB × 并行度（用于存储ExecutionGraph）
- **Checkpoint间隔** ≥ 一次Checkpoint耗时的3倍

### 注意事项
- Flink on YARN要求Hadoop版本 ≥ 2.8（支持container类型和资源标签）
- 使用Kerberos认证时，客户端需要`kinit`获取ticket，并在`flink-conf.yaml`中设置`security.kerberos.login.keytab`
- 推荐使用Application模式，因为它减少了客户端的版本依赖冲突

### 常见踩坑经验

**案例1：Per-Job模式下TaskManager启动后很快退出，日志显示"Container killed by ApplicationMaster"**
- 根因：TaskManager堆内存 + 堆外内存总和超过了YARN Container允许的最大内存
- 解方：`yarn.scheduler.maximum-allocation-mb`必须 >= `taskmanager.memory.process.size`

**案例2：Application模式下作业提交成功但立刻失败，日志显示"Failed to create directory flink-checkpoints"**
- 根因：Application模式下，main方法在YARN AM中执行，AM用户的HDFS权限与客户端用户不同
- 解方：在YARN AM的配置中指定Hadoop用户：`-Dyarn.app.mapreduce.am.env.HADOOP_USER_NAME=flink`

**案例3：Checkpoint频繁失败，状态很大但HDFS写入慢**
- 根因：RocksDB增量Checkpoint会产生大量小文件（SST文件），HDFS对小文件写入效率低
- 解方：调大`state.backend.rocksdb.compaction.level.max-size-level-base`减少文件数；或使用更快的存储介质（如SSD挂载的本地目录）

### 优点 & 缺点

| | Flink on YARN | Standalone / 手动部署 |
|------|-----------|-----------|
| **优点1** | YARN自动管理资源分配与回收，无需手动调度 | 需手动分配机器、管理集群资源 |
| **优点2** | 与Hadoop生态无缝集成——HDFS/Spark/Hive共享资源池 | 独立集群，与Hadoop生态不互通 |
| **优点3** | Per-Job/Application模式下资源隔离性好，互不影响 | 所有作业共享集群资源，隔离性差 |
| **优点4** | Session/Per-Job/Application三种模式灵活适配不同场景 | 部署模式单一，缺乏弹性 |
| **缺点1** | Container启动速度慢（5-30秒），频繁启停影响时效 | 启动快，进程常驻 |
| **缺点2** | 依赖Hadoop集群和YARN资源，环境复杂度高 | 无外部依赖，一个命令即可启动 |

### 适用场景

**典型场景**：
1. 已建Hadoop/YARN大数据平台的企业——Flink作业直接复用YARN资源
2. 生产作业需要严格资源隔离——Per-Job/Application模式保障作业间隔离
3. 作业需要访问HDFS数据——Flink on YARN天然支持HDFS文件系统
4. 需要灵活的资源伸缩——Session集群适合小作业共享，Per-Job适合大作业独立部署

**不适用场景**：
1. 纯云原生环境（K8S）——YARN在K8S环境下部署复杂，推荐Flink on K8S
2. 小型团队/个人开发测试——Standalone模式更简单，无需维护Hadoop集群

### 思考题

1. 如果一个YARN集群有100个节点、每个节点32GB内存、16核。Flink作业并行度=64，每个TaskManager配置8 Slot、16GB内存。最多可以同时运行几个这样的作业？（不考虑YARN的其他作业）

2. Application模式的"main方法在YARN AM中执行"有什么好处和坏处？如果你的作业的main方法需要加载本地的配置文件（如`application.properties`），Application模式下这个文件怎么传递给YARN AM？

---

> **完整代码**：本章完整代码请参考附录或访问 https://github.com/flink-column/flink-practitioner  
> **思考题答案**：见附录文件 `appendix-answers.md`
