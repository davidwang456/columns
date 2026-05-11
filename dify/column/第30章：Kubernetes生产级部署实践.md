# 第30章：Kubernetes 生产级部署实践

## 1. 项目背景

Docker Compose 一把梭搞定了开发环境和小团队使用，但生产环境有三个硬骨头是 Compose 啃不动的：

**硬骨头一：大促流量自动扩缩容**。618 零点流量翻了 10 倍，你不可能半夜爬起来手动 `docker compose up -d --scale api=10`。你需要平台自动检测 CPU 使用率、自动加 Pod、流量回落后自动缩回去——这是 K8s HPA（Horizontal Pod Autoscaler）的看家本领。

**硬骨头二：滚动更新零停机**。每次 Dify 发新版本你都心惊胆战——`docker compose down && docker compose up -d` 中间有 30 秒的空白期，用户全部看到 502。你需要一次只换一个 Pod、新 Pod 健康检查通过后再接流量、老 Pod 优雅关闭——这是 K8s RollingUpdate 的标配。

**硬骨头三：多机房容灾**。某个可用区的机房断电了——你的服务不能全挂。你需要 Pod 分散部署在不同 Node 甚至不同 AZ，Ingress 自动把流量切到健康的 Pod——这是 K8s 的 `podAntiAffinity` + 多 AZ 调度。

Dify 官方目前没有发布 Helm Chart，但它的微服务架构天然适配 K8s。本章带你从零编写一整套 Dify 的 K8s Manifest（YAML 配置文件）——Deployment、Service、ConfigMap、Secret、Ingress、PVC、HPA——在本地 Kind 集群里部署一套"云原生 Dify"。读完本章，你不再依赖 docker-compose，而是拥有在任何 K8s 集群上部署 Dify 的完整能力。

## 2. 项目设计——剧本式交锋对话

**小胖**："大师！老板说 docker-compose 不够企业级，要迁移到 K8s。我翻了 GitHub 一圈，Dify 没有官方 Helm Chart！难道我要从零开始写 YAML？"

**大师**："对，目前社区还没有成熟的 Helm Chart。但好消息是——写一套 K8s Manifest 比你想象中简单。核心思路就是**一一映射**：docker-compose.yaml 里每个 service 对应一个 Deployment + Service。无状态服务（api/worker/web）用 Deployment，有状态服务（postgres/redis/weaviate）用 StatefulSet + PVC。Nginx 用 K8s Ingress 替代。"

**技术映射**：K8s 部署 = 将 docker-compose 的服务定义翻译为 K8s 资源。无状态→Deployment，有状态→StatefulSet。

**小白**："HPA 自动扩缩容怎么配？我不想半夜被报警电话叫起来手动扩 Pod。"

**大师**："两个关键数字：**触发阈值**和**稳定窗口**。

**触发阈值**：`targetCPU=70%`——当 API Pod 的平均 CPU 超过 70% 时，HPA 自动增加 Pod。如果 CPU 回落到 70% 以下，HPA 自动减少 Pod。

**稳定窗口**：`stabilizationWindowSeconds=300`（5 分钟）——这是缩容的'冷静期'。为什么需要？防止流量抖动导致的频繁扩缩——比如用户突然涌进来 30 秒又走了，如果立刻缩容，新来的用户又要等扩容。等 5 分钟确认流量真的降下来了再缩。"

**技术映射**：HPA = 自动根据指标调整 Pod 数量。缩容稳定窗口防止"抖动"。

**小胖**："滚动更新零停机怎么保证？"

**大师**："三个配置组合保证：`maxUnavailable=0`（更新期间不减少可用 Pod 数——始终至少有一个老 Pod 在服务）+ `maxSurge=1`（允许临时多起一个新 Pod——新老共存的过渡期）+ `readinessProbe`（新 Pod 的 `/health` 返回 200 后，才把流量从老 Pod 切过来）。这三个加起来就是'先起新→后停老'的无缝切换。"

**小白**："生产环境的 PostgreSQL 用 StatefulSet——靠谱吗？万一 Pod 挂了数据会不会丢？"

**大师**："StatefulSet + PVC 能保证 Pod 重启后数据还在（PVC 是独立于 Pod 生命周期的持久存储）。但你还需要三点：
1. **备份**：定期 `pg_dump` 到对象存储（S3/MinIO），PVC 不能代替备份。
2. **主从复制**：用 Patroni 实现 PostgreSQL 的自动故障转移——主库挂了，从库 30 秒内自动晋升。
3. **高可用评估**：如果你的自建 PostgreSQL 挂了，恢复需要多久？如果 RTO（恢复时间目标）不能接受，考虑用云厂商的托管 PostgreSQL（如 AWS RDS）。"

## 3. 项目实战

### 核心 K8s 资源全景

```
Namespace: dify-prod
├── Deployment: dify-api (3 replicas)
│   ├── HPA: dify-api-hpa (min=2, max=10, CPU>70%)
│   └── Service: dify-api (ClusterIP:5001)
├── Deployment: dify-worker (2 replicas)
├── Deployment: dify-web (2 replicas)
│   └── Service: dify-web (ClusterIP:3000)
├── StatefulSet: dify-postgres (1 replica + PVC 50Gi)
│   └── Service: dify-postgres (ClusterIP:5432)
├── StatefulSet: dify-redis (1 replica + PVC 10Gi)
│   └── Service: dify-redis (ClusterIP:6379)
├── ConfigMap: dify-config (共享环境变量)
├── Secret: dify-secrets (密钥和密码)
└── Ingress: dify.example.com → /api/* → api, /* → web
```

### 关键 Manifest

**API Deployment + HPA + Service**：

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: dify-api
  namespace: dify-prod
spec:
  replicas: 3
  strategy:
    type: RollingUpdate
    rollingUpdate:
      maxSurge: 1          # 允许临时多 1 个 Pod
      maxUnavailable: 0    # ★ 零停机核心：更新期间不减少可用 Pod
  selector:
    matchLabels:
      app: dify-api
  template:
    metadata:
      labels:
        app: dify-api
    spec:
      affinity:
        podAntiAffinity:   # Pod 打散到不同 Node/Zone
          preferredDuringSchedulingIgnoredDuringExecution:
          - weight: 100
            podAffinityTerm:
              topologyKey: topology.kubernetes.io/zone  # 跨 AZ 分散
      containers:
      - name: api
        image: langgenius/dify-api:1.14.0
        ports:
        - containerPort: 5001
        envFrom:
        - configMapRef:
            name: dify-config
        - secretRef:
            name: dify-secrets
        resources:
          requests: {memory: "512Mi", cpu: "500m"}
          limits: {memory: "2Gi", cpu: "2000m"}
        livenessProbe:     # 存活探针——失败则重启 Pod
          httpGet:
            path: /health
            port: 5001
          initialDelaySeconds: 30
          periodSeconds: 10
          failureThreshold: 3
        readinessProbe:    # ★ 就绪探针——就绪后才接收流量
          httpGet:
            path: /health
            port: 5001
          initialDelaySeconds: 10
          periodSeconds: 5
          failureThreshold: 2
        lifecycle:         # 优雅关闭
          preStop:
            exec:
              command: ["/bin/sh", "-c", "sleep 10"]  # 等待 10s 让现有请求完成
---
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: dify-api-hpa
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: dify-api
  minReplicas: 2
  maxReplicas: 10
  metrics:
  - type: Resource
    resource:
      name: cpu
      target:
        type: Utilization
        averageUtilization: 70
  behavior:
    scaleDown:
      stabilizationWindowSeconds: 300   # ★ 缩容冷静 5 分钟
      policies:
      - type: Pods
        value: 1                         # 每次最多缩 1 个 Pod
        periodSeconds: 60
    scaleUp:
      stabilizationWindowSeconds: 0      # 扩容不需要冷静期
      policies:
      - type: Pods
        value: 2                         # 每次最多扩 2 个 Pod
        periodSeconds: 30
---
apiVersion: v1
kind: Service
metadata:
  name: dify-api
spec:
  selector:
    app: dify-api
  ports:
  - port: 5001
    targetPort: 5001
```

**ConfigMap + Secret**：

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: dify-config
data:
  MODE: "api"
  DEPLOY_ENV: "PRODUCTION"
  LOG_LEVEL: "INFO"
  CONSOLE_API_URL: "https://dify.example.com"
  APP_API_URL: "https://dify.example.com"
  DB_HOST: "dify-postgres"
  DB_PORT: "5432"
  DB_DATABASE: "dify"
  REDIS_HOST: "dify-redis"
  REDIS_PORT: "6379"
  STORAGE_TYPE: "s3"
  S3_BUCKET_NAME: "dify-prod-storage"
  S3_ENDPOINT: "https://s3.amazonaws.com"
  GUNICORN_WORKERS: "4"
  GUNICORN_WORKER_CONNECTIONS: "1000"
---
apiVersion: v1
kind: Secret
metadata:
  name: dify-secrets
type: Opaque
stringData:
  SECRET_KEY: "your-64-char-random-secret-key"
  DB_USERNAME: "dify"
  DB_PASSWORD: "your-db-password"
  REDIS_PASSWORD: "your-redis-password"
  S3_ACCESS_KEY: "your-access-key"
  S3_SECRET_KEY: "your-secret-key"
```

**Ingress 路由**：

```yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: dify-ingress
  annotations:
    nginx.ingress.kubernetes.io/ssl-redirect: "true"
    nginx.ingress.kubernetes.io/proxy-body-size: "100m"        # 文件上传限制
    nginx.ingress.kubernetes.io/proxy-read-timeout: "3600"     # LLM 长连接
    nginx.ingress.kubernetes.io/proxy-buffering: "off"         # SSE 流式
    cert-manager.io/cluster-issuer: "letsencrypt-prod"         # 自动 HTTPS
spec:
  tls:
  - hosts: [dify.example.com]
    secretName: dify-tls
  rules:
  - host: dify.example.com
    http:
      paths:
      - path: /console/api
        pathType: Prefix
        backend:
          service: {name: dify-api, port: {number: 5001}}
      - path: /api
        pathType: Prefix
        backend:
          service: {name: dify-api, port: {number: 5001}}
      - path: /v1
        pathType: Prefix
        backend:
          service: {name: dify-api, port: {number: 5001}}
      - path: /files
        pathType: Prefix
        backend:
          service: {name: dify-api, port: {number: 5001}}
      - path: /
        pathType: Prefix
        backend:
          service: {name: dify-web, port: {number: 3000}}
```

### 测试验证

```bash
# 1. 本地 Kind 集群部署
kind create cluster --name dify-test
kubectl apply -f k8s/
kubectl get pods -n dify-prod -w  # 等待所有 Pod Running

# 2. 验证滚动更新零停机
# 终端 1：持续请求
while true; do curl -s -o /dev/null -w "%{http_code}" http://localhost/health; sleep 0.5; done
# 终端 2：触发滚动更新
kubectl set image deployment/dify-api api=langgenius/dify-api:1.14.1 -n dify-prod
kubectl rollout status deployment/dify-api -n dify-prod
# 终端 1 应该持续输出 200，没有 502

# 3. 验证 HPA 扩容
kubectl run -it --rm loadgen --image=busybox -n dify-prod -- \
  sh -c "while true; do wget -q -O- http://dify-api:5001/health; done"
kubectl get hpa dify-api-hpa -n dify-prod -w
# 观察 REPLICAS 从 3 涨到更多
```

## 4. 项目总结

| K8s 资源 | Dify 组件 | 关键注意事项 |
|----------|----------|------------|
| Deployment | api, worker, web | 无状态，随意扩缩。配 RollingUpdate + readinessProbe |
| StatefulSet | postgres, redis | 有状态，需 PVC 持久化。生产环境推荐云托管 DB |
| Ingress | 替代 Nginx | SSL 终止 + URL 路由。SSE 必须配 `proxy-buffering: off` |
| HPA | api | CPU/内存触发。缩容配 5min 稳定窗口防抖动 |
| ConfigMap/Secret | 所有配置 | Secret 不要提交 Git。用 SealedSecret 或 ExternalSecrets |

**思考题**：
1. PostgreSQL 在 K8s 中用 StatefulSet 部署有什么风险？生产环境推荐什么方案？（提示：主从切换、备份恢复的复杂度。云托管 RDS 是最省心的）
2. 如何实现蓝绿发布——新版本 API 完全就绪后再一次性切流量？

> **参考答案**：见附录 D
