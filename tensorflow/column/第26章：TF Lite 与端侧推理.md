# 第26章：TF Lite 与端侧推理

## 1. 项目背景

某智能家居公司开发了一款"智能门铃"——当有人按门铃时，摄像头拍照，设备本地识别来访者是"家人""快递员"还是"陌生人"，然后推送不同通知。产品经理要求：识别必须在门铃设备上完成（不能上传云端——涉及隐私且网络延迟不可控），单次推理延迟 < 100ms，模型文件 < 5MB。

算法工程师小柯用第 10 章学到的 CNN 训练了一个人脸分类模型（SavedModel），准确率 94%。但问题来了——这个 SavedModel 的 `.pb` 文件 98MB，门铃设备的 ARM Cortex-A53 芯片跑 Python + TensorFlow 是不可能的：没 Python 运行时、内存只有 512MB、没有 GPU。

小柯找到了 TensorFlow Lite（TFLite）——一个专为移动和嵌入式设备设计的轻量级推理引擎。核心思路是：训练用 TensorFlow（Python + GPU）→ 转换为 TFLite 格式（极小、无 Python 依赖）→ 在设备上运行（Android/iOS/嵌入式 Linux/microcontroller）。

**痛点放大**：服务器端模型无法直接搬到端侧——(1) 模型文件太大（百 MB 级），移动网络下载和存储不可接受；(2) 端侧没有 Python 运行时和 TF 库，必须用 C++ 推理引擎；(3) 端侧功耗和延迟敏感，需要量化加速。TF Lite 通过模型转换 + 量化压缩 + 平台 Delegate 三个步骤解决这些问题。

## 2. 项目设计

**小胖**（拿着手机测模型）：98MB 的模型放到手机上？这不是开玩笑吗？我王者荣耀也才 10GB——但那是游戏，模型推理要跑在嵌入式门铃上，那破芯片连跑个贪吃蛇都费劲！

**大师**：你说到点子上了。TensorFlow Lite 解决的就是"减肥 + 搬家"——先把模型从 98MB"减"到 5MB，然后"搬"到一个不依赖 Python 的 C++ 运行环境上。核心手段是**量化（Quantization）**——把模型里的参数从 32 位浮点数（float32）压缩成 8 位整数（int8）。

**小胖**：float32 变 int8？那我这个 0.72348 的浮点数怎么用整数表示？

**大师**：通过一个线性映射：`float_value = scale × (int_value - zero_point)`。比如 scale=0.01, zero_point=128——那么 int=200 就代表 float = 0.01×(200-128) = 0.72。模型的每一层都有自己的一组 scale 和 zero_point，推理时用整数运算，又快又省内存。

**技术映射**：量化 = 浮点数 → 整数映射。int8 量化可将模型体积减小 4 倍（32→8 bit），推理延迟降低 2-4 倍（整数运算比浮点快），精度损失通常 < 2%。

**小白**：那三种量化方式（动态范围、整数量化、float16）有什么区别？什么时候用哪种？

**大师**：

| 量化方式 | 权重精度 | 激活值精度 | 体积缩减 | 精度损失 | 硬件要求 |
|---------|---------|-----------|---------|---------|---------|
| **动态范围量化** | int8 | float32 | ~4x | 极小 (<0.5%) | 无特殊要求 |
| **全整数量化** | int8 | int8 | ~4x | 小 (1-2%) | 需量化校准数据 |
| **float16 量化** | float16 | float16 | ~2x | 极小 | 需 GPU Delegate |

**大师**：不提供校准数据 → 用动态量化（最简单，零额外数据需求）。能提供几百张校准图 → 用整数量化（极致压缩，端侧最常用）。只在 GPU Delegate 上跑 → float16（精度无损）。

**技术映射**：动态量化只在推理时动态计算激活值的 scale（无需校准数据）；全整数量化需要校准数据预先计算激活值范围；float16 量化仅在 GPU 上可用。

**小白**：Delegate 又是什么？GPU Delegate、NNAPI Delegate 这些？

**大师**：Delegate 是 TFLite 的"硬件加速插件"。CPU 什么都能算但慢，GPU 算矩阵乘法特别快，NNAPI（Android 神经网络 API）可以利用 DSP/NPU 芯片进一步加速。TFLite 默认用 CPU 推理，你可以通过指定 Delegate 把计算派发到更快的硬件上：

```python
# CPU 推理（最兼容）
interpreter = tf.lite.Interpreter(model_path="model.tflite")

# GPU 推理（需要 Android/iOS GPU Delegate）
interpreter = tf.lite.Interpreter(
    model_path="model.tflite",
    experimental_delegates=[tflite.load_delegate('libtensorflowlite_gpu_delegate.so')]
)
```

**技术映射**：Delegate 是 TFLite 的设备抽象层，将计算节点的执行派发到 GPU/NPU/DSP 等专用硬件。不同平台支持不同 Delegate——Android 有 GPU+NNAPI，iOS 有 GPU+Core ML。

**小胖**：那端侧预处理一致性呢？我在服务器上训练时图片预处理是 `(img-0.5)/0.5`，TFLite 里也得做一样的操作？

**大师**：这就是一个大坑！服务器上你用 Python 的 `tf.image.resize` 做预处理，TFLite 端要确保完全一样的处理逻辑——包括 resize 的插值算法（bilinear vs bicubic）、归一化参数、颜色通道顺序（RGB vs BGR）。最好的做法是把预处理逻辑也写进 TFLite 模型里（用 `tf.keras.layers` 构建预处理层），这样端侧只需要投喂原始图片 bytes，预处理在模型内部完成，绝无一致性问题。

## 3. 项目实战

### 3.1 环境准备

```bash
pip install tensorflow==2.16.1 numpy==1.26.4 pillow==10.3.0
```

> TFLite 转换器已包含在 `tensorflow` 包中，无需额外安装。端侧运行需要下载 Android/iOS SDK。

### 3.2 分步实现

**步骤一：SavedModel → TFLite 转换 + 三种量化对比**

目标：将一个训练好的 CNN 模型转换为 TFLite，对比三种量化方式的体积和精度。

```python
import tensorflow as tf
from tensorflow import keras
import numpy as np
import os
import tempfile

# 1. 训练一个简单的图像分类模型
np.random.seed(42)
img_size = (96, 96)
n_classes = 5

model = keras.Sequential([
    keras.layers.Conv2D(32, 3, activation="relu", input_shape=(*img_size, 3)),
    keras.layers.MaxPooling2D(2),
    keras.layers.Conv2D(64, 3, activation="relu"),
    keras.layers.GlobalAveragePooling2D(),
    keras.layers.Dense(n_classes, activation="softmax"),
])

model.compile(optimizer="adam", loss="sparse_categorical_crossentropy", metrics=["accuracy"])

# 模拟训练数据
X_train = np.random.rand(500, *img_size, 3).astype(np.float32)
y_train = np.random.randint(0, n_classes, 500)
model.fit(X_train, y_train, epochs=3, batch_size=32, verbose=0)

# 2. 导出 SavedModel
tmpdir = tempfile.mkdtemp()
savedmodel_path = os.path.join(tmpdir, "saved_model")
model.save(savedmodel_path)
print(f"SavedModel 大小: {_get_dir_size(savedmodel_path) / 1024:.1f} KB")

# 3. 转换为 TFLite（无量化）
def _get_dir_size(path):
    return sum(os.path.getsize(os.path.join(dp, f)) for dp, _, fn in os.walk(path) for f in fn)

converter = tf.lite.TFLiteConverter.from_saved_model(savedmodel_path)
tflite_float = converter.convert()
float_path = os.path.join(tmpdir, "model_float32.tflite")
with open(float_path, "wb") as f:
    f.write(tflite_float)
print(f"TFLite (float32) 大小: {os.path.getsize(float_path) / 1024:.1f} KB")

# 4. 动态范围量化
converter_dyn = tf.lite.TFLiteConverter.from_saved_model(savedmodel_path)
converter_dyn.optimizations = [tf.lite.Optimize.DEFAULT]  # 动态量化
tflite_dynamic = converter_dyn.convert()
dynamic_path = os.path.join(tmpdir, "model_dynamic.tflite")
with open(dynamic_path, "wb") as f:
    f.write(tflite_dynamic)
print(f"TFLite (动态量化) 大小: {os.path.getsize(dynamic_path) / 1024:.1f} KB")

# 5. 全整数量化（需要代表性数据集作为校准数据）
def representative_dataset():
    for _ in range(100):
        data = np.random.rand(1, *img_size, 3).astype(np.float32)
        yield [data]

converter_int = tf.lite.TFLiteConverter.from_saved_model(savedmodel_path)
converter_int.optimizations = [tf.lite.Optimize.DEFAULT]
converter_int.representative_dataset = representative_dataset
converter_int.target_spec.supported_ops = [tf.lite.OpsSet.TFLITE_BUILTINS_INT8]
converter_int.inference_input_type = tf.uint8   # 输入也量化
converter_int.inference_output_type = tf.uint8  # 输出也量化
tflite_int = converter_int.convert()
int_path = os.path.join(tmpdir, "model_int8.tflite")
with open(int_path, "wb") as f:
    f.write(tflite_int)
print(f"TFLite (全整数量化) 大小: {os.path.getsize(int_path) / 1024:.1f} KB")

# 6. 精度对比
def evaluate_tflite(tflite_path, is_quantized=False):
    interpreter = tf.lite.Interpreter(model_path=tflite_path)
    interpreter.allocate_tensors()

    input_details = interpreter.get_input_details()[0]
    output_details = interpreter.get_output_details()[0]

    correct = 0
    total = 100

    for i in range(total):
        test_img = np.random.rand(1, *img_size, 3).astype(np.float32)
        test_label = np.random.randint(0, n_classes)

        # 量化模型需要把输入转为 uint8
        if is_quantized:
            scale, zero_point = input_details["quantization"]
            test_img_quant = (test_img / scale + zero_point).astype(np.uint8)
            interpreter.set_tensor(input_details["index"], test_img_quant)
        else:
            interpreter.set_tensor(input_details["index"], test_img)

        interpreter.invoke()
        output = interpreter.get_tensor(output_details["index"])

        if is_quantized:
            output = (output.astype(np.float32) - output_details["quantization"][1]) * \
                     output_details["quantization"][0]

        if np.argmax(output) == test_label:
            correct += 1

    return correct / total

# Keras 基准
keras_preds = model.predict(X_train[:100], verbose=0)
keras_acc = np.mean(np.argmax(keras_preds, axis=1) == y_train[:100])

print(f"\n=== 精度与体积对比 ===")
print(f"{'模型':<20} {'体积(KB)':>10} {'精度':>8}")
print("-" * 40)
print(f"{'Keras (基准)':<20} {_get_dir_size(savedmodel_path)/1024:>10.1f} {keras_acc:>8.4f}")

for name, path, quantized in [
    ("TFLite float32", float_path, False),
    ("TFLite 动态量化", dynamic_path, False),
    ("TFLite 整数量化", int_path, True),
]:
    acc = evaluate_tflite(path, quantized)
    size = os.path.getsize(path) / 1024
    print(f"{name:<20} {size:>10.1f} {acc:>8.4f}")

# 清理
import shutil
shutil.rmtree(tmpdir, ignore_errors=True)
```

**步骤二：将预处理嵌入 TFLite 模型（防止端侧不一致）**

目标：构建一个"输入 raw bytes → 输出分类结果"的端到端 TFLite 模型。

```python
import tensorflow as tf
from tensorflow import keras
import numpy as np

# 构建带预处理的完整模型
IMG_H, IMG_W, IMG_C = 96, 96, 3
N_CLASSES = 5

# 输入：JPEG 编码的 bytes（端侧最方便的输入方式）
raw_input = keras.Input(shape=(), dtype=tf.string, name="jpeg_bytes")

# 预处理层（都在图内完成，端侧无需额外代码）
x = keras.layers.Lambda(
    lambda img: tf.image.decode_jpeg(img, channels=IMG_C)
)(raw_input)
x = keras.layers.Resizing(IMG_H, IMG_W)(x)
x = keras.layers.Rescaling(1.0 / 255.0)(x)  # 归一化

# CNN Backbone
x = keras.layers.Conv2D(32, 3, activation="relu")(x)
x = keras.layers.MaxPooling2D(2)(x)
x = keras.layers.Conv2D(64, 3, activation="relu")(x)
x = keras.layers.GlobalAveragePooling2D()(x)
output = keras.layers.Dense(N_CLASSES, activation="softmax", name="probs")(x)

e2e_model = keras.Model(inputs=raw_input, outputs=output)
e2e_model.compile(optimizer="adam", loss="sparse_categorical_crossentropy")

print("端到端模型 (raw bytes → probs):")
e2e_model.summary()

# 将模型转换为 TFLite（包含预处理！）
converter = tf.lite.TFLiteConverter.from_keras_model(e2e_model)
converter.optimizations = [tf.lite.Optimize.DEFAULT]
tflite_e2e = converter.convert()

# 模拟端侧推理：输入 JPEG bytes
import io
from PIL import Image

test_img = (np.random.rand(IMG_H, IMG_W, IMG_C) * 255).astype(np.uint8)
img_pil = Image.fromarray(test_img)
buf = io.BytesIO()
img_pil.save(buf, format="JPEG")
jpeg_bytes = buf.getvalue()

# 在 TFLite 中测试（输入 raw bytes，输出 probs）
interpreter = tf.lite.Interpreter(model_content=tflite_e2e)
interpreter.allocate_tensors()
input_details = interpreter.get_input_details()[0]
output_details = interpreter.get_output_details()[0]

interpreter.set_tensor(input_details["index"], np.array([jpeg_bytes]))
interpreter.invoke()
probs = interpreter.get_tensor(output_details["index"])

print(f"\n端到端 TFLite 推理结果: {probs[0]}")
print(f"预测类别: {np.argmax(probs[0])}")
print(f"置信度: {np.max(probs[0]):.4f}")
print(f"\nTFLite 模型大小: {len(tflite_e2e) / 1024:.1f} KB")
```

**步骤三：Android/iOS 端侧集成概念代码**

目标：展示 Android (Java/Kotlin) 端如何调用 TFLite 模型。

```java
// Android 端 TFLite 推理示例（概念代码，不可直接运行）
// ========================================================

// 1. 将 model.tflite 放入 assets/ 目录
// 2. 添加依赖: implementation 'org.tensorflow:tensorflow-lite:2.16.1'

// 加载模型
Interpreter tflite = new Interpreter(loadModelFile());

// 获取输入输出信息
int inputIndex = 0;
int outputIndex = 0;
DataType inputType = tflite.getInputTensor(inputIndex).dataType();

// 准备输入（例如：摄像头帧 → Bitmap → ByteBuffer）
ByteBuffer inputBuffer = ByteBuffer.allocateDirect(4 * 96 * 96 * 3);
inputBuffer.order(ByteOrder.nativeOrder());
// 将 Bitmap 像素填充到 inputBuffer...

// 执行推理
float[][] output = new float[1][NUM_CLASSES];  // 输出概率
tflite.run(inputBuffer, output);

// 处理结果
int predictedClass = argmax(output[0]);
float confidence = output[0][predictedClass];
```

```python
# Python 端 TFLite 推理（概念代码，展示了完整的输入输出处理流程）
import numpy as np

def tflite_inference_example(tflite_path, jpeg_bytes):
    """完整的 TFLite 推理流程（模拟端侧行为）"""
    # 加载模型
    interpreter = tf.lite.Interpreter(model_path=tflite_path)
    interpreter.allocate_tensors()

    input_details = interpreter.get_input_details()
    output_details = interpreter.get_output_details()

    print("=== TFLite 模型签名 ===")
    for inp in input_details:
        print(f"  Input: {inp['name']}, shape={inp['shape']}, dtype={inp['dtype']}")
    for out in output_details:
        print(f"  Output: {out['name']}, shape={out['shape']}, dtype={out['dtype']}")

    # 设置输入
    interpreter.set_tensor(input_details[0]["index"],
                           np.array([jpeg_bytes]))

    # 推理
    interpreter.invoke()

    # 获取输出
    probs = interpreter.get_tensor(output_details[0]["index"])
    return probs
```

### 3.3 TFLite 端侧部署 Checklist

| # | 检查项 | 验证方式 |
|---|--------|----------|
| 1 | SavedModel → TFLite 转换成功 | `tf.lite.TFLiteConverter.from_saved_model()` 无报错 |
| 2 | 量化后精度损失 < 2% | 同一批测试数据，Keras vs TFLite 的 Top-1 Acc 对比 |
| 3 | TFLite 模型大小满足设备要求 | 文件 < 5MB（移动端）/ < 500KB（MCU） |
| 4 | 预处理逻辑嵌入模型 | 模型输入为 raw bytes，输出为 probs |
| 5 | 端侧推理延迟 < 目标值 | Android：用 `SystemClock.elapsedRealtime()` 测量 |
| 6 | 多平台推理一致 | Keras / TFLite-CPU / TFLite-GPU 三种输出误差 < 1e-3 |

## 4. 项目总结

### 4.1 量化方式对比

| 方式 | 体积缩减 | 延迟提升 | 精度损失 | 硬件依赖 | 适用 |
|------|---------|---------|---------|---------|------|
| **动态范围** | ~4x | 1.5-2x | < 0.5% | 无 | 快速部署、不提供校准数据 |
| **全整数 (int8)** | ~4x | 2-4x | 1-2% | 需校准数据 | 移动/嵌入式端侧首选 |
| **float16** | ~2x | 1.5x (GPU) | ≈ 0 | GPU Delegate | GPU 推理、精度敏感 |
| **无量化** | ~1x | 1x | 0 | 无 | 调试/基线对比 |

### 4.2 适用场景

1. **移动端图像分类**：TFLite + GPU Delegate，模型 3-5MB，推理 < 20ms
2. **嵌入式设备人脸识别**：TFLite + 全整数量化，模型 < 2MB
3. **IoT 传感器异常检测**：TFLite Micro + int8 量化，模型 < 100KB，MCU 运行
4. **实时视频分割/风格迁移**：TFLite + GPU/NNAPI Delegate
5. **离线语音命令识别**：TFLite + int8，端侧无需网络

**不适用场景**：
1. 模型本身就 >100MB 且精度不能妥协——考虑用 Distillation（知识蒸馏）缩小模型再量化
2. 需要频繁更新模型且用户不愿意下载大文件——考虑云端推理（TensorFlow Serving）

### 4.3 注意事项

- **整数量化必须有代表性校准数据**：校准数据应覆盖真实场景的各种输入分布（光照、角度、噪声），否则量化后的 scale/zero_point 不准确，精度损失 >5%
- **不支持的操作会 fallback 到 float**：如果模型中有 TFLite 不支持的 Op（如某些 `tf.raw_ops`），转换会失败或回退到 float32。用 `tf.lite.TFLiteConverter.allow_custom_ops=True` 允许自定义 Op
- **TFLite 不支持资源型变量**：`tf.lookup.StaticHashTable` 等资源在 TFLite 中不可用，需在转换前替换为普通 Tensor 操作
- **端侧内存管理**：`interpreter.allocateTensors()` 后不要频繁创建新的 Interpreter，复用同一个可以减少内存分配开销

### 4.4 常见踩坑经验

1. **坑**：TFLite 推理结果与 Keras 完全不同。
   **根因**：预处理不一致——Keras 用 `image/255.0` 归一化，TFLite 输入了原始 `[0,255]` 像素值。
   **解决**：将预处理嵌入 TFLite 模型（本章步骤二），端侧只输入 raw bytes。

2. **坑**：整数量化后精度下降 10%+。
   **根因**：校准数据集太小（< 50 张）或不能代表真实分布。量化器看到的激活值范围太窄，scale 计算不准。
   **解决**：至少提供 500-1000 张代表性校准样本，且覆盖各种边界情况（全黑、全白、高对比度）。

3. **坑**：Android 端 TFLite 推理 crash: `"java.lang.IllegalArgumentException: Cannot copy to a TensorFlowLite tensor ... from a Java Buffer"`。
   **根因**：`ByteBuffer` 的字节序（ByteOrder）或大小不匹配。输入张量的 shape 要求 `(1, H, W, 3)`，但 ByteBuffer 只有 `H*W*3`。
   **解决**：确保 `ByteBuffer.allocateDirect()` 的大小 = `1 × H × W × 3 × 4` bytes，且 `ByteOrder.nativeOrder()`。

### 4.5 思考题

1. 你有一个 50MB 的图像分割模型（U-Net），需要部署到手机端。模型里面有大量的 `tf.nn.conv2d` 和 `tf.image.resize` 操作。设计一个部署方案，包含：(a) 量化策略选择；(b) 如果 TFLite 不支持 `tf.image.resize`，有哪些替代方案？(c) 如何验证端侧推理与训练环境的一致性？

2. 在端侧设备上，用户可能上传任意尺寸的图片（320×200 到 4096×3072 不等），但你的模型只接受 224×224。你如何在 TFLite 模型内处理各种输入尺寸，同时确保 resize 行为与训练时完全一致？

### 4.6 推广计划提示

- **算法工程师**：训练完模型后增加一个"TFLite 导出 + 精度验证"的标准步骤，纳入模型交付 checklist
- **移动端开发**：在项目中封装一个 `TFLiteClassifier` 类，统一管理 Interpreter 的加载、预热和复用
- **测试工程师**：建立 TFLite 推理一致性测试——同一批数据分别跑 Keras predict 和 TFLite 推理，输出差异应 < 1e-3（float32）/ < 0.05（int8）
