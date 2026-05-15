# 第2章：环境搭建——Docker Compose 一键部署全栈

## 1. 项目背景

"大师，上周的分享会大家反馈很好，但我回来想自己搭个环境跑一下，搞了两天都没跑起来..."小胖在群聊里发了个哭泣的表情。这几乎是每个 Debezium 新手的共同经历——教程看懂了，环境搭不起来。Zookeeper、Kafka、Kafka Connect、MySQL、Debezium Connector，5 个组件之间环环相扣，任何一个端口不通或配置错误，整个链路就跑不起来。

传统的搭法是一步步手动安装：先下载 Zookeeper 配置 zoo.cfg，再下载 Kafka 配置 server.properties，然后找 Kafka Connect 的启动脚本，再去 MySQL 里执行一堆 GRANT 语句，最后把 Debezium Connector 的 JAR 包放到 plugin.path 目录...整个过程至少需要半天，中间任何一个疏忽都可能导致 Connector 状态 FAILED，然后对着日志一脸茫然。

**这就是本章要解决的问题**：用 Docker Compose 一键编排，30 分钟内拥有一个完全可用的 Debezium 实验环境。我们将从零起步，理解每个容器的职责和配置，掌握环境验证的每一步命令。

### 痛点放大

如果没有 Docker Compose 编排，手动搭建面临的核心问题：

- **依赖地狱**：JDK 17 → Zookeeper 3.8 → Kafka 3.6 → Kafka Connect 3.6 → MySQL 8.0，版本组合需严格匹配，一个版本不对就全链不通
- **网络配置繁琐**：5 个组件间的端口、hostname 相互引用，手动改配置文件极易出错
- **环境一致性差**：你的 macOS 上能跑，同事的 Windows 上不行，CI 环境又是另一套
- **重置成本高**：调试过程中想"推到重来"，手动搭建需要一个个 kill 进程、删数据目录

Docker Compose 将所有依赖打包在一个 yml 文件中，`docker compose up -d` 一条命令启动一切，`docker compose down -v` 一条命令完全销毁。从"半天搭建"到"30 分钟开干"，这是效率的革命。

---

## 2. 项目设计——三人对话

**（周五下午，工位区域，小胖盯着显示器抓耳挠腮）**

**小胖**："大师救命！我上周听你讲了 CDC 原理热血沸腾，回家就试着搭环境。下了个 Zookeeper，下了个 Kafka，又下了个 Kafka Connect，装 MySQL 的时候发现端口被占用了，改了端口又发现 Kafka 连不上 Zookeeper...折腾了两天，我现在连一个 Topic 都没创建出来！"

**小白**（从隔壁工位探过头）："小胖你没用 Docker Compose？现在谁还手动搭中间件环境啊。我跟大师学的时候，一个 `docker-compose.yml` 文件就搞定了，5 分钟出活。"

**小胖**："Docker Compose 我知道，但那个 yml 文件里一堆配置我看不懂——什么 `CONNECT_BOOTSTRAP_SERVERS`、`CONNECT_GROUP_ID`，还有 `KAFKA_ADVERTISED_LISTENERS` 是什么鬼？我照着网上抄了一个直接报错，根本不敢改。"

**大师**（拉过椅子坐下）："这正是大多数人的瓶颈——能抄但不敢改，一旦出问题不知道怎么查。我来带你从第一行开始写这个 docker-compose.yml，写完你就知道自己掌控了全局。先想一个问题：你出门点外卖，需要几个角色？"

**小胖**："外卖小哥？商家？加上我这个买家，三个角色。"

**大师**："对。我们的 CDC 环境也是类似——**Kafka 有点像物流仓库**，消息先存到这里再分发给消费者。Kafka 本身需要 **Zookeeper（Kraft 模式下可选）** 来管理集群元数据，就像物流中心需要调度系统。**Kafka Connect 就像分发站**，它负责把 Debezium Connector 这个'骑手'派到各个数据库门口。**MySQL 就是一个'商家'**，负责产出变更数据。"

**小白**："那 docker-compose.yml 里这些大写字母的环境变量是什么？"

**大师**："好问题。Docker Compose 启动 Kafka 时，是通过环境变量来覆盖 Kafka 默认配置文件的。比如 `KAFKA_ADVERTISED_LISTENERS` 就是告诉 Kafka '你的对外地址是什么'。Docker 容器内部用的是 `kafka:9092`，但外部（宿主机和其他容器）访问时需要知道真实地址。我们来逐行拆解——"

**大师**（打开笔记本开始写）：

```yaml
version: '3.8'
services:
  zookeeper:
    image: confluentinc/cp-zookeeper:7.6.0
    # Zookeeper 负责 Kafka 集群的元数据管理（Kafka 3.3+ 可选 Kraft 替代）
    environment:
      ZOOKEEPER_CLIENT_PORT: 2181
    ports:
      - "2181:2181"
```

**小胖**："等等，为什么是 `confluentinc/cp-` 开头？不是 apache/kafka 吗？"

**大师**："问得好。Confluent 是 Kafka 的商业公司，他们的 Docker 镜像预装了 Java、Kafka、Kafka Connect，并且支持通过环境变量快速配置，比 Apache 官方裸镜像方便得多。但背后的 Kafka 内核是一样的，协议完全兼容。"

**小白**："那 Kafka Connect 和 Kafka 的关系是什么？我看很多教程把它俩放一个容器里？"

**大师**："本质上是包含关系——Kafka Connect 是 Kafka 的一个子组件，在源码上属于 `connect/` 目录，但部署时可以独立于 Kafka Broker 运行。开发环境通常把 Connect 和 Broker 放在同一台机器，生产环境会拆分成独立的集群。在 Docker Compose 中，我们用的是 Confluent 的 `cp-kafka-connect` 镜像，它已经包含了 Kafka Connect 的二进制文件，我们只需要通过环境变量配置它连接哪个 Kafka 集群。"

```yaml
  connect:
    image: confluentinc/cp-kafka-connect:7.6.0
    depends_on:
      - kafka
    environment:
      CONNECT_BOOTSTRAP_SERVERS: kafka:9092      # 连接到 Kafka
      CONNECT_GROUP_ID: debezium-lab              # Connect 集群标识
      CONNECT_CONFIG_STORAGE_TOPIC: connect-configs
      CONNECT_OFFSET_STORAGE_TOPIC: connect-offsets
      CONNECT_STATUS_STORAGE_TOPIC: connect-statuses
      CONNECT_KEY_CONVERTER: org.apache.kafka.connect.json.JsonConverter
      CONNECT_VALUE_CONVERTER: org.apache.kafka.connect.json.JsonConverter
      CONNECT_PLUGIN_PATH: /kafka/connect      # Debezium JAR 存放位置
```

**大门**："看到这些 `CONNECT_*_STORAGE_TOPIC` 了吗？这就是 Kafka Connect 内部用来存储 **配置、offset、状态** 的三个系统 Topic。它们由 Connect Worker 自动创建和管理，你不需要手动操作，但理解它们的用途是后面排障的基础。"

**小胖**："那 Debezium Connector 的 JAR 包在哪？怎么装进 Kafka Connect 的？"

**大师**："有两种方式。方式一：通过 Docker 的 volumes 挂载到 `/kafka/connect` 目录；方式二：用 Dockerfile 构建自定义镜像，把 JAR 包直接 COPY 进去。推荐方式一，因为方便随时换版本——"

```yaml
    volumes:
      - ./debezium-connector-mysql:/kafka/connect/debezium-connector-mysql
```

**技术映射**：`CONNECT_BOOTSTRAP_SERVERS` = Kafka 集群的"家庭住址"；`CONNECT_PLUGIN_PATH` = Connect 的"工具架"，所有 Connector JAR 包都放在这里等待加载。

**大师**："好了，6 个 service 定义完了——zookeeper、kafka、connect、mysql、schema-registry、debezium-ui。总共约 120 行 YAML。接下来我们就实战搭建，一条命令跑起来。"

---

## 3. 项目实战

### 环境准备

- Docker Desktop 4.x+（或 Docker Engine 24.x+）
- 内存建议 8GB+ 分配给 Docker
- 磁盘空间 20GB+

### 步骤1：创建项目目录并下载 Connector 插件

**目标**：建立标准化的项目目录结构。

```bash
# 创建项目目录
mkdir -p ~/debezium-lab/plugins
cd ~/debezium-lab

# 下载 Debezium MySQL Connector 2.7.1.Final
wget https://repo1.maven.org/maven2/io/debezium/debezium-connector-mysql/2.7.1.Final/debezium-connector-mysql-2.7.1.Final-plugin.tar.gz
tar -xzf debezium-connector-mysql-2.7.1.Final-plugin.tar.gz -C plugins/

# 目录结构确认
ls plugins/debezium-connector-mysql/
# 预期输出：debezium-api-2.7.1.Final.jar  debezium-connector-mysql-2.7.1.Final.jar  ...

# 创建 MySQL 初始化脚本目录
mkdir -p mysql-init
```

### 步骤2：编写 MySQL 初始化 SQL

**目标**：准备好 binlog 开启权限和测试库表。

```sql
-- mysql-init/01-init.sql
-- 创建 debezium 专用账号
CREATE USER 'debezium'@'%' IDENTIFIED BY 'dbz1234';
GRANT SELECT, RELOAD, SHOW DATABASES, REPLICATION SLAVE, REPLICATION CLIENT ON *.* TO 'debezium'@'%';
FLUSH PRIVILEGES;

-- 创建实验数据库
CREATE DATABASE IF NOT EXISTS inventory;
USE inventory;

-- 创建订单表
CREATE TABLE IF NOT EXISTS orders (
    id INT PRIMARY KEY AUTO_INCREMENT,
    customer_id INT NOT NULL,
    product_name VARCHAR(255) NOT NULL,
    quantity INT NOT NULL DEFAULT 1,
    price DECIMAL(10,2) NOT NULL,
    status VARCHAR(50) NOT NULL DEFAULT 'pending',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);

-- 插入一些初始数据
INSERT INTO orders (customer_id, product_name, quantity, price, status) VALUES
(1001, 'iPhone 15', 1, 7999.00, 'completed'),
(1002, 'MacBook Pro', 1, 14999.00, 'processing'),
(1003, 'AirPods Pro', 2, 1899.00, 'shipped');
```

### 步骤3：编写 docker-compose.yml

**目标**：一键部署 Zookeeper + Kafka + Kafka Connect + MySQL + Schema Registry + Debezium UI。

```yaml
# docker-compose.yml
version: '3.8'
services:
  zookeeper:
    image: confluentinc/cp-zookeeper:7.6.0
    container_name: zookeeper
    ports:
      - "2181:2181"
    environment:
      ZOOKEEPER_CLIENT_PORT: 2181
      ZOOKEEPER_TICK_TIME: 2000

  kafka:
    image: confluentinc/cp-kafka:7.6.0
    container_name: kafka
    depends_on:
      - zookeeper
    ports:
      - "9092:9092"
    environment:
      KAFKA_BROKER_ID: 1
      KAFKA_ZOOKEEPER_CONNECT: zookeeper:2181
      KAFKA_ADVERTISED_LISTENERS: PLAINTEXT://localhost:9092
      KAFKA_OFFSETS_TOPIC_REPLICATION_FACTOR: 1
      KAFKA_TRANSACTION_STATE_LOG_REPLICATION_FACTOR: 1
      KAFKA_TRANSACTION_STATE_LOG_MIN_ISR: 1
      KAFKA_GROUP_INITIAL_REBALANCE_DELAY_MS: 0

  mysql:
    image: mysql:8.0
    container_name: mysql
    ports:
      - "3306:3306"
    environment:
      MYSQL_ROOT_PASSWORD: root1234
      MYSQL_DATABASE: inventory
    volumes:
      - ./mysql-init:/docker-entrypoint-initdb.d
    command:
      - --server-id=1
      - --log-bin=mysql-bin
      - --binlog-format=ROW
      - --binlog-row-image=FULL
      - --gtid-mode=ON
      - --enforce-gtid-consistency=ON

  connect:
    image: confluentinc/cp-kafka-connect:7.6.0
    container_name: connect
    depends_on:
      - kafka
      - mysql
    ports:
      - "8083:8083"
    environment:
      CONNECT_BOOTSTRAP_SERVERS: kafka:9092
      CONNECT_GROUP_ID: debezium-lab
      CONNECT_CONFIG_STORAGE_TOPIC: connect-configs
      CONNECT_OFFSET_STORAGE_TOPIC: connect-offsets
      CONNECT_STATUS_STORAGE_TOPIC: connect-statuses
      CONNECT_CONFIG_STORAGE_REPLICATION_FACTOR: 1
      CONNECT_OFFSET_STORAGE_REPLICATION_FACTOR: 1
      CONNECT_STATUS_STORAGE_REPLICATION_FACTOR: 1
      CONNECT_KEY_CONVERTER: org.apache.kafka.connect.json.JsonConverter
      CONNECT_VALUE_CONVERTER: org.apache.kafka.connect.json.JsonConverter
      CONNECT_INTERNAL_KEY_CONVERTER: org.apache.kafka.connect.json.JsonConverter
      CONNECT_INTERNAL_VALUE_CONVERTER: org.apache.kafka.connect.json.JsonConverter
      CONNECT_REST_ADVERTISED_HOST_NAME: connect
      CONNECT_PLUGIN_PATH: /kafka/connect
    volumes:
      - ./plugins:/kafka/connect

  schema-registry:
    image: confluentinc/cp-schema-registry:7.6.0
    container_name: schema-registry
    depends_on:
      - kafka
    ports:
      - "8081:8081"
    environment:
      SCHEMA_REGISTRY_KAFKASTORE_BOOTSTRAP_SERVERS: PLAINTEXT://kafka:9092
      SCHEMA_REGISTRY_HOST_NAME: schema-registry
      SCHEMA_REGISTRY_LISTENERS: http://0.0.0.0:8081

  debezium-ui:
    image: debezium/debezium-ui:2.7
    container_name: debezium-ui
    depends_on:
      - connect
    ports:
      - "8080:8080"
    environment:
      KAFKA_CONNECT_URIS: http://connect:8083
```

### 步骤4：启动并验证全栈环境

**目标**：一键启动所有容器并逐层验证。

```bash
# 启动所有容器（-d 后台运行）
docker compose up -d

# 预期输出：
# [+] Running 7/7
#  ✔ Container zookeeper       Started
#  ✔ Container kafka           Started
#  ✔ Container mysql           Started
#  ✔ Container connect         Started
#  ✔ Container schema-registry Started
#  ✔ Container debezium-ui     Started

# 等待 MySQL 完成初始化（大约 30 秒）
sleep 30

# ---------- 验证层1：Zookeeper ----------
docker exec zookeeper zkServer.sh status
# 预期输出：Mode: standalone

# ---------- 验证层2：Kafka ----------
docker exec kafka kafka-topics --bootstrap-server localhost:9092 --list
# 预期输出：
# connect-configs
# connect-offsets
# connect-statuses

# ---------- 验证层3：MySQL ----------
docker exec mysql mysql -uroot -proot1234 -e "SHOW VARIABLES LIKE 'log_bin';"
# 预期输出：
# +---------------+-------+
# | Variable_name | Value |
# +---------------+-------+
# | log_bin       | ON    |
# +---------------+-------+

docker exec mysql mysql -uroot -proot1234 -e "SELECT * FROM inventory.orders;"
# 预期输出：3 行初始数据

# ---------- 验证层4：Kafka Connect ----------
curl http://localhost:8083/connectors
# 预期输出：[]

curl http://localhost:8083/connector-plugins | python3 -m json.tool | grep debezium
# 预期输出：
# "class": "io.debezium.connector.mysql.MySqlConnector"

# ---------- 验证层5：Debezium UI ----------
# 浏览器打开 http://localhost:8080，确认页面正常加载

# ---------- 验证层6：Schema Registry ----------
curl http://localhost:8081/subjects
# 预期输出：[]
```

### 可能遇到的坑及解决方法

| 坑 | 现象 | 解决方法 |
|----|------|---------|
| MySQL 容器启动失败 | `Exit 1` | 检查 `my.cnf` 中有无 `server-id` 冲突；删除已有 MySQL 数据卷重试 |
| Kafka 启动缓慢 | 持续 `STARTING` 状态 | 检查 Docker 内存分配是否 >= 4GB；等待 60 秒，Kafka 启动较慢 |
| Connect 找不到 Connector 插件 | `curl /connector-plugins` 返回空 | 检查 `./plugins` 目录挂载是否正确；确认 JAR 文件有读权限 |
| Connect 无法连接 Kafka | 日志显示 `Connection refused` | 确认 `KAFKA_ADVERTISED_LISTENERS` 中的地址在 Connect 容器内可达 |
| Schema Registry 启动失败 | `Exit 1` | 确认 Kafka 已经完全启动（`docker logs kafka` 查看是否打印 `started`） |

### 完整 docker-compose.yml 清单

已将上述完整 YAML 存放在项目仓库：`https://github.com/example/debezium-lab`（附录）。

---

## 4. 项目总结

### 优点 & 缺点

| 维度 | Docker Compose 方案 | 手动安装方案 |
|------|-------------------|-------------|
| 启动耗时 | ★★★★★ < 5 分钟 | ★☆☆☆☆ > 2 小时 |
| 可复现性 | ★★★★★ 100% 可复现 | ★★☆☆☆ 依赖本机环境 |
| 销毁重建 | ★★★★★ 一条命令 | ★☆☆☆☆ 逐个 kill + 删目录 |
| 学习成本 | ★★★☆☆ 需要 Docker 基础 | ★★★★☆ 接触底层配置 |
| 生产适用性 | ★★☆☆☆ 仅限开发/测试 | ★★★★☆ 了解每个组件部署细节 |

### 适用场景

1. **个人学习 & 实验**：快速搭建可丢弃的实验环境
2. **团队协作**：新人入职只需一条命令即可获得全栈环境
3. **CI/CD 集成**：集成测试中一键启动 CDC 环境，测试完自动销毁
4. **Demo 演示**：给领导/客户演示 CDC 能力时快速启动
5. **持续开发环境**：开发自定义 Connector 时反复清洗重建

### 注意事项

- **内存分配**：Docker 至少分配 8GB 内存，6 个容器同时运行资源占用约 4-6GB
- **端口冲突**：3306（MySQL）、8080（UI）、8083（Connect）、8081（Schema Registry）、9092（Kafka）、2181（Zookeeper）这些端口不要被本机其他服务占用
- **数据持久化**：本配置未设置 volumes 持久化（除 MySQL 初始化脚本），`docker compose down -v` 会**删除所有数据**，生产环境请务必挂载数据卷

### 思考题

1. 如果把 `KAFKA_OFFSETS_TOPIC_REPLICATION_FACTOR` 设置为 3，当前单节点 Kafka Broker 能否启动成功？为什么？（提示：理解 replication factor 和 ISR 的关系）

2. 本环境中的 Kafka Connect 使用了 JSON Converter。如果要在生产环境中切换到 Avro Converter，docker-compose.yml 需要做哪些修改？（提示：涉及 CONVERTER 配置变更、Schema Registry 集成）

**（第1章思考题答案）**

1. 5000 张表的场景，推荐使用多个 Connector（按数据库拆分，如 50 个 Connector，每个负责 2 个库）。原因：一个 Connector 下的所有 Task 共享一个 Kafka Consumer Group，单 Connector 的 Task 数量受限于表数 × Topic 分区数，故障时影响面大。分 Connector 可以隔离故障域，独立调参（不同业务的 snapshot 策略可能不同），缺点是运维复杂度略高——可通过 Ansible 等自动化工具弥补。
2. 如果新消费者从 offset 0 开始消费，Schema Registry 能够帮助反序列化所有历史版本的 Schema——Schema Registry 中保存了完整的 Schema 版本历史（在 `_schemas` Topic 中），消息的 Schema ID 决定了使用哪个版本的 Schema 进行反序列化，只要 Schema Registry 没有被清理过就可以正常工作。兼容性策略上建议使用 `BACKWARD` 兼容（新 Schema 能读旧数据），确保存量消费者不会因 Schema 变更而崩溃。

---

> **推广提示**：本章的 docker-compose.yml 是后续所有章节的基石。建议运维团队在此基础上扩展数据卷挂载和健康检查，作为团队内部的"CDC 速建工具包"维护在 Git 仓库中。
