---
title: "第15章 东西向网关与多集群流量：East-West Gateway 入门"
part: "第二部分：核心能力篇（第11-22章）"
chapter: 15
---

# 第15章 东西向网关与多集群流量：East-West Gateway 入门

## 15.1 项目背景

**单集群边界被打破之后的“新南北向”**

当组织采用多集群部署（异地容灾、部门隔离、合规分区）时，集群之间的流量不再是“外部用户入口”那么简单，而是**东西向跨集群**调用。若仍依赖公网或扁平 VPN，既难做统一策略，也难观测。Istio 通过 **East-West Gateway** 将集群间流量纳入同一套 mTLS、路由与可观测体系。

**与 ServiceEntry、多集群 DNS 的关系**

跨集群首先要解决**服务发现**与**证书信任**。实践中常与 CoreDNS、`istioctl x create remote secret`、多集群控制平面拓扑结合。本章聚焦流量入口形态与网关职责，具体多集群安装以官方多集群指南为准。

## 15.2 项目设计：大师画一张“集群间收费站”

**场景设定**：同城双集群 `cluster-a` 与 `cluster-b` 需要互访 `catalog` 服务，小白希望互访流量也经过 mTLS，并能在 Grafana 中看到按集群维度分解的指标。

**核心对话**：

> **小白**：我们两个集群里都有 catalog，互访时怎么知道走哪一个？
>
> **大师**：先定**拓扑**：是共享控制平面还是多控制平面。无论哪种，East-West Gateway 都是集群间流量的**入口门面**——就像高速互通的收费站，先过站再分流。
>
> **小白**：和 Ingress Gateway 有什么不一样？
>
> **大师**：Ingress 主要面向**来自集群外用户**；East-West 面向**来自其他集群的服务**。职责类似，但证书、DNS、路由往往由**平台团队**统一编排。

**类比阐释**：Ingress 像城市机场；East-West 像城际高铁站的换乘口——都检票，但客流来源与安检策略不同。

## 15.3 项目实战：East-West Gateway 概念配置

```yaml
apiVersion: networking.istio.io/v1beta1
kind: Gateway
metadata:
  name: eastwest-gateway
  namespace: istio-system
spec:
  selector:
    istio: eastwestgateway
  servers:
  - port:
      number: 15443
      name: tls
      protocol: TLS
    tls:
      mode: ISTIO_MUTUAL
    hosts:
    - "*.local"
```

```yaml
apiVersion: networking.istio.io/v1beta1
kind: VirtualService
metadata:
  name: catalog-cross-cluster
  namespace: catalog
spec:
  hosts:
  - catalog.catalog-global.svc.cluster.local
  gateways:
  - mesh
  - istio-system/eastwest-gateway
  http:
  - route:
    - destination:
        host: catalog.catalog-global.svc.cluster.local
        port:
          number: 8080
```

```bash
istioctl proxy-config cluster deploy/catalog -n catalog | grep -i global
```

## 15.4 项目总结

| 维度 | 详细分析 |
|:---|:---|
| **核心优点** | **集群间流量纳入网格策略**；**与多集群拓扑解耦展示** |
| **主要缺点** | **运维复杂度高**；**DNS 与证书配置易错** |
| **典型使用场景** | **异地多活**、**测试与生产隔离集群互访** |
| **关键注意事项** | **控制平面拓扑选择**；**remote secret 与 kubeconfig 权限** |
| **常见踩坑经验** | **服务名与 namespace 不一致**；**忘记为东西向开放端口** |

---

## 编者扩展

> **本章导读**：多集群时代，东西向网关是集群间握手的「外交官」。

### 趣味角

North-South 是城市大门，East-West 是城际高速——没有 E-W Gateway，集群间就像没收费站和路牌，车能开但没人记账。

### 实战演练

在单集群先用文档画出「子集群 A ↔ 子集群 B」的证书与 discovery 信任链；若环境允许，部署最小多集群并验证 `istioctl remote-clusters`。

### 深度延伸

primary-remote vs multi-primary 在运维负担与脑裂风险上的权衡表（各写三条）。

---

上一章：[第14章 金丝雀发布：渐进式交付的艺术](第14章 金丝雀发布：渐进式交付的艺术.md) | 下一章：[第16章 熔断与降级：韧性设计的核心](第16章 熔断与降级：韧性设计的核心.md)

*返回 [专栏目录](README.md)*
