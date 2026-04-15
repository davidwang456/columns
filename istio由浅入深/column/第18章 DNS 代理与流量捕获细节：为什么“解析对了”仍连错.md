---
title: "第18章 DNS 代理与流量捕获细节：为什么“解析对了”仍连错"
part: "第二部分：核心能力篇（第11-22章）"
chapter: 18
---

# 第18章 DNS 代理与流量捕获细节：为什么“解析对了”仍连错

## 18.1 项目背景

**业务场景（拟真）：`nslookup` 对、Kiali 子集却不对**

应用在 Pod 内解析 `reviews.default.svc.cluster.local` **成功**，但部分请求仍落到**错误子集**或**无指标**。根因常是：**路由与端点由 xDS/EDS 决定**，不是由一次 DNS 结果单独决定；**DNS 代理**、**搜索域**、**TTL**、**直连 Pod IP** 也会绕过你以为的「服务语义」。

**痛点放大**

- **把 DNS 当路由**：解析正确 ≠ cluster/subset 正确。
- **捕获边界**：`127.0.0.1`、未重定向端口、hostNetwork 等路径可能**不经 Sidecar**。
- **双栈/IPv6**：解析顺序与监听不一致导致偶发。

```mermaid
flowchart LR
  DNS[CoreDNS/代理] --> App[应用 connect]
  App --> SB[Sidecar 拦截]
  SB --> XDS[xDS cluster/EDS]
```

## 18.2 项目设计：小胖、小白与大师的「同名不同路」

**第一轮**

> **小胖**：名字解析对了还能连错？你们网格是不是太玄学了？
>
> **小白**：我该信 `nslookup` 还是 `proxy-config cluster`？
>
> **大师**：**信 Envoy 真相**：`cluster` 名、subset、endpoint 健康度以 `istioctl proxy-config` 为准。DNS 只解决「名字→IP」，**路由与负载均衡在 Sidecar**。
>
> **大师 · 技术映射**：**DNS ↔ 解析；xDS ↔ 路由与端点集。**

**第二轮**

> **小白**：直连 Pod IP 会怎样？
>
> **大师**：可能 **绕过部分服务级策略**（视配置），且与 Headless/StatefulSet 场景更易踩坑——生产应约束 **经 Service 访问**。

## 18.3 项目实战：诊断命令组合

**步骤 1：DNS 与 resolv**

```bash
kubectl exec -it deploy/sleep -c sleep -- nslookup reviews.default.svc.cluster.local
istioctl proxy-config cluster deploy/sleep | grep reviews
istioctl proxy-config endpoint deploy/sleep | grep reviews
kubectl exec -it deploy/sleep -c sleep -- cat /etc/resolv.conf
```

**步骤 2**：对照 `cluster` / `endpoint` 与 VirtualService subset。

## 18.4 项目总结

**优点与缺点**

| 维度 | 理解 DNS+xDS | 只查 DNS |
|:---|:---|:---|
| 排障 | 可定位路由/端点 | 易误判 |

**适用场景**：Headless/StatefulSet；跨 NS；DNS 代理相关 Issue。

**不适用场景**：纯集群外 DNS（无 Sidecar）。

**典型故障**：Endpoint 不健康；subset 标签漂移；直连 Pod IP。

**思考题（参考答案见第19章或附录）**

1. 为何「DNS 解析成功」仍可能出现 `UH`（no healthy upstream）？
2. 简述一种「绕过 Sidecar」的流量路径及风险。

**推广与协作**：平台文档化捕获范围；开发禁止生产直连 Pod IP；网络团队对齐双栈策略。

---

## 编者扩展

> **本章导读**：解析≠路由；**实战演练**：nslookup 与 proxy-config 对照；**深度延伸**：CoreDNS vs Envoy DNS。

---

上一章：[第17章 WorkloadEntry：将虚拟机与裸机工作负载纳入网格](第17章 WorkloadEntry：将虚拟机与裸机工作负载纳入网格.md) | 下一章：[第19章 自定义 CA 与证书：企业 PKI 与 Istio 的衔接](第19章 自定义 CA 与证书：企业 PKI 与 Istio 的衔接.md)

*返回 [专栏目录](README.md)*
