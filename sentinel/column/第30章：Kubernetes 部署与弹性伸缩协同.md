# 第30章：Kubernetes 部署与弹性伸缩协同

## 1 项目背景

前面 29 章我们都在"传统部署"或"Docker Compose"环境下使用 Sentinel。但随着团队全面迁入 Kubernetes，新的挑战出现了：

- Pod 重启后 IP 会变，Sentinel Dashboard 中会出现"幽灵应用"（旧 IP 的离线机器一直显示）
- HPA 根据 CPU 自动扩缩容，但 Sentinel 的单机限流阈值是写死的——Pod 从 3 个变到 10 个，每个 Pod 的 QPS 阈值还是 300，全局 QPS 上限从 900 变成了 3000，这可能超出后端的承受能力
- Pod 扩容时新 Pod 的 Sentinel 规则如何加载？是从 Nacos 动态加载还是依赖 ConfigMap？
- 滚动发布时新旧版本共存，Sentinel 规则如何兼容？

本章将解决 Sentinel 在 Kubernetes 环境中的部署、配置和与弹性伸缩的协同问题。

## 2 项目设计

**小白**："K8s 中 Pod IP 是动态的，Dashboard 怎么跟踪？"

**大师**："Sentinel Dashboard 通过心跳来识别客户端。旧 Pod 下线后心跳停止，Dashboard 会在 30 秒后标记为离线。但那些'幽灵应用'确实会堆积——建议定期清理 Dashboard 中超过 1 小时未心跳的机器。"

**小胖**："HPA 自动扩容时，每个新 Pod 都会加载单机限流规则——QPS=300 × 10 个 Pod = 3000 QPS 全局上限。但后端数据库只能扛 1500 QPS，这怎么办？"

**大师**："两个方案：一是用集群流控（第 22 章），Token Server 维护全局 1500 的阈值；二是用 KEDA 替代 HPA，KEDA 可以基于 Prometheus 指标（包括 Sentinel 的拒绝 QPS）来做更精细的扩缩容。"

**小白**："大师，Pod 重启后 Sentinel Dashboard 中出现重复的机器记录怎么办？我们之前有 3 个 Pod 每天滚动重启，Dashboard 里积压了上百个离线机器。"

**大师**："这个问题需要从两方面解决。一是 Sentinel 客户端配置 `spring.cloud.sentinel.transport.client-ip` 为 Pod 的固定标识——比如用 StatefulSet 的 Pod 名称，或用 Downward API 注入 Pod UID。二是 Dashboard 可以开启自动清理，把超过 30 分钟无心跳的机器标记为离线并移除。"

**小胖**："那 ConfigMap 方式加载 Sentinel 规则有什么坑吗？Nacos 是额外部署的，我们在想能不能用 K8s 原生的 ConfigMap 来管理规则。"

**大师**："ConfigMap 有两个致命缺陷：第一，挂载更新有延迟，kubelet 默认 60 秒同步一次，紧急规则变更等不了；第二，ConfigMap 有 1MB 大小限制，规则多了不够存。所以生产环境我只推荐 Nacos 作为规则源。如果你确实不想用 Nacos，可以用 Sentinel 的 Apollo DataSource 或自建 Redis DataSource。"

**小白**："HPA 和 Sentinel 系统保护同时触发会有什么后果？"

**大师**："这会产生'双重限流'——HPA 在扩容，Sentinel 在限流，两者互不感知导致系统抖动。正确做法是设置优先级：Sentinel 系统保护的 CPU 阈值（80%）> HPA 的扩容 CPU 阈值（60%）。这样 HPA 先扩容，扩容不够了 Sentinel 才兜底限流。就像消防系统——先开喷淋（HPA 扩容），实在不行才疏散人群（Sentinel 限流）。"

**小胖**："Token Server 用 Deployment 还是 StatefulSet？扩容时 IP 会变，Consumer 怎么找到它？"

**大师**："Token Server 必须用 StatefulSet + Headless Service。因为 Consumer 需要通过稳定的网络标识连接到固定的 Token Server Pod，不能每次扩容都重建连接。StatefulSet 会确保 `sentinel-token-server-0`、`sentinel-token-server-1` 这些标识在 Pod 重启后保持不变。同时设置 `podManagementPolicy: Parallel` 加速启动。"

**小白**："如果 K8s 做了多集群部署（比如北京、上海各一个 K8s），Sentinel 怎么跨集群协同？用同一套规则吗？"

**大师**："多集群下规则策略取决于业务形态。如果是'单元化'架构（每集群独立），每个集群独立维护 Nacos/规则，互不影响。如果是'多活'架构（流量跨集群），需要全局 Token Server 协同——但跨集群的实时通信延迟是这个方案的阻碍。务实的做法是：每个集群独立集群流控，通过统一的 Nacos 集群管理规则，规则由中心运维统一发布到各集群。"

**小胖**："我们想用 Istio/Service Mesh 替换部分 Sentinel 功能，Sentinel 在 Mesh 场景下还有价值吗？"

**大师**："有——两者互补而非替代。Istio 的限流在 Envoy Sidecar 层，优势是无侵入、多语言支持；但缺点是不能做热点参数限流、不能做调用链路的精细控制、不能做业务级熔断。Sentinel 在应用层能感知业务逻辑——比如'某个 SKU 的热点限流'是 Istio 做不到的。正确的分层是：Istio 做四层通用限流，Sentinel 做七层业务限流。"

**小白**："Pod Disruption Budget (PDB) 和 Sentinel 怎么配合？比如缩容时，如果正在处理请求的 Pod 被突然杀掉，Consumer 侧会收到错误。"

**大师**："PDB 控制的是'最小可用数'，但保证不了'优雅下线'。正确的配合是：PDB 设置 `minAvailable: 1` 保证至少 1 个 Pod 在线 + `preStop` hook 中先通知 Sentinel 下线（标记该 Pod 不再接收新流量） + `sleep 15` 排空在途请求 + 真正终止。Sentinel 本身不支持'优雅下线通知'，需要自定义实现——在 `preStop` 中发送 HTTP 请求到 Consumer 的 Sentinel Dashboard，标记该节点为'下线中'。"

**小胖**："滚动更新时，旧 Pod 和新 Pod 的 Sentinel 规则可能不同（新版本改了资源名），这会导致新旧 Pod 之间规则不匹配——Consumer 配的熔断规则是用旧资源名，但新 Pod 接受的是新资源名，熔断完全失效。"

**大师**："这就是'规则兼容性'问题。解决方式：1）资源名变更属于 breaking change，必须保证新旧版本一致；2）如果必须变更，用 `maxSurge=1, maxUnavailable=0` 策略保证任何时候没有版本混合；3）在 Nacos 中同时维护新旧两套规则，待流量全部切到新版本后再删除旧规则。说白了，资源名是 Sentinel 规则的 key —— key 变了，所有依赖它的规则都要同步变更。"

**小胖**："最后问一个运维问题：K8s Pod 被杀后，Sentinel Dashboard 里那台机器什么时候消失？我们在做故障演练时，发现 Dashboard 里有一堆死 IP。"

**大师**："Dashboard 默认 30 秒无心跳标记为离线，但不会自动删除。你需要配置 Dashboard 的 `sentinel.dashboard.remove.inactive.machine.enabled=true`，设置 `sentinel.dashboard.remove.inactive.machine.interval=60000`（1 分钟清理一次）。另外，客户端可以配置 `spring.cloud.sentinel.transport.client-ip` 使用 Pod Name（通过 Downward API），而不是 Pod IP——这样即使 IP 变了，Dashboard 也能识别是同一个逻辑节点。"

## 3 项目实战

### 3.1 K8s 部署清单

```yaml
# sentinel-deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: order-service
spec:
  replicas: 3
  selector:
    matchLabels:
      app: order-service
  template:
    metadata:
      labels:
        app: order-service
    spec:
      containers:
      - name: order-service
        image: order-service:latest
        ports:
        - containerPort: 8090
        - containerPort: 8719    # Sentinel 通信端口
        env:
        - name: NACOS_ADDR
          value: "nacos-service.default:8848"
        - name: SENTINEL_DASHBOARD
          value: "sentinel-dashboard.default:8080"
        resources:
          requests:
            cpu: "500m"
            memory: "512Mi"
          limits:
            cpu: "2000m"
            memory: "1Gi"
---
apiVersion: v1
kind: Service
metadata:
  name: order-service
spec:
  selector:
    app: order-service
  ports:
  - port: 8090
  - port: 8719
    name: sentinel
```

### 3.2 HPA 配置

```yaml
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: order-service-hpa
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: order-service
  minReplicas: 2
  maxReplicas: 10
  metrics:
  - type: Resource
    resource:
      name: cpu
      target:
        type: Utilization
        averageUtilization: 60
```

### 3.3 Sentinel 与 HPA/KEDA 配合策略

| 场景 | Sentinel 策略 | HPA/KEDA 策略 |
|------|-------------|--------------|
| 日常流量 | 单机或集群流控 | HPA CPU 60% |
| 突发流量 | 集群流控（全局阈值不变） | HPA 快速扩容 + Sentinel 限流兜底 |
| 弹性伸缩中 | 新 Pod 自动从 Nacos 加载规则 | 关联 KEDA ScaledObject |
| 已过载 | 系统保护触发（CPU 80%） | HPA 扩容中（系统保护在 HPA 前兜底） |

### 3.4 KEDA 配置（按 Sentinel 拒绝数扩缩容）

```yaml
apiVersion: keda.sh/v1alpha1
kind: ScaledObject
metadata:
  name: order-service-scaler
spec:
  scaleTargetRef:
    name: order-service
  minReplicaCount: 2
  maxReplicaCount: 10
  triggers:
  - type: prometheus
    metadata:
      serverAddress: http://prometheus.default:9090
      metricName: sentinel_block_rate
      query: |
        sum(rate(sentinel_resource_block_qps{service="order-service"}[2m]))
      threshold: "10"    # 拒绝 QPS > 10 时触发扩容
```

### 3.5 滚动发布规则兼容

```yaml
# 发布策略：确保新旧版本 Sentinel 规则兼容
strategy:
  rollingUpdate:
    maxSurge: 1
    maxUnavailable: 0
```

发布检查清单：
- [ ] 新版本使用的 Sentinel 资源名是否与旧版本一致？
- [ ] 新版本的 Nacos dataId 是否有变化？
- [ ] 新版本的 Feign/Dubbo 接口签名是否有变更（会影响资源名）？

### 3.6 Pod 启动时 Sentinel 规则预热

Pod 启动后需要立即加载 Sentinel 规则，避免冷启动窗口期的流量穿透：

```java
@Component
public class SentinelBootstrap implements ApplicationRunner {

    @Value("${spring.application.name}")
    private String appName;

    @Override
    public void run(ApplicationArguments args) {
        // 确保 Nacos DataSource 在服务接收流量前完成初始化
        ReadableDataSource<String, List<FlowRule>> flowDs =
            new NacosDataSource<>(
                "nacos-service.default:8848",
                "SENTINEL_FLOW",
                appName + "-flow-rules",
                source -> JSON.parseObject(source,
                    new TypeReference<List<FlowRule>>() {})
            );
        FlowRuleManager.register2Property(flowDs.getProperty());

        log.info("Sentinel rules loaded for app: {}", appName);
    }
}
```

```yaml
# 配置 readinessProbe，确保规则加载完成后再接收流量
spec:
  containers:
  - name: order-service
    readinessProbe:
      httpGet:
        path: /actuator/health
        port: 8090
      initialDelaySeconds: 10    # 等待 Sentinel 初始化
      periodSeconds: 5
    lifecycle:
      preStop:
        exec:
          command: ["/bin/sh", "-c", "sleep 15"]  # 优雅下线，排空队列
```

### 3.7 K8s 中 Sentinel 的监控指标暴露

```java
@Configuration
public class SentinelMetricExporter {

    @Bean
    public MeterRegistryCustomizer<MeterRegistry> sentinelMetrics() {
        return registry -> {
            // 将 Sentinel 指标注册到 Micrometer（Prometheus 可采集）
            registry.gauge("sentinel.flow.rules.count",
                FlowRuleManager.getRules(), List::size);
            registry.gauge("sentinel.degrade.rules.count",
                DegradeRuleManager.getRules(), List::size);
        };
    }
}
```

```yaml
# ServiceMonitor — 让 Prometheus Operator 自动发现 Sentinel 指标
apiVersion: monitoring.coreos.com/v1
kind: ServiceMonitor
metadata:
  name: order-service-monitor
spec:
  selector:
    matchLabels:
      app: order-service
  endpoints:
  - port: http
    path: /actuator/prometheus
    interval: 15s
```

### 3.8 多环境下的 Sentinel 规则隔离

```java
// 根据 K8s namespace 自动选择规则 group
@Component
public class EnvAwareDataSource {

    @Value("${k8s.namespace:default}")
    private String namespace;

    @PostConstruct
    public void init() {
        // dev → SENTINEL_FLOW_DEV, prod → SENTINEL_FLOW_PROD
        String groupId = "SENTINEL_FLOW_" + namespace.toUpperCase();

        ReadableDataSource<String, List<FlowRule>> ds =
            new NacosDataSource<>(nacosAddr, groupId, dataId, parser);
        FlowRuleManager.register2Property(ds.getProperty());

        log.info("Sentinel using Nacos group: {}", groupId);
    }
}
```

**步骤九：Sentinel Helm Chart 模板**

```yaml
# values.yaml
replicaCount: 3
image:
  repository: order-service
  tag: latest
  pullPolicy: IfNotPresent

sentinel:
  dashboard:
    enabled: true
    host: sentinel-dashboard.default.svc.cluster.local
    port: 8080
  rules:
    source: nacos  # nacos | apollo | configmap
  transport:
    port: 8719
    clientIp: ${POD_NAME}  # 使用 Downward API 注入 Pod 名称

nacos:
  host: nacos-service.default.svc.cluster.local
  port: 8848

hpa:
  enabled: true
  minReplicas: 2
  maxReplicas: 10
  targetCPUUtilization: 60

keda:
  enabled: true
  prometheusServer: http://prometheus.default:9090
  blockRateThreshold: 10

service:
  type: ClusterIP
  port: 8090
  sentinelPort: 8719

resources:
  requests:
    cpu: 500m
    memory: 512Mi
  limits:
    cpu: 2000m
    memory: 1Gi
```

`templates/deployment.yaml` 关键部分：

```yaml
spec:
  containers:
  - name: {{ .Chart.Name }}
    env:
    - name: POD_NAME
      valueFrom:
        fieldRef:
          fieldPath: metadata.name  # Downward API：注入 Pod 名称
    - name: NACOS_ADDR
      value: "{{ .Values.nacos.host }}:{{ .Values.nacos.port }}"
    - name: SENTINEL_CLIENT_IP
      value: "$(POD_NAME)"   # 使用 Pod Name 而非 IP 作为客户端标识
    lifecycle:
      preStop:
        exec:
          command:
          - /bin/sh
          - -c
          - |
            # 1. 通知 Sentinel Dashboard 本节点即将下线
            curl -X POST http://{{ .Values.sentinel.dashboard.host }}:{{ .Values.sentinel.dashboard.port }}/registry/machine \
              -H "Content-Type: application/json" \
              -d '{"app":"{{ .Chart.Name }}","ip":"$(POD_NAME)","port":8719,"offline":true}'
            # 2. 等待 15 秒排空在途请求
            sleep 15
    readinessProbe:
      httpGet:
        path: /actuator/health
        port: {{ .Values.service.port }}
      initialDelaySeconds: 15  # 等待规则加载
      periodSeconds: 5
      failureThreshold: 3
    livenessProbe:
      httpGet:
        path: /actuator/health/liveness
        port: {{ .Values.service.port }}
      initialDelaySeconds: 30
      periodSeconds: 10
```

**步骤十：Pod Disruption Budget 配合 Sentinel 优雅下线**

```yaml
# pdb.yaml
apiVersion: policy/v1
kind: PodDisruptionBudget
metadata:
  name: order-service-pdb
spec:
  minAvailable: 1       # 至少保证 1 个 Pod 可用
  selector:
    matchLabels:
      app: order-service
---
# 配合：preStop 中先注销 Sentinel 再退出
# 这样 PDB + preStop 共同保证：
# - 缩容时不会把最后一个 Pod 杀掉
# - 被杀掉的 Pod 会先完成 Sentinel 注销 + 排空队列
```

**步骤十一：Istio + Sentinel 混合部署（四层 vs 七层限流分层）**

```yaml
# Istio DestinationRule — 四层限流（连接数、请求数上限）
apiVersion: networking.istio.io/v1beta1
kind: DestinationRule
metadata:
  name: order-service
spec:
  host: order-service.default.svc.cluster.local
  trafficPolicy:
    connectionPool:
      tcp:
        maxConnections: 1000      # Istio 做连接级保护
      http:
        http1MaxPendingRequests: 100
        http2MaxRequests: 500
        maxRequestsPerConnection: 10
---
# Sentinel — 七层业务限流（在应用内部生效）
# 不需要修改 K8s 配置，Sentinel 在 Java 进程内运行
# 资源: createOrder → QPS 500 (业务精控)
# 资源: querySensitiveSKU → 热点参数限流 (Istio 做不到)
```

**分层策略**：
```
┌─────────────────────────────────┐
│  Istio Envoy Sidecar            │  ← 四层：连接数、总体 QPS 上限
│  (限流/熔断/负载均衡)              │
├─────────────────────────────────┤
│  Sentinel (应用内)               │  ← 七层：业务精细限流、热点参数
│  (精细限流/热点参数/业务熔断)       │
└─────────────────────────────────┘
```

**步骤十二：Token Server StatefulSet 部署**

```yaml
# sentinel-token-server-statefulset.yaml
apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: sentinel-token-server
spec:
  serviceName: sentinel-token-server
  podManagementPolicy: Parallel  # 并行启动，不用等前一个就绪
  replicas: 3
  selector:
    matchLabels:
      app: sentinel-token-server
  template:
    metadata:
      labels:
        app: sentinel-token-server
    spec:
      affinity:
        podAntiAffinity:           # 反亲和性：每个节点最多一个
          requiredDuringSchedulingIgnoredDuringExecution:
          - labelSelector:
              matchLabels:
                app: sentinel-token-server
            topologyKey: kubernetes.io/hostname
      containers:
      - name: token-server
        image: order-service:latest
        env:
        - name: SENTINEL_TOKEN_SERVER_ROLE
          value: "true"
        - name: CLUSTER_SERVER_PORT
          value: "18730"
        ports:
        - containerPort: 18730
          name: token-server
---
apiVersion: v1
kind: Service
metadata:
  name: sentinel-token-server
spec:
  clusterIP: None  # Headless Service
  selector:
    app: sentinel-token-server
  ports:
  - port: 18730
    name: token-server
```

**步骤十三：滚动更新 Sentinel 规则兼容保障**

```java
// 版本变更检测 —— 在 Spring Boot 启动时检查资源名兼容性
@Component
public class SentinelResourceCompatChecker implements ApplicationRunner {

    @Override
    public void run(ApplicationArguments args) {
        // 1. 扫描当前版本的所有 @SentinelResource 注解
        Set<String> currentResources = scanSentinelResources();
        
        // 2. 从 Nacos 读取上一版本的资源名列表（存储在 annotation metadata 中）
        Set<String> previousResources = loadPreviousResources();
        
        // 3. 差异检测
        Set<String> removed = new HashSet<>(previousResources);
        removed.removeAll(currentResources);
        Set<String> added = new HashSet<>(currentResources);
        added.removeAll(previousResources);
        
        if (!removed.isEmpty()) {
            log.warn("⚠️ 资源名已删除（旧规则可能失效）: {}", removed);
        }
        if (!added.isEmpty()) {
            log.info("🆕 新增资源名（需在 Nacos 中配置规则）: {}", added);
        }
        
        // 4. 如果有破坏性变更，可选择性阻止启动或降级
        if (!removed.isEmpty() && isProduction()) {
            log.error("生产环境资源名有破坏性变更，终止启动!");
            // ... 发送告警，考虑终止
        }
    }
}
```

**踩坑记录**：

1. **Pod 重启后 Dashboard 乱象**：设置 `project.name` 和 `spring.cloud.sentinel.transport.client-ip` 使用稳定的标识（如 StatefulSet 的 Pod Name）。
2. **HPA 和 Sentinel 系统保护竞争**：系统保护 CPU 阈值应高于 HPA 扩容 CPU 阈值（系统保护 80%、HPA 60%），避免两者同时触发。
3. **ConfigMap 规则更新不实时**：K8s ConfigMap 挂载更新有延迟（默认约 1 分钟），对 Sentinel 规则实时性不够。推荐始终用 Nacos 作为规则源。
4. **Sentinel 通信端口不应对外暴露**：8719 端口仅用于 Dashboard ↔ 客户端通信，不应暴露到 Service 外部。Service 中该端口应标记 `clusterIP: None` 或不暴露。
5. **Pod Name 作为 client-ip 的长度限制**：StatefulSet Pod 名称可能很长（如 `order-service-statefulset-123`），Sentinel 对 client-ip 字段有长度限制（默认 64 字符）。

## 4 项目总结

### 4.1 K8s 部署 Checklist

- [ ] Sentinel 通信端口 8719 在 Service 中暴露
- [ ] Nacos 地址使用 K8s Service Name
- [ ] `eager=true` 确保 Sentinel 在首次请求前初始化
- [ ] HPA CPU 阈值 (60%) < Sentinel 系统保护 CPU 阈值 (80%)
- [ ] 集群流控 Token Server 使用 StatefulSet + Headless Service
- [ ] Pod Name 通过 Downward API 注入为 `client-ip`（避免幽灵应用）
- [ ] readinessProbe `initialDelaySeconds` 设置为 10-15s（确保规则加载完成）
- [ ] preStop hook 中注销 Sentinel + `sleep 15` 排空在途请求
- [ ] PDB 设置 `minAvailable: 1`（防止全部 Pod 同时被杀）
- [ ] ServiceMonitor 配置让 Prometheus Operator 自动发现 Sentinel 指标
- [ ] 滚动更新策略 `maxSurge=1, maxUnavailable=0`（保证版本兼容）
- [ ] Token Server Pod 使用反亲和性部署在不同 Node 上

### 4.2 Sentinel + K8s 协同指标矩阵

| 指标 | 阈值建议 | 触发对象 | 动作 | 恢复条件 |
|------|---------|---------|------|---------|
| CPU 使用率 60% | HPA targetCPU | HPA Controller | 扩容 Pod | CPU < 50% 持续 5min 缩容 |
| CPU 使用率 80% | Sentinel 系统保护 | Sentinel SystemSlot | 限流兜底 | CPU < 70% 自动恢复 |
| Sentinel 拒绝 QPS > 10 | KEDA trigger | KEDA ScaledObject | 扩容 Pod | 拒绝 QPS < 3 |
| Pod 内存 > 80% | HPA memory | HPA Controller | 扩容 Pod | 内存 < 60% |
| 熔断打开 | DegradeSlot | Sentinel | 返回降级结果 | timeWindow 过期后半开 |
| Pod 下线（PreStop） | 优雅停机 | K8s lifecycle | 排空队列 + 注销 | N/A |

### 4.3 K8s 环境特定踩坑矩阵

| 问题 | 现象 | 解决方案 | 优先级 |
|------|------|---------|-------|
| 幽灵应用 | Dashboard 出现大量离线机器 | Downward API 注入 Pod Name 作为 client-ip | P0 |
| Token Server 销毁 | 缩容时销毁了 Token Server Pod | StatefulSet + 反亲和性确保 Token Server 不随业务 Pod 缩容 | P0 |
| 规则加载竞态 | Pod 接收流量时规则尚未加载 | readinessProbe `initialDelaySeconds` 15s | P0 |
| HPA/Sentinel 竞争 | 系统保护与 HPA 同时触发 | 系统保护 CPU 阈值 (80%) > HPA CPU 阈值 (60%) | P0 |
| ConfigMap 更新延迟 | 修改 ConfigMap 后 1 分钟才生效 | 只用 Nacos 做规则源，不用 ConfigMap | P1 |
| 版本混合规则失效 | 新旧版本资源名不一致，熔断失效 | `maxUnavailable=0` + 资源名兼容检查 | P1 |
| Sentinel 端口暴露 | 8719 端口通过 Service 对外暴露 | Service 中仅暴露 8090（业务端口） | P1 |
| 跨集群规则不一致 | 多集群部署规则版本差异 | 统一 Nacos 集群管理，GitOps 同步 | P2 |
| Istio Sidecar 双重限流 | Istio 限流 + Sentinel 限流同时触发 | 分层策略：Istio 做四层，Sentinel 做七层 | P2 |

### 4.4 K8s vs 传统部署对比

| 维度 | 传统部署 (VM/Docker Compose) | Kubernetes 部署 |
|------|---------------------------|----------------|
| 实例标识 | IP 固定 | IP 动态 → 需用 Pod Name |
| 规则加载 | 启动时从本地/Nacos 加载 | Nacos + readinessProbe 保障 |
| 弹性伸缩 | 无 → Sentinel 单机阈值固定 | HPA/KEDA → 需联动集群流控 |
| 优雅下线 | kill -15 → 依赖 JVM ShutdownHook | preStop hook → 可控排空 |
| 监控发现 | 手动配置 Prometheus target | ServiceMonitor 自动发现 |
| Token Server | 固定 IP | StatefulSet + Headless Service |
| 故障自愈 | 无 | Pod 自动重启 → 但 Sentinel 规则需重载 |
| 多环境 | 手动切换 | namespace + envFrom 自动注入 |

### 4.5 Sentinel + K8s 生产部署拓扑

```
┌─────────────────────────────────────────────┐
│                Kubernetes Cluster            │
│                                             │
│  ┌─────────────┐  ┌──────────────┐          │
│  │ Nacos 集群   │  │ Sentinel     │          │
│  │ (StatefulSet)│  │ Dashboard    │          │
│  │ 3 replicas  │  │ (Deployment) │          │
│  └──────┬──────┘  └──────┬───────┘          │
│         │                │                   │
│  ┌──────┴────────────────┴───────┐          │
│  │        Service Mesh           │          │
│  │  ┌────────┐  ┌────────┐      │          │
│  │  │Pod-0   │  │Pod-1   │ ...  │          │
│  │  │Sentinel│  │Sentinel│      │          │
│  │  │+ Istio │  │+ Istio │      │          │
│  │  └────────┘  └────────┘      │          │
│  │  HPA/KEDA 自动扩缩容           │          │
│  └──────────────────────────────┘          │
│                                             │
│  ┌──────────────────────────────┐          │
│  │   Prometheus + Grafana       │          │
│  │   (ServiceMonitor 自动发现)    │          │
│  └──────────────────────────────┘          │
└─────────────────────────────────────────────┘
```

### 4.6 思考题

1. 如果 K8s 集群中有 3 个 deployment 共 30 个 Pod 使用集群流控，Token Server 应该如何部署？缩容时如何保证 Token Server 的可用性？
2. Sentinel 与 Service Mesh（Istio）的限流功能有何差异？在什么场景下应该同时使用两者？

### 4.7 推广计划

- **运维/SRE**：维护 Sentinel 在 K8s 中的 Helm Chart 模板。
- **开发团队**：确保服务在新 Pod 启动后 5 秒内完成 Sentinel 规则加载。
- **测试团队**：在 K8s 环境中测试 HPA 扩缩容时 Sentinel 规则的行为。
