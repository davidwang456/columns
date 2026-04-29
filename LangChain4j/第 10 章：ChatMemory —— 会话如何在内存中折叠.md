# 第 10 章：ChatMemory —— 会话如何在内存中折叠

## 1. 项目背景

### 业务场景（拟真）

客服机器人上线第一周表现不错，用户问「我的订单什么时候发货？」它能回答「您的订单 #12345 已于 3 月 15 日发出，预计 3 月 18 日到达。」但到了第五轮对话——用户连续追问「那如果我一直没收到怎么办？」「退货运费谁出？」「能帮我查另一个订单吗？」——模型开始答非所问。不是因为模型变笨了，而是因为 **每次请求都从零开始**——模型不知道用户刚刚说过「还有一个订单」指的是什么，因为它没有「记忆能力」。

最简单的方案是把整段历史对话每次都发给模型——但这样成本太高：如果聊了 50 轮，每次请求都要把前面 50 轮的所有文本都发一遍，token 消耗呈线性增长。如果用户的 history 超过了模型的上下文窗口（比如 128K tokens），直接报错。

### 痛点放大

`ChatMemory` 要解决的问题是：**在保留上下文与控制 token 长度之间找平衡**。没有记忆策略的话：

- **成本不可控**：全量历史发送，token 消耗与对话轮次成正比——用户聊 10 轮的成本是 1 轮的 10 倍以上。
- **体验断层**：模型失忆后，用户需要重复之前说过的话——「我刚才不是说了订单号吗？怎么又问我一遍？」
- **调试困难**：不知道模型的「失忆」是因为截断设置得太短，还是因为检索没命中，还是模型本身的问题。

LangChain4j 提供了两种内存策略：**`MessageWindowChatMemory`**（按消息条数截断，保留最近 N 条）和 **`TokenWindowChatMemory`**（按估算 token 数截断，更贴近真实的上下文窗口与计费）。

## 2. 项目设计：小胖、小白与大师的对话

**小胖**：记忆是不是就像朋友圈——只显示最近三天，之前的朋友圈就不让你看了？

**大师**：朋友圈的比喻很准——区别在于朋友圈是「按天数」，ChatMemory 是「按条数或按 token 数」。两者目标一样：只保留「足够做决策」的上下文，丢掉太久远或太占空间的信息。`MessageWindowChatMemory` 就像「只显示最近 10 条朋友圈」，`TokenWindowChatMemory` 就像「只显示最近 1000 字的内容」。

**小白**：那什么时候用 `MessageWindow`、什么时候用 `TokenWindow`？还有一个关键问题——如果 system message 很长（比如写了几百字的客服规范），它会不会先被截断策略挤掉？

**大师**：选择的判断依据是：**短对话 PoC 用条数省事（`MessageWindowChatMemory.withMaxMessages(10)`），因为条数容易理解**。一旦要贴近 **计费和真正的上下文窗口上限**，就必须用 `TokenWindowChatMemory`——因为它按 token 数截断，与模型计费单位一致。system message 被挤掉是一个 **非常真实的风险**。如果 system 很长（2000 tokens），但你的 `MessageWindowChatMemory` 设置了「保留最近 10 条消息」——这 10 条消息的 token 数可能已经超过了上下文窗口，而 system message 在截断策略中可能被当作「最早的消息」优先丢弃。结果是：**关键约束（「不要提及竞争对手」、「必须用中文回复」）被静默移除了**，模型行为偏离预期。`TokenWindowChatMemory` 的好处就是：它会把 system message 的 token 一起算进预算里，不容易「无声爆窗」。**技术映射**：**窗口策略的核心选择 = 选择条数近似值还是 token 精确值——如果你的 system message、few-shot 或工具描述很重，选 TokenWindow 更安全；如果都是用户与 AI 的短消息对话，MessageWindow 更简单**。

**小白**：多用户同时聊天——会不会出现用户 A 说「我要退款」，用户 B 的对话里也看到了 A 的退款信息？这叫「串话」吧？还有，工具调用返回的大 JSON（几百个 token）会不会一次性把窗口撑爆？

**大师**：串话是 **生产中最常见的 ChatMemory 事故**，根因是：把 `ChatMemory` 声明成了单例（Singleton）。一个 `ChatMemory` 实例就是一个会话记录。如果所有用户共享一个实例，他们就从同一个「历史池」里读写——用户 A 的消息被用户 B 看到了。解法是：**每个会话（session）或每个用户必须拥有独立的 `ChatMemory` 实例**，通常通过 `memoryId` 来隔离（第 13 章会讲持久化的 `ChatMemoryStore`）。工具调用返回的大 JSON 确实可以瞬间撑爆窗口——比如一个查订单的工具返回了完整的订单详情 JSON（可能 2000 tokens），这一条消息就占了你窗口预算的一半。解法有两个思路：① **对工具返回做摘要**，不要返回完整 JSON，只返回必要字段提炼后的一句话；② **把工具结果外置存储**，在 memory 里只保留一个引用指针。**技术映射**：**记忆截断的阈值、工具结果的长度、多用户的实例隔离——这三件事是 ChatMemory 在生产上出问题的前三名原因。它们不是在写 CRUD 时会遇到的经验，但一旦线上出一次，就是 SS 级事故**。

## 3. 项目实战

### 环境准备

```bash
cd langchain4j-examples/tutorials
export OPENAI_API_KEY="sk-your-key-here"
```

### 步骤 1：跑通带记忆的对话

```java
import dev.langchain4j.data.message.*;
import dev.langchain4j.memory.ChatMemory;
import dev.langchain4j.memory.chat.TokenWindowChatMemory;
import dev.langchain4j.model.openai.OpenAiStreamingChatModel;
import dev.langchain4j.model.openai.OpenAiTokenCountEstimator;
import dev.langchain4j.model.chat.response.ChatResponse;
import dev.langchain4j.model.chat.response.StreamingChatResponseHandler;
import java.util.concurrent.CompletableFuture;

import static dev.langchain4j.model.openai.OpenAiChatModelName.GPT_4_O_MINI;

public class MemoryDemo {

    public static void main(String[] args) throws Exception {

        OpenAiStreamingChatModel model = OpenAiStreamingChatModel.builder()
                .apiKey(System.getenv("OPENAI_API_KEY"))
                .modelName(GPT_4_O_MINI)
                .build();

        // Token 窗口记忆，最多保留 1000 个 token
        ChatMemory chatMemory = TokenWindowChatMemory.withMaxTokens(
                1000, new OpenAiTokenCountEstimator(GPT_4_O_MINI));

        // 添加 system message
        chatMemory.add(SystemMessage.from("You are a senior Java developer. " +
                "Answer concisely. When asked about best practices, always mention testing."));

        // 用户第一轮
        UserMessage userMessage1 = UserMessage.from(
                "How do I optimize database queries in Spring Boot?");
        chatMemory.add(userMessage1);
        
        String answer1 = streamChat(model, chatMemory);
        System.out.println("User: " + userMessage1.singleText());
        System.out.println("AI: " + answer1);

        // 用户第二轮（上下文应保留第一轮的答案）
        UserMessage userMessage2 = UserMessage.from(
                "Can you give me a concrete example with @Query?");
        chatMemory.add(userMessage2);
        
        String answer2 = streamChat(model, chatMemory);
        System.out.println("User: " + userMessage2.singleText());
        System.out.println("AI: " + answer2);
    }

    private static String streamChat(OpenAiStreamingChatModel model, 
                                      ChatMemory chatMemory) throws Exception {
        CompletableFuture<String> future = new CompletableFuture<>();
        
        model.chat(chatMemory.messages(), new StreamingChatResponseHandler() {
            @Override
            public void onPartialResponse(String partial) {
                System.out.print(partial);
            }
            @Override
            public void onCompleteResponse(ChatResponse response) {
                chatMemory.add(response.aiMessage());
                future.complete(response.aiMessage().text());
            }
            @Override
            public void onError(Throwable error) {
                future.completeExceptionally(error);
            }
        });
        
        return future.get();
    }
}
```

第二轮提问时，模型应记得刚才的话题是「数据库查询优化」，继续给出 `@Query` 的例子而不是从零开始。

### 步骤 2：感受截断效果

```java
// 将 maxTokens 从 1000 改成 50
ChatMemory chatMemory = TokenWindowChatMemory.withMaxTokens(
        50, new OpenAiTokenCountEstimator(GPT_4_O_MINI));
```

多轮对话后，观察模型是否「失忆」——答非所问。这说明记忆被截断了。

### 步骤 3：检查 messages 列表

```java
// 打印发给模型前的消息列表（脱敏后用于调试）
chatMemory.messages().forEach(msg -> {
    String role = msg.type().name();
    String text;
    if (msg instanceof UserMessage) {
        text = ((UserMessage) msg).singleText();
    } else if (msg instanceof AiMessage) {
        text = ((AiMessage) msg).text();
    } else if (msg instanceof SystemMessage) {
        text = ((SystemMessage) msg).text();
    } else {
        text = msg.toString();
    }
    System.out.println("[" + role + "] " + 
        (text.length() > 50 ? text.substring(0, 50) + "..." : text));
});
```

### 步骤 4：破坏实验——单例记忆串话

```java
// 这演示了为什么不能单例共享 ChatMemory
ChatMemory sharedMemory = TokenWindowChatMemory.withMaxTokens(
        1000, new OpenAiTokenCountEstimator(GPT_4_O_MINI));

// 模拟两个用户共用同一个 memory
// 用户 A 说「我要退款」
sharedMemory.add(UserMessage.from("I want a refund for order #12345"));
// 用户 B 的对话会看到 A 的消息！
System.out.println(sharedMemory.messages().size());  // != 0！
```

### 闯关任务

| 难度 | 动手 | 过关标准 |
|------|------|----------|
| ★ | maxTokens 从 1000 改 50 跑两轮 | 观察截断后的失忆 |
| ★★ | 超长 system + 短 user | 确认 system 是否被挤掉 |
| ★★★ | 两线程共用同一 memory 实例 | 复现串话 bug |

### 可能遇到的坑

| 坑 | 表现 | 解法 |
|----|------|------|
| 单例 ChatMemory | 用户 A 看到 B 的对话 | 每会话独立实例 |
| 只看条数不看 token | 隐秘超窗 | 用 TokenWindowChatMemory |
| 工具 JSON 撑爆窗口 | 截断后丢了关键约束 | 摘要化或外存 |
| 不检查 messages() | 不知道截断了什么 | 调试时脱敏打印 |

### 测试验证

```java
// 并发场景下每线程独立 memory 实例
// 断言：Thread A 的 memory 不包含 Thread B 的消息
```

### 完整代码清单

[`_05_Memory.java`](../../langchain4j-examples/tutorials/src/main/java/_05_Memory.java)

## 4. 项目总结

### 优点与缺点

| 维度 | Token/Message 窗口 | 全量历史 | 外部摘要服务 |
|------|-------------------|---------|-------------|
| 成本可控 | 高 | 低 | 中 |
| 实现复杂度 | 中 | 低 | 高 |
| 典型缺点 | 估算误差 | 超窗/贵 | 一致性问题 |

### 适用 / 不适用场景

**适用**：多轮客服、迭代澄清、短上下文 + RAG 主流架构。

**不适用**：单轮 FAQ 无需上下文、强依赖精确 token 窗对账（估算不能替代厂商账单）。

### 常见踩坑

1. 单例 ChatMemory 串话
2. 只看条数不看 token 导致隐秘超窗
3. 工具链路与 memory 顺序错误

### 进阶思考题

1. Spring 中 prototype Bean + session 映射如何避免内存 Map 泄漏？
2. 何时引入滚动摘要而非单纯截断？

### 推广计划

| 角色 | 建议阅读顺序 | 协作要点 |
|------|-------------|----------|
| 开发 | 本章 → 第 13 章 | 勿共享 memory 实例 |
| 运维 | 监控 token 与消息条数 | 会话 Map 上限与淘汰 |
| 测试 | 并发 + 截断边界 | 长 system + 长 user |

### 检查清单

- **测试**：并发场景下每线程独立 memory；对截断边界构成长 system + 长 user 用例
- **运维**：监控每次请求消息条数与 token 估算；为会话内存泄漏设上限

### 附录

| 模块 | 说明 |
|------|------|
| `langchain4j-core`、`langchain4j` | `ChatMemory`、`TokenWindowChatMemory`、`MessageWindowChatMemory` |

推荐阅读：`_05_Memory.java`、`ChatMemory`、流式 handler 文档。
