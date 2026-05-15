# 第30章：Kubernetes监控与Grafana Operator

## 1. 项目背景

"K8s集群跑着200个Pod，10个Namespace，3个Node。每次新建一个微服务就得手工创建ServiceMonitor、PrometheusRule、GrafanaDashboard——光手动操作就占用了30%的上线时间。"

DevOps团队正在做Kubernetes监控的标准化建设。社区方案kube-prometheus-stack已经集成了Prometheus + Grafana + Alertmanager，但日常运维中的"手工创建Dashboard"操作依旧繁琐：新应用上线→开发去Grafana UI手动创建Dashboard→手动配变量→手动设告警——至少15分钟一个。

Grafana Operator通过Kubernetes CRD（自定义资源）把Dashboard/DataSource/Folder变成了Kubernetes原生资源。开发在Helm Chart或Kustomize中声明Dashboard→部署应用时Dashboard自动出现在Grafana中。这就是"Dashboard as Code"在K8s中的最佳实践。

本章从kube-prometheus-stack部署开始，带你实现"一个kubectl apply，Dashboard就位"的Operator自动化。

## 2. 项目设计

**小胖**：大师，我们在K8s上每部署一个微服务，就得去Grafana里手工建Dashboard。运维已经抱怨说这不是现代化做法。CI/CD都自动化了，最后一步"建Dashboard"还靠手工？

**大师**：你缺的是Grafana Operator。它的核心思想是把Dashboard定义成Kubernetes资源。你可以通过以下CRD管理Grafana：

```yaml
apiVersion: grafana.integreatly.org/v1beta1
kind: GrafanaDashboard
metadata:
  name: order-service-dashboard
spec:
  instanceSelector:
    matchLabels:
      app: grafana
  json: |
    {
      "title": "Order Service Dashboard",
      "panels": [...]
    }
```

`kubectl apply`这个YAML后，Operator自动把Dashboard导入到Grafana。

**小白**：那它和kube-prometheus-stack里的Dashboard管理有什么不同？

**大师**：kube-prometheus-stack是一个Helm Chart，它打包了Prometheus + Grafana + Node Exporter + 大量预定义Dashboard。它通过ConfigMap管理Dashboard（把JSON存入ConfigMap）。

Grafana Operator更进一步——它是独立的CRD，不依赖特定Helm Chart。你可以：
1. 在应用Helm Chart中包含GrafanaDashboard YAML
2. 部署应用时Dashboard自动出现在Grafana
3. Grafana升级/重建后，Operator自动重新导入所有Dashboard

**小胖**：那告警规则呢？也有CRD？

**大师**：告警规则走PrometheusRule CRD（由Prometheus Operator管理），不是Grafana Operator的范畴。但Grafana Operator可以管理GrafanaAlert CRD——这才是真正把Grafana Alerting纳入K8s管理的方案。

完整流程：
```
开发提交代码
  → CI构建镜像
    → Helm Chart部署（包含ServiceMonitor + PrometheusRule + GrafanaDashboard CRD）
      → Prometheus Operator：创建ServiceMonitor，Prometheus自动发现并抓取指标
      → Prometheus Operator：创建PrometheusRule，Prometheus自动加载告警规则
      → Grafana Operator：创建GrafanaDashboard，Grafana自动导入Dashboard
      → 开发者打开Grafana，Dashboard已经就位
```

**技术映射**：Grafana Operator = 自动化装修队（你在蓝图里画好Dashboard，装修队自动装修），CRD = 标准订单格式，ServiceMonitor = 自动发现标签（贴了"监控我"标签的Pod自动被抓取）。

## 3. 项目实战

**环境准备**

假设已有K8s集群（或Kind/Minikube）。

**步骤一：部署kube-prometheus-stack**

```bash
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm repo update

helm install monitoring prometheus-community/kube-prometheus-stack \
  --namespace monitoring --create-namespace \
  --set grafana.enabled=true \
  --set grafana.adminPassword=admin123 \
  --set prometheus.prometheusSpec.serviceMonitorSelectorNilUsesHelmValues=false
```

验证：
```bash
kubectl get pods -n monitoring
# 预期：prometheus-0, alertmanager-0, grafana-xxx, node-exporter-xxx, operator-xxx
```

**步骤二：部署Grafana Operator**

```bash
helm repo add grafana https://grafana.github.io/helm-charts
helm repo update

helm install grafana-operator grafana/grafana-operator \
  --namespace monitoring
```

验证CRD已创建：
```bash
kubectl get crd | grep grafana
# 预期：grafanadashboards.grafana.integreatly.org, grafanadatasources.grafana.integreatly.org
```

**步骤三：创建GrafanaDashboard CRD**

```yaml
# order-service-dashboard.yaml
apiVersion: grafana.integreatly.org/v1beta1
kind: GrafanaDashboard
metadata:
  name: order-service
  namespace: monitoring
  labels:
    app: grafana
spec:
  instanceSelector:
    matchLabels:
      app.kubernetes.io/name: grafana
  folder: "应用监控"
  json: |
    {
      "uid": "order-service-v1",
      "title": "订单服务监控",
      "tags": ["auto-generated", "order"],
      "panels": [
        {
          "title": "QPS",
          "type": "stat",
          "targets": [{
            "expr": "sum(rate(http_requests_total{service=\"order-svc\"}[5m]))"
          }],
          "gridPos": {"h": 4, "w": 6, "x": 0, "y": 0}
        },
        {
          "title": "Error Rate",
          "type": "stat",
          "targets": [{
            "expr": "sum(rate(http_requests_total{service=\"order-svc\",status=~\"5..\"}[5m])) / sum(rate(http_requests_total{service=\"order-svc\"}[5m])) * 100"
          }],
          "gridPos": {"h": 4, "w": 6, "x": 6, "y": 0}
        }
      ],
      "schemaVersion": 38
    }
```

应用：
```bash
kubectl apply -f order-service-dashboard.yaml
```

验证：
```bash
kubectl get grafanadashboards -n monitoring
# 检查Grafana中是否出现该Dashboard
```

**步骤四：GrafanaDataSource CRD**

```yaml
apiVersion: grafana.integreatly.org/v1beta1
kind: GrafanaDatasource
metadata:
  name: prometheus-ds
  namespace: monitoring
spec:
  instanceSelector:
    matchLabels:
      app.kubernetes.io/name: grafana
  datasource:
    name: Prometheus-K8s
    type: prometheus
    url: http://prometheus-operated.monitoring.svc:9090
    access: proxy
    isDefault: true
    editable: false
    jsonData:
      timeInterval: "15s"
```

**步骤五：应用Helm Chart中集成Dashboard**

在微服务的Helm Chart中添加GrafanaDashboard模板：

```yaml
# templates/grafana-dashboard.yaml
{{- if .Values.monitoring.enabled }}
apiVersion: grafana.integreatly.org/v1beta1
kind: GrafanaDashboard
metadata:
  name: {{ include "app.fullname" . }}
  labels:
    app.kubernetes.io/name: grafana
spec:
  instanceSelector:
    matchLabels:
      app.kubernetes.io/name: grafana
  folder: "{{ .Values.monitoring.folder }}"
  json: |
    {{ .Values.monitoring.dashboardJson | nindent 4 }}
{{- end }}
```

部署时Dashboard自动随应用创建。

**步骤六：Grafana Alert CRD（实验性）**

```yaml
apiVersion: grafana.integreatly.org/v1beta1
kind: GrafanaAlertRule
metadata:
  name: cpu-high-alert
spec:
  instanceSelector:
    matchLabels:
      app: grafana
  folderUID: "alert-folder"
  ruleGroup: "infrastructure"
  title: "CPU使用率过高"
  for: "5m"
  condition: "C"
  data:
    - refId: "A"
      datasourceUid: "prometheus"
      model:
        expr: '100 - (avg(rate(node_cpu_seconds_total{mode="idle"}[5m])) * 100)'
    - refId: "C"
      datasourceUid: "-100"
      model:
        type: "classic_conditions"
        conditions:
          - type: "query"
            evaluator:
              type: "gt"
              params: [80]
```

**常见坑点**
1. **GrafanaDashboard JSON格式错误**：YAML中嵌入JSON要注意缩进。可以用`--dry-run=client`预览。
2. **Dashboard UID冲突**：两个CRD用了相同uid→后者覆盖前者。确保UID唯一。
3. **Operator找不到Grafana实例**：`instanceSelector`的labels必须与Grafana deployment的labels匹配。
4. **Dashboard更新后Grafana中不生效**：Operator通过`resyncPeriod`定时比对。可以设置Grafana CR中的resyncPeriod更短或手动触发重载。

**步骤七：实战——应用Helm Chart中集成完整监控**

一个微服务的Helm Chart模板，包含ServiceMonitor + PrometheusRule + GrafanaDashboard三件套：

```yaml
# templates/monitoring.yaml
{{- if .Values.monitoring.enabled }}
---
# ServiceMonitor: 告诉Prometheus怎么采集指标
apiVersion: monitoring.coreos.com/v1
kind: ServiceMonitor
metadata:
  name: {{ include "app.fullname" . }}
  labels:
    release: monitoring
spec:
  selector:
    matchLabels:
      app: {{ include "app.fullname" . }}
  endpoints:
    - port: metrics
      interval: 15s
      path: /metrics
---
# PrometheusRule: 告警规则
apiVersion: monitoring.coreos.com/v1
kind: PrometheusRule
metadata:
  name: {{ include "app.fullname" . }}
spec:
  groups:
    - name: {{ include "app.fullname" . }}-alerts
      rules:
        - alert: HighErrorRate
          expr: |
            sum(rate(http_requests_total{
              service="{{ include "app.fullname" . }}",
              status=~"5.."
            }[5m])) / sum(rate(http_requests_total{
              service="{{ include "app.fullname" . }}"
            }[5m])) * 100 > 1
          for: 3m
          labels:
            severity: warning
            service: {{ include "app.fullname" . }}
          annotations:
            summary: "{{ include "app.fullname" . }} 错误率 {{ $value }}%"
            dashboard_url: "https://grafana.example.com/d/{{ include "app.fullname" . }}"
---
# GrafanaDashboard: 自动创建Dashboard
apiVersion: grafana.integreatly.org/v1beta1
kind: GrafanaDashboard
metadata:
  name: {{ include "app.fullname" . }}
spec:
  instanceSelector:
    matchLabels:
      app.kubernetes.io/name: grafana
  folder: "应用监控"
  json: |
    {
      "uid": "{{ include "app.fullname" . }}-v1",
      "title": "{{ include "app.fullname" . }} 监控",
      "tags": ["auto-generated"],
      "timezone": "browser",
      "panels": [
        {
          "title": "QPS",
          "type": "stat",
          "targets": [{
            "expr": "sum(rate(http_requests_total{service=\"{{ include "app.fullname" . }}\"}[5m]))",
            "refId": "A"
          }],
          "gridPos": {"h": 4, "w": 6, "x": 0, "y": 0}
        },
        {
          "title": "Error Rate",
          "type": "stat",
          "targets": [{
            "expr": "sum(rate(http_requests_total{service=\"{{ include "app.fullname" . }}\",status=~\"5..\"}[5m])) / sum(rate(http_requests_total{service=\"{{ include "app.fullname" . }}\"}[5m])) * 100",
            "refId": "B"
          }],
          "gridPos": {"h": 4, "w": 6, "x": 6, "y": 0},
          "fieldConfig": {
            "defaults": {
              "thresholds": {
                "steps": [
                  {"value": 0, "color": "green"},
                  {"value": 1, "color": "red"}
                ]
              }
            }
          }
        },
        {
          "title": "QPS Trend",
          "type": "timeseries",
          "targets": [{
            "expr": "sum(rate(http_requests_total{service=\"{{ include "app.fullname" . }}\"}[5m]))",
            "refId": "C"
          }],
          "gridPos": {"h": 8, "w": 12, "x": 0, "y": 4}
        }
      ],
      "schemaVersion": 38
    }
{{- end }}
```

部署时一次性创建应用+监控：
```bash
kubectl apply -f <(helm template myapp ./chart)
# 验证
kubectl get servicemonitor,prometheusrule,grafanadashboard | grep myapp
```

删除应用时监控自动清理：
```bash
helm uninstall myapp
# ServiceMonitor/PrometheusRule/GrafanaDashboard一并删除
```

## 4. 项目总结

**K8s监控CRD全家桶**

| CRD | 管理对象 | 所属Operator |
|-----|---------|-------------|
| GrafanaDashboard | Grafana Dashboard | Grafana Operator |
| GrafanaDatasource | Grafana Data Source | Grafana Operator |
| GrafanaAlertRule | Grafana Alert Rule | Grafana Operator |
| ServiceMonitor | Prometheus采集目标 | Prometheus Operator |
| PodMonitor | Prometheus Pod采集 | Prometheus Operator |
| PrometheusRule | Prometheus告警规则 | Prometheus Operator |

**优势**
| 特性 | 说明 |
|------|------|
| 声明式管理 | YAML描述期望状态，Operator自动达成 |
| 应用全生命周期 | Dashboard随应用一起部署、升级、删除 |
| GitOps友好 | Dashboard变更经过Git Review和CI/CD |
| 批量管理 | 修改模板后所有Dashboard自动更新 |

**适用场景**
1. K8s原生环境的Grafana管理
2. 大量微服务各需独立Dashboard
3. 多环境（dev/staging/prod）Dashboard自动同步

**思考题**
1. GrafanaDashboard CRD创建的Dashboard，如果在Grafana UI中被人手动修改了，Operator会怎么做？
2. 100个微服务×每个一个Dashboard = 100个GrafanaDashboard CRD。Grafana加载这么Dashboard会不会变慢？如何优化？
