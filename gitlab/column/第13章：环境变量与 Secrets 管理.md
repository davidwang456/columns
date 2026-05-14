# 第13章：环境变量与 Secrets 管理

## 1. 项目背景

> **业务场景**：一家金融科技公司的安全审计中发现，CI/CD 流水线中多处硬编码了生产环境密钥——数据库密码写在 `.gitlab-ci.yml` 里，第三方支付 API Key 直接暴露在 job 日志中，甚至某位开发者把自己的 Personal Access Token 写进了代码仓库（虽然有 `.gitignore` 但已经 push 了历史 commit）。

最严重的一次事故：一名外包开发在 CI 脚本里写了 `echo "Database password: $DB_PASSWORD"`（为了调试），结果这个 job 日志在 Runner 上缓存了 90 天，任何人都能通过 GitLab UI 查看。安全团队发现时，这个日志已经被查看了 50+ 次——但无法追溯是谁看的。

安全总监下令："所有敏感信息必须使用 GitLab CI Variables 的 Masked 模式管理，任何硬编码密钥的 commit 必须用 BFG Repo-Cleaner 清理历史。生产密钥只能用 Protected 变量，仅限 main 分支访问。"

**痛点放大**：CI/CD 中的 Secret 管理远不止"不要硬编码密码"。你需要理解 GitLab 变量的 5 层继承体系（Instance → Group → Subgroup → Project → Job）、变量类型（Variable/File/Masked/Protected）、优先级规则、以及如何保护变量不被日志输出。太多团队在配置好 CI 后，却因密钥泄露而功亏一篑。

## 2. 项目设计——剧本式交锋对话

**场景**：安全审计会议，白板上列出 CI 变量管理中发现的 12 个安全风险。

---

**安全工程师**："这次审计发现的最大问题是——几乎所有敏感变量都用了 `Variable` 类型，在 job 日志里是纯文本显示。有 3 个变量的值直接出现在了 job 日志中。"

**小胖**："那我也不是故意的啊……调试的时候用 echo 打印了一下变量，忘记删了。"

**大师**："这就是 Masked variables 存在的意义。当你把一个变量设为 Masked 时，GitLab 会自动检测 job 日志中的变量值，并替换为 `[MASKED]`。即使你在 script 里写了 `echo $DB_PASSWORD`，日志里也只会显示 `echo [MASKED]`。"

**小胖**："那 Protected 变量呢？跟 Masked 有什么不同？"

**大师**："Protected 是控制'谁能用这个变量'，Masked 是控制'变量值被看到了怎么办'。Protected 变量只在保护分支（如 main）和标签上可用——普通 feature 分支的 CI 无法读取这些变量。这意味着即使有人偷到了 CI 脚本的执行权限，在 feature 分支上也拿不到生产密钥。技术映射：Protected 像是'只有 VIP 包间才能点这道菜'，Masked 像是'就算你看到了小票，价格也被涂黑了'。"

**小白**："My File 类型的变量呢？我看文档里提到可以在 CI 中注入文件。"

**大师**："File 类型的变量特别适合注入证书、密钥文件。比如你有一个 `GCP_SERVICE_ACCOUNT_KEY`，如果作为普通 Variable，它的值是一个超长的 JSON 字符串，在环境变量中各种转义问题。但如果作为 File 类型，GitLab 会把它写入一个临时文件，然后你可以用 `cat $GCP_SERVICE_ACCOUNT_KEY` 或者直接作为 `--key-file` 参数传递。"

**安全工程师**："还有问题——变量优先级。我发现一个 Project 级别的变量 DB_PASSWORD 的值被 Group 级别的同名变量覆盖了，而开发根本没意识到。"

**大师**："变量优先级规则是理解 CI 变量体系的关键。从低到高：默认预定义变量 → .gitlab-ci.yml 全局 variables → job 级别 variables → Pipeline 触发变量 → Project/Group CI Variables。同级时后定义的覆盖先定义。技术映射——就像法律体系：宪法（Instance）> 法律（Group）> 地方法规（Project）> 临时规定（Job）。"

**小胖**："那我要是既想要 Project 级别方便管理，又想要 Job 级别灵活覆盖，怎么设计？"

**大师**："好的策略是：基础配置放 Group 级别（如公司统一的 Registry 地址）、环境差异放 Project 级别（如 staging/production 的域名）、job 特殊需要放 job 级别（如某个测试需要不同的数据库）。变最敏感的部分（生产密码）用 Protected + Masked，确保分支隔离。"

---

## 3. 项目实战

### 环境准备

> **目标**：为一个微服务项目搭建安全的分层 CI 变量体系，覆盖 Group/Project/Job 三级。

**前置条件**：GitLab Group 和 Project（参考第4章），有 Maintainer 以上权限。

### 分步实现

#### 步骤1：规划变量分层架构

**目标**：设计从 Instance 到 Job 的 5 级变量体系。

```
变量层级规划表:

Instance (管理员配置)
├── CI_REGISTRY: registry.example.com      # 公司统一 Docker Registry
├── SMTP_PASSWORD: [masked]                # 公司 SMTP
│
├─ Group: acme-corp (公司级别)
│  ├── SONAR_HOST: https://sonar.internal  # 代码扫描服务地址
│  ├── NPM_REGISTRY: https://npm.internal  # 私有 npm 源
│  │
│  ├─ Subgroup: ecommerce (业务线级别)
│  │  ├── K8S_API_URL: https://k8s.internal # K8s 集群地址
│  │  │
│  │  └─ Project: shop-api (项目级别)
│  │     ├── DB_HOST: staging.db.internal   # Staging 数据库
│  │     ├── DB_PASSWORD: [masked+protected] # Staging 密码
│  │     ├── PROD_DB_PASSWORD: [masked+protected] # 生产密码
│  │     └── AWS_ACCESS_KEY_ID: [masked+protected]
```

#### 步骤2：通过 API 批量创建和管理 CI 变量

**目标**：用 API 创建三层变量，验证 Masked/Protected 效果。

```bash
export GITLAB_URL="http://gitlab.local:8929"
export GITLAB_TOKEN="glpat-xxxx"
export PROJECT_ID="<project-id>"
export GROUP_ID="<group-id>"

# ===== Group 级别变量 =====
# 创建普通变量（所有项目继承）
curl --request POST \
  --header "PRIVATE-TOKEN: $GITLAB_TOKEN" \
  --header "Content-Type: application/json" \
  --data '{
    "key": "SONAR_HOST",
    "value": "https://sonarqube.internal.company.com",
    "variable_type": "env_var",
    "protected": false,
    "masked": false,
    "raw": true
  }' \
  "$GITLAB_URL/api/v4/groups/$GROUP_ID/variables"

# 创建 Masked 变量（日志中自动脱敏）
curl --request POST \
  --header "PRIVATE-TOKEN: $GITLAB_TOKEN" \
  --header "Content-Type: application/json" \
  --data '{
    "key": "NPM_TOKEN",
    "value": "npm_abc123XYZtoken456",
    "variable_type": "env_var",
    "protected": false,
    "masked": true,
    "raw": true
  }' \
  "$GITLAB_URL/api/v4/groups/$GROUP_ID/variables"

# ===== Project 级别变量 =====
# 创建 Staging 数据库密码（Masked，但非 Protected——feature 分支也可以访问）
curl --request POST \
  --header "PRIVATE-TOKEN: $GITLAB_TOKEN" \
  --header "Content-Type: application/json" \
  --data '{
    "key": "DB_PASSWORD",
    "value": "staging_secret_password_123",
    "variable_type": "env_var",
    "protected": false,
    "masked": true,
    "raw": true
  }' \
  "$GITLAB_URL/api/v4/projects/$PROJECT_ID/variables"

# 创建生产数据库密码（Masked + Protected——仅 main 分支可用）
curl --request POST \
  --header "PRIVATE-TOKEN: $GITLAB_TOKEN" \
  --header "Content-Type: application/json" \
  --data '{
    "key": "PROD_DB_PASSWORD",
    "value": "prod_super_secret_456",
    "variable_type": "env_var",
    "protected": true,
    "masked": true,
    "raw": true
  }' \
  "$GITLAB_URL/api/v4/projects/$PROJECT_ID/variables"

# ===== File 类型变量（注入 JSON 密钥文件）=====
curl --request POST \
  --header "PRIVATE-TOKEN: $GITLAB_TOKEN" \
  --header "Content-Type: application/json" \
  --data '{
    "key": "GCP_SA_KEY",
    "value": "{\"type\":\"service_account\",\"project_id\":\"acme-prod\",\"private_key_id\":\"abc123\"}",
    "variable_type": "file",
    "protected": true,
    "masked": true,
    "raw": true
  }' \
  "$GITLAB_URL/api/v4/projects/$PROJECT_ID/variables"

# 查看所有变量
curl --header "PRIVATE-TOKEN: $GITLAB_TOKEN" \
  "$GITLAB_URL/api/v4/projects/$PROJECT_ID/variables" | python3 -m json.tool
```

#### 步骤3：在 .gitlab-ci.yml 中使用变量

**目标**：展示如何使用不同层级的变量，以及如何安全地在 CI 中引用。

```yaml
# .gitlab-ci.yml
stages:
  - build
  - deploy

variables:
  # Job 级别默认值（可被 CI Variables 覆盖）
  APP_ENV: "development"

# 验证变量的 job
verify-variables:
  stage: build
  image: alpine:latest
  script:
    # 普通变量：直接引用
    - echo "SonarQube URL: $SONAR_HOST"

    # Masked 变量：引用正常，日志中自动脱敏
    - echo "Using NPM token: $NPM_TOKEN"
    # 日志显示: Using NPM token: [MASKED]

    # File 类型变量：cat 查看路径
    - echo "GCP key file path: $GCP_SA_KEY"
    - cat "$GCP_SA_KEY"
    # 日志显示: GCP key file path: /builds/.../tmp/...

    # 使用变量但不 echo 到日志
    - npm config set //registry.npmjs.org/:_authToken $NPM_TOKEN

    # 查看变量来源（带 CI_ 前缀的是预定义变量）
    - echo "Branch: $CI_COMMIT_BRANCH"
    - echo "Pipeline source: $CI_PIPELINE_SOURCE"
    - echo "Job ID: $CI_JOB_ID"

# 部署到 Staging（可以用 DB_PASSWORD 因为它不是 Protected）
deploy-staging:
  stage: deploy
  image: alpine:latest
  script:
    - echo "Deploying to staging with DB: ${DB_HOST:?DB_HOST not set}"
    # ${VAR:?} 语法：如果变量未设置，立即失败退出
    - echo "Using DB password"  # 不输出实际密码
  environment:
    name: staging
  rules:
    - if: '$CI_COMMIT_BRANCH != "main"'
  variables:
    APP_ENV: "staging"

# 部署到生产（需要保护分支才能使用 Protected 变量）
deploy-production:
  stage: deploy
  image: alpine:latest
  script:
    - echo "Deploying to production"
    # PROD_DB_PASSWORD 是 Protected，只有 main 分支才能读到
    - echo "Production DB: ${PROD_DB_HOST:-default.prod.db}"
  environment:
    name: production
  rules:
    - if: '$CI_COMMIT_BRANCH == "main"'
  variables:
    APP_ENV: "production"
```

#### 步骤4：验证变量的安全性

**目标**：验证 Masked/Protected 变量在不同场景下的行为。

```bash
# 测试1：在 feature 分支上尝试访问 Protected 变量
# 创建一个 feature 分支并 push
git checkout -b feature/test-protected-var
# 在 .gitlab-ci.yml 中添加 job：
cat >> .gitlab-ci.yml << 'EOF'

test-protected-var:
  stage: build
  image: alpine:latest
  script:
    - |
      if [ -z "$PROD_DB_PASSWORD" ]; then
        echo "Protected variable NOT available (expected on feature branch)"
      else
        echo "WARNING: Protected variable IS available (unexpected)"
        exit 1
      fi
  rules:
    - if: '$CI_COMMIT_BRANCH != "main"'
EOF

git add .gitlab-ci.yml && git commit -m "test: check protected variable" && git push origin feature/test-protected-var
# 查看 Pipeline 结果 → 应输出 "Protected variable NOT available"

# 测试2：验证 Masked 变量不会出现在日志中
# 在 CI 中执行 echo $NPM_TOKEN
# 查看 job 日志 → 应显示 [MASKED] 而不是实际值

# 测试3：验证变量优先级
# 在 Group 级别设置 DB_HOST=group.db.com
# 在 Project 级别设置 DB_HOST=project.db.com
# 在 CI 中 echo $DB_HOST
# 应输出: project.db.com (Project 覆盖 Group)
```

#### 步骤5：处理历史提交中的密钥泄露

**目标**：从 Git 历史中彻底清除已泄露的密钥。

```bash
# 如果密钥不小心被提交到仓库（即使是历史提交）
# 使用 BFG Repo-Cleaner 清理（比 git filter-branch 快 100 倍）

# 1. 下载 BFG
wget https://repo1.maven.org/maven2/com/madgag/bfg/1.14.0/bfg-1.14.0.jar

# 2. 创建密码替换文件
echo "AKIAIOSFODNN7EXAMPLE" > passwords.txt
echo "old_secret_key" >> passwords.txt

# 3. 克隆仓库（mirror 模式）
git clone --mirror http://gitlab.local:8929/project/repo.git repo.git
cd repo.git

# 4. 运行 BFG 清理
java -jar ../bfg-1.14.0.jar --replace-text ../passwords.txt .

# 5. 清理 reflog 和垃圾回收
git reflog expire --expire=now --all
git gc --prune=now --aggressive

# 6. Push 回 GitLab（force push 保护分支需临时放开）
git push --force

# 7. 最后——立即更换所有已泄露的密钥！
# 清理 Git 历史只是删除了记录，已泄露的密钥需要立即作废和更换
```

### 完整代码清单

- 变量分层架构设计（步骤1）
- API 批量创建变量脚本（步骤2）
- `.gitlab-ci.yml`：变量使用示例（步骤3）
- BFG 密钥清理命令（步骤5）

### 测试验证

```bash
# 验证1：列出 Project 所有变量
curl --header "PRIVATE-TOKEN: $GITLAB_TOKEN" \
  "$GITLAB_URL/api/v4/projects/$PROJECT_ID/variables" | \
  python3 -c "
import json, sys
for v in json.load(sys.stdin):
    print(f'{v[\"key\"]}: type={v[\"variable_type\"]}, masked={v[\"masked\"]}, protected={v[\"protected\"]}')
"

# 验证2：查看变量继承
# 列出 Group 变量
curl --header "PRIVATE-TOKEN: $GITLAB_TOKEN" \
  "$GITLAB_URL/api/v4/groups/$GROUP_ID/variables" | \
  python3 -c "import json,sys; [print(v['key']) for v in json.load(sys.stdin)]"

# 验证3：测试 Protected 变量在非保护分支不可用
# 在 feature 分支 CI 中 echo $PROD_DB_PASSWORD，确认输出为空

# 验证4：验证 Masked 变量日志脱敏
# 在 CI 中 echo "password is $DB_PASSWORD"
# 检查 job 日志中是否显示 [MASKED]
```

## 4. 项目总结

### 优点 & 缺点

| 变量类型 | 优点 | 缺点 |
|---------|------|------|
| env_var | 简单直接，大部分场景适用 | 值在环境变量中可见，有打印泄露风险 |
| file | 适合注入证书/密钥文件 | 多了一层文件路径抽象 |
| masked | 自动日志脱敏 | 不是 100% 可靠（base64 编码的值可能绕过） |
| protected | 分支级别隔离，防止非保护分支泄漏 | 增加 CI 配置复杂度 |

### 变量优先级（低到高）

1. GitLab 预定义变量（`CI_*`）
2. `.gitlab-ci.yml` 全局 `variables`
3. `.gitlab-ci.yml` job 级别 `variables`
4. Pipeline 触发变量（Run Pipeline 时传入）
5. Project CI Variables
6. Group CI Variables
7. Instance CI Variables
8. 同名变量：后定义覆盖先定义（同级），子覆盖父（跨级）

### 适用场景

- **Group 级别变量**：公司级别的公共配置（Docker Registry、SonarQube URL）
- **Project 级别变量 + Masked**：环境相关的密码（Staging/Production 数据库）
- **Protected + Masked**：生产环境密钥（仅 main/tag 可用）
- **File 类型**：K8s kubeconfig、GCP SA Key、SSL 证书
- **Job 级别 variables**：单次 job 需要的特殊配置

**不适用场景**：
- 需要动态刷新的短期凭证（考虑用 Vault/AWS Secrets Manager 集成，第26章会深入）

### 注意事项

- **Masked 变量有长度和内容限制**：值至少 8 个字符，不能包含空格或特殊模式的字符串（如 `true`、`yes`）
- **Protected 变量只在保护分支/标签可用**：main、develop 等保护分支可以访问
- **变量在 job 日志中被自动脱敏仅限于 Masked 变量**：普通 Variable 类型不脱敏
- **API Token 等敏感信息**：务必在 GitLab UI 中设置为 Masked + Protected，而非硬编码

### 常见踩坑经验

1. **变量设置了但 CI 中读不到**：检查是否是 Protected 变量但在非保护分支使用。根因：Protected 变量的分支限制。解决：非敏感变量不勾选 Protected，或在 Settings → Repository 中保护目标分支。
2. **Masked 变量仍然在日志中显示**：值太短（< 8 个字符）或包含 GitLab 无法识别的内容。根因：GitLab 的 Masked 脱敏有最短长度要求。解决：确保密码至少有 8 个字符且符合常见格式。
3. **File 类型变量的文件找不到**：`$GCP_SA_KEY` 被作为普通字符串处理。根因：在脚本中需要将变量值作为文件路径使用（`cat $GCP_SA_KEY`），而不是作为文件内容（`echo "$GCP_SA_KEY"` 会输出路径而非内容）。解决：正确地使用 `cat` 或 `--key-file` 参数传递。

### 思考题

1. GitLab 的 Masked 变量会检测日志中的变量值并替换为 `[MASKED]`。但如果我把密码 base64 编码后再 echo（`echo $PASSWORD | base64`），Masked 机制还能生效吗？如果不能，如何防止这种绕过？
2. 如果你需要在 CI 中使用 AWS STS 的临时凭证（有效期 1 小时），这种场景应该如何设计变量策略？是否有比 CI Variables 更好的方案？

> 答案见附录 D。

### 推广计划提示

- **运维**：变量的分层管理是安全基线，建议审计所有 Project 的变量是否都配置了正确的 Masked/Protected 属性
- **开发**：养成良好的习惯——不要在 script 中 echo 敏感变量值，Masked 不是万能保险
- **安全**：定期扫描 CI 变量列表和 job 日志，检测是否有未正确 Masked 的敏感信息
