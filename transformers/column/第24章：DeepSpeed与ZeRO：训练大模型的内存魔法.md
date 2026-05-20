# 第24章：DeepSpeed 与 ZeRO：训练大模型的内存魔法

## 1 项目背景

### 业务场景

算法团队接到了一个挑战性任务：用公司积累的 2 万条客服对话数据微调一个 13B 参数的大模型（如 Qwen-14B-Chat），使其具备多轮客服对话能力。

小陈尝试用第 23 章学的数据并行跑训练，结果第一轮就失败了——单张 A10（24GB）连模型都装不下（13B 模型 FP16 需约 26GB 显存）。换成 A100（80GB）后勉强装下，但训练时 OOM：模型 26GB + 梯度 26GB + Adam 优化器状态 52GB + 激活值 ≈ 120GB，远超 80GB。

技术经理质问："难道 13B 模型必须用 4 张 A100 才能训练？有没有办法用更少的资源？"

### 痛点放大

大模型训练的内存黑洞来自四个部分：

```
全量微调 13B 模型（FP16）的显存占用:
┌─────────────────────┬──────────┐
│ 组件                │ 显存     │
├─────────────────────┼──────────┤
│ 模型参数 (Weights)   │ 26 GB    │
│ 梯度 (Gradients)     │ 26 GB    │
│ 优化器状态 (Adam)    │ 52 GB    │   ← 参数×2 (momentum+variance)
│ 激活值 (Activations) │ 20-40 GB │   ← 取决于batch_size和seq_len
├─────────────────────┼──────────┤
│ 合计                 │ 124-144 GB│
└─────────────────────┴──────────┘

单张 A100 (80GB): 远不够
4张 A100 (320GB): 够但太贵
```

ZeRO（Zero Redundancy Optimizer）就是为解决这个问题设计的——它把参数、梯度和优化器状态"拆开"分布到多张卡上，消除冗余存储。

---

## 2 项目设计

### 剧本式交锋对话

**场景**：周五下午 5:30，AI Lab。小陈对着 OOM 报错已经盯着看了半小时。小胖端着一份超大份的豪华套餐过来。

---

**小胖**:"小陈你这报错比我的外卖订单还长。又OOM了？你是不是把整个模型塞进一张卡里了？就像我一个人要点 10 个菜——肚子装不下可以叫朋友一人分两个菜嘛！"

**小陈**:"这就是 DeepSpeed ZeRO 做的——把模型参数拆到多张卡上。但我搞不清 Stage 1/2/3 的区别。"

**小白**:"ZeRO 的三个 Stage 拆分的对象不同：

- **ZeRO-1**：只拆分**优化器状态**（Adam 的 momentum 和 variance）。每张卡存 1/N 的优化器状态。
- **ZeRO-2**：拆分优化器状态 + **梯度**。每张卡只存自己那部分参数对应的梯度。
- **ZeRO-3**：拆分优化器状态 + 梯度 + **模型参数本身**。每张卡只存 1/N 的模型参数，前向传播时需要从其他卡收集参数。

显存节省：ZeRO-1 省约 4 倍，ZeRO-2 省约 8 倍，ZeRO-3 省约 N 倍（N=卡数）。"

**大师**:"小白解释得不错。让我补充三个工程关键点。

**第一，每个 Stage 的通信开销。** 
- ZeRO-1：只做一次 `all_reduce` 同步梯度，通信开销最小
- ZeRO-2：同 ZeRO-1 类似的通信量
- ZeRO-3：每次前向和反向传播都需要从其他卡收集参数（`all_gather`），通信量显著增加。卡的互联带宽（NVLink vs PCIe）对 ZeRO-3 影响极大

**第二，CPU Offload。**
当 GPU 显存仍然不够时，DeepSpeed 支持把优化器状态（甚至模型参数）卸载到 CPU 内存。CPU 内存便宜（128GB 几百块），但访问慢 10-20 倍。典型的折中方案：ZeRO-2 + CPU offload 优化器状态——用 8 张 V100（32GB）就能训练 175B 的 GPT-3。

**第三，与 Trainer 的集成。**
Transformers 的 Trainer 可以通过一个 JSON 配置文件直接接入 DeepSpeed，无需改 Python 代码：

```bash
accelerate launch --config_file deepspeed_config.json train.py
```

只需要写一个 `deepspeed_config.json` 指定 Stage、offload 等参数。"

**小胖**:"那到底选哪个 Stage？我们也不是每次都训练 175B。"

**大师**:"决策表：

| 你的情况 | 推荐 ZeRO Stage |
|---------|----------------|
| 单卡能装下模型但 OOM 在训练 | ZeRO-1 |
| 单卡勉强装下模型 | ZeRO-2 |
| 单卡装不下模型 | ZeRO-3 |
| 显存严重不足 | ZeRO-3 + CPU offload |
| 还有 NVMe 固态 | ZeRO-Infinity (offload 到 SSD) |

**最佳实践**：从 ZeRO-2 开始，不够再加 offload，还不够升到 ZeRO-3。"

**技术映射总结**：
- ZeRO-1 = 只把账本（优化器状态）分开放，菜还在一人盘里
- ZeRO-2 = 账本+备忘录（梯度）都分开放
- ZeRO-3 = 连菜（模型参数）都分开放，每人端一部分拼成一桌
- CPU Offload = 桌子放不下先放冰箱（CPU内存），慢但够用

---

## 3 项目实战

### 3.1 环境准备

```bash
pip install deepspeed>=0.13.0  # DeepSpeed 核心库
pip install transformers==4.44.0 torch accelerate datasets

# 验证安装
ds_report
# 查看 DeepSpeed 环境和可用 ops
```

### 3.2 DeepSpeed 配置与训练

```json
// ds_config_zero2.json — ZeRO Stage 2 配置
{
    "train_batch_size": "auto",
    "train_micro_batch_size_per_gpu": "auto",
    "gradient_accumulation_steps": "auto",
    "gradient_clipping": 1.0,
    "zero_optimization": {
        "stage": 2,
        "overlap_comm": true,
        "contiguous_gradients": true,
        "reduce_bucket_size": 5e8,
        "allgather_bucket_size": 5e8
    },
    "fp16": {
        "enabled": true,
        "loss_scale": 0,
        "loss_scale_window": 1000,
        "initial_scale_power": 16,
        "hysteresis": 2,
        "min_loss_scale": 1
    },
    "optimizer": {
        "type": "AdamW",
        "params": {
            "lr": "auto",
            "betas": [0.9, 0.999],
            "eps": 1e-8,
            "weight_decay": "auto"
        }
    },
    "scheduler": {
        "type": "WarmupLR",
        "params": {
            "warmup_min_lr": "auto",
            "warmup_max_lr": "auto",
            "warmup_num_steps": "auto"
        }
    },
    "communication_data_type": "fp16"
}
```

```json
// ds_config_zero3.json — ZeRO Stage 3 配置（带 CPU offload）
{
    "zero_optimization": {
        "stage": 3,
        "offload_optimizer": {
            "device": "cpu",
            "pin_memory": true
        },
        "offload_param": {
            "device": "cpu",
            "pin_memory": true
        },
        "overlap_comm": true,
        "contiguous_gradients": true,
        "sub_group_size": 1e9,
        "reduce_bucket_size": 5e8,
        "stage3_prefetch_bucket_size": 5e8,
        "stage3_param_persistence_threshold": 1e6,
        "stage3_max_live_parameters": 1e9,
        "stage3_max_reuse_distance": 1e9,
        "stage3_gather_16bit_weights_on_model_save": true
    },
    "fp16": {
        "enabled": true
    },
    "optimizer": {
        "type": "AdamW",
        "params": {
            "lr": 2e-5
        }
    },
    "scheduler": {
        "type": "WarmupLR",
        "params": {
            "warmup_num_steps": 100
        }
    },
    "train_batch_size": "auto",
    "train_micro_batch_size_per_gpu": "auto",
    "gradient_accumulation_steps": "auto"
}
```

```python
# train_with_deepspeed.py
"""用 DeepSpeed + Trainer 训练（零代码改动）"""

import numpy as np
from datasets import Dataset
from transformers import (
    AutoTokenizer, AutoModelForSequenceClassification,
    Trainer, TrainingArguments, DataCollatorWithPadding,
    HfDeepSpeedConfig,
)
from sklearn.metrics import accuracy_score
import os

# 准备数据
tokenizer = AutoTokenizer.from_pretrained("bert-base-chinese")
model = AutoModelForSequenceClassification.from_pretrained(
    "bert-base-chinese", num_labels=2
)

data = {"text": ["好", "差"] * 200, "label": [1, 0] * 200}
ds = Dataset.from_dict(data).train_test_split(test_size=0.2, seed=42)

def tokenize_fn(examples):
    return tokenizer(examples["text"], truncation=True, max_length=128)

tokenized = ds.map(tokenize_fn, batched=True)

# 训练配置
training_args = TrainingArguments(
    output_dir="./output/deepspeed_demo",
    per_device_train_batch_size=16,
    per_device_eval_batch_size=32,
    num_train_epochs=3,
    evaluation_strategy="epoch",
    save_strategy="epoch",
    fp16=True,                         # DeepSpeed 的 fp16 由 config 控制
    deepspeed="./ds_config_zero2.json", # 指定 DeepSpeed 配置文件
    report_to="none",
    logging_steps=10,
)

def compute_metrics(eval_pred):
    logits, labels = eval_pred
    preds = np.argmax(logits, axis=-1)
    return {"accuracy": accuracy_score(labels, preds)}

trainer = Trainer(
    model=model,
    args=training_args,
    train_dataset=tokenized["train"],
    eval_dataset=tokenized["test"],
    tokenizer=tokenizer,
    data_collator=DataCollatorWithPadding(tokenizer),
    compute_metrics=compute_metrics,
)

print("开始 DeepSpeed 训练...")
trainer.train()
trainer.save_model("./output/deepspeed_model")

# 启动命令:
# deepspeed --num_gpus=4 train_with_deepspeed.py
# 或者:
# accelerate launch --use_deepspeed --deepspeed_config_file ds_config_zero2.json train_with_deepspeed.py
```

### 3.3 ZeRO Stage 对比脚本

```python
# zero_stage_compare.py
"""对比不同 ZeRO Stage 的显存占用和训练速度"""

import torch
import time

def estimate_memory(model_size_gb: float, num_gpus: int, stage: int,
                    batch_size: int = 8, seq_len: int = 512):
    """
    估算不同 ZeRO Stage 的显存占用

    假设:
    - FP16 训练
    - Adam 优化器
    - 激活值 ≈ batch_size * seq_len * hidden_dim * num_layers * 常量
    """
    # 粗略估算公式
    weights = model_size_gb
    gradients = model_size_gb
    optimizer_states = model_size_gb * 2  # Adam: momentum + variance
    activations = model_size_gb * 0.8 * (batch_size / 8) * (seq_len / 512)

    total_without_zero = weights + gradients + optimizer_states + activations

    if stage == 0:  # 无 ZeRO
        per_gpu = total_without_zero
    elif stage == 1:  # 拆分优化器状态
        per_gpu = weights + gradients + optimizer_states / num_gpus + activations
    elif stage == 2:  # 拆分优化器状态 + 梯度
        per_gpu = weights + gradients / num_gpus + optimizer_states / num_gpus + activations
    elif stage == 3:  # 拆分全部
        per_gpu = (weights + gradients + optimizer_states) / num_gpus + activations

    return {
        "total_no_zero": round(total_without_zero, 1),
        "per_gpu": round(per_gpu, 1),
        "savings": f"{(1 - per_gpu / max(total_without_zero, 0.01)) * 100:.0f}%",
    }


if __name__ == "__main__":
    model_sizes = [0.1, 1.0, 7.0, 13.0, 70.0]  # GB (FP16)
    gpu_sizes = [24, 40, 80]  # 常见 GPU 显存

    print("ZeRO Stage 显存估算 (4 GPU, BS=8, SeqLen=512):")
    print(f"{'模型大小':<10}", end="")
    for stage in [0, 1, 2, 3]:
        print(f"{'ZeRO-'+str(stage):<15}", end="")
    print()
    print("-" * 70)

    for size in model_sizes:
        print(f"{size:.0f}B FP16{'':>2}", end="")
        for stage in [0, 1, 2, 3]:
            est = estimate_memory(size, 4, stage)
            per_gpu = est["per_gpu"]
            marker = " ✓" if per_gpu <= 24 else " ⚠" if per_gpu <= 80 else " ✗"
            print(f"{per_gpu:>5.1f}GB{marker}    ", end="")
        print()

    print("\n单卡能否训练? (✓=24GB可, ⚠=需80GB, ✗=需更多卡)")
```

### 3.4 训练监控

```python
# deepspeed_monitor.py
"""DeepSpeed 训练中的显存与吞吐监控"""

import torch
import time
from transformers import TrainerCallback


class DeepSpeedMonitorCallback(TrainerCallback):
    """监控 DeepSpeed 训练的显存和吞吐"""

    def __init__(self):
        self.step_times = []
        self.memory_records = []

    def on_step_begin(self, args, state, control, **kwargs):
        self._step_start = time.time()

    def on_step_end(self, args, state, control, **kwargs):
        elapsed = time.time() - self._step_start
        self.step_times.append(elapsed)

        # GPU 显存使用
        if torch.cuda.is_available():
            for i in range(torch.cuda.device_count()):
                allocated = torch.cuda.memory_allocated(i) / 1024**3
                reserved = torch.cuda.memory_reserved(i) / 1024**3
                self.memory_records.append({
                    "step": state.global_step,
                    "gpu": i,
                    "allocated_gb": round(allocated, 2),
                    "reserved_gb": round(reserved, 2),
                })

    def on_log(self, args, state, control, logs=None, **kwargs):
        if self.step_times:
            recent = self.step_times[-10:]
            avg_step_time = sum(recent) / len(recent)
            throughput = args.per_device_train_batch_size * args.gradient_accumulation_steps / avg_step_time

            print(f"\n[性能] 平均步时: {avg_step_time:.2f}s, "
                  f"吞吐: {throughput:.1f} samples/s")

            if self.memory_records:
                latest = self.memory_records[-1]
                print(f"[显存] GPU{latest['gpu']}: "
                      f"已分配 {latest['allocated_gb']:.1f}GB, "
                      f"已保留 {latest['reserved_gb']:.1f}GB")

    def on_train_end(self, args, state, control, **kwargs):
        avg_time = sum(self.step_times) / len(self.step_times) if self.step_times else 0
        total_time = sum(self.step_times)
        print(f"\n训练统计: 总步数={state.global_step}, "
              f"总耗时={total_time/60:.1f}min, "
              f"平均步时={avg_time:.2f}s")


# 使用:
# trainer = Trainer(
#     ...,
#     callbacks=[DeepSpeedMonitorCallback()],
# )
```

### 3.5 测试验证

```python
# test_deepspeed.py
import json
import pytest
import os

class TestDeepSpeedConfig:
    def test_zeRO2_config_valid(self):
        """验证 ZeRO-2 配置格式"""
        config = {
            "zero_optimization": {
                "stage": 2,
                "overlap_comm": True,
            },
            "fp16": {"enabled": True},
        }
        assert config["zero_optimization"]["stage"] == 2

    def test_zeRO3_config_valid(self):
        """验证 ZeRO-3 配置格式"""
        config = {
            "zero_optimization": {
                "stage": 3,
                "offload_optimizer": {"device": "cpu"},
                "offload_param": {"device": "cpu"},
            },
        }
        assert config["zero_optimization"]["stage"] == 3
        assert config["zero_optimization"]["offload_optimizer"]["device"] == "cpu"
```

---

## 4 项目总结

### 4.1 优点与缺点

| 方案 | 显存节省 | 速度影响 | 代码改动 |
|------|---------|---------|---------|
| **ZeRO-1** | 约 4× | 几乎无损 | 零改动（json配置） |
| **ZeRO-2** | 约 8× | 轻微下降（5%） | 零改动 |
| **ZeRO-3** | 约 N×（N=卡数） | 下降 10-30% | 零改动 |
| **ZeRO-3 + CPU Offload** | 极大 | 下降 50%+ | 零改动 |

### 4.2 适用场景

| 场景 | 推荐方案 |
|------|---------|
| 7B 模型在 4×A100 上训练 | ZeRO-2 |
| 13B 模型在 8×V100(32GB) 上训练 | ZeRO-3 |
| 70B 模型在有限资源上训练 | ZeRO-3 + CPU offload |
| 单卡消费级GPU微调7B | QLoRA（比 ZeRO 更轻量） |

**不适用场景**：
- 模型太小（<300M参数）→ ZeRO 的通信开销大于收益
- 通信带宽极低（<10Gbps）→ ZeRO-3 通信瓶颈严重

### 4.3 注意事项

1. **`train_micro_batch_size_per_gpu` vs `per_device_train_batch_size`**：DeepSpeed 用前者，Trainer 用后者，两者含义相同
2. **ZeRO-3 保存模型**：需设置 `stage3_gather_16bit_weights_on_model_save: true`，否则保存的是分片
3. **与 PEFT/LoRA 的关系**：LoRA 和 ZeRO 可以叠加使用，LoRA 大幅减小了可训练参数

### 4.4 常见踩坑经验

| 故障现象 | 根因 | 解决方案 |
|---------|------|---------|
| DeepSpeed 初始化报 `SIGSEGV` | CUDA/CUDNN 版本与 DeepSpeed 不兼容 | 用 `ds_report` 检查，降级到兼容版本 |
| ZeRO-3 训练极慢 | 未开启 `overlap_comm` 或通信带宽低 | 开启 overlap_comm，检查 NVLink 是否正常 |
| CPU offload 后训练速度骤降 | CPU 内存不够导致 swap | 监控 CPU 内存使用，确保无 swap |

### 4.5 思考题

1. **初级**：在 `zero_stage_compare.py` 中，将 num_gpus 从 4 改为 8，ZeRO-3 的 per_gpu 显存是多少？为什么没有继续线性下降？
2. **进阶**：ZeRO-3 在前向传播时需要从其他卡收集参数。这个 `all_gather` 操作发生在哪些时刻？如果卡间带宽只有 PCIe 32GB/s（非 NVLink），会对训练速度产生多大影响？

（答案将在第25章末尾给出）

### 4.6 第23章思考题答案

**第23章思考题1**：
- 4卡通常比2卡快1.5-1.8倍（非理想2倍），瓶颈在NCCL通信开销。卡越多通信轮次越多，每次all_reduce的延迟叠加。

**第23章思考题2**：
- GPU0 显存利用率高、GPU1 低的原因：(1) batch内文本长度分布不均，GPU0的样本恰好都比GPU1长；(2) 模型分配不均（如使用模型并行时）。解决方案：(1) 用 `LengthGroupedSampler` 将相近长度的样本分到同一batch；(2) 检查 `device_map` 是否均衡分配模型层。

### 4.7 推广计划提示

| 部门 | 建议行动 |
|------|---------|
| **开发团队** | 大模型训练脚本默认包含 DeepSpeed 配置文件，通过环境变量切换 Stage |
| **测试团队** | 在 CI 中跑单步训练验证 DeepSpeed 配置正确性，不跑完整训练 |
| **运维团队** | 配置 GPU 集群的 NCCL 和 DeepSpeed 相关环境变量，确保多卡通信正常 |

---

> **下一章预告**：第25章将进入模型服务化进阶——异步队列、动态批量合并、限流熔断和请求优先级，把第14章的简单API升级为生产级推理网关。
