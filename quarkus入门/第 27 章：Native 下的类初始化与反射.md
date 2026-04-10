# 第 27 章：Native 下的类初始化与反射

## 0. 课程卡片

| 项目 | 说明 |
|------|------|
| **建议课时** | 60 分钟 |
| **学习目标** | 使用 `quarkus.native.additional-build-args`；理解 `initialize-at-run-time`；收集反射配置流程 |
| **先修** | 第 25 章 |

---

## 1. 项目背景

Native Image 对 **类初始化时刻** 与 **反射** 敏感。典型症状：运行期 `NoSuchMethodError`、序列化失败。需要 **hint** 或重构减少动态性。

---

## 2. 项目设计：大师与小白的对话

**小白**：「JVM 好好的，Native 崩了。」

**大师**：「**更静态的世界**。动态越多，越要显式登记。」

**大师**：「`--initialize-at-run-time` 是止血绷带，不是架构。」

**测试**：「怎么回归 Native？」

**大师**：「CI 一条 **native integration**；关键路径覆盖反射点。」

**架构师**：「第三方库不兼容 Native 怎么办？」

**大师**：「换库、fork、或评估是否值得上 Native。」

---

## 3. 知识要点

- `quarkus.native.additional-build-args`  
- 官方 **native-image agent** 收集反射（以文档为准）  
- `META-INF/native-image` 资源

---

## 4. 项目实战

### 4.1 `application.properties`

```properties
quarkus.native.additional-build-args=\
  --initialize-at-run-time=com.example.legacy.LegacyHolder
```

### 4.2 注册反射（编程式示意，以扩展 API 为准）

若编写扩展，可使用 `ReflectiveClassBuildItem`（概念）；应用项目常用 JSON 配置：

`src/main/resources/META-INF/native-image/org.acme/myapp/reflect-config.json`（路径与格式以 GraalVM 文档为准）：

```json
[
  {
    "name": "com.example.Dto",
    "allDeclaredFields": true,
    "allDeclaredConstructors": true
  }
]
```

### 4.3 Maven 构建

```bash
./mvnw package -Dnative -DskipTests -Dquarkus.native.native-image-xmx=8g
```

### 4.4 Kubernetes：与 JVM 不同的资源建议

Native 进程堆概念不同；`resources` 以压测为准，**勿直接照搬 JVM 堆参数**。

```yaml
resources:
  requests:
    memory: "128Mi"
  limits:
    memory: "256Mi"
```

---

## 5. 课堂实验

| 步骤 | 操作 | 预期结果 |
|------|------|----------|
| 1 | 在演示分支运行 Native，记录失败栈 | 保存样例 |
| 2 | 添加 `initialize-at-run-time` 后重建 | 对比是否恢复 |
| 3 | 讨论：该类的静态初始化里做了什么 | 根因分析 |
| 4 | （讲师）演示 agent 收集反射 | 方法论 |

---

## 6. 项目总结

| 维度 | 说明 |
|------|------|
| **优点** | 有杠杆修复。 |
| **缺点** | 经验要求高。 |
| **适用场景** | Native 生产。 |
| **注意事项** | 记录每个 run-time init 理由。 |
| **常见踩坑** | 到处 run-time；JSON 未进镜像。 |

**延伸阅读**：<https://quarkus.io/guides/writing-native-applications-tips>
