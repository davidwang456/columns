# 中级篇：第 13～24 章

前置：[part1-basic.md](part1-basic.md)。模板见 [00-template-pack.md](00-template-pack.md)。

---

## 第 13 章：RowKey 设计——散列、反转时间、盐与热点

**受众：主【Dev】 辅【Ops】 难度：中级**

### 1）项目背景

- **开发**：RowKey 决定延迟与均衡；顺序键易热点。
- **运维**：热点直接表现为单 RS CPU/请求堆积。
- **测试**：压测需覆盖「打散后」与「恶意顺序」对比。

### 2）项目设计（大师 × 小白）

- **小白**：「用自增 long 当 RowKey 最省事。」
- **大师**：「**字典序连续**会把写入打到少数 Region；用 **hash 前缀、盐、反转时间戳** 等打散。」
- **小白**：「打散后怎么按用户查？」
- **大师**：「**查询模式要写在设计里**：若主要按 userId 查，把 **userId（定长）** 放进 RowKey，前面再加短散列。」

### 3）项目实战

- 设计两种 RowKey：`seq` vs `hash4(userId)+userId+reverse(ts)+orderId`。
- 使用 `RegionLocator.getRegionLocation`（Java）或 UI 观察 Region 请求是否倾斜。
- 推演：10 倍写入下哪种会先触顶单 RS？

```java
try (Connection conn = ConnectionFactory.createConnection(conf);
     RegionLocator locator = conn.getRegionLocator(TableName.valueOf("training", "orders"))) {
  HRegionLocation loc = locator.getRegionLocation(Bytes.toBytes("your_rowkey"));
  // 记录 region 名与 host
}
```

### 4）项目总结

- **优点**：合理设计可同时服务点查与范围扫。
- **缺点**：难以事后「改 RowKey」；需迁移或双写。
- **适用**：高写入、多租户、时序。
- **注意**：定长字段避免 Scan 边界错误。
- **踩坑**：只 hash 不保留业务前缀导致无法用户维 Scan。
- **运维检查项**：是否有热点 Region 告警。
- **测试检查项**：skew 压测场景是否入回归。

---

## 第 14 章：Filter——FilterList、代价与误用

**受众：主【Dev】 难度：中级**

### 1）项目背景

- **开发**：在服务端过滤减少网络传输；理解 CPU 代价。
- **测试**：验证 Filter 与列缺失组合。

### 2）项目设计（大师 × 小白）

- **小白**：「Filter 能代替二级索引吗？」
- **大师**：「**不能通用代替**；大范围 Scan + Filter 仍可能很重。」
- **小白**：「多个条件呢？」
- **大师**：「`FilterList` 组合 **MUST_PASS_ALL / MUST_PASS_ONE**。」

### 3）项目实战

```java
Scan scan = new Scan().addFamily(Bytes.toBytes("d"));
Filter f1 = new SingleColumnValueFilter(
    Bytes.toBytes("d"), Bytes.toBytes("status"),
    CompareOperator.EQUAL, Bytes.toBytes("PAID"));
scan.setFilter(f1);
```

对比实验：同样条件，**窄 RowKey 范围 Scan + Filter** vs **宽范围 Scan + Filter** 的耗时与 RPC。

### 4）项目总结

- **优点**：灵活、减少客户端处理。
- **缺点**：滥用导致 RS CPU 高。
- **适用**：已知范围的精细化筛选。
- **注意**：`Filter` 与 `batch/caching` 交互阅官方文档。
- **踩坑**：以为 Filter 会魔法式加速全表扫。
- **测试检查项**：无匹配行、部分列缺失。

---

## 第 15 章：批量——BufferedMutator、batch、背压

**受众：主【Dev】 难度：中级**

### 1）项目背景

- **开发**：高吞吐写入路径。
- **运维**：关注 flush 与 memstore 压力。
- **测试**：部分失败、客户端 OOM。

### 2）项目设计（大师 × 小白）

- **小白**：「for 循环 put 一万次？」
- **大师**：「RPC 爆炸；用 **`BufferedMutator`** 或合理 **batch**。」
- **小白**：「出错了呢？」
- **大师**：「看异常与 **`RetriedMutationsException`** 等；要有 **重试与死信** 策略。」

### 3）项目实战

```java
try (Connection conn = ConnectionFactory.createConnection(conf);
     BufferedMutator mutator = conn.getBufferedMutator(TableName.valueOf("training", "orders"))) {
  for (int i = 0; i < 1000; i++) {
    Put p = new Put(Bytes.toBytes("rk" + i));
    p.addColumn(Bytes.toBytes("d"), Bytes.toBytes("v"), Bytes.toBytes("x"));
    mutator.mutate(p);
  }
}
```

对比：同步逐条 `table.put` 与 `BufferedMutator` 的吞吐与平均延迟。

### 4）项目总结

- **优点**：显著提升吞吐。
- **缺点**：错误处理复杂；内存与 buffer 需调参。
- **适用**：批量导入、异步管道落库。
- **注意**：`close` 前隐式 flush。
- **踩坑**：buffer 过大 OOM。
- **测试检查项**：部分失败、进程 kill 中途。

---

## 第 16 章：异步客户端——AsyncConnection、AsyncTable

**受众：主【Dev】 难度：中级**

### 1）项目背景

- **开发**：高并发扇出读、与异步框架集成。
- **测试**： CompletableFuture 组合与超时。

### 2）项目设计（大师 × 小白）

- **小白**：「和同步比啥好处？」
- **大师**：「**线程阻塞少**；适合 I/O 密集、多行并行 Get。」
- **小白**：「更难调试？」
- **大师**：「是，需要规范 **超时、异常链、背压**。」

### 3）项目实战

使用 `AsyncConnection` 获取 `AsyncTable`，对多个 RowKey 发起 `get`，`CompletableFuture.allOf` 等待；设置统一超时。具体 API 以当前 HBase 版本为准，参阅 [Client API](https://hbase.apache.org/book.html#client) 与源码 `AsyncConnection`。

### 4）项目总结

- **优点**：高并发友好。
- **缺点**：编程与排查复杂度高。
- **适用**：网关聚合、并行补全。
- **注意**：线程池与队列长度。
- **踩坑**：无限制 `allOf` 千万 Future。
- **测试检查项**：超时风暴、线程池耗尽。

---

## 第 17 章：checkAndPut、Increment 与并发测试

**受众：主【Dev、QA】 难度：中级**

### 1）项目背景

- **开发**：乐观锁、状态机迁移。
- **测试**：并发冲突与重试路径。

### 2）项目设计（大师 × 小白）

- **小白**：「先 get 再 put 安全吗？」
- **大师**：「**不安全**；中间可能被改。用 **`checkAndPut`** 做 CAS。」

### 3）项目实战

```java
Put put = new Put(row);
put.addColumn(fam, qual, Bytes.toBytes("PAID"));
boolean ok = table.checkAndPut(row, fam, qual, Bytes.toBytes("NEW"), put);
```

**测试**：多线程同时 `checkAndPut`，统计成功次数与最终状态唯一性。

### 4）项目总结

- **优点**：单行原子 CAS。
- **缺点**：冲突时需业务重试。
- **适用**：库存、状态机（谨慎跨系统）。
- **注意**：比较的是**单元格值**，不是整行。
- **踩坑**：误用存在性检查当分布式锁。
- **测试检查项**：ABA 是否业务可接受。

---

## 第 18 章：列族进阶——压缩、缓存、Bloom、TTL、MOB 概念

**受众：主【Dev、Ops】 难度：中级**

### 1）项目背景

- **开发**：读写放大与空间权衡。
- **运维**：变更压缩需观察 compaction。

### 2）项目设计（大师 × 小白）

- **小白**：「压缩越狠越好？」
- **大师**：「**CPU 换空间**；还要考虑读路径解压成本。」
- **小白**：「MOB 是啥？」
- **大师**：「**中等对象**列的存储优化，适合略大的二进制/文本，需单独评估版本与运维。」

### 3）项目实战

对测试表 `alter` 列族设置 `COMPRESSION => 'SNAPPY'`（或集群支持算法），`describe` 对比；查阅官方 [Compression](https://hbase.apache.org/book.html#compression)。

### 4）项目总结

- **优点**：省空间、可控读放大。
- **缺点**：参数错配拖累 P99。
- **适用**：冷数据高压缩、热数据低延迟调 cache。
- **注意**：Major compaction 后压缩才完全生效等细节阅文档。
- **踩坑**：BLOCKCACHE 过小导致热读抖动。
- **运维检查项**：变更后 24～72h 观察 IO 与延迟。

---

## 第 19 章：Snapshot、Export 与恢复演练

**受众：主【Ops】 辅【Dev】 难度：中级**

### 1）项目背景

- **运维**：备份与误删恢复。
- **开发**：知道恢复 RTO/RPO 边界。

### 2）项目设计（大师 × 小白）

- **小白**：「snapshot 会锁表吗？」
- **大师**：「**不等价于停写**；但与 **compaction、文件引用** 有关，需读官方备份章节。」
- **小白**：「能跨集群吗？」
- **大师**：「常配合 **ExportSnapshot、DistCp** 等流程。」

### 3）项目实战

在测试环境：创建 snapshot → clone 到新表或恢复流程（按公司 Runbook）；参考官方 [Backup and Snapshots](https://hbase.apache.org/book.html#backup_restore)。

### 4）项目总结

- **优点**：逻辑备份、克隆方便。
- **缺点**：流程复杂；与 HDFS 空间强相关。
- **适用**：发布前、重大 DDL 前。
- **注意**：命名规范与保留周期。
- **踩坑**：以为 snapshot 替代异地灾备。
- **测试检查项**：恢复后抽样行对比脚本。

---

## 第 20 章：容量规划——Region 数、堆内存、磁盘与网络

**受众：主【Ops、Dev】 难度：中级**

### 1）项目背景

- **运维**：扩容与机型选型。
- **开发**：表预分区与写入速率预估。

### 2）项目设计（大师 × 小白）

- **小白**：「Region 越多越好？」
- **大师**：「**过多**增加 Master 负担与元数据开销；**过少**易热点。要平衡。」

### 3）项目实战

填表：RS 台数、每 RS Region 数、堆、磁盘类型、网卡、副本数；对照官方 [Region and Capacity](https://hbase.apache.org/book.html#region-capacity-planning) 做一页纸结论。

### 4）项目总结

- **优点**：提前避免瓶颈。
- **缺点**：业务增长难精确预测。
- **适用**：上生产前、大促前。
- **注意**：与 HDFS、ZK 一并规划。
- **踩坑**：只按磁盘不算 IOPS。
- **测试检查项**：压测报告附环境规格。

---

## 第 21 章：安全——Kerberos、ACL、网络

**受众：主【Ops、Dev】 难度：中级**

### 1）项目背景

- **运维**：集群合规与多租户。
- **开发**：客户端 principal、keytab 轮换。

### 2）项目设计（大师 × 小白）

- **小白**：「内网就不加密了吧？」
- **大师**：「**纵深防御**：TLS、ACL、认证视公司策略。」

### 3）项目实战

阅读官方 [Security](https://hbase.apache.org/book.html#security) 与 [ACL matrix](https://hbase.apache.org/book.html#acl.matrix)；列出本环境启用的机制与运维联系人。

### 4）项目总结

- **优点**：降低越权与数据泄露风险。
- **缺点**：配置与排障成本高。
- **适用**：生产、多团队共用集群。
- **注意**：证书过期、keytab 同步。
- **踩坑**：测试直连生产元数据。
- **测试检查项**：无权限账户的否定用例。

---

## 第 22 章：客户端调优——超时、重试、caching、线程

**受众：主【Dev、Ops】 难度：中级**

### 1）项目背景

- **开发**：减少毛刺与线程饥饿。
- **运维**：与 RS 超时配置协同。

### 2）项目设计（大师 × 小白）

- **小白**：「超时设越大越稳？」
- **大师**：「过大**拖死线程**；过小**误杀**正常请求。要压测校准。」

### 3）项目实战

对同一 Scan 调整 `setCaching`（如 50 vs 500），记录 RPC 次数、P99、客户端堆；记录推荐值与理由。

### 4）项目总结

- **优点**：成本低的性能手段。
- **缺点**：参数与环境强相关。
- **适用**：读多写多业务。
- **注意**：与 `setBatch` 组合行为。
- **踩坑**：多线程共用一个非线程安全 Table。
- **测试检查项**：弱网下重试次数与风暴。

---

## 第 23 章：集成测试——MiniCluster、HBaseTestingUtility、CI 隔离

**受众：主【QA、Dev】 难度：中级**

### 1）项目背景

- **测试**：可重复自动化。
- **开发**：模块级回归不依赖共享环境。

### 2）项目设计（大师 × 小白）

- **小白**：「连开发共用集群跑 CI？」
- **大师**：「**不稳定**；用 **MiniCluster** 或容器化单测，表名带 **build id**。」

### 3）项目实战

阅读本仓库相关文档：[Unit Testing](https://hbase.apache.org/book.html#unit.tests)（与 `hbase-website` 中 `unit-testing.mdx` 对应）；写一个最小测试：启动 utility → 建表 → Put/Get → 关闭。

### 4）项目总结

- **优点**：快速反馈。
- **缺点**：与生产行为仍有差异。
- **适用**：PR 门禁、核心库。
- **注意**：资源清理与超时。
- **踩坑**：并行 job 表名冲突。
- **运维检查项**：CI 资源配额。

---

## 第 24 章：性能测试——模型、基线、报告与 SLA

**受众：主【QA】 辅【全员】 难度：中级**

### 1）项目背景

- **测试**：可对比的压测报告。
- **运维**：与 SLA、容量挂钩。
- **开发**：优化前后有据。

### 2）项目设计（大师 × 小白）

- **小白**：「压到报错为止？」
- **大师**：「先定 **SLO**：例如 P99 写入 50ms、读 20ms，再找到 **饱和点**。」

### 3）项目实战

输出标准报告一节：**环境**、**数据量**、**并发**、**混合比例**、**结果表格**、**监控截图**、**结论与风险**。与运维对齐是否触发告警。

### 4）项目总结

- **优点**：上线决策有依据。
- **缺点**：准备成本高。
- **适用**：大版本升级、大促、架构改造。
- **注意**：预热、GC 日志、是否冷缓存。
- **踩坑**：单机压测结果外推生产。
- **开发检查项**：是否提供可复现的 data load 脚本。

---

**中级篇完。下一部分：** [part3-advanced.md](part3-advanced.md)
