# 第26章：Webhooks 与外部系统集成

## 1. 项目背景

> **业务场景**：一家公司使用 GitLab 管理代码和 CI/CD，用 Jira 管理项目，用飞书做日常沟通，用 PagerDuty 做告警。但这些系统之间完全孤立——开发在 GitLab 上合并了 MR，Jira 上的工单还停留在"In Progress"；CI 失败了没人知道，直到用户投诉才发现线上挂了 2 小时。

产品经理抱怨："每次 MR 合并后，我都要手动去 Jira 更新状态，有时候忘了就导致周报数据不准。"运维抱怨："生产环境部署失败了没人知道，因为告警全靠人工看 Pipeline。"

CTO 拍板："GitLab 必须和现有的外部系统打通——MR 合并自动更新 Jira 工单，部署失败自动发 PagerDuty 告警，新 Issue 自动通知到对应飞书群。"

**痛点放大**：GitLab 的 Webhooks 系统是它作为 DevOps 平台与外部世界沟通的"神经系统"。通过 Webhooks，GitLab 可以在代码推送、MR 创建、Pipeline 完成、Issue 变更等事件发生时，自动向外部系统发送通知。结合集成面板（Integrations），你不需要写代码就能完成大部分常见集成；结合自定义 Webhook 接收器，你可以实现任意复杂的自动化流程。

## 2. 项目设计——剧本式交锋对话

**场景**：自动化讨论会，各个团队的代表都在为系统之间的"信息孤岛"诉苦。

---

**产品经理**："我真的不想再手动同步 Jira 状态了——每天至少要花 15 分钟。能不能让 GitLab MR 合并后自动更新 Jira？"

**大师**："GitLab 有内置的 Jira Integration。你在项目中配置好 Jira 的连接信息后，只需要在 MR 描述或 commit message 中写 `Closes PROJ-123`，合并后 GitLab 会自动把 Jira 的 PROJ-123 工单标记为已完成。"

**小胖**："那飞书通知呢？我们想在 MR 创建时自动给评审人发飞书消息。"

**大师**："飞书没有内置集成，但可以用自定义 Webhook。你创建一个 Webhook 监听 Merge Request Events，指向一个你自己写的接收器服务。接收器收到 GitLab 的 POST 请求后，解析 JSON 数据，然后调用飞书的机器人 Webhook API 发消息。技术映射——GitLab Webhook 就像门铃，有人按了（MR 创建），门铃响了（POST 请求），你家里的人（接收器服务）去处理。"

**小白**："Webhook 的安全性怎么保证？任何人都能伪造请求吗？"

**大师**："GitLab Webhook 支持 Secret Token 验证。你在创建 Webhook 时设置一个 secret token，GitLab 会在每次请求的 HTTP Header `X-Gitlab-Token` 中带上这个 token。你的接收器收到请求后先验证 token——不匹配的直接拒绝。另外还可以配置 SSL 证书验证和 IP 白名单。"

**运维**："PagerDuty 集成呢？部署失败要立即告警。"

**大师**："GitLab 有内置的 PagerDuty Integration。配置好后，Pipeline 失败时会自动创建 PagerDuty incident。你可以在 `.gitlab-ci.yml` 中精确控制什么样的失败才触发告警。"

---

## 3. 项目实战

### 环境准备

> **目标**：配置 Jira 集成、飞书通知 Webhook、PagerDuty 告警，并编写一个自定义 Webhook 接收器。

**前置条件**：GitLab CE 17.x，Jira/飞书/PagerDuty 账号。

### 分步实现

#### 步骤1：配置内置集成——Jira

**目标**：配置 GitLab-Jira 集成，实现 MR 与 Jira 工单自动关联。

**通过 GitLab UI 配置**：

```
Project → Settings → Integrations → Jira
→ ✅ Active
→ Web URL: https://your-company.atlassian.net
→ Username/Email: jira-bot@company.com
→ Password/API Token: <Jira API Token>
→ Transition ID: 31 (对应 "Done" 状态)
→ ✅ Enable comments
→ ✅ Enable Jira issue closing
```

**使用方式**：

```bash
# 在 commit message 中引用 Jira 工单
git commit -m "fix: resolve payment rounding bug

This fixes the issue reported in QA.
Ref PROJ-123"

# 在 MR 描述中关闭工单
# MR description:
# Closes PROJ-123
# 合并后 Jira PROJ-123 自动转为 Done 状态

# 在分支名中引用工单
git checkout -b feature/PROJ-123-fix-payment
```

**通过 API 配置 Jira 集成**：

```bash
curl --request PUT \
  --header "PRIVATE-TOKEN: $GITLAB_TOKEN" \
  --header "Content-Type: application/json" \
  --data '{
    "url": "https://your-company.atlassian.net",
    "username": "jira-bot@company.com",
    "password": "<jira-api-token>",
    "active": true,
    "jira_issue_transition_id": 31,
    "commit_events": true,
    "merge_requests_events": true,
    "comment_on_event_enabled": true
  }' \
  "$GITLAB_URL/api/v4/projects/$PROJECT_ID/services/jira"
```

#### 步骤2：配置自定义 Webhook——飞书通知

**目标**：创建 GitLab Webhook → 自定义接收器 → 飞书机器人发消息。

**步骤 A：创建飞书机器人并获取 Webhook URL**：

```
飞书 → 群聊 → 设置 → 群机器人 → 添加自定义机器人
→ 复制 Webhook URL: https://open.feishu.cn/open-apis/bot/v2/hook/xxx
```

**步骤 B：在 GitLab 中配置 Webhook**：

```
Project → Settings → Webhooks
→ URL: https://your-receiver.com/gitlab-webhook
→ Secret Token: <random-string>
→ 触发事件:
  ✅ Push events
  ✅ Merge request events
  ✅ Pipeline events
→ ✅ Enable SSL verification
→ Add webhook
```

**步骤 C：编写 Webhook 接收器服务**：

```python
#!/usr/bin/env python3
"""webhook_receiver.py - GitLab Webhook 接收器，转发到飞书"""
from flask import Flask, request, jsonify
import hmac, hashlib, json, requests

app = Flask(__name__)

# 配置
GITLAB_SECRET = "your-random-secret-token"
FEISHU_WEBHOOK = "https://open.feishu.cn/open-apis/bot/v2/hook/xxx"

def verify_gitlab_token():
    """验证 GitLab Webhook Secret Token"""
    token = request.headers.get('X-Gitlab-Token', '')
    return hmac.compare_digest(token, GITLAB_SECRET)

def send_feishu(title, content):
    """发送飞书消息"""
    data = {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {"tag": "plain_text", "content": title},
                "template": "blue"
            },
            "elements": [
                {"tag": "markdown", "content": content}
            ]
        }
    }
    requests.post(FEISHU_WEBHOOK, json=data)

@app.route('/gitlab-webhook', methods=['POST'])
def handle_webhook():
    if not verify_gitlab_token():
        return jsonify({'error': 'Invalid token'}), 403

    event_type = request.headers.get('X-Gitlab-Event', '')
    data = request.json

    if event_type == 'Merge Request Hook':
        mr = data['object_attributes']
        action = mr['action']
        title = mr['title']
        author = data['user']['name']
        url = mr['url']

        if action == 'open':
            send_feishu(
                f"📝 新 MR: {title}",
                f"**作者**: {author}\n**分支**: {mr['source_branch']} → {mr['target_branch']}\n[查看 MR]({url})"
            )
        elif action == 'merge':
            send_feishu(
                f"✅ MR 已合并: {title}",
                f"**作者**: {author}\n**合并者**: {data['user']['name']}\n[查看 MR]({url})"
            )

    elif event_type == 'Pipeline Hook':
        pipeline = data['object_attributes']
        if pipeline['status'] == 'failed':
            send_feishu(
                f"❌ Pipeline 失败: {data['project']['name']}",
                f"**分支**: {pipeline['ref']}\n**Commit**: {pipeline['sha'][:8]}\n[查看 Pipeline]({pipeline['url']})"
            )

    elif event_type == 'Push Hook':
        pusher = data['user_name']
        branch = data['ref'].replace('refs/heads/', '')
        commits = len(data['commits'])
        send_feishu(
            f"🚀 Push: {data['project']['name']}/{branch}",
            f"**推送者**: {pusher}\n**Commit数**: {commits}"
        )

    return jsonify({'status': 'ok'}), 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080)
```

#### 步骤3：PagerDuty 告警集成

**目标**：Pipeline 失败时自动创建 PagerDuty 告警。

```yaml
# .gitlab-ci.yml
# 方式 A：GitLab 内置 PagerDuty Integration
# Project → Settings → Integrations → PagerDuty
# → ✅ Active
# → Service Key: <PagerDuty Integration Key>

# 方式 B：在 CI 中直接调用 PagerDuty API
alert-on-failure:
  stage: notify
  image: alpine:latest
  script:
    - |
      curl -X POST "https://events.pagerduty.com/v2/enqueue" \
        -H "Content-Type: application/json" \
        -d "{
          \"routing_key\": \"$PAGERDUTY_ROUTING_KEY\",
          \"event_action\": \"trigger\",
          \"payload\": {
            \"summary\": \"Pipeline failed: $CI_PROJECT_NAME ($CI_COMMIT_BRANCH)\",
            \"source\": \"gitlab-ci\",
            \"severity\": \"critical\",
            \"custom_details\": {
              \"project\": \"$CI_PROJECT_NAME\",
              \"branch\": \"$CI_COMMIT_BRANCH\",
              \"commit\": \"$CI_COMMIT_SHORT_SHA\",
              \"pipeline_url\": \"$CI_PIPELINE_URL\"
            }
          }
        }"
  when: on_failure
  rules:
    - if: '$CI_COMMIT_BRANCH == "main"'
```

#### 步骤4：Webhook 调试与监控

**目标**：学会测试和调试 Webhook 配置。

```bash
# 1. GitLab Webhook 测试
# Project → Settings → Webhooks → Test → 选择事件类型
# 查看 "Recent events" 区域，确认 HTTP 状态码和响应内容

# 2. 用 RequestBin 调试（临时接收 Webhook）
# https://requestbin.com 创建测试端点
# 将 Webhook URL 指向 RequestBin
# 触发事件 → 在 RequestBin 查看收到的原始 JSON

# 3. 用 ngrok 暴露本地接收器
ngrok http 8080
# 将 ngrok 提供的公网 URL 填入 Webhook URL
# 在本地 IDE 中打断点调试接收器

# 4. 查看 Webhook 发送记录
curl --header "PRIVATE-TOKEN: $GITLAB_TOKEN" \
  "$GITLAB_URL/api/v4/projects/$PROJECT_ID/hooks/1"
# 查看 hook 的 recent_deliveries 和响应
```

### 完整代码清单

- `webhook_receiver.py`：飞书通知接收器
- `.gitlab-ci.yml`：PagerDuty 告警集成
- Jira Integration API 配置

### 测试验证

```bash
# 验证1：Jira 集成
# 创建 MR，描述中写 "Closes PROJ-123"
# 合并后检查 Jira → PROJ-123 是否转为 Done

# 验证2：飞书通知
# 创建 MR → 检查飞书群是否收到消息
# 合并 MR → 检查飞书群是否收到合并通知

# 验证3：Pipeline 失败告警
# 故意让 Pipeline 失败 → 检查是否收到 PagerDuty 告警
# 或检查飞书群是否收到失败通知

# 验证4：Webhook 安全
# 构造不带 Secret Token 的请求 → 应返回 403
curl -X POST http://receiver/gitlab-webhook \
  -H "Content-Type: application/json" \
  -d '{"test": true}'
# HTTP 403
```

## 4. 项目总结

### 集成方式对比

| 集成方式 | 复杂度 | 优点 | 缺点 |
|---------|--------|------|------|
| 内置 Integration | 低 | 零代码，一键配置 | 仅限 GitLab 支持的平台 |
| 自定义 Webhook | 中 | 任意平台，灵活 | 需要维护接收器服务 |
| CI Job 直接调用 | 中 | 不依赖外部接收器 | 只在 Pipeline 中可用 |

### 适用场景

- **Jira 集成**：开发团队用 Jira 管理项目，用 GitLab 管理代码
- **飞书/钉钉通知**：实时了解 MR 和 Pipeline 状态
- **PagerDuty 告警**：生产环境 Pipeline 失败需要 on-call 响应的场景
- **自定义审批流**：GitLab MR 事件 → Webhook → 企业内部审批系统

**不适用场景**：
- 不需要外部系统集成的独立 GitLab 使用
- 团队规模太小（5 人以下），口头沟通比自动化更快

### 注意事项

- **Secret Token 必须验证**：否则任何人都可以伪造 GitLab Webhook 请求
- **Webhook 超时**：GitLab 期望接收器在 10 秒内返回响应，长时间处理应用异步方式
- **重试机制**：GitLab 会重试失败的 Webhook（最多 5 次），确保接收器幂等
- **内置 Integration 和 Webhook 的区别**：Integration 是 GitLab 主动调用外部 API，Webhook 是 GitLab 发送事件给接收器

### 常见踩坑经验

1. **Webhook 触发但接收器没收到**：GitLab 的 Recent events 显示 HTTPS 证书错误。根因：自签名证书不被 GitLab 信任。解决：使用 CA 签发的证书，或在 Webhook 设置中关闭 SSL 验证。
2. **飞书消息格式不对**：接收器返回 200 但飞书群没收到消息。根因：飞书 Webhook 要求特定的 JSON 格式。解决：参考飞书机器人文档中的消息格式示例。
3. **Webhook 重复触发**：同一个 MR 事件触发了两次。根因：MR 的 `action` 可以为 `open` 和 `update`——每次 push 更新 MR 都会触发 `update`。解决：在接收器中根据 `action` 字段过滤。

### 思考题

1. 如果你的 Webhook 接收器需要处理高并发（数百个项目同时触发事件），如何设计架构来保证接收的可靠性和水平扩展能力？
2. GitLab 内置了 30+ 种 Integration（Jira、Slack、PagerDuty、Teams 等）。当你需要的两个平台都有内置 Integration 但它们的功能有重叠（如既想 Slacker 通知又想飞书通知），应该如何协调？

> 答案见附录 D。

### 推广计划提示

- **运维**：Webhook 是 GitLab 事件总线的核心，掌握它可以打通所有外部系统
- **开发**：内置 Integration 可以节省大量手动同步工作，优先使用而非自己写 Webhook
- **安全**：Webhook Secret Token 应该定期轮换，特别是有人离职后
