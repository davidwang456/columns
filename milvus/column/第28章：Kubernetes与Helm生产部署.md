# 第28章：Kubernetes 与 Helm 生产部署

> **定位**：把 Milvus 放进云原生生产环境。
> **版本**：Milvus 2.5.x / Helm Chart
> **源码关联**：deployments/helm/、deployments/k8s/、configs/milvus.yaml

---

## 1. 项目背景

运维团队接到任务：将 Milvus 从 Standalone Docker 迁移到 K8s 生产集群。要求支持水平扩缩容、滚动升级、存储持久化和健康检查。老周虽然熟悉 K8s 的基本操作，但 Milvus 的 Helm Chart 涉及 10+ 个服务组件，配置参数将近 200 个。

第一次部署时，他把 Helm Chart 的默认值直接 apply 了——结果出了三个问题：

1. **PVC 没有配置 StorageClass**：默认用了集群的 default StorageClass（HDD），etcd 的 IO 延迟飙升，Coordinator 频繁 timeout。
2. **Pod 反亲和没有配置**：两个 QueryNode 被调度到了同一个物理节点上，该节点内存不足，两个 QN 都 OOM。
3. **健康检查过于严格**：默认的 `readinessProbe` 在 Pod 启动后立即检查，但 Segment 加载需要 30 秒，Pod 被反复重启。

更麻烦的是，业务方要求灰度升级——从 Milvus 2.4 升级到 2.5，但希望先让 10% 流量走新版。老周不知道怎么在 Helm Chart 层面做多版本共存。

本章将覆盖 Helm Chart 关键配置、存储选型、滚动升级策略和生产优化。

---

## 2. 项目设计（剧本式交锋对话）

**第一幕：Helm Chart 核心配置——不是所有参数都要改**

*（老周把 Helm Chart 的 values.yaml 打印出来——整整 8 页，200+ 参数）*

**小胖**（绝望地）："200 个参数！我改到什么时候——"

**大师**："你不需要改 200 个参数。生产部署只需要关注 15 个核心参数，其他都用默认值。"

**大师**（在白板上圈出核心参数）：

```
values.yaml 核心参数分类:

┌─────────────────────────────────────────────────────────────┐
│ 一类: 组件副本数 (决定高可用)                                  │
│   proxy.replicas: 2                                         │
│   queryNode.replicas: 3                                     │
│   dataNode.replicas: 2                                      │
│   indexNode.replicas: 2                                     │
│   mixCoordinator.replicas: 1  ← Coordinator 单点 (v2.5)      │
├─────────────────────────────────────────────────────────────┤
│ 二类: 资源配置 (决定成本)                                       │
│   queryNode.resources.requests.memory: "16Gi"                │
│   queryNode.resources.limits.memory: "32Gi"                  │
│   dataNode.resources.requests.memory: "8Gi"                  │
│   etcd.resources.requests.memory: "2Gi"                      │
├─────────────────────────────────────────────────────────────┤
│ 三类: 存储 (决定性能和持久性)                                    │
│   etcd.persistence.storageClass: "fast-ssd"                  │
│   minio.persistence.storageClass: "standard-hdd"             │
│   minio.persistence.size: "500Gi"                            │
├─────────────────────────────────────────────────────────────┤
│ 四类: 调度策略 (决定稳定性)                                      │
│   queryNode.nodeSelector: {pool: "milvus-worker"}             │
│   queryNode.affinity.podAntiAffinity: {...}                   │
│   dataNode.tolerations: [...]                                │
├─────────────────────────────────────────────────────────────┤
│ 五类: 服务暴露 (决定可访问性)                                     │
│   service.type: ClusterIP  (或 LoadBalancer)                  │
│   proxy.service.port: 19530                                  │
│   attu.enabled: true                                         │
└─────────────────────────────────────────────────────────────┘
```

**大师**："关键决策表——"

| 决策 | 选项 | 推荐 |
|------|------|------|
| **etcd 存储** | SSD（快但贵）vs HDD（慢但便宜） | **SSD**——etcd 的 IO 直接影响 Coordinator 性能 |
| **MinIO 存储** | SSD vs HDD | HDD 即可——对象存储是顺序写，不需要随机 IO |
| **消息队列** | Pulsar vs Kafka vs StreamingNode | StreamingNode（2.5+，减少外部依赖） |
| **Coordinator** | 独立 3 进程 vs 混合部署 | 混合部署（mixCoordinator）节省资源 |
| **QueryNode 节点** | 独立节点 vs 与其他服务混部 | **独立节点**——内存密集型，避免争抢 |

> **技术映射**：etcd 要 SSD = 会计的账本要放在手边随时翻（不能放在地下仓库）；MinIO 用 HDD = 仓库的货架可以用便宜的（顺序存取的）；QueryNode 独立节点 = 健身房的大块头自己用一间房（内存大，不能和别人挤）。

---

**第二幕：Pod 反亲和、节点选择与生产优化**

**小白**："Pod 反亲和是什么？为什么我的两个 QueryNode 会被调度到同一台机器上？"

**大师**："K8s 默认调度策略不考虑'同一服务的 Pod 应该分散到不同节点'——所以两个 QueryNode 可能落在同一台 32GB 的机器上，每个要 16GB，刚好用完但没冗余。Pod 反亲和就是告诉 K8s：'这两个 Pod 别放一起'。"

```yaml
# values.yaml — QueryNode 反亲和配置
queryNode:
  affinity:
    podAntiAffinity:
      requiredDuringSchedulingIgnoredDuringExecution:
        - labelSelector:
            matchExpressions:
              - key: app.kubernetes.io/component
                operator: In
                values: ["querynode"]
          topologyKey: kubernetes.io/hostname  # 不同物理机
```

**大师**："生产环境的四个调度优化——"

| 优化 | 配置方式 | 效果 |
|------|---------|------|
| **Pod 反亲和** | `podAntiAffinity` | 相同组件的 Pod 分散到不同节点 |
| **节点选择** | `nodeSelector: {pool: milvus}` | 专用节点池，避免与其他服务混部 |
| **资源限制** | `resources.limits` | 防止单个 Pod 内存泄漏拖垮整个节点 |
| **污点容忍** | `tolerations` | QueryNode 可以调度到 GPU/大内存专用节点 |

**大师**："另外，健康检查配置也很关键——"

```yaml
# 推荐配置：给 QueryNode 足够的启动时间
queryNode:
  readinessProbe:
    initialDelaySeconds: 60    # ★ 等 60 秒再检查 (Segment 加载需要时间)
    periodSeconds: 10
    failureThreshold: 5        # ★ 容忍 5 次失败 (共 50 秒)
  livenessProbe:
    initialDelaySeconds: 120
    periodSeconds: 20
```

> **技术映射**：反亲和 = 消防通道（不能让两个人堵在同一个出口）；initialDelaySeconds = 汽车预热（刚启动时不要猛踩油门）；failureThreshold = 容忍度（偶尔打不着火再试几次，不是一次就报废）。

---

**第三幕：灰度升级——从 2.4 到 2.5 的安全过渡**

**小胖**："灰度升级怎么做？我怕升级到一半全挂了——"

**大师**："三步灰度升级法——"

```
Milvus 版本灰度升级 (2.4 → 2.5):

Step 1: 部署新版集群 (新版本 2.5)
  helm install milvus-v25 milvus/milvus --version 2.5.5 \
    -f values-v25.yaml --namespace milvus-v25

Step 2: 双写数据
  # 业务代码改造：同时写入两个集群
  milvus_v24.insert(data)
  milvus_v25.insert(data)

Step 3: 灰度切换读流量
  # 路由层按比例分流
  traffic_router:
    milvus_v24: 90%  # 稳定版
    milvus_v25: 10%  # 灰度

Step 4: 对比监控 + 逐步放量
  10% → 观察 1 小时 → 50% → 观察 2 小时 → 100%

Step 5: 下线旧版
  # 停写 + 清退旧集群
  helm uninstall milvus-v24 --namespace milvus-v24
```

**大师**："关键原则——"

| 原则 | 说明 |
|------|------|
| **先部署后切换** | 新版集群完全就绪后再切流量 |
| **可回滚** | 保留旧版集群至少 1 周，确认新版稳定后再删除 |
| **数据双写** | 灰度期间新旧都要写数据，保证切换时数据不丢 |
| **分批放量** | 10%→50%→100%，每步观察监控至少 30 分钟 |

---

## 3. 项目实战

### 3.1 实战目标

在 K8s 中部署一个高可用 Milvus 集群，配置 Pod 反亲和和滚动升级。

### 3.2 环境准备

```bash
kubectl create namespace milvus-prod
helm repo add milvus https://zilliztech.github.io/milvus-helm
helm repo update
```

### 3.3 分步实现

#### 步骤 1：生产级 values.yaml

```yaml
# values-prod.yaml
# 安装: helm install milvus-prod milvus/milvus -f values-prod.yaml -n milvus-prod

cluster:
  enabled: true

# ── Proxy ──
proxy:
  replicas: 2
  resources:
    requests: {memory: "2Gi", cpu: "1"}
    limits: {memory: "4Gi", cpu: "2"}
  service:
    type: ClusterIP

# ── QueryNode ──
queryNode:
  replicas: 3
  resources:
    requests: {memory: "16Gi", cpu: "2"}
    limits: {memory: "32Gi", cpu: "4"}
  affinity:
    podAntiAffinity:
      requiredDuringSchedulingIgnoredDuringExecution:
        - labelSelector:
            matchExpressions:
              - key: app.kubernetes.io/component
                operator: In
                values: ["querynode"]
          topologyKey: kubernetes.io/hostname
  readinessProbe:
    initialDelaySeconds: 60
    periodSeconds: 10
    failureThreshold: 5

# ── DataNode ──
dataNode:
  replicas: 2
  resources:
    requests: {memory: "4Gi", cpu: "1"}
    limits: {memory: "8Gi", cpu: "2"}

# ── IndexNode ──
indexNode:
  replicas: 2
  resources:
    requests: {memory: "4Gi", cpu: "1"}

# ── Coordinator (混合部署) ──
mixCoordinator:
  replicas: 1
  resources:
    requests: {memory: "1Gi", cpu: "0.5"}

# ── Storage ──
etcd:
  replicaCount: 3
  persistence:
    storageClass: "fast-ssd"
    size: 20Gi

minio:
  persistence:
    storageClass: "standard"
    size: 500Gi
    
pulsar:
  enabled: false

streamingNode:
  enabled: true
```

#### 步骤 2：部署验证脚本

```python
# step2_deploy_verify.py
"""K8s Milvus 部署验证脚本"""
import subprocess
import json
import time

NS = "milvus-prod"
RELEASE = "milvus-prod"

def kubectl(*args):
    cmd = ["kubectl"] + list(args) + ["-n", NS]
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.stdout.strip()

print("=" * 60)
print("Milvus K8s 部署验证")
print("=" * 60)

# 1. Pod 状态
print("\n1. Pod 状态:")
pods = kubectl("get", "pods", "-o", "json")
pods_data = json.loads(pods)
for item in pods_data.get("items", []):
    name = item["metadata"]["name"]
    phase = item["status"]["phase"]
    ready = f"{item['status'].get('ready', '?')}/{len(item['spec']['containers'])}"
    icon = "✓" if phase == "Running" else "✗"
    print(f"  {icon} {name:<50} {phase:<10} {ready}")

# 2. Service
print("\n2. Service 端点:")
svcs = kubectl("get", "svc", "-o", "json")
svcs_data = json.loads(svcs)
for item in svcs_data.get("items", []):
    name = item["metadata"]["name"]
    ports = [f"{p['port']}" for p in item.get("spec", {}).get("ports", [])]
    print(f"  {name:<30} {item['spec']['type']:<12} {', '.join(ports)}")

# 3. PVC 状态
print("\n3. PVC 状态:")
pvcs = kubectl("get", "pvc", "-o", "json")
pvcs_data = json.loads(pvcs)
for item in pvcs_data.get("items", []):
    name = item["metadata"]["name"]
    phase = item["status"]["phase"]
    size = item["spec"]["resources"]["requests"]["storage"]
    print(f"  {name:<40} {phase:<12} {size}")

# 4. 验证 Milvus 连接
print("\n4. Milvus 连接验证:")
# Port-forward Proxy
print("  执行: kubectl port-forward svc/milvus-proxy 19530:19530 -n milvus-prod")
print("  然后运行: python -c 'from pymilvus import connections, utility; "
      "connections.connect(\"localhost\", \"19530\"); "
      "print(utility.get_server_version())'")
```

#### 步骤 3：滚动升级演练

```bash
# 升级到新版本
helm upgrade milvus-prod milvus/milvus \
  -f values-prod.yaml \
  --set image.tag=v2.5.6 \
  --namespace milvus-prod \
  --wait --timeout 10m

# 观察滚动升级过程
kubectl rollout status deployment/milvus-prod-querynode -n milvus-prod
kubectl get pods -n milvus-prod -w

# 如果升级失败，回滚
helm rollback milvus-prod 1 --namespace milvus-prod
```

#### 步骤 4：生产环境 ConfigMap 与配置分离

```yaml
# configmap.yaml — 将 Milvus 配置从 values.yaml 中分离
apiVersion: v1
kind: ConfigMap
metadata:
  name: milvus-custom-config
  namespace: milvus-prod
data:
  milvus.yaml: |
    # 对象存储配置（覆盖 Helm Chart 中的默认值）
    minio:
      address: minio.milvus-prod.svc.cluster.local
      port: 9000
      accessKeyID: minioadmin
      secretAccessKey: minioadmin
      useSSL: false
      bucketName: milvus-data
      rootPath: files
    
    # 日志配置
    log:
      level: info
      file:
        rootPath: /var/log/milvus
        maxSize: 300       # MB
        maxAge: 10         # 天
        maxBackups: 20
    
    # 查询节点配置
    queryNode:
      cacheSize: 32        # GB
      gracefultime: 30     # 秒（优雅停机时间）
    
    # Compaction 配置
    dataCoord:
      compaction:
        enableAutoCompaction: true
        maxSegmentSize: 1024  # MB

# 通过 Helm values 引用自定义 ConfigMap
# values-prod.yaml:
#   customConfigMap: "milvus-custom-config"
```

#### 步骤 5：K8s 部署故障演练

```python
# step5_k8s_chaos.py
"""K8s Milvus 故障演练脚本"""
import subprocess
import time

NS = "milvus-prod"

def delete_pod(component: str):
    """删除一个 Pod 模拟故障"""
    print(f"\n[演练] 删除 {component} Pod...")
    result = subprocess.run(
        ["kubectl", "delete", "pod", "-n", NS, "-l",
         f"app.kubernetes.io/component={component}", "--wait=false"],
        capture_output=True, text=True
    )
    print(result.stdout.strip())
    return time.time()

def watch_recovery(start_time: float):
    """观察 Pod 恢复"""
    print(f"[观察] 等待 Pod 恢复...")
    for i in range(12):  # 最多等 60 秒
        time.sleep(5)
        result = subprocess.run(
            ["kubectl", "get", "pods", "-n", NS],
            capture_output=True, text=True
        )
        if "Running" in result.stdout and "0/1" not in result.stdout:
            elapsed = time.time() - start_time
            print(f"  恢复成功! 耗时 {elapsed:.1f} 秒")
            return elapsed
    print("  恢复超时 (>60s)")
    return 60

# 演练 1: 删除一个 QueryNode（有 Replica 应该 0 秒影响）
print("=" * 60)
print("故障演练 1: QueryNode 宕机")
print("=" * 60)
t = delete_pod("querynode")
r = watch_recovery(t)
print(f"恢复时间: {r:.1f}s (有 Replica 时搜索应不中断)")

# 演练 2: 删除 Proxy（有其他 Proxy 接替）
print("\n" + "=" * 60)
print("故障演练 2: Proxy 宕机")
print("=" * 60)
t = delete_pod("proxy")
r = watch_recovery(t)
print(f"恢复时间: {r:.1f}s (其他 Proxy 自动接替)")
```

#### 步骤 6：多环境部署策略

```python
# step6_multi_env.py
"""生成多环境部署对比配置"""
environments = {
    "dev": {
        "queryNode.replicas": 1,
        "queryNode.resources.requests.memory": "4Gi",
        "replica_number": 1,
        "etcd.replicaCount": 1,
        "minio.persistence.size": "50Gi",
        "description": "开发环境：最小化配置，快速部署"
    },
    "staging": {
        "queryNode.replicas": 2,
        "queryNode.resources.requests.memory": "8Gi",
        "replica_number": 1,
        "etcd.replicaCount": 3,
        "minio.persistence.size": "200Gi",
        "description": "测试环境：接近生产配置，验证功能"
    },
    "prod": {
        "queryNode.replicas": 6,
        "queryNode.resources.requests.memory": "16Gi",
        "replica_number": 2,
        "etcd.replicaCount": 3,
        "minio.persistence.size": "2000Gi",
        "description": "生产环境：高可用配置，Replica=2"
    },
}

print("多环境部署配置对比:")
print("=" * 60)
for env, config in environments.items():
    print(f"\n[{env.upper()}] {config['description']}")
    for k, v in config.items():
        if k != "description":
            print(f"  {k}: {v}")
```

---

## 4. 项目总结

### 4.1 生产部署 Checklist

```
□ etcd 使用 SSD StorageClass
□ MinIO 配置足够的持久化容量 (预估: 原始数据 × 1.5 + 索引)
□ QueryNode 配置 Pod 反亲和
□ readinessProbe initialDelaySeconds ≥ 60
□ 配置资源 requests/limits
□ Coordinator 单点告警配置
□ 备份 ConfigMap 和 values.yaml 到 Git
```

### 4.2 注意事项

- **PVC 扩容不是自动的**：存储满了需要手动扩容 PVC 或清理数据。
- **Helm Chart 升级前先 diff**：`helm diff upgrade` 预览变更。
- **不要在生产环境用 `helm install --force`**：会强制替换已有资源。

### 4.3 思考题

1. 如果 K8s 集群需要跨 AZ 部署 Milvus 实现机房级容灾，QueryNode 的拓扑分布如何设计？
2. etcd 的 3 节点部署在生产中如何做备份？万一 3 个节点全挂了怎么恢复？

---

> **下一章预告**：第29章将学习 RAG 的生产化治理——从 Demo 到可运营系统。读完本章，你应该能独立在 K8s 上部署生产级 Milvus 集群。
