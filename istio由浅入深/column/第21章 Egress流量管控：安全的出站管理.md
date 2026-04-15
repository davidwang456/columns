---
title: "第21章 Egress流量管控：安全的出站管理"
part: "第二部分：核心能力篇（第11-22章）"
chapter: 21
---

# 第21章 Egress流量管控：安全的出站管理

## 21.1 项目背景

**业务场景（拟真）：合规要求「出站白名单 + 审计可查」**

安全团队要求：业务 Pod **只能**访问经批准的 SaaS（如支付 API），其余外网 **默认拒绝**；且出站需经 **固定源 IP / 集中日志** 满足防火墙对账。**REGISTRY_ONLY** + **ServiceEntry 登记** + 可选 **Egress Gateway** + **AuthorizationPolicy** 构成「出境海关」。

**痛点放大**

- **ALLOW_ANY**：任意出站，数据外泄与恶意 C2 难防。
- **只配 Egress 无 SE**：路由与发现对不齐。
- **性能**：全量经 Egress 可能成为瓶颈。

```mermaid
flowchart LR
  Pod --> SE[ServiceEntry 白名单]
  SE --> EGW[可选 Egress Gateway]
  EGW --> Ext[外部]
```

## 21.2 项目设计：小胖、小白与大师的出境检查

**第一轮**

> **小胖**：不让上网，第三方 API 怎么调？
>
> **小白**：REGISTRY_ONLY 和 ALLOW_ANY 啥区别？DNS 泄露咋防？
>
> **大师**：**REGISTRY_ONLY** 只允许已登记 host 出站；未登记则拒绝。白名单靠 **ServiceEntry** 维护；DNS 策略与 **NetworkPolicy** 可配合防绕过。Egress Gateway 用于集中审计与固定出口 IP。
>
> **大师 · 技术映射**：**outboundTrafficPolicy.mode ↔ 默认允许/拒绝；Egress + Authz ↔ 谁可访问哪类外网。**

**第二轮**

> **大师**：先列 **依赖清单** 再开策略，避免「上线当天才发现要调新域名」。

## 21.3 项目实战：构建安全出站体系

**步骤 1：mesh 出站模式（REGISTRY_ONLY）**

```yaml
# 强制REGISTRY_ONLY模式
apiVersion: install.istio.io/v1alpha1
kind: IstioOperator
spec:
  meshConfig:
    outboundTrafficPolicy:
      mode: REGISTRY_ONLY

---
# Egress Gateway部署
apiVersion: install.istio.io/v1alpha1
kind: IstioOperator
spec:
  components:
    egressGateways:
    - name: istio-egressgateway
      enabled: true
      k8s:
        nodeSelector:
          node-type: egress-gateway
        resources:
          requests:
            cpu: 2000m
            memory: 2Gi

---
# 出站访问控制
apiVersion: security.istio.io/v1beta1
kind: AuthorizationPolicy
metadata:
  name: egress-access-control
  namespace: istio-system
spec:
  selector:
    matchLabels:
      istio: egressgateway
  action: ALLOW
  rules:
  - from:
    - source:
        namespaces: ["order-service"]
    to:
    - operation:
        hosts: ["api.stripe.com"]
        ports: ["443"]
```

**步骤 2**：为每个外部依赖补充 **ServiceEntry**（本章未展开，见第5章）；**步骤 3**：Egress 高可用与 HPA 按流量评估。

**测试验证**：未登记域名 `curl` 应失败；登记后成功；审计日志经 Egress 可检索。

## 21.4 项目总结

**优点与缺点**

| 维度 | 白名单 + Egress | ALLOW_ANY |
|:---|:---|:---|
| 安全 | 强 | 弱 |
| 复杂度 | 高 | 低 |

**适用场景**：金融政企；数据防泄漏；固定出口 IP。

**不适用场景**：开发随意试外网（可用独立命名空间放宽）。

**典型故障**：漏配 ServiceEntry；Egress 单点；DNS 绕过。

**思考题（参考答案见第22章或附录）**

1. `REGISTRY_ONLY` 下遗漏 ServiceEntry 时，典型错误现象是什么？
2. 何时需要 Egress Gateway 而不是仅 Sidecar 直连已登记外部主机？

**推广与协作**：安全定白名单；平台维护 Egress；业务申报外网依赖。

---

## 编者扩展

> **本章导读**：出站是数据泄露与合规的高风险面：Egress 治理不是可选项。

### 趣味角

没有 Egress 策略的集群，像写字楼每层都有消防梯直通街面——方便抽烟，也方便夹带。

### 实战演练

实现「仅允许经 Egress Gateway 访问外网 HTTPS」的路径；从 sleep 发起 `curl https://example.com` 并抓访问日志中的 `upstream_cluster`。

### 深度延伸

域名白名单与 TLS SNI 检查的差异；内网 DNS 投毒时的失效模式。

---

上一章：[第20章 Sidecar 资源治理：配额、限制与调度协同](第20章 Sidecar 资源治理：配额、限制与调度协同.md) | 下一章：[第22章 核心能力篇复盘：从对象模型到运维闭环](第22章 核心能力篇复盘：从对象模型到运维闭环.md)

*返回 [专栏目录](README.md)*
