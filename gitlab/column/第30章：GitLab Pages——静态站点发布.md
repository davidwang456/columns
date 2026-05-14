# 第30章：GitLab Pages——静态站点发布

## 1. 项目背景

> **业务场景**：一家公司的前端团队需要为每个产品线维护技术文档站点（VuePress 搭建），但他们面临几个痛点：文档和代码在不同仓库（代码在 GitLab、文档在 GitHub Pages），每次文档更新要手动构建并推到 GitHub；文档版本和代码版本不同步（v2.0 的代码对应着 v1.0 的文档）；MR Review 只能 review 代码不能 preview 文档效果。

产品团队也想用同样的机制搭建产品帮助中心，但不知道如何在不增加基础设施的情况下实现。

**痛点放大**：GitLab Pages 可以让你在 CI 中自动构建静态站点（VuePress、Hugo、Docusaurus、Hexo 等）并发布到 GitLab。每个项目自动获得一个 `*.gitlab.io` 的子域名，支持自定义域名和 HTTPS（Let's Encrypt 自动签发）。结合 Review Apps，还可以为每个 MR 自动创建预览环境——合并前就能看到文档效果。

## 2. 项目设计——剧本式交锋对话

**场景**：团队规划文档站点时，大家对 Pages 的功能了解不多。

---

**小胖**："我们用 GitHub Pages 做文档不是挺好的？为什么非要用 GitLab Pages？"

**大师**："从技术上讲没有高下之分，但整合到 GitLab 有几个好处：第一，文档和代码在同一个仓库——版本天然同步。第二，CI 自动发布——文档更新只要 push 代码，剩下的全自动。第三，Review Apps——每个 MR 自动创建一个预览页面，合并前就能看到效果。技术映射——GitHub Pages 就像租了个独立店面卖东西，GitLab Pages 就像在你家楼下开了个小卖部——方便，而且是你的。"

**小白**："那配置复杂吗？我看 Pages 需要写 `.gitlab-ci.yml`？"

**大师**："不复杂。核心就两步：第一步，CI job 生成静态文件到 `public/` 目录。第二步，把 `public/` 目录作为 artifacts 保存，GitLab Pages 会自动部署。如果你用 VuePress/Hugo/Docusaurus 这类工具，它们构建后默认输出到 `public/` 或 `dist/`——只需要在 CI 中把输出目录指向 `public/`。"

**小胖**："自定义域名呢？我想用 `docs.mycompany.com`。"

**大师**："配置两步：DNS 加一条 CNAME 指向 `<namespace>.gitlab.io`，GitLab Pages 设置中添加这个域名，GitLab 自动用 Let's Encrypt 签发 SSL 证书。全部 HTTPS，零运维。"

**小胖**："那每个 MR 的预览页面怎么实现？"

**大师**："利用 GitLab 的 Review Apps 机制。CI 中为 MR 创建一个 `environment`，Pages 发布到 `public/` 时用 `$CI_COMMIT_REF_SLUG` 作为子路径前缀。MR 合并后 `stop_review` job 自动清理。每个 MR 有独立的预览 URL。"

---

## 3. 项目实战

### 环境准备

> **目标**：搭建一个 VuePress 文档站点，通过 GitLab CI 自动发布到 Pages，配置自定义域名和 MR 预览。

**前置条件**：GitLab CE 17.x，Pages 默认启用。

### 分步实现

#### 步骤1：初始化 VuePress 项目

**目标**：在项目中初始化 VuePress 2.x，配置基础结构。

```bash
# 在项目根目录
mkdir docs && cd docs

# 初始化
npm init -y
npm install -D vuepress@next @vuepress/theme-default

# 配置 VuePress
mkdir -p .vuepress/ public/

cat > .vuepress/config.js << 'EOF'
import { defaultTheme } from '@vuepress/theme-default'

export default {
  lang: 'zh-CN',
  title: '电商平台技术文档',
  description: 'Acme 电商平台开发者文档',
  base: '/',

  theme: defaultTheme({
    navbar: [
      { text: '首页', link: '/' },
      { text: '快速开始', link: '/guide/' },
      { text: 'API 参考', link: '/api/' },
      { text: '运维', link: '/ops/' },
    ],
    sidebar: {
      '/guide/': [
        { text: '快速开始', link: '/guide/' },
        { text: '环境搭建', link: '/guide/setup' },
        { text: '项目结构', link: '/guide/structure' },
      ],
      '/api/': [
        { text: 'API 概述', link: '/api/' },
        { text: '认证方式', link: '/api/auth' },
        { text: '商品接口', link: '/api/products' },
        { text: '订单接口', link: '/api/orders' },
      ],
      '/ops/': [
        { text: '部署指南', link: '/ops/' },
        { text: '监控告警', link: '/ops/monitoring' },
        { text: '故障排查', link: '/ops/troubleshooting' },
      ],
    },
    repo: 'https://gitlab.local/acme-corp/ecommerce/shop-api',
  }),
}
EOF

# 创建文档内容
cat > README.md << 'EOF'
---
home: true
heroText: 电商平台文档
tagline: 从入门到上线的完整指南
actions:
  - text: 快速开始 →
    link: /guide/
features:
  - title: 📦 商品管理
    details: 完整的商品 CRUD、搜索、分类功能
  - title: 🛒 订单系统
    details: 购物车、下单、支付全流程
  - title: 🚀 运维工具
    details: 自动部署、监控、告警
footer: Acme Corp © 2026
EOF

# 创建 .gitignore
echo "node_modules/\npublic/\n.vuepress/.cache/\n.vuepress/.temp/" > .gitignore
```

#### 步骤2：配置 Pages CI/CD

**目标**：编写 CI 配置，自动构建并发布到 GitLab Pages。

```yaml
# .gitlab-ci.yml - GitLab Pages
stages:
  - pages          # GitLab Pages 要求 job 在 pages stage
  - deploy-review  # MR 预览
  - stop-review    # 清理预览

variables:
  NODE_VERSION: "20-alpine"

# ===== 正式 Pages 发布（main 分支）=====
pages:
  stage: pages
  image: node:${NODE_VERSION}
  before_script:
    - cd docs
    - npm ci --prefer-offline
  script:
    - npx vuepress build .
    - # 构建输出在 docs/.vuepress/dist/
    # GitLab Pages 要求 artifacts 路径为 public/
    - mkdir -p ../public
    - cp -r .vuepress/dist/* ../public/
  artifacts:
    paths:
      - public/           # ⚠️ 必须是 public/ 目录！
    expire_in: 30 days
  rules:
    - if: '$CI_COMMIT_BRANCH == "main"'
  environment:
    name: production
    url: https://acme-corp.gitlab.io/ecommerce/shop-api    # GitLab Pages URL

# ===== MR 预览 Pages =====
review-pages:
  stage: deploy-review
  image: node:${NODE_VERSION}
  before_script:
    - cd docs
    - npm ci --prefer-offline
  script:
    - npx vuepress build .
    # MR 预览使用子目录（避免覆盖 main 的 Pages）
    - mkdir -p ../public/mr-${CI_MERGE_REQUEST_IID}
    - cp -r .vuepress/dist/* ../public/mr-${CI_MERGE_REQUEST_IID}/
  artifacts:
    paths:
      - public/
    expire_in: 7 days
  rules:
    - if: '$CI_PIPELINE_SOURCE == "merge_request_event"'
  environment:
    name: review/mr-${CI_MERGE_REQUEST_IID}
    url: https://acme-corp.gitlab.io/ecommerce/shop-api/mr-${CI_MERGE_REQUEST_IID}
    on_stop: stop-review-pages

# 清理 MR 预览
stop-review-pages:
  stage: stop-review
  image: alpine:latest
  script:
    - echo "Cleaning up review pages for MR #${CI_MERGE_REQUEST_IID}"
    # 实际清理需要额外步骤（如删除子目录），简化示例
  environment:
    name: review/mr-${CI_MERGE_REQUEST_IID}
    action: stop
  rules:
    - if: '$CI_PIPELINE_SOURCE == "merge_request_event"'
  when: manual
```

**Pages 关键要点**：

| 要点 | 说明 |
|------|------|
| job 名称 | 必须是 `pages` |
| artifacts 路径 | 必须是 `public/` |
| job 所在 stage | stage 名无限制，但通常叫 `pages` |
| 最大站点大小 | 1GB（GitLab.com）或自定义 |

#### 步骤3：配置自定义域名和 HTTPS

**目标**：将 Pages 从 `*.gitlab.io` 改为自定义域名。

```
1. DNS 配置（在域名管理商处）：
   docs.acme.com  CNAME  acme-corp.gitlab.io

2. GitLab Pages 设置：
   Project → Settings → Pages
   → New Domain: docs.acme.com
   → ✅ Automatic certificate management using Let's Encrypt
   → Save

3. 等待 DNS 生效 + 证书签发（通常 5-10 分钟）
   验证：curl -I https://docs.acme.com
   应返回 HTTP 200 + Let's Encrypt 证书
```

#### 步骤4：其他静态站点框架配置

**Hugo**：

```yaml
# .gitlab-ci.yml - Hugo Pages
pages:
  stage: pages
  image: klakegg/hugo:0.123-ext-alpine
  script:
    - hugo --destination public
  artifacts:
    paths:
      - public/
  rules:
    - if: '$CI_COMMIT_BRANCH == "main"'
```

**Docusaurus**：

```yaml
pages:
  stage: pages
  image: node:20-alpine
  script:
    - cd website
    - npm ci
    - npm run build
    - mkdir -p ../public
    - cp -r build/* ../public/
  artifacts:
    paths:
      - public/
```

### 完整代码清单

- VuePress 项目结构（`docs/`）
- `.gitlab-ci.yml`（Pages + Review Apps）
- DNS 配置记录

### 测试验证

```bash
# 验证1：Pages 部署成功
# Project → Deploy → Pages
# 应显示 Pages URL 和 "Deployed" 状态

# 验证2：文档站点可访问
curl -I https://acme-corp.gitlab.io/ecommerce/shop-api
# HTTP 200

# 验证3：MR 预览
# 创建 MR → 查看 MR 页面 → "View app" 按钮
# 或直接访问 preview URL

# 验证4：自定义域名 HTTPS
openssl s_client -connect docs.acme.com:443 -servername docs.acme.com < /dev/null 2>/dev/null | openssl x509 -noout -issuer -subject
# 确认是 Let's Encrypt 签发的证书
```

## 4. 项目总结

### 静态站点框架对比

| 框架 | 语言 | 构建速度 | 适用场景 |
|------|------|---------|---------|
| VuePress | Vue/JS | 快 | 技术文档 |
| Hugo | Go | 极快 | 博客、文档 |
| Docusaurus | React/JS | 中 | 开源项目文档 |
| Hexo | Node.js | 中 | 博客 |
| MkDocs | Python | 快 | 文档 |
| Jekyll | Ruby | 慢 | 博客 |

### 适用场景

- **技术文档站点**：API 文档、开发指南、运维手册
- **产品帮助中心**：用户手册、FAQ
- **个人/团队博客**
- **内部知识库**：用 GitLab Pages 替代 Confluence 部分功能

**不适用场景**：
- 需要服务端动态渲染的应用（Pages 是纯静态）
- 需要登录/权限控制的页面（可以用 nginx 反向代理保护）

### 注意事项

- **Pages 站点的代码必须是公开的**（CE 版限制——Private 项目也可以用 Pages，但 URL 可被猜测）
- **artifacts 路径必须是 `public/`**，否则 GitLab 不会识别为 Pages 发布
- **job 名称必须是 `pages`**——这是 GitLab 的约定，不是 CI 变量的配置
- **Pages 更新不是实时的**——产生新 artifacts 后需要 1-3 分钟生效

### 常见踩坑经验

1. **Pages 404 但 Pipeline 成功了**：artifacts 路径不是 `public/`。根因：GitLab Pages 只会从 `public/` 目录读取文件。解决：确认 CI 中构建产物被复制到 `public/`。
2. **自定义域名证书签发失败**：DNS 记录未生效。根因：Let's Encrypt 验证 CNAME 时，DNS 还没同步。解决：等待 DNS 生效后再添加域名，或先验证 nslookup 返回正确的 CNAME。
3. **Pages MR 预览覆盖了主站**：MR 预览也写入了 `public/` 根目录。根因：没有用子目录隔离 MR 预览。解决：MR 预览写入 `public/mr-${CI_MERGE_REQUEST_IID}/` 子目录。

### 思考题

1. 如果你有 10 个微服务项目，每个都有自己的 VuePress 文档站点。如何设计 CI 模板和 Pages 发布策略，让所有文档统一在一个入口页面聚合展示？
2. GitLab Pages 默认文件名是 `${namespace}.gitlab.io/${project}`。如果你希望 Pages URL 为 `docs.acme.com`（不包含项目名），应该如何配置？多项目共用一个域名时的路由如何处理？

> 答案见附录 D。

### 推广计划提示

- **开发**：Pages + CI 自动发布让文档维护从"额外工作"变成"写代码顺便做的事"
- **产品**：用 Pages 快速搭建产品帮助中心，节省独立站点的开发和运维成本
- **运维**：Pages 的日志和指标可通过 CI artifacts 和 Pipeline 状态监控
