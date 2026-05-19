# 第29章：RAG 生产化治理与质量评估

> **定位**：让知识库从 Demo 变成可运营系统。
> **版本**：Milvus 2.5.x
> **源码关联**：internal/proxy/hybrid_search.go、pymilvus/orm/collection.py

---

## 1. 项目背景

某企业内部知识库 RAG 系统上线 3 个月后，运营团队反馈了两个尖锐的问题：

1. **"搜不到"的投诉越来越多**：HR 部门搜"产假政策"返回的是 2022 年的旧版制度，而 2024 年的新版明明已经导入了系统。排查发现：新版文档的 Chunk 嵌入向量和旧版几乎一样（因为改动的只是日期和部分措辞），Milvus 把两个版本都排在 TopK 里，但旧版在更高位。
2. **"搜错了"**：财务部搜"差旅费报销标准"的时候，Top1 是市场部的"差旅费申请模板"——两个文档都和"差旅费"语义相关，但性质完全不同。

算法团队意识到：RAG 系统上线不等于完事。它需要持续的质量监控（哪些 Query 召回不准）、迭代优化（调整 Chunk 策略/重排序模型/查询改写）和运营管理（文档版本管理、权限过滤）。

本章将建立 RAG 系统的评估体系（Recall@K、MRR、NDCG）、查询改写策略和线上反馈闭环。

---

## 2. 项目设计（剧本式交锋对话）

**第一幕：RAG 质量评估——Recall@K、MRR、NDCG 三剑客**

*（算法小王拿着 50 条标注的查询-答案对，不知道用什么指标汇报）*

**小胖**（看着三个公式发呆）："Recall@K、MRR、NDCG——这三个到底有什么区别？我该汇报哪个？"

**大师**："三个指标看的是'质量'的三个不同侧面——"

**大师**（在白板上写出公式和案例）：

```
案例: 用户问"产假政策"，知识库中正确答案是 doc_A
     系统返回 Top5: [doc_B(0.92), doc_A(0.88), doc_C(0.75), doc_D(0.60), doc_E(0.55)]
     正确答案 doc_A 在第 2 位

指标 1: Recall@K — "正确答案在不在 TopK 里？"
─────────────────────────────────────────────
  Recall@5 = 1 (doc_A 在 Top5 里) ✓
  Recall@3 = 1 (doc_A 在 Top3 里) ✓
  Recall@1 = 0 (doc_A 不在 Top1 里) ✗
  
  特点: 只关心"有没有"，不关心"排第几"
  适用: 评估召回阶段的覆盖能力

指标 2: MRR (Mean Reciprocal Rank) — "正确答案排在第几位？"
─────────────────────────────────────────────
  RR = 1 / rank = 1/2 = 0.5
  如果正确答案在第 1 位: RR = 1/1 = 1.0
  如果正确答案在第 10 位: RR = 1/10 = 0.1
  
  特点: 关心"排名"，排名越靠前分越高
  适用: 评估排序质量

指标 3: NDCG@K (Normalized Discounted Cumulative Gain) — "排序质量有多好？"
─────────────────────────────────────────────
  不仅关心正确答案是否在前面，还区分"部分相关"和"完全相关"
  
  相关性打分: doc_A=3(完美相关), doc_B=1(部分相关), doc_C/D/E=0(不相关)
  DCG = 3/log2(2) + 1/log2(3) + 0 + 0 + 0 = 3.0 + 0.63 = 3.63
  理想DCG (doc_A 在第一位) = 3/log2(2) + 1/log2(3) = 3.63
  NDCG = DCG/IDCG ≈ 1.0
  
  特点: 最精细（区分相关性等级）
  适用: 高级评估场景
```

**大师**："推荐选择——"

| 阶段 | 推荐指标 | 原因 |
|------|---------|------|
| 召回阶段评估 | Recall@K (K=10) | 只关心找没找到，不关心排序 |
| 排序/重排评估 | MRR | 关心正确答案在第几位 |
| 精细化评估 | NDCG@10 | 需要人工相关性标注（3级制） |
| 日常监控 | Recall@10 + MRR | 两者结合，简单有效 |

> **技术映射**：Recall@K = 寻宝游戏（找到金子就得分）；MRR = 翻牌游戏（金子在第几张牌后面）；NDCG = 鉴宝评级（金子的成色也要纳入评分）。

---

**第二幕：Query 改写与检索增强——怎么让搜索更聪明**

**小白**："用户搜'产假怎么请'为什么搜不到'员工休假管理办法'？明明内容是对的但标题不对——"

**大师**："这就是'查询和文档词汇不匹配'的经典问题。解决方案是 Query Rewriting（查询改写）。"

**大师**（画出改写策略）：

```
Query 改写策略:

原始 Query: "产假怎么请"
     │
     ├── 策略1: 同义词扩展
     │   "产假" → "产假 OR 生育假 OR 分娩假"
     │   ↓
     │   新 Query: "产假 生育假 分娩假 怎么请"
     │
     ├── 策略2: HyDE (Hypothetical Document Embedding)
     │   先让 LLM 生成一段假设的答案:
     │   "员工需提前10个工作日提出产假申请，附医院证明..."
     │   ↓ 对这段假设答案做 Embedding
     │   用这个 Embedding 去搜（比原 Query 的语义更丰富）
     │
     ├── 策略3: Query Decomposition (查询分解)
     │   "产假怎么请" → LLM 拆解为:
     │   ① "产假天数是多少"
     │   ② "产假申请流程"
     │   ③ "产假需要什么证明材料"
     │   ↓ 多 Query 并行搜索
     │   ↓ 结果合并去重
     │
     └── 策略4: Multi-Generation
         同上，但每个子 Query 独立走 RAG 流水线，最后汇总答案
```

**大师**："策略推荐——"

| 策略 | 延迟 | 效果 | 推荐 |
|------|------|------|------|
| 同义词扩展 | 低 (+10ms) | 中 | 快速上线，适合词表固定的领域 |
| HyDE | 高 (+2-5s, 需LLM) | 高 | 效果最好但延迟大 |
| Query 分解 | 高 (+2-5s) | 高 | 适合多角度复杂问题 |

> **技术映射**：同义词扩展 = 多带几个别名去查户籍（"张三"查不到就查"张老三"）；HyDE = 凭记忆先画张草图再对着找（更容易匹配）；Query 分解 = 把大问题拆成小问题分头查。

---

**第三幕：线上反馈闭环——如何持续改进**

**小胖**："系统上线了，怎么知道它好不好？总不能靠用户投诉才知道吧？"

**大师**："建立 RAG 的反馈闭环——"

```
RAG 质量反馈闭环:

     ┌──────────────────────────────────┐
     │   用户搜索                         │
     │   ↓                               │
     │   系统返回结果                      │
     │   ↓                               │
     │   用户行为反馈                      │
     │   ├─ 正面: 点击/复制/分享/好评      │
     │   └─ 负面: 快速离开/点踩/重新搜索   │
     │   ↓                               │
     │   自动标注 + 人工抽检                │
     │   ├─ 差评样本 → 人工评审 → 标注集   │
     │   └─ 好评样本 → 验证召回稳定性      │
     │   ↓                               │
     │   优化迭代                          │
     │   ├─ 调整 Chunk 策略                │
     │   ├─ 优化 Rerank 模型               │
     │   ├─ 补充缺失文档                   │
     │   └─ 重建索引                       │
     │   ↓                               │
     │   上线 → 回到用户搜索               │
     └──────────────────────────────────┘
```

**大师**："闭环的核心是——不依赖人工投诉，而是自动捕获'差评信号'。"

| 信号 | 含义 | 如何处理 |
|------|------|---------|
| 用户 2 秒内关闭结果 | 结果完全不对 | 自动标记为差评样本 |
| 用户复制了内容 | 结果有用 | 标记为好评样本 |
| 用户重新搜索了（换关键词） | 第一次的结果不满意 | 对比两次搜索结果的差异 |
| 用户点踩按钮 | 明确不满意 | 最高优先级人工评审 |

---

## 3. 项目实战

### 3.1 实战目标

为企业知识库建立一套检索质量评估集，并通过混合检索和重排提升命中率。

### 3.2 分步实现

#### 步骤 1：构建标注评估集

```python
# step1_eval_dataset.py
"""构建 RAG 评估标注集"""
import json

# 标注格式: (query, relevant_doc_ids, relevance_scores)
eval_set = [
    {
        "query": "产假怎么请？需要什么材料？",
        "relevant_docs": [
            {"doc_id": "HR-2024-001", "score": 3},  # 3=完全相关
            {"doc_id": "HR-2024-003", "score": 2},  # 2=部分相关
        ],
    },
    {
        "query": "出差住宿标准是多少？",
        "relevant_docs": [
            {"doc_id": "FIN-2024-010", "score": 3},
        ],
    },
    {
        "query": "加班调休规则",
        "relevant_docs": [
            {"doc_id": "HR-2024-002", "score": 3},
            {"doc_id": "HR-2024-004", "score": 1},  # 1=弱相关
        ],
    },
]

with open("rag_eval_set.json", "w", encoding="utf-8") as f:
    json.dump(eval_set, f, ensure_ascii=False, indent=2)

print(f"评估集: {len(eval_set)} 条标注数据")
```

#### 步骤 2：RAG 质量评估器

```python
# step2_rag_evaluator.py
"""RAG 质量评估：Recall@K, MRR, NDCG"""
import math
from collections import defaultdict

class RAGEvaluator:
    """RAG 质量评估器"""
    
    def __init__(self, eval_set: list):
        self.eval_set = eval_set
    
    def evaluate(self, search_fn) -> dict:
        """评估搜索函数的质量
        
        Args:
            search_fn: 搜索函数 (query) -> [(doc_id, score), ...]
        """
        recalls_at_k = defaultdict(list)
        mrrs = []
        ndcgs_at_k = defaultdict(list)
        
        for item in self.eval_set:
            query = item["query"]
            relevant = {d["doc_id"]: d["score"] for d in item["relevant_docs"]}
            results = search_fn(query)  # [(doc_id, score), ...]
            result_ids = [r[0] for r in results]
            
            # Recall@K
            for k in [1, 3, 5, 10]:
                top_k_ids = result_ids[:k]
                hits = sum(1 for rid in top_k_ids if rid in relevant)
                recalls_at_k[k].append(hits / len(relevant))
            
            # MRR
            for rank, rid in enumerate(result_ids, 1):
                if rid in relevant:
                    mrrs.append(1.0 / rank)
                    break
            else:
                mrrs.append(0.0)
            
            # NDCG@10
            dcg = 0
            for rank, rid in enumerate(result_ids[:10], 1):
                rel = relevant.get(rid, 0)
                dcg += rel / math.log2(rank + 1)
            idcg = sum(
                sorted(relevant.values(), reverse=True)[i] / math.log2(i + 2)
                for i in range(min(10, len(relevant)))
            )
            ndcgs_at_k[10].append(dcg / idcg if idcg > 0 else 0)
        
        metrics = {}
        for k in [1, 3, 5, 10]:
            if recalls_at_k[k]:
                metrics[f"Recall@{k}"] = round(
                    sum(recalls_at_k[k]) / len(recalls_at_k[k]), 4
                )
        metrics["MRR"] = round(sum(mrrs) / len(mrrs), 4)
        metrics["NDCG@10"] = round(
            sum(ndcgs_at_k[10]) / len(ndcgs_at_k[10]), 4
        )
        return metrics

# 模拟搜索函数
def mock_search(query):
    """模拟搜索：返回 (doc_id, score) 列表"""
    # 实际使用时替换为真实的 Milvus 搜索
    return [("HR-2024-001", 0.93), ("FIN-2024-010", 0.85),
            ("HR-2024-003", 0.72), ("doc_999", 0.65)]

evaluator = RAGEvaluator(eval_set)
metrics = evaluator.evaluate(mock_search)
print("RAG 质量评估结果:")
for k, v in metrics.items():
    print(f"  {k:<15}: {v}")
```

#### 步骤 3：差评样本自动收集

```python
# step3_feedback_collector.py
"""用户反馈自动收集器"""
import json
import time
from collections import deque

class FeedbackCollector:
    """搜索反馈收集器"""
    
    def __init__(self, max_samples=1000):
        self.negative_samples = deque(maxlen=max_samples)  # 差评
        self.positive_samples = deque(maxlen=max_samples)  # 好评
    
    def record(self, query: str, results: list,
               user_action: str, dwell_time_ms: int):
        """记录一次搜索反馈
        
        Args:
            query: 搜索词
            results: 返回结果列表
            user_action: click/copy/back/refine/none
            dwell_time_ms: 停留时间（毫秒）
        """
        is_negative = False
        reason = ""
        
        if user_action == "back" and dwell_time_ms < 2000:
            is_negative = True
            reason = "快速离开"
        elif user_action == "refine":
            is_negative = True
            reason = "重新搜索"
        elif user_action == "copy":
            is_negative = False  # 好评
            reason = "复制内容"
        
        sample = {
            "query": query,
            "top_result_id": results[0]["id"] if results else None,
            "user_action": user_action,
            "dwell_time_ms": dwell_time_ms,
            "reason": reason,
            "timestamp": time.time(),
        }
        
        if is_negative:
            self.negative_samples.append(sample)
            if len(self.negative_samples) % 100 == 0:
                print(f"  ⚠ 差评累计: {len(self.negative_samples)} 条")
        else:
            self.positive_samples.append(sample)
    
    def export_negative_samples(self, filepath: str):
        """导出差评样本供人工评审"""
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(list(self.negative_samples), f,
                      ensure_ascii=False, indent=2)
        print(f"差评样本已导出: {filepath} ({len(self.negative_samples)} 条)")

# 使用示例
collector = FeedbackCollector()
collector.record("产假怎么请", [{"id": "doc_001"}], "back", 1200)
collector.record("差旅标准", [{"id": "doc_010"}], "copy", 5000)
collector.export_negative_samples("negative_samples.json")
```

---

## 4. 项目总结

### 4.1 RAG 质量评估指标速查

| 指标 | 公式 | 解读 | 使用场景 |
|------|------|------|---------|
| Recall@K | 正确文档出现在 TopK 中的比例 | 越高越好，≥0.95 为优秀 | 召回阶段评估 |
| MRR | 1 / 第一个正确答案的排名 | 越高越好，≥0.8 为优秀 | 排序质量 |
| NDCG@K | 考虑相关性等级的排序质量 | 越高越好 | 精细化评估 |

### 4.2 治理迭代节奏

- **每周**：自动收集差评样本，导出 Top10 差评 Query 做人工评审
- **每月**：更新评估标注集，重新评估 Recall@10 和 MRR
- **每季度**：全面评估是否需要调整 Chunk 策略、Embedding 模型或混合检索权重

### 4.3 注意事项

- **评估集必须持续更新**：业务变化后，旧的评估集可能不再代表真实需求。
- **不要只看一个指标**：Recall 高但 MRR 低 = 找得到但拍得靠后（排序有问题）。
- **自动反馈可能有噪音**：用户快速离开不一定是因为结果差，可能只是看了标题就知道答案。

### 4.4 思考题

1. 如果 Recall@10 = 0.98 但用户投诉率仍然很高，可能是什么问题？如何进一步排查？
2. HyDE 策略中 LLM 生成的"假设答案"如果本身有偏见或幻觉，会对召回产生什么影响？如何防范？

---

> **下一章预告**：第30章是中级篇综合实战——构建生产级 RAG 检索平台。读完本章，你应该能建立 RAG 系统的持续质量评估和迭代优化体系。
