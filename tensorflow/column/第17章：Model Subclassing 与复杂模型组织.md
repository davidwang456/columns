# 第17章：Model Subclassing 与复杂模型组织

## 1. 项目背景

算法工程师阿杰接手了一个"电商广告多目标优化"项目。广告系统不仅要预测用户点击率（CTR），还要预测点击后的转化率（CVR）——即用户不仅要点广告，还要真正下单。业务方希望一个模型同时输出两个预测值：`click_prob` 和 `conv_prob`。

阿杰一开始用第 5 章的 Functional API 搭了一个双输出模型：

```python
inputs = Input(shape=(50,))
shared = Dense(128, activation="relu")(inputs)
click_out = Dense(1, activation="sigmoid", name="click")(shared)
conv_out  = Dense(1, activation="sigmoid", name="conv")(shared)
model = Model(inputs, [click_out, conv_out])
```

模型跑通了，但新的需求来了——产品经理说："CTR 和 CVR 之间有关系——用户点击后可能不会立刻买，但会先加购物车，过几天再买。我们希望 CVR 的预测能利用 CTR 分支的中间特征。"阿杰需要在 CTR 分支和 CVR 分支之间加一条"信息高速公路"，这在 Functional API 里还能做，但加上动态路由、条件分支后就越来越吃力。

更麻烦的是，CTR 预估模型里有一个自定义的"特征交叉层"（FM Cross Layer），标准的 `Dense` 层实现不了，必须自己写。阿杰需要自定义一个 Layer 类，还要保证它能被 `model.save()` 正确序列化。

**痛点放大**：Sequential 和 Functional API 适合"静态计算图"——模型结构在 `__init__` 时确定，训练时不变。但真实业务中常见的需求——自定义计算逻辑（如 FM Cross、Attention Pooling）、动态分支（根据输入值走不同路径）、可复用的模型子模块——都需要 Model Subclassing。

## 2. 项目设计

**小胖**（看着阿杰的代码）：Subclassing 不就是继承一个 `keras.Model` 然后写个 `call` 方法吗？我在第 4 章就做过手写训练循环了，跟这有啥不一样？

**大师**：区别在于"工程的复杂度"。第 4 章你手写的训练循环只是把 `GradientTape` + `optimizer.apply_gradients` 放在一个 for 循环里——模型本身还是用 `Sequential` 搭的。Subclassing 是让你从 Layer 级别开始自定义。想象一下——标准 `Dense` 层做的是 `y = activation(Wx + b)`，但你需要的 FM Cross 层做的是 `y = x0 + x0 * sum(x_i * v_i)`，其中 v_i 是一个可学习的向量。标准层做不到，你必须自己写。

**技术映射**：Model Subclassing 让你完全控制前向传播逻辑。继承 `keras.Model` → 实现 `__init__`(定义子层) + `call`(定义前向逻辑) → 可选 `build`(惰性创建权重) + `get_config`(序列化)。

**小白**（盯着屏幕上的报错）：那 `build` 和 `__init__` 里的权重创建有什么区别？我经常看到有人在 `__init__` 里加 `self.dense = Dense(64)`，也有人在 `build` 里创建变量。

**大师**：这是 Keras 里最容易搞混的概念之一。`build(input_shape)` 是"惰性初始化"——只有当你知道输入形状时才创建权重。如果你在 `__init__` 里写 `Dense(64)` 但不传 `input_shape`，Dense 层的权重直到第一次被调用时才真正创建（因为那时才知道输入维度）。但如果你写 `Dense(64, input_shape=(50,))`，权重在 `__init__` 时就创建了。

```python
class MyLayer(keras.layers.Layer):
    def __init__(self, units, **kwargs):
        super().__init__(**kwargs)
        self.units = units
        # 不在这里创建 self.kernel，因为还不知道 input_dim

    def build(self, input_shape):
        # input_shape 是 (batch, features)，取 features 作为输入维度
        self.kernel = self.add_weight(
            shape=(input_shape[-1], self.units),
            initializer="glorot_uniform",
            trainable=True,
        )

    def call(self, inputs):
        return tf.matmul(inputs, self.kernel)
```

**大师**：如果输入维度是已知的（如 `Embedding` 层的 `input_dim` 参数），你可以在 `__init__` 里直接创建权重。如果维度依赖实际输入（比如上一层输出是 64 还是 128 不确定），就用 `build`。

**技术映射**：`build(input_shape)` 是延迟初始化模式——在第一次调用 `call` 前由框架自动调用，此时输入形状已知。推荐总是用 `build`，除非你在 `__init__` 时已确定所有维度。

**小白**：那 `training` 参数在 `call` 里怎么用？我写了自定义层后 Dropout 和 BN 的行为全乱了。

**大师**：`call(self, inputs, training=None)` 里的 `training` 是个布尔值或 None。关键规则——如果你的自定义层内部有子层（比如里面有 Dropout/BN），你必须把 `training` 传给它们：

```python
class MyBlock(keras.layers.Layer):
    def __init__(self, units, **kwargs):
        super().__init__(**kwargs)
        self.dense = keras.layers.Dense(units)
        self.bn = keras.layers.BatchNormalization()
        self.dropout = keras.layers.Dropout(0.3)

    def call(self, inputs, training=None):
        x = self.dense(inputs)
        x = self.bn(x, training=training)      # 必须传！
        x = self.dropout(x, training=training)  # 必须传！
        return x
```

如果不传 `training`——在推理时 Dropout 仍然会随机丢弃神经元，BN 会用 batch 级统计量（而非全局统计量），导致推理结果不可复现。

**小胖**：那 `get_config` 呢？自定义层保存模型时报错说找不到这个层。

**大师**：当 Keras 保存模型时，它需要知道"这个自定义层构造时需要哪些参数"。如果自定义层有超参数（如 `units=64`），必须实现 `get_config` 来告诉 Keras "这些参数是什么"，否则加载模型时无法重建这个层。

```python
def get_config(self):
    config = super().get_config()
    config.update({"units": self.units})
    return config
```

这是自定义层最容易被忽略的一步——你辛辛苦苦训练好了模型，一保存再加载就报 `Unknown layer`。**get_config 是自定义层的"身份证"**，没有它模型就失忆了。

## 3. 项目实战

### 3.1 环境准备

```bash
pip install tensorflow==2.16.1 numpy==1.26.4
```

### 3.2 分步实现

**步骤一：自定义 FM Cross Layer**

目标：实现 FwFM 中的特征交叉层——`x0 + sum(x_i * x_j * w_ij)`。

```python
import tensorflow as tf
from tensorflow import keras

class FMCrossLayer(keras.layers.Layer):
    """实现 FM 二阶特征交叉: output = 0.5 * (sum(v·x)^2 - sum((v·x)^2))"""

    def __init__(self, embed_dim=16, **kwargs):
        super().__init__(**kwargs)
        self.embed_dim = embed_dim

    def build(self, input_shape):
        # input_shape = (batch, num_features)
        num_features = input_shape[-1]
        self.V = self.add_weight(
            name="cross_weights",
            shape=(num_features, self.embed_dim),
            initializer="glorot_uniform",
            trainable=True,
        )

    def call(self, inputs):
        # inputs: (batch, num_features)
        # vx: (batch, embed_dim) —— 每个特征乘以自己的隐向量然后求和
        vx = tf.matmul(inputs, self.V)  # (batch, embed_dim)
        # FM 公式: 0.5 * (sum(vx)^2 - sum(vx^2))
        sum_square = tf.square(tf.reduce_sum(vx, axis=1, keepdims=True))
        square_sum = tf.reduce_sum(tf.square(vx), axis=1, keepdims=True)
        cross_term = 0.5 * (sum_square - square_sum)
        return tf.concat([inputs, cross_term], axis=-1)

    def get_config(self):
        config = super().get_config()
        config.update({"embed_dim": self.embed_dim})
        return config

# 测试 FM Cross Layer
x = tf.constant([[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]])
layer = FMCrossLayer(embed_dim=8)
output = layer(x)
print(f"FM Cross 输入 shape: {x.shape}, 输出 shape: {output.shape}")
```

运行输出：
```
FM Cross 输入 shape: (2, 3), 输出 shape: (2, 4)
```

**步骤二：多任务 CTR+CVR Subclassing 模型**

目标：实现共享 backbone + CTR 分支 + CVR 分支（CVR 利用 CTR 特征）。

```python
from tensorflow import keras

class MultiTaskCTRCVR(keras.Model):
    """多任务模型：CTR 预测 + CVR 预测，CVR 分支接收 CTR 的中间特征"""

    def __init__(self, feature_dim, hidden_units=(128, 64), **kwargs):
        super().__init__(**kwargs)
        self.feature_dim = feature_dim

        # 共享层
        self.shared_dense1 = keras.layers.Dense(hidden_units[0], activation="relu")
        self.shared_bn = keras.layers.BatchNormalization()
        self.shared_dropout = keras.layers.Dropout(0.3)

        self.shared_dense2 = keras.layers.Dense(hidden_units[1], activation="relu")

        # CTR 分支
        self.ctr_hidden = keras.layers.Dense(32, activation="relu", name="ctr_hidden")
        self.ctr_out = keras.layers.Dense(1, activation="sigmoid", name="ctr_prob")

        # CVR 分支（利用 CTR 中间特征）
        self.cvr_concat = keras.layers.Concatenate()
        self.cvr_hidden = keras.layers.Dense(32, activation="relu", name="cvr_hidden")
        self.cvr_out = keras.layers.Dense(1, activation="sigmoid", name="cvr_prob")

        # 损失追踪器
        self.loss_tracker = keras.metrics.Mean(name="loss")
        self.ctr_loss_tracker = keras.metrics.Mean(name="ctr_loss")
        self.cvr_loss_tracker = keras.metrics.Mean(name="cvr_loss")
        self.ctr_auc = keras.metrics.AUC(name="ctr_auc")
        self.cvr_auc = keras.metrics.AUC(name="cvr_auc")

    def call(self, inputs, training=None):
        # 共享 backbone
        x = self.shared_dense1(inputs)
        x = self.shared_bn(x, training=training)
        x = self.shared_dropout(x, training=training)
        x = self.shared_dense2(x)

        # CTR 分支
        ctr_h = self.ctr_hidden(x)
        ctr_pred = self.ctr_out(ctr_h)

        # CVR 分支：拼接共享特征 + CTR 中间特征（信息高速公路）
        cvr_input = self.cvr_concat([x, tf.stop_gradient(ctr_h)])
        cvr_h = self.cvr_hidden(cvr_input)
        cvr_pred = self.cvr_out(cvr_h)

        return {"ctr_prob": ctr_pred, "cvr_prob": cvr_pred}

    def compute_loss(self, x, y, sample_weight=None):
        """自定义损失计算——处理 CTR 和 CVR 的不均衡权重"""
        y_pred = self(x, training=True)
        ctr_loss = keras.losses.binary_crossentropy(y["ctr"], y_pred["ctr_prob"])
        cvr_loss = keras.losses.binary_crossentropy(y["cvr"], y_pred["cvr_prob"])
        # CVR 可能更稀疏，给更低权重
        return tf.reduce_mean(ctr_loss) + 0.3 * tf.reduce_mean(cvr_loss)

    def train_step(self, data):
        x, y = data
        with tf.GradientTape() as tape:
            y_pred = self(x, training=True)
            ctr_loss = tf.reduce_mean(keras.losses.binary_crossentropy(y["ctr"], y_pred["ctr_prob"]))
            cvr_loss = tf.reduce_mean(keras.losses.binary_crossentropy(y["cvr"], y_pred["cvr_prob"]))
            total_loss = ctr_loss + 0.3 * cvr_loss

        grads = tape.gradient(total_loss, self.trainable_variables)
        self.optimizer.apply_gradients(zip(grads, self.trainable_variables))

        # 更新指标
        self.loss_tracker.update_state(total_loss)
        self.ctr_loss_tracker.update_state(ctr_loss)
        self.cvr_loss_tracker.update_state(cvr_loss)
        self.ctr_auc.update_state(y["ctr"], y_pred["ctr_prob"])
        self.cvr_auc.update_state(y["cvr"], y_pred["cvr_prob"])

        return {m.name: m.result() for m in self.metrics}

    def test_step(self, data):
        x, y = data
        y_pred = self(x, training=False)
        ctr_loss = tf.reduce_mean(keras.losses.binary_crossentropy(y["ctr"], y_pred["ctr_prob"]))
        cvr_loss = tf.reduce_mean(keras.losses.binary_crossentropy(y["cvr"], y_pred["cvr_prob"]))

        self.loss_tracker.update_state(ctr_loss + 0.3 * cvr_loss)
        self.ctr_auc.update_state(y["ctr"], y_pred["ctr_prob"])
        self.cvr_auc.update_state(y["cvr"], y_pred["cvr_prob"])
        return {m.name: m.result() for m in self.metrics}

    @property
    def metrics(self):
        return [self.loss_tracker, self.ctr_loss_tracker, self.cvr_loss_tracker,
                self.ctr_auc, self.cvr_auc]

# 模拟训练
import numpy as np
tf.random.set_seed(42)
np.random.seed(42)

N = 2000; D = 50
X_train = np.random.randn(N, D).astype(np.float32)
y_ctr = (np.random.rand(N) > 0.7).astype(np.float32)
y_cvr = (y_ctr * (np.random.rand(N) > 0.5)).astype(np.float32)  # CVR 更加稀疏

model = MultiTaskCTRCVR(feature_dim=D)
model.compile(optimizer=keras.optimizers.Adam(1e-3))
model.fit(
    X_train,
    {"ctr": y_ctr.reshape(-1, 1), "cvr": y_cvr.reshape(-1, 1)},
    epochs=5, batch_size=128, validation_split=0.2, verbose=1,
)
```

**步骤三：单元测试验证模型输入输出**

目标：为自定义模型编写形状验证和梯度连通性测试。

```python
import tensorflow as tf
from tensorflow import keras
import numpy as np

def test_model_shapes():
    """验证模型输入输出的形状正确性"""
    model = MultiTaskCTRCVR(feature_dim=50)

    # 未 build 前先跑一次 trigger build
    _ = model(tf.constant(np.random.randn(2, 50).astype(np.float32)), training=False)

    # 测试单样本
    x = tf.constant(np.random.randn(1, 50).astype(np.float32))
    outputs = model(x, training=False)
    assert "ctr_prob" in outputs, "缺少 ctr_prob 输出"
    assert "cvr_prob" in outputs, "缺少 cvr_prob 输出"
    assert outputs["ctr_prob"].shape == (1, 1), f"CTR shape 错误: {outputs['ctr_prob'].shape}"
    assert outputs["cvr_prob"].shape == (1, 1), f"CVR shape 错误: {outputs['cvr_prob'].shape}"

    # 测试 batch
    x_batch = tf.constant(np.random.randn(32, 50).astype(np.float32))
    outputs_batch = model(x_batch, training=False)
    assert outputs_batch["ctr_prob"].shape == (32, 1)

    print("✅ 所有形状断言通过")

def test_gradient_flow():
    """验证梯度能正常回传到所有可训练变量"""
    model = MultiTaskCTRCVR(feature_dim=50)
    x = tf.constant(np.random.randn(4, 50).astype(np.float32))
    y = {
        "ctr": tf.constant([[1.0], [0.0], [1.0], [0.0]]),
        "cvr": tf.constant([[1.0], [0.0], [0.0], [0.0]]),
    }

    with tf.GradientTape() as tape:
        outputs = model(x, training=True)
        loss = tf.reduce_mean(keras.losses.binary_crossentropy(y["ctr"], outputs["ctr_prob"])) + \
               tf.reduce_mean(keras.losses.binary_crossentropy(y["cvr"], outputs["cvr_prob"]))

    grads = tape.gradient(loss, model.trainable_variables)
    none_grads = [v.name for v, g in zip(model.trainable_variables, grads) if g is None]
    assert len(none_grads) == 0, f"以下变量梯度为 None: {none_grads}"

    print(f"✅ {len(model.trainable_variables)} 个变量全部有梯度")

# 运行测试
test_model_shapes()
test_gradient_flow()
```

### 3.3 三种建模方式对比

| 能力 | Sequential | Functional API | Model Subclassing |
|------|-----------|---------------|-------------------|
| 单输入单输出线性拓扑 | ✅ | ✅ | ✅ |
| 多输入/多输出 | ❌ | ✅ | ✅ |
| 残差/跳跃连接 | ❌ | ✅ | ✅ |
| 共享层（同一实例多次调用） | ❌ | ✅ | ✅ |
| 动态控制流（if/while） | ❌ | ❌ | ✅ |
| `model.save()` / `load_model()` | ✅ 自动 | ✅ 自动 | ⚠️ 需手动实现 `get_config` |
| `model.summary()` 自动追踪 shape | ✅ | ✅ | ⚠️ 需 `build()` 或先 call 一次 |
| 代码量 | 最少 | 中 | 最多 |

## 4. 项目总结

### 4.1 优点与缺点

| 方面 | 优点 | 缺点 |
|------|------|------|
| 灵活性 | 完全自由的前向传播逻辑，支持动态 if/while/循环 | 容易写出难以调试的控制流 |
| 模块化 | 自定义 Layer 可跨项目复用（如 FMCrossLayer） | 需要额外实现 `get_config` 才能序列化 |
| 多任务支持 | 多分支、信息高速公路、任意 loss 组合 | 需手动管理多个 loss tracker 和 metric |
| 可测试性 | 每个 Layer 可独立单元测试 | 需要额外写形状和梯度测试（不像 Sequential 自动保证） |

### 4.2 适用场景

1. **多任务学习**：CTR+CVR、分类+回归等多输出场景
2. **自定义算子**：FM Cross、Attention Pooling、动态路由等非标准计算
3. **GAN/强化学习等非标准训练范式**：需要精细控制损失、梯度和更新逻辑
4. **可复用的模型组件**：将自定义层封装为 pip 包在团队内共享

**不适用场景**：
1. 标准 CNN/LSTM 分类回归——Sequential 或 Functional API 更简洁，且自动支持 save/summary
2. 团队成员不熟悉 `get_config`/`build`/`training` 传递机制——维护成本可能很高

### 4.3 注意事项

- **`call` 必须显式传 `training`**：每个自定义 Layer 内的 Dropout/BN 子层必须接收 `training=training`，否则推理时行为异常
- **`get_config` 不可省略**：不实现 `get_config`，`model.save()` 和 `clone_model()` 都会失败
- **`train_step` 覆写后 Callback 行为仍在**：Callback 的 `on_epoch_begin` 等钩子仍会被调用，但如果覆写了 `test_step` 而没更新 `self.compiled_metrics`，`val_accuracy` 会不准确
- **`build` 中的 `add_weight` 变量命名**：如果变量名与子层重名，Keras 追踪机制可能报错，建议用 `self.xxx_weight` 命名风格

### 4.4 常见踩坑经验

1. **坑**：Model Subclassing 模型 `model.save()` 时报 `NotImplementedError: Layers with arguments in `__init__` must override `get_config`。
   **根因**：自定义层 `__init__` 中有额外参数（如 `units`），但没实现 `get_config`。
   **解决**：实现 `get_config` 方法，用 `config.update({"units": self.units})` 记录所有自定义参数。

2. **坑**：在 `call` 中创建了新的 `tf.Variable`（如计数器），每次 call 都会创建新变量导致显存泄漏。
   **根因**：`call` 每次前向都会执行，新变量应在 `build` 或 `__init__` 中创建。
   **解决**：所有 `tf.Variable` 创建放在 `build` 中（使用 `self.add_weight`），`call` 只用不创建。

3. **坑**：`model.summary()` 显示 "multiple" 而非具体维度。
   **根因**：Subclassing 模型的形状信息需要在第一次 `call` 后才能确定，`summary()` 时还没 build。
   **解决**：在 `model.summary()` 之前手动调用一次 `model(tf.zeros((1, input_dim)))` 触发 build，或覆写 `compute_output_shape`。

### 4.5 思考题

1. 本章的 `FMCrossLayer` 实现了 FM 的二阶交叉。如果要求你扩展为支持三阶交叉（`x_i * x_j * x_k`），你会如何修改 `call` 方法？计算复杂度会如何变化？

2. 一个 `Model Subclassing` 模型需要在 TensorFlow Serving 上部署。它能把 `call` 方法注册为 Signature 的推理函数吗？如果不能，你会如何解决？

### 4.6 推广计划提示

- **新人开发**：先掌握 Functional API 写多任务模型，再进阶到 Subclassing 实现自定义训练循环
- **算法工程师**：自定义 Layer 应有独立文件（如 `layers/fm_cross.py`），并附带形状测试
- **测试工程师**：为所有自定义 Layer 编写 `test_build` / `test_forward_shape` / `test_gradient_flow` 三个基本测试
