# 第 8 章：Scan 基础——边界、caching；Scan 非快照

> 统一模板与检查表：[../00-template-pack.md](../00-template-pack.md)
> 官方文档：[HBase Reference Guide](https://hbase.apache.org/docs)

上一章：[第 7 章](07-第07章-Put-Get-Delete与行大小意识.md) | 下一章：[第 9 章](09-第09章-Admin建表-描述符列族预分区.md)

---

**受众：主【Dev、QA】 难度：基础**

### 0）本章路线图（由浅入深）

| 层次 | 你要达成的状态 | 建议用时 |
|------|----------------|----------|
| L1 听懂 | 能复述：Scan **不是**表级一致快照 | 15 min |
| L2 能做 | 会设 start/stop、caching，能写闭合区间注意点 | 40 min |
| L3 能辩 | 能与产品经理解释「列表页与详情短暂不一致」是否可接受 | 需求级 |

### 开场一分钟（趣味钩子）

Scan 像**逛夜市录像**：你从头到尾走一趟，摊位（行）可能**中途换了招牌**。你不能说「我录像里的夜市是同一时刻的平行宇宙」——官方文档说的就是：**别用 OLTP 的隔离级别硬套 Scan**。

### 1）项目背景

- **开发**：列表、导出依赖 Scan；不得假设快照隔离；分页要**稳定 RowKey 续扫**或明确产品语义。
- **测试**：并发写入 + Scan 的预期需写明（见 [ACID semantics](https://hbase.apache.org/acid-semantics)）；缺陷分级要区分「语义不符」与「真 bug」。
- **运维**：大范围 Scan 是热点与带宽杀手；要与业务约定**错峰、限流、只读副本**等策略（视架构）。
- **若跳过本章**：列表接口容易出现「验收标准不成立」的扯皮。

### 2）项目设计（大师 × 小白）

- **小白**：「Scan 像数据库可重复读吗？」
- **大师**：「**不像**。官方明确：**Scan 不是表级一致快照**；多行可能来自不同时间点。」
- **小白**：「那分页呢？」
- **大师**：「用 **start/stopRow**、**PageFilter** 或 **last row 续扫**；配合 **setCaching** 控制 RPC 次数。」
- **小白**：`setBatch` 和 `setCaching` 啥区别？
- **大师**：「简记：**caching 管每次 RPC 带多少行**；**batch 管一行里每次带多少列**（细读官方 Client 文档防误用）。」
- **小白**：「Scan 能随便多线程共享吗？」
- **大师**：「`ResultScanner` 使用规则阅文档；乱共享会得到**玄学迭代器**。」
- **段子**：小白说「我 Scan 全表做统计。」大师：「可以，先签**免责与预算**。」

### 3）项目实战

**代码（必做）**

```java
Scan scan = new Scan()
    .withStartRow(Bytes.toBytes("order#10000"), true)
    .withStopRow(Bytes.toBytes("order#20000"), false)
    .addFamily(Bytes.toBytes("d"));
scan.setCaching(200);
try (ResultScanner rs = table.getScanner(scan)) {
  for (Result res : rs) {
    // 处理；注意 res 可能无某些列
  }
}
```

**小实验：并发下的「现象记录」**

- 线程 A 持续 Put 新行（固定前缀），线程 B Scan 同前缀。
- **不要求**消灭重复/漏读，而是**记录现象**并对照 ACID 文档写 3 句话结论。

**验收**

- 能解释 `withStartRow` 第二个参数 true/false 的含义（含边界行是否包含）。
- 贴出 RPC 次数随 `caching` 变化的**定性**观察（可用日志或 metrics）。

### 4）项目总结

- **优点**：顺序读吞吐高；适合前缀与范围。
- **缺点**：滥用全表扫；语义与业务误解；与 Filter 组合可能重（第 14 章）。
- **适用**：RowKey 连续范围、前缀；导出与批处理。
- **注意**：`setBatch` 与 `setCaching` 区别；客户端超时与 scanner 生命周期。
- **踩坑**：把 Scan 当 OLTP 多维检索；深分页不带 RowKey 续扫。
- **测试检查项**：是否引用 ACID 文档中 Scan 条目；并发场景的预期是否文档化。

### 5）课堂自测与作业（讲师可选用）

**自测**

1. 用业务语言解释「Scan 非快照」对用户列表页意味着什么？
2. 为什么说「全表 Scan + 客户端过滤」常常是坏主意？
3. `ResultScanner` 为什么必须在 finally / try-with-resources 里 close？

**作业**

- 设计 3 条测试用例：空范围、单行边界、并发写入交错（步骤 + 预期 + 是否必现）。

---

**返回目录**：[../README.md](../README.md)
