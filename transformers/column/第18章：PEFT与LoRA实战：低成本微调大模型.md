# 第18章：PEFT 与 LoRA 实战：低成本微调大模型

## 1 项目背景

### 业务场景

算法团队接到了一个"不可能完成"的需求：用公司内部 2000 条人工标注的客服问答对，微调一个 7B 参数的开源大模型（如 Qwen-7B、ChatGLM3-6B），让它学会输出固定格式的 JSON 客服回复模板。

小陈试着用全量微调跑了一次——单卡 A100（80GB 显存）直接 OOM，即使用 DeepSpeed ZeRO-3 + 4 卡并行，训练耗时 8 小时，显存峰值仍有 60GB。这意味着一轮实验就跑了一天，而产品经理说"至少要调 10 轮 prompt 模板，每轮都得重新微调"。

更糟糕的是，业务方还有 3 个不同的场景（售前咨询、售后处理、投诉安抚），如果用传统全量微调，每个场景一个完整模型：4 × 7B = 28GB 磁盘 × 3 ≈ 84GB 存储，部署时 GPU 服务器至少需要 4 张 A100，成本超过每月 3 万元。

### 痛点放大

大模型微调的核心矛盾是：模型参数越来越多（从 110M 的 BERT 到 7B/13B/70B 的 LLaMA/Qwen），但业务标注数据通常只有几百到几千条。全量微调 7B 模型需要：

1. **显存黑洞**：全量微调 7B 模型 ≈ 模型参数 14GB + 梯度 14GB + 优化器状态 28GB + 激活值 ≈ 60-70GB，远超单卡消费级 GPU 的 24GB
2. **存储爆炸**：每个微调后的模型都是一个完整副本，10 个业务场景 = 10 × 14GB = 140GB
3. **切换成本高**：A/B 测试两个模型版本需要同时加载两个完整模型到显存

PEFT（Parameter-Efficient Fine-Tuning）和 LoRA（Low-Rank Adaptation）正是为了解决这些痛点而生——用不到 1% 的参数量，实现接近全量微调的效果。

---

## 2 项目设计

### 剧本式交锋对话

**场景**：周三上午 11:00，AI Lab。小陈第四次微调跑崩之后，盯着 OOM 报错怀疑人生。小胖端着一碗关东煮过来。

---

**小胖**:"小陈你这 GPU 是不是又炸了？每次路过都看到红字。你就不能只训练一部分参数吗？就像我健身——也不是全身肌肉都练，重点练腹肌就够了。"

**小白**:"小胖你这个比喻意外地精准。LoRA 做的就是这件事——它不是训练整个模型，而是在模型旁边额外挂两个很小的矩阵 A 和 B，只训练这两个小矩阵。原始模型的权重全部冻结不动。"

**小胖**:"等等，挂两个小矩阵就能改变模型行为？这不科学吧？"

**大师**:"这恰恰是 LoRA 的精妙之处。让我来解释原理。

**核心思想：低秩适应。** 原模型某层的权重矩阵 W 是 `d×d`（比如 4096×4096）。全量微调是直接更新 W。LoRA 的假设是：微调带来的权重变化量 ΔW 虽然是 `d×d` 维，但它可以用两个小矩阵的乘积来近似：**ΔW = A × B**，其中 A 是 `d×r`，B 是 `r×d`，r 称为**秩**（rank），通常取 8、16 或 32。

前向传播变成：**h = W·x + A·B·x**（W 冻结，A 和 B 可训练）

举个例子：W 是 4096×4096 = 16.7M 个参数。如果 r=16，A 是 4096×16，B 是 16×4096，加起来只有 4096×16×2 = 131K 个参数——是原始 W 的 **0.78%**！十倍以上的参数压缩。

**为什么有效？** 研究表明，大模型在微调时，权重的更新矩阵 ΔW 确实具有很低的"内在秩"——模型不需要改动所有方向，只需要在少数几个关键方向上调整就够了。LoRA 恰好捕捉了这些关键方向。"

**小白**:"那 r 怎么选？r 越大效果越好但参数越多？怎么在效果和效率之间平衡？"

**大师**:"实践经验：
- r=4 或 8：适合简单分类任务，参数极少
- r=16：最通用的选择，分类/生成效果都接近全量微调
- r=32 或 64：任务特别复杂或数据量大时选
- alpha 参数（缩放系数）通常设为 r 的 2 倍，如 r=16 → alpha=32"

**小胖**:"还有一个问题——LoRA 要加到哪些层？是所有 Linear 层都加吗？"

**大师**:"常见做法是加到 Attention 层的 Q（query）和 V（value）投影矩阵上。有些也会加到 K 和 O 上。`target_modules=["q_proj", "v_proj"]` 是最常用的配置。全部 Linear 层都加效果可能更好但参数也更多。

**QLoRA** 是 LoRA 的升级版——在 LoRA 的基础上加上了 4-bit 量化，把模型权重从 fp16 压缩到 int4，显存占用再降 75%。7B 模型用 QLoRA 只需要约 6GB 显存就能微调，一块 RTX 3060 12GB 就够了。"

**技术映射总结**：
- LoRA = 给大模型打了两个"小补丁"（A 和 B 矩阵），只训练补丁不动本体
- rank r = 补丁的"精细度"，越高越精确但越占资源
- QLoRA = LoRA + 4-bit 量化，消费级 GPU 也能微调 7B 模型

---

## 3 项目实战

### 3.1 环境准备

```bash
pip install transformers==4.44.0 torch
pip install peft>=0.10.0 accelerate>=0.27.0
pip install bitsandbytes>=0.42.0  # QLoRA 需要
pip install datasets
```

### 3.2 LoRA 微调实战

```python
# lora_finetune.py
"""LoRA 微调 —— 只用 1% 参数训练大模型"""

import torch
from transformers import (
    AutoTokenizer, AutoModelForCausalLM,
    Trainer, TrainingArguments, DataCollatorForLanguageModeling,
)
from peft import LoraConfig, get_peft_model, TaskType
from datasets import Dataset

# ===== 1. 加载基座模型 =====
MODEL_NAME = "uer/gpt2-chinese-cluecorpussmall"  # 演示用小模型
# 实际大模型: "Qwen/Qwen-7B-Chat" 或 "THUDM/chatglm3-6b"

tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)
tokenizer.pad_token = tokenizer.eos_token

model = AutoModelForCausalLM.from_pretrained(
    MODEL_NAME,
    torch_dtype=torch.float16 if torch.cuda.is_available() else torch.float32,
    device_map="auto" if torch.cuda.is_available() else None,
)

# ===== 2. 配置 LoRA =====
lora_config = LoraConfig(
    task_type=TaskType.CAUSAL_LM,
    r=8,                              # 秩（rank）
    lora_alpha=16,                    # 缩放系数（通常 2×r）
    lora_dropout=0.1,                 # LoRA dropout
    target_modules=["c_attn"],        # GPT-2 的 attention 层
    # 对于 LLaMA/Qwen 模型:
    # target_modules=["q_proj", "v_proj", "k_proj", "o_proj"],
    bias="none",                      # 不训练 bias
)

model = get_peft_model(model, lora_config)
model.print_trainable_parameters()
# 输出: trainable params: 294,912 || all params: 102,538,496 || trainable%: 0.2877

# ===== 3. 准备训练数据 =====
# 客服回复模板微调数据
training_data = [
    {"text": "用户: 怎么退款？\n客服: {\"action\":\"guide\",\"content\":\"请在订单详情页点击申请退款按钮，填写退款原因后提交申请，3-7个工作日到账。\"}"},
    {"text": "用户: 物流太慢了\n客服: {\"action\":\"apology\",\"content\":\"非常抱歉给您带来不便。我已为您查询物流状态，目前包裹已到达【中转站】，预计48小时内送达。\"}"},
    {"text": "用户: 商品坏了\n客服: {\"action\":\"solution\",\"content\":\"很抱歉出现质量问题。您可以申请退款退货，或者我们为您重新发货。请问您更倾向于哪种方案？\"}"},
    {"text": "用户: 怎么联系人工\n客服: {\"action\":\"transfer\",\"content\":\"正在为您转接人工客服，请稍候。您也可以拨打400-123-4567直接联系。\"}"},
    {"text": "用户: 优惠券怎么用\n客服: {\"action\":\"guide\",\"content\":\"下单时在结算页面点击【使用优惠券】，选择您要使用的优惠券即可。注意优惠券有使用期限，请在有效期内使用。\"}"},
]

def format_example(example):
    # 构建训练格式: prompt + completion
    formatted = f"你是一个专业的客服助手。请根据用户问题，输出标准JSON格式的回复。\n\n{example['text']}"
    return {"formatted_text": formatted}

ds = Dataset.from_list(training_data)
ds = ds.map(format_example)

def tokenize_fn(examples):
    result = tokenizer(
        examples["formatted_text"],
        truncation=True,
        max_length=256,
        padding="max_length",
    )
    # 对于 Causal LM，labels = input_ids
    result["labels"] = result["input_ids"].copy()
    return result

tokenized_ds = ds.map(tokenize_fn, batched=True)

# 划分训练/验证
split_ds = tokenized_ds.train_test_split(test_size=0.2, seed=42)

# ===== 4. 训练 =====
training_args = TrainingArguments(
    output_dir="./output/lora_finetune",
    per_device_train_batch_size=2,
    per_device_eval_batch_size=2,
    gradient_accumulation_steps=4,
    num_train_epochs=10,
    learning_rate=2e-4,               # LoRA 通常用比全量微调更高的 lr
    warmup_ratio=0.1,
    logging_steps=5,
    evaluation_strategy="steps",
    eval_steps=10,
    save_strategy="epoch",
    fp16=torch.cuda.is_available(),
    report_to="none",
)

trainer = Trainer(
    model=model,
    args=training_args,
    train_dataset=split_ds["train"],
    eval_dataset=split_ds["test"],
    tokenizer=tokenizer,
)

print("开始 LoRA 微调...")
trainer.train()

# ===== 5. 保存 LoRA 权重 =====
model.save_pretrained("./models/lora_customer_service")
tokenizer.save_pretrained("./models/lora_customer_service")
print("LoRA 权重已保存（仅 ~1MB，非完整模型）")

# ===== 6. 推理测试 =====
model.eval()
test_prompt = "你是一个专业的客服助手。请根据用户问题，输出标准JSON格式的回复。\n\n用户: 我要退货\n客服:"
inputs = tokenizer(test_prompt, return_tensors="pt").to(model.device)

with torch.no_grad():
    outputs = model.generate(
        **inputs,
        max_new_tokens=100,
        temperature=0.7,
        top_p=0.9,
        do_sample=True,
    )

response = tokenizer.decode(outputs[0], skip_special_tokens=True)
print(f"\n推理测试:\n{response}")
```

### 3.3 LoRA 权重管理与合并

```python
# lora_manager.py
"""LoRA 权重管理：保存、加载、合并、切换"""

from peft import PeftModel, PeftConfig
from transformers import AutoModelForCausalLM, AutoTokenizer
import torch

BASE_MODEL = "uer/gpt2-chinese-cluecorpussmall"

def load_lora_model(base_model_name: str, lora_path: str):
    """加载 LoRA 模型（基座 + LoRA 权重）"""
    config = PeftConfig.from_pretrained(lora_path)
    print(f"LoRA 配置: r={config.r}, alpha={config.lora_alpha}")

    model = AutoModelForCausalLM.from_pretrained(
        base_model_name,
        torch_dtype=torch.float16,
        device_map="auto",
    )
    model = PeftModel.from_pretrained(model, lora_path)
    return model


def merge_and_save(base_model_name: str, lora_path: str, output_path: str):
    """
    将 LoRA 权重合并到基座模型并保存
    合并后的模型可用于标准推理（不需要 peft 库）
    """
    print("加载基座模型...")
    model = AutoModelForCausalLM.from_pretrained(
        base_model_name,
        torch_dtype=torch.float16,
    )

    print("加载 LoRA 权重...")
    model = PeftModel.from_pretrained(model, lora_path)

    print("合并 LoRA 权重到基座模型...")
    merged_model = model.merge_and_unload()

    print(f"保存合并后模型到 {output_path}")
    merged_model.save_pretrained(output_path, safe_serialization=True)
    tokenizer = AutoTokenizer.from_pretrained(lora_path)
    tokenizer.save_pretrained(output_path)

    # 对比大小
    import os
    lora_size = sum(
        os.path.getsize(os.path.join(lora_path, f))
        for f in os.listdir(lora_path) if os.path.isfile(os.path.join(lora_path, f))
    )
    merged_size = sum(
        os.path.getsize(os.path.join(output_path, f))
        for f in os.listdir(output_path) if f.endswith(".safetensors")
    )
    print(f"LoRA 适配器大小: {lora_size/1024:.1f} KB")
    print(f"合并后模型大小: {merged_size/1024**2:.1f} MB")

    return merged_model


def hot_swap_lora(model, new_lora_path: str):
    """
    热切换 LoRA 适配器（不重新加载基座模型）
    适合在线服务中切换不同业务场景的 LoRA
    """
    # 卸载旧 LoRA
    model = model.unload()

    # 加载新 LoRA
    model = PeftModel.from_pretrained(model, new_lora_path)
    model.eval()
    return model


# ===== 使用示例 =====
if __name__ == "__main__":
    # 1. 加载带 LoRA 的模型
    print("=" * 50)
    print("1. 加载 LoRA 模型")
    model = load_lora_model(BASE_MODEL, "./models/lora_customer_service")
    trainable = sum(p.numel() for p in model.parameters() if p.requires_grad)
    total = sum(p.numel() for p in model.parameters())
    print(f"LoRA 可训练参数: {trainable:,} / {total:,} ({trainable/total:.1%})")

    # 2. 合并并保存（生成可独立部署的完整模型）
    print("\n" + "=" * 50)
    print("2. 合并 LoRA 到基座")
    merge_and_save(
        BASE_MODEL,
        "./models/lora_customer_service",
        "./models/merged_customer_service"
    )

    # 3. 模拟多业务 LoRA 热切换
    print("\n" + "=" * 50)
    print("3. LoRA 热切换演示")
    # 假设有售前、售后、投诉三个 LoRA
    # model = hot_swap_lora(model, "./loras/pre_sales")
    # model = hot_swap_lora(model, "./loras/after_sales")
    print("热切换完成（只需加载 ~1MB 的 LoRA 权重，无需重新加载 7GB 基座）")
```

### 3.4 QLoRA 配置（4-bit 量化）

```python
# qlora_config.py
"""QLoRA 配置：4-bit 量化 + LoRA，消费级 GPU 微调 7B 模型"""

import torch
from transformers import BitsAndBytesConfig
from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training

# 4-bit 量化配置
bnb_config = BitsAndBytesConfig(
    load_in_4bit=True,
    bnb_4bit_quant_type="nf4",          # NF4 量化类型
    bnb_4bit_compute_dtype=torch.float16,  # 计算时用 fp16
    bnb_4bit_use_double_quant=True,     # 双重量化（进一步压缩）
)

# 加载量化后的模型（仅 ~4GB 显存）
# model = AutoModelForCausalLM.from_pretrained(
#     "Qwen/Qwen-7B-Chat",
#     quantization_config=bnb_config,
#     device_map="auto",
#     trust_remote_code=True,
# )

# 为 k-bit 训练准备模型
# model = prepare_model_for_kbit_training(model)

# LoRA 配置（与标准 LoRA 相同）
# lora_config = LoraConfig(
#     r=16,
#     lora_alpha=32,
#     target_modules=["q_proj", "v_proj", "k_proj", "o_proj"],
#     lora_dropout=0.05,
#     bias="none",
#     task_type="CAUSAL_LM",
# )
# model = get_peft_model(model, lora_config)

print("QLoRA 显存占用估算:")
print("  7B 模型 + 4bit 量化:     ~4 GB")
print("  LoRA 适配器 (r=16):      ~0.04 GB")
print("  优化器 + 梯度:            ~0.5 GB")
print("  总计:                     ~5 GB  (RTX 3060 12GB 可运行)")
```

### 3.5 测试验证

```python
# test_lora.py
import pytest
import os
import torch
from peft import LoraConfig, get_peft_model, TaskType, PeftModel
from transformers import AutoModelForCausalLM

class TestLoRA:
    def test_lora_config(self):
        config = LoraConfig(
            r=8, lora_alpha=16, task_type=TaskType.CAUSAL_LM,
            target_modules=["c_attn"]
        )
        assert config.r == 8
        assert config.lora_alpha == 16

    def test_trainable_params_ratio(self):
        model = AutoModelForCausalLM.from_pretrained("prajjwal1/bert-tiny")
        lora_config = LoraConfig(
            r=4, lora_alpha=8, task_type=TaskType.CAUSAL_LM,
            target_modules=["c_attn"],
        )
        peft_model = get_peft_model(model, lora_config)
        trainable = sum(p.numel() for p in peft_model.parameters() if p.requires_grad)
        total = sum(p.numel() for p in peft_model.parameters())
        assert trainable / total < 0.05  # LoRA 参数应 < 5%

    def test_lora_save_load(self, tmp_path):
        model = AutoModelForCausalLM.from_pretrained("prajjwal1/bert-tiny")
        lora_config = LoraConfig(r=4, lora_alpha=8,
                                 task_type=TaskType.CAUSAL_LM)
        peft_model = get_peft_model(model, lora_config)

        save_path = str(tmp_path / "test_lora")
        peft_model.save_pretrained(save_path)
        assert os.path.exists(os.path.join(save_path, "adapter_config.json"))
```

---

## 4 项目总结

### 4.1 优点与缺点

| 方面 | 优点 | 缺点 |
|------|------|------|
| **参数效率** | 可训练参数 < 1%，存储极小（几 MB vs 几 GB） | 对于与预训练差异极大的任务，效果可能不如全量微调 |
| **显存需求** | QLoRA 使消费级 GPU（12GB）可微调 7B 模型 | 推理时多一步基座+LoRA 加载，首次推理略慢 |
| **热切换** | 多业务场景可共用基座，切换仅需加载 1MB LoRA | 需要自行管理 LoRA 版本和基座版本兼容性 |
| **灾难性遗忘** | 基座不变，几乎不会遗忘原有能力 | LoRA 本身不解决多任务干扰问题 |

### 4.2 适用场景

| 场景 | 推荐方案 |
|------|---------|
| 消费级 GPU 微调 7B+ 大模型 | QLoRA（4bit + LoRA） |
| 多业务线共享底座 | LoRA（每业务一个 adapter） |
| 快速实验迭代 | LoRA（几分钟训练 vs 全量微调的数小时） |
| 分类任务微调 | LoRA（r=8~16）+ q_proj, v_proj |

**不适用场景**：
- 需要大幅改变模型行为（如从对话模型改为代码模型），全量微调更合适
- 推理延迟极度敏感（LoRA 推理比全量微调模型略慢 5-10%）

### 4.3 注意事项

1. **学习率**：LoRA 通常用比全量微调高 10 倍的 lr（如 2e-4 vs 2e-5），因为只训练少量参数
2. **target_modules 选择**：不同模型层的命名不同，LLaMA/Qwen 用 `q_proj, v_proj`，BERT 用 `query, value`
3. **alpha 与 r 的关系**：alpha 控制 LoRA 输出的缩放，通常 alpha = 2×r

### 4.4 常见踩坑经验

| 故障现象 | 根因 | 解决方案 |
|---------|------|---------|
| LoRA 训练效果很差 | target_modules 选错，没有覆盖关键层 | 至少包含 `q_proj` 和 `v_proj` |
| OOM 仍然发生 | 量化配置有误或未使用 device_map | 确认 `load_in_4bit=True` 和 `device_map="auto"` |
| 合并后模型变差 | merge 时使用了不同的 dtype | merge 保持原始 dtype，merge 后再转 dtype |

### 4.5 思考题

1. **初级**：在 `lora_finetune.py` 中，将 `r=8` 改为 `r=2` 和 `r=32`，分别训练并对比效果和训练速度差异。
2. **进阶**：如果你有 3 个业务场景（售前、售后、投诉），如何使用 LoRA 实现"一个基座 + 3 个 adapter"的架构，并在线上服务中根据请求类型动态选择 adapter？

（答案将在第19章末尾给出）

### 4.6 第17章思考题答案

**第17章思考题1**：
- 预期结果：冻结 0 层（全量微调）F1 最高但最慢；冻结 3-6 层 F1 略微下降但速度提升明显；冻结 9 层 F1 下降 2-3%；冻结 12 层（只训分类头）F1 下降 5-10%。最优冻结层数取决于数据量与预训练域的相似度。

**第17章思考题2**：
- 两种策略：(1) 数据采样：每个 epoch 对 50 万条任务降采样到与小任务相近的量级（如 5000 条），对 500 条任务过采样 10 倍；(2) Loss 加权：`total_loss = loss_large * 0.2 + loss_small * 0.8`。推荐两者结合。

### 4.7 推广计划提示

| 部门 | 建议行动 |
|------|---------|
| **开发团队** | 新建 AI 功能时默认考虑 LoRA 微调，评估是否需要全量微调 |
| **测试团队** | 每次新增 LoRA adapter 后运行回归测试，确认不影响其他业务 |
| **运维团队** | LoRA adapter 纳入模型版本管理，建立 adapter 与基座的兼容性矩阵 |

---

> **下一章预告**：第19章将处理长文本问题——合同、报告、日志等超长文档如何突破 512/4096 token 的限制？滑窗、先摘要后分类、层级建模等多种策略详解。
