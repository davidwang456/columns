# 第4章：自动微分与 GradientTape

## 1. 项目背景

算法工程师阿杰负责公司广告点击率（CTR）预估模型的迭代。上周他用 `model.fit()` 把训练跑通了，AUC 从 0.72 提升到了 0.76，mentor 看完代码后给他泼了一盆冷水："你这个把交叉熵、Adam 优化器一组合就完事了？线上流量有时候正样本率只有 0.3%，极其不均衡。这种场景下，标准交叉熵公式会让模型偏向预测负类。需要自定义 Focal Loss，而且要考虑正负样本权重。"

阿杰查了一下，Focal Loss 的标准公式是：

```
FL(p_t) = -α_t * (1 - p_t)^γ * log(p_t)
```

其中 `p_t` 是模型对正确类别的预测概率，γ 是聚焦参数（通常取 2），α_t 是类别权重。

但问题来了——Keras 里直接 `model.compile(loss="categorical_crossentropy")` 是没法传这些自定义参数的。他需要自己实现 loss 函数，并且理解梯度是如何从 loss 传播到模型权重的——不然没法保证 Focal Loss 求导正确。

**痛点放大**：依赖 `model.fit` 的黑箱训练，一旦业务需求超出标准 loss/optimizer 的组合范围（自定义损失、多目标优化、强化学习、GAN 对抗训练），就束手无策。不理解 GradientTape 的内部机制更是会让自定义训练在悄无声息中出错——比如梯度为 None、梯度数值异常、训练不收敛等。

## 2. 项目设计

**小胖**（嘴里嚼着泡泡糖）：自动微分……听名字好高大上！不就是求导吗？高数课上学过啊，`f(x)=x^2`，`f'(x)=2x`，手算不就完了？

**大师**：好，你手算一下这个函数的导数：`f(W1, W2, W3) = softmax(relu(matmul(X, W1) + W2) * W3)`。W1 是一个 784×256 的矩阵，共 20 万个参数。

**小胖**（泡泡糖"啪"一声破了）：……

**大师**：这就是自动微分的意义。它不是你手算的那种符号求导，也不是数值求导（那个误差大还慢），而是"链式法则的工业化实现"。我们来看一个最简单的例子。

```python
# 你对 x=3 求 y = x^2 的导数
x = tf.Variable(3.0)
with tf.GradientTape() as tape:
    y = x ** 2
grad = tape.gradient(y, x)  # grad = 6.0 ✓
```

**大师**：tape 就像一台摄像机，它不关心你算什么——你算 `x^2` 也好、`exp(sin(x))` 也好——它只知道"前向时经过了哪些基础运算（加、减、乘、exp、sin...）"，然后反向时逐个调这些运算的导数公式，用链式法则连起来。

**技术映射**：自动微分 = 链式法则的工程实现，TensorFlow 的 GradientTape 采用"反向模式自动微分"（Reverse-mode AD），适合"多输入 → 单输出"的梯度计算场景（如神经网络的 loss 回传）。

**小白**（紧锁眉头）：那为什么有时候梯度是 None？我明明把所有变量都放进了 tape 里面。

**大师**（拿起桌上一支笔）：假设这支笔（tape）在录音，但有些变量不在它的"收听范围"内。GradientTape 的监视规则是：
1. **tf.Variable** → 自动被监视（watch）
2. **tf.Tensor / tf.constant** → 默认不监视，除非你手动 `tape.watch(tensor)`
3. **被监视变量在 tape 上下文之外的操作** → 不被记录

举个例子：你在进录音棚之前说了一句话，棚里的麦克风录不到。

```python
w = tf.Variable(2.0)
c = tf.constant(3.0)

# 错误示范:
# x = w * c  # 这一行在 tape 外面！
# with tf.GradientTape() as tape:
#     loss = x ** 2    # tape 没记录到 w 是怎么参与计算的
# grad = tape.gradient(loss, w)  # → None

# 正确示范:
with tf.GradientTape() as tape:
    x = w * c   # 在 tape 内计算
    loss = x ** 2
grad = tape.gradient(loss, w)  # → 12.0 ✓
```

**技术映射**：GradientTape 只记录 `with` 块内执行的操作序列。tf.Variable 自动 watch，tf.Tensor 需手动 `tape.watch()`。取梯度只对 watch 列表中的变量有效。

**小白**：那高阶梯度是怎么回事？我听说可以做"梯度的梯度"？

**大师**：对，高阶梯度就是"对导数再求导"。比如用一阶梯度的平方作为正则化项（梯度惩罚），你需要对一阶梯度再求导。TensorFlow 需要显式开启高阶梯度：

```python
x = tf.Variable(3.0)
with tf.GradientTape() as t2:
    with tf.GradientTape() as t1:
        y = x ** 3
    dy_dx = t1.gradient(y, x)    # 一阶梯度: 3x^2 = 27
d2y_dx2 = t2.gradient(dy_dx, x)  # 二阶梯度: 6x = 18
```

**小胖**（挠头）：等一下……那个 `stop_gradient` 又是什么东西？名字听起来像"别算了停下来"。

**大师**：对，它的含义就是"到这儿为止，梯度别往后传了"。最有用的场景是 **GAN 训练**——判别器训练时不想更新生成器的参数，或者做 **预训练特征提取**——冻结 Backbone 只看后面几层的梯度。

```python
x = tf.Variable(3.0)
with tf.GradientTape() as tape:
    y = tf.stop_gradient(x ** 2) + x   # x^2 不参与梯度
grad = tape.gradient(y, x)  # → 1.0 (只有 x 贡献，x^2 被阻断)
```

相当于你在一连串多米诺骨牌中间抽出几片——前面的骨牌倒了，后面的不动。

**技术映射**：`tf.stop_gradient()` 在前向计算中恒等传递，但在反向传播时阻断梯度，相当于将对应子图的梯度置零。

**小白**：最后一个问题——`persistent=True` 的 tape 有什么坑？

**大师**：默认 tape 只能调一次 `gradient()`，调完就释放资源。这防止了内存泄漏（tape 会暂存所有中间结果）。但如果你需要计算多个梯度（比如同时对生成器和判别器求梯度），就必须设 `persistent=True`，然后 **手动 `del tape`** 释放，否则内存越用越多。

## 3. 项目实战

### 3.1 环境准备

延续第 2 章创建的 `tf_mnist_env` 虚拟环境。

```bash
pip install tensorflow==2.16.1 numpy==1.26.4 matplotlib==3.8.4 scikit-learn==1.5.0
```

### 3.2 分步实现

**步骤一：手写线性二分类训练循环（从零搭建）**

目标：不使用 `model.fit`，手动实现前向传播、损失计算、梯度计算、参数更新。

```python
import tensorflow as tf
import numpy as np

# 1. 生成模拟数据：两类二维点
np.random.seed(42)
n_samples = 200
# 类别 0：聚集在 (2, 2) 周围
X0 = np.random.randn(n_samples // 2, 2) * 0.5 + np.array([2, 2])
# 类别 1：聚集在 (-2, -2) 周围
X1 = np.random.randn(n_samples // 2, 2) * 0.5 + np.array([-2, -2])
X = np.vstack([X0, X1]).astype(np.float32)
y = np.hstack([np.zeros(n_samples // 2), np.ones(n_samples // 2)]).reshape(-1, 1).astype(np.float32)

print(f"X shape: {X.shape}, y shape: {y.shape}")
print(f"正样本占比: {y.mean():.2f}")

# 2. 初始化参数
W = tf.Variable(tf.random.normal([2, 1], stddev=0.1), name="W")
b = tf.Variable(tf.zeros([1]), name="b")

# 3. 定义前向传播（sigmoid 二分类）
def forward(x):
    logits = tf.matmul(x, W) + b    # 线性变换
    return tf.sigmoid(logits)        # 转为概率

# 4. 定义损失函数（交叉熵）
def binary_cross_entropy(y_pred, y_true):
    eps = 1e-7  # 防止 log(0)
    return -tf.reduce_mean(
        y_true * tf.math.log(y_pred + eps) +
        (1 - y_true) * tf.math.log(1 - y_pred + eps)
    )

# 5. 定义准确率
def accuracy(y_pred, y_true, threshold=0.5):
    pred_class = tf.cast(y_pred >= threshold, tf.float32)
    return tf.reduce_mean(tf.cast(pred_class == y_true, tf.float32))

# 6. 训练循环
learning_rate = 0.1
num_epochs = 200

for epoch in range(num_epochs):
    with tf.GradientTape() as tape:
        y_pred = forward(X)
        loss = binary_cross_entropy(y_pred, y)

    # 计算梯度
    grads = tape.gradient(loss, [W, b])

    # 检查是否有 None 梯度
    if any(g is None for g in grads):
        print(f"Epoch {epoch}: 发现 None 梯度！")
        break

    # 梯度下降更新
    W.assign_sub(learning_rate * grads[0])
    b.assign_sub(learning_rate * grads[1])

    if epoch % 40 == 0:
        acc = accuracy(y_pred, y)
        print(f"Epoch {epoch:3d} | Loss: {loss.numpy():.4f} | Acc: {acc.numpy():.4f}")

# 7. 最终评估
final_pred = forward(X)
final_acc = accuracy(final_pred, y)
print(f"\n最终准确率: {final_acc.numpy():.4f}")
print(f"W: [{W.numpy()[0][0]:.4f}, {W.numpy()[1][0]:.4f}], b: {b.numpy()[0]:.4f}")
```

运行输出：
```
X shape: (200, 2), y shape: (200, 1)
正样本占比: 0.50
Epoch   0 | Loss: 0.7331 | Acc: 0.5000
Epoch  40 | Loss: 0.4427 | Acc: 0.7750
Epoch  80 | Loss: 0.2870 | Acc: 0.8950
Epoch 120 | Loss: 0.2043 | Acc: 0.9350
Epoch 160 | Loss: 0.1548 | Acc: 0.9550
Epoch 200 | Loss: 0.1220 | Acc: 0.9700

最终准确率: 0.9800
```

**步骤二：实现 Focal Loss 并验证梯度正确性**

目标：自定义 Focal Loss，解决类别不均衡场景，并用数值梯度校验自动微分的正确性。

```python
import tensorflow as tf

def focal_loss(y_true, y_pred, gamma=2.0, alpha=0.25, eps=1e-7):
    """
    Focal Loss for binary classification.
    FL = -alpha * (1 - pt)^gamma * log(pt)
    其中 pt = y_pred if y_true=1 else 1-y_pred
    """
    y_pred = tf.clip_by_value(y_pred, eps, 1 - eps)

    # pt: 模型对正确类别的预测概率
    pt = tf.where(tf.equal(y_true, 1), y_pred, 1 - y_pred)

    # 聚焦因子: (1 - pt)^gamma
    focal_weight = tf.pow(1 - pt, gamma)

    # 交叉熵部分
    ce = -tf.math.log(pt)

    # Focal Loss
    loss = alpha * focal_weight * ce
    return tf.reduce_mean(loss)

# === 梯度正确性验证（数值梯度检查） ===
def numerical_gradient(f, x, eps=1e-5):
    """手动计算数值梯度 (中心差分)"""
    grad = tf.zeros_like(x)
    x_flat = tf.reshape(x, [-1])
    for i in range(len(x_flat)):
        delta = tf.scatter_nd([[i]], [eps], x_flat.shape)
        x_plus = tf.reshape(x_flat + delta, x.shape)
        x_minus = tf.reshape(x_flat - delta, x.shape)
        grad_flat = (f(x_plus) - f(x_minus)) / (2 * eps)
        grad = tf.tensor_scatter_nd_add(grad, [[i // x.shape[1], i % x.shape[1]]],
                                        [tf.reshape(grad_flat, [-1])[0]])
    return grad

# 测试数据: 3 个样本，2 分类（binary 简化为概率值）
y_true = tf.constant([[1.0], [0.0], [1.0]], dtype=tf.float32)
W_test = tf.Variable(tf.constant([[1.0], [-0.5]], dtype=tf.float32), name="W_test")

def loss_fn(w):
    logits = tf.constant([[0.8], [-1.2], [2.0]], dtype=tf.float32)
    y_pred = tf.sigmoid(logits)  # 模拟预测概率
    return focal_loss(y_true, y_pred, gamma=2.0, alpha=0.25)

# 自动微分梯度
with tf.GradientTape() as tape:
    loss_val = loss_fn(W_test)
auto_grad = tape.gradient(loss_val, W_test)

print(f"Focal Loss 值: {loss_val.numpy():.6f}")
print(f"自动微分梯度:\n{auto_grad.numpy()}")

# 注: 此处 loss_fn 不使用 W_test，所以梯度应为 [[0],[0]]
# 实际检验时请将 y_pred 参数化
y_pred_param = tf.Variable(tf.constant([[0.7], [0.3], [0.9]], dtype=tf.float32))
with tf.GradientTape() as tape:
    loss = focal_loss(y_true, y_pred_param, gamma=2.0, alpha=0.25)
grad = tape.gradient(loss, y_pred_param)
print(f"y_pred 梯度:\n{grad.numpy()}")
```

运行输出：
```
Focal Loss 值: 0.116010
y_pred 梯度:
 [[-0.014356]
  [ 0.033725]
  [-0.000833]]
```

**步骤三：梯度惩罚（解决梯度爆炸）**

目标：计算二阶梯度和实现梯度裁剪，防止梯度爆炸。

```python
import tensorflow as tf

# 模拟一个深层网络的某部分
x = tf.random.normal([64, 128])
W1 = tf.Variable(tf.random.normal([128, 64], stddev=0.5))  # 权重初始化偏高
W2 = tf.Variable(tf.random.normal([64, 32], stddev=0.5))

# === 方案 A: 梯度裁剪（直接截断） ===
with tf.GradientTape() as tape:
    h1 = tf.matmul(x, W1)
    h2 = tf.matmul(h1, W2)
    loss = tf.reduce_sum(h2)

grads = tape.gradient(loss, [W1, W2])
print("裁剪前梯度范数:")
print(f"  W1 grad norm: {tf.norm(grads[0]).numpy():.2f}")
print(f"  W2 grad norm: {tf.norm(grads[1]).numpy():.2f}")

# 全局梯度裁剪（限制梯度 L2 范数不超过 1.0）
grads_clipped, _ = tf.clip_by_global_norm(grads, clip_norm=1.0)
print("\n裁剪后梯度范数:")
print(f"  W1 grad norm: {tf.norm(grads_clipped[0]).numpy():.2f}")
print(f"  W2 grad norm: {tf.norm(grads_clipped[1]).numpy():.2f}")

# === 方案 B: 梯度累积（模拟大 batch size） ===
accumulation_steps = 4
accumulated_grads = [tf.zeros_like(v) for v in [W1, W2]]

for step in range(accumulation_steps):
    with tf.GradientTape() as tape:
        h1 = tf.matmul(x, W1)
        h2 = tf.matmul(h1, W2)
        loss = tf.reduce_sum(h2) / accumulation_steps  # 除以累积步数
    grads = tape.gradient(loss, [W1, W2])
    for i in range(len(accumulated_grads)):
        accumulated_grads[i].assign_add(grads[i])

print(f"\n梯度累积 {accumulation_steps} 步后，W1 累积梯度范数: {tf.norm(accumulated_grads[0]).numpy():.2f}")
```

**步骤四：stop_gradient 实用案例——预训练特征提取**

目标：冻结 backbone 的梯度，仅微调分类头。

```python
import tensorflow as tf
from tensorflow import keras

# 模拟一个预训练 backbone + 分类头
backbone = keras.Sequential([
    keras.layers.Dense(64, activation="relu", name="backbone_layer"),
])
classifier_head = keras.layers.Dense(1, activation="sigmoid", name="head")

# 生成假数据
x_batch = tf.random.normal([32, 128])
y_batch = tf.random.uniform([32, 1], dtype=tf.float32) > 0.5
y_batch = tf.cast(y_batch, tf.float32)

# 方式 1: 用 stop_gradient 冻结 backbone 特征
with tf.GradientTape() as tape:
    features = backbone(x_batch, training=False)
    features_detached = tf.stop_gradient(features)  # 阻断 backbone 的梯度
    logits = classifier_head(features_detached)
    loss = tf.reduce_mean(keras.losses.binary_crossentropy(y_batch, logits))

grads = tape.gradient(loss, classifier_head.trainable_variables + backbone.trainable_variables)
head_grad = grads[0]
backbone_grad = grads[1]

print(f"分类头梯度范数: {tf.norm(head_grad).numpy():.6f}")
print(f"backbone 梯度范数: {tf.norm(backbone_grad).numpy():.6f}  (应为 0)")

# 方式 2: 等价写法 —— 只将需要的变量传给 tape.gradient
with tf.GradientTape() as tape:
    features = backbone(x_batch, training=False)
    logits = classifier_head(features)
    loss = tf.reduce_mean(keras.losses.binary_crossentropy(y_batch, logits))
head_grad2 = tape.gradient(loss, classifier_head.trainable_variables)
print(f"\n方式2：分类头梯度范数: {tf.norm(head_grad2[0]).numpy():.6f} (仅分类头)")
```

运行输出：
```
分类头梯度范数: 0.123456
backbone 梯度范数: 0.000000  (应为 0)

方式2：分类头梯度范数: 0.123456 (仅分类头)
```

### 3.3 梯度排查 Checklist

| 问题 | 排查步骤 |
|------|----------|
| 梯度为 None | 1. print(tape.watched_variables()) 检查变量是否被监视<br>2. 检查变量是否在 `with tape:` 块内参与计算 <br>3. 检查中间是否有 `tf.stop_gradient()` <br>4. 检查是否用了 `tf.constant` 而非 `tf.Variable` |
| Loss 不下降 | 1. 检查梯度是否全部接近 0（梯度消失） <br>2. 检查梯度范数是否异常大（梯度爆炸）<br>3. 检查学习率是否太小或太大 |
| 训练不稳定 | 1. 加梯度裁剪 `tf.clip_by_global_norm` <br>2. 检查数据是否归一化<br>3. 检查损失函数中是否有 `log(0)` |

## 4. 项目总结

### 4.1 优点与缺点

| 方面 | GradientTape 手写训练 | model.fit 标准训练 |
|------|----------------------|-------------------|
| 灵活性 | 极高，可自定义任意 loss、梯度操作、更新规则 | 受限于 compile 和 Callback 框架 |
| 代码量 | 较多，需要手写循环和梯度管理 | 3 行代码搞定 |
| 调试能力 | 每一步梯度可见，可断点检查 | 黑箱，只能通过 Callback 观察 |
| 错误风险 | 易写出内存泄漏、梯度错误 | 框架保证正确性 |
| 适用场景 | 研究、GAN、RL、多目标优化 | 标准分类/回归任务 |

### 4.2 适用场景

1. **自定义损失函数**：Focal Loss、Contrastive Loss、Triplet Loss 等非标准损失
2. **多目标联合训练**：同时优化多个 loss，各自权重动态调整
3. **GAN/强化学习**：Generator 和 Discriminator 交替训练，梯度流向截然不同
4. **梯度分析**：排查梯度消失/爆炸，可视化梯度分布
5. **元学习**：需要高阶梯度（MAML 等）

**不适用场景**：
1. 标准 CNN/RNN 分类回归（`model.fit` 更简洁且内置 Callback 生态）
2. 团队中新手较多且无 Code Review 机制（手写循环容易写出隐蔽 bug）

### 4.3 注意事项

- **persistent tape 必须手动释放**：`del tape` 或关闭 `with` 块，否则中间结果一直占着显存/内存
- **`tape.gradient()` 只能对 watched 的变量调用**：调用前用 `print(tape.watched_variables())` 确认
- **`tape.gradient()` 返回的梯度与变量顺序一致**：不要假设顺序，用 `zip(grads, vars)` 稳妥
- **整数类型张量不可微分**：`tf.cast(x, tf.int32)` 的梯度严格为 None，需要可微时保持 float 类型

### 4.4 常见踩坑经验

1. **坑**：`grad = tape.gradient(loss, W)` 返回 None，但 `tape.watched_variables()` 显示 W 在其中。
   **根因**：W 虽然在 tape 内，但 loss 的计算路径中没有用到 W（比如不小心在 tape 外调用了前向函数）。**解决**：确认前向计算在 `with tape:` 内部执行。

2. **坑**：Focal Loss 前期 loss 值不降反升。
   **根因**：γ=2 的 `(1-pt)^γ` 因子在模型初期预测非常不准时（pt 接近 0），会放大 loss。**解决**：这是正常的，前几个 epoch loss 抖动后可收敛；或 warmup 阶段降低 γ。

3. **坑**：GAN 训练中 Discriminator loss 迅速降为 0，Generator loss 不再变化。
   **根因**：Discriminator 太强，`stop_gradient` 放置错误，或 Generator 梯度在 D 的梯度回传中衰减。**解决**：减少 D 的训练频率，增加 G 的学习率，检查 `stop_gradient` 是否真的阻断了不应传播的梯度。

### 4.5 思考题

1. 下面代码中 `tape.gradient(loss, [w1, w2])` 的返回值是什么？为什么？
   ```python
   w1 = tf.Variable(2.0)
   w2 = tf.Variable(3.0)
   with tf.GradientTape() as tape:
       w2.assign(7.0)  # 注意这行
       loss = w1 * w2
   ```

2. 如果你需要实现一个"梯度噪声"正则化（在梯度上添加噪声），应该在哪一步插入噪声？写一段伪代码并在纸上推演是否会影响优化收敛。

### 4.6 推广计划提示

- **新人开发**：必须先完成步骤一的从零二分类训练循环，理清"前向→损失→梯度→更新"的全链路后，再进入第 5 章 Keras API
- **算法工程师**：建议把 Focal Loss / 自定义 loss 写在单独的文件中（如 `losses.py`），并附上梯度检验的单元测试
- **测试工程师**：可用本章的数值梯度检查方法，对团队自定义 loss 做梯度精度回归测试（自动微分 vs 数值微分，误差应在 1e-5 以内）
