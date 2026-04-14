# 第 5 章：Sidecar 注入：标签、修订版与 Pod 生命周期

**受众提示**：运维管命名空间与工作负载标签；开发理解 Pod 内多容器与启动顺序；测试理解「未注入」与「已注入」行为差异。

**实验基线**：[LAB-KIT.md](LAB-KIT.md)。

---

## 1. 项目背景

Sidecar 注入是 Istio 的**门面工程**：未注入的 Pod 流量不经过 Envoy，网格策略**全部不生效**。注入方式主要有 **自动注入**（namespace/workload label）与 **手动**（`istioctl kube-inject` 或注解）。生产环境常配合 **revision**（如 `istio.io/rev=canary`）实现控制面金丝雀升级：同一集群可并存多版本 istiod，按命名空间或工作负载逐步迁移。

本章讲清：**谁被注入**、**何时重建 Pod**、**升级时如何滚动**，避免推广时出现「我改了 YAML 为啥线上不生效」的经典误解。

---

## 2. 项目设计：大师与小白的对话

**小白**：我给 Deployment 加了注解，但没重启 Pod，是不是没注入？

**大师**：**对**。Envoy Sidecar 是 Pod 规约的一部分，**旧 Pod 不会凭空长出 Sidecar**。要滚动重启。

**小白**：命名空间打了 `istio-injection=enabled` 就稳了吗？

**大师**：若使用 **revision**，通常要对应 `istio.io/rev=<rev>` 或兼容你集群的注入方式。团队要在文档里写死**唯一正确标签组合**，避免口口相传。

**小白**：Init 容器是干啥的？

**大师**：常见是 **iptables 重定向**（或 CNI 等模式），把出站/入站流量导到 Envoy。排障时看 Init 容器是否 exit 0。

**小白**：Sidecar 挂了，业务 Pod 还算 Running 吗？

**大师**：取决于**重启策略**与探针。若 Sidecar 非主容器，可能业务仍 Running 但流量异常——要用**就绪探针 + 网格健康检查**综合看。

**要点清单**

1. 注入变更 = **Pod 模板变更** → 需要滚动。
2. revision 与 namespace label **成对**出现。
3. 排障顺序：`describe pod` → Init → `istio-proxy` 日志。

---

## 3. 项目实战

### 3.1 命名空间自动注入（示例）

```bash
kubectl get ns default --show-labels
kubectl label namespace default istio-injection=enabled --overwrite
```

若使用 revision（示例名 `canary`）：

```bash
kubectl label namespace app-ns istio.io/rev=canary --overwrite
```

### 3.2 部署示例并检查 Sidecar

```bash
kubectl apply -f samples/bookinfo/platform/kube/bookinfo.yaml
kubectl get pods

kubectl get pod -l app=productpage -o jsonpath="{.spec.containers[*].name}"
# 期望输出包含 istio-proxy（名称因版本可能略有差异）
```

### 3.3 强制滚动重启（注入策略变更后）

```bash
kubectl rollout restart deployment/<name> -n <namespace>
```

### 3.4 查看注入与元数据

```bash
kubectl describe pod -l app=productpage | findstr /i "istio"
```

---

## 4. 项目总结

**优点**

- 声明式注入，**与 GitOps 兼容**；
- revision 支持**平滑升级**控制面。

**缺点**

- Pod 内多容器，**排障心智**变复杂；
- 注入遗漏是**最高频**「策略不生效」原因。

**适用场景**

- 微服务默认**全量注入**；对无状态服务最顺。

**注意事项**

- **Job/CronJob**、**DaemonSet**、**有状态**工作负载需单独评审。
- 资源 limit 要 **app + sidecar** 一起算。

**常见踩坑**

1. **现象**：`analyze` 正常但路由不生效。**原因**：Pod 未注入。**处理**：label + rollout。
2. **现象**：部分 Pod 旧 revision。**原因**：滚动未完成或 HPA 创建了新 RS。**处理**：`kubectl get rs` 与 `rollout status`。
3. **现象**：Init CrashLoop。**原因**：与主机网络/CNI 冲突或权限。**处理**：对照 Istio CNI 章节（第 34 章）与平台日志。

---

### 4.1 再谈一个场景：Job 与 CronJob

批处理任务若被注入 Sidecar，可能出现「主容器退出但 Pod 不结束」的经典问题。推广清单里要对 **Job 类工作负载**单独打勾：关闭注入、或采用适配模式。否则排障会长期消耗平台信用。

### 4.2 与发布系统的协同

滚动发布系统若快速多次变更 Pod 模板，可能放大注入配置错误的影响面。建议把「注入标签变更」设为**单独变更类型**，需要额外审批与观察窗口。

---

## 附：自测与练习

### 自测题

1. 为什么修改注入策略后必须滚动 Pod？
2. `istio-injection=enabled` 与 `istio.io/rev` 可能如何共存或互斥（依你们版本实践）？
3. Init 容器失败时，优先查看哪些信息？

### 动手作业

在一个测试 Deployment 上切换注入开关各一次，截图 `kubectl describe pod` 中容器列表变化，并写入内部笔记。

**延伸阅读**：Istio *Sidecar Injection*；升级与 revision 见第 32 章。

---

### 4.5 本章小结：把知识变成「可执行」

推广 Istio 时，最容易失败的不是「不会配」，而是「配了没人敢动」。建议每章学完后，在团队看板增加一张卡片：**谁能在生产执行与本章相关的变更**、**需要哪些审批**、**回滚命令是什么**。技术文档若不能映射到流程，就仍是幻灯片。

### 4.6 与第 24、31、36 章的联动提示

- **排障**：把 `istioctl analyze` 与 `proxy-config` 写进 oncall 第一步（第 24 章）。
- **交付**：把安装与 values 纳入 GitOps（第 31 章）。
- **治理**：把试点范围、预算与里程碑写成公开路线图（第 36 章）。
