# 第21章：Tempo分布式链路追踪

## 1. 项目背景

"订单接口的P99延迟从50ms飙升到了800ms，但监控图只能告诉我'变慢了'，根本不知道慢在哪个环节——是网关慢了？订单服务慢了？还是数据库慢了？"

微服务架构下的"慢请求定位"是所有SRE的噩梦。传统监控（Metrics）只能告诉你"出问题了"和"出问题的范围"，但无法精确定位问题在分布式调用链中的具体节点。你需要的是一个能追踪一次请求从网关→订单服务→库存服务→支付服务→数据库的全链路工具——这就是分布式链路追踪。

Grafana Tempo作为LGTM栈的"T"（Traces），设计理念与Loki和Prometheus一脉相承——低成本、大规模、与Grafana深度集成。它只存储Trace，不索引Span内容（除了必选标签），通过对象存储实现经济高效的长期保留。配合TraceQL查询语言和Service Graph可视化，Tempo填补了可观测性体系中最关键的"调用链黑盒"缺口。

本章将带你从OpenTelemetry埋点开始，搭建Tempo，到Grafana关联查询，完成从Metrics→Logs→Traces的三柱联动。

## 2. 项目设计

**小胖**（指着一根陡峭上升的P99延迟线）：大师，订单服务P99延迟飙了。Prometheus告诉我出事了，Loki告诉我日志里有timeout错误，但我还是不知道是哪个下游服务拖慢的。有没有办法看到一次请求的完整"行走路线"？

**大师**：这就是Trace的用武之地。Trace（追踪）记录了一次请求在分布式系统中的完整生命周期。它由多个Span（跨度）组成——每个Span代表调用链中的一个环节。

比如一次HTTP请求`POST /api/orders`，Trace的Span结构是：
```
Span: POST /api/orders (500ms)
  ├── Span: 参数校验 (2ms)
  ├── Span: MySQL查询用户 (30ms)
  ├── Span: gRPC 库存服务 (200ms)
  │   ├── Span: 检查库存 (50ms)
  │   └── Span: 锁定库存 (150ms)
  ├── Span: HTTP 支付服务 (250ms)
  │   └── Span: 支付处理 (240ms)
  └── Span: 写订单 (15ms)
```
一看就知道：库存服务和支付服务各占了差不多200ms，加起来就是瓶颈。

**小胖**：那Grafana Tempo在这其中扮演什么角色？

**大师**：Tempo只做一件事——接收、存储和查询Trace数据。不做采样决策（交给OpenTelemetry Collector）、不做业务分析（交给Grafana可视化）。这正好和Prometheus的设计哲学一致——专注做好一件事。

数据流向是：
```
应用(OpenTelemetry SDK) 
  → OpenTelemetry Collector(接收/采样/批处理)
    → Tempo(Ingester → 对象存储)
      → Grafana(查询/可视化)
```

**小白**：那TraceQL是什么？又是一种新的查询语言？

**大师**：TraceQL之于Tempo，相当于PromQL之于Prometheus、LogQL之于Loki。语法遵循管道+过滤的模式：

```traceql
{ span.http.method = "POST" && span.http.route = "/api/orders" } | select(status)
```

最基本的结构：
```
{ 条件过滤器 } | 操作符
```

支持的过滤条件包括：Span属性（`span.http.status_code`）、资源属性（`resource.service.name`）、Duration（`duration > 500ms`）、Error状态等。

Tempo + TraceQL + Grafana的组合让Trace的查询体验大大超越传统方案（如Jaeger UI），特别是：

1. **Service Graph**：自动生成微服务间的调用拓扑图，一眼看依赖关系和调用量。
2. **Trace to Metrics**（Span Metrics）：Tempo可以从Span中自动生成RED指标（Rate/Error/Duration），在Grafana上像Prometheus指标一样使用。
3. **Metric → Trace**（Exemplar）：从Prometheus指标异常点的Exemplar直接跳转到Tempo的完整Trace视图。

**小胖**：说到Exemplar，这和上一章Loki的Exemplar有什么关系？

**大师**：同一个机制。应用在Prometheus指标中携带`trace_id`作为Exemplar。当你在Grafana Time series面板上看到P99延迟异常点旁边的蓝色圆点，点击后可以选择跳转到Tempo查看完整Trace。如果应用同时把`trace_id`注入到日志中（通过OTel的Log correlation），那你可以从Tempo的Span再跳转到Loki查看该Span期间的日志——这就是LGTM三柱联动的闭环。

**技术映射**：Trace = 快递全程跟踪（每个中转站扫描=一个Span），Span = 快递中转记录（谁处理的、花多久），Service Graph = 快递路网图，Exemplar = 快递追踪号（贯穿所有环节的唯一标识）。

## 3. 项目实战

**环境准备**

在Docker Compose中添加Tempo和OpenTelemetry Collector：

```yaml
  tempo:
    image: grafana/tempo:2.5.0
    container_name: tempo
    command: ["-config.file=/etc/tempo.yaml"]
    volumes:
      - ./tempo.yaml:/etc/tempo.yaml
      - tempo_data:/var/tempo
    ports:
      - "3200:3200"  # tempo
      - "4317:4317"  # otlp grpc
      - "4318:4318"  # otlp http

  otel-collector:
    image: otel/opentelemetry-collector-contrib:0.96.0
    container_name: otel-collector
    volumes:
      - ./otel-config.yaml:/etc/otel/config.yaml
    ports:
      - "4319:4317"
      - "4320:4318"

volumes:
  tempo_data:
```

创建 `tempo.yaml`：

```yaml
server:
  http_listen_port: 3200

distributor:
  receivers:
    otlp:
      protocols:
        grpc:
        http:

ingester:
  max_block_duration: 5m

compactor:
  compaction:
    block_retention: 48h

storage:
  trace:
    backend: local
    local:
      path: /var/tempo/traces
    wal:
      path: /var/tempo/wal
```

创建 `otel-config.yaml`：

```yaml
receivers:
  otlp:
    protocols:
      grpc:
        endpoint: 0.0.0.0:4317
      http:
        endpoint: 0.0.0.0:4318

processors:
  batch:
    timeout: 1s
    send_batch_size: 1024
  memory_limiter:
    limit_mib: 512
    spike_limit_mib: 128

exporters:
  otlp:
    endpoint: tempo:4317
    tls:
      insecure: true

service:
  pipelines:
    traces:
      receivers: [otlp]
      processors: [memory_limiter, batch]
      exporters: [otlp]
```

**步骤一：应用OpenTelemetry埋点**

以Go服务为例，集成OTel SDK：

```go
package main

import (
    "context"
    "net/http"
    
    "go.opentelemetry.io/otel"
    "go.opentelemetry.io/otel/attribute"
    "go.opentelemetry.io/otel/exporters/otlp/otlptrace/otlptracegrpc"
    "go.opentelemetry.io/otel/sdk/resource"
    sdktrace "go.opentelemetry.io/otel/sdk/trace"
    semconv "go.opentelemetry.io/otel/semconv/v1.24.0"
    "go.opentelemetry.io/contrib/instrumentation/net/http/otelhttp"
)

func initTracer() (*sdktrace.TracerProvider, error) {
    ctx := context.Background()
    
    exporter, err := otlptracegrpc.New(ctx,
        otlptracegrpc.WithEndpoint("otel-collector:4317"),
        otlptracegrpc.WithInsecure(),
    )
    if err != nil {
        return nil, err
    }
    
    tp := sdktrace.NewTracerProvider(
        sdktrace.WithBatcher(exporter),
        sdktrace.WithResource(resource.NewWithAttributes(
            semconv.SchemaURL,
            semconv.ServiceName("order-service"),
            attribute.String("environment", "production"),
        )),
        sdktrace.WithSampler(sdktrace.AlwaysSample()), // 开发环境全采样
    )
    
    otel.SetTracerProvider(tp)
    return tp, nil
}

func main() {
    tp, _ := initTracer()
    defer tp.Shutdown(context.Background())
    
    // 使用otelhttp自动为HTTP Server创建Span
    handler := otelhttp.NewHandler(http.HandlerFunc(orderHandler), "POST /api/orders")
    http.ListenAndServe(":8080", handler)
}

func orderHandler(w http.ResponseWriter, r *http.Request) {
    ctx := r.Context()
    tracer := otel.Tracer("order-service")
    
    // 手动创建子Span - 查询数据库
    ctx, dbSpan := tracer.Start(ctx, "query-database")
    time.Sleep(30 * time.Millisecond) // 模拟数据库查询
    dbSpan.SetAttributes(attribute.String("db.system", "mysql"))
    dbSpan.End()
    
    // 手动创建子Span - 调用下游服务
    ctx, rpcSpan := tracer.Start(ctx, "call-payment-service")
    time.Sleep(200 * time.Millisecond) // 模拟RPC调用
    rpcSpan.End()
    
    w.WriteHeader(200)
}
```

**步骤二：配置Grafana Tempo数据源**

Grafana → Data Sources → Add data source → Tempo：

| 参数 | 值 |
|------|-----|
| Name | `Tempo` |
| URL | `http://tempo:3200` |
| Trace to logs | Loki → 选择Loki数据源 → Tag: `trace_id` |
| Trace to metrics | Prometheus → 选择Prometheus数据源 → Tag: `trace_id` |
| Service map | 开启 |
| Span bar | 开启 |

Save & test。

**步骤三：Explore中的Trace查询**

打开Grafana Explore → 选择Tempo数据源 → Query type选`TraceQL`。

```traceql
# 查询所有包含错误的Span
{ status = error }

# 查询特定服务的慢Trace（>1秒）
{ resource.service.name = "order-service" && duration > 1s }

# 查询特定HTTP接口的Trace
{ span.http.method = "POST" && span.http.route = "/api/orders" }

# 查询调用过支付服务的Trace
{ resource.service.name = "payment-service" }

# 查询特定TraceID
{ trace:id = "abc123def456" }
```

查询结果会列出所有匹配的Trace，点击一个TraceID展开：
- **Trace视图**：瀑布图展示所有Span的时间分布
- **Span详情**：每个Span的属性、事件、错误信息
- **Service Graph**：从Trace中推导的服务依赖拓扑

**步骤四：Service Graph服务拓扑**

在Grafana中创建Service Graph面板。不需要写查询——Tempo数据源内置了Service Graph功能。

Dashboard → Add panel → 选择Tempo数据源 → 面板类型选择`Node Graph`。

配置：
- Data source: Tempo
- Query type: Service Map

Grafana自动从Tempo的Trace数据中构建服务间调用关系图。节点大小反映请求量，连线粗细反映调用频率，连线颜色反映错误率/延迟。

**步骤五：Metrics→Traces→Logs三柱联动**

这部分需要应用配合做Exemplar关联：

在Prometheus指标中注入TraceID：
```go
import "github.com/prometheus/client_golang/prometheus"

var requestDuration = prometheus.NewHistogramVec(
    prometheus.HistogramOpts{
        Name: "http_request_duration_seconds",
    },
    []string{"method", "path"},
)

// 在处理函数中
requestDuration.WithLabelValues(r.Method, r.URL.Path).ObserveWithExemplar(
    duration,
    prometheus.Labels{"trace_id": span.SpanContext().TraceID().String()},
)
```

然后配置Grafana的数据源间关联：

1. **Prometheus → Tempo**：Prometheus数据源Settings → Exemplars → Add link → Tempo → URL: `$${__value.raw}` → Label: `trace_id`

2. **Tempo → Loki**：Tempo数据源Settings → Trace to logs → Datasource: Loki → Tags: `[{"key": "trace_id", "value": "trace_id"}]` 

3. **验证联动**：
   - 在Prometheus Time series面板中看到异常数据点→点击旁边的蓝色Exemplar点→跳转到Tempo查看Trace
   - 在Tempo的Span详情中→点击"Logs for this span"→跳转到Loki查看该Span期间的日志

**步骤六：Trace摘要指标（Span Metrics）**

Tempo可以自动从Span生成RED指标，在Grafana中当作Prometheus指标查询：

创建两个Tempo数据源（一个用于Trace查询，一个用于Metrics）：

第二个Tempo数据源URL同样指向Tempo，但额外配置：
- 开启`Span metrics`（需要Tempo中启用`metrics_generator`）

然后在Grafana面板中查询：
```traceql
# 等同于PromQL中的rate
{ resource.service.name = "order-service" } | rate() by (span.http.status_code)

# 延迟直方图
{ resource.service.name = "order-service" } | histogram_over_time(duration) by (span.name)
```

**常见坑点**
1. **采样率过高导致存储膨胀**：全采样在生产环境不现实（每秒10000请求×100个微服务）。合理设置采样策略：错误必须采样（errors always sample），正常请求按1%-10%采样。
2. **OTel版本兼容**：OTel SDK、Collector、Tempo的版本要匹配。Tempo 2.5支持OTLP 1.0+。
3. **Trace跨服务时丢失**：确保TraceContext在HTTP Header（`traceparent`）、gRPC Metadata、消息队列消息头中正确传递。
4. **Tempo Ingester OOM**：Ingester在内存中暂存Trace数据后刷写。流量突增可能导致OOM。调整`max_block_bytes`。

## 4. 项目总结

**三柱联动体系**

| 可观测性支柱 | 数据 | 工具 | 查询语言 | 回答的问题 |
|-------------|------|------|---------|-----------|
| Metrics | 聚合指标 | Prometheus/Mimir | PromQL | 出问题了吗？ |
| Logs | 事件日志 | Loki | LogQL | 日志里有什么线索？ |
| Traces | 请求链路 | Tempo | TraceQL | 是哪个环节慢了？ |

**优点**
| 特性 | 说明 |
|------|------|
| 低成本 | 对象存储后端，成本远低于Jaeger+ES |
| 深度集成 | 与Grafana/Prometheus/Loki三柱联动 |
| 无依赖 | 不需要Cassandra/ES等外部数据库 |
| TraceQL | 强大的Trace搜索语言 |

**缺点**
| 特性 | 说明 |
|------|------|
| 无全文搜索 | Trace内容不做索引，搜索依靠标签 |
| 采用要求 | 需要应用集成OpenTelemetry SDK |
| 生态比Jaeger小 | 部分企业级功能还在开发中 |

**适用场景**
1. 微服务慢请求定位：从Metrics→Trace→Logs三步下钻
2. 分布式调用链分析：Service Graph看依赖和瓶颈
3. 错误请求溯源：精确找到出错的那个Span
4. 性能基线分析：Trace聚合指标做长期趋势

**注意事项**
1. OpenTelemetry SDK有性能开销（约1-5% CPU），高QPS场景注意采样策略
2. Tempo的查询结果受采样率影响——低采样率下Trace可能不完整
3. Service Graph依赖完整的Trace数据，缺Span会导致拓扑不准确
4. Trace的保留时间由Tempo的compactor配置决定

**常见踩坑经验**
1. **Span超时后丢弃**：Tempo默认max_trace_duration是30分钟，超长Trace会被截断。
2. **generator挂掉丢失Span Metrics**：metrics_generator计算的是窗口内的数据，挂了后窗口期数据丢失。
3. **TraceID格式不匹配**：Tempo要求32位十六进制TraceID，某些SDK可能生成不同格式的ID。

**思考题**
1. 生产环境每秒10000请求，100个微服务。如何设计采样策略既能捕获关键Trace（错误/慢请求）又不让存储成本爆炸？
2. Service Graph会自动从Trace中推导服务依赖。但如果两个服务之间通过消息队列异步通信，Service Graph还能正确展示它们的依赖关系吗？
