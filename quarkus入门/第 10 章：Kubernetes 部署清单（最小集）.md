# 第 10 章：Kubernetes 部署清单（最小集）

## 0. 课程卡片

| 项目 | 说明 |
|------|------|
| **建议课时** | 60～75 分钟 |
| **学习目标** | 手写完整 **Deployment + Service + Ingress + ConfigMap + Secret**；与 Quarkus 端口/探针对齐 |
| **先修** | 第 3、7、9 章 |

---

## 1. 项目背景

「能构建镜像」≠「能稳定跑在集群」。需要：**资源限制、探针、配置注入、对外路由**。本章给出**一份可复制的一栈 YAML**（多文档 `---` 分隔），便于 GitOps 与课堂 `kubectl apply -f`。

---

## 2. 项目设计：大师与小白的对话

**小白**：「Deployment 和 Pod YAML 啥关系？」

**大师**：「平时写 **Deployment**，它生成 ReplicaSet 再生成 Pod；不要手写单个 Pod 做长期工作负载。」

**运维**：「Ingress 控制器我们集群只有一种吗？」

**大师**：「要确认是 nginx、traefik 还是云 LB；**class 与 annotation** 按平台文档。」

**测试**：「我如何在预发验证新镜像？」

**大师**：「改 Deployment `image` 字段 + **滚动更新**；配合 readiness 保证无断流。」

**架构师**：「Secret 明文写进 Git 吗？」

**大师**：「**绝不**。课堂用 `stringData` 演示，真系统用 SealedSecrets 或外部 KMS。」

---

## 3. 知识要点

- `resources.requests/limits` 与 JVM 堆关系（后续性能章深化）  
- `strategy.rollingUpdate`  
- Ingress `path` 与 Quarkus `context-path`（若配置）一致

---

## 4. 项目实战：完整 `k8s/full-stack.yaml`

> **替换项**：`registry.example.com/acme/quarkus-lab:1.0.0`、Ingress host、`IngressClassName`。

```yaml
apiVersion: v1
kind: Namespace
metadata:
  name: quarkus-lab
---
apiVersion: v1
kind: Secret
metadata:
  name: quarkus-lab-secret
  namespace: quarkus-lab
type: Opaque
stringData:
  DB_PASSWORD: "change-me-in-real-life"
---
apiVersion: v1
kind: ConfigMap
metadata:
  name: quarkus-lab-config
  namespace: quarkus-lab
data:
  QUARKUS_PROFILE: "prod"
  MY_FEATURE_FLAG: "true"
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: quarkus-lab
  namespace: quarkus-lab
spec:
  replicas: 2
  strategy:
    type: RollingUpdate
    rollingUpdate:
      maxUnavailable: 0
      maxSurge: 1
  selector:
    matchLabels:
      app: quarkus-lab
  template:
    metadata:
      labels:
        app: quarkus-lab
    spec:
      containers:
        - name: app
          image: registry.example.com/acme/quarkus-lab:1.0.0
          imagePullPolicy: IfNotPresent
          ports:
            - name: http
              containerPort: 8080
          envFrom:
            - configMapRef:
                name: quarkus-lab-config
            - secretRef:
                name: quarkus-lab-secret
          env:
            - name: JAVA_TOOL_OPTIONS
              value: "-XX:MaxRAMPercentage=70"
          startupProbe:
            httpGet:
              path: /q/health/ready
              port: http
            periodSeconds: 5
            failureThreshold: 30
          readinessProbe:
            httpGet:
              path: /q/health/ready
              port: http
            initialDelaySeconds: 5
            periodSeconds: 10
          livenessProbe:
            httpGet:
              path: /q/health/live
              port: http
            initialDelaySeconds: 30
            periodSeconds: 20
          resources:
            requests:
              cpu: "200m"
              memory: "384Mi"
            limits:
              cpu: "1"
              memory: "512Mi"
---
apiVersion: v1
kind: Service
metadata:
  name: quarkus-lab
  namespace: quarkus-lab
spec:
  selector:
    app: quarkus-lab
  ports:
    - name: http
      port: 80
      targetPort: http
---
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: quarkus-lab
  namespace: quarkus-lab
  annotations:
    # 按集群 Ingress 控制器调整，例如 nginx：
    # nginx.ingress.kubernetes.io/rewrite-target: /
spec:
  ingressClassName: nginx
  rules:
    - host: quarkus-lab.example.com
      http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                name: quarkus-lab
                port:
                  number: 80
```

### 4.1 最小 `pom.xml` 提醒（应用侧）

应用需包含 `quarkus-smallrye-health` 与业务 `quarkus-rest`，否则探针路径无效。

---

## 5. 课堂实验

| 步骤 | 操作 | 预期结果 |
|------|------|----------|
| 1 | `kubectl apply -f k8s/full-stack.yaml` | 所有对象 Created |
| 2 | `kubectl -n quarkus-lab get pods,svc,ing` | 2 Pod Running，Service 有 ClusterIP |
| 3 | `kubectl -n quarkus-lab describe pod -l app=quarkus-lab` | Events 无探针失败 |
| 4 | `kubectl -n quarkus-lab port-forward svc/quarkus-lab 8080:80` | 本地 curl 通 |
| 5 | （可选）配置 hosts + 访问 Ingress host | 验证七层路由 |
| 6 | **故障注入**：故意改错镜像 tag | Pod ImagePullBackOff，学会 `describe` |

**清理**：

```bash
kubectl delete namespace quarkus-lab --ignore-not-found
```

---

## 6. 项目总结

| 维度 | 说明 |
|------|------|
| **优点** | 清单化、可 GitOps、与 Quarkus 端点契合。 |
| **缺点** | Ingress 与云厂商差异大。 |
| **适用场景** | 标准微服务上线。 |
| **注意事项** | Secret 管理；PDB/HPA 见第 32 章。 |
| **常见踩坑** | 探针路径错；limit 过小 OOMKilled。 |

**延伸阅读**：<https://kubernetes.io/docs/concepts/workloads/controllers/deployment/>
