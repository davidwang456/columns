# 第 21 章：ACID 与事务表：增量更新与 Compaction

> **专栏分档**：中级篇  
> **总纲索引**：[hive-column-outline.md](../hive-column-outline.md)（第五章 · 第 21 章对照表）  
> **业务主线**：电商平台「用户行为 + 交易」离线数仓（曝光、点击、下单、退款、风控特征）。

## 本章大纲备忘（写作前对照总纲）

| 项 | 内容 |
|----|------|
| 一句话摘要 | 事务表写入语义与 compaction 运维。 |
| 业务锚点 / 技术焦点 | 拉链表维护成本高、增量更新需求。 |
| 源码或文档锚点 | `ql`。 |

单章目标篇幅 **3000～5000 字**，四段结构对齐 [template.md](../template.md)。

---

## 1 项目背景（约 500 字）

维度 **缓慢变化** 用拉链表维护，夜间 `INSERT OVERWRITE` 全量重算 **窗口长达 6 小时**。部分场景（如 **退款状态修正**）希望 **行级 MERGE**，引入 **Hive ACID / 事务表**。新问题：**delta 文件膨胀**、**读放大**、**compaction 与查询抢资源**。本章讲 **ORC ACID 基础、INSERT/UPDATE/MERGE 语义、compaction 策略**（与发行版文档对齐）。

补充：**ACID 不是替代调度**。Compaction 若与 **高峰查询** 同队列，会出现 **读延迟尖刺**；常见做法是为 compaction 设 **独立 YARN 队列** 与 **并发上限**，并在表属性层设定 **自动合并阈值**（参数名依版本）。另外，**MERGE** 语句在审计上要等价于「可解释的业务事件」，建议工单强制附 **主键与影响行数预估**。

---

## 2 项目设计（约 1200 字）


> **角色（对齐 [template.md](../template.md)）**：**小胖**（生活化比喻、抛问题）· **小白**（原理、边界、风险与备选）· **大师**（选型与「**技术映射**」承接）。  
> **对话结构**：小胖开球 1～2 轮 → 小白追问 2～3 轮 → 大师解答并引出下一子话题；全文循环 **2～3 次**，覆盖本章核心概念。

**小胖**：事务表是不是变成 MySQL 了？

**小白**：是 **有限事务能力**，面向 **批式更新 + 快照读**，不是高并发 OLTP。

**大师**：技术映射：**Hive ACID ≈ 基于文件版本 + 事务日志的乐观模型**。

**小胖**：compaction 是啥？

**小白**：把 **base + deltas** 压成新 base，降低读合并成本；类似 **垃圾回收**。

**大师**：要规划 **轻量/重量压缩** 窗口与 **并发限制**。


> **§2·第三轮**（对齐 [template.md](../template.md) 的第三循环）

**小胖**：事务表听着像数据库——我能拿它当 MySQL 替吗？

**小白**：**写放大、Compaction 与读合并** 成本在；高并发单行更新、强交互事务不是 Hive 主战场。

**大师**：适合 **批增量 + 周期合并** 的链路；若业务要 **毫秒级点查更新**，应显式分流到 OLTP 或流表，再在数仓里 **快照对齐**。

**技术映射**：**Hive ACID ≈ 对象存储上的 MVCC + 后台合并**；语义对齐 **快照隔离** 而非银行核心账务。


---

## 3 项目实战（约 1500～2000 字）

> 语法与属性以当前集群 Hive 版本为准；以下为教学骨架。

### 步骤 1：建事务表（示意）

```sql
CREATE TABLE acid_orders (
  id BIGINT, status STRING, amt DECIMAL(10,2)
)
CLUSTERED BY (id) INTO 4 BUCKETS
STORED AS ORC
TBLPROPERTIES ('transactional'='true');
```

### 步骤 2：MERGE 示例骨架

```sql
MERGE INTO acid_orders AS t
USING staging_orders AS s
ON t.id = s.id
WHEN MATCHED THEN UPDATE SET status = s.status, amt = s.amt
WHEN NOT MATCHED THEN INSERT VALUES (s.id, s.status, s.amt);
```

### 步骤 3：观察 `SHOW COMPACTIONS`（若可用）与文件布局

**坑**：**非 bucket 事务表** 限制（视版本）。  
**坑**：**长事务** 阻塞 compaction。

**验证**：压测前后 **读延迟** 与 **文件数** 曲线。

### 步骤 4：Compaction 观测面板（最小字段集）

- `initiator` / `state` / `start time` / `end time`  
- `delta` 文件数趋势（从 HDFS `hdfs dfs -count` 或表级工具）  
- 读查询 P95 与 compaction 时间窗 **重叠度**

### 步骤 5：MERGE 幂等评审问句（贴 MR 模板）

1. `USING` 子查询是否可能 **重复 key**？  
2. `WHEN NOT MATCHED` 是否会 **误插入历史脏数据**？  
3. 失败重跑是否 **双倍扣款/双倍更新**？（用 **staging + 单键水位线** 兜底）
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
| 支持 upsert 语义 | 运维 compaction 必须跟上 |
| 简化部分拉链逻辑 | 与部分引擎集成需验证 |
| 快照读有助回放 | 小文件/写放大风险 |

### 适用与不适用

- **适用**：中频批量修正、合规修正。  
- **不适用**：高频流式更新（用 Flink/Hudi/Iceberg 等更合适）。  

### 注意事项

- **锁与并发**（第 25 章）。  
- **备份** 与 **回滚脚本**。  

### 常见生产踩坑

1. **未开事务** 却用 `UPDATE`。  
2. **Compaction 失败** 堆积导致读极慢。  
3. **Spark 读 Hive ACID** 版本不匹配。

### 思考题

1. ACID 表与 **Iceberg MERGE** 在运维模型上的差异？  
2. 如何为 compaction 设置 **SLA 与告警**？  
3. 若 `MERGE` 与 **日间只读报表** 并发，你如何设计 **快照隔离** 的用户预期（提示：读可能看到合并前/后的哪一刻）？

### 跨部门推广提示

- **运维**：compaction **独立队列** 与限流。  
- **开发**：MERGE 脚本 **幂等键** 强制评审。
