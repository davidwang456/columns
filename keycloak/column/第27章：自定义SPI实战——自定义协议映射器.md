# 第27章：自定义SPI实战——自定义协议映射器

## 1 项目背景

某电商平台的后端微服务集群——订单中心、库存中心、物流中心、报表中心——共12个服务，每个服务都需要根据当前登录用户的"部门树"做数据权限过滤。部门树是一个4级嵌套层级结构：公司→事业部→部门→组。例如，研发事业部下基础平台部的身份认证组员工张三，他的数据可见范围应当是"身份认证组及以下数据"，而研发事业部的技术总监则可以查看整个研发事业部的全部数据。

目前的实现方式令人焦虑：每个微服务内部都在收到Access Token后执行相同的两步查询——首先调用Keycloak Admin API获取用户属性中存储的`department_id`字段，然后带着这个ID调用公司组织架构服务REST API获取完整的部门树路径。这意味着一个普通的API请求，背后隐藏着两次额外的RPC调用。压测数据显示，单次请求的端到端延迟因此增加了50-80ms——在P99场景下，这50ms足以让一个原本10ms的查询接口超过百毫秒的SLA红线。更重要的是，12个微服务各自维护着相同的"查部门"代码逻辑，部门树的查询方式一旦变更（比如组织架构服务从REST升级到gRPC），需要协调12个团队同时修改、测试、发布。

架构师在技术评审会上画了一个红圈："Keycloak手里握着用户的所有属性，Token签发那一刻就知道用户的部门ID——为什么不能让它在签发Token的时候直接把完整部门树塞进JWT里？这样每个微服务拿到Token后直接解析Claims就能拿到部门数据，零额外查询、零网络延迟、零代码重复。"

但这个方案的直接阻力也摆在眼前。首先，内置的Protocol Mapper只能处理简单的键值映射——User Attribute Mapper能把一个用户属性平铺进一个Claim，却不能将单个`department_id`展开为四级嵌套的部门树JSON。其次，Token体积需要控制：部门树的完整JSON结构可能在1-3KB之间，如果12个微服务各自需要不同的Claims组合（订单服务需要完整部门树，报表服务只需要部门ID），必须在Token体积和信息完整性之间做灵活的按客户端取舍。最后，Script Mapper曾被用来做这种复杂映射，但Keycloak 25起已经明确废弃——JavaScript引擎（Nashorn/GraalVM）可在服务端执行任意代码，在身份认证服务器上运行不可审计的脚本是安全评审中的红线。

核心技术挑战可以归结为一点：如何通过Keycloak SPI扩展，在不触碰Keycloak源码的前提下，编写一个Java Protocol Mapper，在Token签发时动态查询组织架构服务并将嵌套的部门树JSON安全地注入到Access Token Claims中，同时按Client Scope的绑定范围控制不同客户端获取不同的Claims集合。

---

## 2 项目设计——剧本式交锋对话

**小胖**（手上转着一支笔，屏幕上开着Keycloak管理控制台的Client Scopes页面）：大师，我昨天寄快递的时候突然悟了！你看快递单——发件人的姓名、电话、地址全贴在包裹上，快递员看一眼就知道是谁寄的、从哪寄的，根本不用再打开快递公司的后台系统去查。这不就是Protocol Mapper干的事吗？把用户的信息直接"贴"到Token上，每个拿到Token的服务自己解析就能用。所以为什么还要费劲写SPI？把用户属性一股脑全塞到Token里不就行了，省得每个微服务都来调我！

**大师**（放下手里的键盘）：小胖，你这个快递单的比喻非常精准。Protocol Mapper的本质，就是用用户数据去"装饰"Token——它不是凭空生成数据，而是把Keycloak已知的（或通过外部服务查询到的）用户信息，按照某种映射规则嵌入到JWT的Claims中去。Token签发前的最后一刻，所有绑定的Protocol Mapper依次执行，把各自的"贴纸"贴上去。

但你后面的问题——"把属性全塞进去"——恰恰就是坑所在。你想想，快递单上如果印了发件人的身份证号、银行卡号、家庭住址、工作单位、最近10次寄件记录——这张单子在每个快递员手里过一遍，信息泄露的窗口就扩大一圈。Token也一样：JWT放在HTTP的Authorization Header里，HTTP Header不是无限大的。Nginx、Envoy、Tomcat这些常见的反向代理和服务容器，对Header总大小有默认8KB的限制。如果你的部门树JSON就占了2KB，再加上其他Claims、角色列表、权限信息，Token体积很容易突破安全边界。

> **大师技术映射**：快递单贴发件人信息 = Protocol Mapper向Token注入Claims。快递单印满隐私信息 = Token体积膨胀导致Header截断。

---

**小白**（从白板前转过身来，手里拿着记号笔）：大师，小胖说的一股脑塞进去我当然知道不对。我关心几个更具体的点：第一，Keycloak内置的那些Mapper分别能做什么？我注意到Admin Console里点"Add Mapper"时有一长串选项——User Attribute、User Property、Group Membership、Hardcoded Claim、Role List……它们的区别是什么？第二，Script Mapper为什么被废弃了？我知道是安全问题，但具体是哪些攻击面？第三，也是最关键的——同样是张三这个用户，他的Access Token给A客户端（订单服务）时需要包含完整部门树，给B客户端（报表服务）时只需要一个`department_id`就够了，这种"同一个用户不同客户端拿到不同Claims"的需求怎么实现？

**大师**：三个问题恰好是从认知到实战的完整路径，我们一个个来。

先讲内置Mapper的分类。Keycloak的Protocol Mapper体系本质上是一组"数据搬运工"——从不同的数据源搬运数据到Token Claims中。数据源有五种，对应五种核心内置Mapper：

- **User Property Mapper**：搬运用户的基础属性——`username`、`email`、`firstName`、`lastName`、`emailVerified`等。这些属性来自Keycloak的`UserModel`对象。
- **User Attribute Mapper**：搬运用户的自定义属性——管理员在Admin Console中为用户配置的任意键值对（如`department_id`、`employee_number`、`office_location`）。属性可以多值，但单个Mapper通常只映射一个Key。
- **Group Membership Mapper**：搬运用户的组关系——将用户所属的全部Group名称或Group路径列表注入Claim。注意它只搬运Group名称/路径，不搬运Group的属性。
- **Hardcoded Claim Mapper**：搬运一个硬编码的常量——不依赖用户数据，纯粹将一个固定值注入Token。常用于标记Token来源或版本号（如`"token_source": "keycloak-v26"`）。
- **Role List Mapper**：搬运用户的角色集合——将Realm和Client Role的名称列表（或完整角色树）注入Claim。它是Authorization体系的"前台接口"——资源服务器通常通过`realm_access.roles`字段做RBAC判断。

五种Mapper的共同局限是：它们只能做**一对一或一对多**的扁平原样搬运，无法做**结构转换**。你有一个`department_id=dept003`的属性，User Attribute Mapper能原样注入`"department_id": "dept003"`，但它不能自动把这个ID展开为{"公司":..., "事业部":..., "部门":..., "组":...}的嵌套JSON——这就是为什么要写自定义Mapper。

> **大师技术映射**：内置五种Mapper = 五条固定的传送带，分别从用户属性仓库、基础资料库、组仓库、角色仓库和常量表搬运数据到Token。每一条传送带只能搬运它认识的货物类型。

---

**大师**（喝了一口水，继续）：第二个问题——Script Mapper为什么被废弃。Script Mapper原本允许管理员在Admin Console里写一段JavaScript，这段脚本在Token签发时被Nashorn（JDK 8-14）或GraalVM JavaScript引擎执行，脚本可以访问`user`对象、`realm`对象、`session`对象，几乎能做任意的Token数据拼装。

问题出在三个层面。**安全层面**：JavaScript引擎`ScriptEngine.eval()`允许执行任意代码——一个拥有Realm管理权限的管理员可以在Script Mapper里写一段`Runtime.getRuntime().exec("rm -rf /")`，或者用`java.lang.System.exit()`关停进程，或者读取服务器文件系统的敏感配置。这不是一个可以通过沙箱简单解决的问题——Nashorn本身不支持强制沙箱，GraalVM的沙箱也需要额外配置`HostAccess`限制。**审计层面**：存储在数据库`PROTOCOL_MAPPER_CONFIG`表中的JavaScript脚本，对代码审查和变更审计完全不友好——你无法通过Git追溯谁在什么时候改了什么逻辑。**性能层面**：每次Token签发时初始化JavaScript引擎、编译脚本、执行、销毁引擎的全流程，在高并发场景下会产生显著的CPU开销。

替代方案就是本章要讲的：**Java Mapper**——将映射逻辑编译为Java类，通过SPI注册，由Keycloak的Quarkus容器在启动时加载。它享有和内置Mapper完全相同的执行优先级、生命周期管理和性能优化路径。唯一代价是：修改映射逻辑需要重新编译、打包、部署JAR并重启Keycloak——但这恰恰是"安全性"的体现：变更必须走CI/CD流程，可追溯、可回滚。

> **大师技术映射**：Script Mapper = 让快递员自己在包裹上写备注，写什么、怎么写没有约束，可能画只乌龟也可能写错门牌号。Java Mapper = 统一印制的不干胶标签纸，格式固定、内容可追溯，多快好省。

---

**大师**（继续讲第三个问题）：同一个用户不同客户端获取不同Claims——这正是Client Scope + Mapper组合的用武之地。

Keycloak的Claim注入不是"全局"的——每个Protocol Mapper可以绑定在三个层级上：**全局Client Scope**、**自定义Client Scope**和**特定Client**。全局Client Scope（如内置的`roles`、`web-origins`）对所有客户端生效。自定义Client Scope是管理员创建的任意Scope模板，里面可以挂载任意一组Mapper。最关键的是——自定义Client Scope可选地分配给各Client：**Default Scope**（默认打入Token）和**Optional Scope**（客户端显式请求时才打入Token）。

回到你的场景：创建一个名为`department-info`的自定义Client Scope，在其中挂载"部门树Map（完整部门树）"和"部门ID Mapper（简单字段）"两个Protocol Mapper。然后将`department-info`作为Optional Scope分配给订单服务客户端和报表服务客户端。订单服务在请求Token时，在`scope`参数中显式包含`department-info`，Token中即获得完整部门树；报表服务的Token请求不带该scope参数，Token中就只有默认Claims，不包含部门树。这就是按客户端定制的本质——不是同一个Token变来变去，而是**不同的Token请求产生不同内容的Token**。

---

**小胖**（把笔一扔，掏出手机）：大师，那我还有个问题——如果部门树深度有10层，每层节点又带20多个属性（部门ID、部门名称、部门类型、父部门ID、负责人、联系电话、办公地址、成立时间……），这JSON得有20KB了吧？Token也太大了！有没有一种方案——Token里只放个部门ID，微服务查Redis拿完整部门树？

**大师**：你触及了Token设计中一个根本性的权衡——**自包含Token（Self-contained Token）vs 引用Token（Reference Token）**。

自包含Token的方案——也就是本章的做法——是将所有业务数据直接内嵌在JWT中。优点是微服务零依赖、零延迟（解析JWT即可拿到数据）、无外部网络调用。缺点是Token体积与数据量成正比，且Token一旦签发，其内嵌数据在Token有效期内无法实时更新（张三调了部门，但已签发的Token里还是旧部门树）。

引用Token的方案——Token中只放一个`department_id`，微服务在接收到请求后用这个ID查Redis或组织架构服务获取完整部门树。优点是Token体积极轻（几百字节），且部门数据实时更新。缺点也很明显：每个微服务必须能访问Redis（引入了基础设施依赖），每次请求多一次网络查询（Redis延迟约1-2ms，可以接受），且如果Redis数据与实际组织架构服务不一致，会产生权限判断偏差。

业界还有一种折中方案叫**Phantom Token**——Token签发时是自包含的完整JWT，但API Gateway在转发给后端微服务之前，将Token替换为一个无序的UUID（Phantom Token），同时将完整JWT存入共享缓存。后端微服务收到Phantom Token后，通过调用Gateway的introspect端点或直接查缓存来获取Claims。这个方案的巧妙之处在于：Token在"公网"上暴露的是短小的UUID，不泄露任何业务信息；在"内网"上后端服务仍然可以获取完整数据。Curity公司最早推广了这一模式。

另一个值得关注的标准是**OAuth 2.0 Rich Authorization Request（RAR，RFC 9396）**——它允许客户端在授权请求中以结构化的方式声明它需要哪些"授权详情"。这与Client Scope实现的功能相似，但粒度更细、支持JSON结构化的请求参数。Keycloak 26尚未原生支持RAR，但可以通过自定义Authenticator和Mapper的组合实现类似的动态授权数据注入。

> **大师总结技术映射**：

| 生活比喻 | 技术映射 |
|---------|---------|
| 快递单贴发件人信息 | Protocol Mapper注入Token Claims |
| 不同快递公司用不同格式的快递单 | Client Scope按客户端定制Claims |
| 快递单太大塞不进快递柜格子 | Token超过8KB Header限制 |
| 快递单只写个取件码，快递柜里存包裹 | Reference Token（引用模式） |
| 快递单在外面只显示编号，快递员扫描后能看到完整信息 | Phantom Token模式 |
| 发件人声明"本次快递需要保价、需要签收拍照" | Rich Authorization Request（RAR） |

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

目标：开发一个自定义Protocol Mapper，在Access Token中注入用户所属的完整部门树（4级嵌套JSON），在ID Token中只注入简单字段，并通过Client Scope实现按客户端定制Claims。

---

### 步骤1：创建部门树Protocol Mapper

**目标**：实现自定义Protocol Mapper的核心逻辑——查询部门树并将嵌套JSON注入Token。

```java
package com.mycompany.keycloak.mapper;

import org.keycloak.models.*;
import org.keycloak.protocol.ProtocolMapperModel;
import org.keycloak.protocol.oidc.mappers.*;
import org.keycloak.provider.ProviderConfigProperty;
import org.keycloak.representations.AccessToken;
import org.keycloak.representations.IDToken;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

public class DepartmentTreeProtocolMapper extends AbstractOIDCProtocolMapper
        implements OIDCAccessTokenMapper, OIDCIDTokenMapper, UserInfoTokenMapper {

    public static final String PROVIDER_ID = "department-tree-mapper";

    // 模拟组织架构数据（生产环境应从组织架构服务获取）
    private static final Map<String, DepartmentNode> ORG_TREE;

    static {
        ORG_TREE = new LinkedHashMap<>();
        ORG_TREE.put("root",   new DepartmentNode("root",   "公司",       "company",    null));
        ORG_TREE.put("div001", new DepartmentNode("div001", "研发事业部",  "division",   "root"));
        ORG_TREE.put("div002", new DepartmentNode("div002", "营销事业部",  "division",   "root"));
        ORG_TREE.put("dept001",new DepartmentNode("dept001","基础平台部",  "department", "div001"));
        ORG_TREE.put("dept002",new DepartmentNode("dept002","业务中台部",  "department", "div001"));
        ORG_TREE.put("dept003",new DepartmentNode("dept003","电商业务部",  "department", "div002"));
        ORG_TREE.put("grp001", new DepartmentNode("grp001", "身份认证组",  "group",      "dept001"));
        ORG_TREE.put("grp002", new DepartmentNode("grp002", "网关组",      "group",      "dept001"));
        ORG_TREE.put("grp003", new DepartmentNode("grp003", "订单组",      "group",      "dept003"));
    }

    @Override
    public AccessToken transformAccessToken(AccessToken token,
            ProtocolMapperModel mappingModel, KeycloakSession session,
            UserSessionModel userSession, ClientSessionContext clientCtx) {

        UserModel user = userSession.getUser();
        String departmentId = user.getFirstAttribute("department_id");

        if (departmentId != null) {
            List<Map<String, String>> deptTree = getDepartmentPath(departmentId);
            token.getOtherClaims().put("department_tree", deptTree);
            token.getOtherClaims().put("department_id", departmentId);

            if (!deptTree.isEmpty()) {
                token.getOtherClaims().put("department_name",
                        deptTree.get(deptTree.size() - 1).get("name"));
            }
        }
        return token;
    }

    @Override
    public IDToken transformIDToken(IDToken token, ProtocolMapperModel mappingModel,
            KeycloakSession session, UserSessionModel userSession,
            ClientSessionContext clientCtx) {
        // ID Token中只放简单字段，不放完整部门树以减少体积
        UserModel user = userSession.getUser();
        String deptId = user.getFirstAttribute("department_id");
        if (deptId != null) {
            token.getOtherClaims().put("department_id", deptId);
        }
        return token;
    }

    @Override
    public AccessToken transformUserInfoAccessToken(AccessToken token,
            ProtocolMapperModel mappingModel, KeycloakSession session,
            UserSessionModel userSession, ClientSessionContext clientCtx) {
        // UserInfo端点复用Access Token的完整数据
        return transformAccessToken(token, mappingModel, session, userSession, clientCtx);
    }

    private List<Map<String, String>> getDepartmentPath(String deptId) {
        List<Map<String, String>> path = new ArrayList<>();
        DepartmentNode node = ORG_TREE.get(deptId);
        while (node != null) {
            Map<String, String> item = new LinkedHashMap<>();
            item.put("id", node.id);
            item.put("name", node.name);
            item.put("type", node.type);
            path.add(0, item);
            node = node.parentId != null ? ORG_TREE.get(node.parentId) : null;
        }
        return path;
    }

    static class DepartmentNode {
        String id, name, type, parentId;
        DepartmentNode(String id, String name, String type, String parentId) {
            this.id = id; this.name = name; this.type = type; this.parentId = parentId;
        }
    }

    @Override
    public String getDisplayType() {
        return "部门树映射器";
    }

    @Override
    public String getId() {
        return PROVIDER_ID;
    }

    @Override
    public String getHelpText() {
        return "将用户的部门组织树（4级嵌套结构）注入Token Claims。"
                + "Access Token含完整部门树JSON，ID Token仅含department_id字段。";
    }
}
```

---

### 步骤2：实现ProtocolMapper Factory

**目标**：创建Mapper的工厂类，声明可配置属性（哪个Token类型、Claim名称），完成SPI注册。

```java
package com.mycompany.keycloak.mapper;

import org.keycloak.Config;
import org.keycloak.models.KeycloakSession;
import org.keycloak.models.KeycloakSessionFactory;
import org.keycloak.protocol.ProtocolMapper;
import org.keycloak.protocol.ProtocolMapperModel;
import org.keycloak.protocol.oidc.mappers.AbstractOIDCProtocolMapperFactory;
import org.keycloak.provider.ProviderConfigProperty;

import java.util.ArrayList;
import java.util.List;

public class DepartmentTreeProtocolMapperFactory
        extends AbstractOIDCProtocolMapperFactory {

    public static final String PROVIDER_ID = "department-tree-mapper";

    private static final List<ProviderConfigProperty> CONFIG_PROPERTIES =
            new ArrayList<>();

    static {
        ProviderConfigProperty accessTokenClaim = new ProviderConfigProperty();
        accessTokenClaim.setName("access.token.claim");
        accessTokenClaim.setLabel("添加到Access Token");
        accessTokenClaim.setHelpText("是否将部门树注入Access Token。选中时包含完整部门树JSON。");
        accessTokenClaim.setType(ProviderConfigProperty.BOOLEAN_TYPE);
        accessTokenClaim.setDefaultValue("true");
        CONFIG_PROPERTIES.add(accessTokenClaim);

        ProviderConfigProperty idTokenClaim = new ProviderConfigProperty();
        idTokenClaim.setName("id.token.claim");
        idTokenClaim.setLabel("添加到ID Token");
        idTokenClaim.setHelpText("是否将部门ID注入ID Token。注意：ID Token中只放简单字段。");
        idTokenClaim.setType(ProviderConfigProperty.BOOLEAN_TYPE);
        idTokenClaim.setDefaultValue("true");
        CONFIG_PROPERTIES.add(idTokenClaim);

        ProviderConfigProperty userInfoClaim = new ProviderConfigProperty();
        userInfoClaim.setName("userinfo.token.claim");
        userInfoClaim.setLabel("添加到UserInfo");
        userInfoClaim.setHelpText("是否在UserInfo端点的响应中包含部门树。");
        userInfoClaim.setType(ProviderConfigProperty.BOOLEAN_TYPE);
        userInfoClaim.setDefaultValue("false");
        CONFIG_PROPERTIES.add(userInfoClaim);

        ProviderConfigProperty claimName = new ProviderConfigProperty();
        claimName.setName("claim.name");
        claimName.setLabel("Claim Name");
        claimName.setHelpText("部门树Claim在Token中的键名，默认为department_tree。");
        claimName.setType(ProviderConfigProperty.STRING_TYPE);
        claimName.setDefaultValue("department_tree");
        CONFIG_PROPERTIES.add(claimName);
    }

    @Override
    public ProtocolMapper create(KeycloakSession session) {
        return new DepartmentTreeProtocolMapper();
    }

    @Override
    public String getDisplayType() {
        return "部门树映射器";
    }

    @Override
    public String getId() {
        return PROVIDER_ID;
    }

    @Override
    public String getHelpText() {
        return "将用户的部门组织树（4级嵌套：公司→事业部→部门→组）注入Token Claims。";
    }

    @Override
    public List<ProviderConfigProperty> getConfigProperties() {
        return CONFIG_PROPERTIES;
    }
}
```

---

### 步骤3：SPI注册

在`src/main/resources/META-INF/services/`目录下创建文件`org.keycloak.protocol.ProtocolMapper`，内容为：

```
com.mycompany.keycloak.mapper.DepartmentTreeProtocolMapperFactory
```

**注意**：文件名是SPI接口的全限定类名——`org.keycloak.protocol.ProtocolMapper`——而非`org.keycloak.protocol.oidc.mappers.AbstractOIDCProtocolMapper`。Keycloak在启动时扫描`META-INF/services/`目录下所有以SPI接口名命名的文件，读取其中声明的Factory实现类全限定名并完成注册。文件里每一行一个Factory类名。

完整的Maven项目结构：

```
department-tree-mapper/
├── pom.xml
└── src/main/
    ├── java/com/mycompany/keycloak/mapper/
    │   ├── DepartmentTreeProtocolMapper.java
    │   └── DepartmentTreeProtocolMapperFactory.java
    └── resources/META-INF/services/
        └── org.keycloak.protocol.ProtocolMapper
```

编译并部署：

```bash
mvn clean package -DskipTests
cp target/department-tree-mapper-1.0.jar $KEYCLOAK_HOME/providers/
$KEYCLOAK_HOME/bin/kc.sh build
$KEYCLOAK_HOME/bin/kc.sh start-dev
```

---

### 步骤4：创建自定义Client Scope并绑定Mapper

**目标**：通过Admin Console创建`department-info` Scope，使部门树Mapper可按客户端分配。

1. 登录Keycloak Admin Console → **Client Scopes** → **Create client scope**
2. Name: `department-info`，Protocol: `openid-connect`，Type: Optional
3. 进入`department-info` → **Mappers** → **Add mapper** → **By configuration**
4. 在下拉框中找到并选择"部门树映射器"
5. 配置参数：`access.token.claim`=ON, `id.token.claim`=ON, `userinfo.token.claim`=OFF, `claim.name`=`department_tree`
6. 点击Save

---

### 步骤5：将Client Scope分配给客户端

1. 进入 **Clients** → 选择目标客户端（如`oms-backend`）→ **Client Scopes**
2. 在Optional Client Scopes区域找到`department-info` → 点击 **Add selected**
3. 同样操作为`oms-frontend`添加该Scope（设为Default或Optional取决于前端是否需要部门树）

---

### 步骤6：验证Token中的Claims

**目标**：通过curl获取Token并解析其中的部门树JSON。

```bash
# 确保测试用户zhangsan设置了department_id属性
# Admin Console → Users → zhangsan → Attributes → 
#   Key: department_id → Value: grp001 → Save

# 获取Token（显式请求department-info scope）
TOKEN=$(curl -s -X POST \
  http://localhost:8080/realms/demo-realm/protocol/openid-connect/token \
  -d "client_id=oms-backend" \
  -d "client_secret=<your-client-secret>" \
  -d "username=zhangsan" \
  -d "password=test123" \
  -d "grant_type=password" \
  -d "scope=openid department-info" | jq -r '.access_token')

# 解析JWT Payload查看部门树
echo "$TOKEN" | cut -d'.' -f2 | base64 -d 2>/dev/null | jq '.department_tree'
```

运行结果（预期JSON输出）：

```json
{
  "department_tree": [
    {"id": "root", "name": "公司", "type": "company"},
    {"id": "div001", "name": "研发事业部", "type": "division"},
    {"id": "dept001", "name": "基础平台部", "type": "department"},
    {"id": "grp001", "name": "身份认证组", "type": "group"}
  ],
  "department_id": "grp001",
  "department_name": "身份认证组"
}
```

如果客户端在Token请求中未包含`department-info` scope，则Access Token中不包含`department_tree`字段——验证了按客户端定制Claims的机制。

---

### 步骤7：验证Token体积控制

```bash
# 对比添加Mapper前后的Token大小
echo "$TOKEN" | wc -c

# 确保Token大小在安全范围内（通常2-4KB，远低于8KB限制）

# 检查Header大小（模拟nginx限制检查）
TOTAL_HEADER_SIZE=$(echo -n "Authorization: Bearer $TOKEN" | wc -c)
echo "Authorization Header size: $TOTAL_HEADER_SIZE bytes"
```

运行结果：Token大小约2-3KB，Authorization Header总大小约3-4KB，在Nginx默认的8KB Header限制内安全。

---

### 可能遇到的坑

1. **组织架构数据变更后Token中的缓存数据不会自动刷新**。Token是"签发时的快照"——如果张三在上午10:00获取了Token（此时属于"身份认证组"），10:15被调岗到"网关组"，他在10:00-10:15之间签发的Token仍然带旧部门树。解决方案：设定合理的Token有效期（建议15-30分钟），或配合Token Revocation机制。如果需要即时生效，考虑将Token有效期设为极短（5分钟），配合Refresh Token轮换——但这会显著增加Token端点的负载。

2. **循环调用组织架构服务获取部门树可能阻塞认证线程**。生产环境的组织架构服务通常是远程RPC调用，从`getDepartmentPath()`中的while循环每次都有网络IO。如果部门树深度10层、每层查询100ms，10层就是1秒——用户等1秒才拿到Token是不接受的。解决方案：在Mapper内部使用本地缓存（Caffeine/Guava Cache），或Keycloak启动时将整个组织架构树加载到内存（适用于组织架构变更不频繁的场景），或使用异步查询（但当前AccessToken的`transformAccessToken`是同步方法）。

3. **Claim名称冲突**——自定义Claim与标准OIDC Claim重名。OIDC标准保留了大量Claim名称（如`sub`、`name`、`email`、`preferred_username`等），自定义Claim应使用带命名空间的名称（如`mycompany_department_tree`或`urn:mycompany:claims:department_tree`），避免覆盖标准Claim。

4. **大部门树JSON的序列化性能**。`LinkedHashMap`嵌套的JSON序列化由Jackson完成，对于4级深度、每级数十字节的数据，开销在微秒级，可忽略。但如果节点属性达到数十个且用户量很大，建议使用`writeValueAsString()`预序列化后直接注入字符串类型的Claim，避免对象图的深度遍历。

---

### 测试验证

**测试1：不同部门用户的Token差异**

```bash
# 用户zhangsan (department_id=grp001, 身份认证组)
curl -s -X POST ... -d "username=zhangsan" -d "password=test123" \
  -d "scope=openid department-info" | jq '.access_token' | cut -d'.' -f2 | base64 -d | jq '.department_name'
# 预期：身份认证组

# 用户lisi (department_id=grp003, 订单组)
curl -s -X POST ... -d "username=lisi" -d "password=test123" \
  -d "scope=openid department-info" | jq '.access_token' | cut -d'.' -f2 | base64 -d | jq '.department_name'
# 预期：订单组
```

**测试2：ID Token vs Access Token的Claims差异**

```bash
# 获取ID Token
curl -s -X POST ... -d "scope=openid department-info" | jq '.id_token' | cut -d'.' -f2 | base64 -d | jq 'keys | .[] | select(startswith("dept"))'
# 预期：仅包含department_id，不包含department_tree
```

**测试3：不带scope的Token是否不含部门树**

```bash
curl -s -X POST ... -d "scope=openid" | jq '.access_token' | cut -d'.' -f2 | base64 -d | jq '.department_tree'
# 预期：null（Token中无department_tree字段）
```

---

## 4 项目总结

### 优点与缺点对比

| 维度 | 自定义Java Mapper（本章） | Script Mapper（已废弃） | 微服务端查Admin API |
|------|------------------------|----------------------|-------------------|
| 安全性 | ✅ 编译为字节码，须走CI/CD | ❌ JS可调用Java反射执行任意代码 | ✅ API有独立鉴权 |
| Token体积 | ⚠️ 数据内嵌，体积随数据增长 | ⚠️ 同左 | ✅ Token只含基本Claims |
| 网络延迟 | ✅ 零额外RPC，Token即数据 | ✅ 同左 | ❌ 每次请求多2次RPC(+50-80ms) |
| 数据实时性 | ⚠️ Token有效期内的数据快照 | ⚠️ 同左 | ✅ 实时查询最新数据 |
| 变更可追溯 | ✅ Git管理，CI/CD审计 | ❌ DB中的脚本无版本控制 | ✅ 服务代码Git管理 |
| 运维复杂度 | ⚠️ 需重新编译部署JAR | ⚠️ Admin Console直接修改脚本 | ✅ 服务自行管理 |
| 资源消耗 | ✅ 认证时一次性计算 | ❌ 每次Token签发初始化JS引擎 | ❌ 每次请求额外网络IO |

### 适用场景

1. **业务信息注入Token**：当微服务需要用户的组织架构、员工等级、数据分区等业务属性来做路由或权限过滤时，注入到Token中免去了每个服务单独查询的开销。
2. **微服务零查询认证**：对于Serverless或边缘计算场景，微服务可能没有访问Keycloak Admin API或Redis的网络路径，自包含的Token是唯一可行的方案。
3. **前端避免多次API调用**：前端SPA可以在解析ID Token时直接拿到用户所属部门、角色等业务信息，避免加载页面后再发多次API请求获取这些数据。
4. **异构系统集成**：当第三方系统需要从Token中读取特定业务字段（如SaaS平台通过Token中的租户ID路由到正确数据分区），标准的OIDC Claims不够用，自定义Mapper提供灵活的扩展点。

### 不适用场景

1. **业务数据频繁变更**：如果部门信息每小时变更多次，Token快照意义上的数据可能很快过时，建议使用引用Token+实时查询方案。
2. **Token体积敏感的超高并发场景**：如IoT设备认证——每个设备的Token如果带几百KB的业务数据，在百万设备并发时会显著增加网络带宽消耗。

### 注意事项

- **Token体积不可无限增长**：每个Mapper的注入是累加的。一个用户同时有`department_tree`(2KB)、`role_list`(1KB)、`group_membership`(500B)、自定义属性(200B)，Token很容易超过8KB Header限制。建议定期审查所有已绑定的Mapper，移除不再需要的映射。
- **敏感信息不要放入Token**：手机号、身份证号、银行卡号等个人信息在JWT中仅经过Base64编码——不是加密，任何人截获Token后都能解码查看。如果必须传递敏感信息，使用JWE（JSON Web Encryption）对Token做二次加密，或采用Phantom Token模式，通过内网introspect接口安全返回敏感Claims。
- **缓存一致性**：Mapper内部如果使用了本地缓存（如Caffeine），需要注意缓存刷新策略与Token有效期的匹配——缓存在Token有效期内的数据不一致是可接受的，但缓存时间不应超过Token有效期。
- **`transformAccessToken`是同步方法**：不要在方法内做长时间阻塞的网络IO。如果需要调用外部服务，务必使用超时控制和断路保护，否则一次外部服务故障会拖垮整个Token端点。

### 常见踩坑经验

1. **问题**：自定义Mapper在Admin Console的"Add Mapper"下拉框中看不到。**根因**：SPI注册文件`META-INF/services/org.keycloak.protocol.ProtocolMapper`未创建或文件名拼写错误（常见错误：文件名写成`org.keycloak.protocol.oidc.mappers.OIDCAccessTokenMapper`）。**解决**：检查Keycloak启动日志中的`SPI: org.keycloak.protocol.ProtocolMapper`行，确认Factory已被加载。

2. **问题**：部门树Claim出现在Token中但值为`null`。**根因**：用户在`transformAccessToken`中直接修改`token.getOtherClaims()`时，如果`department_id`属性为空，仍调用了`put("department_tree", null)`——JWT序列化时Jackson默认会输出`null`值。**解决**：在注入Claim之前做null检查，仅当值非空时才put。

3. **问题**：ID Token中出现了不应有的完整部门树。**根因**：只实现了`OIDCAccessTokenMapper`而没实现`OIDCIDTokenMapper`——但`transformAccessToken`的逻辑意外被ID Token的生成流程调用了（在某些Keycloak版本中存在这样的行为）。**解决**：明确分别实现`OIDCAccessTokenMapper`和`OIDCIDTokenMapper`接口，各自提供差异化的`transformAccessToken`和`transformIDToken`方法。

### 思考题

1. **部门树深度10层+每层20属性，Token超20KB如何优化？** 一种方案是"轻量Token + 共享缓存"——Token中只放`department_id`，微服务通过Redis查询完整部门树。请对比这种方案与本章的"Token自包含"方案在以下维度的优劣：网络延迟（自包含0ms vs Redis查询1-2ms）、数据一致性（自包含快照 vs Redis实时）、基础设施依赖（自包含零依赖 vs 需Redis）、故障容错（Redis宕机时微服务是否还能做权限判断？）。如果选择折中的Phantom Token方案，API Gateway应如何设计Token替换的流程？

2. **多级权限过滤的组合优化**：如果一个微服务不仅需要部门树，还需要用户的"数据分区"（按customer_id分片）、"敏感等级"（普通/机密/绝密）、"可访问的功能模块列表"（通过RBAC角色推导出的菜单权限），共4组业务数据。这些数据是分别注入4个独立的Claim（每个Claim 1-2KB，总计4-8KB），还是聚合为一个`business_context` JSON对象（可能3-5KB）？请从客户端解析便利性、Schema演进独立性、Token体积压缩效率三个维度分析两种方案的优劣。

