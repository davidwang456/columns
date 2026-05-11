# 第17章：Spark与MapReduce任务节点深度实战

> **定位**：从cron脚本到企业级大数据调度——用Spark与MapReduce节点构建可观测、可调优的生产级计算任务。
> **核心内容**：Spark节点配置全解（程序类型/主类/JAR/资源参数/部署模式）、MapReduce节点对比实战、YARN集成与ApplicationId追踪、Spark on K8s支持、Worker端JAR下载机制、峰值数据天的动态资源调优。
> **实战目标**：为大麦电商构建一套完整的Spark ETL工作流（检验分区 → Spark清洗聚合 → SQL校验结果），并横向对比MapReduce实现相同逻辑的差异。

---

## 1. 项目背景

大麦电商的数据团队维护着一套Spark ETL夜间作业，每天凌晨2点处理500GB原始订单数据——清洗脏数据、关联用户维度表、聚合成每日经营指标（GMV、客单价、复购率、品类分布），最终写入数仓DWD层供下游报表消费。

这套作业的现状令人揪心：一个cron定时任务挂在边缘节点上，凌晨2点触发一条`spark-submit`命令。执行成功了没人知道，执行失败了也没人知道——往往是第二天早会打开报表，发现数据还是昨天的，才顺藤摸瓜查到凌晨的Spark任务OOM崩溃了。更糟糕的是，团队没有任何历史执行记录，想回答"这个任务过去30次跑了多久"都无从查起。每逢黑色星期五，流量暴涨导致原始数据量飙到正常的10倍（约2TB），Spark任务运行时长从2小时膨胀到6小时，直接与第二天的执行窗口重叠——两个Spark-submit同时向YARN申请资源，集群资源瞬间被挤爆。

CTO下死命令：必须把Spark作业迁移到DolphinScheduler统一调度，做到三件事——(1) 将Spark任务编排为完整数据管道的一个环节（上游依赖分区检查，下游串联质量校验），(2) 任务失败后5分钟内钉钉告警，不用等第二天才被动发现，(3) 针对峰值数据天（黑五、618、双11）自动适配资源配额，避免任务重叠和集群雪崩，(4) 建立从原始数据到最终报表的完整血缘追踪。

---

## 2. 项目设计——剧本式交锋对话

**场景**：技术讨论区，小胖刚把上一章的SubProcess工作流部署上线，自信满满。大师在白板上写下"Spark"和"MapReduce"两个词，下面画了一个YARN集群的方框。

**小胖**（翘着二郎腿，一脸轻松）：

> "Spark任务不就是spark-submit一行命令嘛！把JAR上传到资源中心，在DS里拖个Spark节点，填上主类名和JAR路径，搞定。我上周在自己的开发机上就这么跑通的——有什么难的？MapReduce更简单，hadoop jar一行命令，main class填上就完事。"

**小白**（放下手中的咖啡，眉头紧锁，一连串追问）：

> "胖哥你这属于'跑通了就等于学会了'。我问你六个问题：第一，driver-memory和executor-memory怎么配？给少了OOM，给多了浪费YARN队列资源，别的团队等着排队呢。第二，cluster模式和client模式选哪个？client模式下driver跑在Worker节点上，Worker重启driver就没了，你考虑过吗？第三，如果YARN队列满了，Spark任务提交不上去怎么办？是无限重试还是直接失败？第四，Spark 2.x和3.x的配置项有差异——比如3.x用spark.sql.adaptive.enabled默认开启自适应查询，你提交命令的时候会不会跟2.x的固定参数冲突？第五，Python Spark任务——pyspark的依赖包怎么处理？UDF里引用了pandas和numpy，Worker节点上看不到这些包怎么办？第六，也是最重要的——Spark任务跑起来之后，你怎么知道它在YARN上的ApplicationId？想查日志是去YARN ResourceManager还是去DS的日志页？"

**小胖**的笑容逐渐凝固，递过薯片的手停在了半空中。

**大师**（接过薯片，在白板上画了一条完整的执行路径）：

> "小白的六个问题，个个都是生产环境的必修课。我先画一张图，你们看着图听我说。"

> "Spark任务在DolphinScheduler里的完整执行路径是：**定时触发 → Master生成Command写入数据库 → Worker争抢任务 → Worker从资源中心下载JAR到本地工作目录 → 拼接spark-submit命令行 → 提交到YARN → 监控YARN状态 → 回调结果给Master**。这条链路上的每一步都可能出错，而'把JAR上传到资源中心'只是第二步。真正考验人的，是后面五步的容错、监控和调优。"

> "想象一下：Spark任务就是一个'大厨'，DolphinScheduler的角色是'餐厅调度台'。调度台告诉大厨（Worker）——凌晨2点该做菜了，食材（JAR）在冷库（资源中心）里，你去取。大厨取到食材后，跑到YARN厨房里找到灶台（Executor），开始烹饪。烹饪过程中调度台通过YARN的前台系统（yarn-aop模块）随时查看'这盘菜做到哪一步了'（ApplicationId追踪）。如果灶台坏了（Executor丢失），大厨会自动换一个灶台（Spark容错）。如果厨房打烊了（YARN队列满），调度台会通知值班人员（告警）。"

> **技术映射**：Worker收到Spark任务后，会先调用资源中心API下载JAR到`{execution_work_dir}/{task_instance_id}/`目录，然后拼接命令：`spark-submit --class com.damai.etl.SalesETL --master yarn --deploy-mode cluster --driver-memory 2G --executor-memory 4G --num-executors 4 --executor-cores 2 {local_jar_path} {args}`。DS内置的`dolphinscheduler-task-spark`模块负责封装这个过程。yarn-aop模块通过正则匹配spark-submit标准输出中的`Submitted application application_xxx`来捕获ApplicationId，并写入任务日志，后续可通过该ID在YARN ResourceManager上精确追踪任务。

**小白**（快速记完笔记，抬起头）：

> "那Spark on Kubernetes呢？我们团队明年要切K8s，DS支持吗？"

**大师**：

> "支持。DS的Spark节点不限制底层的资源管理器——你在spark-submit命令里把`--master yarn`改成`--master k8s://https://k8s-api-server:6443`，再配上K8s的认证信息（通过spark.kubernetes.authenticate.driver.serviceAccountName等参数），DS就会把任务提交到K8s集群上跑。核心原理不变——DS负责触发和监控，YARN/K8s负责资源调度和任务执行。对DS来说，它们都是spark-submit后面的一个`--master`参数而已。"

**小胖**（终于缓过神来）：

> "那MapReduce节点呢？同样是跑JAR，跟Spark有啥本质区别？"

**大师**：

> "本质区别就一句话：**Spark是内存计算引擎，MapReduce是磁盘迭代引擎**。MapReduce节点在DS里的配置跟Spark类似——填主类、填JAR路径、填程序参数——但底层执行的是`hadoop jar`命令而非`spark-submit`。MapReduce的每一步Shuffle都写磁盘，所以处理TB级数据时比Spark慢一个数量级。但它有一个好处：稳定性极高。一个跑了十年的MapReduce程序，在没有任何调优的情况下换到新集群上，照样跑得通。Spark任务换个版本、换个Hive元数据、换个序列化方式，都可能出状况。所以我对企业客户的建议是：**新业务首选Spark，存量稳定业务保留MapReduce，不要为了迁移而迁移，除非业务对时效有明确要求。**"

---

## 3. 项目实战

### 环境准备

- DolphinScheduler 3.x 集群模式已部署，至少一个Worker节点在线
- 确保Worker节点安装了Spark 3.x和Hadoop（需配置`SPARK_HOME`和`HADOOP_HOME`环境变量）
- YARN集群正常运行，已创建专用队列`root.damai_etl`
- 资源中心已启用（参考第12章）

### Step 1：编写Spark ETL应用程序

```java
// SalesETL.java
import org.apache.spark.sql.SparkSession;
import org.apache.spark.sql.Dataset;
import org.apache.spark.sql.Row;

public class SalesETL {
    public static void main(String[] args) {
        String bizDate = args[0]; // 业务日期，如 2024-01-15
        SparkSession spark = SparkSession.builder()
            .appName("SalesETL_" + bizDate)
            .enableHiveSupport()
            .getOrCreate();

        // 读取ODS层原始订单
        Dataset<Row> orders = spark.sql(
            "SELECT * FROM ods.orders WHERE dt='" + bizDate + "'"
        );

        // 清洗：去重 + 过滤无效数据
        Dataset<Row> cleaned = orders
            .dropDuplicates("order_id")
            .filter("amount > 0 AND user_id IS NOT NULL");

        // 写入DWD层
        cleaned.write
            .mode("overwrite")
            .partitionBy("dt")
            .saveAsTable("dwd.daily_orders");

        System.out.println("SalesETL finished. Rows: " + cleaned.count());
        spark.stop();
    }
}
```

### Step 2：构建JAR并上传到资源中心

```bash
mvn clean package -DskipTests
# 构建产物：target/sales-etl-1.0.jar
```

登录DolphinScheduler →【资源中心】→【创建目录】`/spark_jars/` → 上传`sales-etl-1.0.jar`到该目录。

> **注意**：JAR文件名必须与代码中的配置完全一致，包括大小写。资源中心的路径是大小写敏感的。

### Step 3：创建Spark任务节点

进入项目 →【工作流定义】→ 拖入【Spark】节点。关键配置如下：

| 配置项 | 值 | 说明 |
|--------|-----|------|
| 程序类型 | JAVA | 支持JAVA、SCALA、PYTHON三种 |
| 主类 | com.damai.etl.SalesETL | 必须是全限定类名 |
| 主程序包 | resource://spark_jars/sales-etl-1.0.jar | resource://前缀指向资源中心 |
| 主程序参数 | ${biz_date} | 业务日期，由全局参数传入 |
| Spark版本 | SPARK3 | 可选SPARK1、SPARK2、SPARK3 |
| 部署模式 | cluster | Driver运行在YARN AM中，Worker节点重启不影响 |
| Driver-核数 | 1 | 默认即可，不参与数据计算 |
| Driver-内存 | 2G | 需考虑collect()回传数据量 |
| Executor-数量 | 4 | 根据数据量和集群资源规划 |
| Executor-核数 | 2 | 建议2-4核，过高导致HDFS吞吐瓶颈 |
| Executor-内存 | 4G | 不含overhead（约=max(384M, 0.1×4G)=400M） |
| 主程序参数 | ${biz_date} | 传递给main(String[] args) |
| 其他参数 | --conf spark.yarn.queue=root.damai_etl | 指定YARN队列 |

### Step 4：构建完整ETL工作流DAG

工作流名称：`damai_daily_sales_etl`

节点编排（线性依赖）：

```
[check_partition] → [sales_etl] → [validate_output]
```

- **check_partition**（Shell节点）：验证上游ODS分区是否存在
  ```bash
  #!/bin/bash
  PARTITION_EXISTS=$(hive -e "SHOW PARTITIONS ods.orders" | grep "dt=${biz_date}" | wc -l)
  if [ "$PARTITION_EXISTS" -eq 0 ]; then
      echo "ERROR: 分区 dt=${biz_date} 不存在，终止流程"
      exit 1
  fi
  echo "分区校验通过: dt=${biz_date}"
  ```

- **sales_etl**（Spark节点）：上述配置的主ETL任务
- **validate_output**（SQL节点）：校验输出结果
  ```sql
  SELECT COUNT(*) AS row_count FROM dwd.daily_orders WHERE dt='${biz_date}'
  ```

> 配置全局参数`biz_date`，默认值为`${system.biz.date}`（DS内置变量，代表当前业务日期）。

### Step 5：创建MapReduce对照任务

```java
// LogAnalyzer.java —— 日志级别统计
import org.apache.hadoop.conf.Configuration;
import org.apache.hadoop.fs.Path;
import org.apache.hadoop.io.IntWritable;
import org.apache.hadoop.io.LongWritable;
import org.apache.hadoop.io.Text;
import org.apache.hadoop.mapreduce.Job;
import org.apache.hadoop.mapreduce.Mapper;
import org.apache.hadoop.mapreduce.Reducer;

public class LogAnalyzer {
    public static class LogMapper
            extends Mapper<LongWritable, Text, Text, IntWritable> {
        private final static IntWritable ONE = new IntWritable(1);
        private Text level = new Text();

        public void map(LongWritable key, Text value, Context ctx)
                throws java.io.IOException, InterruptedException {
            String line = value.toString();
            if (line.contains("ERROR")) {
                level.set("ERROR");
                ctx.write(level, ONE);
            } else if (line.contains("WARN")) {
                level.set("WARN");
                ctx.write(level, ONE);
            }
        }
    }

    public static class LogReducer
            extends Reducer<Text, IntWritable, Text, IntWritable> {
        public void reduce(Text key, Iterable<IntWritable> vals, Context ctx)
                throws java.io.IOException, InterruptedException {
            int sum = 0;
            for (IntWritable v : vals) sum += v.get();
            ctx.write(key, new IntWritable(sum));
        }
    }

    public static void main(String[] args) throws Exception {
        Configuration conf = new Configuration();
        Job job = Job.getInstance(conf, "LogAnalyzer");
        job.setJarByClass(LogAnalyzer.class);
        job.setMapperClass(LogMapper.class);
        job.setReducerClass(LogReducer.class);
        job.setOutputKeyClass(Text.class);
        job.setOutputValueClass(IntWritable.class);
        // args[0]=输入路径, args[1]=输出路径
        org.apache.hadoop.mapreduce.lib.input.FileInputFormat
            .addInputPath(job, new Path(args[0]));
        org.apache.hadoop.mapreduce.lib.output.FileOutputFormat
            .setOutputPath(job, new Path(args[1]));
        System.exit(job.waitForCompletion(true) ? 0 : 1);
    }
}
```

MapReduce任务节点配置：

| 配置项 | 值 |
|--------|-----|
| 程序类型 | JAVA |
| 主类 | com.damai.etl.LogAnalyzer |
| 主程序包 | resource://mr_jars/log-analyzer-1.0.jar |
| 程序参数 | /input/logs/${biz_date} /output/logs/${biz_date} |

### Step 6：运行与验证

1. 选择一个测试日期（如`2024-01-15`），在工作流定义页点击【运行】，填写参数`biz_date=2024-01-15`。
2. 观察执行过程：
   - **check_partition**：日志中应输出"分区校验通过"
   - **sales_etl**：点击节点→【查看日志】，确认Worker已成功从资源中心下载JAR，spark-submit命令已执行。关键日志行：`Submitted application application_1705276800000_0001`——这就是YARN ApplicationId
   - **validate_output**：确认输出行数>0
3. 登录YARN ResourceManager Web UI，用ApplicationId搜索该任务，确认资源使用量（vCores、Memory）与配置一致。

### Step 7：峰值数据天动态资源调优

针对黑色星期五等数据量暴增场景，使用**Conditions条件节点**实现动态路由：

```
[check_data_volume] → {条件分支}
                      ├─ 数据量 ≤ 500GB → [sales_etl_normal]  (executor: 4核/4G×4)
                      └─ 数据量 > 500GB → [sales_etl_bigday]  (executor: 8核/8G×8)
```

`check_data_volume`（Shell节点）输出数据量标记：
```bash
#!/bin/bash
VOLUME_GB=$(hive -e "SELECT ROUND(SUM(size)/1024/1024/1024,0) FROM (SELECT SUM(size) AS size FROM ods.orders WHERE dt='${biz_date}') t" 2>/dev/null)
echo "数据量: ${VOLUME_GB}GB"
if [ "$VOLUME_GB" -gt 500 ]; then
    echo '${setVar=IS_BIG_DAY=true}'
else
    echo '${setVar=IS_BIG_DAY=false}'
fi
```

Conditions节点条件：`IS_BIG_DAY == true` → 走大配置分支，`IS_BIG_DAY == false` → 走常规配置分支。

### Step 8：常见踩坑与解决方案

| 问题 | 根因 | 解决 |
|------|------|------|
| JAR文件找不到：`java.io.FileNotFoundException` | 资源中心路径与任务配置不匹配（如大小写/多余空格） | 严格复制资源中心显示的完整路径，`resource://`前缀不可省略 |
| `ClassNotFoundException: com.damai.etl.SalesETL` | 主类配置名与JAR中实际包路径不一致 | 用`jar tf sales-etl-1.0.jar \| grep SalesETL`确认类的完整路径 |
| YARN队列不存在：`Failed to submit app to queue` | 指定的YARN队列未创建或名称拼写错误 | 在YARN上执行`yarn queue -show`验证队列名，或在"其他参数"中修正`--conf spark.yarn.queue` |
| spark-submit命令找不到：`spark-submit: command not found` | Worker节点未配置SPARK_HOME环境变量 | 在`dolphinscheduler_env.sh`中添加`export SPARK_HOME=/opt/spark`，重启Worker |
| Executor内存溢出：`OutOfMemoryError: Java heap space` | executor-memory配置不足或数据倾斜导致部分分区数据量远超平均值 | 增大executor-memory，同时对倾斜Key加盐打散（`concat(key, '_', rand()*10)`） |
| Python Spark：`ModuleNotFoundError: No module named 'pandas'` | Worker节点未安装Python依赖 | 方案一：使用`--py-files`上传依赖包；方案二：通过`--archives`指定Conda环境压缩包；方案三：在Worker节点全局安装 |
| Worker下载JAR超时 | 资源中心文件过大或Worker到资源中心网络延迟高 | 增加Worker配置项`resource.download.timeout`，或对大JAR提前分发到各Worker本地路径 |
| Driver OOM但Executor空闲 | cluster模式下Driver在AM中运行，collect()将大量数据拉回Driver导致OOM | 避免在Driver端调用collect()，改用write直接写存储，或在client模式下增大Driver内存 |

---

## 4. 项目总结

### Spark vs MapReduce 任务对比

| 维度 | Spark节点 | MapReduce节点 |
|------|----------|--------------|
| 底层命令 | spark-submit | hadoop jar |
| 计算模型 | 内存迭代（RDD/DAG） | 磁盘迭代（Map→Shuffle→Reduce） |
| 处理速度 | TB级数据分钟到小时级 | TB级数据小时级 |
| 容错机制 | RDD血缘重算 + Checkpoint | Task级别重试 |
| 编程模型 | Java/Scala/Python，支持SQL/DataFrame/Streaming | 仅Java，Map+Reduce两阶段 |
| 适用场景 | ETL、机器学习、实时流处理 | 存量离线批处理、简单聚合统计 |
| DS配置复杂度 | 较高（内存/核数/版本/部署模式） | 较低（主类+JAR+参数即可） |

### YARN vs K8s 作为执行后端

- **YARN**：Hadoop生态原生支持，与HDFS数据本地性结合最好。适合已有Hadoop集群的传统企业，运维成熟度高。
- **Kubernetes**：容器化部署，资源隔离粒度更细。Spark 3.x原生支持K8s（不依赖YARN），适合云原生架构和混合云场景。
- **选择建议**：如果数据在HDFS上且团队熟悉Hadoop运维，优先YARN；如果公司整体向K8s迁移且数据在对象存储（S3/MinIO），优先K8s。

### 资源调优核心原则

1. **executor-memory不只是给计算用的**——40%用于Shuffle/Join时的内存缓存，30%用于用户数据结构，30%预留。如果executor-memory=4G，实际可用于缓存的内存约1.6G。
2. **executor-cores建议不超过5**——HDFS客户端对单个DataNode的并发读取有限制，核数过多会导致I/O线程竞争，吞吐量不升反降。
3. **动态资源分配优先于静态配置**——开启`spark.dynamicAllocation.enabled=true`，让Spark根据任务负载自动增减executor，避免峰值天手动改配置的滞后性。
4. **小文件问题前置解决**——在Spark任务开始前增加一步`COALESCE`或`REPARTITION`，避免产出大量小文件拖垮下游Hive查询。

### 思考题

1. 某天凌晨，`damai_daily_sales_etl`工作流的Spark节点报错：`Initial job has not accepted any resources; check your cluster UI to ensure that workers are registered`。但同时段其他团队的Spark任务能正常提交。请分析可能的根因（至少三种），并给出排查步骤。

2. 大麦电商计划将Spark任务从YARN迁移到Kubernetes。迁移后，原本运行2小时的Spark ETL任务变成了4小时。请从数据本地性、网络带宽、序列化开销三个角度分析性能下降的可能原因，并提出优化方案。

---

> **下一章预告**：第18章《DataX数据同步节点与多源异构集成》将深入讲解如何用DS的DataX节点打通MySQL、Hive、HDFS、Elasticsearch等异构数据源之间的数据通道，构建企业级数据集成管道。
