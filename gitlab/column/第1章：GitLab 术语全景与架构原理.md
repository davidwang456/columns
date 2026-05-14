# 第1章：GitLab 术语全景与架构原理

## 1. 项目背景

> **业务场景**：某互联网公司技术团队从 SVN + Jenkins 的"原始社会"模式，决定全面迁移到 GitLab，但团队成员面对 GitLab 的众多概念（Merge Request、Pipeline、Runner、Gitaly）感到困惑，不知从何入手。

团队现状是这样的：代码托管在 SVN，每次合并代码都要手动解决冲突，CI/CD 靠 Jenkins 的"祖传脚本"吊着一口气，发布上线依赖运维手动操作。新入职的小李第一次提交代码，因为不了解分支策略，直接把 feature 代码推到了主干，导致生产环境宕机 2 小时。

CTO 决定："全面迁移到 GitLab，统一研发流程。"

但问题来了：GitLab 到底是什么？它和 GitHub 有什么本质区别？为什么它需要那么多组件——Rails、Gitaly、Sidekiq、PostgreSQL、Redis？团队中有人以为 GitLab 只是"私有的 GitHub"，有人以为它就是"装了 CI/CD 的代码仓库"。理解偏差导致迁移方案漏洞百出——有人提议"直接把 SVN 仓库原样导入"，完全忽略 GitLab 的分支模型和管理体系。

**痛点放大**：如果不先建立统一的术语体系和架构认知，后续的所有章节都是"盲人摸象"。开发不知道 MR 和直接 push 的差异，测试不理解 CI Pipeline 的触发机制，运维架设 GitLab 时只装了一个 Rails 却忽略了 Gitaly 导致 Git 操作奇慢无比——这些都是术语理解不到位引发的生产事故。

## 2. 项目设计——剧本式交锋对话

**场景**：周一晨会后的茶水间，三人讨论 GitLab 迁移方案。

---

**小胖**：（端着奶茶走进来）"大师，CTO 说要迁移到 GitLab，这不就是个代码仓库吗？跟 GitHub 有啥区别？我之前个人项目都用 GitHub，免费的不要白不要啊。"

**大师**：（放下咖啡杯）"小胖，你把 GitLab 理解成'私有 GitHub'就只看到了冰山一角。GitLab 是一个完整的 DevOps 平台，代码仓库只是它的一小部分——它还有 CI/CD、容器镜像仓库、包管理、安全扫描、项目管理，甚至还有 Wiki 和监控。"

**小胖**："所以它是个'全家桶'？那不是更重吗？我们小团队用得了这么多？"

**小白**：（从文档中抬起头）"我关心的是另一个问题。我看 GitLab 架构图，它里面有 Rails、Gitaly、Sidekiq、PostgreSQL、Redis、Nginx 一堆组件——为什么一个'代码仓库'需要这么多东西？我装个 Gitea 一个二进制就搞定了。"

**大师**："好问题，这正是今天要讲清楚的。咱们从最核心的 Git 操作说起——当你在终端执行 `git push`，这个请求是怎么在 GitLab 内部被处理的？"

**小胖**："不就是把文件传到服务器上吗？"

**大师**："没那么简单。你的 `git push` 请求首先到达 Nginx，Nginx 把它反向代理给 GitLab Workhorse——这是一个用 Go 写的高性能代理——Workhorse 解析请求后，把鉴权信息发给 GitLab Rails 做权限校验。Rails 查数据库确认你有推送权限后，通过 gRPC 调用 Gitaly 服务。Gitaly 才是真正操作 Git 仓库的组件——它执行 `git receive-pack`，把数据写入磁盘。"

**小胖**："哇，一层套一层，这不就像外卖配送——你点餐（git push），平台接单（Nginx），骑手取餐（Workhorse），商家出餐（Rails），后厨做菜（Gitaly）？"

**大师**："技术映射——Nginx 是网关、Workhorse 是协议代理、Rails 是业务逻辑、Gitaly 是存储引擎。这个分层架构让每个组件可以独立扩缩容。"

**小白**："那 Sidekiq 和 Redis 呢？它们在什么场景下工作？"

**大师**："很多操作不能同步完成。比如你推送代码后，GitLab 要发邮件通知、要触发 CI Pipeline、要更新 MR 的 diff 缓存——这些都用 Sidekiq 异步处理，Redis 作为消息队列。想象你去银行办业务：柜员（Rails）接待你，把复杂的后台处理放进工作队列（Redis），后台工作人员（Sidekiq）慢慢处理，处理完了通知你。"

**小胖**："那 PostgreSQL 存什么？代码又不是存在数据库里的。"

**大师**："PostgreSQL 存的是元数据——项目信息、用户权限、Issue、MR 描述、Pipeline 状态、所有 CI 配置。Git 仓库本身是存在文件系统上的（通过 Gitaly 管理），但'这个仓库属于哪个项目'、'谁有权限访问'、'MR #42 是谁创建的'这些问题，都靠 PostgreSQL 来回答。"

**小白**："所以我画一张图的话，应该是：用户 → Nginx → Workhorse → Rails（查 PostgreSQL 做权限）→ Gitaly（操作 Git 仓库），Sidekiq 从 Redis 取任务做异步处理。对吗？"

**大师**："就是这个思路。这张图就是 GitLab 的核心架构，后面每一章讲的原理、配置、优化，都基于这张图。技术映射：GitLab 是一个以 Rails 为大脑、Gitaly 为肌肉、Redis+Sidekiq 为神经系统的有机整体。"

---

## 3. 项目实战

### 环境准备

> **目标**：在本地搭建一个简化版的 GitLab 架构演示环境，用手工启动各组件的方式理解 GitLab 的协同工作。

**前置依赖**：

| 组件 | 版本要求 | 说明 |
|------|---------|------|
| Docker | 24.0+ | 运行 GitLab CE 容器 |
| Docker Compose | 2.20+ | 编排多组件 |
| curl | 7.0+ | API 测试 |
| git | 2.40+ | 客户端操作 |

### 分步实现

#### 步骤1：启动一个完整的 GitLab CE 实例

**目标**：用 Docker Compose 一键启动 GitLab，观察各组件运行状态。

**docker-compose.yml**：

```yaml
# GitLab CE 17.x 单机部署
# 注意：GitLab 需要至少 4GB 可用内存

version: '3.8'
services:
  gitlab:
    image: gitlab/gitlab-ce:17.0.0-ce.0
    container_name: gitlab-ce
    restart: always
    hostname: 'gitlab.local'
    environment:
      GITLAB_OMNIBUS_CONFIG: |
        external_url 'http://gitlab.local:8929'
        gitlab_rails['gitlab_shell_ssh_port'] = 2224
        # 降低内存占用（开发环境）
        puma['worker_processes'] = 2
        sidekiq['max_concurrency'] = 10
        prometheus_monitoring['enable'] = false
    ports:
      - "8929:8929"   # HTTP
      - "2224:22"     # SSH
    volumes:
      - gitlab_config:/etc/gitlab
      - gitlab_logs:/var/log/gitlab
      - gitlab_data:/var/opt/gitlab
    shm_size: '256m'

volumes:
  gitlab_config:
  gitlab_logs:
  gitlab_data:
```

**启动命令**：

```bash
# 在 docker-compose.yml 所在目录执行
docker compose up -d

# 查看启动日志（首次启动约需 3-5 分钟）
docker compose logs -f gitlab

# 等待出现 "GitLab is ready!" 后访问
# 浏览器打开 http://localhost:8929
```

**可能遇到的坑及解决方法**：

| 问题 | 原因 | 解决方法 |
|------|------|---------|
| 容器启动后立即退出 | 内存不足（< 4GB） | 确保 Docker Desktop 分配了至少 4GB 内存 |
| 502 Whoops 错误 | GitLab 内部服务未完全启动 | 等待 3-5 分钟，`docker compose logs gitlab` 观察进度 |
| 端口冲突 | 8929 或 2224 已被占用 | 修改 docker-compose.yml 中的端口映射 |
| SSH 连接被拒 | 容器内外 SSH 端口不一致 | 确认 `gitlab_rails['gitlab_shell_ssh_port']` 与 ports 映射一致 |

#### 步骤2：获取 root 密码并登录

**目标**：获取初始 root 密码，完成首次登录。

```bash
# 获取 root 初始密码（容器启动后 24 小时内有效）
docker exec -it gitlab-ce grep 'Password:' /etc/gitlab/initial_root_password

# 输出示例：
# Password: aBc123XyZ456...

# 如果密码文件已过期，重置密码：
docker exec -it gitlab-ce gitlab-rake "gitlab:password:reset[root]"
# 输入新密码后确认
```

**验证登录**：

```bash
# 用 curl 验证 API 可访问性
curl -s http://localhost:8929/api/v4/version | python3 -m json.tool

# 输出：
# {
#   "version": "17.0.0",
#   "revision": "abc1234"
# }
```

#### 步骤3：探索 GitLab 核心组件的运行状态

**目标**：通过命令行查看 GitLab 内部各组件的运行情况，理解架构关系。

```bash
# 进入容器
docker exec -it gitlab-ce bash

# 1. 查看所有 GitLab 组件状态
gitlab-ctl status

# 输出示例：
# run: gitaly: (pid 1234) 100s; run: log: (pid 1000) 200s
# run: gitlab-workhorse: (pid 1235) 100s
# run: logrotate: (pid 1250) 99s
# run: nginx: (pid 1236) 100s
# run: postgresql: (pid 1200) 101s
# run: puma: (pid 1230) 100s         ← Rails 应用服务器
# run: redis: (pid 1100) 102s
# run: sidekiq: (pid 1231) 100s

# 2. 查看 Puma（Rails）日志
tail -f /var/log/gitlab/puma/puma_stdout.log

# 3. 查看 Gitaly 日志
tail -f /var/log/gitlab/gitaly/current

# 4. 查看 Sidekiq 日志
tail -f /var/log/gitlab/sidekiq/current

# 5. 连接 PostgreSQL 查看数据库
gitlab-psql -d gitlabhq_production -c "
  SELECT count(*) as projects FROM projects;
  SELECT count(*) as users FROM users;
"

# 6. 连接 Redis 查看队列状态
gitlab-redis-cli LLEN queue:default
```

#### 步骤4：创建第一个项目并触发内部流程

**目标**：创建一个测试项目，通过 `git push` 观察 GitLab 各组件的协同工作。

```bash
# 在宿主机上操作
# 配置 Git 客户端
git config --global user.name "Test User"
git config --global user.email "test@example.com"

# 创建测试项目目录
mkdir gitlab-test && cd gitlab-test
git init
echo "# Hello GitLab" > README.md
git add README.md
git commit -m "Initial commit"

# 推送前配置远程仓库（使用 HTTP 方式）
git remote add origin http://root:<password>@localhost:8929/root/test-project.git

# 需要先在 GitLab UI 创建项目：
# 1. 登录 http://localhost:8929
# 2. 点击 "Create a project" → "Create blank project"
# 3. Project name: test-project, Visibility: Private
# 4. 取消勾选 "Initialize repository with a README"

git push -u origin main

# 推送时，同时在容器内实时观察 Gitaly 日志：
# docker exec -it gitlab-ce tail -f /var/log/gitlab/gitaly/current
# 你会看到类似 "fetching remote" "pack_objects" 等 Git RPC 调用

# 观察 Sidekiq 处理异步任务：
# docker exec -it gitlab-ce tail -f /var/log/gitlab/sidekiq/current
# 你会看到 "ProcessCommitWorker" "CreatePipelineWorker" 等异步任务
```

### 测试验证

```bash
# 测试1：验证 API 可用性
curl -s --header "PRIVATE-TOKEN: <your_token>" \
  http://localhost:8929/api/v4/projects | python3 -m json.tool

# 测试2：验证 Git 操作正常
git clone http://root:<password>@localhost:8929/root/test-project.git test-clone
cd test-clone && ls README.md  # 确认文件存在

# 测试3：验证 Sidekiq 异步处理
docker exec -it gitlab-ce gitlab-rails runner "
  puts Sidekiq::Queue.all.map { |q| [q.name, q.size] }.to_h
"
# 输出队列积压情况
```

### 完整代码清单

- `docker-compose.yml`：GitLab CE 部署配置（见步骤1）
- GitLab 组件状态检查命令合集（见步骤3）
- Git 项目创建与推送流程（见步骤4）

## 4. 项目总结

### 优点 & 缺点

| 维度 | 优点 | 缺点 |
|------|------|------|
| 架构 | 组件解耦，Gitaly/PostgreSQL/Redis 可独立扩容 | 组件多，单机部署资源消耗大（最低 4GB RAM） |
| 功能 | 完整 DevOps 平台，一站式覆盖代码→CI→CD→监控 | 部分高级功能仅 EE 版可用 |
| 部署 | Docker 一键启动，5 分钟可用 | 生产环境 HA 架构复杂，需独立管理 PG/Redis/Gitaly |
| 运维 | gitlab-ctl 统一管理各组件 | 版本升级有时需要跨多个大版本 |
| 对比 GitHub | CI/CD 内建、私有部署、无用量限制 | 社区活跃度略低于 GitHub |

### 适用场景

- **企业内部 DevOps 平台**：需要代码托管 + CI/CD + 包管理一体化
- **金融/政务等合规场景**：数据必须私有化部署，不能上公有云
- **多团队协作**：Group 体系支持复杂的组织架构与权限隔离
- **微服务/容器化团队**：内建 Container Registry 和 Kubernetes 集成

**不适用场景**：
- 个人开发者小项目（GitHub Pages / Actions 免费额度更友好）
- 纯 Git 托管的简单需求（Gitea / Gogs 更轻量）

### 注意事项

- **内存要求**：生产环境建议 16GB+ RAM，否则 Sidekiq 和 Puma 会频繁 OOM
- **存储规划**：Git 仓库会持续增长，建议使用 SSD，定期执行 `git gc`
- **备份策略**：不要只备份 PostgreSQL——Git 仓库文件、上传附件、CI Artifacts 都需要独立备份
- **版本升级**：不要跨大版本升级（如 15.x 直接升 17.x），严格按官方升级路径执行

### 常见踩坑经验

1. **502 错误反复出现**：根因是 Puma worker 被 OOM Killer 杀掉。解决：增加内存或减少 `puma['worker_processes']`。
2. **git push 超时**：根因是 Gitaly 磁盘 IO 瓶颈。解决：将 Git 仓库存储迁移到 SSD，或拆分 Gitaly 节点。
3. **Sidekiq 队列积压**：根因是 Redis 内存不足。解决：增加 Redis 内存配置，或调整 Sidekiq 并发度。

### 思考题

1. 如果在 GitLab 中删除了一个项目，Git 仓库数据是否立即从磁盘删除？为什么？
2. 当 GitLab 的 PostgreSQL 宕机但 Gitaly 仍在运行时，用户还能执行 `git clone` 吗？原因是什么？

> **提示**：第1题答案涉及 GitLab 的软删除机制（projects.deleted_at），第2题答案需要理解 Workhorse 对 Rails 的鉴权依赖。详细答案见附录 D。

### 推广计划提示

- **开发**：重点理解 Gitaly 和 Workhorse 的协作关系，这对后续学习 CI/CD 和源码分析至关重要
- **运维**：重点掌握 gitlab-ctl 命令和各组件日志路径，建立 GitLab 运维的全局视野
- **测试**：理解 GitLab 的 API 体系，本章的 curl 示例是后续自动化测试的基础
