# 第 31 章：Assignment 与 Balance——迁移与 RIT

> 统一模板与检查表：[../00-template-pack.md](../00-template-pack.md)
> 官方文档：[HBase Reference Guide](https://hbase.apache.org/docs)

上一章：[第 30 章](30-第30章-Region-Split-Merge-策略与P99.md) | 下一章：[第 32 章](32-第32章-MVCC与读点-行内可见性与Scan语义.md)

---

**受众：主【Ops、Dev】 难度：高级**

### 0）本章路线图（由浅入深）

| 层次 | 你要达成的状态 | 建议用时 |
|------|----------------|----------|
| L1 听懂 | balance 受表状态、机架、配置制约；RIT 是应急信号 | 25 min |
| L2 能做 | 浏览 Master / Assignment 包，写 OPENING→OPEN 半页笔记 | 90 min |
| L3 能讲 | 能指挥 on-call：先看 procedure 还是先看 RS 日志 | 应急 |

### 开场一分钟（趣味钩子）

Assignment 像**航班改签**：大部分时候自动搞定；一旦进入**长时间 RIT（Region In Transition）**，就像旅客卡在登机口与飞机之间——**地勤（Master）**和**机组（RS）**要一起查。

### 1）项目背景

- **运维**：扩缩容、RS 故障、手动 move 均依赖 Assignment；RIT 长期不消属于应急场景；需熟悉 `hbck` / `hbck2`（按版本）。
- **开发**：理解「Region 不可用窗口」对应用重试的影响；避免无限重试风暴。
- **测试**：迁移、重启期间的读写成功率；与客户端超时配置联合验收。
- **若跳过本章**：扩容后「有的 RS 累死有的闲死」却无法解释。

### 2）项目设计（大师 × 小白）

- **小白**：「balance 会自动吗？」
- **大师**：「Master 侧有均衡逻辑，但**受表状态、机架、配置**制约；异常时要看 procedure 与日志。」
- **小白**：「move Region 安全吗？」
- **大师**：「有**短暂不可用窗口**；业务要容忍重试。」
- **小白**：「RIT 是啥？」
- **大师**：「Region 状态机没走完；长时间挂着要升级处理。」
- **小白**：「我能一次 move 一千个吗？」
- **大师**：「可以，先写**遗嘱**；可能引发风暴。」
- **段子**：小白半夜同时 rolling restart 所有 RS。大师：「月亮不睡你不睡，RIT 陪你到天明。」

### 3）项目实战（源码导读）

- [`HMaster.java`](../../../hbase-server/src/main/java/org/apache/hadoop/hbase/master/HMaster.java)（入口与职责）
- [`MasterRpcServices.java`](../../../hbase-server/src/main/java/org/apache/hadoop/hbase/master/MasterRpcServices.java)（RPC 面）
- 结合 AssignmentManager / Procedure 相关包（2.x+ 多在 `org.apache.hadoop.hbase.master.assignment` 等路径，以当前分支为准）做 **1 页** 流程笔记：Region **OPENING → OPEN**。

**演练桌（无集群也可）**：给一张「某 Region 长时间 CLOSING」假日志截图，分组写**三步排查**。

### 4）项目总结

- **优点**：自动均衡与故障转移。
- **缺点**：RIT 卡住需人工介入（视工具版本）；误操作引发风暴。
- **适用**：扩缩容、RS 替换、机架迁移。
- **注意**：procedure 积压；与 ZK / 协调组件健康绑定。
- **踩坑**：同时大量 move 引发风暴；忽略表禁用状态。
- **运维检查项**：`hbck` / `hbck2` 使用培训（按版本文档）；RIT 告警。
- **测试检查项**：迁移期间读成功率；重试上限。

### 5）课堂自测与作业（讲师可选用）

**自测**

1. balance 完美均衡为何在现实中少见？
2. RIT 长期存在时，优先怀疑链路上的哪两类组件？
3. 客户端在 Region 迁移期间应具备什么素质（概念级）？

**作业**

- 写一页「Region 迁移 Runbook」模板：触发条件、命令、验证、回滚（可空着命令占位）。

---

**返回目录**：[../README.md](../README.md)
