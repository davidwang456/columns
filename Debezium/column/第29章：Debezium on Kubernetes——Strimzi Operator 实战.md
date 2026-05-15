# 第29章：Debezium on Kubernetes——Strimzi Operator 实战

## 1. 项目背景

"我们的 CDC 管道三个月前从 Docker Compose 迁移到了 Kubernetes，但运维体验不但没有提升，反而更差了——每次升级 Kafka Connect 版本要手动 drain Worker Pod、编辑 Deployment YAML、kubectl apply、等滚动更新完成、再去验证所有 Connector 状态。上周五晚上升级 3.6→3.7 时，因为忘了一个环境变量，15 个 Connector 全部 UNASSIGNED 了 2 个小时才被发现。老板说'你们不是上了 K8s 吗？怎么运维还这么原始？'"

这是很多团队从手动部署转向 K8s 时的"中间态困境"——容器是跑在 K8s 上了，但运维方式还是老一套。Kafka Connect 的 Worker、Connector、Task 三者之间的关系没有映射到 K8s 的原生资源模型（Deployment/StatefulSet/Custom Resource），升级时需要手动操作多个层面。更麻烦的是，Connector 的配置（JSON）以 REST API 调用方式存在，而不是声明式的 YAML——这导致了"K8s 集群中的配置漂移"：Pod 重建后 Connector 配置还在（存在 Kafka 内部 Topic 中），但你是通过 curl 创建的，而不是 kubectl apply，Git 仓库中的 YAML 和实际状态不一致。

**Strimzi Operator** 是 Red Hat 开源的 Kafka on Kubernetes 解决方案，它通过 **Kubernetes CRD（Custom Resource Definition，自定义资源定义）** 将 Kafka 集群、Kafka Connect 集群、KafkaConnector 抽象为 K8s 的一等公民资源。这意味着：

- 创建一个 Kafka Connect 集群 → `kubectl apply -f kafka-connect.yaml`
- 创建一个 Debezium Connector → `kubectl apply -f connector.yaml`
- 滚动升级 Connect 版本 → 修改 YAML 中的 `version: 3.7.0`，Operator 自动滚动
- Git 仓库中的 YAML = 集群中的实际状态 → **GitOps 声明式管理**

### 痛点放大

传统 K8s 部署方式 vs Strimzi Operator 的四重对比：

| 运维操作 | 传统 K8s 部署 | Strimzi Operator |
|---------|-------------|-----------------|
| 创建 Connect 集群 | 手写 Deployment+Service+ConfigMap | 一个 KafkaConnect YAML |
| 创建 Connector | curl POST REST API | kubectl apply KafkaConnector YAML |
| 滚动升级 | kubectl edit deployment + 等待 | 改 version 字段 → 自动滚动 |
| 故障自愈 | 依赖 liveness probe + restartPolicy | K8s 原生 + Operator 状态协调 |
| 插件管理 | 手动挂载 volume 或 rebuild 镜像 | build 字段自动打包到镜像 |
| Secret 管理 | 明文环境变量 | K8s Secret 加密挂载 |
| 配置漂移 | 常见（curl 改的，Git 不知道） | 不可能（YAML 即真相） |

## 2. 项目设计——三人对话

**（周一早上，小胖抱着 MacBook 冲到大师的工位，屏幕上 K9s 的 Pod 列表一片红色）**

**小胖**："大师救命！我上周五升级 Kafka Connect 从 3.6 到 3.7，改完 Deployment 后滚动更新卡住了——新 Pod 一直 CrashLoopBackOff。查了半天日志才发现新版本的 Connect 需要额外配一个 `CONNECT_CONNECTOR_CLIENT_CONFIG_OVERRIDE_POLICY` 环境变量。问题是这个变量在旧版 Deployment YAML 里根本没有，我是手动 curl 创建的 Connector，K8s 里根本看不到 Connector 的配置！"

**大师**："这就是典型的'手动创建 Connector + K8s Deployment'的大坑。你的 Connector 配置存在 Kafka 的 `connect-configs` Topic 中，而不是在 K8s 的 etcd 中。K8s 不感知 Connector 的存在——所以当你改 Deployment 时，K8s 只管 Pod 拉起来，根本不知道 Connector 配置是否兼容新版本。"

**小白**："那 Strimzi Operator 是怎么解决这个问题的？它怎么让 K8s '感知'到 Connector？"

**大师**："通过 CRD——Strimzi 定义了三个自定义资源类型：`Kafka`（代表一个 Kafka 集群）、`KafkaConnect`（代表一个 Connect 集群）、`KafkaConnector`（代表一个 Connector）。当你执行 `kubectl apply -f connector.yaml` 时，Operator 的 Controller 会 watch 到这个资源的创建事件，然后调用 Kafka Connect REST API 去创建对应的 Connector。反之，如果你 `kubectl delete kafkaconnector`，Operator 会自动调用 DELETE API。"

**小胖**："那升级版本呢？Operator 怎么做到零停机的？"

**大师**："Operator 的 Reconciler Loop 不断对比'期望状态'（YAML 中的 spec）和'实际状态'（集群中的情况）。当你改 `version: 3.7.0` 时，Operator 检测到 spec 变化 → 创建新版本 Pod → 等新 Pod 健康检查通过 → 把旧 Pod 从 Service 端点摘除 → 优雅终止旧 Pod → 删除旧 Pod。这个过程逐个 Pod 执行（滚动更新），每个时刻至少保留 `replicas - 1` 个健康 Pod。整个过程 Consumer 端会经历一次短暂的 Rebalance（< 30 秒），数据不会丢。"

**小白**："那 Connector 的 offset 和 status 呢？新 Pod 启动后怎么知道上次读到哪了？"

**大师**："Offset 存储在 Kafka 的 `connect-offsets` Topic 中，与 Worker Pod 的生命周期完全解耦。新 Pod 启动 → 加入 Connect Group → Leader 读取 `connect-configs` Topic 恢复所有 Connector 配置 → 分配 Task 给各 Worker → 各 Worker 从 `connect-offsets` Topic 读取对应 Task 的 offset → 继续消费。K8s Pod 重启和 Kafka Connect Worker 重启在这个机制下是等价的。"

**小胖**（眼睛发亮）："那听起来 Operator 就是 K8s 版的 Ansible——我想要什么状态写在 YAML 里，Operator 负责把集群调整到这个状态。"

**大师**："完全正确！技术映射——Operator = K8s 中的自动驾驶仪。CRD YAML = 你的目的地导航设置。Reconciler Loop = 自动驾驶仪的持续纠偏（偏航了自动打方向盘）。滚动升级 = 高速路上自动变道——后车（新Pod）先加速到与车队同速，前车（旧Pod）收到信号后减速让位，整个过程后排乘客（下游消费者）几乎无感知。"

**小胖**："那我们有 3 个环境——dev、staging、prod——怎么通过 Git 管理这三套配置？如果每个环境都要手动改 YAML 里的 server.id、topic.prefix 也很累吧？"

**大师**："Kustomize 或 Helm 来解决。你把公共部分放在 `base/` 目录，每个环境的差异放在 `overlays/dev/`、`overlays/staging/`、`overlays/prod/` 中。ArgoCD 或 Flux 监听 Git 仓库的变更自动同步。Git 仓库中 connector 目录的每一次 commit → 自动部署到对应 K8s 集群。"

---

## 3. 项目实战

### 环境准备

```bash
# 确认有 K8s 集群和 kubectl 已配置（Minikube/Kind/云 K8s 均可）
kubectl version --short
kubectl get nodes
```

### 步骤1：部署 Strimzi Operator

**目标**：在 K8s 集群中安装 Strimzi Operator，建立 Kafka CRD 基础设施。

```bash
# 创建专用命名空间
kubectl create namespace kafka

# 安装 Strimzi Operator（最新稳定版）
kubectl create -f 'https://strimzi.io/install/latest?namespace=kafka' -n kafka

# 等待 Operator Pod 启动
kubectl get pods -n kafka --watch
# 预期输出：
# NAME                                        READY   STATUS    RESTARTS   AGE
# strimzi-cluster-operator-xxxxxxxxxx-xxxxx   1/1     Running   0          30s

# 验证 CRD 已安装
kubectl get crd | grep strimzi
# 预期输出：
# kafkaconnectors.kafka.strimzi.io
# kafkaconnects.kafka.strimzi.io
# kafkabridges.kafka.strimzi.io
# kafkamirrormaker2s.kafka.strimzi.io
# kafkas.kafka.strimzi.io
# ...
```

### 步骤2：声明并部署 Kafka 集群

**目标**：通过 `Kafka` CRD 声明一个 3 节点的 Kafka 集群（生产环境最小规模）。

```yaml
# kafka-cluster.yaml
apiVersion: kafka.strimzi.io/v1beta2
kind: Kafka
metadata:
  name: debezium-kafka
  namespace: kafka
spec:
  kafka:
    version: 3.6.0
    replicas: 3
    listeners:
      - name: plain
        port: 9092
        type: internal
        tls: false
      - name: tls
        port: 9093
        type: internal
        tls: true
    config:
      offsets.topic.replication.factor: 3
      transaction.state.log.replication.factor: 3
      transaction.state.log.min.isr: 2
      default.replication.factor: 3
      min.insync.replicas: 2
      inter.broker.protocol.version: "3.6"
    storage:
      type: persistent-claim
      size: 100Gi
      deleteClaim: false
  zookeeper:
    replicas: 3
    storage:
      type: persistent-claim
      size: 50Gi
      deleteClaim: false
  entityOperator:
    topicOperator: {}
    userOperator: {}
```

```bash
kubectl apply -f kafka-cluster.yaml -n kafka
kubectl get kafka -n kafka
# 预期：debezium-kafka 状态 READY

# 验证 Kafka Pod 数量和状态
kubectl get pods -n kafka -l strimzi.io/kind=Kafka
# 预期：3 个 Kafka Broker + 3 个 Zookeeper 节点
```

### 步骤3：声明 Kafka Connect 集群并配置 Debezium 插件

**目标**：通过 `KafkaConnect` CRD 声明一个包含 MySQL + PG + MongoDB 三种 Connector 插件的 Connect 集群。

```yaml
# kafka-connect-debezium.yaml
apiVersion: kafka.strimzi.io/v1beta2
kind: KafkaConnect
metadata:
  name: debezium-connect
  namespace: kafka
  annotations:
    strimzi.io/use-connector-resources: "true"
spec:
  version: 3.6.0
  replicas: 3
  bootstrapServers: debezium-kafka-kafka-bootstrap:9092
  image: myregistry.internal/debezium-connect:2.7.1
  config:
    group.id: debezium-connect-cluster
    offset.storage.topic: connect-cluster-offsets
    config.storage.topic: connect-cluster-configs
    status.storage.topic: connect-cluster-statuses
    
    # 三个内部 Topic 的 HA 配置
    config.storage.replication.factor: 3
    offset.storage.replication.factor: 3
    status.storage.replication.factor: 3
    
    # Key/Value 转换器
    key.converter: io.confluent.connect.avro.AvroConverter
    value.converter: io.confluent.connect.avro.AvroConverter
    key.converter.schema.registry.url: http://schema-registry.kafka.svc:8081
    value.converter.schema.registry.url: http://schema-registry.kafka.svc:8081
    
    # 内部转换器
    config.storage.replication.factor: 3
    offset.storage.replication.factor: 3
    status.storage.replication.factor: 3
  
  # ★ 关键：build 字段——告诉 Operator 如何构建包含 Debezium 插件的镜像
  build:
    output:
      type: docker
      image: myregistry.internal/debezium-connect:2.7.1
      pushSecret: registry-credentials
    plugins:
      - name: debezium-mysql-connector
        artifacts:
          - type: tgz
            url: https://repo1.maven.org/maven2/io/debezium/debezium-connector-mysql/2.7.1.Final/debezium-connector-mysql-2.7.1.Final-plugin.tar.gz
            sha512sum: xxxxx  # 可选：校验和
      - name: debezium-postgres-connector
        artifacts:
          - type: tgz
            url: https://repo1.maven.org/maven2/io/debezium/debezium-connector-postgres/2.7.1.Final/debezium-connector-postgres-2.7.1.Final-plugin.tar.gz
      - name: debezium-mongodb-connector
        artifacts:
          - type: tgz
            url: https://repo1.maven.org/maven2/io/debezium/debezium-connector-mongodb/2.7.1.Final/debezium-connector-mongodb-2.7.1.Final-plugin.tar.gz
  
  # 资源限制
  resources:
    requests:
      memory: 2Gi
      cpu: 1000m
    limits:
      memory: 4Gi
      cpu: 2000m
  
  # JVM 配置
  jvmOptions:
    -Xms: 2g
    -Xmx: 4g
    gcLoggingEnabled: false
  
  # 健康检查
  readinessProbe:
    initialDelaySeconds: 30
    timeoutSeconds: 5
  
  livenessProbe:
    initialDelaySeconds: 30
    timeoutSeconds: 5
```

```bash
kubectl apply -f kafka-connect-debezium.yaml -n kafka

# 等待 Connect Pod 就绪
kubectl get kafkaconnect -n kafka
kubectl get pods -n kafka -l strimzi.io/kind=KafkaConnect
# 预期：3 个 Connect Worker Pod 都是 Running
```

### 步骤4：声明式创建 Debezium MySQL Connector

**目标**：通过 `KafkaConnector` CRD 创建一个 MySQL Connector，实现 GitOps 化管理。

```yaml
# orders-mysql-connector.yaml
apiVersion: kafka.strimzi.io/v1beta2
kind: KafkaConnector
metadata:
  name: orders-mysql-connector
  namespace: kafka
  labels:
    strimzi.io/cluster: debezium-connect
    app: ecommerce
    domain: orders
    tenant: tenant-a
    env: production
spec:
  class: io.debezium.connector.mysql.MySqlConnector
  tasksMax: 2
  pause: false
  config:
    # 数据库连接
    database.hostname: mysql-orders.production.svc.cluster.local
    database.port: "3306"
    database.user: "${secrets:debezium/mysql-orders-credentials:username}"
    database.password: "${secrets:debezium/mysql-orders-credentials:password}"
    database.server.id: "184291"
    
    # Topic 和路由配置
    topic.prefix: prod.orders
    database.include.list: inventory
    table.include.list: inventory.orders,inventory.order_items
    
    # 快照策略
    snapshot.mode: initial
    snapshot.locking.mode: minimal
    snapshot.fetch.size: "20000"
    
    # Schema 历史
    schema.history.internal.kafka.bootstrap.servers: debezium-kafka-kafka-bootstrap:9092
    schema.history.internal.kafka.topic: schema-changes.prod.orders
    
    # 事务元数据（开启）
    provide.transaction.metadata: "true"
    
    # 性能参数
    max.batch.size: "8192"
    max.queue.size: "32768"
    poll.interval.ms: "100"
    compression.type: "snappy"
    
    # SMT 链：拍平 + 脱敏
    transforms: "unwrap,maskSensitive"
    transforms.unwrap.type: io.debezium.transforms.ExtractNewRecordState
    transforms.unwrap.delete.handling.mode: rewrite
    transforms.maskSensitive.type: org.apache.kafka.connect.transforms.ReplaceField$Value
    transforms.maskSensitive.exclude: "user_phone,user_id_card,user_bank_account"
    
    # 信号表（支持增量快照）
    signal.enabled.channels: source
    signal.data.collection: inventory.debezium_signal
    
    # 增量快照
    incremental.snapshot.chunk.size: "5000"
```

```bash
# 先创建 Secret 存储数据库密码
kubectl create secret generic debezium-mysql-orders-credentials \
  --from-literal=username=debezium_sync \
  --from-literal=password='ProdSecurePass123!' \
  -n kafka

# 部署 Connector
kubectl apply -f orders-mysql-connector.yaml -n kafka

# 验证 Connector 状态
kubectl get kafkaconnector -n kafka
# 预期输出：
# NAME                      CLUSTER             CONNECTOR CLASS                                  STATE
# orders-mysql-connector    debezium-connect    io.debezium.connector.mysql.MySqlConnector       Ready

# 查看 Connector 详细信息
kubectl describe kafkaconnector orders-mysql-connector -n kafka

# 等价于 curl 查看 REST API 状态（Operator 内部自动调用）
kubectl exec -n kafka deploy/debezium-connect -- curl -s http://localhost:8083/connectors/orders-mysql-connector/status
```

### 步骤5：滚动升级 + 版本回滚验证

**目标**：修改 Connect 版本触发自动滚动升级，展示 GitOps 回滚能力。

```bash
# 场景1：滚动升级 Connect 版本 3.6.0 → 3.7.0
kubectl patch kafkaconnect debezium-connect -n kafka \
  --type='merge' \
  -p '{"spec":{"version":"3.7.0"}}'

# 观察滚动升级过程
kubectl get pods -n kafka -l strimzi.io/kind=KafkaConnect -w
# 观察日志：
# Pod debezium-connect-7d8f9-abcde   Running   0          30s   ← 新 Pod
# Pod debezium-connect-6c7e8-xyzab   Running   0          5m    ← 旧 Pod
# Pod debezium-connect-6c7e8-xyzab   Terminating   0          5m    ← 被优雅终止

# 这期间 Kafka Consumer 会经历一次短暂的 Rebalance（< 30 秒）
# 数据不会丢失，因为 offset 持久化在 connect-offsets Topic 中

# 场景2：如果新版本有问题，Git revert + kubectl apply 即可回滚
git revert HEAD  # 回退 Git 中的版本变更
kubectl apply -f kafka-connect-debezium.yaml -n kafka  # Operator 自动回滚
```

### 步骤6：多环境管理——Kustomize 分层

```bash
# 目录结构
kubernetes/cdc/
├── base/
│   ├── kafka-connect.yaml          # 公共 Connect 配置
│   └── kustomization.yaml
├── overlays/
│   ├── dev/
│   │   ├── kustomization.yaml      # replicas: 1, version: 3.7.0
│   │   └── connectors/
│   │       └── orders-dev.yaml
│   ├── staging/
│   │   ├── kustomization.yaml      # replicas: 2, version: 3.6.0
│   │   └── connectors/
│   └── prod/
│       ├── kustomization.yaml      # replicas: 3, version: 3.6.0
│       └── connectors/
│           ├── orders-prod.yaml
│           ├── payments-prod.yaml
│           └── users-prod.yaml

# overlays/prod/kustomization.yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - ../../base
  - connectors/
patches:
  - patch: |-
      - op: replace
        path: /spec/replicas
        value: 3
    target:
      kind: KafkaConnect
      name: debezium-connect

# 部署到不同环境
kubectl apply -k overlays/dev/ -n kafka-dev
kubectl apply -k overlays/prod/ -n kafka-prod
```

### 步骤7：日常运维——常用 kubectl 命令

```bash
# 查看所有 Connector 状态（K8s 原生方式）
kubectl get kafkaconnector -n kafka

# 暂停某个 Connector
kubectl patch kafkaconnector orders-mysql-connector -n kafka \
  --type='merge' -p '{"spec":{"pause":true}}'

# 恢复
kubectl patch kafkaconnector orders-mysql-connector -n kafka \
  --type='merge' -p '{"spec":{"pause":false}}'

# 修改 Connector 配置（改表过滤条件）
kubectl edit kafkaconnector orders-mysql-connector -n kafka
# 修改 spec.config.table.include.list → Operator 自动调用 PUT /config

# 删除 Connector（会清除 REST API 注册，但不会删 Kafka Topic）
kubectl delete kafkaconnector orders-mysql-connector -n kafka

# 查看 Connect Worker Pod 日志
kubectl logs -n kafka -l strimzi.io/kind=KafkaConnect --tail=100 -f
```

### 可能遇到的坑及解决方法

| 坑 | 现象 | 根因 | 解决方法 |
|----|------|------|---------|
| build 镜像失败 | Connect Pod 不启动，Operator 日志报 ImagePullBackOff | 镜像仓库认证失败 | 创建 `registry-credentials` Secret + 在 spec.build.output.pushSecret 中引用 |
| Connector 状态 NotReady | `kubectl get kafkaconnector` 显示 NotReady | Connector REST API 返回 FAILED | `kubectl describe kafkaconnector` 查看 status.conditions 中的错误信息 |
| 升级后 Connector offset 丢失 | Connector 重新执行全量快照 | offset Topic 的 replication factor < min.insync.replicas | 确保三个内部 Topic 的 RF ≥ 3 |
| Secret 引用不生效 | `${secrets:...}` 被当作字符串字面量 | 引用的 Secret 不存在或 key 不匹配 | `kubectl get secret` 确认 Secret 名和 key 一致 |
| `spec.pause: true` 不生效 | Operator 不处理 pause | Operator 版本 < 0.30 不支持 pause 功能 | 升级 Strimzi 到最新版本 |

---

## 4. 项目总结

### 优点 & 缺点

| 维度 | Strimzi Operator | 手工 K8s Deployment + curl | Docker Compose |
|------|-----------------|---------------------------|----------------|
| 部署复杂度 | ★★★★☆ 一次学习 | ★★☆☆☆ 手写多层级 YAML | ★★★★★ 一条命令 |
| GitOps 兼容 | ★★★★★ 天然支持 | ★★☆☆☆ 需额外集成 | ☆☆☆☆☆ 不支持 |
| 滚动升级 | ★★★★★ 自动+优雅 | ★★☆☆☆ 手动+易出错 | ★☆☆☆☆ 单点 |
| 故障自愈 | ★★★★★ K8s + Operator | ★★★★☆ K8s liveness | ★☆☆☆☆ 需外部监控 |
| 插件管理 | ★★★★★ build 字段 | ★★★☆☆ 手写 Dockerfile | ★★☆☆☆ volumes 挂载 |
| 多环境管理 | ★★★★★ Kustomize | ★★★☆☆ 手动复制 YAML | ★★☆☆☆ 手动复制 |

### 适用场景

1. **生产级 K8s 环境**：需要声明式管理、GitOps、自动滚动升级
2. **多环境多集群**：dev/staging/prod 通过 Kustomize 分层管理
3. **安全合规**：K8s Secret 加密 + RBAC + NetworkPolicy 满足金融级安全要求
4. **大规模 Connector 治理**：50+ Connector，Git 仓库管理所有 YAML
5. **CI/CD 集成**：Git commit → ArgoCD 自动同步 → 自动化测试 → 生产发布

### 不适用场景

1. **非 K8s 环境**：如果团队还在用 Docker Compose/物理机，Strimzi 无用武之地
2. **极简开发环境**：个人学习时 Docker Compose 一条命令更快，不需要 Operator 的复杂度

### 注意事项

- **Strimzi 版本的 Kafka 版本支持范围**：Strimzi 0.39 支持 Kafka 3.6.x，跨大版本升级前查兼容性矩阵
- **build 镜像建议提前构建**：`spec.build` 在 CI 中预先构建，避免 Pod 启动时在线下载 JAR
- **Secret 格式**：`${secrets:<secret-name>:<key>}` 引用方式，与 `${env:...}` 不同，不要混淆

### 常见踩坑经验

1. **"kubectl apply 了 Connector YAML 但 REST API 没收到"**——label `strimzi.io/cluster` 的值必须与 KafkaConnect 资源的 `metadata.name` 完全一致，否则 Operator 不知道该 Connector 属于哪个 Connect 集群。
2. **"滚动升级后新 Pod 一直 Pending"**——检查 `resources.requests` 是否超过了 Node 的可用资源。新 Pod 需要额外的 CPU/Memory 来启动（旧 Pod 还未终止），所以 Node 需要有至少 1 个 Pod 的额外余量。
3. **"GitOps 场景：Git 中改了什么，但 K8s 中没有同步"**——如果使用 ArgoCD，检查 Application 的 `syncPolicy.automated.prune=true` 是否开启（确保 Git 中删除的文件在 K8s 中也删除）。

### 思考题

1. `spec.pause: true`（暂停 Connector）和 `kubectl delete kafkaconnector`（删除 KafkaConnector 资源）有何本质区别？pause 后 Kafka 的 `connect-offsets` Topic 中的 offset 是否保留？delete 后 offset 呢？pause 后 Connector 的 Task 状态是什么？

2. Strimzi 的 `KafkaConnector` CRD 中 `tasksMax: 2` 配置与 Debezium MySQL Connector 的单 Task 设计有无冲突？如果 Debezium MySQL Connector 在源码层面限制了只能有 1 个 Task（因为 binlog 消费是单线程的），`tasksMax: 2` 会发生什么？

**（第28章思考题答案）**

1. 健康心跳机制：在 MySQL 中创建一张 `cdc_heartbeat` 表，CronJob 每分钟 INSERT 一行（含当前时间戳）。独立 Consumer 订阅该心跳 Topic，如果超过 2 分钟未收到新心跳事件，说明 CDC 管道的"端到端"链路（MySQL→Debezium→Kafka→Consumer）中的某一环断开，触发独立于 Prometheus 的告警通道（如直接调用 PagerDuty API）。

2. 自动扩容策略：Prometheus 记录 `debezium_MilliSecondsBehindSource > 60000` 持续 10 分钟 → Alertmanager 发送 webhook 到 KEDA（K8s Event-driven Autoscaling）的 ScaledObject → KEDA 触发 HPA 扩容 Kafka Connect Worker 的 replicas 数量。缩容策略：Lag < 5000ms 持续 30 分钟后，KEDA 逐步缩容回基础数量。

---

> **推广提示**：将 Strimzi YAML 模板纳入团队 Git 仓库 `kubernetes/cdc/` 目录，设置为 ArgoCD 的自动同步 Application。制定团队规范：**任何 Connector 的创建、修改、删除都必须通过 Git PR + Code Review，禁止直接 curl REST API**。这一条规范落地后，CDC 的运维事故率可降低 80% 以上——因为每次变更都有 Git 审计记录和 Review 环节。
