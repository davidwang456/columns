# 第38章：大规模 GitLab 性能调优

## 1. 项目背景

> **业务场景**：某公司 GitLab 实例从 100 用户增长到 5000 用户后，性能全面崩盘——开发高峰期（上午 10 点和下午 3 点）MR 列表页加载耗时 15 秒，`git push` 超时率高达 30%，CI Pipeline 触发延迟从 2 秒飙升到 40 秒。运维团队给服务器加内存（32GB→64GB）和 CPU（8 核→16 核），但效果甚微——MR 列表页仅从 15 秒降到 13.5 秒，`git clone` 依然超时。紧急升级硬件花了大笔钱，问题却没有解决。

运维花了 2 周时间做性能剖析，发现了真正的问题分布：50% 的慢请求源于 PostgreSQL 缺少关键复合索引，导致 MR 和 Issue 列表每次全表扫描数十万行记录；30% 是因为 Gitaly 的 PackObjectsCache 未启用，每次 clone/fetch 都重新计算 Git 对象包——这是纯 CPU 密集型操作；15% 是因为 Sidekiq 队列和 Rails 缓存共享同一个 Redis 实例，Sidekiq 大量重试任务抢占了 Redis 的内存和 IO，导致 Rails 缓存全部被淘汰、页面渲染走全量查询；剩下 5% 是 Rails 的 N+1 查询问题。修复这些软件层面的瓶颈后，MR 列表页从 15 秒降到了 1.5 秒。加硬件的回报仅 10%，调软件的回报是 90%。

**痛点放大**：大规模 GitLab 的性能调优涉及五个核心维度——PostgreSQL（慢查询分析 + 连接池）、Gitaly（对象缓存 + RPC 并发控制）、Redis（内存策略 + 三实例分离）、Puma/Rails（线程池 + 连接池）、Sidekiq（队列分组 + 并发调优）。每个维度都有专属的诊断工具和优化手段。不掌握这五维调优方法论，加再多硬件也是治标不治本——就像给一辆爆胎的车加更大马力，跑得再快也是歪的。

---

## 2. 项目设计——剧本式交锋对话

**场景**：性能调优专项作战会议室，白板上画着 GitLab 五个核心组件的架构图，每个组件旁边标注着当前的瓶颈指标（红色数字）。桌上一堆监控曲线图，全是飙升的延迟和错误率。

---

**小胖**（指着白板上 PostgreSQL 旁边标的 "avg query 450ms"）："大帅，我们加内存从 32G 到 64G，CPU 也从 8 核翻到 16 核——这不科学啊！内存和 CPU 都翻倍了，MR 列表页还是 15 秒？这跟没加一样啊！"

**大师**："因为你的瓶颈根本不在内存。看这条 SQL——`SELECT * FROM merge_requests WHERE project_id = 123 ORDER BY updated_at DESC LIMIT 20`，每次 MR 列表页都要执行。它没有用到正确的组合索引，PostgreSQL 只能全表扫描 10 万条 MR 记录，然后再排序、再取前 20 条。加内存不能让磁盘 IO 变快，加 CPU 也不能让一次扫描跳过 99980 行不必要的数据。"

**小胖**："那怎么知道哪些 SQL 缺索引？我们有上万个表，总不能一个一个看吧？"

**大师**："PostgreSQL 自带 `pg_stat_statements` 扩展——它就像一个'SQL 摄像头'，记录每条 SQL 的执行次数、平均耗时、总耗时、返回行数。你按 `total_time` 降序排列，Top 20 就是当前最大的性能杀手。GitLab 自带的 Prometheus Exporter 也暴露了 `gitlab_sql_duration_seconds` 指标，按 endpoint 聚合，能一眼看出慢端口的 SQL 瓶颈。"

**小白**："`pg_stat_statements` 会自动记录所有 SQL？对生产库有性能开销吗？"

**大师**："好问题。它默认采样而不是全量记录——`pg_stat_statements.max = 5000` 表示只追踪前 5000 种归一化的 SQL 模板，开销通常小于 2%。但这是推断性的——生产环境建议先在 staging 验证。**技术映射**——`pg_stat_statements` 就像高速公路上的测速摄像头：不是每辆车都拍（采样），但每个超速点都会被记录（热点 SQL），额外消耗可以忽略不计。"

---

**小白**（翻着手册）："Gitaly 的 PackObjectsCache——我看文档说启用后 clone 能加速很多，但也写着'会占用额外内存'。到底要不要开？会不会把内存撑爆？"

**大师**："先理解原理。每次 `git clone` 或 `git fetch` 时，Gitaly 需要做两件事：一是找出所有需要的 Git 对象（commit、tree、blob），二是把这些对象序列化打包成一个 `.pack` 文件发回客户端。打包是纯 CPU 密集操作——一个大仓库的 pack 计算可能吃掉 2-4 个 CPU 核心，耗时 30 秒以上。如果 50 个人同时 clone，CPU 直接打满。"

**小胖**："所以缓存了就不用算了？这跟食堂预制菜一个道理嘛——提前把菜做好，来了人直接热！"

**大师**："小胖说对了！PackObjectsCache 就是这个思路——它把最近计算过的 pack 结果缓存在磁盘上（`/var/opt/gitlab/gitaly/pack-objects-cache`），相同请求直接读取缓存返回，不需要重新计算。代价是磁盘和内存——缓存目录的大小取决于活跃仓库的数量和 pack 体积。对于日均 500 次 clone 的实例，命中率能做到 80% 以上，CPU 占用直接腰斩。"

**小白**："那并发限制呢？为什么要限制每个仓库的 RPC 并发数？"

**大师**："Gitaly 按 gRPC 方法暴露出几十种 RPC 调用，不同 RPC 的资源消耗天差地别。`GarbageCollect` 要对整个仓库做 repack，极端情况下可以吃 8 个核心、跑 10 分钟。如果不限制，一个开发手贱触发了大仓库的 GC，所有人的 push 和 clone 都会被等到的 RPC 请求挨饿——这就是'吵闹邻居'问题。**技术映射**——并发限制就像高速公路上的大货车限速和专用车道：不能让一辆满载的货车占满所有车道，小轿车（普通 clone/push）才能正常通行。"

---

**小胖**："Redis 实例分离是啥操作？我们现在就一个 Redis，存缓存、跑队列、记 Rate Limit——它又没报过故障，为啥要拆？"

**大师**："Redis 在 GitLab 中承担了三类完全不同的职责。第一类是**缓存**——session、view cache、MR diff 缓存，访问频率极高但可以丢失（丢了重新算就是慢一点）。第二类是**队列**——Sidekiq 的消息队列，绝对不能丢（丢了任务就丢了）。第三类是**共享状态**——Rate Limiting 计数器、分布式锁，需要强一致性和原子操作。这三类数据的访问模式完全不同：缓存需要大内存、允许淘汰；队列需要持久化（RDB + AOF）；共享状态延迟敏感度最高。"

**小白**："混在一起会有什么具体问题？"

**大师**："经典故障——Sidekiq 因上游故障产生大量重试任务，消息队列膨胀把 Redis 内存吃满。Redis 的 `maxmemory-policy` 如果你设了 `allkeys-lru`，它会毫不犹豫地淘汰缓存 key——所有人的 session 都丢了，用户被迫重新登录，然后登录请求又打到数据库，数据库再被打死……这就是级联故障。**技术映射**——Redis 实例分离就像把厨房的冷柜、灶台、传菜台分成三个独立台面：冷柜断电不影

响了灶台炒菜，传菜台有油污也不会污染冷柜食材。"

---

## 3. 项目实战

### 环境准备

| 工具 | 用途 | 版本要求 |
|------|------|---------|
| GitLab Omnibus | 调优目标实例 | 17.x CE/EE |
| pg_stat_statements | PostgreSQL 慢查询分析 | PG 14+ 内置扩展 |
| PgBouncer | 数据库连接池 | 1.21+ |
| Prometheus + Grafana | 实时指标监控 | 内置集成 |
| redis-cli | Redis 诊断 | 7.0+ |
| curl / jq | API 验证 | 任意版本 |

### 分步实现

#### 步骤1：PostgreSQL 调优——慢查询诊断 + 复合索引 + PgBouncer

**目标**：通过 `pg_stat_statements` 定位慢查询，添加针对性复合索引，将数据库查询延迟降低 70% 以上。

**(1) 启用并查询慢 SQL**

```sql
-- postgresql.conf 中启用（需重启 PostgreSQL）
-- shared_preload_libraries = 'pg_stat_statements'
-- pg_stat_statements.track = all

-- 查找 Top 20 高消耗 SQL（按总耗时排序）
SELECT
  queryid,
  LEFT(query, 150) AS query_preview,
  calls,
  mean_exec_time::numeric(10,2) AS avg_ms,
  total_exec_time::numeric(10,2) AS total_ms,
  rows / NULLIF(calls, 0) AS avg_rows
FROM pg_stat_statements
WHERE dbid = (SELECT oid FROM pg_database WHERE datname = 'gitlabhq_production')
ORDER BY total_exec_time DESC
LIMIT 20;
-- 预期输出：Top 1 可能是 MR 列表查询，total_ms 可达数十万毫秒
-- 常见问题：无索引导致 Seq Scan，rows 值远超需要返回的行数
```

**(2) 添加关键复合索引（CONCURRENTLY 避免锁表）**

```sql
-- ⚠️ 使用 CONCURRENTLY 创建，不阻塞读写，但耗时长（大表需 30min-2h）
-- 生产环境建议在低峰期执行，并监控锁等待

-- MR 列表页核心索引（按项目、状态、更新时间排序）
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_mrs_project_state_updated
  ON merge_requests(project_id, state, updated_at DESC);

-- Issue 列表页索引 + INCLUDE 宽索引（避免回表）
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_issues_project_state_inc
  ON issues(project_id, state) INCLUDE (title, created_at, closed_at);

-- CI Pipeline 列表页索引
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_pipelines_project_status_id
  ON ci_pipelines(project_id, status, id DESC);

-- MR diff 文件查询索引
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_mr_diffs_mr_id
  ON merge_request_diffs(merge_request_id);

-- 创建后记得更新统计信息
ANALYZE merge_requests;
ANALYZE issues;
ANALYZE ci_pipelines;
-- 预期效果：再次查询 pg_stat_statements，mean_time 从 >200ms 降至 <5ms
```

**(3) 配置 PgBouncer 连接池**

```ini
;; /etc/pgbouncer/pgbouncer.ini
[databases]
gitlabhq_production = host=127.0.0.1 port=5432 dbname=gitlabhq_production

[pgbouncer]
listen_addr = 0.0.0.0
listen_port = 6432
auth_type = md5
auth_file = /etc/pgbouncer/userlist.txt
pool_mode = transaction           ;; ★ GitLab 推荐 transaction 模式
default_pool_size = 100           ;; 连接池大小 = Puma worker * db_pool * 0.5
max_client_conn = 500
server_idle_timeout = 60
client_idle_timeout = 0
```

```ruby
# gitlab.rb 中切换数据库连接到 PgBouncer
postgresql['enable'] = false                           # 如果 PgBouncer 在外置 PG 前
gitlab_rails['db_host'] = '127.0.0.1'
gitlab_rails['db_port'] = 6432                        # PgBouncer 端口
gitlab_rails['db_prepared_statements'] = false         # ★ transaction mode 必须关闭
# 执行 sudo gitlab-ctl reconfigure && sudo gitlab-ctl restart
```

**常见踩坑**：开启 PgBouncer transaction 模式后忘记关闭 `db_prepared_statements`，会导致 Rails 的 prepared statement 在连接归还池后失效，报 `PG::InvalidSqlStatementName` 错误。

---

#### 步骤2：Gitaly 调优——PackObjectsCache 启用 + RPC 并发限制

**目标**：开启 PackObjectsCache 降低 clone/fetch 的 CPU 消耗，通过并发限制防止重型 RPC 抢占资源。

```ruby
# gitlab.rb —— Gitaly 核心调优配置
gitaly['pack_objects_cache']['enabled'] = true
gitaly['pack_objects_cache']['dir'] = '/var/opt/gitlab/gitaly/pack-objects-cache'
gitaly['pack_objects_cache']['max_age'] = '20m'          # 缓存有效期，大仓库建议 10-20min
gitaly['pack_objects_cache']['dir_max_size'] = 50 * 1024 # 缓存目录最大 50GB

# ★ 按 RPC 类型设置并发限制——防止"吵闹邻居"
gitaly['concurrency'] = [
  {
    'rpc' => '/gitaly.RepositoryService/GarbageCollect',
    'max_per_repo' => 1     # GC 极度消耗资源，每仓库同时只允许 1 个
  },
  {
    'rpc' => '/gitaly.RepositoryService/RepackFull',
    'max_per_repo' => 1     # 全量 repack 同样限制
  },
  {
    'rpc' => '/gitaly.RepositoryService/PackObjects',
    'max_per_repo' => 2     # pack 计算消耗中等，限制 2 个并发
  },
  {
    'rpc' => '/gitaly.SmartHTTPService/PostReceivePack',
    'max_per_repo' => 5     # push 操作频繁，允许多并发
  },
  {
    'rpc' => '/gitaly.CommitService/.*',
    'max_per_repo' => 10    # commit 相关查询较轻量
  }
]

# 全局并发限制（对所有 RPC 生效）
gitaly['concurrency_per_repo_allowlist'] = [
  '/gitaly.RepositoryService/WriteRef'  # 免限 RPC（更新 ref，必须快速响应）
]
```

**验证效果**：

```bash
# 1. 查看 PackObjectsCache 命中率
curl -s http://localhost:9236/metrics | grep 'pack_objects_cache'
# gitaly_pack_objects_cache_hit_total{dir="/var/opt/gitlab/gitaly/pack-objects-cache"}
# gitaly_pack_objects_cache_miss_total{dir="/var/opt/gitlab/gitaly/pack-objects-cache"}
# 计算命中率：hit / (hit + miss) 目标 > 80%

# 2. 查看 RPC 并发队列等待
curl -s http://localhost:9236/metrics | grep 'gitaly_concurrency_limiting'
# gitaly_concurrency_limiting_in_progress    → 当前正在执行的请求数
# gitaly_concurrency_limiting_queued         → 排队等待的请求数（目标 < 5）
# gitaly_concurrency_limiting_acquiring_seconds → 获取并发槽位的等待时间（目标 P99 < 1s）
```

**常见踩坑**：`max_age` 设得太小（如 5 分钟）导致缓存命中率不足 30%；设得太大（如 2 小时）导致`dir_max_size` 被撑满。建议从 20 分钟起步，观察命中率和磁盘占用后微调。

---

#### 步骤3：Redis 调优——内存策略 + 三实例分离

**目标**：配置 Redis 内存淘汰策略防 OOM，将缓存、队列、共享状态拆分为三个独立 Redis 实例。

```ruby
# gitlab.rb —— Redis 内存策略配置
redis['maxmemory'] = '4gb'
redis['maxmemory_policy'] = 'volatile-lru'    # ★ 仅淘汰带 TTL 的 key（缓存类）
redis['maxmemory_samples'] = 5                # LRU 采样精度
redis['save'] = ['900 1', '300 10', '60 10000']  # RDB 持久化策略
redis['tcp_keepalive'] = 300                  # 防止连接断开

# ★★★ 三实例分离（大规模部署必须）
# 方案 A：三台独立 Redis 服务器（推荐，完全隔离）
gitlab_rails['redis_cache_instance']       = 'redis://redis-cache.internal:6379/0'
gitlab_rails['redis_queues_instance']      = 'redis://redis-queues.internal:6379/0'
gitlab_rails['redis_shared_state_instance'] = 'redis://redis-sharedstate.internal:6379/0'

# 方案 B：单机三端口（资源受限时的折中方案）
# 在同一台机器上用不同端口区分实例，至少隔离了内存空间
# redis_cache_instance:    port 6379, maxmemory 2gb, maxmemory-policy volatile-lru
# redis_queues_instance:   port 6380, maxmemory 4gb, maxmemory-policy noeviction (★ 队列不可丢)
# redis_shared_state_instance: port 6381, maxmemory 1gb, maxmemory-policy noeviction
```

**对不同 Redis 实例的差异化配置建议**：

| 实例 | 职责 | 推荐 maxmemory | 推荐淘汰策略 | 持久化 | 原因 |
|------|------|---------------|-------------|--------|------|
| Cache | Session/View Cache | 2-4 GB | volatile-lru | 关闭 | 缓存可重建 |
| Queues | Sidekiq 消息队列 | 4-8 GB | noeviction | RDB+AOF | 队列数据不可丢 |
| Shared State | Rate Limit/Lock | 1-2 GB | noeviction | RDB | 需要原子性 |

```bash
# 验证 Redis 内存状态
sudo gitlab-redis-cli -h redis-cache.internal INFO memory | grep -E "used_memory_human|maxmemory_human|evicted_keys"
# used_memory_human:3.2G  → 当前使用了 3.2G
# maxmemory_human:4.0G    → 上限 4G
# evicted_keys:12450       → 如果持续增长说明 maxmemory 需要扩大

# 验证 Sidekiq 队列积压
sudo gitlab-rake gitlab:sidekiq:queue_size
# 正常：每个队列 < 100，高危：任一队列 > 10,000
```

**常见踩坑**：三实例分离后忘记在 `gitlab.rb` 中同时配置 `redis_cache_sentinels` 等 Sentinel 参数（如果用了 Sentinel 高可用），导致故障切换后连接不到新 master。

---

#### 步骤4：Puma/Rails 调优——Worker + 线程 + 数据库连接池

**目标**：根据服务器 CPU 核数和内存合理配置 Puma 的 worker 数量和线程池大小，计算正确的 `db_pool` 值。

```ruby
# gitlab.rb —— Puma Web 服务调优
# 计算公式（以 16 核 64GB 服务器为例）：
#   worker_processes = CPU 核数 × 0.5 ~ 0.75  → 16 * 0.5 = 8
#   min/max_threads = 4~8 起步，根据内存和并发量调整
#   db_pool = max_threads × 1.5（留足 buffer）→ 16 * 1.5 = 24，取整 25
#   per_worker_max_memory_mb = (总内存 - OS/其他组件) / worker 数 × 0.7
#     → (64GB - 8GB OS - 4GB PG - 4GB Redis - 4GB Gitaly - 4GB Sidekiq) / 8 * 0.7 ≈ 3.4GB，取值 3000

puma['worker_processes'] = 8              # worker 进程数
puma['min_threads'] = 4                   # 每个 worker 最小线程数（含 Puma 内部线程）
puma['max_threads'] = 16                  # 每个 worker 最大线程数
                                          # 总并发连接 ≈ worker × max_threads = 8 × 16 = 128
puma['per_worker_max_memory_mb'] = 3000   # ★ 单个 worker 内存超此值将被优雅重启
                                          # 防止 Ruby 内存碎片导致 OOM

# 数据库连接池——关键计算公式：
# 最大连接数 = Puma worker × max_threads × 1.1（Puma 额外连接）+ 后台任务连接
#   = 8 × 16 × 1.1 + 20（Sidekiq 连接）≈ 161
# PostgreSQL max_connections 必须 ≥ 此值！否则报 "remaining connection slots are reserved"
gitlab_rails['db_pool'] = 25              # 每个进程的连接池大小
postgresql['max_connections'] = 200       # PostgreSQL 总连接上限

# Rails 内层缓存
gitlab_rails['redis_cache_instance'] = 'redis://redis-cache.internal:6379/0'
gitlab_rails['cache_namespace'] = 'gitlab-cache'
```

```bash
# 验证 Puma 实际线程使用情况
curl -s http://localhost:8080/metrics | grep puma
# puma_workers{index="0"} 1
# puma_running{index="0"} 8     → 当前活跃线程（应该在 min 和 max 之间）
# puma_pool_capacity{index="0"} 8 → 剩余可用线程（为 0 说明线程耗尽，需加大 max_threads）

# 验证数据库连接使用
sudo gitlab-psql -d gitlabhq_production -c \
  "SELECT count(*) AS active_conns FROM pg_stat_activity WHERE state = 'active';"
# 值接近 max_connections 时说明连接池不足
```

**常见踩坑**：`db_pool` × `worker_processes` > `postgresql['max_connections']` 导致连接耗尽。公式是 `db_pool` 控制每个进程（含 Puma worker + Sidekiq）的连接数上限，总数不能超过 PG 的 `max_connections`。另外 `per_worker_max_memory_mb` 设得太低会导致 worker 频繁重启，建议在 2048-4096 之间根据实际内存调整。

---

#### 步骤5：Sidekiq 调优——队列分组 + 并发控制

**目标**：通过 queue_groups 将不同类型的任务分流到独立 Sidekiq 进程，按业务优先级配置差异化并发。

```ruby
# gitlab.rb —— Sidekiq 队列分组与并发调优

# ★ 队列分组：将 GitLab 的 50+ 种 Sidekiq 队列分配到不同进程组
# 策略：高延迟敏感（mailers/actioncable）→ 低并发 + 专属进程
#       高吞吐（pipeline_processing/default）→ 高并发 + 多 worker
#       低优先级（cleanup/maintenance）→ 单 worker + 低并发

sidekiq['queue_groups'] = [
  # 组 1：实时交互（邮件、推送通知、Webhook）
  # 优先级最高，但延迟敏感，保持低并发避免拥堵
  'mailers,email_receiver,service_desk_email_receiver,actioncable',

  # 组 2：核心业务（Pipeline 处理、MR 操作、仓库管理）
  # 占用 CPU 高，2 个进程分担
  'pipeline_processing,pipeline_creation,pipeline_default,default,merge,gitlab_shell',
  'pipeline_processing,pipeline_creation,pipeline_default,default,merge,gitlab_shell',

  # 组 3：高级功能（安全扫描、合规、Pages）
  'security_scanner,container_repository,package_registry,pages,pages_domain_verification',

  # 组 4：低优先级维护（清理、数据迁移、GEO）
  'project_export,incident_management,background_migration,database,cleanup,*'
]

# 全局并发限制
sidekiq['max_concurrency'] = 25               # 每个 Sidekiq 进程的最大线程数
sidekiq['min_concurrency'] = 5                # 最小线程数（none 表示不设下限）

# Sidekiq 内存限制——防止内存泄漏导致 OOM
sidekiq['per_worker_max_memory_mb'] = 2048    # 单个 Sidekiq 进程内存上限

# 优雅关闭——给正在执行的任务足够时间完成
sidekiq['timeout'] = 30                       # 关闭时等待任务完成的超时秒数
```

```bash
# 查看各队列的积压情况
sudo gitlab-rake gitlab:sidekiq:queue_size
# Queue Name                     Size
# default                        125    ← 注意：持续增长说明处理不过来
# pipeline_processing             12
# mailers                          3
# background_migration           2048   ← 高危！迁移任务堆积

# 查看 Sidekiq 进程资源占用
sudo gitlab-ctl status sidekiq
# run: sidekiq: (pid 12345) 3600s; run: log: (pid 12300) 3600s
# ↑ uptime 正常说明进程没有频繁重启

ps aux | grep sidekiq | awk '{sum+=$6} END {print "Total RSS:", sum/1024, "MB"}'
# 检查总内存是否接近上限
```

**常见踩坑**：`queue_groups` 中把 `*` 通配符放在了非最后一个组——通配符会匹配所有队列，导致排在后面的组永远收不到任务。`*` 必须放在最后一个 group 中。另外 `max_concurrency` 设太高会导致每个 Sidekiq 进程都打开大量 PG 连接，注意 `max_concurrency` × Sidekiq 进程数不要超过 `max_connections` 预留的 Sidekiq 配额。

---

### 测试验证

```bash
# === 验证1：数据库慢查询改善 ===
# 清空 pg_stat_statements 统计后等 10 分钟重新查询
sudo gitlab-psql -d gitlabhq_production -c "SELECT pg_stat_statements_reset();"
# ... 等待 10 分钟正常运行 ...
sudo gitlab-psql -d gitlabhq_production -c \
  "SELECT LEFT(query, 120) AS sql_snippet,
          calls,
          mean_exec_time::numeric(10,2) AS avg_ms
   FROM pg_stat_statements
   ORDER BY mean_exec_time DESC LIMIT 10;"
# 预期：优化前 Top 1 > 200ms → 优化后 Top 1 < 10ms

# === 验证2：Gitaly 缓存命中率 ===
curl -s http://localhost:9236/metrics \
  | grep -E "pack_objects_cache_(hit|miss)_total" \
  | grep -v "^#"
# gitaly_pack_objects_cache_hit_total 14520
# gitaly_pack_objects_cache_miss_total 3520
# 命中率 = 14520 / (14520+3520) = 80.5% ✅

# === 验证3：MR 列表页响应时间 ===
time curl -s -o /dev/null -w "HTTP %{http_code}, time: %{time_total}s\n" \
  -H "PRIVATE-TOKEN: $GITLAB_TOKEN" \
  "$GITLAB_URL/api/v4/projects/$PROJECT_ID/merge_requests?per_page=20"
# 预期：优化前 > 15s → 优化后 < 2s

# === 验证4：Prometheus 关键指标（Grafana 大盘检查） ===
# Puma P99 响应时间
curl -s "http://localhost:9090/api/v1/query?query=histogram_quantile(0.99,rate(puma_http_request_duration_seconds_bucket[5m]))"
# 预期 P99 < 2s

# Sidekiq 队列积压
curl -s "http://localhost:9090/api/v1/query?query=sidekiq_queue_size{queue='default'}"
# 预期 < 100（持续监控 24h 无积压累积）

# Gitaly RPC 延迟 P99
curl -s "http://localhost:9236/metrics" | grep 'gitaly_pack_objects_request_latency_seconds'
```

---

## 4. 项目总结

### 五维调优速查表

| 组件 | 常见瓶颈 | 诊断工具 | 核心优化手段 | 预期收益 | 风险/代价 |
|------|---------|---------|-------------|---------|----------|
| PostgreSQL | 缺索引、连接耗尽 | `pg_stat_statements` | 复合索引、PgBouncer | 查询延迟 -70% | 索引占据磁盘空间（10-30% 增量） |
| Gitaly | Clone/Fetch 慢、RPC 饥饿 | `/metrics` 端点 | PackObjectsCache、并发限制 | CPU 占用 -50%、Clone 加速 3x | 缓存占额外磁盘 + 内存 |
| Redis | 内存碎片、级联故障 | `redis-cli INFO` | `volatile-lru`、三实例分离 | 消除缓存/队列干扰 | 运维复杂度增加（管理 3 个实例） |
| Puma/Rails | Worker 连接池不足 | Puma `/metrics` | db_pool 调整、per_worker 内存限制 | 页面加载 -50% | 内存占用随 worker 数线性增加 |
| Sidekiq | 队列积压、任务饥饿 | `rake queue_size` | queue_groups 分组、concurrency 差异化 | 积压恢复速度 + 80% | 配置复杂，队列错分导致任务搁浅 |

### 适用场景

- **1000+ 活跃用户**：触发五维调优的基线，至少需要 PostgreSQL 索引优化 + Redis 实例分离
- **开发高峰期 GitLab 卡顿**：优先查 PostgreSQL 慢查询 + Puma 连接池
- **git clone/fetch 普遍超时**：优先启用 PackObjectsCache，再查 Gitaly RPC 并发限制
- **CI Pipeline 触发延迟严重**：优先查 Sidekiq 的 `pipeline_processing` 队列积压
- **GitLab 间歇性 502**：优先查 Puma 的 `per_worker_max_memory_mb` 是否触发 worker 频繁重启

**不适用场景**：
- 小于 100 用户的 GitLab 实例——默认配置已足够，过度调优只会增加维护负担
- 企业已购买 GitLab Dedicated（SaaS）托管版——基础设施调优由 GitLab 团队负责

### 注意事项

- **`CREATE INDEX CONCURRENTLY` 耗时可能达数小时**：大表 (1 亿+行) 的 CONCURRENTLY 索引创建需 1-4 小时，必须在维护窗口执行，且该期间不能有其他 DDL
- **PackObjectsCache 内存占用不可忽视**：建议内存 ≥ 32GB 的节点才启用；启用后监控 `dir_max_size` 和实际磁盘占用
- **Redis `volatile-lru` 只淘汰有 TTL 的 key**：确保缓存 key 设置了过期时间；如果没有 TTL，`volatile-lru` 不会淘汰任何 key → 内存写满后 Redis 拒绝写入
- **性能调优是迭代的**：顺序必须是「诊断 → 优化 → 验证 → 诊断下一个」，不要一次改太多——否则你无法确定哪个改动生效了
- **PgBouncer transaction 模式不支持 `SET` 和 `LISTEN/NOTIFY`**：如果使用了 PostgreSQL 的 LISTEN 功能（如某些实时通知），必须改为 session 模式

### 常见踩坑经验

1. **加了索引但查询没变快——统计信息过期**
   根因：PostgreSQL 的查询计划器依赖 `pg_statistic` 统计信息，大表 `ANALYZE` 默认只在表内容变化超过 10% 时自动触发。如果刚加了索引但统计信息未更新，计划器可能会继续选择全表扫描。解决：手动执行 `ANALYZE merge_requests;` 更新统计信息后重试，或调低 `autovacuum_analyze_scale_factor`。

2. **PackObjectsCache 内存飙升导致 OOM Killer 杀进程**
   根因：`max_age` 设置过长（如 60 分钟）+ `dir_max_size` 未限制 → 缓存目录撑爆磁盘 → Gitaly 写缓存失败 → 但内存中的缓存索引仍保留 → 内存持续增长。解决：设置合理的 `max_age`（10-20 分钟）和 `dir_max_size`（按实际磁盘容量计算），并开启 Gitaly 的 `pack_objects_cache_max_cached_objects` 限制单个仓库的缓存对象数。

3. **Sidekiq queue_groups 配置后某些队列永远不消费**
   根因：`queue_groups` 中的通配符 `*` 放在了第一个或中间位置，导致后续组收不到任何任务——因为 Wildcard 匹配了所有队列名，第一个 Sidekiq 进程把所有任务都抢走了。解决：`*` 通配符必须且只能在最后一个 group 中；每个队列名必须明确出现在某个 group 中，否则会被 `*` 兜底匹配。

### 思考题

1. PgBouncer 的 `transaction` 模式 vs `session` 模式：GitLab 官方推荐使用 `transaction` 模式以最大化连接复用，但什么场景下必须改用 `session` 模式？如果项目使用了 PostgreSQL 的 `LISTEN/NOTIFY`、`SET` 会话变量、或者 `PREPARE` 语句，会有什么后果？

2. 只有一台 8 核 32GB 的服务器（无法横向拆分组件），所有 GitLab 组件共存于同一台机器。在资源受限的情况下，五维调优的优先级应该怎么排？为什么？

> 答案见附录 D。

### 推广计划提示

- **运维/SRE**：将五维调优纳入 GitLab 季度体检——用 Prometheus 指标 `pg_stat_statements`、`gitaly_pack_objects_cache_hit_total`、`sidekiq_queue_size` 运行自动化检查清单，生成调优报告
- **DBA**：`pg_stat_statements` 输出应纳入周会 review——GitLab 版本升级后可能出现新引入的慢查询，以及新增功能表缺少索引
- **开发**：理解 `db_pool` 和连接池的计算公式——在写 MR 涉及数据库迁移时，评估新表是否需要索引，避免投产后再加索引触发锁表
- **架构师**：先诊断后开药——不是所有配置项都开到最大值，每个参数都要有「当前值 → 调整值 → 调整依据」的链路
