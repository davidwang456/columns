# Kafka 专栏章节索引（定稿）

本索引落实「30～40 章」范围：**正文 35 章 + 扩展 3 章 = 38 章**。扩展章（36～38）与中级/高级主题衔接，可按团队节奏选发。

| 编号 | 文件名 | 级别 | 主题 |
|---:|---|---|---|
| 01 | [第 1 章：Kafka 是什么——消息队列还是日志？.md](<chapters/第 1 章：Kafka 是什么——消息队列还是日志？.md>) | 基础 | Kafka 是什么：消息队列还是日志？ |
| 02 | [第 2 章：本地环境与第一条消息——Broker、Topic、CLI.md](<chapters/第 2 章：本地环境与第一条消息——Broker、Topic、CLI.md>) | 基础 | 本地环境与第一条消息：Broker/Topic/CLI |
| 03 | [第 3 章：Topic 与 Partition——并行与有序边界.md](<chapters/第 3 章：Topic 与 Partition——并行与有序边界.md>) | 基础 | Topic 与 Partition：并行与有序边界 |
| 04 | [第 4 章：副本与可用性入门——Replication Factor 与 min.insync.replicas.md](<chapters/第 4 章：副本与可用性入门——Replication Factor 与 min.insync.replicas.md>) | 基础 | 副本与可用性入门：Replication Factor |
| 05 | [第 5 章：Producer 基础——发送、Key、Headers.md](<chapters/第 5 章：Producer 基础——发送、Key、Headers.md>) | 基础 | Producer 基础：发送、Key、Headers |
| 06 | [第 6 章：Consumer 基础——poll、提交位移.md](<chapters/第 6 章：Consumer 基础——poll、提交位移.md>) | 基础 | Consumer 基础：poll、提交位移 |
| 07 | [第 7 章：Consumer Group：组内负载与再均衡入口.md](<chapters/第 7 章：Consumer Group：组内负载与再均衡入口.md>) | 基础 | Consumer Group：组内负载与再均衡入口 |
| 08 | [第 8 章：位移与 Offset：`__consumer_offsets`.md](<chapters/第 8 章：位移与 Offset：`__consumer_offsets`.md>) | 基础 | 位移与 Offset：`__consumer_offsets` |
| 09 | [第 9 章：消息格式与序列化：String／JSON 与演进痛点.md](<chapters/第 9 章：消息格式与序列化：String／JSON 与演进痛点.md>) | 基础 | 消息格式与序列化：String/JSON 与演进痛点 |
| 10 | [第 10 章：运维视角：核心指标与日志从哪里看.md](<chapters/第 10 章：运维视角：核心指标与日志从哪里看.md>) | 基础 | 运维视角：核心指标与日志从哪里看 |
| 11 | [第 11 章：测试视角：最小集成测试与数据准备.md](<chapters/第 11 章：测试视角：最小集成测试与数据准备.md>) | 基础 | 测试视角：最小集成测试与数据准备 |
| 12 | [第 12 章：安全初探：PLAINTEXT 到 SSL／SASL 路线图.md](<chapters/第 12 章：安全初探：PLAINTEXT 到 SSL／SASL 路线图.md>) | 基础 | 安全初探：PLAINTEXT 到 SSL/SASL 路线图 |
| 13 | [第 13 章：生产端调优：batch、linger、compression.md](<chapters/第 13 章：生产端调优：batch、linger、compression.md>) | 中级 | 生产端调优：batch、linger、compression |
| 14 | [第 14 章：acks、重试、幂等与乱序：交付语义实践.md](<chapters/第 14 章：acks、重试、幂等与乱序：交付语义实践.md>) | 中级 | acks、重试、幂等与乱序：交付语义实践 |
| 15 | [第 15 章：消费者再均衡深入：partition 分配与 STW.md](<chapters/第 15 章：消费者再均衡深入：partition 分配与 STW.md>) | 中级 | 消费者再均衡深入：partition 分配与 STW |
| 16 | [第 16 章：消费滞后（Lag）与热点排查：线程模型.md](<chapters/第 16 章：消费滞后（Lag）与热点排查：线程模型.md>) | 中级 | 消费滞后（Lag）与热点排查：线程模型 |
| 17 | [第 17 章：日志留存与清理：retention、compact topic.md](<chapters/第 17 章：日志留存与清理：retention、compact topic.md>) | 中级 | 日志留存与清理：retention、compact topic |
| 18 | [第 18 章：Exactly-once（EOS）入门：事务型 Producer.md](<chapters/第 18 章：Exactly-once（EOS）入门：事务型 Producer.md>) | 中级 | Exactly-once（EOS）入门：事务型 Producer |
| 19 | [第 19 章：Kafka Connect：Source／Sink 与错误处理.md](<chapters/第 19 章：Kafka Connect：Source／Sink 与错误处理.md>) | 中级 | Kafka Connect：Source/Sink 与错误处理 |
| 20 | [第 20 章：Kafka Streams：状态存储与交互式查询入门.md](<chapters/第 20 章：Kafka Streams：状态存储与交互式查询入门.md>) | 中级 | Kafka Streams：状态存储与交互式查询入门 |
| 21 | [第 21 章：Schema Registry：Avro／Protobuf／JSON Schema.md](<chapters/第 21 章：Schema Registry：Avro／Protobuf／JSON Schema.md>) | 中级 | Schema Registry：Avro/Protobuf/JSON Schema |
| 22 | [第 22 章：监控体系：JMX／Prometheus 指标清单.md](<chapters/第 22 章：监控体系：JMX／Prometheus 指标清单.md>) | 中级 | 监控体系：JMX/Prometheus 指标清单 |
| 23 | [第 23 章：集群运维：滚动升级、参数变更、扩缩容.md](<chapters/第 23 章：集群运维：滚动升级、参数变更、扩缩容.md>) | 中级 | 集群运维：滚动升级、参数变更、扩缩容 |
| 24 | [第 24 章：多集群与灾备入门：MirrorMaker／Cluster Linking.md](<chapters/第 24 章：多集群与灾备入门：MirrorMaker／Cluster Linking.md>) | 中级 | 多集群与灾备入门：MirrorMaker/Cluster Linking |
| 25 | [第 25 章：Controller 与元数据：从 ZK 到 KRaft 迁移叙事.md](<chapters/第 25 章：Controller 与元数据：从 ZK 到 KRaft 迁移叙事.md>) | 高级 | Controller 与元数据：从 ZK 到 KRaft 迁移叙事 |
| 26 | [第 26 章：KRaft 深入：controller quorum 与故障域.md](<chapters/第 26 章：KRaft 深入：controller quorum 与故障域.md>) | 高级 | KRaft 深入：controller quorum 与故障域 |
| 27 | [第 27 章：副本与 ISR：HW／LEO、同步机制.md](<chapters/第 27 章：副本与 ISR：HW／LEO、同步机制.md>) | 高级 | 副本与 ISR：HW/LEO、同步机制 |
| 28 | [第 28 章：Log 存储：segment、index、时间索引.md](<chapters/第 28 章：Log 存储：segment、index、时间索引.md>) | 高级 | Log 存储：segment、index、时间索引 |
| 29 | [第 29 章：网络层与请求处理：线程模型、背压.md](<chapters/第 29 章：网络层与请求处理：线程模型、背压.md>) | 高级 | 网络层与请求处理：线程模型、背压 |
| 30 | [第 30 章：事务与 EOS 内核：事务日志、协调器交互.md](<chapters/第 30 章：事务与 EOS 内核：事务日志、协调器交互.md>) | 高级 | 事务与 EOS 内核：事务日志、协调器交互 |
| 31 | [第 31 章：Consumer Group Coordinator：心跳、会话、再均衡协议.md](<chapters/第 31 章：Consumer Group Coordinator：心跳、会话、再均衡协议.md>) | 高级 | Consumer Group Coordinator：心跳、会话、再均衡协议 |
| 32 | [第 32 章：性能压测方法论：瓶颈分层与报告模板.md](<chapters/第 32 章：性能压测方法论：瓶颈分层与报告模板.md>) | 高级 | 性能压测方法论：瓶颈分层与报告模板 |
| 33 | [第 33 章：JVM／OS 调优：GC、页缓存、文件描述符.md](<chapters/第 33 章：JVM／OS 调优：GC、页缓存、文件描述符.md>) | 高级 | JVM/OS 调优：GC、页缓存、文件描述符 |
| 34 | [第 34 章：源码导读：Producer 发送路径.md](<chapters/第 34 章：源码导读：Producer 发送路径.md>) | 高级 | 源码导读：Producer 发送路径 |
| 35 | [第 35 章：源码导读：Broker 写入路径 ／ Log append.md](<chapters/第 35 章：源码导读：Broker 写入路径 ／ Log append.md>) | 高级 | 源码导读：Broker 写入路径 / Log append |
| 36 | [第 36 章：Kafka Streams 进阶：repartition、容错.md](<chapters/第 36 章：Kafka Streams 进阶：repartition、容错.md>) | 扩展 | Kafka Streams 进阶：repartition、容错 |
| 37 | [第 37 章：安全深化：ACL、KMS.md](<chapters/第 37 章：安全深化：ACL、KMS.md>) | 扩展 | 安全深化：ACL、KMS |
| 38 | [第 38 章：Testcontainers、契约测试与混沌／灾备演练.md](<chapters/第 38 章：Testcontainers、契约测试与混沌／灾备演练.md>) | 扩展 | Testcontainers、契约测试与混沌/灾备演练 |

## 与干系人确认的结论（落地版）

- **章节数量**：采用 **38 章**（满足 30～40），其中 **36～38 为扩展篇**，可独立排期。
- **读者**：开发 / 运维 / 测试共用大纲；单章「总结」固定包含优点、缺点、场景、注意事项、踩坑。
- **独立成篇**：每章文件自包含背景、对话、实战、总结；交叉引用仅作延伸阅读。
