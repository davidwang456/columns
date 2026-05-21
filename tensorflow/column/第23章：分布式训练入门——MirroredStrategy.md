# 第23章：分布式训练入门——MirroredStrategy

## 1. 项目背景

算法工程师大刘负责的文本分类模型（Transformer Encoder, 12 层）训练越来越慢——单张 V100 (32GB) 上训练一个 epoch 需要 45 分钟，跑完 50 个 epoch 需要近 2 天。更糟糕的是，显存不够——batch_size 只能设到 16，batch 太小导致训练不稳定，loss 震荡严重。

公司的 GPU 服务器上有 4 张 V100，但大刘的代码只用了 1 张——其他 3 张卡在空转。他尝试手动把模型复制到 4 张卡上分别训练，但遇到了三个问题：(1) 4 张卡上的模型权重不同步——每张卡各学各的，最后不知道该用哪个；(2) 数据怎么分配到 4 张卡上？；(3) batch_size 怎么算——每张卡 16 还是总共 16？

组长老陈看了一眼代码，加了两行：

```python
strategy = tf.distribute.MirroredStrategy()
with strategy.scope():
    model = create_model()
    model.compile(...)
```

训练时间从 45 min/epoch 降到了 14 min/epoch，4 张卡的利用率都接近 100%。

**痛点放大**：随着模型规模和数据量的增长，单 GPU 训练面临两大瓶颈——(1) 计算瓶颈：训练时间从小时级增长到天级；(2) 显存瓶颈：大模型放不进一张卡。分布式训练通过"多卡并行"解决这两个问题——MirroredStrategy 是最简单的数据并行方案，代码改动极小。

## 2. 项目设计

**小胖**（指着一排 GPU 服务器）：4 张卡不就是 4 个工人吗？把数据分成 4 份，每人干一份，最后汇总不就行了？这有什么难的？

**大师**：思路对——这就是"数据并行"（Data Parallelism）。但你忽略了关键问题：**怎么汇总**？4 个工人各自算完自己的那一份后，每个人手里的"经验"（梯度）不一样——你怎么让他们达成共识？

**小胖**：取平均值不就行了？

**大师**：对！MirroredStrategy 就是这么做的——每张卡独立计算自己那份数据的梯度，然后 AllReduce（全局归约）取平均，用平均梯度去更新权重。关键是——所有卡上的权重在任何时刻都必须保持一致（"镜面反射"——Mirrored 的由来）。

**技术映射**：MirroredStrategy = 数据并行 + 同步参数更新。每张 GPU 有一份完整的模型副本，各自处理一个 mini-batch 子集，`AllReduce` 通信同步梯度后统一更新。

**小白**（在本子上画了一个环）：AllReduce 具体是怎么工作的？4 张卡之间怎么通信？

**大师**：最直观的方案是——每张卡把梯度发给"中心服务器"，中心求完平均后再发回给所有人。但这样中心就成了瓶颈。MirroredStrategy 默认用 NCCL（NVIDIA Collective Communications Library），它用的是更高效的 **Ring AllReduce**——4 张卡形成环，数据在环上流动，每人只和邻居通信。这样通信量是 O(2(N-1)/N) 而非 O(N)，N 张卡扩展性更好。

```
Ring AllReduce (4 GPU):
  GPU0 → GPU1 → GPU2 → GPU3 → GPU0
  (数据在环上转一圈，每个人都拿到了平均值)
```

**技术映射**：NCCL 的 Ring AllReduce 将通信量从 O(N²) 降到 O(N)，N 卡训练时通信开销不会线性增长。

**小白**：那 batch_size 和学习率在多卡时怎么调整？单卡 batch=16，4 卡是 4×16=64 还是 16？

**大师**：这是最容易被忽略的细节。MirroredStrategy 的 `GLOBAL_BATCH_SIZE = per_replica_batch_size × num_replicas`。如果单卡 batch=16，4 卡全局 batch=64。在这种情况下，学习率通常需要线性缩放——`new_lr = old_lr × num_replicas`。因为现在每个 step 看到的样本量变了 4 倍，梯度更稳定，可以用更大的学习率。

```python
# 单卡: lr=1e-3, batch=16
# 4 卡: base_lr=1e-3 * 4 = 4e-3, batch=16 (每卡) → 全局 batch=64
```

但这不是绝对的——某些模型（特别是带 BN 的）对 batch 大小敏感。经验法则是：**不超过单卡时的 2-4 倍学习率**，且需要通过实验验证。

**小胖**：那代码改动有多大？我看老陈只加了两行？

**大师**：核心代码改动确实只需两行——但隐含着"所有模型创建、编译、数据加载都必须在 `strategy.scope()` 内"的约束。数据分发也是自动的——`model.fit(dataset)` 时，MirroredStrategy 自动把 dataset 的每个 batch 均匀切分给各张卡。

```python
strategy = tf.distribute.MirroredStrategy()
print(f"可用 GPU 数量: {strategy.num_replicas_in_sync}")

with strategy.scope():
    model = create_model()
    model.compile(optimizer=..., loss=..., metrics=...)

# 训练时自动分发
model.fit(train_ds, epochs=10)  # 每张卡自动拿到 batch_size/replicas 的数据
```

**技术映射**：`strategy.scope()` 上下文管理器的核心作用——将变量创建、优化器状态、checkpoint 管理都纳入分布式策略的控制。在 scope 外创建的变量不会被分发。

## 3. 项目实战

### 3.1 环境准备

```bash
pip install tensorflow==2.16.1 numpy==1.26.4
```

> **注意**：本章代码在单 GPU 或无 GPU 环境也能运行——MirroredStrategy 在无 GPU 时会自动退化为单 CPU 模式，方便开发调试。多卡验证需要真正的多 GPU 环境。

### 3.2 分步实现

**步骤一：单卡 vs 多卡训练框架对比**

目标：理解 MirroredStrategy 的代码模式，在单机环境模拟多卡行为。

```python
import tensorflow as tf
import numpy as np
import time

# 检测可用 GPU
gpus = tf.config.list_physical_devices("GPU")
print(f"检测到 {len(gpus)} 个 GPU")
for gpu in gpus:
    print(f"  {gpu}")

# === 1. 创建分布式策略 ===
strategy = tf.distribute.MirroredStrategy()
print(f"分布式副本数: {strategy.num_replicas_in_sync}")

# === 2. 在 scope 内构建和编译模型 ===
def create_model():
    return tf.keras.Sequential([
        tf.keras.layers.Conv2D(64, 3, activation="relu", input_shape=(64, 64, 3)),
        tf.keras.layers.MaxPooling2D(2),
        tf.keras.layers.Conv2D(128, 3, activation="relu"),
        tf.keras.layers.GlobalAveragePooling2D(),
        tf.keras.layers.Dense(10, activation="softmax"),
    ])

with strategy.scope():
    model = create_model()
    model.compile(
        optimizer=tf.keras.optimizers.Adam(1e-3),
        loss="sparse_categorical_crossentropy",
        metrics=["accuracy"],
    )

# === 3. 准备数据（注意 GLOBAL_BATCH_SIZE） ===
per_replica_batch = 32
global_batch = per_replica_batch * strategy.num_replicas_in_sync
print(f"Per-replica batch: {per_replica_batch}, Global batch: {global_batch}")

# 模拟数据
np.random.seed(42)
X = np.random.rand(2000, 64, 64, 3).astype(np.float32)
y = np.random.randint(0, 10, 2000)

train_ds = tf.data.Dataset.from_tensor_slices((X, y)) \
    .shuffle(2000).batch(global_batch).prefetch(tf.data.AUTOTUNE)

# === 4. 训练（Strategy 会自动分发数据到各 GPU） ===
print("\n开始分布式训练...")
start = time.time()
history = model.fit(train_ds, epochs=5, verbose=1)
elapsed = time.time() - start
print(f"训练耗时: {elapsed:.1f}s")
```

**步骤二：验证 AllReduce 对梯度的影响**

目标：对比单卡和多卡在相同数据上的梯度是否一致（验证同步更新的正确性）。

```python
import tensorflow as tf
import numpy as np

strategy = tf.distribute.MirroredStrategy()

# 简单模型
with strategy.scope():
    model = tf.keras.Sequential([
        tf.keras.layers.Dense(4, activation="relu", input_shape=(8,)),
        tf.keras.layers.Dense(1),
    ])
    optimizer = tf.keras.optimizers.SGD(0.1)

# 固定数据
x = tf.constant(np.random.randn(8, 8).astype(np.float32))
y = tf.constant(np.random.randn(8, 1).astype(np.float32))

# 手动计算一次梯度更新，观察参数变化
with strategy.scope():
    @tf.function
    def train_step(inputs, labels):
        with tf.GradientTape() as tape:
            predictions = model(inputs, training=True)
            loss = tf.reduce_mean(tf.square(predictions - labels))
        grads = tape.gradient(loss, model.trainable_variables)
        optimizer.apply_gradients(zip(grads, model.trainable_variables))
        return loss

    # 在策略的分布式上下文中执行
    per_replica_loss = strategy.run(train_step, args=(x, y))
    total_loss = strategy.reduce(tf.distribute.ReduceOp.SUM, per_replica_loss, axis=None)
    print(f"分布式训练 step loss: {total_loss.numpy():.6f}")

# 对比：不用策略的单卡训练
single_model = tf.keras.Sequential([
    tf.keras.layers.Dense(4, activation="relu", input_shape=(8,)),
    tf.keras.layers.Dense(1),
])
single_opt = tf.keras.optimizers.SGD(0.1)

with tf.GradientTape() as tape:
    pred = single_model(x, training=True)
    loss = tf.reduce_mean(tf.square(pred - y))
grads = tape.gradient(loss, single_model.trainable_variables)
single_opt.apply_gradients(zip(grads, single_model.trainable_variables))
print(f"单卡训练 step loss: {loss.numpy():.6f}")
```

**步骤三：学习率缩放与 batch_size 关系验证**

目标：通过实验验证 GBN（Global Batch Normalization）对收敛的影响。

```python
import tensorflow as tf
import numpy as np

def train_with_batch_size(global_batch, lr_scale=1.0):
    """用指定全局 batch_size 训练，返回验证准确率"""
    strategy = tf.distribute.MirroredStrategy()

    # 模拟数据
    X = np.random.randn(4000, 20).astype(np.float32)
    y = np.random.randint(0, 2, 4000).astype(np.float32)

    ds = tf.data.Dataset.from_tensor_slices((X, y)) \
        .shuffle(4000).batch(global_batch).prefetch(tf.data.AUTOTUNE)

    with strategy.scope():
        model = tf.keras.Sequential([
            tf.keras.layers.Dense(64, activation="relu", input_shape=(20,)),
            tf.keras.layers.Dense(32, activation="relu"),
            tf.keras.layers.Dense(1, activation="sigmoid"),
        ])
        base_lr = 1e-3 * lr_scale
        model.compile(optimizer=tf.keras.optimizers.Adam(base_lr),
                      loss="binary_crossentropy", metrics=["accuracy"])

    history = model.fit(ds, epochs=5, verbose=0, validation_split=0.2)
    return max(history.history["val_accuracy"])

# 实验：固定单卡 batch，变多卡数
print("=== Batch Size × LR 缩放实验 ===\n")
configs = [
    # (global_batch, lr_scale, 描述, 相当于)
    (32, 1.0, "单卡 bs=32", "基准"),
    (64, 1.5, "单卡 bs=64 或 2卡×32", "bs翻倍, lr×1.5"),
    (128, 2.0, "4卡×32 或 单卡 bs=128", "bs翻4倍, lr×2"),
]

for gb, lr_scale, desc, equiv in configs:
    acc = train_with_batch_size(gb, lr_scale)
    print(f"  {desc:<18} ({equiv:<12}): val_acc = {acc:.4f}")
```

**步骤四：多卡常见问题诊断**

```python
# === 多卡训练诊断脚本 ===
import tensorflow as tf

def diagnose_distributed_training():
    """诊断分布式训练环境"""
    print("=== 分布式训练环境诊断 ===\n")

    # 1. GPU 可见性
    gpus = tf.config.list_physical_devices("GPU")
    print(f"1. 可见 GPU: {len(gpus)} 个")
    for g in gpus:
        print(f"   {g}")

    # 2. 显存分配策略
    for g in gpus:
        try:
            tf.config.experimental.set_memory_growth(g, True)
            print(f"2. GPU 显存增长模式: 已开启 (memory_growth)")
        except RuntimeError:
            print(f"2. GPU 显存增长模式: 已初始化，无法修改")

    # 3. NCCL 是否可用
    try:
        strategy = tf.distribute.MirroredStrategy()
        print(f"3. MirroredStrategy 创建成功: {strategy.num_replicas_in_sync} 个副本")
    except Exception as e:
        print(f"3. MirroredStrategy 创建失败: {e}")

    # 4. TF_CONFIG 环境变量（多机训练才需要）
    import os
    tf_config = os.environ.get("TF_CONFIG", "未设置 (单机训练)")
    print(f"4. TF_CONFIG: {tf_config}")

    # 5. 混合精度（Volta+ GPU 支持）
    from tensorflow.keras import mixed_precision
    policy = mixed_precision.global_policy()
    print(f"5. 精度策略: {policy.name}")

diagnose_distributed_training()
```

### 3.3 多卡训练 Checklist

| 检查项 | 正确做法 | 常见错误 |
|--------|---------|---------|
| `strategy.scope()` | 所有模型创建和编译在 scope 内 | 在 scope 外创建模型，权重不同步 |
| Global batch size | `per_replica_batch × num_replicas` | 直接用单卡 batch_size，实际 batch 变小了 |
| 学习率 | 建议 `lr × sqrt(num_replicas)` 或线性缩放 | 不调整 lr，收敛变慢 |
| 数据集 | `dataset.batch(GLOBAL_BATCH)` | batch 后没考虑副本数 |
| Checkpoint | 在 scope 内创建并保存 | scope 外保存可能只保存了部分权重 |
| `model.evaluate` | 自动聚合指标 | 手动计算需用 `strategy.reduce()` |
| 随机性 | 设置 `tf.random.set_seed()`（每卡独立） | 不设置种子导致实验不可复现 |

## 4. 项目总结

### 4.1 分布式策略对比

| 策略 | 适用场景 | 优点 | 缺点 |
|------|---------|------|------|
| **MirroredStrategy** | 单机多 GPU | 代码改动极小，同步更新 | 受单机 GPU 数量限制（通常 4-8 卡） |
| **MultiWorkerMirroredStrategy** | 多机多 GPU | 扩展到多台机器 | 需要配置 TF_CONFIG 和网络 |
| **ParameterServerStrategy** | 大规模稀疏模型 | 参数服务器架构，适合 Embedding | 异步更新，一致性弱（第 24 章） |
| **TPUStrategy** | Google TPU | 极高吞吐 | 仅限 TPU 硬件 |
| **OneDeviceStrategy** | 单设备测试 | 调试专用 | 无加速 |

### 4.2 适用场景

1. **单机多卡同步训练**：日常训练的主流方案（4-8 GPU）
2. **大批量训练**：通过多卡模拟大 batch_size，改善 BN 统计量稳定性
3. **快速原型验证**：单卡写好 → 加两行切到多卡 → 验证吞吐提升
4. **混合精度 + 多卡**：在 scope 内开启混合精度（`mixed_precision.set_global_policy("mixed_float16")`）

**不适用场景**：
1. 模型本身就比单卡显存大（放不进一张卡）——MirroredStrategy 每卡都有完整模型副本，不会减小单卡显存。需要模型并行（GPipe/Mesh-TensorFlow）
2. 离线单独推理——用单卡即可，Strategy 在推理时没有收益

### 4.3 注意事项

- **`model.save()` 必须在 scope 内**：否则保存的是单副本的权重（可能丢失同步后的完整权重）
- **自定义 `train_step` 需兼容 Strategy**：在覆写的 `train_step` 中，`self.optimizer` 和梯度更新需确保在 Strategy 的分布式上下文中工作
- **NCCL 超时与通信故障**：多卡通信依赖 NVLink/PCIe，如果某张卡过热或显存错误，NCCL 会 hang 住整个训练进程——`export NCCL_DEBUG=INFO` 查看日志
- **数据 sharding**：`dataset.batch(GLOBAL_BATCH)` 后，Strategy 会自动按副本数均匀切分。如果用 `distribute_datasets_from_function`，需要手动实现切分逻辑

### 4.4 常见踩坑经验

1. **坑**：4 卡训练反而比单卡慢。
   **根因**：模型太小，计算时间 < GPU 间通信时间——NCCL AllReduce 的开销大于并行计算带来的收益。
   **解决**：增加模型复杂度或单卡 batch_size。小模型（参数 < 100 万）单卡可能更好。

2. **坑**：2 卡训练正常，4 卡训练 loss NaN。
   **根因**：学习率线性缩放到 4×，结合 BN 的小 batch 统计量不稳定，梯度爆炸。
   **解决**：使用 LARS/LAMB 优化器（专为大 batch 设计），或降低 lr 缩放因子到 `sqrt(num_replicas)`。

3. **坑**：`model.fit` 时 `validation_data` 在多卡上指标不一致。
   **根因**：验证数据只在主副本（replica 0）上评估，但 metrics 的状态可能被其他副本的残余更新污染。
   **解决**：在 `test_step` 覆写中确保验证只在 replica 0 执行，或用 `strategy.run()` 每个副本都跑然后 `strategy.reduce()` 求平均。

### 4.5 思考题

1. 你有 4 张 V100 (32GB)，需要训练一个 12GB 的模型。当前单卡 batch_size=64，训练稳定。如果改为 4 卡 MirroredStrategy，batch_size 有哪些调整方案？每种方案对收敛速度和显存占用有何影响？

2. MirroredStrategy 默认使用 NCCL Ring AllReduce。如果 8 张卡中有一张故障，训练会怎样？你如何设计一个容错机制来处理 GPU 故障？提示：考虑 `tf.distribute.experimental.PreemptionWatcher`。

### 4.6 推广计划提示

- **算法工程师**：在 `config.py` 中添加 `USE_DISTRIBUTED=True/False` 开关，一键切换单卡/多卡模式
- **平台工程师**：制做标准化的多卡训练 Docker 镜像（预装 NCCL + cuDNN 匹配版本），并在 K8s 中配置 GPU 亲和性
- **测试工程师**：写一个"多卡一致性测试"——同数据单卡 vs 多卡训练，最终 loss 差异应 < 1%（同步更新的保证）
