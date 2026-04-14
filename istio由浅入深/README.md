# Istio 内部推广大纲（独立章节）

本目录包含 **36 章** 独立 Markdown 文件，面向开发、运维、测试跨部门推广，每章采用统一四段式结构：**项目背景 → 大师与小白对话 → 项目实战 → 项目总结**。建议每章正文 **3000～5000 字**（中文）。

## 范围确认（Scope）

| 项目 | 决议 |
|------|------|
| 主线路章节数 | **36 章**（满足「30～40 章」区间） |
| 可选扩展（第 37～40 章） | 不纳入主线路文件编号；需要时从下列主题独立加章：`Wasm` 扩展、`Telemetry API`、与 OPA/Gatekeeper 协同、eBPF 观测补充、CI 中的 `istioctl analyze`、混沌工程专项、源码导读（Pilot 推送路径、xDS/ADS） |
| 与仓库其他文档 | [istio-ug.md](../../istio-ug.md) 可作安装与可观测性素材；本目录章节为推广专用完整叙事 |

## 文件命名与目录约定

- 路径：`docs/istio-promotion/`
- 章节文件：`第 NN 章：章节标题.md`（`NN` 为两位序号 **01～36**，便于排序；标题与正文一级标题一致）
- 说明：Windows 文件名不可用 ASCII `/`，原 `Kind/Minikube`、`Header/URI`、`A/B` 等在文件名中已改为全角 `／`；配套文件仍为 `README.md`、`LAB-KIT.md`、`REVIEW.md`

## 章节索引

### 第一篇：认知与上手（基础）

| 序号 | 文件（可点击打开） | 标题摘要 |
|------|-------------------|----------|
| 01 | [第 01 章：微服务之后，为什么还需要一层「网格」](<第 01 章：微服务之后，为什么还需要一层「网格」.md>) | 微服务之后，为什么还需要一层「网格」 |
| 02 | [第 02 章：Istio 版图：控制面、数据面、生态位](<第 02 章：Istio 版图：控制面、数据面、生态位.md>) | Istio 版图：控制面、数据面、生态位 |
| 03 | [第 03 章：实验环境：Kind／Minikube + 版本与镜像策略](<第 03 章：实验环境：Kind／Minikube + 版本与镜像策略.md>) | 实验环境：Kind／Minikube + 版本与镜像策略 |
| 04 | [第 04 章：istioctl 与安装：Profile 到底选谁](<第 04 章：istioctl 与安装：Profile 到底选谁.md>) | istioctl 与安装：Profile 到底选谁 |
| 05 | [第 05 章：Sidecar 注入：标签、修订版与 Pod 生命周期](<第 05 章：Sidecar 注入：标签、修订版与 Pod 生命周期.md>) | Sidecar 注入：标签、修订版与 Pod 生命周期 |
| 06 | [第 06 章：流量入口初识：Service、ClusterIP 到 Envoy](<第 06 章：流量入口初识：Service、ClusterIP 到 Envoy.md>) | 流量入口初识：Service、ClusterIP 到 Envoy |
| 07 | [第 07 章：VirtualService：路由与权重](<第 07 章：VirtualService：路由与权重.md>) | VirtualService：路由与权重 |
| 08 | [第 08 章：DestinationRule：子集、负载均衡、连接池](<第 08 章：DestinationRule：子集、负载均衡、连接池.md>) | DestinationRule：子集、负载均衡、连接池 |
| 09 | [第 09 章：Gateway + VirtualService：南北向流量](<第 09 章：Gateway + VirtualService：南北向流量.md>) | Gateway + VirtualService：南北向流量 |
| 10 | [第 10 章：ServiceEntry：调用集群外服务](<第 10 章：ServiceEntry：调用集群外服务.md>) | ServiceEntry：调用集群外服务 |

### 第二篇：流量工程与韧性（中级）

| 序号 | 文件（可点击打开） | 标题摘要 |
|------|-------------------|----------|
| 11 | [第 11 章：超时、重试、熔断与重试风暴](<第 11 章：超时、重试、熔断与重试风暴.md>) | 超时、重试、熔断与重试风暴 |
| 12 | [第 12 章：流量镜像：影子流量与验收](<第 12 章：流量镜像：影子流量与验收.md>) | 流量镜像：影子流量与验收 |
| 13 | [第 13 章：故障注入：延迟与错误码](<第 13 章：故障注入：延迟与错误码.md>) | 故障注入：延迟与错误码 |
| 14 | [第 14 章：Header／URI 匹配与金丝雀路由](<第 14 章：Header／URI 匹配与金丝雀路由.md>) | Header／URI 匹配与金丝雀路由 |
| 15 | [第 15 章：金丝雀发布：从 5% 到 100% 的运维剧本](<第 15 章：金丝雀发布：从 5% 到 100% 的运维剧本.md>) | 金丝雀发布：从 5% 到 100% 的运维剧本 |
| 16 | [第 16 章：A／B 与基于用户的粘性路由](<第 16 章：A／B 与基于用户的粘性路由.md>) | A／B 与基于用户的粘性路由 |
| 17 | [第 17 章：多集群入门：单控制面与多网络概念](<第 17 章：多集群入门：单控制面与多网络概念.md>) | 多集群入门：单控制面与多网络概念 |
| 18 | [第 18 章：多集群流量：locality 与 failover](<第 18 章：多集群流量：locality 与 failover.md>) | 多集群流量：locality 与 failover |
| 19 | [第 19 章：EnvoyFilter：何时用、何时不用](<第 19 章：EnvoyFilter：何时用、何时不用.md>) | EnvoyFilter：何时用、何时不用 |

### 第三篇：可观测性与排障（中级）

| 序号 | 文件（可点击打开） | 标题摘要 |
|------|-------------------|----------|
| 20 | [第 20 章：指标：Istio 标准指标与 RED](<第 20 章：指标：Istio 标准指标与 RED.md>) | 指标：Istio 标准指标与 RED |
| 21 | [第 21 章：分布式追踪：Trace 与 baggage](<第 21 章：分布式追踪：Trace 与 baggage.md>) | 分布式追踪：Trace 与 baggage |
| 22 | [第 22 章：访问日志：格式、字段与脱敏](<第 22 章：访问日志：格式、字段与脱敏.md>) | 访问日志：格式、字段与脱敏 |
| 23 | [第 23 章：Kiali：拓扑与配置校验](<第 23 章：Kiali：拓扑与配置校验.md>) | Kiali：拓扑与配置校验 |
| 24 | [第 24 章：排障：istioctl proxy-config 与 analyze](<第 24 章：排障：istioctl proxy-config 与 analyze.md>) | 排障：istioctl proxy-config 与 analyze |
| 25 | [第 25 章：SLO 与告警：网格视角 Golden Signals](<第 25 章：SLO 与告警：网格视角 Golden Signals.md>) | SLO 与告警：网格视角 Golden Signals |

### 第四篇：零信任与安全（中高级）

| 序号 | 文件（可点击打开） | 标题摘要 |
|------|-------------------|----------|
| 26 | [第 26 章：mTLS：PeerAuthentication 与渐进式开启](<第 26 章：mTLS：PeerAuthentication 与渐进式开启.md>) | mTLS：PeerAuthentication 与渐进式开启 |
| 27 | [第 27 章：RequestAuthentication 与 JWT](<第 27 章：RequestAuthentication 与 JWT.md>) | RequestAuthentication 与 JWT |
| 28 | [第 28 章：AuthorizationPolicy：命名空间到工作负载](<第 28 章：AuthorizationPolicy：命名空间到工作负载.md>) | AuthorizationPolicy：命名空间到工作负载 |
| 29 | [第 29 章：网关 TLS 终止与证书轮转](<第 29 章：网关 TLS 终止与证书轮转.md>) | 网关 TLS 终止与证书轮转 |
| 30 | [第 30 章：安全运维：策略变更与审计](<第 30 章：安全运维：策略变更与审计.md>) | 安全运维：策略变更与审计 |

### 第五篇：平台与生命周期（中高级）

| 序号 | 文件（可点击打开） | 标题摘要 |
|------|-------------------|----------|
| 31 | [第 31 章：IstioOperator 与 Helm](<第 31 章：IstioOperator 与 Helm.md>) | IstioOperator 与 Helm |
| 32 | [第 32 章：升级与回滚：修订版与金丝雀升级 istiod](<第 32 章：升级与回滚：修订版与金丝雀升级 istiod.md>) | 升级与回滚：修订版与金丝雀升级 istiod |
| 33 | [第 33 章：资源与性能：Sidecar 成本与调优](<第 33 章：资源与性能：Sidecar 成本与调优.md>) | 资源与性能：Sidecar 成本与调优 |
| 34 | [第 34 章：Ambient 与 Istio CNI](<第 34 章：Ambient 与 Istio CNI.md>) | Ambient 与 Istio CNI |
| 35 | [第 35 章：多集群与多网络进阶](<第 35 章：多集群与多网络进阶.md>) | 多集群与多网络进阶 |
| 36 | [第 36 章：企业落地路线图：POC → 试点 → 全量](<第 36 章：企业落地路线图：POC → 试点 → 全量.md>) | 企业落地路线图：POC → 试点 → 全量 |

## 阅读顺序

建议按 **第 01 章 → 第 36 章** 阅读；第二篇可与第三篇穿插（例如先完成第 20 章再回第 14 章）视团队排期调整。
