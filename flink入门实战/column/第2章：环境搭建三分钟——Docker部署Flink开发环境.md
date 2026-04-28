# 第2章：环境搭建三分钟——Docker部署Flink开发环境

---

## 1. 项目背景

"我照着文档配了一上午了还没跑起来！"

这是很多Flink新手的第一声哀嚎。手动安装Flink流程看似简单：下载二进制包 → 解压 → 改配置 → 启动。但落到真实开发环境，问题如雨后春笋般冒出：

- **版本海啸**：Flink 1.17和1.18的默认配置项不一样，Kafka Connector有scala版本后缀，Hadoop依赖版本不匹配直接ClassNotFound
- **环境不一致**：Windows开发机 vs Linux服务器 vs 同事的Mac，每个人跑出来的结果差之千里。Windows上Flink的路径分隔符问题、WSL性能损耗、端口映射混乱
- **依赖地狱**：Flink自带的log4j和项目引入的logback打起来；flink-shaded-hadoop和CDH自带的Hadoop冲突；scala版本2.11 vs 2.12 vs 2.13，一个不对就报LinkageError
- **上下游难搭**：开发一个Kafka→Flink→MySQL作业，先要装Kafka（配ZooKeeper）、装MySQL（建表配权限）、装Flink，三套配置三套排错

一个新人要花整整一个白天才能点亮"本地HelloWorld"的成就，而团队里每个成员都要重复经历一遍。版本升级一次，全组跟着重配一遍。效率损失肉眼可见。

**Docker容器化** 给出了优雅的解法：将Flink、Kafka、MySQL等组件全部打包进容器，通过`docker-compose up -d`一条命令拉起整个开发环境。环境即代码，一次编写，全员复用。

---

## 2. 项目设计

> 场景：新人入职第三天，在手动安装Flink失败第二次后，瘫在工位上。

**小胖**（抱着一桶薯片路过）：哟，咋了？对着屏幕发啥呆呢？

**小白**：我按照官方文档装Flink，先下了二进制包，配了HADOOP_CLASSPATH，改了flink-conf.yaml，start-cluster.sh倒是跑起来了，但我写了个SocektWordCount，提交作业之后TaskManager日志里报"java.lang.NoClassDefFoundError: org/apache/flink/XXX"。我又去下jar包，版本还不对。

**大师**（路过听到）：你遇到的是典型的"手动安装的脆弱性"问题。每个人机器的JDK版本、PATH变量、已安装的scala版本、系统临时目录权限都不相同，这些隐性差异累积起来，调试成本极高。**技术映射：这暴露了"环境依赖 = 不可控全局变量"的问题——你的业务逻辑本身没错，错的是运行环境的隐式假设。**

**小胖**：那咋整？总不能大家都用同一台机器开发吧？

**大师**：用Docker。把Flink、Kafka、MySQL全部写在一个docker-compose.yml里，一条命令拉起全部服务。你的开发机只需要装Docker Desktop和IDEA，剩下的事情交给镜像。

**小白**：Docker我知道，但Flink在Docker里跑，我代码怎么提交？怎么断点调试？数据怎么持久化？总不能每次重启数据都没了吧？

**大师**：三个问题，三个答案。第一，代码提交用Volume挂载——把本地的jar包或代码目录映射进容器，免去手动copy。第二，Flink 1.18之后支持Session集群模式配合IDEA Remote Debug，你可以在容器外打断点调试容器内的TaskManager。第三，数据持久化通过Docker Volumn将RocksDB状态目录、Checkpoint目录映射到宿主机，重启数据不丢。

**小胖**：明白了！Docker就像给每个服务套了一层保护壳，壳里啥版本都配好了，壳外我随便折腾。**技术映射：容器 = 进程级虚拟化，通过cgroup做资源隔离，通过namespace做文件系统隔离，每个容器只对外暴露必要的端口。**

**小白**：那具体怎么做？我写了docker-compose.yml，但Flink容器和Kafka容器之间怎么通信？同一台宿主机上还好说，那如果跨机器呢？

**大师**：Docker Compose会默认创建一个bridge网络，所有service通过服务名相互发现。比如你的Flink代码里配置bootstrap.servers = kafka:9092，Compose的DNS解析会自动把kafka这个主机名映射到Kafka容器的IP。跨机器场景可以改用Docker Swarm或Kubernetes，那是中级篇的内容。

现在动手——我带你三分钟搭好一套完整的Flink+Kafka+MySQL开发环境。

---

## 3. 项目实战

### 环境准备

| 组件 | 版本 | 用途 |
|------|------|------|
| Docker Desktop | 4.27+ | 容器运行环境 |
| Docker Compose | v2.24+ | 多容器编排 |
| IntelliJ IDEA | 2023+ | 开发IDE |
| apache/flink | 1.18-scala_2.12 | Flink镜像 |
| confluentinc/cp-kafka | 7.6.0 | Kafka消息队列 |
| mysql | 8.0 | 关系型数据库 |

> **坑位预警**：Windows用户请确保Docker Desktop使用WSL 2 backend。在Settings → General中勾选"Use WSL 2 based engine"。如果总内存低于16GB，建议在Resources中将内存限制调整为4GB，避免Flink容器OOM。

### 分步实现

#### 步骤1：创建项目目录结构

**目标**：建立标准化的Flink开发项目骨架。

```
flink-workspace/
├── docker-compose.yml          # 环境编排
├── flink-conf/
│   └── flink-conf.yaml         # Flink自定义配置（可选覆盖）
├── sql/
│   └── init.sql                # MySQL初始化DDL
├── jobs/                       # 存放编译好的jar包
│   └── flink-practitioner.jar
└── data/                       # 持久化数据目录
    ├── kafka-data/
    ├── mysql-data/
    └── flink-checkpoints/
```

```bash
mkdir -p flink-workspace/{flink-conf,sql,jobs,data/{kafka,mysql,flink-checkpoints}}
```

#### 步骤2：编写docker-compose.yml

**目标**：用Compose定义Flink集群（1 JobManager + 2 TaskManager）+ Kafka + MySQL。

```yaml
version: '3.8'

networks:
  flink-net:
    driver: bridge

services:
  # ------------------------------------------------------------
  # ZooKeeper（Kafka依赖）
  # ------------------------------------------------------------
  zooKeeper:
    image: confluentinc/cp-zookeeper:7.6.0
    hostname: zookeeper
    container_name: flink-zk
    environment:
      ZOOKEEPER_CLIENT_PORT: 2181
      ZOOKEEPER_SERVER_ID: 1
    networks: [flink-net]
    ports:
      - "2181:2181"

  # ------------------------------------------------------------
  # Kafka
  # ------------------------------------------------------------
  kafka:
    image: confluentinc/cp-kafka:7.6.0
    hostname: kafka
    container_name: flink-kafka
    depends_on: [zooKeeper]
    ports:
      - "9092:9092"
    environment:
      KAFKA_BROKER_ID: 1
      KAFKA_ZOOKEEPER_CONNECT: zookeeper:2181
      KAFKA_ADVERTISED_LISTENERS: PLAINTEXT://localhost:9092
      KAFKA_LISTENERS: PLAINTEXT://0.0.0.0:9092
      KAFKA_OFFSETS_TOPIC_REPLICATION_FACTOR: 1
    volumes:
      - ./data/kafka:/var/lib/kafka/data
    networks: [flink-net]

  # ------------------------------------------------------------
  # MySQL
  # ------------------------------------------------------------
  mysql:
    image: mysql:8.0
    hostname: mysql
    container_name: flink-mysql
    ports:
      - "3306:3306"
    environment:
      MYSQL_ROOT_PASSWORD: flink123
      MYSQL_DATABASE: flink_demo
    volumes:
      - ./data/mysql:/var/lib/mysql
      - ./sql/init.sql:/docker-entrypoint-initdb.d/init.sql
    networks: [flink-net]

  # ------------------------------------------------------------
  # Flink JobManager
  # ------------------------------------------------------------
  jobmanager:
    image: apache/flink:1.18-scala_2.12
    hostname: jobmanager
    container_name: flink-jm
    ports:
      - "8081:8081"     # WebUI
      - "6123:6123"     # JobManager RPC
    command: jobmanager
    environment:
      - JOB_MANAGER_RPC_ADDRESS=jobmanager
    volumes:
      - ./jobs:/jobs                        # 挂载jar包目录
      - ./flink-conf:/opt/flink/conf        # 可选：覆盖默认配置
      - ./data/flink-checkpoints:/checkpoints
    networks: [flink-net]

  # ------------------------------------------------------------
  # Flink TaskManager × 2
  # ------------------------------------------------------------
  taskmanager:
    image: apache/flink:1.18-scala_2.12
    hostname: taskmanager
    container_name: flink-tm
    depends_on: [jobmanager]
    command: taskmanager
    environment:
      - JOB_MANAGER_RPC_ADDRESS=jobmanager
      - TASK_MANAGER_NUMBER_OF_TASK_SLOTS=2
    volumes:
      - ./jobs:/jobs
      - ./data/flink-checkpoints:/checkpoints
    networks: [flink-net]
    deploy:
      replicas: 2    # 启动2个TaskManager副本
```

#### 步骤3：编写MySQL初始化脚本

**目标**：Flink启动时自动创建结果表，避免作业因表不存在而失败。

创建 `sql/init.sql`：

```sql
CREATE DATABASE IF NOT EXISTS flink_demo;
USE flink_demo;

-- 词频统计结果表
CREATE TABLE IF NOT EXISTS word_count (
    word      VARCHAR(128) NOT NULL PRIMARY KEY,
    cnt       BIGINT       NOT NULL DEFAULT 0,
    update_ts TIMESTAMP    DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);
```

#### 步骤4：一键启动环境

**目标**：用一条命令拉起所有容器并验证服务可用。

```bash
docker-compose up -d
```

启动过程输出：

```
[+] Running 7/7
 ✔ Container flink-zk     Started
 ✔ Container flink-mysql  Started
 ✔ Container flink-kafka  Started
 ✔ Container flink-jm     Started
 ✔ Container flink-tm-1   Started
 ✔ Container flink-tm-2   Started
```

验证各服务：

```bash
# 1. Flink WebUI
curl http://localhost:8081
# 预期：返回Flink Dashboard HTML页面，显示1个JobManager + 2个TaskManager + 4个Slot

# 2. Kafka可用性
docker exec flink-kafka kafka-topics --bootstrap-server localhost:9092 --list
# 预期：空列表（无报错）

# 3. MySQL可达
docker exec flink-mysql mysql -uroot -pflink123 -e "SHOW DATABASES;"
# 预期：显示flink_demo库
```

> **坑位预警**：如果Flink WebUI访问不到，检查Docker Desktop的端口映射是否被占用。`netstat -ano | findstr :8081` 看是否已被其他进程占用，改用 `ports: "8082:8081"` 做端口映射。

#### 步骤5：修改第1章的WordCount，提交到Docker集群

**目标**：将上一章的SocketWordCount改造为Kafka→Flink→MySQL，提交到容器集群运行。

创建 `KafkaToMySQLWordCount.java`：

```java
package com.flink.column.chapter02;

import org.apache.flink.api.common.functions.FlatMapFunction;
import org.apache.flink.api.java.tuple.Tuple2;
import org.apache.flink.connector.jdbc.JdbcConnectionOptions;
import org.apache.flink.connector.jdbc.JdbcSink;
import org.apache.flink.connector.kafka.source.KafkaSource;
import org.apache.flink.connector.kafka.source.enumerator.initializer.OffsetsInitializer;
import org.apache.flink.streaming.api.datastream.DataStream;
import org.apache.flink.streaming.api.environment.StreamExecutionEnvironment;
import org.apache.flink.util.Collector;

public class KafkaToMySQLWordCount {

    public static void main(String[] args) throws Exception {
        // 1. 执行环境（自动读取容器内的flink-conf.yaml）
        StreamExecutionEnvironment env = StreamExecutionEnvironment.getExecutionEnvironment();
        env.enableCheckpointing(10_000);  // 10秒一次Checkpoint

        // 2. Kafka Source（注意bootstrap.servers用容器服务名）
        KafkaSource<String> source = KafkaSource.<String>builder()
                .setBootstrapServers("kafka:9092")
                .setTopics("input-topic")
                .setGroupId("flink-wordcount")
                .setStartingOffsets(OffsetsInitializer.latest())
                .setValueOnlyDeserializer()
                .build();

        DataStream<String> text = env.fromSource(source, "kafka-source");

        // 3. 词频统计
        DataStream<Tuple2<String, Long>> counts = text
                .flatMap(new Tokenizer())
                .keyBy(t -> t.f0)
                .sum(1);

        // 4. MySQL Sink（幂等写入：INSERT ... ON DUPLICATE KEY UPDATE）
        counts.addSink(JdbcSink.sink(
                "INSERT INTO word_count(word, cnt) VALUES (?, ?) " +
                "ON DUPLICATE KEY UPDATE cnt = cnt + VALUES(cnt)",
                (ps, t) -> {
                    ps.setString(1, t.f0);
                    ps.setLong(2, t.f1);
                },
                new JdbcConnectionOptions.JdbcConnectionOptionsBuilder()
                        .withUrl("jdbc:mysql://mysql:3306/flink_demo")
                        .withDriverName("com.mysql.cj.jdbc.Driver")
                        .withUsername("root")
                        .withPassword("flink123")
                        .build()
        )).name("mysql-sink");

        // 5. 执行
        env.execute("Chapter02-KafkaToMySQLWordCount");
    }

    private static final class Tokenizer
            implements FlatMapFunction<String, Tuple2<String, Long>> {
        @Override
        public void flatMap(String line, Collector<Tuple2<String, Long>> out) {
            for (String word : line.toLowerCase().split("\\W+")) {
                if (!word.isEmpty()) {
                    out.collect(Tuple2.of(word, 1L));
                }
            }
        }
    }
}
```

**添加Maven依赖**（在原有pom.xml基础上追加）：

```xml
<dependency>
    <groupId>org.apache.flink</groupId>
    <artifactId>flink-connector-kafka</artifactId>
    <version>3.0.2-1.18</version>
</dependency>
<dependency>
    <groupId>org.apache.flink</groupId>
    <artifactId>flink-connector-jdbc</artifactId>
    <version>3.1.2-1.18</version>
</dependency>
<dependency>
    <groupId>mysql</groupId>
    <artifactId>mysql-connector-java</artifactId>
    <version>8.0.33</version>
</dependency>
```

编译打包：

```bash
mvn clean package -DskipTests
```

#### 步骤6：提交作业到Flink集群

**目标**：将打好的jar包提交到容器内的Flink Session集群。

```bash
# 1. 将jar包放入jobs目录
cp target/flink-practitioner.jar jobs/

# 2. 通过Flink WebUI提交：
#    http://localhost:8081 → Submit New Job → Add New → 选择jar → 输入主类名 → Submit

# 或者命令行提交（需要在容器内执行）：
docker exec flink-jm flink run -c com.flink.column.chapter02.KafkaToMySQLWordCount /jobs/flink-practitioner.jar
```

#### 步骤7：验证端到端流程

**目标**：生产数据到Kafka，验证Flink消费并写入MySQL。

```bash
# 1. 创建Kafkainput-topic
docker exec flink-kafka kafka-topics --bootstrap-server localhost:9092 \
  --create --topic input-topic --partitions 3 --replication-factor 1

# 2. 生产测试数据
docker exec -i flink-kafka kafka-console-producer --bootstrap-server localhost:9092 \
  --topic input-topic <<EOF
hello flink docker
hello world
flink stream processing
EOF

# 3. 查询MySQL结果
docker exec flink-mysql mysql -uroot -pflink123 -e \
  "SELECT * FROM flink_demo.word_count ORDER BY cnt DESC;"
```

**预期输出**：

```
+----------+-----+
| word     | cnt |
+----------+-----+
| hello    |   2 |
| flink    |   2 |
| world    |   1 |
| docker   |   1 |
| stream   |   1 |
| processing|   1 |
+----------+-----+
```

---

### 日常开发工作流汇总

```bash
# 启动环境
docker-compose up -d

# 编译代码
mvn clean package -DskipTests

# 复制jar
cp target/*.jar jobs/

# 提交作业（WebUI或CLI）
docker exec flink-jm flink run /jobs/your-job.jar

# 查看日志
docker-compose logs -f taskmanager

# 停环境（保留数据）
docker-compose down

# 停环境（清数据，慎用）
docker-compose down -v
```

### 可能遇到的坑

1. **Kafkaconnect refused**：Flink连不上Kafka，抛出`TimeoutException`
   - 排查三步走：① `docker exec flink-kafka kafka-topics --list` 确认Kafka活 ② 确认代码里bootstrap.servers用的是容器服务名`kafka:9092`而非`localhost:9092` ③ 检查Flink和Kafka是否在同一网络
2. **ClassNotFoundException: org.apache.flink.connector.jdbc**：缺JDBC connector依赖
   - 解决：确认pom.xml中已添加flink-connector-jdbc，且通过maven-shade-plugin打包进了fat jar
3. **MySQL "Table 'word_count' doesn't exist"**：表没有被自动创建
   - 解决：手动执行`docker exec flink-mysql mysql -uroot -pflink123 < sql/init.sql`，或检查init.sql是否挂载到docker-entrypoint-initdb.d

---

## 4. 项目总结

### 优点 & 缺点

| | Docker环境 | 手动安装（对比） |
|------|-----------|----------------|
| **优点1** | 一条命令拉起全部依赖 | 每个组件手动下载配置，平均1-2小时 |
| **优点2** | 版本环境与宿主机完全隔离，无冲突 | 全局安装，多项目版本冲突频发 |
| **优点3** | 团队共享docker-compose.yml，环境一致 | 每人一套不同的环境，经典"我这能跑" |
| **优点4** | 可编排上下游（Kafka+MySQL+Flink联动） | 各组件各自启动，端口/网络配置全靠脑记 |
| **缺点1** | Docker Desktop占用资源较大（约2-3GB内存） | 原生安装资源开销小 |
| **缺点2** | 容器内调试不如本地IDE方便 | 可直接在IDE中run/debug |
| **缺点3** | 网络IO多一层虚拟化，少量性能损失 | 无虚拟化开销 |

### 适用场景

**推荐**：
1. 新项目初期探索阶段，需要快速搭建全链路验证环境
2. 团队多人协作开发，需要统一开发环境基线
3. CI/CD中使用Docker容器做集成测试
4. 新人入职，用Docker环境降低上手门槛

**不推荐**：
1. 生产环境——推荐使用Flink on K8S Operator或YARN（见中级篇）
2. 资源受限的笔记本（<8GB内存）——Docker Desktop本身占用2GB+
3. 需要频繁修改Flink核心配置的场景——每次改配置要重启容器

### 注意事项
- Docker Desktop对Windows/Mac的内存限制：建议设置内存≥4GB，CPU ≥ 2核
- 生产环境不要用Docker Compose部署Flink集群——它没有高可用能力，JobManager挂了不会自动恢复
- Checkpoint目录映射到宿主机后，确保宿主机目录不为空且Flink用户有写入权限

### 常见踩坑经验

**案例1：Windows Docker Desktop上TaskManager反复退出，日志"Unable to create file for JNA"**
- 根因：Windows文件系统大小写不敏感，与Linux容器内部分lib冲突
- 解决：将Flink状态和Checkpoint目录迁移到WSL文件系统而非Windows NTFS

**案例2：Kafka容器日志报"Connection to node -1 could not be established"，Flink消费端一直timeout**
- 根因：`ADVERTISED_LISTENERS`配置为`localhost:9092`，但Flink容器通过服务名`kafka:9092`访问时，Kafka返回的advertised地址是`localhost:9092`——Flink容器里的`localhost`不是Kafka容器
- 解决：将ADVERTISED_LISTENERS改为`PLAINTEXT://kafka:9092`（容器间通信），或者对Flink容器使用宿主机IP+端口映射

**案例3：docker-compose down -v 导致MySQL数据全丢**
- 根因：`-v`参数会删除所有volumes，包括MySQL数据卷
- 解决：日常使用`docker-compose down`不要加`-v`；需要重置环境时手动执行`docker-compose down -v && docker-compose up -d`

### 思考题

1. docker-compose中taskmanager使用了`deploy: replicas: 2`，但Flink WebUI里显示的是2个TaskManager还是4个Slot？taskmanager.replicas和taskmanager.numberOfTaskSlots之间的关系是什么？

2. 如果我希望Flink作业能从Docker容器外部（比如宿主机上的Kafka客户端）发送数据到Kafka，docker-compose中的Kafka配置需要怎么调整？为什么ADVERTISED_LISTENERS要同时配置内外两个地址？

---

> **完整代码**：本章完整代码请参考附录或访问 https://github.com/flink-column/flink-practitioner  
> **思考题答案**：见附录文件 `appendix-answers.md`
