# 《Flink实战派：从零到架构师》专栏大纲

> 专栏总章数：**38章** | 字数范围：每章 3000~5000 字 | 风格：实战为主，理论为辅

---

## 基础篇（第1-15章）—— 新人开发/测试入门

| 章节 | 主题 | 核心实战项目 |
|------|------|-------------|
| 第1章 | Flink是什么——从批到流的演进之路 | 术语体系、JobManager/TaskManager架构图、数据流图 |
| 第2章 | 环境搭建三分钟——Docker部署Flink开发环境 | Docker Compose一键启动Flink+Kafka+MySQL |
| 第3章 | Hello World——用WordCount理解数据流 | DataStream API WordCount + IDE断点调试 |
| 第4章 | 数据源与数据汇——从Kafka来，到MySQL去 | Kafka Source → Flink → MySQL/JDBC Sink |
| 第5章 | 变身术——Map/FlatMap/Filter数据变换 | 日志ETL清洗：JSON解析、字段过滤、格式转换 |
| 第6章 | 分而治之——KeyBy分组与聚合 | 实时PV/UV统计，分区策略选择 |
| 第7章 | 时间旅行者——EventTime与Watermark入门 | 事件时间vs处理时间，延迟容忍度配置 |
| 第8章 | 流动的窗口——滚动/滑动/会话窗口实战 | 每分钟交易额统计（滚动）、5分钟滑动PV（滑动）、用户行为会话切分 |
| 第9章 | 状态保鲜——ValueState与ListState初探 | 订单状态机、用户浏览路径记录 |
| 第10章 | 断点续传——Checkpoint让作业重启不丢数据 | 配置Checkpoint、故障模拟与自动恢复 |
| 第11章 | Rich Function——算子生命周期解密 | open/close钩子的连接池管理实战 |
| 第12章 | 侧输出流——脏数据分流与异常兜底 | 正常/脏数据分流写入不同Sink |
| 第13章 | Table API与SQL入门——写SQL也能做流计算 | 用Flink SQL完成PV/UV/成交额统计 |
| 第14章 | UDF自定义函数——当内置函数不够用 | 标量函数、表函数、聚合函数三件套 |
| 第15章 | **基础篇大综合——实时订单大屏监控系统** | Kafka→Flink→Redis→Grafana 完整链路 |

---

## 中级篇（第16-28章）—— 核心开发/运维进阶

| 章节 | 主题 | 核心实战项目 |
|------|------|-------------|
| 第16章 | Flink on YARN——从笔记本到生产集群 | 提交模式（Session/Per-Job/Application）、资源隔离 |
| 第17章 | Flink on Kubernetes——云原生部署最佳实践 | Operator模式 + Native K8S Session部署 |
| 第18章 | State Backend对决——RocksDB vs Heap选型 | 百万key级别下RocksDB增量Checkpoint压测对比 |
| 第19章 | 端到端精确一次——Exactly-Once从原理到落地 | Kafka→Flink→Kafka两阶段提交、事务超时调优 |
| 第20章 | 水位线深度解析——乱序处理与延迟容错 | SideOutput+Lateness多级延迟处理策略 |
| 第21章 | 双流Join全集——Regular/Interval/Window Join | 订单流+支付流对账、广告曝光+点击归因 |
| 第22章 | 异步IO——告别同步阻塞的性能瓶颈 | Redis异步查询，单算子QPS从300→30000 |
| 第23章 | CEP复杂事件处理——金融风控场景 | 连续3次登录失败告警、异常交易模式识别 |
| 第24章 | 反压诊断与调优——从背压到高吞吐 | Task Metrics → Flame Graph定位瓶颈、Credit-Based流控源码剖析 |
| 第25章 | 可观测性三件套——Metrics+Logging+Tracing | Prometheus联邦 + Grafana面板 + 自定义Metrics Reporter |
| 第26章 | Flink SQL进阶——窗口TopN/去重/多流Join | 直播间实时TopN商品、UV去重四种方案对比 |
| 第27章 | Savepoint——作业不停机升级与状态迁移 | 兼容/UPSERT状态迁移3步曲、Schema Evolution |
| 第28章 | **中级篇大综合——实时数仓CDC入湖全链路** | Flink CDC → Kafka → Hudi/Iceberg → Hive/Spark查询 |

---

## 高级篇（第29-38章）—— 架构师/资深开发

| 章节 | 主题 | 核心实战项目 |
|------|------|-------------|
| 第29章 | JobManager源码剖析——调度与容错核心 | SchedulerNG、ExecutionGraph生命周期、Failover策略 |
| 第30章 | TaskManager源码剖析——Task线程模型 | Task Slot分配、Mailbox模型、内存Segment管理 |
| 第31章 | 大状态优化——增量/Rocks/Spill三件套 | RocksDB Compaction调优、Local Recovery、状态剪枝 |
| 第32章 | 自定义Connector——打造企业级Source/Sink | 实现一个能并行、支持Checkpoint的分片Source |
| 第33章 | 窗口源码剖析与自定义Trigger | WindowOperator源码解读、自定义早激发/迟到处理Trigger |
| 第34章 | Flink SQL自定义算子——Table/SQL扩展 | 自定义ScalarFunction + TableFunction + AggregateFunction源码级注册 |
| 第35章 | 极端场景优化——数据倾斜/WAL瓶颈/Hot Key | 两阶段聚合、Salt分桶、Key-Group Rebalance |
| 第36章 | SRE落地——SLA保障与多活容灾 | 作业分级告警(AVP)、跨集群双活、自动弹性伸缩 |
| 第37章 | ML/Flink——实时特征工程与在线推理 | Flink + Flink ML Pipeline实时特征计算 |
| 第38章 | **高级篇大综合——企业级实时特征平台** | Flink+ClickHouse+Redis构建毫秒级实时特征服务 |

---

## 统计总览

| 级别 | 章节范围 | 数量 | 核心产出 |
|------|----------|------|----------|
| 基础篇 | 第1-15章 | 15 | 独立完成实时大屏系统 |
| 中级篇 | 第16-28章 | 13 | 独立交付CDC实时数仓 |
| 高级篇 | 第29-38章 | 10 | 设计企业级实时特征平台 |
| **总计** | | **38章** | 三次综合实战，层层递进 |

---

## 推广计划（按部门推荐阅读顺序）

### 测试部门
- **优先级阅读**：基础篇第1-6章 → 第8-10章 → 第12章
- **协作事项**：提供集成测试所需的数据模拟脚本与Checkpoint恢复场景

### 开发部门
- **优先级阅读**：基础篇全文 → 中级篇全文 → 高级篇第29-30章、第32-34章
- **协作事项**：统一Connector开发规范、Code Review检查项对照表

### 运维部门
- **优先级阅读**：基础篇第2章、第10章 → 中级篇第16-17章、第24-25章、第27章 → 高级篇第36章
- **协作事项**：建立Flink作业健康度SOP、SLA分级告警模板
