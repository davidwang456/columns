# 第1章：Debezium 术语全景与 CDC 工作原理

## 1. 项目背景

凌晨三点，运维群炸了——电商大促的订单数据同步延迟超过15分钟，下游的库存系统、物流系统、财务系统全线告急。DBA 小赵揉了揉眼睛，看着那张"经过手工 cron job + mysqlbinlog + Python 脚本"拼凑而成的数据同步方案，叹了口气：这套祖传方案已经跑了一年多，高峰期 binlog 解析脚本动不动就 OOM 重启，数据丢失完全靠第二天人工对账。

这并非个例。在 Debezium 这样的 CDC（Change Data Capture，变更数据捕获）平台出现之前，绝大多数团队都在重复造类似的轮子：定时全量同步（性能差、延迟高）、触发器方案（侵入性强、影响业务）、binlog 解析脚本（维护成本高、可靠性差）。这些自研方案在数据量小的时候尚可应付，一旦业务增长到每天数亿行变更量，"同步延迟"和"数据丢失"就像两座大山，压得运维和开发喘不过气。

更令人头疼的是多数据源场景。一个典型的电商系统可能同时使用 MySQL 做交易、PostgreSQL 做用户画像、MongoDB 做行为日志。如果为每种数据库分别维护一套同步脚本，维护成本呈指数级增长——每新增一张表，都要在三个系统中修改配置；每升级一次数据库版本，都要重新适配 binlog 格式。

Debezium 要解决的，正是这个"异构数据源的实时变更捕获"问题。它建立在 Apache Kafka Connect 框架之上，提供了一套标准化的 CDC 能力：一行配置即可接入新数据源，所有变更统一格式写入 Kafka，下游消费者只需订阅 Topic 即可获取实时数据流。

### 痛点放大

没有统一的 CDC 平台时，数据同步链路通常长这样：

> **MySQL binlog** → **Cron 脚本轮询** → **Python 解析 json** → **写入 Redis Queue** → **下游服务消费**

这套链路存在以下核心问题：
- **可靠性差**：脚本崩溃后无法从断点恢复，binlog position 靠人脑记忆
- **可维护性差**：每新增一张源表，需修改 3 个脚本的配置；DBA 和开发互相推诿"这是谁的锅"
- **Schema 变更噩梦**：MySQL DDL 之后，Python 脚本的解析逻辑立马罢工，凌晨发版修 Bug 是常态
- **无 Schema 契约**：上下游对数据格式没有统一约定，字段改名导致消费端异常，定位耗时数小时

而有了 Debezium 之后，链路变为：

> **MySQL binlog** → **Debezium Connector（自动解析）** → **Kafka Topic（结构化消息）** → **任意下游消费者**

任何 DML 操作在秒级内被捕获，Schema 变更自动同步到 Schema Registry，下游消费者收到结构化的 Avro/JSON 消息，无需关心中间实现细节。

---

## 2. 项目设计——三人对话

**（茶水间，下午三点，小胖拿着一包薯片走进来）**

**小胖**："大师、小白，救救我！我们运营团队要做个实时大屏，订单表在我的 MySQL 里，用户画像在隔壁组的 PostgreSQL 里，他们的 MongoDB 里还有用户的浏览日志。Leader 说要在同一个大屏上实时展示这三个数据源的聚合结果，数据延迟不能超过 5 秒。我现在脑子一团浆糊..."

**小白**（放下手中的《数据库系统实现》）："这不就是经典的 CDC 场景吗？用 Debezium 一把梭就行了。不过小胖你说的这个，三个数据源、三种数据库，确实有点复杂。Debezium 能做这个我知道，但具体怎么把三种不同数据库的变更统一起来，我还真没深究过原理。"

**大师**（端着马克杯走过来）："小胖这个问题提得很好。我先打个比方——你们可以把 Debezium 想象成一个超级物流中转中心。每家数据库厂商都有自己的方言（binlog、WAL、Oplog），Debezium 就像请了一群翻译官，分别派驻到每家数据库门口，实时把各种方言翻译成标准普通话（Kafka 消息），然后统一送到物流仓库（Kafka Topic）。下游的消费者只需要学会普通话，不用再管上游说的是什么方言。"

**小胖**："哦！就像美团外卖——不同的商家（MySQL/PG/MongoDB）各自出餐（产生变更），骑手（Debezium Connector）去取餐，送到我手里（Kafka Topic），我不管商家用什么锅炒菜，我只要拿到打包好的外卖就行！"

**小白**："但这个比喻有个坑。外卖骑手可能迟到，那 Debezium 怎么保证变更数据不丢失？而且如果 '商家'（数据库）中途换了菜单结构（DDL），'骑手'要怎么处理？"

**大师**："好问题。先说延迟——Debezium 不是定时轮询，而是**实时监听**数据库的事务日志。以 MySQL 为例，它就像一个影子消费者，直接连到 binlog 的尾部，数据库每提交一个事务，它立刻收到通知，延迟通常在毫秒到秒级。再说可靠性——Debezium 用 **Offset** 机制记录读到 binlog 的哪个位置，就像我在读一本书时夹了个书签。即使 Connector 崩溃重启，只要 Kafka 中保存的 offset 还在，它就能从书签位置继续读，一条数据也不会丢。"

**大师**（补充道）："技术映射：Offset 就是 'binlog 的书签'，存储了 binlog 文件名 + 位点（position），或者 GTID（全局事务 ID）。这个 offset 信息持久化在 Kafka 的 `connect-offsets` Topic 中，而不是 Connector 的内存里，所以即使整个 Connect 集群宕机，offset 也不会丢失。"

**小胖**："那我的 PostgreSQL 和 MongoDB 怎么办？也是类似的原理？"

**大师**："原理类似但实现不同。PostgreSQL 用的是 **WAL（Write-Ahead Log）+ 逻辑复制槽**，MongoDB 用的是 **Oplog**。我先画个架构图——"

```
┌──────────┐  ┌──────────────┐  ┌───────────┐
│  MySQL   │  │  PostgreSQL  │  │  MongoDB  │
│ (binlog) │  │    (WAL)     │  │  (Oplog)  │
└────┬─────┘  └──────┬───────┘  └─────┬─────┘
     │               │                │
     ▼               ▼                ▼
┌───────────────────────────────────────────┐
│         Kafka Connect (Worker 集群)        │
│  ┌─────────┐ ┌──────────┐ ┌───────────┐  │
│  │  MySQL  │ │    PG    │ │  MongoDB  │  │
│  │Connector│ │Connector │ │ Connector │  │
│  └────┬────┘ └────┬─────┘ └─────┬─────┘  │
└───────┼───────────┼─────────────┼─────────┘
        │           │             │
        ▼           ▼             ▼
┌───────────────────────────────────────────┐
│            Apache Kafka Cluster           │
│  Topic: orders │ users │ behavior_logs   │
└──────────────┬────────────────────────────┘
               │
        ┌──────┴──────┐
        ▼             ▼
   ┌─────────┐  ┌──────────┐
   │ 实时大屏  │  │ 数据仓库  │
   └─────────┘  └──────────┘
```

**小白**："看到这个图我突然理解了——Kafka Connect 是统一的框架，不同的 Debezium Connector 只是这个框架上的插件。但大师，一个 Connector 启动后是怎么工作的？它会一直不停地读数据库的日志吗？"

**大师**："问到了核心。一个 Connector 的生命周期分两个阶段——**初始快照（Snapshot）和流式 CDC（Streaming）**。就像图书馆盘点：先花几个小时把现有藏书（已有数据）清点一遍，这个叫快照；之后每天只要关注新到的书和借出去的书（增量变更），这个叫流式 CDC。快照阶段完成之后，Connector 自动切换到流式模式，无缝衔接。"

**小胖**："那快照阶段数据库会不会被锁住啊？我们的订单表有几千万行，快照几个小时的话，订单系统不就瘫了？"

**大师**："这就是参数的艺术了。Debezium 提供了多种快照模式——`snapshot.mode=initial` 是默认的全量快照，`when_needed` 只在 offset 丢失时执行快照，`never` 跳过快照直接从当前位置开始读变更。对于大表，还可以使用**增量快照（Incremental Snapshot）**，把表按主键分段，一段一段地读，每段之间不持锁，业务完全无感。这个我们后面章节会详细实战。"

**小胖**："太强了！那我还有一个问题——数据库里的表结构经常改，比如加个字段或者删个字段，Debezium 能自动适配吗？"

**大师**："能，但需要配合 **Schema Registry**。每当表结构发生变更（DDL），Debezium 会生成一个新的 Avro Schema 版本并注册到 Schema Registry。下游消费者读到消息时，Schema Registry 会根据消息中的 Schema ID 自动查找对应版本的 Schema 进行反序列化。这就是所谓的 **Schema 契约**——上游产出的数据结构和下游消费的数据结构形成一份书面约定。"

**大师**："OK，今天的核心概念总结一下：CDC（变更数据捕获）→ Connector（连接器）→ Offset（读取位点）→ Snapshot（初始快照）→ Streaming（流式 CDC）→ Schema Registry（Schema 版本管理）。这六个词构成 Debezium 的完整词汇表。下一章我们开始动手，用 Docker Compose 一键搭建全栈环境。"

---

## 3. 项目实战

### 环境准备

本章主要是概念讲解，实战部分以架构图绘制和术语理解为主。让我们先确保理解基础上的环境预检。

#### 步骤1：确认核心概念理解

**目标**：能够在白板上画出 Debezium 的六层架构并口头解释。

不要跳过这一步。拿出一张纸（或打开 Whiteboard 工具），尝试画出以下结构：

1. **数据源层**：MySQL（binlog）、PostgreSQL（WAL）、MongoDB（Oplog）
2. **Connector 层**：各数据库对应的 Debezium Connector，运行在 Kafka Connect Worker 中
3. **事件总线层**：Apache Kafka Cluster，每个表对应的变更 Topic
4. **Schema 管理层**：Confluent Schema Registry（或 Apicurio）
5. **消费者层**：实时大屏、数据仓库、缓存更新等下游应用
6. **管控层**：Debezium UI / REST API / Prometheus 监控

完成后，对着这幅图自言自语讲一遍数据流：MySQL 执行 `INSERT INTO orders VALUES(1, 'phone', 999)` → binlog 写入一条 Write_rows_event → MySQL Connector 的 BinlogClient 读到这个事件 → Connector 内部将事件转换为 Change Event → 序列化为 JSON/Avro → 发送到 Kafka Topic `dbserver1.inventory.orders` → Schema Registry 校验 Schema 兼容性 → 下游 Consumer 拉取消息并反序列化 → 实时大屏刷新。

#### 步骤2：术语卡片制作

**目标**：建立个人术语速查表。

在 Notion/语雀等工具中建立以下术语卡片（建议表格形式）：

| 术语 | 英文 | 一句话解释 | 类比 |
|------|------|-----------|------|
| 变更数据捕获 | Change Data Capture (CDC) | 实时监听并捕获数据库中的数据变更（增删改） | 图书馆的"新书上架 & 借阅登记"流水账 |
| 连接器 | Connector | 负责连接特定数据源并捕获变更的插件 | 派驻到不同商家的取餐骑手 |
| 变更事件 | Change Event | Debezium 产出的每一条 DML 操作的标准格式消息 | 一份标准化的快递包裹单 |
| 偏移量 | Offset | 记录 CDC 读取到数据库日志的哪个位置 | 读书时的书签 |
| 快照 | Snapshot | 全量导出表当前的数据状态 | 图书馆盘点现有藏书 |
| 单消息转换 | Single Message Transform (SMT) | 在消息投递前对事件内容做变形处理 | 快递打包前给包裹换包装 |
| Schema 注册中心 | Schema Registry | 统一管理消息的 Schema 版本与兼容性 | 合同模板管理中心 |
| Topic | Topic | Kafka 中用于存储消息的逻辑队列 | 快递分拣中心的货架 |

#### 步骤3：安装 Kafka 命令行工具预检

**目标**：确认本地环境已有 Kafka 客户端工具，为下一章环境搭建做准备。

```bash
# 检查 Java 版本（Debezium 2.7+ 需要 JDK 17）
java -version
# 预期输出：openjdk version "17.0.x" ...

# 创建工具目录（Windows 用户建议使用 WSL2 或 Git Bash）
mkdir -p ~/debezium-lab/bin
cd ~/debezium-lab/bin

# 下载 Kafka（如未安装）
wget https://downloads.apache.org/kafka/3.6.0/kafka_2.13-3.6.0.tgz
tar -xzf kafka_2.13-3.6.0.tgz
cd kafka_2.13-3.6.0

# 验证 Kafka 脚本可用
bin/kafka-topics.sh --version
# 预期输出：3.6.0
```

**常见坑**：
- Windows 原生环境下 Kafka 脚本路径分隔符问题：使用 WSL2 或 Git Bash，不要用 CMD
- JDK 版本不匹配：Debezium 2.5+ 要求 JDK 17，JDK 11 会导致 `java.lang.UnsupportedClassVersionError`
- 部分公司内网无法直接下载 Kafka，可预先下载好放入工具目录

---

## 4. 项目总结

### 优点 & 缺点

| 维度 | Debezium | 自研脚本方案 | 时间戳轮询方案 |
|------|---------|-------------|---------------|
| 数据实时性 | ★★★★★ 毫秒级 | ★★☆☆☆ 分钟级 | ★☆☆☆☆ 依赖轮询间隔 |
| 可靠性 | ★★★★★ offset 持久化 | ★★☆☆☆ 依赖人工管理 | ★☆☆☆☆ 易丢失 |
| 多数据源支持 | ★★★★★ 统一框架 | ★★☆☆☆ 每种数据库独立脚本 | ★☆☆☆☆ 需适配不同库 |
| Schema 管理 | ★★★★★ Schema Registry | ★☆☆☆☆ 无 | ★☆☆☆☆ 无 |
| 运维成本 | ★★★★☆ 需维护 Kafka 集群 | ★★☆☆☆ 脚本维护成本高 | ★★★☆☆ 相对简单 |
| 部署复杂度 | ★★★☆☆ 组件较多 | ★★★★☆ 脚本简单 | ★★★★★ 极简 |
| 学习曲线 | ★★★☆☆ 概念较多 | ★★★★☆ 简单直接 | ★★★★★ 极简 |

### 适用场景

1. **多系统数据同步**：ERP、CRM、WMS 等多个业务系统之间的数据实时同步，替代传统的 ETL
2. **实时数据大屏**：订单量、GMV、库存水位等多维度指标的实时聚合展示
3. **缓存失效**：MySQL 数据变更后自动刷新 Redis 缓存，替代代码中的缓存更新逻辑
4. **审计日志**：自动记录所有数据变更的历史轨迹，无需业务代码介入
5. **搜索引擎同步**：MySQL 变更实时推送到 Elasticsearch，保持搜索索引的新鲜度

### 不适用场景

1. **简单的单表备份**：如果只是每天备份一次数据，直接用 `mysqldump` 比搭整套 CDC 管道轻量得多
2. **报表类 OLAP 查询**：Debezium 是数据传输工具，不是查询引擎，实时宽表查询应使用 ClickHouse/Doris 等 OLAP 引擎

### 注意事项

- **版本兼容性**：Debezium 2.7 + Kafka 3.6 + MySQL 8.0 是推荐的组合，不要随意跨版本混搭
- **安全边界**：Debezium Connector 需要数据库的 `REPLICATION` 权限，不要用 `root` 账号，创建专门的 `debezium` 用户并最小化授权
- **网络规划**：Kafka Connect 需要同时连通数据库和 Kafka Broker，两者通常在不同网段，提前规划安全组规则

### 常见踩坑经验

1. **"我的 MySQL 明明开启了 binlog，Connector 还是报错"**：检查 `binlog_format` 是否为 ROW（不是 STATEMENT 或 MIXED），检查 `binlog_row_image` 是否为 FULL（不是 MINIMAL）
2. **"Topic 里没有数据，但 Connector 状态是 RUNNING"**：检查 Connector 配置中的 `table.include.list` 格式是否正确，正确格式为 `database.table`（如 `mydb.orders`），不是 `database.*`
3. **"Schema Registry 部署后 Connector 启动失败"**：检查 `key.converter` 和 `value.converter` 是否配置为 `io.confluent.connect.avro.AvroConverter`，并且 `schema.registry.url` 是否正确可达

### 思考题

1. 如果一个 MySQL 实例有 50 个库、每个库有 100 张表，总共 5000 张表需要 CDC 同步，你会用一个 Connector 还是多个 Connector？请从故障隔离、资源消耗、运维复杂度三个维度分析利弊。（提示：参考 Kafka Connect 的 Task 并行模型）

2. 在 Schema Registry 中，如果一张表的 Schema 已经从 v1 演进到了 v5，此时一个新的消费者从 Kafka offset 为 0 开始消费（最早的 offset 对应 v1 Schema），Schema Registry 能否正确帮助它反序列化所有历史消息？如果不能，你会如何设计兼容策略？

**答案将在下一章末尾给出。**

---

> **推广提示**：本章建议作为"CDC & Debezium 科普材料"分发给团队全体成员（开发、测试、运维、架构师），帮助统一术语体系。建议配合一次 30 分钟的午餐分享会，现场在白板上画出架构图。
