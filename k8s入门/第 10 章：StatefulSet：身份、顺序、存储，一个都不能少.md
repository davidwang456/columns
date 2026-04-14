# 第 10 章：StatefulSet：身份、顺序、存储，一个都不能少

> 对应学习计划第 1 周 StatefulSet 和有状态应用实验。

如果说 Deployment 解决的是“可替换实例如何批量管理”，那么 StatefulSet 解决的就是另一类完全不同的问题：**有些实例不能被随便替换，它们需要稳定名字、稳定存储和有序生命周期。** 数据库、队列、注册中心、主从集群节点，很多时候都属于这一类。也正因为如此，StatefulSet 是 Kubernetes 学习路径里第一个真正提醒你“不是所有应用都能按无状态方式处理”的对象。

## 1）项目背景

为什么有状态应用不能简单照搬 Deployment 的思路？因为很多服务真正依赖的，不只是“有几个副本”，而是“每个副本是谁、它对应哪块数据、它在集群拓扑里处于什么顺序”。

举几个典型场景：

- MySQL 主从节点，实例身份不同，恢复策略不同。
- Kafka、ZooKeeper、etcd 这类集群节点，需要稳定网络标识。
- 带持久卷的数据库实例，Pod 重建后仍要挂回原来的存储。
- 某些组件要求按顺序启动、按顺序停止，不能乱序并发替换。

Deployment 的设计目标是无状态世界：实例可替换、名字不重要、卷不强调稳定绑定、更新主要追求平滑和批量。而 StatefulSet 的设计目标正好相反，它更关心：

- 实例是否拥有稳定的有序名称，例如 `mysql-0`、`mysql-1`。
- 每个实例是否绑定自己独立的 PVC。
- 扩容和缩容时是否保持可预期顺序。
- 服务发现时能否通过稳定 DNS 名称找到特定节点。

因此，StatefulSet 的关键价值不在于“也能跑多个副本”，而在于它把**身份、顺序和存储**正式交给平台管理。这对有状态工作负载来说非常重要，因为很多时候，业务真正依赖的不是“数量”，而是“身份连续性”。

你也需要理解一个重要事实：StatefulSet 不是让有状态应用“自动变简单”，它只是提供了一个更合适的平台抽象。数据库的主从、选主、复制、恢复、备份等复杂性并不会因为用了 StatefulSet 就消失。它只是帮助你把基础的实例编排边界表达清楚。

## 2）项目设计：通过大师和小白的对话引出该主题

**小白**：大师，前面 Deployment 看起来已经很强了，为什么数据库还老说不能直接用 Deployment？

**大师**：因为 Deployment 擅长的是“谁坏了都可以换一个”，而数据库这类服务很多时候不是这样。它们更关心“这个实例是谁”“这块数据是不是它的”“启动顺序对不对”。

**小白**：可我给数据库 Pod 也配个 PVC，不就行了吗？

**大师**：只配 PVC 还不够。你还需要稳定身份、稳定 DNS、按顺序扩缩容，甚至要确保一个实例重建后还是回到它自己的那份存储上。

**小白**：所以 StatefulSet 解决的是“实例身份持续存在”的问题？

**大师**：对。你可以把它理解成“有编号、讲顺序、带专属存储”的工作负载控制器。

**小白**：它和 Deployment 最明显的外在差别是什么？

**大师**：最直观的就是 Pod 名称。Deployment 下的 Pod 名字通常带随机后缀；StatefulSet 下的 Pod 会有稳定序号，比如 `mysql-0`、`mysql-1`。

**小白**：那这个序号除了好看，还有什么实际作用？

**大师**：它不仅影响名字，还影响 DNS、卷绑定、集群角色分工和恢复路径。对很多有状态系统来说，这就是实例身份的一部分。

**小白**：那是不是所有带存储的应用都应该用 StatefulSet？

**大师**：也不是。关键看你要不要稳定身份和有序行为。只是“挂个持久卷”的单实例服务，不一定非要 StatefulSet；但只要实例之间有角色、编号和稳定关系，通常就该认真考虑它。

## 3）项目实战：通过主代码片段使用该主题的对象

这一节我们以一个最小 MySQL StatefulSet 为例，观察三个关键现象：Pod 名字稳定、PVC 独立生成、扩缩容有顺序。

### 第一步：准备 Headless Service

StatefulSet 通常会配合一个 Headless Service 使用，用来提供稳定 DNS 解析。

新建 `mysql-headless.yaml`：

```yaml
apiVersion: v1
kind: Service
metadata:
  name: mysql
spec:
  clusterIP: None
  selector:
    app: mysql
  ports:
  - port: 3306
    targetPort: 3306
```

这里最关键的是 `clusterIP: None`，它表示这是一个无头服务，不提供统一虚拟 IP，而是让客户端能直接解析到每个 Pod 的稳定网络身份。

### 第二步：编写最小 StatefulSet

新建 `mysql-statefulset.yaml`：

```yaml
apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: mysql
spec:
  serviceName: mysql
  replicas: 2
  selector:
    matchLabels:
      app: mysql
  template:
    metadata:
      labels:
        app: mysql
    spec:
      containers:
      - name: mysql
        image: mysql:8.0
        env:
        - name: MYSQL_ROOT_PASSWORD
          value: example123
        ports:
        - containerPort: 3306
        volumeMounts:
        - name: data
          mountPath: /var/lib/mysql
  volumeClaimTemplates:
  - metadata:
      name: data
    spec:
      accessModes: ["ReadWriteOnce"]
      resources:
        requests:
          storage: 5Gi
```

### 第三步：应用资源并观察稳定身份

```bash
kubectl apply -f mysql-headless.yaml
kubectl apply -f mysql-statefulset.yaml
kubectl get pods -l app=mysql -w
kubectl get pvc
```

你会看到 Pod 名称通常是：

- `mysql-0`
- `mysql-1`

同时，每个副本还会生成自己的 PVC，例如：

- `data-mysql-0`
- `data-mysql-1`

这一步是 StatefulSet 最核心的直观体验：**副本不仅有数量，还有身份；不仅有身份，还有与身份绑定的数据。**

### 第四步：验证有序扩缩容

```bash
kubectl scale statefulset mysql --replicas=3
kubectl get pods -l app=mysql -w
```

扩容时，你会看到新的 `mysql-2` 在前面的实例就绪后再创建。缩容时，也通常会从序号较大的实例开始删除。对很多有状态系统来说，这种顺序性非常重要，因为它避免了无序扰动带来的风险。

### 第五步：理解 StatefulSet 不替你解决什么

虽然 StatefulSet 提供了稳定身份和卷绑定，但它并不会自动帮你完成：

- MySQL 主从复制配置。
- 节点故障后的数据一致性修复。
- 数据备份与恢复策略。
- 高可用切换逻辑。

也就是说，它提供的是“合适的编排骨架”，不是“数据库高可用的全部答案”。这点一定要分清。

### 这一节应该带走什么

- StatefulSet 适用于需要稳定身份和存储绑定的工作负载。
- Headless Service 是稳定 DNS 发现的重要配套。
- `volumeClaimTemplates` 让每个副本自动拥有独立卷。
- StatefulSet 管编排边界，不代替数据库中间件自身的复制与一致性机制。

## 4）项目总结：总结该主题对象的优点和缺点，使用场景，注意事项和常见踩坑经验

### 优点

- 它为有状态应用提供了稳定身份、稳定网络和稳定卷绑定。
- 它支持有序扩缩容和有序部署，适合对顺序敏感的系统。
- 它让数据库、注册中心、消息队列等工作负载在 Kubernetes 上有了更合适的运行抽象。

### 缺点

- 学习和运维复杂度都明显高于 Deployment。
- 它只解决编排层问题，不自动解决数据一致性和业务高可用。
- 一旦底层存储和网络设计不当，StatefulSet 会把复杂性暴露得更明显。

### 使用场景

- 数据库、注册中心、消息队列、分布式协调组件。
- 需要稳定 DNS 名称和实例编号的集群服务。
- 需要为每个副本绑定独立持久卷的场景。

### 注意事项

- 使用 StatefulSet 时，通常要同时设计好 Headless Service 和持久卷策略。
- 不要因为“带存储”三个字就盲目使用 StatefulSet，要先确认是否真的需要稳定身份。
- 数据备份、恢复、复制、故障切换仍需要额外方案，不会自动拥有。

### 常见踩坑经验

- 最常见的坑，是为了省事把数据库当成普通 Deployment 部署，结果升级和恢复都很混乱。
- 第二个坑，是只创建 StatefulSet，不配 Headless Service，导致稳定发现能力没有真正建立起来。
- 第三个坑，是误以为用了 StatefulSet 就等于“数据库高可用已经解决”，忽视了复制、备份和恢复设计。

这一章真正要建立的意识是：**当实例的“身份”本身成为业务语义的一部分时，就不能再用无状态心智处理它。** 这正是 StatefulSet 存在的根本原因。
