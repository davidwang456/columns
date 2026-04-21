# 第 28 章：HCatalog 与上下游元数据贯通

> **专栏分档**：中级篇  
> **总纲索引**：[hive-column-outline.md](../hive-column-outline.md)（第五章 · 第 28 章对照表）  
> **业务主线**：电商平台「用户行为 + 交易」离线数仓（曝光、点击、下单、退款、风控特征）。

## 本章大纲备忘（写作前对照总纲）

| 项 | 内容 |
|----|------|
| 一句话摘要 | 与 Spark 等共享元数据的注意点。 |
| 业务锚点 / 技术焦点 | 上下游 schema 不一致。 |
| 源码或文档锚点 | `source/hive/hcatalog`。 |

单章目标篇幅 **3000～5000 字**，四段结构对齐 [template.md](../template.md)。

---

## 1 项目背景（约 500 字）

Spark 作业读 Hive 表 **`dwd_impression`**，升级 Spark 后 **Parquet 字段类型解析变更**，与 Hive 元数据 **不一致** 导致 **静默读错列**。团队需要理解：**Metastore 作为单一目录**、**HCatalog API**、**StorageDescriptor** 与 **Spark Catalog 配置**（`spark.sql.hive.metastore.version` 等）的对齐策略。

补充：跨引擎问题里 **「表能查」但「结果不对」** 往往比 **直接报错** 更危险。建议对核心表建立 **双引擎回归集**：同一 `WHERE dt=x AND id in (...)` 在 Hive 与 Spark 各跑一次，**checksum 对齐**（第 12 章迁移清单的延伸）。

---

## 2 项目设计（约 1200 字）


> **角色（对齐 [template.md](../template.md)）**：**小胖**（生活化比喻、抛问题）· **小白**（原理、边界、风险与备选）· **大师**（选型与「**技术映射**」承接）。  
> **对话结构**：小胖开球 1～2 轮 → 小白追问 2～3 轮 → 大师解答并引出下一子话题；全文循环 **2～3 次**，覆盖本章核心概念。

**小胖**：HCatalog 是不是老古董？

**小白**：概念上它是 **Hive 元数据 API 与上下游桥接**；很多发行版把能力 **并入 Spark/HMS 客户端**。

**大师**：技术映射：**HCatalog ≈ 用统一元数据描述文件表**。

**小胖**：为啥会 schema 不一致？

**小白**：**Hive ALTER** 与 **Spark 直接写 Parquet** 不同步；或 **case sensitive** 配置差异。


> **§2·第三轮**（对齐 [template.md](../template.md) 的第三循环）

**小胖**：HCatalog 像「各菜系统一点餐」——菜单改了谁通知谁？

**小白**：**Schema 演进** 要 **契约测试**：下游 Spark/Flink 读 Hive 表时，**增列兼容、改类型不兼容** 必须 CI 拦。

**大师**：把 **「破坏性变更」** 定义成清单（删列、窄化类型、改分隔符），变更必须走 **版本号 + 公告周期**。

**技术映射**：**元数据贯通 = Hive Metastore 作为事实源 + 下游引擎的 schema 契约测试**。


---

## 3 项目实战（约 1500～2000 字）

### 步骤 1：用 Spark SQL 读 Hive 表（伪命令）

```bash
spark-sql --conf spark.sql.catalogImplementation=hive -e "DESCRIBE EXTENDED dwd_impression"
```

### 步骤 2：对比 Hive `DESCRIBE FORMATTED`

列 **serde、location、partition keys** 是否一致。

### 步骤 3：制定 **「写表规范」**

- 默认 **Hive 管 DDL**，Spark **只写数据不改Serde**（或反之但统一）  
- **Schema evolution** 流程：先 Hive `ALTER` 再 Spark 发布

**坑**：**Hive bucketed** 表被 Spark **非 bucket 写入** 破坏布局。  
**坑**：**不同 ORC 版本**。

**验证**：变更列类型后 **双引擎 SELECT** 同 key 行级 diff。

### 步骤 4：`DESC FORMATTED` 双引擎对齐检查表

| 检查项 | Hive `DESC FORMATTED` | Spark `DESCRIBE EXTENDED` |
|--------|------------------------|-----------------------------|
| Location | 一致？ | 一致？ |
| InputFormat / SerDe | 一致？ | 一致？ |
| Partition cols | 顺序一致？ | 顺序一致？ |

### 步骤 5：发布流程插入「元数据门禁」

- DDL MR 必须附 **Spark + Hive** 两侧 `DESCRIBE` 截图  
- 列类型变更必须附 **兼容性说明**（是否需回填）
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
| 单一元数据降低协作成本 | 多引擎语义坑多 |
| HCatalog 促进工具集成 | 版本矩阵复杂 |
| 与数据治理衔接 | 需要流程而非仅靠工具 |

### 适用与不适用

- **适用**：Hive+Spark 双栈团队。  
- **不适用**：完全 Iceberg 统一 catalog 的新栈（仍建议读本章历史包袱）。  

### 注意事项

- **大小写敏感** 与 **保留字**。  
- **时间类型** 映射。  

### 常见生产踩坑

1. **Spark overwrite** 删分区元数据。  
2. **动态分区列顺序** 与 Spark writer 不一致。  
3. **缓存元数据陈旧**。

### 思考题

1. **Glue Data Catalog** 与 **Hive Metastore** 双注册如何防漂移？  
2. 如何用 **合约测试** 保护跨引擎 schema？  
3. 若 Spark 使用 **case sensitive** 模式而 Hive 不敏感，如何避免「同名列大小写」导致的 join  silently empty？

### 跨部门推广提示

- **架构**：发布 **《跨引擎写表规范》** 一页纸。  
- **CI**：schema diff 阻断发布。
