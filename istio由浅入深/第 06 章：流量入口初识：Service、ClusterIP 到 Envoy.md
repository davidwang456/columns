# 第 6 章：流量入口初识：Service、ClusterIP 到 Envoy

**受众提示**：开发建立「一次请求」的完整路径；运维理解抓包与端口；测试理解断言点（客户端、网关、Sidecar、应用）。

**实验基线**：[LAB-KIT.md](LAB-KIT.md)。

---

## 1. 项目背景

有了 Sidecar 后，**同一 Service 名**在应用视角仍是 Kubernetes 服务发现，但在数据面会先进入 **Envoy** 再转发。理解「ClusterIP → Envoy listener → cluster → upstream」有助于：解释延迟、理解为何 `kubectl` 看到的 endpoints 与网格路由不一致、以及使用 `istioctl proxy-config` 时知道该看什么对象。

本章用**口头路径**与**最小命令**建立直觉，为第 7～9 章的 `VirtualService`/`Gateway` 打地基。

---

## 2. 项目设计：大师与小白的对话

**小白**：我 `curl` 同一个 Service DNS，有 Sidecar 和没有差在哪？

**大师**：没有 Sidecar 时，kube-proxy 把流量打到后端 Pod；有 Sidecar 时，**出站先进本地 Envoy**，由 xDS 决定实际 cluster 与负载均衡策略。

**小白**：那 ClusterIP 还有用吗？

**大师**：**有**。它仍是服务发现与虚拟 IP 的锚点；网格在其之上**叠加**路由与策略。

**小白**：为什么 latency 多了 0.x ms？

**大师**：**多一跳 + 策略执行**。是否接受看 SLO；优化手段包括连接池、keep-alive、减少不必要遥测（第 33 章）。

**小白**：我从集群外访问呢？

**大师**：通常走 **Ingress / Gateway / NodePort** 到集群内，再进 Sidecar。**入口与东西向**配置对象不同（第 9 章）。

**要点清单**

1. **Sidecar 是本地代理**，出站/入站默认拦截。
2. **服务名**仍是应用寻址方式；**路由规则**来自 Istio CRD + xDS。
3. 排障分层：**网络通不通** → **Envoy 配置对不对** → **应用逻辑**。

---

## 3. 项目实战

前置：已安装 Istio 且 Bookinfo 运行（第 4、5 章）。

### 3.1 集群内访问（无网关）

```bash
kubectl exec deploy/productpage -c productpage -- curl -sS -o /dev/null -w "%{http_code}\n" http://reviews:9080/reviews/1
```

### 3.2 查看 Envoy listener（节选）

```bash
export POD=$(kubectl get pod -l app=productpage -o jsonpath="{.items[0].metadata.name}")
istioctl proxy-config listener $POD -n default
```

### 3.3 查看 cluster

```bash
istioctl proxy-config cluster $POD -n default | findstr reviews
```

### 3.4 对照 Service

```bash
kubectl get svc reviews -o wide
kubectl get endpoints reviews -o yaml
```

观察：Kubernetes endpoints 与 Envoy cluster 中 upstream 地址应对应（细节受 subset 等影响）。

---

## 4. 项目总结

**优点**

- 路径清晰后，**指标与日志**能对应到具体 hop。
- 团队语言统一：**listener/route/cluster**。

**缺点**

- 初次接触 xDS 概念多，需要**刻意练习** `proxy-config`。
- 多网络/多集群时路径更绕（第 17、18、35 章）。

**适用场景**

- 排障培训、**性能基线**测量、与中间件团队对齐。

**注意事项**

- 不要**用 `curl` 成功**反推「网格策略一定正确」——要对照 `VirtualService` 是否命中。
- IPv6、双栈、headless Service 有特例，生产需专项验证。

**常见踩坑**

1. **现象**：直连 Pod IP 能通，走 Service 不行。**原因**：策略、subset、mTLS 等组合问题。**处理**：分层验证。
2. **现象**：`proxy-config` 为空或异常。**原因**：Sidecar 未连上 istiod。**处理**：`istioctl proxy-status`。
3. **现象**：延迟抖动。**原因**：连接池默认值或 TLS 握手。**处理**：调整 DR 与连接池（第 8、33 章）。

---

### 4.1 再谈一个场景：headless Service

Headless Service 在网格中的行为与普通 ClusterIP 有差异，常导致「我以为会负载均衡」的误解。推广到中间件团队时，要提前声明：**不要假设所有 Service 类型在网格里表现一致**，涉及有状态与分区路由时要专项评审。

### 4.2 与性能基线

建立「同 QPS 下直连 Pod IP vs 走 Service vs 走 Sidecar」三段对比的**实验方法**，避免口头争论。注意测试方法要公平（连接复用、payload 一致）。

---

## 附：自测与练习

### 自测题

1. 解释 listener、route、cluster 在排障时的阅读顺序。
2. ClusterIP 在网格中仍然起什么作用？
3. 为什么说「curl 成功」不足以证明路由策略正确？

### 动手作业

对 Bookinfo 任一 Pod 导出 `proxy-config route` 片段，标注与 `VirtualService` 的对应关系（可涂敏）。

**延伸阅读**：Envoy 官方 *Architecture*（概览）；第 24 章 `istioctl` 全家桶。

---

### 4.5 本章小结：把知识变成「可执行」

推广 Istio 时，最容易失败的不是「不会配」，而是「配了没人敢动」。建议每章学完后，在团队看板增加一张卡片：**谁能在生产执行与本章相关的变更**、**需要哪些审批**、**回滚命令是什么**。技术文档若不能映射到流程，就仍是幻灯片。

### 4.6 与第 24、31、36 章的联动提示

- **排障**：把 `istioctl analyze` 与 `proxy-config` 写进 oncall 第一步（第 24 章）。
- **交付**：把安装与 values 纳入 GitOps（第 31 章）。
- **治理**：把试点范围、预算与里程碑写成公开路线图（第 36 章）。
