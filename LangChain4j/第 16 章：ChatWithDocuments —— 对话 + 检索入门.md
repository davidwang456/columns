# 第 16 章：ChatWithDocuments —— 对话 + 检索入门

## 1. 项目背景

在深入 RAG 前，`ChatWithDocuments` 模式把「助理既能聊天，又能查私域文档」收缩为一条 `AiServices` 装配路径：给 Assistant 配置 `ContentRetriever`。它衔接第 12 章 AiServices 与第五部分 RAG：先理解「多一个依赖组件」，再学 ingest、切分、向量库。

教程文件：`_12_ChatWithDocumentsExamples.java`

## 2. 项目设计：小胖、小白与大师的对话

**小胖**：这跟第 18 章 Easy RAG 是不是一回事？

**小白**：界线在哪？topK 能指定吗？多轮对话闲聊会不会影响检索？

**大师**：体验相近，差别在透明度——本示例更倾向让你看到管线拼装。topK 在 `EmbeddingStoreContentRetriever` 的 builder 里配。闲聊问题会稀释检索 query——需要查询重写（第 21 章）或分离检索 query。**技术映射**：**检索 query ≠ 闲聊全文**。

**小胖**：库存这种实时数能从文档里搜吗？

**小白**：只对某些意图启用文档？要展示引用来源吗？

**大师**：**绝不能从文档里搜库存**——实时数据走 Tool 调 API。向量库只放政策条款、FAQ 类静态文本。B 端用户很在意来源——建议展示引用。**技术映射**：**ContentRetriever = 静态文本证据；Tool = 实时系统真相；两者泾渭分明**。

## 3. 项目实战

### 环境准备

```bash
cd langchain4j-examples/tutorials
export OPENAI_API_KEY="sk-your-key-here"
```

### 步骤 1：AiServices + ContentRetriever 装配

```java
import dev.langchain4j.data.segment.TextSegment;
import dev.langchain4j.memory.chat.MessageWindowChatMemory;
import dev.langchain4j.model.embedding.EmbeddingModel;
import dev.langchain4j.model.embedding.onnx.allminilml6v2q.AllMiniLmL6V2QuantizedEmbeddingModel;
import dev.langchain4j.model.openai.OpenAiChatModel;
import dev.langchain4j.rag.content.retriever.EmbeddingStoreContentRetriever;
import dev.langchain4j.service.AiServices;
import dev.langchain4j.store.embedding.InMemoryEmbeddingStore;
import dev.langchain4j.store.embedding.EmbeddingStoreIngestor;

import static dev.langchain4j.model.openai.OpenAiChatModelName.GPT_4_O_MINI;

interface Assistant {
    String chat(String userMessage);
}

public class ChatWithDocumentsDemo {

    public static void main(String[] args) {

        ChatModel model = OpenAiChatModel.builder()
                .apiKey(System.getenv("OPENAI_API_KEY"))
                .modelName(GPT_4_O_MINI)
                .build();

        // 准备一些内嵌知识
        String knowledgeBase = """
            Company policy: Refunds are processed within 5-7 business days.
            Company policy: Password resets require admin approval.
            Company policy: Annual leave must be approved by your manager.
            FAQ: How to reset password - Go to Settings > Security > Reset Password.
            FAQ: How to request leave - Submit through HR portal at hr.example.com.
            """;

        // 切分+嵌入+存入内存向量库
        InMemoryEmbeddingStore<TextSegment> embeddingStore = new InMemoryEmbeddingStore<>();
        EmbeddingModel embeddingModel = new AllMiniLmL6V2QuantizedEmbeddingModel();
        
        EmbeddingStoreIngestor.ingest(embeddedStore, embeddingStore);

        // 构建 ContentRetriever
        ContentRetriever retriever = EmbeddingStoreContentRetriever.from(embeddingStore);

        Assistant assistant = AiServices.builder(Assistant.class)
                .chatModel(model)
                .chatMemory(MessageWindowChatMemory.withMaxMessages(10))
                .contentRetriever(retriever)
                .build();

        // 测试：问知识库里的内容
        String answer = assistant.chat("How do I reset my password?");
        System.out.println(answer);

        // 测试：问知识库外的问题
        String answer2 = assistant.chat("What's the weather like today?");
        System.out.println(answer2);
    }
}
```

**预期输出**：
```
You can reset your password by going to Settings > Security > Reset Password.
```
第二个问题应使用模型常识回答，而非从知识库中搜索天气。

### 步骤 2：写三类问题的测试

```java
// 1. 仅需常识的问题（不涉及知识库）
String question1 = "What is the capital of France?";
// 预期：用模型常识回答，不应检索

// 2. 必须命中文档的问题
String question2 = "How long do refunds take?";
// 预期：检索到 policy 并用 5-7 business days 回答

// 3. 需要实时状态的问题
String question3 = "What is my current order status?";
// 预期：应触发工具调用（而不是搜文档）
```

### 步骤 3：区分「为什么」检索失败

```java
// Case A：索引中根本无相关内容 → 模型应说不知道
// Case B：有相关内容但没被召回 → 调 topK 或检查嵌入质量  
// Case C：有内容且召回但模型没引用 → 加提示约束
```

### 闯关任务

| 难度 | 动手 | 过关标准 |
|------|------|----------|
| ★ | 新增一段文档并验证模型能回答 | 检索引擎在运行 |
| ★★ | 写一段矛盾条款看模型如何处理 | 了解模型选边或和稀泥 |
| ★★★ | 标注三类问题各应走检索还是工具 | 理解事实源分界 |

### 可能遇到的坑

| 坑 | 表现 | 解法 |
|----|------|------|
| ingest 未做 | 模型回答猜测式胡编 | 先执行 ingest 再查询 |
| 无引用来源 | 用户不信任回答 | 返回 sources 列表 |
| 超大文档未分页 | 调试困难 | 用分段预览 |

### 测试验证

```bash
# 三态用例：无索引 / 坏索引 / 精确命中
# 断言引用块与 UI 一致性
```

### 完整代码清单

[`_12_ChatWithDocumentsExamples.java`](../../langchain4j-examples/tutorials/src/main/java/_12_ChatWithDocumentsExamples.java)

## 4. 项目总结

### 优点与缺点

| 维度 | ChatWithDocuments | 仅 ChatModel | Easy RAG |
|------|------------------|-------------|---------|
| 心智负担 | 中 | 低 | 低 |
| 透明度 | 较高 | 无 RAG | 较低 |

### 适用 / 不适用场景

**适用**：原型、培训、团队对齐「对话+知识」。

**不适用**：已确定上生产级分区 ACL（须按第 24/25/32 章设计）。

### 常见踩坑

1. ingest 未做却期望文档事实
2. 无引用却要求用户信任
3. 超大文档未分页预览

### 进阶思考题

1. 何时把 ContentRetriever 换成多路融合（第 27 章）？
2. 会话轮次多时如何压缩查询（第 21 章）避免噪声？

### 推广计划

| 角色 | 建议阅读顺序 | 协作要点 |
|------|-------------|----------|
| 开发 | 第 12 章 → 本章 → 第 18 章 | 文档 vs 工具分层 |
| 产品 | 本章 + 第 24 章 | 来源展示设计 |
| 运维 | 检索耗时占比 | ingest 批任务配额 |

### 检查清单

- **测试**：对无索引/坏索引/精确命中三态设计用例；验证引用块与 UI 一致性
- **运维**：监控检索耗时占比；单独对 ingest 批任务设资源配额

### 附录

| 模块 | 说明 |
|------|------|
| `langchain4j` | `AiServices`、`EmbeddingStoreContentRetriever` |

推荐阅读：`_12_ChatWithDocumentsExamples.java` → 衔接 `Easy_RAG_Example.java`。
