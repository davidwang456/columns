# 第27章：Prometheus Operator——K8s原生部署与管理

## 一、项目背景

公司Kubernetes集群已经从最初的1个扩展到5个，运维团队在每个集群都是手动部署Prometheus——Helm install打底，然后手工改prometheus.yml、手动配ServiceMonitor。随着业务团队的增多，每个团队都想接入监控，每天都会有同事在群里@运维小王：“帮忙加个ServiceMonitor”、“能不能帮我们调一下scrape_interval”、“这个relabel规则啥意思帮看看”。小王疲于奔命，但更致命的问题藏在表面之下。

在没有标准化之前，5个集群的Prometheus配置各不一样。有的集群用ServiceMonitor自动发现，有的还在用static_configs手动维护target列表；有的集群采集间隔是15秒，有的是30秒；有的集群重标签规则能正确剔除不需要的label，有的干脆没有配。小王曾在一次跨集群排障时被这种不一致彻底搞懵：“同一个应用的名字和label，在不同集群的Prometheus里完全不一样，这让人怎么查？”

真正点燃导火索的是一个事故：某天凌晨，一个业务团队为了“清理无用资源”，随手删除了两个PrometheusRule CRD对象——对应的告警规则直接消失，整个集群的监控告警出现盲区，直到第二天线上故障没收到告警才被发现。根因在于：这些Prometheus配置资源没有纳入Git管理，谁删了什么、什么时候删的，完全不可追溯。

团队痛定思痛，决定引入Prometheus Operator（kube-prometheus-stack）实现K8s原生的声明式管理。Prometheus Operator通过K8s CRD（Custom Resource Definition）将Prometheus的配置转化为K8s原生资源：Prometheus对象定义实例规格、ServiceMonitor定义采集规则、PrometheusRule定义告警规则、AlertmanagerConfig定义告警路由。Operator Controller持续监听这些CRD的变化，自动生成prometheus.yml并触发Prometheus reload。这样一来，Prometheus的配置管理实现了GitOps化——一切皆YAML，一切可审计，一切可回滚。

更关键的是，Operator模式还解决了传统Helm部署的一个核心矛盾：你通过Helm修改values.yaml升级了Prometheus，但Prometheus的ConfigMap在被Helm覆盖后，任何手工修改都会在下一次Helm升级时被还原。而Operator是持续Reconcile的——它不仅创建资源，还持续修正偏差。如果有人手动改掉了ConfigMap或删掉了StatefulSet的一个Pod，Operator会立刻检测到并自动修复到期望状态。这就把Prometheus从"一次性部署产物"变成了"持续受保障的服务"。

## 二、剧本式交锋对话

**小胖**：“大师，我听说Prometheus Operator能自动生成prometheus.yml？那以前我们手写的配置是不是白学了？”

**大师**：“不是白学，是进化了。你手写的那些scrape_configs、relabel_configs，Operator照样能生成——只不过你不用亲自写prometheus.yml了，而是通过创建K8s CRD对象来声明你要什么。比如你要采集某个应用的/metrics端点，以前你是编辑prometheus.yml加一段scrape_config，现在你只需要创建一个ServiceMonitor对象，剩下的交给Operator。”

**小白**：“等一下，Operator……这个词我听到好多次了。它到底是个什么东西？是不是就是在K8s里跑了一个'管理员'程序？”

**大师**：“可以这么理解。Operator是K8s的一种设计模式，核心逻辑是三段式：**CRD定义期望状态 → Controller监听变化 → Reconcile（调谐）使实际状态向期望状态靠拢**。具体到Prometheus Operator：你创建一个Prometheus CRD对象，声明'我要一个2副本的Prometheus实例，数据保留15天，用50G的存储'——这是你的期望状态。Controller看到这个声明后，会去检查集群里实际的StatefulSet、ConfigMap、Service是不是符合期望。不符合？那就调谐：创建StatefulSet、生成ConfigMap、挂载PVC。这些操作都是自动的，你只需要声明你要什么。”

**小胖**：“我听说kube-prometheus-stack这个Helm Chart会装一堆东西，到底有哪些？”

**大师**：“kube-prometheus-stack是一个全家桶，它包含：**Prometheus Operator**（控制器本身）、**Prometheus**（监控核心）、**Alertmanager**（告警管理）、**Grafana**（可视化）、**Node Exporter**（节点指标采集）、**kube-state-metrics**（K8s对象状态指标）。除此之外还有Prometheus Adapter、Prometheus Node Exporter等。你Helm install那一下，监控全栈基本就齐了。”

**小白**：“那核心CRD之间的关系是什么？ServiceMonitor到底怎么找到要采集的Pod？”

**大师**：“这条链路非常清晰。**Prometheus CRD**声明了Prometheus实例（name、replicas、retention、storage等），其中有一个关键字段是**serviceMonitorSelector**——它定义了'哪些ServiceMonitor属于这个Prometheus实例管理'。**ServiceMonitor**声明了采集规则，它通过**selector**找到K8s Service，再通过Service的selector找到背后的Pod，最终自动生成scrape_configs。链路的起点是Prometheus对象，终点是Pod的/metrics端口。”

**小胖**：“那为什么不继续用static_configs？我写static_configs也写了快一年了，挺熟的。”

**大师**：“因为Pod的IP是动态的。Pod被重建、滚动更新、调度到其他节点，IP全变了。你static_configs写死了IP，Pod一重建就抓不到了。而ServiceMonitor不一样：它通过Service的selector匹配Pod，Service本身就是一个稳定的抽象层——只要Pod的label符合Service的selector，不管Pod漂到哪个Node、IP变成什么，Operator都能自动跟踪变化并更新采集目标。”

**小白**：“那告警规则呢？PrometheusRule是怎么被加载进去的？”

**大师**：“你创建一个PrometheusRule CRD对象，里面写alert规则（promql、for、severity等），给它打上release: monitoring这个label（和serviceMonitorSelector同理），Operator就会发现它，自动将规则内容注入到Prometheus的rule_files路径下。更关键的是，这个YAML文件可以放到Git仓库里管理——任何人想新增或修改告警规则，提一个PR，经过review合入后自动由CI/CD或Flux/ArgoCD apply到集群。规则变更可追溯、可回滚，再也不会出现'谁手滑删了规则'的情况。”

**小胖**：“那如果有些采集目标不在K8s集群里呢？比如集群外有几台VM，上面跑了Node Exporter，ServiceMonitor管不到吧？”

**大师**：“好问题。这时候用**additionalScrapeConfigs**。你可以创建一个Secret，里面放传统的prometheus.yml格式的scrape_configs，然后在Prometheus CRD中引用这个Secret的名字和key。Operator会把这个Secret里的配置追加到生成的prometheus.yml末尾。这种方式弥补了ServiceMonitor的局限性——它只能发现K8s内的资源，集群外的目标还是需要static_configs。”

**小白**：“那像--web.enable-remote-write-receiver、--web.enable-admin-api这些Prometheus启动参数呢？在Operator模式下怎么配？”

**大师**：“这些参数全部通过Prometheus CRD的spec字段来配置，不需要你写命令行。比如enableAdminAPI: true就是--web.enable-admin-api，enableRemoteWriteReceiver: true就是--web.enable-remote-write-receiver，remoteWrite数组就是remote_write配置段。Operator做的就是把CRD中的声明式配置翻译成Prometheus能理解的标识位和配置文件段。”

**小胖**：“我还有个疑问——如果Prometheus CRD定义了replicas: 2，Operator创建了两个StatefulSet Pod，那这两个Prometheus实例之间是什么关系？它们是一个集群吗？”

**大师**：“不是集群。Prometheus本身没有集群模式，两个副本是完全独立的——各自采集、各自存储、各自评估告警。StatefulSet给它们分配了固定序号（prometheus-0和prometheus-1），但它们在逻辑上没有主从关系。高可用的效果是靠'冗余副本 + Alertmanager去重'实现的：两个副本都会发出相同的告警，Alertmanager通过group_wait和group_interval接收告警后，按照alertname+cluster+severity等标签做哈希去重，确保你收到的通知不会有重复。这种模式虽然简单，但在生产环境被广泛应用——它的核心思想是'宁可多评估一次，也不漏掉一条告警'。”

## 三、项目实战

### 环境准备

- Kubernetes集群（minikube/kind/k3s均可）
- Helm 3.x
- kubectl已配置并可访问集群

### 步骤1：使用Helm安装kube-prometheus-stack

```bash
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm repo update

# 查看可配置参数
helm show values prometheus-community/kube-prometheus-stack > values.yaml

# 安装
helm install monitoring prometheus-community/kube-prometheus-stack \
  --namespace monitoring \
  --create-namespace \
  --set prometheus.prometheusSpec.retention=15d \
  --set prometheus.prometheusSpec.storageSpec.volumeClaimTemplate.spec.resources.requests.storage=50Gi \
  --set grafana.adminPassword=admin123
```

其中`retention=15d`设置数据保留15天，`storage=50Gi`通过PVC申请50Gi持久化存储，确保Prometheus重启后历史数据不丢失。

### 步骤2：通过ServiceMonitor采集自定义应用

先创建一个带/metrics端点的示例应用，并暴露Service：

```yaml
# app-deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: sample-app
  namespace: default
spec:
  replicas: 2
  selector:
    matchLabels:
      app: sample-app
  template:
    metadata:
      labels:
        app: sample-app
    spec:
      containers:
        - name: app
          image: nginx:latest
          ports:
            - containerPort: 80
---
apiVersion: v1
kind: Service
metadata:
  name: sample-app
  namespace: default
  labels:
    app: sample-app
spec:
  selector:
    app: sample-app
  ports:
    - name: http
      port: 80
      targetPort: 80
```

再创建ServiceMonitor对象：

```yaml
apiVersion: monitoring.coreos.com/v1
kind: ServiceMonitor
metadata:
  name: sample-app
  namespace: default
  labels:
    release: monitoring
spec:
  selector:
    matchLabels:
      app: sample-app
  endpoints:
    - port: http
      path: /metrics
      interval: 15s
      scrapeTimeout: 10s
  namespaceSelector:
    matchNames:
      - default
```

关键字段解读：
- `selector.matchLabels`：匹配Service的label，这里是`app: sample-app`。Operator通过这个selector找到Service，再通过Service的selector找到背后的Pod。
- `endpoints[].port`：Service上定义的port name（此处为`http`），不是containerPort编号。
- `endpoints[].path`：metrics暴露路径，默认就是`/metrics`。
- `interval/scrapeTimeout`：覆盖全局默认采集间隔和超时。
- `namespaceSelector.matchNames`：指定在哪些namespace中查找匹配的Service。
- **`labels.release: monitoring`**：这是关键！必须与Prometheus CRD中`serviceMonitorSelector.matchLabels`的值一致（kube-prometheus-stack默认用`release`作为匹配label），否则Operator不会发现这个ServiceMonitor。

### 步骤3：声明式告警规则管理（PrometheusRule）

```yaml
apiVersion: monitoring.coreos.com/v1
kind: PrometheusRule
metadata:
  name: app-alerts
  namespace: default
  labels:
    release: monitoring
spec:
  groups:
    - name: app-alerts
      rules:
        - alert: AppDown
          expr: up{job="sample-app"} == 0
          for: 2m
          labels:
            severity: critical
          annotations:
            summary: "应用 {{ $labels.pod }} 宕机"
            description: "Pod {{ $labels.pod }} 的up指标为0，已持续2分钟"
```

PrometheusRule创建后，Operator会自动将其注入到Prometheus的rule_files中。Git管理方式：将上述YAML放入Git仓库，通过CI/CD pipeline或Flux/ArgoCD自动apply到集群，告警规则的任何变更都会经过PR review流程，确保变更可追溯。

### 步骤4：使用additionalScrapeConfigs采集外部目标

当ServiceMonitor无法满足需求（如采集集群外VM上的Node Exporter）时，通过Secret挂载额外配置：

```yaml
# 1. 先创建Secret包含额外采集配置
apiVersion: v1
kind: Secret
metadata:
  name: additional-scrape-configs
  namespace: monitoring
stringData:
  extra-scrape-configs.yaml: |
    - job_name: 'external-node'
      static_configs:
        - targets:
            - '10.0.1.100:9100'
          labels:
            env: 'prod'
            datacenter: 'beijing'
```

然后在values.yaml中引用该Secret：

```yaml
prometheus:
  prometheusSpec:
    additionalScrapeConfigsSecret:
      name: additional-scrape-configs
      key: extra-scrape-configs.yaml
```

### 步骤5：常用的Operator定制配置

以下是values.yaml中常见的定制项及其使用场景：

```yaml
prometheus:
  prometheusSpec:
    # 存储配置
    retention: 15d           # 数据保留时长
    retentionSize: 10GB      # 数据保留最大容量（与retention同时使用时先达到者生效）
    storageSpec:
      volumeClaimTemplate:
        spec:
          accessModes: ["ReadWriteOnce"]
          resources:
            requests:
              storage: 50Gi

    # 资源限制
    resources:
      requests:
        cpu: 500m
        memory: 2Gi
      limits:
        cpu: 2000m
        memory: 4Gi

    # 副本数（高可用）
    replicas: 2

    # 外部标签（多集群区分必备）
    externalLabels:
      cluster: prod-k8s-1
      region: beijing

    # Remote Write（对接长期存储）
    remoteWrite:
      - url: http://victoriametrics:8428/api/v1/write

    # 容忍和亲和性
    tolerations: [...]
    affinity: {...}

    # 开启Admin API（生产环境谨慎开启）
    enableAdminAPI: false
```

### 可能遇到的坑

1. **ServiceMonitor的namespaceSelector**：默认只匹配当前namespace。跨namespace采集需要显式指定`matchNames`或设置`any: true`（需要对应RBAC权限）。
2. **Service和ServiceMonitor的namespace关系**：Service和ServiceMonitor必须在同一个namespace，但ServiceMonitor可以通过namespaceSelector匹配其他namespace的Service（Operator v0.50+支持）。
3. **Helm卸载时PVC不自动删除**：数据会残留。如果重新安装且PVC名称相同，可能挂载到旧数据上导致冲突。
4. **PrometheusRule的group名重复**：两个PrometheusRule的group名相同时，Operator会**合并**它们而不是覆盖——可能导致规则意外叠加，排查时非常费劲。

### 测试验证

```bash
# 确认ServiceMonitor创建成功
kubectl get servicemonitors -A

# 端口转发Prometheus Web UI
kubectl port-forward svc/monitoring-kube-prometheus-prometheus 9090 -n monitoring

# 访问 http://localhost:9090
# Status → Targets：确认sample-app的采集目标出现且状态为UP
# Status → Configuration：查看Operator自动生成的prometheus.yml
# Status → Rules：查看已加载的告警规则
```

## 四、项目总结

### CRD关系链路

```
Prometheus (声明实例)
    │  serviceMonitorSelector
    ▼
ServiceMonitor (声明采集规则)
    │  selector.matchLabels
    ▼
Service (稳定负载均衡抽象)
    │  selector
    ▼
Pod (实际采集目标)
    │  :port/metrics
    ▼
/metrics端点
```

### kube-prometheus-stack组件清单

| 组件 | 功能 |
|------|------|
| Prometheus Operator | CRD控制器，负责Reconcile |
| Prometheus | 指标采集与告警评估核心 |
| Alertmanager | 告警去重、分组、路由、静默 |
| Grafana | 可视化仪表板 |
| Node Exporter | 节点级指标导出（CPU、内存、磁盘等） |
| kube-state-metrics | K8s资源对象状态指标（Pod、Deployment等） |
| Prometheus Adapter | 自定义指标API，供HPA使用 |

### ServiceMonitor vs PodMonitor vs ScrapeConfig

| 方式 | 采集目标 | 灵活性 | 适用场景 |
|------|---------|--------|---------|
| ServiceMonitor | 通过Service匹配Pod | 中等 | 标准K8s微服务 |
| PodMonitor | 直接匹配Pod | 较低 | 无Service的Pod |
| ScrapeConfig（v0.60+） | 任意HTTP端点 | 最高 | 集群外目标或非标准采集 |

### 适用场景

- **纯K8s环境**：所有业务都部署在K8s中，Operator是自然选择——部署、采集、告警、可视化全部声明式管理，零手工维护。
- **多租户平台**：每个租户独立namespace，通过ServiceMonitor+namespaceSelector实现采集隔离，PrometheusRule按namespace分组，RBAC确保各团队只管理自己的规则。
- **GitOps运维模式**：配置即代码，变更走PR流程，审计追踪无忧。结合Flux或ArgoCD，代码仓库就是Prometheus配置的唯一真实来源（Single Source of Truth）。
- **频繁变更采集规则**：团队经常增删监控目标或调整采集参数，声明式管理比手工编辑配置文件更高效、更不容易出错。
- **多集群联邦场景**：每个集群部署一套kube-prometheus-stack，通过externalLabels标记集群身份，上层Thanos或VictoriaMetrics聚合查询——集群级别的监控配置始终一致。

### 注意事项

- Helm升级时注意CRD版本兼容性——Operator主版本升级前先检查CRD是否已同步更新，否则Controller无法正常Reconcile。
- StatefulSet中Prometheus Pod的序号决定了角色：prometheus-0是事实上的"主"实例（通过启动参数中的pod-name区分），prometheus-1是"备"副本。
- 跨namespace采集需要配置对应的RBAC权限，Operator默认只授权了自身namespace。

### 常见踩坑经验

**案例1：serviceMonitorSelector label不匹配**。创建的ServiceMonitor没有被打上`release: monitoring`标签，Prometheus的serviceMonitorSelector默认匹配release label，导致Collector完全忽略了该ServiceMonitor。排查方式是查看Prometheus的Configuration页面——如果找不到对应的采集任务，第一反应就应该是label不匹配。

**案例2：跨namespace采集权限问题**。ServiceMonitor中namespaceSelector设置了`any: true`，但Prometheus ServiceAccount没有其他namespace的get/pod/list权限，Operator生成的target列表始终为空。需要在Prometheus CRD中配置serviceAccount的ClusterRole权限。

**案例3：Helm升级后CRD未更新**。Helm升级了kube-prometheus-stack Chart，但default Helm行为不会更新CRD（除非显式指定）。旧的CRD schema与新版Controller不兼容，Operator日志中持续出现reconcile错误。解决方案是先手动apply新版CRD，再升级Helm Release。

### 思考题

1. **Prometheus Operator如何实现两个Prometheus实例的高可用？哪个实例负责告警评估？**

   Prometheus本身是一个单机程序，所谓的"高可用"实际上是通过部署多个独立副本（replicas: 2）实现的——每个副本独立采集相同目标、独立评估告警规则。这种方式不解决数据一致性（每个副本的TSDB完全独立），但解决了可用性问题。每个副本都会独立评估告警规则并发送告警给Alertmanager，Alertmanager通过grouping和deduplication机制对重复告警去重。不存在"哪个实例负责告警评估"——每个实例都负责，重复的告警由Alertmanager兜底处理。

2. **如何在Prometheus Operator中配置Remote Read同时从VictoriaMetrics和本地TSDB查询数据？**

   在Prometheus CRD的spec中配置`remoteRead`字段，指向VictoriaMetrics的查询端点，同时本地TSDB会作为默认数据源。PromQL查询会合并远端数据和本地数据——如果查询的时间范围在本地retention之内，Prometheus可能同时读取本地和远端数据；如果超出本地retention，则完全依赖远端数据。配置示例：`remoteRead: - url: http://victoriametrics:8428/api/v1/read`。
