# 第17章：Milvus 分布式架构与组件职责

> **定位**：从单机使用进入集群视角。
> **版本**：Milvus 2.5.x
> **源码关联**：cmd/milvus/、internal/rootcoord/、internal/datacoord/、internal/querycoordv2/、internal/proxy/

---

## 1. 项目背景

某电商平台业务增长迅猛，商品数据从 100 万膨胀到 500 万，日均搜索从 300 QPS 涨到 5000 QPS。Standalone 单机模式下问题集中爆发：QueryNode 内存告警、写入延迟抖动、Proxy 偶尔超时、单点故障无备机。

运维团队决定升级到 Milvus Cluster 分布式模式。但架构师张工发现，团队对分布式架构的理解停留在"多部署几台机器"的层面：

1. **不知道哪些组件可以水平扩展**——把所有组件都部署了 3 副本，结果 etcd 和 RootCoord 的副本反而引入了不必要的 leader 选举开销。
2. **不会看组件日志来定位瓶颈**——搜索慢了就看 QueryNode 日志，写不进去就看 DataNode 日志，但没有理解组件间的上下游依赖。
3. **不理解组件如何协同工作**——以为 Proxy 只是个"转发器"，不知道它承担了多少校验、分片、合并的职责。
4. **扩缩容没有章法**——数据量增长就加 DataNode，QPS 增长就加 QueryNode——原则没错，但不知道具体多少数据配多少 Node。

本章将用 Helm 部署一套 Milvus Cluster，并深入每个组件的职责、启动参数、日志特征和扩缩容策略。

---

## 2. 项目设计（剧本式交锋对话）

**第一幕：Standalone vs Cluster——拆开后的区别**

*（张工在黑板上画了 12 个方框，标注着各个组件名称）*

**小胖**（惊讶地）："Standalone 就一个 docker compose，Cluster 怎么变成 12 个服务了？！"

**大师**："因为 Standalone 是把 12 个角色挤进一个进程里——就像一个全能选手，又是前锋又是后卫又是守门员。Cluster 是把 12 个角色各自独立成专业选手。"

**大师**（画对比图）：

```
Standalone（单进程多角色）              Cluster（多进程各司其职）
─────────────────────────              ─────────────────────────
┌─────────────────────┐                ┌────────────┐  ┌────────────┐
│  milvus-standalone  │                │  Proxy ×3  │  │  Proxy ×3  │
│  ┌── Proxy          │                └─────┬──────┘  └─────┬──────┘
│  ├── RootCoord      │                      │               │
│  ├── DataCoord      │        ┌─────────────┼───────────────┼───┐
│  ├── QueryCoord     │        │   ┌─────────▼─────────────▼─┐ │
│  ├── DataNode       │        │   │    Message Queue         │ │
│  ├── QueryNode      │        │   │    (Pulsar / Kafka)     │ │
│  └── IndexNode      │        │   └─┬─────────┬──────────┬──┘ │
└─────────────────────┘        │     │         │          │    │
                               │ ┌───▼───┐ ┌───▼───┐ ┌───▼──┐ │
  + etcd + MinIO               │ │Data-  │ │Index- │ │Query-│ │
                               │ │Node×2 │ │Node×2 │ │Node×3│ │
                               │ └───┬───┘ └───┬───┘ └───┬──┘ │
                               │     │         │         │     │
                               │ ┌───▼─────────▼─────────▼───┐ │
                               │ │     Object Storage        │ │
                               │ │     (MinIO / S3)           │ │
                               │ └───────────────────────────┘ │
                               └───────────────────────────────┘
```

**大师**："Cluster 模式下，每个组件的角色和 Standalone 版本一模一样，区别在于——"

| 组件 | Standalone | Cluster | 可扩容 |
|------|-----------|---------|--------|
| Proxy | 内置 | 独立部署 ×N | ✓ 水平扩展 |
| RootCoord | 内置 | 独立部署 ×1（有状态） | ✗ 单点 |
| DataCoord | 内置 | 独立部署 ×1（有状态） | ✗ 单点 |
| QueryCoord | 内置 | 独立部署 ×1（有状态） | ✗ 单点（v2 支持 Active-Standby） |
| DataNode | 内置 | 独立部署 ×N | ✓ 水平扩展 |
| QueryNode | 内置 | 独立部署 ×N | ✓ 水平扩展 |
| IndexNode | 内置 | 独立部署 ×N | ✓ 水平扩展 |

**小白**："为什么 Coordinator 不能水平扩展？单点挂了怎么办？"

**大师**："Coordinator 是有状态服务——RootCoord 管全局 ID 分配和时间戳、DataCoord 管 Segment 分配、QueryCoord 管 Load 调度。做多副本意味着分布式一致性问题，Milvus 的选择是让它们保持单点简单，通过 etcd 的 lease 和快速故障恢复来保证可用性。QueryCoord v2 已经支持 Active-Standby 模式，算是部分解决了高可用。"

> **技术映射**：Standalone = 全能选手一人打全场（灵活但体力有限）；Cluster = 专业分工的球队（每个人只做一件事，能独立换人）；Coordinator = 教练团队（只有一个总教练，但助理教练/医疗团队可随时顶上）。

---

**第二幕：Coordinator 三剑客——谁管什么**

**小胖**："那 RootCoord、DataCoord、QueryCoord 三个到底谁管什么？我以前一直以为它们是一个东西——"

**大师**："这三是 Milvus 的大脑。给他们各自一个职责口诀——"

```
RootCoord: "你是谁，你长什么样"
─────────────────────────────────
负责 DDL 操作：
  ✓ 创建/删除 Collection
  ✓ 创建/删除 Partition
  ✓ 分配全局唯一 ID（ID Allocator）
  ✓ 分配时间戳（Timestamp Oracle）
  ✓ 管理 Collection Alias
源码: internal/rootcoord/

DataCoord: "数据存哪里，什么时候封存"
─────────────────────────────────
负责数据管理：
  ✓ 分配 Segment 给 DataNode
  ✓ 触发 Flush 操作
  ✓ 触发 Compaction 操作
  ✓ 管理 Channel 分配
  ✓ 监控 Segment 生命周期
源码: internal/datacoord/

QueryCoord: "数据加载到哪里，搜索路径怎么走"
─────────────────────────────────
负责查询调度：
  ✓ Collection Load / Release
  ✓ QueryNode 管理（心跳、扩缩容）
  ✓ Segment 分配（哪个 QueryNode 加载哪些 Segment）
  ✓ Replica 管理
  ✓ Balance 调度（负载均衡）
源码: internal/querycoordv2/
```

**大师**："举个三家协作的例子——加载一个 Collection 到搜索可用状态："

```
1. 用户调用 collection.load()
    ↓
2. QueryCoord 收到 Load 请求
    → 问 RootCoord: "这个 Collection 有哪些 Segment?"
    → 问 DataCoord: "这些 Segment 的当前状态是什么?"（Sealed/Growing）
    ↓
3. QueryCoord 制定加载计划
    → 选择一组可用的 QueryNode
    → 分配 Segment 到各 QueryNode
    ↓
4. QueryNode 从对象存储下载 Segment 和索引文件
    → 加载到本地内存
    → 通知 QueryCoord "加载完成"
    ↓
5. QueryCoord 更新 Load 状态
    → collection.load_state = Loaded
    → 用户现在可以搜索了
```

> **技术映射**：RootCoord = 户籍管理处（管身份和档案）；DataCoord = 仓库调度中心（管货物入库和盘点）；QueryCoord = 快递站调度（管包裹分发到哪个快递柜）。

---

**第三幕：Node 三兄弟——谁干什么活**

**小胖**："那 DataNode、QueryNode、IndexNode 三兄弟呢？"

**大师**：

```
DataNode: "我管写入"
──────────────────
  ✓ 从 Message Queue 消费写入数据
  ✓ 写入 Growing Segment（内存）
  ✓ 触发 Flush → 生成 Binlog → 写入对象存储
  ✓ 响应 DataCoord 的 Compaction 指令
关键日志: "DataNode flush segment 123 completed"

QueryNode: "我管搜索"
──────────────────
  ✓ 从对象存储加载 Segment + 索引到内存
  ✓ 执行向量检索（调用 internal/core C++ 引擎）
  ✓ 执行标量过滤表达式
  ✓ 返回搜索结果给 Proxy
关键日志: "QueryNode search completed, segment=456, latency=2ms"

IndexNode: "我管建索引"
──────────────────
  ✓ 从对象存储读取原始向量数据
  ✓ 构建 HNSW / IVF / DISKANN 索引
  ✓ 将索引文件写回对象存储
  ✓ 通知 DataCoord 索引构建完成
关键日志: "IndexNode build index for segment 789, type=HNSW"
```

**大师**："还有一个新角色——**StreamingNode**（Milvus 2.5+），它替代了 Pulsar/Kafka 的部分功能，把消息队列内嵌到 Milvus 内部，减少外部依赖。"

| 组件 | 故障影响 | 恢复方式 |
|------|---------|---------|
| Proxy 宕机 | 客户端请求无法进入 | 自动切换（多 Proxy） |
| DataNode 宕机 | 写入阻塞，数据在 MQ 中积压 | DataCoord 重新分配 Channel 到其他 DataNode |
| QueryNode 宕机 | 对应 Segment 不可搜索 | QueryCoord 检测到心跳丢失，重新分配 Segment |
| IndexNode 宕机 | 索引构建任务暂停 | DataCoord 重新分配任务 |

> **技术映射**：DataNode = 仓库入库员（负责收件和打包上架）；QueryNode = 快递柜（取件快，但容量有限）；IndexNode = 分拣系统（对包裹建立快速查找标签）。

---

## 3. 项目实战

### 3.1 实战目标

用 Helm 部署 Milvus Cluster，观察各组件日志和健康状态，验证分布式架构行为。

### 3.2 环境准备

```bash
# Kubernetes 集群（minikube / kind / 生产 K8s）
kubectl get nodes

# Helm
helm version

# 添加 Milvus Helm repo
helm repo add milvus https://zilliztech.github.io/milvus-helm
helm repo update
```

### 3.3 分步实现

#### 步骤 1：部署 Milvus Cluster

```yaml
# values.yaml — Helm 自定义配置
# 安装: helm install milvus-cluster milvus/milvus -f values.yaml

cluster:
  enabled: true  # 启用 Cluster 模式

# ---- Proxy ----
proxy:
  replicas: 2
  resources:
    requests:
      memory: "1Gi"
      cpu: "500m"
    limits:
      memory: "2Gi"
      cpu: "2"

# ---- Coordinators ----
rootCoordinator:
  replicas: 1
  resources:
    requests:
      memory: "512Mi"
      cpu: "250m"

dataCoordinator:
  replicas: 1
  resources:
    requests:
      memory: "512Mi"
      cpu: "250m"

queryCoordinator:
  replicas: 1
  resources:
    requests:
      memory: "512Mi"
      cpu: "250m"

# ---- Nodes ----
dataNode:
  replicas: 2
  resources:
    requests:
      memory: "2Gi"
      cpu: "1"
    limits:
      memory: "4Gi"
      cpu: "2"

indexNode:
  replicas: 2
  resources:
    requests:
      memory: "2Gi"
      cpu: "1"

queryNode:
  replicas: 3
  resources:
    requests:
      memory: "4Gi"
      cpu: "2"
    limits:
      memory: "8Gi"
      cpu: "4"

# ---- External Dependencies ----
etcd:
  replicaCount: 3

minio:
  resources:
    requests:
      memory: "512Mi"

pulsar:
  enabled: false  # 2.5+ 可用 streamingNode 替代

streamingNode:
  enabled: true   # 内置消息流
```

```bash
# 部署命令
helm install milvus-cluster milvus/milvus -f values.yaml --namespace milvus --create-namespace

# 查看 Pods
kubectl get pods -n milvus -w

# 期望输出（约 5-10 分钟后全部 Running）:
# NAME                               READY   STATUS
# milvus-cluster-etcd-0              1/1     Running
# milvus-cluster-minio-xxx           1/1     Running
# milvus-cluster-proxy-xxx           1/1     Running
# milvus-cluster-rootcoord-xxx       1/1     Running
# milvus-cluster-datacoord-xxx       1/1     Running
# milvus-cluster-querycoord-xxx      1/1     Running
# milvus-cluster-datanode-xxx        1/1     Running
# milvus-cluster-indexnode-xxx       1/1     Running
# milvus-cluster-querynode-xxx       1/1     Running
```

#### 步骤 2：组件健康检查脚本

```python
# step2_cluster_health.py
"""Milvus Cluster 健康检查脚本"""
from pymilvus import connections, utility, Collection
import requests

def check_cluster_health(milvus_host="localhost", milvus_port=19530,
                         k8s_ns="milvus"):
    """检查 Cluster 各组件健康状态"""
    report = {}
    
    # 1. Milvus 连接检查
    try:
        connections.connect(host=milvus_host, port=milvus_port)
        version = utility.get_server_version()
        report["milvus"] = {"status": "✅", "version": version}
    except Exception as e:
        report["milvus"] = {"status": "❌", "error": str(e)}
        return report
    
    # 2. 列出所有 Collections（验证 RootCoord 正常）
    try:
        collections = utility.list_collections()
        report["rootcoord"] = {"status": "✅", "collections": len(collections)}
    except Exception as e:
        report["rootcoord"] = {"status": "❌", "error": str(e)}
    
    # 3. 检查 etcd
    try:
        r = requests.get(f"http://{milvus_host}:2379/health", timeout=5)
        report["etcd"] = {"status": "✅" if r.ok else "⚠️"}
    except:
        report["etcd"] = {"status": "❌"}
    
    # 4. 检查 MinIO
    try:
        r = requests.get(f"http://{milvus_host}:9000/minio/health/live", timeout=5)
        report["minio"] = {"status": "✅" if r.ok else "⚠️"}
    except:
        report["minio"] = {"status": "❌"}
    
    return report

# 使用
health = check_cluster_health()
for component, info in health.items():
    print(f"{component:15s}: {info['status']}  {info.get('version', '')}")
```

#### 步骤 3：观察组件日志特征

```python
# step3_log_observer.py
"""根据组件日志特征判断问题类型"""
# 实际使用 kubectl logs，这里用代码总结各组件关键日志模式

COMPONENT_LOGS = {
    "Proxy": {
        "正常": ["Proxy received search request", "Proxy task completed"],
        "异常": ["connection refused to querycoord", "grpc: timeout"],
        "排查": "如果大量 timeout → check QueryCoord/QueryNode 是否 OOM",
    },
    "RootCoord": {
        "正常": ["CreateCollection completed", "AllocTimestamp completed"],
        "异常": ["etcd request timeout", "failed to load collection meta"],
        "排查": "如果 etcd timeout → 检查 etcd 磁盘 IO 和网络延迟",
    },
    "DataCoord": {
        "正常": ["Segment allocated", "Flush triggered for channel"],
        "异常": ["no available DataNode", "channel not balanced"],
        "排查": "如果 no available DataNode → 增加 DataNode 或检查心跳",
    },
    "DataNode": {
        "正常": ["Flush completed", "Binlog uploaded to storage"],
        "异常": ["flush timeout", "out of memory"],
        "排查": "如果 OOM → 减小 Segment 大小或增加 DataNode 内存",
    },
    "QueryCoord": {
        "正常": ["Load collection completed", "Segment assigned to QueryNode"],
        "异常": ["QueryNode heartbeat timeout", "no available QueryNode"],
        "排查": "如果 heartbeat timeout → 检查 QueryNode 是否 OOM/崩溃",
    },
    "QueryNode": {
        "正常": ["Search completed", "Segment loaded"],
        "异常": ["search timeout", "out of memory", "load segment failed"],
        "排查": "如果 search timeout → 增大 ef 参数或加 QueryNode",
    },
    "IndexNode": {
        "正常": ["Index build completed", "Index file uploaded"],
        "异常": ["build index timeout", "no enough memory for index"],
        "排查": "如果 memory 不足 → 考虑 IVF_SQ8 替代 HNSW",
    },
}

for component, logs in COMPONENT_LOGS.items():
    print(f"\n[{component}]")
    print(f"  正常: {logs['正常'][0]}")
    print(f"  异常: {logs['异常'][0]}")
    print(f"  排查: {logs['排查']}")
```

#### 步骤 4：扩缩容验证

```bash
# 扩容 QueryNode（K8s 命令）
kubectl scale deployment milvus-cluster-querynode -n milvus --replicas=5

# 缩容
kubectl scale deployment milvus-cluster-querynode -n milvus --replicas=3

# 验证：QueryCoord 日志中应看到新 QueryNode 的注册和 Segment 重新分配
kubectl logs -n milvus deployment/milvus-cluster-querycoord --tail=50
```

---

## 4. 项目总结

### 4.1 优缺点对比

| 维度 | Standalone | Cluster |
|------|-----------|---------|
| 部署复杂度 | 低（docker compose） | 高（Helm/K8s + 多个服务） |
| 可用性 | 单点故障 | 多副本高可用（除 Coordinator） |
| 扩展性 | 固定（1 进程） | 灵活（Node 独立扩缩容） |
| 资源利用率 | 低（所有功能挤在一起） | 高（按需分配资源） |
| 运维成本 | 低 | 高（日志/监控/告警 ×N 组件） |

### 4.2 适用场景

- **Standalone**：开发测试、小规模 POC、数据量 < 100 万
- **Cluster**：生产环境、数据量 > 100 万、需要高可用和高并发

### 4.3 注意事项

- Coordinator 是单点（v2.5 中 RootCoord/DataCoord 仍为单点），需配置健康检查和自动重启。
- Cluster 的网络延迟（Proxy→Coordinator→Node）是叠加的，单次搜索延迟通常比 Standalone 多 1-3ms。
- 对象存储（MinIO/S3）是全局共享的，不需要每个 Node 单独挂盘。

### 4.4 思考题

1. 如果 QueryCoord 宕机但 QueryNode 仍正常运行，已加载的 Collection 是否还能搜索？为什么？
2. DataNode 扩容到 4 个后，发现写入吞吐没有线性增长（只从 5000 条/s 涨到 8000 条/s）——瓶颈可能在哪里？

---

> **下一章预告**：第18章将深入写入链路——追踪一条向量从 Proxy 到对象存储的完整旅程。读完本章，你应该能独立部署和管理 Milvus Cluster。
