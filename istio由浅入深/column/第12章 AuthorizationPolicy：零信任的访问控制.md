---
title: "第12章 AuthorizationPolicy：零信任的访问控制"
part: "第二部分：核心能力篇（第11-22章）"
chapter: 12
---

# 第12章 AuthorizationPolicy：零信任的访问控制

## 12.1 项目背景

微服务越权访问的风险、传统防火墙的粗粒度局限、基于身份的细粒度授权需求，这些挑战推动了Istio AuthorizationPolicy的发展。它实现了基于身份的细粒度访问控制，将授权决策从应用代码中剥离，由基础设施统一执行。

## 12.2 项目设计：大师构建零信任防线

**场景设定**：小白需要确保只有订单服务能访问支付服务，同时管理后台只能进行查询操作不能扣款。

**核心对话**：

> **大师**：AuthorizationPolicy的from、to、when三个维度可以精确控制：来源（谁发起请求）、操作（请求做什么）、条件（附加约束）。你的需求可以这样实现——

## 12.3 项目实战：多维度授权策略配置

```yaml
apiVersion: security.istio.io/v1beta1
kind: AuthorizationPolicy
metadata:
  name: payment-service-policy
  namespace: payment
spec:
  selector:
    matchLabels:
      app: payment-service
  action: ALLOW
  rules:
  # 规则1：order-service可以扣款
  - from:
    - source:
        principals: ["cluster.local/ns/order/sa/order-service"]
    to:
    - operation:
        methods: ["POST"]
        paths: ["/charge", "/refund"]
  
  # 规则2：admin-service可以查询
  - from:
    - source:
        principals: ["cluster.local/ns/admin/sa/admin-service"]
    to:
    - operation:
        methods: ["GET"]
        paths: ["/transactions", "/balance"]
  
  # 默认拒绝所有其他访问
```

## 12.4 项目总结

| 维度 | 要点 |
|:---|:---|
| **优点** | 细粒度、动态评估、审计友好 |
| **缺点** | 策略数量膨胀、性能影响、调试复杂 |
| **关键场景** | 多租户隔离、敏感服务保护、合规审计 |
| **踩坑经验** | 默认拒绝的渐进实施、策略冲突检测、性能基准测试 |

---

## 编者扩展

> **本章导读**：AuthorizationPolicy 把「谁能对谁做什么」写成可审计的策略，而不是散落在代码里的 if。

### 趣味角

如果把 JWT 当作「门票」，RequestAuthentication 验票真伪，AuthorizationPolicy 决定「持票人能进哪个展厅」——二者少一个都会出事。

### 实战演练

写一条 DENY all + ALLOW 特定 JWT claim 的策略；用 `istioctl authz check` 或日志验证拒绝与放行。

### 深度延伸

策略评估顺序（CUSTOM、DENY、ALLOW）与「显式拒绝优先」的运维含义；何时需要 ext-authz 插件？

---

上一章：[第11章 PeerAuthentication深度：细粒度的传输安全](第11章 PeerAuthentication深度：细粒度的传输安全.md) | 下一章：[第13章 RequestAuthentication：终端用户身份与 JWT 验证](第13章 RequestAuthentication：终端用户身份与 JWT 验证.md)

*返回 [专栏目录](README.md)*
