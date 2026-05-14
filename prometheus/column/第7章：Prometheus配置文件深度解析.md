# 第7章：Prometheus配置文件深度解析

## 一、项目背景

运维团队最近踩了不少坑。同事小王给Prometheus加了一个新的采集任务，`reload`之后盯着监控大盘等了五分钟——毛都没有。检查了网络、检查了exporter状态，一切正常。最后在`scrape_configs`里逐行排查，发现`metrics_path`写成了`/metric`，少了一个`s`。Prometheus确实去采集了，只是路径不对，exporter返回了404，Prometheus也不报错，只在`up`指标里默默记了个0。

另一个同事配置`basic_auth`连接需要认证的exporter，`promtool check config`直接报错说YAML解析失败。调试了一个下午，才发现密码里有个`@`字符，YAML把它当成了特殊符号。一行配置，三种痛苦。

最惨的一次是生产事故。有人在`global`块里把`scrape_interval`设成了`1s`，心想"这是全局默认值，每个job应该可以覆盖吧"。结果所有job都继承了这个`1s`，Prometheus每秒向几十个目标发起采集，内存瞬间飙升——OOM了。事故复盘时才发现，全组对`global`与`job`级别的配置继承关系理解有偏差。

`prometheus.yml`看似简单——几百行YAML，几个配置块——实则布满陷阱。YAML缩进错误能把整个配置块静默吞掉。`scrape_timeout`和`scrape_interval`之间存在约束关系但配置检查时并不强制报错。`external_labels`会影响跨实例联邦查询的结果去重。`rule_files`的路径是相对于配置文件所在目录而不是Prometheus的工作目录。`alerting`块中手动配置`static_configs`和用`service discovery`发现Alertmanager，两种方式各有各的坑。

本章逐行拆解`prometheus.yml`，从`global`到`scrape_configs`到`rule_files`到`alerting`，把每个字段的含义、默认值、约束关系和常见踩坑点全部梳理清楚。读完这一章，你不仅能写出经得起`promtool`检验的生产级配置，还能在同事问"为什么监控没数据"时，第一时间想到去检查`metrics_path`。

## 二、剧本式交锋对话

**小胖**（挠头）：大师，昨天给Prometheus加了个新job，`reload`之后就是没数据。我检查了exporter是活的，网络也没问题，targets页面显示"UP"，但死活没有自定义指标，求解！

**大师**：targets页面点了UP进去看过吗？

**小胖**：没……就看了一眼状态是绿色的。

**大师**：点进去，看Scrape细节。`metrics_path`是什么？

**小胖**：哦！等等——是`/metric`，应该是`/metrics`，少了个s……

**大师**：对吧。Prometheus只管采集，路径错了exporter返回404，它不会报错，只是`up`指标为0或者压根没有你期望的指标。这种"静默失败"很常见。另外检查一下你job里的`scrape_interval`和`scrape_timeout`，timeout必须小于interval，否则每次采集都会超时断开。

**小白**：说到这个，我看到`global`里也配了`scrape_interval`，job里也配了，到底哪个生效？

**大师**：问得好。`global`不是传统意义的"全局默认"，而是**可以被scrape_config中同名配置覆盖的默认值**。你在job级别写了`scrape_interval: 5s`，就以5s为准；没写，就继承`global`的值。`evaluation_interval`是另一条时间线——它控制alerting/recording rules的评估频率，和scrape完全独立。一个管采集，一个管告警评估，别搞混。

**小胖**：那`external_labels`呢？我看global里还有个这玩意儿。

**大师**：`external_labels`会在Prometheus写入远程存储或进行联邦查询时，给所有时序数据打上额外的标签。比如你有一个北京集群和一个上海集群，各自部署了一套Prometheus。北京的配`external_labels: {datacenter: beijing}`，上海的配`datacenter: shanghai`。这样Thanos或联邦查询时，方知道这条数据来自哪个机房。但要注意——`external_labels`的label名不能和exporter本身暴露的label冲突，否则会出问题。

**小白**：我在scrape_configs里还看到`honor_labels`和`honor_timestamps`，这俩是啥？

**大师**：`honor_labels`决定要不要"尊重"exporter自己给出的标签。默认是`false`，意味着如果exporter的数据里带了`job`这个label，Prometheus会用自己的`job_name`覆盖它。设为`true`就是听exporter的。`honor_timestamps`同理——是否采纳exporter数据自带的时间戳。在推(push)场景下配合Pushgateway使用时，这些字段才真正派上用场。大多数场景保持默认就好。

**小胖**（翻笔记）：还有认证这块，`basic_auth`、`bearer_token`、`oauth2`，三选一？

**大师**：是的。最常用的是`basic_auth`，配`username`和`password`。如果密码含特殊字符（`@`、`#`、`:`等），用单引号包裹，或者用`password_file`指向一个文件，避免YAML解析问题。`bearer_token`适合API token场景，直接写token或用`bearer_token_file`。`oauth2`是最完整的认证方案，配置`client_id`、`client_secret`、`token_url`，Prometheus会自己刷新token。如果exporter跑在HTTPS上且是自签名证书，还需要配`tls_config`——测试环境可以用`insecure_skip_verify: true`快速验证，但**生产环境一定要配`ca_file`或系统信任的证书**。

**小白**：rule_files支持glob模式是吗？路径是相对于哪儿的？

**大师**：对的，可以写`rule_files: ['rules/*.yml', 'alerts/*.yml']`自动匹配目录下所有yml文件。路径是相对于`prometheus.yml`所在的目录，不是你启动Prometheus时的工作目录——这个经常踩坑。alerting块里配置Alertmanager也一样，`static_configs`手动写死地址最简单，但如果你有多个Alertmanager实例，可以用`service_discovery`来动态发现。

## 三、项目实战

### 环境准备

- Prometheus 2.55+ 已安装并运行
- `promtool`命令行工具（随Prometheus安装自带，执行`promtool --version`确认）
- 需要Basic Auth的测试exporter（可使用nginx做auth_proxy模拟）
- 一个自签名证书的HTTPS exporter（可选，用于TLS配置测试）

### 步骤1：从最小配置开始

创建`prometheus.yml`，从最精简的配置开始，逐行理解：

```yaml
global:
  scrape_interval: 15s
  evaluation_interval: 15s
  external_labels:
    datacenter: beijing

scrape_configs:
  - job_name: 'prometheus'
    static_configs:
      - targets: ['localhost:9090']
```

逐行解释：

- `global.scrape_interval: 15s`：所有scrape job的默认采集间隔。job级别不覆盖时，每15秒采集一次。
- `global.evaluation_interval: 15s`：alerting/recording rules的评估间隔。**和采集是两条独立的时间线**——就算你每秒采集一次，rule还是每15秒评估一次，除非在job级别也改了。
- `external_labels`：给所有从此Prometheus出去的数据打上`datacenter=beijing`标签，用于联邦和远程写入场景的数据区分。
- `scrape_configs`是一个数组，每个元素定义一个采集job。这里只有一个job，采集Prometheus自己的`/metrics`端点。
- `static_configs.targets`：手动指定采集目标列表，`localhost:9090`就是Prometheus自身的地址。

为什么`global`里把`scrape_interval`和`evaluation_interval`都设成了15s？这是官方建议的初始值——采集频率和告警评估频率一致，既不会漏掉数据变化，也不会过于频繁造成资源浪费。

### 步骤2：多Job配置与字段全解

现在扩展配置，增加两个不同频率的采集job，展示job级别的配置覆盖：

```yaml
global:
  scrape_interval: 15s
  evaluation_interval: 15s
  external_labels:
    datacenter: beijing
    cluster: prod

scrape_configs:
  - job_name: 'prometheus'
    static_configs:
      - targets: ['localhost:9090']

  - job_name: 'high-freq-service'
    scrape_interval: 5s
    scrape_timeout: 4s
    metrics_path: '/custom/metrics'
    scheme: http
    params:
      module: ['cpu']
    static_configs:
      - targets: ['app-server-1:8080', 'app-server-2:8080']

  - job_name: 'low-freq-batch'
    scrape_interval: 60s
    scrape_timeout: 30s
    metrics_path: '/metrics'
    scheme: https
    basic_auth:
      username: admin
      password: secret123
    tls_config:
      insecure_skip_verify: true
    static_configs:
      - targets: ['batch-server:9100']

  - job_name: 'elasticsearch'
    scrape_interval: 30s
    scrape_timeout: 10s
    metrics_path: '/_prometheus/metrics'
    scheme: http
    basic_auth:
      username: admin
      password: 'p@ss:w0rd#special'
    relabel_configs:
      - source_labels: [__address__]
        target_label: instance
    metric_relabel_configs:
      - regex: 'go_.*'
        action: drop
    static_configs:
      - targets: ['es-node:9200']
```

字段逐行解析：

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `job_name` | string | **必填** | job的唯一标识，会作为`job`标签加入所有采集指标的label set |
| `scrape_interval` | duration | global值 | 此job的采集间隔，覆盖global设置 |
| `scrape_timeout` | duration | 10s | 单次采集超时时间，**必须 < scrape_interval** |
| `metrics_path` | string | `/metrics` | exporter暴露指标的HTTP路径 |
| `scheme` | string | `http` | 协议，可选`http`或`https`。选`https`时需配置`tls_config` |
| `params` | map[string][]string | 无 | 追加到请求URL的查询参数，如`?module=cpu` |
| `basic_auth` | object | 无 | HTTP Basic认证，含`username`和`password`(或`password_file`) |
| `relabel_configs` | array | 无 | 采集前的relabel规则，用于修改/过滤target label |
| `metric_relabel_configs` | array | 无 | 采集后的relabel规则，用于修改/过滤采集到的指标的label |

关键约束：`scrape_timeout`必须严格小于`scrape_interval`。如果你设`scrape_interval: 10s`且`scrape_timeout: 15s`，Prometheus不会报错，但每次采集都会超时，导致数据永远采集不全。

### 步骤3：配置认证和TLS

**场景一：内网Elasticsearch Exporter需要Basic Auth**

```yaml
scrape_configs:
  - job_name: 'es-secure'
    metrics_path: '/_prometheus/metrics'
    scheme: http
    basic_auth:
      username: admin
      password: 'p@ss:w0rd#special'
    static_configs:
      - targets: ['es-node:9200']
```

如果密码含有特殊字符（`@`、`#`、`:`、`%`等），**必须用单引号包裹**，否则YAML解析器会误读。更安全的做法是用`password_file`：

```yaml
    basic_auth:
      username: admin
      password_file: /etc/prometheus/secrets/es_password.txt
```

将密码写入文件，避免明文出现在配置文件中，也绕开了YAML转义问题。

**场景二：HTTPS exporter使用自签名证书**

```yaml
scrape_configs:
  - job_name: 'secure-exporter'
    scheme: https
    tls_config:
      ca_file: /etc/prometheus/certs/ca.crt
      cert_file: /etc/prometheus/certs/client.crt
      key_file: /etc/prometheus/certs/client.key
      server_name: exporter.internal.example.com
    static_configs:
      - targets: ['secure-host:8443']
```

如果仅做测试验证连接通不通，可以临时用：

```yaml
    tls_config:
      insecure_skip_verify: true
```

**但生产环境绝对不要用`insecure_skip_verify: true`**——它会让Prometheus跳过证书验证，给你一种"配置成功"的假象，实际上你的数据在明文传输，任何人都可以伪造exporter返回假指标。

### 步骤4：rule_files和alerting配置

**Recording & Alerting Rules配置**

```yaml
rule_files:
  - 'rules/recording_rules.yml'
  - 'rules/alert_rules.yml'
  - 'alerts/*.yml'
```

几点关键：

- 路径是相对于`prometheus.yml`所在的目录，不是Prometheus的启动目录。
- 支持glob模式：`alerts/*.yml`会加载`alerts`目录下所有`.yml`文件。
- `evaluation_interval`控制这些rule被评估的频率。

**Alertmanager配置**

```yaml
alerting:
  alertmanagers:
    - static_configs:
        - targets:
            - 'alertmanager-1:9093'
            - 'alertmanager-2:9093'
      path_prefix: /alertmanager
      scheme: http
      timeout: 10s
```

也可以用service discovery动态发现Alertmanager：

```yaml
alerting:
  alertmanagers:
    - dns_sd_configs:
        - names:
            - '_alertmanager._tcp.example.com'
```

对于高可用部署，建议用`static_configs`配多个Alertmanager实例，或通过consul/k8s等service discovery自动感知Alertmanager的增删。

### 步骤5：使用promtool校验配置

`promtool`是Prometheus自带的配置检查工具，任何配置修改后都应该先过一遍：

```bash
# 语法和语义检查
promtool check config prometheus.yml
```

如果一切正确，输出：

```
Checking prometheus.yml
  SUCCESS: prometheus.yml is valid prometheus config file syntax
```

现在故意制造几个错误，观察`promtool`的报错能力：

**错误1：YAML缩进错误**

```yaml
global:
  scrape_interval: 15s
  external_labels:
  datacenter: beijing    # 缺少缩进
```

```
FAILED: prometheus.yml is not valid YAML
```

**错误2：时间格式错误**

```yaml
scrape_interval: 15seconds    # 应该是15s
```

```
FAILED: unknown unit "econds" in duration "15seconds"
```

**错误3：job_name重复**

```
FAILED: found multiple scrape configs with job name "prometheus"
```

最佳实践：在CI/CD pipeline中集成`promtool check config`步骤，任何prometheus.yml的修改必须通过检查才能合并：

```yaml
# .gitlab-ci.yml 示例
validate-prometheus-config:
  stage: test
  script:
    - promtool check config prometheus.yml
    - promtool check rules rules/*.yml
```

### 可能遇到的坑

1. **YAML冒号后必须有空格**：`job_name:prometheus`报错，正确写法是`job_name: prometheus`。YAML的key-value分隔符"冒号+空格"是个整体。

2. **scrape_interval设太小导致OOM**：生产环境不要低于5s。Prometheus会在内存中保存每个target的当前采集数据，如果采集太频繁而target又很多，内存会被瞬间撑爆。

3. **external_labels冲突**：如果exporter暴露的指标中已经有名为`datacenter`的label，Prometheus会拒绝添加同名的external_label。联邦查询时，如果两个Prometheus实例的`external_labels`完全相同，上层查询端无法区分数据来源，导致数据错乱。

## 四、项目总结

### 配置层级继承关系

```
global (默认值层)
  ├── scrape_configs[].job (覆盖global)
  │     └── static_configs[].targets (继承job配置)
  ├── rule_files (独立，不受global采集参数影响)
  └── alerting.alertmanagers (独立配置块)
```

核心原则：job级别写了就按job的来，没写就继承global。不存在"global是硬性上限，job可以在范围内调整"这种层级约束。

### 配置字段速查表

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `global.scrape_interval` | duration | 1m | 采集间隔默认值 |
| `global.evaluation_interval` | duration | 1m | rule评估间隔 |
| `global.external_labels` | map | {} | 联邦/远程写入区分实例 |
| `scrape_configs[].scrape_interval` | duration | global值 | 可覆盖global |
| `scrape_configs[].scrape_timeout` | duration | 10s | 必须 < scrape_interval |
| `scrape_configs[].metrics_path` | string | /metrics | 采集路径 |
| `scrape_configs[].scheme` | string | http | http或https |
| `scrape_configs[].honor_labels` | bool | false | 是否保留exporter原始label |
| `scrape_configs[].honor_timestamps` | bool | true | 是否保留exporter原始时间戳 |
| `scrape_configs[].basic_auth` | object | 无 | Basic认证 |
| `scrape_configs[].bearer_token` | string | 无 | Bearer Token认证 |
| `scrape_configs[].oauth2` | object | 无 | OAuth2认证 |
| `scrape_configs[].tls_config` | object | 无 | TLS/SSL配置 |
| `scrape_configs[].relabel_configs` | array | 无 | 采集前relabel |
| `scrape_configs[].metric_relabel_configs` | array | 无 | 采集后relabel |
| `rule_files` | []string | [] | rules文件路径，支持glob |
| `alerting.alertmanagers` | array | 无 | Alertmanager目标配置 |

### 适用场景与注意事项

**适用场景**：
- 单机部署：只需一个`scrape_configs`采集Prometheus自身
- 多Job多间隔：核心服务5s采集，批处理60s采集，弹性配置
- 跨数据中心联邦：通过`external_labels`标记数据来源
- 远程存储：配合`remote_write`将数据以不同标签写入长期存储后端

**注意事项**：
- `scrape_timeout`必须严格小于`scrape_interval`，两者相等也会导致频繁超时
- `external_labels`的label名不能与exporter暴露的label冲突
- `rule_files`路径相对于配置文件所在目录，不是工作目录
- YAML中冒号后必须有空格，密码含特殊字符用单引号或`password_file`
- 生产环境禁止使用`tls_config.insecure_skip_verify: true`

### 常见踩坑经验

**案例1：metrics_path漏写斜杠**。`metrics_path: 'metrics'`导致路径拼接为`/metrics`当然没问题。但`metrics_path: 'metric'`只少了一个`s`，Prometheus不会报错，只是`up{job="xxx"} == 0`。排查技巧：直接查看Targets页面每个目标的Scrape详情。

**案例2：basic_auth密码含特殊字符**。密码中的`@`、`#`、`:`会破坏YAML结构，`promtool check config`会直接报YAML解析错误。解决：单引号包裹或用`password_file`。

**案例3：honor_labels引发标签冲突**。某次配置了`honor_labels: true`，而exporter恰好在指标中带了`instance`标签。Prometheus自身的`instance`标签被覆盖，导致Grafana大盘中主机名显示异常。不是特殊场景（Pushgateway）不要轻易开启`honor_labels`。

### 思考题

1. 如果设置`scrape_timeout: 15s`、`scrape_interval: 10s`，会发生什么？Prometheus会报错吗？

2. `external_labels`在联邦场景下有什么作用？如果两个Prometheus实例的`external_labels`完全相同，会带来什么问题？

---

*参考答案欢迎在评论区留言讨论。下一章将进入Recording Rules与Alerting Rules的世界，教你用PromQL写出生产级告警规则。*
