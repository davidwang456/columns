# 第17章：GitLab 高可用架构设计与部署

## 1. 项目背景

> **业务场景**：一家 SaaS 公司从 50 人扩张到 300 人，GitLab 单机实例越来越吃力。上午 10 点开发高峰期间，每次 `git push` 要等 5 秒以上，MR 的 diff 加载要 10 秒。更致命的是，上个月服务器 RAID 卡故障导致 GitLab 停机 8 小时——因为整个平台就一台机器，没有任何冗余。

CTO 下了死命令："三个月内架构升级到高可用——GitLab 不能成为单点故障。"但运维团队对着 GitLab 的 HA 架构图犯了难：Gitaly Cluster 需要 Praefect + 3 个 Gitaly 节点 + PostgreSQL 选举，Patroni + etcd 管理 PostgreSQL HA，Redis Sentinel 做主从切换——这比部署一个 Kubernetes 集群还复杂。

**痛点放大**：GitLab 高可用不是简单地把组件"多装几份"，而是一个涉及数据一致性、网络分区处理、自动故障转移的分布式系统设计问题。Gitaly 的 Quorum 写入模型、PostgreSQL 的流复制、Redis 的哨兵选举——每个组件都有自己的一套 HA 方案，而且互相关联。

## 2. 项目设计——剧本式交锋对话

**场景**：运维团队的技术评审会，白板上画满了服务器拓扑图。

---

**小胖**："GitLab 高可用为啥要把每个组件都拆出去？PostgreSQL 用云厂商的 RDS 不就行了吗？"

**大师**："拆分不是为了追求复杂度，而是每个组件有不同的 HA 需求。PostgreSQL 是有状态服务——你存了用户数据、Issue、MR 信息，不能丢，所以需要主从复制 + 自动故障转移。Redis 存的是会话和缓存——丢了可以重建，但丢了会有短暂的性能影响，所以哨兵模式就够了。Gitaly 存的是 Git 仓库——数据量最大，对 IOPS 要求最高，所以需要 Quorum 写入保证一致性。"

**小白**："Gitaly Cluster 的 Quorum 写入是什么意思？是不是必须要 3 个节点？"

**大师**："不是必须 3 个节点，而是奇数节点可以容忍少数故障。在 Gitaly Cluster 中，Praefect 是代理层，负责把写入请求分发到多个 Gitaly 节点。Quorum 写入是指：一个写操作只有在多数节点确认后才返回成功。3 个节点可以容忍 1 个节点故障，5 个节点可以容忍 2 个。技术映射——这就像银行的金库需要 3 把钥匙中的 2 把同时转动才能开门，任何一把钥匙丢了都不会让金库靠一把钥匙就能打开。"

**小胖**："那 Geo 和 HA 有什么区别？我看文档里还有一个 Geo 功能。"

**大师**："HA 是你同一机房内的冗余——目标是单机故障不影响服务。Geo 是跨地域的灾备——当整个机房都挂了（地震、断电），你可以在另一地点恢复。HA 追求的是 RTO（恢复时间）趋近于零、RPO（数据丢失）趋近于零；Geo 容忍分钟级的恢复时间和少量的数据丢失。技术映射：HA 是双电源供电，Geo 是在另一个城市有完整的备份发电机。"

**小白**："那我们 300 人的团队，到底需要 HA 还是 Geo？"

**大师**："300 人团队，如果业务 7x24 不能停，先做 HA——这是防御单点故障的基础。等团队超过 1000 人或业务有跨地域合规要求时，再上 Geo。不要同时做 HA 和 Geo——每增加一层复杂度都是运维风险的倍增。"

---

## 3. 项目实战

### 环境准备

> **目标**：用 Docker Compose 搭建一个简化的 GitLab HA 环境：2 个 Rails 节点 + 3 个 Gitaly 节点 + 独立 PostgreSQL + 独立 Redis。

**前置条件**：至少 3 台服务器或 1 台大内存机器（64GB+）。

### 分步实现

#### 步骤1：理解 GitLab HA 的整体架构

```
                    ┌─────────────┐
                    │  Nginx LB   │  (负载均衡)
                    └──────┬──────┘
              ┌────────────┼────────────┐
              ▼            ▼            ▼
        ┌──────────┐ ┌──────────┐ ┌──────────┐
        │  Rails 1 │ │  Rails 2 │ │  Rails 3 │  (Web/API)
        └────┬─────┘ └────┬─────┘ └────┬─────┘
             │             │             │
    ┌────────┼─────────────┼─────────────┼────────┐
    │        ▼             ▼             ▼        │
    │  ┌──────────┐                    ┌───────┐  │
    │  │ Patroni  │◄── etcd ──────────►│Redis  │  │
    │  │   PG HA  │   选举              │Sentinel│  │
    │  └────┬─────┘                    └───────┘  │
    │       │                                     │
    │  ┌────┴──────────────────────────────┐      │
    │  │         Gitaly Cluster            │      │
    │  │  ┌────────┐ ┌────────┐ ┌───────┐ │      │
    │  │  │Gitaly 1│ │Gitaly 2│ │Gitaly3│ │      │
    │  │  └────────┘ └────────┘ └───────┘ │      │
    │  │         ▲                        │      │
    │  │    ┌────┴─────┐                  │      │
    │  │    │ Praefect │  (代理/协调器)   │      │
    │  │    └──────────┘                  │      │
    │  └──────────────────────────────────┘      │
    └────────────────────────────────────────────┘
```

#### 步骤2：Docker Compose 搭建简化 HA 环境

**目标**：在一台机器上模拟 GitLab HA 的核心组件。

```yaml
# docker-compose-ha.yml - GitLab HA 演示环境
version: '3.8'

networks:
  gitlab-ha:
    driver: bridge

services:
  # ===== Nginx 负载均衡 =====
  nginx-lb:
    image: nginx:alpine
    container_name: gitlab-lb
    volumes:
      - ./nginx-lb.conf:/etc/nginx/nginx.conf:ro
    ports:
      - "80:80"
    networks:
      - gitlab-ha
    depends_on:
      - rails-1
      - rails-2

  # ===== Rails 节点 1 =====
  rails-1:
    image: gitlab/gitlab-ce:17.0.0-ce.0
    container_name: gitlab-rails-1
    hostname: rails-1
    environment:
      GITLAB_OMNIBUS_CONFIG: |
        external_url 'http://rails-1'
        roles ['application']
        gitlab_rails['db_host'] = 'postgres'
        gitlab_rails['db_password'] = 'gitlab'
        gitlab_rails['redis_host'] = 'redis'
        gitlab_rails['gitaly_client'] = [
          { 'name' => 'default', 'address' => 'praefect:2305', 'token' => 'praefect-token' }
        ]
    networks:
      - gitlab-ha
    depends_on:
      - postgres
      - redis

  # ===== Rails 节点 2 =====
  rails-2:
    image: gitlab/gitlab-ce:17.0.0-ce.0
    container_name: gitlab-rails-2
    hostname: rails-2
    environment:
      GITLAB_OMNIBUS_CONFIG: |
        external_url 'http://rails-2'
        roles ['application']
        gitlab_rails['db_host'] = 'postgres'
        gitlab_rails['db_password'] = 'gitlab'
        gitlab_rails['redis_host'] = 'redis'
        gitlab_rails['gitaly_client'] = [
          { 'name' => 'default', 'address' => 'praefect:2305', 'token' => 'praefect-token' }
        ]
    networks:
      - gitlab-ha
    depends_on:
      - postgres
      - redis

  # ===== PostgreSQL =====
  postgres:
    image: postgres:14-alpine
    container_name: gitlab-postgres
    environment:
      POSTGRES_USER: gitlab
      POSTGRES_PASSWORD: gitlab
      POSTGRES_DB: gitlabhq_production
    volumes:
      - postgres_data:/var/lib/postgresql/data
    networks:
      - gitlab-ha

  # ===== Redis =====
  redis:
    image: redis:7-alpine
    container_name: gitlab-redis
    command: redis-server --appendonly yes
    volumes:
      - redis_data:/data
    networks:
      - gitlab-ha

  # ===== Praefect（Gitaly 代理）=====
  praefect:
    image: registry.gitlab.com/gitlab-org/build/cng/gitaly:v17.0.0
    container_name: gitlab-praefect
    command: /usr/bin/praefect -config /etc/gitaly/praefect-config.toml
    volumes:
      - ./praefect-config.toml:/etc/gitaly/praefect-config.toml:ro
    networks:
      - gitlab-ha
    depends_on:
      - gitaly-1
      - gitaly-2
      - gitaly-3

  # ===== Gitaly 节点 1 =====
  gitaly-1:
    image: registry.gitlab.com/gitlab-org/build/cng/gitaly:v17.0.0
    container_name: gitlab-gitaly-1
    volumes:
      - gitaly1_data:/var/opt/gitlab/git-data
      - ./gitaly-config.toml:/etc/gitaly/config.toml:ro
    networks:
      - gitlab-ha

  # ===== Gitaly 节点 2 =====
  gitaly-2:
    image: registry.gitlab.com/gitlab-org/build/cng/gitaly:v17.0.0
    container_name: gitlab-gitaly-2
    volumes:
      - gitaly2_data:/var/opt/gitlab/git-data
      - ./gitaly-config.toml:/etc/gitaly/config.toml:ro
    networks:
      - gitlab-ha

  # ===== Gitaly 节点 3 =====
  gitaly-3:
    image: registry.gitlab.com/gitlab-org/build/cng/gitaly:v17.0.0
    container_name: gitlab-gitaly-3
    volumes:
      - gitaly3_data:/var/opt/gitlab/git-data
      - ./gitaly-config.toml:/etc/gitaly/config.toml:ro
    networks:
      - gitlab-ha

volumes:
  postgres_data:
  redis_data:
  gitaly1_data:
  gitaly2_data:
  gitaly3_data:
```

#### 步骤3：配置 Praefect 和 Gitaly

**目标**：配置 Praefect 代理实现 Gitaly 节点的 Quorum 写入和故障转移。

```toml
# praefect-config.toml
listen_addr = "0.0.0.0:2305"
socket_path = "/var/opt/gitlab/praefect/praefect.socket"

prometheus_listen_addr = "0.0.0.0:9652"

[tls]
# 生产环境必须启用 TLS

[database]
host = "postgres"
port = 5432
user = "gitlab"
password = "gitlab"
dbname = "praefect_production"

# 虚拟存储配置
[[virtual_storage]]
name = "default"

# Gitaly 节点列表（3 节点，多数写入）
[[virtual_storage.node]]
storage = "gitaly-1"
address = "tcp://gitaly-1:8075"
token = "gitaly-token"

[[virtual_storage.node]]
storage = "gitaly-2"
address = "tcp://gitaly-2:8075"
token = "gitaly-token"

[[virtual_storage.node]]
storage = "gitaly-3"
address = "tcp://gitaly-3:8075"
token = "gitaly-token"

# 故障转移配置
[failover]
enabled = true
election_strategy = "per_repository"  # 每个仓库独立选举 Primary
```

```toml
# gitaly-config.toml（三节点共用）
socket_path = "/var/opt/gitlab/gitaly/gitaly.socket"

[[storage]]
name = "default"
path = "/var/opt/gitlab/git-data/repositories"

[auth]
token = "gitaly-token"
transitioning = false

[prometheus]
listen_addr = "0.0.0.0:9236"

[logging]
level = "info"
format = "json"
```

#### 步骤4：验证 HA 功能

**目标**：模拟节点故障，验证自动故障转移。

```bash
# 1. 启动 HA 环境
docker compose -f docker-compose-ha.yml up -d

# 2. 查看 Praefect 集群状态
docker exec -it gitlab-praefect praefect -config /etc/gitaly/praefect-config.toml \
  dial-nodes --virtual-storage default
# 输出：各 Gitaly 节点的连接状态和 Primary/Secondary 角色

# 3. 模拟 Gitaly 故障
docker stop gitlab-gitaly-1

# 4. 验证 Git 操作仍正常（2/3 节点存活，Quorum 满足）
# 在 Rails 容器中执行：
docker exec -it gitlab-rails-1 gitlab-rails runner "
  project = Project.first
  puts project.repository.branch_names
"
# 应该正常返回分支列表，不受 1 个 Gitaly 节点宕机影响

# 5. 停止第二个 Gitaly 节点
docker stop gitlab-gitaly-2
# 此时只剩 1/3 节点可用，Quorum 不满足
# 尝试 Git 写入操作应该失败
```

### 完整代码清单

- `docker-compose-ha.yml`：HA 环境编排
- `praefect-config.toml`：Praefect 代理配置
- `gitaly-config.toml`：Gitaly 节点配置
- `nginx-lb.conf`：负载均衡配置

### 测试验证

```bash
# 验证1：负载均衡分发
curl -s http://localhost/api/v4/version | python3 -c "import json,sys; print(json.load(sys.stdin)['version'])"
# 反复执行，应该轮询到不同的 Rails 节点

# 验证2：数据库共享
# 在 rails-1 创建项目，在 rails-2 应该能查到
docker exec -it gitlab-rails-1 gitlab-rails runner "
  Project.create!(name: 'ha-test', path: 'ha-test', namespace: Namespace.first)
  puts 'Project created on rails-1'
"

docker exec -it gitlab-rails-2 gitlab-rails runner "
  p = Project.find_by(path: 'ha-test')
  puts \"Found project: #{p.name} on rails-2\"
"

# 验证3：Gitaly 冗余
docker logs gitlab-praefect 2>&1 | grep "failover"
# 查看故障转移日志
```

## 4. 项目总结

### 优点 & 缺点

| 组件 | HA 方案 | 优点 | 缺点 |
|------|--------|------|------|
| Rails | 多节点 + LB | 水平扩展，处理更多请求 | 有状态请求需要 Session 共享 |
| Gitaly | Praefect + 3 节点 | Quorum 保证一致性，自动故障转移 | 需要额外 1 个 Praefect 节点 |
| PostgreSQL | Patroni + etcd | 自动选主，成熟稳定 | 需要 etcd 集群（至少 3 个） |
| Redis | Sentinel | 自动主从切换 | 切换时有短暂不可用 |

### 适用场景

- **300+ 人团队**：需要 HA 保证服务可用性
- **7x24 业务**：不能容忍单机故障导致的停机
- **金融/政务等高要求行业**：数据一致性优先

**不适用场景**：
- 50 人以下的团队（单机 + 定期备份足够）
- 已有云厂商 RDS/Redis 服务的（直接用云服务，减少运维负担）

### 注意事项

- **网络延迟**：Gitaly 节点之间、Rails 和 Gitaly 之间的网络延迟应 < 1ms
- **时钟同步**：所有节点必须 NTP 同步，否则 Praefect 的故障检测可能误判
- **Gitaly 节点必须奇数**：3、5、7——原因在于 Quorum（多数派）算法
- **测试故障场景**：定期演练节点宕机、网络分区——不要等到真实故障才第一次验证

### 常见踩坑经验

1. **Praefect 无法连接到 Gitaly**：防火墙或安全组拦截了 Gitaly 的 8075 端口。根因：Gitaly 默认 tcp 监听端口需显式开放。解决：确保所有节点间端口互通。
2. **PostgreSQL 流复制延迟过大**：大量 MR diff 写入时备库跟不上。根因：同步复制模式下主库受备库影响。解决：考虑使用异步复制 + 允许少量数据丢失（业务决策）。
3. **HA 切换后 Session 丢失**：用户需要重新登录。根因：Rails session 存储在本地 cookie 中（加密），不需要共享存储。但某些 CSRF token 在切换后可能失效。解决：确保 `gitlab_rails['secret_key_base']` 在所有 Rails 节点上一致。

### 思考题

1. 如果你的 Gitaly Cluster 有 3 个节点，其中 1 个节点磁盘损坏需要替换。新节点加入后，数据如何从其他节点同步？Praefect 在这个过程中扮演什么角色？
2. GitLab Rails 节点本身是无状态的——所有状态在 PostgreSQL、Redis、Gitaly 中。这意味着 Rails 节点可以任意扩缩容。但为什么官方建议 Rails 节点数不超过 10 个？瓶颈在哪？

> 答案见附录 D。

### 推广计划提示

- **运维**：HA 架构是 GitLab 生产运维的必修课。建议先在沙箱环境用 Docker Compose 跑一遍理解原理
- **架构师**：关注各组件 HA 方案的选型逻辑——Quorum 写入、Leader 选举、负载均衡
- **开发**：了解 HA 架构后，能更好地判断"为什么某些操作在高负载时变慢"的根因
