# 第35章：Attention 与模型前向传播源码链路

## 1 项目背景

### 业务场景

算法团队在客服工单分类模型的基础上，尝试做 Attention 可视化——展示模型在判断"这是投诉工单"时关注了文本中的哪些词。产品经理想把这个功能做成一个可解释性面板，让客服理解 AI 为什么做出这个判断。

小陈在 BERT 模型的 `forward()` 中插入了一个 hook 来提取 attention 权重，但发现提取出来的 attention 矩阵全是 0——原来 BERT 默认不返回 attention weights（`output_attentions=False`）。即使设置为 True 后，返回的 attention 矩阵维度是 `(batch, num_heads, seq_len, seq_len)`，如何从 12 层 12 头共 144 个矩阵中提取有意义的信息又是个难题。

同时，团队在尝试用 KV Cache 加速 GPT-2 生成时，发现 `past_key_values` 的使用方式与预期不同——KV Cache 的 shape 在每一步都会增长，但代码中并没有显式的拼接操作。这背后的机制是什么？

### 痛点放大

Attention 是 Transformer 的灵魂，但从"知道 Attention 公式"到"看懂源码中 Attention 的完整计算链路"中间有一条大沟：

1. **attention_mask 的玄学**：BERT 的 `extended_attention_mask` 为什么是 `(batch, 1, 1, seq_len)`？为什么要加到一个 `dtype` 矩阵上？
2. **Self-Attention 的实现**：`torch.matmul(Q, K.transpose(-2, -1))` 乘以 `scaling` 再加 mask 再 softmax 再乘以 V——每一步对应源码的哪一行？
3. **KV Cache 数据流**：`past_key_values` 是如何在每步生成中被更新和重用的？

---

## 2 项目设计

### 剧本式交锋对话

**场景**：周三下午 2:00，AI Lab。小陈在对着 BERT 的 `modeling_bert.py` 源码一行行打注释。

---

**小胖**:"你这对着源码一行行看，眼睛不酸吗？Attention 不就是 Q×K 再 softmax 再 ×V 吗？三行公式的事。"

**小陈**:"公式是三行，但代码里为了支持 batch、mask、multi-head、padding、causal 等场景，至少 50 行。"

**小白**:"我研究过 `modeling_bert.py` 的 forward。最让我困惑的是 `extended_attention_mask` 的处理——为什么要把 `(batch, seq_len)` 的 mask 扩展成 `(batch, 1, 1, seq_len)` 四维？为什么又 `(1.0 - mask) * -10000.0`？"

**大师**:"这正是理解 Attention 源码的钥匙。让我逐层揭开。

**第一层：attention_mask 的生成与广播。**

原始 `attention_mask` 是 `(batch, seq_len)` 的 0/1 矩阵（1=有效token, 0=PAD）。在进入 Attention 之前，它被转换为：

```python
# modeling_bert.py 中的 BertModel.forward()
extended_attention_mask = attention_mask[:, None, None, :]  # (batch, 1, 1, seq_len)
extended_attention_mask = (1.0 - extended_attention_mask) * -10000.0
```

为什么是四维？因为 Attention 的 score 矩阵是 `(batch, num_heads, query_len, key_len)`。通过广播，`(batch, 1, 1, seq_len)` 自动扩展到 `(batch, num_heads, query_len, seq_len)`。

为什么 `(1.0 - mask) * -10000.0`？PAD 位置的 mask=0 → `(1-0)*-10000 = -10000`，加到 attention score 上后，softmax(-10000) ≈ 0，PAD 位置在 Attention 中权重为 0。有效位置的 mask=1 → `(1-1)*-10000 = 0`，不影响 score。这比直接做 masked_fill 更高效。

**第二层：Self-Attention 的完整计算。**

进入 `BertSelfAttention.forward()` 后的流程：
1. Q、K、V 投影：`query = self.query(hidden_states)`，key 和 value 同理
2. 重塑为 multi-head：`(batch, seq, hidden) → (batch, num_heads, seq, head_dim)`
3. Attention Score：`scores = matmul(Q, K^T) / sqrt(head_dim)`
4. 加 mask：`scores += attention_mask`
5. Softmax：`probs = softmax(scores, dim=-1)`
6. Dropout（训练时）：`probs = dropout(probs)`
7. 加权求和：`context = matmul(probs, V)`
8. 合并多头：`(batch, num_heads, seq, head_dim) → (batch, seq, hidden)`
9. 输出投影：`output = self.output.dense(context)`

**第三层：KV Cache 的数据流。**

在生成模型（GPT-2）中，`past_key_values` 缓存了之前步的 K 和 V。每步生成时：
- 输入只有前一步生成的 1 个 token
- Q 只从这 1 个 token 计算
- K 和 V 是之前所有步的拼接（从 cache 中取出 + 当前步新计算的拼接）
- Attention 只关注该 token 与所有历史 token 的关系

```python
# past_key_value 的结构: (key, value) 的 tuple，每个是 (batch, num_heads, past_len, head_dim)
if past_key_value is not None:
    key = torch.cat([past_key_value[0], key], dim=-2)  # 拼接历史 key
    value = torch.cat([past_key_value[1], value], dim=-2)
```

**技术映射总结**：
- attention_mask 四维化 = 把简单的"有效/无效"标记转成 Attention 计算能直接加上的数值
- Scale + Mask + Softmax + Matmul(V) = Attention 的四步舞
- KV Cache = 记事本，只记已经写过的内容，新内容追加在后面

---

## 3 项目实战

### 3.1 环境准备

```bash
pip install transformers==4.44.0 torch matplotlib seaborn
```

### 3.2 Attention 可视化

```python
# attention_visualizer.py
"""从 BERT 中提取并可视化 Attention 权重"""

import torch
import numpy as np
import matplotlib.pyplot as plt
from transformers import AutoTokenizer, AutoModel

MODEL_NAME = "bert-base-chinese"

tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)
model = AutoModel.from_pretrained(MODEL_NAME, output_attentions=True)
model.eval()

text = "这个产品质量太差了，我要投诉！"
inputs = tokenizer(text, return_tensors="pt")

with torch.no_grad():
    outputs = model(**inputs)

# outputs.attentions 是一个 tuple，长度为 num_layers (12)
# 每层: (batch, num_heads, seq_len, seq_len)
attentions = outputs.attentions  # 12 层的 attention 权重

tokens = tokenizer.convert_ids_to_tokens(inputs["input_ids"][0])
print(f"Tokens ({len(tokens)}): {tokens}")
print(f"\nAttention 结构:")
print(f"  层数: {len(attentions)}")
print(f"  每层: {attentions[0].shape}")  # (1, 12, seq_len, seq_len)
print(f"  头数: {attentions[0].shape[1]}")
print(f"  序列长度: {attentions[0].shape[2]}")

# ===== 可视化: 最后一层所有头的平均 Attention =====
def plot_attention_matrix(attn_weights, tokens, title, layer=-1):
    """绘制 Attention 热力图"""
    # 取指定层的所有头的平均
    if isinstance(attn_weights, tuple):
        attn = attn_weights[layer][0].mean(dim=0).numpy()  # (seq_len, seq_len)
    else:
        attn = attn_weights[0].mean(dim=0).numpy()

    # 只显示非特殊 token
    valid_tokens = [t for t in tokens if t not in ("[CLS]", "[SEP]", "[PAD]")]
    valid_indices = [i for i, t in enumerate(tokens) if t not in ("[CLS]", "[SEP]", "[PAD]")]
    attn_valid = attn[valid_indices][:, valid_indices]

    fig, ax = plt.subplots(figsize=(10, 8))
    im = ax.imshow(attn_valid, cmap="YlOrRd")

    ax.set_xticks(range(len(valid_tokens)))
    ax.set_yticks(range(len(valid_tokens)))
    ax.set_xticklabels(valid_tokens, rotation=45, ha="right", fontsize=10)
    ax.set_yticklabels(valid_tokens, fontsize=10)

    plt.colorbar(im, ax=ax)
    ax.set_title(title, fontsize=14)
    plt.tight_layout()
    plt.savefig("./output/attention_heatmap.png", dpi=150)
    print(f"\nAttention 热力图已保存至 ./output/attention_heatmap.png")


# ===== 可视化: 各层 [CLS] token 与各词的 Attention =====
def plot_cls_attention(attentions, tokens):
    """查看 [CLS] token 对各层的注意力分布"""
    cls_attentions = []
    for layer_attn in attentions:
        # layer_attn: (1, num_heads, seq_len, seq_len)
        # [CLS] 是第 0 个 token
        cls_attn = layer_attn[0, :, 0, :].mean(dim=0).numpy()  # 所有头平均
        cls_attentions.append(cls_attn)

    cls_attentions = np.array(cls_attentions)

    fig, ax = plt.subplots(figsize=(12, 6))
    im = ax.imshow(cls_attentions, cmap="Blues", aspect="auto")

    ax.set_xticks(range(len(tokens)))
    ax.set_yticks(range(len(attentions)))
    ax.set_xticklabels(tokens, rotation=45, ha="right")
    ax.set_ylabel("Layer")

    plt.colorbar(im, ax=ax)
    ax.set_title("[CLS] token 对各层的 Attention 分布")
    plt.tight_layout()
    plt.savefig("./output/cls_attention_layers.png", dpi=150)
    print("CLS Attention 分层图已保存至 ./output/cls_attention_layers.png")


# ===== 提取 Hook: 在 forward 中插入自定义逻辑 =====
def register_attention_hook():
    """使用 PyTorch Hook 提取中间层 Attention"""
    model = AutoModel.from_pretrained(MODEL_NAME)
    model.eval()

    attention_weights = []

    def hook_fn(module, input, output):
        """Hook 函数：在 Self-Attention 层的输出处捕获"""
        # output[1] 是 attention_probs (如果 output_attentions=False, 这里是 None)
        if isinstance(output, tuple) and len(output) > 1 and output[1] is not None:
            attention_weights.append(output[1].detach())

    # 在 BERT 的每一层 Self-Attention 上注册 hook
    for i, layer in enumerate(model.encoder.layer):
        layer.attention.self.register_forward_hook(hook_fn)

    # 前向传播
    inputs = tokenizer(text, return_tensors="pt")
    with torch.no_grad():
        _ = model(**inputs, output_attentions=True)

    print(f"\n通过 Hook 捕获了 {len(attention_weights)} 层的 Attention 权重")
    return attention_weights, model


if __name__ == "__main__":
    # 1. Attention 矩阵可视化
    plot_attention_matrix(attentions, tokens,
                         f"BERT 最后一层 Attention 热力图\n'{text}'")

    # 2. CLS 分层 Attention
    plot_cls_attention(attentions, tokens)

    # 3. Hook 方式提取
    hook_attns, _ = register_attention_hook()
```

### 3.3 KV Cache 追踪

```python
# kv_cache_trace.py
"""追踪 GPT-2 的 KV Cache 机制"""

import torch
from transformers import AutoTokenizer, AutoModelForCausalLM

MODEL_NAME = "gpt2"
tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)
tokenizer.pad_token = tokenizer.eos_token
model = AutoModelForCausalLM.from_pretrained(MODEL_NAME)
model.eval()

prompt = "The weather today is"
inputs = tokenizer(prompt, return_tensors="pt")

print(f"Prompt: '{prompt}'")
print(f"Input IDs: {inputs['input_ids'][0].tolist()}")

# ===== 方式1: 不使用 KV Cache（完整前向） =====
print("\n===== 方式1: 无 Cache 完整前向 =====")
with torch.no_grad():
    outputs_full = model(**inputs, use_cache=False)
print(f"  Logits shape: {outputs_full.logits.shape}")  # (1, 5, vocab)

# ===== 方式2: 使用 KV Cache（逐步生成） =====
print("\n===== 方式2: 使用 KV Cache 逐步生成 =====")
past = None
input_ids = inputs["input_ids"]

# 第一步：用全序列获取 KV Cache
with torch.no_grad():
    outputs = model(input_ids, use_cache=True, past_key_values=past)
    past = outputs.past_key_values

print(f"  Step 0: 输入 {input_ids.shape[1]} tokens → 获得 KV Cache")

# 分析 KV Cache 结构
for i, layer_past in enumerate(past[:1]):  # 只看第一层
    key, value = layer_past
    print(f"  Layer 0: key shape={key.shape}, value shape={value.shape}")
    # key: (batch, num_heads, seq_len, head_dim)

# 第二步：用上一步生成的最后一个 token + Cache 生成下一个
next_token_id = torch.argmax(outputs.logits[:, -1, :], dim=-1).unsqueeze(0)
print(f"\n  Step 1: 输入 1 个新 token (id={next_token_id.item()})")

with torch.no_grad():
    outputs2 = model(next_token_id, use_cache=True, past_key_values=past)
    past2 = outputs2.past_key_values

# KV Cache 应该变长
for i, (layer_past1, layer_past2) in enumerate(zip(past[:1], past2[:1])):
    key1, _ = layer_past1
    key2, _ = layer_past2
    print(f"  Layer 0 KV Cache 增长: {key1.shape[-2]} → {key2.shape[-2]} tokens")
    # key1.shape[-2]: 5 → key2.shape[-2]: 6 (多了一个 token)

next_token = tokenizer.decode(next_token_id[0])
print(f"  生成的下一个 token: '{next_token}'")

# 估算 KV Cache 内存占用
batch_size = 1
num_layers = model.config.n_layer  # 12 for GPT-2
num_heads = model.config.n_head    # 12
head_dim = model.config.n_embd // num_heads  # 64
seq_len = 128  # 假设生成 128 tokens
bytes_per_element = 2  # FP16

kv_cache_bytes = (2 * batch_size * num_layers * num_heads * seq_len * head_dim * bytes_per_element)
print(f"\nKV Cache 估算 (seq_len={seq_len}):")
print(f"  {kv_cache_bytes / 1024:.0f} KB ({kv_cache_bytes / 1024**2:.1f} MB)")


# ===== 方式3: 使用 model.generate()（内部自动用 KV Cache） =====
print("\n===== 方式3: model.generate() (内部自动使用 KV Cache) =====")
gen_outputs = model.generate(
    inputs["input_ids"],
    max_new_tokens=10,
    do_sample=False,
    return_dict_in_generate=True,
    output_scores=False,
)
generated = tokenizer.decode(gen_outputs.sequences[0], skip_special_tokens=True)
print(f"  生成结果: '{generated}'")
```

### 3.4 attention_mask 生成追踪

```python
# attention_mask_trace.py
"""追踪 attention_mask 的生成与转换"""

import torch
from transformers import AutoTokenizer, AutoModel

MODEL_NAME = "bert-base-chinese"
tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)
model = AutoModel.from_pretrained(MODEL_NAME)
model.eval()

# 模拟不等长序列（第二个序列被 PAD 了）
texts = ["今天天气不错", "短"]
inputs = tokenizer(texts, padding=True, return_tensors="pt")

print("原始 attention_mask (2D):")
print(f"  shape: {inputs['attention_mask'].shape}")
print(f"  values:\n{inputs['attention_mask']}")
# tensor([[1, 1, 1, 1, 1, 1, 1, 1],  ← "今天天气不错" + [CLS][SEP]
#         [1, 1, 1, 1, 0, 0, 0, 0]]) ← "短" + [CLS][SEP] + PAD×4

# 模拟 modeling_bert.py 中的转换
attention_mask = inputs["attention_mask"]
batch_size, seq_len = attention_mask.shape

# Step 1: 扩展维度
extended = attention_mask[:, None, None, :]  # (batch, 1, 1, seq_len)
print(f"\n扩展后 (4D): shape={extended.shape}")
print(f"  序列1 (全部有效):\n{extended[0, 0, 0]}")
print(f"  序列2 (后4个PAD):\n{extended[1, 0, 0]}")

# Step 2: 转换为 additive mask
additive_mask = (1.0 - extended) * -10000.0
print(f"\nAdditive Mask (4D):")
print(f"  序列1: {additive_mask[0, 0, 0].tolist()}")
print(f"  序列2: {additive_mask[1, 0, 0].tolist()}")
# 有效位置=0, PAD位置=-10000

# Step 3: 为什么用 additive 而非 masked_fill？
print(f"\n为什么用加法而非 masked_fill？")
print(f"  加法: score + mask → 直接修改 tensor, GPU 友好, 不改变 shape")
print(f"  masked_fill: score[mask==0] = -inf → 需要布尔索引, 多步操作")

# Step 4: causal mask（GPT 生成用）
print(f"\nCausal Mask (GPT 生成用, 模拟 5 tokens):")
seq = 5
causal = torch.tril(torch.ones(seq, seq))
print(f"  {causal}")
# tensor([[1., 0., 0., 0., 0.],  ← token0 只能看到 token0
#         [1., 1., 0., 0., 0.],  ← token1 能看到 token0-1
#         [1., 1., 1., 0., 0.],
#         [1., 1., 1., 1., 0.],
#         [1., 1., 1., 1., 1.]])

causal_mask = (1.0 - causal) * -10000.0
print(f"\n  Additive causal mask:")
print(f"  {causal_mask}")
```

### 3.5 测试验证

```python
# test_attention.py
import pytest
import torch
from transformers import AutoModel, AutoTokenizer

class TestAttention:
    def test_output_attentions(self):
        model = AutoModel.from_pretrained("prajjwal1/bert-tiny")
        tokenizer = AutoTokenizer.from_pretrained("prajjwal1/bert-tiny")
        inputs = tokenizer("test", return_tensors="pt")
        outputs = model(**inputs, output_attentions=True)
        assert outputs.attentions is not None
        assert len(outputs.attentions) == model.config.num_hidden_layers

    def test_attention_shape(self):
        model = AutoModel.from_pretrained("prajjwal1/bert-tiny")
        tokenizer = AutoTokenizer.from_pretrained("prajjwal1/bert-tiny")
        inputs = tokenizer("hello world", return_tensors="pt")
        outputs = model(**inputs, output_attentions=True)
        # (batch, num_heads, seq_len, seq_len)
        assert outputs.attentions[0].shape == (1, 2, 5, 5)  # tiny bert 有 2 heads

    def test_kv_cache_growth(self):
        from transformers import GPT2LMHeadModel, GPT2Tokenizer
        tokenizer = GPT2Tokenizer.from_pretrained("gpt2")
        tokenizer.pad_token = tokenizer.eos_token
        model = GPT2LMHeadModel.from_pretrained("gpt2")
        inputs = tokenizer("hello", return_tensors="pt")
        with torch.no_grad():
            out1 = model(**inputs, use_cache=True)
            past = out1.past_key_values
            # 用最后一个 token + cache
            next_id = torch.argmax(out1.logits[:, -1, :], dim=-1).unsqueeze(0)
            out2 = model(next_id, past_key_values=past, use_cache=True)
            past2 = out2.past_key_values
            # Cache 应该增长 1
            assert past2[0][0].shape[2] == past[0][0].shape[2] + 1
```

---

## 4 项目总结

### 4.1 优点与缺点

| 方面 | 优点 | 缺点 |
|------|------|------|
| **attention_mask 四维广播** | 高效、GPU 友好 | 维度变换不直观，调试困难 |
| **KV Cache** | 避免重复计算，生成速度提升 10 倍+ | 显存随序列长度线性增长 |
| **output_attentions** | 一句代码导出权重，可视化方便 | 返回所有层的 attention 占用内存大 |
| **Hook 机制** | 在任意层插入自定义逻辑 | Hook 太多会导致训练/推理变慢 |

### 4.2 适用场景

| 场景 | 推荐方案 |
|------|---------|
| 模型可解释性分析 | `output_attentions=True` |
| 长文本生成加速 | KV Cache（默认开启） |
| 自定义 attention 计算 | 继承并重写 `BertSelfAttention` |
| 训练时节省显存 | `output_attentions=False`（默认） |

**不适用场景**：
- 需要访问中间层 hidden states → 用 `output_hidden_states=True`
- 需要修改模型结构 → 直接改源码中的对应类

### 4.3 注意事项

1. **`output_attentions=True` 显存开销**：每层额外存储 `(batch, heads, seq, seq)` 的 float32 矩阵，长序列开销巨大
2. **Hook 要记得 detach**：Hook 中捕获的 tensor 如果不 detach，会阻止梯度计算图释放
3. **causal_mask 的生成**：每个模型实现略有不同（GPT-2 vs LLaMA），不要直接用 BERT 的代码去套 GPT

### 4.4 常见踩坑经验

| 故障现象 | 根因 | 解决方案 |
|---------|------|---------|
| Attention 热力图全亮 | 没过滤 token_type_ids 和特殊 token | 只展示有效内容 token 的 attention |
| KV Cache 不对齐 | GPT 和 BERT 的 cache 格式不同 | 阅读对应 `modeling_xxx.py` 中的实现 |
| `extended_attention_mask` 广播报错 | shape 对不齐 | 确认 `[:, None, None, :]` 的维度 |

### 4.5 思考题

1. **初级**：在 `attention_visualizer.py` 中，对比第 1 层和第 12 层的 Attention 模式——底层更多关注相邻词还是全局词？顶层呢？
2. **进阶**：如何在不修改 Transformers 源码的情况下，将 BERT 的 Self-Attention 替换为稀疏 Attention（如只关注前 k 个最重要的 token）？请给出 Hook 或子类化的方案。

（答案将在第36章末尾给出）

### 4.6 第34章思考题答案

**第34章思考题1**：
- 不能。新 token 的 embedding 在 BERT 冻结时无法被更新（`requires_grad=False`），而分类头在冻结的 embedding 上做分类。新 token 始终用初始化的均值 embedding，无法学到有意义的表示。解决方案：至少让 embedding 层可训练，或使用 prefix-tuning 等 PEFT 方法。

**第34章思考题2**：
- 方案：(1) 在 BPE 预分词阶段，用正则 `[\w]+|[\S]` 将代码标识符作为整体，防止 `myVariableName` 被拆成 `my`/`Variable`/`Name`；(2) 训练自定义 BPE tokenizer，在代码语料上统计频率，让高频标识符被整体收录；(3) 或直接用 `CodeBERT`/`CodeGPT` 等预训练好的代码 tokenizer。

### 4.7 推广计划提示

| 部门 | 建议行动 |
|------|---------|
| **开发/算法团队** | 用 `output_attentions=True` 做关键样本的可解释性分析 |
| **测试团队** | 验证 KV Cache 开关对生成结果是否有影响 |
| **架构师** | 评估 Attention 可视化在产品可解释性面板中的集成方案 |

---

> **下一章预告**：第36章深入 Generation 源码——generate() 的完整调用链、LogitsProcessor/StoppingCriteria 的工作原理、流式输出的实现机制。
