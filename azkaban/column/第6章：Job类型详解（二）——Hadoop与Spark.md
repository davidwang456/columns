# 第6章：Job类型详解（二）——Hadoop与Spark

## 1. 项目背景

### 业务场景

某电商平台每天产生TB级的用户行为日志。数据团队需要Azkaban调度两类核心计算任务：
1. 凌晨2点启动MapReduce作业——清洗原始日志，按用户ID聚合PV/UV指标。
2. 凌晨4点启动Spark作业——基于清洗后的数据，运行机器学习模型计算用户偏好标签。

起初团队用`type=command`加`hadoop jar`命令来提交，但问题很快暴露：command Job无法感知Yarn集群状态，一个Job挂在Yarn队列里等了2小时，Azkaban却一直显示"RUNNING"，不知道是集群资源不足还是程序死锁。

### 痛点放大

使用通用command类型提交Hadoop/Spark任务时：

1. **状态不同步**：Yarn上任务已经失败并重试了3次，Azkaban还以为一切正常。
2. **日志缺失**：只能看到"提交成功"的命令行输出，真正有用的Yarn Application日志需要手动去ResourceManager查看。
3. **资源浪费**：任务挂了但Azkaban不知道，Executor线程一直被占用，其他任务排队等待。
4. **配置散落**：Hadoop/Spark参数散落在shell脚本、环境变量和JVM参数中，难以统一管理。

## 2. 项目设计——剧本式交锋对话

**小胖**（拍着桌子）：大师，见鬼了！昨晚上跑的一个Spark任务，Azkaban显示成功了，但我查Hive表，数据根本没更新！日志里就一行"Submitted application_xxx"，后面什么都没了。

**大师**：你是不是用`type=command`直接调`spark-submit`了？

**小胖**：对啊，我写的`command=spark-submit --class xxx --master yarn my.jar`，跑完返回exit code 0啊！

**大师**：这里有个大坑——`spark-submit`的exit code只表示"提交是否成功"，不表示"Spark任务是否执行成功"。提交成功后spark-submit就退出了，此时Yarn上的Application可能还在运行，甚至已经失败了。你等于只检查了"快递是否被快递员取走"，没有检查"快递是否送到收件人手里"。

**小白**：所以Azkaban有专门的Spark Job类型来解决这个问题？

**大师**：没错。Azkaban内置了`hadoopJava`和`spark`两种专用Job类型。它们的核心区别在于——会主动连接Yarn ResourceManager，持续监控Application状态，直到Application真正完成或失败。

**小白**：那具体是怎么实现的呢？监控是轮询还是回调？

**大师**：Azkaban的Hadoop/Spark Job类型底层使用Hadoop YarnClient API，每30秒轮询一次Application状态。状态机如下：

```
NEW → SUBMITTED → ACCEPTED → RUNNING → FINISHED
                                    ↘ FAILED
                                    ↘ KILLED
```

只有当Yarn Application进入`FINISHED`状态，并且最终状态为`SUCCEEDED`时，Azkaban Job才标记为成功。任何非成功终态（FAILED/KILLED/FINISHED_WITH_ERROR）都会导致Job失败。

**小胖**：那我之前那些用command类型的任务，是不是都应该改过来？

**大师**：分情况。如果你的Spark任务是短任务（几分钟跑完），且spark-submit用了`--deploy-mode client`（提交后等待完成），那command类型也能用。但如果你的任务是长任务、关键任务、或者用了`--deploy-mode cluster`，就一定要用专用类型。

**小白**：配置上有什么区别？专用类型要配什么参数？

**大师**（在白板上写下对比）：

```
command类型配置：               spark类型配置：
type=command                   type=spark
command=spark-submit \         spark.master=yarn
  --class Main \               spark.class=com.example.Main
  --master yarn \              spark.executor.memory=4g
  --executor-memory 4g \       spark.executor.cores=2
  my.jar                       spark.num.executors=10
                               spark.jars=my.jar,dep.jar
                               spark.yarn.queue=etl
```

专用类型的参数直接写在Job文件里，不需要记忆spark-submit的命令行格式。而且Azkaban会自动拼接spark-submit命令，配置文件集中在Job中。

**小胖**：那有什么坑吗？比如Java版本的兼容性问题？

**大师**：有！最容易踩的坑就是——Azkaban的JVM和Hadoop的JVM版本不一致。Azkaban运行在JDK 8上，但你的Hadoop集群可能是JDK 11环境。如果spark或hadoopJava类型在Azkaban JVM中加载了不兼容的类库，就会报`NoClassDefFoundError`。

解决方案是在Job中设置`env.HADOOP_HOME`和`env.JAVA_HOME`，让专用Job类型在单独的环境中提交任务。

### 技术映射总结

- **command类型提交Spark** = 让信鸽送信（丢没丢不知道，只看信鸽飞走了）
- **spark类型提交Spark** = 用快递+物流追踪（从取件到签收全程可查）
- **YarnClient轮询** = 每隔30秒看一眼快递柜（确认包裹被取走才算完）
- **HADOOP_CONF_DIR** = 快递站地址簿（告诉Azkaban去哪找Yarn集群）

## 3. 项目实战

### 3.1 环境准备

| 组件 | 版本 | 用途 |
|------|------|------|
| Hadoop | 2.7+/3.x | Yarn集群 + HDFS |
| Spark | 2.4+/3.x | 分布式计算引擎 |
| Azkaban | 3.90.0 | 调度引擎 |
| 可用Yarn队列 | - | 提交任务的目标队列（如`default`、`etl`） |

**前置条件**：确保Azkaban服务器能访问Yarn ResourceManager（默认8032端口）且配置了`HADOOP_CONF_DIR`。

### 3.2 分步实现

#### 步骤1：配置Azkaban与Hadoop集成

**目标**：让Azkaban能连接Yarn集群。

在Azkaban的配置文件`azkaban.properties`中添加：

```properties
# hadoop配置目录（指向core-site.xml, yarn-site.xml, hdfs-site.xml所在目录）
azkaban.hadoop.conf.dir=/etc/hadoop/conf

# 支持HadoopJob的插件
azkaban.jobtype.plugin.dir=plugins/jobtypes

# 启用Hadoop相关Job类型
azkaban.use.hadoop.jobtype=true
```

**环境变量检查**：

```bash
# 验证Azkaban能访问Hadoop
export HADOOP_CONF_DIR=/etc/hadoop/conf
hadoop fs -ls / 2>/dev/null && echo "Hadoop accessible" || echo "Hadoop NOT accessible"
```

#### 步骤2：编写MapReduce Job配置

**目标**：使用hadoopJava类型提交MapReduce作业。

首先准备一个简单的WordCount MapReduce程序（以jar形式），然后编写Job文件：

```bash
# hadoop_wordcount.job
type=hadoopJava
job.class=com.example.WordCount
classpath=./wordcount.jar,./lib/*
main.args=/user/hive/warehouse/ods/user_log/dt=2025-01-15,/tmp/wordcount_output
force.output.overwrite=true

# Hadoop参数
hadoop.job.ugi=etl_user
mapred.map.tasks=20
mapred.reduce.tasks=5
mapred.job.queue.name=etl

# 失败重试
retries=1
retry.backoff=120000
```

**参数说明**：
- `type=hadoopJava`：声明这是一个Hadoop Java作业
- `job.class`：Main类的全限定名
- `classpath`：jar包路径，多个用逗号分隔
- `main.args`：传递给main方法的参数
- `force.output.overwrite=true`：输出目录存在时覆盖

#### 步骤3：编写Spark Job配置

**目标**：使用spark类型提交Spark作业。

```bash
# spark_etl.job
type=spark
spark.master=yarn
spark.deploy.mode=cluster
spark.class=com.example.LogCleaner
spark.jars=spark-etl.jar,postgresql-42.2.5.jar
spark.driver.memory=4g
spark.driver.cores=2
spark.executor.memory=8g
spark.executor.cores=4
spark.num.executors=10
spark.yarn.queue=etl
spark.conf.spark.sql.adaptive.enabled=true
spark.conf.spark.dynamicAllocation.enabled=false

# 传给main方法的参数
spark.args=input_path=/data/logs/dt=2025-01-15
spark.args.1=output_path=/user/hive/warehouse/dwd/user_log_clean
spark.args.2=date=2025-01-15

# Hadoop属性
hadoop.job.ugi=etl_user

retries=1
retry.backoff=300000
```

**关键区别**：
- `spark.deploy.mode=cluster`：Driver运行在Yarn集群上（Azkaban只监控状态）
- `spark.deploy.mode=client`（默认）：Driver运行在Azkaban Executor进程中（较少用）

#### 步骤4：创建带依赖的Flow

**目标**：将Hadoop MR和Spark Job编排成一个完整的ETL Flow。

```bash
# data_pipeline.flow
nodes=hadoop_wordcount,spark_etl
```

```bash
# spark_etl.job 中增加依赖
dependsOn=hadoop_wordcount
```

**执行逻辑**：
```
hadoop_wordcount (MR: 日志清洗)
        ↓
spark_etl (Spark: 数据加工)
```

#### 步骤5：监控Yarn Application生命周期

**目标**：通过日志观察专用Job类型如何监控Yarn状态。

在执行`hadoop_wordcount`时，Azkaban日志会输出：

```
[INFO] Submitting hadoopJava job: hadoop_wordcount
[INFO] Application submitted: application_1705312345678_0001
[INFO] Application state: ACCEPTED
[INFO] Application state: RUNNING
[INFO] Map 0% Reduce 0%
[INFO] Map 50% Reduce 0%
[INFO] Map 100% Reduce 30%
[INFO] Map 100% Reduce 100%
[INFO] Application completed successfully: application_1705312345678_0001
```

```bash
# 在Azkaban服务器上用Yarn命令交叉验证
yarn application -list | grep application_170531
# 输出：application_1705312345678_0001  etl_user  SPARK  default  RUNNING  ...
```

#### 步骤6：处理Yarn队列资源不足

**目标**：配置队列优先级和等待策略。

```bash
# spark_with_queue_strategy.job
type=spark
# ... 省略Spark配置 ...

# 队列策略
spark.yarn.queue=etl

# 多个备选队列（Azkaban 3.80+支持）
spark.yarn.queue=etl,default,data_pipeline

# 最大等待时间（队列满时等待多久）
spark.yarn.queue.max.wait=600000
```

**常见队列问题处理**：

| 错误信息 | 原因 | 解决方案 |
|---------|------|---------|
| `Queue xxx has 0% capacity` | 队列无容量 | 切换到其他队列 |
| `Application is added to the scheduler and is not yet activated` | 队列资源繁忙 | 等待或增加资源 |
| `Failed to submit application to YARN` | RM不可达 | 检查网络和RM状态 |

### 3.3 完整代码清单

完整的ETL项目结构：

```
etl_hadoop_spark/
├── data_pipeline.flow          # Flow定义
├── hadoop_wordcount.job        # MapReduce Job配置
├── spark_etl.job               # Spark Job配置
├── jars/
│   ├── wordcount.jar           # MapReduce程序包
│   └── spark-etl.jar           # Spark程序包
└── lib/
    └── postgresql-42.2.5.jar   # 依赖包
```

Git仓库：`https://github.com/your-org/azkaban-flows/etl_hadoop_spark`

### 3.4 测试验证

```bash
#!/bin/bash
# verify_hadoop_spark.sh

echo "=== Hadoop/Spark Job验证 ==="

# 1. 验证Hadoop连接性
echo "[Test 1] 检查Hadoop环境..."
if hadoop version > /dev/null 2>&1; then
    echo "  [PASS] Hadoop CLI可用"
else
    echo "  [FAIL] Hadoop CLI不可用"
fi

# 2. 验证Yarn可访问
echo "[Test 2] 检查Yarn ResourceManager..."
if yarn node -list > /dev/null 2>&1; then
    echo "  [PASS] Yarn ResourceManager可访问"
else
    echo "  [FAIL] Yarn不可访问"
fi

# 3. 验证Spark可用
echo "[Test 3] 检查Spark..."
if spark-submit --version > /dev/null 2>&1; then
    echo "  [PASS] Spark可用"
else
    echo "  [FAIL] Spark不可用"
fi

# 4. 提交测试Job
echo "[Test 4] 提交Hadoop WordCount测试..."
curl -b cookies.txt \
  -X POST "http://localhost:8081/executor?ajax=executeFlow" \
  --data "project=etl_hadoop_spark&flow=data_pipeline"

# 5. 监控执行状态
echo "[Test 5] 监控Yarn Application..."
sleep 15
yarn application -list -appStates RUNNING,ACCEPTED 2>/dev/null | grep azkaban

echo "=== 验证完成 ==="
```

## 4. 项目总结

### Hadoop/Spark Job类型对比

| 维度 | command + spark-submit | hadoopJava | spark |
|------|----------------------|------------|-------|
| 提交复杂度 | ★★☆ 手写命令 | ★☆☆ 配参数 | ★☆☆ 配参数 |
| 状态追踪 | ★☆☆ 只知提交状态 | ★★★ 全程追踪 | ★★★ 全程追踪 |
| Yarn日志回传 | ★☆☆ 需手动查找 | ★★★ 自动关联 | ★★★ 自动关联 |
| 集群配置管理 | ★☆☆ 散落脚本中 | ★★☆ 集中配置 | ★★★ 集中配置 |
| 队列调度感知 | ★☆☆ 无 | ★★☆ 基础感知 | ★★★ 多队列感知 |

### 适用场景

- **适用**：Hadoop MapReduce批处理、Spark SQL/MLlib/Streaming作业、需要精确Job状态追踪的生产环境、多团队共享Yarn集群的协作场景
- **不适用**：只用本地模式的Spark任务、非Yarn集群（如K8s + Spark Operator）、Flink等非Hadoop生态的计算引擎

### 注意事项

- `hadoopJava`和`spark`类型依赖Azkaban服务器上配置的`HADOOP_CONF_DIR`，务必指向正确的集群配置
- `spark.deploy.mode=cluster`时，Driver运行在Yarn上，Azkaban服务器的Spark客户端版本需与集群一致
- 专用Job类型会在Azkaban进程中加载Hadoop客户端库，有JVM级别的类冲突风险
- Yarn Application会自动清理，但失败的Application会保留一段时间，注意磁盘监控

### 常见踩坑经验

1. **NoSuchMethodError / ClassNotFoundException**：Azkaban自带的Hadoop客户端库与集群版本不一致。解决：将集群的Hadoop jars复制到Azkaban的`extlib/`目录，或在`azkaban.properties`中配置`azkaban.extra.lib.dir`。
2. **Yarn队列被拒绝**：默认提交到`default`队列，但该队列已禁用或已满。解决：在Job中明确指定`hadoop.job.queue.name=etl`或`spark.yarn.queue=etl`。
3. **OOM: Java heap space**：Spark Driver内存不够（默认1G），处理大数据时OOM。解决：设置`spark.driver.memory=4g`；如果是client模式，还需调整Azkaban Executor的JVM堆大小。

### 思考题

1. 如果Azkaban与Yarn集群之间的网络不稳定（偶发断连），`hadoopJava`类型的Job会如何处理？你认为当前默认的30秒轮询机制在这种网络环境下有哪些不足？
2. 在一个共享Yarn集群中，多个Azkaban实例同时提交任务，如何做到"每个实例最多占用集群资源的30%"，防止某个实例的任务"吃光"集群资源？
