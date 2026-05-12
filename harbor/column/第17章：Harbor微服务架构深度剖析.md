# 第17章：Harbor 微服务架构深度剖析

## 1 项目背景

云折科技是一家快速成长的互联网电商公司，拥有200+开发人员，日均部署300+次容器镜像，生产环境运行着5000+容器实例。他们一年前从单机Docker Compose部署了Harbor，当时只有8个项目和20个开发人员使用。一年后，Harbor上已有150+个项目、8000+镜像制品，承载着公司所有微服务的镜像分发。随着规模扩大，架构师团队开始思考扩展和定制问题。

**痛点一：出故障时不知道哪个服务是根因。** 某天下午，Harbor响应缓慢——Portal页面加载10秒、`docker pull`速度从2MB/s骤降到200KB/s。运维排查了所有8个容器的日志，花了整整2小时才发现根因是JobService中的一个GC任务占用了大量CPU。GC任务只是边缘服务，为什么会拖慢整个Harbor？原来Core和JobService虽然分开部署，但共享了宿主机的CPU资源——GC密集计算导致整体CPU throttle，Core的API请求排队等待。

**痛点二：想做自定义扩展但不知道从哪个服务入手。** 安全部门要求"在每次push镜像后，自动调用外部审计系统记录镜像元数据和推送者信息"。架构师知道需要写自定义代码，但不确定这个逻辑应该加在Core、JobService还是Registry中。如果加在Core，会不会影响API响应速度？如果加在Registry的webhook中，能否拿到用户的上下文信息？

**痛点三：K8s迁移时资源规划没有依据。** 生产环境Harbor需要从Docker Compose迁移到K8s，运维需要为每个服务分配合理的CPU/Memory Request和Limit。但团队不知道每个服务处理一个请求消耗多少资源、哪些服务可以水平扩展、哪些服务必须单实例。如果没有依据地设值，要么资源浪费（Request设太高），要么Pod频繁OOMKilled（Limit设太低）。

**痛点四：微服务间通信链路不透明。** 当运维在网络层配置了微隔离策略后，Harbor的部分功能开始异常——扫描任务提交失败、GC任务状态无法更新。排查发现防火墙规则误封了Core到Redis的6379端口，但团队花了半天才定位到这个通信链路，因为不清楚"Core到底是通过HTTP还是Redis跟JobService通信的"。

本章将"打开Harbor的引擎盖"，逐一剖析Core、Portal、JobService、Registry、Registryctl、Trivy Adapter、Redis、PostgreSQL八个组件的职责边界、通信协议、数据流和扩展策略。

---

## 2 项目设计——剧本式交锋对话

**场景：架构评审会，团队讨论是否可以从单机Docker Compose部署迁移到K8s分布式部署。**

**小胖**（看着白板上的架构图，皱着眉头）："太复杂了！8个容器，20多条网络连线。能不能把Core和JobService合并成一个服务？你看GitLab Container Registry就一个二进制，多清爽。"

**大师**（笑着摇头）："小胖，你说的'合并'是经典的反模式——把不同性质的负载混在一起。Core处理同步请求（API调用、认证），JobService处理异步长任务（GC、复制、扫描）。混在一起会怎样？一个GC任务吃掉了CPU，用户登录都被卡住。"

大师在白板上画了一个厨房："这就跟厨房一样——洗菜切菜（同步快活）和熬汤（异步慢活）最好分开。你洗菜的时候旁边炉子慢慢炖着，互不干扰。如果非要一个人又切菜又盯汤——客人一喊点菜你就手忙脚乱，汤也扑了，菜也没切好。"

**技术映射**：Harbor的微服务拆分遵循**CQS（Command Query Separation）**原则——Core作为命令/查询的统一入口（同步API），JobService作为异步命令的执行器。这种设计允许Core水平扩展处理更多API请求，JobService独立扩容Worker数量处理更多异步任务。源码中，Core的API层在`src/server/v2.0/`下的各handler中，而异步任务的创建通过`src/controller/`层将任务写入Redis队列。

**小白**（推了推眼镜，在EverNote上快速记笔记）："那每个服务的通信协议是什么？它们之间是走HTTP还是gRPC还是消息队列？我想理清数据流——特别是出错的时候能知道从哪里入手排查。"

**大师**（满意地点头）："好问题，这正是架构评审的核心。这8个组件之间的通信走的是不同的协议——我画一个通信矩阵："

```
通信矩阵：
┌──────────────┬───────────────────────────────────────────────────┐
│ 通信路径      │ 协议           说明                               │
├──────────────┼───────────────────────────────────────────────────┤
│ Client → Nginx│ HTTP/HTTPS     TLS终止 + 反向代理（端口443/80）    │
│ Nginx → Core │ HTTP           API请求路由（/api/v2.0/*）          │
│ Nginx → Portal│ HTTP          静态文件 (Angular SPA)              │
│ Core → PG    │ TCP (pq)       PostgreSQL wire protocol, 端口5432  │
│ Core → Redis │ TCP (RESP)     缓存/会话/任务队列, 端口6379         │
│ Core → JobSvc│ Redis Queue    RPUSH/BRPOP（非HTTP！）              │
│ Core → Registry│ HTTP          Token验证 + Blob代理               │
│ JobSvc → Trivy│ HTTP          扫描请求（REST API）                 │
│ JobSvc → Registry│ HTTP        GC触发 / 复制                      │
│ Registry → Storage│ 文件系统/S3 API  Blob读写                     │
│ Registryctl → Registry│ HTTP  配置重载                             │
└──────────────┴───────────────────────────────────────────────────┘
```

**技术映射**：Harbor Core与JobService之间的通信最为特殊——它们通过Redis的消息队列（List数据结构）传递任务，而非直接的HTTP调用。这是因为JobService的Worker是无状态的，任务失败了可以重试——Redis的BRPOP/BLPOP天然支持这种"生产-消费"模式。源码中，Core通过`src/jobservice/`包将任务序列化为JSON后RPUSH入队，JobService Worker在`src/jobservice/job/`中通过`BRPOP`阻塞等待任务。

**小胖**："Redis到底在Harbor中存了什么？不就是个缓存吗？我之前看监控，Redis的内存占用持续增长，是不是要定期重启？"

**大师**："Redis在Harbor中扮演了四个角色，远不止缓存——理解这四个角色是排查很多故障的关键。"

**角色一：Session存储。** 用户登录Portal后，Session Token存在Redis中（Key：`_sid:<token>`，Value：用户信息JSON）。这就是为什么你重启Core容器后不需要重新登录——Session在Redis里，不在Core内存里。

**角色二：任务队列。** Core创建异步任务（如scan/replicate/gc）时，不是直接调JobService API，而是：
```
Core → RPUSH job:scan:queue <job_json>
JobService Worker → BRPOP job:scan:queue
```
每个任务类型有独立队列：`job:scan:queue`、`job:replication:queue`、`job:gc:queue`。

**角色三：分布式锁。** 定时任务（如标签保留策略）需要防止多个JobService Worker同时执行——用Redis的`SETNX`实现。Key格式：`lock:scheduled_job:<job_name>`，TTL为任务执行时间的2倍。

**角色四：元数据缓存。** 项目信息、仓库列表等频繁查询的数据缓存在Redis中，减少PostgreSQL压力（TTL通常5-30分钟）。Key格式：`project:<project_id>:info`。

**技术映射**：Key命名规范统一为`{namespace}:{resource}:{identifier}`，例如`project:42:repository_count`、`artifact:sha256:abc123:tags`。源码中缓存逻辑在`src/pkg/cache/`包中实现，通过`Cache-Aside`模式读写。

**小白**（若有所思）："假如某个服务挂掉了，整个Harbor还能运行吗？比如说Redis挂了，用户还能pull镜像吗？我想知道各服务的容错边界。"

**大师**："这是架构评审最核心的问题——服务的崩溃域。我给一个'单点故障影响表'："

| 故障服务 | Push/Pull | Portal | 扫描 | GC | 复制 |
|---------|-----------|--------|------|----|----|
| Redis | ❌ push需Session | ❌ 无法登录 | ❌ 任务丢失 | ❌ | ❌ |
| PostgreSQL | ❌ 认证失败 | ❌ | ❌ | ❌ | ❌ |
| Core | ❌（需代理） | ❌ | ❌ | ❌ | ❌ |
| JobService | ✅ | ✅ | ❌ | ❌ | ❌ |
| Registry | ❌ | ✅ | ✅ | ❌ | ✅ |

"注意——Registry是唯一直接影响`docker pull/push`的服务，但Core挂了镜像也不能pull（因为需要通过Core做Token认证）。真正的高可用需要Core+Registry+PG+Redis全部冗余部署。"

**技术映射**：Registry可以通过配置`auth.token.realm`绕过Core直接验证Token（使用Core签发的公钥），但Harbor默认不在这种模式下运行。参见`src/registryctl/config.yml`中的auth配置项。

---

## 3 项目实战

### 环境要求

| 组件 | 版本 | 说明 |
|------|------|------|
| Harbor | v2.12.x | 目标分析版本 |
| Docker Engine | 24.x+ | 运行Harbor容器 |
| Docker Compose | v2.x+ | 编排Harbor组件 |
| jq | 1.6+ | JSON解析工具（API响应分析） |
| curl | 7.68+ | HTTP请求测试 |
| redis-cli | 6.0+ | Redis命令行工具（随Redis容器） |
| psql (optional) | 14+ | PostgreSQL客户端 |

### 3.1 追踪一次 `docker push` 的完整请求链路

**目标**：通过模拟push操作，理解每个组件在请求链路中的角色。

```bash
# Step 1: 获取认证Token（模拟docker login后的认证）
# docker client 首先访问 /v2/，Harbor返回 WWW-Authenticate 头
curl -v https://harbor.company.com/v2/ 2>&1 | grep -i "www-authenticate"

# 预期输出：
# < Www-Authenticate: Bearer realm="https://harbor.company.com/service/token",
#   service="harbor-registry",scope="registry:catalog:*"

# Step 2: 通过 /service/token 获取JWT Token
TOKEN=$(curl -s -u admin:Str0ng@Admin2024 \
  "https://harbor.company.com/service/token?service=harbor-registry&scope=repository:order-platform/order-service:pull,push" | \
  jq -r '.token')

# 解码JWT查看核心权限字段
echo $TOKEN | cut -d'.' -f2 | base64 -d 2>/dev/null | jq '.access'

# 预期输出：
# [
#   {
#     "type": "repository",
#     "name": "order-platform/order-service",
#     "actions": ["pull", "push"]
#   }
# ]

# Step 3: 使用Token发起Blob上传（模拟docker push的第一步）
curl -X POST -H "Authorization: Bearer $TOKEN" \
  "https://harbor.company.com/v2/order-platform/order-service/blobs/uploads/" -v 2>&1

# 预期输出中包含 Location 头，指向上传URL：
# < Location: https://harbor.company.com/v2/order-platform/order-service/blobs/uploads/<uuid>
```

**完整链路图**：
```
1. Docker Client → Nginx (HTTPS:443)
   ↓ 反向代理到 harbor-core (HTTP:8080)
2. Nginx → harbor-core (验证Authorization)
   ↓ Core签发JWT Token → 返回给Client
3. Client → Nginx → Core → Registry (HTTP:5000)
   ↓ Core代理Blob请求 (Token已嵌入Header)
4. Registry → 本地文件系统 / S3 写入Blob
   ↓ Blob写入成功后返回201 Created
5. Core → Redis RPUSH job:scan:queue（异步触发扫描）
   ↓ Redis队列存储任务JSON
6. JobService Worker → BRPOP job:scan:queue
   → 调用 Trivy Adapter 执行漏洞扫描
```

### 3.2 验证各组件间网络连通性

**目标**：从Core容器内测试与所有依赖服务的连通性。

```bash
# 进入Core容器
docker exec -it harbor-core /bin/sh

# 逐一验证连通性
echo "=== PostgreSQL ==="
# Core通过Go的database/sql连接PG，端口5432
nc -zv harbor-db 5432 2>&1
# 预期输出：harbor-db (172.18.0.3:5432) open

echo "=== Redis ==="
redis-cli -h redis ping
# 预期输出：PONG

echo "=== Registry ==="
# Registry健康检查端点
curl -s http://registry:5000/v2/ 2>&1
# 预期输出：{}（空JSON对象，表示Registry正常运行）

echo "=== JobService ==="
# JobService统计接口
curl -s http://harbor-jobservice:8080/api/v1/stats 2>&1 | head -c 200
# 预期输出：{"total_jobs": ..., "pending_jobs": ..., "running_jobs": ..., "workers": ...}

echo "=== Trivy Adapter ==="
# Trivy健康检查
curl -s http://trivy-adapter:8080/probe/healthy 2>&1
# 预期输出：Healthy 或 200 OK

exit
```

### 3.3 监控JobService任务队列深度

**目标**：实时监控任务队列状态，识别任务积压问题。

```bash
# 查看各任务队列长度
docker exec redis redis-cli <<EOF
LLEN job:scan:queue
LLEN job:replication:queue
LLEN job:gc:queue
LLEN job:retention:queue
LLEN job:webhook:queue
EOF

# 示例输出：
# 3   ← 3个扫描任务等待处理
# 0
# 1   ← 1个GC任务等待处理
# 0
# 2   ← 2个webhook回调等待处理

# 查看队列中第一个任务的详细内容（不消费）
docker exec redis redis-cli LRANGE job:scan:queue 0 0 | jq '.'

# 通过API查看JobService统计
curl -s -u admin:Str0ng@Admin2024 \
  "https://harbor.company.com/api/v2.0/jobservice/stats" | jq '.'

# 预期输出：
# {
#   "total_jobs": 1250,
#   "pending_jobs": 5,
#   "running_jobs": 2,
#   "scheduled_jobs": 10,
#   "workers": {
#     "scan": 2,
#     "replication": 1,
#     "gc": 1,
#     "retention": 1
#   }
# }
```

### 3.4 配置Core多实例水平扩展

**目标**：验证Core无状态特性，配置多实例部署。

```yaml
# docker-compose.core-scale.yml
# 通过scale指令扩展Core实例（用于Docker Compose v2+）
services:
  core:
    build:
      context: .
    deploy:
      replicas: 3
    environment:
      - CORE_URL=http://core:8080
      - CORE_LOCAL_URL=http://core:8080
```

```bash
# 使用docker compose scale（测试用）
cd /opt/harbor
docker compose up -d --scale core=3

# 验证多实例运行
docker compose ps core

# 测试：连续请求API，观察响应来自不同实例
for i in $(seq 1 10); do
  curl -s -u admin:Str0ng@Admin2024 \
    "https://harbor.company.com/api/v2.0/health" | jq '.hostname'
done

# 预期输出：hostname在不同实例间轮转（Nginx负载均衡）
```

**扩展Core的关键前提**：
- Core本身无状态（状态在PostgreSQL和Redis中）
- Nginx必须配置`upstream core_backend`做负载均衡
- 所有Core实例共享同一个Redis（Session共享）
- JWT签名私钥（`/etc/core/private_key.pem`）必须在所有实例间一致

### 3.5 实时分析各服务资源消耗

**目标**：获取每个组件的资源消耗数据，为K8s资源规划提供依据。

```bash
# 实时查看各容器资源统计
docker stats --no-stream --format "table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.NetIO}}"

# 典型生产环境输出示例：
# NAME                CPU %    MEM USAGE / LIMIT     NET I/O
# harbor-core         2.5%     450MiB / 4GiB         15MB / 8MB
# harbor-db           1.8%     320MiB / 2GiB         2MB / 1MB
# redis               0.3%     80MiB / 1GiB          500KB / 300KB
# registry            0.8%     120MiB / 2GiB         20MB / 5MB
# registryctl         0.1%     30MiB / 512MiB        100KB / 50KB
# harbor-jobservice   1.2%     180MiB / 2GiB         1MB / 500KB
# harbor-portal       0.1%     25MiB / 256MiB        200KB / 100KB
# nginx               0.2%     15MiB / 256MiB        45MB / 30MB
# trivy-adapter       0.5%     350MiB / 4GiB         5MB / 2MB

# 持续监控并输出到文件（用于生成基线数据）
docker stats --format "{{.Name}},{{.CPUPerc}},{{.MemUsage}}" > harbor-stats-$(date +%Y%m%d).csv &

# 模拟高负载后对比：
# 1. 使用ab或wrk对Core API施加压力
# 2. 同时docker push一个大型镜像
# 3. 观察资源消耗峰值
```

### 3.6 模拟服务故障观察影响范围

**目标**：通过停止单个服务，验证影响范围和容错设计。

```bash
# 场景1：停止JobService——验证Push/Pull不受影响
docker compose stop harbor-jobservice
echo "JobService stopped. Testing docker pull..."
docker pull harbor.company.com/order-platform/order-service:latest
# 预期：pull成功（Registry和Core都在运行）
docker push harbor.company.com/order-platform/order-service:v1.0.1
# 预期：push成功，但扫描任务不会被创建（不触发异步扫描）

# 场景2：停止Redis——验证影响范围
docker compose stop redis
echo "Redis stopped. Testing..."
curl -s -u admin:Str0ng@Admin2024 \
  "https://harbor.company.com/api/v2.0/projects" 2>&1
# 预期：返回500，因为Core依赖Redis做Session缓存

# 场景3：恢复服务
docker compose start redis harbor-jobservice
sleep 10
curl -s -u admin:Str0ng@Admin2024 \
  "https://harbor.company.com/api/v2.0/health" | jq '.status'
# 预期：返回 "healthy"
```

### 3.7 可能遇到的坑

**坑1：Core → JobService 任务丢失**

| 项目 | 内容 |
|------|------|
| **症状** | Core成功推送了镜像（返回201），API中制品可见，但扫描从未触发。Portal中显示"未扫描"。 |
| **根因** | Core向Redis `RPUSH`任务成功，但JobService Worker未`BRPOP`——可能是Worker挂了，或者JobService容器启动时Redis不可达（Worker初始化失败后不会自动重连）。 |
| **排查** | `docker logs harbor-jobservice --tail 50 \| grep "worker"` 查看Worker状态；`docker exec redis redis-cli LLEN job:scan:queue` 确认队列积压。 |
| **解决** | 重启JobService容器：`docker compose restart harbor-jobservice`。为预防，在K8s中配置livenessProbe检测Worker健康状态。 |

**坑2：多Core实例下JWT签名不一致**

| 项目 | 内容 |
|------|------|
| **症状** | Core水平扩展为3个实例后，用户偶尔收到401 Unauthorized。观察发现只有在请求被负载均衡到Core-2或Core-3时出现。 |
| **根因** | 新增Core实例的`/etc/core/private_key.pem`与Core-1不一致——JWT签名用的私钥不同，Registry验证Token签名失败。 |
| **解决** | 确保所有Core实例挂载同一个Secret或ConfigMap。在docker compose中确保`volumes`指向同一个密钥文件路径。 |

**坑3：Redis内存持续增长直至OOM**

| 项目 | 内容 |
|------|------|
| **症状** | Redis内存从初始200MB增长到2GB（maxmemory限制），然后开始evict数据，导致Session丢失、任务队列被截断。 |
| **根因** | （1）某些缓存Key没有设置TTL（如项目信息缓存），永久驻留。（2）任务队列中失败任务未被清理——Core只push不检查队列积压。 |
| **解决** | 设置`maxmemory-policy allkeys-lru`；对任务队列设置上限（`LTRIM`）；配置`Harbor Core`的`cache_cleanup_interval: 1h`。 |

**坑4：Nginx超时配置导致长耗时API被截断**

| 项目 | 内容 |
|------|------|
| **症状** | `/api/v2.0/projects/{id}/summary` 接口偶尔返回504 Gateway Timeout。此接口需要聚合多个子查询（存储统计、制品数、漏洞数），在数据量大的项目上耗时可达90秒。 |
| **根因** | Nginx的`proxy_read_timeout`默认为60秒，超时后切断连接。Core仍在执行查询（浪费数据库资源），但响应已被Nginx丢弃。 |
| **解决** | 在nginx.conf中增加`proxy_read_timeout 120s;`。长期方案是对summary接口做异步化——提交查询任务后返回job ID，通过轮询获取结果。 |

---

## 4 项目总结

### 4.1 八组件职责与能力矩阵

| 组件 | 核心职责 | 可水平扩展 | 资源敏感性 | 单点故障影响 | 关键依赖 | 监听端口 |
|------|---------|-----------|-----------|------------|---------|---------|
| Nginx | 反向代理 + TLS终止 | ✅ 多副本 | 网络I/O | Push/Pull/Portal全部不可用 | 证书文件 | 443/80 |
| Core | API服务 + 认证 + 业务逻辑 | ✅ 多副本 | CPU + 内存 | 全功能不可用 | PG + Redis | 8080 |
| Portal | Web前端（Angular SPA） | ✅ 多副本 | 低 | Web UI不可用（CLI不受影响） | Core API | 8080 |
| JobService | 异步任务（扫描/复制/GC） | ⚠️ 多Worker | CPU密集型 | 扫描/复制/GC暂停 | Redis + Registry | 8080 |
| Registry | 镜像存储与分发 | ⚠️ 有限（需共享存储） | 磁盘I/O + 网络 | push/pull不可用 | 存储后端 | 5000 |
| Registryctl | Registry配置管理 | ❌ 单实例 | 低 | GC失败（不影响push/pull） | Registry | 8080 |
| Trivy Adapter | 漏洞扫描引擎 | ⚠️ 有限 | CPU + 内存(大) | 扫描失败 | CVE数据库 | 8080 |
| PostgreSQL | 元数据持久化 | ⚠️ 复杂（主从复制） | 磁盘I/O | 全功能不可用 | - | 5432 |
| Redis | 缓存/队列/锁/Session | ✅ 哨兵/集群 | 内存 | Session丢失 + 任务堆积 | - | 6379 |

### 4.2 数据流完整分类

| 数据流 | 路径 | 协议 | 同步/异步 | 重试策略 |
|--------|------|------|----------|---------|
| Push镜像 | Client→Nginx→Core→Registry→Storage | HTTP | 同步 | Client重试 |
| Pull镜像 | Client→Nginx→Core→Registry→Storage | HTTP | 同步 | Client重试 |
| Portal登录 | Client→Nginx→Core→PG/Redis | HTTP | 同步 | 无（用户手动） |
| 漏洞扫描 | Core→Redis→JobService→Trivy Adapter | Redis Q+HTTP | 异步 | 3次指数退避 |
| 复制同步 | Core→Redis→JobService→Remote Registry | Redis Q+HTTP | 异步 | 3次指数退避 |
| 垃圾回收 | Core→Redis→JobService→Registryctl | Redis Q+HTTP | 异步 | 3次指数退避 |
| 标签保留 | 定时→JobService→Registryctl | HTTP | 异步 | 3次指数退避 |
| Webhook回调 | Core→Redis→JobService→External URL | Redis Q+HTTP | 异步 | 3次指数退避 |

### 4.3 适用场景与不适场景

**适用场景：**
- **故障定位**：知道每个服务的职责边界后，能快速判断故障在哪个服务（如无法pull → 先查Registry → 再查Core → 再查存储后端）
- **性能调优**：根据资源敏感性为每个服务分配合理的CPU/Memory配额（Registry加SSD，Trivy加大内存）
- **扩展规划**：Core和Nginx可水平扩展应对高并发API请求，Registry需要SSD和充足网络带宽
- **客制化开发**：清楚应该修改哪个服务的代码（自定义逻辑优先在JobService或Core中实现）
- **网络策略配置**：根据通信矩阵配置K8s NetworkPolicy或防火墙规则，确保微隔离同时不误封合法流量

**不适场景：**
- **极简部署**：如果团队只有3-5个开发人员、不到10个镜像，微服务架构反而是负担——单机Docker Compose足够
- **全部无状态化**：PostgreSQL和存储后端天然有状态，不能为了微服务化而强行无状态（会引入更复杂的分布式一致性问题）

### 4.5 注意事项

1. **Registryctl是隐藏的单点**：如果它挂了，GC将无法执行（但不影响Push/Pull）。在高可用方案中需要为Registryctl也配置冗余
2. **JobService Worker数量不能无限制增加**：每个Worker占用一个PostgreSQL连接——Pool Size有限（默认100），Worker数量必须小于连接池容量
3. **Redis不可用时整个系统瘫痪**：Core依赖Redis做Session缓存，而PostgreSQL无法替代这个角色——Redis的HA比PostgreSQL的HA更紧急
4. **Nginx的超时配置影响全局**：`proxy_read_timeout`必须覆盖最长API响应时间（如summary接口可达90秒），否则会产生大量虚假504
5. **Trivy Adapter内存预算是最大的**：Trivy需要将CVE数据库加载到内存（约300-500MB），加上扫描时的峰值内存，建议预留4GB以上

### 4.6 常见故障速查表

| 故障现象 | 根因 | 快速解决 |
|---------|------|---------|
| Portal加载但API全部报502 | Core容器挂掉 | `docker compose restart harbor-core` |
| docker pull超时 | Registry磁盘IO高或存储不可达 | 检查存储后端 + SSD性能 |
| 扫描一直"Pending" | Redis队列积压（JobService Worker不够） | 增加Worker或重启JobService |
| API偶尔返回500 | PostgreSQL连接池耗尽 | 检查Core的`MAX_OPEN_CONNS`配置 |
| 登录后立即被踢出 | Redis Session丢失（内存evict） | 增加Redis maxmemory或启用RDB |
| push成功但scan未触发 | JobService未连接Redis | 重启JobService + 检查Redis网络 |

### 4.7 深度思考题

1. **如果公司需要为Harbor加一个"自动将推送的镜像元数据同步到CMDB系统"的功能，应该修改哪个组件的代码？涉及哪些通信链路？如果JobService处理，如何保证元数据不丢失（事务性）？**

2. **当前Harbor的单点故障风险在哪里？如果要实现全组件高可用，请按"改造难度从低到高"排序各组件的HA改造路径，并说明原因（考虑有状态/无状态、数据一致性要求等维度）。**

---

> 下一章预告：第18章将深入Harbor的认证与鉴权源码框架，从Token Service到JWT签发验证完整链路。
