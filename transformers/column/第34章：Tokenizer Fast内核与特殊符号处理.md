# 第34章：Tokenizer Fast 内核与特殊符号处理

## 1 项目背景

### 业务场景

客服工单分类系统在处理大量文本时出现了一个诡异的现象：同一个 Tokenizer，用 `tokenizer(text)` （Fast 模式）编码的结果和用 `tokenizer.encode(text)` （Slow 模式）的结果不一致——两串 token ID 差了一个位置。这导致推理时模型收到了和训练时不同的 token 序列，线上准确率波动 2-3 个百分点。

排查后发现：代码中混合使用了 `tokenizer.__call__()` 和 `tokenizer.encode()`，而前者走的是 Rust 后端的 Fast Tokenizer，后者走的是 Python 后端的 Slow Tokenizer——两者在某些边界情况下的行为有微小差异。

更严重的是，运营团队引入了一批新的业务术语（如"闪电退"、"极速赔"），这些词在 BERT 的词表中不存在，全被映射成了 `[UNK]`。分类模型因此完全无法理解包含这些新词的工单。

### 痛点放大

Tokenizer 虽小，但在生产环境中是"多米诺骨牌的第一张"——token 错了，后面全错：

1. **Fast vs Slow**：Fast Tokenizer 用 Rust 实现，速度快 5-10 倍，但某些边缘情况的处理与 Python 版不同
2. **offset_mapping**：将 token 位置映射回原始字符串的关键工具，理解偏差直接导致 NER span 错位
3. **词表扩展**：业务术语不在词表中 → 全变 [UNK] → 模型无法理解 → 分类准确率下降
4. **特殊 Token**：`AddedToken`、`chat_template`、`special_tokens_map` 的作用和配置陷阱

```
分词结果差异:
输入: "闪电退服务已上线"
Slow Tokenizer: ['闪','电','退','服','务','已','上','线']  ← 8个token
Fast Tokenizer: ['闪','电','退','服务','已','上线']      ← 6个token
                    ↑ "服务"和"上线"被合并了！
```

---

## 2 项目设计

### 剧本式交锋对话

**场景**：周五下午 2:00，AI Lab。小陈正在对比 Fast 和 Slow Tokenizer 的输出差异。

---

**小胖**:"这 Fast 和 Slow 不就是快和慢的区别吗？快一点不好吗，为什么还留着 Slow？"

**小陈**:"Fast Tokenizer 用 Rust 写的，确实快很多。但它和 Slow 在某些边界情况下的行为不完全一样——比如对中文的预分词、emoji 处理、全角半角转换。Rust 版本为了性能做了一些简化。"

**小白**:"具体差异在哪？代码层面——Fast Tokenizer 的核心类是 `PreTrainedTokenizerFast`，而 Slow 是 `PreTrainedTokenizer`。两者的 `_tokenize()` 方法实现完全不同：Fast 的实现在 Rust 编译的 `.so/.dll` 二进制文件中，Slow 是纯 Python。"

**大师**:"让我把 Tokenizer 的三个深层话题讲清楚。

**话题一：Fast vs Slow 的差异来源。** Fast Tokenizer 底层是 HuggingFace 的 `tokenizers` 库（Rust 实现）。它把预分词（pre-tokenization）、BPE/WordPiece 合并、后处理等步骤都在 Rust 中完成。但 Rust 版本和 Python 版本在某些 Unicode 处理上存在差异：
- Unicode 规范化：Python 默认 NFC 规范化，Rust 版本可能使用不同的规范化策略
- 全角/半角处理：Python 有 `unicodedata` 库，Rust 需要额外实现
- 特殊字符边界：emoji、零宽字符的处理可能不同

**话题二：offset_mapping —— token 位置到原始字符的桥梁。** NER 任务中最关键的工具。用法：
```python
encoded = tokenizer("深圳市腾讯", return_offsets_mapping=True)
# offset_mapping: [(0,1), (0,2), (0,3), (2,4), (3,5), (4,6)]
# token:         [CLS]  深    圳    市   腾    讯
```
每个 tuple 是 `(start_char_index, end_char_index)`，对应原始字符串中的字符位置。通过它才能把 token 级别的预测（B-ORG）映射回原始字符串的实体（"深圳市腾讯"）。

但有个坑：**特殊 token 的 offset 是 (0, 0)**。如果你在合并实体时不过滤掉 offset=(0,0) 的 token，会把 `[CLS]` 也当成一个实体。

**话题三：词表扩展。** 当业务术语不在词表中时，两种扩展方式：
1. `tokenizer.add_tokens(["闪电退", "极速赔"])`：添加到词表末尾，需要同步调用 `model.resize_token_embeddings(len(tokenizer))`
2. `tokenizer.add_special_tokens({"additional_special_tokens": ["<LIGHTNING_REFUND>"]})`：添加特殊标记，通常用尖括号包裹

添加后必须重新训练 embedding（或至少训练新 token 的 embedding 行）。"

**技术映射总结**：
- Fast Tokenizer = 跑车（快但有盲区），Slow Tokenizer = 电动车（稳但慢）
- offset_mapping = 翻译对照表，每个翻译（token）对应原文的哪一段
- 词表扩展 = 词典里加新词，但得同步更新模型对这个新词的认识

---

## 3 项目实战

### 3.1 环境准备

```bash
pip install transformers==4.44.0 torch tokenizers==0.19.1
```

### 3.2 Fast vs Slow 对比

```python
# tokenizer_compare.py
"""Fast vs Slow Tokenizer 深度对比"""

import time
from transformers import AutoTokenizer


def compare_fast_slow(model_name: str = "bert-base-chinese"):
    """对比 Fast 和 Slow Tokenizer 的行为差异"""

    # 加载两个版本
    tokenizer_slow = AutoTokenizer.from_pretrained(model_name, use_fast=False)
    tokenizer_fast = AutoTokenizer.from_pretrained(model_name, use_fast=True)

    print(f"Slow Tokenizer 类型: {type(tokenizer_slow).__name__}")
    print(f"Fast Tokenizer 类型: {type(tokenizer_fast).__name__}")

    # 测试文本（含各种边缘情况）
    test_texts = [
        "闪电退服务已上线",                 # 新词
        "今天天气不错🙂心情很好😡",         # emoji
        "Hello世界！2024年5月20日",         # 中英混合
        "ｔｅｓｔ全角英文",                # 全角字符
        "深圳市腾讯计算机系统有限公司",      # 长实体
    ]

    print(f"\n{'文本':<40} {'Slow tokens':<25} {'Fast tokens':<25} {'一致?'}")
    print("-" * 100)

    for text in test_texts:
        tokens_slow = tokenizer_slow.tokenize(text)
        tokens_fast = tokenizer_fast.tokenize(text)
        is_same = tokens_slow == tokens_fast
        same_mark = "✓" if is_same else "✗"

        print(f"{text:<40} {str(tokens_slow)[:23]:<25} {str(tokens_fast)[:23]:<25} {same_mark}")

    # offset_mapping 对比
    print(f"\n{'='*60}")
    print("offset_mapping 对比 (输入: '深圳市腾讯')")
    text = "深圳市腾讯"
    encoded_slow = tokenizer_slow(text, return_offsets_mapping=True)
    encoded_fast = tokenizer_fast(text, return_offsets_mapping=True)

    print(f"\nSlow Tokenizer:")
    for token, offset in zip(
        tokenizer_slow.convert_ids_to_tokens(encoded_slow["input_ids"]),
        encoded_slow["offset_mapping"]
    ):
        char_span = text[offset[0]:offset[1]] if offset != (0, 0) else "[SPECIAL]"
        print(f"  {token:<10} offset={offset} → '{char_span}'")

    print(f"\nFast Tokenizer:")
    for token, offset in zip(
        tokenizer_fast.convert_ids_to_tokens(encoded_fast["input_ids"]),
        encoded_fast["offset_mapping"]
    ):
        char_span = text[offset[0]:offset[1]] if offset != (0, 0) else "[SPECIAL]"
        print(f"  {token:<10} offset={offset} → '{char_span}'")


def benchmark_speed():
    """性能对比"""
    tokenizer_slow = AutoTokenizer.from_pretrained("bert-base-chinese", use_fast=False)
    tokenizer_fast = AutoTokenizer.from_pretrained("bert-base-chinese", use_fast=True)

    texts = ["这是一个测试文本用于性能对比" * 10] * 1000

    # Slow
    start = time.time()
    for text in texts:
        _ = tokenizer_slow(text)
    slow_time = time.time() - start

    # Fast
    start = time.time()
    for text in texts:
        _ = tokenizer_fast(text)
    fast_time = time.time() - start

    print(f"\n性能对比 (1000 条文本):")
    print(f"  Slow: {slow_time:.2f}s ({1000/slow_time:.0f} 条/秒)")
    print(f"  Fast: {fast_time:.2f}s ({1000/fast_time:.0f} 条/秒)")
    print(f"  加速比: {slow_time/fast_time:.1f}x")


if __name__ == "__main__":
    compare_fast_slow("bert-base-chinese")
    benchmark_speed()
```

### 3.3 词表扩展实战

```python
# vocab_extension.py
"""词表扩展 —— 添加业务术语到 Tokenizer"""

import torch
from transformers import AutoTokenizer, AutoModelForSequenceClassification


def extend_tokenizer_vocab(model_name: str, new_tokens: list):
    """扩展 Tokenizer 词表并同步模型 embedding"""
    tokenizer = AutoTokenizer.from_pretrained(model_name)
    model = AutoModelForSequenceClassification.from_pretrained(
        model_name, num_labels=2
    )

    print(f"原始词表大小: {len(tokenizer)}")
    print(f"原始 embedding 大小: {model.get_input_embeddings().weight.shape}")

    # 1. 添加新 token
    num_added = tokenizer.add_tokens(new_tokens)
    print(f"\n添加了 {num_added} 个新 token: {new_tokens}")

    # 验证
    test_text = "闪电退服务已上线，支持极速赔"
    tokens = tokenizer.tokenize(test_text)
    ids = tokenizer.encode(test_text)
    print(f"\n测试文本: {test_text}")
    print(f"  Tokens: {tokens}")
    print(f"  IDs: {ids}")
    print(f"  [UNK] 数量: {tokens.count('[UNK]')}")

    # 2. 同步模型 embedding 大小
    model.resize_token_embeddings(len(tokenizer))
    print(f"\n模型 embedding 已调整: "
          f"{model.get_input_embeddings().weight.shape}")

    # 3. 验证新 embedding 的初始化
    new_token_ids = tokenizer.convert_tokens_to_ids(new_tokens)
    embedding = model.get_input_embeddings()
    for token, tid in zip(new_tokens, new_token_ids):
        if tid >= 0:
            vec = embedding.weight[tid]
            print(f"  '{token}' (id={tid}): embedding norm={vec.norm().item():.4f}, "
                  f"mean={vec.mean().item():.4f}")
            # 新 token 的 embedding 初始化为已有 embedding 的均值（Transformers 默认行为）

    return tokenizer, model


def add_special_business_tokens(model_name: str):
    """添加特殊业务符号"""
    tokenizer = AutoTokenizer.from_pretrained(model_name)
    model = AutoModelForSequenceClassification.from_pretrained(
        model_name, num_labels=3
    )

    # 方式1: 作为普通 token 添加
    tokenizer.add_tokens(["<ORDER_ID>", "<PHONE_NUM>", "<AMOUNT>"])

    # 方式2: 作为特殊 token 添加（不会被切分）
    special_tokens = {
        "additional_special_tokens": ["<LIGHTNING_REFUND>", "<EXPRESS_CLAIM>"]
    }
    tokenizer.add_special_tokens(special_tokens)

    model.resize_token_embeddings(len(tokenizer))

    print(f"词表大小: {len(tokenizer)}")
    print(f"特殊 token:")
    print(f"  pad_token: {tokenizer.pad_token} (id={tokenizer.pad_token_id})")
    print(f"  cls_token: {tokenizer.cls_token} (id={tokenizer.cls_token_id})")
    print(f"  sep_token: {tokenizer.sep_token} (id={tokenizer.sep_token_id})")
    print(f"  additional_special_tokens: {tokenizer.additional_special_tokens}")

    # 保存扩展后的 tokenizer（必须保存，否则推理和训练不一致）
    tokenizer.save_pretrained("./models/extended_tokenizer")
    model.save_pretrained("./models/extended_model")
    print("\n已保存扩展后的 tokenizer 和 model")

    return tokenizer, model


if __name__ == "__main__":
    # 测试词表扩展
    tokenizer, model = extend_tokenizer_vocab(
        "bert-base-chinese",
        ["闪电退", "极速赔", "当日达", "次日达", "准时宝"]
    )

    # 测试特殊 token
    print("\n" + "=" * 50)
    add_special_business_tokens("bert-base-chinese")
```

### 3.4 AddedToken 与 chat_template

```python
# special_tokens_demo.py
"""AddedToken 和 chat_template 的使用"""

from transformers import AutoTokenizer
from tokenizers import AddedToken


def demo_added_token():
    """AddedToken 控制 token 的行为"""
    tokenizer = AutoTokenizer.from_pretrained("bert-base-chinese")

    # 普通 add_tokens: 新 token 可以被进一步切分
    tokenizer.add_tokens(["闪电退"])

    # AddedToken: 可以控制是否在前后加空格、是否归一化
    special_tok = AddedToken("<ORDER>", single_word=True, normalized=False)
    tokenizer.add_tokens([special_tok])

    text = "订单<ORDER>12345支持闪电退"
    tokens = tokenizer.tokenize(text)
    print(f"文本: {text}")
    print(f"Tokens: {tokens}")
    # <ORDER> 被作为整体保留，不会被切分

    # 检查 special_tokens_map
    print(f"\nspecial_tokens_map: {tokenizer.special_tokens_map}")


def demo_chat_template():
    """Chat Template —— 对话格式化成模型输入"""
    # chat_template 主要用于聊天模型 (LLaMA, Qwen, ChatGLM 等)
    tokenizer = AutoTokenizer.from_pretrained("HuggingFaceH4/zephyr-7b-beta")

    messages = [
        {"role": "system", "content": "你是一个客服助手"},
        {"role": "user", "content": "怎么退款？"},
    ]

    # 使用 chat_template 格式化
    prompt = tokenizer.apply_chat_template(
        messages,
        tokenize=False,  # 先不 tokenize，看格式
        add_generation_prompt=True,
    )
    print("Chat Template 格式化后的 prompt:")
    print(prompt)

    # 也可以直接 tokenize
    inputs = tokenizer.apply_chat_template(
        messages,
        tokenize=True,
        add_generation_prompt=True,
        return_tensors="pt",
    )
    print(f"\nTokenized input shape: {inputs.shape}")


if __name__ == "__main__":
    demo_added_token()
    print("\n" + "=" * 50)
    try:
        demo_chat_template()
    except Exception as e:
        print(f"chat_template demo 需要联网下载模型: {e}")
```

### 3.5 测试验证

```python
# test_tokenizer_extended.py
import pytest
from transformers import AutoTokenizer

class TestTokenizerExtension:
    def test_add_tokens(self):
        tokenizer = AutoTokenizer.from_pretrained("bert-base-chinese")
        before = len(tokenizer)
        tokenizer.add_tokens(["测试词A", "测试词B"])
        after = len(tokenizer)
        assert after == before + 2

    def test_unk_reduced(self):
        tokenizer = AutoTokenizer.from_pretrained("bert-base-chinese")
        tokenizer.add_tokens(["闪电退"])
        ids = tokenizer.encode("闪电退服务")
        unk_id = tokenizer.unk_token_id
        assert unk_id not in ids  # "闪电退" 不应再是 [UNK]

    def test_special_tokens_preserved(self):
        tokenizer = AutoTokenizer.from_pretrained("bert-base-chinese")
        tokenizer.add_special_tokens({"additional_special_tokens": ["<TEST>"]})
        assert "<TEST>" in tokenizer.additional_special_tokens
        tid = tokenizer.convert_tokens_to_ids("<TEST>")
        assert tid != tokenizer.unk_token_id
```

---

## 4 项目总结

### 4.1 优点与缺点

| 方面 | 优点 | 缺点 |
|------|------|------|
| **Fast Tokenizer** | 批量处理快 5-10 倍，offset_mapping 准确 | 某些 Unicode 边界处理与 Slow 有差异 |
| **Slow Tokenizer** | 纯 Python，行为与 PyTorch 代码完全一致 | 批量处理慢，不适合大数据量 |
| **词表扩展** | 让模型理解业务术语，直接提升准确率 | 需重新训练 embedding（至少新 token 部分） |
| **AddedToken** | 精细控制 token 行为 | API 较复杂，容易与 add_tokens 混淆 |

### 4.2 适用场景

| 场景 | 推荐方案 |
|------|---------|
| 在线推理批量处理 | Fast Tokenizer |
| 训练数据预处理（一次编码多次使用） | Fast 或 Slow 均可（预处理只跑一次） |
| NER 任务 offset 对齐 | Fast Tokenizer + offset_mapping |
| 有大量业务术语 | 词表扩展 + 增量训练 embedding |
| 聊天模型对话格式化 | chat_template |

**不适用场景**：
- 对 token 序列可读性有要求（Fast Tokenizer 的某些 token 边界不太直观）

### 4.3 注意事项

1. **训练推理一致**：保存 tokenizer 时必须包含所有添加的 token 和特殊符号映射
2. **`resize_token_embeddings` 后需要训练**：新 token 的 embedding 初始化为平均值，仍需训练
3. **offset_mapping 的 (0,0) 过滤**：处理 NER 数据时务必过滤掉特殊 token 的 (0,0) offset

### 4.4 常见踩坑经验

| 故障现象 | 根因 | 解决方案 |
|---------|------|---------|
| 线上推理结果偏移 | Fast/Slow Tokenizer 输出不一致 | 统一使用一种 Tokenizer，或线上也用 Slow |
| 添加新 token 后 embedding 不匹配 | 忘记调用 `resize_token_embeddings` | 添加 token 后立即 resize |
| chat_template 报错 | 模型没有 chat_template 配置 | 手动设置 `tokenizer.chat_template = "..."` |

### 4.5 思考题

1. **初级**：在 `vocab_extension.py` 中，如果添加了 100 个新 token 但 `resize_token_embeddings` 后只用少量数据训练分类头（冻结 BERT），新 token 的 embedding 能被有效训练吗？
2. **进阶**：你的业务需要支持 50 万条包含代码片段的文本。请设计一个 Tokenizer 方案，确保代码中的变量名（如 `myVariableName`）不被过度切分。（提示：考虑 BPE 预分词策略）

### 4.6 第33章思考题答案

**第33章思考题1**：
- 分类头被随机初始化 → 直接训练即可，因为分类头参数少（768×num_labels），几百条数据几个 epoch 就能收敛。但如果 missing_keys 包含 encoder 层的参数 → 需要排查 checkpoint 是否完整。

**第33章思考题2**：
- Transformers 默认不检查权重值。健康检查方案：(1) 计算每层权重的均值和标准差，与同架构已知正常模型的统计量对比；(2) 检测全零张量（`tensor.abs().sum() == 0`）；(3) 检测 NaN/Inf（`torch.isnan(tensor).any()`）。定期运行，对比 baseline。

### 4.7 推广计划提示

| 部门 | 建议行动 |
|------|---------|
| **开发团队** | 统一使用 Fast Tokenizer + batch_encode_plus，减少预处理耗时 |
| **测试团队** | 验证 Slow/Fast Tokenizer 输出一致性，建立回归测试 |
| **算法团队** | 定期 review 业务术语覆盖率，必要时扩展词表 |

---

> **下一章预告**：第35章深入 Attention 与模型前向传播源码——从 BERT 的 forward() 逐行追踪 Self-Attention 计算、attention_mask 的生成与广播、past_key_values 的 KV Cache 机制。
