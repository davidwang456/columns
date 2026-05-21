# 第34章：Op Kernel 机制与自定义算子开发

## 1. 项目背景

推荐团队的算法工程师小李遇到了一个性能瓶颈——他们的双塔召回模型中有一个"特征交叉后 TopK 过滤"操作：对用户向量与候选物品向量做点积，取 Top 1000。这个操作用 TensorFlow 标准 API 实现需要 3 步：点积矩阵（O(n²) 内存）→ `tf.nn.top_k` → gather。

当候选物品池从 1 万扩展到 50 万时，完整的 50 万×128 点积矩阵已经占满了 32GB 显存，OOM 了。小李需要的是一个"近似 TopK"算法——不需要显式构建全量相似度矩阵，而是在一个动态阈值下增量式过滤。标准 TensorFlow 没有这个算子，他需要自己写一个自定义 Op。

自定义 Op 是 TensorFlow 高级能力的标志——它让你用 C++/CUDA 实现高效的新算子，然后注册到 TensorFlow 的运行时中，Python 代码可以像调用 `tf.matmul` 一样调用它。

**痛点放大**：当标准 TensorFlow API 无法高效实现你的需求时（如自定义优化器、专有硬件加速、非标准数学运算），你需要自建 Op。自定义 Op 的开发涉及 C++ 编码、Op 注册、Shape 推断、梯度注册、Python 封装、测试验证——这是一条完整的"从使用者到扩展者"的技术跃迁路径。

## 2. 项目设计

**小胖**（恐惧地看着 C++ 代码）：写 C++？我是 Python 用户啊！自定义 Op 能不能全用 Python 写？

**大师**：可以——但有限制。`tf.py_function` 可以包装 Python 逻辑为 Op，但它有两个致命缺陷：(1) 图模式下性能极差（每次调用都需要回到 Python 解释器）；(2) GPU 上不可用——Tensor 必须从 GPU 拷回 CPU 处理，再拷回去。真正的自定义 Op 必须用 C++（CPU）或 CUDA（GPU）实现。

**技术映射**：自定义 Op = C++ 算子注册 + Kernel 实现 + Shape 推断 + 梯度注册 + Python 封装。这需要理解 TF 的 Op 注册机制和运行时分发流程。

**小白**（在白板上画）：那我一步步来——自定义 Op 的开发流程具体有哪些步骤？

**大师**：标准流程 7 步走：

```
Step 1: 定义 Op 接口 (C++ REGISTER_OP)
         → 声明 Op 名称、输入输出类型、Shape 推断函数

Step 2: 实现 Kernel (C++ OpKernel 子类)
         → Compute() 方法中完成实际计算

Step 3: 注册 Kernel (REGISTER_KERNEL_BUILDER)
         → 绑定 Op 与 Kernel，指定设备 (CPU/GPU)

Step 4: 注册梯度 (REGISTER_OP_GRADIENT)
         → 告诉 TF 这个 Op 的导数怎么算

Step 5: 编译 (Bazel/CMake/g++ shared library)
         → 编译为 .so 动态库

Step 6: Python 封装 (tf.load_op_library)
         → 加载 .so，暴露为 Python 函数

Step 7: 测试验证
         → 形状测试 + 数值测试 + 梯度检验 + 性能对比
```

**技术映射**：Op 注册（REGISTER_OP）定义接口契约——Python 绑定由此自动生成。Kernel 注册（REGISTER_KERNEL_BUILDER）定义实现——同一 Op 可以有多个 Kernel（CPU/GPU/TPU）。梯度注册确保 Op 可以用于训练。

**小胖**：REGISTER_OP 和 REGISTER_KERNEL_BUILDER 有什么区别？这不是重复了吗？

**大师**：不是重复，是职责分离。`REGISTER_OP` 定义"这个 Op 叫什么、吃什么、吐什么"（接口）。`REGISTER_KERNEL_BUILDER` 定义"在什么设备上用哪个 Kernel 实现它"（实现）。同一个 Op（如 `MatMul`）可以在 CPU 上用一个 Eigen 实现的 Kernel，在 GPU 上用另一个 cuBLAS 实现的 Kernel——同一个接口，多个实现。

**小白**：那 Shape 推断函数是干什么的？

**大师**：Shape 推断让 TF 在"不实际执行计算"的情况下就知道输出的 shape——这对于图编译优化（如内存预分配、算子融合）至关重要。如果你不提供 Shape 推断，TF 就必须保守地把输出 shape 设为一个大的未知范围——可能导致后续显存分配失败。

```cpp
// Shape 推断函数示例 (C++)
REGISTER_OP("MyOp")
    .Input("input: float")
    .Output("output: float")
    .SetShapeFn([](::tensorflow::shape_inference::InferenceContext* c) {
        c->set_output(0, c->input(0));  // 输出 shape = 输入 shape
        return Status::OK();
    });
```

## 3. 项目实战

### 3.1 环境准备

```bash
pip install tensorflow==2.16.1 numpy==1.26.4
```

> C++ 编译需要：GCC 7+/MSVC 2019+、Bazel 或 CMake、TensorFlow 的头文件（可从 pip 预编译包中提取或从源码编译）。本章提供"纯 Python 自定义 Op"演示概念 + C++ 代码模板。

### 3.2 分步实现

**步骤一：纯 Python 自定义 Layer（快速原型）**

目标：用 Python 实现一个自定义 Layer，理解"自定义计算"的概念。

```python
import tensorflow as tf
from tensorflow import keras
import numpy as np

class ApproxTopK(keras.layers.Layer):
    """近似 TopK 过滤层——用 Python 实现的自定义操作（概念验证）"""

    def __init__(self, k=100, threshold=0.5, **kwargs):
        super().__init__(**kwargs)
        self.k = k
        self.threshold = threshold

    def call(self, similarities):
        """输入: (batch, num_candidates) 相似度矩阵
           输出: (batch, k) top-k indices
        """
        # 先阈值过滤（减少排序量）
        mask = tf.cast(similarities >= self.threshold, similarities.dtype)
        filtered = similarities * mask

        # 取 TopK
        _, top_indices = tf.nn.top_k(filtered, k=self.k)
        return top_indices

    def get_config(self):
        config = super().get_config()
        config.update({"k": self.k, "threshold": self.threshold})
        return config

# 测试
layer = ApproxTopK(k=5, threshold=0.5)
sims = tf.constant(np.random.rand(3, 1000).astype(np.float32))
top_indices = layer(sims)
print(f"输入 similarity: (3, 1000)")
print(f"输出 indices: {top_indices.shape}")
print(f"Top-5 indices (batch 0): {top_indices[0].numpy()}")

# 验证输出值都 ≥ threshold
top_vals = tf.gather(sims, top_indices, batch_dims=1)
print(f"Top-5 values: {top_vals[0].numpy()}")
```

**步骤二：C++ 自定义 Op 代码模板**

目标：展示 C++ 自定义 Op 的完整代码结构（编译后可用）。

```cpp
// my_custom_op.cc —— C++ 自定义 Op 完整模板
// 编译命令: 见下方

#include "tensorflow/core/framework/op.h"
#include "tensorflow/core/framework/op_kernel.h"
#include "tensorflow/core/framework/shape_inference.h"

using namespace tensorflow;

// ========== Step 1: 注册 Op 接口 ==========
REGISTER_OP("TopKFilter")
    .Input("input: float")         // 输入: 相似度矩阵
    .Attr("k: int")                // 属性: top-k 参数
    .Attr("threshold: float")      // 属性: 过滤阈值
    .Output("values: float")       // 输出: top-k 值
    .Output("indices: int32")      // 输出: top-k 索引
    .SetShapeFn([](shape_inference::InferenceContext* c) {
        // 输入 shape: (batch, num_candidates)
        shape_inference::ShapeHandle input;
        TF_RETURN_IF_ERROR(c->WithRank(c->input(0), 2, &input));

        int k;
        TF_RETURN_IF_ERROR(c->GetAttr("k", &k));

        // 输出 shape: (batch, k)
        shape_inference::DimensionHandle batch_dim = c->Dim(input, 0);
        c->set_output(0, c->MakeShape({batch_dim, k}));  // values
        c->set_output(1, c->MakeShape({batch_dim, k}));  // indices
        return Status::OK();
    });

// ========== Step 2: 实现 CPU Kernel ==========
class TopKFilterCpuOp : public OpKernel {
public:
    explicit TopKFilterCpuOp(OpKernelConstruction* context) : OpKernel(context) {
        OP_REQUIRES_OK(context, context->GetAttr("k", &k_));
        OP_REQUIRES_OK(context, context->GetAttr("threshold", &threshold_));
    }

    void Compute(OpKernelContext* context) override {
        // 获取输入
        const Tensor& input = context->input(0);
        auto input_flat = input.flat<float>();

        const int batch_size = input.dim_size(0);
        const int num_candidates = input.dim_size(1);

        // 创建输出
        Tensor* values_out = nullptr;
        Tensor* indices_out = nullptr;
        OP_REQUIRES_OK(context, context->allocate_output(
            0, TensorShape({batch_size, k_}), &values_out));
        OP_REQUIRES_OK(context, context->allocate_output(
            1, TensorShape({batch_size, k_}), &indices_out));

        auto values_flat = values_out->flat<float>();
        auto indices_flat = indices_out->flat<int32>();

        // 简化实现: 每行做阈值过滤 + top-k
        for (int b = 0; b < batch_size; ++b) {
            // 收集大于阈值的位置和值
            std::vector<std::pair<float, int>> candidates;
            for (int i = 0; i < num_candidates; ++i) {
                float val = input_flat(b * num_candidates + i);
                if (val >= threshold_) {
                    candidates.push_back({val, i});
                }
            }

            // 部分排序取 top-k
            int actual_k = std::min((int)candidates.size(), k_);
            std::partial_sort(candidates.begin(),
                             candidates.begin() + actual_k,
                             candidates.end(),
                             std::greater<std::pair<float, int>>());

            // 填充输出
            for (int j = 0; j < k_; ++j) {
                if (j < actual_k) {
                    values_flat(b * k_ + j) = candidates[j].first;
                    indices_flat(b * k_ + j) = candidates[j].second;
                } else {
                    values_flat(b * k_ + j) = 0.0f;
                    indices_flat(b * k_ + j) = 0;
                }
            }
        }
    }

private:
    int k_;
    float threshold_;
};

// ========== Step 3: 注册 Kernel ==========
REGISTER_KERNEL_BUILDER(Name("TopKFilter").Device(DEVICE_CPU), TopKFilterCpuOp);

// ========== Step 4: 注册梯度 (可选) ==========
// 如果需要在训练中使用，需要注册梯度
// REGISTER_OP_GRADIENT("TopKFilter", TopKFilterGrad);
```

**步骤三：编译与加载自定义 Op（Bazel/CMake）**

```python
# 编译命令（需要在 TensorFlow 源码树中，或使用独立的 CMake 配置）
print("""
=== 编译自定义 Op ===

方案 A: 在 TF 源码树中用 Bazel 编译 (推荐):
  1. 将 my_custom_op.cc 放入 tensorflow/core/user_ops/
  2. 在 tensorflow/core/user_ops/BUILD 中添加:
     tf_custom_op_library(
         name = "my_custom_op.so",
         srcs = ["my_custom_op.cc"],
     )
  3. 编译: bazel build //tensorflow/core/user_ops:my_custom_op.so

方案 B: 独立用 g++ 编译 (实验用):
  TF_CFLAGS=( $(python -c 'import tensorflow as tf; print(" ".join(tf.sysconfig.get_compile_flags()))') )
  TF_LFLAGS=( $(python -c 'import tensorflow as tf; print(" ".join(tf.sysconfig.get_link_flags()))') )
  g++ -std=c++17 -shared my_custom_op.cc -o my_custom_op.so \\
      -fPIC ${TF_CFLAGS[@]} ${TF_LFLAGS[@]} -O2

方案 C: Windows (MSVC):
  pip install tensorflow 应已包含头文件
  用 CMake + MSVC 编译, 链接 tensorflow_framework.lib
""")
```

**步骤四：Python 加载与测试自定义 Op**

```python
import tensorflow as tf
import numpy as np

# 加载自定义 Op
try:
    custom_ops = tf.load_op_library("./my_custom_op.so")
    print("自定义 Op 加载成功 ✅")
except Exception as e:
    print(f"Op 库未编译: {e}")
    print("(本章重点在理解 Op 注册机制, Python 层可用纯 Layer 替代)")

# 如果加载成功，测试
# result = custom_ops.top_k_filter(similarities, k=100, threshold=0.5)

# ==== 梯度验证函数（用于验证自定义 Op 的梯度注册）====
def gradient_check(op_fn, inputs, delta=1e-4, tolerance=1e-3):
    """
    数值梯度 vs 自动微分梯度验证
    op_fn: 一个接收 tf.Variable 输入的函数
    inputs: tf.Variable 的初始值列表
    """
    for var in inputs:
        with tf.GradientTape() as tape:
            output = op_fn(*inputs)
            # output 是标量才能做梯度验证
            if len(output.shape) == 0:
                loss = output
            else:
                loss = tf.reduce_sum(output)

        grads = tape.gradient(loss, inputs)

        # 数值梯度验证（简化版）
        var_np = var.numpy()
        num_grad = np.zeros_like(var_np)

        for idx in np.ndindex(var_np.shape[:1]):  # 只检查前几个元素
            old = var_np[idx]
            var_np[idx] = old + delta
            loss_plus = op_fn(*[tf.constant(v.numpy() if hasattr(v, 'numpy') else v) for v in inputs])
            var_np[idx] = old - delta
            loss_minus = op_fn(*[tf.constant(v.numpy() if hasattr(v, 'numpy') else v) for v in inputs])
            var_np[idx] = old

            if len(loss_plus.shape) > 0:
                loss_plus_val = tf.reduce_sum(loss_plus).numpy()
                loss_minus_val = tf.reduce_sum(loss_minus).numpy()
            else:
                loss_plus_val = loss_plus.numpy()
                loss_minus_val = loss_minus.numpy()
            num_grad[idx] = (loss_plus_val - loss_minus_val) / (2 * delta)

        # 比较
        max_diff = np.max(np.abs(grads[0].numpy()[:4] - num_grad[:4]))
        print(f"  梯度最大差异: {max_diff:.2e} {'✅' if max_diff < tolerance else '❌'}")

# 测试梯度验证
x = tf.Variable(tf.random.normal([3, 10]))
w = tf.Variable(tf.random.normal([10, 1]))
def simple_fn(x, w):
    return tf.matmul(x, w)
print("标准 matmul 梯度验证:")
gradient_check(simple_fn, [x, w])
```

### 3.3 自定义 Op 开发决策树

```
你的需求:
├─ 可以用标准 TF 算子组合实现
│  └─→ 用 Keras Layer / tf.py_function (简单)
├─ 需要高性能 CPU/C++ 实现
│  └─→ C++ 自定义 Op (本章)
├─ 需要 GPU/CUDA 加速
│  └─→ CUDA Kernel + REGISTER_KERNEL_BUILDER(DEVICE_GPU)
├─ 已有一个外部 C/C++ 库想嵌入 TF
│  └─→ tf.load_op_library + C++ wrapper
└─ 需要定义新的梯度规则
   └─→ REGISTER_OP_GRADIENT (训练中使用)
```

## 4. 项目总结

### 4.1 自定义 Op vs tf.py_function

| 方面 | C++ 自定义 Op | `tf.py_function` |
|------|-------------|-----------------|
| 性能 | 高（C++ 直接执行） | 低（需回到 Python 解释器） |
| GPU 支持 | ✅（CUDA Kernel） | ❌（必须拷回 CPU） |
| 图模式兼容 | ✅（原生图 Op） | ⚠️（图模式内为"黑盒"） |
| 自动微分 | ✅（注册梯度） | ⚠️（需手动实现） |
| 分发部署 | ✅（可序列化到 SavedModel） | ❌（不可跨平台分发） |
| 开发成本 | 高（C++/CUDA + 编译） | 低（纯 Python） |

### 4.2 适用场景

1. **性能瓶颈算子**：标准 API 实现 O(n²) 的你用自定义 O(n log n) 算法替代
2. **专有硬件加速**：为 TPU/FPGA 写特定的 Kernel
3. **自定义优化器**：需要非标准梯度更新规则的优化器（如 LAMB）
4. **外部库集成**：已有 C/C++ 高性能计算库，嵌入 TF

**不适用场景**：
1. 逻辑可用标准 `tf.*` API 组合实现且性能可接受——直接用 Python Layer 封装
2. 只做应用层开发、无 C++ 经验且无性能瓶颈

### 4.3 注意事项

- **Shape 推断必须提供**：否则后续层无法正确分配内存，图优化也无法识别融合机会
- **Kernel 必须是线程安全的**：TF 的 Executor 可能并发调用同一 Kernel 的 `Compute()`，注意共享状态
- **自定义 Op 的 ABI 兼容性**：`.so` 编译时的 TensorFlow 版本必须与运行时完全一致——生产环境建议用 Docker 镜像固定 ABIs
- **梯度注册的 `grad_inputs` 顺序**：必须与 Op 的输入顺序一致——第一个梯度对应第一个输入，第二个对应第二个输入

### 4.4 常见踩坑经验

1. **坑**：编译好的 `.so` 加载时报 `undefined symbol: _ZTIN10tensorflow8OpKernelE`。
   **根因**：C++ ABI 不兼容——编译时和运行时的 TensorFlow 用不同版本的 GCC 编译。
   **解决**：确保编译环境与运行时 Docker 镜像的一致（GCC version + stdlib）。

2. **坑**：Op 运行正确，但 `model.save()` 后 `load_model()` 报 `Op type not registered 'MyOp'`。
   **根因**：SavedModel 在恢复时需要加载 `.so` 中的 Op 定义——加载模型前没有先 `tf.load_op_library`。
   **解决**：在 `load_model()` 之前先执行 `tf.load_op_library("./my_op.so")`。

3. **坑**：GPU Kernel 比 CPU Kernel 还慢。
   **根因**：GPU Kernel 的 launch overhead（调用 cuBLAS/cuDNN 的调度开销）比计算本身还大——Kernel 的计算量太小（如只做了几个元素的加法），不值得调度 GPU。
   **解决**：小计算量的 Op 放在 CPU 上执行；GPU Kernel 需要足够大的数据量才能摊销调度开销。

### 4.5 思考题

1. 你要实现一个自定义的"稀疏矩阵乘法"（SpMM）Op。输入是 `SparseTensor` 和 `Dense Tensor`，输出是 `Dense Tensor`。画出这个 Op 的 `REGISTER_OP` 定义、Shape 推断函数和 CPU Kernel 的伪代码。梯度怎么处理（稀疏梯度的稠密化为步骤）？

2. TensorFlow 已经有一个 `REGISTER_OP("TopKV2")`。如果你只需要一个"只输出 top-k 的索引（不要值）"的 Op，能不能基于已有的 `TopKV2` 的 Kernel 来注册你的新 Op？这样做的优缺点是什么？

### 4.6 推广计划提示

- **架构师/资深开发**：自定义 Op 是团队能力的"护城河"——在关键路径上自建 Op 可获得数倍的性能优势
- **算法工程师**：先用 Python Layer 验证逻辑正确性，确认瓶颈后再投入 C++ 工程化
- **平台工程师**：维护一个内部的自定义 Op 库（`team_custom_ops.so`），统一编译和测试标准
