# 第16章：【基础篇综合实战】搭建企业级SSO统一认证平台

## 1 项目背景

某中型制造企业成立于2008年，员工规模2000余人，主营精密零部件加工。十年间，IT系统像搭积木一样陆续堆叠起来——OA办公系统部署于2010年，基于.NET框架自研，使用Windows AD域直接认证；ERP系统是2013年采购的某商业套件，自带用户表，有一套独立的账号密码体系；CRM客户管理系统和工单系统于2018年上线，共用一个OpenLDAP目录服务；企业邮箱使用Exchange Server，通过Active Directory同步账号。五个核心系统，四条认证链路，各自为政。

员工的日常是：早上打开电脑先登录域账号进OA，打开ERP输第二套密码，切到CRM再输LDAP密码（有人设置成和ERP密码一样，有人懒得记就贴个便签在显示器边框上），遇到设备报修打开工单系统又是同一套LDAP密码——但Session不共享，每天至少登录三四次。IT服务台每月接到约80个"密码忘了"的工单，占全部工单量的40%以上。HR部门更头疼：新员工入职需要IT在OA、ERP、CRM、工单系统、邮箱五个系统分别创建账号；员工离职时，HR系统走完审批流，IT需要逐个系统禁用账号，偶尔漏掉一两个系统的账号成了"僵尸号"，安全审计报告上亮红灯。

2024年底的一次安全审计揭开了更深的问题：35%的账号属于已离职员工但未清理，12%的ERP账号存在多人共用现象（一个班组六个人共享一个"主管账号"），密码复杂度合规率不足60%。信息安全部门在年度工作会议上立下"军令状"——一个月内将五个核心系统全部接入统一认证平台，实现单点登录、统一账号生命周期管理、强制执行密码安全策略，所有登录操作可审计可追溯。

项目需求清单明确如下：

| 优先级 | 需求 | 说明 |
|--------|------|------|
| P0 | 统一登录入口 | 五个系统共用一个品牌化登录页，一次登录全系统通行 |
| P0 | AD员工同步 | 老员工的AD账号不丢失、不重建，平滑过渡 |
| P0 | 新员工自动创建账号 | 入职流程触发自动分配部门角色和默认权限 |
| P1 | 密码策略强制执行 | 12位以上、含大小写+数字+特殊字符、历史密码5次不可重复 |
| P1 | 失败登录锁定 | 5次失败锁定15分钟，防止暴力破解 |
| P1 | 离职自动禁用 | HR离职流程触发API即时禁用账号并强制下线 |
| P2 | 审计日志 | 所有登录操作记录、可接入SIEM |

这就牵涉到第1-15章所学的几乎全部核心模块——Realm多租户、客户端管理、用户与凭证体系、角色与RBAC、OAuth2/OIDC协议、Token机制、密码策略与暴力破解防护、会话管理与SSO、品牌化主题定制、LDAP/AD联邦、Admin REST API自动化。本章将这些知识点拧成一股绳，完成一个真实的企业级项目落地。

---

## 2 项目设计——剧本式交锋对话

**小胖**（抱着一杯奶茶走进会议室，把白板笔往桌上一丢）：大师、小白，我这周末帮朋友搬家，突然对这次统一认证改造有了深刻感悟！你们想象一下——一栋老旧办公楼，五层楼每个房间的门锁都不一样：一楼是球形锁、二楼是磁卡锁、三楼是密码锁、四楼是指纹锁、五楼居然还用挂锁！现在物业要求装一套统一门禁系统——进大门刷一次卡，所有房间都能进。但物业说了：不能砸墙，不能换门，不能影响住户正常进出。这不就是咱们给五个系统接Keycloak嘛！

**大师**（端起保温杯抿了一口）：小胖今天这比喻堪比产品经理的需求文档。门禁系统就是Keycloak——统一认证入口。磁卡就是你登录后的Session。五个房间的旧锁就是各系统自带的认证方式——AD、数据库用户表、LDAP……关键在于"不能砸墙"——业务系统不能停摆，老用户不能要求重新注册。

**小白**（在白板上画了五个方框和中间一个大圆）：比喻很妙，但技术细节需要落地。我有三个核心问题。第一，五个系统的代码大部分是老旧遗留系统，尤其是ERP那个PHP老古董，开发团队早没人了，源代码都找不到——这种零代码改动的系统怎么接入Keycloak？第二，2000多个AD用户用了十年，密码、权限、部门属性都沉淀在AD里，如果要求所有员工重新注册，IT会被骂死——如何实现平滑过渡？第三，HR系统是企业自研的Java应用，离职审批走完后怎么自动触发Keycloak禁用账号？

**大师**：这三个问题恰好是本次项目的三大支柱。先说第一点——零代码侵入的接入策略。对于ERP这种无法修改源码的遗留系统，我们在Nginx反向代理层做认证拦截，利用Nginx的`auth_request`模块对每个请求发起子请求到Keycloak验证登录状态。Nginx层面判定的结果是"放行"还是"重定向登录页"，后端应用完全无感知——这就相当于在旧房间的门口加装了一个统一门禁的读卡器，房间原来的锁还挂着，但从来不锁了。

第二点——AD用户平滑过渡。我提一个叫"双轨过渡"的方案。核心思路是：Keycloak通过User Federation（第13章知识点）对接企业AD，将AD设为用户数据的权威来源，但不在Keycloak中重建用户。员工用AD密码登录Keycloak，Keycloak通过LDAP协议向AD校验密码——这一步员工完全无感知，他们继续用同一个密码，只是登录的页面换了。同时，Keycloak开启Import Users功能，通过周期性同步将AD用户的属性（用户名、邮箱、部门、所属组）拉取到Keycloak本地缓存，用于角色分配和Token签发。

第三点——HR离职Webhook。HR系统在离职审批通过后，调用Keycloak Admin REST API（第14章知识点）禁用用户账号并强制下线所有会话。不需要等IT手动操作，从HR点"确认离职"到该员工所有系统登录失效，延迟不超过30秒。

> **大师技术映射**：Nginx auth_request → 旧房间门口加装统一门禁读卡器，旧锁悬挂但不锁。双轨过渡 → 既保留旧户口本（AD）的权威性，又在物业登记处（Keycloak）存了复印件方便查。HR Webhook → HR签完离职单的瞬间，门禁系统立即注销该员工的磁卡。

---

**小胖**（第二轮，从工位上抱来了笔记本电脑）：大师说得美，但我担心的是一出事怎么办。万一Keycloak自己挂了，五个系统是不是全部瘫痪？这是不是把四个小故障域合并成了一个超级大故障域？还有密码策略——原来AD有AD的规则、ERP有ERP的规则，现在统一了，用谁的规则？给设严格了老员工骂娘，设松了安全审计过不了。

**大师**：故障域的问题问到根上了。单体Keycloak确实是单点故障风险，但基础篇阶段我们先搞定功能跑通，中级篇第17章会专门讲集群部署。现阶段在Docker Compose中给Keycloak配置健康检查和自动重启，配合PostgreSQL做数据持久化，单节点故障可在一分钟内自愈。另外，已登录用户的Session在Keycloak宕机期间仍有效——因为各应用在实际鉴权时校验的是Token签名而非实时查询Keycloak，Keycloak只负责"发证"，不负责每次"验票"。

密码策略方面，Keycloak的Password Policies（第9章知识点）支持细粒度配置，而且可以设置多级策略叠加。我们的做法是：在Realm级别设一个基线策略（12位、复杂度四选三、历史密码5次），这是面向全公司的硬性要求。对于财务、HR等敏感部门，通过Group级别的角色关联，在应用侧额外校验Access Token中包含的部门Claim——如果Token里标记用户属于"财务组"，应用端要求每次操作进行二次确认。这样既保证了安全底线，又不至于给普通操作岗位造成困扰。

**小白**（放下笔）：灰度发布和回滚方案呢？2000多个用户的生产环境，直接一把切的风险太大了。

**大师**：灰度发布分三步走。第一步——并行期（第1-3天）：五个系统同时保留旧认证方式和Keycloak新认证方式，员工可以自愿试用新登录页，收集反馈。第二步——分批迁移（第4-10天）：按部门灰度，先切研发部50人，观察两天；再切行政和销售部门200人，再观察两天；最后切生产车间1500人。每批切完后监控Keycloak登录错误率、AD认证延迟、用户工单量。第三步——全量切换（第11-15天）：确认稳定后，关闭旧认证入口。

回滚方案高度依赖第3步中的Nginx层面开关——每个系统的Nginx配置中保留一个`location`块，切换一个变量即可快速回退到旧认证方式。AD联邦模式下回滚更简单：员工直接用AD密码登录各系统原始入口，Keycloak故障不影响原有认证链路。

> **大师技术映射**：灰度发布 → 新锁装上后先只给研发部发新钥匙，旧钥匙还能用，慢慢扩大发新钥匙的范围。回滚 → 门禁断电时，老挂锁还在门上，重新锁上即可。

---

## 3 项目实战

### 环境准备

| 组件 | 版本/说明 | 角色 |
|------|----------|------|
| Keycloak | quay.io/keycloak/keycloak:26.1 | 统一认证核心 |
| PostgreSQL | postgres:16-alpine | Keycloak数据库持久化 |
| OpenLDAP | osixia/openldap:1.5.0 | 模拟企业AD域 |
| Nginx | nginx:1.25-alpine | 反向代理 + auth_request认证层 |
| 模拟OA | nginx:alpine (app1-oa) | 模拟.NET OA系统 |
| 模拟ERP | nginx:alpine (app2-erp) | 模拟PHP遗留ERP |
| 模拟CRM | nginx:alpine (app3-crm) | 模拟CRM系统 |
| 模拟工单 | nginx:alpine (app4-ticket) | 模拟工单系统 |
| 模拟邮箱 | nginx:alpine (app5-mail) | 模拟Exchange邮箱 |
| HR Webhook | python:3.11-slim (Flask) | 模拟HR离职通知 |

### 步骤1：整体架构Docker Compose编排

**目标**：一键启动全部10个容器，搭建完整的SSO演示环境。

创建`docker-compose.yml`：

```yaml
services:
  postgres:
    image: postgres:16-alpine
    container_name: kc-postgres
    environment:
      POSTGRES_DB: keycloak
      POSTGRES_USER: keycloak
      POSTGRES_PASSWORD: kc-pass
    networks:
      - sso-net

  keycloak:
    image: quay.io/keycloak/keycloak:26.1
    container_name: keycloak
    command:
      - start
      - --db=postgres
      - --db-url=jdbc:postgresql://postgres:5432/keycloak
      - --db-username=keycloak
      - --db-password=kc-pass
      - --hostname=keycloak
      - --http-enabled=true
      - --http-port=8080
    environment:
      KC_BOOTSTRAP_ADMIN_USERNAME: admin
      KC_BOOTSTRAP_ADMIN_PASSWORD: admin
    ports:
      - "8080:8080"
    depends_on:
      - postgres
    networks:
      - sso-net

  openldap:
    image: osixia/openldap:1.5.0
    container_name: openldap
    environment:
      LDAP_ORGANISATION: "Precision Manufacturing Co."
      LDAP_DOMAIN: "precisionmfg.local"
      LDAP_ADMIN_PASSWORD: "ldap-admin"
      LDAP_BASE_DN: "dc=precisionmfg,dc=local"
    ports:
      - "389:389"
    networks:
      - sso-net

  nginx-gateway:
    image: nginx:1.25-alpine
    container_name: nginx-gateway
    ports:
      - "80:80"
    volumes:
      - ./nginx/nginx.conf:/etc/nginx/nginx.conf:ro
      - ./nginx/conf.d:/etc/nginx/conf.d:ro
    depends_on:
      - keycloak
    networks:
      - sso-net

  app1-oa:
    image: nginx:alpine
    container_name: app1-oa
    volumes:
      - ./apps/oa/index.html:/usr/share/nginx/html/index.html:ro
    networks:
      - sso-net

  app2-erp:
    image: nginx:alpine
    container_name: app2-erp
    volumes:
      - ./apps/erp/index.html:/usr/share/nginx/html/index.html:ro
    networks:
      - sso-net

  app3-crm:
    image: nginx:alpine
    container_name: app3-crm
    volumes:
      - ./apps/crm/index.html:/usr/share/nginx/html/index.html:ro
    networks:
      - sso-net

  app4-ticket:
    image: nginx:alpine
    container_name: app4-ticket
    volumes:
      - ./apps/ticket/index.html:/usr/share/nginx/html/index.html:ro
    networks:
      - sso-net

  app5-mail:
    image: nginx:alpine
    container_name: app5-mail
    volumes:
      - ./apps/mail/index.html:/usr/share/nginx/html/index.html:ro
    networks:
      - sso-net

  hr-webhook:
    image: python:3.11-slim
    container_name: hr-webhook
    working_dir: /app
    volumes:
      - ./hr-webhook:/app
    command: sh -c "pip install flask requests && python app.py"
    ports:
      - "5000:5000"
    networks:
      - sso-net

networks:
  sso-net:
    driver: bridge
```

启动环境：

```bash
docker compose up -d
```

等待所有10个容器进入`healthy`/`running`状态后，访问`http://localhost:8080`进入Keycloak管理控制台，使用`admin/admin`登录，进入Admin Console创建一个名为`precisionmfg`的新Realm作为本项目的租户空间。

### 步骤2：配置AD Federation

**目标**：将OpenLDAP（模拟AD）作为User Federation接入Keycloak Realm。

选择`precisionmfg` Realm → **User Federation** → **Add provider** → **ldap**，配置如下：

| 配置项 | 值 | 说明 |
|--------|-----|------|
| Console Display Name | precisionmfg-ad | 联邦名称 |
| Enabled | ON | 启用 |
| Edit Mode | READ_ONLY | 密码等修改不回写AD |
| Vendor | Active Directory | 底层协议适配（OpenLDAP选Other亦可） |
| Connection URL | ldap://openldap:389 | 容器间通信 |
| Users DN | ou=people,dc=precisionmfg,dc=local | 用户搜索根节点 |
| Bind DN | cn=admin,dc=precisionmfg,dc=local | Keycloak连接LDAP的凭据 |
| Bind Credential | ldap-admin | 管理员密码 |
| Search Scope | Subtree | 递归搜索子目录 |
| Import Users | ON | 将LDAP用户同步到Keycloak本地数据库（只读副本） |
| Sync Registrations | ON | 周期性同步 |

点击**Test connection**和**Test authentication**验证连通性。保存后点击**Synchronize all users**触发首次全量同步。

配置Group映射：在联邦配置页→**Mappers**→创建新的**group-ldap-mapper**，将AD中的`memberOf`属性映射到Keycloak Group：

| 配置项 | 值 |
|--------|-----|
| Name | ad-group-mapper |
| LDAP Groups DN | ou=groups,dc=precisionmfg,dc=local |
| Group Name LDAP Attribute | cn |
| Membership LDAP Attribute | member |
| Mode | READ_ONLY |

### 步骤3：Nginx auth_request实现老旧系统无侵入接入

**目标**：对无法修改源码的ERP系统（模拟旧PHP应用），通过Nginx反向代理层做认证拦截，无需改动应用一行代码。

创建`nginx/conf.d/erp.conf`：

```nginx
server {
    listen 80;
    server_name erp.precisionmfg.local;

    location / {
        auth_request /auth;
        error_page 401 = @keycloak_login;

        # 透传用户身份信息到后端
        auth_request_set $user $upstream_http_x_user;
        proxy_set_header X-User $user;

        proxy_pass http://app2-erp:80;
    }

    location = /auth {
        internal;
        proxy_pass http://keycloak:8080/realms/precisionmfg/protocol/openid-connect/userinfo;
        proxy_set_header Authorization $http_authorization;
        proxy_pass_request_body off;
        proxy_set_header Content-Length "";
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }

    location @keycloak_login {
        return 302 http://keycloak:8080/realms/precisionmfg/protocol/openid-connect/auth
            ?client_id=erp-system
            &redirect_uri=$scheme://$host$request_uri
            &response_type=code
            &scope=openid;
    }
}
```

**工作原理**：Nginx收到用户请求→发起内部子请求`/auth`→子请求携带用户浏览器的`Authorization`头访问Keycloak userinfo端点→Keycloak验证Access Token有效→返回200→Nginx放行请求到后端ERP→用户看到ERP页面。如果Token无效或不存在→Keycloak返回401→Nginx命中`@keycloak_login`→浏览器重定向到Keycloak登录页→用户输入AD账号密码→登录成功后Keycloak重定向回ERP→用户无感知。

在Keycloak中注册ERPSystem客户端（选择OpenID Connect、访问类型**public**），配置Redirect URIs为`http://erp.precisionmfg.local/*`。其余四个系统（OA、CRM、工单、邮箱）的接入方式类似，每个系统在Nginx配置中对应一个server块、在Keycloak中对应一个客户端。

### 步骤4：品牌化登录页部署

**目标**：应用第11章的自定义主题，将Keycloak默认登录页替换为企业品牌（公司Logo、主色调#1B3A5C、中文提示语）。

主题开发已在第11章详细讲解，这里仅列出部署关键步骤：将定制好的`precisionmfg-theme`目录挂载到Keycloak容器的`/opt/keycloak/themes/`路径。在`docker-compose.yml`的keycloak服务中添加volume：

```yaml
volumes:
  - ./themes/precisionmfg-theme:/opt/keycloak/themes/precisionmfg-theme:ro
```

然后在Realm Settings → **Themes**中将Login Theme设为`precisionmfg-theme`。

### 步骤5：配置完整安全策略

**目标**：将第9章（密码策略与暴力破解防护）和第10章（会话管理）的知识综合落地。

**密码策略**（Realm Settings → Authentication → Password Policy）：

| 策略 | 值 | 作用 |
|------|-----|------|
| Minimum Length | 12 | 最少12位 |
| Uppercase Characters | 1 | 至少1个大写字母 |
| Lowercase Characters | 1 | 至少1个小写字母 |
| Digits | 1 | 至少1个数字 |
| Special Characters | 1 | 至少1个特殊字符 |
| Password History | 5 | 新密码不能与最近5次重复 |

**暴力破解防护**（Realm Settings → Security Defenses → Brute Force Detection）：

| 配置项 | 值 | 说明 |
|--------|-----|------|
| Enabled | ON | |
| Max Login Failures | 5 | 5次失败后锁定 |
| Wait Increment | 1 minute | 每次连续失败等待时间递增 |
| Max Wait | 15 minutes | 最长锁定15分钟 |
| Failure Reset Time | 12 hours | 12小时后失败计数清零 |
| Quick Login Check Milli Seconds | 1000 | 快速检查缓存时间 |

**会话策略**（Realm Settings → Tokens）：

| 配置项 | 值 |
|--------|-----|
| SSO Session Idle | 30 minutes |
| SSO Session Max | 10 hours |
| Client Session Idle | 30 minutes |
| Client Session Max | 10 hours |

**并发会话**（Realm Settings → Sessions）：

| 配置项 | 值 |
|--------|-----|
| Limit concurrent sessions | ON |
| Max concurrent sessions | 3 |

**Token策略**（Realm Settings → Tokens）：

| 配置项 | 值 | 理由 |
|--------|-----|------|
| Access Token Lifespan | 5 minutes | 缩短泄露窗口 |
| Refresh Token Lifespan | 30 minutes | 覆盖最长空闲超时 |

### 步骤6：HR系统Webhook实现自动账号管理

**目标**：模拟HR系统在员工离职审批通过后，通过Admin REST API自动禁用Keycloak账号并强制下线所有会话。

创建`hr-webhook/app.py`：

```python
from flask import Flask, request, jsonify
import requests

app = Flask(__name__)

KEYCLOAK_URL = "http://keycloak:8080"
REALM = "precisionmfg"
ADMIN_USER = "admin"
ADMIN_PASS = "admin"


def get_admin_token():
    resp = requests.post(
        f"{KEYCLOAK_URL}/realms/master/protocol/openid-connect/token",
        data={
            "grant_type": "password",
            "client_id": "admin-cli",
            "username": ADMIN_USER,
            "password": ADMIN_PASS,
        },
    )
    return resp.json()["access_token"]


@app.route("/webhook/employee/offboard", methods=["POST"])
def employee_offboard():
    data = request.json
    username = data.get("username")
    action = data.get("action")

    if not username:
        return jsonify({"error": "username is required"}), 400

    token = get_admin_token()
    headers = {"Authorization": f"Bearer {token}"}

    # 查找用户
    resp = requests.get(
        f"{KEYCLOAK_URL}/admin/realms/{REALM}/users",
        params={"username": username, "exact": "true"},
        headers=headers,
    )
    users = resp.json()

    if not users:
        return jsonify({"error": "user not found"}), 404

    user_id = users[0]["id"]

    if action == "disable":
        # 禁用账号
        requests.put(
            f"{KEYCLOAK_URL}/admin/realms/{REALM}/users/{user_id}",
            json={"enabled": False},
            headers=headers,
        )
        # 强制下线所有会话
        requests.post(
            f"{KEYCLOAK_URL}/admin/realms/{REALM}/users/{user_id}/logout",
            headers=headers,
        )
        return jsonify({"status": "disabled", "user_id": user_id})

    elif action == "enable":
        requests.put(
            f"{KEYCLOAK_URL}/admin/realms/{REALM}/users/{user_id}",
            json={"enabled": True},
            headers=headers,
        )
        return jsonify({"status": "enabled", "user_id": user_id})

    return jsonify({"error": "invalid action"}), 400


@app.route("/webhook/employee/onboard", methods=["POST"])
def employee_onboard():
    data = request.json
    token = get_admin_token()
    headers = {"Authorization": f"Bearer {token}"}

    resp = requests.post(
        f"{KEYCLOAK_URL}/admin/realms/{REALM}/users",
        json={
            "username": data["username"],
            "email": data["email"],
            "firstName": data["firstName"],
            "lastName": data["lastName"],
            "enabled": True,
            "groups": data.get("groups", []),
            "credentials": [{"type": "password", "value": data["initialPassword"], "temporary": True}],
        },
        headers=headers,
    )
    return jsonify(resp.json()), resp.status_code


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
```

**测试Webhook**：

```bash
# 禁用账号
curl -X POST http://localhost:5000/webhook/employee/offboard \
  -H "Content-Type: application/json" \
  -d '{"username": "zhangsan", "action": "disable"}'

# 新员工入职
curl -X POST http://localhost:5000/webhook/employee/onboard \
  -H "Content-Type: application/json" \
  -d '{"username": "newemp001", "email": "newemp001@precisionmfg.local", "firstName": "小明", "lastName": "王", "initialPassword": "Temp@12345678", "groups": ["/production"]}'
```

### 步骤7：审计日志配置

**目标**：记录所有登录、登出、Token签发操作，存储30天用于安全审计和SIEM接入。

Realm Settings → **Events**：

| 配置项 | 值 |
|--------|-----|
| Save Events | ON |
| Saved Types | LOGIN, LOGOUT, REGISTER, CODE_TO_TOKEN, LOGIN_ERROR, REFRESH_TOKEN, TOKEN_EXCHANGE, CLIENT_LOGIN |
| Expiration | 30 days |
| Admin Events（Saved）| ON |

如需接入外部SIEM系统（如Splunk），在第26章（中级篇）会讲解通过自定义Event Listener SPI将事件实时推送到Kafka/ELK。

---

### 测试验证清单

| 验证项 | 操作步骤 | 预期结果 |
|--------|---------|---------|
| SSO登录串联 | 登录OA→新开Tab访问ERP/CRM/工单/邮箱 | 四个系统无需再次输入密码，直接进入 |
| AD用户登录 | 使用OpenLDAP中已存在的AD账号密码登录Keycloak | 登录成功，Keycloak用户列表中出现该联邦用户 |
| 新员工创建 | POST /webhook/employee/onboard → 在Keycloak中搜索新用户 | 用户存在且拥有指定Group角色 |
| 新员工登录 | 新员工使用临时密码登录 | 被提示修改密码，修改后可访问所有5系统 |
| 离职禁用 | POST /webhook/employee/offboard → 用该账号尝试登录 | 返回"账号已禁用" |
| 强制离线 | 登录状态下触发offboard → 刷新页面 | 页面被重定向到登录页 |
| 暴力破解测试 | 连续输入5次错误密码 → 第6次尝试 | 返回"用户已临时锁定"，15分钟后自动解封 |
| 密码策略验证 | 设置密码为"123456789012" | 拒绝并提示缺少大小写字母和特殊字符 |
| 审计日志 | Admin Console → Events → 查看Login和Login Error事件 | 所有登录尝试均有记录，含IP、时间戳、客户端 |

### 可能遇到的坑

| 故障现象 | 根因 | 解决方案 |
|---------|------|---------|
| Nginx auth_request超时 | `proxy_read_timeout`默认为60s，Keycloak userinfo端点响应慢时nginx等待过久 | 在`/auth` location中添加`proxy_read_timeout 5s;`，超时快速失败 |
| AD同步后新用户无法立即登录 | Keycloak缓存了用户列表，新同步的用户要等缓存过期才可见 | 在联邦配置中缩短用户缓存TTL，或手动点**Synchronize all users**触发即时刷新 |
| 五个系统Session超时不一致 | 各系统自身的Cookie过期时间未与Keycloak Token对齐 | 应用端读取Access Token的`exp`字段，在Token过期前主动静默续期或重定向登录 |
| Backchannel Logout漏网 | 部分应用未配置Backchannel Logout URL，单点注销时被跳过 | 在Keycloak客户端配置中填写Backchannel Logout URL并设置`Backchannel Logout Session Required`为ON |
| OpenLDAP容器启动后用户数据丢失 | 未挂载LDAP数据目录，容器重启后数据重置 | 在docker-compose中添加`volumes: - ./ldap-data:/var/lib/ldap`和`- ./ldap-slapd:/etc/ldap/slapd.d` |
| HR Webhook Token频繁获取 | 每次调用都获取一次Admin Token，存在性能开销且增加master Realm的Token签发压力 | 加入Token缓存机制，过期前复用（代码中增加简单的内存缓存检查`exp`字段） |

---

## 4 项目总结

### 项目成果回顾

一个月内，五个核心系统的认证链路从四条孤立的"羊肠小道"整合成一条统一的SSO高速公路。员工打开任何系统，浏览器自动重定向到带有企业Logo的登录页，输入AD账号密码后全系统通行。新员工入职时HR系统触发Webhook自动创建账号并分配部门角色，离职时审批通过即自动禁用账号并踢出所有在线会话——从第15章到第16章，我们亲手搭建了一套可运行的统一认证平台。

### 企业级SSO方案对比

| 维度 | 本方案（Keycloak+AD Federation） | 自研JWT认证中心 | 商业SaaS（Okta/Auth0） | 纯AD/LDAP直连 |
|------|-------------------------------|---------------|----------------------|-------------|
| 建设周期 | 1个月（含测试） | 3-6个月 | 1-2周配置 | 0（已有基础设施） |
| 初始成本 | 零（开源） | 高（开发人力） | 中（按用户数月付费） | 零 |
| 协议支持 | OIDC/SAML/LDAP全覆盖 | 依赖自研实现 | OIDC/SAML | 仅LDAP/Kerberos |
| 遗留系统接入 | Nginx auth_request无侵入 | 需改造应用代码 | 需SDK集成 | 天然（但无OIDC） |
| 密码策略 | 内置12+策略可组合 | 需自研实现 | 内置 | 仅AD密码策略 |
| 单点注销 | Backchannel+Frontchannel | 需自行实现 | 内置 | 不支持 |
| 审计日志 | 内置+可扩展SPI | 需自研 | 内置 | 依赖域控日志 |
| 运维复杂度 | 低-中 | 高 | 低（厂商维护） | 中 |

### 关键指标

- **SSO覆盖率**：从0提升到100%，五个系统全部实现单点登录
- **用户登录效率**：平均从每次3次登录操作（约2分钟）降为1次（约15秒），时间减少约87%
- **密码重置工单**：从每月80+张降至每月8张以下，减少90%
- **账号安全合规率**：密码复杂度合规率从60%提升至100%，离职账号清理率从65%提升至100%

### 下一步改进方向

1. **集群高可用**：当前单体Keycloak存在单点故障风险，中级篇第17-20章将部署多节点集群，配合Infinispan分布式缓存和PostgreSQL主从复制实现高可用
2. **多因素认证**：接入第12章的社交登录（企业微信）和第37章的自定义MFA（短信OTP），为财务等敏感部门增加二次验证
3. **SIEM集成**：通过第26章的Event Listener SPI将登录日志实时推送到Kafka→ELK，实现异常登录（异地IP、深夜登录、高频失败）自动告警
4. **逐步淘汰LDAP**：在Keycloak稳定运行6个月后，评估将用户数据全量迁移到Keycloak本地数据库，关闭User Federation，降低对AD的实时依赖

### 注意事项

1. **运维交接文档**：项目验收时务必交付完整的架构拓扑图、Docker Compose编排说明、Keycloak Realm配置导出JSON、Nginx配置文件清单、Webhook接口文档。不要让知识锁在老员工脑子里
2. **监控告警配置**：对接Prometheus采集Keycloak的`/metrics`端点（第30章详细讲解），设置Keycloak服务不可用、AD联邦连通性失败、登录错误率突增三条核心告警规则
3. **灾备方案**：定期执行`partial export`导出Realm配置和用户数据（`/admin/realms/precisionmfg/partial-export`端点），存储到安全异地。Keycloak服务器不可用时，Nginx网关切到维护模式展示静态"系统维护中"页面，而非直接暴露后端应用
4. **SSL证书**：本实战为演示环境使用了HTTP，生产环境务必为Keycloak和Nginx配置HTTPS，并将Realm的`Require SSL`设为`all requests`，Cookie的`SameSite`设为`None; Secure=true`

### 思考题

1. **跨域多AD Federation架构**：假设公司收购了一家子公司，该子公司有自己独立的AD域（`child.precisionmfg.local`），与母公司的AD域（`precisionmfg.local`）之间无信任关系。母公司的部分员工需要访问子公司的系统，子公司的员工也需要访问母公司的系统。请设计如何在一个Keycloak Realm中同时配置两个LDAP User Federation源，并确保双方用户在登录时路由到正确的AD域进行密码校验。（提示：关注Keycloak对多User Federation Provider的支持策略、Username密码校验时的匹配顺序、以及如何通过Authentication Flow中的`Identity Provider Redirector`实现按邮件域名自动选择LDAP源）

2. **不停服版本升级**：Keycloak 26.x运行6个月后发布了27.0版本，修复了一个严重的安全漏洞。如何在不停服的情况下将生产集群从26.x滚动升级到27.x？请设计升级方案，包含数据库Schema迁移策略（liquibase自动执行 vs 手动执行）、节点逐个替换的灰度流程、以及升级失败后的快速回滚方案。（提示：参考Keycloak的Rolling Upgrade官方文档，关注`--spi`参数和Infinispan缓存协议的跨版本兼容性）

### 部门协作推广计划

| 部门 | 核心任务 | 建议阅读顺序 |
|------|---------|------------|
| 开发团队 | 理解OAuth2流程、客户端配置、Token校验逻辑、API集成方式 | 第1章→第4章→第7章→第8章→第14章→第15章→本章 |
| 运维团队 | Docker部署、Nginx反向代理配置、LDAP联邦维护、监控告警 | 第2章→第3章→第9章→第10章→第13章→本章 |
| 测试团队 | SSO场景测试用例设计、跨系统登录/注销流程验证、安全策略覆盖测试 | 第4章→第5章→第7章→第9章→第10章→本章 |

基础篇到此完结。从第1章的术语全景到第16章的5系统统一认证平台落地，我们完成了从理论学习到生产实践的完整闭环。下一章将进入中级篇——集群架构与高可用设计，把单点Keycloak扩展为支撑万级并发的分布式认证中心。

---

> **推广计划提示**：本章为基础篇收官之战，建议团队以小组形式完成Docker Compose部署并跑通全部7个测试验证项。开发人员重点关注Nginx auth_request和Admin REST API的集成模式，运维人员重点关注Docker编排和联邦配置，测试人员参照测试验证清单编写自动化测试脚本。
