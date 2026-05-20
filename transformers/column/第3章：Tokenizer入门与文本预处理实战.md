# 第3章：Tokenizer 入门与文本预处理实战

## 1 项目背景

### 业务场景

客服中心运营主管王姐发现一个诡异现象：智能工单分类系统的准确率在内部测试集上高达 92%，但上线两周后实际准确率只有 71%。她找来算法工程师小陈排查原因。

小陈对比了训练数据和线上数据，发现三个致命差异：

1. **用户输入不规范**："为撒子我的单子还没有到"（含方言、错别字）、"昨天上午9:30下的单，今天下午3:00还没发货😡"（含时间、数字、emoji）、"商品描述说颜色是【雾霾蓝】，收到的是【天空蓝】，我就想问客服这TM叫雾霾蓝？？？"（含标点滥用、敏感词）。

2. **长度分布偏差**：训练数据平均长度 50 tokens，但线上用户投诉工单平均 180 tokens，超过 15% 的工单超过模型最大输入长度 512 tokens，被直接截断。

3. **特殊字符处理不一致**：训练时的 tokenizer 对 emoji 和全角符号做了特殊处理，但线上用的 tokenizer 配置不同，同一句"亲，东西不错🙂"被编码为完全不同的 token 序列。

### 痛点放大

Tokenization（分词）是文本进入模型的第一道工序，却最容易被忽视。三个核心痛点：

```
┌──────────────────┐
│ 原始文本           │  "亲，东西不错🙂，但物流太慢😡"
└───────┬──────────┘
        │ Tokenizer
        ▼
┌──────────────────┐
│ Token 序列         │  可能产生的问题：
│ [101, 872, ...]   │  1. 错别字/方言 → [UNK]（未知 token）
│                    │  2. Emoji → 被丢弃或映射错误
│                    │  3. 超长文本 → 尾部被截断丢失信息
│                    │  4. 中英文混合 → 边界切分混乱
└──────────────────┘
```

如果 Tokenizer 这一步出了问题，后续的模型训练和推理都建立在错误的基础上。本章的目标是：掰开 Tokenizer 内核，搞清楚 BPE/WordPiece/SentencePiece 三种分词策略的本质差异，并用实战构建一个健壮的文本预处理脚本。

---

## 2 项目设计

### 剧本式交锋对话

**场景**：周三下午 3:00，工位区。小陈正对着密密麻麻的 token ID 数组发呆。小胖晃过来。

---

**小胖**："小陈你在数啥呢？我看看……101、872、1962……这是啥密码吗？"

**小陈**："这是 Tokenizer 把'亲，东西不错🙂'编码后的 token ID。我在排查线上分类准确率为什么比测试集低了 20 个点。"

**小胖**："等下，那个😊表情也变成数字了？我以为模型直接读中文呢。这不就是翻译成暗号嘛——食堂后厨也不看中文菜单，都是编号：1号鱼香肉丝、2号宫保鸡丁。"

**小白**（凑过来）:"小胖你这比喻只能解释 token ID——但 Tokenizer 远不止这个。我问你：'自然语言处理'这个词，是应该整体作为一个 token，还是拆成'自然'、'语言'、'处理'三个 token？还是更细粒度拆成'自'、'然'、'语'……？不同的拆分策略影响模型对语义的理解。"

**小胖**："呃……这也有讲究？我以为跟查字典一样，每个词对应一个 ID。"

**大师**（拿着白板笔走过来）:"这正是 Tokenizer 的核心矛盾：**词表大小 vs 分词粒度**。词表越大，每个 token 越完整（比如整个词作为一个 token），但词表会大到无法训练。词表越小，token 粒度越细（比如拆到字符级别），但语义信息被稀释，需要更多的 token 来表示同一段文本。"

大师在白板上画了一张图：

```
词表小（字符级） ←——————————→ 词表大（词级）
 token 粒度细                token 粒度完整
 token 数量多                token 数量少
 OOV 问题少                  OOV 问题多
"自然语言处理"                "自然语言处理"
→ ["自","然","语","言",       → ["自然语言处理"]
    "处","理"]               (但 "自然语言处理" 不在
 (6个token)                   词表中 → [UNK])
```

**大师**:"解决这个矛盾的三个主流方案：BPE、WordPiece、SentencePiece。"

**小白**:"BPE 我听说过，好像 GPT 系列用的就是这个？它是怎么选'哪些词该合并'的？"

**大师**:"BPE（Byte Pair Encoding）从字符开始，统计训练语料中相邻字符对的共现频率，每次合并频率最高的那一对，直到达到预设的词表大小。比如 'low' 出现 5 次，'lower' 出现 2 次，'newest' 出现 6 次，'newer' 出现 3 次——BPE 会发现 'e' 和 'r' 经常一起出现，于是创建 'er' token。"

**小胖**:"WordPiece 呢？跟 BPE 有啥不一样？"

**大师**:"WordPiece（BERT 使用）不是按频率合并，而是按**能最大化训练语料似然度**的准则来合并——它选的合并规则是'这个合并最能提高语言模型的概率'。还有一个表面差异：WordPiece 在子词开头不加特殊标记，但在非词首的子词前加 '##'。比如 'playing' 可能被切为 ['play', '##ing']。"

**小白**:"那 SentencePiece 呢？我经常在 T5、LLaMA 的配置里看到。"

**大师**:"SentencePiece 最大的特点是把空格也当作普通字符处理，不需要预先分词。它通常配合 Unigram 算法或 BPE 使用。好处是对所有语言（包括中文、日文等不用空格分词的语言）一视同仁，不需要写语言特定的预处理规则。这也是为什么多语言模型（mT5、XLM-R）几乎都用 SentencePiece。"

**技术映射总结**：
- BPE = 按共现频率合并字符 → GPT、RoBERTa 使用
- WordPiece = 按似然度准则合并 → BERT、DistilBERT 使用
- SentencePiece = 空格字符化 + Unigram/BPE → T5、LLaMA、XLM-R 使用

**小胖**:"那我还有个实际问题——我们线上工单经常有超长文本和 emoji，Tokenizer 怎么处理？"

**大师**:"关键参数三个：**truncation**（截断策略）、**padding**（填充策略）、**max_length**（最大长度）。emoji 的问题取决于词表是否收录——新模型通常用字节级 BPE，把 emoji 也当作字节序列编码，几乎不会有 OOV。老模型（如原始 BERT）对 emoji 可能输出 [UNK]。我们需要在预处理阶段做好兼容。"

---

## 3 项目实战

### 3.1 环境准备

```bash
# 延续上一章的环境，安装额外依赖
pip install matplotlib>=3.7.0  # 用于文本长度分布可视化
```

### 3.2 分词器对比实验

#### 目标

加载三个不同分词策略的 Tokenizer，对比同一段文本的分词结果。

```python
# tokenizer_compare.py
from transformers import AutoTokenizer

text = "亲，东西不错🙂，但物流太慢了😡！能不能帮我查一下订单号XH20240501的状态？"

print(f"原始文本: {text}\n")
print(f"文本长度（字符数）: {len(text)}\n")

# 1. BERT 中文分词器（WordPiece）
bert_tokenizer = AutoTokenizer.from_pretrained("bert-base-chinese")
bert_tokens = bert_tokenizer.tokenize(text)
bert_ids = bert_tokenizer.encode(text)
print("=" * 60)
print("BERT (WordPiece):")
print(f"  Tokens ({len(bert_tokens)}): {bert_tokens}")
print(f"  IDs ({len(bert_ids)}): {bert_ids}")
print(f"  Emoji '🙂' 是否被识别: {'[UNK]' not in bert_tokens}")

# 2. RoBERTa 中文分词器（BPE）
roberta_tokenizer = AutoTokenizer.from_pretrained("hfl/chinese-roberta-wwm-ext")
roberta_tokens = roberta_tokenizer.tokenize(text)
roberta_ids = roberta_tokenizer.encode(text)
print("=" * 60)
print("RoBERTa (BPE):")
print(f"  Tokens ({len(roberta_tokens)}): {roberta_tokens}")
print(f"  IDs ({len(roberta_ids)}): {roberta_ids}")

# 3. XLM-R 多语言分词器（SentencePiece）
xlmr_tokenizer = AutoTokenizer.from_pretrained("xlm-roberta-base")
xlmr_tokens = xlmr_tokenizer.tokenize(text)
xlmr_ids = xlmr_tokenizer.encode(text)
print("=" * 60)
print("XLM-R (SentencePiece):")
print(f"  Tokens ({len(xlmr_tokens)}): {xlmr_tokens}")
print(f"  IDs ({len(xlmr_ids)}): {xlmr_ids}")
```

运行输出示例：

```
原始文本: 亲，东西不错🙂，但物流太慢了😡！能不能帮我查一下订单号XH20240501的状态？

文本长度（字符数）: 37

============================================================
BERT (WordPiece):
  Tokens (29): ['亲', '，', '东', '西', '不', '错', '[UNK]', '[UNK]'...]
  IDs (31): [101, 3563, 8024, 691, 5647, 679, 6956, 100, 100...]
  Emoji '🙂' 是否被识别: False

============================================================
RoBERTa (BPE):
  Tokens (28): ['亲', '，', '东', '西', '不', '错', '<unk>'...]
  IDs (30): [101, 2674, 511, 693, 668, 905, 1...]
  Emoji '🙂' 是否被识别: False

============================================================
XLM-R (SentencePiece):
  Tokens (28): ['▁亲', '，', '东西', '不错', '🙂', '，', '但', ...]
  IDs (29): [0, 22492, 3, 96057, 49171, 15540, 3, ...]
  Emoji '🙂' 是否被识别: True
```

**关键发现**：BERT 和 RoBERTa 的中文词表未收录 emoji，`🙂` 和 `😡` 均被映射为 `[UNK]` 或 `<unk>`；XLM-R 的 SentencePiece 词表字节级编码，所有的 emoji 都能被正常编码。

### 3.3 文本长度分布分析

#### 目标

加载一批客服工单数据，分析文本长度分布，确定合理的 `max_length` 值。

```python
# length_analysis.py
import json
import matplotlib.pyplot as plt
from transformers import AutoTokenizer

# 模拟客服工单数据
sample_tickets = [
    "我的订单什么时候发货？已经等了三天了。",
    "收到的商品和网页描述完全不一样，颜色从雾霾蓝变成了天空蓝，材质从纯棉变成了化纤，我要退货退款！请尽快处理，这是我第三次联系你们了，前两次客服都说让我等消息，到现在也没有回复。订单号XH20240501，金额299元。",
    "客服态度很好，问题解决了。",
    "投诉！！！物流显示已签收，但我根本没收到！！快递员电话打不通！这是什么服务？？？😡😡😡",
    "您好，我想咨询一下，这个产品支持7天无理由退货吗？如果可以的话，退货流程是什么样的？运费谁来承担？",
    # ... 更多工单 ...
]

# 用三个常见模型测试长度分布
models = {
    "bert-base-chinese": "BERT中文",
    "hfl/chinese-roberta-wwm-ext": "RoBERTa中文",
    "xlm-roberta-base": "XLM-R多语言"
}

for model_name, label in models.items():
    tokenizer = AutoTokenizer.from_pretrained(model_name)
    lengths = [len(tokenizer.encode(t)) for t in sample_tickets]

    print(f"\n{label} 长度统计:")
    print(f"  平均: {sum(lengths)/len(lengths):.0f} tokens")
    print(f"  最大: {max(lengths)} tokens")
    print(f"  最小: {min(lengths)} tokens")
    print(f"  超过512的: {sum(1 for l in lengths if l > 512)}/{len(lengths)}")

# 绘制长度分布
plt.figure(figsize=(10, 4))
all_lengths = []
for model_name, label in models.items():
    tokenizer = AutoTokenizer.from_pretrained(model_name)
    lengths = [len(tokenizer.encode(t)) for t in sample_tickets]
    all_lengths.append(lengths)
    plt.hist(lengths, alpha=0.5, label=label, bins=20)

plt.axvline(x=512, color='r', linestyle='--', label='max_length=512')
plt.xlabel('Token Count')
plt.ylabel('Frequency')
plt.title('不同分词器的编码长度分布')
plt.legend()
plt.grid(True, alpha=0.3)
plt.savefig('token_length_distribution.png', dpi=150)
print("\n分布图已保存至 token_length_distribution.png")
```

### 3.4 健壮的文本预处理脚本

#### 目标

构建一个生产级别的预处理脚本，处理以下边缘情况：
- 超长文本（>512 tokens）的智能截断
- Emoji 和特殊符号的保留
- 繁简体统一
- 多余空白符清理
- 结构化日志输出

```python
# text_preprocessor.py
"""客服工单文本预处理 —— 生产级"""

import re
import logging
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass
from transformers import AutoTokenizer

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


@dataclass
class PreprocessResult:
    original_text: str
    cleaned_text: str
    input_ids: List[int]
    attention_mask: List[int]
    token_count: int
    was_truncated: bool
    warnings: List[str]


class TicketPreprocessor:
    """客服工单文本预处理器"""

    def __init__(
        self,
        model_name: str = "xlm-roberta-base",
        max_length: int = 512,
        truncation_side: str = "right"  # 默认截断尾部
    ):
        self.tokenizer = AutoTokenizer.from_pretrained(model_name)
        self.max_length = max_length
        self.truncation_side = truncation_side
        logger.info(f"预处理器初始化: model={model_name}, max_length={max_length}")

    def clean_text(self, text: str) -> Tuple[str, List[str]]:
        """清洗文本，保留必要信息"""
        warnings = []

        # 1. 检查空输入
        if not text or not text.strip():
            raise ValueError("输入文本为空")

        # 2. 统一全角符号为半角（保留中文标点语义）
        original = text
        # 全角逗号 → 半角逗号
        text = text.replace("\uff0c", ",")

        # 3. 清理多余空白（保留单个空格，中文场景按需保留）
        text = re.sub(r"\s+", " ", text).strip()

        # 4. 统一繁简体（可选，视需求而定）
        # text = self._convert_traditional_to_simplified(text)

        # 5. 检测超长警告
        if len(text) > 2000:
            warnings.append(f"文本长度 {len(text)} 字符，建议前端做字数限制")

        # 6. 记录是否包含 emoji
        emoji_pattern = re.compile(
            "[\U0001F600-\U0001F64F\U0001F300-\U0001F5FF"
            "\U0001F680-\U0001F6FF\U0001F1E0-\U0001F1FF]",
            flags=re.UNICODE
        )
        if emoji_pattern.search(text):
            warnings.append("文本包含 emoji，请确认模型词表支持")

        return text, warnings

    def preprocess(self, text: str, return_full: bool = True) -> PreprocessResult:
        """完整预处理流程"""
        warnings = []
        cleaned, clean_warnings = self.clean_text(text)
        warnings.extend(clean_warnings)

        # 编码
        encoded = self.tokenizer(
            cleaned,
            max_length=self.max_length,
            truncation=True,
            padding="max_length",  # 固定长度，方便批处理
            return_tensors="pt",
            return_attention_mask=True,
        )

        input_ids = encoded["input_ids"][0].tolist()
        attention_mask = encoded["attention_mask"][0].tolist()

        # 计算实际 token 数（不计 padding）
        actual_tokens = sum(attention_mask)
        was_truncated = actual_tokens == self.max_length

        if was_truncated:
            # 估算被截断的内容量
            full_ids = self.tokenizer.encode(cleaned)
            warnings.append(f"文本被截断: 原始 {len(full_ids)} tokens → {self.max_length} tokens")

        logger.info(
            f"预处理完成: chars={len(text)} → tokens={actual_tokens}"
            f"{' (截断)' if was_truncated else ''}"
        )

        return PreprocessResult(
            original_text=text,
            cleaned_text=cleaned,
            input_ids=input_ids,
            attention_mask=attention_mask,
            token_count=actual_tokens,
            was_truncated=was_truncated,
            warnings=warnings,
        )

    def batch_preprocess(self, texts: List[str]) -> List[PreprocessResult]:
        """批量预处理"""
        results = []
        for i, text in enumerate(texts):
            try:
                result = self.preprocess(text)
                results.append(result)
            except Exception as e:
                logger.error(f"第 {i} 条预处理失败: {e}")
                results.append(PreprocessResult(
                    original_text=text,
                    cleaned_text="",
                    input_ids=[],
                    attention_mask=[],
                    token_count=0,
                    was_truncated=False,
                    warnings=[f"预处理失败: {str(e)}"]
                ))
        return results


# ===== 使用示例 =====
if __name__ == "__main__":
    preprocessor = TicketPreprocessor(model_name="xlm-roberta-base")

    test_cases = [
        "正常文本：我的订单什么时候发货？",
        "超长文本：" + "这个产品真的是太好了太棒了简直无敌了" * 50,
        "含emoji：东西收到了质量很好🙂🙂🙂很开心",
        "含特殊符号：订单号【XH20240501】金额￥299.00，联系📞400-123-4567",
        "",  # 空文本，测试异常处理
    ]

    for i, text in enumerate(test_cases):
        print(f"\n--- 测试用例 {i+1} ---")
        print(f"输入: {text[:80]}{'...' if len(text) > 80 else ''}")
        try:
            result = preprocessor.preprocess(text)
            print(f"Token 数: {result.token_count}")
            print(f"是否截断: {result.was_truncated}")
            if result.warnings:
                print(f"警告: {result.warnings}")
        except Exception as e:
            print(f"错误: {e}")
```

运行输出示例：

```
--- 测试用例 1 ---
输入: 正常文本：我的订单什么时候发货？
Token 数: 13
是否截断: False

--- 测试用例 2 ---
输入: 超长文本：这个产品真的是太好了太棒了简直无敌了这个产品真的是太好了太棒了简直无敌了...
Token 数: 512
是否截断: True
警告: ['文本被截断: 原始 708 tokens → 512 tokens']

--- 测试用例 3 ---
输入: 含emoji：东西收到了质量很好🙂🙂🙂很开心
Token 数: 17
是否截断: False
警告: ['文本包含 emoji，请确认模型词表支持']

--- 测试用例 4 ---
输入: 含特殊符号：订单号【XH20240501】金额￥299.00，联系📞400-123-4567
Token 数: 34
是否截断: False
警告: ['文本包含 emoji，请确认模型词表支持']

--- 测试用例 5 ---
输入: 
错误: 输入文本为空
```

### 3.5 测试验证

```python
# test_preprocessor.py
import pytest
from text_preprocessor import TicketPreprocessor, PreprocessResult

@pytest.fixture
def preprocessor():
    return TicketPreprocessor(model_name="xlm-roberta-base", max_length=128)

class TestTicketPreprocessor:
    def test_normal_text(self, preprocessor):
        result = preprocessor.preprocess("这是正常的测试文本")
        assert result.token_count > 0
        assert not result.was_truncated
        assert len(result.input_ids) == 128  # max_length padding

    def test_empty_text(self, preprocessor):
        with pytest.raises(ValueError):
            preprocessor.preprocess("")

    def test_emoji_text(self, preprocessor):
        result = preprocessor.preprocess("产品很好🙂")
        assert result.token_count > 0
        assert any("emoji" in w.lower() for w in result.warnings)

    def test_long_text_truncation(self, preprocessor):
        long_text = "测试文本" * 200
        result = preprocessor.preprocess(long_text)
        assert result.was_truncated
        assert len([w for w in result.warnings if "截断" in w]) > 0

    def test_attention_mask(self, preprocessor):
        result = preprocessor.preprocess("测试")
        # 有效 token 数应等于 attention_mask 中 1 的数量
        assert sum(result.attention_mask) == result.token_count
```

---

## 4 项目总结

### 4.1 优点与缺点

| 方面 | 优点 | 缺点 |
|------|------|------|
| **BPE** | 实现简单，OOV 问题少，适合多语言 | token 边界不够语义化，中文场景 token 偏碎片化 |
| **WordPiece** | 子词切分相对语义化，`##` 前缀标识清晰 | 中文支持需额外训练词表，emoji 和特殊字符易出 [UNK] |
| **SentencePiece** | 语言无关，空格字符化，天然支持多语言和 emoji | 分词结果可读性差（空格变成 `▁`），调试不直观 |
| **Fast Tokenizer** | Rust 实现，批量编码快 5-10 倍 | 部分模型不支持 Fast 版（如 SentencePiece 模型） |

### 4.2 适用场景

| 场景 | 推荐 Tokenizer |
|------|---------------|
| 中文单语分类/NER | `bert-base-chinese` + WordPiece |
| 中英混合/多语言 | `xlm-roberta-base` + SentencePiece |
| 对话/生成 | 按基底模型自带 Tokenizer（如 Qwen、LLaMA） |
| 行业术语密集 | 自行训练 BPE/SentencePiece 词表扩展 |

**不适用场景**：
- 直接对 tokenizer 输出做人工阅读和分析（token 序列可读性差）
- 不做预处理直接将用户原始文本喂给模型（需要先清洗和规范化）

### 4.3 注意事项

1. **模型与 Tokenizer 严格对齐**：`bert-base-chinese` 的 tokenizer 不能配 `chinese-roberta` 的模型，否则 token ID 映射错位，预测结果完全随机
2. **特殊 token 保留**：`[CLS]`、`[SEP]`、`[PAD]`、`[MASK]` 等特殊 token 不能随意删除，否则模型行为不可预期
3. **截断策略选择**：`truncation="longest_first"`（智能截断）比 `only_first`/`only_second` 更适合多段落输入

### 4.4 常见踩坑经验

| 故障现象 | 根因 | 解决方案 |
|---------|------|---------|
| 输入中文，输出全是 [UNK] | tokenizer 词表无中文字符 | 换用中文预训练模型或 multilingual 版本 |
| `token_type_ids` 导致预测偏差 | 单句输入不必要传 `token_type_ids` | 去掉 token_type_ids 或全传 0 |
| Fast tokenizer 编码结果与 Slow 不一致 | Fast 版并行处理浮点误差累积 | 对比输出差异，小于阈值可接受；关键场景用 Slow 版 |
| 线上模型输入预处理与训练不一致 | 训练和推理用了不同 tokenizer 配置 | 把 tokenizer 配置与模型一起保存，推理时加载同一配置 |

### 4.5 思考题

1. **初级**：用 `AutoTokenizer.from_pretrained("bert-base-chinese")` 编码 "Hello 世界！"，观察中英文混合时 WordPiece 的切分行为。为什么英文部分会被切得更细？
2. **进阶**：假设你有 10 万条医疗领域工单，需要训练一个专用 tokenizer。你会选择 BPE 还是 SentencePiece？需要设置多大的词表？为什么？

（答案将在第4章末尾给出）

### 4.6 推广计划提示

| 部门 | 建议行动 |
|------|---------|
| **开发团队** | 将 `text_preprocessor.py` 封装为团队公共库，统一所有 NLP 服务的文本预处理入口 |
| **测试团队** | 基于 `TickerPreprocessor` 的边界测试构建测试套件，覆盖 emoji、超长文本、空输入、SQL 注入式文本 |
| **运维团队** | 监控 Tokenizer 编码耗时（P95），若 `batch_encode_plus` 耗时超过 100ms 切换为 Fast Tokenizer |

---

> **下一章预告**：第4章将深入模型加载机制——`from_pretrained` 背后到底发生了什么？config.json、pytorch_model.bin、safetensors 各有什么作用？如何搭建离线模型加载器？
