# 第 4 章：istioctl 与安装：Profile 到底选谁

**受众提示**：运维负责安装与变更窗口；开发了解 Profile 差异避免「本地 demo、线上 minimal」认知错位；测试关注附加组件是否齐全。

**实验基线**：[LAB-KIT.md](LAB-KIT.md)。

---

## 1. 项目背景

Istio 安装路径主要有 **`istioctl install`** 与 **Helm/IstioOperator**（第 31 章深入）。入门推广阶段，`istioctl` 最直观：一条命令选择 **profile**，附带内置组件组合（如 Prometheus、Kiali 是否装上）。错误选择会导致：**demo 太重占满笔记本**、**minimal 缺监控学员跟不上**。

本章回答：常见 profile 含义、教学与生产的边界、如何用 `istioctl verify` / `analyze` 做安装后验收。

---

## 2. 项目设计：大师与小白的对话

**小白**：文档里 demo、default、minimal、empty，我选哪个？

**大师**：**内训与 POC** 常用 `demo`：带示例组件，省心。接近生产可用 `default` 或 `minimal` 再按需加 addon。`empty` 几乎不装，适合高级定制。

**小白**：demo 能直接上生产吗？

**大师**：**不建议**。demo 打开的东西多，攻击面与资源占用都偏大。生产按**最小权限 + 基线监控**逐步加。

**小白**：`istioctl` 和 `kubectl` 啥关系？

**大师**：`istioctl` 是 Istio 专用 CLI：安装、升级、Sidecar 诊断、配置差异分析。日常路由仍是 `kubectl apply` YAML。

**小白**：装完怎么知道成没成？

**大师**：`istioctl verify-install`、`istioctl analyze`，再加 `kubectl get pods -n istio-system`。别只看「命令没报错」。

**要点清单**

1. **教学**：偏 `demo`；**生产**：偏 `default`/`minimal` + 显式运维项。
2. 安装与升级前读**发行说明**中的 breaking changes。
3. 把 `istioctl version` 输出贴到工单，便于排障。

---

## 3. 项目实战

> 版本与参数以当前 Istio 为准；下列为常见模式示例。

### 3.1 下载与 PATH

```bash
# 官方一键下载（Linux/macOS 常见）
curl -L https://istio.io/downloadIstio | sh -
cd istio-1.xx.x
export PATH=$PWD/bin:$PATH
istioctl version
```

### 3.2 安装 demo profile（实验环境）

```bash
istioctl install --set profile=demo -y
kubectl get pods -n istio-system -w
```

### 3.3 验收

```bash
istioctl verify-install
istioctl analyze --all-namespaces
```

### 3.4 查看当前生效配置（概念）

```bash
istioctl profile dump demo
```

生产向可改为 `minimal` 并分步启用遥测、ingress 网关等（结合第 20、31 章）。

---

## 4. 项目总结

**优点**

- `istioctl` **交互友好**，适合工作坊逐步演示。
- Profile **封装最佳实践组合**，降低初次认知负担。

**缺点**

- 隐藏细节多，**「一键」不等于「理解」**。
- 团队若已全面 Helm 化，需统一**单一事实来源**（Git 中的 values）。

**适用场景**

- 学习与中等规模集群首次落地。
- 需要快速起 **addon** 做可观测性演示时。

**注意事项**

- **版本 skew**：`istioctl` 版本应匹配安装的 Istio 控制面版本。
- 变更窗口内保留**旧 revision** 做金丝雀（第 32 章）。

**常见踩坑**

1. **现象**：`analyze` 报大量信息级问题。**原因**：CRD 未完全就绪或示例命名空间未 label。**处理**：等待 Ready，区分 Error vs Info。
2. **现象**：笔记本风扇狂转。**原因**：demo + Prometheus + Grafana 全量。**处理**：换 minimal + 外部监控，或加大 Docker 资源。
3. **现象**：多人重复执行 `install` 互相覆盖。**原因**：无 GitOps 锁。**处理**：第 31 章引入 Operator/Helm 与变更评审。

---

### 4.1 再谈一个场景：demo 上生产

某团队为赶进度把 `demo` profile 直接用于准生产，随后出现监控缺失与额外组件攻击面。推广时要明确：**profile 是起点，不是终点**；生产 checklist 必须包含：组件最小化、ingress/egress 控制、证书与密钥管理、备份与升级路径（第 31、32 章）。

### 4.2 与团队工具链对齐

若团队已使用 Helmfile、Argo CD，应尽早把「istioctl 演示」迁移为「Git 中的声明式 values」，避免两套真相。

---

## 附：自测与练习

### 自测题

1. `istioctl install` 与纯 Helm 安装各有什么优劣语境？
2. `istioctl verify-install` 与 `istioctl analyze` 分别验证什么？
3. 为什么 `istioctl` 客户端版本建议与控制面对齐？

### 动手作业

导出当前集群使用的 profile dump（脱敏后）存入 Git，标注与 LAB-KIT 的差异及原因。

**延伸阅读**：Istio *Installation Guides*；`istioctl install -h` 当前选项。

---

### 4.5 本章小结：把知识变成「可执行」

推广 Istio 时，最容易失败的不是「不会配」，而是「配了没人敢动」。建议每章学完后，在团队看板增加一张卡片：**谁能在生产执行与本章相关的变更**、**需要哪些审批**、**回滚命令是什么**。技术文档若不能映射到流程，就仍是幻灯片。

### 4.6 与第 24、31、36 章的联动提示

- **排障**：把 `istioctl analyze` 与 `proxy-config` 写进 oncall 第一步（第 24 章）。
- **交付**：把安装与 values 纳入 GitOps（第 31 章）。
- **治理**：把试点范围、预算与里程碑写成公开路线图（第 36 章）。
