# 第33章：Core 核心服务源码剖析

## 1 项目背景

某金融科技公司基于Harbor自建了企业级容器镜像仓库，服务于内部200+开发团队。在日常运维中，安全团队提出了三个硬性需求：第一，所有API响应必须追加`X-Request-ID`和`X-Trace-ID`以便接入公司自研的链路追踪平台；第二，需要在认证环节增加双因子验证（2FA）——当用户执行删除操作时，即使已通过OIDC认证，仍需额外验证一次性动态口令；第三，所有"删除"类操作必须记录详细的审计日志（包括操作人、时间、IP、删除的镜像Tag列表和digest），并推送到独立的审计服务。

开发团队在实施过程中暴露出以下痛点：

**痛点一：Beego框架不熟悉。** Harbor Core使用Beego作为Go Web框架，其路由注册采用`NSNamespace`+`NSRouter`的链式声明方式，辅以`FilterFunc`中间件链，与团队熟悉的Gin/Echo框架差异巨大。团队成员不清楚Beego的请求生命周期中`BeforeStatic` / `BeforeRouter` / `BeforeExec` / `AfterExec` / `FinishRouter`五个过滤阶段的触发时机和上下文数据可用性，导致自定义中间件写在了错误的阶段。

**痛点二：中间件栈深度与执行顺序陷阱。** 一个典型的API请求经过7层中间件：Recovery（panic恢复）→ Log（访问日志）→ Token/Session（多种认证方式的统一解析）→ Security（RBAC鉴权与策略匹配）→ ReadOnly（GC状态下拒绝写操作）→ Quota（项目/仓库配额检查）→ Artifact（制品级细粒度访问控制）。团队发现：如果在Security层之前插入自定义过滤，会在未鉴权状态下泄露系统元数据；如果在Security层与ReadOnly层之间插入，某些写操作可能绕过只读检查。

**痛点三：分层架构导致调用链路追踪困难。** Harbor Core采用Handler → Controller → DAO三层架构，但一个看似简单的"删除项目"操作，实际触发的事件链跨越了`src/server/v2.0/handler/project.go`、`src/controller/project/controller.go`、`src/controller/repository/controller.go`、`src/controller/artifact/controller.go`、`src/controller/quota/controller.go`、`src/pkg/notification/notification.go`六个模块。每当代码执行到一个跨层调用，团队成员需要频繁在IDE中跳转，心智负担极高。

**痛点四：配置加载机制隐晦。** Harbor Core的配置不是通过简单的`flag.Parse()`或`viper.Unmarshal()`完成的。它涉及自研的`configloader`——从`harbor.yml`文件→环境变量覆盖→默认值填充的三级优先级合并。团队在修改数据库连接池参数后重启失败，排查了2小时才发现`harbor.yml`中的`max_idle_conns`必须同时匹配PostgreSQL服务端的`max_connections`配置，否则连接池会因数据库端拒绝连接而陷入永久等待。

---

## 2 项目设计——剧本式交锋对话

**第一回合：从小胖的困惑开始**

**小胖**（抓耳挠腮地看源码）："大师大师，我快疯了！你看这个`beego.InsertFilter("/*", beego.BeforeRouter, SecurityFilter)`——这不就是个拦截器嘛，跟我们Java里写Spring Security的`OncePerRequestFilter`差不多。但为什么Core要把认证和鉴权拆成两个独立的Filter？合在一起一个filter不就完了吗？就好比进公司大门，保安既查工牌又校验权限，为啥要设两道门卡？"

**大师**（端着茶杯微笑）："小胖，你这个比喻很有意思。回到你的保安场景——如果公司有10栋楼，每栋楼有不同层级的门禁，保安是在大门口就把所有人拦下逐一核查全部权限效率高，还是先在大门口快速验证'这是我们的员工'，然后让各楼层的门禁自己去判断'这个员工能进这间机房吗'效率高？Harbor也是同样的道理：Token/Session Filter负责"你是谁"（身份验证），Security Filter负责"你能做什么"（权限鉴权）。拆分的核心原因是——身份验证只需要一次IO（查数据库或Redis），而权限鉴权需要结合当前请求的资源+操作逐次判定。如果把两者耦合在一起，那个'只需要验身份的静态资源请求'也会触发一次完整的RBAC策略评估。"

**小白**（若有所思）:"那按这个逻辑，Stateless的JWT Bearer Token解析其实不查数据库，为什么Token Filter里还要放一个SessionAuth的fallback？如果一个环境只配置了OIDC，SessionAuth那段代码不就永远是死代码？这在架构上算不算不干净？"

**大师**（放下茶杯，在白板上画了起来）:"这个问题很好。Core的认证中间件设计采用了**策略链模式（Chain of Responsibility）**——它维护一个`[]Authenticator`切片，遍历所有注册的认证器，第一个返回'成功'的就停止。SessionAuth不是OIDC的fallback，它是提供给**同时使用UI登录和API Token**的混合场景的。你说得对，如果环境只配了OIDC，SessionAuth就是死代码——但实际上Core启动时会根据配置**动态构建**这个认证器切片，你的`harbor.yml`里没有配`auth_mode: db_auth`，SessionAuth根本不会被append进去。这是**编译时灵活、运行时精简**的Go惯用模式，不是架构不干净。"

---

**第二回合：深入中间件执行顺序**

**小胖**（激动地拍桌子）:"那我明白了！所以我现在要加一个自定义Header中间件，只需要在`router.go`的init函数里写一个`beego.InsertFilter`就行？那我放在Security前还是Security后？这有区别吗？反正就是往Response里塞两个Header，跟认证有什么关系？"

**大师**（收起笑容，神情严肃）:"这就是今天最容易踩的坑。你往Response加`X-Request-ID`当然没问题——但你加的那个中间件里如果`ctx.Input.GetData("project_id")`访问了请求参数，那就必须放在SecurityFilter**之后**。为什么？因为SecurityFilter承担了一个隐式职责：它负责做**请求参数的解析和上下文的注入**。在SecurityFilter执行之前，`ctx.Input.Context.Request.Context()`里没有`project_id`、没有`user_id`、没有任何业务元数据。你的中间件如果依赖这些数据，放在Security前就会panic。"

**小白**（快速翻阅源码，突然抬头）:"等等，我发现了！ReadOnly Filter的实现里写了一句`if c.Ctx.Input.IsGet() { return }`——那就是说，GC期间只拦写操作，读操作是放行的。那如果我的Webhook通知是在Artifact层之后触发的——GC期间用户拉镜像触发的Webhook会正常发送吗？会不会因为ReadOnly的副作用导致通知被抑制了？"

**大师**（赞许地点点头）:"小白你抓到了一个真正的架构权衡点。ReadOnly Filter的定位是'防止在GC标记阶段产生新的垃圾数据'，所以它只拦截`POST/PUT/PATCH/DELETE`——这些都是会产生新Blob或修改元数据的操作。Webhook通知本身是**读操作触发的副作用**，不产生新的存储数据，所以不受ReadOnly影响。但这里有另一个边界问题：如果Webhook回调的目标是一个Harbor API，那这个回调请求本身也会再经过一次中间件链——可能会出现'写操作被拒绝但读操作通过'的不一致状态。"

---

**第三回合：从调试到架构思考**

**小胖**（苦恼地）:"大师，我改完router.go重新编译后，所有API都返回500了！日志里显示`orm: no db found`——但我明明没动BaseDao的代码啊！这怎么像一个'蝴蝶效应'——改个路由把数据库搞崩了？"

**大师**:"这不是蝴蝶效应，这是Beego的`init()`函数执行顺序问题。你看你新加的中间件文件——文件名是`a_custom_filter.go`对吧？Go的`init()`函数按照**文件名的字母顺序**执行。你的`a_`开头的文件比Core原有的`security.go`先初始化，如果你的`init()`里调用了任何需要数据库连接的代码（比如初始化一个DAO实例来读取配置），那就会在数据库连接池建立之前执行，直接触发空指针。这就是为什么Harbor Core约定所有数据访问必须在`beego.Run()`之后——它用一个名为`postinit`的回调钩子来保证安全。"

**小白**:"这么说，Beego的`init()`顺序问题本质上是Go语言的'包级别依赖不可声明'的问题？那为什么不用依赖注入框架（比如Wire或Dig）来替代？"

**大师**:"你的想法方向是对的，但时机和成本需要考虑。Harbor诞生于2016年，那个年代Go社区的DI框架还不成熟。用`init()`+全局变量是当时的主流实践。今天如果要改造——全部58个handler文件、30+个controller文件、40+个DAO文件都需要做依赖注入改造，工作量至少是3人月的级别，而且需要所有贡献者同步切换心智模型。所以现在的策略是：新模块（比如P2P预热服务）采用干净的依赖注入，老模块保持稳定。这也是大型开源项目常见的**渐进式架构演进**策略。"

**小胖**（恍然大悟）:"所以总结一下——理解Core源码的三个关键心智模型是：过滤器链的执行顺序决定数据可用性、init函数的字母序决定初始化时序、Handler→Controller→DAO三层调用链追踪依赖IDE跳转？"

**大师**:"还有第四点：**所有配置变更必须走三级合并模型**（yaml → 环境变量 → 默认值）。如果直接在`config.go`里硬编码修改，下次`harbor.yml`升级时你的修改就被覆盖了。记住，Harbor的配置系统是'声明式优先，环境变量兜底，默认值保底'。"

---

## 3 项目实战

### 3.1 环境要求

| 组件 | 版本要求 | 用途 |
|------|---------|------|
| Go | ≥1.20 | 编译Core二进制 |
| PostgreSQL | 12+ | 元数据持久化存储 |
| Redis | 6.0+ | 会话缓存与分布式锁 |
| Beego | v2.x (vendor内) | Web框架，无需单独安装 |
| Docker Compose | ≥1.29 | 本地开发环境搭建 |
| harbor.yml | 2.10+模板 | Core运行时配置 |

### 3.2 步骤一：搭建Core本地可调试的开发环境

**目标**：让Core进程能够脱离Docker容器，在本地IDE中运行并连接容器中的PostgreSQL/Redis。

```go
// src/cmd/core/main.go — 关键初始化流程
func main() {
    // 阶段1: 加载应用配置——三级合并
    // harbor.yml → 环境变量覆盖 → 默认值  → 写入全局 config.Instance()
    config.Init()

    // 阶段2: 初始化数据库连接池
    // 通过Beego ORM框架建立PostgreSQL连接
    // 连接参数(host/port/user/password/dbname/max_idle/max_open)全部来自config.Instance()
    if err := dao.Init(); err != nil {
        log.Fatalf("init database failed: %v", err)
    }

    // 阶段3: 初始化Redis连接池
    // 用于Session存储、Rate Limiting计数器、分布式锁
    if err := cache.Init(); err != nil {
        log.Fatalf("init cache failed: %v", err)
    }

    // 阶段4: 注册中间件链 (顺序至关重要)
    registerMiddlewares()

    // 阶段5: 注册v2.0 API路由
    // 采用Beego的Namespace路由分组, 按资源划分路由树
    registerV2APIs()

    // 阶段6: 启动HTTP Server, 监听8080端口
    beego.Run()
}

// registerMiddlewares 注册全局中间件, 顺序决定执行优先级
func registerMiddlewares() {
    // Filter1: Recovery —— 最外层, 捕获所有panic
    beego.InsertFilter("/*", beego.BeforeRouter, middleware.Recovery())

    // Filter2: AccessLog —— 记录每个请求的方法/路径/耗时/状态码
    beego.InsertFilter("/*", beego.BeforeRouter, middleware.LogFilter())

    // Filter3: TokenAuth —— 多认证方式统一入口 (Basic/Session/OIDC/Robot)
    beego.InsertFilter("/*", beego.BeforeRouter, security.TokenAuth())

    // Filter4: Security —— RBAC鉴权与资源级权限校验
    beego.InsertFilter("/*", beego.BeforeRouter, security.SecurityFilter())

    // Filter5: ReadOnly —— GC期间拒绝写操作
    beego.InsertFilter("/*", beego.BeforeRouter, middleware.ReadOnlyFilter())

    // Filter6: Quota —— 项目和仓库级配额校验
    beego.InsertFilter("/*", beego.BeforeRouter, middleware.QuotaFilter())

    // Filter7: Artifact —— 制品级访问控制 (细粒度到Tag/Digest)
    beego.InsertFilter("/*", beego.BeforeRouter, middleware.ArtifactFilter())
}
```

```yaml
# 本地开发用的 harbor.yml 精简配置
hostname: 127.0.0.1
http:
  port: 8080
database:
  type: postgresql
  host: 127.0.0.1
  port: 5432
  username: postgres
  password: root123
  max_idle_conns: 50
  max_open_conns: 100
redis:
  host: 127.0.0.1
  port: 6379
  password:
  db_index: 1
```

### 3.3 步骤二：追踪一个完整的删除项目调用链

**目标**：理解Handler → Controller → DAO三层如何协作完成一个业务操作，以及Notification如何在Controller层触发。

```go
// ============== 第1层: Handler — 参数解析与HTTP响应 ==============
// 文件: src/server/v2.0/handler/project.go
type ProjectAPI struct {
    BaseAPI  // 内嵌基础API, 提供RenderError、GetString等工具方法
    ctl      controller.ProjectController  // 持有Controller接口, 实现依赖注入
}

func (p *ProjectAPI) Delete() {
    // 从URL路径中提取项目ID: DELETE /api/v2.0/projects/{id}
    projectID, err := p.GetInt64FromPath(":id")
    if err != nil || projectID <= 0 {
        p.RenderError(http.StatusBadRequest, "invalid project id")
        return
    }

    // 调用Controller层执行业务逻辑
    // 注意: 传递context.Context, 不是Beego的ctx
    ctx := p.Ctx.Request.Context()
    if err := p.ctl.Delete(ctx, projectID); err != nil {
        // 根据错误类型返回不同HTTP状态码
        if errors.Is(err, controller.ErrConflict) {
            p.RenderError(http.StatusConflict, err.Error())
        } else if errors.Is(err, controller.ErrForbidden) {
            p.RenderError(http.StatusForbidden, err.Error())
        } else {
            p.RenderError(http.StatusInternalServerError, err.Error())
        }
        return
    }
    p.RenderSuccess()
}

// ============== 第2层: Controller — 业务编排与事务控制 ==============
// 文件: src/controller/project/controller.go
type controller struct {
    projDao    project.DAO          // 项目数据访问对象
    repoDao    repository.DAO       // 仓库数据访问对象
    artDao     artifact.DAO         // 制品数据访问对象
    quotaCtl   quota.Controller     // 配额控制器(释放项目配额)
    notifMgr   notification.Manager // 通知管理器(发送Webhook)
    auditMgr   audit.Manager        // 审计日志管理器
}

// Delete 实现项目删除的完整事务流程
func (c *controller) Delete(ctx context.Context, projectID int64) error {
    // Step 1: 获取项目信息 —— 校验项目存在且未被删除
    project, err := c.projDao.Get(ctx, projectID)
    if err != nil {
        return fmt.Errorf("get project %d: %w", projectID, err)
    }

    // Step 2: 检查项目下是否还有仓库 —— 防止级联删除
    // 设计理念: Harbor采用"先清空后删除"策略, 避免误删
    repos, err := c.repoDao.ListByProject(ctx, projectID)
    if err != nil {
        return fmt.Errorf("list repos: %w", err)
    }
    if len(repos) > 0 {
        return fmt.Errorf(
            "project %s contains %d repositories, remove them first",
            project.Name, len(repos),
        )
    }

    // Step 3: 软删除项目 (标记deleted=true而非物理删除)
    // 软删除的好处: 支持回滚/审计追溯/GC异步清理关联数据
    project.Deleted = true
    project.DeletedTime = time.Now()
    if err := c.projDao.Update(ctx, project); err != nil {
        return fmt.Errorf("update project: %w", err)
    }

    // Step 4: 释放项目占用配额 (在配额系统中标记资源可回收)
    if err := c.quotaCtl.Release(ctx, project.QuotaID); err != nil {
        // 配额释放失败不影响项目删除, 但需要记录日志
        log.Warningf("release quota for project %d failed: %v", projectID, err)
    }

    // Step 5: 发送异步通知 (Webhook/Slack/邮件)
    // 通知是"最佳努力"模式, 失败不阻塞主流程
    go c.notifMgr.OnProjectDelete(ctx, project)

    // Step 6: 记录审计日志 (写入独立的审计表)
    go c.auditMgr.Record(ctx, &audit.Event{
        Resource: "project",
        Action:   "delete",
        TargetID: projectID,
        Detail:   fmt.Sprintf("deleted project %s", project.Name),
    })

    return nil
}

// ============== 第3层: DAO — 数据库操作封装 ==============
// 文件: src/core/dao/project.go
type dao struct {
    ormer orm.Ormer  // Beego ORM的数据库操作器
}

// Get 根据项目ID查询项目, 自动过滤已删除记录
func (d *dao) Get(ctx context.Context, id int64) (*models.Project, error) {
    project := &models.Project{}
    // 使用Beego ORM的QueryBuilder, 等价于:
    // SELECT * FROM project WHERE project_id = ? AND deleted = false
    o := d.ormer
    err := o.QueryTable(&models.Project{}).
        Filter("project_id", id).
        Filter("deleted", false).
        One(project)
    if err != nil {
        if err == orm.ErrNoRows {
            return nil, fmt.Errorf("project %d not found", id)
        }
        return nil, err
    }
    return project, nil
}

// Update 更新项目的指定字段
func (d *dao) Update(ctx context.Context, project *models.Project) error {
    o := d.ormer
    _, err := o.Update(project, "deleted", "deleted_time", "update_time")
    return err
}
```

### 3.4 步骤三：实现自定义中间件——审计日志增强

**目标**：在Security中间件之后插入自定义审计中间件，记录每个写操作的详细上下文。

```go
// 文件: src/server/middleware/audit/enhanced_audit.go
package audit

import (
    "encoding/json"
    "time"

    "github.com/beego/beego/v2/server/web/context"
    "github.com/goharbor/harbor/src/lib/log"
    "github.com/goharbor/harbor/src/pkg/audit"
)

// EnhancedAuditFilter 增强审计中间件
// 注册位置: 在SecurityFilter之后, ReadOnlyFilter之前
// 原因: 需要SecurityFilter注入的user_id和project_id, 但又要在只读检查之前记录所有尝试
func EnhancedAuditFilter(ctx *context.Context) {
    // 只审计写操作 (POST/PUT/PATCH/DELETE), 减少日志量
    method := ctx.Input.Method()
    if method == "GET" || method == "HEAD" || method == "OPTIONS" {
        return
    }

    // 从SecurityFilter注入的上下文中提取用户信息
    userID := ctx.Input.GetData("user_id")
    projectID := ctx.Input.GetData("project_id")

    startTime := time.Now()

    // 将原始请求体保存一份副本用于审计
    // 注意: Beego的Input.RequestBody只能读一次, 需要手动恢复
    requestBody := make([]byte, ctx.Input.Request.Body.Length())
    ctx.Input.Request.Body.Read(requestBody)

    // 构造审计事件
    event := audit.Event{
        Username:   getUsername(ctx),
        Resource:   ctx.Input.URL(),
        Action:     method,
        ProjectID:  safeToInt64(projectID),
        RequestIP:  ctx.Input.IP(),
        UserAgent:  ctx.Input.Header("User-Agent"),
        RequestAt:  startTime,
        // 敏感字段脱敏——密码、Token等字段过滤后记录
        Request:    sanitizeRequestBody(requestBody),
    }

    // 将审计事件写入Redis Stream (异步批量写入, 避免阻塞请求)
    // 使用go func()异步执行, 防止审计写入延迟影响API响应时间
    go func(e audit.Event) {
        if err := publishAuditEvent(ctx, e); err != nil {
            // 审计失败不影响主业务, 但需要打印错误日志
            log.Errorf("failed to publish audit event: %v", err)
        }
    }(event)
}

// sanitizeRequestBody 对请求体中的敏感字段进行脱敏
func sanitizeRequestBody(body []byte) []byte {
    var data map[string]interface{}
    if err := json.Unmarshal(body, &data); err != nil {
        return body // 非JSON请求体直接返回
    }
    sensitiveFields := []string{"password", "secret", "token", "key"}
    for _, field := range sensitiveFields {
        if _, ok := data[field]; ok {
            data[field] = "***REDACTED***"
        }
    }
    sanitized, _ := json.Marshal(data)
    return sanitized
}

// 注册中间件到全局过滤器链
func init() {
    // 参数说明:
    // "/*" — 匹配所有路径
    // beego.BeforeRouter — 在路由匹配之后、Handler执行之前触发
    // EnhancedAuditFilter — 自定义过滤器函数
    beego.InsertFilter("/*", beego.BeforeRouter, EnhancedAuditFilter)
}
```

### 3.5 步骤四：编译、部署与验证

```bash
# Step 1: 编译Core二进制
# 使用harbor项目根目录的Makefile, 会自动处理vendor依赖和CGO配置
cd $HARBOR_SRC
make compile_core
# 产物: ./make/harbor_core (Linux/amd64 静态链接二进制)

# Step 2: 构建开发用Docker镜像
# 将本地编译的二进制打入新镜像, 替代docker-compose中的官方镜像
docker build \
  --build-arg HARBOR_CORE_BIN=./make/harbor_core \
  -t harbor-core:dev \
  -f make/photon/core/Dockerfile \
  .

# Step 3: 修改docker-compose.yml使用开发镜像
# core:
#   image: harbor-core:dev  # 替换 goharbor/harbor-core:v2.10.0
#   volumes:
#     - ./harbor.yml:/etc/core/app.conf:ro

# Step 4: 重启Harbor Core服务
docker compose up -d core

# Step 5: 验证审计日志是否生效
# 发起一个删除项目请求, 检查Core日志
curl -X DELETE \
  -u "admin:Harbor12345" \
  -H "Content-Type: application/json" \
  http://127.0.0.1:8080/api/v2.0/projects/1

# 检查Core容器日志中的审计记录
docker logs harbor-core 2>&1 | grep "audit_event"
# 预期输出:
# {"level":"info","msg":"audit_event","username":"admin","action":"DELETE","resource":"/api/v2.0/projects/1","project_id":1,...}
```

### 3.6 常见陷阱与解决方案

| 序号 | 陷阱描述 | 根本原因 | 解决方案 |
|------|---------|---------|---------|
| 1 | 修改router.go后全站返回405 Method Not Allowed | Beego的`NSRouter`是精确方法匹配，同一路径注册了GET但未注册POST时，POST请求返回405而非路由到fallback。新注册路由时未考虑HTTP方法的完整性 | 使用`grep -r "NSRouter.*你的路径" src/server/`检查所有方法是否已注册；考虑用`NSAutoRouter`替代手动注册（不推荐生产环境） |
| 2 | 自定义中间件中使用`ctx.Input.RequestBody`后Handler收到空Body | Beego的`Input.Request.Body`是`io.ReadCloser`——只能读取一次。中间件读完后Body的offset在末尾，Handler再读就是空 | 在中间件中使用`io.TeeReader`拷贝一份Body内容，或先将Body读取到`[]byte`再通过`ctx.Input.Request.Body = io.NopCloser(bytes.NewBuffer(bodyBytes))`恢复 |
| 3 | `init()`函数中访问数据库导致`nil pointer dereference` | Go的`init()`按照文件名字母顺序执行。如果自定义文件字母序在前，其`init()`中访问了尚未初始化的全局DAO实例 | 将数据库操作从`init()`迁移到`beego.Run()`之后的回调中（使用`beego.AddAPPStartHook`）；或确保文件名字母序在所有DAO初始化文件之后（例如取名为`z_custom_middleware.go`） |
| 4 | 修改harbor.yml后Core启动失败但日志无明确错误 | Harbor Core的`config.Init()`中有大量静默fallback——格式错误的数字字段被替换为0，未知字段被忽略 | 使用`make validate`或直接运行`harbor-core --validate-config`检查配置文件合法性；重点检查`max_idle_conns`等数据库参数是否超出PostgreSQL服务端限制 |

---

## 4 项目总结

### 4.1 Core分层架构速查

| 层级 | 目录 | 职责 | 依赖方向 | 测试策略 |
|------|------|------|----------|---------|
| Handler | `src/server/v2.0/handler/` | HTTP协议适配：解析参数、序列化响应、错误码映射 | → Controller接口 | Mock Controller进行参数校验测试 |
| Controller | `src/controller/` | 业务编排：组合多个DAO调用、事务控制、事件发布 | → DAO接口 + 外部服务 | Mock DAO进行业务流程测试 |
| DAO | `src/core/dao/` | 数据持久化：封装Beego ORM操作、SQL查询优化 | → 数据库 | 使用TestContainers启动真实PostgreSQL测试 |
| Middleware | `src/server/middleware/` | 横切关注点：认证、鉴权、日志、限流、审计 | → 全局Context | 构造模拟Context进行隔离测试 |
| Model | `src/pkg/` | 领域模型：数据结构定义、类型常量、接口定义 | 无外部依赖 | 纯单元测试 |

### 4.2 适用场景与不适用场景

**适用场景：**
1. 企业内部需要自定义认证方式（如对接LDAP/AD/OIDC Provider）
2. 需要在API网关层对所有Harbor请求进行统一审计日志采集
3. 需要为特定资源类型（如Helm Chart）扩展自定义REST API端点
4. 需要修改配额检查策略（如按时间段动态调整配额上限）
5. 需要在制品拉取前注入额外的安全策略检查（如镜像签名验证）

**不适用场景：**
1. 仅需修改镜像存储路径或后端存储类型 —— 这属于Registry层的职责，修改Core无法影响Blob的读写路径
2. 仅需调整GC策略或Replication任务的调度频率 —— 这些由JobService管理，Core只负责提交任务，不参与执行逻辑

### 4.3 注意事项

1. **切勿在`init()`中访问数据库**：Go的包初始化顺序是不确定的，即使在同一个包内，文件的编译顺序也影响`init()`的执行次序。始终使用`beego.AddAPPStartHook`或在`main()`的`beego.Run()`之前显式调用初始化函数。
2. **中间件插入位置决定数据可用性**：在SecurityFilter之前，`ctx.Input.GetData("user_id")`返回nil。画一张你的自定义中间件所需数据的依赖图，确保其位置在所有数据提供者之后。
3. **Beego ORM的QueryTable链式调用不是线程安全的**：`QueryTable`返回的`QuerySeter`实例共享了ORM对象的内部状态，如果需要并发查询，使用`orm.NewOrm()`创建独立的Ormer实例。
4. **三级配置合并的优先级是yaml < 环境变量 < 默认值**：如果你在Linux环境变量中设置了`DATABASE_PORT=5433`，那么即使`harbor.yml`配了5432也用5433。排查配置问题时的标准流程是：先打印`config.Instance()`的最终值，再倒推哪个来源覆盖了它。
5. **Handler的BaseAPI内嵌了Beego的Controller**：这意味所有Handler都继承了Beego Controller的`Prepare()`/`Finish()`生命周期钩子。如果你在自定义Handler中重写了`Prepare()`，记得调用`p.BaseAPI.Prepare()`，否则日志和认证逻辑都会被跳过。

### 4.4 常见陷阱速查表

| 陷阱 | 出现概率 | 影响范围 | 快速诊断命令 |
|------|---------|---------|-------------|
| 中间件顺序错误导致认证绕过 | 中 | 安全漏洞 | `grep -n "InsertFilter" src/server/v2.0/router.go` |
| `init()`中DB空指针 | 高 | Core无法启动 | `docker logs harbor-core \| grep "nil pointer"` |
| Body只读一次导致参数丢失 | 中 | 特定API返回400 | `curl -v -X POST ... ` 对比请求体与响应中的错误详情 |
| 配置合并优先级误解 | 高 | 运行行为与预期不符 | `docker exec harbor-core /harbor/harbor_core --version` 查看最终配置 |

### 4.5 深度思考题

1. **如果要把Harbor Core从Beego框架迁移到Gin或Echo，影响范围有多大？** 请从以下维度估算：路由注册方式的变更（58个Handler文件）、中间件签名的适配（Beego的`FilterFunc` vs Gin的`gin.HandlerFunc`）、ORM的解耦（Beego ORM与框架强绑定吗？）、Controller生命周期的迁移（`Prepare()`/`Finish()`对应Gin的哪些钩子）。你认为迁移的性价比如何？

2. **Core当前的中间件链是全同步的——每个Filter都是阻塞执行。** 如果要将审计日志Filter改造为异步执行（不影响请求响应时延），你会采用什么方案？请考虑以下trade-off：goroutine的开销、Context的传递与取消传播、异步日志丢失的风险与容忍度、以及如何保证审计日志的时序不乱序（例如同一用户的删除→创建操作的审计顺序不能颠倒）。

---

> 下一章预告：第34章剖析JobService异步任务引擎源码。
