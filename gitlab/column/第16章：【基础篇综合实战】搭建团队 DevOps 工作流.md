# 第16章：【基础篇综合实战】搭建团队 DevOps 工作流

## 1. 项目背景

> **业务场景**：一家 10 人创业公司决定全面采用 GitLab 作为研发协作平台。CTO 要求在一周内搭建完整的 DevOps 工作流：代码提交后自动构建 → 自动测试 → 构建 Docker 镜像 → 自动部署到测试环境。团队成员有的是从 SVN 转过来的，有的是刚毕业的新人，对 GitLab 的完整流程没有任何经验。

当前的研发现状令人头疼：
- 代码靠 FTP 上传到服务器，经常出现"在我机器上好的啊"的问题
- 没有 Code Review，代码质量完全依赖开发者自觉
- 测试靠开发自己人肉点，经常漏测
- 部署上线需要运维手动执行 10+ 个步骤的脚本，中间任何一个环节出错都要重来
- 版本管理一团糟——同一个 War 包三个环境（测试、预发布、生产）各一个版本，谁都不知道哪个包对应哪次发布

CTO 下定决心："我们需要一个标准化的流程——从 code push 到 production deploy，一键触发，中间零人工干预。"

**痛点放大**：本章将融会贯通基础篇 1-15 章的全部知识，从零搭建一个完整的、工业级的团队 DevOps 工作流。这不仅是技术整合，更是一次团队研发文化的升级。

## 2. 项目设计——剧本式交锋对话

**场景**：项目启动会，CTO 在白板上画出理想中的 DevOps 工作流。

---

**CTO**："我们的目标很简单：代码 push 上去，剩下的事情全自动。测试、构建、部署，全部由 GitLab 处理。"

**小胖**："这也太理想了吧？我们现在连 `.gitlab-ci.yml` 怎么写都还没搞清楚呢。"

**大师**："其实不复杂。我们把整个流程拆成 5 个步骤：第一，代码管理——用 GitLab Flow 的分支策略和保护规则；第二，CI 构建——提交代码后自动跑 lint、单元测试、构建；第三，镜像打包——测试通过后自动构建 Docker 镜像；第四，制品管理——镜像推送到 GitLab Registry；第五，自动部署——用 Docker Compose 或简单的 SSH 部署到测试环境。"

**小白**："那我们现有的 War 包部署方式是不是要全改？学习成本会不会太高？"

**大师**："循序渐进。第一步先跑通 CI（lint + test），让团队看到 Pipeline 的好处——代码质量立竿见影提高。第二步再加 Docker 构建。第三步再加自动部署。每个步骤都要让团队看到价值，而不是一次性推到面前。"

**小胖**："那我们用什么技术栈？Java 还是 Node.js？"

**大师**："我们以 Node.js 为例——它是目前创业公司最常用的技术栈。但本章涉及的 CI/CD 概念和 Pipeline 设计模式，对 Java、Python、Go 等项目同样适用。技术映射——CI/CD 的核心不是你用什么语言，而是一套标准化的流程：代码 → 质量门禁 → 构建 → 测试 → 制品 → 部署。"

---

## 3. 项目实战

### 环境准备

> **目标**：从零搭建一个 Node.js 项目的完整 DevOps 工作流，覆盖代码管理 → CI → 镜像构建 → 自动部署全流程。

**前置条件**：

| 组件 | 版本 | 用途 |
|------|------|------|
| GitLab CE | 17.x | DevOps 平台 |
| GitLab Runner (Docker) | 17.x | CI 执行器 |
| Docker | 24+ | 容器运行时 |
| Node.js | 20 LTS | 开发环境 |

### 分步实现

#### 步骤1：初始化项目结构与代码管理

**目标**：创建项目，初始化基础代码，配置分支保护和 MR 模板。

```bash
# 创建项目
mkdir express-api && cd express-api
git init

# 初始化 package.json
cat > package.json << 'EOF'
{
  "name": "express-api",
  "version": "1.0.0",
  "description": "Team DevOps demo API",
  "main": "src/index.js",
  "scripts": {
    "start": "node src/index.js",
    "dev": "node --watch src/index.js",
    "test": "jest --coverage --reporters=default --reporters=jest-junit",
    "lint": "eslint src/ tests/",
    "build": "echo 'Build completed'"
  },
  "dependencies": {
    "express": "^4.19.0"
  },
  "devDependencies": {
    "jest": "^29.7.0",
    "jest-junit": "^16.0.0",
    "eslint": "^8.56.0",
    "supertest": "^6.3.0"
  }
}
EOF

# 初始化项目代码
mkdir -p src tests

cat > src/index.js << 'EOF'
const express = require('express');
const app = express();
const PORT = process.env.PORT || 3000;

app.get('/health', (req, res) => {
  res.json({ status: 'ok', timestamp: new Date().toISOString() });
});

app.get('/api/greeting', (req, res) => {
  const name = req.query.name || 'World';
  res.json({ message: `Hello, ${name}!` });
});

if (require.main === module) {
  app.listen(PORT, () => {
    console.log(`Server running on port ${PORT}`);
  });
}

module.exports = app;
EOF

cat > tests/app.test.js << 'EOF'
const request = require('supertest');
const app = require('../src/index');

describe('API Tests', () => {
  test('GET /health returns ok', async () => {
    const res = await request(app).get('/health');
    expect(res.status).toBe(200);
    expect(res.body.status).toBe('ok');
  });

  test('GET /api/greeting returns greeting', async () => {
    const res = await request(app).get('/api/greeting?name=GitLab');
    expect(res.status).toBe(200);
    expect(res.body.message).toContain('GitLab');
  });

  test('GET /api/greeting without name uses default', async () => {
    const res = await request(app).get('/api/greeting');
    expect(res.body.message).toContain('World');
  });
});
EOF

# 安装依赖
npm install
npm test  # 确认本地测试通过

# 配置分支保护（GitLab UI）
# Settings → Repository → Protected branches
# main: Allowed to merge = Maintainers, Allowed to push = No one

# 创建 .gitignore 并提交
cat > .gitignore << 'EOF'
node_modules/
dist/
.env
*.log
coverage/
junit.xml
EOF

git add .
git commit -m "feat: initialize express API project with tests"
git remote add origin http://gitlab.local:8929/acme-corp/ecommerce/express-api.git
git push -u origin main
```

#### 步骤2：配置完整的 CI/CD Pipeline

**目标**：编写 `.gitlab-ci.yml`，实现 lint → test → build → docker build → deploy 全流程。

```yaml
# .gitlab-ci.yml
stages:
  - quality      # 代码质量检查（lint）
  - test         # 单元测试
  - build        # Docker 构建
  - deploy       # 部署

variables:
  NODE_VERSION: "20-alpine"
  DOCKER_DRIVER: overlay2
  IMAGE_TAG: $CI_REGISTRY_IMAGE:$CI_COMMIT_SHORT_SHA

# 缓存配置
.npm_cache:
  cache:
    key:
      files:
        - package-lock.json
    paths:
      - node_modules/

# ===== Stage 1: 代码质量 =====
lint:
  stage: quality
  image: node:${NODE_VERSION}
  script:
    - npm ci --prefer-offline
    - npm run lint
  rules:
    - if: '$CI_PIPELINE_SOURCE == "merge_request_event"'
    - if: '$CI_COMMIT_BRANCH == "main"'
  cache:
    key:
      files:
        - package-lock.json
    paths:
      - node_modules/

# ===== Stage 2: 测试 =====
unit-test:
  stage: test
  image: node:${NODE_VERSION}
  script:
    - npm ci --prefer-offline
    - npm run test
  coverage: '/All files[^|]*\|[^|]*\s+([\d\.]+)/'
  artifacts:
    when: always
    reports:
      junit: junit.xml
      coverage_report:
        coverage_format: cobertura
        path: coverage/cobertura-coverage.xml
  rules:
    - if: '$CI_PIPELINE_SOURCE == "merge_request_event"'
    - if: '$CI_COMMIT_BRANCH == "main"'
  cache:
    key:
      files:
        - package-lock.json
    paths:
      - node_modules/
    policy: pull

# ===== Stage 3: Docker 构建（仅 main 分支）=====
docker-build:
  stage: build
  image: docker:24-dind
  services:
    - docker:24-dind
  before_script:
    - docker login -u $CI_REGISTRY_USER -p $CI_JOB_TOKEN $CI_REGISTRY
  script:
    - docker build -t $IMAGE_TAG -t $CI_REGISTRY_IMAGE:latest .
    - docker push $IMAGE_TAG
    - docker push $CI_REGISTRY_IMAGE:latest
  rules:
    - if: '$CI_COMMIT_BRANCH == "main"'

# ===== Stage 4: 部署到 Staging =====
deploy-staging:
  stage: deploy
  image: alpine:latest
  before_script:
    - apk add --no-cache openssh-client
  script:
    - |
      echo "Deploying ${IMAGE_TAG} to staging server..."
      ssh -o StrictHostKeyChecking=no deploy@staging-server << 'ENDSSH'
        docker pull ${CI_REGISTRY_IMAGE}:latest
        docker stop express-api || true
        docker rm express-api || true
        docker run -d --name express-api -p 3000:3000 \
          -e NODE_ENV=staging \
          --restart=always \
          ${CI_REGISTRY_IMAGE}:latest
        docker image prune -f
      end
  environment:
    name: staging
    url: http://staging.example.com
  rules:
    - if: '$CI_COMMIT_BRANCH == "main"'
  needs:
    - docker-build
```

#### 步骤3：编写 Dockerfile 和 docker-compose.yml

**目标**：多阶段构建 + 本地开发环境编排。

```dockerfile
# Dockerfile
FROM node:20-alpine AS builder
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --only=production
COPY . .

FROM node:20-alpine AS runner
RUN addgroup -g 1001 -S nodejs && adduser -S nodejs -u 1001
WORKDIR /app
COPY --from=builder --chown=nodejs:nodejs /app/node_modules ./node_modules
COPY --from=builder --chown=nodejs:nodejs /app/src ./src
COPY --from=builder --chown=nodejs:nodejs /app/package.json ./
USER nodejs
EXPOSE 3000
HEALTHCHECK --interval=30s --timeout=3s \
  CMD wget -q --spider http://localhost:3000/health || exit 1
CMD ["node", "src/index.js"]
```

```yaml
# docker-compose.yml（本地开发环境）
version: '3.8'
services:
  app:
    build: .
    ports:
      - "3000:3000"
    environment:
      - NODE_ENV=development
    volumes:
      - ./src:/app/src  # 热重载
    command: node --watch src/index.js
```

#### 步骤4：设置 CI Variables 和 Webhook 通知

**目标**：配置部署服务器的 SSH 密钥变量和企业微信/钉钉通知。

```bash
# 通过 API 设置 CI 变量
# 部署服务器 SSH 私钥（File 类型，方便直接使用）
curl --request POST \
  --header "PRIVATE-TOKEN: $GITLAB_TOKEN" \
  --header "Content-Type: application/json" \
  --data '{
    "key": "SSH_PRIVATE_KEY",
    "value": "'"$(cat ~/.ssh/deploy_key | base64)"'",
    "variable_type": "file",
    "protected": true,
    "masked": true
  }' \
  "$GITLAB_URL/api/v4/projects/$PROJECT_ID/variables"

# 或者用 Project Settings → CI/CD → Variables 添加：
# DEPLOY_HOST: staging.example.com
# DEPLOY_USER: deploy
# SSH_KNOWN_HOSTS: $(ssh-keyscan staging.example.com)
```

**配置 Webhook 通知（企业微信）**：

```yaml
# 在 .gitlab-ci.yml 中添加通知 job
notify-success:
  stage: deploy
  image: alpine:latest
  script:
    - |
      curl -X POST "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxx" \
        -H "Content-Type: application/json" \
        -d "{
          \"msgtype\": \"markdown\",
          \"markdown\": {
            \"content\": \"## ✅ 部署成功\n> 项目: ${CI_PROJECT_NAME}\n> 分支: ${CI_COMMIT_BRANCH}\n> 提交: ${CI_COMMIT_SHORT_SHA}\n> 环境: staging\n> [查看 Pipeline](${CI_PIPELINE_URL})\"
          }
        }"
  when: on_success
  needs:
    - deploy-staging

notify-failure:
  stage: deploy
  image: alpine:latest
  script:
    - |
      curl -X POST "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxx" \
        -H "Content-Type: application/json" \
        -d "{
          \"msgtype\": \"markdown\",
          \"markdown\": {
            \"content\": \"## ❌ Pipeline 失败\n> 项目: ${CI_PROJECT_NAME}\n> 分支: ${CI_COMMIT_BRANCH}\n> 提交: ${CI_COMMIT_SHORT_SHA}\n> [查看 Pipeline](${CI_PIPELINE_URL})\"
          }
        }"
  when: on_failure
```

#### 步骤5：端到端验证

**目标**：从代码提交到部署成功，跑通完整流程。

```bash
# 1. 创建 feature 分支
git checkout -b feature/add-user-api

# 2. 添加新功能
cat >> src/index.js << 'EOF'

// Users API
const users = [
  { id: 1, name: 'Alice' },
  { id: 2, name: 'Bob' },
];

app.get('/api/users', (req, res) => {
  res.json(users);
});

app.get('/api/users/:id', (req, res) => {
  const user = users.find(u => u.id === parseInt(req.params.id));
  if (!user) return res.status(404).json({ error: 'User not found' });
  res.json(user);
});
EOF

# 3. 添加测试
cat >> tests/app.test.js << 'EOF'

describe('Users API', () => {
  test('GET /api/users returns list', async () => {
    const res = await request(app).get('/api/users');
    expect(res.status).toBe(200);
    expect(res.body.length).toBe(2);
  });

  test('GET /api/users/:id returns user', async () => {
    const res = await request(app).get('/api/users/1');
    expect(res.status).toBe(200);
    expect(res.body.name).toBe('Alice');
  });

  test('GET /api/users/:id 404 for missing', async () => {
    const res = await request(app).get('/api/users/999');
    expect(res.status).toBe(404);
  });
});
EOF

# 4. 提交并 push
git add . && git commit -m "feat: add users API endpoints" && git push

# 5. 在 GitLab 创建 MR → 观察 Pipeline：
# - Stage 1 (quality): lint 自动运行
# - Stage 2 (test): 单元测试自动运行 → 覆盖率报告显示在 MR 中
# - Pipeline 通过后显示绿色勾

# 6. Reviewer 审批后合并到 main → 自动触发完整 Pipeline：
# - Stage 3 (build): 构建 Docker 镜像 → 推送到 Container Registry
# - Stage 4 (deploy): 自动部署到 Staging 环境

# 7. 验证部署结果
curl http://staging.example.com/health
# {"status":"ok","timestamp":"2026-05-12T..."}

curl http://staging.example.com/api/users
# [{"id":1,"name":"Alice"},{"id":2,"name":"Bob"}]
```

### 完整代码清单

- `src/index.js`：Express API 应用
- `tests/app.test.js`：单元测试（Jest + Supertest）
- `Dockerfile`：多阶段构建
- `docker-compose.yml`：本地开发环境
- `.gitlab-ci.yml`：完整 CI/CD Pipeline

### 验收标准

```bash
# ✅ 代码 push 到 main → CI 自动触发
# ✅ Lint 检查通过
# ✅ 单元测试通过 + 覆盖率报告
# ✅ Docker 镜像自动构建
# ✅ 镜像推送到 GitLab Registry
# ✅ 自动部署到 Staging 环境
# ✅ 部署成功通知到企业微信
# ✅ 全过程 < 5 分钟
# ✅ 零人工干预
```

### 测试验证

```bash
# 验证1：CI Pipeline 自动触发
# 推送代码到任意分支 → CI/CD → Pipelines → 确认 Pipeline 自动创建
curl --header "PRIVATE-TOKEN: $GITLAB_TOKEN" \
  "$GITLAB_URL/api/v4/projects/$PROJECT_ID/pipelines?per_page=1" | \
  python3 -c "import json,sys; p=json.load(sys.stdin)[0]; print(f'Pipeline #{p[\"id\"]}: {p[\"status\"]}')"
# 预期输出：Pipeline #xxx: success  (或 running)

# 验证2：单元测试报告可查看
# MR 页面 → 应显示 Test summary 区域
curl --header "PRIVATE-TOKEN: $GITLAB_TOKEN" \
  "$GITLAB_URL/api/v4/projects/$PROJECT_ID/jobs?scope=success&per_page=1" | \
  python3 -c "import json,sys; j=json.load(sys.stdin)[0]; print(f'Job: {j[\"name\"]}, Stage: {j[\"stage\"]}')"

# 验证3：Docker 镜像推送到 Registry
curl --header "PRIVATE-TOKEN: $GITLAB_TOKEN" \
  "$GITLAB_URL/api/v4/projects/$PROJECT_ID/registry/repositories" | \
  python3 -c "import json,sys; print(f'Repos: {len(json.load(sys.stdin))}')"
# 预期输出：Repos: 1 （至少 1 个镜像仓库）

# 验证4：Staging 环境部署成功
curl -s http://staging.internal:3000/health | python3 -m json.tool
# 预期输出：{"status":"ok","timestamp":"..."}

# 验证5：飞书/Slack 通知发送
# 检查飞书群或 Slack 频道 → 确认收到部署成功通知
# 通知内容应包含：项目名、分支名、commit SHA、Pipeline 链接
```

## 4. 项目总结

### 实现成果

| 环节 | 基础篇相关章节 | 本综合实战中的应用 |
|------|---------------|-------------------|
| 代码管理 | 第3-5章 | GitLab Flow 分支策略 + MR 模板 + 保护分支 |
| CI 配置 | 第9-10章 | 4 阶段 Pipeline + Docker Runner |
| 制品管理 | 第12章 | Artifacts（测试报告）+ Cache（npm） |
| 密钥管理 | 第13章 | SSH 私钥 File 变量 + Masked |
| 镜像仓库 | 第14章 | Docker 构建 + Registry 推送 |
| 日常运维 | 第15章 | 备份 + 日志监控 + 故障排查 |

### 工作流对比

| 维度 | 传统方式（之前） | DevOps 方式（之后） |
|------|-----------------|-------------------|
| 代码提交 | FTP 上传 | Git push + 分支管理 |
| 质量检查 | 人工 review（经常跳过） | CI 自动 lint + test |
| 构建 | 开发者本地手动打包 | CI 自动 Docker build |
| 部署 | 运维手动执行脚本 | CI 自动部署 |
| 回滚 | 手动找旧包重新部署 | `docker run <上一个镜像标签>` |
| 可追溯 | "大概是上个月发布的吧" | 每次部署关联到精确的 commit |

### 注意事项

- **Runner 必须保持在线**：至少 1 个 Docker Runner 处于 active 状态
- **Registry 认证**：CI 中使用 `$CI_JOB_TOKEN`，本地使用 Personal Access Token
- **部署密钥安全**：SSH 私钥务必用 File 类型 + Protected + Masked
- **环境隔离**：Staging 和 Production 应使用不同的部署目标

### 常见踩坑经验

1. **Docker build 在 CI 中失败**：`Cannot connect to the Docker daemon`。根因：Runner 没有挂载 docker.sock 或未开启 privileged。解决：Runner config.toml 中配置 `privileged = true` 和 `volumes = ["/var/run/docker.sock:/var/run/docker.sock"]`。
2. **deploy job 中 ssh 连接被拒**：StrictHostKeyChecking 导致未知主机。根因：第一次连接目标主机时 SSH 需要确认指纹。解决：用 `-o StrictHostKeyChecking=no` 或提前添加 `SSH_KNOWN_HOSTS`。
3. **Pipeline 缓存不生效**：每次都重新 npm install。根因：cache key 配置错误。解决：确保 `cache:key:files: [package-lock.json]` 且每次提交 lock 文件。

### 思考题

1. 如果这个项目需要同时部署到 Staging 和 Production 两个环境，但只有 Production 部署需要手动点击触发，应该如何修改 `.gitlab-ci.yml`？
2. 当前部署是用 SSH 到目标机器执行 `docker run`。如果要迁移到 Kubernetes 部署，Pipeline 需要做哪些改动？核心概念如何映射？

> 答案见附录 D。

### 推广计划提示

- **开发**：本章是基础篇的集大成，建议边阅读边在沙箱 GitLab 上实操
- **运维**：关注 deploy job 的实现——SSH 部署、环境变量管理、事后通知
- **测试**：关注 CI 中测试报告（JUnit）和覆盖率（cobertura）的自动解析
- **管理**：这是向团队演示 GitLab 完整价值的最佳案例——从代码到部署全自动化
