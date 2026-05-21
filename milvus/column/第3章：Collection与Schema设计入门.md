# 第3章：Collection 与 Schema 设计入门

> **定位**：理解 Milvus 中数据建模的第一步。
> **版本**：Milvus 2.5.x
> **源码关联**：internal/proxy/create_collection.go、internal/rootcoord/create_collection.go、pkg/v2/proto/schema.pb.go

---

## 1. 项目背景

某电商平台商品库已有 500 万条商品记录存储在 MySQL 中，业务方希望引入向量搜索支持"口语化找商品"功能。数据架构师李明被要求设计 Milvus 的 Collection Schema，将商品数据从关系型数据库同步到向量数据库。

李明一开始觉得这就是"建表"——把 MySQL 的表结构照搬到 Milvus 就行了。他把 23 个字段（商品 ID、标题、描述、价格、类目、品牌、库存、销量、评分、图片 URL 列表、上架时间、修改时间……）全部定义进 Schema，然后尝试创建 Collection。

问题很快暴露出来：

1. **向量维度不知道怎么定**——团队选了三个 Embedding 模型做对比（768 维、1024 维、1536 维），Schema 创建后维度不能改，选错就得删库重建。
2. **Primary Key 设计陷阱**——李明用了 MySQL 的自增 int64 做主键，但商品数据是从 MySQL 同步过来的，已有自己的 ID 体系，导致写入时主键冲突。
3. **Metric Type 选错**——他随便选了个 L2 距离，后来发现商品语义相似度行业通用的是 COSINE，由于索引已经构建完毕，切换代价极大。
4. **动态字段滥用**——为了让 Schema 灵活，启用了 Dynamic Field，结果搜索时无法做标量过滤（因为类型推断不稳定）。

团队花了一周回滚、重建、重新设计 Schema。本章的目标是：让你在创建第一个 Collection 之前，先理解 Schema 设计的核心约束和最佳实践，避免"一次建错、全盘重建"的惨痛教训。

---

## 2. 项目设计（剧本式交锋对话）

**第一幕：Collection、Field、Primary Key 的关系**

*（会议室白板上画满了 MySQL 表结构，小胖正试图往 Milvus Schema 里塞 23 个字段）*

**小胖**（烦躁地）："大师，我就想建个表存商品，为什么 Milvus 搞得这么复杂？Collection、Schema、Field、Primary Key——不就是 CREATE TABLE 那一套吗？我直接把 MySQL 的 DDL 搬过来行不行？"

**大师**："行是行，但你会后悔的。Milvus 的 Collection 虽然看起来像数据库的 Table，但它的设计哲学完全不同。传统数据库是'行式存储、宽表设计'，恨不得把所有字段都放进一张表；Milvus 是'列式存储、向量优先'，你要仔细想清楚哪些字段是搜索必需的，哪些可以在业务层处理。"

**大师**（画了个对比图）：

```
MySQL 思路（行式存储）：           Milvus 思路（列式存储 + 向量优先）：
┌────┬──────┬─────┬────┬──────────┐    ┌────────────────────────────────┐
│ id │ name │price│... │(无向量)  │    │ Field: id (PK)                 │
├────┼──────┼─────┼────┼──────────┤    │ Field: title (VARCHAR)    ←──  │
│ 1  │ 椅子 │ 199 │... │          │    │ Field: price (FLOAT)            │
│ 2  │ 桌子 │ 599 │... │          │    │ Field: category (VARCHAR)       │
└────┴──────┴─────┴────┴──────────┘    │ Field: title_vec (FLOAT_VECTOR) │← 核心！
                                        │ Field: image_vec (FLOAT_VECTOR) │
                                        └────────────────────────────────┘
```

**小白**："等一下——你说 Milvus 是列式存储？那和 ClickHouse 那种列存数据库有什么区别？"

**大师**："区别在于，Milvus 的列式存储是'分段列存'。数据先按 Partitions 分区，再按 Segments 分片，最后每个 Segment 内部按列存储。这样搜索时只需要加载向量列和少量标量列，而不是把整行 23 个字段全部加载到 QueryNode 内存中。但代价是，Schema 一旦创建，向量字段的维度就不能改了。"

**小胖**："所以我的 23 个字段只能挑几个关键的存进 Milvus？其他字段怎么办？"

**大师**："分两步走。第一步，在 Milvus 中只存搜索必需的字段——主键、向量、过滤条件用的标量字段（价格、类目、状态）。第二步，搜索结果拿到主键列表后，去 MySQL 或 Redis 里捞完整信息。这叫'检索分离'——Milvus 负责'找到哪些 ID'，业务库负责'这些 ID 的详情'。"

> **技术映射**：Collection = 向量数据的容器（不是宽表的替代品）；Schema = 字段定义集合（不可逆操作需谨慎）；Vector Field = 核心字段（维度不可变）；Scalar Field = 辅助字段（仅存搜索过滤条件）。

---

**第二幕：动态字段、AutoID、Nullable、Default Value 的使用边界**

**小白**："大师，你说的那几个高级特性——Dynamic Field、AutoID、Nullable、Default Value——我看了文档但不知道什么时候该用，什么时候不该用。能不能给个决策树？"

**大师**（一边在白板上画一边说）：

```
                    ┌─ 主键来源是什么？
                    │
         ┌──────────┴──────────┐
         │ 业务已有 ID（如商品 SKU）│       │ 无 ID，需要自动生成     │
         └──────────┬──────────┘       └──────────┬──────────┘
                    │                             │
         auto_id=False                    auto_id=True
         用业务 SKU 做主键                  Milvus 自动分配 int64
         （务必确保唯一性！）                （适合日志、事件等无自然主键场景）

                     ┌─ 需要灵活添加字段吗？
                     │
          ┌──────────┴──────────┐
          │ 是，字段经常变        │       │ 否，字段固定不变         │
          └──────────┬──────────┘       └──────────┬──────────┘
                     │                             │
          enable_dynamic_field=True      enable_dynamic_field=False
          灵活性高，但：                      性能好，类型安全，但：
          - 无法为动态字段建索引           - 新增字段需重建 Collection
          - 类型推断可能出错               - 适合生产环境稳定场景
          - 适合 POC 和快速实验
```

**小胖**："那 Nullable 呢？我有些商品可能没有价格（比如新品预发布），需要存 NULL 吗？"

**大师**："尽量不要用 Nullable。Milvus 的标量过滤是基于索引的，NULL 值会导致两个问题：第一，索引中处理 NULL 的开销比普通值大；第二，过滤表达式需要额外写 `field IS NULL` 或 `field IS NOT NULL`，团队容易忘记。更好的做法是给个约定值，比如价格未知时用 -1，然后过滤条件写 `price > 0`。"

**小白**："Default Value 呢？"

**大师**："Default Value 的典型场景是'数据迁移时的字段补齐'。比如现有的 500 万商品没有'来源渠道'字段，新 Schema 想加这个字段——如果设置了 Default Value='unknown'，旧数据写入时就不用额外处理。但要注意，Default Value 只能给标量字段设置，向量字段不支持。"

> **技术映射**：AutoID = 自增主键（适用于无业务主键场景）；Dynamic Field = 灵活字段（牺牲性能换便利）；Nullable = 允许空值（能用默认值替代就不用 NULL）；Default Value = 字段的兜底值（数据迁移利器）。

---

**第三幕：向量维度与 Metric Type 的业务含义**

**小白**（翻着 Embedding 模型文档）："大师，我们团队选了三个 Embedding 模型——BGE-Large（1024维）、text-embedding-ada-002（1536维）、all-MiniLM-L6-v2（384维）。Schema 里的向量维度到底选哪个？'

**大师**："这个问题的答案不在 Milvus，而在你的 Embedding 模型。关键原则是：**Schema 中的 dim 必须与 Embedding 模型的输出维度精确一致**。如果模型输出 1024 维，Schema 定义了 768 维，写入时直接报错；反过来 768 维的向量写入 1024 维的字段也会失败。所以，在创建 Collection 之前，必须先确定 Embedding 模型。"

**小白**："那我们选模型的时候应该考虑什么？"

**大师**："三个维度：精度、速度、成本。"

| 模型                     | 维度   | 优势            | 劣势         | 适用场景         |
| ---------------------- | ---- | ------------- | ---------- | ------------ |
| all-MiniLM-L6-v2       | 384  | 快、内存占用小、本地可部署 | 语义精度一般     | 快速 POC、模糊搜索  |
| BGE-Large              | 1024 | 中文语义优秀、召回率高   | 计算量大、内存需求高 | 中文语义搜索、RAG   |
| text-embedding-ada-002 | 1536 | 通用语义覆盖广       | 云端调用有延迟和成本 | 多语言场景、无需本地部署 |

**大师**："如果你确定了用 BGE-Large（1024维），那 Schema 就这样写："

```python
FieldSchema(name="embedding", dtype=DataType.FLOAT_VECTOR, dim=1024)
```

**小胖**："那 Metric Type 呢？L2、IP、COSINE 这三个有什么区别？"

**大师**："这个问题问得好。很多新手乱选 Metric Type，导致搜索结果完全不符合预期。"

**大师**（在白板上画坐标系）：

```
        L2 距离（欧几里得距离）          COSINE 相似度（余弦相似度）       IP 内积（Inner Product）

        两点之间的直线距离              两个向量之间的夹角余弦值          向量点积
        d = √((x₁-y₁)²+...+(xₙ-yₙ)²)  cos(θ) = A·B/(|A|×|B|)         A·B

        ↓ 越小越相似                   ↓ 越接近1越相似                 ↓ 越大越相似

        向量值的大小影响结果            只关心方向，不关心长度            既关心方向又关心长度
        适合图像特征向量               适合文本语义向量                 适合模型专门训练的向量
```

**大师**："选择 Metric Type 的黄金法则——"

| Metric Type | 选择条件                               | 典型场景               |
| ----------- | ---------------------------------- | ------------------ |
| COSINE      | Embedding 模型经过 L2 归一化，或者你不太确定该选什么  | 文本语义搜索（最通用，推荐默认选项） |
| L2          | 原始特征向量（如图像像素、MFCC 音频特征），向量大小有实际意义  | 图像相似检索、音频指纹匹配      |
| IP          | Embedding 模型专门训练用于最大化内积（如推荐系统双塔模型） | 推荐系统召回、广告检索        |

**小白**："那我用 BGE-Large 做文本语义搜索，就用 COSINE？"

**大师**："对！BGE 系列模型的输出已经做了归一化，COSINE 是最佳选择。而且记住，**建索引和搜索时的 Metric Type 必须一致**——如果你用 COSINE 建了索引，然后用 L2 搜，结果是不可信的。"

> **技术映射**：Vector Dim = Embedding 模型的输出维度（不可变的绑定关系）；Metric Type = 判断"相似"的数学标准（需与模型输出匹配）；COSINE = 文本语义的默认选项；L2 = 图像特征的优先选择。

---

## 3. 项目实战

### 3.1 实战目标

为一个商品语义搜索系统设计 Collection Schema，并用 Python SDK 创建集合，验证 Schema 的正确性。

### 3.2 环境准备

```bash
# 确保 Milvus Standalone 已启动（见第2章）
docker compose ps

# 安装依赖
pip install pymilvus==2.5.5 sentence-transformers
```

### 3.3 分步实现

#### 步骤 1：定义业务模型

先梳理清楚"什么数据存 Milvus，什么数据存 MySQL"：

| 数据列                   | 存放位置              | 原因                       |
| --------------------- | ----------------- | ------------------------ |
| 商品 ID（业务主键）           | Milvus（PK）+ MySQL | Milvus 搜索返回 ID，MySQL 查详情 |
| 商品标题                  | Milvus（标量）+ MySQL | Milvus 中存一份用于搜索结果的直接展示   |
| 标题向量 (1024维)          | Milvus（向量字段）      | 核心检索字段                   |
| 价格                    | Milvus（标量）        | 搜索过滤条件：价格区间              |
| 商品类目                  | Milvus（标量）        | 搜索过滤条件：限定类目              |
| 库存状态                  | Milvus（标量）        | 搜索过滤条件：仅搜有货商品            |
| 商品描述、图片 URL、SKU、标签... | 仅 MySQL           | 搜索不需要这些字段参与              |

#### 步骤 2：创建 Collection Schema

```python
# create_collection.py
"""为商品语义搜索系统设计并创建 Collection"""
from pymilvus import connections, Collection, utility
from pymilvus import CollectionSchema, FieldSchema, DataType

# ---------- 1. 连接 Milvus ----------
connections.connect(host="localhost", port="19530")
print(f"✓ 已连接 Milvus v{utility.get_server_version()}")

# ---------- 2. 定义 Schema ----------
# 字段设计原则：
#   - 只存搜索必需的字段（检索分离）
#   - 向量字段维度 = Embedding 模型输出维度（这里用 BGE-Large: 1024维）
#   - 标量字段仅存过滤条件和展示用的核心字段

fields = [
    # 主键：使用商品的业务 ID（MySQL 中的自增 ID）
    FieldSchema(
        name="product_id",
        dtype=DataType.INT64,
        is_primary=True,
        auto_id=False,          # 不使用自动生成，用 MySQL 的 ID
        description="商品唯一标识（来自 MySQL 主键）"
    ),
    # 向量字段：商品标题的 Embedding（BGE-Large 模型输出 1024 维）
    FieldSchema(
        name="title_embedding",
        dtype=DataType.FLOAT_VECTOR,
        dim=1024,               # 务必与 Embedding 模型一致！
        description="商品标题的语义向量"
    ),
    # 标量字段：商品标题（搜索结果直接展示用）
    FieldSchema(
        name="title",
        dtype=DataType.VARCHAR,
        max_length=512,
        description="商品标题"
    ),
    # 标量字段：价格（做价格区间过滤）
    FieldSchema(
        name="price",
        dtype=DataType.FLOAT,
        description="商品售价"
    ),
    # 标量字段：商品类目（限定搜索范围）
    FieldSchema(
        name="category",
        dtype=DataType.VARCHAR,
        max_length=64,
        description="商品所属类目，如'户外运动'、'家居日用'"
    ),
    # 标量字段：库存状态（仅搜索有货商品）
    FieldSchema(
        name="in_stock",
        dtype=DataType.BOOL,
        description="是否在售（True=有货，False=下架）"
    ),
]

# ---------- 3. 创建 Schema 对象 ----------
schema = CollectionSchema(
    fields=fields,
    description="商品语义搜索集合 — 支持标题向量检索 + 价格/类目/库存过滤",
    enable_dynamic_field=False,     # 生产环境尽量关闭动态字段
)

# ---------- 4. 创建 Collection ----------
collection_name = "product_search"

# 先判断是否已存在，存在则删除（开发环境用，生产慎用）
if utility.has_collection(collection_name):
    utility.drop_collection(collection_name)
    print(f"⚠ 已删除旧 Collection: {collection_name}")

collection = Collection(name=collection_name, schema=schema)
print(f"✓ Collection '{collection_name}' 创建成功")

# ---------- 5. 验证 Schema ----------
print(f"\n{'='*60}")
print(f"Collection 信息")
print(f"{'='*60}")
print(f"名称: {collection.name}")
print(f"描述: {collection.description}")
print(f"字段数量: {len(collection.schema.fields)}")
print(f"是否启用动态字段: {collection.schema.enable_dynamic_field}")
print(f"\n字段列表:")
for f in collection.schema.fields:
    dim_info = f" dim={f.params['dim']}" if f.dtype == DataType.FLOAT_VECTOR else ""
    pk_info = " [主键]" if f.is_primary else ""
    auto_info = " [auto_id]" if f.auto_id else ""
    print(f"  • {f.name}: {f.dtype.name}{dim_info}{pk_info}{auto_info} — {f.description}")
```

**预期输出**：

```
✓ 已连接 Milvus v2.5.5
✓ Collection 'product_search' 创建成功

============================================================
Collection 信息
============================================================
名称: product_search
描述: 商品语义搜索集合 — 支持标题向量检索 + 价格/类目/库存过滤
字段数量: 6
是否启用动态字段: False

字段列表:
  • product_id: INT64 [主键] — 商品唯一标识（来自 MySQL 主键）
  • title_embedding: FLOAT_VECTOR dim=1024 — 商品标题的语义向量
  • title: VARCHAR — 商品标题
  • price: FLOAT — 商品售价
  • category: VARCHAR — 商品所属类目，如'户外运动'、'家居日用'
  • in_stock: BOOL — 是否在售（True=有货，False=下架）
```

#### 步骤 3：Schema 设计自查清单

在代码中加一个自查函数，帮助团队验证 Schema 是否合理：

```python
# schema_checklist.py
"""Schema 设计自查清单"""

def validate_schema(collection):
    """验证 Schema 设计是否符合最佳实践"""
    issues = []
    schema = collection.schema

    # 检查 1：必须有且只有一个主键
    pk_fields = [f for f in schema.fields if f.is_primary]
    if len(pk_fields) != 1:
        issues.append(f"❌ 主键数量错误：期望 1 个，实际 {len(pk_fields)} 个")
    else:
        issues.append(f"✓ 主键: {pk_fields[0].name} ({pk_fields[0].dtype.name})")

    # 检查 2：必须有至少一个向量字段
    vec_fields = [f for f in schema.fields if f.dtype in (DataType.FLOAT_VECTOR, DataType.BINARY_VECTOR)]
    if len(vec_fields) == 0:
        issues.append("❌ 缺少向量字段！Collection 必须包含至少一个向量字段")
    else:
        for vf in vec_fields:
            issues.append(f"✓ 向量字段: {vf.name} (dim={vf.params.get('dim', 'N/A')})")

    # 检查 3：生产环境应关闭动态字段
    if schema.enable_dynamic_field:
        issues.append("⚠ Dynamic Field 已启用，生产环境建议关闭以避免类型推断问题")
    else:
        issues.append("✓ Dynamic Field 已关闭")

    # 检查 4：不要有太多标量字段（建议 ≤ 10）
    scalar_count = len(schema.fields) - len(vec_fields) - len(pk_fields)
    if scalar_count > 10:
        issues.append(f"⚠ 标量字段过多 ({scalar_count})，建议将非过滤/非展示字段移至业务数据库")
    else:
        issues.append(f"✓ 标量字段数量: {scalar_count}（适中）")

    return issues

# 用法
collection = Collection("product_search")
for check in validate_schema(collection):
    print(check)

# 预期输出:
# ✓ 主键: product_id (INT64)
# ✓ 向量字段: title_embedding (dim=1024)
# ✓ Dynamic Field 已关闭
# ✓ 标量字段数量: 4（适中）
```

---

## 4. 项目总结

### 4.1 优缺点对比

| 维度         | Milvus Collection        | MySQL Table      | Elasticsearch Index  |
| ---------- | ------------------------ | ---------------- | -------------------- |
| 向量存储       | 原生支持，高性能 ANN 检索          | 不原生支持向量检索        | 通过 dense_vector 扩展支持 |
| 列式存储       | 原生列存，加载效率高               | 行式存储             | 倒排索引+列存              |
| Schema 灵活性 | 向量字段维度不可变，标量字段可设 default | ALTER TABLE 灵活变更 | Dynamic Mapping 非常灵活 |
| 动态字段       | 支持，但有类型推断风险              | 不支持              | Mapping 动态推断（更成熟）    |
| 检索能力       | 专用向量检索                   | 精确查询、JOIN        | 全文搜索+聚合分析            |

### 4.2 适用场景

- **商品语义搜索**：标题向量 + 价格/类目标量过滤（本章示例）
- **RAG 知识库**：文档 Chunk 向量 + 文档 ID/来源/时间过滤
- **图片相似检索**：图片特征向量 + 标签/上传时间过滤
- **用户行为推荐**：用户 Embedding + 用户属性分段过滤
- **风控相似样本**：交易向量 + 时间窗口/金额范围过滤

**不适用场景**：需要复杂 JOIN 的多表关联查询（Milvus 不支持跨 Collection JOIN）、需要全文分词的场景（应用 ES，Milvus 仅做向量召回）。

### 4.3 注意事项

- **向量维度不可逆**：创建 Collection 后 dim 无法修改。必须在创建前确定 Embedding 模型。
- **主键唯一性**：如果用业务 ID 做主键且 `auto_id=False`，写入时主键重复会报错（Upsert 可以覆盖）。
- **Metric Type 一致性**：建索引和搜索时的 Metric Type 必须一致，否则距离分值语义不准确。
- **Dynamic Field 慎用**：生产环境建议关闭。如需灵活字段，考虑在业务层处理元数据。
- **字段数量控制**：Milvus 不是"宽表数据库"，标量字段尽量控制在 10 个以内。

### 4.4 常见踩坑经验

1. **维度不匹配导致写入失败**：团队切换了 Embedding 模型但忘记更新 Schema。解决方案：在 CI 中加一个测试，启动时验证 Schema dim 与 Embedding 模型输出一致。
2. **auto_id=True 但用了业务 ID 写入**：Milvus 自动生成了主键，导致业务 ID 丢失映射关系。解决方案：先明确主键策略，不要混用。
3. **Dynamic Field 中存储了日期字符串**：Attu 展示正常，但过滤表达式 `field > '2024-01-01'` 作为字符串比较而非日期比较，结果错误。解决方案：日期字段显式定义为 VARCHAR 或 INT64（存时间戳）。

### 4.5 思考题

1. 如果一个 Collection 需要同时支持"商品标题语义搜索"和"商品图片以图搜图"，Title Embedding 是 1024 维，Image Embedding 是 512 维，应该创建一个 Collection 还是两个 Collection？为什么？
2. Schema 中 `enable_dynamic_field=True` 与 `enable_dynamic_field=False` 在搜索时的性能差异有多大？提示：考虑索引构建和内存开销。

### 4.6 推广计划提示

- **开发团队**：将 Schema 设计自查清单纳入 Code Review 流程，每个新 Collection 必须通过检查。
- **算法团队**：在确定 Embedding 模型后，将模型名称和输出维度写入团队 Wiki，与 Schema 定义同步更新。
- **测试团队**：编写 Schema 合规性测试，验证向量字段维度、Metric Type、主键策略是否符合设计文档。

---

> **下一章预告**：第4章我们将学习 Embedding 生成与数据批量写入，把真实的商品文本变成可检索的向量数据。读完本章，你应该能独立设计一个生产可用的 Collection Schema。
