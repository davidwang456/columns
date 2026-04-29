# 第 19 章：Naive RAG —— 拆分摄取与查询

## 1. 项目背景

团队已通过 Easy RAG 做出 Demo，但无法解释「答错时错在切分、嵌入还是模型」。需要在可教学、可调试的显式管线上迭代：加载 → 切分 → 嵌入 → 写入向量库 → topK 检索 → 拼进提示，暂不引入重排、路由、压缩等高级策略。

教程文件：`rag-examples/src/main/java/_2_naive/Naive_RAG_Example.java`

## 2. 项目设计：小胖、小白与大师的对话

**小胖**：Naive 是不是菜的意思？上线能用吗？

**小白**：Naive 能当 MVP 吗？示例里 Chat 走云、embed 走本地 ONNX——为什么分开？

**大师**：Naive 是**可调试基线**，不是贬义。当 MVP 可行，但用户量和合规上来要补元数据、鉴权、混合检索。Chat 和 embed 分开部署是因两者需求不同——embed 本地跑省费用且满足数据主权。**技术映射**：**Naive = 显式的可调试基线，不是功能全集**。

## 3. 项目实战

### 环境准备

```bash
cd langchain4j-examples/rag-examples
export OPENAI_API_KEY="sk-your-key-here"
```

### 步骤 1：显式六步管线

```java
import dev.langchain4j.data.document.*;
import dev.langchain4j.data.document.parser.TextDocumentParser;
import dev.langchain4j.data.document.splitter.DocumentSplitters;
import dev.langchain4j.data.segment.TextSegment;
import dev.langchain4j.model.embedding.EmbeddingModel;
import dev.langchain4j.model.embedding.onnx.allminilml6v2q.AllMiniLmL6V2QuantizedEmbeddingModel;
import dev.langchain4j.model.openai.OpenAiChatModel;
import dev.langchain4j.store.embedding.*;
import dev.langchain4j.rag.content.retriever.EmbeddingStoreContentRetriever;
import dev.langchain4j.service.AiServices;
import java.nio.file.Paths;
import java.util.List;

import static dev.langchain4j.model.openai.OpenAiChatModelName.GPT_4_O_MINI;

interface Assistant {
    String chat(String userMessage);
}

public class NaiveRAGDemo {

    public static void main(String[] args) {
        
        // Step 1: Chat 模型（云）
        ChatModel chatModel = OpenAiChatModel.builder()
                .apiKey(System.getenv("OPENAI_API_KEY"))
                .modelName(GPT_4_O_MINI)
                .build();

        // Step 2: 加载文档
        Document document = FileSystemDocumentLoader
            .loadDocument(Paths.get("documents/policy.txt"), new TextDocumentParser());
        System.out.println("Loaded: " + document.text().length() + " chars");

        // Step 3: 切分
        DocumentSplitter splitter = DocumentSplitters.recursive(300, 50);
        List<TextSegment> segments = splitter.split(document);
        System.out.println("Split into: " + segments.size() + " segments");

        // Step 4: 嵌入模型（本地 ONNX）
        EmbeddingModel embeddingModel = new AllMiniLmL6V2QuantizedEmbeddingModel();

        // Step 5: 嵌入并写入向量库
        EmbeddingStore<TextSegment> embeddingStore = new InMemoryEmbeddingStore<>();
        List<Embedding> embeddings = embeddingModel.embedAll(segments).content();
        embeddingStore.addAll(embeddings, segments);
        System.out.println("Embedded and stored " + embeddings.size() + " vectors");

        // Step 6: 构建检索器
        ContentRetriever retriever = EmbeddingStoreContentRetriever.from(embeddingStore);

        Assistant assistant = AiServices.builder(Assistant.class)
                .chatModel(chatModel)
                .chatMemory(MessageWindowChatMemory.withMaxMessages(10))
                .contentRetriever(retriever)
                .build();

        String answer = assistant.chat("What is the return policy?");
        System.out.println("Answer: " + answer);
    }
}
```

**预期输出**：
```
Loaded: 512 chars
Split into: 3 segments
Embedded and stored 3 vectors
Answer: Items can be returned within 30 days of purchase...
```

### 步骤 2：每一步都有日志

```java
// 在 import 后添加日志配置
// 在生产中，你可以通过这一行知道是 embed 慢还是 LLM 慢
long t0 = System.nanoTime();
// ... 每步操作 ...
long t1 = System.nanoTime();
System.out.println("Step X took: " + (t1 - t0) / 1_000_000 + "ms");
```

### 步骤 3：调 chunk 大小看效果

```java
// 对比不同 chunk 大小的影响
DocumentSplitters.recursive(100, 20).split(document);  // 小 chunk
DocumentSplitters.recursive(500, 100).split(document);  // 大 chunk
```

### 步骤 4：故意只 embed 一半文档

```java
// 只把前一半 segment 写入 store
List<TextSegment> halfSegments = segments.subList(0, segments.size() / 2);
```

观察模型是否对后半段内容胡编——这就是 **负样本**。

### 闯关任务

| 难度 | 动手 | 过关标准 |
|------|------|----------|
| ★ | 纸笔画七个方框连线 | 理解完整管线 |
| ★★ | 调大 chunk 问跨段问题 | 理解分段的语义边界 |
| ★★★ | 只 embed 一半文档 | 观察胡编 = 负样本 |

### 可能遇到的坑

| 坑 | 表现 | 解法 |
|----|------|------|
| 忘记 embed / 重复 embed | 索引不一致 | 幂等批处理 |
| 切分太小 | 丢失上下文 | 按业务语义调参 |
| 切分太大 | 混合语义噪声 | 按 token 上限切 |
| 评测只看最终回答 | 掩盖检索问题 | 先看召回命中率 |

### 测试验证

```bash
# 召回列表 snapshot（脱敏）与黄金集比对 top@k
# 空召回时断言应用提示
```

### 完整代码清单

[`Naive_RAG_Example.java`](../../langchain4j-examples/rag-examples/src/main/java/_2_naive/Naive_RAG_Example.java)

## 4. 项目总结

### 优点与缺点

| 维度 | Naive 显式管线 | Easy RAG | 低层 RAG |
|------|--------------|---------|---------|
| 可调试性 | 高 | 低 | 最高 |
| 样板代码 | 多 | 少 | 最多 |
| 典型缺点 | 口语检索脆弱 | 黑箱 | 维护成本 |

### 适用 / 不适用场景

**适用**：内部培训、首次向量库接现网前验证。

**不适用**：亿级文档强 ACL（须后续章节与生产向量库）。

### 常见踩坑

1. 忘记 embed / 重复 embed 未去重
2. 切分太小/太大 → 丢上下文或噪声
3. 评测只看最终回答不看召回片段

### 进阶思考题

1. 同一文档两次 ingest segment 数不一致时如何定位？
2. 何时引入第 21 章压缩而非调大 chunk？

### 推广计划

| 角色 | 建议阅读顺序 | 协作要点 |
|------|-------------|----------|
| 开发 | 第 18 章 → 本章 → 第 20 章 | 七框图必画 |
| 测试 | 召回 snapshot | 空召回断言 |
| 运维 | 批索引配额 | 向量库磁盘/内存 |

### 检查清单

- **测试**：snapshot 召回列表（脱敏）与黄金集比对 top@k；空召回断言
- **运维**：批索引资源配额；向量库磁盘或内存阈值

### 附录

| 模块 | 说明 |
|------|------|
| `langchain4j` | `DocumentSplitter`、`EmbeddingStoreIngestor` |
| `embeddings/*` | 如 `BgeSmallEnV15QuantizedEmbeddingModel` |

推荐阅读：`Naive_RAG_Example.java` 顶部注释、`EmbeddingStoreContentRetriever`。
