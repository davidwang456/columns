# 第 7 章：流式输出与 Token 流

## 1. 项目背景

### 业务场景（拟真）

产品经理在用户访谈中发现：用户在使用 AI 客服聊天时，点击发送按钮后盯着空白屏幕等待 5-8 秒，然后整段文字突然出现——用户觉得「好慢」。换用流式接口后，同样的 5-8 秒生成时间，用户看到的是 **第一个字在 1 秒内出现，后面的字逐个打出**。用户反馈从「慢」变成了「还可以，它在写」。

这就是流式在体验上的核心价值：**优化首字时间（TTFT, Time To First Token），不一定会缩短总生成时间**。同步接口必须等整段回复全部生成完毕才返回给你；而流式接口通过 `StreamingChatResponseHandler` 在模型生成过程中 **逐个 token/片段推送** 给客户端。

### 痛点放大

同步方式的主要问题是 **线程阻塞**：`ChatModel.chat(String)` 在 Servlet 线程上阻塞等待完整响应。假设一个请求需要 8 秒生成，那这一个线程在这 8 秒内不能做任何其他事。如果并发是 200——200 × 8 = 1600 线程秒的消耗——线程池很快就耗尽。

流式虽然解决了体验问题，但引入了新的复杂度：

- **需要回调处理**：不再是一行代码拿到结果，而是需要实现 `onPartialResponse`（每次收到 token）、`onCompleteResponse`（生成完毕）、`onError`（出错）三个回调。
- **网关可能破坏流式**：Nginx/网关默认会缓冲响应，等上游全部发送完才吐给客户端——这就把流式变成了假流式。
- **测试更难**：同步可以直接 assert 返回的字符串，流式需要收集所有 partial 再断言。

## 2. 项目设计：小胖、小白与大师的对话

**小胖**：流式是不是就是网速更快？像 5G 下载——还没下完就能边看边播，5G 比 4G 快所以不卡？

**大师**：5G 的比喻对了一半。流式改善的 **不是总下载时间，而是「首帧时间」**——也就是第一个字出现在屏幕上的时间。同步模式像等整部电影下完再看（首帧时间=下载总时间），流式像边下边播（首帧时间=第一个数据包到达时间）。但 **总生成时间两者几乎一样**——模型该算多少 token 还是得算多少 token。快的只是用户心理感受：看到第一个字在 1 秒内出现，用户就进入了「等待模式」；如果盯着空白屏幕看 8 秒，用户就会焦虑。

**小白**：那官方示例里的 `CompletableFuture` 是干什么用的？我看到它调用了 `join()` 来等待完成——在真实的 Web 应用里能这么用吗？

**大师**：这是很多初学者会踩的坑。`CompletableFuture` 在教程里的作用只有一个：**把异步回调「同步化」，好让 `main` 方法不立即退出**。你启动了异步调用，主线程如果不等待就直接结束了，那异步回调都没机会执行。所以用 `future.join()` 让主线程等待异步完成——这**只在 `main` 方法里可以这样做**。在生产 Web 应用（Spring MVC / WebFlux）里，**绝对不要在请求线程里调用 `join()`**——这会阻塞 Tomcat/Netty 的线程。正确做法：把流式调用的结果直接绑定到 HTTP 响应流（SSE/WebSocket）上，或者把阻塞操作隔离到专用的异步 worker 线程池。**技术映射**：**onPartialResponse 收到的增量片段仅用于实时展示；所有需要持久化（存数据库、审计、计费）的数据，必须以 onCompleteResponse 里的完整 ChatResponse 为准**。

**小胖**：流到一半报错了咋办？我们的网关（Nginx）那边需不需要配什么？

**大师**：`onError` 是 **必须实现的回调**——你不实现它，出错时用户界面就卡住不动了，没人知道发生了什么。`onError` 里至少要：① 关闭 UI 的加载状态（让用户知道出错了）；② 打点告警记录错误信息。关于网关，流式最怕三个配置问题：**proxy_buffering on**（Nginx 默认开启缓冲——等上游全部发完再一次性吐给客户端，流式就变成了假流式）；**proxy_read_timeout 太短**（模型生成长回答时需要几十秒，网关 10 秒就超时掐断了）；**HTTP/2 流控参数没配**（大流量时可能因为流控限制而断流）。**技术映射**：**流式接口的生产观测三要素：TTFT（首字延迟）、失败率（onError 被触发的比例）、分块数（一次完整响应被分成了多少 chunk）**。

## 3. 项目实战

### 环境准备

```bash
cd langchain4j-examples/tutorials
export OPENAI_API_KEY="sk-your-key-here"
```

### 步骤 1：跑通流式输出

```java
import dev.langchain4j.model.openai.OpenAiStreamingChatModel;
import dev.langchain4j.model.chat.response.ChatResponse;
import dev.langchain4j.model.chat.response.StreamingChatResponseHandler;
import java.util.concurrent.CompletableFuture;

import static dev.langchain4j.model.openai.OpenAiChatModelName.GPT_4_O_MINI;

public class StreamingDemo {

    public static void main(String[] args) {

        OpenAiStreamingChatModel model = OpenAiStreamingChatModel.builder()
                .apiKey(System.getenv("OPENAI_API_KEY"))
                .modelName(GPT_4_O_MINI)
                .build();

        CompletableFuture<ChatResponse> futureResponse = new CompletableFuture<>();

        model.chat("Write a short poem about Java programming", 
            new StreamingChatResponseHandler() {

            @Override
            public void onPartialResponse(String partialResponse) {
                // 每次收到一部分 token 就打印（不换行）
                System.out.print(partialResponse);
            }

            @Override
            public void onCompleteResponse(ChatResponse completeResponse) {
                System.out.println("\n\n[Done streaming]");
                futureResponse.complete(completeResponse);
            }

            @Override
            public void onError(Throwable error) {
                System.err.println("\n[Error]: " + error.getMessage());
                futureResponse.completeExceptionally(error);
            }
        });

        // 等待完成（含 main 线程，生产别这样用）
        futureResponse.join();
    }
}
```

**预期输出**（效果是文字逐个打出）：
```
Java code, a quiet grace,
Typing out in time and space...
[Done streaming]
```

### 步骤 2：统计 chunk 数

```java
// 在类中添加计数器
int[] chunkCount = {0};

@Override
public void onPartialResponse(String partialResponse) {
    chunkCount[0]++;
    System.out.print(partialResponse);
}

// 完成后输出 chunk 数
System.out.println("\nTotal chunks: " + chunkCount[0]);
```

记录同一 prompt 在不同模型下的 chunk 数差异。

### 步骤 3：token 估算

```java
import dev.langchain4j.model.openai.OpenAiTokenCountEstimator;

OpenAiTokenCountEstimator estimator = 
    new OpenAiTokenCountEstimator(GPT_4_O_MINI);

int tokenCount = estimator.estimateTokenCountInText(
    "Write a short poem about Java programming");
System.out.println("Estimated input tokens: " + tokenCount);
```

**预期输出**：`Estimated input tokens: 8`

### 步骤 4：破坏实验——流中报错

```java
// 用不存在的模型名触发流式错误
.modelName("gpt-9999")
```

预期 `onError` 被调用，输出：
```
[Error]: statusCode: 404 Not Found
```

确认 `onCompleteResponse` **没有被调用**——这就是为什么 `onError` 必须处理。

### 闯关任务

| 难度 | 动手 | 过关标准 |
|------|------|----------|
| ★ | `onPartialResponse` 里 `count++` | 知道一次回答分了多少块 |
| ★★ | 超长 prompt 对比估算 token 与延迟 | 建立代价直觉 |
| ★★★ | 用 `curl -N` 调 SSE 对比缓冲 vs 真流 | 理解网关缓冲的影响 |

```bash
# 用 curl 测试原始 SSE 流式响应
curl -N https://api.openai.com/v1/chat/completions \
  -H "Authorization: Bearer $OPENAI_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-4o-mini",
    "stream": true,
    "messages": [{"role": "user", "content": "Count from 1 to 5"}]
  }'
```

对比是否每个 chunk 即时出现，还是攒了一批才显示。

### 可能遇到的坑

| 坑 | 表现 | 解法 |
|----|------|------|
| 未处理 `onError` | 静默失败，用户看到空白 | 总是实现 `onError` |
| 网关默认缓冲 | 服务端流式、网关攒够才吐 = 假流式 | 配 Nginx `proxy_buffering off` |
| 用 partial 拼接做审计 | 编码边界在 chunk 中间断裂 | 以 `onCompleteResponse` 的全文为准 |
| 在请求线程 `join()` | 拖垮吞吐 | 隔离到 async worker |

### 测试验证

- 契约测试：`error` 路径有用户可见反馈
- 负载测试：关注首包时间 P95 与完整响应成功率

### 完整代码清单

[`_04_Streaming.java`](../../langchain4j-examples/tutorials/src/main/java/_04_Streaming.java)

## 4. 项目总结

### 优点与缺点

| 维度 | Streaming + Handler | 同步 chat | SSE 手写 HTTP |
|------|--------------------|-----------|--------------|
| TTFT 体验 | 优 | 差 | 视实现 |
| 复杂度 | 中 | 低 | 高 |
| 测试 | 需上下界 | 易 | 中 |

### 适用 / 不适用场景

**适用**：聊天 UI、实时写作、长报告草稿、边生成边展示引用。

**不适用**：批处理离线生成（同步更简单）、强依赖严格逐 token 断句（chunk 边界非语义单元）。

### 常见踩坑

1. 未处理 `onError` → 静默失败
2. 网关默认缓冲 → 假流式
3. 在响应式线程阻塞 `join()` → 拖垮吞吐

### 进阶思考题

1. 如何用 `ChatResponse` 统一拿到 usage，与流式 partial 对齐计费？
2. Reactor Netty 上调用阻塞 `ChatModel` 的推荐隔离模式？

### 推广计划

| 角色 | 建议阅读顺序 | 协作要点 |
|------|-------------|----------|
| 开发 | 本章 → 第 34 章 | 禁止在请求线程错误阻塞 |
| 运维 | 本章 + 网关 | SSE 超时、限流、断开日志 |
| 测试 | 本章 | error 路径契约 + 分块上下界 |

### 检查清单

- **测试**：契约测试验证 error 路径有用户可见反馈；负载测试关注首包时间 P95
- **运维**：为流式路由单独配置超时、限流与断开连接日志

### 附录

| 模块 | 说明 |
|------|------|
| `langchain4j-open-ai` | `OpenAiStreamingChatModel` |
| `langchain4j-core` | `StreamingChatResponseHandler`、`ChatResponse` |

推荐阅读：`_04_Streaming.java`、`StreamingAssistant.java`（Spring 示例）。
