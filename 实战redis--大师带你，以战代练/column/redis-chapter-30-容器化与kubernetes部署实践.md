# 第30章：容器化与Kubernetes部署实践

## 1. 项目背景

公司基础设施逐步迁移到 Kubernetes，应用服务已经容器化，Redis 却仍由少数运维同学手工部署在虚拟机上。开发希望测试环境能一键拉起 Redis，运维希望生产实例有统一的资源限制、探针、存储和监控，架构团队则担心容器网络、持久化卷和滚动更新会影响 Redis 稳定性。

Redis 容器化不是把 `redis-server` 塞进镜像就结束。它涉及配置挂载、数据卷、资源限制、健康检查、反亲和、持久化、备份、监控接入和故障恢复。尤其在 Kubernetes 中，Redis 这类有状态服务通常需要 StatefulSet、Headless Service 和 PVC，而不是普通 Deployment。

本章用一个“主从 Redis + Sentinel 可演进部署”为实战背景，先给出单实例 StatefulSet，再说明如何扩展到高可用。目标是让读者掌握 Redis 在 K8s 中的部署骨架和风险点，为后续生产级平台打基础。

## 2. 项目设计

小胖先问：“Docker 里跑 Redis 我会，`docker run redis` 就行。放到 K8s 里是不是写个 Deployment？”

小白马上追问：“Redis 有数据目录，Pod 重建后名字和存储要稳定。Deployment 适合无状态服务，Redis 至少要考虑 PVC 和稳定网络标识。”

大师说：“K8s 里跑 Redis，第一选择通常是 StatefulSet。它提供稳定 Pod 名称、稳定存储和有序启动。Headless Service 负责给每个 Pod 暴露固定 DNS。技术映射：Redis 是有状态服务，编排对象要体现身份和数据生命周期。”

小胖又说：“那我把内存 limit 写大点，K8s 自动管不就行？”

小白反问：“如果 Redis 的 `maxmemory` 大于容器 limit，系统 OOM Kill 怎么办？如果探针太激进，Redis 正在加载 AOF 时被反复重启怎么办？”

大师回答：“容器资源限制必须和 Redis 配置一致。`maxmemory` 要低于容器 memory limit，留出碎片、fork 和缓冲区空间。探针要理解 Redis 启动恢复时间，不要把恢复中的实例误杀。技术映射：K8s 负责调度和重启，Redis 仍需要自己的容量和持久化规划。”

小胖最后问：“生产是不是直接手写 YAML？”

大师说：“小规模可以手写，团队化建议用 Helm Chart 或 Redis Operator。Operator 能封装主从、Sentinel、备份和扩缩容，但也要评估成熟度。不要因为工具高级就跳过原理。”

## 3. 项目实战

### 3.1 ConfigMap 与配置

先准备 Redis 配置：

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: redis-config
data:
  redis.conf: |
    appendonly yes
    dir /data
    protected-mode no
    maxmemory 768mb
    maxmemory-policy allkeys-lfu
    tcp-keepalive 60
```

如果容器 memory limit 是 1Gi，`maxmemory` 配 768Mi 左右更稳妥，给 fork、碎片和缓冲区留空间。

### 3.2 StatefulSet 单实例

```yaml
apiVersion: v1
kind: Service
metadata:
  name: redis
spec:
  clusterIP: None
  selector:
    app: redis
  ports:
    - name: redis
      port: 6379
---
apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: redis
spec:
  serviceName: redis
  replicas: 1
  selector:
    matchLabels:
      app: redis
  template:
    metadata:
      labels:
        app: redis
    spec:
      containers:
        - name: redis
          image: redis:8.6
          command: ["redis-server", "/usr/local/etc/redis/redis.conf"]
          ports:
            - containerPort: 6379
          resources:
            requests:
              cpu: "500m"
              memory: "1Gi"
            limits:
              cpu: "1"
              memory: "1Gi"
          volumeMounts:
            - name: config
              mountPath: /usr/local/etc/redis
            - name: data
              mountPath: /data
          readinessProbe:
            exec:
              command: ["redis-cli", "PING"]
            initialDelaySeconds: 10
            periodSeconds: 5
          livenessProbe:
            exec:
              command: ["redis-cli", "PING"]
            initialDelaySeconds: 60
            periodSeconds: 10
      volumes:
        - name: config
          configMap:
            name: redis-config
  volumeClaimTemplates:
    - metadata:
        name: data
      spec:
        accessModes: ["ReadWriteOnce"]
        resources:
          requests:
            storage: 20Gi
```

部署和验证：

```bash
kubectl apply -f redis-config.yaml
kubectl apply -f redis-statefulset.yaml
kubectl get pod,pvc,svc -l app=redis
kubectl exec -it redis-0 -- redis-cli SET k8s:demo ok
kubectl exec -it redis-0 -- redis-cli GET k8s:demo
```

### 3.3 运维流程与故障验证

删除 Pod 验证 PVC 保留：

```bash
kubectl delete pod redis-0
kubectl get pod redis-0 -w
kubectl exec -it redis-0 -- redis-cli GET k8s:demo
```

观察日志和配置：

```bash
kubectl logs redis-0
kubectl exec -it redis-0 -- redis-cli INFO persistence
kubectl exec -it redis-0 -- redis-cli INFO memory
```

生产变更流程建议：

1. 配置改动先在测试命名空间验证。
2. 检查 `maxmemory`、容器 limit、PVC 容量和 AOF 策略是否匹配。
3. 变更前备份 RDB/AOF 或做快照。
4. 灰度重启，观察 readiness、复制延迟、命中率和慢日志。
5. 演练节点驱逐、Pod 重建、PVC 挂载失败和网络抖动。

### 3.4 高可用与 Operator 选择

主从或 Sentinel 可以继续使用 StatefulSet 扩展，但手写脚本要处理主从发现、故障转移和客户端重连。生产上常见选择：

```text
简单测试：单 Redis StatefulSet
中小规模：Helm Chart 部署主从 + Sentinel
平台化：Redis Operator 管理实例、备份、扩缩容和监控
高吞吐分片：Redis Cluster 或云厂商托管 Redis
```

反亲和示例：

```yaml
affinity:
  podAntiAffinity:
    preferredDuringSchedulingIgnoredDuringExecution:
      - weight: 100
        podAffinityTerm:
          labelSelector:
            matchLabels:
              app: redis
          topologyKey: kubernetes.io/hostname
```

监控接入可以把 Redis Exporter 作为 sidecar 或独立 Deployment，通过 ServiceMonitor 交给 Prometheus Operator 采集。

常见坑：第一，用 Deployment 跑生产 Redis，Pod 重建后身份和存储不可控。第二，`maxmemory` 贴近容器 limit，碎片或 fork 时被 OOM Kill。第三，livenessProbe 太激进，AOF 加载慢时反复重启。第四，PVC 使用低性能存储，AOF fsync 抖动影响延迟。第五，滚动更新前没有确认主从和 Sentinel 状态。

## 4. 项目总结

Redis 容器化的关键是尊重有状态服务的特征。StatefulSet 解决稳定身份和存储，ConfigMap 解决配置管理，PVC 解决数据持久化，资源限制和探针解决运行边界，监控与演练保证可运营。

优点：部署标准化、环境一致、扩缩容和迁移更可控，便于接入平台监控。缺点：K8s 增加了网络、存储和调度复杂度；Redis 高可用仍需要 Sentinel、Cluster、Operator 或托管服务配合；错误探针和资源限制会放大故障。

适用场景包括测试环境一键部署、内部平台实例管理、中小规模缓存服务和云原生运维体系。不适合在没有存储、备份、监控和故障演练的情况下直接承载核心交易链路。

思考题：
1. 为什么 StatefulSet 比 Deployment 更适合 Redis？
2. `maxmemory`、容器 memory limit 和节点物理内存之间应该如何留安全余量？

推广建议：开发团队熟悉连接地址和配置变更流程，测试团队负责 Pod 删除、节点驱逐和存储异常演练，运维团队维护 Helm、Operator、备份和监控，架构团队决定哪些业务适合自建 K8s Redis，哪些应使用托管服务。
