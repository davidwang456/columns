# 第13章：用户联邦——LDAP/AD集成

## 1 项目背景

某传统制造企业成立于2004年，二十年间积累了3000余名员工的核心账号数据，全部存储在微软Active Directory（AD）中。AD承载了员工计算机登录、文件共享权限、打印机访问、内部邮件系统等日常办公的全部认证需求。近两年公司启动数字化转型战略，IT部门陆续上线了CRM客户管理系统、OA审批系统、BI报表平台等多个SaaS应用。管理层的目标是：所有新系统实现统一认证，员工只需记住一套账号密码。

理想很丰满，现实很骨感。AD暴露的认证接口是LDAP（Lightweight Directory Access Protocol）协议——一种诞生于1993年的目录访问协议。新上线的SaaS系统大多只支持OIDC或SAML协议，与LDAP天生不对付。CRM厂商的技术支持直接回复："我们支持SAML，不支持LDAP绑定，请用户自行对接。"BI报表平台更干脆，只提供了OIDC登录入口。IT部门不得不面对一个窘境：要么给每个系统单独维护一套账号（员工记三套密码，离职后HR得挨个通知各系统管理员删账号），要么想办法在AD和新系统之间加一层"翻译器"。

更大的隐患潜伏在细节中。AD的LDAP查询性能存在硬瓶颈——单次LDAP Bind认证耗时在50-200ms之间，当3000名员工早高峰集中登录时，认证压力全部压在域控制器上。AD的密码策略（必须包含大写+小写+数字+特殊字符，90天强制过期）与新系统的安全要求不完全一致，某些SaaS应用的后台密码校验逻辑在AD密文面前直接报错。更棘手的是，"员工离职后，AD账号半小时内禁用，但CRM里的登录会话还挂着"——这就是典型的幽灵会话问题。

另一个难以回避的事实是：Keycloak不直接修改AD数据。用户在Keycloak端修改密码后，密码变更无法回写AD（单向联邦）。这带来了两种解决思路——要么Keycloak全量导入AD用户后在本地维护（双写方案），要么Keycloak按需从AD拉取用户数据（联邦方案）。双写方案下Keycloak本地用户和AD用户的数据一致性问题是场噩梦：AD里改了邮箱，Keycloak里还是旧邮箱，谁来同步、多久同步一次、同步失败怎么办？

Keycloak的User Federation机制正是为这种场景而生：Keycloak不存储AD用户的完整副本，而是通过LDAP协议按需从AD中拉取用户信息并完成认证。Keycloak对上层应用暴露标准OIDC接口，AD完全无感知——员工仍然用AD密码登录，新系统通过Keycloak验证身份，数据源头依然是AD。

---

## 2 项目设计——剧本式交锋对话

**小胖**（抱着一袋干脆面走进会议室）：大师，大师！我昨天突然想通了一个问题。你知不道公司HR系统里的员工信息就是权威数据源？入职、调岗、离职全在HR系统里操作，其他系统——比如门禁、食堂消费、OA——都要从HR系统同步员工信息。这不就是你说的User Federation嘛！那Keycloak为啥不直接把AD里的3000个用户一次性导入自己的数据库，以后就跟AD没关系了？省得每次都去AD查，多慢。

**大师**（放下手中的保温杯）：小胖，你用HR系统做比喻是对的，但你设想的"一次性导入"方案恰好踩中了联邦设计最大的坑。我问你一个问题：如果AD里李四的邮箱从`lisi@oldcompany.com`改成`lisi@newcompany.com`，你Keycloak里还存着旧邮箱，登录时给SaaS系统发了一个旧邮箱的Claim——CRM的邮件通知发到旧邮箱去了，李四收不到，谁的责任？

**小白**（在白板上写下几个词）：所以联邦（Federation）和导入（Import/Sync）的本质区别是——联邦相当于"每次去AD现场查户口本"，导入相当于"把户口本复印一份放自己抽屉里"。前者保证数据的权威性，后者带来副本的一致性问题。但我有更具体的问题：LDAP那一堆术语——DN、Base DN、Bind DN——到底是什么？配置LDAP User Federation的时候每个字段起什么作用？

**大师**：好，先扫清LDAP术语障碍。LDAP目录是一棵树，每个节点（条目）都有一个唯一标识叫**DN（Distinguished Name，可分辨名称）**，类似文件系统中的绝对路径。比如John Doe在LDAP中的DN可能是`uid=johndoe,ou=people,dc=mycompany,dc=com`。拆开看：`dc=mycompany,dc=com`是域组件（Domain Component），把域名`mycompany.com`拆成两段；`ou=people`是组织单元（Organizational Unit），类似文件夹中的"用户"目录；`uid=johndoe`是用户ID，类似文件名。

**Base DN**是你搜索的起始节点——相当于告诉Keycloak"从这个分支往下找用户"。如果设为`dc=mycompany,dc=com`，就会搜索整个域；设为`ou=people,dc=mycompany,dc=com`则只搜people下的用户。设错了Base DN，用户一个都搜不到，这是配置中最高发的低级错误。

**Bind DN**是Keycloak用来"登录LDAP"的凭据——LDAP不允许匿名操作，Keycloak需要以一个已知身份绑定后才能执行搜索。通常用AD的管理员账号如`cn=Administrator,cn=Users,dc=mycompany,dc=com`，或专用服务账号如`cn=keycloak-svc,ou=service,dc=mycompany,dc=com`。Bind Credential就是这个账号的密码。

> **大师技术映射**：LDAP目录 → 公司档案室的文件柜。DN → 档案的完整编号（包含楼层-房间-柜号-格号）。Base DN → "请从这个柜子开始往后找"。Bind DN → 档案室管理员的工作证，没有它连门都进不去。

---

**小胖**（嚼着干脆面）：那密码的事怎么说？如果在Keycloak端让用户改密码，能回写到AD里吗？要不能的话，用户改完密码还得再跑到Windows上改一次AD密码，这不是更麻烦了吗？

**大师**：这是User Federation的**单向性**根本约束。Keycloak提供了三种Edit Mode配置——`READ_ONLY`：Keycloak只从LDAP读取数据，不能向LDAP写入任何内容，用户密码修改、属性更新都由AD侧控制。`WRITABLE`：Keycloak可以将用户属性变更（如邮箱、部门）写入LDAP，但密码操作受限——LDAP密码通常以哈希形式存储（AD使用NTLM Hash），Keycloak默认的密码哈希算法与AD不兼容，强制写回可能导致用户再也无法通过AD登录Windows。`UNSYNCED`：折中方案，用户从LDAP导入后，Keycloak断开与LDAP的关联，后续修改都只在Keycloak本地生效，LDAP不再受影响。

生产环境99%的情况选`READ_ONLY`，理由很简单：AD是公司统一的密码权威源，任何可能破坏AD密码一致性的操作都是不可接受的。用户在Keycloak端看到的"修改密码"按钮就直接灰掉或者跳转到AD的密码自助服务页面。如果业务确实需要用户在Keycloak端修改密码并回写AD，必须启用**Kerberos集成**：Keycloak通过Kerberos协议执行`kpasswd`操作，利用Kerberos的`set_password`服务来修改AD密码。这套配置远比LDAP基础认证复杂，需要配置Keycloak服务器的Kerberos客户端（krb5.conf）、确保Keycloak服务器已加入AD域、设置正确的SPN（Service Principal Name）。

**小白**：那双写方案在什么场景下合理？如果一家小公司只有50个人，AD也不是什么复杂的企业版，能不能直接用Keycloak做用户数据库算了？

**大师**：双写方案的核心矛盾是"谁才是真相来源（Source of Truth）"。当AD和Keycloak各自维护一份用户数据时，任何属性变更都可能产生分歧——AD说张三在研发部，Keycloak说张三在市场部，到底信谁的？解决这个矛盾需要专门的同步机制（Periodic Sync），但同步本身存在延迟窗口，这期间可能出现两个版本的"真相"。小公司如果AD使用率低、用户属性简单、没有复杂的域策略，确实可以考虑直接从AD全量导入用户到Keycloak，然后把Keycloak作为新的事实来源，逐步废弃AD——这是迁移路径中的过渡方案，而非长期并存的架构。

> **大师技术映射**：READ_ONLY → 图书馆只能阅览不可外借，所有书必须在馆内。WRITABLE → 允许做笔记但不能撕页。UNSYNCED → 整本书影印带走，之后和原件再无关系。

---

**小胖**（第二轮）：上周我试了试LDAP同步，3000个用户同步了快两分钟。这个性能能优化吗？还有万一AD服务器挂了，Keycloak这边是不是所有用户都登不了？

**大师**：性能优化有三个杠杆。第一是**LDAP连接池**——Keycloak内部维护一组到LDAP服务器的长连接。配置项`connectionPoolSize`决定同时维持多少个连接，建议设置为核心数的2倍（如20个）。如果并发登录量大，连接池耗尽后新的认证请求会排队等待，响应时间暴涨。第二是**分页查询**——同步大量用户时开启`Pagination=true`并设置合理的`pageSize`（如1000），避免一次查询加载全部数据导致LDAP服务器OOM或网络超时。第三是**缓存策略**——Keycloak对联邦用户的第一次认证会经过完整的LDAP Bind+Search流程，认证成功后将用户数据写入本地缓存。后续请求命中缓存后不再穿透到LDAP，认证延迟可从200ms降到5ms以下。User Federation配置页的Cache Settings中可控制用户缓存时间和失效策略。

关于AD宕机问题，这就是**联邦降级方案**的核心场景。Keycloak并不会在AD宕机瞬间踢出所有已登录用户。用户的User Session由Keycloak自身管理，只在认证环节才需要访问LDAP。已登录用户的Token在有效期内依然可用，只有新登录请求会失败。但这里有一个敏感点：如果Token中携带了LDAP同步过来的用户属性（如部门、邮箱），且Token的有效期较长，这段时间内AD属性变更无法反映到Token中——这就是"属性同步延迟"和"Token数据陈旧"的交叉地带。要实现优雅降级，建议在Keycloak前面加一个健康检查机制：如果LDAP健康检查失败（连续N次Bind超时），切换到携带陈旧数据但维持服务的"半断网"模式，并触发告警。

> **大师技术映射**：LDAP连接池 → 银行柜台窗口数，窗口太少客户排队，窗口太多柜员闲置。缓存 → 大脑记住了邻居长什么样，不用每次见面都看身份证。降级方案 → 手机没信号时，已下载的地图依然能导航，但搜不了新地址。

---

## 3 项目实战

### 环境准备

| 组件 | 版本/说明 |
|------|----------|
| Keycloak | 26.x，基于第2-3章环境，Realm=**demo-realm** |
| Docker Compose | 2.x，用于部署OpenLDAP测试环境 |
| OpenLDAP | osixia/openldap:1.5.0（测试用，非企业AD） |
| phpLDAPadmin | osixia/phpldapadmin:0.9.0（LDAP可视化管理工具） |
| curl / jq | API调试工具 |

确认Keycloak服务正在运行，且已创建`demo-realm`。

### 步骤1：Docker Compose部署OpenLDAP测试环境

**目标**：在Docker中启动一个独立的OpenLDAP服务，模拟企业AD环境。

在项目目录下创建`docker-compose-ldap.yml`：

```yaml
services:
  openldap:
    image: osixia/openldap:1.5.0
    container_name: openldap
    environment:
      LDAP_ORGANISATION: "My Company"
      LDAP_DOMAIN: "mycompany.com"
      LDAP_ADMIN_PASSWORD: "admin123"
      LDAP_BASE_DN: "dc=mycompany,dc=com"
    ports:
      - "389:389"
    volumes:
      - ./ldap/ldif:/container/service/slapd/assets/config/bootstrap/ldif/custom
    networks:
      - keycloak-net

  phpldapadmin:
    image: osixia/phpldapadmin:0.9.0
    container_name: phpldapadmin
    environment:
      PHPLDAPADMIN_LDAP_HOSTS: openldap
      PHPLDAPADMIN_HTTPS: "false"
    ports:
      - "6443:80"
    depends_on:
      - openldap
    networks:
      - keycloak-net

networks:
  keycloak-net:
    name: keycloak-net
    external: true
```

> **注意**：上述网络配置要求Keycloak也加入名为`keycloak-net`的Docker网络。如果Keycloak以宿主机直连方式运行，OpenLDAP容器端口已映射到宿主机389端口，Keycloak中配置`ldap://localhost:389`即可。下面的示例假设Keycloak与OpenLDAP在同一个Docker网络中，Keycloak通过容器名`openldap`访问。

```bash
# 创建LDIF目录并启动服务
mkdir -p ldap/ldif
docker compose -f docker-compose-ldap.yml up -d
```

运行结果：两个容器启动后，可以通过`http://localhost:6443`访问phpLDAPadmin管理界面。登录DN填`cn=admin,dc=mycompany,dc=com`，密码`admin123`。

### 步骤2：向OpenLDAP导入测试用户

**目标**：创建组织架构和测试用户数据，模拟真实企业LDAP目录结构。

创建`ldap/ldif/users.ldif`文件：

```ldif
# 创建组织单元 people
dn: ou=people,dc=mycompany,dc=com
objectClass: organizationalUnit
ou: people

# 创建组织单元 groups
dn: ou=groups,dc=mycompany,dc=com
objectClass: organizationalUnit
ou: groups

# 创建组织单元 service（服务账号）
dn: ou=service,dc=mycompany,dc=com
objectClass: organizationalUnit
ou: service

# 创建Keycloak服务账号（用于Bind DN）
dn: cn=keycloak-svc,ou=service,dc=mycompany,dc=com
objectClass: simpleSecurityObject
objectClass: organizationalRole
cn: keycloak-svc
userPassword: service123
description: Keycloak LDAP Bind Account

# 用户 John Doe
dn: uid=johndoe,ou=people,dc=mycompany,dc=com
objectClass: inetOrgPerson
cn: John Doe
sn: Doe
givenName: John
uid: johndoe
mail: johndoe@mycompany.com
userPassword: password123
departmentNumber: ENG-001
telephoneNumber: +86-010-8888-0001
employeeType: fulltime

# 用户 Jane Doe
dn: uid=janedoe,ou=people,dc=mycompany,dc=com
objectClass: inetOrgPerson
cn: Jane Doe
sn: Doe
givenName: Jane
uid: janedoe
mail: janedoe@mycompany.com
userPassword: password123
departmentNumber: HR-002
telephoneNumber: +86-010-8888-0002
employeeType: fulltime

# 用户 Bob Smith
dn: uid=bobsmith,ou=people,dc=mycompany,dc=com
objectClass: inetOrgPerson
cn: Bob Smith
sn: Smith
givenName: Bob
uid: bobsmith
mail: bobsmith@mycompany.com
userPassword: password123
departmentNumber: IT-003
telephoneNumber: +86-010-8888-0003
employeeType: contractor
```

导入LDIF数据：

```bash
docker cp ldap/ldif/users.ldif openldap:/tmp/users.ldif
docker exec openldap ldapadd -x -D "cn=admin,dc=mycompany,dc=com" \
  -w admin123 -f /tmp/users.ldif
```

运行结果：终端输出`adding new entry "ou=people,dc=mycompany,dc=com"`等若干行成功信息。

验证导入结果：

```bash
# 搜索所有用户
docker exec openldap ldapsearch -x -D "cn=admin,dc=mycompany,dc=com" \
  -w admin123 -b "dc=mycompany,dc=com" "(objectClass=inetOrgPerson)"
```

### 步骤3：在Keycloak中配置LDAP User Federation

**目标**：在Keycloak中添加LDAP提供者，打通Keycloak与OpenLDAP之间的用户数据通道。

操作路径：`Admin Console → demo-realm → User Federation → Add provider → LDAP`

关键配置项：

| 配置项 | 值 | 说明 |
|--------|---|------|
| Enabled | ON | 启用此联邦提供者 |
| Console Display Name | Company AD | Admin Console中显示的名称 |
| Vendor | Other | OpenLDAP选Other，生产AD选Active Directory |
| Connection URL | ldap://openldap:389 | LDAP服务器地址（Docker网络内用容器名，宿主机模式用localhost） |
| Users DN | ou=people,dc=mycompany,dc=com | 用户搜索的起始DN |
| Bind Type | simple | 简单绑定（用户名+密码） |
| Bind DN | cn=keycloak-svc,ou=service,dc=mycompany,dc=com | 服务账号DN |
| Bind Credential | service123 | 服务账号密码（点击Test Connection验证） |
| Edit Mode | READ_ONLY | 只读模式，不向LDAP写入 |
| Search Scope | Subtree | 搜索范围：One Level（仅一层）/ Subtree（全部子层级） |
| Import Users | ON | 启用用户按需导入 |
| Sync Registrations | OFF | 不将Keycloak新建用户反向写入LDAP |
| Pagination | ON | 启用分页查询 |
| Page Size | 500 | 每页返回的用户数量 |

保存配置后，点击页面顶部的**Test authentication**：输入一个LDAP用户的uid（如`johndoe`）和密码（`password123`），验证Keycloak能否通过LDAP成功认证该用户。

常见坑：`Test Connection`成功但`Test authentication`失败——Bind DN的权限不足以搜索Users DN下的条目。解决方法是确保服务账号在Users DN范围内有读取权限：在OpenLDAP中可执行`ldapsearch`以Keycloak服务账号身份验证。

### 步骤4：配置LDAP属性映射

**目标**：将LDAP目录中的用户属性映射到Keycloak用户的标准字段和自定义属性。

进入User Federation → Company AD → **Mappers**标签页，点击Add mapper创建以下映射：

| 映射类型 | Name | LDAP属性 | Keycloak字段 |
|---------|------|---------|-------------|
| user-attribute-ldap-mapper | username | uid | username |
| user-attribute-ldap-mapper | email | mail | email |
| user-attribute-ldap-mapper | firstName | givenName | firstName |
| user-attribute-ldap-mapper | lastName | sn | lastName |
| user-attribute-ldap-mapper | department | departmentNumber | department |

每个mapper的详细配置示例（以username映射为例）：

- Name: `username`
- Mapper Type: `user-attribute-ldap-mapper`
- User Model Attribute: `username`
- LDAP Attribute: `uid`
- Read Only: ON
- Always Read Value From LDAP: OFF（依赖缓存）
- Is Mandatory In LDAP: ON
- Attribute Default Value: （留空）

> **关键细节**：`Always Read Value From LDAP`设为ON意味着每次生成Token都重新查询LDAP获取该属性最新值，代价是增加认证延迟。设为OFF则使用缓存中的值，属性更新不会立即反映到Token中。生产环境建议：频繁变更的属性（如手机号）设为ON，稳定属性（如uid、部门）设为OFF。

### 步骤5：同步验证

**目标**：触发用户同步并验证LDAP用户已在Keycloak中出现。

操作：返回User Federation → Company AD，点击**Synchronize all users**按钮。页面底部显示同步进度条和统计信息（成功导入数、失败数、已更新数）。

同步完成后，进入`Admin Console → Users → View all users`，应看到三个带**Federated**标记的用户：
- johndoe
- janedoe
- bobsmith

点击任一用户，其Attributes标签页应包含从LDAP映射过来的`department`属性。

**API方式验证**：

```bash
# 获取Admin Token
ADMIN_TOKEN=$(curl -s -X POST http://localhost:8080/realms/master/protocol/openid-connect/token \
  -d "client_id=admin-cli" \
  -d "username=admin" \
  -d "password=admin" \
  -d "grant_type=password" | jq -r '.access_token')

# 查询已同步的联邦用户
curl -s "http://localhost:8080/admin/realms/demo-realm/users?search=john" \
  -H "Authorization: Bearer $ADMIN_TOKEN" | jq '.[0] | {username, email, firstName, lastName, federationLink}'
```

运行结果：

```json
{
  "username": "johndoe",
  "email": "johndoe@mycompany.com",
  "firstName": "John",
  "lastName": "Doe",
  "federationLink": "federation-link-representation"
}
```

### 步骤6：测试联邦用户登录

**目标**：验证LDAP用户可以使用AD/OpenLDAP密码直接登录Keycloak并获取Token。

```bash
# LDAP用户使用LDAP密码登录Keycloak
curl -s -X POST http://localhost:8080/realms/demo-realm/protocol/openid-connect/token \
  -d "client_id=oms-frontend" \
  -d "username=johndoe" \
  -d "password=password123" \
  -d "grant_type=password" | jq '{access_token: (.access_token[0:50] + "..."), refresh_token: (.refresh_token[0:50] + "..."), expires_in}'
```

运行结果：成功返回`access_token`、`refresh_token`、`expires_in`等字段。密码由OpenLDAP验证而非Keycloak本地数据库。

接下来可以解析Token中的用户信息，验证属性映射是否正确：

```bash
# 解析Access Token的payload
ACCESS_TOKEN=$(curl -s -X POST http://localhost:8080/realms/demo-realm/protocol/openid-connect/token \
  -d "client_id=oms-frontend" \
  -d "username=johndoe" \
  -d "password=password123" \
  -d "grant_type=password" | jq -r '.access_token')

# 提取payload（Base64解码）
echo "$ACCESS_TOKEN" | cut -d'.' -f2 | base64 -d 2>/dev/null | jq '{sub, preferred_username, email, name}'
```

运行结果输出Token中的`preferred_username`应为`johndoe`，`email`应为`johndoe@mycompany.com`。

### 步骤7：配置定期同步

**目标**：配置Keycloak自动定期从LDAP同步用户数据，避免手动触发。

操作：`User Federation → Company AD → Settings`页面下半部分**Sync Settings**区域：

| 配置项 | 值 | 说明 |
|--------|---|------|
| Periodic Full Sync | ON | 启用定期全量同步 |
| Full Sync Period (seconds) | 86400 | 每24小时执行一次全量同步 |
| Periodic Changed Users Sync | ON | 启用增量变更同步（依赖LDAP的`modifyTimestamp`或AD的`uSNChanged`属性） |
| Changed Users Sync Period (seconds) | 300 | 每5分钟同步变更用户 |

保存配置后，Keycloak后台会按设定的频率自动触发同步任务，无需人工干预。

> **Changed Users Sync原理**：Keycloak在每次同步时会记录已同步用户的最后修改时间戳。下次增量同步时，只查询自上次同步以来`modifyTimestamp`有变化的用户。这要求LDAP服务器支持修改时间戳字段——OpenLDAP默认支持，AD通过`uSNChanged`属性支持。

### 可能遇到的坑

| 故障现象 | 根因 | 解决方案 |
|---------|------|---------|
| Test Connection成功但用户搜索为空 | Search Scope误设为One Level，而用户不在Users DN的直接下一层 | 改为Subtree |
| LDAP over SSL报证书错误 | 自签名证书不被Keycloak信任 | 测试环境：在Connection URL中勾选"Enable StartTLS"为OFF；生产环境：导入LDAP服务器CA证书到Keycloak JVM truststore |
| 大量用户同步时超时 | 未开启分页查询导致LDAP服务端一次性加载全量数据内存溢出 | 开启Pagination并设置合理的pageSize |
| LDAP密码修改后Keycloak缓存期内仍用旧密码 | 用户信息缓存未过期，Keycloak优先命中缓存而非穿透LDAP | 缩短`Cache Policy`中用户缓存时间，或开启`Eviction Hour`定时清空缓存 |
| 用户属性映射不生效 | Mapper中的LDAP属性名与LDAP Schema中的实际属性名大小写不匹配 | LDAP属性名通常全小写，确认LDAP Schema中的确切属性名 |
| Bind DN权限不足 | 服务账号只有管理员所在的DN下的权限，无法搜索Users DN | 在LDAP中给服务账号分配全局读权限 |

### 测试验证清单

- [ ] LDAP用户在Keycloak用户列表中可见，标记为Federated
- [ ] LDAP用户属性（username、email、firstName、lastName、department）正确映射到Keycloak用户
- [ ] LDAP用户可通过LDAP密码成功登录Keycloak并获取Token
- [ ] 密码错误时登录失败（验证密码确实由LDAP校验而非Keycloak本地校验）
- [ ] 修改LDAP中的用户属性后，执行Synchronize操作后在Keycloak中更新
- [ ] 在LDAP中新增用户后，Synchronize后在Keycloak中出现
- [ ] 在LDAP中删除用户后，Synchronize后在Keycloak中被标记（取决于`Sync Deleted Users`配置）

---

## 4 项目总结

### 三种用户管理方案对比

| 维度 | User Federation | 全量数据导入 | 双写共存 |
|------|----------------|------------|---------|
| 数据权威源 | LDAP/AD为唯一真相来源 | Keycloak成为新来源 | 两个来源并存，需要冲突仲裁 |
| 认证性能 | 首次较慢（穿透LDAP），后续快（缓存命中） | 快（本地数据库查询） | 取决于查询路由策略 |
| AD宕机影响 | 新登录失败，已登录用户无影响 | 无影响（数据在本地） | 取决于当前查询走到哪个源 |
| 数据一致性 | 天然一致，延迟仅取决于缓存 | 导入后断开关联，完全不一致 | 依赖同步频率，存在延迟窗口 |
| 密码变更 | 由AD侧控制，不可从Keycloak回写 | Keycloak独立控制 | 冲突风险高，需明确仲裁策略 |
| 运维复杂度 | 中（需维护LDAP连接健康） | 低（一次性导入后即为静态数据） | 高（需维护同步任务、冲突处理） |

### 适用场景

- **企业AD/LDAP存量用户接入**：公司已有成熟的AD基础设施，新增SaaS应用需要通过OIDC/SAML接入统一认证。典型场景如传统企业数字化转型、政府单位信息系统整合。
- **统一认证中间层**：多个下游应用各自需要用户认证，但认证方式各不相同（OIDC、SAML、LDAP-Bind），Keycloak作为协议转换中间层统一对下对接LDAP、对上暴露OIDC。
- **混合用户来源管理**：部分用户来自AD（正式员工），部分用户来自社交登录（外包人员），部分用户为Keycloak本地用户（临时账号），三条用户来源线并存于同一Realm。
- **AD逐步迁移过渡期**：企业计划从AD迁移到Azure AD或其他云目录，迁移期间新旧系统需要并存，Keycloak作为过渡期的统一入口。

### 不适用场景

- **纯绿地项目**（无历史LDAP负担）：直接使用Keycloak本地用户数据库更简单，无需维护LDAP联邦链路。
- **对认证延迟极度敏感的系统**（P99 < 10ms）：联邦模式的首认证穿透LDAP带来了不可控的网络延迟，应全量导入用户后关闭联邦。

### 生产注意事项

1. **READ_ONLY模式是默认安全选择**：除非有确切的业务需求（如属性回写），不要轻易开启WRITABLE模式。误改AD数据的后果可能影响全公司Windows域登录。
2. **缓存策略直接影响用户属性可见性**：用户缓存TTL设为1小时意味着LDAP侧属性变更后最多1小时才能在Keycloak中生效。对时效性敏感的属性（如账户启用/禁用状态）建议缩短缓存周期或关闭缓存。
3. **LDAP服务器高可用**：Keycloak的Connection URL支持多地址配置（逗号分隔，如`ldap://ad1.company.com ldap://ad2.company.com`），Keycloak会按顺序尝试连接以实现故障转移。但这不是真正的负载均衡——第一个地址不可用时才切到第二个。
4. **Bind DN的权限最小化原则**：为非管理员操作创建专用的LDAP服务账号，只授予用户搜索和属性读取权限，严禁授予写入或管理权限。
5. **LDAP分页查询与兼容性**：并非所有LDAP服务器都支持分页控件（Paged Results Control），配置前需确认LDAP服务端兼容性。OpenLDAP和AD都支持，但部分老旧的Novell eDirectory不支持。

### 常见踩坑经验

- **Bind DN路径错误**：某团队将Bind DN配置为`cn=admin,cn=Users,dc=company,dc=com`（Windows AD的默认管理员路径），而AD实际的管理员位于`cn=Administrator,cn=Users,dc=company,dc=com`——`admin`和`Administrator`一字之差，调试了两天。
- **DN中的特殊字符转义**：某员工的CN包含逗号（如`cn=Smith, Jr., John`），LDAP协议要求逗号在DN中必须转义为`\,`，即`cn=Smith\, Jr.\, John,ou=people,...`。未正确转义导致该用户被同步遗漏，且日志中无任何报错。
- **同步大用户量导致LDAP服务器CPU飙升**：某公司AD有50000+用户，配置了每5分钟全量同步，导致域控制器CPU持续100%。正确做法是：全量同步频率设为一周一次，日常用增量变更同步维持。

### 思考题

1. **AD宕机降级问题**：当AD服务器完全宕机时，已登录的联邦用户是否会被立即踢出Keycloak？他们的Token在有效期内是否还能正常使用？如果AD宕机长达8小时，Keycloak如何实现"允许已登录用户继续工作，新登录用户收到友好提示"的优雅降级？

2. **从AD迁移到云目录**：假设公司决定将用户目录从本地AD迁移到Azure AD（Entra ID），Keycloak需要同时连接两个目录源。如何设计平滑过渡方案，让部分用户从AD认证、部分用户从Azure AD认证，且两边用户数据一致？迁移完成后如何无缝切换到纯Azure AD联邦？

3. **Kerberos集成深度**：如果业务要求用户在Keycloak端修改密码后自动回写AD，需要配置Kerberos集成。请调研Keycloak的Kerberos Federation Provider配置流程、`krb5.conf`的必要参数，以及`SPN`的正确设置方法。

---

> **推广计划提示**：本章面向运维工程师和平台开发。运维部门可参考LDAP配置和缓存调优部分直接操作生产环境，开发部门重点关注属性映射和Token中用户信息的正确性。建议配合第5章（用户管理）、第6章（角色体系）和第10章（会话管理）阅读，形成完整的"用户来源→角色分配→会话控制"知识链路。
