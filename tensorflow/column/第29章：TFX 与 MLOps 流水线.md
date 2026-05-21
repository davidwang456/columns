# 第29章：TFX 与 MLOps 流水线

## 1. 项目背景

某金融科技公司有 4 个算法工程师，各自维护着风控、信用评分、反欺诈、客户流失预测 4 个模型。每个模型的训练流程都是：手动从 Hive 拉数据 → Jupyter Notebook 上清洗 → 手工调参训练 → 导出模型 → 发邮件给后端部署。这个"手工作坊"模式很快暴露了五个痛点：

- **不可复现**：小李的风控模型上个月 AUC=0.85，这个月重新训练 AUC=0.81——她记不清上次用了哪个版本的数据
- **数据质量问题发现滞后**：一个字段的缺失率从 2% 涨到 30%，直到模型上线后效果暴跌才发现
- **部署流程混乱**：老张把 `.h5` 文件直接发到微信群里，后端工程师不知道这是训练好的模型还是中间实验品
- **审计困难**：监管要求"模型上线必须经过数据验证和指标门禁审批"，但团队没有任何记录证明他们做了这些检查
- **重复劳动**：4 个人各自写了 4 套数据清洗代码，逻辑基本相同但 API 不统一

**痛点放大**：MLOps 不是"用 Docker 跑训练"——它是一条标准化、可复用、可审计的流水线（Pipeline），覆盖数据导入→数据验证→特征工程→训练→评估→发布的完整链路。TFX（TensorFlow Extended）是 Google 内部的 MLOps 平台在 TensorFlow 生态中的开源实现。

## 2. 项目设计

**小胖**（看着满桌的 Jupyter Notebook）：我们不就是这么训练的吗？数据拉下来 → Notebook 里跑跑 → 效果 OK 就部署。多简单！搞 TFX 这么重的框架，是不是有点大炮打蚊子？

**大师**：你们现在的做法确实简单——但简单在"当下"，复杂在"未来"。我问你三个问题：第一，3 个月后你能复现今天这个模型吗？第二，如果数据里某个字段突然全是 NULL，你的 Notebook 会自动发现并停止训练吗？第三，监管要你证明"模型的训练数据经过验证"，你能在 5 分钟内拿出证据吗？

**小胖**（沉默）……不能。

**大师**：这就是 TFX 的价值——把机器学习从"工匠手艺"变成"工业流水线"。TFX 用一系列标准化的组件（Building Blocks），把数据导入、验证、转换、训练、评估、发布串联起来。每个组件的输出都有 Metadata（元数据）记录，可以实现端到端的可追溯。

**技术映射**：TFX 的核心理念是将 ML 工作流组件化 + 元数据管理。每个组件稳定独立，组件间通过 ML Metadata 共享信息，形成可审计、可复用的流水线。

**小白**：TFX 有哪些核心组件？每个做什么的？

**大师**（在面前摆出 7 张便签）：

| 组件 | 做什么 | 输入 | 输出 |
|------|--------|------|------|
| **ExampleGen** | 从数据源导入数据（CSV/BigQuery/Hive...） | 原始数据 | tf.Examples |
| **StatisticsGen** | 自动生成数据统计报告 | tf.Examples | 数据统计 |
| **SchemaGen** | 推断数据的 Schema（字段类型/值范围） | 统计报告 | Schema 定义 |
| **ExampleValidator** | 检测数据异常（缺失率变化/分布漂移） | 统计 + Schema | 异常报告 |
| **Transform** | 特征工程（归一化/分桶/词表） | tf.Examples | 转换图 + 转换后数据 |
| **Trainer** | 训练模型 | 转换后数据 | SavedModel |
| **Evaluator** | 验证模型指标是否达标（"门禁"） | SavedModel + 验证集 | 通过/驳回 |
| **Pusher** | 将达标的模型发布到部署目标 | SavedModel | 发布到 Serving 目录 |

**大师**：看——每个组件做一件事，就像工厂流水线的每一台机器。ExampleGen 是"卸货区"、StatisticsGen+SchemaGen+ExampleValidator 是"质检站"、Transform 是"切割打磨"、Trainer 是"组装"、Evaluator 是"最终质检"、Pusher 是"发货"。

**技术映射**：TFX 采用 DAG（有向无环图）编排组件，数据在组件间流转，每个组件的输出被 ML Metadata 记录——这样你可以精确地知道"这个模型是用哪个版本的数据、经过哪些变换、在哪一步被驳回的"。

**小白**：那 TFX 跟 Airflow / Kubeflow 是什么关系？TFX 是替代 Airflow 的还是基于它的？

**大师**：TFX 定义的是"做什么"（What）——组件及其逻辑。Airflow / Kubeflow 定义的是"怎么跑"（How）——任务调度和执行。它们是配合关系：TFX 生成一个 Pipeline 对象，Airflow/Kubeflow 负责定时触发、分布式执行、失败重试。

常见的编排选择：
- **本地开发/测试**：`tfx.orchestration.LocalDagRunner`（不需要 Airflow）
- **生产级定时调度**：TFX + Airflow（适合已有 Airflow 的团队）
- **K8s 原生**：TFX + Kubeflow Pipelines（适合 K8s 生态）
- **Google Cloud**：Vertex AI Pipelines（全托管）

**小胖**：那 ML Metadata 是什么？为什么每个组件都要记录它？

**大师**：ML Metadata 就是这条流水线的"黑匣子"——它记录了每一次运行中每个组件的输入、输出、参数、执行时间。这解决了三个关键问题：(1) 可复现——你可以找到"上次训练用的数据集 hash"；(2) 可比较——你可以对比"这两次训练的区别在哪个组件"；(3) 可审计——你可以证明"模型经过了 ExampleValidator 和 Evaluator 的双重门禁"。

## 3. 项目实战

### 3.1 环境准备

```bash
# TFX 核心包（含本地运行器）
pip install tfx==1.15.1 tensorflow==2.16.1

# 可视化相关
pip install tensorflow-model-analysis tensorflow-data-validation
```

### 3.2 分步实现

**步骤一：构建最小 TFX Pipeline（含 ExampleGen → Trainer → Pusher）**

目标：搭建一个能跑通的 TFX 流水线，理解各组件之间的数据流转。

```python
# pipeline_simple.py —— 最小 TFX 流水线
import os
import tempfile
from tfx import v1 as tfx

# 1. 准备数据（CSV → 流水线输入）
import pandas as pd
import numpy as np

data_dir = tempfile.mkdtemp()
csv_path = os.path.join(data_dir, "transactions.csv")

# 生成模拟交易数据
np.random.seed(42)
n = 5000
df = pd.DataFrame({
    "amount": np.random.uniform(1, 5000, n),
    "age": np.random.randint(18, 80, n),
    "tx_count_7d": np.random.poisson(3, n),
    "is_fraud": np.random.choice([0, 1], n, p=[0.98, 0.02]),
})
df.to_csv(csv_path, index=False)
print(f"数据已生成: {csv_path}")
print(f"样本数: {len(df)}, 欺诈率: {df['is_fraud'].mean():.2%}")

# 2. 定义 Pipeline
pipeline_root = os.path.join(tempfile.mkdtemp(), "tfx_pipeline")
serving_model_dir = os.path.join(tempfile.mkdtemp(), "serving_models")

# TFX 组件定义
def create_pipeline(pipeline_name, pipeline_root, data_root, serving_dir):
    # 数据导入
    example_gen = tfx.components.CsvExampleGen(input_base=data_root)

    # 数据统计
    statistics_gen = tfx.components.StatisticsGen(
        examples=example_gen.outputs["examples"]
    )

    # Schema 推断
    schema_gen = tfx.components.SchemaGen(
        statistics=statistics_gen.outputs["statistics"]
    )

    # 数据验证
    example_validator = tfx.components.ExampleValidator(
        statistics=statistics_gen.outputs["statistics"],
        schema=schema_gen.outputs["schema"],
    )

    # 训练器（内联一个简单模型）
    trainer = tfx.components.Trainer(
        module_file=_create_trainer_module(),
        examples=example_gen.outputs["examples"],
        schema=schema_gen.outputs["schema"],
        train_args=tfx.proto.TrainArgs(num_steps=100),
        eval_args=tfx.proto.EvalArgs(num_steps=50),
    )

    # 推送器
    pusher = tfx.components.Pusher(
        model=trainer.outputs["model"],
        push_destination=tfx.proto.PushDestination(
            filesystem=tfx.proto.PushDestination.Filesystem(
                base_directory=serving_dir
            )
        ),
    )

    # 组装 Pipeline
    components = [
        example_gen, statistics_gen, schema_gen,
        example_validator, trainer, pusher,
    ]

    return tfx.dsl.Pipeline(
        pipeline_name=pipeline_name,
        pipeline_root=pipeline_root,
        components=components,
        enable_cache=True,  # 开启缓存，避免重复计算
    )

def _create_trainer_module():
    """动态创建一个 Trainer 模块文件"""
    module_content = """
import tensorflow as tf
from tensorflow import keras
import tensorflow_transform as tft

def _input_fn(file_pattern, batch_size=128):
    # 读取 TFRecord
    dataset = tf.data.TFRecordDataset(tf.io.gfile.glob(file_pattern))
    # 简化的解析逻辑
    def _parse(proto):
        features = {
            "amount": tf.io.FixedLenFeature([], tf.float32),
            "age": tf.io.FixedLenFeature([], tf.int64),
            "tx_count_7d": tf.io.FixedLenFeature([], tf.int64),
            "is_fraud": tf.io.FixedLenFeature([], tf.int64),
        }
        parsed = tf.io.parse_single_example(proto, features)
        label = tf.cast(parsed.pop("is_fraud"), tf.float32)
        # 拼接特征
        feat_list = [tf.cast(v, tf.float32) for v in parsed.values()]
        return tf.stack(feat_list, axis=-1), label

    return dataset.map(_parse).batch(batch_size).repeat().prefetch(tf.data.AUTOTUNE)

def run_fn(fn_args):
    model = keras.Sequential([
        keras.layers.Dense(32, activation="relu", input_shape=(3,)),
        keras.layers.Dense(16, activation="relu"),
        keras.layers.Dense(1, activation="sigmoid"),
    ])
    model.compile(optimizer=keras.optimizers.Adam(1e-3),
                  loss="binary_crossentropy",
                  metrics=["accuracy", keras.metrics.AUC()])
    model.fit(
        _input_fn(fn_args.train_files),
        steps_per_epoch=fn_args.train_steps,
        validation_data=_input_fn(fn_args.eval_files),
        validation_steps=fn_args.eval_steps,
        epochs=1,
    )
    model.save(fn_args.serving_model_dir, save_format="tf")
"""
    import tempfile
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False)
    f.write(module_content)
    f.close()
    return f.name

# 3. 运行 Pipeline
pipeline = create_pipeline("fraud_detection_pipeline", pipeline_root,
                            data_dir, serving_model_dir)

# 使用本地运行器执行
tfx.orchestration.LocalDagRunner().run(pipeline)

print(f"\nPipeline 完成！")
print(f"  Pipeline root: {pipeline_root}")
print(f"  模型 Serving 目录: {serving_model_dir}")
```

**步骤二：组件详解——从 ExampleGen 到 SchemaGen**

目标：深入理解数据统计和 Schema 的生成与验证。

```python
# 各组件输出说明
print("""
=== TFX 组件数据流 ===

ExampleGen (CsvExampleGen):
  输入: CSV 文件目录
  输出: TFRecord 格式的 tf.Examples
  目录: {pipeline_root}/CsvExampleGen/examples/

StatisticsGen:
  输入: tf.Examples
  输出: 数据统计 protobuf (每个特征的分布、缺失率、百分位数等)
  目录: {pipeline_root}/StatisticsGen/statistics/

  === 统计报告包含的信息 ===
  - 每个特征的 count / missing / mean / std / min / max
  - 数值特征的分位数 (median, P1, P5, P25, P75, P95, P99)
  - 分类特征的 top-K 频次
  - 特征之间的相关性热力图

SchemaGen:
  输入: 统计报告
  输出: Schema protobuf (字段类型/值域/必需性)
  内容示例:
    feature { name: "amount" type: FLOAT }
    feature { name: "age" type: INT domain { min: 18 max: 80 } }
    feature { name: "is_fraud" type: INT domain { min: 0 max: 1 } }

ExampleValidator:
  输入: 统计报告 + Schema
  输出: 异常报告
  检测内容:
  - 训练集 vs 验证集的分布差异
  - 新数据中缺失值是否超出预期范围
  - 是否有特征的值超出了 Schema 定义的范围
""")
```

**步骤三：Evaluator 质量门禁**

目标：理解如何在 TFX 中设置"指标不达标则阻止发布"的质量门禁。

```python
# Evaluator 的 "blessing" 机制
print("""
=== TFX Evaluator 质量门禁 ===

Evaluator 通过 "Blessing（赐福）" 机制实现门禁:
  1. Trainer 产出模型
  2. Evaluator 用验证集评估模型
  3. 如果指标达标 → BLESSED (赐福通过)
  4. 如果指标不达标 → NOT_BLESSED (拒绝)
  5. Pusher 只推送 BLESSED 的模型

Evaluator 配置示例:
  eval_config = tfma.EvalConfig(
    model_specs=[tfma.ModelSpec(label_key="is_fraud")],
    metrics_specs=[
      tfma.MetricsSpec(
        metrics=[
          tfma.MetricConfig(class_name="AUC"),
          tfma.MetricConfig(class_name="BinaryAccuracy"),
        ],
        thresholds={
          "auc": tfma.MetricThreshold(
            value_threshold=tfma.GenericValueThreshold(
              lower_bound={"value": 0.75}  # AUC ≥ 0.75 才通过
            )
          ),
        },
      )
    ],
    slicing_specs=[
      tfma.SlicingSpec(),  # 全量
      tfma.SlicingSpec(feature_keys=["age_bucket"]),  # 按年龄段切片
    ],
  )

Pusher 配置:
  pusher = tfx.components.Pusher(
    model=trainer.outputs["model"],
    model_blessing=evaluator.outputs["blessing"],
    push_destination=...,
  )
  # Pusher 会检查 blessing:
  # - BLESSED → 推送到生产目录
  # - NOT_BLESSED → 跳过（不推送）
""")
```

**步骤四：与 Airflow 集成概念**

目标：理解如何将 TFX Pipeline 部署到 Airflow 定时运行。

```python
# Airflow DAG 集成
print("""
=== TFX + Airflow 集成 ===

from tfx.orchestration import airflow as tfx_airflow

# 生产环境：用 Airflow 编排
airflow_dag = tfx_airflow.TfxDagRunner(
    config=tfx_airflow.AirflowPipelineConfig(
        pipeline=pipeline,
        # 每天凌晨 2 点运行
        schedule_interval="@daily",
        start_date=datetime(2025, 1, 1),
    )
).run()

# Airflow DAG 文件结构
# dags/
#   fraud_detection_pipeline.py  ← 这个文件
#   config.py                    ← 流水线配置
#   trainer_module.py            ← 模型训练逻辑

=== 团队角色分工 ===
┌────────────┬────────────────────────────────┐
│ 算法工程师  │ 编写 Transform/Trainer/Evaluator │
│ 平台工程师  │ 维护 Airflow/Kubeflow 编排环境    │
│ 测试工程师  │ 维护 ExampleValidator 的异常规则   │
│ 运维工程师  │ 维护 Pusher 目标 + Serving 环境    │
└────────────┴────────────────────────────────┘
""")
```

### 3.3 TFX 流水线架构图

```
┌───────────────────────────────────────────────────────────┐
│                     ML Metadata (记录一切)                  │
├───────────────────────────────────────────────────────────┤
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐     │
│  │ExampleGen│→│Statistics│→│SchemaGen │→│ExampleV  │     │
│  │ 数据导入 │ │   Gen    │ │ Schema   │ │ alidator │     │
│  │          │ │ 数据统计 │ │  推断    │ │ 异常检测 │     │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘     │
│       ↓                                           ↓       │
│  ┌──────────┐                               (异常报告)    │
│  │ Transform│                                              │
│  │ 特征工程 │                                              │
│  └──────────┘                                              │
│       ↓                                                    │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐                  │
│  │ Trainer  │→│Evaluator │→│ Pusher   │                  │
│  │  训练    │ │ 模型评估 │ │ 模型发布 │                  │
│  │          │ │  门禁    │ │          │                  │
│  └──────────┘ └──────────┘ └──────────┘                  │
│                     ↓ 指标不达标?                          │
│                   NOT_BLESSED → 阻止发布                   │
└───────────────────────────────────────────────────────────┘
```

## 4. 项目总结

### 4.1 TFX 各组件优缺点

| 组件 | 优点 | 缺点 |
|------|------|------|
| **ExampleGen** | 支持多种数据源（CSV/BigQuery/Kafka） | 自定义数据源需实现 Executor |
| **StatisticsGen + SchemaGen** | 自动生成全面数据报告 | 默认配置生成的 Schema 过于宽松（需手动收紧约束） |
| **ExampleValidator** | 自动检测数据异常 + 漂移 | 规则需持续维护；过于严格容易误报阻断 |
| **Transform** | 特征工程与训练解耦，保证一致性 | 全量数据 Transform 对大数据集慢 |
| **Trainer** | 集成 Keras + 自定义训练循环 | 调试困难（在容器内运行） |
| **Evaluator + Pusher** | Blessing 门禁 + 自动发布 | 门禁规则定义不够灵活 |

### 4.2 适用场景

1. **金融/医疗等强审计需求**：ML Metadata 保障全程可追溯
2. **多团队协作**：标准化流水线，团队间复用组件
3. **定时自动训练**：Airflow/Kubeflow 每天自动触发重训练
4. **数据质量敏感**：ExampleValidator 在训练前拦截脏数据
5. **频繁发布模型**：Pusher 自动推送到 Serving，无需人工介入

**不适用场景**：
1. 实验探索期（模型还没定型）——TFX 的配置成本偏高，先用 Notebook 快速迭代
2. 模型极简单（一个 CSV → Dense → 部署）——整套 TFX 是 over-engineering
3. 需要极致灵活的编排——TFX 的 DAG 模型不如 Prefect/Dagster 灵活

### 4.3 注意事项

- **Transform 和 Trainer 共享特征逻辑**：Transform 的输出（`transform_graph`）必须在 Trainer 中加载，确保训练和推理时的特征处理完全一致。不一致 = 训练-推理 skew
- **Evaluator 的 slicing（切片评估）**：只评估全量准确率不够——必须按关键维度（如年龄段/地区/时间）切片评估，确保每个切片都不差
- **ML Metadata 数据库的选择**：开发环境用 SQLite（零配置），生产环境用 MySQL（支持多流水线并发）
- **Caching 的坑**：TFX 默认开启缓存——如果改了 Trainer 代码但没改输入数据，可能会使用缓存的旧模型（需手动 invalidate cache）

### 4.4 常见踩坑经验

1. **坑**：ExampleValidator 一直报"训练/验证数据分布漂移"，但实际数据没变。
   **根因**：验证集是从训练集末尾切出来的（时间顺序），两个时期的分布确实有自然差异（如 11 月 vs 12 月的消费行为不同）。
   **解决**：放宽 ExampleValidator 的阈值，或将"时间周期差异"加入期望的 drift 范围。

2. **坑**：Trainer 在 TFX 中运行与本地 Notebook 结果不同。
   **根因**：Transform 组件的特征转换逻辑（如归一化参数）是在全量训练集上 fit 的——Notebook 中手动写的归一化和 Transform 的输出可能不一致。
   **解决**：始终用 Transform 的 `tft.scale_to_z_score` 等 API，不要手动 `(x-mean)/std`。

3. **坑**：Pusher 没有推送模型，但 Trainer 和 Evaluator 都成功了。
   **根因**：Evaluator 的 blessing 是 NOT_BLESSED（指标未达到阈值），但日志信息不够明显。
   **解决**：在流水线中增加一个"Notify"步骤——不管 blessing 结果都发一条消息（通过/驳回 + 具体指标值）。

### 4.5 思考题

1. 你的 TFX Pipeline 每天凌晨 2 点运行，但某天 Hive 数据源的某个分区为空（周末无交易）——ExampleGen 导入了 0 条记录。后续组件会怎么处理？你如何让 Pipeline 在这种情况下优雅终止并发送告警？

2. TFX 的 Trainer 组件默认使用 GenericExecutor（在容器中运行 trainer_module）。如果你需要训练一个超大模型（需要 8 卡 GPU），Trainer 如何利用分布式环境？请设计 Trainer + K8s + GPU 的集成方案。

### 4.6 推广计划提示

- **新人开发**：先用 `LocalDagRunner` 在本地跑通最小 Pipeline（ExampleGen + Trainer + Pusher），理解组件间数据流
- **平台工程师**：搭建 TFX + Airflow/Kubeflow 的标准化容器镜像和 K8s 部署模板
- **算法工程师**：将特征工程逻辑从 Notebook 迁移到 TFX Transform 组件中，实现训练-推理一致性
