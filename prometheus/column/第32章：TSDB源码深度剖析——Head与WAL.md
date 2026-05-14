# 第32章：TSDB源码深度剖析——Head与WAL

> 高级篇开篇 | 源码级理解Prometheus存储引擎的心脏

---

## 一、项目背景

某大型互联网公司的Prometheus集群承载着500万active series的监控数据。运维团队发现了一个令人困惑的内存问题：Prometheus的运行内存长期稳定在32GB，但每当有一个新的exporter上线（新增约10万series），内存便会瞬间飙升到38GB，随后缓慢回落至34GB——再也回不到32GB。几周后，Prometheus毫无征兆地触发OOM被Kubernetes杀死。重启后，WAL回放长达30分钟，业务监控出现严重空白。

运维团队百思不得其解：为什么内存"只增不减"？既然老数据已经刷到磁盘Block，Head中过期的series为何不释放？WAL回放30分钟到底在干什么？

答案全部藏在TSDB的源码深处——Head Block的memSeries生命周期管理、WAL的Record编码格式，以及mmap的内存映射策略。本章作为高级篇的开篇，将带领读者从源码维度解剖Prometheus TSDB最核心的两个数据结构：Head和WAL。

Head Block是整个TSDB的"心脏"——所有采集到的数据首先写入Head（纯内存），再由compaction机制沉淀为磁盘上的Persistent Block。WAL（Write-Ahead Log）则是灾难恢复的基石，确保在任何崩溃场景下数据不丢失。深入理解headAppender的写入路径、memSeries的哈希表结构、WAL的三种Record类型以及Checkpoint机制，方能真正掌握Prometheus的性能调优与故障诊断能力。

本章不仅是Prometheus源码阅读的起点，更是从"会用Prometheus"跨越到"理解Prometheus"的分水岭。我们将从TSDB最核心的两个数据结构开始，逐步揭开500万series场景下那些隐蔽的性能陷阱。

---

## 二、剧本式交锋对话

**小胖**（运维工程师，满头大汗地跑进监控室）：大师救命！Prometheus又OOM了，这次重启WAL回放了快30分钟，业务方已经炸锅了！我看了看监控，内存长期32GB，只要新上个exporter就往上涨，掉不下来——这内存都去哪了啊？

**大师**（抿了一口茶）：你先告诉我，Prometheus的数据写入流程你清楚吗？

**小白**（SRE新人，抢答道）：我来！数据从exporter采集过来，先写内存里的Head Block，然后每2小时compaction成磁盘上的Block，最后被TSDB持久化。WAL是用来防崩溃的，先写日志再改内存。

**大师**：不错，框架对了，但细节才是魔鬼。Head Block本质上是一个完整的迷你TSDB——它在内存里维护了一张哈希表（series索引）、一组内存chunk（热数据）、以及WAL（灾备）。你刚才说"先写日志再改内存"——这只是故事的一半。

**小胖**：那另一半是什么？跟内存不回收有什么关系？

**大师**：Head中的每一根series，从它被"注册"进哈希表的那一刻起，就再也没有主动释放过——除非它变成"stale"且超过一定时间不再有新数据写入。你们那个exporter一上线就是10万新series，内存当然飙6GB。但exporter下线之后，这些series不会立刻被清除，GC周期很长，所以内存只回落到34GB而非32GB。这就是Head中memSeries的生命周期——**注册即常驻**。

**小白**：等等，那每个series到底占多大内存？10万series真的能吃6GB？

**大师**：好问题。我们看memSeries结构体。一个memSeries除了labelset、chunks数组外，还关联了倒排索引中的postings。labelset是一组key-value对，按go的`string`类型存储，堆上分配。假设一个series平均10个label，每个label 40字节，加上map overhead——单series轻松80-100字节。再算上memChunk（每个chunk 1024字节）、mmap映射描述符（~64字节）——一条series 200字节起跳。10万series就是20MB的"净"占用。但go的内存分配器不会立刻归还给OS（因为用`runtime.MemStats`看是堆内存，但从OS看是RSS），加上Prometheus内部的大量临时分配和GC延迟——6GB的RSS增长并非不合理。

**小胖**：我理解了内存的问题。那WAL回放为什么这么慢？我们的WAL segment文件有快1000个，每个128MB…光读取就得一阵子。

**大师**：这就是Checkpoint机制没有发挥作用的典型症状。你把WAL想象成"前台服务台"的备用电池。Head = 前台服务台，所有新业务都在服务台处理，快速响应；WAL = 备用电池（断电后服务台的数据不丢）；Persistent Block = 后台档案柜（每2小时归档一次）。正常运行时，前台服务台每处理一个请求，备用电池就闪烁一次（写WAL）。每过一段时间，将当前服务台的完整状态拍张快照（Checkpoint），旧的电池记录就可以回收了。

**小胖**：你的意思是，我们的Checkpoint没有正常触发？

**大师**：大概率是。WAL的Checkpoint触发有两个条件：segment数量超阈值，或者Head被compaction成新的Persistent Block。如果你们的Prometheus因为内存紧张导致compaction变慢，Checkpoint迟迟不触发，WAL segment越积越多，回放时就需要重放所有segment——30分钟已经算快的了。

**小白**：我突然想到，Head中的chunk数据是不是不全在内存里？

**大师**：正是。Head中有两种chunk：`headChunk`（当前正在写入的，纯内存，热数据）和`mmappedChunks`（已经满了的旧chunk，通过mmap映射到磁盘，按需加载）。mmap的精妙之处在于——操作系统只在你真正读取某段数据时才从磁盘加载到page cache，平时只是一段虚拟地址映射。这就是"温数据"的存储策略：不占你的go堆内存，但想读的时候随时能读到。

**小胖**（若有所思）：所以核心问题有两个：第一，series注册后一直在哈希表里，没有主动清理机制；第二，WAL Checkpoint不正常导致segment堆积。那我们应该怎么解决？

**大师**：短期方案——手动触发compaction，加速Checkpoint；长期方案——控制label基数，避免explosive series增长。至于源码层面，我们接下来就逐段解剖headAppender的写入路径。

---

## 三、项目实战

### 环境准备

- Prometheus源码：`git clone https://github.com/prometheus/prometheus`
- Go 1.21+
- 核心阅读路径：`tsdb/head.go`、`tsdb/head_append.go`、`tsdb/wal/wal.go`、`tsdb/wal/reader.go`、`tsdb/record/record.go`
- 能编译调试：`make build`

### 步骤1：阅读Head Block的核心数据结构

打开 `tsdb/head.go`，定位到以下关键结构体：

```go
// Head结构体（简化）
type Head struct {
    chunkRange    int64           // Head覆盖的时间范围（默认2h）
    minTime, maxTime int64        // Head中数据的时间范围
    postings *index.MemPostings   // 倒排索引（label → series IDs）
    series    *stripeSeries       // 分片的series哈希表（减少锁竞争）
    samples   *memChunkPool       // 空闲chunk池（避免频繁GC）
}

// memSeries 结构体（简化）
type memSeries struct {
    ref  chunks.HeadSeriesRef    // series唯一ID
    lset labels.Labels           // 标签集
    mmappedChunks []*mmappedChunk // mmap映射的chunk（已满的旧chunk）
    headChunk     *memChunk      // 当前正在写入的chunk
    sampleBuf     [4]sample      // 缓冲最近4个sample
    pendingCommit bool           // 是否有未commit的数据
}

// memChunk结构体（简化）
type memChunk struct {
    chunk    *chunkenc.XORChunk  // XOR压缩的chunk实际数据
    minTime, maxTime int64       // chunk内数据时间范围
}
```

**逐段解读：**

- **stripeSeries为何分片？** Go原生的`map`不是并发安全的。Prometheus的Head同时面对读查询和写追加，如果用一个`sync.RWMutex`保护整张map，写锁会阻塞所有查询。stripeSeries将哈希表切分为N个"stripe"（分片），每个stripe独立持锁——写入只锁目标stripe，其他stripe的查询不受影响。这是读多写少场景的经典优化。

- **mmappedChunks与headChunk的分工**：`headChunk`是当前正在收数据的chunk，位于go堆上，纯内存访问。一旦chunk写满（120个sample或达到1024字节），就被转移到`mmappedChunks`切片中，并通过mmap映射到磁盘文件。此时该chunk不再占用go堆内存，但操作系统page cache仍持有热数据。这解释了为什么Prometheus的RSS可能远大于go的堆内存。

- **sampleBuf为何存4个sample？** `rate()`、`irate()`、`delta()`等PromQL函数至少需要2个相邻数据点来计算斜率。存4个而非2个，是因为chunk边界处经常需要"跨界"读取——当前headChunk的最后一点和上一个mmapChunk的最后一点共同计算rate。多留2个sample做缓冲，避免每次都去触发mmap page fault。

- **pendingCommit标记**：新数据写入headChunk后，对应的WAL Record尚未落盘时置为true。Commit完成后置为false。这个标记防止在WAL未落盘的情况下，因不安全的compaction导致数据丢失。

### 步骤2：追踪headAppender的写入路径

打开 `tsdb/head_append.go`，核心方法是 `headAppender.Append()`：

```go
func (a *headAppender) Append(ref storage.SeriesRef,
    lset labels.Labels, t int64, v float64) (storage.SeriesRef, error) {
    s := a.head.series.getByID(chunks.HeadSeriesRef(ref))
    if s == nil {
        // 新series：在哈希表中创建
        s = a.head.getOrCreate(lset.Hash(), lset)
    }
    // 检查timestamp是否单调递增
    if t <= s.maxTime {
        return 0, storage.ErrOutOfOrderSample
    }
    // 写入headChunk
    ok, _ := s.append(t, v, a.appendID, a.head.chunkDiskMapper)
    if !ok {
        // chunk满了，切新的
        s.cutNewHeadChunk(t, a.head.chunkDiskMapper)
    }
    // 记录到WAL sample batch
    a.sampleSeries = append(a.sampleSeries, record.RefSample{
        Ref: chunks.HeadSeriesRef(s.ref), T: t, V: v,
    })
    return storage.SeriesRef(s.ref), nil
}
```

**核心逻辑链条：**

1. **Ref查找**：如果`ref=0`表示新series，走`getOrCreate`分支——计算labelset的hash，定位到对应的stripe，加锁后插入哈希表。如果ref非零，直接通过`getByID`用series ID查找已存在的memSeries。

2. **乱序检查**：`t <= s.maxTime`——Prometheus严格要求时间戳单调递增。同等时间戳的重复写入会被直接拒绝。

3. **chunk写入与切分**：`s.append()`尝试将sample写入当前headChunk。XOR压缩的特性决定了chunk容量是自适应的——波动剧烈的时间序列sample体积大（XOR delta大），一个chunk能装的sample就少；平稳序列能装更多。

4. **WAL batch积累**：sample不立即写WAL，而是累积在`a.sampleSeries`切片中。等到`Commit()`调用时，将所有pending的series和samples批量编码为WAL Record，一次fsync写入。

如果在生产环境中需要诊断写入问题，可以在关键路径加入日志：

```go
level.Debug(a.head.logger).Log("msg", "sample appended",
    "series", s.lset.String(), "t", t, "v", v)
```

### 步骤3：理解WAL的Record格式

打开 `tsdb/wal/wal.go` 和 `tsdb/record/record.go`：

```go
// WAL Record类型
const (
    RecordSeries    = 1  // 注册新的series（labelset → series ID映射）
    RecordSamples   = 2  // 写入sample数据
    RecordTombstones = 3 // 标记删除
)

// 写入WAL的流程（简化）
func (w *WAL) Log(recs ...[]byte) error {
    // 1. 编码为二进制：type(1byte) + length(varint) + data
    // 2. 写入当前segment文件（默认128MB一个segment）
    // 3. 如果当前segment满了，切到新segment
    // 4. 调用fsync刷盘
}
```

WAL Record的二进制格式（Length-delimited encoding）：

```
[RecType: 1byte] [RecLength: varint] [RecData: N bytes] [CRC32: 4bytes]
```

- **RecType**：1字节，标记Record类型（1=Series, 2=Samples, 3=Tombstones）
- **RecLength**：varint编码，表示RecData的字节长度
- **RecData**：实际负载数据（Series记录是labelset的序列化，Samples记录是多个RefSample的结构化编码）
- **CRC32**：4字节校验和，用于检测磁盘位翻转或corruption

**为什么不用Protobuf？** WAL追求极致性能——protobuf需要引入外部依赖和额外的编解码开销，而length-delimited format是一种极简的自描述格式：只需要一个byte标识类型，一个varint标识长度，后面跟原始二进制。解析时直接用`binary.Read`操作字节流，零拷贝，零反射。

### 步骤4：Checkpoint机制分析

打开 `tsdb/wal/checkpoint.go`：

Checkpoint的触发条件：
- WAL segment文件数量超过`DefaultSegmentsMax`（默认5个）时自动触发
- Head Block被compaction为Persistent Block时强制触发

Checkpoint的生成内容：
```
1. 遍历Head中所有存活的memSeries
2. 将每个series的当前状态（labelset + series ID）序列化写入checkpoint
3. 将每个series中尚未被flushed的samples写入checkpoint
4. 删除所有早于checkpoint覆盖范围的WAL segment文件
```

Checkpoint的效果：假设Prometheus运行了24小时，每2小时compaction一次，WAL累计了12个segment。如果不做Checkpoint，重启时需要从头回放所有12个segment；有了Checkpoint后，只需要从最后一次Checkpoint开始回放——通常只有1-2个segment。这就是为什么WAL回放时间可以从30分钟缩短到几分钟。

一个关键细节：Checkpoint本身也是一个WAL文件（格式相同），回放逻辑统一处理——先恢复Checkpoint中的series和samples，再重放后续的增量WAL segment。

### 步骤5：编写Go程序读取WAL文件

利用Prometheus的tsdb库编写一个简易的WAL reader：

```go
package main

import (
    "fmt"
    "os"

    "github.com/prometheus/prometheus/tsdb/record"
    "github.com/prometheus/prometheus/tsdb/wal"
)

func main() {
    if len(os.Args) < 2 {
        fmt.Fprintf(os.Stderr, "Usage: %s <wal-segment-file>\n", os.Args[0])
        os.Exit(1)
    }

    seg, err := wal.OpenReadSegment(os.Args[1])
    if err != nil {
        panic(err)
    }
    defer seg.Close()

    r := wal.NewReader(seg)
    defer r.Close()

    var dec record.Decoder
    var seriesCount, sampleCount int

    for r.Next() {
        rec := r.Record()
        switch dec.Type(rec) {
        case record.Series:
            series, err := dec.Series(rec, nil)
            if err != nil {
                fmt.Fprintf(os.Stderr, "decode series error: %v\n", err)
                continue
            }
            seriesCount += len(series)
            for _, s := range series {
                fmt.Printf("Series: ref=%d labels=%s\n", s.Ref, s.Labels)
            }
        case record.Samples:
            samples, err := dec.Samples(rec, nil)
            if err != nil {
                fmt.Fprintf(os.Stderr, "decode samples error: %v\n", err)
                continue
            }
            sampleCount += len(samples)
            fmt.Printf("Samples: %d records\n", len(samples))
        case record.Tombstones:
            fmt.Println("Tombstones record found")
        }
    }

    fmt.Printf("\nSummary: %d series, %d samples\n", seriesCount, sampleCount)
}
```

运行方式：

```bash
go run wal_reader.go /path/to/prometheus/data/wal/00000001
```

输出示例：

```
Series: ref=1 labels={__name__="http_requests_total", job="api", instance="10.0.0.1:8080"}
Series: ref=2 labels={__name__="http_request_duration_seconds", job="api", instance="10.0.0.1:8080"}
Samples: 120 records
Samples: 115 records
...
Summary: 250 series, 15000 samples
```

通过这个小工具，你可以亲眼看到WAL中存储的每条Series注册记录和Sample数据，验证自己对WAL格式的理解。

### 可能遇到的坑

1. **WAL segment文件是二进制格式**，不能用文本编辑器直接打开——使用`wal_reader`或`promtool tsdb dump`命令解析。

2. **修改Prometheus源码时**，Go module依赖关系可能让你头疼。修改完源码后务必执行`go mod tidy`同步依赖，并注意官方库的版本兼容性——v2.45.0之前和之后的TSDB API有显著差异。

3. **stripeSeries的锁模型**：每个stripe独立持锁（`sync.RWMutex`），跨stripe操作时必须注意加锁顺序。例如`getOrCreate`先在stripe A加锁查找，没找到再去stripe B创建——此时若另一个goroutine在stripe B持有写锁并试图跨到stripe A，死锁就发生了。Prometheus源码通过保证锁顺序（总是按stripe index从小到大）避免了这一问题。

4. **WAL回放时的corrupt record**：如果WAL segment文件存在物理损坏（如磁盘坏道），解码时CRC32校验失败会直接导致回放中断。应急方案是手动删除该segment（代价是丢失segment内尚未被checkpoint覆盖的series/samples），或者使用`promtool tsdb dump`跳过损坏记录。

### 测试验证

- 运行wal_reader工具，输出WAL中的Record统计，确认编码格式理解正确。
- 实时观察memSeries数量：

```bash
curl http://localhost:9090/api/v1/status/tsdb | jq '.data.headStats.numSeries'
```

- 估算Head chunk总数：`numSeries × avgChunksPerSeries`，与`headStats.numSeries`和磁盘上的chunk文件数量对比验证。

---

## 四、项目总结

### Head Block架构全景

文本描述的Head Block分层架构：

```
┌─────────────────────────────────────────────────────┐
│                  headAppender                        │
│          (接收scrape sample的入口)                    │
└──────────────────────┬──────────────────────────────┘
                       │ Append(timestamp, value)
                       ▼
┌─────────────────────────────────────────────────────┐
│              stripeSeries 哈希表                      │
│   stripe[0] → memoSeries{A, B} (lock[0])             │
│   stripe[1] → memoSeries{C, D} (lock[1])             │
│   ...                                                │
│   stripe[N] → memoSeries{X, Y} (lock[N])             │
└────────┬──────────────────────────────┬──────────────┘
         │                              │
         ▼                              ▼
   ┌──────────┐                 ┌──────────────┐
   │ headChunk│ (热数据·纯内存)   │ mmappedChunks│ (温数据·mmap)
   │ 当前写入  │                 │ 已满旧chunk   │
   └──────────┘                 └──────┬───────┘
                                       │ compaction (每2h)
                                       ▼
                              ┌──────────────────┐
                              │ Persistent Block │ (冷数据·磁盘)
                              │  索引+chunks      │
                              └──────────────────┘
```

### WAL生命周期时间线

```
Sample到达 → headAppender.Append()
    │
    ▼
WAL Record生成（编码Series/Samples）
    │
    ▼
写入当前Segment文件
    │
    ├── Segment未满（<128MB）→ 继续追加
    │
    └── Segment已满 → 创建新Segment（00000001 → 00000002）
            │
            ▼
    Segment数量 > 阈值（默认5个）
            │
            ▼
    Checkpoint生成（快照当前Head状态）
            │
            ▼
    旧Segment被删除（数据已在Checkpoint或Persistent Block中）
```

### 关键源码文件索引

| 文件 | 核心函数/结构体 | 职责 |
|------|----------------|------|
| `tsdb/head.go` | `Head`, `stripeSeries`, `memSeries`, `memChunk` | Head Block的顶层结构和内存索引 |
| `tsdb/head_append.go` | `headAppender.Append()`, `headAppender.Commit()` | 写入路径的核心逻辑 |
| `tsdb/wal/wal.go` | `WAL.Log()`, `WAL.NextSegment()` | WAL的写入和segment管理 |
| `tsdb/wal/reader.go` | `Reader.Next()`, `Reader.Record()` | WAL的读取接口 |
| `tsdb/wal/checkpoint.go` | `Checkpoint()` | Checkpoint的生成逻辑 |
| `tsdb/record/record.go` | `RecordSeries`, `RecordSamples`, `Decoder` | Record的编解码定义 |

### 适用场景

- 深度调优TSDB写入性能（调整stripeSize、chunkRange等参数）
- 诊断Prometheus OOM问题（分析memSeries和labelset的内存占用）
- 优化WAL回放速度（配置Checkpoint触发策略）
- 二次开发自定义exporter适配TSDB写入路径
- 编写WAL数据分析工具（如监控WAL膨胀速率）

### 注意事项

- **锁模型是核心**：修改Head相关源码前，必须先理解stripeSeries的分片锁模型。跨stripe操作是死锁的高发区。
- **WAL格式随版本演变**：不同版本的Prometheus可能在WAL Record格式上有细微差异（如新增字段），跨版本升级时注意兼容性。
- **labelset内存占用是OOM的常见元凶**：高基数label（如`user_id`、`request_id`）会让memSeries数量爆炸式增长——500万series如果每个占用200字节，仅memSeries本身就有1GB，算上倒排索引的postings开销轻松2-3GB。

### 常见踩坑经验

**案例1：高基数label导致memSeries膨胀**

某团队将HTTP请求的`request_id`作为label暴露，每个请求生成一个不同的series。Prometheus在业务高峰期瞬间涌入100万新series，哈希表扩容导致STW（Stop The World）超过500ms，后续GC压力巨大。排查思路：通过`promtool tsdb analyze`检查label基数分布，将高基数label移除或用`relabel_config`丢弃。

**案例2：WAL回放时内存翻倍**

WAL回放过程中，Head需要重建所有memSeries。此时新写入正在追加到Head，回放逻辑又在创建series——两股写入流叠加，内存峰值远超正常运行。某团队500万series场景下，回放期间内存峰值达到60GB（正常32GB），直接再次触发OOM。解决方案：在Prometheus启动参数中限制`--storage.tsdb.wal-segment-size`，或者增加节点内存，或者在回放期间暂时停止scrape。

**案例3：stripeSize设置不当导致锁竞争**

默认stripeSize为16384。如果机器有64核CPU，大量查询并发访问stripeSeries时，16384个stripe足够分散锁竞争。但如果手动设置为1（退化为单锁），读查询会在哈希表上排队，P99查询延迟可达秒级。建议stripeSize保持在默认值，或根据CPU核数微调（2的幂次，不偏离默认值太多）。

### 思考题

1. Head中的stripeSeries分片数默认是多少？分片数太多或太少各有什么问题？请结合go的`sync.RWMutex`机制和CPU缓存行（cache line）原理进行分析。

2. WAL的Checkpoint和传统数据库（如MySQL InnoDB）的Checkpoint在概念上有哪些相似和不同之处？Prometheus的Checkpoint为什么可以设计得比数据库的Checkpoint更"轻量"？

---

*本章是高级篇的开端，后续章节将深入compaction机制、倒排索引构建、以及PromQL查询执行的源码细节。建议读者在阅读本章后，实际clone Prometheus源码，用dlv或gdb设置断点，跟踪一次完整的写入流程——从headAppender.Append()到WAL.Log()，亲眼见证数据在Head与WAL之间的流动。源码即是文档，代码不会说谎。*
