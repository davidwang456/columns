---
title: "第17章 WorkloadEntry：将虚拟机与裸机工作负载纳入网格"
part: "第二部分：核心能力篇（第11-22章）"
chapter: 17
---

# 第17章 WorkloadEntry：将虚拟机与裸机工作负载纳入网格

## 17.1 项目背景

**Kubernetes 并非工作负载的全部**

企业存量系统大量运行在虚拟机或物理机上，短期内无法容器化。若这些系统需要与网格内服务统一 mTLS、指标与路由，需要一种**非 Pod 形式的工作负载抽象**。

**ServiceEntry 与 WorkloadEntry 的分工**

ServiceEntry 将**外部服务端点**注册进网格；WorkloadEntry 则描述**可承载在网格外但属于同一身份平面**的工作负载（例如固定 IP 上的进程），常与 `WorkloadGroup`、智能 DNS 结合，实现渐进式迁移。

## 17.2 项目设计：大师给老系统一张“临时身份证”

**场景设定**：遗留计费进程跑在指定 VM 上，小白希望 Sidecar 以进程或容器形式部署在该主机，与网格内 `billing-service` 互访时使用统一 SPIFFE ID。

**核心对话**：

> **小白**：虚拟机里没有 Pod，怎么注入 Sidecar？
>
> **大师**：可以用 **Sidecar 安装在 VM 上**（或使用进程级代理），再用 WorkloadEntry 把这台机器声明为网格成员。关键是**身份**与**地址**要对上：SPIFFE ID、IP、端口、标签一致。
>
> **小白**：和纯 ServiceEntry 有何区别？
>
> **大师**：ServiceEntry 更像“外部服务目录”；WorkloadEntry 更像“这名外部成员也是我们编制内的同事”，可与 `WorkloadGroup` 一起做生命周期管理。

## 17.3 项目实战：WorkloadEntry 示例

```yaml
apiVersion: networking.istio.io/v1beta1
kind: WorkloadEntry
metadata:
  name: billing-vm-01
  namespace: finance
spec:
  serviceAccount: billing-legacy
  address: 10.20.30.40
  labels:
    app: billing
    version: legacy
  ports:
    grpc: 50051
```

```yaml
apiVersion: networking.istio.io/v1beta1
kind: ServiceEntry
metadata:
  name: billing-legacy
  namespace: finance
spec:
  hosts:
  - billing-legacy.finance.svc.cluster.local
  ports:
  - number: 50051
    name: grpc
    protocol: GRPC
  resolution: STATIC
  workloadSelector:
    labels:
      app: billing
```

## 17.4 项目总结

| 维度 | 详细分析 |
|:---|:---|
| **核心优点** | **平滑纳管遗留系统**；**统一身份与策略** |
| **主要缺点** | **主机级部署与升级成本**；**标签与 IP 漂移需治理** |
| **典型使用场景** | **容器与 VM 共存**、**分阶段上云** |
| **关键注意事项** | **SPIFFE ID 与 ServiceAccount 映射**；**防火墙与 Sidecar 端口** |
| **常见踩坑经验** | **地址变更未同步 WE**；**健康检查与注册信息不一致** |

---

## 编者扩展

> **本章导读**：虚拟机不是二等公民：WorkloadEntry 让老系统与 Pod 并肩出现在同一服务名下。

### 趣味角

WorkloadEntry 像给虚拟机办「网格居住证」：没证也能通，但有了证才能享受同等待遇的 mTLS 与策略。

### 实战演练

为一台 VM 或 mock IP 注册 WorkloadEntry + ServiceEntry，从网格内 Pod `curl` 验证；检查 `istioctl proxy-config endpoint` 是否出现非 Kubernetes endpoint。

### 深度延伸

生命周期：VM 上下线与健康检查如何与 Kubernetes readiness 对齐？

---

上一章：[第16章 熔断与降级：韧性设计的核心](第16章 熔断与降级：韧性设计的核心.md) | 下一章：[第18章 DNS 代理与流量捕获细节：为什么“解析对了”仍连错](第18章 DNS 代理与流量捕获细节：为什么“解析对了”仍连错.md)

*返回 [专栏目录](README.md)*
