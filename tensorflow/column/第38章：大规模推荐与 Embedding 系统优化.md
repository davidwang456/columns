# 第38章：大规模推荐与 Embedding 系统优化

## 1. 项目背景

某内容平台（类似今日头条）的推荐系统需要为 2 亿用户和 5000 万篇内容做个性化推荐。算法架构师老罗面对的是一个"超大稀疏特征"挑战：

- 用户侧特征：用户 ID（2 亿）、年龄（100 个桶）、地域（3000 个城市）、兴趣标签（10 万维的多值特征）
- 内容侧特征：内容 ID（5000 万）、作者 ID（200 万）、分类（200 类）、标签（5 万维）
- 总特征维度：约 2.5 亿维
- 如果全部 one-hot → 2.5 亿维 × 128 维 Embedding = 32GB × 4 bytes = 128GB（单卡完全放不下）

老罗目前用了第 14 章的双塔模型 + 第 24 章的 ParameterServerStrategy。但衍生出了新问题：(1) Embedding 表更新不均衡——热门内容每天被更新几万次，冷门内容几乎不更新；(2) 30% 的内容是"冷启动物品"（新文章），没有历史交互数据；(3) 每天有 100 万新用户和 50 万新内容，Embedding 需要频繁增量更新，但全量表重训成本太高。

**痛点放大**：大规模推荐系统的挑战不只是"模型大"——更是数据稀疏（Sp4）、冷启动（Cold Start）、特征更新不均衡（Long-tail）、在线学习与离线训练一致性（Train-Serving Skew）。这些问题的解决方案涉及 Embedding 分片、特征淘汰、冷热分离、增量更新等工程技巧。

## 2. 项目设计

**小胖**（看着 128GB 这个数字）：128GB 不是单卡 32GB 的 4 倍吗？用 4 张卡分片不就完了？ParameterServerStrategy 本来就是干这个的！

**大师**：对，但"分片"只是第一步。真正的挑战在分片之后——你的 Embedding 表里，头部 1% 的热门内容占了 90% 的更新频率，剩下的 99% 内容几乎不被访问。如果均匀分片——热门内容集中在某几个 PS 上，那几个 PS 会被打爆（hotspot），其他 PS 空转。

这就是**冷热分离（Hot/Cold Separation）**的策略——把访问频次高的热门 Embedding 放在独立的"热 PS"上（高频读写、小数据量），冷门 Embedding 放在"冷 PS"上（低频访问、大数据量、甚至可以放 SSD）。热 PS 用 CPU + 大内存保证低延迟，冷 PS 可以用更大的分片 + SSD 降低成本。

**技术映射**：Embedding 冷热分离 = 按访问频次分区存储，热区（高频 ID）放高速存储（DRAM），冷区（低频 ID）放低速存储（SSD/HDD），配合 LRU 缓存实现高效访问。

**小白**：冷启动问题怎么解决？新文章没有交互数据，Embedding 训不出来。

**大师**：冷启动的解决方案依赖于"特征泛化"——不只用"文章 ID"这种稀疏特征，还用"文章属性"这种稠密特征。一个新文章来了，它的"ID Embedding"确实是随机的，但它的"作者 ID Embedding""分类标签 Embedding""标题 BERT 编码"这些特征已经训练好了——模型可以通过这些"非 ID 特征"推断新文章的表示。

```python
# 冷启动物品的 Embedding = 属性特征加权组合
cold_item_vec = (
    0.3 * author_embedding(author_id)     # 作者的 embedding
    + 0.2 * category_embedding(cat_id)    # 分类的 embedding
    + 0.3 * bert_encoding(title_text)     # 标题的语义编码
    + 0.2 * random_noise                  # 一点点噪声 (探索)
)
# 随着交互数据累积，ID Embedding 被训练，权重逐渐向 ID Embedding 倾斜
```

**技术映射**：冷启动的通用解决思路——用"可泛化特征"（内容属性/作者/语义编码）弥补"ID Embedding"的缺失。冷启动物品的表示 = 属性特征加权 fusion。

**小胖**：那增量训练呢？每天 50 万新内容，不能每次都全量重训吧？

**大师**：增量训练（Incremental Training）的核心思路是——昨天的模型 checkpoint + 今天的新数据 → 今天的模型。不是从头训练，而是"在昨天的肩膀上继续跑"。实现方式：

1. 从 checkpoint 恢复模型和优化器状态
2. 冻结旧内容的 Embedding（或降低其学习率）
3. 只对新内容/新用户的 Embedding 用正常学习率训练
4. 配合"特征准入/淘汰机制"——Embedding 表太大时，淘汰掉最近 N 天没被访问的特征

**技术映射**：增量训练 = checkpoint 恢复 + 新样本训练 + 特征生命周期管理（准入/淘汰）。避免全量重训的成本，同时保证模型及时反映最新的用户和内容。

## 3. 项目实战

### 3.1 环境准备

```bash
pip install tensorflow==2.16.1 numpy==1.26.4
```

### 3.2 分步实现

**步骤一：冷热分离 Embedding 设计**

目标：实现一个简单的冷热分离 Lookup 层。

```python
import tensorflow as tf
import numpy as np

class HotColdEmbedding(tf.keras.layers.Layer):
    """冷热分离 Embedding 层——模拟生产实现"""

    def __init__(self, vocab_size, embed_dim, hot_ratio=0.1, **kwargs):
        super().__init__(**kwargs)
        self.vocab_size = vocab_size
        self.embed_dim = embed_dim
        self.hot_size = int(vocab_size * hot_ratio)    # 热区大小
        self.cold_size = vocab_size - self.hot_size     # 冷区大小

    def build(self, input_shape):
        # 热区 Embedding（高频访问）
        self.hot_embed = self.add_weight(
            name="hot_embed",
            shape=(self.hot_size, self.embed_dim),
            initializer="glorot_uniform",
            trainable=True,
        )
        # 冷区 Embedding（低频访问）
        self.cold_embed = self.add_weight(
            name="cold_embed",
            shape=(self.cold_size, self.embed_dim),
            initializer="glorot_uniform",
            trainable=True,
        )

    def call(self, inputs):
        """inputs: (batch,) 整数 ID 序列"""
        # 判断每个 ID 属于热区还是冷区
        hot_mask = inputs < self.hot_size
        cold_mask = inputs >= self.hot_size

        # 热区查找
        hot_ids = tf.boolean_mask(inputs, hot_mask)
        hot_vecs = tf.nn.embedding_lookup(self.hot_embed, hot_ids)

        # 冷区查找（ID 需要 shift 到冷区本地索引）
        cold_ids = tf.boolean_mask(inputs, cold_mask) - self.hot_size
        cold_vecs = tf.nn.embedding_lookup(self.cold_embed, cold_ids)

        # 合并（按原始顺序排回去）
        # 简化处理: 用 scatter 恢复顺序
        batch_size = tf.shape(inputs)[0]
        result = tf.TensorArray(tf.float32, size=batch_size, dynamic_size=False)

        # (此处简化——实际生产中用 tf.where + tf.gather 更高效)
        return self._merge(hot_vecs, cold_vecs, hot_mask, cold_mask, batch_size)

    def _merge(self, hot_vecs, cold_vecs, hot_mask, cold_mask, batch_size):
        """合并热区和冷区结果"""
        # 创建空的结果张量
        result = tf.zeros([batch_size, self.embed_dim])

        # 填充热区
        hot_indices = tf.where(hot_mask)
        hot_indices = tf.reshape(hot_indices, [-1])
        result = tf.tensor_scatter_nd_update(result, tf.expand_dims(hot_indices, 1), hot_vecs)

        # 填充冷区
        cold_indices = tf.where(cold_mask)
        cold_indices = tf.reshape(cold_indices, [-1])
        result = tf.tensor_scatter_nd_update(result, tf.expand_dims(cold_indices, 1), cold_vecs)

        return result

# 测试
layer = HotColdEmbedding(vocab_size=1000000, embed_dim=64, hot_ratio=0.1)
_ = layer(tf.constant([0, 500, 150000, 999999]))  # trigger build
print("热区大小: 100,000 (Top 10%)")
print("冷区大小: 900,000 (剩余 90%)")

# 查找——混合热区和冷区 ID
test_ids = tf.constant([0, 50, 100000, 500000, 999999])
vecs = layer(test_ids)
print(f"输入 ID: {test_ids.numpy()}")
print(f"输出 shape: {vecs.shape}")
```

**步骤二：冷启动 Embedding Fusion**

目标：为冷启动物品构造"属性融合 Embedding"。

```python
import tensorflow as tf

class ColdStartItemEmbedding(tf.keras.layers.Layer):
    """冷启动物品 Embedding：ID + 属性特征加权融合"""

    def __init__(self, vocab_size, embed_dim,
                 num_categories, num_authors, **kwargs):
        super().__init__(**kwargs)
        self.vocab_size = vocab_size
        self.embed_dim = embed_dim
        self.num_categories = num_categories
        self.num_authors = num_authors

        # 融合权重（可训练，决定 ID 和属性各自的重要性）
        self.fusion_weights = None
        self.warmup_threshold = 10  # 少于 10 次曝光的认为是冷启动物品

    def build(self, input_shape):
        # ID Embedding
        self.id_embed = self.add_weight(
            name="item_id_embed",
            shape=(self.vocab_size, self.embed_dim),
            initializer="glorot_uniform",
        )
        # 分类 Embedding
        self.cat_embed = self.add_weight(
            name="category_embed",
            shape=(self.num_categories, self.embed_dim),
            initializer="glorot_uniform",
        )
        # 作者 Embedding
        self.author_embed = self.add_weight(
            name="author_embed",
            shape=(self.num_authors, self.embed_dim),
            initializer="glorot_uniform",
        )
        # 融合权重 (sigmoid → [0,1] 之间)
        self.fusion_weights = self.add_weight(
            name="fusion_weights",
            shape=(3,),
            initializer="zeros",
            trainable=True,
        )

    def call(self, item_ids, cat_ids, author_ids, exposure_counts=None):
        """构造物品表示 = ID + 分类 + 作者 的加权融合"""
        # 各特征 Embedding
        id_vec = tf.nn.embedding_lookup(self.id_embed, item_ids)        # (B, E)
        cat_vec = tf.nn.embedding_lookup(self.cat_embed, cat_ids)       # (B, E)
        author_vec = tf.nn.embedding_lookup(self.author_embed, author_ids)

        # 归一化融合权重 (softmax)
        fused_w = tf.nn.softmax(self.fusion_weights)  # (3,)

        # 冷启动调整：曝光少的物品降低 ID 权重，提高属性权重
        if exposure_counts is not None:
            cold_mask = tf.cast(
                tf.less(exposure_counts, self.warmup_threshold), tf.float32
            )  # (B,)
            # 冷启动时: id_w -= 0.2, cat_w += 0.1, author_w += 0.1
            id_adj = fused_w[0] - 0.2 * cold_mask[..., tf.newaxis]
            cat_adj = fused_w[1] + 0.1 * cold_mask[..., tf.newaxis]
            author_adj = fused_w[2] + 0.1 * cold_mask[..., tf.newaxis]
        else:
            id_adj, cat_adj, author_adj = fused_w[0], fused_w[1], fused_w[2]

        # 加权求和
        item_vec = (
            id_vec * id_adj +
            cat_vec * cat_adj +
            author_vec * author_adj
        )
        return item_vec

# 测试：模拟冷启动物品
layer = ColdStartItemEmbedding(
    vocab_size=10000, embed_dim=64,
    num_categories=200, num_authors=5000,
)

item_ids = tf.constant([0, 1, 2])
cat_ids = tf.constant([5, 10, 15])
author_ids = tf.constant([100, 200, 300])
# 物品 0 曝光 100 次（热）、物品 1 曝光 3 次（冷）、物品 2 曝光 0 次（冷）
exposure = tf.constant([100.0, 3.0, 0.0])

vecs = layer(item_ids, cat_ids, author_ids, exposure)
print(f"物品向量 shape: {vecs.shape}")
print(f"物品0 (热, 曝光100): 以 ID embedding 为主")
print(f"物品1 (冷, 曝光3):   属性权重提高")
print(f"物品2 (冷, 曝光0):   属性权重最高")
```

**步骤三：增量训练框架——checkpoint 恢复 + 特征淘汰**

目标：实现从 checkpoint 恢复 + 只训练新特征 + 淘汰过期 Embedding。

```python
import tensorflow as tf
import numpy as np
import os
import tempfile

# 模拟增量训练流程
tmpdir = tempfile.mkdtemp()
ckpt_dir = os.path.join(tmpdir, "checkpoints")

# === Phase 1: 初始全量训练 ===
model = tf.keras.Sequential([
    tf.keras.layers.Dense(64, activation="relu", input_shape=(32,)),
    tf.keras.layers.Dense(1, activation="sigmoid"),
])
model.compile(optimizer="adam", loss="binary_crossentropy")

X_old = np.random.randn(500, 32).astype(np.float32)
y_old = np.random.randint(0, 2, 500).astype(np.float32)
model.fit(X_old, y_old, epochs=3, verbose=0)

# 保存 checkpoint
ckpt = tf.train.Checkpoint(model=model, optimizer=model.optimizer)
ckpt_mgr = tf.train.CheckpointManager(ckpt, ckpt_dir, max_to_keep=3)
ckpt_mgr.save()
print(f"Phase 1 完成 — checkpoint 已保存")

# === Phase 2: 增量训练 (恢复 + 新数据) ===
restored_model = tf.keras.Sequential([
    tf.keras.layers.Dense(64, activation="relu", input_shape=(32,)),
    tf.keras.layers.Dense(1, activation="sigmoid"),
])
restored_model.compile(optimizer="adam", loss="binary_crossentropy")

restored_ckpt = tf.train.Checkpoint(model=restored_model, optimizer=restored_model.optimizer)
latest = tf.train.latest_checkpoint(ckpt_dir)
restored_ckpt.restore(latest).expect_partial()
print(f"Phase 2: 从 {latest} 恢复模型")

# 新数据训练（降低学习率做增量微调）
restored_model.optimizer.learning_rate.assign(1e-4)
X_new = np.random.randn(200, 32).astype(np.float32)
y_new = np.random.randint(0, 2, 200).astype(np.float32)
restored_model.fit(X_new, y_new, epochs=2, verbose=0)
print(f"Phase 2 完成 — 增量训练")

# 清理
import shutil
shutil.rmtree(tmpdir, ignore_errors=True)

# === 特征淘汰策略说明 ===
print("""
=== Embedding 特征生命周期管理 ===

特征准入 (Admission):
  新 ID 出现 → 创建 Embedding 并随机初始化
  配额管理: 表大小到达上限时，LRU 淘汰最久未使用的

特征淘汰 (Eviction):
  决策因子:
  1. Last Access Time: 最近 N 天未被访问
  2. Frequency: 总访问次数 < 阈值 T
  3. Gradient Norm: 最近的梯度范数接近 0（对 loss 无贡献）

淘汰策略:
  if (days_since_last_access > 30 OR
      (total_impressions < 10 AND days_since_last_access > 7)):
      evict(embedding_id)

淘汰后处理:
  - 新请求访问已淘汰 ID → 重新初始化（冷启动）
  - 渐进式淘汰: 每天最多淘汰 5% 的 Embedding，防止突然性能下降
""")
```

**步骤四：在线学习一致性（Train-Serving Skew 解决）**

目标：确保离线训练和在线 Serving 的特征处理完全一致。

```python
import tensorflow as tf

print("""
=== 训练-推理一致性保障 ===

问题: 离线训练和在线推理的特征处理逻辑容易不一致，
      导致 model.predict() 和 Serving 输出差异大。

解决方案:

1. 特征工程统一使用 tf.Transform (TFX):
   - 离线: Transform 组件产出 transform_graph
   - 在线: Serving 加载同一个 transform_graph
   - 保证: 同一个函数，同一个输出

2. Serving 签名中嵌入预处理:
   @tf.function(input_signature=[tf.TensorSpec(shape=[None], dtype=tf.string)])
   def serve(raw_inputs):
       # 所有预处理在模型内完成
       tokens = tokenizer(raw_inputs)     # 分词
       padded = pad_sequences(tokens)
       embedded = embedding_lookup(padded)
       predictions = model(embedded)
       return predictions

3. 特征一致性测试:
   训练环境 predict → Serving predict → TFLite predict
   三者输出差异 < 1e-5 (float32) / < 0.02 (int8)

4. 实时特征快照:
   离线训练用"过去 7 天"的特征快照训练
   在线推理用"此刻"的实时特征
   → 特征延迟差异（Feature Lag）需要被容忍
   → 用滑动窗口统计量做特征时间对齐
""")
```

### 3.3 大规模推荐系统架构图

```
┌─────────────────────────────────────────────────────────┐
│                   离线训练 (TFX Pipeline)                 │
│  ┌─────────────┐  ┌──────────────┐  ┌───────────────┐  │
│  │ Data Sources │→│ Transform    │→│ Trainer (PS)  │  │
│  │ (Hive/Kafka)│  │ (特征工程)   │  │ 双塔/DeepFM   │  │
│  └─────────────┘  └──────────────┘  └───────────────┘  │
│                                            ↓             │
│                                     ┌──────────────┐    │
│                                     │ Embedding DB │    │
│                                     │ (Faiss/Milvu)│    │
│                                     └──────────────┘    │
└─────────────────────────────────────────────────────────┘
                          ↓
┌─────────────────────────────────────────────────────────┐
│                    在线推理 (Serving)                      │
│  ┌──────────┐  ┌───────────┐  ┌────────────┐           │
│  │ 请求接入  │→│ 特征服务   │→│ DNN 推理   │→ 响应      │
│  │          │  │ (实时特征) │  │ + ANN 检索 │           │
│  └──────────┘  └───────────┘  └────────────┘           │
│                         ↓                                │
│                  ┌──────────────┐                        │
│                  │ 特征回传/Kafka│ → 在线学习             │
│                  └──────────────┘                        │
└─────────────────────────────────────────────────────────┘
```

## 4. 项目总结

### 4.1 大规模推荐系统挑战与方案

| 挑战 | 表现 | 解决方案 |
|------|------|---------|
| **参数规模** | Embedding 表 > 100GB | 冷热分离 + PS 分片 |
| **数据稀疏** | 99% 特征从未出现 | 特征泛化 + 冷启动 fusion |
| **更新不均衡** | 热特征被打爆，冷特征不更新 | 冷热分区 + 差异化 lr |
| **冷启动** | 新用户/新物品无数据 | 属性 Embedding 加权组合 |
| **增量更新** | 每天 50 万新内容 | 增量训练 + 特征准入/淘汰 |
| **训练-推理偏斜** | 离线 AUC 高在线 CTR 低 | tf.Transform 统一 + 预处理嵌入模型 |

### 4.2 适用场景

1. **超大规模推荐**：亿级用户+千万级物品的召回/排序
2. **广告 CTR 预估**：稀疏特征密集，冷启动问题突出
3. **搜索排序**：query-doc 匹配 + 实时特征更新
4. **社交 Feed 流**：用户兴趣快速漂移，需要增量更新能力

**不适用场景**：
1. 中等规模推荐（百万级用户）——双塔 + MirroredStrategy 即可，不需要 PS
2. 稠密特征模型（ResNet/BERT 类）——没有超大 Embedding 表，常规分布式足够

### 4.3 注意事项

- **冷热分离的分界点**：不是固定的比例——需要通过访问频次分布（如 Zipf 分布）确定最优切分点（通常 top 5-10% 为热区）
- **Embedding 维度与稀疏度的关系**：特征越稀疏、频率越低，Embedding 维度可以越小（低频特征信息量少，高维 Embedding 容易过拟合）
- **增量训练的学习率策略**：旧 Embedding 用极小学习率（如 1e-6），新 Embedding 用正常学习率（如 1e-3），防止大量旧参数被少量新数据"冲坏"

### 4.4 常见踩坑经验

1. **坑**：冷启动 fusion 的权重全部退化到 ID Embedding（属性权重趋近于 0），冷启动物品完全没有泛化。
   **根因**：训练数据中全是热物品（曝光 > 100 次），模型从来没学到"属性 Embedding 有用"——因为它不需要这个信息就能拟合训练集。
   **解决**：训练时对热物品随机 mask 掉 ID Embedding（以一定概率回退到属性 Embedding），强制模型学习属性泛化。

2. **坑**：增量训练后，旧验证集上的 AUC 大幅下降（从 0.82 到 0.75），但新数据上的 AUC 提升。
   **根因**：增量训练过度拟合了新数据（concept drift），"忘记"了旧数据的分布模式（catastrophic forgetting）。
   **解决**：增量训练时混入 20-30% 的旧数据样本（replay buffer），或对旧参数施加 L2 正则化限制其偏离原始值的程度。

3. **坑**：特征淘汰后，Serving 请求命中已淘汰 ID 时返回全 0 向量——推荐结果质量崩溃。
   **根因**：离线淘汰了某些 ID，但 Serving 的在线请求队列中仍有这些 ID 的请求（存在时间窗口）。
   **解决**：淘汰 + 冷启动兜底（返回属性融合 Embedding 而非全 0 向量）；淘汰和模型同步部署之间留至少 1 小时的缓冲期。

### 4.5 思考题

1. 你的推荐系统有"用户 Embedding"和"物品 Embedding"。用户 Embedding 基于过去 30 天的行为窗口计算，物品 Embedding 基于全局交互。如果一个用户突然改变了兴趣（如从"看电视剧"变成"看球赛"），两个 Embedding 系统各自如何响应？如何加速用户 Embedding 对兴趣变化的响应？

2. 特征淘汰策略中，如果错误地淘汰了一个"虽然最近没被访问但很快会再火"的 Embedding（如季节性内容），系统能否自我修复？设计一个"软淘汰"机制——不直接删除，而是降级到冷区并给一个"复活期"。

### 4.6 推广计划提示

- **算法工程师**：推荐模型的冷启动和增量更新策略需要在离线实验中完整验证，确认线上指标无退化再部署
- **平台工程师**：Embedding 服务（热区/冷区）的延迟和可用性需要独立监控，SLA 与模型推理分开
- **测试工程师**：增量训练的 Golden Dataset 需要包含新旧数据混合的样本，验证模型不过度遗忘旧知识
