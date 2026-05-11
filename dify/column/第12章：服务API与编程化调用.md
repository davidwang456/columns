# 第12章：服务 API 与编程化调用

## 1. 项目背景

"Dify 的控制台很好用，但我们的业务系统是用 Python 写的后台服务，不能每次都需要人登录网页手动操作。"这是企业集成 Dify 时的典型诉求。Dify 不是只给运营人员点鼠标用的——它提供了完整的 REST API，让你可以通过代码创建 App、管理知识库、发起对话、执行 Workflow。

Dify 的 API 体系分为三大类：
- **Console API**（`/console/api/*`）：给管理后台用的，需要 Session 登录态，用于创建 App、配置 Provider 等管理操作。
- **Web API**（`/api/*`）：给 WebApp 前端用的，用于普通用户的对话交互。
- **Service API**（`/v1/*`）：给开发者编程调用的，使用 API Key 鉴权，这是本章的重点。

Service API 的设计相当完善——支持 Chat、Completion、Workflow 三种运行模式，支持流式（streaming）和阻塞式（blocking）两种响应方式，支持文件上传、会话管理、知识库查询。有了它，你可以把 Dify 当做一个"LLM 能力中台"——内部所有的业务系统（客服、营销、数据分析）都通过 API 调用 Dify，统一管理模型成本、监控质量。

本章将带你通过 Python 代码系统地掌握 Service API 的核心用法，从简单的单次调用到复杂的批量处理和文件上传，并最终用一套完整的脚本实现"批量处理 100 条数据并导出 CSV"的实战场景。

## 2. 项目设计——剧本式交锋对话

**小胖**：（拿着一份需求文档）"大师，产品经理让我把 Dify 集成到我们的后台系统里——用户在前端点一下按钮，后台自动调 Dify 生成分析报告。可是我找了半天，Dify 的 API 文档在哪里？"

**大师**："登录 Dify 控制台后，右上角头像 → **API 密钥**，创建 Key 之后点击'API 参考'就能看到 Swagger 文档。不过我要提醒你：Dify 的 API 分三种，你需要的 Service API 路径是 `/v1/*`，鉴权方式是用 `Authorization: Bearer app-xxx`。"

**技术映射**：Service API（`/v1/*`）= 面向开发者的编程接口，Console API（`/console/api/*`）= 面向管理后台的接口。

**小白**："流式和阻塞式有什么区别？什么时候用哪个？"

**大师**：
- **阻塞式（blocking）**：发送请求后，等服务端完整生成回复后一次性返回。适合后台批处理任务——发 100 条数据给 Dify，等全部处理完拿到结果写数据库。延迟 = 生成时间，但代码简单。
- **流式（streaming）**：发送请求后，服务端通过 SSE（Server-Sent Events）持续推送生成的文本片段。适合前端聊天界面——用户可以实时看到 AI"打字"的过程，体验更好。延迟 = Token 级，但代码需要处理 Event Stream。"

**小胖**："流式响应怎么解析？SSE 那格式我从来没见过。"

**大师**："SSE 本质上是文本流，每行一个事件。Dify 发送的格式像这样：

```
data: {"event": "workflow_started", "workflow_run_id": "xxx"}
data: {"event": "node_started", "data": {"node_id": "123", "node_type": "llm"}}
data: {"event": "message", "answer": "你好"}
data: {"event": "message", "answer": "，我是"}
data: {"event": "message", "answer": "AI 助手"}
data: {"event": "message_end", "conversation_id": "yyy", "message_id": "zzz"}
```

每一行 `data:` 后面是一个 JSON 事件。你要做的就是监听这些事件，根据 `event` 类型做相应处理。"

**技术映射**：SSE（Server-Sent Events）= HTTP 长连接 + `text/event-stream` Content-Type + `data:` 行前缀。

**小白**："那如果要上传文件呢？比如上传一张图片让 AI 分析。"

**大师**："分两步：先调文件上传 API 拿到 file_id，再把 file_id 作为输入参数传给 Chat/Workflow API。Dify 支持的文件类型包括图片、文档、音视频。文件上传后存储在配置的存储后端（本地/S3/OSS）上。"

**小胖**："API Key 的管理呢？我怕泄露了怎么办？"

**大师**："三个建议：
1. 每个业务系统用不同的 API Key，不要一把 Key 走天下
2. Key 存到环境变量或密钥管理服务（如 Vault），不要硬编码在代码里
3. 定期轮换 Key——在 Dify 控制台删掉旧的，创建新的，更新业务系统配置"

## 3. 项目实战

### 分步实现

#### 步骤1：创建 API Key 并测试连通性（目标：获取编程访问凭证）

1. Dify 控制台 → 右上角头像 → **API 密钥** → **创建密钥**
2. 复制生成的 Key（形如 `app-xxxxxxxxxxxxx`）
3. 测试连通性：

```python
import requests

API_BASE = "http://localhost/v1"
API_KEY = "app-xxxxxxxxxxxxx"

resp = requests.get(f"{API_BASE}/parameters",
    headers={"Authorization": f"Bearer {API_KEY}"}
)

if resp.status_code == 200:
    params = resp.json()
    print("API 连接成功！")
    print(f"应用名称: {params.get('suggested_questions', [])}")
    print(f"开场白: {params.get('opening_statement', '无')}")
else:
    print(f"连接失败: {resp.status_code} - {resp.text}")
```

**这个接口不需要发送消息**，返回的是 App 的公共配置信息（开场白、建议问题、输入变量等），可以用来做前置校验。

#### 步骤2：阻塞式对话调用（目标：最简单的 API 交互）

```python
def send_message_blocking(query, user="test-user", conversation_id=""):
    """阻塞式发送消息，等待完整回复后返回"""
    resp = requests.post(
        f"{API_BASE}/chat-messages",
        headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
        json={
            "inputs": {},           # 输入变量（对应 App 里定义的自定义变量）
            "query": query,         # 用户消息
            "user": user,           # 用户标识（用于会话隔离）
            "response_mode": "blocking",
            "conversation_id": conversation_id,  # 留空则创建新会话
        },
        timeout=120  # 长时间生成可能需要较大超时
    )
    data = resp.json()
    return {
        "answer": data.get("answer", ""),
        "conversation_id": data.get("conversation_id", ""),
        "message_id": data.get("message_id", ""),
        "tokens": data.get("metadata", {}).get("usage", {}).get("total_tokens", 0),
    }

# 测试
result = send_message_blocking("你好，请用一句话介绍你自己")
print(f"回复: {result['answer']}")
print(f"消耗 Token: {result['tokens']}")
```

#### 步骤3：流式对话调用（目标：实时展示 AI 打字过程）

```python
import json

def send_message_streaming(query, user="test-user", conversation_id=""):
    """流式发送消息，逐 Token 打印回复"""
    resp = requests.post(
        f"{API_BASE}/chat-messages",
        headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
        json={
            "inputs": {},
            "query": query,
            "user": user,
            "response_mode": "streaming",
            "conversation_id": conversation_id,
        },
        stream=True,
        timeout=120
    )
    
    conversation_id = ""
    full_answer = ""
    
    for line in resp.iter_lines():
        if not line:
            continue
        
        line_str = line.decode("utf-8")
        if not line_str.startswith("data: "):
            continue
        
        # 解析 SSE 事件
        event_data = json.loads(line_str[6:])  # 去掉 "data: " 前缀
        event_type = event_data.get("event", "")
        
        if event_type == "message":
            chunk = event_data.get("answer", "")
            print(chunk, end="", flush=True)
            full_answer += chunk
        
        elif event_type == "message_end":
            conversation_id = event_data.get("conversation_id", "")
            metadata = event_data.get("metadata", {})
            print(f"\n\n[Token: {metadata.get('usage', {}).get('total_tokens', 'N/A')}]")
        
        elif event_type == "error":
            print(f"\n错误: {event_data.get('message', '未知错误')}")
            break
    
    return {"answer": full_answer, "conversation_id": conversation_id}

# 测试
send_message_streaming("介绍 Dify 的三个核心功能")
```

#### 步骤4：调用 Workflow API（目标：程序化执行 Workflow）

```python
def run_workflow(inputs, user="test-user", streaming=True):
    """执行 Workflow，返回结果"""
    resp = requests.post(
        f"{API_BASE}/workflows/run",
        headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
        json={
            "inputs": inputs,      # 对应 Workflow 开始节点的输入变量
            "user": user,
            "response_mode": "streaming" if streaming else "blocking",
        },
        stream=streaming,
        timeout=300  # Workflow 可能耗时较长
    )
    
    if streaming:
        outputs = {}
        for line in resp.iter_lines():
            if not line:
                continue
            line_str = line.decode("utf-8")
            if line_str.startswith("data: "):
                event = json.loads(line_str[6:])
                
                if event.get("event") == "workflow_finished":
                    outputs = event.get("data", {}).get("outputs", {})
                    print(f"\nWorkflow 完成！输出: {outputs}")
        
        return outputs
    else:
        data = resp.json()
        return data.get("data", {}).get("outputs", {})

# 测试 Workflow
result = run_workflow({"query": "OpenAI 发布 GPT-5", "category": "AI 新闻"})
print(result)
```

#### 步骤5：文件上传 API（目标：上传文件供 App 使用）

```python
def upload_file(file_path, user="test-user"):
    """上传文件到 Dify，返回 file_id"""
    with open(file_path, "rb") as f:
        resp = requests.post(
            f"{API_BASE}/files/upload",
            headers={"Authorization": f"Bearer {API_KEY}"},
            files={"file": (os.path.basename(file_path), f)},
            data={"user": user}
        )
    
    if resp.status_code == 200:
        file_id = resp.json().get("id")
        print(f"文件上传成功: {file_id}")
        return file_id
    else:
        print(f"上传失败: {resp.text}")
        return None

# 在对话中引用文件
def send_message_with_file(query, file_id, user="test-user"):
    """发送带文件引用的消息"""
    resp = requests.post(
        f"{API_BASE}/chat-messages",
        headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
        json={
            "query": query,
            "user": user,
            "response_mode": "blocking",
            "files": [{"type": "image", "transfer_method": "local_file", "upload_file_id": file_id}]
        }
    )
    return resp.json().get("answer", "")
```

#### 步骤6：综合实战——批量处理 100 条数据并导出 CSV（目标：完整业务集成）

```python
import csv
import time

def batch_process_workflow(input_csv_path, output_csv_path):
    """
    批量处理：读取 CSV → 逐行调用 Workflow → 写入结果 CSV
    input_csv 格式：id,content
    output_csv 格式：id,content,result,status
    """
    results = []
    
    with open(input_csv_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    
    total = len(rows)
    print(f"开始批量处理 {total} 条数据...")
    
    for i, row in enumerate(rows):
        try:
            result = run_workflow(
                inputs={"query": row["content"]},
                user=f"batch-{row['id']}",
                streaming=False
            )
            
            results.append({
                "id": row["id"],
                "content": row["content"],
                "result": result.get("text", ""),
                "status": "success"
            })
            print(f"[{i+1}/{total}] 完成: {row['id']}")
            
        except Exception as e:
            results.append({
                "id": row["id"],
                "content": row["content"],
                "result": str(e),
                "status": "failed"
            })
            print(f"[{i+1}/{total}] 失败: {row['id']} - {e}")
        
        # 避免触发限流
        time.sleep(0.5)
    
    # 写入结果 CSV
    with open(output_csv_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["id", "content", "result", "status"])
        writer.writeheader()
        writer.writerows(results)
    
    success_count = sum(1 for r in results if r["status"] == "success")
    print(f"\n批处理完成: {success_count}/{total} 成功")
    print(f"结果已保存至: {output_csv_path}")

# 使用示例
# batch_process_workflow("input.csv", "output.csv")
```

### 测试验证

```bash
# 测试 1：验证 API Key 可用
curl http://localhost/v1/parameters \
  -H "Authorization: Bearer app-xxx"
# 预期：返回 App 的配置信息 JSON

# 测试 2：阻塞式对话
curl -X POST http://localhost/v1/chat-messages \
  -H "Authorization: Bearer app-xxx" \
  -H "Content-Type: application/json" \
  -d '{"query":"Hello","user":"test","response_mode":"blocking"}'
# 预期：返回 JSON，包含 answer 和 conversation_id

# 测试 3：流式对话（观察 SSE 事件）
curl -N -X POST http://localhost/v1/chat-messages \
  -H "Authorization: Bearer app-xxx" \
  -H "Content-Type: application/json" \
  -d '{"query":"写一首五言绝句","user":"test","response_mode":"streaming"}'
# 预期：逐行输出 SSE 事件，data:{"event":"message","answer":"..."}
```

## 4. 项目总结

### 优点与缺点

| 维度 | 优点 | 缺点 |
|------|------|------|
| **API 设计** | RESTful 风格，统一的 `Bearer` 鉴权，流式/阻塞双模式 | Workflow API 和 Chat API 路径不同，概念上不够统一 |
| **流式支持** | SSE 标准协议，事件类型丰富（message/node_started/workflow_finished） | 流式断开后无法恢复，需要重新发起请求 |
| **文件上传** | 支持多格式文件，上传后 file_id 跨请求引用 | 大文件上传（>100MB）可能超时，缺少分片上传 |
| **错误处理** | 流式模式有 error 事件，阻塞式返回标准 HTTP 错误码 | 错误信息有时不够具体（比如模型调用失败只说"内部错误"） |

### 适用场景

| 场景 | 推荐 API 模式 |
|------|------------|
| **前端聊天界面** | 流式 Chat API（SSE → 实时渲染） |
| **后台批处理** | 阻塞式 Workflow API（逐条处理 + 结果汇总） |
| **定时报告生成** | Cron Job → 阻塞式 Chat API → 结果写数据库 |
| **微服务集成** | 阻塞式 Workflow API（同步调用，超时控制） |
| **数据分析管道** | Workflow API batch 模式（可配合 K6 压测优化吞吐） |

### 注意事项

1. **超时设置**：Workflow API 可能需要 30-120 秒，务必根据场景设置合理的 HTTP timeout
2. **并发限制**：短时间内大量请求可能触发 Dify 的内部限流，建议控制 QPS 并实现重试逻辑
3. **API Key 保存**：使用环境变量存储 Key：`import os; API_KEY = os.getenv("DIFY_API_KEY")`

### 常见踩坑经验

1. **坑：流式响应解析时中文乱码** → 根因：`iter_lines()` 默认不处理编码，中文字符跨 chunk 被截断。解决：使用 `iter_content(chunk_size=None)` 配合手动按行分割
2. **坑：Workflow 调用返回 400 "input required"** → 根因：缺少 Workflow 开始节点定义的必填变量。解决：在 Dify 控制台查看 Workflow 的"开始"节点的变量列表
3. **坑：API Key 泄露导致额度被盗刷** → 根因：Key 被提交到公开的 GitHub 仓库。解决：立即在 Dify 控制台删除该 Key，使用 `git-secrets` 或 pre-commit hook 检测 Key 泄漏

### 思考题

1. **进阶题**：Dify 的 Service API 目前没有原生支持"并发请求同一个 conversation_id"，如果你需要实现并发对话，你会如何设计？（提示：考虑请求队列和 conversation 的线程安全）

2. **进阶题**：如果你需要实现"请求日志追踪"——为每个 API 请求关联一个 trace_id 并在所有后端日志中可查询，你会利用 Dify 的什么机制？（提示：OpenTelemetry 集成）

> **参考答案**：见附录 D
