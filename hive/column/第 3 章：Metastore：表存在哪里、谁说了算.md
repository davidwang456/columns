# 第 3 章：Metastore：表存在哪里、谁说了算

> **专栏分档**：基础篇  
> **总纲索引**：[hive-column-outline.md](../hive-column-outline.md)（第五章 · 第 3 章对照表）  
> **业务主线**：电商平台「用户行为 + 交易」离线数仓（曝光、点击、下单、退款、风控特征）。

## 本章大纲备忘（写作前对照总纲）

| 项 | 内容 |
|----|------|
| 一句话摘要 | 区分元数据与 HDFS 数据路径；理解嵌入式 vs 远程 Metastore。 |
| 业务锚点 / 技术焦点 | 删表后数据还在、多人协作命名。 |
| 源码或文档锚点 | 详见 [hive-column-outline.md](../hive-column-outline.md) 第五章；源码 `source/hive/metastore`、`standalone-metastore`；笔记 [hive元数据库/](../hive元数据库/)。 |

单章目标篇幅 **3000～5000 字**，四段结构对齐 [template.md](../template.md)。

---

## 1 项目背景（约 500 字）

数仓团队遇到两起「灵异事件」：一是分析师执行 `DROP TABLE` 后，磁盘占用几乎没降，运维说 HDFS 上目录还在；二是两名开发在不同会话里建了同名表，互相覆盖报错，最后发现连的不是同一个 **Metastore 实例**。业务方质问：**表到底存在哪里？删表删的是什么？**

Hive 的答案是：**表 = 元数据（Metastore 里的记录） + 数据文件（通常在 HDFS 路径上）**。`DROP TABLE` 对内表与外表行为不同；元数据与文件系统 **没有自动的强一致事务**（需运维流程与回收站策略补齐）。本章建立 **Metastore 心智模型**：它存什么、谁读写它、嵌入式与远程模式差异，并给出可操作的排查顺序。

再记一个**高频误会**：有人把 **「能在 Hive 里 SELECT 出数据」** 当成 **「数据属于 Hive」**。实际上 Hive 只是在 **翻译** 元数据里的路径去读文件；若外表 `LOCATION` 指向的对象存储桶被 **离线 Spark 作业覆盖写**，Hive 元数据一行不改，你的 **查询结果也会「神不知鬼不觉」地变**。因此 **外表 LOCATION 的写入权限** 必须纳入数据安全评审，而不是「反正不是内表」就放松。

---

## 2 项目设计（约 1200 字）


> **角色（对齐 [template.md](../template.md)）**：**小胖**（生活化比喻、抛问题）· **小白**（原理、边界、风险与备选）· **大师**（选型与「**技术映射**」承接）。  
> **对话结构**：小胖开球 1～2 轮 → 小白追问 2～3 轮 → 大师解答并引出下一子话题；全文循环 **2～3 次**，覆盖本章核心概念。

**小胖**：Metastore 听起来像「图书馆索引卡片」？书在架子上，卡片在柜台？

**小白**：接近。更准确说，它存 **库、表、列、分区、存储格式、SerDe、HDFS LOCATION** 等。没有它，Hive 只是把字符串 SQL 解析了，却不知道去哪个路径读 ORC。

**大师**：技术映射：**Metastore ≈ 数据目录（Data Catalog）**；HDFS ≈ **书架上的书**。借书还书（读写数据）要经过目录登记。

**小胖**：那为什么删表了书还在？

**小白**：外表（EXTERNAL）默认 **只删目录条目不删文件**（具体行为与版本/参数相关，生产要以官方文档为准）；内表（MANAGED）通常会删元数据并删目录。很多人踩坑在 **误把生产目录配成外表 LOCATION**，以为 `DROP` 会清文件。

**大师**：所以协作规范要写清：**哪些层允许外表、LOCATION 谁审批、回收站是否开启**。Metastore 不负责替你「业务上安全删除」。

**技术映射**：内外表语义 ≈ **所有权与生命周期策略**。

**小胖**：嵌入式和远程啥区别？

**小白**：嵌入式（如 Derby）适合单机 toy；多人开发要用 **远程 Metastore 服务 + 共享 DB（MySQL/Postgres）**，否则每人一个 Derby 文件，元数据分裂。

**大师**：HiveServer2 通常配置为 **连远程 Metastore**，客户端只连 HS2。这样 **连接风暴** 不会直接打爆 DB，Metastore 可做池化与缓存（仍要注意 DB 锁与慢查询）。

**小胖**：元数据库挂了，我昨天跑成功的作业今天还能复现吗？

**小白**：**已落盘的 HDFS 数据**通常还在，但 **没有 Metastore 就无法解析表**；正在跑、需要频繁取元数据的阶段可能失败；**新会话**基本无法 `SHOW TABLES`。

**大师**：技术映射：**Metastore 可用性 = 数据湖的「电话簿」可用性**；电话簿丢了不等于书没了，但你也几乎拨不通任何号码。

**技术映射**：把容灾重点放在 **元数据库备份 + HMS 多实例 + 连接池超时**，而不是只备份 HDFS。

---

## 3 项目实战（约 1500～2000 字）

### 环境准备

- 已具备第 2 章 Docker 或测试集群；能执行 `beeline`。  
- 有权限查看 `hive-site.xml` 或等价配置中心项。  
- DBA 只读账号可查 Metastore 后端库表（可选，用于加深理解）。

### 步骤 1：确认 Metastore 模式（目标：回答「我连的是谁」）

在会话中执行：

```sql
SET hive.metastore.uris;
-- 若为空，可能嵌入式或从配置文件读取；以实际部署为准
SHOW CREATE TABLE default.foo;
```

同时阅读配置中与 `javax.jdo.option.ConnectionURL`、`hive.metastore.uris` 相关的项，把结论写进笔记：**嵌入式 / 远程 / 直连 DB（不推荐）** 哪一种。

**预期**：能用一段话向同事解释「元数据在哪个 JDBC 库、Thrift URI 是什么」。

### 步骤 2：对比内外表删除语义（目标：亲手验证「数据还在」）

```sql
CREATE DATABASE IF NOT EXISTS demo_meta;
USE demo_meta;

-- 外表：指向一个你 mkdir 的 HDFS 目录（路径按集群调整）
CREATE EXTERNAL TABLE ext_click (id INT, channel STRING)
ROW FORMAT DELIMITED FIELDS TERMINATED BY ','
LOCATION 'hdfs:///tmp/demo_meta_ext_click';

-- 向内表路径外直接 put 一个文件或后续 insert 视环境而定
DROP TABLE ext_click;
```

随后在 HDFS 检查 `hdfs:///tmp/demo_meta_ext_click` 是否仍在。

**坑**：`LOCATION` 写错桶或权限不足 → `CREATE` 失败；**不要用生产 ODS 路径练习**。

### 步骤 3：分区元数据直觉（目标：衔接第 7 章）

```sql
CREATE TABLE part_demo (id INT, msg STRING)
PARTITIONED BY (dt STRING)
STORED AS PARQUET;

-- 若环境允许动态分区，可 INSERT 一天分区；否则只理解 SHOW PARTITIONS
SHOW PARTITIONS part_demo;
```

**预期**：理解 **分区列不出现在数据文件字段列表的重复存储方式**（视格式与写入路径而定），但 Metastore 会登记分区键。

### 步骤 4：协作清单（目标：可落地）

在团队规范中增加三条：

1. 生产 **ODS 层 LOCATION 命名规范** 与负责人。  
2. `DROP` 外表前 **必须** `SHOW CREATE TABLE` 截图留审计。  
3. 开发共用 **同一 Metastore URI**，禁止私自嵌入式 Derby 提交作业。

**验证**：新人 onboarding 能根据 Wiki 在 10 分钟内画 **「HS2 → Metastore → DB / HDFS」** 示意图。

### 进阶：用 `DESCRIBE FORMATTED` 建立「肌肉记忆」

对任意一张正式表执行：

```sql
DESCRIBE FORMATTED your_table;
```

在输出中定位 **`Location:`、`Table Type:`、`Serde Library:`、`Partition Information`** 四段，用手机拍照或复制到笔记——以后每次 **删表 / 改 LOCATION / 改 SerDe** 前强制自己先看这四段，可显著降低误操作率。

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
| 统一元数据，多引擎可复用 | Metastore+DB 成单点与性能瓶颈 |
| 分区/列统计可服务优化器 | 元数据与文件易 **漂移**（外部写入） |
| 权限与血缘可挂钩治理 | 升级脚本与锁表风险需 DBA 协同 |
| 支持企业级部署（standalone） | 配置项多，排障门槛高 |

### 适用与不适用

- **适用**：多团队共享数仓、需要 HCatalog 贯通下游。  
- **不适用**：极简单机脚本且无人协作（嵌入式可凑合但不推荐上团队）。  

### 注意事项

- **备份**：元数据库与 HDFS **同时** 考虑灾备。  
- **锁表**：大批量 `MSCK REPAIR`、分区修复与统计收集要与 DBA 窗口对齐。  

### 常见生产踩坑

1. **多个 Metastore 实例写同一 DB 无协调**：元数据损坏。  
2. **外表路径被离线任务覆盖写**：表能查，数据「突变」。  
3. **升级 Hive 小版本未跑脚本**：分区字段显示异常或统计丢失。

### 思考题

1. 若允许分析师对外表执行 `ALTER TABLE SET LOCATION`，会带来哪些治理风险？  
2. Metastore DB 连接池打满时，HS2 侧典型报错是什么？如何快速区分网络问题与 DB 慢查询？  
3. 若同一表在 **开发与生产 Metastore** 中同名但 `LOCATION` 不同，调度脚本如何避免「连错环境」？（提示：JDBC URL、库前缀、`--hiveconf` 显式覆盖。）

### 跨部门推广提示

- **运维**：监控 Metastore 进程、JVM、DB 连接数与慢 SQL。  
- **测试**：用 **固定分区范围** 断言 `COUNT(*)`，避免环境漂移。  
- **开发**：把 `SHOW CREATE TABLE` 纳入 Code Review 检查项。
