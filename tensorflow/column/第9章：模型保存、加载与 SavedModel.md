# 第9章：模型保存、加载与 SavedModel

## 1. 项目背景

算法工程师阿杰训练好了一个用户评分预测模型（输入用户特征和商品特征，输出 1-5 分的评分），在 Jupyter Notebook 上测试效果很好。现在需要把这个模型交付给后端工程师老张，集成到线上推荐服务中。

阿杰把保存模型的方式发给了老张——用的 `model.save("model.h5")`。老张在自己的 Python 脚本中用 `tf.keras.models.load_model("model.h5")` 加载模型，然后调用 `model.predict(user_features)`。结果报错了：`ValueError: Layer "dropout" expects 1 input(s), but it received 2 input tensors`。

两人折腾了 2 小时，发现原因：阿杰训练用的是 TensorFlow 2.16，老张服务端装的是 TensorFlow 2.10，H5 格式在不同版本间的兼容性有问题。而且阿杰的训练代码里有 Dropout 层，训练时 `training=True`，推理时应该是 `training=False`——但老张直接调 `model.predict()`，并没意识到 Keras 的 `predict()` 在某些版本下对 Dropout 的行为不一致。

更严重的是，老张不知道模型期望的输入是什么——用户特征的维度是 15 还是 18？商品特征是 10 维还是 12 维？特征拼接的顺序是先用户还是先商品？阿杰说"我都写在代码注释里了"，但老张的 Python 环境里根本没有阿杰的代码仓库。

**痛点放大**：模型保存不只是"把权重存下来"——它需要解决三个问题：(1) 格式兼容性（不同版本、不同环境下能否正确加载）；(2) 推理接口标准化（输入输出的 shape、dtype、名称是什么，不需要看源码就能推理）；(3) 训练态与推理态的差异（Dropout/BatchNorm 的 behavior 不同，必须显式切换）。

## 2. 项目设计

**小胖**（举着一个 U 盘）：不就保存一个模型嘛！像 Word 文档一样，Ctrl+S 存成文件，发给别人打开就行了。搞三种格式（Keras/H5/SavedModel）不是没事找事吗？

**大师**：你存 Word 文档，发给用 Pages 的人——打不开。发给用 Word 2010 的人——排版乱了。发给手机——字体没了。模型文件比 Word 文档更复杂——它不只是"文字+格式"，它是一个计算程序（前向传播结构）+ 一堆数字（权重）+ 一些配置（优化器状态）。Keras、H5、SavedModel 三种格式就是解决"在不同地方打开"的问题。

**小胖**：那三种格式有什么区别？

**大师**：

| 格式 | 后缀 | 包含内容 | 适用场景 |
|------|------|----------|----------|
| **Keras v3** | `.keras` | 模型结构 + 权重 + 编译信息 + Optimizer 状态 | Keras 3 最佳选择 |
| **H5** | `.h5` / `.hdf5` | 模型结构 + 权重（旧版不全） | 兼容旧版 TF2.x |
| **SavedModel** | 目录 | 完整的计算图 + 权重 + Signature | 部署到 Serving/TFLite/TF.js |

**大师**：`.keras` 是 Keras 3 的原生格式，保存最完整——结构、权重、优化器状态全存了，加载回来可以继续训练。`.h5` 是历史遗留格式，兼容性差（特别是不同版本间）。`SavedModel` 是 TensorFlow 的"工业标准"——不只是存权重，还存了推理签名（Signature），你部署到 TensorFlow Serving 时，Serving 读的就是 SavedModel。

**技术映射**：`.keras` 用于本地开发和断点续训（含 optimizer 状态）；H5 仅用于旧项目兼容；SavedModel 用于模型部署和跨语言推理（Python/C++/Java）。

**小白**（盯着屏幕上的报错）：那我怎么让老张知道模型输入到底是什么？总不能每次都跑去看阿杰的训练代码吧？

**大师**：这就是 `SignatureDef` 的作用。SavedModel 里面有个 `saved_model.pb` 文件，记录了这个模型接受什么输入、产出什么输出、每个张量的名字和形状。

```bash
# 用命令行工具查看 SavedModel 的 Signature
saved_model_cli show --dir ./my_model --all
```

它会输出类似这样的信息：

```
signature_def['serving_default']:
  inputs['user_features'] tensor_info: dtype: DT_FLOAT, shape: (-1, 15)
  inputs['item_features'] tensor_info: dtype: DT_FLOAT, shape: (-1, 12)
  outputs['rating']       tensor_info: dtype: DT_FLOAT, shape: (-1, 1)
```

**大师**：你看，不需要看一行阿杰的训练代码，老张拿到这个信息就知道：输入是两个张量，用户特征 15 维 float、商品特征 12 维 float，输出是 1 维 float 评分。这就是 Signature——模型的"使用说明书"。

**技术映射**：SignatureDef 是 SavedModel 的推理接口契约，定义了输入输出的名称、数据类型和形状，让模型消费者不需要阅读训练代码即可集成。

**小白**：那为什么 `.predict()` 和 SavedModel 推理出来的结果有时不一样？特别是模型里有 Dropout 和 BatchNormalization？

**大师**（在白板上画了两张图）：这是很多人的大坑。Dropout 在训练时，随机把一部分神经元"关掉"（输出 0），以防止过拟合。但在推理时，所有神经元都工作——如果推理时还在"随机关"，那同一张图推理 5 次可能得到 5 个不同结果。BatchNormalization 在训练时用当前 batch 的均值/方差归一化，推理时用训练过程中累计的全局均值/方差。

```python
# 训练时
outputs = model(x, training=True)    # Dropout 生效, BN 用 batch 统计量

# 推理时
outputs = model(x, training=False)   # Dropout 关闭, BN 用全局统计量
# model.predict(x) 内部默认 training=False，但不完全可靠
```

**大师**：`.predict()` 虽然默认 `training=False`，但某些自定义层可能存在 training 参数传递错误。最保险的做法是用 SavedModel——它会把这些行为固化成推理专用的图，彻底排除 training 模式的干扰。

**技术映射**：`model(x, training=True)` 对应训练行为；`model(x, training=False)` 对应推理行为；`.predict()` 和 SavedModel 推理均默认推理模式，但自定义层需要显式处理 `training` 参数。

**小胖**：那 `model.save()` 和 `tf.saved_model.save()` 有区别吗？我看教程里两个都有。

**大师**：`model.save("path.keras")` 保存 Keras 格式；`model.save("path")` 如果路径不带扩展名，会自动保存为 SavedModel 格式；`tf.saved_model.save(model, "path")` 强制保存为 SavedModel，且对 Keras Model 和非 Keras 的 tf.Module 都适用。一般来说，用 `model.save("my_model")` 就够了，Keras 会自动推断格式。

## 3. 项目实战

### 3.1 环境准备

```bash
pip install tensorflow==2.16.1 numpy==1.26.4
```

### 3.2 分步实现

**步骤一：三种格式的保存与加载对比**

目标：理解 `.keras`、`.h5`、SavedModel 三种格式在代码层面的差异。

```python
import tensorflow as tf
from tensorflow import keras
import numpy as np
import os
import tempfile
import json

# 准备临时目录
base_dir = tempfile.mkdtemp()

# 构建一个简单的评分预测模型
inputs = keras.Input(shape=(15,), name="user_features")
x = keras.layers.Dense(64, activation="relu", name="dense_1")(inputs)
x = keras.layers.Dropout(0.3, name="dropout")(x)
x = keras.layers.Dense(32, activation="relu", name="dense_2")(x)
outputs = keras.layers.Dense(1, name="rating")(x)
model = keras.Model(inputs=inputs, outputs=outputs, name="rating_model")

model.compile(optimizer="adam", loss="mse", metrics=["mae"])

# 模拟训练
X = np.random.randn(100, 15).astype(np.float32)
y = np.random.randn(100, 1).astype(np.float32)
model.fit(X, y, epochs=2, batch_size=16, verbose=0)

# 获取稳定的推理结果作为基准
baseline_pred = model.predict(X[:5], verbose=0)
print(f"基准预测 (前5个样本):\n{baseline_pred.flatten()}")

# === 格式 1: Keras v3 (.keras) ===
keras_path = os.path.join(base_dir, "rating_model.keras")
model.save(keras_path)
print(f"\n.keras 文件大小: {os.path.getsize(keras_path) / 1024:.1f} KB")

loaded_keras = keras.models.load_model(keras_path)
keras_pred = loaded_keras.predict(X[:5], verbose=0)
print(f"Keras 加载后预测一致性: {np.allclose(baseline_pred, keras_pred, atol=1e-5)}")

# === 格式 2: SavedModel (目录) ===
savedmodel_path = os.path.join(base_dir, "rating_savedmodel")
model.save(savedmodel_path)  # 路径不带扩展名 → 自动存为 SavedModel
print(f"\nSavedModel 目录大小: {sum(
    os.path.getsize(os.path.join(dp, f))
    for dp, dn, fn in os.walk(savedmodel_path)
    for f in fn
) / 1024:.1f} KB")

loaded_sm = keras.models.load_model(savedmodel_path)
sm_pred = loaded_sm.predict(X[:5], verbose=0)
print(f"SavedModel 加载后预测一致性: {np.allclose(baseline_pred, sm_pred, atol=1e-5)}")

# SavedModel 命令行查看
print(f"\n查看 SavedModel 签名命令:")
print(f'  saved_model_cli show --dir "{savedmodel_path}" --all')
```

**步骤二：SavedModel Signature 定制与独立推理脚本**

目标：给 SavedModel 打上自定义 Signature，让推理端无需 Keras 即可运行。

```python
import tensorflow as tf
from tensorflow import keras
import numpy as np
import os
import tempfile

# 构建多输入模型：用户特征 + 商品特征 → 评分
user_input = keras.Input(shape=(15,), dtype=tf.float32, name="user_features")
item_input = keras.Input(shape=(12,), dtype=tf.float32, name="item_features")

# 合并特征
merged = keras.layers.concatenate([user_input, item_input], name="concat")
x = keras.layers.Dense(64, activation="relu")(merged)
x = keras.layers.Dropout(0.3)(x)
rating_output = keras.layers.Dense(1, name="rating")(x)

model = keras.Model(inputs=[user_input, item_input], outputs=rating_output, name="rating_model")
model.compile(optimizer="adam", loss="mse")

# 模拟训练
X_user = np.random.randn(200, 15).astype(np.float32)
X_item = np.random.randn(200, 12).astype(np.float32)
y = np.random.randn(200, 1).astype(np.float32)
model.fit([X_user, X_item], y, epochs=2, verbose=0)

# === 定制 Signature：使用 @tf.function 定义推理函数 ===
@tf.function(input_signature=[
    tf.TensorSpec(shape=[None, 15], dtype=tf.float32, name="user_features"),
    tf.TensorSpec(shape=[None, 12], dtype=tf.float32, name="item_features"),
])
def serve_fn(user_features, item_features):
    """定义推理签名函数"""
    return {
        "rating": model({"user_features": user_features, "item_features": item_features},
                        training=False)
    }

# 保存为 SavedModel
export_dir = os.path.join(tempfile.mkdtemp(), "rating_serving", "1")

tf.saved_model.save(
    model,
    export_dir,
    signatures={"serving_default": serve_fn},
)
print(f"模型已导出至: {export_dir}")
print(f"目录结构:")
for item in os.listdir(export_dir):
    print(f"  {item}")

# === 独立推理脚本（不需要 import Keras model！） ===
print("\n=== 独立推理测试 ===")
loaded = tf.saved_model.load(export_dir)
infer_fn = loaded.signatures["serving_default"]

# 模拟线上请求
test_user = tf.constant(np.random.randn(3, 15).astype(np.float32))
test_item = tf.constant(np.random.randn(3, 12).astype(np.float32))

result = infer_fn(user_features=test_user, item_features=test_item)
print(f"推理结果 shape: {result['rating'].shape}")
print(f"评分预测:\n{result['rating'].numpy().flatten()}")
print(f"\nSignature 结构:")
print(f"  inputs:  {[i for i in infer_fn.structured_input_signature]}")
print(f"  outputs: {[o for o in infer_fn.structured_outputs]}")
```

**步骤三：训练态 vs 推理态差异验证**

目标：验证 Dropout 和 BatchNormalization 在 training=True/False 下的行为差异。

```python
import tensorflow as tf
from tensorflow import keras
import numpy as np

# 构建含 Dropout 和 BN 的模型
model = keras.Sequential([
    keras.layers.Dense(32, activation="relu", input_shape=(10,)),
    keras.layers.BatchNormalization(),
    keras.layers.Dropout(0.5),
    keras.layers.Dense(1),
])
model.compile(optimizer="adam", loss="mse")

# 固定输入数据
fixed_input = tf.constant(np.ones((1, 10), dtype=np.float32))

# 测试 1: training=True — 每次结果不同（Dropout 随机丢弃）
print("=== training=True (5次推理) ===")
for i in range(5):
    out = model(fixed_input, training=True)
    print(f"  run {i+1}: {out.numpy().flatten()[0]:.6f}")

# 测试 2: training=False — 每次结果一致（Dropout 关闭）
print("\n=== training=False (5次推理) ===")
for i in range(5):
    out = model(fixed_input, training=False)
    print(f"  run {i+1}: {out.numpy().flatten()[0]:.6f}")

# 测试 3: model.predict() — 内部应自动设 training=False
print("\n=== model.predict() (5次) ===")
for i in range(5):
    out = model.predict(fixed_input, verbose=0)
    print(f"  run {i+1}: {out.flatten()[0]:.6f}")

# 测试 4: SavedModel 推理 — 固化推理模式
import tempfile, os
sm_path = os.path.join(tempfile.mkdtemp(), "test_sm")
model.save(sm_path)
loaded = tf.saved_model.load(sm_path)
infer = loaded.signatures["serving_default"]

print("\n=== SavedModel 推理 (5次) ===")
for i in range(5):
    out = infer(tf.constant(np.ones((1, 10), dtype=np.float32)))
    result = list(out.values())[0].numpy().flatten()[0]
    print(f"  run {i+1}: {result:.6f}")
```

运行输出（示例）：
```
=== training=True (5次推理) ===
  run 1: 0.123456
  run 2: 0.654321
  run 3: 0.234567
  run 4: 0.345678
  run 5: 0.012345

=== training=False (5次推理) ===
  run 1: 0.500000
  run 2: 0.500000
  run 3: 0.500000
  run 4: 0.500000
  run 5: 0.500000
```

### 3.4 关键 API 速查

| 操作 | 代码 |
|------|------|
| 保存 Keras 模型 | `model.save("path.keras")` |
| 保存 SavedModel | `model.save("path/")` 或 `tf.saved_model.save(model, "path/")` |
| 加载模型 | `keras.models.load_model("path")`（自动识别格式） |
| 仅保存/加载权重 | `model.save_weights("weights.h5")` / `model.load_weights("weights.h5")` |
| 命令行查看 SavedModel | `saved_model_cli show --dir ./model --all` |
| 从 SavedModel 加载推理函数 | `loaded = tf.saved_model.load(path); loaded.signatures["serving_default"](**inputs)` |
| 检查是否从 checkpoint 恢复 | `tf.train.latest_checkpoint(dir)` |

## 4. 项目总结

### 4.1 三种格式对比

| 特性 | `.keras` | H5 (`.h5`) | SavedModel (目录) |
|------|----------|-----------|-------------------|
| 保存完整度 | 架构+权重+优化器+编译配置 | 架构+权重（旧不完全） | 计算图+权重+Signature |
| 交叉版本兼容 | 好（Keras 3 标准） | 差（小版本间可能不兼容） | 优秀（TF1/2 兼容） |
| 跨语言支持 | 仅 Python/Keras | 仅 Python/Keras | Python/C++/Java/JS |
| 部署就绪 | 需转换 | 需转换 | 原生支持 Serving/TFLite |
| 继续训练 | 是 | 否（无 optimizer 状态） | 否（仅推理图） |
| 文件大小 | 小（单文件） | 小（单文件） | 大（多文件目录） |

### 4.2 适用场景

1. **开发迭代阶段**：用 `.keras` 保存，可断点续训
2. **跨团队交付**：用 SavedModel，附带 Signature 说明输入输出
3. **部署 TensorFlow Serving**：必须用 SavedModel 格式
4. **转换为 TFLite**：从 SavedModel 出发转换路径最稳定
5. **仅迁移权重**：用 `save_weights`/`load_weights`（不依赖模型代码）

**不适用场景**：
1. H5 格式——除非需要兼容 TF 2.10 之前的遗留系统，新项目一律不用
2. SavedModel 用于断点续训——它不保存 optimizer 状态，继续训练用 `.keras`

### 4.3 注意事项

- **命名一致性**：多输出模型保存时各输出层的 `name` 会写入 Signature，推理时通过 name 获取输出，改名会导致 `KeyError`
- **自定义层的序列化**：如果你的模型含自定义 Layer/Model，需要正确实现 `get_config()` 和 `from_config()`，否则 saved_model 无法加载
- **SavedModel 与 TF1 兼容**：TF2 保存的 SavedModel 不能在 TF1 加载；但 TF1 的可以 TF2 加载（向后兼容）
- **输入 shape 的 batch 维度**：Signature 中的 shape 建议设为 `(None, feature_dim)`，batch 维度留 None 以支持动态 batch

### 4.4 常见踩坑经验

1. **坑**：`model.save("model.h5")` 后 `load_model` 报 `Unknown layer: 'CustomLayer'`。
   **根因**：H5 格式只存层的配置字典，自定义层需要注册或传入 `custom_objects`。
   **解决**：使用 `.keras` 格式，或 `load_model(path, custom_objects={"CustomLayer": MyCustomLayer})`。

2. **坑**：SavedModel 推理和训练代码推理结果不一致（差异 > 5%）。
   **根因**：模型中含 Dropout 且保存时未显式设 `training=False`，导致 SavedModel 记录的推理图中 Dropout 仍生效。
   **解决**：用 `@tf.function` 包装推理函数，在函数内确保 `training=False`；或在保存前调用 `model.eval()`。

3. **坑**：`model.save()` 报 `AssertionError: Tried to export a function which references an untracked resource`。
   **根因**：模型中有 `tf.Variable` 或 `tf.lookup.StaticHashTable` 等未注册为模型属性的资源。
   **解决**：这些资源需要在 `__init__` 中通过 `self.resource = ...` 注册为模型属性，或在 `build()` 方法中创建。

### 4.5 思考题

1. 你的公司需要在 TensorFlow Serving、TensorFlow Lite（Android 端）和 TensorFlow.js（浏览器端）三个平台部署同一个模型。请设计一套"一模型三部署"的方案——说明在哪个阶段应保存为什么格式，以及如何验证三个平台的推理一致性。

2. 下面是一个自定义模型——它在 `build()` 中创建了一个查找表（`tf.lookup.StaticHashTable`）。这段代码在用 `model.save()` 导出 SavedModel 时会报错。请找出问题并修复：
   ```python
   class MyModel(keras.Model):
       def build(self, input_shape):
           self.lookup_table = tf.lookup.StaticHashTable(
               tf.lookup.KeyValueTensorInitializer(keys=[1,2,3], values=[10,20,30]),
               default_value=-1)
   ```

### 4.6 推广计划提示

- **新人开发**：本地开发全程用 `.keras` 格式，避免 H5 的兼容性问题；部署时转换成 SavedModel
- **平台工程师**：制定团队 SavedModel 规范——必须含 `serving_default` signature，输入输出名称和 shape 必须显式声明
- **测试工程师**：编写推理一致性测试——训练环境 `.predict()` 的结果 vs SavedModel 推理的结果，误差应 < 1e-5（浮点精度）
