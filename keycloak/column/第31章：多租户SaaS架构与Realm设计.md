# 第31章：多租户SaaS架构与Realm设计

## 1 项目背景

某SaaS CRM公司服务500+企业客户，每个客户拥有独立的用户群（平均1000用户/客户）、独立的权限体系（销售主管/客户经理/客服专员三级角色）、独立的品牌化登录页（企业Logo+专属配色+自定义域名）。初期为了"快速交付"，技术团队采用了最粗暴的多租户方案——"一个客户一个Keycloak实例"。

运维团队为此管理着整整500个Docker容器。算一笔粗账：每个Keycloak容器即使空载也至少占用512MB堆内存（JVM基线），500个容器吃掉250GB总内存；每个容器独立维护数据库连接池（至少10个连接），500×10=5000个数据库连接；版本升级时，运维人员需要逐一操作500个实例——按照每实例5分钟计算，一轮升级需要42小时不间断工作。更要命的是，Keycloak每季度发布一次安全补丁，这意味着运维团队每个季度都要经历一轮煎熬。

CTO在季度复盘会上拍板："这套架构已经不可持续。明年客户翻倍到1000家时，我们就要管理1000个实例——不是技术问题，是管理灾难。重构为多租户架构，在一个Keycloak集群中用Realm做租户隔离。三个目标：降低资源消耗（内存从250GB压缩到10GB以内）、统一版本管理（一次升级覆盖所有租户）、自动化租户生命周期（入驻→运营→停用→归档全流程无人值守）。"

痛点放大：500个Keycloak实例的运维地狱。共享Realm和独立Realm的策略选择困境——共享Realm用`tenant_id`属性区分客户，资源最省但隔离性弱，一旦某客户要求定制密码策略或对接LDAP，就会与全局配置冲突；独立Realm能做到配置完全隔离，但1000个Realm是否会压垮Keycloak？此外，租户自助入驻的自动化链路如何实现——客户在线注册后自动创建Realm、初始化客户端和角色、生成管理员密码并发送邮件；不同租户对认证方式的需求大相径庭——大型客户需要对接企业内部AD/LDAP，互联网型客户需要微信/Google社交登录，政府客户需要国密算法和UKey硬件认证。这些都是多租户架构设计中不可回避的核心命题。

---

## 2 项目设计——剧本式交锋对话

**场景**：周一晨会结束，小胖、小白和大师三人坐在会议室讨论新架构方案。白板上画满了各种框图。

---

**小胖**（拿着白板笔）：大师，我先抛一个想法——你看商场里的各个店铺，共用一个商场大门，但每家店有自己的收银系统和会员管理体系。这不就是咱们要的多租户吗？但是话说回来，一个Realm不就够了吗？把所有客户的数据用`tenant_id`属性区分就行，省时省力，500个客户共用一套配置，管理起来多方便？

**大师**（放下咖啡杯）：比喻很形象，但你只看到了"大门"的层面。你说共享一个Realm、用`tenant_id`区分，我问你四个问题：第一，客户A要求密码长度12位、必须包含大小写和特殊字符，客户B说密码6位就行、他们员工年纪大记不住——你怎么在一个Realm里同时满足这两种密码策略？第二，客户A需要对接企业AD，所有用户走LDAP认证，客户B只需要用户名密码——一个Realm怎么同时配置两种User Federation？第三，如果客户A的管理员登录后看到了客户B的用户列表——哪怕只是一个API响应的JSON字段泄露——这就是法律层面的数据安全问题。第四，客户A的品牌登录页是深蓝色科技风，客户B是红色中国风——一个Realm只能配一套登录主题。

**小胖**：（抓抓头）好吧，一个确实不够。那独立Realm总行了吧——500个客户500个Realm，每个完全隔离，完美！

> **大师技术映射**：共享Realm+属性隔离 = 大杂院（共享大门和水电，各家只用布帘隔开）。独立Realm = 独立公寓（每家独立门锁、独立水电表、独立装修）。"一个Realm只配一套主题"意味着认证流程（密码策略、OTP、验证码等）和UI渲染都是Realm级别的配置，不存在"同一个Realm里按tenant_id切换主题"的内置机制。

---

**小白**（翻开笔记本，上面列满了问题）：大师，我想追问三个要点。第一，共享Realm和独立Realm的本质差异到底在哪？仅仅是"隔离程度不同"太笼统了——从数据、配置、认证策略三个维度分别看，哪些是能共享的，哪些是必须隔离的？第二，1000个Realm对Keycloak的性能到底有什么影响——启动时间、内存占用、数据库查询延迟？第三，Realm之间的认证策略可以做到完全独立吗——比如租户A走AD认证，租户B走社交登录（微信+Google+GitHub三合一），同时在线，互不干扰？

**大师**（站到白板前，画出三层隔离模型）：

**数据层隔离**：每个Realm在数据库中有独立的记录集。UserEntity、RoleEntity、ClientEntity等核心实体的数据库表中都有一个`REALM_ID`字段，所有查询都带`WHERE REALM_ID = ?`过滤。这意味着即使用户名相同（比如两个租户各有一个`admin`账号），它们在数据库中是两行不同记录，绝不可能互相看到。这一层隔离没有任何妥协余地。

**配置层隔离**：Realm级别的配置（密码策略、Token生命周期、SMTP服务器、Brute Force检测参数、Login页面主题）是100%独立的。租户A可以把Access Token有效期设为5分钟（高安全要求），租户B设为30天（便利性优先），不会互相影响。

**认证策略隔离**：这是独立Realm的最大优势。租户A在`ldap-tenant` Realm中配置User Federation对接Microsoft AD，租户B在`social-tenant` Realm中配置三个Identity Provider（微信/Google/GitHub）。Keycloak内部的认证SPI（Authentication SPI）在执行认证流程时，始终绑定在当前Realm上下文——它只会查找当前Realm下配置的Federation Provider和IdP列表，跨Realm的Provider对当前认证流不可见。

关于性能影响——我做了一组测试数据，可以直观理解：

| Realm数量 | DB大小 | 启动时间 | 堆内存占用 | 首次认证延迟 |
|-----------|--------|---------|-----------|-------------|
| 10 | ~50MB | 8s | 500MB | 300ms |
| 100 | ~500MB | 30s | 1.2GB | 500ms |
| 500 | ~2.5GB | 2min | 3GB | 800ms |
| 1000 | ~5GB | 5min | 5GB | 1.2s |

启动时间线性增长的根本原因是JPA存储层的Realm懒加载机制——Keycloak启动时不会立即加载所有Realm，只有在某个Realm被首次访问时才触发数据库加载。但在集群环境下，缓存预热的遍历操作会触发全量加载。内存占用主要来自Infinispan缓存——每个Realm维护独立的缓存域（用户缓存、角色缓存、客户端缓存、Session缓存），Realm越多，缓存条目越多。

> **大师技术映射**：数据隔离 = 数据库`REALM_ID`垂直分片；配置隔离 = 每个Realm维护独立的安全策略对象图；认证隔离 = Authentication SPI的Provider链表按Realm上下文加载。性能瓶颈核心 = Infinispan缓存域乘以Realm数量的线性膨胀。

---

**小胖**（举手打断）：大师大师，你刚说了两种极端——全共享和全独立，那我能不能来个混合的？大客户给独立Realm，小客户共享一个Realm，这样既省资源又满足隔离需求？

**大师**：（赞赏地点头）你抓到核心了。这恰恰是生产环境中最佳实践——**混合模式/分层策略**。具体来说：

第一层——**Enterprise大型客户**（>5000用户或需要AD对接）：独立Realm。这类客户付费能力最强、定制需求最多、对数据隔离的合规要求最严，值得独占一个Realm。

第二层——**Pro中型客户**（500-5000用户）：按行业分Realm。比如"零售业租户Realm"、"制造业租户Realm"、"金融业租户Realm"，同一行业的客户共享一个Realm，用Group+Role做二次隔离。同行业客户的合规要求和认证方式差异较小，共享Realm不会产生配置冲突。

第三层——**Basic小型客户**（<500用户）：共享一个`micro-tenants` Realm。所有小客户的数据通过自定义用户属性`tenant_id`和`company_name`区分。在Token中注入`tenant_id`声明，业务应用根据该声明做数据隔离。

这三层策略的资源消耗：1000个客户中假设大客户占10%（100家）独立Realm+中型客户占20%（200家）分入5个行业Realm+小微客户占70%（700家）共享1个Realm，共计106个Realm——内存约1.5GB，启动约35秒，完全可控。

除此之外，还有三个关键设计必须现在确定：
- **租户生命周期自动化**：Realm创建→初始化配置（密码策略/Token生命期）→注册默认Client→创建租户管理员账号→启用运行→停用（禁用Realm但不删除数据）→归档（导出JSON备份到S3后删除）→永久删除。整条链路需要封装为REST API供业务系统调用。
- **Realm配额管理**：限制每Realm的用户数上限、活跃Session数上限、Client注册数上限。通过Event Listener监控资源使用，超配额时触发告警或限制创建。
- **租户级主题定制**：Keycloak主题引擎支持按Realm配置不同主题。技术上只需将每个租户的登录页设计打包为主题JAR放入`/opt/keycloak/themes/`目录，在Realm配置中指向对应主题名即可。

**小白**：（追问）那安全隔离边界呢？独立Realm之间是不是绝对没有数据泄露风险？

**大师**：我先给你一颗定心丸——在正常配置下，Realm间不存在数据泄露。但有三类边界情况需要注意：第一，Admin REST API——Master Realm的管理员调用`GET /admin/realms/{realm}/users`可以查看任意Realm的用户数据，这意味着管理员权限必须严格管控。第二，Token Exchange——如果开启了跨Realm Token Exchange（内部IdP信任），一个Realm的用户可以换取另一个Realm的Token，这本质上是一个跨Realm的授权通道，必须审核每一对信任关系。第三，事件日志——Keycloak的Event Store（登录/登出/错误事件）存储时带有`REALM_ID`字段，但如果运维日志系统（ELK/Loki）在采集时未做字段过滤，可能将租户A的登录日志暴露给租户B的管理员查看。

> **大师技术映射**：混合模式 = 大客户独立Realm + 行业分组Realm + 小微客户共享Realm，核心是用"客户价值"而非"技术平权"来决定隔离级别。安全边界 = Realm隔离是物理级的，但Admin API/Token Exchange/Event Logs这三条横切面需要额外防护。

---

## 3 项目实战

### 环境准备

- Keycloak 24.x+（支持多Realm管理）
- Python 3.10+ + requests库（`pip install requests`）
- PostgreSQL 15（业务系统独立数据库，存储租户元数据）
- Spring Boot 3.x（租户入驻API后端，选装）

### 步骤1：设计租户管理系统核心表（业务侧）

在业务系统的独立数据库中创建租户元数据表和配置表。注意：这些表属于**SaaS业务层**，不直接操作Keycloak内部表，而是通过Admin REST API间接管理Keycloak Realm。

```sql
-- 租户元数据表（业务系统独立数据库）
CREATE TABLE saas_tenants (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_name VARCHAR(200) NOT NULL,
    realm_name VARCHAR(100) UNIQUE NOT NULL,
    plan VARCHAR(20) NOT NULL CHECK (plan IN ('free','basic','pro','enterprise')),
    max_users INT DEFAULT 100,
    status VARCHAR(20) NOT NULL DEFAULT 'provisioning'
        CHECK (status IN ('provisioning','active','suspended','archived')),
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    theme_name VARCHAR(100) DEFAULT 'saas-default',
    auth_method VARCHAR(50) DEFAULT 'password',
    ldap_config JSONB,
    custom_domain VARCHAR(255)
);

-- Realm配置表（与租户一对一关联）
CREATE TABLE saas_realm_configs (
    realm_name VARCHAR(100) PRIMARY KEY REFERENCES saas_tenants(realm_name),
    access_token_lifespan INT DEFAULT 300,
    sso_session_idle_timeout INT DEFAULT 1800,
    sso_session_max_lifespan INT DEFAULT 36000,
    brute_force_enabled BOOLEAN DEFAULT true,
    password_policy TEXT DEFAULT 'length(8) and digits(1) and specialChars(1)',
    client_templates JSONB,
    created_at TIMESTAMP DEFAULT NOW()
);
```

**为什么要独立建表而非直接操作Keycloak数据库？** Keycloak内部表结构是私有实现（非公开API），版本升级时字段可能变化。通过Admin REST API操作是唯一官方支持的方式。业务表记录租户的**业务视角元数据**（套餐级别、配额限制、自定义域名等Keycloak不关心的信息），实现关注点分离。

### 步骤2：租户Realm创建自动化（Python实现）

核心自动化类，封装Keycloak Admin REST API，实现租户Realm的全生命周期管理。

```python
# tenant_provisioner.py
import requests
import secrets
import string
import json
from typing import Dict, Optional

class TenantProvisioner:
    """Keycloak多租户Realm自动化管理"""

    def __init__(self, keycloak_url: str, admin_token: str):
        self.base_url = keycloak_url.rstrip("/")
        self.headers = {
            "Authorization": f"Bearer {admin_token}",
            "Content-Type": "application/json"
        }

    def create_tenant(self, tenant_config: dict) -> dict:
        """
        完整创建租户Realm，包括：Realm本身 → 默认Client → 默认角色 → 管理员账号 → 主题
        """
        realm_name = tenant_config["realm_name"]

        # 1. 创建Realm本体
        realm_data = {
            "realm": realm_name,
            "enabled": True,
            "displayName": tenant_config["company_name"],
            "loginWithEmailAllowed": True,
            "accessTokenLifespan": 300,        # 5分钟
            "ssoSessionIdleTimeout": 1800,      # 30分钟
            "ssoSessionMaxLifespan": 36000,     # 10小时
            "bruteForceProtected": True,
            "eventsEnabled": True,
            "adminEventsEnabled": True,
            "adminEventsDetailsEnabled": True
        }
        self._api("POST", "/admin/realms", data=realm_data)
        print(f"[√] Realm {realm_name} 创建成功")

        # 2. 创建默认OIDC客户端（租户的核心应用入口）
        client_data = {
            "clientId": f"{realm_name}-portal",
            "name": f"{tenant_config['company_name']} 门户",
            "enabled": True,
            "publicClient": False,
            "standardFlowEnabled": True,
            "directAccessGrantsEnabled": True,
            "serviceAccountsEnabled": True,
            "redirectUris": [tenant_config.get("redirect_uri",
                f"https://{realm_name}.saas.example.com/*")],
            "webOrigins": [tenant_config.get("web_origin",
                f"https://{realm_name}.saas.example.com")]
        }
        self._api("POST", f"/admin/realms/{realm_name}/clients", data=client_data)
        print(f"[√] Client {realm_name}-portal 创建成功")

        # 3. 创建默认角色体系
        default_roles = [
            {"name": "tenant_admin", "description": "租户超级管理员"},
            {"name": "manager", "description": "业务管理员"},
            {"name": "member", "description": "普通成员"}
        ]
        for role in default_roles:
            self._api("POST", f"/admin/realms/{realm_name}/roles", data=role)
        print(f"[√] 默认角色创建成功")

        # 4. 创建租户管理员账号（初始密码随机生成，强制首次登录修改）
        temp_password = self._generate_password()
        admin_user = {
            "username": f"admin@{realm_name}",
            "email": f"admin@{realm_name}.saas.example.com",
            "enabled": True,
            "emailVerified": False,
            "credentials": [{
                "type": "password",
                "value": temp_password,
                "temporary": True
            }],
            "realmRoles": ["tenant_admin"]
        }
        self._api("POST", f"/admin/realms/{realm_name}/users", data=admin_user)
        print(f"[√] 管理员账号创建成功，临时密码: {temp_password}")

        # 5. 应用租户自定义登录页主题
        theme_name = tenant_config.get("theme_name", "saas-default")
        self._api("PUT", f"/admin/realms/{realm_name}", data={
            "realm": realm_name,
            "loginTheme": theme_name,
            "accountTheme": theme_name
        })
        print(f"[√] 主题 {theme_name} 应用成功")

        return {
            "realm": realm_name,
            "status": "active",
            "admin_username": f"admin@{realm_name}",
            "admin_temp_password": temp_password
        }

    def suspend_tenant(self, realm_name: str) -> dict:
        """停用租户：禁止所有用户登录并踢出已登录Session"""
        self._api("PUT", f"/admin/realms/{realm_name}", data={
            "realm": realm_name,
            "enabled": False
        })
        self._api("POST", f"/admin/realms/{realm_name}/logout-all")
        return {"realm": realm_name, "status": "suspended"}

    def archive_tenant(self, realm_name: str) -> dict:
        """归档租户：导出Realm配置后删除，数据保存在对象存储"""
        # 1. 部分导出（不含用户数据——用户数据可选择保留或导出）
        export_data = self._api(
            "POST",
            f"/admin/realms/{realm_name}/partial-export",
            data={"exportGroupsAndRoles": True, "exportClients": True}
        )
        # 2. 在实际项目中，此处调用S3/MinIO存储导出数据
        # save_to_s3(f"archived-realms/{realm_name}.json", json.dumps(export_data))
        print(f"[√] Realm {realm_name} 配置已导出")

        # 3. 删除Realm
        self._api("DELETE", f"/admin/realms/{realm_name}")
        print(f"[√] Realm {realm_name} 已删除")
        return {"realm": realm_name, "status": "archived"}

    def get_tenant_usage(self, realm_name: str) -> dict:
        """查询租户当前资源使用量（用于配额检查）"""
        user_count = len(self._api("GET",
            f"/admin/realms/{realm_name}/users", params={"max": -1}))
        sessions = self._api("GET", f"/admin/realms/{realm_name}/sessions")
        active_sessions = len(sessions)
        return {
            "realm": realm_name,
            "users": user_count,
            "active_sessions": active_sessions
        }

    def _api(self, method: str, path: str, data: Optional[dict] = None,
             params: Optional[dict] = None) -> dict:
        url = f"{self.base_url}{path}"
        resp = requests.request(
            method, url, json=data, params=params,
            headers=self.headers, timeout=30
        )
        if resp.status_code >= 400:
            raise Exception(
                f"API Error [{resp.status_code}]: {path}\n{resp.text}"
            )
        return resp.json() if resp.text else {}

    @staticmethod
    def _generate_password(length: int = 16) -> str:
        alphabet = string.ascii_letters + string.digits + "!@#$%^&*"
        return "".join(secrets.choice(alphabet) for _ in range(length))


# ========== 使用示例 ==========
if __name__ == "__main__":
    # 获取Master Realm管理员Token
    admin_login = requests.post(
        "http://localhost:8080/realms/master/protocol/openid-connect/token",
        data={
            "client_id": "admin-cli",
            "username": "admin",
            "password": "admin",
            "grant_type": "password"
        }
    )
    access_token = admin_login.json()["access_token"]

    provisioner = TenantProvisioner("http://localhost:8080", access_token)

    # 创建一个新租户
    result = provisioner.create_tenant({
        "realm_name": "customer-acme",
        "company_name": "ACME 科技有限公司",
        "theme_name": "acme-brand",
        "redirect_uri": "https://crm.acme.com/*",
        "web_origin": "https://crm.acme.com"
    })
    print(json.dumps(result, indent=2, ensure_ascii=False))
```

### 步骤3：租户自助入驻API（Spring Boot + Keycloak Admin Client）

业务系统对外暴露REST接口，允许客户自助注册并自动创建Keycloak Realm。

```java
// TenantOnboardingController.java
@RestController
@RequestMapping("/api/saas")
public class TenantOnboardingController {

    private final TenantRepository tenantRepo;
    private final ApplicationEventPublisher eventPublisher;
    private final Keycloak keycloakClient;

    public TenantOnboardingController(TenantRepository tenantRepo,
                                       ApplicationEventPublisher eventPublisher,
                                       Keycloak keycloakClient) {
        this.tenantRepo = tenantRepo;
        this.eventPublisher = eventPublisher;
        this.keycloakClient = keycloakClient;
    }

    @PostMapping("/tenants")
    public ResponseEntity<TenantResponse> createTenant(
            @RequestBody @Valid TenantRequest request) {

        // 1. 校验Realm名称规范：纯小写字母+短横线，最长64字符
        if (!request.getRealmName().matches("^[a-z][a-z0-9-]{0,63}$")) {
            return ResponseEntity.badRequest()
                .body(TenantResponse.error("Realm名称不符合规范"));
        }

        // 2. 唯一性检查
        if (tenantRepo.existsByRealmName(request.getRealmName())) {
            return ResponseEntity.badRequest()
                .body(TenantResponse.error("租户名已被占用"));
        }

        // 3. 调用Keycloak Admin REST API创建Realm
        RealmRepresentation realm = buildRealmRepresentation(request);
        try {
            keycloakClient.realms().create(realm);
        } catch (Exception e) {
            return ResponseEntity.status(500)
                .body(TenantResponse.error("Realm创建失败: " + e.getMessage()));
        }

        // 4. 创建默认客户端
        ClientRepresentation client = buildDefaultClient(request.getRealmName());
        keycloakClient.realm(request.getRealmName()).clients().create(client);

        // 5. 创建租户管理员账号
        UserRepresentation adminUser = buildAdminUser(request);
        keycloakClient.realm(request.getRealmName()).users().create(adminUser);

        // 6. 记录到业务数据库
        Tenant tenant = new Tenant();
        tenant.setRealmName(request.getRealmName());
        tenant.setCompanyName(request.getCompanyName());
        tenant.setPlan(request.getPlan());
        tenant.setStatus("active");
        tenantRepo.save(tenant);

        // 7. 异步初始化租户模板数据（示例角色、欢迎邮件等）
        eventPublisher.publishEvent(new TenantCreatedEvent(tenant));

        return ResponseEntity.ok(TenantResponse.success(tenant));
    }

    @DeleteMapping("/tenants/{realmName}")
    public ResponseEntity<Void> deleteTenant(@PathVariable String realmName) {
        // 先标记为已归档
        tenantRepo.updateStatus(realmName, "archived");
        // 异步执行导出+删除（避免长时间阻塞HTTP请求）
        eventPublisher.publishEvent(new TenantArchiveEvent(realmName));
        return ResponseEntity.accepted().build();
    }

    @GetMapping("/tenants/{realmName}/usage")
    public ResponseEntity<TenantUsage> getUsage(@PathVariable String realmName) {
        RealmResource realm = keycloakClient.realm(realmName);
        long userCount = realm.users().count();
        long sessionCount = realm.getClientSessionStats().stream()
            .mapToLong(ClientSessionStats::getActive).sum();

        TenantUsage usage = new TenantUsage();
        usage.setRealm(realmName);
        usage.setUserCount(userCount);
        usage.setActiveSessionCount(sessionCount);
        usage.setUserLimit(
            tenantRepo.findByRealmName(realmName).orElseThrow().getMaxUsers()
        );
        return ResponseEntity.ok(usage);
    }

    private RealmRepresentation buildRealmRepresentation(TenantRequest req) {
        RealmRepresentation realm = new RealmRepresentation();
        realm.setRealm(req.getRealmName());
        realm.setDisplayName(req.getCompanyName());
        realm.setEnabled(true);
        realm.setLoginWithEmailAllowed(true);
        realm.setAccessTokenLifespan(300);
        realm.setSsoSessionMaxLifespan(36000);
        realm.setBruteForceProtected(true);
        realm.setEventsEnabled(true);
        return realm;
    }

    private ClientRepresentation buildDefaultClient(String realmName) {
        ClientRepresentation client = new ClientRepresentation();
        client.setClientId(realmName + "-portal");
        client.setStandardFlowEnabled(true);
        client.setDirectAccessGrantsEnabled(true);
        client.setRedirectUris(
            List.of("https://" + realmName + ".saas.example.com/*"));
        return client;
    }
}
```

### 步骤4：Realm配额限制实现（自定义Event Listener SPI）

通过Keycloak的SPI扩展点实现租户资源使用的实时监控与超配额拦截。

```java
// TenantQuotaListenerProviderFactory.java
public class TenantQuotaListenerProviderFactory
        implements EventListenerProviderFactory {

    @Override
    public EventListenerProvider create(KeycloakSession session) {
        return new TenantQuotaListenerProvider(session);
    }

    @Override
    public String getId() {
        return "tenant-quota";
    }
}

// TenantQuotaListenerProvider.java
public class TenantQuotaListenerProvider implements EventListenerProvider {

    private static final Logger logger =
        LoggerFactory.getLogger(TenantQuotaListenerProvider.class);
    private final KeycloakSession session;

    public TenantQuotaListenerProvider(KeycloakSession session) {
        this.session = session;
    }

    @Override
    public void onEvent(Event event) {
        RealmModel realm = session.realms().getRealm(event.getRealmId());
        if (realm == null) return;
        String realmName = realm.getName();

        // 只监控用户注册和登录事件
        if (event.getType() == EventType.REGISTER
                || event.getType() == EventType.LOGIN) {

            long userCount = session.users().getUsersCount(realm, false);
            // 从Realm自定义属性中读取配额配置
            String maxUsersStr = realm.getAttribute("saas_max_users");
            if (maxUsersStr == null) return;

            int maxUsers = Integer.parseInt(maxUsersStr);

            if (userCount >= maxUsers * 0.9) {  // 90%阈值告警
                logger.warn("Realm {} 用户数接近配额上限: {}/{}",
                    realmName, userCount, maxUsers);
            }

            if (userCount >= maxUsers) {
                logger.error("Realm {} 用户数已超出配额: {}/{}",
                    realmName, userCount, maxUsers);
                // 发送Alertmanager告警，触发运维工单
            }
        }
    }

    @Override
    public void onEvent(AdminEvent event, boolean includeRepresentation) {
        // 可监控管理员操作：CREATE/UPDATE/DELETE用户、客户端等
        if (event.getOperationType() == OperationType.CREATE
                && event.getResourceTypeAsString().equals("USER")) {
            RealmModel realm = session.realms().getRealm(event.getRealmId());
            if (realm == null) return;
            long userCount = session.users().getUsersCount(realm, false);
            String maxUsersStr = realm.getAttribute("saas_max_users");
            if (maxUsersStr == null) return;
            int maxUsers = Integer.parseInt(maxUsersStr);
            if (userCount > maxUsers) {
                logger.error("Realm {} 创建用户被配额拦截", realm.getName());
                throw new RuntimeException("租户用户数已达上限，请联系管理员升级套餐");
            }
        }
    }

    @Override
    public void close() {}
}
```

SPI注册配置（`META-INF/services/org.keycloak.events.EventListenerProviderFactory`）：
```
com.saas.spi.TenantQuotaListenerProviderFactory
```

### 步骤5：测试验证

```bash
# ========== 1. 批量创建10个测试租户 ==========
for i in {1..10}; do
  curl -X POST http://localhost:8081/api/saas/tenants \
    -H "Content-Type: application/json" \
    -d "{\"realmName\":\"tenant-${i}\",\"companyName\":\"Test Company ${i}\",\"plan\":\"basic\"}"
done

# ========== 2. 验证租户间用户隔离 ==========
# 在 tenant-1 创建用户
curl -X POST http://localhost:8080/admin/realms/tenant-1/users \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"username":"zhangsan","enabled":true,"credentials":[{"type":"password","value":"test123"}]}'

# 在 tenant-2 查询用户 —— 应该查不到 tenant-1 的 zhangsan
curl http://localhost:8080/admin/realms/tenant-2/users?username=zhangsan \
  -H "Authorization: Bearer $TOKEN"
# 预期返回: []  (空数组)

# ========== 3. 验证配额限制生效 ==========
# 查看 tenant-1 当前使用情况
curl http://localhost:8081/api/saas/tenants/tenant-1/usage
# 预期返回: {"realm":"tenant-1","userCount":1,"activeSessionCount":0,"userLimit":100}

# ========== 4. 验证Realm停用 ==========
curl -X PUT http://localhost:8081/api/saas/tenants/tenant-1 \
  -H "Content-Type: application/json" \
  -d '{"status":"suspended"}'
# 然后尝试用 tenant-1 的用户登录 —— 应该返回账号已禁用
```

### 可能遇到的坑

1. **Realm命名规范**：Keycloak要求Realm名称为纯小写字母、数字和短横线，不能包含下划线、空格或大写字母。自动化创建时必须在前端和后端双校验命名规范，否则Admin API返回400错误但错误消息极其不友好（`"Invalid realm name format"`），排查困难。

2. **过多Realm的首次访问延迟**：JPA存储层对Realm做懒加载——只有被访问的Realm才会从数据库加载到缓存。但这会导致"冷启动惩罚"：租户A首次登录时，如果该Realm从未被访问过，登录延迟可能高达2-3秒。**解决方案**：在Keycloak启动脚本中添加Realm预热任务——启动后遍历所有活跃Realm并调用`GET /admin/realms/{realm}`触发加载。

3. **删除Realm后事件日志残留**：`DELETE /admin/realms/{realm}`仅删除Realm核心数据（用户、角色、客户端），不会自动清理`EVENT_ENTITY`和`ADMIN_EVENT_ENTITY`表中的历史事件。长时间运营后，已删除Realm的事件日志会持续占用数据库空间。**解决方案**：定时任务清理`REALM_ID`不在活跃Realm列表中的事件记录。

4. **Admin API创建Realm的并发限制**：当客户大规模入驻时（例如营销活动中同时注册100个租户），并发调用`POST /admin/realms`可能导致数据库锁竞争。建议在业务侧实现**串行排队机制**——将创建请求放入消息队列，由专门的Worker单线程消费并创建，保证成功率。

---

## 4 项目总结

### 三种多租户架构模式对比

| 维度 | 共享Realm+属性隔离 | 独立Realm | 独立Keycloak实例 |
|------|-------------------|----------|-----------------|
| **资源消耗** | 极低（1个Realm/所有租户） | 中等（N个Realm/集群） | 极高（N个JVM实例） |
| **数据隔离性** | 弱（靠应用层WHERE过滤） | 强（数据库REALM_ID物理隔离） | 最强（完全独立的数据库） |
| **配置独立性** | 极弱（所有租户共享密码策略/主题/Token配置） | 强（每租户独立安全策略） | 最强 |
| **运维复杂度** | 低（单一配置源） | 中等（需管理Realm生命周期） | 极高（N套独立部署和监控） |
| **扩展性** | 受单Realm性能上限约束 | 受集群内存/DB容量约束（建议≤500） | 无限（加机器即可） |
| **认证策略多样性** | 不支持（一个Realm一种认证模式） | 完全支持（每租户独立AD/Social/UKey） | 完全支持 |
| **成本模型** | 几乎零额外成本 | 每100个Realm约+800MB内存/+500MB DB | 每实例约512MB内存+独立运维人力 |
| **适用场景** | 小微SaaS、内部小团队 | 中大型SaaS、集团多业务线 | 金融/政务等高合规行业 |

### 适用场景

- **SaaS多租户平台**（CRM/ERP/OA）：不同企业客户的数据和权限需要物理隔离，同时运维团队希望集中管理——独立Realm是最佳方案。
- **集团企业多业务线**：零售、物流、金融等业务线各自拥有独立IT团队和用户群体，但共享集团基础设施——每个业务线一个Realm。
- **合作伙伴生态**：上游企业与下游经销商/代理商在同一个平台上协作，但各自的用户和权限不能互通——独立Realm+身份代理（IdP Federation）可选桥接。
- **开发/测试/生产环境隔离**：每个环境创建独立Realm，避免配置变更跨环境传播。

**不适用场景**：
- B2C超大规模应用（千万级用户），单Realm下有大量用户时独立Realm模式并无优势，建议用User Federation对接外部用户存储。
- 需要高频跨租户协作的场景（如企业间即时通讯、文档协作），Realm隔离反而成为障碍，应使用单Realm+Group+细粒度权限策略。

### 注意事项

1. **Realm数量上限**：根据测试数据，建议单集群控制在**500个Realm以内**。超过500后启动时间超过2分钟且内存达到3GB以上，影响滚动更新和故障恢复速度。
2. **启动时间线性增长**：Realm数量增加导致启动时间线性增长，需在健康检查配置中适当调大`startupProbe`的`initialDelaySeconds`。
3. **定期清理僵尸Realm**：客户试用结束后可能遗留未使用的Realm，应建立自动归档策略——连续90天无登录的免费租户自动停用，180天后归档。
4. **跨Realm身份联合**：如果后期需要跨租户协作能力，需提前规划IdP Federation架构，而不是事后"拆墙"——从独立Realm回归共享Realm的迁移成本极高。

### 常见踩坑经验

- **Realm之间属性冲突**：当多个Realm配置了相同的自定义用户属性名但类型不同（如租户A的`employee_id`是String，租户B的`employee_id`是Integer），在跨Realm Token Exchange场景下可能导致属性序列化失败。解决方案：业务侧的属性统一使用String类型，数值型字段仅在应用层解析。
- **跨Realm身份联合困难**：如果两个独立Realm的客户后期需要合并（公司并购），将租户A的用户迁移到租户B的Realm没有原生工具支持——需要编写自定义脚本逐用户导出导入，且密码哈希不可直接转移（需用户重置密码）。
- **Realm配额监控遗漏**：仅监控用户数而忽略Session数，导致单个租户的100个用户产生5000个活跃Session（每人多设备），拖慢整个集群的Infinispan Session缓存。应同时监控用户数、Session数、Client数三个指标。

### 思考题

1. **万级租户挑战**：如果需要支持10000个租户（每个租户平均20个用户，总共20万用户），Keycloak单集群的架构是否还能支持？如果不行，是否需要引入**Realm分片机制**——部署多个Keycloak集群（Cluster-A负责租户0000-4999，Cluster-B负责租户5000-9999），由前置路由层根据Realm名称将请求转发到对应集群？请设计分片路由策略和跨分片的Admin API聚合方案。

2. **白标（White Label）架构**：如何实现每个租户使用自己的域名（`https://login.tenant-a.com`和`https://login.tenant-b.com`）和完全不同的登录页设计？Keycloak的单一部署只能绑定一个前端URL（`frontendUrl`），你需要设计一个反向代理层（Nginx/Envoy）根据请求的`Host`头动态路由到不同Realm，同时结合主题引擎实现多品牌视觉。请给出Nginx配置和Keycloak主题注册方案的完整设计。
