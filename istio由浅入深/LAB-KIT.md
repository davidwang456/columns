# 实验基线（Lab Kit）

推广与撰写章节时，请统一以下基线，保证命令与截图可复现。若团队升级版本，**只改本文件一处**，并在各章「项目实战」中注明「以 LAB-KIT 为准」。

## Kubernetes

| 项 | 建议值 | 说明 |
|----|--------|------|
| 发行版 | Kind / 云厂商托管 K8s 均可 | 本地推荐 Kind，单节点即可 |
| 版本 | **1.28～1.30**（示例） | 与 Istio 支持矩阵对齐；以 [Istio 官方文档](https://istio.io/latest/docs/releases/supported-releases/) 为准 |
| 节点 | 至少 2 vCPU / 4 GiB 内存（单节点） | Bookinfo + 可观测组件偏吃内存 |
| CNI | 默认即可 | 若试验 Ambient/CNI 相关章节，按该章要求单独建集群 |

## Istio

| 项 | 建议值 | 说明 |
|----|--------|------|
| 安装方式 | `istioctl install` | 与推广章节一致 |
| Profile | `demo`（学习） / `minimal`+addon（进阶） | 生产勿直接使用 `demo` |
| 修订版（Revision） | 教学可默认 `default`；升级章节用 `canary` 等显式 revision | 与第 32 章联动 |
| 命名空间 | 业务命名空间打 `istio-injection=enabled` 或等价 | 与第 5 章一致 |

## 示例应用

| 项 | 建议值 |
|----|--------|
| 默认示例 | **Bookinfo**（`samples/bookinfo`） |
| 变体 | 金丝雀/镜像类章节可为 reviews 增加 v2/v3；外部依赖用 `httpbin.org` 等（注意网络策略） |

## 可观测性（与章节对应）

| 组件 | 用途 |
|------|------|
| Prometheus + Grafana | 第 20、25 章 |
| Jaeger 或 Zipkin | 第 21 章 |
| Kiali | 第 23 章 |
| `istioctl dashboard` | 可选，本地演示 |

## 验证习惯（全员）

1. 变更后：`kubectl get pods,svc` 与 `istioctl analyze -n <ns>`。
2. Sidecar 相关：`istioctl proxy-config cluster|route|listener`（见第 24 章）。
3. 对外访问：以 `Gateway` 或 `kubectl port-forward` 为准，文档中写清入口 IP/端口。

## 版本漂移时的处理

- 在章节文首增加一行：**实验基线见 [LAB-KIT.md](LAB-KIT.md)，本章编写基于 Istio x.y.z / K8s a.b.c。**
- 命令若随版本变化，在「项目总结」增加「版本差异」一条。
