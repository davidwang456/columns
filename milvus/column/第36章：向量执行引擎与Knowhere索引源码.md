# 第36章：向量执行引擎与 Knowhere 索引源码

> **定位**：理解 Milvus 高性能检索的核心计算层。
> **版本**：Milvus 2.5.x
> **源码关联**：internal/core/src/index/hnsw/、internal/core/src/index/ivf/、internal/core/src/index/Knowhere.h

---

## 1. 项目背景

核心开发小陈被分配了一个极致性能优化任务：将 HNSW 搜索延迟从 3ms 降到 1.5ms。他先在 Go 层做优化——减少 `searchSegment` 的锁竞争、优化结果合并算法——但延迟纹丝不动。他用 pprof 做 CPU 分析才发现：99% 的搜索时间花在 CGO 调用上，Go 层的优化是隔靴搔痒。

真正需要优化的是 `internal/core/src/index/hnsw/` 中的 C++ 代码——图导航算法、SIMD 加速、内存布局。但小陈对 C++ 不熟，面对 5000+ 行 `Hnsw.cpp` 无从下手。

更让他困惑的是 Knowhere 的抽象层——为什么要包一层 Knowhere？HNSW、IVF、DISKANN 三种完全不同的索引怎么统一成一套接口？SIMD 优化具体在哪些计算环节生效？

本章将深入 Knowhere 的抽象架构、HNSW 的图导航算法、SIMD 加速原理和火焰图定位热点方法。

---

## 2. 项目设计（剧本式交锋对话）

**第一幕：Knowhere——索引的统一抽象层**

*（小陈在 C++ 代码里看到 `Knowhere::Search()`，但 HNSW 和 IVF 的实现完全不同——它们怎么共用一个接口？）*

**小胖**："Knowhere 是个啥？为什么要在 HNSW 上面再包一层？直接用 HNSW 不行吗？"

**大师**："Knowhere 就是'索引的多态层'。HNSW 是一张图，IVF 是聚类 + 倒排，DISKANN 是磁盘图——数据结构完全不同，但 Knowhere 让上层（Go 层）不关心底层是什么索引。"

**大师**（画出 Knowhere 分层架构）：

```
Knowhere 分层架构:

┌──────────────────────────────────────────────────────────┐
│                   Go 层 (QueryNode)                       │
│            index.Search(vectors, params)                  │
│                     ↓ CGO                                 │
├──────────────────────────────────────────────────────────┤
│                  Knowhere 抽象层                           │
│  ┌───────────────────────────────────────────────────┐  │
│  │  class Index {                                    │  │
│  │    virtual DatasetPtr Search(                      │  │
│  │      const DatasetPtr& dataset,                    │  │
│  │      const Config& config) = 0;                    │  │
│  │  };                                               │  │
│  └───────────────────────────────────────────────────┘  │
│                     ↓ 工厂模式                            │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌───────────┐  │
│  │  HNSW    │ │  IVF     │ │ DISKANN  │ │  BruteForce│  │
│  │  Index   │ │  Index   │ │  Index   │ │  Index     │  │
│  └──────────┘ └──────────┘ └──────────┘ └───────────┘  │
├──────────────────────────────────────────────────────────┤
│              底层库 (hnswlib / faiss / ...)               │
│  - hnswlib: 图导航算法                                    │
│  - faiss: IVF/PQ 量化                                    │
│  - diskann: 磁盘辅助索引                                  │
│  - SIMD 指令: SSE/AVX/AVX512                             │
└──────────────────────────────────────────────────────────┘
```

**大师**："Knowhere 的核心价值——"

| 价值 | 说明 |
|------|------|
| **接口统一** | Go 层只调 `index.Search()`，不关心索引类型 |
| **解耦底层** | 换底层库（hnswlib → 自研）只需改 C++ 内部 |
| **工厂模式** | `IndexFactory.Create(type)` 根据类型创建实例 |
| **参数透传** | Go 的 `{"M":16,"ef":64}` 通过 Config 透传到 C++ |

**大师**："Knowhere 中的关键数据结构——"

```cpp
// internal/core/src/index/Knowhere.h

// Dataset — 查询和结果的数据容器
struct Dataset {
    int64_t rows;           // 向量行数
    int64_t dim;            // 向量维度
    const void* tensor;     // 原始数据 (float*)
    int64_t* ids;           // [结果] 返回的 ID 列表
    float* distances;       // [结果] 返回的距离列表
};

// Index — 所有索引的基类
class Index {
public:
    // 构建索引
    virtual Status Build(const DatasetPtr& dataset, const Config& config) = 0;
    
    // 搜索（核心调用！）
    virtual DatasetPtr Search(
        const DatasetPtr& dataset,  // 查询向量
        const Config& config        // 搜索参数: {"ef": 64}
    ) = 0;
    
    // 序列化 / 反序列化
    virtual Status Serialize(BinarySet& binset) = 0;
    virtual Status Deserialize(const BinarySet& binset) = 0;
};
```

> **技术映射**：Knowhere = USB 接口协议（不管你插的是鼠标/键盘/U盘，接口都一样）；Index 基类 = 协议规范（定义了 Build/Search/Serialize 三件事）；工厂模式 = 插上什么设备就加载什么驱动。

---

**第二幕：HNSW 图导航算法——一步步拆解**

**小白**："HNSW 内部到底是怎么搜的？不是在图上跳来跳去吗？"

**大师**："对，本质上就是'跳图'——从顶层粗粒度跳到下层细粒度。"

```cpp
// HNSW 搜索算法的伪代码

DatasetPtr HNSWIndex::Search(const DatasetPtr& query, const Config& config) {
    
    int ef = config["ef"];        // 搜索宽度
    int k = config["k"];          // TopK
    auto queries = query->tensor; // 查询向量数组
    
    // Step 1: 找到入口点（最顶层的一个节点）
    int entryPoint = findEntryPoint();
    
    for (each query_vector) {
        
        // Step 2: 从顶层到底层逐层搜索
        //  每层维护一个大小为 ef 的候选队列
        PriorityQueue candidates(ef);  // 候选队列
        candidates.push(entryPoint, distance(query_vector, entryPoint));
        
        for (int level = maxLevel; level >= 0; level--) {
            PriorityQueue newCandidates(ef);
            
            // Step 3: 扩展当前层的候选节点
            while (!candidates.empty()) {
                int node = candidates.pop();
                
                // Step 4: 检查该节点的所有邻居 (最多 M 个)
                for (int neighbor : graph[node][level]) {
                    float dist = distance(query_vector, neighbor);
                    
                    // Step 5: 如果距离比队列中最远的还近 → 加入候选
                    if (newCandidates.size() < ef || dist < newCandidates.topDist()) {
                        newCandidates.push(neighbor, dist);
                    }
                }
            }
            candidates = newCandidates;
        }
        
        // Step 6: 从最终候选队列中取 TopK
        // 返回距离最近的 k 个结果
        return candidates.topK(k);
    }
}
```

**大师**："HNSW 搜索的核心复杂度——"

| 参数 | 含义 | 对复杂度的影响 |
|------|------|-------------|
| **M** | 每个节点的最大邻居数 | M↑ → 图更密 → 跳转更快 → O(log N)的常数变小 |
| **ef** | 搜索时每层维护的候选队列大小 | ef↑ → 搜索更彻底 → 召回↑ 但延迟↑ (线性关系) |
| **maxLevel** | 图的层数 | log(N) 层，每层跳 O(1) 个节点 |

**大师**："为什么 HNSW 快？——因为它把 O(N) 的暴力检索降成了 O(log N) 的图导航。100 万条数据，暴力检索要算 100 万次距离；HNSW 只需要跳约 20 层 × 每层约 20 个邻居 = 400 次距离计算——快 2500 倍！"

> **技术映射**：多级分层 = 先用世界地图定位国家（top layer），再用省地图定位城市（middle），最后用街道地图找具体房子（bottom layer）；ef = 导航时你愿意同时查几个候选路线。

---

**第三幕：SIMD、内存布局与性能火焰图**

**小胖**："你刚才说距离计算——1024 维向量的 COSINE 距离怎么算才快？"

**大师**："向量距离计算是 HNSW 搜索中 80% 的 CPU 时间花的地方。优化它有三个方向——"

```
向量距离计算 (1024维 COSINE) 的三种实现:

方案1: 朴素循环 (C++ 标准)
  float dot = 0;
  for (int i = 0; i < 1024; i++) {
      dot += a[i] * b[i];  // 1024 次乘法 + 1024 次加法
  }
  耗时: ~1000ns (标量计算)

方案2: SIMD 向量化 (AVX2, 256bit寄存器)
  // 一次处理 8 个 float (256bit / 32bit = 8)
  __m256 sum = _mm256_setzero_ps();
  for (int i = 0; i < 1024; i += 8) {
      __m256 va = _mm256_load_ps(&a[i]);
      __m256 vb = _mm256_load_ps(&b[i]);
      sum = _mm256_fmadd_ps(va, vb, sum);  // FMA: a*b+c
  }
  耗时: ~200ns (8倍加速!)

方案3: AVX-512 (512bit寄存器)
  // 一次处理 16 个 float
  耗时: ~120ns (16倍加速!)
```

**大师**："Milvus 的 Knowhere 在编译时会检测 CPU 支持的指令集（SSE/AVX2/AVX-512），自动选择最快的 SIMD 实现。"

**大师**："用火焰图定位热点——"

```bash
# 生成 QueryNode 火焰图
# 1. 采集 CPU profile
perf record -g -p $(pgrep milvus) -- sleep 30

# 2. 生成火焰图
perf script | stackcollapse-perf.pl | flamegraph.pl > querynode.svg

# 3. 查看热点
# 火焰图中最宽的函数 = 最耗 CPU 的函数
# 通常看到:
#   最宽: Knowhere::HNSW::Search()        — 索引检索
#   其次: faiss::fvec_inner_product()     — 向量内积计算
#   再次: std::priority_queue::push()     — 候选队列维护
```

> **技术映射**：SIMD = 一个人同时做 8 道算术题（vs 一道道做）；火焰图 = 体检热力图（颜色最亮最宽的部位就是最消耗能量的）；FMA 指令 = 乘法+加法一步完成（省一次中间结果读写）。

---

## 3. 项目实战

### 3.1 实战目标

跟踪一次 HNSW Search 从 QueryNode 到 C++ 执行引擎的调用链，并生成火焰图。

### 3.2 分步实现

#### 步骤 1：Go → C++ 调用链追踪

```python
# step1_call_trace.py
"""输出 HNSW Search 的 Go→C++ 完整调用链"""
CALL_CHAIN = """
HNSW Search 调用链 (Go → CGO → C++ → 底层库):

┌─────────────────────────────────────────────────────────────┐
│ QueryNode (Go)                                              │
│   querynodev2/search.go:Search()                            │
│   ├─ searchSegment()                                        │
│   └─ searchWithIndex()                                      │
│       ↓                                                      │
│       index.Search(vectors, params)                          │
│       ↓ CGO 边界                                            │
├─────────────────────────────────────────────────────────────┤
│ Knowhere (C++)                                              │
│   Knowhere.h → Index::Search()                              │
│   └─ HnswIndexNode::Search()                                │
│       ↓                                                      │
├─────────────────────────────────────────────────────────────┤
│ HNSW 实现 (C++ / hnswlib)                                   │
│   hnswlib::HierarchicalNSW::searchKnn()                     │
│   ├─ findEntryPoint()          找顶层入口                    │
│   ├─ for level = maxLevel...0:                              │
│   │   └─ searchBaseLayer()     该层的图导航                  │
│   │       ├─ getNeighbors()    遍历邻居                      │
│   │       ├─ distance_func()   ★ 计算距离 (SIMD)             │
│   │       └─ candidate_queue.push()  维护候选队列            │
│   └─ return topK(k)            返回最优 k 个                │
├─────────────────────────────────────────────────────────────┤
│ 返回结果 (C++ → CGO → Go)                                   │
│   Dataset: {ids: [...], distances: [...]}                   │
└─────────────────────────────────────────────────────────────┘

热点耗时分布 (1024维向量, 100万数据):
  distance_func()     ~60%  (通过 SIMD 优化)
  candidate_queue     ~20%  (维护优先队列)
  neighbor traversal  ~15%  (遍历邻居列表)
  other               ~5%
"""

print(CALL_CHAIN)
```

#### 步骤 2：火焰图生成脚本

```bash
#!/bin/bash
# gen_flamegraph.sh — 生成 Milvus QueryNode 火焰图

# 1. 采集 30 秒 CPU profile
echo "采集 CPU profile (30s)..."
sudo perf record -F 99 -g -p $(pgrep -f "milvus.*querynode") -- sleep 30

# 2. 转换为火焰图数据
echo "生成火焰图..."
perf script | ./FlameGraph/stackcollapse-perf.pl > out.folded
./FlameGraph/flamegraph.pl out.folded > querynode_flamegraph.svg

echo "火焰图已生成: querynode_flamegraph.svg"
echo "用浏览器打开查看 → 找到最宽的函数 = 最大热点"
```

#### 步骤 3：关键源码文件速查

```python
# step3_knowhere_map.py
"""Knowhere 核心源码文件速查表"""
print("""
Knowhere 源码导航:

  接口定义:
    internal/core/src/index/Knowhere.h          — Index 基类 + Dataset
    internal/core/src/index/KnowhereConfig.h    — 全局配置

  HNSW 实现:
    internal/core/src/index/hnsw/hnsw.cc        — HNSW 主逻辑
    internal/core/src/index/hnsw/hnswlib/       — hnswlib 底层库

  IVF 实现:
    internal/core/src/index/ivf/ivf.cc          — IVF 主逻辑
    internal/core/src/index/ivf/ivf_flat.cc     — IVF_FLAT
    internal/core/src/index/ivf/ivf_sq8.cc      — IVF_SQ8 量化

  SIMD 相关:
    internal/core/src/simd/                     — SIMD 抽象层
    internal/core/src/simd/distances_ref.cc     — 标量实现（回退）
    internal/core/src/simd/distances_avx.cc     — AVX2 实现
    internal/core/src/simd/distances_avx512.cc  — AVX-512 实现

  测试:
    internal/core/unittest/test_hnsw.cc        — HNSW 单测
    internal/core/unittest/test_simd.cc        — SIMD 正确性验证
""")
```

---

## 4. 项目总结

### 4.1 Knowhere 性能优化方向

| 优化方向 | 措施 | 效果 |
|---------|------|------|
| SIMD 指令 | AVX2/AVX-512 向量化距离计算 | 距离计算加速 8-16x |
| 内存布局 | 向量数据连续存储（AoS → SoA） | 提高 Cache 命中率 |
| 批量计算 | 多个查询向量一次传入 | 减少函数调用开销 |
| 图结构优化 | M/ef 参数调优 | 减少无效跳转 |

### 4.2 注意事项

- **SIMD 需要 CPU 支持**：AVX-512 在部分云服务器上不可用，编译时自动降级。
- **CGO 调用有开销（~100ns/次）**：QueryNode 中尽量批量调用 C++，减少 CGO 次数。
- **火焰图需要 debug 符号**：编译时不要 strip 符号表。

### 4.3 思考题

1. HNSW 的图结构在搜索时是只读的。如果利用这个特性，能否用 `mmap` 让多个 QueryNode 共享同一份索引文件以节省内存？
2. COSINE 距离需要 L2 归一化向量。如果在写入 Milvus 时预先做了归一化，距离计算可以简化成什么？性能提升多少？

---

> **下一章预告**：第37章将深入 StreamingNode 与实时数据链路。读完本章，你应该能定位 C++ 引擎的计算瓶颈并生成性能火焰图。
