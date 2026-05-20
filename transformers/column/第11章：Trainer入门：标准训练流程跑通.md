# 第11章：Trainer 入门：标准训练流程跑通

## 1 项目背景

### 业务场景

算法工程师小陈在上一章已经完成了数据清洗和切分，现在面临的问题是：如何把数据喂给模型、开始训练？第5章他用手写的训练循环跑通了分类模型，但代码长、易出错——忘记 `model.train()` 和 `model.eval()` 切换、手动写了 optimizer.step() 和 scheduler.step()、梯度清零时机搞反导致 loss 不收敛。更麻烦的是，他想加断点续训、自动保存最佳 checkpoint、wandb 可视化，每加一个功能就要改几十行代码。

与此同时，团队新入职的实习生小李需要训练一个情感分析模型。小陈把代码给他后，小李花了三天才跑通——因为 `device` 没有自动适配、`DataLoader` 参数没对齐、`learning_rate` 设错导致 loss 直接 NaN。

技术经理意识到，团队缺少一个标准化的训练框架。

### 痛点放大

手写训练循环虽然灵活，但在团队协作中存在致命缺陷：

1. **样板代码膨胀**：每个任务（分类/NER/QA）都需要类似的 `for epoch in range(epochs)` 循环，但细节参数（batch_size、lr、device）散落在各处
2. **功能缺失**：断点续训、梯度累积、混合精度训练、分布式训练——每加一个能力都意味着几十行难以调试的代码
3. **可复现性差**：换一台机器跑，忘记设置同样的 random seed，结果就不一样；同事拉着代码跑，缺了某个参数导致效果完全不同
4. **缺少实验管理**：训练了 10 组超参数，哪组效果最好？checkpoint 放在哪个目录？wandb/tensorboard 怎么接？

```
手写训练循环的痛点：

for epoch in range(epochs):           ← 每个项目都要写一遍
    model.train()                     ← 忘记切换导致评估不准
    for batch in dataloader:          ← 参数散落各处
        optimizer.zero_grad()         ← 时机搞反就白训
        loss = model(**batch).loss
        loss.backward()               ← 显存不够就 OOM
        optimizer.step()
        scheduler.step()              ← 忘记调用导致 lr 不变
    model.eval()                      ← 评估时忘记 no_grad
    ... 保存、日志、early_stop ...     ← 每加一个功能就改几十行
```

HuggingFace Trainer 正是为解决这些问题而设计的——它把训练循环中的各个环节抽象为标准化的钩子和配置，让你把精力放在模型和数据上，而非训练循环的样板代码。

---

## 2 项目设计

### 剧本式交锋对话

**场景**：周三下午 2:00，AI Lab。小陈在给实习生小李讲解训练代码，小李已经对着屏幕上的 200 行训练脚本看了半小时。

---

**小胖**（抱着一袋薯片）:"小李你咋看这么久还没跑？我当年学做饭，第一天就是番茄炒蛋，三分钟搞定。你这模型训练怎么跟满汉全席似的？"

**小李**:"小陈给的代码太长了。我就想训练一个简单的情感分类模型，结果要写 DataLoader、optimizer、scheduler、train/eval 循环，还有保存 checkpoint、记录日志……我自己改了一行参数，整个训练就不收敛了。"

**小胖**:"那你不能找个'一键做饭'的工具吗？就是把番茄炒蛋的步骤都封装好，你只需要告诉它要番茄还是鸡蛋就行了。"

**小白**（听到关键词抬起头）:"小胖说的就是 Trainer。Trainer 是 HuggingFace 提供的训练框架，它把训练循环封装成一个 `Trainer` 对象。你只需要提供四样东西：model、训练参数、训练数据、评估数据。然后 `.train()` 就好了。"

**小陈**:"但我有个疑问——Trainer 那么封装，如果我想改 loss 计算方式或者加入自定义的梯度操作怎么办？"

**大师**（放下咖啡杯走过来）:"问得好。Trainer 的设计哲学是：**80% 的场景开箱即用，20% 的场景通过钩子扩展**。

理解 Trainer 的三个核心层次：

**第一层：TrainingArguments。** 这是一个 dataclass，包含了训练所需的所有配置参数——batch size、epochs、learning rate、warmup steps、weight decay、保存策略、日志策略、混合精度……你不需要在代码里散落这些参数，全部集中在一个对象里。而且可以直接从命令行覆盖：`python train.py --learning_rate 3e-5 --num_train_epochs 10`。

**第二层：Trainer 主循环。** `.train()` 一行的背后是：数据加载 → 前向传播 → loss 计算 → 反向传播 → 梯度裁剪 → optimizer step → scheduler step → 日志记录 → 评估 → 保存 checkpoint。你不需要写任何一行循环代码。

**第三层：Callback 和可重写方法。** 如果要自定义行为——比如自定义 loss 计算，你重写 `compute_loss()` 方法；训练过程中做额外操作（如发送 Slack 通知），你写一个 `TrainerCallback` 子类；评估指标自定义，你传一个 `compute_metrics` 函数。Trainer 不是封闭的，它是一个'可扩展的框架'。

看个对比：手写训练循环 200 行 → Trainer 只需 30 行配置 + 1 行 `.train()`。"

**小胖**:"那 DataCollator 是干啥的？我看代码里有个 `DataCollatorWithPadding`。"

**大师**:"DataCollator 的核心作用是**把不同长度的样本拼成一个 batch**。因为 Transformer 要求所有输入长度一致，所以 batch 内的样本需要 padding 到相同长度。固定 max_length 太浪费——一个 batch 里最长样本 50 tokens、最短 10 tokens，padding 到 128 就浪费了 78 个位置。DataCollatorWithPadding 的做法是：每个 batch 动态地 padding 到该 batch 内的最大长度。"

**技术映射总结**：
- TrainingArguments = 训练配置的"总开关"，所有超参数集中管理
- Trainer.train() = 一键启动的"自动驾驶"，封装了完整的训练循环
- DataCollatorWithPadding = "智能打包员"，动态 padding 省显存
- Callback = "钩子函数"，在训练的各个阶段插入自定义逻辑

---

## 3 项目实战

### 3.1 环境准备

```bash
pip install transformers==4.44.0 datasets==2.21.0 evaluate==0.4.2
pip install scikit-learn>=1.3.0
```

### 3.2 Trainer 最小训练示例

#### 目标

用 Trainer 完成一个 30 行代码的情感分析训练。

```python
# trainer_minimal.py
"""Trainer 最小化训练示例 —— 30 行代码完成情感分类"""

from datasets import Dataset
from transformers import (
    AutoTokenizer, AutoModelForSequenceClassification,
    Trainer, TrainingArguments, DataCollatorWithPadding,
)
import numpy as np
from sklearn.metrics import accuracy_score

# 1. 准备数据（模拟 20 条）
data = {
    "text": [
        "这个产品质量太差了，非常失望", "物流很快，包装也很好",
        "客服态度很好，帮我解决了问题", "价格太贵，不值得购买",
        "用了三天就坏了，垃圾", "超级好用，推荐给大家",
        "一般般吧，没什么特别的", "性价比很高",
        "跟我预期的完全不一样", "质量不错，下次还来买",
    ] * 2,  # 重复一次凑够 20 条
    "label": [0, 1, 1, 0, 0, 1, 0, 1, 0, 1] * 2,  # 0=负面, 1=正面
}
dataset = Dataset.from_dict(data).train_test_split(test_size=0.2, seed=42)

# 2. 加载模型和 tokenizer
model_name = "bert-base-chinese"
tokenizer = AutoTokenizer.from_pretrained(model_name)
model = AutoModelForSequenceClassification.from_pretrained(
    model_name, num_labels=2
)

# 3. Tokenize
def tokenize_fn(examples):
    return tokenizer(examples["text"], truncation=True, max_length=128)

tokenized = dataset.map(tokenize_fn, batched=True)

# 4. 训练配置
training_args = TrainingArguments(
    output_dir="./output/minimal_trainer",
    per_device_train_batch_size=4,
    per_device_eval_batch_size=4,
    num_train_epochs=3,
    evaluation_strategy="epoch",
    save_strategy="epoch",
    logging_strategy="steps",
    logging_steps=2,
    load_best_model_at_end=True,
    metric_for_best_model="accuracy",
    report_to="none",
)

# 5. 评估指标
def compute_metrics(eval_pred):
    logits, labels = eval_pred
    preds = np.argmax(logits, axis=-1)
    return {"accuracy": accuracy_score(labels, preds)}

# 6. Trainer + DataCollator（动态 padding）
trainer = Trainer(
    model=model,
    args=training_args,
    train_dataset=tokenized["train"],
    eval_dataset=tokenized["test"],
    tokenizer=tokenizer,
    data_collator=DataCollatorWithPadding(tokenizer),
    compute_metrics=compute_metrics,
)

# 7. 训练！（就这一行）
print("开始训练...")
trainer.train()

# 8. 评估
results = trainer.evaluate()
print(f"\n最终结果: {results}")

# 9. 保存
trainer.save_model("./models/sentiment_minimal")
tokenizer.save_pretrained("./models/sentiment_minimal")
print("模型已保存")
```

运行输出示例：

```
开始训练...
{'loss': 0.6873, 'learning_rate': 1.33e-05, 'epoch': 1.0}
{'eval_loss': 0.5124, 'eval_accuracy': 0.75, 'epoch': 1.0}
{'loss': 0.4231, 'learning_rate': 6.67e-06, 'epoch': 2.0}
{'eval_loss': 0.3102, 'eval_accuracy': 0.875, 'epoch': 2.0}
{'loss': 0.2156, 'learning_rate': 0.0, 'epoch': 3.0}
{'eval_loss': 0.1987, 'eval_accuracy': 1.0, 'epoch': 3.0}

最终结果: {'eval_loss': 0.1987, 'eval_accuracy': 1.0}
模型已保存
```

### 3.3 TrainingArguments 核心参数详解

```python
# training_args_guide.py
"""TrainingArguments 关键参数详解"""

from transformers import TrainingArguments

args = TrainingArguments(
    output_dir="./output/demo",          # 输出目录（checkpoint、日志）

    # ===== 训练控制 =====
    num_train_epochs=3,                  # 训练轮数（与 max_steps 二选一）
    # max_steps=1000,                    # 最大步数（与 epoch 二选一）

    # ===== Batch 大小 =====
    per_device_train_batch_size=8,       # 每 GPU 的训练 batch size
    per_device_eval_batch_size=16,       # 每 GPU 的评估 batch size
    gradient_accumulation_steps=2,       # 梯度累积步数（等价于 batch_size*2）

    # ===== 学习率 =====
    learning_rate=2e-5,                  # 学习率
    weight_decay=0.01,                   # 权重衰减
    warmup_ratio=0.1,                    # warmup 占比（前 10% 步线性增加 lr）
    # warmup_steps=500,                  # warmup 步数（与 ratio 二选一）
    lr_scheduler_type="linear",          # 调度器类型: linear/cosine/constant

    # ===== 混合精度 =====
    fp16=True,                           # 混合精度训练（仅 CUDA，省显存加快）
    # bf16=True,                         # BF16（A100/H100 等新 GPU）
    # tf32=True,                         # TF32（Ampere+ 架构）

    # ===== 保存策略 =====
    save_strategy="epoch",               # 保存时机: no/steps/epoch
    save_steps=500,                      # 每 N 步保存（与 save_strategy 配合）
    save_total_limit=2,                  # 最多保留 N 个 checkpoint

    # ===== 评估策略 =====
    evaluation_strategy="epoch",         # 评估时机: no/steps/epoch
    eval_steps=500,                      # 每 N 步评估
    load_best_model_at_end=True,         # 训练结束后加载最佳 checkpoint
    metric_for_best_model="accuracy",    # 最佳模型选择指标
    greater_is_better=True,              # 指标越大越好

    # ===== 日志 =====
    logging_strategy="steps",            # 日志记录时机
    logging_steps=50,                    # 每 N 步记录一次
    report_to="tensorboard",             # 上报到: tensorboard/wandb/mlflow/none

    # ===== 其他 =====
    seed=42,                             # 随机种子（可复现）
    dataloader_num_workers=2,            # DataLoader 工作线程数
    remove_unused_columns=True,          # 自动移除模型不需要的列
    ddp_find_unused_parameters=False,    # 分布式训练优化

    # ===== 训练参数覆盖示例 =====
    # 命令行覆盖: python train.py --learning_rate 5e-5 --num_train_epochs 5
)
```

### 3.4 自定义 Trainer

```python
# custom_trainer.py
"""自定义 Trainer: 重写 loss、添加 Callback"""

import torch
from transformers import Trainer, TrainerCallback
import numpy as np


class CustomTrainer(Trainer):
    """自定义 Trainer —— 加入类别权重和自定义 loss"""

    def __init__(self, class_weights=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.class_weights = class_weights

    def compute_loss(self, model, inputs, return_outputs=False):
        """
        重写 loss 计算，加入类别权重
        解决分类任务中标签分布不均的问题
        """
        labels = inputs.pop("labels")
        outputs = model(**inputs)

        if self.class_weights is not None:
            # 带权重的 CrossEntropyLoss
            loss_fct = torch.nn.CrossEntropyLoss(
                weight=torch.tensor(self.class_weights, device=labels.device)
            )
            loss = loss_fct(outputs.logits, labels)
        else:
            loss = outputs.loss

        return (loss, outputs) if return_outputs else loss


class LoggingCallback(TrainerCallback):
    """自定义 Callback: 训练过程中打印更多信息"""

    def on_log(self, args, state, control, logs=None, **kwargs):
        """每次记录日志时触发"""
        if logs and "loss" in logs:
            current_lr = logs.get("learning_rate", None)
            lr_str = f", lr={current_lr:.2e}" if current_lr else ""
            print(f"  [Step {state.global_step}] loss={logs['loss']:.4f}{lr_str}")

    def on_epoch_end(self, args, state, control, **kwargs):
        """每个 epoch 结束时触发"""
        print(f"\n--- Epoch {state.epoch:.0f} 完成 ---")

    def on_train_end(self, args, state, control, **kwargs):
        """训练结束时触发"""
        print(f"\n训练完成！总共 {state.global_step} 步, "
              f"耗时 {state.epoch:.2f} epochs")


class EarlyStoppingOnMetricCallback(TrainerCallback):
    """自定义早停: 连续 N 次评估无改善则停止"""

    def __init__(self, patience=3, threshold=0.001):
        self.patience = patience
        self.threshold = threshold
        self.best_metric = None
        self.wait = 0

    def on_evaluate(self, args, state, control, metrics=None, **kwargs):
        if metrics is None:
            return

        current_metric = metrics.get("eval_accuracy", 0)

        if self.best_metric is None or current_metric > self.best_metric + self.threshold:
            self.best_metric = current_metric
            self.wait = 0
        else:
            self.wait += 1
            if self.wait >= self.patience:
                print(f"早停触发！验证指标 {self.wait} 轮未提升。")
                control.should_training_stop = True


# ===== 使用示例 =====
if __name__ == "__main__":
    from datasets import Dataset
    from transformers import (
        AutoTokenizer, AutoModelForSequenceClassification,
        TrainingArguments, DataCollatorWithPadding,
    )

    # 模拟数据
    data = {
        "text": ["好", "差", "还行", "垃圾", "不错"] * 4,
        "label": [1, 0, 1, 0, 1] * 4,
    }
    ds = Dataset.from_dict(data).train_test_split(test_size=0.2, seed=42)

    tokenizer = AutoTokenizer.from_pretrained("bert-base-chinese")
    model = AutoModelForSequenceClassification.from_pretrained(
        "bert-base-chinese", num_labels=2
    )

    def tokenize_fn(examples):
        return tokenizer(examples["text"], truncation=True, max_length=128)

    tokenized = ds.map(tokenize_fn, batched=True)

    args = TrainingArguments(
        output_dir="./output/custom_trainer",
        per_device_train_batch_size=4,
        num_train_epochs=5,
        evaluation_strategy="epoch",
        save_strategy="no",
        report_to="none",
    )

    trainer = CustomTrainer(
        class_weights=[1.0, 2.0],  # 类别1权重更高
        model=model,
        args=args,
        train_dataset=tokenized["train"],
        eval_dataset=tokenized["test"],
        tokenizer=tokenizer,
        data_collator=DataCollatorWithPadding(tokenizer),
        callbacks=[
            LoggingCallback(),
            EarlyStoppingOnMetricCallback(patience=2),
        ],
    )

    trainer.train()
```

### 3.5 测试验证

```python
# test_trainer.py
import pytest
import os
from transformers import AutoModelForSequenceClassification

MODEL_PATH = "./models/sentiment_minimal"

@pytest.mark.skipif(not os.path.exists(MODEL_PATH), reason="模型未训练")
class TestTrainerOutput:
    def test_model_saved(self):
        assert os.path.exists(f"{MODEL_PATH}/config.json")
        assert os.path.exists(f"{MODEL_PATH}/model.safetensors") or \
               os.path.exists(f"{MODEL_PATH}/pytorch_model.bin")

    def test_model_loads(self):
        model = AutoModelForSequenceClassification.from_pretrained(MODEL_PATH)
        assert model is not None

    def test_prediction_works(self):
        import torch
        from transformers import AutoTokenizer
        tokenizer = AutoTokenizer.from_pretrained(MODEL_PATH)
        model = AutoModelForSequenceClassification.from_pretrained(MODEL_PATH)
        inputs = tokenizer("这个产品很好", return_tensors="pt")
        with torch.no_grad():
            outputs = model(**inputs)
        assert outputs.logits.shape == (1, 2)
```

---

## 4 项目总结

### 4.1 优点与缺点

| 方面 | 优点 | 缺点 |
|------|------|------|
| **开箱即用** | 一行 `.train()` 替代 200 行样板代码 | 高度封装导致训练内部细节不透明 |
| **TrainingArguments** | 所有超参数集中管理，支持命令行覆盖 | 参数数量多（100+），新手不知哪些是关键 |
| **动态 padding** | DataCollator 按 batch 内最长样本 padding，省显存 | 动态长度导致每个 batch 计算量不同，影响 GPU 利用率稳定性 |
| **Callback 扩展** | 在训练各阶段注入自定义逻辑，灵活不侵入 | Callback 执行顺序有时不直观（如 on_save 和 on_evaluate 的先后） |

### 4.2 适用场景

| 场景 | 推荐方案 |
|------|---------|
| 标准分类/NER/QA 微调 | Trainer + AutoModel + DataCollator |
| 需要断点续训 | `TrainingArguments(resume_from_checkpoint=True)` |
| 类别不平衡 | 重写 `compute_loss()` 加入 class_weight |
| 自定义训练逻辑 | 继承 Trainer 重写 `training_step()` 或 `compute_loss()` |
| 实验追踪 | `report_to="wandb"` 或 `report_to="tensorboard"` |

**不适用场景**：
- 需要自定义优化器/调度器交互的复杂训练（如 GAN 的交替训练）
- RLHF（人类反馈强化学习）等多阶段训练，需要写自定义训练循环或用 TRL 库

### 4.3 注意事项

1. **`load_best_model_at_end=True` 需要 `save_strategy` 与 `evaluation_strategy` 一致**：必须相同才能在每个评估点保存
2. **`remove_unused_columns=True`（默认）会删除模型 forward 不需要的列**：如果模型自定义了额外的输入字段，必须设为 False
3. **`gradient_accumulation_steps` 与 `per_device_train_batch_size` 的乘积** = 有效 batch size，影响 batch norm 行为和 loss 收敛

### 4.4 常见踩坑经验

| 故障现象 | 根因 | 解决方案 |
|---------|------|---------|
| `KeyError: 'labels'` | dataset 中没有 `labels` 列 | 确保 tokenize 后的 dataset 包含 `labels` 字段 |
| 训练不收敛 | learning rate 过大（默认 5e-5 对某些小数据集太高） | 降低 lr 到 2e-5 或 1e-5 |
| 评估时 OOM | eval_batch_size 过大 | 降低 `per_device_eval_batch_size` |
| 训练到一半报 `CUDA error` | 某个 batch 尺寸异常（超长文本） | 检查 tokenize 时 `max_length` 设置 |

### 4.5 思考题

1. **初级**：在 `TrainingArguments` 中，`per_device_train_batch_size=4` + `gradient_accumulation_steps=4` 的有效 batch size 是多少？与直接设 `per_device_train_batch_size=16` 有什么区别？
2. **进阶**：Trainer 默认使用 AdamW 优化器。如果想换成 SGD + Momentum，该如何修改？（提示：查看 Trainer 的 `create_optimizer()` 方法）

（答案将在第12章末尾给出）

### 4.6 第10章思考题答案

**第10章思考题1**：
- `streaming=True` 时返回的是 `IterableDataset`，不支持 `len()` 调用，会抛出 `TypeError`。因为流式模式下数据从磁盘逐条读取，无法预知总数（除非数据源在读取前声明了行数）。

**第10章思考题2**：
- 增量处理方案：(1) 每周新数据单独命名文件（如 `tickets_2024W03.jsonl`）；(2) 用 `load_dataset("json", data_files="tickets_2024W03.jsonl")` 加载新数据；(3) 对新数据调用预处理函数 `new_data.map(clean_fn)`；(4) 用 `concatenate_datasets([old_dataset, new_data])` 合并；(5) 保存为新的版本 `dataset.save_to_disk("v3")`。利用 Arrow 的内存映射，旧数据不会重复占用内存。

### 4.7 推广计划提示

| 部门 | 建议行动 |
|------|---------|
| **开发团队** | 统一使用 Trainer 作为训练入口，将训练配置沉淀为 `config/train_config.yaml` 模板 |
| **测试团队** | 在 CI 中运行 `trainer_minimal.py` 确保训练流程正常，作为冒烟测试 |
| **运维团队** | 理解 `fp16` / `bf16` 的显存开销差异，为 GPU 集群的资源调度提供参考 |

---

> **下一章预告**：第12章将深入模型评估——Accuracy 高就代表模型好吗？F1、AUC、混淆矩阵该怎么解读？如何进行错误分析定位数据问题？
