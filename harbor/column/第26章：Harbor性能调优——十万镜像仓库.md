# 第26章：Harbor 性能调优——十万镜像仓库

## 1 项目背景

某头部视频平台拥有8000+研发人员，其Harbor私有镜像仓库承载着全公司微服务镜像的存储与分发。该实例管理着8000+个仓库、12万+个制品、50万+个Blob层，月均pull请求超过500万次，日均push约2万次。随着业务从单体架构向微服务迁移，镜像构建频率从每周一次变为每天近百次，性能瓶颈集中爆发。

**痛点一：docker pull首字节延迟3-5秒，用户体验恶化。** 开发人员反映即使拉取一个100MB的小镜像，也要干等3-5秒才开始传输数据，而相同镜像从Docker Hub拉取只需要0.5秒。运维排查发现，罪魁祸首是Harbor Core的Token签发环节——每次pull操作都需要先调用`GET /service/token`获取授权Token，Core需要查询PostgreSQL中的项目角色表、Redis中的Session缓存、再调用Registry的challenge接口，整个链路平均耗时2.8秒。更糟的是，Token默认30分钟过期，在CI/CD高峰期，同一个构建流水线可能反复签发Token十几次。

**痛点二：Portal仓库列表加载超时，运维效率极低。** `order-platform`项目下有500+个仓库，打开Harbor Portal的仓库列表页面需要等待20+秒，页面空白期间用户以为浏览器卡死了。排查发现API `/api/v2.0/projects/{id}/repositories?page_size=-1` 返回的JSON超过2MB（500个仓库×每个4KB的元数据），浏览器解析并渲染这500行数据又卡了5秒。运维人员在紧急故障时根本不敢打开Portal——排查问题变成了"盲人摸象"。

**痛点三：并发push时PostgreSQL CPU飙至100%，引发雪崩效应。** "双十一"促销前，Jenkins批量构建100+个微服务镜像并并发推送到Harbor。PostgreSQL CPU从日常的20%瞬间飙到100%，大量慢查询堆积在pg_stat_activity中，Core API响应超过30秒触发HTTP超时。docker push客户端收到502后自动重试——但重试进一步增加了数据库负载，形成"重试→超时→再重试"的恶性循环，最终整个Harbor集群约30分钟不可用，影响了促销上线窗口。

**痛点四：GC策略缺失导致Blob碎片化，存储吞吐暴跌。** 由于CI构建的高频push→delete→push操作（每次构建生成新tag后删除旧tag），同一镜像层的不同版本散落在文件系统的各处。Registry的Blob存储本质上是一个基于内容寻址的文件系统，频繁删除会在ext4文件系统上留下大量碎片。磁盘吞吐从初始的300MB/s逐步降到50MB/s——运维用`fio`测试才发现随机IOPS已经从5000降到800。更要命的是，默认的GC任务每周日凌晨2点才运行，且只清理未引用的Blob，从不做碎片整理。

---

## 2 项目设计——剧本式交锋对话

**场景：性能调优专项会议室，小胖拿着一杯奶茶推门而入，桌上摆满了监控大盘的打印件。**

**【第一轮：小胖开球——加机器？】**

**小胖**："各位大佬，Harbor性能不行这事儿——不就是加机器嘛！CPU不够加CPU，内存不够加内存，跟打游戏升级装备一样，氪金就完了呗？"

**大师**（放下手里的咖啡杯）："小胖，你说的是'纵向扩容'——这是最贵也最偷懒的调优方式。我给你算笔账：一台64核256G的服务器月租2万，但改几行配置就能提升30-50%性能，成本为零。你现在Harbor的PostgreSQL连shared_buffers都还在用默认的128MB——这相当于你买了一辆法拉利但只在一档开！"

**小胖**："128MB？我以为PostgreSQL会自己优化……"

**大师**："PostgreSQL不会自动调整shared_buffers，它默认只占128MB，不管你服务器有多少内存。这就是所谓的'出厂设定陷阱'——你觉得跑起来就行，实际上它在用最低配跑。"

"Harbor性能调优分四层，从上到下像一个金字塔："
```
Layer 1: 系统层 → 内核参数、文件描述符上限、磁盘IO调度器（地基）
Layer 2: Docker层 → 日志驱动、存储驱动、资源cgroup限制（承重墙）
Layer 3: Harbor层 → harbor.yml参数、连接池、Token有效期（水电管道）
Layer 4: 数据库层 → PostgreSQL配置、索引策略、连接管理（心脏）
```
**技术映射**：性能瓶颈遵循"帕累托法则（80/20原则）"——80%的性能问题来自20%的配置不当。最常见的"三大隐形杀手"：(1) PostgreSQL连接数不足导致请求排队；(2) Token签发未设合理有效期，重复计算；(3) Registry的Blob存储与文件系统不匹配（如在HDD上用ext4存储百万级小文件）。

**【第二轮：小白追问——Token有效期越长越好？】**

**小白**（推了推眼镜）："大师，您说Token有效期从30分钟调到60分钟可以减少签发频率。但我在想——如果一个用户的权限被撤销了，他手里的Token在60分钟内依然有效，这不是安全风险吗？有没有一个平衡点？"

**大师**："问得好，这是个经典的'性能vs安全'权衡。Token有效期本质上是一个trade-off：设短，安全但性能差；设长，性能好但有权限窗口。业界最佳实践是——"

"**分层设置**：普通用户Token设为30分钟，CI/CD服务账号的Robot Token设为60分钟甚至90分钟。因为CI是高频使用者，且Robot账号权限变更极少。Harbor 2.9+支持为Robot Account单独设置过期时间。"
```yaml
# harbor.yml——分层Token策略
core:
  token_expiration: 30        # 普通用户30分钟
  robot_token_expiration: 90  # Robot账号90分钟（CI高频场景）
```
"另外，Registry自身还有一个认证缓存层——`authtoken.cachettl`。即使Core说Token有效30分钟，Registry可以在内存中缓存认证结果一段时间，避免每次blob请求都回调Core验证。这三层缓存（Core签发→Registry验证→Redis Session）像一个'认证接力赛'——任何一棒的缓存没配好，整个链路都会变慢。"

**小白**："原来如此——所以不是简单地调大一个值，而是要把认证链路拆开看，每个环节都有缓存空间。"

**【第三轮：小胖再问——PostgreSQL调优有啥诀窍？】**

**小胖**（挠了挠头）："那PostgreSQL这块……我看shared_buffers要设内存的25%，effective_cache_size要设75%——这不就是把内存'大锅饭'一样分出去吗？有没有傻瓜式记忆法？"

**大师**："你这个'大锅饭'比喻有点意思！让我给你一个'后厨类比'——"

"- **shared_buffers（4GB）** = 厨师备菜台——在大火炒菜时，食材直接放台面上，伸手就拿。太小了就得频繁开冰箱（磁盘），太大了备菜台占地方（操作系统也需要内存做文件缓存）。
- **effective_cache_size（12GB）** = 菜单预估——告诉厨师"今天的客人大概会点这12G范围内的菜"，厨师就会提前规划备菜顺序，不会临时翻冰箱。
- **work_mem（64MB）** = 每个灶台的砧板——单个查询做排序/哈希操作时使用的临时内存。设太小排序需要写临时文件（磁盘），设太大100个并发查询会吃掉6.4GB内存。
- **random_page_cost（SSD=1.1，HDD=4.0）** = 走路速度——随机IO和顺序IO的代价比。SSD上随机读和顺序读差不多快（1.1：1），HDD上随机读慢4倍（4：1）。PostgreSQL据此决定用索引扫描还是全表扫描。"

**小胖**："那`max_connections`呢？"

**大师**："PostgreSQL采用'每连接一进程'模型——每个连接fork一个操作系统进程，占5-10MB内存。300个连接理论上占用1.5-3GB。但真正的杀手是——当连接数超过200时，即使连接空闲，PG内部的snapshot管理开销也会呈指数增长。所以公式是：`max_open_conns = max_connections × 0.5~0.7`，给系统留喘息空间。"

**技术映射**：PG的查询计划器（planner）依赖`random_page_cost`和`effective_cache_size`来决策"用索引还是直接扫全表"。在SSD上把`random_page_cost`从默认的4.0降到1.1，相当于告诉PG"随机读和顺序读代价差不多"，PG会更积极地使用索引——在很多场景下这能带来5-10倍的查询性能提升。

**【第四轮：小白深挖——系统层参数真的有用吗？】**

**小白**："大师，Linux内核参数那块——`net.core.somaxconn`、`tcp_tw_reuse`，这些跟Harbor一个HTTP应用有什么关系？调了真的能感知到吗？"

**大师**："这个问题触及了'全栈调优'的精髓。我举个具体例子——"

"当100个CI worker同时`docker push`时，每个push都会建立到Registry的HTTPS连接。TCP三次握手后进入`TIME_WAIT`状态——默认情况下这个状态持续60秒。如果连接速率超过`/proc/sys/net/ipv4/tcp_max_tw_buckets`（默认18000），新连接就会被直接丢弃——表现为随机的`Connection refused`。

而`net.core.somaxconn`控制了每个端口上等待accept的连接队列长度。Registry默认的HTTP Server（Go net/http）的listen backlog通常是128。当瞬间涌入300个连接请求时，超过128个的会被内核直接丢弃——客户端那边就报`Connection timeout`。这就像一个餐厅：门口排队区（somaxconn）太小，客人来了没地方站就直接走了，餐厅里面明明还有空位（worker）也没用。

另外，`vm.swappiness=10`告诉内核'尽量别用Swap'——PostgreSQL的shared_buffers如果在Swap里，一次查询可能需要从磁盘读回来，性能直接下降100倍。这就是为什么很多DBA说'PostgreSQL碰到Swap等于挂了'。"

**小白**："那我明白了——系统层参数不是直接让应用变快，而是消除'基础设施的漏斗'，让上层应用的吞吐能力不被底层限制。"

**大师**："精准总结！调优的本质不是让单个请求变快，而是**消除系统的短板（bottleneck）**，让吞吐量达到理论最大值。"

---

## 3 项目实战

### 环境要求

| 组件 | 版本/规格 | 用途 |
|------|----------|------|
| Harbor | v2.10.0+ | 主调优对象 |
| OS | Ubuntu 22.04 / CentOS 8+ | 内核参数调优 |
| Docker Engine | 24.0+ | 存储驱动与日志调优 |
| PostgreSQL | 15.x（Harbor内置） | 数据库参数与索引优化 |
| 压力测试工具 | wrk 4.x + go-stress-testing | 基准测试与A/B对比 |
| profiling工具 | Go pprof(go tool pprof) | CPU/内存火焰图分析 |
| 磁盘 | SSD（NVMe推荐） | random_page_cost调优前提 |

### 步骤一：性能基准采集——建立调优前的"快照"

**目标**：记录当前性能基线，后续每步调优后对比。

```bash
# ===== 1. 采集系统级基线 =====
# CPU核数与内存
lscpu | grep -E "^CPU\(s\)|Model name" && free -h

# 磁盘IOPS（假设Registry存储在/data）
fio --name=randread --ioengine=libaio --iodepth=32 --rw=randread \
    --bs=4k --direct=1 --size=1G --numjobs=4 --runtime=60 \
    --group_reporting --filename=/data/fio_test

# 预期输出示例（SSD）:
#   read: IOPS=180k, BW=702MiB/s

# 文件描述符上限
ulimit -n
cat /proc/sys/fs/file-max

# ===== 2. 采集PostgreSQL基线 =====
docker exec harbor-db psql -U postgres -c "
SELECT name, setting, unit, context 
FROM pg_settings 
WHERE name IN ('max_connections','shared_buffers','effective_cache_size',
               'work_mem','maintenance_work_mem','random_page_cost');
"
# 预期输出：大部分为默认值（shared_buffers=128MB, max_connections=100）

# ===== 3. 采集API性能基线（使用wrk） =====
# 安装wrk
apt-get install -y wrk || brew install wrk

# 测试仓库列表API（需要Basic Auth）
echo "admin:Str0ng@Admin2024" > /tmp/harbor_creds
wrk -t4 -c50 -d30s --latency \
    -s <(cat <<'SCRIPT'
        wrk.method = "GET"
        wrk.path = "/api/v2.0/projects/1/repositories?page_size=50"
        wrk.headers["Authorization"] = "Basic " .. 
            (function() 
                local f = io.open("/tmp/harbor_creds"):read("*all")
                return require("base64").encode(f:gsub("\n",""))
            end)()
SCRIPT
    ) https://harbor.company.com

# 预期输出示例：
#   Requests/sec:    45.23
#   Latency Avg:     820.15ms
#   Latency P99:     2850.33ms  ← 这就是问题！
```

### 步骤二：操作系统层调优——夯实地基

**目标**：消除TCP连接瓶颈和文件描述符限制，优化磁盘IO。

```bash
# ===== 写入sysctl参数 =====
cat >> /etc/sysctl.conf <<'EOF'
# ----- TCP连接优化 -----
# 全连接队列最大值（每个监听端口）
net.core.somaxconn = 65535
# 半连接队列最大值（SYN_RECV状态）
net.ipv4.tcp_max_syn_backlog = 8192
# 允许快速复用TIME_WAIT状态的连接
net.ipv4.tcp_tw_reuse = 1
# TIME_WAIT socket最大数量
net.ipv4.tcp_max_tw_buckets = 5000

# ----- 文件描述符 -----
fs.file-max = 2097152
# 单个进程可打开的文件数（需配合ulimit）
fs.nr_open = 2097152

# ----- 内存与Swap -----
# 尽量不用Swap（对PostgreSQL至关重要）
vm.swappiness = 10
# 脏页比例（减少突发IO压力）
vm.dirty_ratio = 15
vm.dirty_background_ratio = 5

# ----- 网络缓冲区 -----
net.core.rmem_max = 134217728
net.core.wmem_max = 134217728
net.ipv4.tcp_rmem = 4096 87380 134217728
net.ipv4.tcp_wmem = 4096 65536 134217728
EOF

# 立即生效
sysctl -p

# 验证
sysctl net.core.somaxconn
# 预期输出: net.core.somaxconn = 65535

# ===== 设置文件描述符限制（Systemd环境） =====
mkdir -p /etc/systemd/system/docker.service.d
cat > /etc/systemd/system/docker.service.d/limits.conf <<'EOF'
[Service]
LimitNOFILE=1048576
LimitNPROC=infinity
EOF

systemctl daemon-reload && systemctl restart docker

# 验证Docker daemon的limits
docker info | grep -i "file descriptors"
# 预期输出:  File Descriptors: 1048576
```

### 步骤三：Docker Engine调优——优化容器运行时

**目标**：控制日志膨胀，确认存储驱动。

```bash
# ===== 配置daemon.json =====
cat > /etc/docker/daemon.json <<'EOF'
{
  "log-driver": "json-file",
  "log-opts": {
    "max-size": "100m",
    "max-file": "3"
  },
  "storage-driver": "overlay2",
  "default-ulimits": {
    "nofile": {
      "Name": "nofile",
      "Hard": 65535,
      "Soft": 65535
    }
  }
}
EOF

systemctl restart docker

# 验证存储驱动
docker info | grep "Storage Driver"
# 预期输出: Storage Driver: overlay2

# 检查是否有大量未清理的镜像/容器占用磁盘
docker system df
# 如果RECLAIMABLE > 30%，执行清理：
docker system prune -a -f --filter "until=72h"
```

### 步骤四：Harbor配置调优——核心参数优化

**目标**：优化连接池、Token策略和异步任务。

```bash
cd /opt/harbor

# ===== 备份原配置 =====
cp harbor.yml harbor.yml.bak.$(date +%Y%m%d)

# ===== 修改harbor.yml =====
# 编辑 database 段
sed -i '/^database:/,/^[a-z]/{
  /max_idle_conns/c\  max_idle_conns: 50
  /max_open_conns/c\  max_open_conns: 200
}' harbor.yml

# 更安全的做法——手动编辑harbor.yml：
```

编辑 `harbor.yml`，确保以下参数正确：

```yaml
# harbor.yml —— 生产级性能配置
database:
  max_idle_conns: 50       # 空闲连接池，不要设为0（0=无限制，会泄漏连接）
  max_open_conns: 200      # 总连接池上限 = PG max_connections * 0.6~0.7

core:
  token_expiration: 60     # 普通用户Token有效期（分钟）
  robot_token_expiration: 43200  # Robot账号30天（CI流水线用）

registry:
  relativeurls: true       # 返回相对URL，减少TLS开销
  # Registry自身的Token缓存（减少回调Core验证）
  token:
    realm: https://harbor.company.com/service/token
    authtoken:
      cachettl: 10m        # 认证结果在Registry内存中缓存10分钟

jobservice:
  max_job_workers: 20      # Worker线程数（建议=CPU核数）

metric:
  enabled: true            # 开启Prometheus Metrics（下一章用到）
  port: 9090
```

```bash
# ===== 重新部署Harbor =====
./prepare
docker compose down
docker compose up -d

# 等待所有服务就绪（约30-60秒）
watch -n 2 'docker compose ps'

# 验证改后配置
curl -s -u admin:Str0ng@Admin2024 \
    https://harbor.company.com/api/v2.0/systeminfo | \
    jq '.auth_mode, .registry_url'
```

### 步骤五：PostgreSQL性能调优——心脏手术

**目标**：让PostgreSQL充分利用32GB物理内存。

```bash
# ===== 确认PG数据目录位置 =====
docker inspect harbor-db | jq '.[0].Mounts[] | select(.Destination=="/var/lib/postgresql/data") | .Source'

# ===== 编辑postgresql.conf（在容器内部） =====
docker exec -it harbor-db bash

# 先查看当前内存使用情况
free -h
# 假设输出: total=32GB, available=24GB

# 备份配置
cp /var/lib/postgresql/data/postgresql.conf \
   /var/lib/postgresql/data/postgresql.conf.bak

# 修改关键参数
cat >> /var/lib/postgresql/data/postgresql.conf <<'EOF'

# ===== Harbor生产级PG配置 =====
max_connections = 300

# 内存配置（基于32GB物理内存）
shared_buffers = 8GB                # 25% RAM → 8GB
effective_cache_size = 24GB         # 75% RAM → 24GB
work_mem = 64MB                     # 排序/哈希操作：每个操作的内存
maintenance_work_mem = 2GB          # VACUUM/索引维护内存
wal_buffers = 64MB                  # WAL缓冲区（默认16MB）

# SSD优化
random_page_cost = 1.1              # SSD：随机读≈顺序读
effective_io_concurrency = 200      # SSD允许200个并发IO
seq_page_cost = 1.0                 # 顺序读代价基准

# 查询计划器
default_statistics_target = 100     # 统计采样精度（默认100已够用）

# WAL与检查点（减少写压力）
checkpoint_completion_target = 0.9  # 检查点分散到90%的周期内
max_wal_size = 8GB                  # WAL最大8GB（默认1GB太小）
min_wal_size = 2GB

# 自动Vacuum（防止事务ID回卷和表膨胀）
autovacuum_max_workers = 5
autovacuum_naptime = 30s            # 更频繁地检查
EOF

exit  # 退出容器
```

```bash
# ===== 使PG配置生效 =====
# 方式一：热加载（部分参数需要重启）
docker exec harbor-db psql -U postgres -c "SELECT pg_reload_conf();"

# 方式二：完全重启（shared_buffers等需要）
docker restart harbor-db

# 验证参数生效
docker exec harbor-db psql -U postgres -c "
SELECT name, setting, unit 
FROM pg_settings 
WHERE name IN ('shared_buffers','effective_cache_size','max_connections','random_page_cost')
  AND source != 'default';
"
# 预期输出：
#          name           | setting | unit
# ------------------------+---------+------
#  effective_cache_size   | 25165824| 8kB  ← 24GB
#  max_connections        | 300     |
#  random_page_cost       | 1.1     |
#  shared_buffers         | 1048576 | 8kB  ← 8GB
```

### 步骤六：数据库索引优化——精准加速

**目标**：为高频慢查询创建覆盖索引。

```sql
-- 连接PG
docker exec -it harbor-db psql -U postgres -d registry

-- 查看当前耗时最长的查询类型
SELECT query, calls, mean_exec_time, total_exec_time
FROM pg_stat_statements  -- 需先启用pg_stat_statements扩展
ORDER BY total_exec_time DESC
LIMIT 10;

-- 如果未启用pg_stat_statements：
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;
SELECT pg_reload_conf();

-- ===== 创建关键索引 =====

-- 1. Artifact查询加速（按仓库+推送时间查询，Portal常用）
CREATE INDEX IF NOT EXISTS idx_artifact_repo_push 
    ON artifact (repository_id, push_time DESC);
-- 解释：Portal的仓库列表页按推送时间倒序展示，这个复合索引将顺序扫描变为索引扫描

-- 2. Blob去重查询（GC扫描时的高频查询）
CREATE INDEX IF NOT EXISTS idx_blob_digest_status 
    ON blob (digest, status);
-- 解释：GC需要找出哪些blob不再被任何artifact引用，digest是核心匹配字段

-- 3. Tag查询加速（docker pull时按repo+tag查找artifact）
CREATE INDEX IF NOT EXISTS idx_tag_repo_name 
    ON tag (repository_id, name);
-- 解释：docker pull harbor.company.com/project/repo:tag时，
-- Registry调用Core API按repository_id+name查找对应的artifact

-- 4. 项目成员权限查询（Token签发时的权限校验）
CREATE INDEX IF NOT EXISTS idx_project_member_project_entity 
    ON project_member (project_id, entity_type, entity_id);
-- 解释：每次Token签发都要查"这个用户在这个项目里有什么角色"

-- ===== 更新表统计信息 =====
ANALYZE artifact;
ANALYZE blob;
ANALYZE tag;
ANALYZE project_member;

-- 验证索引使用情况
SELECT 
    schemaname, tablename, indexname, idx_scan, idx_tup_read, idx_tup_fetch
FROM pg_stat_user_indexes
WHERE schemaname = 'public'
ORDER BY idx_scan DESC;
-- 对比创建索引前后的idx_scan变化
```

### 步骤七：Go pprof 性能剖析——定位Core热点函数

**目标**：用火焰图精准定位Core代码中的CPU热点。

```bash
# ===== 开启pprof =====
# 方式1：修改docker-compose.yml中core的environment
#   - CORE_ARGS=-pprof=true
# 方式2：通过环境变量注入
docker exec harbor-core /harbor/harbor_core -pprof=true &

# 实际上最佳做法是修改docker-compose.yml：
cd /opt/harbor
# 在core服务的environment段增加：
#   - CORE_ARGS=-pprof=true
# 或在harbor.yml中配置后重新prepare

# ===== 采集CPU Profile =====
# 先确认pprof端口（默认6060）
docker exec harbor-core netstat -tlnp | grep 6060

# 采集30秒的CPU采样
curl -o /tmp/cpu_$(date +%H%M).prof \
    http://localhost:6060/debug/pprof/profile?seconds=30

# 采集堆内存Profile
curl -o /tmp/heap_$(date +%H%M).prof \
    http://localhost:6060/debug/pprof/heap

# ===== 分析CPU Profile =====
go tool pprof /tmp/cpu_1430.prof

# 在pprof交互终端中：
# (pprof) top 20
# 预期看到类似：
#   Showing nodes accounting for 12.5s, 78.13% of 16s total
#   flat  flat%   sum%    cum   cum%
#   3.2s 20.00% 20.00%  3.2s 20.00%  runtime.cgocall
#   2.1s 13.13% 33.13%  4.5s 28.13%  github.com/goharbor/harbor/src/core/auth.(*)...  ← 认证相关
#   1.8s 11.25% 44.38%  1.8s 11.25%  github.com/lib/pq.(*conn).exec

# (pprof) list auth
# 查看auth包中具体哪个函数最耗时

# (pprof) web
# 生成火焰图（需要安装graphviz: apt install graphviz）

# 生成可分享的SVG火焰图
go tool pprof -svg /tmp/cpu_1430.prof > /tmp/harbor_cpu_flame.svg
```

### 步骤八：A/B 对比验证——量化调优收益

**目标**：用相同压测脚本对比调优前后的性能。

```bash
# ===== 调优后重新执行基准测试 =====
# 复用步骤一的测试脚本

wrk -t4 -c50 -d30s --latency \
    -s /tmp/benchmark_script.lua \
    https://harbor.company.com/api/v2.0/projects/1/repositories?page_size=50

# ===== 对比结果 =====
# 调优前（示例）：
#   Requests/sec:    45.23
#   Latency Avg:     820.15ms
#   Latency P99:     2850.33ms
#
# 调优后（示例）：
#   Requests/sec:    312.50    ← 提升 6.9x
#   Latency Avg:     145.20ms  ← 降低 82%
#   Latency P99:     420.10ms  ← 降低 85%

# ===== 测试并发Push =====
# 调优前：20个并发push有5-8个失败
# 调优后：20个并发push全部成功

for i in $(seq 1 20); do
  docker pull alpine:3.19 > /dev/null 2>&1
  docker tag alpine:3.19 harbor.company.com/perf-test/app-$i:v1
  docker push harbor.company.com/perf-test/app-$i:v1 &
done
wait
echo "成功Push数: $(docker image ls harbor.company.com/perf-test/* | wc -l)"
```

### 常见坑与解决方案

| # | 坑 | 根因 | 解决 |
|---|-----|------|------|
| 1 | 改了`postgresql.conf`但参数未生效 | PG的`shared_buffers`等参数需要**完全重启**而非`pg_reload_conf()`；仅部分参数支持热加载。 | 执行`docker restart harbor-db`而非`pg_reload_conf()`。用`SELECT name,setting,source FROM pg_settings WHERE name='shared_buffers'`确认source变为`configuration file`。 |
| 2 | `max_open_conns`设太高（如900）反而更慢 | Harbor Core默认`max_open_conns=900`直接占满PG的`max_connections=100`，导致Registry等其他组件无连接可用，请求排队超时。 | 遵循公式：`max_open_conns = PG max_connections × 0.5~0.7`。例如PG设300，则Core设150-200。 |
| 3 | `work_mem`设太大导致OOM | `work_mem=256MB`时，如果有100个并发查询都做排序，PG会尝试分配25.6GB内存——超过物理内存后触发OOM Killer。 | `work_mem`不是"每个连接"而是"每个操作"的内存。公式：`work_mem ≤ (总内存 - shared_buffers) / (max_connections × 2)`。典型值32-64MB。 |
| 4 | 磁盘IO调度器用CFQ导致SSD性能受限 | CFQ（完全公平队列）是为HDD设计的，会插入不必要的IO空闲等待，SSD上浪费30%+的随机IOPS。 | 查看当前调度器：`cat /sys/block/sda/queue/scheduler`。SSD设为：`echo "none" > /sys/block/sda/queue/scheduler`（NVMe）或`echo "mq-deadline"`（SATA SSD）。 |
| 5 | GC后Registry重启导致pull变慢 | Registry重启后内存中的blob元数据缓存丢失，前几分钟每次pull都要从磁盘读取blob索引——相当于"冷启动"。 | Registry配置`storage.cache.blobdescriptor=redis`将blob索引缓存到Redis中，重启后无需重新扫描磁盘。 |

---

## 4 项目总结

### 4.1 调优前后对比

| 维度 | 调优前 | 调优后 | 提升幅度 |
|------|--------|--------|---------|
| API P99延迟 | 2850ms | 420ms | ↓ 85% |
| 并发Push成功率（20并发） | 60% (8失败) | 100% | ↑ 67% |
| Portal仓库列表加载 | 25s | 3s | ↓ 88% |
| PG慢查询数/分钟 | 45 | 3 | ↓ 93% |
| 磁盘读吞吐（Blob） | 50MB/s | 280MB/s | ↑ 460% |
| PG CPU峰值 | 100% | 35% | ↓ 65% |
| Token签发耗时P50 | 2.8s | 0.3s | ↓ 89% |

### 4.2 适用场景

| 场景 | 说明 |
|------|------|
| 日均pull > 10万次的公共基础镜像仓库 | Token缓存收益最大 |
| CI/CD构建频率 > 100次/小时 | 连接池+索引优化效果显著 |
| 仓库数量 > 1000个 | Portal分页+索引+shared_buffers缺一不可 |
| Harbor部署在HDD服务器上 | IO调度器+random_page_cost调整至关重要 |
| 多Region同步场景 | 网络缓冲区调优减少跨Region复制延迟 |

**不适用场景**：
- 个人开发者单机Harbor（日pull < 100次）——默认配置完全够用，过度调优浪费精力
- 仅使用S3外部存储的Registry——磁盘IO调度器调优无效

### 4.3 五项注意事项

1. **调优是迭代过程，不是一次性操作。** 每次只改一个层级的参数，压测验证后再改下一层——否则出了问题无法定位是哪个改动导致。
2. **`shared_buffers`不要超过物理内存的40%。** PostgreSQL依赖操作系统文件缓存做"双缓存"，shared_buffers太大反而抢占了OS缓存空间。
3. **索引不是越多越好。** 每个索引都会降低INSERT/UPDATE速度，因为写入时需要同步更新索引。只建"高频查询"对应的索引。
4. **`token_expiration`不要超过24小时。** 过长的Token有效期意味着被泄露的Token在一天内仍然可用——这是安全审计的红线。
5. **在维护窗口内做PG参数变更。** `shared_buffers`和`max_connections`的修改需要重启PG，会导致Harbor Core短时不可用（约30秒）。务必在维护窗口操作。

### 4.4 常见故障速查

| 故障现象 | 根因 | 快速定位命令 |
|----------|------|-------------|
| `docker pull` 报 `denied: requested access to the resource is denied` | Token签发超时（PG连接耗尽） | `docker logs harbor-core --tail 50 \| grep -i "token\|timeout"` |
| `docker push` 卡在 `Preparing` 阶段不动 | Registry写入磁盘IO阻塞 | `iostat -x 1` 查看`%util`是否100% |
| Harbor Portal 页面白屏 > 30秒 | PG全表扫描project表（缺索引） | PG中执行：`EXPLAIN ANALYZE SELECT * FROM project WHERE ...` |

### 4.5 深度思考

1. **Harbor Core用Go编写，GC（垃圾回收）会影响API延迟。如何通过`GOMEMLIMIT`和`GOGC`环境变量限制Core的内存使用，避免GC STW（Stop-The-World）导致P99毛刺？**（提示：Go 1.19+支持`GOMEMLIMIT=2GiB`软限制内存，配合`GOGC=50`降低GC频率）

2. **当前所有调优都在单实例上。如果要在3个数据中心各部署一套Harbor并通过复制策略同步，如何在保证数据一致性的前提下，让用户总是从最近的数据中心拉取镜像？**（提示：涉及DNS Geo-Routing + Registry Proxy Cache + Harbor复制策略的"推模式"vs"拉模式"选择）

---

> 下一章预告：第27章将搭建Harbor的Prometheus监控体系，从黑盒到白盒全面掌控系统健康。
