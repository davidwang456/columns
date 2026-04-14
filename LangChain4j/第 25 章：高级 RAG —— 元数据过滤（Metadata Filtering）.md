# 第 25 章：高级 RAG —— 元数据过滤（Metadata Filtering）

## 1. 项目背景

即使检索语义「像正确答案」，在 **多租户/多部门** 系统中，也必须保证 **用户只见授权范围内的文档片段**。仅靠 **在提示里叮嘱模型** 不足以防 **数据越权**；必须在 **检索层** 用 **metadata filter** 约束 **`findRelevant`** 的候选集。

`langchain4j-core` 在 `store.embedding.filter` 包下提供 **可组合的过滤表达式**（`And`/`Or`/`Not` 与比较器）。示例：

- `langchain4j-examples/rag-examples/src/main/java/_3_advanced/_05_Advanced_RAG_with_Metadata_Filtering_Examples.java`

若业务需要 **类 SQL** 表达，可关注 **`langchain4j-embedding-store-filter-parser-sql`**（名称以发行版 BOM 为准）等解析模块，将 **字符串谓词** 映射为 **filter 对象**——适合 **运营配置**，但要 **防注入**。

## 2. 项目设计：大师与小白的对话

**小白**：为什么不下推 filter 到数据库？

**大师**：**应该下推**；否则你会 **先拉回大量向量再在内存过滤**，成本高且易 **信息泄露**到应用日志。

**小白**：filter 从哪来？

**大师**：**服务端从认证上下文推导**（tenant、部门、密级），**绝不**盲信客户端传的 JSON。

**小白**：复杂 OR 条件会不会太宽？

**大师**：会，属于 **安全热点**；需要 **单元测试**与 **渗透测试** 双检。

**小白**：字段类型不一致怎么办？

**大师**：**ingest 规范化**：枚举、ISO 时间戳、小写 tenant slug。

**小白**：过滤和路由（第 22 章）重复吗？

**大师**：**路由**是 **粗粒度**「去哪找」；**过滤**是 **细粒度**「能看哪些行」。两者应 **一致**：**路由选的域** ⊆ **过滤允许的域**。

**小白**：「管理员看全部」怎么实现？

**大师**：**显式** `if (admin) skipTenantFilter` —— 但要 **审计**并 **最小化**管理员会话窗口。

**小白**：性能？

**大师**：依赖 **向量库索引**是否支持 **metadata 索引**；否则变成 **慢查询**。

## 3. 项目实战：主代码片段

> **场景入戏**：不设 metadata filter 的多租户 RAG，像在 **合租房里共用衣柜**：别人的 **白衬衫**可能被你的 **向量相似度**误捞过来穿——**法律上叫数据越权**。

阅读 [`_05_Advanced_RAG_with_Metadata_Filtering_Examples.java`](../../langchain4j-examples/rag-examples/src/main/java/_3_advanced/_05_Advanced_RAG_with_Metadata_Filtering_Examples.java)：

1. 圈出一个 **`And(...)`**，**口述**给人听（像解释 **WHERE**）。  
2. 改写成 **参数化 SQL 伪代码**：`WHERE tenant_id = ? AND ...`。  
3. **红队脑暴**：攻击者能否 **猜租户枚举**？→ 记在 **安全评审**附录。

#### 深度彩蛋

若团队爱写 **字符串 SQL filter**，对照模块 **`langchain4j-embedding-store-filter-parser-sql`**（若引入）——分清 **解析器**与 **注入面**。

## 4. 项目总结

### 优点

- **安全下推**，性能与合规更好。  
- **表达力**覆盖常见比较操作。

### 缺点

- **不同向量库**能力不一致，需要 **特性矩阵**。  
- **schema 演进**与 **存量索引重建** 绑定。

### 适用场景

- 多租户 SaaS、**分级保密**内部搜索。

### 注意事项

- **默认拒绝**。  
- **渗透测试**纳入发版门槛。

### 常见踩坑

1. **OR** 过宽 → **跨租户泄露**。  
2. **过滤仅在前端执行**。  
3. **metadata 未索引**导致 **隐式全表扫**。

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
