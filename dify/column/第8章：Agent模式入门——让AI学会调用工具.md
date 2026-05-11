# 第8章：Agent 模式入门——让 AI 学会调用工具

## 1. 项目背景

"帮我把这份 50 页的 PDF 翻译成英文，然后发邮件给 john@example.com。"——这是个典型的"Agent 任务"。如果要靠 Chat App 或简单的 Workflow 来做，你需要分三步手动操作：上传 PDF 翻译 → 复制翻译结果 → 打开发邮件界面 → 粘贴内容 → 发送。但 Agent 可以自动完成全过程——翻译是调用 LLM，提取 PDF 文本是调用文档工具，发送邮件是调用邮件工具——Agent 像一个小助理，根据任务自动决定需要用什么工具、按什么顺序执行。

Dify 的 Agent 模式基于经典的 ReAct（Reasoning + Acting）范式——Thought（思考：我需要先做什么）→ Action（行动：调用某个工具）→ Observation（观察：工具返回了什么结果）→ 循环直到任务完成。这个机制让 Agent 不仅能"回答问题"，还能"做事"。

但 Agent 也有"翻车"的常见场景：调用工具后不知道下一步该怎么走（无限循环）、工具调用结果和预期不符（幻觉调用）、任务太简单却用了很贵的模型（杀鸡用牛刀）。本章将带你从 Agent 的基本配置出发，通过一个"智能行程规划 Agent"的完整实战，理解 Agent 的两种策略（FC 和 CoT）、工具选择与配置、以及如何写好 Agent 的系统提示词让它"听话"。

## 2. 项目设计——剧本式交锋对话

**小胖**：（兴奋地挥舞着手机）"大师！我昨天试了 Dify 的 Agent，让它帮我查'今天北京的天气'，它真的自己打开了搜索工具，搜到了天气，然后告诉我结果！这跟 ChatGPT 自动搜索完全不一样——我能看到它的每一步操作！"

**大师**："这就是 Agent 的魅力——它不是黑箱，而是透明地展示每一步推理和行动。你看到的那三步就是经典的 ReAct 循环：

- **Thought**（思考）：'用户想知道今天北京的天气，我需要搜索获取信息。'
- **Action**（行动）：调用 Google Search，输入'北京天气 2026年5月11日'
- **Observation**（观察）：搜索返回了'北京 5月11日 晴 18-28℃'
- **Final Answer**（最终回答）：'北京今天晴朗，气温 18-28℃，适合出行。'"

**技术映射**：ReAct = Reasoning（推理） + Acting（执行），每一步行动前先思考，执行后观察结果再决策。

**小白**："那 Function Calling Agent 和 ReAct（CoT）Agent 有什么区别？"

**大师**："这是 Dify Agent 的两种策略，底层原理完全不同：
- **Function Calling（FC）Agent**：利用 LLM 原生的 Function Calling 能力。模型被训练成能输出 JSON 格式的函数调用（如 `{"name": "get_weather", "arguments": {"city": "Beijing"}}`）。Dify 接收到这个 JSON 后，在平台上执行对应的工具，把结果返回给 LLM。这种方式稳定、快速，但只有部分模型支持（GPT-4、Claude 等）。
- **Chain-of-Thought（CoT）Agent**：也叫 ReAct Agent。它不依赖模型的 Function Calling 能力，而是通过精心设计的 Prompt 模板让 LLM 以纯文本形式输出'思考→行动→观察'。兼容所有模型（包括不支持 Function Calling 的开源模型），但输出格式不如 FC 稳定。"

**小胖**："那我该选哪个？"

**大师**："如果你用的是 GPT-4 或 Claude，优先选 FC——更可靠更快速。如果你用的是开源模型（如 Qwen、Llama），只能选 CoT。另外，如果任务需要很长的推理链（超过 10 步），CoT 的文本输出反而比 FC 更灵活——因为你可以看到 LLM 的完整思考过程。"

**技术映射**：FC Agent = 结构化工具调用（JSON Schema），CoT Agent = 文本驱动的工具调用（Prompt Engineering）。

**小白**："Agent 配置里有个'最大迭代次数'，这个设多少合适？"

**大师**："这取决于任务的复杂度。简单查询（查天气、翻译）设 5 步就够了。复杂任务（多工具协作、数据分析）设 10-15 步。但要注意——设太大有两个风险：一是 Agent 可能无限循环（思考→行动→不满意→再思考……），Token 消耗暴涨；二是如果某个工具持续返回错误，Agent 会反复重试。我们的经验是设一个你'能接受的失败上限'——比如客服场景设 8 步，宁愿失败一次让人工介入，也比让 Agent 烧 $2 的 Token 强。"

**小胖**："那 Agent 的系统提示词怎么写？和 Chat App 有什么不同？"

**大师**："Agent 的提示词有三个特殊点：
1. **角色 + 能力边界**：告诉它'你是谁'和'你能做什么、不能做什么'。
2. **工具使用规则**：什么时候用哪个工具，工具调用失败时如何处理。
3. **输出格式约束**：最终回答的格式要求（比如'不要输出中间思考过程'）。"

## 3. 项目实战

### 环境准备

| 条件 | 说明 |
|------|------|
| Dify 已部署 | 第 2 章完成 |
| LLM Provider 已配置 | 第 3 章完成，建议用 GPT-4o 或 Claude（支持 Function Calling） |
| 工具可用 | 至少启用 Google Search 或 Web Scraper 工具 |

### 分步实现

#### 步骤1：创建 Agent App 并配置基础参数（目标：跑通第一个 Agent）

1. Dify 首页 → **创建应用** → **Agent** → 命名"行程规划助手"
2. 在 Agent 编辑页配置：

```yaml
模型：GPT-4o（建议）
Agent 策略：Function Calling

系统提示词：
  你是一个专业的旅行规划助手。你可以帮助用户：
  - 搜索旅游目的地信息和天气
  - 推荐景点和美食
  - 规划每日行程路线

  规则：
  1. 先搜索用户提到的目的地基本信息（天气、最佳旅行季节）
  2. 再根据用户偏好推荐景点和美食
  3. 最后生成一份结构化的行程计划
  4. 如果搜索结果不足，诚实告知用户并建议替代方案
  5. 行程计划以表格形式呈现

最大迭代次数：8
```

3. **工具配置**：在右侧工具面板中，添加以下工具：
   - **Google Search**（或 **DuckDuckGo**）
   - **Web Scraper**（用于抓取网页详细内容）
   - **Current Time**（获取当前时间）

4. 在预览窗口测试：

```
用户：我计划6月份去京都旅行3天，帮我规划一个行程，我喜欢历史文化景点和当地美食。

Agent 思考过程：
  Thought：用户想去京都旅行，我需要先搜索京都6月天气和推荐景点。
  Action：调用 Google Search → "京都 6月 天气 旅游"
  Observation：京都6月平均气温22-28℃，偶有梅雨，适合避暑行程...
  Thought：获取了天气信息，现在搜索历史文化景点。
  Action：调用 Google Search → "京都 必游 历史文化景点 推荐"
  Observation：金阁寺、清水寺、伏见稻荷大社、二条城...
  Thought：再搜索美食推荐。
  Action：调用 Google Search → "京都 传统美食 推荐"
  Observation：怀石料理、抹茶甜品、汤豆腐、锦市场小吃...
  Thought：信息已足够，现在整合成行程。
  
最终回答：
  | 日期 | 上午 | 下午 | 晚上 | 美食推荐 |
  |------|------|------|------|---------|
  | Day1 | 金阁寺 | 二条城 | 祇园散步 | 怀石料理 |
  | Day2 | 伏见稻荷 | 清水寺 | 锦市场 | 抹茶甜品 |
  | Day3 | 岚山竹林 | 天龙寺 | 返程 | 汤豆腐 |
```



#### 步骤2：配置自定义 API 工具（目标：让 Agent 调用公司内部系统）

场景：为行程规划 Agent 添加一个"查询公司假期余额"的工具，因为用户可能想问"我还有几天年假，够不够去旅行"。

在 Dify 控制台 → **工具** → **创建自定义工具** → **OpenAPI Schema**：

```yaml
openapi: 3.0.0
info:
  title: 假期查询
  version: 1.0.0
servers:
  - url: https://hr-api.internal.acme.com
paths:
  /api/v1/leave-balance:
    get:
      operationId: getLeaveBalance
      summary: 查询员工假期余额
      parameters:
        - name: employee_id
          in: query
          required: true
          schema:
            type: string
      responses:
        '200':
          description: 假期余额
          content:
            application/json:
              schema:
                type: object
                properties:
                  annual_leave:
                    type: number
                  sick_leave:
                    type: number
                  personal_leave:
                    type: number
```

**配置说明**：
- `operationId`：Agent 会把这个显示为"可调用的操作"
- `parameters`：定义了工具需要的输入参数（Agent 会自动从对话中提取参数值）
- `responses.schema`：定义了返回数据的结构（帮助 Agent 理解如何解读结果）

保存后，在 Agent 的工具面板中勾选"假期查询"工具。

测试：

```
用户：我想请假去京都旅行5天，但我不知道我还有多少年假。
  
Agent 思考：
  Thought：用户需要查询假期余额，我需要 employee_id。
           但用户没有提供，我需要先询问。
  Final Answer：好的，我先帮您查询假期余额。请问您的工号是多少？
  
用户：12345
  Thought：现在有了工号，调用假期查询工具。
  Action：调用 getLeaveBalance → employee_id=12345
  Observation：{"annual_leave": 8, "sick_leave": 5, "personal_leave": 2}
  Thought：年假还有8天，5天旅行够用了。
  Final Answer：您目前还有8天年假，去京都5天完全够用！...
```

#### 步骤3：对比 FC 和 CoT 策略（目标：理解两种策略的差异）

创建两个相同的 Agent（一个 FC，一个 CoT），用同一任务测试：

**任务**：搜索"Dify 开源平台最新版本特性"，然后总结出三条最重要的更新。

**FC Agent 的表现**（约 3 步）：
```
Step 1: 调用 Google Search → "Dify latest version features 2026"
Step 2: 调用 Web Scraper → 打开搜索结果第一个链接
Step 3: 基于抓取内容，生成三条总结
```

**CoT Agent 的表现**（约 4-5 步）：
```
Thought: 我需要搜索 Dify 最新版本信息
Action: {"tool": "google_search", "query": "Dify latest version"}
Observation: "Dify 1.14.0 released with new features..."
Thought: 搜索结果提供了版本号，我需要获取更详细的信息
Action: {"tool": "web_scraper", "url": "https://..."}
Observation: "Full changelog: 1. Workflow parallel execution, 2. Enhanced RAG..."
Thought: 我已经获得了三个主要更新，现在可以总结了
Final Answer: Dify 1.14.0 的三大更新是...
```

**结论**：FC 更简洁，CoT 的思考过程更透明。如果 Agent 行为不符合预期，CoT 的详细日志更有助于调试。

#### 步骤4：Agent 行为调试与优化（目标：解决 Agent 常见问题）

**问题 1：Agent 反复调用同一个工具**

症状：Agent 调用 Google Search 后不满意结果，再次调用，陷入循环。

解决：
```yaml
系统提示词中添加：
  如果连续两次搜索返回的结果都相似，请基于已有信息给出最佳回答，
  而不是继续搜索。完美答案不存在，请在信息充分时果断给出结论。
```

**问题 2：Agent 调用不存在的工具**

症状：Agent 尝试调用"发送邮件"，但你没配置邮件工具。

解决：
```yaml
系统提示词中明确：
  你只能使用以下工具：Google Search、Web Scraper、Current Time。
  不要尝试调用未列出的工具。如果任务需要你无法使用的工具，
  请诚实告知用户限制并建议替代方案。
```

**问题 3：Agent 输出的最终答案太啰嗦**

解决：
```yaml
系统提示词：最终回答请控制在 300 字以内，使用要点列表而非长段落。
```

### 测试验证

```bash
# 测试 1：通过 API 调用 Agent
curl -X POST http://localhost/v1/chat-messages \
  -H "Authorization: Bearer app-xxxxxxxx" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "帮我查一下北京明天天气，适合户外运动吗？",
    "user": "test-user",
    "response_mode": "streaming"
  }'

# 预期：SSE 事件中包含 agent_thought（思考过程）和 message（最终回答）

# 测试 2：验证 Agent 日志（在 Dify 控制台日志页）
# 筛选条件：应用 = 行程规划助手，查看每条日志的：
# - 总迭代步数
# - 每步触发的工具
# - Token 消耗
```

## 4. 项目总结

### 优点与缺点

| 维度 | 优点 | 缺点 |
|------|------|------|
| **自主决策** | Agent 自动决定何时调用哪个工具，无需预设流程 | 决策结果不可控，可能走"弯路"浪费 Token |
| **策略灵活** | FC/CoT 两种策略适配不同模型 | CoT 策略依赖 Prompt 模板，输出格式不稳定 |
| **工具生态** | 内置 50+ 工具 + 自定义 API + MCP + Workflow-as-Tool | 工具报错时 Agent 可能重试多次，缺少断熔机制 |
| **透明度** | CoT 模式下可看到完整思考链 | FC 模式思考过程不透明，难以调试 |
| **可观测性** | 日志页显示每步的工具调用和结果 | 缺少 Agent 专有指标（如工具成功率、平均步数） |

### 适用场景

| 场景 | 说明 |
|------|------|
| **智能搜索助手** | 自动搜索、筛选、整合多源信息，生成综合回答 |
| **自动化办公** | 查询内部系统数据 + 调用外部 API + 整理为报告/邮件 |
| **数据分析 Agent** | 查询数据库 → 分析趋势 → 调用画图工具 → 生成可视化报告 |
| **客户服务 Agent** | 查询订单系统 → 检查退换货政策 → 生成处理建议 |
| **研究助手** | 搜索论文 → 提取关键信息 → 对比分析 → 生成文献综述 |

**不适用场景**：
- 确定性业务流程（如固定审批流），用 Workflow 更合适
- 对延迟要求极高的场景（Agent 多步调用可能耗时 5-15 秒）

### 注意事项

1. **Token 成本控制**：Agent 可能比 Chat App 多消耗 3-10 倍 Token，务必设置最大迭代次数和用量告警
2. **工具幂等性**：Agent 可能重复调用同一个工具，确保你的自定义工具是幂等的（多次调用结果一致且无副作用）
3. **输出可靠性**：Agent 的最终格式可能不稳定，建议在 Agent 之后接 Workflow 的参数提取器做格式化

### 常见踩坑经验

1. **坑：Agent 第一步就不调用工具，直接编造答案** → 根因：系统提示词中没有强制要求"每次必须先搜索"。解决：加一句"在回答任何问题前，请先使用搜索工具获取最新信息"
2. **坑：Agent 调用工具的查询参数很奇怪** → 根因：Agent 从用户问题中提取参数失败。解决：在工具定义的 `description` 中写清楚参数的含义和示例值
3. **坑：FC Agent 报错"模型不支持 Function Calling"** → 根因：当前选择的模型不支持。解决：切换到 GPT-4 或 Claude，或切换为 CoT 策略

### 思考题

1. **进阶题**：Agent 在调用工具失败时，Dify 如何处理？你能否设计一个更智能的重试策略——比如工具 A 连续失败 2 次后自动切换到替代工具 B？

2. **进阶题**：如果让你给 Agent 增加一个"记忆"能力——记住用户在之前对话中透露的偏好信息（如"我吃素"、"我在北京工作"），你会怎么设计？（提示：考虑 Dify 的 conversation 变量）

> **参考答案**：见附录 D

---

> **推广计划提示**：本章适合所有角色。Agent 是基础篇中"技术难度"最高的一章，建议开发人员多次实践不同策略和工具的搭配。产品/运营可以从"行程规划助手"入手，感受 Agent 的实际价值后再深入学习配置细节。
