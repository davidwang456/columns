# 第20章：TensorBoard 实验管理与可视化

## 1. 项目背景

算法团队正在做一个"新闻分类"模型——将新闻分为 12 个类别。团队有 4 个算法工程师，每个人都在独立跑实验。一个月下来，大家积累了 30 多组实验结果，但问题浮现了：

- 小张说他用 `lr=0.001` 和 `batch_size=128` 跑到了验证准确率 89%，但找不到当时保存的训练日志——他只在终端看了最后几行输出
- 小李说 `lr=0.0005` 效果最好，但小张说他也试过这个配置，"根本不收敛"——两人谁也说服不了谁，因为没有可对比的记录
- 小王发现模型在第 15 个 epoch 出现过拟合迹象（训练 loss 降、val loss 升），但他不知道具体是哪一层先过拟合的
- 团队 leader 想知道"这 30 组实验中，`learning_rate` 和 `batch_size` 哪个超参数对模型效果影响最大"——没人能回答

更糟糕的是，团队正在准备向业务方汇报，需要展示"我们如何在多组实验中选出了最佳模型"——但他们只有几组 Excel 表格和终端输出的最后一行。

**痛点放大**：机器学习实验管理不是"跑完看准确率"就结束了——它包括：(1) 训练过程的可视化记录（每条 loss 曲线、每层权重分布）；(2) 多次实验的对比分析（哪个超参数最关键）；(3) 模型的可追溯性（某次实验用了什么数据、什么配置）；(4) 团队协作时的实验共享和复现。TensorBoard 是解决这些问题的标准工具。

## 2. 项目设计

**小胖**（看着满屏的终端输出）：这些 loss 数字我看都看麻了！每天跑 3 组实验，每组的 loss 都得自己手动抄到 Excel 里画图——这也太原始了吧？

**大师**：你这种"手动抄表"方式有两个致命问题：第一，你只记录了 loss 的最后一个数字，中间怎么震荡的、什么时候过拟合的，你全丢了。第二，30 组实验排在一起，你肉眼根本比不出来哪个配置最优。

TensorBoard 就是你的"自动驾驶记录仪"——它在后台自动记录每次训练的 loss 曲线、准确率变化、权重分布，你只需要跑完实验后在浏览器里打开 TensorBoard，所有实验一目了然。而且它不是事后截图——是**实时**刷新，你在训练的时候就能看到曲线在走。

**技术映射**：TensorBoard 是 TensorFlow 的可视化套件，通过 Callback 在训练过程中记录 Scalars（标量曲线）、Histograms（权重分布）、Graphs（计算图）、Images/Text（自定义可视化）。

**小白**（盯着 TensorBoard 的界面）：这些曲线我会看，但怎么判断过拟合和欠拟合？

**大师**：训练曲线是模型的"体检报告"，三张图就够了：

```
过拟合：                           欠拟合：
loss  ↑      train                 loss  ↑   train
      |    ╱                        |    ╱
      |  ╱    val                   |  ╱  val
      |╱      ╲                    |╱
      |        ╲___ train 持续降   | ╲_____ 两者都高且差不多
      |         ╱  val 开始升      |
      └──────────────→ epoch       └──────────────→ epoch

正常收敛：                         学习率太大：
loss  ↑                            loss  ↑
      |╲    train                   |  ╱╲╱╲╱╲  剧烈震荡
      | ╲                          | ╱
      |  ╲___  val                 |╱
      |   ╲                       └──────────────→ epoch
      └──────────────→ epoch
```

**大师**：过拟合 = train loss 降、val loss 升。欠拟合 = 两者都停在较高的水平。学习率过大 = loss 剧烈震荡不下降。这些肉眼一两秒就能判断。

**小白**：那 HParams 是什么？跟普通 scalar 曲线有啥不同？

**大师**：普通 scalar 是"一条曲线"——告诉你这次训练 loss 怎么变的。HParams 是"热力图"——告诉你 30 次实验中，`learning_rate=0.001, batch_size=64` 这张组合的准确率是多少。TensorBoard 的 HParams 插件能自动找出"哪个超参数对结果影响最大"，然后按重要性排序给你看。

**小胖**：那自定义 summary 呢？我看到还能记录图片、文字？

**大师**：对，这是 TensorBoard 的隐藏大招。比如你可以在验证阶段把"模型预测错误的图片"记录到 TensorBoard——一眼就能看出哪些类容易混淆。你可以记录混淆矩阵的图片、embedding 投影图、甚至是注意力权重的可视化。这些对 debug 模型行为比看 loss 数字直观多了。

**技术映射**：`tf.summary.image()` 记录图片；`tf.summary.text()` 记录文本；`tf.summary.histogram()` 记录权重的分布变化（检测梯度消失/爆炸）；`tf.summary.scalar()` 是基础。

**小白**：最后一个问题——团队怎么共享实验？总不能每个人本地跑 TensorBoard 然后截图发群里吧？

**大师**：标准的做法是——所有实验的日志写到共享存储（NFS/HDFS）的同一个根目录下：

```
/logs/
├── experiment_001_lr0.001_bs64/
├── experiment_002_lr0.0005_bs128/
├── ...
```

然后团队搭一个常驻的 TensorBoard 服务器，指向这个目录。每个人在浏览器里打开 `http://tensorboard-server:6006` 就能看到所有实验。实验命名要有规范——`{日期}_{模型名}_{关键超参数}`，比如 `20250101_resnet_lr0.001_bs128`。

## 3. 项目实战

### 3.1 环境准备

```bash
pip install tensorflow==2.16.1 numpy==1.26.4 matplotlib==3.8.4 scikit-learn==1.5.0
```

### 3.2 分步实现

**步骤一：基础 TensorBoard Callback + 自定义 summary**

目标：记录 loss/accuracy 标量 + 自定义图片 summary。

```python
import tensorflow as tf
from tensorflow import keras
import numpy as np
import os
import tempfile
import datetime

# 日志目录（按时间戳分实验）
log_dir = os.path.join(tempfile.mkdtemp(), "logs",
                       datetime.datetime.now().strftime("%Y%m%d-%H%M%S"))
summary_writer = tf.summary.create_file_writer(log_dir)

# 模拟数据
np.random.seed(42)
X = np.random.randn(2000, 20).astype(np.float32)
y = np.random.randint(0, 5, 2000).astype(np.int32)
X_val, y_val = X[-400:], y[-400:]
X_train, y_train = X[:-400], y[:-400]

model = keras.Sequential([
    keras.layers.Dense(128, activation="relu", input_shape=(20,)),
    keras.layers.Dropout(0.3),
    keras.layers.Dense(64, activation="relu"),
    keras.layers.Dropout(0.3),
    keras.layers.Dense(5, activation="softmax"),
])

model.compile(
    optimizer=keras.optimizers.Adam(1e-3),
    loss="sparse_categorical_crossentropy",
    metrics=["accuracy"],
)

# === 自定义 Callback: 记录权重分布和混淆矩阵 ===
class CustomSummaryCallback(keras.callbacks.Callback):
    def __init__(self, log_dir, val_data, class_names=None, log_freq=5):
        super().__init__()
        self.writer = tf.summary.create_file_writer(log_dir + "/custom")
        self.val_data = val_data
        self.class_names = class_names or [str(i) for i in range(5)]
        self.log_freq = log_freq

    def on_epoch_end(self, epoch, logs=None):
        if (epoch + 1) % self.log_freq != 0:
            return

        with self.writer.as_default():
            # 1. 记录权重直方图
            for layer in self.model.layers:
                for weight in layer.weights:
                    tf.summary.histogram(weight.name, weight, step=epoch)

            # 2. 记录预测结果的混淆矩阵（文本形式）
            X_v, y_v = self.val_data
            y_pred = np.argmax(self.model.predict(X_v, verbose=0), axis=1)
            from sklearn.metrics import confusion_matrix
            cm = confusion_matrix(y_v, y_pred)
            cm_text = "混淆矩阵:\n" + " " * 8 + " ".join(f"{n:>6}" for n in self.class_names)
            for i, row in enumerate(cm):
                cm_text += f"\n{self.class_names[i]:>8}" + " ".join(f"{v:>6}" for v in row)
            tf.summary.text("confusion_matrix", cm_text, step=epoch)

            # 3. 记录学习率
            lr = self.model.optimizer.learning_rate
            if hasattr(lr, 'numpy'):
                lr_val = lr.numpy()
            else:
                lr_val = lr
            tf.summary.scalar("learning_rate", lr_val, step=epoch)

        self.writer.flush()

# === 训练 ===
custom_cb = CustomSummaryCallback(log_dir, (X_val, y_val),
                                   class_names=["政治","经济","科技","体育","娱乐"])

history = model.fit(
    X_train, y_train,
    batch_size=64, epochs=20,
    validation_data=(X_val, y_val),
    callbacks=[
        keras.callbacks.TensorBoard(
            log_dir=log_dir,
            histogram_freq=5,
            write_graph=True,
        ),
        custom_cb,
    ],
    verbose=0,
)

print(f"TensorBoard 日志保存在: {log_dir}")
print(f"启动命令: tensorboard --logdir={log_dir}")
```

**步骤二：HParams 超参数对比实验**

目标：对 6 组超参数组合运行实验，在 TensorBoard 中对比。

```python
import tensorflow as tf
from tensorboard.plugins.hparams import api as hp
import numpy as np
import os
import tempfile

# 模拟数据
X = np.random.randn(1000, 10).astype(np.float32)
y = np.random.randint(0, 2, 1000).astype(np.float32)

# === HParams 配置 ===
HP_LR = hp.HParam("learning_rate", hp.Discrete([1e-2, 1e-3, 1e-4]))
HP_UNITS = hp.HParam("hidden_units", hp.Discrete([32, 64, 128]))
HP_DROPOUT = hp.HParam("dropout", hp.Discrete([0.2, 0.5]))

hparams_list = [
    {HP_LR: 1e-2, HP_UNITS: 64,  HP_DROPOUT: 0.2},
    {HP_LR: 1e-3, HP_UNITS: 64,  HP_DROPOUT: 0.2},
    {HP_LR: 1e-4, HP_UNITS: 64,  HP_DROPOUT: 0.2},
    {HP_LR: 1e-3, HP_UNITS: 32,  HP_DROPOUT: 0.2},
    {HP_LR: 1e-3, HP_UNITS: 128, HP_DROPOUT: 0.2},
    {HP_LR: 1e-3, HP_UNITS: 64,  HP_DROPOUT: 0.5},
]

# TensorBoard 日志根目录
hparams_log_dir = os.path.join(tempfile.mkdtemp(), "hparams_logs")

print("=== 运行 6 组 HParams 实验 ===\n")

for run_id, hparams in enumerate(hparams_list, 1):
    tf.random.set_seed(42)
    run_name = f"run_{run_id:02d}_lr{hparams[HP_LR]:.0e}_u{hparams[HP_UNITS]}_d{hparams[HP_DROPOUT]}"
    run_dir = os.path.join(hparams_log_dir, run_name)

    # 记录 HParams 到 TensorBoard
    with tf.summary.create_file_writer(run_dir).as_default():
        hp.hparams_config(
            hparams=[HP_LR, HP_UNITS, HP_DROPOUT],
            metrics=[hp.Metric("val_accuracy", display_name="验证准确率")],
        )
        hp.hparams(hparams)

    # 构建模型
    model = tf.keras.Sequential([
        tf.keras.layers.Dense(hparams[HP_UNITS], activation="relu", input_shape=(10,)),
        tf.keras.layers.Dropout(hparams[HP_DROPOUT]),
        tf.keras.layers.Dense(hparams[HP_UNITS] // 2, activation="relu"),
        tf.keras.layers.Dropout(hparams[HP_DROPOUT]),
        tf.keras.layers.Dense(1, activation="sigmoid"),
    ])

    model.compile(
        optimizer=tf.keras.optimizers.Adam(hparams[HP_LR]),
        loss="binary_crossentropy",
        metrics=["accuracy"],
    )

    history = model.fit(
        X, y,
        batch_size=64, epochs=15,
        validation_split=0.2, verbose=0,
        callbacks=[
            tf.keras.callbacks.TensorBoard(log_dir=run_dir, histogram_freq=0),
            hp.KerasCallback(run_dir, hparams),  # 报告指标
        ],
    )

    final_val_acc = history.history["val_accuracy"][-1]
    print(f"  {run_name}: val_acc = {final_val_acc:.4f}")

print(f"\n所有日志保存在: {hparams_log_dir}")
print(f"在 TensorBoard 中打开: tensorboard --logdir={hparams_log_dir}")
print(f"然后进入 HParams 标签页查看超参数对比")

# 快速分析：哪个参数影响最大
results = {}
for run_id, hparams in enumerate(hparams_list, 1):
    key = (hparams[HP_LR], hparams[HP_UNITS], hparams[HP_DROPOUT])
    tf.random.set_seed(42)
    model = tf.keras.Sequential([
        tf.keras.layers.Dense(hparams[HP_UNITS], activation="relu", input_shape=(10,)),
        tf.keras.layers.Dropout(hparams[HP_DROPOUT]),
        tf.keras.layers.Dense(hparams[HP_UNITS] // 2, activation="relu"),
        tf.keras.layers.Dense(1, activation="sigmoid"),
    ])
    model.compile(optimizer=tf.keras.optimizers.Adam(hparams[HP_LR]),
                  loss="binary_crossentropy", metrics=["accuracy"])
    h = model.fit(X, y, epochs=15, batch_size=64, validation_split=0.2, verbose=0)
    results[key] = h.history["val_accuracy"][-1]

# 按参数分组计算均值
print("\n=== 超参数影响分析 ===")
for hp_name, hp_obj in [("learning_rate", HP_LR), ("hidden_units", HP_UNITS), ("dropout", HP_DROPOUT)]:
    print(f"\n{hp_name}:")
    for val in hp_obj.domain.values:
        matching = [acc for k, acc in results.items()
                    if k[list(hp_obj.domain.values).index(val)] == val]
        if matching:
            print(f"  {val}: mean val_acc = {np.mean(matching):.4f}")
```

**步骤三：Embedding Projector 可视化**

目标：将高维特征通过 PCA/t-SNE 投影到三维，在 TensorBoard 中交互查看。

```python
import tensorflow as tf
import numpy as np
import os
import tempfile

# 模拟 500 个 64 维向量 + 标签
np.random.seed(42)
n_samples = 500
embed_dim = 64
n_classes = 5

embeddings = np.random.randn(n_samples, embed_dim).astype(np.float32)
labels = np.random.randint(0, n_classes, n_samples)

# 保存向量
projector_dir = os.path.join(tempfile.mkdtemp(), "projector_logs")
os.makedirs(projector_dir, exist_ok=True)

# 写入向量文件（TSV 格式）
vec_path = os.path.join(projector_dir, "embeddings.tsv")
meta_path = os.path.join(projector_dir, "metadata.tsv")

np.savetxt(vec_path, embeddings, delimiter="\t")
with open(meta_path, "w", encoding="utf-8") as f:
    f.write("label\tcategory\n")
    for lbl in labels:
        f.write(f"{lbl}\tclass_{lbl}\n")

# TensorBoard 的 Projector 配置
config = tf.compat.v1.ConfigProto()
from tensorboard.plugins import projector
projector_config = projector.ProjectorConfig()
embedding_info = projector_config.embeddings.add()
embedding_info.tensor_name = "embeddings"
embedding_info.metadata_path = os.path.basename(meta_path)
projector.visualize_embeddings(tf.compat.v1.SummaryWriter(projector_dir), projector_config)

print(f"Embedding 数据已保存到: {projector_dir}")
print(f"启动 TensorBoard: tensorboard --logdir={projector_dir}")
print(f"点击 Projector 标签页查看高维向量的 3D 可视化")
```

### 3.3 TensorBoard 使用 Checklist

| 功能 | TensorBoard 标签页 | 适合什么场景 |
|------|-------------------|-------------|
| 训练/验证 loss 和 accuracy | SCALARS | 每天必看，判断收敛、过拟合、学习率 |
| 权重和梯度的分布变化 | HISTOGRAMS | 排查梯度消失/爆炸、权重是否在更新 |
| 计算图结构 | GRAPHS | 理解模型拓扑、检查显存占用 |
| 训练样本和增强效果 | IMAGES | 验证数据增强是否合理、标签是否正确 |
| 文本样本 | TEXT | NLP 模型的生成结果可视化 |
| 超参数实验对比 | HPARAMS | 多组实验的并行对比与重要性排序 |
| 高维向量投影 | PROJECTOR | Embedding 质量的直观判断 |

## 4. 项目总结

### 4.1 TensorBoard vs 其他实验管理工具

| 方面 | TensorBoard | MLflow | Weights & Biases |
|------|-------------|--------|-----------------|
| 安装复杂性 | 零（随 TF 内置） | 需额外安装 | 需注册账号 |
| 训练曲线可视化 | ✅ 优秀 | ✅ 良好 | ✅ 优秀 |
| HParams 对比 | ✅ 内置插件 | ✅ 支持 | ✅ 支持 |
| 模型注册/版本管理 | ❌ | ✅ | ✅ |
| 团队协作 | 需共享存储 | ✅ 中心化 Server | ✅ 云端 |
| 自定义可视化 | ✅ tf.summary API | ⚠️ 有限 | ✅ |
| 离线使用 | ✅ | ✅ | ❌ 需联网 |

### 4.2 适用场景

1. **日常训练监控**：每轮训练自动记录 scalars + histograms
2. **超参数搜索**：HParams 插件对比多组实验，可视化重要性排序
3. **模型 Debug**：通过 weight histogram 发现不更新的层、通过 image summary 检查数据增强
4. **Embedding 分析**：Projector 可视化词向量或物品向量的聚类效果
5. **团队知识沉淀**：共享 TensorBoard 日志目录，作为团队的"实验档案馆"

**不适用场景**：
1. 需要严格的模型版本管理和审批流程（需配合 MLflow 或 TFX）
2. 极大规模的参数搜索（> 1000 组实验）——HParams 的性能和 UI 可能不够友好

### 4.3 注意事项

- **日志目录命名规范**：`{项目名}/{日期}/{实验描述}_{关键超参数}`，如 `news_cls/20250101/baseline_lr0.001_bs64`
- **`histogram_freq` 不要设太频繁**：每 step 记录一次 histogram 会产生巨大日志文件（GB 级），建议 `histogram_freq=5`（每 5 个 epoch 记录一次）
- **不同实验不要写到同一个 log 目录**：TensorBoard 按子目录区分实验，同名目录会覆盖
- **远程日志**：TensorBoard 支持读取 S3/GCS 上的日志，但启动时会下载索引文件，远程目录很大时启动慢

### 4.4 常见踩坑经验

1. **坑**：TensorBoard 打开后 SCALARS 页面空白。
   **根因**：TensorBoard 的 `--logdir` 路径没有 event 文件（`.tfevents.*`），或路径层级不对。
   **解决**：确认日志目录下有 `events.out.tfevents.*` 文件；`logdir` 指向的是包含这些文件的目录，不是子目录。

2. **坑**：HParams 页面中"Parallel Coordinates View"无数据显示。
   **根因**：`hp.KerasCallback` 或手动 `tf.summary.scalar` 必须用 `hp.hparams(hparams)` 注册过的 metric name，名字不一致会导致数据不关联。
   **解决**：print 一下 `hp.hparams_config` 中定义的 `metrics` 列表，确保 `KerasCallback` 监控的 metric name 完全一致。

3. **坑**：TensorBoard 占用大量内存，浏览器卡顿。
   **根因**：日志包含 `histogram_freq=1` 的大量 histogram 数据，TensorBoard 加载时解析全部。
   **解决**：降低 `histogram_freq`；清理不需要的旧日志目录；使用 `tensorboard --samples_per_plugin=50` 限载每个插件的样本数。

### 4.5 思考题

1. 你需要在一个多任务模型中同时监控 3 个 loss（总 loss / CTR loss / CVR loss）。TensorBoard SCALARS 页面默认把所有指标按字母序排列，如何让它们按你的逻辑分组？请设计一种命名规范实现。

2. 如果你想让 TensorBoard 展示"模型预测的可信度"——对于每个验证集样本，记录它被正确分类时的平均置信度 vs 错误分类时的平均置信度——你会如何用 `tf.summary` API 实现？

### 4.6 推广计划提示

- **新人开发**：训练前必须指定 `--logdir`，把"看 TensorBoard"变成和 `git status` 一样的肌肉记忆
- **算法工程师**：制定团队的实验命名规范 + TensorBoard 日志归档规则，每月清理过期的实验日志
- **运维/平台工程师**：在团队内部搭建一个常驻 TensorBoard 服务（Docker + NFS 挂载日志目录），省去每人本地启动的麻烦
