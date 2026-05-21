# 第5章：Keras Sequential 与函数式 API 入门

## 1. 项目背景

某在线教育平台的用户增长团队发现一个现象：试用期用户在试用课程结束后的第 3-7 天流失率突然飙升到 40%。运营负责人找到算法团队："能不能根据用户试用期间的行为数据，提前预测哪些用户会流失？我们想在用户流失前主动发优惠券挽留。"

算法工程师小琳拿到了一份脱敏数据，包含 50000 名试用用户的信息：注册时长（天）、观看课程数、完成作业数、社区发帖数、最近 7 天登录天数、是否绑定手机号、使用的设备类型（iOS/Android/PC）等 15 个特征。标签是"是否在 14 天内流失"（0=留存，1=流失）。

小琳的目标很明确：快速搭建一个二分类模型，输出每个用户的流失概率，以便运营团队筛选高风险用户。她面临的选择是——用 Keras Sequential API 还是 Functional API？

她在同事的代码里看到两种写法：有人用 `model = Sequential([Dense(...), ...])` 几行搞定；有人用 `inputs = Input(...); x = Dense(...)(inputs); model = Model(inputs, outputs)` 这种看起来更啰嗦的方式。她不清楚两种写法的边界，也不清楚什么时候该用哪种。

**痛点放大**：
- Sequential API 够简单，但如果需求变成"不仅要预测流失概率，还要预测用户可能对哪个课程感兴趣"（多任务），就写不了
- Functional API 灵活但容易写出"魔法线团"——层之间的连接方向搞混
- 两种 API 在 compile/fit/evaluate 阶段是一样的，但模型结构不可互换
- 如果一开始选了 Sequential，后面需要多输入/多输出时就得全部重写

## 2. 项目设计

**小胖**（趴在桌上刷手机）：诶，这个 Sequential 是不是就是"一个接一个排排队"？像我打饭：先拿盘子 → 盛饭 → 打菜 → 浇汁 → 端走。一层一层往下走，没法跳步，也没法回头？

**大师**（竖起大拇指）：今天小胖的比喻非常到位！Sequential 就是你描述的"直线流水线"——数据从第一个层进去，从左往右经过每一层，最后从最后一层出来。中间不能分叉、不能跳过、不能并联。就像一条只有一个入口和一个出口的工厂流水线。

```python
# Sequential: 单线流水线
model = keras.Sequential([
    keras.layers.Dense(64, activation="relu", input_shape=(15,)),  # 入口
    keras.layers.Dropout(0.3),
    keras.layers.Dense(32, activation="relu"),
    keras.layers.Dense(1, activation="sigmoid"),                  # 出口
])
```

**技术映射**：Sequential API 适用于层的线性堆叠（单一输入 → 单一输出，无分支/跳跃连接），是 Keras 最简洁的建模方式，覆盖 80% 的日常建模需求。

**小白**：那 Functional API 又是什么？听起来像"函数式编程"？

**大师**：Functional API 不是编程范式那个"函数式"，而是"把每一层当成一个函数来调用"。它的核心思想是：`Layer` 是一个可调用对象，`output = Layer(input)` 就是"把输入传给这一层，拿到输出"。因为输入和输出都是 Tensor，你可以像搭乐高一样——一个基座分出两个分支，两个分支后面又合并。

```python
# Functional API: 灵活拼接
inputs = keras.Input(shape=(15,), name="features")
x = keras.layers.Dense(64, activation="relu")(inputs)
x = keras.layers.Dropout(0.3)(x)
x = keras.layers.Dense(32, activation="relu")(x)
outputs = keras.layers.Dense(1, activation="sigmoid", name="churn_prob")(x)
model = keras.Model(inputs=inputs, outputs=outputs)
```

**大师**：表面上看跟 Sequential 差不多对吧？但真正的威力在这儿：

```python
# Functional API: 多输入 + 共享层
user_input = keras.Input(shape=(10,), name="user_features")
course_input = keras.Input(shape=(8,), name="course_features")

shared_dense = keras.layers.Dense(32, activation="relu")
user_vec = shared_dense(user_input)    # 同样的层处理用户特征
course_vec = shared_dense(course_input)  # 同样的层处理课程特征

merged = keras.layers.concatenate([user_vec, course_vec])
churn_output = keras.layers.Dense(1, activation="sigmoid", name="churn")(merged)
category_output = keras.layers.Dense(5, activation="softmax", name="category")(merged)

model = keras.Model(inputs=[user_input, course_input], outputs=[churn_output, category_output])
```

**小胖**（张大嘴巴）：等一下！用户特征和课程特征进了同一个 `Dense` 层？这不乱套了？

**大师**：好问题！这叫"共享权重"。你想想，用户对某个课程的兴趣和课程本身对用户的吸引力，本质上是不是可以共用一套评价体系？比如"趣味性""难度""互动性"——不管是评价一个用户还是评价一门课，这三个维度都有意义。共享层的意思就是"用同一把尺子量不同的东西"。

**技术映射**：共享层在 Functional API 中通过"同一个 Layer 实例被多次调用"实现。它的权重被所有输入路径共享，适合 Siamese 网络、双塔模型等需要对称特征处理的场景。

**小白**（若有所思）：那 Model Subclassing 又是什么？我在代码里见过 `class MyModel(keras.Model)`，看着更自由。

**大师**：对，Subclassing 是最灵活但也最复杂的方式。Sequential 和 Functional 是"配置式"建模——你声明结构，框架帮你生成前向图。Subclassing 是"命令式"建模——你在 `call()` 方法里手动写 for 循环、if-else、Python 逻辑。

三者的选择原则是：
- **80% 的场景** → Sequential，代码最少，够用
- **需要多输入/多输出/共享层/残差连接** → Functional API
- **需要动态控制流（如循环次数取决于输入）** → Model Subclassing

**小胖**：那 `model.compile` 和 `model.fit` 到底是什么原理？背后发生了什么？

**大师**（站起身在白板上画流程图）：

```
model.compile(optimizer, loss, metrics)  ──→  绑定训练配置
model.fit(x, y, epochs, batch_size)      ──→  执行训练循环

fit() 内部实际做的事情（伪代码）：
for epoch in range(epochs):
    for batch in data:
        with tf.GradientTape() as tape:  ←── 自动创建 tape
            y_pred = model(batch_x)       ←── 前向传播
            loss = loss_fn(y_pred, batch_y)
        grads = tape.gradient(loss, model.trainable_variables)
        optimizer.apply_gradients(zip(grads, model.trainable_variables))
        metrics.update_state(batch_y, y_pred)  ←── 更新指标
    log_metrics(epoch, metrics.result())
    callbacks.on_epoch_end(epoch)         ←── 触发 Callback
```

**大师**：看到了吗？`fit()` 其实就是我们第 4 章手写的那套训练循环，只不过 Keras 帮你自动做了 tape 管理、梯度更新、指标累加、日志打印。这不神奇，只是封装。

**技术映射**：`compile()` 配置优化器和损失函数（声明式）；`fit()` 执行训练循环（包含前向、损失、梯度、更新、指标、Callback 六大环节）；`evaluate()` 和 `predict()` 分别用于评估和推理。

**小白**：那 compile 的时候 `loss` 和 `metrics` 有什么区别？我老是混淆。

**大师**：`loss` 是给优化器看的——优化器说"我要 minimize 这个值"。`metrics` 是给人看的——你说"我要监控这个值好不好"。比如你训练一个分类模型：loss 是交叉熵（数学上可导，能拿来更新权重），metrics 是准确率和 AUC（不可导或者业务理解更直观，只用来展示，不参与梯度计算）。

## 3. 项目实战

### 3.1 环境准备

```bash
pip install tensorflow==2.16.1 numpy==1.26.4 pandas==2.0.3 scikit-learn==1.5.0 matplotlib==3.8.4
```

### 3.2 分步实现

**步骤一：用 Sequential API 实现用户流失预测**

目标：用最简单的方式搭建、训练、评估一个二分类模型。

```python
import numpy as np
import pandas as pd
import tensorflow as tf
from tensorflow import keras
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import StandardScaler

# 1. 生成模拟用户数据
np.random.seed(42)
n_samples = 50000

data = {
    "register_days":       np.random.randint(1, 30, n_samples),
    "courses_watched":     np.random.poisson(3, n_samples),
    "homework_done":       np.random.poisson(1, n_samples),
    "forum_posts":         np.random.poisson(0.5, n_samples),
    "login_days_7d":       np.random.randint(0, 7, n_samples),
    "has_phone":           np.random.randint(0, 2, n_samples),
    "device_ios":          np.random.randint(0, 2, n_samples),
    "device_android":      np.random.randint(0, 2, n_samples),
    "avg_session_min":     np.random.exponential(10, n_samples),
    "courses_browsed":     np.random.poisson(5, n_samples),
    "search_count":        np.random.poisson(2, n_samples),
    "fav_count":           np.random.poisson(1, n_samples),
    "review_count":        np.random.poisson(0.3, n_samples),
    "days_since_last_act": np.random.randint(0, 14, n_samples),
    "referral_count":      np.random.poisson(0.2, n_samples),
}
df = pd.DataFrame(data)

# 构造标签：活跃度越低的用户越容易流失
activity_score = (
    df["courses_watched"] * 3 +
    df["homework_done"] * 5 +
    df["login_days_7d"] * 2 -
    df["days_since_last_act"] * 4 +
    df["forum_posts"] * 3 +
    np.random.randn(n_samples) * 5  # 加噪声
)
threshold = np.percentile(activity_score, 40)   # 约 40% 的用户标记为流失
df["churn"] = (activity_score < threshold).astype(int)

print(f"数据集大小: {df.shape}")
print(f"流失率: {df['churn'].mean():.2%}")

# 2. 数据划分与标准化
y = df.pop("churn").values
X = df.values.astype(np.float32)

X_train, X_test, y_train, y_test = train_test_split(
    X, y, test_size=0.2, random_state=42, stratify=y
)

scaler = StandardScaler()
X_train = scaler.fit_transform(X_train).astype(np.float32)
X_test = scaler.transform(X_test).astype(np.float32)

print(f"X_train: {X_train.shape}, X_test: {X_test.shape}")

# 3. Sequential API 建模
seq_model = keras.Sequential([
    keras.layers.Input(shape=(15,)),
    keras.layers.Dense(128, activation="relu"),
    keras.layers.BatchNormalization(),
    keras.layers.Dropout(0.3),
    keras.layers.Dense(64, activation="relu"),
    keras.layers.BatchNormalization(),
    keras.layers.Dropout(0.3),
    keras.layers.Dense(32, activation="relu"),
    keras.layers.Dense(1, activation="sigmoid"),
], name="sequential_churn_model")

seq_model.summary()

# 4. 编译与训练
seq_model.compile(
    optimizer=keras.optimizers.Adam(learning_rate=1e-3),
    loss="binary_crossentropy",
    metrics=["accuracy", keras.metrics.AUC(name="auc")],
)

history_seq = seq_model.fit(
    X_train, y_train,
    batch_size=256,
    epochs=10,
    validation_split=0.2,
    verbose=1,
)

# 5. 评估
seq_results = seq_model.evaluate(X_test, y_test, verbose=0, return_dict=True)
print(f"\nSequential 模型测试结果:")
for k, v in seq_results.items():
    print(f"  {k}: {v:.4f}")
```

运行输出（示例）：
```
数据集大小: (50000, 16)
流失率: 40.00%
X_train: (40000, 15), X_test: (10000, 15)
Model: "sequential_churn_model"
┌──────────────────────────┬──────────────────────┬───────────────┐
│ Layer (type)             │ Output Shape         │       Param # │
├──────────────────────────┼──────────────────────┼───────────────┤
│ dense (Dense)            │ (None, 128)          │         2,048 │
│ batch_normalization      │ (None, 128)          │           512 │
│ dropout (Dropout)        │ (None, 128)          │             0 │
│ dense_1 (Dense)          │ (None, 64)           │         8,256 │
│ batch_normalization_1    │ (None, 64)           │           256 │
│ dropout_1 (Dropout)      │ (None, 64)           │             0 │
│ dense_2 (Dense)          │ (None, 32)           │         2,080 │
│ dense_3 (Dense)          │ (None, 1)            │            33 │
└──────────────────────────┴──────────────────────┴───────────────┘

Epoch 1/10 - loss: 0.5321 - accuracy: 0.7245 - auc: 0.8012
...
Epoch 10/10 - loss: 0.3924 - accuracy: 0.8234 - auc: 0.9015

Sequential 模型测试结果:
  loss: 0.3918
  accuracy: 0.8231
  auc: 0.9023
```

**步骤二：用 Functional API 实现多任务模型**

目标：同时预测"流失概率"和"用户所属活跃度等级"（3 分类）。

```python
from tensorflow import keras

# Functional API 建模 —— 多输出
inputs = keras.Input(shape=(15,), name="user_features")

x = keras.layers.Dense(128, activation="relu", name="shared_1")(inputs)
x = keras.layers.BatchNormalization(name="bn_1")(x)
x = keras.layers.Dropout(0.3, name="dropout_1")(x)
x = keras.layers.Dense(64, activation="relu", name="shared_2")(x)
x = keras.layers.BatchNormalization(name="bn_2")(x)
x = keras.layers.Dropout(0.3, name="dropout_2")(x)

# 分支 1: 流失预测（二分类）
churn_out = keras.layers.Dense(32, activation="relu", name="churn_hidden")(x)
churn_out = keras.layers.Dense(1, activation="sigmoid", name="churn_prob")(churn_out)

# 分支 2: 活跃度分类（3 级）
activity_out = keras.layers.Dense(32, activation="relu", name="activity_hidden")(x)
activity_out = keras.layers.Dense(3, activation="softmax", name="activity_level")(activity_out)

func_model = keras.Model(
    inputs=inputs,
    outputs=[churn_out, activity_out],
    name="functional_multitask_model",
)

# 查看模型结构
keras.utils.plot_model(func_model, show_shapes=True, show_layer_names=True, dpi=60)
func_model.summary()
```

> **坑点**：`plot_model` 需要安装 graphviz（`pip install graphviz` 并安装系统 graphviz 二进制）。

**步骤三：两种 API 的训练对比**

```python
# 构造多任务标签
# 活跃度标签：根据 activity_score 分为 3 级
activity_labels = np.zeros(len(y), dtype=np.int32)
activity_labels[activity_score > np.percentile(activity_score, 70)] = 2  # 高活跃
activity_labels[activity_score < np.percentile(activity_score, 30)] = 0  # 低活跃
activity_labels[(activity_score >= np.percentile(activity_score, 30)) &
                (activity_score <= np.percentile(activity_score, 70))] = 1  # 中活跃

y_train_activity = activity_labels[:len(y_train)]
y_test_activity = activity_labels[len(y_train):]

# 编译多输出模型
func_model.compile(
    optimizer=keras.optimizers.Adam(learning_rate=1e-3),
    loss={
        "churn_prob": "binary_crossentropy",
        "activity_level": "sparse_categorical_crossentropy",
    },
    loss_weights={"churn_prob": 1.0, "activity_level": 0.5},  # 活跃度任务权重减半
    metrics={
        "churn_prob": ["accuracy", keras.metrics.AUC(name="auc")],
        "activity_level": ["accuracy"],
    },
)

# 训练
history_func = func_model.fit(
    X_train,
    {"churn_prob": y_train, "activity_level": y_train_activity},  # 字典格式的多目标
    batch_size=256,
    epochs=10,
    validation_split=0.2,
    verbose=1,
)

# 评估
func_results = func_model.evaluate(
    X_test,
    {"churn_prob": y_test, "activity_level": y_test_activity},
    verbose=0,
    return_dict=True,
)
print(f"\nFunctional 多任务模型测试结果:")
for k, v in func_results.items():
    print(f"  {k}: {v:.4f}")
```

运行输出（示例）：
```
Epoch 1/10 - loss: 0.8156 - churn_prob_loss: 0.5312 - activity_level_loss: 0.5687
...
Epoch 10/10 - loss: 0.5623 - churn_prob_loss: 0.3812 - activity_level_loss: 0.3622

Functional 多任务模型测试结果:
  loss: 0.5601
  churn_prob_loss: 0.3798
  activity_level_loss: 0.3605
  churn_prob_accuracy: 0.8241
  churn_prob_auc: 0.9051
  activity_level_accuracy: 0.8456
```

**步骤四：Sequential vs Functional 可维护性对比**

| 对比维度 | Sequential | Functional API |
|----------|------------|----------------|
| 代码行数 | 8 行 | 15 行（多任务） |
| 多输入支持 | 不支持 | 支持 `[input1, input2]` |
| 多输出支持 | 不支持 | 支持 `[output1, output2]` |
| 层共享 | 不支持 | 同一实例多次调用 |
| 残差/跳跃连接 | 不支持 | 支持 `x + skip` |
| 模型可视化 | `summary()` | `summary()` + `plot_model()` |
| 从 Sequential 迁移到 Functional | 需完全重写 | — |
| 适合场景 | 简单分类/回归 | 多任务、复杂拓扑 |

**迁移方案**：如果你的项目从 Sequential 起步，后面需要升级到多输出/多输入，重构步骤：

```python
# 原 Sequential 模型
seq_model = keras.Sequential([
    keras.layers.Dense(64, activation="relu", input_shape=(15,)),
    keras.layers.Dense(1, activation="sigmoid"),
])

# 等价 Functional 写法（重构基础）
inputs = keras.Input(shape=(15,))
x = keras.layers.Dense(64, activation="relu")(inputs)
x = keras.layers.Dense(32, activation="relu")(x)        # ← 在这里插新的分支
output_a = keras.layers.Dense(1, activation="sigmoid", name="task_a")(x)
output_b = keras.layers.Dense(3, activation="softmax", name="task_b")(x)
new_model = keras.Model(inputs=inputs, outputs=[output_a, output_b])

# 权重迁移：Sequential 的第一层权重 copy 到 Functional
new_model.layers[1].set_weights(seq_model.layers[0].get_weights())
```

### 3.3 常用 compile 参数速查

| 参数 | 说明 | 示例 |
|------|------|------|
| `optimizer` | 优化器 | `"adam"`, `keras.optimizers.SGD(0.01)` |
| `loss` | 损失函数 | `"binary_crossentropy"`, `{"out1": "mse", "out2": "categorical_crossentropy"}` |
| `metrics` | 监控指标 | `["accuracy"]`, `[keras.metrics.AUC()]` |
| `loss_weights` | 多输出损失权重 | `[1.0, 0.5]` 或 `{"out1": 1.0, "out2": 0.5}` |
| `weighted_metrics` | 带样本权重的指标 | `["accuracy"]` |
| `run_eagerly` | 逐行执行（调试用） | `True`（极慢，仅调试用） |

## 4. 项目总结

### 4.1 优点与缺点

| API | 优点 | 缺点 |
|-----|------|------|
| **Sequential** | 代码最简洁（3-5 行建模）；适合 80% 场景；不易出错；团队学习成本低 | 只能单输入→单输出→线性拓扑；无法实现层共享/残差连接 |
| **Functional** | 支持任意拓扑（DAG）；多输入多输出；层共享；可视化强 | 代码多于 Sequential；层调用顺序易写错；调试时需要 trace 前向计算图 |
| **Subclassing** | 完全自由（动态控制流）；适合研究和创新模型 | 代码最多；不易序列化（`save` 需要 `get_config`）；可能丢失 SavedModel 优化 |

### 4.2 适用场景

**Sequential 适用场景**：
1. 标准 MLP 分类/回归
2. 简单 CNN 图像分类（卷积层线性堆叠）
3. 快速原型验证

**Functional API 适用场景**：
1. 多任务学习（同时预测流失 + 活跃度）
2. 多输入模型（用户特征 + 物品特征 → 匹配度）
3. 残差网络（ResNet 的 skip connection）
4. Inception 等多分支架构
5. 模型融合/集成

**不适用 Sequential 的场景**：任何需要分支、多输出、多输入、层共享、跳跃连接的模型架构。

### 4.3 注意事项

- **命名冲突**：Functional API 中同名的 Layer 实例被多次调用时，Keras 会自动生成唯一名称（如 `dense` → `dense_1`），建议显式指定 `name` 参数避免混淆
- **plot_model 依赖**：需要 `pip install pydot graphviz`，Windows 还需安装 graphviz 系统包并加入 PATH
- **多输出 loss 字典**：key 必须与层 `name` 严格一致，拼写错误会导致 KeyError
- **BatchNormalization 在推理时行为不同**：`model.predict()` 时自动使用移动均值和方差（与训练态不同），确保 `training=False`

### 4.4 常见踩坑经验

1. **坑**：Sequential 模型的 `summary()` 第一行显示 `Output Shape: (None, 15)` 但实际输入是 `(None, 16)`。
   **根因**：第一层没声明 `input_shape` 或 `Input` 层，导致模型在 `fit` 时才推断形状，但 `summary()` 是推测的。
   **解决**：始终在 Sequential 第一层加 `input_shape=` 或使用 `keras.Input(shape=...)`，不要依赖延迟推断。

2. **坑**：Functional 多输出模型的 `model.evaluate()` 返回的 loss 值远大于训练时的。
   **根因**：`loss_weights` 只影响训练时梯度的加权，`evaluate` 返回的 `loss` 也有加权。但 `loss_weights` 不同步到 `evaluate` 的单项指标。
   **解决**：在 `evaluate` 时用 `return_dict=True` 查看每个子 loss 的具体值，不要只看总 loss。

3. **坑**：从 Sequential 切换到 Functional 后，原来保存的权重文件（`.h5`）加载失败。
   **根因**：H5 格式按层名称匹配权重，Functional 重构后隐含的层名可能变了（`dense_1` vs `dense_2`）。
   **解决**：使用 `.keras` 格式（Keras v3 原生格式）保存，或手动 `new_layer.set_weights(old_model.layers[i].get_weights())` 逐层迁移。

### 4.5 思考题

1. 下面的 Functional 模型构建代码中存在一个 bug——`shared_layer` 实际上并没有被共享。找出问题并修复：
   ```python
   inp1 = keras.Input(shape=(10,))
   inp2 = keras.Input(shape=(10,))
   x1 = keras.layers.Dense(16, activation="relu")(inp1)
   x2 = keras.layers.Dense(16, activation="relu")(inp2)
   merged = keras.layers.concatenate([x1, x2])
   ```

2. 设计一个方案：如何在不改动模型代码的情况下，用 Keras Callback 机制实现"每个 epoch 结束后自动保存最佳模型"和"训练 loss 连续 3 个 epoch 不下降则终止训练"。（提示：参考 `ModelCheckpoint` 和 `EarlyStopping`）

### 4.6 推广计划提示

- **新人开发**：先精通用 Sequential 完成简单分类/回归任务，再用 Functional API 实现多任务模型（本章实战代码）
- **算法工程师**：建议团队建模规范——标准单任务用 Sequential，复杂拓扑用 Functional，动态逻辑用 Subclassing。三者在组织内分工明确，Code Review 时一目了然
- **测试工程师**：对 Functional API 的多输出模型，单元测试需要验证多个输出的形状、数值范围和梯度连通性
