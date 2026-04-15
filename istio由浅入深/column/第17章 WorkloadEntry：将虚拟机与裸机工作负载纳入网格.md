---
title: "第17章 WorkloadEntry：将虚拟机与裸机工作负载纳入网格"
part: "第二部分：核心能力篇（第11-22章）"
chapter: 17
---

# 第17章 WorkloadEntry：将虚拟机与裸机工作负载纳入网格

## 17.1 项目背景

**业务场景（拟真）：计费进程还在 VM，但要和网格里同一套 mTLS**

遗留 **计费** 跑在固定 IP 的 VM 上，短期无法容器化，但安全要求与 K8s 内 **billing** 服务 **同一 SPIFFE 身份平面**、同一策略。纯 **ServiceEntry** 只解决「外部主机名解析」；**WorkloadEntry** 把该 VM **登记为网格成员**（地址、标签、端口、SA），常与 **WorkloadGroup**、VM 侧 **Sidecar** 安装配合，做渐进上云。

**痛点放大**

- **身份割裂**：无 WE 则只能当「匿名外网」，难做细粒度 Authz。
- **IP/标签漂移**：变更未同步 WE → 流量黑洞或错配。
- **运维成本**：主机上装代理与升级与 Pod 不同频。

```mermaid
flowchart LR
  WE[WorkloadEntry\n地址+标签] --> SE[ServiceEntry\n服务名]
  SE --> Mesh[与 Pod 同策略面]
```

## 17.2 项目设计：小胖、小白与大师的「编制内 VM」

**第一轮**

> **小胖**：VM 上再装个 Sidecar，运维不是双倍活吗？
>
> **小白**：WorkloadEntry 和 ServiceEntry 同时出现时谁定义 endpoints？
>
> **大师**：**WorkloadEntry** 描述「这台机器是谁、端口与标签」；**ServiceEntry** 仍负责**服务名与协议**。`workloadSelector` 把 WE 挂到同一逻辑服务下。VM 上需部署 **istio-proxy**（或等价），否则只有目录没有执行面。
>
> **大师 · 技术映射**：**WorkloadEntry ↔ 网格外成员的 xDS 端点；SPIFFE ↔ ServiceAccount。**

**第二轮**

> **大师**：把 **IP 变更、SA 轮换** 纳入变更单，否则与 Pod 混用服务名时会「偶发连错」。

## 17.3 项目实战：WorkloadEntry 示例

**步骤 1：WorkloadEntry**

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

**步骤 2**：确认 VM 侧 Sidecar 已安装且与 `istio-system` 控制面连通；`istioctl proxy-status` 可见 VM 代理（若适用）。

## 17.4 项目总结

**优点与缺点**

| 维度 | WorkloadEntry + VM Sidecar | 仅 ServiceEntry |
|:---|:---|:---|
| 身份 | SPIFFE 一致 | 多为匿名外网 |
| 运维 | 重 | 轻 |

**适用场景**：VM/裸金属渐进纳管；混合云。

**不适用场景**：可快速容器化且无身份诉求（可直接迁 Pod）。

**典型故障**：地址未更新；selector 不匹配；防火墙阻断 xDS。

**思考题（参考答案见第18章或附录）**

1. `workloadSelector` 与 `WorkloadEntry.labels` 如何协作？
2. 为何仅有 ServiceEntry 不足以让 VM 获得与 Pod 相同的 mTLS 身份？

**推广与协作**：平台维护 WE 模板；网络放行；应用团队申报 IP/端口变更。

---

## 编者扩展

> **本章导读**：VM 与 Pod 同服务名；**实战演练**：改 IP 观察端点变化；**深度延伸**：WorkloadGroup 生命周期。

### 趣味角

WorkloadEntry 像给虚拟机办「网格居住证」：没证也能通，但有了证才能享受同等待遇的 mTLS 与策略。

### 实战演练

为一台 VM 或 mock IP 注册 WorkloadEntry + ServiceEntry，从网格内 Pod `curl` 验证；检查 `istioctl proxy-config endpoint` 是否出现非 Kubernetes endpoint。

### 深度延伸

生命周期：VM 上下线与健康检查如何与 Kubernetes readiness 对齐？

---

上一章：[第16章 熔断与降级：韧性设计的核心](第16章 熔断与降级：韧性设计的核心.md) | 下一章：[第18章 DNS 代理与流量捕获细节：为什么“解析对了”仍连错](第18章 DNS 代理与流量捕获细节：为什么“解析对了”仍连错.md)

*返回 [专栏目录](README.md)*
