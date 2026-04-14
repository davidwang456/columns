# 第 7 章：流式输出与 Token 流

## 1. 项目背景

交互式产品里，**首字时间（TTFT）**往往比**总耗时**更影响体验：用户宁愿看到文字逐字跳出，也不愿盯着一个转圈等待整段生成。同步 `ChatModel.chat(String)` 必须等模型生成完才返回，而 **流式 API** 则通过 `StreamingChatResponseHandler`（或 WebFlux `Flux` 等适配）把 **partial response** 推给调用方。与此同时，**估算 token** 有助于预估费用与是否将超过上下文窗口。

教程 `_04_Streaming.java`（`langchain4j-examples/tutorials/src/main/java/_04_Streaming.java`）演示 `OpenAiStreamingChatModel` + `onPartialResponse` + `onCompleteResponse`，是理解「流式生命周期」的第一块拼图；与 Spring 示例里 `/streamingAssistant` 的 `Flux` 组合起来，就形成「控制台 → Web SSE」的完整故事。

## 2. 项目设计：大师与小白的对话

**小白**：流式是不是一定更快？

**大师**：**总时间**往往相近；快的是 **感知**。后端仍要等模型算完所有 token，只是**边算边推**。

**小白**：`CompletableFuture` 在这里干什么？

**大师**：把 **异步回调风格** 转成主线程可 **`join()`** 的同步出口，便于教程 main 一行跑完。Web 应用里通常不需要 join，而是 **回调到 SSE/WS**。

**小白**：部分响应要拼接吗？

**大师**：`onPartialResponse` 往往是 **增量片段**（具体语义以 provider 为准）；展示端通常 **直接 print**；若要做完整持久化，需要 **自己 append** 或通过 `ChatResponse` 获取最终 `AiMessage`。

**小白**：出错时流一半停了怎么办？

**大师**：`onError` 要能 **关闭 UI、补偿日志、打点**；不要让用户无限等。可尝试 **有限次重试** 或提示「请缩短问题」。

**小白**：网关对 SSE 有什么问题？

**大师**：**超时**、**缓冲**、**HTTP/2 兼容性**。运维要在 **反向代理** 上单独调 `proxy_read_timeout` 等（第 34 章场景）。

**小白**：为何要打印 token 估计？

**大师**：帮助建立 **代价直觉**：长 prompt 在流式前后都耗钱；也利于排查「是不是提示写太长」。

**小白**：测试流式怎么断言？

**大师**：收集 **完整响应** 与 **分块次数**；对分块次数只设上下界，不对每块内容逐字快照（非确定性）。

**小白**：阻塞线程池风险？

**大师**：在 Servlet 线程模型里若错误阻塞，会拖垮吞吐量；**响应式栈**要把阻塞模型调用隔离到 worker。

## 3. 项目实战：主代码片段

> **场景入戏**：同步 `chat` 像**等整集 Netflix 缓冲完再看**；流式像**边下边播**——用户爽的是**首字时间（TTFT）**，你烦的是 **`onError` 从哪冒出来** 和 **网关把 chunked 响应缓冲没了**。

```java
OpenAiStreamingChatModel model = OpenAiStreamingChatModel.builder()
        .apiKey(ApiKeys.OPENAI_API_KEY)
        .modelName(GPT_4_O_MINI)
        .build();

CompletableFuture<ChatResponse> futureChatResponse = new CompletableFuture<>();

model.chat(prompt, new StreamingChatResponseHandler() {

    @Override
    public void onPartialResponse(String partialResponse) {
        System.out.print(partialResponse);
    }

    @Override
    public void onCompleteResponse(ChatResponse completeResponse) {
        System.out.println("\n\nDone streaming");
        futureChatResponse.complete(completeResponse);
    }

    @Override
    public void onError(Throwable error) {
        futureChatResponse.completeExceptionally(error);
    }
});

futureChatResponse.join();
```

示例亦演示「**估算 prompt token**」（对费用直觉极有用）：

```java
new OpenAiTokenCountEstimator(GPT_4_O_MINI).estimateTokenCountInText(prompt)
```

**仓库锚点**：[`_04_Streaming.java`](../../langchain4j-examples/tutorials/src/main/java/_04_Streaming.java)。

#### 闯关任务

| 难度 | 动手 | 你会学到 |
|------|------|----------|
| ★ | 在 `onPartialResponse` 里**数 chunk 次数**（简单 `count++`） | **流**不是文字均匀切分，chunk 边界**不可当自然语言边界** |
| ★★ | 故意传入 **超长 prompt**，对比 **估算 token** 与 **体感延迟** | **输入先烧钱**——长提示是隐形刺客 |
| ★★★ | 打开 `spring-boot-example` 的 `StreamingAssistant` + `AssistantController`，用 `curl -N` 调 SSE | **网关缓冲** vs **真流**：经典运维坑 |

#### 挖深一层

- **`CompletableFuture.join()`**：教程为了 **main 一行结束**；Web 里通常 **绝不 join 在请求线程**——交给异步或 Reactor。  
- **背压**：若 `onPartialResponse` 里又写 DB，可能 **比模型吐字还慢** → 需队列或丢弃策略。  
- **完整答案**：持久化应以 **`onCompleteResponse`** 的 `ChatResponse` 为准，而非自己拼接 partial（防编码边界错乱）。

## 4. 项目总结

### 优点

- **体验显著提升**；利于长答案场景。  
- **handler** 模式边界清晰：partial / complete / error。

### 缺点

- 客户端与服务端 **实现复杂度** 上升。  
- **非确定性** 使部分集成测试更脆弱。

### 适用场景

- 聊天 UI、实时写作辅助、长报告草稿。  
- 需要 **边生成边展示引用/来源** 的产品（配合 RAG 溯源）。

### 注意事项

- **背压**：高流量下注意下游消费能力。  
- **完整性与审计**：流式结束后仍要保存 **最终全文** 到数据库。

### 常见踩坑

1. **未处理 onError** 导致静默失败。  
2. **网关默认缓冲** 使「流式」变「一次性」。  
3. 在 **响应式线程** 上阻塞 `join()`。

---

### 本期给测试 / 运维的检查清单

**测试**：契约测试验证 **error 路径** 有用户可见反馈；负载测试关注 **首包时间 P95** 与 **完整响应成功率**。  
**运维**：为 **SSE/流式路由** 单独配置超时、限流与 **断开连接日志**；监控 **中途失败率** 与厂商侧 5xx 关联。

### 附录：相关 Maven 模块与源码类

| 模块 | 说明 |
|------|------|
| `langchain4j-open-ai` | `OpenAiStreamingChatModel` |
| `langchain4j-core` | `StreamingChatResponseHandler`、`ChatResponse` |

推荐阅读：`_04_Streaming.java`、`StreamingAssistant.java`（Spring 示例）、`AssistantController` 流式路由。
