# 第 8 章：分桶：抽样与 Join 友好的表设计

> **专栏分档**：基础篇  
> **总纲索引**：[hive-column-outline.md](../hive-column-outline.md)（第五章 · 第 8 章对照表）  
> **业务主线**：电商平台「用户行为 + 交易」离线数仓（曝光、点击、下单、退款、风控特征）。

## 本章大纲备忘（写作前对照总纲）

| 项 | 内容 |
|----|------|
| 一句话摘要 | 分桶数选取、与分区的组合。 |
| 业务锚点 / 技术焦点 | 分析抽样、大表 Join 稳定性。 |
| 源码或文档锚点 | `ql` bucket 相关。 |

单章目标篇幅 **3000～5000 字**，四段结构对齐 [template.md](../template.md)。

---

## 1 项目背景（约 500 字）

风控需要 **10% 用户粒度** 的离线抽样训练集；广告算法要在曝光表与创意维表上做大 Join。团队发现：仅分区仍会在分区内 **shuffle 巨大**；希望通过 **CLUSTERED BY ... INTO N BUCKETS** 让相同 join key 落入同一文件桶，配合 **SMB Join**（中级篇）降低 shuffle。本章建立 **分桶数选择、与分区组合、tablesample** 的基础。

补充：**分桶不是银弹**。若 join key 与桶列不一致，或一侧表 **从未按桶写入**（文件布局名不副实），优化器仍可能退回 **shuffle join**。因此分桶策略要和 **ETL 写入路径** 一起设计，并在数据质量里加 **「桶内文件数 / 桶键基数」** 抽检。

---

## 2 项目设计（约 1200 字）


> **角色（对齐 [template.md](../template.md)）**：**小胖**（生活化比喻、抛问题）· **小白**（原理、边界、风险与备选）· **大师**（选型与「**技术映射**」承接）。  
> **对话结构**：小胖开球 1～2 轮 → 小白追问 2～3 轮 → 大师解答并引出下一子话题；全文循环 **2～3 次**，覆盖本章核心概念。

**小胖**：分桶像把一个大抽屉分成小格子？

**小白**：对同一列 `hash(key) % bucket_num` 决定行去哪个文件。Join 双方同桶列、同桶数时，优化器有机会 **桶对桶** 读本地。

**大师**：技术映射：**分桶 ≈ 数据共置（co-location） hint**。

**小胖**：桶数取多少？

**小白**：太小 → 单文件过大；太大 → 小文件多。经验起点：**与常用 reduce 并行度同量级** 或 **约为日均数据量/目标文件大小** 的函数，但需实测。

**大师**：桶列要选 **高基数、Join 常用等值键**（如 `user_id`），避免低基数导致 **严重倾斜**。

**技术映射**：桶列选择 ≈ **共置键 = join 键**。

**小胖**：分区和分桶冲突吗？

**小白**：常见模式 `PARTITIONED BY(dt) CLUSTERED BY(user_id) INTO 256 BUCKETS`：先按天切，再按用户哈希。

**小胖**：改桶数为啥要全表重写？

**小白**：桶数变了，**hash 模数** 变了，同一 `uid` 会去不同物理桶；不重写就会出现 **逻辑桶号与文件布局错位**。

**大师**：技术映射：**桶数是物理布局的一部分**，不是纯元数据开关。

---

## 3 项目实战（约 1500～2000 字）

### 步骤 1：建桶表

```sql
CREATE DATABASE IF NOT EXISTS demo_bucket;
USE demo_bucket;

CREATE TABLE IF NOT EXISTS user_click (
  uid BIGINT,
  event STRING
)
CLUSTERED BY (uid) INTO 8 BUCKETS
STORED AS ORC;
```

### 步骤 2：插入数据

```sql
INSERT OVERWRITE TABLE user_click
SELECT uid, event FROM some_source;
-- some_source 替换为实验用小表
```

### 步骤 3：抽样

```sql
SELECT * FROM user_click TABLESAMPLE (BUCKET 1 OUT OF 8 ON uid) LIMIT 100;
```

**预期**：返回约 1/8 桶内样本（行为与数据分布相关，教学以理解语义为主）。

**坑**：`INSERT` 未触发分桶排序要求时，文件布局可能不符合 SMB 前提 → 需 `INSERT` 路径支持 cluster 写出（与版本/引擎相关）。

### 步骤 4：阅读计划

对两表 join `EXPLAIN`，观察是否出现 **BucketMapJoin** 类算子（视统计与引擎）。

**验证**：文档记录 **桶数 8 改为 16** 对文件数与查询耗时的变化假设，留作中级篇回归。

### 步骤 5：构造 `some_source` 的最小可复现数据

```sql
CREATE TABLE some_source (uid BIGINT, event STRING);
INSERT INTO some_source VALUES
  (1001, 'pv'), (1002, 'pv'), (1003, 'click'),
  (1004, 'pv'), (1005, 'click'), (1006, 'pv');
```

再执行 `INSERT OVERWRITE TABLE user_click SELECT ...`，便于你在 **小数据** 上观察 **输出文件个数** 与 **桶 id 分布**（配合 `hdfs dfs -ls`）。

### 步骤 6：抽样与近似的业务注意

`TABLESAMPLE` 常用于 **探索**，不等价于 **统计上严格的 10% 用户抽样**（若用户多行事件，需按 `uid` 去重后再抽样，见窗口函数章）。在对外报表中披露抽样方法，避免被审计挑战。
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
| 支持高效抽样 | 改桶数通常需重写全表 |
| 为 SMB Join 铺路 | 低基数桶列易倾斜 |
| 控制文件粒度 | 维护与理解成本高 |
| 与 ORC stripe 协同好 | 错误桶列收益为零 |

### 适用与不适用

- **适用**：大表等值 join、可接受重写成本的核心表。  
- **不适用**：频繁改 schema 的探索表、小表。  

### 注意事项

- **桶列类型变更** 视同重建。  
- **与 Tez/Spark 桶对齐** 需对照团队标准。  

### 常见生产踩坑

1. 仅建表声明桶，但数据写入路径未 cluster → **名不副实**。  
2. 桶数与 HDFS block 不匹配导致 **小文件**。  
3. `TABLESAMPLE` 误用 `ON rand()` 失去桶语义。

### 思考题

1. 当 `uid` 长尾分布时，如何结合 **salt** 或 **倾斜优化**（第 19 章）？  
2. Iceberg/Hive 外表桶策略如何统一？（高级篇衔接。）  
3. 若 `INSERT` 后 `hdfs` 上文件数 **远大于桶数**，说明什么？应如何排查写入路径是否真正 cluster？

### 跨部门推广提示

- **测试**：对桶表 join 结果做 **与未桶化随机抽样对比** 的近似校验。  
- **架构**：桶表纳入 **核心资产清单**，变更走 MR。
