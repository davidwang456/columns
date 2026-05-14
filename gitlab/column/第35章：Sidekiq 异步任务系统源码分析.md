# 第35章：Sidekiq 异步任务系统源码分析

## 1. 项目背景

> **业务场景**：某公司的 GitLab 实例突然不能创建新的 CI Pipeline——用户点击 "Run Pipeline" 后界面一直转圈，最终超时。运维查看日志发现 Sidekiq 有三个队列严重积压：`default` 队列有 80,000 条任务、`mailers` 队列有 15,000 条、`pipeline_processing` 队列有 5,000 条。运维想清理积压，但担心误删重要任务——比如正在处理的项目删除任务。

排查发现，积压的根源不是 Sidekiq 本身，而是外部 SMTP 服务器响应极慢（每次连接要 30 秒超时），而 GitLab 在每次 MR 合并时都同步发送邮件通知。大量 `NotificationMailer` Worker 占据了所有 Sidekiq 线程，其他任务排队等待。运维把 SMTP 连接超时从默认的 30 秒改成 5 秒后，积压很快恢复了。

**痛点放大**：Sidekiq 是 GitLab 的异步引擎——处理 Webhook、发送邮件、生成 CI Pipeline、计算 MR diff、清理缓存、导入导出项目……几乎所有不要求即时响应的操作都在 Sidekiq 中运行。理解 Sidekiq 在 GitLab 中的设计模式（队列优先级、Worker 分类、幂等性、重试策略），是 GitLab 运维调优和自定义扩展的基础技能。

## 2. 项目设计——剧本式交锋对话

**场景**：Sidekiq 积压故障的复盘会。运维小王在投影仪上展示 Sidekiq 的 Grafana 面板。

---

**小胖**（看着积压曲线）："所以 Sidekiq 就是 Redis 队列 + 多线程 Worker 嘛——有任务来了就处理，怎么还会积压 8 万条？CPU 又不高。"

**大师**："积压不是 CPU 的问题，是'等待'的问题。Sidekiq 默认有 25 个 worker 线程——如果 25 个线程都在等 SMTP 服务器 30 秒超时，那这 30 秒内没有任何其他任务能被处理。这就好比一家银行有 25 个柜台，但 25 个柜员都在打电话给同一个没人接的号码——后面排队的 100 个人只能干等。技术映射——Sidekiq 的线程就像银行柜台，CPU 占用低不代表效率高，可能都在空等。"

**小胖**："那为什么不把邮件 Worker 放到单独的队列里，占用独立的线程池？"

**大师**："GitLab 确实做了队列隔离——`mailers` 是一个独立的队列。但默认情况下，Sidekiq 的 25 个线程是共享的——所有队列共享这 25 个线程。你可以通过 `queue_groups` 配置给不同队列分配不同数量的线程。比如给 `mailers` 只分配 2 个线程——即使 SMTP 挂了，也只占用 2 个线程而不是全部。"

**小白**："我数了数 `app/workers/` 目录下有 300+ 个 Worker 文件。有什么规律可以快速分类吗？"

**大师**："可以从三个维度分类。第一，按队列——`critical`（系统关键）、`mailers`（邮件）、`pipeline_processing`（CI）、`default`（通用）、`project_export`（导出）、`project_destroy`（删除）。第二，按紧急度——`urgency :high/:medium/:low`。第三，按资源类型——`worker_resource_boundary :cpu/:memory/:unknown`。通过这三个标签，你可以快速判断某个 Worker 的优先级和资源需求。"

**小胖**："我想自己写一个 Worker——每月生成项目健康度报告——要怎么弄？"

**大师**："分四步：第一步，在 `app/workers/` 下创建 Worker 类，include `ApplicationWorker`。第二步，声明队列和紧急度（`sidekiq_options queue: :default, retry: 3`）。第三步，实现 `perform` 方法——这是入口。第四步，如果有定时需求，用 GitLab 的 `CronWorker` 或在 GitLab UI 中创建 Pipeline Schedule。最关键的——声明 `idempotent!`（幂等性），确保任务重试不会产生副作用。"

**小白**："idempotent! 是什么意思？"

**大师**："幂等性 = 一个任务执行 1 次和执行 10 次结果相同。比如'给用户发送激活邮件'——如果重试了，用户会收到 10 封一样的邮件，这就是不幂等。GitLab 的 Worker 有大量是幂等的——比如 ProcessCommitWorker 分析一个 commit 是否触发 CI，同一个 commit 分析 10 次结果不变。声明 `idempotent!` 告诉 Sidekiq 可以安全重试这个任务。技术映射——幂等就像电梯的关门按钮，按 1 次和按 10 次效果一样。"

---

## 3. 项目实战

### 环境准备

| 工具 | 用途 |
|------|------|
| GitLab 源码（第32章已克隆） | Worker 源码阅读 |
| GitLab Omnibus 17.x | 在真实环境观察 Sidekiq |
| Redis CLI | 直接查看队列状态 |

### 分步实现

#### 步骤1：Sidekiq 核心配置与队列体系

**目标**：理解 Sidekiq 的配置结构，掌握队列优先级和线程分配。

```ruby
# ====== 文件：config/initializers/10_sidekiq.rb ======
# （文件编号 10 表示在启动流程中较早执行）

Sidekiq.configure_server do |config|
  # Redis 连接配置
  config.redis = {
    url: Gitlab.config.redis.url,
    namespace: 'resque:gitlab'            # Sidekiq 兼容 Resque 的命名空间
  }

  # ⭐ 队列定义（按优先级排序，越靠前越先处理）
  config.queues = %w[
    critical                     # 系统关键（最短队列——保证快速响应）
    mailers                      # 邮件发送
    default                      # 大部分非关键任务
    pipeline_processing          # CI/CD Pipeline 处理
    pipeline_hooks               # Pipeline 触发后的 hooks（如部署后通知）
    project_export               # 项目导出（耗时长）
    project_destroy              # 项目删除（罕见但很重要）
    ci_pipeline_creation         # Pipeline 创建
    # ... 还有更多队列
  ]

  # ⭐ 队列组——给不同队列分配独立线程
  # 格式：'队列名1,权重1 队列名2,权重2'
  # 权重越高分配到的 CPU 时间越多
  config[:queue_groups] = [
    'critical,2  *',                      # critical 队列权重 2
    'mailers,1   *',                      # mailers 队列权重 1
    'default,1  *',
    'pipeline_processing,1 *',
    '*,1        *'                        # 其他队列权重 1
  ]

  # ⭐ 并发控制
  config.options[:concurrency] = 25       # 全局最大并发线程数
  config.options[:max_retries] = 25       # 任务最大重试次数

  # 死信队列配置
  config.options[:dead_max_jobs] = 10000  # 死信最多保留 10000 条
  config.options[:dead_timeout_in_seconds] = 180 * 24 * 3600  # 180 天后清理
end
```

**GitLab 队列分类速查**：

| 队列名 | 典型 Worker | 优先级 | 例行业务 |
|--------|-----------|--------|---------|
| `critical` | `SystemHookWorker`, `AuthorizedKeysWorker` | 最高 | 系统级别通知，SSH key 同步 |
| `mailers` | `NotificationMailer` | 高 | 邮件通知（MR/Issue/CI 结果） |
| `default` | `WebhookWorker`, `ProjectCacheWorker` | 中 | Webhook、缓存刷新 |
| `pipeline_processing` | `ProcessCommitWorker`, `PipelineHooksWorker` | 中 | CI Pipeline 生命周期 |
| `project_export` | `ProjectExportWorker` | 低 | 项目导出（可能跑数十分钟） |
| `project_destroy` | `ProjectDestroyWorker` | 低 | 异步删除大项目 |

#### 步骤2：典型 Worker 源码分析

**目标**：阅读两个典型 Worker 的实现，理解 GitLab 的 Worker 设计模式。

```ruby
# ====== Worker 示例 1：ProcessCommitWorker ======
# 文件：app/workers/ci/process_commit_worker.rb
# 职责：用户 push 代码后，分析 commit 变更，创建 CI Pipeline

module Ci
  class ProcessCommitWorker
    include ApplicationWorker       # ⭐ 所有 GitLab Worker 的基类模块

    # ⭐ Sidekiq 级别配置
    sidekiq_options retry: 3        # 失败重试 3 次

    # ⭐ GitLab 级别配置
    feature_category :continuous_integration   # 功能分类（用于监控）
    urgency :high                              # 紧急度：高
    worker_resource_boundary :cpu             # 资源类型：CPU 密集
    idempotent!                                # 声明幂等（同一 commit 多次处理结果一致）

    # ⭐ 核心逻辑入口
    def perform(project_id, user_id, commit_sha)
      # ⚠️ perform 的参数必须是简单类型（Integer/String/Array）
      #    不能传 ActiveRecord 对象——因为任务可能在几秒/几分钟后执行
      #    届时对象可能已被修改或删除
      project = Project.find(project_id)
      user = User.find(user_id)

      # 核心逻辑：创建 Pipeline
      Ci::CreatePipelineService.new(project, user, ref: commit_sha).execute
    rescue ActiveRecord::RecordNotFound => e
      # 如果项目/用户在任务执行前被删除，静默失败
      # 注意：声明了 idempotent! 意味着重试不会产生副作用
      logger.warn("ProcessCommitWorker: #{e.message}")
    end
  end
end

# ====== Worker 示例 2：WebhookWorker ======
# 文件：app/workers/hooks/webhook_worker.rb
# 职责：向外部 Webhook URL 发送 HTTP POST 请求

class WebhookWorker
  include ApplicationWorker

  sidekiq_options retry: 5, dead: false      # 重试 5 次，不进死信队列

  feature_category :integrations
  urgency :low                                # 低紧急度——Webhook 延迟几秒可接受

  def perform(hook_id, data, hook_name)
    hook = WebHook.find(hook_id)

    # 发送 HTTP 请求（可能超时）
    # ⚠️ 这里使用了 HTTP 超时设置——防止线程长时间等待
    hook.execute(data, hook_name, timeout: 10)
  rescue => e
    # 失败后重试（最多 5 次，间隔指数递增：30s → 60s → 2min → 5min → 15min）
    raise e
  end
end
```

**GitLab Worker 设计模式总结**：

| 模式要素 | 说明 | 示例 |
|---------|------|------|
| `include ApplicationWorker` | 所有 Worker 必须 include | 提供通用方法（日志、监控） |
| `sidekiq_options` | 重试次数、死信策略 | `retry: 3`, `dead: false` |
| `feature_category` | 功能分类标签 | `:continuous_integration` |
| `urgency` | 紧急度 | `:high`, `:medium`, `:low` |
| `worker_resource_boundary` | 资源特征 | `:cpu`, `:memory`, `:unknown` |
| `idempotent!` | 幂等声明 | 确保安全重试 |
| `perform(id, ...)` | 只传简单类型参数 | 不传 AR 对象 |

#### 步骤3：自定义 Worker 实战——项目健康度报告

**目标**：编写一个完整的 Sidekiq Worker，每月自动生成项目健康度报告并创建 Issue。

```ruby
# ====== 第一步：创建 Worker 文件 ======
# 文件：app/workers/projects/health_report_worker.rb

module Projects
  class HealthReportWorker
    include ApplicationWorker

    # Sidekiq 配置
    sidekiq_options retry: 2, queue: :default

    # GitLab 配置
    feature_category :projects
    urgency :low                     # 月报不紧急
    worker_resource_boundary :cpu    # 需要遍历所有项目
    idempotent!                      # 同一个月重复执行生成相同报告

    # ⭐ 入口方法
    def perform(report_admin_id = nil)
      # 获取报告管理员
      admin = if report_admin_id
                User.find(report_admin_id)
              else
                User.admins.first
              end

      return unless admin

      # 生成报告
      report = generate_health_report

      # 创建 Issue
      create_report_issue(report, admin)
    end

    private

    # 生成健康度报告数据
    def generate_health_report
      # 查询所有活跃项目（30 天内有活动）
      active_projects = Project
        .where('last_activity_at > ?', 30.days.ago)
        .includes(:statistics)
        .order('statistics.repository_size DESC NULLS LAST')

      active_projects.map do |project|
        stats = project.statistics
        {
          name: project.full_path,
          last_activity: project.last_activity_at&.strftime('%Y-%m-%d'),
          repo_size: format_size(stats&.repository_size || 0),
          open_issues: project.issues.opened.count,
          open_mrs: project.merge_requests.opened.count,
          ci_success_rate: calculate_ci_rate(project),
          health_score: calculate_health_score(project)
        }
      end
    end

    # 计算 CI 成功率
    def calculate_ci_rate(project)
      pipelines = project.pipelines.where('created_at > ?', 30.days.ago)
      total = pipelines.count
      return 100.0 if total.zero?
      succeeded = pipelines.success.count
      (succeeded.to_f / total * 100).round(1)
    end

    # 计算健康度评分
    def calculate_health_score(project)
      score = 100
      score -= 30 if project.last_activity_at && project.last_activity_at < 60.days.ago
      score -= 20 if project.merge_requests.opened.where('created_at < ?',14.days.ago).exists?
      score -= 10 if project.issues.opened.where('created_at < ?', 30.days.ago).exists?
      [score, 0].max
    end

    # 格式化文件大小
    def format_size(bytes)
      return '0 B' if bytes.zero?
      units = %w[B KB MB GB TB]
      exp = (Math.log(bytes) / Math.log(1024)).to_i
      exp = units.size - 1 if exp >= units.size
      format('%.1f %s', bytes.to_f / (1024 ** exp), units[exp])
    end

    # 创建 Issue 报告
    def create_report_issue(report, admin)
      report_project = Project.find_by_full_path('infra/reports')

      return unless report_project

      # 生成 Markdown 表格
      markdown = <<~MARKDOWN
        ## 项目健康度月报 - #{Date.today.strftime('%Y年%m月')}

        | 项目 | 最近活动 | 仓库大小 | Open Issues | Open MRs | CI成功率 | 健康度 |
        |------|---------|---------|------------|---------|---------|-------|
        #{report.map { |r| "| #{r[:name]} | #{r[:last_activity]} | #{r[:repo_size]} | #{r[:open_issues]} | #{r[:open_mrs]} | #{r[:ci_success_rate]}% | #{'🟢' if r[:health_score] >= 70}#{'🟡' if r[:health_score] < 70 && r[:health_score] >= 40}#{'🔴' if r[:health_score] < 40} #{r[:health_score]} |" }.join("\n")}

        > 报告自动生成于 #{Time.current.strftime('%Y-%m-%d %H:%M')}
        > 健康度评分：🟢 >= 70 🟡 40-69 🔴 < 40
      MARKDOWN

      Issue.create!(
        project: report_project,
        title: "📊 项目健康度月报 - #{Date.today.strftime('%Y年%m月')}",
        description: markdown,
        author: admin,
        labels: 'automated-report,health-check'
      )
    end
  end
end

# ====== 第二步：注册定时任务（Cron Job）======
# GitLab 的定时任务在 GitLab UI 中配置：
# Project → CI/CD → Schedules → New schedule
# 或通过 Rails Console 手动触发：
# Projects::HealthReportWorker.perform_async
```

#### 步骤4：Sidekiq 运维与诊断

**目标**：掌握 Sidekiq 的日常运维命令和故障诊断流程。

```bash
# ═══════ 状态查看 ═══════

# 1. Sidekiq 进程状态
sudo gitlab-ctl status sidekiq
# 输出：run: sidekiq: (pid 12345) 3600s

# 2. 查看所有队列的积压情况（排序）
sudo gitlab-rails runner "
  Sidekiq::Queue.all.sort_by { |q| -q.size }.each do |q|
    puts '#{q.name}: #{q.size} jobs'
  end
"
# 输出示例：
# default: 80342 jobs
# mailers: 15200 jobs
# pipeline_processing: 5230 jobs

# 3. 查看死信队列数量
sudo gitlab-rails runner "
  puts \"Dead jobs: #{Sidekiq::DeadSet.new.size}\"
"

# 4. 查看当前正在执行的 Worker
sudo gitlab-ctl tail sidekiq | grep -E "JID|perform"

# 5. 查看统计信息
sudo gitlab-rails runner "
  stats = Sidekiq::Stats.new
  puts \"Processed: #{stats.processed}\"
  puts \"Failed: #{stats.failed}\"
  puts \"Scheduled: #{stats.scheduled_size}\"
  puts \"Retries: #{stats.retry_size}\"
  puts \"Workers busy: #{stats.workers_size}\"
  puts \"Enqueued: #{stats.enqueued}\"
"

# ═══════ 队列管理 ═══════

# 6. 清空特定队列（⚠️ 谨慎操作！）
sudo gitlab-rails runner "
  Sidekiq::Queue.new('mailers').clear
  puts 'mailers queue cleared'
"

# 7. 查看特定队列的前 5 个任务（不消费）
sudo gitlab-rails runner "
  Sidekiq::Queue.new('default').first(5).each do |job|
    puts \"#{job.klass}: #{job.args}\"
  end
"

# 8. 重试所有死信任务
sudo gitlab-rails runner "
  dead_count = Sidekiq::DeadSet.new.size
  Sidekiq::DeadSet.new.each(&:retry)
  puts \"Retried #{dead_count} dead jobs\"
"

# 9. 查看 Sidekiq 实时日志
sudo gitlab-ctl tail sidekiq | grep -E "fail|error|timeout"
```

### 完整代码清单

- `config/initializers/10_sidekiq.rb`：Sidekiq 配置文件
- `app/workers/ci/process_commit_worker.rb`：ProcessCommitWorker
- `app/workers/hooks/webhook_worker.rb`：WebhookWorker
- `app/workers/projects/health_report_worker.rb`：自定义健康度报告 Worker
- Sidekiq 运维命令速查（步骤4）

### 测试验证

```bash
# 验证1：队列状态检查
sudo gitlab-rails runner "
  Sidekiq::Queue.all.each { |q| puts '#{q.name}: #{q.size}' }
"
# 期望：各队列积压 < 1000

# 验证2：自定义 Worker 手动触发
sudo gitlab-rails runner "
  Projects::HealthReportWorker.perform_async
  puts 'Health report worker enqueued'
"

# 验证3：确认 Worker 执行成功
sudo gitlab-rails runner "
  # 等几秒后检查
  stats = Sidekiq::Stats.new
  puts \"Failed after trigger: #{stats.failed}\"
"

# 验证4：查看 Redis 中的队列数据（原始数据）
sudo gitlab-redis-cli LLEN "resque:gitlab:queue:default"
# 返回队列中的任务数
```

## 4. 项目总结

### GitLab Worker 分类与资源特征

| Worker 类型 | 队列 | 紧急度 | 资源类型 | 典型耗时 |
|-----------|------|--------|---------|---------|
| `ProcessCommitWorker` | `pipeline_processing` | high | cpu | 1-10s |
| `WebhookWorker` | `default` | low | unknown（等网络） | 1-30s |
| `NotificationMailer` | `mailers` | medium | unknown（等 SMTP） | 0.5-30s |
| `ProjectExportWorker` | `project_export` | low | cpu + memory | 1-30min |
| `GarbageCollectWorker` | `default` | low | cpu + io | 10s-数小时 |
| `ProjectDestroyWorker` | `project_destroy` | medium | io | 1-10min |

### 优点 & 缺点

| 维度 | 优点 | 缺点 |
|------|------|------|
| 并发模型 | 多线程高效利用 CPU | 所有队列共享线程池，一个慢队列拖所有 |
| 重试机制 | 指数退避重试保证可靠性 | 不合理的重试次数会导致死信溢出 |
| 可观测性 | 自带 Web UI + Prometheus 指标 | 排查特定任务需要进入 Rails Console |

### 适用场景

- **异步通知**：MR 合并后的邮件/Webook 通知
- **耗时操作**：项目导入导出、大仓库 GC
- **定时任务**：周报、月报、定期缓存清理
- **事件驱动**：git push → 创建 CI Pipeline、issue 更新 → 同步 Jira

**不适用场景**：
- 需要即时反馈的操作（用户点击后必须 2 秒内看到结果）
- 超高频操作（每秒数千次——考虑用 Redis 原子操作）

### 注意事项

- **Worker 的 perform 参数必须是简单类型**：传 User 对象可能在执行时已删除
- **idempotent! 不是自动的**：声明了但代码里没实现幂等逻辑 = 没声明
- **不要在生产环境直接 `gitlab-rails runner "Sidekiq::Queue.new.clear"`**：操作不可逆
- **SMTP/外部 API 超时必须设置合理值**：否则一个慢下游拖垮整个 Sidekiq

### 常见踩坑经验

1. **Sidekiq 静默停止工作**：Redis 内存满了，Sidekiq 无法写入新的任务。根因：Redis 的 `maxmemory` 被写满且 `maxmemory-policy` 为 `noeviction`（拒绝写入）。解决：设置 `maxmemory-policy volatile-lru` 让 Redis 自动淘汰旧数据。
2. **Webhook 重试风暴**：外部 API 挂了，WebhookWorker 反复重试 5 次，大量任务涌入死信队列。根因：`retry: 5` 对临时故障太慷慨。解决：降低不幂等操作的 `retry` 次数，死信队列配置监控告警。
3. **ProcessCommitWorker 队列积压**：push 高峰期大量 commit 涌入。根因：GitLab merge 时引入大量 commit，每个都触发 ProcessCommitWorker。解决：合并策略选择 Squash and Merge（一个 MR = 一个 commit = 一个 Worker 调用）。

### 思考题

1. Sidekiq 的 `idempotent!` 声明确保了任务可以安全重试。如果编写一个"发送短信验证码"的 Worker，idempotent 应该设为 true 还是 false？如果必须重试，如何保证不重复发送？
2. 某 Worker 执行一次需要 5 秒，队列积压 10 万条任务——即使 25 个线程全速运行也需要 5.5 小时才能清空。在任务不可丢失的前提下，有哪些加速方案？（提示：考虑分批、优先级、独立线程池）

> 答案见附录 D。

### 推广计划提示

- **运维**：Sidekiq 监控应该纳入日常巡检——队列积压 > 5000 就要告警
- **开发**：编写自定义 Worker 时，严格遵守幂等、简单参数、合理重试三原则
- **架构师**：Sidekiq 的队列优先级设计模式可以推广到内部微服务的异步任务系统设计
