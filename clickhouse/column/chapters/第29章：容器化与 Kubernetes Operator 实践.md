# 第29章：容器化与 Kubernetes Operator 实践

> **版本**：ClickHouse 24.x LTS
> **定位**：中级篇核心章节。从 Docker Compose 手动编排到 Altinity Operator 声明式管理，掌握 ClickHouse 在 Kubernetes 上的生产级部署方法论。
> **前置阅读**：第23章（分布式表与集群管理）、第14章（SQL 优化入门）、第4章（MergeTree 家族）
> **预计阅读**：45 分钟 | **实战耗时**：90 分钟

---

## 1. 项目背景

某中型互联网公司的基础设施团队，三年前搭了 ClickHouse 集群——8 台物理机，安装靠 SSH 脚本 + RPM 包手动部署，配置靠 Ansible Playbook 批量下发。每加一个新分片，运维小刘就得先采购机器（等两周），再手写 SSH 脚本装依赖、改 config.xml、配 ZooKeeper、重启集群。一次扩容从决策到上线，最快三周。

更要命的是故障处理。上个月某天凌晨三点，一台机器磁盘满导致进程挂掉，值班同事睡得太死没接到告警，集群瘫痪了四个小时——直到早上业务方打电话骂人才发现。老板在复盘会上拍了桌子："能不能让它崩了自动重启？能不能加机器别让我等两周？能不能像我们后端微服务一样，一句 `kubectl scale` 就搞定了？"

这三个"能不能"就是容器化的核心诉求：**自动化部署、自愈能力、弹性伸缩**。

但 ClickHouse 跟无状态微服务有本质区别。微服务的 Pod 挂了，Kubernetes 直接换一个新的，数据全在远端的 MySQL/Redis 里，无伤大雅。ClickHouse 的 Pod 挂了，它本地的几百 GB MergeTree 数据怎么办？新 Pod 漂到另一台 Node 上，数据读不到——等于失忆。这就是**有状态服务**和 K8s 的**无状态设计哲学**之间的根本矛盾。

团队也试过最朴素的方案：写一个 StatefulSet，每个 Pod 挂一块云盘，Pod 绑死在特定 Node 上。跑了两个月，又冒出新问题：(1) 加一个分片需要手动改 config.xml 里的 shard 定义，再重启所有服务；(2) ZooKeeper 节点配置硬编码在配置文件中，ZK 集群迁移时全部得重改；(3) 存储满了想扩容 PVC，发现用的 `local-path` StorageClass 根本不支持在线 resize。用老板的话说："你们只是把物理机上的脏乱差搬到了容器里。"

正当团队一筹莫展之际，架构师老赵在 GitHub 上发现了一个叫 **Altinity ClickHouse Operator** 的项目——它是 Kubernetes 社区主推的 ClickHouse 官方 Operator，用 CRD（Custom Resource Definition）把集群拓扑、存储配置、ZooKeeper 连接全部声明在一份 YAML 里。改动拓扑只需改 YAML 并 `kubectl apply`，Operator 自动完成剩余操作。

本章将带领你从 Docker Compose 手工编排出发，一步步上到 K8s Operator 的声明式管理，解决上述所有痛点。

---

## 2. 项目设计：剧本式交锋对话

周一早上，运维小刘在工位上对着三块屏幕抓狂。左边是 Pod 列表（有个 Pod CrashLoopBackOff 了半小时），中间是阿里云控制台（磁盘满了要扩容），右边是钉钉群（业务方在催查询超时）。

**小胖**把咖啡往桌上一墩："Docker 不就是一个容器吗？`docker run` 跑起来不就行了？你们非要搞什么 Kubernetes、什么 Operator——我看就是过度设计。我们以前物理机跑得好好的，简单粗暴有效。"

**小白**头也不抬，手里翻着 Altinity Operator 的 GitHub README："小胖你说的简单粗暴，全公司只有你睡得着觉。上个月凌晨那台机器挂了，谁被老板拉到群里骂了整整四十分钟？你说 Docker——那我问你几个很现实的问题："

"第一，你现在要搭一个 2 分片 2 副本的集群，需要 4 个 ClickHouse 实例加 3 个 ZooKeeper 节点，一共 7 个容器。你倒是用 `docker run` 给我起一遍？哪个先起、哪个后起？ZK 还没 Ready 的时候 CH 连不上，你怎么处理？"

"第二，分片 2 的磁盘满了，在物理机上你登录去删文件。在容器里，数据在 `/var/lib/clickhouse` 挂载的 Volume 里——你不能删，那就得扩容 PVC。你的 `docker run` 能扩吗？"

"第三，业务高峰期要临时加一个分片分担压力，你的 `docker run` 怎么让新分片自动注册到集群里、让分布式表知道有新节点加入？"

**小胖**被怼得一愣一愣的，但嘴上不服："那……那 Kubernetes 又能怎么样？不还是容器吗？"

**大师**从工位后走过来，拉过一把椅子坐下："小胖问的其实是一个好问题——容器化只是手段，不是目的。Kubernetes 的价值不在容器本身，在它提供的 **控制循环**。我来画一下演进路径。"

大师在白板上写了三行字：

```
第一代：裸金属时代     SSH + RPM + Ansible      "会跑就行"
第二代：容器摸索期     docker run + Compose     "能搭起来"
第三代：云原生时代     StatefulSet + Operator   "会自己照顾好自己"
```

"ClickHouse 的容器化是一个逐渐把'人的操作'变成'自动控制逻辑'的过程。我们来一步步看。"

---

**大师**先讲 Compose："Docker Compose 解决了第一层问题——**环境一致性和一键启动**。`docker-compose up -d` 一条命令就能把整个集群拉起来，包括 ZooKeeper 和 CH 的依赖顺序。你把 `docker-compose.yml` 提交到 Git，所有开发、测试、预发环境用的都是同一套编排。但 Compose 的边界很明确——它只管启动顺序，不管运行时的容错和伸缩。Pod 挂了它不会自动重启，磁盘满了它不会自动扩容，加节点得手动改 YAML 再跑一遍。"

"所以到了生产环境，你需要 StatefulSet。"

**小胖**插话："StatefulSet 又是什么？比 Deployment 多了啥？"

**大师**："Deployment 管理的是无状态 Pod——Pod 之间完全等价，随便删，新 Pod 完全替代旧 Pod，没有人关心它是第几个。StatefulSet 管理的 Pod 有**固定的编号和网络标识**。比如 `ch-cluster-0`、`ch-cluster-1`、`ch-cluster-2`——名字永远不会变，即使 Pod 被重新调度到另一台 Node 上。这就为 ClickHouse 提供了基础：每个 Pod 都绑着一个 PVC（持久卷声明），Pod 无论被调度到哪，PVC 都会跟随它——或者在支持 ReadWriteMany 的存储上，PVC 保持不动，新 Pod 重新挂上。"

"但 StatefulSet 只是一个**编排原语**，它理解'Pod 需要编号'和'Pod 需要固定的卷'，但不理解'ClickHouse 需要注册到 ZooKeeper、需要出现在分布式表的 shard 配置里、扩容后需要更新宏变量'。这些是 ClickHouse 特有的运维知识——StatefulSet 不懂，它也不应该懂。这就是 Operator 诞生的原因。"

---

**技术映射 #1**：StatefulSet 解决了有状态 Pod 的**身份稳定**和**存储绑定**问题，但不理解应用层逻辑。Operator = StatefulSet + 领域知识（Domain Knowledge）。领域知识包括：集群拓扑注册、ZooKeeper 路径初始化、宏变量自动注入、配置热更新策略、滚动重启顺序。

**小白**追问："所以 Operator 是 StatefulSet 的'智能升级版'？它具体做了哪些我们刚才说的'ClickHouse 特有的运维操作'？"

**大师**："问得好。我们以 Altinity Operator 为例。你在 YAML 里声明了 `shardsCount: 2, replicasCount: 2`——Operator 不是简单地去创建 4 个 Pod。它做的事至少包括：

1. **拓扑发现与注册**：每个 Pod 启动时，Operator 自动注入正确的 `macros`（`{cluster}`、`{shard}`、`{replica}`），这些宏会被 ClickHouse 用来构建 `system.clusters` 表，分布式表依赖它来发现同集群的其他节点。
2. **配置同步**：当你改了 `shardsCount` 从 2 到 3，Operator 不只是创建新 Pod——它会自动更新所有已有 Pod 的 `remote_servers` 配置（即分布式表的路由表），让所有节点知道新分片的存在。
3. **ZooKeeper 路径管理**：每个 ClickHouse 集群在 ZK 中需要一条干净的路径（如 `/clickhouse/tables/cluster-1/`）。Operator 在创建集群时会初始化这条路径；删除集群时会清理（可选，防止孤儿节点）。
4. **滚动重启策略**：当你修改配置（比如 `max_server_memory_usage`），Operator 不是同时重启所有 Pod——它按照一定顺序（先从副本，最后主分片）逐个重启，保证集群始终有可用节点响应查询。"

**小胖**瞪大眼睛："这……这工作量不少啊。那存储呢？我们上次 PVC 满了想扩，说是不支持在线 resize——那到底选什么 StorageClass？"

---

**大师**掉头在白板上画了一张表：

```
StorageClass    延迟      扩容         数据迁移      适用场景
───────────────────────────────────────────────────────────
local-path      极低      ❌不支持     ❌绑定节点    开发/单机测试
Longhorn        低        ✅支持       ✅自动同步    中小规模生产
Ceph RBD        中        ✅支持       ✅无状态块    大规模多租户
hostPath        极低      ❌不支持     ❌绑定节点    性能极致场景
```

"`local-path` 是最常见的—尤其在测试和边缘节点。它的卷直接建在 Node 本地磁盘上，I/O 延迟最低。但有两个致命缺陷：第一，大多数 `local-path` provisioner 不支持 PVC 扩容——你声明了 500Gi，满了就是满了，没法在线 resize。第二，Pod 被调度到另一台 Node 时，PV 还在原 Node 上，数据读不到——所以 StatefulSet 必须配合 `podAffinity` 把 Pod 钉死在某个 Node 上。这在生产环境中是个隐患——Node 故障会导致该分片完全不可用，直到 Node 恢复。"

"Longhorn 是 Rancher 出品的分布式块存储，在 K8s 上跑得很顺。它把每个 Node 的本地磁盘集中成卷池，数据自动跨 Node 做多副本同步。Pod 漂移到其他 Node，Longhorn 会自动把读写流量切到新 Node 上的副本——延迟略高于 `local-path`（多一跳网络），但可用性大幅提升。而且支持在线 resize，直接在 PVC 上改 `resources.requests.storage`，Longhorn 立即扩。"

"Ceph RBD 则是企业级的选择——做了十多年的分布式存储，成熟度最高。它的好处是块设备完全无状态，Pod 调度到哪，RBD 的块就 attach 到哪。但延迟是最高的——RBD 的三副本写需要等所有副本确认，对写入密集的 ClickHouse Merge 任务有影响。还有一点：Ceph 的运维复杂度本身就是一门学科，没有专门的存储团队不要轻易碰。"

---

**技术映射 #2**：ClickHouse 在 K8s 上的存储选型 = I/O 延迟 vs 可用性 vs 运维复杂度的三元权衡。`local-path` 性能最好但可用性最差（数据与 Node 绑定，Node 宕机 = 数据不可达）。Longhorn/Ceph 通过分布式多副本换取高可用，代价是额外的网络 I/O 延迟。生产环境建议 Longhorn（自带 K8s 集成，运维简单）或 Ceph（有专人维护时选择）。

---

**小白**追问："那 ZooKeeper 呢？也在 K8s 里跑吗？数据会不会丢？"

**大师**："两种方案。方案一：用 Helm Chart 或 Operator 在 K8s 里搭一个 ZK 集群。官方 ZK 镜像 + StatefulSet + 专用 PVC 即可。Helm 的 Bitnami Chart 已经封装得不错，一键安装 3 节点 ZK。优点是部署一致性——ZK 和 CH 都受同样的 K8s 调度和管理。缺点是你要维护另一个有状态服务——ZK 的磁盘 I/O 对 latency 要求很高，如果 K8s 节点的磁盘 I/O 负载高，ZK 会话超时会导致 CH 集群脑裂。"

"方案二：用 ClickHouse 自带的 `clickhouse-keeper` 替代 ZooKeeper。从 21.x 版本起，ClickHouse 内置了一套兼容 ZK 协议的 Keeper 组件，用 C++ 重写了核心逻辑。你可以用 Altinity Operator 一键部署 Keeper，代码路径与 CH 一致，省去额外维护 ZK 集群的麻烦。生产环境推荐用 Keeper——性能更好，延迟更低。"

"至于数据安全性——ZK/Keeper 的 `dataDir` 一定要挂 PVC。Snapshot 和事务日志存在磁盘上，Pod 重启后从 Snapshot 恢复，配合 `autopurge.snapRetainCount` 参数控制保留的 Snapshot 数量。"

---

**小胖**来了兴致："那自动伸缩呢？Kubernetes 不是有 HPA 吗？能不能让 ClickHouse 在高峰期自动加分片，低峰期自动减？"

**大师**哈哈一笑："这是**容器化 ClickHouse 最难回答的问题**。HPA（Horizontal Pod Autoscaler）对无状态服务很好用——看 CPU 高了就加 Pod，低了就减，Service 自动负载均衡。但 ClickHouse 的 HPA 有本质障碍：

1. **数据分布问题**：新分片加入后，老分片上的数据不会自动迁移到新分片上。如果你的数据按 `toYYYYMMDD(timestamp)` 分片，加了一个新分片——新分片是空的，老分片照旧压着，查询热点根本没转移。你必须手动执行数据重均衡。
2. **反收缩更难**：减少分片意味着你要把被减分片上的数据先迁移到剩余分片，然后安全地把这个分片从分布式表的配置里摘掉。少一步，数据就丢了。
3. **分布式查询的放大效应**：`SELECT count() FROM distributed_table` 会发给所有分片。分片越多，扇出越大，延迟增加。如果不加节制地 scale，反而降低查询性能。"

"所以现实是——**Altinity Operator 已经支持声明式修改 shardsCount/ replicasCount（改 YAML + kubectl apply），但数据重均衡仍需人工介入**。HPA 建议搭配监控告警——CPU/内存/磁盘水位接近阈值时，自动发告警，人工评估是否需要扩分片。完全无人值守的自动 HPA，今天的 ClickHouse 还做不到。这不是 Operator 的问题，是分布式 OLAP 数据库本身的架构约束。"

---

**技术映射 #3**：ClickHouse 的弹性伸缩瓶颈在**数据重分布**，不在 Pod 的创建速度。K8s HPA 的"加 Pod = 加容量"对 ClickHouse 并不成立——新 Pod 只是一个空壳，数据不会自动迁移。真正的扩容是"加分片 + 迁移数据"，需要人工介入或外部工具（如 `clickhouse-backup` + `clickhouse-copier`）配合。

大师合上白板笔："总结一下。从裸金属到 Operator，我们是在把运维知识从人的脑子里搬到 YAML 里，从手工变成自动化。Operator 不是银弹——它不能替你决定什么时候扩容、数据怎么均衡——但它把你能决定的那些事，从十几个 SSH 命令浓缩成一句 `kubectl apply`。这就是它的价值。"

---

## 3. 项目实战

### 环境准备

本章实验需要一套 K8s 集群环境。如果你没有现成的集群，可以用 Kind 或 Minikube 在本地搭建：

```bash
# 方案一：Kind（推荐，轻量级本地 K8s）
kind create cluster --name clickhouse-lab --config - <<EOF
kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
nodes:
  - role: control-plane
  - role: worker
  - role: worker
  - role: worker
EOF

# 方案二：Minikube（带 4 核 8G 以上）
minikube start --cpus=4 --memory=8192 --driver=docker
```

确保以下工具已安装：
```bash
kubectl version --client
helm version
docker version
```

---

### Step 1：Docker Compose 编排 ClickHouse + ZooKeeper 集群

先从最基础的 Compose 入手，理解多节点集群的拓扑依赖。创建项目目录：

```bash
mkdir -p ch-comp-lab/config/{ch1,ch2}
```

编写集群配置文件。每个 ClickHouse 节点需要指定自己的 `macros`，以便分布式表通过 `system.clusters` 发现其他节点：

```xml
<!-- config/ch1/macros.xml -->
<clickhouse>
    <macros>
        <cluster>lab_cluster</cluster>
        <shard>01</shard>
        <replica>ch1</replica>
    </macros>
</clickhouse>
```

```xml
<!-- config/ch2/macros.xml -->
<clickhouse>
    <macros>
        <cluster>lab_cluster</cluster>
        <shard>02</shard>
        <replica>ch2</replica>
    </macros>
</clickhouse>
```

再为两个节点编写共享的集群拓扑配置：

```xml
<!-- config/ch1/remote_servers.xml（两个节点内容相同） -->
<clickhouse>
    <remote_servers replace="1">
        <lab_cluster>
            <shard>
                <replica>
                    <host>ch1</host>
                    <port>9000</port>
                </replica>
            </shard>
            <shard>
                <replica>
                    <host>ch2</host>
                    <port>9000</port>
                </replica>
            </shard>
        </lab_cluster>
    </remote_servers>
</clickhouse>
```

Docker Compose 文件：

```yaml
# docker-compose.yml
version: '3.8'
services:
  zk1:
    image: zookeeper:3.8
    environment:
      ZOO_MY_ID: 1
      ZOO_SERVERS: server.1=zk1:2888:3888 server.2=zk2:2888:3888 server.3=zk3:2888:3888

  zk2:
    image: zookeeper:3.8
    environment:
      ZOO_MY_ID: 2
      ZOO_SERVERS: server.1=zk1:2888:3888 server.2=zk2:2888:3888 server.3=zk3:2888:3888

  zk3:
    image: zookeeper:3.8
    environment:
      ZOO_MY_ID: 3
      ZOO_SERVERS: server.1=zk1:2888:3888 server.2=zk2:2888:3888 server.3=zk3:2888:3888

  ch1:
    image: clickhouse/clickhouse-server:24.12
    depends_on: [zk1, zk2, zk3]
    hostname: ch1
    ports: ["8123:8123"]
    volumes:
      - ch1_data:/var/lib/clickhouse
      - ./config/ch1:/etc/clickhouse-server/config.d
    ulimits:
      nofile:
        soft: 262144
        hard: 262144

  ch2:
    image: clickhouse/clickhouse-server:24.12
    depends_on: [zk1, zk2, zk3]
    hostname: ch2
    ports: ["8124:8123"]
    volumes:
      - ch2_data:/var/lib/clickhouse
      - ./config/ch2:/etc/clickhouse-server/config.d
    ulimits:
      nofile:
        soft: 262144
        hard: 262144

volumes:
  ch1_data:
  ch2_data:
```

启动集群：

```bash
docker-compose up -d

# 检查所有服务是否就绪
docker-compose ps

# 进入 ch1 创建分布式表
docker exec -it ch-comp-lab-ch1-1 clickhouse-client -q "
CREATE TABLE test_local ON CLUSTER lab_cluster (
    event_time DateTime,
    user_id UInt64,
    event_type String
) ENGINE = MergeTree()
PARTITION BY toYYYYMM(event_time)
ORDER BY (event_time, user_id);

CREATE TABLE test_distributed ON CLUSTER lab_cluster AS test_local
ENGINE = Distributed(lab_cluster, default, test_local, rand());
"

# 写入测试数据，验证分布
for i in $(seq 1 1000); do
  docker exec ch-comp-lab-ch1-1 clickhouse-client -q \
    "INSERT INTO test_distributed VALUES (now(), $i, 'click')"
done

# 分别查两个节点，观察数据分布
docker exec ch-comp-lab-ch1-1 clickhouse-client -q \
  "SELECT count() FROM test_local"
docker exec ch-comp-lab-ch2-1 clickhouse-client -q \
  "SELECT count() FROM test_local"
```

Compose 方案的局限性在这一步就会暴露：当你尝试增加第三个分片时，必须手动：(1) 创建新 service 和 volume，(2) 在 `remote_servers.xml` 中增加 shard 定义，(3) 将新配置复制到所有已有节点，(4) 逐个重启所有 CH 容器。这就是 Operator 要解决的痛点。

---

### Step 2：构建自定义 ClickHouse 镜像

在生产环境中，你几乎不会直接用官方镜像——至少需要加自定义配置、UDF 脚本、健康检查。编写 Dockerfile：

```dockerfile
# Dockerfile
FROM clickhouse/clickhouse-server:24.12

# 安装自定义工具（UDF 运行时依赖）
RUN apt-get update && apt-get install -y \
    python3 python3-pip \
    && rm -rf /var/lib/apt/lists/*

# 复制自定义配置（会覆盖 /etc/clickhouse-server/config.d/ 下的默认配置）
COPY config/custom.xml /etc/clickhouse-server/config.d/custom.xml
COPY config/users.xml   /etc/clickhouse-server/users.d/custom.xml

# 复制 UDF 脚本（executable 类型的 UDF）
COPY udf/ /var/lib/clickhouse/user_scripts/
RUN chmod +x /var/lib/clickhouse/user_scripts/*.py

# 预创建常用目录，避免运行时权限问题
RUN mkdir -p /var/lib/clickhouse/user_files /var/lib/clickhouse/backup \
    && chown -R clickhouse:clickhouse /var/lib/clickhouse

# 自定义健康检查（比默认的 TCP 端口探测更可靠——验证引擎层可用性）
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD clickhouse-client --query "SELECT 1" || exit 1

# 切换为非 root 用户运行
USER clickhouse
```

构建并推送镜像：

```bash
# 构建镜像
docker build -t my-registry/clickhouse-custom:24.12 .

# 推送到私有仓库（K8s 集群需要能拉取到）
docker push my-registry/clickhouse-custom:24.12
```

关键提示：**HelathCheck 指令对 K8s 尤其重要**。K8s 的 `readinessProbe` 和 `livenessProbe` 依靠这个健康检查来判断 Pod 是否就绪。如果 ClickHouse 进程存活但 ZooKeeper 连接断开了导致查询全部超时，TCP 端口检测是无法发现的——因为它只检查 9000 端口是否监听。`clickhouse-client --query "SELECT 1"` 则测试了完整的查询路径：网络 → 权限校验 → 引擎初始化 → 结果返回。

---

### Step 3：部署 Altinity ClickHouse Operator

[Altinity Operator](https://github.com/Altinity/clickhouse-operator) 是 CNCF 生态中最成熟的 ClickHouse K8s Operator，核心 CRD 叫 `ClickHouseInstallation`（简称 CHI）：

```bash
# 安装 Operator（部署到 kube-system namespace）
kubectl apply -f https://github.com/Altinity/clickhouse-operator/raw/master/deploy/operator/clickhouse-operator-install.yaml

# 验证 Operator Pod 运行状态
kubectl get pods -n kube-system -l app=clickhouse-operator

# 查看 CRD 安装
kubectl get crd | grep clickhouse
```

CRD 列表包括：
- `ClickHouseInstallation`（CHI）：核心资源，定义一个 ClickHouse 集群的完整拓扑
- `ClickHouseInstallationTemplate`（CHIT）：可复用的配置模板（镜像、资源限制、PVC 规格）
- `ClickHouseOperatorConfiguration`（CHOPConf）：Operator 自身的行为配置

现在声明一个生产级集群：

```yaml
# clickhouse-cluster.yaml
apiVersion: "clickhouse.altinity.com/v1"
kind: "ClickHouseInstallation"
metadata:
  name: "production-cluster"
spec:
  defaults:
    templates:
      podTemplate: clickhouse-pod
      volumeClaimTemplate: clickhouse-storage
      serviceTemplate: clickhouse-service

  configuration:
    # ZooKeeper 连接配置（在 K8s 中用 StatefulSet Headless Service 发现）
    zookeeper:
      nodes:
        - host: zk-0.zk-headless.default.svc.cluster.local
          port: 2181
        - host: zk-1.zk-headless.default.svc.cluster.local
          port: 2181
        - host: zk-2.zk-headless.default.svc.cluster.local
          port: 2181

    # ClickHouse 全局设置（config.xml 级别）
    settings:
      max_server_memory_usage_to_ram_ratio: 0.85
      max_concurrent_queries: 100
      max_partitions_per_insert_block: 100
      merge_tree/min_rows_for_wide_part: 0

    # 集群拓扑定义
    clusters:
      - name: default
        layout:
          shardsCount: 2
          replicasCount: 2

        # 内置 Keeper 替代外部 ZooKeeper（可选）
        # layout:
        #   shardsCount: 2
        #   replicasCount: 2
        #   keeper:
        #     enabled: true
        #     replicasCount: 3

  # 模板定义区
  templates:
    # Pod 模板：定义容器的镜像、资源、端口、探针
    podTemplates:
      - name: clickhouse-pod
        spec:
          containers:
            - name: clickhouse
              image: clickhouse/clickhouse-server:24.12
              resources:
                requests:
                  memory: "8Gi"
                  cpu: "4"
                limits:
                  memory: "16Gi"
                  cpu: "8"
              ports:
                - name: http
                  containerPort: 8123
                - name: tcp
                  containerPort: 9000
                - name: interserver
                  containerPort: 9009
              readinessProbe:
                httpGet:
                  path: /ping
                  port: 8123
                initialDelaySeconds: 30
                periodSeconds: 10
              livenessProbe:
                httpGet:
                  path: /ping
                  port: 8123
                initialDelaySeconds: 60
                periodSeconds: 30

    # 服务模板：K8s Service 配置
    serviceTemplates:
      - name: clickhouse-service
        spec:
          ports:
            - name: http
              port: 8123
            - name: tcp
              port: 9000
          type: ClusterIP

    # PVC 模板：持久化存储声明
    volumeClaimTemplates:
      - name: clickhouse-storage
        spec:
          accessModes: ["ReadWriteOnce"]
          storageClassName: "local-path"    # 生产环境建议 Longhorn 或 Ceph RBD
          resources:
            requests:
              storage: "500Gi"
```

提交资源并使用 kubectl 观察创建过程：

```bash
# 部署集群
kubectl apply -f clickhouse-cluster.yaml

# 查看 CHI 资源状态（正在创建时状态为 InProgress，完成后变为 Completed）
kubectl get chi production-cluster -w

# 查看 Operator 创建的 Pod（命名规则：chi-{集群名}-{集群}-{分片}-{副本}-{实例序号}）
kubectl get pods -l clickhouse.altinity.com/chi=production-cluster

# 预期输出：4 个 CH Pod + 可能的 Keeper Pod
# NAME                                              READY   STATUS
# chi-production-cluster-default-0-0-0              1/1     Running
# chi-production-cluster-default-0-1-0              1/1     Running
# chi-production-cluster-default-1-0-0              1/1     Running
# chi-production-cluster-default-1-1-0              1/1     Running
```

查看 Operator 自动生成的 Service 和 ConfigMap：

```bash
# Operator 为每个 Pod 创建了一个独立的 Service（用于集群内节点间通信）
kubectl get svc -l clickhouse.altinity.com/chi=production-cluster

# Operator 为每个 Pod 生成了包含正确 macros 的配置
kubectl get configmap -l clickhouse.altinity.com/chi=production-cluster

# 查看某个 Pod 的宏变量是否正确注入
kubectl exec chi-production-cluster-default-0-0-0 -- \
  clickhouse-client -q "SELECT * FROM system.macros"
# 预期输出：
# cluster    default
# shard      0
# replica    0
```

---

### Step 4：存储性能测试

不同 StorageClass 的 I/O 性能直接影响 ClickHouse Merge 和扫描的速度。在 Pod 内执行测试：

```bash
# 进入任意 ClickHouse Pod
kubectl exec -it chi-production-cluster-default-0-0-0 -- bash

# 顺序写入测试（oflag=direct 绕过页缓存，测真实磁盘性能）
dd if=/dev/zero of=/var/lib/clickhouse/test bs=1M count=2000 oflag=direct

# 顺序读取测试
dd if=/var/lib/clickhouse/test of=/dev/null bs=1M count=2000 iflag=direct

# ClickHouse 内置磁盘性能压测（推荐——更贴近引擎读写模式）
clickhouse-client -q "
SELECT * FROM file('disk_benchmark', 'RawBLOB', 'x UInt8')
SETTINGS max_threads=1
"
```

三种典型 StorageClass 的预期性能（NVMe SSD 环境）：

```
StorageClass      顺序写(MB/s)  顺序读(MB/s)  随机读 IOPS
─────────────────────────────────────────────────────────
local-path        1800-2500      2500-3200      350K-500K
Longhorn          600-900        800-1200       80K-150K
Ceph RBD (SSD)    400-700        500-900        50K-80K
```

结论很直观：`local-path` 性能远超任何分布式存储，因为它直接访问裸盘。**如果你的 ClickHouse 集群不需要跨 Node 的 Pod 漂移能力（即每个分片固定绑定到一台 Node），`local-path` 是性能最优选**——代价是 Node 宕机导致分片不可用，需要靠副本在其他分片上的冗余来恢复查询。

---

### Step 5：部署 ZooKeeper / clickhouse-keeper

方案 A：使用 Bitnami Helm Chart 部署 ZooKeeper：

```bash
helm repo add bitnami https://charts.bitnami.com/bitnami
helm repo update

# 安装 3 节点 ZK 集群，每个节点 20Gi 存储
helm install zk bitnami/zookeeper \
  --set replicaCount=3 \
  --set persistence.size=20Gi \
  --set persistence.storageClass=local-path

# 查看 ZK Headless Service（CHI 配置中用这个地址来发现 ZK 节点）
kubectl get svc zk-headless

# 验证 ZK 集群状态
kubectl exec zk-0 -- zkServer.sh status
```

方案 B：使用 clickhouse-keeper（推荐）。修改 CHI YAML，将 `zookeeper.nodes` 替换为内置 Keeper 配置即可，Operator 会自动创建 Keeper 专用 Pod：

```yaml
# 在 clusters 的 layout 中加入 keeper 配置段（替代 YAML 中的 external zookeeper nodes）
# 更详细的写法：
layout:
  shardsCount: 2
  replicasCount: 2
  keeper:
    enabled: true
    replicasCount: 3
    volumeClaimTemplate: clickhouse-storage
config:
  keeper_server:
    tcp_port: 9181
    server_id: "{installation}-{cluster}-{shard}-{replica}"
    coordination_settings:
      operation_timeout_ms: 10000
      session_timeout_ms: 30000
    raft_configuration:
      server:
        - id: 1
          hostname: keeper-0.keeper-headless
          port: 9444
        - id: 2
          hostname: keeper-1.keeper-headless
          port: 9444
        - id: 3
          hostname: keeper-2.keeper-headless
          port: 9444
```

---

### Step 6：运维操作——扩缩容与配置变更

**场景一：增加分片（Scale Out）**

将 `shardsCount: 2` 改为 `shardsCount: 3`，重新 apply：

```yaml
# 仅修改 shardsCount 字段
layout:
  shardsCount: 3
  replicasCount: 2
```

```bash
# 应用变更
kubectl apply -f clickhouse-cluster.yaml

# 观察新 Pod 创建和旧 Pod 配置更新
kubectl get pods -l clickhouse.altinity.com/chi=production-cluster -w

# Operator 自动执行的操作：
# 1. 创建新的 StatefulSet（或 Pod Template）
# 2. 新 Pod 启动并注册到 ZooKeeper/Keeper
# 3. 更新所有已有 Pod 的 remote_servers 配置（添加入口指向新分片）
# 4. 按顺序滚动重启受影响的 Pod
```

**场景二：手动数据重均衡**

新分片加入后，历史数据不会自动迁移。你需要手动触发：

```sql
-- 1. 在任一节点上查询各分片数据量分布
SELECT
    hostName(),
    count() AS rows,
    formatReadableSize(sum(bytes_on_disk)) AS disk
FROM clusterAllReplicas('default', system.parts)
WHERE active
GROUP BY hostName()
ORDER BY rows DESC;

-- 2. 方案一：通过分布式表 SELECT + INSERT 重新分发（简单粗暴，但会全量扫描）
-- 注意：在低峰期执行；可能触发大量 MERGE 操作
INSERT INTO test_distributed
SELECT * FROM test_distributed;

-- 3. 方案二：使用 clickhouse-copier（推荐）
-- clickhouse-copier 是官方提供的分区级数据迁移工具，支持断点续传
-- 通过 ZooKeeper 协调多台 Worker 并行搬数据，比 INSERT SELECT 高效得多

-- 4. 方案三：分区迁移（如果按时间分区，直接 DETACH/ATTACH 分区）
-- ALTER TABLE test_local ON CLUSTER default DETACH PARTITION '202503';
-- ALTER TABLE test_local ON CLUSTER default ATTACH PARTITION '202503';
```

**场景三：配置热更新**

修改内存限制等运行时参数：

```yaml
# 在 configuration.settings 中修改
settings:
  max_server_memory_usage_to_ram_ratio: 0.80   # 从 0.85 降至 0.80
  max_concurrent_queries: 150                   # 从 100 调至 150
```

```bash
# 应用变更
kubectl apply -f clickhouse-cluster.yaml

# Operator 会识别 settings 变更，生成新的 ConfigMap，
# 然后按"先副本后主分片"的顺序滚动重启 Pod
# 注意：滚动重启期间，同分片的不同副本保证至少有一个在线
kubectl rollout status statefulset/chi-production-cluster-default-0
```

**场景四：PVC 扩容**

如果 StorageClass 支持在线扩容（如 Longhorn），直接修改 PVC：

```bash
# 直接编辑 PVC 的 storage request（需要 StorageClass allowVolumeExpansion=true）
kubectl edit pvc data-chi-production-cluster-default-0-0-0
# 将 storage: 500Gi 改为 storage: 800Gi

# 观察扩容进度
kubectl describe pvc data-chi-production-cluster-default-0-0-0 | grep -A5 Conditions

# 如果使用的 local-path 不支持扩容，需要手动迁移：
# 1. 创建新的更大 PVC
# 2. 逐个分片做 ALTER TABLE ... FETCH PARTITION（利用副本机制迁移数据）
# 3. 停止旧 PVC 对应的 Pod，切换到新 PVC
```

---

### 测试验证

完成以上所有步骤后，逐条验证核心功能：

```bash
# 1. 集群健康检查——所有 Pod Running
kubectl get pods -l clickhouse.altinity.com/chi=production-cluster

# 2. 写入测试数据，验证数据在各分片上的分布
kubectl exec chi-production-cluster-default-0-0-0 -- clickhouse-client -q "
CREATE TABLE test_dist ON CLUSTER '{cluster}' (
    ts DateTime, id UInt64, val Float64
) ENGINE = Distributed('{cluster}', default, test_local, rand());
"

# 插入 10 万行
for i in $(seq 1 100); do
  kubectl exec chi-production-cluster-default-0-0-0 -- clickhouse-client -q \
    "INSERT INTO test_dist SELECT now() + number, number, rand() FROM numbers(1000)"
done

# 查看每个 Pod 的数据量
for pod in $(kubectl get pods -l clickhouse.altinity.com/chi=production-cluster -o name); do
  echo "=== $pod ==="
  kubectl exec $pod -- clickhouse-client -q "SELECT count() FROM test_local"
done

# 3. 模拟故障自愈——删除一个 Pod
kubectl delete pod chi-production-cluster-default-0-1-0

# 观察 Pod 自动重建（StatefulSet 控制器会立即创建同名新 Pod）
kubectl get pods -w

# 4. 模拟扩容——修改 YAML 增加一个分片后 apply
# 观察新分片 Pod 自动创建，并检查集群配置是否自动更新
kubectl exec chi-production-cluster-default-2-0-0 -- clickhouse-client -q \
  "SELECT hostName() FROM system.clusters WHERE cluster='default'"
```

验证清单：

| 测试项                    | 预期结果                                   | 通过? |
|---------------------------|--------------------------------------------|-------|
| `kubectl get pods`        | 2分片×2副本 = 4 个 Pod 全部 Running         | ☐     |
| INSERT 后分布查询          | 4 个 Pod 各有大致相近的数据量                | ☐     |
| 删除一个 Pod               | StatefulSet 自动重建同编号 Pod，PVC 重新挂载 | ☐     |
| 查询分布式表               | 数据返回正确，无节点缺失报错                   | ☐     |
| 扩容到 3 分片              | 新 Pod 创建，已有 Pod 配置自动更新，不含手动干预 | ☐     |

---

## 4. 项目总结

### 部署方式全景对比

| 方式                 | 复杂度 | 自动扩缩 | 自愈能力 | 配置管理        | 适用场景           |
|----------------------|--------|----------|----------|-----------------|--------------------|
| 裸金属 + Ansible     | 低起步 | ❌       | ❌       | 手动            | 性能极致、物理机独占 |
| Docker Compose       | 低     | ❌       | 有限     | 手动            | 开发测试、PoC       |
| K8s StatefulSet      | 中     | 手动     | ✅       | 手动 ConfigMap  | 小规模生产           |
| Altinity Operator    | 中     | 半自动*  | ✅       | 声明式 CRD      | 中大型生产、多租户   |
| 云厂商托管版          | 低     | ✅       | ✅       | 控制台          | 不想运维、预算充足   |

> \* Operator 支持声明式修改拓扑（改 YAML 即扩缩容），但数据重均衡仍需手动触发，不是真正的 auto-scaling。

### 适用场景

- **多租户 ClickHouse 服务平台**：同一个 K8s 集群上跑多个互相隔离的 CHI 实例，每个实例独立分配资源配额（ResourceQuota），通过 Namespace 隔离。Operator 的 CHIT 模板机制让创建一套"标准规格"的 ClickHouse 租户变成改几行 YAML。
- **CI/CD 集成环境**：每个 Pull Request 自动拉起一套完整 CH 集群跑集成测试，测试完自动销毁。Operator + Helmfile / Kustomize 可以实现"一个环境 = 一个 YAML overlay"。
- **跨云 / 混合云部署**：K8s 是最高效的云抽象层。一套 Operator YAML，在 AWS EKS、阿里云 ACK、本地 Kubeadm 上都能跑（前提是 StorageClass 适配好）。

### 不适用场景

- **极致 I/O 性能要求**：如果你在用 `clickhouse-benchmark` 压 QPS，目标是单节点 10W+ QPS——裸金属 + NVMe + local-path 才是答案，K8s 的网络和存储虚拟化开销会吃性能。
- **集群规模极小**：单节点、数据不超过 1TB。一个 `docker run` 足够，K8s 的复杂度不值得。
- **团队无 K8s 运维经验**：Operator 降低了 ClickHouse 的运维门槛，但前提是你已经有一套健康运行的 K8s 集群。K8s 本身的运维（版本升级、网络插件、RBAC、StorageClass 调试）仍然需要专项技能。

### 常见踩坑经验

**坑一：StatefulSet 删除 PVC 行为**
```bash
# 执行此命令会删除 StatefulSet，但 PVC 默认不会删除！
kubectl delete statefulset chi-production-cluster-default-0
# PVC 仍然存在，数据没有丢——这是 K8s 的保护机制。
# 但如果你不小心 delete 了 PVC，数据就真没了。
# 最佳实践：给 PVC 打上 finalizers 或使用 Velero 定期备份。
```

**坑二：local-path 存储的节点亲和性**
```
场景：Pod 原本在 Node-A 上，PV 在 Node-A 的 /mnt/disks/ssd1/。
Node-A 宕机，Pod 被调度到 Node-B。
打开 /var/lib/clickhouse——空的。
原因：local-path PV 是 hostPath 的一种封装，物理绑定在一台 Node 上。
解决：在 Pod Template 中设置 nodeSelector 或 podAffinity 锁定节点，
      或者使用分布式存储（Longhorn/Ceph），牺牲延迟换可用性。
```

**坑三：Operator 更新时的短暂不可用**
```
Operator 集群滚动重启时，逐个 Pod 重启。某个分片的两个副本可能被先后重启，
导致该分片短暂不可用。极端情况下，如果重启速度过快、重启前没有等副本追赶完成，
查询会报 "All replicas are stale"。
解决：调整 Operator 的 reconcile 间隔，配置更长的 gracePeriod，
      生产环境建议在低峰期执行拓扑变更。
```

**坑四：ClickHouse 启动慢导致的健康检查误杀**
```
Pod 启动后，ClickHouse 进程需要做副本追赶（从 ZooKeeper/Keeper 拉取元数据），
如果数据量大，追赶可能需要数分钟。在此期间 readinessProbe 失败，
K8s 判断 Pod 不健康，反复重启——形成"启动-被杀-启动"的死循环。
解决：调大 initialDelaySeconds（如 120s），
      将 failureThreshold 增大到 5。必要时使用 startupProbe 替代 livenessProbe。
```

### 思考题

1. **为什么 ClickHouse 在 K8s 上很难实现真正的 HPA（水平自动伸缩）？**  
   提示：思考数据分布——新加入的 ClickHouse 节点有数据吗？如果数据按 `rand()` 分片，旧的查询负载能自动分摊到新节点吗？分布式 OLAP 的扩容和微服务的扩容本质区别在哪里？

2. **扩容一个新分片后，如何在不影响在线服务的情况下完成数据重均衡？**  
   提示：考虑使用 `clickhouse-copier` 的分区级迁移能力、`ALTER TABLE ... MOVE PARTITION` 的原子性、以及在物化视图中利用 `ReplicatedMergeTree` 的 FETCH PARTITION 能力。设计一个 4 步操作手册（step-by-step）。

3. **如果 ZooKeeper 也在 K8s 里运行，ClickHouse 和 ZK 部署在同一 Namespace 还是不同 Namespace？各自的 trade-off 是什么？**  
   提示：考虑故障隔离（同一个 Node 宕机后同时失去 CH 和 ZK 的风险）、网络延迟（同 Namespace 跨 Namespace 的 Service 发现是否有性能差异）、RBAC 权限隔离。

