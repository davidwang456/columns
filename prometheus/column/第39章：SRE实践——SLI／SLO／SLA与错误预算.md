# 第39章：SRE实践——SLI/SLO/SLA与错误预算

## 1. 项目背景

某SaaS平台运维团队一直使用Prometheus做监控告警，配置了CPU超过80%、内存超过90%、磁盘快满等常规告警规则。运维团队一度认为自己的监控体系已经足够完善——所有仪表盘都是绿色的，所有告警都得到了及时处理。

然而产品经理频繁投诉："监控大盘明明是绿的，但用户一直说系统很慢，体验一直在恶化。"运维团队一头雾水，仔细排查后发现：虽然服务器资源一切正常，但核心API的P99延迟已经从200ms持续恶化到了800ms。只因为没有超过某个固定的硬阈值（如1000ms），告警系统对此毫无反应。

CTO在技术复盘会上提出："能不能换一个思路——从用户视角定义'服务健康度'？比如'99.9%的请求必须在500ms内返回'。如果某个月做不到这个目标，那么本月的所有非紧急发布暂停。"运维团队这才意识到：传统的"资源阈值告警"和真正的"用户体验健康度"之间存在巨大的鸿沟。

这正是Google SRE体系中SLI（服务水平指标）、SLO（服务水平目标）、SLA（服务水平协议）和错误预算（Error Budget）所要解决的核心问题。Prometheus天然适合定义和监控SLI/SLO——用PromQL写出SLI查询，设定SLO目标值，再用多窗口多燃尽速率告警（Multi-window Multi-burn-rate Alert）来保障错误预算不被意外耗尽。本章将从零构建一套完整的SLO监控体系。

## 2. 剧本式交锋对话

**小胖**："大师，我快被产品经理逼疯了。服务器资源明明全部正常，告警一条没触发，可用户投诉系统慢。查了半天才发现P99延迟悄悄从200ms涨到了800ms。我们能不能直接加一个'P99>500ms就告警'的规则？"

**大师**（摇头）："那样会收到大量误报。任何在线服务都无法做到100%完美——偶尔的P99抖动是正常的。SLO的精髓就在于：**允许一定比例的失败**。比如你定义'99.9%的请求要在500ms内返回'，这意味着允许0.1%的请求超过这个阈值。这0.1%就是你的错误预算（Error Budget）。"

**小白**："那错误预算有什么用？不还是在容忍失败吗？"

**大师**："错误预算的核心价值是**驱动决策**。当预算充足时（比如还剩80%），研发团队可以自由发布新功能，做一些带风险的变更。但当预算快耗尽时（比如只剩10%），就要冻结发布，全体投入可靠性修复。错误预算是连接'开发速度'和'稳定性'的桥梁——不再是'能发还是不能发'的主观判断，而是数据驱动。"

**小胖**："有点理解了。那告警什么时候该响？"

**大师**："关键在于**燃尽速率（Burn Rate）**。你需要在错误预算快被烧完之前收到告警。举个例子：99.9%的可用性SLO，30天窗口，错误预算=30天×0.1%=43.2分钟。如果1小时内就烧掉了2%的月度预算，这个速度（14.4x burn rate）意味着按此趋势5天内预算就会耗尽——必须立刻告警。这就是**多窗口多燃尽速率告警**的核心逻辑：短窗口（如1小时）检测快速燃烧，长窗口（如6小时）检测慢性燃烧，两者结合则触发告警。"

**小白**："所以不能简单地用P99>500ms就告警？"

**大师**："对。偶发性的SLO不达标是允许的——这正是错误预算存在的意义。只有预算快烧完时才需要告警和介入。Google SRE的建议是：当burn rate超过14.4x（即1小时烧掉2%的月度预算）时触发critical告警，超过1x（6小时烧掉5%）时触发warning告警。这样既能避免告警疲劳，又能在问题恶化之前给你足够的反应时间。"

## 3. 项目实战

### 环境准备

- Prometheus运行中，应用暴露RED指标（Rate/Errors/Duration）
- Grafana运行中
- 应用暴露Histogram类型的HTTP请求延迟指标（`http_request_duration_seconds_bucket`）和Counter类型的请求计数指标（`http_requests_total`）

### 步骤1：定义SLI的PromQL

**SLI类型1——可用性（Availability）：**

```promql
# 成功请求占比
sum(rate(http_requests_total{status=~"2..|3.."}[5m]))
/
sum(rate(http_requests_total[5m]))
* 100
```

**SLI类型2——延迟（Latency），使用Histogram bucket精确计算：**

```promql
# 延迟 ≤ 500ms的请求占比
sum(rate(http_request_duration_seconds_bucket{le="0.5"}[5m]))
/
sum(rate(http_request_duration_seconds_count[5m]))
* 100
```

**SLI类型3——吞吐量（Throughput），辅助判断：**

```promql
# QPS是否高于最低阈值
sum(rate(http_requests_total[5m])) >= 1000
```

### 步骤2：设定SLO并配置Recording Rule

以可用性为例，设定SLO为99.9%（30天滚动窗口），Error Budget = 30天 × 0.1% = 43.2分钟/月。

创建Recording Rule持久化保存SLI值，避免每次告警评估都重新计算：

```yaml
# prometheus_rules.yml
groups:
  - name: slo_recording_rules
    interval: 30s
    rules:
      - record: job:slo:availability:ratio_rate5m
        expr: |
          sum(rate(http_requests_total{status=~"2..|3.."}[5m]))
          /
          sum(rate(http_requests_total[5m]))

      - record: job:slo:latency_p99:seconds_rate5m
        expr: |
          histogram_quantile(0.99,
            sum(rate(http_request_duration_seconds_bucket[5m])) by (le)
          )
```

### 步骤3：Multi-window Multi-burn-rate Alert

这是Google SRE Book推荐的核心告警策略。核心理念：短窗口+短for检测快速燃烧，长窗口+长for检测慢性燃烧，两者AND组合避免误报。

```yaml
groups:
  - name: slo_alerts
    rules:
      # 快速燃烧：1h窗口，burn rate > 14.4x，for 5m
      - alert: SLOErrorBudgetBurnRateHigh1h
        expr: |
          (
            (1 - sum(rate(http_requests_total{status=~"2..|3.."}[1h]))
             / sum(rate(http_requests_total[1h])))
            /
            (1 - 0.999)
          ) > 14.4
          and
          (
            (1 - sum(rate(http_requests_total{status=~"2..|3.."}[5m]))
             / sum(rate(http_requests_total[5m])))
            /
            (1 - 0.999)
          ) > 14.4
        for: 5m
        labels:
          severity: critical
          slo: "99.9%"
        annotations:
          summary: "SLO错误预算高速燃烧（1h窗口，burn rate > 14.4x）"

      # 慢速燃烧：6h窗口，burn rate > 1x，for 15m
      - alert: SLOErrorBudgetBurnRateLow6h
        expr: |
          (
            (1 - sum(rate(http_requests_total{status=~"2..|3.."}[6h]))
             / sum(rate(http_requests_total[6h])))
            /
            (1 - 0.999)
          ) > 1.0
          and
          (
            (1 - sum(rate(http_requests_total{status=~"2..|3.."}[30m]))
             / sum(rate(http_requests_total[30m])))
            /
            (1 - 0.999)
          ) > 1.0
        for: 15m
        labels:
          severity: warning
          slo: "99.9%"
        annotations:
          summary: "SLO错误预算缓慢燃烧（6h窗口，burn rate > 1x）"
```

**Burn Rate速查表（基于30天SLO窗口）：**

| Burn Rate | 1h消耗 | 6h消耗 | 24h消耗 | 3d消耗 | 告警意义 |
|-----------|--------|--------|---------|--------|---------|
| 1x | 0.14% | 0.83% | 3.3% | 10% | 正常消耗，无需告警 |
| 10x | 1.4% | 8.3% | 33% | 100% | 需关注，准备排查 |
| 14.4x | 2% | 12% | 48% | — | 严重，1h窗口critical告警 |
| 100x | 14% | 83% | — | — | 极其严重，立即响应 |

### 步骤4：构建Grafana SLO Dashboard

**面板1——当前SLI值（Stat面板）：**

```promql
# 30天滚动窗口的可用性SLI
avg_over_time(job:slo:availability:ratio_rate5m[30d]) * 100
```

**面板2——错误预算剩余（Gauge面板）：**

```promql
# 剩余错误预算百分比
100 - ((1 - avg_over_time(job:slo:availability:ratio_rate5m[30d])) / (1 - 0.999) * 100)
```

阈值设置：绿(>50%)、黄(10%-50%)、红(<10%)。

**面板3——Burn Rate实时监控（Time Series）：**

```promql
# 当前1h窗口的burn rate
((1 - sum(rate(http_requests_total{status!~"2..|3.."}[1h]))
  / sum(rate(http_requests_total[1h])))
 / (1 - 0.999))
```

**面板4——错误预算燃尽趋势图（Time Series）：**

将累计消耗的错误预算随时间绘制成曲线，并与"均匀消耗线"对比——如果实际曲线在均匀消耗线之上，说明预算正在加速消耗。

### 步骤5：将SLO融入发布流程

错误预算驱动的发布决策矩阵：

- **预算剩余 > 50%**：开发团队可自由发布
- **预算剩余 20%-50%**：发布需TL审批
- **预算剩余 < 20%**：冻结所有非紧急发布
- **预算耗尽（<0%）**：仅允许可靠性修复发布

CI/CD Pipeline中的自动化检查脚本：

```bash
# 通过Prometheus API查询错误预算剩余
BUDGET_REMAINING=$(curl -s 'http://prometheus:9090/api/v1/query' \
  --data-urlencode 'query=100-((1-avg_over_time(job:slo:availability:ratio_rate5m[30d]))/(1-0.999)*100)' \
  | jq '.data.result[0].value[1]')

if (( $(echo "$BUDGET_REMAINING < 20" | bc -l) )); then
    echo "Error budget too low (${BUDGET_REMAINING}%), deploy blocked"
    exit 1
fi
```

### 可能遇到的坑

1. **SLI窗口选择**：太短（5m）产生毛刺，太长（1h）对故障不敏感——多窗口告警是正解。
2. **低流量陷阱**：如果每小时请求量<100，SLI统计无意义——设置最小流量阈值，低于阈值时跳过SLO评估。
3. **Burn rate阈值调优**：Google建议14.4x和1x是通用值，但7天SLO窗口需要重新计算（7天窗口下14.4x意味着1h烧掉约4%的预算）。
4. **多SLO冲突**：可用性SLO达标但延迟SLO不达标——需按业务优先级排序，核心SLO优先保障。

### 测试验证

模拟5xx错误率升高5%，观察burn rate告警是否在预期时间窗口内触发（1h窗口应在2-3分钟内触发），检查Grafana SLO Dashboard面板数据正确性和阈值色彩联动，在CI/CD pipeline中模拟低预算场景验证发布阻断逻辑。

## 4. 项目总结

### SLI/SLO/SLA概念关系

SLI（服务水平指标）是基础层——可量化的度量值，如"过去5分钟的成功率是99.95%"。SLO（服务水平目标）是中间层——给SLI设定目标值，如"30天滚动窗口的成功率≥99.9%"。SLA（服务水平协议）是顶层——对外承诺加上违约后果，如"月度可用性<99.9%则赔付10%服务费"。三者关系：SLI是温度计读数，SLO是设定范围，SLA是合同条款。

### SLI类型速查表

| SLI类型 | 定义 | PromQL模板 | 适用场景 |
|---------|------|-----------|---------|
| 可用性 | 成功请求/总请求 | `sum(rate(http_requests_total{status=~"2.."}[5m])) / sum(rate(http_requests_total[5m]))` | API、Web服务 |
| 延迟 | 快请求/总请求 | `sum(rate(http_request_duration_seconds_bucket{le="0.5"}[5m])) / sum(rate(http_request_duration_seconds_count[5m]))` | 面向用户服务 |
| 吞吐量 | 请求速率 | `sum(rate(http_requests_total[5m]))` | 容量规划 |
| 新鲜度 | 数据延迟 | `time() - last_successful_update_timestamp` | 数据处理管道 |

### 适用场景与不适用场景

**适用场景**：面向用户的API服务、SaaS平台、金融交易系统、需要SLA合同的对外服务。

**不适用场景**：批处理任务（不适合实时SLI）、纯内部工具（无需SLA约束）、极低流量测试环境（统计无意义）。

### 核心注意事项

SLO不是越多越好——3到5个核心SLO足以覆盖最关键的用户旅程。SLO需要定期review（每季度），随业务变化调整目标值。错误预算消耗必须可视化到研发团队的日常工作流中（如Slack机器人推送、发布平台集成），让每个人都看得到预算还剩多少。

### 常见踩坑经验

**案例1**：某团队用5分钟窗口计算SLI并直接告警，结果每次部署重启都触发大量误报。改为Multi-window Multi-burn-rate后，误报率降低90%以上。

**案例2**：同一服务同时配置了传统阈值告警（如"错误率>1%告警"）和Burn Rate告警，两者频繁重复触发。解决方案：让Burn Rate告警完全替代固定阈值告警，因为它们更精准地表达了"SLO面临风险"的语义。

**案例3**：SLO设定为99.99%（月度），错误预算仅4.3分钟/月。任何一次稍大的发布都会耗尽预算，导致冻结策略频繁触发，完全失去弹性调节的意义。后调整为99.9%，预算43.2分钟/月，业务和稳定性取得了良好平衡。

### 思考题

1. 如果你的服务有季节性流量波动（白天QPS 1000，夜间QPS 10），SLO应该如何设计？
2. 多个微服务之间有依赖关系，一个服务的可用性SLO是否需要考虑下游服务的可用性？为什么？
