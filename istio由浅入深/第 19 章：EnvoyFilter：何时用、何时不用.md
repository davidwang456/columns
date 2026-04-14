# 第 19 章：EnvoyFilter：何时用、何时不用

**受众提示**：高级开发/平台工程评估；运维严控变更；测试加强回归。

**实验基线**：[LAB-KIT.md](LAB-KIT.md)。

---

## 1. 项目背景

`EnvoyFilter` 允许向 Envoy 注入**原生配置**片段，能力极强：改 filter chain、调 WASM、精细 TLS。代价是：**绕过 Istio 抽象**，升级 Istio 时**易碎**。推广立场：**默认不用**；仅当 CRD 无法满足且**有平台组维护**时启用。

---

## 2. 项目设计：大师与小白的对话

**小白**：网上示例都是 EnvoyFilter，我是不是该抄？

**大师**：**先问**官方 CRD 能否解决；抄之前评估**升级风险**。

**小白**：我们只想改一个 access log 字段。

**大师**：先看 **Telemetry API** / **Telemetry** 资源（视版本）能否覆盖；再考虑 EnvoyFilter。

**小白**：谁 code review？

**大师**：**平台 + 网络 + 安全**三方签字的级别。

**要点清单**

1. **强版本锁定**与**集成测试**。
2. **命名空间范围**最小化。
3. 文档记录**业务原因**与**替代路线图**。

---

## 3. 项目实战

> 以下仅为**结构示例**，真实 filter 名称与版本强相关，**勿直接复制到生产**。

```yaml
apiVersion: networking.istio.io/v1alpha3
kind: EnvoyFilter
metadata:
  name: example-access-log-patch
  namespace: istio-system
spec:
  workloadSelector:
    labels:
      istio: ingressgateway
  configPatches:
  - applyTo: NETWORK_FILTER
    match:
      context: GATEWAY
      listener:
        filterChain:
          filter:
            name: envoy.filters.network.http_connection_manager
    patch:
      operation: MERGE
      value:
        typed_config:
          '@type': type.googleapis.com/envoy.extensions.filters.network.http_connection_manager.v3.HttpConnectionManager
          access_log:
          - name: envoy.access_loggers.file
            typed_config:
              '@type': type.googleapis.com/envoy.extensions.access_loggers.file.v3.FileAccessLog
              path: /dev/stdout
```

**验证**：`istioctl proxy-config listener` 观察变更；升级前在**预发**跑全链路。

---

## 4. 项目总结

**优点**

- **几乎无限**扩展 Envoy；
- 可解**紧急**生产问题。

**缺点**

- **脆弱**、难审计、新人难懂；
- 与 Istio **升级强耦合**。

**适用场景**

- 平台团队有 **Envoy 专家**；**短期**补丁等待上游合入。

**注意事项**

- 建立 **deprecation** 计划；
- **禁止**业务团队随意添加。

**常见踩坑**

1. **现象**：升级 Istio 全红。**原因**：filter 名称变更。**处理**：集成测试门禁。
2. **现象**：仅网关生效/不生效。**原因**：`applyTo`/`context` 选错。**处理**：对照 Envoy 配置逐层排查（第 24 章）。
3. **现象**：性能下降。**原因**：filter 顺序或 WASM 开销。**处理**： profiling。

---

### 4.1 再谈一个场景：升级日

某团队在升级 Istio 小版本后 EnvoyFilter 失效，原因是 filter 名称变更。结论：**EnvoyFilter 必须纳入升级 CI 的集成测试**，并在升级 checklist 中单列。

### 4.2 与供应商策略

若长期依赖 EnvoyFilter 修补问题，应同步推动上游 **Istio API** 或 **Envoy** 官方能力，避免永久负债。

---

## 附：自测与练习

### 自测题

1. 为什么默认建议「先 CRD，后 EnvoyFilter」？
2. EnvoyFilter 的主要风险有哪些？
3. `applyTo`/`context` 选错会导致什么现象？

### 动手作业

列出你们若使用 EnvoyFilter 的三条准入标准（团队签字版），并贴在 Wiki。

**延伸阅读**：Istio *EnvoyFilter*；Envoy *Version compatibility*。

---

### 4.3 代码审阅清单（EnvoyFilter 专用）

- [ ] 是否确认无官方 CRD 可替代？
- [ ] 是否写明升级兼容策略与 owner？
- [ ] 是否在预发与生产 **ingressgateway**、**sidecar** 两类 workload 都验证？
- [ ] 是否评估 P99 延迟与内存？

### 4.4 技术债登记

每条 EnvoyFilter 在工单系统登记为 **debt**，设**到期清理**日期或「上游合并后删除」条件。

---

### 4.5 本章小结：把知识变成「可执行」

推广 Istio 时，最容易失败的不是「不会配」，而是「配了没人敢动」。建议每章学完后，在团队看板增加一张卡片：**谁能在生产执行与本章相关的变更**、**需要哪些审批**、**回滚命令是什么**。技术文档若不能映射到流程，就仍是幻灯片。

### 4.6 与第 24、31、36 章的联动提示

- **排障**：把 `istioctl analyze` 与 `proxy-config` 写进 oncall 第一步（第 24 章）。
- **交付**：把安装与 values 纳入 GitOps（第 31 章）。
- **治理**：把试点范围、预算与里程碑写成公开路线图（第 36 章）。
