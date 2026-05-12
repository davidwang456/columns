# 第38章：Harbor 安全模型源码解析

## 1 项目背景

某省商业银行按照银保监会《商业银行信息科技风险管理指引》的要求，对全行使用的Harbor镜像仓库进行全面的安全审计。审计范围覆盖身份认证、访问控制、配额管理、漏洞阻断、合规审计五个维度。运维团队需要在理解Harbor安全模型源码的基础上，补齐安全短板并通过审计。

**痛点一：认证链的完整性和可追溯性。** 银行使用LDAP+OIDC双因子认证，用户在Portal登录后，Harbor Core生成的JWT Token携带角色信息。但安全审计要求回答："如果有人绕过Portal直接调用Core API，Core如何验证Token？Token中的项目角色是如何注入的？"。团队需要从源码层面理清认证→鉴权→授权中间件的调用链条。

**痛点二：配额检查的时机与孤立数据处理。** Docker push是一个多步骤流程（Blob Upload → Blob Patch → Blob Put → Manifest Put），Harbor的配额检查在多个步骤中被触发。审计人员提出了一个尖锐问题："如果用户在push多个Blob的过程中配额耗尽，已成功上传但尚未关联Manifest的Blob如何处理？它们会永久占用存储空间吗？是否需要手动清理？"

**痛点三：策略绕过路径的审计。** Harbor提供了标签不可变性（Immutability）、CVE阻止、内容信任三种策略。但安全团队发现：通过Harbor API直接调用`DELETE /api/v2.0/projects/{project}/repositories/{repo}/artifacts/{ref}/tags/{tag}`删除标签后，原有标签名可以重新push同名的未扫描镜像——相当于绕过了CVE阻止策略。这到底是Bug还是设计允许的合法操作？

**痛点四：Webhook通知的可靠性保证。** 银行的SIEM系统依赖Harbor的Webhook推送安全事件（push/delete/scan完成/策略触发）。审计要求回答：如果Webhook目标（SIEM）暂时不可用，事件是否会丢失？Harbor是如何保证事件传递的at-least-once语义的？重试机制是否有上限？

---

## 2 项目设计——剧本式交锋对话

*银行数据中心的安全运维办公室，三块大屏上分别显示着Harbor的访问日志、CVE扫描结果和SIEM告警。桌上摆着一本《Harbor源码导读》打印版，书页已经被翻得卷边。*

**小胖**：（瘫在人体工学椅上）"大师，Harbor的安全模型不就是检查用户有没有权限吗？用if-else判断一下不就完了？搞这么多中间件——auth、rbac、quota、immutable、vulnerable、contenttrust——这不是俄罗斯套娃吗？我数了一下，一个push请求最多要穿过6个中间件！这性能能扛得住？"

**大师**：（把保温杯往桌上一顿）"小胖，你见过机场安检吗？"

**小胖**："见过啊，先刷身份证、再过安检门、然后行李过X光，有时候还要被摸一遍。"

**大师**："对。机场不会让一个人同时查身份证+过X光。为什么？因为每个环节的检查维度不同——身份证验证是'你是不是你'（Auth），安检门检查'你有没有带金属'（Policy），X光检查'行李里有没有违禁液体'（CVE Blocking）。分开检查的好处是：一旦某一步失败，可以精确告诉用户'你卡在哪一步'。如果合在一个if-else里——用户收到一个模糊的'拒绝访问'，排查问题要看到底是密码错了还是配额超了还是CVE太高。"

**小白**：（手指在白板上快速画出请求流水线）"我理解分层的价值，但我不理解的是——这些中间件的执行顺序是谁决定的？顺序可以改吗？比如配额检查如果在认证检查之前，未认证的用户也能触发配额计算——这是不是一种信息泄露？攻击者可以通过'配额已满'和'认证失败'两种不同错误来推断项目是否存在？"

**大师**：（眼睛一亮，站起来走到白板前）"小白你问到核心安全问题了。这是个经典的**时序信道（Timing Side-Channel）**问题。Harbor把Auth中间件放在第一个，就是为了先确定'你是谁'，再检查'你能做什么'。顺序由`server/middleware/`下的`Init()`函数注册顺序决定。如果配额检查在认证之前——攻击者的确可以通过错误类型推断项目存在性，这在OWASP TOP 10里属于'安全配置错误'。"

**小胖**："那我再问一个实际的——配额检查在push过程中被触发好几次，第一次是POST /blobs/uploads初始化上传时，第二次是PUT /manifests提交时。如果第一次检查通过了（还剩5GB配额），用户开始上传Blob，上传了4GB后——另一个用户也上传了2GB并成功了。那第一个用户的Manifest PUT时再检查配额，发现只剩1GB了，但已经传了4GB的Blob——这些Blob怎么办？会变成孤儿Blob吗？"

**大师**："精辟。这就是配额设计的'竞态窗口'问题。Harbor的配额检查采用**乐观策略**——在Manifest PUT时做最终校验，如果不足则拒绝整个Manifest，但之前上传的Blob确实会留在Registry上。不过不用担心，Docker Registry的垃圾回收（GC）会定期清理**未被任何Manifest引用的Blob**。这就像你去超市买了10件商品，结账时发现钱不够——你可以退掉几件，但不能把退掉的商品偷偷带出去。已上传的Blob就是'放在收银台上的商品'，没有Manifest的引用，GC会在一段时间后当作垃圾清理。"

**小白**："那GC什么时候触发？如果GC在Blob上传后1小时才清理，这1小时内这些Blob确实占着配额空间？"

**大师**："嗯，这是目前Harbor配额系统的一个已知局限。配额计算是基于**所有Blob的存储总和**（包括未被引用的），但GC是定期批量执行（默认每2小时）。所以在这2小时内，那些'孤儿Blob'确实占用配额。对于磁盘紧张的场景，这是一个需要关注的点。社区有人提议改为**悲观策略**——先预占配额、上传完后再确认，但会大幅增加实现的复杂度。"

**小胖**："标签不可变性这块我踩过坑。Harbor Portal上设置了`latest`标签不可变，我在Portal上修改会报错。但我直接用API`DELETE /v2/<repo>/manifests/<digest>`——删成功了！然后重新push new latest，旧策略形同虚设。这是Bug吗？"

**大师**：（摇头）"这不是Bug，是设计权衡。Harbor的标签不可变性检查在中间件层（`server/middleware/immutable/`），它拦截的是**标签覆盖操作**——`PUT /v2/<repo>/manifests/latest`。但`DELETE /v2/<repo>/manifests/<digest>`删除的是Manifest本身（不是覆盖标签），中间件没有拦截删除操作。为什么？因为如果连删除都拦截，那么项目管理员删除一个错误标签的能力也被剥夺了——这是一种**权限与安全的平衡**。"

**小白**："那是不是说——Harbor的安全模型是有意留了'管理员后门'？系统管理员可以绕过所有策略？"

**大师**："对。在Harbor中，拥有`SysAdmin`角色或`ProjectAdmin`角色的用户，有权删除不可变规则、更改CVE阻止阈值、修改配额上限。这不是'后门'——这是**权限模型的正门**。安全不等于不允许任何操作，而是确保只有授权的人能做高风险操作——而且所有操作都被审计日志记录。审计日志就是事后追查的证据链。"

**小胖**："最后一个——Webhook。我设置了'所有push事件推送到企业微信'。测试时发现push后过了3秒企业微信才收到消息。Webhook是异步的吗？如果SIEM系统挂了，事件会丢吗？"

**大师**：（在笔记本上画出一个队列模型）"Webhook完全异步。流程是这样的：API请求（如push）完成 → 业务逻辑层产生Event对象 → Event被推入notification Queue → API返回200给用户 → JobService从队列取Event → 调用Webhook Handler → 发送HTTP请求到目标。也就是说，通知的成败不影响API响应。如果SIEM挂了，JobService会按指数退避重试3次（1分钟、5分钟、15分钟），3次都失败就标记为`Error`并停止。所以严格来说，Harbor提供的是**at-most-once**（至多一次）语义——如果3次重试都失败，事件确实会丢失，但会被记录下来供排查。要实现at-least-once，你得在SIEM端做幂等接收。"

---

## 3 项目实战

### 3.1 环境要求

| 组件 | 版本要求 | 说明 |
|------|---------|------|
| Go | >= 1.21 | 源码分析与中间件开发 |
| Harbor | >= v2.9.0 | 安全中间件源码分析目标 |
| PostgreSQL | >= 14 | 审计日志和策略配置存储 |
| Redis | >= 6.0 | 事件队列和Session缓存 |
| curl/jq | 任意 | API测试工具 |

### 3.2 步骤一：理解安全中间件链的初始化

**目标**：理清Harbor Core启动时如何注册和排序安全中间件。

```go
// 安全中间件初始化（基于Harbor v2.9源码结构）
// 源码参考: src/server/middleware/ 目录下各子包的 init() 函数

package middleware

import (
    "github.com/beego/beego/v2/server/web"
    "github.com/goharbor/harbor/src/server/middleware/quota"
    "github.com/goharbor/harbor/src/server/middleware/immutable"
    "github.com/goharbor/harbor/src/server/middleware/vulnerable"
    "github.com/goharbor/harbor/src/server/middleware/contenttrust"
    "github.com/goharbor/harbor/src/server/middleware/security"
    "github.com/goharbor/harbor/src/server/middleware/auditlog"
)

// InitMiddlewares 按安全优先级注册所有中间件
// 注册顺序即为执行顺序——先注册的先执行
func InitMiddlewares() {
    // Layer 1: 安全上下文（解析JWT Token，注入SecurityContext到ctx）
    web.InsertFilter("/api/*", web.BeforeRouter, security.SecurityFilter())

    // Layer 2: 配额检查（项目+系统级存储配额）
    web.InsertFilter("/api/*", web.BeforeRouter, quota.QuotaFilter())

    // Layer 3: 标签不可变性（阻止覆盖受保护的tag）
    web.InsertFilter("/api/*", web.BeforeRouter, immutable.MatchImmutableFilter())

    // Layer 4: 内容信任（验证镜像是否经过Notary签名）
    web.InsertFilter("/api/*", web.BeforeRouter, contenttrust.ContentTrustFilter())

    // Layer 5: CVE阻止策略（阻止含高危漏洞的镜像被拉取）
    web.InsertFilter("/api/*", web.BeforeRouter, vulnerable.VulnerableFilter())

    // Layer 6: 审计日志（所有请求无论成功失败都记录）
    web.InsertFilter("/api/*", web.BeforeRouter, auditlog.AuditLogFilter())
}
```

### 3.3 步骤二：配额检查中间件源码剖析

**目标**：理解配额如何在push流程的多阶段检查中实现。

```go
// quota/middleware.go —— 配额检查中间件核心逻辑

package quota

import (
    "context"
    "fmt"
    "net/http"

    "github.com/goharbor/harbor/src/controller/quota"
    "github.com/goharbor/harbor/src/lib/errors"
    "github.com/goharbor/harbor/src/pkg/types"
)

// QuotaFilter 配额检查中间件入口
// 在Blob Upload初始化和Manifest PUT时被触发
func QuotaFilter() func(ctx *context.Context) {
    return func(ctx *context.Context) {
        // 仅拦截写操作——GET/HEAD等读请求直接放行
        if ctx.Request.Method == http.MethodGet ||
            ctx.Request.Method == http.MethodHead ||
            ctx.Request.Method == http.MethodOptions {
            return
        }

        // 解析请求中的项目信息
        projectID, err := extractProjectID(ctx)
        if err != nil {
            return // 非项目级请求，跳过
        }

        // 获取当前项目的配额配置
        quotaCfg, err := quota.Ctl.GetByProject(ctx.Request.Context(), projectID)
        if err != nil {
            abortWithError(ctx, http.StatusInternalServerError, "quota config not found")
            return
        }

        // 判断请求类型——针对Blob Upload请求预估新增大小
        estimatedSize := estimateRequestSize(ctx)
        currentUsage := quotaCfg.Used

        // 配额检查核心逻辑
        if quotaCfg.HardLimit > 0 && // 0表示无限制
            (currentUsage + estimatedSize) > quotaCfg.HardLimit {
            
            // 配额不足——返回507 Insufficient Storage
            abortWithError(ctx, http.StatusInsufficientStorage,
                fmt.Sprintf("quota exceeded: current=%dMB, requested=%dMB, limit=%dMB",
                    currentUsage/1024/1024,
                    estimatedSize/1024/1024,
                    quotaCfg.HardLimit/1024/1024))
            return
        }

        // 配额预警（可选）——超过80%时记录Warning日志
        if currentUsage > quotaCfg.HardLimit*80/100 {
            log.Warnf("project %d quota usage %d%% approaching limit",
                projectID, currentUsage*100/quotaCfg.HardLimit)
        }
    }
}

func extractProjectID(ctx *context.Context) (int64, error) {
    // 从URL中解析 /api/v2.0/projects/{id}/... 模式
    // 或从请求体中的 project_id 字段提取
    return 0, nil // 简化版，实际需解析路由
}

// estimateRequestSize 预估本次请求增加的存储量
func estimateRequestSize(ctx *context.Context) int64 {
    path := ctx.Request.URL.Path

    // Manifest PUT 请求——估算Manifest+所有新Blob的总大小
    if containsMethod(path, "/manifests/") && ctx.Request.Method == http.MethodPut {
        contentLength := ctx.Request.ContentLength
        // 检查请求头中Docker-Content-Digest和Layer列表
        return contentLength * 3 // 估算3倍（Blob可能比Manifest大）
    }

    // Blob Upload PATCH 请求——从Content-Range头获取大小
    if containsMethod(path, "/blobs/uploads/") && ctx.Request.Method == http.MethodPatch {
        contentRange := ctx.Request.Header.Get("Content-Range")
        if contentRange != "" {
            var start, end int64
            fmt.Sscanf(contentRange, "%d-%d", &start, &end)
            return end - start + 1
        }
    }

    // Blob Upload PUT 完成请求——从query参数digest推知大小
    if containsMethod(path, "/blobs/uploads/") && ctx.Request.Method == http.MethodPut {
        return ctx.Request.ContentLength
    }

    return 0
}

func abortWithError(ctx *context.Context, statusCode int, message string) {
    ctx.ResponseWriter.WriteHeader(statusCode)
    ctx.ResponseWriter.Write([]byte(fmt.Sprintf(`{"errors":[{"code":"%s","message":"%s"}]}`,
        http.StatusText(statusCode), message)))
    ctx.Abort(0, "")
}

func containsMethod(s, substr string) bool {
    return len(s) >= len(substr) && s[len(s)-len(substr):] == substr
}
```

### 3.4 步骤三：CVE阻止策略源码剖析

**目标**：理解漏洞阻止策略如何根据扫描报告决定是否允许镜像拉取。

```go
// vulnerable/handler.go —— CVE阻止策略中间件

package vulnerable

import (
    "context"
    "net/http"

    "github.com/goharbor/harbor/src/controller/scan"
    "github.com/goharbor/harbor/src/controller/project"
    "github.com/goharbor/harbor/src/lib/orm"
)

// severityWeights 将字符串Severity转为可比较的数值
var severityWeights = map[string]int{
    "Unknown":  0,
    "None":     0,
    "Low":      1,
    "Medium":   2,
    "High":     3,
    "Critical": 4,
}

// VulnerableFilter CVE阻止策略中间件
// 在用户拉取镜像（GET /v2/<repo>/manifests/<tag>）时触发
func VulnerableFilter() func(ctx *context.Context) {
    return func(ctx *context.Context) {
        // 仅拦截拉取操作
        if !isPullRequest(ctx) {
            return
        }

        // 从SecurityContext中获取项目ID
        projectID, ok := getProjectIDFromContext(ctx)
        if !ok {
            return
        }

        // 获取项目的CVE阻止策略配置
        projectCfg, err := project.Ctl.Get(ctx.Request.Context(), projectID,
            "prevent_vul", "severity", "vulnerability_allowlist")
        if err != nil {
            return // 获取失败不阻塞请求（宽容模式）
        }

        // 检查是否启用了阻止策略
        if projectCfg.PreventVul != "true" {
            return // 未启用，放行
        }

        // 获取制品的最新扫描报告
        artifactDigest := extractArtifactDigest(ctx)
        if artifactDigest == "" {
            return
        }

        report, err := scan.Ctl.GetReport(ctx.Request.Context(), artifactDigest,
            "application/vnd.scanner.adapter.vuln.report.harbor+json; version=1.0")
        if err != nil {
            // 扫描报告不存在——根据配置决定是否放行
            // 保守策略：无报告 = 不安全，阻止拉取
            abortWithCVEBlock(ctx, "no scan report available for this artifact")
            return
        }

        // 获取策略配置的严重级别阈值
        thresholdWeight := severityWeights[projectCfg.Severity]

        // 逐条检查漏洞是否超过阈值
        for _, vul := range report.Vulnerabilities {
            vulWeight := severityWeights[vul.Severity]

            if vulWeight >= thresholdWeight {
                // 检查白名单——如果在白名单中，即使超阈值也放行
                if isInAllowlist(vul.ID, projectCfg.VulnerabilityAllowlist) {
                    continue // 白名单豁免
                }

                // 阻止拉取
                abortWithCVEBlock(ctx,
                    fmt.Sprintf("artifact blocked by CVE policy: %s(%s) >= threshold %s",
                        vul.ID, vul.Severity, projectCfg.Severity))
                return
            }
        }

        // 所有漏洞都在阈值以下——放行
    }
}

func isPullRequest(ctx *context.Context) bool {
    return ctx.Request.Method == http.MethodGet &&
        containsPath(ctx.Request.URL.Path, "/manifests/")
}

func extractArtifactDigest(ctx *context.Context) string {
    // 从URL中提取digest参数，如 /v2/library/nginx/manifests/sha256:abc123
    parts := parsePathSegments(ctx.Request.URL.Path)
    if len(parts) >= 4 {
        return parts[len(parts)-1]
    }
    return ""
}

func isInAllowlist(cveID string, allowlistJSON string) bool {
    // 解析allowlist JSON，检查CVE ID是否在豁免列表中
    // 支持通配符匹配，如 CVE-2023-* 豁免所有2023年的CVE
    return false // 简化实现
}

func abortWithCVEBlock(ctx *context.Context, reason string) {
    ctx.ResponseWriter.WriteHeader(http.StatusPreconditionFailed) // 412
    ctx.ResponseWriter.Write([]byte(fmt.Sprintf(
        `{"errors":[{"code":"DENIED","message":"%s"}]}`, reason)))
    log.Warnf("[CVE-BLOCK] %s", reason)
    ctx.Abort(0, "")
}
```

### 3.5 步骤四：审计日志中间件实现

**目标**：记录所有API请求的操作者、操作类型、操作资源和操作时间。

```go
// auditlog/middleware.go —— 审计日志中间件

package auditlog

import (
    "context"
    "time"

    "github.com/goharbor/harbor/src/lib/log"
    "github.com/goharbor/harbor/src/pkg/audit"
    "github.com/goharbor/harbor/src/pkg/audit/model"
)

// AuditLogFilter 审计日志中间件
// 记录所有API请求——无论成功还是失败
func AuditLogFilter() func(ctx *context.Context) {
    return func(ctx *context.Context) {
        startTime := time.Now()

        // 提取操作信息
        auditEvent := buildAuditEvent(ctx)

        // 将audit事件发送到JobService异步处理
        // (不阻塞API请求)
        go func() {
            if err := audit.Mgr.Create(context.Background(), auditEvent); err != nil {
                log.Errorf("failed to record audit log: %v", err)
            }
        }()

        // 记录请求耗时
        elapsed := time.Since(startTime)
        log.Infof("[AUDIT] %s %s | user=%s | duration=%dms | status=%d",
            ctx.Request.Method, ctx.Request.URL.Path,
            auditEvent.Username, elapsed.Milliseconds(),
            ctx.ResponseWriter.Status())
    }
}

func buildAuditEvent(ctx *context.Context) *model.AuditLog {
    // 从SecurityContext提取当前用户信息
    username := "anonymous"
    if secCtx := getSecurityContext(ctx); secCtx != nil {
        username = secCtx.GetUsername()
    }

    return &model.AuditLog{
        ProjectID:  extractProjectID(ctx),
        Operation:  mapOperation(ctx.Request.Method, ctx.Request.URL.Path),
        Resource:   ctx.Request.URL.Path,
        ResourceType: mapResourceType(ctx.Request.URL.Path),
        Username:   username,
        OpTime:     time.Now(),
        UserAgent:  ctx.Request.Header.Get("User-Agent"),
        RequestID:  ctx.Request.Header.Get("X-Request-ID"),
        // Tag、RepoName等字段从URL中解析...
    }
}

// mapOperation 将HTTP方法+路径映射为可读的操作名
func mapOperation(method, path string) string {
    switch {
    case method == "POST" && contains(path, "/manifests/"):
        return "push"
    case method == "GET" && contains(path, "/manifests/"):
        return "pull"
    case method == "DELETE" && contains(path, "/manifests/"):
        return "delete-artifact"
    case method == "GET" && contains(path, "/tags/list"):
        return "list-tags"
    case method == "POST" && contains(path, "/scanners"):
        return "create-scanner"
    default:
        return strings.ToLower(method)
    }
}

func mapResourceType(path string) string {
    switch {
    case contains(path, "/manifests/"):
        return "artifact"
    case contains(path, "/blobs/"):
        return "blob"
    case contains(path, "/projects/"):
        return "project"
    case contains(path, "/scanners"):
        return "scanner"
    default:
        return "api"
    }
}
```

### 3.6 步骤五：安全策略集成测试

**目标**：编写测试脚本验证各安全中间件的实际行为。

```bash
#!/bin/bash
# security_test.sh —— Harbor安全策略验证脚本

HARBOR_URL="https://harbor.bank.com"
ADMIN_USER="admin"
ADMIN_PASS="Harbor12345"

echo "========== Test 1: Immutable Tag Protection =========="
# 创建不可变规则
curl -sk -u $ADMIN_USER:$ADMIN_PASS \
  -X POST "$HARBOR_URL/api/v2.0/projects/1/immutabletagrules" \
  -H "Content-Type: application/json" \
  -d '{
    "scope_selectors": {"repository": [{"kind": "doublestar", "pattern": "**"}]},
    "tag_selectors": [{"kind": "doublestar", "pattern": "latest"}],
    "disabled": false
  }'

# 尝试覆盖latest标签（应失败: 412 Precondition Failed）
echo "[TEST] Overwriting protected tag 'latest'..."
docker tag nginx:alpine $HARBOR_URL/test/immutable-test:latest
docker push $HARBOR_URL/test/immutable-test:latest 2>&1 | grep -q "412"
if [ $? -eq 0 ]; then
    echo "[PASS] Immutable tag protection works"
else
    echo "[FAIL] Immutable tag was overwritten!"
fi

echo ""
echo "========== Test 2: CVE Blocking Policy =========="
# 推送一个包含已知高危漏洞的镜像
echo "[INFO] Pushing vulnerable image for CVE blocking test..."
# 预期：拉取时被CVE策略阻止

echo ""
echo "========== Test 3: Quota Enforcement =========="
# 设置项目配额为100MB
curl -sk -u $ADMIN_USER:$ADMIN_PASS \
  -X PUT "$HARBOR_URL/api/v2.0/projects/1" \
  -H "Content-Type: application/json" \
  -d '{"storage_limit": 100}'

# 推送一个>100MB的镜像（应失败: 507 Insufficient Storage）
echo "[TEST] Pushing oversized image..."
# dd if=/dev/zero of=bigfile bs=1M count=200
# 预期：docker push在PUT manifest阶段被配额拦截

echo ""
echo "========== Test 4: Audit Log Completeness =========="
# 查询审计日志确认操作被记录
curl -sk -u $ADMIN_USER:$ADMIN_PASS \
  "$HARBOR_URL/api/v2.0/audit-logs?page_size=5" | python3 -m json.tool
```

### 3.7 常见陷阱与解决方案

**陷阱一：CVE白名单配置后仍被阻止拉取**

*现象*：将`CVE-2023-4586`加入项目的Vulnerability Allowlist后，拉取仍返回412。查看日志：`blocked by CVE policy: CVE-2023-4586(Low) >= threshold Medium`——看起来是在Low就被拦截了。

*根因*：CVE白名单检查**晚于**阈值比较。代码逻辑是：如果漏洞Severity ≥ 阈值，才检查白名单。但这里的"Low >= Medium"是False，应该是直接放行的。真正的问题是：白名单的匹配逻辑没有生效——白名单中的CVE ID写法是`CVE-2023-4586`，但扫描报告中的CVE ID可能是`CVE-2023-4586 (glibc)`包含额外信息，精确匹配失败。

*解决方案*：白名单匹配应使用**子串匹配**或**正则匹配**，不要用精确字符串相等：
```go
func isInAllowlist(cveID string, allowlist []string) bool {
    for _, pattern := range allowlist {
        if strings.Contains(cveID, pattern) {
            return true
        }
    }
    return false
}
```

**陷阱二：配额检查的死锁场景**

*现象*：两个用户同时在同一个项目push大镜像，两个push都在Blob Upload阶段通过配额检查（各有10GB剩余），但Manifest PUT时双双失败——因为两边加起来超过20GB了。

*根因*：配额检查是**乐观锁**模式——Blob Upload时不扣减配额，只在Manifest PUT时做最终检查。这导致从Blob Upload到Manifest PUT之间存在竞态窗口。

*解决方案*：短期方案——为每个Blob Upload预占配额（Reserve），Manifest PUT时确认（Commit）或释放（Rollback）。但会增加系统复杂度。当前Harbor默认行为是让后push的用户失败（类似于数据库的First-Committer-Wins策略）。

**陷阱三：审计日志在高并发下丢失**

*现象*：安全审计时发现某些时间段的API操作在审计日志中不完整——部分delete操作未记录。

*根因*：审计日志中间件使用`go func()`异步写入，不等待写入完成就返回。如果API请求响应非常快（如delete小制品），goroutine可能尚未写入就被系统回收。加上Redis队列瞬时压力大时可能丢弃消息。

*解决方案*：对于关键的写操作（push/delete/修改策略），审计日志应改为**同步写入**（至少等待写入确认），或使用可靠的消息队列（如Kafka）保证持久化。但会牺牲一定性能。

---

## 4 项目总结

### 4.1 安全中间件分层对比表

| 中间件 | 检查时机 | 检查维度 | 失败HTTP状态码 | 可旁路？ |
|--------|---------|---------|---------------|---------|
| SecurityFilter | 所有请求 | JWT Token有效性 + 角色解析 | 401 | 否 |
| QuotaFilter | POST/PUT/PATCH | 项目存储配额 | 507 | 否（SysAdmin可改配额） |
| ImmutableFilter | PUT Manifest（tag覆盖） | Tag是否被保护 | 412 | 否（Admin可删规则） |
| VulnerableFilter | GET Manifest（拉取） | CVE严重级别 vs 阈值 | 412 | 否（Admin可关策略） |
| ContentTrust | GET Manifest（拉取） | Notary签名验证 | 412 | 可配置跳过 |
| AuditLogFilter | 所有请求 | 操作审计记录 | N/A（不阻断） | 否 |

### 4.2 适用场景

1. **金融/医疗等高合规行业**——利用多层安全管道满足等保、SOX、HIPAA等合规审计要求，每层都产生可回溯的审计记录。
2. **多租户SaaS平台**——每个租户一个Harbor项目，配额防止租户间资源抢占，不可变标签防止租户误覆盖生产镜像。
3. **CI/CD安全门禁**——在CD流水线最后一步，由Harbor的CVE阻止策略充当"安全门"：只有Severity低于阈值的镜像才能被部署到生产环境。
4. **供应链安全合规**——通过Content Trust中间件验证镜像签名，确保只运行经过签名的可信镜像，防止供应链投毒攻击。
5. **内部威胁检测**——审计日志记录所有操作（包括管理员操作），与SIEM系统对接后可检测异常行为（如非工作时间大量删除镜像）。

### 4.3 不适用场景

1. **对实时性要求极高的边缘计算场景**——多层中间件的叠加会增加API响应延迟（通常5-15ms），边缘节点可能不适合全量安全管道。
2. **需要'四眼原则'审批的场景**——Harbor所有策略操作都是单个管理员即可执行，不支持多人审批后才能变更策略的工作流。

### 4.4 注意事项

1. **中间件顺序不可随意调整**：认证必须在最前面，否则未认证用户的请求会穿过配额/策略层，可能在错误信息中泄露系统状态。
2. **配额检查存在竞态窗口**：Blob Upload通过检查后到Manifest PUT之间的配额可能被其他用户消耗，建议保留20%配额缓冲区或启用配额预警。
3. **不可变规则对API删除不生效**：`DELETE /v2/<repo>/manifests/<digest>`可以绕过不可变规则（设计如此），需要通过审计日志事后检测异常删除行为。
4. **Webhook仅提供at-most-once语义**：默认重试3次后丢弃事件——关键安全事件建议对接双通道（Webhook + 定时审计日志轮询），防止单点丢失。
5. **CVE白名单粒度需谨慎**：使用通配符`CVE-2023-*`虽然方便，但可能掩护了真正的高危漏洞。建议精确匹配具体CVE ID并定期Review豁免列表。

### 4.5 常见陷阱速查表

| 陷阱 | 现象 | 根因 | 解决 |
|------|------|------|------|
| CVE白名单精确匹配 | 白名单不生效 | 报告CVE ID含额外文本 | 使用子串/正则匹配 |
| 配额乐观锁竞态 | 两个push之一失败 | Blob Upload到Put间无预占 | 保留20%缓冲+重试 |
| 审计日志异步丢失 | 部分操作未记录 | go func()未等完成 | 关键操作同步写入或Kafka |
| DELETE绕过不可变 | 受保护tag仍可间接删除 | 拦截的是覆盖而非删除 | 审计日志+告警+恢复流程 |
| Webhook事件丢失 | SIEM收不到通知 | 重试3次后丢弃 | 双通道（Webhook+日志轮询） |

### 4.6 深度思考

**问题一**：当前Harbor的CVE阻止策略是"镜像级别"的——如果镜像中任何一个组件的漏洞超过阈值，整个镜像被阻止拉取。如果要改为"组件级别"——拉取时不阻止，但Kubernetes部署时只替换有漏洞的组件而保留安全组件——这需要在Harbor安全模型中新增哪一层的检查？是否需要改动Registry的API接口？

**问题二**：Harbor的安全中间件链是线性的——如果要在不修改Harbor源码的情况下，插入一个自定义的安全中间件（如"操作频率限制"——每分钟push不超过10次），目前的扩展机制是否支持？如果支持，应该在`InitMiddlewares()`中插入到哪个位置？如果不支持，需要Harbor社区做什么样的架构改动来支持第三方安全中间件热插拔？

---

> 下一章预告：第39章将综合运用前面的源码知识，进行完整的Harbor自定义扩展开发实战。
