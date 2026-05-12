# 第18章：Harbor 认证与鉴权源码框架

## 1 项目背景

尚云金融科技（FinCloud）公司正在进行等保三级和安全审计整改，Harbor作为核心基础设施，需要满足一系列安全合规要求。安全部门对Harbor的认证鉴权提出了四个具体挑战。

**痛点一：Token过期时间理解不一致导致CI/CD生产事故。** 开发者用`docker login`获取的Token默认30天有效，CI流水线配置好后运转正常。但某天Jenkins任务突然报401 Unauthorized——排查发现是`helm registry login`获取的Token只有1小时有效（helm使用的是Registry Token而非Robot Token），同一个Harbor、同一个用户，为什么Token有效期不同？团队花了一下午才搞清楚"registry token"和"robot token"是两个概念。

**痛点二：机器人账户Token泄露后无法快速吊销。** 某外部合作商的CI机器人账户Token被误提交到公开GitHub仓库。安全团队在告警后要求"5分钟内吊销这个Token"。但Harbor Portal中没有"撤销Token"的按钮——团队紧急删除机器人账户然后重建，Token确实失效了，但所有依赖该机器人的CI流水线也全部中断，引发了一次P0事故。安全团队质疑：为什么Token没有独立的吊销机制？

**痛点三：自定义认证扩展入口不明确。** 公司要求"用户必须通过企业微信扫码登录Harbor Portal"，需要用企业微信OAuth2替代默认的OIDC流程。但开发团队研究了Harbor源码结构后，发现认证逻辑分散在`src/core/auth/`、`src/server/middleware/security/`、`src/common/security/`等多个模块中，修改点不明确——改错了可能破坏整个认证体系。

**痛点四：RBAC与Registry原生权限的混用导致越权。** 一个开发者在Harbor中只有"访客"角色（只能pull），但由于测试需要，管理员直接修改了Registry的配置文件给该用户加了push权限。结果这个"非标准权限"在下一次Harbor prepare后被覆盖——开发者的push权限丢失，发了40分钟工单追查"为什么我的权限突然没了"。

本章将深入Harbor认证鉴权的源码架构，解析Token Service机制、JWT签发/验证流程、LDAP/OIDC集成点和机器人账户的权限模型。

---

## 2 项目设计——剧本式交锋对话

**场景：安全架构评审会，白板上画满了认证流程图，安全部门的老陈被请来做技术讲解。**

**小胖**（打着哈欠）："Harbor的登录不就是用户名密码发到后端验证，然后返回一个Token吗？跟别的系统有啥区别？我每天docker login一次就完事了。"

**大师**："区别大了去了。小胖你可能不知道——Harbor有两种'登录'，两种走的是完全不同的认证流程，拿到的是不同形态的凭证。你每天用CLI感受到的只是其中一种。"

**流程A：Portal登录（Session-based, Web UI用）**

```
浏览器 → POST /c/login (username + password)
       → Core验证认证源（本地DB/LDAP/OIDC）
       → Core在Redis中创建Session (_sid:xxx, TTL=30min可续期)
       → 浏览器收到Cookie: _sid=xxx
       → 后续所有Web请求自动带上Cookie
       → 每次请求Core从Redis查Session确认身份
```

**流程B：docker/helm CLI登录（Token-based）**

```
docker client → GET /v2/（无凭证）
              → Harbor返回401 + WWW-Authenticate头
              → docker转而请求 /service/token?scope=...（带Basic Auth）
              → Core验证username:password（查同一套认证源）
              → Core签发JWT Token（嵌入scope、有效期）
              → docker保存Token到 ~/.docker/config.json
              → 后续registry请求带 Authorization: Bearer <JWT>
```

"关键区别：Portal登录拿的是Redis Session Cookie（服务端状态），CLI登录拿的是自包含的JWT Token（客户端状态）。这就是为什么重启Core容器后你不需要重新`docker login`——JWT Token还在客户端，而且自带签名，Registry不需要回Core验证。"

**技术映射**：Portal登录的核心代码在`src/server/middleware/security/auth_proxy.go`，处理`/c/login`端点。CLI Token签发在`src/core/service/token/token.go`的`MakeToken()`函数中。两者复用了同一套认证源（通过`src/common/dao/`层的`GetUser`函数），但返回的凭证形态完全不同——前者是Redis Session ID，后者是RS256签名的JWT。

**小白**："那JWT Token的具体结构是什么？我特别想知道scope字段是怎么控制'这个用户只能pull不能push'的——如果有bug，是不是可能拿到超出权限的Token？"

**大师**（在白板上写出JWT Payload结构）："JWT的核心权限字段就是`access`数组。看这个结构："

```json
{
  "iss": "harbor-token-issuer",
  "sub": "dev01",
  "aud": "harbor-registry",
  "exp": 1737000000,
  "iat": 1736900000,
  "nbf": 1736900000,
  "access": [
    {
      "type": "repository",
      "name": "order-platform/order-service",
      "actions": ["pull"]
    }
  ]
}
```

"Core在签发Token时，根据用户在数据库中的角色动态生成这个access列表。以`order-platform/order-service`仓库为例：
- **项目管理员**：actions = `["*"]`（pull, push, delete, scanner-pull）
- **开发者**：actions = `["push", "pull", "delete"]`
- **访客（仅pull）**：actions = `["pull"]`
- **未授权用户**：该仓库不出现在access数组中

Registry收到Token后，验证JWT签名（用Core签发的公钥），从access数组中判断当前操作是否被允许。如果用户请求push但access中只有pull，Registry返回403。"

**技术映射**：JWT Token的签发逻辑在`src/core/service/token/authutils.go`中的`MakeAndFilter()`函数。它先通过`src/pkg/permission/`层查询用户在该项目中的角色，再映射为标准Docker权限（`*`/`push`/`pull`/`delete`）。Token默认有效期为30分钟，配置项`token_expiration`在`harbor.yml`中。

**小胖**："那CLI Token的30天有效期和Helm的1小时有效期是怎么回事？同一个Core、同一个逻辑，怎么有效期还不一样？你们是不是在源码里写了两个分支？"

**大师**："有效期不是Core随机给的，而是由Token的**类型（Token Type）**决定的。Harbor实际上有四种Token类型："

| Token类型 | 有效期 | 获取方式 | 用途 | 配置来源 |
|----------|--------|---------|------|---------|
| Registry Token | 默认30分钟 | 自动（push/pull时） | docker/helm cli操作 | `harbor.yml: token_expiration` |
| Robot Token | 永久/自定义（创建时设`expires_at`） | 手动创建 | CI/CD长期自动化 | 创建API参数 |
| Portal Session | 默认30分钟（可续期） | Portal登录 | Web UI操作 | `harbor.yml: session_timeout` |
| Scanner Token | 固定24小时 | JobService自动刷新 | Trivy扫描Registry时认证 | 源码硬编码`24h` |

"Helm拿到的1小时Token，不是Registry Token错了——而是你用的`helm registry login`实际上触发了Portus（旧版）的某些逻辑，或者helm自己维护了Token缓存策略。Harbor v2.x中helm和docker共享同一套Registry Token逻辑，默认都是30分钟。"

**技术映射**：Token有效期常量定义在`src/core/service/token/authutils.go`的第42行：`defaultRegistryTokenExpiration = 30 * time.Minute`。Robot Token的过期时间在`src/controller/robot/model.go`中作为模型字段`ExpiresAt`存储，由创建API传入。Session超时配置在`src/server/middleware/security/idtoken.go`中读取`session_timeout`。

**小白**（皱眉思考）："如果我用同一个用户名在不同机器上`docker login`，多个Token同时有效吗？如果一台机器的Token被窃取了，怎么单独吊销它而不影响其他机器？"

**大师**："这是个在安全审计中频繁被问到的问题。答案是——**Harbor的Registry Token不支持单独吊销**。JPEG Token是无状态的，一旦签发，在过期前始终有效。这就是JWT的'双刃剑'：优点是Registry不需要每次回Core验证，缺点是你无法主动让它失效。"

"可行的缓解方案有三个：
1. **缩短Token有效期**：设`token_expiration: 15`（15分钟），这样即使泄露，攻击窗口也只有15分钟
2. **使用Robot Token替代个人账号**：Robot Token可以被整个删除（间接吊销），避免暴露个人密码
3. **Core端Token黑名单**：在Core中维护一个Redis Set记录被吊销的Token ID（`jti`），每次Token验证时检查黑名单——但这会引入额外的Redis查询，Harbor默认不开启此功能"

**技术映射**：JWT吊销的黑名单机制在`src/core/service/token/`中有预留的接口`InvalidTokenChecker`，但Harbor v2.12中默认实现为空（`NoOpChecker`）。如果需要启用，需要实现该接口并在Core启动时注入。这部分是Harbor社区正在讨论的Feature Request #18765。

---

## 3 项目实战

### 环境要求

| 组件 | 版本 | 说明 |
|------|------|------|
| Harbor | v2.12.x | 分析目标版本 |
| jq | 1.6+ | 解析JWT和API响应 |
| OpenSSL | 1.1+ | 验证JWT RS256签名 |
| curl | 7.68+ | 模拟认证流程 |
| redis-cli | 6.0+ | 查看Session数据 |

### 3.1 追踪一次 `docker login` 的完整认证链路

**目标**：手动复现docker login的完整HTTP交互，理解每个请求的作用。

```bash
# Step 1: 无凭证访问v2端点，获取WWW-Authenticate挑战
curl -v https://harbor.company.com/v2/ 2>&1 | grep -i "www-authenticate"

# 预期输出：
# < Www-Authenticate: Bearer realm="https://harbor.company.com/service/token",
#   service="harbor-registry", scope="registry:catalog:*"

# Step 2: 用Basic Auth请求Token（用户名:密码的Base64编码）
TOKEN=$(curl -s -u admin:Str0ng@Admin2024 \
  "https://harbor.company.com/service/token?service=harbor-registry&scope=repository:order-platform/order-service:pull,push" | \
  jq -r '.token')

echo "Token acquired (first 80 chars): ${TOKEN:0:80}..."
# 预期输出：eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJoYXJib3ItdG9rZW4t...

# Step 3: 解码JWT的Header和Payload（不验证签名）
echo "=== JWT Header ==="
echo $TOKEN | cut -d'.' -f1 | base64 -d 2>/dev/null | jq '.'
# {"alg": "RS256", "typ": "JWT", "kid": "xxxx"}

echo "=== JWT Payload ==="
echo $TOKEN | cut -d'.' -f2 | base64 -d 2>/dev/null | jq '.'
# 查看 sub(用户), access(权限), exp(过期时间)

echo "=== Expiry Info ==="
EXP=$(echo $TOKEN | cut -d'.' -f2 | base64 -d 2>/dev/null | jq -r '.exp')
echo "Token expires at: $(date -d @$EXP 2>/dev/null || date -j -f '%s' $EXP)"
# Token expires at: Mon Dec 22 14:30:00 UTC 2025

# Step 4: 用Token访问Registry验证权限
curl -s -H "Authorization: Bearer $TOKEN" \
  "https://harbor.company.com/v2/order-platform/order-service/tags/list" | jq '.'

# 预期输出（如果是管理员）：
# {
#   "name": "order-platform/order-service",
#   "tags": ["v1.0.0", "v1.0.1", "latest"]
# }
```

### 3.2 修改Token有效期并验证效果

**目标**：调整Registry Token有效期，测试超时后的行为。

```bash
# Step 1: 修改harbor.yml
cd /opt/harbor
cp harbor.yml harbor.yml.bak

# 将token_expiration改为5分钟（方便测试）
sed -i 's/token_expiration:.*/token_expiration: 5/' harbor.yml

# Step 2: 重新生成配置并重启
./prepare
docker compose restart harbor-core

# Step 3: 获取新Token并记录签发时间
TOKEN=$(curl -s -u admin:Str0ng@Admin2024 \
  "https://harbor.company.com/service/token?service=harbor-registry&scope=repository:order-platform/order-service:pull" | \
  jq -r '.token')

IAT=$(echo $TOKEN | cut -d'.' -f2 | base64 -d 2>/dev/null | jq -r '.iat')
EXP=$(echo $TOKEN | cut -d'.' -f2 | base64 -d 2>/dev/null | jq -r '.exp')
echo "Issued at: $IAT | Expires at: $EXP | Lifetime: $((EXP - IAT)) seconds"
# 预期输出：Issued at: 1736900000 | Expires at: 1736900300 | Lifetime: 300 seconds

# Step 4: 等待5分钟后用该Token访问Registry
sleep 310
curl -v -H "Authorization: Bearer $TOKEN" \
  "https://harbor.company.com/v2/order-platform/order-service/tags/list" 2>&1

# 预期输出：HTTP 401 Unauthorized
# < Www-Authenticate: Bearer realm="https://harbor.company.com/service/token", ...
# Token过期后需要重新获取
```

### 3.3 查看Redis中的Portal Session数据

**目标**：深入理解Session的存储结构和生命周期。

```bash
# Step 1: 通过Portal登录获取Session Cookie
# 在浏览器中登录后，从DevTools → Application → Cookies 复制 _sid 值
SID="abc123def456"

# Step 2: 在Redis中查看Session数据
docker exec redis redis-cli GET "_sid:$SID" | jq '.'

# 预期输出示例：
# {
#   "user_id": 1,
#   "username": "admin",
#   "email": "admin@company.com",
#   "sysadmin_flag": true,
#   "creation_time": "2025-12-22T14:00:00Z"
# }

# Step 3: 查看Session剩余有效时间
docker exec redis redis-cli TTL "_sid:$SID"
# 预期输出：1620 → 剩余27分钟（30分钟 - 已过3分钟）

# Step 4: 手动延长Session（模拟浏览器续期）
docker exec redis redis-cli EXPIRE "_sid:$SID" 1800
echo "Session extended by 30 minutes"

# Step 5: 列出当前所有活跃Session
docker exec redis redis-cli SCAN 0 MATCH "_sid:*" COUNT 10
# 返回活跃Session Key列表
```

### 3.4 创建和管理机器人账户Token

**目标**：通过API管理机器人账户，理解Token生命周期。

```bash
# Step 1: 创建机器人账户（设置1小时后过期）
curl -X POST -u admin:Str0ng@Admin2024 \
  -H "Content-Type: application/json" \
  -d '{
    "name": "robot$order-platform+gitlab-ci",
    "description": "GitLab CI automation for order-platform",
    "access": [
      {
        "resource": "repository",
        "action": ["pull", "push"]
      }
    ],
    "expires_at": '$(date -d "+1 hour" +%s)'
  }' \
  "https://harbor.company.com/api/v2.0/projects/1/robots" | jq '.'

# 预期输出（注意：Token仅此一次可见！）：
# {
#   "id": 42,
#   "name": "robot$order-platform+gitlab-ci",
#   "secret": "JxO8Kp2M...",    ← 立即保存！之后不再返回
#   "expires_at": 1736903900
# }

# Step 2: 用机器人Token登录（用户名格式为robot$<project>+<name>）
echo "JxO8Kp2M..." | docker login harbor.company.com \
  -u "robot\$order-platform+gitlab-ci" --password-stdin

# 预期输出：Login Succeeded

# Step 3: 查询机器人列表
curl -s -u admin:Str0ng@Admin2024 \
  "https://harbor.company.com/api/v2.0/projects/1/robots" | \
  jq '.[] | {id: .id, name: .name, expired: .expires_at, disabled: .disabled}'

# Step 4: 禁用机器人（间接吊销，但不删除）
curl -X PATCH -u admin:Str0ng@Admin2024 \
  -H "Content-Type: application/json" \
  -d '{"disabled": true}' \
  "https://harbor.company.com/api/v2.0/projects/1/robots/42"

# Step 5: 删除机器人（彻底吊销Token）
curl -X DELETE -u admin:Str0ng@Admin2024 \
  "https://harbor.company.com/api/v2.0/projects/1/robots/42"
```

### 3.5 OIDC认证集成关键源码路径

**目标**：理解OIDC集成涉及的源码文件和修改点，为自定义认证扩展做准备。

```bash
# Harbor Core中OIDC认证的关键源码文件
# （以下路径基于Harbor v2.12源码树）

# 1. OIDC Provider 初始化与Token验证
# src/core/auth/oidc/
#   ├── auth.go          # OIDC Provider注册、UserInfo获取
#   ├── user.go          # IDP用户 → Harbor本地用户的映射逻辑
#   └── secret.go        # Client Secret的安全存储

# 2. OIDC登录回调路由
# src/server/middleware/security/
#   └── oidc.go           # /c/oidc/callback 的HTTP处理器

# 3. 通用认证中间件
# src/server/middleware/security/
#   ├── auth_proxy.go     # Session验证（Portal请求）
#   ├── secret.go         # CSRF Token生成与验证
#   └── idtoken.go        # ID Token解析

# 4. 权限校验层
# src/pkg/permission/
#   └── evaluator/        # RBAC权限评估器
```

**OIDC登录完整流程**：
```
1. 用户点击Portal上的 "Login via OIDC"
2. Portal重定向浏览器到 {oidc_provider}/authorize?client_id=xxx&redirect_uri=...
3. IDP验证用户身份 → 重定向回 Harbor: /c/oidc/callback?code=xxx&state=yyy
4. Core的oidc.go handler收到callback:
   a. 用code换token（POST {oidc_provider}/token + client_secret）
   b. 用access_token获取userinfo（GET {oidc_provider}/userinfo）
   c. 调用user.go: 查找或创建Harbor本地用户（匹配email/subject）
   d. 创建Redis Session → Set-Cookie: _sid
   e. 重定向浏览器到Portal首页
5. 后续请求：Core从Redis验证Session
```

### 3.6 可能遇到的坑

**坑1：OIDC集成后admin无法用本地账号登录**

| 项目 | 内容 |
|------|------|
| **症状** | 配置OIDC后，Portal登录页面默认跳转到OIDC登录，admin找不到本地登录入口。 |
| **根因** | `harbor.yml`中`auth_mode: oidc_auth`后，Portal默认展示OIDC登录按钮。本地登录入口隐藏在底部"使用本地账号登录"链接中，不够显眼。 |
| **解决** | 直接访问`https://harbor.company.com/account/sign-in?login_type=local`，或在Portal登录页底部点击"本地账号登录"。应急时可用admin+密码通过API操作。 |

**坑2：多个Core实例JWT签名验证失败**

| 项目 | 内容 |
|------|------|
| **症状** | Core做了水平扩展（3副本），用户偶尔收到401 Unauthorized。同一个Token在Core-1签发但被Core-2/3拒绝。 |
| **根因** | JWT由Core用`/etc/core/private_key.pem`签发，Registry用`/etc/core/public_key.pem`验证。如果Core实例间的密钥对不一致（各自生成了不同的密钥），签发的Token无法被Registry正确验证。 |
| **解决** | 在docker-compose.yml中统一密钥挂载：所有Core实例的`volumes`指向同一个Secret。在K8s中用Secret资源挂载，确保同一namespace内共享。 |

**坑3：docker push进行中Token过期导致中断**

| 项目 | 内容 |
|------|------|
| **症状** | Docker push一个大镜像（如2GB），持续10分钟。在8分钟左右时报错`unauthorized: authentication required`，push中断。 |
| **根因** | Docker CLI在每次push/pull会话开始时获取一次Token，中途不会自动刷新。如果Token在push中途过期，Registry返回401，Docker CLI终止push，不会自动重新认证。 |
| **解决** | 增大`token_expiration`到60分钟（覆盖最长push时间）。或者改用增量push——同一个镜像的后续push通常只传差异层（Blob），耗时更短。 |

**坑4：Helm Chart推送时认证方式不一致**

| 项目 | 内容 |
|------|------|
| **症状** | `helm registry login`成功，但`helm push`时报403 Forbidden。 |
| **根因** | Helm 3.8+使用ORAS库推送Chart（通过OCI协议），需要`push`权限。而`helm registry login`只是验证了用户名密码——获取的Token可能只包含`pull`权限（如果用户是访客角色）。 |
| **解决** | 确认用户至少是"开发者"角色（在项目→成员中设置）。Helm push实际上是OCI Artifact push，权限要求与docker push一致。 |

**坑5：LDAP用户首次登录时Harbor不会自动同步组**

| 项目 | 内容 |
|------|------|
| **症状** | 公司配置了LDAP认证，用户导入成功，但LDAP Group信息没有自动映射到Harbor的项目成员。 |
| **根因** | Harbor的LDAP集成分为"认证"和"组同步"两个独立流程。认证（登录时验证用户名密码）会自动创建用户，但组同步需要额外配置`ldap_group_*`参数并设置定时同步。 |
| **解决** | 在harbor.yml中配置`ldap_group_base_dn`和`ldap_group_search_filter`，然后设置`ldap_group_search_scope`并确保Core可以访问LDAP的group对象类。首次同步可通过Portal的"LDAP组"页面手动触发。 |

---

## 4 项目总结

### 4.1 四种认证模式完整对比

| 维度 | Portal Session登录 | CLI Token登录 | Robot Token认证 | OIDC SSO登录 |
|------|-------------------|--------------|----------------|-------------|
| 触发场景 | 浏览器访问Portal | docker/helm login | CI/CD脚本中设置env | Portal OIDC按钮 |
| 凭证输入 | 表单username+password | CLI交互式或Stdin | 创建时一次性生成secret | IDP重定向认证 |
| 验证方 | Core→DB/LDAP/OIDC | Core→DB/LDAP/OIDC | Core→DB（Robot表） | Core→IDP Token交换 |
| 最终凭证形态 | Cookie: `_sid=<token>` | JWT Bearer Token | Robot Name + Secret | Cookie: `_sid=<token>` |
| 凭证存储位置 | Redis（服务端） | `~/.docker/config.json` | 环境变量/Secret Manager | Redis（服务端） |
| 默认有效期 | 30min（可续期） | 30min（可配置） | 永久（可设expires_at） | 30min（可续期） |
| 是否可单独吊销 | ✅ 删Redis Key | ❌ 需等过期 | ✅ 删/禁用Robot | ✅ 删Redis Key |
| 适用广度 | 仅Web Portal | docker/helm/等CLI | docker/helm/API | 仅Web Portal |
| RBAC粒度 | 用户全局角色+项目角色 | 同用户 | 仅创建时指定的action | 同用户 |

### 4.2 适用场景与不适用场景

**适用场景：**
- **企业SSO/OIDC统一认证**：复用企业已有的统一身份认证体系（如Okta、Auth0、Keycloak），消除独立密码管理
- **CI/CD自动化认证**：机器人账户Token免除定期更新密码的麻烦，且Token与机器人身份绑定便于审计溯源
- **安全合规加固**：理解JWT scope后可自行验证"Token是否有越权风险"；通过调整Token有效期缩小攻击窗口
- **审计溯源**：每个Token → 对应用户或机器人 → 审计日志可追溯到操作人
- **多租户权限隔离**：基于RBAC+access数组，实现同一Harbor中不同团队的精细化权限控制

**不适场景：**
- **临时借权限**：即使内部用户临时需要push（平时只有pull），需要管理员手动调整角色——Harbor不原生支持"临时提权"机制
- **Token联动吊销**：如果一个用户的密码被重置，已签发的JWT Token不会自动失效——需要额外实现Token黑名单机制

### 4.3 注意事项

1. **机器人Token创建后只显示一次**——务必在创建时通过API响应捕获或重定向到安全存储（如Vault）。如果丢失，只能删除机器人重建
2. **CLI Token有效期不宜过长**（建议Registry Token < 2小时）——JWT Token一旦泄露，在有效期内可被任何持有者使用。缩短有效期即使泄露影响范围可控
3. **Session存储在Redis中**——如果Redis故障且未开启RDB/AOF持久化，所有用户Session丢失，全部需要重新登录
4. **JWT签名私钥是所有Core实例共享的安全根**——如果私钥泄露，攻击者可以伪造任意权限的Token。私钥必须严格保护（K8s Secret + 最小访问权限）
5. **OIDC和LDAP的"自动创建用户"可能导致未预期账号激增**——建议配置`oidc_user_claim`和`ldap_uid`参数过滤可登录的用户范围，避免全员可登

### 4.4 常见故障速查表

| 故障现象 | 根因 | 快速解决 |
|---------|------|---------|
| docker push中途报401 | Token超时（push耗时长） | 增加`token_expiration`到60分钟 |
| helm push报403 | 用户角色只有pull权限 | 在项目中升级为"开发者"角色 |
| Portal登录后立即跳回登录页 | Redis不可达（Session写入失败） | `docker compose restart redis` |
| OIDC登录后白屏/无响应 | IDP证书过期或redirect_uri不匹配 | 检查IDP配置 + Core日志 |
| Robot Token突然失效 | Robot到达`expires_at`或被禁用 | 查询Robot状态并延长过期时间 |
| 多Core实例部分返回401 | 私钥不一致 | 统一所有Core实例的`private_key.pem` |
| CLI Token在Portal中不可见 | Session和Token是两套凭证 | 正常现象——CLI Token是自包含JWT |

### 4.5 深度思考题

1. **如果公司的IDP不支持OIDC但支持SAML2，Harbor能否通过修改源码接入SAML认证？需要在哪些模块做改造？请设计一个最小改造方案，包括需要新增/修改的Go文件清单和核心接口变更。**

2. **设计并实现一个"Token黑名单"机制：Core签发的JWT Token默认不可单独吊销。如果要在Core中加入一个Redis-backed的黑名单检查（每次Registry请求时验证），需要在哪些代码位置插入检查逻辑？这种设计会引入什么副作用（性能、一致性、可用性）？**

---

> 下一章预告：第19章将深入Harbor的数据模型层——PostgreSQL Schema全览与Redis缓存策略。
