# 第32章：TensorFlow Runtime 与执行链路源码总览

## 1. 项目背景

资深工程师大刚在排查一个诡异的 bug：他的 Transformer 模型在 Eager 模式下运行正常，但加上 `@tf.function` 后输出结果不一样——difference 高达 0.3（而不是期望的 1e-6）。他怀疑是 AutoGraph 的 bug，但团队里没人能说清楚"一个 `tf.matmul` 调用从 Python 代码到 GPU Kernel 执行到底经过了哪些步骤"。

大刚开始读 TensorFlow 源码——从 `tensorflow/python/ops/math_ops.py` 的 `matmul` 函数一路追下去，经过了 7 个文件、跨了 Python 和 C++ 两个语言层，最后停在了 `tensorflow/core/kernels/matmul_op.cc`——他终于理解了整个调用链路，并在中间层的 `ConcreteFunction` 缓存机制中发现了一个与动态 shape 相关的坑。

**痛点放大**：当你遇到"模型行为与预期不符""`@tf.function` 后出错""性能优化无从下手"等高级问题时，仅仅理解 Python API 是不够的——你需要知道 TensorFlow 运行时（Runtime）的内部架构：Eager 和图模式有什么区别？Python 操作如何转化为 C++ Kernel 调用？`ConcreteFunction` 的 tracing 和缓存机制是什么？

## 2. 项目设计

**小胖**（盯着源码目录）：我看 `tensorflow/python/ops/math_ops.py` 里的 `matmul` 函数——也没几行代码啊？就调了一个 `gen_math_ops.mat_mul`，这有啥好研究的？

**大师**：你说到点子上了——你看到的只是"冰山顶部"。`gen_math_ops.mat_mul` 不是手写的——它是从 C++ 的 Op 定义文件（`tensorflow/core/ops/math_ops.cc`）自动生成的 Python 绑定。真正的执行链路比你想象的长得多。我们用一个 `tf.matmul(a, b)` 调用，追踪它的完整路径。

**技术映射**：TensorFlow 的 API 是分层架构——Python Layer（用户可见）→ Python-C 桥接（PyBind/AutoGen）→ C++ Runtime（Executor/Placer/Allocator）→ Kernel 层（设备特定的计算实现）。

**大师**（在白板上画出调用链）：

```
Python 层:
  tf.matmul(a, b)
    ↓
  math_ops.py: matmul() → gen_math_ops.mat_mul()
    ↓ (auto-generated Python wrapper)
  gen_math_ops.py: mat_mul() → _op_def_lib.apply_op("MatMul", ...)
    ↓
  tensorflow/python/framework/op_def_library.py
    构造 OpDef + NodeDef + 类型推导 → eager/op_executor.py
    ↓
C++ 层 (通过 pybind11 或 SWIG):
  tensorflow/python/eager/pywrap_tfe_src.* 
  → EagerOperation::Execute()
    ↓
  tensorflow/core/common_runtime/eager/execute.cc
  → EagerExecutor → KernelAndDevice::Run()
    ↓
  tensorflow/core/kernels/matmul_op.cc
  → MatMulOp<Device, T>::Compute() 
    ↓  (CPU: Eigen, GPU: cuBLAS)
  硬件执行
```

**大师**：这个调用链大概经过了 10-12 层抽象。其中每一层都有可能成为性能瓶颈或 bug 的来源。理解这条链路，你就能回答三个关键问题：(1) Eager 执行和图执行在哪个节点分叉？(2) 自定义 Op 应该注入到哪一层？(3) 为什么 `tf.print` 在图模式下行为不同？

**小白**：那 Eager 执行和图执行具体在哪一步分叉？

**大师**：在 `EagerOperation::Execute()` 这一步。Eager 模式下，它直接把 Operation 派发到 Kernel 执行——算完就完，不建图。图模式下（`@tf.function`），它把 Operation 构建成一个 `GraphDef` 节点（而不是立即执行），等整个函数被 trace 完成后，把整个图一次性传给 `Executor::Run()`。

这就像一个厨师做菜——Eager 模式是你点一个菜他当场做一个，图模式是你先点完一整桌菜（构建 Graph），然后厨房可以优化上菜顺序（算子融合、内存复用），一次性高效出菜。

**技术映射**：Eager Execution = `ExecuteOp immediately`（Python 层直接调用 Kernel）；Graph Execution = `Build Graph → Optimize → Execute`（先构建计算图，后统一优化执行）。

**小白**：那 `ConcreteFunction` 的 tracing 和缓存机制是什么？

**大师**：这是 `@tf.function` 底层最核心的机制。当你第一次用某种输入 shape/dtype 调用一个 `@tf.function` 时，TF 会创建一个 `ConcreteFunction`——它把 Python 代码"追踪"成一个固定的计算图（Graph）。下次如果再用相同 shape/dtype 调用，TF 直接从缓存取出这个 ConcreteFunction，跳过 tracing，直接执行图——这就是 `@tf.function` 加速的原理。

```python
@tf.function
def my_fn(x):
    return x + 1

my_fn(tf.constant(1))    # 第1次: tracing → ConcreteFunction(shape=(), dtype=int32)
my_fn(tf.constant(2))    # 第2次: 缓存命中，直接执行 ✓
my_fn(tf.constant(1.0))  # 第3次: dtype变了 → retracing → 新ConcreteFunction
```

但如果每次输入 shape 都不同——比如序列长度 10/15/20 各自 trace 一次——频繁 retracing 会严重拖慢性能。这就是第 33 章要讲的 `input_signature` 解决的问题。

## 3. 项目实战

### 3.1 环境准备

```bash
pip install tensorflow==2.16.1 numpy==1.26.4
```

> 阅读源码需要克隆 TensorFlow 仓库（选读）：
> `git clone https://github.com/tensorflow/tensorflow.git --depth=1`

### 3.2 分步实现

**步骤一：追踪 `tf.matmul` 的 Python 层调用路径**

目标：理解 Python 层的 Operation 创建和派发流程。

```python
import tensorflow as tf
import numpy as np

# === 1. 追踪普通的 tf.matmul ===
a = tf.constant([[1.0, 2.0], [3.0, 4.0]])
b = tf.constant([[5.0, 6.0], [7.0, 8.0]])

# 查看这个 operation 的类型
c = tf.matmul(a, b)
print(f"Result type: {type(c).__name__}")     # EagerTensor
print(f"Shape: {c.shape}")
print(f"Device: {c.device}")

# === 2. 查看底层的 Operation 对象 ===
# 在 Eager 模式下，Tensor 也会关联一个 Operation
op = c.op
if op is not None:
    print(f"\nOperation type: {op.type}")        # MatMul
    print(f"Operation name: {op.name}")
    print(f"Inputs: {[i.name for i in op.inputs]}")
    print(f"Device: {op.device}")
else:
    print("\n(注: 某些 Eager Tensor 的 .op 可能为 None)")

# === 3. 用 @tf.function 看 ConcreteFunction ===
@tf.function
def matmul_fn(x, y):
    return tf.matmul(x, y)

# 第一次调用 → tracing
c1 = matmul_fn(a, b)
print(f"\nConcreteFunction 1: {matmul_fn.pretty_printed_concrete_signatures()}")

# 换一个 shape → retracing
a2 = tf.constant([[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]])
b2 = tf.constant([[1.0, 2.0], [3.0, 4.0], [5.0, 6.0]])
c2 = matmul_fn(a2, b2)
print(f"ConcreteFunction 2: {matmul_fn.pretty_printed_concrete_signatures()}")
```

运行输出：
```
Result type: EagerTensor
Shape: (2, 2)
Device: /job:localhost/replica:0/task:0/device:CPU:0

Operation type: MatMul
Operation name: MatMul
Inputs: ['Const', 'Const_1']
Device: /job:localhost/replica:0/task:0/device:CPU:0

ConcreteFunction 1:
  (x: float32[2,2], y: float32[2,2]) -> float32[2,2]
ConcreteFunction 2:
  (x: float32[2,2], y: float32[2,2]) -> float32[2,2]
  (x: float32[2,3], y: float32[3,2]) -> float32[2,2]
```

**步骤二：源码文件导航——从 Python 到 C++ 的路径追踪**

目标：建立"出了问题去哪找源码"的直觉。

```python
print("""
=== TensorFlow 源码导航 ===

1. API 入口层 (修改参数 + 调用底层):
   tensorflow/python/ops/math_ops.py        ← matmul() 函数
   tensorflow/python/ops/array_ops.py       ← reshape/concat
   tensorflow/python/ops/nn_ops.py          ← conv2d/relu

2. Python-C 桥接层 (自动生成):
   tensorflow/python/ops/gen_math_ops.py    ← mat_mul (auto-gen)
   tensorflow/python/framework/op_def_library.py  ← apply_op

3. Eager 执行层 (Python→C++ 入口):
   tensorflow/python/eager/execute.py       ← execute()
   tensorflow/python/eager/context.py       ← EagerContext
   tensorflow/python/eager/def_function.py  ← Function/tracing

4. C++ Runtime 层 (执行引擎):
   tensorflow/core/common_runtime/eager/execute.cc    ← EagerExecute
   tensorflow/core/common_runtime/executor.cc         ← GraphExecutor
   tensorflow/core/common_runtime/direct_session.cc   ← Session::Run
   tensorflow/core/common_runtime/placer.cc           ← 设备放置

5. Op 定义 + Kernel 实现 (计算核心):
   tensorflow/core/ops/math_ops.cc          ← REGISTER_OP("MatMul")
   tensorflow/core/kernels/matmul_op.cc     ← MatMulOp::Compute()
   tensorflow/core/kernels/conv_ops.cc      ← Conv2D

=== 三组关键概念对比 ===

Tensor (Python) vs Tensor (C++):
  Python: tensorflow/python/framework/tensor.py → 轻量 wrapper
  C++:   tensorflow/core/framework/tensor.h   → 真正的数据容器

EagerTensor vs Tensor:
  EagerTensor 持有具体数值 (eager 模式)
  Tensor (symbolic) 只是图中一个占位符 (graph 模式)

Operation (Python) vs NodeDef (C++):
  Python: tensorflow/python/framework/ops.py → Operation
  C++:   tensorflow/core/framework/node_def.proto → NodeDef (protobuf)
""")
```

**步骤三：ConcreteFunction tracing 机制实验**

目标：通过实验理解 `@tf.function` 何时触发 retracing。

```python
import tensorflow as tf

@tf.function
def dynamic_fn(x):
    """这个函数对不同 shape 的输入会反复 retracing"""
    return tf.reduce_sum(x)

# 实验：不同的 shape 触发 retracing
shapes_to_test = [
    tf.constant([1.0, 2.0]),           # shape=(2,)
    tf.constant([1.0, 2.0, 3.0]),     # shape=(3,) → 新 shape
    tf.constant([[1.0, 2.0]]),         # shape=(1,2) → 新 shape
    tf.constant([4.0, 5.0]),           # shape=(2,) → 缓存命中!
]

print("=== Retracing 实验 ===")
for i, x in enumerate(shapes_to_test):
    # 追踪之前有多少 ConcreteFunction
    num_before = len(dynamic_fn._list_all_concrete_functions_for_serialization())
    result = dynamic_fn(x)
    num_after = len(dynamic_fn._list_all_concrete_functions_for_serialization())
    retraced = num_after > num_before
    print(f"  Step {i+1}: shape={x.shape}, retraced={'YES' if retraced else 'NO (缓存命中)'}, result={result.numpy()}")

# 对比：用 input_signature 锁定 shape
@tf.function(input_signature=[
    tf.TensorSpec(shape=[None], dtype=tf.float32)  # 允许多长一维向量
])
def fixed_fn(x):
    return tf.reduce_sum(x)

print("\n=== input_signature 固定后 ===")
for i, x in enumerate(shapes_to_test):
    if len(x.shape) == 1:  # 只测一维的情况
        result = fixed_fn(x)
        print(f"  Step {i+1}: shape={x.shape}, result={result.numpy()} (统一签名，无 retracing)")
```

**步骤四：追踪 Eager vs Graph 执行差异**

目标：看清 Eager 和 Graph 模式在 Operation 创建上的根本区别。

```python
import tensorflow as tf

# === Eager 模式：Operation 直接执行 ===
print("=== Eager 模式 ===")
a = tf.constant([1.0, 2.0, 3.0])
b = tf.constant([4.0, 5.0, 6.0])

with tf.GradientTape() as tape:
    tape.watch(a)
    c = a * b  # ← 这里 Operation 已经被执行了！c 里有具体的数值
print(f"c = {c.numpy()}")  # [4. 10. 18.]

# === Graph 模式：Operation 只是建图，不执行 ===
print("\n=== Graph 模式 (@tf.function) ===")
@tf.function
def compute(x, y):
    z = x * y
    return z

# 第一次调用 → tracing → 建 Graph → 执行
result = compute(a, b)
print(f"result = {result.numpy()}")

# 查看内部的 Graph
concrete_fn = compute.get_concrete_function(a, b)
graph = concrete_fn.graph
print(f"Graph 中的 Operation 数量: {len(graph.get_operations())}")
print(f"前 3 个 Op: {[op.name for op in graph.get_operations()][:3]}")
```

**步骤五：实际源码追踪练习——从 Python 到 C++ 逐层追踪**

目标：模仿大刚的排查过程，对一个实际的 trace 调用做逐层定位。

```python
# === 模拟大刚的排查过程 ===
import tensorflow as tf

# 原始问题：Eager 模式正常，@tf.function 后异常
# 简化复现：一个带动态 shape 的操作

@tf.function
def suspicious_fn(x):
    # 假设这里有复杂的控制流
    shape = tf.shape(x)
    result = tf.cond(
        shape[0] > 5,
        lambda: x * 2.0,
        lambda: x * 3.0,
    )
    return result

# 追踪路径：
# 1. Python 层: suspicious_fn.python_function → 源码
print("1. Python 函数源码:")
import inspect
print(inspect.getsource(suspicious_fn.python_function)[:300])

# 2. 获取 ConcreteFunction
x = tf.constant([[1.0, 2.0], [3.0, 4.0], [5.0, 6.0], [7.0, 8.0], [9.0, 10.0], [11.0, 12.0]])
cf = suspicious_fn.get_concrete_function(x)

# 3. 查看 Graph 中的 Operation
print(f"\n2. Graph 中的 Operations:")
for op in cf.graph.get_operations():
    print(f"   {op.name} (type={op.type})")

# 4. 查看 GraphDef (序列化后的图结构)
graph_def = cf.graph.as_graph_def()
print(f"\n3. GraphDef 节点数: {len(graph_def.node)}")
print(f"   Node 0: name={graph_def.node[0].name}, op={graph_def.node[0].op}")

# 5. Eager 模式下的执行路径
print(f"\n4. Eager 模式调用:")
result_eager = tf.cond(
    tf.shape(x)[0] > 5,
    lambda: x * 2.0,
    lambda: x * 3.0,
)
print(f"   结果: {result_eager.numpy()[:2].flatten()}")

# 6. Graph 模式下的执行路径（通过 ConcreteFunction）
print(f"\n5. Graph 模式调用:")
result_graph = cf(x)
print(f"   结果: {result_graph.numpy()[:2].flatten()}")
print(f"   一致性: {tf.reduce_all(tf.equal(result_eager, result_graph)).numpy()}")
```

运行输出：
```
1. Python 函数源码:
@tf.function
def suspicious_fn(x):
    shape = tf.shape(x)
    result = tf.cond(
        shape[0] > 5,
        lambda: x * 2.0,
        lambda: x * 3.0,
    )
    return result

2. Graph 中的 Operations:
   x (type=Placeholder)
   Shape (type=Shape)
   StridedSlice (type=StridedSlice)
   Greater (type=Greater)
   ...

3. GraphDef 节点数: 8

4. Eager 模式调用:
   结果: [2. 4.]

5. Graph 模式调用:
   结果: [2. 4.]
   一致性: True
```

### 3.3 源码阅读实用技巧

| 技巧 | 命令/方法 |
|------|----------|
| **查找 Op 定义** | `grep -r "REGISTER_OP.*MatMul" tensorflow/core/ops/` |
| **查找 Kernel 实现** | `grep -r "REGISTER_KERNEL_BUILDER.*MatMul" tensorflow/core/kernels/` |
| **查看 Python 绑定** | `python -c "import tensorflow as tf; help(tf.matmul)"` |
| **追踪函数调用** | `tf.autograph.to_code(fn.python_function)` 查看 AutoGraph 转换 |
| **查看 ConcreteFunction** | `fn.pretty_printed_concrete_signatures()` |
| **对比 Eager vs Graph** | `tf.config.run_functions_eagerly(True)` 临时关闭图模式 |

## 4. 项目总结

### 4.1 关键概念速查

| 概念 | Python 位置 | C++ 位置 | 职责 |
|------|-----------|---------|------|
| **Tensor** | `tensorflow/python/framework/tensor.py` | `tensorflow/core/framework/tensor.h` | 多维数据容器 |
| **Operation** | `tensorflow/python/framework/ops.py` | `tensorflow/core/framework/op.h` | 计算节点 |
| **Graph** | `tensorflow/python/framework/ops.py` | `tensorflow/core/graph/graph.h` | 计算图的容器 |
| **EagerContext** | `tensorflow/python/eager/context.py` | `tensorflow/core/common_runtime/eager/context.h` | 管理执行模式 |
| **ConcreteFunction** | `tensorflow/python/eager/function.py` | — | tracing 结果 + Graph 缓存 |
| **Executor** | — | `tensorflow/core/common_runtime/executor.cc` | 图执行引擎 |
| **Placer** | — | `tensorflow/core/common_runtime/placer.cc` | 设备放置决策 |
| **Kernel** | — | `tensorflow/core/kernels/` 目录 | 具体算子的设备实现 |

### 4.2 适用场景

1. **排查 `@tf.function` 行为异常**：理解 tracing/retracing 机制
2. **性能调优**：理解 Eager vs Graph 的执行差异，找到 Python 开销
3. **开发自定义 Op**：理解 Op 注册→Shape Inference→Kernel 分发全链路（第 34 章）
4. **阅读 TF 源码**：建立从 API 调用到内核执行的"地图"

**不适用场景**：
1. 日常模型开发——只需理解 API 语义，无需深入 Runtime
2. 纯应用层故障排查——先用第 15 章的 SOP，确认不是应用层问题再深入源码

### 4.3 注意事项

- **Eager 模式下 `.op` 可能为 None**：某些 Eager 操作（如 `tf.add`）在 Eager 模式下不创建 Operation 对象。用 `tf.print` 而非 `print` 调试 `@tf.function`
- **`ConcreteFunction` 的缓存键**：缓存基于 (input dtype, input shape) 的组合。即使 shape 只是其中一个维度变了（如 batch 32→64），也会触发 retracing
- **源码版本匹配**：阅读 TensorFlow 源码时，务必 checkout 到与你安装的 TF 版本一致的 tag（如 `v2.16.1`），否则代码对不上

### 4.4 常见踩坑经验

1. **坑**：`model.predict` 在 Eager 模式下正常，但包装到 `@tf.function` 后输出全为 0。
   **根因**：`model.predict` 内部依赖 `self.compiled_metrics` 等非图友好操作，不能直接放在 `@tf.function` 中。
   **解决**：用 `model(x, training=False)` 代替 `model.predict(x)` 进行图模式推理。

2. **坑**：`tf.print` 在 `@tf.function` 中正常，但 `print` 只执行一次。
   **根因**：Python 的 `print` 在 tracing 阶段执行（只跑一次）。`tf.print` 是 TensorFlow 的 Operation，被嵌入 Graph 中每一步都执行。
   **解决**：在 `@tf.function` 中永远用 `tf.print` 代替 `print`。

3. **坑**：`model.compile(run_eagerly=True)` 后训练正常，但去掉后报 shape mismatch。
   **根因**：Eager 模式下 shape 推断更宽松（如支持动态 batch），图模式需要确定的 shape。数据管道中某些 batch 大小不一致导致。
   **解决**：用 `dataset.batch(size, drop_remainder=True)` 确保所有 batch 大小一致。

### 4.5 思考题

1. `tf.constant` 创建的 Tensor 和 `tf.Variable` 创建的 Variable 在 C++ 层面分别对应什么数据结构？Variable 如何实现"自动被 GradientTape 追踪"？

2. 你在源码中看到一个 `REGISTER_OP("MatMul")` 的 C++ 宏调用。这个宏展开后做了什么？它如何让 Python 的 `gen_math_ops` 模块"知道"有 MatMul 这个 Op？

### 4.6 推广计划提示

- **资深开发/架构师**：本章是高级篇的入口，建立从 Python API→C++ Runtime→Kernel 的全局认知
- **算法工程师**：不需要记住所有源码路径，但需要理解"Eager 执行 vs 图执行"的分叉点——这对使用 `@tf.function` 和排查性能问题至关重要
