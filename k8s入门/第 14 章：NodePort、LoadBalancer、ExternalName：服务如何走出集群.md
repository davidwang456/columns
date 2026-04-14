# 第 14 章：NodePort、LoadBalancer、ExternalName：服务如何走出集群

> 对应学习计划第 1 周 Service 四种类型的对比理解。

上一章解决的是“集群内部如何稳定访问服务”，这一章要解决的则是另一个现实问题：**如果调用方不在集群里，服务应该怎么暴露出去？** 很多新手第一次做外部访问时，最容易走向两个极端：要么把所有服务都做成 `NodePort`，把节点端口开得到处都是；要么一看到 `LoadBalancer` 就觉得这一定是终极答案。实际上，不同暴露方式解决的是不同环境下的不同问题。

## 1）项目背景

Kubernetes 的 Service 在“对外暴露”这件事上，常见有几种路径：

- `ClusterIP`：只在集群内部可用。
- `NodePort`：把服务暴露到每个节点的某个固定端口上。
- `LoadBalancer`：由云厂商或外部负载均衡器提供一个对外入口。
- `ExternalName`：不是暴露自身服务，而是把一个服务名映射到外部域名。

为什么要搞这么多类型？因为集群所在环境差异非常大：

- 本地学习环境里，通常没有云 LB，NodePort 更容易演示。
- 云环境里，LoadBalancer 更符合生产入口需求。
- 某些场景不是要暴露本地服务，而是要把集群内服务名指向外部系统，这才轮到 ExternalName。

初学者常见误区主要有两个：

- 把 NodePort 当成“通用外部访问方案”，导致端口暴露混乱，安全和运维成本都很高。
- 以为 LoadBalancer 是 Kubernetes 天然内建能力，而忽略了它往往依赖云厂商能力或额外控制器。

所以，本章最重要的不是背几种类型名称，而是形成一个判断模型：**调用方在哪里、暴露粒度是什么、环境有没有外部 LB 能力、是否需要真正对外发布。**

## 2）项目设计：通过大师和小白的对话引出该主题

**小白**：大师，我想让本地浏览器访问集群里的服务，是不是所有 Service 都应该改成 NodePort？

**大师**：学习阶段可以用 NodePort 演示，但别把它当成默认答案。它只是解决“从节点端口把流量引进来”的一种简单方式。

**小白**：那生产是不是直接上 LoadBalancer？

**大师**：很多云环境确实会这么做，但要记住，LoadBalancer 往往依赖云厂商或外部控制器支持。不是你写了 `type: LoadBalancer`，任何环境都一定能自动给你外网地址。

**小白**：那 ExternalName 又是干什么的？名字听起来有点不像“暴露服务”。

**大师**：它本来就不是把你的服务暴露出去，而是让集群内通过一个 Kubernetes 服务名去访问外部域名。比如把 `mysql-ext` 映射到公司现有数据库域名。

**小白**：也就是说，几种 Service 类型不是“谁更高级”，而是谁适合哪个访问方向和环境？

**大师**：完全正确。NodePort 更像实验和过渡手段；LoadBalancer 更偏生产入口；ExternalName 更像服务名映射工具。

**小白**：那我是不是应该先问“谁来访问”，再决定用哪种类型？

**大师**：对。先看调用路径，再看环境能力，最后再看对象类型。

## 3）项目实战：通过主代码片段使用该主题的对象

这一节我们用最小示例分别看 `NodePort`、`LoadBalancer` 和 `ExternalName` 的表达方式。

### 第一步：NodePort 示例

新建 `web-nodeport.yaml`：

```yaml
apiVersion: v1
kind: Service
metadata:
  name: web-nodeport
spec:
  type: NodePort
  selector:
    app: web
  ports:
  - port: 80
    targetPort: 80
    nodePort: 30080
```

应用后：

```bash
kubectl apply -f web-nodeport.yaml
kubectl get svc web-nodeport
```

在本地实验环境中，你通常可以通过 `节点IP:30080` 访问它。这种方式简单直观，非常适合学习阶段验证“集群外访问”的基本概念。

### 第二步：LoadBalancer 示例

```yaml
apiVersion: v1
kind: Service
metadata:
  name: web-lb
spec:
  type: LoadBalancer
  selector:
    app: web
  ports:
  - port: 80
    targetPort: 80
```

在支持外部负载均衡的环境中，你执行：

```bash
kubectl apply -f web-lb.yaml
kubectl get svc web-lb
```

你可能会看到一个 `EXTERNAL-IP` 被分配出来。但在本地 `kind`、`minikube` 等环境中，这个值往往会停留在 `pending`，因为并没有云厂商替你创建负载均衡器。

### 第三步：ExternalName 示例

```yaml
apiVersion: v1
kind: Service
metadata:
  name: external-db
spec:
  type: ExternalName
  externalName: db.example.internal
```

这个对象不会选中任何 Pod，也不会暴露你的应用，而是让集群内通过 `external-db` 这个名字去解析 `db.example.internal`。这类用法很适合把外部已有系统“接入”到集群内的服务名体系里。

### 第四步：形成一个选择思路

可以用下面这个简单框架来判断：

- 只是集群内访问：`ClusterIP`
- 本地学习或临时调试：`NodePort`
- 云环境正式对外入口：优先考虑 `LoadBalancer` 或配合 Ingress
- 集群内访问外部域名：`ExternalName`

### 第五步：不要把“能访问”误当成“适合长期使用”

NodePort 最大的诱惑，就是“最简单就能看到效果”。但简单不等于适合生产。端口管理、安全暴露面、统一入口治理，这些问题只要环境一复杂，就会迅速放大。

### 这一节应该带走什么

- Service 暴露方式的选择，首先由访问路径和环境决定。
- NodePort 适合学习和过渡，不适合无脑推广成所有服务的默认入口。
- LoadBalancer 往往依赖外部基础设施能力，不是纯对象声明就能凭空生效。
- ExternalName 用于“映射外部服务名”，而不是“暴露本地服务”。

## 4）项目总结：总结该主题对象的优点和缺点，使用场景，注意事项和常见踩坑经验

### 优点

- 它让服务暴露能力能够随着环境变化灵活选择，而不是一刀切。
- NodePort、LoadBalancer、ExternalName 分别覆盖了学习、生产和外部映射等不同场景。
- 通过清晰的类型划分，平台可以把“内部访问”和“外部访问”明确区分开来。

### 缺点

- 类型一多，初学者容易混淆“暴露自己”和“映射外部”的不同语义。
- 某些类型强依赖环境能力，例如 LoadBalancer 依赖云或外部控制器。
- 如果缺少统一入口治理，服务暴露很容易变得混乱。

### 使用场景

- 本地调试和教学演示时使用 NodePort。
- 云环境正式对外发布时使用 LoadBalancer 或其上层方案。
- 需要在集群内引用外部服务名时使用 ExternalName。

### 注意事项

- 不要默认把所有服务都做成外网可访问。
- 先确认环境是否支持 LoadBalancer，再决定是否依赖它。
- ExternalName 不会创建后端 Pod，也不会做流量代理，只是 DNS 级映射。

### 常见踩坑经验

- 最常见的坑，是所有服务都顺手开成 NodePort，最后端口分配和安全边界一团乱。
- 第二个坑，是在本地环境里写了 LoadBalancer 就期待自动拿到外网地址。
- 第三个坑，是把 ExternalName 当成“转发代理”，结果发现它根本不负责代理流量。

这一章真正想建立的，是一种入口设计意识：**不是所有服务都应该“被看见”，更不是所有环境都该用同一种暴露方式。**
