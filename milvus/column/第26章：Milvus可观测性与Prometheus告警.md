# 第26章：Milvus 可观测性与 Prometheus 告警

> **定位**：建立生产运维的监控基线。
> **版本**：Milvus 2.5.x
> **源码关联**：internal/proxy/metrics.go、internal/querynodev2/metrics.go、internal/datanode/metrics.go

---

## 1. 项目背景

运维老周的 Milvus 集群已经稳定运行了 3 个月。某天早上，业务方投诉"商品搜索非常慢"。老周打开 Grafana 想排查，发现一个尴尬的事实——虽然 Milvus 暴露了 Metrics 端口（9091），但没有任何 Grafana 大盘。他只能 SSH 到每台机器上手动 grep 日志。

更致命的是，没有告警机制。过去一个月里发生了两次隐性故障——一次是 QueryNode 内存悄悄涨到 90%，另一次是 MinIO 磁盘使用率达到 95%——都是业务方投诉后才发现。

老周痛定思痛，花了一周搭建了 Prometheus + Grafana 监控体系，配置了 5 条核心告警规则。结果第二天就收到了第一封告警邮件：`QueryNode 内存使用率 > 85%`。他在 QueryNode OOM 之前主动加了内存，避免了一次线上故障。

本章将覆盖 Milvus Metrics 分类、关键监控指标、Prometheus 采集配置、Grafana 大盘设计和核心告警规则。

---

## 2. 项目设计（剧本式交锋对话）

**第一幕：Milvus Metrics 分类——不是所有指标都要看**

*（老周把 Grafana 接好之后，发现有 300+ 个指标，眼花缭乱）*

**小胖**（看着满屏曲线）："300 多个指标！我看哪个都不知道——"

**大师**："不要被指标数量吓到。Milvus 的指标分成五类，每类只需关注 2-3 个核心指标就够了。"

**大师**（画分类图）：

```
Milvus Metrics 五大分类:

┌──────────────────────────────────────────────────────────────┐
│ 1. 请求类 (Proxy)                                            │
│    核心: milvus_proxy_req_count (请求总数)                    │
│         milvus_proxy_req_latency (请求延迟分布)               │
│         milvus_proxy_req_fail_count (失败请求数)              │
├──────────────────────────────────────────────────────────────┤
│ 2. 延迟类 (QueryNode / DataNode)                             │
│    核心: milvus_querynode_search_latency                      │
│         milvus_datanode_flush_latency                        │
├──────────────────────────────────────────────────────────────┤
│ 3. 资源类 (QueryNode / DataNode)                             │
│    核心: milvus_querynode_mem_usage (内存使用)                │
│         milvus_querynode_cpu_usage (CPU 使用)                 │
│         milvus_querynode_segment_count (Segment 数)           │
├──────────────────────────────────────────────────────────────┤
│ 4. 索引类 (IndexNode)                                        │
│    核心: milvus_indexnode_build_task_count (索引任务积压)     │
│         milvus_indexnode_build_latency (构建耗时)             │
├──────────────────────────────────────────────────────────────┤
│ 5. 存储类 (全局)                                              │
│    核心: milvus_storage_size (对象存储占用)                   │
│         milvus_datanode_flush_size (Flush 数据量)             │
│         milvus_compaction_task_count (Compaction 任务数)     │
└──────────────────────────────────────────────────────────────┘
```

**大师**："新手最容易犯的错误是'全量监控'——把所有 300 个指标都接入 Grafana，造成信息过载。记住 RED 原则——"

| 原则 | 含义 | 对应 Milvus 指标 |
|------|------|-----------------|
| **R**ate (请求速率) | 每秒多少个请求 | `milvus_proxy_req_count` |
| **E**rrors (错误率) | 请求失败比例 | `milvus_proxy_req_fail_count / milvus_proxy_req_count` |
| **D**uration (延迟) | 请求耗时分布 | `milvus_proxy_req_latency` (P50/P95/P99) |

> **技术映射**：RED 原则 = 体检三件套（心跳/血压/体温）；300 个指标 = 全身体检报告（正常人只需要看关键几项）。

---

**第二幕：Prometheus 采集配置与 Grafana 大盘设计**

**小白**："Prometheus 怎么采集 Milvus 的指标？需要 Agent 吗？"

**大师**："不需要。Milvus 各组件原生暴露了 `/metrics` 端点（默认端口 9091），Prometheus 直接 HTTP 拉取即可——"

```yaml
# prometheus.yml — Milvus 采集配置
scrape_configs:
  - job_name: "milvus-proxy"
    static_configs:
      - targets: ["proxy-1:9091", "proxy-2:9091"]
    metrics_path: "/metrics"
    scrape_interval: 15s

  - job_name: "milvus-querynode"
    static_configs:
      - targets: ["querynode-1:9091", "querynode-2:9091", "querynode-3:9091"]
    scrape_interval: 15s

  - job_name: "milvus-datanode"
    static_configs:
      - targets: ["datanode-1:9091", "datanode-2:9091"]
    scrape_interval: 15s

  - job_name: "milvus-indexnode"
    static_configs:
      - targets: ["indexnode-1:9091"]
    scrape_interval: 30s  # 索引指标变化慢，频率可以低一些
```

**大师**："Grafana 大盘设计——一个主 Dashboard + 三个专项 Dashboard："

```
主Dashboard: "Milvus Overview"
  面板布局（4行3列）:
  ┌────────────┬────────────┬────────────┐
  │ QPS        │ Error Rate │ P95 Latency│  ← 行1: 核心 RED
  ├────────────┼────────────┼────────────┤
  │ QN Memory  │ QN CPU     │ QN Segments│  ← 行2: QueryNode
  ├────────────┼────────────┼────────────┤
  │ DN Flush   │ Index Queue│ Storage    │  ← 行3: Data+Index
  ├────────────┼────────────┼────────────┤
  │ etcd Health│ MQ Lag     │ MQ Health  │  ← 行4: External
  └────────────┴────────────┴────────────┘

专项Dashboard:
  ① "QueryNode Deep Dive" — 每个 QN 的 Segment 分布、搜索延迟分位数
  ② "DataNode Deep Dive" — 每个 DN 的 Flush 吞吐、消费延迟
  ③ "IndexNode Deep Dive" — 索引构建队列、失败率
```

> **技术映射**：Prometheus = 体检数据采集仪（从各器官收集指标）；Grafana = 体检报告可视化（把数据画成图表）；主 Dashboard = 一页概览（CEO 看的）；专项 Dashboard = 器官详情（专科医生看的）。

---

**第三幕：5 条核心告警规则**

**小胖**："告警怎么配？我怕配太多变成'狼来了'——"

**大师**："只配置 5 条核心告警，每条都有明确的级别和处理人——"

```yaml
# alerts.yml — Prometheus 告警规则
groups:
  - name: milvus_critical
    rules:
    # ── 告警 1: 组件不可用 ──
    - alert: MilvusComponentDown
      expr: up{job=~"milvus-.*"} == 0
      for: 2m
      labels:
        severity: critical
      annotations:
        summary: "Milvus 组件 {{ $labels.job }} 不可用"
        description: "组件 {{ $labels.instance }} 已宕机超过 2 分钟"
        action: "立即检查 Pod/进程状态: kubectl get pods | grep {{ $labels.job }}"

    # ── 告警 2: 搜索延迟突增 ──
    - alert: MilvusSearchLatencyHigh
      expr: |
        histogram_quantile(0.95, 
          rate(milvus_proxy_req_latency_bucket{function_name="Search"}[5m])
        ) > 200
      for: 5m
      labels:
        severity: warning
      annotations:
        summary: "搜索 P95 延迟 > 200ms"
        description: "过去 5 分钟搜索 P95 延迟为 {{ $value }}ms"
        action: "检查 QueryNode 内存/CPU、Segment 数量、索引状态"

    # ── 告警 3: QueryNode 内存告警 ──
    - alert: MilvusQueryNodeMemoryHigh
      expr: milvus_querynode_mem_usage / milvus_querynode_mem_total > 0.85
      for: 5m
      labels:
        severity: warning
      annotations:
        summary: "QueryNode 内存使用率 > 85%"
        description: "节点 {{ $labels.instance }} 内存使用率 {{ $value | humanizePercentage }}"
        action: "考虑增加 QueryNode 或 Release 不常用的 Collection"

    # ── 告警 4: 写入积压 ──
    - alert: MilvusWriteBacklog
      expr: milvus_datanode_consume_lag > 10000
      for: 10m
      labels:
        severity: warning
      annotations:
        summary: "DataNode 消费延迟 > 10000 条"
        description: "MQ 积压达到 {{ $value }} 条，写入可能延迟"
        action: "检查 DataNode 资源、增加 DataNode 副本数"

    # ── 告警 5: 存储空间不足 ──
    - alert: MilvusStorageLow
      expr: milvus_storage_size / milvus_storage_capacity > 0.90
      for: 5m
      labels:
        severity: critical
      annotations:
        summary: "对象存储使用率 > 90%"
        description: "存储已使用 {{ $value | humanizePercentage }}"
        action: "清理旧备份、触发 Compaction、扩容存储"
```

**大师**："告警分级原则——"

| 级别 | 响应时间 | 通知方式 | 示例 |
|------|---------|---------|------|
| **Critical** | 5 分钟内 | 电话 + 短信 + IM | 组件宕机、存储不足 |
| **Warning** | 30 分钟内 | IM + 邮件 | 延迟飙升、内存告警 |
| **Info** | 下个工作日 | 邮件 | Segment 数量偏高 |

> **技术映射**：告警规则 = 烟雾报警器（烟大到一定程度才响）；告警分级 = 小火用灭火器（Warning），大火叫消防车（Critical）；狼来了 = 阈值设太低（P95 > 10ms 就告警，天天响）。

---

## 3. 项目实战

### 3.1 实战目标

搭建 Milvus 监控大盘，配置 5 条核心告警并模拟触发。

### 3.2 环境准备

```bash
# Prometheus + Grafana 快速部署
docker run -d --name prometheus -p 9090:9090 \
  -v ./prometheus.yml:/etc/prometheus/prometheus.yml \
  prom/prometheus

docker run -d --name grafana -p 3000:3000 grafana/grafana
pip install pymilvus==2.5.5
```

### 3.3 分步实现

#### 步骤 1：Grafana Dashboard JSON 基础模板

```python
# step1_grafana_template.py
"""生成 Grafana Dashboard JSON 模板"""
import json

dashboard = {
    "title": "Milvus Overview",
    "panels": [
        {
            "title": "Search QPS",
            "targets": [{
                "expr": 'sum(rate(milvus_proxy_req_count{function_name="Search"}[1m]))',
                "legendFormat": "QPS"
            }],
            "gridPos": {"x": 0, "y": 0, "w": 8, "h": 6}
        },
        {
            "title": "Error Rate",
            "targets": [{
                "expr": 'sum(rate(milvus_proxy_req_fail_count[1m])) / sum(rate(milvus_proxy_req_count[1m])) * 100',
                "legendFormat": "Error %"
            }],
            "gridPos": {"x": 8, "y": 0, "w": 8, "h": 6}
        },
        {
            "title": "Search P95 Latency (ms)",
            "targets": [{
                "expr": 'histogram_quantile(0.95, sum(rate(milvus_proxy_req_latency_bucket{function_name="Search"}[1m])) by (le))',
                "legendFormat": "P95"
            }],
            "gridPos": {"x": 16, "y": 0, "w": 8, "h": 6}
        },
        {
            "title": "QueryNode Memory Usage",
            "targets": [{
                "expr": 'milvus_querynode_mem_usage / milvus_querynode_mem_total * 100',
                "legendFormat": "{{instance}}"
            }],
            "gridPos": {"x": 0, "y": 6, "w": 12, "h": 6}
        },
        {
            "title": "Segment Count per QueryNode",
            "targets": [{
                "expr": 'milvus_querynode_segment_count',
                "legendFormat": "{{instance}}"
            }],
            "gridPos": {"x": 12, "y": 6, "w": 12, "h": 6}
        },
        {
            "title": "Index Build Queue",
            "targets": [{
                "expr": 'milvus_indexnode_build_task_count',
                "legendFormat": "Queue"
            }],
            "gridPos": {"x": 0, "y": 12, "w": 12, "h": 6}
        },
        {
            "title": "DataNode Flush Latency (ms)",
            "targets": [{
                "expr": 'histogram_quantile(0.95, rate(milvus_datanode_flush_latency_bucket[1m]))',
                "legendFormat": "P95"
            }],
            "gridPos": {"x": 12, "y": 12, "w": 12, "h": 6}
        },
    ]
}

with open("milvus_dashboard.json", "w") as f:
    json.dump(dashboard, f, indent=2)

print("Dashboard JSON 已生成 → milvus_dashboard.json")
print("导入方式: Grafana → Dashboards → Import → 上传此文件")
```

#### 步骤 2：告警模拟脚本

```python
# step2_alert_sim.py
"""模拟触发告警条件的脚本"""
import time
import json
import requests

PROMETHEUS = "http://localhost:9090"

def query_promql(promql: str) -> dict:
    """执行 PromQL 查询"""
    r = requests.get(f"{PROMETHEUS}/api/v1/query", params={"query": promql})
    return r.json()

def check_alerts():
    """执行 5 条告警检查"""
    alerts = []
    
    # 1. 组件可用性
    resp = query_promql('up{job=~"milvus-.*"}')
    for result in resp.get("data", {}).get("result", []):
        if float(result["value"][1]) == 0:
            alerts.append(f"[CRITICAL] 组件不可用: {result['metric']['instance']}")
    
    # 2. 搜索延迟
    resp = query_promql(
        'histogram_quantile(0.95, rate(milvus_proxy_req_latency_bucket{function_name="Search"}[5m]))'
    )
    for result in resp.get("data", {}).get("result", []):
        val = float(result["value"][1])
        if val > 0.2:  # > 200ms (值单位是秒)
            alerts.append(f"[WARNING] 搜索 P95={val*1000:.0f}ms > 200ms")
    
    # 3. QueryNode 内存
    resp = query_promql('milvus_querynode_mem_usage / milvus_querynode_mem_total')
    for result in resp.get("data", {}).get("result", []):
        val = float(result["value"][1])
        if val > 0.85:
            alerts.append(f"[WARNING] QN {result['metric']['instance']} 内存={val*100:.0f}%")
    
    # 4. 写入积压
    resp = query_promql('milvus_datanode_consume_lag')
    for result in resp.get("data", {}).get("result", []):
        val = float(result["value"][1])
        if val > 10000:
            alerts.append(f"[WARNING] DataNode 积压={val:.0f} 条")
    
    # 5. 存储
    resp = query_promql('milvus_storage_size / milvus_storage_capacity')
    for result in resp.get("data", {}).get("result", []):
        val = float(result["value"][1])
        if val > 0.90:
            alerts.append(f"[CRITICAL] 存储={val*100:.0f}%")
    
    return alerts

# 定时检查
print("Milvus 告警检查器启动 (每 30 秒)...")
while True:
    alerts = check_alerts()
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    
    if alerts:
        print(f"\n[{timestamp}] ⚠ {len(alerts)} 条告警:")
        for a in alerts:
            print(f"  {a}")
    else:
        print(f"\r[{timestamp}] ✓ 无告警", end="")
    
    time.sleep(30)
```

#### 步骤 3：使用 PyMilvus 获取组件级 Metrics

```python
# step3_pymilvus_metrics.py
"""通过 PyMilvus API 获取运行时 Metrics"""
from pymilvus import connections, utility

connections.connect(host="localhost", port="19530")

# 获取 Milvus 组件级别的 Metrics（需要在 Proxy 上开启）
# 注意：此功能需要 Milvus 2.4+ 且配置开启 metrics 端点
try:
    metrics_raw = utility.get_metric("system_info")
    print("系统信息:", metrics_raw[:200])
except Exception as e:
    print(f"Metrics 获取需要配置: {e}")

# 通过 HTTP 直接查询 Metrics 端点
import requests

def get_component_metrics(host: str, port: int = 9091):
    """获取指定组件的 Metrics"""
    try:
        r = requests.get(f"http://{host}:{port}/metrics", timeout=5)
        return r.text
    except Exception as e:
        return f"无法获取: {e}"

# 解析关键指标
def parse_metrics_text(text: str, metric_name: str):
    """从 Prometheus 格式文本中提取指定指标的值"""
    values = []
    for line in text.split("\n"):
        if line.startswith(metric_name) and not line.startswith("#"):
            parts = line.split()
            if len(parts) >= 2:
                values.append({"labels": parts[0], "value": parts[1]})
    return values

# 示例：获取 Proxy 的 QPS
proxy_metrics = get_component_metrics("localhost", 9091)
qps_data = parse_metrics_text(proxy_metrics, "milvus_proxy_req_count")
for item in qps_data[:5]:
    print(f"  {item['labels']} = {item['value']}")
```

---

## 4. 项目总结

### 4.1 Milvus 监控矩阵

| 层级 | 关键指标 | 健康范围 | 告警阈值 |
|------|---------|---------|---------|
| Proxy | QPS、Error Rate、P95 Latency | Error < 0.1%, P95 < 100ms | Error > 1%, P95 > 200ms |
| QueryNode | Memory%、CPU%、Segment Count | Memory < 80%, Segments < 1000 | Memory > 85%, Segments > 3000 |
| DataNode | Consume Lag、Flush Latency | Lag < 1000, Flush P95 < 2s | Lag > 10000, Flush > 5s |
| IndexNode | Build Queue、Build Latency | Queue < 10 | Queue > 100 |
| Storage | Object Storage Usage% | < 80% | > 90% |

### 4.2 注意事项

- **告警不要配太多**：5-8 条核心告警即可，多了会变成噪音（狼来了效应）。
- **Grafana 大盘不要信息过载**：主面板 12 个面板以内，详情放子面板。
- **Prometheus 数据保留设合理值**：默认 15 天，对长期趋势分析够用。

### 4.3 思考题

1. 如果 Milvus 部署在 K8s 中，Prometheus 的 `static_configs` 如何适配 Pod IP 的动态变化？有什么更好的服务发现方式？
2. 搜索 P95 延迟告警阈值应该设多少？如何根据历史数据动态调整阈值（而非拍脑袋定一个固定值）？

---

> **下一章预告**：第27章将完成 1000 万向量规模的系统压测和调优。读完本章，你应该能搭建完整的 Milvus 监控和告警体系。
