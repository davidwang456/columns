# 第 7 章：VirtualService：路由与权重

**受众提示**：开发编写路由与版本分流；运维发布金丝雀；测试验证权重与 Header 命中。

**实验基线**：[LAB-KIT.md](LAB-KIT.md)。

---

## 1. 项目背景

`VirtualService`（VS）是 Istio 流量管理的**核心对象**之一：把发往某个 Host（或网关上的路由）的请求，按**匹配条件**导向不同子集（subset），并支持**权重**、**重试**、**超时**等。与 Kubernetes Service 不同，VS 描述的是**意图**（Intent），由控制面翻译成 Envoy 路由。

推广时最容易混淆的是：**VS 绑在谁身上**——常见是 mesh 内服务或 `Gateway` 上的 HTTP 路由。本章用 Bookinfo 的 `reviews` 服务做 **v1/v2/v3 权重拆分**，可观察星星评分差异。

---

## 2. 项目设计：大师与小白的对话

**小白**：有了 Deployment 的 v1/v2，为啥还要 VirtualService？

**大师**：Deployment 只能**副本比例**；VS 可以**按 Header、用户、URI** 分流，且与网格观测、安全策略统一。

**小白**：权重加起来必须 100 吗？

**大师**：Istio 会**归一化**到各目的地；习惯上写清楚 100 更利于 Code Review。

**小白**：VS 和 Service 同名冲突吗？

**大师**：VS 的 `hosts` 常写**服务 DNS 名**，是**附加层**而非替代 Service。

**小白**：我改了 VS，多久生效？

**大师**：通常秒级；若未生效，先看 **Sidecar 是否同步**（`proxy-status`）。

**要点清单**

1. **hosts** 与 **http.route.destination** 要对应到 **DestinationRule 的子集**（第 8 章）。
2. 多规则时注意**匹配顺序**与**优先级**。
3. 金丝雀与监控结合（第 15 章）。

---

## 3. 项目实战

前置：Bookinfo 已部署；`reviews` 有 v1/v2/v3 版本。

### 3.1 创建 DestinationRule（子集定义，可与第 8 章合并练习）

```yaml
apiVersion: networking.istio.io/v1beta1
kind: DestinationRule
metadata:
  name: reviews
spec:
  host: reviews
  subsets:
  - name: v1
    labels:
      version: v1
  - name: v2
    labels:
      version: v2
  - name: v3
    labels:
      version: v3
```

### 3.2 VirtualService：按权重分流

```yaml
apiVersion: networking.istio.io/v1beta1
kind: VirtualService
metadata:
  name: reviews
spec:
  hosts:
  - reviews
  http:
  - route:
    - destination:
        host: reviews
        subset: v1
      weight: 80
    - destination:
        host: reviews
        subset: v2
      weight: 20
```

```bash
kubectl apply -f reviews-dr-vs.yaml
```

### 3.3 验证

多次刷新 Bookinfo 页面或通过脚本访问 `productpage`，观察 `reviews` 评分变化（v1 无星、v2/v3 有星）。

```bash
kubectl exec deploy/productpage -c productpage -- sh -c "for i in 1 2 3 4 5; do curl -sS http://reviews:9080/reviews/1 | head -c 120; echo; done"
```

### 3.4 排障

```bash
istioctl get virtualservice reviews -o yaml
istioctl proxy-config route deploy/productpage -n default
```

---

## 4. 项目总结

**优点**

- **声明式金丝雀**与**灰度**能力强；
- 与观测、认证策略同一套模型。

**缺点**

- 规则复杂后**可读性**下降，需要**命名规范**与**注释**。
- 错误配置可导致**流量黑洞**或**意外全切**。

**适用场景**

- 多版本共存、灰度发布、按用户/租户路由。

**注意事项**

- 变更前在**预发**用相同 labels 验证 subset 是否命中。
- 与 **HPA** 联动时注意版本标签一致性。

**常见踩坑**

1. **现象**：权重不生效。**原因**：subset 标签与 Pod 不一致。**处理**：`kubectl get pod --show-labels`。
2. **现象**：503 激增。**原因**：子集无可用后端。**处理**：检查 `DestinationRule` 与 `Deployment`。
3. **现象**：只命中 v1。**原因**：另有更高优先级规则或 `match` 未命中。**处理**：`istioctl proxy-config route` 排序检查。

---

### 4.1 再谈一个场景：规则优先级

当多条 `http` 路由并存时，**匹配顺序**决定了线上行为。建议在评审中强制要求：每条规则写明「为何需要」与「兜底策略」，并在预发用**固定 Header** 做矩阵测试，避免「只在生产偶发」。

### 4.2 与监控联动

金丝雀权重变更后，必须在 Grafana 上同时看 **source/destination workload** 维度，确认流量确实按预期拆分，而不是只盯着 Deployment 副本数。

---

## 附：自测与练习

### 自测题

1. `VirtualService` 的 `hosts` 通常写什么？与 Kubernetes Service 名称关系是什么？
2. 权重路由与 Deployment 副本比例路由有何不同能力边界？
3. 修改 VS 后应使用哪些命令验证数据面生效？

### 动手作业

为 `reviews` 配置 90/10 权重并在脚本中采样 200 次，统计比例是否接近（允许误差范围写在实验记录里）。

**延伸阅读**：Istio *Traffic Management* / VirtualService；第 8、14 章。
