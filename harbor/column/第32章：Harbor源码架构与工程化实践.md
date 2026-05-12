# 第32章：Harbor 源码架构与工程化实践

## 1 项目背景

某公司决定深度参与Harbor开源社区贡献，同时为内部业务需求开发自定义扩展功能（如自研的审批流引擎、定制化的审计报告、内部LDAP/SSO深度集成）。然而，团队面对Harbor庞大的Go语言代码库（50万+行源码、2000+源文件、30+子目录），完全不知从何下手。

**痛点一：Go Module依赖地狱。** Harbor的`go.mod`文件接近2000行，声明了200+个直接依赖和大量间接依赖。团队第一次执行`go build ./...`时，遇到了经典的Go依赖冲突：某个依赖库的`v1.2.0`版本需要Go 1.21的`cmp`标准库包，而另一个依赖锁定了Go 1.20的兼容性。更糟的是，Harbor使用了自己的Makefile封装了编译参数——LDFLAGS注入版本号、构建时间、Git Commit等信息——直接`go build`几乎无法通过。团队花了两天时间才在本地成功编译Core组件。

**痛点二：不知道核心调用路径。** Harbor的`src/`目录下包含30+个子目录，源文件超过2000个。团队想实现一个看似简单的需求——在"删除镜像"操作前增加一个自定义审批检查——但完全不知道应该从哪个文件开始追踪。是`src/server/v2.0/handler/`？还是`src/controller/artifact/`？还是`src/dao/`？MVC三层在Harbor中如何映射？Controller和Handler的分工边界在哪里？这导致了大量的无效代码浏览——在数千个文件中"找"比"改"花的时间多10倍。

**痛点三：测试体系庞大难以驾驭。** Harbor的测试分为四个层级：单元测试（UT）、集成测试（IT）、API测试（Swagger-based）、端到端测试（e2e）。本地执行`make test`——包含了UT和IT——花了超过2小时仍未完成。团队只想跑与修改相关的测试，但不知道如何只跑"Artifact Controller"的单元测试。更糟的是，某些测试依赖了外部服务（PostgreSQL、Redis、MinIO），本地不启动这些服务时测试会直接panic。

**痛点四：贡献流程不熟悉，PR被秒关。** 团队兴奋地提交了第一个PR——修复了一个日志格式的小Bug。结果PR在1小时内被Harbor Maintainer关闭，原因有三：缺少DCO（Developer Certificate of Origin）签名、没有关联Issue、Commit Message格式不符合规范（需要`feat:`/`fix:`/`chore:`前缀）。这些看似简单的流程问题，让团队的第一次开源贡献体验极其受挫。

本章将系统梳理Harbor源码的目录结构、核心模块职责、分层架构、构建体系、测试执行策略和社区贡献的完整流程，帮助读者建立"拿到Harbor源码就知道从哪里开始改"的能力。

---

## 2 项目设计——剧本式交锋对话

**场景：公司内部培训室，架构师老陈给准备做Harbor源码贡献的开发团队做入门培训。投屏上展示着Harbor的源码目录树。**

**小胖**（全栈开发，实用主义）："50万行Go代码——我从哪看起？老陈你能不能给我一条'最小阅读路径'——只看2000行代码就能理解Harbor的核心架构？我不需要知道每个细节，但得知道'一个HTTP请求从Nginx到PostgreSQL的完整链路'。"

**老陈**（架构师，Harbor Contributor）："好。Harbor的'最小阅读路径'聚焦在四块——按阅读顺序："

**起点：`src/cmd/core/main.go`（约100行）**

"这是Harbor Core进程的入口——所有旅程的起点。`main()`函数做的事情极其清晰：初始化配置（从harbor.yml和环境变量）→ 初始化数据库连接池（PostgreSQL）→ 初始化Redis连接 → 注册所有HTTP路由 → 启动HTTP Server（默认8080端口）→ 阻塞等待信号。

你读完这100行代码，就知道Harbor Core在启动时做了哪些初始化——以后每次调试'为什么某个功能不工作'时，回来检查对应的初始化步骤。"

**核心一：`src/server/v2.0/router.go`（路由注册，约200行）**

"所有REST API的路由注册都在这里——这是Harbor的URL地图。你在这里能看到每个API路径映射到哪个Handler。例如：
- `GET /api/v2.0/projects` → `handler.ProjectAPI{}.List()`
- `DELETE /api/v2.0/projects/{id}/repositories/{name}/artifacts/{ref}` → `handler.RepositoryAPI{}.DeleteArtifact()`

追踪任何API请求的第一步，就是在这个文件中找到路由定义。"

**核心二：`src/pkg/`（公共库——Harbor的心肺）**

```
pkg/
├── auth/         # 认证框架（支持DB/LDAP/OIDC/UAA）
├── token/        # JWT Token签发与验证（Registry的Bearer Token）
├── scan/         # 扫描器适配器接口（三层：v1/api/spec）
├── replication/  # 镜像复制引擎（推/拉/事件驱动）
├── robot/        # 机器人账户管理
├── permission/   # RBAC权限评估引擎
└── config/       # 配置管理（harbor.yml解析）
```

"`pkg/`目录下的代码是Harbor中复用度最高的部分——Core、JobService、Registryctl等多个组件都在引用这些包。理解`pkg/`的设计，就理解了Harbor的核心抽象。"

**核心三：`src/controller/`（业务逻辑层——MVC中的C）**

```
controller/
├── project/      # 项目CRUD + 配额管理 + CVE白名单
├── artifact/     # 制品管理（镜像/Helm Chart/OCI Artifact）
├── repository/   # 仓库管理（Tag操作、复制触发）
├── replication/  # 复制策略的执行和状态管理
├── scanner/      # 扫描器注册和生命周期管理
└── tag/          # Tag保留策略和不可变Tag
```

"Controller是Harbor中业务逻辑最密集的地方。每个Controller实现了对应的业务规则——例如`artifact.Controller.Delete()`会检查：制品是否被P2P预热任务引用？是否有不可变Tag？是否在复制策略中？——全部通过后才调用DAO层删除。"

**终点：追踪一个API请求的完整链路**

"以`GET /api/v2.0/projects`为例——这是最经典的只读请求：

1. `router.go:93` → 匹配路由，调用 `handler.ProjectAPI{}.List()`
2. `handler/project.go` → 解析HTTP Query参数（page/page_size/name），构建Query对象
3. `controller/project/controller.go` → 执行业务逻辑：过滤deleted项目、RBAC权限过滤（只返回当前用户有权限看到的项目）
4. `dao/pgsql/project.go` → 执行SQL：`SELECT * FROM project WHERE deleted=false AND project_id IN (...) ORDER BY name`
5. `models/project.go` → 数据库模型的ORM映射（GORM/Beego ORM）

每一步都是清晰的单向依赖——上层依赖下层，下层不知道上层的存在。"

**技术映射**：Harbor采用分层架构——`handler（HTTP层）→ controller（业务逻辑层）→ dao（数据访问层）→ models（数据库模型）`。这种设计模式在Go Web开发中很常见，但Harbor的实现有自己特色——Controller和DAO之间没有使用Repository模式（DAO本身就是Repository），且Controller直接返回models对象给Handler（不使用DTO）。

**小白**（高级开发，关注工程化细节）："`make build`和直接`go build ./...`的区别到底是什么？为什么我直接go build总是报错？Makefile里藏了什么魔法？"

**老陈**："这个问题问得好。Harbor的Makefile封装了大量**构建前置步骤**——直接go build相当于跳过了所有准备工作。让我们看看`make build`实际上做了什么："

```
make build 的执行流程：

1. make install
   ├── 安装swagger (go install github.com/go-swagger/go-swagger)
   ├── 安装golangci-lint (lint工具)
   ├── 安装mockgen (测试mock生成工具)
   ├── 安装protoc + protoc-gen-go (gRPC代码生成)
   └── 安装其他Go工具链

2. make swagger
   └── 从api/swagger.yaml生成Go代码（models + handlers stub）

3. make compile_core
   ├── 设置LDFLAGS注入版本信息：
   │   -X github.com/goharbor/harbor/src/pkg/version.Version=v2.12.0
   │   -X github.com/goharbor/harbor/src/pkg/version.GitCommit=abc123
   │   -X github.com/goharbor/harbor/src/pkg/version.BuildTime=2024-01-16_10:30
   ├── cd src && go build -ldflags="..." -o ../make/harbor_core ./core
   └── 编译结果：make/harbor_core

4. make compile_jobservice
   └── 同上，编译JobService组件

5. make compile_registryctl
   └── 同上，编译Registryctl组件

6. make build_ui
   ├── cd src/portal
   ├── npm install (Angular前端依赖)
   ├── npm run build (Angular编译)
   └── 编译结果嵌入到 make/ 目录
```

"所以直接`go build ./...`会失败，因为：
- Swagger生成的代码还未生成（`api/v2.0/`下的models和handlers stub不存在）
- 版本信息未被注入（某些代码依赖version包的变量）
- 依赖工具链版本不匹配（protoc/gen版本需要完全一致）

如果你只改后端Go代码，最快的方式是：`make install && make swagger && make compile_core` —— 跳过前端编译和完整构建。"

**小胖**："那我改了一个Controller的方法，怎么只跑相关的单元测试？`make test`跑全部要2小时啊。"

**老陈**："测试也是有分层执行策略的。Harbor的测试分为四层：

- **单元测试（UT）**：`make ut` —— 只跑Go的单元测试（不含集成测试），约10分钟。只测试纯逻辑，不连数据库。
- **集成测试（IT）**：需要启动PostgreSQL/Redis等依赖，约30分钟。
- **API测试**：基于Swagger定义自动生成的API测试，约20分钟。
- **端到端测试（e2e）**：完整部署Harbor后执行的测试，约60分钟。

只跑你修改的单包测试：

```bash
# 只跑 artifact controller 的单元测试
cd src && go test ./controller/artifact/... -v -count=1

# 只跑 artifact dao 的PostgreSQL集成测试
cd src && go test ./dao/pgsql/ -run TestArtifact -v

# 跑指定测试函数
go test ./controller/artifact/ -run TestController_Delete -v
```

关键是——确保你的UT不依赖外部服务。Harbor的UT使用mockgen生成mock对象（MockDAO、MockClient等），测试时不连接真实数据库。"

**小白**："Harbor的ORM层我看到用了GORM和Beego ORM混用——这是历史遗留问题吗？如果要迁移数据库从PostgreSQL到MySQL，需要改哪些东西？"

**老陈**："Harbor从v2.0开始从Beego ORM迁移到GORM，但迁移尚未完成——所以你现在会看到两者混用。新代码应该使用GORM。至于数据库迁移——Harbor目前**只支持PostgreSQL**，迁移到MySQL需要改的东西远超ORM层：

1. SQL方言差异（PostgreSQL的`$1` vs MySQL的`?`占位符）
2. 迁移脚本（`make/migrations/` 目录下的SQL迁移脚本全为PostgreSQL语法）
3. 特性依赖（JSONB字段、数组类型、WITH RECURSIVE查询、`RETURNING`子句）
4. 驱动层（`dao/pgsql/`目录下的所有代码）
5. 部署脚本（harbor.yml、docker-compose文件中的数据库配置）

所以迁移到MySQL不是一个'修改几个文件'能解决的事——Harbor社区目前也没有这个计划。"

---

## 3 项目实战

### 3.1 环境要求

| 组件 | 版本要求 | 说明 |
|------|---------|------|
| Go | 1.21.x | Harbor v2.12要求Go 1.21（`go.mod`中指定） |
| Docker | 24.0+ | 运行本地Harbor测试环境和容器化依赖 |
| Docker Compose | v2.20+ | Harbor开发环境编排 |
| Node.js | 18.x LTS | 前端Portal编译（Angular 16+） |
| npm | 9.x | 前端依赖管理 |
| PostgreSQL | 15 | 本地测试数据库（可选，UT可mock） |
| Redis | 7 | 本地测试缓存（可选，UT可mock） |
| golangci-lint | v1.55+ | 代码规范检查 |
| swagger | v0.30+ | API文档和代码生成 |
| protoc | v25+ | gRPC代码生成（JobService通信） |

### 3.2 第一步：搭建Harbor开发环境

**目标**：从零搭建Harbor开发环境，能成功编译Core组件。

```bash
# 步骤1：克隆Harbor源码
git clone https://github.com/goharbor/harbor.git
cd harbor
git checkout v2.12.0

# 查看项目结构
ls -la
# 预期看到：Makefile, src/, api/, make/, tests/, tools/

# 步骤2：安装开发工具链
make install
# 此命令会安装以下工具（需要网络）：
#   - swagger (go-swagger/go-swagger)
#   - golangci-lint
#   - mockgen (uber-go/mock)
#   - protoc + protoc-gen-go
#   - go-licenses
# 预期每个工具的安装都有日志输出，最后显示完成

# 步骤3：生成Swagger代码
make swagger
# 预期输出：
# generate all API handlers
# 在 src/server/v2.0/handler/ 和 src/server/v2.0/models/ 下生成代码

# 步骤4：编译Core组件（跳过前端）
make compile_core
# 预期输出：
# compiling binary for harbor core
# 编译产物：make/harbor_core

# 验证编译产物
file make/harbor_core
# 预期输出：
# make/harbor_core: ELF 64-bit LSB executable, x86-64, ...

./make/harbor_core version
# 预期输出：
# Harbor version: v2.12.0-abc123

# 步骤5：编译前端Portal（可选——只有改前端UI才需要）
make build_ui
# 预期输出（耗时较长）：
# npm install ...
# npm run build ...
# 前端产物生成在 src/portal/dist/

# 步骤6：编译全部组件（包括镜像构建）
make build
# ⚠️ 这需要较长时间（30-60分钟）——包含前端编译、所有服务镜像构建
```

### 3.3 第二步：理解核心目录结构

**目标**：建立Harbor源码目录结构的完整认知地图。

```
harbor/
├── Makefile                      # 构建入口（⭐ 从这里开始理解编译流程）
├── go.mod                        # Go Module依赖定义（~2000行）
├── go.sum                        # 依赖锁定文件
│
├── src/                          # 所有Go源码 ⭐
│   ├── cmd/                      # 各组件入口（main.go）
│   │   ├── core/main.go          # Harbor Core启动入口（~100行）
│   │   ├── jobservice/main.go    # JobService启动入口
│   │   └── registryctl/main.go   # Registryctl启动入口
│   │
│   ├── server/                   # HTTP层（Handler + Router）
│   │   ├── v2.0/
│   │   │   ├── router.go         # ⭐ API路由注册（核心文件，~200行）
│   │   │   ├── handler/          # 各资源的HTTP Handler
│   │   │   │   ├── project.go    # 项目API Handler
│   │   │   │   ├── artifact.go   # 制品API Handler
│   │   │   │   ├── repository.go # 仓库API Handler
│   │   │   │   └── ...
│   │   │   ├── models/           # Swagger自动生成的API模型
│   │   │   └── middleware/       # 中间件（认证/鉴权/限流/CORS）
│   │
│   ├── controller/               # 业务逻辑层 ⭐
│   │   ├── project/              # 项目业务逻辑
│   │   ├── artifact/             # 制品业务逻辑（⭐ 最核心的模块之一）
│   │   ├── repository/           # 仓库业务逻辑
│   │   ├── replication/          # 复制引擎业务逻辑
│   │   ├── scanner/              # 扫描器管理业务逻辑
│   │   ├── tag/                  # Tag管理业务逻辑
│   │   └── ...
│   │
│   ├── dao/                      # 数据访问层（PostgreSQL）
│   │   ├── pgsql/                # PostgreSQL具体实现
│   │   │   ├── project.go
│   │   │   ├── artifact.go
│   │   │   └── ...
│   │   └── dao.go                # DAO接口定义
│   │
│   ├── models/                   # 数据库模型定义（ORM映射）
│   │   ├── project.go            # Project表模型
│   │   ├── artifact.go           # Artifact表模型
│   │   └── ...
│   │
│   ├── pkg/                      # 公共库（⭐ 被多组件复用）
│   │   ├── auth/                 # 认证框架
│   │   ├── token/                # JWT Token签发验证
│   │   ├── scan/                 # 扫描器适配器接口（三层抽象）
│   │   │   ├── rest/v1/          # Scanner Adapter HTTP API定义
│   │   │   └── v1/               # 扫描报告格式定义
│   │   ├── replication/          # 复制引擎
│   │   ├── robot/                # 机器人账户
│   │   ├── permission/           # RBAC权限评估引擎
│   │   ├── config/               # 配置管理
│   │   └── ...
│   │
│   └── jobservice/               # JobService组件完整源码
│
├── api/                          # Swagger API定义（YAML）
│   └── v2.0/swagger.yaml         # ⭐ REST API定义（Harbor API文档源）
│
├── make/                         # 编译产物目录
│   ├── harbor_core               # Core二进制文件
│   ├── harbor_jobservice         # JobService二进制文件
│   ├── migrations/               # PostgreSQL数据库迁移脚本
│   └── common/                   # 配置文件模板
│
├── tests/                        # e2e测试和测试资源
├── tools/                        # 开发工具脚本
└── contrib/                      # 社区贡献的辅助工具
```

### 3.4 第三步：追踪一个API请求的完整链路

**目标**：以`GET /api/v2.0/projects`为例，追踪从HTTP请求到SQL查询的完整调用链。

```go
// ══════════════════════════════════════════════
// 第一站：路由注册
// 文件：src/server/v2.0/router.go（约第93行）
// ══════════════════════════════════════════════
func registerAPIRoutes(api *beego.Namespace) {
    // 使用Beego的Namespace路由机制
    api.Router("/projects", &handler.ProjectAPI{}, "get:List")
    // 含义：GET /api/v2.0/projects -> handler.ProjectAPI{}.List()
}

// ══════════════════════════════════════════════
// 第二站：HTTP Handler
// 文件：src/server/v2.0/handler/project.go（简化的实际代码）
// ══════════════════════════════════════════════
type ProjectAPI struct {
    BaseAPI           // 继承通用Handler（错误处理、响应序列化等）
    proCtl controller.ProjectController  // Controller接口（依赖注入）
}

func (p *ProjectAPI) List() {
    // 1. 解析HTTP Query参数
    page, _ := p.GetInt64("page", 1)
    pageSize, _ := p.GetInt64("page_size", 10)
    name := p.GetString("name")
    public := p.GetString("public")  // 可选：只查公开项目
    
    // 2. 构建Query对象
    query := &q.Query{
        Keywords: map[string]interface{}{
            "name":   name,
            "public": public,
        },
        PageNumber: page,
        PageSize:   pageSize,
    }
    
    // 3. 获取当前用户信息（从JWT Token解析）
    ctx := p.Ctx.Request.Context()
    
    // 4. 调用Controller层
    projects, total, err := p.proCtl.List(ctx, query)
    if err != nil {
        p.SendError(err)
        return
    }
    
    // 5. 返回JSON响应
    p.Data["json"] = models.ProjectListResponse{
        Total:    total,
        Projects: projects,
    }
    p.ServeJSON()
}

// ══════════════════════════════════════════════
// 第三站：Controller（业务逻辑层）
// 文件：src/controller/project/controller.go（简化的实际代码）
// ══════════════════════════════════════════════
type Controller interface {
    List(ctx context.Context, query *q.Query) ([]*models.Project, int64, error)
    Create(ctx context.Context, project *models.Project) (int64, error)
    Delete(ctx context.Context, id int64) error
    // ... 其他方法
}

type controller struct {
    dao     dao.ProjectDAO          // DAO接口（可以是PGSQL实现或Mock实现）
    auth    auth.Authorizer         // 权限验证器
    quota   quota.Controller        // 配额管理
}

func (c *controller) List(ctx context.Context, query *q.Query) ([]*models.Project, int64, error) {
    // 1. 获取当前用户
    user, ok := ctx.Value("user").(*models.User)
    if !ok {
        return nil, 0, errors.New("user not found in context")
    }
    
    // 2. 权限过滤：只返回当前用户有READ权限的项目
    // RBAC评估引擎判断用户角色（Project Admin/Developer/Guest）
    authorizedIDs, err := c.auth.GetAuthorizedProjectIDs(ctx, user)
    if err != nil {
        return nil, 0, err
    }
    
    // 3. 将权限过滤条件注入Query
    if query.Keywords == nil {
        query.Keywords = make(map[string]interface{})
    }
    query.Keywords["project_ids"] = authorizedIDs
    
    // 4. 调用DAO层查询数据库
    projects, total, err := c.dao.List(ctx, query)
    if err != nil {
        return nil, 0, err
    }
    
    return projects, total, nil
}

// ══════════════════════════════════════════════
// 第四站：DAO（数据访问层）
// 文件：src/dao/pgsql/project.go（简化的实际代码）
// ══════════════════════════════════════════════
type ProjectDAO interface {
    List(ctx context.Context, query *q.Query) ([]*models.Project, int64, error)
    Get(ctx context.Context, id int64) (*models.Project, error)
    Create(ctx context.Context, project *models.Project) (int64, error)
    Delete(ctx context.Context, id int64) error
}

type projectDAO struct {
    db *gorm.DB  // GORM数据库实例
}

func (d *projectDAO) List(ctx context.Context, query *q.Query) ([]*models.Project, int64, error) {
    var projects []*models.Project
    var total int64
    
    // 构建GORM查询
    db := d.db.WithContext(ctx).
        Model(&models.Project{}).
        Where("deleted = ?", false)
    
    // 动态条件：如果query指定了project_ids，添加IN查询
    if ids, ok := query.Keywords["project_ids"].([]int64); ok && len(ids) > 0 {
        db = db.Where("project_id IN (?)", ids)
    }
    
    // 名称模糊搜索
    if name, ok := query.Keywords["name"].(string); ok && name != "" {
        db = db.Where("name LIKE ?", "%"+name+"%")
    }
    
    // 计数
    if err := db.Count(&total).Error; err != nil {
        return nil, 0, err
    }
    
    // 分页 + 排序 + 查询
    if err := db.
        Order("name ASC").
        Offset(int((query.PageNumber - 1) * query.PageSize)).
        Limit(int(query.PageSize)).
        Find(&projects).Error; err != nil {
        return nil, 0, err
    }
    
    return projects, total, nil
}

// ══════════════════════════════════════════════
// 第五站：数据库模型
// 文件：src/models/project.go
// ══════════════════════════════════════════════
type Project struct {
    ProjectID    int64     `gorm:"primaryKey;column:project_id;autoIncrement"`
    OwnerID      int       `gorm:"column:owner_id;index"`
    Name         string    `gorm:"column:name;type:varchar(255);uniqueIndex"`
    Public       bool      `gorm:"column:public;default:false"`
    RegistryID   int64     `gorm:"column:registry_id"`
    CreationTime time.Time `gorm:"column:creation_time;autoCreateTime"`
    UpdateTime   time.Time `gorm:"column:update_time;autoUpdateTime"`
    Deleted      bool      `gorm:"column:deleted;default:false;index"`
}

func (p *Project) TableName() string {
    return "project"
}
```

"看到这个完整的调用链路了吗？每一层有清晰的职责边界：

- **Handler**：解析HTTP参数、调用Controller、序列化响应。**不知道数据库的存在**。
- **Controller**：执行业务规则（权限检查、配额验证、状态机转换）。**不知道SQL的存在**。
- **DAO**：执行SQL查询、构建动态条件。**不知道HTTP的存在**。
- **Model**：定义数据库表的Go结构体映射。**没有任何业务逻辑**。"

### 3.5 第四步：编写和运行单元测试

**目标**：学习Harbor的测试模式，编写基于mock的单元测试。

```go
// ══════════════════════════════════════════════
// 文件：src/controller/project/controller_test.go
// Harbor 的标准单元测试模式
// ══════════════════════════════════════════════

package project

import (
    "context"
    "testing"
    
    "github.com/goharbor/harbor/src/dao"
    "github.com/goharbor/harbor/src/models"
    "github.com/stretchr/testify/assert"
    "github.com/stretchr/testify/mock"
)

// MockProjectDAO 是 DAO 接口的Mock实现
type MockProjectDAO struct {
    mock.Mock
}

func (m *MockProjectDAO) List(ctx context.Context, query *q.Query) ([]*models.Project, int64, error) {
    args := m.Called(ctx, query)
    return args.Get(0).([]*models.Project), args.Get(1).(int64), args.Error(2)
}

// TestController_List 测试列出项目的正常流程
func TestController_List(t *testing.T) {
    // 1. 准备Mock
    mockDAO := new(MockProjectDAO)
    
    expectedProjects := []*models.Project{
        {ProjectID: 1, Name: "order-platform"},
        {ProjectID: 2, Name: "user-service"},
    }
    
    // 设置Mock的期望行为
    mockDAO.On("List", mock.Anything, mock.Anything).
        Return(expectedProjects, int64(2), nil)
    
    // 2. 创建Controller（注入Mock DAO）
    ctrl := &controller{
        dao: mockDAO,
    }
    
    // 3. 准备测试Context（注入用户信息）
    ctx := context.WithValue(context.Background(), "user", &models.User{
        UserID: 1,
        Username: "testuser",
    })
    
    // 4. 执行被测试的方法
    projects, total, err := ctrl.List(ctx, &q.Query{
        PageNumber: 1,
        PageSize:   10,
    })
    
    // 5. 断言结果
    assert.NoError(t, err)
    assert.Equal(t, int64(2), total)
    assert.Len(t, projects, 2)
    assert.Equal(t, "order-platform", projects[0].Name)
    
    // 6. 验证Mock被正确调用
    mockDAO.AssertExpectations(t)
}
```

```bash
# 运行单元测试
cd harbor/src

# 运行所有单元测试（~10分钟）
make ut

# 只运行project controller的单元测试
go test ./controller/project/ -v -count=1
# 预期输出：
# === RUN   TestController_List
# --- PASS: TestController_List (0.00s)
# PASS
# ok  github.com/goharbor/harbor/src/controller/project  0.012s

# 运行指定测试函数
go test ./controller/project/ -run TestController_List -v

# 生成测试覆盖率报告
go test ./controller/project/ -coverprofile=coverage.out
go tool cover -html=coverage.out  # 浏览器查看覆盖率热力图
```

### 3.6 第五步：提交你的第一个Harbor PR

**目标**：按照Harbor社区规范提交一个正确的Pull Request。

```bash
# 步骤1：在GitHub上Fork仓库
# 浏览器访问：https://github.com/goharbor/harbor
# 点击右上角 Fork -> 选择你的账户

# 步骤2：克隆Fork并创建功能分支
git clone https://github.com/YOUR_USERNAME/harbor.git
cd harbor
git remote add upstream https://github.com/goharbor/harbor.git
git checkout -b feat/add-custom-metrics-endpoint

# 步骤3：编写代码 + 测试
# 例如：在 src/server/v2.0/handler/ 添加新的metrics端点

# 步骤4：本地运行测试和Lint
make lint
# 修复所有golangci-lint报告的问题
make ut
# 确保所有单元测试通过

# 步骤5：提交代码（⚠️ 关键：必须包含DCO签名）
git add .
git commit -s -m "feat: add custom Prometheus metrics endpoint for registry operations

This commit adds a new /api/v2.0/metrics/registry endpoint that exposes
registry-specific Prometheus metrics (push/pull latency, bandwidth, error rates).

Fixes: #12345

Signed-off-by: Your Name <your@company.com>"

# ⚠️ Commit Message格式规范（Harbor使用Conventional Commits）：
# feat:     新功能
# fix:      Bug修复
# chore:    杂项（构建、CI、依赖更新）
# docs:     文档更新
# refactor: 代码重构
# test:     测试相关
# perf:     性能优化

# 步骤6：Push并创建PR
git push origin feat/add-custom-metrics-endpoint

# 步骤7：在GitHub创建PR
# 访问你的Fork仓库 -> Compare & pull request
# PR模板要求填写：
# - Summary of the change（变更摘要）
# - What tests were done（测试结果）
# - Related issue（关联Issue）
# - Screenshots (if UI change)（UI截图）

# 步骤8：等待CI通过
# Harbor CI会自动运行：
# - golangci-lint（代码风格）
# - unit tests（单元测试）
# - swagger validation（API文档一致性）
# - DCO check（签名检查）
# - license check（许可证合规）
```

### 3.7 可能遇到的坑

**坑1：make build失败于前端编译**

现象：`make build`在`make build_ui`步骤失败，报错`npm ERR! ... Angular CLI requires Node.js 18+`。

根因：前端Portal使用Angular 16+，要求Node.js 18+和npm 9+。系统自带的Node.js版本过旧（如14.x）。

解决方案：
```bash
# 如果只改后端Go代码，跳过前端编译
make install && make swagger && make compile_core

# 如果确实需要前端编译，安装正确的Node.js版本
# 使用nvm管理Node版本
nvm install 18
nvm use 18
node --version  # 确认 v18.x
make build_ui

# 或在Docker中编译前端
docker run --rm -v $(pwd):/workspace -w /workspace/src/portal \
  node:18-alpine sh -c "npm install && npm run build"
```

**坑2：Swagger生成失败——版本不匹配**

现象：`make swagger`失败，报错`go-swagger: unknown command "generate"`或版本兼容性错误。

根因：不同版本的`go-swagger`工具生成的代码格式不同。Harbor v2.12依赖特定版本的go-swagger（在`tools/swagger/gen_apis.sh`中指定）。

解决方案：
```bash
# 检查并安装Harbor指定的swagger版本
cat tools/swagger/gen_apis.sh | head -20
# 查找版本号（通常在注释中）

# 手动安装指定版本
SWAGGER_VERSION="v0.30.5"
go install github.com/go-swagger/go-swagger/cmd/swagger@${SWAGGER_VERSION}

# 或使用Makefile自动安装
make install  # 会自动安装指定版本的swagger
```

**坑3：PR被CI拒绝——golangci-lint不通过**

现象：PR的CI流水线在`lint`步骤失败，大量格式、命名和代码风格错误。

根因：Harbor有严格的代码风格规范（`.golangci.yml`定义），包括但不限于：
- 函数长度限制（`funlen`检查）
- 圈复杂度限制（`gocyclo`检查）
- 命名规范（`revive`检查）
- 错误处理规范（`errcheck`检查）

解决方案：
```bash
# 提交前本地跑lint
make lint
# 预期输出（如果有问题）：
# src/controller/project/controller.go:45:6: exported: type name will be used as ...
# src/controller/project/controller.go:78: Function 'List' is too long (85 > 80)

# 逐一修复所有warning和error
# 常见修复：
# 1. 过长的函数 -> 拆分为多个子函数
# 2. 未处理的error -> 添加 if err != nil { return err }
# 3. 导出的类型缺少注释 -> 添加 // TypeName 描述注释

# 再次检查
make lint
# 预期输出：无任何warning或error
```

**坑4：UT在本地通过，但在CI中失败**

现象：本地`make ut`全部通过，但PR的CI流水线中UT失败。

根因：本地环境和CI环境的差异——Go版本、OS、时区等都可能导致测试行为不同。

解决方案：
```bash
# 方案A：在CI一致的Docker环境中运行测试
docker run --rm -v $(pwd):/workspace -w /workspace/src \
  golang:1.21 go test ./...

# 方案B：检查测试中的硬编码逻辑
# 避免使用：
# - time.Now()（CI的时区可能不同）
# - 系统特定路径（如 /home/user/ vs /root/）
# - 依赖本地环境变量
# 应使用：
# - mock clock（如 github.com/benbjohnson/clock）
# - t.TempDir() 创建临时目录
# - 在测试中显式设置所需的环境变量
```

**坑5：Beego ORM和GORM混用导致的连接池冲突**

现象：代码中同时使用了Beego ORM（`orm.NewOrm()`）和GORM（`db.WithContext(ctx)`），在高并发下数据库连接池耗尽。

根因：Harbor正在从Beego ORM迁移到GORM，新旧两套ORM各自维护独立的连接池，总连接数可能超过PostgreSQL的`max_connections`。

解决方案：
```bash
# 新代码一律使用GORM（不要使用Beego ORM）
# 在dao层检查：src/dao/pgsql/ 目录下的新文件都应使用GORM

# 如果必须混用（修改老代码时应遵循一致性原则），确保连接池配置合理：
# harbor.yml
database:
  max_open_conns: 50   # GORM连接池
  max_idle_conns: 10

# Beego ORM连接池在代码中通过 orm.SetMaxIdleConnsPerDB() 配置
```

---

## 4 项目总结

### 4.1 Harbor核心模块依赖图

```
cmd/core/main.go (进程入口)
    │
    ├── server/v2.0/router.go (HTTP路由注册)
    │       ├── server/v2.0/handler/ (HTTP Handler 层)
    │       │       ├── 解析请求参数
    │       │       ├── 调用 controller 层
    │       │       └── 序列化响应JSON
    │       │
    │       ├── controller/ (业务逻辑层)
    │       │       ├── 权限检查 (pkg/permission/)
    │       │       ├── 配额验证 (pkg/quota/)
    │       │       ├── 业务规则执行
    │       │       └── 调用 dao 层
    │       │
    │       ├── dao/pgsql/ (数据访问层)
    │       │       ├── GORM/Beego ORM
    │       │       └── PostgreSQL SQL查询
    │       │
    │       └── models/ (数据库模型)
    │               └── GORM Tag 映射
    │
    ├── pkg/auth/ (认证框架: DB/LDAP/OIDC)
    ├── pkg/token/ (JWT签发验证: Bearer Token)
    ├── pkg/scan/ (扫描器适配器接口: rest/v1 + v1)
    ├── pkg/replication/ (镜像复制引擎: 推/拉/事件)
    └── pkg/permission/ (RBAC权限评估: 角色/资源/操作)
```

### 4.2 适用场景

1. **阅读源码理解Harbor工作原理**：通过追踪"最小阅读路径"（main.go → router.go → handler → controller → dao），快速理解Harbor的架构思想。
2. **为Harbor开发自定义扩展**：例如添加新的API端点、自定义认证后端、扩展扫描器适配器等。
3. **参与Harbor开源社区贡献**：修复Bug、改进文档、提交新功能——掌握PR流程和代码规范。
4. **企业内部二次开发**：在Harbor基础上开发企业定制版本（如内网环境优化、合规功能增强）。
5. **学习Go语言大型项目的工程化实践**：Harbor的分层架构、依赖注入模式、Mock测试策略、Makefile编译体系都是很好的Go工程化参考。

### 4.3 不适用场景

1. **不需要修改Harbor源码的运维场景**：如果只是部署和使用Harbor（配置harbor.yml、管理项目、操作API），不需要阅读源码。参考Harbor官方文档和本专栏前31章即可。
2. **快速原型验证**：如果需要快速验证一个Docker Registry相关的想法，开源的CNCF Distribution项目比Harbor更适合作为基础——它更轻量、无Harbor复杂的业务逻辑层。

### 4.4 注意事项

1. **Beego ORM遗留代码慎改**：`src/dao/`下使用Beego ORM的老文件（如部分replication/scan相关DAO），修改时保持ORM一致性——不要在同一文件中混用Beego和GORM。
2. **Swagger生成的代码不要手动修改**：`src/server/v2.0/models/`和部分`handler/`文件由`make swagger`自动生成。手动修改会被下次生成覆盖——修改前确认文件头顶是否有"Code generated by go-swagger"注释。
3. **DCO签名不可省略**：Harbor要求每个commit必须有`Signed-off-by`行。使用`git commit -s`自动添加。缺少DCO的PR会被CI自动关闭。
4. **测试文件命名规范**：单元测试文件放在与被测代码相同的package中，文件名为`xxx_test.go`。集成测试放在`tests/`目录下，文件名符合`*_test.go`。
5. **数据库迁移脚本不可逆**：`make/migrations/`下的SQL脚本一旦执行不可回滚。设计新迁移时优先考虑向前兼容（添加列而不是修改列，添加表而不是删除表）。

### 4.5 常见故障排查表

| 故障现象 | 根因 | 排查命令 | 解决方案 |
|---------|------|---------|---------|
| `go build`报"package not found" | Swagger代码未生成 | 检查`src/server/v2.0/models/`是否有文件 | `make swagger`生成API代码 |
| `make build`在UI步骤失败 | Node.js版本过低 | `node --version` | 安装Node.js 18+ 或跳过前端编译 |
| `make ut`需要2小时以上 | 跑的是集成测试而非单元测试 | `make ut` vs `make test` 对比 | 使用`make ut`只跑单测，或`go test -short` |
| PR被CI的DCO check拒绝 | 缺少`Signed-off-by` | `git log -1` 查看commit末尾 | 使用`git commit -s --amend`补签 |
| golangci-lint报"Function too long" | 函数超过80行 | `make lint` 查看具体函数 | 拆分为多个子函数 |
| 测试在所有环境通过但CI失败 | 时区/OS差异 | 检查测试中的`time.Now()`等系统依赖 | 使用mock clock，在CI Docker中本地验证 |

### 4.6 深度思考

1. **在Harbor源码中，找到"删除镜像"（Artifact Delete）操作的完整代码路径——从`router.go`的路由定义开始，追踪到PostgreSQL的SQL执行结束。列出沿途经过的每个文件、函数名称，以及每个函数中关键的"安全检查"（如：镜像是否被P2P预热任务引用？是否有不可变Tag保护？是否有关联的复制任务在执行中？）。基于这个分析，设计一个"删除镜像审批流程"的自定义扩展方案。**

2. **Harbor的ORM层正从Beego ORM迁移到GORM。如果你需要为Harbor添加一个新的数据表（如`custom_approval`表），你会如何设计这个变更？需要考虑：（1）新表放在哪个DAO文件中？（2）使用Beego ORM还是GORM？（3）数据库迁移脚本怎么写（`make/migrations/`下的SQL是什么）？（4）如何编写基于Mock的单元测试来测试新增的Controller逻辑？**

---

> 下一章预告：第33章将深入Harbor Core核心服务的源码剖析，包括依赖注入机制、ORM框架迁移策略和性能优化实战。
