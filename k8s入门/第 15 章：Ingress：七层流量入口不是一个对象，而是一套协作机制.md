# 第 15 章：Ingress：七层流量入口不是一个对象，而是一套协作机制

> 对应学习计划中服务入口治理与后续微服务项目需求。

当系统里只有一个服务时，Service 暴露通常还不算复杂；可一旦有多个 Web 服务、多个域名、多个路径规则、还要做 HTTPS、重写、灰度和统一入口治理时，单靠 Service 类型已经不够用了。Ingress 之所以重要，不在于它又多了一个 YAML，而在于它把“七层流量入口规则”从服务对象中单独抽出来，交给专门的入口控制器去实现。

## 1）项目背景

很多人第一次接触 Ingress 时，最容易犯的误解是：我写了一个 `Ingress` 对象，流量应该就自动进来了。这个误解非常普遍，因为从 YAML 形式上看，Ingress 很像一个“定义入口规则”的资源；但如果不理解它背后的协作机制，就会觉得它“时灵时不灵”。

要真正理解 Ingress，必须先拆开两层：

- `Ingress` 资源：描述“域名、路径、TLS、转发规则”这些期望状态。
- `Ingress Controller`：真正监听并实现这些规则的控制器和代理组件。

换句话说，Ingress 对象本身更像一份“流量路由规则声明”，而不是一个自动带实现的负载均衡器。如果集群里没有对应控制器，这份规则就只是一张纸，没有执行者。

为什么这套设计很有价值？因为它把下面这些需求统一表达出来了：

- 同一个入口域名下，不同路径转发到不同服务。
- 不同子域名路由到不同业务。
- 统一做 HTTPS/TLS 终止。
- 在入口层附加访问控制、限流、重写等能力。

当系统进入微服务阶段之后，这种统一入口模型几乎是必需品。否则每个服务自己暴露一个端口、一个外网地址，不仅成本高，而且治理混乱，证书管理和路由规则也会失控。

所以，本章最重要的认识是：**Ingress 不是单独一个对象的能力，而是“声明规则 + 控制器执行 + Service 转发”的协作体系。**

## 2）项目设计：通过大师和小白的对话引出该主题

**小白**：大师，我创建了一个 Ingress，但浏览器访问就是不通。我还以为写完 YAML 就能自动生效。

**大师**：这说明你已经碰到了 Ingress 最经典的坑。Ingress 资源只负责描述规则，不负责自己实现规则。

**小白**：也就是说，我还得安装什么东西？

**大师**：对，你需要一个 Ingress Controller。它会监听这些 Ingress 资源，然后把规则变成真正的反向代理配置和流量入口。

**小白**：所以 Ingress 对象更像“配置”，Controller 才像“执行器”？

**大师**：完全正确。这和 Deployment、Controller、Pod 的关系有点像：对象描述期望，控制器负责落地。

**小白**：那为什么不直接让 Service 支持路径转发、域名规则这些能力？

**大师**：因为那会把四层转发和七层路由全都塞进一个对象，边界会很混乱。Service 更适合做基础服务发现和后端暴露，Ingress 更适合统一的 HTTP/HTTPS 入口治理。

**小白**：那我是不是可以理解成：Service 解决“服务在哪”，Ingress 解决“请求怎么进来、该走哪条路”。

**大师**：这个总结非常好。

## 3）项目实战：通过主代码片段使用该主题的对象

这一节我们做一个最小路径路由示例：同一个域名下，`/api` 转到后端服务，`/` 转到前端服务。

### 第一步：确保集群里有 Ingress Controller

在本地学习环境中，你通常需要先安装一个控制器，例如 `ingress-nginx`。否则光有 Ingress 资源不会真正生效。

如果是 `kind` 环境，通常会先安装对应的控制器清单；如果是云环境，也可能已经由平台提前提供。

### 第二步：准备两个后端 Service

假设你已经有：

- `frontend` Service
- `api` Service

它们都应当是集群内可访问的 `ClusterIP` 服务。

### 第三步：创建 Ingress 路由规则

新建 `app-ingress.yaml`：

```yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: app-ingress
spec:
  rules:
  - host: demo.local
    http:
      paths:
      - path: /api
        pathType: Prefix
        backend:
          service:
            name: api
            port:
              number: 80
      - path: /
        pathType: Prefix
        backend:
          service:
            name: frontend
            port:
              number: 80
```

这份配置表达得很清楚：

- 当请求命中 `demo.local/api` 时，转给 `api` 服务。
- 当请求命中 `demo.local/` 时，转给 `frontend` 服务。

### 第四步：应用并观察 Ingress 状态

```bash
kubectl apply -f app-ingress.yaml
kubectl get ingress
kubectl describe ingress app-ingress
```

这里重点关注：

- Ingress 是否被控制器识别。
- 地址字段是否被填充。
- 事件里是否有控制器同步规则的记录。

### 第五步：理解它与 Service 的协作关系

很容易忽略的一点是：Ingress 并不是直接把请求转到 Pod，而是**先匹配规则，再转给 Service，再由 Service 找到后端 Pod**。这意味着：

- 如果 Service 不通，Ingress 也不可能通。
- 如果 Pod 没 Ready，Service 没后端，Ingress 也会表现异常。
- Ingress 排障不能只看 Ingress 自己，还要顺着链路继续看 Service 和 Pod。

### 第六步：为什么说它是一套协作机制

因为 Ingress 的真正价值，不是“有个对象”，而是这整条链路：

- 开发者声明七层规则。
- Controller 把规则翻译成代理配置。
- Service 负责后端抽象。
- Pod 负责真正响应业务。

只要这条链路里任意一环断了，入口都不会稳定。

### 这一节应该带走什么

- Ingress 是规则对象，不是自带实现的流量入口。
- Ingress Controller 是规则真正落地的执行者。
- Ingress 更适合做 HTTP/HTTPS 的统一入口治理。
- 排障时必须沿着 `Ingress -> Service -> Pod` 这条链路往下看。

## 4）项目总结：总结该主题对象的优点和缺点，使用场景，注意事项和常见踩坑经验

### 优点

- 它把七层路由规则从 Service 中解耦出来，便于统一治理。
- 它非常适合多服务、多域名、多路径的入口管理。
- 它能与 TLS、认证、限流、重写等入口能力自然结合。

### 缺点

- 对初学者来说，最大的难点在于它不是“单对象自带能力”，必须理解控制器协作。
- 不同 Controller 对注解和高级能力支持存在差异，迁移成本需要考虑。
- 入口治理一旦变复杂，排障链路也会更长。

### 使用场景

- 多个 HTTP/HTTPS 服务共用统一入口。
- 需要基于域名、路径进行七层路由的业务。
- 需要统一证书、访问策略和入口治理的微服务平台。

### 注意事项

- 先确认集群里是否安装并正常运行了 Ingress Controller。
- 不要把 Ingress 当成 Service 的替代品，它建立在 Service 之上。
- 不同环境和 Controller 的行为差异要提前了解，避免盲目照搬注解。

### 常见踩坑经验

- 最常见的坑，是以为声明一个 Ingress 就能自动生效，却忽略了控制器根本没装。
- 第二个坑，是只盯 Ingress 规则，不看 Service 和后端 Pod 状态，排障方向从一开始就偏了。
- 第三个坑，是把 Ingress 理解成“集群对外发布的全部答案”，却忽略了它仍然只是入口治理层的一部分。

这一章真正想建立的，是一种入口系统意识：**从浏览器到业务 Pod，中间不是一跳，而是一条规则、控制器、Service 和后端实例共同协作的流量链路。**
