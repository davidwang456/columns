# 第17章：认证体系——LDAP/OAuth/SAML/JWT

## 1. 项目背景

"公司要求Grafana接入企业SSO（单点登录），支持AD域账号。研发想用Google OAuth登录，安全团队要求SAML并开启MFA。三个部门的需求互相冲突，到底该怎么选？"

安全架构师阿伦正在经历Grafana身份认证方案选型的决策困境。一个Grafana平台上同时服务运维、开发、安全三个部门，每个部门有不同的身份系统要求。运维习惯用LDAP/AD域账号，开发喜欢OAuth（GitHub/Google一键登录），安全团队必须走SAML+企业IdP。

Grafana支持几乎所有主流认证协议，但关键不在于"支持什么"，而在于"怎么组合"。一个典型的配置可能是：LDAP作为主认证 + OAuth作为备选 + JWT用于自动化API访问 + 匿名访问用于公共大屏。更复杂的是组映射、角色同步、属性过滤等高级配置——这些在官方文档语焉不详，只能靠实战经验积累。

本章将通过三个典型场景（企业AD域接入、OAuth SSO配置、多协议共存），完整覆盖Grafana的认证体系。

## 2. 项目设计

**小胖**（看着Grafana登录页发呆）：大师，我们公司用的是AD域账号。每次登录Grafana还要单独输入用户名密码，同事抱怨为什么不直接拿域账号登录。这个是不是叫LDAP？

**大师**：没错。LDAP（轻量级目录访问协议）是企业和Grafana集成最主流的方式。但广义的LDAP包括了微软的Active Directory（AD），它的认证过程是这样的：

Grafana把你的用户名发给LDAP服务器 → LDAP服务器用这个用户名和密码做bind（绑定）操作 → 如果bind成功说明密码正确 → 然后做search操作查出这个用户属于哪些组 → Grafana根据组的映射关系赋予对应的角色。

**小白**（记录）：那具体配置呢？我听说LDAP配置很复杂。

**大师**：确实是最复杂的认证配置。看看Grafana中典型的LDAP配置：

```toml
[[servers]]
host = "ldap.example.com"
port = 389
use_ssl = false
bind_dn = "cn=admin,dc=example,dc=com"
bind_password = "admin_password"
search_filter = "(cn=%s)"
search_base_dns = ["dc=example,dc=com"]

# 组映射——这是LDAP配置的核心
[[servers.group_mappings]]
group_dn = "cn=grafana-admins,ou=groups,dc=example,dc=com"
org_role = "Admin"

[[servers.group_mappings]]
group_dn = "cn=grafana-editors,ou=groups,dc=example,dc=com"
org_role = "Editor"

[[servers.group_mappings]]
group_dn = "cn=grafana-viewers,ou=groups,dc=example,dc=com"
org_role = "Viewer"
```

**小胖**：这个`search_filter = "(cn=%s)"`里的`%s`是什么？

**大师**：你输入的用户名会替换`%s`。比如你输入`zhangsan`，实际执行的LDAP查询是`(cn=zhangsan)`。这要求你的LDAP目录中用户记录有`cn`这个属性。如果用AD，可能应该用`sAMAccountName`属性，filter要写成`(&(sAMAccountName=%s)(objectClass=user))`。

**小白**：那OAuth呢？我们研发团队想用GitHub账号登录，这个配置简单。

**大师**：OAuth确实简单得多。三步搞定：
1. 到OAuth Provider（GitHub/Google/Azure AD）创建一个OAuth App，获取Client ID和Client Secret
2. 配置Grafana
3. 配置回调URL（必须是`https://your-grafana.com/login/github`这种格式）

OAuth的关键配置参数：

```ini
[auth.github]
enabled = true
allow_sign_up = true
client_id = YOUR_CLIENT_ID
client_secret = YOUR_CLIENT_SECRET
scopes = user:email,read:org
auth_url = https://github.com/login/oauth/authorize
token_url = https://github.com/login/oauth/access_token
api_url = https://api.github.com/user
allowed_organizations = my-company  # 限制只有公司组织成员可登录
```

**小胖**（追问）：那如果我想让管理员用LDAP、普通开发用OAuth，两个都开了会发生什么？

**大师**：这就是多认证协议共存。Grafana可以同时开启多种认证。登录页面会显示多个登录按钮（"Sign in with LDAP"、"Sign in with GitHub"）。不同协议登录的用户在Grafana内部统一管理——你可以设置哪个协议的登录用户自动获得什么角色。

关键配置：
```ini
[auth]
disable_login_form = false  # 保留表单登录
oauth_auto_login = false    # 不自动跳转OAuth
signout_redirect_url = /login

[auth.basic]   # 始终保留basic auth（API使用）
enabled = true
```

**小白**：JWT认证是什么场景用的？

**大师**：JWT用于"嵌入式"场景。比如你已经有一个内部系统门户，用户在那个门户登录后，你希望在页面中嵌入Grafana的Dashboard（iframe），且不用再次登录。

原理：你的门户系统生成一个JWT Token（包含username、email、role），Grafana验证Token的签名，然后根据Token中的信息自动创建或关联用户。

```ini
[auth.jwt]
enabled = true
header_name = X-JWT-Assertion
email_claim = sub
username_claim = sub
jwk_set_url = https://auth.example.com/.well-known/jwks.json
auto_sign_up = true
role_attribute_path = contains(groups[*], 'grafana-admin') && 'Admin' || 'Viewer'
```

**小胖**：`role_attribute_path`这行看起来像魔法。

**大师**：这是Grafana的JMESPath表达式。意思是：如果用户的JWT中的groups数组里包含`grafana-admin`，则给他Admin角色，否则给Viewer。JMESPath在多种认证中用于动态角色映射——LDAP、OAuth、SAML、JWT都支持。

**技术映射**：LDAP = 公司门禁卡（刷卡进公司，权限按部门分配），OAuth = 微信扫码登录（第三方担保你的身份），SAML = 护照签证（由权威机构签发的身份文件），JWT = 临时通行证（有签发机关和有效期）。

## 3. 项目实战

**环境准备**

由于真实LDAP/AD/OAuth环境搭建复杂，本章采用"模拟+配置讲解"方式。若有真实环境可对照操作。

**步骤一：LDAP认证配置**

在grafana.ini中添加（或用环境变量）：

```ini
[auth.ldap]
enabled = true
config_file = /etc/grafana/ldap.toml
allow_sign_up = true

[auth]
disable_login_form = false
```

创建 `/etc/grafana/ldap.toml`：

```toml
[[servers]]
host = "ldap.example.com"
port = 636
use_ssl = true
start_tls = false
ssl_skip_verify = false

# 管理员的绑定凭据（用于搜索用户）
bind_dn = "cn=grafana-bind,dc=example,dc=com"
bind_password = "bind_password_here"

# 用户搜索
search_filter = "(&(uid=%s)(objectClass=person))"
search_base_dns = ["ou=users,dc=example,dc=com"]

# 用户属性映射
[servers.attributes]
name = "givenName"
surname = "sn"
username = "uid"
member_of = "memberOf"
email = "mail"

# 组映射——LDAP组到Grafana角色的映射
[[servers.group_mappings]]
group_dn = "cn=grafana-admin,ou=groups,dc=example,dc=com"
org_role = "Admin"
grafana_admin = true

[[servers.group_mappings]]
group_dn = "cn=grafana-editor,ou=groups,dc=example,dc=com"
org_role = "Editor"

[[servers.group_mappings]]
group_dn = "cn=grafana-viewer,ou=groups,dc=example,dc=com"
org_role = "Viewer"
```

验证LDAP配置：
```bash
# 查看LDAP相关日志
docker logs grafana | grep -i ldap

# 测试LDAP连接（在Grafana服务器上）
ldapsearch -H ldaps://ldap.example.com:636 \
  -D "cn=grafana-bind,dc=example,dc=com" \
  -w "bind_password_here" \
  -b "ou=users,dc=example,dc=com" \
  "(uid=zhangsan)"
```

**步骤二：OAuth（GitHub）认证配置**

**2.1 在GitHub创建OAuth App**

Settings → Developer settings → OAuth Apps → New OAuth App：
- Application name: `Grafana`
- Homepage URL: `https://grafana.example.com`
- Authorization callback URL: `https://grafana.example.com/login/github`

获取 Client ID 和 Client Secret。

**2.2 Grafana配置**

```ini
[auth.github]
enabled = true
allow_sign_up = true
client_id = Iv1.xxxxxxxxxxxx
client_secret = xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
scopes = user:email,read:org
auth_url = https://github.com/login/oauth/authorize
token_url = https://github.com/login/oauth/access_token
api_url = https://api.github.com/user

# 团队同步（将GitHub团队映射到Grafana Team）
team_ids = 
allowed_organizations = my-company

# 角色映射
role_attribute_path = contains(groups[*], '@my-company/grafana-admins') && 'Admin' || 'Editor'
```

**2.3 验证OAuth登录**

访问Grafana登录页 → 点击"Sign in with GitHub" → 跳转到GitHub授权页 → 授权后回到Grafana → 自动创建用户并登录。

**步骤三：SAML认证配置（企业SSO）**

SAML通常用于集成企业IdP（如Okta、Azure AD、Keycloak）。

```ini
[auth.saml]
enabled = true
allow_sign_up = true
certificate_path = /etc/grafana/saml/certificate.crt
private_key_path = /etc/grafana/saml/private_key.pem
idp_metadata_url = https://idp.example.com/metadata

# SAML属性映射
assertion_attribute_name = displayName
assertion_attribute_login = email
assertation_attribute_email = email
assertion_attribute_groups = groups

# 角色映射
role_values_admin = Admin
role_values_editor = Editor
role_values_grafana_admin = Admin
```

配置过程：
1. 在IdP（如Okta）中创建Grafana应用
2. 配置SAML的ACS URL: `https://grafana.example.com/saml/acs`
3. 下载IdP的Metadata XML并上传到Grafana
4. 在Grafana中配置证书和私钥
5. 验证：访问Grafana自动跳转IdP登录

**步骤四：JWT认证配置**

场景：在内部Portal页面嵌入Grafana Dashboard iframe。

```ini
[auth.jwt]
enabled = true
header_name = X-JWT-Assertion
email_claim = sub
username_claim = sub
jwk_set_url = https://auth.example.com/.well-known/jwks.json
expected_claims = {"iss": "https://auth.example.com"}
auto_sign_up = true
role_attribute_path = "Viewer"  # JWT用户统一给Viewer

# 如果需要在URL参数中传递JWT
url_login = true
```

验证JWT认证：
```bash
# 生成JWT Token（示例）
TOKEN=$(python3 -c "
import jwt
token = jwt.encode({'sub': 'testuser', 'iss': 'https://auth.example.com'}, 'secret')
print(token)
")

# 用JWT访问Grafana API
curl -H "X-JWT-Assertion: $TOKEN" \
  http://localhost:3000/api/user
```

**步骤五：多认证协议共存**

完整配置示例（grafana.ini）：

```ini
[auth]
disable_login_form = false
oauth_auto_login = false
signout_redirect_url = /login

# 同时开启多种认证
[auth.ldap]
enabled = true
config_file = /etc/grafana/ldap.toml

[auth.github]
enabled = true
client_id = xxx
client_secret = xxx

[auth.saml]
enabled = true
...

[auth.jwt]
enabled = true
...

# 匿名访问（用于公共大屏）
[auth.anonymous]
enabled = true
org_name = Main Org.
org_role = Viewer
```

登录页面效果：
```
┌──────────────────────────────┐
│      Sign in to Grafana      │
│                              │
│  [Username        ]          │
│  [Password        ]          │
│  [         Login         ]   │
│                              │
│  ──── or ────               │
│                              │
│  [Sign in with LDAP]         │
│  [Sign in with GitHub]       │
│  [Sign in with SSO (SAML)]   │
└──────────────────────────────┘
```

**常见坑点**
1. **LDAP搜索超时**：大型AD域中search可能很慢(>10s)。优化：收紧search_base_dns范围、用分页搜索、或者设置更长的超时`group_search_filter_user_attribute = "member"`用group反向搜索。
2. **OAuth回调URL严格匹配**：`root_url`的http/https、末尾/、端口号必须与OAuth Provider中配置的完全一致。
3. **SAML证书过期**：IdP的证书过期后，所有SAML用户都无法登录。定期检查证书有效期并设置告警。
4. **多协议用户冲突**：如果同一个用户通过LDAP和OAuth各登录了一次，Grafana会创建两个独立的用户（因为验证机制不同）。避免：用email作为关联键配置`auto_assign_org`策略。
5. **HTTPS是硬要求**：OAuth和SAML的callback需要HTTPS。开发环境可以使用`GF_SERVER_PROTOCOL=http`配合`GF_SERVER_ENFORCE_DOMAIN=false`跳过。

## 4. 项目总结

**认证协议对比矩阵**

| 协议 | 复杂度 | 用户体验 | 安全性 | 适用场景 |
|------|--------|---------|--------|---------|
| LDAP | 中-高 | 好（域账号免二次登录） | 中 | 企业内部AD/OpenLDAP |
| OAuth | 低 | 极好（一键登录） | 高 | 研发/SaaS团队 |
| SAML | 高 | 极好（SSO无感） | 极高 | 企业级IdP集成 |
| JWT | 中 | 无感（嵌入场景） | 高 | 门户嵌入/自动化 |
| Basic/Form | 极低 | 差 | 低 | 开发调试/兜底方案 |

**适用场景**
1. LDAP：企业内部已有AD域，所有员工有域账号
2. OAuth：研发团队用GitHub/GitLab账号，方便和代码仓库集成
3. SAML：大型企业有Okta/Azure AD/Keycloak等IdP
4. JWT：自研门户需要内嵌Grafana，用户无需二次登录
5. 匿名访问：公司TV大屏展示，不需要登录

**注意事项**
1. `allow_sign_up = true`时，通过任何认证的新用户都会自动创建Grafana账号——可能造成账号失控
2. LDAP的`group_mappings`如果配置错误，用户即使认证通过也没有Dashboard权限（角色默认Viewer）
3. OAuth的`allowed_organizations`强烈建议配置，防止公司外部人员登录
4. SAML的`single_logout`功能需要IdP支持，不是所有IdP都支持

**常见踩坑经验**
1. **Grafana LDAP日志显示"找不到用户"但用户确实存在**：问题在search_filter不匹配。AD中用户的标识属性是`sAMAccountName`不是`uid`或`cn`。
2. **OAuth登录后500错误**：`root_url`配置为HTTP但OAuth Provider要求HTTPS回调，协议不匹配。
3. **JWT Token过期时间短导致Dashboard频繁跳登录**：JWT的exp（过期时间）需要>Dashboard的刷新周期，建议至少15分钟。

**思考题**
1. 一个公司有三种员工：正式员工（有AD账号）、外包员工（有Grafana本地账号）、访客（只能看公共大屏）。如何配置Grafana的认证体系？
2. LDAP组映射可以将LDAP组映射到Grafana角色，但如果一个用户同时属于"admin组"和"editor组"，最终角色是什么？
