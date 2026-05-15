# Debezium CDC 实战修炼专栏大纲

> 版本：Debezium 2.7+ / Kafka 3.6+
> 面向人群：开发、运维、测试、架构师
> 总章节：40 章（基础篇 16 章 / 中级篇 15 章 / 高级篇 9 章）
> 每章独立成文件，字数 3000-5000 字

---

## 专栏定位

以 Debezium 2.7 官方源码与生态工具为骨架，从 CDC 核心概念到多数据源实战，从 Kafka Connect 架构到源码剖析，从性能调优到实时数据中台落地，全链路贯通。每一章均采用「业务痛点 → 三人剧本对话 → 代码实战 → 总结思考」的四段式结构，实战为主、理论为辅，兼顾趣味性、实战性与深度。

---

## 阅读路线建议

| 角色 | 建议阅读顺序 | 重点章节 |
|------|-------------|---------|
| 新人开发/测试 | 基础篇全读 → 中级篇选读 | 第 1-16 章 |
| 核心开发/运维 | 基础篇速读 → 中级篇精读 → 高级篇选读 | 第 17-31、32-40 章 |
| 架构师/资深开发 | 高级篇为主线，按需回溯中级篇 | 第 32-40 章，辅以 17-31 章 |

---

## 角色设定

| 角色 | 性格标签 | 职责 | 话风示例 |
|------|---------|------|---------|
| 小胖 | 爱吃爱玩、不求甚解 | 用生活化比喻抛出问题，引发讨论 | "这不就跟食堂打饭排队一样吗？为啥要搞那么复杂？" |
| 小白 | 喜静、喜深入 | 追问原理、边界条件、风险、备选方案 | "那如果队头阻塞了怎么办？有没有比这更轻量的方案？" |
| 大师 | 资深技术 Leader | 讲透业务约束与选型，由浅入深打比方 | "你可以把连接池想象成银行柜台——开几个窗口既要满足客流，又不能浪费人力。" |

---

# 基础篇（第 1-16 章）

> **核心目标**：建立 CDC 核心概念，掌握 Debezium 单机部署、主流数据源接入与初级运维。
> **技术关联**：Kafka Connect 框架、MySQL/PostgreSQL/MongoDB Connector、Avro 序列化。

---

## 第1章：Debezium 术语全景与 CDC 工作原理
**定位**：专栏总览与开篇，建立统一语系。
**核心内容**：
- 术语词典：CDC、Change Event、Connector、Task、Offset、Schema History、Snapshot、Topic、SMT
- CDC 的四种实现方式对比：基于时间戳、基于触发器、基于日志、基于快照
- Debezium 在 Kafka Connect 生态中的位置（含架构图）
- 变更事件生命周期：捕获 → 序列化 → 路由 → 投递
- 技术栈全景图：Kafka → Kafka Connect → Debezium Connector → 下游消费者
**实战目标**：基于架构图，向团队讲清楚 Debezium 的整个数据流链路。

---

## 第2章：环境搭建——Docker Compose 一键部署全栈
**定位**：从零到一，30 分钟内拥有一个可用的 CDC 实验环境。
**核心内容**：
- Docker Compose 编排：Zookeeper → Kafka → Kafka Connect → MySQL → Debezium Connector
- 各组件端口、网络、数据卷的规划与配置
- 关键环境变量（CONNECT_BOOTSTRAP_SERVERS、CONNECT_GROUP_ID 等）详解
- Kafka 基础验证：Topic 创建、Producer/Consumer 命令行工具使用
- MySQL 开启 binlog 的必要配置（server-id、binlog_format=ROW、binlog_row_image=FULL）
**实战目标**：一键启动 6 个容器的全栈环境，验证 Kafka 消息收发正常，MySQL binlog 写入正常。

---

## 第3章：MySQL Connector 入门——捕获第一条 CDC 记录
**定位**：最基础也是最核心的 Connector，跑通第一条 "INSERT → Kafka Topic" 链路。
**核心内容**：
- MySQL Connector 注册：REST API（POST /connectors）方式与配置文件方式
- 核心参数解析：connector.class、database.hostname、database.server.id、database.include.list、table.include.list
- 启动后 Kafka 自动创建 Topic 的内部流程
- 消费第一条变更事件：kafka-console-consumer 查看消息内容
- INSERT/UPDATE/DELETE 三种操作的事件结构对比
**实战目标**：在 MySQL 中创建业务表并执行 DML 操作，在 Kafka Consumer 中实时观察到对应的 Change Event。

---

## 第4章：数据变更事件（Change Event）格式深度解析
**定位**：读懂 Debezium 产出的每一条消息，是后续一切实战的基础。
**核心内容**：
- 事件公共结构：schema（field/type/optional/name）、payload（before/after/op/ts_ms/source/transaction）
- source 字段详解：db、table、server_id、file、pos、gtid、snapshot
- op 字段取值含义：c/u/d/r/truncate/m
- before vs after 字段：null 的四种语义（未知/未设/无/快照初始值）
- 主键变更事件：DELETE + INSERT 的隐含语义
**实战目标**：在测试库中对同一张表执行增/删/改操作，依次解析每种操作的 Change Event，画出事件结构图。

---

## 第5章：Connector 配置参数全解与最佳实践
**定位**：掌握每个关键参数背后的权衡，避免"糊里糊涂配错了还不知道"。
**核心内容**：
- 连接类参数：database.hostname、port、user、password、server.id、serverTimezone
- 过滤类参数：database.include.list / exclude、table.include.list / exclude、column.include.list / exclude
- 行为类参数：snapshot.mode、snapshot.locking.mode、snapshot.fetch.size、decimal.handling.mode
- 性能类参数：poll.interval.ms、max.queue.size、max.batch.size
- 参数优先级与覆盖规则：系统级 → Connector 级 → Task 级
**实战目标**：编写一份"Connector 参数自查清单"，对 30+ 个核心参数逐一标注作用、默认值与推荐值。

---

## 第6章：Snapshot 快照机制全解
**定位**：理解"初始同步"的四种模式与各自的适用场景，避免首次启动踩坑。
**核心内容**：
- 四种 snapshot.mode 对比：initial、when_needed、never、schema_only、initial_only、no_data
- 全局读锁 vs 表级锁 vs 无锁快照的实现路径
- snapshot.fetch.size 对快照速度的影响
- 快照中断后的恢复机制（offset 记录点）
- 大表快照常见问题：锁超时、事务日志膨胀、主从延迟加剧
**实战目标**：对一张 100 万行的表分别用 initial、when_needed、never 模式启动 Connector，对比行为差异。

---

## 第7章：Topic 路由与命名策略——多库多表数据分流
**定位**：从默认 Topic 到自定义路由，实现多租户数据隔离。
**核心内容**：
- 默认 Topic 命名规则：<serverName>.<databaseName>.<tableName>
- topic.prefix 与 database.server.name 的区别
- 自定义 Topic 路由：topic.Topic、topic.creation.default.replication.factor
- 逻辑表名映射：tableTopic → 多表数据汇入同一个 Topic
- 多租户场景：按租户 ID 拆分 Topic、按操作类型拆分 Topic
**实战目标**：部署 3 个 Connector 同时采集 source_a、source_b、source_c 三个数据库，各自路由到不同 Topic 前缀。

---

## 第8章：PostgreSQL Connector 实战
**定位**：掌握 PG 的 WAL 解码机制与 Debezium 的集成方式。
**核心内容**：
- PostgreSQL 逻辑复制原理：Write-Ahead Log（WAL）→ Replication Slot → pgoutput 插件
- PG 前序配置：wal_level=logical、max_replication_slots、max_wal_senders
- plugin.name 参数：pgoutput vs wal2json vs decoderbufs
- publication 与 replication slot 的手动管理与自动管理
- PG Schema 变更后的行为：新增列、删除列、改列类型的 CDC 表现
**实战目标**：在 PostgreSQL 中创建 publication，部署 Debezium PG Connector，验证 JSON/JSONB 类型字段的捕获正确性。

---

## 第9章：MongoDB Connector 实战
**定位**：非关系型数据库的 CDC 方案，理解 Oplog 驱动的变更捕获。
**核心内容**：
- MongoDB Oplog 原理：Capped Collection、操作记录格式（i/u/d/c）
- MongoDB Connector 的前置条件：Replica Set 配置、Oplog 大小调整
- Shard 集群下的 Connector 部署策略
- 文档结构变更：嵌套字段的新增/删除如何体现在 Change Event 中
- MongoDB Connector 特有参数：capture.mode、snapshot.mode、field.exclude.list
**实战目标**：搭建 MongoDB Replica Set，部署 Connector，测试嵌套文档更新后的 CDC 事件格式。

---

## 第10章：单消息转换（SMT）基础篇——事件变形记
**定位**：SMT 是 Debezium 中最灵活的瑞士军刀，掌握常用的 5 个 SMT。
**核心内容**：
- SMT 概念：消息投递前（生产者端）的数据变形，无下游感知
- 常用 SMT 详解：
  - ExtractNewRecordState：去掉外层 Schema 包装，提取 after 字段
  - SetSchemaMetadata：修改 Schema 名称
  - Flatten：将嵌套结构拍平
  - Cast：字段类型转换
  - HeaderFrom：从字段提取到消息头
- SMT 链式组合："transforms=" 参数的多 SMT 串联
- SMT 顺序的重要性：先 ExtractNewRecordState 再 Cast 的正确姿势
**实战目标**：编写一条 SMT 链，将 MySQL 的原始 Change Event 转换为下游微服务可直接消费的 JSON 格式。

---

## 第11章：Schema Registry 与 Avro 序列化实战
**定位**：从 JSON 到 Avro，实现 Schema 版本管理与数据契约。
**核心内容**：
- 为什么需要 Schema Registry：生产-消费 Schema 绑定、数据契约、存储节约
- Confluent Schema Registry 部署与配置（内置 Kafka Topic：_schemas）
- Debezium + Avro Converter 的完整配置链路
- Avro  vs JSON 的消息体积对比（实测数据）
- Schema 兼容性策略：BACKWARD、FORWARD、FULL、NONE 的语义与实践
**实战目标**：部署 Schema Registry，开启 Avro 序列化，对比同一批数据 JSON vs Avro 的磁盘与带宽占用。

---

## 第12章：Debezium UI——可视化连接器管理
**定位**：告别命令行，用 Web 界面轻松管理 Connector 生命周期。
**核心内容**：
- Debezium UI 部署（Docker / 嵌入式两种方式）
- Connector 创建向导：从 UI 上一步步填写参数并启动 Connector
- 实时监控面板：Connector 状态、Task 分布、Lag 柱状图、错误日志
- Connector 运维操作：暂停、重启、重新配置、删除
- UI 的权限模型与多用户场景
**实战目标**：通过 Debezium UI 创建、暂停、修改并重启一个 MySQL Connector，全程零命令行。

---

## 第13章：Connector 状态机与故障排查初级手册
**定位**：理解 Connector 的状态流转，建立故障排查 SOP。
**核心内容**：
- 状态机：UNASSIGNED → RUNNING → PAUSED → FAILED → DESTROYED
- Task 状态独立于 Connector 状态：一个 Task 失败不意味着 Connector 失败
- 常见启动失败场景及根因：
  - binlog 未开启 / 格式错误
  - 数据库权限不足（REPLICATION CLIENT、SELECT、RELOAD）
  - server.id 冲突
  - Kafka Topic 创建权限问题
- 排查工具链：GET /connectors/{name}/status、GET /connectors/{name}/tasks/{id}/status、Kafka Connect 日志
- 故障恢复：手动重启 Connector vs 自动重启策略
**实战目标**：人为制造 5 种常见配置错误，观察 Connector 状态变化并写出排查步骤。

---

## 第14章：Debezium Server——轻量级独立进程模式
**定位**：当不想引入 Kafka 时，Debezium Server 是轻量替代方案。
**核心内容**：
- Debezium Server vs Kafka Connect 模式的架构差异
- 支持的 Sink 类型：HTTP Client、Redis Stream、Apache Pulsar、Amazon Kinesis、Google Cloud Pub/Sub、NATS
- application.properties 配置全解（debezium.source.* + debezium.sink.*）
- HTTP Sink 实战：变更事件直推下游 REST API
- Redis Stream Sink 实战：用 Redis 做消息暂存
**实战目标**：用 Debezium Server 将 MySQL 变更数据实时推送到 Redis Stream，并用 xread 命令消费验证。

---

## 第15章：日常运维与监控入门
**定位**：从"能跑"到"稳跑"，建立日常运维 Checklist。
**核心内容**：
- JMX 指标监控：jmx_prometheus_javaagent + Prometheus 指标暴露
- 关键 REST API：
  - GET /connectors → 全量 Connector 列表
  - GET /connectors/{name}/status → 运行状态
  - PUT /connectors/{name}/config → 在线修改配置
  - DELETE /connectors/{name} → 安全删除
- Kafka 侧运维：Topic 保留策略、数据清理、分区扩容
- Connector 冷热备份策略：配置导出（JSON 文件）+ 定期快照存储
- 日志配置：log4j 级别调整、关键日志关键字告警（闪断、重连）
**实战目标**：编写一份 Connector 日常运维 Checklist，覆盖启动检查、运行巡检、周报产出、故障应急 4 个维度。

---

## 第16章：【基础篇综合实战】电商订单多数据源 CDC 流水线
**定位**：融会贯通基础篇全部知识。
**核心内容**：
- 业务场景：某电商系统，订单数据在 MySQL，用户画像在 PostgreSQL，行为日志在 MongoDB
- 需求拆解：三个 Connector 同时运行 → 统一 Kafka Topic 路由 → Avro 序列化 → SMT 变形 → 下游多消费者
- Docker Compose 编排：MySQL + PostgreSQL + MongoDB + Zookeeper + Kafka + Kafka Connect + Debezium + Schema Registry + Debezium UI
- 分步实现：环境搭建 → Connector 部署 → SMT 配置 → Schema Registry 集成 → 数据验证
- 验收标准：三张源表任意 DML 操作，10 秒内下游 Consumer 收到对应 Change Event，格式符合约定
- 全链路压测：1000 QPS 写入下无丢数据、无死锁、Lag < 1000
**最终交付物**：一份可复现的 docker-compose.yml + Connector 配置 JSON 集合 + 验证脚本

---

# 中级篇（第 17-31 章）

> **核心目标**：掌握分布式场景下的架构设计、性能调优、可观测性与云原生实践。
> **技术关联**：Kafka Connect 分布式 Worker、增量快照、事务边界、Prometheus/Grafana、K8s。

---

## 第17章：事务元数据与边界处理
**定位**：端到端的事务一致性是 CDC 的核心价值。
**核心内容**：
- 事务元数据字段：transaction.id、transaction.status（BEGIN/END）、transaction.data_collection_order
- parameter: provide.transaction.metadata 开启后的行为变化
- 多表事务的 Change Event 排序逻辑：按事务提交时间（commit timestamp）保证顺序
- 下游消费端的幂等设计：利用 transaction.id 实现去重
- 长事务（> 10 万行）对消息管道的影响与处理策略
**实战目标**：模拟一个跨 3 张表的事务操作，验证下游 Consumer 能完整接收到 BEGIN → 变更事件 → END 的事务边界。

---

## 第18章：高级 Topic 路由——按表/按库/按操作类型分流
**定位**：从粗略路由到精细路由，实现数据的精准分发。
**核心内容**：
- ContentBasedRouter SMT：基于事件内容的条件路由（按 op 类型、按字段值）
- RegexRouter SMT：基于正则表达式的 Topic 重命名
- Topic Routing 决策树：database.route → table.route → operation.route
- 路由规则组合实战：INSERT 到 topic.orders.crud.insert，DELETE 到 topic.orders.crud.delete
- 多 Topic 消费者的偏移量管理（独立 Topic 的消费进度各自维护）
**实战目标**：为订单表实现"INSERT 和 UPDATE 走一条 Topic，DELETE 走另一条 Topic"的路由规则。

---

## 第19章：高级 SMT 实战——字段过滤、类型转换与表达式路由
**定位**：SMT 进阶，掌握组合拳解决复杂数据变形需求。
**核心内容**：
- Filter.Out：按条件丢弃不符合条件的变更事件
- ValueToKey：从事件字段提取 Kafka 消息 Key（实现同 Key 消息顺序保证）
- ExtractField.Out：只提取特定字段（如只传 after 中的某列）
- ApplyTransform：基于 JSON Path / JEXL 表达式的复杂字段计算
- HoistField：将嵌套字段提升为顶层字段
- SMT 组合实战：先 Filter 过滤敏感数据 → ExtractNewRecordState 拍平 → ValueToKey 提取分区键 → Cast 转换类型
**实战目标**：为一个用户信息表配置 SMT 链：去除 deleted_at 不为空的软删除记录 → 提取 email 字段为 Key → 隐藏 password 字段。

---

## 第20章：Schema 演进与兼容性管理
**定位**：Schema 变更是 CDC 生产环境的第一大痛点，必须掌握三大兼容策略。
**核心内容**：
- Schema 变更的类型：新增列、删除列、修改列类型、修改列顺序、重命名列
- Avro 兼容性策略详析：
  - BACKWARD：新 Schema 可读取旧数据
  - FORWARD：旧 Schema 可读取新数据
  - FULL：双向兼容
  - NONE：无兼容性要求
- Schema History Topic：记录每次 DDL 变更的历史 Schema
- Schema 变更时 Connector 的自动处理 vs 手动介入
- 常见 Schema 变更的踩坑经验（删列导致旧事件无法反序列化）
**实战目标**：在 MySQL 表中依次执行新增列、修改列类型、删除列三种 DDL，观察 Schema Registry 中的 Schema 版本变化与 Connector 行为。

---

## 第21章：分布式 Worker 与 Connector 任务调度
**定位**：理解 Kafka Connect 的分布式骨络，才能驾驭多 Connector 高可用部署。
**核心内容**：
- Kafka Connect 分布式架构：Worker 节点、Leader 选举、Task 分配
- 配置参数：config.storage.topic、offset.storage.topic、status.storage.topic（三类内部 Topic）
- Rebalance 机制：Worker 加入/离开时的 Task 重新分配（增量协作式 Rebalance）
- REST API 的负载均衡：任意 Worker 都可接收请求，自动转发到 Leader
- Worker 故障对 Connector 的影响：Task 自动迁移、状态恢复
**实战目标**：部署 3 节点的 Kafka Connect 集群，手动停掉一个 Worker，观察 Task 的自动迁移过程。

---

## 第22章：信号表（Signaling）与通知渠道
**定位**：在不重启 Connector 的前提下，通过信号表动态控制 Connector 行为。
**核心内容**：
- 信号表的原理：Connector 轮询一张特殊表，读取信号并执行动作
- 五种信号类型：执行增量快照、日志水位推送、暂停/恢复快照、执行 DDL 变更、自定义信号
- 信号表 DDL 结构：(id, type, data, status, create_time)
- 通知渠道（Notification）：将 Connector 内部事件推送到 Kafka Topic / JMX / 日志
- 实战：通过 INSERT 到 signal 表触发增量快照
**实战目标**：创建 signal 表，通过插入一条信号记录触发增量快照，全程不重启 Connector。

---

## 第23章：增量快照（Incremental Snapshot）深度实战
**定位**：解决"初始快照锁表、大表快照耗时数小时"的痛点，掌握无锁增量同步技术。
**核心内容**：
- 增量快照 vs 初始快照的核心差异：不锁表、可分块、可恢复
- 增量快照原理：基于主键分段（Chunk）逐块读取，块之间不持锁
- signal 触发增量快照的详细流程：signal → 分块计算 → 逐块快照 → 完成后自动切入流式 CDC
- 增量快照参数：incremental.snapshot.chunk.size、incremental.snapshot.watermarking.strategy
- 大表增量快照实战：千万级表，分 1000 个 Chunk，不停机完成全量同步
**实战目标**：对一张 500 万行的表执行增量快照，监控 Chunk 执行进度，验证快照期间写入的数据不丢失。

---

## 第24章：跨库数据一致性——事务元数据 + 幂等消费
**定位**：在分布式事务场景下，保障 CDC 数据的精确一次语义。
**核心内容**：
- 基于 GTID（MySQL）/ LSN（PostgreSQL）的事务 ID 追踪
- 事务边界标识符：transaction.id 的全局唯一性保障
- 下游幂等消费策略：
  - 基于主键 + 版本号的 Upsert
  - 基于事务 ID 的去重表
  - 基于 Kafka 事务的精确一次写入
- 跨库事务场景：XA 事务、SAGA 模式下各参与者独立提交的 CDC 表现
- 一致性校验工具：使用 Debezium 的 verify 工具对比源表与目标表的行数/校验和
**实战目标**：搭建 MySQL → Kafka → 下游 PostgreSQL 的 CDC 同步链路，验证任意中断恢复后数据一致性。

---

## 第25章：性能调优实战——吞吐量翻倍的 10 个参数
**定位**：从"能用"到"好用"，用数据说话的性能调优实战。
**核心内容**：
- 调优维度：快照阶段性能 / 流式阶段性能 / 网络传输性能
- 10 个关键参数调优实验：
  - snapshot.fetch.size：快照批量读取行数（默认 2000，大表可调至 10000）
  - max.queue.size：内存队列大小（默认 8192，高吞吐调至 32768）
  - max.batch.size：每次 poll 最大事件数（默认 2048，高吞吐调至 8192）
  - poll.interval.ms：轮询间隔（默认 500ms，低延迟场景调至 100ms）
  - tombstones.on.delete：DELETE 后是否发送墓碑消息
  - heartbeat.interval.ms：心跳间隔
  - max.in.flight.requests：生产者未确认请求数
  - compression.type：消息压缩类型
  - topic.creation.default.partitions：Topic 分区数
  - database.initial.statements：连接时执行 SQL
- 调优前后性能对比实验：使用 JMH / Sysbench 工具压测，产出调优报告
**实战目标**：对一个 1000 万行的表进行全量快照 + 持续写入，对比默认配置与调优配置的快照耗时与 Lag。

---

## 第26章：多租户与大规模 Connector 治理
**定位**：100+ Connector 场景下的资源编排与自动化治理。
**核心内容**：
- 大规模 Connector 的挑战：资源竞争、命名冲突、运维复杂度指数级增长
- 多租户隔离策略：按 Connector Group 隔离 Kafka Consumer Group
- Kafka Connect Worker 资源规划：Connector / Task 数量与 CPU/内存的映射关系
- Connector 自动化部署：使用 Ansible / Terraform / K8s Operator 批量管理
- 命名规范与配置模板化：connector.{tenant}.{env}.{db}.{table} 的四级命名体系
- 大规模场景下的监控策略：按租户粒度配置 Grafana 面板
**实战目标**：编写一个 Ansible Playbook，支持批量部署 50+ Connector，按租户生成配置并统一管理。

---

## 第27章：高可用与故障恢复
**定位**：生产环境的最后一道防线，保证 CDC 链路的 99.99% 可用性。
**核心内容**：
- Kafka Connect 集群 HA：多 Worker → Leader 选举 → Task 自动 rebalance
- Replication Slot 保护：防止 slot 膨胀导致 WAL 磁盘耗尽
- 连接断开恢复：database.history.kafka.recovery.* 参数详解
- Offset 丢失恢复策略：从快照重做 vs 基于 GTID/LSN 的增量恢复
- 跨机房容灾：主备 Kafka Connect 集群的切换 SOP
- 灾难恢复演练：主集群故障 → 备集群接管 → 数据丢失量评估
**实战目标**：模拟 Kafka Connect Worker 宕机 + 数据库重启的复合故障，验证自动恢复流程与 RPO。

---

## 第28章：Prometheus + Grafana 可观测性体系
**定位**：从黑盒到白盒，搭建 Debezium 专属监控大盘。
**核心内容**：
- JMX 指标暴露：jmx_prometheus_javaagent 配置，关键指标映射
- Debezium 核心监控指标：
  - Connector 级：MilliSecondsSinceLastEvent、TotalNumberOfEventsSeen、SnapshotCompleted/Aborted
  - Task 级：StreamingMilliSecondsBehindSource、QueueRemainingCapacity、EventProcessingTime
- Grafana 大盘设计：RED 方法（Rate / Errors / Duration）+ USE 方法（Utilization / Saturation / Errors）
- 告警规则设计：
  - StreamingMilliSecondsBehindSource > 60000（延迟超过 1 分钟）
  - Connector Status = FAILED
  - QueueRemainingCapacity < 10%
  - Worker 内存 > 80%
- 告警通知渠道：企业微信/钉钉/Slack Webhook
**实战目标**：搭建 Prometheus + Grafana 监控栈，导入 Debezium 专属大盘 JSON，配置 3 条核心告警。

---

## 第29章：Debezium on Kubernetes——Strimzi Operator 实战
**定位**：云原生时代的 Debezium 部署与运维范式。
**核心内容**：
- Strimzi Operator 原理：CRD（Kafka、KafkaConnect、KafkaConnector）模型
- 部署架构：Operator → Kafka Cluster → Kafka Connect Cluster → KafkaConnector 资源
- 声明式 Connector 管理：编写 KafkaConnector YAML，Operator 自动创建/更新/删除
- 基于 Annotations 的高级路由与网络策略
- 滚动升级策略：先升级 Kafka Connect 镜像 → 再升级 Connector 配置
- 存储与持久化：PersistentVolume 用于 offset 和 schema history
**实战目标**：在 K8s 集群中部署 Strimzi Operator，通过 YAML 文件声明并启动一个 MySQL Connector。

---

## 第30章：数据去敏与安全合规
**定位**：GDPR/等保/个人信息保护法要求下的 CDC 数据安全实践。
**核心内容**：
- 敏感数据分类：个人身份信息（PII）、金融数据、医疗数据
- 去敏策略选择：
  - 字段级脱敏：手机号中间 4 位星号、身份证前 4 + 后 4 保留
  - 字段级加密：AES 加密 + 下游解密
  - 字段级删除：column.exclude.list 直接排除敏感列
  - 字段级哈希：邮箱哈希化后保留去重能力
- SMT 脱敏实战：编写自定义 SMT 实现 AES 加密
- 审计日志：记录谁在何时访问了哪些 CDC 数据
- 数据生命周期管理：Kafka Topic 的敏感数据自动过期策略
**实战目标**：为员工表配置 SMT 链，对 salary 字段加密、对 phone 字段脱敏、对 ssn 字段直接排除。

---

## 第31章：【中级篇综合实战】企业级多机房 CDC 数据总线
**定位**：融会贯通中级篇知识，构建跨机房高可用的 CDC 基础设施。
**核心内容**：
- 业务场景：某跨国电商，主数据中心在华东，灾备中心在新加坡，需实时同步 200+ 张核心表
- 架构设计：
  - 华东 Kafka Connect 集群（6 Worker）+ 新加坡灾备集群（6 Worker）
  - 每机房独立 Kafka Cluster（3 Broker）→ 通过 MirrorMaker 2 跨机房复制
  - 多租户 Connector 隔离（按 BU 分配不同 Connect Group）
- 功能实现：
  - Connector 自动化部署（Ansible）+ 统一命名规范
  - 增量快照策略（新表上线不停机同步）
  - 数据去敏（SMT 链实现 PII 字段保护）
  - 跨机房数据一致性校验
  - Prometheus + Grafana 统一可观测性
- 验收标准：任意表延迟 < 5s，RPO < 10s，单机房故障自动切换时间 < 60s
- **最终交付物**：架构设计文档 + Ansible Playbook + Grafana 大盘 JSON + 故障演练报告

---

# 高级篇（第 32-40 章）

> **核心目标**：源码级理解 Debezium 的实现原理，掌握自定义 Connector/SMT 开发、极端场景优化与实时数据中台落地。
> **技术关联**：EmbeddedEngine 源码、Database Adapter 体系、Flink/ksqlDB 集成、Iceberg/Hudi 数据湖。

---

## 第32章：Debezium 架构全景与 EmbeddedEngine 源码剖析
**定位**：从源码视角理解 Connector 的运行引擎，建立完整的代码心智模型。
**核心内容**：
- 模块划分总览：debezium-core / debezium-connector-mysql / debezium-connector-postgres / debezium-connector-mongodb / debezium-server
- EmbeddedEngine 源码：initialize() → run() → stop() 三大生命周期
- 核心组件交互：
  - ChangeEventSourceCoordinator：协调 Snapshot 与 Streaming 两种事件源
  - RecordMaker：将数据库行变转换为 SourceRecord
  - OffsetContext：管理 source offset 的持久化与恢复
  - SchemaHistory：管理 DDL 历史的记录与回溯
- 线程模型：Source Task Thread → Producer Thread 的单线程设计
- 源码阅读路线图：debezium-core/src/main/java/io/debezium/embedded/EmbeddedEngine.java 为入口
**实战目标**：在 EmbeddedEngine 关键方法中插入日志，追踪一个 Change Event 从数据库 binlog 到 Kafka Topic 的完整生命周期。

---

## 第33章：MySQL Connector 源码——Binlog 解析与事件转换链路
**定位**：深入 MySQL Connector 源码，理解 binlog 位点管理 + 行变事件 + DDL 事件的全链路处理。
**核心内容**：
- MySQL Connector 源码包结构：io/debezium/connector/mysql/
- Binlog 读取层：
  - BinaryLogClient（binlog-client 库）：连接、认证、事件流订阅
  - ChainedReader：快照读取与 binlog 读取的切换机制
- 事件转换层：
  - EventDeserializer：binlog 事件 → RowChangedEvent
  - RecordMaker：RowChangedEvent → SourceRecord
  - SnapshotChangeEventSource：初始快照的逻辑实现
- GTID 跟踪：GtidSet 的结构、位点记录与恢复
- DDL 事件处理：AlterTableParser 如何解析并更新内存中的 Schema 快照
**实战目标**：在 EventDeserializer 中插入断点，跟踪一条 UPDATE 语句如何从 binlog bytes 变为 SourceRecord。

---

## 第34章：数据库适配器（Database Adapter）体系与新增数据库支持
**定位**：理解 Debezium 如何抽象不同数据库的共性，掌握新增数据库支持的通用方法论。
**核心内容**：
- Database Adapter 三层抽象：
  - Connection：数据库连接管理、查询执行
  - Snapshotter：快照策略实现
  - ChangeEventSource：变更事件捕获
- 核心接口与抽象类：
  - io.debezium.pipeline.EventDispatcher
  - io.debezium.pipeline.spi.ChangeEventSourceFactory
  - io.debezium.pipeline.source.snapshot.incremental.IncrementalSnapshotChangeEventSource
- 新增数据库支持的 Checklist：
  1. 实现 Connection 接口（JDBC / 原生驱动）
  2. 实现 Snapshotter（全量 vs 增量）
  3. 实现 ChangeEventSource（流式变更捕获）
  4. 实现 TypeConverter（数据库类型 → Avro Schema 类型）
  5. 注册 Module 并实现 connector.class SPI
- 以 SQL Server Connector 为例解析适配层的实现
**实战目标**：编写一份"新增数据库 Connector 的开发 Checklist"，以外部分析 Oracle LogMiner Connector 的实现路径验证 Checklist 的完整性。

---

## 第35章：自定义 SMT 开发实战——从接口到部署
**定位**：从 SMT 使用者升级为 SMT 开发者，掌握自定义数据变形能力。
**核心内容**：
- SMT 开发接口：Transformation<R extends ConnectRecord<R>>，需实现 configure() / apply()
- 开发步骤：
  1. 继承 BaseTransformation 抽象类
  2. 实现 apply(Schema, Struct value) 编译版本
  3. 注册 ServiceLoader（META-INF/services）
- 实战案例 1：开发 EncryptField SMT（对指定字段做 AES 加密）
- 实战案例 2：开发 GeoIPEnrich SMT（基于 IP 字段补充地理位置信息）
- 单元测试：使用 JUnit 5 + Testcontainers 编写 SMT 单元测试
- 部署：打包 JAR → 放入 Kafka Connect plugin.path → 重启 Worker → connector 配置中引用
- 调试技巧：transform 中打印日志、断点调试、与上下游 SMT 的链式协作验证
**实战目标**：开发、测试、部署一个 EncryptField SMT，实现对 phone 字段的 AES 加密。

---

## 第36章：自定义 Connector 开发实战——以 Redis Connector 为例
**定位**：从 Connector 使用者升级为 Connector 开发者，掌握完整的 Connector 开发生命周期。
**核心内容**：
- Connector 开发接口体系：
  - SourceConnector：start / stop / taskConfigs / taskClass / config / version
  - SourceTask：start / poll / stop / version
- Redis Connector 需求定义：监听 Redis 的 Write 命令（SET/DEL/HSET 等），作为 Source 投递到 Kafka
- 源码实现：
  1. RedisConnector 类：解析配置、管理 OffsetStorageReader
  2. RedisConnectorTask 类：启动 Redis Monitor 连接、poll 周期读取命令
  3. RedisCommandHandler：将 Redis 命令转换为 Change Event
  4. 配置类：RedisConnectorConfig（继承 CommonConnectorConfig）
- 打包与部署：Maven Shade Plugin → 生成 fat JAR → plugin.path 加载
- 验证与调优：压测 Redis 写入 10 万/s，观察 Connector 吞吐量
**实战目标**：开发一个最小可用的 Redis Source Connector，能够将 Redis SET 命令转换为 Change Event 写入 Kafka。

---

## 第37章：极端场景性能优化——百万 TPS 下的调优策略
**定位**：当数据量超过常规场景的阈值后，系统性地逐层突破性能瓶颈。
**核心内容**：
- 分层调优策略：
  - 数据库层：binlog_row_image=MINIMAL（减少事件体积）、sync_binlog 调整、双一参数权衡
  - Kafka Connect 层：JVM GC 优化（G1GC 配置）、task.max 调优、生产者参数（linger.ms / batch.size）
  - Kafka Broker 层：分区扩展、磁盘 IO 优化（SSD RAID10）、网络带宽保障
  - OS 层：tcp buffer 调整（net.core.rmem_max / wmem_max）、文件描述符上限（ulimit -n）
- 零拷贝（Zero Copy）在 Kafka 中的应用：sendfile 系统调用在消息传输中的体现
- 性能剖析工具：JFR（Java Flight Recorder）、async-profiler、JMH 微基准测试
- 火焰图定位热点函数：使用 async-profiler 生成火焰图，定位队列等待/网络 IO 瓶颈
- 端到端延迟优化：数据库 binlog 写入 → Connector 轮询 → Kafka 投递 → 消费者拉取的延迟分布
**实战目标**：使用 wrk/Sysbench 对 MySQL 以 10 万/s 速度写入，逐层调优，生成优化前后的延迟分布对比图与火焰图。

---

## 第38章：数据湖集成——Debezium → Kafka → Iceberg/Hudi
**定位**：CDC 是数据湖的"实时入湖引擎"，掌握 CDC 驱动的 Lakehouse 架构。
**核心内容**：
- CDC → 数据湖的价值：实时数仓、Time Travel、增量查询、SCD Type 2 自动生成
- Apache Iceberg 集成：
  - Debezium Change Event → Kafka → Iceberg Kafka Sink Connector
  - Iceberg 的 Upsert/Merge Into 对 CDC 的原生支持
  - 分区策略：按日期/小时分区 + 按业务字段 Z-order 排序
- Apache Hudi 集成：
  - Debezium → Kafka → Hudi DeltaStreamer
  - Hudi 的 COW（Copy on Write）与 MOR（Merge on Read）表类型选择
- 数据湖中的 Schema 管理：Iceberg 的 Schema Evolution 与 Debezium 的 Schema History 如何协同
- 实战：MySQL 订单表 → Kafka → Flink → Iceberg 的完整入湖链路
**实战目标**：搭建 MySQL → Debezium → Kafka → Flink → Iceberg 的 CDC 入湖链路，验证 Iceberg 表的 Upsert 查询和 Time Travel 能力。

---

## 第39章：实时数仓落地——Debezium + Flink/ksqlDB 流式 ETL
**定位**：CDC 数据的终极价值在数仓，掌握基于 CDC 的流式宽表构建与物化视图刷新。
**核心内容**：
- CDC 驱动的实时数仓架构：ODS → DWD → DWS → ADS 的分层流式构建
- Debezium + Flink CDC 的正确姿势：
  - Flink SQL 直接消费 Debezium Change Event（Debezium Format）
  - ChangLog Stream 的 Upsert Materialize 到下游数据库
  - 多流 Join 构建实时宽表（订单流 LEFT JOIN 用户流 LEFT JOIN 商品流）
- Debezium + ksqlDB：
  - ksqlDB 的 SOURCE CONNECTOR 直接集成 Debezium
  - CREATE STREAM / CREATE TABLE 的 DDL 与 SQL 变换
  - 物化视图（Materialized View）的自动增量刷新
- 实时数仓的 3 个关键指标：
  - 数据新鲜度（Data Freshness）：< 5 秒
  - 数据一致性：多流 Join 后的主键与引用完整性
  - 查询性能：ClickHouse/Doris/StarRocks 的物化视图优化
**实战目标**：基于 MySQL 的 orders + users + products 三表，用 Flink SQL 构建一张实时订单宽表，落盘到 ClickHouse。

---

## 第40章：【高级篇综合实战】金融级实时数据中台 CDC 底座
**定位**：融会贯通高级篇知识，产出可直接交付的生产级 CDC 基础设施。
**核心内容**：
- 业务场景：某金融科技公司，核心系统 300+ 张表分布于 8 个数据库，需构建统一的实时数据中台
- 架构设计：
  - 8 个数据库 × 对应 Connector → 统一 Kafka Cluster（3 AZ 各 3 Broker）
  - 自定义 SMT 实现 AES 加密（符合 PCI DSS 标准） + 敏感字段脱敏
  - 基于 GTID 的一致性校验服务（每小时一次，自动告警）
  - 实时数仓层：Flink 流式 ETL → ClickHouse 宽表 → Superset 实时大盘
  - 数据湖层：Iceberg 离线分区 + 全量快照
- 功能实现：
  - 使用 Ansible + Terraform 实现一键部署
  - Connector 配置模板化（环境变量驱动）
  - 全链路压测（MySQL 100 万/s 写入 → 端到端延迟 < 1s）
  - 故障模拟：单机房断电 → 自动切换灾备 → 数据一致性校验
- 验收标准：
  - 99.99% 可用性（年度宕机时间 < 53 分钟）
  - 端到端延迟 P99 < 1s
  - 数据一致性 > 99.999%
  - 单实例支持百万 TPS
- **最终交付物**：架构设计文档 + 自动化部署代码 + SMT 源码 + 监控大盘 JSON + 全链路压测报告 + 故障演练 SOP

---

# 附录与资源

## 附录 A：Debezium 版本选型与兼容性矩阵

| Debezium 版本 | Kafka 版本 | Java 版本 | MySQL | PostgreSQL | MongoDB |
|--------------|-----------|----------|-------|------------|---------|
| 2.7.x | 3.6+ | 17 | 8.0+ | 12-16 | 6.0+ |
| 2.6.x | 3.5+ | 17 | 8.0+ | 12-16 | 6.0+ |
| 2.5.x | 3.4+ | 17 | 8.0+ | 12-15 | 5.0-7.0 |
| 1.9.x | 2.8-3.3 | 11 | 5.7-8.0 | 10-14 | 4.0-6.0 |

## 附录 B：CDC 方案对比

| 方案 | 数据库支持 | 序列化 | 部署模式 | 社区活跃度 | 适用场景 |
|------|-----------|--------|---------|-----------|---------|
| Debezium | MySQL/PG/Mongo/Oracle/SQL Server/DB2/etc | Avro/JSON | Kafka Connect / Server | ★★★★★ | 企业级 CDC 平台 |
| Canal | MySQL only | Protobuf/JSON | 独立进程 | ★★★★ | 阿里体系 MySQL CDC |
| Maxwell | MySQL only | JSON | 独立进程 | ★★★ | 轻量 MySQL CDC |
| Oracle GoldenGate | Oracle + 异构数据库 | 自定义 | 独立进程 | ★★★★ | Oracle 为核心的异构同步 |

## 附录 C：排障速查手册（TOP 20 生产故障）

1. Connector FAILED → "Database history topic is missing"
2. Connector FAILED → "Access denied; you need REPLICATION CLIENT privilege"
3. PG Connector 堵塞 → WAL 磁盘满（Replication Slot 未消费）
4. MySQL Connector 延迟大 → binlog_row_image=FULL 导致消息体积过大
5. 快照阶段 MySQL 锁表 → snapshot.locking.mode 参数选择错误
6. ...（完整 20 条见各章"常见踩坑经验"汇总）

## 附录 D：推荐工具链

- 消息平台：Apache Kafka、Redpanda、Apache Pulsar
- 序列化：Confluent Schema Registry、Apicurio Registry
- 容器编排：Docker Compose、Kubernetes、Strimzi Operator
- 监控：Prometheus、Grafana、Jaeger
- 流处理：Apache Flink、ksqlDB、RisingWave
- 数据湖：Apache Iceberg、Apache Hudi、Delta Lake
- OLAP：ClickHouse、Apache Doris、StarRocks
- 压测工具：Sysbench、wrk、JMH

## 附录 E：思考题参考答案索引

- 基础篇思考题答案：见各章末尾
- 中级篇思考题答案：见各章末尾
- 高级篇思考题答案：见各章末尾

---

> **版权声明**：本专栏基于 Debezium 2.7 官方源码（Apache 2.0 License）编写，所有源码引用均遵循原许可证条款。
