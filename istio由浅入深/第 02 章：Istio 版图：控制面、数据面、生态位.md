# 第 2 章：Istio 版图：控制面、数据面、生态位

**受众提示**：开发重点理解「配置如何到 Envoy」；运维重点理解控制面组件与升级单元；测试重点理解观测与策略生效点。

**实验基线**：[LAB-KIT.md](LAB-KIT.md)。

---

## 1. 项目背景

上一章回答了「为何需要网格」。本章把 **Istio 在体系中的位置**说清楚，避免与 Kubernetes 网络（CNI）、Ingress Controller、云负载均衡混淆。Istio 的数据面通常是 **Envoy**，以 Sidecar 形式与业务容器同 Pod；控制面历史上包含 Pilot、Citadel、Galley 等角色，现代发行版多合并为 **istiod**（控制面单体化简化部署），对外仍通过 Kubernetes CRD（如 `VirtualService`、`Gateway`）声明意图。

推广时团队常问：**「我们是不是又多了一个 Ingress？」**——答案是否定的：Ingress 主要处理**进入集群**的 HTTP 路由；Istio 的 `Gateway` 可承担入口，但核心价值在**全链路**（含东西向）一致策略与身份。另一个高频问题：**「CNI 和 Istio 谁管网络？」**——CNI 管 Pod IP、跨节点转发；Istio 在 L7（及必要时的 L4）上**劫持/代理**应用流量并执行策略。

本章仍为概念章，实战安装从第 3、4 章开始；此处建立心智模型，后续读源码（`pilot/`、`pkg/config`）时不迷路。

---

## 2. 项目设计：大师与小白的对话

**小白**：一张图里 istiod、Ingress、CNI、Service 全有，我眼睛花了。

**大师**：记住分层：**CNI** 让 Pod 互通；**Service** 给稳定虚拟 IP 和 kube-proxy/ipvs 负载均衡；**Istio 数据面**在 Pod 内把流量「截胡」到 Envoy，再走「网格里的服务名」。

**小白**：截胡会不会把 kube-dns 搞乱？

**大师**：应用仍可以 `reviews` 这种短名访问，但解析与路由由 Envoy **按 xDS 配置**完成。运维要习惯看 `istioctl proxy-config`，而不是只 `kubectl get endpoints`。

**小白**：控制面就一个 istiod 吗？

**大师**：对多数部署是。它做配置聚合、证书签发（与 Citadel 职责合并）、向 Sidecar **推送** xDS。外部插件（CA、远程配置）可插，但心智上先记 **istiod + Sidecar** 就够。

**小白**：和 Linkerd、Consul Connect 比呢？

**大师**：选型是另一场战争。推广 Istio 时强调：**社区与集成广度、Envoy 生态、多集群与扩展**。若团队已有强 Consul，不必硬拗；POC 用统一标准（延迟、策略一致性、排障时间）说话。

**要点清单**

1. **数据面**：Envoy Sidecar = 策略执行点。
2. **控制面**：istiod = 配置与证书中枢（实现细节可查发行版说明）。
3. **入口**：`Gateway` + `VirtualService` 常作为北向入口；可与传统 Ingress 并存（边界要设计）。
4. **东西向**：`VirtualService`/`DestinationRule` 等描述服务间路由与韧性。

---

## 3. 项目实战

### 3.1 心智模型 ASCII（可贴到内部 Wiki）

```
[Client] --> [Cloud LB / NodePort] --> [Gateway Pod / Ingress]
                                              |
                    +-------------------------+-------------------------+
                    |  NS: default            |  NS: backend             |
                    v                         v                          v
              +-----------+            +-----------+              +-----------+
              | App + Envoy|  mTLS    | App + Envoy|   metrics   | App + Envoy|
              +-----------+          +-----------+              +-----------+
                    \___________________|_______________________/
                                    istiod (xDS, certs)
```

### 3.2 对照命令（安装后执行，用于建立「对象存在」直觉）

```bash
# 控制面 Deployment（名称因版本略异）
kubectl get deploy -n istio-system

# 核心 CRD 是否存在
kubectl get crd | findstr istio.io

# 任选一个已注入的工作负载，看 Sidecar 容器
kubectl get pod -l app=productpage -o jsonpath="{.spec.containers[*].name}"
```

### 3.3 与源码目录（可选）

在本仓库中，可延伸阅读：`pilot/`（xDS 与推送）、`pkg/config/`（配置模型）。推广阶段不必全员读，架构组可在第 24 章后组织「源码导读」。

---

## 4. 项目总结

**优点**

- 分工清晰：**平台**管 istiod 与网格 CRD，**业务**管 Deployment 与业务 ConfigMap。
- Envoy 生态成熟，扩展（Wasm、Filter）路径明确。

**缺点**

- 组件与 CRD 多，新人**概念负担**重；需配套内部「一页纸」与排障路径（第 23、24 章）。
- 与现有 Ingress 方案**重叠**时若职责不清，易出现「双份路由」。

**适用场景**

- 需要**统一东西向治理**的中大型平台团队。
- 已有 K8s 运维能力，能承担控制面 SLO。

**注意事项**

- 文档与培训中统一术语：**Gateway vs Ingress**，避免口头混用。
- 升级 istiod 时，理解**数据面与控制面版本 skew** 支持范围。

**常见踩坑**

1. **现象**：以为「没建 Gateway 就没有 Istio」。**原因**：东西向仍可能走 Sidecar。**处理**：用 `istioctl proxy-status` 看同步状态。
2. **现象**：把 Istio 当「仅入口网关」用，东西向全绕过。**原因**：未注入或未正确 label。**处理**：回到第 5 章注入与命名空间策略。

---

### 4.1 再谈一个场景：排障时的「信源」

线上出现延迟抖动时，若团队仍只习惯 `kubectl get` 与业务日志，往往会错过 Envoy 层的连接池、重试与 TLS 细节。建立「**先看 proxy-config，再谈代码**」的习惯，不是推卸责任，而是把**观测与配置**分层。推广期可在 oncall Runbook 里把 `istioctl proxy-config route/cluster` 列为标准第二步（详见第 24 章）。

### 4.2 与团队现有组件对齐

若公司已部署 Nginx Ingress、API 网关、服务注册中心，应在架构图上**标注流量穿越顺序**：客户端 → 云 LB → 网关 → Sidecar → 应用。任何一层都可能添加/剥离 Header，影响金丝雀与鉴权。

---

## 附：自测与练习

### 自测题

1. 用自己的话描述数据面与控制面的职责边界。
2. `istiod` 与 Kubernetes API Server 分别解决什么问题？
3. 为什么说「只看 Service 与 Endpoints 不足以解释网格行为」？

### 动手作业

画一张你们当前环境的简化数据路径图（可用白板拍照），标出是否经过 Sidecar 与入口网关。与第 6、9 章对照找缺口。

**延伸阅读**：Istio *Architecture*；[istio-ug.md](../../istio-ug.md) 第 2 节核心组件（术语若过时以官方为准）。
