# 第 33 章：安全基线（非 root、只读根文件系统）

## 0. 课程卡片

| 项目 | 说明 |
|------|------|
| **建议课时** | 50 分钟 |
| **学习目标** | 编写 `securityContext`；为只读根文件系统挂 `emptyDir`；镜像非 root |
| **先修** | 第 9、10 章 |

---

## 1. 项目背景

企业扫描要求 **non-root**、**readOnlyRootFilesystem**、**drop capabilities**。Quarkus 默认 stdout 日志友好；**临时文件**需挂载可写目录。

---

## 2. 项目设计：大师与小白的对话

**运维**：「Pod 必须以非 root 运行。」

**大师**：「Dockerfile `USER` 与 `runAsUser` 一致。」

**小白**：「只读根文件系统启动失败。」

**大师**：「`/tmp` 或 `java.io.tmpdir` 挂 **emptyDir**。」

**安全**：「Capabilities 还要 NET_BIND_SERVICE 吗？」

**大师**：「监听 8080 通常不需要 <1024 端口；**drop ALL**。」

---

## 3. 知识要点

- `runAsNonRoot` / `runAsUser`  
- `readOnlyRootFilesystem: true`  
- JVM Native 临时目录注意

---

## 4. 项目实战

### 4.1 `Dockerfile` 片段（JVM）

```dockerfile
FROM registry.access.redhat.com/ubi9/openjdk-17-runtime:1.20
WORKDIR /work
COPY target/quarkus-app/ /work/
USER 185
ENV JAVA_OPTS_APPEND="-Djava.io.tmpdir=/tmp"
EXPOSE 8080
ENTRYPOINT ["java", "-jar", "/work/quarkus-run.jar"]
```

### 4.2 完整 `k8s/secure-pod.yaml`

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: secure-app
spec:
  replicas: 1
  selector:
    matchLabels:
      app: secure-app
  template:
    metadata:
      labels:
        app: secure-app
    spec:
      securityContext:
        runAsNonRoot: true
        runAsUser: 185
        fsGroup: 185
      containers:
        - name: app
          image: registry.example.com/acme/secure-app:1.0.0
          securityContext:
            allowPrivilegeEscalation: false
            readOnlyRootFilesystem: true
            capabilities:
              drop: ["ALL"]
          volumeMounts:
            - name: tmp
              mountPath: /tmp
          ports:
            - containerPort: 8080
      volumes:
        - name: tmp
          emptyDir: {}
```

### 4.3 `application.properties`

```properties
# 若有写文件需求，显式指向 /tmp 下子目录
quarkus.http.body-handler.uploads-directory=/tmp/uploads
```

---

## 5. 课堂实验

| 步骤 | 操作 | 预期结果 |
|------|------|----------|
| 1 | 只读 FS 无 volume 启动 | 失败（预期） |
| 2 | 加 `emptyDir` `/tmp` | 成功 |
| 3 | `kubectl exec` 看进程用户 | 非 root |
| 4 | 镜像扫描（trivy image） | 记录 HIGH 数量基线 |

---

## 6. 项目总结

| 维度 | 说明 |
|------|------|
| **优点** | 降攻击面；合规。 |
| **缺点** | 排障路径变化。 |
| **适用场景** | 金融、政务、强基线。 |
| **注意事项** | OpenShift SCC。 |
| **常见踩坑** | 未挂 tmp；调试工具进生产镜像。 |

**延伸阅读**：<https://kubernetes.io/docs/concepts/security/pod-security-standards/>
