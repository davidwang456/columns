# 第27章：监控体系与 Prometheus 集成

## 1 项目背景

某金融科技公司Harbor上线一年后，运维团队发现自己对Harbor的运行状态"几乎看不见"——内部审计报告指出Harbor作为公司镜像供应链的核心基础设施，却没有任何白盒监控，被列为高危风险项。

**痛点一：被动发现故障，平均发现延迟45分钟。** 每次都是用户在群里喊"Docker pull特别慢"或"CI构建失败了"，运维才知道Harbor出了问题。有一次PostgreSQL的WAL日志把磁盘写满（当时未配置`max_wal_size`），Registry写入全部阻塞——但运维直到2小时后开发者无法push才意识到。这2小时内积压了1200+个失败的CI构建任务，影响了当天所有服务的上线窗口。

**痛点二：缺少性能基线，故障排查靠直觉。** 运维不知道"正常"的API QPS是多少、P99延迟应该是多少毫秒。当有人反馈"Harbor变慢了"，运维只能凭经验猜测是"数据库问题"还是"网络问题"——没有数据支撑的猜测就像蒙眼射箭。更麻烦的是，领导要求运维提交月度SLA报告，但运维拿不出任何量化的可用性数据，只有零散的工单记录。

**痛点三：存储增长无预警，扩容永远滞后。** Harbor存储从200GB涨到750GB用了5个月——运维直到磁盘使用率达到92%才收到操作系统级磁盘告警。此时紧急扩容需要走采购审批→到货→上架→配置的流程，至少3周。如果能提前1个月预判增长趋势，就能从容规划。更糟的是，他们没有按项目维度统计存储用量——不知道是哪个团队的镜像占了最多空间，无法做成本分摊（成本核算）。

**痛点四：Prometheus集成配置混乱，监控数据"假阳性"。** Harbor官方文档提到了Prometheus集成，但运维按照文档配置后，Grafana中看到的指标要么为空，要么数值异常。排查后发现三个隐藏问题：(1) Harbor的Metrics端点默认不暴露，需要在`harbor.yml`中显式开启且重新`prepare`；(2) 不同组件的Metrics端口各不相同——Core用9090、Registry用5001、JobService用8080，文档中未统一说明；(3) Core的Metrics端点需要Basic Auth，而Prometheus的`basic_auth`配置在2.x版本的YAML缩进格式极为敏感。

本章将使用"四金指标"（USE+RED方法论）完整搭建Harbor + Prometheus + Grafana + Alertmanager的监控体系，从黑盒监控到白盒监控全面覆盖，并配置分级告警规则。

---

## 2 项目设计——剧本式交锋对话

**场景：周一晨会后，运维组长在会议室投影上打开了一张空白的Grafana面板，准备给团队讲解监控设计思路。**

**【第一轮：小胖开球——黑盒 vs 白盒】**

**小胖**（端着咖啡走到白板前）："大师，Harbor不就是个镜像仓库嘛，监控它干啥？我写个脚本每分钟curl一下登录接口，能通就行了呗——这跟'看看人还有没有呼吸'一个道理。"

**大师**（笑着摇头）："小胖，你说的这叫'黑盒监控'——只检查外部可用性，相当于你站在医院门口问'里面还有人活着吗'。但Harbor真正的风险在'白盒监控'——你得像ICU监护仪一样看到内部脏器（组件）的状态。"

"我给你画个真实案例：某天早晨8点，用户说push有点慢。你如果只有黑盒监控，只能回一句'我试了试登录没问题啊'。但如果有白盒监控，你能看到的是——"

```
黑盒视角：  Harbor Core返回200 ✓
白盒视角：  PostgreSQL连接池使用率 94% ⚠
            Redis内存使用率 87% ⚠
            JobService队列积压 134 个任务 🔴
            Registry API P99延迟 4.2s 🔴
-> 结论：PG连接即将耗尽，jobservice的GC任务阻塞了Registry IO
```

"Harbor暴露的Metrics来自三个'信号源'，像一个身体有三个检查窗口："

| 来源 | 端点路径 | 认证方式 | 关键指标 |
|------|---------|---------|---------|
| Harbor Core | `/metrics`（端口9090） | Basic Auth | API QPS、延迟直方图、认证成功率 |
| Registry | `/metrics`（端口5001） | 无认证 | Blob上传/下载字节数、HTTP状态码分布 |
| JobService | `/metrics`（端口8080） | 无认证 | 任务队列长度、Worker空闲率、任务重试次数 |

**技术映射**：Harbor Core使用Prometheus Go客户端库（`promhttp.Handler()`）暴露Metrics，格式为标准的Prometheus exposition format（纯文本，key-value pairs）。需要在`harbor.yml`中显式开启`metric.enabled: true`并执行`./prepare`重新生成nginx配置——因为Metrics路由需要在Nginx中代理转发。

**【第二轮：小白追问——RED和USE怎么选？】**

**小白**（在笔记本上飞快记录）："大师，我看Google SRE的书里提到RED方法（Rate/Errors/Duration）和USE方法（Utilization/Saturation/Errors），Harbor这么多指标，到底该用哪个方法论？"

**大师**："好问题！RED和USE是互补的——不是二选一，而是组合拳。"

"**RED方法**——面向**请求驱动型**资源（HTTP服务）："
| RED指标 | Harbor中的含义 | 关键Metrics | 示例告警 |
|---------|---------------|-------------|---------|
| Rate | Core API每秒请求数 | `harbor_core_http_requests_total` | QPS突然从200涨到5000 → 可能有脚本在暴力刷API |
| Errors | 5xx/4xx错误占比 | `http_requests_total{code=~"5.."}` | 5xx比例>5% → 后端PG/Redis可能异常 |
| Duration | API P99延迟 | `harbor_core_http_request_duration_seconds` | P99>2s → 慢查询堆积或GC影响 |

"**USE方法**——面向**资源型**组件（数据库/存储/CPU）："
| USE指标 | Harbor中的含义 | 关键Metrics | 示例告警 |
|---------|---------------|-------------|---------|
| Utilization | PG连接使用率 | `pg_stat_database_numbackends / max_connections` | >70% → 准备扩容连接池 |
| Saturation | JobService队列深度 | `harbor_jobservice_queue_length` | >100 → Worker处理不过来 |
| Errors | Registry Blob写入失败 | `registry_http_requests_total{code=~"5.."}` | 任何5xx → 存储后端异常 |

**小白**："那到底监控什么？总不能把所有指标都配上告警吧？"

**大师**："这就是'告警哲学'的关键——**告警只告'即将发生的问题'，不告'已经发生的结果'**。我给你一个黄金四件套："
1. **延迟（Latency）**→ P99 > 2s → 用户还没投诉，你先知道
2. **流量（Traffic）**→ QPS突变 → 可能是攻击或异常流量
3. **错误（Errors）**→ 5xx率 > 5% → 内部组件可能损坏
4. **饱和度（Saturation）**→ 连接池/队列 > 80% → 即将耗尽

"这四点简称'Google SRE黄金信号'。基于这四个信号，你可以回答80%的'出了什么问题'，而不需要上线查日志。"

**【第三轮：小胖再问——存储监控怎么做？】**

**小胖**："我们运维最怕磁盘满了——上个月半夜2点收到磁盘告警，起来一看Harbor已经不可写了。有没有办法提前知道啥时候会满？"

**大师**："存储监控有三层，一层比一层有价值："

"**第一层——物理磁盘（最粗糙）**：`node_filesystem_avail_bytes`，这是'还剩多少'。但它不知道是谁占的，也不知道增长速度。"

"**第二层——项目维度存储（更有用）**：Harbor Core暴露了`harbor_project_storage_usage_bytes`——告诉你'order-platform项目占了320GB，dev-testing项目占了180GB'。这样你可以找到'存储大户'，精准治理。对应的PromQL："
```promql
# Top 5 存储消耗项目
topk(5, sum(harbor_project_storage_usage_bytes) by (project_name))

# 存储增长率（GB/天），用于预测
predict_linear(
  sum(harbor_project_storage_usage_bytes)[7d], 
  7 * 86400
) / 1024 / 1024 / 1024
```

"**第三层——Blob去重率（最有深度）**：Harbor的一个核心理念是'内容寻址'——相同layer只存一份。但如果不监控去重率，哪天去重失效了（比如有人用不同压缩方式push了相同的layer），存储会翻倍增长。去重率计算公式："
```
去重率 = 1 - (registry实际磁盘占用 / 所有项目artifact报告的总大小)
```
"当去重率 < 50% 时就要警觉了——说明大量重复Blob被存储，可能是CI流水线的构建方式有问题。"

**小胖**："原来存储监控不只是'看看还剩多少'，还能知道谁在用、增长多快、有没有浪费……"

**【第四轮：小白深挖——高基数问题】**

**小白**："大师，我有个担忧——Harbor的Metrics如果包含`repository_name`、`tag`这种高基数label（几万种取值），Prometheus的内存会不会爆炸？"

**大师**（竖起大拇指）："这就是生产级监控要警惕的'高基数时间序列爆炸'问题。Harbor很幸运——它的Core Metrics **不包含** repository_name或tag这种label。Core的label是`method`、`path`（API路由模板，如`/api/v2.0/projects/:id/repositories`）、`code`——这些都是有限的枚举值。"

"但Registry的Metrics确实暴露了`repository` label——如果你有8000个仓库，每个仓库有10个tag，每个tag在Prometheus中都是一个独立的时间序列。8000个仓库 × 5个关键指标 = 40000条时间序列——还在Prometheus的舒适区（单实例建议<100万条）。"

"规避高基数的最佳实践是——**在Prometheus配置中使用`metric_relabel_configs`丢弃高基数label**："
```yaml
# prometheus.yml
scrape_configs:
  - job_name: 'harbor-registry'
    metric_relabel_configs:
      - source_labels: [repository]
        regex: '.+'          # 匹配所有带repository label的指标
        action: labeldrop    # 直接丢弃这个label
```

**技术映射**：Prometheus的TSDB（时间序列数据库）中，每条时间序列需要约3KB内存。100万条序列 ≈ 3GB内存。生产环境建议用`--storage.tsdb.retention.time=30d`控制历史数据保留时长。

---

## 3 项目实战

### 环境要求

| 组件 | 版本/规格 | 用途 |
|------|----------|------|
| Harbor | v2.10.0+ | Metrics暴露源 |
| Prometheus | v2.50.0+ | 指标采集与存储 |
| Grafana | v10.4+ (OSS) | 可视化大盘 |
| Alertmanager | v0.27+ | 告警路由与静默 |
| 操作系统 | Linux (Ubuntu 22.04) | 部署平台 |
| 网络 | Prometheus需与Harbor容器在同一Docker网络 | 或通过host网络 |
| 存储（Prometheus） | 50GB+ SSD | 30天数据保留 |

### 步骤一：配置Harbor暴露Metrics端点

**目标**：让Harbor的三个核心组件暴露Prometheus格式的指标。

```bash
cd /opt/harbor

# ===== 1. 编辑harbor.yml =====
# 确保以下配置块存在且正确
```

```yaml
# harbor.yml —— Metrics配置
metric:
  enabled: true
  port: 9090           # Core metrics端口
  path: /metrics       # Core metrics路径
```

```bash
# ===== 2. 验证配置语法 =====
python3 -c "
import yaml
with open('/opt/harbor/harbor.yml') as f:
    cfg = yaml.safe_load(f)
    print('metric.enabled:', cfg.get('metric', {}).get('enabled'))
    print('metric.port:', cfg.get('metric', {}).get('port'))
"
# 预期输出: metric.enabled: True, metric.port: 9090

# ===== 3. 重新生成配置并部署 =====
./prepare
# 观察输出中metrics相关日志
# 预期看到: "Generated /opt/harbor/common/config/nginx/conf.d/metrics.conf"

docker compose down
docker compose up -d

# 等待所有容器healthy（约30-60秒）
sleep 10
docker compose ps
# 预期所有Status列显示 "Up (healthy)"
```

```bash
# ===== 4. 验证三个Metrics端点 =====

# 测试Core Metrics（需要Basic Auth）
curl -s -u admin:Str0ng@Admin2024 \
    http://localhost:9090/metrics | head -30
# 预期输出示例：
# # HELP harbor_core_http_request_duration_seconds ...
# # TYPE harbor_core_http_request_duration_seconds histogram
# harbor_core_http_requests_total{code="200",method="GET",path="/api/v2.0/projects"} 12543
# ...

# 测试Registry Metrics（无需认证）
# Registry metrics默认在5001端口的/metrics路径
curl -s http://localhost:5001/metrics | head -20
# 预期输出：
# # HELP registry_http_requests_total ...
# registry_http_requests_total{code="200",method="GET"} 89234

# 测试JobService Metrics（端口8080）
curl -s http://localhost:8080/metrics | head -20
# 预期输出：
# # HELP harbor_jobservice_queue_length ...
# harbor_jobservice_queue_length 3
```

### 步骤二：部署Prometheus并配置抓取任务

**目标**：部署Prometheus，配置对Harbor三个组件的Metrics抓取。

```bash
# ===== 1. 创建Prometheus配置目录 =====
mkdir -p /opt/prometheus/data
chmod 777 /opt/prometheus/data   # 容器内nobody用户需要写权限

# ===== 2. 获取Harbor的Docker网络名称 =====
HARBOR_NET=$(docker network ls --filter name=harbor -q | head -1)
echo "Harbor网络ID: $HARBOR_NET"

# ===== 3. 编写prometheus.yml =====
cat > /opt/prometheus/prometheus.yml <<'PROMEOF'
global:
  scrape_interval: 15s       # 全局抓取间隔
  scrape_timeout: 10s        # 单次抓取超时
  evaluation_interval: 15s   # 告警规则评估间隔

# 告警规则文件（步骤四用到）
rule_files:
  - /etc/prometheus/alerting_rules.yml

scrape_configs:
  # ----- Harbor Core -----
  - job_name: 'harbor-core'
    scrape_interval: 15s
    basic_auth:
      username: 'admin'
      password: 'Str0ng@Admin2024'
    static_configs:
      - targets: ['harbor-core:9090']
    metric_relabel_configs:
      # 保留路径模板，丢弃高基数具体ID
      - source_labels: [path]
        regex: '/api/v2\.0/projects/\d+/repositories/.*'
        replacement: '/api/v2.0/projects/:id/repositories/:name'
        target_label: path_pattern
        action: replace

  # ----- Harbor Registry -----
  - job_name: 'harbor-registry'
    scrape_interval: 30s     # Registry指标较多，降低频率
    static_configs:
      - targets: ['registry:5001']
    metric_relabel_configs:
      # 丢弃repository label（高基数避免）
      - source_labels: [repository]
        regex: '.+'
        action: labeldrop

  # ----- Harbor JobService -----
  - job_name: 'harbor-jobservice'
    scrape_interval: 30s
    static_configs:
      - targets: ['harbor-jobservice:8080']

  # ----- 宿主机节点指标（可选，用于磁盘预测） -----
  - job_name: 'node'
    scrape_interval: 60s
    static_configs:
      - targets: ['host.docker.internal:9100']   # 需要先部署node_exporter
PROMEOF

# ===== 4. 编写告警规则文件（先创建占位） =====
cat > /opt/prometheus/alerting_rules.yml <<'RULESEOF'
groups:
  - name: harbor_alerts
    rules: []   # 步骤四中填充
RULESEOF

# ===== 5. 启动Prometheus容器 =====
docker run -d --name prometheus \
  --network harbor_harbor \
  -v /opt/prometheus/prometheus.yml:/etc/prometheus/prometheus.yml:ro \
  -v /opt/prometheus/alerting_rules.yml:/etc/prometheus/alerting_rules.yml:ro \
  -v /opt/prometheus/data:/prometheus \
  -p 9090:9090 \
  prom/prometheus:v2.50.0 \
  --storage.tsdb.retention.time=30d \
  --storage.tsdb.retention.size=45GB \
  --web.enable-lifecycle \
  --web.console.templates=/etc/prometheus/consoles

# 参数说明：
# --storage.tsdb.retention.time=30d: 保留30天数据
# --storage.tsdb.retention.size=45GB: 数据上限45GB
# --web.enable-lifecycle: 允许通过API热加载配置

# ===== 6. 验证Prometheus启动 =====
sleep 5
docker logs prometheus --tail 20
# 预期看到：Server is ready to receive web requests.

curl -s http://localhost:9090/api/v1/status/config | python3 -m json.tool | head -20

# ===== 7. 验证targets状态 =====
curl -s http://localhost:9090/api/v1/targets | python3 -c "
import sys, json
data = json.load(sys.stdin)
for t in data['data']['activeTargets']:
    health = '✓' if t['health'] == 'up' else '✗'
    print(f\"{health} {t['labels']['job']:25s} {t['scrapeUrl']}\")
"
# 预期输出：
# ✓ harbor-core               http://harbor-core:9090/metrics
# ✓ harbor-registry            http://registry:5001/metrics
# ✓ harbor-jobservice          http://harbor-jobservice:8080/metrics
```

### 步骤三：部署Grafana并配置Harbor大盘

**目标**：创建包含RED+USE指标的Grafana可视化大盘。

```bash
# ===== 1. 部署Grafana =====
docker run -d --name grafana \
  --network harbor_harbor \
  -v /opt/grafana/data:/var/lib/grafana \
  -p 3000:3000 \
  -e GF_SECURITY_ADMIN_PASSWORD=Grafana@2024 \
  -e GF_INSTALL_PLUGINS=grafana-piechart-panel \
  grafana/grafana:10.4.0

# ===== 2. 配置Prometheus数据源（通过API） =====
sleep 10  # 等待Grafana启动
curl -s -X POST http://admin:Grafana@2024@localhost:3000/api/datasources \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Prometheus",
    "type": "prometheus",
    "url": "http://prometheus:9090",
    "access": "proxy",
    "isDefault": true
  }' | python3 -m json.tool

# 预期输出包含: "message": "Datasource added"
```

```bash
# ===== 3. 通过API创建Grafana Dashboard =====
# 以下创建5个核心Panel的Dashboard
cat > /tmp/harbor_dashboard.json <<'DASHJSON'
{
  "dashboard": {
    "title": "Harbor 生产监控大盘",
    "panels": [
      {
        "id": 1,
        "title": "API QPS (按method+path)",
        "type": "graph",
        "targets": [{
          "expr": "sum(rate(harbor_core_http_requests_total[5m])) by (method, path)",
          "legendFormat": "{{method}} {{path}}"
        }],
        "gridPos": {"h": 8, "w": 12, "x": 0, "y": 0}
      },
      {
        "id": 2,
        "title": "API Error Rate (5xx %)",
        "type": "graph",
        "targets": [{
          "expr": "sum(rate(harbor_core_http_requests_total{code=~\"5..\"}[5m])) / sum(rate(harbor_core_http_requests_total[5m])) * 100",
          "legendFormat": "5xx Error %"
        }],
        "gridPos": {"h": 8, "w": 12, "x": 12, "y": 0},
        "thresholds": [{"value": 5, "color": "red"}]
      },
      {
        "id": 3,
        "title": "P99 API Latency (秒)",
        "type": "graph",
        "targets": [{
          "expr": "histogram_quantile(0.99, sum(rate(harbor_core_http_request_duration_seconds_bucket[5m])) by (le, path))",
          "legendFormat": "{{path}}"
        }],
        "gridPos": {"h": 8, "w": 12, "x": 0, "y": 8}
      },
      {
        "id": 4,
        "title": "存储使用量 Top 5 项目 (GB)",
        "type": "graph",
        "targets": [{
          "expr": "topk(5, sum(harbor_project_storage_usage_bytes) by (project_name)) / 1024 / 1024 / 1024",
          "legendFormat": "{{project_name}}"
        }],
        "gridPos": {"h": 8, "w": 12, "x": 12, "y": 8}
      },
      {
        "id": 5,
        "title": "Pull/Push 速率 (次/分钟)",
        "type": "graph",
        "targets": [{
          "expr": "sum(rate(registry_http_requests_total{action=~\"pull|push\"}[5m])) by (action)",
          "legendFormat": "{{action}}"
        }],
        "gridPos": {"h": 8, "w": 12, "x": 0, "y": 16}
      },
      {
        "id": 6,
        "title": "JobService队列积压",
        "type": "stat",
        "targets": [{
          "expr": "harbor_jobservice_queue_length"
        }],
        "gridPos": {"h": 4, "w": 6, "x": 12, "y": 16},
        "thresholds": [{"value": 50, "color": "yellow"}, {"value": 100, "color": "red"}]
      },
      {
        "id": 7,
        "title": "存储增长预测 (7天后预计GB)",
        "type": "stat",
        "targets": [{
          "expr": "predict_linear(sum(harbor_project_storage_usage_bytes)[7d], 7 * 86400) / 1024 / 1024 / 1024"
        }],
        "gridPos": {"h": 4, "w": 6, "x": 18, "y": 16}
      }
    ],
    "schemaVersion": 16,
    "time": {"from": "now-6h", "to": "now"},
    "refresh": "30s"
  },
  "overwrite": true
}
DASHJSON

# 导入Dashboard
curl -s -X POST http://admin:Grafana@2024@localhost:3000/api/dashboards/db \
  -H "Content-Type: application/json" \
  -d @/tmp/harbor_dashboard.json | python3 -m json.tool
# 预期输出包含: "status": "success"
```

```bash
# ===== 4. 网络可达性验证（从Prometheus容器内测试） =====
docker exec prometheus wget -qO- --header="Authorization: Basic $(echo -n 'admin:Str0ng@Admin2024' | base64)" \
    http://harbor-core:9090/metrics 2>&1 | head -5

# 或者用Prometheus UI手动验证：
echo "访问 http://localhost:9090/targets 查看所有targets状态"
echo "访问 http://localhost:3000 打开Grafana (admin / Grafana@2024)"
```

### 步骤四：配置分级告警规则与Alertmanager

**目标**：建立P0/P1/P2三级告警，通过Alertmanager路由到不同通知渠道。

```bash
# ===== 1. 编写告警规则文件 =====
cat > /opt/prometheus/alerting_rules.yml <<'RULESEOF'
groups:
  - name: harbor_critical
    rules:
      # P0——核心组件存活
      - alert: HarborCoreDown
        expr: up{job="harbor-core"} == 0
        for: 1m
        labels:
          severity: critical
          team: platform
        annotations:
          summary: "Harbor Core 停止响应"
          description: "Harbor Core ({{ $labels.instance }}) 已不可达超过1分钟"
          runbook_url: "https://wiki.company.com/harbor/core-down"

      # P0——5xx错误率飙升
      - alert: HarborHighErrorRate
        expr: |
          (
            sum(rate(harbor_core_http_requests_total{code=~"5.."}[5m])) 
            / 
            sum(rate(harbor_core_http_requests_total[5m]))
          ) > 0.1
        for: 5m
        labels:
          severity: critical
          team: platform
        annotations:
          summary: "Harbor 5xx错误率 > 10%"
          description: "当前5xx占比: {{ $value | humanizePercentage }}，请检查PostgreSQL和Redis状态"

  - name: harbor_warning
    rules:
      # P1——API延迟恶化
      - alert: HarborSlowAPI
        expr: |
          histogram_quantile(0.99, 
            sum(rate(harbor_core_http_request_duration_seconds_bucket[5m])) by (le)
          ) > 2
        for: 10m
        labels:
          severity: warning
          team: platform
        annotations:
          summary: "Harbor API P99延迟 > 2秒"
          description: "当前P99延迟: {{ $value }}秒，可能原因：PG慢查询/JVM GC/网络延迟"

      # P1——存储空间预警
      - alert: HarborStorageWarning
        expr: |
          predict_linear(
            sum(harbor_project_storage_usage_bytes)[7d], 
            7 * 86400
          ) / 1024 / 1024 / 1024 > 800
        for: 1h
        labels:
          severity: warning
          team: platform
        annotations:
          summary: "Harbor存储预计7天后超过800GB"
          description: "基于过去7天趋势，7天后将达到 {{ $value | humanize }}GB，请规划扩容"

      # P1——JobService积压
      - alert: HarborJobQueueBacklog
        expr: harbor_jobservice_queue_length > 100
        for: 10m
        labels:
          severity: warning
          team: platform
        annotations:
          summary: "JobService任务队列积压 > 100"
          description: "当前积压: {{ $value }}个任务，GC/复制/Vulnerability扫描可能受影响"

  - name: harbor_info
    rules:
      # P2——磁盘使用率（来自节点exporter，如果有）
      - alert: HarborDiskUsage
        expr: |
          (
            node_filesystem_avail_bytes{mountpoint="/data"} 
            / 
            node_filesystem_size_bytes{mountpoint="/data"}
          ) < 0.15
        for: 30m
        labels:
          severity: info
          team: platform
        annotations:
          summary: "Harbor数据磁盘可用空间 < 15%"
          description: "磁盘 {{ $labels.device }} 挂载点 {{ $labels.mountpoint }} 仅剩 {{ $value | humanizePercentage }}"
RULESEOF

# ===== 2. 重新加载Prometheus配置 =====
curl -X POST http://localhost:9090/-/reload
# 预期输出: "Lifecycle reload succeeded" (如果启用了--web.enable-lifecycle)
```

```bash
# ===== 3. 部署Alertmanager =====
cat > /opt/prometheus/alertmanager.yml <<'AMEOF'
global:
  resolve_timeout: 5m
  smtp_smarthost: 'smtp.company.com:587'
  smtp_from: 'harbor-alert@company.com'
  smtp_auth_username: 'harbor-alert@company.com'
  smtp_auth_password: 'SMTP_PASSWORD_HERE'

# 通知路由树
route:
  group_by: ['alertname', 'severity']
  group_wait: 30s
  group_interval: 5m
  repeat_interval: 4h
  receiver: 'default-email'
  routes:
    - match:
        severity: critical
      receiver: 'pagerduty-critical'
      continue: true
    - match:
        severity: warning
      receiver: 'slack-platform'

receivers:
  - name: 'default-email'
    email_configs:
      - to: 'platform-team@company.com'
        headers:
          Subject: '[Harbor监控] {{ .GroupLabels.alertname }}'

  - name: 'pagerduty-critical'
    # 生产环境使用PagerDuty/企业微信/钉钉等
    webhook_configs:
      - url: 'https://hooks.slack.com/services/T.../B.../xxxxx'
        send_resolved: true

  - name: 'slack-platform'
    slack_configs:
      - api_url: 'https://hooks.slack.com/services/T.../B.../xxxxx'
        channel: '#harbor-alerts'
        title: '{{ .GroupLabels.alertname }}'
        text: '{{ range .Alerts }}{{ .Annotations.summary }}\n{{ .Annotations.description }}\n{{ end }}'
AMEOF

docker run -d --name alertmanager \
  --network harbor_harbor \
  -v /opt/prometheus/alertmanager.yml:/etc/alertmanager/alertmanager.yml:ro \
  -p 9093:9093 \
  prom/alertmanager:v0.27.0

# 在prometheus.yml中添加Alertmanager地址后reload
# alerting:
#   alertmanagers:
#     - static_configs:
#         - targets: ['alertmanager:9093']
```

### 步骤五：PromQL 查询速查——常用排查query

**目标**：掌握日常故障排查中最常用的PromQL查询。

```bash
# ===== 实时排查常用查询 =====

# 1. 当前哪个API路径最慢？
curl -s 'http://localhost:9090/api/v1/query?query=topk(5,histogram_quantile(0.99,sum(rate(harbor_core_http_request_duration_seconds_bucket[5m]))by(le,path)))'

# 2. 过去1小时500错误的时序趋势
curl -s 'http://localhost:9090/api/v1/query_range?query=sum(rate(harbor_core_http_requests_total{code=~"5.."}[5m]))&start='$(date -d '1 hour ago' +%s)'&end='$(date +%s)'&step=60s'

# 3. 哪个项目存储增长最快？
curl -s 'http://localhost:9090/api/v1/query?query=topk(5,deriv(harbor_project_storage_usage_bytes[24h]))'

# 4. Registry Pull vs Push比例
curl -s 'http://localhost:9090/api/v1/query?query=sum(rate(registry_http_requests_total[5m]))by(action)'

# 5. API可用率（非5xx比例）
curl -s 'http://localhost:9090/api/v1/query?query=(1-(sum(rate(harbor_core_http_requests_total{code=~"5.."}[5m]))/sum(rate(harbor_core_http_requests_total[5m]))))*100'
```

### 常见坑与解决方案

| # | 坑 | 根因 | 解决 |
|---|-----|------|------|
| 1 | Core的Metrics端点返回404 | `harbor.yml`中`metric.enabled:true`但未执行`./prepare`——Nginx没有将/metrics路由代理到Core。 | 执行`./prepare && docker compose up -d`。用`docker exec harbor-nginx cat /etc/nginx/conf.d/metrics.conf`确认配置文件已生成。 |
| 2 | Basic Auth配置正确但Prometheus仍报401 | Prometheus的`basic_auth`必须写在与`static_configs`同级，写在`scrape_configs`下或`static_configs`内都会导致解析失败。 | 检查YAML缩进：`basic_auth`与`static_configs`同级在`job_name`下。正确格式参见步骤二中的prometheus.yml。 |
| 3 | Grafana面板显示"No data" | PromQL中label名与Harbor实际暴露的Metrics名不匹配。Harbor v2.8+中`harbor_core_`前缀是新增的，旧版无此前缀。 | 在Prometheus UI（http://localhost:9090）中先手动输入metric名，利用自动补全确认实际名称。或用`curl localhost:9090/api/v1/label/__name__/values`列出所有metric名。 |
| 4 | `harbor_project_storage_usage_bytes`始终为0 | 该指标来自Harbor的周期性配额计算任务，默认每24小时更新一次。刚部署的新Harbor可能尚未执行第一次计算。 | 手动触发计算：`docker exec harbor-core /harbor/harbor_core --mode=quota`或在Harbor Portal中查看系统管理→垃圾回收→立即计算。 |
| 5 | 多实例Prometheus重复告警 | 多个Prometheus实例配置了相同的告警规则但没有用Alertmanager去重。 | Alertmanager的`group_by`和`group_interval`可以合并相同告警。生产建议用Prometheus Federation模式——一个中心Prometheus从各Region的Prometheus聚合数据，只在中心配置告警。 |

---

## 4 项目总结

### 4.1 监控方法对比

| 维度 | 黑盒监控（Blackbox） | 白盒监控（Prometheus） | 组合效果 |
|------|---------------------|----------------------|---------|
| 覆盖范围 | 仅外部可用性 | 全部内部指标 | 360°无死角 |
| 故障发现速度 | 用户投诉后（被动） | 趋势异常即告警（主动） | 从45分钟→5分钟 |
| 故障定位精度 | "Harbor不可用" | "PG连接池85%，Core P99=3.2s" | 定位到具体组件 |
| 容量规划 | 无数据支撑 | 存储增长预测 | 提前4周预警 |
| 实施复杂度 | 低（脚本即可） | 中等（需部署3个组件） | 投入产出比极高 |

### 4.2 告警分级策略

| 级别 | 命名 | 通知方式 | 响应SLA | 示例 |
|------|------|---------|---------|------|
| P0 - Critical | HarborXxxCritical | PagerDuty + 电话 + 群通知 | 15分钟内响应 | Core Down、5xx率>10% |
| P1 - Warning | HarborXxxWarning | Slack频道 + 邮件 | 1小时内处理 | P99>2s、存储7天后满 |
| P2 - Info | HarborXxxInfo | 邮件摘要 | 下一个工作日 | 磁盘<20%、证书即将到期 |

### 4.3 适用场景

| 场景 | 说明 |
|------|------|
| 多团队共享Harbor（>5个团队） | 存储按项目拆分监控，做成本分摊 |
| Harbor承载CI/CD流水线 | JobService队列监控避免积压导致构建失败 |
| 需要SLA报告对外承诺 | Prometheus数据可直接生成月度可用率报告 |
| 多Region Harbor部署 | Federation模式统一监控，用一个Grafana看所有Region |
| 合规审计要求（金融/医疗） | 所有API访问有Metrics记录，审计时有数据可追溯 |

**不适用场景**：
- 个人开发者单机Harbor——维护Prometheus+Grafana的成本超过Harbor本身
- 已有成熟的全公司监控平台（如Datadog/NewRelic）——直接对接Harbor Metrics端点即可，无需重复部署

### 4.4 五项注意事项

1. **Prometheus数据存储需要SSD。** TSDB的写放大系数约为2-3倍，500个targets每天约产生15-20GB数据，HDD上查询延迟会达到秒级。
2. **Grafana的`refresh`不要设太短。** 生产Dashboard建议`30s`或`1m`刷新，`5s`刷新会显著增加Prometheus的查询负载。
3. **告警规则必须带`for`持续时间。** 没有`for`的告警会在单个采样点异常时立即触发——频繁的抖动告警（flapping）会导致运维告警疲劳。
4. **定期清理Prometheus的TSDB。** `--storage.tsdb.retention.time=30d`和`--storage.tsdb.retention.size=45GB`取先到者。监控`prometheus_tsdb_storage_blocks_bytes`确保磁盘不写满。
5. **Harbor升级后验证Metric名称。** Harbor大版本升级（如v2.8→v2.10）可能重命名或增删Metrics——升级后务必检查Grafana面板是否仍然有效。

### 4.5 常见故障速查

| 故障现象 | 根因 | 快速定位命令 |
|----------|------|-------------|
| Grafana显示"Prometheus datasource error" | Prometheus容器网络不可达 | `docker exec grafana wget -qO- http://prometheus:9090/-/healthy` |
| 告警规则评估返回空 | PromQL中label名有拼写错误 | 在Prometheus UI的Graph页面手动输入查询验证 |
| Alertmanager不发送通知 | `alertmanager.yml`中receiver配置有误 | `docker logs alertmanager --tail 50 \| grep -i "error\|fail"` |

### 4.6 深度思考

1. **当前Prometheus只监控了单个Harbor实例。如果公司有3个Region的Harbor，如何用Prometheus Federation模式实现多实例统一监控？Federation的`match[]`参数如何设计才能只拉取聚合指标而不拉取原始高基数数据？**（提示：Federation的`/federate?match[]={__name__=~"harbor_.*"}`可以全局规则过滤，在中心Prometheus只存储聚合后的指标）

2. **如果要求Harbor的API可用率达到99.9%（SLO），P99延迟 < 500ms。请用Prometheus的Recording Rules计算SLI（服务水平指标），并设计Error Budget消耗预警——当Error Budget消耗速度超过预期时提前告警。**（提示：SLI = sum(rate(requests_total{code!~"5.."}[28d])) / sum(rate(requests_total[28d]))，Error Budget = (1 - SLO) × total_requests，用burn rate监控消耗速度）

---

> 下一章预告：第28章将讲解Harbor在Kubernetes中的生产部署实践——Helm Chart定制化与运维。
