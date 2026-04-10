# HBase 多部门培训讲义（36 章主线 + 4 章选修）

本目录面向**开发、运维、测试**，由浅入深；开发路线含架构、原理与**本仓库源码导读入口**。

## 分章文件（推荐按章阅读）

每章独立文件位于 [chapters/](chapters/)，命名规则：**序号 + 章名主题**（如 `01-第01章-HBase定位-与RDBMS-ES-Kafka-HDFS的边界.md`）。章内包含上一章/下一章导航、模板链接。

- **机器可读一览**（与脚本 `CHAPTER_SLUGS` 对齐）：[章节文件列表.md](章节文件列表.md)
- **40 章正文合一**（便于全文检索/导出）：[章节全文合集.md](章节全文合集.md)（由 `python generate_chapter_files.py` 生成）

| 章 | 文件（`chapters/` 目录下） |
|----|---------------------------|
| 第 1 章 | `01-第01章-HBase定位-与RDBMS-ES-Kafka-HDFS的边界.md` |
| 第 2 章 | `02-第02章-架构鸟瞰-Master-RegionServer-Meta-协调服务.md` |
| 第 3 章 | `03-第03章-环境搭建-单机伪分布式或Docker.md` |
| 第 4 章 | `04-第04章-hbase-shell-namespace与DDL.md` |
| 第 5 章 | `05-第05章-数据模型-RowKey列族Qualifier-Cell时间戳.md` |
| 第 6 章 | `06-第06章-Java入门-Configuration-Connection-Table.md` |
| 第 7 章 | `07-第07章-Put-Get-Delete与行大小意识.md` |
| 第 8 章 | `08-第08章-Scan基础-边界与caching-Scan非快照.md` |
| 第 9 章 | `09-第09章-Admin建表-描述符列族预分区.md` |
| 第 10 章 | `10-第10章-一致性与超时-行级原子与batch.md` |
| 第 11 章 | `11-第11章-监控入门-指标日志延迟.md` |
| 第 12 章 | `12-第12章-测试基础-等价类与RowKey边界.md` |
| 第 13 章 | `13-第13章-RowKey设计-散列反转时间盐与热点.md` |
| 第 14 章 | `14-第14章-Filter-FilterList代价与误用.md` |
| 第 15 章 | `15-第15章-批量-BufferedMutator与batch背压.md` |
| 第 16 章 | `16-第16章-异步客户端-AsyncConnection-AsyncTable.md` |
| 第 17 章 | `17-第17章-checkAndPut-Increment与并发测试.md` |
| 第 18 章 | `18-第18章-列族进阶-压缩缓存Bloom-TTL-MOB概念.md` |
| 第 19 章 | `19-第19章-Snapshot-Export与恢复演练.md` |
| 第 20 章 | `20-第20章-容量规划-Region堆磁盘与网络.md` |
| 第 21 章 | `21-第21章-安全-Kerberos-ACL与网络.md` |
| 第 22 章 | `22-第22章-客户端调优-超时重试-caching线程.md` |
| 第 23 章 | `23-第23章-集成测试-MiniCluster与CI隔离.md` |
| 第 24 章 | `24-第24章-性能测试-模型基线报告与SLA.md` |
| 第 25 章 | `25-第25章-读路径-客户端到RegionServer.md` |
| 第 26 章 | `26-第26章-写路径-MemStore与Flush.md` |
| 第 27 章 | `27-第27章-WAL-滚动回放与可靠性.md` |
| 第 28 章 | `28-第28章-HFile-StoreFile-块索引Bloom.md` |
| 第 29 章 | `29-第29章-Compaction-Minor-Major与IO放大.md` |
| 第 30 章 | `30-第30章-Region-Split-Merge-策略与P99.md` |
| 第 31 章 | `31-第31章-Assignment与Balance-迁移与RIT.md` |
| 第 32 章 | `32-第32章-MVCC与读点-行内可见性与Scan语义.md` |
| 第 33 章 | `33-第33章-RPC与Protobuf-服务边界.md` |
| 第 34 章 | `34-第34章-Coprocessor-Observer-Endpoint与发布风险.md` |
| 第 35 章 | `35-第35章-Replication-DR拓扑与切换演练.md` |
| 第 36 章 | `36-第36章-综合实战-亿级订单HBase与Elasticsearch.md` |

### 选修（第 37～40 章，与主线并行）

| 章 | 文件 |
|----|------|
| 第 37 章 · Phoenix / SQL 层 | `37-第37章-选修-Phoenix-SQL层与HBase查询.md` |
| 第 38 章 · Spark / Flink 集成 | `38-第38章-选修-Spark与Flink集成.md` |
| 第 39 章 · MOB 大对象 | `39-第39章-选修-MOB-大对象列存储.md` |
| 第 40 章 · Quota / 限流 | `40-第40章-选修-Quota-命名空间与表级限流配额.md` |

**重新生成分章文件**：在 `docs/hbase-training` 下执行 `python generate_chapter_files.py`（依赖现有 `part1-basic.md`～`part4-capstone.md`）。

---

## 合订本（便于检索与 diff）

| 文件 | 内容 |
|------|------|
| [00-template-pack.md](00-template-pack.md) | 统一四章模板、受众标签、作业与检查表示例 |
| [part1-basic.md](part1-basic.md) | 第 1～12 章 · 基础篇 |
| [part2-intermediate.md](part2-intermediate.md) | 第 13～24 章 · 中级篇 |
| [part3-advanced.md](part3-advanced.md) | 第 25～35 章 · 高级篇（含源码路径） |
| [part4-capstone.md](part4-capstone.md) | 第 36 章 · 综合实战 |

---

## 建议节奏

- 每周 2～3 章，约 3～4 个月完成主线；第 36 章为跨部门答辩与演练。
- 第 37～40 章按岗位选修（Phoenix、数据平台集成、大对象、多租户配额）。
- 讲义中 HBase 配置与 API 请以**公司现网大版本**为准，必要时增加「版本差异附录」。

## 仓库内参考

- 客户端：`hbase-client/src/main/java/org/apache/hadoop/hbase/client/`
- 服务端：`hbase-server/src/main/java/org/apache/hadoop/hbase/regionserver/`、`.../master/`
- ACID 说明：`hbase-website/app/pages/_landing/acid-semantics/content.md`
