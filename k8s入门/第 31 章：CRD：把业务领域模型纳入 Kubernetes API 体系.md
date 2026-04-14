# 第 31 章：CRD：把业务领域模型纳入 Kubernetes API 体系

> 对应学习计划第 4 周 CRD 与自定义资源。

学到 Kubernetes 的这个阶段，你会开始遇到一个非常关键的问题：内置对象已经很多了，为什么还不够？答案很简单，因为平台最终总会走到“业务语义”这一步。Pod、Deployment、Service 当然重要，但它们更多描述的是基础设施与交付模型；一旦你想表达“数据库集群”“缓存实例”“备份任务”“灰度发布单”“租户配额策略”这类领域对象，继续把语义塞进注解、ConfigMap 或一堆脚本里，系统很快就会变得难以维护。CRD 存在的意义，就是让你把自己的领域模型正式纳入 Kubernetes API 体系。

## 1）项目背景

为什么平台会需要自定义资源？因为 Kubernetes 的强大之处从来不只是它自带了多少对象，而在于它允许你把“平台要管理的东西”继续对象化。现实里，团队往往会有越来越多不属于内置资源的管理需求，比如：

- 一套内部数据库实例交付标准。
- 某种中间件集群的生命周期管理。
- 平台级别的租户、配额、网络策略模板。
- 自定义发布流程、备份策略、合规策略对象。

如果没有 CRD，这些需求通常会退化成几种低水平实现：

- 把语义塞进注解和标签，靠约定解释。
- 用大块 ConfigMap 存非结构化配置。
- 在平台脚本或后台数据库里维护另一套“隐藏状态”。
- 让控制器自己记忆规则，却不把它暴露成一等 API 对象。

这些做法的问题非常一致：**语义不显式、结构不统一、验证不严格、协作不透明。**

CRD（CustomResourceDefinition）给出的解决方案是：把你的领域对象变成 Kubernetes 认识的一类正式 API 资源。这样你就能得到很多原生收益：

- 对象有自己的 kind 和 API group。
- 能被 `kubectl get/apply/describe` 操作。
- 能定义 schema 做字段校验。
- 能和控制器、RBAC、审计、事件体系无缝衔接。

这也是为什么很多 Operator 和平台系统都建立在 CRD 之上。因为一旦你能把业务对象声明成一等资源，平台就不再只是“管理容器”，而是开始真正“管理系统”。

## 2）项目设计：通过大师和小白的对话引出该主题

**小白**：大师，我现在已经有 Deployment、Service、ConfigMap 这些对象了，为什么还会需要 CRD？

**大师**：因为这些对象描述的是 Kubernetes 原生关心的资源，但平台最终会遇到自己的领域模型。比如你们内部可能有“数据库实例”“租户套餐”“发布策略”这种东西，内置对象未必能直接表达。

**小白**：那我不能先用注解或 ConfigMap 凑合吗？

**大师**：短期可以，长期几乎一定会混乱。因为那些语义没有变成正式对象，平台就没法围绕它建立清晰的校验、权限和控制逻辑。

**小白**：所以 CRD 的核心价值，不是多造一种 YAML，而是让业务语义正式进入 API？

**大师**：完全正确。它的本质是“把你的世界接进 Kubernetes 的对象世界”。

**小白**：那我定义了 CRD 之后，它会自动帮我实现业务逻辑吗？

**大师**：不会。CRD 负责定义对象和结构，控制器负责响应对象变化并执行业务逻辑。对象化和自动化是两步，不是一回事。

**小白**：原来 CRD 是平台扩展的入口，不是平台能力本身的全部。

**大师**：这句话很重要。学 CRD，必须同时记住“表达能力”和“执行能力”是分离的。

## 3）项目实战：通过主代码片段使用该主题的对象

这一节我们用一个最小的 `BookStore` 资源举例，体验“把领域模型变成 Kubernetes API 对象”这件事。

### 第一步：定义一个最小 CRD

新建 `bookstore-crd.yaml`：

```yaml
apiVersion: apiextensions.k8s.io/v1
kind: CustomResourceDefinition
metadata:
  name: bookstores.demo.io
spec:
  group: demo.io
  names:
    plural: bookstores
    singular: bookstore
    kind: BookStore
    shortNames:
    - bs
  scope: Namespaced
  versions:
  - name: v1alpha1
    served: true
    storage: true
    schema:
      openAPIV3Schema:
        type: object
        properties:
          spec:
            type: object
            properties:
              replicas:
                type: integer
              image:
                type: string
```

这里你应该先抓住几个关键点：

- `group + version + kind` 决定这类对象在 API 里的身份。
- `scope` 决定它是命名空间级还是集群级。
- `schema` 让自定义字段开始有结构、有校验，而不是随便写。

### 第二步：让 API Server 接受这类新对象

```bash
kubectl apply -f bookstore-crd.yaml
kubectl get crd bookstores.demo.io
kubectl api-resources | grep bookstore
```

做完这一步后，Kubernetes 就不再把 `BookStore` 当成陌生东西了。也就是说，**你的领域对象已经从“文档约定”升级成了“集群承认的资源类型”。**

### 第三步：创建一个自定义资源实例

```yaml
apiVersion: demo.io/v1alpha1
kind: BookStore
metadata:
  name: sample-store
spec:
  replicas: 2
  image: nginx:1.27
```

应用后你可以：

```bash
kubectl apply -f sample-bookstore.yaml
kubectl get bookstores
kubectl describe bookstore sample-store
```

这一刻非常关键，因为它是很多人第一次真正感受到 CRD 价值的时刻：你已经不只是在管理 Pod 和 Service，而是在管理自己的业务对象。

### 第四步：理解为什么 schema 很重要

如果你没有 schema，CRD 很容易退化成“长得像对象的无结构 JSON 容器”。而有了 schema 后：

- 字段格式更清晰。
- 错误值可以更早被拒绝。
- 文档和对象更容易对齐。

这会极大提升 CRD 长期可维护性。初学者最容易踩的坑之一，就是一上来把 schema 设计得要么极度粗糙，要么过度复杂。好的实践通常是：**先建最小可用结构，再逐步演进。**

### 第五步：理解 CRD 和控制器的边界

学到这里一定要牢记：

- CRD 负责“对象长什么样”。
- 控制器负责“对象变化后要做什么”。

如果只有 CRD 没有控制器，你只是多了一类可读写资源；如果两者结合，你才真正拥有了“声明一个对象，平台自动帮你维护它”的能力。

### 这一节应该带走什么

- CRD 让业务领域模型进入 Kubernetes API 体系。
- 它的价值在于对象化、结构化、可治理，而不只是“多一种 YAML”。
- schema 是 CRD 长期可维护性的关键。
- CRD 和控制器是一对常见组合，但职责完全不同。

## 4）项目总结：总结该主题对象的优点和缺点，使用场景，注意事项和常见踩坑经验

### 优点

- 它让平台能管理的不再只是基础设施对象，而是正式扩展到业务领域对象。
- 它让自定义平台能力获得了原生 API、权限、审计和工具链支持。
- 它是 Operator、平台控制器和内部平台抽象的重要起点。

### 缺点

- 学习曲线明显上升，因为你开始从“使用平台”进入“扩展平台”。
- 一旦 schema 设计混乱，后续版本演进会变得非常痛苦。
- 如果没有控制器配合，很多人会误以为 CRD 本身能自动实现业务逻辑。

### 使用场景

- 平台需要定义自己的资源模型时。
- 内部中间件、数据库、发布策略、租户策略等对象管理。
- Operator 和自定义平台能力建设。

### 注意事项

- 先定义最小可用 schema，不要一上来过度设计。
- 设计 CRD 时要考虑未来版本演进，而不是只看眼前字段。
- 要始终区分“定义对象”和“执行业务逻辑”这两层。

### 常见踩坑经验

- 最常见的坑，是内置对象不够用时继续把语义塞进注解、脚本或 ConfigMap，最终平台越来越乱。
- 第二个坑，是一上来就设计过度复杂的 CRD schema，后续难以维护和演进。
- 第三个坑，是以为 CRD 一创建平台就会自动帮你管理对象，忽略了控制器才是真正执行业务逻辑的部分。

这一章真正想帮你建立的，是平台扩展视角：**当内置对象不够表达你的系统时，应该扩展 API，而不是堆积隐式约定。**
