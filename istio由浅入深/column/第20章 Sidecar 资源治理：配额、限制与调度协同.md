---
title: "第20章 Sidecar 资源治理：配额、限制与调度协同"
part: "第二部分：核心能力篇（第11-22章）"
chapter: 20
---

# 第20章 Sidecar 资源治理：配额、限制与调度协同

## 20.1 项目背景

**Sidecar 不是“免费午餐”**

每个 Pod 增加 `istio-proxy` 后，集群可调度容量与 Namespace 配额都会被消耗。若未在 LimitRange、ResourceQuota 中预留，易出现**注入成功但调度失败**或**节点资源碎片化**。

**与 HPA、VPA 的耦合**

HPA 依据 CPU 扩容时，Sidecar 占用可能被算入工作负载 CPU，引发**过早扩容**；若忽略 Sidecar，又可能**低估节点需求**。需要平台视角统一建模。

## 20.2 项目设计：大师提醒“算账单时别忘了代理”

**场景设定**：小白团队把应用 `requests` 调低以通过配额审核，结果大规模注入后集群出现 Pending。

**核心对话**：

> **小白**：为什么同样的 YAML，注入前能调度，注入后不行？
>
> **大师**：把 Sidecar 的 `requests/limits` 加回 Pod 总量里算一遍。很多团队只算业务容器。
>
> **小白**：能统一给 Sidecar 降配吗？
>
> **大师**：可以，但要监控代理延迟与丢包。资源是性能的上游约束。

## 20.3 项目实战：覆盖 Sidecar 资源

```yaml
metadata:
  annotations:
    sidecar.istio.io/proxyCPU: "500m"
    sidecar.istio.io/proxyMemory: "256Mi"
```

```yaml
# 全局默认（示意，以 IstioOperator/MeshConfig 为准）
apiVersion: install.istio.io/v1alpha1
kind: IstioOperator
spec:
  values:
    global:
      proxy:
        resources:
          requests:
            cpu: 100m
            memory: 128Mi
```

```bash
kubectl describe quota -n production
kubectl top pod -n production
```

## 20.4 项目总结

| 维度 | 详细分析 |
|:---|:---|
| **核心优点** | **可预测调度**；**成本核算透明** |
| **主要缺点** | **过度压降资源影响性能** |
| **典型使用场景** | **大规模集群**、**多租户共享** |
| **关键注意事项** | **LimitRange 默认值**；**DaemonSet 与节点容量** |
| **常见踩坑经验** | **配额只算业务容器**；**忽略 init 容器短暂峰值** |

---

## 编者扩展

> **本章导读**：Sidecar 不是免费的：request/limit 与调度约束要纳入容量模型。

### 趣味角

每个 Pod 多一个 Sidecar，就像每桌客人多一副公筷——卫生好了，但洗碗量和摆台空间都上去了。

### 实战演练

对典型微服务 Pod 统计 `kubectl top pod` 中 app 与 istio-proxy 的 CPU/内存占比；用 `ProxyConfig` 或注解收紧资源后观察 HPA 行为。

### 深度延伸

大连接数场景下 Envoy 内存与 `concurrency` 调优的定性关系。

---

上一章：[第19章 自定义 CA 与证书：企业 PKI 与 Istio 的衔接](第19章 自定义 CA 与证书：企业 PKI 与 Istio 的衔接.md) | 下一章：[第21章 Egress流量管控：安全的出站管理](第21章 Egress流量管控：安全的出站管理.md)

*返回 [专栏目录](README.md)*
