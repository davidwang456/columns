# 第20章：Kubernetes上部署Keycloak集群

## 1 项目背景

某互联网公司历时一年完成了全部微服务的Kubernetes容器化改造，50+服务在K8s上稳定运行。现在运维团队将目光转向了最后一块非K8s阵地——Keycloak。当前的Keycloak以裸机方式部署在一台物理服务器上，单节点运行，没有冗余，没有自动恢复。上周五凌晨三点，这台服役四年的服务器突发内存故障，认证服务全线中断，导致所有依赖SSO登录的业务系统——CRM、OA、支付后台——集体不可用。值班运维从被窝里爬起来SSH上去重启服务，前前后后折腾了38分钟。周一晨会上，CTO给了一句话："要么上K8s，要么换方案。"

运维团队很清楚K8s的能力清单：Pod崩溃后自动重启（自愈）、Node宕机后Pod自动漂移到健康节点（故障迁移）、CPU超过阈值自动扩容（HPA）、滚动更新实现零停机升级、Ingress Controller统一管理HTTPS证书。这些特性恰好能解决Keycloak当前的所有痛点。但问题很快就浮出水面：**Keycloak是有状态服务**——它依赖PostgreSQL数据库存储Realm配置和用户数据，依赖Infinispan缓存同步集群节点间的会话和登录状态，依赖持久化存储保存自定义主题文件。而K8s天生为无状态服务设计，Pod是"即用即抛"的，节点调度完全由调度器决定。StatefulSet和Deployment哪种更适合Keycloak？Infinispan缓存在Pod漂移后如何发现集群其他成员？TLS证书在K8s环境下怎么自动管理和续签？Persistent Volume应该选哪类存储——本地硬盘、NFS还是云厂商提供的块存储？

KUBE_PING发现机制是Keycloak在K8s场景下的关键差异化技术。传统裸机部署使用DNS_PING，节点通过DNS A记录发现集群同伴；而K8s环境下KUBE_PING直接查询Kubernetes API获取同类Pod的IP列表，利用Headless Service为每个Pod提供稳定的DNS名称（如`keycloak-0.keycloak-headless.default.svc.cluster.local`）。这套机制解决了Pod IP动态变化的发现难题，但也引入了新的复杂性：Pod需要有权限调用K8s API（ServiceAccount + RBAC），跨节点部署时Infinispan缓存的网络延迟可能上升。

> **核心矛盾**：Keycloak是"有状态公民"想搬进"无状态公寓"，需要特意为它改造基础设施——StatefulSet提供稳定标识，Headless Service提供网络路由，PersistentVolumeClaim提供持久存储，cert-manager提供自动TLS。

---

## 2 项目设计——剧本式交锋对话

**小胖**（端着咖啡，桌上的工位摆着一只小黄鸭）：大师，我想了一晚上终于理解了——把Keycloak从裸机搬到K8s，就像从独栋别墅搬到高层公寓！以前住别墅，房子大、独门独户、东西随便堆，但停电了自己修、水管漏了自己扛、小偷来了自己报警。现在搬进高层公寓，房间确实变小了，每层几十户共用电梯，但物业24小时值班——水管漏了物业修、停电了物业启动发电机、小偷来了保安巡逻。K8s不就是这样吗？Pod不能像裸机那样随便装插件和改内核参数，但有Scheduler、HPA、Ingress Controller这些"管家服务"兜底！

**大师**（放下手中的架构图）：小胖，你这比喻很有意思。但我问你一个问题：既然K8s会自动重启崩溃的Pod，为什么我们还要部署3个副本？一个Pod就够了，死了K8s自动拉起来，省下两份CPU和内存，何乐而不为？

**小胖**（愣住了，低头想了想）：嗯……好像是因为重启还要时间？Pod从Crash到Kubelet检测到、再到调度器选中新节点、拉镜像、启动JVM……这段时间服务是中断的？

**小白**（推了下眼镜）：不止是启动时间。一个Pod被调度到某个Node上运行，即使K8s快速重启它，如果这个Node本身宕机了呢？单副本的Pod需要等调度器发现Node失联（默认40秒的node-monitor-grace-period），然后标记NotReady（再等5分钟Pod的tolerations），最后才把Pod调度到健康节点。这一套流程走下来，五六分钟过去了。3个Pod分布在3个不同Node，任意一个Node宕机只需把该Node上的Pod调度到别处，其他两个Pod仍然在继续服务——**高可用不是靠"拉得快"而是靠"冗余"**。

**大师**（在白板上画了StatefulSet和Deployment的对比图）：小白点到了核心。再往下挖一层——Keycloak作为有状态服务，到底应该用StatefulSet还是Deployment？

**小白**（拿出一张表）：我查过文档，Deployment为每个Pod分配随机名称（如`keycloak-7d8f9b6c4-x2hk`），Pod重启后名称和IP全变。StatefulSet给每个Pod分配稳定序号（`keycloak-0`、`keycloak-1`、`keycloak-2`），配合Headless Service为每个Pod提供固定DNS名——`keycloak-0.keycloak-headless.namespace.svc.cluster.local`。Keycloak的Infinispan集群节点发现依赖的就是这些稳定的DNS标识。如果一个Infinispan节点加入集群时告诉同伴"我叫keycloak-1"，死亡重启后换了个名字"keycloak-7d8f-xxxxx"，同伴们根本不认识这个新名字，缓存同步就断了。**StatefulSet的稳定网络标识是Keycloak集群通信的前提条件**。

**大师**（在对比下写了总结）：精确。Deployment的设计哲学是"Pod是一群可互换的羊"，StatefulSet的设计哲学是"每个Pod都是有名字的个体"。Keycloak节点之间通过Infinispan的分布式缓存同步会话数据，这套缓存协议要求节点身份稳定——StatefulSet是唯一合适的选择。

> **大师技术映射**：Deployment → 共享单车，车坏了换一辆骑，没人关心车架号。StatefulSet → 私家车，每辆都有唯一车牌号，交警、保险、停车场都认这个号码。Keycloak集群节点 → 每个节点就是一辆需要唯一车牌的私家车。

---

**小胖**（翻着K8s文档）：那KUBE_PING和DNS_PING到底什么区别？我看配置里有个`KC_CACHE_STACK=kubernetes`，改了之后到底发生了什么？

**大师**：这是Keycloak K8s部署中最核心的网络发现机制，我给你画清楚。

**DNS_PING**（传统方式）：每个Keycloak节点启动时，用Java的DNS解析查询一个固定的DNS名称（比如`keycloak-cluster`），从返回的A记录列表中获取其他节点的IP地址。所有节点向这些IP发起TCP连接，尝试加入Infinispan集群。这个方案在裸机或Docker Compose下工作得很好——只要你在DNS Server或`/etc/hosts`中提前注册了所有节点IP。但到了K8s，Pod IP是动态分配的，每次重启都可能变，你无法预先写入DNS记录。

**KUBE_PING**（云原生方式）：Keycloak节点启动时不查DNS，而是通过K8s API查询所有带相同Label（如`app=keycloak`）的Pod。K8s API返回Pod列表，KUBE_PING从中提取每个Pod的IP和状态，然后发起集群加入请求。这意味着——**只要有Pod存在且能访问K8s API，就能自动发现集群成员，不需要任何静态DNS配置**。背后的技术实现依赖两件事：一是Pod中的ServiceAccount需要有`list`和`get` Pods的权限（通过RBAC赋予），二是JGroups协议栈使用`KUBE_PING`替代`TCPPING`。

**小白**：那Headless Service又是干什么的？我看StatefulSet里定义了一个`clusterIP: None`的Service。

**大师**：Headless Service（`clusterIP: None`）是StatefulSet的灵魂。普通Service提供一个虚拟Cluster IP，K8s kube-proxy通过iptables/IPVS将流量随机负载均衡到后端Pod。Headless Service没有Cluster IP，DNS查询它的名称时直接返回所有就绪Pod的IP地址。更关键的是——它为每个Pod创建独立DNS A记录：`<pod-name>.<service-name>.<namespace>.svc.cluster.local`。KUBE_PING通过K8s API拿到Pod列表，但集群内JGroups通信最终使用的是这些稳定的DNS名称，而不是Pod IP（IP可能变，但`keycloak-0.keycloak-headless`这个DNS名永远指向keycloak-0 Pod，不管它被调度到哪个Node）。

> **大师技术映射**：DNS_PING → 你在电话本里查朋友号码，朋友换号了你得手动更新电话本。KUBE_PING → 你在微信群（K8s API）里问"谁在线？"，群友在线状态实时反馈。Headless Service → 每个人有固定的群昵称（如`keycloak-0`），不管他们换了几次手机号（Pod IP），在群里都能被精准@到。

---

**小胖**（第二轮，歪着脑袋）：还有个问题！Ingress怎么配？我看YAML里塞了一堆`nginx.ingress.kubernetes.io`的annotation。TLS到底应该在Ingress层终止还是在Keycloak层处理？还有cert-manager这个"自动TLS管家"到底怎么运作的？

**大师**：TLS终止位置的选择核心取决于你对"内网可信"的定义。方案有两种：

**方案一：TLS终止于Ingress（推荐）**。外部流量通过HTTPS到达Ingress Controller，Nginx在这里解密TLS，然后把明文HTTP请求转发给Keycloak Pod。Keycloak本身不需要配置证书。优点是证书只在Ingress一处管理，cert-manager只需为Ingress申请证书即可。缺点是Ingress到Pod这段内网通信是HTTP明文——如果你的K8s集群内网络分区不可信（多租户共享集群），这有安全隐患。

**方案二：TLS透传到Keycloak（更高安全等级）**。Ingress不做TLS终止，HTTPS请求原封不动转发到Keycloak Pod，Keycloak自己配置证书。需要在Keycloak级别配置`http.tlsSecret`，并在Ingress上设置`nginx.ingress.kubernetes.io/ssl-passthrough: "true"`。优点是从外到内全程加密。缺点是证书管理分布在Ingress和Keycloak两处，cert-manager需要分别为它们签发证书，且ssl-passthrough下Ingress无法读取HTTP头做路由决策。

cert-manager的工作原理可以比作"门卫自动帮你续租房合同"：你创建一个`ClusterIssuer`或`Issuer`资源，声明使用Let's Encrypt ACME协议。当你为Ingress添加annotation `cert-manager.io/cluster-issuer: letsencrypt-prod`时，cert-manager Controller检测到新Ingress，自动执行三步——(1)向Let's Encrypt的ACME服务器发起证书申请，(2)在指定的域名路径下创建临时Pod完成HTTP01验证（证明你确实控制这个域名），(3)验证通过后下载证书并存入指定的K8s Secret（如`keycloak-tls`）。证书到期前30天，cert-manager自动发起续签流程，全程零人工干预。

**小白**（追问）：那Ingress和Gateway API呢？K8s社区现在推Gateway API，我们该不该直接上Gateway API？

**大师**：Gateway API是K8s SIG-Network推出的新一代流量管理标准，相比Ingress有三个核心改进——更丰富的路由表达能力（HTTP路由、TCP/UDP路由、TLS路由）、角色分离设计（基础设施团队管理GatewayClass和Gateway，应用团队管理HTTPRoute）、更强的跨命名空间能力。但目前（2025年）Gateway API的生态成熟度仍不如Ingress——cert-manager对Gateway API的自动TLS支持虽已有实验性实现，但社区主流方案仍在Ingress。对于Keycloak这种生产级认证服务，**当前建议用Ingress（nginx-ingress）配cert-manager**，待Gateway API的证书自动化等配套生态成熟后再迁移不迟。

> **大师技术映射**：TLS终止于Ingress → 大厦前台保安拆包安检（解密），然后人带着东西进电梯（明文内网）。TLS透传到Keycloak → 包裹全程密封，只有收件人（Keycloak）有开包钥匙。cert-manager → 不用你记护照到期日，出入境管理局自动上门帮你续签。

---

## 3 项目实战

### 环境准备

- **K8s集群**：minikube / k3d / Kind，推荐Kind，资源轻量且支持模拟多节点
- **kubectl**：v1.28+
- **Helm**：v3.12+（可选，用于安装cert-manager）
- **域名**：配置本地`/etc/hosts`或DNS将`auth.company.com`指向Ingress IP

```bash
# 使用Kind创建3节点K8s集群（模拟多Node场景）
kind create cluster --config - <<EOF
kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
nodes:
  - role: control-plane
  - role: worker
  - role: worker
  - role: worker
EOF

# 安装nginx-ingress（Kind需要额外配置）
kubectl apply -f https://raw.githubusercontent.com/kubernetes/ingress-nginx/main/deploy/static/provider/kind/deploy.yaml

# 等待Ingress就绪
kubectl wait --namespace ingress-nginx --for=condition=ready pod \
  --selector=app.kubernetes.io/component=controller --timeout=180s
```

---

### 步骤1：安装cert-manager（自动TLS证书管理）

目标：部署cert-manager，配置Let's Encrypt ClusterIssuer，使Keycloak的TLS证书自动签发与续签。

```bash
# 安装cert-manager CRD和Controller
kubectl apply -f https://github.com/cert-manager/cert-manager/releases/download/v1.15.0/cert-manager.yaml

# 验证运行状态
kubectl get pods -n cert-manager
```

```yaml
# cluster-issuer.yaml
apiVersion: cert-manager.io/v1
kind: ClusterIssuer
metadata:
  name: letsencrypt-staging          # 先试用staging环境，避免Let's Encrypt生产环境限频
spec:
  acme:
    server: https://acme-staging-v02.api.letsencrypt.org/directory
    email: admin@company.com
    privateKeySecretRef:
      name: letsencrypt-staging
    solvers:
      - http01:
          ingress:
            className: nginx
```

> **坑点警告**：Let's Encrypt生产环境有严格的速率限制——同一域名每周最多申请5次。调试阶段务必用staging环境，验证通过后再切换到`https://acme-v02.api.letsencrypt.org/directory`。否则域名被限频后要等一周才能再次申请。

---

### 步骤2：安装Keycloak Operator（推荐方式）

目标：通过Operator（CRD声明式管理）部署Keycloak，比手写StatefulSet更简洁且覆盖更多运维场景。

```bash
# 创建命名空间
kubectl create namespace keycloak

# 安装CRD定义（Keycloak自定义资源）
kubectl apply -f https://raw.githubusercontent.com/keycloak/keycloak-k8s-resources/26.1.0/kubernetes/keycloaks.k8s.keycloak.org-v1.yml

# 安装Operator Deployment + RBAC
kubectl apply -f https://raw.githubusercontent.com/keycloak/keycloak-k8s-resources/26.1.0/kubernetes/keycloak-operator.yml

# 验证Operator Pod运行
kubectl get pods -n keycloak
# 期望输出：keycloak-operator-xxxxxxxx-xxxxx  1/1  Running
```

> **架构说明**：Operator工作在keycloak命名空间，通过Watch机制监听`Keycloak` CR的变更事件。当你创建/修改一个Keycloak CR，Operator的Reconcile循环会对比期望状态和当前状态，自动创建/更新StatefulSet、Service、Ingress、ConfigMap等子资源。

---

### 步骤3：创建数据库Secret和Keycloak CR

```yaml
# keycloak-db-secret.yaml
apiVersion: v1
kind: Secret
metadata:
  name: keycloak-db-secret
  namespace: keycloak
type: Opaque
stringData:
  username: keycloak
  password: "Str0ngP@ssw0rd!"
---
# keycloak-cluster.yaml —— Keycloak CR，Operator会自动生成StatefulSet+Service+Ingress
apiVersion: k8s.keycloak.org/v2alpha1
kind: Keycloak
metadata:
  name: keycloak-cluster
  namespace: keycloak
  labels:
    app: keycloak
spec:
  instances: 3                       # 3副本高可用
  db:
    vendor: postgres
    host: postgres-keycloak          # 数据库Service名
    database: keycloak
    usernameSecret:
      name: keycloak-db-secret
      key: username
    passwordSecret:
      name: keycloak-db-secret
      key: password
  hostname:
    hostname: auth.company.com
  ingress:
    enabled: true
    className: nginx
    annotations:
      cert-manager.io/cluster-issuer: letsencrypt-staging    # 自动TLS
      nginx.ingress.kubernetes.io/proxy-buffer-size: 128k     # Keycloak响应头较大
      nginx.ingress.kubernetes.io/proxy-buffers-number: "4"
    tls:
      - hosts:
          - auth.company.com
        secretName: keycloak-tls
  http:
    tlsSecret: keycloak-tls           # 如果TLS终止于Ingress，此字段可选
  additionalOptions:
    - name: health-enabled
      value: "true"
    - name: metrics-enabled
      value: "true"
    - name: log-console-output
      value: json
    - name: cache
      value: ispn
    - name: cache-stack
      value: kubernetes               # 启用KUBE_PING
```

```bash
# 先创建PostgreSQL（简化演示用K8s部署）
kubectl apply -f - <<EOF
apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: postgres-keycloak
  namespace: keycloak
spec:
  serviceName: postgres-keycloak
  replicas: 1
  selector:
    matchLabels:
      app: postgres-keycloak
  template:
    metadata:
      labels:
        app: postgres-keycloak
    spec:
      containers:
        - name: postgres
          image: postgres:16
          env:
            - name: POSTGRES_DB
              value: keycloak
            - name: POSTGRES_USER
              valueFrom:
                secretKeyRef:
                  name: keycloak-db-secret
                  key: username
            - name: POSTGRES_PASSWORD
              valueFrom:
                secretKeyRef:
                  name: keycloak-db-secret
                  key: password
          ports:
            - containerPort: 5432
          volumeMounts:
            - name: pgdata
              mountPath: /var/lib/postgresql/data
  volumeClaimTemplates:
    - metadata:
        name: pgdata
      spec:
        accessModes: ["ReadWriteOnce"]
        resources:
          requests:
            storage: 10Gi
---
apiVersion: v1
kind: Service
metadata:
  name: postgres-keycloak
  namespace: keycloak
spec:
  selector:
    app: postgres-keycloak
  ports:
    - port: 5432
EOF

# 等待数据库就绪
kubectl wait --for=condition=ready pod -l app=postgres-keycloak -n keycloak --timeout=120s

# 部署Keycloak CR
kubectl apply -f keycloak-db-secret.yaml
kubectl apply -f keycloak-cluster.yaml

# 观察Pod启动过程
kubectl get pods -n keycloak -w
```

---

### 步骤4：手动YAML方式部署（备选，深入理解StatefulSet）

目标：在没有Operator时，手写完整的StatefulSet+Headless Service+Ingress，理解底层机制。

```yaml
# headless-service.yaml —— StatefulSet必须有对应的Headless Service
apiVersion: v1
kind: Service
metadata:
  name: keycloak-headless
  namespace: keycloak
  labels:
    app: keycloak
spec:
  clusterIP: None                     # 关键：声明为Headless Service
  selector:
    app: keycloak
  ports:
    - port: 8080
      name: http
    - port: 7800
      name: jgroups-tcp              # Infinispan集群通信端口
    - port: 57800
      name: jgroups-tcp-fd          # Infinispan故障检测端口
---
# keycloak-statefulset.yaml —— 3副本、反亲和性、KUBE_PING
apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: keycloak
  namespace: keycloak
  labels:
    app: keycloak
spec:
  serviceName: keycloak-headless     # 必须与Headless Service的name一致
  replicas: 3
  podManagementPolicy: Parallel      # 并行启动3个Pod，加速启动
  updateStrategy:
    type: RollingUpdate
    rollingUpdate:
      partition: 0                   # 0=全部更新；设为3=暂停更新
  selector:
    matchLabels:
      app: keycloak
  template:
    metadata:
      labels:
        app: keycloak
    spec:
      serviceAccountName: keycloak-sa
      # Pod反亲和性：确保Pod分布在不同Node（物理机/可用区）
      affinity:
        podAntiAffinity:
          requiredDuringSchedulingIgnoredDuringExecution:
            - labelSelector:
                matchExpressions:
                  - key: app
                    operator: In
                    values:
                      - keycloak
              topologyKey: kubernetes.io/hostname
      containers:
        - name: keycloak
          image: quay.io/keycloak/keycloak:26.1
          command: ["/opt/keycloak/bin/kc.sh"]
          args: ["start", "--optimized", "--verbose"]
          env:
            - name: KC_DB
              value: postgres
            - name: KC_DB_URL_HOST
              value: postgres-keycloak
            - name: KC_DB_URL_DATABASE
              value: keycloak
            - name: KC_DB_USERNAME
              valueFrom:
                secretKeyRef:
                  name: keycloak-db-secret
                  key: username
            - name: KC_DB_PASSWORD
              valueFrom:
                secretKeyRef:
                  name: keycloak-db-secret
                  key: password
            - name: KC_HOSTNAME
              value: auth.company.com
            - name: KC_CACHE
              value: ispn
            - name: KC_CACHE_STACK
              value: kubernetes          # KUBE_PING
            - name: JAVA_OPTS_APPEND
              value: "-XX:+UseZGC -XX:MaxRAMPercentage=75.0"
            - name: JGROUPS_DISCOVERY_EXTERNAL_IP
              value: "false"             # K8s内不需要外部IP发现
          ports:
            - name: http
              containerPort: 8080
            - name: jgroups
              containerPort: 7800
            - name: jgroups-fd
              containerPort: 57800
          # 健康检查探针
          startupProbe:
            httpGet:
              path: /health/started
              port: http
            initialDelaySeconds: 30
            failureThreshold: 60         # Keycloak启动慢，允许5分钟（60*5s）
            periodSeconds: 5
          livenessProbe:
            httpGet:
              path: /health/live
              port: http
            initialDelaySeconds: 30
            periodSeconds: 10
            failureThreshold: 3
          readinessProbe:
            httpGet:
              path: /health/ready
              port: http
            initialDelaySeconds: 20
            periodSeconds: 5
            failureThreshold: 3
          resources:
            requests:
              memory: "512Mi"
              cpu: "250m"
            limits:
              memory: "2Gi"
              cpu: "2000m"
          volumeMounts:
            - name: keycloak-themes
              mountPath: /opt/keycloak/themes/my-brand
      volumes:
        - name: keycloak-themes
          configMap:
            name: keycloak-theme-config
---
# service-account.yaml —— KUBE_PING必需的RBAC权限
apiVersion: v1
kind: ServiceAccount
metadata:
  name: keycloak-sa
  namespace: keycloak
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: keycloak-pod-reader
rules:
  - apiGroups: [""]
    resources: ["pods"]
    verbs: ["get", "list"]              # KUBE_PING只需要这两个权限
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: keycloak-sa-pod-reader
subjects:
  - kind: ServiceAccount
    name: keycloak-sa
    namespace: keycloak
roleRef:
  kind: ClusterRole
  name: keycloak-pod-reader
  apiGroup: rbac.authorization.k8s.io
---
# ingress.yaml —— 会话保持配置
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: keycloak
  namespace: keycloak
  annotations:
    cert-manager.io/cluster-issuer: letsencrypt-staging
    nginx.ingress.kubernetes.io/ssl-redirect: "true"
    nginx.ingress.kubernetes.io/proxy-buffer-size: 128k
    nginx.ingress.kubernetes.io/proxy-buffers-number: "4"
    # 会话亲和性——将同一用户的请求路由到同一Pod（Cluster内部已有Infinispan同步，该配置为性能优化）
    nginx.ingress.kubernetes.io/affinity: "cookie"
    nginx.ingress.kubernetes.io/session-cookie-name: "AUTH_SESSION_ID"
    nginx.ingress.kubernetes.io/session-cookie-path: "/"
    nginx.ingress.kubernetes.io/session-cookie-max-age: "3600"
spec:
  tls:
    - hosts:
        - auth.company.com
      secretName: keycloak-tls
  rules:
    - host: auth.company.com
      http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                name: keycloak-headless   # 指向Headless Service
                port:
                  number: 8080
```

> **坑点警告**：Ingress的`backend.service.name`如果指向普通Service，流量会被kube-proxy随机分发到任一Pod——可能会出现用户登录时code发到Pod A但token请求发到Pod B的情况。Infinispan分布式缓存保证了跨Pod的会话一致性，因此这个随机分发在Keycloak集群中是安全的。但如果你的Keycloak配置了`user-sessions-cache-size`且使用本地缓存模式而非分布式缓存，则必须配合`nginx.ingress.kubernetes.io/affinity: "cookie"`启用会话亲和性。

---

### 步骤5：部署并验证集群

```bash
# 部署所有YAML
kubectl apply -f k8s/

# 观察Pod依次启动
kubectl get pods -n keycloak -w -o wide
# 期望输出：
# NAME        READY  STATUS    NODE
# keycloak-0  1/1    Running   kind-worker
# keycloak-1  1/1    Running   kind-worker2
# keycloak-2  1/1    Running   kind-worker3

# 查看集群成员发现日志——确认KUBE_PING生效
kubectl logs keycloak-0 -n keycloak | grep -i "Received new cluster view"
# 期望输出：Received new cluster view for channel keycloak: [keycloak-0|2, keycloak-1|3, keycloak-2|4]

# 验证健康端点
kubectl exec -it keycloak-0 -n keycloak -- curl -s http://localhost:8080/health/ready
# 期望输出：{"status":"UP","checks":[...]}

# 验证cert-manager已签发证书
kubectl get certificate -n keycloak
# 期望输出：NAME           READY   SECRET         AGE
#           keycloak-tls   True    keycloak-tls   5m

# 通过Ingress访问（本地/etc/hosts已配置auth.company.com -> Ingress IP）
curl -k https://auth.company.com/health/ready
```

**高可用验证**——删除一个Pod，观察自动恢复和服务连续性：

```bash
# 窗口1：持续发送健康检查请求
while true; do
  curl -s -o /dev/null -w "%{http_code} %{time_total}s\n" https://auth.company.com/health/ready -k
  sleep 2
done

# 窗口2：删除一个Pod模拟故障
kubectl delete pod keycloak-1 -n keycloak

# 观察：健康检查始终返回200（另外两个Pod继续工作）
# Pod自动重建后自动重新加入Infinispan集群
kubectl logs keycloak-1 -n keycloak | grep "Received new cluster view"
```

---

### 步骤6：HPA自动扩缩容（实验性配置）

> **注意**：Keycloak作为有状态服务，HPA水平扩缩容存在局限性——新Pod加入后需要重新建立Infinispan分布式缓存拓扑，期间可能有短暂的服务降级。生产环境建议优先垂直扩缩容（调整Resource Limit），HPA作为应对突发流量的补充手段。

```yaml
# keycloak-hpa.yaml
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: keycloak-hpa
  namespace: keycloak
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: StatefulSet
    name: keycloak
  minReplicas: 3
  maxReplicas: 10
  metrics:
    - type: Resource
      resource:
        name: cpu
        target:
          type: Utilization
          averageUtilization: 70
  behavior:
    scaleDown:
      stabilizationWindowSeconds: 300    # 缩容前观察5分钟，避免频繁伸缩
    scaleUp:
      stabilizationWindowSeconds: 60
```

---

### 可能遇到的坑

| 坑 | 现象 | 根因 | 解决方案 |
|---|------|------|---------|
| KUBE_PING无法发现节点 | 每个Pod日志显示`Received new cluster view with 1 member` | Pod的ServiceAccount没有K8s API的`get/list pods`权限 | 检查ClusterRole和ClusterRoleBinding是否已创建且正确绑定 |
| StatefulSet Pod启动卡在Pending | `kubectl describe pod keycloak-0`显示`0/3 nodes available` | PodAntiAffinity要求每个Pod在不同Node，但集群Node数少于副本数 | 降低副本数或增加Worker节点 |
| cert-manager证书一直未Ready | `kubectl describe certificate`显示`Waiting for HTTP-01 challenge` | Ingress未正确暴露或域名DNS未指向Ingress IP | 检查Ingress的`status.loadBalancer.ingress`是否有IP，确认外部可访问 |
| 滚动升级后部分Pod无法加入集群 | 新Pod日志报`Timeout waiting for cluster view` | `updateStrategy.rollingUpdate.partition`未正确配置导致新旧Pod JGroups协议栈不兼容 | 升级前将`partition`设为`replicas`暂停自动升级，手动验证后再调回0 |
| JGroups网络通信超时 | 节点间Infinispan同步失败 | 网络插件(Calico/Flannel/Cilium)没有开放7800/57800端口 | 检查NetworkPolicy，确保允许TCP 7800和57800端口的Pod间通信 |

---

## 4 项目总结

### 优点 & 缺点：K8s部署 vs 裸机部署 vs Docker Compose

| 维度 | K8s部署（StatefulSet+Operator） | 裸机部署 | Docker Compose |
|------|-------------------------------|---------|---------------|
| 高可用 | 天然支持：自动故障转移、多副本 | 需自建负载均衡+主备切换脚本 | 单机多容器，宿主机宕机全挂 |
| 扩缩容 | 声明式HPA弹性伸缩 | 手动添加服务器+重配DNS | 修改`docker-compose.yml`+重启 |
| 滚动更新 | 零停机，可金丝雀/蓝绿部署 | 停服→升级→启动，有窗口期 | `docker-compose up -d`逐个替换 |
| TLS证书 | cert-manager自动签发+续签 | 手动申请+cron任务续签 | 不支持自动化 |
| 存储管理 | PVC+StorageClass动态供给 | 本地磁盘，无抽象层 | 本地Volume绑定 |
| 运维复杂度 | 需要K8s运维经验，YAML配置多 | 依赖Linux运维经验 | 最简单，适合开发测试 |
| 资源利用率 | Pod按Request/Limit弹性分配，装箱率高 | 资源独占，利用率低 | 宿主机资源硬隔离不足 |

### 适用场景

- **云原生企业**：组织已全面K8s化，拥有成熟的K8s运维团队和CI/CD流水线
- **弹性伸缩需求**：认证请求量波动大（如电商大促、节假日高峰），需要根据CPU/内存自动扩缩
- **零停机升级**：对SLA要求高（99.9%+），不能接受维护窗口中断认证服务
- **多环境一致性**：开发/测试/预发/生产环境通过同一批K8s YAML部署，消除环境差异

**不适用场景**：
- 团队无K8s运维经验且没有上K8s计划——裸机部署的复杂度远低于K8s学习成本
- 极小规模部署（单租户、日活跃用户<1000）——K8s的资源开销（etcd、kubelet、CNI）远大于Keycloak自身

### 注意事项

1. **Resource Request/Limit必须设置**：不设Request会导致调度器将多个Pod堆在同一节点上——PodAntiAffinity配置将失效。不设Limit可能导致一个Pod OOM后连锁拖垮同节点的其他Pod。
2. **PodDisruptionBudget（PDB）**：配置`minAvailable: 2`确保驱逐操作（如Node维护）不会同时驱逐所有Keycloak Pod，导致服务中断。
3. **数据库高可用**：Keycloak的有状态性根源于数据库。如果PostgreSQL是单点，再多Keycloak副本也无意义——推荐PostgreSQL Operator（如CloudNativePG）或托管数据库（RDS/Cloud SQL）实现数据库层高可用。
4. **备份策略**：定期备份Keycloak数据库（Realm配置、用户数据）和PVC中的持久化数据（主题文件）。Operator方式下数据库通常由外部管理，备份策略与人有关而非K8s有关。
5. **监控告警**：集成Prometheus+Grafana监控Keycloak指标（/metrics端点），配置JVM堆内存使用率、GC频率、登录成功/失败率、Infinispan缓存命中率的告警规则。

### 常见踩坑经验

**故障案例一：Headless Service命名规则错误**。某团队将StatefulSet的`serviceName`设为`keycloak-svc`但实际创建的Headless Service名为`keycloak-headless`——名称不匹配导致K8s不会为Pod创建独立DNS记录，Infinispan集群发现失败，每个Pod启动后认为自己是集群唯一成员。修复后还发现Service的`selector`与Pod的`labels`不匹配，Pod永远不被选中。

**故障案例二：RBAC权限遗漏**。Operator自动为Keycloak Pod创建了ServiceAccount，但手写YAML时遗漏了ClusterRole。Pod日志不断报`403 Forbidden`访问K8s API导致KUBE_PING回退到静态列表模式。排查花了两小时，最后在`kubectl auth can-i list pods --as=system:serviceaccount:keycloak:keycloak-sa`中发现权限缺失。

**故障案例三：cert-manager ACME HTTP01验证失败**。团队在Keycloak Ingress中配置了TLS和cert-manager annotation，但Ingress Controller暴露的是NodePort而非LoadBalancer，外部Let's Encrypt服务器无法访问`http://auth.company.com/.well-known/acme-challenge/xxx`路径。解决方式：在路由层配置端口转发或使用DNS01验证方式（需要DNS服务商的API密钥）。

---

### 思考题

1. **Keycloak作为有状态服务，在K8s上如何实现跨数据中心的更好方案？**单个K8s集群即使多副本，仍依赖同一套数据库。如果数据中心整体故障（火灾、网络中断）怎么办？请思考跨K8s集群的Keycloak高可用方案——例如多集群部署+数据库主主复制/跨区域只读副本+全局DNS流量调度（GeoDNS），并分析Infinispan跨数据中心缓存同步的网络延迟和冲突解决机制。

2. **如果Keycloak需要存储用户头像等二进制数据，在K8s上应该如何设计存储方案？**Keycloak默认不存储二进制文件，但通过自定义User Storage Provider可以扩展。请设计一套方案——利用K8s的PersistentVolumeClaim挂载对象存储（MinIO/Ceph RGW）、通过ReadWriteMany模式让多个Keycloak Pod共享访问同一个存储卷，并评估此方案在性能、一致性和备份恢复方面的表现。如果选择外部对象存储（S3/OSS），Keycloak如何在不修改核心代码的前提下通过SPI扩展实现用户头像的上传和读取？
