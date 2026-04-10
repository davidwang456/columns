# 第 25 章：GraalVM Native Image

## 0. 课程卡片

| 项目 | 说明 |
|------|------|
| **建议课时** | 90～120 分钟（含首次 Native 构建） |
| **学习目标** | 执行 `-Dnative` 构建；理解 JVM vs Native 取舍；Native 容器多阶段构建概念 |
| **先修** | 第 1、9 章；CI 机器有足够 CPU/内存 |

---

## 1. 项目背景

**Native Image** 将应用编译为本地可执行文件，常带来 **更快启动、更小 RSS**；代价是 **构建时间长**、**反射/动态特性需配置**。适合强弹性、Serverless、边缘；未必是默认选项。

---

## 2. 项目设计：大师与小白的对话

**小白**：「全员 Native？」

**大师**：「**否**。先 JVM 模式跑稳 SLO，再对热点服务评估 Native。」

**运维**：「构建机 32C 都满。」

**大师**：「用 **远程构建**、**分层缓存**、**仅主干构建 Native**。」

**测试**：「Native 测试矩阵怎么搞？」

**大师**：「至少一条 **native integration** 流水线；与 JVM 矩阵分开。」

**架构师**：「动态代理很多的老库怎么办？」

**大师**：「评估替换或写 **reflection-config**；成本进选型表。」

---

## 3. 知识要点

- `./mvnw package -Dnative`  
- `quarkus.native.*`  
- Mandrel vs GraalVM CE

---

## 4. 项目实战

### 4.1 `pom.xml` 片段（profile）

```xml
<profiles>
  <profile>
    <id>native</id>
    <activation>
      <property>
        <name>native</name>
      </property>
    </activation>
    <properties>
      <quarkus.package.type>native</quarkus.package.type>
    </properties>
  </profile>
</profiles>
```

或使用命令行：`-Dnative`（Quarkus 插件识别）。

### 4.2 `application.properties`

```properties
quarkus.native.additional-build-args=-H:+ReportExceptionStackTraces
```

### 4.3 构建命令（Linux/macOS 或 WSL）

```bash
./mvnw package -Dnative -DskipTests
# 产出 target/*-runner
```

### 4.4 多阶段 `Dockerfile.native`（教学版）

```dockerfile
FROM quay.io/quarkus/ubi-quarkus-native-image:mandrel-builder-image-jdk-21 AS build
COPY --chown=quarkus:quarkus . /code
USER quarkus
WORKDIR /code
RUN ./mvnw -B package -Dnative -DskipTests

FROM registry.access.redhat.com/ubi9/ubi-minimal:9.4
WORKDIR /work/
COPY --from=build /code/target/*-runner /work/application
RUN chmod +x /work/application
EXPOSE 8080
USER 1001
ENTRYPOINT ["/work/application"]
```

> 镜像名以 Quarkus 官方文档当前推荐为准。

### 4.5 Kubernetes：`Deployment` 使用 Native 镜像

```yaml
spec:
  template:
    spec:
      containers:
        - name: app
          image: registry.example.com/acme/api-native:1.0.0
          resources:
            requests:
              memory: "64Mi"
              cpu: "50m"
            limits:
              memory: "128Mi"
              cpu: "500m"
```

（数值为示意，需压测校准。）

---

## 5. 课堂实验

| 步骤 | 操作 | 预期结果 |
|------|------|----------|
| 1 | JVM jar 记录冷启动到首请求耗时 | T_jvm |
| 2 | Native runner 同样测量 | T_native |
| 3 | 对比 RSS（`ps` 或 cgroup） | 数值记录 |
| 4 | 故意引入反射类未注册（讲师准备分支） | Native 运行失败，学会读报告 |
| 5 | 讨论：CI 是否每条 PR 构建 Native | 成本决策 |

---

## 6. 项目总结

| 维度 | 说明 |
|------|------|
| **优点** | 冷启动与内存常优。 |
| **缺点** | 构建慢；动态限制。 |
| **适用场景** | 强弹性、边缘。 |
| **注意事项** | CI 资源；缓存策略。 |
| **常见踩坑** | 反射未登记；滥用 run-time init。 |

**延伸阅读**：<https://quarkus.io/guides/building-native-image>
