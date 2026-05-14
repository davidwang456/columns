# 第33章：TSDB源码深度剖析——Compaction与索引

## 一、项目背景

运维团队最近发现了一个令人不安的现象：Prometheus 的数据目录 `data/` 下，block 文件夹数量已经膨胀到 500 多个，每个只有几十 MB。同样的 PromQL 查询，查最近 2 小时的数据只需 50ms，但查 3 天前的数据却要 5 秒——慢了整整 100 倍。

运维老张百思不得其解：为什么 Prometheus 不把所有小 block 合并成一个大 block 呢？实际上它确实会——这就是 Compaction 机制的作用。但问题在于，Compaction 有时候会卡住：CPU 飙到 100%，却长时间不能产出新 block，导致数据查询延迟越来越大。老张一看磁盘，I/O 队列都满了。

这背后隐藏着更深层的问题：TSDB 的倒排索引（Postings Index）到底是怎么工作的？为什么按 label 过滤查询可以做到 O(1) 找到所有包含某个 label 的 series ID？为什么 label 基数越大查询越慢？Tombstone（软删除）为什么不立即物理删除数据？答案全部藏在 Compaction 策略和索引结构之中。

本章将深入 TSDB 的三个核心机制：**Level-based Compaction 合并策略**，解释它如何借鉴 LSM-Tree 的分层思想，按时间重叠度将小 block 逐级合并；**倒排索引的构建与查询算法**，揭示 `{job="node"}` 这类标签过滤为何能瞬间定位目标 series；以及 **Tombstone 的延迟清理机制**，解释 Prometheus 为何选择"宁可多存、不可误删"的哲学。读完本章，你将能够诊断 Compaction 性能问题、合理设计标签体系，并理解每一次 PromQL 查询在 TSDB 内部的完整执行路径。

---

## 二、剧本式交锋对话

**角色设定：**
- **小胖**：初级 SRE，刚接手 Prometheus 运维，喜欢从直觉出发提问
- **小白**：中级工程师，有一定基础，擅长追问细节
- **大师**：资深架构师，对 TSDB 源码了如指掌，解释通俗又深入

---

**小胖**（翻看着 Grafana 面板，眉头紧锁）：大师，我们 Prometheus 的 block 文件夹已经 500 多个了，磁盘都快满了。查最近的数据飞快，查几天前的就像在翻旧报纸，慢得要死。我想直接写个脚本把所有 block 合并成一个大文件，你觉得行不行？

**大师**（笑着摇头）：你这个想法方向是对的，但 Prometheus 自己就在做这件事——Compaction。只不过它不是"一次性全合并"，而是分层渐进式的。你想啊，如果每次有新数据进来都要重新合并整个所有 block，I/O 开销得多大？

**小白**（插话）：分层？是不是像 LevelDB 那种 LSM-Tree 的套路？

**大师**：正是。Prometheus 的 Compaction 用的是 Level-based 策略。新写入的 block 在 Level 1，当 Level 1 的 block 数量超过阈值（默认 3 个），且它们的时间范围有重叠，就会触发合并：把 Level 1 的几个 block 合并成一个，放入 Level 2。同理，Level 2 多了再合并到 Level 3……一层层往上推。这样就避免了"全量重写"的灾难。

**小胖**：那为什么不能把今天的数据和去年的数据合并？

**大师**：问得好！关键约束是**时间范围重叠**。Compaction 只会合并时间范围有重叠的 block。今天新写入的 block 时间范围是 `[10:00, 12:00]`，去年的 block 时间范围是 `[2025-05-14, 2025-05-15]`，二者毫无交集，合并它们毫无意义——查询也不会同时访问这两个 block。强制合并只会浪费 I/O，还增加新 block 的体积。

**小胖**（若有所思）：那查 `up{job="node"}` 这种带标签过滤的 PromQL，是扫描所有 block 去匹配吗？那如果有一万个 series，不是要扫一万次？

**大师**：当然不会。这就靠**倒排索引**了。它的结构和你在图书馆查书的思路一模一样：你按"作者=张三"查书，不需要翻遍所有书架，而是直接查索引卡"作者→张三→《书A》、《书B》"。TSDB 的倒排索引也是这样：

```
label name → label value → []seriesID
```

当查询 `{job="node"}` 时，直接从 `map["job"]["node"]` 拿到所有匹配的 series ID 列表——时间复杂度 O(1)。然后拿着这些 series ID 去**正排索引**（`seriesID → []chunkRef`）找到对应 chunk 在文件中的位置，最后读取 chunk 数据。

**小白**（追问）：那如果查询条件有两个标签，比如 `{job="node", instance="web-01"}`，是怎么处理的？

**大师**：这就是倒排索引的经典操作——**Postings List 求交集**。先从 `job=node` 拿到 series ID 列表 A，再从 `instance=web-01` 拿到列表 B，然后用双指针归并算法求 A ∩ B。Postings list 采用**差分编码**压缩存储，比如原始列表 `[1, 5, 23, 100]` 存为 `[1, 4, 18, 77]`，大幅节省磁盘和内存。

**小胖**：那删除数据呢？比如某个 exporter 下线了，我想删掉它的历史数据……

**大师**：这就引出 Tombstone 机制了。Prometheus **从不立即物理删除数据**。当你发起删除请求，它只是在 `tombstones` 文件中记录一条："series X，时间范围 `[t1, t2]` 的数据已标记删除"。查询时，TSDB 会读取 tombstone 记录，在返回 chunk 前过滤掉被标记的时间段。真正的物理删除发生在 **Compaction 时**——合并过程中，遍历到被 tombstone 完全覆盖的 series 或 chunk 时直接跳过，不写入新 block。旧 block 随后被删除。

**小白**：为什么不直接物理删除？

**大师**：代价太高了。如果一条 series 的某个 chunk 被标记删除，物理删除意味着要重写整个 block 的 chunk 文件和索引文件——block 可能有几十 GB。Tombstone 的思路是"延迟批量处理"：把删除操作推迟到 Compaction 这个本就该重写数据的时机，一并处理。这是典型的"宁可多存、不可误删"哲学——删除不可逆，必须慎之又慎。

---

## 三、项目实战

### 环境准备

本实战基于 Prometheus 源码，重点关注以下文件：

| 文件 | 作用 |
|------|------|
| `tsdb/compact.go` | Compaction 的触发、调度和合并执行 |
| `tsdb/index/index.go` | 磁盘上索引文件的结构定义与读写 |
| `tsdb/index/postings.go` | 倒排索引（MemPostings）的内存实现 |
| `tsdb/tombstones/tombstones.go` | Tombstone 的读写接口和实现 |

你还需要一个正在运行并产生数据的 Prometheus 实例，用于观察 compaction 行为。

### 步骤1：追踪 Compaction 的触发和调度

打开 `tsdb/compact.go`，核心调度逻辑如下：

```go
func (db *DB) compact() (changes bool, returnErr error) {
    // 1. 获取所有可合并的 block（按时间范围分组）
    blocks := db.Blocks()
    // 2. 选择要合并的 block 组（时间重叠的 → 可以合并）
    plan, err := db.compactor.Plan(db.dir, blocks)
    // 3. 执行合并（创建新 block，写入合并后的 chunks 和 index）
    for _, p := range plan {
        uid, err := db.compactor.Compact(db.dir, p, db.blocks)
    }
}
```

`Plan` 方法负责制定合并计划：将 block 按时间范围排序，找出时间重叠的组，再根据每个 level 的最大 block 数来决定哪些组需要合并。核心原则：**同一 level 中时间重叠的 block 数量超过阈值时才触发合并**。

`Compact` 函数的核心流程：

```go
func (c *LeveledCompactor) Compact(dest string, dirs []string) (uid ulid.ULID, err error) {
    // 1. 创建一组 BlockReader，打开源 block
    // 2. 创建一个新的 BlockWriter（接收合并后的数据）
    // 3. 遍历所有源 block 的 series：
    //    a. 如果该 series 有 tombstone → 跳过（这才是真正删除的时刻！）
    //    b. 合并同一 series 的多个 chunk（去重+排序）
    //    c. 写入新 block 的 chunk 文件
    //    d. 写入新 block 的 index
    // 4. 关闭 writer，写入 meta.json
    // 5. 删除源 block（现在可以物理删除了）
}
```

**关键洞察：**
- Compaction 是重 I/O + CPU 密集型操作——需要读取所有源 block 的数据，重写一份合并后的新 block
- 合并时会对同一 series 的 chunk 做**去重**（同一时间范围的 chunk 只保留一份）
- **Tombstone 真正生效的时刻**就在这里：合并时遇到被完全标记删除的 series 直接跳过，不被写入新 block
- 磁盘占用在合并期间会**临时翻倍**——因为新旧 block 同时存在，合并完成后旧 block 才会被删除

### 步骤2：理解倒排索引的构建

打开 `tsdb/index/postings.go`，内存中的倒排索引结构：

```go
// MemPostings 是内存中的倒排索引（在 Head 中使用）
type MemPostings struct {
    m       map[string]map[string][]storage.SeriesRef
    mtx     sync.RWMutex
    ordered bool
}

// 写入 series 到倒排索引
func (p *MemPostings) Add(id storage.SeriesRef, lset labels.Labels) {
    for _, l := range lset {
        p.m[l.Name][l.Value] = append(p.m[l.Name][l.Value], id)
    }
}

// 查询：找到所有匹配 label 的 series ID 列表
func (p *MemPostings) Get(name, value string) []storage.SeriesRef {
    return p.m[name][value]
}
```

**数据结构本质**：一个两层 map——第一层 key 是 label name（如 `"job"`），第二层 key 是 label value（如 `"node"`），value 是 series ID 数组。所以 `Get("job", "node")` 的时间复杂度是 O(1)。

磁盘上的索引结构（`index/index.go`）更加复杂且精心优化：

```
Index File Structure:
┌─────────────────────┐
│ Symbol Table        │ ← 所有 label name/value 的字符串查找表，支持二分搜索
├─────────────────────┤
│ Series              │ ← 每条 series 的 labelset + chunk 引用信息
├─────────────────────┤
│ Postings Offset Tab │ ← 从 label 到 postings list 文件偏移量的快速映射
├─────────────────────┤
│ Postings            │ ← 实际存储的 series ID 列表（差分编码压缩）
├─────────────────────┤
│ TOC (Table of Contents) │ ← 各部分在文件中的偏移量，实现 mmap 后 O(1) 定位
└─────────────────────┘
```

通过 TOC 可以快速定位到任意 section 的文件偏移量，配合 mmap 实现零拷贝读取。

### 步骤3：追踪 `up{job="node"}` 的完整查询路径

打开 `tsdb/querier.go`，`Select` 方法是查询的入口：

```go
func (q *blockQuerier) Select(sortSeries bool,
    hints *storage.SelectHints, ms ...*labels.Matcher) storage.SeriesSet {
    // 1. 从倒排索引中获取匹配的 series ID 集合
    p, err := q.index.Postings(ms[0].Name, ms[0].Value) // "job", "node"

    // 2. 如果有多个 matcher（如 {job="node", instance="web-01"}），
    //    取多个 postings list 的交集
    for _, m := range ms[1:] {
        p2 := q.index.Postings(m.Name, m.Value)
        p = q.index.Intersect(p, p2) // 交集算法（双指针归并）
    }

    // 3. 遍历 series ID 列表
    var ss storage.SeriesSet
    for p.Next() {
        seriesID := p.At()
        // 4. 读取该 series 的 labelset（从 index 中查找）
        lset, chunks := q.index.Series(seriesID)
        // 5. 根据 hints 中的时间范围过滤 chunk
        // 6. 返回匹配的 chunks
    }
    return ss
}
```

查询的核心是**Postings List 求交集**——这是一项经典的倒排索引操作，和搜索引擎处理多关键词查询的原理完全相同。Intersect 使用双指针归并算法：两个已排序的 series ID 列表，同时遍历，遇到相同 ID 就输出，否则移动较小值那边的指针。时间复杂度 O(m+n)。

Postings list 之所以高效，还在于**差分编码压缩**：series ID 在 postings list 中是递增的，存储相邻 ID 的差值（delta）而非原始值，delta 通常很小，可以用更少的字节表示。例如 `[1, 5, 23, 100]` 存储为 `[1, 4, 18, 77]`，节省大量空间。

### 步骤4：Tombstone 的生命周期

打开 `tsdb/tombstones/tombstones.go`：

```go
type TombstoneReader interface {
    Get(id storage.SeriesRef) (Intervals, error) // 获取某 series 的被删除时间范围
}
```

Tombstone 在两个关键场景发挥作用：

```go
// 场景1：查询时过滤掉被删除的数据
func (q *blockQuerier) Select(...) {
    for p.Next() {
        intervals, _ := q.tombstones.Get(seriesID)
        // 根据 intervals 裁剪 chunk 列表 → 排除已删除的时间范围
    }
}

// 场景2：Compaction 时物理删除
func (c *LeveledCompactor) Compact(...) {
    for each series {
        if tombstones.IsFullyDeleted(seriesID, minTime, maxTime) {
            continue // 整个 series 被完全标记删除 → 不写入新 block
        }
        // 只写入未标记删除的 chunk
    }
}
```

Tombstone 的延迟删除哲学体现了存储系统的经典权衡：**用空间换安全**。立即物理删除需要重写整个 block（代价极高且容易出错），而 Tombstone 将删除推迟到 Compaction 这个"本来就该重写数据"的时机一并处理。

### 步骤5：手动观察 Compaction 行为

你可以写一个小工具用 Prometheus 的 tsdb 库手动触发 Compaction：

```go
func main() {
    db, _ := tsdb.Open("/path/to/data", nil, nil,
        tsdb.DefaultOptions())
    defer db.Close()

    // 强制执行 compaction
    if err := db.Compact(); err != nil {
        log.Fatal(err)
    }
}
```

不建议在生产环境手动触发，但测试环境可以观察：
- Compaction 前：`ls data/ | wc -l` → 大量小 block
- Compaction 后：block 数量明显减少
- 查询 `prometheus_tsdb_compactions_total` 观察 compaction 的触发频率

### 常见踩坑

1. **Compaction 磁盘空间不足**：合并过程中新旧 block 并存，磁盘占用临时翻倍。如果磁盘所剩无几，新 block 创建失败但旧 block 不会回滚——可能导致数据损坏。**建议预留 30% 以上的磁盘余量**。

2. **Index Symbol Table 中 label value 过长**：所有 label 的 name 和 value 都会进入 Symbol Table。如果某个 label value 特别长（如用 URL 作为标签值），会在内存产生大量字符串拷贝。**标签值尽量控制在 128 字节以内**。

3. **两个大 Postings list 求交集**：如果两个 label 的基数都非常高（百万级 series），Intersect 的双指针遍历会非常慢且消耗大量内存。**高基数 label 是查询性能的头号杀手**。

### 测试验证

```bash
# 使用 promtool 检查 block 重叠情况
promtool tsdb analyze /path/to/data/

# 观察 Prometheus 日志中的 compaction 事件
grep "compaction completed" prometheus.log

# 监控 compaction 指标变化
curl -s http://localhost:9090/metrics | grep compactions_total
```

---

## 四、项目总结

### Compaction 策略层次

```
Level 1 (最新)  → [Block A] [Block B] [Block C]  ← 超过3个且时间重叠 → 合并
                      ↓
Level 2         →        [Block ABC]              ← 与同层其他 block 继续合并
                      ↓
Level 3         →        [Block ABCD]
                      ↓
                    ...逐层上升...
```

每一层都有最大 block 数量限制，超限就触发合并。合并后的 block 进入下一层。这样既避免了频繁的全量重写，又保证了查询效率（block 总数可控）。

### 索引结构总览

```
查询 PromQL
    │
    ▼
倒排索引 (Postings Index)
    label:value → [seriesID₁, seriesID₂, ...]
    │
    ▼
正排索引 (Series Index)
    seriesID → labelset + [chunkRef₁, chunkRef₂, ...]
    │
    ▼
Chunk 文件
    chunkRef → 原始时序数据 (XOR 压缩)
```

### 查询路径全景

```
PromQL: up{job="node", instance="web-01"}
    │
    ├→ 1. Postings("job", "node")      → [1, 5, 23, 100]
    ├→ 2. Postings("instance", "web-01") → [5, 23, 200]
    ├→ 3. Intersect([1,5,23,100], [5,23,200]) → [5, 23]
    ├→ 4. Series(5)  → {labels, chunkRefs}
    ├→ 5. Filter chunks by time range (hints)
    └→ 6. Read chunk data → 返回时序数据
```

### 适用场景

- **诊断查询性能瓶颈**：当某条 PromQL 突然变慢，先检查是否涉及高基数 label 的 Postings Intersect
- **优化标签设计**：避免将高变化值（如 user_id、request_id）作为 label，它们会导致 series 爆炸和 Postings list 膨胀
- **规划存储容量**：根据 Compaction 的临时空间需求，预留足够磁盘余量

### 核心注意事项

1. **Compaction 的 I/O 开销**：合并大型 block 是纯 I/O 密集型操作，**强烈建议使用 SSD**，HDD 上可能让 Compaction 持续数小时
2. **标签设计决定索引效率**：高基数 label → Postings list 变大 → Intersect 变慢。每个 label value 对应一个独立的 postings list entry，这意味着 1000 个不同的 `instance` 值会产生 1000 条记录
3. **Tombstone 累积效应**：如果一个 block 中 80% 的数据被 tombstone 标记，查询时每次都要读取并过滤大量无效数据，**性能下降显著**

### 常见踩坑经验

**案例一：Compaction 失败导致 block 堆积。** 某集群的 Prometheus 数据目录下有 2000+ 个 block 未合并，查询延迟超过 10 秒。排查发现磁盘空间不足，Compaction 创建新 block 时失败，但没有错误重试机制导致旧 block 不断堆积。**解决**：扩容磁盘后手动调用 `db.Compact()`，block 数量从 2000+ 降至 200 以内。

**案例二：倒排索引内存爆炸。** 某业务将 trace_id 设置为 label，导致 series 数量突破 1000 万。MemPostings 的 map 结构急剧膨胀，Prometheus OOM 频繁重启。**解决**：移除高基数 label，series 降至 10 万，内存恢复稳定。**教训**：永远不要把 request_id、trace_id 这类高度唯一的值作为 label。

**案例三：Tombstone 累积拖慢查询。** 某团队大量删除过期 series 的 API 数据（每秒数万条），tombstone 文件迅速膨胀至 GB 级。查询时每次都要遍历庞大的 tombstone 列表过滤数据，P99 延迟飙升到 30 秒。**解决**：强制触发 Compaction 物理清理 tombstone 数据后恢复正常。**教训**：大量删除后务必确认 Compaction 已执行完毕。

### 思考题

1. **如果有两个 label matchers，一个是高基数（1000 万条 series），一个是低基数（10 条 series），Postings Intersect 应该从哪个 list 开始遍历？为什么？**

   *提示：考虑 Intersect 算法的复杂度与两个列表长度的关系。*

2. **Prometheus 的 Compaction 策略为什么不直接合并所有 block 成一个大的？**

   *提示：从写入放大（Write Amplification）、查询模式、以及"时间局部性"的角度思考。*
