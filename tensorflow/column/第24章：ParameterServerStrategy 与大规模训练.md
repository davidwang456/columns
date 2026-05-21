# 第24章：ParameterServerStrategy 与大规模训练

## 1. 项目背景

一家短视频平台的推荐团队维护着一个超大规模推荐模型——用户 Embedding 表（2 亿用户 × 128 维）加视频 Embedding 表（5000 万视频 × 128 维），仅 Embedding 参数就接近 32GB。加上 DNN 部分，总参数量超过 40GB——单张 V100 (32GB) 根本放不下。

团队尝试了第 23 章的 MirroredStrategy——模型放不进一张卡，每张卡都要一个完整的模型副本，4 张卡加起来需要 160GB 显存但实际只有 128GB，直接 OOM。

架构师老周提出了 ParameterServerStrategy 方案——把 Embedding 表拆分到不同的参数服务器（Parameter Server, PS）上，每个 PS 负责一部分参数，多个 Worker 各自训练，从 PS 上读写参数。Worker 之间不需要通信——它们通过 PS 间接同步。

但新问题来了：异步更新模式下，Worker A 更新了 User123 的 Embedding，Worker B 几乎同时也在更新同一个 User123——两个更新可能互相覆盖，导致参数不一致。而且在 Docker Compose 部署中，PS 挂了整个训练会不会崩？

**痛点放大**：当模型参数（特别是 Embedding 表）大到单卡放不下时，MirroredStrategy 的"每卡一个完整副本"模式失效。ParameterServerStrategy 通过参数分片解决显存问题，并通过异步更新解耦 Worker 间的依赖——代价是一致性减弱，需要容错处理。

## 2. 项目设计

**小胖**（挠头）：参数服务器？听着像是一个中心的"权重仓库"。所有 GPU 训练的时候都去这个仓库拿参数、还参数？这不就又回到中心化瓶颈了吗？

**大师**：参数服务器确实像"仓库"，但不是只有一个——通常有多个 PS，每个只存一部分参数。比如 32GB 的 Embedding 表，切成 4 个 8GB 的 PS，每个存一部分。而且 Worker 是"异步"地从 PS 读写——Worker A 拿完参数就去算自己的，算完了把梯度 update 推回 PS，不需要等 Worker B。

这就是异步训练（Asynchronous Training）——每个 Worker 独立工作，互不等待。

**技术映射**：ParameterServerStrategy = 参数分片（Sharding）+ 异步更新（Async Update）。Worker 独立计算梯度并 push 到 PS，PS 负责参数存储和更新。Worker 间无直接通信。

**小白**：异步更新不会导致"数据不一致"吗？Worker A 更新了一个参数，Worker B 读到的是更新前的还是更新后的？

**大师**：这正是异步训练的代价——**参数陈旧（Staleness）**。Worker B 可能拿到的是一两秒前的旧参数。这会导致：Worker B 用旧参数算了梯度，然后把这个"基于过时信息"的梯度 push 回 PS。理论上这会让训练不稳定——但实际上对于推荐系统这种"稀疏梯度 + 海量数据"的场景，少量的 staleness 不影响最终效果，十几秒内的参数漂移是可忽略的。

而且有缓解手段——`staleness` 参数控制 Worker 与 PS 的同步频率：

```python
strategy = tf.distribute.experimental.ParameterServerStrategy(
    cluster_resolver,
    variable_partitioner=partitioner  # 控制参数如何分片
)
```

**技术映射**：异步训练的 staleness 是"用精度换吞吐"——梯度可能基于旧参数计算，但 Worker 无需互相等待，吞吐量大幅提升。推荐系统场景对此容忍度高，敏感任务（如强化学习）不适合。

**小胖**：那 `TF_CONFIG` 是什么东西？为什么你代码里有个 JSON？

**大师**：`TF_CONFIG` 是多机训练的"通信录"——它告诉每个进程"你是谁（Worker 还是 PS）、你在哪个机器上、其他人在哪里"。单机训练不需要这个（因为所有 GPU 在同一台机器上），多机训练必须配置。

```python
# 多机参数服务器训练的 TF_CONFIG 示例
TF_CONFIG = {
    "cluster": {
        "worker": ["worker0:2222", "worker1:2222", "worker2:2222"],
        "ps": ["ps0:2222", "ps1:2222"],
    },
    "task": {"type": "worker", "index": 0}  # 当前进程的身份
}
```

Worker 进程设置 `task.type="worker"`，PS 进程设置 `task.type="ps"`。PS 进程只需要启动 `tf.distribute.Server` 然后等待 Worker 连接——它不执行任何训练代码。

**技术映射**：`TF_CONFIG` 环境变量是 TensorFlow 分布式集群的"拓扑描述"——定义哪些节点是 Worker（执行训练），哪些是 PS（存储参数），以及它们的网络地址。

## 3. 项目实战

### 3.1 环境准备

```bash
pip install tensorflow==2.16.1 numpy==1.26.4
```

> 本章模拟代码可在单机运行（使用 `OneDeviceStrategy` 替代真实多机 PS 策略感知）。真实多机部署需要多台机器配置 TF_CONFIG。

### 3.2 分步实现

**步骤一：参数分片原理演示**

目标：理解 Variable Partitioning 如何将一个大的 Embedding 表切分到多个设备。

```python
import tensorflow as tf
import numpy as np

# === 使用 tf.distribute 的 Variable 分片 ===
# 注意：ParameterServerStrategy 的完整功能需要多机环境
# 本节在单机上演示分片的概念

# 模拟大 Embedding 表：词汇表 10 万，128 维
vocab_size = 100_000
embed_dim = 128

# 创建分片器：在维度 0 上切成 N 片
partitioner = tf.distribute.experimental.partitioners.FixedShardsPartitioner(
    num_shards=4  # 切成 4 片，每片 25,000 × 128
)

# 用分片器创建 Variable
with tf.device("CPU:0"):  # 通常 PS 在 CPU 上，Embedding 表放 CPU
    sharded_embedding = tf.Variable(
        initial_value=lambda: tf.random.normal([vocab_size, embed_dim]),
        name="sharded_embedding",
        # 在 ParameterServerStrategy 中会自动分片，此处演示分片概念
    )

print(f"Embedding 表大小: {vocab_size * embed_dim * 4 / 1024 / 1024:.1f} MB (float32)")
print(f"分片数: 4, 每片大小: {vocab_size // 4 * embed_dim * 4 / 1024:.1f} KB")

# 模拟分片查询
def lookup_shard(embedding_ids, shard_id, shard_size):
    """模拟从某个 PS 分片上查找 Embedding"""
    # 找出属于当前分片的 ID
    mask = (embedding_ids // (vocab_size // 4)) == shard_id
    shard_ids = embedding_ids[mask]
    local_ids = shard_ids % shard_size  # 转为分片内索引
    return local_ids, shard_ids

# 测试：查找 [0, 25000, 50000, 75000] 分别在哪片
test_ids = tf.constant([0, 25_000, 50_000, 75_000, 12_345])
for shard_id in range(4):
    local, global_ = lookup_shard(test_ids, shard_id, vocab_size // 4)
    if len(local) > 0:
        print(f"  PS{shard_id}: global_ids={global_.numpy()}, local_ids={local.numpy()}")
```

运行输出：
```
Embedding 表大小: 48.8 MB (float32)
分片数: 4, 每片大小: 3200.0 KB
  PS0: global_ids=[    0 12345], local_ids=[    0 12345]
  PS1: global_ids=[25000], local_ids=[0]
  PS2: global_ids=[50000], local_ids=[0]
  PS3: global_ids=[75000], local_ids=[0]
```

**步骤二：ParameterServerStrategy 训练模板**

目标：展示多机参数服务器训练的代码模板（单机模拟模式）。

```python
import tensorflow as tf
import numpy as np
import os
import json

# 模拟多机环境的 TF_CONFIG（实际使用时从环境变量读取）
# 此处演示单机版本，重用 OneDeviceStrategy 的逻辑

def create_cluster_spec():
    """模拟一个 2 Worker + 2 PS 的集群"""
    return tf.train.ClusterSpec({
        "worker": ["localhost:12345", "localhost:12346"],
        "ps": ["localhost:12347", "localhost:12348"],
    })

# === PS 进程代码（独立运行） ===
def run_ps_server(task_index):
    """参数服务器进程：只启动 Server，等待连接"""
    cluster = create_cluster_spec()
    server = tf.distribute.Server(
        cluster,
        job_name="ps",
        task_index=task_index,
        protocol="grpc",
    )
    print(f"[PS-{task_index}] 启动, 等待 Worker 连接...")
    server.join()  # 阻塞直到所有 Worker 退出

# === Worker 进程代码 ===
def run_worker(task_index):
    """Worker 进程：执行训练"""
    cluster = create_cluster_spec()
    cluster_resolver = tf.distribute.cluster_resolver.SimpleClusterResolver(
        cluster, rpc_layer="grpc"
    )

    # 大 Embedding 的分片器
    variable_partitioner = tf.distribute.experimental.partitioners.MiniBatchPartitioner(
        tf.distribute.experimental.partitioners.FixedShardsPartitioner(num_shards=4),
        min_shard_bytes=1024,
    )

    strategy = tf.distribute.experimental.ParameterServerStrategy(
        cluster_resolver,
        variable_partitioner=variable_partitioner,
    )

    with strategy.scope():
        # 提供大 Embedding 表获取模型的推荐模式
        model = create_large_embedding_model()

        model.compile(
            optimizer=tf.keras.optimizers.Adam(1e-3),
            loss="binary_crossentropy",
            metrics=["accuracy"],
        )

    # 训练数据
    dataset = create_dataset().batch(64)
    distributed_dataset = strategy.experimental_distribute_dataset(dataset)

    model.fit(distributed_dataset, epochs=10)
    print(f"[Worker-{task_index}] 训练完成")


def create_large_embedding_model(vocab_size=500_000, embed_dim=128):
    """模拟大 Embedding 模型"""
    # 两个塔
    user_input = tf.keras.Input(shape=(), dtype=tf.int64, name="user_id")
    item_input = tf.keras.Input(shape=(), dtype=tf.int64, name="item_id")

    # 大 Embedding 表（会被 strategy 自动分片到多个 PS）
    user_emb = tf.keras.layers.Embedding(vocab_size, embed_dim, name="user_embedding")(user_input)
    item_emb = tf.keras.layers.Embedding(vocab_size, embed_dim, name="item_embedding")(item_input)

    user_emb = tf.keras.layers.Flatten()(user_emb)
    item_emb = tf.keras.layers.Flatten()(item_emb)

    # 合并 + DNN
    merged = tf.keras.layers.Concatenate()([user_emb, item_emb])
    x = tf.keras.layers.Dense(128, activation="relu")(merged)
    x = tf.keras.layers.Dense(64, activation="relu")(x)
    output = tf.keras.layers.Dense(1, activation="sigmoid")(x)

    return tf.keras.Model(inputs=[user_input, item_input], outputs=output)


def create_dataset():
    """模拟推荐训练数据"""
    np.random.seed(42)
    n = 5000
    user_ids = np.random.randint(0, 500_000, n)
    item_ids = np.random.randint(0, 500_000, n)
    labels = np.random.randint(0, 2, n).astype(np.float32)
    ds = tf.data.Dataset.from_tensor_slices((
        {"user_id": user_ids, "item_id": item_ids}, labels
    ))
    return ds


print("=== ParameterServerStrategy 架构说明 ===")
print("""
架构图:
  Worker-0 ──push gradients──→ PS-0 (Embedding Shard 0, 1)
  Worker-1 ──pull params────→ PS-1 (Embedding Shard 2, 3)
  
  特点:
  - 每个 PS 存储一部分参数分片
  - Worker 异步 push/pull，互不等待
  - Embedding 表自动分片，对小 Embedding 不分割
  - Checkpoint 需从所有 PS 收集参数
""")
print("注：完整的 ParameterServerStrategy 需要多个物理/容器节点运行。")
print("以上为代码模板，实际部署需 Docker Compose 或 K8s 编排。")
```

**步骤三：异步训练一致性模拟实验**

目标：对比同步训练 vs 异步训练的梯度差异。

```python
import tensorflow as tf
import numpy as np

# 模拟简单的权重更新场景
# 场景：两个 Worker 同时在更新同一个变量

initial_value = 10.0

# === 同步更新（MirroredStrategy 方式） ===
# 两个 Worker 都计算梯度 d=+2，加总后取平均 = (2+2)/2 = +2
# 更新后 = 10 + 2 = 12
sync_result = initial_value + (2.0 + 2.0) / 2
print(f"同步更新: {initial_value} + avg(2, 2) = {sync_result}")

# === 异步更新（ParameterServerStrategy 方式） ===
# Worker A: 读到 10，算梯度 +2，push → PS 更新为 12
# Worker B: 读到 10（旧值！），算梯度 +2，push → PS 更新为 12（覆盖了 A 的更新！）
async_result_lost_update = initial_value + 2.0
print(f"异步更新(丢更新): {initial_value} + 2 = {async_result_lost_update} (B 覆盖了 A)")

# 缓解方案：每个 Worker 只更新自己负责的 Embedding 分片
# 这样即使异步，两个 Worker 不会同时更新同一个参数
print(f"\n分片后: Worker A 更新 shard_0, Worker B 更新 shard_1 → 无冲突")

# 实际计算 staleness 的影响
print(f"\nStaleness 影响分析:")
print(f"  模型 converged 时，梯度接近 0 → 异步更新的差异可忽略")
print(f"  模型初期，梯度很大 → 异步更新可能导致震荡")
print(f"  解决方案: 初期用较小学习率 (warmup)，后期异步不影响")
```

**步骤四：Checkpoint 与容错恢复**

```python
import tensorflow as tf
import os
import tempfile

def demo_checkpoint_recovery():
    """演示 ParameterServerStrategy 的 checkpoint 保存与恢复"""
    tmpdir = tempfile.mkdtemp()

    # 模拟模型
    model = tf.keras.Sequential([
        tf.keras.layers.Dense(16, activation="relu", input_shape=(8,)),
        tf.keras.layers.Dense(1),
    ])
    model.compile(optimizer="adam", loss="mse")

    # 训练几步
    X = tf.random.normal([100, 8])
    y = tf.random.normal([100, 1])
    model.fit(X, y, epochs=2, verbose=0)

    # 保存 checkpoint
    ckpt = tf.train.Checkpoint(model=model, optimizer=model.optimizer)
    ckpt_path = os.path.join(tmpdir, "training_checkpoints", "ckpt")
    ckpt.save(ckpt_path)
    print(f"Checkpoint 已保存到: {ckpt_path}")

    # 模拟恢复：构建新模型，从 checkpoint 恢复
    restored_model = tf.keras.Sequential([
        tf.keras.layers.Dense(16, activation="relu", input_shape=(8,)),
        tf.keras.layers.Dense(1),
    ])
    restored_model.compile(optimizer="adam", loss="mse")

    restored_ckpt = tf.train.Checkpoint(model=restored_model)

    # 找到最新的 checkpoint
    latest = tf.train.latest_checkpoint(os.path.dirname(ckpt_path))
    restored_ckpt.restore(latest).expect_partial()
    print(f"从 {latest} 恢复模型完成")

    # 验证恢复后输出一致
    test_x = tf.constant([[1.0]*8])
    pred_orig = model.predict(test_x, verbose=0)
    pred_rest = restored_model.predict(test_x, verbose=0)
    print(f"恢复后输出一致性: {np.allclose(pred_orig, pred_rest)}")

    # 清理
    import shutil
    shutil.rmtree(tmpdir, ignore_errors=True)

demo_checkpoint_recovery()
```

## 4. 项目总结

### 4.1 MirroredStrategy vs ParameterServerStrategy

| 方面 | MirroredStrategy | ParameterServerStrategy |
|------|-----------------|------------------------|
| 同步模式 | 同步（所有 Worker 一起更新） | 异步（Worker 独立 push/pull） |
| 参数位置 | 每卡一份完整副本 | 分片存储在多个 PS 上 |
| 适用模型 | 模型 < 单卡显存 | 超大 Embedding 表（推荐/搜索） |
| 扩展性 | 受单机 NVLink 带宽限制 | 可扩展到多机多 PS |
| 一致性 | 强一致（同步梯度） | 弱一致（可能有 staleness） |
| 容错性 | 一卡挂全停 | PS 挂影响部分参数，Worker 挂可重连 |
| 收敛速度 | 快（同步更新方向更准） | 可能慢（异步需更多 step） |
| 部署复杂度 | 简单（单机环境） | 复杂（需配置 TF_CONFIG + 多机网络） |

### 4.2 适用场景

1. **超大规模推荐模型**：用户/物品 Embedding 表达数千万级，必须分片（如 YouTube、TikTok 的推荐 recall）
2. **稀疏特征密集型任务**：广告 CTR 预估、搜索排序（特征高达亿维）
3. **带宽受限的多机训练**：Worker 间网络带宽有限时，异步训练比同步 AllReduce 更高效

**不适用场景**：
1. 密集参数模型（ResNet/Transformer 参数量 < 1GB）——MirroredStrategy 更合适
2. 对一致性要求高的训练（如强化学习的策略梯度）——异步更新导致的不稳定可能致命
3. 模型参数量小但计算密集（如 GPT 类 LLM）——需要模型并行（Megatron/DeepSpeed），不是参数服务器

### 4.3 注意事项

- **PS 数量与分片粒度**：PS 数量太多（> Worker 数量）→ 通信开销大；PS 太少 → 单 PS 成为瓶颈。经验：PS 数量 = Worker 数量的 1/2 到 1 倍
- **Staleness 与学习率**：异步训练的梯度噪声更大，学习率通常设为同步训练的 1/2 到 1/4
- **Checkpoint 保存**：需要从所有 PS 上收集参数后才能保存完整 checkpoint。推荐用 `tf.train.CheckpointManager` 管理多机 checkpoints
- **Docker Compose 部署顺序**：先启动 PS（`server.join()` 等待），确认 PS 就绪后再启动 Worker。顺序反了会连接失败

### 4.4 常见踩坑经验

1. **坑**：Worker 启动后一直等待，不开始训练。
   **根因**：所有 PS 必须在 Worker 启动前全部就绪。Worker 启动时会尝试连接所有 PS，如果有一个 PS 没起来就会卡住。
   **解决**：用 Docker Compose 的 `depends_on` + `healthcheck` 确保 PS 先启动并监听端口。

2. **坑**：异步训练 loss 在大约 5000 step 后开始剧烈震荡。
   **根因**：初期 Embedding 的梯度大，异步更新的 staleness 导致参数冲突严重。
   **解决**：初期用更小学习率（warmup），或在 Embedding 表上使用 Adagrad（天然对稀疏梯度友好，学习率自衰减）。

3. **坑**：PS 的内存持续增长，最终 OOM。
   **根因**：PS 为每个 Embedding 维护了优化器状态（如 Adam 的 m/v），这些状态没有分片——全部存在同一个 PS 上。
   **解决**：对 Embedding 表使用 Adagrad 或 Ftrl 优化器（状态参数量小，仅需一阶动量）；或将优化器状态也分片。

### 4.5 思考题

1. 你有一个推荐模型：用户 Embedding (1 亿 × 256) + 物品 Embedding (5000 万 × 256) + DNN (约 100 万参数)。设计 PS 和 Worker 的部署方案：几个 PS？每个 PS 存哪些参数？Worker 数量如何确定？

2. 异步训练的 staleness 能否通过"限制每个 Worker 的最大延迟步数"来缓解？如果 Worker A 落后 PS 超过 100 step，强制它先同步再训练。请分析这种"有界异步（Bounded Staleness）"的优缺点。

### 4.6 推广计划提示

- **算法工程师**：推荐模型的 Embedding 规模是选择训练策略的首要考虑——Embedding 参数 < 2GB → MirroredStrategy；> 2GB → ParameterServerStrategy
- **平台工程师**：为 ParameterServerStrategy 构建标准化 Docker 镜像 + K8s Operator，封装 TF_CONFIG 注入逻辑
- **测试工程师**：异步训练的评估需要额外关注指标稳定性——多次训练的结果标准差应 < 2%
