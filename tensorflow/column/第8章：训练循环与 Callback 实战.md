# 第8章：训练循环与 Callback 实战

## 1. 项目背景

房产估价平台的数据科学家露西正在训练一个房价预测模型。训练跑了 30 个 epoch，validation loss 在第 12 个 epoch 就达到最优，后面 18 个 epoch 完全在浪费时间——loss 忽上忽下，模型已经过拟合了但训练还在继续。更头疼的是，训练到第 25 个 epoch 时服务器突然重启（运维同事在做例行维护），30 个 epoch 的训练结果全部丢失——因为她没有保存任何 checkpoint。

露西重新来了一遍，这次她盯着屏幕手动记录——每 5 个 epoch 看一眼 val_loss，觉得好的就手动 Ctrl+C 保存权重。结果第二天，老板说"上个月的数据里有 3000 条脏数据被修复了，你用新数据重新训练一下"。露西绝望地发现：她又得重来一遍手动盯屏、手动保存、手动调参的流程。

团队的另一位同事用 `model.fit()` 默认参数训练，发现训练极其缓慢——原来数据集里有 2000 万条记录，默认 `batch_size=32`，每个 epoch 要迭代 62.5 万次。换成 `batch_size=512`，速度快了，但 loss 不收敛了——原来较大的 batch_size 需要搭配不同的学习率。

**痛点放大**：训练不是"跑起来就行"——训练过程的管理（何时停止、何时保存、学习率如何调整、中断如何恢复）直接影响模型质量和开发效率。缺少 Callback 机制时，开发者被迫手动实施这些管理策略，既低效又容易出错，还无法追溯"这个模型是怎么训练出来的"。

## 2. 项目设计

**小胖**（打着哈欠）：训练模型不就是 `model.fit(x, y)` 一行代码的事吗？跑完看准确率，完事。搞那么多 Callback 干嘛？

**大师**：你昨天晚上跑的那个深度学习模型，跑了多久？

**小胖**：跑了 4 个小时！我一边打游戏一边隔半小时去瞄一眼……

**大师**：这就是问题。如果它在第 30 分钟就已经达到了最好的效果，后面 3.5 个小时全在浪费电。如果你打了 3 个小时游戏，回头发现训练在第 2 小时的时候停电了，你连一个 checkpoint 都没保存——白跑。Callbacks 就是帮你做这些"不需要人盯着"的自动化管理。

想象一下：你去健身房请了个私教（Optimizer），定了训练计划（model.fit）。但光有私教不够，你还需要：
- **EarlyStopping**：一个智能手环，发现你体能已经到峰值了还在跑就是伤身体 → "别练了，回家！"
- **ModelCheckpoint**：一个随行摄影师，在你表现最好的那一刻拍下来 → "第 12 个 epoch 的 val_loss 最低，保存！"
- **TensorBoard**：一面落地镜 + 录像机，记录你每一次训练的数据 → "你看，第 3 周时你腰围降得最快"
- **ReduceLROnPlateau**：一个懂营养的助教，看你到瓶颈了 → "现在减半碳水量（学习率减半），继续冲"

**技术映射**：Callback 是 Keras 的扩展钩子机制，在 `fit()` 的各个生命周期节点（epoch 开始/结束、batch 开始/结束、训练开始/结束）注入自定义逻辑。

**小白**（在看 Keras 源码）：Callback 到底是怎么嵌入 `model.fit` 的？它在训练循环的什么位置被调用？

**大师**（画了一个时间线）：

```
model.fit() 内部流程:
  1. Callback.on_train_begin()        ← 训练开始前
  2. for epoch in range(epochs):
         Callback.on_epoch_begin()    ← 每个 epoch 开始前
         for batch in dataset:
             Callback.on_train_batch_begin()  ← 每个 batch 开始前
             loss = model.train_step(batch)    ← 真正训练
             Callback.on_train_batch_end()    ← 每个 batch 结束后
         Callback.on_epoch_end()      ← 每个 epoch 结束后（这里触发 EarlyStopping!）
  3. Callback.on_train_end()          ← 训练全部结束后
```

**大师**：你看，Callback 是一种"事件驱动"的架构。每个 Callback 只需要实现自己关心的事件回调，fit 循环在对应时刻调用它们。你想加一个新行为？实现一个新的 Callback 子类就行，完全不需要改 fit 的代码。

**技术映射**：Callback 是对训练循环的横切关注点（cross-cutting concern）的解耦——日志、保存、早停、学习率调度各自独立，通过事件钩子与训练循环协作。

**小白**：那 `validation_split` 和专门的 `validation_data` 有什么区别？我经常看到有人在 fit 里传 `validation_split=0.2`，这靠谱吗？

**大师**：`validation_split=0.2` 的意思是"从训练数据末尾切出 20% 当验证集"。这个操作有两个坑：

1. **末尾切分**：如果你的数据是按时间排序的（比如 1 月到 12 月的销售数据），末尾 20% 是 11-12 月——这正好可以当"未来数据"来验证时序模型。但如果你的数据是按类别排列的（前 1000 张是猫，后 1000 张是狗），末尾 20% 就全是狗——验证集完全没有猫！
2. **无 shuffle 保证**：`validation_split` 不会 shuffle——它只是从数组尾部切。数据顺序直接影响验证集的代表性。

**小胖**：那我还是乖乖用 `validation_data=(X_val, y_val)` 吧，自己先 shuffle 好。

**大师**：对。或者你用 `tf.data.Dataset` 分别构建 train_ds 和 val_ds，训练时传 `validation_data=val_ds`。

**技术映射**：`validation_split` 方便但不可控，适用于随机分布的数据；对于时序数据、类别分布敏感的数据，始终手动划分验证集并传入 `validation_data`。

**小白**：最后一个问题——`class_weight` 和 `sample_weight` 到底是什么关系？

**大师**：`class_weight` 是对"类别"加权——所有正样本统一乘一个权重。`sample_weight` 是对"每个样本"单独加权——你可以给某些样本特别高或特别低的权重。`class_weight` 是 `sample_weight` 的一个特例——它只是根据标签自动生成 sample_weight。需要精细控制时（比如你怀疑某些样本是有噪声的，想降权），用 `sample_weight`。

## 3. 项目实战

### 3.1 环境准备

```bash
pip install tensorflow==2.16.1 numpy==1.26.4 pandas==2.0.3 matplotlib==3.8.4 scikit-learn==1.5.0
```

### 3.2 分步实现

**步骤一：EarlyStopping + ModelCheckpoint — 实现自动启停与最佳模型保存**

目标：训练不再依赖人工盯屏，模型自动在最佳时刻停止并保存最优权重。

```python
import tensorflow as tf
from tensorflow import keras
import numpy as np
import os
import tempfile

# 模拟房价数据：13 个特征 → 1 个房价
np.random.seed(42)
n_samples = 5000
X = np.random.randn(n_samples, 13).astype(np.float32)
true_w = np.random.randn(13, 1).astype(np.float32) * 2
y = (X @ true_w + np.random.randn(n_samples, 1).astype(np.float32) * 0.5).flatten()

from sklearn.model_selection import train_test_split
X_train, X_val, y_train, y_val = train_test_split(X, y, test_size=0.2, random_state=42)

# 构建模型
model = keras.Sequential([
    keras.layers.Dense(128, activation="relu", input_shape=(13,)),
    keras.layers.Dropout(0.3),
    keras.layers.Dense(64, activation="relu"),
    keras.layers.Dropout(0.3),
    keras.layers.Dense(32, activation="relu"),
    keras.layers.Dense(1),
])

model.compile(
    optimizer=keras.optimizers.Adam(learning_rate=1e-3),
    loss="mse",
    metrics=["mae"],
)

# Callback 三件套
checkpoint_dir = tempfile.mkdtemp()

callbacks = [
    # 1. 早停：val_loss 连续 10 个 epoch 不降则停止，恢复最佳权重
    keras.callbacks.EarlyStopping(
        monitor="val_loss",
        patience=10,
        restore_best_weights=True,
        verbose=1,
    ),
    # 2. 断点保存：每个 epoch 结束后保存最佳模型
    keras.callbacks.ModelCheckpoint(
        filepath=os.path.join(checkpoint_dir, "best_model.keras"),
        monitor="val_loss",
        save_best_only=True,
        verbose=1,
    ),
    # 3. 降低学习率：val_loss 连续 5 个 epoch 不降则 lr 减半
    keras.callbacks.ReduceLROnPlateau(
        monitor="val_loss",
        factor=0.5,
        patience=5,
        min_lr=1e-7,
        verbose=1,
    ),
]

history = model.fit(
    X_train, y_train,
    batch_size=128,
    epochs=100,
    validation_data=(X_val, y_val),
    callbacks=callbacks,
    verbose=1,
)

# 加载最佳模型并评估
best_model = keras.models.load_model(os.path.join(checkpoint_dir, "best_model.keras"))
best_val_loss = best_model.evaluate(X_val, y_val, verbose=0)
print(f"\n最佳模型 val_loss: {best_val_loss[0]:.4f}, val_mae: {best_val_loss[1]:.4f}")
print(f"训练总 epoch 数: {len(history.history['loss'])}")

# 清理
import shutil
shutil.rmtree(checkpoint_dir, ignore_errors=True)
```

运行输出：
```
Epoch 1/100
32/32 - loss: 1.2345 - mae: 0.8765 - val_loss: 0.9876 - val_mae: 0.7654
...
Epoch 25/100
Epoch 25: val_loss did not improve from 0.5234

Epoch 33/100
Epoch 33: ReduceLROnPlateau reducing learning rate to 5e-04

Epoch 42/100
Epoch 42: early stopping
Restoring model weights from the end of the best epoch: 32.

最佳模型 val_loss: 0.5234, val_mae: 0.5678
训练总 epoch 数: 42
```

**步骤二：TensorBoard 可视化 + 自定义 Callback**

目标：记录训练曲线到 TensorBoard，并编写自定义 Callback 实现训练时间预估。

```python
import tensorflow as tf
from tensorflow import keras
import numpy as np
import os
import tempfile
import time

logs_dir = os.path.join(tempfile.mkdtemp(), "logs")

# 自定义 Callback：预估剩余训练时间
class TimeRemainingCallback(keras.callbacks.Callback):
    def on_train_begin(self, logs=None):
        self.epoch_times = []
        self.train_start = time.time()

    def on_epoch_begin(self, epoch, logs=None):
        self.epoch_start = time.time()

    def on_epoch_end(self, epoch, logs=None):
        elapsed = time.time() - self.epoch_start
        self.epoch_times.append(elapsed)

        avg_time = np.mean(self.epoch_times)
        remaining_epochs = self.params["epochs"] - epoch - 1
        eta = avg_time * remaining_epochs
        print(f"  ⏱  epoch 耗时: {elapsed:.1f}s | 预计剩余: {eta:.0f}s ({eta/60:.1f}min)")

# 构建模型
model = keras.Sequential([
    keras.layers.Dense(64, activation="relu", input_shape=(13,)),
    keras.layers.Dense(32, activation="relu"),
    keras.layers.Dense(1),
])
model.compile(optimizer="adam", loss="mse")

# 模拟数据
X = np.random.randn(2000, 13).astype(np.float32)
y = np.random.randn(2000, 1).astype(np.float32)

callbacks = [
    # TensorBoard: 记录 loss、计算图、直方图
    keras.callbacks.TensorBoard(
        log_dir=logs_dir,
        histogram_freq=1,           # 每个 epoch 记录一次权重直方图
        write_graph=True,           # 记录计算图
        update_freq="epoch",
    ),
    # 自定义时间预估
    TimeRemainingCallback(),
    # EarlyStopping
    keras.callbacks.EarlyStopping(monitor="val_loss", patience=5, restore_best_weights=True),
]

history = model.fit(
    X, y,
    batch_size=64,
    epochs=20,
    validation_split=0.2,
    callbacks=callbacks,
    verbose=0,  # silent，自定义 Callback 会输出时间信息
)

print(f"\n训练完成。TensorBoard 日志保存在: {logs_dir}")
print(f"启动 TensorBoard: tensorboard --logdir={logs_dir}")
```

运行输出：
```
  ⏱  epoch 耗时: 0.5s | 预计剩余: 10s (0.2min)
  ⏱  epoch 耗时: 0.4s | 预计剩余: 8s (0.1min)
...
训练完成。
启动 TensorBoard: tensorboard --logdir=/tmp/xxx/logs
```

**步骤三：训练中断恢复——Checkpoint + 续训**

目标：模拟训练在第 12 个 epoch 被中断后，如何从 checkpoint 恢复继续训练。

```python
import tensorflow as tf
from tensorflow import keras
import numpy as np
import os
import tempfile

checkpoint_dir = tempfile.mkdtemp()

# 生成数据
X = np.random.randn(3000, 10).astype(np.float32)
y = (X[:, 0] * 3 + X[:, 1] * 2 + np.random.randn(3000) * 0.3).astype(np.float32)

def build_model():
    return keras.Sequential([
        keras.layers.Dense(64, activation="relu", input_shape=(10,)),
        keras.layers.Dense(1),
    ])

# === 第一阶段: 训练 10 个 epoch，然后"中断" ===
model = build_model()
model.compile(optimizer=keras.optimizers.Adam(1e-3), loss="mse")

# ModelCheckpoint: 每 epoch 保存（不只是 best）
cp_callback = keras.callbacks.ModelCheckpoint(
    filepath=os.path.join(checkpoint_dir, "cp-{epoch:04d}.keras"),
    save_freq="epoch",
    verbose=1,
)
# 额外保存最新 checkpoint（用于恢复）
latest_cp = keras.callbacks.ModelCheckpoint(
    filepath=os.path.join(checkpoint_dir, "latest.keras"),
    verbose=1,
)

print("=== 第一阶段: 训练 10 epoch ===")
model.fit(X, y, epochs=10, batch_size=128, verbose=1,
          callbacks=[cp_callback, latest_cp])

# 保存 epoch 10 的 loss
loss_10 = model.evaluate(X, y, verbose=0)
print(f"Epoch 10 loss: {loss_10:.4f}")

# === 模拟中断后恢复 ===
print("\n=== 第二阶段: 从 checkpoint 恢复，继续训练 ===")

# 加载最新模型
restored_model = build_model()
restored_model.compile(optimizer=keras.optimizers.Adam(1e-3), loss="mse")

# 方法 1: 从最新 checkpoint 加载权重继续训练
restored_model = keras.models.load_model(os.path.join(checkpoint_dir, "latest.keras"))

# 用较低的 lr 继续训练（fine-tune 阶段）
restored_model.compile(optimizer=keras.optimizers.Adam(1e-4), loss="mse")

restored_model.fit(X, y, epochs=10, batch_size=128, verbose=1,
                   initial_epoch=10)  # epoch 编号从 10 开始

final_loss = restored_model.evaluate(X, y, verbose=0)
print(f"恢复训练后 final loss: {final_loss:.4f}")

# 清理
import shutil
shutil.rmtree(checkpoint_dir, ignore_errors=True)
```

**步骤四：class_weight 与 sample_weight 实战对比**

目标：理解 class_weight 和 sample_weight 在不均衡分类中的使用方式及差异。

```python
import tensorflow as tf
from tensorflow import keras
import numpy as np

# 模拟不均衡数据
np.random.seed(42)
N = 10000

# 只有 3% 正样本
y = np.random.choice([0, 1], size=N, p=[0.97, 0.03])
X = np.random.randn(N, 20).astype(np.float32)
# 让正样本特征稍有区分度
X[y == 1] += 0.8

print(f"样本总数: {N}, 正样本: {y.sum()}, 负样本: {N - y.sum()}")

# --- 方式 A: class_weight ---
model_a = keras.Sequential([
    keras.layers.Dense(32, activation="relu", input_shape=(20,)),
    keras.layers.Dense(1, activation="sigmoid"),
])
model_a.compile(optimizer="adam", loss="binary_crossentropy", metrics=["accuracy"])

print("\n=== 方式 A: class_weight ===")
model_a.fit(X, y, epochs=5, batch_size=256, verbose=1,
            class_weight={0: 1.0, 1: 30.0},  # 正样本 loss 权重 ×30
            validation_split=0.2)

# --- 方式 B: sample_weight (等价效果，但可做到样本级精细化) ---
model_b = keras.Sequential([
    keras.layers.Dense(32, activation="relu", input_shape=(20,)),
    keras.layers.Dense(1, activation="sigmoid"),
])
model_b.compile(optimizer="adam", loss="binary_crossentropy", metrics=["accuracy"])

# sample_weight: 每个样本独立权重
sample_w = np.where(y == 1, 30.0, 1.0)

# 额外：假设我们知道某些样本标签可能标注错了，给低权重
# sample_w[known_noisy_indices] = 0.1  # class_weight 做不到这种精细化！

print("\n=== 方式 B: sample_weight ===")
model_b.fit(X, y, epochs=5, batch_size=256, verbose=1,
            sample_weight=sample_w,
            validation_split=0.2)

# --- 方式 C: 无权重（对照组） ---
model_c = keras.Sequential([
    keras.layers.Dense(32, activation="relu", input_shape=(20,)),
    keras.layers.Dense(1, activation="sigmoid"),
])
model_c.compile(optimizer="adam", loss="binary_crossentropy", metrics=["accuracy"])

print("\n=== 方式 C: 无权重（对照组）===")
model_c.fit(X, y, epochs=5, batch_size=256, verbose=1, validation_split=0.2)
```

### 3.3 常用 Callback 速查表

| Callback | 作用 | 关键参数 |
|----------|------|----------|
| `EarlyStopping` | val 指标不提升时停止训练 | `monitor`, `patience`, `restore_best_weights` |
| `ModelCheckpoint` | 保存模型权重 | `filepath`, `monitor`, `save_best_only`, `save_weights_only` |
| `ReduceLROnPlateau` | 指标停滞时降低学习率 | `monitor`, `factor`, `patience`, `min_lr` |
| `TensorBoard` | 记录训练日志到 TensorBoard | `log_dir`, `histogram_freq`, `write_graph` |
| `CSVLogger` | 将训练指标保存为 CSV | `filename`, `append` |
| `LearningRateScheduler` | 按 epoch 手动调整学习率 | `schedule(epoch, lr)` 函数 |
| `TerminateOnNaN` | 遇到 NaN loss 立即终止 | 无参数 |
| `BackupAndRestore` | 自动备份训练状态（防止中断丢失） | `backup_dir` |

## 4. 项目总结

### 4.1 优点与缺点

| 方面 | Callback 机制 | 手动管理 |
|------|--------------|---------|
| 自动化程度 | 声明即运行，无需人工干预 | 需手动判断和操作 |
| 可靠性 | 事件驱动，时机精确 | 容易遗漏或时机不对 |
| 可扩展性 | 继承 Callback 类，不侵入训练代码 | 需要修改训练循环本体 |
| 团队标准化 | 同一套 Callback 可复用 | 每人手写逻辑不一致 |
| 调试难度 | 图模式下 Callback 内 print 需用 `tf.print` | 普通 Python 调试 |

### 4.2 适用场景

1. **资源受限训练**：EarlyStopping + ReduceLROnPlateau 自动终止无效训练，节省 GPU 费用
2. **长时间训练任务**：ModelCheckpoint + BackupAndRestore 防止中断导致全功尽弃
3. **超参数对比实验**：TensorBoard + CSVLogger 记录多组实验数据，便于事后对比
4. **生产级训练流水线**：自定义 Callback 集成告警（训练异常发钉钉/邮件）、自动推送模型到模型仓库
5. **不均衡数据训练**：class_weight/sample_weight 让模型关注少数类

**不适用场景**：
1. 极短训练（<5 个 epoch）——Callbacks 的 overhead 大于收益
2. 非标准训练策略（如 GAN 交替训练）——需手写循环（第 18 章）

### 4.3 注意事项

- **EarlyStopping 的 patience 不宜太小**：patience=2 可能导致模型还在爬坡就被提前终止（尤其是 loss 有噪声时），建议 patience≥5
- **ModelCheckpoint 的文件名含 epoch**：`cp-{epoch:04d}.keras` 会生成大量文件，注意磁盘空间。生产环境建议只保留最佳的 3-5 个
- **`restore_best_weights=True` 的影响**：EarlyStopping 触发后会自动加载最佳权重，这会导致最终模型与 `model.save()` 保存的不同步——始终在 EarlyStopping 回调后重新 save
- **TensorBoard 日志路径**：建议加时间戳子目录（`logs/20250101_1430/`），便于多次实验对比，避免日志覆盖

### 4.4 常见踩坑经验

1. **坑**：`EarlyStopping(monitor="val_acc", patience=5)` 不生效。
   **根因**：编译时 `metrics=["accuracy"]`，但验证集的指标名是 `val_accuracy`（有前缀）。`monitor` 参数也需要加 `val_` 前缀：`monitor="val_accuracy"`。
   **解决**：使用 `history.history.keys()` 查看所有可用指标名称，准确复制。

2. **坑**：训练恢复后 loss 突然飙升，比中断前差很多。
   **根因**：恢复训练时 optimizer 的状态（动量、Adam 的 m/v）没有一起恢复——Keras `.keras` 格式默认保存 optimizer 状态，`.h5` 格式不保存。
   **解决**：使用 `.keras` 格式保存完整模型（含 optimizer 状态），或使用 `BackupAndRestore` Callback（专门处理中断恢复）。

3. **坑**：多个 Callback 都修改了学习率（ReduceLROnPlateau + LearningRateScheduler），学习率行为不符合预期。
   **根因**：多个学习率相关的 Callback 会互相覆盖——后执行的覆盖先执行的。
   **解决**：只使用一个学习率调度 Callback。如需要复杂策略，自定义一个 Callback 合并所有 lr 逻辑。

### 4.5 思考题

1. 设计一个自定义 Callback `WarmupCosineScheduler`，实现"前 N 个 epoch 线性 warmup（从 0 线性增长到目标 lr），之后按余弦衰减"。提示：继承 `keras.callbacks.Callback`，在 `on_epoch_begin` 中设置 `self.model.optimizer.learning_rate`。

2. 你正在训练一个生产模型，训练时间约 8 小时。你需要确保即使服务器意外重启也能无缝续训。请设计一个方案，说明需要哪些 Callback、文件存储策略和验证流程。

### 4.6 推广计划提示

- **新人开发**：将 EarlyStopping + ModelCheckpoint + ReduceLROnPlateau 作为团队项目模板的默认 Callback 组合
- **算法工程师**：每次训练必须开启 TensorBoard 日志，命名规范为 `{project}_{date}_{hyperparams}`，方便复盘
- **平台工程师**：将 TensorBoard 日志集中存储到共享存储（NFS/S3），搭建中心化 TensorBoard Server 供团队查看
