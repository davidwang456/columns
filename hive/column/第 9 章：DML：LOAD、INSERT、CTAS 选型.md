# 第 9 章：DML：LOAD、INSERT、CTAS 选型

> **专栏分档**：基础篇  
> **总纲索引**：[hive-column-outline.md](../hive-column-outline.md)（第五章 · 第 9 章对照表）  
> **业务主线**：电商平台「用户行为 + 交易」离线数仓（曝光、点击、下单、退款、风控特征）。

## 本章大纲备忘（写作前对照总纲）

| 项 | 内容 |
|----|------|
| 一句话摘要 | 各写入语义与幂等策略；重复导数治理。 |
| 业务锚点 / 技术焦点 | 重复导入、覆盖写错分区。 |
| 源码或文档锚点 | [HiveSQL之DML语句.md](../HiveSQL之DML语句.md)。 |

单章目标篇幅 **3000～5000 字**，四段结构对齐 [template.md](../template.md)。

---

## 1 项目背景（约 500 字）

ODS 接入广告曝光：上游每日在 HDFS 产出 `dt=...` 目录，数据组用 **LOAD DATA** 快速挂表；DWD 用 **INSERT OVERWRITE** 做清洗；探索分析常用 **CTAS** 快速落临时宽表。事故场景：同一脚本被调度器 **重复触发**，`INSERT OVERWRITE` 把已校验分区再次覆盖为半成品；另一团队用 **LOAD** 把本地 `file://` 路径误指到生产 Namenode。需要统一 **写入语义、幂等键、回滚策略**。

再补 **幂等的三层模型**（建议写进调度规范）：**调度层**（Airflow `job_id` / `dag_run_id` 去重）、**数据层**（目标分区 **水位线文件** 或 **行级主键 + 去重表**）、**查询层**（报表只读 **已发布标签** 的 DWS 分区）。Hive 的 `INSERT` 语句本身只解决数据层的一部分；若只有 `OVERWRITE` 没有水位线，重复跑仍可能 **覆盖为半成品**。

---

## 2 项目设计（约 1200 字）


> **角色（对齐 [template.md](../template.md)）**：**小胖**（生活化比喻、抛问题）· **小白**（原理、边界、风险与备选）· **大师**（选型与「**技术映射**」承接）。  
> **对话结构**：小胖开球 1～2 轮 → 小白追问 2～3 轮 → 大师解答并引出下一子话题；全文循环 **2～3 次**，覆盖本章核心概念。

**小胖**：LOAD 和 INSERT 不就是往里塞数据吗？

**小白**：**LOAD** 更偏「把已有文件挪/拷到表目录」（语义随 LOCAL/INPATH 与内外表变化）；**INSERT** 走 **查询计划**，可跨表转换格式。

**大师**：技术映射：**LOAD ≈ 文件级搬运**；**INSERT ≈ SQL 级变换写入**。

**小胖**：那幂等怎么做？

**小白**：调度层用 **分区水位线**（如成功文件 `_SUCCESS`）、或 **INSERT 前校验行数阈值**；或用 **ACID/事务表**（中级篇）做增量。

**大师**：`INSERT OVERWRITE` 是 **大锤**，适合 T+1 全量重算分区；`INSERT INTO` 是 **累加**，要防重复行。

**技术映射**：Overwrite vs Into ≈ **可重复跑批 vs 追加语义**。

**小胖**：CTAS 呢？

**小白**：`CREATE TABLE ... AS SELECT` 一步建表+写入，探索很方便，但 **不能分区模板化** 能力受限（视版本），且易在 default 库堆积「临时资产」。

**小胖**：`INSERT INTO` 和 **Spark append** 混写一张表会怎样？

**小白**：可能出现 **文件布局不一致、小文件、锁**（第 25 章）与 **ACID 语义**（若事务表）等组合问题。原则是：**同一分区同一窗口内只许一个「写入 owner」**。

**大师**：技术映射：**写入并发 = 文件系统事实 + 元数据事实 + 引擎锁** 的三元耦合。

---

## 3 项目实战（约 1500～2000 字）

### 步骤 1：LOAD 外表路径（示意）

```sql
-- 假设已存在外表指向 HDFS 某目录（第 5 章）
LOAD DATA INPATH 'hdfs:///tmp/stage/impression/dt=2026-04-20/*' INTO TABLE ods_impression_ext;
```

**注意**：生产慎用 `LOAD`，先确认 **是否移动文件**、目标分区。

### 步骤 2：INSERT OVERWRITE 分区

```sql
INSERT OVERWRITE TABLE dwd_impression PARTITION (dt='2026-04-20')
SELECT id, channel, creative
FROM   ods_impression_ext
WHERE  dt = '2026-04-20' AND id IS NOT NULL;
```

### 步骤 3：CTAS 探索

```sql
CREATE TABLE tmp_explore AS
SELECT channel, COUNT(*) cnt
FROM dwd_impression
WHERE dt = '2026-04-20'
GROUP BY channel;
```

### 步骤 4：幂等检查清单（文档）

1. 调度 DAG 是否 **去重触发**？  
2. `OVERWRITE` 前是否校验 **上游行数 / 文件大小**？  
3. 失败重跑是否 **覆盖半成品**？——考虑 **staging 分区 → promote**。

**坑**：`INSERT INTO` 重复跑导致 **重复键**。  
**坑**：动态分区未限制 **最大分区数**（第 11 章）。

**验证**：同一 `INSERT OVERWRITE` 连续执行两次，下游 `COUNT DISTINCT` 核心指标不变。

### 步骤 5：「staging → promote」伪代码流程（文字版）

1. `INSERT OVERWRITE ... PARTITION (dt, stage='staging')` 写入 **暂存子分区** 或 **独立 staging 表**。  
2. 跑 **行数、主键重复率、NULL 率** 校验 SQL；失败则 `ALTER`/`DROP` staging，不动线上。  
3. 成功则 **第二次** `INSERT OVERWRITE` 到正式 `stage='prod'` 或 **swap 分区指针**（视实现：同表不同 location  rarely，常用两表 rename 策略）。  
4. 在工单记录 **输入文件清单哈希** 便于审计。
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
| OVERWRITE 简化 T+1 重算 | 误分区覆盖不可恢复风险高 |
| INSERT 与 SQL 生态统一 | 大作业耗资源、需队列治理 |
| LOAD 快速接入已有文件 | 语义依赖内外表与路径，易误用 |
| CTAS 提升探索效率 | 临时表泛滥、元数据污染 |

### 适用与不适用

- **OVERWRITE**：日级全量重算分区。  
- **INTO**：明确追加且上游去重可靠。  
- **不适用**：需要细粒度 upsert 且无二义键时（考虑 MERGE/ACID）。

### 注意事项

- **权限**：LOAD 涉及 HDFS 移动权限。  
- **压缩**：INSERT 写出格式由表 `STORED AS` 决定。  

### 常见生产踩坑

1. 并行作业双写同一分区 **竞态**。  
2. `CTAS` 未指定 LOCATION 导致 **默认仓路径权限** 问题。  
3. 小文件爆炸（每个 INSERT 一片文件）。

### 思考题

1. 如何用 **staging + rename** 模式实现近似原子发布？  
2. `INSERT` 与 Spark 写 Hive 表并发时如何协调 **锁**（第 25 章）？  
3. 若 `LOAD DATA INPATH` **移动**了上游仍在写入的目录，会出现什么现象？如何从事前流程上禁止？

### 跨部门推广提示

- **测试**：为关键分区设计 **行数上下界** 断言。  
- **运维**：监控 **同一分区短时间多次 OVERWRITE** 告警。
