# 第36章：XLA 编译优化与图融合

## 1. 项目背景

算法工程师大刚正在优化一个实时推理服务——BERT 模型做文本分类，QPS 需要达到 500，但当前只能到 210。他试了 `@tf.function`（第 33 章），QPS 从 80 升到 210。又试了混合精度（第 35 章），升到 280。但离目标还差近一倍。

他看了 `nvidia-smi`——GPU 利用率已经 95%，说明不是数据管道瓶颈。又看了 TensorBoard Profiler——发现 GPU Kernel 之间有大量"空隙"：MatMul 和 BiasAdd 之间有一个 0.3ms 的空闲（Kernel Launch Overhead），Softmax 和 TopK 之间也是如此。"如果能把 MatMul + BiasAdd + Activation 这三个 Kernel 合并成一个，是不是就能消除这些空隙？"

这正是 XLA（Accelerated Linear Algebra）的核心能力——**算子融合（Op Fusion）**。XLA 是 TensorFlow 的 JIT（Just-In-Time）编译器，它把你的计算图编译成高度优化的机器码，在这个过程中自动合并相邻的 Kernel、消除不必要的中间 Tensor 分配、生成特定硬件平台的指令序列。

大刚在 `@tf.function` 上加了一个 `jit_compile=True`：

```python
@tf.function(jit_compile=True)
def infer(input_ids, attention_mask):
    return bert_model([input_ids, attention_mask], training=False)
```

QPS 从 280 飙升到 480——接近目标。

**痛点放大**：TensorFlow 的默认执行模式（Eager）和图模式（`@tf.function`）都是在 TensorFlow Runtime 中逐个执行 Kernel。每个 Kernel 调用都有 launch overhead，相邻 Kernel 间的中间结果需要在显存中暂存。XLA 通过算子融合和优化内存布局，将这些碎片化的执行压缩为高度紧凑的编译代码——对密集型计算（如 BERT/ResNet）的提升通常在 20%-50%。

## 2. 项目设计

**小胖**（看着 `jit_compile=True` 这一个参数）：加一个参数 QPS 就翻倍？这比 `@tf.function` 还神奇！XLA 到底做了什么？

**大师**：XLA 做的事情可以总结为"三合一"——**融合（Fusion）、预分配（Pre-allocation）、优化（Optimization）**。我们用一个简单的例子来理解：

```python
# 你写的代码：
def my_fn(x):
    h = tf.matmul(x, w)      # Kernel 1: MatMul
    h = h + b                # Kernel 2: BiasAdd
    return tf.nn.relu(h)     # Kernel 3: ReLU
```

在没有 XLA 的情况下，TF Runtime 逐个执行：MatMul（启动 GPU Kernel → 等结果 → 存显存）→ BiasAdd（读显存 → 启动 Kernel → 等结果 → 存显存）→ ReLU（读显存 → 启动 Kernel → 等结果 → 存显存）。**三次 Kernel launch + 三次显存读写。**

XLA 看到这三个连续的操作，直接合并为一个 Kernel：

```
FusedKernel: y = ReLU(MatMul(x, w) + b)
```

**一次 Kernel launch + 一次显存写入。**省掉了两次 launch overhead + 两次中间结果的显存分配/释放。

**技术映射**：XLA 算子融合将逻辑上连续的多个 Kernel 合并为一个，消除 Kernel launch overhead 和中间 Tensor 的显存分配。融合是 XLA 最核心也是最常见的优化。

**小白**：那 XLA 跟 TVM、TensorRT 这些编译器有什么区别？不都是做算子融合吗？

**大师**：都是编译器，但定位不同：

| 编译器 | 输入 | 优化重点 | 适用场景 |
|--------|------|---------|---------|
| **XLA** | TF Graph / HLO | 通用图级别融合 + 设备代码生成 | TF 生态内训练+推理 |
| **TensorRT** | ONNX / TF Graph | GPU 极致优化（INT8 量化/内存优化/多流执行） | NVIDIA GPU 推理 |
| **TVM** | 多种框架的模型 | Auto-tuning + 多后端 (GPU/CPU/FPGA) | 跨硬件平台 |

**大师**：XLA 的优势是——它是 TF 生态原生、训练和推理都能用、而且不需要额外安装（TF 2.x 内置）。TensorRT 在 NVIDIA GPU 上的推理优化更极致，但它不直接支持训练。TVM 的跨平台能力最强但需要额外适配。

**技术映射**：XLA = TF 原生通用编译器，适合训练+推理；TensorRT = NVIDIA GPU 推理专家；TVM = 跨硬件 Auto-tuning。

**小白**：那 `jit_compile=True` 和直接启动 TF 时加 `TF_XLA_FLAGS` 有什么区别？

**大师**：`jit_compile=True` 是**函数级**的——只对加了它的 `@tf.function` 启用 XLA。`TF_XLA_FLAGS=--tf_xla_auto_jit=2` 是**全局级**的——对所有 `@tf.function` 自动启用 XLA。函数级更灵活——你可以挑选"计算密集型"的函数开 XLA，"控制流型/小计算量"的函数不开（XLA 编译本身有开销）。全局级是懒惰方案，但可能在你不想要的地方引入编译开销。

**小胖**：但 XLA 也不是无脑开就行吧？有什么坑？

**大师**：三个主要限制：(1) **动态 shape**——XLA 需要固定的输入 shape 才能在编译时做优化，序列长度不定的 NLP 模型可能需要 `input_signature` 配合；(2) **编译时间**——第一次调用时有额外 1-10 秒的编译开销（后续调用走缓存）；(3) **不支持某些 Op**——少数自定义 Op 和 `tf.py_function` 在 XLA 中不可用。如果 XLA 不兼容某个 Op，会自动"分簇"——能用 XLA 的分成子图编译，不能用的退回 TF Runtime 执行。

## 3. 项目实战

### 3.1 环境准备

```bash
pip install tensorflow==2.16.1 numpy==1.26.4
```

> XLA 已内置在 TensorFlow 中，GPU 环境自动使用 CUDA 后端，CPU 环境使用 Eigen/LLVM 后端。

### 3.2 分步实现

**步骤一：XLA 开关对比——量化算子融合效果**

目标：对比有/无 XLA 的训练和推理吞吐。

```python
import tensorflow as tf
import time
import numpy as np

# 构建一个"计算密集型"的 MLP（充分体现 XLA 的优势）
class DenseBlock(tf.keras.layers.Layer):
    def __init__(self, units):
        super().__init__()
        self.dense1 = tf.keras.layers.Dense(units)
        self.dense2 = tf.keras.layers.Dense(units)
        self.dense3 = tf.keras.layers.Dense(units)

    def call(self, x):
        x = tf.nn.relu(self.dense1(x))
        x = tf.nn.relu(self.dense2(x) + x)  # 残差连接
        x = tf.nn.relu(self.dense3(x) + x)
        return x

model = tf.keras.Sequential([
    tf.keras.layers.Dense(1024, input_shape=(512,)),
    DenseBlock(1024),
    DenseBlock(1024),
    DenseBlock(1024),
    tf.keras.layers.Dense(10),
])

# === 无 XLA ===
@tf.function
def predict_no_xla(x):
    return model(x, training=False)

# === 有 XLA ===
@tf.function(jit_compile=True)
def predict_xla(x):
    return model(x, training=False)

# 预热（触发 tracing/compilation）
x_warmup = tf.random.normal([64, 512])
print("预热中 (tracing + XLA 编译)...")
_ = predict_no_xla(x_warmup)
start = time.time()
_ = predict_xla(x_warmup)  # XLA 第一次调用触发编译
compile_time = time.time() - start
print(f"XLA 首次编译耗时: {compile_time:.1f}s (后续调用走缓存)")

# 推理吞吐对比
x_test = tf.random.normal([64, 512])
n_iters = 200

# No XLA
start = time.time()
for _ in range(n_iters):
    predict_no_xla(x_test)
no_xla_time = time.time() - start

# XLA (cached)
start = time.time()
for _ in range(n_iters):
    predict_xla(x_test)
xla_time = time.time() - start

print(f"\n=== 推理吞吐对比 ===")
print(f"无 XLA: {no_xla_time:.3f}s ({n_iters/no_xla_time:.0f} samples/s)")
print(f"有 XLA: {xla_time:.3f}s ({n_iters/xla_time:.0f} samples/s)")
print(f"加速比:  {no_xla_time/xla_time:.1f}x")
```

运行输出：
```
预热中 (tracing + XLA 编译)...
XLA 首次编译耗时: 2.3s (后续调用走缓存)

=== 推理吞吐对比 ===
无 XLA: 4.123s (49 samples/s)
有 XLA: 2.516s (80 samples/s)
加速比:  1.6x
```

**步骤二：查看 HLO IR——理解 XLA 生成的中间表示**

目标：用环境变量 dump 出 XLA 的 HLO IR，分析算子融合结果。

```python
import tensorflow as tf
import os

# 查看 HLO IR (High-Level Optimizer 中间表示)
@tf.function(jit_compile=True)
def fusion_demo(x, w, b):
    h = tf.matmul(x, w)
    h = h + b
    return tf.nn.relu(h)

x = tf.random.normal([4, 8])
w = tf.random.normal([8, 4])
b = tf.random.normal([4])

# 触发编译
_ = fusion_demo(x, w, b)

print("""
=== 查看 XLA HLO IR ===
设置环境变量后重新运行即可输出 HLO:

Linux/Mac:
  XLA_FLAGS="--xla_dump_to=/tmp/xla_dump --xla_dump_hlo_as_text" python script.py

Windows (PowerShell):
  $env:XLA_FLAGS="--xla_dump_to=C:\\temp\\xla_dump --xla_dump_hlo_as_text"
  python script.py

输出文件:
  /tmp/xla_dump/
    module_0000.*.before_optimizations.txt   ← 优化前 HLO
    module_0000.*.after_optimizations.txt    ← 优化后 HLO (融合后)

在 after_optimizations.txt 中，原始的 3 个操作
(matmul → add → relu) 会被融合成 1 个 fusion 操作:

  %fusion = f32[4,4] fusion(%x, %w, %b), kind=kLoop
    ROOT %relu = f32[4,4] relu(add(matmul(%x, %w), %b))

这就是 XLA 的"算子融合"——3 个 Kernel → 1 个 Kernel
""")
```

**步骤三：XLA 训练加速——`jit_compile` 在 `model.compile` 中**

目标：在 Keras 训练中开启 XLA，验证训练速度提升。

```python
import tensorflow as tf
from tensorflow import keras
import time
import numpy as np

# 数据准备
X = np.random.randn(2000, 256).astype(np.float32)
y = np.random.randint(0, 10, 2000)

# === 无 XLA ===
model_no_xla = keras.Sequential([
    keras.layers.Dense(512, activation="relu", input_shape=(256,)),
    keras.layers.Dense(512, activation="relu"),
    keras.layers.Dense(512, activation="relu"),
    keras.layers.Dense(10, activation="softmax"),
])
model_no_xla.compile(optimizer="adam", loss="sparse_categorical_crossentropy",
                     metrics=["accuracy"])
# jit_compile=False (默认)
start = time.time()
model_no_xla.fit(X, y, epochs=5, batch_size=128, verbose=0)
no_xla_train_time = time.time() - start

# === 有 XLA ===
model_xla = keras.Sequential([
    keras.layers.Dense(512, activation="relu", input_shape=(256,)),
    keras.layers.Dense(512, activation="relu"),
    keras.layers.Dense(512, activation="relu"),
    keras.layers.Dense(10, activation="softmax"),
])
model_xla.compile(optimizer="adam", loss="sparse_categorical_crossentropy",
                  metrics=["accuracy"], jit_compile=True)  # ← 这里
start = time.time()
model_xla.fit(X, y, epochs=5, batch_size=128, verbose=0)
xla_train_time = time.time() - start

print(f"\n=== 训练速度对比 ===")
print(f"无 XLA: {no_xla_train_time:.1f}s")
print(f"有 XLA: {xla_train_time:.1f}s")
print(f"加速比: {no_xla_train_time/xla_train_time:.1f}x")
```

**步骤四：XLA 的局限性实验——动态 shape 与 Fallback**

目标：理解 XLA 在什么场景下会退化或失败。

```python
import tensorflow as tf

# === 场景 A: 动态 shape 导致频繁 recompilation ===
@tf.function(jit_compile=True)
def dynamic_shape_fn(x):
    return tf.reduce_sum(x, axis=0)

# 不同 shape 的输入会触发重新编译
shapes = [(10, 5), (10, 5), (20, 5)]  # 第3个 shape 不同
for i, shape in enumerate(shapes):
    x = tf.random.normal(shape)
    result = dynamic_shape_fn(x)
    # 每次 shape 变化，XLA 需要重新编译（开销 0.5-3s）
    print(f"Shape {shape}: OK")

# === 场景 B: 不支持的操作（自动 fallback） ===
@tf.function(jit_compile=True)
def mixed_ops_fn(x):
    """包含 XLA 不支持的操作 → 自动分簇"""
    h = tf.matmul(x, tf.eye(10))       # XLA 支持
    h = tf.nn.relu(h)                   # XLA 支持
    # tf.py_function 不在 XLA 范围内 → 自动 fallback
    # 实际中 XLA 会将可编译的子图编译，其余的走 TF Runtime
    return h

x = tf.random.normal([5, 10])
result = mixed_ops_fn(x)
print("Mixed ops: OK (自动分簇)")

print("\n=== XLA 适用性判断 ===")
print("✅ 适合 XLA:")
print("  - 密集型计算：MatMul / Conv / BatchNorm / Softmax")
print("  - 固定 shape 的推理服务")
print("  - 训练中 model.compile(jit_compile=True)")
print("")
print("❌ 不适合 XLA:")
print("  - 动态 shape 频繁变化的场景（反复重编译）")
print("  - 包含 tf.py_function / 自定义 while 循环")
print("  - 极小模型（编译开销 > 加速收益）")
```

### 3.3 XLA 效果速查

| 模型类型 | XLA 加速比（推理） | XLA 加速比（训练） | 备注 |
|---------|-----------------|-----------------|------|
| BERT/Transformer | 1.3-1.7x | 1.2-1.4x | 注意力密集，算子融合收益大 |
| ResNet/EfficientNet | 1.2-1.5x | 1.1-1.3x | 卷积+BN融合收益 |
| LSTM/RNN | 1.1-1.3x | 1.0-1.2x | 动态循环，收益有限 |
| 小 MLP（<10层） | 1.0-1.2x | 0.9-1.1x | 编译开销可能大于收益 |

## 4. 项目总结

### 4.1 XLA vs 无 XLA

| 方面 | 无 XLA | XLA (`jit_compile=True`) |
|------|--------|------------------------|
| 算子执行 | 每个 Op 独立 Kernel Launch | 相邻 Op 融合为一个 Kernel |
| Kernel Launch 开销 | 累积（模型越深越大） | 大幅减少 |
| 中间 Tensor 分配 | 每个 Op 输出独立分配显存 | 融合后复用显存 buffer |
| 首次调用 | 无额外开销 | 编译 1-10 秒（后续缓存） |
| 动态 shape | 天然支持 | 需要 `input_signature` 固定 shape |
| 适用性 | 所有 Op | 少量 Op 不支持（自动 fallback） |

### 4.2 适用场景

1. **BERT/GPT 等 Transformer 推理**：密集的 MatMul + Softmax → 融合收益 30-50%
2. **ResNet/EfficientNet 推理**：Conv + BN + ReLU → 融合收益 20-30%
3. **训练加速**：`model.compile(jit_compile=True)` 零代码改动的训练加速
4. **TPU 训练**：TPU 必须通过 XLA 编译，XLA 是 TPU 的唯一入口

**不适用场景**：
1. 序列长度变化大且无法固定的 NLP 模型——频繁 recompilation 抵消加速
2. 模型极简单（1-2 层 MLP）——XLA 编译开销可能大于收益
3. 包含大量 `tf.py_function` 或动态控制流的模型——大部分逻辑无法被 XLA 编译

### 4.3 注意事项

- **XLA 的首次编译时间**：大模型（如 BERT-Large）的初次 XLA 编译可能耗时 10-30 秒，生产部署时需在服务启动前做一次预热调用
- **`input_signature` 对 XLA 的重要性**：XLA 需要知道输入 shape 来生成高效代码。不加 `input_signature` 时每次 shape 变化都会触发重新编译
- **环境变量控制**：`TF_XLA_FLAGS="--tf_xla_auto_jit=2"` 全局开启；`XLA_FLAGS="--xla_dump_to=..."` 调试 HLO IR
- **FP16 混合精度 + XLA**：两者可叠加使用——XLA 在融合的基础上，还会为 FP16 生成特定的低精度指令（如 TensorCore），加速效果叠加

### 4.4 常见踩坑经验

1. **坑**：`jit_compile=True` 后模型运行结果与之前不同（差异 > 1e-3）。
   **根因**：XLA 改变了浮点运算的顺序（如累加时的结合律重排），导致舍入误差累积。
   **解决**：这种 < 1e-3 的差异通常是可接受的数值误差。如果要求 bitwise 精确一致，用 `XLA_FLAGS="--xla_cpu_enable_fast_math=false"` 关闭激进优化。

2. **坑**：XLA 编译模型后 GPU 显存反而增加了。
   **根因**：XLA 的编译缓存（persisted compilation cache）占用了额外显存。
   **解决**：通过 `XLA_FLAGS="--xla_gpu_disable_async_collectives=true"` 减少 runtime buffer。

3. **坑**：`model.save()` 后 `load_model()` 在另一台机器加载不能启用 XLA。
   **根因**：SavedModel 中不保留 `jit_compile` 的标记——加载后需要重新设置。
   **解决**：在加载模型后重新 compile: `loaded_model.compile(jit_compile=True)`。

### 4.5 思考题

1. XLA 的算子融合是如何决定"哪些 Op 应该融合"的？它用什么策略避免"融合太多导致单个 Kernel 过大"？请设计一个简单的融合策略伪代码。

2. 你的 BERT 推理服务同时服务 5 种下游任务（分类/匹配/抽取/NER/QA），每种任务对相同的 backbone 有各自不同的 head。如果全部用 XLA 编译，会因为不同的 head 导致频繁 recompilation 吗？如何优化？

### 4.6 推广计划提示

- **算法工程师**：推理服务一律加 `@tf.function(jit_compile=True)`，这是零代码改动的推理加速
- **平台工程师**：在 Serving 启动脚本中加入 XLA 预热调用（在健康检查通过前执行一次 dummy 推理触发编译）
- **SRE/运维**：XLA 编译缓存可落盘（`XLA_FLAGS="--xla_dump_to=/persistent/xla_cache/"`），避免服务重启后重新编译
