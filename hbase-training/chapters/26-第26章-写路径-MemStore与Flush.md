# 第 26 章：写路径——MemStore、Flush

> 统一模板与检查表：[../00-template-pack.md](../00-template-pack.md)
> 官方文档：[HBase Reference Guide](https://hbase.apache.org/docs)

上一章：[第 25 章](25-第25章-读路径-客户端到RegionServer.md) | 下一章：[第 27 章](27-第27章-WAL-滚动回放与可靠性.md)

---

**受众：主【Dev、Ops】 难度：高级**

### 0）本章路线图（由浅入深）

| 层次 | 你要达成的状态 | 建议用时 |
|------|----------------|----------|
| L1 听懂 | Put → WAL → MemStore → Flush → HFile 主线 | 20 min |
| L2 能做 | 打开 MemStore / Flusher 类注释，写 200 字摘要 | 60 min |
| L3 能讲 | 能解释 flush 尖刺与写入延迟的关系 | 排障 |

### 开场一分钟（趣味钩子）

写入像**洗碗**：你可以先堆在水池里（MemStore），但水池满了必须**开闸放水**（flush）——那一刻下水道（磁盘 IO）会响。多列族像**多个水池共用一个下水口**，一个满可能牵连全家。

### 1）项目背景

- **开发**：理解写入延迟与 flush 关系；大行、批量导入对 memstore 的冲击；与客户端超时联动。
- **运维**：调 memstore 上限、刷写压力；观察 flush 队列长度与告警。
- **测试**：高写入场景下 P99 抖动用例；与容量规划数据结合。
- **若跳过本章**：看到「周期性卡顿」只会重启 RS。

### 2）项目设计（大师 × 小白）

- **小白**：「Put 立刻进 HFile？」
- **大师**：「先 **WAL**，再 **MemStore**；达条件 **flush** 成 **HFile**。」
- **小白**：「WAL 和 MemStore 谁先？」
- **大师**：「主线是 **先 WAL 再内存可见**（概念级，细节读文档/源码）；保证可恢复。」
- **小白**：「flush 为啥会抖？」
- **大师**：「内存数据落盘、生成新文件，可能触发后续 compaction 压力。」
- **小白**：「多列族麻烦在哪？」
- **大师**：「flush 策略更复杂；可能**互相拖慢**。」
- **段子**：小白说「我把 memstore 调到无限大。」大师：「JVM 想给你办追悼会。」

### 3）项目实战（源码导读）

- 打开 [`hbase-server/.../regionserver/MemStore.java`](../../../hbase-server/src/main/java/org/apache/hadoop/hbase/regionserver/MemStore.java)，阅读类注释与公开方法职责（不必通读实现）。
- 打开 [`MemStoreFlusher.java`](../../../hbase-server/src/main/java/org/apache/hadoop/hbase/regionserver/MemStoreFlusher.java)，理清 flush 触发在代码中的调用关系（可配合 IDE Find Usages）。
- 回答：**flush 与 compaction 谁先谁后、各解决什么问题**（书面 200 字）。

**加分**：在测试集群观察一次写入高峰的 flush 相关日志关键字（脱敏截图）。

### 4）项目总结

- **优点**：写内存聚合，顺序写磁盘；吞吐高。
- **缺点**：flush / compaction 不当引发 IO 毛刺。
- **适用**：调优写入密集表；解释周期性延迟。
- **注意**：多列族时 flush 行为更复杂；memstore 与堆比例。
- **踩坑**：memstore 过大导致 RS OOM 风险；忽略 WAL 慢导致写放大感知。
- **运维检查项**：flush 队列长度监控；磁盘 IO 饱和度。
- **测试检查项**：持续写入下 P99 与告警；与 GC 日志关联。

### 5）课堂自测与作业（讲师可选用）

**自测**

1. MemStore 存在的核心收益是什么？
2. flush 产出什么产物？放在哪一层存储？
3. 为什么说「写入慢」不一定是网络问题？

**作业**

- 画写路径时序图：从客户端到 HDFS（标注 WAL 与 HFile）。

---

**返回目录**：[../README.md](../README.md)
