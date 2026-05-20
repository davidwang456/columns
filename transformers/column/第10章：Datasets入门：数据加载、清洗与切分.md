# 第10章：Datasets 入门：数据加载、清洗与切分

## 1 项目背景

### 业务场景

某电商公司的数据分析师小王收到了一份来自运营团队的数据需求：分析过去 6 个月的 30 万条客服工单，提取高频问题类型、统计各类型趋势变化，并导出训练数据集供算法团队训练分类模型。

小王打开数据文件时傻眼了——原始数据分布在 5 个系统中：
- CRM 系统导出的是 Excel 格式，含 15 个无关列
- 在线客服系统导出的是 JSON Lines，编码混乱（GBK/UTF-8 混杂）
- 电话录音转写文本是 CSV，大量空行和测试数据
- 邮件工单是 .txt 文件，每封一个文件，文件名是日期+编号

他试着用 Pandas 处理，但遇到了几个问题：30 万条数据一次性加载到内存（约 2GB）、数据清洗逻辑分散在多个脚本中难以维护、训练/验证/测试集切分后没有版本记录导致后续实验不可复现。

### 痛点放大

数据预处理是机器学习项目中最耗时但最不被重视的环节。根据 Google 的调研，ML 工程师 80% 的时间花在数据准备上。核心痛点：

1. **格式不统一**：CSV、JSON、Excel、Parquet、文本文件……每种格式需要不同的读取方式
2. **大文件 OOM**：Pandas 默认全量加载到内存，30 万条文本数据约 2-4GB，加上 tokenize 后内存翻倍
3. **处理逻辑不可复现**：清洗、过滤、切分的代码散落在各个 notebook 里，换个人重跑就是另一个结果
4. **缺少数据版本管理**：本次实验用的是 80/20 切分，下次用 70/15/15，无法追踪哪些数据被用于哪个实验

```
CSV ──┐
JSON ─┤
Excel ┤──→ 统一加载 ──→ 清洗过滤 ──→ 训练/验证/测试切分 ──→ Tokenize
TXT  ──┤                              │
Parquet┘                              └── 版本记录 + 缓存
```

HuggingFace Datasets 库正是为了解决这些痛点而设计的——它把 Apache Arrow 作为底层格式，支持内存映射（memory mapping）、流式处理、自动缓存和版本追踪。

---

## 2 项目设计

### 剧本式交锋对话

**场景**：周二下午 4:00，数据工坊。小王对着三个屏幕上的 Pandas DataFrame 发愁。

---

**小胖**（拿着一包瓜子）:"小王你怎么开三个屏幕还皱着眉头？数据又乱了？"

**小王**:"别提了。我把 30 万条工单从 Excel、CSV、JSON 里分别读出来，合并去重后剩 25 万。结果用 Pandas 加载 tokenizer 一跑，16GB 内存直接爆了。"

**小胖**:"那你不能分批跑吗？处理一批存一批。"

**小白**:"分批当然是方案之一。但你怎么确保每一批的随机种子一致？怎么记录当前批次是第几批？训练/验证/测试切分怎么保持一致？更重要的是——如果下周又加了 5 万条新数据，你怎么增量处理而不重新跑全量？"

**小王**:"对，这就是我愁的地方。Pandas 能干活，但缺少这些工程化的基础设施。"

**大师**（合上笔记本）:"这就是我推荐你们用 HuggingFace Datasets 库的原因。它不是 Pandas 的替代品，而是针对 ML 数据管道的工程化方案。理解三个核心概念：

**第一个核心：Apache Arrow 底层格式。**

Datasets 用 Arrow 存储数据，与 Pandas 最大的区别是——Arrow 支持内存映射（memory mapping）。数据存在磁盘上，用到哪读到哪，不需要全部加载到内存。30 万条工单文本在磁盘上可能占 2GB，但运行时只占用约 200MB 内存——因为只有当前正在处理的 batch 被加载到内存。

**第二个核心：map() + 自动缓存。**

你用 `dataset.map(tokenize_fn, batched=True)` 做预处理时，Datasets 会把 tokenize 后的结果缓存到磁盘。下次重新运行时，如果 `tokenize_fn` 没变，它直接从缓存加载，不重新计算。这就是**增量处理**的保障——新数据只处理新部分，旧数据自动跳过。

**第三个核心：可复现的切分。**

`dataset.train_test_split(test_size=0.2, seed=42)` 保证给定 same seed 永远得到相同的切分。切分后还能保存到磁盘，下次直接用 `load_from_disk()` 加载，不需要重新生成随机数——这在团队协作和 CI/CD 中特别有用。"

**小胖**:"这听起来像是给 Pandas 加了'自动记账'功能？就是你买菜、做饭、吃饭每一步都自动记下来，下次做同样的菜直接看菜谱快进？"

**大师**:"这个比喻很精准！Datasets 做的就是数据管道的'记账 + 快进'。流式处理（streaming）更是神器——你对数据做 `filter`、`map`、`shuffle` 这些操作时，Datasets 不是立刻执行，而是构建一个操作图（类似 Spark 的惰性计算），只在最终 `set_format()` 或遍历时才触发计算。"

**小白**:"那什么场景下 Pandas 更合适，什么场景下 Datasets 更合适？"

**大师**:"Pandas 适合：小数据（<100MB）的探索性分析、画图、快速 pivot 统计。Datasets 适合：大数据的 ML 预处理、与 Trainer 集成、需要可复现的数据管道、团队协作。一条简单判断原则：如果你的数据最终要喂给 `Trainer`，就用 Datasets；如果你只是在 Jupyter 里探索数据分布，用 Pandas。"

**技术映射总结**：
- Datasets = 为 ML 设计的 Pandas，底层 Arrow = 内存映射，不用全量加载
- `map()` + 缓存 = 增量处理，旧数据不重复计算
- `train_test_split(seed=42)` = 可复现切分，任何环境下跑结果一样
- `streaming=True` = 惰性计算，构建操作图但只在需要时执行

---

## 3 项目实战

### 3.1 环境准备

```bash
pip install datasets==2.21.0 pandas>=2.0.0 pyarrow>=14.0.0
```

### 3.2 多格式数据加载与清洗

#### 目标

从 CSV、JSON、Excel 等格式加载数据，统一清洗并合并。

```python
# data_pipeline.py
"""多源数据加载、清洗与切分"""

from datasets import Dataset, DatasetDict, load_dataset
import pandas as pd
import os

# ===== 1. 模拟多源数据 =====
os.makedirs("./data/raw", exist_ok=True)

# CSV 文件（CRM 系统导出）
csv_data = pd.DataFrame({
    "ticket_id": ["T001", "T002", "T003", "T004", "T005"],
    "text": [
        "订单一直没收到，请帮我查一下物流",
        "商品质量太差了要退款！",
        "这个产品怎么使用？",
        "test 测试工单 111",
        "",  # 空工单
    ],
    "category": ["物流问题", "退款", "咨询", "无效", "无效"],
    "source": ["crm", "crm", "crm", "crm", "crm"],
    "created_at": ["2024-01-15", "2024-01-16", "2024-01-17", "2024-01-18", "2024-01-19"],
})
csv_data.to_csv("./data/raw/crm_tickets.csv", index=False)

# JSON Lines（在线客服系统）
import json
json_data = [
    {"id": "J001", "content": "客服电话打不通，投诉！", "type": "投诉", "platform": "online"},
    {"id": "J002", "content": "请问包邮吗", "type": "咨询", "platform": "online"},
    {"id": "J003", "content": "已收到退款谢谢", "type": "其他", "platform": "online"},
]
with open("./data/raw/online_chat.jsonl", "w", encoding="utf-8") as f:
    for item in json_data:
        f.write(json.dumps(item, ensure_ascii=False) + "\n")

# ===== 2. 加载多源数据 =====
print("=" * 60)
print("2. 加载数据")
print("=" * 60)

# 方式1: 从 CSV 加载
csv_dataset = load_dataset("csv", data_files="./data/raw/crm_tickets.csv", split="train")
print(f"CSV 数据: {len(csv_dataset)} 条, 列: {csv_dataset.column_names}")

# 方式2: 从 JSON Lines 加载
json_dataset = load_dataset("json", data_files="./data/raw/online_chat.jsonl", split="train")
print(f"JSON 数据: {len(json_dataset)} 条, 列: {json_dataset.column_names}")

# 方式3: 从 Pandas DataFrame 直接构建
df = pd.DataFrame({
    "text": ["邮件咨询退款流程", "投诉物流损坏"],
    "label": ["咨询", "投诉"]
})
df_dataset = Dataset.from_pandas(df)
print(f"DataFrame 数据: {len(df_dataset)} 条")

# ===== 3. 数据清洗 =====
print("\n" + "=" * 60)
print("3. 数据清洗")
print("=" * 60)

def clean_text(example):
    """清洗单条文本"""
    text = example.get("text") or example.get("content", "")

    # 去除首尾空白
    text = text.strip()

    # 过滤：空文本、纯数字、纯标点
    if not text or len(text) < 3 or text.isdigit():
        return {"keep": False, "text": text}

    return {"keep": True, "text": text}

def filter_invalid(example):
    """过滤无效样本"""
    return example["keep"]

# 统一列名
csv_dataset = csv_dataset.rename_column("text", "text")

# 清洗 CSV 数据
csv_cleaned = (
    csv_dataset
    .map(clean_text)
    .filter(filter_invalid)
    .remove_columns(["keep"])
)
print(f"CSV 清洗: {len(csv_dataset)} → {len(csv_cleaned)} 条")

# 合并多源数据
# 注意: 合并前确保列名一致
json_renamed = json_dataset.rename_column("content", "text")
combined = Dataset.from_list(
    list(csv_cleaned) + list(json_renamed)
)
print(f"合并后: {len(combined)} 条")

# 去重
def compute_hash(example):
    return {"text_hash": hash(example["text"])}

before_dedup = len(combined)
combined = combined.map(compute_hash)
# 按 hash 去重
seen = set()
unique_indices = []
for i, example in enumerate(combined):
    if example["text_hash"] not in seen:
        seen.add(example["text_hash"])
        unique_indices.append(i)
combined = combined.select(unique_indices)
print(f"去重: {before_dedup} → {len(combined)} 条")

# ===== 4. 标签映射与验证 =====
print("\n" + "=" * 60)
print("4. 标签处理")
print("=" * 60)

label_name_map = {
    "物流问题": "物流问题",
    "退款": "退款退货",
    "咨询": "咨询",
    "投诉": "投诉",
    "其他": "其他",
    "无效": "无效",
}
label2id = {v: i for i, v in enumerate(sorted(set(label_name_map.values())))}
id2label = {i: v for v, i in label2id.items()}

print(f"标签映射: {label2id}")

def normalize_label(example):
    raw = example.get("category") or example.get("type", "其他")
    normalized = label_name_map.get(raw, "其他")
    example["label"] = label2id[normalized]
    return example

combined = combined.map(normalize_label)

# 标签分布
from collections import Counter
label_counts = Counter(combined["label"])
print(f"标签分布: {label_counts}")

# ===== 5. 训练/验证/测试集切分 =====
print("\n" + "=" * 60)
print("5. 数据集切分")
print("=" * 60)

# 先分出测试集（10%）
split1 = combined.train_test_split(test_size=0.1, seed=42, stratify_by_column="label")
train_val = split1["train"]
test_set = split1["test"]

# 再从 train_val 中分出验证集（约 11%，使得最终 80/10/10）
split2 = train_val.train_test_split(test_size=0.111, seed=42, stratify_by_column="label")
train_set = split2["train"]
val_set = split2["test"]

dataset = DatasetDict({
    "train": train_set,
    "validation": val_set,
    "test": test_set,
})

print(f"训练集: {len(dataset['train'])} 条")
print(f"验证集: {len(dataset['validation'])} 条")
print(f"测试集: {len(dataset['test'])} 条")
print(f"总计: {len(dataset['train']) + len(dataset['validation']) + len(dataset['test'])} 条")

# ===== 6. 保存处理后的数据集 =====
dataset.save_to_disk("./data/processed_tickets")
print("\n数据集已保存至 ./data/processed_tickets")
```

### 3.3 流式处理大文件

```python
# streaming_demo.py
"""流式处理 —— 处理超大文件不爆内存"""

from datasets import load_dataset

# 模拟超大数据集场景：直接用 streaming 模式加载
# 实际使用时替换为真实的大文件路径
print("流式处理演示（不会全量加载到内存）")

# streaming=True 返回的是 IterableDataset
stream_dataset = load_dataset(
    "csv",
    data_files="./data/raw/crm_tickets.csv",
    split="train",
    streaming=True,
)

# 流式 filter: 不会立刻执行，只在迭代时触发
filtered = stream_dataset.filter(lambda x: len(x["text"].strip()) >= 3)

# 流式 map
def add_length(example):
    example["text_len"] = len(example["text"])
    return example

mapped = filtered.map(add_length)

# 取前 3 条验证
print("前 3 条数据:")
for i, item in enumerate(mapped):
    if i >= 3:
        break
    print(f"  [{i}] 文本: {item['text'][:40]}... 长度: {item['text_len']}")

# 统计：逐条消费不会 OOM
total = 0
total_len = 0
for item in mapped:
    total += 1
    total_len += item["text_len"]

print(f"\n总计 {total} 条, 平均长度 {total_len/total:.0f} 字符")

# 注意：IterableDataset 不支持 len() 和随机访问
# 如果需要 shuffle，需要设置缓冲区：
# shuffled = stream_dataset.shuffle(buffer_size=1000, seed=42)
```

### 3.4 Dataset 与 Trainer 集成

```python
# dataset_to_trainer.py
"""将处理好的 Dataset 直接喂给 Trainer"""

from datasets import load_from_disk
from transformers import (
    AutoTokenizer,
    AutoModelForSequenceClassification,
    Trainer,
    TrainingArguments,
    DataCollatorWithPadding,
)
import torch

# 加载处理好的数据集
dataset = load_from_disk("./data/processed_tickets")

# Tokenize
model_name = "bert-base-chinese"
tokenizer = AutoTokenizer.from_pretrained(model_name)

def tokenize_fn(examples):
    return tokenizer(
        examples["text"],
        truncation=True,
        max_length=128,
        # 不用 padding="max_length"，让 DataCollator 动态 padding
    )

tokenized = dataset.map(tokenize_fn, batched=True)
tokenized = tokenized.remove_columns(["text"])
tokenized.set_format("torch", columns=["input_ids", "attention_mask", "label"])

# DataCollator 动态 padding（比固定 max_length 更省显存）
data_collator = DataCollatorWithPadding(tokenizer=tokenizer)

# 模型
model = AutoModelForSequenceClassification.from_pretrained(
    model_name, num_labels=len(set(dataset["train"]["label"]))
)

# Trainer
training_args = TrainingArguments(
    output_dir="./output/dataset_demo",
    per_device_train_batch_size=8,
    num_train_epochs=3,
    evaluation_strategy="epoch",
    save_strategy="epoch",
    report_to="none",
)

trainer = Trainer(
    model=model,
    args=training_args,
    train_dataset=tokenized["train"],
    eval_dataset=tokenized["validation"],
    tokenizer=tokenizer,
    data_collator=data_collator,
)

print("开始训练...")
trainer.train()

# 测试集评估
test_results = trainer.evaluate(tokenized["test"])
print(f"测试集: {test_results}")
```

### 3.5 测试验证

```python
# test_data_pipeline.py
import pytest
import os
from datasets import load_from_disk

DATA_PATH = "./data/processed_tickets"

@pytest.mark.skipif(not os.path.exists(DATA_PATH), reason="数据未生成")
class TestDataPipeline:
    def test_dataset_structure(self):
        ds = load_from_disk(DATA_PATH)
        assert "train" in ds
        assert "validation" in ds
        assert "test" in ds

    def test_no_empty_text(self):
        ds = load_from_disk(DATA_PATH)
        for split in ds.values():
            for example in split:
                assert len(example["text"].strip()) >= 3

    def test_label_range(self):
        ds = load_from_disk(DATA_PATH)
        max_label = max(max(split["label"]) for split in ds.values())
        assert max_label >= 0

    def test_split_no_overlap(self):
        """验证各 split 之间无数据泄漏"""
        ds = load_from_disk(DATA_PATH)
        train_texts = set(ds["train"]["text"])
        val_texts = set(ds["validation"]["text"])
        test_texts = set(ds["test"]["text"])
        assert train_texts.isdisjoint(val_texts)
        assert train_texts.isdisjoint(test_texts)
        assert val_texts.isdisjoint(test_texts)
```

---

## 4 项目总结

### 4.1 优点与缺点

| 方面 | 优点 | 缺点 |
|------|------|------|
| **内存映射** | 大文件（GB级）不会 OOM，只加载当前 batch | 随机访问大文件时磁盘 IO 可能成为瓶颈 |
| **流式处理** | 惰性计算，操作图优化，适合 ETL pipeline | 不支持 `len()` 和随机索引，调试不直观 |
| **自动缓存** | 重复运行跳过已处理步骤，节省大量时间 | 缓存文件占用磁盘（可用 `cleanup_cache_files()` 清理） |
| **与 Trainer 集成** | 无缝对接，`data_collator` 自动动态 padding | 自定义训练循环时需要额外处理数据格式 |

### 4.2 适用场景

| 场景 | 推荐方案 |
|------|---------|
| 几万条数据的预处理 | `load_dataset` + `map` + `save_to_disk` |
| 百万级数据 | `streaming=True` 流式处理 |
| 多源数据合并 | 统一列名后 `concatenate_datasets` |
| 团队协作可复现 | `save_to_disk` + 版本控制 + seed 固定 |

**不适用场景**：
- 复杂的关系型数据 join/groupby 操作（Pandas/SQL 更合适）
- 每次都需要完全不同的预处理逻辑（缓存失效，Datasets 优势不显）

### 4.3 注意事项

1. **`map()` 中的函数必须是可 pickle 的**：lambda 表达式可能导致序列化失败，建议定义为具名函数
2. **`batched=True` 大幅加速**：单条处理 vs 批处理效率差 10 倍以上
3. **`stratify_by_column` 需要数值 label**：分层采样仅支持数值列，字符标签需先转为 label id

### 4.4 常见踩坑经验

| 故障现象 | 根因 | 解决方案 |
|---------|------|---------|
| `map()` 后数据消失 | 函数没有返回正确格式的 dict | 确保返回 `{"key": value}` 格式的字典 |
| 缓存读取极慢 | 缓存目录碎片化 | 定期调用 `Dataset.cleanup_cache_files()` 清理旧缓存 |
| `IterableDataset` 不支持 `train_test_split` | 流式模式下无随机访问 | 先 `.take(n)` 取出小样本验证，或用 `split="train[:80%]"` |

### 4.5 思考题

1. **初级**：在 `data_pipeline.py` 中，将 `streaming=True` 开启后，尝试调用 `len(dataset)`，会发生什么？为什么？
2. **进阶**：你的数据每周新增 1 万条。设计一个增量处理方案，使得每周只需处理新增数据而不重新跑全量。（提示：利用 `Dataset.save_to_disk()` + 文件名时间戳 + `concatenate_datasets()`）

（答案将在第11章末尾给出）

### 4.6 第9章思考题答案

**第9章思考题1**：
- `distiluse-base-multilingual-cased-v2` 是知识蒸馏版，参数量约 135M，比 `paraphrase-multilingual-MiniLM-L12-v2`（118M）略大。两者编码速度接近（~1000 条/秒），但 MiniLM 在中文语义相似度任务（STS-B 中文）上通常略优。选择依赖具体场景评估。

**第9章思考题2**：
- 混合检索方案：(1) 对 question 做 Embedding，检索相似 question（问题匹配）；(2) 对 answer 做 Embedding，检索相关 answer（答案匹配）；(3) 融合分数 `final_score = α * question_score + (1-α) * answer_score`，α 建议 0.6~0.7；(4) 最终返回融合排序后的 Top K 结果。这种方法称为 Hybrid Search，兼具问题匹配和答案相关性。

### 4.7 推广计划提示

| 部门 | 建议行动 |
|------|---------|
| **开发团队** | 建立统一的数据管道脚本 `data_pipeline.py`，所有模型的训练数据统一从此入口产出 |
| **测试团队** | 在 CI/CD 中加入数据验证步骤：运行 `test_data_pipeline.py` 确认无数据泄漏和标签异常 |
| **运维团队** | 监控磁盘缓存大小，设置定期清理策略（保留最近 2 个版本的缓存，删除更早的） |

---

> **下一章预告**：第11章将进入 Trainer 训练框架的正规化使用——详解 TrainingArguments 的核心参数、动态 padding 的 DataCollator、断点续训与最佳模型加载。这是基础篇最后一个技能模块，为第16章的综合实战铺平道路。
