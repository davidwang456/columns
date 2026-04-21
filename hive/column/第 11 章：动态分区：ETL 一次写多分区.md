# 第 11 章：动态分区：ETL 一次写多分区

> **专栏分档**：基础篇  
> **总纲索引**：[hive-column-outline.md](../hive-column-outline.md)（第五章 · 第 11 章对照表）  
> **业务主线**：电商平台「用户行为 + 交易」离线数仓（曝光、点击、下单、退款、风控特征）。

## 本章大纲备忘（写作前对照总纲）

| 项 | 内容 |
|----|------|
| 一句话摘要 | 动态分区参数与上限；防「分区爆炸」。 |
| 业务锚点 / 技术焦点 | 一次跑批写入上千分区。 |
| 源码或文档锚点 | `hive-site` 动态分区项；[Hive常用配置-应用相关.md](../Hive常用配置-应用相关.md)。 |

单章目标篇幅 **3000～5000 字**，四段结构对齐 [template.md](../template.md)。

---

## 1 项目背景（约 500 字）

ODS 清洗作业要把 **多国家、多渠道** 的明细一次从 staging 写入 `PARTITIONED BY(dt,country,channel)`。若用静态分区 SQL，需要 **成百个 INSERT**；团队启用 **动态分区**，结果某次脏数据在 `country` 字段出现 **异常枚举值**，一夜创建 **8000+ 新分区**，Metastore 膨胀、NameNode 压力飙升。本章讲清 **动态分区开关、strict/nonstrict、最大分区数、与严格模式** 的组合治理。

补充：**动态分区与「维度缓慢变化」不要混谈**。若 `country` 会因上游映射表调整而改名，动态分区会 **制造历史分区名分裂**（`UK` vs `United Kingdom`），后期治理成本高于一次性 **规范化维表** 的成本。

---

## 2 项目设计（约 1200 字）


> **角色（对齐 [template.md](../template.md)）**：**小胖**（生活化比喻、抛问题）· **小白**（原理、边界、风险与备选）· **大师**（选型与「**技术映射**」承接）。  
> **对话结构**：小胖开球 1～2 轮 → 小白追问 2～3 轮 → 大师解答并引出下一子话题；全文循环 **2～3 次**，覆盖本章核心概念。

**小胖**：动态分区是不是「让 Hive 自己猜分区名」？

**小白**：是根据 **SELECT 最后几列** 与 `PARTITION(...)` 中 **动态列** 对应写入；不是猜，是 **数据驱动**。

**大师**：技术映射：**动态分区 = 把维度值映射到子目录名**。

**小胖**：strict 和 nonstrict 啥区别？

**小白**：strict 通常要求 **至少一个静态分区**（常见最粗粒度日期），防止全动态导致 **误写全表所有组合**；nonstrict 放开限制，风险更高。

**大师**：生产常见：**最左静态 dt，其余动态**。

**技术映射**：strict 模式 ≈ **安全护栏**。

**小胖**：最大分区数限制会不会把合法作业杀掉？

**小白**：会，但这是 **有意熔断**。真正要做的是上游 **维度白名单** + staging 校验。

**小胖**：Spark 动态写入 Hive 分区也要调 Hive 这些参数吗？

**小白**：路径取决于 **写入链路**：若是 **Spark 写 Hive 表格式** 仍可能受 Hive 元数据/锁影响；很多团队把 **「动态分区爆炸」** 的治理前移到 **Spark `repartition(col)` + 维度过滤`**。

**大师**：技术映射：**参数是最后一道闸；数据质量是第一道闸**。


> **§2·第三轮**（对齐 [template.md](../template.md) 的第三循环）

**小胖**：动态分区一次写几百个目录——像一次给整栋楼每户门口贴条，贴错一户怎么办？

**小白**：**strict 模式、非严格模式、分区列空值** 都会改变行为；还要防 **误造海量空分区** 把 Metastore 撑爆。

**大师**：ETL 任务里把 **最大动态分区数**、**输入行数预估**、**失败重跑幂等** 写进 checklist；大促前 **压测分区爆炸** 场景。

**技术映射**：**动态分区写入 = 元数据放大器**；治理抓手是 **上限 + 抽样校验 + 回滚分区**。


---

## 3 项目实战（约 1500～2000 字）

### 步骤 1：会话参数（示例名，以版本文档为准）

```sql
SET hive.exec.dynamic.partition = true;
SET hive.exec.dynamic.partition.mode = nonstrict; -- 实验环境；生产倾向 strict
SET hive.exec.max.dynamic.partitions=1000;
SET hive.exec.max.dynamic.partitions.pernode=100;
```

### 步骤 2：动态写入

```sql
CREATE TABLE IF NOT EXISTS dwd_sales (
  order_id BIGINT,
  amount    DECIMAL(10,2)
)
PARTITIONED BY (dt STRING, country STRING)
STORED AS PARQUET;

INSERT OVERWRITE TABLE dwd_sales PARTITION (dt, country)
SELECT order_id, amount, dt, country
FROM   staging_sales
WHERE  dt = '2026-04-20';
```

### 步骤 3：失败注入（实验）

在 staging 人为插入非法 `country='__bad__'`，观察是否触发 **分区数限制** 或 **作业失败**。

**坑**：`INSERT` 列顺序与分区列 **未对齐** → 数据进错分区。  
**坑**：动态分区 + **笛卡尔积** 中间结果 → 组合爆炸。

**验证**：作业日志出现 `Number of dynamic partitions` 相关计数，与 Metastore `SHOW PARTITIONS` 增量一致且在阈值内。

### 步骤 4：维度白名单 SQL 模板（插入动态写入之前）

```sql
-- 假设 dim_country 为权威维表
SELECT s.*
FROM staging_sales s
WHERE EXISTS (SELECT 1 FROM dim_country d WHERE d.code = s.country);
```

将 **过滤后的 staging** 作为动态分区 `INSERT` 的输入，可显著降低 **异常 country**。

### 步骤 5：监控指标建议

- 单次作业 **新建分区数**（从 Metastore audit 或 HMS MySQL 统计）  
- **`hive.exec.max.dynamic.partitions` 触发次数**（应接近 0）
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
| 极大减少 SQL 模板重复 | 配置错误可瞬间制造海量分区 |
| 与 Spark 动态写入习惯接近 | 排障需结合数据质量规则 |
| 适合多维度切片批处理 | 对元数据服务压力大 |

### 适用与不适用

- **适用**：有界维度集合（国家、渠道=几十个）。  
- **不适用**：高基数自由文本直接作分区键。  

### 注意事项

- **白名单**：维度表 left semi join 过滤。  
- **监控**：分区增量报警。  

### 常见生产踩坑

1. `nonstrict` 打开 + 脏键 → **分区爆炸**。  
2. 动态列顺序调整未更新注释 → **静默错位**。  
3. 小文件 per 分区（配合合并策略）。

### 思考题

1. 如何用 **staging 单分区聚合 + 二次静态写入** 降低动态分区风险？  
2. 与 Iceberg hidden partition 相比，Hive 原生动态分区运维差异？（第 32 章）  
3. 若 `channel` 字段来自 **用户可控埋点**，动态分区是否应绝对禁止？替代方案是什么？

### 跨部门推广提示

- **数据质量**：把 **分区键合法性** 纳入 Great Expectations / Deequ 类校验。  
- **运维**：Metastore **分区总数** 基线与告警。
