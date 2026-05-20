# 第23章：分布式训练入门：Accelerate 与多卡训练

## 1 项目背景

### 业务场景

算法团队用单个 BERT 模型做的工单分类效果不错，但产品经理提出新需求：用更大的模型（XLM-RoBERTa-large，560M参数）在 50 万条多语言工单上训练。小陈在单卡 A10（24GB）上尝试训练，设 batch_size=4 就 OOM 了，降到 batch_size=2 后训练跑起来了，但一个 epoch 要跑 6 小时，3 个 epoch 就是 18 小时——周五下班前提交的任务，周一早上来看可能还在跑。

更糟糕的是，训练过程中 GPU 突然报错 `CUDA out of memory`——某个 batch 恰好包含几条超长工单，tokenize 后长度 400+，显存峰值超出了 24GB。训练中断后模型没做 checkpoint 保存，loss 回退到上一个 checkpoint，白白浪费了半天。

公司有 4 张 A10 闲置，但团队之前只用过单卡训练，没搞过多卡。

### 痛点放大

从单卡到多卡，不是简单的"把代码复制到 4 张卡同时跑"：

1. **数据并行 vs 模型并行**：数据并行是每张卡有完整模型副本，各自算不同数据，最后同步梯度。模型并行是把模型切到不同卡上。什么时候用哪种？两者能不能结合？
2. **同步开销**：4 张卡各自算完梯度后，需要跨卡通信求平均——NCCL通信有开销，卡太多反而拖慢训练
3. **随机种子一致性**：多卡训练时 DataLoader 的随机种子怎么设才能保证每张卡看到不同数据但不重复？

```
单卡训练: [GPU0: 完整模型 + 全部数据] → 12h/epoch
数据并行: [GPU0: 完整模型 + 数据1/4] ┐
         [GPU1: 完整模型 + 数据2/4] ├→ 梯度同步 → 3h/epoch
         [GPU2: 完整模型 + 数据3/4] │
         [GPU3: 完整模型 + 数据4/4] ┘
```

---

## 2 项目设计

### 剧本式交锋对话

**场景**：周四下午 4:00，AI Lab。小陈正在单卡上跑训练，GPU风扇狂转。小胖端着一盒炸鸡走过来。

---

**小胖**:"小陈你训练跑这么久，隔壁 4 张 GPU 都闲着呢。你就不能把训练拆到 4 张卡上一起跑吗？就像四个人一起搬砖——每人搬一块，肯定比一个人搬四块快。"

**小陈**:"道理我懂，但代码怎么写？把模型复制 4 份到 4 张卡？数据怎么分？梯度怎么同步？"

**小白**:"最常用的方案是**数据并行**（DDP，DistributedDataParallel）。每张 GPU 上有完整的模型副本，但各自处理不同的数据子集。前向传播各自算，反向传播各自算梯度，然后用 `all_reduce` 通信把 4 张卡的梯度求平均——这样等效于 batch_size 扩大了 4 倍。"

**大师**:"小白说出了核心，但还有几个关键细节。让我把分布式训练的三种范式讲清楚。

**范式一：数据并行（DDP）。** 最常用。每张卡有模型完整副本，数据按卡数切分。优点：代码改动最小。前提：单卡能装下完整模型。不适合：模型太大单卡装不下。

**范式二：模型并行（Model Parallelism）。** 把模型的不同层放到不同卡上。比如 40 层的模型，GPU0 放 1-20 层，GPU1 放 21-40 层。每张卡资源占用减半。缺点：卡间有串行依赖——GPU1 必须等 GPU0 算完第 20 层传给 GPU1 才能开始算第 21 层，存在'流水线气泡'（GPU 空闲等待时间）。

**范式三：张量并行（Tensor Parallelism）。** 把单层内的矩阵乘法拆分到多卡。比如一个 `4096×4096` 的矩阵乘法，按列切成两份 `4096×2048`，分别放到两张卡上并行计算，再合并。优点：单层也能拆分，适合超大模型。缺点：通信频繁，仅适合高速互联（NVLink）。

**实际工程中用 Accelerate——** 它是 HuggingFace 推出的分布式训练"一键开关"。核心思想：用一份代码，通过配置文件切换单卡/多卡/DeepSpeed/TPU，无需改动训练循环。

它的配置超简单：

```yaml
# accelerate config
compute_environment: LOCAL_MACHINE
distributed_type: MULTI_GPU
num_processes: 4  # 用4张卡
mixed_precision: fp16
```

然后用 `accelerate launch train.py` 替代 `python train.py`，其他代码几乎不变。"

**小胖**:"那 `accelerate` 跟直接用 `torchrun` 有什么区别？"

**大师**:"`torchrun` 是 PyTorch 原生的多卡启动器，需要手动改代码（加 `DistributedSampler`、`all_reduce` 等）。Accelerate 在 `torchrun` 之上做了封装，Trainer 内部已经集成了，几乎零代码改动。一句话：能用 Accelerate + Trainer 就不要手写 DDP。"

**技术映射总结**：
- 数据并行 = 4个人各自做同一张试卷，最后对答案取平均
- 模型并行 = 4个人接力完成试卷（每人做不同部分），有等待时间
- Accelerate = 分布式训练的"自动挡"，不用手动控制离合和油门

---

## 3 项目实战

### 3.1 环境准备

```bash
pip install transformers==4.44.0 torch datasets accelerate
pip install evaluate scikit-learn

# 配置 accelerate（交互式）
accelerate config
# 选择: This machine / multi-GPU / 多少张卡 / fp16 yes

# 或者直接写配置文件
accelerate config default --config_file accelerate_config.yaml
```

### 3.2 Accelerate 单卡→多卡迁移

```python
# train_with_accelerate.py
"""用 Accelerate 将单卡训练脚本无缝迁移到多卡"""

import torch
import numpy as np
from datasets import Dataset
from transformers import (
    AutoTokenizer, AutoModelForSequenceClassification,
    Trainer, TrainingArguments, DataCollatorWithPadding,
)
from accelerate import Accelerator
from sklearn.metrics import accuracy_score

# ===== 方式A: 纯 Trainer（推荐，零代码改动多卡） =====
def train_with_trainer():
    """Trainer 已内置 Accelerate 支持，只需 accelerate launch"""
    tokenizer = AutoTokenizer.from_pretrained("bert-base-chinese")
    model = AutoModelForSequenceClassification.from_pretrained(
        "bert-base-chinese", num_labels=2
    )

    # 数据
    data = {
        "text": ["好", "差"] * 100,
        "label": [1, 0] * 100,
    }
    ds = Dataset.from_dict(data).train_test_split(test_size=0.2, seed=42)

    def tokenize_fn(examples):
        return tokenizer(examples["text"], truncation=True, max_length=128)

    tokenized = ds.map(tokenize_fn, batched=True)

    args = TrainingArguments(
        output_dir="./output/accelerate_demo",
        per_device_train_batch_size=8,    # 每卡 batch_size
        per_device_eval_batch_size=16,
        num_train_epochs=3,
        evaluation_strategy="epoch",
        save_strategy="epoch",
        fp16=True,                        # 混合精度
        report_to="none",
        # 多卡相关不需要额外配置，Trainer 自动处理
    )

    def compute_metrics(eval_pred):
        logits, labels = eval_pred
        preds = np.argmax(logits, axis=-1)
        return {"accuracy": accuracy_score(labels, preds)}

    trainer = Trainer(
        model=model,
        args=args,
        train_dataset=tokenized["train"],
        eval_dataset=tokenized["test"],
        tokenizer=tokenizer,
        data_collator=DataCollatorWithPadding(tokenizer),
        compute_metrics=compute_metrics,
    )

    trainer.train()
    print("训练完成！")


# ===== 方式B: 手写训练循环 + Accelerator =====
def train_with_accelerator():
    """如果需要自定义训练循环，使用 Accelerator"""
    accelerator = Accelerator(
        mixed_precision="fp16",
        gradient_accumulation_steps=2,
        log_with="tensorboard",
    )

    tokenizer = AutoTokenizer.from_pretrained("bert-base-chinese")
    model = AutoModelForSequenceClassification.from_pretrained(
        "bert-base-chinese", num_labels=2
    )

    # 数据
    texts = ["测试" * _ for _ in range(200)]
    labels = [i % 2 for i in range(200)]

    from torch.utils.data import DataLoader, TensorDataset
    encodings = tokenizer(texts, truncation=True, max_length=128,
                          padding="max_length", return_tensors="pt")
    dataset = TensorDataset(encodings["input_ids"], encodings["attention_mask"],
                            torch.tensor(labels))
    dataloader = DataLoader(dataset, batch_size=8, shuffle=True)

    optimizer = torch.optim.AdamW(model.parameters(), lr=2e-5)

    # Accelerator 一键准备（自动处理设备放置、DDP包装、混合精度）
    model, optimizer, dataloader = accelerator.prepare(
        model, optimizer, dataloader
    )

    accelerator.print(f"训练设备: {accelerator.device}")
    accelerator.print(f"进程数: {accelerator.num_processes}")

    model.train()
    for epoch in range(3):
        total_loss = 0
        for batch in dataloader:
            input_ids, attention_mask, labels = batch

            outputs = model(
                input_ids=input_ids,
                attention_mask=attention_mask,
                labels=labels,
            )
            loss = outputs.loss

            # accelerator.backward 替代 loss.backward()
            accelerator.backward(loss)

            optimizer.step()
            optimizer.zero_grad()

            total_loss += loss.item()

        avg_loss = total_loss / len(dataloader)
        accelerator.print(f"Epoch {epoch+1}, loss={avg_loss:.4f}", )
        # 只在主进程打印
        accelerator.log({"loss": avg_loss, "epoch": epoch})

    accelerator.print("训练完成！")

    # 保存模型（先 unwrap 解除 DDP 包装）
    unwrapped_model = accelerator.unwrap_model(model)
    unwrapped_model.save_pretrained("./output/accelerate_custom")
    tokenizer.save_pretrained("./output/accelerate_custom")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "custom":
        train_with_accelerator()
    else:
        train_with_trainer()

# 启动命令:
# 单卡: python train_with_accelerate.py
# 多卡: accelerate launch --num_processes=4 train_with_accelerate.py
```

### 3.3 分布式训练调试工具

```python
# dist_debug.py
"""分布式训练调试辅助"""

import torch
import torch.distributed as dist
from accelerate import Accelerator


def check_distributed_setup():
    """检查分布式环境是否正常"""
    accelerator = Accelerator()

    print(f"[Rank {accelerator.process_index}] 分布式环境检查:")
    print(f"  总进程数: {accelerator.num_processes}")
    print(f"  当前进程: {accelerator.process_index}")
    print(f"  设备: {accelerator.device}")
    print(f"  本地进程数: {accelerator.local_process_index}")
    print(f"  混合精度: {accelerator.mixed_precision}")

    # 检查 NCCL 通信
    if accelerator.num_processes > 1:
        # 简单的 all_reduce 测试
        tensor = torch.tensor([accelerator.process_index], dtype=torch.float32).to(accelerator.device)
        dist.all_reduce(tensor, op=dist.ReduceOp.SUM)
        expected_sum = sum(range(accelerator.num_processes))
        assert tensor.item() == expected_sum, f"NCCL all_reduce 失败: {tensor.item()} != {expected_sum}"
        print(f"  NCCL all_reduce 测试通过 ✓ (sum={tensor.item()})")

    # 检查梯度同步
    if accelerator.num_processes > 1:
        model = torch.nn.Linear(10, 2).to(accelerator.device)
        model = accelerator.prepare(model)

        # 简单的前向+反向
        x = torch.randn(4, 10).to(accelerator.device)
        y = model(x)
        loss = y.sum()
        accelerator.backward(loss)

        # 检查梯度是否一致（在主进程打印）
        grad = model.weight.grad
        print(f"  梯度形状: {grad.shape}, 梯度范数: {grad.norm().item():.6f}")
        print(f"  DDP 梯度同步测试通过 ✓")

    accelerator.print("分布式环境检查全部通过 ✓")


def sync_and_compare(value, accelerator: Accelerator):
    """多进程间同步并对比值（用于验证数据一致性）"""
    if accelerator.num_processes == 1:
        return value

    tensor = torch.tensor([float(value)], device=accelerator.device)
    dist.all_reduce(tensor, op=dist.ReduceOp.SUM)
    return tensor.item() / accelerator.num_processes


if __name__ == "__main__":
    # 用 accelerate launch 运行
    check_distributed_setup()
```

启动方式：

```bash
# 1. 首次使用需配置
accelerate config

# 2. 单卡运行
python dist_debug.py

# 3. 多卡运行（4卡）
accelerate launch --num_processes=4 dist_debug.py

# 4. 多机多卡运行（机器1: 主节点）
accelerate launch --num_processes=8 --num_machines=2 \
  --machine_rank=0 --main_process_ip=192.168.1.10 \
  --main_process_port=29500 train.py

# 5. 查看 accelerate 配置
accelerate env
```

### 3.4 混合精度与梯度累积

```python
# mixed_precision_demo.py
"""混合精度训练与梯度累积配置"""

from transformers import TrainingArguments

# 方案1: FP16（最常用，兼容性最好）
args_fp16 = TrainingArguments(
    output_dir="./output/fp16",
    fp16=True,
    fp16_opt_level="O2",           # O1=保守混合精度, O2=激进混合精度
    per_device_train_batch_size=8,
    gradient_accumulation_steps=2, # 有效 batch=8*2*卡数
    learning_rate=2e-5,
)

# 方案2: BF16（需要 Ampere+ 架构，如 A100/A10/RTX3090）
# 优点: 不需要 loss scaling，比 FP16 更稳定
args_bf16 = TrainingArguments(
    output_dir="./output/bf16",
    bf16=True,
    per_device_train_batch_size=8,
    gradient_accumulation_steps=2,
    learning_rate=2e-5,
)

# 梯度累积的原理:
# 每 micro_batch_size=8 算一次梯度但不更新参数
# 累积 gradient_accumulation_steps=2 次后，梯度求平均再更新
# 等效于 batch_size=16，但显存占用相当于 batch_size=8

print("FP16 训练配置:")
print(f"  per_device_batch: {args_fp16.per_device_train_batch_size}")
print(f"  gradient_accumulation: {args_fp16.gradient_accumulation_steps}")
print(f"  有效 batch_size(单卡): "
      f"{args_fp16.per_device_train_batch_size * args_fp16.gradient_accumulation_steps}")
```

### 3.5 测试验证

```python
# test_distributed.py
import pytest
import torch
from accelerate import Accelerator

class TestAccelerate:
    def test_accelerator_creation(self):
        acc = Accelerator()
        assert acc.device is not None
        assert acc.num_processes >= 1

    def test_gradient_accumulation_math(self):
        """梯度累积的等效 batch 计算"""
        per_device_batch = 8
        grad_accum = 4
        num_devices = 2
        effective_batch = per_device_batch * grad_accum * num_devices
        assert effective_batch == 64

    def test_distributed_barrier(self):
        """分布式屏障测试"""
        acc = Accelerator()
        # barrier 确保所有进程同步
        acc.wait_for_everyone()
        # 如果没抛异常就是通过
```

---

## 4 项目总结

### 4.1 优点与缺点

| 并行策略 | 优点 | 缺点 |
|---------|------|------|
| **数据并行(DDP)** | 代码改动最小，线性加速好 | 单卡必须装下完整模型 |
| **模型并行** | 可训练超大模型 | 流水线气泡浪费GPU时间 |
| **Accelerate** | 一键切换单卡/多卡/TPU，Trainer零改动 | 自动策略可能非最优 |
| **梯度累积** | 等效增大batch_size而不增显存 | batch norm统计量基于小batch |

### 4.2 适用场景

| 场景 | 推荐方案 |
|------|---------|
| 单卡能装下的小模型（<1B） | 数据并行 + Accelerate |
| 单卡装不下的大模型（>7B） | 模型并行/张量并行 + DeepSpeed |
| 快速原型验证 | 单卡 + 梯度累积模拟大batch |
| 多机多卡 | Accelerate + torchrun |

**不适用场景**：
- 通信带宽低的机器间（如百兆网络）→ 通信开销抵消并行收益

### 4.3 注意事项

1. **随机种子**：多卡训练时 `seed` 需在 `accelerator` 之后设置，确保每卡的数据 shuffle 不同
2. **日志打印**：用 `accelerator.print()` 替代 `print()`，否则每个进程都打印一遍
3. **学习率缩放**：有效 batch_size 增大时学习率通常按 `sqrt(batch_size_ratio)` 或线性缩放

### 4.4 常见踩坑经验

| 故障现象 | 根因 | 解决方案 |
|---------|------|---------|
| 多卡训练 loss 不下降 | NCCL 版本不匹配或端口被占用 | `accelerate env` 检查环境，`lsof -i:29500` 查端口 |
| 每张卡显存占用不一致 | batch内文本长度差异大 | 使用 sorted sampler 或 bucketing |
| `accelerate launch` 找不到模块 | 虚拟环境路径在多进程间丢失 | 在配置文件中指定 `python_executable` |

### 4.5 思考题

1. **初级**：用 `accelerate launch --num_processes=2` 和 `--num_processes=4` 分别运行训练，对比 epoch 耗时。是否 4 卡比 2 卡快 2 倍？如果不是，瓶颈在哪？
2. **进阶**：训练时发现 GPU0 显存利用率 95%，GPU1 只有 30%。可能的原因是什么？如何平衡多卡间的负载？

（答案将在第24章末尾给出）

### 4.6 第22章思考题答案

**第22章思考题1**：
- max_wait_ms=10：延迟低但batch小，GPU利用率不高；max_wait_ms=200：batch大吞吐高但等待时间长。50ms 是常见平衡点，需根据实际延迟要求调优。

**第22章思考题2**：
- 混合精度量化方案：(1) 用校准数据跑FP32推理，记录每层的输出分布；(2) 对输出分布进行敏感性分析——计算每层量化前后的KL散度；(3) KL散度 > 阈值的层标记为"敏感层"用FP16，其余用INT4；(4) 实现时用 `BitsAndBytesConfig` 的 `llm_int8_skip_modules` 跳过敏感层。HuggingFace 已在 LLM.int8() 论文中实现了类似逻辑。

### 4.7 推广计划提示

| 部门 | 建议行动 |
|------|---------|
| **开发团队** | 新模型训练脚本默认支持 Accelerate，单卡/多卡通过配置切换 |
| **测试团队** | 多卡训练后验证每张卡输出的梯度是否一致（DDP同步正确性） |
| **运维团队** | 配置GPU集群的NCCL环境变量（`NCCL_IB_DISABLE`、`NCCL_SOCKET_IFNAME`等） |

---

> **下一章预告**：第24章将深入 DeepSpeed ZeRO——理解 Stage 1/2/3 如何拆分参数、梯度和优化器状态，用一张 24GB 的卡训练 13B 模型。
