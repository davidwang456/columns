# 第 25 章：高级 RAG —— 元数据过滤（Metadata Filtering）

## 1. 项目背景

### 业务场景（拟真）

多租户系统中，语义「像正确答案」仍可能 **越权**：用户看到 **其他租户文档片段**。仅靠 **提示里叮嘱模型** 不够；必须在 **检索层** 用 **metadata filter** 约束 `findRelevant` 候选集。

### 痛点放大

若 **filter 不下推到数据库**：**先拉回大量向量再内存过滤** → 成本高、日志易 **泄露** 候选集。`store.embedding.filter` 提供 **可组合表达式**（`And`/`Or`/`Not`）。可选 **`langchain4j-embedding-store-filter-parser-sql`** 将 **字符串谓词** 映射为 filter——适合 **运营配置**，须 **防注入**。示例：[`_05_Advanced_RAG_with_Metadata_Filtering_Examples.java`](../../langchain4j-examples/rag-examples/src/main/java/_3_advanced/_05_Advanced_RAG_with_Metadata_Filtering_Examples.java)。

## 2. 项目设计：小胖、小白与大师的对话

**小胖**：filter 像小区门禁？只扫自己楼栋？

**小白**：为啥要 **下推数据库**？**filter 从哪来**？**复杂 OR** 会不会太宽？

**大师**：**应下推**；否则 **内存过滤** 贵且易泄露。**filter 从认证上下文推导**（tenant、密级），**绝不**盲信客户端 JSON。**OR 过宽** = **安全热点**——**单测 + 渗透**。**技术映射**：**filter = 检索层硬约束**。

**小胖**：和 **第 22 章路由** 重复吗？

**小白**：**管理员看全部**？**性能**？

**大师**：**路由**粗粒度「去哪找」；**过滤**细粒度「能看哪些行」——**路由选域 ⊆ 过滤允许域**。管理员 **显式 skip** 须 **审计 + 最小窗口**。性能依赖 **向量库 metadata 索引**；否则 **隐式全表扫**。**技术映射**：**默认拒绝 + 索引对齐**。

---

## 3. 项目实战

### 环境准备

- [`_05_Advanced_RAG_with_Metadata_Filtering_Examples.java`](../../langchain4j-examples/rag-examples/src/main/java/_3_advanced/_05_Advanced_RAG_with_Metadata_Filtering_Examples.java)。

### 分步任务

1. 圈出 **`And(...)`**，口述成 **WHERE**。  
2. 改写成 **参数化 SQL 伪代码**。  
3. **红队**：攻击者猜 **租户枚举**？记入安全评审。

**深度**：若用 **SQL 字符串 filter**，对照 **`langchain4j-embedding-store-filter-parser-sql`**（若引入）——分清 **解析器与注入面**。

### 测试验证

- **同 query 不同租户** 矩阵；**篡改 tenant** header。

### 完整代码清单

见仓库 `_05_Advanced_RAG_with_Metadata_Filtering_Examples.java`。

---

## 4. 项目总结

### 优点与缺点（与同类做法对比）

| 维度 | Metadata filter | 仅提示约束 | 应用层过滤召回 |
|------|-----------------|------------|----------------|
| 安全 | 高 | 低 | 中 |
| 性能 | 依赖下推 | 不适用 | 差 |
| 典型缺点 | 库能力矩阵 | 不可信 | 泄露面 |

### 适用场景

- 多租户 SaaS、**分级保密** 内部搜索。

### 不适用场景

- **单租户、全公开文档**——可简化。

### 注意事项

- **默认拒绝**；**渗透** 纳入发版。

### 常见踩坑经验（生产向根因）

1. **OR 过宽** → 跨租户泄露。  
2. **过滤仅在前端**。  
3. **metadata 未索引** → 全表扫。

### 进阶思考题

1. **ingest 规范化**（枚举、ISO 时间）与 **存量重建** 的迁移顺序？  
2. **filter 与路由** 不一致时的 **自动化检测**？

### 推广计划提示（多部门）

| 角色 | 建议阅读顺序 | 协作要点 |
|------|----------------|----------|
| **开发** | 第 22、24 章 → 本章 | **一致路由域** |
| **安全** | 本章 | **渗透门槛** |
| **运维** | 索引字段 | **慢查询** 报警 |

---

### 本期给测试 / 运维的检查清单

**测试**：**固定用例矩阵**（同 query、不同租户）；**模糊安全**测试尝试 **篡改 tenant** header。  
**运维**：向量库侧 **索引字段**与 **查询计划**抽检；异常 **慢查询**报警。

### 附录：相关 Maven 模块与源码类

| 模块 | 说明 |
|------|------|
| `langchain4j-core` | `store.embedding.filter.*` |
| `langchain4j-embedding-store-filter-parser-sql` | SQL 字符串解析（若引入） |

推荐阅读：`_05_Advanced_RAG_with_Metadata_Filtering_Examples.java`。
