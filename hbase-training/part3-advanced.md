# 高级篇：第 25～35 章（原理、源码导读、生产级）

前置：[part2-intermediate.md](part2-intermediate.md)。  
本仓库路径均以 `hbase-server`、`hbase-client` 模块为锚点（相对仓库根目录）。

---

## 第 25 章：读路径——从客户端到 RegionServer

**受众：主【Dev】 辅【Ops、QA】 难度：高级**

### 1）项目背景

- **开发**：排查「读慢」需知 meta 缓存、Region 定位。
- **运维**：meta 与网络抖动影响全集群读。
- **测试**：模拟 meta 延迟时的客户端表现。

### 2）项目设计（大师 × 小白）

- **小白**：「Get 直接打到存数据的机器？」
- **大师**：「客户端先解析 **RowKey → Region**；查 **hbase:meta**（带本地缓存），再向 **RegionServer** 发 RPC。」

### 3）项目实战

1. 阅读官方 [Client Architecture](https://hbase.apache.org/book.html#client) 与 [Architecture](https://hbase.apache.org/book.html#arch)。
2. 源码入口（客户端）：浏览 `hbase-client/src/main/java/org/apache/hadoop/hbase/client/` 下与 **meta 定位、RegionLocator** 相关的类（如 `ConnectionImplementation`、`RegionLocator` 实现类，随版本略有不同）。
3. 在集群上用 trace 或日志标出一次 Get 经过的组件。

### 4）项目总结

- **优点**：理解后定位「慢在定位还是慢在 RS」更快。
- **缺点**：版本差异大，需对照分支阅读。
- **适用**：性能优化、故障分析。
- **注意**：meta 表自身要健康。
- **踩坑**：误以为客户端每次全表扫找数据。
- **运维检查项**：meta Region 是否均衡。
- **测试检查项**：Region 迁移期间的读成功率。

---

## 第 26 章：写路径——MemStore、Flush

**受众：主【Dev、Ops】 难度：高级**

### 1）项目背景

- **开发**：理解写入延迟与 flush 关系。
- **运维**：调 memstore 上限、刷写压力。

### 2）项目设计（大师 × 小白）

- **小白**：「Put 立刻进 HFile？」
- **大师**：「先 **WAL**，再 **MemStore**；达条件 **flush** 成 **HFile**。」

### 3）项目实战（源码导读）

- 打开 [`hbase-server/.../regionserver/MemStore.java`](../../hbase-server/src/main/java/org/apache/hadoop/hbase/regionserver/MemStore.java)，阅读类注释与公开方法职责（不必通读实现）。
- 打开 [`MemStoreFlusher.java`](../../hbase-server/src/main/java/org/apache/hadoop/hbase/regionserver/MemStoreFlusher.java)，理清 flush 触发在代码中的调用关系（可配合 IDE Find Usages）。
- 回答：**flush 与 compaction 谁先谁后、各解决什么问题**（书面 200 字）。

### 4）项目总结

- **优点**：写内存聚合，顺序写磁盘。
- **缺点**：flush/compaction 不当引发 IO 毛刺。
- **适用**：调优写入密集表。
- **注意**：多列族时 flush 行为更复杂。
- **踩坑**：memstore 过大导致 RS OOM 风险。
- **运维检查项**：flush 队列长度监控。

---

## 第 27 章：WAL——滚动、回放与可靠性

**受众：主【Dev、Ops】 难度：高级**

### 1）项目背景

- **运维**：磁盘满、旧 WAL 清理、RS 崩溃恢复。
- **开发**：理解「成功返回」与落盘路径。

### 2）项目设计（大师 × 小白）

- **小白**：「WAL 就是日志文件？」
- **大师**：「保证 **崩溃可恢复**；滚动与归档策略影响磁盘与恢复时间。」

### 3）项目实战（源码导读）

- 在 `hbase-server/src/main/java/org/apache/hadoop/hbase/regionserver/` 下搜索 **WAL** 相关实现（如 `WAL`、`FSHLog` 等，具体类名随版本变化）。
- 结合官方 [WAL](https://hbase.apache.org/book.html#wal) 章节，画 **RS 宕机 → 日志回放** 顺序图。

### 4）项目总结

- **优点**：持久化与恢复基石。
- **缺点**：同步策略与延迟权衡。
- **适用**：灾备与 RPO 讨论。
- **注意**：多 RS 同时故障的极端场景。
- **踩坑**：误删 `oldWALs` 目录。
- **测试检查项**：kill RS 进程后数据可恢复性抽样。

---

## 第 28 章：HFile / StoreFile——块、索引、Bloom

**受众：主【Dev】 难度：高级**

### 1）项目背景

- **开发**：理解读放大与 Scan 成本。

### 2）项目设计（大师 × 小白）

- **小白**：「HFile 是文本吗？」
- **大师**：「**二进制块结构**，有索引与 Bloom 过滤不存在数据。」

### 3）项目实战（源码导读）

- [`StoreFile.java`](../../hbase-server/src/main/java/org/apache/hadoop/hbase/regionserver/StoreFile.java)
- [`StoreFileScanner.java`](../../hbase-server/src/main/java/org/apache/hadoop/hbase/regionserver/StoreFileScanner.java)  
阅读类注释；对照官方 [HFile](https://hbase.apache.org/book.html#hfile)。

### 4）项目总结

- **优点**：高效顺序读、可跳过无关块。
- **缺点**：小文件多、compaction 前读放大高。
- **适用**：Compaction 策略选型讨论。
- **注意**：Bloom 假阳性概率。
- **踩坑**：认为「有 Bloom 就不会读磁盘」。

---

## 第 29 章：Compaction——Minor / Major 与 IO 放大

**受众：主【Ops、Dev】 难度：高级**

### 1）项目背景

- **运维**：Compaction 与磁盘 IO、P99 抖动直接相关。
- **开发**：文件数过多导致读放大，需从原理上理解为何「有时突然变慢」。

### 2）项目设计（大师 × 小白）

- **小白**：「Compaction 能关吗？」
- **大师**：「**不能指望长期关闭**；文件数与读性能会恶化。」

### 3）项目实战（源码导读）

- [`CompactSplit.java`](../../hbase-server/src/main/java/org/apache/hadoop/hbase/regionserver/CompactSplit.java)
- 结合官方 [Compaction](https://hbase.apache.org/book.html#compaction)  
记录：**Minor** 与 **Major** 各触发条件（概念级）及运维观察指标。

### 4）项目总结

- **优点**：减少文件数、清理墓碑。
- **缺点**：Major 可能 IO 与延迟尖刺。
- **适用**：读延迟抖动排查。
- **注意**：业务低峰窗口、限流（若版本支持）。
- **踩坑**：手动 major 在生产大表上无节制执行。
- **运维检查项**：compaction 队列长度。

---

## 第 30 章：Region Split / Merge——策略与 P99

**受众：主【Ops、Dev】 难度：高级**

### 1）项目背景

- **运维**：分裂/合并影响元数据与负载分布。
- **开发**：与 RowKey 热点、Region 大小参数共同决定扩展行为。

### 2）项目设计（大师 × 小白）

- **小白**：「Region 太大就慢？」
- **大师**：「过大 flush/compaction 成本高；过小元数据多。**分裂策略**在两者之间找平衡。」

### 3）项目实战（源码导读）

- [`RegionSplitPolicy.java`](../../hbase-server/src/main/java/org/apache/hadoop/hbase/regionserver/RegionSplitPolicy.java) 及其实现类（如 `ConstantSizeRegionSplitPolicy` 等）。
- 阅读官方 [Region Split](https://hbase.apache.org/book.html#region-split) 相关章节。

### 4）项目总结

- **优点**：随数据增长水平扩展。
- **缺点**：分裂瞬间与元数据更新可能带来抖动。
- **适用**：大表增长规划。
- **注意**：自定义 split 与 RowKey 设计配合。
- **踩坑**：过小 Region 导致元数据膨胀。
- **测试检查项**：分裂过程中读写错误率。

---

## 第 31 章：Assignment 与 Balance——迁移与 RIT

**受众：主【Ops、Dev】 难度：高级**

### 1）项目背景

- **运维**：扩缩容、RS 故障、手动 move 均依赖 Assignment；RIT 长期不消属于应急场景。
- **开发**：理解「Region 不可用窗口」对应用重试的影响。

### 2）项目设计（大师 × 小白）

- **小白**：「balance 会自动吗？」
- **大师**：「Master 侧有均衡逻辑，但**受表状态、机架、配置**制约；异常时要看 procedure 与日志。」

### 3）项目实战（源码导读）

- [`HMaster.java`](../../hbase-server/src/main/java/org/apache/hadoop/hbase/master/HMaster.java)（入口与职责）
- [`MasterRpcServices.java`](../../hbase-server/src/main/java/org/apache/hadoop/hbase/master/MasterRpcServices.java)（RPC 面）
- 结合 AssignmentManager / Procedure 相关包（2.x+ 多在 `org.apache.hadoop.hbase.master.assignment` 等路径，以当前分支为准）做 **1 页** 流程笔记：Region **OPENING → OPEN**。

### 4）项目总结

- **优点**：自动均衡与故障转移。
- **缺点**：RIT 卡住需人工介入（视工具版本）。
- **适用**：扩缩容、RS 替换。
- **注意**：procedure 积压。
- **踩坑**：同时大量 move 引发风暴。
- **运维检查项**：`hbck` / `hbck2` 使用培训（按版本文档）。

---

## 第 32 章：MVCC 与读点——行内可见性与 Scan 语义衔接

**受众：主【Dev】 难度：高级**

### 1）项目背景

- **开发**：解释并发读写下列表与详情页「为何偶尔不一致」时需落到 MVCC 与 Scan 语义，而非笼统说「缓存」。

### 2）项目设计（大师 × 小白）

- **小白**：「MVCC 是不是和 PostgreSQL 一样？」
- **大师**：「**行内版本与读点**有相似思想，但 **Scan 仍非表级快照**；不要混用隔离级别类比。」

### 3）项目实战（源码导读）

- [`MultiVersionConcurrencyControl.java`](../../hbase-server/src/main/java/org/apache/hadoop/hbase/regionserver/MultiVersionConcurrencyControl.java)
- 再读 [ACID semantics](https://hbase.apache.org/acid-semantics) 中 **Consistency of Scans** 与行内原子段落。

### 4）项目总结

- **优点**：把「行内一致」与「Scan 非快照」从原理上打通。
- **缺点**：概念抽象。
- **适用**：与业务解释「为何看到中间状态」。
- **注意**：与客户端时间戳写入的关系。
- **踩坑**：混用自定义 timestamp 导致「看不到刚写的」。

---

## 第 33 章：RPC 与 Protobuf——服务边界

**受众：主【Dev】 难度：高级**

### 1）项目背景

- **开发**：自定义 Filter、Coprocessor 或排查「服务端到底执行了啥」时需能定位 RPC 层。

### 2）项目设计（大师 × 小白）

- **小白**：「HBase 用 HTTP 吗？」
- **大师**：「客户端与服务端主要是 **Hadoop RPC + Protobuf** 定义的服务接口；不是 REST。」

### 3）项目实战（源码导读）

- [`MasterRpcServices.java`](../../hbase-server/src/main/java/org/apache/hadoop/hbase/master/MasterRpcServices.java)
- 在 `hbase-server` 中查找 **RegionServer** 侧 `...RpcServices` 类（名称随版本如 `RSRpcServices`），列出 **5 个** 与读写相关的 RPC 方法名（从接口或类声明中浏览即可）。

### 4）项目总结

- **优点**：读源码时知道从哪一层入手。
- **缺点**：protobuf 与版本兼容细节多。
- **适用**：自定义 filter/coprocessor 联调。
- **注意**：客户端与服务端版本匹配。
- **踩坑**：用反射 hack 私有 RPC。

---

## 第 34 章：Coprocessor——Observer、Endpoint 与发布风险

**受众：主【Dev】 辅【Ops】 难度：高级**

### 1）项目背景

- **开发**：在服务端扩展逻辑前评估是否必须用 Coprocessor。
- **运维**：jar 升级与 RS 滚动发布风险。

### 2）项目设计（大师 × 小白）

- **小白**：「Coprocessor 能像数据库触发器吗？」
- **大师**：「类似，但在 **RegionServer 进程内**；**错一次影响面大**，升级要小心。」

### 3）项目实战

- 阅读官方 [Coprocessor](https://hbase.apache.org/book.html#cp) 与 [HBase APIs](https://hbase.apache.org/book.html#hbase-apis)。
- **实验**：最小 **Observer**（仅日志，禁止生产直接拷贝）部署到测试表；记录 jar 分发与 `hbase-site` / 表属性配置步骤。

### 4）项目总结

- **优点**：服务端过滤、聚合、权限扩展点。
- **缺点**：调试难、版本耦合。
- **适用**：强定制、减少数据移动。
- **注意**：代码必须极健壮，避免阻塞 RS。
- **踩坑**：在 coprocessor 内做重网络 IO。
- **运维检查项**：发布 checklist、回滚 jar。

---

## 第 35 章：Replication、DR 拓扑与切换演练

**受众：主【Ops、Dev】 难度：高级**

### 1）项目背景

- **运维**：多机房与灾备合规；主备切换 Runbook。
- **开发**：读写路由、幂等与冲突解决策略。

### 2）项目设计（大师 × 小白）

- **小白**：「Replication 是同步的吗？」
- **大师**：「常见是**异步复制**；RPO 不为零，应用要接受最终一致窗口。」

### 3）项目实战

- 阅读官方 [Cluster Replication](https://hbase.apache.org/book.html#replication) 与仓库内 `hbase-website` 对应文档。
- **演练**（测试双集群）：主备或主主拓扑择一；写出 **切换步骤**、**RPO/RTO 假设**、**回切条件**；开发确认应用 **读主写主** 规则。

### 4）项目总结

- **优点**：容灾、就近读。
- **缺点**：延迟、冲突、运维复杂。
- **适用**：多机房、合规。
- **注意**：peer 状态、带宽与积压。
- **踩坑**：双写无冲突解决策略。
- **测试检查项**：断网、延迟注入后的数据一致性抽样。

---

## 高级篇阅读清单（汇总）

| 主题 | 建议入口文件（本仓库） |
|------|------------------------|
| MemStore / Flush | `hbase-server/.../regionserver/MemStore.java`, `MemStoreFlusher.java` |
| Compaction | `hbase-server/.../regionserver/CompactSplit.java` |
| StoreFile | `hbase-server/.../regionserver/StoreFile.java` |
| Split 策略 | `hbase-server/.../regionserver/RegionSplitPolicy.java` |
| Master | `hbase-server/.../master/HMaster.java`, `MasterRpcServices.java` |
| MVCC | `hbase-server/.../regionserver/MultiVersionConcurrencyControl.java` |
| 客户端连接 | `hbase-client/.../client/ConnectionFactory.java`, `Table.java` |

**高级篇完。综合实战：** [part4-capstone.md](part4-capstone.md)
