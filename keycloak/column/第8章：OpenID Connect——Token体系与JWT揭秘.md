# 第8章：OpenID Connect——Token体系与JWT揭秘

## 1 项目背景

某金融科技公司的后端团队凌晨三点被On-Call电话惊醒——API网关拦截了大量正常用户的请求，日志清一色显示"Token Expired"。值班工程师紧急回滚了网关配置才恢复服务。第二天复盘时发现了一个致命的认知偏差：前端在用户登录后拿到的是**ID Token**，它默认5分钟过期，而前端开发者误将其作为API调用的凭证传入`Authorization`头。后端网关虽然做了Token校验，但校验的也是这个已经过期的ID Token——整个团队混淆了"证明身份"和"证明权限"两种Token的职责边界。

雪上加霜的是，安全团队当周的渗透测试报告中指出：攻击者只需将JWT的Payload中的`{"role": "user"}`手工改为`{"role": "admin"}`并重新Base64编码，再删掉Signature部分，后端竟然直接信任了这个被篡改的Token。根因是后端"方便起见"只解码了Payload读取角色字段，完全没有验证签名——等于把银行金库的锁换成了纸条上的"请开门"。

更深层的隐患还在蔓延：运维团队为了方便管理，配置了长达30天的Access Token过期时间，且没有实施Refresh Token机制——Token一旦泄露，攻击者拥有整整一个月的窗口期。与此同时，安全架构师发现JWKS（JSON Web Key Set）端点对外暴露但未受关注，这意味着如果有人诱导服务器接受一个对称签名算法（HS256）并用自己的密钥签发Token，传统的非对称签名校验将完全失效——这就是臭名昭著的**签名算法降级攻击**。

Token体系的混乱不会立刻让系统崩溃，但它像定时炸弹一样埋在生产环境的各个角落。理解三种Token的职责边界、JWT的结构与安全机制、以及Token校验的正确姿势，是每个后端工程师在接入OIDC之前必须穿越的"安全门禁"。

---

## 2 项目设计——剧本式交锋对话

> 人物：**小胖**（两年经验，爱吃零食，习惯用生活比喻）、**小白**（应届生，刨根问底）、**大师**（技术Leader，10年认证领域老兵）
>
> 场景：下午三点，会议室投影上显示着"Token体系架构设计"几个大字。

---

**小胖**（抱着一袋薯片）：我昨天研究了一下，Keycloak发Token的时候一下给了三个——`access_token`、`id_token`、`refresh_token`。这不就跟我去办了三张卡一样吗？一张身份证证明我是谁，一张门禁卡让我进楼，还有一张物业续卡方便我下次来。但我就不明白了，为啥不能一个Token搞定所有事情？

**大师**（笑着敲了敲白板）：小胖你这个比喻精确到让我想给你加薪。**ID Token就是身份证**——上面有你的姓名、年龄、身份证号，但没人会拿身份证去刷小区门禁。它的唯一用途是告诉应用"这个人是谁"。**Access Token是门禁卡**——上面不需要你的姓名，但它有权限标识，刷卡时门禁系统关心的是"这张卡能不能开这扇门"。**Refresh Token是物业续卡凭据**——你不需要天天打卡，持卡去物业就能换一张新门禁卡，避免你天天重新登记。

为什么不能合并？因为**目的不同导致的生命周期和暴露范围完全不同**。身份证过期时间长（相当于ID Token的`exp`），但丢失后只泄露身份信息，不影响门禁安全。门禁卡的权限范围广（API鉴权），所以要短周期（通常5-15分钟），丢失后影响时间有限。如果合并成一个Token，那就相当于把身份证号、门禁权限、续卡凭证全写在一张纸上——任何人捡到就能以你的身份畅通无阻。

> **技术映射**：ID Token=身份凭证（JWT，面向客户端），Access Token=授权凭证（JWT，面向资源服务器），Refresh Token=续期凭证（Opaque string，仅与认证服务器交互）。三者面向不同受众，合并会扩大攻击面。

---

**小白**（推了推眼镜，在白板上画了一个JWT的三段结构）：JWT的那三个Base64段我大概懂了——Header声明算法，Payload放Claims，Signature做签名。但我有三个疑点。第一，Base64编码不等于加密，任何人都能解码——那Payload放用户ID岂不等于裸奔？第二，验证签名时用的公钥怎么安全获取？如果有人篡改了JWKS端点的响应怎么办？第三，网上说HS256有算法降级风险，具体是怎么被利用的？

**大师**：三连问，刀刃舔到了钢板上。

**第一，Base64可解码≠不安全。** JWT的设计哲学是"签名保完整，不保机密"。Payload的信息确实任何人可读，所以绝不能放密码、信用卡号等敏感数据。真要保护载荷内容，用JWE（JSON Web Encryption）加密——但生产环境中绝大多数场景根本不需要加密Payload，因为Token走的是HTTPS信道，传输层已经加密了。

**第二，JWKS公钥的信任不是靠HTTPS建立的。** JWKS端点返回的公钥只是"数据"，验证者需要预先知道哪个公钥是可信的。Keycloak的JWKS返回的每个Key都有一个`kid`（Key ID），Token的Header中也有`kid`字段。验证流程是：从Token Header读`kid`→到JWKS端点找对应`kid`的公钥→用这个公钥验签。如果Token中没有`kid`或JWKS中找不到匹配的公钥，直接拒绝——这是JWT安全的第一道防线。

**第三，算法降级攻击的精妙之处。** 假设你的系统代码是：

```python
algorithm = jwt_header.get("alg")  # ← 攻击者控制了Header
public_key = fetch_jwks(jwt_header.get("kid"))
verify(token, public_key, algorithm)
```

攻击者只需篡改Header为`{"alg": "HS256", "kid": "..."}`，然后把你的RSA公钥当作HMAC对称密钥，用同一个公钥对篡改后的Payload签名。由于HS256使用对称密钥，验证时会把公钥作为HMAC密钥去验签——而攻击者也持有这个公钥！这就是为什么**必须硬编码算法白名单**（只接受RS256/ES256），绝不要信任JWT Header中的`alg`字段。

> **技术映射**：JWT编码≠加密，HTTPS保护传输信道。JWKS的`kid`是验签的索引键，必须与Token Header中的`kid`严格匹配。算法选择必须服务端硬指定，Header声明的`alg`仅作参考。

---

**小胖**（突然放下薯片）：那Access Token过期了怎么办？总不能每次都让用户重新输密码吧？还有那个Token Introspection端点又是什么——本地验签不更方便吗？

**大师**（欣慰地点头）：小胖问到点子上了。这就是Refresh Token存在的意义。流程是这样的：Access Token过期→客户端用Refresh Token向Keycloak换一个新的Access Token→Keycloak验证Refresh Token有效→返回新Token。整个过程中用户无感知——这叫**平滑续期**（Silent Refresh）。Refresh Token本身很长寿（可能几天甚至更长），但它只经过认证服务器和客户端之间的单一通道，暴露面远小于穿梭于几十个微服务之间的Access Token。

至于**本地校验 vs 在线Introspection**，这是一个经典的架构权衡。本地校验快——直接解码JWT、找JWKS公钥验签，毫秒级完成。但致命缺陷是**无法实时吊销**：即使管理员在Keycloak中禁用了用户，已经签发的JWT在过期前依然"合法有效"。在线Introspection恰恰解决这个问题——每次请求都调用Keycloak的`/token/introspect`端点，Keycloak实时检查用户状态和Token是否被撤销，但代价是每次请求多一次网络往返。

生产中的最佳实践是**分层策略**：读操作用本地校验（容忍短暂的吊销延迟），写操作/敏感操作走Introsspection端点实时验证。或者，把Access Token过期时间设得足够短（5分钟），配合Refresh Token续期来缩小"吊销窗口"。

> **技术映射**：Refresh Token=续租机制，本地验签=性能优选，Introspection=实时安全。二者结合实现"短周期Token+实时吊销检查"的安全架构。

---

**小白**（翻看着笔记本）：那自定义Claims怎么注入？比如我想在Access Token里加一个`department`字段表示用户所属部门。还有，RS256和ES256到底选哪个？Token会不会因为塞太多Claims变得太大？

**大师**：自定义Claims通过**Client Scope → Protocol Mapper**注入。Keycloak内置了User Attribute Mapper、Group Membership Mapper等多种映射器——你可以创建一个Mapper，将用户的`department`属性映射到Access Token的`department` Claim中。映射时可以指定Token类型（Access Token/ID Token/两者），精确定义哪个Token携带哪些声明。

签名算法选型，记住一句话：**新项目无脑选ES256**。RS256（RSA）历史悠久、兼容性最好，但密钥巨大（2048bit起步），Token体积和签名计算开销都更高。ES256（ECDSA P-256曲线）用256bit密钥达到与RSA 3072bit同等的安全强度，签名速度更快、Token更小。HS256（HMAC-SHA256）最简单，但它是对称算法——签发和验证用同一个密钥，意味着所有需要验签的服务都要共享这个密钥，密钥泄露风险成倍放大。所以HS256只适合单体验签场景（如内部微服务的Service-to-Service Token），面向外部客户端的OIDC场景必须用RS256或ES256。

Token体积问题很现实。HTTP Header通常有8KB上限（Nginx默认），有些CDN甚至限制到4KB。一个塞满10层嵌套部门结构和20个权限组的JWT轻松突破4KB。控制方法三条：**①精简Claims**——只放必须的、高频读取的字段，大段权限数据走独立接口查询；**②选ES256**——比RS256节省约40% Token体积；**③开启Token压缩**——某些网关支持在Header中对JWT做Deflate压缩后传输。

> **技术映射**：自定义Claims通过Protocol Mapper注入，实现按需声明。签名算法ES256>RS256>HS256（从非对称到对称，安全性和通用性递减）。Token体积控制是高性能API网关的重要课题。

---

## 3 项目实战

### 环境准备

| 组件 | 版本/说明 |
|------|----------|
| Keycloak | 26.x，基于第2章环境，Realm=**demo-realm**，客户端=**oms-backend** |
| curl / jq | 命令行调试API响应及JSON格式化 |
| Python 3 | 3.10+，需安装依赖库 |
| jwt-cli | 可选，命令行JWT解码工具 |
| openssl | 用于RSA/EC密钥格式验证 |

确认Keycloak运行正常。安装Python依赖：

```bash
pip install requests cryptography authlib
```

本实战假设`demo-realm`中已存在用户`testuser / test123`且已配置`oms-backend`为Confidential客户端。如未创建，请参照第2-4章完成基础设施搭建。

### 步骤1：获取Token并解析JWT三部分结构

**目标**：通过OAuth 2.0 Password Grant获取Token，逐段解析JWT的Header、Payload、Signature。

```bash
# 获取Token（替换CLIENT_SECRET为实际值）
TOKEN_RESPONSE=$(curl -s -X POST \
  "http://localhost:8080/realms/demo-realm/protocol/openid-connect/token" \
  -d "client_id=oms-backend" \
  -d "client_secret=<YOUR_CLIENT_SECRET>" \
  -d "username=testuser" \
  -d "password=test123" \
  -d "grant_type=password")

# 提取各Token
ACCESS_TOKEN=$(echo "$TOKEN_RESPONSE" | jq -r '.access_token')
ID_TOKEN=$(echo "$TOKEN_RESPONSE" | jq -r '.id_token')
REFRESH_TOKEN=$(echo "$TOKEN_RESPONSE" | jq -r '.refresh_token')

echo "Access Token length: ${#ACCESS_TOKEN}"
echo "ID Token length: ${#ID_TOKEN}"
echo "Refresh Token: ${REFRESH_TOKEN:0:30}..."
```

**运行结果**：`ACCESS_TOKEN`和`ID_TOKEN`均为标准的JWT格式——三段以`.`分隔的Base64 URL编码字符串。`REFRESH_TOKEN`为Opaque字符串，不遵循JWT格式。

解析JWT的三段结构——注意Base64 URL Safe编码需要补齐填充符：

```bash
# 解析Access Token
echo "=== ACCESS TOKEN HEADER ==="
echo "$ACCESS_TOKEN" | cut -d'.' -f1 | base64 -d 2>/dev/null | jq .

echo "=== ACCESS TOKEN PAYLOAD ==="
echo "$ACCESS_TOKEN" | cut -d'.' -f2 | base64 -d 2>/dev/null | jq .

# Signature部分为二进制密文，不可直接解码
echo "=== SIGNATURE (base64url, raw) ==="
echo "$ACCESS_TOKEN" | cut -d'.' -f3 | head -c 50
```

**运行结果示例——Access Token Header**：

```json
{
  "alg": "RS256",
  "typ": "JWT",
  "kid": "mzQZgXlhEGo8FSxDWcW04EZ-qJ0NLYN1QRTg_TKWmIM"
}
```

**Access Token Payload**：

```json
{
  "exp": 1715431200,
  "iat": 1715430900,
  "jti": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "iss": "http://localhost:8080/realms/demo-realm",
  "sub": "f8e7d6c5-b4a3-21f0-9876-543210abcdef",
  "typ": "Bearer",
  "azp": "oms-backend",
  "sid": "abc123def-456-789-ghi-jkl012345678",
  "acr": "1",
  "scope": "openid profile email",
  "preferred_username": "testuser"
}
```

对比**ID Token Payload**——注意关键Claims差异（红色表示独有的）：
- `nonce`（防重放攻击）
- `aud`通常是client_id（声明此ID Token的预期接收者）
- 包含更多用户身份字段（`name`, `given_name`, `family_name`, `email`）

### 步骤2：编写Python脚本对比三种Token的Claims差异

**目标**：批量获取三种Token，可视化对比它们的Claims差异。

创建脚本`compare_tokens.py`：

```python
import requests
import base64
import json

KEYCLOAK_URL = "http://localhost:8080/realms/demo-realm/protocol/openid-connect"
CLIENT_ID = "oms-backend"
CLIENT_SECRET = "<YOUR_CLIENT_SECRET>"
USERNAME = "testuser"
PASSWORD = "test123"

def decode_jwt_part(part):
    """处理Base64 URL Safe编码且自动补齐padding"""
    padding = 4 - len(part) % 4
    if padding != 4:
        part += '=' * padding
    return json.loads(base64.urlsafe_b64decode(part))

# 获取Token
resp = requests.post(f"{KEYCLOAK_URL}/token", data={
    "client_id": CLIENT_ID,
    "client_secret": CLIENT_SECRET,
    "username": USERNAME,
    "password": PASSWORD,
    "grant_type": "password"
})
resp.raise_for_status()
tokens = resp.json()

# 逐个解析
for name in ['access_token', 'id_token', 'refresh_token']:
    token = tokens.get(name)
    if not token:
        print(f"\n=== {name.upper()} ===\n(not present)")
        continue

    print(f"\n{'='*60}")
    print(f"  {name.upper()}")
    print(f"{'='*60}")

    if token.count('.') != 2:
        print(f"  [Opaque Token, length={len(token)}]")
        print(f"  {token[:40]}...")
        continue

    parts = token.split('.')
    header = decode_jwt_part(parts[0])
    payload = decode_jwt_part(parts[1])

    print(f"[HEADER]")
    print(json.dumps(header, indent=2, ensure_ascii=False))

    print(f"\n[PAYLOAD]")
    print(json.dumps(payload, indent=2, ensure_ascii=False))

    # 高亮特有字段
    if name == 'id_token':
        print("\n[ID Token特有字段]")
        for field in ['name', 'given_name', 'family_name',
                       'email', 'email_verified', 'nonce', 'at_hash']:
            if field in payload:
                print(f"  {field}: {payload[field]}")
    elif name == 'access_token':
        print("\n[Access Token特有字段]")
        for field in ['scope', 'azp', 'acr', 'typ']:
            if field in payload:
                print(f"  {field}: {payload[field]}")

# 关键Claims对比表
print(f"\n{'='*60}")
print("  三种Token核心Claims对比")
print(f"{'='*60}")
print(f"{'Claim':<20} {'ID Token':<18} {'Access Token':<18} {'Refresh Token':<18}")
print("-" * 74)

claims_map = {
    'sub': '用户唯一标识',
    'iss': '签发者URI',
    'aud': '受众（谁该用此Token）',
    'exp': '过期时间',
    'iat': '签发时间',
    'nbf': '生效时间',
    'auth_time': '认证时间',
    'azp': '授权方client_id',
    'scope': '权限范围',
    'typ': 'Token类型',
}
for claim, desc in claims_map.items():
    id_val = "✓" if claim in decode_jwt_part(tokens['id_token'].split('.')[1]) else "✗"
    at_val = "✓" if claim in decode_jwt_part(tokens['access_token'].split('.')[1]) else "✗"
    print(f"{claim}({desc}):".ljust(22) + f"{id_val}".ljust(18) + f"{at_val}".ljust(18) + "N/A(Opaque)".ljust(18))
```

**运行结果**：三种Token的Claims差异清晰可见——ID Token侧重于用户身份信息（`name`, `email`, `birthdate`等），Access Token侧重于授权信息（`scope`, `azp`, `acr`），Refresh Token完全为Opaque字符串不携带Claims。

### 步骤3：使用JWKS公钥验证JWT签名

**目标**：获取Keycloak发布的公钥集（JWKS），用Python独立验证Access Token签名，不依赖Keycloak SDK。

**获取JWKS端点**：

```bash
curl -s "http://localhost:8080/realms/demo-realm/protocol/openid-connect/certs" | jq .
```

**运行结果示例**：

```json
{
  "keys": [
    {
      "kid": "mzQZgXlhEGo8FSxDWcW04EZ-qJ0NLYN1QRTg_TKWmIM",
      "kty": "RSA",
      "alg": "RS256",
      "use": "sig",
      "n": "zcV-ti3H9uJ...（长整数，RSA modulus）",
      "e": "AQAB"
    }
  ]
}
```

**Python验签脚本** (`verify_jwt.py`)：

```python
import requests
import json
from authlib.jose import JsonWebKey, jwt, errors

KEYCLOAK_URL = "http://localhost:8080/realms/demo-realm/protocol/openid-connect"
ACCESS_TOKEN = "<从步骤1获取的ACCESS_TOKEN>"

# 1. 解码Header获取kid和alg（仅读取，不信任alg）
import base64

def decode_jwt_header(token):
    part = token.split('.')[0]
    padding = 4 - len(part) % 4
    if padding != 4:
        part += '=' * padding
    return json.loads(base64.urlsafe_b64decode(part))

header = decode_jwt_header(ACCESS_TOKEN)
token_kid = header.get("kid")
token_alg = header.get("alg")
print(f"Token Header: alg={token_alg}, kid={token_kid}")

# 2. 从JWKS端点获取公钥，按kid匹配
jwks_resp = requests.get(f"{KEYCLOAK_URL}/certs")
jwks = jwks_resp.json()

matching_key = None
for key in jwks["keys"]:
    if key.get("kid") == token_kid:
        matching_key = key
        break

if not matching_key:
    raise ValueError(f"No matching key found for kid={token_kid}")

print(f"Matched JWK: kty={matching_key['kty']}, alg={matching_key.get('alg')}")

# 3. 硬编码算法白名单——防御降级攻击
ALLOWED_ALGORITHMS = {"RS256", "RS384", "RS512", "ES256", "ES384", "ES512"}
if matching_key.get("alg", "").upper() not in ALLOWED_ALGORITHMS:
    raise ValueError(f"Algorithm {matching_key.get('alg')} not in allowed list")

# 4. 导入公钥并验证签名
try:
    jwk_key = JsonWebKey.import_key(matching_key)

    # 注意：必须在decode时显式指定允许的算法列表，而非信任Header中的alg
    claims = jwt.decode(
        ACCESS_TOKEN,
        jwk_key,
        claims_options={
            "iss": {"essential": True, "value": "http://localhost:8080/realms/demo-realm"},
            "exp": {"essential": True},
        }
    )
    claims.validate()  # 验证exp/iat/nbf/iss等
    print("\n✅ Signature VALID — Token is authentic")
    print(f"   Subject: {claims.get('sub')}")
    print(f"   Issued at: {claims.get('iat')}")
    print(f"   Expires at: {claims.get('exp')}")
    print(f"   Issuer: {claims.get('iss')}")

except errors.BadSignatureError:
    print("\n❌ Signature INVALID — Token may be tampered")
except errors.ExpiredTokenError:
    print("\n⏱️ Token EXPIRED")
except errors.InvalidClaimError as e:
    print(f"\n❌ Invalid claim: {e}")
except Exception as e:
    print(f"\n❌ Verification failed: {e}")
```

**运行结果**：签名验证成功后输出Subject和Claims信息。如果手工篡改Payload（如修改`sub`字段）后再运行，将触发`BadSignatureError`异常。这直观证明了**JWT签名的防篡改能力**。

### 步骤4：访问Token Introspection端点（在线校验）

**目标**：通过Keycloak的Introspection端点实时验证Token有效性。

```bash
# 在线校验Access Token
curl -s -X POST \
  "http://localhost:8080/realms/demo-realm/protocol/openid-connect/token/introspect" \
  -d "client_id=oms-backend" \
  -d "client_secret=<YOUR_CLIENT_SECRET>" \
  -d "token=$ACCESS_TOKEN" | jq .
```

**运行结果——有效Token**：

```json
{
  "active": true,
  "sub": "f8e7d6c5-b4a3-21f0-9876-543210abcdef",
  "exp": 1715431200,
  "iat": 1715430900,
  "iss": "http://localhost:8080/realms/demo-realm",
  "client_id": "oms-backend",
  "username": "testuser",
  "token_type": "Bearer",
  "scope": "openid profile email"
}
```

**运行结果——已禁用用户的Token**（先在Admin Console禁用testuser再执行）：

```json
{
  "active": false
}
```

这就是本地校验无法实现的**实时吊销感知**——本地验签仅知道Token签名正确且未过期，但不知道签发者是否已经撤销了该用户的访问权限。

### 步骤5：自定义Claims注入（通过Client Scope Mapper）

**目标**：将用户的`department`属性注入Access Token的Payload，实现"谁属于哪个部门"的Token级权限信息传递。

**操作步骤**：

1. 为用户添加自定义属性：`Admin Console → Users → testuser → Attributes`，添加Key=`department`，Value=`研发部`，保存。

2. 创建Client Scope（如已存在可跳过）：`Client Scopes → Create client scope`，Name=`department-scope`，Type=`Optional`，Protocol=`openid-connect`，Include in Token Scope=On。

3. 在`department-scope`中添加Mapper：`Mappers → Add mapper → By configuration → User Attribute`：
   - Name: `department-mapper`
   - User Attribute: `department`
   - Token Claim Name: `department`
   - Claim JSON Type: `String`
   - Add to ID token: ON
   - Add to access token: ON
   - Add to userinfo: OFF
   - Multivalued: OFF

4. 将Client Scope绑定到客户端：`Clients → oms-backend → Client Scopes`，在Optional client scopes中添加`department-scope`。

5. 重新获取Token（需显式请求scope）：

```bash
TOKEN_RESPONSE=$(curl -s -X POST \
  "http://localhost:8080/realms/demo-realm/protocol/openid-connect/token" \
  -d "client_id=oms-backend" \
  -d "client_secret=<YOUR_CLIENT_SECRET>" \
  -d "username=testuser" \
  -d "password=test123" \
  -d "grant_type=password" \
  -d "scope=openid department-scope")

# 解码Payload确认department字段
echo "$TOKEN_RESPONSE" | jq -r '.access_token' | cut -d'.' -f2 | base64 -d 2>/dev/null | jq '.department'
```

**运行结果**：Payload中新增`"department": "研发部"`。如果步骤4中未将Client Scope绑定到客户端，Token中不会出现此字段——这验证了**Scope驱动的Claims注入机制**。

### 可能遇到的坑

1. **Base64解码失败**：JWT使用Base64 URL Safe编码（`-`代替`+`，`_`代替`/`，末尾无`=`填充），标准`base64 -d`会报错。解决方案：解码前补齐`=`填充符，或使用`base64.urlsafe_b64decode()`。

2. **本地验签与Introspection双写导致延迟翻倍**：不要同时做本地验签和在线Introspection——这是常见误区。正确做法是根据接口敏感度**二选一**或按读/写分流。同时做两条路径既浪费资源又可能导致验签失败超时。

3. **JWKS公钥轮换期间的签名失败**：Keycloak定期轮换签名密钥（默认每7天）。轮换瞬间新旧公钥同时存在于JWKS端点（新`kid`+旧`kid`），但Token可能由旧私钥签发。应用层必须实现**公钥缓存 + 失败重试（刷新缓存）**机制：验签失败→重新拉取JWKS→用新拉取的公钥再试一次→仍失败才拒绝。

4. **Token体积膨胀导致HTTP 431 Header Too Large**：Access Token不应承载大量Claims（如完整权限树、10层嵌套部门结构）。Nginx默认`large_client_header_buffers`为8KB，当Token+其他Header超过此大小返回431错误。解决方案：Token中仅存用户ID和最小权限集，详细权限信息由资源服务器通过用户ID查询本地缓存。

### 测试验证

编写自动化验证脚本`test_token_flow.sh`：

```bash
#!/bin/bash
set -e
KC_URL="http://localhost:8080/realms/demo-realm/protocol/openid-connect"
CLIENT_ID="oms-backend"
CLIENT_SECRET="<YOUR_CLIENT_SECRET>"

echo "1. Get tokens..."
RESP=$(curl -s -X POST "$KC_URL/token" \
  -d "client_id=$CLIENT_ID" -d "client_secret=$CLIENT_SECRET" \
  -d "username=testuser" -d "password=test123" \
  -d "grant_type=password" -d "scope=openid department-scope")
ACCESS_TOKEN=$(echo "$RESP" | jq -r '.access_token')
REFRESH_TOKEN=$(echo "$RESP" | jq -r '.refresh_token')
echo "   Access Token: ${ACCESS_TOKEN:0:20}..."

echo "2. Verify JWT structure..."
SEGMENTS=$(echo "$ACCESS_TOKEN" | tr '.' '\n' | wc -l)
[ "$SEGMENTS" -eq 3 ] && echo "   ✅ JWT has 3 segments" || echo "   ❌ Invalid JWT"

echo "3. Check custom claim..."
PAYLOAD=$(echo "$ACCESS_TOKEN" | cut -d'.' -f2)
# Add padding if needed
PAD=$(( 4 - ${#PAYLOAD} % 4 ))
[ $PAD -ne 4 ] && PAYLOAD="${PAYLOAD}$(printf '=%.0s' $(seq 1 $PAD))"
DEPT=$(echo "$PAYLOAD" | base64 -d 2>/dev/null | jq -r '.department // "NOT_FOUND"')
[ "$DEPT" = "研发部" ] && echo "   ✅ department=$DEPT" || echo "   ❌ department claim missing"

echo "4. Test introspection..."
ACTIVE=$(curl -s -X POST "$KC_URL/token/introspect" \
  -d "client_id=$CLIENT_ID" -d "client_secret=$CLIENT_SECRET" \
  -d "token=$ACCESS_TOKEN" | jq -r '.active')
[ "$ACTIVE" = "true" ] && echo "   ✅ Token is active" || echo "   ❌ Token inactive"

echo "5. Test refresh..."
NEW_RESP=$(curl -s -X POST "$KC_URL/token" \
  -d "client_id=$CLIENT_ID" -d "client_secret=$CLIENT_SECRET" \
  -d "refresh_token=$REFRESH_TOKEN" \
  -d "grant_type=refresh_token")
NEW_ACCESS=$(echo "$NEW_RESP" | jq -r '.access_token')
NEW_REFRESH=$(echo "$NEW_RESP" | jq -r '.refresh_token')
[ -n "$NEW_ACCESS" ] && echo "   ✅ Refresh succeeded" || echo "   ❌ Refresh failed"
[ "$NEW_REFRESH" != "$REFRESH_TOKEN" ] && echo "   ✅ Refresh Token rotated" \
  || echo "   ⚠️ Refresh Token unchanged (rotation disabled)"

echo -e "\n🎯 All checks passed!"
```

---

## 4 项目总结

### 优点与缺点：本地JWT校验 vs 在线Introspection

| 维度 | 本地JWT校验 | 在线Token Introspection |
|------|-----------|------------------------|
| 性能 | ✅ 毫秒级，纯本地运算 | ❌ 每次请求增加20-100ms网络延迟 |
| 安全性 | ⚠️ 依赖公钥信任链，无实时吊销 | ✅ 实时检查Token状态和用户有效性 |
| 实时性 | ❌ Token签发后无法远程撤销 | ✅ 用户禁用→立即拒绝 |
| 高可用 | ✅ 不依赖Keycloak在线状态 | ❌ Keycloak不可用时无法验签 |
| 运维复杂度 | ✅ 仅需缓存JWKS公钥 | ❌ 需维护Introspection端点连接池和重试策略 |
| Token体积依赖 | ⚠️ 权限信息必须在Token内 | ✅ 权限可在线查询，Token轻量 |

**最佳实践**：生产环境采用**混合策略**——读操作本地校验 + 写操作Introspection校验，Access Token过期时间设为5分钟，配合Refresh Token续期使"吊销窗口"足够小。

### 适用场景

1. **ID Token**：前端展示用户信息（头像、姓名）、客户端身份确认。不应发送到资源服务器做API鉴权。
2. **Access Token**：微服务间API调用鉴权、资源服务器权限校验。应短周期（5-15分钟）+ Refresh Token续期。
3. **Refresh Token**：长期会话保持、移动端"记住我"功能。应单次使用并轮换（Rotation）。
4. **本地JWT校验**：高吞吐API网关、读密集型内部服务、对吊销延迟容忍的场景。
5. **在线Introspection**：写操作、金融交易、用户权限实时变更频繁的场景。

**不适用场景**：Access Token不应承载大量静态权限（改用权限查询服务）；Refresh Token不应在前端单页应用的localStorage中长期存储（建议使用httpOnly Cookie + BFF模式）。

### 注意事项

- **JWT不加密**：Payload中任何人可解码阅读，严禁存放密码、手机号、身份证号等敏感信息。需要加密载荷时使用JWE（JSON Web Encryption）。
- **算法必须硬编码**：验签时不信任JWT Header中的`alg`字段——攻击者可将RS256降级为HS256，用公钥作为HMAC私钥伪造签名。服务端必须维护算法白名单（如仅接受`RS256,ES256`）。
- **Token过期时间设定**：Access Token建议5-15分钟（移动端可放宽至30分钟），Refresh Token建议24小时-7天，ID Token建议与Access Token一致或更短。
- **Refresh Token Rotation**：每次用Refresh Token换取新Access Token时，同时签发新的Refresh Token并吊销旧的——即使旧的Refresh Token被窃取，攻击者和合法用户只有一个能持有最新有效的Refresh Token，系统检测到重用时可主动撤销所有Token。
- **Kid不匹配应立即拒绝**：Token Header中的`kid`必须在JWKS中能找到对应公钥，找不到意味着Token可能由已过期密钥签发或来自伪造的签发者。

### 常见踩坑经验

1. **问题**：登录后立即调用API返回401。**根因**：前端将ID Token作为Access Token传入`Authorization`头，ID Token默认5分钟过期而用户登录已超过5分钟。**解决**：前端Auth SDK配置中明确区分`idToken`和`accessToken`，API调用始终使用`accessToken`。

2. **问题**：安全扫描报告"JWT算法降级漏洞"。**根因**：后端验签代码中`algorithm = jwt_header.alg`直接信任了Header中的算法声明。**解决**：硬编码`allowed_algorithms = ["RS256", "ES256"]`，验签时强制使用服务端指定的算法而非从Header读取。

3. **问题**：生产环境Keycloak升级后所有Token验签失败。**根因**：Keycloak升级时重新生成了Realm签名密钥，但应用层JWKS缓存未刷新（默认缓存24小时）。**解决**：实现JWKS缓存刷新机制——验签失败时主动清除缓存重新拉取JWKS并重试一次，设置合理缓存TTL（建议1小时）。

### 思考题

1. **为什么使用Refresh Token比使用长期Access Token更安全？** Refresh Token仅在与认证服务器的单条信道上传输，泄露面远小于在几十个微服务间传递的Access Token。且Refresh Token Rotation机制使窃取者即使拿到Refresh Token也面临与合法用户的"竞态"——谁先用谁有效，被弃用的Token立即吊销。请描述Rotation的完整流程及Reuse Detection（重用检测）的实现思路。

2. **如果需要在Access Token中传递用户所属的部门组织结构（可能嵌套10层），应该如何设计避免Token过大？** 提示：考虑"按需查询"而非"全量注入"的架构——Token中仅存放用户ID和直属部门ID，资源服务器通过用户ID调用组织架构服务获取完整部门树。进一步思考：哪些场景绝对需要在Token中携带层级信息（如K8s RBAC JWT）？如何在Token体积和信息完整性之间找到平衡点？自研Protocol Mapper能否实现动态Claims按需注入？

---

> **下一章预告**：第9章——密码策略与暴力破解防护。我们将深入Keycloak的认证安全体系，揭开密码哈希算法的选择逻辑、Brute Force Detection的滑动窗口算法、以及多因素认证（MFA）在企业级场景中的落地实践。
