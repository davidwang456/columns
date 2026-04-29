# 第29章 生产级部署：Kubernetes与YARN

## 1 项目背景

### 业务场景：从本地跑通到生产高可用

第3章我们用Docker Compose搭建了开发环境，但生产环境的要求完全不同：
1. **高可用**：JobManager节点宕机后自动切换
2. **弹性伸缩**：根据数据量自动扩缩TaskManager
3. **资源隔离**：不同CDC作业之间互不影响
4. **版本管理**：CDC作业的升级、回滚可重复

Kubernetes和YARN是生产环境最主流的两种资源管理平台。Flink CDC的`flink-cdc.sh`脚本原生支持这两种模式的部署。

### Flink部署模式对比

```
Session Mode (共享集群):
  JM ── TM(Slot1:作业A, Slot2:作业B)
  优点: 资源池化，启动快
  缺点: 作业间资源竞争，隔离性差

Application Mode (每作业独享集群):
  作业A: JM_A ── TM_A
  作业B: JM_B ── TM_B
  优点: 资源隔离，JM故障不影响其他作业
  缺点: 启动慢，资源开销大

Per-Job Mode (已废弃，Flink 1.15+) :
  介于Session和Application之间
```

---

## 2 项目设计 · 三人交锋对话

**小胖**（挠头）：Kubernetes模式部署Flink CDC，是不是先把Flink集群搭在K8s上，然后用`flink-cdc.sh --target kubernetes-session`提交？

**大师**：K8s有两种提交模式：

**模式1：Session集群模式（`kubernetes-session`）**
```
1. 创建Flink Session集群（JM + TM Pool）在K8s上
2. 使用`flink-cdc.sh --target kubernetes-session`提交作业
3. 多个作业共享TM Pool
```

**模式2：Application模式（`kubernetes-application`）——推荐**
```
1. 直接使用`flink-cdc.sh --target kubernetes-application`提交
2. 框架自动为每个CDC作业创建专属JM + TM
3. 作业完成后自动销毁集群
```

**生产环境推荐Application模式**，因为：
- 资源隔离（一个OOM不会影响其他CDC作业）
- 版本独立（不同作业可以使用不同Flink版本）
- 日志隔离（每个作业有自己的Pod）

**小白**：那K8s Application模式下的Checkpoint和Savepoint怎么管理？Pod销毁后State丢了吗？

**大师**：这正是K8s部署的核心配置——**持久化State Backend**。在Application模式下：
1. Checkpoint保存到S3/HDFS（持久存储），不依赖Pod本地磁盘
2. 作业升级时，新Pod从S3加载最新的Checkpoint继续处理
3. Pod重启或迁移后，状态数据不会丢失

```yaml
# K8s Application模式的状态配置
kubernetes:
  state.backend: filesystem
  state.checkpoints.dir: s3://my-bucket/flink-cdc-checkpoints
  state.savepoints.dir: s3://my-bucket/flink-cdc-savepoints
  high-availability: org.apache.flink.kubernetes.highavailability.KubernetesHaServicesFactory
```

**技术映射**：K8s Application模式就像"租车出行"——每次出行（每启动作业）都租一辆新车（新Pod），车（作业）用完就还。但你的导航设置和行驶记录（Checkpoint）保存在你的手机（S3）里，下次租车直接加载设置。

---

## 3 项目实战

### 分步实现

#### 步骤1：Kubernetes环境准备

```bash
# 1. 确保已有K8s集群
kubectl get nodes

# 2. 创建命名空间
kubectl create namespace flink-cdc

# 3. 创建配置映射（flink-conf.yaml）
kubectl create configmap flink-config \
  --from-file=conf/flink-conf.yaml \
  -n flink-cdc

# 4. 创建S3密钥（用于Checkpoint存储）
kubectl create secret generic s3-credentials \
  --from-literal=aws.access-key-id=YOUR_KEY \
  --from-literal=aws.secret-access-key=YOUR_SECRET \
  -n flink-cdc
```

#### 步骤2：使用flink-cdc.sh提交到K8s Application模式

```bash
# 准备JAR包
# 确保flink-cdc-pipeline-connector-mysql-3.0.0.jar等JAR在lib目录

# 提交Pipeline到K8s Application模式
flink-cdc.sh pipeline-prod.yaml \
  --target kubernetes-application \
  --flink-home /opt/flink \
  --kubernetes.container.image flink:1.20.3 \
  --kubernetes.namespace flink-cdc \
  --kubernetes.service-account flink \
  --kubernetes.jobmanager.cpu 1.0 \
  --kubernetes.jobmanager.memory 2048m \
  --kubernetes.taskmanager.cpu 2.0 \
  --kubernetes.taskmanager.memory 4096m \
  --kubernetes.taskmanager.slots 4

# 提交后Flink会在K8s上创建Deployment:
# - flink-cdc-pipeline-app-<job-id>-jm (JobManager)
# - flink-cdc-pipeline-app-<job-id>-tm (TaskManager)
```

#### 步骤3：YARN Application模式部署

```bash
# 1. 确保Hadoop环境就绪
export HADOOP_CLASSPATH=`hadoop classpath`

# 2. 提交到YARN Application模式
flink-cdc.sh pipeline-prod.yaml \
  --target yarn-application \
  --flink-home /opt/flink \
  --yarn.application.name "CDC-Orders-Pipeline" \
  --yarn.application.queue "data" \
  --yarn.application.priority 5 \
  --yarn.taskmanager.cpu 4 \
  --yarn.taskmanager.memory 4096 \
  --yarn.jobmanager.memory 2048

# 3. 查看YARN Application状态
yarn application -list | grep CDC-Orders
```

#### 步骤4：生产级Pipeline配置模板

```yaml
# pipeline-prod.yaml — 生产级配置
source:
  type: mysql
  hostname: ${MYSQL_HOST}              # 环境变量注入
  port: 3306
  username: ${MYSQL_USER}
  password: ${MYSQL_PASSWORD}
  tables: shop.orders
  server-id: 5400-5402
  server-time-zone: Asia/Shanghai
  scan.startup.mode: initial
  # 连接池优化
  connect.timeout: 30s
  connect.max-retries: 5
  debezium:
    snapshot.locking.mode: none

sink:
  type: kafka
  properties:
    bootstrap.servers: ${KAFKA_BOOTSTRAP}
  sink:
    semantic: exactly-once
    batch-size: 16384

pipeline:
  name: CDC Orders Production
  parallelism: 2
  schema.change.behavior: EVOLVE

# 部署相关配置在命令行参数中传递
```

#### 步骤5：K8s下Flink CDC的滚动升级

```bash
# 1. 触发Savepoint
kubectl exec -n flink-cdc deploy/flink-cdc-pipeline-app-jm -- \
  flink savepoint <job-id> s3://my-bucket/flink-cdc-savepoints/

# 2. 停止旧Pipeline
flink-cdc.sh stop <job-id>

# 3. 从Savepoint恢复新版本Pipeline
flink-cdc.sh pipeline-prod-v2.yaml \
  --from-savepoint s3://my-bucket/flink-cdc-savepoints/savepoint-xxx \
  --allow-non-restored \
  --target kubernetes-application \
  --kubernetes.container.image flink:1.21.0    # 升级到Flink 1.21
```

#### 步骤6：K8s上Flink CDC的高可用配置

```yaml
# flink-conf.yaml — K8s HA配置
high-availability: org.apache.flink.kubernetes.highavailability.KubernetesHaServicesFactory
high-availability.storageDir: s3://my-bucket/flink-cdc/ha/

# Checkpoint配置
state.backend: rocksdb
state.backend.incremental: true
state.checkpoints.dir: s3://my-bucket/flink-cdc/checkpoints

# 重启策略
restart-strategy: fixed-delay
restart-strategy.fixed-delay.attempts: 10
restart-strategy.fixed-delay.delay: 30s

# K8s特性
kubernetes.jobmanager.replicas: 2          # JM副本数（HA）
kubernetes.rest-service.exposed.type: LoadBalancer
```

#### 常见陷坑及解决方法

| 问题 | 原因 | 解决方法 |
|------|------|---------|
| Application模式下无法访问Flink Web UI | K8s Service未暴露 | 添加`--kubernetes.rest-service.exposed.type=NodePort`参数 |
| Checkpoint写入S3失败 | 缺少Hadoop/S3依赖JAR | 将`flink-s3-fs-hadoop` JAR放入flink lib目录 |
| YARN Application超时被kill | 默认Application Master超时10分钟 | 设置`yarn.application.timeout=3600000`（1小时） |
| K8s Pod重启后Checkpoint丢失 | state.backend配置为`jobmanager` | 改为`filesystem`或`rocksdb`，配置外部存储路径 |

---

## 4 项目总结

### 部署模式对比

| 模式 | 资源隔离 | 启动速度 | 运维复杂度 | 适用规模 |
|------|---------|---------|-----------|---------|
| Docker Compose | ❌ | 快 | 低 | 开发/测试 |
| Standalone Session | ⚠️（共享TM） | 快 | 低 | 小规模生产 |
| K8s Session | ⚠️（共享TM） | 中 | 中 | 中型生产 |
| K8s Application | ✅（独立集群） | 慢（每次创建集群） | 中 | 推荐生产 |
| YARN Application | ✅（独立集群） | 中 | 中 | Hadoop生态 |
| Flink Operator（K8s） | ✅ | 中 | 高（需安装Operator） | 大规模生产 |

### 生产环境部署清单

```
□ 资源管理:
  □ K8s / YARN资源队列配置
  □ CPU和内存Requests/Limits设置
  □ 配置HPA（Horizontal Pod Autoscaler）

□ 数据持久化:
  □ 外部State Backend（S3/HDFS）
  □ 外部Checkpoint存储
  □ Savepoint存储

□ 高可用:
  □ JobManager副本数 >= 2
  □ HA Storage配置
  □ K8s Pod Anti-Affinity

□ 安全:
  □ 密钥管理（K8s Secret / Vault）
  □ 网络策略（NetworkPolicy）
  □ TLS传输加密

□ 可观测性:
  □ Prometheus Reporter配置
  □ 日志收集（Fluentd/Logstash）
  □ 告警规则配置
```

### 常见踩坑经验

**故障案例1：K8s Application模式下JM Pod无法创建**
- **现象**：提交后JM Pod一直处于Pending状态
- **根因**：K8s集群资源不足，或缺少Flink需要的ServiceAccount权限
- **解决方案**：检查`kubectl describe pod`确认具体原因，创建正确的RBAC Role和ServiceAccount

**故障案例2：YARN Application模式下任务提交成功但无法运行**
- **现象**：`flink-cdc.sh`返回成功，但YARN的Application状态为ACCEPTED后立即FAILED
- **根因**：缺少Hadoop依赖，或flink-cdc.sh关联的JAR包在HDFS上不可访问
- **解决方案**：使用`--yarn.provided.lib.dirs`指定共享lib目录，将Flink + CDC JAR包上传到HDFS

**故障案例3：Checkpoint写入S3失败导致作业不断重启**
- **现象**：作业提交后立即重启，循环不止
- **根因**：S3访问凭证配置错误，Checkpoint无法写入S3。Flink的固定延迟重启策略导致无限重试
- **解决方案**：修复S3凭证，或设置`restart-strategy.fixed-delay.attempts=3`限制重试次数

### 思考题

1. **进阶题①**：Flink K8s Application模式下，如果同时提交10个CDC Pipeline，K8s上会创建10个JM + 10组TM Pod。这些Pod之间如何实现网络互通？每个CDC作业的Kafka Sink是否可以共享同一个Kafka连接池？

2. **进阶题②**：在生产环境中，如果Flink CDC作业需要升级Flink版本（如从1.18升级到1.20），但Flink K8s Operator的版本不同。应该如何设计升级流程？Savepoint的兼容性如何保证？提示：考虑Flink的Savepoint跨版本兼容性策略。

---

> **下一章预告**：第30章「综合实战：多源异构数据集成平台」——中级篇的最终实战。本章将设计并实现一个完整的企业级多源异构数据集成平台：MySQL订单 + PostgreSQL用户 + MongoDB日志 → 统一写入Kafka + Iceberg + Doris。
