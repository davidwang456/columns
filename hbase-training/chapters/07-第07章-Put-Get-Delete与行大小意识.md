# 第 7 章：Put、Get、Delete 与行大小意识

> 统一模板与检查表：[../00-template-pack.md](../00-template-pack.md)
> 官方文档：[HBase Reference Guide](https://hbase.apache.org/docs)

上一章：[第 6 章](06-第06章-Java入门-Configuration-Connection-Table.md) | 下一章：[第 8 章](08-第08章-Scan基础-边界与caching-Scan非快照.md)

---

**受众：主【Dev】 难度：基础**

### 0）本章路线图（由浅入深）

| 层次 | 你要达成的状态 | 建议用时 |
|------|----------------|----------|
| L1 听懂 | 知道单行原子、Delete 墓碑语义、大行危害 | 20 min |
| L2 能做 | 独立完成 Put/Get/Delete，并处理 `Result.isEmpty()` | 40 min |
| L3 能辩 | 能评估「大 JSON / 图片」是否应走 MOB 或对象存储 | 与第 18、39 章衔接 |

### 开场一分钟（趣味钩子）

Put/Get 像**快递柜存取**：一次操作只动**一个格子（一行）**里的东西，原子完成。你若非要一次塞进去**半辆自行车**，柜门能关上，但**搬运工（RS）和叉车（Compaction）**会在背后骂你。工程上：**大行是技术债的物理形态**。

### 1）项目背景

- **开发**：单行原子写入；避免单行过大；删除要理解**墓碑**与可见性，别写「假删除」业务。
- **测试**：断言 Put 后 Get 的值与列存在性；覆盖**空结果、缺列族、错误编码**。
- **运维**：大行推高 flush、split、RPC 与磁盘放大；告警上表现为单 Region 异常胖。
- **若跳过本章**：后续批量写、Filter、性能调优都失去基础参照。

### 2）项目设计（大师 × 小白）

- **小白**：「一个 Put 里放 1MB 字符串行吗？」
- **大师**：「**技术上可能，工程上危险**：影响 flush、拆分、RPC。大对象考虑 MOB 或对象存储。」
- **小白**：「Delete 是删整行吗？」
- **大师**：「可指定列或时间范围；**墓碑**与 compaction 有关，需理解可见性。」
- **小白**：「我 Delete 了为什么还能 get 到？」
- **大师**：「可能读到**更早时间戳**的 Cell；或 tombstone 尚未 compact；要对照 VERSIONS 与时间戳。」
- **小白**：「Get 要不要加列裁剪？」
- **大师**：「要。**只取需要的列**减少网络与反序列化。」
- **段子**：小白说「我 Result 没判空就 `.getValue`。」大师：「你获得了**随机 NPE 盲盒**。」

### 3）项目实战

**代码骨架（必做）**

```java
byte[] row = Bytes.toBytes("order#10001");
Put put = new Put(row);
put.addColumn(Bytes.toBytes("d"), Bytes.toBytes("status"), Bytes.toBytes("PAID"));
table.put(put);

Get get = new Get(row);
get.addColumn(Bytes.toBytes("d"), Bytes.toBytes("status"));
Result r = table.get(get);
if (r.isEmpty()) {
  // 明确分支：不存在 vs 权限 vs 路由错误（结合日志）
} else {
  byte[] v = r.getValue(Bytes.toBytes("d"), Bytes.toBytes("status"));
}
```

**实验 1：行大小感受（讲师带领）**

- 写入 10KB、100KB、1MB（测试环境）Cell 各一次，观察 RS 日志与延迟（定性即可）。
- **讨论**：业务上 1MB 字段应放哪里？

**实验 2：Delete 观察**

- `Put` 两个版本后 `Delete` 最新，再 `get` 带 `VERSIONS => 2`，记录看到的现象。

**验收**：代码里必须有 `isEmpty()` 分支；实验记录 5 行以内。

### 4）项目总结

- **优点**：行级操作简单直接；语义清晰。
- **缺点**：大行、大 Cell 拖累集群；Delete 语义需培训业务方。
- **适用**：订单、状态、计数（也可用 Increment，第 17 章）。
- **注意**：字符编码统一用 `Bytes` 或约定 UTF-8；监控单行大小分布（若有）。
- **踩坑**：未校验 `Result.isEmpty()`；误把 Delete 当「立即物理抹除」。
- **测试检查项**：空结果、多列、不存在列族的错误处理；编码边界（中文、emoji）。

### 5）课堂自测与作业（讲师可选用）

**自测**

1. 单行写操作的原子性范围是什么？
2. 为什么「大 Cell」伤害的不只是网络，还有 Compaction？
3. `getValue` 返回 null 可能有哪些原因？

**作业**

- 打印 `Result` 中每个 Cell 的 family、qualifier、timestamp（见 00-template-pack 示例 A）。

---

**返回目录**：[../README.md](../README.md)
