# 第29章：Agent 与工具调用入门

## 1 项目背景

### 业务场景

客服工单分类服务和 FAQ 问答机器人上线后，客服效率提升了 40%。但产品经理很快发现了新痛点——很多用户的问题需要**执行操作**而不仅仅是查找信息：

- "帮我查一下订单 XH20240501 的物流状态" → 需要调用物流查询 API
- "我的会员积分还有多少？" → 需要调用 CRM 系统查询
- "帮我算一下 299 元的商品打 8 折后多少钱" → 需要计算
- "最近 7 天有多少未处理的投诉工单？" → 需要调用数据库查询

传统流程是：客服手动打开 3-4 个后台系统查询 → 汇总信息 → 回复用户。平均耗时 2 分钟/次，如果 AI 能自己完成这些操作，每单可节省 90% 的时间。

### 痛点放大

纯语言模型只能"说"不能"做"——它们被困在对话窗口里，无法感知外部世界也无法执行操作。Agent 框架就是用来打破这堵墙的：

```
传统 LLM:  用户输入 → LLM → 文本输出（只会说）
Agent:     用户输入 → LLM思考 → 调用工具 → 观察结果 → LLM再思考 → 文本输出（会说+会做）
```

核心挑战：
1. **工具调用决策**：LLM 怎么知道什么时候该调用工具、调用哪个工具、参数怎么填？
2. **工具结果理解**：API 返回了一个 JSON 或错误码，LLM 怎么理解并决定下一步？
3. **安全边界**：如果 LLM 决定调用"删除订单"这类危险 API，谁来做权限控制？

---

## 2 项目设计

### 剧本式交锋对话

**场景**：周五下午 4:30，AI Lab。小陈刚跑通了一个 Agent Demo，能帮用户查天气。小胖凑过来看。

---

**小胖**:"哟，你这机器人居然会查天气了？怎么做到的，难道 LLM 自带天气预报功能？"

**小陈**:"不是。LLM 本身还是只会说话。但我给了它一个工具列表——`get_weather(city)`。当用户问'北京今天天气怎么样'时，模型不是直接编一个天气回答（那是幻觉！），而是输出一个函数调用：`get_weather("北京")`。然后我的代码真正执行这个函数，拿到天气数据，再把结果喂回给 LLM，让它组织成自然语言回答。"

**小胖**:"哦！就像你让一个实习生帮你查资料——他不会自己编答案，但知道该去哪个系统查，查到后把结果告诉你。"

**小白**:"这就是 ReAct（Reasoning + Acting）模式。但我有个疑问——怎么让 LLM 输出正确的函数调用格式？如果它输出的 JSON 格式错了怎么办？"

**大师**:"好问题。Agent 的实现有三个关键技术点。

**第一：工具描述（Tool Schema）。** 每个工具需要用清晰的语言描述它的功能、参数和返回值。这类似于写 API 文档——LLM 通过阅读工具描述来理解它能做什么。

```python
tools = [
    {
        "name": "get_weather",
        "description": "查询指定城市的天气",
        "parameters": {
            "city": {"type": "string", "description": "城市名称，如'北京'"}
        },
        "returns": "包含温度、湿度、天气状况的 JSON"
    },
]
```

**第二：推理循环。** Agent 的核心是一个 while 循环：
1. 用户输入 + 工具列表 → LLM → 输出（可能是文本回答，也可能是函数调用请求）
2. 如果是函数调用 → 执行函数 → 结果拼回对话上下文 → 回到步骤 1
3. 如果是文本回答 → 返回给用户

这个循环会持续到 LLM 认为问题已解决或达到最大轮次。

**第三：安全沙箱。** Agent 能调用工具意味着它能执行代码、访问数据库、操作 API。必须有三层安全：
1. **工具白名单**：只允许调用预定义的工具，禁止执行任意代码
2. **参数校验**：调用工具前验证参数类型、范围（如 city 不能是 SQL 注入语句）
3. **危险操作确认**：高权限操作（如删除、退款）需人工二次确认

Transformers 库自 4.33 版本起内置了 `agents` 模块，提供了标准化的工具定义和 Agent 运行框架。"

**技术映射总结**：
- Agent = 给 LLM 装上了"手和脚"，能调 API、查数据库、执行计算
- 工具描述 = 给实习生写的"操作手册"，告诉他每个系统怎么用
- ReAct 循环 = "思考→行动→观察→再思考"的循环
- 安全沙箱 = 给实习生的权限限制，不能删库跑路

---

## 3 项目实战

### 3.1 环境准备

```bash
pip install transformers>=4.44.0 torch
pip install duckduckgo-search>=5.0  # 搜索工具（可选）
```

### 3.2 自定义 Agent 工具

```python
# agent_tools.py
"""定义 Agent 工具集"""

import json
import math
import random
from typing import Dict, Any


class WeatherTool:
    """天气查询工具（模拟）"""
    name = "get_weather"
    description = "查询指定城市的天气信息，返回温度和天气状况"

    # 模拟数据
    _weather_data = {
        "北京": {"temp": 28, "humidity": 65, "condition": "晴"},
        "上海": {"temp": 32, "humidity": 80, "condition": "多云转阴"},
        "深圳": {"temp": 30, "humidity": 75, "condition": "阵雨"},
        "成都": {"temp": 25, "humidity": 70, "condition": "阴"},
    }

    def __call__(self, city: str) -> str:
        city = city.strip()
        if city in self._weather_data:
            data = self._weather_data[city]
            return json.dumps({
                "city": city,
                "temperature_celsius": data["temp"],
                "humidity_percent": data["humidity"],
                "condition": data["condition"],
            }, ensure_ascii=False)
        return json.dumps({"error": f"未找到城市'{city}'的天气数据"})


class CalculatorTool:
    """安全计算器工具"""
    name = "calculate"
    description = "执行数学计算，支持加减乘除、乘方、开方等运算"

    ALLOWED_FUNCTIONS = {
        "abs": abs, "round": round, "min": min, "max": max,
        "pow": pow, "sqrt": math.sqrt,
    }

    def __call__(self, expression: str) -> str:
        # 安全：只允许白名单中的函数和基础运算
        expression = expression.strip()

        # 移除危险内容
        forbidden = ["import", "exec", "eval", "open", "os.", "sys.",
                     "__", "subprocess", "shutil"]
        for word in forbidden:
            if word in expression.lower():
                return json.dumps({"error": "表达式包含禁止的操作"})

        # 限制长度
        if len(expression) > 200:
            return json.dumps({"error": "表达式过长"})

        try:
            # 使用安全的 eval 环境
            safe_dict = {"__builtins__": {}}
            safe_dict.update(self.ALLOWED_FUNCTIONS)
            safe_dict.update({"math": math})

            result = eval(expression, {"__builtins__": {}}, safe_dict)
            return json.dumps({"result": result, "expression": expression})
        except Exception as e:
            return json.dumps({"error": f"计算错误: {str(e)}"})


class OrderLookupTool:
    """订单查询工具（模拟）"""
    name = "lookup_order"
    description = "查询订单信息，需要提供订单号"

    _orders = {
        "XH20240501": {"status": "运输中", "courier": "顺丰快递",
                       "estimated_delivery": "2024-05-22"},
        "XH20240502": {"status": "已签收", "signed_at": "2024-05-18"},
        "XH20240503": {"status": "待发货"},
    }

    def __call__(self, order_id: str) -> str:
        order_id = order_id.strip().upper()
        if order_id in self._orders:
            return json.dumps({"order_id": order_id, **self._orders[order_id]},
                              ensure_ascii=False)
        return json.dumps({"error": f"未找到订单'{order_id}'"})


class FAQSearchTool:
    """FAQ 知识库检索工具"""
    name = "search_faq"
    description = "搜索客服FAQ知识库，返回相关问题和答案"

    _faq = {
        "退款": "在订单详情页点击申请退款，填写原因后提交。3-7个工作日到账。",
        "发货": "下单后24小时内发货，物流信息可在订单详情查看。",
        "退货": "7天无理由退货，保持商品原包装。退回运费由平台承担。",
        "联系客服": "在线客服工作时间9:00-21:00，或拨打400-123-4567。",
    }

    def __call__(self, query: str) -> str:
        query_lower = query.lower()
        results = []
        for keyword, answer in self._faq.items():
            if keyword in query_lower or query_lower in keyword:
                results.append({"keyword": keyword, "answer": answer})

        if results:
            return json.dumps(results, ensure_ascii=False)
        return json.dumps({"message": "未找到相关FAQ，建议转人工客服"})


# 注册所有工具
TOOLS = {
    "get_weather": WeatherTool(),
    "calculate": CalculatorTool(),
    "lookup_order": OrderLookupTool(),
    "search_faq": FAQSearchTool(),
}
```

### 3.3 Agent 推理循环

```python
# agent_loop.py
"""Agent 推理循环 —— ReAct 模式"""

import json
import re
import torch
from typing import List, Dict, Any, Optional
from transformers import AutoTokenizer, AutoModelForCausalLM


class SimpleAgent:
    """基于 ReAct 模式的简易 Agent"""

    SYSTEM_PROMPT = """你是一个智能客服助手。你可以使用以下工具来帮助用户解决问题：

{tool_descriptions}

回复规则：
1. 如果需要调用工具，请严格按以下格式输出：
   <tool_call>
   {{"name": "工具名", "arguments": {{"参数名": "参数值"}}}}
   </tool_call>
2. 如果可以基于工具返回结果直接回答用户，请用自然语言回复
3. 如果不能回答且不需要调用工具，请说明原因

当前对话:
{conversation}
助手:"""

    def __init__(self, model_name: str = "uer/gpt2-chinese-cluecorpussmall",
                 tools: Dict[str, Any] = None):
        self.tokenizer = AutoTokenizer.from_pretrained(model_name)
        self.tokenizer.pad_token = self.tokenizer.eos_token
        self.model = AutoModelForCausalLM.from_pretrained(model_name)
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self.model.to(self.device)
        self.model.eval()

        self.tools = tools or {}
        self.max_iterations = 5  # 防止无限循环

    def _get_tool_descriptions(self) -> str:
        """生成工具描述文本"""
        descs = []
        for name, tool in self.tools.items():
            descs.append(f"- {name}: {tool.description}")
        return "\n".join(descs)

    def _generate(self, prompt: str, max_tokens: int = 200) -> str:
        """调用 LLM 生成回复"""
        inputs = self.tokenizer(prompt, return_tensors="pt",
                                truncation=True, max_length=1024).to(self.device)

        with torch.no_grad():
            outputs = self.model.generate(
                **inputs,
                max_new_tokens=max_tokens,
                temperature=0.3,
                top_p=0.9,
                do_sample=True,
                pad_token_id=self.tokenizer.eos_token_id,
                eos_token_id=self.tokenizer.eos_token_id,
            )

        full_text = self.tokenizer.decode(outputs[0], skip_special_tokens=True)
        # 提取"助手:"之后的内容
        if "助手:" in full_text:
            return full_text.split("助手:")[-1].strip()
        return full_text[len(prompt):].strip()

    def _parse_tool_call(self, response: str) -> Optional[Dict]:
        """解析 LLM 输出中的工具调用"""
        match = re.search(r'<tool_call>\s*(\{.*?\})\s*</tool_call>', response, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                pass
        return None

    def _execute_tool(self, tool_name: str, arguments: Dict) -> str:
        """执行工具调用"""
        if tool_name not in self.tools:
            return json.dumps({"error": f"未知工具: {tool_name}"})

        try:
            tool = self.tools[tool_name]
            result = tool(**arguments)
            return result if isinstance(result, str) else json.dumps(result)
        except Exception as e:
            return json.dumps({"error": f"工具执行失败: {str(e)}"})

    def run(self, user_input: str) -> Dict:
        """Agent 主循环"""
        conversation = f"用户: {user_input}\n"
        tool_calls_made = []

        for iteration in range(self.max_iterations):
            prompt = self.SYSTEM_PROMPT.format(
                tool_descriptions=self._get_tool_descriptions(),
                conversation=conversation,
            )

            response = self._generate(prompt)
            tool_call = self._parse_tool_call(response)

            if tool_call:
                # 执行工具调用
                tool_name = tool_call.get("name", "")
                arguments = tool_call.get("arguments", {})

                tool_result = self._execute_tool(tool_name, arguments)
                tool_calls_made.append({
                    "tool": tool_name,
                    "arguments": arguments,
                    "result": tool_result,
                })

                # 将结果反馈给 LLM
                conversation += f"助手: <tool_call>\n{json.dumps(tool_call, ensure_ascii=False)}\n</tool_call>\n"
                conversation += f"工具返回: {tool_result}\n"

            else:
                # LLM 给出了最终回答
                return {
                    "answer": response,
                    "tool_calls": tool_calls_made,
                    "iterations": iteration + 1,
                }

        # 超过最大迭代次数
        return {
            "answer": "抱歉，处理您的问题超时了，正在为您转接人工客服。",
            "tool_calls": tool_calls_made,
            "iterations": self.max_iterations,
            "status": "timeout",
        }


# ===== 使用示例 =====
if __name__ == "__main__":
    from agent_tools import TOOLS

    agent = SimpleAgent(tools=TOOLS)

    queries = [
        "北京今天天气怎么样？",
        "帮我查一下订单XH20240501的物流状态",
        "计算 299 × 0.8 等于多少？",
        "怎么申请退款？",
    ]

    for q in queries:
        result = agent.run(q)
        print(f"\n👤 用户: {q}")
        print(f"🤖 助手: {result['answer']}")
        if result.get("tool_calls"):
            for tc in result["tool_calls"]:
                print(f"  🔧 调用了: {tc['tool']}({tc['arguments']})")
```

### 3.4 Transformers 原生 Agent

```python
# transformers_agent_demo.py
"""使用 Transformers 内置 Agent 框架"""

from transformers import ReactAgent, Tool, HfApiEngine
from typing import Optional


# 定义自定义工具
class WeatherTool(Tool):
    name = "get_weather"
    description = "查询城市天气。参数: city (城市名称)"

    inputs = {
        "city": {"type": "string", "description": "城市名称"}
    }
    output_type = "string"

    def forward(self, city: str) -> str:
        import json
        data = {"北京": "28°C 晴", "上海": "32°C 多云"}
        return json.dumps({city: data.get(city, "未知")}, ensure_ascii=False)


class CalculatorTool(Tool):
    name = "calculator"
    description = "执行数学计算。参数: expression (数学表达式)"

    inputs = {
        "expression": {"type": "string", "description": "数学表达式，如'2+3*4'"}
    }
    output_type = "string"

    def forward(self, expression: str) -> str:
        import json
        try:
            result = eval(expression, {"__builtins__": {}}, {})
            return json.dumps({"result": result})
        except Exception as e:
            return json.dumps({"error": str(e)})


# 注意: 运行此代码需要 HuggingFace Hub token 和网络连接
# from transformers import load_tool, ReactCodeAgent

# agent = ReactCodeAgent(
#     tools=[WeatherTool(), CalculatorTool()],
#     llm_engine=HfApiEngine("Qwen/Qwen2.5-7B-Instruct"),
# )

# agent.run("北京天气怎么样？")
# agent.run("帮我算一下 156 × 23")

print("Transformers 原生 Agent 已就绪")
print("需要: pip install transformers[hf_xet] + HF token")
```

### 3.5 测试验证

```python
# test_agent.py
import pytest
import json
from agent_tools import WeatherTool, CalculatorTool, OrderLookupTool

class TestAgentTools:
    def test_weather(self):
        tool = WeatherTool()
        result = json.loads(tool("北京"))
        assert result["city"] == "北京"
        assert "temperature_celsius" in result

    def test_weather_not_found(self):
        tool = WeatherTool()
        result = json.loads(tool("火星"))
        assert "error" in result

    def test_calculator(self):
        tool = CalculatorTool()
        result = json.loads(tool("2 + 3 * 4"))
        assert result["result"] == 14

    def test_calculator_safe(self):
        tool = CalculatorTool()
        result = json.loads(tool("__import__('os').system('ls')"))
        assert "error" in result or "禁止" in str(result)

    def test_order_lookup(self):
        tool = OrderLookupTool()
        result = json.loads(tool("XH20240501"))
        assert result["status"] == "运输中"
```

---

## 4 项目总结

### 4.1 优点与缺点

| 方面 | 优点 | 缺点 |
|------|------|------|
| **Agent 框架** | LLM 从"被动回答"升级为"主动办事" | 多轮推理增加延迟和 token 消耗 |
| **ReAct 模式** | 推理过程可解释（能看到调用了什么工具） | 对 LLM 的指令遵循能力要求高 |
| **工具解耦** | 工具可独立开发测试，替换方便 | 工具描述质量直接影响 Agent 表现 |
| **安全沙箱** | 允许执行操作但可控 | 白名单规则需持续更新 |

### 4.2 适用场景

| 场景 | 推荐方案 |
|------|---------|
| 客服查询类（查订单、查物流、查积分） | Agent + API 工具 |
| 数据分析助手（查数据库、画图表） | Agent + SQL + 可视化工具 |
| 自动化运维（查日志、重启服务） | Agent + 运维 API + 人工确认 |
| 智能家居控制 | Agent + IoT API |

**不适用场景**：
- 纯文本创意生成（不需要工具调用）
- 实时性要求 < 500ms（Agent 多轮推理延迟高）
- 高安全性操作（金融交易等）→ 禁止 Agent 自动执行

### 4.3 注意事项

1. **max_iterations**：必须设置上限（建议 3-5 轮），防止无限循环消耗 token
2. **工具超时**：每个工具调用应有独立超时（如 5s），避免一个慢 API 卡住整个 Agent
3. **Prompt 注入防护**：用户输入中不应包含工具调用格式，需做输入清洗

### 4.4 常见踩坑经验

| 故障现象 | 根因 | 解决方案 |
|---------|------|---------|
| Agent 无限循环调用工具 | LLM 无法从工具结果中提取足够信息 | 优化工具返回格式，加入明确的"无更多信息"标志 |
| 工具调用格式解析失败 | LLM 输出的 JSON 有语法错误 | 后处理修复 + 重试机制 |
| Agent 调用危险工具 | 安全白名单不完整 | 工具分级（read/write/admin），write 以上需确认 |

### 4.5 思考题

1. **初级**：在 `agent_tools.py` 中新增加一个 `get_time` 工具，接受 `timezone` 参数返回当前时间。如何确保 Agent 在用户问"现在几点了"时能正确调用这个工具？
2. **进阶**：设计一个**多工具编排**方案——用户问"帮我查订单 XH20240501，如果已签收就帮我申请退款"。这需要 Agent 顺序调用两个工具且第二个依赖第一个的结果。如何实现这种**条件工具链**？

（答案将在第30章末尾给出）

### 4.6 第28章思考题答案

**第28章思考题1**：
- 会变化。CLIP 对 prompt 措辞敏感。添加形容词（"可爱的""忠诚的"）可能改变文本向量的方向，导致与图片的相似度变化。最佳实践：统一使用简单的 `{类别}` 或 `一张{类别}的照片` 模板，避免添加不必要的修饰词。

**第28章思考题2**：
- 图文联合检索方案：(1) 用 `processor(images=image, text=description)` 同时编码图片和文本；(2) 分别得到图片向量 `img_vec` 和文本向量 `text_vec`；(3) 融合查询向量 `query_vec = α * img_vec + (1-α) * text_vec`（α 为图片权重，建议 0.5-0.7）；(4) 用融合向量在商品图片向量库中检索 Top-K。库中商品图片预先用 `model.get_image_features()` 编码。

### 4.7 推广计划提示

| 部门 | 建议行动 |
|------|---------|
| **开发团队** | 新工具遵循统一接口规范，所有工具必须有 `name`、`description` 和类型注解 |
| **测试团队** | 为每个工具编写独立单元测试，Agent 集成测试覆盖多工具组合场景 |
| **运维/安全** | 审核工具白名单，确认 write/admin 级工具有人工确认机制 |

---

> **下一章预告**：第30章将讲解安全、合规与内容风控——Prompt Injection 攻击、输入/输出审核、模型许可证与数据隐私，为中级篇收官。
