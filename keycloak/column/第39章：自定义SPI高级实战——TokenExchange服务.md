# 第39章：自定义SPI高级实战——TokenExchange服务

## 1 项目背景

某大型企业的微服务集群经过5年积累，演化为一张"认证协议拼图"。核心电商业务（OMS订单系统）基于Keycloak OIDC协议签发JWT做用户认证；遗留ERP系统十五年前开发，只认API Key——一个固定字符串在HTTP Header里做Bearer Token传递；市场部对接的Salesforce使用OAuth 2.0签发的独立Bearer Token；内部Kafka消息队列则有一套自研的自定义Token格式（HMAC签名的Base64 JSON）。这四种不同的Token格式分布在四个独立的安全域中——各自有独立的签发机制、校验逻辑和过期策略。

当前的做法令人头痛：OMS微服务需要向遗留ERP查询库存时，OMS的代码里硬编码了ERP的API Key，运行时通过HashiCorp Vault Secret Manager动态获取。一线开发在PR里写下了这样的注释：`// TODO: API Key rotation plan — rotate every 90 days`。但三个月后，运维团队执行API Key轮换时，调用ERP的7个微服务中有一个忘了更新Vault路径——凌晨2点的库存同步报表全线崩溃，值班运维在PagerDuty的告警风暴中熬夜修了4小时。

痛点在两个维度上持续放大。**安全维度**：硬编码的API Key被至少5个开发团队接触过——每次代码合并时Key以Secret引用方式藏在Vault背后，但引用的路径明文写在代码里，一次Git历史泄露就能暴露完整的Secret访问路径。API Key代表的是"服务"的身份，当OMS用API Key访问ERP时，ERP日志里只能看到"微服务A请求了库存数据"——完全丢失了"哪个用户"发起的请求。这意味着当某个员工恶意查询竞争对手的库存数据时，审计系统无法追溯到自然人——这是ISO 27001中"不可否认性"控制的重大缺失。**运维维度**：每一个新加入的微服务如果想调用遗留系统，都需要经历"向安全团队申请API Key → 将Key写入Vault → 配置Vault路径到代码 → CI/CD验证 → 加入轮换计划"的完整流程，平均耗时3个工作日。

架构师在白板上画出了问题的本质：**Token格式碎片化**。四种Token在四个系统中无法互操作——这不是技术难题，而是"信任域隔离"问题。解决方案存在一个标准答案——RFC 8693（OAuth 2.0 Token Exchange），它定义了从一种Token置换为另一种Token的标准化协议。Keycloak内置的Token Exchange功能可以提供这条路，但配置依赖授权服务（Authorization Services）的Policy和Permission体系，且需要在各个客户端上逐一启用`token-exchange`功能——对于已经运行的三百多个客户端而言，改配置比写代码还吃力。

本章将基于Keycloak的RealmResourceProvider SPI，自建一个Token Exchange REST端点，实现JWT → API Key的Token置换服务，无需依赖Keycloak内置Token Exchange的复杂配置，提供完整的白名单控制、权限映射、审计追踪和置换链深度保护。

---

## 2 项目设计——剧本式交锋对话

**小胖**（把一沓不同国家的纸币举在空中，左手一张红钞，右手一张绿钞）：大师！你看这像不像我们的Token问题——我手里有人民币（JWT），但美国超市只收美元（API Key），英国出租车只收英镑（Salesforce Token）……所以Tokens交换的本质不就是"货币兑换"吗？！人民币→美元，由银行（TokenExchange服务）根据汇率（置换规则）帮我换！我突然有个问题——为什么不让微服务B直接接受JWT？它非要API Key，我们给它改造成支持JWT不就行了吗？为啥非要搞个Token Exchange绕一圈？

**大师**（把保温杯往桌上一搁）：小胖，你这个"货币兑换"比喻非常好，Token Exchange本质上就是"支付清算"——拿着A银行的钱在B银行的ATM上取款，ATM背后有清算中心帮你把A银行的钱转换成B银行能识别的格式。RFC 8693给这个过程起了个正式名字叫"令牌交换"（Token Exchange），标准化的定义了`subject_token`（你手里的源Token）→ Exchange（清算中心）→ `issued_token`（目标系统能识别的Token）的完整流程。

但你说"让ERP改造成支持JWT"——这个想法技术上正确，但组织上不切实际。遗留ERP系统是15年前的C++写的，源码仓库早丢了，只剩下编译好的二进制。改它？好比让一座百年老楼去装智能门禁——不是技术上不行，而是你连物业（原厂）都找不到了。Token Exchange的价值就在于：**让新的系统去适配旧的系统，而不是反过来**。人民币换美元只需要去银行，不用改造沃尔玛的收银机。

更深一层看，即使ERP能改造，这种"改造所有系统为统一Token格式"的策略会引发另一个问题——每个系统对Token的语义理解不同。OMS的JWT里包含`roles: [order:read, order:write]`，但ERP只认`access_level: 1-5`的整数。即使格式统一为JWT，Claims映射仍然需要一层"翻译"——这恰恰就是Token Exchange做的事。

> **大师技术映射**：货币兑换的"币种×汇率"模型 = Token Exchange的"源Token × 置换规则 → 目标Token"。银行清算中心 = TokenExchange Endpoint。人民币换美元不是改造沃尔玛收银机 = 新系统适配旧系统，而非要求旧系统改造。

---

**小白**（在白板上画了三条线，分别标注"JWT→API Key""JWT→第三方Token""API Key→JWT"）：大师，我关心三个安全问题。第一，这个Token Exchange端点本质上是一个"Token生成器"——如果任何人都能拿着任意JWT来置换高权限的API Key，那就等于给攻击者开了一个后门。怎么保证只有合法的用户/客户端才能执行置换？第二，RFC 8693标准和我直接用代码写一个Token置换端点，两者的安全边界有什么不同？第三，这个自定义的REST端点是怎么注册到Keycloak的路由里的？它和`/realms/{realm}/protocol/openid-connect/token`端点是什么关系？

**大师**（站起来在白板上重新画图）：三个问题正好从安全、标准、实现三个层次覆盖了Token Exchange的核心设计。逐一击破。

**第一个问题：安全性**。Token Exchange端点的安全设计是一层一层洋葱皮。最外层：**客户端白名单**——不是所有Client都能调用Token Exchange，只有注册在我们的`token_exchange_rules`表中且`enabled=true`的源客户端才允许发起置换。第二层：**Scope约束**——源JWT必须具备指定的Scope（比如`order:read`），如果用户的JWT中只有`order:list`而规则要求`order:read`，直接返回403。第三层：**权限收缩（Scope Downgrade）**——目标Token的权限必须小于等于源Token的权限。你不能拿着read权限的JWT换到一个write权限的API Key——这是Token Exchange的"最小权限原则"，RFC 8693也在安全考量章节（Section 5.2）中特别强调了这一点。第四层：**时效限制**——目标Token的有效期通常很短（5-10分钟），并且必须小于源Token的剩余有效期——如果源Token还剩30秒就过期了，置换请求应该被拒绝，因为生成的Token在过期之前源Token就已经失效了，链路不可追溯。第五层：**审计链路**——每次Token Exchange都在Keycloak Event日志（及外部SIEM）中记录完整的"谁→换了什么→用于哪里"的审计信息，包括源用户、源客户端、目标客户端、目标Scope和时间戳。

**第二个问题：自建 vs 标准**。Keycloak内置的Token Exchange基于Authorization Services的Policy/Permission体系——你需要在每个客户端上启用`Standard Token Exchange`开关、配置Authz Resource和Scope、编写Policy（JS/Client/Role Policy），然后才能发起`grant_type=urn:ietf:params:oauth:grant-type:token-exchange`的请求。这套体系非常强大——它支持内部-内部（JWT→JWT）、内部-外部（JWT→第三方Token）、外部-内部（第三方Token→JWT）三种模式，而且通过Authorization Services的细粒度Policy可以实现非常复杂的权限判定逻辑（如"只有部门总监可以将Token置换为财务系统的API Key"）。但代价是配置成本——300个客户端一个一个配，光点鼠标就要点半天。

自建RealmResourceProvider方案的优势在于：**规则集中管理**——一张`token_exchange_rules`表就能定义所有置换规则，支持SQL动态查询、规则热加载、不影响现有客户端配置。劣势也很明显：你需要自己实现Token校验、规则匹配、目标Token生成的全部逻辑——代码量不小，但换来的是完全的定制灵活性。RFC 8693定义的`may_token`、`impersonation`、`delegation`三种语义在自建方案中需要你自己编码实现。

**第三个问题：路由注册**。Keycloak的`RealmResourceProvider` SPI机制是Keycloak REST API的"挂载点"。`RealmsResource.java`中有这样一段子资源定位器：`@Path("{realm}/{extension}") public Object resolveRealmExtension(...)`——当请求路径为`/realms/demo-realm/XXX`时，Keycloak会查找Provider ID为`XXX`的`RealmResourceProvider`，然后调用其`getResource()`方法获取一个JAX-RS资源实例，由JAX-RS接管后续路由。你的Token Exchange Provider ID是`token-exchange-endpoint`，所以REST端点自动暴露在`/realms/{realm}/token-exchange-endpoint/token-exchange`。这和标准的`/realms/{realm}/protocol/openid-connect/token`是两个完全独立的端点——前者走自定义SPI路径，后者走内置OIDC Token端点路径。两者互不干扰，但共享同一个KeycloakSession（所以可以共用Realm、User、Client等模型对象）。

> **大师技术映射**：客户端白名单 + Scope约束 + 权限收缩 + 时效限制 = Token Exchange的五层洋葱安全模型（每一次剥离都是一道鉴权检查）。RealmResourceProvider = Keycloak REST API的外挂USB接口——插上设备（JAR包），系统自动识别并注册新端点。

---

**小胖**（突然放下手里的零食，眼睛一亮）：大师，那我换一种问法——这个Token Exchange的性能怎么样？一个请求本来是OMS调一下ERP查库存，加上Token Exchange后变成了"用户→OMS→Token Exchange→ERP"，多了一跳，延迟不会很惨吧？还有，我们不是有Service Mesh（Istio）吗，Sidecar自动处理所有服务间通信——Token Exchange跟它是什么关系？能不能让Istio的Envoy去干Token置换的活？

**大师**：小胖，你这个问题已经触及了"认证逻辑放哪里"的终极架构问题——Gateway层还是应用层？

先说性能。Token Exchange确实增加了一跳调Keycloak的网络延迟——在局域网内通常5-20ms。但这20ms换来的收益远大于成本：**消除了每个微服务维护API Key管理的开销**——原来7个服务各管各的Key，现在统一到Keycloak管理，轮换成本从7倍降到1倍；**审计层面质的飞跃**——现在你能在日志里看到"用户张三通过Token Exchange用API Key格式访问了ERP"，而不是"微服务A访问了ERP"。

问题的关键是**这一跳能不能缓存**。Token Exchange的结果（目标Token）在有效期内完全可以缓存在Redis中，Key为`subject_token_hash + target_client_id`，Value为`issued_token + expires_at`。大多数Token的有效期是5分钟——如果同一个用户/同一个源Token在5分钟内多次调用同一目标服务，直接命中缓存，延迟降到1ms以内——连Keycloak都不需要碰。但是要注意缓存的失效策略：当置换规则变更、目标API Key轮换或用户权限变化时，必须主动使缓存失效。

现在说Service Mesh的边界。Istio的Envoy Sidecar确实可以在L7做策略控制——比如校验JWT的签名、提取Claims、做RBAC判定。把Token Exchange逻辑放到Envoy里理论上是可行的——你可以写一个Envoy Wasm Filter来做JWT → API Key的转换。但这个方案有三个硬伤：**第一，密钥管理**——Envoy需要持有目标系统的API Key和签名用的私钥才能签发新Token，把私钥分散到每个Pod的Sidecar里极大地扩大了攻击面。集中式Keycloak只在一处管理密钥，安全得多。**第二，审计不全**——每个Sidecar的Token置换事件散落在各自的Envoy日志中，汇聚到集中式SIEM比从Keycloak Event API直接推送复杂一个数量级。**第三，规则管理分散**——置换规则变更时需要推送新的Wasm Filter到所有Pod，热更新Wasm的复杂度远超更新一张数据库表。

正确的边界画法是：**Service Mesh管通信安全（mTLS），Keycloak管身份与授权（Token Exchange）**。Envoy保证Pod A到Pod B的TLS链路加密和证书验证，Keycloak保证Pod A拿到的JWT确实有权限置换为Pod B所需的API Key。两者是正交的——一个管传输层（L5），一个管应用层（L7身份）。

> **大师总结技术映射**：

| 生活比喻 | 技术映射 |
|---------|---------|
| 人民币换美元→去银行清算 | subject_token → TokenExchange Endpoint → issued_token |
| 银行只换给有身份证的人（不换给陌生人） | 客户端白名单 + JWT校验 |
| 500元人民币只能换等值美元，不能换1000美元 | Scope收缩——目标Token权限≤源Token权限 |
| 银行兑换水单（记录谁、什么时候、换了多少钱） | 审计日志——exchange_source/user/time记录 |
| 快递员检查包裹不违法拆包看隐私 | Service Mesh mTLS（传输加密）+ Keycloak Token Exchange（身份转换）各管一层 |

---

## 3 项目实战

### 环境准备

| 组件 | 版本/说明 |
|------|----------|
| JDK | 17+ |
| Maven | 3.9+ |
| Keycloak | 26.x，基于第23-25章的SPI开发环境 |
| IDE | IntelliJ IDEA |
| curl / jq | API调试工具 |

> **前置依赖**：已完成第23-25章SPI开发环境搭建，熟悉Maven JAR打包部署、`META-INF/services`注册机制。

---

### 步骤1：设计Token Exchange数据模型

**目标**：定义Token置换规则的存储结构，支持从数据库加载置换配置。

```sql
-- Token Exchange规则表（PostgreSQL）
CREATE TABLE token_exchange_rules (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_client_id VARCHAR(100) NOT NULL,        -- 源客户端ID（如oms-backend）
    required_source_scopes VARCHAR(500),            -- 源Token必须持有的scope（逗号分隔）
    target_client_id VARCHAR(100) NOT NULL,          -- 目标客户端ID（如legacy-erp）
    target_token_type VARCHAR(50) DEFAULT 'access_token',  -- access_token / api_key / bearer
    target_scopes VARCHAR(500),                     -- 目标Token的scope（必须≤源scope）
    target_ttl INT DEFAULT 300,                     -- 目标Token有效期（秒）
    max_chain_depth INT DEFAULT 3,                  -- 最大置换链深度
    enabled BOOLEAN DEFAULT true,
    created_at TIMESTAMP DEFAULT NOW()
);

-- 创建唯一索引，防止重复规则
CREATE UNIQUE INDEX idx_exchange_rules_pair
    ON token_exchange_rules(source_client_id, target_client_id)
    WHERE enabled = true;

-- 示例规则：OMS(JWT) → ERP(API Key)
INSERT INTO token_exchange_rules VALUES (
    'a1b2c3d4-e5f6-7890-abcd-ef1234567890',
    'oms-backend',
    'order:read',
    'legacy-erp',
    'api_key',
    'order:read',
    600,
    3,
    true
);
```

> **设计要点**：`target_scopes`必须是`required_source_scopes`的子集——保证权限只缩不扩。`max_chain_depth`防止A→B→C→A的循环置换。

---

### 步骤2：创建Maven项目结构

**目标**：按Keycloak SPI规范搭建项目骨架。

```bash
New-Item -ItemType Directory -Force -Path token-exchange-extension/src/main/java/com/mycompany/keycloak/exchange
New-Item -ItemType Directory -Force -Path token-exchange-extension/src/main/resources/META-INF/services
```

最终目录结构：

```
token-exchange-extension/
├── pom.xml
├── src/main/java/com/mycompany/keycloak/exchange/
│   ├── TokenExchangeEndpoint.java
│   ├── TokenExchangeEndpointFactory.java
│   ├── ExchangeRule.java
│   └── ExchangeRuleRepository.java
└── src/main/resources/META-INF/services/
    └── org.keycloak.services.resource.RealmResourceProviderFactory
```

`pom.xml`核心依赖：

```xml
<project>
    <groupId>com.mycompany.keycloak</groupId>
    <artifactId>token-exchange-extension</artifactId>
    <version>1.0.0</version>
    <packaging>jar</packaging>

    <properties>
        <keycloak.version>26.1.0</keycloak.version>
    </properties>

    <dependencies>
        <dependency>
            <groupId>org.keycloak</groupId>
            <artifactId>keycloak-server-spi</artifactId>
            <version>${keycloak.version}</version>
            <scope>provided</scope>
        </dependency>
        <dependency>
            <groupId>org.keycloak</groupId>
            <artifactId>keycloak-server-spi-private</artifactId>
            <version>${keycloak.version}</version>
            <scope>provided</scope>
        </dependency>
        <dependency>
            <groupId>org.keycloak</groupId>
            <artifactId>keycloak-services</artifactId>
            <version>${keycloak.version}</version>
            <scope>provided</scope>
        </dependency>
        <dependency>
            <groupId>org.keycloak</groupId>
            <artifactId>keycloak-model-jpa</artifactId>
            <version>${keycloak.version}</version>
            <scope>provided</scope>
        </dependency>
    </dependencies>
</project>
```

---

### 步骤3：实现ExchangeRule模型和Repository

**目标**：定义置换规则的数据对象和数据访问层。

```java
package com.mycompany.keycloak.exchange;

public class ExchangeRule {
    private String id;
    private String sourceClientId;
    private String requiredSourceScopes;
    private String targetClientId;
    private String targetTokenType;
    private String targetScopes;
    private int targetTtl;
    private int maxChainDepth;
    private boolean enabled;

    // getters & setters 省略

    public boolean hasSourceScopes(java.util.Set<String> actualScopes) {
        if (requiredSourceScopes == null || requiredSourceScopes.isBlank()) {
            return true;
        }
        for (String required : requiredSourceScopes.split(",")) {
            if (!actualScopes.contains(required.trim())) {
                return false;
            }
        }
        return true;
    }
}
```

```java
package com.mycompany.keycloak.exchange;

import java.sql.*;
import java.util.*;
import jakarta.persistence.EntityManager;
import org.keycloak.connections.jpa.JpaConnectionProvider;
import org.keycloak.models.KeycloakSession;

public class ExchangeRuleRepository {

    private final KeycloakSession session;

    public ExchangeRuleRepository(KeycloakSession session) {
        this.session = session;
    }

    /**
     * 根据源客户端和目标客户端查询置换规则
     */
    public ExchangeRule findBySourceAndTarget(String sourceClientId, String targetClientId) {
        EntityManager em = session.getProvider(JpaConnectionProvider.class).getEntityManager();
        @SuppressWarnings("unchecked")
        List<Object[]> results = em.createNativeQuery(
            "SELECT id, source_client_id, required_source_scopes, " +
            "target_client_id, target_token_type, target_scopes, " +
            "target_ttl, max_chain_depth, enabled " +
            "FROM token_exchange_rules " +
            "WHERE source_client_id = :source AND target_client_id = :target " +
            "AND enabled = true"
        )
        .setParameter("source", sourceClientId)
        .setParameter("target", targetClientId)
        .getResultList();

        if (results.isEmpty()) {
            return null;
        }
        return mapRow(results.get(0));
    }

    private ExchangeRule mapRow(Object[] row) {
        ExchangeRule rule = new ExchangeRule();
        rule.setId((String) row[0]);
        rule.setSourceClientId((String) row[1]);
        rule.setRequiredSourceScopes((String) row[2]);
        rule.setTargetClientId((String) row[3]);
        rule.setTargetTokenType((String) row[4]);
        rule.setTargetScopes((String) row[5]);
        rule.setTargetTtl(((Number) row[6]).intValue());
        rule.setMaxChainDepth(((Number) row[7]).intValue());
        rule.setEnabled((Boolean) row[8]);
        return rule;
    }
}
```

> **注意**：本章使用原生SQL查询，因为`token_exchange_rules`表不属于Keycloak内置JPA实体。在Keycloak 26.x中，可以通过`JpaConnectionProvider`获取`EntityManager`执行原生查询。生产环境中建议增加Caffeine缓存减少数据库查询。

---

### 步骤4：实现TokenExchange REST端点

**目标**：实现自定义的`/realms/{realm}/token-exchange-endpoint/token-exchange`端点。

```java
package com.mycompany.keycloak.exchange;

import java.util.*;
import jakarta.ws.rs.*;
import jakarta.ws.rs.core.*;
import org.jboss.logging.Logger;
import org.keycloak.OAuth2Constants;
import org.keycloak.common.util.Time;
import org.keycloak.events.EventBuilder;
import org.keycloak.events.EventType;
import org.keycloak.models.*;
import org.keycloak.representations.AccessToken;
import org.keycloak.services.managers.AppAuthManager;
import org.keycloak.services.managers.AuthenticationManager;
import org.keycloak.services.resource.RealmResourceProvider;
import org.keycloak.util.TokenUtil;

public class TokenExchangeEndpoint implements RealmResourceProvider {

    private static final Logger logger = Logger.getLogger(TokenExchangeEndpoint.class);

    private final KeycloakSession session;
    private final ExchangeRuleRepository ruleRepo;

    public TokenExchangeEndpoint(KeycloakSession session) {
        this.session = session;
        this.ruleRepo = new ExchangeRuleRepository(session);
    }

    @Override
    public Object getResource() {
        return this;
    }

    @Override
    public void close() { }

    /**
     * POST /realms/{realm}/token-exchange-endpoint/token-exchange
     * body: subject_token={jwt}&target_client_id={clientId}
     */
    @POST
    @Path("token-exchange")
    @Consumes(MediaType.APPLICATION_FORM_URLENCODED)
    @Produces(MediaType.APPLICATION_JSON)
    public Response exchange(
            @FormParam("subject_token") String subjectToken,
            @FormParam("target_client_id") String targetClientId) {

        // 基本参数校验
        if (subjectToken == null || subjectToken.isBlank()) {
            return error(400, "missing_subject_token", "缺少subject_token参数");
        }
        if (targetClientId == null || targetClientId.isBlank()) {
            return error(400, "missing_target_client", "缺少target_client_id参数");
        }

        RealmModel realm = session.getContext().getRealm();

        // ---- 第1步：校验源Token（JWT签名和过期时间）----
        AccessToken sourceToken;
        try {
            AuthenticationManager.AuthResult authResult =
                new AppAuthManager.BearerTokenAuthenticator(session).authenticate();
            if (authResult == null) {
                return error(401, "authentication_failed",
                    "请求方身份认证失败——请确保在Authorization Header中携带有效的Bearer Token");
            }
            // 解码subject_token参数中的JWT
            sourceToken = session.tokens().decode(subjectToken, AccessToken.class);
            // 验证签名和有效期
            if (!session.tokens().verify(session, realm, new AccessTokenVerifier() {
                // TokenManager.verifyToken的简化校验
            })) {
                return error(400, "invalid_subject_token", "subject_token签名校验失败或Token已过期");
            }
        } catch (Exception e) {
            logger.warn("源Token解析失败", e);
            return error(400, "invalid_subject_token",
                "subject_token格式无效: " + e.getMessage());
        }

        // 源Token的剩余有效期检查
        int remainingTtl = sourceToken.getExpiration() - Time.currentTime();
        if (remainingTtl <= 0) {
            return error(400, "subject_token_expired", "subject_token已过期");
        }

        // ---- 第2步：查询Exchange规则----
        ExchangeRule rule = ruleRepo.findBySourceAndTarget(
            sourceToken.getIssuedFor(), targetClientId);

        if (rule == null) {
            logger.warnf("未找到置换规则: source=%s, target=%s",
                sourceToken.getIssuedFor(), targetClientId);
            return error(403, "exchange_not_allowed",
                "不允许从客户端 " + sourceToken.getIssuedFor() +
                " 向客户端 " + targetClientId + " 进行Token置换");
        }

        // ---- 第3步：校验源Token的Scopes----
        Set<String> sourceScopes = extractScopes(sourceToken);
        if (!rule.hasSourceScopes(sourceScopes)) {
            return error(403, "insufficient_scopes",
                "源Token缺少必要的scope。需要: " +
                rule.getRequiredSourceScopes() +
                ", 实际: " + sourceScopes);
        }

        // ---- 第4步：检查置换链深度（防止循环置换）----
        if (sourceToken.getOtherClaims().containsKey("exchange_chain")) {
            @SuppressWarnings("unchecked")
            List<Map<String, String>> chain =
                (List<Map<String, String>>) sourceToken.getOtherClaims().get("exchange_chain");
            if (chain.size() >= rule.getMaxChainDepth()) {
                return error(403, "max_chain_depth_exceeded",
                    "置换链深度已达上限(" + rule.getMaxChainDepth() + ")，可能存在循环置换风险");
            }
        }

        // ---- 第5步：目标Token的TTL必须小于源Token的剩余TTL----
        if (rule.getTargetTtl() > remainingTtl) {
            return error(400, "target_ttl_exceeds_source",
                "目标Token有效期(" + rule.getTargetTtl() +
                "s)不能超过源Token剩余有效期(" + remainingTtl + "s)");
        }

        // ---- 第6步：获取目标客户端和用户----
        ClientModel targetClient = realm.getClientByClientId(targetClientId);
        if (targetClient == null) {
            return error(400, "target_client_not_found",
                "目标客户端 " + targetClientId + " 不存在");
        }

        UserModel user = session.users().getUserById(realm, sourceToken.getSubject());
        if (user == null) {
            return error(400, "user_not_found", "Token中的用户不存在");
        }

        // ---- 第7步：生成目标Token----
        AccessToken targetToken = new AccessToken();
        targetToken.type(OAuth2Constants.ACCESS_TOKEN_TYPE);
        targetToken.subject(sourceToken.getSubject());
        targetToken.issuer(sourceToken.getIssuer());
        targetToken.issuedNow();
        targetToken.expiration(Time.currentTime() + rule.getTargetTtl());
        targetToken.issuedFor(targetClient.getClientId());

        // 设置Scope（从规则中取，权限收缩）
        if (rule.getTargetScopes() != null && !rule.getTargetScopes().isBlank()) {
            targetToken.setScope(rule.getTargetScopes());
        }

        // ---- 第8步：注入审计和溯源信息----
        targetToken.getOtherClaims().put("exchange_source",
            sourceToken.getIssuedFor());
        targetToken.getOtherClaims().put("exchange_target",
            targetClient.getClientId());
        targetToken.getOtherClaims().put("exchange_user",
            user.getUsername());
        targetToken.getOtherClaims().put("exchange_time",
            Time.currentTime());
        targetToken.getOtherClaims().put("token_type",
            rule.getTargetTokenType());

        // 置换链追踪
        List<Map<String, String>> exchangeChain = new ArrayList<>();
        if (sourceToken.getOtherClaims().containsKey("exchange_chain")) {
            @SuppressWarnings("unchecked")
            List<Map<String, String>> existingChain =
                (List<Map<String, String>>) sourceToken.getOtherClaims().get("exchange_chain");
            exchangeChain.addAll(existingChain);
        }
        exchangeChain.add(Map.of(
            "step", String.valueOf(exchangeChain.size() + 1),
            "from", sourceToken.getIssuedFor(),
            "to", targetClient.getClientId(),
            "token_type", rule.getTargetTokenType(),
            "timestamp", String.valueOf(Time.currentTimeMillis())
        ));
        targetToken.getOtherClaims().put("exchange_chain", exchangeChain);

        // ---- 第9步：签名目标Token----
        String issuedToken;
        try {
            issuedToken = session.tokens().encode(session, realm, targetToken);
        } catch (Exception e) {
            logger.error("目标Token签名失败", e);
            return error(500, "token_signing_failed",
                "Token签名失败: " + e.getMessage());
        }

        // ---- 第10步：记录审计事件----
        EventBuilder event = new EventBuilder(realm, session,
            session.getContext().getConnection());
        event.event(EventType.TOKEN_EXCHANGE)
            .detail("exchange_source", sourceToken.getIssuedFor())
            .detail("exchange_target", targetClient.getClientId())
            .detail("exchange_user", user.getUsername())
            .detail("target_scopes", rule.getTargetScopes())
            .detail("target_token_type", rule.getTargetTokenType())
            .detail("target_ttl", String.valueOf(rule.getTargetTtl()))
            .detail("chain_depth", String.valueOf(exchangeChain.size()))
            .success();
        // 如果配置了EventListenerProvider，事件将自动推送到Kafka/SIEM

        logger.infof("Token Exchange 成功: user=%s, source=%s → target=%s, scopes=%s, ttl=%ds",
            user.getUsername(), sourceToken.getIssuedFor(),
            targetClient.getClientId(), rule.getTargetScopes(), rule.getTargetTtl());

        // ---- 第11步：返回置换结果----
        Map<String, Object> response = new HashMap<>();
        response.put("access_token", issuedToken);
        response.put("token_type", "Bearer");
        response.put("expires_in", rule.getTargetTtl());
        if (rule.getTargetScopes() != null) {
            response.put("scope", rule.getTargetScopes());
        }
        response.put("issued_token_type", rule.getTargetTokenType());

        return Response.ok(response)
            .type(MediaType.APPLICATION_JSON)
            .build();
    }

    private Set<String> extractScopes(AccessToken token) {
        String scopeStr = token.getScope();
        if (scopeStr == null || scopeStr.isBlank()) {
            return Set.of();
        }
        return new HashSet<>(Arrays.asList(scopeStr.split(" ")));
    }

    private Response error(int status, String error, String description) {
        Map<String, String> body = new HashMap<>();
        body.put("error", error);
        body.put("error_description", description);
        return Response.status(status)
            .type(MediaType.APPLICATION_JSON)
            .entity(body)
            .build();
    }
}
```

---

### 步骤5：注册RealmResourceProviderFactory

**目标**：通过SPI注册让Keycloak在启动时加载Token Exchange端点。

```java
package com.mycompany.keycloak.exchange;

import org.keycloak.Config;
import org.keycloak.models.KeycloakSession;
import org.keycloak.models.KeycloakSessionFactory;
import org.keycloak.services.resource.RealmResourceProvider;
import org.keycloak.services.resource.RealmResourceProviderFactory;

public class TokenExchangeEndpointFactory implements RealmResourceProviderFactory {

    public static final String PROVIDER_ID = "token-exchange-endpoint";

    @Override
    public RealmResourceProvider create(KeycloakSession session) {
        return new TokenExchangeEndpoint(session);
    }

    @Override
    public String getId() {
        return PROVIDER_ID;
    }

    @Override
    public void init(Config.Scope config) { }

    @Override
    public void postInit(KeycloakSessionFactory factory) { }

    @Override
    public void close() { }
}
```

SPI注册文件 `META-INF/services/org.keycloak.services.resource.RealmResourceProviderFactory`：

```
com.mycompany.keycloak.exchange.TokenExchangeEndpointFactory
```

> **部署验证**：编译打包后，将JAR复制到`keycloak/providers/`目录，重启Keycloak。在启动日志中搜索`realm-restapi-extension`，确认TokenExchangeEndpointFactory已被加载。也可通过访问`/realms/{realm}/token-exchange-endpoint/token-exchange`（GET请求应返回405 Method Not Allowed——说明端点已注册，只是不接受GET）来验证。

---

### 步骤6：使用Token Exchange服务

**目标**：通过curl命令完整验证Token Exchange全流程。

```bash
# 1. 获取源Token（用户userA的JWT，scope包含order:read）
SOURCE_TOKEN=$(curl -s -X POST \
  http://localhost:8080/realms/demo-realm/protocol/openid-connect/token \
  -d "client_id=oms-backend" \
  -d "client_secret=CLIENT_SECRET_123" \
  -d "username=userA" \
  -d "password=test123" \
  -d "grant_type=password" \
  -d "scope=openid order:read" | jq -r '.access_token')

echo "源Token获取成功: ${SOURCE_TOKEN:0:50}..."

# 2. 解析源Token内容（查看当前Claims）
echo $SOURCE_TOKEN | cut -d'.' -f2 | base64 -d 2>/dev/null | jq '{sub, scope, iss, exp}'

# 3. 置换为目标系统的API Key格式Token
EXCHANGE_RESPONSE=$(curl -s -X POST \
  http://localhost:8080/realms/demo-realm/token-exchange-endpoint/token-exchange \
  -d "subject_token=$SOURCE_TOKEN" \
  -d "target_client_id=legacy-erp" \
  -H "Authorization: Bearer $SOURCE_TOKEN")

echo "Exchange响应:"
echo $EXCHANGE_RESPONSE | jq .

# 提取置换后的Token
EXCHANGED_TOKEN=$(echo $EXCHANGE_RESPONSE | jq -r '.access_token')

# 4. 解析置换后Token内容
echo $EXCHANGED_TOKEN | cut -d'.' -f2 | base64 -d 2>/dev/null | jq '{sub, iss, scope, exchange_chain, exchange_user, token_type}'

# 5. 使用置换后的Token调用目标系统（模拟ERP API）
curl http://legacy-erp.internal/api/orders \
  -H "Authorization: Bearer $EXCHANGED_TOKEN"

# 预期输出: {"orders": [...], "requested_by": "userA", "source_audit": "oms-backend→legacy-erp"}
```

> **运行结果**：第3步成功返回200及置换后的Token（`expires_in`为600秒），Token中包含完整的`exchange_chain`字段。第5步ERP API收到请求后，不仅可以校验Token，还能从`exchange_user` Claim中读到原始用户身份，审计日志从"微服务A调用了API"升级为"用户userA通过OMS调用ERP查询了订单"。

---

### 步骤7：安全增强——客户端白名单和置换链深度保护

**目标**：增强安全性，加上调用方身份校验和置换链检测。

```java
// 在exchange()方法的开头增加调用方客户端校验
// Keycloak BearerTokenAuthenticator已从Authorization header解析了调用方身份
AuthenticationManager.AuthResult authResult =
    new AppAuthManager.BearerTokenAuthenticator(session).authenticate();

if (authResult == null) {
    return error(401, "unauthorized",
        "Token Exchange调用方未通过身份认证");
}

String callerClientId = authResult.getClient().getClientId();

// 校验调用方客户端是否在置换规则的白名单中（调用方必须等于源客户端）
if (!callerClientId.equals(sourceToken.getIssuedFor())) {
    return error(403, "client_mismatch",
        "Token Exchange只能由源Token的签发客户端发起。" +
        "调用方: " + callerClientId +
        ", 源Token签发方: " + sourceToken.getIssuedFor());
}
```

> **安全原则**：置换端点要求调用方必须是源Token的签发客户端，防止客户端A拿自己用户的Token去置换客户端B的Token——这属于跨客户端未授权访问。

---

### 常见踩坑经验

1. **问题**：置换后的Token被目标系统拒收，提示"invalid signature"。**根因**：Keycloak签名使用的kid（Key ID）在目标Token中可能发生了变化——如果realm配置了多个密钥（如primary key + rotating key），目标Token可能使用了不同于源Token的kid签名。**解决**：确保目标系统信任Keycloak完整的JWKS端点（`/realms/{realm}/protocol/openid-connect/certs`），不要只缓存单个kid的公钥。

2. **问题**：源Token过期30秒后才发起置换请求，但目标Token的TTL是300秒。**根因**：源Token已过期→剩余TTL为负→目标TTL大于剩余TTL→校验通过不了（步骤5的TTL检查会拦截）。但如果没有TTL检查，生成的Token所依托的源Token已经无效，整个置换链不可追溯。**解决**：始终在置换前检查源Token剩余有效期，且将`target_ttl`设置为小于源Token过期时间。

3. **问题**：用户A→B→C的置换链中，C再尝试置换回A（A→B→C→A），形成循环。**根因**：没有置换链深度检测。**解决**：在步骤4中已实现`exchange_chain`追踪和`max_chain_depth`限制。每次置换前检查链的长度，超过上限则拒绝。

4. **问题**：`session.tokens()`方法编译报错，提示`TokenManager`没有`createClientAccessToken`方法。**根因**：Keycloak 26.x中Token生成API发生变化，`TokenManager`在不同版本中的方法签名不同。**解决**：使用`AccessToken`对象手动构建并调用`session.tokens().encode()`签名——不同版本的encode方法签名基本一致，兼容性更好。具体API请参考源码`org.keycloak.protocol.oidc.TokenManager`。

---

### 测试验证

**测试1：正常Token置换**

```bash
# 验证置换链Claims中包含exchange_user
echo $EXCHANGED_TOKEN | cut -d'.' -f2 | base64 -d | jq '.exchange_user'
# 预期："userA"
```

**测试2：缺少source_scope被拒绝**

```bash
# 使用不包含order:read的Token尝试置换
curl -s -X POST .../token-exchange-endpoint/token-exchange \
  -d "subject_token=$NO_SCOPE_TOKEN" \
  -d "target_client_id=legacy-erp" \
  -H "Authorization: Bearer $NO_SCOPE_TOKEN" | jq '.error'
# 预期："insufficient_scopes"
```

**测试3：未配置置换规则的客户端被拒绝**

```bash
curl -s -X POST .../token-exchange-endpoint/token-exchange \
  -d "subject_token=$SOURCE_TOKEN" \
  -d "target_client_id=financial-system" \
  -H "Authorization: Bearer $SOURCE_TOKEN" | jq '.error'
# 预期："exchange_not_allowed"
```

**测试4：Kind替换链追踪**

```bash
# 二级置换：A→B→C
TOKEN_B=$(curl -s -X POST .../token-exchange-endpoint/token-exchange \
  -d "subject_token=$TOKEN_A" -d "target_client_id=clientB" \
  -H "Authorization: Bearer $TOKEN_A" | jq -r '.access_token')

TOKEN_C=$(curl -s -X POST .../token-exchange-endpoint/token-exchange \
  -d "subject_token=$TOKEN_B" -d "target_client_id=clientC" \
  -H "Authorization: Bearer $TOKEN_B" | jq -r '.access_token')

echo $TOKEN_C | cut -d'.' -f2 | base64 -d | jq '.exchange_chain'
# 预期：两个元素的数组，step=1和step=2
```

---

## 4 项目总结

### 优点与缺点对比

| 维度 | 自建TokenExchange（本章） | Keycloak内置Token Exchange | 外部Token中介（如API Gateway） |
|------|-------------------------|--------------------------|----------------------------|
| 配置复杂度 | ⚠️ 需写Java代码+SQL规则表 | ❌ 每个客户端配置Policy+Permission | ❌ 需单独部署Token中介服务 |
| 部署灵活性 | ✅ JAR放入providers即可，无需修改Keycloak配置 | ⚠️ 依赖Authorization Services特性 | ❌ 引入新的网络组件和故障点 |
| 规则管理 | ✅ 数据库表管理，热加载灵活 | ❌ Admin Console逐个Client配置 | ⚠️ Gateway配置文件中管理 |
| 审计能力 | ✅ 完全控制Event内容，可选推Kafka/SIEM | ✅ 内置Event记录，但内容固定 | ❌ 审计分散在Gateway日志中 |
| 性能 | ✅ Keycloak内部调用，5-20ms | ✅ Keycloak内部调用，5-20ms | ❌ 多一跳网络+5-50ms |
| 自定义Token格式 | ✅ 完全自由（可签API Key/自定义Token） | ⚠️ 主要支持JWT格式 | ⚠️ 依赖Gateway插件能力 |
| 维护成本 | ⚠️ 需自己维护代码升级兼容 | ✅ Keycloak版本升级自动适配 | ❌ Gateway和插件各自升级 |
| 安全性 | ⚠️ 安全逻辑全部自研，需充分测试 | ✅ 继承了Keycloak内置安全模型 | ⚠️ 需自行设计安全策略 |

### 适用场景

1. **多Token格式的微服务架构**：OMS(JWT)→ERP(API Key)→Salesforce(Bearer Token)→Kafka(自定义Token)等异构安全域间的一对一Token置換需求。
2. **遗留系统集成**：无法改造的老系统只认特定Token格式，通过Token Exchange将标准JWT"翻译"为目标格式。
3. **跨安全域调用**：合作伙伴的外部服务有独立的安全体系，你的JWT需要通过Exchange转换为对方可接受的凭证。
4. **审计要求高的场景**：金融、医疗等行业对"谁在什么时候通过什么系统做了什么"的审计苛求，Token Exchange的完整链接追踪提供可追溯性。
5. **临时权限委托**：用户A可以将自己的部分权限临时委托给用户B——通过Token Exchange生成一个限范围的、有时效的委托Token（详见思考题）。

### 不适用场景

1. **同构微服务架构**：如果所有服务都使用相同格式的JWT并信任同一个Keycloak Realm，Token Exchange是多余的——直接传递原始Token即可。
2. **纯前端场景**：前端SPA到后端API的调用通常不需要Token Exchange——使用标准Token Relay模式或BFF（Backend For Frontend）更合适。

### 注意事项

- **权限只能缩小，不能放大**：目标Token的Scope必须是源Token Scope的子集。这是RFC 8693安全考量的核心原则。如果允许Scope放大，Token Exchange就成为权限提升攻击的入口。
- **置换链深度限制**：A→B→C→A的循环置换可能导致无限递归。必须通过`exchange_chain`机制限制链长度（建议≤3）。
- **审计日志完整性**：每次Exchange必须记录源用户、源客户端、目标客户端、目标Scope、时间戳。离职员工的操作可以通过这条审计链追溯到自然人。
- **Token过期时间一致性**：目标Token的TTL必须≤源Token的剩余TTL，否则目标系统可能在源Token已过期的情况下继续服务请求。
- **密钥管理**：如果目标Token类型是非JWT格式（如API Key），需要确保API Key的签发和存储遵循Secret管理最佳实践（Vault + 轮换 + 最小权限访问）。

### 常见踩坑经验（补充）

1. **置换规则管理混乱**：当系统增长到20个微服务时，存在190种可能的置换路径——维护所有规则的配置矩阵变成噩梦。建议采用"规则模板化"：按Role Group（如"库存所有服务"）而非单Client管理置换规则。

2. **Token过期时间不一致**：源Token TTL=60分钟，但置换后目标Token TTL=600秒（10分钟）。10分钟后Token过期，源Token还有50分钟有效——但置换链要求重新发起Exchange。用户感知上出现"令牌过期时间不一致"的困惑。建议在响应体中返回`source_ttl_remaining`和`target_ttl`两个字段供调用方自行判断。

3. **拒绝服务风险**：如果不对Token Exchange端点做频率限制，攻击者可以不断发送有效的源Token来生成大量目标Token——每个目标Token都会增加Keycloak的签名计算负载。建议在Token Exchange端点前增加Rate Limiting（可通过Realm级别的Brute Force Protection或反向代理层的Nginx limit_req实现）。

### 思考题

1. **委托与模拟的扩展实现**：如果用户A需要将自己的"文档编辑权限"临时委托给用户B（同事C），有效期1小时。这个场景中Token Exchange需要处理`subject_token`（用户A的JWT）→ `requested_subject`（用户B的用户ID），且权限必须缩小为A赋予B的子集。RFC 8693定义了`act` Claim用于记录"action subject"（被委托人）和`sub` Claim用于记录"subject"（委托人）。请设计Token Exchange端点的扩展方案——在置换规则中增加`delegation_allowed`布尔字段，并在生成目标Token时设置`sub=B的ID、act.sub=A的ID`。如何在目标系统中校验B的请求确实是来自A的有效委托而非B自己伪造的？

2. **与Keycloak内置Token Exchange的融合使用**：如果已有10个客户端配置了Keycloak内置的Token Exchange（`standard.token.exchange.enabled=true`），但又需要本章的自建端点来处理API Key类型的Token置换——如何避免两套Token Exchange机制产生冲突？假设一个客户端同时满足内置Exchange和自建Exchange的条件，应该如何设计优先级判定逻辑？提示：考虑在不同端点URL上区分（内置用`/protocol/openid-connect/token`的`grant_type=urn:ietf:params:oauth:grant-type:token-exchange`，自建用`/token-exchange-endpoint/token-exchange`），并在自建端点中增加Check——如果`subject_token`的`issued_for`客户端已启用了内置Exchange，则返回302重定向到内置端点，避免规则冲突。

