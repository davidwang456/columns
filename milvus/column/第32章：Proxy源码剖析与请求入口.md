# 第32章：Proxy 源码剖析与请求入口

> **定位**：理解所有客户端请求进入 Milvus 的第一站。
> **版本**：Milvus 2.5.x
> **源码关联**：internal/proxy/impl.go、internal/proxy/task_search.go、internal/proxy/task_insert.go

---

## 1. 项目背景

核心开发小陈在修一个 Bug：客户端 `Insert` 返回成功但数据在 Milvus 中查不到。他加了日志后发现，`Insert` 请求在 Proxy 层被成功路由到了 Pulsar，返回了 InsertResponse，但 DataNode 没有消费到这条消息。排查 Pulsar 发现消息确实被写入了，但属于一个"僵尸 Channel"——DataCoord 已经释放了这个 Channel，但 Proxy 的缓存还没更新。

这个 Bug 的根因在 Proxy 的三个职责交叉点：**请求校验（Request Validation）、消息分发（Message Distribution）、缓存管理（Cache Management）**。小陈只有读懂 Proxy 的完整处理流程，才能理解为什么缓存不同步导致了消息投递到僵尸 Channel。

本章将深读 Proxy 的源码实现：`SearchTask` 和 `InsertTask` 的执行流程、任务队列机制、gRPC 错误处理以及与 Coordinator 的交互模式。

---

## 2. 项目设计（剧本式交锋对话）

**第一幕：Proxy 的三大职责——不只是一个转发器**

*（小陈在 Proxy 的代码里打印了 50 处日志，终于找到僵尸 Channel 的根因）*

**小胖**（不解地）："Proxy 不就是个转发器吗？收到请求 → 校验一下 → 发给后端——为啥有 3000 行代码？"

**大师**："Proxy 根本不是简单转发器。它是 Milvus 的门卫、调度员和翻译官三合一。"

**大师**（画出 Proxy 内部架构）：

```
Proxy 内部架构 (impl.go + task_*.go)

┌─────────────────────────────────────────────────────────┐
│                     Proxy Service                        │
│                                                         │
│  ┌─────────────────────────────────────────────────┐   │
│  │              gRPC Handlers                       │   │
│  │  Search() / Insert() / CreateCollection() / ...  │   │
│  └────────────────────┬────────────────────────────┘   │
│                       │ 创建 Task                       │
│  ┌────────────────────▼────────────────────────────┐   │
│  │              Task Queue (任务队列)                │   │
│  │  ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐           │   │
│  │  │Search│ │Insert│ │Delete│ │Create│ ...        │   │
│  │  │Task  │ │Task  │ │Task  │ │Coll  │           │   │
│  │  └──┬───┘ └──┬───┘ └──────┘ └──────┘           │   │
│  └─────┼────────┼──────────────────────────────────┘   │
│        │        │                                       │
│  ┌─────▼────────▼──────────────────────────────────┐   │
│  │          Task.Execute() 执行引擎                  │   │
│  │                                                  │   │
│  │  SearchTask.Execute():                           │   │
│  │    ① validateRequest()    参数校验                │   │
│  │    ② getShardLeaders()    获取分片信息            │   │
│  │    ③ getSegmentInfo()  → QueryCoord             │   │
│  │    ④ search()           → QueryNode(s)          │   │
│  │    ⑤ reduce()             结果归并                │   │
│  │    ⑥ sendResponse()       返回客户端              │   │
│  │                                                  │   │
│  │  InsertTask.Execute():                           │   │
│  │    ① validateRequest()    参数校验                │   │
│  │    ② assignChannels()     分片到 Channel          │   │
│  │    ③ allocTimestamp()   → RootCoord             │   │
│  │    ④ produceMsg()       → Message Queue         │   │
│  │    ⑤ sendResponse()       返回客户端              │   │
│  └──────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────┘
```

**大师**："Proxy 的三个核心职责——"

| 职责 | 代码体现 | 关键文件 |
|------|---------|---------|
| **门卫**（校验+限流） | `validateRequest()` / RateLimiter | `task_*.go → OnEnqueue()` |
| **调度员**（路由+分片） | `getShardLeaders()` / `assignChannels()` | `task_search.go` / `task_insert.go` |
| **翻译官**（协议转换） | gRPC ↔ internal protobuf | `impl.go → gRPC Handlers` |

> **技术映射**：门卫 = 公司前台（查工牌、限人数）；调度员 = 会议室管理员（看你是什么会，分到几号会议室）；翻译官 = 同声传译（把客户需求翻译成内部指令）。

---

**第二幕：SearchTask 的完整执行流程——代码级追踪**

**小白**："SearchTask.Execute() 里面到底做了什么？能逐行解释吗？"

**大师**："我带你走一遍 SearchTask 的 6 个步骤——"

```go
// internal/proxy/task_search.go (简化逻辑)

type SearchTask struct {
    Condition
    *milvuspb.SearchRequest     // 原始 gRPC 请求
    result  *milvuspb.SearchResults
    query   *milvuspb.SearchResult // 最终结果
}

// Execute — SearchTask 的主执行函数
func (t *SearchTask) Execute(ctx context.Context) error {
    
    // Step 1: 参数校验
    // 检查: 向量维度是否匹配? TopK 是否合法? Expr 语法是否正确?
    if err := t.validateRequest(); err != nil {
        return err
    }
    
    // Step 2: 获取 Collection 的 Shard Leader 信息
    // Shard Leader = 每个 Shard 对应的"主"Proxy（如果是多 Proxy 部署）
    leaders, err := t.getShardLeaders()
    if err != nil {
        return err
    }
    
    // Step 3: 向 QueryCoord 查询 Segment 分布
    // 返回: "哪些 Segment 分布在哪些 QueryNode 上"
    segmentInfo, err := t.queryCoord.GetSegmentInfo(ctx, t.collectionID)
    if err != nil {
        return err
    }
    
    // Step 4: 并发向多个 QueryNode 发送搜索子请求
    // 每个 QueryNode 负责自己加载的 Segment
    results, err := t.searchChannel(ctx, segmentInfo)
    // 内部实现:
    //   for each QueryNode:
    //       go func() {
    //           resp, _ := queryNode.Search(ctx, subRequest)
    //           resultChan <- resp
    //       }()
    
    // Step 5: 收集 + 归并结果
    // 从所有 QueryNode 收集 TopK，做全局排序
    merged, err := t.reduce(results)
    
    // Step 6: 构造 gRPC Response
    t.result = &milvuspb.SearchResults{
        Results: merged,
    }
    return nil
}
```

**大师**："关键设计模式——"

| 模式 | 位置 | 作用 |
|------|------|------|
| **Task 模式** | `task_*.go` | 每种请求类型一个 Task，统一 Execute() 接口 |
| **Fan-out/Fan-in** | `searchChannel()` | 并发发往多个 QueryNode，再收集合并 |
| **缓存减少 RPC** | `globalMetaCache` | Collection/Partition/Segment 信息缓存，避免每次都调 Coordinator |
| **超时传播** | `context.WithTimeout()` | 超时信息通过 Context 树贯穿整个调用链 |

> **技术映射**：Task 模式 = 标准化工单（每个请求一张工单，统一处理流程）；Fan-out = 把任务复印多份分给不同部门；Meta Cache = 公司通讯录（不用每次都去 HR 查）。

---

**第三幕：错误处理与常见客户端错误根因**

**小胖**："用户经常报 `collection not loaded` 和 `dimension mismatch` ——这些错误是 Proxy 产生的还是后端产生的？"

**大师**："这些错误大部分是 Proxy 在校验阶段就拦截下来了——"

```go
// 常见错误在 Proxy 层的产生位置

// 错误 1: "collection not loaded"
// 位置: task_search.go → validateRequest()
func (t *SearchTask) validateRequest() error {
    // 检查 Collection 是否存在
    coll, err := t.globalMetaCache.GetCollection(ctx, t.collectionName)
    if err != nil {
        return merr.WrapErrCollectionNotFound(t.collectionName)
    }
    // 检查 Load 状态
    if !t.queryCoord.IsCollectionLoaded(ctx, coll.ID) {
        return merr.WrapErrCollectionNotLoaded(t.collectionName)
        // ↑ 这就是你常看到的 "collection not loaded" 错误！
    }
    return nil
}

// 错误 2: "dimension mismatch"
// 位置: task_search.go → validateRequest()
func (t *SearchTask) validateRequest() error {
    // ...
    if len(t.vectors[0]) != coll.GetVectorFieldDim() {
        return merr.WrapErrParameterInvalid(
            "vector dimension", 
            fmt.Sprintf("expected %d, got %d", 
                coll.GetVectorFieldDim(), len(t.vectors[0])))
        // ↑ "dimension mismatch" 就是在这里被拦截的！
    }
    return nil
}

// 错误 3: "collection not found"
// 位置: task_create_collection.go
// 创建 Collection 时如果名称已存在，由 RootCoord 返回错误
// Proxy 不做重复检查，而是把错误从 RootCoord 透传给客户端
```

**大师**："理解错误来源对调试至关重要——"

| 错误 | 产生位置 | 排查方向 |
|------|---------|---------|
| `collection not loaded` | Proxy 校验 | 检查 `collection.load()` 是否执行 |
| `dimension mismatch` | Proxy 校验 | 检查 Embedding 模型维度与 Schema 一致 |
| `collection not found` | Proxy 校验 / RootCoord | 检查 Collection 是否存在 |
| `search timeout` | Proxy 超时控制 | 检查 QueryNode 响应时间 |
| `duplicate primary key` | DataNode 写入 | 检查主键是否重复 |

---

## 3. 项目实战

### 3.1 实战目标

给一次 Search 请求打日志，追踪 Proxy 中从 gRPC 入口到任务执行的完整路径。

### 3.2 分步实现

#### 步骤 1：Proxy 关键代码追踪脚本

```go
// trace_proxy.go — 添加到 internal/proxy/ 目录下
// 这个文件帮助在关键路径上输出格式化的追踪日志

package proxy

import (
    "context"
    "time"
    "go.uber.org/zap"
)

// TraceSearchTask 追踪 SearchTask 的完整执行路径
func (node *Proxy) TraceSearchTask(ctx context.Context, 
    req *milvuspb.SearchRequest) (*milvuspb.SearchResults, error) {
    
    log := log.Ctx(ctx)
    
    // 阶段 1: gRPC 入口
    t0 := time.Now()
    log.Info("[Trace] Search 请求到达 Proxy",
        zap.String("collection", req.GetCollectionName()),
        zap.Int("nq", len(req.GetVectors())),
        zap.String("expr", req.GetExpr()))
    
    // 阶段 2: 参数校验
    if err := validateSearchRequest(req); err != nil {
        log.Warn("[Trace] 参数校验失败", zap.Error(err))
        return nil, err
    }
    log.Info("[Trace] 参数校验通过", 
        zap.Duration("cost", time.Since(t0)))
    
    // 阶段 3: 获取 Segment 分布
    t1 := time.Now()
    segmentInfo, err := node.queryCoord.GetSegmentInfo(ctx, req.GetCollectionID())
    if err != nil {
        return nil, err
    }
    log.Info("[Trace] Segment 分布获取完成",
        zap.Int("segments", len(segmentInfo)),
        zap.Duration("cost", time.Since(t1)))
    
    // 阶段 4: 并发搜索
    t2 := time.Now()
    results, err := node.searchSegments(ctx, req, segmentInfo)
    log.Info("[Trace] QueryNode 搜索完成",
        zap.Duration("cost", time.Since(t2)))
    
    // 阶段 5: 归并
    t3 := time.Now()
    final := reduceSearchResults(results)
    log.Info("[Trace] 结果归并完成",
        zap.Duration("cost", time.Since(t3)))
    
    log.Info("[Trace] Search 请求处理完成",
        zap.Duration("total", time.Since(t0)))
    
    return final, nil
}
```

#### 步骤 2：断点观察清单

```python
# step2_breakpoints.py
"""Proxy 源码断点观察清单"""
BREAKPOINTS = """
Proxy 源码关键断点:

1. impl.go:Search()          # gRPC 入口
   观察: 原始 SearchRequest 的内容
   提问: Collection 名、向量维度、Expr 是否如预期？

2. task_search.go:OnEnqueue()  # 任务入队
   观察: 任务队列长度
   提问: 当前有多少任务在排队？是否有积压？

3. task_search.go:validateRequest()  # 参数校验
   观察: 校验逻辑和错误返回
   提问: 最常见的客户端错误（dimension mismatch）在这里产生

4. task_search.go:getSegmentInfo()  # 获取 Segment 路由
   观察: QueryCoord 返回的 Segment→QueryNode 映射
   提问: 是否有热点 QueryNode（分配了过多 Segment）？

5. task_search.go:searchChannel()  # 并发搜索
   观察: 每个 QueryNode 的响应时间和结果数
   提问: 最慢的 QueryNode 是谁？为什么慢？

6. task_search.go:reduce()  # 结果归并
   观察: 归并前的各节点结果、归并后的最终 TopK
   提问: 归并算法是否正确？Distance 排序是否一致？
"""

print(BREAKPOINTS)
```

#### 步骤 3：用日志追踪一次完整的 CreateCollection+Insert+Search

```python
# step3_full_trace.py
"""通过 PyMilvus 触发全链路追踪"""

# 前提：已在 Go 侧代码中加好 Trace 日志（见步骤 1）
# 然后在 SDK 侧触发操作，观察 Go 侧日志输出

trace_script = """
操作序列 & 预期 Go 侧日志:

1. CreateCollection
   Go 日志: "[Trace] CreateCollection 到达 Proxy"
   Go 日志: "[RootCoord] CreateCollection 开始"
   Go 日志: "[RootCoord] 写入 etcd 元数据"
   Go 日志: "[Trace] CreateCollection 完成"

2. Insert (1000条)
   Go 日志: "[Trace] Insert 到达 Proxy (rows=1000)"
   Go 日志: "[Trace] 分片完成 (4 shards)"
   Go 日志: "[Trace] 写入 MQ 完成 (cost=2ms)"
   Go 日志: "[DataNode] 消费消息"
   Go 日志: "[DataNode] 写入 Growing Segment"

3. Flush
   Go 日志: "[DataCoord] Flush 触发"
   Go 日志: "[DataNode] Binlog 上传 MinIO"

4. Search
   Go 日志: "[Trace] Search 到达 Proxy"
   Go 日志: "[Trace] Segment 路由: QN1=[A,B], QN2=[C,D]"
   Go 日志: "[QueryNode-1] Search 完成 (3ms, 10 results)"
   Go 日志: "[QueryNode-2] Search 完成 (2ms, 10 results)"
   Go 日志: "[Trace] 归并完成 (total=8ms)"
"""

print("全链路追踪日志对照表:")
print(trace_script)
```

---

## 4. 项目总结

### 4.1 Proxy 源码关键点速查

| 关键点 | 源码位置 | 职责 |
|--------|---------|------|
| gRPC Server | `impl.go:Register()` | 注册 gRPC 服务 |
| Search 入口 | `task_search.go:Execute()` | 搜索请求 6 步处理 |
| Insert 入口 | `task_insert.go:Execute()` | 写入请求 5 步处理 |
| 参数校验 | `task_*.go:validateRequest()` | 拦截 90% 的客户端错误 |
| Meta Cache | `globalMetaCache` | 缓存 Collection/Segment 信息 |
| 超时控制 | `context.WithTimeout()` | 全链路超时传播 |

### 4.2 调试 Proxy 的黄金命令

```bash
# 查看 Proxy 日志
docker compose logs standalone | grep -i "proxy.*search\|proxy.*insert"
# 或者 K8s:
kubectl logs -n milvus deployment/milvus-proxy --tail=100 | grep SearchTask

# 增加 Proxy 日志级别（配置文件）
# configs/milvus.yaml → log.level: debug
```

### 4.3 思考题

1. Proxy 的 `globalMetaCache` 缓存了 Collection/Segment 信息。如果 DataCoord 更新了 Segment 状态但 Proxy 的缓存还未刷新，会导致什么错误？如何设计缓存失效策略？
2. SearchTask 中并发调用多个 QueryNode 时，如果其中一个 QueryNode 响应超时，Proxy 是等待它还是用其他 QN 的结果直接返回？

---

> **下一章预告**：第33章将深入 RootCoord——元数据管理的控制中心。读完本章，你应该能读懂 Proxy 的源码并定位请求入口的错误根因。
