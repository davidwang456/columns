# 第25章：Compaction、GC 与存储治理

> **定位**：控制长期运行后的存储膨胀。
> **版本**：Milvus 2.5.x
> **源码关联**：internal/datacoord/compaction_trigger.go、internal/datanode/compaction.go、internal/datacoord/garbage_collector.go

---

## 1. 项目背景

运维老周的 Milvus 集群已运行 6 个月，存储了 800 万条商品向量。最近他发现一个诡异现象：MinIO 的对象存储从最初的 50GB 膨胀到了 280GB，但 Collection 的数据量只从 500 万涨到 800 万（增长 60%）。也就是说，存储增长是数据增长的 5 倍！

老周排查发现三个"存储黑洞"：

1. **未合并小 Segment**：增量写入产生了 12000+ 个小 Segment（每个只含几百条数据），每个小 Segment 独立存储一套 Binlog + Index 文件，元数据开销巨大。
2. **删除但未清理的 Delta Log**：过去 6 个月累计删除了 200 万条数据，但它们的 Delta Log 仍然占据着 30GB 磁盘空间——因为没有触发 Compaction。
3. **GC 未正常执行**：对象存储中堆积了大量孤儿文件（旧 Segment 被 Compaction 合并后，新 Segment 已生成，但旧文件没有被 GC 清理）。

老周手动触发了一次 Compaction，发现搜索 P95 延迟从 200ms 降到了 30ms——原来大量小 Segment 在搜索时，QueryNode 要逐一加载它们的索引元数据，产生了巨大的开销。

本章将深入 Segment 生命周期、Compaction 触发机制、GC 回收策略和存储优化。

---

## 2. 项目设计（剧本式交锋对话）

**第一幕：Segment 生命周期完整图解**

*（老周在对象存储控制台看到满屏的 binlog 文件，头皮发麻）*

**小胖**（震惊地）："280GB！每条向量才 3KB，800 万条不就是 24GB 吗？其他 250GB 是什么？"

**大师**："是多版本数据、删除标记和碎片化 Segment 的叠加。先说 Segment 的生命周期——"

**大师**（画生命周期图）：

```
Segment 的完整生命周期:

┌──────────┐   数据写入    ┌──────────┐   Flush    ┌──────────┐
│ Growing  │ ────────────→│ Growing  │ ────────→ │  Sealed  │
│ (内存)   │              │ (内存满) │            │ (Binlog) │
│ 可写入   │              │ 触发Flush│            │ 只读     │
└──────────┘              └──────────┘            └────┬─────┘
                                                      │
                          ┌───────────────────────────┘
                          │
                 ┌────────▼────────┐
                 │   Compaction     │ ← 合并多个小Segment
                 │   (合并+去重)     │
                 └────────┬────────┘
                          │
              ┌───────────┴───────────┐
              │                       │
     ┌────────▼────────┐    ┌────────▼────────┐
     │  Compacted Seg  │    │  旧 Segment      │
     │  (新的大Segment) │    │  (待GC清理)      │
     └─────────────────┘    └────────┬────────┘
                                     │
                            ┌────────▼────────┐
                            │   GC (垃圾回收)  │
                            │   永久删除旧文件   │
                            │   释放磁盘空间    │
                            └─────────────────┘
```

**大师**："你的 250GB 额外数据来自——"

| 来源 | 占比 | 成因 | 解决 |
|------|------|------|------|
| 小 Segment 碎片（12000+ Segments） | ~40% | 频繁写入+频繁 Flush，每次生成新 Binlog 文件 | Compaction 合并小 Segment |
| 未清理 Delta Log | ~30% | 删除 200 万条数据，Delta Log 积累 | Compaction 物理删除 + GC |
| 旧版本索引文件 | ~20% | Segment 合并后旧 Segment 的索引文件未被 GC | 调优 GC 策略 |
| 正常增长 | ~10% | 业务数据增长 | 合理 |

> **技术映射**：小 Segment = 写满一张纸就换一张新的（散落满桌）；Compaction = 把散落的纸装订成册（整齐省空间）；GC = 碎纸机销毁旧草稿（真正释放空间）。

---

**第二幕：Compaction 触发机制与调优**

**小白**："Compaction 什么时候触发？能手动触发吗？"

**大师**："两种触发方式——"

```
Compaction 触发机制:

1. 自动触发（DataCoord 后台周期性检查）
   触发条件（任一满足）:
   ├─ Segment 数量 > datacoord.compaction.max.segment (默认 3000)
   ├─ 单个 Partition 的 Segment > 阈值
   ├─ Delta Log 大小 > 阈值（删除积累到一定程度）
   └─ 定时触发（datacoord.compaction.interval, 默认 30 分钟）

2. 手动触发
   collection.compact()  # 强制触发
   
⚠ 手动触发后不会阻塞，Compaction 是后台异步执行的
```

**大师**："关键调优参数——"

| 参数 | 默认值 | 推荐值 | 说明 |
|------|-------|-------|------|
| `datacoord.compaction.max.segment` | 3000 | 1000-5000 | Segment 超过此数自动触发 Compaction |
| `datacoord.compaction.enable.autoCompaction` | true | true | 建议开启，不要关闭 |
| `datanode.flush.insertBufSize` | 16MB | 32-64MB | 每次 Flush 的缓冲大小（太小产生碎 Segment） |

**小胖**："Compaction 会阻塞写入吗？"

**大师**："不会。Compaction 是在后台独立的 DataNode 线程中执行的，它读取已封存的 Sealed Segment，合并后生成新的 Compacted Segment，不会影响正在写入的 Growing Segment。但注意——Compaction 会消耗 CPU 和磁盘 IO，建议在业务低峰期触发手动 Compaction。"

> **技术映射**：自动 Compaction = 扫地机器人定时巡逻；手动 Compaction = 按"立刻打扫"按钮；碎片化 = 满地的纸片，看着多但没多少内容。

---

**第三幕：GC 策略——为什么删了数据磁盘没减**

**小胖**："那最根本的问题——为什么我上周删了 200 万条数据，磁盘一点没少？"

**大师**："因为你只完成了'逻辑删除'，还没走到'物理删除'。删除数据的完整路径——"

```
Delete 请求 → 磁盘释放的完整路径:

Step 1: collection.delete("id in [...]")
         → 写入 Delta Log（标记删除）
        ⚠ 此时数据在磁盘上仍然存在！磁盘 ↑

Step 2: Compaction 触发
         → 读取 Sealed Segment + Delta Log
         → 合并时丢弃已标记删除的行
         → 生成新的 Compacted Segment（不含已删除数据）
        ⚠ 旧 Segment 和 Delta Log 仍然在磁盘上！磁盘 ↑

Step 3: GC 触发
         → 检测到旧 Segment 已无引用
         → 等待 retention 时间（默认 10 分钟）
         → 从对象存储永久删除旧文件
        ✅ 磁盘空间终于释放！磁盘 ↓

全程耗时: Step1 → Step3 = 30分钟到数小时
```

**大师**："GC 的三个关键配置——"

| 参数 | 默认值 | 说明 |
|------|-------|------|
| `datacoord.gc.interval` | 30 分钟 | GC 检查周期 |
| `datacoord.gc.missingTolerance` | 1 天 | 无归属文件保留时间 |
| `datacoord.gc.dropTolerance` | 1 天 | 已删除 Collection 的文件保留（误删恢复窗口） |

> **技术映射**：逻辑删除 = 给书贴"报废"标签；Compaction = 把标签页和书芯分开；GC = 回收站清空（彻底销毁）。

---

## 3. 项目实战

### 3.1 实战目标

模拟频繁更新和删除场景，观察 Compaction 前后的磁盘占用与搜索性能变化。

### 3.2 环境准备

```bash
pip install pymilvus==2.5.5 numpy sentence-transformers
```

### 3.3 分步实现

#### 步骤 1：模拟频繁写入/删除产生碎片

```python
# step1_fragment_sim.py
"""模拟频繁写入和删除，产生大量小 Segment"""
import time
import numpy as np
from pymilvus import connections, Collection, utility
from pymilvus import CollectionSchema, FieldSchema, DataType

connections.connect(host="localhost", port="19530")

COLL_NAME = "compaction_demo"
if utility.has_collection(COLL_NAME):
    utility.drop_collection(COLL_NAME)

fields = [
    FieldSchema(name="id", dtype=DataType.INT64, is_primary=True),
    FieldSchema(name="vec", dtype=DataType.FLOAT_VECTOR, dim=128),
    FieldSchema(name="text", dtype=DataType.VARCHAR, max_length=256),
    FieldSchema(name="status", dtype=DataType.VARCHAR, max_length=32),
]
collection = Collection(COLL_NAME, CollectionSchema(fields))

# 模拟 10 轮写入+删除（每轮产生小 Segment）
for round_idx in range(10):
    batch_size = 1000
    ids = list(range(round_idx * batch_size + 1, (round_idx + 1) * batch_size + 1))
    vecs = np.random.rand(batch_size, 128).astype(np.float32).tolist()
    texts = [f"round_{round_idx}_{i}" for i in range(batch_size)]
    statuses = ["active"] * batch_size
    
    collection.insert([ids, vecs, texts, statuses])
    utility.flush([COLL_NAME])  # 每轮都 Flush → 产生大量小 Segment
    print(f"  第 {round_idx+1} 轮: 写入 {batch_size} 条, Flushed")

# 删除 50% 的数据
all_ids = list(range(1, 10001))
delete_ids = all_ids[::2]  # 一半数据
collection.delete(f"id in {delete_ids}")
print(f"\n删除 {len(delete_ids)} 条数据")

# 查看 Segment 状态
segments_before = utility.get_query_segment_info(COLL_NAME)
growing_count = len([s for s in segments_before if s.state_name == "Growing"])
sealed_count = len([s for s in segments_before if s.state_name == "Sealed"])
print(f"Compaction 前: Growing={growing_count}, Sealed={sealed_count}, "
      f"总 Segment={len(segments_before)}")
print(f"> 大量小 Sealed Segment + 删除操作的 Delta Log → 碎片严重")
```

#### 步骤 2：触发 Compaction 并观察

```python
# step2_trigger_compaction.py
"""手动触发 Compaction 并观察 Segment 数量变化"""
import time
from pymilvus import Collection, utility

collection = Collection("compaction_demo")

# 手动触发 Compaction
print("触发 Compaction...")
collection.compact()
time.sleep(2)

# 等待 Compaction 完成（轮询检查 Segment 数量）
print("等待 Compaction 完成...")
max_wait = 60
start = time.time()

while time.time() - start < max_wait:
    segments = utility.get_query_segment_info(collection.name)
    sealed = len([s for s in segments if s.state_name == "Sealed"])
    growing = len([s for s in segments if s.state_name == "Growing"])
    
    print(f"  Growing={growing}, Sealed={sealed}, 总={len(segments)}")
    
    # 如果 Sealed Segment 数量下降说明 Compaction 在工作
    if sealed <= 3:  # 假设 Compaction 后剩余 3 个左右大 Segment
        print("  > Compaction 完成！")
        break
    
    time.sleep(5)

# 最终状态
segments_after = utility.get_query_segment_info(collection.name)
print(f"\nCompaction 后: 总 Segment={len(segments_after)}")

# 搜索性能前后对比（需先建索引和 Load）
collection.create_index("vec", {
    "index_type": "HNSW", "metric_type": "COSINE",
    "params": {"M": 8, "efConstruction": 100}
})
utility.wait_for_index_building_complete(collection.name, timeout=60)
collection.load()

print("> Compaction 合并了小 Segment，减少搜索时的 Segment 遍历开销")
```

#### 步骤 3：存储治理检查清单

```python
# step3_storage_checklist.py
"""定期存储治理检查"""
from pymilvus import connections, utility
import json

connections.connect(host="localhost", port="19530")

def storage_governance_report():
    """存储治理检查报告"""
    checks = []
    
    collections = utility.list_collections()
    
    for cname in collections:
        segments = utility.get_query_segment_info(cname)
        
        # 检查 1: Segment 数量是否过多
        total_seg = len(segments)
        sealed_seg = len([s for s in segments if s.state_name == "Sealed"])
        
        checks.append({
            "collection": cname,
            "total_segments": total_seg,
            "sealed_segments": sealed_seg,
            "issues": [],
        })
        
        if total_seg > 1000:
            checks[-1]["issues"].append(
                "⚠ Segment 数量 > 1000，建议触发 Compaction"
            )
        
        if sealed_seg > 500:
            checks[-1]["issues"].append(
                "⚠ Sealed Segment > 500，可能 Compaction 未完成或关闭"
            )
    
    # 检查 2: GC 是否正常运行
    # (无法通过 API 直接检查，需看对象存储文件变化)
    checks.append({
        "check": "GC 状态",
        "suggestion": "检查 MinIO/S3 中是否有大量旧 binlog 文件未被清理"
    })
    
    return checks

# 输出报告
report = storage_governance_report()
print("=" * 60)
print("存储治理检查报告")
print("=" * 60)
for item in report:
    if "collection" in item:
        print(f"\n[{item['collection']}]")
        print(f"  Segment 总数: {item['total_segments']}")
        print(f"  Sealed: {item['sealed_segments']}")
        for issue in item["issues"]:
            print(f"  {issue}")
    else:
        print(f"\n[{item['check']}]")
        print(f"  {item['suggestion']}")
```

---

## 4. 项目总结

### 4.1 存储膨胀原因与治理

| 原因 | 症状 | 治理手段 |
|------|------|---------|
| 小 Segment 过多 | 搜索慢、对象存储文件多 | Compaction 合并 |
| Delta Log 积累 | 磁盘不释放、搜索慢（需加载大量 Delta） | 触发 Compaction + 等待 GC |
| GC 未执行 | 对象存储持续增长 | 检查 GC 日志、调整 retention |
| Flush 太频繁 | 产生大量小 Segment | 增大 `insertBufSize`，减少 Flush 频率 |

### 4.2 最佳实践

- **定期检查 Segment 数量**：超过 1000 就触发 Compaction。
- **业务低峰做 Compaction**：手动 Compaction 消耗 CPU 和 IO，避免高峰期执行。
- **不要关闭自动 Compaction**：除非有特殊需求。
- **设置 GC TTL 合理值**：不要设太小（误删无法恢复）也不要设太大（垃圾堆积）。

### 4.3 注意事项

- **Compaction 不能解决所有问题**：如果数据量本身增长了 10 倍，Compaction 只是把碎片整理成大文件，不会减少总量。
- **GC 不是即时的**：默认 retention=30 分钟，刚 Compaction 完不会立刻释放磁盘。
- **不要在生产环境频繁 `compact()`**：把自动 Compaction 交给系统，只在紧急情况（Segment 数 > 10000）时手动触发。

### 4.4 思考题

1. 如果 Compaction 速度赶不上新 Segment 产生的速度（比如每秒产生 10 个新 Segment 但 Compaction 每秒只能合并 5 个），最终会导致什么后果？如何设计解决方案？
2. Milvus 的 Compaction 和 LSM-Tree（如 RocksDB）的 Compaction 在算法层面有何异同？为什么 Milvus 的 Compaction 频率更低？

---

> **下一章预告**：第26章将搭建 Prometheus + Grafana 监控体系。读完本章，你应该能独立治理 Milvus 的存储膨胀问题。
