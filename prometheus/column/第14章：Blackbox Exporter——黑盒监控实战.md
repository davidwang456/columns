# 第14章：Blackbox Exporter——黑盒监控实战

## 一、项目背景

周二晚上11点，运维团队正在聚精会神地打《魔兽世界》团本。Prometheus面板上，所有Node Exporter指标一片绿色——CPU使用率35%，内存充裕，磁盘IO正常。一切看起来完美无缺。

然而，公司官网已经挂了整整40分钟。

起因是安全团队当天下午做了一次防火墙策略变更，误将外部HTTPS流量全部拦截。从服务器内部看，Nginx进程运行正常，80和443端口监听中，本地`curl localhost`返回200——Node Exporter忠实地报告着"一切正常"。但从外网访问的用户看到的却是连接超时。客服电话被打爆，运维却因为"监控一切绿色"而浑然不觉。直到产品总监一个电话打到值班手机："你们自己打开官网看看！"

这个故事的教训很朴素：**白盒监控告诉你系统是否健康，黑盒监控告诉你用户是否能用。**

白盒监控（Whitebox Monitoring）关注系统内部状态——CPU、内存、磁盘、进程、应用内部指标。它像体检报告，告诉你身体各项指标是否正常。黑盒监控（Blackbox Monitoring）从外部视角探测服务——HTTP请求能否返回200？TCP端口是否可达？DNS能否解析？它像问一个人"你还好吗"，看他能不能回答你。两者不是替代关系，而是互补关系。

Prometheus生态中的黑盒探针就是**Blackbox Exporter**。它是一个"代理探测器"——Prometheus告诉它"帮我探测这个地址"，它从自己所处的位置发起HTTP/TCP/DNS/ICMP/gRPC请求，并将结果返回给Prometheus。但随之而来是一系列实际问题：如何实现北京、上海、新加坡同时探测？SSL证书快过期了怎么自动发现？HTTP返回200就真的代表页面正常吗？如果返回的是错误页面也是200怎么办？本章将逐一解答。

## 二、剧本式交锋对话

**（小胖冲进大师的工位，屏幕上Prometheus面板一片绿色）**

**小胖：** 大师，出大事了！昨晚官网挂了快一小时我们才知道，可Prometheus一条告警都没触发。所有服务器的Node Exporter指标全是绿的。这监控到底有没有用啊？

**大师：** 小胖，我问你一个问题：你去医院体检，各项指标正常，就代表你今天能出门上班吗？

**小胖：** 这……体检报告正常当然能上班啊。等等，你的意思是——

**大师：** 我的意思是，体检报告只能告诉你"内部器官有没有问题"，但不能告诉你"这个人能不能走路"。你现在的监控做的就是"体检"——CPU、内存、磁盘全看了，唯独没做的事情是：**从用户的角度，真的去访问一下网站，看它能不能打开。**

**小白：**（端着咖啡走过来）大师说得对。Node Exporter监控的是白盒指标——服务器"内部"长什么样。但那个防火墙策略，是从外部把流量挡住了，服务器内部当然察觉不到。我们需要的是**黑盒监控**——从外部去探测服务是否可达。

**小胖：** 黑盒？这名字听着像飞机黑匣子……在Prometheus里怎么实现？

**大师：** Prometheus有个官方组件叫**Blackbox Exporter**，专门干这件事。它的工作原理很巧妙——Blackbox Exporter自己不去主动探测，而是等Prometheus来请求它。流程是这样的：

1. Prometheus按`scrape_interval`向Blackbox Exporter发起HTTP请求
2. 请求里带上参数，告诉Blackbox："请帮我去探测 `www.baidu.com`，用HTTP模块"
3. Blackbox Exporter收到指令后，代执行探测——发起HTTP请求、建立TCP连接、做DNS解析
4. 探测完成后，Blackbox把结果（状态码、耗时、SSL证书信息等）返回给Prometheus

**小白：** 所以Blackbox Exporter本质上是一个"探测代理"。核心优势是——它从Prometheus所在的位置进行探测。如果你在三个地域各部署一套Prometheus+Blackbox Exporter，就能实现**多地域探测对比**。

**小胖：** 原来如此！那Blackbox都能探测什么？

**大师：** 它支持通过**模块化配置**定义多种探针：

- **HTTP探针**：发起HTTP/HTTPS请求，检查状态码、响应体内容、SSL证书
- **TCP探针**：检查TCP端口是否可达——比如MySQL的3306、Redis的6379
- **DNS探针**：检查DNS解析是否正常，支持A/AAAA/SOA/MX等记录类型
- **ICMP探针**：类似ping，检查网络可达性
- **gRPC探针**：对gRPC服务发起健康检查

每种探针用一个`module`来定义。Prometheus采集时通过`__param_module`参数指定用哪个模块。

**小胖：** 等等，你刚才说HTTP探针还能检查响应体内容？

**大师：** 对，这正是回答你"状态码200就够了吗"这个问题的关键。假设你的登录页面挂了，但Nginx返回了一个通用的200错误页面——这时候状态码检查就失效了。Blackbox支持**响应体正则匹配**：配置`fail_if_body_not_matches_regexp`，让探测器去验证页面里是否包含预期的关键词，比如`"login"`。这样即便状态码是200，只要页面上没有"login"字样，探测就会标记为失败。

**小白：** 还有一个容易被忽略的场景——**SSL证书过期监控**。证书过期意味着HTTPS握手失败，网站在用户端直接不可访问，而且恢复时间长（涉及证书申请、审批、部署）。Blackbox的HTTP探针可以配置`fail_if_not_ssl: true`，并返回`probe_ssl_earliest_cert_expiry`指标——证书的Unix过期时间戳。你用`(probe_ssl_earliest_cert_expiry - time()) / 86400`就能算出还剩多少天。

**小胖：** 明白了！白盒是体检报告，黑盒是"喂，你还好吗？"两者各司其职。那具体怎么配置呢？

**大师：** 好问题。来，我直接带你实战一遍。

## 三、项目实战

### 环境准备

在已有的Docker Compose环境中添加Blackbox Exporter。确保你已有一个可探测的HTTP服务（使用Nginx或任意Web应用）。

### 步骤1：Blackbox Exporter配置与启动

修改`docker-compose.yml`，添加Blackbox Exporter服务：

```yaml
blackbox-exporter:
  image: prom/blackbox-exporter:latest
  ports:
    - "9115:9115"
  volumes:
    - ./blackbox.yml:/etc/blackbox_exporter/config.yml
  command:
    - '--config.file=/etc/blackbox_exporter/config.yml'
```

创建`blackbox.yml`配置文件：

```yaml
modules:
  http_2xx:
    prober: http
    timeout: 5s
    http:
      valid_status_codes: [200, 301, 302]
      method: GET
      no_follow_redirects: false
      fail_if_ssl: false
      fail_if_not_ssl: false
      tls_config:
        insecure_skip_verify: false

  http_post_2xx:
    prober: http
    timeout: 5s
    http:
      method: POST
      headers:
        Content-Type: application/json
      body: '{"test": "probe"}'
      valid_status_codes: [200]

  tcp_connect:
    prober: tcp
    timeout: 5s

  icmp:
    prober: icmp
    timeout: 5s

  dns_soa:
    prober: dns
    dns:
      query_name: "."
      query_type: "SOA"

  http_certificate:
    prober: http
    timeout: 5s
    http:
      method: GET
      fail_if_not_ssl: true
      tls_config:
        insecure_skip_verify: false
```

逐行解释每个module的用途：
- **`http_2xx`**：通用HTTP检查。验证状态码是否在200/301/302范围内，是日常外网探测最常用的模块。自动跟随重定向，不强制要求HTTPS
- **`http_post_2xx`**：POST请求检查。适用于API端点健康检查，可以携带请求头和请求体，验证API是否正常响应
- **`tcp_connect`**：TCP端口连通性。用于监控数据库、缓存等非HTTP服务的端口可达性
- **`icmp`**：ICMP Ping探测。注意：Docker中运行需要`NET_RAW`能力或root权限，否则会报"socket: operation not permitted"
- **`dns_soa`**：DNS SOA记录查询。用于验证DNS服务器是否正常工作
- **`http_certificate`**：SSL证书探测。设置`fail_if_not_ssl: true`后，非HTTPS目标会直接失败。配合`tls_config`校验证书有效性

### 步骤2：配置Prometheus采集Blackbox Exporter

在`prometheus.yml`中添加采集任务：

```yaml
scrape_configs:
  - job_name: 'blackbox-http'
    metrics_path: /probe
    params:
      module: [http_2xx]
    static_configs:
      - targets:
          - https://www.google.com
          - https://github.com
          - http://localhost:8080
    relabel_configs:
      - source_labels: [__address__]
        target_label: __param_target
      - source_labels: [__param_target]
        target_label: instance
      - target_label: __address__
        replacement: blackbox-exporter:9115
```

三条relabel_configs的作用是理解Blackbox采集机制的关键：

1. **`__address__` → `__param_target`**：将targets列表中的目标地址赋值给HTTP查询参数`target`。当Prometheus请求Blackbox Exporter时，URL变成`/probe?module=http_2xx&target=https://www.google.com`，这样Blackbox才知道要探测哪个地址
2. **`__param_target` → `instance`**：将探测目标地址保存为`instance`标签，保留在最终指标中。如果不做这一步，所有指标的`instance`标签都会是`blackbox-exporter:9115`，无法区分不同目标的探测结果
3. **`__address__` → `blackbox-exporter:9115`**：将Prometheus实际发起HTTP请求的地址重写为Blackbox Exporter的服务地址。因为Prometheus本来要去连接targets里的地址，但那些是我们"要探测的目标"，不是"要采集指标的目标"

### 步骤3：验证HTTP探针结果

启动容器后，在Prometheus Web UI（`http://localhost:9090`）中查询以下核心指标：

| 指标名 | 含义 | 示例值 |
|---|---|---|
| `probe_success` | 探测是否成功（0或1） | `1` |
| `probe_http_status_code` | HTTP响应状态码 | `200` |
| `probe_http_duration_seconds` | 请求总耗时（含连接/TLS/处理各阶段） | `0.35` |
| `probe_ssl_earliest_cert_expiry` | SSL证书最早过期时间的Unix时间戳 | `1735689600` |
| `probe_dns_lookup_time_seconds` | DNS解析耗时 | `0.005` |
| `probe_http_ssl` | 是否使用了HTTPS | `1` |

**`probe_success`是最重要的指标**——它是对整个探测结果的二元判断（成功=1，失败=0）。对HTTP探针而言，状态码在`valid_status_codes`范围内、响应体验证通过（如有配置）、SSL验证通过（如开启）等条件全部满足，`probe_success`才等于1。

针对`probe_success`创建基础告警规则：

```yaml
- alert: WebsiteDown
  expr: probe_success{job="blackbox-http"} == 0
  for: 2m
  labels:
    severity: critical
  annotations:
    summary: "网站 {{ $labels.instance }} 不可达"
    description: "Blackbox探测 {{ $labels.instance }} 已持续2分钟失败，请立即排查"
```

`for: 2m`的作用是**防抖**——避免网络瞬时抖动造成误报。

### 步骤4：SSL证书过期监控

随着HTTPS全面普及，证书过期等于网站不可用。而且证书过期恢复时间远长于服务重启——你需要申请证书、通过验证、下载部署，短则几十分钟，长则以天计。

查询当前证书剩余天数：

```promql
(probe_ssl_earliest_cert_expiry - time()) / 86400
```

这个表达式将Unix时间戳的差值转换为天数。例如返回`28.5`表示还有约28天半过期。

创建告警规则：

```yaml
- alert: SSLCertificateExpiring
  expr: (probe_ssl_earliest_cert_expiry - time()) / 86400 < 30
  for: 1h
  labels:
    severity: warning
  annotations:
    summary: "{{ $labels.instance }} SSL证书将在 {{ $value | printf \"%.0f\" }} 天后过期"
    description: "请在证书过期前完成续期和部署，避免HTTPS服务中断"
```

可以梯度设置告警级别：30天`warning`、14天`critical`，给运维留足处理时间。

### 步骤5：TCP和DNS探测配置

除了HTTP，TCP端口探测是第二常用的场景。监控数据库和缓存服务的端口可达性：

```yaml
- job_name: 'blackbox-tcp'
  metrics_path: /probe
  params:
    module: [tcp_connect]
  static_configs:
    - targets:
        - 'mysql-server:3306'
        - 'redis-server:6379'
        - 'kafka-broker:9092'
    labels:
      group: 'infrastructure'
  relabel_configs:
    - source_labels: [__address__]
      target_label: __param_target
    - source_labels: [__param_target]
      target_label: instance
    - target_label: __address__
      replacement: blackbox-exporter:9115
```

验证命令：

```promql
probe_success{job="blackbox-tcp"}
```

类似地，DNS探测可以验证DNS服务器是否正常响应：

```yaml
- job_name: 'blackbox-dns'
  metrics_path: /probe
  params:
    module: [dns_soa]
  static_configs:
    - targets:
        - '8.8.8.8'
        - '1.1.1.1'
  relabel_configs:
    - source_labels: [__address__]
      target_label: __param_target
    - source_labels: [__param_target]
      target_label: instance
    - target_label: __address__
      replacement: blackbox-exporter:9115
```

### 可能遇到的坑

**1. ICMP探测需要特殊权限**

ICMP使用原始套接字，普通Docker容器默认没有权限。报错信息为：`"msg":"Error listening to socket","err":"listen ip4:icmp 0.0.0.0: socket: operation not permitted"`。解决方案：在`docker-compose.yml`中添加`cap_add: [NET_RAW]`，或用host网络模式。

**2. HTTP响应体正则不要过于严格**

配置`fail_if_body_matches_regexp`或`fail_if_body_not_matches_regexp`时，正则过于严格会导致页面微调后误报警。建议用核心关键词（如`"login"`、`"首页"`）而非完整的HTML片段。

**3. timeout的层级关系**

Blackbox Exporter的`timeout` < Prometheus的`scrape_timeout` < `scrape_interval`。如果Blackbox的超时大于Prometheus的采集超时，Prometheus会在Blackbox返回结果之前断开连接，导致采集失败。通常建议：Blackbox timeout设为5s，Prometheus `scrape_timeout`设为10s。

**4. 多地域探测的架构**

单一探测点的问题是：探测点本身网络异常会导致所有目标都显示不可达。多地域探测的标准方案是：每个地域部署一套独立的Prometheus + Blackbox Exporter，然后通过Thanos或Prometheus Federation将各地域的`probe_success`指标聚合到一个中央Prometheus。在Grafana中按地域维度做面板分组展示。

### 测试验证

验证采集配置是否生效：

```bash
curl -s http://localhost:9090/api/v1/targets | jq '.data.activeTargets[] | select(.labels.job | startswith("blackbox")) | {labels: .labels, health: .health}'
```

预期输出所有blackbox任务的健康状态为`"up"`。

验证探测逻辑：故意停掉一个被测服务：

```bash
docker stop nginx-test
```

观察`probe_success`从1变为0的时间应为：`for: 2m`（防抖窗口）+ `scrape_interval`（采集间隔），通常3分钟内触发告警。

验证SSL证书过期告警：使用一个证书有效期较短的测试域名，或在Grafana中查询`(probe_ssl_earliest_cert_expiry - time()) / 86400`确认剩余天数计算正确。

## 四、项目总结

### 黑盒 vs 白盒监控对比

| 维度 | 黑盒监控 | 白盒监控 |
|---|---|---|
| **视角** | 外部用户视角 | 内部系统视角 |
| **核心问题** | "服务能用吗？" | "系统健康吗？" |
| **典型指标** | 可达性、响应时间、状态码、SSL证书 | CPU、内存、磁盘、QPS、错误率 |
| **实现方式** | Blackbox Exporter、Ping、外部拨测 | Node Exporter、应用内Metrics端点 |
| **适用场景** | 外网可达性、API端点检查、SSL管理 | 资源监控、性能分析、容量规划 |
| **局限** | 无法定位根因（只知道"不通"，不知道"为什么不通"） | 看不到外部可达性问题（防火墙、DNS、CDN故障） |
| **类比** | 看一个人有没有在呼吸 | 给一个人做全身体检 |

### 探测类型速查

| 探测类型 | Prober | 核心参数 | 核心指标 |
|---|---|---|---|
| HTTP | `http` | `valid_status_codes`、`method`、`fail_if_ssl`、`fail_if_body_not_matches_regexp` | `probe_http_status_code`、`probe_http_duration_seconds`、`probe_ssl_earliest_cert_expiry` |
| TCP | `tcp` | 无额外参数，仅检查三次握手 | `probe_success`、`probe_duration_seconds` |
| DNS | `dns` | `query_name`、`query_type` | `probe_dns_lookup_time_seconds`、`probe_success` |
| ICMP | `icmp` | 无额外参数（需root/NET_RAW） | `probe_success`、`probe_icmp_duration_seconds` |
| gRPC | `grpc` | `service`、`tls` | `probe_success`、`probe_grpc_health_check_response` |

### 适用场景

1. **外网可达性监控**：从公网探测公司官网、API网关、CDN节点是否可达
2. **API健康检查**：POST请求验证关键API端点，确保核心业务流程可用
3. **SSL证书生命周期管理**：自动化监控所有HTTPS站点的证书过期时间，提前30天告警
4. **第三方依赖监控**：探测支付网关、短信平台等外部API的运行状态
5. **基础设施端口监控**：确保MySQL、Redis、Kafka等关键服务的TCP端口可达

### 核心注意事项

- **探测频率控制**：不要对第三方站点设置过高的探测频率（建议不低于30s），避免被目标IP封禁
- **防抖设置**：`probe_success == 0`告警必须加`for`参数（建议2-5分钟），过滤网络瞬时抖动
- **Blackbox自身监控**：Blackbox Exporter挂了，所有探测都停了。加一条`up{job="blackbox-http"} == 0`告警
- **模块合理拆分**：不要在一个module里混用HTTP和TCP逻辑。不同探测类型用独立module管理

### 常见踩坑经验

**案例一：国外站点超时误报警。** 团队监控了`github.com`和`stackoverflow.com`，每到晚高峰跨国链路质量下降时就收到大量告警。解决方案：将国外站点的`timeout`从5s调高到15s，并在告警规则中对已知不稳定目标设置更长的`for`窗口（10分钟），或干脆对跨国目标使用部署在海外节点的Blackbox Exporter。

**案例二：TLS证书校验失败后滥用`insecure_skip_verify`。** 内部服务的自签名证书导致`probe_success=0`，有人直接在blackbox.yml中把`insecure_skip_verify`设为`true`。这等于关闭了所有SSL验证，连证书过期都不会被发现。正确做法：将内部CA证书挂载到Blackbox容器中，通过`tls_config.ca_file`指定。

**案例三：ICMP在容器中报"operation not permitted"。** Kubernetes中部署Blackbox Exporter做了ICMP探测，Pod一直报权限错误。除了加`NET_RAW` capability外，注意部分云厂商的托管Kubernetes（如GKE Autopilot）不允许修改Pod安全上下文，此时ICMP探测需要改用其他方案（如TCP探测替代）。

### 思考题

1. **如何实现"不仅检查HTTP 200，还要验证响应体中包含'login'字样"？**  
   提示：在`http_2xx`模块的HTTP配置中添加`fail_if_body_not_matches_regexp`字段，设置正则表达式匹配预期的页面内容。

2. **如果有北京/上海/新加坡三个地域的探测点，如何在Grafana中对比展示？**  
   提示：每个地域部署独立的Prometheus + Blackbox Exporter，通过`external_labels`区分地域。使用Prometheus Federation或Thanos聚合，在Grafana中以`region`为维度对`probe_success`和`probe_http_duration_seconds`做面板分组，直观对比各区域的探测结果差异。
