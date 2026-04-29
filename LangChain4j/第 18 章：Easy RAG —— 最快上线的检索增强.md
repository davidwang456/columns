# 第 18 章：Easy RAG —— 最快上线的检索增强

## 1. 项目背景

### 业务场景（拟真）

某创业公司的 Java 后端在两周后要向投资人演示一个「AI 知识库问答」原型：运营上传几份产品说明 TXT 文件，用户就能在对话框问「Model X 支持哪些参数？」架构组来不及在这两周内搭起独立的 Milvus 或 pgvector 集群——甚至还没确定用哪个向量库。

这就是 Easy RAG 要解决的问题：**用最少的代码、最少的架构决策，先跑通一个「上传文档 → 提问 → 从文档中找答案」的完整闭环**。ingest 过程的解析、切分、嵌入全部由 `EmbeddingStoreIngestor` 的默认值完成——你不用关心背后用了什么 splitter、什么 embedding model。

### 痛点放大

如果从一开始就想把 RAG 管线做到生产级——选向量库、选嵌入模型、调切分参数、配元数据过滤——两周时间大概率连原型都跑不通。Easy RAG 的理念是 **分层递进**：先交付可演示问答，再逐一偿还技术债。

代价是「黑箱」——你看不到文档是怎么被切开的、用的是什么嵌入模型、存储在哪里。一旦 Demo 通过、产品决定上线，你就需要打开黑箱：第 19 章 Naive RAG 让你看到每一行代码；第 20 章低层 RAG 让你控制每一步；第 24-26 章让你加上权限和安全。

## 2. 项目设计：小胖、小白与大师的对话

**小胖**：Easy RAG 是不是像火锅自助——我自己带肉（文档）来，锅底（框架）自动给我切好涮好？不用我动手切菜？

**大师**：火锅的比喻非常准确——你只管把肉（文档）端进来，`EmbeddingStoreIngestor` 像火锅汤底一样自动帮你完成所有工序：切成合适大小（DocumentSplitter 默认）、烫熟（EmbeddingModel 嵌入）、捞出摆盘（存进 InMemoryEmbeddingStore）。你不用管后厨（代码实现）怎么操作的，只需享受结果——问「Model X 多少钱」模型从文档中找到答案回答你。代价是——如果肉没切好（切分太粗导致混合语义），或者汤底不对（默认的嵌入模型不适合你的语言），你没法直接调整，必须换到第 20 章的「低层自己做火锅」。

**小白**：那个 `InMemoryEmbeddingStore`——意思是不是所有向量都存在应用内存里？它只能跑玩具级别的 Demo 吧？生产肯定不行？

**大师**：对——`InMemoryEmbeddingStore` 顾名思义：所有向量和文本都存在 JVM 堆内存里。这决定了它的三个局限：① **容量受限于 JVM 内存**——大几万条 segments 可能没问题，上百万条堆内存就撑不住了；② **没有持久化**——应用重启后索引丢失，需要重新 ingest；③ **不能跨实例共享**——水平扩展到多 Pod，每个 Pod 有自己的内存索引，彼此不一致。但是！**它最适合的场景恰好就是 PoC、单测、和日活几百人的小规模内测**——没有外部向量库的依赖，你可以在一个 `main` 方法里完成从加载到回答的全过程。生产上线时再换 pgvector/Milvus——接口层由 `EmbeddingStore` 抽象隔离，业务代码几乎不用改。**技术映射**：**Easy RAG = 默认管线 + 最小化配置，它的价值不是「生产级」，而是「让你在 10 分钟内跑通首个检索增强问答，确认这个模式对你的业务有效」**。

**小白**：如果换了嵌入模型——比如从默认的小模型换成 OpenAI 的 text-embedding-3 怎么办？还有——公司的机密文档通过 Easy RAG 被召回后，会不会直接泄露进提示词里被用户看到？

**大师**：换嵌入模型需要 **显式地配置 `EmbeddingStoreIngestor` 和 `ContentRetriever`**——也就是从 Easy RAG 升级到第 19 章的 Naive RAG（显式管线）。Easy RAG 把这一切藏在默认值里，你要换模型就必须打开管线自己配。关于安全——Easy RAG **不管企业权限**。意味着如果文档包含「客户 A 的合同金额」和「客户 B 的合同金额」，用户问「客户 A 的合同金额是多少」，检索器可能把客户 B 的合同也召回回来——因为向量相似度只看语义不看权限。这就是为什么第 24 章的元数据过滤和第 25 章的元数据过滤是生产 RAG 的前提条件——在检索阶段就根据租户 ID 过滤掉不属于当前用户的数据。**技术映射**：**ContentRetriever 是片段集合的抽象接口，背后可以是向量检索、关键词搜索或混合检索——但安全地召回不是 Easy RAG 的承诺，需要额外的权限治理层**。

## 3. 项目实战

### 环境准备

```bash
cd langchain4j-examples/rag-examples
export OPENAI_API_KEY="sk-your-key-here"

# 准备示例文档
mkdir -p documents
cat > documents/policy.txt << 'EOF'
Company Return Policy:
- Items can be returned within 30 days of purchase.
- Refunds are processed within 5-7 business days.
- Items must be in original packaging.

Product Specifications:
- Model X: 256GB storage, 8GB RAM, $599
- Model Y: 512GB storage, 16GB RAM, $899
- Model Z: 1TB storage, 32GB RAM, $1299
EOF
```

### 步骤 1：跑通 Easy RAG

```java
// 核心代码（节选自 Easy_RAG_Example.java）
import dev.langchain4j.data.document.Document;
import dev.langchain4j.data.document.loader.FileSystemDocumentLoader;
import dev.langchain4j.data.segment.TextSegment;
import dev.langchain4j.memory.chat.MessageWindowChatMemory;
import dev.langchain4j.model.openai.OpenAiChatModel;
import dev.langchain4j.rag.content.retriever.EmbeddingStoreContentRetriever;
import dev.langchain4j.service.AiServices;
import dev.langchain4j.store.embedding.InMemoryEmbeddingStore;
import dev.langchain4j.store.embedding.EmbeddingStoreIngestor;

import static dev.langchain4j.model.openai.OpenAiChatModelName.GPT_4_O_MINI;

interface Assistant {
    String chat(String userMessage);
}

public class EasyRAGDemo {

    public static void main(String[] args) {
        
        ChatModel model = OpenAiChatModel.builder()
                .apiKey(System.getenv("OPENAI_API_KEY"))
                .modelName(GPT_4_O_MINI)
                .build();

        // 加载文档
        List<Document> documents = FileSystemDocumentLoader
            .loadDocuments(java.nio.file.Paths.get("documents"), "*.txt");

        System.out.println("Loaded " + documents.size() + " documents.");

        // Easy RAG：一键 ingest
        InMemoryEmbeddingStore<TextSegment> embeddingStore = new InMemoryEmbeddingStore<>();
        EmbeddingStoreIngestor.ingest(documents, embeddingStore);

        ContentRetriever retriever = EmbeddingStoreContentRetriever.from(embeddingStore);

        Assistant assistant = AiServices.builder(Assistant.class)
                .chatModel(model)
                .chatMemory(MessageWindowChatMemory.withMaxMessages(10))
                .contentRetriever(retriever)
                .build();

        // 测试问答
        String[] questions = {
            "What is the return policy?",
            "How much does Model Y cost?",
            "What is the weather like today?"  // 不在文档中的问题
        };

        for (String q : questions) {
            System.out.println("\nQ: " + q);
            System.out.println("A: " + assistant.chat(q));
        }
    }
}
```

**预期输出**：
```
Loaded 1 documents.

Q: What is the return policy?
A: Items can be returned within 30 days of purchase. Refunds take 5-7 business days.

Q: How much does Model Y cost?
A: Model Y costs $899.

Q: What is the weather like today?
A: I don't have information about the current weather. (不胡编)
```

### 步骤 2：闯关试验

| 关卡 | 操作 | 通过标准 |
|------|------|----------|
| ★ | 改 `documents/policy.txt` 一句事实重启再问 | 回答跟着变——检索真在工作 |
| ★★ | 塞两段互相矛盾的条款 | 看模型选边站还是和稀泥 |
| ★★★ | 中文条款+英文提问 | 观察跨语言检索短板 |

### 步骤 3：矛盾文档实验

```bash
# 在 documents 中添加条款2
cat > documents/terms2.txt << 'EOF'
Special Promotion Terms:
- Return period extended to 60 days for promotional items.
- Refunds processed within 2-3 business days for premium members.
EOF
```

重启运行，看模型如何处理 `policy.txt`（30天）和 `terms2.txt`（60天）的矛盾。

### 可能遇到的坑

| 坑 | 表现 | 解法 |
|----|------|------|
| 路径/编码问题 | 文档加载失败 | 用绝对路径+UTF-8 |
| 误以为 Easy = 生产 | 换外置库、观测、回滚没做 | 记录技术债 |
| 空检索仍胡编 | 回答不在文档中的问题 | 提示中加「无依据则拒答」 |

### 测试验证

```bash
# 构造三类用例：
# 1. 文档有答案 → 应准确引用
# 2. 文档无答案 → 应说不知道
# 3. 多文档冲突 → 应说明矛盾
```

### 完整代码清单

[`Easy_RAG_Example.java`](../../langchain4j-examples/rag-examples/src/main/java/_1_easy/Easy_RAG_Example.java)

## 4. 项目总结

### 优点与缺点

| 维度 | Easy RAG | Naive RAG | 仅 Chat 无检索 |
|------|---------|-----------|--------------|
| 上线速度 | 极快 | 中 | 最快 |
| 可控性 | 弱 | 强 | 无检索 |
| 典型缺点 | 黑箱默认、难审计 | 实现成本高 | 无私域事实 |

### 适用 / 不适用场景

**适用**：内部分享、工作坊、Hackathon；引入外置向量库前的逻辑验证。

**不适用**：强 ACL 合规分区（须第 24/25/32 章）、亿级文档（须分布式）。

### 常见踩坑

1. 路径/编码（Windows vs UNIX）导致加载失败
2. 误以为 Easy = 生产（缺外置库、观测、回滚）
3. 空检索仍胡编——提示须无依据则拒答

### 进阶思考题

1. 如何将文档版本号与 embedding 模型版本打进同一条审计日志？
2. 跨语言问答时 Easy 默认 embed 的短板？

### 推广计划

| 角色 | 建议阅读顺序 | 协作要点 |
|------|-------------|----------|
| 开发 | 第 16 章 → 本章 → 第 19 章 | 技术债清单 |
| 产品 | 本章 | 矛盾条款与拒答体验 |
| 运维 | 本章 + 第 32 章 | ingest 峰值、未来索引重建 |

### 检查清单

- **测试**：构造文档有答案/无答案/多文档冲突三类用例
- **运维**：记录嵌入批任务 CPU/内存峰值；预留外置向量库连接串与重建索引 Runbook

### 附录

| 模块 | 说明 |
|------|------|
| `langchain4j` | `AiServices`、`InMemoryEmbeddingStore` |
| `langchain4j-easy-rag` | 简化摄取与默认管线 |

推荐阅读：`Easy_RAG_Example.java`、`EmbeddingStoreIngestor`、`AiServices`。
