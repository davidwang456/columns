# 第34章：DataCoord 与 DataNode 写入源码

> **定位**：深入写入、Flush、Segment 生命周期的实现。
> **版本**：Milvus 2.5.x
> **源码关联**：internal/datacoord/server.go、internal/datanode/data_sync_service.go、internal/datanode/flush_manager.go

---

## 1. 项目背景

核心开发小陈接到一个 Bug：在 `auto_id=True` 的 Collection 上执行 `Insert` 后，SDK 返回的 ID 列表与实际写入的主键不一致。他发现返回的 ID 是 Proxy 向 RootCoord 申请的，但实际上写入 DataNode 时 DataNode 又自己分配了一套 ID。

这个 Bug 揭示了写入链路中一个重要设计细节：**ID 分配和写入是两个独立步骤**——Proxy 负责预分配 ID，DataNode 负责实际写入。如果这两个步骤的数据对齐出问题（比如 Proxy 申请的 ID 被丢弃了一部分），就会出现"返回的 ID 在 Milvus 中查不到"的幽灵记录。

本章将深入 DataCoord（Segment 分配、Flush 调度、Compaction 触发）和 DataNode（消息消费、写入缓冲、Binlog 生成）的源码实现。

---

## 2. 项目设计（剧本式交锋对话）

**第一幕：DataCoord——数据协调的大脑**

*（小陈在 DataCoord 的日志里打印了 Segment 分配的全过程）*

**小胖**："DataCoord 不就是管 Segment 分配的吗？跟 RootCoord 控元数据、QueryCoord 控搜索比起来，它不就是个仓库管理员？"

**大师**："仓库管理员没错——但仓库管理是门学问。DataCoord 管着三个最难的事——"

**大师**（画出 DataCoord 职责图）：

```
DataCoord 四大职责:

┌─────────────────────────────────────────────────────────────┐
│                      DataCoord                              │
│                                                             │
│  ① Segment 分配                                             │
│     ✓ 为新 Channel 分配新的 Growing Segment                 │
│     ✓ 监控 Segment 大小，达到阈值触发分配新 Segment           │
│     ✓ 记录 Segment → DataNode 的映射关系                     │
│     ↘ key 函数: AssignSegmentID()                           │
│                                                             │
│  ② Channel 管理                                              │
│     ✓ 每个 Shard 对应一个 Channel (Pulsar Topic)             │
│     ✓ Channel → DataNode 分配 (哪个 DataNode 消费哪个 Topic) │
│     ↘ key 函数: WatchChannels() / BalanceChannels()         │
│                                                             │
│  ③ Flush 调度                                               │
│     ✓ 检测 Growing Segment 是否达到 Flush 阈值               │
│     ✓ 触发 Flush → DataNode 序列化 → 生成 Binlog             │
│     ✓ 等待 Flush 完成后更新 Segment 状态为 Sealed            │
│     ↘ key 函数: TriggerFlush()                              │
│                                                             │
│  ④ Compaction 调度                                           │
│     ✓ 检测 Sealed Segment 数量是否超标                       │
│     ✓ 触发 Compaction → 合并多个小 Segment                   │
│     ✓ 完成后将旧 Segment 标记为 Dropped → 等待 GC             │
│     ↘ key 函数: TriggerCompaction()                         │
└─────────────────────────────────────────────────────────────┘
```

**大师**："DataCoord 的核心是一个有限状态机（FSM）——"

```
Segment 状态机 (DataCoord 管理):

                写入数据            
  ┌──────────┐ ───────→ ┌──────────┐  Flush    ┌──────────┐
  │ 创建      │          │ Growing  │ ────────→ │  Sealed  │
  │ (初始)    │          │ (可写入)  │            │ (已封存)  │
  └──────────┘          └──────────┘            └────┬─────┘
                                                     │
                                    Compaction ──────┤
                                                     │
                                         ┌───────────▼─────────┐
                                         │    Compacted         │
                                         │ (新的大Segment)       │
                                         └─────────────────────┘
                                         旧 Segment → Dropped → GC
```

> **技术映射**：DataCoord = 仓库调度中心（管货位分配、盘点、合并）；Channel = 传送带编号（每条传送带独立运转）；Segment 状态机 = 货物的生命周期（入库→上架→盘点→合并→清理）。

---

**第二幕：DataNode 的写入流程——从 MQ 到 Binlog**

**小白**："DataNode 内部到底是怎么把数据从 MQ 消费过来并落盘的？"

**大师**："DataNode 内部是一个流水线——"

```go
// internal/datanode/data_sync_service.go (简化逻辑)

type DataSyncService struct {
    channelName  string
    consumer     mq.Consumer          // MQ 消费者 (Pulsar/Kafka)
    writeBuffer  *WriteBuffer          // 写入缓冲区
    flushManager *FlushManager         // Flush 管理器
}

// Start — DataNode 的主循环
func (ds *DataSyncService) Start(ctx context.Context) {
    // 循环消费 MQ 消息
    for {
        // Step 1: 从 MQ 拉取一批消息
        msgs, err := ds.consumer.Consume(ctx, 1000)  // 每次最多1000条
        if err != nil {
            log.Error("消费消息失败", zap.Error(err))
            continue
        }
        
        // Step 2: 解析 protobuf → InsertMsg
        for _, msg := range msgs {
            insertMsg := parseInsertMessage(msg)
            
            // Step 3: 写入 Buffer (按 Segment ID 分组)
            // 同一个 Segment 的数据聚合在一起
            ds.writeBuffer.Append(insertMsg.GetSegmentID(), insertMsg)
            
            // Step 4: 检查是否需要 Flush
            if ds.writeBuffer.Size(insertMsg.GetSegmentID()) >= ds.flushThreshold {
                // 异步触发 Flush
                go ds.flushManager.Flush(ctx, insertMsg.GetSegmentID())
            }
        }
        
        // ACK 消息 (确认消费)
        ds.consumer.Ack(msgs)
    }
}

// FlushManager.Flush — 将内存数据序列化落盘
func (fm *FlushManager) Flush(ctx context.Context, segID int64) error {
    // Step 1: 从 WriteBuffer 取出该 Segment 的所有数据
    data := fm.writeBuffer.Pop(segID)
    
    // Step 2: 按列序列化 → Binlog 格式
    binlogPath := fmt.Sprintf("bucket/seg_%d/binlog", segID)
    binlogWriter := NewBinlogWriter(binlogPath)
    for _, col := range data.Columns() {
        binlogWriter.WriteColumn(col.Name(), col.Values())
    }
    
    // Step 3: 上传 Binlog 到对象存储 (MinIO/S3)
    if err := fm.storage.Upload(ctx, binlogPath, binlogWriter.Bytes()); err != nil {
        return err
    }
    
    // Step 4: 通知 DataCoord Flush 完成
    fm.dataCoord.NotifyFlushCompleted(ctx, segID, binlogPath)
    
    log.Info("Flush 完成", zap.Int64("segID", segID),
        zap.Int("rows", data.RowCount()))
    return nil
}
```

**大师**："关键设计决策——"

| 设计 | 位置 | 原因 |
|------|------|------|
| 批量消费 MQ | `Consume(ctx, 1000)` | 减少网络 IO，每次拉 1000 条 |
| WriteBuffer 按 Segment 分组 | `Append(segID, msg)` | 同一 Segment 的数据一起落盘 |
| 异步 Flush | `go fm.flushManager.Flush()` | Flush 慢（写 S3），不能阻塞消息消费 |
| Binlog 按列存储 | `WriteColumn()` | 搜索时只加载向量列，不加载标量列 |

> **技术映射**：MQ 消费 = 从传送带上取包裹；WriteBuffer = 分拣台（同目的地的包裹放一起）；Flush = 装上货车发往仓库（序列化+写对象存储）。

---

**第三幕：写入积压和 Flush 慢的源码级排查**

**小胖**："写入延迟偶尔飙到 10 秒——怎么排查是 DataNode 的问题还是对象存储的问题？"

**大师**："三步排查法——"

```
写入延迟排查决策树:

Step 1: 看 DataNode 的消费延迟
  指标: milvus_datanode_consume_lag
  ├─ lag 正常 (< 1000) → Step 2
  └─ lag 偏高 (> 10000) → 问题在消费端
      解决: ① 加 DataNode ② 增加 consumer fetch 批量 ③ 检查 MQ 负载

Step 2: 看 Flush 延迟
  指标: milvus_datanode_flush_latency (P95)
  ├─ P95 < 2s (正常) → Step 3
  └─ P95 > 5s → 问题在 Flush 过程
      子排查:
      ├─ 序列化慢? → 检查 Segment 是否太大 (减小 maxSize)
      ├─ 上传慢? → 检查对象存储的写延迟 (网络/磁盘)
      └─ 通知 DataCoord 慢? → 检查 DataCoord 负载

Step 3: 看对象存储
  指标: MinIO/S3 的 PutObject 延迟
  ├─ P95 < 100ms → 问题不在存储
  └─ P95 > 500ms → 对象存储 IO 瓶颈
      解决: ① 升级存储磁盘 ② 检查网络带宽 ③ 增大 DataNode 写缓冲
```

**大师**："最容易被忽略的坑——`segment.maxSize` 设太大。默认 1GB 看起来不多，但 Flush 时要序列化整个 Segment 到内存，1GB 数据序列化 + 上传可能需要 5-10 秒。"

---

## 3. 项目实战

### 3.1 实战目标

在 DataNode 写入路径增加调试日志，观察 1 万条数据从消息到 Binlog 的过程。

### 3.2 分步实现

#### 步骤 1：DataNode 写入路径日志增强

```go
// 在 internal/datanode/data_sync_service.go 中添加追踪日志

func (ds *DataSyncService) traceConsume(ctx context.Context, msgs []Message) {
    log := log.Ctx(ctx)
    if len(msgs) == 0 {
        return
    }
    
    t0 := time.Now()
    
    // 解析消息
    for _, msg := range msgs {
        insert := parseInsertMessage(msg)
        ds.writeBuffer.Append(insert.GetSegmentID(), insert)
        
        // 每 1000 条打一次日志
        if ds.writeBuffer.RowCount() % 1000 == 0 {
            log.Info("[DataNode-Trace] 缓冲区状态",
                zap.Int("totalRows", ds.writeBuffer.RowCount()),
                zap.Int("segments", ds.writeBuffer.SegmentCount()),
                zap.Int64("biggestSeg", ds.writeBuffer.BiggestSegID()),
            )
        }
    }
    
    // ACK
    ds.consumer.Ack(msgs)
    
    log.Info("[DataNode-Trace] 消费完成",
        zap.Int("msgCount", len(msgs)),
        zap.Duration("cost", time.Since(t0)))
}

// 在 FlushManager 中添加追踪
func (fm *FlushManager) traceFlush(ctx context.Context, segID int64, data *BufferData) error {
    log := log.Ctx(ctx)
    t0 := time.Now()
    
    log.Info("[DataNode-Trace] Flush 开始",
        zap.Int64("segID", segID),
        zap.Int("rows", data.RowCount()),
        zap.Int("columns", len(data.Columns())))
    
    // 序列化
    t1 := time.Now()
    binlog := serializeToBinlog(data)
    log.Info("[DataNode-Trace] 序列化完成",
        zap.Duration("cost", time.Since(t1)),
        zap.Int("binlogSize", len(binlog)))
    
    // 上传
    t2 := time.Now()
    if err := fm.storage.Upload(ctx, binlogPath, binlog); err != nil {
        return err
    }
    log.Info("[DataNode-Trace] 上传完成",
        zap.Duration("cost", time.Since(t2)))
    
    log.Info("[DataNode-Trace] Flush 完成",
        zap.Duration("totalCost", time.Since(t0)))
    
    return nil
}
```

#### 步骤 2：用 PyMilvus 触发批量写入并观察日志

```python
# step2_observe_flush.py
"""批量写入并观察 DataNode 日志"""
from pymilvus import connections, Collection, utility
import numpy as np
import time

connections.connect(host="localhost", port="19530")
collection = Collection("bench_1m")

# 批量插入 10000 条
N = 10000
ids = [int(time.time() * 1000) + i for i in range(N)]
vecs = np.random.rand(N, 768).astype(np.float32).tolist()

print(f"开始插入 {N} 条数据...")
t0 = time.time()
collection.insert([ids, vecs])
insert_time = time.time() - t0
print(f"Insert 完成: {N/insert_time:.0f} 条/s")

# Flush
print("执行 Flush...")
t0 = time.time()
utility.flush([collection.name])
flush_time = time.time() - t0
print(f"Flush 完成: {flush_time:.1f}s")

print(f"\n> 预期 DataNode 日志输出:")
print(f"  [DataNode-Trace] 缓冲区状态: totalRows=...")
print(f"  [DataNode-Trace] Flush 开始: segID=... rows=...")
print(f"  [DataNode-Trace] 序列化完成: cost=...ms")
print(f"  [DataNode-Trace] 上传完成: cost=...ms")
```

#### 步骤 3：写入延迟分解分析

```python
# step3_write_latency_breakdown.py
"""写入延迟分解工具"""
WRITE_PIPELINE = """
写入延迟全链路分解:

┌─────────────────┬──────────┬─────────────────────┐
│ 阶段             │ 典型耗时  │ 观测方式             │
├─────────────────┼──────────┼─────────────────────┤
│ ① SDK → Proxy   │ ~0.5ms   │ SDK 端 t0→t1        │
│ ② Proxy 校验    │ ~0.3ms   │ Proxy 日志          │
│ ③ Proxy → MQ    │ ~1ms     │ Proxy produceMsg    │
│ ④ MQ 排队       │ 0-100ms  │ MQ lag 指标         │
│ ⑤ DataNode 消费 │ ~10ms    │ DataNode consume    │
│ ⑥ WriteBuffer   │ ~1ms     │ DataNode append     │
│ ⑦ Flush(本次)   │ ~500ms   │ DataNode flush      │
│ ⑧ Flush(总体)   │ 0-10s    │ 后台异步，不计入客户端 │
├─────────────────┼──────────┼─────────────────────┤
│ 客户端感知(~)    │ ~15ms    │ ①+②+③               │
│ 实际落盘(~)     │ ~500ms   │ ④+⑤+⑥+⑦            │
└─────────────────┴──────────┴─────────────────────┘

注意: 客户端只等①→③ (同步), ④→⑦全部异步!
"""

print(WRITE_PIPELINE)
```

---

## 4. 项目总结

### 4.1 DataCoord/DataNode 关键源码文件

| 文件 | 关键函数 | 职责 |
|------|---------|------|
| `internal/datacoord/server.go` | `Start()`, `AssignSegmentID()` | 服务入口 + Segment 分配 |
| `internal/datacoord/channel_manager.go` | `WatchChannels()` | Channel ↔ DataNode 映射 |
| `internal/datacoord/compaction_trigger.go` | `TriggerCompaction()` | Compaction 触发 |
| `internal/datanode/data_sync_service.go` | `Start()`, `Consume()` | MQ 消费主循环 |
| `internal/datanode/flush_manager.go` | `Flush()` | Flush 执行 |
| `internal/datanode/write_buffer.go` | `Append()`, `Pop()` | 写入缓冲区 |

### 4.2 写入链路排查速查

| 症状 | 排查方向 | 关键指标 |
|------|---------|---------|
| 写入延迟高 | DataNode 消费慢 | `consume_lag` |
| 写入偶尔卡顿 | Flush 慢 (序列化/上传) | `flush_latency P95` |
| 写入报错 | MQ 连接 / Segment 分配失败 | DataCoord/DataNode 日志 |
| 写入吞吐低 | 批量太小 / Shard 太少 | 批量大小和 Shard 配置 |

### 4.3 思考题

1. 如果 DataNode 在 Flush 过程中崩溃（Binlog 上传了一半），这条 Binlog 会变成"脏数据"吗？DataCoord 如何检测和处理这种半成品文件？
2. DataNode 的 WriteBuffer 在内存中。如果机器断电，缓冲区中的数据会丢吗？在 MQ 消费层面有什么保障？

---

> **下一章预告**：第35章将深入 QueryCoord 和 QueryNode 的搜索调度源码。读完本章，你应该能理解写入链路的完整源码实现。
