# 第25章：Agent 策略深度解析——FC vs CoT 源码对比

## 1. 项目背景

第 8 章我们用 Agent 搭了一个行程规划助手——配置策略、添加工具、写好 Prompt，AI 就自己跑起来了。但当 Agent 在 8 步后陷入循环（反复调同一个工具但结果不满意），或者明明有搜索工具却不去调用（Agent 认为"不需要搜索"），你就需要从源码级理解 Agent 的决策机制。

Dify 的 Agent 有两种策略：**Function Calling（FC）Agent** 和 **Chain-of-Thought（CoT）Agent**。它们的核心差异不在于"能不能调用工具"（两种都能调用），而在于**工具调用的触发机制**和**决策过程的透明度**。

FC Agent 依赖 LLM 原生的 Function Calling 能力——模型被训练成能输出结构化的 JSON（`{"name": "get_weather", "arguments": {"city": "Beijing"}}`）。Dify 解析 JSON 后执行工具，把结果返回给 LLM。这个流程稳定、快速，但只有高级模型（GPT-4、Claude）支持。

CoT Agent 不依赖模型的 Function Calling 能力，而是通过精心设计的 Prompt 模板，让 LLM 以纯文本形式输出"思考：...动作：工具名[参数]"。Dify 用正则解析这行文本后执行工具。兼容所有模型（包括开源的小模型），但文本解析容易出错——LLM 多打了空格、少写了引号、用了中文冒号——正则就匹配失败。

**场景一：CoT Agent 卡在第 3 步不动了**。检查日志发现 LLM 输出了"动作：google_search[北京天气]"——但 Dify 的正则是 `"动作：(.+)\[(.+)\]"`，匹配不到中文冒号（应该是英文冒号）。这是一个时区/编码问题。

**场景二：FC Agent 调用了"不存在的工具"**。根因：LLM 的 tool_calls 中返回了未注册的函数名。Dify 应该捕获 `ToolNotFoundError` 并告诉 LLM "这个工具不存在，请选择别的工具"——但如果这个处理逻辑没写对，Agent 就卡住了。

## 2. 项目设计——剧本式交锋对话

**小胖**：（拿着日志对比）"大师！我用 FC Agent 和 CoT Agent 测了同一个任务——'查北京天气并推荐户外活动'。FC 用了 3 步，CoT 用了 6 步。而且 FC 的 Token 消耗才 850，CoT 是 1200。这差距也太大了，FC 完胜啊？"

**大师**："不能简单说'完胜'。FC 的 3 步分别是：第一步 LLM 输出 `tool_calls: [{name: "search", args: {query: "北京天气"}}]`；第二步 Dify 执行搜索；第三步 LLM 基于搜索结果生成最终回答。CoT 的 6 步是：第一步思考'需要搜索天气'；第二步输出'动作：search[北京天气]'；第三步等待搜索结果；第四步思考'天气是晴天，可以推荐户外活动'；第五步输出'最终答案：...'；第六步格式修正。CoT 多出来的 3 步全是'思考过程'——它不是低效，而是把每一步推理都显式输出了。"

**技术映射**：FC 的思考过程在 LLM 内部（不输出），CoT 的思考过程是 LLM 的显式输出。步数差异 = CoT 多了"思考步骤"。

**小白**：（在白板上画对比图）"那 CoT 的容错性怎么样？如果 LLM 写'动作：搜索天气[北京]'而不是'动作：google_search[北京]'，Dify 怎么知道调哪个工具？"

**大师**："这就是 CoT 最大的弱点——**工具名称不是结构化匹配的**。FC 的 `tool_calls` 中 `function.name` 是确定的字段，Dify 精确匹配。CoT 的正则是 `"动作：(.+)\[(.+)\]"`，从文本中提取可能的工具名。如果 LLM 写了'搜索天气'但实际工具叫 `google_search`，正则提取出了'搜索天气'，Dify 在工具管理器里找不到这个工具名，就会抛 `ToolNotFoundError`。处理方式通常是：把错误信息反馈给 LLM（'工具 搜索天气 不存在，可用工具：google_search, web_scraper'），让 LLM 自己修正。"

**技术映射**：FC = 结构化匹配（精确），CoT = 自然语言匹配（模糊）。CoT 需要额外的错误处理和 LLM 修正循环。

**小胖**："那什么时候我该选 FC，什么时候选 CoT？"

**大师**："选型决策树：
- 如果你用的模型是 GPT-4 或 Claude → 优先 FC（更快、更可靠、Token 消耗更低）
- 如果你用的模型是开源模型（Qwen、Llama）或本地模型 → 只能选 CoT（不支持 Function Calling）
- 如果你的任务需要很长的推理链（如'分析这份财报并指出三个风险点'）→ CoT 可能更好，因为你能看到 LLM 的完整思考过程（更容易调试）
- 如果你对稳定性要求很高（如金融交易合规场景）→ FC 更合适（结构化输出不易出错）"

## 3. 项目实战

### 环境准备

| 条件 | 说明 |
|------|------|
| Dify 已部署 | Agent App 已创建 |
| 两种策略的环境 | GPT-4（测 FC）+ 任意模型（测 CoT） |
| 测试任务 | 准备 3 个标准任务用于对比 |

### 分步实现

#### 步骤1：FC Agent 核心源码解读（目标：理解结构化工具调用的完整流程）

```python
# api/core/agent/fc_agent_runner.py（核心逻辑，带注释）
class FCAgentRunner(BaseAgentRunner):
    def run(self, query: str) -> Generator[AgentEvent]:
        history = []  # 工具调用的历史记录
        
        for step in range(self.max_iterations):
            # === Step 1: 组装消息 ===
            # System Prompt + 之前的所有工具调用历史 + 当前用户问题
            messages = self._build_messages(query, history)
            
            # === Step 2: 调用 LLM ===
            # ★ 关键：tools 参数告诉 LLM 有哪些工具可用
            # LLM 可以选择调用工具，也可以直接输出最终答案
            response = self.model_instance.invoke_llm(
                messages=messages,
                tools=self.tools,  # 工具列表：[{"name": "search", "parameters": {...}}, ...]
            )
            
            # === Step 3: 判断 LLM 的选择 ===
            if response.tool_calls:
                # LLM 决定调用工具
                tool_call = response.tool_calls[0]  # 目前只处理单个工具调用
                tool_name = tool_call.function.name
                tool_args = json.loads(tool_call.function.arguments)
                
                yield AgentActionEvent(tool=tool_name, args=tool_args)
                
                # === Step 4: 执行工具 ===
                try:
                    tool_result = self.tool_manager.execute(tool_name, tool_args)
                    yield AgentObservationEvent(result=tool_result)
                except ToolNotFoundError:
                    tool_result = f"工具 {tool_name} 不存在"
                except ToolExecutionError as e:
                    tool_result = f"工具执行失败: {str(e)}"
                
                # === Step 5: 记录历史并继续循环 ===
                history.append({
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [tool_call]
                })
                history.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": tool_result
                })
            else:
                # LLM 直接输出了最终答案（不调用工具了）
                yield AgentFinalAnswerEvent(answer=response.content)
                return response.content
        
        # 达到最大迭代次数
        raise AgentMaxIterationError(f"Agent 超过最大迭代次数 {self.max_iterations}")
```

**关键设计**：FC Agent 的"历史消息"中包含了结构化的 `tool_calls`——LLM 在下一轮推理时能看到"我上一轮调用了什么工具、得到了什么结果"。这是 FC Agent 能做出正确后续决策的基础。

#### 步骤2：CoT Agent 的 Prompt 模板和解析逻辑（目标：理解文本驱动的工具调用）

```python
# api/core/agent/cot_agent_runner.py（核心逻辑）
class CotAgentRunner(BaseAgentRunner):
    SYSTEM_PROMPT_TEMPLATE = """你可以使用以下工具：
{tools_description}

请严格按照以下格式响应（不要添加多余内容）：

思考：我应该先做什么？
动作：工具名称[参数]
观察：工具返回的结果
...（可重复思考-动作-观察循环）...
最终答案：你的最终回复

现在开始解决用户的问题！"""
    
    def run(self, query: str) -> Generator[AgentEvent]:
        prompt = self.SYSTEM_PROMPT_TEMPLATE.format(
            tools_description=self._describe_tools()
        )
        
        for step in range(self.max_iterations):
            # 把用户问题追加到 Prompt 末尾
            full_prompt = prompt + f"\n\n用户：{query}"
            
            # 调用 LLM（无 tools 参数——CoT 依赖纯文本）
            response = self.model_instance.invoke_llm(full_prompt)
            
            # ★ 解析 LLM 的文本输出
            action_match = re.search(r'动作：(.+)\[(.+)\]', response)
            final_match = re.search(r'最终答案：(.+)', response, re.DOTALL)
            
            if action_match:
                tool_name = action_match.group(1).strip()
                tool_arg = action_match.group(2).strip()
                
                yield AgentActionEvent(tool=tool_name, args=tool_arg)
                
                try:
                    result = self.tool_manager.execute(tool_name, tool_arg)
                    yield AgentObservationEvent(result=result)
                except ToolNotFoundError:
                    result = f"工具 '{tool_name}' 不存在。可用: {self._list_tool_names()}"
                
                # 把观察结果追加到 Prompt，继续循环
                prompt += f"\n观察：{result}"
            
            elif final_match:
                return final_match.group(1).strip()
            
            else:
                # 既没有"动作："也没有"最终答案："——格式错误
                prompt += "\n观察：输出格式错误，请使用'动作：工具名[参数]'或'最终答案：...'"
    
    def _describe_tools(self) -> str:
        """将工具列表转换为 LLM 可读的描述"""
        desc = []
        for tool in self.tools:
            desc.append(f"- {tool.name}: {tool.description}")
            desc.append(f"  参数: {tool.get_parameters()}")
        return "\n".join(desc)
```

**关键设计**：CoT Agent 每轮都把完整 Prompt + 用户问题 + 历史观察结果重新发给 LLM。这解释了为什么 CoT 比 FC 消耗更多 Token——Prompt 越来越长。

#### 步骤3：FC vs CoT 量化对比实验（目标：用数据做选择）

准备 3 个标准任务，每种策略跑 5 次取平均值：

```text
任务 1：简单查询 - "今天北京天气怎么样"
  FC: 平均 3.2 步, 920 tokens, 成功率 100%
  CoT: 平均 5.6 步, 1400 tokens, 成功率 96%
  结论：FC 明显更优（步数少 43%, Token 少 34%）

任务 2：多步骤复杂任务 - "帮我做京都 3 天旅行攻略"
  FC: 平均 7.4 步, 4200 tokens, 成功率 92%
  CoT: 平均 9.2 步, 5800 tokens, 成功率 88%
  结论：FC 更优但差距缩小（复杂任务中 CoT 的思考过程有额外价值）

任务 3：模糊意图 - "最近有什么好玩的"
  FC: 平均 5.1 步, 2100 tokens, 成功率 78%  ← 有些失败因为 LLM 不知道用什么工具
  CoT: 平均 6.8 步, 2800 tokens, 成功率 85%  ← 略高因为 LLM 在思考中能"自我纠偏"
  结论：CoT 在模糊场景下略优（思考过程帮助 LLM 自我澄清意图）

综合结论：
- 明确任务 → FC（更快、更稳定、更便宜）
- 模糊任务 → CoT（思考链帮助 Intent Resolution）
- 开源模型 → 只能 CoT
- 合规要求 → FC（结构化输出可校验）
```

### 测试验证

```bash
# 查看 Agent 日志中的每一步思考过程
# Dify 控制台 → 应用日志 → 选择某条 Agent 对话 → 查看详情
# 日志格式：
# Step 1: Thought: ...  |  Action: google_search["北京天气"]
# Step 2: Observation: 北京5月11日 晴 18-28℃
# Step 3: Thought: ...  |  Final Answer: 北京今天晴朗...

# 如果 CoT Agent 出现格式解析失败：
docker logs docker-api-1 | Select-String "动作：|action parse fail|ToolNotFoundError"
```

## 4. 项目总结

### FC vs CoT 全面对比

| 维度 | FC Agent | CoT Agent |
|------|---------|----------|
| **工具调用** | LLM 原生 tool_calls（JSON Schema） | Prompt 文本解析（正则匹配） |
| **支持模型** | GPT-4/Claude 等高级模型 | 所有模型（包括开源） |
| **稳定性** | 高（结构化输出，不依赖文本解析） | 中（依赖 Prompt 质量和正则健壮性） |
| **Token 消耗** | 低（历史消息紧凑） | 高（每轮发送完整 Prompt） |
| **调试体验** | 低（思考过程在 LLM 内部不输出） | 高（完整思考链可见） |
| **模糊意图** | 弱（LLM 可能不知道选哪个工具） | 强（思考过程帮助自我澄清） |

### 适用场景

| 场景 | 推荐策略 | 原因 |
|------|---------|------|
| 客服机器人（查询订单/FAQ） | FC | 任务明确，稳定性优先 |
| 数据分析助手 | CoT | 需要看到中间推理过程 |
| 开源模型环境 | CoT | 模型不支持 Function Calling |
| 金融合规 Agent | FC | 结构化输出可审计 |
| 开放式研究助手 | CoT | 思考链帮助自主探索 |

### 注意事项

1. **FC Agent 中 `tools` 参数不能超过 LLM 限制**：GPT-4 的 tools 参数有大小限制（约 128K tokens），太多工具会导致超出上下文窗口
2. **CoT 的正则要做好容错**：LLM 可能输出中文/英文冒号、全角/半角方括号。正则应该写得更宽松
3. **两种策略的"历史消息"格式不兼容**：不能混合使用——不能在 FC Agent 的对话历史上继续用 CoT

### 常见踩坑经验

1. **坑：CoT Agent 连续 3 步都解析失败** → 根因：正则太严格（如要求英文方括号但 LLM 用了中文方括号）。解决：放宽正则为 `动作\s*[:：]\s*(.+?)\s*[\[［](.+?)[\]］]`
2. **坑：FC Agent 调工具后 LLM 说"我无法调用工具"** → 根因：返回给 LLM 的工具结果格式不对，LLM 不理解。解决：工具返回的结果应该是自然语言文本，不是原始 JSON
3. **坑：Agent 在"第 7 步就该结束"但硬是跑到了第 8 步** → 根因：`max_iterations=8` 但在 System Prompt 中没有给 LLM 足够的"该收手了"的信号。解决：在 Prompt 中加"如果信息已经足够，请立即输出最终答案，不要重复搜索"

### 思考题

1. **进阶题**：FC Agent 的 `tool_calls` 目前只处理单个工具调用。如果 LLM 同时返回多个 `tool_calls`（比如同时调天气和地图两个 API），Dify 如何处理并行工具调用？（提示：asyncio.gather 同时执行多个工具）

2. **进阶题**：如果 CoT Agent 连续 2 步解析失败（LLM 格式不对），你会如何自动切换到"降级模式"——强制要求 LLM 直接输出最终答案而非继续尝试调工具？

> **参考答案**：见附录 D

---

> **推广计划提示**：本章是 Agent 源码对比的核心章节。必须完成步骤 3 的量化对比实验（3 个任务 × 2 种策略 × 5 次），这是选择 Agent 策略的决策依据。架构师建议关注"全 Key 冷却"和"CoT 解析容错"两个方向的改进。
