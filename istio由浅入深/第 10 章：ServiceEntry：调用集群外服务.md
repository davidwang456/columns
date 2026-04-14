# 第 10 章：ServiceEntry：调用集群外服务

**受众提示**：开发列外部依赖；运维管出口策略与防火墙；测试验证外网调用与故障模拟。

**实验基线**：[LAB-KIT.md](LAB-KIT.md)。

---

## 1. 项目背景

微服务常访问 **SaaS、支付、短信、遗留 HTTP API**。集群外主机默认不在网格的服务发现里；`ServiceEntry` 将**外部服务**注册进网格，使 Envoy 能对其执行**路由、TLS、mTLS（视场景）与策略**。同时可配合 `WorkloadEntry`（本章略）描述外部端点。

推广目的：让团队**显式化**外部依赖，避免「悄悄 `curl` 公网」无法治理。

---

## 2. 项目设计：大师与小白的对话

**小白**：不建 ServiceEntry 就不能访问外网吗？

**大师**：很多情况下**仍能访问**（取决于出口网络策略），但网格**无法**对其做一致的策略与观测建模。

**小白**：`resolution: DNS` 和 `NONE` 啥区别？

**大师**：DNS 表示**动态解析**；NONE 常用于已知的静态端点。选错会影响**连接建立**与负载均衡行为。

**小白**：外部 API 要走公司代理怎么办？

**大师**：涉及 **HTTP 代理、Egress Gateway、透明代理** 等企业架构，需单独设计（可扩展第 37 章）。

**要点清单**

1. **显式登记**外部依赖，纳入变更评审。
2. 与 **AuthorizationPolicy** 联动做**出站**控制（第 28 章）。
3. 注意 **TLS 原始连接** vs **终止** 的差异。

---

## 3. 项目实战

### 3.1 允许访问外部 HTTPS 服务（示例）

```yaml
apiVersion: networking.istio.io/v1beta1
kind: ServiceEntry
metadata:
  name: external-httpbin
spec:
  hosts:
  - httpbin.org
  ports:
  - number: 443
    name: tls
    protocol: TLS
  resolution: DNS
  location: MESH_EXTERNAL
```

```bash
kubectl apply -f external-httpbin-se.yaml
```

### 3.2 从网格内验证

```bash
kubectl exec deploy/sleep -c sleep -- curl -sS -o /dev/null -w "%{http_code}\n" https://httpbin.org/get
```

> `sleep` 示例来自 Istio `samples/sleep`。

### 3.3 与 VirtualService 组合（可选）

对外部 host 做**超时、重试**时，确保 VS 的 host 与 ServiceEntry 一致。

---

## 4. 项目总结

**优点**

- 外部依赖**可治理、可观测**；
- 为 **Egress 管控**打基础。

**缺点**

- 配置遗漏会导致**策略盲区**；
- DNS、证书、SNI 问题**排障较难**。

**适用场景**

- 调用公网 API、**混合云**、**合作伙伴**固定域名。

**注意事项**

- 与 **网络策略（NetworkPolicy）**、**防火墙** 三方对齐。
- **PII** 出境合规审查。

**常见踩坑**

1. **现象**：TLS 握手失败。**原因**：SNI/ALPN 与 `ServiceEntry` 端口协议不匹配。**处理**：调整 `protocol` 或使用 `TLS`/`HTTPS` 正确建模。
2. **现象**：间歇性 503。**原因**：DNS 解析抖动或 **IP 变化**。**处理**：考虑 `resolution` 与连接池。
3. **现象**：策略不生效。**原因**：流量未经过 Sidecar 或 **SE host 写错**。**处理**：核对 `hosts` 与客户端 URL。

---

### 4.1 再谈一个场景：出口合规

访问公网 API 时，合规可能要求固定 egress IP 或禁止直连。此时仅 `ServiceEntry` 不够，还需要 **Egress Gateway** 或企业代理架构。推广时别把「能访问」当成「可治理」，要把**审计、限流、凭证**一并纳入。

### 4.2 与 NetworkPolicy

Kubernetes NetworkPolicy 与 Istio 策略在不同层次；两者都开启时，**取交集**。排障要两边对照，避免单看一侧。

---

## 附：自测与练习

### 自测题

1. `MESH_EXTERNAL` 的语义是什么？
2. `resolution: DNS` 可能解决什么问题？
3. 为什么外部依赖建议显式登记为 ServiceEntry？

### 动手作业

为 `httpbin.org` 与你们真实依赖各建一条 ServiceEntry（测试环境），对比 `proxy-config cluster` 差异。

**延伸阅读**：Istio *ServiceEntry*；Egress 最佳实践。

---

### 4.3 排障分层：从「能 ping」到「能握手」

外部访问问题建议按层拆解：**DNS 是否解析**、**TCP 是否通达**、**TLS 是否握手**、**HTTP 是否 200**。Sidecar 日志与 `istioctl proxy-config cluster` 往往能在第 3、4 层给出线索；若只在应用里看到 `connection reset`，要记得向上追问是**哪一跳**重置。对第三方 SaaS，还要准备**对方状态页**与**限流**沟通渠道，避免团队在内部分析两小时才发现是上游维护。

### 4.4 与开发协作的「外部依赖清单」

把 `ServiceEntry` 当作**依赖注册表**：每条记录写明 owner、用途、是否含 PII、是否允许缓存、以及**故障时的降级策略**（超时后的默认值、开关位）。清单进入评审后，测试同学才能系统化设计**契约测试**与**降级演练**，而不是依赖个人经验。

---

### 4.5 本章小结：把知识变成「可执行」

推广 Istio 时，最容易失败的不是「不会配」，而是「配了没人敢动」。建议每章学完后，在团队看板增加一张卡片：**谁能在生产执行与本章相关的变更**、**需要哪些审批**、**回滚命令是什么**。技术文档若不能映射到流程，就仍是幻灯片。

### 4.6 与第 24、31、36 章的联动提示

- **排障**：把 `istioctl analyze` 与 `proxy-config` 写进 oncall 第一步（第 24 章）。
- **交付**：把安装与 values 纳入 GitOps（第 31 章）。
- **治理**：把试点范围、预算与里程碑写成公开路线图（第 36 章）。
