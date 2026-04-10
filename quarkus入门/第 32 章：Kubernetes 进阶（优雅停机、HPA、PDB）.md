# 第 32 章：Kubernetes 进阶（优雅停机、HPA、PDB）

## 0. 课程卡片

| 项目 | 说明 |
|------|------|
| **建议课时** | 75 分钟 |
| **学习目标** | 配置 `quarkus.shutdown.timeout`；完整滚动发布 YAML；HPA + PDB |
| **先修** | 第 7、10 章 |

---

## 1. 项目背景

滚动发布与缩容发送 **SIGTERM**。应用须在 **terminationGracePeriodSeconds** 内排空请求。**HPA** 扩缩与 **PDB** 可用性约束需联合设计。

---

## 2. 项目设计：大师与小白的对话

**运维**：「发布偶发 502。」

**大师**：「**readiness 先失败**、网关摘除、再处理 in-flight；超时与网关对齐。」

**小白**：「收到 SIGTERM 立刻 `System.exit`。」

**大师**：「应先拒绝新连接、完成手头请求、再退出。」

**架构师**：「HPA 只看 CPU 够吗？」

**大师**：「业务应用可配 **自定义指标** 或结合 **KEDA**；CPU 只是起点。」

---

## 3. 知识要点

- `quarkus.shutdown.timeout`  
- `preStop` hook  
- `PodDisruptionBudget`

---

## 4. 项目实战

### 4.1 `application.properties`

```properties
quarkus.shutdown.timeout=30s
```

### 4.2 完整 `k8s/rollout-advanced.yaml`

```yaml
apiVersion: policy/v1
kind: PodDisruptionBudget
metadata:
  name: api-pdb
spec:
  minAvailable: 1
  selector:
    matchLabels:
      app: api
---
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: api-hpa
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: api
  minReplicas: 2
  maxReplicas: 10
  metrics:
    - type: Resource
      resource:
        name: cpu
        target:
          type: Utilization
          averageUtilization: 70
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: api
spec:
  replicas: 2
  strategy:
    type: RollingUpdate
    rollingUpdate:
      maxUnavailable: 0
      maxSurge: 1
  selector:
    matchLabels:
      app: api
  template:
    metadata:
      labels:
        app: api
    spec:
      terminationGracePeriodSeconds: 60
      containers:
        - name: app
          image: registry.example.com/acme/api:1.0.0
          ports:
            - name: http
              containerPort: 8080
          lifecycle:
            preStop:
              exec:
                command: ["sh", "-c", "sleep 5"]
          readinessProbe:
            httpGet:
              path: /q/health/ready
              port: http
            periodSeconds: 5
          livenessProbe:
            httpGet:
              path: /q/health/live
              port: http
            initialDelaySeconds: 30
            periodSeconds: 20
```

### 4.3 `pom.xml`

应用需 `quarkus-smallrye-health`；无特殊插件。

---

## 5. 课堂实验

| 步骤 | 操作 | 预期结果 |
|------|------|----------|
| 1 | `kubectl apply -f k8s/rollout-advanced.yaml` | 对象创建 |
| 2 | `kubectl rollout restart deployment/api` 同时 `watch kubectl get pods` | 平滑轮转 |
| 3 | 压测过程中滚动发布 | 错误率可接受（预设阈值） |
| 4 | `kubectl drain` 单节点（实验集群） | PDB 阻止违反 minAvailable |
| 5 | 将 `shutdown.timeout` 调极大，观察 `SIGKILL` | 理解 grace 上限 |

---

## 6. 项目总结

| 维度 | 说明 |
|------|------|
| **优点** | 发布平滑；与 Quarkus 配置协同。 |
| **缺点** | 参数需跨团队联调。 |
| **适用场景** | 生产。 |
| **注意事项** | 长连接/WebSocket 单独策略。 |
| **常见踩坑** | grace 过短；未摘流就杀进程。 |

**延伸阅读**：<https://kubernetes.io/docs/concepts/workloads/pods/pod-lifecycle/#pod-termination>
