# 第37章：StreamingNode、WAL 与实时数据链路

> **定位**：理解实时写入、可见性和消息流的底层支撑。
> **版本**：Milvus 2.5.x
> **源码关联**：internal/streamingnode/、internal/datanode/data_sync_service.go、pkg/v2/mq/

---

## 1. 项目背景

Milvus 2.5 引入了一个重要的架构变化：**StreamingNode** 替代了部分 Pulsar/Kafka 的功能，把消息队列内嵌到 Milvus 内部。这意味着部署不再强依赖外部 MQ。

核心开发小陈接到任务：将公司 Milvus 集群从 2.4（依赖 Pulsar）升级到 2.5（使用 StreamingNode），验证写入延迟和可靠性与之前一致。但他在升级后发现两个问题：

1. **写入延迟抖动变大**：2.4 时 P95=5ms，升级后 P95=80ms。排查发现 StreamingNode 的 WAL 刷盘频率比 Pulsar 低。
2. **断点续传行为不同**：2.4 时 DataNode 重启后从 Pulsar 的 ack 位点续消费；2.5 的 StreamingNode 使用自己的位点管理，重启后偶尔少消费了几百条消息。

本章将深入 StreamingNode 的架构设计、WAL 实现、消息消费位点管理和实时数据如何进入 Growing Segment。

---

## 2. 项目设计（剧本式交锋对话）

**第一幕：StreamingNode 替代 MQ——架构变化与收益**

*（小陈在架构图上用红笔圈出了新加的 StreamingNode，旧架构里的 Pulsar 被划掉了）*

**小胖**："StreamingNode 是什么？为什么要把 Pulsar 换掉？Pulsar 不是挺稳定的吗？"

**大师**："Pulsar 稳定没问题，但它是'外部依赖'——需要单独部署、单独监控、单独升级。StreamingNode 把消息队列的能力内嵌到 Milvus 进程内，做到'开箱即用'。"

**大师**（画出架构对比）：

```
2.4 架构 (外部 Pulsar):              2.5 架构 (StreamingNode 内置):

Proxy ──→ Pulsar ──→ DataNode        Proxy ──→ StreamingNode ──→ DataNode
              │                                  │
         独立部署、独立运维                     Milvus 进程内
         3 个 Pulsar 节点                    随 Milvus 自动扩缩容
         额外 6GB 内存                       共享 Milvus 节点内存

2.5 架构中 StreamingNode 承担的角色:

┌────────────────────────────────────────────────────────────┐
│                   StreamingNode                            │
│                                                            │
│  ┌──────────────────┐   ┌──────────────────────────────┐  │
│  │  WAL Manager      │   │  Message Dispatcher           │  │
│  │  (Write-Ahead Log)│   │  (消息分发器)                  │  │
│  │                   │   │                               │  │
│  │  ✓ 接收 Proxy 写入 │   │  ✓ 按 Channel 分发消息        │  │
│  │  ✓ 刷盘到本地磁盘   │   │  ✓ 管理消费位点 (position)    │  │
│  │  ✓ 保证不丢数据    │   │  ✓ 检测消费者心跳             │  │
│  └──────────────────┘   └──────────────────────────────┘  │
└────────────────────────────────────────────────────────────┘
         ↓ 消费
    ┌──────────┐
    │ DataNode │ ← 消费 WAL 中的消息, 写入 Growing Segment
    └──────────┘
```

| 对比维度 | 外部 Pulsar/Kafka | StreamingNode (内置) |
|---------|------------------|---------------------|
| 部署复杂度 | 高（独立集群） | 低（Milvus 进程内） |
| 运维成本 | 高（独立监控） | 低（跟随 Milvus 监控） |
| 延迟 | ~1ms | ~0.5ms（减少网络跳转） |
| 可靠性 | 高（成熟产品） | 追赶中（2.5 新增） |
| 适用场景 | 大规模集群（> 10 节点） | 中小规模（< 10 节点） |

> **技术映射**：Pulsar = 外包物流公司（专业但需要对接管理）；StreamingNode = 自建物流团队（灵活但初期不够成熟）；WAL = 物流签收单（每件货都有签名记录，丢了能找到）。

---

**第二幕：WAL 实现——数据如何保证不丢**

**小白**："StreamingNode 的 WAL 是怎么保证数据不丢的？刷盘策略是什么？"

**大师**："WAL（Write-Ahead Log）的核心设计——先写日志再处理。"

```go
// StreamingNode WAL 的核心写入流程 (简化)

type WALManager struct {
    segments   map[string]*WALSegment  // Channel → WAL 文件
    flushQueue chan *FlushTask         // 异步刷盘队列
}

// Write — Proxy 写入数据的入口
func (w *WALManager) Write(channel string, msgs []Message) (uint64, error) {
    
    // Step 1: 获取该 Channel 对应的 WAL Segment
    seg := w.getOrCreateSegment(channel)
    
    // Step 2: 序列化消息到内存缓冲
    data, err := serializeMessages(msgs)
    if err != nil {
        return 0, err
    }
    
    // Step 3: 追加写入 WAL 文件
    //   先写数据, 再写索引 (记录 offset + len)
    offset, err := seg.Append(data)
    if err != nil {
        return 0, err
    }
    
    // Step 4: 异步刷盘 (默认 10ms 刷一次或 1MB 刷一次)
    w.flushQueue <- &FlushTask{seg: seg}
    
    // Step 5: 返回位点 (position)
    //   Consumer 从这个位点开始消费
    return seg.GetPosition(), nil
}

// Flush — 后台刷盘 goroutine
func (w *WALManager) flushLoop() {
    ticker := time.NewTicker(10 * time.Millisecond)  // 10ms 刷一次
    var batch []*WALSegment
    
    for {
        select {
        case task := <-w.flushQueue:
            batch = append(batch, task.seg)
            // 积累到 1MB 就刷
            if totalSize(batch) > 1*1024*1024 {
                w.flushBatch(batch)
                batch = nil
            }
        case <-ticker.C:
            // 定时刷盘 (即使不够 1MB)
            if len(batch) > 0 {
                w.flushBatch(batch)
                batch = nil
            }
        }
    }
}
```

**大师**："WAL 的可靠性保障——"

| 保障 | 实现方式 | 后果 |
|------|---------|------|
| **不丢数据** | 先写 WAL 再返回 ACK | Proxy 收到 ACK = 数据已持久化到 WAL |
| **刷盘策略** | 10ms 定时 + 1MB 阈值 | 最坏情况：断电丢失最后 10ms 的数据 |
| **断点续消费** | Consumer 记录已消费的 position | 重启后从上次 position 继续消费 |

**小白**："那 10ms 的刷盘间隔意味着最坏会丢 10ms 的数据？"

**大师**："对——这是一个权衡。如果改成同步刷盘（每条消息都 fsync），延迟会增加 100 倍。10ms 异步刷盘是业界常用的平衡点。"

> **技术映射**：WAL = 会计的流水账（先记账再入账）；异步刷盘 = 每 10 秒结一次账而不是每笔都跑银行；position = 账本的页号（恢复时从上次的页号继续）。

---

**第三幕：实时数据链路——从 Proxy 到 Growing Segment 的完整路径**

**小胖**："那一条实时数据从 Proxy 写入到能被搜索到，走过了哪些路径？"

**大师**："实时数据的完整链路（使用 StreamingNode 时）——"

```
实时数据链路 (StreamingNode 版本):

T0: collection.insert(data)
    ↓ gRPC InsertRequest

T0+1ms: Proxy 接收 + 校验
    ↓ Proxy → StreamingNode (本地进程内通信, 无网络开销!)

T0+2ms: StreamingNode WAL 写入
    ↓ 数据写入 WAL 文件 + 内存缓冲
    ↓ 返回 ACK 给 Proxy

T0+3ms: Proxy 返回 InsertResponse 给客户端
    ✅ 客户端收到"写入成功"

T0+5ms: DataNode 消费 WAL
    ↓ 从上次消费位点继续读取

T0+10ms: DataNode 写入 Growing Segment
    ↓ 数据进入内存中的 Growing Segment

T0+10ms: Growing Segment 数据 = 可搜索！
    ✅ QueryNode 加载 Growing Segment → 暴力检索可见

T0+60min: Flush 触发
    ↓ Growing Segment → Sealed Segment
    ↓ Binlog 写入对象存储

T0+65min: IndexNode 构建索引
    ↓ HNSW 索引文件写入对象存储

T0+70min: QueryNode 加载索引
    ✅ 搜索走 HNSW 索引（毫秒级延迟）
```

**大师**："关键延迟节点——"

| 节点 | 延迟 | 客户端可见？ |
|------|------|------------|
| Proxy → StreamingNode WAL | ~2ms | 同步等待 |
| DataNode 消费 WAL | ~5ms | 异步 |
| Growing Segment 可搜索 | ~10ms | 下一轮搜索可见 |
| Flush + 索引构建 | ~60min | 异步（索引完成后搜索加速） |

---

## 3. 项目实战

### 3.1 实战目标

构造高频写入实验，观察消息积压、消费位点和搜索可见性的变化。

### 3.2 分步实现

#### 步骤 1：高频写入模拟

```python
# step1_high_freq_write.py
"""高频写入模拟 + 消息积压观察"""
import time
import numpy as np
from concurrent.futures import ThreadPoolExecutor
from pymilvus import connections, Collection

connections.connect(host="localhost", port="19530")
collection = Collection("bench_1m")

DIM = 128
BATCH = 100
TOTAL = 10000

vecs = np.random.rand(TOTAL, DIM).astype(np.float32).tolist()
ids = [int(time.time() * 1000) + i for i in range(TOTAL)]

t0 = time.time()
for i in range(0, TOTAL, BATCH):
    end = min(i+BATCH, TOTAL)
    collection.insert([ids[i:end], vecs[i:end]])
    
    # 每 1000 条检查一次
    if end % 1000 == 0:
        elapsed = time.time() - t0
        print(f"  写入 {end}/{TOTAL} 条, {end/elapsed:.0f} 条/s, "
              f"已写入但未 Flush: {end} 条")

total_time = time.time() - t0
print(f"\n写入完成: {TOTAL/total_time:.0f} 条/s")

# 观察：消息从 Proxy → StreamingNode WAL → DataNode 消费 → Growing Segment
print("\n实时数据链路观察:")
print(f"  T+0ms: Proxy 接收 Insert 请求")
print(f"  T+2ms: StreamingNode WAL 返回 ACK")
print(f"  T+10ms: DataNode 消费并写入 Growing Segment")
print(f"  T+10ms: 数据进入 Growing Segment, 可搜索 (暴力检索)")
```

#### 步骤 2：消费位点监控

```python
# step2_position_monitor.py
"""消费位点监控（通过 Prometheus 指标）"""
import requests

def check_consumer_lag(prometheus_url="http://localhost:9091"):
    """检查 DataNode 消费积压"""
    
    # StreamingNode 暴露的消费位点指标
    queries = {
        "wal_write_position": 
            "milvus_streamingnode_wal_position{type='write'}",
        "consumer_position":
            "milvus_streamingnode_consumer_position",
        "lag":
            "milvus_streamingnode_wal_position{type='write'} - milvus_streamingnode_consumer_position",
    }
    
    for name, promql in queries.items():
        try:
            r = requests.get(f"{prometheus_url}/api/v1/query",
                           params={"query": promql})
            data = r.json()
            if data.get("data", {}).get("result"):
                val = data["data"]["result"][0]["value"][1]
                print(f"  {name}: {float(val):.0f}")
        except Exception as e:
            print(f"  {name}: 获取失败 ({e})")

print("消费位点监控 (StreamingNode):")
print("  如果 consumer_position < wal_write_position → 有积压")
check_consumer_lag()
```

#### 步骤 3：StreamingNode vs Pulsar 特性对比

```python
# step3_mq_comparison.py
"""StreamingNode vs Pulsar 功能对比"""
print("""
StreamingNode vs Pulsar 特性对比:

┌──────────────────┬─────────────────┬─────────────────┐
│ 特性              │ Pulsar          │ StreamingNode   │
├──────────────────┼─────────────────┼─────────────────┤
│ 消息持久化        │ BookKeeper      │ 本地 WAL 文件   │
│ 消费位点管理      │ Pulsar Cursor   │ 本地 position   │
│ 多订阅者          │ ✓ (原生)        │ ✓ (支持)        │
│ 消息 TTL          │ ✓               │ ✓               │
│ 死信队列          │ ✓               │ ✗ (暂不支持)    │
│ 外部依赖          │ 是 (独立集群)    │ 否 (进程内)     │
│ 成熟度            │ 高 (Apache 顶级) │ 追赶中          │
│ 推荐场景          │ 大规模(>10节点)  │ 中小规模(<10节点)│
└──────────────────┴─────────────────┴─────────────────┘
""")
```

---

## 4. 项目总结

### 4.1 WAL 配置关键参数

| 参数 | 默认 | 调优建议 |
|------|------|---------|
| `streamingNode.wal.flushInterval` | 10ms | 延迟敏感→5ms; 吞吐优先→20ms |
| `streamingNode.wal.flushSize` | 1MB | 大写入→4MB; 小写入→512KB |
| `streamingNode.wal.retention` | 24h | 按需调整（过期 WAL 自动清理） |

### 4.2 注意事项

- **WAL 磁盘要快**：StreamingNode 的 WAL 存储在本地磁盘，建议用 SSD。
- **升级前做好回滚预案**：从 Pulsar 迁到 StreamingNode 时，保留旧 Pulsar 集群至少 1 周。
- **StreamingNode 的 WAL 不跨节点复制**：节点宕机 = 该节点上的 WAL 数据暂时不可读。
- **消费位点是关键**：确保 DataNode 重启后能从正确的位点续消费，避免数据丢失或重复。
- **小规模优先用 StreamingNode**：减少外部依赖，降低运维复杂度。规模 > 10 节点后考虑 Pulsar 或 Kafka。

### 4.3 思考题

1. StreamingNode 的 WAL 文件如果磁盘满了怎么办？Milvus 会自动清理旧 WAL 还是需要手动干预？
2. 如果 StreamingNode 节点宕机后重启，它上面的 WAL 数据会被重新消费吗？DataNode 的消费位点存在哪里？

### 4.4 StreamingNode 迁移指南

从 Pulsar/Kafka 迁移到 StreamingNode 的步骤:

```
迁移步骤:
  ① 备份当前数据（milvus-backup）
  ② 部署新 Milvus 2.5 集群（开启 streamingNode）
  ③ 双写验证: 旧集群 + 新集群同时写入
  ④ 搜索对比: 相同 query 对比两集群结果一致性
  ⑤ 灰度切换: 10% → 50% → 100% 流量切到新集群
  ⑥ 保留旧集群 1 周（回滚预案）
  
回滚条件:
  ⚠ 写入成功率 < 99.9% → 回滚
  ⚠ 搜索 P95 > 旧集群 × 1.5 → 回滚
  ⚠ 数据一致性校验失败 → 回滚
```

### 4.5 WAL 故障场景与恢复

| 故障场景 | 影响 | 恢复方式 |
|---------|------|---------|
| StreamingNode 进程崩溃 | WAL 数据暂不可读 | 重启后重放 WAL，DataNode 从上次位点续消费 |
| WAL 磁盘满 | 写入被阻塞 | 清理旧 WAL (retention 过期自动清理) |
| 机器断电 | 最后 10ms 数据可能丢失 (异步刷盘) | 重启后 WAL 文件完整性校验，跳过损坏记录 |
| DataNode 消费太慢 | MQ (WAL) 积压 | 增加 DataNode 副本数，考虑回到 Pulsar（更大规模场景） |

---

> **下一章预告**：第38章将探讨极端规模（十亿级向量）下的性能优化与成本治理。读完本章，你应该能理解 Milvus 2.5 的实时数据链路底层实现。
