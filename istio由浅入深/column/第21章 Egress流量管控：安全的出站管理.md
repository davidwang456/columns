---
title: "第21章 Egress流量管控：安全的出站管理"
part: "第二部分：核心能力篇（第11-22章）"
chapter: 21
---

# 第21章 Egress流量管控：安全的出站管理

## 21.1 项目背景

出站流量的安全盲区、数据泄露与恶意通信风险、合规审计的出站记录需求，这些挑战推动了Egress Gateway成为高安全环境的标配组件。

## 21.2 项目设计：大师设立出境检查

**场景设定**：小白需要管控服务能访问哪些外部网站，防止恶意代码外泄数据。

## 21.3 项目实战：构建安全出站体系

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

## 21.4 项目总结

| 维度 | 要点 |
|:---|:---|
| **优点** | 集中管控、审计完整、策略统一 |
| **缺点** | 性能瓶颈、单点故障、配置复杂 |
| **关键场景** | 高安全环境、合规要求、数据防泄漏 |
| **踩坑经验** | DNS泄露绕过、性能调优、高可用设计 |

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
