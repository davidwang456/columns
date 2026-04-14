# 第 13 章：Service ClusterIP：给易变 Pod 一个稳定入口

> 对应学习计划第 1 周 Service 与服务发现。

学到这里，很多人会第一次真正意识到一个问题：Pod 明明能跑服务，为什么系统里还需要 Service？原因很朴素，因为 Pod 虽然能跑，但它的 IP 是易变的、实例是可替换的，而业务调用需要一个稳定入口。ClusterIP Service 的意义，就是在“实例会变”和“调用要稳”之间，给系统建立一个稳定的中间层。

## 1）项目背景

Pod 的设计是“可替换实例”。这在平台层是优点，因为坏了就重建、升级就替换、扩缩容也更灵活；但在调用层会立刻引出一个问题：如果后端实例经常重建，客户端该连谁？

假设没有 Service，你只能靠下面这些方式来调用：

- 直接写死 Pod IP。
- 自己维护一份实例列表。
- 容器重建后手动改配置。
- 在业务代码里自建服务发现逻辑。

这些做法在单机时代也许还能忍，但在 Kubernetes 中几乎注定不可持续。因为 Pod 的 IP 可以变化，副本数可以变化，实例的生命周期也可以随时被控制器接管。平台需要一种方式，让调用方不必关心“后面到底是哪几个 Pod”，而只关心“这个服务叫什么”。

ClusterIP Service 正是这个抽象：

- 给一组 Pod 提供一个稳定的虚拟 IP。
- 通过标签选择器把后端 Pod 自动收进来。
- 结合集群 DNS，让调用方通过服务名访问，而不是直接盯实例地址。

同时，Service 还会和 `Endpoints` / `EndpointSlice` 一起工作，维护当前可用后端列表。这意味着，当 Pod 被替换、扩容、摘流时，调用方看到的服务名不变，平台在背后悄悄调整真实后端。

这套机制的价值非常大，因为它让“实例管理”和“服务调用”彻底解耦。开发者不再需要关心 Pod 变化，而是把精力放在“服务依赖关系”本身。

## 2）项目设计：通过大师和小白的对话引出该主题

**小白**：大师，我已经有 3 个 Web Pod 了，容器里也能看到各自的 IP。那我是不是直接让前端去调这些 IP 就行了？

**大师**：今天可以，明天就不一定了。Pod 重建、扩容、缩容之后，这些 IP 很可能就变了。

**小白**：可总得有个地址能访问后端吧？

**大师**：所以才需要 Service。Service 的作用，就是在“后端实例会变”这个现实和“调用地址要稳定”这个需求之间，建立一层稳定抽象。

**小白**：也就是说，客户端看到的是一个固定服务名，平台背后再去决定流量打到哪些 Pod？

**大师**：对。你可以把 Service 看成“面向调用方的固定门牌号”，而 Pod 是门后面不断轮换的工作人员。

**小白**：那 ClusterIP 的 `IP` 是真的有一台机器在监听吗？

**大师**：这正是有趣的地方。它通常是一个虚拟 IP，不一定对应真实网卡上的地址，而是由 kube-proxy 和网络规则在节点上把流量导到后端 Pod。

**小白**：那我以后服务间调用，最推荐的方式就是通过服务名？

**大师**：没错。用服务名，不要用 Pod IP。这不仅是方便，更是整个编排系统成立的基础前提。

## 3）项目实战：通过主代码片段使用该主题的对象

这一节我们用一个三副本 Deployment 配一个 ClusterIP Service，观察平台如何把易变 Pod 组织成一个稳定入口。

### 第一步：准备后端 Deployment

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: web
spec:
  replicas: 3
  selector:
    matchLabels:
      app: web
  template:
    metadata:
      labels:
        app: web
    spec:
      containers:
      - name: nginx
        image: nginx:1.27
        ports:
        - containerPort: 80
```

### 第二步：创建 ClusterIP Service

新建 `web-service.yaml`：

```yaml
apiVersion: v1
kind: Service
metadata:
  name: web
spec:
  selector:
    app: web
  ports:
  - port: 80
    targetPort: 80
```

应用并观察：

```bash
kubectl apply -f web-deployment.yaml
kubectl apply -f web-service.yaml
kubectl get svc web
kubectl get pods -l app=web -o wide
kubectl get endpoints web
```

这里要重点观察三件事：

- `Service` 拿到了一个稳定的 `ClusterIP`。
- 真实后端是那 3 个 Pod。
- `Endpoints` 中的后端会随着 Pod 变化自动更新。

### 第三步：在集群内通过服务名访问

你可以起一个临时测试 Pod：

```bash
kubectl run dns-test --image=busybox:1.36 -it --rm --restart=Never -- sh
```

进入后执行：

```sh
nslookup web
wget -qO- http://web
```

这一步很关键，因为它让你直观体验到：**调用方看到的是服务名，而不是 Pod IP。**

### 第四步：删除一个后端 Pod，看 Service 是否还能正常工作

```bash
kubectl delete pod -l app=web
kubectl get pods -l app=web -w
kubectl get endpoints web -w
```

你会看到旧 Pod 删除、新 Pod 补起、Endpoints 更新，而服务名和 Service 本身都保持不变。这正是 Service 抽象的意义所在。

### 第五步：理解 ClusterIP 的边界

ClusterIP 只在集群内部可访问。它适合：

- 前后端服务间调用。
- 微服务之间的内部通信。
- 集群内中间件访问。

如果你需要从集群外访问，就要继续看下一章的 `NodePort`、`LoadBalancer` 和 `ExternalName` 等暴露方式。

### 这一节应该带走什么

- Pod IP 易变，服务入口需要稳定抽象。
- ClusterIP Service 通过标签把后端 Pod 动态组织起来。
- 服务名 + DNS 是 Kubernetes 内部调用的推荐姿势。
- Service 不是实例本身，而是实例集合的稳定入口。

## 4）项目总结：总结该主题对象的优点和缺点，使用场景，注意事项和常见踩坑经验

### 优点

- 它把服务调用从实例地址中解耦出来，大幅降低了实例变更带来的影响。
- 它天然支持多副本后端和服务发现，是微服务内部通信的基线能力。
- 它与 Deployment、readiness、DNS、负载转发链路天然协同。

### 缺点

- 初学者容易把 Service 的虚拟 IP 理解成“某个真实容器地址”，从而混淆实现机制。
- 它只提供四层级别的基本访问入口，七层路由仍需 Ingress 或其他方案。
- 如果标签、探针和后端状态配置错了，Service 也可能看起来“有对象但没后端”。

### 使用场景

- 集群内服务发现和服务间调用。
- 多副本无状态服务的统一访问入口。
- 微服务体系中的基础通信抽象。

### 注意事项

- 调用时优先使用服务名，而不是 Pod IP。
- Service 的 `selector` 必须和后端 Pod 标签正确匹配。
- 如果后端 Pod 未 Ready，通常不会被加入可用后端列表。

### 常见踩坑经验

- 最常见的坑，是在配置文件或代码里硬编码 Pod IP，结果一重建就全线失效。
- 第二个坑，是标签写错，导致 Service 虽然创建成功，但没有任何后端。
- 第三个坑，是看到 Service 存在就默认网络没问题，却没有继续检查 Endpoints 和 Pod Readiness。

这一章真正想帮你建立的是一种服务视角：**业务依赖的应该是“服务”，而不是“某个具体实例”。**
