# 第 3 章：配置体系与 12-factor

## 0. 课程卡片

| 项目 | 说明 |
|------|------|
| **建议课时** | 50～60 分钟 |
| **学习目标** | 掌握 `application.properties`、**profile**、环境变量覆盖；理解 `@ConfigMapping`；能画出「配置从 K8s 到 JVM」的路径 |
| **先修** | 第 1～2 章 |
| **课堂材料** | 白板画 ConfigMap → env → Quarkus |

---

## 1. 项目背景

[12-factor](https://12factor.net/config) 强调：**配置存环境**，与代码严格分离。Kubernetes 通过 **ConfigMap / Secret** 注入环境变量或挂载文件。  
Quarkus 使用 **SmallRye Config**：`application.properties` 提供默认值与 profile 片段，运行期可用 **环境变量**（含 `QUARKUS_` 前缀规则）覆盖。

本章让运维与开发用**同一张表**对齐：键名、敏感级别、各环境差异。

---

## 2. 项目设计：大师与小白的对话

**小白**：「我把配置打在 Helm values 里，算不算 12-factor？」

**大师**：「算**注入方式**，但应用仍要有**稳定键名**与**类型**（字符串/时长/布尔）。否则换交付方式（Helm → Operator）又要改代码。」

**运维**：「我们希望同一镜像跑 prod／staging，只靠 env 区分。」

**大师**：「那就用 `%prod.`、`%staging.` profile，再加 env 覆盖关键开关。镜像里**不要** baked-in 数据库密码。」

**测试**：「CI 里怎么模拟生产配置？」

**大师**：「用相同键名，值指向 testcontainer 或 mock；不要在测试里硬编码另一套 property 名。」

**小白**：「`quarkus.http.port=8080` 和 `QUARKUS_HTTP_PORT` 谁优先？」

**大师**：「环境变量通常覆盖文件（具体优先级可查官方 **Configuration Reference**）；课堂实验里你们会亲手验证。」

**架构师**：「复杂配置要不要用 YAML？」

**大师**：「可以，但团队要统一；多数项目 `properties` + `@ConfigMapping` 已够。」

**安全专员**：「Secret 会进环境变量，进程列表能看见吗？」

**大师**：「会。高敏场景评估 **文件挂载 + 只读 volume** 或外部密钥引擎；本章先建立基线意识。」

---

## 3. 知识要点

- **Profile**：`%dev`、`%test`、`%prod` 等前缀。  
- **环境变量映射**：点变下划线、大写，如 `my.service.timeout` → `MY_SERVICE_TIMEOUT`（`QUARKUS_` 用于 quarkus 命名空间）。  
- **`@ConfigMapping`**：类型安全、可校验。  
- **K8s**：`envFrom.configMapRef` + `secretRef` 是常见模式。

---

## 4. 项目实战

### 4.1 `application.properties`（多 profile 完整示例）

`src/main/resources/application.properties`：

```properties
# 默认（全环境共用）
quarkus.application.name=order-service
my.business.feature-x=false

# 开发：详细日志
%dev.quarkus.log.console.level=DEBUG
%dev.my.business.feature-x=true

# 生产：JSON 日志 + 关闭调试
%prod.quarkus.log.console.json=true
%prod.quarkus.log.console.level=INFO
%prod.quarkus.log.category."org.acme".level=INFO

# 业务键（示例）
my.service.timeout=2s
my.service.downstream-url=http://localhost:9000
```

### 4.2 `@ConfigMapping` 接口

`src/main/java/org/acme/config/MyServiceConfig.java`：

```java
package org.acme.config;

import io.smallrye.config.ConfigMapping;
import io.smallrye.config.WithDefault;
import java.time.Duration;

@ConfigMapping(prefix = "my.service")
public interface MyServiceConfig {

    Duration timeout();

    @WithDefault("http://localhost:9000")
    String downstreamUrl();
}
```

### 4.3 注入使用

```java
package org.acme;

import jakarta.enterprise.context.ApplicationScoped;
import jakarta.inject.Inject;
import org.acme.config.MyServiceConfig;

@ApplicationScoped
public class DownstreamProbe {

    @Inject
    MyServiceConfig cfg;

    public String describe() {
        return "timeout=" + cfg.timeout() + ", url=" + cfg.downstreamUrl();
    }
}
```

### 4.4 Kubernetes：ConfigMap + Deployment 片段（完整 YAML）

`k8s/10-configmap.yaml`：

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: order-service-config
  labels:
    app: order-service
data:
  MY_BUSINESS_FEATURE_X: "true"
  MY_SERVICE_TIMEOUT: "5s"
  MY_SERVICE_DOWNSTREAM_URL: "http://pricing.staging.svc.cluster.local:8080"
```

`k8s/11-deployment-env.yaml`（节选，与第 10 章完整 Deployment 可合并）：

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: order-service
spec:
  replicas: 1
  selector:
    matchLabels: { app: order-service }
  template:
    metadata:
      labels: { app: order-service }
    spec:
      containers:
        - name: app
          image: registry.example.com/acme/order-service:1.0.0
          ports:
            - containerPort: 8080
          env:
            - name: QUARKUS_PROFILE
              value: "prod"
          envFrom:
            - configMapRef:
                name: order-service-config
            # Secret 示例（键名需与配置映射一致）
            # - secretRef:
            #     name: order-service-secret
```

---

## 5. 课堂实验

### 实验 1：环境变量覆盖（约 15 分钟）

| 步骤 | 操作 | 预期结果 |
|------|------|----------|
| 1 | 不加 env 启动 `quarkus:dev`，访问打印 `MyServiceConfig` 的端点或单元测试 | `timeout=2s` |
| 2 | 同一 shell 执行 `export MY_SERVICE_TIMEOUT=10s` 再启动 | 变为 `10s` |
| 3 | 使用 `QUARKUS_PROFILE=prod` 启动，观察日志格式差异 | prod 下 JSON（若已按上文配置） |

### 实验 2：profile 行为（约 10 分钟）

| 步骤 | 操作 | 预期结果 |
|------|------|----------|
| 1 | 在 `%dev` 与 `%prod` 为同一键设不同值 | 切换 profile 后值变化 |
| 2 | 讨论：哪些键**禁止**进 ConfigMap（明文密钥） | 小组汇报 |

### 实验 3：kubectl 干跑（约 15 分钟，需集群或讲师演示）

| 步骤 | 操作 | 预期结果 |
|------|------|----------|
| 1 | `kubectl apply --dry-run=client -f k8s/10-configmap.yaml -o yaml` | 校验 YAML 语法 |
| 2 | （可选）apply 后 `kubectl get cm order-service-config -o yaml` | 数据与预期一致 |

**清理**：删除实验 ConfigMap：`kubectl delete configmap order-service-config --ignore-not-found`。

---

## 6. 项目总结

| 维度 | 说明 |
|------|------|
| **优点** | profile + env 模型清晰；可类型化；与 K8s 注入天然契合。 |
| **缺点** | 键名多时需文档；错误前缀导致「配置了不生效」。 |
| **适用场景** | 多环境、多集群、灰度开关。 |
| **注意事项** | 密钥不进 Git；Secret 轮转流程。 |
| **常见踩坑** | 误用 `%prod` 配置在本地；大小写与 `QUARKUS_` 规则混淆。 |

**延伸阅读**：<https://quarkus.io/guides/config-reference>
