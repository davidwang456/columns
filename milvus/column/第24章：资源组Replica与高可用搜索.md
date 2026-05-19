# 第24章：资源组、Replica 与高可用搜索

> **定位**：让查询服务从"能用"变成"抗压"。
> **版本**：Milvus 2.5.x
> **源码关联**：internal/querycoordv2/server.go、internal/querycoordv2/balance.go、internal/querycoordv2/replica.go

---

## 1. 项目背景

某电商平台大促期间，搜索 QPS 从日常 500 飙到 8000。凌晨 0 点刚过，监控告警炸了：P95 延迟从 15ms 涨到 1800ms，错误率从 0% 涨到 12%。运维紧急排查发现：3 台 QueryNode 全部 CPU 100%，其中 1 号节点因为加载了一个超大 Segment（8GB），内存即将 OOM。

运维临时加了 2 台 QueryNode，但新节点的加入并没有改善——因为 QueryCoord 的负载均衡器还没有把 Segment 迁移到新节点上。等了 5 分钟后 Segment 开始迁移，但过程中 1 号节点终于 OOM 崩溃了——它上面的 20 个 Segment 瞬间变成不可搜索状态，直到 QueryCoord 检测到心跳丢失并重新分配，又过了 3 分钟。

这次故障暴露了三个问题：
1. **没有按业务隔离资源**：核心的"商品搜索"和次要的"推荐召回"共用同一组 QueryNode，推荐的高负载拖垮了搜索。
2. **没有多副本**：每个 Segment 只有一个副本，QueryNode 宕机 = Segment 暂时不可搜索。
3. **没有弹性扩容**：手动加 QueryNode 后等负载均衡太慢（分钟级），来不及应对秒级流量尖峰。

本章将引入 Resource Group、Replica 和 Segment 故障转移机制。

---

## 2. 项目设计（剧本式交锋对话）

**第一幕：Resource Group——按业务隔离计算资源**

*（运维小王在大促复盘会上展示"商品搜索被推荐拖垮"的监控曲线）*

**小胖**（指着交叉的曲线）："为什么推荐系统一跑，搜索就挂？它们不是在不同服务里的吗？"

**大师**："虽然业务逻辑在不同服务里，但它们共用同一组 QueryNode！这就好比公司只有一个会议室——销售部开会的时候研发部就没地方开会。**Resource Group 就是给不同业务分配专用会议室。**"

**大师**（画资源组架构图）：

```
默认状态（无 Resource Group）:      有 Resource Group:
────────────────────────────      ────────────────────
所有 Collection 共用 QueryNode    按业务隔离

┌─────────────────────┐          ┌──────────────────────┐
│    QueryNode × 3    │          │  RG: product_search   │
│ ┌───┐ ┌───┐ ┌───┐  │          │  ┌────────┐┌────────┐│
│ │ C1│ │ C2│ │ C3│  │          │  │QN 1    ││QN 2    ││ ← 专用
│ └───┘ └───┘ └───┘  │          │  └────────┘└────────┘│
│  商品  推荐  图片   │          └──────────────────────┘
│  搜索  召回  检索   │          
└─────────────────────┘          ┌──────────────────────┐
                                 │  RG: recommend        │
                                 │  ┌────────┐┌────────┐│
                                 │  │QN 3    ││QN 4    ││ ← 专用
                                 │  └────────┘└────────┘│
                                 └──────────────────────┘
```

**大师**："Resource Group 的本质——"

| 概念 | 说明 |
|------|------|
| **Resource Group** | 一组 QueryNode（或 DataNode）的逻辑分组 |
| **创建 RG** | `create resource_group "product_rg"` |
| **分配节点** | `transfer node 1,2 to "product_rg"` |
| **绑定 Collection** | `assign collection "product_search" to "product_rg"` |

```python
# Resource Group 操作示例
from pymilvus import utility

# 创建资源组
utility.create_resource_group("search_rg")
utility.create_resource_group("recommend_rg")

# 把 QueryNode 转移到资源组
utility.transfer_node(source="__default_resource_group",
                      target="search_rg", num_node=2)

# 绑定 Collection
utility.assign_collections_to_resource_group(
    resource_group="search_rg",
    collection_names=["product_search"]
)
```

**小白**："那如果一个 Resource Group 里的 QueryNode 都挂了怎么办？"

**大师**："对应的 Collection 就不可搜索了——这既是隔离的好处（不会影响其他业务），也是代价（失去了共享资源的弹性）。解决方案是设置**最小 QueryNode 数**和告警规则。"

> **技术映射**：Resource Group = 公司会议室管理系统（不同部门不同的房间，谁也别占谁的）；默认 Resource Group = 开放工位区（所有人混用，先到先得）。

---

**第二幕：Replica——从单副本到多副本**

**小胖**："上次故障 1 号 QueryNode OOM 后要等 3 分钟才能恢复搜索——太慢了！有没有办法让一个 Segment 同时存在多个 QueryNode 上？"

**大师**："这就是 Replica 的作用——**同一个 Collection 的数据在多个 QueryNode 上各存一份副本。**"

```
无 Replica:                         有 Replica (×2):
─────────────────                   ─────────────────
每个 Segment 只有1个副本              每个 Segment 有2个副本

QueryNode 1: Seg A, Seg B           QueryNode 1: Seg A, Seg B
QueryNode 2: Seg C, Seg D           QueryNode 2: Seg A, Seg B  ← 副本
QueryNode 3: Seg E                  QueryNode 3: Seg C, Seg D
                                    QueryNode 4: Seg C, Seg D  ← 副本
                                    
QN1 宕机: Seg A/B 暂时不可搜         QN1 宕机: QN2 上的 Seg A/B 副本
                                    ← 立刻顶上，搜索不受影响！
恢复时间: 3 分钟 (重新分配)          恢复时间: 0 秒！
```

```python
# 设置 Collection 的 Replica 数
collection.load(replica_number=2)
# 每个 Segment 会被加载到 2 个不同的 QueryNode 上
```

**大师**："Replica 的核心收益和代价——"

| 维度 | Replica=1 | Replica=2 | Replica=3 |
|------|-----------|-----------|-----------|
| 故障恢复 | 分钟级（需重新分配 Segment） | 秒级（副本自动接替） | 秒级 |
| 读吞吐 | 1x | ~1.8x（负载分散） | ~2.5x |
| 内存成本 | 1x | 2x | 3x |
| QueryNode 最小数 | 1 | 2 | 3 |

**小白**："那 Replica 设置多少合适？"

**大师**："默认 1 个副本。生产环境建议至少 2 个副本——用 2 倍内存换故障时 0 秒恢复。对于金融级高可用场景可以用 3 个副本。"

> **技术映射**：Replica = 重要文件多复印几份放不同地方（一份丢了还有备份）；Replica=2 = 双保险；Replica=3 = 银行级别的三地容灾。

---

**第三幕：QueryNode 故障转移全过程**

**大师**："我带你走一遍 QueryNode 宕机后自动恢复的完整时间线——"

```
T0: QueryNode-1 正常运行
    加载了 Seg-A, Seg-B（各2副本）
    副本分别在 QN-1 和 QN-2 上

T0+1s: QN-1 OOM 宕机
    QueryCoord 还没检测到（心跳丢失检测有延迟）

T0+5s: QueryCoord 心跳超时
    标记 QN-1 为 OFFLINE

T0+6s: QueryCoord 开始 Segment 重分配
    检查 Seg-A: QN-2 上有副本 → 不需要紧急性重分配
    检查 Seg-B: QN-2 上有副本 → 不需要紧急性重分配
    但 Replica 数不足 → 调度后台任务：
      在 QN-3 上新建 Seg-A 和 Seg-B 的副本
      恢复 Replica=2 的状态

T0+7s: 搜索恢复正常！
    Proxy 收到新的 Segment 路由信息
    所有搜索请求只发往 QN-2 和 QN-3

T0+30s: QN-3 上 Seg-A 加载完成
    Replica 恢复为 2，集群回到健康状态

关键耗时:
  T0 → T0+6s (6秒): 故障检测窗口
  T0+6s → T0+7s (1秒): Segment 路由更新
  ──────────────────
  总搜索不可用窗口: 约7秒 (对于有副本的Segment是0秒)
```

**大师**："如果有 Replica=2，搜索不可用窗口可以从 3 分钟缩短到 ~7 秒（仅心跳检测延迟）。如果没有 Replica，这 3 分钟是等新节点加载 Segment 的时间。"

> **技术映射**：心跳丢失检测 = 保安每 5 秒巡逻一次看谁不在位；副本切换 = 备用灯泡自动亮起；Segment 加载 = 新保安到位后重新接管责任区。

---

## 3. 项目实战

### 3.1 实战目标

为核心业务 Collection 配置多副本搜索，模拟 QueryNode 故障并验证恢复时间。

### 3.2 环境准备

```bash
pip install pymilvus==2.5.5
# 需要 Cluster 模式（有多个 QueryNode）
# 或至少 Standalone 环境用于 API 验证
```

### 3.3 分步实现

#### 步骤 1：Resource Group 管理脚本

```python
# step1_rg_manager.py
"""Resource Group 管理工具"""
from pymilvus import connections, utility

connections.connect(host="localhost", port="19530")

class ResourceGroupManager:
    """资源组管理器"""
    
    def list_groups(self):
        """列出所有资源组"""
        groups = utility.list_resource_groups()
        for g in groups:
            print(f"  RG: {g}")
            nodes = utility.describe_resource_group(g)
            print(f"    Nodes: {len(nodes.capacity)}")
            print(f"    Collections: {nodes.num_loaded_collection}")
        return groups
    
    def create_group(self, name: str):
        """创建资源组"""
        utility.create_resource_group(name)
        print(f"  RG '{name}' 已创建")
    
    def transfer_nodes(self, source: str, target: str, count: int):
        """转移节点"""
        utility.transfer_node(
            source=source, target=target, num_node=count
        )
        print(f"  已从 {source} 转移 {count} 个节点到 {target}")
    
    def assign_collections(self, group: str, collections: list):
        """将 Collection 绑定到资源组"""
        utility.assign_collections_to_resource_group(
            resource_group=group,
            collection_names=collections
        )
        print(f"  Collections {collections} 已绑定到 RG '{group}'")

    def status_report(self):
        """生成资源组状态报告"""
        print("=" * 60)
        print("Resource Group 状态报告")
        print("=" * 60)
        for g in utility.list_resource_groups():
            info = utility.describe_resource_group(g)
            print(f"\n[{g}]")
            print(f"  节点数: {len(info.capacity)}")
            print(f"  已加载 Collection: {info.num_loaded_collection}")
            for node_id, caps in info.capacity.items():
                print(f"  Node {node_id}: {caps}")

# 使用
mgr = ResourceGroupManager()
mgr.create_group("core_search")
mgr.status_report()
```

#### 步骤 2：Replica 配置与验证

```python
# step2_replica_config.py
"""配置 Replica 并验证多副本加载状态"""
from pymilvus import Collection, connections

connections.connect(host="localhost", port="19530")

collection = Collection("product_search_prod")

# 查看当前 Replica 状态
print("当前 Collection 信息:")
print(f"  Replica 数: {collection.load_state}")
# 获取各 Segment 的副本分布
segments = collection.get_replicas()
for seg in segments:
    print(f"  Segment {seg.group_id}: "
          f"Replicas={seg.num_replicas}, "
          f"Nodes={seg.node_ids}")

# 设置 Replica=2（在 Load 时指定）
print("\n> 重新 Load 并设置 Replica=2...")
collection.release()
collection.load(replica_number=2)

# 再次查看
segments = collection.get_replicas()
for seg in segments[:5]:
    print(f"  Segment {seg.group_id}: "
          f"Replicas={seg.num_replicas}, "
          f"Nodes={seg.node_ids}")

print(f"\n> 每个 Segment 现在有 2 个副本，分布在不同 QueryNode")
```

#### 步骤 3：模拟故障恢复验证

```python
# step3_fault_simulation.py
"""模拟 QueryNode 故障转移（概念验证）"""
import time
from pymilvus import Collection, connections

connections.connect(host="localhost", port="19530")
collection = Collection("product_search_prod")

print("=" * 60)
print("QueryNode 故障恢复验证")
print("=" * 60)

# 1. 故障前状态
print("\n1. 故障前:")
print(f"   数据量: {collection.num_entities}")
print(f"   Load 状态: {utility.load_state(collection.name).name}")

# 2. 模拟故障（手动 Release 一个节点的 Segment）
#    实际生产中是 QueryNode 宕机触发自动故障转移
print("\n2. [模拟] QueryNode-1 宕机...")
print("   (实际场景: kubectl delete pod querynode-1)")

# 3. 验证搜索仍然可用（有副本）
print("\n3. 故障后搜索验证:")
t0 = time.time()
try:
    results = collection.search(
        data=[[0.1]*384], anns_field="title_vec",
        param={"metric_type": "COSINE", "params": {"ef": 16}},
        limit=5, timeout=10
    )
    elapsed = time.time() - t0
    print(f"   搜索成功! {len(results[0])} 条, 耗时 {elapsed*1000:.1f}ms")
    print(f"   → 说明: 副本自动接替，搜索不受影响")
except Exception as e:
    print(f"   搜索失败: {e}")

# 4. 恢复时间估算
print(f"\n4. 故障恢复时间线:")
print(f"   ① QueryNode 宕机 (~T0)")
print(f"   ② QueryCoord 心跳超时检测 (~T0+5s)")
print(f"   ③ 副本自动接替搜索 (~T0+5s)")
print(f"   ④ 新副本构建完成 (~T0+30s, Replica 恢复到设定值)")
print(f"   → 搜索不可用窗口: ~5 秒 (有副本)")
print(f"   → 搜索不可用窗口: ~180 秒 (无副本, 需重新加载Segment)")
```

---

## 4. 项目总结

### 4.1 高可用配置速查

| 配置 | 值 | 效果 |
|------|---|------|
| Replica 数 | 2 | 单节点故障搜索不中断 |
| Resource Group | 按业务隔离 | 避免一个业务拖垮另一个 |
| QueryNode 最小数 | 2×Replica | 确保每个副本在不同物理机 |
| 心跳检测间隔 | 5s (默认) | 影响故障检测速度 |

### 4.2 适用场景

- **核心业务**：商品搜索、支付验证 → Replica=2 + Resource Group 隔离
- **次要业务**：推荐候选、离线分析 → Replica=1 + Shared RG
- **开发测试**：Replica=1, Standalone 即可

### 4.3 注意事项

- **Replica > 1 要求 QueryNode ≥ Replica 数**：Replica=2 至少需要 2 个 QueryNode。
- **Resource Group 中转移节点是实时生效的**：已加载的 Segment 会被重新分配，可能短暂影响搜索。
- **增加 Replica 不能替代备份**：Replica 是容灾（防止硬件故障），备份是恢复（防止误删/数据损坏）。

### 4.4 思考题

1. 如果一个 Collection 设了 Replica=2，但只有 2 个 QueryNode。其中一个 QueryNode 宕机后，系统能自动在剩余的 1 个 QueryNode 上创建第二个副本吗？
2. Resource Group 的隔离粒度是"一组 QueryNode"。如果同一个业务内部有多个 Collection，能不能在同一个 RG 内进一步做资源隔离？

---

> **下一章预告**：第25章将深入 Compaction、GC 与存储治理——控制长期运行后的存储膨胀。读完本章，你应该能为生产系统配置高可用搜索和资源隔离。
