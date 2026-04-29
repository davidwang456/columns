# 第 21 章：高级 RAG —— 查询压缩（Query Compression）

## 1. 项目背景

### 业务场景（拟真）

某售后的智能问答系统中，用户提问习惯是直接粘贴整封投诉邮件或聊天记录——300-500 字的「我上周买了个东西，到货发现坏了，联系客服没人理，你们到底管不管？……」这种长文本直接做向量检索，效果非常差：关键在于「退货」两个字就够了，但查询向量被那 498 字的情绪叙述「支配」了——召回的片段不相关，甚至可能召回带有「联系客服」字的其他文档。

**查询压缩（Query Compression）** 的职责是在用户 query 进入向量检索之前，**压缩掉噪声、保留意图核心**，生成一段更短但更精确的检索 query。

### 痛点放大

不压缩时，长查询直接 embed 会导致：

- **向量偏移**：full-text embedding 会被大量情绪词和背景描述稀释，召回的前几个片段可能完全不相关。
- **成本膨胀**：query 越长、embedding 的 token 越多（云端 embed 按 token 计费），向量检索的延迟也更高。
- **多轮噪声**：客服对话第 5 轮时，用户的 query 往往包含前面轮次的引用（「我刚才说的那个订单怎么样了」），不压缩的话模型在检索时把前文的噪声也带进去了。

## 2. 项目设计：小胖、小白与大师的对话

**小胖**：查询压缩是不是就是把用户写的长篇大论删掉一些字，只留关键词？那我用正则表达式把「你好」「请问」删掉不就行了？

**大师**：你提的正则方案是「启发式压缩」——确实可以快速过滤一些高频无意义词，但它的泛化能力很差。比如用户写「我不要退款，我要换货」——正则如果简单地删了「不」字就变成「要退款」，把意思完全搞反了。真正的查询压缩（用 LLM 或专门的压缩模型）是在 **理解语义** 的前提下进行缩句，保留否定词、实体词和时间条件。它比你想象得更聪明，但也更昂贵（多一次 LLM 调用）。

**小白**：那压缩和查询重写（query rewriting）是一回事吗？压缩之后会不会把重要的「不要退款而是换货」这种否定条件给压丢了？

**大师**：压缩和重写是两种不同的 **查询变形（query transformation）**。**压缩**（compression）是去噪缩句——把 300 字的投诉缩成「订单 #12345 要求退款」。**重写**（rewriting）是同义扩展——把「苹果本」扩写成「MacBook laptop」以增加召回面。两者可以组合使用。你说得对，压丢否定条件是最需要防范的风险——方案是 **加一层规则校验**：如果压缩后的 query 丢失了原文中包含的否定词（「不」「没」「别」），或者丢失了原文中的关键实体（订单号、日期），就 **回退使用原文** 进行检索。**技术映射**：**查询压缩 = 用额外的 LLM 推理成本换取检索质量的提升；关键的权衡不是「压了多少字」，而是「有没有压错信息」——回退策略不是可选项，是压缩功能上线的前提条件**。

---

## 3. 项目实战

### 环境准备

```bash
# 前置条件：已有可运行的 RAG 管线（第 19 章 Naive RAG）
cd langchain4j-examples/rag-examples
export OPENAI_API_KEY="sk-your-key-here"

# 所需依赖已在 pom.xml 中（langchain4j-core、langchain4j-open-ai 等）
```

### 分步实现

#### 步骤 1：在已有 RAG 管线上加装 QueryTransformer

```java
import dev.langchain4j.rag.query.transformer.CompressingQueryTransformer;
import dev.langchain4j.rag.query.router.QueryRouter;
import dev.langchain4j.rag.content.retriever.ContentRetriever;
import dev.langchain4j.rag.content.retriever.EmbeddingStoreContentRetriever;

// 创建压缩器——它内部需要 LLM 来完成压缩
ChatModel compressorModel = OpenAiChatModel.builder()
        .apiKey(System.getenv("OPENAI_API_KEY"))
        .modelName(GPT_4_O_MINI)
        .temperature(0.0)  // 压缩需要确定性输出
        .build();

QueryTransformer transformer = new CompressingQueryTransformer(compressorModel);

// 装配进 ContentRetriever
ContentRetriever retriever = EmbeddingStoreContentRetriever.builder()
        .embeddingStore(embeddingStore)
        .embeddingModel(embeddingModel)
        .queryTransformer(transformer)  // 在这里插入压缩器
        .maxResults(3)
        .build();

// 重建 Assistant
Assistant assistant = AiServices.builder(Assistant.class)
        .chatModel(chatModel)
        .contentRetriever(retriever)
        .build();
```

#### 步骤 2：对比有无压缩的检索效果

```java
// 构造一个带噪声的长查询
String longQuery = "Hi there! I'm writing to express my frustration with the product " +
    "I purchased last week from your store. The item arrived damaged and I've tried " +
    "calling customer service multiple times but nobody answers the phone. I really want " +
    "a refund as soon as possible. My order number is ORD-2024-88888. Please help!";

// 模拟检索过程
// 方案 A：不压缩
List<EmbeddingMatch<TextSegment>> rawMatches = 
    embeddingStore.findRelevant(embedQuery(longQuery), 5);

System.out.println("=== Without Compression ===");
for (EmbeddingMatch<TextSegment> m : rawMatches) {
    System.out.println("Score: " + m.score() + " | " + 
        m.embedded().text().substring(0, 80) + "...");
}

// 方案 B：先压缩再检索
String compressed = transformer.transform(longQuery);
System.out.println("\nCompressed query: " + compressed);

List<EmbeddingMatch<TextSegment>> compressedMatches = 
    embeddingStore.findRelevant(embedQuery(compressed), 5);

System.out.println("=== With Compression ===");
for (EmbeddingMatch<TextSegment> m : compressedMatches) {
    System.out.println("Score: " + m.score() + " | " + 
        m.embedded().text().substring(0, 80) + "...");
}
```

**预期运行结果（文字描述）**：不压缩时，召回的前几条可能包含「call customer service」这类词的片段而非退款政策。压缩后（如压缩为「refund order ORD-2024-88888」），召回的片段应更精准命中退款相关段落。

#### 步骤 3：降级路径测试

```java
// 故意传入空字符串、极短文本、超长文本，断言降级行为
// 空字符串 → 走原文（原文也是空，直接返回）
String empty = "";
String compressedEmpty = transformer.transform(empty);
assert compressedEmpty.equals(empty) : "空字符串应返回原文";

// 超长文本 → LLM 压缩，如果 LLM 超时，回退原文
String veryLong = "a".repeat(10000);
String compressedLong = null;
try {
    compressedLong = transformer.transform(veryLong);
} catch (Exception e) {
    compressedLong = veryLong;  // 回退原文
    System.out.println("Compression failed, fallback to original: " + e.getMessage());
}
```

### 可能遇到的坑

| 坑 | 表现 | 解法 |
|----|------|------|
| 压缩模型与主模型共用配额 | 压缩消耗 token 导致主模型被限流 | 压缩用更便宜的模型或用独立配额 |
| 压缩提示无人评审 | 压缩 prompt 可能被注入 | 压缩提示走 prompt review 流程 |
| 压缩输出空字符串 | 检索直接返回空 | 空结果降级为回退原文 |
| 过度压缩丢否定词 | 「不要退款」变「退款」 | 规则校验 + hash 对比关键 token |

### 测试验证

```bash
# 验证压缩是否生效：对比原始 query 和压缩后 query 的长度
# 在日志中同时打印 originalQuery 和 compressedQuery（生产环境脱敏）

# 预期：compressedQuery 应明显短于 originalQuery
# 预期：同样的黄金测试集，有压缩的 Recall@K >= 无压缩
```

### 完整代码清单

`_3_advanced/_01_Advanced_RAG_with_Query_Compression_Example.java`

---

## 4. 项目总结

### 优点与缺点

| 维度 | Query Compression | 仅重写扩展 | 不处理长查询 |
|------|------------------|-----------|-----------|
| 降噪效果 | 强（去噪保持意图） | 视策略（可扩大噪声） | 无 |
| 额外成本 | +1 次 LLM 调用 | +0～1 次 LLM 调用 | 0 |
| 上线风险 | 压错否定词、压缩模型被注入 | 无风险 | 检索质量差 |
| 典型缺点 | 延迟增加（额外 LLM 调用） | 难控扩展边界 | 长查询命中率低 |

### 适用场景

- 邮件/工单等 **长文本输入** 的问答系统
- 多轮对话中 query 包含大量前文引用噪声
- 需要控制检索 token 预算的场景

### 不适用场景

- **短关键词检索**（10 字以内）——压缩反而添加无关 token
- 无法承受额外 LLM 调用成本——用启发式规则代替

### 注意事项

- 压缩 prompt 中不要暴露内部分类名或索引名
- 监控压缩步骤的耗时占比——如果超过总延迟 20%，考虑换更轻量的压缩方案
- 压缩用的模型建议与主模型隔离配额

### 常见踩坑

1. **过度压缩丢否定条件**：用户说「不要推荐含花生的产品」，压缩后变成「推荐含花生的产品」——必须在压缩后做否定词校验
2. **无回退导致全路瘫痪**：压缩 LLM 超时 → 整个检索不可用 → 必须在代码中实现超时降级到原文
3. **压缩提示无人评审**：压缩 prompt 中包含业务逻辑——必须走 prompt review 流程

### 进阶思考题

1. 多语言长邮件场景下，如何确保压缩模型与输入使用同语言？检测到中文输入但压缩模型只支持英文会怎样？
2. 压缩步骤的耗时占比如何纳入端到端 SLO？如果压缩耗时超过阈值，自动降级不走压缩的逻辑怎么写？

### 推广计划提示

| 角色 | 建议阅读顺序 | 协作要点 |
|------|-------------|----------|
| 开发 | 第 19 章 → 本章 → 第 22 章 | 压缩提示走评审、回退逻辑必须实现 |
| 运维 | 监控 compressor 独立配额 | compressor 与主模型的配额分离、熔断设置 |
| 测试 | 对抗 + 降级 | 空压缩、超长输入、否定词丢失场景 |