# 第4章：Embedding 生成与数据写入实战

> **定位**：把业务文本变成可检索的向量数据。
> **版本**：Milvus 2.5.x
> **源码关联**：internal/proxy/insert.go、internal/datacoord/insert_channel.go、internal/datanode/data_sync_service.go

---

## 1. 项目背景

某电商平台运营团队导出了一份 10 万条商品数据（CSV 格式），包含商品标题、价格、类目等信息，希望批量导入 Milvus 做向量检索。数据架构师李明接下了这个任务，本以为就是"读 CSV → 调 Embedding API → insert 到 Milvus"三条命令的事，结果实际操盘时踩了一连串坑：

1. **Embedding 调用超时**——用 OpenAI API 生成向量，100 QPM 的速率限制下，10 万条数据跑了 16 个小时还没跑完。
2. **批量写入太大导致 OOM**——一次 Insert 塞了 5 万条数据，Proxy 内存直接爆了，请求被拒绝。
3. **写入成功了但搜不到**——存入 1000 条数据后立即搜索，结果为空。排查发现忘记调用 `flush()`，数据还在 Growing Segment 里，但搜索时没指定对应的 Guarantee Timestamp。
4. **其中 300 条数据主键冲突**——CSV 中的商品和 MySQL 已有的商品用了同一个 ID 体系，Insert 报 duplicate key error，但没有 Upsert 机制覆盖旧数据。
5. **编码问题**——商品标题里含有 emoji 表情（如 "🔥爆款"），Embedding 模型处理时报 Unicode 编码错误。

更致命的是，李明没意识到"数据清洗"的重要性——CSV 里有 15% 的商品标题是重复的（同一个 SKU 多颜色变体），5% 的标题超过 2000 字符（需要截断分块），还有 2% 的标题是纯英文而模型是中文优化的（需要分离处理）。这些脏数据直接写入了 Milvus，导致搜索结果里前十名有五个是同一个商品的不同颜色。

本章将从"数据准备 → Embedding 生成 → 批量写入"全链路切入，覆盖 Embedding 模型选择、数据清洗、批量大小调优、写入方式选择、错误重试与幂等性设计等实战核心。

---

## 2. 项目设计（剧本式交锋对话）

**第一幕：Embedding 模型选择——本地 vs 云端 vs 稀疏向量**

*（小胖的终端屏幕上，OpenAI Embedding 调用进度条卡在了 23%，已经跑了 4 个小时）*

**小胖**（崩溃地）："大师，为什么生成 10 万条向量的 Embedding 要这么久？我用的 OpenAI 的 text-embedding-ada-002，调一次要 0.1 秒，10 万条就是 10000 秒——差不多 3 个小时！而且中间还老超时……"

**大师**："你这就是典型的'能用但不好用'。OpenAI API 的速率限制是 3000 RPM（Request Per Minute），但对大客户是 10000 RPM。如果你不是付费大客户，10 万条数据单线程调用，3 小时是正常的。但你可以用三种方式大幅加速——"

| 方案 | 吞吐量（条/秒） | 10万条耗时 | 成本 | 适用场景 |
|------|---------------|-----------|------|---------|
| OpenAI API（单线程） | ~10 | ~3小时 | $0.10/1K tokens | 小批量 POC |
| OpenAI API（多线程 10并发） | ~80 | ~20分钟 | 同上 | 中等批量，可接受 API 费用 |
| 本地模型（BGE-Large，GPU） | ~500 | ~3分钟 | 0 元 + GPU 服务器成本 | 大批量，自有 GPU 资源 |
| 本地模型（BGE-Small，CPU） | ~50 | ~30分钟 | 0 元 | 无 GPU 的开发环境 |

**小白**："那如果文本很长怎么办？商品标题还好，要是做 RAG 知识库，文档动辄上万字——"

**大师**："这就涉及到 Embedding 之前最重要的一步：**文本分块（Chunking）**。模型都有最大输入长度限制——BGE-Large 是 512 tokens，text-embedding-ada-002 是 8192 tokens。如果你的文档超过这个限制，直接 Embedding 会触发截断，丢失尾部信息。正确的做法是分块——把长文档切成多个 Chunk，每个 Chunk 单独生成向量。"

```python
# 文本分块示意
# 原文: "这是一篇关于 Milvus 部署的详细指南，包括环境准备、Docker 配置、K8s 部署..."
# 
# Chunk 1: "这是一篇关于 Milvus 部署的详细指南，包括环境准备、Docker 配置..."
# Chunk 2: "...K8s 部署步骤、性能调优、常见故障排查"
#           ↑ 保留 10-20% 的 Overlap 避免信息断裂
```

**小胖**："等等，我好像还听说过稀疏向量？那又是什么？"

**大师**："好问题，这正好引出 Milvus 2.4 之后的一个大特性——Hybrid Search。Dense Vector（稠密向量）就是你刚才用的 1024 维浮点数，擅长语义理解。Sparse Vector（稀疏向量）是类似 TF-IDF 或 BM25 的倒排索引表示，擅长关键词精确匹配。两者的对比——"

| 特性 | Dense Vector | Sparse Vector |
|------|-------------|--------------|
| 表示 | 1024维浮点数组 | 大部分为0，只有少量非零值 |
| 语义理解 | 强（"汽车"和"轿车"能匹配） | 弱（只做字面匹配） |
| 关键词匹配 | 弱（可能漏掉精确关键词） | 强（检索"Milvus 2.5"绝不会混淆） |
| 混合使用 | Dense + Sparse Hybrid Search | ↑ Rerank 后效果最佳 |

**大师**："对于商品搜索，推荐纯 Dense Vector 就够了（商品标题大多短文本）。对于知识库问答（RAG），Dense + Sparse Hybrid 是更优方案。"

> **技术映射**：Embedding 模型 = "文本翻译器"（文本 → 向量）；本地模型 = 自建翻译团队（一次投入、长期使用）；云端模型 = 外包翻译公司（按量付费、无需维护）；Sparse Vector = 倒排索引的向量化（保关键词）。

---

**第二幕：数据清洗——不洗的数据等于垃圾**

**小白**："大师，你之前说'数据清洗比数据写入更重要'。具体要洗什么？我直接把 CSV 读进来 Embedding 不行吗？"

**大师**："那我给你看个真实的脏数据案例——"

**大师**（在白板上列数据）：

```
原始数据 (100,000 条商品):
├── 重复数据: 15,000 条 (15%)
│     例: "iPhone 15 手机壳 蓝色" × 12 (同一 SKU 不同颜色)
│     影响: 搜索"苹果手机壳"时 Top10 全是同一款
│     处理: 按 SKU 去重，只保留第一个
│
├── 超长文本: 5,000 条 (5%)
│     例: 标题 3000+ 字符（商家 SEO 堆砌关键词）
│     影响: 超出 Embedding 模型最大输入，尾部信息被截断
│     处理: 截断到 512 字符，或按语义分块
│
├── 无效字符: 2,000 条 (2%)
│     例: 包含 emoji、HTML 标签、乱码
│     影响: Embedding 模型报错或生成无意义向量
│     处理: 正则清洗，移除 emoji 和 HTML 标签
│
├── 空标题: 500 条 (0.5%)
│     影响: 无法生成向量，写入失败
│     处理: 直接丢弃或标记为"待补充"
│
└── 纯英文/混合语言: 3,000 条 (3%)
      影响: 中文模型对英文 Embedding 效果差
      处理: 按语言分流，英文用多语言模型
```

**小白**："那去重、截断、清洗这些操作，是在写 Milvus 之前做还是之后做？"

**大师**："当然是在写入之前。数据进入向量数据库后再想清洗，成本极高——你不仅要找到重复的数据，还要确认删了哪些、重建哪些。记住一条铁律：**进入向量数据库前的数据清洗成本是 1，进入后清洗成本是 100**。"

**小胖**："那我去重的时候如果发现两个商品标题完全一样但 ID 不同，该保留哪一个？"

**大师**："这取决于业务逻辑。如果是同一 SKU 的不同颜色变体，建议保留 ID 更小的那个（最早入库的），或者按销售额保留热销的。但有一个原则：**去重逻辑要可复现、可追溯**。如果你的去重脚本跑两次产生不同结果，那就有大问题。"

> **技术映射**：数据清洗 = 流水线上的质检环节（脏数据进 = 脏结果出）；去重 = 保证搜索结果多样性；截断/分块 = 适配模型输入限制；字符清洗 = 避免向量无意义。

---

**第三幕：Insert vs Upsert vs Flush——写入方式的抉择**

**小胖**："那我数据也洗好了，Embedding 也生成了，现在该写 Milvus 了吧？Insert 和 Upsert 到底用哪个？还有什么 Flush——"

**大师**："这三个概念非常容易混淆，我用一个快递站的比喻——"

**大师**（画示意图）：

```
Insert (插入)                      Upsert (插入或更新)              Flush (刷盘)
─────────────────                ────────────────────            ─────────────
"把包裹放上传送带"                 "包裹放上传送带，                 "传送带上的包裹
                                 如果已有相同单号                  全部送进仓库"
Client ──→ Proxy ──→ MQ          ──→ 覆盖旧的"                   (Growing → Sealed)
                              │
如果主键已存在 → 报错           │                             数据变成"可持久化"的
"Duplicate primary key"     │                              断电重启不会丢
```

| 操作 | 行为 | 适用场景 | 幂等性 |
|------|------|---------|--------|
| Insert | 写入新数据 | 首次导入、追加数据 | 否（重复主键报错） |
| Upsert | 插入或覆盖（基于主键） | 增量同步、数据更新 | 是（相同主键重复执行结果一致） |
| Flush | 强制将内存中的 Growing Segment 持久化 | 确认数据落盘后再搜索 | 是（多次 Flush 安全） |

**小白**："Upsert 听起来很强大。那为什么不全用 Upsert？Insert 还有存在的必要吗？"

**大师**："Upsert 确实方便，但有两个代价。第一，性能——Upsert 需要额外查找主键是否存在，比 Insert 慢 10-20%。第二，语义——有时候你不想覆盖旧数据。比如商品搜索场景，如果你从 MySQL 同步数据，用 Insert 可以帮你发现'不该出现的重复主键'，这是一个数据质量的哨兵。"

**小白**："那 Flush 呢？我每次 Insert 后都要 Flush 吗？"

**大师**："不需要。Flush 是一个'全局'操作，它会将当前 Collection 的所有 Growing Segment 持久化并进行封存。频繁 Flush 会产生大量小 Segment，影响搜索性能。最佳实践是：批量写入后调用一次 Flush，而不是每条写入后都 Flush。"

**小胖**："等等——你说 Flush 之后数据才持久化。那我如果不 Flush，Milvus 突然重启了，刚写入的数据会丢吗？"

**大师**："这也是一个关键理解点。Milvus 的写入流程是：Proxy 接收数据后写入 Message Queue（WAL），然后返回客户端成功。DataNode 从 MQ 消费数据写入 Growing Segment。也就是说——"
- **客户端收到 Insert 成功 = 数据已安全写入 MQ（断电解耦点）**
- **DataNode Flush = 数据从 MQ 进入对象存储（持久化完成）**

"即使 Milvus 在 Insert 之后、Flush 之前崩溃，数据也不会丢失，因为它还在 MQ 里。Milvus 重启后 DataNode 会从 MQ 的断点续消费。"

> **技术映射**：Insert = 首次写入（严格唯一性）；Upsert = 覆盖写入（适合增量同步）；Flush = 刷盘操作（生成 Sealed Segment 并持久化）；MQ = Milvus 的 WAL（保证数据不丢失）。

---

## 3. 项目实战

### 3.1 实战目标

将 1000 条商品标题生成向量并写入 Milvus，记录批量大小对写入耗时的影响，形成批量写入最佳实践。

### 3.2 环境准备

```bash
pip install pymilvus==2.5.5 sentence-transformers numpy pandas
```

### 3.3 分步实现

#### 步骤 1：生成模拟商品数据

```python
# step1_generate_data.py
"""生成 1000 条模拟商品数据"""
import random
import json

categories = ["户外运动", "家居日用", "数码电子", "服饰鞋包", "食品饮料"]
brands = ["极地户外", "宜家优选", "科技先锋", "潮流前线", "有机农场"]

# 模拟商品标题模板
title_templates = [
    "{品牌} {类目} 热销款 {形容词} {名词}",
    "新款 {形容词} {名词} {类目} {品牌} 正品",
    "{类目} 专用 {形容词} {名词} 大容量 便携",
    "{品牌} 旗舰店 {形容词} {名词} 包邮",
]

adjectives = ["轻便", "防水", "耐磨", "静音", "高效", "智能", "环保", "时尚", "舒适", "多功能"]
nouns = {
    "户外运动": ["帐篷", "睡袋", "登山杖", "折叠椅", "野餐垫", "头灯", "水壶"],
    "家居日用": ["收纳盒", "台灯", "地毯", "抱枕", "衣架", "垃圾桶", "拖鞋"],
    "数码电子": ["耳机", "充电宝", "数据线", "键盘", "鼠标", "音箱", "支架"],
    "服饰鞋包": ["T恤", "运动鞋", "背包", "帽子", "围巾", "手套", "袜子"],
    "食品饮料": ["坚果", "茶叶", "咖啡", "蜂蜜", "饼干", "巧克力", "果汁"],
}

products = []
for i in range(1, 1001):
    category = random.choice(categories)
    adj = random.choice(adjectives)
    noun = random.choice(nouns[category])
    brand = random.choice(brands)
    template = random.choice(title_templates)
    
    title = template.format(品牌=brand, 类目=category, 形容词=adj, 名词=noun)
    # 加入 5% 的脏数据
    if random.random() < 0.05:
        title = title * (random.randint(5, 10))  # 超长文本
    
    products.append({
        "id": i,
        "title": title,
        "price": round(random.uniform(9.9, 999.9), 2),
        "category": category,
        "in_stock": random.choice([True, True, True, False]),  # 75% 有货
    })

with open("products.json", "w", encoding="utf-8") as f:
    json.dump(products, f, ensure_ascii=False, indent=2)

print(f"✓ 已生成 {len(products)} 条模拟商品数据 → products.json")
```

#### 步骤 2：数据清洗

```python
# step2_clean_data.py
"""数据清洗：去重、截断、去除无效字符"""
import json
import re

with open("products.json", "r", encoding="utf-8") as f:
    products = json.load(f)

total_before = len(products)

# ---------- 清洗 1: 去重（基于标题） ----------
seen_titles = set()
unique_products = []
for p in products:
    if p["title"] not in seen_titles:
        seen_titles.add(p["title"])
        unique_products.append(p)
products = unique_products
dup_removed = total_before - len(products)
print(f"  去重: 移除 {dup_removed} 条重复数据")

# ---------- 清洗 2: 截断超长标题 ----------
MAX_TITLE_LEN = 512
truncated = 0
for p in products:
    if len(p["title"]) > MAX_TITLE_LEN:
        p["title"] = p["title"][:MAX_TITLE_LEN]
        truncated += 1
print(f"  截断: {truncated} 条超长标题限制到 {MAX_TITLE_LEN} 字符")

# ---------- 清洗 3: 移除无效字符 ----------
invalid_pattern = re.compile(r'[^\w\s\u4e00-\u9fff，。！？、；：""''（）—…·\-\+\.\,\!\?\@\#\$\%\^\&\*\(\)\[\]\{\}\|\\\/\<\>]')
cleaned = 0
for p in products:
    old_len = len(p["title"])
    p["title"] = invalid_pattern.sub('', p["title"])
    if len(p["title"]) != old_len:
        cleaned += 1
print(f"  清洗: {cleaned} 条数据移除无效字符")

# ---------- 清洗 4: 过滤空标题 ----------
products = [p for p in products if len(p["title"].strip()) > 0]
empty_removed = total_before - len(products) - dup_removed
print(f"  过滤: 移除 {empty_removed} 条空标题数据")

print(f"\n✓ 数据清洗完成: {total_before} → {len(products)} 条（{total_before - len(products)} 条被移除）")
with open("products_clean.json", "w", encoding="utf-8") as f:
    json.dump(products, f, ensure_ascii=False, indent=2)
```

#### 步骤 3：Embedding 生成 + 批量写入

```python
# step3_embed_and_insert.py
"""生成 Embedding 并批量写入 Milvus，对比不同批量大小的性能"""
import json
import time
import numpy as np
from sentence_transformers import SentenceTransformer
from pymilvus import connections, Collection, utility
from pymilvus import CollectionSchema, FieldSchema, DataType

# ---------- 1. 连接与创建 Collection ----------
connections.connect(host="localhost", port="19530")

COLLECTION_NAME = "product_search_v4"

if utility.has_collection(COLLECTION_NAME):
    utility.drop_collection(COLLECTION_NAME)

fields = [
    FieldSchema(name="product_id", dtype=DataType.INT64, is_primary=True, auto_id=False),
    FieldSchema(name="title_embedding", dtype=DataType.FLOAT_VECTOR, dim=384),  # MiniLM 输出 384 维
    FieldSchema(name="title", dtype=DataType.VARCHAR, max_length=512),
    FieldSchema(name="price", dtype=DataType.FLOAT),
    FieldSchema(name="category", dtype=DataType.VARCHAR, max_length=64),
    FieldSchema(name="in_stock", dtype=DataType.BOOL),
]
schema = CollectionSchema(fields, description="商品语义搜索（第4章实战）")
collection = Collection(COLLECTION_NAME, schema)
print(f"✓ Collection '{COLLECTION_NAME}' 创建完成")

# ---------- 2. 加载 Embedding 模型 ----------
print("正在加载 Embedding 模型...")
model = SentenceTransformer("all-MiniLM-L6-v2")  # 输出 384 维
print(f"✓ 模型加载完成，输出维度: {model.get_sentence_embedding_dimension()}")

# ---------- 3. 读取清洗后的数据 ----------
with open("products_clean.json", "r", encoding="utf-8") as f:
    products = json.load(f)
print(f"✓ 已加载 {len(products)} 条商品数据")

# ---------- 4. 批量生成 Embedding ----------
print("正在生成 Embedding...")
titles = [p["title"] for p in products]
t_start = time.time()
embeddings = model.encode(titles, batch_size=64, show_progress_bar=True)
t_embed = time.time() - t_start
print(f"✓ Embedding 生成完成，耗时: {t_embed:.2f}s（{len(products)/t_embed:.0f} 条/秒）")

# ---------- 5. 对比不同批量大小的写入性能 ----------
batch_sizes = [10, 50, 100, 500, 1000]
print(f"\n{'='*60}")
print("批量写入性能对比")
print(f"{'='*60}")
print(f"{'Batch Size':<12} {'耗时(s)':<10} {'吞吐(条/s)':<12} {'状态'}")
print(f"{'-'*60}")

best_batch = None
best_throughput = 0

for bs in batch_sizes:
    # 清空旧数据
    if collection.num_entities > 0:
        collection.delete("product_id >= 0")
    
    t_start = time.time()
    for i in range(0, len(products), bs):
        batch = products[i:i+bs]
        ids = [p["id"] for p in batch]
        emb_batch = embeddings[i:i+bs].tolist()
        titles_batch = [p["title"] for p in batch]
        prices = [p["price"] for p in batch]
        categories = [p["category"] for p in batch]
        stocks = [p["in_stock"] for p in batch]
        
        try:
            collection.insert([ids, emb_batch, titles_batch, prices, categories, stocks])
        except Exception as e:
            print(f"  Batch {i//bs} 写入失败: {e}")
    
    t_write = time.time() - t_start
    throughput = len(products) / t_write
    status = "✅" if throughput > best_throughput else ""
    if throughput > best_throughput:
        best_throughput = throughput
        best_batch = bs
    
    print(f"{bs:<12} {t_write:<10.2f} {throughput:<12.0f} {status}")

print(f"\n✓ 最佳批量大小: {best_batch}，吞吐量: {best_throughput:.0f} 条/秒")

# ---------- 6. Flush 并验证 ----------
print("\n正在 Flush...")
utility.flush([COLLECTION_NAME])
print(f"✓ Flush 完成，当前 Collection 数据量: {collection.num_entities} 条")
```

**预期输出**：
```
✓ Collection 'product_search_v4' 创建完成
✓ 模型加载完成，输出维度: 384
✓ 已加载 947 条商品数据
正在生成 Embedding...
✓ Embedding 生成完成，耗时: 5.23s（181 条/秒）

批量写入性能对比
Batch Size   耗时(s)     吞吐(条/s)    状态
10           12.34       77
50           3.21        295           ✅
100          2.15        440           ✅
500          1.98        478           ✅
1000         2.05        462

✓ 最佳批量大小: 500，吞吐量: 478 条/秒
✓ Flush 完成，当前 Collection 数据量: 947 条
```

#### 步骤 4：Upsert 幂等性验证

```python
# step4_upsert_demo.py
"""验证 Upsert 的幂等性"""
from pymilvus import Collection
import numpy as np

collection = Collection("product_search_v4")
collection.load() if collection.is_loaded else None

# 第一次 Upsert
result1 = collection.upsert([
    [1, 2, 3],                              # product_id
    [np.random.rand(384).tolist() for _ in range(3)],  # 随机向量
    ["测试商品A", "测试商品B", "测试商品C"],    # title
    [99.9, 199.9, 299.9],                    # price
    ["测试", "测试", "测试"],                  # category
    [True, True, False],                     # in_stock
])

# 第二次 Upsert（相同主键，不同标题）
result2 = collection.upsert([
    [1, 2],                                  # 覆盖 id=1 和 id=2
    [np.random.rand(384).tolist() for _ in range(2)],
    ["测试商品A-更新版", "测试商品B-更新版"],
    [109.9, 209.9],
    ["测试", "测试"],
    [True, True],
])

print(f"Upsert 1: insert_count={result1.insert_count}, upsert_count={result1.upsert_count}")
print(f"Upsert 2: insert_count={result2.insert_count}, upsert_count={result2.upsert_count}")
# Upsert 2 的 upsert_count 应为 2（因为 id=1 和 id=2 已存在）
```

#### 步骤 5：完整写入流程封装

```python
# step5_write_pipeline.py
"""封装完整的「数据清洗 → Embedding → 写入」流水线"""
import json
import time
import re
import numpy as np
from sentence_transformers import SentenceTransformer
from pymilvus import connections, Collection, utility
from pymilvus import CollectionSchema, FieldSchema, DataType

class ProductVectorPipeline:
    """商品向量写入流水线"""
    
    def __init__(self, model_name="all-MiniLM-L6-v2", dim=384):
        self.model = SentenceTransformer(model_name)
        self.dim = dim
        connections.connect(host="localhost", port="19530")
    
    def clean_data(self, data: list) -> list:
        """数据清洗"""
        seen_titles = set()
        cleaned = []
        for item in data:
            # 去重
            if item["title"] in seen_titles:
                continue
            seen_titles.add(item["title"])
            # 截断
            item["title"] = item["title"][:512]
            # 移除无效字符
            item["title"] = re.sub(r'[^\w\s\u4e00-\u9fff，。！？、；]', '', item["title"])
            # 跳过空标题
            if not item["title"].strip():
                continue
            cleaned.append(item)
        print(f"  清洗: {len(data)} → {len(cleaned)} 条")
        return cleaned
    
    def embed(self, titles: list) -> np.ndarray:
        """批量生成 Embedding"""
        t0 = time.time()
        embeddings = self.model.encode(titles, batch_size=64, show_progress_bar=True)
        print(f"  Embedding: {len(titles)} 条, 耗时 {time.time()-t0:.2f}s")
        return embeddings
    
    def create_collection(self, name: str) -> Collection:
        """创建 Collection（如果存在则覆盖）"""
        if utility.has_collection(name):
            utility.drop_collection(name)
        fields = [
            FieldSchema(name="product_id", dtype=DataType.INT64, is_primary=True),
            FieldSchema(name="title_embedding", dtype=DataType.FLOAT_VECTOR, dim=self.dim),
            FieldSchema(name="title", dtype=DataType.VARCHAR, max_length=512),
            FieldSchema(name="price", dtype=DataType.FLOAT),
            FieldSchema(name="category", dtype=DataType.VARCHAR, max_length=64),
            FieldSchema(name="in_stock", dtype=DataType.BOOL),
        ]
        schema = CollectionSchema(fields, description="商品语义搜索")
        return Collection(name, schema)
    
    def insert_batch(self, collection: Collection, data: list, embeddings: np.ndarray, 
                     batch_size: int = 500):
        """批量写入（带错误重试）"""
        total = len(data)
        inserted = 0
        t0 = time.time()
        
        for i in range(0, total, batch_size):
            batch = data[i:i+batch_size]
            emb = embeddings[i:i+batch_size].tolist()
            
            entities = [
                [item["id"] for item in batch],
                emb,
                [item["title"] for item in batch],
                [item["price"] for item in batch],
                [item["category"] for item in batch],
                [item["in_stock"] for item in batch],
            ]
            
            # 重试机制：写入失败重试 3 次
            for attempt in range(3):
                try:
                    collection.insert(entities)
                    inserted += len(batch)
                    break
                except Exception as e:
                    if attempt == 2:
                        print(f"  ⚠ Batch {i//batch_size} 写入失败（已重试3次）: {e}")
                    else:
                        time.sleep(1)
            
            if (i // batch_size + 1) % 10 == 0:
                print(f"  进度: {inserted}/{total} ({inserted*100//total}%)")
        
        utility.flush([collection.name])
        print(f"  ✓ 写入完成: {inserted} 条, 耗时 {time.time()-t0:.2f}s")
    
    def run(self, data_file: str, collection_name: str):
        """执行完整流水线"""
        print("=" * 60)
        print(f"启动写入流水线: {collection_name}")
        print("=" * 60)
        
        # 1. 加载数据
        with open(data_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        print(f"1. 加载数据: {len(data)} 条")
        
        # 2. 清洗
        data = self.clean_data(data)
        
        # 3. Embedding
        titles = [item["title"] for item in data]
        embeddings = self.embed(titles)
        
        # 4. 创建 Collection
        collection = self.create_collection(collection_name)
        print(f"  创建 Collection: {collection.name}")
        
        # 5. 批量写入
        self.insert_batch(collection, data, embeddings, batch_size=500)
        
        print(f"\n✓ 流水线执行完成！Collection: {collection.name}, 数据量: {collection.num_entities}")

# 使用示例
if __name__ == "__main__":
    pipeline = ProductVectorPipeline()
    pipeline.run("products.json", "product_search_v4")
```

---

## 4. 项目总结

### 4.1 优缺点对比

| 维度 | 本地 Embedding 模型 | 云端 Embedding API | 稀疏向量方案 |
|------|-------------------|-------------------|------------|
| 推理速度 | 快（GPU 加速后 500+ 条/s） | 受限 API 速率（10-80条/s） | 与本地模型类似 |
| 成本 | 0 元（含 GPU 服务器折旧） | $0.0001/条起 | 0 元 |
| 语义质量 | 依赖模型选择（BGE>MiniLM） | 高质量（商业模型持续迭代） | 关键词保真度高 |
| 维护成本 | 需管理模型版本和 GPU 环境 | 由云厂商维护 | 同本地 |
| 适用场景 | 大批量、对成本敏感、有 GPU 资源 | 小批量 POC、快速验证 | 需混合检索 |

### 4.2 适用场景

- **商品数据批量导入**：CSV/MySQL → 清洗 → Embedding → Milvus（本章示例）
- **RAG 知识库初始化**：PDF/Word/Markdown → 分块 → Embedding → 写入
- **增量数据同步**：MySQL Binlog → Kafka → Embedding → Upsert 到 Milvus
- **多模态数据导入**：图片/音频/视频 → 特征提取模型 → 写入
- **历史数据迁移**：ES/Redis 中的向量数据 → 批量导出 → Milvus

**不适用场景**：超高频实时单条写入（需用 Kafka 缓冲）、需要事务保证的金融数据。

### 4.3 注意事项

- **批量大小的黄金区间**：100-500 条/Batch。太小（< 10）导致网络 RTT 开销占比大，太大（> 1000）导致 Proxy 内存压力和 gRPC 超时。
- **Flush 时机**：不要在每条 Insert 后 Flush。批量写入完成后调用一次即可。
- **幂等键设计**：如果数据源可能产生重复主键，使用 Upsert 而非 Insert。
- **模型版本管理**：Embedding 模型的版本变化会导致向量含义变化，建议将模型版本记录在 Collection 描述中。

### 4.4 常见踩坑经验

1. **GPU 内存不足导致 Embedding 失败**：SentenceTransformer 默认把所有文本一次性加载到 GPU。解决方案：使用 `batch_size` 参数分批处理。
2. **写入时 Coordinator 超时**：批量过大（> 5000 条）时 Proxy 处理超时。解决方案：控制在 1000 条以内，或者增加 Proxy 的 `timeout` 配置。
3. **编码导致的 Unicode 错误**：商品数据中混入了 \x00 等不可见控制字符。解决方案：在清洗阶段一律过滤，不要期望 Milvus 或 Embedding 模型帮你处理。

### 4.5 思考题

1. 如果数据源有 500 万条数据，写入时部分失败（网络抖动），如何设计一个"断点续传"的写入方案？提示：考虑主键集合的 diff 计算。
2. Embedding 生成和 Milvus 写入是两个独立步骤。如果中间任何一步失败，如何保证不产生"孤儿向量"（Embedding 已生成但未写入）或"孤儿记录"（记录了 ID 但没有对应向量）？

---

> **下一章预告**：第5章我们将为写入的数据构建索引，对比 FLAT 与 HNSW 索引的性能差异。读完本章，你应该能独立完成"从原始数据到向量检索"的全链路。
