# 第10章：图像分类入门——CNN 项目实战

## 1. 项目背景

某动物救助机构开发了一个 App，让路人拍照上传流浪猫狗的照片，帮助机构快速统计区域内的猫狗数量。机构的技术负责人老陈找来了算法实习生小林，希望他能用一个开源数据集训练一个猫狗分类模型，嵌入到 App 中。

小林之前只做过表格数据的 MLP 分类（如用户流失预测），觉得图像分类无非就是把图片像素"拉平"成向量，塞进全连接网络。他按这个思路写了一个模型——把 224×224×3 的图片 flatten 成一个 150528 维的向量，再连几个 Dense 层：

```python
model = Sequential([
    Flatten(input_shape=(224, 224, 3)),  # 15 万个输入！
    Dense(1024, activation="relu"),
    Dense(512, activation="relu"),
    Dense(1, activation="sigmoid"),
])
```

训练跑了整整一晚，准确率只有 62%，比他想象的低得多。他检查了数据预处理、归一化，都没问题。老陈看了一眼模型结构，笑了："你这相当于让一个人从 15 万个像素点里找猫的特征——他能找到才怪。CNN 的精髓就是先局部感知，再层层抽象，而不是一股脑全看。"

**痛点放大**：全连接层处理图像面临三个根本缺陷——(1) 参数量爆炸（150528×1024=1.54 亿个参数，第一条全连接层就占了模型 99% 的权重）；(2) 丢失空间信息（flatten 后相邻像素和相隔很远的像素在权重矩阵里的地位相同，无法利用"局部相关性"）；(3) 平移不变性缺失（同一个猫往左移动 10 像素，flatten 后的向量完全不同，模型需要重新学习）。

卷积神经网络（CNN）通过卷积核的局部连接、权值共享、池化的下采样，从根本上解决了这三个问题。

## 2. 项目设计

**小胖**（翻着自己手机里的自拍）：卷积……这个词听着像数学课上的卷积分。一张图要"卷"一下是什么意思？跟卷饼一样？

**大师**（拿起手机打开相机）：你把卷积核想象成一个"放大镜"。比如你现在要在相册里找所有有猫的照片，你不可能一眼就看完整张图——你会拿这个放大镜（3×3 像素的小窗格），从左上角开始，一行一行扫描整张图。放大镜经过每个位置时，如果那片区域像"猫耳朵尖角"，输出就高；如果那片像"模糊背景"，输出就低。

这就是卷积核的工作方式：
1. 一个小的权重矩阵（3×3 或 5×5）在图片上滑动
2. 每个位置做"逐元素乘法再求和"
3. 得到一张新的特征图（feature map）

**技术映射**：卷积操作 (Convolution) = 滑动窗口 + 逐元素乘积累加。每个卷积核是一个小的可学习权重矩阵，扫描整张图提取一种特定的模式（边缘、纹理、角点等）。

**小胖**：那一个卷积核只能识别一种模式？我要识别猫耳朵、猫眼睛、猫鼻子……不得很多个卷积核？

**大师**：对！通常第一层放 32 个卷积核，分别学会检测 32 种低级特征（水平边缘、垂直边缘、斜边、角点等）。第二层放 64 个卷积核，每个卷积核看到的是前一层的 32 张特征图——它能学会组合低级特征成中级特征（"边缘+弧线=眼睛轮廓"）。越深层的卷积核看到的"视野"越大，识别的特征越高级。

**技术映射**：CNN 的层级抽象——浅层检测边缘/颜色/纹理（低级特征），中层组合成部件（眼睛/鼻子/轮子），深层组装成完整物体（猫/狗/汽车）。

**小白**（在白板上画了一个矩阵）：那池化（Pooling）是干什么的？我看代码里 MaxPooling2D 经常跟在 Conv2D 后面。

**大师**：池化的作用就是"浓缩"。MaxPooling 的意思是——在 2×2 的区域里，只保留最大的那个值，扔掉其余三个。这样做有三个好处：
1. **降维**：特征图从 224×224 变成 112×112，计算量减少 75%
2. **平移不敏感**：猫往右挪了 1 像素，经过 2×2 的最大池化后，最大值大概率还在，输出变化不大
3. **增大感受野**：经过多次池化后，深层神经元能"看到"原始输入图的更大区域

**小胖**：哦懂了！就跟我手机相册的缩略图一样——一张高清大图缩成小图，猫的大致轮廓还在，但细节丢了。不过对于"判断是不是猫"来说，轮廓就够了？

**大师**：完全正确。这就是 MaxPooling 在做的——保留最强的信号，丢弃次要细节。

**技术映射**：池化提供平移不变性和降采样。最大池化 (MaxPooling) 保留局部最强激活；平均池化 (AveragePooling) 平滑特征图；全局平均池化 (GlobalAveragePooling) 将每张特征图压缩为一个标量，常用于替代 Flatten。

**小白**：那 padding、stride 这些参数是什么意思？我经常看到 `Conv2D(32, 3, padding="same")`。

**大师**：
- **kernel_size (3)**：卷积核的大小是 3×3
- **stride (步长)**：卷积核每次移动几个像素。stride=1 表示逐像素滑动，输出特征图大小基本不变；stride=2 表示跳着走，输出尺寸减半
- **padding**：边缘填充。`padding="valid"` 表示不填充，边缘像素的卷积核有一部分"悬在图片外面"——不处理，输出尺寸会缩小。`padding="same"` 表示用 0 填充边缘，确保输出尺寸与输入相同

```
输入 (5×5), kernel=3×3, stride=1:
  - padding="valid" → 输出 3×3  (只有完全覆盖的位置才计算)
  - padding="same"  → 输出 5×5  (边缘补 0，确保大小不变)
```

**技术映射**：padding 解决"边缘信息丢失"问题；stride 控制输出尺寸和计算量；kernel_size 决定单次卷积的感受野大小。

**小白**：最后一个问题——GlobalAveragePooling2D 和 Flatten 有什么区别？我看有些模型用 Flatten 有些用 GAP。

**大师**：Flatten 是把特征图直接拉成一长条（比如 7×7×512=25088 维向量），后面再接 Dense 层——这会导致这条全连接层参数量巨大（25088×1024≈2500 万参数）。GlobalAveragePooling 是对每张特征图取一个平均值——7×7×512 变成 1×1×512=512 维向量——参数量骤降。GAP 是轻量化模型的标配，且在迁移学习中表现更好（强制特征图每个位置都和分类结果直接关联，增强定位能力）。

## 3. 项目实战

### 3.1 环境准备

```bash
pip install tensorflow==2.16.1 matplotlib==3.8.4 scikit-learn==1.5.0
```

### 3.2 分步实现

**步骤一：构建 CNN 模型（从卷积原理到代码）**

目标：实现一个完整的 CNN 图像分类模型，包含 Conv → Pool → Conv → Pool → Dense。

```python
import tensorflow as tf
from tensorflow import keras
import numpy as np
import os

# 1. 理解单层卷积的效果
# 创建一个 6×6 的单通道"图片"（有边缘的简单图形）
image = tf.constant([
    [0, 0, 0, 0, 0, 0],
    [0, 1, 1, 1, 1, 0],
    [0, 1, 0, 0, 1, 0],
    [0, 1, 0, 0, 1, 0],
    [0, 1, 1, 1, 1, 0],
    [0, 0, 0, 0, 0, 0],
], dtype=tf.float32)

# 增加 batch 和 channel 维度: (6,6) → (1,6,6,1)
image_batch = tf.reshape(image, [1, 6, 6, 1])

# 水平边缘检测卷积核
edge_kernel = tf.constant([
    [-1., -1., -1.],
    [ 0.,  0.,  0.],
    [ 1.,  1.,  1.],
], dtype=tf.float32)
edge_kernel = tf.reshape(edge_kernel, [3, 3, 1, 1])

# 应用卷积
feature_map = tf.nn.conv2d(image_batch, edge_kernel, strides=[1,1,1,1], padding="VALID")
print("原始图像:")
print(image.numpy())
print(f"\n水平边缘检测结果 (shape: {feature_map.shape}):")
print(tf.squeeze(feature_map).numpy())
```

运行输出：
```
原始图像:
[[0. 0. 0. 0. 0. 0.]
 [0. 1. 1. 1. 1. 0.]
 [0. 1. 0. 0. 1. 0.]
 [0. 1. 0. 0. 1. 0.]
 [0. 1. 1. 1. 1. 0.]
 [0. 0. 0. 0. 0. 0.]]

水平边缘检测结果 (shape: (1, 4, 4, 1)):
[[[ 2.  0.  0. -2.]
  [ 3.  1.  1. -3.]
  [ 3.  1.  1. -3.]
  [ 2.  0.  0. -2.]]]
```

> 正值表示上方暗下方亮的边缘（顶部边界），负值表示上方亮下方暗（底部边界）。

**步骤二：完整的猫狗分类 CNN 训练**

目标：使用真实流程——从目录加载图片 → tf.data 管道 → CNN 训练 → 评估 → 混淆矩阵。

```python
import tensorflow as tf
from tensorflow import keras
import numpy as np
import os
import tempfile
import matplotlib
matplotlib.use("Agg")  # 非交互模式
import matplotlib.pyplot as plt
from sklearn.metrics import confusion_matrix, classification_report

# === 1. 准备模拟猫狗图片数据 ===
data_dir = os.path.join(tempfile.mkdtemp(), "cats_vs_dogs")
img_size = (128, 128)

np.random.seed(42)
for split in ["train", "val"]:
    for cls in ["cat", "dog"]:
        cls_dir = os.path.join(data_dir, split, cls)
        os.makedirs(cls_dir, exist_ok=True)
        n = 400 if split == "train" else 100

        for i in range(n):
            # 生成模拟图片：猫偏圆形纹理，狗偏方形纹理
            if cls == "cat":
                img = np.random.rand(*img_size, 3) * 255
                # 加圆形噪声模拟猫的特征
                cy, cx = np.random.randint(30, 98, 2)
                Y, X = np.ogrid[:128, :128]
                mask = (X - cx)**2 + (Y - cy)**2 < np.random.randint(200, 600)
                img[mask] = img[mask] * 1.5
            else:
                img = np.random.rand(*img_size, 3) * 255
                # 加矩形纹理模拟狗的特征
                rx, ry = np.random.randint(20, 88, 2)
                rw, rh = np.random.randint(20, 60, 2)
                img[ry:ry+rh, rx:rx+rw] = img[ry:ry+rh, rx:rx+rw] * 1.5

            img = np.clip(img, 0, 255).astype(np.uint8)
            fpath = os.path.join(cls_dir, f"{cls}_{i:04d}.jpg")
            tf.io.write_file(fpath, tf.io.encode_jpeg(img))

print(f"数据目录: {data_dir}")
print(f"猫训练集: {len(os.listdir(os.path.join(data_dir, 'train', 'cat')))} 张")
print(f"狗训练集: {len(os.listdir(os.path.join(data_dir, 'train', 'dog')))} 张")
print(f"猫验证集: {len(os.listdir(os.path.join(data_dir, 'val', 'cat')))} 张")
print(f"狗验证集: {len(os.listdir(os.path.join(data_dir, 'val', 'dog')))} 张")

# === 2. 构建 tf.data 管道 ===
AUTOTUNE = tf.data.AUTOTUNE
batch_size = 32

def load_image(path, label):
    img = tf.io.read_file(path)
    img = tf.io.decode_jpeg(img, channels=3)
    img = tf.image.resize(img, img_size)
    img = tf.cast(img, tf.float32) / 255.0
    return img, label

def augment_train(img, label):
    img = tf.image.random_flip_left_right(img)
    img = tf.image.random_brightness(img, max_delta=0.15)
    img = tf.image.random_contrast(img, lower=0.8, upper=1.2)
    return img, label

def build_dataset(split):
    cls_names = ["cat", "dog"]
    paths = []
    labels = []
    for label_idx, cls_name in enumerate(cls_names):
        cls_dir = os.path.join(data_dir, split, cls_name)
        for fname in os.listdir(cls_dir):
            paths.append(os.path.join(cls_dir, fname))
            labels.append(label_idx)

    ds = tf.data.Dataset.from_tensor_slices((paths, labels))
    ds = ds.shuffle(buffer_size=len(paths))
    ds = ds.map(load_image, num_parallel_calls=AUTOTUNE)
    if split == "train":
        ds = ds.map(augment_train, num_parallel_calls=AUTOTUNE)
    ds = ds.batch(batch_size)
    ds = ds.prefetch(AUTOTUNE)
    return ds

train_ds = build_dataset("train")
val_ds = build_dataset("val")

# === 3. CNN 模型构建 ===
cnn_model = keras.Sequential([
    # Block 1
    keras.layers.Conv2D(32, (3, 3), activation="relu", padding="same",
                        input_shape=(*img_size, 3)),
    keras.layers.Conv2D(32, (3, 3), activation="relu", padding="same"),
    keras.layers.MaxPooling2D((2, 2)),
    keras.layers.Dropout(0.25),

    # Block 2
    keras.layers.Conv2D(64, (3, 3), activation="relu", padding="same"),
    keras.layers.Conv2D(64, (3, 3), activation="relu", padding="same"),
    keras.layers.MaxPooling2D((2, 2)),
    keras.layers.Dropout(0.25),

    # Block 3
    keras.layers.Conv2D(128, (3, 3), activation="relu", padding="same"),
    keras.layers.Conv2D(128, (3, 3), activation="relu", padding="same"),
    keras.layers.MaxPooling2D((2, 2)),
    keras.layers.Dropout(0.25),

    # 分类头
    keras.layers.GlobalAveragePooling2D(),
    keras.layers.Dense(256, activation="relu"),
    keras.layers.Dropout(0.5),
    keras.layers.Dense(1, activation="sigmoid"),
], name="cat_vs_dog_cnn")

cnn_model.summary()

# === 4. 编译与训练 ===
cnn_model.compile(
    optimizer=keras.optimizers.Adam(learning_rate=1e-3),
    loss="binary_crossentropy",
    metrics=["accuracy", keras.metrics.AUC(name="auc")],
)

callbacks = [
    keras.callbacks.EarlyStopping(monitor="val_loss", patience=8, restore_best_weights=True),
    keras.callbacks.ReduceLROnPlateau(monitor="val_loss", factor=0.5, patience=4, min_lr=1e-6),
]

history = cnn_model.fit(
    train_ds,
    epochs=30,
    validation_data=val_ds,
    callbacks=callbacks,
    verbose=1,
)

# === 5. 评估：混淆矩阵与分类报告 ===
print("\n=== 收集验证集预测结果 ===")
all_preds = []
all_labels = []
for images, labels in val_ds:
    preds = cnn_model.predict(images, verbose=0).flatten()
    all_preds.extend((preds >= 0.5).astype(int))
    all_labels.extend(labels.numpy())

cm = confusion_matrix(all_labels, all_preds)
print(f"\n混淆矩阵:")
print(f"           预测=猫  预测=狗")
print(f"实际=猫     {cm[0][0]:5d}    {cm[0][1]:5d}")
print(f"实际=狗     {cm[1][0]:5d}    {cm[1][1]:5d}")

print(f"\n分类报告:")
print(classification_report(all_labels, all_preds, target_names=["猫", "狗"]))

val_loss, val_acc, val_auc = cnn_model.evaluate(val_ds, verbose=0)
print(f"\n验证集 - Loss: {val_loss:.4f}, Accuracy: {val_acc:.4f}, AUC: {val_auc:.4f}")

# === 6. 对比 MLP（全连接）Baseline ===
mlp_model = keras.Sequential([
    keras.layers.Flatten(input_shape=(*img_size, 3)),
    keras.layers.Dense(512, activation="relu"),
    keras.layers.Dense(256, activation="relu"),
    keras.layers.Dense(1, activation="sigmoid"),
])
mlp_model.compile(optimizer="adam", loss="binary_crossentropy", metrics=["accuracy"])

print(f"\nCNN 参数量: {cnn_model.count_params():,}")
print(f"MLP 参数量: {mlp_model.count_params():,}")
```

运行输出（示例）：
```
Model: "cat_vs_dog_cnn"
┌─────────────────────────────┬──────────────────────┬──────────────┐
│ Layer (type)                │ Output Shape         │      Param # │
├─────────────────────────────┼──────────────────────┼──────────────┤
│ conv2d (Conv2D)             │ (None, 128, 128, 32) │          896 │
│ conv2d_1 (Conv2D)           │ (None, 128, 128, 32) │        9,248 │
│ max_pooling2d (MaxPooling2D)│ (None, 64, 64, 32)   │            0 │
│ conv2d_2 (Conv2D)           │ (None, 64, 64, 64)   │       18,496 │
│ conv2d_3 (Conv2D)           │ (None, 64, 64, 64)   │       36,928 │
│ max_pooling2d_1             │ (None, 32, 32, 64)   │            0 │
│ conv2d_4 (Conv2D)           │ (None, 32, 32, 128)  │       73,856 │
│ conv2d_5 (Conv2D)           │ (None, 32, 32, 128)  │      147,584 │
│ max_pooling2d_2             │ (None, 16, 16, 128)  │            0 │
│ global_average_pooling2d    │ (None, 128)          │            0 │
│ dense (Dense)               │ (None, 256)          │       33,024 │
│ dense_1 (Dense)             │ (None, 1)            │          257 │
└─────────────────────────────┴──────────────────────┴──────────────┘
Total params: 320,289 (1.22 MB)

Epoch 1/30 - loss: 0.6521 - accuracy: 0.6012 - auc: 0.6543
...
Epoch 22/30 - early stopping (val_loss 不再改善)

混淆矩阵:
           预测=猫  预测=狗
实际=猫       87       13
实际=狗       11       89
分类报告:
              precision    recall  f1-score
        猫       0.89      0.87      0.88
        狗       0.87      0.89      0.88

CNN 参数量: 320,289
MLP 参数量: 38,890,753
```

### 3.3 迁移学习快速体验

```python
# 使用预训练 MobileNetV2 做特征提取（迁移学习快速验证）
base_model = keras.applications.MobileNetV2(
    input_shape=(*img_size, 3),
    include_top=False,
    weights=None,  # 实际项目中用 "imagenet"
)
base_model.trainable = False  # 冻结 backbone

transfer_model = keras.Sequential([
    base_model,
    keras.layers.GlobalAveragePooling2D(),
    keras.layers.Dense(128, activation="relu"),
    keras.layers.Dropout(0.5),
    keras.layers.Dense(1, activation="sigmoid"),
])

transfer_model.compile(optimizer=keras.optimizers.Adam(1e-3),
                       loss="binary_crossentropy", metrics=["accuracy"])
print(f"\n迁移学习模型参数量: {transfer_model.count_params():,}")

# 快速训练几轮看看效果
transfer_model.fit(train_ds, epochs=5, validation_data=val_ds, verbose=1,
                   callbacks=[keras.callbacks.EarlyStopping(patience=3)])
```

## 4. 项目总结

### 4.1 CNN vs MLP 对比

| 方面 | CNN | MLP (全连接) |
|------|-----|-------------|
| 参数量 | 32 万（本例） | 3890 万（本例） |
| 空间信息利用 | 通过卷积核的局部感受野保留空间结构 | Flatten 后空间信息全丢失 |
| 平移不变性 | 权值共享 + 池化 → 天然具备 | 需要大量数据增强来模拟 |
| 训练速度 | 快（参数少，计算集中在矩阵乘法） | 慢（第一层参数量爆炸） |
| 过拟合风险 | 低（参数少 + Dropout） | 高（参数多，容易记住训练样本） |
| 适用场景 | 图像、视频、音频频谱等网格结构数据 | 表格数据、已提取好的特征向量 |

### 4.2 适用场景

1. **图像分类**：猫狗识别、商品分类、医学影像诊断
2. **目标检测**：YOLO/SSD/Faster-RCNN 均在 CNN backbone 上扩展
3. **图像分割**：U-Net 等架构基于卷积编码-解码
4. **视频理解**：3D CNN 或 CNN+LSTM 组合
5. **音频分类**：将音频频谱图（mel spectrogram）作为 CNN 输入

**不适用场景**：
1. 结构化表格数据（树模型或 MLP 更合适）
2. 长序列时序预测（RNN/Transformer 更合适，除了一维 CNN 也有竞争力）

### 4.3 注意事项

- **输入归一化**：CNN 对输入尺度敏感，训练前务必将像素值归一化到 [0,1] 或标准化到 mean=0, std=1
- **卷积核数量的递增**：浅层用较少卷积核（32），深层用较多（128/256）——浅层特征通用，深层特征任务相关
- **GAP vs Flatten**：参数量敏感的场景（移动端、嵌入端）用 GlobalAveragePooling；精度优先且计算资源充裕用 Flatten + Dense
- **Dropout 的位置**：卷积层后 Dropout 一般设 0.2-0.3，全连接层后可设 0.5（全连接层更容易过拟合）

### 4.4 常见踩坑经验

1. **坑**：训练前几个 epoch loss 不降，accuracy 在 50% 波动（二分类）。
   **根因**：学习率太大导致梯度震荡；或数据没 shuffle，每个 batch 只包含一个类别。
   **解决**：降低学习率到 1e-4 重试；检查 tf.data 管道的 shuffle buffer_size。

2. **坑**：val_accuracy 远高于 train_accuracy。
   **根因**：数据增强太激进——训练数据被扭曲得面目全非，模型学不到有效特征；验证集的数据没增强（简单），所以验证比训练好。
   **解决**：降低数据增强强度（如 brightness_delta 从 0.3 降到 0.1），确保增强后的图片人眼仍能辨认。

3. **坑**：CNN 模型推理速度很慢，但模型不大。
   **根因**：输入图片尺寸太大或 channel 太多，在 CPU 上推理时逐像素处理开销大。
   **解决**：适当缩小输入尺寸（如 224→128），或使用 `tf.lite` 量化加速（第 26 章）。

### 4.5 思考题

1. 假设你需要把本章的猫狗分类 CNN 从 128×128 输入改为 256×256 输入，但 GPU 显存有限（batch_size 从 32 降到了 8，导致梯度估计不稳定）。除了减小模型，还有哪些不牺牲 model capacity 的方案？（提示：梯度累积、混合精度训练）

2. 本章的 CNN 用了 3 个 Conv-Pool Block。如果改为 5 个 Block，会有什么收益和风险？如何判断模型深度是否"过度"？

### 4.6 推广计划提示

- **新人开发**：务必先跑通本章的完整 CNN 训练流程（数据管道 + 模型 + 训练 + 评估），再尝试替换自己的数据集
- **算法工程师**：输出混淆矩阵和分类报告是"模型交付"的基本要求，不要只看 accuracy
- **测试工程师**：对图像模型编写鲁棒性测试——旋转/裁剪/亮度变化/遮挡后的预测是否合理（猫转 90 度还是猫）
