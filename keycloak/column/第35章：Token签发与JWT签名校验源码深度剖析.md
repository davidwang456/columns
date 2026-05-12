# 第35章：Token签发与JWT签名校验源码深度剖析

## 1 项目背景

某金融科技公司的安全团队在一次季度渗透测试中发现了一个严重漏洞：测试人员将一个合法JWT的Header中`alg`字段从`RS256`改为`none`，然后将Signature部分置空。令人震惊的是——发往订单微服务`oms-backend`的这个"无签名Token"竟然通过了校验，返回了200 OK和完整的订单数据。深入排查后发现根因：订单服务使用的旧版`jjwt 0.9.0`库在`parseClaimsJwt()`方法中，当遇到`alg:none`时不执行签名校验，直接信任Token中的Claims——这就是著名的"算法混淆漏洞"（Algorithm Confusion Attack）。

安全团队顺着这条线索进一步调查Keycloak的Token安全体系，提出了三个深层问题。第一，Keycloak如何选择签名算法？默认是RS256，但控制台里还列出了ES256、HS256、PS256等——这些算法分别有什么区别？第二，密钥如何生成、存储和轮换？Keycloak的JWKS端点（`/protocol/openid-connect/certs`）对外公开暴露公钥，任何拿到Token的人都能获取验证密钥——这是安全设计还是漏洞？第三，ID Token和Access Token的生成过程有什么不同？Refresh Token Rotation机制如何防止Refresh Token被窃取后的重放使用？

安全团队的追问揭示了Token安全体系的三个关键层级：

**签名层**——算法的安全边界。RS256（RSA+PKCS#1 v1.5 + SHA-256）使用非对称密钥对，私钥签名、公钥验证，符合OpenID Connect规范的要求。ES256（ECDSA P-256 + SHA-256）虽然签名更短、性能更高，但在部分传统系统中兼容性不足。HS256（HMAC + SHA-256）使用共享密钥——意味着所有验证Token的服务都必须持有同一个密钥，密钥泄露范围越广，安全风险越高。这就是Keycloak默认选择RS256的核心原因：在"签名者"和"验证者"之间建立信任隔离——只有Keycloak拥有私钥，所有后端服务只需公钥即可验证。

**密钥管理层**——轮换与验证的时序博弈。密钥轮换（Key Rotation）是安全最佳实践，但它带来一个经典的时间窗口问题：新密钥生成的瞬间，已签发但尚未过期的数十万Token仍用旧密钥签名。如果立即删除旧密钥，那些合法Token的验证将全部失败，导致大范围用户掉线。Keycloak的解决方案是"延迟删除"——新密钥生成后，旧密钥仍保留一段时间（默认1小时，可配置），这段时间内JWKS端点同时提供新旧两套公钥，通过`kid`（Key ID）做多版本匹配。

**Claims注入层**——多源数据的优先级合并。一个Access Token的Payload由多个来源的Claims按优先级合并而成：标准Claims（`iss`、`sub`、`aud`、`exp`、`iat`、`nbf`）由TokenManager强制注入（优先级最高）、角色Claims（`realm_access.roles`、`resource_access.{client}.roles`）由RoleListMapper注入、用户属性Claims（`email`、`preferred_username`）由UserPropertyMapper注入、自定义Claims由ProtocolMapper SPI注入。当多个来源声称为同一个Claim名设置值时，后执行者会覆盖先执行者的值——这就是Claims覆盖规则。

本章将从Keycloak源码出发，追踪Token从签发到签名校验的完整生命周期，深入解析签名算法选择、密钥管理、Claims注入优先级、Refresh Token Rotation的防重放机制，并实战验证"算法混淆攻击"的防护效果。

---

## 2 项目设计——剧本式交锋对话

**小胖**（手里拿着一张银行本票在晃悠）：大师！我昨天去银行办业务，突然想通了JWT签名。你看银行本票——银行用它的钢印（私钥）盖章，任何商家拿到这张本票后，用银行公布的钢印图样（公钥）对着光一比对，就知道是真票还是假票。银行不用把钢印图样寄给每个商家——贴在官网上，谁都能下载。这不就是RS256！Keycloak用它的私钥给Token盖章，后端服务从JWKS端点下载公钥验证。但我就一个疑问——为啥不用HS256呢？HS256用一把共享密钥，加密解密都更快，而且没有公钥泄露的问题，多简单！

**大师**：小胖你这个本票比喻把非对称加密的精髓抓住了。但HS256的"简单"恰恰是它的致命伤。我问你——银行可以把钢印图样发给全国几百万商家验票，但如果HS256是用一个大家都知道的共享密钥盖章……那每个商家都握着这把共享密钥。如果其中一家商家的员工把密钥泄露出去，攻击者不仅能用这个密钥"验证"本票——他还能用这个密钥自己"伪造"本票！作为验证者，你拿到一张本票时无法区分：这钢印是银行盖的，还是某个拿到共享印章的人私自盖的。这就是**签名者不可甄别**的问题——HS256下，签名和验证用的是同一把密钥，掌握密钥的各方在密码学上是平等的，都能"代表"签发者。

相比之下，RS256的私钥只有Keycloak知道，公钥可以满天飞——拿到公钥的人只能"验票"不能"盖章"。这就是非对称密钥的**签名者鉴别能力**——你可以100%确信：只要公钥验证通过，这个Token一定是持有对应私钥的Keycloak签发的。

> **大师技术映射**：银行钢印（私钥）→ RS256签名；钢印图样贴在官网上（公钥）→ JWKS端点公开PEM/JWK。HS256共享密钥 → 在"签名者鉴别"上是盲目的——你不知道签名者是谁。

---

**小白**（飞快地翻着Keycloak源码）：大师，我追踪到Keycloak的密钥管理类`ActiveRsaKey`——它不光存了一个`KeyPair`，还有`kid`、`certificate`（X.509证书）、`use`（SIG签名/ENC加密）、`notBefore`和`notAfter`。我更想知道的是——密钥轮换到底是怎么触发的？旧的ActiveRsaKey什么时候变成Inactive？如果旧的密钥已经失效了，那用旧密钥签发的Token还能验证吗？

**大师**：密钥生命周期管理在Keycloak中由`KeyProvider` SPI负责。服务器首次启动时，`RsaKeyProvider`检测到没有Active Key，自动生成一个新的RSA密钥对并置为Active状态。密钥轮换可以通过三种方式触发：管理员在Admin Console点击"Rotate Key"按钮、调用Admin REST API `/admin/realms/{realm}/keys/rotate`、或者到达密钥的`notAfter`时间后自动触发。

轮换的真正挑战在"验证窗口"上。Keycloak的策略是**轮换时生成新的Active Key，但旧Key并不立即删除**——它被标记为Passive状态，仍保留在密钥列表中，`kid`依然有效，JWKS端点继续暴露其公钥。直到旧Key的过期时间（默认轮换后60分钟）到达，才从JWKS端点和密钥存储中物理移除。这意味着：在这个60分钟的窗口内，用旧`kid`签发的Token和新`kid`签发的Token同时合法，验证方只要在JWKS返回的公钥列表中找到匹配`kid`的公钥即可。

不过，这个窗口对验证方（微服务）也提出了要求——不能"一次性"加载JWKS并永远缓存。验证方必须定期刷新JWKS缓存（推荐每5分钟刷新一次），否则旧Key从JWKS端点移除后验证方仍缓存着旧列表，新Key签发的Token反而验证失败。

> **大师技术映射**：密钥轮换保留旧Key = 换门锁的同时给你一把新旧两把钥匙都能开门的过渡期（60分钟），确保你口袋里的旧钥匙在配新钥匙期间不会让你被锁在外面。JWKS缓存刷新 = 商家必须定期上网查看银行钢印图样有没有更新，不能打印出来贴墙上后就再也不管了。

---

**大师**（转向小胖）：你前面提到了alg=none攻击。原理是这样的——JWT规范允许`alg`设为`none`表示Token不使用签名（主要用于调试场景）。攻击者拦截一个合法JWT后，将Header改为`{"alg":"none","kid":"..."}`并保留Payload和空Signature，发给校验方。如果一个JWT库解析到`alg:none`后跳过第67-96字节的签名校验步骤，直接返回解码后的Payload——攻击成功。

**小白**：Keycloak怎么防护这个？它在签发层面能不能阻止下游被攻击？

**大师**：Keycloak在多层面提供了防护。**签发层面**：在`TokenManager.encodeToken()`中，签名算法从客户端配置`token.endpoint.auth.signing.alg`读取——假设配置了RS256，Keycloak强制使用私钥签名签发Token。如果客户端的算法配置为`none`，验证将直接拒绝——`AlgorithmResolver`会校验算法是否在允许列表中。**验证层面**：Keycloak自身的Token验证（通过`TokenVerifier`类）强制要求算法必须匹配配置——收到`alg:none`的Token直接返回"Unsupported algorithm"。**针对下游的防护**：通过JWKS端点告诉所有验证方"Keycloak签发的Token一定使用RS256或ES256"，鼓励验证方在代码中强制声明`algorithms=["RS256","ES256"]`，拒绝`alg:none`。

**小胖**（眼睛一亮）：说到下游，Refresh Token被偷了怎么办？小偷拿着Refresh Token就能无限换新Access Token？

**大师**：这就是Refresh Token Rotation的核心价值。Keycloak的Rotation机制遵循以下规则：每次使用Refresh Token换取新的Access Token时，Keycloak同时撤销旧的Refresh Token并签发一个新的Refresh Token。这意味着——如果合法用户和攻击者同时持有同一个Refresh Token，谁先用谁就"赢"（拿到新Tokens），另一个人拿到的旧Refresh Token在下次使用时已被标记为失效，请求返回400错误。这就是**自动防重放**——无需维护黑名单，直接通过Token替换实现原子性的"使用权竞争"。

更深一层，Keycloak还支持**Reuse Detection**（复用检测）：如果发现一个已经被使用过的Refresh Token再次被提交（说明存在两个不同的使用者），Keycloak会立即撤销与该用户Session关联的**所有**Refresh Token和Access Token，强制用户重新登录。这比单纯的"谁先用谁赢"更进一步——检测到Token泄露后主动拉闸。

> **大师技术映射**：Refresh Token Rotation = 公交卡每次刷卡后自动发给你一张新车票，旧车票立即作废——小偷捡到你扔掉的旧车票，上车刷卡时发现已经失效。Reuse Detection = 如果检票员发现有人试图用已作废的车票上车，立刻锁卡并通知你重新注册。

---

**小白**（在白板上画了一个时间线）：大师，我想讨论一下签名算法的未来。我注意到Cloudflare、Apple等公司已经开始在生产环境使用Ed25519（EdDSA）算法——它的签名速度比RSA快10倍，签名长度只有64字节。Keycloak 26还不支持EdDSA，但未来会不会加入？还有一个更长远的问题——量子计算的出现会不会彻底颠覆JWT的签名体系？

**大师**：EdDSA（Edwards-curve Digital Signature Algorithm，最常用的实例是Ed25519）确实是签名算法的未来方向。它的核心优势是"小而快"——Ed25519的密钥只有256位（32字节），签名为512位（64字节），而RSA-2048的密钥为256字节，签名为256字节。更重要的是，Ed25519在签名和验证的CPU耗时上比RSA-2048快一个数量级——这意味着在高并发Token签发场景下，从RSA切换到Ed25519可以显著降低Keycloak节点的CPU压力。

Nimbus JOSE+JWT库（Keycloak使用的底层库）从9.x版本已支持EdDSA，Keycloak从26版本开始也在内部的`KeyProvider` SPI中预留了EdDSA的扩展点（`EdDSAKeyProvider`）。但要完全支持EdDSA有三个前置条件：JDK 15+（JDK 15才原生支持Ed25519）、客户端库兼容（下游的`jjwt`、`nimbus-jose-jwt`、`auth0-java`等都必须支持EdDSA验证）、HSM/Cloud KMS支持（如果Keycloak使用云密钥管理服务，需要该服务也支持EdDSA）。

至于量子计算——Shor算法可以在多项式时间内分解大整数，这意味着RSA和ECDSA都会被量子计算机攻破。NIST已经标准化了三项后量子密码学（PQC）算法：CRYSTALS-Kyber（密钥封装）、CRYSTALS-Dilithium和FALCON（数字签名）。但PQC签名的尺寸远大于传统签名——Dilithium的签名约为2.5KB，是RSA-2048的10倍。这对于JWT来说是个挑战：HTTP Header的8KB限制意味着一个签名就可能占掉1/3的配额。业界正在探索"混合签名"方案——在传统签名（RSA/ECDSA）上叠加PQC签名，确保在量子计算尚未成熟时保持向后兼容，同时为后量子时代做好过渡。

> **大师技术映射**：Ed25519 → 从钢印升级为激光雕刻——更快更精细。量子计算 → 银行研发了能复制任何钢印的3D打印机，传统钢印图样全部失效，需要全新的"量子钢印"技术。混合签名 → 过渡期在本票上同时盖传统钢印和新量子钢印，直到所有商家升级了量子验证设备。

---

| 生活比喻 | 技术映射 |
|---------|---------|
| 银行钢印盖章，图样公开 | RS256私钥签名，JWKS公开公钥 |
| 共享印章谁拿到都能盖 | HS256无法辨别签名者身份 |
| 换门锁留60分钟过渡期 | 密钥轮换后旧Key延迟删除 |
| 公交卡刷一次换一张新车票 | Refresh Token Rotation防重放 |
| 激光雕刻 vs 钢印 | EdDSA vs RSA签名性能 |
| 量子3D打印能复制任何钢印 | Shor算法攻破RSA/ECDSA |

---

## 3 项目实战

### 环境准备

| 组件 | 版本/说明 |
|------|----------|
| JDK | 17+（与Keycloak 26.x编译要求一致） |
| Maven | 3.9+ |
| Keycloak源码 | 26.x，基于第33-34章搭建的源码调试环境 |
| IDE | IntelliJ IDEA（配置Remote Debug端口5005） |
| Python | 3.10+ |
| Python依赖 | `pyjwt[crypto]>=2.8.0`、`cryptography>=41.0.0`、`requests>=2.31.0` |

---

### 步骤1：追踪Token生成源码链路

**目标**：在Keycloak源码中设置断点，追踪从Token请求入口到JWT字符串生成的完整调用链。

**1.1 入口：TokenEndpoint.processGrantRequest()**

在`services/src/main/java/org/keycloak/protocol/oidc/endpoints/TokenEndpoint.java`中，`processGrantRequest()`方法是所有Token请求的统一入口。它解析`grant_type`参数后路由到不同的子处理方法：

```java
// TokenEndpoint.java - 简化逻辑
@POST
public Response processGrantRequest() {
    String grantType = formParams.getFirst(OAuth2Constants.GRANT_TYPE);

    switch (grantType) {
        case OAuth2Constants.AUTHORIZATION_CODE:
            return codeToToken();           // authorization_code换Token
        case OAuth2Constants.REFRESH_TOKEN:
            return refreshTokenGrant();     // refresh_token换新Token
        case OAuth2Constants.CLIENT_CREDENTIALS:
            return clientCredentialsGrant(); // 客户端凭证模式
        // ... 其他grant_type
    }
}
```

**1.2 中断：TokenManager.encodeToken()**

无论哪个grant_type，最终都会调用`TokenManager.encodeToken()`生成JWT。在IDEA中定位到`services/src/main/java/org/keycloak/services/managers/TokenManager.java`，在`encodeToken()`方法的第一个`if`语句上设置断点：

```java
// TokenManager.java - encodeToken() 简化核心逻辑
public Token encodeToken(KeycloakSession session, RealmModel realm,
                         UserModel user, ClientModel client,
                         UserSessionModel userSession, AuthenticatedClientSessionModel clientSession,
                         TokenType tokenType) {

    // 1. 选择签名算法
    Algorithm algorithm = getSignatureAlgorithm(session, realm, client);

    // 2. 获取当前Active的签名密钥
    ActiveKey activeKey = session.keys().getActiveKey(
        realm, KeyUse.SIG, algorithm.getAlgorithm()
    );

    // 3. 构建JWT Header
    //    {"alg":"RS256","kid":"45f9e3a2-...","typ":"JWT"}
    JWSBuilder jwsBuilder = session.tokenManager().jwsBuilder(algorithm, activeKey);

    // 4. 构建Payload（注入Claims）
    //    - 标准Claims: iss, sub, aud, exp, iat, nbf, auth_time
    //    - 用户Claims: preferred_username, email, email_verified
    //    - 角色Claims: realm_access.roles, resource_access.{client}.roles
    //    - Mapper Claims: 所有绑定ProtocolMapper注入的自定义Claims
    Token token = initToken(realm, user, client, userSession, tokenType);
    // ... 注入各种Claims

    // 5. JWSSigner.sign() 生成签名 -> 输出完整JWT字符串
    String jwt = jwsBuilder.jsonContent(token).sign();
    token.setEncoded(jwt);
    return token;
}
```

**1.3 签名生成：JWSBuilder.sign()**

继续追踪到`common/src/main/java/org/keycloak/jose/jws/JWSBuilder.java`，`sign()`方法使用Nimbus JOSE+JWT库完成最终的签名操作：

```java
// JWSBuilder.java - sign() 简化逻辑
public String sign() {
    JWSSigner signer = new RSASSASigner((RSAPrivateKey) privateKey);
    // 对 Base64URL(Header) + "." + Base64URL(Payload) 进行签名
    JWSObject jwsObject = new JWSObject(
        new JWSHeader(JWSAlgorithm.RS256),
        new Payload(jsonPayload)
    );
    jwsObject.sign(signer);
    return jwsObject.serialize();  // 返回 header.payload.signature
}
```

完整调用链总结：`TokenEndpoint.processGrantRequest()` → `TokenEndpoint.codeToToken()` → `TokenManager.encodeToken()` → `JWSBuilder.sign()` → `RSASSASigner.sign()`（Nimbus库）。

---

### 步骤2：分析密钥管理源码

**目标**：理解`ActiveRsaKey`的数据结构以及密钥轮换的内部机制。

打开`server-spi-private/src/main/java/org/keycloak/keys/ActiveRsaKey.java`：

```java
// ActiveRsaKey.java - RSA密钥数据结构（简化）
public class ActiveRsaKey extends RsaKeyMetadata {
    private final KeyPair keyPair;           // RSA公私钥对
    private final String kid;                // Key ID，JWKS中的唯一标识
    private final X509Certificate certificate; // X.509证书（可选，mTLS场景）
    private final KeyUse use;                // SIG（签名）或 ENC（加密）
    private final String algorithm;          // RS256/RS384/RS512/ES256等
    private final long notBefore;            // 密钥生效时间（Unix时间戳）
    private final long notAfter;             // 密钥过期时间（Unix时间戳）

    public String getKid()      { return kid; }
    public KeyPair getKeyPair() { return keyPair; }
    public PublicKey getPublicKey() { return keyPair.getPublic(); }
    public PrivateKey getPrivateKey() { return keyPair.getPrivate(); }
}
```

密钥轮换的核心实现位于`services/src/main/java/org/keycloak/services/managers/RealmManager.java`中的`rotateKey()`方法：

```java
// 密钥轮换简化逻辑
public void rotateKey(RealmModel realm, KeyUse keyUse, String algorithm) {
    // 1. 找出当前Active且即将过期的旧Key
    KeyProvider provider = session.getProvider(KeyProvider.class);
    ActiveKey oldKey = provider.getActiveKey(realm, keyUse, algorithm);

    // 2. 将旧Key状态改为Passive（保留但不再用于新Token签发）
    oldKey.setStatus(KeyStatus.PASSIVE);

    // 3. 生成新密钥对 -> 新kid -> 设置为Active
    KeyProvider newProvider = session.getProvider(KeyProvider.class);
    newProvider.generateNewKey(realm, keyUse, algorithm);

    // 4. 旧Key在 notAfter 时间到达后由定时任务物理删除
}
```

对应的Admin REST API调用为：
```bash
curl -X POST http://localhost:8080/admin/realms/demo-realm/keys/rotate \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"use":"SIG","algorithm":"RS256"}'
```

---

### 步骤3：修改TokenManager注入自定义Claims

**目标**：在Token签发时注入用户部门信息，验证Claims注入机制。

**警告**：直接修改Keycloak核心代码仅用于学习目的。生产环境应通过Protocol Mapper SPI扩展实现自定义Claims注入（参见第27章）。

在`TokenManager.java`的`encodeToken()`方法中，找到构建Access Token Payload的位置，添加自定义Claims注入逻辑：

```java
// TokenManager.java - 在encodeToken()方法中，Access Token构建之后添加

// 获取用户自定义属性
Map<String, List<String>> attrs = user.getAttributes();
String department = attrs != null && attrs.containsKey("department")
    ? attrs.get("department").get(0)
    : null;

// 注入到Access Token的Payload
if (department != null && TokenType.ACCESS.equals(tokenType)) {
    token.getOtherClaims().put("custom:department", department);
    token.getOtherClaims().put("custom:source", "keycloak-core-injection");
    token.getOtherClaims().put("custom:timestamp", System.currentTimeMillis() / 1000);
}
```

重新编译Keycloak的服务模块：

```bash
cd D:\software\workspace\keycloak
mvn clean compile -pl services -am -DskipTests
```

验证注入：在Admin Console中为用户`zhangsan`添加属性`department = "identity-platform"`，然后通过授权码流程获取Access Token。在[jwt.io](https://jwt.io)解析Token，Payload中应出现：

```json
{
  "custom:department": "identity-platform",
  "custom:source": "keycloak-core-injection",
  "custom:timestamp": 1715472000
}
```

---

### 步骤4：用外部工具验证Keycloak签发的JWT

**目标**：使用Python的PyJWT库，从JWKS端点获取公钥并验证Keycloak签发的Access Token。

```python
#!/usr/bin/env python3
# verify_keycloak_jwt.py
import jwt
import requests
import json
import time
from jwt.algorithms import RSAAlgorithm

# ===== 1. 获取Token（通过Resource Owner Password Grant） =====
token_url = "http://localhost:8080/realms/demo-realm/protocol/openid-connect/token"
payload = {
    "client_id": "oms-frontend",
    "client_secret": "your-client-secret",
    "username": "zhangsan",
    "password": "test123",
    "grant_type": "password"
}
resp = requests.post(token_url, data=payload)
tokens = resp.json()
access_token = tokens["access_token"]
refresh_token = tokens["refresh_token"]
print(f"[+] Access Token长度: {len(access_token)} 字符")

# ===== 2. 解析Token的Header，获取alg和kid =====
header = jwt.get_unverified_header(access_token)
print(f"[+] Algorithm: {header['alg']}")
print(f"[+] Key ID (kid): {header['kid']}")
print(f"[+] Type: {header.get('typ')}")

# ===== 3. 从JWKS端点获取匹配的公钥 =====
jwks_url = "http://localhost:8080/realms/demo-realm/protocol/openid-connect/certs"
jwks = requests.get(jwks_url).json()
print(f"[+] JWKS端点返回 {len(jwks['keys'])} 个公钥")

public_key = None
for key_data in jwks["keys"]:
    if key_data["kid"] == header["kid"]:
        print(f"[+] 找到匹配公钥: kid={key_data['kid']}, kty={key_data['kty']}")
        # 将JWK转换为RSA公钥对象
        public_key = RSAAlgorithm.from_jwk(json.dumps(key_data))
        break

# ===== 4. 验证签名并解码Token =====
if public_key:
    try:
        decoded = jwt.decode(
            access_token,
            public_key,
            algorithms=[header["alg"]],
            audience="oms-backend",       # Token中aud Claim必须匹配
            options={"verify_exp": True}  # 强制验证过期时间
        )
        print("\n[✅] 签名验证通过！Token内容：")
        print(json.dumps(decoded, indent=2, default=str))
    except jwt.ExpiredSignatureError:
        print("[❌] Token已过期")
    except jwt.InvalidAudienceError:
        print("[❌] Audience不匹配")
    except jwt.InvalidSignatureError:
        print("[❌] 签名验证失败——Token被篡改或密钥不匹配")
else:
    print(f"[❌] 在JWKS中未找到kid={header['kid']}的公钥，密钥可能已轮换")

# ===== 5. 验证算法混淆攻击（alg=none）的防护 =====
# 模拟攻击者修改Header中的alg为none
try:
    decoded_none = jwt.decode(
        access_token,
        key="",  # alg=none不需要密钥
        algorithms=["none"],
        options={"verify_signature": False}
    )
    print("\n[⚠️]  警告：alg=none攻击成功！JWT库允许无签名Token")
except Exception as e:
    print(f"\n[✅] alg=none攻击被阻止: {e}")
```

**预期运行结果**：

```
[+] Access Token长度: 1423 字符
[+] Algorithm: RS256
[+] Key ID (kid): 45f9e3a2-4b8c-4d1a-9f2e-7a6c3b5d8e0f
[+] Type: JWT
[+] JWKS端点返回 2 个公钥
[+] 找到匹配公钥: kid=45f9e3a2-..., kty=RSA

[✅] 签名验证通过！Token内容：
{
  "exp": 1715475600,
  "iat": 1715472000,
  "sub": "a1b2c3d4-...",
  "iss": "http://localhost:8080/realms/demo-realm",
  "aud": "oms-backend",
  "preferred_username": "zhangsan",
  "realm_access": {
    "roles": ["user", "offline_access"]
  },
  "resource_access": {
    "oms-frontend": {
      "roles": ["customer", "order-view"]
    }
  },
  "custom:department": "identity-platform",
  "custom:source": "keycloak-core-injection"
}

[✅] alg=none攻击被阻止: The specified alg value is not allowed
```

---

### 步骤5：验证Refresh Token Rotation机制

**目标**：验证Refresh Token Rotation的防重放效果。

```python
# refresh_token_rotation_test.py
import requests

token_url = "http://localhost:8080/realms/demo-realm/protocol/openid-connect/token"
client_auth = {"client_id": "oms-frontend", "client_secret": "your-client-secret"}

# 1. 首次获取Access Token + Refresh Token
resp1 = requests.post(token_url, data={
    **client_auth,
    "username": "zhangsan", "password": "test123",
    "grant_type": "password"
})
initial_refresh_token = resp1.json()["refresh_token"]
primary_access_token  = resp1.json()["access_token"]
print(f"[+] 初始Refresh Token: {initial_refresh_token[:30]}...")

# 2. 使用Refresh Token换新Token（合法使用）
resp2 = requests.post(token_url, data={
    **client_auth,
    "refresh_token": initial_refresh_token,
    "grant_type": "refresh_token"
})
assert resp2.status_code == 200, f"合法刷新失败: {resp2.text}"
new_tokens = resp2.json()
new_refresh_token = new_tokens["refresh_token"]
new_access_token  = new_tokens["access_token"]
print(f"[+] 新Refresh Token: {new_refresh_token[:30]}...")
print(f"[✅] Refresh Token合法刷新成功")

# 3. 再次使用旧的Refresh Token（重放攻击模拟）
resp3 = requests.post(token_url, data={
    **client_auth,
    "refresh_token": initial_refresh_token,  # 使用旧Token！
    "grant_type": "refresh_token"
})
print(f"\n[🔴] 重放攻击结果: HTTP {resp3.status_code}")
print(f"    Response: {resp3.text}")
if resp3.status_code in (400, 401):
    print(f"[✅] 重放攻击被成功拦截——旧Refresh Token已失效")

# 4. 验证Reuse Detection（可选——需Keycloak配置开启）
# 如果Reuse Detection开启，初次刷新后尝试再次使用旧Refresh Token
# 将导致该UserSession的所有Tokens被撤销
resp4 = requests.post(token_url, data={
    **client_auth,
    "username": "zhangsan", "password": "test123",
    "grant_type": "password"
})
print(f"\n[🔴] Reuse Detection后重新登录: HTTP {resp4.status_code}")
# 如果此前Reuse Detection触发了Session撤销，此处也需要重新登录
```

**预期运行结果**：

```
[+] 初始Refresh Token: eyJhbGciOiJSUzI1NiIsInR5...
[+] 新Refresh Token: eyJhbGciOiJSUzI1NiIsInR5...
[✅] Refresh Token合法刷新成功

[🔴] 重放攻击结果: HTTP 400
    Response: {"error":"invalid_grant","error_description":"Token is not active"}
[✅] 重放攻击被成功拦截——旧Refresh Token已失效
```

---

### 步骤6：签名算法切换实验

**目标**：将Client的签名算法从RS256切换到ES256，观察JWKS端点和Token体积的变化。

**6.1 在Admin Console中修改签名算法**

1. 登录Admin Console → **Clients** → 选择`oms-frontend`
2. **Settings**标签页 → **Fine Grain OpenID Connect Configuration** 展开
3. 找到 **Access Token Signature Algorithm**，将`RS256`改为`ES256`
4. 点击 **Save**

**6.2 观察JWKS端点变化**

切换前后各请求一次JWKS端点，对比输出：

```bash
# 切换前（RS256）
curl -s http://localhost:8080/realms/demo-realm/protocol/openid-connect/certs | jq '.keys[] | {kid, kty, alg}'
# 输出: {"kid": "45f9e3a2-...", "kty": "RSA", "alg": "RS256"}

# 切换后（ES256）
curl -s http://localhost:8080/realms/demo-realm/protocol/openid-connect/certs | jq '.keys[] | {kid, kty, alg, crv}'
# 输出: {"kid": "8b2c71f4-...", "kty": "EC", "alg": "ES256", "crv": "P-256"}
```

**6.3 验证Token体积变化**

```python
# 分别用RS256和ES256签发Token，对比体积
rs256_token = get_token_for_algorithm("RS256")
es256_token = get_token_for_algorithm("ES256")

print(f"RS256 Token长度: {len(rs256_token)} 字符")
print(f"ES256 Token长度: {len(es256_token)} 字符")
print(f"签名部分缩减:  约 {len(rs256_token) - len(es256_token)} 字符")

# 预期：RS256签名约342字符（256字节Base64），ES256签名约86字符（64字节Base64）
# Token总长度差异约256字符
```

**6.4 交叉算法验证（确保签名算法对应性）**

```python
# 用RS256的公钥去验证ES256签发的Token——预期失败
header = jwt.get_unverified_header(es256_token)
# 故意从JWKS获取RS256公钥而非ES256公钥
rsa_key = get_rs256_public_key_from_jwks()  # 获取RS256的RSA公钥

try:
    jwt.decode(es256_token, rsa_key, algorithms=["ES256"])
    print("[❌] 交叉验证意外通过——存在算法降级风险")
except jwt.InvalidSignatureError:
    print("[✅] 交叉验证正确失败——RSA密钥无法验证EC签名")
```

---

### 可能遇到的坑

| 问题 | 原因 | 解决方案 |
|------|------|---------|
| 密钥轮换后已签发Token验证失败 | JWKS端点已移除旧Key，但微服务未刷新JWKS缓存 | 微服务端JWKS缓存TTL设为5分钟，轮换后旧Key保留时间设为60分钟 |
| ES256切换后下游服务报错 | 下游JWT库不支持EC密钥格式或P-256曲线 | 确认下游`jjwt>=0.12.0`或`nimbus-jose-jwt>=9.0`；如果无法升级，回退RS256 |
| Python验证报"Algorithm not supported" | `PyJWT`版本过低或未安装`cryptography`扩展 | 安装：`pip install pyjwt[crypto]>=2.8.0` |
| 自定义Claims注入后编译报错 | `TokenType.ACCESS`在Keycloak不同版本的枚举名可能不同 | 检查`TokenType`枚举定义，可能为`TOKEN_TYPE_ACCESS`或`JWTTokenType.ACCESS` |
| Refresh Token Rotation后客户端掉线 | 客户端未正确处理Rotation返回的新Refresh Token | 客户端每次`refresh_token` grant后必须用响应中的新Refresh Token覆盖本地存储的旧Token |

---

### 测试验证清单

- [ ] TokenEndpoint.processGrantRequest() 断点命中，确认grant_type路由正确
- [ ] TokenManager.encodeToken() 断点命中，确认算法选择、密钥获取、Claims注入顺序
- [ ] JWSBuilder.sign() 方法调用成功，JWT三段式结构完整（`header.payload.signature`）
- [ ] 自定义`custom:department` Claim成功注入Access Token
- [ ] Python脚本验证签名通过（ExpiredSignatureError、InvalidSignatureError正确处理）
- [ ] alg=none攻击被拦截
- [ ] Refresh Token合法刷新成功，旧Refresh Token重放返回400
- [ ] RS256→ES256切换后JWKS端点正确返回EC公钥
- [ ] ES256 Token体积显著小于RS256 Token
- [ ] 交叉算法验证（RSA公钥验证EC签名）正确失败

---

## 4 项目总结

### 签名算法选型指南

| 算法 | 密钥类型 | 签名大小 | 签名速度 | 验证速度 | 安全性 | 推荐场景 |
|------|---------|---------|---------|---------|--------|---------|
| RS256 | RSA 2048-bit | ~256字节 | 慢 | 快 | 高（NIST推荐至2030） | 通用生产环境（默认推荐） |
| ES256 | EC P-256 | ~64字节 | 快 | 快 | 高 | 高性能场景、移动端、IoT |
| PS256 | RSA-PSS | ~256字节 | 慢 | 快 | 高（抗填充攻击优于RS256） | 高安全要求场景 |
| HS256 | 共享密钥 | ~32字节 | 极快 | 极快 | 中（信任边界大） | 内部系统间通信（非面向公众） |
| EdDSA | Ed25519 | ~64字节 | 极快 | 极快 | 高 | 未来主流（Keycloak 26+逐步支持） |

**选择建议**：RS256是最安全的默认选择——兼容性最广，所有主流JWT库和API网关（Kong、APISIX、Nginx Plus、AWS API Gateway）均原生支持。ES256适合性能敏感场景——Token体积小、签名速度快，但部分传统系统（如旧版Nginx OpenID Connect模块）不支持EC密钥。HS256仅适用于内部系统——所有验证方共享同一密钥，一旦任一验证方被攻破，攻击者即可伪造任意Token。

### 密钥管理最佳实践

- **轮换周期**：生产环境建议每30天轮换一次签名密钥；高安全场景（如金融、医疗）缩短至每周或每日
- **旧Key保留时间**：至少保留轮换后生成的旧Token剩余有效时长的1.5倍（例如AT有效期为5分钟，旧Key保留10分钟；RT有效期为30天，旧Key保留45天）
- **JWKS缓存刷新**：验证方（微服务/API网关）的JWKS缓存TTL应设为5分钟，确保密钥轮换后及时感知
- **HSM集成**：将私钥存储在HSM（Hardware Security Module）或Cloud KMS（AWS KMS、Azure Key Vault、GCP KMS）中，私钥永不离开硬件边界

### Refresh Token安全

- **Rotation防重放**：每次刷新后旧RT立即失效——攻击者和合法用户竞争使用同一个RT，"先到先得"
- **Reuse Detection检测泄露**：已用RT再次出现 → 该UserSession全部Token撤销，强制重新认证
- **RT有效期策略**：建议Access Token有效期5-15分钟，Refresh Token有效期30天（可配合Rotation降低泄露风险）
- **OAuth 2.1草案**：明确要求Refresh Token必须实施Rotation或Sender Constraint（如DPoP绑定）

### 常见Token安全漏洞

| 漏洞类型 | 攻击方法 | Keycloak防护 |
|---------|---------|-------------|
| alg=none绕过 | 修改Header `alg`为`none`，去除签名 | `TokenVerifier`强制算法白名单；编码层拒绝`none` |
| kid注入 | 注入恶意`kid`指向攻击者控制的公钥 | Keycloak从不信任客户端传入的`kid`，始终从密钥存储查找 |
| 算法降级 | 将`RS256`改为`HS256`，用公钥作为HMAC密钥 | 算法从客户端配置固定读取，不允许Token Header覆盖 |
| 弱密钥（RSA-512） | 破解512位RSA密钥后自行签发Token | Keycloak默认生成RSA-2048，不提供RSA-512选项 |
| Token重放 | 拦截Access Token后在有效期内重放 | Access Token有效期短（分钟级），配合Refresh Token Rotation降低风险窗口 |

### 思考题

1. 如果需要在下游微服务验证Token时，不直接依赖Keycloak的JWKS端点（减少外部网络调用），而是将公钥预先分发到每个微服务——这种"离线验证"模式的优缺点分别是什么？在密钥轮换时如何保证所有微服务同步更新公钥？

2. Access Token通过Base64URL编码而非加密传输——Payload中的用户属性（如手机号、邮箱）在HTTP层上是明文可读的。如果业务需要保护Token中敏感信息的机密性，除了全站HTTPS之外，JWE（JSON Web Encryption）加密Token的Payload是一个标准方案。如何在Keycloak中通过SPI扩展实现JWE加密的Access Token？加密后的Token如何被下游微服务解密和验证？

---

> **推广计划提示**：本章面向架构师和资深开发。开发团队应关注签名算法选型和密钥轮换对微服务的影响，运维团队应建立JWKS缓存刷新和密钥轮换的SOP，安全团队应定期审计Token签发日志、验证alg=none等攻击的防护有效性。
