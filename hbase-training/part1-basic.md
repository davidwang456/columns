# 基础篇：第 1～12 章

统一结构见 [00-template-pack.md](00-template-pack.md)。官方文档：[HBase Reference Guide](https://hbase.apache.org/docs)；一致性：[ACID semantics](https://hbase.apache.org/acid-semantics)。

---

## 第 1 章：HBase 定位——与 RDBMS、ES、Kafka、HDFS 的边界

**受众：主【全员】 难度：基础**

### 1）项目背景

- **业务/开发**：避免把 HBase 当「分布式 MySQL」设计复杂 SQL 式查询，导致上线后延迟与成本失控。
- **运维/SRE**：明确 HBase 依赖 HDFS（或兼容文件系统）与协调组件，容量与故障域与对象存储、消息队列不同。
- **测试/质量**：选型错误会导致用例模型整个推翻，需在需求阶段对齐「无跨行事务、无丰富 SQL」等边界。

### 2）项目设计（大师 × 小白）

- **小白**：「我们有个订单表，能不能上 HBase 替代 MySQL？」
- **大师**：「先问**访问模式**：是不是主键点查 + 按 RowKey 范围扫？如果要复杂 JOIN、强 ACID 跨表，HBase 不是第一选择。」
- **小白**：「那和 ES 呢？」
- **大师**：「ES 偏**检索与聚合**；HBase 偏**海量行存与低延迟点查/顺序扫**。常组合：ES 查 ID，HBase 取明细。」
- **小白**：「Kafka 呢？」
- **大师**：「Kafka 是**日志流**；HBase 是**在线存储**。可以从 Kafka 消费后写入 HBase，职责不同。」
- **小白**：「底层不是 HDFS 吗？」
- **大师**：「数据文件在 HDFS 上，但**读写路径**由 RegionServer 服务，不是直接当文件系统用。」

### 3）项目实战

**讨论题**：选三个真实业务场景，填表：主访问路径、是否需要事务、QPS/数据量级、是否适合 HBase（是/否/需改造）。

**选型表（示例）**

| 场景 | 适合 HBase？ | 备注 |
|------|--------------|------|
| 用户画像宽表，按 userId 读写 | 通常适合 | RowKey=userId |
| 报表多表 JOIN | 不适合为主存 | 用数仓或联邦查询 |
| 日志检索+全文 | 需配合 ES/Solr | HBase 存原文或摘要 |

### 4）项目总结

- **优点**：水平扩展、稀疏宽表、与 Hadoop 生态一体。
- **缺点**：无通用 SQL、无跨行事务、严重依赖 RowKey 设计。
- **适用**：海量键值/宽表、时序与日志索引、特征与画像。
- **注意**：先定访问模式再定存储。
- **踩坑**：用业务方熟悉的「表」字误导为 RDBMS。
- **测试检查项**：需求评审中是否记录「不支持的能力」。
- **运维检查项**：是否与业务对齐 SLA 与依赖栈（HDFS、ZK 等）。

---

## 第 2 章：架构鸟瞰——Master、RegionServer、Meta、协调服务

**受众：主【Dev、Ops】 辅【QA】 难度：基础**

### 1）项目背景

- **开发**：理解一次 Get 如何路由到 RegionServer。
- **运维**：知道重启、扩容时各组件职责，避免误操作 Master。
- **测试**：构造故障用例时需对准 RS/Master 角色。

### 2）项目设计（大师 × 小白）

- **小白**：「HBase 就一个进程吧？」
- **大师**：「生产至少 **HMaster**（元数据与调度）、**RegionServer**（数据读写）、**HDFS**；还有 **hbase:meta** 记录 Region 位置。」
- **小白**：「客户端先找谁？」
- **大师**：「先找集群入口（如 ZK 或配置中的 Registry），再查 **meta**，最后连 **目标 RS**。」
- **小白**：「Master 挂了还能读写吗？」
- **大师**：「**已分配好的 Region** 通常仍可服务；但**建表、分裂、均衡**等会受影响，视版本与状态而定，不能当常态。」

### 3）项目实战

画一张图：**Client → 协调组件 → meta → RegionServer → HDFS**。对照 [Architecture 文档](https://hbase.apache.org/book.html#arch) 核对组件名称。

### 4）项目总结

- **优点**：职责清晰，便于分区扩展。
- **缺点**：组件多，运维与排查链路长。
- **适用**：所有后续章节的前置心智模型。
- **注意**：meta 表健康至关重要。
- **踩坑**：把单点 Master 当「数据节点」。
- **运维检查项**：meta 与 namenode 是否纳入专项监控。
- **测试检查项**：故障演练是否区分 Master 与 RS。

---

## 第 3 章：环境搭建——单机、伪分布式或 Docker

**受众：主【Ops】 辅【Dev、QA】 难度：基础**

### 1）项目背景

- **开发**：本地或可复现环境调试客户端。
- **运维**：统一公司标准镜像与参数，避免「我机器能跑」。
- **测试**：CI 与手工环境对齐，减少环境问题扯皮。

### 2）项目设计（大师 × 小白）

- **小白**：「我内存 8G，能跑全集群吗？」
- **大师**：「学习可以 **单机/伪分布式**；生产是多 RS。先保证 **JDK、HDFS、HBase 版本矩阵**与公司一致。」
- **小白**：「能直接用 Docker 吗？」
- **大师**：「可以，但要固定**镜像版本与端口**，文档写清 `hbase-site.xml` 挂载位置。」

### 3）项目实战

按公司规范完成：启动 HBase → 打开 Master UI → `hbase shell` 中执行 `status 'simple'`。记录：版本号、JDK、`hbase.rootdir`。

### 4）项目总结

- **优点**：标准化环境降低沟通成本。
- **缺点**：本地与生产差异仍存在（网络、数据量）。
- **适用**：培训、集成测试、POC。
- **注意**：不要将本地调优参数直接拷生产。
- **踩坑**：时间不同步导致奇怪故障。
- **运维检查项**：NTP、hosts、防火墙、目录权限。
- **测试检查项**：环境检查清单入用例库。

---

## 第 4 章：hbase shell——namespace、DDL、describe、count

**受众：主【全员】 难度：基础**

### 1）项目背景

- **开发/运维**：快速验证表结构与 Region。
- **测试**：准备数据与清理环境。

### 2）项目设计（大师 × 小白）

- **小白**：「shell 能写业务逻辑吗？」
- **大师**：「**运维与调试**为主；生产业务应用走 **Java/REST 等 API**。」
- **小白**：「namespace 干嘛用？」
- **大师**：「**多租户隔离**、权限与命名空间，类似逻辑上的 database。」

### 3）项目实战

```text
create_namespace 'training'
create 'training:orders', { NAME => 'd', VERSIONS => 1 }
describe 'training:orders'
put 'training:orders', 'rk001', 'd:status', 'NEW'
get 'training:orders', 'rk001'
scan 'training:orders', { LIMIT => 10 }
```

**预分区演示**（与第 9 章呼应）：

```text
create 'training:orders_splitted', 'd', SPLITS => ['10','20','30']
```

### 4）项目总结

- **优点**：上手快，适合课堂演示。
- **缺点**：大表 `count` 很慢；勿在生产大表随意 count。
- **适用**：培训、紧急排障。
- **注意**：命令大小写与引号。
- **踩坑**：未加 namespace 导致表名冲突。
- **测试检查项**：用例是否注明使用的 namespace。

---

## 第 5 章：数据模型——RowKey、列族、Qualifier、Cell、时间戳

**受众：主【全员】 难度：基础**

### 1）项目背景

- **开发**：列族在表级固定；Qualifier 可动态增加。
- **运维**：列族数量影响 flush/compaction 行为。
- **测试**：验证多版本、TTL 需理解 Cell 粒度。

### 2）项目设计（大师 × 小白）

- **小白**：「表要先建 100 个列吗？」
- **大师**：「**Qualifier 不必预建**；**列族**要规划，不宜过多。」
- **小白**：「同一格写两次呢？」
- **大师**：「每次是一个带 **timestamp** 的 Cell；可保留多 **VERSIONS** 或用最新覆盖策略。」

### 3）项目实战

```text
put 'training:orders', 'rk002', 'd:amt', '100'
put 'training:orders', 'rk002', 'd:amt', '200'   # 新版本
get 'training:orders', 'rk002', { COLUMN => 'd:amt', VERSIONS => 2 }
```

### 4）项目总结

- **优点**：稀疏、灵活。
- **缺点**：滥用列族与版本会放大 IO 与存储。
- **适用**：半结构化、快速演进属性。
- **注意**：列族名、Qualifier 不宜过长。
- **踩坑**：以为像 MySQL 改列名很轻量。
- **开发检查项**：VERSIONS、TTL 是否与业务一致。
- **运维检查项**：表描述是否审计过 CF 数量。

---

## 第 6 章：Java 入门——Configuration、Connection、Table

**受众：主【Dev】 辅【QA】 难度：基础**

### 1）项目背景

- **开发**：正确管理连接生命周期，避免连接泄漏。
- **测试**：自动化用例使用相同模式，便于稳定性测试。

### 2）项目设计（大师 × 小白）

- **小白**：「每次操作 new 一个 Connection？」
- **大师**：「**Connection 重、可复用**；`Table` 轻、用完关。推荐 **try-with-resources**。」
- **小白**：「线程安全吗？」
- **大师**：「**Table 非线程安全**；多线程要么每线程一个 Table，要么做好同步（见官方客户端文档）。」

### 3）项目实战

参考本仓库 `ConnectionFactory` 注释（`hbase-client/.../ConnectionFactory.java`）：

```java
Configuration conf = HBaseConfiguration.create();
try (Connection connection = ConnectionFactory.createConnection(conf);
     Table table = connection.getTable(TableName.valueOf("training", "orders"))) {
  // Put / Get / Scan
}
```

### 4）项目总结

- **优点**：API 清晰，与集群共享底层资源。
- **缺点**：错误使用会导致 FD、线程耗尽。
- **适用**：所有 Java 业务接入。
- **注意**：`close()` 与异常路径。
- **踩坑**：在循环里反复 `createConnection` 不关闭。
- **测试检查项**：长稳测试是否监控连接数。

---

## 第 7 章：Put、Get、Delete 与行大小意识

**受众：主【Dev】 难度：基础**

### 1）项目背景

- **开发**：单行原子写入；避免单行过大。
- **测试**：断言 Put 后 Get 的值与列存在性。

### 2）项目设计（大师 × 小白）

- **小白**：「一个 Put 里放 1MB 字符串行吗？」
- **大师**：「**技术上可能，工程上危险**：影响 flush、拆分、RPC。大对象考虑 MOB 或对象存储。」
- **小白**：「Delete 是删整行吗？」
- **大师**：「可指定列或时间范围；**墓碑**与 compaction 有关，需理解可见性。」

### 3）项目实战

```java
byte[] row = Bytes.toBytes("order#10001");
Put put = new Put(row);
put.addColumn(Bytes.toBytes("d"), Bytes.toBytes("status"), Bytes.toBytes("PAID"));
table.put(put);

Get get = new Get(row);
get.addColumn(Bytes.toBytes("d"), Bytes.toBytes("status"));
Result r = table.get(get);
byte[] v = r.getValue(Bytes.toBytes("d"), Bytes.toBytes("status"));
```

### 4）项目总结

- **优点**：行级操作简单直接。
- **缺点**：大行、大 Cell 拖累集群。
- **适用**：订单、状态、计数（也可用 Increment）。
- **注意**：字符编码统一用 `Bytes` 或约定 UTF-8。
- **踩坑**：未校验 `Result.isEmpty()`。
- **测试检查项**：空结果、多列、不存在列族的错误处理。

---

## 第 8 章：Scan 基础——边界、caching；Scan 非快照

**受众：主【Dev、QA】 难度：基础**

### 1）项目背景

- **开发**：列表、导出依赖 Scan；不得假设快照隔离。
- **测试**：并发写入 + Scan 的预期需写明（见 ACID 文档）。

### 2）项目设计（大师 × 小白）

- **小白**：「Scan 像数据库可重复读吗？」
- **大师**：「**不像**。官方明确：**Scan 不是表级一致快照**；多行可能来自不同时间点。」
- **小白**：「那分页呢？」
- **大师**：「用 **start/stopRow**、**PageFilter** 或 **last row 续扫**；配合 **setCaching** 控制 RPC 次数。」

### 3）项目实战

```java
Scan scan = new Scan()
    .withStartRow(Bytes.toBytes("order#10000"), true)
    .withStopRow(Bytes.toBytes("order#20000"), false)
    .addFamily(Bytes.toBytes("d"));
scan.setCaching(200);
try (ResultScanner rs = table.getScanner(scan)) {
  for (Result res : rs) { /* ... */ }
}
```

**小实验**：线程 A 持续 Put 新行，线程 B Scan 同前缀，观察是否「漏读或重复」——记录为**现象**而非 bug，除非违背产品语义。

### 4）项目总结

- **优点**：顺序读吞吐高。
- **缺点**：滥用全表扫；语义与业务误解。
- **适用**：RowKey 连续范围、前缀。
- **注意**：`setBatch` 与 `setCaching` 区别。
- **踩坑**：把 Scan 当 OLTP 多维检索。
- **测试检查项**：是否引用 [ACID semantics](https://hbase.apache.org/acid-semantics) 中 Scan 条目。

---

## 第 9 章：Admin 建表——描述符、列族、预分区

**受众：主【Dev、Ops】 难度：基础**

### 1）项目背景

- **开发**：代码化建表与 CI 环境。
- **运维**：与 shell 对照，统一 split 策略。

### 2）项目设计（大师 × 小白）

- **小白**：「为啥要 splitKeys？」
- **大师**：「避免**单 Region 扛全部初始写入**，形成热点。」

### 3）项目实战

```java
try (Connection conn = ConnectionFactory.createConnection(conf);
     Admin admin = conn.getAdmin()) {
  TableName tn = TableName.valueOf("training", "orders_java");
  TableDescriptorBuilder tdb = TableDescriptorBuilder.newBuilder(tn);
  ColumnFamilyDescriptor cf = ColumnFamilyDescriptorBuilder
      .newBuilder(Bytes.toBytes("d")).setMaxVersions(1).build();
  tdb.setColumnFamily(cf);
  byte[][] splits = new byte[][] {
    Bytes.toBytes("m"), Bytes.toBytes("t")
  };
  if (!admin.tableExists(tn)) {
    admin.createTable(tdb.build(), splits);
  }
}
```

### 4）项目总结

- **优点**：可重复、可版本管理。
- **缺点**：误删表代价高。
- **适用**：自动化部署、多环境。
- **注意**：`disable` 期间不可用。
- **踩坑**：生产无预分区 + 顺序 RowKey。
- **运维检查项**：DDL 评审与变更窗口。

---

## 第 10 章：一致性与超时——行级原子、batch、不确定完成

**受众：主【Dev、QA】 辅【Ops】 难度：基础**

### 1）项目背景

- **开发**：正确处理超时与重试。
- **测试**：用例覆盖「可能成功可能失败」的断言方式。
- **运维**：协助识别网络与 RS 超时日志。

### 2）项目设计（大师 × 小白）

- **小白**：「batch 里 100 个 Put 全成功才算成功？」
- **大师**：「**多行不保证原子**；返回里逐行成功/失败/超时。」
- **小白**：「超时了我就再发一次？」
- **大师**：「**超时结果未知**；要 **幂等** 或 **读后决策**。」

### 3）项目实战

列出团队规范：**幂等键**、**最大重试**、**退避**、**对账任务**。对照 [ACID semantics](https://hbase.apache.org/acid-semantics) 中 Atomicity、timeout 条目逐条打勾。

### 4）项目总结

- **优点**：单行语义清晰。
- **缺点**：应用层承担跨行与超时语义。
- **适用**：订单状态、计数、CAS。
- **注意**：日志打 request id。
- **踩坑**：超时当失败导致双写。
- **测试检查项**：模拟 RS 慢响应、客户端短超时。

---

## 第 11 章：监控入门——指标、日志、延迟

**受众：主【Ops】 辅【Dev】 难度：基础**

### 1）项目背景

- **运维**：建立最小可观测性。
- **开发**：理解客户端重试在指标上的反映。

### 2）项目设计（大师 × 小白）

- **小白**：「看 CPU 够吗？」
- **大师**：「还要看 **队列、flush、compaction、block cache 命中率、RPC 延迟**。」

### 3）项目实战

从 Master / RS UI 或 JMX 记录至少 5 个指标名称与含义；对照官方 [Metrics](https://hbase.apache.org/book.html#metrics) 章节扩展。

### 4）项目总结

- **优点**：提前发现热点与慢节点。
- **缺点**：指标过多需分层告警。
- **适用**：生产必备。
- **注意**：告警阈值结合业务低峰校准。
- **踩坑**：仅磁盘满才看集群。
- **开发检查项**：业务埋点与 trace id。
- **测试检查项**：压测是否导出监控截图。

---

## 第 12 章：测试基础——等价类、RowKey 边界、数据准备

**受众：主【QA】 辅【Dev】 难度：基础**

### 1）项目背景

- **测试**：系统化覆盖 HBase 特有行为。
- **开发**：交付可测的 RowKey 约定与错误码。

### 2）项目设计（大师 × 小白）

- **小白**：「和测 MySQL 一样吧？」
- **大师**：「要加 **空 RowKey、极大行、并发 batch、Scan 边界、表禁用** 等。」

### 3）项目实战

填写「评审表模板」：功能模块、等价类、边界值、负面用例、是否需要性能用例、清理步骤（truncate/disable）。

### 4）项目总结

- **优点**：减少上线后「没见过」类故障。
- **缺点**：用例维护成本。
- **适用**：所有 HBase 相关需求。
- **注意**：测试数据脱敏。
- **踩坑**：只在空表上测通过即结束。
- **运维检查项**：测试 namespace 与生产隔离。

---

**基础篇完。下一部分：** [part2-intermediate.md](part2-intermediate.md)
