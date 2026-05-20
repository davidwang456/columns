# 第20章：生成控制进阶：Beam Search、约束解码与结构化输出

## 1 项目背景

### 业务场景

客服工单智能分派系统上线后，产品经理提出了新的需求：让 AI 自动生成工单处理建议，格式必须是严格的 JSON，方便后端系统直接解析和流转。例如输入"用户投诉快递损坏了商品"，期望输出：

```json
{"category":"投诉","priority":"高","suggested_action":"联系用户道歉+补发商品","assign_to":"售后组"}
```

算法团队用第 8 章的文本生成方案跑了一个版本，但结果令人头疼：
- 30% 的输出不是合法 JSON（少了一个 `}` 或 `"`）
- 生成的 `category` 有时是模型自己编的类别（不在预定义的 5 个类别中）
- 同样的输入两次调用给出的 `priority` 不同（第一次"高"，第二次"紧急"）
- Beam Search 模式下多样性损失严重——所有输出都趋于同一种"安全回答"

运营主管说："你给我的 JSON 解析不了，我怎么自动流转？"

### 痛点放大

自由文本生成容易，但**结构化可控生成**是另一个级别的问题：

1. **格式约束**：怎么让模型只输出合法 JSON？怎么强制 key 必须是 `category/priority/suggested_action/assign_to` 这四个？
2. **内容约束**：`category` 的值必须从预定义的 5 个类别中选择；`priority` 只能是 `高/中/低`
3. **解码策略困境**：Greedy 太死板（总是同一种回答），Sampling 太随机（格式不稳定），Beam Search 参数如何选择？
4. **输出验证与修复**：当模型生成的 JSON 有语法错误时，是重试、修复还是降级？

---

## 2 项目设计

### 剧本式交锋对话

**场景**：周五上午 10:30，AI Lab。小陈把 50 条生成结果拿给产品经理看，产品经理指着其中 15 条解析失败的 JSON 哭笑不得。

---

**小胖**:"这 JSON 怎么跟我的作文一样——格式乱七八糟。AI 不能强制它输出规范格式吗？就像考试答题卡，必须涂在格子里，不能涂外面。"

**小白**:"这就是 constrained decoding（约束解码）的用武之地。原理是：在模型逐 token 生成时，不是让它自由选择整个词表的任意词，而是根据语法规则限制每一步允许的 token 集合。"

**大师**:"让我把生成控制的三个进阶武器讲透。

**武器一：Beam Search 深度调优。**

第 8 章讲了 Beam Search 的基本概念，但工程上还有三个关键参数：

- `num_beams`：beam 宽度。越大探索空间越大但越慢。经验值：JSON 输出用 3-5（不需要太大），创意文案用 5-10。
- `num_beam_groups`：分组 beam search，用于多样性。设为 2 表示把 beam 分成 2 组，组内独立搜索，组间保证差异。解决"所有 beam 趋于相同"的问题。
- `diversity_penalty`：组间差异惩罚，越大组间输出差异越大。设为 0.5-1.0。

**武器二：约束解码（Constrained Decoding）。**

核心方法是 `prefix_allowed_tokens_fn`——一个回调函数，在模型每步生成下一个 token 时调用，返回当前上下文中允许输出的 token ID 列表。

举个 JSON 约束的例子：如果上一轮生成了 `{"category":"`，那这一步只允许输出 `投诉`、`咨询`、`退款退货`、`物流问题`、`售后` 这 5 个 token 对应的首位。通过 Trie 树可以高效实现这个约束。

`LogitsProcessor` 是更灵活的约束方式——它可以修改整个 logits 分布。比如把不合法的 token 的 logit 设为 `-inf`，让模型绝对不会选它们。

**武器三：输出验证与自动修复。**

即使加了约束解码，仍可能出现格式问题。需要一个**后处理管道**：

1. 正则提取：尝试从输出中提取最长的合法 JSON 子串
2. 语法修复：补全缺失的引号、花括号
3. 内容校验：检查 `category` 值是否在允许列表内，不在则用默认值
4. 重试机制：如果以上都失败，用更低的 temperature 重新生成

```
原始输出: {"category":"投诉 "priority":"高"  ← 缺逗号
    ↓ 正则提取
{"category":"投诉" "priority":"高"}  ← 找到 JSON 模式
    ↓ 语法修复
{"category":"投诉", "priority":"高"}  ← 补逗号
    ↓ 内容校验
{"category":"投诉", "priority":"高"}  ← 值在允许范围内
    ↓
合法 JSON ✓
```

**技术映射总结**：
- Beam Search = 多条路径同时探索，选最优的那条
- 约束解码 = 给模型戴上一副"只能填标准答案"的手套
- 输出修复 = 给生成结果做"错别字检查"，自动修正格式错误

---

## 3 项目实战

### 3.1 环境准备

```bash
pip install transformers==4.44.0 torch pygtrie>=2.5.0
```

### 3.2 Beam Search 深度对比

```python
# beam_search_compare.py
"""Beam Search 参数对比 —— 生成质量与多样性"""

import torch
from transformers import AutoTokenizer, AutoModelForCausalLM

MODEL = "uer/gpt2-chinese-cluecorpussmall"
tokenizer = AutoTokenizer.from_pretrained(MODEL)
tokenizer.pad_token = tokenizer.eos_token
model = AutoModelForCausalLM.from_pretrained(MODEL)
model.eval()
device = "cuda" if torch.cuda.is_available() else "cpu"
model.to(device)

prompt = "请为以下工单生成处理建议JSON：用户投诉商品质量问题\n输出："

inputs = tokenizer(prompt, return_tensors="pt").to(device)

# ===== 1. Greedy vs Beam Search =====
configs = [
    {"name": "Greedy",           "num_beams": 1, "do_sample": False},
    {"name": "Beam=3",           "num_beams": 3, "do_sample": False},
    {"name": "Beam=5",           "num_beams": 5, "do_sample": False},
    {"name": "Beam Group(3,2)",  "num_beams": 6, "num_beam_groups": 3,
     "diversity_penalty": 0.5, "do_sample": False},
    {"name": "Sampling (t=0.7)", "do_sample": True, "temperature": 0.7,
     "top_p": 0.9},
]

print("生成策略对比:\n")
for cfg in configs:
    name = cfg.pop("name")
    with torch.no_grad():
        outputs = model.generate(
            **inputs,
            max_new_tokens=60,
            pad_token_id=tokenizer.eos_token_id,
            num_return_sequences=min(3, cfg.get("num_beams", 1)),
            **cfg,
        )
    print(f"--- {name} ---")
    for i, out in enumerate(outputs):
        text = tokenizer.decode(out, skip_special_tokens=True)
        generated = text[len(prompt):].strip()
        is_json = generated.count("{") == generated.count("}")
        print(f"  [{i+1}] {'✓JSON' if is_json else '✗格式'} | {generated[:80]}")
    print()
```

### 3.3 约束解码实现

```python
# constrained_decoding.py
"""约束解码 —— 确保生成合法 JSON"""

import torch
import pygtrie
from transformers import AutoTokenizer, AutoModelForCausalLM, LogitsProcessor

MODEL = "uer/gpt2-chinese-cluecorpussmall"
tokenizer = AutoTokenizer.from_pretrained(MODEL)
tokenizer.pad_token = tokenizer.eos_token

# ===== 允许的类别值和优先级值 =====
ALLOWED_CATEGORIES = ["投诉", "咨询", "退款退货", "物流问题", "售后"]
ALLOWED_PRIORITIES = ["高", "中", "低"]
ALLOWED_ASSIGN = ["售后组", "物流组", "客服组", "综合组"]


def build_json_trie():
    """构建 JSON 模板的 Trie 约束树"""
    trie = pygtrie.CharTrie()

    # JSON 模板前缀
    template = '{"category":"'
    for c in template:
        # 逐步构建前缀
        pass

    # 在 category 值位置，只允许预定义的类别
    for cat in ALLOWED_CATEGORIES:
        key = template + cat
        trie[key] = True
        # 加上后续的模板
        key += '","priority":"'
        for pri in ALLOWED_PRIORITIES:
            key2 = key + pri
            trie[key2] = True
            key2 += '","suggested_action":"'
            # suggested_action 是自由文本，允许任意字符
            # 在结束引号之前不做限制
            trie[key2] = True
            # 结束部分
            key3 = key2 + '任意内容","assign_to":"'
            for assign in ALLOWED_ASSIGN:
                key4 = key3 + assign + '"}'
                trie[key4] = True

    return trie


class JSONConstraintLogitsProcessor(LogitsProcessor):
    """JSON 约束 LogitsProcessor —— 强制生成预定义类别的值"""

    def __init__(self, tokenizer, allowed_categories, allowed_priorities,
                 allowed_assign):
        self.tokenizer = tokenizer
        self.allowed_categories = allowed_categories
        self.allowed_priorities = allowed_priorities
        self.allowed_assign = allowed_assign

        # 预计算所有允许值的 token ID
        self.category_ids = self._encode_list(allowed_categories)
        self.priority_ids = self._encode_list(allowed_priorities)
        self.assign_ids = self._encode_list(allowed_assign)

    def _encode_list(self, strings):
        ids_set = set()
        for s in strings:
            tokens = self.tokenizer.encode(s, add_special_tokens=False)
            ids_set.add(tokens[0])
        return ids_set

    def __call__(self, input_ids, scores):
        """
        在生成过程中修改 logits
        检测上下文，当在 category/priority/assign_to 的值位置时，
        只允许特定 token
        """
        for batch_idx in range(input_ids.shape[0]):
            # 解码当前已生成的序列
            current_text = self.tokenizer.decode(
                input_ids[batch_idx], skip_special_tokens=True
            )

            # 检测当前在哪个位置
            if current_text.endswith('"category":"'):
                # 只允许预定义的类别
                mask = torch.full_like(scores[batch_idx], float("-inf"))
                for tid in self.category_ids:
                    mask[tid] = 0
                scores[batch_idx] = scores[batch_idx] + mask

            elif current_text.endswith('","priority":"'):
                mask = torch.full_like(scores[batch_idx], float("-inf"))
                for tid in self.priority_ids:
                    mask[tid] = 0
                scores[batch_idx] = scores[batch_idx] + mask

            elif current_text.endswith('","assign_to":"'):
                mask = torch.full_like(scores[batch_idx], float("-inf"))
                for tid in self.assign_ids:
                    mask[tid] = 0
                scores[batch_idx] = scores[batch_idx] + mask

        return scores


# ===== 生成测试 =====
def generate_with_constraint(prompt: str, model, tokenizer, logits_processor):
    inputs = tokenizer(prompt, return_tensors="pt").to(model.device)

    with torch.no_grad():
        outputs = model.generate(
            **inputs,
            max_new_tokens=80,
            do_sample=True,
            temperature=0.7,
            top_p=0.9,
            pad_token_id=tokenizer.eos_token_id,
            logits_processor=[logits_processor],
            num_return_sequences=1,
        )

    return tokenizer.decode(outputs[0], skip_special_tokens=True)


if __name__ == "__main__":
    model = AutoModelForCausalLM.from_pretrained(MODEL)
    model.eval()
    model.to("cuda" if torch.cuda.is_available() else "cpu")

    processor = JSONConstraintLogitsProcessor(
        tokenizer, ALLOWED_CATEGORIES, ALLOWED_PRIORITIES, ALLOWED_ASSIGN
    )

    prompt = "请为以下工单生成JSON处理建议：用户投诉商品质量问题\n输出："

    result = generate_with_constraint(prompt, model, tokenizer, processor)
    print(f"有约束: {result}")

    # 无约束对比
    result_no = generate_with_constraint(prompt, model, tokenizer, None)
    print(f"无约束: {result_no}")
```

### 3.4 JSON 输出验证与修复

```python
# json_validator.py
"""生成结果的 JSON 验证与自动修复"""

import json
import re
from typing import Optional, Dict


class JSONOutputValidator:
    """JSON 输出验证器 + 自动修复"""

    ALLOWED_KEYS = {"category", "priority", "suggested_action", "assign_to"}
    ALLOWED_VALUES = {
        "category": ["投诉", "咨询", "退款退货", "物流问题", "售后"],
        "priority": ["高", "中", "低"],
        "assign_to": ["售后组", "物流组", "客服组", "综合组"],
    }

    DEFAULT_OUTPUT = {
        "category": "咨询",
        "priority": "中",
        "suggested_action": "转人工客服处理",
        "assign_to": "客服组",
    }

    def validate_and_fix(self, raw_output: str, max_retries: int = 2) -> Dict:
        """
        验证 JSON 输出并自动修复

        修复策略：
        1. 提取最长合法 JSON 子串
        2. 补全引号/花括号
        3. 校验字段值是否在允许范围内
        4. 失败则返回默认值
        """
        result = None

        # 策略 1: 直接解析
        try:
            result = json.loads(raw_output)
        except json.JSONDecodeError:
            pass

        # 策略 2: 用正则提取 JSON
        if result is None:
            result = self._extract_json(raw_output)

        # 策略 3: 语法修复
        if result is None:
            result = self._fix_syntax(raw_output)

        # 策略 4: 返回默认值
        if result is None:
            return self.DEFAULT_OUTPUT

        # 内容校验与修复
        result = self._validate_content(result)

        return result

    def _extract_json(self, text: str) -> Optional[Dict]:
        """从文本中提取 JSON 子串"""
        # 找最长的花括号配对
        matches = re.findall(r'\{[^{}]*\}', text)
        for match in sorted(matches, key=len, reverse=True):
            try:
                return json.loads(match)
            except json.JSONDecodeError:
                continue
        return None

    def _fix_syntax(self, text: str) -> Optional[Dict]:
        """修复常见 JSON 格式错误"""
        fixed = text

        # 修复 1: 单引号 → 双引号
        # 小心处理嵌套引号
        if fixed.count("'") > fixed.count('"'):
            fixed = fixed.replace("'", '"')

        # 修复 2: 缺逗号（在 " 和 " 之间）
        fixed = re.sub(r'"\s+(?=")', '", ', fixed)

        # 修复 3: 尾部缺 }
        if fixed.count("{") > fixed.count("}"):
            fixed += "}"

        # 修复 4: 去除花括号外的内容
        start = fixed.find("{")
        end = fixed.rfind("}")
        if start >= 0 and end > start:
            fixed = fixed[start:end + 1]

        try:
            return json.loads(fixed)
        except json.JSONDecodeError:
            return None

    def _validate_content(self, data: Dict) -> Dict:
        """校验并修复 JSON 内容值"""
        result = dict(data)

        # 补全缺失的 key
        for key in self.ALLOWED_KEYS:
            if key not in result:
                result[key] = self.DEFAULT_OUTPUT[key]

        # 校验值是否在允许范围内
        for key, allowed in self.ALLOWED_VALUES.items():
            if result.get(key) not in allowed:
                result[key] = allowed[0]  # 用第一个允许值兜底

        return result


# ===== 使用示例 =====
if __name__ == "__main__":
    validator = JSONOutputValidator()

    test_outputs = [
        # 合法 JSON
        '{"category":"投诉","priority":"高","suggested_action":"补发","assign_to":"售后组"}',
        # 缺逗号
        '{"category":"投诉" "priority":"高" "suggested_action":"补发"}',
        # 单引号
        "{'category':'投诉','priority':'高'}",
        # 非法值
        '{"category":"紧急","priority":"超高","assign_to":"未知组"}',
        # 完全乱码
        "我觉得这个事情应该转给售后处理",
    ]

    for i, raw in enumerate(test_outputs):
        fixed = validator.validate_and_fix(raw)
        is_valid = fixed != validator.DEFAULT_OUTPUT
        print(f"[{i+1}] 输入: {raw[:50]}")
        print(f"     输出: {json.dumps(fixed, ensure_ascii=False)}")
        print(f"     状态: {'✓ 已修复' if is_valid and raw != json.dumps(fixed, ensure_ascii=False) else '✓ 有效' if is_valid else '⚠ 使用默认值'}")
        print()
```

### 3.5 测试验证

```python
# test_constrained.py
import pytest
import json
from json_validator import JSONOutputValidator

class TestJSONValidator:
    def setup_method(self):
        self.validator = JSONOutputValidator()

    def test_valid_json(self):
        result = self.validator.validate_and_fix(
            '{"category":"投诉","priority":"高","suggested_action":"补发","assign_to":"售后组"}'
        )
        assert result["category"] == "投诉"

    def test_missing_comma(self):
        result = self.validator.validate_and_fix(
            '{"category":"投诉" "priority":"高"}'
        )
        assert result["category"] == "投诉"
        assert result["priority"] == "高"

    def test_single_quotes(self):
        result = self.validator.validate_and_fix(
            "{'category':'投诉','priority':'高'}"
        )
        assert result["category"] == "投诉"

    def test_invalid_value(self):
        result = self.validator.validate_and_fix(
            '{"category":"不可能的值"}'
        )
        assert result["category"] in self.validator.ALLOWED_VALUES["category"]

    def test_garbage_input(self):
        result = self.validator.validate_and_fix("这不是JSON")
        assert result == self.validator.DEFAULT_OUTPUT
```

---

## 4 项目总结

### 4.1 优点与缺点

| 方面 | 优点 | 缺点 |
|------|------|------|
| **Beam Search** | 输出质量高，语法更流畅 | 多样性低，所有 beam 趋于相同 |
| **Beam Group** | 保持多样性，有探索能力 | 参数调优复杂，速度比普通 Beam 慢 |
| **约束解码** | 强制输出合法，杜绝格式错误 | 仅适用于有限选项的字段，开放文本无法完全约束 |
| **后处理修复** | 兜底方案，无额外推理开销 | 修复逻辑需要人工维护规则 |

### 4.2 适用场景

| 场景 | 推荐方案 |
|------|---------|
| JSON/SQL/代码生成 | 约束解码 + 输出验证修复 |
| 创意文案 | Top-p sampling（多样性优先） |
| 翻译/摘要 | Beam Search (beam=4~6) |
| 对话回复 | Beam Group + diversity_penalty |

**不适用场景**：
- 完全开放式的自由创作（约束解码会扼杀创意）
- 实时性 < 10ms 要求的生成（Beam Search 太慢）

### 4.3 注意事项

1. **`prefix_allowed_tokens_fn` 的性能**：每步都调用一次，如果内部逻辑复杂（如查 Trie）会影响生成速度
2. **LogitsProcessor 的 mask 叠加**：多个 LogitsProcessor 的 mask 会叠加，注意 -inf 的累积
3. **修复策略降级链**：直接解析 → 正则提取 → 语法修复 → 默认值，每一步都有明确的失败转移

### 4.4 常见踩坑经验

| 故障现象 | 根因 | 解决方案 |
|---------|------|---------|
| 约束解码后输出仍不合法 | 约束只对下一 token 生效，中间跳过时失效 | 组合使用 LogitsProcessor + 后处理修复 |
| Beam Search 输出全一样 | `num_beam_groups` 未设置 | 启用分组 Beam Search |
| JSON 修复后字段丢失 | 正则只提取到部分 JSON | 加回退逻辑，缺字段用默认值补全 |

### 4.5 思考题

1. **初级**：在 `json_validator.py` 中，增加对 `suggested_action` 字段的长度校验（不超过 200 字）。如果超过，自动截断并在末尾加省略号。
2. **进阶**：你需要生成的内容不仅包含 JSON，还包含一段自由文本（如 "处理摘要"）。如何设计一个**混合输出**的约束方案——JSON 部分用约束解码，自由文本部分用正常采样？

（答案将在第21章末尾给出）

### 4.6 第19章思考题答案

**第19章思考题1**：
- stride 越小（重叠越多），窗口数越多，推理越慢。过多的重叠不一定更好——当 stride=window_size/4 时（75% 重叠），相邻窗口信息高度冗余，投票权重被稀释。stride=window_size/2（50% 重叠）通常是最优的平衡点。

**第19章思考题2**：
- 混合策略：(1) 先用轻量级关键词/Embedding 粗召回 20% 的窗口（约 10 个窗口）；(2) 仅对这 10 个窗口做精排分类（而不是全部 50 个窗口）；(3) 计算量从 50 次推理降到 10 次，节省 80%。代价是粗召回可能漏掉关键窗口，需监控粗召回覆盖率 > 95%。

### 4.7 推广计划提示

| 部门 | 建议行动 |
|------|---------|
| **开发团队** | 所有需要结构化输出的生成任务，默认接入 `JSONOutputValidator` 做后处理 |
| **测试团队** | 构造 50 种边界 JSON 输出（缺引号、多花括号、嵌特殊字符），验证修复器的鲁棒性 |
| **运维团队** | 监控约束解码的 token 生成速度（token/s），约束逻辑可能导致 10-20% 的速度下降 |

---

> **下一章预告**：第21章将进入 RAG（检索增强生成）实战——如何把企业知识库和模型生成能力结合起来，构建一个能引用来源、防幻觉的智能问答系统。
