# 第28章：监控体系与 OpenTelemetry 全链路追踪

## 1. 项目背景

"CTO 在周一早会上点名了——'AI 客服上周五下午慢了 3 个小时，用户投诉爆了，你们运维团队居然最后一个知道？'"小陈羞愧地低下了头。他确实不知道——Dify 控制台的"日志"页只显示消息数量和 Token 消耗，没有任何性能指标。用户说慢，他只能靠感觉猜测：是 LLM 调用慢了？数据库满了？还是服务器 CPU 飙了？

直到他搭了 Prometheus + Grafana，才发现真相：周五下午 2 点，知识库从 5 万条涨到了 50 万条（运营批量导入了产品文档），向量检索延迟从 50ms 暴涨到 800ms，拖慢了整个 Chat 响应链。如果他早就有 Grafana 上的"检索延迟"指标——他在 2:05 就能发现问题，而不是等用户骂了 3 个小时。

这个教训教会了小陈一件事：**没有监控的 AI 系统就是在裸奔**。Dify 的基础设施层已经为可观测性做好了充分准备——集成了 OpenTelemetry 自动链路追踪，可以把 Trace 数据发送到 Langfuse；暴露了 `/health` 端点和 Prometheus 指标；支持 Sentry 异常追踪。但 Dify 本身不提供监控面板——意思是"数据都有，你得自己搭界面来看"。

本章搭建一套**三位一体**的可观测性体系，解决三个核心问题：①系统整体健不健康？（Prometheus + Grafana 的 RED 指标——Rate/QPS、Errors/错误率、Duration/延迟）②具体哪个请求慢、慢在哪？（OpenTelemetry + Langfuse 的全链路 Trace）③哪里出 bug 了？（Sentry 实时异常报警）。

## 2. 项目设计——剧本式交锋对话

**小胖**：（指着 Dify 控制台的"日志"页）"大师，这个日志页挺好——能看到每天多少条消息、消耗多少 Token。但我想看 QPS、P99 延迟、5xx 错误率——这些在哪？翻遍了控制台都没找到。"

**大师**："Dify 设计哲学是'专注 AI 编排，不重复造轮子'。业务日志（消息量、Token）它自己管，但系统指标（延迟、错误率、CPU）交给专业工具——你需要在外面搭 Prometheus + Grafana `/health` 端点已经暴露了基础指标，OpenTelemetry 也集成好了——你只需要'点亮'它们。"

**技术映射**：Dify 的可观测性策略 = 内置业务日志 + 外接专业监控工具。关注点分离。

**小白**："三种工具——Prometheus、Langfuse、Sentry——它们的分工是什么？感觉都在'看数据'，会不会重叠？"

**大师**："分工就像医院的不同科室，各看各的：

- **Prometheus + Grafana** = 体检科。看'整体健康'——QPS 多少、P99 延迟多少、错误率多少。如果 QPS 突然掉到 0——服务可能挂了。如果 5xx 率飙到 30%——有严重 bug。这些是运维值班每天必看的。

- **OpenTelemetry + Langfuse** = 放射科（CT 扫描）。看'定位病灶'——'刚才那个用户请求为什么慢了 15 秒？'打开 Trace，时间轴一目了然：DB 查询 50ms、知识库检索 450ms、LLM 调用 14.5 秒。一眼看出是 LLM 慢了。还能看到每次调用的完整 Prompt 和回复——这是调试 Prompt 质量的利器。

- **Sentry** = 急诊科。看'代码异常'——Controller 第 87 行有个 `KeyError: 'model'`，触发参数是 `{'provider': 'openai'}`，波及 230 个用户。秒级报警，带完整堆栈。"

**技术映射**：可观测性三柱 = Metrics（宏观趋势）+ Traces（微观链路）+ Errors（代码异常）。三者的时间分辨率和关注维度不同，互补不重叠。

**小胖**："那 Prometheus 怎么采集 Dify 的指标？Dify 暴露了 `/metrics` 端点吗？"

**大师**："Dify 没有原生的 `/metrics` 端点（不像一些框架自动暴露）。但有三个替代方案：①`/health` 端点返回 `{"status":"ok"}`——用 Blackbox Exporter 做存活监控。②Gunicorn 的 statsd 支持——配置 `--statsd-host` 可以把 Worker 状态发给 Prometheus。③最推荐的——用 OpenTelemetry Collector 接收 Dify 的 Trace/Metrics，再由 Collector 转发给 Prometheus。Dify 已经集成了 OTEL SDK，你只需要配个 Collector。"

**小胖**："Langfuse 能自建吗？我们公司的安全政策不允许把用户 Prompt 发到第三方 SaaS。"

**大师**："可以。Langfuse 是开源的（MIT 协议）。你可以在内网 Docker Compose 部署 Langfuse Server + PostgreSQL。然后把 Dify 的 `LANGFUSE_HOST` 指向你的内网地址就行了。部署步骤和 Dify 差不多——都是 Docker Compose 一键起。"

## 3. 项目实战

### 环境准备

| 条件 | 说明 |
|------|------|
| Dify 已部署 | Docker Compose 或 K8s |
| Prometheus + Grafana | 可独立部署或用 Docker Compose 扩展 |
| Langfuse（自建或 SaaS） | 免费注册 cloud.langfuse.com 或自建 |
| Sentry（可选） | sentry.io 或自建 |

### 分步实现

#### 步骤1：接入 Langfuse 做 LLM 全链路追踪（目标：可视化每次调用的 Token/延迟/Prompt）

```bash
# 方案A：使用 Langfuse Cloud（最简单，5分钟）
# 在 docker/.env 中配置：
LANGFUSE_SECRET_KEY=sk-lf-xxxxxxxxxxxxx
LANGFUSE_PUBLIC_KEY=pk-lf-xxxxxxxxxxxxx
LANGFUSE_HOST=https://cloud.langfuse.com

# 方案B：自建 Langfuse Server（数据不出内网）
# docker-compose.langfuse.yaml
langfuse-server:
  image: ghcr.io/langfuse/langfuse:latest
  ports: ["3000:3000"]
  environment:
    DATABASE_URL: "postgresql://langfuse:pass@langfuse-db:5432/langfuse"
    NEXTAUTH_SECRET: "your-secret"
```

配置后重启 API 容器，发送几个请求，在 Langfuse Dashboard 中看到：

```text
Trace: chat-request-abc123 (总耗时: 15.8s)
├── Span: Knowledge Retrieval (0.45s)
│   ├── Input Query: "我的订单到哪了"
│   └── Output: 3 chunks [Score: 0.92, 0.78, 0.45]
├── Span: LLM Generation (15.2s, 850 tokens)
│   ├── Input Prompt: [{"role":"system","content":"你是客服助手..."}]
│   ├── Output Response: "您好！根据查询，您的订单..."
│   ├── Model: gpt-4o
│   ├── Usage: prompt=320, completion=530, total=850
│   ├── Cost: $0.0085
│   └── First Token Time: 2.3s
└── Span: DB Save (0.05s)
    └── INSERT INTO messages ...

关键发现：
- LLM 调用耗时 15.2s 占了总时间的 96% → 优化重点
- First Token Time 2.3s → 模型排队时间较长
- 知识库检索 0.45s → 正常范围
- Token 成本 $0.0085 → 可追踪
```

#### 步骤2：Grafana 大盘设计（目标：一屏看尽系统健康）

核心 Panel 配置：

```yaml
# === Panel 1: QPS（Rate） ===
Query: rate(flask_http_requests_total[1m])
Title: 每秒请求数 (QPS)
Thresholds: 绿色 < 500, 黄色 500-1000, 红色 > 1000

# === Panel 2: P50/P95/P99 延迟（Duration） ===
Query: histogram_quantile(0.95, rate(flask_http_request_duration_seconds_bucket[5m]))
Title: API 响应延迟 (P95)
Thresholds: 绿色 < 3s, 黄色 3-10s, 红色 > 10s

# === Panel 3: 错误率（Errors） ===
Query: rate(flask_http_requests_total{status=~"5.."}[1m]) 
       / rate(flask_http_requests_total[1m])
Title: 5xx 错误率
Thresholds: 绿色 < 1%, 黄色 1-5%, 红色 > 5%

# === Panel 4: Celery 队列积压 ===
Query: celery_queue_length{queue="celery"}
Title: 待处理任务数
Thresholds: 绿色 < 20, 黄色 20-100, 红色 > 100

# === Panel 5: LLM 调用延迟（按模型） ===
Query: histogram_quantile(0.95, rate(model_call_duration_seconds_bucket[5m])) by (model)
Title: LLM 调用 P95 延迟（按模型）
说明: 区分 GPT-4 和 GPT-3.5 的延迟差异

# === Panel 6: 知识库检索延迟 ===
Query: histogram_quantile(0.95, rate(rag_retrieval_duration_seconds_bucket[5m]))
Title: 知识库检索 P95 延迟
说明: 这个指标最重要——检索变慢会直接拖累整个 Chat 响应
```

**Grafana 告警规则**：

```yaml
groups:
  - name: dify_critical
    rules:
      - alert: HighErrorRate
        expr: rate(flask_http_requests_total{status=~"5.."}[5m]) > 0.05
        for: 2m
        labels: {severity: critical, channel: "oncall"}
        annotations:
          summary: "Dify 5xx 错误率 {{ $value | humanizePercentage }}"
          
      - alert: LLMProviderSlow
        expr: histogram_quantile(0.95, rate(model_call_duration_seconds_bucket[5m])) > 30
        for: 5m
        labels: {severity: warning, channel: "llm-team"}
        annotations:
          summary: "LLM P95 延迟 {{ $value }}s，可能 Provider 限流"
          
      - alert: CeleryBacklog
        expr: celery_queue_length > 100
        for: 10m
        labels: {severity: warning}
        annotations:
          summary: "Celery 队列积压 {{ $value }}，Worker 可能挂了"
          
      - alert: RAGRetrievalSlow
        expr: histogram_quantile(0.95, rate(rag_retrieval_duration_seconds_bucket[5m])) > 1.0
        for: 5m
        labels: {severity: warning}
        annotations:
          summary: "知识库检索 P95 > 1s，可能文档量过大或索引需优化"
```

#### 步骤3：Sentry 异常追踪（目标：秒级定位代码 bug）

```bash
# docker/.env 中添加
SENTRY_DSN=https://xxx@sentry.io/xxx

# 在 Dify 控制台 → 设置 → 追踪 中也可配置
```

配置后，任何未捕获的 Python 异常都会自动发送到 Sentry，包含完整堆栈、本地变量、请求参数。例如：

```text
Sentry Issue: KeyError: 'model'
Location: api/services/app_generate_service.py:142
Request: POST /v1/chat-messages {query: "hello", user: "test"}
Stack:
  File "app_generate_service.py", line 142, in generate
    model = app_config['model']  # ← app_config 缺少 'model' key
Events: 230 in 5 minutes  # ← 230 个用户受影响
First seen: 2026-05-11 14:23:05
```

### 测试验证

```bash
# 1. 确认 OpenTelemetry 启用
docker logs docker-api-1 | Select-String "OpenTelemetry|OTEL|Langfuse"

# 2. 触发请求后在 Langfuse 中查看 Trace
curl -X POST http://localhost/v1/chat-messages \
  -H "Authorization: Bearer app-xxx" \
  -d '{"query":"Hello OTEL","user":"test","response_mode":"blocking"}'

# 3. 验证 Prometheus metrics
curl http://localhost:5001/metrics 2>/dev/null | head -20

# 4. 手动触发"故障"验证告警
# 故意停掉 Worker: docker stop docker-worker-1
# 观察 Grafana 中 celery_queue_length 是否飙红并触发告警
# 恢复: docker start docker-worker-1
```

## 4. 项目总结

### 三位一体可观测性

| 工具 | 看什么 | 精度 | 谁用 | 典型场景 |
|------|-------|------|------|---------|
| **Prometheus + Grafana** | 宏观：QPS、延迟、错误率 | 秒级 | 运维/oncall | 凌晨报警：5xx 率飙升 |
| **OpenTelemetry + Langfuse** | 微观：单次请求链路 | 毫秒级 | 后端开发 | 用户投诉"慢"，定位哪个环节慢 |
| **Sentry** | 代码异常：堆栈+参数 | 行级 | 全栈开发 | 新版本上线 10 分钟后发现 KeyError |

### RED 监控指标

| 指标 | 含义 | 健康基线 | 告警阈值 |
|------|------|---------|---------|
| Rate | QPS | — | 环比突降 > 50% |
| Errors | 5xx 率 | < 1% | > 5% |
| Duration | P95 延迟 | < 3s (Chat) | > 10s |

### 注意事项

1. **Langfuse 数据隐私**：所有 Prompt 和回复都发送到 Langfuse。敏感场景（医疗/金融）务必自建 Langfuse Server
2. **Prometheus 存储成本**：默认保留 15 天约消耗 15GB。生产环境建议配置远程存储（VictoriaMetrics/Thanos）
3. **告警不要设太敏感**：`for: 2m` 过滤瞬时波动，`group_wait: 30s` 合并多条告警防止手机被打爆

### 常见踩坑经验

1. **坑：配了 Langfuse 但看不到 Trace** → 根因：环境变量名拼错（`LANGFUS` vs `LANGFUSE`），或容器没重启。解决：`docker restart docker-api-1`
2. **坑：Grafana 全是 "No Data"** → 根因：Prometheus 的 `scrape_interval` 太大或 target 不通。`curl http://api:5001/health` 先验证连通性
3. **坑：Sentry 告警太多，真正问题被淹没了** → 根因：所有异常都上报了，包括业务层的 `AppNotFoundError`。解决：在 Sentry 中配置过滤规则，只保留 5xx 和未捕获异常

### 思考题

1. **进阶题**：在 Grafana 中如何区分"LLM 调用慢"和"知识库检索慢"——你需要两个独立的 Panel 各看各的。请写出各自的 PromQL。（提示：用 `model_call_duration` 和 `rag_retrieval_duration` 两个不同的 histogram metric）

2. **进阶题**：自建 Langfuse Server 后，如何设置数据保留策略——自动删除 90 天前的 Trace 数据以控制存储成本？

> **参考答案**：见附录 D
