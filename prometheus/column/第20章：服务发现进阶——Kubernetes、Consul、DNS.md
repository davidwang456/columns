# 第20章：服务发现进阶——Kubernetes、Consul、DNS

## 一、项目背景

公司全面上云后，300+微服务全部跑在了Kubernetes集群中。Pod的生命周期短得惊人——滚动更新、弹性伸缩、节点故障重调度，每天IP地址变动超过300次。运维团队最初沿用了传统VM时代的方案，用crontab脚本定时从K8s API拉取Pod IP，生成targets.json喂给Prometheus的file_sd_configs。这套方案在服务规模突破100个后就彻底崩了：脚本要几分钟才能跑完一轮，而K8s里Pod可能30秒内漂移到位。Prometheus频繁对着已经不存在的Pod IP发起采集，scrape error率一度飙到15%。

更棘手的是，公司还有一批"钉子户"服务——要么跑在物理机上通过Consul注册，要么通过DNS SRV记录做服务发现。运维团队面临的是"三套SD方案各管一摊"的窘境：脚本管K8s、手工配置管Consul、静态列表管DNS。每新上一个服务，Prometheus的配置就得改一次，新人不小心改错一次就把整个监控废了。

这就是服务发现的"进阶之痛"：不是没有SD能力，而是不知道Prometheus原生就支持Kubernetes、Consul、DNS这三种动态发现方式，不需要任何外部脚本。尤其是Kubernetes Service Discovery（简称K8s SD）——它是Prometheus生态里最强大也最让人眼花缭乱的配置模块，光是`__meta_kubernetes_*`开头的标签就有几十个。五个role（pod/service/endpoints/node/ingress）各有各的适用场景，选错了要么多采、要么漏采。再加上relabel_configs的配合，如何把K8s的元数据映射成有意义的Prometheus标签？本章就来把这些坑一个一个填平。

---

## 二、剧本式交锋对话

**小胖**：（抓狂地敲键盘）大师！我们的Prometheus又在"炸雷"了——scrape error满天飞，全是"connect: connection refused"。我一看，全是那些已经销毁的Pod IP。不是说Prometheus有服务发现吗？为什么还在采死掉的Pod？

**大师**：（喝了一口咖啡）你用的什么SD方式？

**小胖**：file_sd_configs啊，脚本每两分钟拉一次Pod列表写JSON。

**大师**：两分钟？你知不知道K8s里一个Pod从Pending到Running只要5秒？你这相当于用自行车追高铁。

**小白**：（凑过来）那该用什么？我听说Prometheus有kubernetes_sd_configs？

**大师**：对，这是Prometheus对K8s的原生支持。它通过调用K8s API直接watch集群里的资源变化，Pod创建/销毁可以秒级感知，不需要任何外部脚本。但关键是——你得选对role。

**小胖**：role？我看看文档……pod、service、endpoints、node、ingress，五个！这怎么选？

**大师**：（在白板上画起来）一个一个来。**pod role**最简单粗暴——直接发现K8s集群里所有Pod的IP，不管这个Pod属于哪个Service，统统列出来。好处是覆盖面广，坏处是一个Service背后可能有10个Pod，你会采到10个target，其中任何一个挂了都会报scrape error。

**小白**：那怎么避免采到不健康的Pod？

**大师**：这正是**endpoints role**要解决的问题——它发现的是Service所对应的Endpoints对象。一个Service后面只有健康的Pod才会被写入Endpoints列表。如果某个Pod挂了，Endpoints会自动把它踢掉，Prometheus就不会去采它。**这就是为什么90%的K8s SD配置都用endpoints role——它是面向"服务"的，而非面向"Pod"。**

**小胖**：等等，那service role和endpoints role有什么区别？

**大师**：好问题。**service role**发现的是Service自己的ClusterIP。你用service role每找到一个Service就生成一个target，但这个target的`__address__`指向的是ClusterIP——虚拟IP。你采的是Service的ClusterIP，而不是后端Pod。这通常不是你想要的。**endpoints role**会把你带到真正的Pod IP上，每个后端Pod都是一个独立的target。

**小白**：那node和ingress呢？

**大师**：**node role**用来发现集群Node的IP，适合采集kubelet内置metrics或node_exporter。**ingress role**发现Ingress对象的端点，适合监控Ingress控制器的指标。

**小胖**：（翻看Prometheus配置）对了，这些`__meta_kubernetes_namespace`、`__meta_kubernetes_pod_name`是干嘛的？

**大师**：这是K8s SD的精华——**元数据标签族**。每次Prometheus从K8s API拿到一个Pod/Service/Endpoint，都会把它的所有K8s属性作为`__meta_kubernetes_*`标签贴上去。比如Pod的名字、namespace、node、label、annotation，应有尽有。但它们默认不进入最终的target标签——你需要用**relabel_configs**把它们"转正"。

**小胖**：那Consul SD和DNS SD呢？我们还有一堆跑Consul的遗留服务。

**大师**：Consul SD适合非K8s环境——通过Consul Catalog API拉取已注册的服务列表。如果你的架构是"K8s + 物理机"混合部署，两组服务各用各的注册中心，那就是K8s SD + Consul SD双管齐下。DNS SD则最轻量——配置一个域名，Prometheus定期做DNS A/SRV查询来发现target。适合传统VM或负载均衡器后面的场景，缺点是不能带元数据，只能拿到IP和端口。

**小胖**：还有RBAC！我上次试着配K8s SD，Prometheus日志里什么都没有，target列表空空如也。

**大师**：经典坑。Prometheus的ServiceAccount必须有list/watch权限才能访问K8s API。权限不足时SD不会报错，而是静默返回空列表。查这个问题的第一反应就是去验证`kubectl auth can-i list pods`。

---

## 三、项目实战

### 环境准备

- 一个可用Kubernetes集群（minikube/kind/k3s均可）
- Prometheus部署在K8s集群内（通过ServiceAccount自动获取API访问凭据）
- kubectl命令行工具
- （可选）Consul开发环境用于Consul SD测试

### 步骤1：配置K8s RBAC权限

Prometheus需要调用K8s API来发现Pod/Service/Endpoint等资源。最少需要`get`、`list`、`watch`三种动词——其中**watch最为关键**，它让Prometheus通过长连接实时接收资源变更事件，而不是轮询。缺少watch权限，SD退化为周期性全量同步，延迟大幅增加。

```yaml
apiVersion: v1
kind: ServiceAccount
metadata:
  name: prometheus
  namespace: monitoring
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: prometheus
rules:
  - apiGroups: [""]
    resources: ["nodes", "nodes/metrics", "services", "endpoints", "pods"]
    verbs: ["get", "list", "watch"]
  - apiGroups: ["networking.k8s.io"]
    resources: ["ingresses"]
    verbs: ["get", "list", "watch"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: prometheus
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: prometheus
subjects:
  - kind: ServiceAccount
    name: prometheus
    namespace: monitoring
```

关键点：使用ClusterRole而非Role，因为Pod可能分布在任意namespace，Role只能限定单个namespace。`nodes/metrics`资源是K8s v1.18+引入的，如不需要采集node metrics可省略。

### 步骤2：Kubernetes SD配置——Endpoints Role

以下是经典的"annotation-driven"K8s SD配置，也是Prometheus社区最推荐的模式：通过Pod上的annotation声明"请采集我"，避免采到无关服务。

```yaml
scrape_configs:
  - job_name: 'kubernetes-pods'
    kubernetes_sd_configs:
      - role: endpoints
        api_server: 'https://kubernetes.default.svc'
        tls_config:
          ca_file: /var/run/secrets/kubernetes.io/serviceaccount/ca.crt
        bearer_token_file: /var/run/secrets/kubernetes.io/serviceaccount/token
    relabel_configs:
      # 规则1：只保留打了 prometheus.io/scrape: "true" 注解的Service
      - source_labels: [__meta_kubernetes_service_annotation_prometheus_io_scrape]
        action: keep
        regex: true

      # 规则2：从注解读取采集路径，未配置则用默认 /metrics
      - source_labels: [__meta_kubernetes_service_annotation_prometheus_io_path]
        action: replace
        target_label: __metrics_path__
        regex: (.+)

      # 规则3：从注解读取端口，重写 __address__（IP:Port格式）
      - source_labels: [__address__, __meta_kubernetes_service_annotation_prometheus_io_port]
        action: replace
        regex: ([^:]+)(?::\d+)?;(\d+)
        target_label: __address__
        replacement: $1:$2

      # 规则4：K8s元数据 → Prometheus标签
      - source_labels: [__meta_kubernetes_namespace]
        target_label: namespace
      - source_labels: [__meta_kubernetes_service_name]
        target_label: service
      - source_labels: [__meta_kubernetes_pod_name]
        target_label: pod
      - source_labels: [__meta_kubernetes_pod_node_name]
        target_label: node
```

逐条解释：

- **规则1（keep）**：只保留`prometheus.io/scrape`注解为字符串`"true"`的Service。这是"opt-in"机制——不声明的Service一概不采，避免乱采。注意regex里是`true`（字符串），不是布尔值。
- **规则2（replace）**：从注解`prometheus.io/path`读取采集路径，赋值给内置标签`__metrics_path__`。如果注解不存在，规则不匹配，`__metrics_path__`保持默认值`/metrics`。
- **规则3（replace）**：从注解`prometheus.io/port`读取端口号，拼接成`IP:Port`覆盖`__address__`。正则中的`:?\d+`处理了`__address__`可能已经带端口的情况。
- **规则4**：将K8s元数据映射为标准Prometheus标签，后续查询和告警都可用`namespace`、`pod`、`service`、`node`做过滤。

### 步骤3：验证K8s SD效果

部署一个测试应用，加上必要的annotation：

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: demo-app
spec:
  replicas: 2
  selector:
    matchLabels:
      app: demo
  template:
    metadata:
      labels:
        app: demo
      annotations:
        prometheus.io/scrape: "true"
        prometheus.io/port: "8080"
        prometheus.io/path: "/metrics"
    spec:
      containers:
        - name: app
          image: nginx:alpine
          ports:
            - containerPort: 8080
```

验证步骤：

1. **检查RBAC权限**：执行`kubectl auth can-i list pods --as=system:serviceaccount:monitoring:prometheus`，确保返回`yes`。
2. **查看Prometheus日志**：启动时应看到`msg="Kubernetes SD"`相关信息，表示SD初始化成功。
3. **查看Service Discovery页面**：Prometheus Web UI → Status → Service Discovery → 展开`kubernetes-pods` job。在"Discovered Labels"列可以看到几十个`__meta_kubernetes_*`标签——namespace、pod_name、pod_ip、node_name、annotation等。这些是K8s SD从API拿到的原始元数据。
4. **对比Target Labels**：切换到"Target Labels"列的同一target，标签已被relabel_configs精简为`namespace`、`service`、`pod`、`node`等有意义的项。
5. **测试动态性**：`kubectl scale deployment demo-app --replicas=5`——30秒内Targets页面出现5个新target。`kubectl scale deployment demo-app --replicas=1`——多余target消失。
6. **删除Pod测试**：`kubectl delete pod <pod-name>`——被删Pod的target从Prometheus消失；新的Pod被调度后，新target立即出现。

### 步骤4：Consul SD配置

Consul SD通过Consul的Catalog API获取服务列表。适用于非K8s环境、混合云部署、VM+容器共存场景。

```yaml
scrape_configs:
  - job_name: 'consul-services'
    consul_sd_configs:
      - server: 'consul:8500'
        services: ['web', 'api', 'cache']
        tags: ['prometheus']
        token: '<your-acl-token>'   # 如果Consul启用了ACL
    relabel_configs:
      - source_labels: [__meta_consul_service]
        target_label: service
      - source_labels: [__meta_consul_tags]
        target_label: consul_tags
      - source_labels: [__meta_consul_node]
        target_label: node
```

- `services`字段限定只发现指定的服务名，留空则发现所有服务。
- `tags`字段做进一步过滤——只有标签匹配的节点才会被采集，相当于Consul版的"opt-in"。
- `__meta_consul_*`标签族携带Consul元数据：服务名、标签、节点名、数据中心、健康状态等。
- 如果Consul启用了ACL，必须提供有效的token。

### 步骤5：DNS SD配置（最轻量）

DNS SD完全不需要外部服务——配置一个域名，Prometheus定期做DNS查询获取IP列表。

```yaml
scrape_configs:
  - job_name: 'dns-services'
    dns_sd_configs:
      - names:
          - 'api.internal.example.com'
        type: 'A'
        port: 8080
        refresh_interval: 30s
```

- `type`支持`A`（IPv4地址）、`AAAA`（IPv6地址）和`SRV`（含端口信息）。
- `refresh_interval`控制DNS查询频率。注意：即使设得很短，实际生效也要等DNS TTL过期。如果DNS TTL是300秒，那么一个IP变更最多要300秒才能被Prometheus感知。
- 适用场景：传统VM部署（每个VM一个服务，通过DNS Round-Robin发现）、负载均衡器后端、CDN边缘节点。
- 局限性：只能拿到IP和端口，没有任何元数据标签。无法区分同一个域名下哪个IP对应哪个实例——**DNS SD的匿名target最多，可观测性最弱。**

### 可能遇到的坑

1. **RBAC权限不足，target列表为空**：这是最常见的问题。K8s SD缺少list/watch权限时，不报错，静默返回空列表。排查第一步永远是`kubectl auth can-i`。
2. **endpoints role只在有健康后端时生成target**：如果Service后面所有Pod都CrashLoopBackOff，Endpoints对象为空，Prometheus完全不采集。此时Prometheus看起来"正常"，实际上服务已经挂了但监控没发现——务必配合Blackbox Exporter做健康检查。
3. **annotation是字符串不是布尔值**：`prometheus.io/scrape: "true"`必须带引号（在YAML中）。写成`prometheus.io/scrape: true`会被K8s当成布尔值，SD的`regex: true`匹配不上。
4. **Consul ACL token过期**：如果Consul的ACL token有过期机制，token失效后SD静默失败。建议在Prometheus中添加`__meta_consul_service`缺失告警。
5. **DNS SD的A记录返回多个IP**：如果一个域名解析出10个IP，DNS SD会生成10个target。这在你不知道后端有多少实例时会"失控"——target数量完全依赖DNS服务器返回值。建议只对有明确实例数的场景用DNS SD。

---

## 四、项目总结

### 四种SD方式对比

| 维度 | K8s SD | Consul SD | DNS SD | File SD |
|------|--------|-----------|--------|---------|
| 动态性 | 实时（watch） | 近实时（long polling） | TTL级别（分钟级） | 取决于文件更新频率 |
| 元数据丰富度 | 极高（label/annotation全量） | 高（service/tag/node/dc） | 无（仅IP+端口） | 自定义（JSON字段） |
| 配置复杂度 | 高（需RBAC+relabel） | 中（需Consul集群） | 极低（几行配置） | 低（需更新机制） |
| 适用场景 | 纯K8s环境 | 混合云/微服务 | 传统VM/负载均衡 | 测试/小规模 |

### K8s SD五种Role选型指南

- **endpoints**（最常用）：采集Service后端Pod。适合绝大多数应用监控场景。自动感知Pod上下线。
- **pod**：直接发现所有Pod IP，不管是否属于Service。适合Pod间没有Service关联的场景，如daemonset的网络监控sidecar。
- **service**：发现Service的ClusterIP。用得少，因为ClusterIP不直接对应可采集端口。适合监控kube-proxy/IPtables指标。
- **node**：发现集群Node。配合node_exporter使用。
- **ingress**：发现Ingress端点。配合Ingress Controller指标采集。

**核心原则**：监控业务应用用endpoints；监控基础设施用node；非标准场景才用pod。

### K8s Annotation最佳实践

| Annotation | 含义 | 默认值 |
|------------|------|--------|
| `prometheus.io/scrape` | 是否采集 | `false`（opt-in） |
| `prometheus.io/port` | 采集端口 | 容器暴露的第一个端口 |
| `prometheus.io/path` | 采集路径 | `/metrics` |
| `prometheus.io/scheme` | 协议（http/https） | `http` |

建议在团队的Helm/基础Chart模板中预置这些annotation，业务方只需声明`prometheus.io/scrape: "true"`即可接入。

### 适用场景总结

- **纯K8s环境** → K8s SD（endpoints role），结合annotation实现opt-in采集。
- **K8s + VM混合部署** → K8s SD + Consul SD双管齐下，两组SD各自负责自己境内的服务。
- **传统VM/物理机环境** → DNS SD + File SD组合，DNS管动态IP、File管元数据。
- **开发/测试环境** → static_configs直接写死，最简单的永远是最好的。

### 常见踩坑经验

**案例1：K8s SD的RBAC权限不到位。** 小张配置了K8s SD后，Prometheus一切正常，日志无报错，但Targets页面就是看不到任何K8s target。排查了三天，最后发现ServiceAccount绑定的还是默认角色，根本没有Pod/Service的list权限。SD在初始化时静默跳过——不加任何错误提示。教训：**配置K8s SD后第一步永远是`kubectl auth can-i`。**

**案例2：endpoints role vs pod role的选择错误。** 王工用pod role监控所有Pod，发现target数量是Pod总数的两倍——因为每个Pod既有eth0网卡又有一个sidecar代理的单独指标端口，pod role把两个IP都列了出来。而endpoints role会根据Service端口定义只暴露正确的端口。教训：**业务应用监控用endpoints，不要用pod。**

**案例3：DNS SD的A记录为多个IP时target数量失控。** 小李把某个域名配成DNS SD，配置时以为后端就3台服务器。有一天运维加了5台服务器到DNS轮询列表，Prometheus的target数从3个飙升到8个，超出scrape_interval，导致Prometheus自身内存OOM。教训：**DNS SD的target数量取决于DNS响应，用之前要确认DNS解析的IP数量可控。**

### 思考题

1. **K8s中一个Deployment有3个Pod副本，用endpoints role和pod role分别会创建几个target？各有什么优缺点？**

2. **如何将K8s Pod的label自动同步为Prometheus的label，而不需要每个团队手动配annotation？**（提示：使用`labelmap` action）

---

*本章完。掌握了K8s/Consul/DNS三种动态服务发现方式后，你的Prometheus才算真正获得了"自动感知"的能力——不再需要手工维护target列表，专注于指标和告警本身。下一章我们将深入Relabeling的高级用法。*
