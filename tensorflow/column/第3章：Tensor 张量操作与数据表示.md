# 第3章：Tensor 张量操作与数据表示

## 1. 项目背景

数据分析师小周在一家生鲜电商平台工作，运营团队提了一个需求："根据用户的历史订单记录，预测用户下一次会买什么品类，我们好提前备货和推送优惠券。"小周手上有一份数据——100 万条订单记录，每条记录包含：用户 ID、订单时间、商品品类编码、商品价格、购买数量、是否使用优惠券、配送距离（km）、订单金额。

小周打算用 TensorFlow 训练一个简单的协同过滤 + 多层感知机（MLP）模型。他把数据用 Pandas 加载进来，准备塞进模型里训练，结果处处碰壁：

- Pandas DataFrame 的 `dtype` 是 `int64`、`float64`，但 TensorFlow 默认用 `float32`，类型不匹配直接报错
- 用户 ID 是 "U000123" 这种字符串格式，不能直接转张量
- 品类编码有 56 个，需要 one-hot 还是 embedding？张量形状怎么设计？
- 还有近 2% 的订单配送距离是 -1（表示"未知"），得填充为 0 或均值

**痛点放大**：真实数据到模型输入之间存在多个"格式鸿沟"——数据类型、形状、编码方式、缺失值处理。不理解 Tensor 的 dtype、shape、broadcasting、维度变换等操作，就无法高效地把业务数据转化为模型能消化的"饲料"。

## 2. 项目设计

**小胖**（捧着一碗麻辣烫，热气腾腾）：张量张量，我越听越懵。NumPy 不也有数组嘛，`np.array([1,2,3])` 不就结了？干嘛非要 TensorFlow 再搞一套 Tensor 出来？

**大师**（把麻辣烫推远了一点）：你这一碗麻辣烫，用 NumPy 描述就是 `[粉丝, 豆皮, 牛肉, 汤底]`——一个一维列表。但 TensorFlow 要的是：粉丝多少克、豆皮几片、牛肉几块、汤底多少毫升、加了什么料、每个料的温度曲线、烫了多少秒……你说 NumPy 这条一维数组够用吗？

**小胖**（低头看看碗里）：那确实不够……但你的意思是一个 Tensor 里能放好多维度的信息？

**大师**：对。Tensor 就是 NumPy 数组的"高级亲戚"。它们的核心区别就三个：
1. **GPU 加速**：NumPy 只在 CPU 上跑，Tensor 可以无缝切到 GPU 上算
2. **自动微分**：Tensor 记录了它参与的计算历史，GradientTape 可以"倒带"求导
3. **计算图优化**：TensorFlow 可以把多个 Tensor 操作融合成一个高效的 Kernel 执行

**技术映射**：Tensor 可以看作 NumPy ndarray 的超集——相同的内存布局和操作语义，但多了 GPU 支持、自动微分链路记录和图级别的算子融合优化。

**小白**（在草稿纸上画了三个方框）：那你说的 shape、rank、dtype 又是啥？我老是搞混。

**大师**（拿过小白的笔）：来，直接画图最清楚：

```
标量 (rank=0, shape=()):
  [ 42 ]

向量 (rank=1, shape=(4,)):
  [ 10  25  8  99 ]

矩阵 (rank=2, shape=(3, 4)):
  ┌                    ┐
  │ 1  2  3  4 │
  │ 5  6  7  8 │
  │ 9  10 11 12 │
  └                    ┘

三维张量 (rank=3, shape=(2, 3, 4)):
  一个装了两张 3×4 矩阵的"文件夹"
```

**大师**：rank 就是"坐标轴的数量"，shape 告诉你每个轴上有多少个元素。好比快递包裹——rank 是你说"这包裹有几层包装"，shape 是说"外层多大、中层多大、内层多大"。

**技术映射**：rank 是张量的维度数（`.ndim`），shape 是每个维度的长度元组（`.shape`），dtype 是每个元素的数据类型（`.dtype`）。

**小白**：那 broadcasting 是什么意思？我经常看到写 `a + 1` 把一个数加到一个矩阵上，这不就维度对不上了吗？

**大师**（拿起桌上的一个咖啡托盘和几个杯子）：好，broadcasting 就是"自动扩展匹配"。看这个：假设这个托盘是一张 3×4 的矩阵，每个位置放了一个咖啡杯（开销）。老板说"下个月每个杯子的价格涨 1 块"，你不用把 3×4 的矩阵每个位置都 +1——你只需要一个 `1`（标量），TensorFlow 会自动帮你"复制粘贴"到 3×4 的每个位置。

**小胖**：这就像食堂加辣椒——厨师大勺一撒，每个菜都均匀沾上，不用逐个菜盆去加？

**大师**：精准！但 broadcasting 有规则：从最后一个维度往前对齐，维度相等或者其中一个为 1 才兼容。比如 (3, 4) + (4,) 可以，因为最后一维都是 4；(3, 4) + (3, 1) 也可以，因为 (3, 1) 的 1 会扩展到 4。但 (3, 4) + (2, 4) 就不行——第一维 3 ≠ 2，且都不是 1。

**技术映射**：broadcasting 是 NumPy/TensorFlow 的核心优化特性，避免显式复制数组以节省内存，但需要理解从后往前对齐的兼容规则，否则会写出意料之外的形状。

**小白**：那 tf.constant 和 tf.Variable 到底什么时候用哪个？我上次用了 constant 存权重，梯度全是 None。

**大师**：constant 是"定值"——比如数学里的 π=3.14159...，你不会去"训练 π 得更准"吧？模型的配置常量用它。Variable 是"可训练的量"——权重、偏置，这些每次反向传播都要更新的。Variable 默认会被 GradientTape 监视，constant 不会（除非显式 `tape.watch()`）。

**小白**（记笔记）：简单说就是：常量用 constant，参数用 Variable？

**大师**：对。再加一条：中间计算结果，比如 `hidden = tf.nn.relu(tf.matmul(x, W) + b)` 里的 hidden，TensorFlow 自动生成的就是普通的 Tensor（既非 constant 也非 Variable），它参与前向但不被追踪。

**技术映射**：
- `tf.constant` → 不可变，不被追踪梯度（除非显式 watch），用于模型超参数或固定输入
- `tf.Variable` → 可原地修改，自动被 GradientTape 追踪，用于模型权重
- 中间 Tensor → 由 Operation 自动产生，参与前向计算，tape 内被临时保留

## 3. 项目实战

### 3.1 环境准备

```bash
pip install tensorflow==2.16.1 numpy==1.26.4 pandas==2.0.3
```

本章继续使用第 2 章的 `tf_mnist_env` 虚拟环境，追加安装 pandas。

### 3.2 分步实现

**步骤一：从零创建和操作张量**

目标：掌握常用来创建和变换张量的 API。

```python
import tensorflow as tf
import numpy as np

# === 创建张量的 5 种方式 ===
t1 = tf.constant([1, 2, 3, 4], dtype=tf.int32)          # 从 Python list
t2 = tf.zeros([2, 3], dtype=tf.float32)                 # 全 0 张量
t3 = tf.ones([2, 3])                                    # 全 1 张量
t4 = tf.random.normal([3, 3], mean=0.0, stddev=1.0)     # 正态分布随机
t5 = tf.convert_to_tensor(np.array([[1., 2.], [3., 4.]]), dtype=tf.float32)  # 从 NumPy 转换

for i, t in enumerate([t1, t2, t3, t5], 1):
    print(f"t{i}: shape={t.shape}, dtype={t.dtype}, rank={t.ndim}")
```

运行输出：
```
t1: shape=(4,), dtype=<dtype: 'int32'>, rank=1
t2: shape=(2, 3), dtype=<dtype: 'float32'>, rank=2
t3: shape=(2, 3), dtype=<dtype: 'float32'>, rank=2
t5: shape=(2, 2), dtype=<dtype: 'float32'>, rank=2
```

**步骤二：维度变换与操作**

目标：理解 reshape、transpose、concat、gather 的使用场景。

```python
# 原始数据: 3 个用户 × 4 个特征
features = tf.constant([
    [25, 180, 3, 0],   # 用户1: 年龄 25, 身高 180, 订单数 3, 非 VIP
    [30, 165, 8, 1],   # 用户2
    [22, 175, 1, 0],   # 用户3
], dtype=tf.float32)

# reshape: 展平 (3,4) → (12,)
flat = tf.reshape(features, [-1])   # -1 表示自动推导
print("展平:", flat.numpy())

# transpose: 转置 (3,4) → (4,3)
transposed = tf.transpose(features)
print("转置 shape:", transposed.shape)  # (4, 3)

# concat: 拼接两个张量
extra_user = tf.constant([[40, 170, 12, 1]], dtype=tf.float32)   # (1, 4)
all_users = tf.concat([features, extra_user], axis=0)             # 按行拼接
print("拼接后 shape:", all_users.shape)  # (4, 4)

# gather: 选取指定索引的行
indices = [0, 2]
selected = tf.gather(features, indices)
print("选取第0和2行:\n", selected.numpy())

# stack: 沿新轴堆叠
user_day1 = tf.constant([1, 2, 3], dtype=tf.float32)
user_day2 = tf.constant([4, 5, 6], dtype=tf.float32)
stacked = tf.stack([user_day1, user_day2], axis=0)
print("堆叠后 shape:", stacked.shape)  # (2, 3)
print("堆叠结果:\n", stacked.numpy())
```

**步骤三：broadcasting 与规约操作**

目标：理解 broadcasting 规则和 reduce 系列操作。

```python
# broadcasting 示例
matrix = tf.ones([3, 4], dtype=tf.float32)           # (3, 4)
row_bias = tf.constant([1.0, 2.0, 3.0, 4.0])         # (4,) → 自动 broadcast 到 (3, 4)
result = matrix + row_bias
print("broadcasting 加法:\n", result.numpy())

# broadcasting 失败示例 —— 取消注释会报错
# bad_bias = tf.constant([1.0, 2.0])  # shape (2,) ≠ (4,) 且没有维度为1
# matrix + bad_bias  # InvalidArgumentError

# reduce 操作
sales = tf.constant([
    [100., 200., 150.],   # 商品A在3个门店的销量
    [300., 250., 400.],   # 商品B
    [80.,  120., 90. ],   # 商品C
])
print(f"每个商品总销量: {tf.reduce_sum(sales, axis=1).numpy()}")   # 按行求和
print(f"每个门店总销量: {tf.reduce_sum(sales, axis=0).numpy()}")   # 按列求和
print(f"全局最大值:     {tf.reduce_max(sales).numpy()}")           # 全局最大
print(f"每商品均值:     {tf.reduce_mean(sales, axis=1).numpy()}")  # 按行求均
```

**步骤四：电商订单特征转张量实战**

目标：将 Pandas DataFrame 中的订单数据转化为可供模型训练的 Tensor。

```python
import pandas as pd
import numpy as np
import tensorflow as tf

# 1. 模拟电商订单数据
np.random.seed(42)
n_samples = 1000

df = pd.DataFrame({
    "user_id":         [f"U{np.random.randint(1, 200):06d}" for _ in range(n_samples)],
    "product_category": np.random.randint(0, 56, n_samples),   # 0~55 品类编码
    "order_price":     np.random.uniform(5.0, 500.0, n_samples),
    "quantity":        np.random.randint(1, 10, n_samples),
    "use_coupon":      np.random.randint(0, 2, n_samples),     # 0/1
    "delivery_km":     np.random.uniform(0.5, 20.0, n_samples),
    "order_amount":    np.random.uniform(10.0, 1000.0, n_samples),
})

# 模拟缺失值：delivery_km 随机 2% 置为 -1
mask = np.random.random(n_samples) < 0.02
df.loc[mask, "delivery_km"] = -1.0

print(f"数据量: {len(df)}")
print(f"dtypes:\n{df.dtypes}")
print(f"delivery_km 缺失(-1)数量: {(df['delivery_km'] == -1).sum()}")

# 2. 特征工程 —— Pandas → TensorFlow 张量
# 2.1 处理缺失值
df["delivery_km"] = df["delivery_km"].clip(lower=0)           # -1 → 0
mean_dist = df[df["delivery_km"] > 0]["delivery_km"].mean()   # 计算非零均值
df["delivery_km"] = df["delivery_km"].replace(0, mean_dist)   # 0 用均值填充

# 2.2 品类 one-hot 编码
category_onehot = tf.one_hot(df["product_category"].values, depth=56, dtype=tf.float32)
print(f"品类 one-hot 张量 shape: {category_onehot.shape}")  # (1000, 56)

# 2.3 连续特征转为 float32 张量
cont_features = tf.constant(df[["order_price", "quantity", "delivery_km", "order_amount"]].values, dtype=tf.float32)
print(f"连续特征张量 shape: {cont_features.shape}")  # (1000, 4)

# 2.4 二值特征
use_coupon_tensor = tf.constant(df["use_coupon"].values.reshape(-1, 1), dtype=tf.float32)

# 3. 拼接所有特征 → 最终模型输入
inputs = tf.concat([cont_features, use_coupon_tensor, category_onehot], axis=1)
print(f"最终模型输入张量 shape: {inputs.shape}")  # (1000, 4 + 1 + 56) = (1000, 61)

# 4. 批量归一化（手动实现理解原理）
mean = tf.reduce_mean(inputs, axis=0, keepdims=True)
std = tf.math.reduce_std(inputs, axis=0, keepdims=True)
normalized = (inputs - mean) / (std + 1e-8)
print(f"归一化后 - 均值范围: [{tf.reduce_min(tf.reduce_mean(normalized, axis=0)).numpy():.6f}, "
      f"{tf.reduce_max(tf.reduce_mean(normalized, axis=0)).numpy():.6f}]")
print(f"归一化后 - 标准差范围: [{tf.reduce_min(tf.math.reduce_std(normalized, axis=0)).numpy():.4f}, "
      f"{tf.reduce_max(tf.math.reduce_std(normalized, axis=0)).numpy():.4f}]")

# 5. Tensor ↔ NumPy 互转
inputs_np = inputs.numpy()                   # Tensor → NumPy
inputs_back = tf.convert_to_tensor(inputs_np)  # NumPy → Tensor
print(f"互转验证: {tf.reduce_all(tf.equal(inputs, inputs_back)).numpy()}")
```

运行输出（部分）：
```
数据量: 1000
delivery_km 缺失(-1)数量: 23
品类 one-hot 张量 shape: (1000, 56)
连续特征张量 shape: (1000, 4)
最终模型输入张量 shape: (1000, 61)
归一化后 - 均值范围: [-0.000000, 0.000000]
归一化后 - 标准差范围: [1.0000, 1.0000]
互转验证: True
```

### 3.3 坑点与调试技巧

| 坑点 | 现象 | 解决方法 |
|------|------|----------|
| dtype 不匹配 | `InvalidArgumentError: cannot compute Mul as input #1 was expected to be a float tensor but is a int32 tensor` | 用 `tf.cast(tensor, tf.float32)` 显式转换 |
| shape 不兼容 | `InvalidArgumentError: Incompatible shapes: [32,10] vs. [64,10]` | 打印 `tensor.shape`，检查 batch 维度是否一致 |
| broadcasting 隐含错误 | 以为加的是列向量，实际加的是行向量，导致结果数值全错 | 始终 `print(x.shape, y.shape)` 确认后再做运算 |
| 缺失值直接入模 | Loss NaN、梯度爆炸 | 填充缺失值或标记 mask 后再归一化 |

**调试推荐写法**：
```python
# 每次做张量运算后加断言
def safe_concat(tensors, axis):
    shapes = [t.shape for t in tensors]
    print(f"concat shapes: {shapes}")  # 调试用
    return tf.concat(tensors, axis=axis)
```

## 4. 项目总结

### 4.1 优点与缺点

| 方面 | TensorFlow Tensor | NumPy ndarray |
|------|-------------------|---------------|
| GPU 加速 | 原生支持，`.gpu()` 或自动放置 | 不支持（需 CuPy） |
| 自动微分 | GradientTape 内自动记录链 | 不支持 |
| 静态图编译 | `@tf.function` 编译加速 | 不支持 |
| 生态兼容 | 无缝对接 Keras、tf.data、SavedModel | 需转换 |
| 原地修改 | Tensor 不支持原地修改（需用 Variable） | ndarray 支持 `arr[0]=x` |

### 4.2 适用场景

1. **结构化特征工程**：电商订单、用户画像等表格数据 → Tensor 特征矩阵
2. **高维数据处理**：图像 (H, W, C)、视频 (T, H, W, C)、点云 (N, 3) 等张量密集型任务
3. **跨设备计算**：数据在 CPU 预处理 → GPU 训练 → 结果回 CPU 评估
4. **自定义训练循环**：手写 loss、手动梯度裁剪等需要精细控制张量的场景

**不适用场景**：
1. 纯数据清洗和统计探索阶段（Pandas 操作更方便，交互性强）
2. 极大规模稀疏特征（推荐用 `tf.sparse.SparseTensor` 或专门的稀疏存储格式）

### 4.3 注意事项

- **float32 vs float64**：TensorFlow 默认用 float32，GPU 上 float64 计算效率远低于 float32，非高精度需求不用 float64
- **内存与显存**：Tensor 在 Eager 模式下不会自动释放，大量中间张量可能导致 OOM。用 `del tensor` 或函数局部作用域管理内存
- **int32 vs int64**：TensorFlow 的索引和 shape 操作默认使用 int64（Python int 映射为 int64），但 GPU 上 int64 操作效率低，推荐用 int32

### 4.4 常见踩坑经验

1. **坑**：`tf.concat` 拼接时莫名报 shape 不对，但 print 出来明明一样。
   **根因**：batch 维度在 print 时显示为 `None`（动态），实际运行时才确定。**解决**：用 `tf.shape(tensor)`（返回运行时形状）代替 `tensor.shape`（返回静态形状）。

2. **坑**：`tf.one_hot(indices, depth=56)` 报 OOM。
   **根因**：sparse 特征类别数 56 很小但样本量 1000 万，one-hot 后变成 (1000万, 56) → 数 GB 内存。**解决**：稀疏特征用 Embedding 而非 one-hot（参考第 11 章），或使用 `tf.sparse.from_dense()` 转为稀疏张量。

3. **坑**：`x = x + bias` 创建了新 Tensor 而非原地修改，循环中反复执行导致内存泄漏。
   **根因**：Tensor 不可变，每个 `+` 都 new 一个新 Tensor。**解决**：使用 `tf.Variable.assign_add()` 或在 `@tf.function` 内自动复用内存。

### 4.5 思考题

1. 下面的 broadcasting 操作会成功吗？如果成功，结果的 shape 是什么？如果失败，原因是什么？
   ```python
   a = tf.ones([5, 1, 3])
   b = tf.ones([2, 3])
   c = a + b
   ```

2. 设计一个函数 `safe_normalize(tensor, axis)` ，实现沿指定轴的 Z-score 归一化，要求能正确处理标准差为 0 的情况（避免除零），并写出测试用例。

### 4.6 推广计划提示

- **新人开发**：必须熟练掌握本章的 reshape/concat/gather/reduce 操作，后续所有章节都会用到
- **算法工程师**：重点理解 broadcasting 机制和缺失值处理，这些是特征工程中最易写错的点
- **测试工程师**：建议编写张量操作的单元测试（形状断言、值范围断言），作为数据预处理管道的质量门禁
