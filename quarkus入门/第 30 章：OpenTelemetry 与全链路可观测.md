# 第 30 章：OpenTelemetry 与全链路可观测

## 0. 课程卡片

| 项目 | 说明 |
|------|------|
| **建议课时** | 60～75 分钟 |
| **学习目标** | 启用 OTel 导出；理解采样率；日志与 trace 关联思路 |
| **先修** | 第 6、8 章；可选 Tempo/Jaeger 环境 |

---

## 1. 项目背景

**可观测三支柱**：logs、metrics、traces。OpenTelemetry 提供统一 SDK 与导出。生产必须控制 **采样率** 与 **attribute 基数**。

---

## 2. 项目设计：大师与小白的对话

**运维**：「日志里看不到 traceId。」

**大师**：「要启用 **log-trace correlation**（属性名查版本文档）。」

**小白**：「100% 采样？」

**大师**：「除非你希望 OTel Collector 和存储先撑不住。」

**测试**：「压测时开全采样？」

**大师**：「**不要**；用低采样 + 错误放大采样（若配置）。」

---

## 3. 知识要点

- `quarkus-opentelemetry`  
- Exporter：OTLP endpoint  
- 与 Micrometer 关系：互补

---

## 4. 项目实战

### 4.1 `pom.xml`

```xml
<dependency>
  <groupId>io.quarkus</groupId>
  <artifactId>quarkus-opentelemetry</artifactId>
</dependency>
```

### 4.2 `application.properties`（示例，属性以当前版本为准）

```properties
quarkus.otel.enabled=true
quarkus.otel.traces.enabled=true
# 示例：导出到本机 collector
quarkus.otel.exporter.otlp.endpoint=http://localhost:4317
quarkus.otel.traces.sampler=parentbased_traceidratio
quarkus.otel.traces.sampler.arg=0.1
```

### 4.3 `docker-compose-otel.yml`（课堂 Collector）

```yaml
services:
  otel-collector:
    image: otel/opentelemetry-collector:0.102.1
    command: ["--config=/etc/otel-collector-config.yaml"]
    volumes:
      - ./otel-collector-config.yaml:/etc/otel-collector-config.yaml:ro
    ports:
      - "4317:4317"
```

`otel-collector-config.yaml`（最简导出到日志，教学用）：

```yaml
receivers:
  otlp:
    protocols:
      grpc:
exporters:
  logging:
    loglevel: debug
service:
  pipelines:
    traces:
      receivers: [otlp]
      exporters: [logging]
```

### 4.4 Kubernetes：`OpenTelemetryCollector`（可选）

若使用 OpenTelemetry Operator，可部署 `Instrumentation` CR 自动注入；讲义以平台文档为准。

---

## 5. 课堂实验

| 步骤 | 操作 | 预期结果 |
|------|------|----------|
| 1 | 启动 collector + 应用，打几条请求 | collector 日志出现 span |
| 2 | 调采样率 0.01 vs 1.0 观察负载 | 体感差异 |
| 3 | 配置日志 correlation（若版本支持） | 日志行含 trace id |
| 4 | 讨论：PII 不进 span attribute | 合规清单 |

---

## 6. 项目总结

| 维度 | 说明 |
|------|------|
| **优点** | 标准化；生态广。 |
| **缺点** | 采样与基数治理难。 |
| **适用场景** | 生产微服务。 |
| **注意事项** | 出口网络策略。 |
| **常见踩坑** | 100% 采样；context 丢失。 |

**延伸阅读**：<https://quarkus.io/guides/opentelemetry>
