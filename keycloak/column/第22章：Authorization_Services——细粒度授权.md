# 第22章：Authorization Services——细粒度授权

## 1 项目背景

某在线协作文档平台（类似Google Docs）经过两年的技术积累，已经构建了较为完善的用户体系和基本的RBAC权限模型——用户登录后根据角色（管理员、编辑者、查看者）获得对应的菜单和功能权限。平台运营数据持续增长，日活突破50万，协作文档数量超过200万份。

然而产品团队最近提出了"协作权限升级"需求，直接把团队的技术方案推翻了。需求文档这样写道："文档所有者可以在分享弹窗中输入协作者的邮箱，然后从下拉框中选择'可查看''可评论''可编辑'三种权限级别。同一个文档的不同协作者可以拥有不同权限——张三只能看不能改，李四可以添加评论但不能编辑正文，王五可以做任何操作。"这还不够，文档所有者还希望设置文档的公开可见范围——"仅协作成员""公司内部可见""完全公开"——每种范围下对于匿名用户、登录用户、协作者有不同级别的默认权限。

安全部门随后提出了更严格的合规要求：所有权限变更操作必须记录完整的审计日志——包括谁在什么时间将哪个文档的什么权限授予了哪个用户、通过什么途径授予（手动分享/批量导入/API调用）、权限变更的历史轨迹。审计团队需要能随时回答："请导出上周张三对文档#12345的所有权限变更记录"。

这些需求彻底超出了RBAC的能力边界。RBAC的本质是"角色→功能"的静态映射——它能回答"用户zhangsan有哪些角色"，但无法回答"用户zhangsan能否编辑文档#12345"这类资源级别的动态授权问题。团队的第一反应是自建权限表：`user_id | resource_id | permission_type | granted_by | created_at`。这个方案第一个月似乎还能应付，但三个月后问题全面爆发——权限表膨胀到3000万行，每次文档加载需要执行3次以上的JOIN查询，缓存失效问题频发，权限变更的审计日志则完全重构成了另一张独立的变更历史表。更致命的是，产品又提出了"文件夹继承权限"的需求——文档所在的文件夹权限应自动继承到子文档——自建的权限表根本无法优雅表达这种层次化授权逻辑。

UMA（User-Managed Access）协议在团队中几乎是一片知识空白，"Permission"和"Policy"的概念界限模糊，JavaScript策略的性能风险也让人心存疑虑。Keycloak的Authorization Services正是在这个背景下进入视野的——它提供了一套完整的Resource/Scope/Policy/Permission授权模型，天然支持资源级别的细粒度授权，内置UMA协议实现，并提供可扩展的策略引擎。

---

## 2 项目设计——剧本式交锋对话

**小胖**（啃着薯片，盯着产品需求文档）：大师，我看这个需求不就是Google Docs那个分享按钮嘛——点分享，输入邮箱，选"可查看""可评论""可编辑"，啪一下就好了。这不是很简单吗？数据库里加一张`document_permissions`表不就搞定了，为啥Keycloak非要搞一整套Authorization Services？Resource、Scope、Policy、Permission……光听这些概念我头都大了。

**大师**：小胖，你说的自建权限表方案我十年前就做过。你的`document_permissions`表现在只有三列：user、document、permission_type。但再过一个月，产品说"文件夹也要设权限，子文档继承父文件夹的权限"——你怎么办？加个递归查询？然后产品又说"VIP用户可以免授权查看所有公开文档"——你再加一个VIP判断分支？接着安全部门说"连续输错三次密码后禁止编辑操作"——你又加一个风控判断？每一层新需求都会让你的权限判断代码变成洋葱——层层包裹，最后没人敢动。Keycloak Authorization Services的本质是把"谁能对什么资源做什么操作"这个决策逻辑从你的业务代码中完全剥离出来，变成一组可配置、可组合、可审计的策略规则。你只需要问一句"允许吗？"，而不需要知道"为什么允许"。

**小白**（在笔记本上写了四个词，圈来圈去）：那我先搞清楚这几个核心概念。Resource、Scope、Policy、Permission——它们四者的关系到底是什么？我感觉Scope和Permission特别容易混淆。

**大师**：好，我用文档平台来串起来说。

**Resource（资源）**是你需要保护的数据实体——文档#12345、文件夹/projects/2024、甚至是一个API端点`DELETE /api/documents/{id}`都可以注册为Resource。每个Resource有一个唯一的ID和一组属性，比如文档#12345的`type=document`、`department=研发部`、`owner=zhangsan`。

**Scope（作用域）**是对资源的操作——view（查看）、comment（评论）、edit（编辑）、delete（删除）、share（分享）。Scope是Resource的附属概念，一个Resource下可以挂多个Scope。

**Policy（策略）**回答"谁在什么条件下可以访问"——这是整个模型的大脑。Keycloak提供了七种策略类型：基于角色的（特定角色可访问）、基于用户的（指定用户列表）、基于规则的（JavaScript脚本自定义判断逻辑）、基于时间/日期的（工作日/时间段限制）、基于客户端的（特定客户端可访问）、聚合策略（组合多个策略的AND/OR逻辑）、基于组成员的（Group内成员可访问）。每种策略你都可以设置Decision Strategy——Affirmative（任一策略通过即通过）、Unanimous（所有策略通过才通过）、Consensus（半数以上通过）。

**Permission（权限）**是前三者的"粘合剂"——它把Resource、Scope、Policy绑在一起，定义了一条授权规则："对于资源X的Scope Y，应用策略Z来决策"。你可以把它理解为一个关联表：`document#12345 + edit → 策略：文档所有者 OR 编辑者角色`。

**小白**：那UMA协议在这个体系里扮演什么角色？还有RPT和普通Access Token有什么本质区别？

**大师**：这个问题很关键。普通的Access Token只能告诉你"用户是谁、有哪些角色"，它是在认证阶段签发的。但RBAC的Access Token无法表达"用户A对文档X有编辑权限"这种资源级信息——它只能说用户A是`editor`角色。

UMA（User-Managed Access）协议解决了这个问题。它的工作流程分成五步：第一，资源服务器通过Protection API向Keycloak注册资源（这就是步骤2里我们用的`/authz/protection/resource_set`端点）；第二，客户端携带普通Access Token向Keycloak请求授权——此时发起的不是OAuth2标准Token请求，而是`grant_type=urn:ietf:params:oauth:grant-type:uma-ticket`的UMA授权请求，请求体中携带`permission=文档-年度报告#edit`；第三，Keycloak的Policy Evaluator引擎评估该用户在该资源上匹配的所有策略；第四，如果策略通过，Keycloak签发一个RPT（Requesting Party Token）；第五，客户端以RPT令牌访问资源服务器，资源服务器解析RPT中的permissions声明来做出决策。

RPT和普通Access Token的核心区别在于：普通Token的载荷是`realm_access.roles`和`resource_access`，而RPT的载荷中多了一个`authorization.permissions`数组——例如`[{"rsid":"doc-12345","rsname":"文档-年度报告","scopes":["view","edit"]}]`。这意味着RPT是"携带授权结果"的令牌——它不仅仅声明了"你是谁"，更声明了"你能做什么"。这也意味着RPT的体积会随着用户拥有的权限数量增长而增长——1000个文档的权限会让RPT体积超过100KB，这就是后续需要关注的Token体积控制问题。

**小胖**：等等，那个JavaScript策略听着有点危险——在Keycloak里执行自定义JS脚本？如果运维写了一段有注入风险的代码怎么办？

**大师**（点头）：你的安全意识很好。JavaScript策略确实是把双刃剑。Keycloak使用Nashorn引擎（JDK内置的JavaScript引擎）来执行JS策略，它提供了`$evaluation`对象作为沙箱接口——`$evaluation.getContext()`获取请求上下文、`$evaluation.getPermission()`获取当前权限对象、`$evaluation.grant()`表示授权通过、`$evaluation.deny()`表示拒绝。它本身是运行在JVM沙箱内的，不能直接访问文件系统或网络。

但你想到了一个关键风险点——如果策略逻辑写得太复杂，比如在策略里查询外部API或遍历大量用户属性，不仅执行耗时会长，还可能因异常导致服务降级。Keycloak对JS策略有硬性超时限制（默认30秒），超时会自动中断执行并记录错误日志。生产环境的建议是：JS策略只用于简单的属性比较逻辑（如判断用户部门和资源部门是否一致），复杂逻辑应该用自定义SPI的Policy Provider来实现——那是Java代码，可以充分测试和优化。

**大师总结技术映射**：

| 生活比喻 | 技术映射 |
|---------|---------|
| 写字楼里的每个房间 | Resource：需要保护的数据实体 |
| 房间门上的"推""拉""禁止进入"标牌 | Scope：对资源的操作 |
| 门禁规则——"研发部员工可刷卡进入" | Policy：谁在什么条件下可以访问 |
| 门禁系统把"房间+操作+规则"绑定 | Permission：Resource + Scope + Policy的组合 |
| 临时访客登记拿到的门禁卡 | RPT：携带授权结果的令牌 |
| 物业安保中心统一管理门禁规则 | Keycloak Authorization Services：集中式策略引擎 |

---

## 3 项目实战

### 环境准备

| 组件 | 版本/说明 |
|------|----------|
| Keycloak | 26.x，Realm=**demo-realm** |
| Python 3 | 3.10+，`pip install requests pyjwt` |
| curl | 任意版本，用于Protection API调用 |
| 测试用户 | zhangsan（文档所有者，已创建）、lisi（同部门同事）、wangwu（其他部门用户） |

确认Keycloak服务运行在`http://localhost:8080`，且`demo-realm`和上述用户已存在（参考前续章节搭建）。

### 步骤1：启用客户端Authorization Services

**操作**：Admin Console → demo-realm → Clients → 选择或创建一个名为`oms-backend`的客户端 → Settings标签页 → **Authorization Enabled** → 切换为**ON** → 保存。

**说明**：启用后，该客户端会获得一套完整的Authorization配置子菜单——Settings（基础配置，包括策略执行模式、决策策略）、Resources（资源注册和管理）、Scopes（操作定义）、Policies（策略规则）、Permissions（授权绑定）、Evaluate（在线策略测试工具）、Export（配置导入导出）。

### 步骤2：创建Resources（注册受保护资源）

通过Protection API注册文档资源。首先需要获取PAT（Protection API Token）——这是资源服务器用来注册资源的管理令牌：

```bash
# 步骤2.1：获取PAT（Protection API Token）
# 注意：grant_type使用客户端凭证模式，客户端必须是confidential类型
curl -X POST http://localhost:8080/realms/demo-realm/protocol/openid-connect/token \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "client_id=oms-backend" \
  -d "client_secret=<YOUR_CLIENT_SECRET>" \
  -d "grant_type=client_credentials"
# 运行结果：返回JSON，提取其中的 access_token 即为PAT

# 步骤2.2：使用PAT注册资源
curl -X POST http://localhost:8080/realms/demo-realm/authz/protection/resource_set \
  -H "Authorization: Bearer $PAT" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "文档-年度报告",
    "displayName": "2024年度总结报告",
    "type": "document",
    "icon_uri": "http://localhost:8080/icons/document.png",
    "scopes": ["view", "comment", "edit"],
    "owner": "zhangsan",
    "attributes": {
      "department": ["研发部"],
      "securityLevel": ["internal"],
      "folderId": ["/projects/2024"]
    }
  }'
# 运行结果：{"_id":"a1b2c3d4-e5f6-7890-abcd-ef1234567890","user_access_policy_uri":"..."}
# _id即为resource_id，后续Policies和Permissions都会用到它
```

**说明**：`scopes`字段列出了该资源支持的操作类型，`attributes`存储业务属性（部门、安全级别、所属文件夹等），这些属性可以在JavaScript策略中通过`resource.getAttributes()`访问。

### 步骤3：创建Scopes和Policies

**在Admin Console中操作**（也可通过REST API，但管理界面更直观）：

1. **创建Scopes**：进入`oms-backend` → Authorization → Scopes → Create：
   - Name: `view`，Display Name: 查看
   - Name: `comment`，Display Name: 评论
   - Name: `edit`，Display Name: 编辑

2. **创建User Policy（文档所有者）**：Authorization → Policies → Create → User：
   - Name: `文档所有者`
   - Users: `zhangsan`
   - Logic: Positive

3. **创建Role Policy（编辑者角色）**：Authorization → Policies → Create → Role：
   - Name: `编辑者角色`
   - Realm Roles: （创建一个名为`editor`的Realm角色并选中）
   - Logic: Positive

4. **创建JavaScript Policy（同部门可查看）**：Authorization → Policies → Create → JavaScript：

```javascript
// 同部门可查看策略
var context = $evaluation.getContext();
var identity = context.getIdentity();
var attributes = identity.getAttributes();

// 获取目标资源属性
var permission = $evaluation.getPermission();
var resource = permission.getResource();
var resourceDept = resource.getAttributes().get("department");

// 获取当前用户部门属性
var userDeptAttr = attributes.getValue("department");

// 判断是否为同一部门
if (resourceDept && userDeptAttr && resourceDept.contains(userDeptAttr)) {
    $evaluation.grant();
}
```

**配置要点**：JavaScript策略在此配置后只对"同部门"这一个维度做判断。如果同时需要工作日时间限制，应当创建独立的时间策略，然后通过聚合策略将两者组合——这是"单一职责原则"在策略设计中的体现。

### 步骤4：创建Permissions（绑定Resource + Scope + Policy）

在Authorization → Permissions中创建三条Permission：

**Permission 1**：文档所有者拥有edit权限
- Name: `文档所有者-编辑权限`
- Resource: 文档-年度报告
- Scopes: edit
- Policies: 文档所有者（User Policy）
- Decision Strategy: Affirmative

**Permission 2**：编辑者角色拥有comment权限
- Name: `编辑者-评论权限`
- Resource: 文档-年度报告
- Scopes: comment
- Policies: 编辑者角色（Role Policy）
- Decision Strategy: Affirmative

**Permission 3**：同部门可查看
- Name: `同部门-查看权限`
- Resource: 文档-年度报告
- Scopes: view
- Policies: 同部门可查看（JavaScript Policy）
- Decision Strategy: Affirmative

### 步骤5：授权请求与RPT获取（Python实现）

```python
import requests
import base64
import json

BASE_URL = "http://localhost:8080"
REALM = "demo-realm"
CLIENT_ID = "oms-backend"
CLIENT_SECRET = "<YOUR_CLIENT_SECRET>"
TOKEN_URL = f"{BASE_URL}/realms/{REALM}/protocol/openid-connect/token"

def get_access_token(username, password):
    """步骤1：获取普通Access Token（用户认证）"""
    resp = requests.post(TOKEN_URL, data={
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "username": username,
        "password": password,
        "grant_type": "password"
    })
    if resp.status_code != 200:
        raise Exception(f"Auth failed: {resp.status_code} {resp.text}")
    return resp.json().get("access_token")

def get_rpt(access_token, resource_name, scopes):
    """步骤2：通过UMA流程获取RPT（携带授权结果）"""
    resp = requests.post(TOKEN_URL, data={
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "grant_type": "urn:ietf:params:oauth:grant-type:uma-ticket",
        "audience": CLIENT_ID,
        "permission": f"{resource_name}#{','.join(scopes)}"
    }, headers={
        "Authorization": f"Bearer {access_token}"
    })
    if resp.status_code == 200:
        return resp.json().get("access_token")
    elif resp.status_code == 403:
        print(f"Authorization denied for {resource_name}#{scopes}")
        return None
    else:
        raise Exception(f"RPT failed: {resp.status_code} {resp.text}")

def decode_jwt_payload(token):
    """解码JWT Payload（不验证签名，仅用于演示）"""
    payload = token.split(".")[1]
    payload += "=" * (4 - len(payload) % 4) if len(payload) % 4 else ""
    decoded = base64.urlsafe_b64decode(payload)
    return json.loads(decoded)

# ==================== 测试1：zhangsan（文档所有者）请求edit权限 ====================
print("=" * 60)
print("测试1：zhangsan请求文档-年度报告#edit")
access_token_zs = get_access_token("zhangsan", "Welcome@2024")
rpt_zs = get_rpt(access_token_zs, "文档-年度报告", ["edit"])
if rpt_zs:
    permissions = decode_jwt_payload(rpt_zs).get("authorization", {}).get("permissions", [])
    print(f"zhangsan获得的权限: {json.dumps(permissions, indent=2, ensure_ascii=False)}")
    # 预期输出：包含 {"rsname":"文档-年度报告", "scopes":["edit"]}

# ==================== 测试2：lisi（同部门同事，研发部）请求edit权限 ====================
print("\n" + "=" * 60)
print("测试2：lisi（同部门）请求文档-年度报告#edit")
access_token_ls = get_access_token("lisi", "Welcome@2024")
rpt_ls_edit = get_rpt(access_token_ls, "文档-年度报告", ["edit"])
# 预期：403拒绝（lisi不是文档所有者，也没有editor角色）
print(f"edit请求结果: {'授权通过' if rpt_ls_edit else '403拒绝（预期行为）'}")

print("\n测试2b：lisi请求文档-年度报告#view")
rpt_ls_view = get_rpt(access_token_ls, "文档-年度报告", ["view"])
if rpt_ls_view:
    permissions = decode_jwt_payload(rpt_ls_view).get("authorization", {}).get("permissions", [])
    print(f"lisi获得的权限: {json.dumps(permissions, indent=2, ensure_ascii=False)}")
    # 预期输出：包含view权限（同部门可查看策略通过）

# ==================== 测试3：wangwu（其他部门用户）请求view权限 ====================
print("\n" + "=" * 60)
print("测试3：wangwu（其他部门）请求文档-年度报告#view")
access_token_ww = get_access_token("wangwu", "Welcome@2024")
rpt_ww = get_rpt(access_token_ww, "文档-年度报告", ["view"])
print(f"view请求结果: {'授权通过' if rpt_ww else '403拒绝（预期行为——wangwu不是研发部）'}")
```

**运行结果解读**：
- zhangsan作为文档所有者，成功获取edit权限的RPT——匹配了User Policy"文档所有者"
- lisi是研发部成员，view权限请求返回RPT并携带`view` scope——匹配了JavaScript Policy"同部门可查看"
- lisi的edit请求返回403——他的身份不匹配任何edit相关的策略
- wangwu非研发部，view请求也返回403——JavaScript策略判断部门属性不匹配后自动拒绝

### 步骤6：使用AuthzClient Java库

对于Java后端应用，Keycloak提供了官方的`keycloak-authz-client`库来简化授权交互：

```java
// Maven依赖
// <dependency>
//     <groupId>org.keycloak</groupId>
//     <artifactId>keycloak-authz-client</artifactId>
//     <version>26.1.0</version>
// </dependency>

import org.keycloak.authorization.client.AuthzClient;
import org.keycloak.authorization.client.AuthorizationDeniedException;
import org.keycloak.representations.idm.authorization.AuthorizationRequest;
import org.keycloak.representations.idm.authorization.AuthorizationResponse;
import org.keycloak.representations.idm.authorization.Permission;

// 初始化客户端（keycloak.json需在classpath下）
AuthzClient authzClient = AuthzClient.create();

// 方式1：使用用户名密码直接授权
AuthorizationRequest request = new AuthorizationRequest();
request.addPermission("文档-年度报告", "edit");

AuthorizationResponse response = authzClient.authorization("zhangsan", "Welcome@2024")
    .authorize(request);
String rpt = response.getToken();

// 方式2：使用已有的Access Token请求RPT
// AuthorizationResponse response = authzClient.authorization(accessToken)
//     .authorize(request);

// 解析授权结果
for (Permission permission : response.getToken().getAuthorization().getPermissions()) {
    System.out.println("资源: " + permission.getResourceName()
        + " → 操作: " + permission.getScopes());
}
```

### 可能遇到的坑

1. **PAT Token使用客户端凭证模式获取**：Protection API的端点`/authz/protection/resource_set`要求使用PAT认证，而PAT必须通过客户端凭证模式（`grant_type=client_credentials`）获取，不能用用户密码模式。如果使用了错误的grant_type，会收到401 Unauthorized。

2. **JavaScript策略默认30秒超时**：如果策略中执行了外部HTTP请求或大量JSON解析，可能在生产负载下触发超时。超时后该策略被标记为DENY，不会抛出异常到客户端。建议在Admin Console的Evaluate工具中单独测试JS策略执行时间，如果接近10秒就需要优化。

3. **Permission删除后级联失效**：如果一条Permission绑定了Resource A + Scope view + Policy B，当你删除这个Permission后，即使Resource A的view scope仍有其他Permission关联，用户请求时需要确保有其他Policy覆盖。建议先用Evaluate工具模拟删除后的效果再操作。

4. **UMA flow中permission参数格式**：`permission=资源名#scope1,scope2`——资源名必须和注册时的name完全一致（区分大小写），scope用逗号分隔。如果资源名中有特殊字符，需要进行URL编码。

5. **RPT Token体积膨胀**：用户拥有的权限越多，RPT中的permissions数组越长。默认情况下Keycloak会将当前请求涉及的所有权限都写入RPT。可以通过配置客户端Authorization Settings中的"Limit"选项（按需返回）来控制Token体积——设置后RPT只返回请求中指定的权限。

### 测试验证矩阵

| 用户 | 部门 | 请求操作 | 预期结果 | 实际结果 |
|------|------|---------|---------|---------|
| zhangsan | 研发部 | edit | 授权通过 | ✓ User Policy命中 |
| zhangsan | 研发部 | view | 授权通过 | ✓ User Policy或JS Policy命中 |
| lisi | 研发部 | edit | 403拒绝 | ✓ 不匹配任何edit策略 |
| lisi | 研发部 | view | 授权通过 | ✓ JS Policy命中（同部门） |
| lisi | 研发部 | comment | 403拒绝 | ✓ lisi无editor角色 |
| wangwu | 市场部 | view | 403拒绝 | ✓ JS Policy判断部门不匹配 |
| wangwu | 市场部 | edit | 403拒绝 | ✓ 无任何匹配策略 |

---

## 4 项目总结

### 优点与缺点

| 维度 | Keycloak Authz Services | 自建权限表 | Casbin |
|------|------------------------|-----------|--------|
| 统一管理 | ✅ Admin Console + REST API一体化 | ❌ 需自建管理后台 | ⚠️ 依赖配置文件或策略管理适配器 |
| UMA协议支持 | ✅ 内置UMA 2.0完整实现 | ❌ 需从零实现协议 | ❌ 不支持UMA |
| 策略灵活性 | ✅ 7种策略类型，支持JS自定义规则 | ⚠️ 策略逻辑硬编码在业务层 | ✅ 支持多种模型（RBAC/ABAC/ACL） |
| 审计追踪 | ✅ 内置事件日志，权限变更自动记录 | ⚠️ 需自建审计表+切面 | ❌ 需自行集成 |
| 性能 | ⚠️ 策略评估涉及多次数据库查询，需配合缓存 | ✅ 数据库索引优化后性能极佳 | ✅ 内存匹配，纳秒级决策 |
| 学习曲线 | ⚠️ Resource/Scope/Policy/Permission四层模型 + UMA协议 | ✅ 数据库表谁都会建 | ⚠️ PERM模型 + 策略语法有门槛 |
| 扩展性 | ✅ SPI扩展点丰富，可自定义Policy Provider | ❌ 新需求=改代码=发布 | ⚠️ 自定义函数和匹配器 |

### 适用场景

1. **多租户文档协作平台**：每个文档有自己的协作者列表，权限粒度细化到view/comment/edit三级，支持文件夹权限继承——天然匹配Resource模型。

2. **B2B复杂权限场景**：根据合同级别、客户类型、服务有效期等多维度组合授权，可以用聚合策略（AND/OR）组合多个Policy来实现。

3. **API资源精细化管控**：将REST API端点注册为Resource，不同Scope对应HTTP Method（GET→view、POST→create、PUT→update、DELETE→delete），用客户端策略限定哪些第三方应用可以调用哪些API。

4. **用户自主授权（UMA）**：允许终端用户通过UI自主管理"谁能访问我的资源"，无需管理员介入——UMA设计的核心场景。

5. **合规审计场景**：需要回答"谁在什么时间对哪个资源授予了什么权限"，Keycloak的事件体系可以直接输出结构化审计日志。

### 不适用场景

1. **极简应用**：如果只有3-5个固定角色，没有资源级权限需求，维持RBAC即可——Authorization Services引入了额外的复杂度，投入产出比不高。

2. **超大规模资源（100万+）**：每个文档注册为一个Resource意味着100万条Resource记录。虽然Keycloak在技术上可以承受这个数量级，但策略评估时会涉及多次数据库查找，P99延迟可能显著增长。建议设计分层授权架构——部门级别→项目级别→文档级别，上层权限自动继承到下层。

### 注意事项

- **JavaScript策略的性能上限**：JVM Nashorn引擎的性能远不如原生Java代码。当策略数量超过20条且包含JS策略时，每次授权请求的策略评估总耗时可能超过100ms。建议将高频调用的JS策略迁移为自定义SPI Policy Provider（Java实现）。

- **RPT Token体积控制**：用户的permissions数量决定了RPT中的`authorization.permissions`数组长度。如果每个文档都单独签发一个权限条目，1000个文档的RPT可能超过200KB——这会显著拖累HTTP请求和反序列化性能。可以使用分组权限（如`/projects/2024/*`通配符）来压缩Token体积。

- **策略缓存时间权衡**：在Authorization Settings中可以配置策略评估结果的缓存时间（默认30秒）。太短会增加数据库负载（每次请求都重新评估），太长会导致权限变更不能及时生效。30-120秒是生产环境的经验区间。

### 常见踩坑经验

1. **问题**：UMA授权请求返回`invalid_grant`错误。**根因**：`permission`参数的资源名称与注册时不一致——注册时是"文档-年度报告"，请求时写成了"年度报告"。资源名区分大小写且必须完全匹配。

2. **问题**：PAT Token过期后Protection API调用失败。**根因**：PAT也是JWT Token，有过期时间（默认1分钟，可通过客户端Settings中的Access Token Lifespan调整）。**解决**：每次调用资源注册API前检查PAT过期状态，或在代码中实现自动刷新逻辑——用`refresh_token`（如果客户端配置了Refresh Token）或重新获取客户端凭证。

3. **问题**：JavaScript策略中`$evaluation.grant()`在复杂条件下没有被调用。**根因**：JavaScript策略的决策必须显式调用`grant()`或`deny()`——如果代码因异常提前退出（例如`resourceDept`为null时直接抛出了TypeError），策略会被标记为DENY但不报错。**解决**：在JS策略中做好空值防御，所有属性访问前做null检查。

### 思考题

1. **百万级资源的分层授权设计**：如果文档数量达到100万级别，每个文档都注册为Keycloak Resource，策略评估性能会如何变化？请设计一个"部门→项目→文档"的三层授权架构方案：部门级别的Policy覆盖部门下所有文档，项目级别的Policy覆盖项目下所有文档，文档级别的Policy只处理该文档的特例覆盖。如何实现这种"父级权限自动继承、子级可覆盖"的机制？

2. **实时权限同步**：用户zhangsan正在编辑文档#12345，此时文档所有者lisi将zhangsan的权限从"可编辑"降级为"仅可查看"。此时zhangsan的RPT尚未过期，他仍然可以继续编辑——这是RPT缓存的固有问题。请设计一个方案来解决这个"实时撤权"问题：是缩短RPT有效期（代价是频繁重新授权）？还是推送权限变更事件让应用层主动处理（代价是实现复杂度）？还是采用每次操作都实时向Keycloak确认（代价是延迟）？
