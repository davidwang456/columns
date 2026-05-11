# 第35章：Event 系统与 SSE 流式传输源码

## 1. 项目背景

浏览器中 AI 一字一字"打出来"的效果——背后是 Dify 的 **Event 系统** + **SSE（Server-Sent Events）协议**在协作。

**Event 系统**定义了 20+ 种事件类型：`workflow_started`（Workflow 开始了）、`node_started`（某个节点开始执行）、`llm_chunk`（LLM 输出的一个 Token）、`agent_thought`（Agent 的思考步骤）、`message_end`（这条消息结束了）……每种事件携带不同的数据，前端根据事件类型决定 UI 怎么变。

**SSE** 是一种基于 HTTP 长连接的单向推送协议。服务端用 `Content-Type: text/event-stream` 响应头，然后逐行发送 `data: {JSON}\n\n`。前端用 `ReadableStream` + `TextDecoder` 逐行解析。SSE 比 WebSocket 更简单（纯 HTTP、自动重连），但只支持单向通信。

理解这套系统有三个实际价值：**自定义前端展示**（如添加"正在检索知识库…"的进度提示）；**扩展事件类型**（添加自定义埋点事件上报分析系统）；**排查流式响应中断**（SSE 断开后如何恢复？Nginx 缓冲了 SSE 怎么办？）。

## 2. 项目设计——剧本式交锋对话

**小胖**："大师，前端怎么知道 AI 正在'思考'还是'打字'？这两个状态切换很丝滑，是前端硬编码的吗？"

**大师**："不是硬编码。后端发的每个 SSE 事件都有 `event` 字段。前端收到 `event: "node_started"` → 显示'正在分析……'；收到 `event: "llm_chunk"` → 把 chunk 追加到回复文字后面（打字效果）；收到 `event: "message_end"` → 停止加载动画、显示完成状态。整个状态切换由事件驱动。"

**小白**："为什么 Nginx 要配 `proy_buffering off`？"

**大师**："Nginx 默认会对后端响应做缓冲——等攒够 4KB 或 8KB 再一次发给客户端。这对普通 HTTP 没问题（减少网络包数量），但对 SSE 是灾难——用户会看到 AI '突然弹出一大段文字'，而不是逐字打字。`proy_buffering off` 告诉 Nginx：'别缓冲，收到什么立刻发什么'。还要加 `X-Accel-Buffering: no` 响应头——这是告诉 Nginx 的 FastCGI 模块也别缓冲。"

**小胖**："SSE 断了怎么办？会自动重连吗？"

**大师**："浏览器的 `EventSource` API 会自动重连（默认 3 秒后重试），但 Dify 当前用的是手写的 `fetch + ReadableStream` 而非 `EventSource`——所以需要自己在代码里实现重连逻辑。并且 SSE 不支持断点续传——连接断后重新发请求，会新建一个 conversation 或从头开始。这是 SSE 方案的固有局限。如果需要更可靠的实时通信，用 WebSocket（Socket.IO）。"

## 3. 项目实战

### Event 类型完整体系

```python
class WorkflowEventType:
    # === Workflow 级别 ===
    WORKFLOW_STARTED = "workflow_started"
    WORKFLOW_FINISHED = "workflow_finished"
    WORKFLOW_FAILED = "workflow_failed"
    
    # === 节点级别 ===
    NODE_STARTED = "node_started"      # 节点开始执行
    NODE_FINISHED = "node_finished"    # 节点执行成功
    NODE_FAILED = "node_failed"        # 节点执行失败
    
    # === LLM 特有 ===
    LLM_CHUNK = "llm_chunk"            # 每个 Token（打字效果的核心）
    LLM_USAGE = "llm_usage"            # Token 消耗统计
    
    # === Agent 特有 ===
    AGENT_THOUGHT = "agent_thought"    # Agent 的思考步骤
    AGENT_ACTION = "agent_action"      # Agent 调用工具
    AGENT_OBSERVATION = "agent_observation"  # 工具执行结果
    
    # === 消息级别 ===
    MESSAGE = "message"                # 消息内容块
    MESSAGE_END = "message_end"        # 消息结束（带 message_id 和 conversation_id）
    MESSAGE_REPLACE = "message_replace"  # 替换之前的消息
    
    # === 错误 ===
    ERROR = "error"
```

### SSE 服务端实现

```python
# api/controllers/web/completion.py（简化）
from flask import Response, stream_with_context
import json

def generate_sse_response(generator):
    """
    将 Python Generator 包装为 SSE 响应
    
    Generator 中的每个 event 对象会序列化为 JSON，
    以 data: {JSON}\n\n 格式写入响应流。
    """
    def generate():
        for event in generator:
            # 核心格式：data: {JSON}\n\n
            event_json = json.dumps(event.to_dict(), ensure_ascii=False)
            yield f"data: {event_json}\n\n"
        
        # 结束标记（可选）
        yield "data: [DONE]\n\n"
    
    return Response(
        stream_with_context(generate()),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',           # 不缓存
            'X-Accel-Buffering': 'no',              # ★ Nginx 不缓冲
            'Connection': 'keep-alive',             # 保持连接
            'Access-Control-Allow-Origin': '*',
        }
    )
```

### 前端 SSE 消费

```typescript
// web/app/components/share/chat/hooks/use-chat.ts（简化核心逻辑）
async function handleStreamResponse(response: Response) {
    const reader = response.body?.getReader();
    if (!reader) return;
    
    const decoder = new TextDecoder();
    let buffer = '';
    
    while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        
        // 解码新收到的字节
        buffer += decoder.decode(value, { stream: true });
        
        // 按行分割
        const lines = buffer.split('\n');
        // 最后一行可能是不完整的——保留到下次
        buffer = lines.pop() || '';
        
        for (const line of lines) {
            if (line.startsWith('data: ')) {
                const eventData = JSON.parse(line.slice(6));
                
                // ★ 根据事件类型更新 UI 状态
                switch (eventData.event) {
                    case 'message':
                    case 'llm_chunk':
                        // 打字效果：追加到已有回答
                        setAnswer(prev => prev + (eventData.answer || eventData.text || ''));
                        break;
                    
                    case 'node_started':
                        // 显示"正在执行：xxx节点"
                        setCurrentStep(`正在执行: ${eventData.data?.title || '...'}`);
                        break;
                    
                    case 'message_end':
                        // 消息完成
                        setConversationId(eventData.conversation_id);
                        setMessageId(eventData.message_id);
                        setIsLoading(false);
                        break;
                    
                    case 'workflow_finished':
                        // Workflow 执行完成
                        setOutputs(eventData.data?.outputs || {});
                        break;
                    
                    case 'error':
                        setError(eventData.message || '未知错误');
                        setIsLoading(false);
                        break;
                }
            }
        }
    }
}
```

### 自定义事件——添加阶段进度

```python
# 在 Workflow 引擎中添加自定义进度事件
class StageProgressEvent:
    """自定义 Workflow 进度事件"""
    def __init__(self, stage: str, progress: int, message: str):
        self.event = "stage_progress"
        self.data = {
            "stage": stage,           # 如 "retrieval" / "generation" / "formatting"
            "progress": progress,     # 0-100
            "message": message,       # "正在检索知识库..."
        }

# 在 GraphEngine.run() 中推送
# 执行知识库检索前：
yield StageProgressEvent("retrieval", 0, "开始检索知识库...")
# 检索完成后：
yield StageProgressEvent("retrieval", 100, "知识库检索完成（3 条结果）")
# 开始 LLM 生成：
yield StageProgressEvent("generation", 0, "正在生成回复...")
# 生成 50% 时：
yield StageProgressEvent("generation", 50, "已生成 50%...")
```

### 测试验证

```bash
# 观察原始 SSE 数据流
curl -N -X POST http://localhost/v1/chat-messages \
  -H "Authorization: Bearer app-xxx" \
  -H "Content-Type: application/json" \
  -d '{"query":"写一首五言绝句","user":"test","response_mode":"streaming"}'

# 预期输出：
# data: {"event":"workflow_started","workflow_run_id":"xxx","data":{...}}
# data: {"event":"node_started","data":{"node_id":"123","node_type":"start",...}}
# data: {"event":"node_finished","data":{"node_id":"123","outputs":{...}}}
# data: {"event":"node_started","data":{"node_id":"456","node_type":"llm",...}}
# data: {"event":"message","answer":"春"}
# data: {"event":"message","answer":"眠"}
# data: {"event":"message","answer":"不"}
# ...（逐 Token 推送）
# data: {"event":"message_end","conversation_id":"...","message_id":"..."}
```

## 4. 项目总结

| 层级 | 技术 | 关键文件 | 职责 |
|------|------|---------|------|
| Event 定义 | Python Class | `core/workflow/node_events/` | 事件数据结构 |
| SSE 服务端 | Flask Generator | `controllers/web/completion.py` | `data: JSON\n\n` |
| SSE 前端 | ReadableStream | `web/.../hooks/use-chat.ts` | 逐行解析 + UI 更新 |
| WebSocket 替代 | Socket.IO | `extensions/ext_socketio.py` | 双向通信（Event 体系中可选） |

### SSE vs WebSocket

| 维度 | SSE | WebSocket |
|------|-----|-----------|
| 通信方向 | 单向（服务端→客户端） | 双向 |
| 协议 | HTTP | WS（升级自 HTTP） |
| 浏览器支持 | `EventSource` API | `WebSocket` API |
| 自动重连 | `EventSource` 内置 | 需手写 |
| Nginx 配置 | 需 `proxy_buffering off` | 需 `Upgrade` 头支持 |

**思考题**：
1. SSE 是单向通道。用户点击"停止生成"按钮时，如何告知服务端？（提示：用另一个 HTTP POST 请求或切换到 WebSocket）
2. SSE 断开后如何实现断点续传——用户重连后不丢失已生成的内容？（提示：服务端缓存已生成的 Token 历史，重连时从断点继续推送）

> **参考答案**：见附录 D
