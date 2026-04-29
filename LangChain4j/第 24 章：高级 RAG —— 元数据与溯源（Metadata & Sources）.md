# 第 24 章：高级 RAG —— 元数据与溯源（Metadata & Sources）

## 1. 项目背景

### 业务场景（拟真）

某金融科技公司的合规部门要求：AI 客服给出的所有涉及政策和条款的回答，**必须能追溯到源文档的具体版本和段落**。用户问「提前还款有违约金吗」，模型回答「根据《个人消费贷款合同》v2.3 第 4.2 条，提前还款收取剩余本金的 1%」，合规审核的同事需要能点开这个回答看到原文。

这就是 **溯源（Source Attribution）**——它要求在 ingest 时为每个 `TextSegment` 写入 metadata（文档 ID、版本号、段落号），在回答时通过 API 响应一并返回匹配的 source 列表。

### 痛点放大

不溯源时：模型回答无法验证——用户问「违约金多少」，模型说「3%」，但合规发现最新合同已经改成「1%」了。因为没有版本号，没人知道模型引用的是哪个版本的合同。metadata 还可以用于后续的审计和权限过滤——「这个 source 所属的租户跟当前用户一致吗？」

## 2. 项目设计：小胖、小白与大师的对话

**小胖**：metadata 是不是就像给每本书贴上图书馆的标签——书名、作者、分类号、上架日期？贴太多标签会不会把书包撑坏了？

**大师**：图书馆的比喻很贴切。贴标签的原则是：**只存短键和外联 ID**，不要存大段文本。metadata 的典型字段是 `docId`、`version`、`tenantId`、`chunkIndex`——都是短字符串或整数。不要往 metadata 里存大段的政策原文——那是全文，不应该放在 metadata 里，应该放在原始存储中。metadata 只做 **索引和指针** 用。

**小白**：如果用户不看 UI 上的来源引用，我们还需要做溯源吗？版本号写在哪里？PII 能不能放进 metadata？

**大师**：即使用户不点开引用，审计和合规仍然需要——B2B 场景下，合规检查时可以导出回答及其关联的 source 列表进行核对。版本号由 **文档发布流水线** 写入——每次文档更新时自动递增版本号，并作为 metadata 写入新的 segments。PII 在 metadata 里要尽量避免；如果必须包含用户 ID 这类信息，需要 **访问控制 + 日志脱敏**。**技术映射**：**引用一致性 = 单次召回的原子性——召回的片段、展示的来源、模型的依据必须是同一次检索操作的结果，不能模型引用 v1 片段、页面展示却指向 v2**。

---

## 3. 项目实战

### 环境准备

```bash
cd langchain4j-examples/rag-examples
export OPENAI_API_KEY="sk-your-key-here"
```

### 分步实现

#### 步骤 1：ingest 时写入 metadata

```java
import dev.langchain4j.data.segment.TextSegment;
import dev.langchain4j.store.embedding.EmbeddingStore;
import dev.langchain4j.store.embedding.InMemoryEmbeddingStore;

// 切分后的 segments 附上 metadata
List<TextSegment> segments = splitter.split(document);
for (int i = 0; i < segments.size(); i++) {
    TextSegment segment = segments.get(i);
    segment.metadata().put("docId", "POLICY-2024-001");
    segment.metadata().put("version", "2.1");
    segment.metadata().put("department", "legal");
    segment.metadata().put("language", "zh-CN");
    segment.metadata().put("chunkIndex", String.valueOf(i));
}

// 嵌入并写入（带 metadata）
List<Embedding> embeddings = embeddingModel.embedAll(segments).content();
embeddingStore.addAll(embeddings, segments);

System.out.println("Ingested " + segments.size() + " segments with metadata.");
```

#### 步骤 2：返回溯源列表

```java
// 定义带来源的响应结构
public class AnswerWithSources {
    String answer;
    List<Source> sources;
}

public class Source {
    String docId;
    String version;
    String snippet;
    double score;
}

// 检索并构建溯源
List<EmbeddingMatch<TextSegment>> matches = 
    embeddingStore.findRelevant(queryEmbedding, 3);

AnswerWithSources result = new AnswerWithSources();
result.sources = matches.stream().map(m -> {
    Source s = new Source();
    s.docId = m.embedded().metadata().getString("docId");
    s.version = m.embedded().metadata().getString("version");
    s.snippet = m.embedded().text().substring(0, Math.min(200, m.embedded().text().length()));
    s.score = m.score();
    return s;
}).collect(Collectors.toList());

// 传给 LLM 的提示中包含片段文本，返回给前端时附带 sources
System.out.println("Found " + result.sources.size() + " sources:");
result.sources.forEach(s -> 
    System.out.println("  " + s.docId + " v" + s.version + " (score: " + s.score + ")"));
```

#### 步骤 3：版本不一致检测

```java
// 如果召回的片段版本与当前最新版本不一致，标记出来
String latestVersion = fetchLatestVersion("POLICY-2024-001");

for (Source source : result.sources) {
    if (!source.version.equals(latestVersion)) {
        System.out.println("WARN: Source " + source.docId 
            + " version " + source.version 
            + " is outdated. Latest version: " + latestVersion);
        // 可降级：不展示该来源，或提示用户信息可能过时
    }
}
```

### 可能遇到的坑

| 坑 | 表现 | 解法 |
|----|------|------|
| metadata 过多 | 向量库存储膨胀 | 只存短键+指针 |
| PII 录入 metadata | 合规事故 | metadata 中禁用敏感字段 |
| 版本号缺失 | 无法判断信息新鲜度 | 文档流水线强制写版本号 |

### 测试验证

```bash
# 端到端断言：响应结构中 source.id 属于当前用户授权集合
# 链接 403 时不泄露文档存在性（返回通用错误）
```

### 完整代码清单

`_04_Advanced_RAG_with_Metadata_Example.java`、`_09_Advanced_RAG_Return_Sources_Example.java`

---

## 4. 项目总结

### 优点与缺点

| 维度 | Metadata + Return sources | 无溯源 RAG | 仅模型口述 |
|------|-------------------------|-----------|----------|
| 审计能力 | 强（可逐条回溯至原文） | 弱 | 无 |
| 用户信任度 | 高（可验证信息来源） | 中 | 低 |
| 工程成本 | 高（需管线写 metadata） | 低 | 最低 |

### 适用场景

- 金融、医疗、政务等强合规行业
- 内部政策助理需要审计追踪

### 不适用场景

- 闲聊型机器人（用户不关心来源）
- 文档频繁变更但没有版本管理

### 常见踩坑

1. **只展示自然语言不展示来源** → 用户无法验证答案
2. **版本号缺失** → 引用过时条款
3. **metadata 泄漏内部代号** → 竞品可能通过 source 字段推测你的文档分类

### 进阶思考题

1. 多语言同一文档时，metadata 中 language 字段如何与路由协同？
2. CDN 缓存了旧答案，但文档已更新——如何让缓存与来源版本对齐？

### 推广计划提示

| 角色 | 建议阅读顺序 | 协作要点 |
|------|-------------|----------|
| 开发 | 本章 → 第 25 章 | 同一批 match 进提示和响应 |
| 合规 | 本章 | 导出需带齐来源 |
| 运维 | CMS 健康检查 | 索引与源站版本巡检 |