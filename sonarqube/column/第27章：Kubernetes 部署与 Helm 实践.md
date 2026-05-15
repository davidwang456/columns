# 第27章：Kubernetes 部署与 Helm 实践

## 1. 项目背景

**业务场景**：某公司的运维团队在 6 个月内将 80% 的应用迁移到了 Kubernetes，现在只剩下 SonarQube 仍运行在一台物理服务器上。运维团队希望将 SonarQube 也迁移到 K8s，实现统一的部署、监控和备份体系。但 SonarQube 的特殊性带来了挑战：Elasticsearch 需要 `vm.max_map_count` 内核参数、PostgreSQL 数据库需要持久化存储、CE 和 Web 组件需要独立扩缩容。

**痛点放大**：

- **Docker Compose 到 K8s 的迁移**：单机部署的配置不能直接平移，需要重新设计 Pod 和 Service
- **持久化存储困境**：Elasticsearch 的索引数据、PostgreSQL 的数据文件都需要 PVC，但不同 StorageClass 的 IO 性能差异巨大
- **资源限制冲突**：ES 的内存需求与 K8s 的 Pod 资源限制机制存在冲突
- **探针配置**：就绪探针和存活探针的时机和参数容易配置错误，导致 Pod 反复重启

**更多现实场景**：

- **场景一：Pod 被驱逐后数据丢失**：团队使用默认的 `emptyDir` 作为 ES 数据存储（没有配置 PVC）。某天 Node 内存不足，kubelet 驱逐了 SonarQube Pod。Pod 在另一台 Node 上重建后，所有历史分析数据全部丢失——ES 索引、项目配置、质量门禁历史，一片空白。恢复只能靠数据库备份 + Reindex，耗时 4 小时。

- **场景二：探针配置不当导致启动循环**：团队将 `initialDelaySeconds` 设为 30 秒，但 SonarQube 启动需要 2-3 分钟（加载插件、建立 ES 连接）。K8s 在 30 秒后开始探活失败计数，6 次失败后 kill Pod 重启——导致 SonarQube 永远无法完成启动，陷入 CrashLoopBackOff。

- **场景三：资源限制过紧导致 OOMKilled**：团队为 SonarQube Pod 设置了 `memory: 2Gi` 的 limit，但 ES 的 JVM 堆已经配置了 2GB（`-Xmx2g`），加上 JVM 自身开销和 native memory，实际内存使用超过 3GB。Pod 被 OOMKiller 反复杀死，每次刚启动不到 5 分钟就挂了。

- **场景四：Ingress 上传限制导致大报告被拒**：一个大型 Java 项目（2000+ 文件）的 Scanner 报告压缩后有 50MB。默认的 Nginx Ingress `proxy-body-size` 是 1MB，报告上传被 413 错误拒绝。团队排查了 3 个小时才发现是 Ingress 层的问题，而非 SonarQube 本身。

**K8s 部署关键决策**：
1. Persistence 用什么 StorageClass？本地磁盘、网络块存储还是 NFS？
2. PostgreSQL 是集群内 StatefulSet 还是外部的云数据库？
3. Ingress 层如何处理 Scanner 上传的大请求体？
4. 如何实现 ES 的 `vm.max_map_count` 在所有 Node 上统一配置？
5. 升级 Helm Chart 时如何保证零停机（或最小停机）？

## 2. 项目设计

### 剧本式交锋对话

---

**小胖**（打开 Helm Chart 的 values.yaml，密密麻麻数百行）："大师，Helm Chart 里的 values.yaml 有 500 行配置——哪些是必须改的？哪些用默认值就行？"

**大师**："对于测试环境，你只需要改 5 个值。对于生产环境，大约 20 个值。别被 500 行吓到——其中 90% 都是可选的。"

**小白**："SonarQube 在 K8s 上运行时，哪些是特别需要注意的点？"

**大师**："三个最关键的：

1. **Elasticsearch 的 `vm.max_map_count`**：K8s 节点上必须设置这个内核参数。如果你用 AWS EKS 或 GKE，需要在 Node 启动脚本中设置。
2. **持久化存储**：ES 数据（`/opt/sonarqube/data`）必须使用 PVC。StorageClass 的选择直接影响 ES 的性能——本地 SSD > 网络块存储 > NFS。
3. **PostgreSQL 外置**：生产环境永远不要用 SonarQube 内嵌的 H2 或 Chart 自带的 PostgreSQL。使用独立部署的 PostgreSQL（可以是 K8s 外的 RDS / Cloud SQL，也可以是 K8s 内的专用 StatefulSet）。"

**小胖**："大师，资源限制这里我一直搞不太清楚。SonarQube Pod 到底需要多少 CPU 和内存？我设 requests 和 limits 设多少合适？"

**大师**："这是 K8s 部署中最需要精打细算的配置。SonarQube Pod 里实际运行了三个进程——Web Server、CE Worker、Elasticsearch——它们共享 Pod 的资源限制。资源需求按规模来算：

**小型（< 50 项目）**：
```yaml
resources:
  requests:
    cpu: 1
    memory: 3Gi
  limits:
    cpu: 2
    memory: 4Gi
```

**中型（50-200 项目）**：
```yaml
resources:
  requests:
    cpu: 2
    memory: 6Gi
  limits:
    cpu: 4
    memory: 8Gi
```

**大型（200-1000 项目）**：
```yaml
resources:
  requests:
    cpu: 4
    memory: 12Gi
  limits:
    cpu: 8
    memory: 16Gi
```

但有**一个关键约束**：ES 的 JVM 内存（`SONAR_SEARCH_JAVAOPTS` 中的 `-Xmx`）+ Web JVM 内存 + CE JVM 内存的总和不能超过 `limits.memory`。最安全的公式是：

```
limits.memory ≥ ES_Xmx + Web_Xmx + CE_Xmx + 1GB（JVM overhead + OS）
```

如果 JVM 堆总和接近 limits，Pod 会被 OOMKilled——因为 JVM 之外还有 native memory 的开销（通常占堆内存的 20-30%）。"

**小白**："探针参数怎么调？我们之前设了 initialDelaySeconds=30，Pod 一直重启..."

**大师**："探针的 `initialDelaySeconds` 必须大于 SonarQube 的启动时间。启动时间主要取决于：

1. **插件数量**：插件越多启动越慢（每个插件都要加载和初始化）
2. **ES 连接建立**：如果 ES 数据目录已有数据，启动更快
3. **数据库连接**：首次启动或数据库迁移时会慢

**推荐配置**：

```yaml
# 存活探针 - 检测进程是否活着
livenessProbe:
  httpGet:
    path: /api/system/health
    port: 9000
  initialDelaySeconds: 180   # 给足 3 分钟启动时间
  periodSeconds: 30
  failureThreshold: 6        # 连续失败 6 次 = 3 分钟才重启
  timeoutSeconds: 10

# 就绪探针 - 检测服务是否可接受流量
readinessProbe:
  httpGet:
    path: /api/system/status
    port: 9000
  initialDelaySeconds: 120   # 启动 2 分钟后开始检查
  periodSeconds: 10          # 更频繁地检查
  failureThreshold: 3
  timeoutSeconds: 5

# 启动探针 - K8s 1.16+ 专用，启动期间不触发 liveness
startupProbe:
  httpGet:
    path: /api/system/health
    port: 9000
  initialDelaySeconds: 30
  periodSeconds: 10
  failureThreshold: 30       # 最多等 30×10 = 5 分钟启动
```

关键区别：
- `livenessProbe` 失败 → K8s 杀掉 Pod 重启（用于死锁/卡死场景）
- `readinessProbe` 失败 → K8s 从 Service 后端摘除（用于临时过载）
- `startupProbe` 失败前不会触发 livenessProbe（保护启动过程）

**小胖**："那 StorageClass 怎么选？我们环境里有 local-path、Ceph RBD、还有 NFS——用哪个最好？"

**大师**："按性能排序：本地 SSD > 网络块存储（Ceph RBD / AWS EBS）> NFS > 对象存储。具体选择看你的环境：

| StorageClass | IOPS | 延迟 | 适用场景 | 缺点 |
|-------------|------|------|---------|------|
| local-path (hostPath) | 最高 | 最低 | 单节点测试/小规模 | Pod 不能漂移到其他节点 |
| gp3 / gp2 (AWS EBS) | 高 | 低 | AWS 生产环境 | 只能单 AZ |
| managed-premium (Azure) | 高 | 低 | Azure 生产环境 | 成本较高 |
| Ceph RBD | 中高 | 中 | 私有云自建 | 运维复杂度 |
| NFS | 中 | 中 | 共享存储场景 | ES 不推荐（随机写性能差） |

**生产环境推荐**：云上的话用云厂商的块存储（EBS/Managed Disk）+ `ReadWriteOnce`。自建集群的话用 Ceph RBD 或 Longhorn。**绝对不要用 NFS 给 ES 做数据盘**——ES 的随机读写和 mmap 依赖文件系统的直接访问能力，NFS 的锁机制和网络延迟会导致 ES 频繁超时。"

---

## 3. 项目实战

### 3.1 环境准备

- Kubernetes 1.27+ 集群
- Helm 3.13+
- 独立的 PostgreSQL 实例（或使用 Helm 自带的）

### 3.2 分步实现

**步骤 1：添加 SonarQube Helm 仓库**

```bash
helm repo add sonarqube https://SonarSource.github.io/helm-chart-sonarqube
helm repo update
```

**步骤 2：创建自定义 values.yaml**

```yaml
# values.yaml
image:
  repository: sonarqube
  tag: 10.7.0-community

replicaCount: 1

# 暴露方式（NodePort 或 Ingress）
service:
  type: NodePort
  externalPort: 9000
  internalPort: 9000

# 持久化存储
persistence:
  enabled: true
  storageClass: "local-path"  # 或 gp2 / managed-premium
  accessMode: ReadWriteOnce
  size: 20Gi

# PostgreSQL 外置配置（推荐）
postgresql:
  enabled: false  # 不使用 Helm 自带的 PostgreSQL

jdbcOverwrite:
  enabled: true
  jdbcUrl: "jdbc:postgresql://postgres-prod.internal:5432/sonar"
  jdbcUsername: sonar
  jdbcPassword: "changeme"

# ES JVM 配置
elasticsearch:
  configureNode: true
  bootstrapChecks: true

# 环境变量（对应 sonar.properties）
env:
  - name: SONAR_SEARCH_JAVAOPTS
    value: "-Xms1g -Xmx2g"
  - name: SONAR_WEB_JAVAOPTS
    value: "-Xms512m -Xmx1g"
  - name: SONAR_CE_JAVAOPTS
    value: "-Xms512m -Xmx1g"
  - name: SONAR_CE_WORKER_COUNT
    value: "3"

# 资源限制
resources:
  limits:
    cpu: 4
    memory: 8Gi
  requests:
    cpu: 2
    memory: 4Gi

# Ingress 配置
ingress:
  enabled: true
  hosts:
    - name: sonarqube.company.com
  annotations:
    nginx.ingress.kubernetes.io/proxy-body-size: "100m"
  tls:
    - secretName: sonarqube-tls
      hosts:
        - sonarqube.company.com

# 健康探针
livenessProbe:
  initialDelaySeconds: 120
  periodSeconds: 30
  failureThreshold: 6
readinessProbe:
  initialDelaySeconds: 60
  periodSeconds: 30
  failureThreshold: 6
```

**步骤 3：部署**

```bash
# 安装
helm install sonarqube sonarqube/sonarqube \
  -f values.yaml \
  -n sonarqube --create-namespace

# 查看 Pod 状态
kubectl get pods -n sonarqube -w

# 查看日志
kubectl logs -n sonarqube -l app=sonarqube --tail=20
```

**步骤 4：配置 Ingress 和 TLS**

```bash
# 创建 TLS Secret
kubectl create secret tls sonarqube-tls \
  --cert=path/to/cert.pem \
  --key=path/to/key.pem \
  -n sonarqube

# 验证 Ingress
kubectl get ingress -n sonarqube
```

**步骤 5：验证部署**

```bash
# 健康检查
curl -k -u admin:admin https://sonarqube.company.com/api/system/health

# 查看资源使用
kubectl top pod -n sonarqube
```

### 3.3 生产环境 Checklist

| 项目 | 要求 |
|------|------|
| PostgreSQL 外置 | 使用独立的高可用 PostgreSQL |
| 持久化存储 | StorageClass 确保不随 Pod 删除 |
| 资源限制 | 设置 CPU/Memory limits |
| TLS 终止 | Ingress 或 LB 层完成 |
| 备份 | PVC 备份 + PostgreSQL 备份 |
| 节点亲和性 | 确保 ES Pod 调度到有足够内存的节点 |
| 反亲和性 | 多副本时避免调度到同一节点 |

### 3.4 常见问题排查

**问题 1：Pod 一直 CrashLoopBackOff**

```bash
# 查看上一次容器的退出原因
kubectl describe pod -n sonarqube <pod-name> | grep -A 5 "Last State"

# 查看 OOMKilled 记录
kubectl describe pod -n sonarqube <pod-name> | grep OOMKilled

# 如果 OOMKilled → 增加 limits.memory
# 如果 Error 退出码 137 → OOMKilled（SIGKILL）
# 如果 Error 退出码 1 → 应用内部错误，查看日志
kubectl logs -n sonarqube <pod-name> --previous | tail -50
```

**问题 2：ES 启动失败（vm.max_map_count）**

```bash
# 在所有 K8s Node 上检查
for node in $(kubectl get nodes -o name); do
    echo "=== $node ==="
    # 需要 SSH 到节点或使用 DaemonSet 检查
done

# 修复方案：使用 DaemonSet 设置内核参数（需要 privileged 权限）
# 或配置 Node 的 sysctl（kubelet 参数 --allowed-unsafe-sysctls）
```

**问题 3：Ingress 上传报告 413 错误**

```yaml
# 增大 Ingress 的 proxy-body-size
ingress:
  annotations:
    nginx.ingress.kubernetes.io/proxy-body-size: "200m"    # 允许 200MB 上传
    nginx.ingress.kubernetes.io/proxy-connect-timeout: "300"
    nginx.ingress.kubernetes.io/proxy-read-timeout: "300"
    nginx.ingress.kubernetes.io/proxy-send-timeout: "300"
```

**问题 4：插件 JAR 持久化**

SonarQube 的插件默认存储在 POD 内部路径 `/opt/sonarqube/extensions/plugins/`，Pod 重启后自定义插件会丢失。解决方案：

```yaml
# 使用 Init Container 在启动前下载插件到 shared volume
initContainers:
  - name: download-plugins
    image: busybox:1.36
    command:
      - sh
      - -c
      - |
        wget -O /plugins/sonar-pmd-plugin-3.4.0.jar \
          https://your-artifactory.example.com/plugins/sonar-pmd-plugin-3.4.0.jar
    volumeMounts:
      - name: plugins-volume
        mountPath: /plugins

volumes:
  - name: plugins-volume
    persistentVolumeClaim:
      claimName: sonarqube-plugins-pvc
```

### 3.5 验证

```bash
# Helm 状态
helm list -n sonarqube

# 所有资源就绪
kubectl wait --for=condition=ready pod -l app=sonarqube -n sonarqube --timeout=300s
```

---

## 4. 项目总结

### 4.1 K8s vs Docker Compose 部署对比

| 维度 | K8s Helm | Docker Compose |
|------|---------|---------------|
| 水平扩展 | ✅ 支持（Data Center 版） | ❌ 单机 |
| 滚动升级 | ✅ 原生支持 | ❌ 手动 |
| 资源隔离 | ✅ Namespace + Resource Quota | 🟡 容器级 |
| 自愈 | ✅ Pod 自动重启 | ❌ 需 `restart: always` |
| 复杂度 | 高（需 K8s 运维知识） | 低（一条 docker compose up） |

### 4.2 Helm values.yaml 生产环境关键参数速查

| 参数路径 | 推荐值 | 说明 |
|---------|--------|------|
| `image.tag` | `10.7.0-community` | 固定版本，避免 `latest` |
| `persistence.storageClass` | `gp3` / `managed-premium` | 块存储，非 NFS |
| `persistence.size` | `50Gi`（中型）| 预留扩容空间 |
| `postgresql.enabled` | `false` | 生产环境用外置 DB |
| `env.SONAR_SEARCH_JAVAOPTS` | `-Xms2g -Xmx4g` | ES 堆≤物理内存 50% |
| `env.SONAR_CE_WORKER_COUNT` | `2-3` | 按项目规模调整 |
| `ingress.annotations.proxy-body-size` | `"200m"` | 允许大报告上传 |
| `livenessProbe.initialDelaySeconds` | `180` | 等待启动完成 |
| `resources.limits.memory` | `8Gi+` | ≥ JVM堆总和 + 2GB |
| `nodeSelector` | `node-type: sonarqube` | 绑定到专用节点 |

### 4.3 Helm 升级最佳实践

```bash
# 1. 查看当前版本和配置差异
helm get values sonarqube -n sonarqube > current-values.yaml
helm diff upgrade sonarqube sonarqube/sonarqube -f new-values.yaml -n sonarqube

# 2. 备份 PVC 数据（快照）
# AWS: EBS Snapshot / Azure: Disk Snapshot
kubectl get pvc -n sonarqube

# 3. 在维护窗口执行升级
helm upgrade sonarqube sonarqube/sonarqube \
  -f new-values.yaml \
  -n sonarqube \
  --timeout 10m \
  --wait

# 4. 验证升级
kubectl rollout status deployment/sonarqube-sonarqube -n sonarqube
curl -k https://sonarqube.company.com/api/system/health
```

### 4.4 注意事项

1. **ES 的 `vm.max_map_count` 必须在 K8s 节点上设置**：Pod 继承 Node 的内核参数，无法在容器内修改。
2. **Community Edition 不能多副本**：只有 Data Center 版支持多副本 Web 节点。
3. **升级前先备份 PVC**：Helm 升级可能导致 PVC 数据丢失或不可恢复。

### 4.5 思考题

1. SonarQube Community Edition 在 K8s 中只支持单副本。如果 Pod 被驱逐到另一个节点，如何保证服务快速恢复？
2. 使用 StatefulSet（而非 Deployment）部署 SonarQube 有什么优缺点？

---

> **推广计划提示**：K8s 部署建议先在测试环境验证 2 周，确保 Helm Chart 配置稳定后，再按 "生产备节点 → 切换流量" 的方式迁移。不要直接从物理机/Docker 直接切到 K8s 生产环境。
