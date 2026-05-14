# 第15章：GitLab 日常运维基础

## 1. 项目背景

> **业务场景**：一家公司使用 GitLab 半年后，运维团队发现日常运维工作远比想象中复杂——每周都要处理 502 错误、磁盘空间告警、Runner 离线等突发问题。最严重的一次，GitLab 的 PostgreSQL 磁盘写满导致整个平台不可用 3 小时，因为团队不知道如何快速清理 CI Artifacts 占用的大量空间。

运维负责人坦言："我们只学会了'装'和'用'，但没有人教我们'维稳'。"团队面对的问题包括：
- 早上 10 点开发高峰期频繁 502，但不知道是 Puma worker 不够还是 Gitaly 超时
- 磁盘空间每周一告警，但不知道是 Git 仓库、Artifacts、Registry 还是日志在膨胀
- Runner 突然离线但日志没有任何错误，重启又能恢复——找不到根因
- 想改个配置但不确定 reload 和 restart 的区别，怕影响在线用户
- 有同事误操作删除了重要项目，但没有最近的备份可以恢复

**痛点放大**：GitLab 的生产运维需要掌握的不仅是一组命令，而是一套运维思维——知道在哪里看日志、怎么分析瓶颈、怎么做容量规划、怎么设计备份恢复。任何平台"能用"和"稳用"之间的差距，就是运维的价值。

## 2. 项目设计——剧本式交锋对话

**场景**：值班群突然弹出告警——GitLab 502 错误率飙升，运维小王紧急排查。

---

**小王**："又 502 了！这是本周第三次了。每次都是重启 Puma 就好，但过两天又出现。有没有彻底的解决办法？"

**大师**："502 的本质是前端 Nginx/Workhorse 无法连接到后端的 Puma 服务。但造成 502 的原因至少有 5 种——Puma worker 耗尽、Sidekiq 占用太多内存导致 Puma 被 OOM kill、Gitaly 慢查询阻塞了 Puma 的请求线程、数据库连接池满了 Puma 在等待、甚至只是 CPU 被打满了。你需要通过监控数据来判断是哪一种。"

**小胖**："那怎么看是哪种原因？每次 502 我就重启，治标不治本。"

**大师**："第一个要看的是 Puma 的日志——`/var/log/gitlab/puma/puma_stdout.log`。如果日志里有 `Out of memory` 或 worker 频繁重启，说明是内存不够——增加 Puma worker 的内存或减少 worker 数量。第二个是 `gitlab-ctl tail gitaly`——如果在 502 的时间点出现大量 `RPC timeout`，说明 Gitaly 是瓶颈。第三个是 PostgreSQL——`gitlab-psql` 进去查 `SELECT * FROM pg_stat_activity WHERE state = 'active'`，看是否有长时间运行的慢查询。"

**小白**："那磁盘空间告警呢？我们每个月都要手动清理空间。"

**大师**："磁盘空间的来源有四个：Git 仓库数据（最大头）、CI Artifacts（第二大）、Container Registry 镜像、日志文件。你需要分别排查：用 `du -sh /var/opt/gitlab/git-data/repositories` 看仓库大小，用 `gitlab-rake gitlab:cleanup:orphan_job_artifact_files` 清理孤儿 artifacts，用 `du -sh /var/opt/gitlab/gitlab-rails/shared/artifacts` 看 artifacts 占用。最重要的是——配置自动清理策略，而不是等告警了才手动清理。"

**小王**："备份恢复呢？我们每周手动备份一次，但从来没真的恢复过——不知道流程能不能走通。"

**大师**："备份不是做了就行，而是要验证。一个完整的备份恢复验证流程至少应该每季度演练一次：在备用服务器上执行 `gitlab-backup restore` → `gitlab-ctl reconfigure` → 验证数据完整性 → 验证 Git 操作正常 → 验证 CI Pipeline 能跑。技术映射——备份就像买保险，买了不验证就像买了保险但不知道理赔流程。"

---

## 3. 项目实战

### 环境准备

> **目标**：掌握 GitLab 日常运维的核心操作——状态检查、日志分析、故障排查、备份恢复、容量清理。

**前置条件**：Omnibus 部署的 GitLab 实例（有 sudo 权限）。

### 分步实现

#### 步骤1：系统状态全面检查

**目标**：一条命令检查 GitLab 所有组件状态，快速评估系统健康度。

```bash
# ===== 1. GitLab 组件状态 =====
sudo gitlab-ctl status
# 期望输出：所有组件都显示 "run: <component>: (pid XXXX) ..."

# 如果某个组件状态为 "down"，单独查看：
sudo gitlab-ctl tail <component-name>

# ===== 2. GitLab 综合健康检查 =====
sudo gitlab-rake gitlab:check SANITIZE=true
# 检查项目：
# - GitLab 配置是否正确
# - 数据库是否可以连接
# - Redis 是否可以连接
# - Git 仓库权限是否正确
# - 卫星仓库是否同步

# ===== 3. 环境信息 =====
sudo gitlab-rake gitlab:env:info
# 输出：GitLab 版本、Ruby 版本、PostgreSQL 版本、系统信息

# ===== 4. 数据库迁移状态 =====
sudo gitlab-rake db:migrate:status
# 确认所有 migration 都是 "up" 状态

# ===== 5. HTTP 健康检查端点（无需 sudo）=====
curl -s http://localhost/-/health
# 返回 "GitLab OK" 表示核心服务正常

curl -s http://localhost/-/readiness
# 返回 JSON，包含各子系统的就绪状态：
# {"db_check":{"status":"ok"},"redis_check":{"status":"ok"},"gitaly_check":{"status":"ok"}}

# ===== 6. 系统资源 =====
free -h                 # 内存使用
df -h                   # 磁盘使用
top -bn1 | head -5      # CPU 负载
```

#### 步骤2：日志分析与故障定位

**目标**：学会查看不同组件的日志，快速定位常见故障。

```bash
# ===== GitLab 日志体系 =====
# 日志目录结构：
# /var/log/gitlab/
# ├── puma/          ← Rails 应用日志（Web 请求）
# ├── gitaly/        ← Git 操作日志
# ├── sidekiq/       ← 异步任务日志
# ├── gitlab-shell/  ← SSH Git 操作日志
# ├── nginx/         ← 反向代理日志
# ├── postgresql/    ← 数据库日志
# └── redis/         ← 缓存日志

# ===== 场景1：排查 502 错误 =====
# 1. 查看 Nginx 错误日志
sudo tail -100 /var/log/gitlab/nginx/gitlab_error.log | grep "502"
# 如果看到 "connect() failed (111: Connection refused) while connecting to upstream"
# 说明 Puma 没有在监听端口

# 2. 查看 Puma 日志
sudo tail -100 /var/log/gitlab/puma/puma_stdout.log
# 查找 "Out of memory"、"worker timeout" 等关键字

# 3. 查看系统日志确认是否是 OOM kill
sudo dmesg | grep -i "killed process"
# 如果看到 "Killed process puma"，说明 Puma 被 OOM killer 杀掉了

# ===== 场景2：Git 操作慢 =====
# 查看 Gitaly RPC 延迟
sudo grep "grpc.request.latency" /var/log/gitlab/gitaly/current | tail -20
# 如果看到大量 >500ms 的请求，说明 Gitaly 负载过高

# ===== 场景3：Runner 调度异常 =====
# 查看 Sidekiq 日志
sudo tail -100 /var/log/gitlab/sidekiq/current | grep "Pipeline"
# 查看是否有 "CreatePipelineWorker" 或 "RunPipelineScheduleWorker" 错误

# ===== 实时追踪所有日志 =====
sudo gitlab-ctl tail                  # 所有组件
sudo gitlab-ctl tail puma             # 只看 Puma
sudo gitlab-ctl tail gitaly sidekiq   # 只看 Gitaly + Sidekiq
```

#### 步骤3：备份与恢复演练

**目标**：执行一次完整的备份+恢复流程，验证数据完整性。

```bash
# ===== 备份 =====

# 1. 查看当前备份配置
sudo grep -A 10 "backup" /etc/gitlab/gitlab.rb
# 关键配置：
# gitlab_rails['backup_path'] = "/var/opt/gitlab/backups"
# gitlab_rails['backup_keep_time'] = 604800  # 保留 7 天

# 2. 执行完整备份
sudo gitlab-backup create
# 备份内容包括：
# - 数据库（PostgreSQL 全量 dump）
# - Git 仓库数据
# - 上传附件
# - CI Artifacts
# - Container Registry 镜像（如果有）
# - GitLab Pages 内容
# - 包仓库

# 备份文件位置：
# /var/opt/gitlab/backups/<timestamp>_<version>_gitlab_backup.tar

# 3. 同时备份配置文件（备份脚本不包括这些）：
sudo tar -czf /var/opt/gitlab/backups/gitlab-config-$(date +%s).tar.gz \
  /etc/gitlab/gitlab.rb \
  /etc/gitlab/gitlab-secrets.json

# 4. 自动备份（crontab 定时任务）
sudo crontab -l
# 添加：
# 0 2 * * * /opt/gitlab/bin/gitlab-backup create CRON=1

# ===== 恢复（在备用服务器上验证）=====

# 1. 确认备份文件存在
ls -la /var/opt/gitlab/backups/*gitlab_backup.tar

# 2. 先恢复配置文件
sudo cp gitlab-config-*.tar.gz /tmp/ && cd /tmp
sudo tar -xzf gitlab-config-*.tar.gz
sudo cp /tmp/etc/gitlab/gitlab.rb /etc/gitlab/
sudo cp /tmp/etc/gitlab/gitlab-secrets.json /etc/gitlab/

# 3. 停止相关服务
sudo gitlab-ctl stop puma
sudo gitlab-ctl stop sidekiq

# 4. 执行恢复（BACKUP=时间戳部分）
sudo gitlab-backup restore BACKUP=1715472000_2026_05_12_17.0.0

# 5. 重配置并重启
sudo gitlab-ctl reconfigure
sudo gitlab-ctl restart

# 6. 验证数据完整性
sudo gitlab-rake gitlab:check SANITIZE=true

# 7. 验证 Git 操作
git clone http://gitlab.local/repo.git /tmp/test-clone
# 确认可以克隆并文件完整

# 8. 清理验证环境
sudo gitlab-ctl stop
```

#### 步骤4：磁盘空间管理与清理

**目标**：分析磁盘空间占用，配置自动清理策略。

```bash
# ===== 1. 分析磁盘占用 =====
sudo du -sh /var/opt/gitlab/* | sort -hr | head -10
# 典型输出：
# 50G  /var/opt/gitlab/git-data      ← Git 仓库
# 20G  /var/opt/gitlab/gitlab-rails   ← Artifacts + 上传
# 10G  /var/opt/gitlab/postgresql     ← 数据库
# 5G   /var/log/gitlab                ← 日志

# Git 仓库详细分析
sudo du -sh /var/opt/gitlab/git-data/repositories/@hashed/*/* | sort -hr | head -10

# Artifacts 分析
sudo du -sh /var/opt/gitlab/gitlab-rails/shared/artifacts

# ===== 2. 清理过期 Artifacts =====
# 查看过期 artifacts 数量
sudo gitlab-rake gitlab:cleanup:orphan_job_artifact_files DRY_RUN=1

# 实际清理
sudo gitlab-rake gitlab:cleanup:orphan_job_artifact_files DRY_RUN=0

# ===== 3. 清理 Docker Registry =====
# GitLab 17.x 注册表垃圾回收
sudo gitlab-ctl registry-garbage-collect -m
# -m 参数：删除未引用的 manifest（更彻底的清理）

# ===== 4. 清理 Git 仓库（Git GC）=====
# 查找大仓库（> 100MB）
sudo gitlab-rails runner "
Project.find_each do |p|
  size = p.statistics&.repository_size || 0
  if size > 100 * 1024 * 1024  # > 100MB
    puts \"#{p.full_path}: #{size / 1024 / 1024}MB\"
  end
end
"

# 对大仓库执行 GC（在 Gitaly 节点上）
# 注意：这会短暂锁定仓库
sudo /opt/gitlab/embedded/bin/git -C /var/opt/gitlab/git-data/repositories/@hashed/xx/xx/xxx.git gc --aggressive

# ===== 5. 清理旧备份 =====
# 检查备份保留策略
sudo grep backup_keep_time /etc/gitlab/gitlab.rb

# 手动清理 30 天前的备份
sudo find /var/opt/gitlab/backups -name "*_gitlab_backup.tar" -mtime +30 -delete
```

#### 步骤5：常用运维配置热更新

**目标**：理解 reconfigure、restart、reload 的区别，避免误操作导致服务中断。

```bash
# ===== 配置文件修改流程 =====
# 1. 修改配置文件
sudo vi /etc/gitlab/gitlab.rb

# 2. 检查配置语法（测试模式，不会应用）
sudo gitlab-ctl check-config
# 输出 "OK" 表示配置语法正确

# 3. 应用配置并重启受影响的组件
sudo gitlab-ctl reconfigure
# reconfigure = 重新生成所有组件的配置文件 + 重启受影响的组件
# 注意：这可能导致短暂的服务中断（通常 10-30 秒）

# ===== 热重载 vs 重启的区别 =====
# gitlab-ctl hup <service>    - 发送 HUP 信号，重载配置但不中断连接
# gitlab-ctl restart <service> - 完全停止再启动，有短暂中断
# gitlab-ctl reconfigure       - 重新生成配置文件 + 重启组件

# Nginx 热重载（无中断）：
sudo gitlab-ctl hup nginx

# Puma 热重载（无中断，worker 逐个重启）：
sudo gitlab-ctl hup puma

# 需要完全重启的情况：
sudo gitlab-ctl restart        # 所有组件重启（有中断，约 1-2 分钟）
sudo gitlab-ctl restart puma   # 单个组件重启

# ===== 版本升级流程 =====
# 1. 检查当前版本
sudo gitlab-rake gitlab:env:info | grep "GitLab version"

# 2. 查阅升级路径（官方文档）
# https://docs.gitlab.com/ee/update/index.html#upgrade-paths

# 3. 备份（必须在升级前备份！）
sudo gitlab-backup create

# 4. 停止部分服务（可选，减少升级期间的数据变化）
sudo gitlab-ctl stop puma sidekiq

# 5. 升级
sudo apt-get update
sudo apt-get install gitlab-ce=<target-version>

# 6. 数据库迁移（升级后自动执行，也可手动）
sudo gitlab-rake db:migrate:status
```

### 完整代码清单

- 状态检查命令脚本（步骤1）
- 日志分析速查表（步骤2）
- 备份恢复流程脚本（步骤3）
- 磁盘清理命令集（步骤4）

### 测试验证

```bash
# 验证1：健康检查
curl -s http://localhost/-/health && echo "GitLab is healthy"

# 验证2：备份文件完整
sudo tar -tzf /var/opt/gitlab/backups/latest_gitlab_backup.tar | head -20

# 验证3：数据库连接
sudo gitlab-psql -c "SELECT version();"
# 应返回 PostgreSQL 版本

# 验证4：Redis 连接
sudo gitlab-redis-cli PING
# 应返回 PONG

# 验证5：Gitaly 连通性
sudo /opt/gitlab/embedded/bin/gitaly check /var/opt/gitlab/gitaly/config.toml
```

## 4. 项目总结

### 优点 & 缺点

| 操作 | 优点 | 缺点 |
|------|------|------|
| gitlab-ctl status | 一条命令查看所有组件状态 | 仅显示是否在运行，不显示健康状态 |
| gitlab-ctl tail | 实时追踪日志，多组件同时监控 | 高负载时输出太多，难以过滤 |
| gitlab-backup | 一键全量备份 | 大实例备份耗时长，备份期间有性能影响 |
| gitlab-rake cleanup | 定期清理避免磁盘爆满 | 部分清理命令文档不全，需要验证 |

### 常用故障排查矩阵

| 现象 | 可能原因 | 排查命令 |
|------|---------|---------|
| 502 错误 | Puma 不可用 | `gitlab-ctl tail puma` + `free -h` |
| Git push 超时 | Gitaly 负载高 | `gitlab-ctl tail gitaly` + `iostat -x 1` |
| Runner 不调度 | Sidekiq 队列积压 | `gitlab-ctl tail sidekiq` + `gitlab-redis-cli LLEN queue:default` |
| 页面加载慢 | 数据库慢查询 | `gitlab-psql` + 查 `pg_stat_activity` |
| 磁盘告警 | Artifacts/日志膨胀 | `du -sh /var/opt/gitlab/*` |

### 注意事项

- **升级前必须备份**：这是 GitLab 运维的铁律——没有备份的升级就是赌博
- **reconfigure 不等于 restart**：reconfigure 会重写配置文件并用新的配置启动组件
- **不要随意清理 /var/opt/gitlab/git-data 下的文件**：Git 仓库的磁盘布局是哈希目录结构
- **至少每季度做一次恢复演练**：验证备份可用性和恢复流程

### 常见踩坑经验

1. **备份恢复后 GitLab 无法启动**：恢复后忘记执行 `gitlab-ctl reconfigure`。根因：备份恢复只恢复了数据，没恢复组件配置。解决：恢复后必须执行 reconfigure 让各组件重新读取配置。
2. **磁盘满了但不知道谁占的**：用 du 一层层查发现是 `@hashed` 目录下某个大仓库。根因：某个项目有大量二进制文件频繁提交（如设计师把 PSD 文件也提交了）。解决：配置 `push rule` 限制文件大小，对存在的仓库执行 `git gc --aggressive`。
3. **gitlab-ctl reconfigure 报错**：修改 gitlab.rb 时语法错误。根因：Ruby 配置文件的引号、逗号缺失。解决：先用 `gitlab-ctl check-config` 验证语法，修改后对比官方文档。

### 思考题

1. GitLab 的 PostgreSQL、Redis、Gitaly 三个核心组件如果分别宕机，对用户的影响是什么？哪些操作可以正常进行，哪些会失败？
2. 你发现某个 GitLab 实例的磁盘 IO util 持续 100%，但 CPU 和内存都正常。这可能是什么原因？如何定位是哪个组件在大量读写磁盘？

> 答案见附录 D。

### 推广计划提示

- **运维**：本章是 GitLab 运维的"驾驶手册"，务必建立起状态检查、日志分析、备份恢复的日常习惯
- **开发**：了解 502/503 等常见错误的排查思路后，遇到问题可以第一时间做初步诊断再找运维
- **测试**：备份恢复流程应该包含在测试团队的灾备演练计划中
