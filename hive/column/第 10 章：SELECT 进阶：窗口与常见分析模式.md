# 第 10 章：SELECT 进阶：窗口与常见分析模式

> **专栏分档**：基础篇  
> **总纲索引**：[hive-column-outline.md](../hive-column-outline.md)（第五章 · 第 10 章对照表）  
> **业务主线**：电商平台「用户行为 + 交易」离线数仓（曝光、点击、下单、退款、风控特征）。

## 本章大纲备忘（写作前对照总纲）

| 项 | 内容 |
|----|------|
| 一句话摘要 | 排名、累计、留存类窗口函数。 |
| 业务锚点 / 技术焦点 | 运营要看「7 日留存」等复杂窗口。 |
| 源码或文档锚点 | `ql`。 |

单章目标篇幅 **3000～5000 字**，四段结构对齐 [template.md](../template.md)。

---

## 1 项目背景（约 500 字）

增长团队要 **7 日留存**：用户在第 0 日活跃，是否在随后 7 日内再次活跃。用多段自连接不仅 SQL 难写，且在 Hive 上 **shuffle 成本极高**。改用 **窗口函数**（`ROW_NUMBER`、`LEAD/LAG`、`SUM() OVER`、`COUNT(DISTINCT) OVER` 限制注意）可以把逻辑写清楚。本章用 **简化事件表** 演示 **分区+排序窗口** 与 **常见坑（重复计数、边界 NULL）**。

补充：**留存不是窗口函数的专利**。工程上常拆成 **「活跃日表」→「自连接/范围 join」→「聚合」** 三段，窗口函数只是让 SQL **更短**；当数据量极大时，**bitmap / minhash / 增量状态表** 可能更省资源——Hive 仍可作为 **口径层** 调度这些逻辑（甚至 UDAF）。本章先掌握窗口语义，避免一上来就写 **几十行自连接**。

---

## 2 项目设计（约 1200 字）


> **角色（对齐 [template.md](../template.md)）**：**小胖**（生活化比喻、抛问题）· **小白**（原理、边界、风险与备选）· **大师**（选型与「**技术映射**」承接）。  
> **对话结构**：小胖开球 1～2 轮 → 小白追问 2～3 轮 → 大师解答并引出下一子话题；全文循环 **2～3 次**，覆盖本章核心概念。

**小胖**：窗口函数和 `GROUP BY` 啥区别？

**小白**：`GROUP BY` **折叠行**；窗口函数 **保留明细行**，在每行上附加聚合结果。

**大师**：技术映射：**窗口 = 保留粒度的向量批计算**。

**小胖**：`PARTITION BY` 和表分区同名好晕。

**小白**：窗口里的 `PARTITION BY uid` 是 **逻辑分组**，与 HDFS 分区无关，只是不幸撞名。

**大师**：读 SQL 时先在脑子里把 **表分区裁剪** 与 **窗口分区** 分开。

**小胖**：留存为啥常用 `LEAD`？

**小白**：把「下一次活跃日期」拉到当前行，再日期差分判断是否在 7 天内；也可用 **首次后续事件时间** 子查询，窗口往往更短。

**小胖**：`ROWS BETWEEN` 和默认帧有啥坑？

**小白**：默认帧有时是 `RANGE`（与 **peer rows** 有关），聚合结果可能 **和你直觉的「滑动三行」不一致**；写清 `ROWS BETWEEN 2 PRECEDING AND CURRENT ROW` 更可控。

**大师**：技术映射：**窗口帧 = 在有序序列上再切一条子序列**；忘了指定帧，等于把解释权交给优化器默认值。


> **§2·第三轮**（对齐 [template.md](../template.md) 的第三循环）

**小胖**：窗口函数像「每个班发排名条」——会不会发着发着把纸发没了（内存爆）？

**小白**：会：**无界窗口、大 PARTITION BY 基数、嵌套窗口** 都可能导致 **state 膨胀**；要约束 **分区裁剪 + 预聚合**。

**大师**：评审窗口 SQL 时固定问：**能否改成子查询先聚合再窗口**、**frame 是否可缩**；把 **TopN 与累计** 拆成两条可测 SQL。

**技术映射**：**窗口 = 有序流上的状态算子**；成本 ∝ **分区键基数 × frame 宽度 × 列数**。


---

## 3 项目实战（约 1500～2000 字）

假设 `dwd_user_active(uid BIGINT, active_dt STRING)` 已按日去重。

### 步骤 1：每人按时间排序

```sql
SELECT uid, active_dt,
       LAG(active_dt) OVER (PARTITION BY uid ORDER BY active_dt) AS prev_dt
FROM dwd_user_active;
```

### 步骤 2：定义「会话起点」——间隔 >1 天则新会话（示意）

```sql
WITH ordered AS (
  SELECT uid, active_dt,
         CASE WHEN LAG(active_dt) OVER (PARTITION BY uid ORDER BY active_dt) IS NULL
                   OR datediff(to_date(active_dt), to_date(LAG(active_dt) OVER (PARTITION BY uid ORDER BY active_dt))) > 1
              THEN 1 ELSE 0 END AS is_new_session
  FROM dwd_user_active
)
SELECT * FROM ordered;
```

> 日期函数以集群 Hive 版本为准，可用 `date_sub` 等替代。

### 步骤 3：7 日留存粗算思路

对 cohort 日 `c.dt`，统计 `exists active_dt in (c.dt, c.dt+7]`：可用 **范围 join** 或 **位图/数组聚合**（进阶），窗口版留作思考题。

### 步骤 4：`ROW_NUMBER` 去重

```sql
SELECT uid, active_dt
FROM (
  SELECT uid, active_dt,
         ROW_NUMBER() OVER (PARTITION BY uid, active_dt ORDER BY active_dt) rn
  FROM raw_active
) t WHERE rn = 1;
```

**坑**：`ORDER BY` 不稳定导致 `ROW_NUMBER` 随机 → 加 **确定性 tie-breaker 列**。  
**坑**：`COUNT(DISTINCT x) OVER (...)` 在部分版本/引擎 **不支持或极慢**。

**验证**：对同一 uid 手工算 1 条样本，与 SQL 结果对照。

### 步骤 5：`DENSE_RANK` 与运营「并列排名」

```sql
SELECT uid, active_dt,
       DENSE_RANK() OVER (PARTITION BY uid ORDER BY active_dt) AS nth_day_seq
FROM dwd_user_active;
```

用于区分 **连续活跃天数序列** 与 **日历日序列**（后者需补全未出现日期，通常用维度表 **左连接** 或 **日历 spine**）。

### 步骤 6：窗口与 **表分区裁剪** 的组合写法（推荐模板）

```sql
WITH base AS (
  SELECT *
  FROM dwd_user_active
  WHERE active_dt BETWEEN '2026-04-01' AND '2026-04-30'  -- 若表另有 dt 分区列，可叠加 AND dt='...' 做分区裁剪
)
SELECT ...窗口表达式... FROM base;
```

**原则**：先 **缩小 base**，再开窗，避免优化器虽裁剪但仍在大中间结果上排序。
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
| SQL 表达力接近业务语言 | 复杂窗口计划难读 |
| 减少自连接 shuffle | 误用 DISTINCT 窗口代价高 |
| 支持排名/累计等模式 | 需要理解帧默认值 `RANGE/ROWS` |

### 适用与不适用

- **适用**：留存、漏斗、排名、滑动累计。  
- **不适用**：极深嵌套窗口导致优化器失控时，考虑 **分层物化**（中级篇）。  

### 注意事项

- **NULL 排序位置** 影响 `FIRST_VALUE`。  
- **数据倾斜** 于 `PARTITION BY` 键时窗口也会拖垮（第 19 章）。  

### 常见生产踩坑

1. 窗口 `ORDER BY` 常量导致全排序无意义。  
2. 把 **表分区字段** 忘在 `WHERE`，先裁剪再开窗。  
3. `ROWS BETWEEN` 未写全导致 **默认帧** 与预期不符。

### 思考题

1. 用窗口函数实现 **首次下单后 7 日内复购率**，如何定义帧避免跨用户污染？  
2. 当 `COUNT(DISTINCT)` 不可用作窗口时，你的替代方案？  
3. 若 `active_dt` 存在 **同一日多条事件**，留存定义应先去重再开窗，还是用 `ROW_NUMBER` 选代表行？各对口径有何影响？

### 跨部门推广提示

- **测试**：给 **窗口结果** 提供小样本手工表。  
- **产品**：口径文档写清 **活跃定义** 与 **自然日/业务日**。
