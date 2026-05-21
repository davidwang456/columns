# 第12章：Transformer 入门与小型文本匹配

## 1. 项目背景

一家 SaaS 公司的客服系统有一个 FAQ 模块——包含 2000 条标准问题和答案（如"如何重置密码""退款流程是什么""支持哪些支付方式"）。用户输入问题时，系统需要从 2000 条 FAQ 中找到最匹配的那一条，返回对应答案。

算法工程师阿涛一开始用的是第 11 章学到的 LSTM 模型——用同一个 LSTM 分别编码用户问题和 FAQ 问题，然后计算余弦相似度，选出相似度最高的一条。模型上线后，测试团队发现了一个严重问题：当用户输入"怎么把钱拿回来"时，模型匹配到了"如何赚钱"而不是"退款流程"——因为"钱"这个关键词在两个 FAQ 中都有出现，LSTM 给它俩的编码相似度居然差不多。

阿涛深入分析发现：LSTM 的编码能力其实够用，但"怎么把钱拿回来"和"退款流程"的核心语义相似性需要更精细的注意力机制来捕获——模型需要理解"把钱拿回来"="退款"，而不是逐字匹配。而且 FAQ 匹配这种场景，2000 条候选的编码可以预先算好存起来——这就是典型的"双编码器+向量检索"架构。

**痛点放大**：RNN/LSTM 在处理长文本时有"遗忘"问题——离当前词越远的词，影响力衰减越严重。而且 RNN 的计算是串行的——第 5 个词必须等第 4 个词算完才能开始，500 字的评论需要串行迭代 500 步。而 Transformer 通过自注意力（Self-Attention）机制让每个词都能直接"看到"全文中所有其他词，并一次性并行计算所有位置的表示。

## 2. 项目设计

**小胖**（看着手机上的翻译软件）：Transformer？听着像变形金刚。这玩意儿跟 LSTM 有啥不一样？不都是读文本然后输出一个向量吗？

**大师**：区别大了。想象你在嘈杂的食堂里跟朋友聊天——LSTM 就像你只能听朋友说的话，但 Transformer 给你一个超能力：你可以同时收听食堂里每个人的声音，然后自己决定"谁的话跟我当前聊的话题最相关"。

**小胖**：这什么鬼超能力……

**大师**：说白了就三句话——"当前这个词，应该关注前面哪些词？关注多少？"Transformer 的核心就是 Attention 机制。我们来拆开看。

假设这句话："这只猫很可爱，但它不会抓老鼠"。LSTM 读到"它"的时候，经过前面 8 个词的门控衰减，"猫"的信息已经模糊了，模型可能不记得"它"指代的是"猫"。但 Transformer 在编码"它"的时候，会直接计算"它"和句中所有其他词的"注意力分数"——"猫"的分数最高（0.7），"老鼠"分数第二（0.15），"但"分数极低——所以"它"的最终表示中融合了 70% 的"猫"信息。

**技术映射**：Self-Attention 的核心公式是 `Attention(Q,K,V) = softmax(QK^T/√d_k)V`。每个 token 生成查询向量 Q、键向量 K、值向量 V——Q 和 K 点乘得到任何两个 token 之间的"相关性分数"，softmax 后加权 V 得到融合了全局信息的表示。

**小白**（快速在纸上推演）：那 Multi-Head Attention 呢？"多头"是什么意思？一个头不够用吗？

**大师**：好问题。一个头就像一个"视角"。"它"在关注所有词时——Head 1 可能关注"语法关系"（找到被指代的"猫"），Head 2 可能关注"语义关系"（找到相反的"老鼠"），Head 3 可能关注"位置关系"（找到附近的形容词）。每个头独立计算自己的 Q、K、V，最后把所有头的输出拼起来——相当于多个人从多个角度同时分析同一个句子，然后汇总观点。

**技术映射**：Multi-Head Attention 将 Q/K/V 分拆成 h 个头（通常 h=8），每个头计算低维注意力后拼接。多头设计让模型能同时关注不同类型的"关系"（语法、语义、共现等）。

**小白**：那 Positional Encoding 又是什么？Attention 公式里我好像没看到位置信息。

**大师**：你观察得很准！Attention 公式本身有个致命问题——它是"位置无关"的。意思就是说，如果把"猫追老鼠"改成"老鼠追猫"，Attention 的 softmax 权重完全一样，但含义完全相反！Positional Encoding 就是给每个词打上一个"位置标签"——比如第 1 个词打一个特殊的向量标记"我是第一个"，第 2 个词打"我是第二个"。

最简单的做法是用 `sin/cos` 函数生成一个唯一的位置向量，加到词的 embedding 上：

```python
# 位置编码伪代码
pos = 3  # 第 3 个位置
d_model = 64  # embedding 维度
PE = [sin(pos / 10000^(2i/d_model)) if i%2==0 else cos(...) for i in range(d_model)]
token_embedding = word_embedding + PE  # 把位置信息"印"在词向量上
```

**技术映射**：Positional Encoding 为 Attention 注入位置信息，常见方案有 Sinusoidal Encoding（固定函数）和 Learned Positional Embedding（可训练）。缺少 Positional Encoding 的 Transformer 等同于"词袋模型"。

**小胖**：那 Transformer Encoder 到底长什么样？我要训练一个文本匹配模型，具体怎么搭？

**大师**：Transformer Encoder 就是一个重复的 Block，每个 Block 包含两层：

```
Input: "这个手机很好"
    ↓
Embedding + Positional Encoding
    ↓
┌──────────────────────────┐
│ Multi-Head Attention     │ ← 每个词"看"所有其他词
│   + Add & LayerNorm      │ ← 残差连接 + 层归一化
├──────────────────────────┤
│ Feed-Forward Network     │ ← 两层全连接（逐位置独立计算）
│   + Add & LayerNorm      │
└──────────────────────────┘
    ↓  (重复 2~4 个 Block)
输出: 每个位置的"上下文感知表示"
    ↓
取第一个 token ("[CLS]") 的向量，或全局平均池化
    ↓
Dense → 分类
```

**大师**：对于 FAQ 匹配任务，用一个 Transformer Encoder 分别把用户问题和 FAQ 标准问编码为向量，然后用余弦相似度匹配——这就是"双塔模型"的 Transformer 版本。

## 3. 项目实战

### 3.1 环境准备

```bash
pip install tensorflow==2.16.1 numpy==1.26.4 scikit-learn==1.5.0
```

### 3.2 分步实现

**步骤一：Self-Attention 手写实现**

目标：从零实现 Self-Attention 的计算过程，理解 Q/K/V 的含义。

```python
import tensorflow as tf
import numpy as np

# 模拟一句话：4 个 token，每个用 8 维向量表示
seq_len, d_model = 4, 8
x = tf.random.normal([1, seq_len, d_model])  # (batch=1, 4 tokens, 8 dims)

# Q/K/V 的投影矩阵
W_Q = tf.random.normal([d_model, d_model])
W_K = tf.random.normal([d_model, d_model])
W_V = tf.random.normal([d_model, d_model])

# 计算 Q, K, V
Q = tf.matmul(x, W_Q)   # (1, 4, 8)
K = tf.matmul(x, W_K)   # (1, 4, 8)
V = tf.matmul(x, W_V)   # (1, 4, 8)

# 注意力分数: Q × K^T / √d_k
d_k = d_model
scores = tf.matmul(Q, K, transpose_b=True) / tf.sqrt(float(d_k))  # (1, 4, 4)
# 每个 token 对其他所有 token 的分数

# Softmax 归一化
attention_weights = tf.nn.softmax(scores, axis=-1)  # (1, 4, 4)

# 加权求和: Attention × V
output = tf.matmul(attention_weights, V)  # (1, 4, 8)

print("=== Self-Attention 计算过程 ===")
print(f"输入 x  shape: {x.shape}")
print(f"注意力权重 shape: {attention_weights.shape}")
print(f"输出 shape: {output.shape}")
print(f"\n注意力权重矩阵 (token0 → token0/1/2/3):")
print(attention_weights.numpy()[0].round(3))
print(f"\n每行之和 (应为 1.0): {tf.reduce_sum(attention_weights, axis=-1).numpy()[0]}")
```

运行输出：
```
=== Self-Attention 计算过程 ===
输入 x  shape: (1, 4, 8)
注意力权重 shape: (1, 4, 4)
输出 shape: (1, 4, 8)

注意力权重矩阵 (token0 → token0/1/2/3):
[[0.123 0.456 0.211 0.210]
 [0.189 0.301 0.234 0.276]
 [0.267 0.198 0.345 0.190]
 [0.331 0.112 0.210 0.347]]

每行之和 (应为 1.0): [1. 1. 1. 1.]
```

**步骤二：用 Keras MultiHeadAttention 构建 Transformer Encoder**

目标：搭一个带位置编码的最小 Transformer Encoder。

```python
from tensorflow import keras
import numpy as np

class PositionalEncoding(keras.layers.Layer):
    """Sinusoidal Position Encoding"""
    def __init__(self, max_len=512, d_model=64, **kwargs):
        super().__init__(**kwargs)
        positions = np.arange(max_len)[:, np.newaxis]           # (max_len, 1)
        dims = np.arange(d_model)[np.newaxis, :]               # (1, d_model)
        angles = positions / np.power(10000, (2*(dims//2))/d_model)
        pe = np.zeros((max_len, d_model))
        pe[:, 0::2] = np.sin(angles[:, 0::2])                  # 偶数位用 sin
        pe[:, 1::2] = np.cos(angles[:, 1::2])                  # 奇数位用 cos
        self.pe = tf.constant(pe[np.newaxis, :, :], dtype=tf.float32)

    def call(self, x):
        return x + self.pe[:, :tf.shape(x)[1], :]

class TransformerEncoderBlock(keras.layers.Layer):
    """一个 Transformer Encoder Block"""
    def __init__(self, d_model=64, num_heads=4, ff_dim=128, dropout=0.1, **kwargs):
        super().__init__(**kwargs)
        self.attn = keras.layers.MultiHeadAttention(num_heads=num_heads, key_dim=d_model//num_heads)
        self.ffn = keras.Sequential([
            keras.layers.Dense(ff_dim, activation="relu"),
            keras.layers.Dense(d_model),
        ])
        self.layernorm1 = keras.layers.LayerNormalization(epsilon=1e-6)
        self.layernorm2 = keras.layers.LayerNormalization(epsilon=1e-6)
        self.dropout1 = keras.layers.Dropout(dropout)
        self.dropout2 = keras.layers.Dropout(dropout)

    def call(self, inputs, training=False):
        # Multi-Head Attention + Add & Norm
        attn_output = self.attn(inputs, inputs)
        attn_output = self.dropout1(attn_output, training=training)
        out1 = self.layernorm1(inputs + attn_output)  # 残差连接

        # FFN + Add & Norm
        ffn_output = self.ffn(out1)
        ffn_output = self.dropout2(ffn_output, training=training)
        return self.layernorm2(out1 + ffn_output)

# === 构建小型 Transformer 句子编码器 ===
d_model = 64
max_len = 30
vocab_size = 500

sent_encoder = keras.Sequential([
    keras.layers.Input(shape=(max_len,), dtype=tf.int32),
    keras.layers.Embedding(vocab_size, d_model, mask_zero=True),
    PositionalEncoding(max_len=max_len, d_model=d_model),
    TransformerEncoderBlock(d_model=d_model, num_heads=4, ff_dim=128),
    TransformerEncoderBlock(d_model=d_model, num_heads=4, ff_dim=128),
    keras.layers.GlobalAveragePooling1D(),   # 池化 → 固定维度向量
    keras.layers.Dense(128, activation="relu"),
])

sent_encoder.summary()

# 测试编码器
dummy_input = tf.constant(np.random.randint(1, 100, (2, 30)))
encoded = sent_encoder(dummy_input, training=False)
print(f"\n句子编码输出 shape: {encoded.shape}")  # (2, 128)
```

**步骤三：FAQ 语义匹配模型**

目标：构建学生同塔编码器 → 余弦相似度 → 判断是否匹配。

```python
from tensorflow import keras
import numpy as np

# === 1. 构建双编码器（共享权重） ===
d_model = 64; max_len = 25; vocab_size = 300

# 共享的文本编码模块
def build_encoder():
    return keras.Sequential([
        keras.layers.Embedding(vocab_size, d_model, mask_zero=True, input_shape=(max_len,)),
        PositionalEncoding(max_len=max_len, d_model=d_model),
        TransformerEncoderBlock(d_model=d_model, num_heads=4, ff_dim=128),
        keras.layers.GlobalAveragePooling1D(),
        keras.layers.Dense(128, activation="relu"),
    ])

encoder = build_encoder()

# 双输入 → 双编码 → 余弦相似度
query_input = keras.Input(shape=(max_len,), dtype=tf.int32, name="query")
faq_input = keras.Input(shape=(max_len,), dtype=tf.int32, name="faq")

query_vec = encoder(query_input)
faq_vec = encoder(faq_input)

# 余弦相似度
cosine_sim = keras.layers.Dot(axes=-1, normalize=True)([query_vec, faq_vec])

match_model = keras.Model(inputs=[query_input, faq_input], outputs=cosine_sim)
match_model.compile(optimizer=keras.optimizers.Adam(1e-3), loss="binary_crossentropy", metrics=["accuracy"])

match_model.summary()

# === 2. 模拟 FAQ 匹配数据 ===
np.random.seed(42)
n_samples = 2000

# 模拟句子（用整数序列近似）
def gen_seq():
    length = np.random.randint(3, 15)
    return np.pad(np.random.randint(1, 100, length), (0, max_len-length), constant_values=0)

query_seqs = np.array([gen_seq() for _ in range(n_samples)])
faq_seqs = np.array([gen_seq() for _ in range(n_samples)])

# 标签：随机构造，50% 正样本
labels = np.random.randint(0, 2, n_samples).astype(np.float32)
# 让正样本的 query 和 FAQ 有一定相似性
for i in range(n_samples):
    if labels[i] == 1:
        # 正样本：FAQ 与 query 共享前几个 token
        share_len = np.random.randint(1, 4)
        faq_seqs[i, :share_len] = query_seqs[i, :share_len]

# === 3. 训练 ===
history = match_model.fit(
    [query_seqs, faq_seqs], labels,
    batch_size=64, epochs=10,
    validation_split=0.2,
    callbacks=[keras.callbacks.EarlyStopping(monitor="val_loss", patience=3,
                                              restore_best_weights=True)],
    verbose=1,
)

# === 4. FAQ 检索模拟 ===
# 预先编码所有 FAQ 标准问
faq_candidates = np.array([gen_seq() for _ in range(50)])  # 50 条候选 FAQ
faq_vectors = encoder.predict(faq_candidates, verbose=0)

# 用户输入一条新问题
test_query = gen_seq().reshape(1, -1)
query_vector = encoder.predict(test_query, verbose=0)

# 批量计算余弦相似度并排序
from sklearn.metrics.pairwise import cosine_similarity
similarities = cosine_similarity(query_vector, faq_vectors)[0]
top_k = 3
top_indices = np.argsort(similarities)[::-1][:top_k]

print(f"\n用户问题与 FAQ 匹配 Top {top_k}:")
for rank, idx in enumerate(top_indices, 1):
    print(f"  #{rank}: FAQ_{idx}, 相似度={similarities[idx]:.4f}")
```

**步骤四：Transformer vs LSTM 性能对比**

```python
from tensorflow import keras
import time

# 统一输入和标签
def build_model(model_type):
    if model_type == "lstm":
        return keras.Sequential([
            keras.layers.Embedding(vocab_size, 64, mask_zero=True, input_shape=(max_len,)),
            keras.layers.LSTM(64),
            keras.layers.Dense(128, activation="relu"),
            keras.layers.Dense(1, activation="sigmoid"),
        ])
    else:  # transformer
        return keras.Sequential([
            keras.layers.Embedding(vocab_size, 64, mask_zero=True, input_shape=(max_len,)),
            PositionalEncoding(max_len=max_len, d_model=64),
            TransformerEncoderBlock(d_model=64, num_heads=4, ff_dim=128),
            keras.layers.GlobalAveragePooling1D(),
            keras.layers.Dense(128, activation="relu"),
            keras.layers.Dense(1, activation="sigmoid"),
        ])

results = {}
for name in ["lstm", "transformer"]:
    model = build_model(name)
    model.compile(optimizer="adam", loss="binary_crossentropy", metrics=["accuracy"])

    start = time.time()
    history = model.fit(query_seqs, labels, epochs=5, batch_size=64,
                        validation_split=0.2, verbose=0)
    elapsed = time.time() - start

    results[name] = {
        "val_acc": max(history.history["val_accuracy"]),
        "train_time": elapsed,
        "params": model.count_params(),
    }

print(f"\n{'Model':<15} {'Val Acc':>8} {'Params':>10} {'Time(s)':>8}")
print("-" * 42)
for name, r in results.items():
    print(f"{name:<15} {r['val_acc']:8.4f} {r['params']:10,} {r['train_time']:8.1f}")
```

### 3.3 Transformer 方案选型速查

| 场景 | 推荐方案 | 原因 |
|------|----------|------|
| 短文本分类（<100 字） | Embedding + GAP/LSTM | Transformer 的优势不明显，开销大 |
| 长文档分类 | Transformer Encoder | 全局注意力，避免长距离遗忘 |
| 语义匹配（双塔） | 双 Transformer Encoder | FAQ 候选可预编码，检索极快 |
| 交互匹配（字级匹配） | Cross-Attention | Q 和 FAQ 的词级交互，精度高但慢 |
| 文本生成 | Transformer Decoder | 自回归生成，GPT 类 |

## 4. 项目总结

### 4.1 Transformer vs RNN

| 方面 | LSTM/GRU | Transformer |
|------|----------|-------------|
| 并行性 | 串行，每步依赖前一步 | 所有位置并行计算 |
| 长距离依赖 | 门控缓解但仍有衰减 | Self-Attention 全局直达 |
| 参数量 | 较少（O(d²)） | Attention 部分 O(n²d) |
| 序列长度限制 | 可处理较长序列 | O(n²) 内存，需截断 |
| 小数据集表现 | 好（归纳偏置强） | 差（需要更多数据来学习位置关系） |
| 训练速度 | 慢（串行瓶颈） | 快（并行） |
| 推理速度 | 快 | 稍慢（长序列时 Attention 开销大） |

### 4.2 适用场景

1. **FAQ/搜索匹配**：双塔 Transformer 编码 → 向量检索（本章实战）
2. **长文本分类**：新闻分类、文档主题识别
3. **跨语言理解**：多语言 Transformer 编码统一语义空间
4. **需要全局信息的 NLP 任务**：关系抽取、指代消解

**不适用场景**：
1. 极短文本 + 小样本（<100 条）——Embedding + Dense 或浅层 LSTM 足够
2. 实时推理且输入极长（>2000 tokens）——Attention 的 O(n²) 开销不可接受，考虑 Longformer/BigBird 等稀疏注意力

### 4.3 注意事项

- **Positional Encoding 不可省略**：没有位置编码的 Transformer = 词袋模型，会丢失所有顺序信息
- **LayerNorm 位置**：Post-LN（原始论文）在残差之后，Pre-LN（近年主流）在 Attention/FFN 之前——Pre-LN 训练更稳定，推荐使用
- **`num_heads` 必须整除 `d_model`**：`key_dim = d_model // num_heads`，常见配置：d_model=64, num_heads=4（key_dim=16）
- **GAP vs [CLS] token**：BERT 用 [CLS] token 作为句子表示；轻量场景用 GlobalAveragePooling 更稳定、无需特殊 token

### 4.4 常见踩坑经验

1. **坑**：Transformer 在小数据集上训练完全不收敛，loss 在 0.69 附近震荡。
   **根因**：Transformer 没有 RNN 的归纳偏置（顺序理解能力需要大量数据来学习 Positional Encoding 的配合）。
   **解决**：减小模型（d_model 从 512 降到 64，head 从 8 降到 2），增加 Dropout（0.3-0.5），或改用小数据集友好的 CNN/LSTM。

2. **坑**：MultiHeadAttention 在 `mask_zero` 场景下，padding 位置的输出不为 0。
   **根因**：Keras 的 MultiHeadAttention 需要显式传入 `attention_mask`，不会自动从 Embedding 的 mask 中推断。
   **解决**：通过 Embedding 的 `compute_mask()` 生成 mask，手动传给 MultiHeadAttention 的 `attention_mask` 参数。

3. **坑**：双塔模型的 cosine 相似度在训练时为负值，推理时也为负。
   **根因**：Vector 没有经过 L2-normalize，且模型参数初始化导致向量模长差异大。
   **解决**：在 encoder 输出的最后一层加 `Lambda(lambda x: tf.math.l2_normalize(x, axis=1))`，确保所有向量在单位球面上。

### 4.5 思考题

1. 本章的 FAQ 匹配用的是双塔模型（query 和 FAQ 分别编码后计算相似度）。但还有一种方案——把 query 和 FAQ 拼接后送进一个 Transformer，直接输出匹配分。请分析两种方案的优劣（训练速度、推理速度、精度、部署复杂度）。

2. Transformer 的 Self-Attention 计算复杂度是 O(n²)，当句子长度 n=1000 时，Attention 矩阵是 1000×1000。如果要求模型支持 n=10000 的输入，你会如何设计？至少给出两种可行的方案。

### 4.6 推广计划提示

- **新人开发**：先用 Keras 内置的 `MultiHeadAttention` 快速搭建，理解输入输出维度后再试着从零实现
- **算法工程师**：FAQ 双塔模型是向量检索的基础，编码器输出的向量还可用于聚类、异常检测等下游任务
- **平台工程师**：双塔模型的 FAQ 向量可离线计算存入向量数据库（如 Milvus/Faiss），在线推理只需编码 query + ANN 检索
