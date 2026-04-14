---
title: "第18章 DNS 代理与流量捕获细节：为什么“解析对了”仍连错"
part: "第二部分：核心能力篇（第11-22章）"
chapter: 18
---

# 第18章 DNS 代理与流量捕获细节：为什么“解析对了”仍连错

## 18.1 项目背景

**Kubernetes DNS 与 Envoy 服务发现的缝隙**

应用解析 `reviews.default.svc.cluster.local` 看似正确，但 Sidecar 可能使用不同的簇名、子集或 EDS 端点。DNS TTL、搜索域、IPv4/IPv6 双栈也会导致“偶发连错实例”。

**透明流量捕获与 Init 容器**

Istio 通过 iptables 或 eBPF 将流量重定向到 Envoy。若应用绑定 `127.0.0.1` 或绕过 loopback，可能不受治理。理解捕获边界是排查**为什么有的流量没有指标**的关键。

## 18.2 项目设计：大师解释“同名不同路”

**场景设定**：小白在 Pod 内 `nslookup` 正常，但 Kiali 显示一部分请求未经过预期子集。

**核心对话**：

> **小白**：DNS 没问题，为什么路由错？
>
> **大师**：服务网格里**路由决策在 Envoy**，不一定等于你 `curl` 时以为的那个 Cluster。要用 `istioctl proxy-config` 看实际 cluster 名称、端点与健康状态。
>
> **小白**：那应用还要不要用 cluster DNS？
>
> **大师**：要，但要意识到 Sidecar 会拦截出站连接。若你直连 Pod IP，可能绕过部分策略——这也是生产上要约束客户端行为的原因。

## 18.3 项目实战：诊断命令组合

```bash
kubectl exec -it deploy/sleep -c sleep -- nslookup reviews.default.svc.cluster.local
istioctl proxy-config cluster deploy/sleep | grep reviews
istioctl proxy-config endpoint deploy/sleep | grep reviews
kubectl exec -it deploy/sleep -c sleep -- cat /etc/resolv.conf
```

## 18.4 项目总结

| 维度 | 详细分析 |
|:---|:---|
| **核心优点** | **澄清 DNS 与 xDS 的边界**；**缩短网络类故障定位时间** |
| **主要缺点** | **工具输出信息量大**；**需理解 Envoy 命名规则** |
| **典型使用场景** | **跨命名空间调用**、**Headless Service**、**StatefulSet 场景** |
| **关键注意事项** | **搜索域与 FQDN**；**短连接与长连接差异** |
| **常见踩坑经验** | **把 DNS 正常当路由正常**；**忽略 Endpoint 不健康** |

---

## 编者扩展

> **本章导读**：DNS 在网格里常被「二次解释」：解析对了也可能连错上游。

### 趣味角

Sidecar 的 DNS 捕获像给手机装了企业 MDM：你以为拨的是外卖电话，MDM 可能根据策略转接到内部总机。

### 实战演练

对比 Pod 内 `nslookup` 与 `curl -v` 显示解析结果；开启/关闭 DNS 代理相关设置后重复实验。

### 深度延伸

CoreDNS vs Envoy DNS filter 的职责边界；IPv4/IPv6 双栈下的坑。

---

上一章：[第17章 WorkloadEntry：将虚拟机与裸机工作负载纳入网格](第17章 WorkloadEntry：将虚拟机与裸机工作负载纳入网格.md) | 下一章：[第19章 自定义 CA 与证书：企业 PKI 与 Istio 的衔接](第19章 自定义 CA 与证书：企业 PKI 与 Istio 的衔接.md)

*返回 [专栏目录](README.md)*
