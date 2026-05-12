# 第28章：Harbor 于 Kubernetes 生产落地

## 1 项目背景

某电商平台Kubernetes集群规模已达200+ Node、3000+ Pod，所有微服务已容器化。团队决定将Harbor从Docker Compose单机部署迁移到K8s集群中运行，以统一基础设施运维栈。但首次迁移尝试以"回滚到Compose"告终——团队低估了Harbor在K8s中的复杂度。

**痛点一：Helm Chart默认值在生产环境引发级联故障。** 团队直接用`helm install harbor/harbor`（全部默认值）部署后，发现所有组件默认内存512MB limit——Core在启动5分钟后因加载全部8000+仓库元数据到内存而触发OOMKilled。默认`persistence`配置使用集群默认StorageClass，结果PostgreSQL的PVC被调度器分配到了HDD节点上——随机IO性能只有SSD的1/10，API响应从150ms飙到了2秒。更糟的是，默认`core.replicas=1`导致升级时必然有停机窗口。

**痛点二：证书管理迷宫——4种证书10+个配置项。** Harbor在K8s中至少涉及4类TLS证书：(1) Ingress外部TLS证书（用户浏览器到Nginx）；(2) 内部组件间TLS证书（Core→Registry通信）；(3) Core的token签名私钥；(4) Trivy漏洞数据库下载证书（如果走代理）。Helm Chart中这些证书的配置分散在`expose.tls`、`internalTLS`、`core.secret`、`trivy.ssl`等多个values路径下，配置遗漏一个就导致Core无法连接Registry——报错信息却是模糊的`dial tcp: i/o timeout`而非"证书不匹配"。

**痛点三：优雅升级失败——PostgreSQL写入冲突引发死锁。** Harbor Helm Chart执行`helm upgrade`时的默认`strategy.type=RollingUpdate`，导致新旧Core Pod同时运行了约15秒。两个Core实例同时尝试初始化数据库schema（migration）、同时更新`project`表的同一行元数据——PostgreSQL报`deadlock detected`。更隐蔽的是，新旧版本的JobService同时尝试处理同一批复制任务——Registry端的blob push出现409 conflict。

**痛点四：备份恢复策略不K8s化——仍在用Docker exec。** 团队沿用Docker Compose时代的备份脚本：`docker exec harbor-db pg_dump > backup.sql`。但在K8s中Pod名称是动态的（`harbor-database-0`、`harbor-database-1`），需要先`kubectl get pod -n harbor | grep database`查Pod名再执行备份。而且这种"手工备份"没有纳入K8s的CronJob体系——备份成功与否无人知晓，备份文件散落在运维笔记本上。

---

## 2 项目设计——剧本式交锋对话

**场景：K8s迁移方案评审会，小胖把失败的helm install输出打印了一桌子，大师拿着红笔在上面圈问题。**

**【第一轮：小胖开球——helm install不就行了？】**

**小胖**（指着满桌的报错日志）："大师，K8s部署Harbor不就是`helm install`一下吗？跟点外卖一样，点一下按钮就送到了——结果我们点了，送来了一个'夹生饭'。"

**大师**（拿起一张A4纸，画了一个架构图）："小胖，Harbor在K8s里不是'一份外卖'——它是'一整桌菜'。Helm Chart帮你摆好了碗筷（配置模板），但菜的分量（资源配额）、口味（存储后端）、上菜顺序（启动依赖）都需要你自己调。"

"Harbor Helm Chart把原本Docker Compose的9个单体服务映射成了K8s的原生资源。但你得理解这个映射关系，才能知道该调哪里："

```
┌─────────────────────────────────────────────────┐
│                  K8s Cluster                     │
│                                                  │
│  ┌── Ingress (Nginx/Traefik) ────────────────┐  │
│  │  harbor.company.com → harbor-portal:80     │  │
│  │  harbor.company.com/v2 → harbor-core:8080  │  │
│  └────────────────────────────────────────────┘  │
│                                                  │
│  ┌── Deployments (无状态，可水平扩展) ─────────┐  │
│  │  harbor-core      → 2 replicas (API核心)    │  │
│  │  harbor-portal    → 2 replicas (Web界面)    │  │
│  │  harbor-jobservice→ 1 replica (异步任务)    │  │
│  │  harbor-nginx     → 1 replica (反向代理)    │  │
│  └──────────────────────────────────────────────┘  │
│                                                  │
│  ┌── StatefulSets (有状态，需要持久存储) ──────┐  │
│  │  harbor-database  → 1 replica (PG 15)      │  │
│  │  harbor-redis     → 1 replica (Redis 7)    │  │
│  │  harbor-registry  → 1 replica (Registry)   │  │
│  │  harbor-trivy     → 1 replica (漏洞扫描)    │  │
│  └──────────────────────────────────────────────┘  │
│                                                  │
│  ┌── PVCs (持久化存储) ────────────────────────┐  │
│  │  data-harbor-database-0  → 50Gi (SSD!)     │  │
│  │  data-harbor-redis-0     → 20Gi             │  │
│  │  registry-storage        → 500Gi (对象/S3) │  │
│  └──────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────┘
```

**技术映射**：Harbor Helm Chart（helm repo: `https://helm.goharbor.io`）将一个Docker Compose项目中的9个service逐一映射为K8s资源。核心设计原则：(1) 无状态组件用Deployment（Core、Portal、Nginx、JobService）——支持HPA自动扩缩容；(2) 有状态组件用StatefulSet（PostgreSQL、Redis、Registry、Trivy）——保证了Pod的有序启动和稳定的网络标识（Pod-0, Pod-1）；(3) Registry的选择是关键——如果用本地文件系统存储，Registry必须用StatefulSet（PVC绑定到特定Pod）；如果后端是S3/MinIO对象存储，Registry可以用Deployment无痛水平扩展。

**【第二轮：小白追问——StorageClass和PVC陷阱】**

**小白**（在values.yaml上标注）："大师，我们上次的PG PVC被分配到HDD上了——K8s的StorageClass机制我不是很熟，能不能详细讲讲怎么确保PG一定跑在SSD上？"

**大师**："这个问题问到了K8s存储的'命门'。K8s的PVC机制是一道'自动门'——你提交PVC请求，StorageClass的provisioner自动创建PV。但如果你的集群有多个StorageClass（比如一个SSD的、一个HDD的），而你不在values.yaml中显式指定`storageClass`，调度器就会用默认的——通常就是那个便宜的HDD。"

"看一下你们的StorageClass列表："
```bash
$ kubectl get sc
NAME              PROVISIONER          RECLAIMPOLICY   VOLUMEBINDINGMODE
ssd-gp3 (default) ebs.csi.aws.com      Delete          WaitForFirstConsumer
hdd-sc1           ebs.csi.aws.com      Delete          WaitForFirstConsumer
```
"看到了吗？默认是`ssd-gp3`的话还好，但如果默认是`hdd-sc1`——你的PG和Registry直接'掉坑里'。"

"正确做法是在values.yaml中显式指定每个PVC的StorageClass："
```yaml
persistence:
  persistentVolumeClaim:
    registry:
      size: 500Gi
      storageClass: "ssd-gp3"   # 显式指定
    database:
      size: 50Gi
      storageClass: "ssd-gp3"   # PG必须有SSD
    redis:
      size: 20Gi
      storageClass: "ssd-gp3"
```

"还有一个更隐蔽的坑——`WaitForFirstConsumer`模式下，PVC创建后处于Pending状态是**正常的**，直到有Pod真正使用它才会创建PV并挂载。很多运维看到Pending就以为出错了，实际上只是还没绑定。"

**小白**："那如果用的是MinIO对象存储呢？是不是就不用PVC了？"

**大师**："对！这是Registry最推荐的架构——后端用S3/MinIO，Registry本身变为无状态，可以随意水平扩展。配置方式："
```yaml
registry:
  storage: "azure"  # 或 "s3"、"gcs"、"oss"、"swift"
  azure:
    accountname: "harborstorage"
    accountkey: "xxx"
    container: "registry"
    realm: "core.windows.net"
```

**【第三轮：小胖再问——升级时新旧Pod同时运行怎么办？】**

**小胖**："我们上次helm upgrade的时候，新旧Core Pod同时跑了15秒，数据库都死锁了——这怎么搞？"

**大师**："这是Harbor Helm Chart的一个'反直觉'设计——你需要在升级时手动控制Pod替换策略。几个关键的配置项："

```yaml
# ===== 升级策略——核心！ =====
core:
  replicas: 2
  strategy:
    type: Recreate          # 先停止旧Pod，再启动新Pod（有停机窗口但安全）
  # 或使用 RollingUpdate + maxSurge=0 (K8s 1.22+)
  
database:
  strategy:
    type: Recreate          # 数据库绝对不能新旧并存

registry:
  strategy:
    type: Recreate          # Registry也建议Recreate

# 最关键：数据库迁移由第一个Core Pod执行
core:
  configureUserSettings: true
  # 使用initContainer等待数据库就绪
```

"另外，Harbor v2.10+的Chart支持`migration.enabled=true`——在执行helm upgrade之前先跑一个单独的Migration Job，确保数据库schema先更新完再启动新Core。这样即使新旧Pod短暂共存，也不会出现schema不兼容的写冲突："

```bash
# 带migration的升级流程
helm upgrade harbor harbor/harbor \
  -f values.yaml -n harbor \
  --set migration.enabled=true \
  --wait --timeout 20m
```

**【第四轮：小白深挖——高可用方案】**

**小白**："大师，我们线上需要99.9%可用——如果要实现Harbor的跨可用区高可用，应该怎么设计？"

**大师**："这个问题没有一个'通用答案'——得看你愿意投入多少复杂度预算。我给你三个层级："

"**Level 1：单AZ高可用（投入低，适合中小团队）**"
- Core和Portal设`replicas:2` + PodAntiAffinity（同一AZ内分散到不同Node）
- 使用云厂商的托管PostgreSQL和Redis（RDS + ElastiCache）替代内置PG/Redis——云厂商负责HA
- N+1冗余——任意一个Node挂了，调度器自动重建Pod

"**Level 2：跨AZ高可用（投入中，适合大多数生产场景）**"
- 使用K8s的`topologySpreadConstraints`确保Pod分布在至少2个AZ
- 外部PG使用Multi-AZ部署（如RDS Multi-AZ）
- 外部Redis使用Sentinel（哨兵）模式，3个节点跨3个AZ
- Registry后端使用S3（对象存储本身就跨AZ）

```yaml
# values.yaml —— 跨AZ反亲和配置
affinity:
  podAntiAffinity:
    requiredDuringSchedulingIgnoredDuringExecution:
      - labelSelector:
          matchExpressions:
            - key: component
              operator: In
              values: [core, registry, portal]
        topologyKey: topology.kubernetes.io/zone
```

"**Level 3：多Region高可用（投入高，适合大型企业）**"
- 每个Region独立一套Harbor（包括PG + Redis + Registry）
- Region间通过Harbor的复制策略（Replication Rule）同步镜像
- 用DNS Geo-Routing让用户自动路由到最近的Region
- 需要一个'仲裁Region'来决定哪个Region的PG是Primary（避免脑裂）

**技术映射**：K8s的`topologySpreadConstraints`是在1.19版本GA的调度特性。`topologyKey: topology.kubernetes.io/zone`让调度器计算每个Zone中匹配label的Pod数量，尽量均匀分布。但注意这是soft constraint——如果你只有2个Zone但需要3副本，必然会有一个Zone多放一个Pod。

---

## 3 项目实战

### 环境要求

| 组件 | 版本/规格 | 用途 |
|------|----------|------|
| Kubernetes | v1.28+ | 容器编排平台 |
| Helm | v3.14+ | Chart部署工具 |
| Harbor Helm Chart | v1.15+ (对应Harbor v2.10) | Harbor的K8s部署模板 |
| kubectl | v1.28+ | K8s命令行工具 |
| Ingress Controller | Nginx Ingress v1.10+ / Traefik v3 | 外部流量入口 |
| cert-manager | v1.14+（可选） | 自动TLS证书管理 |
| 外部存储 | S3/MinIO（推荐）或 SSD StorageClass | Registry后端 |
| 外部数据库（推荐） | PostgreSQL 15+（云厂商托管） | 生产建议外部PG |

### 步骤一：准备K8s环境与前置依赖

**目标**：确认K8s集群满足Harbor部署的前置条件，安装必要的Controller。

```bash
# ===== 1. 检查K8s版本 =====
kubectl version --short
# 预期: Server Version: v1.28.x 或更高

# ===== 2. 检查可用StorageClass =====
kubectl get sc
# NAME                 PROVISIONER       RECLAIMPOLICY   VOLUMEBINDINGMODE
# ssd-gp3 (default)    ebs.csi.aws.com   Delete          WaitForFirstConsumer
# 如果没有SSD StorageClass，需要先创建：
# kubectl apply -f ssd-storageclass.yaml

# ===== 3. 创建Namespace =====
kubectl create namespace harbor
kubectl label namespace harbor \
    pod-security.kubernetes.io/enforce=privileged \
    name=harbor

# ===== 4. 检查Ingress Controller =====
kubectl get pods -A | grep ingress
# 预期看到 nginx-ingress-controller 或 traefik 的Pod Running
# 如果没有，先安装Nginx Ingress：
# helm repo add ingress-nginx https://kubernetes.github.io/ingress-nginx
# helm install ingress-nginx ingress-nginx/ingress-nginx \
#   --namespace ingress-nginx --create-namespace

# ===== 5. （可选）准备TLS证书 =====
# 方式A：自签名证书（测试用）
openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
  -keyout /tmp/harbor-tls.key -out /tmp/harbor-tls.crt \
  -subj "/CN=harbor.k8s.company.com" \
  -addext "subjectAltName=DNS:harbor.k8s.company.com"

kubectl create secret tls harbor-tls \
  --cert=/tmp/harbor-tls.crt \
  --key=/tmp/harbor-tls.key \
  -n harbor

# 方式B：cert-manager自动管理（生产推荐）
# 见步骤二中expose.tls.certSource=auto配置
```

### 步骤二：编写生产级 values.yaml

**目标**：创建一份可直接用于生产的Helm values文件，覆盖所有关键配置项。

```bash
# ===== 获取Chart所有可配置项的默认值作为参考 =====
helm show values harbor/harbor > /tmp/harbor-default-values.yaml
wc -l /tmp/harbor-default-values.yaml
# 大约800-1000行，以下为精选的生产配置
```

```yaml
# harbor-prod-values.yaml —— 生产级配置
# 适用场景: 200+ Node K8s集群, 5000+ 开发者, 日均10万+ pull

# ===== 外部暴露与TLS =====
expose:
  type: ingress
  tls:
    enabled: true
    certSource: secret        # 或 "auto" 使用cert-manager自动签发
    secret:
      secretName: "harbor-tls"
      notarySecretName: "harbor-notary-tls"
  ingress:
    hosts:
      core: harbor.k8s.company.com
      notary: notary.k8s.company.com
    controller: default       # nginx / traefik / gce
    className: "nginx"
    annotations:
      nginx.ingress.kubernetes.io/proxy-body-size: "0"     # 允许上传大镜像
      nginx.ingress.kubernetes.io/proxy-read-timeout: "600"
      nginx.ingress.kubernetes.io/proxy-send-timeout: "600"
      # 如果你在上游有WAF/CDN，需要下面这个：
      # nginx.ingress.kubernetes.io/whitelist-source-range: "10.0.0.0/8,172.16.0.0/12"

externalURL: https://harbor.k8s.company.com

# ===== 持久化存储 =====
persistence:
  enabled: true
  resourcePolicy: "keep"     # 删除Helm release时保留PVC
  persistentVolumeClaim:
    registry:
      existingClaim: ""      # 留空则自动创建PVC
      size: 500Gi
      storageClass: "ssd-gp3"
    database:
      size: 50Gi
      storageClass: "ssd-gp3"
    redis:
      size: 20Gi
      storageClass: "ssd-gp3"

# ===== Harbor Core（API核心） =====
core:
  replicas: 2                # 生产至少2副本
  strategy:
    type: Recreate           # 升级时先停旧再启新（安全）
  resources:
    requests:
      memory: 512Mi
      cpu: 500m
    limits:
      memory: 2Gi
      cpu: 2000m
  # 数据库连接池
  database:
    maxIdleConns: 50
    maxOpenConns: 200
  # Token配置
  tokenExpiration: 60        # 普通用户Token 60分钟
  robotTokenExpiration: 43200  # Robot Token 30天（CI用）
  # 使用initContainer等待数据库就绪
  initContainers:
    - name: wait-for-db
      image: busybox:1.36
      command:
        - sh
        - -c
        - |
          echo "Waiting for harbor-database:5432..."
          until nc -z harbor-database 5432; do
            echo "Database not ready yet, retrying..."
            sleep 3
          done
          echo "Database is ready!"
  # Pod反亲和——相同组件的Pod分散到不同Node
  affinity:
    podAntiAffinity:
      preferredDuringSchedulingIgnoredDuringExecution:
        - weight: 100
          podAffinityTerm:
            labelSelector:
              matchExpressions:
                - key: component
                  operator: In
                  values: ["core"]
            topologyKey: kubernetes.io/hostname

# ===== Harbor Portal（Web UI） =====
portal:
  replicas: 2
  resources:
    requests:
      memory: 128Mi
      cpu: 100m
    limits:
      memory: 512Mi
      cpu: 500m

# ===== Harbor Registry（镜像存储核心） =====
registry:
  replicas: 1                # 本地存储=1；S3对象存储可>1
  strategy:
    type: Recreate
  resources:
    requests:
      memory: 256Mi
      cpu: 250m
    limits:
      memory: 1Gi
      cpu: 1000m
  # 如果使用S3/MinIO作为后端（推荐生产使用）：
  # storage: "s3"
  # s3:
  #   region: us-east-1
  #   bucket: harbor-registry
  #   accesskey: AKIAXXXXX
  #   secretkey: xxxxxxxxxxxx

# ===== 内置PostgreSQL =====
database:
  type: internal             # 或 "external" 使用云厂商PG
  internal:
    resources:
      requests:
        memory: 512Mi
        cpu: 500m
      limits:
        memory: 4Gi
        cpu: 4000m
    # PG性能参数（通过init脚本设置）
    maxConnections: 300
    sharedBuffers: 4GB
    # 数据初始化参数
    initContainers:
      - name: tune-postgres
        image: busybox:1.36
        command: ['sh', '-c', 'echo "PostgreSQL tuning will be handled by configmap"']

# ===== 内置Redis =====
redis:
  type: internal
  internal:
    resources:
      requests:
        memory: 128Mi
        cpu: 100m
      limits:
        memory: 1Gi
        cpu: 500m

# ===== JobService（异步任务引擎） =====
jobservice:
  replicas: 1
  jobLoggers: ["file", "stdout"]
  maxJobWorkers: 20          # Worker线程数
  resources:
    requests:
      memory: 256Mi
      cpu: 200m
    limits:
      memory: 1Gi
      cpu: 1000m
  # 任务重试策略
  maxRetries: 3
  retryBackoff: 60s

# ===== Trivy漏洞扫描 =====
trivy:
  enabled: true
  debugMode: false
  resources:
    requests:
      memory: 512Mi
      cpu: 500m
    limits:
      memory: 2Gi
      cpu: 2000m
  # GitHub Token用于访问Trivy漏洞数据库（避免GitHub API限速）
  # gitHubToken: "ghp_xxxxxxxxxxxx"

# ===== 内部TLS（组件间通信加密） =====
internalTLS:
  enabled: true
  certSource: auto           # Chart自动生成内部证书

# ===== Metrics（监控集成） =====
metrics:
  enabled: true
  serviceMonitor:
    enabled: true            # 自动创建Prometheus ServiceMonitor
  core:
    path: /metrics
    port: 8001
  registry:
    path: /metrics
    port: 5001
  jobservice:
    path: /metrics
    port: 8080

# ===== 日志级别 =====
logLevel: info               # debug/info/warn/error

# ===== Harbor初始管理员密码 =====
harborAdminPassword: "K8sProd@2024!"

# ===== 迁移Job（升级时使用） =====
migration:
  enabled: true              # 升级时先跑迁移Job再启动新Pod
```

### 步骤三：安装Harbor

**目标**：使用Helm安装Harbor并监控部署过程。

```bash
# ===== 1. 添加Harbor Helm仓库 =====
helm repo add harbor https://helm.goharbor.io
helm repo update

# 查看可用版本
helm search repo harbor/harbor --versions | head -10
# NAME            CHART VERSION   APP VERSION
# harbor/harbor   1.15.0          2.10.0
# harbor/harbor   1.14.1          2.9.5

# ===== 2. 验证values.yaml语法 =====
python3 -c "
import yaml, sys
with open('harbor-prod-values.yaml') as f:
    cfg = yaml.safe_load(f)
    print('externalURL:', cfg.get('externalURL'))
    print('core.replicas:', cfg.get('core', {}).get('replicas'))
    print('metrics.enabled:', cfg.get('metrics', {}).get('enabled'))
    print('internalTLS.enabled:', cfg.get('internalTLS', {}).get('enabled'))
    print('OK: values.yaml parsed successfully')
"

# ===== 3. 预渲染Chart（不实际部署，仅验证模板） =====
helm template harbor harbor/harbor \
  -f harbor-prod-values.yaml \
  -n harbor > /tmp/harbor-rendered.yaml

# 检查渲染后的关键资源
grep -E "kind:|name:|replicas:" /tmp/harbor-rendered.yaml | head -30
# 预期看到Deployment、StatefulSet、PVC等资源的kind和name

# ===== 4. 执行安装 =====
helm install harbor harbor/harbor \
  -f harbor-prod-values.yaml \
  -n harbor \
  --timeout 20m \
  --wait \
  --wait-for-jobs

# 参数说明：
# --timeout 20m: 等待最长20分钟（大PVC创建可能较慢）
# --wait: 等待所有资源就绪后才返回
# --wait-for-jobs: 如果有migration job，等待它完成

# ===== 5. 监控部署过程 =====
# 在另一个终端中运行：
kubectl get pods -n harbor -w
# 预期Pod启动顺序：
# 1. harbor-database-0 (PG就绪) 
# 2. harbor-redis-0 (Redis就绪)
# 3. harbor-core-xxx (Core启动，等待initContainer完成)
# 4. harbor-registry-xxx
# 5. harbor-jobservice-xxx
# 6. harbor-portal-xxx
# 7. harbor-nginx-xxx
# 8. harbor-trivy-xxx

# 最终状态检查
kubectl get pods -n harbor
# NAME                                    READY   STATUS    RESTARTS   AGE
# harbor-core-7b8f9c6d5-abcde            1/1     Running   0          5m
# harbor-core-7b8f9c6d5-fghij            1/1     Running   0          5m
# harbor-database-0                       1/1     Running   0          5m
# harbor-jobservice-6c7d8e9f-xkz         1/1     Running   0          5m
# harbor-nginx-5d4e3f2a-xyz              1/1     Running   0          5m
# harbor-portal-4c5d6e7f-abcde           1/1     Running   0          5m
# harbor-redis-0                          1/1     Running   0          5m
# harbor-registry-8d7e6f5c-abc           1/1     Running   0          5m
# harbor-trivy-0                          1/1     Running   0          5m
```

```bash
# ===== 6. 验证功能 =====
# 登录Harbor
echo "K8sProd@2024!" | docker login harbor.k8s.company.com \
  --username admin --password-stdin
# 预期输出: Login Succeeded

# 推送测试镜像
docker pull alpine:3.19
docker tag alpine:3.19 harbor.k8s.company.com/library/alpine:test
docker push harbor.k8s.company.com/library/alpine:test
# 预期: push成功

# 验证Metrics端点
kubectl port-forward -n harbor svc/harbor-core 9090:9090 &
curl -s -u admin:K8sProd@2024! http://localhost:9090/metrics | head -10
# 预期看到Prometheus格式的指标

# 验证Ingress
curl -kI https://harbor.k8s.company.com
# 预期: HTTP/2 200
```

### 步骤四：升级与回滚

**目标**：安全地执行Harbor版本升级，掌握回滚操作。

```bash
# ===== 1. 查看当前部署的版本和revision =====
helm list -n harbor
# NAME    NAMESPACE   REVISION  UPDATED               STATUS    CHART         APP VERSION
# harbor  harbor      1         2024-01-15 10:30:00   deployed  harbor-1.14.1 2.9.5

helm history harbor -n harbor
# REVISION  UPDATED               STATUS      CHART         DESCRIPTION
# 1         2024-01-15 10:30:00   deployed    harbor-1.14.1 Install complete

# ===== 2. 查看新版本Changelog =====
helm repo update
helm search repo harbor/harbor --versions | head -3
# 假设新版本: harbor-1.15.0 → Harbor 2.10.0

# ===== 3. 获取新版本默认values，与当前values做diff =====
helm show values harbor/harbor --version 1.15.0 > /tmp/new-defaults.yaml

# 比较差异（重点关注新增/废弃的配置项）
diff <(python3 -c "
import yaml
def flatten(d, prefix=''):
    items = []
    if isinstance(d, dict):
        for k,v in d.items():
            items.extend(flatten(v, f'{prefix}{k}.'))
    else:
        items.append(f'{prefix[:-1]}={d}')
    return items
for k in sorted(flatten(yaml.safe_load(open('harbor-prod-values.yaml')))):
    print(k)
") <(python3 -c "
import yaml
def flatten(d, prefix=''):
    items = []
    if isinstance(d, dict):
        for k,v in d.items():
            items.extend(flatten(v, f'{prefix}{k}.'))
    else:
        items.append(f'{prefix[:-1]}={d}')
    return items
for k in sorted(flatten(yaml.safe_load(open('/tmp/new-defaults.yaml')))):
    print(k)
") | head -50

# ===== 4. 执行升级（务必在维护窗口内） =====
helm upgrade harbor harbor/harbor \
  -f harbor-prod-values.yaml \
  -n harbor \
  --version 1.15.0 \
  --set migration.enabled=true \
  --timeout 25m \
  --wait \
  --wait-for-jobs

# ===== 5. 如果升级失败——回滚 =====
helm rollback harbor 1 -n harbor --timeout 15m
# 回滚到revision 1（升级前的版本）

# 验证回滚后状态
kubectl get pods -n harbor
helm history harbor -n harbor
# 预期看到新revision 3 状态为 deployed（回滚也是一个新revision）
```

### 步骤五：K8s原生备份与恢复

**目标**：用K8s CronJob替代Docker exec方式的备份。

```bash
# ===== 1. 创建备份专用PVC =====
cat <<'EOF' | kubectl apply -f -
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: harbor-backup-pvc
  namespace: harbor
spec:
  accessModes:
    - ReadWriteOnce
  resources:
    requests:
      storage: 200Gi
  storageClassName: ssd-gp3
EOF

# ===== 2. 创建备份CronJob =====
cat <<'EOF' | kubectl apply -f -
apiVersion: batch/v1
kind: CronJob
metadata:
  name: harbor-db-backup
  namespace: harbor
spec:
  schedule: "0 2 * * *"          # 每天凌晨2:00
  successfulJobsHistoryLimit: 7  # 保留最近7次成功的Job记录
  failedJobsHistoryLimit: 3      # 保留最近3次失败的Job记录
  concurrencyPolicy: Forbid      # 禁止并发执行（防止两个备份同时写）
  jobTemplate:
    spec:
      template:
        spec:
          restartPolicy: OnFailure
          containers:
          - name: backup
            image: postgres:15-alpine
            command:
            - /bin/bash
            - -c
            - |
              set -e
              BACKUP_DATE=$(date +%Y%m%d_%H%M%S)
              BACKUP_FILE="/backups/harbor-db-${BACKUP_DATE}.sql.gz"
              
              echo "[$(date)] Starting Harbor database backup..."
              
              pg_dump \
                -h harbor-database \
                -U postgres \
                -d registry \
                --no-owner \
                --no-acl \
                | gzip > "${BACKUP_FILE}"
              
              BACKUP_SIZE=$(stat -c%s "${BACKUP_FILE}" 2>/dev/null || stat -f%z "${BACKUP_FILE}")
              echo "[$(date)] Backup completed: ${BACKUP_FILE} (size: ${BACKUP_SIZE} bytes)"
              
              # 清理7天前的旧备份
              find /backups -name "harbor-db-*.sql.gz" -mtime +7 -delete
              echo "[$(date)] Cleaned up backups older than 7 days"
              
              # 列出当前保留的备份
              echo "[$(date)] Current backups:"
              ls -lh /backups/
            env:
            - name: PGPASSWORD
              valueFrom:
                secretKeyRef:
                  name: harbor-database
                  key: password
            volumeMounts:
            - name: backup-storage
              mountPath: /backups
          volumes:
          - name: backup-storage
            persistentVolumeClaim:
              claimName: harbor-backup-pvc
EOF

# ===== 3. 手动触发一次备份（验证） =====
kubectl create job --from=cronjob/harbor-db-backup \
  harbor-db-backup-manual -n harbor

# 等待Job完成
kubectl wait --for=condition=complete job/harbor-db-backup-manual \
  -n harbor --timeout=300s

# 查看备份日志
kubectl logs job/harbor-db-backup-manual -n harbor
# 预期输出：
# [Tue Jan 16 02:00:01 UTC 2024] Starting Harbor database backup...
# [Tue Jan 16 02:00:15 UTC 2024] Backup completed: /backups/harbor-db-20240116_020001.sql.gz (size: 45234123 bytes)

# ===== 4. 验证备份文件可用性 =====
kubectl get pvc harbor-backup-pvc -n harbor
# 创建临时Pod读取备份文件
kubectl run backup-test --rm -it --restart=Never -n harbor \
  --image=busybox:1.36 \
  --overrides='{"spec":{"containers":[{"name":"test","image":"busybox:1.36","command":["sh","-c","ls -lh /backups/ && zcat /backups/harbor-db-*.sql.gz | head -20"],"volumeMounts":[{"name":"backup","mountPath":"/backups"}]}],"volumes":[{"name":"backup","persistentVolumeClaim":{"claimName":"harbor-backup-pvc"}}]}}'
# 预期看到SQL dump的前20行——确认备份内容有效
```

### 步骤六：配置HPA（水平自动扩缩容）

**目标**：为Harbor Core配置基于CPU的自动扩缩容。

```bash
# ===== 创建HPA =====
cat <<'EOF' | kubectl apply -f -
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: harbor-core-hpa
  namespace: harbor
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: harbor-core
  minReplicas: 2
  maxReplicas: 5
  metrics:
  - type: Resource
    resource:
      name: cpu
      target:
        type: Utilization
        averageUtilization: 70
  - type: Resource
    resource:
      name: memory
      target:
        type: Utilization
        averageUtilization: 80
  behavior:
    scaleDown:
      stabilizationWindowSeconds: 300   # 缩容前观察5分钟
      policies:
      - type: Percent
        value: 50
        periodSeconds: 60
    scaleUp:
      stabilizationWindowSeconds: 0     # 扩容立即执行
      policies:
      - type: Percent
        value: 100
        periodSeconds: 60
EOF

# 验证HPA状态
kubectl get hpa -n harbor
# NAME               REFERENCE                TARGETS          MINPODS   MAXPODS   REPLICAS
# harbor-core-hpa    Deployment/harbor-core   35%/70%, 60%/80% 2         5         2

# 模拟压力测试触发扩容（可选）
# for i in $(seq 1 500); do
#   curl -s -u admin:K8sProd@2024! \
#     https://harbor.k8s.company.com/api/v2.0/projects &
# done
# 观察 kubectl get hpa -n harbor -w 中 REPLICAS 增加
```

### 常见坑与解决方案

| # | 坑 | 根因 | 解决 |
|---|-----|------|------|
| 1 | `helm install` 后Core CrashLoopBackOff | PostgreSQL Pod尚未Ready时Core已启动——Helm的`--wait`只等Pod创建完成不等服务就绪。 | 添加initContainer等待数据库端口：`command: ['sh','-c','until nc -z harbor-database 5432; do sleep 2; done']`。或用`--wait-for-jobs`配合migration Job。 |
| 2 | Registry PVC永远Pending | PVC请求的StorageClass不存在或名称拼写错误（大小写敏感！）。如果StorageClass的`volumeBindingMode=WaitForFirstConsumer`，也表现为Pending——但这是正常状态。 | `kubectl get sc`确认StorageClass名称完全一致。`kubectl describe pvc -n harbor`查看Events中是否有`provisioner not found`错误。如果是WaitForFirstConsumer模式，确认有Pod在使用这个PVC。 |
| 3 | `helm upgrade`后新旧Core并存导致死锁 | `strategy.type=RollingUpdate`（默认）允许新旧Pod短暂共存。两个Core同时做schema migration → PG报`deadlock detected`。 | 设置`core.strategy.type=Recreate`。或升级前手动`kubectl scale deployment harbor-core --replicas=0 -n harbor`，升级后再恢复。 |
| 4 | `internalTLS.enabled=true` 后Core无法连接Registry | 内部TLS证书由Chart自动生成，但certificate的SAN（Subject Alternative Name）中不包含Service的clusterIP——Go的TLS客户端严格校验SAN。 | 检查Core日志：`kubectl logs deployment/harbor-core -n harbor | grep -i "cert\|tls\|x509"`。临时解决：设`internalTLS.enabled=false`后排查证书SAN配置。 |
| 5 | Ingress返回413 Request Entity Too Large | Nginx Ingress默认`proxy-body-size=1m`，镜像blob轻松超过这个限制。 | 在values.yaml中添加Ingress annotation：`nginx.ingress.kubernetes.io/proxy-body-size: "0"`（0=不限制）。 |
| 6 | `helm uninstall` 后PVC未被删除 | `persistence.resourcePolicy=keep`（推荐）防止误删。但如果你确实需要清理，需要手动删除PVC。 | `kubectl delete pvc -n harbor -l app=harbor`。注意：确认不再需要数据后再执行——PVC删除后数据不可恢复。 |

---

## 4 项目总结

### 4.1 K8s与Docker Compose部署全面对比

| 维度 | Docker Compose | Kubernetes (Helm) | 评价 |
|------|---------------|-------------------|------|
| 部署方式 | `docker compose up -d` | `helm install + kubectl apply` | Compose更简单 |
| 高可用 | 需手动Keepalived+双机热备 | Deployment `replicas:2` 天然HA | K8s压倒性优势 |
| 水平扩展 | 手动`docker compose scale` | HPA自动弹性 | K8s智能 |
| 滚动升级 | `docker compose pull && up -d`（有停机） | `helm upgrade`（Recreate策略：秒级中断） | 相当（配置得当的话） |
| 存储 | 宿主机目录绑定（bind mount） | PVC（支持50+种后端） | K8s灵活度远超Compose |
| 备份 | `docker exec pg_dump` + cron | CronJob + PVC | K8s可观测性更强 |
| 监控集成 | 需手动配置Prometheus target | `metrics.serviceMonitor.enabled`一键对接 | K8s生态完善 |
| 证书管理 | 手动openssl + 文件挂载 | cert-manager自动签发+续期 | K8s自动化 |
| 学习曲线 | ⭐⭐（30分钟上手） | ⭐⭐⭐（需要了解K8s+Helm） | Compose门槛低 |
| 推荐场景 | 小团队(<20人)/开发测试 | **生产环境/50+人团队** | 依据规模选择 |

### 4.2 适用场景

| 场景 | 说明 |
|------|------|
| 公司已有成熟K8s运维体系 | 统一用Helm管理所有中间件，Harbor不再需要单独维护VM |
| 需要99.9%+可用性 | K8s的自我修复（自愈）+多副本天然满足高可用要求 |
| 镜像规模快速增长（年增200%+） | HPA自动伸缩Core + S3无上限扩展Registry存储 |
| 多团队共享Harbor（>10个团队） | 按Namespace隔离项目 + ResourceQuota限制存储用量 |
| 需要合规审计 | CronJob备份 + cert-manager证书管理 = 审计员无话可说 |

**不适用场景**：
- 个人开发者或5人以下小团队——维护K8s集群本身的开销超过Harbor，直接用Docker Compose更划算
- 已有成熟的VMware/物理机运维体系且没有K8s迁移计划——不要为Harbor单独引入K8s

### 4.3 五项注意事项

1. **Harbor Helm Chart版本与Harbor版本严格绑定。** Chart v1.15.0对应Harbor v2.10.0，跨主版本号（如v1.x→v2.x）的Chart可能有breaking changes。升级前必须读Release Notes。
2. **自定义values后保留备份并做版本管理。** `harbor-prod-values.yaml`应该纳入Git仓库管理——每次修改都要commit，方便追踪历史。升级时基于新版Chart的默认values重新merge而非直接覆盖。
3. **database和redis的PVC扩容需要StorageClass支持。** 仅当StorageClass在创建时设置了`allowVolumeExpansion: true`，PVC才能在线扩容。否则需要手动迁移数据。
4. **Registry的副本数受存储后端限制。** 本地文件系统存储→`replicas:1`（强制）；S3/MinIO对象存储→可设置`replicas:2+`实现高可用。不要盲目设置`registry.replicas>1`而不改存储后端。
5. **内部TLS证书的有效期。** Chart自动生成的内部证书默认有效期1年。在`values.yaml`中设置`internalTLS.certValidity=87600h`（10年）或配置cert-manager自动续期。证书过期后组件间通信静默失败——非常难排查。

### 4.4 常见故障速查

| 故障现象 | 根因 | 快速定位命令 |
|----------|------|-------------|
| `docker login` 成功但 `docker push` 报 `unauthorized` | Core→Registry的Token签发失败（内部TLS或Secret不匹配） | `kubectl logs deployment/harbor-core -n harbor \| grep -i "token\|unauthorized"` |
| `helm upgrade` 卡住不返回 | StatefulSet的Pod在`Terminating`状态等待PVC卸载 | `kubectl get pods -n harbor \| grep Terminating` → 手动`kubectl delete pod --force` |
| Portal页面加载失败（空白页） | Portal无法访问Core API（Service名或端口配置错误） | `kubectl exec deployment/harbor-portal -n harbor -- wget -qO- http://harbor-core/api/v2.0/health` |

### 4.5 深度思考

1. **当前Harbor的Registry后端是本地PVC，只能单副本运行。如果迁移到S3/MinIO对象存储做后端，Registry能否安全地扩展到3副本？扩展后——两个副本同时收到同一个blob的push请求时，S3后端如何保证写入的幂等性？（提示：Registry的存储驱动在S3上使用DynamoDB或文件锁来实现分布式锁）**

2. **如果公司要求Harbor实现跨Region的"异地多活"——即用户无论从哪个Region pull都能得到完全一致的镜像，且一个Region故障不影响另一个Region。需要如何设计Harbor的复制拓扑？Replication策略应该用"push模式"还是"pull模式"？如何解决两个Region同时push同一个tag的冲突？（提示：研究Harbor的Replication Rule的`override`策略和`remote_registry`的部署模式）**

---

> 全书终——三章Harbor生产实战已全部完结。从性能调优到监控体系再到K8s落地，希望这份手册能陪伴你的Harbor生产之路。
