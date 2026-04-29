# 第 12 章：AiServices —— 接口即「智能服务」

## 1. 项目背景

### 业务场景（拟真）

团队每天写大量样板代码：接收用户消息 → 构造 `UserMessage` 对象 → 放成 `List<ChatMessage>` → 调 `model.chat(messages)` → 从 `Response` 中提取文本 → 返回给调用方。如果再加个 system prompt、加个记忆、加个工具、加个检索——每加一样，样板代码量翻倍。

业务开发的抱怨集中在：**「我只想定义『这个接口用什么样的 system prompt、调什么模型、挂什么工具』然后就用它，能不能像 Spring Data JPA 那样写个 interface 就完事了？」**

`AiServices` 就是 LangChain4j 对这个问题给出的答案。它允许你定义一个纯 Java 接口，用 `@SystemMessage`、`@UserMessage` 等注解描述行为，框架在底层为你 **生成代理实现**，注入 `ChatModel`、`ChatMemory`、tools 和 retriever。调用起来就像调用一个普通的 Spring Bean。

### 痛点放大

手写样板代码的主要问题：

- **重复劳动**：每次增加一个新的 AI 功能（翻译、摘要、分类），都要复制粘贴「构造消息列表 → 调模型 → 解析结果」三段式代码。
- **不可测性**：样板代码和业务逻辑混在一起，无法对业务逻辑做独立的单元测试——每次测试都真实调模型。
- **不一致性**：三个开发写了三个 AI 服务，system prompt 里换行的风格不同、错误处理的方式不同、日志打的格式不同。

`AiServices` 的目标就是：**把「拼消息→调模型→解析」这个流程彻底封装起来**，让你只需要关心两件事——「我的接口签名是什么」和「我的提示词是什么」。

## 2. 项目设计：小胖、小白与大师的对话

**小胖**：这跟 Spring Data JPA 有点像啊——定义一个 `interface` 加几个注解，背后全自动给你生成实现代码？

**大师**：从「写接口得实现」这个形态上确实像 JPA——你定义一个 `interface` 加几个注解，框架帮你生成代理类。但 **心智模型必须彻底切换**：JPA 背后是确定的 SQL 查询——同样的查询条件每次返回同样的结果。AiServices 背后是 **概率系统**——同样的输入模型可能每次输出不同。这意味着你不能像对待 JPA 那样「只管调不管兜底」——你必须显式设计 **超时怎么办、重试怎么办、观测怎么打点、降级怎么切**。

**小白**：那一个接口里写多个方法——比如 `chat()`、`translate()`、`summarize()`——会不会互相污染？它们的 ChatMemory 和提示词是共享的吗？

**大师**：**这是 AiServices 最常见的误用场景。** 一个接口里的多个方法默认共享同一个 `ChatMemory` 实例——如果你在 `chat()` 里聊了 5 轮「帮我查订单 #123」，然后调 `translate()` 翻译一句话——模型可能还记得那 5 轮订单对话，干扰了翻译的纯净性。两个原则：① **不同业务职责拆成不同接口**——翻译一个接口、客服一个接口、摘要一个接口，各自有独立的 ChatMemory 和工具集；② **如果同一接口的多个方法需要共享上下文**（如「先查订单再总结」），那就让它们用同一套 memory，但要确保提示词里明确区分当前该做什么。**技术映射**：**AiServices 是语法糖 + 横切能力织入（自动注入 memory/tools/retriever），不是魔法 RPC——你不能把它当 RPC 调了就不管超时和降级；把它当 RPC 调，线上一定出事故**。

**小白**：那怎么单测？每次跑测试都真调一次模型既慢又贵。Kotlin 项目能用吗？

**大师**：**可测试性是 AiServices 相比手写样板最大的优势之一**——因为你的业务代码面向的是接口，所以可以轻松用 Mockito mock 整个接口：`when(assistant.chat(any())).thenReturn("mock response")`，不需要真实模型。也可以用「假的 ChatModel」——一个返回固定字符串的 `ChatModel` 实现，注入到 `AiServices.builder()` 中做集成测试，不需要网络。Kotlin 完全支持——`langchain4j-kotlin` 模块提供了 Kotlin 友好的扩展函数和协程支持，抽象层与 Java 完全一致。**技术映射**：**可测试性靠的是接口边界的清晰定义——面向接口编程在这里不是口号，是你能否在不调模型的情况下覆盖 90% 业务逻辑的关键分水岭**。

## 3. 项目实战

### 环境准备

```bash
cd langchain4j-examples/tutorials
export OPENAI_API_KEY="sk-your-key-here"
```

### 步骤 1：定义 AiServices 接口

```java
import dev.langchain4j.service.AiServices;
import dev.langchain4j.service.SystemMessage;
import dev.langchain4j.service.UserMessage;
import dev.langchain4j.model.chat.ChatModel;
import dev.langchain4j.model.openai.OpenAiChatModel;
import static dev.langchain4j.model.openai.OpenAiChatModelName.GPT_4_O_MINI;

// 1. 定义接口
interface Assistant {
    
    @SystemMessage("You are a polite and helpful assistant")
    String chat(@UserMessage String message);
    
    @SystemMessage("You are a translator. Translate the user's text to {{language}}.")
    String translate(@UserMessage String text, 
                     @dev.langchain4j.service.V("language") String language);
}

public class AiServicesDemo {

    public static void main(String[] args) {

        ChatModel model = OpenAiChatModel.builder()
                .apiKey(System.getenv("OPENAI_API_KEY"))
                .modelName(GPT_4_O_MINI)
                .build();

        // 2. 生成代理
        Assistant assistant = AiServices.builder(Assistant.class)
                .chatModel(model)
                .build();

        // 3. 像调用普通接口一样使用
        String greeting = assistant.chat("Hello! What can you do?");
        System.out.println("Chat result: " + greeting);

        String translation = assistant.translate(
            "How much does this cost?", 
            "Chinese");
        System.out.println("Translation: " + translation);
    }
}
```

**预期输出**：
```
Chat result: Hello! I can help you with questions, translations, and more!
Translation: 这个多少钱？
```

### 步骤 2：加入 ChatMemory

```java
import dev.langchain4j.memory.chat.MessageWindowChatMemory;

Assistant assistant = AiServices.builder(Assistant.class)
        .chatModel(model)
        .chatMemory(MessageWindowChatMemory.withMaxMessages(10))
        .build();

// 多轮对话现在有记忆了
assistant.chat("My name is Alice");
String response = assistant.chat("What's my name?");  // 应记得 Alice
System.out.println(response);  // "Your name is Alice"
```

### 步骤 3：用 @Tool 添加工具

```java
import dev.langchain4j.agent.tool.Tool;

// 定义一个工具类
static class Calculator {
    
    @Tool("Calculates the sum of two numbers")
    int add(int a, int b) {
        return a + b;
    }
}

Assistant assistant = AiServices.builder(Assistant.class)
        .chatModel(model)
        .tools(new Calculator())
        .build();

// 模型会决定何时调用 add
String result = assistant.chat("What is 15 + 27?");
System.out.println(result);  // "15 + 27 = 42"
```

### 步骤 4：单测——用 Mock 代替真实模型

```java
// 测试代码（不需要真实 API Key）
ChatModel mockModel = Mockito.mock(ChatModel.class);
when(mockModel.chat(Mockito.anyString())).thenReturn("Mock response");

Assistant assistant = AiServices.builder(Assistant.class)
        .chatModel(mockModel)
        .build();

String result = assistant.chat("Any question");
assertEquals("Mock response", result);  // 不依赖真实模型
```

### 闯关任务

| 难度 | 动手 | 过关标准 |
|------|------|----------|
| ★ | Ctrl+F `AiServices.builder` 圈出最小配置 | 知道哪些字段是必须的 |
| ★★ | 标注「删掉 memory/tools/retriever 后还能工作吗」 | 理解各组件的可选性 |
| ★★★ | 用 Mock ChatModel 写一个单元测试 | 不依赖真实 API |

### 可能遇到的坑

| 坑 | 表现 | 解法 |
|----|------|------|
| 共享 Assistant Bean + 无 memory | 每轮失忆 | 配 ChatMemory |
| 工具 + RAG 同开 | 提示超长 | 评估是否两者都需要 |
| 多租户单 Assistant 实例 | 串数据 | 每租户独立实例 |

### 测试验证

```bash
# 契约测试：方法签名 ↔ 提示模板变量一致性
# 例如：@UserMessage("Translate to {{lang}}") 必须对应参数 lang
```

### 完整代码清单

[`_08_AIServiceExamples.java`](../../langchain4j-examples/tutorials/src/main/java/_08_AIServiceExamples.java)

## 4. 项目总结

### 优点与缺点

| 维度 | AiServices | 手写 ChatModel | 其他框架 Assistant API |
|------|-----------|---------------|----------------------|
| 可读性 | 高（声明式） | 中 | 视框架 |
| 调试 | 代理层需学习 | 直链 | 视框架 |
| 复杂控制流 | 中 | 灵活 | 视框架 |

### 适用 / 不适用场景

**适用**：BFF/领域服务封装对话、快速 PoC 与后续拆分微服务。

**不适用**：极复杂多段编排（用显式状态机）、必须逐条操纵消息的底层实验。

### 常见踩坑

1. 未配 ChatMemory → 每轮失忆
2. 工具 + RAG 同开 → 提示超长
3. 多租户单 Assistant 实例 → 串数据

### 进阶思考题

1. `@AiService`（Spring）与手动 `AiServices.builder` 在 Listener 注入上差在哪？
2. 如何用字节码断点或日志定位生成的方法入口？

### 推广计划

| 角色 | 建议阅读顺序 | 协作要点 |
|------|-------------|----------|
| 开发 | 第 10 章 → 本章 → 第 14 章 | 接口拆分降提示污染 |
| 测试 | 契约 + 上下文测试 | 签名与模板变量一致 |
| 运维 | 第 34 章 | 首次调用冷启动、原生镜像 |

### 检查清单

- **测试**：契约测试关注方法签名 ↔ 提示模板变量一致性
- **运维**：把 AiServices 创建耗时与首次调用冷启动纳入发布检查

### 附录

| 模块 | 说明 |
|------|------|
| `langchain4j` | `AiServices`、`AiService` 相关注解 |
| `langchain4j-spring-*` | Spring 专用 `@AiService` |

推荐阅读：`_08_AIServiceExamples.java`、`AiServices` 源码入口、`Assistant.java`（Spring）。
