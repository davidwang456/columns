# 第3章 环境搭建：Docker Compose一键部署

## 1 项目背景

### 业务场景：从零开始搭建Flink CDC开发环境

假设你刚加入一家公司，团队决定使用Flink CDC来构建实时数据集成平台。作为新成员，你的第一个任务就是**搭建可复现的开发环境**。你需要确保：
- 所有团队成员在5分钟内能得到一致的开发环境
- MySQL 8.0已开启Binlog，可以测试CDC
- Kafka已就绪，可以作为CDC数据的中转
- Flink Standalone集群已启动，可以提交作业

你会怎么做？告诉每个同事"去下载MySQL、手动开启Binlog、配置server-id、下载Flink解压启动……"？这不仅耗时且容易出配置差错，更可怕的是每个人配置的不一致会引出"我本地跑得通啊"的经典甩锅难题。

### 痛点放大

手动搭建Flink CDC开发环境面临的典型问题：

| 痛点 | 具体表现 |
|------|---------|
| **环境依赖复杂** | 需要安装JDK 11、Maven、MySQL 8.0、Flink、Kafka、ZooKeeper，每个都有特定版本要求 |
| **MySQL配置繁琐** | 必须开启`log-bin`、设置`binlog_format=ROW`、创建CDC专用账号授权 |
| **版本兼容性陷阱** | Flink CDC 2.4需要Flink 1.13+，Flink CDC 3.0需要Flink 1.20+，版本对不上跑不起来 |
| **环境不一致** | Windows/Mac/Linux下MySQL安装路径、配置文件位置完全不同 |
| **卸载/重建困难** | 测试环境搞乱了怎么办？手动卸载再重装至少需要30分钟 |

**Docker Compose** 正是解决这些问题的银弹——一个`docker-compose.yml`文件定义了所有服务的版本、网络、端口映射、数据卷，一行命令启动所有组件，保证所有人环境完全一致。

### 最终环境架构图

```
┌─────────────────────────────────────────────────────┐
│                  Docker Compose                      │
│                                                      │
│  ┌─────────────┐    ┌──────────────┐                │
│  │  ZooKeeper   │    │    Kafka     │                │
│  │  (3.8.1)    │◄──►│  (3.5.1)    │                │
│  └─────────────┘    └──────┬───────┘                │
│                            │                         │
│  ┌─────────────┐    ┌──────┴───────┐                │
│  │   MySQL     │    │  Flink Job   │                │
│  │   (8.0)     │    │   Manager    │                │
│  │  binlog=ON  │    │  (1.20.3)   │                │
│  └─────────────┘    └──────┬───────┘                │
│                            │                         │
│                    ┌───────┴───────┐                │
│                    │ Flink Task    │                │
│                    │  Manager      │                │
│                    │  (1.20.3)    │                │
│                    └───────────────┘                │
│                                                      │
│  网络: flink-cdc-net    数据卷: mysql_data,kafka_data  │
└─────────────────────────────────────────────────────┘
```

---

## 2 项目设计 · 三人交锋对话

### 角色
- **小胖**：贪吃爱玩，喜欢偷懒
- **小白**：严谨细致，关注边界
- **大师**：经验丰富的技术Leader

---

**小胖**（打着哈欠）：又要搭环境啊……上次我装MySQL搞了一下午，先装brew，然后发现Mac上跑MySQL 8各种权限问题，最后换了个Docker镜像跑了。这次能不能直接上Docker，省得折腾？

**大师**（点头）：你踩过的坑正是Docker要解决的根本问题。Flink CDC开发环境涉及5个服务（Flink JM + TM、MySQL、Kafka、ZooKeeper），手动安装至少需要2小时，而且每个人装的版本不一样。用Docker Compose的话，一行`docker-compose up -d`，10分钟全部就绪。

**小胖**：那MySQL的Binlog呢？我听说MySQL默认不开Binlog的，Flink CDC依赖Binlog，这个也得配置吧？

**大师**：没错！MySQL 8.0默认`log_bin=OFF`。我们在Docker Compose中的MySQL镜像需要用自定义配置文件覆盖默认配置，关键参数如下：

```ini
[mysqld]
server-id = 1
log-bin = mysql-bin
binlog-format = ROW
binlog-row-image = FULL
expire-log-days = 7
gtid-mode = ON
enforce-gtid-consistency = ON
```

这些配置会通过Docker的volume挂载到`/etc/mysql/conf.d/`目录下，MySQL启动时自动加载。里面最值得关注的是**GTID模式**——它让Binlog位点不再依赖`(filename, position)`这种物理坐标，而是用全局唯一的GTID标记每个事务，主从切换后CDC作业依然能找到正确的位置。

**小白**（若有所思）：那Kafka和ZooKeeper为什么需要两个容器？我看到了Flink CDC可以直接把MySQL的数据写入Kafka，那Kafka在架构里是必需品吗？

**大师**：好问题！在Flink CDC的架构中，Kafka不是必需品——你可以直接从MySQL CDC读数据，经过Flink处理后，写入任何Sink（比如Iceberg、Doris、或者另一个MySQL）。但引入Kafka有两个关键好处：
1. **解耦**：Source（MySQL CDC）和Sink（写入目标）之间通过Kafka缓冲，任何一方挂掉不影响另一方
2. **多消费者**：同一份CDC数据可以被多个下游应用消费（实时数仓、搜索引擎、缓存失效），互不干扰

**技术映射**：Kafka在这里充当"消息总线"的角色——就像快递中转站，包裹（数据）从中转站发出，不必等快递员（消费者）签收后再发下一批。中转站就是Buffer，快递员就是Consumer。

**小白**：那Flink Standalone模式需要两个容器（JobManager + TaskManager），这和Flink Session Mode有什么关系？如果我要跑多个Flink CDC作业呢？

**大师**：这是Flink的两种部署模式：
- **Session Mode（会话模式）**：一个Flink集群（1个JM + N个TM）共享给多个作业。资源池化，但作业间有资源竞争风险。适合开发测试环境。
- **Application Mode（应用模式）**：每个作业启动一个专用Flink集群，作业结束集群销毁。资源隔离好，但启动开销大。适合生产环境。

我们的Docker Compose搭建的是Session Mode，方便你提交多个作业进行对比测试。生产环境中，通常使用`flink-cdc.sh --target kubernetes-application`直接提交到K8s。

**小胖**（跃跃欲试）：那赶紧搞起来吧！我就喜欢一行命令搞定所有东西的感觉。

---

## 3 项目实战

### 环境准备

**前置条件：**
- Docker Desktop 4.20+（Docker Engine 24.0+）
- Docker Compose V2（Docker Desktop自带）
- Git

### 分步实现

#### 步骤1：编写docker-compose.yml

本步骤目标：定义所有服务容器的配置，包括MySQL 8.0（开启Binlog）、Kafka 3.5.1、ZooKeeper、Flink 1.20.3。

```yaml
version: '3.8'

networks:
  flink-cdc-net:
    driver: bridge

volumes:
  mysql_data:
  kafka_data:
  zk_data:

services:
  # ========== MySQL 8.0 with Binlog enabled ==========
  mysql:
    image: mysql:8.0
    container_name: mysql-cdc
    ports:
      - "3306:3306"
    environment:
      MYSQL_ROOT_PASSWORD: root123
      MYSQL_DATABASE: shop
    volumes:
      # 挂载自定义MySQL配置（开启Binlog、GTID）
      - ./conf/mysql/my.cnf:/etc/mysql/conf.d/my.cnf
      - mysql_data:/var/lib/mysql
    networks:
      - flink-cdc-net
    healthcheck:
      test: ["CMD", "mysqladmin", "ping", "-h", "localhost", "-u", "root", "-proot123"]
      interval: 10s
      timeout: 5s
      retries: 5

  # ========== ZooKeeper (Kafka依赖) ==========
  zoo:
    image: confluentinc/cp-zookeeper:7.4.0
    container_name: zoo-cdc
    ports:
      - "2181:2181"
    environment:
      ZOOKEEPER_CLIENT_PORT: 2181
      ZOOKEEPER_TICK_TIME: 2000
    volumes:
      - zk_data:/var/lib/zookeeper
    networks:
      - flink-cdc-net

  # ========== Kafka 3.5 ==========
  kafka:
    image: confluentinc/cp-kafka:7.4.0
    container_name: kafka-cdc
    ports:
      - "9092:9092"
    depends_on:
      - zoo
    environment:
      KAFKA_BROKER_ID: 1
      KAFKA_ZOOKEEPER_CONNECT: zoo:2181
      KAFKA_ADVERTISED_LISTENERS: PLAINTEXT://localhost:9092
      KAFKA_OFFSETS_TOPIC_REPLICATION_FACTOR: 1
      KAFKA_TRANSACTION_STATE_LOG_MIN_ISR: 1
      KAFKA_TRANSACTION_STATE_LOG_REPLICATION_FACTOR: 1
    volumes:
      - kafka_data:/var/lib/kafka
    networks:
      - flink-cdc-net

  # ========== Flink JobManager ==========
  jobmanager:
    image: flink:1.20.3-scala_2.12-java11
    container_name: flink-jm-cdc
    ports:
      - "8081:8081"
    command: jobmanager
    environment:
      - JOB_MANAGER_RPC_ADDRESS=jobmanager
    volumes:
      # 挂载Flink CDC和MySQL connector的JAR包
      - ./lib:/opt/flink/lib
    networks:
      - flink-cdc-net

  # ========== Flink TaskManager ==========
  taskmanager:
    image: flink:1.20.3-scala_2.12-java11
    container_name: flink-tm-cdc
    depends_on:
      - jobmanager
    command: taskmanager
    environment:
      - JOB_MANAGER_RPC_ADDRESS=jobmanager
      - TASK_MANAGER_NUMBER_OF_TASK_SLOTS=4
    volumes:
      - ./lib:/opt/flink/lib
    networks:
      - flink-cdc-net
```

#### 步骤2：创建MySQL自定义配置

本步骤目标：启用MySQL的Binlog（ROW格式）+ GTID模式，这是Flink CDC能够工作的基础。

创建 `conf/mysql/my.cnf` 文件：

```ini
[mysqld]
# 服务器唯一ID（每个MySQL实例必须不同）
server-id = 1

# 开启二进制日志
log-bin = mysql-bin

# Binlog格式：ROW模式记录每行变更的完整前镜像和后镜像
binlog-format = ROW

# 记录完整的行镜像（包括所有列，即使没有被修改）
binlog-row-image = FULL

# Binlog保留7天，足够Flink CDC从中断点恢复
expire-log-days = 7

# GTID模式（全局事务标识符）——主从切换不断流的关键
gtid-mode = ON
enforce-gtid-consistency = ON

# 字符集
character-set-server = utf8mb4
collation-server = utf8mb4_unicode_ci
```

#### 步骤3：准备Flink CDC JAR包

本步骤目标：下载Flink CDC和MySQL Connector的JAR包，挂载到Flink容器的lib目录。

创建 `lib/` 目录并下载依赖（手动或脚本方式）：

```bash
# 创建lib目录
mkdir -p lib

# 下载Flink CDC MySQL连接器（包含Debezium依赖）
curl -o lib/flink-connector-mysql-cdc-3.0.0.jar \
  https://repo1.maven.org/maven2/org/apache/flink/flink-connector-mysql-cdc/3.0.0/flink-connector-mysql-cdc-3.0.0.jar

# 下载MySQL JDBC驱动
curl -o lib/mysql-connector-java-8.0.33.jar \
  https://repo1.maven.org/maven2/mysql/mysql-connector-java/8.0.33/mysql-connector-java-8.0.33.jar

# 下载Kafka连接器（如果后续章节需要）
# curl -o lib/flink-connector-kafka-3.0.0-1.18.jar ...
```

> **注意**：如果你在中国大陆，访问Maven中央仓库可能较慢，可以替换为阿里云镜像：
> `https://maven.aliyun.com/repository/central`

#### 步骤4：启动所有服务

```bash
# 启动所有容器（后台模式）
docker-compose up -d

# 查看容器状态
docker-compose ps

# 查看日志（确认MySQL和Kafka启动成功）
docker-compose logs -f mysql
docker-compose logs -f kafka
```

**预期输出：**
```
[+] Running 5/5
 ✔ Container mysql-cdc    Started
 ✔ Container zoo-cdc      Started
 ✔ Container kafka-cdc    Started
 ✔ Container flink-jm-cdc    Started
 ✔ Container flink-tm-cdc    Started
```

#### 步骤5：验证环境

验证MySQL Binlog状态和创建CDC用户：

```bash
# 1. 进入MySQL容器
docker exec -it mysql-cdc mysql -uroot -proot123

# 2. 确认Binlog已开启
mysql> SHOW VARIABLES LIKE 'log_bin';
+---------------+-------+
| Variable_name | Value |
+---------------+-------+
| log_bin       | ON    |
+---------------+-------+

# 3. 确认Binlog格式为ROW
mysql> SHOW VARIABLES LIKE 'binlog_format';
+---------------+-------+
| Variable_name | Value |
+---------------+-------+
| binlog_format | ROW   |
+---------------+-------+

# 4. 创建CDC专用用户并授权
mysql> CREATE USER 'cdc_user'@'%' IDENTIFIED BY 'cdc_pass';
mysql> GRANT SELECT, RELOAD, SHOW DATABASES, REPLICATION SLAVE, REPLICATION CLIENT ON *.* TO 'cdc_user'@'%';
mysql> FLUSH PRIVILEGES;

# 5. 验证CDC用户权限
mysql> SHOW GRANTS FOR 'cdc_user'@'%';
```

验证Flink Web UI和Kafka可用性：

```bash
# 1. 验证Flink Web UI（浏览器打开 http://localhost:8081）
# 确认JobManager和TaskManager都已注册，可用Slot数=4

# 2. 验证Kafka生产/消费
# 创建测试Topic
docker exec -it kafka-cdc kafka-topics --create \
  --topic test-cdc \
  --bootstrap-server localhost:9092 \
  --partitions 1 --replication-factor 1

# 生产一条消息
docker exec -it kafka-cdc bash -c 'echo "hello cdc" | kafka-console-producer --topic test-cdc --bootstrap-server localhost:9092'

# 消费验证
docker exec -it kafka-cdc kafka-console-consumer \
  --topic test-cdc \
  --bootstrap-server localhost:9092 \
  --from-beginning --max-messages 1

# 删除测试Topic
docker exec -it kafka-cdc kafka-topics --delete \
  --topic test-cdc \
  --bootstrap-server localhost:9092
```

**预期输出：**
```
Created topic test-cdc.
hello cdc
```

#### 步骤6：关闭和清理环境

```bash
# 停止所有容器（保留数据卷）
docker-compose down

# 完全清理（删除数据卷，谨慎使用！）
docker-compose down -v

# 查看残留容器
docker ps -a | grep cdc
```

#### 常见陷坑及解决方法

| 问题 | 原因 | 解决方法 |
|------|------|---------|
| `docker-compose`命令找不到 | Docker Compose V1/V2命名差异 | 使用`docker compose up -d`（中间无短横线） |
| MySQL启动失败，权限目录错误 | Apple Silicon Mac上的兼容性问题 | 添加`platform: linux/amd64`到MySQL服务配置 |
| Kafka连接失败：Connection refused | Kafka的ADVERTISED_LISTENERS配置不正确 | 确保`KAFKA_ADVERTISED_LISTENERS: PLAINTEXT://localhost:9092` |
| Flink TaskManager连接不到JobManager | 网络桥接配置问题 | 确保所有服务在同一个network `flink-cdc-net`下 |
| Windows下Volume挂载失败 | Docker Desktop路径映射问题 | 使用绝对路径或确保`./conf/`目录实际存在 |

---

## 4 项目总结

### 优点 & 缺点

**Docker Compose开发环境方案的优势：**
1. **一分钟搭建**：一行命令启动所有依赖服务
2. **环境一致性**：团队成员之间、CI/CD环境、生产环境完全一致
3. **无残留**：`docker-compose down -v`即可完全清理，不影响宿主机
4. **版本锁定**：Docker Image Tag精确锁定版本（如`mysql:8.0`而非`mysql:latest`）
5. **可编排**：可通过`depends_on`和`healthcheck`控制启动顺序

**Docker Compose的局限：**
1. **性能开销**：Docker Desktop在Mac/Win上需要虚拟机，I/O性能不如原生
2. **不适用生产**：生产环境建议Kubernetes + Flink Operator，而非Docker Compose
3. **调试困难**：容器内网络互通，但宿主机访问容器服务需端口映射

### 环境方案对比

| 对比维度 | Docker Compose | 手动安装 | 云服务（Confluent Cloud + RDS） |
|---------|---------------|---------|-------------------------------|
| 搭建时间 | 1分钟 | 2~4小时 | 10分钟 |
| 可移植性 | 极高（单文件定义） | 低 | 中 |
| 本地调试 | 支持 | 支持 | 不支持（需要公网） |
| 成本 | 免费 | 免费 | 按量付费 |
| 生产仿真度 | 中 | 高 | 高 |

### 注意事项

1. **JDK版本**：Flink 1.20需要JDK 11，而Flink 1.14之前需要JDK 8。本地开发时注意`JAVA_HOME`切换。
2. **Docker Desktop资源限制**：Flink + Kafka + MySQL + ZooKeeper至少需要4GB内存和2核CPU。在Docker Desktop Settings中分配给足够资源。
3. **lib目录版本匹配**：挂载到Flink `lib/`目录下的JAR包版本必须与Flink版本兼容。错误版本会导致`NoSuchMethodError`。
4. **配置文件热加载**：修改`docker-compose.yml`或配置文件后，需要重新创建容器（`docker-compose up -d --force-recreate`）。

### 常见踩坑经验

**故障案例1：Flink CDC作业提交后ClassNotFoundException**
- **现象**：提交作业后，TaskManager日志报错`ClassNotFoundException: io.debezium.connector.mysql.MySqlConnector`
- **根因**：`flink-connector-mysql-cdc`的JAR包在JobManager上存在，但TaskManager上没有。Flink Standalone模式下JAR不会自动分发
- **解决方案**：不论Session Mode还是Application Mode，都必须保证所有TaskManager的`lib/`目录包含CDC相关JAR包。或者使用`--jar`参数提交时指定完整路径

**故障案例2：MySQL容器启动后无法外部连接**
- **现象**：宿主机用`mysql -h 127.0.0.1 -P 3306 -u root -p`连接失败
- **根因**：MySQL 8.0默认认证插件是`caching_sha2_password`，部分客户端不支持
- **解决方案**：在`my.cnf`中添加`default_authentication_plugin=mysql_native_password`，或者在创建用户时指定`IDENTIFIED WITH mysql_native_password BY 'password'`

**故障案例3：Kafka容器不断重启**
- **现象**：Kafka容器启动后几秒就退出，循环重启
- **根因**：ZooKeeper未完全就绪时Kafka就尝试连接。`depends_on`只保证容器启动顺序，不保证服务本身已可用
- **解决方案**：在Kafka容器启动脚本中添加等待ZooKeeper健康检查的脚本，或使用`healthcheck` + `depends_on`的condition

### 思考题

1. **进阶题①**：在Docker Compose中，如果我想模拟MySQL主从复制场景（1主1从），应该如何在`docker-compose.yml`中配置两个MySQL实例？Flink CDC应该连接主库还是从库读取Binlog？

2. **进阶题②**：Flink Standalone Session模式下，多个Flink CDC作业共享同一个TaskManager的Slot资源。如果一个作业发生OOM，会影响其他作业。你知道Flink有什么机制可以隔离作业资源吗？提示：考虑 Application Mode 或 Per-Job Mode。

---

> **下一章预告**：第4章「第一个Flink CDC程序：DataStream API」——在搭建好的环境上，编写你的第一个Flink CDC程序，从MySQL实时读取订单数据并打印到控制台，全程代码逐行解读。
