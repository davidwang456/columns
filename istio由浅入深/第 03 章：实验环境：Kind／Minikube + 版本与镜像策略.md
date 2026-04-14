# 第 3 章：实验环境：Kind/Minikube + 版本与镜像策略

**受众提示**：运维主导集群创建与镜像拉取；开发关注本地与 CI 一致性；测试关注环境可快速重置。

**实验基线**：[LAB-KIT.md](LAB-KIT.md)。

---

## 1. 项目背景

推广材料若无法复现，信任会迅速流失。本章固定**实验环境**打法：本地 **Kind**（Kubernetes in Docker）或 **Minikube** 适合个人与 CI；团队共享环境可用云托管开发集群。镜像策略上，需考虑**国内拉取**、**版本锁定**与 **Istio 与 K8s 小版本兼容**。Bookinfo 作为跨章节示例，应在同一套环境里反复演练，避免「讲师机器能跑、学员全红」。

目标：读完本章，任何人能按清单起一个「可装 Istio、可跑 Bookinfo」的集群，并理解**为何要锁版本**。

---

## 2. 项目设计：大师与小白的对话

**小白**：我用 Docker Desktop 自带的 K8s 行不行？

**大师**：可以，但难复现。推广课建议 **Kind + 固定 config**，一条脚本起集群，PR 里可审。

**小白**：Kind 和 Minikube 选谁？

**大师**：CI 友好选 Kind；需要模拟多节点网络可 Kind 多 worker。Minikube 对新手 GUI 友好。统一写进 LAB-KIT，别每人一套。

**小白**：Istio 镜像拉不下来怎么办？

**大师**：三条路：**镜像代理**、**私有仓库同步**、**离线 tarball**。运维要在文档里写明，别留给讲师现场发挥。

**小白**：K8s 1.27 和 1.29 差很多吗？

**大师**：对 Istio 来说，看**官方支持矩阵**。推广期宁可全班同一小版本，减少「我这边 CRD 行为不一样」。

**要点清单**

1. **可复现 > 最新**：锁 K8s 与 Istio 小版本。
2. **脚本化** 集群创建与销毁，方便测试重置环境。
3. 镜像与 Helm 值**进 Git**，变更可审计。

---

## 3. 项目实战

以下示例以 **Kind** 与 **Windows/macOS/Linux** 通用思路为主；路径请按本机调整。具体版本以 [LAB-KIT.md](LAB-KIT.md) 为准。

### 3.1 安装 Kind 与 kubectl

```bash
# 示例：通过官方 release 安装 kind 与 kubectl（略，按 OS 选择包）
kind version
kubectl version --client
```

### 3.2 集群配置文件 `kind-config.yaml`（节选：单控制面 + 端口映射可选）

```yaml
kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
nodes:
- role: control-plane
  kubeadmConfigPatches:
  - |
    kind: InitConfiguration
    nodeRegistration:
      kubeletExtraArgs:
        node-labels: "ingress-ready=true"
  extraPortMappings:
  - containerPort: 80
    hostPort: 80
    protocol: TCP
  - containerPort: 443
    hostPort: 443
    protocol: TCP
```

```bash
kind create cluster --name istio-lab --config kind-config.yaml
kubectl cluster-info --context kind-istio-lab
```

### 3.3 资源与健康检查

```bash
kubectl get nodes -o wide
kubectl get pods -A
```

### 3.4 Bookinfo 前置

后续章节将 `kubectl label namespace default istio-injection=enabled` 并部署 Bookinfo；本章只保证集群 Ready。

---

## 4. 项目总结

**优点**

- Kind 可**快速销毁/重建**，适合讲「从零到通」。
- 版本锁定后，**排障可对比**。

**缺点**

- Docker 资源占用；低配机器可能卡。
- 与生产云厂商网络/存储差异大，**生产结论需另测**。

**适用场景**

- 内训、工作坊、POC；CI 流水线中的**烟雾测试**。

**注意事项**

- **时区与 DNS** 在企业网络中常出问题，提前在 Runbook 写明。
- Mac M 系列与部分镜像架构需对齐（`platform`）。

**常见踩坑**

1. **现象**：Kind 节点 NotReady。**原因**：Docker 资源不足或 cgroup。**处理**：给 Docker 加内存/CPU，重建集群。
2. **现象**：`localhost:80` 不通。**原因**：端口映射与本地防火墙冲突。**处理**：改 hostPort 或改用 `kubectl port-forward`。
3. **现象**：学员混用多个 kubeconfig context。**原因**：未固定 `kubectl config use-context`。**处理**：实验前统一打印当前 context。

---

### 4.1 再谈一个场景：CI 里的「可重复」

推广材料若只在讲师笔记本上验证通过，团队信任度会打折。建议把「创建集群 + 安装 Istio + 部署 Bookinfo + 一条冒烟 curl」做成流水线 Job，**失败即红**，作为文档与脚本的持续契约。镜像与版本号写入流水线变量，与 LAB-KIT 同步。

### 4.2 学员环境差异处理

Windows/macOS/Linux 的 shell、路径与 Docker 资源不同。文档中优先给出**可复制**命令，对平台差异单独开「附录：平台注记」，避免正文支离破碎。

---

## 附：自测与练习

### 自测题

1. 为什么实验环境要锁定 Kubernetes 与 Istio 小版本？
2. Kind 与 Minikube 各自更适合什么场景？
3. 「镜像拉取失败」时，至少列出三种解决思路。

### 动手作业

在干净机器上按 LAB-KIT 起集群并记录耗时与卡点；把卡点补进团队 FAQ。

**延伸阅读**：Kind 官方文档；Istio *Platform Setup*。

---

### 4.5 本章小结：把知识变成「可执行」

推广 Istio 时，最容易失败的不是「不会配」，而是「配了没人敢动」。建议每章学完后，在团队看板增加一张卡片：**谁能在生产执行与本章相关的变更**、**需要哪些审批**、**回滚命令是什么**。技术文档若不能映射到流程，就仍是幻灯片。

### 4.6 与第 24、31、36 章的联动提示

- **排障**：把 `istioctl analyze` 与 `proxy-config` 写进 oncall 第一步（第 24 章）。
- **交付**：把安装与 values 纳入 GitOps（第 31 章）。
- **治理**：把试点范围、预算与里程碑写成公开路线图（第 36 章）。
