# 第10章：Alertmanager——告警路由、分组与静默

## 一、项目背景

运维团队终于配好了Prometheus告警规则，满怀信心地上线了。结果第一天晚上，小刘的手机就被炸了——300条告警短信像潮水一样涌进来。他仔细一翻，发现同一个"HighCPUUsage"告警，因为涉及5台机器，每台发了10遍，光这一个告警就占了50条。更惨的是，数据库相关的告警本来应该发给DBA团队，结果全发到了运维群里，DBA完全不知道凌晨数据库主从延迟已经飙到5秒了。还有一次，运维在做计划内维护，主动停掉了两台Web服务器，告警铺天盖地而来，值班同事只好把手机调成静音……然后漏掉了一条真正的磁盘即将写满的告警。

第二天复盘会上，CTO面无表情地说了一句话："告警不是发出去就完事了，要做告警治理。"四个关键词被写在白板上：**路由**、**分组**、**抑制**、**静默**。

这就是Alertmanager的用武之地。Alertmanager是Prometheus生态中的告警治理核心组件，它接收Prometheus Server推送过来的告警，然后负责去重、分组、路由、抑制和静默，最终通过邮件、Webhook、Slack、钉钉等渠道发送给正确的接收者。但配置不当，它会让你从"收不到告警"直接变成"告警轰炸"。route路由树怎么配才能让不同团队各收各的？group_by选哪几个维度才能既聚合又不丢信息？group_wait和group_interval到底有什么区别？inhibition和silence各自的适用场景是什么？怎么对接钉钉、飞书、企业微信这些国产IM？这一章，我们从零开始把Alertmanager搞懂搞透。

## 二、剧本式交锋对话

**小胖**（挠头）：大师，Prometheus Server不是自己就能发告警吗？我看它配置文件里也有alerting的配置，为啥还要单独搞一个Alertmanager？这不是多此一举吗？

**小白**（插嘴）：就是就是！我上次直接让Prometheus对接钉钉Webhook，一条一条地发，也没觉得有什么问题啊。

**大师**（放下茶杯）：你们俩先把"300条告警短信炸手机"的画面在脑子里过一遍。Prometheus Server的职责是**判定**告警——从指标数据里发现异常、触发告警规则。但它不管"这条告警发了没、发了几次、该发给谁、要不要跟其他告警合并"。它每评估一次规则，只要告警还处于触发状态，就会把告警原样推给Alertmanager。如果你让Prometheus直接对接通知渠道，等于把这些问题全部抛给了通知接收者。Alertmanager的定位就是专门做**告警治理**——它坐在Prometheus和真实通知渠道之间，像一座治水大坝，把告警洪峰削峰填谷、分渠导流。

**小胖**（若有所思）：所以数据流是 Prometheus → Alertmanager → 钉钉/邮件/PagerDuty 这样的链？

**大师**：没错。Prometheus把告警一股脑推到Alertmanager，Alertmanager根据你配的**路由树（route）**来分发。路由树的核心是标签匹配——match做精确匹配，match_re做正则匹配，匹配上了就转发到对应的receiver（通知接收器）。而且路由树可以嵌套，子路由只处理从父路由漏下来的告警。举个栗子：根路由发给默认群，然后你在下面加一条子路由——match `severity: critical` 的发给运维组的PagerDuty，match_re `team: 'backend|frontend'` 的发给开发组的钉钉群。这样就实现了"不同团队收不同告警"。

**小白**（举手）：那分组呢？刚才小胖说他同一个"HighCPUUsage"收到了50条，分组是不是干这个的？

**大师**：对。Alertmanager的分组机制有两个核心参数。**group_by**决定按哪些label把告警聚合在一起——比如按`alertname`分组，那么所有同名的告警会打包成一条通知发出去，而不是一条一发。**group_wait**是"攒一波"的时间窗口：收到本组第一条告警后，先等10秒，等同一组的其他告警进来，然后再一起发。这样5台机器的HighCPUUsage就会被合成一条通知，而不是5条。**group_interval**呢，是"发了一波之后，隔多久才能再发下一波"——比如设成10分钟，那么即使这10分钟内有新的告警加入同组，也不会立刻再发，而是攒够10分钟再统一发一次。还有**repeat_interval**，它控制的是"某一条告警一直没恢复，隔多久再提醒你一次"，比如1小时重复一次。

**小胖**：我去，原来告警治理这么多维度！那inhibition和silence呢？听起来很像啊，都是"不发"。

**大师**（笑着摇头）：不一样。**Inhibition（抑制）**是"有A在的时候，B就别发了"，比如整台机器宕机了（InstanceDown告警触发），那这台机器上的CPU高、内存高、磁盘满这些衍生告警还有意义吗？没意义了——根因就是机器挂了，修好机器自然都好了。所以用inhibition规则，让InstanceDown把这些告警全抑制掉。**Silences（静默）**则是"我知道这段时间会吵，主动关掉声音"——比如计划内维护窗口，提前在Alertmanager上创建一条Silence，指定匹配条件（如`instance=web-01`）和时间窗口，这段时间内匹配的告警就不发了。Inhibition是自动的、基于规则的；Silence是手动的、基于时间窗口的。

**小白**（眼睛一亮）：懂了！那我还有一个问题——通知内容怎么定制？告警发到群里总不能是一坨JSON吧？

**大师**：问得好。Alertmanager的通知模板用的是Go Template语法，你可以在receiver的webhook配置里写message字段。模板里能访问很多变量：`.Status`是firing还是resolved，`.CommonLabels`是这个分组里所有告警的公共标签，`.CommonAnnotations`是公共注解（通常放描述信息），`.StartsAt`是告警触发时间。写模板的时候注意——访问map中不存在的key会painc，所以最好用`index`函数：`{{ index .CommonLabels "instance" }}`。对接钉钉的话，message里构造一个markdown格式的JSON就行，钉钉的机器人Webhook会把它渲染成漂亮的卡片消息。

## 三、项目实战

### 环境准备

确保已有一个运行中的Alertmanager（Docker方式）：

```bash
docker run -d --name alertmanager -p 9093:9093 prom/alertmanager
```

同时需要Prometheus的`prometheus.yml`中配置了alerting指向Alertmanager：

```yaml
alerting:
  alertmanagers:
    - static_configs:
        - targets: ['localhost:9093']
```

另外，至少有一条活跃的告警规则（延续第9章的配置），比如之前配好的`HighCPUUsage`规则。

### 步骤1：Alertmanager最小配置

创建`alertmanager.yml`，这是Alertmanager的核心配置文件：

```yaml
global:
  resolve_timeout: 5m

route:
  receiver: 'default'
  group_by: ['alertname']
  group_wait: 10s
  group_interval: 10s
  repeat_interval: 1h

receivers:
  - name: 'default'
    webhook_configs:
      - url: 'http://localhost:8080/webhook'
```

逐行解释：

- **`global.resolve_timeout`**：告警恢复后，如果在5分钟内没有新的firing，Alertmanager才认为该告警真正恢复了。这避免了短暂恢复再触发导致的"抖动通知"。
- **`route.receiver`**：默认接收器，没有命中任何子路由的告警都发给它。
- **`route.group_by`**：按`alertname`标签分组，同名的告警会聚合到一起发。
- **`route.group_wait`**：收到组内第一条告警后等待10秒，攒一波再发，用于同组告警的首次聚合。
- **`route.group_interval`**：同一分组两次通知之间至少间隔10秒。注意这里是演示环境为了方便观察设得短，生产环境应适当拉长。
- **`route.repeat_interval`**：某条告警一直未恢复时，每小时重复提醒一次，防止长时间告警被遗忘。
- **`receivers`**：定义通知接收器，这里用webhook指向本地8080端口做测试。

启动Alertmanager并加载配置：

```bash
docker run -d --name alertmanager -p 9093:9093 \
  -v $(pwd)/alertmanager.yml:/etc/alertmanager/alertmanager.yml \
  prom/alertmanager --config.file=/etc/alertmanager/alertmanager.yml
```

访问 `http://localhost:9093` 可以看到Alertmanager的Web UI。

### 步骤2：实现多级路由——不同团队收不同告警

真实场景中，基础设施告警（`severity=critical`）应该发给运维团队，业务告警（`team=backend`）应该发给开发团队。这就需要多级路由：

```yaml
route:
  receiver: 'default'
  group_by: ['alertname', 'severity']
  group_wait: 10s
  group_interval: 10s
  repeat_interval: 1h
  routes:
    - match:
        severity: critical
      receiver: 'ops-team'
      continue: true
    - match_re:
        team: 'backend|frontend'
      receiver: 'dev-team'

receivers:
  - name: 'default'
    webhook_configs:
      - url: 'http://localhost:8080/webhook'
  - name: 'ops-team'
    webhook_configs:
      - url: 'http://localhost:8081/ops-webhook'
  - name: 'dev-team'
    webhook_configs:
      - url: 'http://localhost:8082/dev-webhook'
```

这里的`group_by`加了`severity`维度——同一个告警名但不同严重级别会拆成不同的通知组，避免critical和warning混在一起降低紧迫感。路由树逻辑如下：

1. 所有告警先进入根路由，命中第一个子路由：如果`severity=critical`，发到`ops-team`。
2. `continue: true`表示即使命中了`ops-team`，**继续**往下匹配后续子路由。如果不加这行，匹配到第一个子路由就会停止——那`severity=critical`的告警就只发给运维，不会同时发给开发团队了。
3. 第二个子路由用`match_re`做正则匹配，`team`标签为`backend`或`frontend`的告警发给`dev-team`。
4. 都没命中的走默认`default`路由。

### 步骤3：配置钉钉Webhook通知

日常告警发到钉钉群是最常见的需求。需要先创建一个钉钉群机器人，拿到Webhook URL（里面有一个access_token）。然后在receiver里配置：

```yaml
receivers:
  - name: 'dingtalk'
    webhook_configs:
      - url: 'https://oapi.dingtalk.com/robot/send?access_token=YOUR_TOKEN'
        send_resolved: true
        http_config:
          headers:
            Content-Type: 'application/json'
        message: |
          {
            "msgtype": "markdown",
            "markdown": {
              "title": "{{ .CommonLabels.alertname }}",
              "text": "### {{ .CommonLabels.alertname }}\n> **状态**: {{ .Status | toUpper }}\n> **级别**: {{ .CommonLabels.severity }}\n> **实例**: {{ index .CommonLabels \"instance\" }}\n> **描述**: {{ .CommonAnnotations.description }}\n> **时间**: {{ .StartsAt.Format \"2006-01-02 15:04:05\" }}"
            }
          }
```

Go Template中的关键变量说明：

- **`.CommonLabels`**：当前通知组中所有告警的公共标签集合，是`map[string]string`类型。比如所有告警都有`alertname=HighCPUUsage`，那它就出现在这里。
- **`.Status`**：告警状态，`firing`或`resolved`。搭配`send_resolved: true`可以在告警恢复时也发一条通知。
- **`.CommonAnnotations`**：告警规则中由`annotations`字段定义的公共注解，通常放`description`、`summary`等人类可读信息。
- **`.StartsAt`**：告警触发时间，用`.Format`方法格式化。注意Go的时间格式模板是固定的`2006-01-02 15:04:05`（Go诞生的时间）。
- **`index`函数**：安全访问map中可能不存在的key。直接用`{{ .CommonLabels.instance }}`如果`instance`标签不存在会panic，导致通知发送失败。

### 步骤4：配置抑制规则（Inhibition）

当一台机器宕机时，该机器上的CPU、内存、磁盘告警都是衍生告警，应该被抑制：

```yaml
inhibit_rules:
  - source_match:
      alertname: 'InstanceDown'
      severity: 'critical'
    target_match_re:
      alertname: 'HighCPUUsage|HighMemoryUsage|DiskAlmostFull'
    equal: ['instance']
```

规则解读：

- **`source_match`**：匹配"抑制源"告警，即InstanceDown触发时。
- **`target_match_re`**：匹配"被抑制目标"告警，即各类资源监控告警。
- **`equal: ['instance']`**：只有当source和target的`instance`标签值相同时，抑制才生效。也就是说，`web-01`的InstanceDown只会抑制`web-01`上的衍生告警，不会误伤`web-02`的告警。

被抑制的告警在Alertmanager UI里会显示为`suppressed`状态，不会触发任何通知。当InstanceDown恢复后，被抑制的告警如果仍在触发，会重新变成`active`并进入通知流程。

### 步骤5：使用Silences临时屏蔽告警

有两种创建Silence的方式：

**方式一：Web UI操作**

打开 `http://localhost:9093/#/silences`，点击"New Silence"。在Matchers区域添加匹配条件，比如`instance="web-01"`。设置开始时间和结束时间（比如计划维护的2小时窗口），填写创建人和备注（方便后续追溯是谁、为什么创建了这个Silence），点击Create。维护期间所有匹配的告警都不会发送通知。

**方式二：API操作**

```bash
curl -X POST http://localhost:9093/api/v2/silences \
  -H "Content-Type: application/json" \
  -d '{
    "matchers": [{"name":"instance","value":"web-01","isRegex":false}],
    "startsAt": "2026-05-14T22:00:00Z",
    "endsAt": "2026-05-15T00:00:00Z",
    "createdBy": "ops-team",
    "comment": "web-01计划维护2小时"
  }'
```

维护结束后，Silence会自动过期，也可以手动删除。API方式适合集成到CI/CD或运维平台中，在发布或变更前自动创建Silence。

### 可能遇到的坑

**坑1：钉钉access_token泄露**。Webhook URL中的access_token一旦泄露，任何人都可以往群里发消息。在生产环境中，务必把token放在环境变量或密钥管理系统中，不要硬编码在配置文件里提交到Git仓库。

**坑2：Go Template访问不存在的label会panic**。直接用`{{ .CommonLabels.instance }}`没问题，但如果某个告警恰好没有`instance`标签，整个模板渲染就会失败，通知发不出去。安全的写法是`{{ index .CommonLabels "instance" }}`，返回空字符串而不会panic。

**坑3：inhibition的equal标签名写错**。如果你写了`equal: ['hostname']`但实际标签是`instance`，抑制规则不会生效，同时Alertmanager不会报任何错。排查时可以去Alertmanager UI里检查告警的label，确保equal字段的标签名完全一致。

**坑4：repeat_interval调太短或太长**。设成5分钟会导致告警轰炸，设成24小时可能导致长时间未恢复的重要告警被遗漏。推荐生产环境设为1小时到4小时之间。

### 测试验证

```bash
# 1. 在本地起一个简单的HTTP Server模拟webhook接收端
python3 -m http.server 8080

# 2. 停掉一台Node Exporter，观察Alertmanager UI
# 进入 http://localhost:9093 查看告警状态：
#   active → suppressed（被inhibition抑制） → resolved（恢复后）

# 3. 验证路由分发
# 分别启动三个webhook监听端口，制造不同标签的告警，
# 确认ops-team和dev-team各收到自己该收的告警

# 4. 测试Silences
# 创建一条匹配 instance=web-01 的Silence
# 停掉web-01的Node Exporter
# 确认该InstanceDown告警在Alertmanager UI中显示为活跃但不发送通知
# 删除Silence后，通知立即重新触发
```

## 四、项目总结

### 路由设计最佳实践

路由树建议按层级设计：**第一层按severity（严重级别）**，把critical和warning分开，critical走紧急通知渠道（电话/PagerDuty），warning走普通渠道（钉钉/邮件）；**第二层按team或service**，把不同业务线的告警分发给对应团队；**第三层按具体告警类型做精细化处理**。这种分层路由既清晰又灵活，新增团队只需要加一条子路由即可。

### 分组参数调优指南

| 参数 | 推荐值 | 说明 |
|------|--------|------|
| `group_wait` | 10s–30s | 首次聚合等待时间，太短=发太多条，太长=首次通知延迟过大 |
| `group_interval` | 5m–15m | 同组通知间隔，避免短时间内刷屏 |
| `repeat_interval` | 1h–4h | 未恢复告警的重复提醒频率，太长可能漏掉，太短造成骚扰 |

### 通知渠道对比

| 渠道 | 适用场景 | 延迟 | 复杂度 |
|------|----------|------|--------|
| Email | 日/周报汇总、非紧急通知 | 低-中 | 低 |
| Webhook | 对接任意系统（钉钉/飞书/企业微信） | 低 | 中 |
| PagerDuty | 7×24值班、排班升级 | 极低 | 高 |
| Slack | 技术团队日常协作 | 低 | 低 |
| WeChat（企业微信） | 国内企业IM通知 | 低 | 中 |

### 适用场景

多团队告警分发（路由树+标签匹配）、告警聚合降噪（group_by分组）、计划维护窗口管理（Silences静默）、跨数据中心告警路由（不同DC的不同Alertmanager实例）、根因分析辅助（Inhibition自动抑制衍生告警）。

### 高可用与运维要点

Alertmanager支持**gossip集群模式**部署，多个实例之间通过gossip协议同步silences和notification日志，实现告警去重——同一个告警只会被集群中的一个实例发送出去，不依赖外部负载均衡器。为什么不能直接用负载均衡？因为Prometheus Server是向**所有**Alertmanager实例推送告警的，如果前面放一个LB做轮询，每条告警会被不同实例各自发送一次，造成重复通知。gossip集群内部的去重机制才是正解。

另外，Webhook receiver的接收端应当实现**幂等性**，因为Alertmanager在网络超时等情况下可能对同一通知重试发送。配置文件建议走**GitOps**流程：所有`alertmanager.yml`的变更都在Git中审批和记录，通过CI/CD自动推送到Alertmanager实例热加载（`POST /-/reload`）。

### 常见踩坑经验

1. **gossip集群脑裂**：网络分区时两个实例都认为自己是leader，导致同一告警发两次。解决方案是cluster节点数≥3并合理配置`cluster.peer-timeout`。
2. **webhook超时未配置**：Alertmanager默认HTTP超时较短，钉钉飞书等外部Webhook可能网络延迟较高导致发送失败。应在`http_config`中显式配置`proxy_url`或调大超时时间。
3. **Silences误匹配**：创建Silence时匹配条件写得太宽（比如只匹配了`severity=critical`），导致计划外的重要告警也被误屏蔽。建议匹配条件尽量精确到`instance`级别。

### 思考题

1. **如何在告警通知中包含一个Grafana大盘链接，让接收者一键跳转到相关监控面板？**（提示：在告警规则的`annotations`中放一个Grafana跳转URL，Go Template中用`{{ .CommonAnnotations.grafana_url }}`渲染出来。）

2. **Alertmanager的gossip集群如何实现多实例间告警去重？为什么不能直接用负载均衡器？**（提示：gossip协议同步通知状态，每个实例都知道哪些告警已经被发送过；Prometheus会向所有实例推送完全相同的数据，所以需要集群内部去重而非靠外部LB。）
