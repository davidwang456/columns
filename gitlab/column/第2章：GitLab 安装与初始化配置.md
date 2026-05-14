# 第2章：GitLab 安装与初始化配置

## 1. 项目背景

> **业务场景**：一家 50 人的创业公司决定引入 GitLab 作为内部 DevOps 平台。CTO 给了运维工程师小王一周时间完成部署，但小王面对 Omnibus、Docker、Helm Chart 三种部署方式犯了难——到底选哪种？每种有什么坑？初始化要配置哪些参数？

小王之前只运维过几台简单的 Web 服务器，面对 GitLab 这种"全家桶"级别的应用有些心虚。他在 Slack 上搜了一圈，发现有人用 Docker 跑 GitLab 三个月后数据全丢（因为没有挂载 volumes），有人用 Omnibus 装完之后不知道怎么改 external_url 导致所有链接都指向 localhost，还有人用 Helm Chart 部署到 K8s 后 Gitaly 的 PVC 写满整个集群……

更糟的是，团队的需求一直在变：最开始只需要代码托管，后来加了 CI/CD，再后来又需要 Container Registry。小王每次新增功能都要重新折腾一遍部署方式，心力交瘁。

**痛点放大**：GitLab 的部署看起来简单——"就一行 docker run 的事"，但如果没有理解安装方式背后的架构约束和初始化配置的关键参数，后续的运维工作会不断踩坑。选错部署方式可能导致数据丢失、性能瓶颈、升级路径堵死等问题。

## 2. 项目设计——剧本式交锋对话

**场景**：运维区工位，小王正在看 GitLab 官方文档，眼前三个浏览器标签页分别开着 Omnibus、Docker、Helm 的安装指南。

---

**小胖**：（探头过来）"王哥，GitLab 怎么装？我看官网说 `docker run` 一行命令就搞定了，这不比装数据库还简单？"

**小王**："我一开始也是这么想的，但看了社区踩坑帖就怂了。有个团队用 Docker 跑了半年，有一天容器挂了数据全没——他们忘了挂 volumes。"

**大师**：（走过来）"小胖，小王说得对。部署方式的选择不是'哪个简单用哪个'，而是要考虑团队规模、运维能力和未来的扩展需求。咱们从三个维度来比较。"

**小白**：（推了推眼镜）"我先问个最根本的问题：为什么 GitLab 不能像 Gitea 那样一个二进制文件搞定？非得搞得这么复杂？"

**大师**："因为 GitLab 不是单一应用，它是一个分布式系统——有 Web 服务、有队列处理、有数据存储、有 Git RPC 服务。Omnibus 包把这些组件打包在一起，用 Chef 管理它们的运行。Docker 镜像又在这之上加了一层容器化封装。"

**小胖**："那 Omnibus 和 Docker 有什么区别？不就是装法不一样吗？"

**大师**："差别大了。Omnibus 是直接在宿主机上安装，所有组件共享宿主机的资源，日志和配置都在 `/etc/gitlab` 和 `/var/opt/gitlab`。Docker 是把这些组件封装在一个容器里，你需要显式挂载 volumes 才能让数据持久化。技术映射——Omnibus 就像买精装房，家具电器都嵌在墙里；Docker 就像租公寓，房子可以搬走，但贵重物品得自己保管。"

**小胖**："那我选 Docker，搬家方便！"

**小王**："但是 Docker 有个问题——GitLab 的各个组件（PostgreSQL、Redis、Gitaly）全跑在一个容器里，没法独立扩容。万一 Gitaly 负载高了，我要单独给 Gitaly 加机器都不行。"

**小白**："那 Helm Chart 呢？K8s 部署是不是能解决这个问题？"

**大师**："Helm Chart 确实把 GitLab 拆成了多个独立 Pod——Webservice Pod（Rails）、Sidekiq Pod、Gitaly Pod、PostgreSQL Pod、Redis Pod。每个组件可以独立扩缩容和升级。但代价是复杂度暴涨——你需要管理一堆 helm values，还要理解各个组件之间的依赖关系。"

**小胖**："那到底该怎么选嘛！"

**大师**："决策逻辑是这样的：如果团队小于 50 人，用 Docker Compose 就够了，资源够用、运维简单。50-500 人的团队，用 Omnibus 在物理机或虚拟机上部署，性能稳定且运维成熟。超过 500 人，或者已经有 K8s 基础设施的团队，可以考虑 Helm Chart。"

**小白**："那初始化配置呢？装好之后要改哪些参数？"

**大师**："最关键的三个参数：`external_url` 决定所有链接的生成域名，`gitlab_rails['gitlab_shell_ssh_port']` 决定 SSH 端口，SMTP 配置决定邮件通知能否发出。另外，时区、备份路径、监控开关也值得在初始化时配置好，而不是事后打补丁。"

---

## 3. 项目实战

### 环境准备

> **目标**：用三种方式部署 GitLab CE，对比各自的配置要点和适用场景。

| 环境 | 版本 | 用途 |
|------|------|------|
| Docker Desktop | 24.0+ | Docker 方式部署 |
| Ubuntu 22.04 VM | - | Omnibus 方式部署 |
| Minikube / Kind | 1.28+ | Helm 方式部署 |

### 分步实现

#### 步骤1：Docker Compose 方式部署（推荐个人/小团队）

**目标**：用 Docker Compose 一键部署 GitLab CE，配置持久化存储和基础参数。

**docker-compose.yml**（生产可用精简版）：

```yaml
version: '3.8'
services:
  gitlab:
    image: gitlab/gitlab-ce:17.0.0-ce.0
    container_name: gitlab
    restart: always
    hostname: 'gitlab.example.com'
    environment:
      GITLAB_OMNIBUS_CONFIG: |
        external_url 'https://gitlab.example.com'
        # SMTP 邮件配置（以 QQ 邮箱为例）
        gitlab_rails['smtp_enable'] = true
        gitlab_rails['smtp_address'] = "smtp.qq.com"
        gitlab_rails['smtp_port'] = 587
        gitlab_rails['smtp_user_name'] = "noreply@qq.com"
        gitlab_rails['smtp_password'] = "your_smtp_code"
        gitlab_rails['smtp_domain'] = "qq.com"
        gitlab_rails['smtp_authentication'] = "login"
        gitlab_rails['smtp_enable_starttls_auto'] = true
        gitlab_rails['gitlab_email_from'] = 'noreply@qq.com'
        # 时区与备份
        gitlab_rails['time_zone'] = 'Asia/Shanghai'
        gitlab_rails['backup_keep_time'] = 604800
        # 性能调优（根据机器配置调整）
        puma['worker_processes'] = 2
        sidekiq['max_concurrency'] = 10
        # 关闭非必要监控（节省资源）
        prometheus_monitoring['enable'] = false
        # SSH 端口映射
        gitlab_rails['gitlab_shell_ssh_port'] = 2224
    ports:
      - "443:443"
      - "80:80"
      - "2224:22"
    volumes:
      - ./config:/etc/gitlab          # 配置文件持久化
      - ./logs:/var/log/gitlab        # 日志持久化
      - ./data:/var/opt/gitlab        # 数据持久化（Git仓库+数据库+上传文件）
    shm_size: '256m'
    # 资源限制
    deploy:
      resources:
        limits:
          memory: 8G
        reservations:
          memory: 4G
```

**启动与初始化验证**：

```bash
# 1. 创建数据目录并设置权限
mkdir -p ./config ./logs ./data
# macOS/Linux 上设置 UID 映射（GitLab 容器内用户 UID 为 998）
# sudo chown -R 998:998 ./data ./logs

# 2. 启动
docker compose up -d

# 3. 实时监控启动日志
docker compose logs -f gitlab

# 4. 等待 GitLab 就绪（看到 "GitLab is ready!" 或检查健康端点）
# 另开终端执行：
while true; do
  STATUS=$(curl -s -o /dev/null -w "%{http_code}" http://localhost/-/health)
  echo "[$(date)] Health check: HTTP $STATUS"
  sleep 10
done

# 5. 获取 root 密码
docker exec -it gitlab cat /etc/gitlab/initial_root_password 2>/dev/null || \
  echo "密码文件已过期（超过24小时），请执行重置命令"
```

**可能遇到的坑**：

| 问题 | 原因 | 解决方法 |
|------|------|---------|
| 容器无法启动，`exit code 137` | OOM，内存不足 | 分配至少 4GB 内存给 Docker，或减少 puma worker |
| 启动后访问出现 502 | 内部服务未就绪 | 等待 3-5 分钟，`docker compose logs` 确认无报错 |
| volume 挂载失败，权限 denied | 宿主机目录权限不匹配 | `chown -R 998:998 ./data`（GitLab 内部 uid=998） |
| HTTPS 证书报错 | 未配置 SSL 证书 | 先用 HTTP 测试，或挂载 Let's Encrypt 证书 |

#### 步骤2：Omnibus 方式部署（推荐生产环境单机）

**目标**：在 Ubuntu 22.04 上通过 Omnibus 包安装 GitLab CE。

```bash
# 1. 安装依赖
sudo apt-get update
sudo apt-get install -y curl openssh-server ca-certificates tzdata perl

# 2. 添加 GitLab 官方仓库并安装
curl -sS https://packages.gitlab.com/install/repositories/gitlab/gitlab-ce/script.deb.sh | sudo bash

# 3. 指定 external_url 安装（此 URL 将作为所有链接的基础域名）
sudo EXTERNAL_URL="https://gitlab.example.com" apt-get install gitlab-ce

# 4. 查看初始 root 密码
# Omnibus 安装的密码在 17.x 中随机生成，存储在：
sudo cat /etc/gitlab/initial_root_password

# 如果没有此文件，用 Rails console 重置：
sudo gitlab-rails runner "
  user = User.find_by(username: 'root')
  user.password = 'NewPassword123!'
  user.password_confirmation = 'NewPassword123!'
  user.save!
  puts 'Root password reset successfully'
"

# 5. 修改核心配置
sudo vi /etc/gitlab/gitlab.rb

# 关键配置项：
# external_url 'https://gitlab.example.com'
# gitlab_rails['time_zone'] = 'Asia/Shanghai'
# gitlab_rails['backup_path'] = '/data/gitlab-backups'
# gitlab_rails['backup_keep_time'] = 604800  # 7天（秒）
# gitlab_rails['smtp_enable'] = true
# ...（同 Docker 版本的 SMTP 配置）

# 6. 应用配置（每次修改 gitlab.rb 后都需要执行）
sudo gitlab-ctl reconfigure

# 7. 查看组件状态
sudo gitlab-ctl status
# 输出示例:
# run: gitaly: (pid 1234) 120s
# run: gitlab-workhorse: (pid 1235) 120s
# run: logrotate: (pid 1250) 119s
# run: nginx: (pid 1236) 120s
# run: postgresql: (pid 1200) 121s
# run: puma: (pid 1230) 120s
# run: redis: (pid 1100) 122s
# run: sidekiq: (pid 1231) 120s
```

**Omnibus 常用运维命令**：

```bash
# 启动/停止/重启全部服务
sudo gitlab-ctl start
sudo gitlab-ctl stop
sudo gitlab-ctl restart

# 只重启特定组件
sudo gitlab-ctl restart puma
sudo gitlab-ctl restart gitaly

# 查看特定组件日志
sudo gitlab-ctl tail puma
sudo gitlab-ctl tail gitaly
sudo gitlab-ctl tail sidekiq

# 数据库操作
sudo gitlab-psql -d gitlabhq_production     # 连接 PostgreSQL
sudo gitlab-rails console                    # Rails 控制台（谨慎操作！）
sudo gitlab-rails db:migrate:status          # 查看数据库迁移状态

# 备份与恢复
sudo gitlab-backup create                    # 创建完整备份
sudo gitlab-backup restore BACKUP=1234567890 # 恢复指定备份
```

#### 步骤3：Kubernetes Helm Chart 方式部署

**目标**：在 K8s 集群中通过 Helm 部署 GitLab（生产级多节点架构）。

```bash
# 1. 添加 GitLab Helm 仓库
helm repo add gitlab https://charts.gitlab.io/
helm repo update

# 2. 导出默认配置模板
helm show values gitlab/gitlab > gitlab-values.yaml

# 3. 编辑核心配置
vi gitlab-values.yaml

# 最小生产配置（关键参数）：
cat << 'EOF' > gitlab-values.yaml
global:
  hosts:
    domain: example.com        # 你的域名
    https: true
    externalIP: 1.2.3.4        # 负载均衡器 IP（或注释掉用 Ingress）
  ingress:
    configureCertmanager: true  # 自动 Let's Encrypt 证书
    tls:
      secretName: gitlab-tls
    # 或使用已有的 TLS Secret
  email:
    from: gitlab@example.com
    display_name: GitLab
    smtp:
      enabled: true
      address: smtp.qq.com
      port: 587
      user_name: noreply@qq.com
      password:
        secret: smtp-password
        key: password
      domain: qq.com
      authentication: login

# 根据团队规模调整资源
gitlab:
  webservice:
    replicas: 2
    resources:
      requests:
        memory: 2G
        cpu: 1
      limits:
        memory: 4G
        cpu: 2
  sidekiq:
    resources:
      requests:
        memory: 1G
        cpu: 0.5
  gitaly:
    persistence:
      size: 50Gi
      storageClass: ssd  # 使用 SSD 存储类

postgresql:
  image:
    tag: 14.9.0
  resources:
    requests:
      memory: 2G
      cpu: 1
    limits:
      memory: 4G
      cpu: 2

redis:
  resources:
    requests:
      memory: 1G
      cpu: 0.5

# 关闭不需要的功能节省资源
certmanager-issuer:
  email: admin@example.com
EOF

# 4. 创建命名空间并部署
kubectl create namespace gitlab
helm upgrade --install gitlab gitlab/gitlab \
  --namespace gitlab \
  --timeout 600s \
  --values gitlab-values.yaml

# 5. 获取 root 密码
kubectl get secret -n gitlab gitlab-gitlab-initial-root-password \
  -o jsonpath='{.data.password}' | base64 --decode && echo

# 6. 监控部署进度
kubectl get pods -n gitlab -w
# 等待所有 Pod 变为 Running 状态（约 10-15 分钟）
```

### 测试验证

```bash
# 验证1：测试 API 可用性（三选一）
# Docker 环境
curl -s http://localhost/api/v4/version | python3 -m json.tool

# Omnibus 环境
curl -s https://gitlab.example.com/api/v4/version | python3 -m json.tool

# K8s 环境
curl -s https://gitlab.example.com/api/v4/version | python3 -m json.tool

# 验证2：测试 Git 操作
git clone https://gitlab.example.com/root/test-project.git
# 确认 clone 成功

# 验证3：测试 SSH 操作（Docker 需确认端口映射）
ssh -T -p 2224 git@localhost
# 输出：Welcome to GitLab, @root!

# 验证4：测试邮件发送（Omnibus/Docker）
sudo gitlab-rails runner "
  Notify.test_email('your@email.com', 'Test Subject', 'Test Body').deliver_now
  puts 'Email sent!'
"
# 检查收件箱确认
```

### 完整代码清单

- `docker-compose.yml`：Docker 单机部署配置（步骤1）
- `gitlab-values.yaml`：K8s Helm Chart 配置（步骤3）
- Omnibus 安装脚本：一键安装命令（步骤2）

## 4. 项目总结

### 优点 & 缺点

| 部署方式 | 优点 | 缺点 | 适用团队规模 |
|---------|------|------|-------------|
| Docker Compose | 一键启动，5 分钟可跑；环境隔离；搬家方便 | 单容器内所有组件无法独立扩缩容；性能开销 | < 50 人 |
| Omnibus | 原生性能；社区资料丰富；升级路径清晰 | 与宿主机耦合；多节点部署需手动配置 | 50-500 人 |
| Helm Chart | 组件解耦；独立扩容；CI/CD 友好 | 复杂度高；需要 K8s 运维经验；调试困难 | 500+ 人 |

### 适用场景

- **Docker Compose**：个人学习、小团队内部测试、轻量级 CI/CD
- **Omnibus**：虚拟机/物理机部署的生产环境、对性能有较高要求的场景
- **Helm Chart**：已有 K8s 集群的团队、需要弹性扩缩容的规模化场景

**不适用场景**：
- 没有任何 Linux/容器基础的纯 Windows 环境（建议用 Docker Desktop 入门）
- 要求极致简单的场景（考虑 Gitea）

### 注意事项

- **external_url 不可事后随意修改**：GitLab 数据库中存储了大量绝对 URL 路径，修改后需手动更新
- **备份先行**：首次部署完成后的第一件事应该是配置自动备份，而非开始使用
- **内存底线**：无论哪种部署方式，GitLab 至少需要 4GB 内存，低于此值会频繁 502
- **存储类型**：Gitaly 仓库数据必须放在 SSD 上，绝不能使用 NFS（Git 操作对 IOPS 要求极高）

### 常见踩坑经验

1. **Docker 数据丢失**：未挂载 volumes 就启动容器，重启后所有数据消失。根因：不熟悉 Docker 持久化机制。解决：时刻确认 docker-compose.yml 中 volumes 字段完整。
2. **Omnibus reconfigure 后 502**：修改 gitlab.rb 后部分组件启动失败。根因：配置语法错误或端口冲突。解决：`sudo gitlab-ctl tail` 查看具体组件日志。
3. **Helm Chart 中 Gitaly PVC 爆满**：未配置 cleanup policy，Git 仓库持续增长。根因：未设置 `gitaly.persistence.size` 的合理值和监控告警。

### 思考题

1. 如果一台 8GB 内存的服务器已经运行了 GitLab Omnibus 版，现在业务要求将 PostgreSQL 迁移到独立的高可用集群上，你需要修改 gitlab.rb 中的哪些参数？迁移步骤应该如何设计？
2. Docker Compose 部署的 GitLab，如何在不丢失数据的情况下升级版本？（提示：docker-compose.yml 中变更 image tag）

> 答案见附录 D。

### 推广计划提示

- **运维**：三种部署方式都要了解，但重点深耕团队实际使用的那一种，其他作为知识储备
- **开发**：理解 external_url 和 volume 挂载的意义，后续开发自定义 CI 脚本时会用到
- **测试**：学会验证部署成功的方法（API 测试 + Git 操作测试），这是搭建测试环境的基础技能
