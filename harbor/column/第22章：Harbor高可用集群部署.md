# 第22章：Harbor 高可用集群部署

## 1 项目背景

某中型电商公司的Harbor单点部署已运行18个月，支撑着200+微服务的镜像存储与分发。2024年3月的一个周一早高峰，Harbor主机因内存故障宕机，引发了一场长达4小时的灾难性停机事故。这促使公司下定决心对Harbor进行高可用改造。

**痛点一：单机故障导致全站瘫痪。** 故障发生在上午9:15——正是业务高峰。Harbor宕机后，所有CI/CD流水线push镜像失败，Kubernetes集群新Pod因无法拉取镜像全部进入`ImagePullBackOff`状态，正在进行的200+微服务滚动更新全部卡死。更糟的是，数据库连接池耗尽导致PostgreSQL需要从WAL日志恢复，实际停机时间远超硬件修复时间。仅这一次事故，直接业务损失约50万元，间接影响了当天约300万元的GMV。

**痛点二：垂直扩展已达物理上限。** Harbor所在物理机已升级到64核CPU/256GB内存——这是当前数据中心能提供的最高配置。促销期间，并发docker pull峰值达到3000QPS，Core服务的CPU使用率稳定在85%以上。运维团队监测到PostgreSQL的`max_connections`（默认100）经常被打满。再增加业务量，Core进程面临OOM风险，而单机PostgreSQL的写吞吐也已接近磁盘IOPS极限。

**痛点三：维护窗口难以协调。** Harbor操作系统安全补丁需要重启服务器——每次重启约5分钟。业务部门合同承诺"Harbor可用率99.99%"，意味着全年停机时间不能超过52分钟。仅靠单机部署，即便所有重启操作都在凌晨执行，也无法满足这个SLA。更麻烦的是，数据库大版本升级（如PostgreSQL 13→15）需要数小时停机——这在单机架构下根本无法执行。

**痛点四：灾备能力为零。** 当前Harbor部署在单一机房的单一机架上。2023年机房空调故障导致温度升至45°C时，运维只能紧急关机——如果当时Harbor的存储也损坏了，所有镜像数据将永久丢失。公司审计部门明确要求"核心制品仓库必须具备跨机房容灾能力"。

Harbor的高可用部署不是一个简单的"多部署几台+负载均衡"，而是涉及四个层面的系统性改造：**接入层（Nginx/Portal多实例+LB）、业务层（Core多实例无状态化）、数据层（PostgreSQL主从流复制）、存储层（Registry共享存储/对象存储）**，以及缓存层（Redis Sentinel/Cluster）。每一层都有不同的高可用策略和运维陷阱。

---

## 2 项目设计——剧本式交锋对话

**场景：运维部会议室，技术评审会上讨论Harbor HA改造方案。白板上画满了架构图。**

**小胖**（运维工程师，爱用生活类比）："高可用不就是多部署几台机器，前面挂个Nginx负载均衡嘛？跟我们外卖App的服务器集群有啥区别？"

**大师**（架构师，15年基础设施经验）："小胖，你这个类比方向对，但Harbor比外卖App复杂。Harbor有9个组件——但能直接水平扩展的只有4个：Nginx、Core、Portal、JobService Worker。其他5个组件——PostgreSQL、Redis、Registry、Registryctl、Trivy Adapter——要么需要特殊的主从架构，要么有状态限制，不能简单地多跑几个实例就完事。"

"打个比方：外卖App的后端服务像快餐店的收银员——多雇几个人就行，大家干一样的活。但Harbor的PostgreSQL像快餐店的中央厨房——你不能开两个中央厨房同时做同一道菜，会出现两份不同的'宫保鸡丁'，这就是数据不一致。所以中央厨房只能有一个主厨（Primary），其他人当备胎（Standby），主厨挂了备胎顶上。"

**小胖**："那Redis呢？Redis不是有Cluster模式吗，多节点分片不就行了？"

**大师**："Redis Cluster主要解决数据分片问题——数据量大到单机存不下时才用。Harbor用Redis主要是做Session存储和任务队列缓存，数据量不大（通常<1GB），不需要分片。但需要高可用——所以用Redis Sentinel模式：一个主节点负责读写，两个从节点同步数据，三个Sentinel进程监控主节点健康状态。主节点挂了，Sentinel自动把从节点提升为主。"

"但这里有个坑——Sentinel切换需要5到30秒。这段时间内，Core无法读写Session，所有已登录用户可能被强制登出。就像你正在用App下单，突然被踢出登录页面——体验很差。"

**小白**（高级开发，喜欢追问原理和边缘情况）："大师，我有个疑问。Core本身是无状态的——所有Core实例共享同一个PostgreSQL和Redis。但Harbor的Token签发依赖私钥，所有Core实例需要用同一份私钥签发Token。这份私钥怎么安全共享？如果存在共享存储上，共享存储本身不又成了单点吗？"

**大师**（赞许地点头）："问得好，这是个经典问题。有三种方案：

1. **共享存储方案**：私钥存在NFS上，所有Core实例挂载同一个目录。简单但NFS本身可能成为单点。
2. **配置管理方案**：通过Kubernetes Secret或HashiCorp Vault注入私钥。每个Core实例启动时从外部获取同一份私钥——这是K8s部署的推荐做法。
3. **证书方案**：不使用共享私钥，而是引入PKI——每个Core实例有自己的证书，签发Token时使用各自证书，验证方信任CA即可。这种方案最安全但实现最复杂。

大多数团队选择方案2——简单且安全。私钥用K8s Secret存储，挂载到每个Core Pod中。"

**小胖**："那Registry存储层呢？镜像的Blob数据每个Registry实例都要能访问吧？用NFS行不行？"

**大师**："NFS在小规模场景下可以，但有两个致命问题：第一，NFS的锁机制在高并发下性能很差——Registry的GC操作需要遍历所有Blob，在NFS上可能耗时数小时。第二，NFS本身又是单点——NFS服务器宕机，所有Registry实例都不可用。

所以生产环境推荐用对象存储——MinIO（自建）或S3（云上）。对象存储本身就是高可用的（MinIO支持多节点纠删码），Registry通过S3 API访问，不需要关心底层存储的高可用。而且对象存储天然支持跨机房复制——顺便解决了灾备问题。"

**技术映射**：Harbor的`storage_service`配置支持多种后端——`filesystem`（本地/NFS）、`s3`、`azure`、`gcs`、`swift`等。修改`harbor.yml`中的`storage_service`字段即可切换存储后端，Registry组件会自动适配。

```
┌─ Harbor HA 四层架构 ────────────────────────────────────────────────┐
│                                                                     │
│  Layer 1: 接入层 (Nginx/Portal)                                      │
│  ├── 策略：多实例 + 健康检查 + 负载均衡 (LVS/HAProxy/Nginx)           │
│  ├── 要点：Nginx的upstream配置中标记down/backup节点                  │
│  └── 故障切换：Keepalived VIP漂移，切换时间 < 3秒                   │
│                                                                     │
│  Layer 2: 业务层 (Core/JobService)                                   │
│  ├── 策略：多实例 + 完全无状态                                       │
│  ├── 要点：所有实例共享PG/Redis，Token私钥统一分发                   │
│  └── 扩容：Core实例数 = PG max_connections × 0.7 / Core max_open_conns│
│                                                                     │
│  Layer 3: 数据层 (PostgreSQL + Redis)                                │
│  ├── PostgreSQL：主从流复制 + Patroni自动故障转移                    │
│  │   └── 关键参数：wal_level=replica, max_wal_senders=5              │
│  ├── Redis：Sentinel(哨兵)模式，≥3个Sentinel节点                     │
│  │   └── 关键参数：down-after-milliseconds=5000, failover-timeout=10000│
│  └── 脑裂防护：Patroni + etcd (PG) / SENTINEL quorum (Redis)         │
│                                                                     │
│  Layer 4: 存储层 (Registry Blob)                                     │
│  ├── 方案A：对象存储 (MinIO/S3) — 推荐生产环境                       │
│  ├── 方案B：NFS共享存储 — 适合小规模/测试环境                        │
│  └── 要点：所有Registry实例必须访问同一份Blob数据                    │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

**小白**："Registryctl组件为什么不能多实例？"

**大师**："Registryctl负责垃圾回收（GC）——它遍历所有Blob，标记未被任何Manifest引用的Blob为可删除。如果有两个Registryctl同时执行GC，就可能出现：实例A正在删除一个Blob，实例B刚好完成了Manifest引用检查认为这个Blob还被引用——这会导致数据不一致。所以Registryctl天然是单实例操作。不过好消息是GC不是高频操作——通常一周执行一次，所以单实例完全够用。"

**小胖**："听完感觉要做的事好多啊——有没有一个HR（高可用）改造清单，按优先级排序？"

**大师**："必须有。优先级从高到低："

| 优先级 | 组件 | 改造项 | 不做的风险 |
|--------|------|--------|-----------|
| P0 | PostgreSQL | 主从流复制 | 数据库宕机=全站瘫痪 |
| P0 | Registry | 共享存储 | 存储宕机=所有镜像丢失 |
| P1 | Core | 多实例+LB | 单Core故障=服务中断 |
| P1 | Redis | Sentinel | Session丢失=用户登出 |
| P2 | Nginx/Portal | 多实例+LB | 接入层单点（影响较小） |
| P3 | JobService | 多Worker | 任务积压（可恢复） |
| — | Trivy Adapter | 保持单实例 | 影响小，可接受 |

---

## 3 项目实战

### 3.1 环境要求

| 资源 | 最低配置 | 推荐配置 | 说明 |
|------|---------|---------|------|
| 服务器数量 | 3台 | 5台+ | PG主/从各1，Core×2，Nginx×1(可与Core混部) |
| CPU | 8核/台 | 16核/台 | Core和PG是CPU密集型 |
| 内存 | 32GB/台 | 64GB/台 | PG的shared_buffers建议设为内存的25% |
| 磁盘 | SSD 500GB | NVMe 1TB | PG的WAL写入对磁盘延迟敏感 |
| 网络 | 千兆 | 万兆 | PG流复制和镜像推送的带宽消耗大 |
| 操作系统 | Ubuntu 22.04 / CentOS 8 | Ubuntu 22.04 LTS | 推荐长期支持版本 |
| Docker | 24.0+ | 25.0+ | Harbor v2.12依赖Docker Compose v2 |
| PostgreSQL | 14 | 15 | Harbor v2.12推荐PG 15 |

### 3.2 架构拓扑

```
                 ┌──────────────┐
                 │   VIP (虚拟IP) │  ← Keepalived 漂移
                 └──────┬───────┘
           ┌────────────┼────────────┐
           ▼            ▼            ▼
     ┌──────────┐ ┌──────────┐ ┌──────────┐
     │ Nginx-1  │ │ Nginx-2  │ │ Nginx-3  │  ← 接入层
     │ :443     │ │ :443     │ │ :443     │
     └────┬─────┘ └────┬─────┘ └────┬─────┘
          │             │            │
          └─────────────┼────────────┘
                        │ upstream → Core实例池
           ┌────────────┼────────────┐
           ▼            ▼            ▼
     ┌──────────┐ ┌──────────┐ ┌──────────┐
     │ Core-1   │ │ Core-2   │ │ Core-3   │  ← 业务层 (无状态)
     └────┬─────┘ └────┬─────┘ └────┬─────┘
          │             │            │
          └─────────────┼────────────┘
                        │
           ┌────────────┼────────────┐
           ▼            ▼            ▼
     ┌──────────┐ ┌──────────┐ ┌──────────────┐
     │ PG Primary│ │ PG Standby│ │ Redis Sentinel│ ← 数据层
     │ :5432    │ │ :5432    │ │ :26379 ×3    │
     └──────────┘ └──────────┘ └──────────────┘
                        │
                        ▼
              ┌─────────────────┐
              │  对象存储 (MinIO) │  ← 存储层
              │  :9000          │
              └─────────────────┘
```

### 3.3 第一步：PostgreSQL 主从流复制

**目标**：建立PostgreSQL主从复制，实现数据库层高可用。

```bash
# ══════════════════════════════════════════════
# 步骤1：主节点配置
# ══════════════════════════════════════════════

# 1.1 编辑主节点 postgresql.conf
cat >> /data/harbor/database/postgresql.conf << 'EOF'
# --- 流复制配置 ---
wal_level = replica              # WAL级别设为replica（支持流复制）
max_wal_senders = 5              # 最大WAL发送进程数（≥从节点数+2留余量）
wal_keep_size = 1GB              # 保留WAL大小（防止从节点落后过多时WAL被清理）
max_replication_slots = 5        # 复制槽数量
hot_standby = on                 # 允许从节点接受只读查询
EOF

# 1.2 编辑主节点 pg_hba.conf，允许从节点复制连接
echo "host replication repl_user 192.168.1.101/32 md5" >> /data/harbor/database/pg_hba.conf
echo "host replication repl_user 192.168.1.102/32 md5" >> /data/harbor/database/pg_hba.conf

# 1.3 重启主节点PostgreSQL使配置生效
docker restart harbor-db

# 1.4 创建复制专用用户
docker exec -it harbor-db psql -U postgres -c \
  "CREATE ROLE repl_user WITH REPLICATION LOGIN PASSWORD 'Repl@Pass2024';"

# 1.5 创建复制槽（防止WAL被过早清理）
docker exec -it harbor-db psql -U postgres -c \
  "SELECT pg_create_physical_replication_slot('standby_slot_1');"

# 验证复制用户和复制槽
docker exec -it harbor-db psql -U postgres -c \
  "SELECT usename, usesuper, userepl FROM pg_user WHERE usename='repl_user';"
# 预期输出：
#  usename   | usesuper | userepl
# -----------+----------+---------
#  repl_user | f        | t

docker exec -it harbor-db psql -U postgres -c \
  "SELECT slot_name, slot_type, active FROM pg_replication_slots;"
# 预期输出：
#   slot_name     | slot_type | active
# ----------------+-----------+--------
#  standby_slot_1 | physical  | f

# ══════════════════════════════════════════════
# 步骤2：从节点初始化
# ══════════════════════════════════════════════

# 2.1 在从节点服务器上清空数据目录
docker stop harbor-db-standby 2>/dev/null || true
rm -rf /data/harbor-standby/database/*

# 2.2 使用pg_basebackup从主节点拉取基础备份
docker run --rm \
  -v /data/harbor-standby/database:/var/lib/postgresql/data \
  goharbor/harbor-db:v2.12.0 \
  pg_basebackup \
    -h 192.168.1.100 -p 5432 \
    -U repl_user \
    -D /var/lib/postgresql/data \
    -Fp -Xs -P -R
# 参数说明：
#   -h 192.168.1.100  主节点IP
#   -U repl_user      复制用户
#   -Fp               明文格式（非tar）
#   -Xs               流式传输WAL
#   -P                显示进度
#   -R                自动生成standby.signal和primary_conninfo
# 预期输出：
# pg_basebackup: initiating base backup, waiting for checkpoint to complete
# pg_basebackup: checkpoint completed
# pg_basebackup: write-ahead log start point: 0/2000028 on timeline 1
# pg_basebackup: starting background WAL receiver
# 23100/23100 kB (100%), 1/1 tablespace
# pg_basebackup: write-ahead log end point: 0/2000100
# pg_basebackup: waiting for background process to finish streaming ...
# pg_basebackup: syncing data to disk ...
# pg_basebackup: base backup completed

# 2.3 -R参数已自动生成 standby.signal 文件和 primary_conninfo
# 验证生成的文件
cat /data/harbor-standby/database/standby.signal
# 这是一个空文件，PostgreSQL检测到它就会以standby模式启动

cat /data/harbor-standby/database/postgresql.auto.conf
# 预期内容：
# primary_conninfo = 'host=192.168.1.100 port=5432 user=repl_user password=Repl@Pass2024'

# 2.4 编辑从节点 postgresql.conf
cat >> /data/harbor-standby/database/postgresql.conf << 'EOF'
hot_standby = on                          # 允许只读查询
hot_standby_feedback = on                 # 向主节点反馈查询状态（防止查询冲突）
max_standby_streaming_delay = 30s         # 最大复制延迟容忍
primary_slot_name = 'standby_slot_1'      # 使用主节点创建的复制槽
EOF

# 2.5 启动从节点
docker run -d --name harbor-db-standby \
  --network harbor-ha \
  -v /data/harbor-standby/database:/var/lib/postgresql/data \
  goharbor/harbor-db:v2.12.0

# ══════════════════════════════════════════════
# 步骤3：验证主从复制状态
# ══════════════════════════════════════════════

# 在主节点检查复制状态
docker exec harbor-db psql -U postgres -c \
  "SELECT client_addr, state, sync_state, 
          pg_wal_lsn_diff(sent_lsn, write_lsn) AS write_lag_bytes,
          pg_wal_lsn_diff(write_lsn, flush_lsn) AS flush_lag_bytes,
          pg_wal_lsn_diff(flush_lsn, replay_lsn) AS replay_lag_bytes
   FROM pg_stat_replication;"
# 预期输出（正常运行中）：
#  client_addr   | state   | sync_state | write_lag_bytes | flush_lag_bytes | replay_lag_bytes
# ---------------+---------+------------+-----------------+-----------------+------------------
#  192.168.1.101 | streaming | async    |       0         |       0         |        0

# 在从节点检查恢复状态
docker exec harbor-db-standby psql -U postgres -c \
  "SELECT pg_is_in_recovery(), 
          pg_last_wal_receive_lsn(),
          pg_last_wal_replay_lsn(),
          pg_last_xact_replay_timestamp();"
# 预期输出：
#  pg_is_in_recovery | pg_last_wal_receive_lsn | pg_last_wal_replay_lsn | pg_last_xact_replay_timestamp
# -------------------+-------------------------+------------------------+-------------------------------
#  t                 | 0/3000A28               | 0/3000A28              | 2024-01-16 11:30:00+00
```

### 3.4 第二步：Redis Sentinel 高可用

**目标**：部署Redis Sentinel集群，实现Redis自动故障转移。

```bash
# ══════════════════════════════════════════════
# 步骤1：部署三个Redis实例（1主2从）
# ══════════════════════════════════════════════

# Redis主节点配置 (redis-master.conf)
cat > /data/harbor/redis/redis-master.conf << 'EOF'
port 6379
requirepass Redis@Pass2024
masterauth Redis@Pass2024
maxmemory 512mb
maxmemory-policy allkeys-lru
save 900 1
save 300 10
save 60 10000
dir /data
EOF

# Redis从节点1配置 (redis-slave-1.conf)
cat > /data/harbor/redis/redis-slave-1.conf << 'EOF'
port 6379
requirepass Redis@Pass2024
masterauth Redis@Pass2024
replicaof 192.168.1.100 6379
maxmemory 512mb
maxmemory-policy allkeys-lru
dir /data
EOF

# Redis从节点2配置 - 同理

# 启动三个Redis实例
docker run -d --name harbor-redis-master --network harbor-ha \
  -v /data/harbor/redis/redis-master.conf:/etc/redis/redis.conf \
  redis:7-alpine redis-server /etc/redis/redis.conf

docker run -d --name harbor-redis-slave-1 --network harbor-ha \
  -v /data/harbor/redis/redis-slave-1.conf:/etc/redis/redis.conf \
  redis:7-alpine redis-server /etc/redis/redis.conf

# ══════════════════════════════════════════════
# 步骤2：部署三个Sentinel节点
# ══════════════════════════════════════════════

# Sentinel配置 (sentinel-1.conf) — 三个Sentinel节点使用相同配置
cat > /data/harbor/redis/sentinel.conf << 'EOF'
port 26379
sentinel monitor harbor-redis 192.168.1.100 6379 2
sentinel auth-pass harbor-redis Redis@Pass2024
sentinel down-after-milliseconds harbor-redis 5000
sentinel failover-timeout harbor-redis 10000
sentinel parallel-syncs harbor-redis 1
sentinel resolve-hostnames yes
sentinel announce-hostnames yes
EOF
# 参数详解：
# sentinel monitor <name> <ip> <port> <quorum>
#   quorum=2 表示至少2个Sentinel同意才触发故障转移（≥3个Sentinel节点时quorum=N/2+1）
# down-after-milliseconds=5000 表示5秒无响应就判定主观下线
# failover-timeout=10000 表示故障转移超时10秒
# parallel-syncs=1 表示每次只同步1个从节点（避免主节点带宽打满）

# 启动三个Sentinel（在不同服务器或不同端口）
for i in 1 2 3; do
  docker run -d --name harbor-sentinel-$i --network harbor-ha \
    -v /data/harbor/redis/sentinel.conf:/etc/redis/sentinel.conf \
    redis:7-alpine redis-sentinel /etc/redis/sentinel.conf
done

# ══════════════════════════════════════════════
# 步骤3：验证Sentinel集群
# ══════════════════════════════════════════════

# 查看Sentinel监控状态
docker exec harbor-sentinel-1 redis-cli -p 26379 sentinel master harbor-redis
# 预期输出：
#  1) "name"         2) "harbor-redis"
#  3) "ip"           4) "192.168.1.100"
#  5) "port"         6) "6379"
#  7) "flags"        8) "master"
#  9) "num-slaves"  10) "2"

docker exec harbor-sentinel-1 redis-cli -p 26379 sentinel slaves harbor-redis
# 列出所有从节点信息

docker exec harbor-sentinel-1 redis-cli -p 26379 sentinel sentinels harbor-redis
# 列出所有Sentinel节点（预期输出3个）
```

### 3.5 第三步：共享存储配置

**目标**：配置对象存储（MinIO）作为Registry的统一存储后端。

```bash
# ══════════════════════════════════════════════
# 方案A：MinIO对象存储（推荐）
# ══════════════════════════════════════════════

# 部署MinIO集群（4节点，纠删码模式）
docker run -d --name minio-1 --network harbor-ha \
  -v /data/minio/data-1:/data \
  -e MINIO_ROOT_USER=harbor-admin \
  -e MINIO_ROOT_PASSWORD=Minio@Pass2024 \
  minio/minio:latest server \
  http://minio-{1...4}/data --console-address ":9001"

# 创建Harbor专用Bucket
# 访问MinIO Console: http://192.168.1.200:9001
# 或使用mc命令行：
mc alias set minio http://192.168.1.200:9000 harbor-admin Minio@Pass2024
mc mb minio/harbor-registry
mc version enable minio/harbor-registry  # 启用版本控制（防误删）

# 修改 harbor.yml，配置对象存储
cat >> harbor.yml << 'EOF'
storage_service:
  s3:
    accesskey: harbor-admin
    secretkey: Minio@Pass2024
    region: us-east-1
    bucket: harbor-registry
    regionendpoint: http://minio-1:9000
    secure: false
    skipverify: false
    v4auth: true
    chunksize: 5242880
    rootdirectory: /docker/registry/v2
    # 可选：配置多部分上传阈值
    multipartcopychunksize: 33554432
    multipartcopymaxconcurrency: 100
    multipartcopythresholdsize: 33554432
EOF

# ══════════════════════════════════════════════
# 方案B：NFS共享存储（简易方案，仅适用于测试/小规模）
# ══════════════════════════════════════════════

# NFS服务器配置 (192.168.1.200)
yum install -y nfs-utils
mkdir -p /data/harbor/registry
chown -R 10000:10000 /data/harbor/registry  # Harbor Registry以uid 10000运行
echo "/data/harbor/registry 192.168.1.0/24(rw,sync,no_root_squash,no_subtree_check)" >> /etc/exports
exportfs -a
systemctl enable --now rpcbind nfs-server

# 客户端挂载 (所有运行Registry的节点)
yum install -y nfs-utils
mount -t nfs -o nolock,soft,timeo=30,retrans=3 192.168.1.200:/data/harbor/registry /data/harbor/registry

# docker-compose中挂载（使用Docker Volume NFS驱动）
# docker-compose.yml
registry:
  volumes:
    - nfs-registry:/storage
volumes:
  nfs-registry:
    driver: local
    driver_opts:
      type: nfs
      o: addr=192.168.1.200,nolock,soft,timeo=30
      device: ":/data/harbor/registry"
```

### 3.6 第四步：负载均衡与健康检查

**目标**：配置Nginx负载均衡和健康检查探针。

```nginx
# ══════════════════════════════════════════════
# Nginx upstream配置 (nginx.conf)
# ══════════════════════════════════════════════
upstream harbor_core {
    # 最少连接数算法（避免请求集中到某个实例）
    least_conn;
    
    server 192.168.1.101:8080 weight=1 max_fails=3 fail_timeout=30s;
    server 192.168.1.102:8080 weight=1 max_fails=3 fail_timeout=30s;
    server 192.168.1.103:8080 weight=1 max_fails=3 fail_timeout=30s backup;
    
    # 长连接复用（减少Core的连接开销）
    keepalive 32;
    keepalive_timeout 60s;
}

upstream harbor_portal {
    server 192.168.1.101:8081;
    server 192.168.1.102:8081;
}

server {
    listen 443 ssl http2;
    server_name harbor.company.com;
    
    ssl_certificate     /etc/nginx/certs/harbor.company.com.crt;
    ssl_certificate_key /etc/nginx/certs/harbor.company.com.key;
    
    # Core API 代理
    location /api/ {
        proxy_pass http://harbor_core;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        
        # 超时配置（扫描/复制等长耗时操作）
        proxy_read_timeout 600s;
        proxy_send_timeout 600s;
        
        # 限流保护
        limit_req zone=harbor_api burst=50 nodelay;
    }
    
    # Registry V2 API 代理（大文件推送）
    location /v2/ {
        proxy_pass http://harbor_core;
        client_max_body_size 0;  # 不限制上传大小
        proxy_request_buffering off;  # 流式传输
        proxy_read_timeout 900s;
    }
    
    # 健康检查端点（供负载均衡器探测）
    location /health-check {
        access_log off;
        return 200 "OK";
    }
}

# 限流区域配置
limit_req_zone $binary_remote_addr zone=harbor_api:10m rate=50r/s;
```

### 3.7 第五步：滚动重启脚本

**目标**：实现Core实例的零停机滚动重启。

```bash
#!/bin/bash
# ═══════════════════════════════════════════════
# harbor-rolling-restart.sh
# Core实例零停机滚动重启脚本
# ═══════════════════════════════════════════════

set -euo pipefail

CORE_INSTANCES=("192.168.1.101:8080" "192.168.1.102:8080" "192.168.1.103:8080")
CONTAINER_NAMES=("harbor-core-1" "harbor-core-2" "harbor-core-3")
NGINX_CONF="/etc/nginx/conf.d/harbor.conf"
HEALTH_TIMEOUT=120  # 等待健康检查超时（秒）

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

for i in "${!CORE_INSTANCES[@]}"; do
    core_addr="${CORE_INSTANCES[$i]}"
    container="${CONTAINER_NAMES[$i]}"
    
    log "=== Rolling restart: $container ($core_addr) ==="
    
    # Step 1: 从LB中摘除该实例（Nginx标记为down）
    log "Step 1/5: Marking $core_addr as down in Nginx..."
    ssh nginx-server "sed -i 's/server $core_addr;/server $core_addr down;/' $NGINX_CONF && nginx -s reload"
    
    # Step 2: 等待当前请求处理完成（优雅关闭）
    log "Step 2/5: Waiting for existing requests to drain..."
    sleep 15
    
    # Step 3: 重启Core实例
    log "Step 3/5: Restarting $container..."
    docker restart "$container"
    
    # Step 4: 等待健康检查通过
    log "Step 4/5: Waiting for health check..."
    waited=0
    while [ $waited -lt $HEALTH_TIMEOUT ]; do
        if curl -sf "http://$core_addr/api/v2.0/health" > /dev/null 2>&1; then
            log "  Core $core_addr is healthy!"
            break
        fi
        sleep 3
        waited=$((waited + 3))
        log "  Waiting... ${waited}s elapsed"
    done
    
    if [ $waited -ge $HEALTH_TIMEOUT ]; then
        log "ERROR: Core $core_addr failed to become healthy within ${HEALTH_TIMEOUT}s!"
        # 告警并终止脚本，需人工介入
        exit 1
    fi
    
    # Step 5: 重新加入LB
    log "Step 5/5: Re-enabling $core_addr in Nginx..."
    ssh nginx-server "sed -i 's/server $core_addr down;/server $core_addr;/' $NGINX_CONF && nginx -s reload"
    
    log "=== $container restart completed successfully ===\n"
done

log "All Core instances restarted. Harbor remains available."
```

### 3.8 可能遇到的坑

**坑1：PostgreSQL脑裂 — 两个节点同时接受写入**

现象：主从切换后，旧主恢复运行但未降级，导致两个PostgreSQL同时接受写入。Core服务随机连接到不同节点，出现"数据写入后查不到"的诡异现象。

根因：缺少自动化的故障检测和选主机制。当主节点网络闪断（而非宕机）时，从节点被提升为新主，旧主恢复后不知道自己已被"废黜"，继续以主节点身份运行。

解决方案：
```bash
# 方案1：使用Patroni + etcd进行自动化管理
# Patroni会自动检测主节点状态，通过etcd进行领导者选举
# 旧主恢复后，Patroni强制其以只读从节点模式启动

# 方案2：人工介入（临时）
# 在旧主上执行强制降级
docker exec harbor-db psql -U postgres -c \
  "SELECT pg_promote();"  # 仅在确定要做主时执行
# 或直接删除旧主数据目录，重新从新主做基础备份

# 方案3：配置PostgreSQL的recovery_target参数
# 在从节点的postgresql.conf中：
recovery_target_timeline = 'latest'
```

**坑2：Redis Sentinel切换期间Session丢失**

现象：Sentinel执行故障转移的5-30秒窗口内，所有已登录用户被强制登出，Portal页面跳转到登录页。

根因：Harbor Core使用Redis存储用户Session。Sentinel切换期间Redis不可写，Core无法验证Session token，直接返回401。

解决方案：
```bash
# 修改 Harbor Core 的 session_timeout 配置
# harbor.yml
session_timeout: 600  # 10分钟（远大于Sentinel切换时间30秒）
# Core会在Session过期前宽容处理——即使Redis暂时不可达也不立即踢出用户

# 另外，配置Harbor Core的Redis连接重试策略：
# 在docker-compose中设置环境变量
core:
  environment:
    - REDIS_URL=redis-sentinel:26379
    - REDIS_MASTER_NAME=harbor-redis
    - REDIS_CONNECTION_TIMEOUT=5s
    - REDIS_RETRY_MAX_ATTEMPTS=5
```

**坑3：大镜像推送期间Core重启导致上传中断**

现象：Jenkins正在推送一个8GB的AI镜像（已推送到60%），运维执行了Core滚动重启，导致上传中断，CI流水线失败。

根因：Docker镜像的push请求是通过Core代理到Registry的（Harbor的Nginx→Core→Registry链路）。虽然Registry本身是多实例共享存储的，但正在进行的push请求的HTTP连接绑定在特定的Core实例上，Core重启会断开这个连接。

解决方案：
```bash
# 1. 在滚动重启前检查是否有活跃的大文件上传
for core in "${CORE_INSTANCES[@]}"; do
  ACTIVE_UPLOADS=$(curl -s "http://$core/api/v2.0/health" | jq '.components[].name')
  # 检查Registry是否有进行中的blob upload
done

# 2. 使用K8s的preStopHook优雅关闭
# 设置terminationGracePeriodSeconds: 300（给5分钟完成当前上传）
# preStop执行：sleep 300 等待所有活跃连接自然断开

# 3. 在Nginx层面启用会话保持（sticky session）
upstream harbor_core {
    ip_hash;  # 同一客户端IP始终路由到同一个Core（有局限性）
    # 或使用sticky cookie（需要nginx-sticky-module）
}
```

**坑4：Nginx作为Registry反向代理时client_max_body_size限制**

现象：推送大于1GB的镜像时，Nginx返回413 Request Entity Too Large。

根因：Nginx默认`client_max_body_size`为1MB。虽然Harbor安装脚本会自动设置为0（不限制），但在自定义HA部署中容易被遗忘。

解决方案：
```nginx
server {
    location /v2/ {
        client_max_body_size 0;       # 不限制上传大小
        proxy_request_buffering off;  # 关闭请求缓冲（流式传输）
        proxy_buffering off;          # 关闭响应缓冲
    }
}
```

**坑5：PostgreSQL的max_connections不足导致Core启动失败**

现象：当Core实例数增加到3个以上时，新Core实例启动失败，日志报错`FATAL: sorry, too many clients already`。

根因：每个Core实例默认使用`max_open_conns=100`个数据库连接。3个Core实例=300个连接，超过了PostgreSQL默认的`max_connections=100`。

解决方案：
```
# 计算公式：Core实例数 × max_open_conns < PG max_connections × 0.7
# 0.7因子：预留30%连接给管理操作、JobService等其他组件

# 方案A：增加PostgreSQL max_connections
# postgresql.conf
max_connections = 500
shared_buffers = 4GB  # 增加连接数需要相应增加shared_buffers

# 方案B：减少Core的max_open_conns
# harbor.yml
database:
  max_open_conns: 50  # 每个Core的数据库连接数

# 方案C：使用PgBouncer连接池（推荐）
# 所有Core → PgBouncer（连接池）→ PostgreSQL（少量连接）
```

---

## 4 项目总结

### 4.1 HA改造清单对比

| 组件 | 改造措施 | 复杂度 | 是否必做 | 故障影响 | 切换时间 |
|------|---------|--------|---------|---------|---------|
| Nginx | 多实例 + LB (Keepalived/LVS) | ⭐⭐ | ✅ | 接入层不可达 | < 3秒 |
| Core | 多实例 + LB | ⭐ | ✅ | 服务中断 | 即时（LB自动摘除） |
| Portal | 多实例 + LB | ⭐ | ✅ | 控制台不可用 | 即时 |
| PostgreSQL | 主从流复制 (+Patroni) | ⭐⭐⭐⭐ | ✅ | 数据丢失 | 30秒-3分钟 |
| Redis | Sentinel / Cluster | ⭐⭐⭐ | ✅ | Session丢失 | 5-30秒 |
| Registry | 共享存储 (MinIO/S3) | ⭐⭐⭐ | ✅ | 镜像不可用 | 无（存储层本身HA） |
| JobService | 多Worker | ⭐ | ⭐ | 任务积压 | 无（任务可重试） |
| Registryctl | 保持单实例 | ⭐ | ❌ | GC无法执行 | N/A（非实时服务） |
| Trivy Adapter | 单实例(可接受) | ⭐ | ❌ | 扫描不可用 | N/A（可稍后重扫） |

### 4.2 适用场景

1. **生产环境99.99%可用性要求**：金融、电商等核心业务的镜像仓库，SLA要求年停机<52分钟。
2. **业务规模持续增长需要水平扩展**：并发Pull/Push QPS持续增长，单机已无法承载。
3. **多机房容灾需求**：需要跨机房部署实现异地灾备，单机房故障不影响服务。
4. **频繁运维操作需要零停机**：操作系统补丁、Harbor版本升级、数据库大版本迁移等需要在线操作。
5. **合规审计要求**：行业监管（如银保监会）要求关键基础设施具备高可用和灾备能力。

### 4.3 不适用场景

1. **小型团队/测试环境**：10人以下团队，镜像量<1000个，单机部署满足需求。HA改造的运维成本远超其收益。
2. **完全云原生托管场景**：已使用云厂商的托管镜像仓库服务（如AWS ECR、阿里云ACR EE），这些服务自带多AZ高可用，无需自建HA。

### 4.4 注意事项

1. **Registryctl必须保持单实例**：垃圾回收（GC）操作不可被多个实例同时执行，否则可能导致Blob数据不一致。
2. **Core实例数上限计算公式**：`Core实例数 × max_open_conns < PostgreSQL max_connections × 0.7`。超出此限制将导致数据库连接耗尽。
3. **Sentinel集群至少需要3个节点**：满足Raft共识算法要求（quorum = N/2+1），2节点无法正常工作（1节点故障时无法形成多数派）。
4. **对象存储优于NFS**：NFS的锁机制在高并发下性能极差，且NFS本身是单点。生产环境应优先使用MinIO或云厂商对象存储。
5. **定期演练故障切换**：至少每季度执行一次故障切换演练，验证自动切换流程的有效性和切换时间是否符合SLA要求。未经过演练的HA方案等同于没有HA。

### 4.5 常见故障排查表

| 故障现象 | 根因 | 排查命令 | 解决方案 |
|---------|------|---------|---------|
| Core启动后立即退出，日志显示"too many clients" | PG连接数超限 | `SELECT count(*) FROM pg_stat_activity;` | 增加PG max_connections或减少Core max_open_conns |
| 从节点显示"streaming"但数据不同步 | WAL被主节点清理 | `SELECT pg_wal_lsn_diff(sent_lsn, replay_lsn) FROM pg_stat_replication;` | 重建复制槽，增大wal_keep_size |
| Sentinel显示"ODOWN"但Redis实际运行正常 | Sentinel节点间网络分区 | `redis-cli -p 26379 sentinel ckquorum harbor-redis` | 检查网络连通性，重启isolated Sentinel |
| 推送大镜像失败"Nginx 504 Gateway Timeout" | proxy_read_timeout不足 | 检查nginx error.log | 增加proxy_read_timeout到900s |
| 镜像pull时"manifest unknown" | Manifest List未同步到新Registry实例 | 检查共享存储是否可访问 | 验证所有Registry实例挂载同一存储 |
| 故障切换后出现"split-brain detected" | 旧主恢复后未降级 | 检查两个PG节点的`pg_is_in_recovery()` | 手动降级旧主或使用Patroni自动管理 |

### 4.6 深度思考

1. **Harbor部署在Kubernetes中时，如何使用StatefulSet + Headless Service实现PostgreSQL和Redis的HA，而不依赖外部中间件（如Patroni + etcd）？这会对故障切换时间（RTO）和数据丢失容忍度（RPO）产生什么影响？**

2. **假设你需要在两个物理距离300km的机房之间部署Harbor HA（异地多活），PostgreSQL的同步流复制会带来约50ms的写入延迟。在"数据零丢失"和"写入性能"之间如何权衡？是否可以设计一个"异步复制+WAL归档+延迟监控"的折中方案？**

---

> 下一章预告：第23章将深入Harbor漏洞扫描系统的架构，探讨如何自定义扫描策略和集成第三方扫描器。
