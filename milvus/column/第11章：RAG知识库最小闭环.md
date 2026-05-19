# 第11章：RAG 知识库最小闭环

> **定位**：用 Milvus 完成第一个 AI 应用闭环。
> **版本**：Milvus 2.5.x
> **源码关联**：internal/proxy/search.go、pymilvus/orm/collection.py

---

## 1. 项目背景

某互联网公司内部有超过 2000 份制度文档（HR 政策、报销流程、技术规范、安全红线等），分散在 Confluence、企业微信文档、GitLab Wiki 等多个平台。员工查找信息的方式是全局搜索关键词——但搜索结果经常不准确。HR 总监吐槽："搜'婚假怎么请'第一条结果是《服务器机房管理办法》，这合理吗？"

CTO 决定启动"企业智能问答机器人"项目，用 RAG（Retrieval-Augmented Generation）架构解决这个问题。算法实习生小林被分配负责召回层——用 Milvus 做向量检索。

小林第一版实现的天真程度让导师王工扶额：

1. **整篇文档直接 Embedding**——把一篇 2 万字的《员工手册》直接生成一个 384 维向量，结果搜"加班调休规则"时相似度极低（因为语义被全文平均掉了）。
2. **Chunk 大小拍脑袋设 500 字**——结果"请婚假需提前 5 个工作日向部门负责人提交《婚假审批表》"被切成了两个 Chunk，前半句在 Chunk_A 里、后半句在 Chunk_B 里，搜索时只能命中一半。
3. **过滤掉低相似度但实际正确的结果**——搜"年假计算方法"，正确文档的相似度只有 0.62（因为关键词密度低），但他设了 threshold=0.7，直接过滤掉了。
4. **拼接 Prompt 时 Chunk 顺序丢失**——RAG 的最后一环是把召回的 Chunk 拼到 Prompt 里，但他没有按原文顺序排序，导致 LLM 看到的是混乱的上下文。
5. **没有评估体系**——他不知道这个知识库到底好不好用，只知道"能返回结果了"。

本章将从文档解析到 LLM 生成，完成 RAG 的最小闭环实现。

---

## 2. 项目设计（剧本式交锋对话）

**第一幕：RAG 是什么——为什么需要它**

*（小林抱着一堆打印出来的技术文档来找王工）*

**小胖**（抱着一袋薯片凑过来）："RAG？这缩写一听就很唬人。不就是一个搜索+一个 ChatGPT 吗？搜出来给 AI 润色一下不就行了？"

**大师**："小胖你又来了。RAG 不是'搜+GPT'的简单拼接，而是一个有精密协作关系的流水线。我问你，如果只靠 GPT 回答问题，不给它任何参考资料，它能回答'公司年假怎么请'吗？"

**小胖**："当然不能啊，GPT 又不是我们公司的员工。"

**大师**："那如果把 2000 份公司制度文档全部塞进 GPT 的 context 呢？"

**小白**："也不行，GPT-4 的 context 最大 128K tokens，2000 份文档远超这个限制，而且成本极高。"

**大师**："对。RAG 的本质就是：**不把所有文档都给 LLM，而是先检索出最相关的几段，只把这几段喂给 LLM**。就像你去图书馆查资料——不是把整个图书馆搬回家，而是先查目录找到相关的几本书和几页。"

```
RAG 完整流水线:

┌──────────┐    ┌───────────┐    ┌──────────┐    ┌───────────┐    ┌──────────┐
│ 文档解析  │ →  │ 文本分块   │ →  │ Embedding │ →  │ Milvus    │ →  │ LLM 生成 │
│ PDF/MD/  │    │ Chunking  │    │ 向量化    │    │ 向量检索   │    │ 最终回答  │
│ HTML/Word│    │ (500字/块) │    │ (BGE-M3)  │    │ TopK 召回  │    │ (GPT-4)  │
└──────────┘    └───────────┘    └──────────┘    └───────────┘    └──────────┘
                                                      │
                                                  召回结果 + 原问题
                                                  ↓
                                          Prompt = 系统提示 + 参考文档 + 用户问题
```

**大师**："这个流水线里，Milvus 只负责'查到最相关的文档 Chunk'这一步。但这一步如果做不好——切块不对、Embedding 模型选错、TopK 太大太小、过滤不准确——LLM 拿到的是垃圾上下文，输出自然也是垃圾。"

> **技术映射**：RAG = 先查资料再答题（而不是闭卷默写）；Milvus 在 RAG 中的角色 = 图书馆检索系统（负责找到对的那几页）；LLM = 读者（看完资料后用自己的话回答）。

---

**第二幕：Chunk 大小、Overlap、Metadata 的设计**

**小白**："大师，切块到底怎么切？我见过有人用固定 512 字、有人用 256 字、还有人按段落切——到底哪种对？"

**大师**："切块没有银弹，但有一套决策框架——"

| 策略 | 优点 | 缺点 | 适用场景 |
|------|------|------|---------|
| **固定长度（256/512/1024 tokens）** | 简单、可控、可复现 | 可能在句子中间切断 | 大多数场景的默认选择 |
| **按段落切** | 保留语义完整性 | 段落长度不均匀，长段落 Embedding 质量差 | 结构化文档（制度/规范） |
| **按语义切（Sentence Splitting + 聚合）** | 每个 Chunk 语义完整 | 计算成本高、速度慢 | 高精度知识库 |
| **递归切（先大后小）** | 兼容不同长度文档 | 实现复杂 | 混合文档类型 |

**大师**："推荐新手用 **512 tokens + 128 tokens Overlap（重叠）** 的固定长度切法。理由——"

1. 512 tokens 是大多数 Embedding 模型的最佳输入长度。
2. 128 tokens Overlap 保证"请婚假需…"不会被切断——前后 Chunk 各包含这段文字。
3. 固定长度保证了写入 Milvus 的向量维度稳定、搜索的一致性。

**大师**："另外，每个 Chunk 必须带 Metadata——"

```python
# ❌ 错误做法：Chunk 不带 Metadata
chunks = ["文档内容段落1", "文档内容段落2", ...]

# ✓ 正确做法：每个 Chunk 带 Metadata
chunks = [
    {
        "text": "请婚假需提前5个工作日向部门负责人提交...",
        "metadata": {
            "doc_id": "HR-2024-001",       # 源文档 ID
            "doc_title": "员工手册-休假篇",  # 源文档标题
            "chunk_index": 42,              # Chunk 序号（用于还原顺序）
            "department": "全公司",          # 适用部门（用于权限过滤）
            "effective_date": "2024-01-01",  # 生效日期
        }
    },
    ...
]
```

**小白**："为什么要存 `chunk_index`？"

**大师**："因为在 RAG 的最后一步——拼 Prompt 的时候——你需要把召回的 Chunk 按原文顺序排列。LLM 看到一个有序的上下文，生成质量远高于看到乱序的碎片。"

> **技术映射**：Chunk = 把长文档切成一页一页的"资料卡片"；Overlap = 相邻卡片之间叠着的部分（防止信息断裂）；Metadata = 每张卡片的"标签"（来源、顺序、适用范围）。

---

**第三幕：相似度阈值、TopK 调参与幻觉治理**

**小胖**："小林说他把 threshold 设了 0.7，结果搜'年假'返回空。我就设成 0.5，结果搜出一堆不相关的东西——到底设多少？"

**大师**："相似度阈值是一个需要根据数据分布来定的参数，不是拍脑袋——"

```python
# 建议：上线前先用一批测试问题跑一遍，统计相似度分布
results = collection.search(data=test_queries, limit=50, ...)
all_distances = [hit.distance for batch in results for hit in batch]

# 统计分位数
import numpy as np
print(f"P25: {np.percentile(all_distances, 25):.3f}")
print(f"P50: {np.percentile(all_distances, 50):.3f}")
print(f"P75: {np.percentile(all_distances, 75):.3f}")
print(f"P90: {np.percentile(all_distances, 90):.3f}")
# 取 P25 作为阈值：保证 75% 的召回结果通过
```

**大师**："至于 TopK——"

| TopK 值 | 优点 | 缺点 | 推荐 |
|---------|------|------|------|
| K=3 | 上下文精炼、Token 成本低 | 可能漏掉关键信息 | 简单问答（如"婚假几天"） |
| K=5-10 | 覆盖面适中 | LLM 需要处理更多上下文 | 大多数场景的默认值 |
| K=20+ | 召回充分 | Token 成本高、可能引入噪声 | 复杂问题（如"对比三种报销方式"） |

**小白**："那幻觉呢？搜出来的东西明明是错的，但 LLM 还很自信地生成了答案——"

**大师**："RAG 幻觉有三个来源，对应三种治理手段——"

| 幻觉类型 | 根因 | 治理手段 |
|---------|------|---------|
| **漏召回** | 相关文档没被向量检索找到 | 增大 TopK、用 Hybrid Search、优化 Chunk 策略 |
| **误召回** | 召回了不相关的文档 | 设置合理相似度阈值、增加关键词过滤 |
| **LLM 胡编** | LLM 无视召回的文档自己编答案 | Prompt Engineering："如果参考文档中没有明确答案，请回答'未找到相关信息'" |

> **技术映射**：相似度阈值 = 及格线（低于这个分的答案直接不采纳）；TopK = 一次给 LLM 看几页资料；漏召回 = 图书管理员没找到对的书；误召回 = 找到了书名相似但内容无关的书。

---

## 3. 项目实战

### 3.1 实战目标

构建一个企业制度问答知识库，支持上传文档、向量化入库和基于 Milvus 的问答召回。

### 3.2 环境准备

```bash
pip install pymilvus==2.5.5 sentence-transformers langchain langchain-text-splitters
```

### 3.3 分步实现

#### 步骤 1：文档加载与分块

```python
# step1_chunking.py
"""文档加载 + 智能分块"""
from langchain_text_splitters import RecursiveCharacterTextSplitter

# 模拟公司制度文档
documents = [
    {
        "title": "员工手册-休假篇",
        "department": "全公司",
        "content": """
        第一条 年假：员工入职满1年后享有5天带薪年假，每增加1年工龄增加1天，上限15天。
        年假需提前5个工作日申请，经部门负责人审批后生效。年假可分次使用，每次不少于半天。
        
        第二条 婚假：员工结婚享有3天婚假，需提供结婚证复印件。婚假需提前10个工作日申请。
        晚婚（男25周岁、女23周岁以上）额外增加7天婚假。
        
        第三条 病假：员工因病需休假的，需提供二级以上医院开具的病假证明。病假3天以内由部门
        负责人审批，3天以上需分管领导审批。病假期间工资按基本工资的80%发放。
        """,
    },
    {
        "title": "报销管理制度",
        "department": "财务部",
        "content": """
        第一条 差旅费报销：出差人员应在出差结束后5个工作日内提交报销申请，附交通票据、
        住宿发票。住宿标准：一线城市不超过500元/天，其他城市不超过350元/天。
        餐补标准：100元/天，不需发票。
        
        第二条 办公用品采购：单价500元以下的办公用品由部门自行采购后报销；500-2000元
        需提前申请采购审批；2000元以上统一由行政部采购。
        
        第三条 培训费：经批准参加的外部培训，凭培训通知和发票全额报销。培训期间的交通
        和住宿按差旅费标准执行。
        """,
    },
]

# 分块器：512 字符/块，128 字符重叠
text_splitter = RecursiveCharacterTextSplitter(
    chunk_size=512,
    chunk_overlap=128,
    separators=["\n\n", "\n", "。", "；", "，", " ", ""],
    length_function=len,
)

chunks = []
for doc in documents:
    doc_chunks = text_splitter.split_text(doc["content"])
    for i, chunk_text in enumerate(doc_chunks):
        chunks.append({
            "text": chunk_text.strip(),
            "doc_id": doc["title"],
            "doc_title": doc["title"],
            "department": doc["department"],
            "chunk_index": i,
        })
    print(f"  {doc['title']}: {len(doc_chunks)} chunks")

print(f"\n总 Chunk 数: {len(chunks)}")
for i, c in enumerate(chunks[:3]):
    print(f"  Chunk {i}: {c['text'][:60]}... [{c['doc_title']}]")
```

#### 步骤 2：写入 Milvus + 构建索引

```python
# step2_rag_milvus.py
"""创建 RAG 知识库 Collection 并写入 Chunk 向量"""
from pymilvus import connections, Collection, utility
from pymilvus import CollectionSchema, FieldSchema, DataType
from sentence_transformers import SentenceTransformer

connections.connect(host="localhost", port="19530")

COLL_NAME = "rag_knowledge_base"

if utility.has_collection(COLL_NAME):
    utility.drop_collection(COLL_NAME)

fields = [
    FieldSchema(name="id", dtype=DataType.INT64, is_primary=True, auto_id=True),
    FieldSchema(name="chunk_text", dtype=DataType.VARCHAR, max_length=2048),
    FieldSchema(name="chunk_vec", dtype=DataType.FLOAT_VECTOR, dim=384),
    FieldSchema(name="doc_id", dtype=DataType.VARCHAR, max_length=256),
    FieldSchema(name="doc_title", dtype=DataType.VARCHAR, max_length=256),
    FieldSchema(name="department", dtype=DataType.VARCHAR, max_length=64),
    FieldSchema(name="chunk_index", dtype=DataType.INT64),
]

schema = CollectionSchema(fields, description="企业制度 RAG 知识库")
collection = Collection(COLL_NAME, schema)

# Embedding + 写入
model = SentenceTransformer("all-MiniLM-L6-v2")
texts = [c["text"] for c in chunks]
embeddings = model.encode(texts, batch_size=64).tolist()

collection.insert([
    texts,
    embeddings,
    [c["doc_id"] for c in chunks],
    [c["doc_title"] for c in chunks],
    [c["department"] for c in chunks],
    [c["chunk_index"] for c in chunks],
])

utility.flush([COLL_NAME])
print(f"已写入 {collection.num_entities} 条 Chunk")

# 索引
collection.create_index("chunk_vec", {
    "index_type": "HNSW", "metric_type": "COSINE",
    "params": {"M": 16, "efConstruction": 200}
})
utility.wait_for_index_building_complete(COLL_NAME, timeout=60)
collection.load()
print("索引构建完成并已 Load")
```

#### 步骤 3：RAG 问答完整流程

```python
# step3_rag_qa.py
"""RAG 问答完整流程：检索 + Prompt 组装 + LLM 生成（模拟）"""
from pymilvus import Collection, connections
from sentence_transformers import SentenceTransformer
import numpy as np

connections.connect(host="localhost", port="19530")
model = SentenceTransformer("all-MiniLM-L6-v2")
collection = Collection("rag_knowledge_base")

class RAGPipeline:
    """RAG 问答流水线"""
    
    def __init__(self, collection, embed_model):
        self.collection = collection
        self.model = embed_model
    
    def retrieve(self, query: str, top_k: int = 5,
                 department: str = None, threshold: float = 0.5):
        """向量召回：从 Milvus 中检索最相关的 Chunk"""
        query_vec = self.model.encode([query]).tolist()
        
        # 构造过滤表达式（可选）
        expr = f"department == '{department}'" if department else None
        
        results = self.collection.search(
            data=query_vec,
            anns_field="chunk_vec",
            param={"metric_type": "COSINE", "params": {"ef": 64}},
            expr=expr,
            limit=top_k,
            output_fields=["chunk_text", "doc_title", "department", "chunk_index"]
        )
        
        # 过滤 + 排序（按 doc_id 分组，组内按 chunk_index 排序）
        retrieved = []
        for hit in results[0]:
            if hit.distance >= threshold:
                retrieved.append({
                    "text": hit.entity.get("chunk_text"),
                    "doc_title": hit.entity.get("doc_title"),
                    "department": hit.entity.get("department"),
                    "chunk_index": hit.entity.get("chunk_index"),
                    "score": round(hit.distance, 4),
                })
        
        # 按文档分组，组内按 chunk_index 排序（保持上下文顺序）
        retrieved.sort(key=lambda x: (x["doc_title"], x["chunk_index"]))
        return retrieved
    
    def build_prompt(self, query: str, retrieved_chunks: list) -> str:
        """组装 Prompt"""
        if not retrieved_chunks:
            return f"问题：{query}\n\n（未找到相关公司制度，请如实告知用户。）"
        
        # 去重 + 拼接上下文
        seen = set()
        context_parts = []
        for chunk in retrieved_chunks:
            if chunk["text"] not in seen:
                seen.add(chunk["text"])
                context_parts.append(
                    f"【来源：{chunk['doc_title']}】{chunk['text']}"
                )
        
        context = "\n\n".join(context_parts)
        
        prompt = f"""你是一个企业知识库助手。请仅根据以下公司制度文档回答问题。
如果文档中没有明确答案，请回答"根据现有制度，暂时无法回答该问题"，不要编造内容。

## 参考文档
{context}

## 用户问题
{query}

## 回答
"""
        return prompt
    
    def answer(self, query: str, top_k: int = 5,
               department: str = None, threshold: float = 0.5):
        """完整问答流程"""
        chunks = self.retrieve(query, top_k, department, threshold)
        prompt = self.build_prompt(query, chunks)
        
        return {
            "query": query,
            "retrieved_count": len(chunks),
            "retrieved_chunks": chunks,
            "prompt": prompt,
            # 实际使用时这里调用 LLM API:
            # answer = llm.generate(prompt)
        }


# 使用示例
rag = RAGPipeline(collection, model)

print("=" * 60)
print("RAG 问答测试")
print("=" * 60)

test_queries = [
    "婚假怎么请？需要提前多久？",
    "出差住宿标准是多少？",
    "年假有多少天？怎么申请？",
    "今天食堂吃什么？",  # 知识库中没有
]

for q in test_queries:
    print(f"\n> 问题: {q}")
    result = rag.answer(q, top_k=5, threshold=0.5)
    print(f"  召回 {result['retrieved_count']} 个 Chunk:")
    for c in result["retrieved_chunks"]:
        print(f"    [{c['doc_title']}] {c['text'][:60]}... ({c['score']:.3f})")
    print(f"\n  Prompt 长度: {len(result['prompt'])} 字符")
```

#### 步骤 4：召回质量评估

```python
# step4_rag_eval.py
"""RAG 召回质量评估：Recall@K 计算"""
import json

# 人工标注的测试集（问题 -> 应召回的正确 doc_id 列表）
test_set = [
    {"query": "婚假怎么请", "relevant_docs": ["员工手册-休假篇"]},
    {"query": "出差报销标准", "relevant_docs": ["报销管理制度"]},
    {"query": "年假天数", "relevant_docs": ["员工手册-休假篇"]},
    {"query": "办公用品采购流程", "relevant_docs": ["报销管理制度"]},
]

def evaluate_recall(rag, test_set, k=5):
    """计算 Recall@K"""
    total_hits = 0
    total_relevant = 0
    
    for item in test_set:
        result = rag.answer(item["query"], top_k=k, threshold=0.3)
        retrieved_docs = set(c["doc_title"] for c in result["retrieved_chunks"])
        relevant_docs = set(item["relevant_docs"])
        
        hits = len(retrieved_docs & relevant_docs)
        total_hits += hits
        total_relevant += len(relevant_docs)
        
        print(f"  Q: {item['query']:<20} 命中: {hits}/{len(relevant_docs)} "
              f"({', '.join(retrieved_docs & relevant_docs)})")
    
    recall = total_hits / total_relevant if total_relevant > 0 else 0
    print(f"\n  Recall@{k}: {recall:.2%}")
    return recall

evaluate_recall(rag, test_set)
```

---

## 4. 项目总结

### 4.1 优缺点对比

| 维度 | RAG + Milvus | 纯 LLM（无检索） | 传统关键词搜索 |
|------|-------------|----------------|--------------|
| 知识覆盖 | 动态更新（加文档即可） | 训练数据截止日期后无法回答 | 需手动维护索引 |
| 回答质量 | 有据可查（引用源文档） | 容易产生幻觉 | 只返回文档片段 |
| 成本 | 中（Embedding + LLM 推理） | 高（需微调或超大 context） | 低 |
| 延迟 | 中（检索 + 生成） | 低（直接生成） | 低 |

### 4.2 适用场景

- **企业知识库问答**：内部制度、SOP、FAQ
- **客服知识库**：产品手册、维修指南
- **法律/医疗辅助**：法规检索 + 问答
- **学术论文检索**：论文库的语义搜索 + 摘要生成

**不适用场景**：实时性要求极高的聊天机器人（检索延迟不可接受）、需要精确数值计算的问答（如"今年利润增长多少%"——应查数据库而非 RAG）。

### 4.3 注意事项

- **Chunk 大小影响全局**：太小语义不完整、太大 Embedding 稀释。512 tokens 是大多数场景的"甜点"。
- **Embedding 模型必须匹配语言**：中文文档用 BGE-M3 或 text2vec，不要用纯英文模型。
- **Prompt 模板需要迭代**：花 20% 时间写检索、80% 时间调 Prompt 是正常的。
- **相似度阈值需要基于数据分布设定**：不要拍脑袋，用分布统计确定。

### 4.4 常见踩坑经验

1. **Chunk 不带 overlap 导致信息断裂**：一个完整条款（如"婚假需提前10天申请，晚婚加7天"）被切成两个 Chunk，LLM 只能看到一半信息。解决：overlap 至少 100 字符。
2. **召回了大量同一文档的不同 Chunk**：搜索结果前 10 条全是《员工手册》的不同段落，其他相关文档被挤出 TopK。解决：在业务层做 doc_id 去重或 diversity rerank。
3. **相似度突然整体偏低**：排查发现 Embedding 模型升级到了新版本，向量空间发生了变化。解决：模型版本和 Milvus 数据绑定，换模型必须重建 Collection。

### 4.5 思考题

1. 如果知识库文档经常更新（每天 50+ 份文档变更），如何设计增量索引更新策略，避免全量重建？
2. Hybrid Search（Dense + Sparse）对 RAG 召回率的提升有多大？请设计一个对比实验验证。

---

> **下一章预告**：第12章我们将扩展到多模态——用 Milvus 实现图片相似检索。读完本章，你应该能独立搭建一个可用的企业 RAG 知识库。
