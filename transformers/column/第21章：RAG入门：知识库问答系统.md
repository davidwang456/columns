# 第21章：RAG 入门：知识库问答系统

## 1 项目背景

### 业务场景

某大型制造企业的IT部门维护着一套包含5000+篇文档的内部知识库——设备操作手册、SOP标准流程、安全规范、HR政策等。员工每天在OA系统中提交约1500次搜索查询，但传统的关键词搜索引擎只能返回文档链接，员工仍需逐篇翻阅找到答案。

CTO提出需求：构建一个"企业级ChatGPT"——员工用自然语言提问，系统直接从知识库中找到答案并生成回复，必须附带引用来源（出自哪篇文档的第几页），且不能胡编乱造（杜绝幻觉）。

技术团队评估后发现问题远比想象中复杂：直接让大模型回答，它会根据训练数据"编造"出看似合理但完全虚构的操作规程；而第7章的抽取式QA只能从单段文档中找答案，无法组合多篇文档的信息。

### 痛点放大

这是典型的"私有知识+生成"问题，纯生成模型和纯检索模型各有一个致命短板：

```
纯大模型（GPT/Qwen）:
  Q: "SOP-2024 规定的紧急停机流程是什么？"
  A: "紧急情况下应立即按下红色紧急停止按钮..."  ← 可能完全是编的！
  问题: 模型训练数据中没有这份内部SOP文档，它会"幻觉"出答案

纯检索（关键词/Embedding）:
  Q: "A设备故障时B工序该怎么处理？"
  A: 返回5篇包含"A设备""B工序"的文档链接
  问题: 用户需要组合信息（A故障原因+B工序影响+应急方案），检索不会组合
```

RAG（Retrieval-Augmented Generation）正是为解决这个矛盾而生：**检索 + 生成 = 先找到相关文档片段，再基于这些片段生成准确答案**。

---

## 2 项目设计

### 剧本式交锋对话

**场景**：周二上午 10:00，AI Lab。小陈刚在内部知识库上跑通了第一版RAG Demo，但召回的相关文档只有一半是真正相关的。

---

**小胖**:"你这个RAG是啥？听名字像是个破布（Rag），哈哈。是不是把知识库的布条缝成一件答案的衣服？"

**小陈**:"差不多。RAG全称是Retrieval-Augmented Generation——检索增强生成。意思是在生成答案之前，先从知识库里检索出最相关的文档片段，把这些片段塞到prompt里，再让大模型基于这些片段生成答案。这样模型就不会瞎编了。"

**小胖**:"哦！就像考试开卷——不是让你凭空答题，而是给你相关的课本页，让你看着答。"

**大师**:"这个比喻很精准。RAG的本质就是**给模型提供参考资料，限制它的思考范围**。让我把RAG的三个核心环节讲清楚。

**环节一：文档处理与索引。** 这是RAG系统的地基。如果把5000篇原始文档直接丢进去检索，效果会很差——一篇30页的设备手册，如果不切分，整篇做Embedding时关键信息被稀释。正确的做法是：

- **chunk切分**：把文档切成合适大小的片段（chunk），通常300-500字一段。太短没有完整语义，太长检索精度下降。
- **overlap重叠**：相邻chunk之间保留10-20%的重叠，防止关键信息正好落在chunk边界被切断。
- **metadata保留**：每个chunk携带来源信息（文档标题、页码、版本号），方便溯源。

**环节二：混合检索。** 只用Embedding（稠密检索）做召回有一个盲区——对于包含精确术语的问题（如'参数PMAX的默认值是多少'），Embedding可能匹配到语义相关但不含精确值的文档。所以生产级RAG会用混合检索：

- **稀疏检索**（BM25/Elasticsearch）：关键词匹配，精确但缺失语义理解
- **稠密检索**（Embedding+FAISS）：语义匹配，但可能漏掉精确术语
- **融合策略**：RRF（Reciprocal Rank Fusion）或加权求和，结合两者优势

**环节三：生成与引用。** 把召回的Top N个chunks拼接到prompt中，要求模型基于这些片段生成答案，并注明引用来源。Prompt模板：

```
请根据以下参考资料回答用户问题。只能使用参考资料中的信息，
如果资料中不包含答案，请明确说"根据现有资料无法回答"。

参考资料:
[1] {chunk1}  (来源: 设备操作手册 v3.2, 第15页)
[2] {chunk2}  (来源: 安全规范 SOP-2024, 第8页)

用户问题: {question}
请给出答案，并注明引用来源编号:
```

```

**小白**:"那怎么评估RAG系统的质量？不能只看生成的答案是否流畅，还得看引用是否准确。"

**大师**:"RAG评估需要三个维度：
- **召回率（Recall@K）**：Top K个检索结果中包含正确答案的比例
- **答案忠实度（Faithfulness）**：生成的答案是否完全基于提供的参考资料（有无幻觉）
- **答案相关性（Answer Relevance）**：生成的答案是否真正回答了用户的问题

这三个指标需要分开评估。召回率只评估检索模块，忠实度和相关性评估整个RAG pipeline。"

**技术映射总结**：
- RAG = 开卷考试，先翻课本（检索）再作答（生成）
- Chunk切分 = 把书拆成卡片，每张卡片包含完整语义
- 混合检索 = 关键词索引 + 语义索引，互补不足
- 召回率/忠实度/相关性 = RAG的"体检三项"，缺一不可

---

## 3 项目实战

### 3.1 环境准备

```bash
pip install transformers==4.44.0 torch sentence-transformers faiss-cpu
pip install langchain>=0.1.0 langchain-community  # RAG 框架
pip install pypdf>=4.0.0  # PDF 解析
```

### 3.2 文档切分与索引构建

```python
# rag_index.py
"""RAG 文档切分与向量索引构建"""

import os
import re
from typing import List, Dict
from sentence_transformers import SentenceTransformer
import numpy as np
import faiss


class DocumentChunker:
    """文档切分器"""

    def __init__(self, chunk_size: int = 500, chunk_overlap: int = 100):
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap

    def split_text(self, text: str, metadata: Dict = None) -> List[Dict]:
        """将长文本切分为带重叠的 chunk"""
        chunks = []
        start = 0
        chunk_idx = 0

        while start < len(text):
            end = min(start + self.chunk_size, len(text))

            # 尽量在句子边界处切分
            if end < len(text):
                # 找最近的句号/换行
                for sep in ["。", "\n", "；", ". "]:
                    last_sep = text.rfind(sep, start, end)
                    if last_sep > start + self.chunk_size // 2:
                        end = last_sep + 1
                        break

            chunk_text = text[start:end].strip()
            if len(chunk_text) >= 50:  # 过滤过短的片段
                chunks.append({
                    "text": chunk_text,
                    "chunk_id": chunk_idx,
                    "metadata": metadata or {},
                })
                chunk_idx += 1

            start = end - self.chunk_overlap
            if start >= len(text):
                break

        return chunks


class RAGIndex:
    """RAG 知识库索引"""

    def __init__(self, embedder_model: str = "paraphrase-multilingual-MiniLM-L12-v2"):
        self.embedder = SentenceTransformer(embedder_model)
        self.chunks = []
        self.index = None

    def build_from_documents(self, documents: List[Dict[str, str]]):
        """
        从文档列表构建索引

        Args:
            documents: [{"title": "...", "content": "...", "source": "..."}]
        """
        chunker = DocumentChunker(chunk_size=500, chunk_overlap=100)
        self.chunks = []

        print(f"处理 {len(documents)} 篇文档...")
        for doc in documents:
            doc_chunks = chunker.split_text(
                doc["content"],
                metadata={"title": doc.get("title", ""), "source": doc.get("source", "")}
            )
            self.chunks.extend(doc_chunks)

        print(f"共生成 {len(self.chunks)} 个 chunk")

        # 编码
        print("正在编码...")
        texts = [c["text"] for c in self.chunks]
        embeddings = self.embedder.encode(texts, normalize_embeddings=True,
                                          show_progress_bar=True)

        # FAISS 索引
        dim = embeddings.shape[1]
        self.index = faiss.IndexFlatIP(dim)
        self.index.add(embeddings.astype(np.float32))
        print(f"索引构建完成: {self.index.ntotal} 个向量, 维度={dim}")

    def search(self, query: str, top_k: int = 5,
               min_score: float = 0.3) -> List[Dict]:
        """混合检索（稠密 + 简单的关键词加权）"""
        if self.index is None:
            raise RuntimeError("索引未构建")

        # 稠密检索
        query_vec = self.embedder.encode(
            [query], normalize_embeddings=True
        ).astype(np.float32)
        scores, indices = self.index.search(query_vec, top_k * 2)

        results = []
        for score, idx in zip(scores[0], indices[0]):
            if idx < 0 or idx >= len(self.chunks):
                continue

            # 关键词加分
            keyword_bonus = self._keyword_score(query, self.chunks[idx]["text"])
            combined_score = score * 0.7 + keyword_bonus * 0.3

            if combined_score >= min_score:
                results.append({
                    "text": self.chunks[idx]["text"],
                    "score": round(float(combined_score), 4),
                    "dense_score": round(float(score), 4),
                    "metadata": self.chunks[idx]["metadata"],
                })

        # 重新排序取 top_k
        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:top_k]

    def _keyword_score(self, query: str, text: str) -> float:
        """简单的关键词匹配分数"""
        query_tokens = set(query)
        text_tokens = set(text)
        if not query_tokens:
            return 0
        overlap = len(query_tokens & text_tokens)
        return overlap / len(query_tokens)


# ===== 使用示例 =====
if __name__ == "__main__":
    # 模拟企业内部文档
    docs = [
        {
            "title": "设备操作手册 v3.2",
            "source": "/docs/manual/equipment_v3.2.pdf",
            "content": "第15页：紧急停机流程。当设备出现异常噪音或烟雾时，"
                       "操作员应立即按下控制面板上的红色紧急停止按钮（EMO-01）。"
                       "按下后设备将在3秒内完全停止，同时触发声光报警。"
                       "恢复操作前必须由维修工程师检查确认。"
                       "第16页：日常维护。每天开机前检查冷却液液位，"
                       "每周清理滤网，每月更换润滑油。"
                       "第17页：故障代码E01-E05。E01表示过载保护触发..."
        },
        {
            "title": "安全规范 SOP-2024",
            "source": "/docs/safety/sop_2024.pdf",
            "content": "第8页：个人防护装备要求。进入生产车间必须佩戴安全帽、"
                       "防护眼镜和防静电鞋。接触化学品时必须加戴防腐蚀手套。"
                       "第9页：火灾应急。发现火情立即启动最近的火警按钮，"
                       "并使用灭火器进行初期扑救。若火势无法控制在30秒内，"
                       "立即撤离并拨打厂内消防电话1199。"
        },
    ]

    index = RAGIndex()
    index.build_from_documents(docs)

    # 检索测试
    queries = [
        "设备出现异常噪音时应该怎么做？",
        "进入车间需要佩戴什么？",
    ]
    for q in queries:
        print(f"\n🔍 {q}")
        results = index.search(q, top_k=3)
        for r in results:
            print(f"  [{r['score']:.3f}] {r['text'][:80]}...")
            print(f"       来源: {r['metadata'].get('title', 'N/A')}")
```

### 3.3 RAG 生成服务

```python
# rag_generator.py
"""RAG 生成服务：检索 + 生成 + 引用"""

import torch
from transformers import AutoTokenizer, AutoModelForCausalLM
from typing import List, Dict


class RAGGenerator:
    """RAG 问答生成器"""

    PROMPT_TEMPLATE = """你是一个企业知识库助手。请严格根据以下参考资料回答用户问题。

规则：
1. 只能使用参考资料中的信息，不得编造
2. 如果资料不足以回答，请明确说"根据现有资料，我无法回答此问题"
3. 回答时引用来源编号，格式为 [来源1][来源2]
4. 回答简洁准确，不超过200字

参考资料：
{context}

用户问题：{question}
回答："""

    def __init__(self, model_name: str = "uer/gpt2-chinese-cluecorpussmall"):
        self.tokenizer = AutoTokenizer.from_pretrained(model_name)
        self.tokenizer.pad_token = self.tokenizer.eos_token
        self.model = AutoModelForCausalLM.from_pretrained(model_name)
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self.model.to(self.device)
        self.model.eval()

    def generate(self, question: str, retrieved_chunks: List[Dict],
                 max_new_tokens: int = 200) -> Dict:
        """基于检索结果生成答案"""
        # 构建上下文
        context_parts = []
        for i, chunk in enumerate(retrieved_chunks):
            source = chunk["metadata"].get("title", f"文档{i+1}")
            context_parts.append(f"[来源{i+1}] {source}: {chunk['text']}")

        context = "\n\n".join(context_parts)

        # 构建 prompt
        prompt = self.PROMPT_TEMPLATE.format(
            context=context,
            question=question,
        )

        # 生成
        inputs = self.tokenizer(prompt, return_tensors="pt",
                                truncation=True, max_length=1024).to(self.device)

        with torch.no_grad():
            outputs = self.model.generate(
                **inputs,
                max_new_tokens=max_new_tokens,
                temperature=0.3,         # 低温度，减少幻觉
                top_p=0.9,
                do_sample=True,
                pad_token_id=self.tokenizer.eos_token_id,
                repetition_penalty=1.1,
            )

        full_output = self.tokenizer.decode(outputs[0], skip_special_tokens=True)
        # 提取"回答："之后的内容
        answer = full_output.split("回答：")[-1].strip()

        # 收集引用来源
        sources = []
        for i, chunk in enumerate(retrieved_chunks):
            sources.append({
                "source_id": i + 1,
                "title": chunk["metadata"].get("title", "未知文档"),
                "score": chunk["score"],
            })

        return {
            "question": question,
            "answer": answer,
            "sources": sources,
            "retrieved_count": len(retrieved_chunks),
        }


# ===== 完整 RAG Pipeline =====
class RAGPipeline:
    """完整的 RAG pipeline：索引 + 检索 + 生成"""

    def __init__(self, index: RAGIndex, generator: RAGGenerator):
        self.index = index
        self.generator = generator

    def ask(self, question: str, top_k: int = 5) -> Dict:
        """问答接口"""
        # Step 1: 检索
        chunks = self.index.search(question, top_k=top_k)

        # Step 2: 判断是否有相关结果
        if not chunks or chunks[0]["score"] < 0.2:
            return {
                "question": question,
                "answer": "根据现有资料，我无法回答此问题。建议您查阅最新版的设备操作手册或联系技术支持。",
                "sources": [],
                "status": "no_relevant_docs",
            }

        # Step 3: 生成
        result = self.generator.generate(question, chunks)
        result["status"] = "success"
        return result


# ===== 使用示例 =====
if __name__ == "__main__":
    from rag_index import RAGIndex

    # 构建索引
    docs = [
        {"title": "设备手册", "source": "manual_v3.pdf", "content": "紧急停机：按下红色按钮EMO-01，3秒内停止。恢复前需工程师检查。日常维护：每天检查冷却液。"},
        {"title": "安全规范", "source": "safety_2024.pdf", "content": "进入车间必须佩戴安全帽、防护眼镜和防静电鞋。火灾时启动火警按钮并拨打1199。"},
    ]
    index = RAGIndex()
    index.build_from_documents(docs)

    generator = RAGGenerator()
    pipeline = RAGPipeline(index, generator)

    questions = [
        "设备出现异常怎么停机？",
        "进入车间要穿什么？",
        "公司食堂在哪？",  # 知识库中没有
    ]

    for q in questions:
        result = pipeline.ask(q)
        print(f"\n👤 用户: {result['question']}")
        print(f"🤖 助手: {result['answer']}")
        if result.get("sources"):
            for s in result["sources"]:
                print(f"    📚 [来源{s['source_id']}] {s['title']}")
```

### 3.4 测试验证

```python
# test_rag.py
import pytest
import numpy as np
from rag_index import DocumentChunker, RAGIndex

class TestDocumentChunker:
    def test_basic_split(self):
        chunker = DocumentChunker(chunk_size=100, chunk_overlap=20)
        text = "这是第一段内容。" * 10
        chunks = chunker.split_text(text)
        assert len(chunks) > 1
        for c in chunks:
            assert 50 <= len(c["text"]) <= 120

    def test_overlap(self):
        chunker = DocumentChunker(chunk_size=200, chunk_overlap=50)
        text = "ABCDEFGHIJ" * 30
        chunks = chunker.split_text(text)
        if len(chunks) >= 2:
            # 检查重叠
            assert chunks[0]["text"][-30:] in chunks[1]["text"] or \
                   any(c in chunks[1]["text"] for c in chunks[0]["text"][-20:])

class TestRAGIndex:
    def test_build_and_search(self):
        docs = [{"title": "test", "source": "test.txt", "content": "这是测试文档内容" * 20}]
        index = RAGIndex()
        index.build_from_documents(docs)
        assert index.index is not None
        results = index.search("测试文档")
        assert len(results) > 0
        assert results[0]["score"] > 0

    def test_no_results(self):
        index = RAGIndex()
        index.build_from_documents([
            {"title": "t", "source": "t.txt", "content": "ABC" * 100}
        ])
        results = index.search("完全不相关的查询XYZ", top_k=3)
        # 没有相关结果时可能返回低分结果
        if results:
            assert results[0]["score"] < 0.5
```

---

## 4 项目总结

### 4.1 优点与缺点

| 方面 | 优点 | 缺点 |
|------|------|------|
| **RAG** | 将私有知识与生成能力结合，减少幻觉，答案可溯源 | 检索质量是瓶颈——检索错了生成必然错 |
| **Chunk切分** | 平衡检索精度与语义完整性 | 固定chunk大小不适应所有文档类型 |
| **混合检索** | 稠密+稀疏互补，召回率显著提升 | 融合权重需要调参 |
| **引用溯源** | 用户可验证答案来源，建立信任 | 引用格式不当时可能误导用户 |

### 4.2 适用场景

| 场景 | 推荐方案 |
|------|---------|
| 企业知识库问答 | RAG + 混合检索 + 引用标注 |
| 客服知识库 | RAG + FAQ Embedding 粗排 + Cross-Encoder 精排 |
| 法律/医疗文档咨询 | RAG + 领域微调Embedding + 严格引用 |
| 产品手册智能问答 | RAG + 结构化文档解析（表格、图表） |

**不适用场景**：
- 需要实时更新的数据（如股价、天气），RAG 索引更新有延迟
- 需要多步推理的复杂问题（如"对比A方案和B方案的总成本，并推荐最优"），需要Agent+多轮RAG

### 4.3 注意事项

1. **chunk_size 与 chunk_overlap**：500字 + 100字重叠是经验之选，问答场景可缩小到200-300字
2. **prompt设计**：必须明确"只能使用参考资料"和"不确定就说不知道"，否则模型仍会自由发挥
3. **检索阈值**：相关性分数 < 阈值的应拒绝回答，防止"强行回答"

### 4.4 常见踩坑经验

| 故障现象 | 根因 | 解决方案 |
|---------|------|---------|
| 检索到的chunk完全不相关 | Embedding模型未适配领域词汇 | 在领域数据上微调Embedding模型 |
| 生成的答案引用了错误的来源编号 | prompt中来源编号与生成时的对齐偏差 | 后处理中校验来源编号是否在有效范围内 |
| 知识库更新后检索不到新内容 | 索引未重建 | 增量更新索引（追加向量+重建FAISS） |

### 4.5 思考题

1. **初级**：在 `rag_index.py` 中，将 `chunk_size` 从 500 改为 200，再改为 1000。对同一问题的检索结果有何变化？哪种更适合你的场景？
2. **进阶**：设计一个**多轮对话RAG**系统——用户可以根据上一轮的回答追问，系统需要结合上下文进行新一轮检索和生成。（提示：考虑历史对话的Embedding和query重写）

（答案将在第22章末尾给出）

### 4.6 第20章思考题答案

**第20章思考题1**：
- 在 `_validate_content` 中增加长度校验：`if len(result.get("suggested_action", "")) > 200: result["suggested_action"] = result["suggested_action"][:197] + "..."`。注意中文字符截断时不能切在多字节字符中间。

**第20章思考题2**：
- 混合输出方案：(1) 将输出分为两阶段——先用约束解码生成JSON部分（`{"category":"投诉"...}`）；(2) JSON生成完毕（检测到 `}` 后），切换为自由采样模式生成自然语言摘要；(3) 实现关键：在 `LogitsProcessor` 中检测当前状态，JSON内用约束，JSON外用正常top-p。或更简单的方案：分两次调用——第一次生成JSON，第二次基于JSON内容生成摘要。

### 4.7 推广计划提示

| 部门 | 建议行动 |
|------|---------|
| **开发团队** | 将 `RAGPipeline` 封装为微服务，暴露 `/ask` 和 `/rebuild_index` 接口 |
| **测试团队** | 构建 100 条人工标注的 QA 对，计算 Recall@5 和答案准确率 |
| **运维团队** | 文档更新后自动触发索引重建，监控索引构建耗时和检索延迟 |

---

> **下一章预告**：第22章将进入推理性能优化——批处理、fp16/int8/int4量化、device_map自动分配、Flash Attention等实战技巧，让推理更快更省。
