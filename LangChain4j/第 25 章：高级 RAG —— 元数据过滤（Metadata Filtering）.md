# 第 25 章：高级 RAG —— 元数据过滤（Metadata Filtering）

## 1. 项目背景

### 业务场景（拟真）

一个多租户的 SaaS 知识库系统中，租户 A 和租户 B 的文档都在同一个 `EmbeddingStore` 里。用户 A 问「我们的退款政策是什么」，语义上最相似的可能是租户 B 的退款政策——因为两家都做电商，退款政策的用词非常像。如果向量检索没有按租户过滤——用户 A 就看到了租户 B 的文档。

**元数据过滤（Metadata Filtering）** 是在检索时通过 `filter` 表达式约束候选集——只搜索 `tenantId = currentUser.tenantId` 的 segments。

### 痛点放大

很多人会想：我可以在提示词里加一句「只回答当前租户的内容」——但这是软约束，模型不一定遵守。正确的做法是 **在检索层就强制过滤**：向量库执行 `findRelevant` 时通过 filter 参数把不属于该租户的文档从候选集中排除。如果 filter 不 **下推到数据库**，而是先全量召回再在内存中过滤，成本极高且候选集泄露了其他租户的数据。

## 2. 项目设计：小胖、小白与大师的对话

**小胖**：元数据过滤是不是就像每个小区的大门门禁——你刷你的卡只能进你的那栋楼，刷另一个人的卡就报警？

**大师**：这个比喻非常精确。但需要注意一个关键点：**门禁必须在「小区门口」就执行（下推到数据库），而不是让所有人都进小区以后再挨家挨户敲门确认是不是这栋楼（内存过滤）**。先全量召回再内存过滤的问题：一是贵（召回 10000 条再丢掉 9900 条），二是泄露（内存里临时存着其他租户的数据，日志可能打印出来）。

**小白**：filter 的来源应该是什么？是从客户端传过来的 JSON 吗？users 可以篡改 tenant 参数怎么办？

**大师**：**永远不能从客户端传来的 JSON、Header 或参数中直接拿 filter 条件。** filter 只能从 **服务端的认证上下文** 推导而来——当前登录用户的 tenantId、角色、数据权限等级。客户端传来的任何 filter 参数都必须视为不受信任的输入，只能作为「在一个已经强制限定于该租户的 filter 之上的额外约束」。**技术映射**：**filter = 检索层的硬约束，不是软提示——它在数据库层面执行，来源只能是服务端认证上下文，不能是客户端传来的任何数据**。

---

## 3. 项目实战

### 环境准备

```bash
cd langchain4j-examples/rag-examples
export OPENAI_API_KEY="sk-your-key-here"
```

### 分步实现

#### 步骤 1：用 filter 做多租户隔离

```java
import static dev.langchain4j.store.embedding.filter.Filter.*;
import dev.langchain4j.store.embedding.filter.Filter;

// 假设当前请求来自 tenant-123 的用户
String currentTenant = "tenant-123";

// 构建 filter：只能看到该租户的文档
Filter tenantFilter = eq("tenantId", currentTenant);

// 检索时传入 filter
List<EmbeddingMatch<TextSegment>> matches = 
    embeddingStore.findRelevant(queryEmbedding, 5, tenantFilter);

System.out.println("Found " + matches.size() + " results for tenant " + currentTenant);
// 不会返回其他租户的 segment
```

#### 步骤 2：复杂组合条件

```java
// 多条件组合：租户 + 部门 + 密级
Filter complexFilter = and(
    eq("tenantId", currentTenant),
    in("department", "legal", "hr"),    // 只看法务和人事
    gte("securityLevel", 3)             // 密级 >= 3
);

List<EmbeddingMatch<TextSegment>> matches = 
    embeddingStore.findRelevant(queryEmbedding, 5, complexFilter);
```

#### 步骤 3：安全渗透测试

```java
// 红队测试：尝试篡改 tenantId
String attackedTenant = request.getParameter("tenantId");  // 用户可能改成 "tenant-999"
String realTenant = SecurityContextHolder.getContext().getAuthentication().getTenantId();

Filter filter = eq("tenantId", realTenant);  // ✅ 只用服务端值
// Filter attackedFilter = eq("tenantId", attackedTenant);  // ❌ 绝对不要用客户端传来的

// 即使攻击者传了 tenantId=tenant-999，filter 还是强制走 realTenant
List<EmbeddingMatch<TextSegment>> matches = 
    embeddingStore.findRelevant(queryEmbedding, 5, filter);
```

### 可能遇到的坑

| 坑 | 表现 | 解法 |
|----|------|------|
| OR 条件过宽 | 跨租户泄露 | 每个 OR 分支都要带上 tenantId 约束 |
| 过滤仅在前端 | 用户可绕过前端直接调 API | 过滤必须在后端执行 |
| metadata 未建索引 | 完整扫描 → 性能退化 | 确保向量库为过滤字段建索引 |

### 测试验证

```bash
# 固定用例矩阵：同 query、不同租户 → 应返回不同结果
# 模糊安全测试：篡改 tenant 请求头 → 应返回 403
```

### 完整代码清单

`_05_Advanced_RAG_with_Metadata_Filtering_Examples.java`

---

## 4. 项目总结

### 优点与缺点

| 维度 | Metadata filter（下推） | 仅提示约束 | 应用层过滤 |
|------|-----------------------|-----------|----------|
| 安全 | 高（数据库级强制） | 低 | 中 |
| 性能 | 好（利用索引） | 不涉及 | 差（先全量召回） |
| 典型缺点 | 依赖向量库的 filter 实现 | 不可信 | 数据泄露风险 |

### 适用场景

- 多租户 SaaS 知识库
- 文档有密级/部门隔离需求
- 需在检索层实现行级安全

### 不适用场景

- 单租户全公开文档（不需要 filter）
- 向量库不支持复杂 filter 表达式

### 常见踩坑

1. **OR 过宽导致跨租户泄露**——每个 OR 分支都必须单独加租户约束
2. **过滤仅在前端**——绕过前端直接调 API 就绕过了过滤
3. **metadata 未建索引**——隐式全表扫描

### 进阶思考题

1. Ingest 阶段 metadata 规范化（枚举值、ISO 日期格式）与存量重建的迁移顺序？
2. filter 与路由（第 22 章）不一致时如何自动化检测？

### 推广计划提示

| 角色 | 建议阅读顺序 | 协作要点 |
|------|-------------|----------|
| 开发 | 第 22 章 → 本章 | 路由域与 filter 一致 |
| 安全 | 本章 | 渗透测试用例集 |
| 运维 | 向量库索引字段 | 慢查询告警 |