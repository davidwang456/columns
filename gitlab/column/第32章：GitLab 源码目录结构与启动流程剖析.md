# 第32章：GitLab 源码目录结构与启动流程剖析

## 1. 项目背景

> **业务场景**：一位资深开发在排查一个奇怪的 GitLab Bug——用户创建 MR 后，有时需要 30 秒才能看到 diff。他想修改 GitLab 源码来加调试日志，但打开 GitLab 的源代码仓库后傻眼了：3000+ 个文件，数百个 Ruby 模块，不知道从哪里入手。

他尝试在 `app/controllers` 下面找 MR 相关的代码，但发现 GitLab 的代码不是简单的 MVC——大量逻辑在 `lib/`、`ee/`、`app/services/` 中，而且很多是运行时动态加载的。折腾了一天才找到 MR diff 的计算逻辑在 `lib/gitlab/git/diff.rb` 和 `app/services/merge_requests/refresh_service.rb` 中。他感叹："如果有一张 GitLab 源码的导航地图就好了——告诉我每个目录是干嘛的，请求是怎么从 Nginx 一路走到数据库的。"

**痛点放大**：GitLab 的代码库规模庞大（Ruby on Rails 单体约 50 万行 + Go 微服务约 20 万行），但结构清晰——只要你理解了它的分层架构和请求处理链路，就能快速定位任意功能的源码位置。本章将带你从 Rails 的 `config.ru` 入口开始，顺着一次完整的 HTTP 请求，建立 GitLab 源码的全局地图。这一章不是让你读完 50 万行代码，而是给你一张"地铁线路图"——以后你需要去哪里，看一眼就知道该坐哪条线。

## 2. 项目设计——剧本式交锋对话

**场景**：源码阅读小组第一次聚会，三人在会议室投屏看 GitLab 的 GitHub 仓库，树形目录展开后密密麻麻。

---

**小胖**（痛苦地抓头）："我就想找到 MR 的审批逻辑在哪，结果在 controller 里没找到，在 model 里也没找到——GitLab 到底把业务逻辑放哪了？我搜 'approve' 搜出来 500 个文件！"

**大师**："小胖，GitLab 遵循的是 Rails 的 Thin Controller, Fat Model, Service Layer 模式加一个 Finder 层。咱们一层层剥开：Controller 只做路由和参数解析——代码在 `app/controllers/`，非常薄，通常不超过 50 行。Model 负责数据持久化和关联——`app/models/`，但 GitLab 的 Model 也不算胖。核心业务逻辑真正在 Service 层——`app/services/`，这里面有 500+ 个 Service 类，MR 的审批逻辑也在其中。"

**小胖**："那我搜 `app/services/merge_requests/`，果然有 `approval_service.rb`！但为什么还有 `lib/` 目录？里面的代码跟 `app/services/` 有什么不同？"

**大师**："`app/` 是 Rails 自动加载的代码——遵循 Rails 约定，文件名和类名一一对应。`lib/` 是 Ruby 通用库——这些代码不依赖 Rails 框架，可以被独立使用。比如 `lib/gitlab/git/` 目录下的代码是对 Git 操作的 Ruby 封装，它们通过 gRPC 客户端与 Gitaly 服务通信，与 Rails 的 HTTP 请求处理完全独立。技术映射——`app/` 就像商场里的店铺（有固定位置和招牌），`lib/` 就像仓库里的工具箱（东西在哪取决于工具类型）。"

**小白**（翻着代码）："我还有一个困惑——`ee/` 目录是什么？我看到 `app/models/project.rb` 和 `ee/app/models/ee/project.rb` 都在修改同一个 Project 类。GitLab 的 CE 和 EE 是怎么做到共享同一套代码的？"

**大师**："这是 GitLab 最巧妙的设计——'代码叠加'模式。CE 是基础版，EE 在 CE 的基础上通过 Ruby 的 `prepend` 机制叠加新功能。`ee/app/models/ee/project.rb` 定义了一个 `EE::Project` 模块，在启动时通过 `Project.prepend(EE::Project)` 注入到 CE 的 Project 类中。这样 EE 可以覆盖 CE 的任何方法，也可以添加新方法——而且 CE 的代码里完全看不到 EE 的影子，两个团队可以独立开发。"

**小胖**："那启动流程呢？Rails 项目一般都是 `rails server` 启动，但 GitLab Omnibus 版本用的是 Puma——它加载了哪些东西？"

**大师**："启动流程从 `config.ru` 开始，依次执行：加载 Rails environment → 100+ 个 initializer 依次执行 → 建立数据库连接池 → 初始化 Redis/Sidekiq → 建立 Gitaly gRPC 连接 → 加载路由 → 启动 Puma worker。理解这个顺序很重要——因为 initializer 的执行顺序会影响全局配置的加载。技术映射——GitLab 的启动流程就像飞机起飞前的检查清单：按顺序检查引擎（数据库）、航电（Redis）、通讯（Gitaly）、舱门（路由），每一步都必须在下一步之前完成。"

**小白**："那我怎么实际调试 GitLab 源码？在本地搭建开发环境吗？"

**大师**："GitLab 官方提供了 GDK（GitLab Development Kit）来简化本地开发环境搭建。但如果你只是想阅读源码和加调试日志，更简单的方式是用 Omnibus 版本 + `gitlab-rails console` 或者直接修改 `/opt/gitlab/embedded/service/gitlab-rails/` 下的文件。不过注意——生产环境不要随意修改源码，调试用 test 环境。"

---

## 3. 项目实战

### 环境准备

> **目标**：克隆 GitLab 源码，搭建本地阅读环境，追踪一次完整的 HTTP 请求。

| 工具 | 版本/方式 | 用途 |
|------|---------|------|
| GitLab 源码 | `git clone https://gitlab.com/gitlab-org/gitlab.git` | 代码阅读 |
| RubyMine / VS Code | 最新版 | IDE（推荐 RubyMine 对 Rails 的支持更好） |
| GDK | `git clone https://gitlab.com/gitlab-org/gitlab-development-kit.git` | 本地开发环境（可选） |
| GitLab Omnibus | 17.x（测试服务器） | 在运行环境中调试 |

### 分步实现

#### 步骤1：克隆源码并理解目录结构

**目标**：获取源码，生成目录树，标注每个目录的职责。

```bash
# 克隆源码（约 2GB，建议使用 --depth 减少历史）
git clone --depth 1 https://gitlab.com/gitlab-org/gitlab.git
cd gitlab

# 安装依赖（仅用于代码索引，不需要完整运行）
# bundle install --without production test

# 生成一级目录结构
ls -1d */ 2>/dev/null
```

**目录结构逐层解读**：

```
gitlab/                           # 根目录 = Rails 项目根
├── app/                          # ⭐ Rails MVC 代码（自动加载）
│   ├── controllers/              # 控制器：处理 HTTP 请求 → 调用 Service
│   ├── models/                   # 模型：ActiveRecord 数据库映射 + 关联 + 校验
│   ├── services/                 # ⭐ 核心业务逻辑（500+ Service 类）
│   ├── finders/                  # 查询构建器（替代复杂的 ActiveRecord scope）
│   ├── workers/                  # Sidekiq 异步任务定义
│   ├── policies/                 # 权限策略（DeclarativePolicy 框架）
│   ├── graphql/                  # GraphQL API 定义
│   ├── views/                    # 视图模板（Haml/Slim）
│   ├── helpers/                  # 视图辅助方法
│   └── validators/               # 自定义校验器
│
├── lib/                          # ⭐ Ruby 通用库（独立于 Rails）
│   ├── gitlab/                   # GitLab 核心模块
│   │   ├── git/                  # Git 操作封装（通过 gRPC 调用 Gitaly）
│   │   ├── ci/                   # CI/CD 配置解析和模板
│   │   ├── auth/                 # 认证授权逻辑
│   │   ├── metrics/              # Prometheus 指标导出
│   │   ├── middleware/           # Rack 中间件
│   │   ├── health_checks/        # 健康检查
│   │   └── database/             # 数据库工具（连接池、迁移辅助）
│   ├── api/                      # REST API 定义（Grape 框架）
│   ├── bitbucket/                # Bitbucket 导入
│   └── github/                   # GitHub 导入
│
├── ee/                           # ⭐ 企业版扩展（代码叠加模式）
│   ├── app/                      # EE 专有的 MVC 代码
│   │   ├── models/ee/            # EE 的 Model 扩展
│   │   └── services/ee/          # EE 的 Service 扩展
│   └── lib/                      # EE 专有的库
│
├── config/                       # 配置与初始化
│   ├── routes.rb                 # ⭐ 路由定义（1000+ 行，小心修改）
│   ├── initializers/             # Rails 初始化器（按文件名排序执行）
│   ├── environments/             # 各环境配置（dev/test/prod）
│   ├── application.rb            # Rails 应用配置
│   └── gitlab.yml.example        # GitLab 默认配置模板
│
├── db/                           # 数据库
│   ├── migrate/                  # 迁移文件（3000+ 个，按时间戳命名）
│   ├── schema.rb                 # 数据库结构快照
│   └── post_migrate/             # 部署后迁移（大表操作）
│
├── spec/                         # 测试（RSpec，50000+ 测试用例）
├── doc/                          # 开发文档
├── vendor/                       # 第三方资产（JS/CSS/Font）
├── Gemfile                       # Ruby 依赖声明（200+ gems）
├── Gemfile.lock                  # 依赖锁定
├── Rakefile                      # Rake 任务入口
└── config.ru                     # ⭐ Rack 入口（Puma 启动点）
```

#### 步骤2：追踪一次完整的 HTTP 请求

**目标**：以 `GET /api/v4/projects` 为例，追踪请求经过的所有代码层，并标注关键文件和行号。

```
🖥 用户请求: GET http://gitlab.local/api/v4/projects?private_token=xxx

┌─ 第1站：Nginx 反向代理 ─────────────────────────────────────────┐
│ 文件: /var/opt/gitlab/nginx/conf/gitlab-http.conf               │
│ 逻辑: proxy_pass http://gitlab-workhorse;                        │
└──────────────────────────────────────────────────────────────────┘
                              ↓
┌─ 第2站：GitLab Workhorse（Go 代理）───────────────────────────────┐
│ 文件: internal/upstream/upstream.go (Go 源码)                    │
│ 逻辑: 判断请求类型                                                │
│   → API 请求 → 直接代理给 Rails                                   │
│   → Git push/pull → Workhorse 预处理后代理给 Gitaly              │
└──────────────────────────────────────────────────────────────────┘
                              ↓
┌─ 第3站：Rack Middleware 链 ───────────────────────────────────────┐
│ 文件: config/application.rb (~20 个中间件按顺序注册)              │
│                                                                 │
│ 1. Gitlab::Middleware::Multipart (文件上传边界处理)              │
│ 2. Gitlab::Middleware::RequestContext (注入请求上下文)           │
│ 3. Rack::Attack (限流——检查是否触发限速规则)                     │
│ 4. Warden (用户认证——验证 private_token)                         │
│    → Warden 调用 Gitlab::Auth.find_for_git_client               │
│    → 查询 User 表确认 token 有效                                  │
│    → 设置 current_user                                          │
│ 5. Gitlab::Middleware::SameSiteCookies                           │
│ 6. Rails Router → 开始匹配路由                                   │
└──────────────────────────────────────────────────────────────────┘
                              ↓
┌─ 第4站：路由匹配 ─────────────────────────────────────────────────┐
│ 文件: config/routes.rb (第 450 行附近)                            │
│                                                                 │
│ namespace :api do                                                │
│   namespace :v4 do                                               │
│     resources :projects, only: [:index] do                       │
│       # GET /api/v4/projects → Api::V4::ProjectsController#index │
└──────────────────────────────────────────────────────────────────┘
                              ↓
┌─ 第5站：Controller ──────────────────────────────────────────────┐
│ 文件: lib/api/projects.rb (GitLab 的 API 用 Grape，非 Rails 控制器)│
│                                                                 │
│ class Projects < Grape::API::Instance                            │
│   get do                                                         │
│     # 参数解析 + 权限检查                                         │
│     authenticate!                                                │
│     projects = ProjectsFinder.new(current_user).execute          │
│     present projects, with: Entities::Project                   │
│   end                                                            │
│ end                                                              │
└──────────────────────────────────────────────────────────────────┘
                              ↓
┌─ 第6站：Finder 层 ────────────────────────────────────────────────┐
│ 文件: app/finders/projects_finder.rb                             │
│                                                                 │
│ class ProjectsFinder < UnionFinder                               │
│   def execute                                                   │
│     # 根据用户权限 + 查询参数构建 SQL                             │
│     items = Project.without_deleted                              │
│     items = filter_by_visibility(items)                          │
│     items = filter_by_search(items)                              │
│     items.includes(:namespace).order(updated_at: :desc)          │
│   end                                                            │
│ end                                                              │
└──────────────────────────────────────────────────────────────────┘
                              ↓
┌─ 第7站：Model 层 ─────────────────────────────────────────────────┐
│ 文件: app/models/project.rb (2900+ 行)                            │
│                                                                 │
│ class Project < ApplicationRecord                                │
│   belongs_to :namespace                                          │
│   has_many :issues                                               │
│   scope :without_deleted, -> { where(pending_delete: false) }   │
│   scope :public_only, -> { where(visibility_level: PUBLIC) }    │
│ end                                                              │
└──────────────────────────────────────────────────────────────────┘
                              ↓
┌─ 第8站：数据库查询 ──────────────────────────────────────────────┐
│ SQL: SELECT * FROM projects                                      │
│      WHERE pending_delete = false                                │
│        AND visibility_level IN (20, 10)                          │
│      ORDER BY updated_at DESC                                    │
│      LIMIT 20                                                    │
└──────────────────────────────────────────────────────────────────┘
                              ↓
┌─ 第9站：响应返回 ─────────────────────────────────────────────────┐
│ 文件: lib/api/entities/project.rb                                │
│ → 将 Project 对象序列化为 JSON                                   │
│ → Grape::Formatter::Json → HTTP Response                        │
│ → Workhorse → Nginx → 客户端                                     │
└──────────────────────────────────────────────────────────────────┘
```

#### 步骤3：EE 代码叠加机制实战演示

**目标**：亲手写一个 EE 叠加示例，理解 prepend 的工作原理。

```ruby
# ===== 第一步：创建 CE 版本的基础类 =====
# ce_app/models/calculator.rb
module CeApp
  class Calculator
    def add(a, b)
      a + b
    end

    def multiply(a, b)
      a * b
    end

    def description
      "CE Calculator: basic arithmetic"
    end
  end
end

# ===== 第二步：创建 EE 版本的扩展模块 =====
# ee_app/models/ee/calculator.rb
module EeApp
  module Calculator
    extend ActiveSupport::Concern

    prepended do
      # 添加新方法（CE 中没有）
      def exponent(a, b)
        a ** b
      end

      # 注册新的 scope 或关联
      scope :advanced, -> { where(type: 'advanced') }
    end

    # 覆盖 CE 的 description 方法
    def description
      "#{super} + EE: advanced math functions"
    end

    # 覆盖 multiply 方法——添加日志
    def multiply(a, b)
      result = super  # 调用 CE 的 multiply
      puts "[EE] #{a} * #{b} = #{result}"
      result
    end
  end
end

# ===== 第三步：运行时叠加 =====
# config/initializers/ee_prepend.rb
# GitLab 在 config/application.rb 中自动加载 EE 模块

require 'ce_app/models/calculator'
require 'ee_app/models/ee/calculator'

# prepend 将 EE::Calculator 插入到 Calculator 的继承链前面
CeApp::Calculator.prepend(EeApp::Calculator)

# ===== 第四步：验证 =====
calc = CeApp::Calculator.new

# CE 原有方法
puts calc.add(2, 3)        # → 5

# EE 新增方法
puts calc.exponent(2, 3)   # → 8  (CE 中没有这个方法！)

# EE 覆盖的方法
puts calc.description      # → "CE Calculator: basic arithmetic + EE: advanced math functions"
calc.multiply(3, 4)        # → 输出 [EE] 3 * 4 = 12，返回 12
```

#### 步骤4：在 Omnibus 环境中调试 GitLab 源码

**目标**：在生产级的 Omnibus 部署中添加调试日志，观察源码执行情况。

```bash
# 1. 进入 Omnibus 的 GitLab Rails 目录
cd /opt/gitlab/embedded/service/gitlab-rails

# 2. 备份要修改的文件
sudo cp app/finders/projects_finder.rb app/finders/projects_finder.rb.bak

# 3. 添加调试日志
sudo vi app/finders/projects_finder.rb
# 在 execute 方法第一行加上：
# Rails.logger.debug("[DEBUG] ProjectsFinder called by user #{current_user&.id}, params: #{params.inspect}")

# 4. 重启 Puma 加载修改
sudo gitlab-ctl restart puma

# 5. 查看日志输出
sudo gitlab-ctl tail puma | grep "\[DEBUG\]"

# 6. 调试完成后恢复原文件
sudo cp app/finders/projects_finder.rb.bak app/finders/projects_finder.rb
sudo gitlab-ctl restart puma
```

### 完整代码清单

- GitLab 源码目录树（步骤1）
- HTTP 请求全链路追踪图（步骤2）
- EE 代码叠加演示脚本（步骤3）
- Omnibus 调试命令集（步骤4）

### 测试验证

```bash
# 验证1：目录结构确认
cd /path/to/gitlab-source
test -f config.ru && echo "✅ config.ru found"
test -d app/services && echo "✅ app/services found"
test -d ee/app && echo "✅ ee/app found"
test -f config/routes.rb && echo "✅ routes.rb found"

# 验证2：EE prepend 机制
cd /tmp && mkdir ce-test && cd ce-test
# 复制步骤3中的 Ruby 代码到文件
ruby -e "
  module CeApp; class Calculator; def add(a,b); a+b; end; end; end
  module EeApp; module Calculator; def multiply(a,b); puts 'EE multiply'; super; end; end; end
  CeApp::Calculator.prepend(EeApp::Calculator)
  calc = CeApp::Calculator.new
  puts calc.add(2,3)
  calc.multiply(2,3)
"
# 应输出: 5\nEE multiply

# 验证3：Omnibus 源码调试
sudo gitlab-rails runner "
  puts Gitlab::VERSION
  puts Rails.root
  puts Project.count
"
# 确认 Rails console 可用
```

## 4. 项目总结

### 源码定位速查表

| 你要找什么 | 第一站去哪 | 关键文件 |
|-----------|-----------|---------|
| API 接口实现 | `lib/api/` | `lib/api/projects.rb` 等 |
| Web 页面逻辑 | `app/controllers/` → `app/services/` | `ProjectsController` → `CreateService` |
| CI/CD 处理 | `app/services/ci/` + `app/models/ci/` | `CreatePipelineService` |
| MR 逻辑 | `app/services/merge_requests/` | `MergeService`, `ApprovalService` |
| Git 操作 | `lib/gitlab/git/`（Rails gRPC 客户端） | `lib/gitlab/git/repository.rb` |
| Gitaly RPC 定义 | `gitaly/proto/`（Go 仓库） | `repository.proto` |
| 权限检查 | `app/policies/` | `app/policies/project_policy.rb` |
| Sidekiq 异步 | `app/workers/` | `app/workers/process_commit_worker.rb` |
| 数据库迁移 | `db/migrate/`（按时间戳查） | 如 `20240101000000_add_index.rb` |
| 路由定义 | `config/routes.rb` | 1000+ 行，按 namespace 搜索 |

### 适用场景

- **排 Bug**：知道 Bug 的 API 端点或页面 URL → 顺藤摸瓜找到对应源码
- **加调试日志**：知道数据流经的代码层 → 在对应位置加 `Rails.logger.debug`
- **自定义扩展**：理解 EE 叠加机制 → 写自己的扩展模块
- **性能分析**：理解请求链路 → 定位瓶颈在哪个组件（Rails/Gitaly/DB）

**不适用场景**：
- 单纯使用 GitLab 不需要读源码（API + Web UI 已够用）
- 快速修复可以先搜社区 issue，不一定非要自己读源码

### 注意事项

- **GitLab 源码有 50 万+ 行**：不要试图"读完"，按需查看，按功能定位
- **EE 代码覆盖 CE 代码时**：如果 CE 方法被 EE 的 `prepend` 覆盖，看 CE 代码时要注意调用链里有 `super`
- **API 层用的是 Grape 框架**，不是标准 Rails Controller——`lib/api/` 下的文件结构对新手来说可能有点陌生
- **修改 Omnibus 部署的源码后要重启 Puma**：`sudo gitlab-ctl restart puma`，否则修改不生效

### 常见踩坑经验

1. **在 `app/controllers/` 中找不到 API**：因为 GitLab 的 API 用的是 Grape 框架，代码在 `lib/api/` 下。根因：用传统 Rails 思维找控制器。解决：API 路由看 `lib/api/api.rb`，具体 API 在 `lib/api/*.rb`。
2. **在 `app/services/` 找到类但不知道谁调用了它**：可以全局搜索类名。根因：Service 类不遵循 Rails 的路由→控制器链条。解决：用 `grep -r "MergeService" app/ lib/` 或 IDE 的 Find Usages。
3. **修改了源码但没生效**：Omnibus 的 Rails 代码在 `/opt/gitlab/embedded/service/gitlab-rails/`，不是 `/home/git/gitlab/`。根因：不熟悉 Omnibus 的目录布局。解决：`find /opt/gitlab -name "gitlab-rails" -type d` 确认实际路径。

### 思考题

1. GitLab 的 `app/services/` 中有 500+ 个 Service 类，它们的命名有什么规律？如何根据"创建一个 Issue"这个需求快速定位到对应的 Service 类？
2. EE 的 `prepend` 模式允许 EE 在不修改 CE 代码的前提下覆盖 CE 的方法。如果 EE 覆盖了 CE 方法后又想调用 CE 的原始逻辑（类似 `super`），调用链是什么？这个设计有什么潜在的陷阱（比如两个 EE 模块同时 prepend 同一个类时）？

> 答案见附录 D。

### 推广计划提示

- **开发**：本章是阅读 GitLab 源码的"导航手册"，建议收藏源码定位速查表
- **架构师**：理解 EE 代码叠加模式后，可以设计类似的插件化架构
- **运维**：学会在 Omnibus 部署中加调试日志后，排查问题时不必等开发给日志
