# 第 18 章：Join 全家桶：Shuffle Join、Map Join、SMB Join

> **专栏分档**：中级篇  
> **总纲索引**：[hive-column-outline.md](../hive-column-outline.md)（第五章 · 第 18 章对照表）  
> **业务主线**：电商平台「用户行为 + 交易」离线数仓（曝光、点击、下单、退款、风控特征）。

## 本章大纲备忘（写作前对照总纲）

| 项 | 内容 |
|----|------|
| 一句话摘要 | 各类 Join 触发条件与改写。 |
| 业务锚点 / 技术焦点 | 大表关联广告曝光明细。 |
| 源码或文档锚点 | `ql`。 |

单章目标篇幅 **3000～5000 字**，四段结构对齐 [template.md](../template.md)。

---

## 1 项目背景（约 500 字）

广告曝光事实表 **10TB/日** 与创意维表 **5GB** 关联。工程师写大表在左、小表在右，期望 **广播**；实际计划却是 **Common Join + 巨大 shuffle**。调参后触发 **Map Join**，耗时从小时降到分钟。另一场景：两张大表 **等值且同桶**，启用 **SMB join** 避免 shuffle。本章梳理 **触发条件、hint、内存阈值** 与 **常见失效原因**。

补充：**Join 顺序与谓词位置** 都会影响是否触发 map join。典型反例：把 **过滤大表的谓词** 写在 `WHERE` 里却由于 **OR 条件** 或 **外连接语义** 无法尽早应用，导致优化器仍认为 **广播侧过大**。这类问题 `EXPLAIN` 往往「看起来合理」，需要结合 **实际中间结果大小**（第 19 章 + YARN counters）一起看。

---

## 2 项目设计（约 1200 字）


> **角色（对齐 [template.md](../template.md)）**：**小胖**（生活化比喻、抛问题）· **小白**（原理、边界、风险与备选）· **大师**（选型与「**技术映射**」承接）。  
> **对话结构**：小胖开球 1～2 轮 → 小白追问 2～3 轮 → 大师解答并引出下一子话题；全文循环 **2～3 次**，覆盖本章核心概念。

**小胖**：Map Join 是不是把小表塞进内存？

**小白**：典型实现把 **小表哈希表** 广播到 map 端，避免 reduce 侧 shuffle join。

**大师**：技术映射：**Map Join = 以内存换网络**。

**小胖**：shuffle join 啥时不可避免？

**小白**：两表都大、且无法分桶共置或 **非等值 join** 时，多半要走 **Common/Shuffle**。

**大师**：SMB 需要 **桶数/桶列一致 + 排序** 等前提，运维成本高。


> **§2·第三轮**（对齐 [template.md](../template.md) 的第三循环）

**小胖**：三张大表一起 shuffle，像三辆大巴同时挤一个匝道——有没有「让一辆走公交专用道」的办法？

**小白**：**Runtime filter / dynamic partition pruning** 这类手段，本质是先让 **小表一侧** 把谓词压缩成 **filter set**，减少大表 shuffle 量；前提是语义允许、版本支持。

**大师**：团队评审 join 时固定问三句：**谁广播、谁分区键对齐、谁兜底 sort-merge**；写进设计模板，避免「全默认 shuffle」。

**技术映射**：**Join 性能 = 数据分布 × 连接键基数 × 可用运行时裁剪**；广播只是其中一种「专用道」。


---

## 3 项目实战（约 1500～2000 字）

### 步骤 1：强制 hint（语法视版本）

```sql
SELECT /*+ MAPJOIN(b) */ a.id, b.name
FROM big_fact a
JOIN small_dim b ON a.dim_id = b.id;
```

### 步骤 2：调整阈值

```sql
SET hive.auto.convert.join=true;
SET hive.mapjoin.smalltablefilesize=...;
```

### 步骤 3：阅读计划

确认 `Map Join` / `Conditional` / `Shuffle Join` 出现位置。

**坑**：**小表其实不小**（统计错误）→ Driver OOM。  
**坑**：**不等值 join** 无法 map join。

**验证**：对 **广播失败** 场景做一次 **内存上限演练**。

### 步骤 4：Join Reorder 实验（理解 CBO）

在 **备份环境** 故意交换两表大小（复制维表膨胀到阈值之上/之下），观察 `EXPLAIN` 是否从 **Map Join** 退回 **Common Join**。记录 **阈值参数名与当前值** 写入团队 wiki。

### 步骤 5：SMB 前置检查清单（简版）

- [ ] 两侧 **bucket 列类型一致**  
- [ ] **bucket 数一致或可整除**（依实现）  
- [ ] **写入路径** 真正按 bucket 聚簇（非仅 DDL 声明）  
- [ ] 统计信息可用（第 17 章）
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

| Join 类型 | 优点 | 缺点 |
|-----------|------|------|
| Map | 省 shuffle | 内存风险 |
| Shuffle | 通用 | 网络/磁盘 IO 大 |
| SMB | 大表间省 shuffle | 前置条件苛刻 |

### 适用与不适用

- **Map**：维表显著小于阈值。  
- **SMB**：核心资产长期共置设计。  

### 注意事项

- **多表 join** 顺序与 hint 作用域。  
- **Skew** 与 join 组合（第 19 章）。  

### 常见生产踩坑

1. **动态分区裁剪失效** 导致「小表」变大。  
2. **ORC stripe** 与 split 造成 **估算偏差**。  
3. **Spark 与 Hive** join 策略不一致。

### 思考题

1. 半连接 `LEFT SEMI JOIN` 在优化上与 inner 有何不同？  
2. 何时用 **bucket map join** 而非普通 map join？  
3. 多表 join 中 **hint 作用域** 若只固定其中一对表，其余 join 仍可能乱序——你如何验证 hint 是否「泄漏」预期？

### 跨部门推广提示

- **建模**：维表 **行数/大小** 纳入数据字典。  
- **测试**：对 join 结果做 **checksum 抽样**。
