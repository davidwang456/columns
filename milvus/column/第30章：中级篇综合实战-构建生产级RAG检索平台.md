# 第30章：【中级篇综合实战】构建生产级 RAG 检索平台

> **定位**：融会贯通中级篇全部知识。
> **版本**：Milvus 2.5.x
> **源码关联**：internal/proxy/、internal/querycoordv2/、internal/querynodev2/、internal/datanode/

---

## 1. 项目背景

某大型企业有 50 个业务部门，每个部门有自己的制度文档、技术规范和知识库。此前各部门各自维护搜索引擎（ES、Solr、甚至静态 HTML），导致三难局面：

1. **搜不准**：纯关键词搜索，用户搜"项目立项审批流程"返回一堆会议纪要。
2. **管不好**：50 套系统各自为政，统一权限管控、数据备份、监控告警全都无从下手。
3. **成本高**：50 套 ES 集群的机器、人力、License 加起来年成本超 200 万。

CTO 决定建设统一的"企业级 RAG 检索中台"——用一套 Milvus 集群服务所有部门，支撑 5000 万条 Chunk、日均 10 万次搜索、P95 延迟 < 300ms。

本章将综合运用中级篇 17-29 章的全部知识：分布式架构 + 写入链路优化 + 搜索链路调优 + 索引选型 + 混合检索 + 多租户隔离 + 一致性控制 + 高可用部署 + 存储治理 + Prometheus 监控 + 压测验证 + RAG 质量评估。

---

## 2. 项目设计（剧本式交锋对话）

**第一幕：中台架构——统一入口 vs 各自为政**

*（架构评审会上，50 个部门的代表吵成一团——有人想保留自己的系统，有人嫌新系统太复杂）*

**小胖**（小声嘀咕）："不就是个搜索吗，搞这么复杂——把 50 套 ES 的数据都同步到 Milvus 不就完了？"

**大师**："小胖你又把问题想简单了。统一不等于简单。统一检索中台要解决四个核心问题——"

**大师**（画出中台全景架构）：

```
┌─────────────────────────────────────────────────────────────┐
│                    统一 API 网关                             │
│         认证鉴权 / 限流 / 路由 / 日志                        │
└───────────┬───────────┬───────────┬─────────────────────────┘
            │           │           │
┌───────────▼──┐ ┌──────▼────┐ ┌───▼───────────┐
│ RAG Service │ │ Search API │ │ Admin Service │
│ 问答生成     │ │ 混合检索    │ │ 文档管理/评估  │
└──────┬───────┘ └─────┬──────┘ └───────┬───────┘
       │               │               │
┌──────▼───────────────▼───────────────▼─────────────────────┐
│                    Milvus Cluster                          │
│  ┌────────┐  ┌──────────┐  ┌──────────┐  ┌─────────────┐  │
│  │Proxy×2 │  │ QCoord   │  │ DCoord   │  │ RCoord      │  │
│  └────────┘  └──────────┘  └──────────┘  └─────────────┘  │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐ │
│  │QueryNode×6   │  │DataNode×4    │  │IndexNode×3      │ │
│  │RG: search_rg │  │              │  │                  │ │
│  └──────────────┘  └──────────────┘  └──────────────────┘ │
│  ┌──────────────┐                                          │
│  │QN: embed_rg │ ← Embedding 专用 QueryNode 隔离          │
│  └──────────────┘                                          │
└────────────────────────────────────────────────────────────┘
       │               │               │
┌──────▼──────┐ ┌──────▼──────┐ ┌──────▼──────┐
│  MinIO/S3   │ │  etcd × 3  │ │ Pulsar/Kafka│
│  对象存储    │ │  元数据     │ │  消息队列    │
└─────────────┘ └─────────────┘ └─────────────┘

配套系统:
┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐
│Prometheus│ │ Grafana  │ │  Attu    │ │  Backup  │
│ 指标采集  │ │ 可视化   │ │ 管理工具  │ │ 备份恢复  │
└──────────┘ └──────────┘ └──────────┘ └──────────┘
```

| 模块 | 技术选型 | 关键设计 |
|------|---------|---------|
| Milvus | Cluster 模式 | Resource Group 隔离不同业务线 |
| 多租户 | Partition Key (tenant_id) | 64 个物理分区 |
| 索引 | HNSW (M=16, ef=200) | 统一参数，热数据用 HNSW、冷数据用 IVF_SQ8 |
| 搜索 | Hybrid Search | Dense(BGE-M3) + Sparse(BM25), WeightedRanker(0.6, 0.4) |
| 一致性 | Bounded (5s) | 文档更新场景容忍 5s 延迟 |
| 高可用 | Replica=2 | 2 副本保证 QueryNode 故障不中断 |
| 监控 | Prometheus + Grafana | 5 条核心告警 |
| 备份 | milvus-backup | 每日全量、每小时增量 |

> **技术映射**：检索中台 = 公司统一邮件系统（之前各部门自己搭建邮件服务器，现在统一成一个）；Resource Group = 部门专用通道（人多的部门通道宽一些）；Partition Key = 部门编号（邮件自动归到对应部门）。

---

**第二幕：数据管道——50 个部门的文档怎么统一**

**小白**："50 个部门的文档格式五花八门（PDF、Word、Wiki、Confluence），怎么统一处理？"

**大师**："设计一个标准化文档接入流水线——"

```
文档接入流水线 (一个部门入一个 Topic):

Step 1: 文档采集
  各部门通过统一 SDK 或 Webhook 推送文档
  格式: {"dept": "HR", "title": "...", "content": "...", "metadata": {...}}
  → Kafka Topic: "doc-ingest-hr", "doc-ingest-finance", ...

Step 2: 文档解析 + 分块
  Ingestion Worker:
    ├─ PDF → PyPDF2 / pdfplumber
    ├─ Word → python-docx
    ├─ Wiki → 各自 API
    └─ 统一分块: 512 tokens + 128 overlap

Step 3: 双向量生成 + 写入
  BGE-M3 → dense_vec (1024维) + sparse_vec
  → Milvus Upsert (tenant_id=dept_name, partition key 自动路由)

Step 4: 索引触发
  新 Chunk 写入后自动触发 Flush
  增量 Compaction 由 DataCoord 自动管理
```

**大师**："关键设计——50 个部门共用一个 Collection，通过 `tenant_id`（Partition Key）隔离。搜索时 `tenant_id == 'HR'` 自动路由到对应分区。"

---

**第三幕：验收标准与上线计划**

**大师**："中台系统的三个验收标准——"

| 指标 | 目标 | 验收方式 |
|------|------|---------|
| 搜索 P95 延迟 | < 300ms（1000 万 Chunk 内） | 压测报告 |
| 核心服务可用性 | ≥ 99.9%（月） | Prometheus uptime |
| 召回准确率 | 持续可评估（Recall@10 ≥ 95%） | 评估集 + 月度报告 |
| 故障恢复时间 | < 5 分钟（有 Replica） | 故障演练 |

**大师**："上线计划三步走——"

```
Phase 1: 灰度 (1-2 周)
  接入 3 个部门（HR、财务、技术部）
  数据量 < 100 万 Chunk
  只开放搜索 API，不开放问答

Phase 2: 扩展 (3-4 周)
  接入剩余 47 个部门
  数据量增长到 1000 万 Chunk
  开放 RAG 问答 API
  持续监控 + 调优

Phase 3: 稳定运营 (持续)
  建立月度质量评估
  新增文档自动接入
  按需扩容 QueryNode/DataNode
```

---

## 3. 项目实战

### 3.1 实战目标

交付一套可在测试环境运行的 RAG 检索平台核心代码，并输出架构设计文档。

### 3.2 分步实现

#### 步骤 1：中台 Collection Schema

```python
# step1_platform_schema.py
"""RAG 检索中台 Collection Schema"""
from pymilvus import connections, Collection, utility
from pymilvus import CollectionSchema, FieldSchema, DataType

connections.connect(host="localhost", port="19530")

COLL_NAME = "rag_platform_v1"
if utility.has_collection(COLL_NAME):
    utility.drop_collection(COLL_NAME)

fields = [
    FieldSchema(name="id", dtype=DataType.INT64, is_primary=True, auto_id=True),
    FieldSchema(name="tenant_id", dtype=DataType.VARCHAR, max_length=64,
                is_partition_key=True),
    FieldSchema(name="chunk_text", dtype=DataType.VARCHAR, max_length=2048),
    FieldSchema(name="dense_vec", dtype=DataType.FLOAT_VECTOR, dim=1024),
    FieldSchema(name="sparse_vec", dtype=DataType.SPARSE_FLOAT_VECTOR),
    FieldSchema(name="doc_id", dtype=DataType.VARCHAR, max_length=256),
    FieldSchema(name="doc_title", dtype=DataType.VARCHAR, max_length=512),
    FieldSchema(name="doc_version", dtype=DataType.INT64),
    FieldSchema(name="permission_level", dtype=DataType.INT64),  # 0=公开 1=部门内 2=机密
    FieldSchema(name="created_at", dtype=DataType.INT64),
    FieldSchema(name="updated_at", dtype=DataType.INT64),
    FieldSchema(name="source", dtype=DataType.VARCHAR, max_length=128),  # PDF/Wiki/Word
]

schema = CollectionSchema(
    fields,
    description="企业级 RAG 检索中台",
    num_partitions=128,  # 50 部门 → 128 个物理分区
)
collection = Collection(COLL_NAME, schema)

# 双索引
collection.create_index("dense_vec", {
    "index_type": "HNSW", "metric_type": "COSINE",
    "params": {"M": 16, "efConstruction": 200}
})
collection.create_index("sparse_vec", {
    "index_type": "SPARSE_WAND", "metric_type": "IP"
})
utility.wait_for_index_building_complete(COLL_NAME, timeout=600)
collection.load(replica_number=2)
print(f"检索中台 Collection '{COLL_NAME}' 就绪, Replica=2")
```

#### 步骤 2：统一搜索 API

```python
# step2_platform_search.py
"""检索中台统一搜索 API"""
from pymilvus import Collection, AnnSearchRequest, WeightedRanker
from FlagEmbedding import BGEM3FlagModel

class RAGPlatformSearch:
    """企业级 RAG 检索中台搜索服务"""
    
    def __init__(self, collection_name: str):
        self.collection = Collection(collection_name)
        self.model = BGEM3FlagModel("BAAI/bge-m3", use_fp16=True)
    
    def search(self, query: str, tenant_id: str,
               top_k: int = 10, permission_level: int = 0,
               doc_version_gte: int = None) -> dict:
        """统一搜索接口
        
        Args:
            query: 搜索文本
            tenant_id: 租户 ID（Partition Key 自动路由）
            top_k: 返回结果数
            permission_level: 用户权限级别（过滤机密文档）
            doc_version_gte: 最低文档版本（过滤旧版）
        """
        # 1. 生成双向量
        emb = self.model.encode([query], return_dense=True, return_sparse=True)
        q_dense = emb["dense_vecs"][0].tolist()
        q_sparse = emb["lexical_weights"][0]
        
        # 2. 构造过滤表达式
        conditions = [f'tenant_id == "{tenant_id}"']
        if permission_level is not None:
            conditions.append(f"permission_level <= {permission_level}")
        if doc_version_gte is not None:
            conditions.append(f"doc_version >= {doc_version_gte}")
        expr = " and ".join(conditions)
        
        # 3. Hybrid Search
        dense_req = AnnSearchRequest(
            data=[q_dense], anns_field="dense_vec",
            param={"metric_type": "COSINE", "params": {"ef": 64}},
            limit=top_k * 3
        )
        sparse_req = AnnSearchRequest(
            data=[q_sparse], anns_field="sparse_vec",
            param={"metric_type": "IP"}, limit=top_k * 3
        )
        
        results = self.collection.hybrid_search(
            reqs=[dense_req, sparse_req],
            rerank=WeightedRanker(0.6, 0.4),
            limit=top_k,
            expr=expr,
            output_fields=["chunk_text", "doc_title", "doc_id",
                          "permission_level", "source", "updated_at"]
        )
        
        # 4. 格式化
        items = []
        for hit in results[0]:
            items.append({
                "chunk_id": hit.id,
                "text": hit.entity.get("chunk_text"),
                "doc_title": hit.entity.get("doc_title"),
                "doc_id": hit.entity.get("doc_id"),
                "score": round(hit.distance, 4),
                "source": hit.entity.get("source"),
            })
        
        return {"results": items, "total": len(items), "tenant": tenant_id}

# 使用
platform = RAGPlatformSearch("rag_platform_v1")
r = platform.search("产假申请流程", tenant_id="HR", permission_level=0)
print(f"HR 搜索: {r['total']} 条结果")
for item in r["results"][:3]:
    print(f"  [{item['doc_title']}] {item['text'][:60]}... ({item['score']:.3f})")
```

#### 步骤 3：平台健康检查

```python
# step3_platform_health.py
"""检索中台健康检查"""
from pymilvus import connections, utility

def platform_health_report():
    """中台健康检查报告"""
    connections.connect(host="localhost", port="19530")
    report = {}
    
    # 存储
    storage = utility.get_storage_info()
    report["storage"] = {
        "used_gb": round(storage.used / 1024**3, 1),
        "total_gb": round(storage.total / 1024**3, 1),
    }
    
    # Collection 状态
    for cname in utility.list_collections():
        state = utility.load_state(cname)
        report[cname] = {
            "entities": Collection(cname).num_entities,
            "load_state": state.name,
            "index_built": Collection(cname).has_index(),
        }
    
    return report

report = platform_health_report()
for k, v in report.items():
    print(f"{k}: {v}")
```

#### 步骤 4：架构设计文档模板

```markdown
# 企业级 RAG 检索中台 - 架构设计

## 1. 系统概览
- 目标: 统一 50 个部门的文档检索
- Milvus: Cluster 模式, K8s 部署
- 数据量: 预估 5000 万 Chunk
- QPS: 日均 10 万次, 峰值 500 QPS

## 2. 核心设计决策
| 决策 | 选择 | 理由 |
|------|------|------|
| 多租户隔离 | Partition Key | 50 部门 < 4096 上限, 自动路由 |
| 索引 | HNSW (M=16) | 高召回+低延迟, 内存预算允许 |
| 检索 | Hybrid (Dense+Sparse) | 语义 + 关键词互补 |
| 高可用 | Replica=2 | 内存成本 2x, 换 0 秒故障恢复 |
| 一致性 | Bounded (5s) | 文档更新场景容忍短延迟 |

## 3. 资源规划
- QueryNode: 6 台 × 32GB
- DataNode: 4 台 × 16GB
- IndexNode: 3 台 × 16GB
- Proxy: 2 台 × 4GB
- 对象存储: 2TB (MinIO)
- etcd: 3 节点 × 20GB SSD

## 4. 监控告警
- P95 搜索延迟 > 300ms → Warning
- QueryNode 内存 > 85% → Warning
- 组件宕机 > 2min → Critical
- 存储使用率 > 90% → Critical
```

---

## 4. 项目总结

### 4.1 中级篇知识映射到中台系统

| 中级篇章节 | 对应中台模块 | 具体应用 |
|-----------|------------|---------|
| 第17章 分布式架构 | 整体架构 | Cluster 模式 + K8s 部署 |
| 第18章 写入链路 | 数据管道 | 文档 → Kafka → Embedding → Milvus |
| 第19章 搜索链路 | 搜索服务 | Proxy → QueryCoord → QueryNode → 归并 |
| 第20章 索引调优 | 索引选型 | HNSW M=16, ef=200 |
| 第21章 混合检索 | 搜索 API | Dense + Sparse Hybrid Search |
| 第22章 多租户 | 权限隔离 | Partition Key + permission_level |
| 第23章 一致性 | 可见性控制 | Bounded 5s |
| 第24章 高可用 | 容灾 | Replica=2 + Resource Group |
| 第25章 存储治理 | 运维 | Compaction + GC |
| 第26章 可观测性 | 监控 | Prometheus + 5 条告警 |
| 第27章 压测 | 验收 | 1000 万 Chunk P95 < 300ms |
| 第28章 K8s 部署 | 部署 | Helm Chart + 灰度升级 |
| 第29章 RAG 治理 | 质量 | Recall@10 + MRR 持续评估 |

### 4.2 验收通过标准

```
□ K8s 部署 Milvus Cluster（12+ Pod 全部 Running）
□ Partition Key 多租户隔离（50 个租户搜索相互不干扰）
□ Hybrid Search 召回率 Recall@10 ≥ 95%
□ 搜索 P95 < 300ms（1000 万 Chunk 压测验证）
□ Prometheus + Grafana 监控就绪（5 条告警已配置）
□ 备份恢复演练通过
□ 灰度升级流程已验证
```

### 4.3 思考题

1. 如果业务部门增长到 500 个（超过 Partition 4096 上限），架构如何升级？
2. 中台系统如何做成本核算——每个部门按照什么维度计费（搜索次数、占用存储、占用内存）？

---

> **中级篇完结**。第31章起进入高级篇——源码级理解 Milvus 的核心实现。读完本章，你应该能交付一套生产级 RAG 检索中台的完整方案。
