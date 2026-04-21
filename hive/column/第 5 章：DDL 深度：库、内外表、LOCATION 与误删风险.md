# 第 5 章：DDL 深度：库、内外表、LOCATION 与误删风险

> **专栏分档**：基础篇  
> **总纲索引**：[hive-column-outline.md](../hive-column-outline.md)（第五章 · 第 5 章对照表）  
> **业务主线**：电商平台「用户行为 + 交易」离线数仓（曝光、点击、下单、退款、风控特征）。

## 本章大纲备忘（写作前对照总纲）

| 项 | 内容 |
|----|------|
| 一句话摘要 | 内外表与 LOCATION 语义；生产误删防护。 |
| 业务锚点 / 技术焦点 | 误删外表目录导致数据不可恢复。 |
| 源码或文档锚点 | `ql`、`serde`；[HiveSQL之DDL语句.md](../HiveSQL之DDL语句.md)。 |

单章目标篇幅 **3000～5000 字**，四段结构对齐 [template.md](../template.md)。

---

## 1 项目背景（约 500 字）

ODS 组为接入广告曝光日志，数据工程在 HDFS 上已有按天落地的 ORC 目录。开发 A 用 **外表** 指过去，方便快速探查；开发 B 误以为 `DROP TABLE` 会清理测试数据，在生产同名库执行了脚本。结果：**元数据没了，HDFS 上数 TB 仍在计费**，下游任务因表不存在全部失败；恢复需要重建表定义并 **精确对齐 SerDe/列顺序/路径**。

团队复盘要求：**内外表选型写进评审清单**；`LOCATION` 变更双人复核；关键目录开 **回收站 / 快照**。本章系统梳理 **CREATE DATABASE/TABLE、内外表、LOCATION、MSCK** 的语义与风险边界。

再强调 **`IF NOT EXISTS` 的双刃剑**：它能防止脚本重复执行报错，也会让你在 **生产误跑脚本** 时「静默成功」——表没建出来却以为建好了。团队实践里常把 **库名前缀**（如 `dev_`/`prod_`）与 **CI 变量注入** 绑定，避免人肉拼字符串。

---

## 2 项目设计（约 1200 字）


> **角色（对齐 [template.md](../template.md)）**：**小胖**（生活化比喻、抛问题）· **小白**（原理、边界、风险与备选）· **大师**（选型与「**技术映射**」承接）。  
> **对话结构**：小胖开球 1～2 轮 → 小白追问 2～3 轮 → 大师解答并引出下一子话题；全文循环 **2～3 次**，覆盖本章核心概念。

**小胖**：内表外表不都是表吗？为啥删起来不一样？

**小白**：内表由 Hive **托管**存储路径（概念上），`DROP` 通常意味着「我不认这张表了，目录也不要了」（仍受版本与 trash 配置影响）。外表强调 **Hive 不拥有数据**，`DROP` 更像「把书签撕了」。

**大师**：技术映射：**MANAGED ≈ 生命周期绑定**；**EXTERNAL ≈ 绑定外部系统的主数据**。

**小胖**：那为啥还有人爱用外表？

**小白**：数据由 **Spark/Flink 先写入**，Hive 只读分析；或数据要 **长期保留** 不受 Hive 误删影响。代价是 **治理责任** 在数据所有者，不在 Hive。

**大师**：协作规则要明确：**谁对 LOCATION 下的文件正确性负责**。ODS 常外表，DWS 可内表，但不是铁律。

**技术映射**：分层策略 ≈ **所有权与删除权分离**。

**小胖**：`LOCATION` 写错会怎样？

**小白**：可能指到空目录→查无数据；指到他人目录→**越权读取**；指到生产桶→测试作业污染生产。

**大师**：所以 `LOCATION` 要配合 **IAM/HDFS ACL** 与 **命名空间规范**，DDL 也要 Code Review。

**小胖**：分区表 `ALTER TABLE ADD PARTITION` 和 `MSCK` 啥区别？

**小白**：`ADD` 是你 **显式告诉** Metastore 多了一条分区映射；`MSCK REPAIR` 是 **扫 HDFS 目录** 试图自动补登记。后者省事但有 **扫错路径/扫出垃圾分区** 的风险。

**大师**：技术映射：**显式 DDL = 强契约；MSCK = 便利与风险对价**。

---

## 3 项目实战（约 1500～2000 字）

### 环境准备

- 测试库 `demo_ddl`；HDFS 上可写子目录权限。  
- Beeline 已连通（第 4 章）。

### 步骤 1：建库与默认路径（目标：理解 `MANAGEDLOCATION`）

```sql
CREATE DATABASE IF NOT EXISTS demo_ddl
COMMENT 'ddl lab'
LOCATION 'hdfs:///tmp/warehouse_demo_ddl.db';

USE demo_ddl;
```

`SHOW CREATE DATABASE demo_ddl;` 观察输出。

### 步骤 2：内表与外表最小对照（目标：看 SHOW CREATE TABLE）

```sql
CREATE TABLE managed_click (id BIGINT, channel STRING)
STORED AS PARQUET;

CREATE EXTERNAL TABLE external_click (id BIGINT, channel STRING)
STORED AS PARQUET
LOCATION 'hdfs:///tmp/external_click_lab';

SHOW CREATE TABLE managed_click;
SHOW CREATE TABLE external_click;
```

### 步骤 3：安全删除演练（目标：流程而非真删生产）

在 **实验目录** 执行：

```sql
DROP TABLE managed_click;
-- 检查 HDFS 上 managed 表对应目录是否删除（视 trash）

DROP TABLE external_click;
hdfs dfs -ls /tmp/external_click_lab
```

**预期**：外表路径文件仍在。

**坑**：`DROP TABLE` 前未 `SHOW CREATE TABLE` 留档 → 恢复困难。  
**坑**：同名表在不同库，脚本未 `USE` → 删错库。

### 步骤 4：ALTER 边界

```sql
CREATE TABLE alter_demo (a INT, b STRING) STORED AS PARQUET;
ALTER TABLE alter_demo CHANGE COLUMN b b STRING COMMENT 'channel id';
```

理解 **部分类型变更受限**（与 Parquet/ORC 演进相关，见存储章）。

**验证**：输出一份 **《内外表选型检查表》**（5 行以内）贴到团队 Wiki。

### 《内外表选型检查表》参考模板

| 问题 | 选内表倾向 | 选外表倾向 |
|------|------------|------------|
| 数据由谁主写？ | Hive 作业为主 | Spark/Flink/第三方系统为主 |
| DROP 是否要删文件？ | 是 | 否 |
| 是否需要跨团队共享原始路径？ | 否 | 是 |
| 是否有合规「不可删仅可脱敏」？ | 谨慎 | 更常见 |
| 是否允许 LOCATION 指生产桶？ | 默认否 | 仅审批后 |

### 步骤 5（可选）：`MSCK REPAIR` 沙箱演练

在 **独立测试目录** 手工 `hdfs dfs -mkdir` 增加 `dt=2099-01-01`，再 `MSCK REPAIR TABLE ...`，观察 `SHOW PARTITIONS` 是否出现 **幽灵分区**——用于培训「为何生产要禁自动 repair 或要配路径白名单」。

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
| 外表保护上游写入数据不被 Hive 误删 | 元数据丢失后「孤儿文件」难治理 |
| 内表简化生命周期 | 误 `DROP` 代价大 |
| LOCATION 灵活挂载已有数据集 | 路径错误难以及时发现 |
| DDL 可版本化纳入 Git | 大表 ALTER 可能触发重写（引擎相关） |

### 适用与不适用

- **外表适用**：上游系统主写入、合规长期留存。  
- **内表适用**：Hive 托管的中间层、可再生的聚合表。  
- **不适用**：用外表指向频繁覆写的临时目录却当 ODS 真相源（漂移风险）。

### 注意事项

- **回收站**：确认集群 HDFS trash 与 Hive 配置。  
- **权限**：删表权与写路径权应分离。  

### 常见生产踩坑

1. **脚本循环 DROP+CREATE** 误伤生产库名变量。  
2. **外表 LOCATION 指到根目录** 导致权限与数据混放。  
3. **字符集/注释乱码** 导致下游解析工具失败。

### 思考题

1. 若将内表改为外表并 `SET LOCATION` 到同一路径，历史分区元数据如何保持一致？  
2. `TRUNCATE TABLE` 对内表与外表行为差异是什么？（查阅当前版本文档。）  
3. 设计一个 **DDL Code Review checklist**：至少包含库名、LOCATION、内外表、分区键、SerDe、是否回灌生产 6 项。

### 跨部门推广提示

- **测试**：对 DDL 脚本做 **库名白名单** 校验。  
- **运维**：定期 **孤儿文件扫描** 报表。  
- **开发**：DDL 变更走 MR + 双人复核。
