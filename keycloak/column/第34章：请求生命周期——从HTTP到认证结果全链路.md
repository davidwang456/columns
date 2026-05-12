# 第34章：请求生命周期——从HTTP到认证结果全链路

## 1 项目背景

某金融科技公司的安全团队在一次季度审计中登录Keycloak管理后台查看事件日志时，发现了一个让人后背发凉的异常——两条登录成功事件的时间戳相差不足5毫秒，但后登录的用户竟然获得了先登录用户的Session。一个用户名为`zhangsan`的员工打开浏览器登录系统，与此同时另一个用户`lisi`也在登录，结果`lisi`的浏览器竟然拿到了`zhangsan`的Session Cookie，直接以`zhangsan`的身份进入了财务审批系统。

安全团队立即展开排查。他们翻遍了Keycloak的Events表（`EVENT_ENTITY`），能看到的只有`LOGIN`事件的时间戳、用户ID和IP地址——HTTP请求的入口和出口数据一清二楚，但中间黑盒里发生了什么，没有任何记录。是哪一步导致了Session交叉？是AuthenticationSession→UserSession转换时出现了竞态？是Infinispan缓存写入非原子操作？还是Cookie写入时机不对？现有的日志体系只能看到"谁在什么时候登录了"，无法回答"这个Session是怎么一步步创建出来的"。

团队的痛点集中在四个层面：第一，Keycloak的11个HTTP处理阶段（Phase Handler）鲜有文档详细描述其执行顺序和职责边界，开发者只知请求进来了、Token出去了，中间链路是迷雾；第二，`AuthenticationManager.authenticateIdentityCookie()`方法中嵌套了多层令牌校验、Session状态检查、Cookie过期等回调逻辑，代码阅读如同剥洋葱，层层深入却难以建立全景图；第三，AuthenticationSession（临时认证会话）→UserSession（用户全局会话）→AuthenticatedClientSession（客户端会话）三级会话的创建时机和关联关系不清楚——哪个先创建？哪个在什么时候销毁？它们之间是一对一还是一对多？第四，`KEYCLOAK_SESSION` cookie到底是什么时候写入浏览器端的？是在认证成功的那一刻，还是在Token签发之后？

只有彻底理解Keycloak的请求处理全链路——从HTTP请求到达JAX-RS端点，到Authentication Flow执行引擎逐步遍历Execution List，到三级Session的逐层创建，到Cookie的写入与Token的签发，最后到HTTP响应返回到客户端——才能定位Session交叉的根因，建立代码级的安全防护。

---

## 2 项目设计——剧本式交锋对话

**小胖**（抱着一箱快递走进会议室，把箱子往桌上一放）：大师，我昨天研究Keycloak的源码，看得眼花缭乱——从HTTP请求进去到Token出来，中间经过十几个类，几十个方法调用，跟走迷宫似的。我突然想到，这不就像快递单的全生命周期吗？收件→分拣→运输→派送→签收，每个环节都在单子上盖章。咱们能不能在Keycloak的每个关键环节也"盖个章"——打一行日志，出问题的时候把"快递单"从头到尾查一遍？

**大师**（笑着指了指快递箱）：小胖你这个比喻非常好，我们今天就顺着这个思路把Keycloak的请求生命周期彻底拆解。先把这个"快递单"上的每一个章对应到源码中的关键节点。

快递单的"收件"就是HTTP请求到达`RealmsResource`——JAX-RS的根资源类。Keycloak的所有REST端点都挂在这个资源类下面，它根据URL路径中的realm参数分发请求。比如`/realms/demo-realm/protocol/openid-connect/auth`，`RealmsResource`先解析出realm名`demo-realm`，再根据路径后缀`/protocol/openid-connect/auth`找到对应的子资源——OIDC登录协议服务类。

快递单的"分拣"就是`AuthorizationEndpoint.buildAuthorization()`——授权端点入口。这里会做一整套校验：client_id是否存在、redirect_uri是否在客户端注册的白名单中、response_type和scope是否合法。任何一项校验失败，请求直接在这一步被拒绝——就像快递单地址不对，分拣中心直接退回。

快递单的"运输"就是`AuthenticationProcessor.authenticateOnly()`，它创建`AuthenticationFlow`实例并调用`processFlow()`——这就是Keycloak的认证流水线。流水线上每一个工位就是一个Execution（认证执行器），比如`UsernamePasswordForm`负责校验用户名密码、`ConditionalOtpForm`负责OTP二次验证。Flow引擎按照Authentication Flow配置的树状结构逐个调用Execution，每个Execution返回一个`Response`——可能是challenge（比如返回登录表单让用户填写）、可能是success（校验通过，继续下一个Execution）、也可能是failure（认证失败）。

快递单的"派送"就是认证成功后UserSession的创建。在`AuthenticationProcessor.attachSession()`中，`UserSessionManager.createUserSession()`创建UserSessionModel——这是用户登录状态的持久载体，存入Infinispan的`sessions`缓存并同步写入数据库。同时创建AuthenticatedClientSessionModel——这是用户与某个具体客户端的绑定关系，存入`clientSessions`缓存。

快递单的"签收"就是`TokenManager`将UserSession中的信息编码为JWT Token，调用`session.tokens().encode()`→`JWSBuilder.sign()`用Realm配置的RSA/EC私钥签名，最后将Token、Session Cookie通过HTTP Response返回给客户端。

**小胖**（眼睛一亮）：所以出问题时，我可以按照这个链路逐个检查——看看是"分拣"阶段校验没通过，还是"运输"阶段某个Execution抛异常了，还是"签收"时签名失败了？

**大师**：没错。这就是请求全链路追踪的价值——把每个环节的"章"都盖上，出问题时定位到具体是哪一步。

---

**小白**（在笔记本上飞快地画着时序图）：大师，我想追问三个细节。第一，OIDC授权码流程在Keycloak内部对应的完整代码路径是什么？第二，`AuthenticationFlow.processFlow()`是如何遍历Execution List的——它是广度优先还是深度优先？第三，UserSession和ClientSession的创建时机到底在哪里——是在认证成功的那一瞬间就创建了，还是等到Token生成的时候才创建？

**大师**：小白问到点子上了。我逐个拆解。

第一个问题，OIDC授权码流程的完整代码路径：

```
HTTP GET /realms/{realm}/protocol/openid-connect/auth?client_id=...&redirect_uri=...&response_type=code
  → RealmsResource (JAX-RS根端点分发)
    → OIDCLoginProtocolService (OIDC协议服务)
      → AuthorizationEndpoint.buildAuthorization() (校验client_id/redirect_uri/scope)
        → AuthenticationManager.authenticateIdentityCookie() (检查是否已有登录Cookie)
          → 如果已登录且prompt!=login → 直接生成code，跳过认证流程
          → 如果未登录 → 创建AuthenticationSessionModel → 返回登录表单(FTL模板)
            → 用户提交用户名/密码 → LoginActionsService.authenticateForm()
              → AuthenticationProcessor.authenticateOnly() 
                → AuthenticationFlow.processFlow() (逐个执行Authentication Execution)
                  → UsernamePasswordForm.authenticate() (密码校验)
                  → ConditionalOtpForm (如果需要OTP)
                  → ... (Flow树中的其他Execution)
                → AuthenticationProcessor.attachSession() 
                  → UserSessionManager.createUserSession() (创建UserSession)
                → AuthenticationProcessor.finishAuthentication()
                  → AuthenticationManager.redirectAfterSuccessfulFlow()
                    → 生成authorization_code → 302重定向到redirect_uri?code=xxx
                    
POST /realms/{realm}/protocol/openid-connect/token (code换Token)
  → TokenEndpoint.processGrantRequest()
    → 校验authorization_code有效性+PKCE code_verifier
    → TokenManager.responseBuilder()
      → session.tokens().encode(accessToken) → JWSBuilder.sign() (RSA签名)
      → session.tokens().encode(idToken) → JWSBuilder.sign()
      → session.tokens().encode(refreshToken) → JWSBuilder.sign()
    → 返回JSON {access_token, id_token, refresh_token, ...}
```

第二个问题，`AuthenticationFlow.processFlow()`的执行模型。它既不是广度优先也不是深度优先，而是**基于树的前序遍历+Required/Alternative/Conditional状态机**。Keycloak的Authentication Flow是一个树形结构，节点类型有三种：Flow（容器节点，包含子Execution或子Flow）和Execution（叶子节点，每个Execution对应一个具体的Authenticator实现）。每个Flow节点有一个`Requirement`属性——`REQUIRED`表示必须成功才能继续，`ALTERNATIVE`表示任意一个成功即可，`CONDITIONAL`表示满足条件才执行。引擎从根Flow开始，按配置顺序遍历子节点：遇到REQUIRED Execution→执行→成功则继续下一个，失败则整个Flow失败；遇到ALTERNATIVE Execution→执行→成功则跳过同级其他ALTERNATIVE，失败则尝试下一个；遇到子Flow→递归进入。

第三个问题，UserSession的创建时机。答案是：**在`AuthenticationProcessor.attachSession()`中创建**——也就是在Authentication Flow执行完毕、所有Required Execution都成功之后、但在生成authorization code**之前**。关键代码在`AuthenticationProcessor.java:1146-1162`：

```java
public static ClientSessionContext attachSession(
    AuthenticationSessionModel authSession, UserSessionModel userSession, 
    KeycloakSession session, RealmModel realm, ...) {
    
    if (userSession == null) {
        userSession = session.sessions().getUserSession(realm, authSession.getParentSession().getId());
        if (userSession == null) {
            userSession = new UserSessionManager(session).createUserSession(
                authSession.getParentSession().getId(), realm, 
                authSession.getAuthenticatedUser(), username, ...);
        }
    }
    // ... 然后创建或复用ClientSession
}
```

ClientSession则是在同一方法的后续步骤中通过`TokenManager.attachAuthenticationSession()`创建的。也就是说：**UserSession和ClientSession都是在认证成功后、返回authorization_code之前同步创建的，不是在Token端点被调用时才创建**。Token端点拿到code后，通过code定位到已存在的UserSession，然后直接基于它生成JWT。

**小白**（追问）：那AuthenticationSessionModel和UserSessionModel之间是什么关系？一个是一对一还是一对多？

**大师**：这是一个很重要的问题。AuthenticationSessionModel是**临时的、未认证的会话**——用户还在登录过程中，还没有完成认证。它存储在Infinispan的`authenticationSessions`缓存中，默认5分钟TTL。UserSessionModel是**持久的、已认证的会话**——用户已经通过了所有认证步骤。它们之间的关系是：一个`RootAuthenticationSessionModel`（根认证会话，存储在Cookie `AUTH_SESSION_ID`中）可以包含多个`AuthenticationSessionModel`（每个客户端一个），一个UserSession可以关联一个RootAuthenticationSession的ID——这个ID字段存储在UserSession的notes中。但它们是两个独立的数据结构，分别存储在不同的Infinispan缓存中。

**大师总结第一轮技术映射**：

- 快递单收件→分拣→运输→派送→签收 → HTTP Request → JAX-RS Routing → Auth Endpoint → Authentication Flow → Session Creation → Token Signing
- 快递柜里的包裹要凭取件码领取 → AuthenticationSession是临时凭证，通过code/ClientSessionCode定位
- 快递单上每个环节的章都是独立存在的 → 每个阶段的日志应该打印在对应的方法入口/出口
- 签收后才算正式交付 → JWT生成+签名完成才算是Token正式签发

---

**小胖**（第二轮，皱着眉头）：大师，我想到一个更危险的问题。刚才说的Session交叉Bug——如果两个用户几乎同时登录，`attachSession()`中先检查`session.sessions().getUserSession()`返回null，然后调用`createUserSession()`。但在高并发下，两个线程可能同时检查都为null，然后都去创建——这不就是典型的check-then-act竞态条件吗？Infinispan缓存写入是原子操作吗？

**大师**（神色变得严肃）：小胖你提到了这一章最核心的源码级安全问题。你说得对，并发Session创建确实存在竞态风险。`UserSessionManager.createUserSession()`内部调用`session.sessions().createUserSession()`，底层通过Infinispan的`cache.putIfAbsent()`实现——这是一个原子操作，第二个并发请求的putIfAbsent会返回失败。但问题的微妙之处在于：Keycloak在`AuthenticationManager.redirectAfterSuccessfulFlow()`中有一段关键代码（第952-958行）：

```java
if (!compareSessionIdWithSessionCookie(session, userSession.getId())) {
    AuthResult result = authenticateIdentityCookie(session, realm, false);
    if (result != null) {
        UserSessionModel oldSession = result.session();
        if (oldSession != null && !oldSession.getId().equals(userSession.getId())) {
            session.sessions().removeUserSession(realm, oldSession);
        }
    }
}
```

这段逻辑的本意是：如果请求携带的`KEYCLOAK_SESSION` cookie中记录的是旧Session，而当前认证生成了新Session，则删除旧Session。但在高并发下，`KEYCLOAK_SESSION` cookie的值和Infinispan中的Session状态之间可能存在短暂的`不一致窗口`——这就是Session交叉Bug的典型根因之一。

**小白**：那认证失败时的回滚机制呢？如果前三个Execution都成功了，第四个失败了，前面已经执行的Execution需要回滚吗？

**大师**：Keycloak的Flow执行引擎对失败的处理是**标记失败并停止遍历**，而非回滚。每个Execution是可插拔的SPI实现，没有统一的"撤销"接口。比如`UsernamePasswordForm`校验成功（记录了"用户名密码校验通过"），随后`ConditionalOtpForm`失败了——前面UsernamePasswordForm的成功状态不会被撤销，只是整个Flow标记为failure。这带来一个潜在问题：某些Authenticator在认证过程中可能修改了用户状态（比如`BruteForceProtector`增加了失败计数器），Flow失败后这些副作用依然存在。这就是为什么BruteForceProtector在每次认证失败时需要增加失败计数——它依赖的是"认证失败的累积次数"，而非"单次Flow的成功/失败"。

另外值得一提的还有Session的重放攻击防御。`AuthenticationManager.createLoginCookie()`在第869行对UserSession ID做了SHA-256哈希：

```java
String sessionCookieValue = sha256UrlEncodedHash(session.getId());
keycloakSession.getProvider(CookieProvider.class).set(CookieType.SESSION, sessionCookieValue, sessionCookieMaxAge);
```

也就是说，写入浏览器的`KEYCLOAK_SESSION` cookie值**不是**UserSession ID的原文，而是它的SHA-256哈希。这是为了防止UserSession ID泄漏后攻击者伪造Cookie。

**大师总结第二轮技术映射**：

- 并发check-then-act → 原子操作putIfAbsent兜底，但Cookie不一致窗口仍可能导致Session交叉
- 快递分拣错误导致包裹被退回 → Flow中任何Execution失败都会终止整个认证流程
- 包裹已签收但系统显示未签收 → Infinispan缓存与数据库之间存在最终一致性窗口
- 快递柜密码不是明文存储 → KEYCLOAK_SESSION Cookie存的是Session ID的哈希，防止反向推导

---

## 3 项目实战

### 环境准备

| 组件 | 版本/说明 |
|------|----------|
| Keycloak | 26.x，基于第33章源码调试环境（IDEA + 本地编译运行） |
| 调试工具 | IntelliJ IDEA Ultimate + Java 17+ |
| HTTP客户端 | Postman 或 curl |
| 压测工具 | Apache Bench (ab) 或 wrk |

确保Keycloak服务以debug模式运行在`http://localhost:8080`，realm名为`demo-realm`，客户端ID为`oms-frontend`（Public客户端，Standard Flow开启，PKCE为S256）。

### 步骤1：启动调试模式并设置关键断点

在IDEA中启动Keycloak（`quarkus dev`模式或直接启动`main`方法），按以下顺序在10个关键位置设置断点：

| # | 文件路径（相对于services模块） | 方法/行号 | 目的 |
|---|------|------|------|
| 1 | `services/src/.../resources/RealmsResource.java` | `@Path("{realm}")` 注解的子资源定位器方法 | HTTP请求入口，观察URL路由分发 |
| 2 | `services/src/.../endpoints/AuthorizationEndpoint.java` | `buildAuthorization()` | 授权端点，观察client_id/redirect_uri校验 |
| 3 | `services/src/.../managers/AuthenticationManager.java` | `authenticateIdentityCookie()` 方法体第一行 | 观察是否已有登录Cookie |
| 4 | `services/src/.../authentication/AuthenticationProcessor.java` | `authenticateOnly()` 第1105行 | Flow执行引擎入口 |
| 5 | `server-spi-private/src/.../authenticators/browser/UsernamePasswordForm.java` | `authenticate()` | 用户名密码校验的Authenticator实现 |
| 6 | `services/src/.../authentication/AuthenticationProcessor.java` | `attachSession()` 第1146行 | UserSession创建，观察竞态条件 |
| 7 | `services/src/.../authentication/AuthenticationProcessor.java` | `finishAuthentication()` 第1217行 | 认证完成，生成authorization_code |
| 8 | `services/src/.../endpoints/TokenEndpoint.java` | `processGrantRequest()` | Token端点处理code换Token请求 |
| 9 | `services/src/.../oidc/TokenManager.java` | `responseBuilder()` 中编码Token的位置（约1443行） | Token编码为JWT |
| 10 | `core/src/.../jose/jws/JWSBuilder.java` | `sign()` 方法中调用签名器的位置 | JWT签名，使用RSA/EC私钥 |

**提示**：不建议一次性激活所有10个断点——同时触发会使调试器响应极慢。可以先禁用全部，按步骤逐步激活。

### 步骤2：追踪一次完整登录流程

**步骤2.1：触发授权码流程第一步**

在浏览器中访问：

```
http://localhost:8080/realms/demo-realm/protocol/openid-connect/auth?client_id=oms-frontend&redirect_uri=http://localhost:3000/callback&response_type=code&scope=openid
```

或者使用curl模拟（便于观察HTTP交互）：

```bash
curl -v "http://localhost:8080/realms/demo-realm/protocol/openid-connect/auth?client_id=oms-frontend&redirect_uri=http://localhost:3000/callback&response_type=code&scope=openid" 2>&1 | head -50
```

**在IDEA中观察执行路径（按断点触发顺序）**：

**断点1触发** → `RealmsResource` 解析URI提取realm参数`demo-realm`。观察变量：`uriInfo.getPathParameters()`中包含`{realm=demo-realm}`。Keycloak的JAX-RS实现使用RESTEasy，根据`@Path`注解将请求路由到`OIDCLoginProtocolService`的对应子资源方法。

**断点2触发** → `AuthorizationEndpoint.buildAuthorization()`。在这一步：
- 从请求参数中提取`client_id`→查询`ClientModel`验证客户端存在且启用
- 提取`redirect_uri`→与客户端的Valid Redirect URIs列表进行匹配
- 初始化`AuthenticationSessionModel`（存储在Infinispan的`authenticationSessions`缓存中，TTL为Realm的`accessCodeLifespan`配置，默认300秒）
- 检查`prompt`参数——如果为`none`则尝试静默认证；如果存在有效Cookie则直接跳过认证流程

**断点3触发** → `AuthenticationManager.authenticateIdentityCookie()`。Keycloak从请求Cookie中提取`KEYCLOAK_IDENTITY` Cookie值，解密并校验其中的`IdentityCookieToken`：
- 该校验包括：Token类型（必须为`Serialized-ID`）、签名有效性、Session是否过期、Session是否仍然存在（检查Infinispan和数据库）、用户是否仍为启用状态
- 如果Cookie有效且用户已登录 → 生成authorization_code直接跳转到redirect_uri，跳过Step 4-7
- 如果Cookie无效或用户未登录 → 返回登录表单（FreeMarker模板）

**步骤2.2：用户提交登录表单**

当Keycloak返回登录表单（断点3分支为未登录），用户在浏览器中填写用户名/密码并提交。表单POST到`/realms/demo-realm/login-actions/authenticate`。

**断点5触发** → `UsernamePasswordForm.authenticate()`：
- 从表单参数中获取`username`和`password`
- 调用`session.userCredentialManager().isValid(realm, user, credential)`校验密码
- 校验成功→将`authenticatedUser`设置到`authenticationSession`中
- 校验失败→记录失败事件（`LOGIN_ERROR`），`BruteForceProtector`增加失败计数

**断点4触发** → `AuthenticationProcessor.authenticateOnly()`。在这里可以观察到Flow引擎的核心执行逻辑：

```java
AuthenticationFlow authenticationFlow = createFlowExecution(this.flowId, null);
Response challenge = authenticationFlow.processFlow();
```

`createFlowExecution()`根据`flowId`（通常是`browser`或`direct grant`）从Realm的配置中加载Authentication Flow树，构建`AuthenticationFlow`实例。`processFlow()`开始按照Flow树的前序遍历逐个执行子节点。在浏览器流程中，典型的Execution顺序是：

```
Browser Flow (REQUIRED)
├── Cookie (ALTERNATIVE) → 已检查，无有效Cookie
├── Identity Provider Redirect (ALTERNATIVE) → 无配置，跳过
└── Forms (ALTERNATIVE)
    └── Username Password Form (REQUIRED) → 用户已提交密码，校验通过
```

**断点6触发** → `AuthenticationProcessor.attachSession()`。这是Session交叉Bug的关键战场。方法执行的逻辑：

1. 首先尝试从Infinispan加载已有Session：`session.sessions().getUserSession(realm, authSession.getParentSession().getId())`
2. 如果返回null（首次登录的典型情况）→调用`UserSessionManager.createUserSession()`创建新Session
3. 如果返回非null但Session已过期 → 调用`userSession.restartSession()`重启
4. 如果返回非null且有效但用户不同 → 抛出`DIFFERENT_USER_AUTHENTICATED`错误（防御了Session冒用）
5. 随后调用`TokenManager.attachAuthenticationSession()`创建`AuthenticatedClientSessionModel`

**断点7触发** → `AuthenticationProcessor.finishAuthentication()`。关键操作：
- 调用`attachSession()`（触发断点6，实际UserSession在此创建）
- 生成`authorization_code`——通过`ClientSessionCode`生成，包含对`AuthenticationSessionModel`的引用
- 触发`event.success()`记录`LOGIN`事件
- 调用`AuthenticationManager.redirectAfterSuccessfulFlow()`→生成HTTP 302重定向到`redirect_uri?code=xxx&state=yyy`

**断点8触发** → `TokenEndpoint.processGrantRequest()`。当客户端收到code后，用以下curl请求换取Token：

```bash
curl -X POST http://localhost:8080/realms/demo-realm/protocol/openid-connect/token \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "client_id=oms-frontend" \
  -d "grant_type=authorization_code" \
  -d "code=<从回调URL中提取的code>" \
  -d "redirect_uri=http://localhost:3000/callback" \
  -d "code_verifier=<PKCE code_verifier>"
```

在断点8处观察：
- code被解析，通过`ClientSessionCode`定位到之前的`AuthenticationSessionModel`
- PKCE验证：计算`SHA256(code_verifier)`并与`AuthenticationSessionModel`中存储的`code_challenge`比对
- code的状态被标记为"已使用"，不允许重放

**断点9触发** → `TokenManager`中Token编码的位置。观察：
- `session.tokens().encode(accessToken)`将`AccessToken`对象序列化为JWT三部分
- `session.tokens().encodeAndEncrypt(idToken)`序列化ID Token
- `session.tokens().encode(refreshToken)`序列化Refresh Token

**断点10触发** → `JWSBuilder.sign()`。最终的签名操作：
- 通过`session.tokens().signatureAlgorithm(TokenCategory.ACCESS)`获取签名算法（通常是RS256）
- 通过`session.getProvider(SignatureProvider.class, "RS256").signer()`获取签名器
- 签名器使用Realm的RSA私钥对JWT的header+payload进行签名，生成JWT的第三部分（signature）

### 步骤3：关键数据结构关系梳理

通过断点观测，可以总结出三个核心数据结构的完整生命周期：

```
RootAuthenticationSessionModel (根认证会话，绑定浏览器)
  ├── 存储位置: Infinispan authenticationSessions 缓存
  ├── 标识方式: AUTH_SESSION_ID Cookie (带路由后缀)
  ├── 生命周期: 用户首次访问授权端点时创建 → 登录成功或超时后销毁
  └── 包含: 一个或多个 AuthenticationSessionModel

AuthenticationSessionModel (单个客户端的临时认证会话，5分钟TTL)
  ├── 创建时机: AuthorizationEndpoint.buildAuthorization() 中
  ├── 存储: 属于RootAuthenticationSession的clientSessions Map
  ├── 关键字段: authenticatedUser, client, redirectUri, authNotes
  ├── 关联: 通过ClientSessionCode (即authorization_code) 可定位
  └── 销毁: 认证完成(变为UserSession) 或 code被消费 或 TTL超时

UserSessionModel (用户全局会话)
  ├── 创建时机: AuthenticationProcessor.attachSession() 中的 createUserSession()
  ├── 存储: Infinispan sessions 缓存 + 数据库 OFFLINE_USER_SESSION 表
  ├── 标识: 通过 KEYCLOAK_SESSION Cookie (值为 sessionId 的 SHA-256 哈希)
  ├── 关联: 一个UserSession包含多个AuthenticatedClientSessionModel
  └── 销毁: 用户登出、Session超时、或管理员吊销

AuthenticatedClientSessionModel (客户端会话)
  ├── 创建时机: TokenManager.attachAuthenticationSession() 中
  ├── 存储: Infinispan clientSessions 缓存
  ├── 关联: 属于一个UserSession，绑定一个Client
  └── 销毁: 用户从此Client登出 或 UserSession被销毁
```

### 步骤4：自定义全链路日志追踪Filter

在Keycloak的SPI扩展机制下，可以注入两个JAX-RS Filter来实现全链路日志追踪。创建以下两个类：

**文件1**：`src/main/java/com/example/tracing/RequestTracingFilter.java`

```java
package com.example.tracing;

import jakarta.ws.rs.container.ContainerRequestContext;
import jakarta.ws.rs.container.ContainerRequestFilter;
import jakarta.ws.rs.ext.Provider;
import java.io.IOException;
import java.util.UUID;
import org.jboss.logging.Logger;

@Provider
public class RequestTracingFilter implements ContainerRequestFilter {
    
    private static final Logger logger = Logger.getLogger(RequestTracingFilter.class);
    
    @Override
    public void filter(ContainerRequestContext requestContext) throws IOException {
        String traceId = UUID.randomUUID().toString().substring(0, 8);
        requestContext.setProperty("traceId", traceId);
        requestContext.setProperty("startTime", System.currentTimeMillis());
        
        logger.infof("[trace=%s] >>> REQUEST: %s %s", 
            traceId,
            requestContext.getMethod(),
            requestContext.getUriInfo().getRequestUri().getPath()
        );
    }
}
```

**文件2**：`src/main/java/com/example/tracing/RequestTracingResponseFilter.java`

```java
package com.example.tracing;

import jakarta.ws.rs.container.ContainerRequestContext;
import jakarta.ws.rs.container.ContainerResponseContext;
import jakarta.ws.rs.container.ContainerResponseFilter;
import jakarta.ws.rs.ext.Provider;
import org.jboss.logging.Logger;

@Provider
public class RequestTracingResponseFilter implements ContainerResponseFilter {
    
    private static final Logger logger = Logger.getLogger(RequestTracingResponseFilter.class);
    
    @Override
    public void filter(ContainerRequestContext requestContext, 
            ContainerResponseContext responseContext) {
        String traceId = (String) requestContext.getProperty("traceId");
        Long startTime = (Long) requestContext.getProperty("startTime");
        
        long elapsed = startTime != null ? System.currentTimeMillis() - startTime : -1;
        
        logger.infof("[trace=%s] <<< RESPONSE: status=%d, elapsed=%dms", 
            traceId,
            responseContext.getStatus(),
            elapsed
        );
    }
}
```

**注册Filter**：在`src/main/resources/META-INF/services/`目录下创建`jakarta.ws.rs.ext.Providers`文件（或`org.keycloak.services.resources.KeycloakApplication`中注册）。

**观察日志输出**：重新编译启动Keycloak后，每次HTTP请求会输出类似以下日志：

```
[trace=a3f8b2c1] >>> REQUEST: GET /realms/demo-realm/protocol/openid-connect/auth
[trace=a3f8b2c1] <<< RESPONSE: status=200, elapsed=45ms
[trace=7d2e1f9a] >>> REQUEST: POST /realms/demo-realm/login-actions/authenticate
[trace=7d2e1f9a] <<< RESPONSE: status=302, elapsed=320ms
[trace=b4c5d6e7] >>> REQUEST: POST /realms/demo-realm/protocol/openid-connect/token
[trace=b4c5d6e7] <<< RESPONSE: status=200, elapsed=85ms
```

### 步骤5：分析Session交叉Bug的根因（实验复现）

**目标**：复现两个用户几乎同时登录时Session交叉的场景，定位根因源码。

**步骤5.1：准备测试数据**

在Keycloak中创建两个测试用户：`alice / Test@1234` 和 `bob / Test@5678`。

**步骤5.2：模拟并发登录**

使用Apache Bench发送并发登录请求：

```bash
# 准备alice的登录数据
echo "username=alice&password=Test@1234&client_id=oms-frontend&grant_type=password&scope=openid" > alice-login.txt

# 准备bob的登录数据
echo "username=bob&password=Test@5678&client_id=oms-frontend&grant_type=password&scope=openid" > bob-login.txt

# 并发发送两个用户的Direct Grant请求（模拟并发登录场景）
curl -X POST http://localhost:8080/realms/demo-realm/protocol/openid-connect/token \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d @alice-login.txt &

curl -X POST http://localhost:8080/realms/demo-realm/protocol/openid-connect/token \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d @bob-login.txt &
```

**步骤5.3：源码级根因分析**

在`AuthenticationProcessor.attachSession()`方法（第1146行）设置条件断点——仅在多个线程同时到达时触发。观察以下竞争窗口：

1. **线程A（alice）**：执行`session.sessions().getUserSession(realm, authSession.getParentSession().getId())` → 返回null
2. **线程B（bob）**：执行`session.sessions().getUserSession(realm, authSession.getParentSession().getId())` → 也返回null（因为A还未完成创建）
3. **线程A**：调用`createUserSession()` → 创建alice的UserSession（Infinispan `putIfAbsent`成功）
4. **线程B**：调用`createUserSession()` → Infinispan `putIfAbsent`因为key冲突返回已有Session（但这是alice的！）

在这个场景中，线程B虽然`putIfAbsent`返回失败，但如果代码没有充分处理这个冲突——比如没有重新从缓存加载Session做校验——就可能"借用"了第一步创建的alice的Session。这就是Session交叉Bug的精确根因。

**应对策略**：确认源码中`UserSessionManager.createUserSession()`返回的是`cache.putIfAbsent()`的结果并做了非空检查。在现代Keycloak版本（17+）中，该竞争已被`putIfAbsent`原子语义保护，但在如下极端场景下仍可能触发：`AUTH_SESSION_ID` Cookie被浏览器或代理错误复用，导致两个用户共享同一个RootAuthenticationSession。

### 可能遇到的坑

1. **断点太多导致调试器响应慢**：10个断点如果全部激活，每次触发IDEA需要挂起全部线程，响应时间可能超过10秒。建议每次只激活3-4个与当前追踪步骤相关的断点，其他保持禁用。

2. **Quarkus dev模式与生产模式的差异**：Quarkus dev模式使用热加载（hot reload），`@Provider`注解的Filter在dev模式下可能不会每次都重新注册。如果注入的Filter没生效，尝试完全重启Keycloak而非等待热加载。

3. **不同Keycloak版本间类路径可能变化**：本章断点位置基于Keycloak 26.x的源码结构。如果你使用的是更早版本（如22.x或之前），`AuthenticationSessionModel`的定义在`server-spi`模块而非独立模块，`CookieType`枚举的名称也可能不同。建议对照自己的源码搜索类名确认确切路径。

4. **Token Timeout导致调试超时**：在IDEA中单步调试时，Realm的`Access Token Lifespan`（默认5分钟）可能在你还未追踪到Token端点时就已经过期。可以在Keycloak Admin Console中将该值临时调整为30分钟（`Realm Settings → Tokens → Access Token Lifespan`）。

5. **Infinispan缓存在调试期间的状态不可见**：IDEA的Variables面板可以展开`KeycloakSession`对象，但其内部的Infinispan缓存引用（`session.sessions()`）在调试器中通常显示为代理对象，无法直接看到缓存内容。建议在代码中添加临时日志打印Session存储的内容。

### 测试验证

完成以上步骤后，验证以下三个场景，确保你对请求全链路有完整理解：

**场景1：正常密码登录**——按步骤2完整追踪一次，确认所有10个断点按顺序触发，UserSession在本应创建的时刻创建，Cookie在`createLoginCookie()`被调用时设置到响应中。

**场景2：已有Session时的静默登录**——先正常登录一次获取Cookie，然后携带Cookie再次访问授权端点。确认断点3(`authenticateIdentityCookie`)返回非null的`AuthResult`，断点4-7被跳过，直接到达断点7(`redirectAfterSuccessfulFlow`)生成code。

**场景3：Token刷新**——使用refresh_token调用token端点（`grant_type=refresh_token`）。确认断点8触发，断点9再次触发（生成新的access_token），但断点4-7不触发（不需要重新认证）。

绘制完整的调用链时序图：

```
时间轴 →

HTTP Request                         JAX-RS Router        Auth Endpoint        Auth Manager         Auth Processor        Token Endpoint
    │                                      │                    │                     │                     │
    ├─ GET /auth?client_id=... ───────────►│                    │                     │                     │
    │                                      ├─ @Path匹配 ──────►│                     │                     │
    │                                      │                    ├─ buildAuthorization │                     │
    │                                      │                    ├─ 校验client_id      │                     │
    │                                      │                    ├─ 创建AuthSession ──►│                     │
    │                                      │                    │                     ├─ identityCookie()  │
    │                                      │                    │                     │   → null            │
    │                                      │                    │ ◄─ 返回登录表单 ────┤                     │
    │ ◄─ 200 (login.ftl) ──────────────────┤                    │                     │                     │
    │                                      │                    │                     │                     │
    ├─ POST /login-actions/authenticate ──►│                    │                     │                     │
    │                                      ├─ form参数绑定 ────┤                     │                     │
    │                                      │                    │                     ├─ authenticateOnly() │
    │                                      │                    │                     ├─ processFlow()     │
    │                                      │                    │                     ├─ UsernamePwd.auth()│
    │                                      │                    │                     ├─ attachSession()   │
    │                                      │                    │                     │  └─ createUserSess │
    │                                      │                    │                     ├─ finishAuth()      │
    │                                      │                    │                     ├─ redirectSuccess() │
    │ ◄─ 302 (redirect_uri?code=xxx) ──────┤                    │                     │                     │
    │                                      │                    │                     │                     │
    ├─ POST /token (code→Token) ──────────►│                    │                     │                     ├─ processGrantReq
    │                                      │                    │                     │                     ├─ 校验code+PKCE
    │                                      │                    │                     │                     ├─ encodeToken(JWS)
    │ ◄─ 200 (JSON: access_token+...) ─────┤                    │                     │                     │
```

---

## 4 项目总结

### Keycloak请求生命周期全景图

```
┌─────────────────────────────────────────────────────────────────────┐
│                        HTTP Request Entry                            │
│  GET/POST /realms/{realm}/protocol/openid-connect/{endpoint}         │
└──────────────────────────┬──────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────────┐
│  JAX-RS Routing Layer (RealmsResource)                               │
│  根据@Path注解将请求分发到对应JAX-RS资源类                              │
│  关键类: RealmsResource, OIDCLoginProtocolService                     │
└──────────────────────────┬──────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────────┐
│  Authorization Endpoint (AuthorizationEndpoint.buildAuthorization)    │
│  - 校验 client_id, redirect_uri, response_type, scope                 │
│  - 创建 RootAuthenticationSessionModel + AuthenticationSessionModel   │
│  - 检查 Identity Cookie → 已登录则直接签发code                        │
│  - Identity Cookie常量: KEYCLOAK_IDENTITY                             │
└──────────────────────────┬──────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────────┐
│  Authentication Flow Engine (AuthenticationProcessor.authenticateOnly)│
│  - createFlowExecution(flowId) → AuthenticationFlow.processFlow()     │
│  - 按Flow树前序遍历执行每个Authentication Execution                    │
│  - 关键SPI: Authenticator (authenticate/action方法)                   │
│  - 支持 REQUIRED / ALTERNATIVE / CONDITIONAL / DISABLED              │
└──────────────────────────┬──────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────────┐
│  Session Creation (AuthenticationProcessor.attachSession)            │
│  - UserSessionManager.createUserSession() → Infinispan sessions缓存   │
│  - TokenManager.attachAuthenticationSession() → clientSessions缓存    │
│  - createLoginCookie() → 设置 KEYCLOAK_SESSION + KEYCLOAK_IDENTITY    │
│  - KEYCLOAK_SESSION值 = SHA-256(UserSessionId)                        │
└──────────────────────────┬──────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────────┐
│  Token Issuance (TokenEndpoint + TokenManager + JWSBuilder)           │
│  - TokenEndpoint.processGrantRequest() 校验code/PKCE                  │
│  - session.tokens().encode(accessToken) → Base64(header.payload)     │
│  - JWSBuilder.sign() → 用Realm私钥签名 → 生成完整JWT                   │
│  - HTTP Response: JSON {access_token, id_token, refresh_token}       │
└─────────────────────────────────────────────────────────────────────┘
```

### 关键数据结构生命周期表

| 数据结构 | 创建时机 | 使用场景 | 销毁时机 |
|---------|---------|---------|---------|
| `RootAuthenticationSessionModel` | 授权端点首次收到请求 | 关联同一浏览器的所有认证会话 | 所有子AuthenticationSession销毁 + TTL到期 |
| `AuthenticationSessionModel` | `AuthorizationEndpoint.buildAuthorization()` | 存储未完成认证的中间状态（用户、client、redirectUri、authNotes） | 认证完成(→UserSession) 或 code被消费 或 TTL超时(默认300秒) |
| `UserSessionModel` | `AuthenticationProcessor.attachSession()` 中的 `createUserSession()` | 用户登录状态的持久载体，SSO单点登录依据 | 用户登出、Session超时、管理员吊销 |
| `AuthenticatedClientSessionModel` | `TokenManager.attachAuthenticationSession()` | 用户与特定Client的绑定关系 | 用户从该Client登出 或 所属UserSession销毁 |
| `KEYCLOOK_SESSION Cookie` (实际Cookie名取决于版本，值为SHA-256哈希) | `AuthenticationManager.createLoginCookie()` | Session Iframe检查、Session重放防御 | Cookie过期 或 用户登出 |
| JWT Access Token | `TokenEndpoint` 处理token请求时 | 客户端携带访问资源服务器 | Token自身过期时间(exp claim) |

### 调试经验总结

**最有效的断点位置**（优先级从高到低）：

1. `AuthenticationProcessor.authenticateOnly()` —— Flow引擎入口，可以观察整个认证决策树
2. `AuthenticationProcessor.attachSession()` —— Session创建，最高价值的调试点
3. `AuthenticationManager.createLoginCookie()` —— Cookie写入，观察Session与Cookie的绑定
4. `AuthenticationManager.authenticateIdentityCookie()` —— 已有Session校验，观察SSO生效情况

**日志注入方法**：通过JAX-RS `ContainerRequestFilter`/`ContainerResponseFilter`注入traceId是最轻量的全链路追踪方案。配合`Logger.infof()`在关键方法入口/出口打印状态，可以构建完整的"快递单"追踪链。

**Arquillian集成测试的替代方案**：源码级调试不需要Arquillian，直接在IDEA中启动Quarkus dev模式即可断点调试。注意Quarkus的classloader与WildFly不同，某些SPI的Provider注册机制在dev模式下有差异。

### 常见源码级Bug分类

| Bug类别 | 典型根因 | 源码位置 | 修复思路 |
|---------|---------|---------|---------|
| Session交叉 | 并发`getUserSession()`→null→`createUserSession()`的check-then-act竞态 | `AuthenticationProcessor.attachSession()` 第1157-1162行 | 利用Infinispan `putIfAbsent`原子语义，增加冲突后重新加载校验逻辑 |
| 缓存不一致 | Infinispan写入成功但数据库事务回滚 | `UserSessionManager.createUserSession()` 中Persistence调用 | 在事务边界内同步刷写缓存，配置Infispan的write-through策略 |
| Token Claims泄露 | 自定义Protocol Mapper未过滤敏感属性 | `AbstractOIDCProtocolMapper.setClaim()` | 在mapper中添加Claim白名单/黑名单过滤逻辑 |
| Cookie跨域混淆 | `KEYCLOAK_SESSION` Cookie的Path设置过宽 | `AuthenticationManager.createLoginCookie()` 中`CookieProvider.set()` | 将Cookie Path限定为具体realm路径而非根路径 |

### 思考题

1. **如果在`finishAuthentication()`成功执行但`TokenEndpoint`还未被调用时，Keycloak发生系统崩溃（如OOM Killer强制终止进程），会出现什么后果？用户的登录状态是否已持久化？authorization_code是否已写入Infinispan？重启后用户需要重新登录吗？如果code已被持久化（Infinispan File Store或数据库），怎样设计补偿机制来清理这些"孤儿code"？**

2. **如果需要在Keycloak中实现"同一个用户在两个设备上登录时，第一个设备被强制退出"的功能（即Single Session Enforcement），应该在请求链路的哪个环节插入检查逻辑？是在`authenticateIdentityCookie()`中拦截已有Session的用户？还是在`redirectAfterSuccessfulFlow()`中主动销毁旧Session？请分析这两种方案的优劣，并考虑并发场景下"旧Session已销毁但新Session尚未创建"的中间状态如何处理。**

---

> **下一章预告**：第35章将深入Keycloak自定义SPI实战——从零编写一个完整的Authenticator插件，实现企业微信扫码登录的集成。我们将手把手完成SPI接口实现、服务注册、管理后台UI扩展和测试验证全流程。
