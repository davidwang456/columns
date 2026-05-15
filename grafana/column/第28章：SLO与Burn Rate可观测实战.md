# 第28章：SLO与Burn Rate可观测实战

## 1. 项目背景

"我们99.9%的可用性目标是谁定的？开发说运维拍的脑袋，运维说是业务方提的。更尴尬的是，没人能量化地衡量我们是否真的达到了这个目标——告警有、监控有，但可靠性到底好不好，谁也说不清。"

SRE团队正在经历度量体系的缺失。他们使用Grafana做了大量监控，但所有监控都以"技术指标"为视角（CPU涨了、QPS降了），而不是"用户体验"为视角。用户关心的是"下单成功率"——而不是"某个服务的P99延迟"。

SLO（Service Level Objective，服务水平目标）正是把技术指标翻译成用户体验指标的桥梁。它定义了"好服务"的量化标准，并通过Error Budget（错误预算）和Burn Rate（燃烧速率）来驱动告警和决策。本章将教你从零构建SLO体系，并在Grafana上实现可视化与告警。

## 2. 项目设计

**小胖**（盯着99个微服务的Dashboard屏幕）：大师，老板问我们服务的可靠性是多少，我说"99.9%"，他说你咋知道的。我说"监控没报警啊"。然后他说"那跟没报警有啥区别？"

**大师**：他问到了SRE的核心——怎么量化可靠性。SLO的三步法：

**SLI（Service Level Indicator）**：服务的度量指标。比如HTTP请求的成功率 = 成功请求数/总请求数。

**SLO（Service Level Objective）**：SLI的目标值。比如"成功率 ≥ 99.9%（月度）"。

**SLA（Service Level Agreement）**：对客户的承诺。通常SLA比SLO更宽松（因为违约要赔钱）。

**小白**：那Error Budget和Burn Rate是什么关系？

**大师**：Error Budget = 1 - SLO。如果SLO是99.9%，Error Budget就是0.1%。意思是"这个月允许的不可用时间是 30天 × 0.1% ≈ 43分钟"。

Burn Rate是"消耗Error Budget的速度"。正常消耗速度是1x（比如1小时消耗0.0014%的Budget = 43分钟/月 ÷ 720小时）。如果Burn Rate突然变成10x，意味着你正在以10倍速度烧预算——43分钟的预算在4.3小时内就会耗尽。

**小胖**：所以告警不是"错误率>1%"就发，而是"错误率已经持续了多长时间的异常"才发？

**大师**：完全正确！SLO的告警基于Burn Rate而不是简单的阈值。

标准的多窗口Burn Rate告警：

| 窗口 | Burn Rate | 告警条件 | 含义 |
|------|-----------|---------|------|
| 1小时 | 14.4x | 1小时内烧了2%的月度Budget | 需要立即关注 |
| 6小时 | 6x | 6小时内烧了5%的月度Budget | 需要处理和修复 |
| 3天 | 3x | 3天内烧了10%的月度Budget | 拒绝新发布，全力修复 |
| 30天 | 1x | 月度Budget耗尽 | SLO未被达成 |

**小白**：这在Grafana上怎么实现？

**大师**：步骤是：

**1. 定义SLI（在Prometheus中）**：
```promql
# 成功率 SLI
sum(rate(http_requests_total{status!~"5.."}[5m]))
/
sum(rate(http_requests_total[5m]))
```

**2. 计算Error Budget Burn Rate**：
```promql
# 当前消耗的Budget
(1 - SLO_TARGET) - (
  sum(rate(http_requests_total{status=~"5.."}[30d])) /
  sum(rate(http_requests_total[30d]))
)
```

**3. 创建SLO Dashboard**：
- SLI当前值（Stat面板，与SLO目标对比）
- Error Budget剩余（Gauge面板，绿色→黄色→红色）
- Burn Rate趋势（Time series面板）
- Error Budget耗尽的预计时间

**4. 创建Burn Rate告警**：
```promql
# 1小时窗口Burn Rate > 14.4
(
  sum(rate(http_requests_total{status=~"5.."}[1h]))
  / sum(rate(http_requests_total[1h]))
) / 0.001 > 14.4
```

**技术映射**：SLO = 产品质量承诺（"次品率不超过0.1%"），Error Budget = 允许的次品数量（这个月只能出43个次品），Burn Rate = 次品出产速度（按这个速度下去，次品配额下周三就用完了）。

## 3. 项目实战

**环境准备**：使用之前的Docker Compose环境。

**步骤一：定义SLI和SLO**

创建一个Prometheus Recording Rule进行SLI计算：

```yaml
# slo-rules.yml
groups:
  - name: slo_metrics
    interval: 30s
    rules:
      - record: slo:availability:ratio5m
        expr: |
          sum(rate(http_requests_total{status!~"5.."}[5m]))
          / sum(rate(http_requests_total[5m]))
        labels:
          slo: "api-availability"
```

目标SLO：99.9%（即Error Budget = 0.1%）

**步骤二：创建SLO Dashboard**

Dashboard布局：

```
Row 1: 关键SLO指标
┌──────────────┬───────────────┬──────────────────┐
│ SLI 当前值   │ Error Budget  │ Budget耗尽预计     │
│ 99.95% (绿)  │ 72% remaining │ 15天后耗尽 (绿)   │
│ Stat面板     │ Gauge         │ Stat             │
└──────────────┴───────────────┴──────────────────┘

Row 2: Burn Rate趋势
┌────────────────────────────────────┐
│ Burn Rate（1h/6h/1d/30d 多窗口）    │
│ Time series                        │
└────────────────────────────────────┘

Row 3: SLI详细趋势
┌────────────────────────────────────┐
│ SLI 30天趋势（SLO目标线标注）       │
│ Time series + threshold            │
└────────────────────────────────────┘

Row 4: Error Budget 烧毁历史
┌────────────────────────────────────┐
│ Error Budget 剩余（30天累计）        │
│ Time series（从30天前开始减）        │
└────────────────────────────────────┘
```

关键PromQL：

```promql
# SLI当前值（Stat面板）
slo:availability:ratio5m * 100

# Error Budget剩余（Gauge面板）
(
  0.001 +  # 0.1%月度Budget
  sum(rate(http_requests_total{status=~"5.."}[30d]))
  / sum(rate(http_requests_total[30d]))
  - 0.001
) / 0.001 * 100
# 如果为负值表示Budget已超支

# Burn Rate（1小时窗口）
(
  sum(rate(http_requests_total{status=~"5.."}[1h]))
  / sum(rate(http_requests_total[1h]))
) / 0.001
# 值>1表示Budget消耗速度快于预期
# 值>14.4表示需要在1小时内响应
```

**步骤三：创建Multi-Window Burn Rate告警**

Grafana Alert Rule配置：

```yaml
# 告警规则1：快速烧Budget（1h窗口）
- alert: SLOBurnRate1hHigh
  expr: |
    (
      sum(rate(http_requests_total{status=~"5.."}[1h]))
      / sum(rate(http_requests_total[1h]))
    ) / 0.001 > 14.4
  for: 3m
  labels:
    severity: critical
    slo: api-availability
  annotations:
    summary: "1小时Burn Rate > 14.4x，Error Budget快速消耗"
    dashboard_url: "/d/slo-dashboard"

# 告警规则2：慢速烧Budget（6h窗口）
- alert: SLOBurnRate6hHigh
  expr: |
    (
      sum(rate(http_requests_total{status=~"5.."}[6h]))
      / sum(rate(http_requests_total[6h]))
    ) / 0.001 > 6
  for: 30m
  labels:
    severity: warning
    slo: api-availability

# 告警规则3：Budget即将耗尽
- alert: SLOErrorBudgetLow
  expr: |
    (0.001 + sum(rate(http_requests_total{status=~"5.."}[30d]))
    / sum(rate(http_requests_total[30d])) - 0.001) / 0.001 < 0.2
  for: 10m
  labels:
    severity: critical
  annotations:
    summary: "Error Budget剩余不足20%，禁止新版本发布"
```

**步骤四：SLO合规Dashboard（多服务聚合）**

创建一个总览SLO Dashboard，展示所有服务的SLO状态：

变量：`$service`（所有服务），`$slo_type`（availability/latency）。

核心面板（Table）：
- 列：服务名、SLO目标、SLI当前值、Error Budget剩余、Burn Rate趋势（Sparkline）、状态
- 着色：Budget<50%黄色、Budget<20%红色、Budget<10%深红

**步骤五：基于SLO的发布决策自动化**

CI/CD Pipeline集成：
```bash
#!/bin/bash
# 部署前检查Error Budget
SERVICE=$1
BUDGET=$(curl -s -H "Authorization: Bearer $TOKEN" \
  "http://localhost:3000/api/ds/query" \
  -d '{"queries":[{"refId":"A","datasource":{"uid":"prometheus"},"expr":"slo_error_budget_remaining{service=\"'"$SERVICE"'\"}"}]}' | \
  jq '.results.A.frames[0].data.values[1][0]')

if (( $(echo "$BUDGET < 20" | bc -l) )); then
    echo "ERROR: Error Budget for $SERVICE is at ${BUDGET}% (< 20%). Deployment blocked."
    echo "Reason: SLO compliance at risk. Wait for Budget regeneration or seek approval."
    exit 1
fi
```

**常见坑点**
1. **SLO目标设得太高**：99.999%（5个9）意味着全年只允许5分钟不可用时间——任何微小的故障都会耗尽Budget。从99.9%开始逐步提升。
2. **只用可用性SLO**：延迟也是用户的感知。建议至少有两个SLO：availability (成功率) + latency (P99延迟)。
3. **Burn Rate告警全开导致告警泛滥**：Multi-window告警会产生多条告警通知。用Group by合并。

**步骤六：实战——SLO驱动发布决策的完整流程**

某周五下午，payment-service准备发布v4.2.0。CI/CD检查Error Budget：

```bash
# pre-deploy-check.sh
SERVICE="payment-service"
SLO_TARGET=99.99

# 1. 计算当前Error Budget剩余
BUDGET_REMAINING=$(curl -s -G \
  "http://prometheus:9090/api/v1/query" \
  --data-urlencode "query=(0.0001 + sum(rate(http_requests_total{service=\"$SERVICE\",status=~\"5..\"}[30d])) / sum(rate(http_requests_total{service=\"$SERVICE\"}[30d])) - 0.0001) / 0.0001 * 100" | \
  jq -r '.data.result[0].value[1]')

echo "Error Budget remaining: ${BUDGET_REMAINING}%"

if (( $(echo "$BUDGET_REMAINING < 20" | bc -l) )); then
    echo "BLOCKED: Error Budget < 20%. Release denied."
    echo "Contact SRE team for manual approval: https://sre.example.com/release-approval"
    exit 1
elif (( $(echo "$BUDGET_REMAINING < 50" | bc -l) )); then
    echo "WARNING: Error Budget < 50%. Consider reducing release scope."
    echo "Proceed? (yes/no)"
    read CONFIRM
    if [ "$CONFIRM" != "yes" ]; then
        exit 1
    fi
fi

echo "Error Budget OK. Proceeding with release."
```

**2. 发布后SLO监控**：

发布后30分钟内，多窗口Burn Rate监控自动检查：

```promql
# 如果15分钟窗口Burn Rate > 10x，自动触发回滚建议
(
  sum(rate(http_requests_total{service="payment-service",status=~"5.."}[15m]))
  / sum(rate(http_requests_total{service="payment-service"}[15m]))
) / 0.0001 > 10
```

如果触发，OnCall通知："v4.2.0发布后15分钟内Error Budget的燃烧速率是正常的15倍。建议立即回滚。回滚命令：`kubectl rollout undo deployment/payment-service`"

**3. 每月SLO复盘Dashboard**：

创建每月SLO报告Dashboard，包含：
- 各服务SLO达成情况（✅/❌）
- Error Budget消耗曲线（30天）
- Top3 Budget消耗事件（哪次故障消耗最多Budget）
- 下月SLO目标建议（是否调整）

```promql
# 上月SLO达成率
slo:availability:ratio30d{service=~"$service"} >= 0.999

# Top Budget消耗事件（按日期分组最大的error rate）
topk(3, 
  sum by (date) (
    increase(http_requests_total{status=~"5.."}[1d])
  )
)
```

**4. 完整SLO生命周期管理**：
```
月初：设定SLO目标 + 分配Error Budget
月中：持续监控Burn Rate + 发布决策基于Budget
月末：复盘SLO达成 + 调整下月目标 + 优化可靠性
```

## 4. 项目总结

**SLO设计指南**

| SLI类型 | 示例 | 推荐SLO |
|---------|------|--------|
| 可用性 | HTTP成功率 | 99.9%（月度） |
| 延迟 | P99延迟 | <500ms |
| 吞吐量 | 请求处理数 | >1000/s |
| 新鲜度 | 数据更新延迟 | <5min |

**适用场景**
1. 服务可靠性度量：量化SRE工作成果
2. 发布决策：Error Budget不足时冻结发布
3. 容量规划：根据SLO推算需要的冗余资源
4. 团队目标对齐：产品/运维/开发用同一套SLO语言

**思考题**
1. 如果一个服务在月初就耗尽了Error Budget，月剩余时间怎么办？应该继续发布还是冻结？
2. SLO的观测窗口是30天，但如果你的产品每个月第1天流量极低（只有平时的1%），月初的Error Budget计算会不会失真？
