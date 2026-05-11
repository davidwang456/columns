# 第23章：LLM 节点与模型调度机制深度解析

## 1. 项目背景

Workflow 中使用频率最高的节点是什么？LLM 节点。但它不是"把 Prompt 拼好一发了事"——背后有三层调度，每一层都可能出问题，排查效率取决于你对这三层的理解深度。

**第一层：Prompt 模板渲染**（Jinja2）。你在 LLM 节点的输入框中写了 `{{#start.query#}}` 和 `{{#context#}}`。Dify 在调用 LLM 之前，必须先用变量池中的实际值替换这些占位符。如果变量不存在、类型不匹配、或者嵌套引用循环——渲染失败就是"变量未找到"错误的根源。

**第二层：模型实例获取**（ModelManager）。Dify 需要根据租户 ID + Provider 类型 + 模型名称，找到匹配的 API 凭据。如果配了 3 个 OpenAI Key，ModelManager 还要决定用哪一个——这就是负载均衡（Round Robin）+ 故障转移（Cooldown）的调度逻辑。选不到可用凭据就报 `NoAvailableModelError`。

**第三层：LLM 调用执行**。不同的 Provider 调用方式不同——OpenAI 走 `httpx.post("https://api.openai.com/v1/chat/completions")`，Ollama 走 `httpx.post("http://ollama:11434/api/chat")`，插件模型走 gRPC。超时、限流、认证失败——每种错误的处理策略不同。

本章的核心价值：帮你建立**精确的三层排查能力**——"模型调不通"是 Prompt 问题、Provider 配置问题、还是网络问题？"Key 分配不均"是负载均衡 bug 还是冷却策略正常行为？"调用突然变慢"是网络延迟、模型排队、还是 Token 限制？

## 2. 项目设计——剧本式交锋对话

**小胖**：（看着控制台日志）"大师！我配了 3 个 OpenAI Key，但日志显示 Key 3 被用了 800 次，Key 1 才用了 200 次。说好的 Round Robin 平均分配呢？这是不是 bug？"

**大师**："结论下早了。你看到的不是'分配不均'，而是**冷却机制的副作用**。当 Key 1 第一次触发 429 Too Many Requests 时，Dify 在 Redis 里给它打了一个 60 秒的冷却标记。在这 60 秒内，所有凭据选择请求都跳过 Key 1，在 Key 2 和 Key 3 之间轮询。如果 Key 2 也被限流了，轮询就只剩 Key 3。所以 Key 3 的调用次数远超 Key 1——不是分配不均，而是 Key 3 从未被限流过。"

**技术映射**：负载均衡 = Round Robin（公平轮询）+ Cooldown（故障转移）。冷却标记存在于 Redis 中，TTL=60s。被冷却的 Key 不参与轮询。

**小白**：（皱眉思考）"那如果 3 个 Key 同时冷却了呢？用户岂不是直接看到'服务不可用'？"

**大师**："这就是 Dify 冷却策略的**已知局限**——它只有简单的固定 TTL 冷却，没有'至少保留一个备用 Key'的兜底策略。如果 3 个 Key 恰好都在冷却中（比如某个用户短时间内发了大量请求），`LoadBalancer.select()` 会在遍历完所有凭据后发现全部冷却，抛出 `NoAvailableModelError`——用户看到的错误信息是'抱歉，所有模型凭据当前不可用，请稍后重试'。"

**技术映射**：冷却策略的局限 = 全量冷却时无优雅降级。改进方向：指数退避（Exponential Backoff）+ 最小可用比例保障。

**小胖**："那 Prompt 渲染呢？我用 `{{#node_123.text#}}` 引用上游输出，有时候报'变量未找到'。但我去变量池里看，明明有 node_123 的 text 字段。"

**大师**："两种可能：
1. **引用时机不对**：你在节点 B 的 Prompt 中引用了节点 A 的 `text`，但节点 A 还没执行完（或报错退出了）。变量池里确实还没有这个变量。
2. **引用语法错**：你可能写成了 `{{#node_123.text#}}` 但实际节点 ID 是 `node_123abc`（多了几个字符）。Dify 的 ID 是动态生成的数字，肉眼容易看错。建议在变量选择器中点击选择而不是手动输入 ID。"

**技术映射**：变量未找到 = 节点未完成（时序问题）或 ID 不匹配（拼写问题），需要从时序和语法两个维度排查。

**小白**："第三层——LLM 调用的实际执行。不同 Provider 的错误处理有什么区别？"

**大师**：
- **OpenAI**：401（Key 无效）→ 冷却 60s；429（限流）→ 冷却 60s + 重试；500（服务端错误）→ 重试 3 次后退避
- **Ollama（本地模型）**：连接拒绝 → 可能 Ollama 没启动或地址错了（`localhost` vs `host.docker.internal` 的问题）；模型未找到 → model name 拼写错误
- **插件模型**：gRPC 超时 → Plugin Daemon 可能挂了或插件响应慢，检查 `docker logs docker-plugin_daemon-1`"

## 3. 项目实战

### 环境准备

| 条件 | 说明 |
|------|------|
| Dify 已部署 | Worker 和 Redis 正常 |
| 至少 2 个 OpenAI Key | 用于观察负载均衡 |
| redis-cli | 用于监控冷却标记 |

### 分步实现

#### 步骤1：阅读 ModelManager 调度源码（目标：理解凭据选择逻辑）

```python
# api/core/model_manager.py（简化核心逻辑，带详细注释）
class ModelManager:
    @staticmethod
    def get_instance(tenant_id: str, provider: str, model_type: str, model: str):
        # === Step 1: 从 ProviderManager 获取该租户的 Provider 配置 ===
        # ProviderManager 维护了每个租户配置了哪些 Provider、
        # 每个 Provider 下有哪些凭据（API Key）
        provider_config = ProviderManager.get_config(tenant_id, provider)
        
        # === Step 2: 获取匹配的凭据列表 ===
        # 同一个 Provider+Model 下可能有多个凭据（如 3 个 OpenAI Key）
        credentials = provider_config.get_credentials(model_type, model)
        
        if not credentials:
            raise ModelNotConfiguredError(
                f"租户 {tenant_id} 未配置 {provider}/{model}"
            )
        
        # === Step 3: 负载均衡选择 ===
        # 在多个凭据中选一个可用（跳过冷却中的）
        selected_credential = LoadBalancer.select(
            tenant_id, provider, model_type, model, credentials
        )
        
        # === Step 4: 构建模型实例 ===
        # ModelInstance 封装了 Provider + Model + Credential + ModelType
        # 对外暴露统一的 invoke_llm() 接口
        return ModelInstance(
            provider=provider,
            model=model,
            credential=selected_credential,
            model_type=model_type,
        )


class LoadBalancer:
    COOLDOWN_TTL = 60  # 冷却 60 秒
    
    @classmethod
    def select(cls, tenant_id, provider, model_type, model, credentials):
        r = redis_client
        
        # 获取当前轮询位置（存在 Redis 中，所有 Pod 共享）
        index_key = f"model_lb_index:{tenant_id}:{provider}:{model_type}:{model}"
        current_index = int(r.get(index_key) or 0)
        
        # 遍历所有凭据，找到第一个不在冷却中的
        for i in range(len(credentials)):
            idx = (current_index + i) % len(credentials)
            config_id = credentials[idx].id
            
            # 检查该凭据是否在冷却中
            cooldown_key = f"model_lb_cooldown:{tenant_id}:{provider}:{model_type}:{model}:{config_id}"
            if not r.exists(cooldown_key):
                # 找到可用的凭据！更新轮询指针
                r.set(index_key, (idx + 1) % len(credentials))
                return credentials[idx]
        
        # 所有凭据都在冷却中
        raise NoAvailableModelError(
            f"所有 {len(credentials)} 个凭据当前均处于冷却状态，请在 {COOLDOWN_TTL} 秒后重试"
        )
    
    @classmethod
    def mark_cooldown(cls, tenant_id, provider, model_type, model, config_id, ttl=None):
        """标记一个凭据为冷却状态（通常由 429 或 401 触发）"""
        r = redis_client
        cooldown_key = f"model_lb_cooldown:{tenant_id}:{provider}:{model_type}:{model}:{config_id}"
        r.setex(cooldown_key, ttl or cls.COOLDOWN_TTL, "1")
        logging.warning(f"凭据 {config_id} 进入冷却，持续 {ttl or cls.COOLDOWN_TTL} 秒")
```

**三个关键细节**：

1. **`current_index` 存在 Redis 中**——这意味着轮询位置是跨 API Pod 共享的。Pod A 用了 Key 1，指针移到 2。Pod B 下一次选 Key 时从 2 开始
2. **`r.exists(cooldown_key)` 是 O(1) 操作**——检查一个 Key 是否在冷却中只需要一次 Redis EXISTS 命令，不影响性能
3. **冷却键不需要手动删除**——`SETEX` 设置了 TTL，60 秒后自动过期

#### 步骤2：LLM 节点的 Prompt 渲染与执行链路（目标：理解完整数据流）

```python
# api/core/workflow/nodes/llm/llm_node.py（简化）
class LLMNode(BaseNode):
    def _run(self, runtime: NodeRuntime) -> dict:
        # ====== Step 1: 渲染 Prompt 模板 ======
        # 将用户在配置面板中写的 Prompt（包含 {{#变量#}}）渲染为真实值
        rendered_prompt = self._render_prompt_template(runtime)
        # rendered_prompt 现在是完整的消息列表：
        # [{"role": "system", "content": "你是客服助手...当前用户是张三..."},
        #  {"role": "user", "content": "用户问题：如何退货"}]
        
        # ====== Step 2: 获取模型实例 ======
        model_instance = ModelManager.get_instance(
            tenant_id=runtime.tenant_id,
            provider=self.model_config['provider'],
            model_type='llm',
            model=self.model_config['model'],
        )
        
        # ====== Step 3: 流式调用 LLM ======
        full_response = ""
        total_tokens = 0
        
        for chunk in model_instance.invoke_llm_stream(rendered_prompt):
            full_response += chunk.text
            total_tokens += chunk.tokens
            # 每个 chunk 包装为事件推给前端
            yield LLMChunkEvent(
                chunk=chunk.text,
                node_id=self.node_id
            )
        
        # ====== Step 4: 返回结构化结果 ======
        return {
            "text": full_response,
            "usage": {
                "total_tokens": total_tokens,
                "prompt_tokens": chunk.prompt_tokens,
                "completion_tokens": chunk.completion_tokens,
            }
        }
```

#### 步骤3：冷却机制实时观察实验（目标：验证冷却的产生和消失）

```bash
# === 准备两个终端 ===
# 终端 1：监控冷却 Key 的出现和消失
watch -n 1 'echo "=== 冷却标记 ===" && docker exec docker-redis-1 redis-cli KEYS "model_lb_cooldown:*" 2>/dev/null && echo "=== 轮询指针 ===" && docker exec docker-redis-1 redis-cli KEYS "model_lb_index:*" 2>/dev/null | while read k; do echo "$k: $(docker exec docker-redis-1 redis-cli GET "$k")"; done'

# 终端 2：快速连续发送 15 条请求（触发限流）
for i in $(seq 1 15); do
  echo "发送请求 $i..."
  curl -s -o /dev/null -w "HTTP %{http_code}\n" \
    -X POST http://localhost/v1/chat-messages \
    -H "Authorization: Bearer app-xxx" \
    -H "Content-Type: application/json" \
    -d "{\"query\":\"快速测试 $i\",\"user\":\"loadtest\",\"response_mode\":\"blocking\"}" &
done
wait

# === 观察终端 1 的输出 ===
# 00:00 → 无冷却标记，轮询指针为 0
# 00:05 → 请求开始，轮询指针依次递增 1→2→3→1→2→3...
# 00:12 → 出现 model_lb_cooldown:...:config_key1（Key 1 触发 429，进入冷却）
# 00:12 → 轮询跳过 Key 1，只在 Key 2 和 Key 3 之间轮询
# 01:12 → Key 1 冷却标记自动消失（TTL 60 秒到期）
# 01:12 → 轮询恢复包含 Key 1

# 观察：冷却期间终端 2 的 HTTP 状态码偶尔出现 429

# 验证冷却 Key 的 TTL
docker exec docker-redis-1 redis-cli TTL \
  "model_lb_cooldown:tenant_xxx:openai:llm:gpt-3.5-turbo:config_yyy"
# 输出：58 → 55 → ... → 3 → 1 → (nil) （Key 已过期）
```

**核心观察**：
1. 冷却标记的出现时间 = 第一个 429 返回的时间
2. 冷却期间其他 Pod 自动跳过该 Key（通过 `r.exists(cooldown_key)` 检查）
3. 60 秒后 Key 自动删除，无需任何清理逻辑——这就是 Redis TTL 的优势

#### 步骤4：排查"模型调不通"的三层诊断法

```bash
# === 第一层：Prompt 渲染 ===
# 症状：变量未找到
# 排查：在 Workflow 调试模式中单步运行 LLM 节点，检查变量池快照
docker logs docker-api-1 | Select-String "VariableNotFoundError"

# === 第二层：凭据选择 ===
# 症状：NoAvailableModelError
# 排查：检查 Redis 中的冷却标记
docker exec docker-redis-1 redis-cli KEYS "model_lb_cooldown:*"
# 如果有结果：所有凭据都在冷却中，等待 TTL 过期
# 如果无结果但还报错：Provider 配置可能没有正确关联到该租户

# === 第三层：LLM 调用执行 ===
# 症状：超时、401、500
# 排查：
docker logs docker-api-1 --tail 50 | Select-String "openai|ollama|429|401|timeout"
# 429 → 触发了冷却，检查冷却标记
# 401 → API Key 无效，在控制台重新配置
# timeout → 网络问题或模型服务响应慢，检查代理设置
```

### 测试验证

```bash
# 综合验证脚本
echo "=== 1. 检查模型实例化链路 ==="
docker logs docker-api-1 --tail 100 | Select-String "ModelManager|get_instance|LoadBalancer"

echo "=== 2. 检查当前冷却状态 ==="
docker exec docker-redis-1 redis-cli KEYS "model_lb_cooldown:*"

echo "=== 3. 检查轮询位置 ==="
docker exec docker-redis-1 redis-cli KEYS "model_lb_index:*" | while read k; do
  echo "$k: $(docker exec docker-redis-1 redis-cli GET "$k")"
done

echo "=== 4. 发送测试请求 ==="
curl -s -X POST http://localhost/v1/chat-messages \
  -H "Authorization: Bearer app-xxx" \
  -d '{"query":"ping","user":"test","response_mode":"blocking"}' | \
  python -c "import sys,json; d=json.load(sys.stdin); print(f'状态: OK, Token: {d.get(\"metadata\",{}).get(\"usage\",{}).get(\"total_tokens\",\"N/A\")}')"
```

## 4. 项目总结

### 三层调度总览

| 调度层 | 职责 | 输入 → 输出 | 常见问题 |
|--------|------|-----------|---------|
| **Prompt 渲染** | 变量替换为真实值 | Prompt 模板 + 变量池 → 完整消息列表 | 变量未找到、类型不匹配 |
| **凭据选择** | 负载均衡选可用 Key | 凭据列表 → 单个可用凭据 | 全 Key 冷却、轮询"不均" |
| **LLM 调用** | 实际执行推理 | 消息列表 → Token 流 | 超时、限流429、认证401 |

### 适用场景

| 场景 | 推荐调试方法 |
|------|------------|
| "变量未找到" | 单步调试 LLM 节点，导出变量池快照 |
| "Key 分配不均" | 查看 Redis 冷却标记，确认非 bug 而是冷却策略 |
| "突然全部不可用" | 检查是否全 Key 冷却 → 增加 Key 数量或降低并发 |
| "调用变慢" | Langfuse Trace 看是网络延迟还是模型排队 |

### 注意事项

1. **冷却 TTL 固定 60 秒**：对突发流量不够灵活。如果你的应用峰值流量每小时只有 5 分钟，60 秒冷却足够；如果流量持续高峰，可能需要增加 Key 而非依赖冷却
2. **轮询指针存储在 Redis**：如果 Redis 重启，指针归零但不影响功能（只是从头开始轮询）
3. **不要让 `cooldown_key` 和其他业务的 Key 重名**：Dify 的 Key 格式包含 `tenant_id + provider + model_type + model + config_id`，确保全局唯一

### 常见踩坑经验

1. **坑：`LoadBalancer.select()` 选择了冷却中的凭据** → 根因：`r.exists(cooldown_key)` 中 Key 名拼写错误（如 model 名称少了后缀），导致检查不到
2. **坑：冷却标记永远不消失** → 根因：用了 `SET` 而非 `SETEX`（忘了设置过期时间），Key 变成永久存在。解决：手动 `DEL` 该 Key，并修复代码
3. **坑：Ollama 模型"调用成功"但返回乱码** → 根因：Ollama 的 API 格式和 OpenAI 不完全兼容（如 temperature 参数范围不同）。检查 Ollama 版本是否需要特定 `options`

### 思考题

1. **进阶题**：如果所有 Key 同时冷却，当前系统直接抛 `NoAvailableModelError`。请设计一个"优雅降级"方案——至少保留一个 Key 作为'最后的希望'（即使被限流也继续使用，而非直接拒绝请求）。（提示：引入 Key 的"最小可用池"概念 + 优先级权重）

2. **进阶题**：当前 Round Robin 只看调用次数，不关心 Token 消耗。如果 Key A 处理了 10 个"说 hello"的请求（共消耗 100 Token），Key B 处理了 1 个"写 1000 字文章"的请求（共消耗 2000 Token），轮询指针认为"公平"，但实际上 Key B 消耗了更多的配额。如何实现"按 Token 消耗量做加权负载均衡"？

> **参考答案**：见附录 D

---

> **推广计划提示**：本章是中级篇的核心之一。必须完成步骤 3 的冷却观察实验——只有亲眼看到冷却 Key 的出现和消失，才能真正理解负载均衡的运作。架构师建议深入思考以上两个思考题，这是模型治理的产品化方向。
