# 第30章：Kubernetes监控体系深度实践

## 一、项目背景

公司的Kubernetes集群已经承载了80%的生产流量，从"能用"阶段正式迈入"好用"阶段。然而，运维团队很快撞上了一堵看不见的墙：Prometheus上挂的Grafana面板只能展示Pod的CPU和内存曲线，但当老板问"上周那个一直重启的Deployment到底出了什么问题？"时，所有人大眼瞪小眼。基础监控指标能告诉你"Pod用了多少资源"，却无法回答"为什么这个Pod不是3个副本而是2个在跑？""哪个namespace的PVC快要满了？""昨天凌晨的Job究竟成功了没有？"

这里的痛点在于：Kubernetes是一个"期望态"驱动的系统。你只盯着cAdvisor的容器运行指标，就像只盯着汽车的发动机转速表却不看油量表、胎压表和故障灯——车在跑，但你不知道它还能跑多久。K8s监控远比传统主机监控复杂，需要覆盖至少五个层面：Node（操作系统层）、Pod（容器运行时层）、Deployment/StatefulSet（工作负载控制器层）、Namespace（资源配额层）、PersistentVolume（存储层）以及Event（事件层）。每一层都需要不同的数据源：cAdvisor暴露容器资源指标，kube-state-metrics暴露K8s对象的状态快照，kubelet自身暴露节点维度的运行指标，而Events则通过API Server的事件流捕获那些在指标中瞬间消失的关键信息。

更棘手的是"元信息鸿沟"：`container_cpu_usage_seconds_total`告诉你这个容器用了0.5核CPU，但它不知道这个容器属于哪个Deployment、服务于哪个Service、被哪个HPA管控。这就需要引入kube-state-metrics来填补K8s对象的状态指标空白，将"期望态"与"运行态"对齐。本章的目标，就是带你建立一套完整的K8s分层监控体系，用USE和RED方法论武装你的K8s运维能力。

## 二、剧本式交锋对话

> 场景：监控组例会。大屏幕上投着一张大饼图——"集群CPU使用率32%"，看起来一切正常。但小胖眉头紧锁。

**小胖**："大师，我总觉得哪里不对劲。我们的Grafana面板显示CPU才32%，但昨天有个业务方投诉说他们的服务响应超时。我上去一看，那个Deployment设定的副本数是3，实际在跑的只有2个。Prometheus上完全看不到这个信息！"

**小白**："对啊对啊！我也有同感。上次有个Pod被OOMKilled了三次，重启了又挂，挂了又启，最后还是业务方自己发现的。我们的CPU、内存曲线看起来都很平稳啊——因为Pod一OOM就被杀了，指标就断了，根本抓不住异常。"

**大师**：（放下手中的保温杯）"你们的问题本质上是同一个：**把K8s监控等同于容器资源监控了**。K8s监控有三个核心数据源，各自有不同的职责。"

大师站起来走到白板前，画出三个圈。

"第一个是**cAdvisor**——它嵌入在每个kubelet里，负责采集每个容器的CPU、内存、IO和网络指标。你们看到的CPU/内存曲线就来自它。cAdvisor的回答是：'这个Pod现在用了200MB内存。'

"第二个是**kube-state-metrics**——它监听K8s API Server，把对象状态转换成Prometheus指标。小胖你说的'Deployment期望3个副本实际跑2个'，就是`kube_deployment_spec_replicas`和`kube_deployment_status_replicas_available`不一致。kube-state-metrics的回答是：'这个Deployment想要3个，但只有2个健康。'

"第三个是**kubelet自身暴露的指标**，比如节点上跑了多少Pod、每个PV还剩多少磁盘空间。它回答的是：'这个节点还能再调度10个Pod。'"

**小胖**："那我是不是只装kube-state-metrics就够了？它也能告诉我Pod的CPU用了多少吧？"

**大师**："这就是最常见的误解。kube-state-metrics**绝不采集容器的CPU和内存**。它只做一件事：把API Server里的K8s对象（Deployment、Pod、Job、PVC等）拍个快照，转化成指标。Pod用了多少CPU？那是cAdvisor的活。为什么这样分工？因为cAdvisor的数据来自Linux cgroup，是运行时实时数据；kube-state-metrics的数据来自etcd中的对象状态，是声明式快照。两者路径不同、延迟不同、语义不同，职责必须分明。"

**小白**："那我在Grafana上该按什么思路来设计面板呢？现在都是一堆散乱的图。"

**大师**："两个方法论可以帮你。**USE方法论**——Utilization（利用率）、Saturation（饱和度）、Errors（错误数）——适用于Node和Pod这类资源型对象。**RED方法论**——Rate（QPS）、Errors（错误率）、Duration（延迟）——适用于Service这种请求驱动型对象。把二者结合，你的监控就有了骨架。"

大师继续写道："比如，用USE思路看一个Node：
- U：CPU利用率超过80%了吗？
- S：节点负载（load1）超过CPU核数了吗？那说明有进程在排队。
- E：节点Ready状态是否为true？

用RED思路看一个Service：
- R：该Service对应Pod的QPS是多少？
- E：这些Pod的容器重启率是否大于0？5xx错误数在涨吗？
- D：P99延迟是否超过了SLA？"

**小胖**："那Events呢？上次Pod OOMKilled，指标里完全看不出原因。"

**大师**："Events是K8s的'黑匣子'。Pod被OOMKilled时，内核瞬间杀掉进程，cAdvisor的指标采样（默认15秒一次）大概率抓不到那一瞬间的内存飙升。但你可以在kube-state-metrics v2+中启用Event指标，或者用kubernetes-event-exporter把Warning级别的Event转成Prometheus指标——这样'Pod OOMKilled'、'ImagePullBackOff'、'FailedMount'这些关键事件就能触发告警了。"

**小白**：（眼睛一亮）"那我明白了！监控大盘应该按Cluster→Namespace→Workload→Pod分层设计，每一层用不同的指标源来回答不同的问题！"

**大师**："完全正确。而且你还要额外关注存储层——`kubelet_volume_stats_used_bytes`告诉你PV还剩多少空间，`kube_persistentvolumeclaim_resource_requests_storage_bytes`告诉你要了多少。两者相除>80%？赶紧扩容。"

## 三、项目实战

### 环境准备

本章延续第27章的环境，确保以下组件可用：
- Kubernetes集群（minikube / kind / k3s 均可）
- kube-prometheus-stack（含Prometheus、Grafana、kube-state-metrics、cAdvisor）
- kubectl已配置完毕

### 步骤1：理解三大数据源的指标结构

#### 数据源1——cAdvisor（通过kubelet暴露）

cAdvisor是容器资源指标的终极来源，它是kubelet的一部分，每个节点上都有一个实例。关键指标：

```promql
# Pod CPU累计使用秒数（rate后得到瞬时CPU用量）
container_cpu_usage_seconds_total

# Pod实际内存占用（含page cache但不含inactive file，最接近OOM判断标准）
container_memory_working_set_bytes

# Pod网络收发字节数
container_network_receive_bytes_total
container_network_transmit_bytes_total

# 容器文件系统使用量
container_fs_usage_bytes
```

> **重要**：`container_memory_working_set_bytes` 是 Kubernetes OOMKiller 的计算依据。不要用 `container_memory_usage_bytes`，后者包含了可以被回收的 inactive file cache，会导致告警阈值不准确。

#### 数据源2——kube-state-metrics

kube-state-metrics是一个独立的Deployment，它Watch API Server中各类K8s对象的变化，并将状态快照暴露为Prometheus指标。关键指标：

```promql
# Deployment可用副本数
kube_deployment_status_replicas_available

# Pod状态（Running / Pending / Failed / Succeeded）
kube_pod_status_phase

# Node状态（condition="Ready"时，status为true/false）
kube_node_status_condition

# PVC申请的存储容量
kube_persistentvolumeclaim_resource_requests_storage_bytes

# Job成功/失败次数
kube_job_status_succeeded
kube_job_status_failed

# 容器重启累计次数
kube_pod_container_status_restarts_total
```

#### 数据源3——kubelet metrics

kubelet自身也通过 `/metrics` 端点暴露节点级指标：

```promql
# 节点上正在运行的Pod数
kubelet_running_pods

# PV可用空间
kubelet_volume_stats_available_bytes

# 容器运行时操作耗时
kubelet_runtime_operations_duration_seconds
```

### 步骤2：构建USE方法论驱动的Node监控

#### USE—Node维度

```promql
# U (Utilization) — CPU利用率
sum(
  rate(node_cpu_seconds_total{mode!="idle"}[5m])
) by (node) /
sum(machine_cpu_cores) by (node) * 100

# S (Saturation) — CPU饱和度（load1/核数 >1 表示进程排队）
avg(node_load1) by (node) /
sum(machine_cpu_cores) by (node)

# E (Errors) — 节点不可用
kube_node_status_condition{condition="Ready", status!="true"}
```

#### RED—Service维度

```promql
# R (Rate) — 服务QPS（需应用暴露histogram指标）
sum(rate(http_requests_total{namespace="prod"}[5m])) by (service)

# E (Errors) — 容器重启率（侧面反映服务健康）
rate(kube_pod_container_status_restarts_total{namespace="prod"}[5m]) > 0

# D (Duration) — P99延迟
histogram_quantile(0.99,
  sum(rate(http_request_duration_seconds_bucket{namespace="prod"}[5m])) by (le)
)
```

> 注意：Duration指标依赖应用自身的instrumentation（如Prometheus client library暴露的histogram），并非K8s原生提供。

### 步骤3：K8s资源状态监控（kube-state-metrics实战）

#### 场景1：Deployment副本数一致性检查

```promql
# 期望副本数 ≠ 可用副本数 → 告警
kube_deployment_spec_replicas
  !=
kube_deployment_status_replicas_available
```

与之配合的滚动更新状态检查：

```promql
# 滚动更新未完成
kube_deployment_status_observed_generation
  !=
kube_deployment_metadata_generation
```

#### 场景2：Pod资源浪费分析

```promql
# CPU Request利用率 < 20% → Request设太高，浪费资源
avg(rate(container_cpu_usage_seconds_total[5m])) by (pod)
  /
avg(kube_pod_container_resource_requests{resource="cpu"}) by (pod) < 0.2

# 内存Request利用率 < 20%
avg(container_memory_working_set_bytes) by (pod)
  /
avg(kube_pod_container_resource_requests{resource="memory"}) by (pod) < 0.2
```

这个查询是成本优化的核心——它能找出哪些Pod的Request设得过高，白白锁定了可调度资源。

#### 场景3：PVC使用率预警

```promql
# PVC已使用空间 / 申请容量 > 80%
kubelet_volume_stats_used_bytes
  /
kube_persistentvolumeclaim_resource_requests_storage_bytes > 0.8
```

> **坑**：这两个指标的label体系不同。`kubelet_volume_stats_used_bytes` 有 `persistentvolumeclaim` 标签，而 `kube_persistentvolumeclaim_resource_requests_storage_bytes` 有 `persistentvolumeclaim` 标签——需要确保匹配关系。如有不一致，使用 `label_replace()` 对齐。

#### 场景4：Job失败监控

```promql
# 最近1小时内失败的Job
increase(kube_job_status_failed[1h]) > 0

# CronJob最近一次执行失败
kube_cronjob_status_last_schedule_time
  and
kube_job_status_failed > 0
```

### 步骤4：K8s Events监控

Pod被OOMKilled时，`kube_pod_container_status_restarts_total` 会递增，但你永远不知道原因。Events才是真相的载体。

推荐使用 [kubernetes-event-exporter](https://github.com/resmoio/kubernetes-event-exporter)，它可以Watch API Server的Events流，按规则过滤后导出为Prometheus指标或Webhook告警。

```bash
helm repo add bitnami https://charts.bitnami.com/bitnami
helm install event-exporter bitnami/kubernetes-event-exporter \
  --set config.receivers[0].name=stdout \
  --set config.receivers[1].name=prometheus \
  --set config.route.routes[0].match[0].kind=Pod \
  --set config.route.routes[0].match[1].reason=OOMKilling
```

**关键Events告警规则：**

```yaml
groups:
  - name: k8s-events
    rules:
      - alert: PodOOMKilled
        expr: increase(kube_event_total{reason="OOMKilling"}[5m]) > 0
        for: 1m
        labels:
          severity: critical
        annotations:
          summary: "Pod {{ $labels.namespace }}/{{ $labels.name }} OOMKilled"

      - alert: ImagePullFailure
        expr: increase(kube_event_total{reason="ImagePullBackOff"}[5m]) > 0
        for: 1m
        labels:
          severity: warning

      - alert: NodeUnhealthy
        expr: increase(kube_event_total{reason="NodeNotReady"}[5m]) > 0
        for: 1m
        labels:
          severity: critical
```

> **关键坑**：Event exporter如果不过滤，会捕获集群中所有Event（包括Normal级别），高负载集群每分钟可能产生数千条Event，直接打爆Prometheus的内存。**务必配置filter只采集Warning级别或指定reason的Event**。

### 步骤5：构建K8s监控分层大盘

在Grafana中按以下结构设计一张"K8s全局监控大盘"：

**第一行——Cluster概览（Stat面板）**

```
集群节点数:     count(kube_node_info)
总Pod数:        count(kube_pod_info)
NotReady节点:   count(kube_node_status_condition{condition="Ready", status="true"} == 0)
Pod重启速率:    sum(rate(kube_pod_container_status_restarts_total[5m]))
```

**第二行——Namespace视图（Table面板）**

按namespace分组，展示每个ns的CPU使用 vs Request vs Limit、内存使用 vs Request vs Limit。设置 `$namespace` 变量联动，点击即可下钻。

```promql
# 各namespace的CPU使用总量
sum by (namespace) (
  rate(container_cpu_usage_seconds_total{container!=""}[5m])
)
```

**第三行——工作负载视图（Time series + Table）**

- Deployment可用副本 vs 期望副本的趋势图
- Pod重启次数Top5排行榜
- StatefulSet / DaemonSet的健康状态

```promql
# Top5重启最频繁的Pod
topk(5,
  sum by (pod, namespace) (
    rate(kube_pod_container_status_restarts_total[5m])
  )
)
```

**第四行——存储和网络（Gauge + Time series）**

```promql
# PVC使用率
(kubelet_volume_stats_used_bytes / kubelet_volume_stats_capacity_bytes) * 100

# Ingress QPS（需部署ingress-nginx并开启metrics）
sum(rate(nginx_ingress_controller_requests[1m])) by (ingress)
```

### 测试验证

```bash
# 1. 确认kube-state-metrics正常工作
kubectl port-forward -n monitoring svc/kube-state-metrics 8080:8080
curl -s http://localhost:8080/metrics | grep "kube_pod_info" | wc -l
# 应该看到集群中所有Pod的数量

# 2. 手动删除一个Pod，观察状态变化
kubectl delete pod <pod-name> -n <namespace>
# 在Prometheus中查询：
# kube_pod_status_phase{namespace="<namespace>", phase="Running"}

# 3. 创建并删除Job，观察状态指标
kubectl create job test-job --image=busybox -- echo "hello"
kubectl delete job test-job
# 查询：kube_job_status_succeeded 或 kube_job_status_failed
```

### 可能遇到的坑

| 坑 | 现象 | 解决方案 |
|---|---|---|
| Label不一致 | kube-state-metrics用 `node`，cAdvisor用 `kubernetes_node` | 用 `label_replace()` 在PromQL中对齐 |
| Memory指标选错 | OOM告警阈值不准 | 使用 `container_memory_working_set_bytes`，它是OOMKill的计算依据 |
| kube-state-metrics版本 | v1→v2指标名完全重写 | 升级前确认版本，更新所有告警规则中的指标名 |
| Event exporter无过滤 | Prometheus OOM | 配置filter只接收Warning级别或特定reason |
| kube-state-metrics部署模式 | ns级别部署缺少集群级指标 | 集群级别用clusterRole部署，ns级别用role部署 |

## 四、项目总结

### K8s监控三层数据源体系

```
┌──────────────────────────────────────────────┐
│                Grafana 统一大盘                │
├──────────────┬───────────────┬────────────────┤
│  cAdvisor    │ kube-state-   │   kubelet +    │
│  (容器资源)   │  metrics      │ Event Exporter │
│              │ (对象状态)     │  (节点+事件)    │
├──────────────┼───────────────┼────────────────┤
│ CPU/内存/IO  │ Deployment副本 │ PV空间/运行Pod数│
│ 网络流量      │ Pod状态/Job   │ OOMKilled/Pull │
│ 文件系统      │ PVC容量/HPA   │ NodeNotReady   │
└──────────────┴───────────────┴────────────────┘
```

### USE/RED × K8s 应用对照表

| 方法论 | 维度 | Node层 | Pod层 | Deployment层 | Service层 |
|--------|------|--------|-------|-------------|-----------|
| USE | U | CPU利用率 | CPU利用率 | — | — |
| USE | S | Load/CPU核数 | Throttling | 副本数不足 | — |
| USE | E | NotReady | CrashLoop | 更新失败 | — |
| RED | R | — | — | — | QPS |
| RED | E | — | 重启次数 | — | 5xx率 |
| RED | D | — | — | — | P99延迟 |

### 关键指标速查表

| 对象 | 最常用指标（Top 5） |
|------|-------------------|
| **Node** | `node_cpu_seconds_total`, `node_load1`, `machine_cpu_cores`, `kube_node_status_condition`, `node_memory_MemAvailable_bytes` |
| **Pod** | `container_cpu_usage_seconds_total`, `container_memory_working_set_bytes`, `kube_pod_status_phase`, `kube_pod_container_status_restarts_total`, `kube_pod_container_resource_requests` |
| **Deployment** | `kube_deployment_spec_replicas`, `kube_deployment_status_replicas_available`, `kube_deployment_status_replicas_updated`, `kube_deployment_metadata_generation`, `kube_deployment_status_observed_generation` |
| **PVC** | `kube_persistentvolumeclaim_resource_requests_storage_bytes`, `kubelet_volume_stats_used_bytes`, `kubelet_volume_stats_available_bytes`, `kubelet_volume_stats_capacity_bytes`, `kube_persistentvolume_status_phase` |
| **Ingress** | `nginx_ingress_controller_requests`, `nginx_ingress_controller_ingress_upstream_latency_seconds`, `nginx_ingress_controller_connect_time_seconds`, `kube_ingress_info`, `nginx_ingress_controller_bytes_sent` |

### 适用场景
- **集群运维**：Node状态、Pod重启、Events告警
- **容量规划**：PVC使用趋势、Node资源水位
- **成本优化**：找出Request/Usage比值过低的Pod（资源浪费识别）
- **故障定位**：通过Deployment副本数 + Events快速定位异常根因

### 注意事项
1. cAdvisor指标有高基数风险——每个Pod的每个container都有独立的`container_*`指标标签组合，大规模集群（5000+ Pod）务必配置 `honor_labels` 和适当的relabeling，必要时降低采集频率。
2. kube-state-metrics部署模式选择：集群级别部署（单个实例Watch全集群）能提供全局视角但内存占用高；命名空间级别部署适合多租户隔离。
3. K8s版本升级可能导致API废弃，间接影响kube-state-metrics的指标生成——升级前务必阅读release notes。

### 常见踩坑经验

**案例1：kube-state-metrics v1 → v2 指标名大改版**
> 某团队从v1.9升级到v2.4后，所有告警规则失效。原因是v2中 `kube_deployment_status_replicas` 改名为 `kube_deployment_status_replicas_available`，类似的改名涉及上百个指标。**教训**：升级kube-state-metrics前，先在测试环境导出一份完整指标列表做diff。

**案例2：cAdvisor的memory指标选错**
> 运维同学设置了 `container_memory_usage_bytes > 80%` 的门槛阈值，结果频繁误报。实际原因是 `container_memory_usage_bytes` 包含了inactive file cache（可被内核回收），而OOMKiller只看 `working_set`。切换到 `container_memory_working_set_bytes` 后误报消失。

**案例3：Namespace级别的kube-state-metrics导致集群级指标缺失**
> 某多租户团队在每个namespace部署了独立的kube-state-metrics（只Watch本ns），结果集群级别的 `kube_node_status_condition` 指标完全缺失，NodeNotReady告警失效。**教训**：部署架构上需要区分——Node、PV等集群级对象必须由集群级别的kube-state-metrics采集。

### 思考题

1. **如何用Prometheus监测K8s HPA的扩容行为？需要哪些指标？**
> 提示：关注 `kube_hpa_spec_max_replicas`（最大副本数）、`kube_hpa_spec_min_replicas`（最小副本数）、`kube_hpa_status_current_replicas`（当前副本数）、`kube_hpa_status_desired_replicas`（期望副本数），以及触发扩容的源指标（如 `container_cpu_usage_seconds_total` 与 `kube_pod_container_resource_requests` 的比值）。

2. **K8s中有个Pod频繁重启但从未进入CrashLoopBackOff状态，如何在Grafana中监控这种情况？**
> 提示：CrashLoopBackOff是连续重启且间隔递增才触发。如果Pod在每分钟重启一次但每次运行超过10秒（CrashLoop阈值），则永远不会进入CrashLoopBackOff。需要监控 `rate(kube_pod_container_status_restarts_total[5m])` 并结合 `changes(kube_pod_container_status_restarts_total[5m])` 来检测高频重启，而非仅依赖Pod status phase。
