# 第36章：Generation 源码：从 generate 到下一个 Token

## 1 项目背景

### 业务场景

客服回复生成系统上线后，产品经理要求新增"敏感词过滤"功能——生成的回复中绝对不能出现"假一赔十"、"绝对安全"、"包治百病"等承诺性词汇。小陈尝试在 prompt 中加入"禁止使用以下词汇"的指令，但模型还是会偶尔输出这些词——LLM 并不能 100% 遵循 prompt 中的否定指令。

另一个需求是"业务术语白名单"——医疗客服场景中，药品名称必须使用标准名。"阿莫西林"不能被模型自由发挥写成"阿莫仙"或"阿莫灵"。

小陈意识到，只有在生成的最底层——每个 token 被选中的那一刻——做拦截，才能真正杜绝这些问题。这需要深入 `generate()` 的源码，理解 `LogitsProcessor` 和 `StoppingCriteria` 的机制。

### 痛点放大

`model.generate()` 一行代码背后是一个复杂的多策略调度系统：

```
generate()
  ├── GenerationConfig 合并（用户参数 + model.generation_config + 默认值）
  ├── 根据参数选择解码策略:
  │   ├── greedy_search()      (do_sample=False, num_beams=1)
  │   ├── sample()             (do_sample=True)
  │   ├── beam_search()        (num_beams > 1)
  │   └── group_beam_search()  (num_beam_groups > 1)
  ├── 每一步:
  │   ├── prepare_inputs_for_generation()  → KV Cache 更新
  │   ├── model.forward()                   → logits
  │   ├── LogitsProcessor.__call__()        → 修改 logits
  │   ├── LogitsWarper.__call__()           → temperature/top_k/top_p
  │   ├── multinomial/greedy 采样           → 选一个 token
  │   └── StoppingCriteria.__call__()       → 是否停止
  └── 返回 GenerateOutput
```

---

## 2 项目设计

### 剧本式交锋对话

**场景**：周四下午 4:00，AI Lab。小陈正在逐行阅读 `generation/utils.py` 的源码，代码超 3000 行。

---

**小胖**:"这 generate 函数也太长了吧？3000 多行！就不能拆开吗？"

**小陈**:"其实已经拆了——`generate()` 本身只是一个调度器，真正干活的是 `greedy_search()`、`sample()` 和 `beam_search()` 这些子函数。`generate()` 做的事情就是：解析参数 → 选策略 → 调用对应的子函数。"

**小白**:"LogitsProcessor 和 LogitsWarper 有什么区别？我看它们都是处理 logits 的。"

**大师**:"虽然都是操作 logits，但职责不同：

- **LogitsProcessor**：在采样前修改 logits 分布。用于**强制约束**——比如把某些 token 的概率改为 `-inf` 让模型永远不选。可以叠加多个，按顺序执行。
- **LogitsWarper**：在采样前对 logits 做**随机性控制**——temperature 缩放、top_k 截断、top_p 过滤。也支持叠加。

两者的区别：Processor 是"规则层"（硬约束），Warper 是"采样层"（软控制）。

**深入源码流程：**

**第一步：GenerationConfig 合并。** `generate()` 的参数来源有三个优先级：
1. 用户调用时显式传入的（最高优先级）
2. `model.generation_config` 中存储的（模型保存时一起保存）
3. `GenerationConfig()` 默认值（最低优先级）

合并逻辑在 `generation/utils.py` 的 `_get_generation_config()` 中。

**第二步：解码循环。** 以 `sample()` 为例：
```python
while True:
    # 准备输入（含 KV Cache 更新）
    model_inputs = self.prepare_inputs_for_generation(input_ids, past_key_values=past)
    # 前向
    outputs = model(**model_inputs)
    # 取最后一个 token 的 logits
    next_token_logits = outputs.logits[:, -1, :]
    # LogitsProcessor 处理
    next_token_scores = logits_processor(input_ids, next_token_logits)
    # LogitsWarper 处理
    next_token_scores = logits_warper(input_ids, next_token_scores)
    # 采样
    next_tokens = torch.multinomial(softmax(next_token_scores), num_samples=1)
    # 检查 StoppingCriteria
    if stopping_criteria(input_ids, scores):
        break
```

**第三步：KV Cache 的更新。** `prepare_inputs_for_generation()` 在每一步只取最后 1 个 token 作为新输入，其余的从 KV Cache 中取。这样每一步的计算量是 O(1) 而非 O(n)。"

**技术映射总结**：
- generate() = 总调度台，选择策略、分配任务
- LogitsProcessor = 安检门，拦截禁止的 token
- LogitsWarper = 调音台，控制随机性和多样性
- StoppingCriteria = 裁判，决定"够了，可以停了"

---

## 3 项目实战

### 3.1 环境准备

```bash
pip install transformers==4.44.0 torch
```

### 3.2 自定义 LogitsProcessor

```python
# custom_logits_processor.py
"""自定义 LogitsProcessor —— 黑名单词过滤 + 白名单约束"""

import torch
from transformers import AutoTokenizer, AutoModelForCausalLM
from transformers.generation import LogitsProcessor, LogitsProcessorList
from typing import List


class BlacklistLogitsProcessor(LogitsProcessor):
    """禁止生成黑名单中的词 —— 将对应 token 的 logit 设为 -inf"""

    def __init__(self, tokenizer, blacklist_words: List[str]):
        self.tokenizer = tokenizer
        # 将黑名单词转换为 token ID 集合
        self.blacklist_ids = set()
        for word in blacklist_words:
            ids = tokenizer.encode(word, add_special_tokens=False)
            self.blacklist_ids.update(ids)
        print(f"黑名单token IDs: {len(self.blacklist_ids)} 个")

    def __call__(self, input_ids: torch.LongTensor,
                 scores: torch.FloatTensor) -> torch.FloatTensor:
        for tid in self.blacklist_ids:
            if tid < scores.shape[-1]:
                scores[:, tid] = -float("inf")
        return scores


class WhitelistLogitsProcessor(LogitsProcessor):
    """当上下文匹配特定模式时，只允许白名单中的 token"""

    def __init__(self, tokenizer, trigger_pattern: str,
                 whitelist_words: List[str]):
        self.tokenizer = tokenizer
        self.trigger_ids = tokenizer.encode(trigger_pattern,
                                            add_special_tokens=False)
        self.whitelist_ids = set()
        for word in whitelist_words:
            ids = tokenizer.encode(word, add_special_tokens=False)
            self.whitelist_ids.add(ids[0])  # 提取首 token ID

    def __call__(self, input_ids, scores):
        # 检查最近生成的 token 序列是否匹配触发模式
        for i in range(input_ids.shape[0]):
            seq = input_ids[i].tolist()
            # 简单前缀匹配：最后 len(trigger) 个 token
            if (len(seq) >= len(self.trigger_ids) and
                seq[-len(self.trigger_ids):] == self.trigger_ids):
                # 只允许白名单
                mask = torch.full_like(scores[i], -float("inf"))
                for tid in self.whitelist_ids:
                    if tid < mask.shape[0]:
                        mask[tid] = 0
                scores[i] = scores[i] + mask
        return scores


class MaxLengthStoppingCriteria:
    """自定义停止条件 —— 检测到句号或达到最大长度"""

    def __init__(self, max_length: int, tokenizer):
        self.max_length = max_length
        self.period_token_id = tokenizer.encode("。", add_special_tokens=False)[0]

    def __call__(self, input_ids, scores, **kwargs):
        # 最后一个 token 是句号 → 停止
        if input_ids.shape[-1] >= 1:
            last_token = input_ids[0, -1].item()
            if last_token == self.period_token_id:
                return True
        # 达到最大长度 → 停止
        if input_ids.shape[-1] >= self.max_length:
            return True
        return False


# ===== 使用示例 =====
if __name__ == "__main__":
    MODEL_NAME = "uer/gpt2-chinese-cluecorpussmall"
    tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)
    tokenizer.pad_token = tokenizer.eos_token
    model = AutoModelForCausalLM.from_pretrained(MODEL_NAME)

    # 定义黑名单
    blacklist = ["假一赔十", "绝对安全", "保证有效", "包治百病"]
    logits_processor = LogitsProcessorList([
        BlacklistLogitsProcessor(tokenizer, blacklist),
    ])

    prompt = "这个产品的效果"
    inputs = tokenizer(prompt, return_tensors="pt")

    # 不使用黑名单
    print("=" * 50)
    print("无黑名单:")
    outputs_no = model.generate(**inputs, max_new_tokens=30, do_sample=False)
    print(f"  {tokenizer.decode(outputs_no[0], skip_special_tokens=True)}")

    # 使用黑名单
    print("\n有黑名单:")
    try:
        outputs_yes = model.generate(
            **inputs, max_new_tokens=30, do_sample=False,
            logits_processor=logits_processor,
        )
        print(f"  {tokenizer.decode(outputs_yes[0], skip_special_tokens=True)}")
    except Exception as e:
        print(f"  (小模型可能不会生成黑名单词，但机制已生效): {e}")

    # 验证黑名单 token ID
    print("\n黑名单 token ID 映射:")
    for word in blacklist:
        ids = tokenizer.encode(word, add_special_tokens=False)
        print(f"  '{word}' → {ids}")
```

### 3.3 流式输出实现

```python
# streaming_generation.py
"""流式输出 —— 基于 generate 的 Streaming"""

import torch
from transformers import AutoTokenizer, AutoModelForCausalLM, TextStreamer
from typing import Iterator


class CustomStreamer(TextStreamer):
    """自定义流式输出 —— 逐词返回"""

    def __init__(self, tokenizer, skip_prompt=True, **kwargs):
        super().__init__(tokenizer, skip_prompt, **kwargs)
        self.generated_tokens = []

    def on_finalized_text(self, text: str, stream_end: bool = False):
        """每生成完一段文本时回调"""
        self.generated_tokens.append(text.strip())
        print(f"[stream] {text.strip()}")


def stream_generate(model, tokenizer, prompt: str,
                    max_new_tokens: int = 50, **kwargs) -> Iterator[str]:
    """使用 generate() 实现流式输出"""
    inputs = tokenizer(prompt, return_tensors="pt")

    # 使用 TextStreamer 作为流式回调
    streamer = CustomStreamer(tokenizer)

    generation_kwargs = dict(
        **inputs,
        streamer=streamer,
        max_new_tokens=max_new_tokens,
        do_sample=True,
        temperature=0.7,
        top_p=0.9,
        pad_token_id=tokenizer.eos_token_id,
        **kwargs,
    )

    # generate 会在内部调用 streamer.on_finalized_text
    model.generate(**generation_kwargs)

    return streamer.generated_tokens


if __name__ == "__main__":
    MODEL_NAME = "uer/gpt2-chinese-cluecorpussmall"
    tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)
    tokenizer.pad_token = tokenizer.eos_token
    model = AutoModelForCausalLM.from_pretrained(MODEL_NAME)

    print("流式生成测试:")
    prompt = "今天天气很好，"
    tokens = stream_generate(model, tokenizer, prompt, max_new_tokens=30)
    print(f"\n完整结果: {''.join(tokens)}")
```

### 3.4 生成过程追踪

```python
# generation_tracer.py
"""追踪 generate() 内部的每一步"""

import torch
from transformers import AutoTokenizer, AutoModelForCausalLM
from transformers import LogitsProcessor

MODEL_NAME = "gpt2"
tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)
tokenizer.pad_token = tokenizer.eos_token
model = AutoModelForCausalLM.from_pretrained(MODEL_NAME)

class TracerLogitsProcessor(LogitsProcessor):
    """记录每一步生成的 token 和概率"""

    def __init__(self, tokenizer):
        self.tokenizer = tokenizer
        self.steps = []

    def __call__(self, input_ids, scores):
        probs = torch.softmax(scores, dim=-1)
        top_prob, top_id = torch.max(probs[0], dim=-1)
        token = self.tokenizer.decode([top_id.item()])

        self.steps.append({
            "step": len(self.steps),
            "context": self.tokenizer.decode(input_ids[0][-5:]),
            "chosen_token": token,
            "chosen_prob": round(top_prob.item(), 4),
        })
        return scores

tracer = TracerLogitsProcessor(tokenizer)
prompt = "The future of AI is"
inputs = tokenizer(prompt, return_tensors="pt")

outputs = model.generate(
    **inputs,
    max_new_tokens=5,
    do_sample=False,  # 贪心模式，每步选择确定
    logits_processor=[tracer],
)

print(f"Prompt: '{prompt}'")
print(f"生成: '{tokenizer.decode(outputs[0], skip_special_tokens=True)}'")
print(f"\n每一步的决策:")
for step in tracer.steps:
    print(f"  Step {step['step']}: 上下文='{step['context']}' "
          f"→ 选择 '{step['chosen_token']}' (p={step['chosen_prob']})")
```

### 3.5 测试验证

```python
# test_generation.py
import pytest
import torch
from transformers import AutoTokenizer, AutoModelForCausalLM
from custom_logits_processor import BlacklistLogitsProcessor

class TestLogitsProcessor:
    def test_blacklist_sets_inf(self):
        tokenizer = AutoTokenizer.from_pretrained("gpt2")
        processor = BlacklistLogitsProcessor(tokenizer, ["hello", "world"])
        scores = torch.ones(1, 1000) * 0.5
        processed = processor(torch.tensor([[1, 2, 3]]), scores)
        hello_id = tokenizer.encode("hello", add_special_tokens=False)[0]
        assert processed[0, hello_id] == -float("inf")

    def test_streamer(self):
        from streaming_generation import CustomStreamer
        streamer = CustomStreamer(
            AutoTokenizer.from_pretrained("gpt2"), skip_prompt=True
        )
        streamer.on_finalized_text("test", stream_end=False)
        assert "test" in streamer.generated_tokens
```

---

## 4 项目总结

### 4.1 优点与缺点

| 方面 | 优点 | 缺点 |
|------|------|------|
| **LogitsProcessor** | 灵活、可叠加、无侵入修改 logits | 每次生成都调用，影响性能 |
| **StoppingCriteria** | 控制生成停止，防止无限生成 | 条件过于复杂时调试困难 |
| **Streaming** | 用户体验好，减少等待感 | 与 batch 推理不兼容 |
| **GenerationConfig** | 参数集中管理、可保存可复用 | 默认值有时不符合直觉 |

### 4.2 适用场景

| 场景 | 推荐方案 |
|------|---------|
| 敏感词过滤 | BlacklistLogitsProcessor |
| 业务术语约束 | WhitelistLogitsProcessor |
| 长文本实时回复 | Streaming + TextStreamer |
| 精确长度控制 | StoppingCriteria 或 max_new_tokens |

**不适用场景**：
- 需要根据生成内容动态调整策略的复杂逻辑 → 考虑 Agent 多轮推理
- 非 Transformers 库 → 使用对应框架的约束机制

### 4.3 注意事项

1. **LogitsProcessor 顺序**：多个 processor 按 LogitsProcessorList 中的顺序依次执行
2. **-inf 的累积**：多个 processor 同时设 -inf 没有问题，但设 0 和 -inf 叠加需要小心
3. **Streaming 与 KV Cache**：streaming 依赖 KV Cache 的增量更新，禁用 cache 时无法流式输出

### 4.4 常见踩坑经验

| 故障现象 | 根因 | 解决方案 |
|---------|------|---------|
| 黑名单不起作用 | 黑名单词被切分为多个 token，只屏蔽了首 token | 屏蔽所有 sub-token ID |
| 流式输出中文乱码 | tokenizer 按 sub-token 输出 | 等完整 token 输出后再组合 |
| `max_new_tokens` 和 `max_length` 混淆 | `max_length` 是 input+output 的总长度 | 用 `max_new_tokens` 更直观 |

### 4.5 思考题

1. **初级**：在 `BlacklistLogitsProcessor` 中增加"不完全匹配"功能——如果黑名单词可能被切分为多个 token，如何确保所有相关 token 都被屏蔽？
2. **进阶**：设计一个 `DynamicTemperatureLogitsWarper`——根据已生成的 token 数量动态调整 temperature（前 20% token 用低温保确定性，后 80% 逐渐升温增加多样性）。

（答案将在第37章末尾给出）

### 4.6 推广计划提示

| 部门 | 建议行动 |
|------|---------|
| **开发团队** | 所有面向用户的生成接口必须接入黑名单 LogitsProcessor |
| **测试团队** | 编写黑名单覆盖测试，确认所有禁止词在任何上下文都不会出现 |
| **运维团队** | 监控 generation 平均 token 数和停止原因（eos/max_length/stopping_criteria） |

---

> **下一章预告**：第37章深入 Trainer 源码——训练循环、Callback 机制、自定义 loss 和评估逻辑，理解 `trainer.train()` 背后的完整流程。
