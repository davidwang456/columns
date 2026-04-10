# 第 30 章：Region Split / Merge——策略与 P99

> 统一模板与检查表：[../00-template-pack.md](../00-template-pack.md)
> 官方文档：[HBase Reference Guide](https://hbase.apache.org/docs)

上一章：[第 29 章](29-第29章-Compaction-Minor-Major与IO放大.md) | 下一章：[第 31 章](31-第31章-Assignment与Balance-迁移与RIT.md)

---

**受众：主【Ops、Dev】 难度：高级**

### 0）本章路线图（由浅入深）

| 层次 | 你要达成的状态 | 建议用时 |
|------|----------------|----------|
| L1 听懂 | Region 过大/过小各有什么问题 | 20 min |
| L2 能做 | 读 RegionSplitPolicy 实现类，举两种策略差异 | 60 min |
| L3 能讲 | 能把 split 尖刺与业务 SLA 对齐沟通 | 管理 |

### 开场一分钟（趣味钩子）

Region 像**披萨切块**：太大，一口噎死（compaction/flush 成本高）；太小，盘子全是边（元数据膨胀、管理开销大）。Split 是**现场再切一刀**——切的时候桌上会晃一下（P99 抖动）。

### 1）项目背景

- **运维**：分裂 / 合并影响元数据与负载分布；与 balance、RIT 联动。
- **开发**：与 RowKey 热点、Region 大小参数共同决定扩展行为；设计表时预留分裂点。
- **测试**：分裂过程中读写错误率、重试；与混沌演练结合。
- **若跳过本章**：单 Region 巨胖时只会「加机器」无效扩容。

### 2）项目设计（大师 × 小白）

- **小白**：「Region 太大就慢？」
- **大师**：「过大 flush/compaction 成本高；过小元数据多。**分裂策略**在两者之间找平衡。」
- **小白**：「能手动 split 吗？」
- **大师**：「可以，但要**懂后果**；可能短期更均衡，也可能制造空 Region。」
- **小白**：「merge 呢？」
- **大师**：「回收过小 Region；前提条件与风险读文档，生产要评审。」
- **小白**：「split 和 RowKey 啥关系？」
- **大师**：「热点 + 顺序键会让分裂**追着尾巴跑**；预分区更治本。」
- **段子**：小白每小时手动 split 一次。大师：「你和 Region 玩**打地鼠**。」

### 3）项目实战（源码导读）

- [`RegionSplitPolicy.java`](../../../hbase-server/src/main/java/org/apache/hadoop/hbase/regionserver/RegionSplitPolicy.java) 及其实现类（如 `ConstantSizeRegionSplitPolicy` 等）。
- 阅读官方 [Region Split](https://hbase.apache.org/book.html#region-split) 相关章节。

**输出**：对比表（两行即可）

| 策略 / 概念 | 触发直觉 | 对顺序写入热点帮助 |
|-------------|----------|---------------------|
|  |  |  |

### 4）项目总结

- **优点**：随数据增长水平扩展。
- **缺点**：分裂瞬间与元数据更新可能带来抖动；误配导致空 Region。
- **适用**：大表增长规划；热点治理辅助手段。
- **注意**：自定义 split 与 RowKey 设计配合；与 merge 运维策略。
- **踩坑**：过小 Region 导致元数据膨胀；盲目手动 split。
- **测试检查项**：分裂过程中读写错误率；客户端重试行为。
- **运维检查项**：Region 数趋势；分裂队列与告警。

### 5）课堂自测与作业（讲师可选用）

**自测**

1. 为什么说 split 不是热点问题的「万能药」？
2. Region 过小会带来哪类系统开销？
3. 分裂过程中用户请求可能经历什么（概念级）？

**作业**

- 观察（或假设）一张表 Region 数随时间曲线，标注预期分裂点与风险。

---

**返回目录**：[../README.md](../README.md)
