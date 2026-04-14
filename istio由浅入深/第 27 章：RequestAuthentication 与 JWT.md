# 第 27 章：RequestAuthentication 与 JWT

**受众提示**：安全与 API 负责人主导；开发对接 IdP；测试伪造 token 与负例。

**实验基线**：[LAB-KIT.md](LAB-KIT.md)。

---

## 1. 项目背景

**RequestAuthentication** 定义**接受的 JWT**（issuer、jwks、audience 等），通常与 **AuthorizationPolicy**（第 28 章）配合：**先认证后授权**。网格可在**网关**或 **Sidecar** 层校验，减少应用重复代码。注意：**JWT 校验开销**与 **密钥轮换**。

---

## 2. 项目设计：大师与小白的对话

**小白**：网关验了 JWT，网格还验？

**大师**：要**统一信任边界**：若网关**已强校验**并**精简**转发头，网格可只做**二次校验**或**信任内网**——架构需文档化，避免双验浪费。

**小白**：Jwks 轮转过吗？

**大师**：**必须**监控；缓存失败会导致**大面积 401**。

**小白**：gRPC 呢？

**大师**：**metadata** 携带 token；配置方式略不同，要实测。

**要点清单**

1. **least privilege**：只信需要的 **aud**。
2. **clock skew** NTP。
3. **日志**不打完整 JWT。

---

## 3. 项目实战

### 3.1 RequestAuthentication 示例骨架

```yaml
apiVersion: security.istio.io/v1beta1
kind: RequestAuthentication
metadata:
  name: jwt-example
  namespace: foo
spec:
  selector:
    matchLabels:
      app: httpbin
  jwtRules:
  - issuer: "https://auth.example.com"
    jwksUri: "https://auth.example.com/.well-known/jwks.json"
```

### 3.2 与 AuthorizationPolicy 组合（预告）

```yaml
apiVersion: security.istio.io/v1beta1
kind: AuthorizationPolicy
metadata:
  name: require-jwt
  namespace: foo
spec:
  selector:
    matchLabels:
      app: httpbin
  action: DENY
  rules:
  - from:
    - source:
        notRequestPrincipals: ["*"]
```

> 具体字段以官方示例为准；**DENY not *** 模式常用作「必须有 JWT」。

### 3.3 验证

用 **有效/过期/篡改** token **curl** 网关入口。

---

## 4. 项目总结

**优点**

- **集中**认证策略；
- 与 **RBAC** 分层清晰。

**缺点**

- **配置**错误易导致**全站 401**；
- **多租户** issuer 管理复杂。

**适用场景**

- **API**、**BFF** 后服务、**零信任**访问。

**注意事项**

- **OPTIONS** 预检与 **CORS**；
- **移动端** token 刷新。

**常见踩坑**

1. **现象**：间歇 401。**原因**：**jwks** 拉取失败。**处理**：缓存与 **fallback**。
2. **现象**：调试信息不足。**原因**：**日志级别**。**处理**：临时 **access log** 字段（脱敏）。
3. **现象**：与 **应用** 鉴权重复。**原因**：职责不清。**处理**：架构评审。

---

### 4.1 再谈一个场景：Jwks 不可用

IdP 抖动会导致大面积 401。要有 **缓存**、**降级策略**（只读接口是否允许）与 **oncall 手册**。

### 4.2 与网关重复校验

若网关已完整校验 JWT，内层策略要避免**重复开销**；用架构图固定信任边界。

---

## 附：自测与练习

### 自测题

1. RequestAuthentication 与 AuthorizationPolicy 的分工？
2. `jwksUri` 轮换时可能出现什么故障？
3. 为什么不建议在日志中打印完整 JWT？

### 动手作业

用有效、过期、篡改三类 JWT 对同一接口各请求一次，记录状态码与 `istio-proxy` 日志差异（脱敏）。

**延伸阅读**：Istio *Authentication Policy*。

---

### 4.3 多租户 issuer

SaaS 场景可能存在多个 **issuer**；`jwtRules` 列表变长时，评审与测试矩阵同步变复杂。建议按**租户域**拆分策略对象（在可维护前提下）。

### 4.4 与刷新令牌

网格主要校验 **access token**；**refresh** 流程通常在应用/IdP，文档要写清边界，避免「401 都甩给平台」。

---

### 4.5 本章小结：把知识变成「可执行」

推广 Istio 时，最容易失败的不是「不会配」，而是「配了没人敢动」。建议每章学完后，在团队看板增加一张卡片：**谁能在生产执行与本章相关的变更**、**需要哪些审批**、**回滚命令是什么**。技术文档若不能映射到流程，就仍是幻灯片。

### 4.6 与第 24、31、36 章的联动提示

- **排障**：把 `istioctl analyze` 与 `proxy-config` 写进 oncall 第一步（第 24 章）。
- **交付**：把安装与 values 纳入 GitOps（第 31 章）。
- **治理**：把试点范围、预算与里程碑写成公开路线图（第 36 章）。

---

### 本章落地清单（可复制到会议纪要）

1. **本章目标（一句话）**：把技术对象翻译成团队能执行的变更或制度。
2. **责任人/协作方**：开发 ___；运维 ___；测试 ___；安全 ___（按需）。
3. **验证方式**：写出「输入 → 期望输出 → 实际输出」至少 1 条；必要时附指标/截图要求。
4. **风险与边界**：哪些场景本章方法不适用？哪些必须升级审批？
5. **回滚**：列出回滚动作（删资源/改权重/回版本）与预计耗时。
6. **与相邻章节关系**：上一章依赖 ___；下一章继续 ___。

### 写给不同角色的一句话

- **开发**：把「业务语义」与「网格语义」对齐，避免把幂等/鉴权全推给平台。
- **运维**：把变更纳入窗口与 GitOps，任何手工 kubectl 都要留痕。
- **测试**：把策略生效写成可重复断言，而不是一次性手工点点点。

### 常见误解澄清（防抬杠小抄）

- 「上了网格就不用关心网络」：错误；网络/CNI/防火墙仍是底座。
- 「网格能自动让系统高可用」：错误；高可用需要架构、容量、数据与演练。
- 「配置在 YAML 里就是真相」：不完整；真相是 **集群实际生效配置**（见 istioctl proxy-config）。
