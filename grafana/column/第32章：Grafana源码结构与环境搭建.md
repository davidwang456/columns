# 第32章：Grafana源码结构与环境搭建

## 1. 项目背景

"公司需要自定义一个Grafana面板来展示机房拓扑图，但官方没有这个面板类型。方案是开发一个自定义插件，但看着Grafana的GitHub仓库——4000+文件，Go+TypeScript+React混在一起，完全不知道从哪里下手。"

资深前端工程师老K面临"从使用者到贡献者"的跨越。Grafana是一个大型开源项目（Star 60K+），采用前后端分离的单体仓库（Monorepo）结构。本章作为高级篇开篇，将深入Grafana源码仓库的架构设计、核心目录、编译调试方法，为后续的插件开发和源码剖析打好基础。

## 2. 项目设计

**小胖**（看着VSCode里打开的Grafana仓库发呆）：大师，我clone了Grafana源码，想看看它怎么工作的。光根目录就有50多个文件夹，我该从哪里看起？

**大师**：Grafana的源码是一个典型的Go+React Monorepo。理解它的核心在于三个"入口"：

**后端入口**：`pkg/cmd/grafana-server/main.go` → 这是Grafana Server的起点。从这里开始，你会看到依赖注入（Google Wire）、服务初始化、HTTP API注册。

**前端入口**：`public/app/app.tsx` → 这是React应用的根组件。从这里展开，会看到路由定义、Redux store、页面组件树。

**插件入口**：`packages/grafana-data/`、`packages/grafana-ui/`、`packages/grafana-runtime/` → 这是插件开发SDK，外部插件依赖的核心库。

**小白**：目录结构的具体职责呢？

**大师**：核心目录职责：

| 目录 | 作用 |
|------|------|
| `pkg/api/` | HTTP API路由和处理函数 |
| `pkg/services/` | 业务逻辑层（如alerting、dashboard、datasource） |
| `pkg/models/` | 数据模型定义 |
| `pkg/tsdb/` | 数据源实现（Prometheus/MySQL/ES等） |
| `pkg/plugins/` | 插件系统管理 |
| `pkg/middleware/` | HTTP中间件（认证/CORS/日志） |
| `pkg/infra/` | 基础设施（数据库/日志/缓存） |
| `public/app/` | 前端页面和路由 |
| `public/app/plugins/` | 内置Panel和数据源插件 |
| `packages/` | 发布到NPM的SDK包 |
| `devenv/` | Docker Compose本地开发环境 |
| `conf/` | 默认配置文件示例 |
| `emails/` | 邮件模板 |
| `scripts/` | 构建/测试/发布脚本 |

其中`pkg/`包含约80%的后端代码，`public/`包含约80%的前端代码。

**小胖**：编译环境怎么搭？Go和Node.js版本有要求吗？

**大师**：Grafana开发环境需要：

- **Go 1.22+**：后端编译。依赖管理用Go Modules。
- **Node.js 20+** + **Yarn**：前端编译。Monorepo管理用Turborepo。
- **Docker**：本地开发时用Docker Compose启动MySQL/PostgreSQL/Prometheus等依赖。

**关键编译命令**：
```bash
# 后端编译
make build-go          # 编译Go服务
make run               # 运行开发模式

# 前端编译  
yarn install           # 安装依赖
yarn start             # 启动前端开发服务器

# 全栈开发
make devenv            # 启动Docker开发环境
```

**热重载**：Grafana支持air热重载（Go后端）和Webpack HMR（React前端）。修改代码后无需重启，自动生效。

**技术映射**：Monorepo = 大型商场（不同楼层卖不同东西但统一管理），`pkg/` = 后台仓库（Go后端逻辑），`public/` = 前台店面（React前端UI），`packages/` = 自有品牌商品（给外部用的SDK）。

## 3. 项目实战

**步骤一：克隆并搭建开发环境**

```bash
git clone https://github.com/grafana/grafana.git
cd grafana
git checkout v11.0.0

# 安装Go依赖
go mod download

# 安装前端依赖
yarn install

# 启动本地开发环境
make devenv sources=postgres,prometheus
```

这会启动Docker Compose开发环境（PostgreSQL + Prometheus），Grafana以开发模式运行。

**步骤二：理解Wire依赖注入**

Grafana使用Google Wire做编译时依赖注入。`wire.go`文件定义了各个服务的创建顺序和依赖关系。

查看入口文件 `pkg/server/wire.go`：
```go
// 简化示例
func Initialize() (*Server, error) {
    wire.Build(
        ProvideDB,
        ProvideHTTPServer,
        ProvideAPIRouter,
        ProvideAlertingService,
        ProvideDashboardService,
        // ...更多服务
    )
    return &Server{}, nil
}
```

Wire在编译时自动生成`wire_gen.go`，解析依赖关系并生成初始化代码。Grafana启动时执行这些生成的代码来创建服务实例。

**步骤三：追踪API请求**

从HTTP请求追踪到业务处理的完整链路：

```
HTTP GET /api/dashboards/uid/:uid
  → pkg/api/api.go: registerRoutes()
    → pkg/api/dashboard.go: GetDashboard()
      → pkg/services/dashboards/dashboard.go: GetDashboard()
        → pkg/services/store/entity.go: GetEntity()
          → 数据库查询
```

调试方法：在`pkg/api/dashboard.go`中添加日志：
```go
func (hs *HTTPServer) GetDashboard(c *models.ReqContext) {
    hs.log.Info("Dashboard fetched", "uid", web.Params(c.Req)[":uid"])
    // 原有逻辑...
}
```

**步骤四：前端路由与组件调试**

前端路由定义在`public/app/routes/routes.tsx`：
```typescript
// 简化示例
{
  path: '/dashboards',
  component: DashboardListPage,
}
{
  path: '/d/:uid/:slug?',
  component: DashboardPage,
}
```

修改前端代码验证热更新：
```bash
yarn start  # 启动前端开发服务器，访问 http://localhost:3001
```

修改`public/app/features/dashboard/components/DashboardRow.tsx`，保存后浏览器自动刷新。

**步骤五：数据源插件开发环境**
```bash
# 使用Grafana官方脚手架创建插件
npx @grafana/create-plugin@latest

# 选择插件类型：datasource
# 输入插件名称
# 自动生成项目结构

# 启动开发模式
cd my-datasource-plugin
yarn dev
```

这会启动一个开发服务器，自动将插件注册到本地Grafana实例。

**步骤六：调试配置**

VSCode调试配置（`.vscode/launch.json`）：
```json
{
  "version": "0.2.0",
  "configurations": [
    {
      "name": "Grafana Backend",
      "type": "go",
      "request": "launch",
      "mode": "debug",
      "program": "${workspaceFolder}/pkg/cmd/grafana-server",
      "args": ["--homepath=${workspaceFolder}", "--packaging=dev"],
      "env": {
        "GF_DATABASE_TYPE": "postgres",
        "GF_DATABASE_URL": "postgres://grafana:grafana@localhost:5432/grafana"
      }
    },
    {
      "name": "Grafana Frontend",
      "type": "chrome",
      "request": "launch",
      "url": "http://localhost:3001",
      "webRoot": "${workspaceFolder}/public"
    }
  ]
}
```

**常见坑点**
1. **Go版本不匹配**：Grafana v11需要Go 1.22+。用`go version`确认。
2. **前端依赖安装失败**：Node.js版本必须是20.x LTS，Yarn版本必须≥4.x。
3. **Wire代码生成错误**：修改`pkg/services/`后，需要运行`make gen-go`重新生成wire代码。
4. **本地开发时CORS报错**：Grafana前端默认连localhost:3000的后端。如果端口不同需要配置proxy。

**步骤七：实战——编译自定义修改并验证**

直接修改Grafana源码并编译部署：

```bash
# 1. 修改后端：在启动信息中添加自定义标识
vim pkg/cmd/grafana-server/main.go
# 在 main() 函数中添加：
# log.Infof("=== 自定义版本 - MyCompany Edition ===")

# 2. 编译后端
make build-go
# 或：go build -o bin/grafana-server ./pkg/cmd/grafana-server

# 3. 修改前端：在登录页添加公司标识
vim public/app/features/login/LoginPage.tsx
# 在登录表单上方添加：
# <div className="company-branding">
#   <h2>MyCompany 可观测性平台</h2>
# </div>

# 4. 编译前端
yarn build

# 5. 运行自定义版本
./bin/grafana-server --homepath=$(pwd) --config=conf/custom.ini
```

自定义配置：
```ini
# conf/custom.ini
[server]
http_port = 3000
domain = localhost

[database]
type = sqlite3
path = data/grafana.db

[security]
admin_user = admin
admin_password = admin

[log]
level = debug
mode = console file
```

验证修改效果：
```bash
# 检查后端日志中是否包含自定义标识
curl http://localhost:3000/api/health
# 观察启动日志
tail -f data/log/grafana.log | grep "自定义版本"

# 检查前端登录页是否显示公司标识
open http://localhost:3000/login  # 查看页面源代码
```

**追踪代码修改影响**：

使用`grep`在源码中搜索一个关键词（如"dashboard"），理清引用关系：
```bash
# 找出所有引用了Dashboard.Save()的代码
rg "\.Save\(" pkg/services/dashboards/ --type go

# 找出RouteRegister的调用位置（理解API路由结构）
rg "RouteRegister\." pkg/api/ --type go | head -20

# 查找所有调用grafana_data包的前端文件
rg "from '@grafana/data'" public/app/ --type tsx | head -10
```

**源码项目结构快速导航命令**：
```bash
# 统计代码量
cloc pkg/ public/ --exclude-dir=node_modules

# 查找最大的文件（可能是核心模块）
find pkg/ -name "*.go" -exec wc -l {} + | sort -rn | head -10

# 查看最近的commits了解当前开发重点
git log --oneline -20
```

**Grafana源码关键模块**

| 模块 | 语言 | 行数(约) | 职责 |
|------|------|---------|------|
| pkg/api | Go | ~20000 | HTTP API路由和处理 |
| pkg/services | Go | ~50000 | 业务逻辑 |
| pkg/tsdb | Go | ~30000 | 数据源实现 |
| public/app | TypeScript | ~200000 | 前端页面和面板 |
| packages | TypeScript | ~50000 | SDK包 |

**阅读路线建议**
1. `pkg/cmd/grafana-server/main.go` → 启动入口
2. `pkg/server/server.go` → 服务器初始化
3. `pkg/api/api.go` → API路由注册
4. `pkg/services/`各子目录 → 具体业务
5. `public/app/app.tsx` → 前端入口

**思考题**
1. Grafana的架构是前后端分离的单体仓库。如果要把一个模块（如Alerting）拆成独立微服务，需要修改哪些层的代码？
2. Wire依赖注入和手动`NewXXX()`创建依赖有什么区别？为什么Grafana选择Wire？

**源码导航速查——常用入口函数速查表**

| 你想做什么 | 从哪里开始看 |
|-----------|-----------|
| 了解启动流程 | `pkg/cmd/grafana-server/main.go` → `pkg/server/server.go` |
| 添加新API | `pkg/api/api.go` → 找到现有类似Handler → 模仿添加 |
| 修改Dashboard逻辑 | `pkg/services/dashboards/` → `pkg/api/dashboard.go` |
| 理解数据库操作 | `pkg/services/sqlstore/` → 找对应的store文件 |
| 修改前端页面 | `public/app/features/` → 找到对应feature |
| 修改框架组件 | `packages/grafana-ui/src/components/` |
| 理解插件系统 | `pkg/plugins/` → `public/app/features/plugins/` |
| 理解数据源查询 | `pkg/tsdb/` → 找到对应的数据源目录 |
| 理解告警引擎 | `pkg/services/ngalert/` |
| 理解认证流程 | `pkg/services/auth/` → `pkg/middleware/auth.go` |

**编译命令速查**：
```bash
make build-go          # 编译Go后端（生产模式）
make build-server      # 编译并启动（开发模式）
make run               # 运行Grafana开发服务器
make test-go           # 运行Go单元测试
make lint-go           # Go代码规范检查
make gen-go            # 重新生成Wire代码

yarn start             # 启动前端开发服务器（HMR热更新）
yarn build             # 编译前端（生产模式）
yarn test              # 运行前端单元测试
yarn lint              # 前端代码规范检查

make devenv            # 启动Docker开发环境（PostgreSQL+Prometheus等）
```
