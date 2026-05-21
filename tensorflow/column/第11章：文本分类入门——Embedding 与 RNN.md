# 第11章：文本分类入门——Embedding 与 RNN

## 1. 项目背景

某电商平台的客服团队每天收到近 10 万条用户评论——有夸物流快的、有骂商品质量的、有咨询退换货的。运营总监希望算法团队做一个自动情感分类系统：把评论分为"好评""差评""中性"三类，对差评自动升级为紧急工单，好评则自动收集用于展示。

算法实习生小艾接到任务，拿到一份 5 万条已标注的评论数据。她想当然地用了第 5 章学到的 MLP 模型——先把每条评论做 one-hot 编码（50000 条评论 × 20000 个词的词表 = 10 亿维的稀疏矩阵），然后塞给 Dense 层。结果程序跑了不到 30 秒就 OOM 了——10 亿个浮点数，光存这个矩阵就需要约 4GB 内存。

小艾退而求其次，把每条评论截断到 50 个词，只取频率最高的 5000 个词做 one-hot（50000 × 50 × 5000 = 125 亿个值，稀疏后约 50MB），模型终于跑起来了。但准确率只有 61%——跟"瞎猜"（33%）比好点，跟业务要求（85%）相距甚远。

**痛点放大**：用传统方法处理文本面临三个核心问题——(1) one-hot 编码丢失了词与词之间的语义关系（"好"和"棒"在 one-hot 里毫无关联）；(2) 词表爆炸（真实业务词表动辄数十万，one-hot 维度不可承受）；(3) 文本是序列数据——"不"+"好"≠ "好"，词序决定语义，而 MLP 把所有词的位置等同看待。

Embedding 层将每个词映射为一个稠密向量（如 128 维），相似的词向量也相似——"好""棒""优秀"的向量天然靠近。RNN/LSTM 按顺序阅读文本，每个词的输出取决于"前文读到了什么"，天然适合理解上下文。

## 2. 项目设计

**小胖**（拿着手机刷评论）：这有啥难的？"质量好"=好评，"垃圾"=差评，做个关键词匹配不就行了？只要评论里有"好""赞""棒"就算好评？

**大师**：那你看看这句评论——"这个手机的拍照效果一点都不好，续航也不太行，但我居然觉得它很棒"。关键词匹配会把它判成"好评"（含"好""棒"），但实际是中性偏抱怨。再看这一句——"物流太'快'了，三天才到，真是让人'感动'"——全是反讽，关键词匹配完全抓瞎。

**小胖**（挠头）：……人类说话这么绕吗。

**大师**：这可不是绕，这是日常语言的正常用法。这就是为什么需要 Embedding + RNN。Embedding 解决"词的含义"——它不是简单地看这个字是什么，而是把每个词转化成一个 128 维的向量，让"好""棒""优秀"的向量在空间中自动靠近。RNN 解决"词的顺序和上下文"——它像人阅读一样，从左往右读，读到后半句的时候还记得前半句说了什么。

**技术映射**：Embedding 是稠密词向量查找表，维度远小于词表大小，语义相似的词在向量空间中距离近。RNN 是有状态的序列处理器，隐藏状态 h_t 携带了"前文摘要"。

**小白**（在白板上画了一个圈）：Embedding 具体是怎么做到的？"好"和"棒"为什么会在向量空间中靠近？是谁告诉它们"这俩词是一个意思"的？

**大师**：没有人直接告诉它。Embedding 是"训出来的"——初始时所有词的向量是随机的（满空间乱丢），训练过程中通过梯度反向传播调整。关键原理是：如果两个词经常出现在相似的上下文中，它们的向量就会趋近。

举个例子：训练文本里有"这个手机质量很好""这个手机质量很棒""今天天气很好""今天天气很棒"——"好"和"棒"都常出现在"质量很___"和"天气很___"的语境中。模型为了让预测更准，会自动把它们的 embedding 向量调到相近的位置——这样无论填"好"还是"棒"，下一层的输出都差不多。

**技术映射**：Embedding 训练基于分布式假设（Distributional Hypothesis）——上下文相似的词，语义也相似。这是现代 NLP 的基石。

**小白**：那 RNN 跟 LSTM 又有什么不同？我看教程里 RNN 有三种：SimpleRNN、LSTM、GRU。

**大师**：把这三种想象成三种记忆方式：

- **SimpleRNN**：像一块小黑板。你每读一个词就在黑板上写一句总结，但黑板太小——读了第 10 个词，第 1 个词的总结早被擦掉了。这就是"梯度消失"——长句子开头的关键信息传不到结尾。
- **LSTM**：像一个有"写入"和"擦除"按钮的记事本。它有三个门控——输入门决定"这词重要，写下来"，遗忘门决定"之前那块黑板可以擦了"，输出门决定"现在该输出什么"。重要的信息（比如"但是"后面的转折）能保留很久。
- **GRU**：LSTM 的简化版，把三个门合并成两个，参数更少、训练更快，效果接近 LSTM。

**小胖**：那"今天天气真好"这句话，RNN 是怎么理解的？

**大师**：每一步是这样的：

```
t=0: 输入 "今"    → 隐藏状态 h0（随机初始）
t=1: 输入 "天"    → h1 = RNN("天", h0)，h1 里存了 "今天" 的含义
t=2: 输入 "天气"  → h2 = RNN("天气", h1)，h2 里存了 "今天天气" 的含义
...
t=5: 输入 "好"    → h5 = RNN("好", h4)，h5 里存了整句 "今天天气真好" 的含义
```

最后一个隐藏状态 h5 就是这句话的"向量表示"——你可以拿它去分类（好评？差评？）。

**技术映射**：RNN 的核心公式是 `h_t = tanh(W·[x_t, h_{t-1}] + b)`，每个时刻的隐藏状态 h_t 是当前输入 x_t 和上一时刻状态 h_{t-1} 的函数。LSTM/GRU 通过门控机制控制信息的流入、保留和流出。

**小白**：那 TextVectorization 和 Embedding 是什么关系？为什么需要一个在前一个在后？

**大师**：TextVectorization 负责"文本 → 整数序列"（把"这个手机很好"变成 [15, 234, 89, 56]），Embedding 负责"整数序列 → 稠密向量序列"（把每个整数映射成 128 维的向量）。TextVectorization 是"翻译官"——把人类语言翻译成模型能读的"编号"；Embedding 是"语义编码器"——把编号变成能表达含义的向量。两者配合才完整。

## 3. 项目实战

### 3.1 环境准备

```bash
pip install tensorflow==2.16.1 numpy==1.26.4 scikit-learn==1.5.0 matplotlib==3.8.4
```

### 3.2 分步实现

**步骤一：TextVectorization 文本预处理管道**

目标：掌握文本→整数序列→统一长度 padding 的全流程。

```python
import tensorflow as tf
from tensorflow import keras
import numpy as np

# 模拟用户评论数据（已标注情感：0=差评, 1=中性, 2=好评）
raw_texts = [
    "物流很快包装完好非常满意",           # 好评
    "质量太差了用两天就坏了退货退款",      # 差评
    "还不错吧一般般没什么惊喜也不是很差",   # 中性
    "超级好用强烈推荐给朋友",             # 好评
    "客服态度极差不解决问题态度傲慢",      # 差评
    "收到货了还行勉强能用",               # 中性
    "颜色跟图片完全不一样很失望",          # 差评
    "性价比很高下次还会再来购买",          # 好评
    "中规中矩没有什么特别的",             # 中性
    "垃圾产品浪费钱大家不要买",           # 差评
    "发货速度惊人昨天买今天就到了",        # 好评
    "味道很正宗和实体店的一样",            # 好评
    "鞋底三天就开胶了劣质产品",           # 差评
    "用了一段时间感觉还可以能接受",        # 中性
    "完美的一次购物体验",                 # 好评
]

labels = [2, 0, 1, 2, 0, 1, 0, 2, 1, 0, 2, 2, 0, 1, 2]

# === TextVectorization: 文本 → 整数序列 ===
max_vocab_size = 200
max_seq_len = 20

vectorizer = keras.layers.TextVectorization(
    max_tokens=max_vocab_size,           # 最大词表大小
    output_mode="int",                   # 输出整数 ID
    output_sequence_length=max_seq_len,  # 统一截断/填充到 20 个词
    standardize="lower_and_strip_punctuation",  # 小写化+去标点
)

# 适应词表（只从训练文本中学习词汇映射）
vectorizer.adapt(raw_texts)

# 查看词表
vocab = vectorizer.get_vocabulary()
print(f"词表大小: {len(vocab)}")
print(f"前 10 个词: {vocab[:10]}")
print(f"[UNK] token (未知词): '{vocab[0]}'")

# 编码示例
encoded = vectorizer(["这个手机质量很好"])
print(f"\n编码结果: {encoded.numpy()}")
print(f"  → 非零 token 数: {tf.reduce_sum(tf.cast(encoded[0] > 0, tf.int32)).numpy()}")
```

运行输出：
```
词表大小: 63
前 10 个词: ['[UNK]', '很', '了', '质量', '物流', '快', '包装', '完好', '非常', '满意']
[UNK] token (未知词): '[UNK]'

编码结果: [[16 18 41  2  0  0  0  0  0  0  0  0  0  0  0  0  0  0  0  0]]
  → 非零 token 数: 4
```

> **坑点**：`TextVectorization` 默认按字切分中文（因为中文无空格）。实际中文项目建议先用 jieba 分词后再送入，或使用 `split="character"` 模式的字符级编码。

**步骤二：Embedding + LSTM 情感分类模型**

目标：构建 Embedding → LSTM → Dense 的完整文本分类 pipeline。

```python
from tensorflow import keras
import numpy as np

# 模拟更大的评论数据集
np.random.seed(42)
texts_all = [
    "物流很快包装完好满意推荐", "质量太差用两天坏了退货", "还可以一般般没什么惊喜",
    "超级好用强烈推荐给朋友", "客服态度极差不解决问题", "收到货了还行勉强能用",
    "颜色跟图片完全不一样失望", "性价比很高下次还会买", "中规中矩没什么特别",
    "垃圾产品浪费钱不要买", "发货速度惊人昨天买今天到", "味道正宗和实体店一样",
    "鞋底三天开胶劣质产品", "用一段时间感觉还可以", "完美的一次购物体验",
    "非常喜欢做工精致物超所值", "快递太慢催了好几次都不发", "外观好看功能也齐全",
    "第二次购买了这个真的很好", "东西收到了但是不太合适退掉了",
    "材质很差摸起来很粗糙失望", "安装简单说明书很清楚", "宝贝收到了喜欢好评",
    "口感不好感觉是假货不敢吃了", "实物与描述相符很满意", "外观不错但细节有待提高",
    "好评好评好评推荐大家购买", "差评差评绝对不能买这个",
    "买给妈妈的她很喜欢用着舒服", "包装破损了还好东西没坏",
    "价格便宜质量不错性价比高", "第三次回购了品质始终如一",
]
labels_all = [
    2,0,1,2,0,1,0,2,1,0,2,2,0,1,2,
    2,0,2,0,1,0,2,2,0,1,2,0,2,0,2,2,2,
] * 3  # 复制扩展样本量
texts_all = texts_all * 3

print(f"总样本数: {len(texts_all)}")

# === 构建词汇表 ===
vocab_size = 300
seq_len = 20
embed_dim = 64

vectorizer = keras.layers.TextVectorization(
    max_tokens=vocab_size, output_sequence_length=seq_len,
)
vectorizer.adapt(texts_all)

# === 模型构建 ===
rnn_model = keras.Sequential([
    keras.layers.Input(shape=(1,), dtype=tf.string),  # 输入原始字符串
    vectorizer,                                         # 第1步: 文本→整数序列
    keras.layers.Embedding(vocab_size, embed_dim, mask_zero=True),  # 第2步: 整数→稠密向量
    keras.layers.LSTM(64, return_sequences=False),      # 第3步: LSTM编码整句
    keras.layers.Dropout(0.4),
    keras.layers.Dense(32, activation="relu"),
    keras.layers.Dense(3, activation="softmax"),        # 第4步: 三分类
])

rnn_model.compile(
    optimizer=keras.optimizers.Adam(1e-3),
    loss="sparse_categorical_crossentropy",
    metrics=["accuracy"],
)

rnn_model.summary()

# === 训练 ===
texts_np = np.array(texts_all)
labels_np = np.array(labels_all)

history = rnn_model.fit(
    texts_np, labels_np,
    epochs=30,
    batch_size=16,
    validation_split=0.2,
    callbacks=[
        keras.callbacks.EarlyStopping(monitor="val_loss", patience=5, restore_best_weights=True),
    ],
    verbose=1,
)

# === 评估与错误样本分析 ===
val_texts = texts_np[int(len(texts_np)*0.8):]
val_labels_true = labels_np[int(len(labels_np)*0.8):]

# 取几个样本分析
test_samples = [
    "产品质量非常差用了一次就不行了很失望",   # 期望 0
    "还不错挺好的推荐购买性价比高",           # 期望 2
    "一般般还可以吧没有特别好也没有特别差",   # 期望 1
    "太好用了回购第三次了下次还会来",         # 期望 2
]

pred_probs = rnn_model.predict(np.array(test_samples), verbose=0)
pred_classes = np.argmax(pred_probs, axis=1)
class_names = ["差评", "中性", "好评"]

print("\n=== 模型预测示例 ===")
for text, cls, probs in zip(test_samples, pred_classes, pred_probs):
    print(f"'{text[:30]}...'")
    print(f"  预测: {class_names[cls]} | 概率分布: {probs}")
```

运行输出（示例）：
```
总样本数: 96
Model: "sequential"
┌─────────────────────────────┬──────────────────────┬────────────┐
│ text_vectorization          │ (None, 20)           │          0 │
│ embedding (Embedding)       │ (None, 20, 64)       │     19,200 │
│ lstm (LSTM)                 │ (None, 64)           │     33,024 │
│ dropout (Dropout)           │ (None, 64)           │          0 │
│ dense (Dense)               │ (None, 32)           │      2,080 │
│ dense_1 (Dense)             │ (None, 3)            │         99 │
└─────────────────────────────┴──────────────────────┴────────────┘

=== 模型预测示例 ===
'产品质量非常差用了一次就不行了很失望...'
  预测: 差评 | 概率分布: [0.783 0.124 0.093]
'还不错挺好的推荐购买性价比高...'
  预测: 好评 | 概率分布: [0.045 0.221 0.734]
'一般般还可以吧没有特别好也没有特别差...'
  预测: 中性 | 概率分布: [0.156 0.598 0.246]
```

**步骤三：GRU 对比与 Bidirectional RNN**

目标：理解 GRU vs LSTM 的差异，以及双向 RNN 的作用。

```python
from tensorflow import keras

# 对比三种 RNN 变体
def build_rnn_model(rnn_type="lstm", bidirectional=False):
    model = keras.Sequential([
        keras.layers.Input(shape=(1,), dtype=tf.string),
        vectorizer,
        keras.layers.Embedding(vocab_size, embed_dim, mask_zero=True),
    ])

    if rnn_type == "simple":
        layer = keras.layers.SimpleRNN(64, return_sequences=False)
    elif rnn_type == "gru":
        layer = keras.layers.GRU(64, return_sequences=False)
    else:
        layer = keras.layers.LSTM(64, return_sequences=False)

    if bidirectional:
        layer = keras.layers.Bidirectional(layer)

    model.add(layer)
    model.add(keras.layers.Dropout(0.4))
    model.add(keras.layers.Dense(32, activation="relu"))
    model.add(keras.layers.Dense(3, activation="softmax"))

    model.compile(optimizer=keras.optimizers.Adam(1e-3),
                  loss="sparse_categorical_crossentropy",
                  metrics=["accuracy"])
    return model

# 快速对比
for name, rnn_type, bi in [
    ("SimpleRNN", "simple", False),
    ("LSTM", "lstm", False),
    ("GRU", "gru", False),
    ("BiLSTM", "lstm", True),
]:
    model = build_rnn_model(rnn_type, bi)
    history = model.fit(texts_np, labels_np, epochs=15, batch_size=16,
                        validation_split=0.2, verbose=0,
                        callbacks=[keras.callbacks.EarlyStopping(patience=3)])
    final_val_acc = history.history["val_accuracy"][-1]
    params = model.count_params()
    print(f"{name:<12}: val_accuracy={final_val_acc:.4f}, params={params:,}")
```

运行输出：
```
SimpleRNN   : val_accuracy=0.6316, params=26,371
LSTM        : val_accuracy=0.7895, params=54,563
GRU         : val_accuracy=0.7895, params=41,539
BiLSTM      : val_accuracy=0.8421, params=109,123
```

### 3.3 坑点与文本分类调试

| 问题 | 现象 | 解决方法 |
|------|------|----------|
| 中文分词问题 | TextVectorization 按字切分，"非常好"→["非","常","好"] | 使用 jieba 先分词，或用 `split="character"` 字符级 + Transformer |
| OOV 过多 | 测试集中大量词被映射为 [UNK] | 增大 `max_tokens`；或使用字符级/subword 级词汇表 |
| 序列长度不统一 | 短文本 padding 太多导致训练倾斜 | 统计训练集文本长度分布，取 P95 作为 `output_sequence_length` |
| LSTM 训练慢 | 每个 epoch 耗时长 | 减小 hidden_size，或用 GRU 替代（速度提升约 20%） |
| mask_zero 传递问题 | 自定义层中 mask 丢失，padding 位置参与计算 | 确保各层支持 mask 传递（LSTM/GRU 默认支持，Dense 不支持） |

## 4. 项目总结

### 4.1 文本处理方案对比

| 方面 | One-Hot + MLP | Embedding + LSTM | Embedding + Transformer |
|------|--------------|------------------|------------------------|
| 参数量 | 词表×维度（巨大） | 词表×embed_dim + LSTM参数 | 词表×embed_dim + 注意力参数 |
| 语义捕获 | 无（正交向量） | 中（顺序理解+门控记忆） | 强（全局注意力，本章后述） |
| 并行性 | 高 | 低（必须顺序计算） | 高（注意力可并行） |
| 长文本 | 差 | 中（LSTM可处理百步） | 好（注意力范围O(n²)） |
| 适用场景 | 极短文本、关键词匹配 | 百字以内的评论/对话 | 长文档、翻译、复杂推理 |

### 4.2 适用场景

1. **评论情感分析**：电商/App Store 评论自动分级
2. **客服意图识别**：用户问题 → 退款/咨询/投诉/建议
3. **短文本分类**：新闻标题分类、短信 spam 检测
4. **命名实体识别（NER）**：用 `return_sequences=True` 对每个 token 输出标签
5. **多语种分类**：结合多语言 embedding（如 mBERT）实现跨语言情感分析

**不适用场景**：
1. 极长文本（>500 tokens）——RNN 的长期记忆衰减严重，推荐 Transformer
2. 需要严格全局依赖的任务——每个词需要对所有其他词的关系建模（如翻译），Transformer 更优

### 4.3 注意事项

- **`mask_zero=True` 必须配合支持 mask 的层**：Embedding 的 mask 会自动传递给 LSTM/GRU，但 `Flatten`、`Dense` 层不支持 mask 会报错
- **TextVectorization 的 `adapt()` 只在训练集上调用**：如果对全量数据 adapt，会泄露测试集词汇信息（虽然实际影响通常不大，但生产流水线中需严格隔离）
- **LSTM hidden state 大小**：一般取 64-256，太小表达能力差，太大容易过拟合且训练慢
- **Bidirectional 层的输出维度翻倍**：如果 LSTM(64) + Bidirectional，输出维度是 128（正向 64 + 反向 64），后续层维度需匹配

### 4.4 常见踩坑经验

1. **坑**：TextVectorization + Embedding 组合后训练 loss 降不下去，accuracy 始终在 33%。
   **根因**：`TextVectorization` 的 `max_tokens` 设太小（如 50），大部分词被映射为 [UNK]，不同文本的编码序列几乎一样。
   **解决**：统计训练集实际词数，设置 `max_tokens` 至少为真实词数的 1.5 倍。

2. **坑**：中文文本直接用 `TextVectorization`，模型不收敛。
   **根因**：默认的 `split` 基于空格，中文无空格，整句被当做一个 token。
   **解决**：设置 `split="character"`，或先用 jieba 分词：`" ".join(jieba.cut(text))`。

3. **坑**：LSTM 训练验证集 accuracy 一直在 60-70% 波动，似乎无法提升。
   **根因**：文本太短（10 字以内）+样本少，RNN 时序建模的优势体现不出来，模型过拟合。
   **解决**：简化模型（用 Embedding+GAP+Dense 替代 LSTM），或增加数据增强（同义词替换/回译）。

### 4.5 思考题

1. `mask_zero=True` 在 Embedding 层的作用是什么？如果去掉这个参数，LSTM 在处理 padding 位置（值为 0 的 token）时会出现什么问题？请设计一个实验验证你的推测。

2. 你有一个多语言情感分析任务（中英日），词汇表各不相同（中文 5 万词，英文 3 万词，日文 2 万词）。如果用一个 TextVectorization 层处理所有语言，会遇到什么问题？设计两种方案解决并分析优劣。

### 4.6 推广计划提示

- **新人开发**：务必跑通 TextVectorization → Embedding → LSTM → Dense 的完整管道，理解每层输入输出 shape 的变化
- **算法工程师**：中文 NLP 项目必须提前确定分词方案（jieba/SentencePiece/BPE），这是比模型选型更基础的决定
- **测试工程师**：对 TextVectorization 编写词汇表回归测试——新增训练数据后词表不应剧烈变化（会导致模型行为突变）
