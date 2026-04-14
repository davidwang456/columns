# 第 8 章：DestinationRule：子集、负载均衡、连接池

**受众提示**：开发理解连接池与熔断参数；运维调优与容量；测试验证异常与降级。

**实验基线**：[LAB-KIT.md](LAB-KIT.md)。

---

## 1. 项目背景

`DestinationRule`（DR）定义**到达目标服务**后的行为：TLS 模式（与 mTLS 策略配合）、**子集**（subset）、**负载均衡算法**、**连接池**、**异常检测（熔断）** 等。与 `VirtualService` 配合：VS 决定「去哪」，DR 决定「到那之后怎么连、怎么失败」。

推广时强调：**DR 是性能与稳定性旋钮**，误调会引发**重试风暴**或**连接耗尽**（见第 11 章）。本章在 Bookinfo 或自建服务上展示 `subset` 与 `trafficPolicy` 片段。

---

## 2. 项目设计：大师与小白的对话

**小白**：DestinationRule 和 Service 的 Endpoints 啥关系？

**大师**：`subset` 用 **labels** 从同一 Service 的后端中**切出**逻辑分组；Envoy 为每个 subset 建 cluster。

**小白**：负载均衡 `LEAST_REQUEST` 一定更好吗？

**大师**：不一定。要看**连接成本**、**长连接**、**缓存命中率**。需要压测（第 33 章）。

**小白**：连接池 `http2MaxRequests` 设很大会怎样？

**大师**：可能**把下游打爆**或占满内存。池是**保护双方**的。

**小白**：`outlierDetection` 和「熔断」是同一个词吗？

**大师**：口语上常混用；Istio 里 outlierDetection 对应 Envoy **异常检测** eject，**与业务熔断**（如 Hystrix）层次不同。

**要点清单**

1. 子集标签必须与 **Pod template** 一致。
2. DR 变更可**影响全集群**到该 host 的流量，**评审必过**。
3. 与 `PeerAuthentication` 的 TLS 设置协同（第 26 章）。

---

## 3. 项目实战

### 3.1 最小 DestinationRule（接第 7 章）

```yaml
apiVersion: networking.istio.io/v1beta1
kind: DestinationRule
metadata:
  name: reviews
spec:
  host: reviews
  trafficPolicy:
    loadBalancer:
      simple: ROUND_ROBIN
    connectionPool:
      tcp:
        maxConnections: 100
      http:
        http1MaxPendingRequests: 10
        http2MaxRequests: 100
  subsets:
  - name: v2
    labels:
      version: v2
  - name: v3
    labels:
      version: v3
```

### 3.2 异常检测（示例）

```yaml
  trafficPolicy:
    outlierDetection:
      consecutive5xxErrors: 5
      interval: 10s
      baseEjectionTime: 30s
```

### 3.3 应用与验证

```bash
kubectl apply -f reviews-dr.yaml
istioctl proxy-config cluster deploy/productpage -n default
```

观察 `reviews` 相关 cluster 的 `outlier` 与 `circuit_breakers` 信息（输出因版本而异）。

---

## 4. 项目总结

**优点**

- **细粒度**控制连接与失败处理；
- 与观测指标联动，可**量化**熔断与驱逐。

**缺点**

- 参数多，**缺省值**未必适合你的业务 QPS。
- 多团队共用时，**DR 命名空间**与**继承**要规范。

**适用场景**

- 高并发、下游易抖动、需要**隔离故障**的服务。

**注意事项**

- 与 **VS 重试** 联用要特别警惕 **重试风暴**（第 11 章）。
- 变更应**灰度**并盯 RED 指标（第 20、25 章）。

**常见踩坑**

1. **现象**：频繁 503。**原因**：连接池过小或 outlier 驱逐过激。**处理**：调参 + 检查下游容量。
2. **现象**：subset 为空。**原因**：label 拼写错误或版本未部署。**处理**：对齐 labels。
3. **现象**：mTLS 与 DR 冲突。**原因**：TLS 设置与全局策略不一致。**处理**：统一 `PeerAuthentication` 与 DR 的 `trafficPolicy.tls`。

---

### 4.1 再谈一个场景：连接池与下游容量

某团队把 `maxConnections` 调得极大以「提升性能」，结果在流量突增时把下游数据库连接打满。连接池是**双向保护**：既要保护客户端，也要保护服务端。调参必须结合压测与下游容量模型。

### 4.2 组织治理

建议为 DestinationRule 变更设立**二级审批**：业务负责人 + 平台负责人，并在变更说明里附上**指标对比窗口**（变更前后 1 小时）。

---

## 附：自测与练习

### 自测题

1. subset 的 label 与 Pod label 不一致会发生什么？
2. outlierDetection 驱逐与「业务熔断」有何层次差异？
3. 何时应怀疑 DR 与 PeerAuthentication 的 TLS 设置冲突？

### 动手作业

在测试环境人为制造连续 5xx，观察 outlier 行为与指标变化，并记录「驱逐—恢复」时间线。

**延伸阅读**：Istio *DestinationRule*；Envoy *Circuit breaking*。

---

### 4.5 本章小结：把知识变成「可执行」

推广 Istio 时，最容易失败的不是「不会配」，而是「配了没人敢动」。建议每章学完后，在团队看板增加一张卡片：**谁能在生产执行与本章相关的变更**、**需要哪些审批**、**回滚命令是什么**。技术文档若不能映射到流程，就仍是幻灯片。

### 4.6 与第 24、31、36 章的联动提示

- **排障**：把 `istioctl analyze` 与 `proxy-config` 写进 oncall 第一步（第 24 章）。
- **交付**：把安装与 values 纳入 GitOps（第 31 章）。
- **治理**：把试点范围、预算与里程碑写成公开路线图（第 36 章）。
