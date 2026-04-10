# 第 28 章：HFile / StoreFile——块、索引、Bloom

> 统一模板与检查表：[../00-template-pack.md](../00-template-pack.md)
> 官方文档：[HBase Reference Guide](https://hbase.apache.org/docs)

上一章：[第 27 章](27-第27章-WAL-滚动回放与可靠性.md) | 下一章：[第 29 章](29-第29章-Compaction-Minor-Major与IO放大.md)

---

**受众：主【Dev】 难度：高级**

### 0）本章路线图（由浅入深）

| 层次 | 你要达成的状态 | 建议用时 |
|------|----------------|----------|
| L1 听懂 | HFile 是二进制块文件；索引与 Bloom 的作用 | 20 min |
| L2 能做 | 读 StoreFile / Scanner 类注释，对照官方 HFile 图 | 60 min |
| L3 能讲 | 能解释小文件多 → 读放大 → compaction 需求 | 闭环到 29 章 |

### 开场一分钟（趣味钩子）

HFile 像**精装百科全书的分册**：每册有**目录（索引）**和**「这个词大概不在本册」贴纸（Bloom）**。Get/Scan 不是「打开 txt 搜索」，而是**按册、按块**精准翻——所以文件册数太多会累断手（读放大）。

### 1）项目背景

- **开发**：理解读放大与 Scan 成本；明白为何 compaction 不是「可有可无」。
- **运维**：文件数、块大小、压缩与 IO 联动；与监控指标对照。
- **测试**：大表 Scan 基线；升级后文件格式兼容性验证（按版本）。
- **若跳过本章**：调参只能「感觉调 block size」。

### 2）项目设计（大师 × 小白）

- **小白**：「HFile 是文本吗？」
- **大师**：「**二进制块结构**，有索引与 Bloom 过滤不存在数据。」
- **小白**：「Bloom 一定准吗？」
- **大师**：「**假阳性**可能多读块；**无假阴性**（概念级）帮助跳过。」
- **小白**：「块越大越好？」
- **大师**：「大块顺序读好，随机读可能更粗粒度；要场景化。」
- **小白**：「StoreFile 和 HFile 啥关系？」
- **大师**：「日常口语常混用；读源码看类职责更准确。」
- **段子**：小白说「有 Bloom 就不会读磁盘。」大师：「Bloom 听了想打人。」

### 3）项目实战（源码导读）

- [`StoreFile.java`](../../../hbase-server/src/main/java/org/apache/hadoop/hbase/regionserver/StoreFile.java)
- [`StoreFileScanner.java`](../../../hbase-server/src/main/java/org/apache/hadoop/hbase/regionserver/StoreFileScanner.java)  
阅读类注释；对照官方 [HFile](https://hbase.apache.org/book.html#hfile)。

**输出**：列出 **3 个**与「跳过 IO」相关的机制 + **1 个**仍可能多读的场景。

### 4）项目总结

- **优点**：高效顺序读、可跳过无关块。
- **缺点**：小文件多、compaction 前读放大高。
- **适用**：Compaction 策略选型讨论；读延迟分析。
- **注意**：Bloom 假阳性概率；块大小与压缩联动。
- **踩坑**：认为「有 Bloom 就不会读磁盘」；忽略 scanner 与缓存层次。
- **测试检查项**：升级后文件格式；大范围 Scan 基线。
- **运维检查项**：Storefile 数量趋势；与 compaction 告警联动。

### 5）课堂自测与作业（讲师可选用）

**自测**

1. 索引帮助解决什么问题？
2. Bloom 假阳性对正确性有影响吗？对性能呢？
3. 为什么文件数膨胀会影响读延迟？

**作业**

- 用一段话把第 25～28 章串成「一次 Get 的旅程」（从客户端到块读取）。

---

**返回目录**：[../README.md](../README.md)
