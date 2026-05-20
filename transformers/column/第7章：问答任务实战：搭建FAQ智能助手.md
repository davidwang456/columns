# 第7章：问答任务实战：搭建 FAQ 智能助手

## 1 项目背景

### 业务场景

某 SaaS 公司的客户成功团队每天都在重复回答相同的问题：CRM 系统怎么批量导入客户？免费版和付费版的区别？API 调用频率限制是多少？7 天无理由退货怎么申请？——这些问题的答案在帮助中心文档里写得清清楚楚，但客户就是不愿意翻。

客服主管统计了一组数据：每天 2000 通在线咨询中，67% 的问题答案已经在帮助中心文档里；客服平均翻找文档耗时 42 秒；客户平均等待 90 秒后才收到回复。这意味着每天有 1340 次咨询本可以被机器人自动回答，却耗费了 15 人时的人力。

产品总监拍板：基于现有的 500 篇帮助文档，搭建一个 FAQ 智能问答机器人。客户用自然语言提问，机器人从文档中自动定位答案片段并回复，只有机器人找不到答案时才转人工。

### 痛点放大

问答（Question Answering, QA）是 NLP 中最贴近"搜索引擎体验"的任务，但实现远非字面搜索那么简单：

1. **答案不是关键词匹配**：客户问"怎么退钱？"，文档里写的是"退款流程"，字面不匹配但语义等价。传统 ES 关键词搜索只能召回包含"退钱"的文档，找不到不包含该词但语义相关的段落。
2. **答案边界定位**：即使在正确的段落里，模型需要精确找到答案片段的起始和结束位置——多一个字或少一个字都会让答案不完整。
3. **长文档滑窗**：帮助文档通常 2000-5000 字，远超 BERT 的 512 token 限制。必须对文档做滑窗切分，并在推理后把答案片段回填到原文中。

```
客户提问: "怎么退钱？"
              │
              ▼
┌──────────────────────────────────┐
│ 帮助文档 (3000字)                 │
│ ... 如需退款，请在订单详情页点击  │
│ "申请退款"，填写原因后提交，客服  │
│ 将在48小时内审核处理...           │
└──────────────────────────────────┘
              │
       [抽取式 QA 模型]
              │
              ▼
    答案: "在订单详情页点击申请退款，填写原因后提交"
```

---

## 2 项目设计

### 剧本式交锋对话

**场景**：周四上午 11:00，站会刚结束。客户成功经理张姐拉住算法工程师小陈。

---

**张姐**:"小陈，我们客服系统那个 FAQ 模块，能不能加个自动回答功能？客户问'怎么续费'，系统自动从帮助文档里找出答案。现在天天人工复制粘贴，手都要断了。"

**小胖**（凑过来）:"这不就搜索引擎嘛！你把文档全部丢进 Elasticsearch，用户搜'续费'就返回包含'续费'的段落。多简单！"

**张姐**:"不行的，昨天一个客户问'怎么取消自动扣费'，文档里写的是'如何关闭续费服务'，只差两个字，ES 搜'取消自动扣费'就搜不到这个文档。"

**小白**:"这就是抽取式 QA 的典型应用场景。ES 做关键词搜索，QA 模型做语义理解。但 QA 模型的原理是什么？为什么模型能精确定位答案的起止位置？"

**大师**（走过来，在白板上画图）:"抽取式 QA 模型的核心是 **start_logits 和 end_logits**。

输入格式是 `[CLS] 问题 [SEP] 上下文 [SEP]`。模型看完整个拼接后的序列，对每个 token 位置输出两个分数：
- `start_logits[i]`：表示第 i 个 token 是答案开头的可能性
- `end_logits[i]`：表示第 i 个 token 是答案结尾的可能性

最终答案 = `argmax(start_logits[i] + end_logits[j])`，其中 i ≤ j 且 j-i+1 ≤ max_answer_length。

这不复杂——你可以想象成在上下文中用荧光笔划一段，模型的任务就是找到最佳的起点和终点。"

**小胖**:"哦！就像玩'大家来找茬'——给一张图片（上下文），让你圈出问题提到的东西（答案）！"

**大师**:"比喻很到位。但有三个工程上的坑：

**第一，长文档滑窗切分。** 帮助文档动辄 3000 字，BERT 只吃 512 个 token。解决方案是用 `doc_stride` 参数做滑窗——比如窗口 384 tokens，每次滑动 128 tokens。最后把每个窗口的答案拼接回原文。

**第二，confidence 阈值。** 有些问题在文档里根本没有答案。比如问'老板叫什么名字'，但文档是产品说明。模型还是会强行给一个最高分的 span，只是分数很低。设置 `score_threshold` 来区分'不知道'和'找到答案'。

**第三，中文分词边界。** 模型以 token 为单位定位起止位置，而中文 token 粒度细。`'申请退款'` 被 BERT 切成 `['申','请','退','款']` 四个 token，答案边界落在不同字符之间会导致答案写回原文时拼接错误。需要用 tokenizer 的 `offset_mapping` 来对齐。"

**小白**:"那 QA 模型适合哪些场景，不适合哪些？"

**大师**:"适合有现成文档库的'从文档中找答案'场景。不适合'需要推理组合多段信息'的场景——比如问'A 产品和 B 产品有什么区别'，答案分散在两篇文档里，抽取式 QA 只能从单段文档中定位，不会跨段落合成。"

**技术映射总结**：
- QA = 输入(问题 + 上下文) → 模型对每个 token 打分 → 取最高分的起止位置 → 片段即为答案
- start_logits/end_logits = 起始分/结束分，像荧光笔标记
- doc_stride = 滑窗步长，解决长文档超限问题
- offset_mapping = token ↔ 原始字符的位置对齐表

---

## 3 项目实战

### 3.1 环境准备

```bash
pip install datasets==2.21.0 evaluate==0.4.2
```

### 3.2 构建 FAQ 数据集

#### 目标

将帮助文档整理为 `(question, context, answer)` 格式。

```python
# prepare_qa_data.py
"""构建 FAQ 问答数据集"""

from datasets import Dataset
from transformers import AutoTokenizer

# ===== 模拟帮助中心文档 =====
faq_pairs = [
    {
        "question": "如何申请退款？",
        "context": "如需申请退款，请在订单详情页点击'申请退款'按钮，填写退款原因并提交申请。"
                   "客服将在48小时内审核处理，审核通过后款项将在3-7个工作日原路退回。"
                   "请注意，已使用超过7天的商品不支持退款。",
        "answer": "在订单详情页点击'申请退款'按钮，填写退款原因并提交申请"
    },
    {
        "question": "退款多久到账？",
        "context": "退款申请审核通过后，支付宝和微信支付将在3个工作日内到账，"
                   "银行卡支付将在7个工作日内到账。如超过上述时间仍未收到退款，"
                   "请联系客服并提供退款单号。",
        "answer": "支付宝和微信支付将在3个工作日内到账，银行卡支付将在7个工作日内到账"
    },
    {
        "question": "免费版有什么限制？",
        "context": "免费版支持5个用户账号、1GB存储空间和基础报表功能。"
                   "付费版支持无限用户、100GB存储空间、高级报表和API接口接入。"
                   "详情请查看定价页面。",
        "answer": "支持5个用户账号、1GB存储空间和基础报表功能"
    },
    {
        "question": "API调用频率限制是多少？",
        "context": "免费版API调用限制为100次/小时，付费版为1000次/小时，"
                   "企业版支持自定义调用上限。超出限制后将返回429错误码。"
                   "如需提升频率限制请联系商务团队。",
        "answer": "免费版100次/小时，付费版1000次/小时，企业版可自定义"
    },
]

print(f"FAQ 数据集: {len(faq_pairs)} 条问答对")

# ===== tokenize =====
model_name = "bert-base-chinese"
tokenizer = AutoTokenizer.from_pretrained(model_name)

def preprocess_qa(examples):
    """将 QA 数据转换为模型输入格式"""
    questions = [q.strip() for q in examples["question"]]
    contexts = [c.strip() for c in examples["context"]]

    # tokenize: question + [SEP] + context
    inputs = tokenizer(
        questions,
        contexts,
        max_length=384,
        truncation="only_second",  # 只截断 context，保留完整 question
        stride=128,                # 滑窗 stride
        return_overflowing_tokens=True,
        return_offsets_mapping=True,
        padding="max_length",
    )

    # 标记哪些 token 属于答案
    sample_mapping = inputs.pop("overflow_to_sample_mapping")
    offset_mapping = inputs.pop("offset_mapping")

    start_positions = []
    end_positions = []

    for i, offsets in enumerate(offset_mapping):
        sample_idx = sample_mapping[i]
        answer = examples["answer"][sample_idx]
        context = examples["context"][sample_idx]

        # 在原始 context 中找答案的位置
        start_char = context.find(answer)
        if start_char == -1:
            # 答案不在当前滑窗片段中
            start_positions.append(0)  # 用 CLS token 占位
            end_positions.append(0)
            continue

        end_char = start_char + len(answer)

        # 将字符位置映射到 token 位置
        token_start_index = 0
        while token_start_index < len(offsets) and offsets[token_start_index][0] <= start_char:
            token_start_index += 1
        token_start_index -= 1

        token_end_index = 0
        while token_end_index < len(offsets) and offsets[token_end_index][1] <= end_char:
            token_end_index += 1
        token_end_index -= 1

        # 检查答案 token 是否超出截断范围
        if (token_start_index >= len(offsets) or
            token_end_index >= len(offsets) or
            offsets[token_start_index] == (0, 0)):
            start_positions.append(0)
            end_positions.append(0)
        else:
            start_positions.append(token_start_index)
            end_positions.append(token_end_index)

    inputs["start_positions"] = start_positions
    inputs["end_positions"] = end_positions
    return inputs


# 准备数据集
dataset = Dataset.from_list(faq_pairs)
tokenized_dataset = dataset.map(
    preprocess_qa,
    batched=True,
    remove_columns=dataset.column_names,
)
tokenized_dataset.set_format("torch")

# 划分训练/验证集
split = tokenized_dataset.train_test_split(test_size=0.2, seed=42)
qa_dataset = {"train": split["train"], "validation": split["test"]}
print(f"数据准备完成: train={len(qa_dataset['train'])}, val={len(qa_dataset['validation'])}")
```

### 3.3 模型训练

```python
# train_qa.py
"""FAQ 问答模型训练"""

from transformers import (
    AutoModelForQuestionAnswering,
    AutoTokenizer,
    Trainer,
    TrainingArguments,
    DefaultDataCollator,
)
import torch

model_name = "bert-base-chinese"
tokenizer = AutoTokenizer.from_pretrained(model_name)
model = AutoModelForQuestionAnswering.from_pretrained(model_name)

# 训练配置
training_args = TrainingArguments(
    output_dir="./output/faq_qa",
    evaluation_strategy="epoch",
    save_strategy="epoch",
    learning_rate=3e-5,
    per_device_train_batch_size=8,
    per_device_eval_batch_size=16,
    num_train_epochs=4,
    weight_decay=0.01,
    load_best_model_at_end=True,
    metric_for_best_model="eval_loss",
    save_total_limit=2,
    report_to="none",
    fp16=torch.cuda.is_available(),
)

trainer = Trainer(
    model=model,
    args=training_args,
    train_dataset=qa_dataset["train"],
    eval_dataset=qa_dataset["validation"],
    tokenizer=tokenizer,
    data_collator=DefaultDataCollator(),
)

print("开始训练 QA 模型...")
trainer.train()

# 保存模型
trainer.save_model("./models/faq_qa")
tokenizer.save_pretrained("./models/faq_qa")
print("模型已保存")
```

### 3.4 FAQ 问答服务

```python
# faq_bot.py
"""FAQ 智能问答机器人"""

import json
import torch
from transformers import pipeline, AutoTokenizer, AutoModelForQuestionAnswering
from typing import List, Dict, Optional


class FAQBot:
    def __init__(self, model_path: str, score_threshold: float = 0.3):
        self.qa_pipeline = pipeline(
            "question-answering",
            model=model_path,
            tokenizer=model_path,
            device=0 if torch.cuda.is_available() else -1,
        )
        self.threshold = score_threshold

    def answer_from_document(self, question: str, document: str) -> Dict:
        """从单篇文档中找答案"""
        result = self.qa_pipeline(question=question, context=document)

        if result["score"] < self.threshold:
            return {
                "answer": "抱歉，我在这篇文档中没有找到相关答案。",
                "score": result["score"],
                "status": "no_answer",
            }

        return {
            "answer": result["answer"],
            "score": round(result["score"], 4),
            "start": result["start"],
            "end": result["end"],
            "status": "found",
        }

    def answer_from_multiple_docs(
        self, question: str, documents: List[str]
    ) -> Dict:
        """从多篇文档中找最佳答案"""
        best_result = {"answer": "未找到答案", "score": 0, "status": "no_answer"}

        for doc in documents:
            result = self.answer_from_document(question, doc)
            if result["status"] == "found" and result["score"] > best_result["score"]:
                best_result = result

        if best_result["status"] == "no_answer":
            best_result["answer"] = "抱歉，我暂时无法回答这个问题，正在为您转接人工客服。"

        return best_result

    def answer_long_document(self, question: str, document: str,
                             chunk_size: int = 300, overlap: int = 50) -> Dict:
        """处理长文档：滑窗切分后逐段问答"""
        # 简单按句子切分（生产环境建议用 spaCy 等工具做更准确的句子切分）
        sentences = document.replace("。", "。\n").split("\n")
        chunks = []
        current_chunk = ""

        for sent in sentences:
            if len(current_chunk) + len(sent) <= chunk_size:
                current_chunk += sent
            else:
                if current_chunk:
                    chunks.append(current_chunk)
                current_chunk = sent[-overlap:] + sent if overlap > 0 else sent

        if current_chunk:
            chunks.append(current_chunk)

        # 逐段找答案，取最高分
        best = {"answer": "未找到", "score": 0, "status": "no_answer"}
        for chunk in chunks:
            result = self.answer_from_document(question, chunk)
            if result["status"] == "found" and result["score"] > best["score"]:
                best = result

        return best


# ===== 使用示例 =====
if __name__ == "__main__":
    bot = FAQBot("./models/faq_qa", score_threshold=0.3)

    # 帮助文档库
    doc_library = [
        "退款政策：订单支付后7天内可申请退款。打开订单详情页，点击申请退款按钮，"
        "填写退款原因后提交。退款将在3-7个工作日原路退回。超过7天的订单不支持退款。",
        "定价方案：免费版支持5用户1GB存储；专业版99元/月，支持20用户50GB存储；"
        "企业版299元/月，无限用户200GB存储。所有方案均支持14天免费试用。",
        "API文档：接口地址 https://api.example.com/v1，认证方式为Bearer Token。"
        "免费套餐100次/小时，专业套餐1000次/小时。超出限额返回HTTP 429。",
    ]

    questions = [
        "怎么退款？",
        "专业版多少钱？",
        "API调用上限是多少？",
        "老板是谁？",  # 文档中没有答案的问题
    ]

    for q in questions:
        result = bot.answer_from_multiple_docs(q, doc_library)
        print(f"\n👤 用户: {q}")
        print(f"🤖 机器人: {result['answer']}")
        print(f"   (置信度: {result['score']:.2%}, 状态: {result['status']})")
```

输出示例：

```
👤 用户: 怎么退款？
🤖 机器人: 打开订单详情页，点击申请退款按钮，填写退款原因后提交
   (置信度: 85.32%, 状态: found)

👤 用户: 专业版多少钱？
🤖 机器人: 99元/月
   (置信度: 92.17%, 状态: found)

👤 用户: API调用上限是多少？
🤖 机器人: 100次/小时
   (置信度: 78.45%, 状态: found)

👤 用户: 老板是谁？
🤖 机器人: 抱歉，我暂时无法回答这个问题，正在为您转接人工客服。
   (置信度: 12.34%, 状态: no_answer)
```

### 3.5 测试验证

```python
# test_faq_bot.py
import pytest
from faq_bot import FAQBot
import os

MODEL_PATH = "./models/faq_qa"

@pytest.fixture
def bot():
    if not os.path.exists(MODEL_PATH):
        pytest.skip("模型未训练")
    return FAQBot(MODEL_PATH)

class TestFAQBot:
    def test_answer_found(self, bot):
        result = bot.answer_from_document(
            "怎么退款？",
            "在订单详情页点击申请退款按钮即可退款。退款将在3天内到账。"
        )
        assert result["status"] == "found"
        assert len(result["answer"]) > 0

    def test_no_answer(self, bot):
        result = bot.answer_from_document(
            "宇宙的尽头是什么？",
            "退款流程请查看帮助中心。"
        )
        assert result["status"] == "no_answer"

    def test_multi_docs(self, bot):
        docs = ["退款需3天", "发货需1天"]
        result = bot.answer_from_multiple_docs("退款要多久？", docs)
        assert "status" in result
```

---

## 4 项目总结

### 4.1 优点与缺点

| 方面 | 优点 | 缺点 |
|------|------|------|
| **抽取式 QA** | 答案精确来自原文，可溯源，不存在幻觉 | 不能生成原文中不存在的答案，无法跨段落组合 |
| **start/end logits** | 模型结构简单，直接预测起止位置 | 长答案（>50 token）定位不准 |
| **置信度阈值** | 有效区分"知道"和"不知道"，降低错误回答风险 | 阈值调参依赖经验，过低误答，过高漏答 |
| **滑窗策略** | 突破512 token限制 | 窗口切分处恰好是答案时可能被截断 |

### 4.2 适用场景

| 场景 | 推荐方案 |
|------|---------|
| 帮助中心FAQ自动回答 | 抽取式 QA + 多文档检索 |
| 合同/法规条款查找 | 抽取式 QA + 长文档滑窗 |
| 客服知识库检索 | QA + Embedding 召回 + 重排序（见第9章） |
| 生成式回答（需推理组合） | 生成式 QA / RAG（见第21章） |

**不适用场景**：
- 需要跨多篇文档综合回答（如"对比A和B的优缺点"）
- 需要数学计算或逻辑推理的问题（如"如果买3件打8折，买5件多少钱"）

### 4.3 注意事项

1. **doc_stride 与 max_length**：`max_length=384` 留给 context 的空间约 350 tokens，`doc_stride=128` 确保每个窗口有足够的重叠
2. **answer 不在窗口内的处理**：将 start_positions 和 end_positions 都设为 0（即 CLS token），表示没有答案
3. **中文 token 对齐**：使用 `offset_mapping` 将 token 索引映射回原始字符串，否则会截出奇怪的半截字符

### 4.4 常见踩坑经验

| 故障现象 | 根因 | 解决方案 |
|---------|------|---------|
| 答案总是从第1个token开始 | answer不在当前窗口，默认设为CLS位置(0) | 将无答案窗口的 start/end 设为 CLS 位置 |
| 答案前后多/少一个"的"字 | offset_mapping 的 char 到 token 映射偏差 ±1 | 检查 offset_mapping 的 `[0]` 和 `[1]` 含义 |
| 所有答案置信度都很高 | 模型在训练时没见过"无答案"样本 | 训练集中加入 20% 的不可回答问题 |

### 4.5 思考题

1. **初级**：在 FAQ 服务中，如果文档库有 500 篇文档，对每个问题都逐篇问答显然太慢。在调用 QA 模型之前，如何快速筛选出最相关的 3-5 篇文档？（提示：想想第9章要讲什么）
2. **进阶**：如果帮助中心文档长期不更新，而实际产品已经变了（如退款从7天缩短为3天），你怎么让模型知道新答案并淘汰旧答案？

（答案将在第8章末尾给出）

### 4.6 第6章思考题答案

**第6章思考题1**：
- "B-条款 O" 代表实体长度为单个 token，后处理应将其作为有效实体保留。规则：检测到 B-* 后，若下一个标签为 O 或 B-*，则当前实体结束，当前 B-* token 作为一个完整实体输出。不应丢弃只有一个 token 的实体。

**第6章思考题2**：
- 方案一：在 BIO 标注中将括号内的别名也标为 B/I-甲方，与主实体共享同一类型。后处理时对连续 B/I 序列整体合并，得到 `"A公司（以下简称'买方'）"` 作为完整实体。
- 方案二：新增实体类型"甲方别名"，标注为 B-甲方别名，后处理中与"甲方"统一映射。两种方案都能解决，方案一更简单。

### 4.7 推广计划提示

| 部门 | 建议行动 |
|------|---------|
| **开发团队** | 将 FAQBot 集成到客服 IM 系统，监听新消息并自动推送建议答案 |
| **测试团队** | 与客户成功团队协作，收集 200 个真实用户问题作为评估集，计算 Top-1 答案准确率 |
| **运维团队** | 文档变更后自动触发模型重新评估和增量训练 pipeline |

---

> **下一章预告**：第8章将进入文本生成领域——从提示词到可控输出，掌握 temperature、top_k、top_p 等生成参数的含义和调参技巧。
