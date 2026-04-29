# Flink CDC 专栏大纲（40章 · 实战驱动 · 由浅入深）

> **专栏定位**：以实战为主线、原理为辅线，覆盖从"零基础写第一个CDC程序"到"读源码、调优万级表、自研Connector"的全路径。
> **角色设计**：每章以"小胖（开球提问）→ 小白（质疑深挖）→ 大师（技术映射总结）"的三人对话驱动技术决策。

---

## 基础篇（第1–15章）—— 上手即实战，打通"单表CDC→多源写入"全流程

| 章节 | 主题 | 核心实战内容 |
|------|------|-------------|
| 第1章 | Flink核心术语与工作原理 | Flink架构图（JobManager/TaskManager/JobGraph）、有状态流处理、Checkpoint/Savepoint、Exactly-Once语义、事件时间与处理时间 |
| 第2章 | CDC技术概述与Flink CDC架构 | CDC概念（基于日志 vs 基于查询）、Flink CDC整体架构图、与Canal/Maxwell/DataX对比、适用场景矩阵 |
| 第3章 | 环境搭建：Docker Compose一键部署 | `docker-compose.yml`编排MySQL 8.0 + Kafka + Flink Standalone集群，验证环境连通性，Maven工程搭建 |
| 第4章 | 第一个Flink CDC程序：DataStream API | `FlinkCDC.java`代码逐行讲解，`DebeziumSourceFunction`配置项（hostname/port/tableList/startupOptions），控制台打印CDC数据 |
| 第5章 | 玩转Flink CDC SQL | `FlinkCDCWithSql.java`代码解读，`CREATE TABLE ... WITH ('connector'='mysql-cdc')`，Flink SQL CLI交互式查询 |
| 第6章 | MySQL Binlog深度解析与CDC配置 | Binlog三种格式（ROW/STATEMENT/MIXED）、GTID原理、server-id冲突处理、`scan.startup.mode`五种模式对比 |
| 第7章 | 深入理解Event模型 | `TableId`/`DataChangeEvent`（INSERT/UPDATE/DELETE/REPLACE）/`SchemaChangeEvent`结构图解，before/after数据对照 |
| 第8章 | Flink CDC数据源配置大全 | MySQL/PostgreSQL/Oracle/MongoDB/SQL Server五大数据源DDL模板，`debezium.*`透传参数，连接池与重连策略 |
| 第9章 | 数据路由实战 | `route`配置项：正则匹配源表→目标表映射、`ALL_MATCH` vs `FIRST_MATCH`模式、多级路由优先级 |
| 第10章 | 基础数据转换 | `projection`列裁剪与别名、`filter`条件过滤（`=`/`<>`/`IS NULL`/`AND/OR`）、计算列（`price * quantity`） |
| 第11章 | PostgreSQL CDC实战 | PG逻辑复制（`wal_level=logical`）、Publication/Slot配置、`decoderbufs`插件、JSONB类型处理 |
| 第12章 | MongoDB CDC实战 | MongoDB Change Stream原理、`resumeAfter`断点续传、嵌套文档打平策略、ObjectId类型映射 |
| 第13章 | Checkpoint与状态恢复 | Checkpoint配置（间隔/超时/对齐模式）、Savepoint手动触发与恢复、Debezium offset存储机制、`FlinkOffsetBackingStore` |
| 第14章 | 监控初探：日志、Metrics与Flink Web UI | Flink Web UI指标解读（Records Sent/Received、Backpressure、Checkpointing）、日志级别调整、`flink-cdc.yaml`全局配置 |
| 第15章 | **综合实战：MySQL实时数据双写** | 完整案例：MySQL订单表→同时写入Kafka（供下游消费）+ MySQL备库（灾备），含初始化全量同步→增量续接 |

---

## 中级篇（第16–30章）—— 架构进阶，搞定"企业级数据集成平台"

| 章节 | 主题 | 核心实战内容 |
|------|------|-------------|
| 第16章 | Pipeline YAML API：声明式数据集成 | 从DataStream/SQL过渡到YAML pipeline，完整YAML模板解析（`source`/`sink`/`route`/`transform`/`pipeline`），`flink-cdc.sh`提交 |
| 第17章 | Pipeline链路全解析 | `FlinkPipelineComposer.compose()`源码导读，Source→PreTransform→PostTransform→Partitioning→SchemaOperator→Sink六阶段拓扑图 |
| 第18章 | 增量快照原理与调优（FLIP-27） | `IncrementalSource`架构，`SnapshotSplit`（全量分块）+ `StreamSplit`（增量续接）混合读取，Watermark信号算法 |
| 第19章 | Chunk切分策略：亿级大表优化 | `MySqlChunkSplitter`源码分析，主键范围切分 vs 非主键表处理，`chunk-key-column`自定义切分列，切分大小与并行度调优 |
| 第20章 | Schema Evolution：DDL变更自动同步 | `schema.change.behavior`五种模式（IGNORE/LENIENT/TRY_EVOLVE/EVOLVE/EXCEPTION），`CreateTableEvent`/`AddColumnEvent`/`AlterColumnTypeEvent`处理流程 |
| 第21章 | 高级数据转换：UDF与表达式编译 | `UserDefinedFunction`生命周期（open/eval/close），`JaninoCompiler`运行时编译表达式，SQL表达式→Java字节码转换过程 |
| 第22章 | 多表Broadcast与宽表合并 | 多张源表→单张宽表的合并策略，主键对齐、字段冲突处理、`table-options`控制写入行为 |
| 第23章 | 数据湖集成：Flink CDC + Apache Iceberg | Iceberg Sink Pipeline Connector配置，Partition Evolution、Hidden Partitioning、Time Travel查询 |
| 第24章 | 数据湖集成：Flink CDC + Apache Paimon | Paimon CDC Ingestion，Primary Key Table与Append-Only Table，`auto-create`自动建表，Compaction策略 |
| 第25章 | OLAP实时写入：Doris / StarRocks | Doris `flink-cdc-pipeline-connector-doris`配置，Stream Load写入，`sink.label-prefix`幂等保证，Duplicate/Unique/Aggregate模型选择 |
| 第26章 | Exactly-Once与幂等写入 | Flink Checkpoint + Two-Phase Commit（2PC）机制，Kafka事务写入，Doris Label去重，端到端Exactly-Once验证 |
| 第27章 | 性能调优：反压诊断与资源配置 | 反压定位三板斧（Web UI / Metrics / Thread Dump），并行度公式推导，内存配置（TaskManager Heap / Network Buffer），`DebeziumChangeFetcher`调优 |
| 第28章 | 可观测性体系：Prometheus + Grafana | `flink-metrics-prometheus`集成，自定义Metric Reporter，Grafana大盘搭建（吞吐量/延迟/反压/Checkpoint/积压） |
| 第29章 | 生产级部署：Kubernetes与YARN | `K8SApplicationDeploymentExecutor`源码导读，`flink-cdc.sh --target kubernetes-application`，YARN Application模式，资源队列与优先级 |
| 第30章 | **综合实战：多源异构数据集成平台** | 完整案例：MySQL订单 + PostgreSQL用户 + MongoDB日志 → 同时写入Kafka + Doris + Iceberg，含路由/转换/Schema Evolution全流程 |

---

## 高级篇（第31–40章）—— 源码级掌控，具备"自研扩展 + SRE落地"能力

| 章节 | 主题 | 核心实战内容 |
|------|------|-------------|
| 第31章 | Flink CDC源码导读 | 10大模块全景图（common/runtime/composer/cli/connect/pipeline-model等），模块依赖关系、编译构建、调试环境搭建 |
| 第32章 | IncrementalSource源码剖析 | `IncrementalSource`/`Reader`/`SplitEnumerator`三件套，`MySqlHybridSplitAssigner`分片分配算法，`BinlogSplitReader`增量读取实现 |
| 第33章 | Debezium引擎集成源码分析 | `DebeziumSourceFunction`的`CheckpointedFunction`实现，`Handover`线程安全交付机制，`FlinkOffsetBackingStore`/`FlinkDatabaseSchemaHistory`状态持久化 |
| 第34章 | SchemaOperator与分布式协调 | `SchemaOperator`（regular模式）Coordinator设计，`SchemaRegistry`/`SchemaManager`/`SchemaDerivator`协作流程，分布式Schema Operator一致性协议 |
| 第35章 | Transform表达式编译与Janino | `TransformExpressionCompiler`编译链路，Calcite SQL解析→Janino生成Java字节码，`PreTransformOperator`/`PostTransformOperator`两阶段变换 |
| 第36章 | 自定义Connector开发（上）：DataSource | `DataSource`接口与`EventSourceProvider`实现，SPI注册（`META-INF/services`），自定义Source的`MetadataAccessor`（表结构获取） |
| 第37章 | 自定义Connector开发（下）：DataSink | `DataSink`接口与`EventSinkProvider`实现，`DataSinkWriterOperator`集成，`MetadataApplier`处理Schema变更，事务写入与幂等保证 |
| 第38章 | 极端场景优化：万级表与大事务 | 50000+张表场景优化（表过滤策略、内存控制），大事务（1亿+行）拆分，GTID断点续传，`capture-new-tables`动态发现 |
| 第39章 | SRE落地实践 | 故障案例库（Binlog被清理/OOM Kill/反压雪崩/Schema不兼容），灰度发布策略（金丝雀→全量），灾备与多活方案，SOP文档模板 |
| 第40章 | **综合实战：从零构建商业化CDC平台** | 完整闭环：需求分析→技术选型→架构设计→编码实现→性能压测→上线运维，含API管控、多租户隔离、计费计量设计 |
|

---

## 附录建议

| 附录 | 内容 |
|------|------|
| 附录A | 各版本兼容性矩阵（Flink 1.12~1.20 / Flink CDC 2.x~3.x） |
| 附录B | 常见错误码速查表（含根因 + 解决方案） |
| 附录C | 完整Docker Compose编排文件汇总 |
| 附录D | 思考题参考答案（每章2道进阶题） |
| 附录E | 各章节推荐阅读路径（开发/运维/测试不同角色） |
