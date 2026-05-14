# 第33章：GitLab Rails 核心模块源码分析

## 1. 项目背景

> **业务场景**：一位架构师接到了一个任务——为 GitLab 添加一个自定义功能：在 MR 合并时自动检查代码变更范围，如果涉及到"支付"模块的代码，必须额外获得安全团队负责人审批。他熟悉 Rails，但从没给 GitLab 这种级别的项目写过扩展。面对 GitLab 的源码，他最核心的困惑有三个：

1. **权限系统**：DeclarativePolicy 是什么？为什么不用 CanCanCan？怎么给自定义功能加权限检查？
2. **CI/CD 状态机**：Pipeline 的状态是怎么流转的？`created → pending → running → success/failed` 这个链条在哪里定义？
3. **Merge Request 引擎**：MR 的 diff 是怎么计算的？为什么大项目（10 万 commit）的 MR diff 要加载 10 秒？

不搞清楚这三个核心模块的原理，他的自定义扩展要么有安全漏洞（权限绕过），要么有性能退化（导致 MR 页面加载卡顿）。

**痛点放大**：GitLab 的 Rails 代码库虽然庞大，但 80% 的日常扩展和调试需求只涉及三个子系统——权限系统（安全基线）、CI/CD 状态机（流程引擎）、Merge Request 引擎（协作核心）。理解这三个模块，就相当于拿到了 GitLab 源码的"三把钥匙"。

## 2. 项目设计——剧本式交锋对话

**场景**：源码阅读小组第二次聚会。架构师小李在白板上画出了 GitLab 的 Rails 分层图，三个红色圆圈圈出了他最困惑的模块。

---

**小李**（指着第一个红圈）："我先问权限系统。我看 `app/policies/` 目录下有好多 Policy 文件，每个文件里都在 `rule { ... }.policy do enable :xxx end`。这个语法跟 CanCanCan 的 `can :read, Project` 完全不一样——为什么 GitLab 要自己造轮子？"

**大师**："因为 GitLab 的权限场景比 CanCanCan 能处理的复杂一个数量级。举个例子——'用户能不能看到这个项目？'，在 GitLab 里不只看用户的角色（Developer/Maintainer），还要看项目的可见性（Private/Internal/Public）、用户的组织关系（是否是 Group 成员）、用户的账号状态（是否被 block）、甚至项目是否被 archived。这些条件之间还有 AND/OR 的组合关系。CanCanCan 的 `can` 语法表达不了这种复杂的条件推导。"

**小胖**："那 DeclarativePolicy 怎么解决这个问题的？"

**大师**："DeclarativePolicy 的核心思路是'条件+规则'分离。你先定义一组可复用的 `condition`——比如 `is_owner`、`is_private_project`、`is_internal_user`。然后定义 `rule`——它是一个或多个条件的组合。`rule { is_owner & ~is_blocked }.policy do enable :remove_project end` 的意思是——当用户是 owner 且没有被 blocked 时，允许删除项目。技术映射——这就像制定公司规章：条件（在办公室/远程）+ 角色（经理/员工）→ 规则（能否审批报销）。"

**小胖**（指着第二个红圈）："Pipeline 状态机呢？我看了 `app/models/ci/pipeline.rb`，里面用了一个叫 `state_machine` 的东西。这跟 Rails 的 enum 有什么区别？"

**大师**："Rails 的 `enum` 只是把一个整数字段映射为符号名字——`enum status: { created: 0, pending: 1 }`。它只管'当前是什么状态'，不管'可以从什么状态转换到什么状态'。`state_machines` gem 提供了完整的有限状态机——你可以定义状态之间的转换路径（transition）、过渡守卫（guard）、进入/离开回调（callback）。比如 Pipeline 不能从 `success` 直接变成 `failed`——状态机可以禁止这种非法转换。"

**小李**（指着第三个红圈）："MR diff 呢？我通过 gRPC 追踪发现最终是 Gitaly 的 `CommitDiff` RPC 在做计算——但为什么合并冲突检测还要再跑一遍？"

**大师**："MR diff 和冲突检测是两个独立的计算过程。MR diff 计算的是 source branch 和 merge base（公共祖先）之间的差异——告诉你'这个 MR 改了什么'。冲突检测计算的是 source branch 和 target branch 最新 commit 之间的合并冲突——告诉你'两个分支同时改同一个文件时能不能合并'。MR diff 可以一次计算、缓存复用；冲突检测必须在每次 push 到 source branch 时重新计算。技术映射——diff 就像看菜单（改了什么菜），冲突检测就像看库存（要的菜还有没有）。"

---

## 3. 项目实战

### 环境准备

| 工具 | 用途 |
|------|------|
| GitLab 源码（第32章已克隆） | 代码阅读 |
| Ruby 3.0+ / Rails 7.0+ | 理解 Rails 机制 |
| GitLab Omnibus 17.x（测试实例） | 实际运行并调试 |

### 分步实现

#### 步骤1：DeclarativePolicy 权限系统源码分析

**目标**：理解权限系统的三层结构——Condition → Rule → Ability，并动手写一个自定义 Policy。

**核心代码路径**：

```ruby
# ====== 第1层：条件定义 (Condition) ======
# 文件：app/policies/project_policy.rb（简化版）

class ProjectPolicy < BasePolicy
  # ⭐ 条件定义——每个 condition 是一个可复用的布尔表达式
  condition(:is_owner) do
    @user && @subject.owner == @user
  end

  condition(:is_maintainer) do
    @subject.team.max_member_access(@user.id) >= Gitlab::Access::MAINTAINER
  end

  condition(:is_developer) do
    @subject.team.max_member_access(@user.id) >= Gitlab::Access::DEVELOPER
  end

  condition(:is_reporter) do
    @subject.team.max_member_access(@user.id) >= Gitlab::Access::REPORTER
  end

  condition(:is_private_project) { @subject.private? }
  condition(:is_internal_project) { @subject.internal? }
  condition(:is_public_project) { @subject.public? }

  condition(:user_is_blocked) { @user&.blocked? }
  condition(:project_archived) { @subject.archived? }

  # ⭐ 第2层：规则定义 (Rule) —— 条件组合
  # 语法：rule { 条件1 & 条件2 }.policy do enable/disable :ability end
  # 规则中的 `~` 表示取反，`|` 表示 OR

  # Owner 权限（最高）
  rule { is_owner & ~user_is_blocked }.policy do
    enable :change_visibility_level
    enable :remove_project
    enable :change_namespace
    enable :manage_access
  end

  # Maintainer 权限
  rule { is_maintainer & ~project_archived }.policy do
    enable :push_to_delete_protected_branch
    enable :manage_merge_requests
    enable :manage_ci_cd
  end

  # Developer 权限
  rule { is_developer }.policy do
    enable :push_code
    enable :create_merge_request_in
    enable :read_wiki
  end

  # Reporter 权限（只读）
  rule { is_reporter }.policy do
    enable :read_project
    enable :read_issue
    enable :download_code
  end

  # 公开项目——任何人可读
  rule { is_public_project }.policy do
    enable :read_project
    enable :read_issue
  end

  # ⭐ 阻止规则（prevent 优先级 > enable）
  rule { ~is_owner & is_private_project & ~is_reporter }.policy do
    prevent :read_project
    prevent :read_issue
  end
end
```

**在 Controller 中使用权限检查**：

```ruby
# 文件：app/controllers/projects_controller.rb
class ProjectsController < ApplicationController
  before_action :authorize_read_project!, only: [:show, :files]

  def show
    # authorize_read_project! 内部执行：
    #   Ability.allowed?(current_user, :read_project, @project)
    #   → Ability 类在 lib/gitlab/auth/ability.rb
    #   → 找到 ProjectPolicy → 评估所有 rule
    #   → 返回最终权限判断 (true/false)
  end

  def destroy
    # 删除项目需要更高的权限
    return access_denied! unless can?(current_user, :remove_project, @project)
    # ...
  end
end
```

**实战：自定义权限检查**：

```ruby
# 需求：添加一个自定义权限——"can_view_financial_data"
# 只有 owner 且项目不是 archived 时才能看财务数据

# 第一步：在 ProjectPolicy 中添加
class ProjectPolicy < BasePolicy
  # ...已有条件...

  rule { is_owner & ~project_archived }.policy do
    enable :view_financial_data  # 新增权限
  end
end

# 第二步：在 Controller 中使用
class ProjectsController < ApplicationController
  def financial_data
    unless can?(current_user, :view_financial_data, @project)
      render_404 and return
    end
    # ... 返回财务数据 ...
  end
end
```

#### 步骤2：Pipeline 状态机源码分析

**目标**：理解 Pipeline/Job 的完整生命周期，追踪从 git push 到 Pipeline 完成的全过程。

```ruby
# ====== Pipeline 状态机（文件：app/models/ci/pipeline.rb）======
module Ci
  class Pipeline < ApplicationRecord
    # 定义状态机
    state_machine :status, initial: :created do
      # 状态转换定义
      event :enqueue do
        transition [:created, :skipped] => :pending
      end

      event :run do
        transition [:pending, :skipped] => :running
      end

      event :drop do
        transition [:created, :pending, :running] => :failed,
          if: ->(pipeline) { pipeline.failure_reason.present? }
      end

      event :succeed do
        transition running: :success
      end

      event :cancel do
        transition [:created, :pending, :running] => :canceled
      end

      event :skip do
        transition any => :skipped
      end

      # ⭐ 状态进入回调（after_transition）
      after_transition any => :running do |pipeline, transition|
        pipeline.run_hooks!       # 执行 Git hooks
      end

      after_transition any => :success do |pipeline, transition|
        pipeline.notify_success!  # 通知关注者
        pipeline.run_after_commit { MergeRequests::MergeWhenPipelineSucceedsService.new(pipeline).trigger }
      end

      after_transition any => :failed do |pipeline, transition|
        pipeline.notify_failure!
      end
    end

    # ====== Pipeline 的生命周期触发链 ======
    # 第1步：用户 git push
    # → GitLab Shell 接收 push → 触发 PostReceive Hook

    # 第2步：Sidekiq Worker 异步处理
    # → Ci::ProcessCommitWorker.perform_async(project_id, user_id, sha)

    # 第3步：创建 Pipeline
    # → Ci::CreatePipelineService.new(project, user, ref: ref).execute
    #   → Pipeline.create!(status: :created)
    #   → 解析 .gitlab-ci.yml → 创建 Build (Job) 记录

    # 第4步：Pipeline 排队
    # → Pipeline.enqueue → status 变为 :pending

    # 第5步：Runner 取 Job
    # → GET /api/v4/jobs/request (Runner 轮询)
    # → Ci::RegisterJobService 分配 Job 给 Runner
    # → Pipeline.run → status 变为 :running

    # 第6步：Runner 回传结果
    # → PATCH /api/v4/jobs/:id (Runner 回传 build log 和 退出码)
    # → 如果退出码 = 0 → Pipeline.succeed → status 变为 :success
    # → 如果退出码 ≠ 0 → Pipeline.drop → status 变为 :failed
  end
end

# ====== Job 状态机（文件：app/models/ci/build.rb）======
module Ci
  class Build < Processable
    state_machine :status, initial: :created do
      event :enqueue do
        transition [:created, :skipped, :manual] => :pending
      end

      event :run do
        transition pending: :running
      end

      event :drop do
        transition [:created, :pending, :running] => :failed
      end

      event :success do
        transition running: :success
      end

      event :cancel do
        transition [:created, :pending, :running] => :canceled
      end

      before_transition any => :running do |build|
        build.started_at = Time.current
      end

      before_transition any => [:success, :failed, :canceled] do |build|
        build.finished_at = Time.current
      end
    end
  end
end
```

**Pipeline 状态流转图**：

```
           push / schedule
                │
                ▼
           ┌─────────┐
           │ created │ ──────────────┐
           └────┬────┘               │
                │ enqueue            │ skip
                ▼                    ▼
           ┌─────────┐         ┌─────────┐
           │ pending │         │ skipped │
           └────┬────┘         └─────────┘
                │ run               │
                ▼                   │
           ┌─────────┐              │
           │ running │◄─────────────┘
           └────┬────┘
       ┌────────┼────────┐
       │        │        │
    succeed   drop    cancel
       │        │        │
       ▼        ▼        ▼
   ┌───────┐┌───────┐┌───────┐
   │success││ failed││canceled│
   └───────┘└───────┘└───────┘
```

#### 步骤3：Merge Request Diff 引擎源码分析

**目标**：理解 MR diff 的生成过程，定位性能瓶颈。

```ruby
# ====== MR Diff 生成流程（文件：app/services/merge_requests/）======

# 第1步：Git Push 触发 RefreshService
# 文件：app/services/merge_requests/refresh_service.rb
module MergeRequests
  class RefreshService
    def execute(oldrev, newrev, ref)
      # 1. 找到关联的 MR
      merge_request = find_merge_request(ref)

      # 2. 计算 merge base（source 和 target 的公共祖先 commit）
      #    这是最耗时的一步——需要在 DAG 中搜索
      merge_base = @repository.merge_base(
        merge_request.target_branch_ref,
        merge_request.source_branch_ref
      )

      # 3. 生成 diff（通过 Gitaly gRPC 调用）
      #    → Gitaly::CommitDiffRequest
      #    → gRPC streaming 返回 diff 数据
      diff = @repository.diff(merge_base, newrev)

      # 4. 保存 diff 到数据库
      MergeRequestDiff.create!(
        merge_request: merge_request,
        head_commit_sha: newrev,
        base_commit_sha: merge_base,
        state: :unfolded,
        diff_type: :regular
      )
    end
  end
end

# 第2步：Gitaly 端的 diff 计算（Ruby 客户端）
# 文件：lib/gitlab/git/diff.rb
module Gitlab::Git
  class Diff
    def between(base_sha, target_sha)
      # 构造 gRPC 请求
      request = Gitaly::CommitDiffRequest.new(
        repository: gitaly_repo,
        left_commit_id: base_sha,
        right_commit_id: target_sha,
        ignore_whitespace_change: false,
        paths: @paths,              # 可以按路径过滤
        max_files: 1000,            # 最多返回 1000 个文件
        max_lines: 50000            # 最多返回 50000 行
      )

      # 发起 gRPC streaming 调用
      # Gitaly 使用 server-side streaming —— 分批返回数据
      diffs = []
      GitalyClient.call(@repository.storage, :commit_service, :commit_diff, request) do |response|
        diffs << Gitlab::Git::Diff.new(response)
      end
      diffs
    end
  end
end

# ⭐ 为什么大 MR 的 diff 慢？
# 1. merge_base 计算：O(n) 遍历 commit DAG
# 2. diff 生成：O(files × lines) 对每个文件逐行比较
# 3. diff 高亮：GitLab 对代码做语法高亮（CPU 密集）
# 4. 存储：diff 序列化为 JSON 后存入 PostgreSQL

# ⭐ GitLab 的优化策略：
# 1. Collapse large diffs：默认只展开前 100 个文件的 diff
# 2. Background diff generation：Sidekiq Worker 异步计算
#    → app/workers/new_merge_request_diff_generation_worker.rb
# 3. Gitaly PackObjectsCache：减少重复计算
# 4. Diff batch loading：分批次发送 gRPC streaming
```

### 完整代码清单

- `app/policies/project_policy.rb`：权限系统完整示例
- `app/models/ci/pipeline.rb`：Pipeline 状态机核心代码
- `app/models/ci/build.rb`：Job 状态机核心代码
- `app/services/merge_requests/refresh_service.rb`：MR Diff 刷新入口
- `lib/gitlab/git/diff.rb`：Gitaly gRPC 客户端

### 测试验证

```bash
# 验证1：权限检查在 Rails Console 中
sudo gitlab-rails console
> user = User.find_by(username: 'root')
> project = Project.first
> Ability.allowed?(user, :read_project, project)
=> true
> Ability.allowed?(user, :remove_project, project)
=> true/false （取决于用户角色）

# 验证2：Pipeline 状态查询
sudo gitlab-rails runner "
  p = Ci::Pipeline.last
  puts \"Pipeline ##{p.id}: #{p.status}\"
  puts \"  Can succeed? #{p.can_succeed?}\"
  puts \"  Can drop? #{p.can_drop?}\"
"

# 验证3：跟踪 MR Diff 的耗时
sudo gitlab-ctl tail gitaly | grep "commit_diff"
# 观察 gRPC streaming 的耗时
# 输出示例: "grpc.request.fullMethod: /gitaly.CommitService/CommitDiff"
#           "grpc.request.duration: 0.452s"
```

## 4. 项目总结

### 三大核心模块速查

| 模块 | 入口文件 | 核心类 | 关键方法 |
|------|---------|--------|---------|
| 权限系统 | `app/policies/project_policy.rb` | `BasePolicy`, `Rule` | `condition`, `rule`, `enable/prevent` |
| CI Pipeline | `app/models/ci/pipeline.rb` | `Ci::Pipeline` | `enqueue`, `run`, `drop`, `succeed` |
| CI Job | `app/models/ci/build.rb` | `Ci::Build` | `enqueue`, `run`, `drop`, `success` |
| MR Diff | `app/services/merge_requests/refresh_service.rb` | `RefreshService` | `execute(oldrev, newrev, ref)` |
| Gitaly Diff | `lib/gitlab/git/diff.rb` | `Gitlab::Git::Diff` | `between(base, target)` |

### 优点 & 缺点

| 模块 | 优点 | 缺点 |
|------|------|------|
| DeclarativePolicy | 条件可复用、性能优于 CanCanCan | 学习曲线陡、调试困难 |
| state_machines | 状态流转路径清晰、有守卫回调 | gem 维护不活跃、状态多了代码冗长 |
| MR Diff 引擎 | 异步生成、流式传输 | 大仓库首次加载慢、缓存策略复杂 |

### 适用场景

- **权限扩展**：需要自定义权限规则（如"只能在工作时间 push"）
- **CI 定制**：理解 Pipeline 状态流转后添加自定义回调
- **MR 优化**：定位 diff 加载慢的瓶颈，针对性优化

### 注意事项

- **DeclarativePolicy 的 prevent 优先级高于 enable**：配置权限时注意辨别
- **Pipeline 状态机中的 after_transition 回调是同步执行的**：不要在里面放耗时操作
- **MR Diff 的 refresh_service 在每次 push 到 source branch 时触发**：频繁 push 会产生大量的 diff 计算

### 常见踩坑经验

1. **权限配置不生效**：修改了 Policy 文件但重启后依然不生效。根因：DeclarativePolicy 有缓存机制，修改代码后需要重启 Puma + 清除 Rails cache。解决：`sudo gitlab-ctl restart puma && sudo gitlab-rails runner "Rails.cache.clear"`。
2. **Pipeline 状态从 success 自动变成 failed**：因为在 after_transition 回调中抛出了异常。根因：`notify_success!` 方法中的 Webhook 调用超时导致回调失败。解决：Webhook 调用应该异步（用 Sidekiq Worker），不要放在状态机的同步回调中。
3. **MR Diff 显示空白**：数据库中 MergeRequestDiff 的 state 一直是 `unstarted`。根因：`NewMergeRequestDiffGenerationWorker` 没有成功入队（Sidekiq 挂了）。解决：检查 Sidekiq 积压，手动重试 diff 生成。

### 思考题

1. GitLab 的权限系统在评估 `can?(user, :read_project, project)` 时，如果 user 既是 project 的 Developer 又是 parent group 的 Guest，最终权限会被如何计算？DeclarativePolicy 是如何处理这种"角色叠加"的？
2. Pipeline 状态机中 `event :cancel` 定义了 `transition [:created, :pending, :running] => :canceled`。但为什么 `:success` 和 `:failed` 不能被 cancel？如果你需要在 Pipeline 成功后'撤销'——回滚已部署的代码——应该用什么机制而不是 cancel 状态机？

> 答案见附录 D。

### 推广计划提示

- **开发**：本章三个模块是 GitLab 源码中"最值得读懂"的部分——覆盖了安全、流程、性能三个核心维度
- **架构师**：DeclarativePolicy 的设计模式（条件+规则分离）可以借鉴到其他系统的权限设计
- **运维**：理解 Pipeline 状态机后，排查 CI 异常时能更快定位是哪个状态转换出了问题
