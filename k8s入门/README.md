# Kubernetes 专栏

本目录为独立成书的 **40 章 Kubernetes 学习专栏**，基于仓库根目录的 [`k8s-learning-plan.md`](../k8s-learning-plan.md) 拆解、扩写而成，面向尚未系统接触过 Kubernetes、希望从入门走到工程化与平台视角的开发者。

**建议入口**：[专栏导航与前言（00）](00-专栏导航与前言.md)（阅读顺序与学习路线速览） · 本文（完整目录与写作约定）

---

## 作者前言

Kubernetes 的学习曲线之所以陡，往往不是因为对象多，而是因为缺少一条能把「概念 → 对象 → 运行时行为 → 边界条件」串起来的路径。这套专栏尝试用同一种节奏写满 40 章：每一章都用 **项目背景** 讲清问题从哪来，用 **大师 / 小白对话** 把抽象概念落到对话里，用 **项目实战** 给出可跟做的最小闭环，用 **项目总结** 收束优缺点、场景、注意事项和踩坑。

你不必按周次死磕，但若你希望两个月左右走完一轮，可直接对照 [`k8s-learning-plan.md`](../k8s-learning-plan.md) 的周任务与验证清单；专栏各章首行的「对应学习计划……」blockquote 即与该计划对齐的锚点。

---

## 全书结构与阅读约定

每章统一采用以下四段结构：

1. **项目背景**：行业场景、问题如何被推到台前、本章解决哪一类工程判断。
2. **项目设计**：通过 **大师** 与 **小白** 的对话引出主题，降低概念密度。
3. **项目实战**：以主代码片段（YAML / 命令）驱动，强调可观察现象与排障意识。
4. **项目总结**：优点与缺点、适用场景、注意事项、常见踩坑。

**术语与写法约定（全书级）**

- **Kubernetes**：正文首次提及尽量写全称；可与 **K8s** 交替，但不混用多种怪异大小写。
- **etcd**：分布式键值存储项目名称统一为小写 `etcd`。
- **kubectl**：命令行工具名保持 `kubectl`。
- **控制平面**：与节点上的 **kubelet**、**kube-proxy** 等对比时，优先使用「控制平面」四字，避免与「控制面 API」等口语混写造成歧义。
- **API 对象名**：保留英文（如 Deployment、Pod、Service、Ingress），与官方文档一致。
- **章节文件名**：与各章正文一级标题一致，格式为 `第 NN 章：标题.md`（冒号为中文全角 `：`，与标题行一致，便于在资源管理器中按章名检索）。

---

## 学习路线（阶段 × 章节）

下列阶段按 **由浅入深** 划分，可与学习计划中的「周」大致对应；具体以各章首行 blockquote 为准。

| 阶段 | 章节 | 核心主题 |
|------|------|----------|
| 入门与本地环境 | 01–03 | 为何需要 K8s、minikube/kind/k3s、kubectl 与声明式工作流 |
| 工作负载与运行时基础 | 04–12 | Pod、Init、探针、资源与 QoS、Deployment/RS、StatefulSet、DaemonSet、Job/CronJob |
| 服务发现与网络 | 13–18 | Service 全系、Ingress、网络模型、kube-proxy、CNI 选型 |
| 配置与存储 | 19–23 | ConfigMap、Secret、卷、PV/PVC/StorageClass、CSI |
| 调度与多租户治理 | 24–30 | 亲和性、污点、配额与 LimitRange、Namespace、RBAC、ServiceAccount |
| 扩展与安全 | 31–34 | CRD、准入 Webhook、NetworkPolicy、Pod Security / SecurityContext |
| 高可用与变更 | 35–36 | 控制平面与 etcd、升级恢复与 PDB |
| 弹性与可观测 | 37–38 | HPA/VPA/集群伸缩、指标日志追踪 |
| 工程化与收束 | 39–40 | Helm/Operator/Mesh、综合实战 |

---

## 章节目录

- [00. 专栏导航与前言](00-专栏导航与前言.md)
- [01. 云原生与 Kubernetes：为什么不是多装几台虚拟机](<第 01 章：云原生与 Kubernetes：为什么不是多装几台虚拟机.md>)
- [02. 实验集群搭建：minikube、kind、k3s 到底怎么选](<第 02 章：实验集群搭建：minikube、kind、k3s 到底怎么选.md>)
- [03. kubectl 与声明式配置：命令不是重点，状态才是重点](<第 03 章：kubectl 与声明式配置：命令不是重点，状态才是重点.md>)
- [04. Pod：Kubernetes 中最小但不简单的调度单元](<第 04 章：Pod：Kubernetes 中最小但不简单的调度单元.md>)
- [05. Init 容器：主业务启动前，先把准备工作做对](<第 05 章：Init 容器：主业务启动前，先把准备工作做对.md>)
- [06. 探针机制：活着、能接流量、刚启动，三件事不是一回事](<第 06 章：探针机制：活着、能接流量、刚启动，三件事不是一回事.md>)
- [07. 资源请求与限制：调度器看的是请求，内核执行的是限制](<第 07 章：资源请求与限制：调度器看的是请求，内核执行的是限制.md>)
- [08. Deployment：无状态应用的默认交付方式](<第 08 章：Deployment：无状态应用的默认交付方式.md>)
- [09. ReplicaSet 与滚动策略：Deployment 背后的执行现场](<第 09 章：ReplicaSet 与滚动策略：Deployment 背后的执行现场.md>)
- [10. StatefulSet：身份、顺序、存储，一个都不能少](<第 10 章：StatefulSet：身份、顺序、存储，一个都不能少.md>)
- [11. DaemonSet：让每个节点都跑同一种职责](<第 11 章：DaemonSet：让每个节点都跑同一种职责.md>)
- [12. Job 与 CronJob：一次性任务和周期任务的正确打开方式](<第 12 章：Job 与 CronJob：一次性任务和周期任务的正确打开方式.md>)
- [13. Service ClusterIP：给易变 Pod 一个稳定入口](<第 13 章：Service ClusterIP：给易变 Pod 一个稳定入口.md>)
- [14. NodePort、LoadBalancer、ExternalName：服务如何走出集群](<第 14 章：NodePort、LoadBalancer、ExternalName：服务如何走出集群.md>)
- [15. Ingress：七层流量入口不是一个对象，而是一套协作机制](<第 15 章：Ingress：七层流量入口不是一个对象，而是一套协作机制.md>)
- [16. Kubernetes 网络模型：每个 Pod 一个 IP，到底意味着什么](<第 16 章：Kubernetes 网络模型：每个 Pod 一个 IP，到底意味着什么.md>)
- [17. kube-proxy：看不见的转发规则，决定了 Service 是否可用](<第 17 章：kube-proxy：看不见的转发规则，决定了 Service 是否可用.md>)
- [18. CNI 与网络插件选型：Calico、Flannel、Cilium 到底差在哪](<第 18 章：CNI 与网络插件选型：Calico、Flannel、Cilium 到底差在哪.md>)
- [19. ConfigMap：配置与镜像分离，别再让环境差异写死在镜像里](<第 19 章：ConfigMap：配置与镜像分离，别再让环境差异写死在镜像里.md>)
- [20. Secret：它比明文好，但远没有你想象得安全](<第 20 章：Secret：它比明文好，但远没有你想象得安全.md>)
- [21. 卷基础：emptyDir、hostPath 与投射卷该怎么用](<第 21 章：卷基础：emptyDir、hostPath 与投射卷该怎么用.md>)
- [22. PV、PVC、StorageClass：持久化存储为什么要多一层抽象](<第 22 章：PV、PVC、StorageClass：持久化存储为什么要多一层抽象.md>)
- [23. CSI：存储插件化之后，Kubernetes 才真正长出了手脚](<第 23 章：CSI：存储插件化之后，Kubernetes 才真正长出了手脚.md>)
- [24. 调度入门：nodeSelector 与节点亲和，先决定 Pod 去哪台机器](<第 24 章：调度入门：nodeSelector 与节点亲和，先决定 Pod 去哪台机器.md>)
- [25. 污点与容忍：不是所有节点，都欢迎所有 Pod](<第 25 章：污点与容忍：不是所有节点，都欢迎所有 Pod.md>)
- [26. Pod 亲和与反亲和：让服务扎堆，还是刻意打散](<第 26 章：Pod 亲和与反亲和：让服务扎堆，还是刻意打散.md>)
- [27. ResourceQuota 与 LimitRange：共享集群必须有预算意识](<第 27 章：ResourceQuota 与 LimitRange：共享集群必须有预算意识.md>)
- [28. Namespace 与多租户入门：隔离从来不是只建个目录那么简单](<第 28 章：Namespace 与多租户入门：隔离从来不是只建个目录那么简单.md>)
- [29. RBAC：细粒度授权不是锦上添花，而是共享集群的底线](<第 29 章：RBAC：细粒度授权不是锦上添花，而是共享集群的底线.md>)
- [30. ServiceAccount 与 API 访问：Pod 也需要身份，不是只有人需要](<第 30 章：ServiceAccount 与 API 访问：Pod 也需要身份，不是只有人需要.md>)
- [31. CRD：把业务领域模型纳入 Kubernetes API 体系](<第 31 章：CRD：把业务领域模型纳入 Kubernetes API 体系.md>)
- [32. 准入控制与 Webhook：请求进入集群前，先过一遍安检](<第 32 章：准入控制与 Webhook：请求进入集群前，先过一遍安检.md>)
- [33. NetworkPolicy：默认互通的世界里，安全需要主动建立边界](<第 33 章：NetworkPolicy：默认互通的世界里，安全需要主动建立边界.md>)
- [34. Pod Security 与 SecurityContext：不是能跑就算安全](<第 34 章：Pod Security 与 SecurityContext：不是能跑就算安全.md>)
- [35. 高可用控制平面与 etcd：大脑不能只有一份备份](<第 35 章：高可用控制平面与 etcd：大脑不能只有一份备份.md>)
- [36. 升级、恢复与 PDB：变更时刻才最考验平台设计](<第 36 章：升级、恢复与 PDB：变更时刻才最考验平台设计.md>)
- [37. 弹性伸缩：HPA、VPA 与集群伸缩不是一个按钮解决所有问题](<第 37 章：弹性伸缩：HPA、VPA 与集群伸缩不是一个按钮解决所有问题.md>)
- [38. 可观测性三支柱：指标、日志、追踪如何协同定位问题](<第 38 章：可观测性三支柱：指标、日志、追踪如何协同定位问题.md>)
- [39. Helm、Operator 与 Service Mesh：工程化之后，平台能力才开始放大](<第 39 章：Helm、Operator 与 Service Mesh：工程化之后，平台能力才开始放大.md>)
- [40. 综合实战：把前面 39 章串成一个能演示、能部署、能运维的微服务项目](<第 40 章：综合实战：把前面 39 章串成一个能演示、能部署、能运维的微服务项目.md>)

---

## 延伸阅读

- 仓库内学习计划：[Kubernetes 两个月深度学习计划（详细版）](../k8s-learning-plan.md)
- 官方文档：<https://kubernetes.io/docs/>
