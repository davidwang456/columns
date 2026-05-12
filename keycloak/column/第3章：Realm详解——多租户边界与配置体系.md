# 第3章：Realm详解——多租户边界与配置体系

## 1 项目背景

某集团型公司拥有三条独立业务线——**零售事业部**、**物流事业部**和**金融事业部**。零售事业部负责线上线下全渠道商品销售，物流事业部管理仓储运输和配送网络，金融事业部则运营供应链金融和消费信贷产品。三条业务线各自拥有独立的IT团队、独立的业务系统和独立的用户群体，但集团CTO要求构建统一的技术底座，以降低运维成本、统一安全策略、实现集中审计。

CTO决策：在同一套Keycloak实例中，为每个事业部创建独立的**Realm（域）**，实现用户、角色、客户端的完全隔离。同时，集团需要一个**Master Realm**作为超级管理员的统一管控入口，集中查看各事业部的配置和运行状态。此外，每条业务线还需区分**开发（dev）、测试（staging）、生产（prod）**三套环境，避免测试数据污染生产数据。

如果没有Realm的租户隔离机制，后果不堪设想：所有用户、角色、客户端混在同一个命名空间里，零售事业部的管理员可能误操作修改物流事业部的角色权限；金融事业部的高敏感客户数据可能暴露给其他部门的运维人员；任何一条业务线的配置变更可能影响到全局，导致整个认证系统不可用。数据库层面更是灾难——用户表、角色表、客户端表全量混杂，一条SQL查询就可能越过部门边界。数据安全无法保障，合规审计无从谈起，运维团队天天疲于应付"谁动了我的配置"这类问题。

这便是本章要解决的核心命题：**Realm作为Keycloak中最底层的多租户隔离单元，如何设计、配置和管理，才能在单实例中安全高效地支撑多条业务线。**

---

## 2 项目设计——剧本式交锋对话

**场景**：午休时间，公司食堂。小胖端着一盘红烧肉坐下，小白正在看Keycloak文档，大师端着咖啡走过来。

---

**小胖**：（嚼着肉）大师，我昨天在配Keycloak，发现登录进去先要选一个叫什么"Realm"的东西。这东西到底是个啥？我就想搞个登录而已，怎么还要先选"领域"？

**大师**：（笑）你可以把Realm想象成写字楼里的不同公司。整栋写字楼只有一个大门，但每家公司在自己的楼层有独立的门禁系统。A公司的员工刷卡只能进A公司的办公区，B公司的员工刷不开A公司的门。Keycloak这个实例就是那栋写字楼，每个Realm就是一层——各自管理自己的员工、门禁规则和访客权限。

**小胖**：哦——那Master Realm就是物业办公室？拿着整栋楼的万能钥匙？

**大师**：差不多就是这个意思。Master Realm是Keycloak自带的超级管理员域，Keycloak的管理员账号都在这里面创建。你可以从Master Realm出发去管理任何一个业务Realm，但反过来不行——就像物业经理可以进任何公司巡检，但公司员工进不了物业办公室。

> **技术映射**：Realm = 独立的安全域（Security Domain），每个Realm拥有独立的用户库、角色体系、客户端注册表和会话管理。Master Realm = 系统管理域，拥有跨Realm管理权限。Realm之间用户、角色、会话完全隔离。

---

**小白**：（放下筷子）我有个疑问。如果在retail-realm创建了一个用户叫"zhangsan"，他能不能登录logistics-realm的应用？或者反过来，如果两个Realm里都有用户叫"zhangsan"，它们是同一个用户吗？

**大师**：好问题。答案是完全不能共享，也完全不是同一个人。retail-realm的zhangsan和logistics-realm的zhangsan在数据库里是两条完全独立的记录，分属不同的Realm ID。即使用户名一样，密码一样，邮箱一样，Keycloak也不认为它们是同一个主体。每个Realm的用户表、角色表、客户端表在数据库层面通过`REALM_ID`字段隔离——就像两家公司的员工花名册分别锁在不同的文件柜里，同名同姓也互不相干。

**小白**：那如果某个用户真的需要跨业务线访问呢？比如集团层面的财务审计员，需要同时访问零售和物流的系统？

**大师**：这就触到了Realm设计的核心取舍。现实中有三种方案：第一，给这个审计员在两个Realm各建一个账号——简单但维护成本高；第二，使用**Identity Brokering（身份代理）**，让一个Realm信任另一个Realm的认证——相当于两个公司签了互认协议；第三，如果跨Realm访问是常态而非例外，那就说明Realm拆错了，应该合并在同一个Realm里，用**Group（用户组）**或**Role（角色）**来区分权限边界。

> **技术映射**：Realm隔离是物理级的——数据库层面`REALM_ID`字段独立存储，一个Realm的用户/角色/客户端不会出现在另一个Realm的查询结果中。跨Realm访问需要通过IdP Federation（SAML/OIDC）建立信任链，或使用Token Exchange机制。

---

**小胖**：（插嘴）那Realm配置里那一堆设置页面都是干啥的？我昨天点进去看，什么General、Login、Email、Themes、Tokens、Security Defenses……眼睛都花了。

**大师**：好，我给你逐个盘一遍：

- **General**：Realm的基本信息，包括Realm ID（创建后不可修改！这个坑无数人踩过）、显示名称、前端URL。这里还有一个关键开关叫"User-Managed Access"，控制是否允许用户自行授权第三方访问自己的资源。
- **Login**：登录行为配置，包括是否允许用户注册、找回密码、记住我、验证邮箱。这里还能配置"Require SSL"——生产环境必须设为"all requests"，否则Token在网络上裸奔。
- **Email**：SMTP邮件服务器配置。忘记密码、邮箱验证这些功能都依赖这里。调试时可以用MailHog或MailDev这类本地SMTP工具。
- **Themes**：登录页、注册页、邮箱模板的主题选择。你可以在这里挂载自定义主题，实现企业品牌化的登录界面。
- **Tokens**：Token生命周期配置——Access Token有效期、SSO Session空闲时间和最大时间、Refresh Token过期策略等。这是安全性和用户体验的博弈场。
- **Security Defenses**：安全防御配置，包括HTTP头安全策略（HSTS、X-Frame-Options、CSP）、Brute Force检测参数等。

**小白**：那Realm角色和客户端角色到底有什么区别？我在创建角色的时候总是纠结选哪个。

**大师**：这是Keycloak角色体系中最容易搞混的概念。**Realm角色**是"全局角色"，属于整个Realm——比如"store-manager"（门店经理）、"warehouse-admin"（仓库管理员）这种组织级别的角色。**客户端角色**是"局部角色"，绑定到具体客户端应用——比如零售POS系统的"cashier"（收银员）和仓库管理系统的"picker"（拣货员）。

设计哲学是：Realm角色定义**组织身份**（你是哪个部门的什么级别），客户端角色定义**应用权限**（你在具体应用里能做什么操作）。一个用户可以有多个Realm角色，每个客户端下又可以有多个客户端角色。组合使用时，通过角色复合（Composite Role）将客户端角色嵌套到Realm角色中，实现"门店经理自动拥有POS系统的所有收银权限"这样的级联效果。

> **技术映射**：Realm Settings = 域级别的安全策略与行为配置，Token = 入站认证凭据的生命周期管理，Realm Role = 全局RBAC颗粒，Client Role = 应用级RBAC颗粒。两者通过Composite Role形成树状授权结构。

---

**小胖**：（吃完最后一口肉）还有一个实际的问题——我们零售线有dev、staging、prod三套环境，是每个环境建一个Realm，还是共享一个Realm用不同Client区分？

**大师**：这个问题在社区里争论了无数次。我的建议是：**开发/测试/生产各建独立Realm**。理由很简单——你在staging上测试密码策略、改Token过期时间、调SMTP配置的时候，绝对不想影响到生产环境。Realm导出导入机制可以帮你把配置从dev复制到staging再到prod，保持一致性。

另一种流派是"共享Realm + 不同Client"——只用一个Realm，每个环境注册不同Client。这种做法配置量小，但风险大：一次Realm级别的配置变更（比如开启User Registration）会同时影响所有环境。而且测试环境的脏数据（垃圾用户、过期Session）会和生产数据混在一起，审计和排查时苦不堪言。

折中方案是：dev和staging可以共享一个Realm（用不同Client区分），但**生产环境必须独立Realm**——这条是铁律。

> **技术映射**：多环境隔离策略 = Realm级别隔离（安全优先）vs Client级别隔离（效率优先）。生产环境必须独立Realm，避免配置变更的爆炸半径。Realm Partial Export/Import = 配置复制的标准化手段。

---

## 3 项目实战

### 环境准备

- Keycloak 26.x 已通过Docker启动（参考第2章），管理控制台运行在 `http://localhost:8080`
- 使用Admin CLI工具`kcadm.sh`（Linux/Mac）或`kcadm.bat`（Windows），首次使用需认证：
```bash
# 登录Master Realm，获取Admin CLI会话
./kcadm.sh config credentials --server http://localhost:8080 \
  --realm master --user admin --password admin
```

---

### 步骤1：创建三个业务Realm

**目标**：通过管理控制台或Admin CLI创建零售、物流、金融三个独立Realm。

**方式一：Admin Console操作**
1. 登录 `http://localhost:8080/admin`，使用Master Realm管理员账号
2. 左上角下拉菜单 → **Create Realm**
3. 输入 Realm name: `retail-realm`，点击 Create
4. 重复步骤2-3，创建 `logistics-realm` 和 `finance-realm`

**方式二：Admin CLI操作**
```bash
# 创建零售Realm
./kcadm.sh create realms -s realm=retail-realm -s enabled=true -s displayName="零售事业部"

# 创建物流Realm
./kcadm.sh create realms -s realm=logistics-realm -s enabled=true -s displayName="物流事业部"

# 创建金融Realm
./kcadm.sh create realms -s realm=finance-realm -s enabled=true -s displayName="金融事业部"

# 验证创建结果
./kcadm.sh get realms --fields realm,displayName,enabled
```

**运行结果**：
```
[
  {"realm":"master","displayName":"Keycloak","enabled":true},
  {"realm":"retail-realm","displayName":"零售事业部","enabled":true},
  {"realm":"logistics-realm","displayName":"物流事业部","enabled":true},
  {"realm":"finance-realm","displayName":"金融事业部","enabled":true}
]
```

---

### 步骤2：配置Realm差异化Token设置

**目标**：根据三条业务线的安全要求差异，配置不同的Token生命周期。

**背景**：零售系统直接面向C端消费者，安全要求最高——Access Token有效期应尽可能短（5分钟），配合Refresh Token保证用户体验。物流系统需要手持终端长时间离线操作，Token有效期需要更长（30分钟）。金融系统居中。

**操作**：分别进入各Realm → **Realm Settings → Tokens** 页面配置：

| 配置项 | retail-realm | logistics-realm | finance-realm |
|--------|-------------|-----------------|---------------|
| Access Token Lifespan | 5分钟 | 30分钟 | 15分钟 |
| SSO Session Idle | 30分钟 | 2小时 | 1小时 |
| SSO Session Max | 10小时 | 24小时 | 12小时 |
| Client Session Idle | 30分钟 | 2小时 | 1小时 |
| Client Session Max | 10小时 | 24小时 | 12小时 |

**Admin CLI批量配置**（以retail-realm为例）：
```bash
# 更新Token配置
./kcadm.sh update realms/retail-realm \
  -s accessTokenLifespan=300 \
  -s ssoSessionIdleTimeout=1800 \
  -s ssoSessionMaxLifespan=36000 \
  -s clientSessionIdleTimeout=1800 \
  -s clientSessionMaxLifespan=36000

# 验证配置
./kcadm.sh get realms/retail-realm --fields accessTokenLifespan,ssoSessionIdleTimeout,ssoSessionMaxLifespan
```

**运行结果**：
```json
{
  "accessTokenLifespan": 300,
  "ssoSessionIdleTimeout": 1800,
  "ssoSessionMaxLifespan": 36000
}
```

**说明**：`accessTokenLifespan=300` 即300秒（5分钟）。当Access Token过期后，客户端需要用Refresh Token换取新的Access Token，这个过程对用户透明。Refresh Token的过期时间由`clientSessionMaxLifespan`控制——零售场景最长10小时，之后用户必须重新登录。

---

### 步骤3：配置Realm角色体系

**目标**：在各Realm中创建符合业务模型的组织角色。

**retail-realm角色体系**：
```bash
# 切换到retail-realm上下文
./kcadm.sh config credentials --server http://localhost:8080 \
  --realm master --user admin --password admin

# 创建Realm级角色
./kcadm.sh create realms/retail-realm/roles -s name=realm-admin -s description="零售域管理员"
./kcadm.sh create realms/retail-realm/roles -s name=store-manager -s description="门店经理"
./kcadm.sh create realms/retail-realm/roles -s name=cashier -s description="收银员"

# 查看角色列表
./kcadm.sh get realms/retail-realm/roles --fields name,description
```

**logistics-realm角色体系**：
```bash
# 创建物流域的Realm级角色
./kcadm.sh create realms/logistics-realm/roles -s name=logistics-admin -s description="物流域管理员"
./kcadm.sh create realms/logistics-realm/roles -s name=warehouse-manager -s description="仓库管理员"
./kcadm.sh create realms/logistics-realm/roles -s name=dispatcher -s description="调度员"
./kcadm.sh create realms/logistics-realm/roles -s name=driver -s description="司机"

# 验证
./kcadm.sh get realms/logistics-realm/roles --fields name
```

**运行结果**：retail-realm拥有3个角色，logistics-realm拥有4个角色。切换Realm选择器后在"Roles"菜单下确认角色彼此独立——在retail-realm中看不到logistics-realm的driver角色，反之亦然。

---

### 步骤4：Realm导出备份

**目标**：将retail-realm的完整配置导出为JSON文件，用于环境迁移或灾备。

**操作**：
```bash
# 导出retail-realm完整配置（包含用户、角色、客户端等）
./kcadm.sh get realms/retail-realm --format export --file retail-realm-export.json

# 仅导出Realm配置（不含用户数据）
./kcadm.sh get realms/retail-realm --format export --fields realm,roles,clients,groups \
  --file retail-realm-config-only.json
```

**替代方案：使用`export`命令**（在Keycloak启动时或运行时）：
```bash
# 通过KC_BOOTSTRAP_ADMIN凭据执行运行时导出
# 注意：此方式需要Keycloak以export模式启动或使用--export-realm参数
docker exec keycloak /opt/keycloak/bin/kc.sh export \
  --realm=retail-realm --dir /tmp/export --users realm_file
```

**导出的JSON结构预览**（retail-realm-export.json）：
```json
{
  "realm": "retail-realm",
  "displayName": "零售事业部",
  "enabled": true,
  "accessTokenLifespan": 300,
  "ssoSessionIdleTimeout": 1800,
  "roles": {
    "realm": [
      {"name": "realm-admin", "description": "零售域管理员"},
      {"name": "store-manager", "description": "门店经理"},
      {"name": "cashier", "description": "收银员"}
    ]
  },
  "clients": [...],
  "users": [...],
  "groups": [],
  "scopeMappings": [...]
}
```

**导入命令**（用于环境迁移）：
```bash
# 通过Admin CLI执行部分导入
./kcadm.sh create partialImport -r target-realm \
  -f retail-realm-export.json
```

---

### 步骤5：Realm事件和日志配置

**目标**：配置Realm级别的事件记录策略，用于安全审计和登录追踪。

**操作**：进入目标Realm → **Realm Settings → Events** 页面：

```bash
# 配置retail-realm的事件策略
./kcadm.sh update realms/retail-realm \
  -s eventsEnabled=true \
  -s 'eventsListeners=["jboss-logging"]' \
  -s adminEventsEnabled=true \
  -s adminEventsDetailsEnabled=true

# 配置保存的事件类型（勾选所有登录和Token相关事件）
./kcadm.sh update realms/retail-realm \
  -s 'enabledEventTypes=["LOGIN","LOGIN_ERROR","REGISTER","REGISTER_ERROR",\
    "LOGOUT","TOKEN_EXCHANGE","REFRESH_TOKEN","CODE_TO_TOKEN",\
    "UPDATE_PASSWORD","VERIFY_EMAIL","IMPERSONATE","CLIENT_LOGIN"]'

# 设置事件过期时间（保留7天）
./kcadm.sh update realms/retail-realm \
  -s eventsExpiration=604800
```

**验证登录事件**：
1. 在retail-realm中创建一个测试用户
2. 使用该用户进行一次登录操作
3. 回到管理控制台 → **Events → Login Events**，查看登录记录

运行结果：事件列表显示登录时间、客户端IP、用户ID、事件类型（LOGIN）等详细信息。可通过筛选器按事件类型、用户、日期范围过滤。

**Admin Events**（独立于Login Events）：记录管理员操作（创建/删除/修改用户、角色、客户端等），在 **Events → Admin Events** 页面查看。

---

### 可能遇到的坑

1. **Realm名称创建后不可修改**
   Realm Name（即Realm ID）一旦创建就无法变更。虽然`displayName`可以随时修改，但系统内部以Realm ID作为主键。命名时务必遵循规范（如`{业务线}-{环境}`格式：`retail-prod`、`logistics-staging`），避免日后需要重新创建导致用户数据迁移。

2. **混淆Master Realm与业务Realm的管理员**
   Master Realm的管理员可以在任何Realm中操作，但Master Realm本身不应创建业务用户和业务客户端。业务Realm的管理员权限由Realm内的`realm-admin`角色控制，切不可将Master Realm的`admin`角色赋予业务管理员。

3. **导出/导入的版本兼容性**
   不同Keycloak主版本（如23.x→26.x）的导出JSON可能存在字段差异，导入前应先在测试环境验证。部分字段（如加密密钥、密码哈希）跨版本可能不兼容。

4. **过多Realm的性能影响**
   每个Realm对应数据库中的一组完整表数据。当Realm数量超过50时，会话表、事件表的全表扫描会显著影响性能，且每次登录都会查询Realm配置表。建议Realm数量控制在50个以内，超过需考虑多Keycloak实例的分片部署。

---

### 测试验证

**验证目标**：确认三个Realm的用户完全隔离。

```bash
# 1. 在retail-realm创建用户
./kcadm.sh create users -r retail-realm -s username=retail-test -s enabled=true
./kcadm.sh set-password -r retail-realm --username retail-test --new-password test123

# 2. 尝试用retail-realm的用户获取logistics-realm的Token（预期失败）
curl -X POST "http://localhost:8080/realms/logistics-realm/protocol/openid-connect/token" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "client_id=admin-cli&username=retail-test&password=test123&grant_type=password"
```

**预期结果**：返回 `401 Unauthorized`，错误信息为 `"error":"invalid_grant","error_description":"Invalid user credentials"`。retail-realm的用户在logistics-realm的认证端点无法通过校验，因为两个Realm的用户库完全独立。

---

## 4 项目总结

### 方案对比

| 维度 | Realm隔离（本章方案） | 独立Keycloak实例 | 单Realm+多Client |
|------|---------------------|-------------------|------------------|
| 隔离强度 | ★★★★★ 数据库级隔离 | ★★★★★ 进程级隔离 | ★★☆☆☆ 仅配置级区分 |
| 运维成本 | 低 | 高（多实例维护） | 极低 |
| 资源开销 | 中（共享进程和DB） | 高（独立进程+独立DB） | 低 |
| 配置互通 | 支持Role映射、IdP信任 | 需外部同步工具 | 天然互通 |
| 故障爆炸半径 | 单Realm级别 | 单实例级别 | 全局级别 |
| 适合租户数 | <50 | >100 | <10 |

### 适用场景

- **多业务线隔离**：集团型公司多条独立业务线，各自独立管理用户与权限
- **多环境隔离**：开发/测试/生产环境通过独立Realm完全隔离，避免测试数据污染
- **SaaS租户隔离**：中小规模SaaS平台（<50租户），每个租户一个Realm
- **集团组织架构映射**：子公司/分支机构通过Realm树形结构映射组织边界
- **安全合规要求**：金融、医疗等强监管行业要求租户数据物理隔离

### 不适用场景

- **需要跨Realm用户无缝共享**：若用户频繁跨业务线访问，应在同一个Realm内用Group+Roles区分，而非拆分Realm
- **Realm数量超过100**：单实例承载100+个Realm会出现数据库性能瓶颈（会话表爆增、配置缓存膨胀），应改用独立实例分片或考虑自定义用户存储提供者

### 注意事项

- **命名规范**：Realm Name创建后不可修改，建议采用`{业务线}-{环境}`命名规范（如`retail-prod`、`logistics-staging`）
- **备份策略**：定期执行Realm Partial Export，建议每日自动备份生产Realm到安全存储
- **事件日志存储**：Login Events和Admin Events默认存储在数据库中，大量事件会膨胀数据库。建议设置合理的过期时间（7-30天），或对接ELK等外部日志系统
- **Security Defenses不要遗漏**：新建Realm后务必检查Security Defenses页面，生产环境应启用HSTS（max-age至少31536000秒）、X-Frame-Options（SAMEORIGIN）和Content-Security-Policy

### 常见踩坑

1. **Master Realm管理混乱**：开发者图方便，把业务用户、业务客户端直接创建在Master Realm中，导致超级管理员域沦为业务域，安全边界崩塌。纠正成本极高——需要完整的数据迁移。
2. **Realm导出遗漏Secrets**：导出Realm时默认不导出客户端密钥（Client Secret），导入后需要重新生成密钥并更新所有集成应用的配置。
3. **跨环境迁移忽略版本兼容**：从dev的Keycloak 25.x导出JSON直接导入prod的Keycloak 26.x，字段不兼容导致导入失败或配置丢失。

### 思考题

1. **如果某SaaS平台需要支持1000个租户（每个租户一个Realm），Keycloak会遇到哪些性能瓶颈？可以如何解决？**（提示：从数据库连接池、会话缓存、Realm配置缓存、事件表膨胀、JPA查询效率等角度思考；解决方向包括Realm分片、多实例+路由网关、独立DB per Tenant等。）

2. **Keycloak的Realm导出是全量还是可以增量的？在对一个已运行半年、拥有50万用户的生产Realm执行导出时，你该如何制定备份策略？**（提示：`partial-export` vs `full-export`、`--users`参数的选择、导出文件大小与备份窗口的权衡。）
