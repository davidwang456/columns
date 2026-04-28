# 第17章：Flink on Kubernetes——云原生部署最佳实践

---

## 1. 项目背景

某互联网公司决定将所有大数据作业从自建机房迁移到云原生Kubernetes平台。Flink作为实时计算的核心引擎，需要适应Kubernetes的资源调度模型。

相比于YARN，K8S提供了更灵活的调度策略、更细粒度的资源控制、以及更好的混合部署能力（Flink作业可以和微服务共享同一集群）。

Flink on K8S有两种主流部署方式：
1. **Native Kubernetes（原生集成）**：Flink直接通过Kubernetes API创建Pod，不依赖任何Operator
2. **Flink Kubernetes Operator（推荐）**：使用Apache Flink官方Operator管理Flink作业的生命周期

Flink Operator提供了声明式作业管理（通过CRD yaml定义作业）、自动升级（滚动更新Savepoint）、Session集群自动扩缩容等高级功能。

---

## 2. 项目设计

> 场景：架构师要求将所有Flink作业迁移到K8S，小胖表示没搞过——"YARN不也能跑吗，为什么要换？"

**大师**：YARN模式有几个K8S天然解决的问题。第一，YARN的Container重启速度比K8S的Pod重启慢得多——K8S的Pod重启可以在500ms内完成。第二，YARN的日志管理不如K8S（需要额外配置日志收集），而K8S天然对接ElasticSearch/Fluentd。第三，资源碎片化——YARN上不同大小的Container碎片越来越难分配，K8S通过namespace和resource quota做得更好。

**技术映射：Flink on K8S = Flink作业作为云原生容器运行。JobManager/TaskManager = Pod，Slot = 容器内的线程资源。Operator = 自动化运维控制器。**

**小白**：那Native模式和Operator模式有什么区别？

**大师**：**Native模式**是Flink自己调用K8S API创建Pod。你执行`flink run -t kubernetes-application`，Flink客户端直接跟K8S APIServer交互。好处是不需要额外安装组件，但运维能力有限。

**Operator模式**在集群中运行一个Flink Operator Pod，它监听自定义资源（FlinkDeployment/FlinkSessionJob）的CRD变化，自动完成创建、升级、扩缩容、Savepoint等操作。

**技术映射：Operator = 扩展K8S的声明式API。你把"想要的最终状态"写入YAML，Operator自动"调和"到目标状态。**

**小胖**：那跟YARN比资源隔离呢？多个Flink作业在同一个K8S集群上跑，会不会互相抢资源？

**大师**：K8S用**namespace + ResourceQuota + LimitRange**做资源隔离。每个Flink作业（或每个团队）分配独立的namespace，设置CPU和内存上限。更细粒度可以用**Pod Priority Class**——核心作业（风控）用高优先级，非核心作业（日志分析）用低优先级，集群资源紧张时优先抢占低优先级Pod。

---

## 3. 项目实战

### 环境准备

| 组件 | 版本 | 说明 |
|------|------|------|
| Kubernetes | 1.27+ | 容器编排平台 |
| kubectl | 1.27+ | K8S CLI工具 |
| Helm | 3.12+ | Operator安装工具 |
| Flink Operator | 1.8+ | Flink CRD控制器 |
| Docker Registry | - | 存储Flink作业镜像 |

### 分步实现

#### 步骤1：构建Flink作业镜像

**目标**：将Flink作业与依赖一起打包为Docker镜像。

```dockerfile
# Dockerfile
FROM flink:1.18-scala_2.12

# 将编译好的fat jar放入Flink的lib目录
COPY jobs/flink-practitioner.jar /opt/flink/usrlib/flink-practitioner.jar

# 可选：添加额外的connector依赖
# COPY connectors/flink-connector-kafka-3.0.2.jar /opt/flink/lib/
# COPY connectors/flink-connector-jdbc-3.1.2.jar /opt/flink/lib/
# COPY connectors/mysql-connector-java-8.0.33.jar /opt/flink/lib/
```

```bash
# 构建并推送镜像
docker build -t myregistry.com/flink-jobs:1.0 .
docker push myregistry.com/flink-jobs:1.0
```

#### 步骤2：安装Flink Kubernetes Operator

**目标**：使用Helm安装Flink Operator。

```bash
# 添加Helm仓库
helm repo add flink-operator https://apache.github.io/flink-kubernetes-operator
helm repo update

# 安装Operator（namespace: flink-operator-system）
helm install flink-kubernetes-operator flink-operator/flink-kubernetes-operator \
  --namespace flink-operator-system \
  --create-namespace \
  --set image.repository=apache/flink-kubernetes-operator

# 验证安装
kubectl get pods -n flink-operator-system
# NAME                                                  READY   STATUS
# flink-kubernetes-operator-<hash>                     1/1     Running
```

#### 步骤3：声明式部署Application集群

**目标**：通过CRD YAML部署Flink作业。

```yaml
# flink-job.yaml
apiVersion: flink.apache.org/v1beta1
kind: FlinkDeployment
metadata:
  name: order-dashboard
  namespace: flink-jobs
spec:
  image: myregistry.com/flink-jobs:1.0
  flinkVersion: v1_18
  flinkConfiguration:
    taskmanager.numberOfTaskSlots: "4"
    state.backend: rocksdb
    state.checkpoints.dir: s3://my-bucket/flink-checkpoints
    state.backend.incremental: "true"
    execution.checkpointing.interval: "30s"
    high-availability: org.apache.flink.kubernetes.highavailability.KubernetesHighAvailabilityServiceFactory
    high-availability.storageDir: s3://my-bucket/flink-ha
    
    # K8S特有配置
    kubernetes.operator.metrics.reporter.slf4j.factory.class: org.apache.flink.metrics.slf4j.Slf4jReporterFactory
    kubernetes.operator.metrics.reporter.slf4j.interval: 60 SECOND

  serviceAccount: flink
  jobManager:
    resource:
      memory: "4096m"
      cpu: 2
    replicas: 2  # HA模式至少2个JM副本
  taskManager:
    resource:
      memory: "8192m"
      cpu: 4
    replicas: 2

  podTemplate:
    spec:
      containers:
        - name: flink-main-container
          env:
            - name: TZ
              value: Asia/Shanghai
          volumeMounts:
            - mountPath: /opt/flink/conf/flink-conf.yaml
              subPath: flink-conf.yaml
              name: flink-config
      volumes:
        - name: flink-config
          configMap:
            name: flink-custom-config

  job:
    jarURI: local:///opt/flink/usrlib/flink-practitioner.jar
    entryClass: com.flink.column.chapter15.RealtimeDashboardJob
    args: []
    parallelism: 8
    upgradeMode: savepoint
    savepointTriggerNonce: 0  # 递增此值触发新的Savepoint
```

```bash
# 部署
kubectl apply -f flink-job.yaml

# 查看Flink作业状态
kubectl get flinkdeployment -n flink-jobs
# NAME              JOB STATUS   LIFECYCLE STATE
# order-dashboard   RUNNING      STABLE

# 查看Pod
kubectl get pods -n flink-jobs
# NAME                                READY   STATUS
# order-dashboard-<jm-hash>-1         1/1     Running   (JobManager)
# order-dashboard-<jm-hash>-2         1/1     Running   (Standby JM)
# order-dashboard-<tm-hash>-1-1       1/1     Running   (TaskManager)
# order-dashboard-<tm-hash>-1-2       1/1     Running   (TaskManager)
```

#### 步骤4：Native K8S Application模式（免Operator）

**目标**：不安装Operator，直接用Flink CLI提交到K8S。

```bash
# 前提：配置kubectl的context正确指向目标集群
# 并且Flink安装目录的conf/flink-conf.yaml配置了kubernetes相关参数

# 配置K8S namespace和service account
export KUBERNETES_NAMESPACE=flink-jobs
export KUBERNETES_SERVICE_ACCOUNT=flink

# 提交Application作业
./bin/flink run-application -t kubernetes-application \
  -Dkubernetes.cluster-id=order-dashboard-native \
  -Dkubernetes.container.image=myregistry.com/flink-jobs:1.0 \
  -Dkubernetes.namespace=flink-jobs \
  -Dkubernetes.jobmanager.replicas=2 \
  -Dkubernetes.taskmanager.cpu=4 \
  -Dtaskmanager.memory.process.size=8192m \
  -Dtaskmanager.numberOfTaskSlots=4 \
  -Dstate.backend=rocksdb \
  -Dstate.checkpoints.dir=s3://my-bucket/flink-checkpoints \
  -Dexecution.checkpointing.interval=30s \
  -p 8 \
  local:///opt/flink/usrlib/flink-practitioner.jar
```

#### 步骤5：作业升级与Savepoint管理

**目标**：使用Operator的滚动升级策略，零停机更新作业逻辑。

```bash
# 1. 构建新版本镜像
docker build -t myregistry.com/flink-jobs:2.0 .
docker push myregistry.com/flink-jobs:2.0

# 2. 更新YAML中的image版本并递增savepointTriggerNonce
# image: myregistry.com/flink-jobs:2.0
# savepointTriggerNonce: 0 → 1（递增触发Savepoint）

# 3. 应用变更
kubectl apply -f flink-job.yaml

# 4. Operator自动执行滚动升级：
#    a) 触发当前作业的Savepoint
#    b) 停止旧作业
#    c) 使用新镜像启动新作业
#    d) 从Savepoint恢复状态
#    e) 确认新作业正常运行后，停止旧Pod

# 5. 查看升级状态
kubectl describe flinkdeployment order-dashboard -n flink-jobs
```

#### 步骤6：FlinkSessionJob——Session集群模式

**目标**：在Operator模式下使用Session集群，多个作业共享资源。

```yaml
# flink-session-cluster.yaml
apiVersion: flink.apache.org/v1beta1
kind: FlinkDeployment
metadata:
  name: flink-session-cluster
spec:
  image: flink:1.18-scala_2.12
  flinkVersion: v1_18
  serviceAccount: flink
  jobManager:
    resource:
      memory: "2048m"
      cpu: 1
  taskManager:
    resource:
      memory: "4096m"
      cpu: 2
---
# flink-session-job.yaml
apiVersion: flink.apache.org/v1beta1
kind: FlinkSessionJob
metadata:
  name: session-order-dashboard
spec:
  deploymentName: flink-session-cluster
  job:
    jarURI: local:///opt/flink/usrlib/flink-practitioner.jar
    entryClass: com.flink.column.chapter15.RealtimeDashboardJob
    parallelism: 4
    upgradeMode: savepoint
```

### 可能遇到的坑

1. **Pod无法拉取镜像：ImagePullBackOff**
   - 根因：私有镜像仓库未配置Secret
   - 解决：`kubectl create secret docker-registry regcred --docker-server=... --docker-username=... --docker-password=...`，并添加到ServiceAccount

2. **TaskManager Pod因OOM被Kill**
   - 根因：K8S的Pod内存limit设置小于Flink任务实际使用
   - 解方：设置`taskmanager.memory.process.size` < Pod的`resources.limits.memory`，保留20%的余量

3. **Checkpoint写入S3速度慢导致作业超时失败**
   - 根因：S3 API的写入延迟比HDFS高
   - 解方：使用S3的兼容存储（如MinIO），或增加`state.backend.local-recovery`，或使用`state.backend.rocksdb.incremental`减少每次Checkpoint传输量

---

## 4. 项目总结

### YARN vs K8S 部署对比

| 维度 | YARN | K8S |
|------|------|-----|
| 资源管理 | YARN ResourceManager | K8S Scheduler |
| 隔离性 | Container（CGroup） | Pod（CGroup + Namespace） |
| 重启速度 | 秒级 | 毫秒~秒级 |
| 日志管理 | YARN Logs + 额外配置 | 原生 + EFK |
| 声明式运维 | 不支持 | Operator CRD |
| 社区趋势 | 稳定存量 | 增长迅速 |

### Operator模式下关键CRD

| CRD | 说明 | 示例用途 |
|-----|------|---------|
| FlinkDeployment | 完整的Flink集群（含作业） | 部署生产作业 |
| FlinkSessionJob | 提交到Session集群的作业 | Session共享模式 |
| FlinkStateSnapshot | 手动触发Savepoint | 作业迁移前备份 |

### 注意事项
- K8S环境下的Flink HA依赖Kubernetes API（非ZooKeeper）。配置：`high-availability: org.apache.flink.kubernetes.highavailability.KubernetesHighAvailabilityServiceFactory`
- Flink on K8S不支持Session模式的Per-Job资源隔离——如需隔离请使用FlinkDeployment独立部署
- 镜像中的Flink版本必须与Operator的flinkVersion字段匹配（如v1_18对应Flink 1.18.x）

### 常见踩坑经验

**案例1：Operator部署后FlinkDeployment一直处于"Pending"状态**
- 根因：Operator没有RBAC权限创建Pod。需要绑定ClusterRole
- 解方：创建ServiceAccount并绑定：`kubectl create clusterrolebinding flink-admin --clusterrole=cluster-admin --serviceaccount=flink-jobs:flink`

**案例2：滚动升级时作业卡在"State upgrade mode is savepoint but no savepoint found"**
- 根因：首次部署时没有Savepoint，但upgradeMode=savepoint要求从Savepoint恢复
- 解方：首次部署用upgradeMode=stateless或last-state，后续升级才用savepoint

**案例3：K8S上TaskManager Pod被驱逐（Evicted）但Flink没有自动恢复**
- 根因：K8S节点压力大时驱逐低优先级Pod，Flink的JM检测到TM丢失但新的TM Pod在PENDING状态
- 解方：设置Pod的priorityClassName为高优先级，或配置Node Affinity避开有问题的节点

### 优点 & 缺点

| | Flink on K8S（Operator模式） | Flink on YARN |
|------|-----------|-----------|
| **优点1** | Pod重启毫秒~秒级，比YARN Container快 | Container启动5-30秒，故障恢复慢 |
| **优点2** | Operator声明式CRD管理，滚动升级自动Savepoint | 升级需手动执行Savepoint + 重新提交 |
| **优点3** | 支持Namespace/ResourceQuota/PriorityClass精细化隔离 | YARN队列资源管理较粗粒度 |
| **优点4** | 日志原生对接EFK，监控体系完善 | 日志需额外配置Log4j/Filebeat |
| **缺点1** | 学习成本高——需掌握K8S、Helm、Operator概念 | YARN运维人员上手快，文档成熟 |
| **缺点2** | 镜像管理复杂——每次代码变更需构建推送新镜像 | 直接提交jar包，无需镜像构建 |

### 适用场景

**典型场景**：
1. 已上云/容器化改造的互联网公司——Flink作业作为云原生服务运行
2. 需要自动化滚动升级的团队——Operator自动Savepoint+回滚
3. 多团队共享集群——K8S Namespace实现租户隔离
4. 微服务+Flink混合部署——Flink与业务服务共用K8S集群

**不适用场景**：
1. 传统Hadoop生态为主的企业——已有YARN运维体系，迁移K8S成本高
2. 小规模开发测试——Standalone/YARN Session模式简单，无需K8S

### 思考题

1. Operator模式中的savepointTriggerNonce字段的作用是什么？如果我想回滚到上一个版本（代码回退但保留状态），应该怎么做？（提示：与FlinkDeployment的.spec.job.savepointTriggerNonce和.spec.job.initialSavepointPath有关）

2. 在K8S上运行Flink作业时，TaskManager Pod的"优雅关闭"（Graceful Shutdown）如何保证数据不丢失？K8S的Pod Termination Grace Period与Flink的Checkpoint之间应该如何配合配置？

---

> **完整代码**：本章完整代码请参考附录或访问 https://github.com/flink-column/flink-practitioner  
> **思考题答案**：见附录文件 `appendix-answers.md`
