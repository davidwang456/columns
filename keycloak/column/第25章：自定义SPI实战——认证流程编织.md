# 第25章：自定义SPI实战——认证流程编织

## 1 项目背景

某跨国企业的安全部门在一次季度审计后下达了一份"认证体系升级令"：公司近年来经历了多次并购，业务线分散，各系统认证方式参差不齐——有的只有密码登录，有的加了个短信验证码，有的干脆完全信任内网IP不做任何认证。安全团队基于零信任理念设计了一套"自适应认证"策略，核心逻辑是：来自公司内网IP段（10.0.0.0/8、172.16.0.0/12、192.168.0.0/16）的用户只需密码登录即可；VPN或外网用户必须通过密码+OTP双因素认证；首次登录或从未知设备登录的用户强制要求邮箱验证码；来自高风险国家IP的登录请求直接拒绝，连密码都不允许尝试。

安全部门的策略文档写得逻辑严密——可以画成一张漂亮的决策树流程图。但开发团队拿到后集体陷入了沉默：这些"if-else"写在哪里？

最早的做法是在应用层硬编码——在登录接口中获取用户IP，判断是否内网，是则直接放行，否则继续判断是否高风险国家，然后判断是否新设备……不到两个月，代码就膨胀到不可维护。安全部门每新增一条规则（比如"连续出差30天的员工登录时也需要OTP"），开发团队就要修改登录逻辑、重新测试、重新发布。更糟糕的是，公司有七个业务系统，每个系统都各自实现了一套相似的判断逻辑，规则之间的差异已经开始导致安全漏洞——A系统忘了加高风险国家检测，攻击者恰好从这条通道进来了。

更深层的问题在于，团队对Keycloak的认证流机制理解不够：Authentication Flow的执行顺序是如何决定的？Conditional、Required、Alternative、Disabled四种Requirement各自的决策规则是什么？为什么密码认证通过后才触发OTP，而不是两者同时弹窗？自定义Authenticator需要理解AuthenticationProcessor这个执行引擎的哪些内部机制？

这些问题指向同一个答案：需要将认证流程从业务代码抽离到Keycloak的SPI层，利用Browser Flow的层级编排能力，将安全策略转化为一组可配置、可扩展、可独立测试的Authenticator组件。

---

## 2 项目设计——剧本式交锋对话

**小胖**（刚从机场出差回来，行李箱还没放好）：大师，我这次出差过安检的时候突然想明白了咱们那个自适应认证的事儿。你看机场安检——所有人先过金属探测门（这就跟密码认证一样，是必过的第一关），如果探测门响了，你就得站到一边让安检员用手持扫描仪再扫一遍（这就是OTP，第二因素）。然后如果系统发现你来自高风险国家，从你一下飞机就被地勤带到小黑屋单独检查了（直接拒绝）。这不就和咱们的需求一模一样吗？但我有一个问题——为什么不直接让每个旅客把所有检查一次做完？密码+OTP+邮箱验证码全来一遍多安全？

**大师**：你这个机场比喻非常精准。回答你的问题：如果每个旅客都做全套检查，浦东机场T2航站楼早上八点的队伍能排到停车场。安全不是越严越好，而是在"可接受的风险水平"下最大化用户体验。你进公司大门刷卡就能进，但进数据中心机房要刷卡+指纹+虹膜——因为资产价值不同，安全投入也不同。

Keycloak的Authentication Flow做的正是这件事——通过Execution的四种Requirement来控制"安检强度"。你过金属探测门是Required（必须通过），门响了才触发手检是Conditional（条件触发），选择走普通通道还是VIP通道是Alternative（任选其一），你今天腿脚不便跳过探测门直接手检是Disabled（暂不启用）。

**技术映射**：机场安检的分级检查 = Keycloak Authentication Flow的Conditional + Required组合机制——不同场景触发不同认证强度，而不是一刀切的全套检查。

---

**小白**（在笔记本上画了一棵Flow树）：我想把这四种Requirement的决策规则彻底搞清楚。Required我理解——必须成功，失败了整个流就失败。Alternative我也理解——多个之中只要有一个成功就行。但Conditional到底怎么实现"if-else"？我看到Keycloak还有一个`ConditionalAuthenticator`接口，它和普通的`Authenticator`有什么区别？还有，子Flow的递归执行机制是怎么工作的——AuthenticationProcessor是深度优先遍历这棵树吗？

**大师**：好，这四个问题串起来就是AuthenticationProcessor的核心逻辑。我画一张流程图你就明白了。

AuthenticationProcessor的执行引擎从顶层Flow的第一个Execution开始，按顺序遍历Execution列表。对于每个Execution，先看它的Requirement：

- **Required**：执行该Authenticator。如果返回`success()`，继续下一个Execution；如果返回`failure()`，整个Flow立即终止并标记为失败；如果返回`attempted()`（表示"我做了但没完成，需要用户交互"），Flow暂停，等待用户提交表单或完成操作后再恢复。
- **Alternative**：执行该Authenticator。如果返回`success()`，当前Flow标记为成功，跳过后面的所有Alternative。如果返回`failure()`或`attempted()`，继续尝试下一个Alternative。也就是说，Alternative之间是"或"的关系——有一个成功就够。
- **Disabled**：直接跳过，不执行。
- **Conditional**：这里是关键。Conditional Execution本身不执行Authenticator，而是执行一个**子Flow**。执行子Flow之前，先调用`ConditionalAuthenticator.matchCondition()`方法。如果返回`true`，子Flow被当作Required执行（子Flow内的所有Required必须通过）；如果返回`false`，子Flow被整体跳过——这就是"if-else"的实现。
- **Optional**（Keycloak 25+新增）：类似Required，但失败不中断整个Flow。

ConditionalAuthenticator是一个继承了Authenticator的子接口，它多了一个`matchCondition()`方法。普通的Authenticator的`authenticate()`方法是"执行认证逻辑"，而ConditionalAuthenticator的`matchCondition()`是"评估是否需要执行这个条件分支"。它本身也继承`authenticate()`，但通常Conditional的实现是在`authenticate()`里直接调`context.success()`——因为真正的判断已经在`matchCondition()`里完成了。

关于子Flow的递归：当AuthenticationProcessor遇到一个Conditional Execution，它判断`matchCondition()`为true后，会进入这个子Flow的Execution列表，对该子列表递归执行同样的遍历逻辑。子Flow内的所有Execution处理完后（All Required Passed），才会回到父Flow继续下一个Execution。这确实是深度优先遍历——一个子Flow没有全部完成之前，不会回到父级。

**技术映射**：ConditionalAuthenticator的matchCondition = 机场安检员扫一眼你的登机牌——经济舱走普通通道，两舱走快速通道，在通道入口就决定分流了。

---

**小胖**：那我不理解了，如果一个Flow里面的Required Authenticator一直不返回success也不返回failure，就是一直在等用户输入，用户也一直不输入——这个Flow会不会永远卡住？还有那个`action()`方法是干嘛的？

**大师**：问到点儿上了。`authenticate()`和`action()`的区分正是理解认证流交互模型的关键。

`authenticate()`是首次进入该Authenticator时触发。比如"用户名密码表单"Authenticator，在`authenticate()`里生成一个登录表单的HTML页面返回给浏览器——此时调的是`context.challenge(loginForm)`。Challenge的意思是"我需要用户给我一些信息才能继续"。调用Challenge之后，该Authenticator进入"等待状态"，整个Flow暂停，浏览器显示登录表单。

用户填好表单点击提交后，HTTP POST请求回到Keycloak。AuthenticationProcessor根据session中的状态找到之前暂停的那个Authenticator，调用它的`action()`方法。`action()`里获取表单数据，验证用户名密码。如果验证通过，调用`context.success()`，Flow继续下一个Execution。如果验证失败，调用`context.failure()`或再次`context.challenge(errorForm)`让用户重试。

所以`authenticate()` = "生成质询"，`action()` = "处理响应"。一个Authenticator可以多次经历action()调用（用户反复输错密码，每次点击提交都触发一次action），直到sucess或failure。

关于"卡住"的问题：AuthenticationFlowException就是处理这个的。如果你在`authenticate()`或`action()`里抛出一个`AuthenticationFlowException`，可以指定一个错误URL——浏览器会跳转到该URL并附带错误参数，由前端页面展示错误信息。Keycloak对每个Flow也有超时机制——AuthenticationSession有TTL，超时后自动过期。

**技术映射**：Challenge = 机场安检员递给你一个托盘说"请把笔记本电脑单独拿出来"；Action = 你按要求放好了笔记本，安检员扫一眼通过让你过去。

---

**小白**：我还有一个架构层面的问题。我看文档说可以在Flow里嵌套SubFlow，SubFlow里再嵌套SubFlow——这最多能嵌套多少层？如果我把几十条认证规则全用Conditional串起来，会不会因为层级太深导致性能问题？还有，自定义Authenticator开发的时候，Factory的`create()`方法是每次认证请求都调用一次，还是在启动时缓存单例？

**大师**：先回答Factory的生命周期问题——Factory本身是单例，在Keycloak启动时通过SPI机制加载一次。但`create()`方法每次认证会话都会调用一次，创建该Authenticator的一个新实例。所以你的Authenticator实现类应该是**无状态**的——所有需要跨请求保持的状态都应该通过`AuthenticationFlowContext`附带的`AuthenticationSession`来存取，比如用`context.getAuthenticationSession().setAuthNote("geo_risk", "high")`存一个标记，在后续的Authenticator中通过`context.getAuthenticationSession().getAuthNote("geo_risk")`读取。

嵌套深度方面，Keycloak没有硬性上限，但Authentication Processor在执行时会维护一个递归调用栈。实际建议不超过4-5层——不是内部限制，而是可维护性边界。每增加一层嵌套，调试时追踪认证路径的复杂度成倍增长。如果真的遇到50+条条件规则，解决方案不是把它们全部塞进一个Browser Flow，而是拆分为多个独立的Flow（比如"内网登录Flow"和"外网登录Flow"），在更上层做条件分流。

关于调试方法：第一，Keycloak支持详细日志——在`standalone.xml`或`keycloak.conf`中将日志级别设置为`DEBUG`，尤其对`org.keycloak.authentication`包。第二，你可以在自定义Authenticator中通过`context.getSession().getLogger()`打日志。第三，Admin Console内置了Authentication的测试工具——在Realm → Authentication → Flows中选择一个Flow，点击"Action"→"Bind flow"，然后去Client中绑定该Flow，最后用测试用户实际登录，通过日志追踪完整的执行路径。第四，如果本地开发，可以在IDE中直接断点调试Keycloak源码——`AuthenticationProcessor.authenticateOnly()`和`AuthenticationProcessor.evaluateRecursiveRequirement()`是两个关键断点位置。

**大师总结技术映射**：

| 生活比喻 | 技术映射 |
|---------|---------|
| 机场安检：过金属探测门→响了手检→高风险带小黑屋 | Flow执行链：Required→Conditional→SubFlow→结果 |
| 安检员看登机牌决定去哪个通道 | ConditionAuthenticator的matchCondition：条件分流 |
| 安检员递托盘让你放笔记本 | context.challenge()：生成质询等待用户交互 |
| 你放好笔记本安检员确认通过 | context.action()：处理用户响应 |
| 飞机等齐所有乘客才关门 | SubFlow的Required全部成功父Flow才继续 |
| 安检电脑记录了你的旅客画像 | AuthenticationSession.setAuthNote()：跨Authenticator共享状态 |

---

## 3 项目实战

### 环境准备

| 组件 | 版本/说明 |
|------|----------|
| Keycloak | 26.x，基于第23-24章的SPI开发环境 |
| JDK | 17+，Maven 3.8+ |
| IDE | IntelliJ IDEA（推荐，支持断点调试Keycloak源码） |
| Realm | demo-realm（已创建，参考第2章） |
| 测试用户 | zhangsan / password123，已配置OTP（用于场景2验证） |

确保前续章节搭建的SPI模块工程结构可用——`src/main/java`下放置Java源码，`src/main/resources/META-INF/services`下放置SPI注册文件，`pom.xml`引入`keycloak-server-spi`和`keycloak-server-spi-private`依赖。

### 步骤1：创建"IP白名单"Authenticator——内网免第二因素

**目标**：判断用户IP是否在企业内网范围内，内网用户直接通过，外网用户则继续执行后续Authenticator。

```java
package com.example.adaptiveauth.authenticator;

import org.keycloak.authentication.AuthenticationFlowContext;
import org.keycloak.authentication.Authenticator;
import org.keycloak.models.KeycloakSession;
import org.keycloak.models.RealmModel;
import org.keycloak.models.UserModel;

import java.util.List;

public class IPWhitelistAuthenticator implements Authenticator {

    private static final List<String> INTERNAL_CIDRS = List.of(
        "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"
    );

    @Override
    public void authenticate(AuthenticationFlowContext context) {
        String clientIp = context.getSession().getContext()
                .getConnection().getRemoteAddr();

        if (isInternalIp(clientIp)) {
            // 内网IP：标记跳过第二因素，直接成功
            context.getAuthenticationSession()
                    .setAuthNote("ip_whitelist_passed", "true");
            context.success();
        } else {
            // 外网IP：不标记跳过，让后续Authenticator正常执行
            context.getAuthenticationSession()
                    .setAuthNote("ip_whitelist_passed", "false");
            context.attempted();
        }
    }

    private boolean isInternalIp(String ip) {
        if (ip == null || ip.isEmpty()) return false;
        return ip.startsWith("10.")
                || ip.startsWith("172.16.")  // 简化判断172.16.0.0/12范围
                || ip.startsWith("192.168.");
    }

    @Override
    public void action(AuthenticationFlowContext context) {
        // 此Authenticator不涉及用户交互，不实现
    }

    @Override
    public boolean requiresUser() {
        return false; // IP检测在用户识别之前即可执行
    }

    @Override
    public boolean configuredFor(KeycloakSession session,
                                  RealmModel realm, UserModel user) {
        return true; // 不需要用户预先配置
    }

    @Override
    public void setRequiredActions(KeycloakSession session,
                                    RealmModel realm, UserModel user) {
        // 此Authenticator不设置Required Action
    }

    @Override
    public void close() {}
}
```

**设计要点**：`requiresUser()`返回`false`，意味着这个Authenticator可以在用户身份识别之前就执行——IP检测根本不需要知道用户是谁。`context.attempted()`表示"我已执行但未完成（也没失败），请继续执行下一个Authenticator"。

### 步骤2：创建"登录设备指纹"Conditional Authenticator

**目标**：判断当前设备是否为用户的已知设备，新设备需要额外验证（邮箱验证码）。

```java
package com.example.adaptiveauth.authenticator;

import org.keycloak.authentication.AuthenticationFlowContext;
import org.keycloak.authentication.authenticators.conditional.ConditionalAuthenticator;
import org.keycloak.models.KeycloakSession;
import org.keycloak.models.RealmModel;
import org.keycloak.models.UserModel;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;

public class NewDeviceConditionalAuthenticator
        implements ConditionalAuthenticator {

    @Override
    public boolean matchCondition(AuthenticationFlowContext context) {
        UserModel user = context.getUser();
        if (user == null) return false;

        String deviceFingerprint = calculateFingerprint(context);
        String knownDevices = user.getFirstAttribute("known_devices");

        if (knownDevices == null || !knownDevices.contains(deviceFingerprint)) {
            // 新设备：返回true触发SubFlow（要求额外验证）
            context.getAuthenticationSession()
                    .setAuthNote("new_device_detected", "true");
            return true;
        }
        // 已知设备：返回false跳过SubFlow
        context.getAuthenticationSession()
                .setAuthNote("new_device_detected", "false");
        return false;
    }

    @Override
    public void authenticate(AuthenticationFlowContext context) {
        context.success();
    }

    private String calculateFingerprint(AuthenticationFlowContext context) {
        String userAgent = context.getSession().getContext()
                .getRequestHeaders().getHeaderString("User-Agent");
        String ip = context.getSession().getContext()
                .getConnection().getRemoteAddr();
        String raw = userAgent + ":" + ip;
        return sha256Hex(raw);
    }

    private String sha256Hex(String input) {
        try {
            MessageDigest md = MessageDigest.getInstance("SHA-256");
            byte[] hash = md.digest(input.getBytes(StandardCharsets.UTF_8));
            StringBuilder hexString = new StringBuilder();
            for (byte b : hash) {
                String hex = Integer.toHexString(0xff & b);
                if (hex.length() == 1) hexString.append('0');
                hexString.append(hex);
            }
            return hexString.toString();
        } catch (NoSuchAlgorithmException e) {
            throw new RuntimeException(e);
        }
    }

    @Override
    public void action(AuthenticationFlowContext context) {}

    @Override
    public boolean requiresUser() {
        return true; // 需要知道用户才能查已知设备列表
    }

    @Override
    public boolean configuredFor(KeycloakSession session,
                                  RealmModel realm, UserModel user) {
        return true;
    }

    @Override
    public void setRequiredActions(KeycloakSession session,
                                    RealmModel realm, UserModel user) {}

    @Override
    public void close() {}
}
```

**核心逻辑**：`matchCondition()`返回true时，AuthenticationProcessor会执行该Conditional Execution下面的子Flow；返回false时子Flow被跳过。`calculateFingerprint()`使用User-Agent和IP的SHA-256哈希作为简易设备指纹——生产环境可以扩展为浏览器指纹（Canvas指纹、WebGL指纹等）。

### 步骤3：创建"地理位置风险检测"Authenticator

**目标**：检测用户登录IP的地理位置，高风险国家直接拒绝，异地登录标记风险。

```java
package com.example.adaptiveauth.authenticator;

import org.keycloak.authentication.AuthenticationFlowContext;
import org.keycloak.authentication.AuthenticationFlowError;
import org.keycloak.authentication.Authenticator;
import org.keycloak.models.KeycloakSession;
import org.keycloak.models.RealmModel;
import org.keycloak.models.UserModel;

import java.util.List;

public class GeoRiskAuthenticator implements Authenticator {

    private static final List<String> HIGH_RISK_COUNTRIES =
            List.of("KP", "IR", "SY"); // 高风险国家ISO代码

    @Override
    public void authenticate(AuthenticationFlowContext context) {
        String clientIp = context.getSession().getContext()
                .getConnection().getRemoteAddr();

        // 调用GeoIP服务解析IP归属地（此处为接口示意）
        GeoLocation location = lookupGeoLocation(clientIp);

        if (location != null
                && HIGH_RISK_COUNTRIES.contains(location.getCountryCode())) {
            // 高风险地区：直接拒绝，不泄露任何提示信息
            context.failure(AuthenticationFlowError.ACCESS_DENIED);
            return;
        }

        // 异地登录检测
        UserModel user = context.getUser();
        if (user != null && location != null) {
            String usualCity = user.getFirstAttribute("usual_login_city");
            if (usualCity != null
                    && !usualCity.equals(location.getCity())
                    && !isInternalIp(clientIp)) {
                context.getAuthenticationSession()
                        .setAuthNote("geo_risk", "high");
            }
        }

        context.success();
    }

    // 模拟GeoIP查询——生产环境替换为MaxMind GeoIP2或ip2region
    private GeoLocation lookupGeoLocation(String ip) {
        // 生产环境实现：
        // DatabaseReader reader = new DatabaseReader.Builder(dbFile).build();
        // CityResponse response = reader.city(InetAddress.getByName(ip));
        // return new GeoLocation(response.getCountry().getIsoCode(),
        //                        response.getCity().getName());
        return null; // 简化：demo中返回null跳过Geo检查
    }

    private boolean isInternalIp(String ip) {
        return ip.startsWith("10.")
                || ip.startsWith("172.16.")
                || ip.startsWith("192.168.");
    }

    @Override
    public void action(AuthenticationFlowContext context) {}

    @Override
    public boolean requiresUser() {
        return true; // 异地检测需要用户常用城市信息
    }

    @Override
    public boolean configuredFor(KeycloakSession session,
                                  RealmModel realm, UserModel user) {
        return true;
    }

    @Override
    public void setRequiredActions(KeycloakSession session,
                                    RealmModel realm, UserModel user) {}

    @Override
    public void close() {}

    // 内部类：GeoIP查询结果
    private static class GeoLocation {
        private String countryCode;
        private String city;

        public GeoLocation(String countryCode, String city) {
            this.countryCode = countryCode;
            this.city = city;
        }
        public String getCountryCode() { return countryCode; }
        public String getCity() { return city; }
    }
}
```

**安全设计要点**：高风险国家拒绝时不返回详细错误信息（只返回`ACCESS_DENIED`），防止攻击者通过错误信息差异探测系统的检测规则。异地登录检测仅标记风险但不阻断——后续的Authenticator可以根据`geo_risk`标记决定是否升级认证强度。

### 步骤4：设计自适应认证Browser Flow

在Admin Console中创建自定义Browser Flow（Realm → Authentication → Flows → Create）：

```
Browser Flow - 自适应认证 (adaptive-browser)
├── Cookie [ALTERNATIVE]                         # 已有Session则跳过认证
├── Kerberos [DISABLED]                          # 暂不使用
├── Identity Provider Redirector [ALTERNATIVE]   # 已有Session则跳过
├── ┌─────────────────────────────────────────┐
│   │ SubFlow: 自适应认证表单 (adaptive-forms)   │
│   │                                         │
│   │ Username Password Form [REQUIRED]       │  ← 第一步：密码认证
│   │ GeoRisk检测 [REQUIRED]                  │  ← 第二步：地理位置风险
│   │                                         │
│   │ IP白名单检测 [CONDITIONAL]              │  ← 第三步：内网免OTP
│   │   └── SubFlow: 内网免第二因素           │
│   │       # 空SubFlow——条件满足直接通过     │
│   │                                         │
│   │ 新设备检测 [CONDITIONAL]               │  ← 第四步：新设备额外验证
│   │   └── SubFlow: 新设备额外验证           │
│   │       ├── OTP Form [REQUIRED]           │  ← 新设备需要OTP
│   │       └── 邮箱验证码 [ALTERNATIVE]      │  ← 或者邮箱验证码
│   │                                         │
│   │ TOTP [ALTERNATIVE]                      │  ← 第五步：常规外网OTP
│   └─────────────────────────────────────────┘
```

**Flow设计说明**：

- **Username Password Form [REQUIRED]**：所有用户都要过密码这关——即使内网用户也不例外，这是最基本的身份证明。
- **GeoRisk检测 [REQUIRED]**：在密码验证之后执行（此时用户名已获知，可以查用户属性）。如果IP来自高风险国家，直接失败，密码正确也没用。
- **IP白名单检测 [CONDITIONAL]**：Conditional Authenticator判断是否为内网IP。如果是内网，子Flow（空）执行成功，继续下一个Execution。这里特意将子Flow设为空——因为内网用户不需要任何额外验证。注意：Conditional返回true时子Flow是必须执行的，如果返回false则跳过子Flow。
- **新设备检测 [CONDITIONAL]**：判断设备指纹是否在已知设备列表中。如果是不认识的设备，子Flow被执行，要求OTP或邮箱验证码（ALTERNATIVE关系，二者选其一即可）。
- **TOTP [ALTERNATIVE]**：常规外网用户走到这里。ALTERNATIVE意味着即使没有配置TOTP也只是跳过，不会阻断登录。如果你的策略要求外网强制TOTP，应改为REQUIRED。

**关键设计决策**：为什么IP白名单和新设备检测都用Conditional而不是Alternative？
- Conditional实现了"条件短路"——内网用户直接跳过OTP，而不是"尝试OTP失败后再走Alternative"。如果设为Alternative，内网用户也会被要求OTP（因为没有TOTP配置会失败，才轮到下一个Alternative），这会严重损害用户体验。
- Alternative的语义是"尝试，失败了换下一个"，Conditional的语义是"先判断，不合条件就不进这个门"。

### 步骤5：工厂类注册

每个自定义Authenticator都需要一个Factory类来注册到Keycloak SPI。以IP白名单为例：

```java
package com.example.adaptiveauth.authenticator;

import org.keycloak.Config;
import org.keycloak.authentication.Authenticator;
import org.keycloak.authentication.AuthenticatorFactory;
import org.keycloak.models.AuthenticationExecutionModel;
import org.keycloak.models.KeycloakSession;
import org.keycloak.models.KeycloakSessionFactory;
import org.keycloak.provider.ProviderConfigProperty;
import org.keycloak.provider.ProviderConfigurationBuilder;

import java.util.Collections;
import java.util.List;

public class IPWhitelistAuthenticatorFactory implements AuthenticatorFactory {

    public static final String PROVIDER_ID = "ip-whitelist-authenticator";
    private static final IPWhitelistAuthenticator SINGLETON =
            new IPWhitelistAuthenticator();

    @Override
    public String getDisplayType() {
        return "IP Whitelist (内网免第二因素)";
    }

    @Override
    public String getReferenceCategory() {
        return "authorization";
    }

    @Override
    public boolean isConfigurable() {
        return true; // 允许在Admin Console中配置内网CIDR
    }

    @Override
    public AuthenticationExecutionModel.Requirement[] getRequirementChoices() {
        return new AuthenticationExecutionModel.Requirement[] {
            AuthenticationExecutionModel.Requirement.REQUIRED,
            AuthenticationExecutionModel.Requirement.ALTERNATIVE,
            AuthenticationExecutionModel.Requirement.DISABLED
            // 注意：Conditional Authenticator才返回CONDITIONAL选项
        };
    }

    @Override
    public boolean isUserSetupAllowed() {
        return false;
    }

    @Override
    public List<ProviderConfigProperty> getConfigProperties() {
        return ProviderConfigurationBuilder.create()
                .property()
                    .name("internal_cidrs")
                    .label("内网CIDR列表")
                    .helpText("逗号分隔的内网IP段，如 10.0.0.0/8,172.16.0.0/12")
                    .type(ProviderConfigProperty.STRING_TYPE)
                    .defaultValue("10.0.0.0/8,172.16.0.0/12,192.168.0.0/16")
                    .add()
                .build();
    }

    @Override
    public Authenticator create(KeycloakSession session) {
        return SINGLETON;
    }

    @Override
    public void init(Config.Scope config) {}

    @Override
    public void postInit(KeycloakSessionFactory factory) {}

    @Override
    public void close() {}

    @Override
    public String getId() {
        return PROVIDER_ID;
    }
}
```

`NewDeviceConditionalAuthenticator`的Factory需要额外调整——它的`getRequirementChoices()`需要包含`CONDITIONAL`：

```java
@Override
public AuthenticationExecutionModel.Requirement[] getRequirementChoices() {
    return new AuthenticationExecutionModel.Requirement[] {
        AuthenticationExecutionModel.Requirement.CONDITIONAL,
        AuthenticationExecutionModel.Requirement.REQUIRED,
        AuthenticationExecutionModel.Requirement.DISABLED
    };
}
```

在`src/main/resources/META-INF/services/`下创建SPI注册文件`org.keycloak.authentication.AuthenticatorFactory`，内容为三个Factory的全限定类名：

```
com.example.adaptiveauth.authenticator.IPWhitelistAuthenticatorFactory
com.example.adaptiveauth.authenticator.NewDeviceConditionalAuthenticatorFactory
com.example.adaptiveauth.authenticator.GeoRiskAuthenticatorFactory
```

编译部署后重启Keycloak，在Admin Console的Authentication → Flows中创建Flow时，下拉框应该能看到"IP Whitelist (内网免第二因素)"、"新设备检测 (条件分流)"、"GeoRisk检测 (地理位置风险)"三个选项。

### 步骤6：验证自适应认证流程

**场景1：内网登录（预期跳过OTP）**

```bash
curl -X POST http://localhost:8080/realms/demo-realm/protocol/openid-connect/token \
  -d "client_id=oms-frontend" \
  -d "username=zhangsan" \
  -d "password=password123" \
  -d "grant_type=password" \
  -H "X-Forwarded-For: 10.0.1.100"
# 预期结果：返回access_token，无需OTP流程
# 执行路径：UsernamePassword→GeoRisk检测→IP白名单(条件通过)→新设备检测(判定已知设备则跳过)→TOTP(ALTERNATIVE已跳过)→成功
```

**场景2：外网+已知设备+OTP**

```bash
curl -X POST http://localhost:8080/realms/demo-realm/protocol/openid-connect/token \
  -d "client_id=oms-frontend" \
  -d "username=zhangsan" \
  -d "password=password123" \
  -d "grant_type=password" \
  -H "X-Forwarded-For: 203.0.113.50"
# 预期结果：密码通过后，返回OTP挑战（HTTP 401 + WWW-Authenticate）
# 执行路径：UsernamePassword→GeoRisk检测(无风险通过)→IP白名单(条件不满足跳过子Flow)→新设备检测(已知设备跳过)→TOTP(ALTERNATIVE触发OTP表单)
```

**场景3：外网+新设备（预期强制OTP或邮箱验证码）**

```bash
curl -X POST http://localhost:8080/realms/demo-realm/protocol/openid-connect/token \
  -d "client_id=oms-frontend" \
  -d "username=zhangsan" \
  -d "password=password123" \
  -d "grant_type=password" \
  -H "X-Forwarded-For: 203.0.113.50" \
  -H "User-Agent: Mozilla/5.0 (Unknown Device)"
# 预期结果：密码通过后，新设备检测触发SubFlow，弹出OTP或邮箱验证码表单
# 执行路径：UsernamePassword→GeoRisk检测→IP白名单(条件不满足)→新设备检测(matchCondition=true)→SubFlow OTP Form→成功
```

### 可能遇到的坑

1. **Conditional Authenticator的matchCondition返回false后，后续Execution仍会执行**。这是设计预期——Conditional只控制它附属的子Flow是否执行，不影响同级后续的Execution。如果希望"内网用户不走后续任何认证"，应该把后续所有Authenticator都放在Conditional的子Flow内部，或者用Flow嵌套实现。

2. **SubFlow的执行是递归的**——AuthenticationProcessor在`executeActions()`和`processFlow()`之间切换。调试时关注日志中的"authenticate"和"action"关键字。一个请求从浏览器到Keycloak可能经历多次HTTP往返（challenge→用户提交→challenge→用户提交），每一次往返都可能穿越Flow树的不同层级。

3. **认证流修改后需要在Client绑定新Flow**。Admin Console中修改了默认Browser Flow后，现有的Client不会自动切换——需要进入Client → Settings → Authentication Flow Overrides → Browser Flow，手动选择你创建的Flow。

4. **Factory的SPI注册文件路径**是`META-INF/services/org.keycloak.authentication.AuthenticatorFactory`，文件内容每行一个Factory的全限定类名。缺少注册或者类名写错，自定义Authenticator不会出现在Admin Console的下拉列表中。

5. **Cookie Authenticator的干扰**。如果你在测试时反复用同一个浏览器访问，Cookie Authenticator会识别已有SSO Session直接放行，导致你的自定义Authenticator根本不被触发。建议每次测试前清除浏览器Cookie，或使用curl（无Cookie状态）进行验证。

---

## 4 项目总结

### 优点与缺点

| 维度 | Keycloak认证流编排 | 应用层硬编码if-else认证 | 第三方MFA产品（DUO/Okta等） |
|------|-------------------|----------------------|---------------------------|
| 认证逻辑聚合 | ✅ 全部认证规则集中在Flow树中管理 | ❌ 登录代码散落各处，重复实现 | ✅ 提供统一认证入口 |
| 策略变更灵活性 | ✅ Admin Console可视化拖拽调整，无需发布 | ❌ 改规则=改代码=发版 | ✅ 管理后台配置 |
| 自定义扩展 | ✅ SPI接口清晰，Java原生开发 | ✅ 完全自主可控 | ⚠️ 通常只能配置不能扩展 |
| 多因素编排 | ✅ Conditional+SubFlow原生支持"先密码后OTP" | ❌ 需自行实现状态机 | ✅ 内置丰富认证链 |
| 供应商锁定 | ✅ 开源，无许可费用 | ✅ 完全自建 | ❌ 商业授权+持续付费 |
| 学习成本 | ⚠️ Flow模型+SPI接口需学习 | ✅ 常规Java开发 | ⚠️ 闭源产品的黑盒配置 |
| 调试复杂度 | ⚠️ 深度嵌套Flow的调用链追踪困难 | ✅ IDE中直接断点 | ❌ 调试手段有限 |

### 适用场景

1. **自适应认证**：根据用户IP、设备指纹、行为历史、时间窗口等上下文信息动态调整认证强度——这正是本章的核心场景。

2. **渐进式多因素认证**：先推行密码+TOTP，逐步加入生物识别（WebAuthn），通过Conditional架构逐步叠加而不用重构认证逻辑。

3. **零信任架构落地**：零信任的核心理念是"从不信任，始终验证"——每次请求都做完整的认证和授权判断，而非依赖网络位置。Keycloak的认证流正是每次登录都执行完整策略链，天然匹配零信任。

4. **多租户差异化认证**：不同Realm或不同Client可以绑定不同的Browser Flow——SaaS平台可以让企业A用密码+OTP，企业B用密码+LDAP，企业C用社交登录，各租户的认证策略互不干扰。

### 不适用场景

1. **认证规则极少（2-3条）**：如果只有"密码"和"内网/外网"两个判断，硬编码的实现更简单直接，引入SPI的整体收益不高。

2. **需要超过认证阶段的持续风险评估**：如果需要在用户操作全程（而非仅登录时）持续评估风险——例如"用户正在执行转账操作，此时检测到IP突变"——这超出认证流的能力范围，需要结合持续的Risk Engine或Session管理来实现。

### 注意事项

- **认证流复杂度与用户体验的平衡**：每增加一个Required Authenticator，用户就多一次交互。如果一个登录流程需要用户反复填写表单，跳出率会急剧上升。建议定期审视Flow中每个节点的必要性——能用Cookie/Session解决的不要每次都弹窗。

- **Conditional的"短路"行为**：Conditional Authenticator的matchCondition返回false时，只是跳过其附属的子Flow，不会跳过同一层级的下一个Execution。这是最常见的设计误解。

- **Brute Force Detection与认证流的协作**：如果Brute Force Detection在密码认证环节已经将用户临时锁定，后续的GeoRisk检测和OTP都不会执行——Flow在密码环节的Required失败时就终止了。因此，密码Authenticator的位置就是整个Flow的"守门人"——如果像本章的设计将GeoRisk放在密码之后，意味着攻击者至少需要知道一个有效用户名并走到密码验证阶段，Brute Force Detection才有机会发挥作用。

- **版本兼容**：本章的SPI代码基于Keycloak 26.x的API编写。不同大版本（22.x→24.x→26.x）之间Authenticator接口可能有小改动，升级前务必查看Migration Guide。

### 常见踩坑经验

1. **问题**：自定义Authenticator在Admin Console看不到。**根因**：SPI注册文件`org.keycloak.authentication.AuthenticatorFactory`未放在`META-INF/services`目录下，或文件内容中的类名与实际类名不一致。**解决**：在Keycloak启动日志中搜索`SPI: org.keycloak.authentication.AuthenticatorFactory`，确认你的Factory已被加载。

2. **问题**：Conditional Authenticator的matchCondition返回true但子Flow没有被执行。**根因**：在Admin Console中，该Execution的Requirement未被设置为CONDITIONAL，而是设成了REQUIRED或ALTERNATIVE——Conditional Authenticator必须配合CONDITIONAL Requirement使用才能触发matchCondition流程。**解决**：进入Flow详情，点击该Execution旁边的下拉菜单，将Requirement改为"Conditional"。

3. **问题**：用户在密码通过后被"卡住"——页面空白无任何提示。**根因**：某个Authenticator的`authenticate()`中既没调`success()`也没调`challenge()`，AuthenticationProcessor无法推进到下一步。**解决**：检查所有自定义Authenticator的`authenticate()`方法——确保每个执行路径都有明确的出口（success/challenge/failure）。

### 思考题

1. **基于用户行为分数的认证策略**：假设公司有一个行为分析系统，每天为每个用户计算一个"信任分数"（0-100分）。连续30天在公司内网登录且无异常行为的用户分数为95分。需求是：信任分数≥90分的用户在非敏感操作（如查看文档）时可以免MFA，但执行敏感操作（修改密码、导出数据）时仍需MFA。请设计一个方案：是将行为分析引擎通过SPI集成到Keycloak认证流中进行决策？还是在认证流中只做基础认证，将行为分数判断交给授权层（Authorization Services的JS策略）？两种方案的优缺点各是什么？

2. **管理50+条条件规则的认证流复杂度**：随着安全策略的不断叠加，项目中的Browser Flow已经从一棵简洁的树膨胀为包含50+个Conditional Execution的庞然大物。请设计一套认证流的治理方案：是否可以采用"策略模式"将多条规则聚合为一个"规则引擎Authenticator"（将所有规则配置化，由一个Authenticator统一评估）？还是保持Flow的树形结构，通过分组和命名规范来治理？如果采用规则引擎方案，如何在不重启Keycloak的情况下动态变更规则？

---

> **推广计划提示**：本章面向认证架构师和资深Java开发。开发团队需理解SPI扩展机制和Flow执行模型以实施自定义Authenticator；运维团队需掌握Admin Console中的Flow绑定操作以配合上线部署；安全团队应参与Flow设计评审，确保认证强度与风险评估策略一致。建议阅读顺序：先通读第7-9章理解认证流程基础，再阅读第23-24章搭建SPI开发环境，最后精读本章完成实战落地。
