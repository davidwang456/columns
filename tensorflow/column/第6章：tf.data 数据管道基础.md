# 第6章：tf.data 数据管道基础

## 1. 项目背景

算法工程师阿豪接到一个商品图片分类任务：公司有 20 万张商品图片，分为 50 个品类，每张图都是 224×224 的 RGB 图像。阿豪一开始直接把所有图片用 `np.load` 或 `PIL.Image` 读进内存——结果程序跑了不到 10 秒，32GB 内存瞬间飙红，操作系统直接把 Python 进程 kill 了。

阿豪赶紧换成"一次只读一个 batch"的策略，手动写了个 Python generator，用 `yield` 逐批返回。训练跑起来了，但速度奇慢——他发现每训练一秒，GPU 利用率只有 15%，剩下的 85% 都在等 CPU 读图、解码、resize。他看了 nvidia-smi，GPU 像是个"饿汉"——喂一口吃一口，吃完就在那干等着下一勺。

更糟糕的是，手动 generator 也有 bug：shuffle（打乱数据顺序）时，他用 `random.shuffle` 把整个文件列表丢进内存——文件列表不大，但 epoch 结束时重新 shuffle，又导致数据加载线程卡顿，训练曲线出现周期性的"锯齿"（每个 epoch 结束时 loss 猛升一下）。

**痛点放大**：手动管理数据加载面临五大问题——(1) 内存爆表；(2) GPU 利用率低（数据成为瓶颈）；(3) shuffle/重复/分批次逻辑写错导致数据泄漏；(4) 数据增强（翻转、裁剪等）与加载耦合，难以调试；(5) 多线程/进程并行读取需要自己管理锁和队列。TensorFlow 的 `tf.data` API 专门解决这些问题，通过声明式管道定义数据流，并自动优化并行度。

## 2. 项目设计

**小胖**（抱着一个巨大的文件夹，里面塞满了打印出来的照片）：我不理解！为啥要把 20 万张图搞这么复杂？直接全部 `imread` 读进来，塞到 `numpy` 数组里，模型训练不就完了？我现在 20 万张照片全打出来了，一张张翻着看，想找哪张就抽哪张——这不也挺好？

**大师**（看着小胖手里的纸堆哭笑不得）：好，那你试试把这摞纸全部摊开在地上，同时找 64 张"猫在睡觉"的照片。而且我要求你找完一批马上交给我，我来"处理"（代表 GPU 训练），处理完你再找下一批。注意——你找的这 64 张不能是连续的（得打乱），每找完一轮还得重新洗牌。

**小胖**（低头看着地上一片狼藉）：……我光摊开就花了 10 分钟。

**大师**：这就是 `tf.data` 要解决的问题。它做了三件事：
1. **惰性加载**：不一次性把 20 万张图全读进内存，而是"我训练到 batch N 时才加载 batch N 的数据"
2. **流水线并行**：CPU 在准备 batch N+1 的数据时，GPU 在训练 batch N——两边同时干活，谁也甭等谁
3. **声明式变换**：打乱、批处理、数据增强这些操作，你只需要声明你要什么，不用管"怎么多线程安全地 shuffle"

**技术映射**：`tf.data.Dataset` 是一个惰性迭代器，支持声明式管道（map/batch/shuffle/prefetch），内置多线程并行和自动预取，解决数据供给瓶颈。

**小白**（眉头紧锁）：你说的惰性加载我懂，但 `.map()`、`.batch()`、`.shuffle()`、`.prefetch()` 这几个操作的执行顺序有关系吗？我把 shuffle 放 batch 前面和后面，结果一样吗？

**大师**（拿出白板）：这个问得好。操作顺序非常关键。看这个：

```
# 正确顺序（打乱在批处理之前）：
dataset = file_list_dataset
    .shuffle(buffer_size=10000)   # 第1步：打乱文件名
    .map(decode_image)             # 第2步：每张图解码
    .batch(64)                     # 第3步：拼成 64 张一批
    .prefetch(tf.data.AUTOTUNE)   # 第4步：后台预先准备下一批

# 错误顺序（打乱在批处理之后）：
dataset = file_list_dataset
    .map(decode_image)
    .batch(64)                     # 先拼成 (64, 224, 224, 3) 的 batch
    .shuffle(buffer_size=100)      # 打乱的是 batch 的顺序！每个 batch 内部的图还是原来的顺序
```

**大师**：原则就是——**shuffle 必须发生在 batch 之前**，否则你打乱的只是 batch 之间的顺序，每个 batch 里还是固定的 64 张图。模型每个 epoch 看到的组合还是有限的。

**技术映射**：`shuffle` 打乱的是"元素级别"顺序——如果在 `batch` 之前，打乱的是单张图片；在 `batch` 之后，打乱的是整个 batch，batch 内部顺序不变。

**小胖**（挠头）：那 `prefetch` 和 `AUTOTUNE` 又是啥？听起来像汽车的自适应巡航。

**大师**：你这个比喻又对了！`prefetch` 就是说"我训练的时候，你别闲着，提前帮我把下一批数据准备好"。相当于自助餐——你吃着一盘，服务员已经端着下一盘在你旁边等着了。`AUTOTUNE` 就是让 TensorFlow 自己决定"服务员站几个、提前准备几盘"——它根据你的 CPU 核数、内存大小、数据加载速度自动调最优值。

```python
dataset = dataset.prefetch(tf.data.AUTOTUNE)
# 相当于告诉 TensorFlow："预取多少你看着办，最优就行"
```

**小白**：那 `from_tensor_slices`、`from_generator`、`TFRecordDataset` 这几种读取方式有什么区别？

**大师**：

| 方式 | 适合场景 | 优缺点 |
|------|----------|--------|
| `from_tensor_slices` | 数据小，能全放进内存（NumPy/Pandas） | 最简单，但内存受限 |
| `from_generator` | 数据来源是自定义 Python 生成器 | 灵活但有 GIL 限制，多线程效果打折扣 |
| `TFRecordDataset` | 大规模数据，存储在磁盘/远程存储 | 最快（C++ 原生读取），支持 shuffle/lookup，但需要预处理成 TFRecord 格式 |

**大师**：小数据用 `from_tensor_slices`，大到放不进内存用 `TFRecordDataset`，特殊需求（比如数据来自数据库查询）用 `from_generator`。一般项目越早切到 TFRecord，后面越省事。

**技术映射**：`from_tensor_slices` 将内存中数据包装为 Dataset；`from_generator` 包装 Python generator（受 GIL 限制）；`TFRecordDataset` 以 C++ 级别读取 TFRecord 二进制文件，性能最优。

## 3. 项目实战

### 3.1 环境准备

```bash
pip install tensorflow==2.16.1 matplotlib==3.8.4 Pillow==10.3.0
```

### 3.2 分步实现

**步骤一：从内存数据创建 Dataset 并理解基础操作**

目标：掌握 `from_tensor_slices`、`batch`、`shuffle`、`take` 的基本用法。

```python
import tensorflow as tf
import numpy as np

# 模拟 1000 张 32×32 的灰度图
np.random.seed(42)
images = np.random.rand(1000, 32, 32, 1).astype(np.float32)
labels = np.random.randint(0, 50, size=(1000,))

# 创建 Dataset
ds = tf.data.Dataset.from_tensor_slices((images, labels))

# 管道操作链
ds = ds.shuffle(buffer_size=500)     # 缓冲区内打乱
ds = ds.batch(64)                    # 每 64 张为一批
ds = ds.prefetch(tf.data.AUTOTUNE)  # 自动预取

# 查看一个 batch
for batch_images, batch_labels in ds.take(1):
    print(f"Images shape: {batch_images.shape}")  # (64, 32, 32, 1)
    print(f"Labels shape: {batch_labels.shape}")   # (64,)
    print(f"Labels 示例: {batch_labels[:5].numpy()}")

# 检查管道结构
print(f"\nelement_spec: {ds.element_spec}")
```

运行输出：
```
Images shape: (64, 32, 32, 1)
Labels shape: (64,)
Labels 示例: [23 41  8 14 37]

element_spec: (TensorSpec(shape=(None, 32, 32, 1), dtype=tf.float32, name=None),
               TensorSpec(shape=(None,), dtype=tf.int64, name=None))
```

**步骤二：从文件目录加载图片，构建完整数据管道**

目标：模拟从磁盘读取商品图片的完整流程——读取文件、解码、预处理、增强、批处理。

```python
import tensorflow as tf
import numpy as np
import os

# 1. 在本地创建模拟的"商品图片目录"
data_root = os.path.join(os.path.dirname(__file__) or ".", "product_images")
os.makedirs(data_root, exist_ok=True)

# 生成 200 张模拟图片
img_count = 200
file_paths = []
for i in range(img_count):
    fname = f"product_{i:04d}.jpg"
    fpath = os.path.join(data_root, fname)
    # 生成随机颜色的纯色图（模拟不同商品）
    r, g, b = np.random.randint(0, 255, 3)
    img = np.full((224, 224, 3), [r, g, b], dtype=np.uint8)
    # 保存为 JPEG
    tf.io.write_file(fpath, tf.io.encode_jpeg(img))
    file_paths.append(fpath)

print(f"已生成 {len(file_paths)} 张图片到 {data_root}")

# 模拟标签：50 个品类
labels = tf.random.uniform([img_count], 0, 50, dtype=tf.int32)

# 2. 构建文件名 Dataset
path_ds = tf.data.Dataset.from_tensor_slices(file_paths)

# 3. 图片解码与预处理函数
def load_and_preprocess(path, label=None):
    # 读取 JPEG 文件
    image = tf.io.read_file(path)
    image = tf.io.decode_jpeg(image, channels=3)
    # 缩放到模型输入尺寸
    image = tf.image.resize(image, [224, 224])
    # 归一化到 [0, 1]
    image = tf.cast(image, tf.float32) / 255.0
    return image, label

# 4. 数据增强函数（训练专用）
def augment(image, label):
    # 随机水平翻转
    image = tf.image.random_flip_left_right(image)
    # 随机亮度调整
    image = tf.image.random_brightness(image, max_delta=0.1)
    # 随机对比度调整
    image = tf.image.random_contrast(image, lower=0.9, upper=1.1)
    return image, label

# 5. 组装完整管道
AUTOTUNE = tf.data.AUTOTUNE

# 标签 Dataset
label_ds = tf.data.Dataset.from_tensor_slices(labels)

# zip 合并文件路径和标签
train_ds = tf.data.Dataset.zip((path_ds, label_ds))
train_ds = train_ds.shuffle(buffer_size=img_count)              # 每 epoch 打乱
train_ds = train_ds.map(load_and_preprocess, num_parallel_calls=AUTOTUNE)  # 并行加载解码
train_ds = train_ds.map(augment, num_parallel_calls=AUTOTUNE)   # 并行数据增强
train_ds = train_ds.batch(32)                                    # 批处理
train_ds = train_ds.prefetch(AUTOTUNE)                           # 预取

# 6. 检查管道输出
print(f"\nDataset element_spec: {train_ds.element_spec}")
for batch_idx, (images, labels_batch) in enumerate(train_ds.take(2)):
    print(f"\nBatch {batch_idx + 1}:")
    print(f"  images shape: {images.shape}, dtype: {images.dtype}")
    print(f"  images 值范围: [{tf.reduce_min(images).numpy():.3f}, {tf.reduce_max(images).numpy():.3f}]")
    print(f"  labels: {labels_batch[:5].numpy()}")

# 7. 提取所有标签验证是否覆盖全品类
all_labels = []
for _, lbls in train_ds.take(10):  # 只取前 10 个 batch 验证
    all_labels.extend(lbls.numpy().tolist())
print(f"\n采样标签品类数: {len(set(all_labels))} (目标 50)")
```

运行输出（示例）：
```
已生成 200 张图片到 product_images

Dataset element_spec: (TensorSpec(shape=(None, 224, 224, 3), dtype=tf.float32),
                       TensorSpec(shape=(None,), dtype=tf.int32))

Batch 1:
  images shape: (32, 224, 224, 3), dtype: <dtype: 'float32'>
  images 值范围: [0.000, 1.000]
  labels: [18 42  7 29 11]

Batch 2:
  images shape: (32, 224, 224, 3), dtype: <dtype: 'float32'>
  labels: [33  6 45 14 28]

采样标签品类数: 50 (目标 50)
```

**步骤三：tf.data 管道调试技巧**

目标：掌握 `take`、`as_numpy_iterator`、`element_spec` 三个调试工具。

```python
import tensorflow as tf

# 创建一个小数据集用于调试
ds = tf.data.Dataset.range(100)
ds = ds.map(lambda x: x * x)        # 每个元素平方
ds = ds.filter(lambda x: x > 500)   # 只保留 >500 的
ds = ds.shuffle(buffer_size=20)
ds = ds.batch(8)

# 调试技巧 1: take(n) 取前 n 个 batch 检查
print("=== take(2) 查看前 2 个 batch ===")
for batch in ds.take(2):
    print(batch.numpy())

# 调试技巧 2: as_numpy_iterator 逐个查看元素
print("\n=== as_numpy_iterator (unbatch) ===")
ds_unbatched = ds.unbatch()
it = ds_unbatched.as_numpy_iterator()
for i in range(5):
    print(next(it))

# 调试技巧 3: element_spec 查看张量规格
print(f"\nelement_spec: {ds.element_spec}")

# 调试技巧 4: 检查管道是否死循环
import time
start = time.time()
count = 0
for _ in ds:
    count += 1
    if time.time() - start > 2.0:  # 2 秒超时
        break
print(f"2 秒内产出 {count} 个 batch")
```

### 3.3 坑点与常见问题

| 问题 | 现象 | 原因与解决 |
|------|------|------------|
| `map` 中函数执行两次 | 打印两次 "loading..." | `map` 在非 eager 模式下 C++ 层面也会 trace 一次。加上 `tf.py_function` 或用 `@tf.function` |
| shuffle buffer_size 太小 | 训练精度波动大，不同 epoch 差异 >3% | `buffer_size` 决定了 shuffle 的随机程度，太小导致数据近似不随机。建议设为数据集大小或至少 10000 |
| `repeat` 放在 `shuffle` 之后 | 每个 epoch 的数据顺序完全一样 | `dataset.repeat().shuffle()` 是正确的：先无限重复，再打乱。`shuffle().repeat()` 会看到重复模式 |
| `.numpy()` 在 `map` 中报错 | `AttributeError: 'Tensor' object has no attribute 'numpy'` | `.numpy()` 只在 eager 模式可用，`map` 默认运行在图模式。改用 `@tf.function` 不可用时用 `tf.py_function` |

## 4. 项目总结

### 4.1 优点与缺点

| 方面 | tf.data | 手动 Generator |
|------|---------|---------------|
| 代码简洁度 | 声明式管道，5 行搞定 | 需要手写 yield/队列/线程管理 |
| GPU 利用率 | prefetch + parallel_map 可逼近 100% | 通常 30-50%，需要手动优化 |
| 多线程安全 | 框架保证，无锁编程 | 需要自己处理 GIL 和竞态 |
| 数据一致性 | TFRecord 保证 schema | 依赖开发者契约 |
| 调试难度 | 图模式下 print 不生效，需要 `tf.print` | Python 原生调试 |

### 4.2 适用场景

1. **图片/视频数据集**：`tf.io.decode_jpeg` + `tf.image.resize` 的组合是最常见的图像管道
2. **大规模文本数据**：`TextLineDataset` 逐行读取 + `map` 分词
3. **TFRecord 格式数据**：团队间数据交换的标准二进制格式
4. **分布式训练**：`tf.data` 与 `tf.distribute.Strategy` 天然集成

**不适用场景**：
1. 数据量极小（<1000 条）——直接用 NumPy 数组塞 `model.fit` 更简单
2. 数据预处理逻辑极端复杂且图模式难以表达——用 `from_generator` 加 `tf.py_function` 过渡

### 4.3 注意事项

- **操作顺序铁律**：`shuffle → repeat → map → batch → prefetch`（shuffle 在 batch 前，prefetch 在最后）
- **`num_parallel_calls=AUTOTUNE`** 是性价比最高的性能开关，默认加在所有 `map` 上
- **`cache()` 的位置**：放在 `map`（解码）之后、`shuffle` 之前，避免每 epoch 重复解码。但 `cache` 会占大量内存/磁盘，仅数据量 < 内存时使用
- **标签拼接方式的坑**：如果用 `Dataset.zip` 分别创建 feature 和 label 的 dataset，确保两者的元素一一对应

### 4.4 常见踩坑经验

1. **坑**：训练 3 小时后突然 OOM，但前 2 小时内存一直正常。
   **根因**：`ds = ds.cache()` 放在 `shuffle` 后面，每 epoch 把 shuffle 后的乱序数据全存磁盘，磁盘满了。
   **解决**：`cache` 只能放在 `map` 之后、`shuffle` 之前，缓存的是解码后的有序数据，shuffle 的随机索引在小内存中完成。

2. **坑**：`dataset.repeat(2)` 训练 2 个 epoch，但第二个 epoch 的 loss 与第一个一模一样。
   **根因**：`repeat` 放在 `shuffle` 之前，第二个 epoch 的 shuffle 和第一个完全相同（因为在 `repeat` 之前就已经 shuffle 完了）。
   **解决**：`shuffle` 在 `repeat` 之后（`ds.repeat(count).shuffle(buffer_size)`），这样每个 repeat 单元内部都是重新 shuffle 的。

3. **坑**：多 GPU 训练时每张卡拿到的数据完全一样（不是随机分片），模型收敛异常。
   **根因**：`model.fit(dataset)` 时未设置 `tf.distribute` 的 `experimental_distribute_dataset_from_function`，导致每张 GPU 拿到的是 dataset 的相同 copy。
   **解决**：使用 `strategy.distribute_datasets_from_function` 确保每张 GPU 从 dataset 的不同位置取数据。

### 4.5 思考题

1. 以下两种 pipeline 写法会产生不同的效果，请分析原因：
   ```python
   # 写法 A
   ds = ds.shuffle(1000).batch(32).prefetch(1)
   # 写法 B
   ds = ds.batch(32).shuffle(1000).prefetch(1)
   ```

2. 如果你有一个 500GB 的 TFRecord 数据集存放在远程对象存储（如 S3/MinIO），你会如何设计数据管道以保证训练速度？请考虑缓存策略、并行读取数量、prefetch buffer 大小。

### 4.6 推广计划提示

- **新人开发**：先从 `from_tensor_slices` 掌握管道概念，再逐步过渡到 `TFRecordDataset`
- **算法工程师**：数据管道是模型训练的第一道关卡，建议将管道代码独立成 `data_pipeline.py`，与模型代码分离
- **测试工程师**：利用 `element_spec` 编写管道输出规格的单元测试——shape 和 dtype 是否符合预期
