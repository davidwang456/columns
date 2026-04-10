# 第 14 章：Filter——FilterList、代价与误用

> 统一模板与检查表：[../00-template-pack.md](../00-template-pack.md)
> 官方文档：[HBase Reference Guide](https://hbase.apache.org/docs)

上一章：[第 13 章](13-第13章-RowKey设计-散列反转时间盐与热点.md) | 下一章：[第 15 章](15-第15章-批量-BufferedMutator与batch背压.md)

---

**受众：主【Dev】 难度：中级**

### 0）本章路线图（由浅入深）

| 层次 | 你要达成的状态 | 建议用时 |
|------|----------------|----------|
| L1 听懂 | 明白 Filter 在服务端做但仍可能扫大量数据 | 15 min |
| L2 能做 | 写 SingleColumnValueFilter + FilterList 组合实验 | 45 min |
| L3 能辩 | 能拒绝「Filter 代替二级索引」的不合理需求 | 评审 |

### 开场一分钟（趣味钩子）

Filter 像**在传送带上挑苹果**：你可以**站近一点少弯腰**（少传网络），但如果传送带上有**一整车苹果**（超大 Scan），你还是会累死在 RS。**先收窄 RowKey，再谈 Filter**。

### 1）项目背景

- **开发**：在服务端过滤减少网络传输；理解 **CPU 与 IO** 代价；与 `batch/caching` 交互阅文档。
- **测试**：验证 Filter 与列缺失、空值、多版本组合；负面用例要覆盖「无匹配行」。
- **运维**：RS CPU 异常高时，排查是否有**宽范围 Scan + 重 Filter** 业务上线。
- **若跳过本章**：开发用 Filter 「治百病」，集群被拖垮。

### 2）项目设计（大师 × 小白）

- **小白**：「Filter 能代替二级索引吗？」
- **大师**：「**不能通用代替**；大范围 Scan + Filter 仍可能很重。」
- **小白**：「多个条件呢？」
- **大师**：「`FilterList` 组合 **MUST_PASS_ALL / MUST_PASS_ONE**。」
- **小白**：「Filter 能下推吗？」
- **大师**：「部分逻辑在服务端；但仍取决于**扫描范围**与存储布局。」
- **小白**：「SingleColumnValueFilter 列不存在会怎样？」
- **大师**：「行为要查文档与版本说明；**测试必须覆盖**。」
- **段子**：小白说「我 Filter 里嵌了正则。」大师：「CPU 想给你发律师函。」

### 3）项目实战

```java
Scan scan = new Scan().addFamily(Bytes.toBytes("d"));
Filter f1 = new SingleColumnValueFilter(
    Bytes.toBytes("d"), Bytes.toBytes("status"),
    CompareOperator.EQUAL, Bytes.toBytes("PAID"));
scan.setFilter(f1);
```

**对比实验（必做）**

1. **窄 RowKey 范围 Scan + Filter**（如某用户前缀内）。
2. **宽范围 Scan + Filter**（讲师控制上限，避免生产事故）。

记录：耗时、RPC 次数（若可观察）、RS CPU（测试环境）。**结论**用一句话写清。

**验收**：实验表 + 一句「何时禁用该写法」的规范。

### 4）项目总结

- **优点**：灵活、减少客户端处理；适合已知范围内的精细化筛选。
- **缺点**：滥用导致 RS CPU 高；与宽 Scan 组合是常见事故模式。
- **适用**：窄 RowKey + 列条件；服务端预处理。
- **注意**：`Filter` 与 `batch/caching` 交互阅官方文档；版本差异记录。
- **踩坑**：以为 Filter 会魔法式加速全表扫；忽略列缺失行为。
- **测试检查项**：无匹配行、部分列缺失、多版本；与 Scan 语义结合的预期。

### 5）课堂自测与作业（讲师可选用）

**自测**

1. Filter 主要节省什么资源？不节省什么？
2. `MUST_PASS_ALL` 与 `MUST_PASS_ONE` 各举一个业务例子。
3. 为什么说「二级索引」问题不能靠 Filter 硬扛？

**作业**

- 找一条线上或开源里「危险 Scan」的伪代码，改写为 RowKey 前缀可剪枝版本（可纸面）。

---

**返回目录**：[../README.md](../README.md)
