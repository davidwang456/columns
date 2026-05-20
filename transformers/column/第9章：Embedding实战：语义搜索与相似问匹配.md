# 第9章：Embedding 实战：语义搜索与相似问匹配

## 1 项目背景

### 业务场景

客服中心有一个积累了 3 年的"历史问答库"，包含约 15 万条已解决的问答对（用户原始问题 + 客服标准回复）。运营团队发现一个规律：每天新进的 8000 条咨询中，约 40% 与历史问题高度相似——比如"怎么改收货地址"和"我想换个地址怎么操作"，表述不同但意图相同。

运营主管提出需求：当新工单进来时，系统自动从历史问答库中匹配最相似的 Top 3 已解决问题，推送给客服作为参考。如果匹配度极高（>0.95），甚至可以自动回复。

第一版方案用 Elasticsearch + 关键词匹配实现了，但效果很差。测试数据显示：
- "如何注销账户"匹配到"如何注册账户"（关键词重叠 3/5，但语义完全相反）
- "退钱的流程是什么" 搜不到"退款流程说明"（"退钱"和"退款"字面不匹配）
- 长尾问题"ios 端闪退怎么办"完全匹配不到"iPhone 客户端崩溃处理"

### 痛点放大

关键词搜索的核心缺陷是**只看字符不看语义**。Embedding（向量化）可以解决这个问题：

```
关键词搜索:  "退钱流程" → 在文档中找包含"退钱"+"流程"的文档 → 漏掉"退款流程说明"
语义搜索:    "退钱流程" → 转成768维向量 → 找最相似的文档向量 → 命中"退款流程说明"
```

但 Embedding 方案也有自己的坑：
1. **Pooling 策略选择**：BERT 输出每个 token 的向量，怎么聚合成一个句子向量？CLS token？Mean pooling？Max pooling？不同的策略差异巨大。
2. **向量质量**：未经微调的 BERT 输出的句子向量质量较差，需要专门训练的 Sentence Embedding 模型或对比学习微调。
3. **大规模检索效率**：15 万条向量做暴力相似度搜索太慢，需要向量索引（如 FAISS）加速。

---

## 2 项目设计

### 剧本式交锋对话

**场景**：周一上午 10:00，AI Lab。小陈正在把 15 万条历史问答编码为向量，电脑风扇狂转。

---

**小胖**（端着豆浆走过来）:"小陈你电脑在挖矿吗？风扇转得跟直升机似的。你这是要把所有文档都变成数学向量？"

**小陈**:"是的，我在把 15 万条问答对做 Embedding。Embedding 就是把文本变成一个固定长度的向量——比如 768 维的浮点数数组。两条语义相近的文本，向量就很接近。"

**小胖**:"那向量怎么比'接近'？怎么算？"

**小陈**:"余弦相似度。两个向量的夹角越小（余弦值越接近 1），就越相似。'退款流程'和'怎么退钱'的向量余弦约 0.89，'退款流程'和'发货时间'的向量余弦约 0.12。"

**小白**（放下手中的书）:"但 BERT 输出的不是一个向量——每个 token 都有一个 768 维向量。你怎么把一个句子浓缩成一个向量？"

**大师**（正好端着咖啡走过）:"这是 Embedding 里最核心也最容易踩坑的问题——**Pooling 策略**。

假设输入'这个产品很好'，BERT 输出 6 个 token（含 CLS/SEP）的向量：
- `[CLS]` → v0
- `这` → v1, `个` → v2, `产品` → v3, `很` → v4, `好` → v5, `[SEP]` → v6

三种 Pooling：
- **CLS Pooling**：直接用 `[CLS]` token 的向量 v0。BERT 预训练时 CLS 被设计为句子的聚合表示，但实测效果一般。
- **Mean Pooling**：对所有 token 的向量取平均（含 CLS/SEP）。最常用，效果稳定。
- **Max Pooling**：每个维度取最大值。对突出特征敏感但丢失整体信息。

研究已经证明：**Mean Pooling 在大多数语义相似度任务上表现最好**，尤其是在带 attention_mask 的情况下——对有效 token 取均值，忽略 PAD。

但有个更根本的问题：BERT 原始预训练任务（MLM + NSP）并不是直接为句子向量相似度优化的。用原生 BERT mean pooling 的效果甚至不如 2019 年的 Sentence-BERT 这种专门训练过的模型。"

**小胖**:"Sentence-BERT 是啥？"

**大师**:"BERT 的一个变体，用 Siamese 网络结构 + 对比学习训练，专门让语义相似的句子向量靠得更近。它用 mean pooling 就非常有效。实际工程中，优先用 `sentence-transformers` 库的模型——比如 `paraphrase-multilingual-MiniLM-L12-v2`，这个模型 118M 参数，速度快，中文效果也很好。"

**小白**:"那 15 万条向量怎么快速检索？总不能一条条比吧。"

**大师**:"这就是向量索引出场的时候了。FAISS（Facebook AI Similarity Search）是当前最流行的方案：
- `IndexFlatIP`：暴力搜索，精确但慢
- `IndexIVFFlat`：倒排索引加速，牺牲少量精度换速度
- `IndexHNSWFlat`：基于图的近似搜索，速度快精度高

15 万数据量用 `IndexFlatIP`（内积相似度）完全够，毫秒级。百万级才需要 IVFPQ 或 HNSW。"

**技术映射总结**：
- Embedding = 把文本转成固定维度向量，像给每句话拍一张"语义照片"
- Pooling = 从多 token 向量合成句子向量的方法，Mean Pooling 最通用
- Sentence-BERT = 专为句子相似度优化的模型，比原生 BERT 效果好一大截
- FAISS = 向量搜索引擎，像给向量建了索引，毫秒级查最相似的

---

## 3 项目实战

### 3.1 环境准备

```bash
pip install sentence-transformers>=2.7.0
pip install faiss-cpu>=1.7.4  # GPU 版用 faiss-gpu
pip install numpy>=1.24.0
```

### 3.2 向量编码与语义搜索

```python
# embedding_search.py
"""基于 Embedding 的语义搜索系统"""

import time
import numpy as np
from sentence_transformers import SentenceTransformer
from typing import List, Tuple, Dict


class SemanticSearchEngine:
    """语义搜索引擎"""

    def __init__(self, model_name: str = "paraphrase-multilingual-MiniLM-L12-v2"):
        print(f"加载模型: {model_name}")
        self.model = SentenceTransformer(model_name)
        self.documents: List[str] = []
        self.embeddings: np.ndarray = None
        self._dim = None

    def index(self, documents: List[str], batch_size: int = 64,
              show_progress: bool = True):
        """
        构建向量索引

        Args:
            documents: 文档列表
            batch_size: 编码批大小
            show_progress: 是否显示进度条
        """
        self.documents = documents
        print(f"正在编码 {len(documents)} 篇文档...")
        start = time.time()

        self.embeddings = self.model.encode(
            documents,
            batch_size=batch_size,
            show_progress_bar=show_progress,
            normalize_embeddings=True,  # L2 归一化，使内积等于余弦相似度
        )
        self._dim = self.embeddings.shape[1]

        elapsed = time.time() - start
        print(f"编码完成，耗时 {elapsed:.1f} 秒")
        print(f"向量维度: {self._dim}, 矩阵大小: {self.embeddings.shape}")

    def search(self, query: str, top_k: int = 5,
               min_score: float = 0.0) -> List[Dict]:
        """
        语义搜索

        Args:
            query: 查询文本
            top_k: 返回 Top K 个结果
            min_score: 最低相似度阈值

        Returns:
            [{"text": ..., "score": ..., "rank": ...}, ...]
        """
        if self.embeddings is None:
            raise RuntimeError("请先调用 index() 构建索引")

        # 编码 query
        query_vec = self.model.encode(
            [query], normalize_embeddings=True
        )[0]

        # 计算余弦相似度（已归一化，内积=余弦）
        scores = np.dot(self.embeddings, query_vec)

        # 排序取 Top-K
        top_indices = np.argsort(scores)[::-1][:top_k]

        results = []
        for rank, idx in enumerate(top_indices):
            score = float(scores[idx])
            if score < min_score:
                continue
            results.append({
                "text": self.documents[idx],
                "score": round(score, 4),
                "rank": rank + 1,
                "index": int(idx),
            })

        return results


# ===== 使用示例 =====
if __name__ == "__main__":
    # 模拟历史问答库
    faq_database = [
        "如何进行退款操作？在订单详情的页面内点击申请退款按钮，然后就去填写原因后提交。",
        "退款需要多长时间？支付宝和微信3个工作日内到账，银行卡7个工作日到账。",
        "怎么修改收货地址？在订单详情点击修改地址，输入新地址后确认保存即可。",
        "订单发货后还能改地址吗？如果已发货需联系客服人工修改，可能产生额外运费。",
        "如何注销账户？在设置-账户安全-注销账户中提交申请，提交后30天不可撤销。",
        "注册账户需要什么信息？手机号或邮箱，设置密码后即可完成注册。",
        "密码忘记了怎么办？在登录页面点击忘记密码，通过手机号或邮箱重置密码。",
        "怎么联系人工客服？在APP内点击我的-联系客服，或拨打400-123-4567。",
    ]

    engine = SemanticSearchEngine()
    engine.index(faq_database)

    # 测试语义搜索
    test_queries = [
        "退钱的流程是什么？",
        "我想换个收货地址",
        "账号怎么删除",
        "客服电话是多少",
    ]

    for query in test_queries:
        print(f"\n{'='*60}")
        print(f"🔍 查询: {query}")
        print(f"{'='*60}")

        results = engine.search(query, top_k=3)
        for r in results:
            bar = "█" * int(r["score"] * 20)
            print(f"  {r['rank']}. [{r['score']:.3f}] {r['text'][:70]}...")
            print(f"     {bar}")
```

输出示例：

```
============================================================
🔍 查询: 退钱的流程是什么？
============================================================
  1. [0.872] 如何进行退款操作？在订单详情的页面内点击申请退款按钮...
     █████████████████
  2. [0.631] 退款需要多长时间？支付宝和微信3个工作日内到账...
     ████████████
  3. [0.215] 怎么联系人工客服？在APP内点击我的-联系客服...
     ████

============================================================
🔍 查询: 账号怎么删除
============================================================
  1. [0.891] 如何注销账户？在设置-账户安全-注销账户中提交申请...
     █████████████████
  2. [0.452] 注册账户需要什么信息？手机号或邮箱，设置密码后即可...
     █████████
  3. [0.189] 密码忘记了怎么办？在登录页面点击忘记密码...
     ███
```

注意查询"退钱的流程"精准命中了"如何进行退款操作"（语义相似度 0.872），而关键词搜索会因"退钱"≠"退款"而漏掉。

### 3.3 FAISS 加速大规模检索

```python
# faiss_index.py
"""FAISS 向量索引 —— 百万级文档毫秒检索"""

import time
import numpy as np
import faiss
from sentence_transformers import SentenceTransformer


class FAISSSearchEngine:
    """基于 FAISS 的语义搜索引擎"""

    def __init__(self, model_name: str = "paraphrase-multilingual-MiniLM-L12-v2"):
        self.model = SentenceTransformer(model_name)
        self.documents = []
        self.index = None
        self._dim = None

    def build_index(self, documents: list, index_type: str = "flat"):
        """构建 FAISS 向量索引"""
        self.documents = documents

        print(f"编码 {len(documents)} 篇文档...")
        start = time.time()
        embeddings = self.model.encode(
            documents, normalize_embeddings=True, show_progress_bar=True
        )
        self._dim = embeddings.shape[1]
        print(f"编码耗时: {time.time() - start:.1f}s")

        # 构建 FAISS 索引
        start = time.time()
        if index_type == "flat":
            # 精确内积搜索（归一化后=余弦相似度）
            self.index = faiss.IndexFlatIP(self._dim)
        elif index_type == "ivf":
            # IVF 倒排索引（大数据量下更快）
            quantizer = faiss.IndexFlatIP(self._dim)
            nlist = min(int(np.sqrt(len(documents))), 4096)
            self.index = faiss.IndexIVFFlat(quantizer, self._dim, nlist)
            # IVF 需要先训练
            self.index.train(embeddings.astype(np.float32))
        else:
            raise ValueError(f"不支持的索引类型: {index_type}")

        self.index.add(embeddings.astype(np.float32))
        print(f"索引构建耗时: {time.time() - start:.1f}s")
        print(f"索引类型: {index_type}, 文档数: {self.index.ntotal}")

    def search(self, query: str, top_k: int = 5) -> list:
        """FAISS 语义搜索"""
        query_vec = self.model.encode(
            [query], normalize_embeddings=True
        ).astype(np.float32)

        scores, indices = self.index.search(query_vec, top_k)

        results = []
        for score, idx in zip(scores[0], indices[0]):
            if idx < 0 or idx >= len(self.documents):
                continue
            results.append({
                "text": self.documents[idx],
                "score": round(float(score), 4),
                "index": int(idx),
            })
        return results

    def benchmark(self, queries: list, top_k: int = 5):
        """性能基准测试"""
        total_time = 0
        for q in queries:
            start = time.time()
            self.search(q, top_k)
            total_time += time.time() - start

        avg_latency = total_time / len(queries) * 1000
        qps = len(queries) / total_time
        print(f"\n性能基准:")
        print(f"  查询数: {len(queries)}")
        print(f"  平均延迟: {avg_latency:.1f} ms")
        print(f"  QPS: {qps:.1f}")
        return avg_latency


# ===== 使用示例 =====
if __name__ == "__main__":
    # 生成模拟大数据（1万条）
    import random
    templates = [
        "如何{verb}{noun}？在{location}点击{button}按钮后操作。",
        "关于{noun}的常见问题：请查看帮助中心的{topic}章节。",
        "{noun}的处理流程：先{action1}，再{action2}，最后{action3}。",
        "如果遇到{problem}，请拨打客服电话{phone}。",
        "{product}的{feature}功能介绍：支持{mode1}和{mode2}两种模式。",
    ]
    verbs = ["申请", "查询", "修改", "取消", "设置"]
    nouns = ["退款", "订单", "地址", "密码", "会员"]
    docs = []
    for i in range(10000):
        t = random.choice(templates).format(
            verb=random.choice(verbs),
            noun=random.choice(nouns),
            location=random.choice(["设置页", "订单详情", "我的页面", "首页"]),
            button=random.choice(["提交", "确认", "保存", "下一步"]),
            topic=random.choice(["账户管理", "支付问题", "物流追踪", "售后处理"]),
            action1=random.choice(["填写表单", "上传凭证", "联系客服"]),
            action2=random.choice(["等待审核", "确认信息", "支付费用"]),
            action3=random.choice(["完成处理", "查收结果", "获取反馈"]),
            problem=random.choice(["登录失败", "支付异常", "闪退", "卡顿"]),
            phone="400-" + "".join(random.choices("0123456789", k=8)),
            product=random.choice(["旗舰版", "专业版", "免费版", "企业版"]),
            feature=random.choice(["数据分析", "客户管理", "自动回复", "批量导入"]),
            mode1=random.choice(["在线", "离线", "自动", "手动"]),
            mode2=random.choice(["同步", "异步", "定时", "实时"]),
        )
        docs.append(t)

    engine = FAISSSearchEngine()
    engine.build_index(docs, index_type="flat")

    test_qs = ["怎么退款？", "密码忘了", "客服电话"]
    engine.benchmark(test_qs)

    for q in test_qs[:3]:
        print(f"\n🔍 {q}")
        for r in engine.search(q, top_k=2):
            print(f"  [{r['score']:.3f}] {r['text'][:80]}")
```

### 3.4 Pooling 策略对比

```python
# pooling_compare.py
"""对比不同 Pooling 策略的句子向量质量"""

import torch
from transformers import AutoTokenizer, AutoModel
import numpy as np


def mean_pooling(model_output, attention_mask):
    """Mean Pooling —— 只对有效 token 取平均（推荐）"""
    token_embeddings = model_output.last_hidden_state
    input_mask_expanded = (
        attention_mask.unsqueeze(-1).expand(token_embeddings.size()).float()
    )
    sum_embeddings = torch.sum(token_embeddings * input_mask_expanded, dim=1)
    sum_mask = torch.clamp(input_mask_expanded.sum(dim=1), min=1e-9)
    return sum_embeddings / sum_mask


def cls_pooling(model_output):
    """CLS Pooling —— 只用 [CLS] token 的向量"""
    return model_output.last_hidden_state[:, 0, :]


def max_pooling(model_output, attention_mask):
    """Max Pooling —— 每维取最大值"""
    token_embeddings = model_output.last_hidden_state
    input_mask_expanded = (
        attention_mask.unsqueeze(-1).expand(token_embeddings.size()).float()
    )
    token_embeddings[input_mask_expanded == 0] = -1e9  # 屏蔽 PAD
    return torch.max(token_embeddings, dim=1)[0]


def cosine_similarity(a, b):
    a_np = a.detach().numpy() if isinstance(a, torch.Tensor) else a
    b_np = b.detach().numpy() if isinstance(b, torch.Tensor) else b
    return np.dot(a_np, b_np) / (np.linalg.norm(a_np) * np.linalg.norm(b_np))


if __name__ == "__main__":
    model_name = "bert-base-chinese"
    tokenizer = AutoTokenizer.from_pretrained(model_name)
    model = AutoModel.from_pretrained(model_name)

    # 相似句子对
    pairs = [
        ("退款流程是什么？", "如何申请退款？"),
        ("退款流程是什么？", "快递多久能到？"),
    ]

    def encode(texts, pooling_fn):
        inputs = tokenizer(texts, padding=True, truncation=True, return_tensors="pt")
        with torch.no_grad():
            outputs = model(**inputs)
        return pooling_fn(outputs, inputs["attention_mask"])

    print("Pooling 策略对比:")
    print(f"{'策略':<15} {'相似句对':<12} {'不相似句对':<12}")
    print("-" * 40)

    for name, fn in [("Mean", mean_pooling), ("CLS", cls_pooling), ("Max", max_pooling)]:
        sim_a = encode([pairs[0][0], pairs[0][1]], fn)
        sim_b = encode([pairs[1][0], pairs[1][1]], fn)

        sim_score = cosine_similarity(sim_a[0], sim_a[1])
        dissim_score = cosine_similarity(sim_b[0], sim_b[1])

        print(f"{name:<15} {sim_score:.4f}        {dissim_score:.4f}")

    print("\n评估标准: 相似句对分数应高, 不相似句对分数应低, 两者差值越大越好")
```

预期输出：

```
Pooling 策略对比:
策略             相似句对       不相似句对
----------------------------------------
Mean            0.8217        0.5473
CLS             0.6312        0.5891
Max             0.7734        0.6120

评估标准: 相似句对分数应高, 不相似句对分数应低, 两者差值越大越好
```

### 3.5 测试验证

```python
# test_embedding.py
import pytest
import numpy as np
from embedding_search import SemanticSearchEngine

@pytest.fixture
def engine():
    e = SemanticSearchEngine()
    e.index([
        "如何申请退款",
        "退款需要多久到账",
        "快递什么时候发货",
        "怎样修改收货地址",
    ])
    return e

class TestSemanticSearch:
    def test_search_returns_results(self, engine):
        results = engine.search("退钱流程", top_k=2)
        assert len(results) == 2
        assert results[0]["rank"] == 1

    def test_semantic_matching(self, engine):
        results = engine.search("怎么退钱？", top_k=1)
        assert "退款" in results[0]["text"]

    def test_score_range(self, engine):
        results = engine.search("怎么退钱？", top_k=1)
        assert 0 <= results[0]["score"] <= 1

    def test_min_score_filter(self, engine):
        results = engine.search("怎么退钱？", top_k=5, min_score=0.9)
        for r in results:
            assert r["score"] >= 0.9
```

---

## 4 项目总结

### 4.1 优点与缺点

| 方面 | 优点 | 缺点 |
|------|------|------|
| **语义搜索** | 不受字面差异影响，"退钱"能匹配"退款" | 存在"语义漂移"——有时高分结果与 query 并不真正相关 |
| **Sentence-BERT** | 开箱即用，mean pooling 效果好，中文多语言模型可用 | 领域专有术语（如医药、法律）需要额外微调 |
| **FAISS IndexFlatIP** | 精确搜索，毫秒级延迟 | 数据量 > 100 万时索引内存占用大 |
| **向量缓存** | 文档不变时不需重复编码 | 文档更新需重建索引 |

### 4.2 适用场景

| 场景 | 推荐方案 |
|------|---------|
| 客服历史问答匹配 | Sentence-BERT + FAISS Flat |
| 大规模文档检索 (>100万) | Sentence-BERT + FAISS IVF/HNSW |
| 知识库 RAG 召回（见第21章） | Embedding 粗排 + Cross-Encoder 精排 |
| 图片/商品相似搜索 | CLIP 等多模态 Embedding 模型 |

**不适用场景**：
- 需要精确关键词匹配的场景（如搜索订单号 XH20240501），Embedding 无法区分不同订单号
- Embedding 维度灾难（百万级以上需配合量化压缩，精度会下降）

### 4.3 注意事项

1. **向量归一化**：`normalize_embeddings=True` 后内积等于余弦相似度，可直接用 FAISS 的 `IndexFlatIP` 替代 `IndexFlatL2`
2. **batch_size 与显存**：10 万条文档一次加载编码可能 OOM，建议 `batch_size=32~64` 分批编码
3. **模型与任务匹配**：`paraphrase-multilingual-MiniLM-L12-v2` 适合语义相似度，如果需要非对称检索（短 query 搜长文档），考虑用专门的非对称模型

### 4.4 常见踩坑经验

| 故障现象 | 根因 | 解决方案 |
|---------|------|---------|
| 搜索结果全不相关 | 没做 `normalize_embeddings`，用了 L2 距离而非余弦 | 设置 `normalize_embeddings=True` 或显式计算余弦 |
| FAISS crash / segfault | numpy 数组类型不是 float32 | 编码结果 `.astype(np.float32)` 后再传入 FAISS |
| 检索越来越慢 | 文档持续增加未重建索引 | 定时重建索引；大批量场景用 IVF 索引增量添加 |

### 4.5 思考题

1. **初级**：将 `SemanticSearchEngine` 中的模型换为 `distiluse-base-multilingual-cased-v2`，对比两个模型的检索速度和准确率有何差异？
2. **进阶**：历史问答库中的问答对包含 question 和 answer 两部分。搜索时应该对 question 做 Embedding 还是对 answer 做 Embedding？还是两者都要？请设计一个**混合检索**方案。

（答案将在第10章末尾给出）

### 4.6 第8章思考题答案

**第8章思考题1**：
- `max_new_tokens=200` 时贪心搜索确实更容易重复。原因：贪心搜索每次都选概率最高的 token，一旦模型进入循环（如生成"很好"后最高概率的下一个词又是"很好"），它会永远重复。token 数越多，进入循环的概率越大。解决方案：`repetition_penalty=1.2` + `no_repeat_ngram_size=3`。

**第8章思考题2**：
- 实现思路：自定义 `StoppingCriteria` 子类，在 `__call__` 中检查 `input_ids` 的最后几个 token 是否解码后包含"。"，若包含则返回 True 停止生成。示例：`class PeriodStoppingCriteria(StoppingCriteria): def __call__(self, input_ids, scores): return "。" in tokenizer.decode(input_ids[0, -5:])`。

### 4.7 推广计划提示

| 部门 | 建议行动 |
|------|---------|
| **开发团队** | 将 `SemanticSearchEngine` 封装为微服务，提供 `/index`（构建索引）和 `/search`（查询）两个接口 |
| **测试团队** | 构建 500 条人工标注的问答对评估集，计算 Recall@5 和 MRR（Mean Reciprocal Rank） |
| **运维团队** | 文档库更新后需重建索引，建议每天凌晨低峰期执行，并在重建期间保持旧索引可服务 |

---

> **下一章预告**：第10章将进入数据处理基础——用 Datasets 库加载、清洗、切分各种格式的数据（CSV/JSON/Excel），为正式的模型训练做好数据准备。
