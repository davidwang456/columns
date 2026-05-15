# 第23章：统一告警中心——Grafana Alerting + Prometheus Alertmanager

## 1. 项目背景

"Grafana里有告警，Prometheus Alertmanager里也有告警。运维看Alertmanager、开发看Grafana，两条线各管各的——当数据库出问题时，运维收到Alertmanager电话但开发在Grafana还没看到，两边信息不同步。"

这是大多数企业监控告警的现状——告警体系分裂。Grafana Alerting（基于Dashboard和外部数据源查询的告警）和Prometheus Alertmanager（基于Prometheus规则的告警）各有优势，但双轨运行导致了信息孤岛：告警查询方式不同、静默独立管理、通知模板不统一。

本章将探讨一道关键的"架构选择题"：是用Grafana Alerting统一所有告警？还是用Alertmanager统一？或是两者共存但通过API桥接？每种方案都有各自的利弊和工程适用性。

## 2. 项目设计

**小胖**（在两个浏览器标签页间切来切去）：大师，我现在运维告警在Alertmanager看，应用告警在Grafana看。上次数据库挂了，Alertmanager发了电话告警给运维，但开发在Grafana上没看到告警——等到用户投诉了才发现问题。有没有办法统一？

**大师**：这是告警体系设计的经典困境。我先给你梳理两套告警的本质区别。

**Grafana Alerting**：
- 查询引擎：Grafana内置，支持PromQL/LogQL/SQL等所有Grafana支持的数据源
- 规则管理：在Grafana UI中创建，存储在Grafana数据库
- 优势：Dashboard联动、多数据源、统一管理、Protheus + MySQL + Loki混合规则
- 劣势：评估性能不如Alertmanager（Go单线程评估）

**Prometheus Alertmanager**：
- 查询引擎：Prometheus自己的规则评估引擎
- 规则管理：Prometheus配置文件中写YAML
- 优势：评估性能极高、成熟稳定、支持高可用
- 劣势：只支持PromQL、与Grafana UI脱节

**小白**（推眼镜）：那实际选型怎么选？

**大师**：三种方案。

**方案A：全用Grafana Alerting（Grafana统一）**
```
Prometheus → Grafana Alerting → Contact Points
其他数据源 ↗
```
适合：团队规模不大（<50条告警规则），希望所有告警在Grafana统一管理。
缺点：评估引擎不如Alertmanager高效，大规模告警可能有性能瓶颈。

**方案B：全用Alertmanager（Prometheus统一）**
```
Prometheus → Alertmanager → Contact Points
Grafana只做可视化和告警状态展示
```
适合：告警规则主要在Prometheus中定义，团队习惯YAML管理。
缺点：无法利用Grafana的多数据源优势。

**方案C：Grafana Alerting + Alertmanager桥接（混合）**
```
Prometheus → Grafana Alerting → Alertmanager → Contact Points
MySQL/Loki → Grafana Alerting ↗
```
这是最推荐的方案——在Grafana中定义所有告警规则（享受多数据源和可视化联动），但通知路由由Alertmanager处理（享受成熟的降噪和分组）。Grafana Alerting作为"告警生成器"，Alertmanager作为"通知分发器"。

**小胖**：方案C听起来最理想。那具体怎么桥接？

**大师**：分两步走。

第一步：在Grafana Alerting的Contact Points中，添加一个Alertmanager类型的Contact Point。指向你的Alertmanager实例。

第二步：所有告警规则的通知策略指向这个Alertmanager Contact Point。Grafana生成的告警推送给Alertmanager，Alertmanager做分组降噪和通知分发。

这样你可以：
1. 在Grafana UI上看到所有告警规则和告警状态
2. 在Alertmanager统一管理静默、分组和多通道分发
3. Grafana的多数据源告警（MySQL/Loki）也能走Alertmanager

**小白**：那Prometheus自身的告警规则呢？如果Prometheus已经有100条老规则了，要不要迁移？

**大师**：这是一个过渡期问题。你可以选择：
- **保持双轨**：Prometheus的Rules不动（老规则），新规则全在Grafana中创建
- **逐步迁移**：把Prometheus的Rules迁移到Grafana Alerting（Grafana支持导入YAML格式的Prometheus Rules）
- **不迁移**：如果Prometheus Rules运行良好，没必要为了统一而统一。通过Alertmanager作为统一的"通知汇总点"即可实现"伪统一"。

**小胖**（松了口气）：那统一后的告警日常操作怎么做？比如查看告警、创建静默？

**大师**：统一后，日常操作在Grafana UI中完成：
- 查看告警状态：Grafana Alerting → Alert rules
- 创建静默：Grafana Alerting → Silences
- 规则管理：Grafana UI（可视化编辑）

Alertmanager UI退化成一个"后台引擎"，基本不需要直接操作（除非做全局静默或调试通知路由）。

**技术映射**：Grafana Alerting = 编辑部（生产告警内容），Alertmanager = 发行部（把告警分发给正确的读者），Contact Point = 发行渠道（报纸/网站/APP）。

## 3. 项目实战

**环境准备**

在Docker Compose中添加Alertmanager：

```yaml
  alertmanager:
    image: prom/alertmanager:v0.27.0
    container_name: alertmanager
    volumes:
      - ./alertmanager.yml:/etc/alertmanager/alertmanager.yml
    ports:
      - "9093:9093"
    command:
      - '--config.file=/etc/alertmanager/alertmanager.yml'
      - '--storage.path=/alertmanager'
```

创建 `alertmanager.yml`：

```yaml
global:
  resolve_timeout: 5m
  slack_api_url: 'https://hooks.slack.com/services/xxx'

route:
  receiver: 'default-receiver'
  group_by: ['alertname', 'severity', 'service']
  group_wait: 30s
  group_interval: 5m
  repeat_interval: 4h

  routes:
    - match:
        severity: critical
      receiver: 'pagerduty-critical'
      continue: false

    - match:
        severity: warning
        team: dev
      receiver: 'slack-dev'
      continue: true

    - match:
        severity: warning
        team: ops
      receiver: 'slack-ops'

receivers:
  - name: 'default-receiver'
    email_configs:
      - to: 'ops@example.com'

  - name: 'pagerduty-critical'
    pagerduty_configs:
      - routing_key: 'your-pd-routing-key'

  - name: 'slack-dev'
    slack_configs:
      - channel: '#dev-alerts'
        title: '{{ .CommonLabels.alertname }}'
        text: '{{ .CommonAnnotations.description }}'

  - name: 'slack-ops'
    slack_configs:
      - channel: '#ops-alerts'
```

**步骤一：Grafana → Alertmanager 桥接配置**

Grafana → Alerting → Contact points → Add contact point：

| 参数 | 值 |
|------|-----|
| Name | `alertmanager-bridge` |
| Integration | `Alertmanager` |
| URL | `http://alertmanager:9093/api/v2/alerts` |
| Basic Auth | (如果你的Alertmanager有认证) |

Save contact point。

**步骤二：创建告警规则并路由到Alertmanager**

Grafana Alerting → Alert rules → New alert rule。

创建一条测试告警：
- Rule name: `BridgeTest_ServiceDown`
- Query: `up{job="node_exporter"}`
- Expression: `IS BELOW 1`
- Labels: `severity: critical`, `team: dev`
- Annotations: `summary: Service {{ $labels.job }} is down`

在Alert rule → Labels and notifications中：
- Contact point override: 选择`alertmanager-bridge`（覆盖默认通知策略）

**步骤三：验证桥接**

触发测试告警（停止node_exporter）：
```bash
docker stop node_exporter
```

观察告警链路：
1. Grafana Alerting → Alert rules → 状态变为Firing
2. Grafana推送告警到Alertmanager → Alertmanager UI (`http://localhost:9093`) → 显示告警
3. Alertmanager根据配置路由通知到对应渠道

恢复：
```bash
docker start node_exporter
```

**步骤四：Prometheus Rules迁移到Grafana**

如果有已有的Prometheus告警规则，迁移步骤如下。

导出Prometheus规则：
```bash
curl http://prometheus:9090/api/v1/rules | jq '.data.groups'
```

转为Grafana API导入格式：
```python
import requests, yaml, json

# 从Prometheus拉取规则
resp = requests.get("http://prometheus:9090/api/v1/rules")
rules = resp.json()["data"]["groups"]

for group in rules:
    for rule in group["rules"]:
        # 转换为Grafana Alert Rule格式
        grafana_rule = {
            "title": rule["name"],
            "ruleGroup": group["name"],
            "folderUID": "alert-rules-folder",
            "for": rule.get("duration", 0),
            "condition": "C",
            "data": [
                {
                    "refId": "A",
                    "datasourceUid": "prometheus",
                    "model": {
                        "expr": rule["query"],
                        "intervalMs": 60000,
                        "datasource": {"type": "prometheus", "uid": "prometheus"}
                    }
                },
                {
                    "refId": "C",
                    "datasourceUid": "-100",
                    "model": {"type": "classic_conditions", "conditions": [...]}
                }
            ],
            "noDataState": "NoData",
            "execErrState": "Error",
            "labels": rule.get("labels", {}),
            "annotations": rule.get("annotations", {})
        }
        
        # 创建规则
        resp = requests.post(
            "http://localhost:3000/api/v1/provisioning/alert-rules",
            headers={"Authorization": "Bearer xxx", "Content-Type": "application/json"},
            json=grafana_rule
        )
        print(f"[{resp.status_code}] {rule['name']}")
```

**步骤五：统一告警Dashboard**

创建一张"统一告警总览"Dashboard，包含：

1. **活跃告警列表（Table面板）**
   数据源：Grafana内置的`-- Grafana --`数据源
   Query type: `Alerts`（显示当前Firing的告警）

2. **告警趋势（Time series）**
   从Grafana自身Prometheus指标查询：
   ```promql
   sum(grafana_alerting_alerts{state="firing"}) by (alertname)
   ```

3. **静默列表**
   ```promql
   sum(grafana_alerting_active_silences)
   ```

4. **通知发送统计**
   ```promql
   rate(grafana_alerting_notification_requests_total[5m])
   ```

**步骤六：告警治理最佳实践**

制定团队告警规范文档：
```markdown
## 告警规则编写规范

### Label 标准
每条告警规则必须包含以下Label：
- `severity`: critical | warning | info
- `team`: dev | ops | security | data
- `service`: 服务名称
- `component`: infrastructure | application | database | network

### Annotation 标准
- `summary`: 一句话告警描述（必填）
- `description`: 详细信息（必填）
- `runbook_url`: 处理手册链接（推荐）
- `dashboard_url`: 监控大屏链接（推荐）
- `value`: 触发时的值（必填）
- `threshold`: 阈值（必填）

### 告警评级标准
| 级别 | 定义 | 响应时间 | 通知方式 |
|------|------|---------|---------|
| critical | 用户可感知的服务中断 | 5min | PagerDuty + 电话 |
| warning | 需要关注但不紧急 | 30min | 钉钉群 + 邮件 |
| info | 提示性信息 | 次日 | 邮件 |

### 告警生命周期管理
1. 创建 → 评审（Is this alert actionable?）
2. 触发 → 处理（Follow runbook）
3. 恢复 → 复盘（Post-mortem）
4. 优化 → 调整阈值或删除无效规则
```

**常见坑点**
1. **Grafana → Alertmanager的网络问题**：Grafana推送告警时如果Alertmanager不可达，告警会重试但可能丢失。
2. **Alertmanager多重路由导致重复通知**：`continue: true`的route会让告警匹配多个receiver，同一条告警被发了多次。
3. **静默冲突**：Grafana和Alertmanager都有静默功能，创建一个静默可能被另一个覆盖。
4. **Label命名不一致**：Grafana Alerting规则中`severity: critical`，迁移到Alertmanager时期望匹配`severity: page`，导致路由匹配不上。

## 4. 项目总结

**三方案决策矩阵**

| 维度 | Grafana统一 | Alertmanager统一 | Grafana+Alertmanager桥接 |
|------|-----------|-----------------|------------------------|
| 学习成本 | 低 | 中 | 中 |
| 告警规则可视化 | ✅ 高 | ❌ Yaml | ✅ 高（规则在Grafana） |
| 通知分发 | 中 | ✅ 强 | ✅ 强（Alertmanager） |
| 多数据源告警 | ✅ 支持 | ❌ 仅PromQL | ✅ Grafana负责 |
| 性能 | 中 | ✅ 高 | 中（Grafana评估） |
| 运维复杂度 | 低 | 中 | 中-高 |

**适用场景**
1. Grafana统一：小型团队（<30条告警），希望所有功能在Grafana内
2. Alertmanager统一：告警量大（>100条）、性能要求高的环境
3. 桥接方案：中型以上团队，既要多数据源告警又要强通知能力

**注意事项**
1. Grafana Alerting评估引擎默认每60s评估一次，高频告警需要调小interval
2. Alertmanager的Cluster功能可以做到HA，Grafana Alerting目前单机运行
3. 迁移期间保留旧告警体系至少30天双轨运行，确认新体系稳定后再下线旧体系

**思考题**
1. Grafana Alerting和Alertmanager桥接后，如果一个告警同时在Grafana静默了、又在Alertmanager静默了——以哪个为准？如果两个静默策略冲突怎么办？
2. 如何设计一套"告警有效性指标"来度量统一告警中心的效果（如：告警处理率、MTTR、误报率）？
