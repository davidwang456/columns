# 第30章：可观测性——Prometheus Grafana Loki 监控

## 1 项目背景

某在线教育平台的Keycloak集群已经上线稳定运行了两个季度，20万师生每天通过它登录教学系统、提交作业、查看成绩。一切看似风平浪静，直到一个周三的凌晨。

深夜2点，监控室一片寂静——准确的说是根本没有监控室。凌晨2:17，所有四个Keycloak节点的CPU同时飙升到100%，JVM Full GC每隔8秒触发一次，内存堆被吃满，数据库连接池从平均40个活跃连接飙升到200上限。这是因为某黑客团伙对登录接口发起了一次精心策划的撞库攻击——使用从暗网购买的数百万条邮箱密码组合，以每分钟30000次的速率撞库。更致命的是，Keycloak默认的暴力破解防护（Brute Force Detection）在这一波攻击面前显得杯水车薪——攻击者使用了数万个不同IP（分布在全球100+国家的僵尸网络），每个IP的登录失败次数远低于Keycloak的检测阈值，而分布式IP的登录请求又难以被通用的IP限流拦截。

然而，直到第二天早上9点，运维团队上班后才从用户投诉中得知昨夜系统完全瘫痪。排查时发现三个致命问题：第一，Docker容器启动时未配置日志驱动和限制，`/var/log/keycloak/`目录下的日志文件被Docker JSON File Driver自动滚动到`/var/lib/docker/containers/*.log`，超出100MB后自动覆盖——凌晨2点到4点那段黄金排查窗口的日志早已被覆盖殆尽。第二，没有任何外部监控系统，Keycloak自带的`/metrics`端点虽然暴露了JVM、HTTP连接数、缓存命中率等指标，但没人去抓取、存储、可视化。第三，没有告警——CPU 100%跑了4个小时，无人知晓。

更可怕的是安全层面。两天后安全团队在例行审计中发现——上周五深夜23:47，一个学生账号从美国得克萨斯州IP（该学生位于北京）使用全新Chrome浏览器成功登录，随后下载了全部教学资料和包含上万条学生家庭住址的班级通讯录。没有异常登录告警、没有异地IP通知、没有设备指纹比对——这个高危登录事件如果当时被发现，损失本可避免。

CTO在紧急会议上怒问："我们的系统是在裸奔吗？没有监控等于盲飞，我给你一周时间建好完整的可观测性体系——指标、日志、告警、可视化大盘，缺一不可。"

痛点总结：缺少实时指标监控（登录QPS、认证成功率、Token签发量、JVM状态），缺少自动化告警（CPU、内存、连接池、登录失败率异常时无人知晓），日志非结构化（关键的`userId`、`ipAddress`、`eventType`深藏在纯文本中，无法快速过滤和聚合），无可视化看板（当CEO在电梯里问"系统健康状况怎么样"时，唯一能回答的只有一句"看起来还好吧"），且Keycloak原生的`/metrics`端点暴露的指标数量庞大、格式为Prometheus text format，但缺乏指标过滤和聚合能力，直接抓取会导致存储成本爆炸。

---

## 2 项目设计——剧本式交锋对话

**小胖**（抱着一袋薯片坐到白板前）：大师，我想到一个比喻——开车啊！油量表、速度表、水温表、故障灯，缺一个就是在盲开。Keycloak不是自带`/health`端点吗？返回个UP不就行了，为啥还要Prometheus、Grafana、Loki这么一堆东西？

**大师**（把小胖的薯片挪到一边）：你这比喻对了一半。我问你，车上的仪表盘只会告诉你"发动机在转"还是"发动机已熄火"？当然不是——它告诉你当前转速、水温、油压、瞬时油耗。Keycloak的`/health`端点只是告诉你"车没熄火"，但车正在以6000转高转速行驶、水温接近沸点、油箱只剩5%——这些信息`/health`一概不知。Prometheus就是你的仪表盘面板，Grafana就是仪表盘的外观皮肤和布局，Loki就是行车记录仪——记录一路上发生了什么。

> **大师技术映射**：仪表盘（油量/速度/水温/故障灯）→ Four Golden Signals（延迟/流量/错误/饱和度）。`/health`端点 → 发动机启动指示灯，只亮灭。Prometheus/Grafana → 全数字仪表盘，实时显示多维时序数据。Loki → 行车记录仪，回溯事件细节。

---

**小白**（翻开笔记本）：大师，我看了Keycloak的官方文档，它暴露了三个健康检查端点——`/health/ready`、`/health/live`、`/health/started`。它们到底有什么区别？各自应该挂到哪个监控配置上？

**大师**（在白板上画出一个节点的启动生命周期）：

```
节点启动 → JVM初始化 → DB连接池建立 → 缓存预热 → 启动完成
           ↑              ↑              ↑          ↑
    /health/live    不可用     不可用     /health/started
    返回200                             返回200
                        /health/ready 返回200
```

- `/health/live`（存活探针）：只检查JVM进程是否还活着——JVM没OOM没卡死就返回200。这是最轻量的检查，K8s的`livenessProbe`挂在这上面。如果这个端口超时，K8s会杀死Pod重启。**它不保证节点能处理请求**——数据库连接断了它照样返回200。
- `/health/ready`（就绪探针）：检查节点是否**完全具备处理请求的能力**——数据库连接通畅、Infinispan缓存已初始化、所有必需服务已启动。只有返回200时，负载均衡器才将流量路由到该节点。K8s的`readinessProbe`和Nginx的`health_check`应该挂在这个端点上。
- `/health/started`（启动探针）：只在启动阶段有意义——标记节点是否已完成完整的启动流程（包括缓存预热）。K8s的`startupProbe`挂在这个端点，防止启动缓慢的Pod被`livenessProbe`误杀。

**小白**：那`/metrics`端点呢？我试着`curl localhost:8080/metrics`，返回了一大堆Prometheus格式的数据，`vendor_statistics_`开头的指标尤其多——它们分别代表什么？

**大师**（在终端前敲出请求输出）：Keycloak的`/metrics`端点暴露了三类指标：

| 指标前缀 | 来源 | 示例 |
|---------|------|------|
| `vendor_statistics_*` | Keycloak内部统计 | `vendor_statistics_sessions_current`、`vendor_statistics_login_total`、`vendor_statistics_cache_miss_total` |
| `jvm_*` | JVM运行时 | `jvm_memory_used_bytes`、`jvm_gc_pause_seconds` |
| `system_cpu_*` / `process_*` | 系统/进程级 | `system_cpu_usage`、`process_open_fds` |

其中`vendor_statistics_*`是Keycloak的核心业务指标，值得深入理解：

- `vendor_statistics_sessions_current`：当前活跃用户会话总数（所有Realm汇总）
- `vendor_statistics_login_total{outcome="success|error"}`：登录事件总数，按成功/失败分类
- `vendor_statistics_refresh_token_total`：Refresh Token签发总数
- `vendor_statistics_cache_{hit|miss}_total{cache="sessions|realms|users|..."}`：按缓存域统计命中/未命中次数
- `vendor_statistics_db_connections_{active|idle|waiting}`：数据库连接池状态

这些指标的关键特征是——它们带有高基数标签。比如`vendor_statistics_login_total`可能带`{realm="student", client_id="web-app", outcome="error"}`标签，三个维度的组合就是数千条时间序列。不当过滤会导致Prometheus的存储膨胀到告警。

> **大师技术映射**：`/health/ready` → 餐厅后厨检查"灶台有火、食材齐备、厨师在岗"。`/health/live` → 只确认"厨房灯还亮着"。`/metrics` → 餐厅的营业日报——今天来了多少客人、翻台率多少、哪道菜被退回最多（登录失败）。

---

**小胖**（举手打断）：等一下，我有个更直接的问题。Prometheus的pull模型——它每隔15秒去Keycloak的`/metrics`端点拉一次数据，那如果15秒内有3000次登录，这些登录的`userId`、`ipAddress`等详细信息岂不是全部丢失了？

**大师**：精准的问题。这恰好暴露了**Metrics和Logs的边界**。Prometheus不做事件级记录——它只记录聚合后的计数器和摘要。`vendor_statistics_login_total{outcome="error"}`告诉你最近15秒内有87次登录失败，但不告诉你这87次分别是谁、从哪个IP来的。这些事件级信息在哪里？在**日志**里。Keycloak的Event系统会将每次登录事件以JSON格式写入日志：`{"timestamp":"...","type":"LOGIN_ERROR","userId":"xxx","ipAddress":"1.2.3.4","error":"invalid_user_credentials"}`——这就是Loki和Promtail的用武之地，它们负责把这些JSON日志汇集成可搜索、可聚合的时序日志流。

**小白**：那RED方法（Rate/Error/Duration）怎么在Keycloak中落地？

**大师**：RED方法是Google SRE提出的监控黄金法则，映射到Keycloak上：

- **Rate（速率）**：`rate(vendor_statistics_login_total[5m])`——每5分钟的平均登录QPS。正常基线可能200/s，如果突然降到10/s说明服务出问题；如果飙升到5000/s可能是攻击。
- **Error（错误率）**：`rate(vendor_statistics_login_total{outcome="error"}[5m]) / rate(vendor_statistics_login_total[5m])`——登录失败占比。正常应该<5%，超过10%就要告警。
- **Duration（耗时）**：Token签发耗时是Keycloak核心延迟指标。Prometheus的Histogram类型指标`vendor_statistics_token_issuance_duration_seconds`记录了不同速度的Token签发次数分布，通过`histogram_quantile(0.99, ...)`计算出P99延迟。

这套RED指标组成一个简洁但覆盖全面的监控三角——速率告诉你"用户用得多不多"，错误率告诉你"用户用成没成"，延迟告诉你"用户等得久不久"。

> **大师技术映射**：Metrics vs Logs → 超市的实时客流计数器（每15秒告诉你进来了多少人，但不记录每个人是谁）vs 超市的监控录像（可以回放寻找某个特定顾客的行为）。RED → 餐厅运营三指标——翻台率（Rate）、退单率（Error）、上菜时间（Duration）。

---

**小胖**（咬着笔帽琢磨了一会儿）：大师，我怎么感觉这套监控搞起来会很烦——一天收几百条告警，全是"数据库连接池使用率超过80%"、"CPU短暂飙到85%又降下来"这种没意义的噪音。过了一周大家就会把所有告警静音。

**大师**（表情严肃）：你戳中了可观测性领域最大的痛点——**告警疲劳**。Google SRE书籍里有一个经典结论：**告警应该是对即将发生的用户痛苦的可操作预判，而不是对任何偏离正常状态的自动通知**。翻译成人话——CPU偶尔从40%跳到80%不是告警，用户收到的502错误才是告警。

**小白**：那SLO怎么设定？没有SLO的告警都是耍流氓对吧？

**大师**（画了一个SLO金字塔）：SLO（Service Level Objective）是告警的锚点。我们这样给Keycloak设定：

```
SLO金字塔 (自上而下)
═══════════════════════════════
SLI（指标）          SLO（目标值）       告警阈值
───────────────────────────────────────
登录成功率           99.5%/月           <99% → Critical
Token签发P99延迟     <1秒              >3秒 → Warning  
节点可用性（up）      99.9%/月           <99.9% → Critical
浏览器端登录完成时间   <3秒 (P95)        >5秒 → Warning
```

SLO的核心思想是——**设定业务真实需要的可用性目标**，而不是追求不切实际的100%。99.5%的登录成功率意味着每月最多允许0.5%的登录失败，即每月21.6分钟的服务降级时间——这是按月统计的误差预算（Error Budget）。当错误预算被快速消耗时（比如一天内用掉了月预算的80%），才触发告警。这样一来，偶尔的短暂波动不会触发骚扰，只有真正的大规模故障才会拉响警报。

**On-call流程**同样关键：收到告警 → 责任工程师5分钟内确认（Acknowledge）→ 15分钟内开始排查 → 如需升级，呼叫二级值班。告警必须配Runbook——一个标准化的排查操作手册，比如"收到HighLoginFailureRate告警后：1）登录Grafana查看登录失败大盘，确定是哪个Realm/Client出现异常；2）在Loki中查询该时间段登录失败日志，确认错误类型（密码错误/用户不存在/账号锁定）；3）如果是密码错误暴增，查看攻击来源IP分布，决定是否封禁IP段或启用CAPTCHA级别提升。"

> **大师技术映射**：告警疲劳 → 狼来了的故事，喊多了没人信。SLO/Error Budget → 每个月的"故障预算"，像家庭财务——偶尔下馆子多花50块没关系，但三天花光了半个月工资就要报警。On-call + Runbook → 消防队的出勤流程——接到火警、穿上装备、按标准流程灭火，而不是到了现场才想"灭火器在哪"。

---

## 3 项目实战

### 环境准备

- Docker Desktop 或 Docker Engine 24+
- Docker Compose v2.x
- 至少8GB可用内存（Prometheus + Grafana + Loki + Keycloak 四件套）
- 端口规划：9090（Prometheus）、3000（Grafana）、3100（Loki）、8080（Keycloak）

---

### 步骤1：Docker Compose 监控栈编排

创建 `docker-compose-monitoring.yml`，一次编排四个核心服务：

```yaml
# docker-compose-monitoring.yml
services:
  postgres:
    image: postgres:16
    container_name: kc-postgres
    environment:
      POSTGRES_DB: keycloak
      POSTGRES_USER: keycloak
      POSTGRES_PASSWORD: keycloak123
    ports:
      - "5432:5432"
    volumes:
      - pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U keycloak"]
      interval: 10s
      timeout: 5s
      retries: 5

  keycloak:
    image: quay.io/keycloak/keycloak:26.1
    container_name: keycloak
    command: start
    environment:
      KC_DB: postgres
      KC_DB_URL_HOST: postgres
      KC_DB_URL_PORT: 5432
      KC_DB_URL_DATABASE: keycloak
      KC_DB_USERNAME: keycloak
      KC_DB_PASSWORD: keycloak123
      KC_BOOTSTRAP_ADMIN_USERNAME: admin
      KC_BOOTSTRAP_ADMIN_PASSWORD: admin
      KC_HOSTNAME: localhost
      KC_HEALTH_ENABLED: "true"
      KC_METRICS_ENABLED: "true"
      KC_LOG: console
      KC_LOG_CONSOLE_OUTPUT: json
    depends_on:
      postgres:
        condition: service_healthy
    ports:
      - "8080:8080"
    volumes:
      - kc_logs:/opt/keycloak/data/log

  prometheus:
    image: prom/prometheus:v2.52.0
    container_name: prometheus
    ports:
      - "9090:9090"
    volumes:
      - ./prometheus.yml:/etc/prometheus/prometheus.yml:ro
      - ./prometheus-alerts.yml:/etc/prometheus/alerts.yml:ro
      - prometheus_data:/prometheus
    command:
      - '--config.file=/etc/prometheus/prometheus.yml'
      - '--storage.tsdb.retention.time=30d'

  grafana:
    image: grafana/grafana:11.0.0
    container_name: grafana
    ports:
      - "3000:3000"
    environment:
      GF_SECURITY_ADMIN_PASSWORD: admin
      GF_INSTALL_PLUGINS: ""
    volumes:
      - ./grafana-datasources.yml:/etc/grafana/provisioning/datasources/datasources.yml:ro
      - grafana_data:/var/lib/grafana

  loki:
    image: grafana/loki:3.1.0
    container_name: loki
    ports:
      - "3100:3100"
    command: -config.file=/etc/loki/local-config.yaml

  promtail:
    image: grafana/promtail:3.1.0
    container_name: promtail
    volumes:
      - ./promtail-config.yml:/etc/promtail/config.yml:ro
      - kc_logs:/var/log/keycloak:ro
    command: -config.file=/etc/promtail/config.yml
    depends_on:
      - loki

volumes:
  pgdata:
  prometheus_data:
  grafana_data:
  kc_logs:
```

**关键说明**：

- `KC_METRICS_ENABLED: "true"`：打开Keycloak的`/metrics`端点，默认监听在`:8080/metrics`。
- `KC_LOG_CONSOLE_OUTPUT: json`：将日志输出格式设为JSON结构化格式。这是Loki日志解析的关键前提——非JSON格式的日志需要正则表达式解析，维护成本高且容易出错。
- `KC_HEALTH_ENABLED: "true"`：同时打开三个健康检查端点。
- Prometheus的数据保留期设为30天（`--storage.tsdb.retention.time=30d`），根据磁盘空间可调整。

---

### 步骤2：Prometheus 抓取配置与指标过滤

创建 `prometheus.yml`：

```yaml
# prometheus.yml
global:
  scrape_interval: 15s
  evaluation_interval: 15s
  external_labels:
    cluster: 'keycloak-prod'
    env: 'production'

alerting:
  alertmanagers:
    - static_configs:
        - targets: ['alertmanager:9093']
rule_files:
  - '/etc/prometheus/alerts.yml'

scrape_configs:
  - job_name: 'keycloak'
    metrics_path: '/metrics'
    scheme: 'http'
    static_configs:
      - targets: ['keycloak:8080']
        labels:
          app: 'keycloak'
          instance: 'keycloak-node-1'
    scrape_timeout: 10s
    metric_relabel_configs:
      # 保留Keycloak核心业务指标
      - source_labels: [__name__]
        regex: 'vendor_statistics_(sessions|login|refresh_token).*'
        action: keep
      # 保留数据库连接池指标
      - source_labels: [__name__]
        regex: 'vendor_statistics_db_connections.*'
        action: keep
      # 保留JVM核心指标（内存、GC、线程）
      - source_labels: [__name__]
        regex: '(jvm_memory_used_bytes|jvm_memory_max_bytes|jvm_gc_pause_seconds_.*|jvm_threads_.*)'
        action: keep
      # 保留系统指标
      - source_labels: [__name__]
        regex: '(system_cpu_usage|process_cpu_usage|process_open_fds|process_max_fds)'
        action: keep
      # 保留up指标（节点存活探测）
      - source_labels: [__name__]
        regex: 'up'
        action: keep
```

**指标过滤的必要性**：Keycloak 26.1的`/metrics`端点暴露了超过800条指标。如果不加`metric_relabel_configs`过滤，每个Keycloak节点每次抓取会产生约1-2MB数据。以3节点集群、15秒抓取间隔计算，一天的数据量约为：`3 × (86400/15) × 1.5MB ≈ 26GB/天`，30天就是780GB——对于中小团队来说存储成本太高。通过白名单过滤核心指标后，每次抓取数据量降到约50KB，30天数据量约2.6GB。

**注意**：如果你需要按Realm维度查看指标，不要用`metric_relabel_configs`的`action: keep`彻底删除其他指标，而是使用`action: labeldrop`只删除高基数标签（如`address`、`client_id`），保留指标本体。

---

### 步骤3：Grafana 数据源配置

创建 `grafana-datasources.yml`：

```yaml
# grafana-datasources.yml
apiVersion: 1

datasources:
  - name: Prometheus
    type: prometheus
    access: proxy
    url: http://prometheus:9090
    isDefault: true
    editable: false

  - name: Loki
    type: loki
    access: proxy
    url: http://loki:3100
    editable: false
```

启动后，在Grafana UI中手动创建三大看板。以下给出每个看板的核心面板PromQL和LogQL查询：

**登录监控大盘**：

| 面板类型 | 面板名称 | PromQL/LogQL | 阈值 |
|---------|---------|-------------|------|
| Stat | 当前活跃会话总数 | `vendor_statistics_sessions_current` | — |
| Graph | 登录QPS | `rate(vendor_statistics_login_total[1m])` | — |
| Graph | 登录成功率 | `sum(rate(vendor_statistics_login_total{outcome="success"}[5m])) / sum(rate(vendor_statistics_login_total[5m]))` | <90%红色 |
| Graph | 登录失败按错误类型 | `sum by(error) (rate(vendor_statistics_login_total{outcome="error"}[5m]))` | — |
| Graph | Token签发速率 | `rate(vendor_statistics_refresh_token_total[5m])` | — |
| Stat | 总登录失败数(24h) | `sum(increase(vendor_statistics_login_total{outcome="error"}[24h]))` | >1000黄色 |

**性能大盘**：

```promql
# Token签发P50/P90/P99延迟
histogram_quantile(0.50, rate(vendor_statistics_token_issuance_duration_seconds_bucket[5m]))
histogram_quantile(0.90, rate(vendor_statistics_token_issuance_duration_seconds_bucket[5m]))
histogram_quantile(0.99, rate(vendor_statistics_token_issuance_duration_seconds_bucket[5m]))

# 缓存命中率（按缓存域）
sum by(cache) (rate(vendor_statistics_cache_hit_total[5m]))
/ sum by(cache) (rate(vendor_statistics_cache_hit_total[5m]) + rate(vendor_statistics_cache_miss_total[5m]))

# 数据库连接池状态
vendor_statistics_db_connections_active
vendor_statistics_db_connections_idle
vendor_statistics_db_connections_waiting

# JVM GC频率与暂停时间
rate(jvm_gc_pause_seconds_count[5m])
histogram_quantile(0.99, rate(jvm_gc_pause_seconds_bucket[5m]))

# JVM堆内存使用率
jvm_memory_used_bytes{area="heap"} / jvm_memory_max_bytes{area="heap"}
```

**基础设施大盘**：

```promql
# 节点存活状态
up{job="keycloak"}

# CPU使用率
system_cpu_usage * 100

# 打开的文件描述符数量（接近上限可能导致连接失败）
process_open_fds / process_max_fds
```

---

### 步骤4：配置告警规则

创建 `prometheus-alerts.yml`：

```yaml
# prometheus-alerts.yml
groups:
  - name: keycloak-critical
    rules:
      - alert: KeycloakNodeDown
        expr: up{job="keycloak"} == 0
        for: 1m
        labels:
          severity: critical
          team: platform
        annotations:
          summary: "Keycloak节点 {{ $labels.instance }} 宕机"
          description: "节点 {{ $labels.instance }} 超过1分钟未响应。请立即检查Docker容器状态和节点网络。"
          runbook_url: "https://wiki.internal/runbooks/keycloak-node-down"

      - alert: HighLoginFailureRate
        expr: |
          (
            sum(rate(vendor_statistics_login_total{outcome="error"}[5m]))
            /
            sum(rate(vendor_statistics_login_total[5m]))
          ) > 0.1
        for: 5m
        labels:
          severity: critical
          team: platform
        annotations:
          summary: "登录失败率超过10%"
          description: "近5分钟登录失败率为 {{ $value | humanizePercentage }}，总失败次数 {{ $labels.total_errors }}。可能原因：撞库攻击/密码策略变更/LDAP上游故障。"
          runbook_url: "https://wiki.internal/runbooks/high-login-failure"

      - alert: TokenIssuanceLatencyHigh
        expr: |
          histogram_quantile(0.99,
            rate(vendor_statistics_token_issuance_duration_seconds_bucket[5m])
          ) > 3
        for: 5m
        labels:
          severity: warning
          team: platform
        annotations:
          summary: "Token签发P99延迟超过3秒"
          description: "P99 Token签发延迟为 {{ $value }}s，可能原因：数据库慢查询/缓存未命中率高/JVM GC频繁。"
          runbook_url: "https://wiki.internal/runbooks/token-latency-high"

  - name: keycloak-warning
    rules:
      - alert: DBConnectionPoolExhausted
        expr: |
          vendor_statistics_db_connections_active
          / vendor_statistics_db_connections_max > 0.9
        for: 5m
        labels:
          severity: warning
          team: platform
        annotations:
          summary: "数据库连接池使用率超过90%"
          description: "连接池使用率为 {{ $value | humanizePercentage }}（活跃{{ $labels.active }}/最大{{ $labels.max }}）。"
          runbook_url: "https://wiki.internal/runbooks/db-connection-pool"

      - alert: JVMMemoryUsageHigh
        expr: |
          jvm_memory_used_bytes{area="heap"}
          / jvm_memory_max_bytes{area="heap"} > 0.9
        for: 10m
        labels:
          severity: warning
          team: platform
        annotations:
          summary: "JVM堆内存使用率超过90%"
          description: "堆内存使用率为 {{ $value | humanizePercentage }}。持续高水位可能导致频繁Full GC。"
          runbook_url: "https://wiki.internal/runbooks/jvm-memory-high"

      - alert: PrometheusScrapeFailure
        expr: up{job="keycloak"} == 0
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "Prometheus抓取Keycloak指标失败"
          description: "无法从 {{ $labels.instance }} 抓取指标。可能原因：/metrics端点未启用或节点无响应。"

  - name: keycloak-slo-burn
    rules:
      - alert: ErrorBudgetBurnFast
        expr: |
          (
            sum(increase(vendor_statistics_login_total{outcome="error"}[1h]))
            /
            sum(increase(vendor_statistics_login_total[1h]))
          ) > 0.005  # 1小时内消耗了月度SLO（99.5%）的误差预算
        for: 10m
        labels:
          severity: critical
          team: platform
        annotations:
          summary: "登录SLO误差预算快速消耗"
          description: "过去1小时内登录失败率已达月度SLO预算上限。请立即排查原因。"
```

---

### 步骤5：Loki日志聚合与慢请求分析

创建 `promtail-config.yml`：

```yaml
# promtail-config.yml
server:
  http_listen_port: 9080
  grpc_listen_port: 0

positions:
  filename: /tmp/positions.yaml

clients:
  - url: http://loki:3100/loki/api/v1/push

scrape_configs:
  - job_name: keycloak-logs
    static_configs:
      - targets:
          - localhost
        labels:
          job: keycloak
          app: keycloak
          __path__: /var/log/keycloak/*.log

    pipeline_stages:
      - json:
          expressions:
            timestamp: timestamp
            sequence: sequence
            loggerClassName: loggerClassName
            loggerName: loggerName
            level: level
            message: message
            threadName: threadName
            threadId: threadId
            ndc: ndc
            hostName: hostName
            processName: processName
            processId: processId
      - labels:
          level: level
          loggerName: loggerName
      - timestamp:
          source: timestamp
          format: RFC3339
      - output:
          source: message
```

**日志结构解析**：Keycloak的Console JSON日志包含丰富的字段。Promtail的`pipeline_stages`先通过`json`阶段解析每个字段，然后将`level`和`loggerName`提取为Loki标签（低基数标签，适合做索引），最后通过`output`将`message`字段设为日志正文，方便Grafana中搜索。

在Grafana Explore中使用LogQL查询：

```logql
# 查找响应时间超过5秒的慢请求
{job="keycloak"} |~ "response_time.*[5-9][0-9]{3,}" | json

# 查找过去1小时内所有失败的登录事件
{job="keycloak", loggerName="org.keycloak.events"}
  |= "LOGIN_ERROR"
  | json
  | line_format "用户 {{.userId}} 在 {{.ipAddress}} 使用 {{.clientId}} 登录失败，原因: {{.error}}"

# 统计过去30分钟内各IP的登录失败次数
sum by(ipAddress) (count_over_time(
  {job="keycloak", loggerName="org.keycloak.events"}
  |= "LOGIN_ERROR"
  | json
  | ipAddress != ""
  [30m]
))

# 查找特定时间段内特定用户的全部登录活动
{job="keycloak", loggerName="org.keycloak.events"}
  |= "LOGIN"
  | json
  | userId = "a1b2c3d4-5678-90ab-cdef-1234567890ab"
```

---

### 步骤6：接入告警通知（Alertmanager → 钉钉 Webhook）

如果你的团队使用钉钉，创建 `alertmanager.yml`：

```yaml
# alertmanager.yml
global:
  resolve_timeout: 5m

route:
  group_by: ['alertname', 'severity']
  group_wait: 30s
  group_interval: 5m
  repeat_interval: 4h
  receiver: 'dingtalk-default'

receivers:
  - name: 'dingtalk-default'
    webhook_configs:
      - url: 'https://oapi.dingtalk.com/robot/send?access_token=YOUR_TOKEN'
        send_resolved: true
        http_config:
          follow_redirects: true
        message: |
          {
            "msgtype": "markdown",
            "markdown": {
              "title": "Keycloak告警 - {{ .GroupLabels.alertname }}",
              "text": "### {{ .GroupLabels.severity }} {{ .GroupLabels.alertname }}\n{{ range .Alerts }}\n- **摘要**: {{ .Annotations.summary }}\n- **详情**: {{ .Annotations.description }}\n- **开始时间**: {{ .StartsAt }}\n{{ end }}"
            }
          }
```

**告警抑制规则**：配置`inhibit_rules`防止告警风暴——例如当整个节点宕机时，抑制来自该节点的数据库连接池告警和JVM内存告警（因为节点宕机是根因，其他告警只是表象）：

```yaml
inhibit_rules:
  - source_match:
      alertname: 'KeycloakNodeDown'
    target_match_re:
      alertname: 'DBConnectionPoolExhausted|JVMMemoryUsageHigh|TokenIssuanceLatencyHigh'
    equal: ['instance']
```

---

### 可能遇到的坑

**坑1：Keycloak /metrics 端点返回401 Unauthorized。**
Keycloak 26.x版本中`/metrics`端点默认无需认证，但如果你配置了`KC_HTTP_RELATIVE_PATH=/auth`或在反向代理后添加了全局认证，`/metrics`可能被拦截。验证方式：`curl -v http://keycloak:8080/metrics`，如果返回401，需在Keycloak管理控制台中将`/metrics`路径从安全约束中排除。

**坑2：指标基数爆炸。**
Keycloak缓存命中率指标`vendor_statistics_cache_hit_total`带有`{cache="sessions|realms|users|authorization|keys|work"}`和`{realm="..."}`标签。在SaaS多租户环境下，如果有200个Realm，仅缓存指标就能产生200×6=1200条时间序列。不建议在`metric_relabel_configs`中直接删除这些指标，而是使用`aggregation`规则（Recording Rules）在Prometheus端提前预聚合，例如：

```yaml
# recording-rules.yml
groups:
  - name: keycloak_aggregations
    rules:
      - record: keycloak:cache_hit_rate:by_cache
        expr: |
          sum by(cache) (rate(vendor_statistics_cache_hit_total[5m]))
          /
          sum by(cache) (rate(vendor_statistics_cache_hit_total[5m]) + rate(vendor_statistics_cache_miss_total[5m]))
```

然后在Grafana中使用预聚合指标`keycloak:cache_hit_rate:by_cache`而非原始指标，大幅降低查询负载。

**坑3：Loki高基数标签。**
Promtail的`pipeline_stages`中只应将低基数字段（如`level`、`loggerName`）提取为Loki标签。**千万不要将`userId`、`ipAddress`、`sessionId`设为Loki标签**——Loki的索引基于标签组合，高基数标签会导致索引膨胀乃至宕机。这些字段应留在日志正文中，通过LogQL的`| json`解析后再用`| userId = "xxx"`进行过滤。

**坑4：时间不一致。**
Prometheus和Loki存储的日志时间戳可能存在偏移。Keycloak的JSON日志中的`timestamp`字段是UTC时间（如ISO 8601格式），Prometheus使用Unix时间戳。Promtail的`timestamp`阶段正确解析了RFC3339格式后，两者在Grafana中可以通过统一的时间范围选择器对齐。

---

### 测试验证

1. **启动监控栈**：
   ```bash
   docker compose -f docker-compose-monitoring.yml up -d
   ```

2. **验证 Keycloak 指标端点**：
   ```bash
   curl -s http://localhost:8080/metrics | grep "vendor_statistics_login_total"
   # 预期返回类似：vendor_statistics_login_total{outcome="success",} 0.0
   ```

3. **验证 Prometheus 抓取**：访问 `http://localhost:9090/targets`，确认 keycloak job 状态为 `UP`。

4. **模拟登录流量并触发告警**：使用k6或简单的bash脚本模拟登录失败请求：
   ```bash
   # 连续发送20次错误密码登录，拉高失败率
   for i in {1..20}; do
     curl -s -X POST http://localhost:8080/realms/master/protocol/openid-connect/token \
       -d "client_id=admin-cli" \
       -d "username=admin" \
       -d "password=wrongpassword" \
       -d "grant_type=password"
   done
   ```

5. **验证Grafana大盘**：访问 `http://localhost:3000`（用户名/密码：admin/admin），导入Prometheus数据源，查看登录监控大盘中失败率曲线上升。

6. **验证Loki日志**：在Grafana Explore中选择Loki数据源，执行查询 `{job="keycloak"} |= "LOGIN_ERROR"`，确认能看到刚才模拟的登录失败事件。

7. **验证告警**：访问 `http://localhost:9090/alerts`，等待5分钟后应看到 `HighLoginFailureRate` 告警状态变为 `FIRING`。

---

## 4 项目总结

### 优点 & 缺点对比

| 维度 | Prometheus + Grafana + Loki | ELK (Elasticsearch + Logstash + Kibana) | 商业APM（Datadog/New Relic） |
|------|---------------------------|----------------------------------------|---------------------------|
| **部署成本** | 免费开源，Docker Compose一键启动 | 免费开源，但ES需要较大内存（建议16GB+） | 按主机或数据量收费，月费用可达数千美元 |
| **Metrics能力** | Prometheus是时序数据库标杆，PromQL强大 | ES非专用时序库，聚合查询性能较差 | APM专用，开箱即用的分布式追踪 |
| **日志能力** | Loki轻量，存储成本低（仅索引标签） | ES日志全文索引，功能最全但存储成本高 | 商业方案日志分析最完善 |
| **学习曲线** | 中（PromQL + LogQL需要学习） | 高（ES DSL + Logstash配置 + Kibana） | 低（SaaS产品，开箱即用） |
| **社区生态** | CNCF毕业项目，K8s原生集成 | 社区庞大但版本碎片化严重 | 商业支持完善，7×24技术响应 |
| **高基数标签** | Prometheus弱，Loki禁 | ES支持，但有性能代价 | 商业方案有优化 |

### 适用场景

- ✅ **生产级Keycloak运维监控**：实时跟踪登录QPS、成功率、Token签发延迟，快速发现异常。
- ✅ **容量规划**：通过JVM堆内存趋势、数据库连接池使用率、CPU负载的历史数据预测扩容时机。
- ✅ **故障根因定位**：Loki日志全文搜索 + Prometheus瞬时指标快照，串起"什么时间-什么指标异常-什么日志记录"的完整证据链。
- ✅ **SLA/SLI合规审计**：Prometheus Recording Rules按月统计登录成功率、服务可用性，向上级/客户出具SLA报告。
- ✅ **安全事件检测**：通过Loki LogQL实时监控异常登录模式（异地IP、深夜登录、新设备），结合Alertmanager告警通知安全团队。

- ❌ **全链路分布式追踪**：本章未涉及Jaeger，Keycloak本身不产生OpenTelemetry Trace数据，如需Token全链路追踪需自定义SPI（另见后续章节）。
- ❌ **日志长期归档（>1年）**：Loki适合短期（30-90天）的运维日志搜索，如需长期审计存档，应考虑将日志导出至S3/对象存储。

### 注意事项

1. **指标保留策略**：Prometheus默认TSDB保留15天。生产环境建议30-90天，在`--storage.tsdb.retention.time=30d`和磁盘成本间权衡。使用Thanos或VictoriaMetrics可突破单机TSDB的存储上限。
2. **告警阈值需根据实际业务调整**：本章给出的10%登录失败率阈值适用于教育平台，金融或医疗场景可能需要更敏感（3-5%）。阈值应基于历史基线（如过去30天的P95值×1.5）动态计算。
3. **Dashboard需要定期维护**：Keycloak版本升级可能引入新的指标前缀或废弃旧指标（如`vendor_statistics_`可能变为`keycloak_statistics_`），每次升级后需验证Dashboard查询是否仍然有效。
4. **Grafana权限管理**：生产环境中不要使用默认的`admin/admin`凭据，应配置OAuth2（如GitHub/GitLab OAuth或Keycloak本身的OIDC）做SSO登录，并按团队角色分配Editor/Viewer权限。

### 常见踩坑经验

- **Prometheus scrape超时导致数据缺失**：Keycloak在高负载时`/metrics`端点的响应可能超过10秒。解决方法：增大`scrape_timeout`到30秒，或将`scrape_interval`从15秒放宽到30秒。
- **Loki "too many outstanding requests"错误**：当LogQL查询扫描日志量过大时，Loki会拒绝查询。优化方法：缩小时间范围、添加更多精确的标签过滤（如先限定`loggerName`再全文搜索）、增加Loki的`query_timeout`和`max_entries_limit_per_query`参数。
- **Grafana中Prometheus与Loki时间不一致**：如果Keycloak容器和Loki/Prometheus容器之间有时区偏差，Grafana的时间轴会出现错位。统一方案：所有Docker容器使用`TZ=Asia/Shanghai`环境变量，并在Grafana中设置默认时区。

### 思考题

1. **SaaS多租户监控挑战**：如果需要在监控平台上同时监控100个Keycloak实例（每个客户一个独立实例），Prometheus的存储和查询性能会成为瓶颈。请思考：使用Prometheus Federation分层架构（每个实例的Prometheus做本地聚合，上层Global Prometheus做跨实例查询）与使用Thanos/Cortex做全局视图，各自有什么优劣？如何设计一套Recording Rules避免在全局Prometheus中产生指标基数爆炸？

2. **安全事件关联分析**：Keycloak的登录事件既在Prometheus中聚合为指标（如"每5分钟登录失败次数"），又在Loki中以结构化日志形式保留事件详情（如"用户A从IP B在时间C登录失败，原因是密码错误"）。如果团队的安全运营中心（SOC）使用的SIEM系统是Splunk/ELK，如何将Keycloak的审计日志实时推送到SIEM并实现安全事件关联分析（例如：检测到同一IP在5分钟内攻击了10个不同账号 → 触发暴力破解告警）？尝试设计一条从Keycloak → Kafka → SIEM的日志传输管道。

---

**（第30章完）**
