# 第 12 章：ORC／Parquet 与压缩：存储成本与读放大

> **专栏分档**：基础篇  
> **总纲索引**：[hive-column-outline.md](../hive-column-outline.md)（第五章 · 第 12 章对照表）  
> **业务主线**：电商平台「用户行为 + 交易」离线数仓（曝光、点击、下单、退款、风控特征）。

## 本章大纲备忘（写作前对照总纲）

| 项 | 内容 |
|----|------|
| 一句话摘要 | 列存格式与压缩算法权衡。 |
| 业务锚点 / 技术焦点 | 存储省但 CPU 高、小文件多。 |
| 源码或文档锚点 | `storage-api`、`serde`。 |

单章目标篇幅 **3000～5000 字**，四段结构对齐 [template.md](../template.md)。

---

## 1 项目背景（约 500 字）

曝光明细从 TEXTFILE+Gzip 迁到 **ORC+ZSTD** 后，存储下降 70%，但部分 CPU 密集聚合 **变慢**。另一张宽表用 **Parquet+SNAPPY**，与 Spark 下游共用良好，但 Hive 侧某些 UDF 读 **复杂嵌套** 性能不佳。团队需要一张 **选型表**：列存 stripe/row group、压缩 **速度与比**、与 **小文件数** 的联动，以及 **迁移检查清单**。

再补 **ORC vs Parquet 的「读路径」差异直觉**：ORC 在 Hive 生态里常与 **predicate pushdown + bloom filter（视写入）+ ACID** 组合更顺；Parquet 在 **Spark/Delta/ Iceberg** 链路里更常见。**嵌套结构**（深层 struct/list）在两种格式下都可能触发 **向量化降级**——若你的热点查询大量依赖嵌套字段，务必用 **真实宽表样本** 做 `EXPLAIN` + 压测，而不是只看存储压缩比。

---

## 2 项目设计（约 1200 字）


> **角色（对齐 [template.md](../template.md)）**：**小胖**（生活化比喻、抛问题）· **小白**（原理、边界、风险与备选）· **大师**（选型与「**技术映射**」承接）。  
> **对话结构**：小胖开球 1～2 轮 → 小白追问 2～3 轮 → 大师解答并引出下一子话题；全文循环 **2～3 次**，覆盖本章核心概念。

**小胖**：ORC 和 Parquet 不都是列存吗？

**小白**：都列存，但 **索引/stripe 默认、嵌套表达、生态默认** 不同。Hive 历史上 ORC 亲和度高；Spark 常见 Parquet。

**大师**：技术映射：**格式 = 存储布局 + 谓词下推能力 + 生态契约**。

**小胖**：压缩越高越好？

**小白**：压缩比 ↑ 往往 CPU ↑、延迟 ↑；热查询常用 **SNAPPY/LZ4**；冷归档用 **ZSTD/GZIP**。

**大师**：还要考虑 **split 大小** 与 **并行度**：过小的 stripe 导致 **读放大**。

**小胖**：小文件和格式啥关系？

**小白**：每个 `INSERT` 一片文件，列存也救不了 NN；需要 **合并、桶、或合并小文件作业**（中级篇）。

**小胖**：列存不是只读用到的列吗，为啥还 IO 大？

**小白**：列裁剪减少 **字节**，但仍要 **打开文件、读 footer、读 stripe index**；小文件多时 **固定开销** 主导。

**大师**：技术映射：**小文件税 = metadata ops + 调度碎片 + 列存 footer 放大**。


> **§2·第三轮**（对齐 [template.md](../template.md) 的第三循环）

**小胖**：列存像超市货架按品类摆——我为啥不能全用最高压缩？

**小白**：**压缩越高，CPU 解压成本越大**；还要考虑 **split 大小、谓词下推、Bloom filter** 是否仍有效。

**大师**：给每类表定 **默认 codec + 级别**，允许 **热数据低压缩、冷数据高压缩**；用 **同查询 A/B** 固化证据。

**技术映射**：**存储格式选择 = 扫描 IO × 解压 CPU × 元数据开销** 的乘积最小化，不是单一指标。


---

## 3 项目实战（约 1500～2000 字）

### 步骤 1：同 schema 建两张表（含显式库名，避免污染 default）

```sql
CREATE DATABASE IF NOT EXISTS demo_storage;
USE demo_storage;

DROP TABLE IF EXISTS t_orc;
DROP TABLE IF EXISTS t_parq;

CREATE TABLE t_orc (id BIGINT, s STRING) STORED AS ORC
TBLPROPERTIES ("orc.compress"="ZLIB");

CREATE TABLE t_parq (id BIGINT, s STRING) STORED AS PARQUET
TBLPROPERTIES ("parquet.compression"="SNAPPY");
```

> 属性名随版本可能变化，以官方文档为准。

### 步骤 2：灌入 **可复现** 的合成数据（建议从「已有小表笛卡尔积」生成）

教学环境不必造 10 万行；关键是 **两张表灌入同一批 `(id,s)`**。任选其一：

**方案 A（推荐）**：从你们已有的采样表复制：

```sql
INSERT INTO t_orc
SELECT id, channel AS s FROM some_sample_table LIMIT 50000;

INSERT INTO t_parq
SELECT id, s FROM t_orc;
```

**方案 B（玩具生成）**：用日历/数字表 `numbers` 自 join 扩行（先建 `numbers( n int )` 含 0..999，再 `CROSS JOIN` 自己得到 1e6 行——注意集群压力，先小后大）。

> 避免依赖版本相关的 `posexplode(split(repeat(...)))` 技巧；**两侧数据一致** 才是对比有效的前提。

### 步骤 2b：`ANALYZE TABLE ... COMPUTE STATISTICS`（若启用）

```sql
ANALYZE TABLE t_orc COMPUTE STATISTICS;
ANALYZE TABLE t_parq COMPUTE STATISTICS;
```

对比 `hdfs dfs -du -h` 目录大小，并记录 **压缩后字节 / 行数**。

### 步骤 3：对比查询延迟（控制变量）

```sql
SET hive.execution.engine=tez; -- 或你们默认引擎

SELECT s, COUNT(*) FROM t_orc WHERE id % 7 = 0 GROUP BY s;
SELECT s, COUNT(*) FROM t_parq WHERE id % 7 = 0 GROUP BY s;
```

记录 **Wall time、CPU time、扫描数据量**（若可观测）；各跑 **3 次取中位数**，避免冷启动误导。

**坑**：**ORC 与 Parquet 混用** 在同一分层无规范 → 下游引擎缓存/向量化路径切换频繁。  
**坑**：**极高基数 string** 在列存中仍占空间 → 考虑字典或归一化。

**验证**：输出 **本团队默认**：ODS/DWD/DWS 各层格式与压缩白名单。

### 步骤 4：迁移检查清单（迁移生产表前勾选）

- [ ] **行数 / sum checksum / distinct key 抽样** 与基线一致  
- [ ] **分区字段** 与 `serde` 参数未改变  
- [ ] **下游 Spark** 读取路径回归（尤其 `timestamp`/`decimal`）  
- [ ] **回滚脚本**（保留旧表 `rename` 或快照）  
- [ ] **小文件合并** 作业窗口已预留
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
| 列存显著降存储与 IO | 小文件仍致命 |
| 谓词下推减少扫描列 | 极高嵌套类型性能因引擎而异 |
| 压缩可配适应冷热 | 迁移需全量重写成本 |
| 与向量化执行协同好 | 错误 stripe 配置难排查 |

### 适用与不适用

- **适用**：大宽表扫描聚合为主。  
- **不适用**：极短生命周期 tiny 表（格式开销不划算）。  

### 注意事项

- **schema 演进**：新增列默认值行为。  
- **跨引擎**：Spark/Hive 对 **timestamp** 语义差异。  

### 常见生产踩坑

1. TEXT 直转 ORC 未调 **stripe size** 导致并行度差。  
2. **不可切分压缩** 导致单 map 超大。  
3. Parquet **字典溢出** 退化性能。

### 思考题

1. 如何量化 **存储节省 vs CPU 增加** 以决定是否 ZSTD？  
2. 宽表 **按列族拆表** 与单 ORC 宽表如何权衡？  
3. 若 ORC 文件 **stripe 过大** 导致 map 并行度不足，你有哪些调参或重写手段？（提示：stripe 与 `orc.stripe.size` 类参数、合并策略。）

### 跨部门推广提示

- **FinOps**：把格式/压缩策略纳入 **成本看板**。  
- **测试**：迁移后 **行数、checksum、抽样 diff** 三件套。
