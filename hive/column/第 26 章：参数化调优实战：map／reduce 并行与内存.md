# 第 26 章：参数化调优实战：map／reduce 并行与内存

> **专栏分档**：中级篇  
> **总纲索引**：[hive-column-outline.md](../hive-column-outline.md)（第五章 · 第 26 章对照表）  
> **业务主线**：电商平台「用户行为 + 交易」离线数仓（曝光、点击、下单、退款、风控特征）。

## 本章大纲备忘（写作前对照总纲）

| 项 | 内容 |
|----|------|
| 一句话摘要 | 并行度、内存、容器参数与 OOM。 |
| 业务锚点 / 技术焦点 | Map/Reduce OOM、GC 频繁。 |
| 源码或文档锚点 | [hive调优/hive参数调优.md](../hive调优/hive参数调优.md)、[堆内存溢出相关.md](../堆内存溢出相关.md)。 |

单章目标篇幅 **3000～5000 字**，四段结构对齐 [template.md](../template.md)。

---

## 1 项目背景（约 500 字）

大作业频繁 **Container OOMKilled**，调大 `mapreduce.map.memory.mb` 后 **队列阻塞**；另一批作业 **小文件多、map 数爆炸**。需要 **系统化调参**：输入拆分、`mapreduce.job.reduces`、Tez `hive.tez.container.size`、`mapjoin` 堆、`spark.executor.memory`（若走 Spark）。本章给 **调参顺序：先定位 OOM 阶段 → 再调并行 → 最后才动代码**。

补充：**参数治理** 和 **调参** 是两件事。没有治理会导致「每人 `SET` 一把」——队列公平性崩、排障无法复现。建议平台提供 **`dev_profile` / `batch_profile` / `explore_profile`** 三套受控参数集，个人会话只允许 **白名单内的 override**。

---

## 2 项目设计（约 1200 字）


> **角色（对齐 [template.md](../template.md)）**：**小胖**（生活化比喻、抛问题）· **小白**（原理、边界、风险与备选）· **大师**（选型与「**技术映射**」承接）。  
> **对话结构**：小胖开球 1～2 轮 → 小白追问 2～3 轮 → 大师解答并引出下一子话题；全文循环 **2～3 次**，覆盖本章核心概念。

**小胖**：内存不够就加倍？

**小白**：可能 **单 task 数据量过大**，加倍只拖延失败；要配合 **分区裁剪、列裁剪、广播阈值**。

**大师**：技术映射：**调参 = 在资源约束下重塑并行剖面**。

**小胖**：Reduce 个数谁决定？

**小白**：**数据量、hive.exec.reducers.bytes.per.reducer**、或 **显式 `SET mapreduce.job.reduces`**；过大会 **shuffle 小文件**，过小会 **单 reduce**。


> **§2·第三轮**（对齐 [template.md](../template.md) 的第三循环）

**小胖**：调参像拧音响旋钮——拧多了会不会「低音炮把邻居震投诉」？

**小白**：会：**container OOM、GC 抖动、小文件暴涨** 都是「旋钮过头」的典型后果；要 **一次只改一类参数** 并留基线。

**大师**：建立 **参数变更单**：动机、前后 diff、回滚值、观测指标（P95、失败率、队列 pending）；没单子不准上生产。

**技术映射**：**Map/Reduce 并行度与内存 = 吞吐与稳定性 trade-off**；最优解依赖 **数据倾斜与 shuffle 量**，不是万能表。


---

## 3 项目实战（约 1500～2000 字）

### 步骤 1：YARN 诊断包

收集 **Container exit code 137/143**、**GC log**。

### 步骤 2：会话级实验矩阵（记录结果表）

| 尝试 | 参数 | 效果 |
|------|------|------|
| A | 提高 container size | |
| B | 增加 reducer 字节阈值 | |
| C | 打开 mapjoin + 调整小表阈值 | |

### 步骤 3：固定一条慢 SQL 做 A/B

**坑**：**全局 SET** 影响他人 → 用 **队列默认 + 会话覆盖**。

**验证**：同一 SQL **Wall time、总 vcore·s、失败率** 三列记录。

### 步骤 4：OOM 分类树（简版）

```text
Container OOM
├─ map 阶段：split 过大 / 解压 / UDF 缓存
├─ shuffle 阶段：单分区过大 / 序列化
└─ reduce 阶段：倾斜 / 聚合状态过大

Driver OOM
├─ 过大结果集 fetch 到客户端
└─ 计划过大 / explain 输出收集（少见）
```

### 步骤 5：把「参数变更」绑定到工单字段

- `before` / `after`（只列关键 3 个键）  
- `rollback`（一键恢复命令）  
- `owner` + `expire_at`（临时会话参数到期提醒）
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
| 快速缓解资源瓶颈 | 过度调参难维护 |
| 可脚本化沉淀 | 版本升级参数语义变化 |
| 与队列联动 | 可能掩盖 SQL 问题 |

### 适用与不适用

- **适用**：已确认计划合理仍资源不足。  
- **不适用**：计划错误（先 EXPLAIN/CBO）。  

### 注意事项

- **记录变更人+原因+回滚值**。  
- **FinOps**：vcore·s 上升要可解释。  

### 常见生产踩坑

1. **Driver OOM** 与 **Executor OOM** 混谈。  
2. **JVM 堆外** 未纳入 container。  
3. **动态分区** + 大 reducer 组合爆内存。

### 思考题

1. 如何用 **Cost-based** 反馈减少盲调？  
2. Tez **unordered split** 与内存关系？  
3. 若提高 `hive.tez.container.size` 后 **队列 pending 暴增**，你如何证明这是「必要代价」还是「掩盖 SQL 问题」？

### 跨部门推广提示

- **平台**：提供 **参数模板 profile**：`small/medium/large`。  
- **开发**：MR 提交附带 **参数注释头**。
