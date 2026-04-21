# 第 22 章：Hive Streaming：近实时入仓链路

> **专栏分档**：中级篇  
> **总纲索引**：[hive-column-outline.md](../hive-column-outline.md)（第五章 · 第 22 章对照表）  
> **业务主线**：电商平台「用户行为 + 交易」离线数仓（曝光、点击、下单、退款、风控特征）。

## 本章大纲备忘（写作前对照总纲）

| 项 | 内容 |
|----|------|
| 一句话摘要 | Streaming API 最小示例与失败重试。 |
| 业务锚点 / 技术焦点 | 秒级日志入仓。 |
| 源码或文档锚点 | `source/hive/streaming`（如 `HiveStreamingConnection`）。 |

单章目标篇幅 **3000～5000 字**，四段结构对齐 [template.md](../template.md)。

---

## 1 项目背景（约 500 字）

埋点从 **小时级批量落地** 升级到 **秒级入仓**，Flume/Kafka consumer 需要向 Hive **追加写入 ORC 分桶**。团队评估 **Hive Streaming API**：按 **TransactionBatch** 提交、**ACK 与重试**、与 **Metastore 锁** 的交互。风险：实现复杂、与 **Flink/Hudi** 路线竞争。本章给 **最小 Java 伪代码链路 + 运维检查点**。

补充：评估 Streaming 时要同时算 **「端到端延迟」** 与 **「对 NN/Metastore 的写入 QPS」**。很多失败案例不是 API 不会用，而是 **把 Kafka 峰值流量原样写入 Hive 小文件**，NN 先报警，延迟再崩——此时应回到 **微批增大、合并、或换湖格式** 的产品决策。

---

## 2 项目设计（约 1200 字）


> **角色（对齐 [template.md](../template.md)）**：**小胖**（生活化比喻、抛问题）· **小白**（原理、边界、风险与备选）· **大师**（选型与「**技术映射**」承接）。  
> **对话结构**：小胖开球 1～2 轮 → 小白追问 2～3 轮 → 大师解答并引出下一子话题；全文循环 **2～3 次**，覆盖本章核心概念。

**小胖**：Streaming 是不是变 Kafka 了？

**小白**：这是 **向 Hive 表流式写入** 的 API，不是消息队列；仍受 **Hive 文件布局与 compaction** 约束。

**大师**：技术映射：**Streaming ingest = 微批事务写文件 + 元数据提交**。

**小胖**：失败重试会重复吗？

**小白**：依赖 **事务 ID 与幂等设计**；消费位点与 Hive **batch 边界**要对齐。

**大师**：与 **Exactly-once** 总目标有差距，多数实现是 **at-least-once + 下游去重**。


> **§2·第三轮**（对齐 [template.md](../template.md) 的第三循环）

**小胖**：近实时入仓，延迟降了，会不会「漏数」？

**小白**：至少一次投递下要 **幂等写入** 或 **去重键**；端到端 SLA 要写清 **可接受重复窗口** 与 **对账频率**。

**大师**：把 **watermark、lag、失败重放** 三个指标接到同一仪表盘；Streaming 故障时先判 **上游 backlog** 还是 **Hive 写入瓶颈**。

**技术映射**：**流式入仓可靠性 = min(源投递语义, Sink 幂等设计, 对账工具覆盖度)**。


---

## 3 项目实战（约 1500～2000 字）

### 步骤 1：阅读 `HiveStreamingConnection` Javadoc（源码树 `streaming` 模块）

列出 **Endpoint、RecordWriter、TransactionBatch** 调用顺序。

### 步骤 2：伪代码骨架（不可直接运行，需依赖版本对齐）

```java
// StreamingConnection.Builder 配置 metastore URI、库表、分桶等
// beginTransaction -> write records -> commit / abort
```

### 步骤 3：运维检查点

- Metastore **连接池**  
- **小文件** 速率 vs **合并**  
- **HS2** 与 streaming **锁竞争**

**坑**：**非 ACID/非 ORC** 表限制。  
**坑**：**并发 writer** 同一分区。

**验证**：PoC 压测 **TPS 与文件数** 曲线，与 Flink 方案对比一页纸。

### 步骤 4：PoC 观测项（建议做成表格）

| 指标 | 目标 | 实际 | 结论 |
|------|------|------|------|
| P95 写入延迟 | | | |
| 每分钟新增小文件数 | | | |
| Metastore RPC | | | |
| 失败重试率 | | | |

### 步骤 5：与 Flink→Iceberg 路径的对比维度

- **Exactly-once 成本**  
- **查询语义（时间旅行/审计）**  
- **团队技能栈**  
- **现有 Hive 资产复用度**
### 环境准备（模板对齐）

- **依赖**：HiveServer2 + Beeline + HDFS（或 Docker），参见 [第 2 章](<第 2 章：HDFS 与 Hive 的最小可运行环境.md>)。
- **版本**：以 [source/hive/pom.xml](../source/hive/pom.xml) 为准；仅在非生产库验证。
- **权限**：目标库 DDL/DML 与 HDFS 路径写权限齐备。

### 运行结果与测试验证（模板对齐）

- 各步骤给出「预期 / 验证」；建议 `beeline -f` 批量执行。**自测回执**：SQL 文件链接 + 成功输出 + 失败 stderr 前 80 行。

### 完整代码清单与仓库附录（模板对齐）

- **本章清单**：合并上文可执行片段为单文件纳入团队 Git（建议 `column/_scripts/`）。
- **上游参考**：<https://github.com/apache/hive>（对照本仓库 `source/hive`）。
- **本仓库路径**：`../source/hive`。

---

## 4 项目总结（约 500～800 字）

### 优点与缺点

| 优点 | 缺点 |
|------|------|
| 原生进 Hive | 生态热度低于湖仓一体方案 |
| 与现有表模型兼容 | 运维门槛高 |
| 可利用 ORC | 延迟仍偏近实时而非真流 |

### 适用与不适用

- **适用**：已有 Hive 表、需 **低延迟追加**。  
- **不适用**：强一致秒级全局视图（考虑 Hudi/Iceberg + Flink）。  

### 注意事项

- **版本锁** 与 **安全认证**。  
- **监控**：batch abort 率。  

### 常见生产踩坑

1. **Zookeeper/HMS** 抖动导致 **事务失败风暴**。  
2. **未限流** 打满 NN。  
3. **与离线批写** 同一分区冲突。

### 思考题

1. Streaming 与 **Kafka → HDFS → 分区注册** 链路在 **成本与一致性** 上如何对比？  
2. 若迁移 Iceberg，**写入 API** 如何切换？  
3. 当 `TransactionBatch` **abort 率** 升高时，你按什么顺序排查（网络、HMS、NN、GC）？

### 跨部门推广提示

- **架构**：把本方案纳入 **数据入口技术雷达**。  
- **SRE**：为 streaming 单独 **容量模型**。
