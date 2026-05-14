# 第37章：自定义 GitLab 扩展开发实战

## 1. 项目背景

> **业务场景**：一家金融科技公司有特殊的合规需求——每个 MR 除常规审批外，还需要"合规部门"审批（当修改涉及支付/风控模块时）。GitLab 内置的 Code Owner 功能可以实现按文件路径指定审批人，但不够用——合规审批的触发规则很复杂：要根据文件路径、修改类型（新增/修改/刪除）、项目风险等级三个维度组合判断。

团队决定自建扩展，但面临一系列问题：从哪里入手？怎么注册自定义的 MR 审批规则？怎么确保 GitLab 版本升级后扩展不冲突？测试怎么搞？他们不想维护一个 GitLab fork——那意味着每次升级都要手动 merge 冲突，维护成本极高。

**痛点放大**：GitLab 虽然功能丰富，但总有 5% 的场景需要定制。选择正确的扩展方式非常重要——方式不对，轻则维护困难，重则升级时系统崩溃。本章从轻到重介绍四种扩展层次，帮助你根据需求的复杂度和维护成本做出最优选择。

## 2. 项目设计——剧本式交锋对话

**场景**：技术选型会议，架构师小李在白板上画出四种扩展方式的对比图。

---

**小胖**："我们的合规审批需求——是不是应该直接在 GitLab 源码里改？修改 MR 创建流程，加上合规检查逻辑。"

**大师**："千万别直接改 GitLab 源码——那是'最重'的扩展方式。你改一次容易，但 GitLab 每个月发一个版本，每次升级你都要重新 merge 你的改动。几十次 merge 下来，你的代码和上游代码的冲突会越来越多，最终变成技术债务。GitLab 有更轻量的扩展方式。"

**小胖**："那什么方式最轻？"

**大师**："最轻的是 Level 1——CI/CD 集成。你在 `.gitlab-ci.yml` 中写一个 job，在 MR 创建时自动跑，检查修改的文件是否涉及敏感模块。如果涉及，CI 失败，MR 不能合并。这是零代码侵入、零升级风险的方式。但局限是——它只能在 CI 中做检查，不能修改 GitLab 自身的审批流程。"

**小白**："那如果 CI 集成不够用呢？比如我们想自动添加审批人，而不仅仅是检查。"

**大师**："那就上 Level 2——API 编程。写一个 Python/Go 脚本，通过 Webhook 监听 MR 创建事件，自动调用 GitLab API 添加审批人。这也是零代码侵入的方式——脚本是独立的服务，不嵌入 GitLab。但局限是——你需要维护一个独立服务，有网络延迟。"

**小白**："Level 3 的 Sidekiq Worker 呢？前面第35章讲过的。"

**大师**："Level 3 是直接给 GitLab 写 Sidekiq Worker——它的权限更大、性能更好（进程内执行，无网络延迟）、可以调用所有 GitLab 内部 API。但代价是——你需要维护一个 GitLab 代码的补丁（patch）。升级时虽然不像 fork 那样痛苦，但你还是要确认你的 Worker 兼容新版本。"

**小胖**："Level 4 Rails Engine 是不是最强大的？"

**大师**："对，但也是维护成本最高的——你需要维护一个 Ruby Gem，挂载在 GitLab 的 Rails Engine 上。你可以添加自定义页面、自定义数据库表、甚至覆盖 GitLab 的现有行为。但升级时任何 GitLab 内部 API 的变化都可能导致你的扩展崩溃。技术映射——这四个 Level 就像改装车：L1 是贴纸（CI），L2 是加装行车记录仪（API），L3 是刷 ECU（Worker），L4 是换发动机（Engine）。"

---

## 3. 项目实战

### 环境准备

| 工具 | 用途 |
|------|------|
| GitLab 17.x | 扩展的目标平台 |
| Python 3.10+ / pip install python-gitlab | L2 API 编程 |
| Ruby 3.0+ / GitLab 源码 | L3/L4 扩展开发 |
| Webhook 接收器（Flask/Express） | L1/L2 集成测试 |

### 分步实现

#### 步骤1：L1——CI/CD 集成（零代码侵入）

**目标**：在 MR 创建时通过 CI job 检查文件变更，涉及敏感模块则阻断合并。

```yaml
# .gitlab-ci.yml —— 自定义合规检查
stages:
  - compliance

# ⭐ 合规审批 job
compliance-check:
  stage: compliance
  image: alpine:latest
  before_script:
    - apk add --no-cache git
  script:
    - |
      echo "🔍 检查 MR 变更的合规性..."

      # 获取 MR 中变更的文件列表
      git fetch origin $CI_MERGE_REQUEST_TARGET_BRANCH_NAME
      FILES=$(git diff --name-only origin/$CI_MERGE_REQUEST_TARGET_BRANCH_NAME...$CI_COMMIT_SHA)

      # 定义敏感模块（按文件路径匹配）
      SENSITIVE_MODULES=("payment/" "wallet/" "kyc/" "finance/accounting/")

      # 检查是否有文件涉及敏感模块
      VIOLATIONS=""
      for file in $FILES; do
        for module in "${SENSITIVE_MODULES[@]}"; do
          if [[ "$file" == $module* ]]; then
            VIOLATIONS="$VIOLATIONS\n  ⚠️ $file (模块: $module)"
          fi
        done
      done

      if [ -n "$VIOLATIONS" ]; then
        echo "❌ 合规检查失败！以下文件涉及敏感模块，需要额外审批："
        echo -e "$VIOLATIONS"
        echo ""
        echo "请联系 @compliance-team 进行审批。"
        exit 1   # CI 失败 → MR 无法合并
      else
        echo "✅ 合规检查通过——MR 不涉及敏感模块"
      fi
  rules:
    - if: '$CI_PIPELINE_SOURCE == "merge_request_event"'
  # 这个 job 只检查，不部署
  needs: []
```

#### 步骤2：L2——API 编程 + Webhook（独立服务）

**目标**：创建一个 Webhook 接收器，在 MR 创建时自动添加合规审批人。

```python
#!/usr/bin/env python3
"""
compliance_webhook.py —— GitLab Webhook 接收器
监听 MR 创建事件 → 涉及敏感文件时自动添加合规审批人
"""

from flask import Flask, request, jsonify
import gitlab
import hmac
import os

app = Flask(__name__)

# 配置
GITLAB_URL = "http://gitlab.local:8929"
GITLAB_TOKEN = os.getenv("GITLAB_TOKEN", "glpat-xxxx")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "your-secret")

# 初始化 GitLab 客户端
gl = gitlab.Gitlab(GITLAB_URL, private_token=GITLAB_TOKEN)

# 敏感模块定义
SENSITIVE_PATTERNS = ["payment/", "wallet/", "kyc/", "finance/accounting/"]
COMPLIANCE_TEAM_ID = 42  # 合规团队的 GitLab Group ID


def verify_webhook_token():
    """验证 GitLab Webhook 的 Secret Token"""
    token = request.headers.get("X-Gitlab-Token", "")
    return hmac.compare_digest(token, WEBHOOK_SECRET)


def get_compliance_approvers():
    """获取合规团队的所有成员"""
    group = gl.groups.get(COMPLIANCE_TEAM_ID)
    return [m.id for m in group.members.list() if m.access_level >= 30]


def handle_merge_request_event(data):
    """处理 MR 创建事件"""
    project_id = data["project"]["id"]
    mr_iid = data["object_attributes"]["iid"]

    project = gl.projects.get(project_id)
    mr = project.mergerequests.get(mr_iid)

    # ⭐ 获取 MR 的文件变更列表
    try:
        changes = mr.changes()
    except gitlab.exceptions.GitlabError:
        # MR 还在生成 diff，暂不处理
        return

    # 检查是否涉及敏感文件
    changed_files = [c.get("new_path", "") for c in changes.get("changes", [])]
    needs_compliance = any(
        any(f.startswith(pattern) for pattern in SENSITIVE_PATTERNS)
        for f in changed_files
    )

    if not needs_compliance:
        print(f"MR !{mr_iid}: 不涉及敏感模块，跳过")
        return

    # ⭐ 添加合规审批人
    approvers = get_compliance_approvers()
    if approvers:
        mr.approvals.set_approvers(approver_ids=approvers)
        mr.notes.create({
            "body": (
                "🔒 **合规审批已自动添加**\n\n"
                "此 MR 修改了以下敏感模块的文件：\n"
                + "".join(f"- `{f}`\n" for f in changed_files if any(f.startswith(p) for p in SENSITIVE_PATTERNS))
                + f"\n已添加 @compliance-team 成员为审批人，请等待合规审批。"
            )
        })
        print(f"✅ MR !{mr_iid}: 已添加 {len(approvers)} 位合规审批人")


@app.route("/gitlab-webhook", methods=["POST"])
def webhook():
    """GitLab Webhook 入口"""
    if not verify_webhook_token():
        return jsonify({"error": "Invalid token"}), 403

    event_type = request.headers.get("X-Gitlab-Event", "")
    data = request.json

    if event_type == "Merge Request Hook":
        action = data["object_attributes"]["action"]
        if action == "open":
            handle_merge_request_event(data)

    return jsonify({"status": "ok"}), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
```

**配置 GitLab Webhook**：

```bash
# Project → Settings → Webhooks
# URL: https://webhook.internal.com:8080/gitlab-webhook
# Secret Token: your-secret
# ✅ Merge request events
# ✅ Enable SSL verification
```

#### 步骤3：L3——Sidekiq Worker（嵌入 GitLab 进程）

**目标**：编写一个 Sidekiq Worker，在 MR 创建时异步执行合规检查。

```ruby
# ====== 文件：app/workers/compliance/approval_worker.rb ======
# 部署方式：将此文件放入 /opt/gitlab/embedded/service/gitlab-rails/app/workers/compliance/
# 注意：GitLab 升级后需要重新部署此文件

module Compliance
  class ApprovalWorker
    include ApplicationWorker

    sidekiq_options retry: 2, queue: :default

    feature_category :compliance
    urgency :high
    idempotent!

    SENSITIVE_PATTERNS = %w[payment/ wallet/ kyc/ finance/accounting/].freeze

    def perform(merge_request_id)
      merge_request = MergeRequest.find_by_id(merge_request_id)
      return unless merge_request

      # 检查敏感文件
      return unless requires_compliance_approval?(merge_request)

      # 添加合规审批人
      add_compliance_approvers(merge_request)
    end

    private

    def requires_compliance_approval?(mr)
      # 获取 MR 修改的文件路径
      modified_paths = mr.modified_paths
      modified_paths.any? do |path|
        SENSITIVE_PATTERNS.any? { |pattern| path.start_with?(pattern) }
      end
    end

    def add_compliance_approvers(mr)
      # 查找合规团队成员
      compliance_group = Group.find_by_path("compliance-team")
      return unless compliance_group

      compliance_users = compliance_group.members
        .where("access_level >= ?", Gitlab::Access::DEVELOPER)
        .map(&:user)

      compliance_users.each do |user|
        mr.approvals.create!(user: user)
      end

      # 添加系统评论
      SystemNoteService.add_compliance_approval(mr, mr.author, compliance_users)
    end
  end
end

# ====== 在 MR 创建后触发 Worker ======
# 修改：app/services/merge_requests/create_service.rb
# 在 execute 方法的适当位置添加：
# Compliance::ApprovalWorker.perform_async(merge_request.id)

# ====== 部署检查清单 ======
# 1. 复制 Worker 文件到正确路径
# 2. 确认文件权限（git:git）
# 3. sudo gitlab-ctl restart sidekiq
# 4. 创建一个测试 MR 验证功能
# 5. GitLab 版本升级后重新部署
```

#### 步骤4：扩展方式决策树

```
需要扩展 GitLab 功能？
│
├─ 只是自动化操作？
│   → L2 API 编程（python-gitlab / glab CLI）
│   ✅ 零侵入、零升级风险
│   ❌ 独立服务需要维护
│
├─ 需要自定义 MR 检查/阻断？
│   → L1 CI 集成（.gitlab-ci.yml）
│   ✅ 零侵入、零升级风险
│   ❌ 只能阻断 CI，不能修改 GitLab 行为
│
├─ 需要修改 GitLab 内部行为（如自动添加审批人）？
│   ├─ 能用 API 实现？
│   │   → L2 API + Webhook
│   │   ✅ 零侵入
│   │   ❌ 有网络延迟
│   │
│   └─ 需要进程内执行？
│       → L3 Sidekiq Worker
│       ✅ 性能好、功能全
│       ❌ 升级需要重新部署文件
│
├─ 需要自定义 UI 页面或数据库表？
│   → L4 Rails Engine (Ruby Gem)
│   ⚠️ 最强大、维护成本最高
│   ⚠️ 不建议团队规模 < 50 人
│
└─ GitLab EE/Ultimate 已有此功能？
    → 升级许可证！
    ✅ 零开发、官方支持
    ✅ 比自己维护扩展便宜得多
```

### 完整代码清单

- L1：`.gitlab-ci.yml` 合规检查 job（步骤1）
- L2：`compliance_webhook.py` Webhook 接收器（步骤2）
- L3：`app/workers/compliance/approval_worker.rb` Sidekiq Worker（步骤3）
- 扩展方式决策树（步骤4）

### 测试验证

```bash
# L1 验证：CI 集成
git checkout -b test/compliance-check && echo "// test" >> payment/process.js
git add . && git commit -m "test: trigger compliance check" && git push
# 在 GitLab 创建 MR
# → Pipeline 应显示 compliance-check job 失败（提示需要合规审批）

# L2 验证：Webhook 接收器
# 本地启动接收器
WEBHOOK_SECRET=test GITLAB_TOKEN=glpat-xxx python3 compliance_webhook.py
# 在 GitLab Webhook 页面点击 "Test" → Push events
# 接收器应输出 200 OK

# L3 验证：Sidekiq Worker
sudo cp compliance/approval_worker.rb /opt/gitlab/embedded/service/gitlab-rails/app/workers/compliance/
sudo gitlab-ctl restart sidekiq
# 创建一个涉及 payment/ 目录的 MR
# 等 10 秒 → 检查 MR 的审批人列表是否自动添加了合规团队成员
# MR comment 区域是否有系统评论
```

## 4. 项目总结

### 四种扩展方式对比

| 方式 | 复杂度 | 升级风险 | 开发时间 | 功能边界 | 推荐场景 |
|------|--------|---------|---------|---------|---------|
| L1 CI 集成 | ⭐ | 无 | 1-2h | CI 检查/阻断 | 自定义 MR 检查 |
| L2 API + Webhook | ⭐⭐ | 无 | 1-2d | GitLab API 范围 | 自动化审批、通知 |
| L3 Sidekiq Worker | ⭐⭐⭐ | 低（需重新部署） | 3-5d | GitLab 内部 API | 高性能异步处理 |
| L4 Rails Engine | ⭐⭐⭐⭐ | 高 | 1-2w | 任意功能 | 自定义页面/DB/流程 |

### 适用场景

- **L1**：需要自定义 MR 质量检查、合规验证
- **L2**：批量管理、自动化审批、外部系统集成
- **L3**：需要高性能、进程内执行的异步任务
- **L4**：需要自定义 UI 页面、自定义数据库表、覆盖 GitLab 核心行为

### 注意事项

- **L3/L4 扩展在 GitLab 升级后需要验证兼容性**：每次升级后创建测试 MR 验证功能正常
- **永远不要修改 CE 源码**：用 prepend（EE 叠加模式）或独立的 Worker/Gem
- **能用 API 解决的问题不用 Worker**：维护成本最低
- **GitLab EE 已有的功能不自己开发**：许可证费可能比开发维护成本低

### 常见踩坑经验

1. **L2 API 调用超时**：Webhook 接收器处理时间超过 GitLab 的 Webhook 超时（默认 10 秒）。根因：在接收器中间步执行耗时操作。解决：接收器只负责"接收+验证+入队"，实际处理用独立的消息队列。
2. **L3 Worker 在升级后消失**：升级 GitLab 后之前放入的 Worker 文件被覆盖。根因：Omnibus 的 `reconfigure` 会重新生成 `/opt/gitlab/embedded/service/gitlab-rails/`。解决：将自定义 Worker 放在 `/var/opt/gitlab/gitlab-rails/etc/` 并配置自动加载路径。
3. **L1 CI 检查误报太多**：正则匹配太宽导致正常文件被拦截。根因：`SENSITIVE_MODULES` 匹配规则没有 scope。解决：使用更精确的路径前缀，或引入 "allowed" 白名单机制。

### 思考题

1. L2 Webhook 接收器如果挂了，GitLab 会丢失事件吗？GitLab Webhook 的重试机制是什么？如何在接收器端实现"至少一次"的处理保证？
2. 如果公司有 50 个 Ruby 项目需要同一个 L3 Worker 扩展，每个项目都复制一份 Worker 文件显然不现实。如何设计一个"可复用的 Worker Gem"并通过 GitLab Omnibus 的 `gitlab_rails['custom_hooks_dir']` 或类似机制统一部署？

> 答案见附录 D。

### 推广计划提示

- **开发**：扩展方式选择最重要的原则是——能用轻的不用重的，能用 API 的不改源码
- **运维**：L3 扩展需要纳入升级检查清单——每次升级后验证自定义 Worker 是否仍然正常
- **管理**：如果 GitLab EE 已有你需要的功能，买许可证比自研扩展便宜
