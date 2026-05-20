# 第37章：Trainer、Callback 与训练循环源码

## 1 项目背景

### 业务场景

算法团队在客服工单分类任务的训练过程中遇到了一个奇怪的问题：训练 loss 一直在正常下降，但验证集 F1 在第 2 个 epoch 后突然从 0.89 跌到 0.62，之后一直震荡不恢复。更奇怪的是，Trainer 的 `EarlyStoppingCallback` 没有触发——因为它是基于 `eval_loss` 判断的，而 `eval_loss` 仍在缓慢下降。

小陈需要在训练过程中同时监控 F1——当 F1 连续 3 个 epoch 没有提升时提前停止训练。此外，还需要在指标异常时发送钉钉告警。这要求他深入理解 Trainer 的 Callback 机制。

另一个需求是自定义 loss：由于投诉类工单的标签极度稀疏（仅占 5%），标准的 CrossEntropyLoss 会让模型忽略投诉类。小陈需要在训练时给类别加权。

### 痛点放大

Trainer 虽然封装良好，但一旦需要自定义行为，就必须理解其内部机制：

```
Trainer.train()
  ├── _inner_training_loop()
  │   ├── for epoch in epochs:
  │   │   ├── for step, batch in dataloader:
  │   │   │   ├── training_step(batch)   ← 可重写
  │   │   │   │   ├── model(**batch)      ← forward
  │   │   │   │   ├── compute_loss()      ← 可重写
  │   │   │   │   └── backward() + step()
  │   │   │   └── callbacks on_step_end   ← 扩展点
  │   │   ├── evaluate()                  ← 可重写
  │   │   └── callbacks on_epoch_end     ← 扩展点
  │   └── callbacks on_train_end          ← 扩展点
```

---

## 2 项目设计

### 剧本式交锋对话

**场景**：周五下午 3:00，AI Lab。小陈在改 Trainer 的源码，屏幕上是密密麻麻的 Callback 代码。

---

**小胖**:"你怎么又在改 Trainer 了？不是说好了 Trainer 开箱即用吗？"

**小陈**:"开箱即用是指 80% 的场景。我们这 20%——自定义 class_weight、自定义早停指标、训练过程发钉钉告警——都得靠 Callback 和重写方法。"

**小白**:"Callback 机制是什么设计模式？观察者模式？我好奇 Trainer 是怎么保证 Callback 的执行时机和顺序的。"

**大师**:"Callbacks 是 Trainer 最精妙的设计。让我拆解 Trainer 的三个核心扩展点。

**扩展点一：Callback 系统。** `TrainerCallback` 定义了训练生命周期中的挂钩点：

- `on_train_begin` → 训练开始前（初始化日志、加载 checkpoint）
- `on_epoch_begin` / `on_epoch_end` → 每个 epoch 的起止
- `on_step_begin` / `on_step_end` → 每个 step 的起止
- `on_log` → 记录日志时触发
- `on_evaluate` → 评估完成后触发
- `on_save` → 保存 checkpoint 时触发
- `on_train_end` → 训练结束后触发

多个 Callback 按注册顺序依次执行。`Trainer` 内部维护了一个 `CallbackHandler`，在每个事件点遍历所有 Callback 调用对应的钩子。

**扩展点二：重写 compute_loss()。** 默认的 `compute_loss()` 直接返回 `outputs.loss`（模型内部计算的 CE loss）。但如果你需要 class_weight、自定义 loss 函数、多个 loss 的组合，重写这个方法是最干净的方案：

```python
class WeightedTrainer(Trainer):
    def compute_loss(self, model, inputs, return_outputs=False):
        labels = inputs.pop("labels")
        outputs = model(**inputs)
        loss = F.cross_entropy(outputs.logits, labels, weight=self.class_weights)
        return (loss, outputs) if return_outputs else loss
```

**扩展点三：重写 evaluate()。** 默认的 evaluate 走完整验证集，但如果你想在评估时记录每个样本的预测结果（用于错误分析），可以重写 `evaluation_loop()` 或 `prediction_step()`。

**关键：Callback 的执行顺序。** Callback 按注册列表顺序执行，但可以通过 `Callback` 的 `.skip_on_*` 属性控制某些回调跳过特定事件。"

**技术映射总结**：
- Callback = 训练的"日程提醒"，到某个时间点自动执行
- compute_loss = 自定义评分规则（不只是标准答案的对错）
- evaluate = 自定义考试方式（不只是做卷子，还要记录每道题的得分）

---

## 3 项目实战

### 3.1 环境准备

```bash
pip install transformers==4.44.0 torch datasets evaluate scikit-learn
```

### 3.2 自定义 Callback

```python
# custom_callbacks.py
"""自定义 TrainerCallback —— 早停 + 告警 + 学习率记录"""

import torch
import numpy as np
from transformers import TrainerCallback, Trainer
from typing import Dict, Optional
import json
import os


class MetricEarlyStoppingCallback(TrainerCallback):
    """基于自定义指标的早停（非 loss）"""

    def __init__(self, metric_name: str = "eval_f1", patience: int = 3,
                 threshold: float = 0.001, greater_is_better: bool = True):
        self.metric_name = metric_name
        self.patience = patience
        self.threshold = threshold
        self.greater_is_better = greater_is_better
        self.best_metric = None
        self.wait = 0

    def on_evaluate(self, args, state, control, metrics=None, **kwargs):
        if metrics is None:
            return

        current = metrics.get(self.metric_name)
        if current is None:
            return

        if self.best_metric is None:
            self.best_metric = current
        else:
            is_better = (
                current > self.best_metric + self.threshold
                if self.greater_is_better
                else current < self.best_metric - self.threshold
            )
            if is_better:
                self.best_metric = current
                self.wait = 0
            else:
                self.wait += 1
                print(f"[EarlyStopping] {self.metric_name} 未提升 "
                      f"({self.wait}/{self.patience}), "
                      f"best={self.best_metric:.4f}, current={current:.4f}")

                if self.wait >= self.patience:
                    print(f"[EarlyStopping] 触发停止！")
                    control.should_training_stop = True


class AlertCallback(TrainerCallback):
    """训练异常告警 Callback"""

    def __init__(self, alert_thresholds: Dict[str, float],
                 webhook_url: Optional[str] = None):
        self.alert_thresholds = alert_thresholds
        self.webhook_url = webhook_url

    def _send_alert(self, message: str):
        """发送告警（模拟，实际中发钉钉/飞书/邮件）"""
        print(f"[ALERT] {message}")
        if self.webhook_url:
            # requests.post(self.webhook_url, json={"msg": message})
            pass

    def on_log(self, args, state, control, logs=None, **kwargs):
        if logs is None:
            return
        if "loss" in logs and logs["loss"] > self.alert_thresholds.get("loss", 10):
            self._send_alert(f"Step {state.global_step}: loss 异常高 ({logs['loss']:.2f})")

    def on_evaluate(self, args, state, control, metrics=None, **kwargs):
        if metrics is None:
            return
        for metric, threshold in self.alert_thresholds.items():
            if metric in metrics and metrics[metric] < threshold:
                self._send_alert(
                    f"Epoch {state.epoch:.1f}: {metric}={metrics[metric]:.4f} "
                    f"低于阈值 {threshold}"
                )


class TrainingRecorderCallback(TrainerCallback):
    """记录训练过程的详细指标到 JSON"""

    def __init__(self, output_dir: str = "./training_records"):
        self.output_dir = output_dir
        os.makedirs(output_dir, exist_ok=True)
        self.records = []

    def on_log(self, args, state, control, logs=None, **kwargs):
        if logs:
            record = {
                "step": state.global_step,
                "epoch": round(state.epoch, 2),
                **{k: v for k, v in logs.items() if isinstance(v, (int, float))},
            }
            self.records.append(record)

    def on_train_end(self, args, state, control, **kwargs):
        path = os.path.join(self.output_dir, f"training_log_{state.global_step}.json")
        with open(path, "w") as f:
            json.dump(self.records, f, indent=2)
        print(f"训练日志已保存: {path}")


# ===== 使用示例 =====
if __name__ == "__main__":
    from transformers import AutoTokenizer, AutoModelForSequenceClassification
    from transformers import Trainer, TrainingArguments, DataCollatorWithPadding
    from datasets import Dataset

    tokenizer = AutoTokenizer.from_pretrained("prajjwal1/bert-tiny")
    model = AutoModelForSequenceClassification.from_pretrained(
        "prajjwal1/bert-tiny", num_labels=2
    )

    data = {"text": ["good", "bad"] * 50, "label": [1, 0] * 50}
    ds = Dataset.from_dict(data).train_test_split(test_size=0.2, seed=42)
    tokenized = ds.map(lambda x: tokenizer(x["text"], truncation=True), batched=True)

    args = TrainingArguments(
        output_dir="./output/callback_demo",
        per_device_train_batch_size=4,
        num_train_epochs=5,
        evaluation_strategy="epoch",
        save_strategy="no",
        report_to="none",
        logging_steps=5,
    )

    trainer = Trainer(
        model=model, args=args,
        train_dataset=tokenized["train"],
        eval_dataset=tokenized["test"],
        tokenizer=tokenizer,
        data_collator=DataCollatorWithPadding(tokenizer),
        callbacks=[
            MetricEarlyStoppingCallback(metric_name="eval_loss",
                                        patience=2, greater_is_better=False),
            AlertCallback({"eval_loss": 0.1}),
            TrainingRecorderCallback(),
        ],
    )

    trainer.train()
```

### 3.3 自定义 Trainer

```python
# custom_trainer.py
"""自定义 Trainer —— 重写 compute_loss 和 training_step"""

import torch
import torch.nn.functional as F
from transformers import Trainer
from typing import Optional, Dict


class WeightedTrainer(Trainer):
    """带类别权重的 Trainer"""

    def __init__(self, class_weights: Optional[torch.Tensor] = None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.class_weights = class_weights

    def compute_loss(self, model, inputs, return_outputs=False):
        """
        重写 loss 计算 —— 支持类别权重
        """
        labels = inputs.pop("labels")
        outputs = model(**inputs)

        if self.class_weights is not None:
            self.class_weights = self.class_weights.to(labels.device)
            loss = F.cross_entropy(
                outputs.logits, labels, weight=self.class_weights
            )
        else:
            loss = outputs.loss

        return (loss, outputs) if return_outputs else loss


class MultiLossTrainer(Trainer):
    """多任务 loss 组合"""

    def __init__(self, task_weights: Dict[str, float] = None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.task_weights = task_weights or {"main": 1.0}

    def compute_loss(self, model, inputs, return_outputs=False):
        """
        组合多个 loss:
        - model 返回 dict 包含多个 loss: {"loss_cls": ..., "loss_aux": ...}
        """
        outputs = model(**inputs)

        total_loss = 0
        for loss_name, weight in self.task_weights.items():
            if loss_name in outputs:
                total_loss += weight * outputs[loss_name]

        return (total_loss, outputs) if return_outputs else total_loss


class GradientMonitorTrainer(Trainer):
    """带梯度监控的 Trainer"""

    def __init__(self, log_grad_every: int = 100, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.log_grad_every = log_grad_every

    def training_step(self, model, inputs):
        """重写训练步骤，添加梯度监控"""
        model.train()
        inputs = self._prepare_inputs(inputs)

        with self.compute_loss_context_manager():
            loss = self.compute_loss(model, inputs)

        # 梯度累积后的平均 loss
        if self.args.gradient_accumulation_steps > 1:
            loss = loss / self.args.gradient_accumulation_steps

        self.accelerator.backward(loss)

        # 梯度监控
        if self.state.global_step % self.log_grad_every == 0:
            total_norm = 0
            for p in model.parameters():
                if p.grad is not None:
                    total_norm += p.grad.data.norm(2).item() ** 2
            total_norm = total_norm ** 0.5
            print(f"[Step {self.state.global_step}] gradient norm = {total_norm:.4f}")

        return loss.detach()


# ===== 使用示例 =====
if __name__ == "__main__":
    from transformers import AutoTokenizer, AutoModelForSequenceClassification
    from datasets import Dataset

    tokenizer = AutoTokenizer.from_pretrained("prajjwal1/bert-tiny")
    model = AutoModelForSequenceClassification.from_pretrained(
        "prajjwal1/bert-tiny", num_labels=2
    )

    data = {"text": ["good"] * 80 + ["bad"] * 20,
            "label": [1] * 80 + [0] * 20}
    ds = Dataset.from_dict(data).train_test_split(test_size=0.2, seed=42)
    tokenized = ds.map(lambda x: tokenizer(x["text"], truncation=True), batched=True)

    from transformers import TrainingArguments, DataCollatorWithPadding

    args = TrainingArguments(
        output_dir="./output/weighted_trainer",
        per_device_train_batch_size=4,
        num_train_epochs=3,
        evaluation_strategy="epoch",
        save_strategy="no",
        report_to="none",
    )

    # 少数类（label=0）权重更高
    class_weights = torch.tensor([2.0, 0.5])  # label 0 权重 4× label 1

    trainer = WeightedTrainer(
        class_weights=class_weights,
        model=model, args=args,
        train_dataset=tokenized["train"],
        eval_dataset=tokenized["test"],
        tokenizer=tokenizer,
        data_collator=DataCollatorWithPadding(tokenizer),
    )

    trainer.train()
```

### 3.4 测试验证

```python
# test_trainer.py
import pytest
import torch
from custom_callbacks import MetricEarlyStoppingCallback

class TestCallbacks:
    def test_early_stop_triggers(self):
        cb = MetricEarlyStoppingCallback(patience=2, greater_is_better=True)
        # 模拟连续 3 次未提升
        class MockControl:
            should_training_stop = False
        ctrl = MockControl()

        cb.on_evaluate(None, None, ctrl, metrics={"eval_f1": 0.8})  # best=0.8
        cb.on_evaluate(None, None, ctrl, metrics={"eval_f1": 0.79}) # wait=1
        cb.on_evaluate(None, None, ctrl, metrics={"eval_f1": 0.79}) # wait=2
        cb.on_evaluate(None, None, ctrl, metrics={"eval_f1": 0.78}) # wait=3 → stop!
        assert ctrl.should_training_stop is True

    def test_improvement_resets_wait(self):
        cb = MetricEarlyStoppingCallback(patience=3)
        ctrl = type('obj', (object,), {'should_training_stop': False})()
        cb.on_evaluate(None, None, ctrl, metrics={"eval_f1": 0.8})
        cb.on_evaluate(None, None, ctrl, metrics={"eval_f1": 0.79})
        cb.on_evaluate(None, None, ctrl, metrics={"eval_f1": 0.85})  # 提升
        assert cb.wait == 0  # 等待计数器应重置
```

---

## 4 项目总结

### 4.1 优点与缺点

| 方面 | 优点 | 缺点 |
|------|------|------|
| **Callback 机制** | 灵活解耦，可任意组合 | Callback 间没有依赖管理，顺序问题需自行处理 |
| **compute_loss 重写** | 支持任意 loss 函数 | 需要理解 inputs 的格式（Trainer 可能修改 inputs） |
| **training_step 重写** | 完全控制每一步 | 破坏封装，升级时可能不兼容 |

### 4.2 适用场景

| 场景 | 推荐方案 |
|------|---------|
| 自定义早停指标 | MetricEarlyStoppingCallback |
| 类别不平衡 | WeightedTrainer + class_weights |
| 多任务训练 | MultiLossTrainer + task_weights |
| 梯度调试 | GradientMonitorTrainer |

**不适用场景**：
- GAN 等交替训练 → 需要自定义训练循环
- RLHF 等多阶段训练 → 用 TRL 库

### 4.3 注意事项

1. **Callback 的 `control.should_training_stop`**：设为 True 后当前 step 不会立即中断，而是完成当前 epoch 后停止
2. **compute_loss 中的 inputs 移除**：调用 `inputs.pop("labels")` 后不要再次使用 inputs
3. **梯度累积**：`compute_loss` 中的 loss 应该是单步的，Trainer 自动做累积除法

### 4.4 常见踩坑经验

| 故障现象 | 根因 | 解决方案 |
|---------|------|---------|
| Callback 不触发 | 没有在 Trainer 初始化时传入 callbacks | `Trainer(..., callbacks=[cb1, cb2])` |
| 自定义 loss 不收敛 | labels 被 pop 后未正确传递 | 确保 pop("labels") 在正确的时机 |
| EarlyStopping 不生效 | metric_for_best_model 与 Callback 中的 metric 名不一致 | 统一命名 |

### 4.5 思考题

1. **初级**：在 `MetricEarlyStoppingCallback` 中增加"最小训练 epoch"限制——前 N 个 epoch 即使指标不提升也不触发早停。
2. **进阶**：设计一个 `HyperParameterTuner`——利用 Trainer + Callback 在训练过程中自动调整学习率（类似 ReduceLROnPlateau，但基于自定义指标而非 loss）。

（答案将在第38章末尾给出）

### 4.6 推广计划提示

| 部门 | 建议行动 |
|------|---------|
| **开发团队** | 将自定义 Callback 模板沉淀为团队公共库 |
| **测试团队** | 为每个自定义 Callback 编写独立单元测试 |
| **算法团队** | 训练时必须接入 `TrainingRecorderCallback`，实验元数据统一管理 |

---

> **下一章预告**：第38章将开发自定义模型——从 PretrainedConfig 到 PreTrainedModel，实现"文本匹配+分类"双任务模型，并注册到 AutoClass。
