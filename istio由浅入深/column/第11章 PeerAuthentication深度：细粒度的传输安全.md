---
title: "第11章 PeerAuthentication深度：细粒度的传输安全"
part: "第二部分：核心能力篇（第11-22章）"
chapter: 11
---

# 第11章 PeerAuthentication深度：细粒度的传输安全

## 11.1 项目背景

不同服务的安全等级差异、端口级别的安全策略需求、渐进式安全加固的实施路径，这些生产环境的复杂场景要求mTLS策略具备多层次、细粒度的配置能力。Istio的PeerAuthentication API支持从网格级别到命名空间级别、再到工作负载级别乃至端口级别的策略叠加与覆盖。

## 11.2 项目设计：大师定制安全策略

**场景设定**：小白负责的核心支付服务已完成mTLS基础配置，但遇到了几个棘手问题：支付服务需要强制mTLS，但关联的监控采集器不支持TLS；健康检查端点如果被加密，负载均衡器的健康探测会失败。

**核心对话**：

> **小白**：大师，我们的支付服务启用了mTLS，但监控系统的Prometheus采集不到指标了，因为Prometheus不支持mTLS。怎么办？
>
> **大师**：PeerAuthentication支持端口级别的例外配置——你可以让支付服务的主体强制mTLS，但暴露给Prometheus的采集端口保持明文。
>
> **小白**：具体怎么配置？
>
> **大师**：PeerAuthentication的策略是分层的：最底层是网格默认策略，像国家的法律；中间是命名空间策略，像地方条例；最上面是工作负载策略，像公司的内部规定。每一层都可以覆盖上一层的配置。

## 11.3 项目实战：多层次mTLS策略配置

**工作负载级别精细化控制**

```yaml
apiVersion: security.istio.io/v1beta1
kind: PeerAuthentication
metadata:
  name: payment-core-policy
  namespace: payment
spec:
  selector:
    matchLabels:
      app: payment-core
      tier: critical
  mtls:
    mode: STRICT
  portLevelMtls:
    # 健康检查端口：负载均衡器探测需要明文
    8080:
      mode: DISABLE
    # 监控指标端口：Prometheus采集，计划Q2接入网格
    9090:
      mode: PERMISSIVE  # 过渡期允许明文
    # 调试端口：仅开发环境启用
    5005:
      mode: DISABLE
```

**策略继承与UNSET模式**

```yaml
apiVersion: security.istio.io/v1beta1
kind: PeerAuthentication
metadata:
  name: inherit-with-exception
  namespace: payment
spec:
  selector:
    matchLabels:
      app: legacy-adapter
  mtls:
    mode: UNSET  # 继承命名空间的STRICT设置
  portLevelMtls:
    # 仅对特定端口覆盖
    3306:  # MySQL兼容端口
      mode: DISABLE
```

## 11.4 项目总结

| 维度 | 要点 |
|:---|:---|
| **优点** | 灵活分层、渐进实施、与现有系统兼容 |
| **缺点** | 策略叠加复杂、调试困难 |
| **关键场景** | 混合安全等级、遗留系统迁移、合规分级 |
| **踩坑经验** | 健康检查端口配置、策略优先级理解、端口匹配精确性 |

---

## 编者扩展

> **本章导读**：传输层策略要落到工作负载粒度：PeerAuthentication 是「谁必须加密」的契约。

### 趣味角

命名空间级 mTLS 像小区规定「本小区住户必须刷卡」；工作负载级像「某栋楼额外要人脸识别」——粒度越细，迁移越慢但越安全。

### 实战演练

为单个 Deployment 设置 STRICT，其余保持 PERMISSIVE，用 `curl` 从未注入的 Pod 访问，记录失败原因与 `tls-check` 输出。

### 深度延伸

与 NetworkPolicy 的交集：mTLS 解决的是**身份**，NetworkPolicy 解决的是**拓扑**——各举一个只开一个不够的例子。

---

上一章：[第10章 mTLS基础：服务间通信的自动加密](第10章 mTLS基础：服务间通信的自动加密.md) | 下一章：[第12章 AuthorizationPolicy：零信任的访问控制](第12章 AuthorizationPolicy：零信任的访问控制.md)

*返回 [专栏目录](README.md)*
