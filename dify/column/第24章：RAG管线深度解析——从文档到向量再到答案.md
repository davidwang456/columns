# 第24章：RAG 管线深度解析——从文档到向量再到答案

## 1. 项目背景

第 5 章我们以"用户视角"体验了 RAG——上传文档、配置分段、测试检索。但当产品经理追问"为什么 PDF 第 15 页的'保修期 2 年'搜不到"时，你需要从"管线视角"排查。Dify 的 RAG 管线分为六个环节：**提取（Extractor）→ 分段（Splitter）→ 向量化（Embedding）→ 索引（Index）→ 检索（Retrieval）→ 重排（Re-rank）**。每个环节都可能导致"搜不到"。

**场景一：表格数据变乱码**。PDF 里有一个产品参数对比表格，上传后检索"内存 16GB"完全搜不到。根因：提取器用的 PyPDF2 对表格识别能力弱，表格里的数据被读成了一行无意义的字符串。换成 pdfplumber 提取器就好很多——但需要手动切换。

**场景二：关键词被切在两段之间**。"产品保修期 2 年"刚好被 500 token 的分段切成了第 14 段末尾（"产品保修"）和第 15 段开头（"期 2 年"）。用户搜"保修期"时，没有一段完整包含"保修期"，Score 只有 0.35——低于检索阈值。

**场景三：中文语义检索效果差**。英文文档用 OpenAI text-embedding-3-small，检索精度 95%。中文文档用同样的模型，精度只有 70%。根因：OpenAI 的 Embedding 模型以英文为主训练，中文语义捕捉不够细。换成中文优化的 BGE-M3，精度回升到 92%。

本章从源码视角逐一拆解 RAG 管线的六个环节，帮你建立"搜不到→定位到管线环节→修复"的精确排查能力。

## 2. 项目设计——剧本式交锋对话

**小胖**：（指着知识库召回测试结果）"大师！问题'保修期多久'，第一条结果 Score 是 0.45，第二条也是 0.45，第三条直接 0.12。但文档里明明有'保修期 2 年'这句话。怎么相关度这么低？"

**大师**："两个排查方向。第一：打开召回测试的详细结果，看看得分最高的 Chunk 的原文是什么。如果原文是'...产品保修期 2 年，在此期间...'但被切成了'...产品保修'和'期 2 年，在此期间...'——那没有任何一个 Chunk 完整包含'保修期'三个字。第二：检查你的检索阈值。如果你设置的 Score 阈值是 0.5，那 0.45 的结果直接被丢弃了，等于'什么都搜不到'。"

**技术映射**：检索质量 = 分段完整性 × 向量模型匹配度 × 检索阈值设置。三个因素任一个出问题都会导致"搜不到"。

**小白**：（拿着三种分段策略的文档）"分段策略怎么选？自动分段、自定义分段、父子分段——名字挺唬人，但我不知道什么时候用哪个。"

**大师**：
- **自动分段**：适合结构清晰的 Markdown/HTML（有明确的 `#` 标题和空行）。Dify 根据文档的"自然段落边界"切分——每一段就是一个 Chunk。但如果文档段落过长（超过 1000 字），自动分段可能产生超大 Chunk，降低检索精度。
- **自定义分段**：你手动设定每个 Chunk 的 Token 数（如 500）和重叠长度（如 50）。适合大多数场景——规则透明、可预测。如果切得不好（如切断了关键短语），调整参数即可。
- **父子分段**：先用大粒度切（如 2000 Token 的"父段"），再在每个父段内用小粒度切（如 400 Token 的"子段"）。检索时用小粒度匹配（精度高），返回结果时把整个父段带回来（上下文完整）。代价是索引体积大、需要 2 次查询。"

**技术映射**：父子分段 = 小索引 + 大返回。用子段来实现高精度匹配，用父段来保证上下文完整性。

**小胖**："混合检索又是怎么回事？向量检索和关键词检索的分数怎么融合？"

**大师**："两个检索各自跑一遍，各返回 top_k × 2 个结果（比如各 10 个）。然后用 **RRF（Reciprocal Rank Fusion）算法**融合排名——不是融合原始分数（向量检索的 score 0.92 和 BM25 的 score 4.3 不在一个量级），而是融合**排名位置**。RRF 公式：`score = Σ weight / (rank + 60)`。Rank 1 的向量结果贡献 0.7/61 ≈ 0.0115，Rank 1 的关键词结果贡献 0.3/61 ≈ 0.0049。把每个文档在两个排名中的贡献加起来，按总和重新排序。"

**技术映射**：RRF = 排名级融合（不是分数级融合），解决了不同检索算法的分数不可比问题。常数 60 控制了排名衰减速度。

**小白**："Re-rank 又是干什么的？不是已经有检索结果了吗？"

**大师**："Re-rank 是'二次精排'。检索返回了 10 个段落，向量相似度排在前 3 的确实很相关，但第 4 名其实也非常相关——只是向量模型的打分不够精细，把它排到了后面。Re-rank 用一个更强大的 Cross-encoder 模型把问题和这 10 个段落重新对比打分，挑出最终真正最相关的 3 个。代价：多了一次模型调用（增加约 200-500ms 延迟），但精度通常能提升 10-20%。"

## 3. 项目实战

### 环境准备

| 条件 | 说明 |
|------|------|
| 知识库已创建 | 第 5 章完成 |
| Embedding 模型已配置 | OpenAI text-embedding-3 或 BGE-M3 |
| 测试文档 | 准备一份包含表格、长段落、中英混排的文档 |

### 分步实现

#### 步骤1：文档提取器源码对比（目标：理解格式兼容的差异）

```bash
# Dify 支持的提取器一览
ls api/core/rag/extractor/
# extractor_factory.py   → 根据 MIME type 自动选择提取器
# pdf_extractor.py       → PyPDF2 + pdfplumber（双引擎）
# word_extractor.py      → python-docx
# excel_extractor.py     → openpyxl
# markdown_extractor.py  → 正则解析
# html_extractor.py      → BeautifulSoup
# notion_extractor.py    → Notion API

# 关键差异：PDF 提取器的质量
# PyPDF2：快速但表格/列排版的提取质量差
# pdfplumber：慢一些但表格提取质量高
# Dify 默认先用 PyPDF2，如果结果质量差（检测到过多乱码或空行），自动切换为 pdfplumber
```

```python
# Extractor 工厂模式的核心（简化）
class ExtractorFactory:
    EXTRACTOR_MAP = {
        'pdf': PDFExtractor,      # 内部双引擎
        'docx': WordExtractor,
        'xlsx': ExcelExtractor,
        'md': MarkdownExtractor,
        'html': HTMLExtractor,
        'txt': TextExtractor,
    }
    
    @classmethod
    def get_extractor(cls, file_type: str) -> BaseExtractor:
        extractor_cls = cls.EXTRACTOR_MAP.get(file_type.lower())
        if not extractor_cls:
            raise UnsupportedFileTypeError(f"不支持的文件格式: {file_type}")
        return extractor_cls()
```

#### 步骤2：混合检索 RRF 融合算法（目标：理解最终检索分数的计算）

```python
# api/core/rag/retrieval/hybrid_retrieval.py（核心融合逻辑）
class HybridRetrieval:
    def search(self, query: str, top_k: int = 5,
               vector_weight: float = 0.6, keyword_weight: float = 0.4):
        # 1. 各检索 top_k * 2 个结果（扩大候选集）
        vector_results = self.vector_store.search(query, top_k * 2)
        keyword_results = self.keyword_index.search(query, top_k * 2)
        
        # 2. RRF 融合
        scores = {}  # {doc_id: final_score}
        
        for rank, (doc_id, _) in enumerate(vector_results):
            scores[doc_id] = scores.get(doc_id, 0) + vector_weight / (rank + 60)
        
        for rank, (doc_id, _) in enumerate(keyword_results):
            scores[doc_id] = scores.get(doc_id, 0) + keyword_weight / (rank + 60)
        
        # 3. 按融合分排序返回 Top-K
        sorted_items = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        return sorted_items[:top_k]

# 示例：向量检索 Rank 1 → score = 0.6 / (1+60) = 0.00984
#      关键词检索 Rank 3 → score = 0.4 / (3+60) = 0.00635
#      总融合分 = 0.01619
```

#### 步骤3：召回测试——对比不同配置组合（目标：量化各环节影响）

在 Dify 控制台 → 知识库 → 召回测试面板：

```text
测试查询："产品保修期多久"

=== 方案 A：自定义分段 500 + 纯向量检索 ===
结果 1 [0.78]："Acme 硬件产品享受 2 年免费保修..."
结果 2 [0.55]："...退换货政策 / 购买后 30 天内..."  ← 不相关
结果 3 [0.42]："...产品参数表 / 型号 A: 16GB..."    ← 不相关

=== 方案 B：父子分段 + 混合检索 (0.5/0.5) ===
结果 1 [0.91]："产品保修期为 2 年。保修范围包括..." ← 子段命中+父段完整
结果 2 [0.78]："退换货流程...保留原包装可享受..."  ← 部分相关
结果 3 [0.12]："...常见问题 Q: 支持哪些 OS..."     ← 低于阈值（丢弃）

=== 方案 C：方案 B + Re-rank ===
结果 1 [Re-rank: 0.96]："产品保修期为 2 年..."     ← 精排后最优
结果 2 [Re-rank: 0.82]："退换货需在保修期内..."    ← 提升了
结果 3 [Re-rank: 0.15]："..."                       ← 被丢弃
```

**结论**：方案 C（父子分段 + 混合检索 + Re-rank）效果最好，但延迟最高（多 200ms）。

### 测试验证

```bash
# 查看文档分段详情
docker exec docker-db-1 psql -U postgres -d dify -c \
  "SELECT d.name, COUNT(s.id) as segments, 
          AVG(LENGTH(s.content)) as avg_len
   FROM documents d
   JOIN segments s ON s.document_id = d.id
   GROUP BY d.id, d.name
   ORDER BY segments DESC;"

# 验证向量数据库中的向量维度
docker exec docker-weaviate-1 wget -qO- \
  "http://localhost:8080/v1/schema" 2>/dev/null | \
  python -c "import sys,json; [print(c['class'], c.get('vectorizer',{})) for c in json.load(sys.stdin)['classes']]"
```

## 4. 项目总结

### 管线六环节总览

| 环节 | 职责 | 关键优化点 | 典型延迟 |
|------|------|----------|---------|
| 提取 | 文件→纯文本 | 表格型 PDF → pdfplumber | 1-10s（取决于文件） |
| 分段 | 纯文本→Chunks | 父子分段保证上下文完整性 | 0.1-1s |
| 向量化 | Chunks→Vectors | 中文选 BGE-M3，英文选 OpenAI | 0.5-2s/段 |
| 索引 | Vectors→VectorDB | 批量写入 + upsert | 0.1-1s/段 |
| 检索 | Query→Top-K Chunks | 混合检索 + RRF 融合 | 50-200ms |
| 重排 | Top-2K→Top-K | Cross-encoder 精排 | 100-500ms |

### 适用场景

| 场景 | 推荐分段策略 | 推荐检索模式 |
|------|------------|------------|
| 法律文档（精确引用） | 父子分段 | 纯关键词检索（BM25） |
| 产品 FAQ | 自定义 300 tokens | 混合检索（向量 0.7 / 关键词 0.3） |
| 技术文档（中英混排） | 自定义 500 tokens | 混合检索 + Re-rank |
| 新闻文章 | 自动分段 | 纯向量检索 |

### 注意事项

1. **Embedding 模型切换需要重建索引**：从 OpenAI 换到 BGE-M3，向量维度可能从 1536 变到 1024，向量数据库中旧数据不兼容，必须清空重建
2. **混合检索权重需根据场景调优**：没有"万能权重"。法律文档偏关键词（0.3/0.7），闲聊 FAQ 偏语义（0.8/0.2）
3. **Re-rank 模型本身也有 Token 限制**：Cross-encoder 模型的输入长度通常有限（如 512 tokens），超长的 Chunk 会被截断

### 常见踩坑经验

1. **坑：PDF 表格数据提取全乱码** → 根因：PyPDF2 对表格识别差。解决：在 `pdf_extractor.py` 中强制使用 pdfplumber（修改提取器选择策略）
2. **坑：分段大小改了但检索效果没变化** → 根因：改了分段参数后没有重新索引。分段参数在索引时写入，已经索引的 Chunk 不会自动重新分段。解决：删除文档后重新上传，或点击"重新索引"
3. **坑：Re-rank 开启后反而变慢了但精度没提升** → 根因：检索返回的候选集质量太差（Top-K 全是弱相关），Re-rank 也无米之炊。解决：先优化检索阶段（换 Embedding 模型或调整混合权重）

### 思考题

1. **进阶题**：如果你的知识库包含大量表格数据（如产品参数对比表），传统的分段策略会把表格行切散。请设计一种"表格感知的分段策略"——检测到表格时，将整张表作为一个不可分割的 Chunk。（提示：在 Extractor 阶段标记表格边界，Splitter 阶段识别边界标记）

2. **进阶题**：当前 RRF 公式中常数 60 是经验值。如果改为 10，排名靠前的结果权重会更大还是更小？对融合排序有什么影响？（提示：`weight / (rank + K)` 中 K 越小，rank 的影响越大——排名靠前的结果获得更多权重）

> **参考答案**：见附录 D

---

> **推广计划提示**：本章是中级篇 RAG 专题的核心。务必完成步骤 3 的召回测试对比（三种方案 × 同一查询），这是说服团队"为什么需要用父子分段/Re-rank"的量化证据。
