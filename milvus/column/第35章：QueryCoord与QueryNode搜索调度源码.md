# 第35章：QueryCoord 与 QueryNode 搜索调度源码

> **定位**：深入查询节点调度、加载和副本管理。
> **版本**：Milvus 2.5.x
> **源码关联**：internal/querycoordv2/server.go、internal/querycoordv2/load_balancer.go、internal/querynodev2/search.go

---

## 1. 项目背景

核心开发小陈负责排查一个"搜索结果不一致"的 Bug：同一个搜索请求，隔 5 秒再搜，返回的 Top10 结果不一样。排查发现是"幽灵 Segment"作怪——QueryCoord 的 Segment 路由表里还有一个已经被 DataCoord 标记为 Dropped 的旧 Segment，导致 QueryNode 加载了旧数据参与搜索。

这个 Bug 的根因在 QueryCoord 的缓存一致性——QueryCoord 维护了一份 Segment→QueryNode 的路由表，但这份表是从 DataCoord 的元数据中间接获取的。当 DataCoord 更新 Segment 状态（比如 Compaction 后旧 Segment 变成 Dropped），QueryCoord 需要同步更新路由表。如果同步有延迟或不一致，就会出现"路由到已删除的 Segment"。

本章将深入 QueryCoord 的调度模型、QueryNode 的搜索执行和 C++ 引擎调用。

---

## 2. 项目设计（剧本式交锋对话）

**第一幕：QueryCoord 的调度模型——谁负责任务分配**

*（小陈在 QueryCoord 日志里发现一条"Segment not found in meta"的警告，追踪到路由表不一致）*

**小胖**（翻着源码）："QueryCoord 的核心逻辑在哪个文件？我找了三个 server.go 不知道看哪个——"

**大师**："注意目录名！QueryCoord 在 `internal/querycoordv2/`，带 v2 后缀。v2 和 v1 的架构完全不同。v1 已废弃。"

**大师**（画出 QueryCoord v2 调度模型）：

```
QueryCoord v2 调度模型:

┌──────────────────────────────────────────────────────────────┐
│                    QueryCoord (v2)                            │
│                                                              │
│  ┌──────────────────────────────────────────────────────┐   │
│  │              Task Scheduler (任务调度器)               │   │
│  │                                                      │   │
│  │  LoadTask → "加载 Segment 到 QueryNode"               │   │
│  │  ReleaseTask → "从 QueryNode 卸载 Segment"             │   │
│  │  BalanceTask → "重新均衡 Segment 分布"                 │   │
│  │  HandoffTask → "故障转移：接管宕机 QN 的 Segment"      │   │
│  └──────────────────────┬───────────────────────────────┘   │
│                         │                                    │
│  ┌──────────────────────▼───────────────────────────────┐   │
│  │         Segment ↔ QueryNode 路由表                     │   │
│  │                                                      │   │
│  │  Segment_A → [QueryNode-1, QueryNode-3]  (2 Replicas) │   │
│  │  Segment_B → [QueryNode-2, QueryNode-4]               │   │
│  │  Segment_C → [QueryNode-1]            (1 Replica)    │   │
│  └──────────────────────┬───────────────────────────────┘   │
│                         │                                    │
│  ┌──────────────────────▼───────────────────────────────┐   │
│  │          Load Balancer (负载均衡器)                    │   │
│  │                                                      │   │
│  │  策略: LeastLoadedFirst — 选负载最低的 QueryNode      │   │
│  │  周期: 每 30 秒检查一次                               │   │
│  │  触发: 新 Segment 产生时 / QN 加入或离开时           │   │
│  └──────────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────────┘
```

**大师**："QueryCoord v2 的核心设计理念——**Task-based Scheduling（基于任务的调度）**。不再像 v1 那样直接操作用 QueryNode，而是通过任务队列：

```go
// internal/querycoordv2/scheduler.go (简化)
type TaskScheduler struct {
    taskQueue  chan Task          // 任务队列
    nodeMgr    *NodeManager       // QueryNode 管理器
    distMgr    *DistributionManager // Segment 分布管理器
}

func (s *TaskScheduler) Schedule(ctx context.Context) {
    for task := range s.taskQueue {
        switch task.Type {
        case LoadTaskType:
            // 选择负载最低的 QueryNode
            targetNode := s.nodeMgr.GetLeastLoadedNode()
            // 分配 Segment 到目标 QueryNode
            s.distMgr.Assign(task.SegmentID, targetNode)
            // 发送 Load 指令给目标 QueryNode
            targetNode.LoadSegment(ctx, task.SegmentID)
            
        case HandoffTaskType:
            // QueryNode 宕机 → 将其 Segment 重新分配给其他 QN
            fallenNode := task.FromNode
            segments := s.distMgr.GetSegmentsOf(fallenNode)
            for _, seg := range segments {
                newTarget := s.nodeMgr.GetLeastLoadedNode()
                s.distMgr.Reassign(seg, fallenNode, newTarget)
                newTarget.LoadSegment(ctx, seg)
            }
        }
    }
}
```

> **技术映射**：Task Scheduler = 快递站调度员（新包裹到了分配派送员、派送员请假了重新分配）；Load Balancer = 看谁手头包裹最少就分给谁；Handoff = 同事请假了把他的包裹接过来继续送。

---

**第二幕：QueryNode 搜索执行——从 Go 到 C++ 的跨越**

**小白**："QueryNode 收到 Search 请求后是怎么执行的？Go 代码和 C++ 是怎么交互的？"

**大师**："这是 Milvus 最核心的一段代码——Go 负责调度和结果归并，C++ 负责高性能检索计算。"

```go
// internal/querynodev2/search.go (简化)

type SearchService struct {
    segmentMgr  *SegmentManager   // 管理已加载的 Segment
    searchPool  *SearchPool       // 搜索线程池
}

// Search — QueryNode 的搜索入口
func (s *SearchService) Search(ctx context.Context, 
    req *querypb.SearchRequest) (*querypb.SearchResponse, error) {
    
    // Step 1: 获取本地已加载的 Segment 列表
    segments := s.segmentMgr.GetSegments(req.GetCollectionID())
    
    // Step 2: 并发搜索每个 Segment
    resultChan := make(chan *SearchResult, len(segments))
    
    for _, seg := range segments {
        go func(segment Segment) {
            result := s.searchSegment(ctx, segment, req)
            resultChan <- result
        }(seg)
    }
    
    // Step 3: 收集 + 合并 TopK
    var allResults []*SearchResult
    for i := 0; i < len(segments); i++ {
        allResults = append(allResults, <-resultChan)
    }
    
    // Step 4: 全局 TopK 排序
    finalResults := mergeTopK(allResults, req.GetTopK())
    
    return &querypb.SearchResponse{Results: finalResults}, nil
}

// searchSegment — 搜索单个 Segment
func (s *SearchService) searchSegment(ctx context.Context, 
    seg Segment, req *querypb.SearchRequest) *SearchResult {
    
    if seg.IsSealed() {
        // Sealed Segment → 走索引检索 (C++ Knowhere)
        return s.searchWithIndex(ctx, seg, req)
    } else {
        // Growing Segment → 暴力检索
        return s.searchBruteForce(ctx, seg, req)
    }
}

// searchWithIndex — 调用 C++ Knowhere 引擎
func (s *SearchService) searchWithIndex(ctx context.Context, 
    seg Segment, req *querypb.SearchRequest) *SearchResult {
    
    // Step 1: 获取 C++ 侧的 Knowhere 索引对象
    index := seg.GetIndex()  // CGO 指针
    
    // Step 2: 准备查询参数
    searchParams := &knowhere.SearchParams{
        TopK:        req.GetTopK(),
        MetricType:  req.GetMetricType(),
        Ef:          req.GetExtraParams()["ef"],
        // ...
    }
    
    // Step 3: 调用 C++ 检索 (通过 CGO)
    // 这是最核心、最耗时的调用！
    results := index.Search(
        req.GetVectors(),       // 查询向量
        searchParams,           // 搜索参数
    )
    
    // Step 4: 有 Expr 吗？→ Go 侧做标量过滤
    if req.GetExpr() != "" {
        results = s.filterByExpr(results, req.GetExpr(), seg)
    }
    
    return results
}
```

**大师**："Go → C++ 调用的关键路径——"

```
QueryNode (Go)                    Knowhere (C++)
─────────────                    ──────────────
index.Search() ──CGO──→         Knowhere::Search()
    ↓                                ↓
    │                           HNSW::Search() 
    │                           ├─ find_entry_point()
    │                           ├─ search_layer()  // 图导航
    │                           └─ collect_results()
    │                                ↓
results ←──CGO──              return ResultSet
```

> **技术映射**：Go 调度层 = 快递站管理员（分派包裹、合并结果）；C++ 执行层 = 快递员（高效的体力活）；CGO = 对讲机（Go 和 C++ 之间的通信桥梁）。

---

**第三幕：模拟 QueryNode 下线——Handoff 全过程**

**小胖**："QueryNode 宕机后，QueryCoord 怎么把它上面的 Segment 重新分配？"

**大师**："这就是 Handoff（交接）机制——"

```go
// Handoff 的源码级流程

// Step 1: QueryCoord 检测到 QueryNode 心跳丢失
func (s *QueryCoordServer) checkHeartbeat(ctx context.Context) {
    for _, node := range s.nodeMgr.GetAllNodes() {
        if time.Since(node.LastHeartbeat) > s.heartbeatTimeout {
            log.Warn("QueryNode 心跳超时",
                zap.Int64("nodeID", node.ID))
            // 触发 Handoff 任务
            s.taskScheduler.Enqueue(HandoffTask{
                FromNode: node.ID,
                Reason:   "heartbeat_timeout",
            })
        }
    }
}

// Step 2: HandoffTask 执行
func (t *HandoffTask) Execute(ctx context.Context, 
    dist *DistributionManager, nodeMgr *NodeManager) error {
    
    // 获取宕机节点上的 Segment 列表
    fallenSegments := dist.GetSegmentsOf(t.FromNode)
    
    for _, seg := range fallenSegments {
        // 检查是否有其他副本可用
        replicas := dist.GetReplicas(seg)
        if len(replicas) > 1 {
            // 有副本！→ 只需要补充副本到目标 Replica 数
            // 搜索不受影响（副本自动接替）
            dist.Remove(seg, t.FromNode)  // 移除失效副本
        } else {
            // 没有副本！→ 紧急重分配到新 QueryNode
            target := nodeMgr.GetLeastLoadedNode()
            dist.Reassign(seg, t.FromNode, target)
            target.LoadSegment(ctx, seg)  // 新节点加载 Segment
        }
    }
    
    // 标记宕机节点为 Offline
    nodeMgr.MarkOffline(t.FromNode)
    return nil
}
```

**大师**："Handoff 的时间线——"

| 时间点 | 事件 | 搜索状态 |
|--------|------|---------|
| T0 | QueryNode 宕机 | Google搜索正常（有副本的话） |
| T0+5s | QueryCoord 心跳超时 | 开始 Handoff |
| T0+6s | 副本自动接替 | 搜索恢复（有副本） |
| T0+6s | 无副本 Segment 开始重分配 | 该 Segment 暂时不可搜索 |
| T0+30s | 新 QueryNode 加载 Segment 完成 | 全部 Segment 恢复可搜索 |

---

## 3. 项目实战

### 3.1 实战目标

模拟 QueryNode 下线，跟踪 QueryCoord 如何重新调度 Segment 并恢复搜索能力。

### 3.2 分步实现

#### 步骤 1：Handoff 模拟脚本

```python
# step1_handoff_sim.py
"""模拟 QueryNode 故障转移"""
import time
from pymilvus import Collection, connections

connections.connect(host="localhost", port="19530")
collection = Collection("product_search_prod")

print("=" * 60)
print("QueryNode Handoff 模拟")
print("=" * 60)

# 1. 故障前状态
print("\n1. 故障前:")
segments = collection.get_replicas()
for seg in segments[:5]:
    print(f"  Segment {seg.group_id}: Replicas={seg.num_replicas}, Nodes={seg.node_ids}")
print(f"  数据量: {collection.num_entities}")

# 2. 模拟 QueryNode 下线
print("\n2. [模拟] QueryNode-1 宕机...")
print("   kubectl delete pod querynode-1 (实际命令)")
print("   QueryCoord 将在 ~5s 后检测到心跳丢失")

# 3. 观察恢复过程
print("\n3. 观察恢复:")
print("   T+0s: QueryNode-1 宕机")
print("   T+5s: QueryCoord 心跳超时, 触发 Handoff")
print("   T+6s: 副本自动接替 (有 Replica 的 Segment)")
print("   T+7s: 搜索恢复正常")
print("   T+30s: 新副本构建完成, Replica 恢复到设定值")

# 4. 验证搜索
print("\n4. 搜索验证:")
time.sleep(2)
try:
    r = collection.search(
        data=[[0.1]*384], anns_field="title_vec",
        param={"metric_type": "COSINE", "params": {"ef": 16}},
        limit=5, timeout=10
    )
    print(f"   搜索成功! {len(r[0])} 条结果 (有副本保护)")
except Exception as e:
    print(f"   搜索失败 (可能无副本): {e}")
```

#### 步骤 2：搜索调度流程可视化

```python
# step2_search_scheduling.py
"""输出 QueryCoord → QueryNode 搜索调度流程图"""
SCHEDULING_FLOW = """
搜索请求调度流程 (Proxy → QueryCoord → QueryNode):

┌──────────────────────────────────────────────────────────────┐
│ Proxy                                                        │
│   SearchTask.Execute()                                       │
│   ├─ 1. 参数校验                                             │
│   └─ 2. 获取 Segment 路由                                    │
│        ↓ getSegmentInfo()                                     │
├──────────────────────────────────────────────────────────────┤
│ QueryCoord                                                   │
│   GetSegmentInfo(collectionID)                               │
│   ├─ 查路由表: Segment_{A,B,C} → QueryNode_{1,2,3}           │
│   └─ 返回: [{QN-1: [A,B]}, {QN-2: [C]}, {QN-3: [D,E]}]     │
├──────────────────────────────────────────────────────────────┤
│ Proxy (Fan-out)                                              │
│   ├─→ QueryNode-1: Search(segments=[A,B], vector=..., topk)  │
│   ├─→ QueryNode-2: Search(segments=[C],   vector=..., topk)  │
│   └─→ QueryNode-3: Search(segments=[D,E], vector=..., topk)  │
├──────────────────────────────────────────────────────────────┤
│ QueryNode-1                                                  │
│   SearchService.Search()                                     │
│   ├─ Segment A (Sealed): searchWithIndex() → C++ HNSW        │
│   ├─ Segment B (Sealed): searchWithIndex() → C++ HNSW        │
│   ├─ 应用 Expr 过滤                                          │
│   └─ 返回本地 TopK                                           │
├──────────────────────────────────────────────────────────────┤
│ Proxy (Fan-in)                                               │
│   ├─ 收集 QN-1/QN-2/QN-3 的结果                              │
│   ├─ 全局排序取 TopK                                          │
│   └─ 返回 SearchResponse                                     │
└──────────────────────────────────────────────────────────────┘
"""

print(SCHEDULING_FLOW)
```

#### 步骤 3：关键源码断点速查

```python
# step3_breakpoints.py
"""QueryCoord/QueryNode 断点速查"""
print("""
QueryCoord v2 关键断点:

  internal/querycoordv2/server.go:
    GetSegmentInfo()           — 返回 Segment→QueryNode 路由
    checkHeartbeat()            — 心跳检测 + Handoff 触发

  internal/querycoordv2/load_balancer.go:
    GetLeastLoadedNode()       — 负载均衡算法

  internal/querycoordv2/handoff.go:
    HandoffTask.Execute()      — 故障转移执行

QueryNode 关键断点:

  internal/querynodev2/search.go:
    Search()                   — 搜索入口
    searchSegment()            — 单个 Segment 搜索
    searchWithIndex()          — C++ 索引检索调用 (CGO 边界)

  internal/querynodev2/segments/segment.go:
    Load()                     — Segment 加载到内存
    Release()                  — Segment 释放

C++ 执行引擎关键断点:

  internal/core/src/index/hnsw/Hnsw.cpp:
    Search()                   — HNSW 图导航搜索
""")
```

---

## 4. 项目总结

### 4.1 QueryCoord/QueryNode 核心文件

| 文件 | 关键函数 | 职责 |
|------|---------|------|
| `internal/querycoordv2/server.go` | `GetSegmentInfo()` | 路由查询 |
| `internal/querycoordv2/scheduler.go` | `Schedule()` | 任务调度 |
| `internal/querycoordv2/load_balancer.go` | `GetLeastLoadedNode()` | 负载均衡 |
| `internal/querycoordv2/handoff.go` | `HandoffTask.Execute()` | 故障转移 |
| `internal/querynodev2/search.go` | `Search()` | 搜索入口 |
| `internal/querynodev2/search.go` | `searchWithIndex()` | C++ 调用 |
| `internal/core/src/index/hnsw/` | `Search()` | HNSW 检索 |

### 4.2 搜索性能排查清单

| 症状 | 排查方向 |
|------|---------|
| 搜索慢 | 看 QN 的 Segment 数量、C++ 检索耗时 |
| 搜索结果不一致 | 看路由表是否包含过期 Segment |
| QN OOM | 看已加载 Segment 总量、启用 IVF_SQ8 |
| 部分 Segment 搜不到 | 看 Handoff 日志、心跳超时 |

### 4.3 思考题

1. QueryCoord 如何保证路由表和 DataCoord 的 Segment 状态一致？如果 DataCoord 标记了一个 Segment 为 Dropped，QueryCoord 多久能感知到？
2. Handoff 过程中，如果目标 QueryNode 在加载 Segment 时也宕机了，系统如何处理这种"连锁故障"？

---

> **下一章预告**：第36章将深入 C++ Knowhere 引擎——HNSW 索引的源码实现。读完本章，你应该能理解 QueryCoord 调度 + QueryNode 搜索执行的完整源码逻辑。
