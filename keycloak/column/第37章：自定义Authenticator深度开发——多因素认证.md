# 第37章：自定义Authenticator深度开发——多因素认证

## 1 项目背景

某省级政务服务平台（以下简称"政务通"）接到省大数据管理局的一纸红头文件：所有面向公众的政务服务系统，必须在一个月内接入省级统一的短信验证码平台，实现"密码+短信验证码"的双因素认证。文件附件里附了一份安全基线检查表——短信验证码必须为6位纯数字、有效期不超过60秒、同一手机号每分钟最多请求3次、同一用户连续错误5次锁定30分钟。每一项都是硬指标，验收时逐条核对，缺一不可。

开发团队打开Keycloak管理控制台，翻到Authentication → OTP Policy页面，心凉了半截——Keycloak内置的OTP只支持TOTP（基于时间的一次性密码，典型实现是Google Authenticator），与短信验证码完全是两套机制。TOTP基于HMAC-SHA1算法对时间窗口做哈希，密钥在用户注册时通过二维码分发给客户端，服务器和客户端各自独立计算验证码；而短信验证码是服务端生成后通过短信通道推送给用户手机的，传递信道、有效期校验、频率限制逻辑均不相同。想用内置OTP来"凑合"行不通。

更棘手的是三个延伸需求。其一，管理员需要在Admin Console中可视化配置短信平台的API地址和密钥——这些敏感参数绝对不能硬编码在JAR包或配置文件里，因为生产环境的API地址、测试地址、灾备地址各不相同。其二，微信小程序用户还需要支持"微信扫码登录"——用户在小程序中打开政务通，显示一个二维码，用微信App扫码后，PC端的Keycloak自动完成认证。其三，短信验证码页面需要支持国际化——中文用户看到"请输入短信验证码"，英文用户看到"Enter SMS verification code"。这三个需求分别对应Authenticator SPI的配置模型、轮询式认证状态机、以及Keycloak的消息国际化机制。

更深层的技术风险在于：短信验证码依赖外部API调用（发送和校验），网络超时、短信平台宕机、短信送达延迟等不确定性因素都需要在Authenticator中做容错处理。本章从零开始，带你实现一个完整的"密码+短信验证码"双因素认证方案，并将轮询式扫码登录作为进阶扩展一并剖析。

---

## 2 项目设计——剧本式交锋对话

**小胖**（刚从公司食堂回来，嘴里还嚼着红烧肉）：大师，我今天去银行办事突然悟了。你看银行怎么验身份的——第一步，柜员拿你身份证在机器上一刷，确认"你是你"（这不就是密码认证嘛）；第二步，柜员说"我给你手机上发了一条验证码，念给我听"——两步都过了才给你办业务。这不是再简单不过的事吗？为什么我看Keycloak源码里那些Authenticator接口绕来绕去的，先`authenticate()`再`action()`，两个步骤之间怎么记住用户已经通过了第一步？

**大师**（端起茶杯，目光越过杯沿）：你这银行比喻点到了认证流程的核心矛盾——**状态保持**。你进银行大门时刷身份证，柜员在你的叫号小票上打了个勾（密码通过）；到柜台时柜员扫码小票就知道你过第一关了，再要短信验证码——小票就是你的"会话"。Keycloak里，这个小票叫`AuthenticationSessionModel`。用户初次请求`/auth`时，Keycloak生成一个AuthenticationSession，后续所有`authenticate()`和`action()`的调用都在同一个Session上下文中进行，通过`context.getAuthenticationSession()`即可获取。你以为银行柜员是天生记性好才记得你过没过第一关？不是，是小票上有你的办理流水号。

> **大师技术映射**：银行叫号小票 = AuthenticationSessionModel，承载了认证流程中跨请求的中间状态。密码通过标记、短信验证码值、发送时间、尝试次数——全部存在这张"小票"里。

---

**小白**（在白板上画了一份Authenticator调用时序图，密密麻麻标注了问号）：我追了`AuthenticationProcessor`的源码，搞清楚了`authenticate()`和`action()`的调用时机。但我对三个细节卡住了——第一，`authenticate()`里生成验证码并发送短信后，`action()`里怎么取到之前生成的验证码做比对？是存在全局变量里还是存在用户Session里？第二，Authenticator的Factory接口里有个`getConfigProperties()`方法，这个方法返回一个`List<ProviderConfigProperty>`，这块配置怎么映射到Admin Console的可视化表单上？管理员在控制台中填的API地址和密钥最终怎么被Authenticator读到？第三，微信扫码登录的轮询机制——二维码本质上是一个UUID，用户在手机微信里扫码成功后，PC上的Keycloak怎么知道扫码成功了？难道要我写个定时器每秒去查一次？

**大师**（放下茶杯，接过小白手里的白板笔，在上面补了几个箭头）：这三个问号全打在Authenticator SPI最核心的设计点上。逐一拆开来看——

先说第一个，`authenticate()`和`action()`之间的数据传递。记住一条铁律：**Authenticator本身是无状态的**——Factory的`create()`方法每次认证会话都会创建新实例，不能在Authenticator里放实例变量存储验证码，因为`authenticate()`调用结束后这个实例就等着GC回收了。正确做法是通过`AuthenticationSession`附带的`authNotes`——一个`Map<String, String>`键值对存储。在`authenticate()`里调用`authSession.setAuthNote("sms_code", "123456")`存入验证码，在`action()`里调用`authSession.getAuthNote("sms_code")`取出比对。`authNotes`的生命周期与认证会话一致，整个Flow内的所有Authenticator和所有`authenticate()`/`action()`调用共享同一个`authNotes`实例。此外，`formData`（用户提交的表单参数）和`Challenge Context`（当前Form Action URL和表单属性）由`AuthenticationFlowContext`自动维护，你不需要手动管理。

第二个，Admin Console的可视化配置路径。`AuthenticatorFactory.getConfigProperties()`定义了配置项元数据——每个`ProviderConfigProperty`包含名称（name）、显示标签（label）、类型（STRING_TYPE、BOOLEAN_TYPE、LIST_TYPE等）、帮助文本（helpText）、默认值（defaultValue）。Keycloak的Admin Console在渲染Flow配置页时，会调用Flow中所有Authenticator对应的Factory的`getConfigProperties()`方法，根据返回的元数据列表自动生成表单——STRING_TYPE生成文本输入框，BOOLEAN_TYPE生成开关按钮，LIST_TYPE生成下拉选择框，PASSWORD类型生成带掩码的密码输入框。管理员在Admin Console中保存配置后，Keycloak将配置值序列化存入`AuthenticationExecutionModel`的`config`字段（本质上是一个`Map<String, String>`的JSON）。运行时，你的Authenticator通过`context.getAuthenticatorConfig()`获取`AuthenticatorConfigModel`对象，再调用`configModel.getConfig().get("smsApiUrl")`读取管理员配置的值。完整的数据链路是：`getConfigProperties()`定义元数据 → Admin Console自动生成表单 → 管理员填写保存 → 运行时`getAuthenticatorConfig()`读取。

第三个，扫码登录的轮询机制。这确实是Authenticator设计中比较精妙的状态机模式。核心思路是三次`challenge()`循环：第一次`authenticate()`生成UUID作为二维码标识，将二维码图片（或生成二维码的凭证）渲染到FTL页面中，同时把UUID写入`authNotes`，调用`context.challenge()`返回页面。FTL页面中嵌入JavaScript的`setInterval()`，每隔1-2秒发一个AJAX请求到Keycloak的`/auth`端点（实际上是重新提交认证表单，触发`action()`）。在`action()`方法中，检查`authNotes`中是否已有"扫码成功"的标记（微信服务端收到小程序扫码回调后，通过Keycloak的自定义REST API写入`authNotes`标记），如果有则`context.success()`，如果超过轮询超时时间则`context.failure()`，否则重新`context.challenge()`让页面继续轮询。整个过程就是：显示二维码 → 轮询扫码状态 → 扫码成功自动提交 → 完成认证。

> **大师技术映射**：authNotes = 你的病历本——挂号时写症状（authenticate写验证码），就诊时医生翻看病历本确认诊断（action读验证码）。getConfigProperties() = 餐厅的调料台——你定义有哪些可选调料（配置项），食客自己调配（管理员填值），厨师做菜时看一眼调料台上的碗（getAuthenticatorConfig读取）。

---

**小胖**（第二轮，从抽屉里掏出一张上个月公司短信平台的账单）：等一下，我算笔账。政务平台预计日均登录量30万次，每条短信4分钱——每天光短信费就是1万2，一年400多万！要是有人恶意刷短信验证码——比如写个脚本疯狂点"发送验证码"，这钱不就打水漂了？还有，扫二维码这个场景——如果一个攻击者做了一个一模一样的假网站，把真网站的二维码截下来贴到假网站上，用户扫了码，是不是就被劫持了？

**大师**（神情严肃起来）：这两个问题不是技术选择题，而是安全基线。先看短信成本控制——你提到的"防刷"在第一版设计里就应该作为强制约束写进去而不是事后打补丁。我们的设计已经包含三层防护：第一层，`authenticate()`进入时先检查`authNotes`中的`sms_last_sent`时间戳，如果距上次发送不足20秒直接拒绝（频率限制）；第二层，同一手机号每分钟最多3次——这需要在`authNotes`之外维护一个更全局的计数器，简单方案是用`RealmModel`级别的缓存或用`UserModel`的属性存储`sms_daily_count`和`sms_daily_date`；第三层，连续错误5次锁定30分钟——在`authNotes`中维护`attempts`计数，达到阈值后将锁定标记和锁定时间写入`UserModel`属性（因为锁定是跨认证会话的，`authNotes`生命周期只覆盖单次认证会话）。三层防护从"单次会话的发送频率"到"设备级别的每日上限"再到"用户级别的累积错误锁定"，层层递进。

关于扫码劫持——二维码本身是一个UUID标识符，不包含任何认证信息。攻击者截获二维码贴到假网站，用户在假网站上看到的确实是同一个二维码。但这为什么不会导致劫持？因为扫码成功后的回调链路是这样的：微信App扫码后，由微信服务器调Keycloak的REST API（携带小程序用户的OpenID和二维码UUID）通知"已扫码"。Keycloak在API中验证两件事：这个UUID确实对应一个正在等待扫码的认证会话；这个OpenID确实绑定到了政务平台的一个用户账号。如果绑定关系不存在，API直接返回"用户未绑定"。攻击者的假网站虽然显示了同样的二维码，但用户扫码后微信会把OpenID提交到Keycloak——Keycloak发现这个OpenID没有绑定到任何政务平台用户，自然拒绝完成认证。攻击者无法替换回调链路中的微信服务器签名校验环节，所以"贴码攻击"在架构层面就被阻挡了。

> **大师总结技术映射**：

| 生活比喻 | 技术映射 |
|---------|---------|
| 银行柜员分两步验证：身份证→短信验证码 | Authenticator的authenticate()→action()：先展示质询表单，再校验用户输入 |
| 叫号小票记录办理流水 | AuthenticationSessionModel的authNotes：跨authenticate/action存储中间状态 |
| 调料台上的小碗由食客自己加 | getConfigProperties() → Admin Console表单 → getAuthenticatorConfig()读取 |
| 餐馆门口排号机显示"前面还有几位" | 扫码登录轮询机制：前端定时查状态 → 扫码成功自动放行 |
| 你一天取钱超过3次银行柜员拒绝服务 | 短信频率限制：authNotes记录发送时间+每日计数+错误锁定 |

---

## 3 项目实战

### 环境准备

| 组件 | 版本/说明 |
|------|----------|
| JDK | 17+ |
| Maven | 3.8+ |
| Keycloak | 26.x，基于第23-25章的SPI开发环境 |
| Mockoon/Flask | 模拟短信API服务（本地使用Mockoon创建Mock Server，监听`localhost:3001`） |
| IDE | IntelliJ IDEA（推荐，方便调试SPI代码） |

**短信API模拟服务的Mockoon配置**：在Mockoon中创建两个端点——`POST /api/sms/send`（请求体包含`phone`和`code`字段，返回`{"success": true}`或`{"success": false}`）和`GET /api/sms/status/{code}`（用于扫码状态查询，返回`{"scanned": true/false}`）。Mockoon的延迟设置可以模拟网络超时——把Mock端点延迟设为5000ms来模拟短信平台超时，验证你的Authenticator中try-catch和超时处理的正确性。

---

### 步骤1：创建Maven项目结构与依赖

**目标**：搭建符合Keycloak SPI规范的完整Maven项目，引入短信HTTP客户端依赖。

项目目录结构：

```
sms-otp-authenticator/
├── pom.xml
├── src/main/java/com/government/keycloak/
│   ├── SmsOtpAuthenticator.java      # Authenticator核心实现
│   ├── SmsOtpAuthenticatorFactory.java # Factory（含配置定义）
│   └── SmsClient.java                # 短信API HTTP客户端封装
├── src/main/resources/
│   ├── META-INF/services/
│   │   └── org.keycloak.authentication.AuthenticatorFactory
│   └── theme-resources/
│       └── templates/
│           └── sms-otp.ftl            # 短信验证码输入页面模板
└── src/main/resources/messages/
    └── messages_zh_CN.properties      # 中文国际化资源
```

`pom.xml` 核心依赖（基于第23章的模板扩展）：

```xml
<dependencies>
    <dependency>
        <groupId>org.keycloak</groupId>
        <artifactId>keycloak-core</artifactId>
        <version>${keycloak.version}</version>
        <scope>provided</scope>
    </dependency>
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
    <!-- HTTP客户端（用于调用短信API） -->
    <dependency>
        <groupId>org.apache.httpcomponents.client5</groupId>
        <artifactId>httpclient5</artifactId>
        <version>5.3</version>
    </dependency>
</dependencies>
```

> **依赖说明**：`httpclient5`没有`provided` scope——它是你的SPI独有的第三方依赖，Keycloak本身不带这个版本。打包时需要用`maven-assembly-plugin`打成Fat JAR，或使用Quarkus的`quarkus.class-loading.parent-first-artifacts`将`httpclient5`加入父优先加载列表，避免类冲突。

---

### 步骤2：实现短信验证码Authenticator核心类

**目标**：实现`authenticate()`生成并发送短信验证码，`action()`校验用户输入，覆盖频率限制、过期处理、错误计数锁定的完整逻辑。

编写 `SmsOtpAuthenticator.java`：

```java
package com.government.keycloak;

import jakarta.ws.rs.core.MultivaluedMap;
import jakarta.ws.rs.core.Response;
import org.keycloak.authentication.AuthenticationFlowContext;
import org.keycloak.authentication.AuthenticationFlowError;
import org.keycloak.authentication.Authenticator;
import org.keycloak.models.AuthenticationExecutionModel;
import org.keycloak.models.AuthenticatorConfigModel;
import org.keycloak.models.KeycloakSession;
import org.keycloak.models.RealmModel;
import org.keycloak.models.UserModel;
import org.keycloak.sessions.AuthenticationSessionModel;

import java.security.SecureRandom;

public class SmsOtpAuthenticator implements Authenticator {

    private static final String AUTH_NOTE_CODE = "sms_otp_code";
    private static final String AUTH_NOTE_PHONE = "sms_otp_phone";
    private static final String AUTH_NOTE_EXPIRY = "sms_otp_expiry";
    private static final String AUTH_NOTE_ATTEMPTS = "sms_otp_attempts";
    private static final String AUTH_NOTE_LAST_SENT = "sms_last_sent";

    @Override
    public void authenticate(AuthenticationFlowContext context) {
        UserModel user = context.getUser();
        String phone = user.getFirstAttribute("phone_number");
        if (phone == null || phone.isBlank()) {
            context.failure(AuthenticationFlowError.INVALID_USER,
                    Response.status(302).header("Location",
                            context.getRefreshExecutionUrl()).build());
            return;
        }

        // 读取Admin Console中的配置
        AuthenticatorConfigModel configModel = context.getAuthenticatorConfig();
        int validitySeconds = 60;
        int codeLength = 6;
        if (configModel != null) {
            String validityStr = configModel.getConfig().get("validitySeconds");
            if (validityStr != null) validitySeconds = Integer.parseInt(validityStr);
            String lenStr = configModel.getConfig().get("codeLength");
            if (lenStr != null) codeLength = Integer.parseInt(lenStr);
        }

        // 频率限制：同一手机号20秒内不能重复发送
        AuthenticationSessionModel authSession = context.getAuthenticationSession();
        String lastSentStr = authSession.getAuthNote(AUTH_NOTE_LAST_SENT);
        long now = System.currentTimeMillis();
        if (lastSentStr != null) {
            long lastSent = Long.parseLong(lastSentStr);
            if (now - lastSent < 20000) {
                long waitSeconds = 20 - (now - lastSent) / 1000;
                Response challenge = context.form()
                        .setError("请等待" + waitSeconds + "秒后再请求验证码")
                        .setAttribute("phone", maskPhone(phone))
                        .createForm("sms-otp.ftl");
                context.challenge(challenge);
                return;
            }
        }

        // 生成N位数字验证码
        String code = String.format("%0" + codeLength + "d",
                new SecureRandom().nextInt((int) Math.pow(10, codeLength)));
        long expiry = now + validitySeconds * 1000L;

        // 存入authNotes——authenticate()和action()之间的数据桥梁
        authSession.setAuthNote(AUTH_NOTE_CODE, code);
        authSession.setAuthNote(AUTH_NOTE_PHONE, phone);
        authSession.setAuthNote(AUTH_NOTE_EXPIRY, String.valueOf(expiry));
        authSession.setAuthNote(AUTH_NOTE_ATTEMPTS, "0");
        authSession.setAuthNote(AUTH_NOTE_LAST_SENT, String.valueOf(now));

        // 调用短信平台API发送验证码
        String apiUrl = configModel != null
                ? configModel.getConfig().get("smsApiUrl")
                : null;
        String apiKey = configModel != null
                ? configModel.getConfig().get("smsApiKey")
                : null;

        SmsClient smsClient = new SmsClient(apiUrl, apiKey);
        try {
            boolean sent = smsClient.send(phone,
                    "您的验证码是：" + code + "，有效期" + validitySeconds + "秒。");
            if (!sent) {
                context.failure(AuthenticationFlowError.INTERNAL_ERROR);
                return;
            }
        } catch (Exception e) {
            // 短信平台超时或不可用的降级处理
            context.getSession().getContext().getLogger()
                    .warn("短信发送失败，电话=" + maskPhone(phone), e);
            context.failure(AuthenticationFlowError.INTERNAL_ERROR);
            return;
        }

        // 渲染验证码输入页面
        Response challenge = context.form()
                .setAttribute("phone", maskPhone(phone))
                .setAttribute("validitySeconds", validitySeconds)
                .createForm("sms-otp.ftl");
        context.challenge(challenge);
    }

    @Override
    public void action(AuthenticationFlowContext context) {
        AuthenticationSessionModel authSession = context.getAuthenticationSession();
        MultivaluedMap<String, String> formData =
                context.getHttpRequest().getDecodedFormParameters();
        String userInput = formData.getFirst("sms_code");

        // 校验：验证码是否过期
        String expiryStr = authSession.getAuthNote(AUTH_NOTE_EXPIRY);
        if (expiryStr == null) {
            context.failure(AuthenticationFlowError.EXPIRED_CODE);
            return;
        }
        long expiry = Long.parseLong(expiryStr);
        if (System.currentTimeMillis() > expiry) {
            // 清除过期验证码，重新回到authenticate流程
            authSession.removeAuthNote(AUTH_NOTE_CODE);
            authSession.removeAuthNote(AUTH_NOTE_EXPIRY);
            Response challenge = context.form()
                    .setError("验证码已过期，请重新获取")
                    .setAttribute("phone", maskPhone(authSession.getAuthNote(AUTH_NOTE_PHONE)))
                    .createForm("sms-otp.ftl");
            context.challenge(challenge);
            return;
        }

        // 校验：错误次数检查（5次锁定）
        String attemptsStr = authSession.getAuthNote(AUTH_NOTE_ATTEMPTS);
        int attempts = attemptsStr == null ? 0 : Integer.parseInt(attemptsStr);
        if (attempts >= 5) {
            String phone = authSession.getAuthNote(AUTH_NOTE_PHONE);
            context.getUser().setSingleAttribute("sms_locked_until",
                    String.valueOf(System.currentTimeMillis() + 30 * 60 * 1000));
            context.failure(AuthenticationFlowError.INVALID_CREDENTIALS);
            return;
        }

        // 校验：验证码比对
        String storedCode = authSession.getAuthNote(AUTH_NOTE_CODE);
        if (storedCode != null && storedCode.equals(userInput)) {
            // 验证成功——清除敏感数据，放行
            authSession.removeAuthNote(AUTH_NOTE_CODE);
            authSession.removeAuthNote(AUTH_NOTE_EXPIRY);
            authSession.removeAuthNote(AUTH_NOTE_ATTEMPTS);
            context.success();
        } else {
            // 验证失败——增加错误计数，允许重试
            authSession.setAuthNote(AUTH_NOTE_ATTEMPTS, String.valueOf(attempts + 1));
            int remaining = 4 - attempts;
            Response challenge = context.form()
                    .setError(remaining > 0
                            ? "验证码错误，还剩" + remaining + "次机会"
                            : "验证码错误次数已达上限，请30分钟后重试")
                    .setAttribute("phone", maskPhone(authSession.getAuthNote(AUTH_NOTE_PHONE)))
                    .createForm("sms-otp.ftl");
            context.challenge(challenge);
        }
    }

    private String maskPhone(String phone) {
        if (phone == null || phone.length() < 7) return phone;
        return phone.substring(0, 3) + "****" + phone.substring(phone.length() - 4);
    }

    @Override
    public boolean requiresUser() {
        return true; // 该Authenticator需要已知用户身份（因为要读手机号）
    }

    @Override
    public boolean configuredFor(KeycloakSession session, RealmModel realm, UserModel user) {
        return user.getFirstAttribute("phone_number") != null;
    }

    @Override
    public void setRequiredActions(KeycloakSession session, RealmModel realm, UserModel user) {
        // 如果用户没有手机号，自动添加一个Required Action要求绑定手机号
        if (user.getFirstAttribute("phone_number") == null) {
            // user.addRequiredAction("bind-phone-number");
        }
    }

    @Override
    public void close() {}
}
```

**关键设计说明**：

- `authenticate()`和`action()`的职责边界：`authenticate()`负责"发起质询"——检查前置条件（用户是否有手机号）、生成验证码、调用短信API发送、渲染输入页面；`action()`负责"处理响应"——读取用户输入、校验过期/次数/码值、决定成功或重试。两者之间通过`authNotes`交换数据。
- 频率限制的三段设计：`authenticate()`入口处的20秒间隔检查（防护同一认证会话内的刷短信行为）→ 可在`action()`错误计数达到5次后添加额外保护（跨认证会话锁定通过UserModel属性实现）→ 生产环境还需在`SmsClient.send()`之前增加每日每手机号的发送上限检查。
- `configuredFor()`返回`true`的含义：只有当用户属性中存在`phone_number`时，该Authenticator才被视为"已配置"。在Alternative组合中，如果用户没绑定手机号，该Authenticator会返回`false`，Keycloak的认证引擎会跳过它，尝试下一个Authenticator。这提供了优雅的降级路径——比如没绑定手机号的用户自动走TOTP认证而不是短信。
- `context.getAuthenticatorConfig()`的读取时机：它在`authenticate()`第一行即可调用，返回的是Admin Console中管理员为此Execution配置的参数。注意判空——首次在Flow中添加该Execution但尚未配置时，此方法返回`null`。

---

### 步骤3：实现短信HTTP客户端（含超时与重试）

**目标**：封装短信API的HTTP调用，处理超时、重试、异常降级。

编写 `SmsClient.java`：

```java
package com.government.keycloak;

import org.apache.hc.client5.http.classic.methods.HttpPost;
import org.apache.hc.client5.http.config.RequestConfig;
import org.apache.hc.client5.http.impl.classic.CloseableHttpClient;
import org.apache.hc.client5.http.impl.classic.CloseableHttpResponse;
import org.apache.hc.client5.http.impl.classic.HttpClients;
import org.apache.hc.core5.http.io.entity.EntityUtils;
import org.apache.hc.core5.http.io.entity.StringEntity;
import org.apache.hc.core5.http.ContentType;
import org.apache.hc.core5.util.Timeout;

import java.util.concurrent.TimeUnit;

public class SmsClient {

    private final String apiUrl;
    private final String apiKey;

    public SmsClient(String apiUrl, String apiKey) {
        this.apiUrl = apiUrl;
        this.apiKey = apiKey;
    }

    public boolean send(String phone, String message) {
        if (apiUrl == null || apiUrl.isBlank()) {
            return false;
        }

        int maxRetries = 2;
        for (int attempt = 0; attempt <= maxRetries; attempt++) {
            try (CloseableHttpClient client = HttpClients.custom()
                    .setDefaultRequestConfig(RequestConfig.custom()
                            .setConnectTimeout(Timeout.of(5, TimeUnit.SECONDS))
                            .setResponseTimeout(Timeout.of(5, TimeUnit.SECONDS))
                            .build())
                    .build()) {

                HttpPost post = new HttpPost(apiUrl);
                post.setHeader("Authorization", "Bearer " + apiKey);
                post.setHeader("Content-Type", "application/json");

                String json = String.format(
                        "{\"phone\":\"%s\",\"message\":\"%s\"}", phone, message);
                post.setEntity(new StringEntity(json, ContentType.APPLICATION_JSON));

                try (CloseableHttpResponse response = client.execute(post)) {
                    int status = response.getCode();
                    if (status >= 200 && status < 300) {
                        return true;
                    }
                    // 4xx错误不重试（如手机号格式错误），5xx错误重试
                    if (status >= 400 && status < 500) {
                        return false;
                    }
                }
            } catch (Exception e) {
                if (attempt == maxRetries) {
                    return false;
                }
                // 指数退避：第1次重试等1秒，第2次等2秒
                try {
                    Thread.sleep((attempt + 1) * 1000L);
                } catch (InterruptedException ie) {
                    Thread.currentThread().interrupt();
                    return false;
                }
            }
        }
        return false;
    }
}
```

> **重试策略说明**：4xx错误（如手机号格式非法）不重试——重试也无法修复。5xx错误（短信平台临时故障）最多重试2次，指数退避。连接超时和响应超时各5秒，防止短信平台假死不响应导致Authenticator线程被长时间阻塞。

---

### 步骤4：实现Factory——驱动Admin Console配置表单

**目标**：定义配置项元数据，使管理员在Admin Console中可视化配置短信API地址、密钥、验证码长度和有效期。

编写 `SmsOtpAuthenticatorFactory.java`：

```java
package com.government.keycloak;

import org.keycloak.Config;
import org.keycloak.authentication.Authenticator;
import org.keycloak.authentication.AuthenticatorFactory;
import org.keycloak.models.AuthenticationExecutionModel;
import org.keycloak.models.KeycloakSession;
import org.keycloak.models.KeycloakSessionFactory;
import org.keycloak.provider.ProviderConfigProperty;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;

public class SmsOtpAuthenticatorFactory implements AuthenticatorFactory {

    public static final String PROVIDER_ID = "sms-otp-authenticator";

    private static final List<ProviderConfigProperty> CONFIG_PROPERTIES = new ArrayList<>();

    static {
        ProviderConfigProperty apiUrl = new ProviderConfigProperty();
        apiUrl.setName("smsApiUrl");
        apiUrl.setLabel("短信平台API地址");
        apiUrl.setType(ProviderConfigProperty.STRING_TYPE);
        apiUrl.setHelpText("短信服务提供商的API端点，如 https://sms.example.com/api/send");
        apiUrl.setDefaultValue("https://sms-provider.example.com/api/send");
        CONFIG_PROPERTIES.add(apiUrl);

        ProviderConfigProperty apiKey = new ProviderConfigProperty();
        apiKey.setName("smsApiKey");
        apiKey.setLabel("API密钥");
        apiKey.setType(ProviderConfigProperty.PASSWORD);
        apiKey.setHelpText("短信平台的认证密钥（Secret），保存后不可见");
        apiKey.setSecret(true);
        CONFIG_PROPERTIES.add(apiKey);

        ProviderConfigProperty codeLength = new ProviderConfigProperty();
        codeLength.setName("codeLength");
        codeLength.setLabel("验证码长度");
        codeLength.setType(ProviderConfigProperty.LIST_TYPE);
        codeLength.setOptions(Arrays.asList("4", "6", "8"));
        codeLength.setDefaultValue("6");
        CONFIG_PROPERTIES.add(codeLength);

        ProviderConfigProperty validitySeconds = new ProviderConfigProperty();
        validitySeconds.setName("validitySeconds");
        validitySeconds.setLabel("有效期（秒）");
        validitySeconds.setType(ProviderConfigProperty.STRING_TYPE);
        validitySeconds.setDefaultValue("60");
        CONFIG_PROPERTIES.add(validitySeconds);
    }

    @Override
    public String getId() {
        return PROVIDER_ID;
    }

    @Override
    public String getDisplayType() {
        return "短信验证码认证";
    }

    @Override
    public String getReferenceCategory() {
        return "sms-otp";
    }

    @Override
    public boolean isConfigurable() {
        return true; // 开启Admin Console中的配置齿轮图标
    }

    @Override
    public boolean isUserSetupAllowed() {
        return false; // 不允许用户在账户管理页面自行配置
    }

    @Override
    public AuthenticationExecutionModel.Requirement[] getRequirementChoices() {
        return new AuthenticationExecutionModel.Requirement[] {
                AuthenticationExecutionModel.Requirement.REQUIRED,
                AuthenticationExecutionModel.Requirement.ALTERNATIVE,
                AuthenticationExecutionModel.Requirement.DISABLED
        };
    }

    @Override
    public List<ProviderConfigProperty> getConfigProperties() {
        return CONFIG_PROPERTIES;
    }

    @Override
    public Authenticator create(KeycloakSession session) {
        return new SmsOtpAuthenticator();
    }

    @Override
    public String getHelpText() {
        return "通过短信发送数字验证码进行双因素认证。需用户属性中存在phone_number字段。";
    }

    @Override
    public void init(Config.Scope config) {}

    @Override
    public void postInit(KeycloakSessionFactory factory) {}

    @Override
    public void close() {}
}
```

> **ProviderConfigProperty的四种类型**：`STRING_TYPE`生成普通文本输入框；`PASSWORD`生成密码输入框（Admin Console中显示为`***`且在数据库中加密存储）；`BOOLEAN_TYPE`生成开关；`LIST_TYPE`生成下拉选择框（需配合`setOptions()`指定可选值列表）。`setSecret(true)`标记对应的值是敏感数据，Admin Console在显示和日志中会做脱敏处理。

---

### 步骤5：编写FTL模板与SPI注册文件

**目标**：创建验证码输入页面的FTL模板，编写SPI注册文件使Keycloak能发现该Authenticator。

编写 `src/main/resources/theme-resources/templates/sms-otp.ftl`：

```html
<#import "template.ftl" as layout>
<@layout.registrationLayout displayMessage=!messagesPerField.existsError('sms_code')>
    <#if message?has_content && message.type = "error">
        <div class="alert alert-error">${message.summary}</div>
    </#if>

    <div class="sms-otp-container">
        <h2>${msg("smsOtpTitle", "短信验证码验证")}</h2>
        <p>${msg("smsOtpInstruction", "验证码已发送至手机")} <strong>${phone!}</strong></p>

        <form id="sms-otp-form" action="${url.loginAction}" method="post">
            <div class="form-group">
                <label for="sms_code">${msg("smsOtpCode", "短信验证码")}</label>
                <input type="text" id="sms_code" name="sms_code"
                       class="form-control" maxlength="${codeLength!'6'}"
                       autocomplete="off" autofocus
                       pattern="[0-9]{${codeLength!'6'}}"
                       inputmode="numeric"
                       required>
            </div>

            <div class="sms-timer">
                <span id="timer-text">${validitySeconds!'60'}秒后过期</span>
            </div>

            <button type="submit" class="btn btn-primary">
                ${msg("doVerify", "验证")}
            </button>
            <a href="${url.loginRestartFlowUrl}" class="btn btn-default">
                ${msg("doCancel", "取消")}
            </a>
        </form>
    </div>

    <script>
    <#if validitySeconds??>
    var seconds = ${validitySeconds};
    var timer = setInterval(function() {
        seconds--;
        var el = document.getElementById('timer-text');
        if (el) el.textContent = seconds + '秒后过期';
        if (seconds <= 0) {
            clearInterval(timer);
            var btn = document.querySelector('#sms-otp-form button[type=submit]');
            if (btn) { btn.disabled = true; btn.textContent = '已过期'; }
        }
    }, 1000);
    </#if>
    </script>
</@layout.registrationLayout>
```

编写 `src/main/resources/META-INF/services/org.keycloak.authentication.AuthenticatorFactory`：

```
com.government.keycloak.SmsOtpAuthenticatorFactory
```

---

### 步骤6：打包、部署与Admin Console配置

**目标**：将SPI JAR部署到Keycloak，在Admin Console中配置短信验证码为Browser Flow的第二因素。

```bash
# 打包（根目录执行）
cd sms-otp-authenticator
mvn clean package -DskipTests

# 将JAR部署到Keycloak的providers目录
cp target/sms-otp-authenticator-1.0.0.jar $KEYCLOAK_HOME/providers/

# 重新构建Keycloak
kc.sh build

# 启动Keycloak
kc.sh start-dev
```

Admin Console配置路径（启动后浏览器操作）：

1. 进入 **Realm → Authentication → Flows**
2. 选择 **Browser Flow**，点击 **Add execution**
3. 在Provider列表中选择 **"短信验证码认证"**，Save
4. 将新增的Execution拖拽到密码认证之后（确保先密码后短信）
5. 将Requirement设为 **REQUIRED**
6. 点击Execution右侧的**齿轮图标**→ 填写短信平台API地址、密钥、验证码长度和有效期 → Save

---

### 步骤7：微信扫码登录Authenticator（概念实现）

**目标**：理解扫码登录的核心状态机——生成二维码→轮询扫码状态→扫码成功自动完成认证。

扫码登录状态机的核心实现思路（在`SmsOtpAuthenticator`中新增一个独立Authenticator `WechatScanAuthenticator`）：

```
状态流转：
1. authenticate(): 
   - 生成UUID作为扫码标识 → qrUuid
   - 将qrUuid存入authNotes: authSession.setAuthNote("qr_uuid", qrUuid)
   - 调用微信二维码生成服务，获取二维码图片URL
   - 渲染qr-scan.ftl页面（含二维码图片和轮询JS）
   - context.challenge(scanPage)

2. 微信小程序侧：
   - 用户扫码 → 小程序获取qrUuid和用户OpenID
   - 小程序调用Keycloak自定义REST API: POST /realms/{realm}/qr-scan/callback
     参数: { qrUuid, openId }
   - REST API验证OpenID与政务平台用户的绑定关系
   - 通过后，找到qrUuid对应的AuthenticationSession
   - authSession.setAuthNote("qr_status", "scanned")
   - authSession.setAuthNote("qr_openid", openId)

3. FTL模板中的轮询JS：
   - setInterval(2000)：每2秒发fetch请求到url.loginAction
   - 请求附带参数 action=poll

4. action():
   - 如果formData中action=poll：
     - 读authSession.getAuthNote("qr_status")
     - 如果=="scanned"：返回200告诉前端扫码成功，前端自动提交表单
     - 如果超时（超过120秒）：返回超时状态，前端显示"二维码已过期"
     - 否则：返回"waiting"，前端继续轮询
   - 如果formData中action=submit：
     - 再次确认扫码状态，context.success() 或 context.failure()
```

---

### 可能遇到的坑

1. **authNotes不适合存放大数据**：`authNotes`存储在数据库的`AUTHENTICATION_SESSION_NOTE`表中，value字段有长度限制。二维码图片的base64编码动辄几十KB，不应存入authNotes。正确做法是将二维码图片存到独立的缓存（如Redis）或生成二维码的参数（如UUID），在FTL模板中通过JavaScript动态调用二维码生成库渲染。

2. **短信API异步发送的时序问题**：`authenticate()`中调用`smsClient.send()`后立即`context.challenge()`返回表单——此时短信可能还在移动运营商的网关中排队，尚未送达用户手机。如果用户在10秒内打开短信输入框并提交，可能会出现"刚收到短信就过期了"的体验问题。优化方案：FTL模板中显示"验证码已发送，预计10秒内到达"的提示文字，而非直接展示倒计时。

3. **轮询间隔对服务器压力**：扫码登录的轮询间隔如果设为200ms，1000个并发认证会话将产生每秒5000次HTTP请求。建议轮询间隔1-2秒，并在Keycloak前端（Nginx/反向代理）层面配置`limit_req`限流。

4. **API密钥存储的安全性**：`ProviderConfigProperty.PASSWORD`类型在数据库中并非真正加密——Keycloak使用可逆的简单加密（`org.keycloak.models.utils.StripSecrets`仅做日志脱敏），数据库管理员可以直接读取密钥明文。生产环境建议使用Keycloak的Vault SPI集成（HashiCorp Vault等），将密钥存储与服务配置分离。

---

### 测试验证

**短信OTP完整流程测试**（使用curl模拟登录，或手动浏览器操作）：

```bash
# 1. 确保测试用户已设置手机号属性
curl -X PUT "http://localhost:8080/admin/realms/demo/users/{userId}" \
  -H "Authorization: Bearer {admin_token}" \
  -H "Content-Type: application/json" \
  -d '{"attributes": {"phone_number": ["13800138000"]}}'

# 2. 浏览器访问：http://localhost:8080/realms/demo/protocol/openid-connect/auth
#    → 输入用户名密码 → 自动跳转到短信验证码页面

# 3. 在Mockoon日志中查看是否有POST /api/sms/send请求

# 4. 输入Mockoon中记录的验证码 → 点击验证 → 登录成功

# 5. 连续输入5次错误验证码 → 第6次被锁定

# 6. 快速连续点击"发送验证码" → 第2次被拒绝（20秒限制）
```

---

## 4 项目总结

### 方案对比

| 维度 | 自研SMS Authenticator | 第三方MFA服务（如阿里云IDaaS） | Keycloak内置OTP（TOTP） |
|------|----------------------|-------------------------------|------------------------|
| 开发成本 | 高（需编写Authenticator+Factory+FTL） | 低（配置即可） | 零（开箱即用） |
| 短信费用 | 可控（自建短信通道，仅支付运营商资费） | 高（服务费+短信费打包计费） | 无短信费用 |
| 安全性 | 中等（自研代码需经过安全审计） | 高（专业安全团队维护） | 高（成熟的TOTP算法，RFC 6238） |
| 可定制性 | 极高（完全控制认证流程和UI） | 低（受限于服务商API） | 中等（可自定义OTP有效期和长度） |
| 用户门槛 | 低（短信人人会用） | 低 | 中等（需安装Authenticator App） |
| 离线可用 | 否（依赖网络和短信通道） | 否 | 是（客户端本地计算） |
| Admin Console集成 | 原生支持（通过ProviderConfigProperty） | 额外集成工作 | 原生支持 |

### 适用场景

- **短信/邮件多因素认证**：银行、政务、电商等需要强身份验证且用户不习惯使用Authenticator App的场景
- **外部认证服务集成**：对接第三方人脸识别、声纹识别、数字证书等非标准认证方式——只需实现Authenticator接口并在`action()`中调用外部服务API
- **微信/支付宝扫码登录**：PC端Web应用需要手机扫码完成认证，通过轮询式状态机实现
- **自适应认证中的条件因素**：作为Conditional Authenticator子Flow中的一个步骤——只有来自外网的请求才触发短信验证

不适用场景：对认证延迟极度敏感的实时系统（短信送达有不确定延迟）、完全离线环境、用户群体为低龄/老人且手机号缺失的场景。

### 注意事项

1. **短信成本控制**：在`authenticate()`中务必实现三层防护——单次会话发送间隔（20秒）、每日每手机号上限、连续错误锁定。生产环境中还需增加IP级别的限流（防止更换手机号绕过多手机号限制）。
2. **authNotes的序列化兼容性**：`authNotes`的值全部是`String`类型。如果存入复杂对象（如JSON），注意不要让序列化后的字符串超过数据库字段长度上限。同时，如果Authenticator迭代后修改了`authNotes`的key名称，需做好新旧key的兼容读取。
3. **`configuredFor()`的语义**：它决定了在Alternative组合中该Authenticator是否被考虑。如果手机号缺失且返回`false`，Keycloak会跳过短信Authenticator，尝试下一个Alternative Authenticator——这可以作为"用户无手机号时降级到TOTP"的优雅实现。
4. **FSM状态设计不完善导致的死循环**：`authenticate()` → `challenge()` → `action()` → `challenge()` 是一条可以循环的链路。如果`action()`中输入校验失败后再次`challenge()`返回同一个FTL表单，用户再次提交又进入`action()`——这是正常的重试循环，不是死循环。但如果`action()`中检查到过期后调`challenge()`请求新页面却不重置`authNotes`中的过期时间，用户每次提交都会看到"已过期"，形成卡死。解决方案：过期处理时调用`authenticate()`（重新生成验证码）或调用`context.resetFlow()`（回到密码认证）。

### 常见踩坑经验

**故障案例1：authNotes中的过期时间使用`Date.toString()`存入，再`Date.parse()`取出，导致夏令时切换期间解析失败。** 根因：`Date.toString()`的输出格式依赖JVM默认时区，与`Date.parse()`的预期格式不一致。解决方案：始终使用`System.currentTimeMillis()`（long类型的时间戳）存入`authNotes`，读出后用`Long.parseLong()`解析。

**故障案例2：`requiresUser()`返回`false`导致`context.getUser()`返回`null`。** 根因：短信Authenticator需要读取用户的手机号属性，如果`requiresUser()`返回`false`，Keycloak在Authenticator执行前不强制要求用户已完成身份确认，此时`context.getUser()`可能返回`null`。解决方案：`requiresUser()`必须返回`true`。

**故障案例3：短信平台切换域名后，旧JAR中硬编码的API地址导致短信发送全面失败。** 根因：开发人员在`SmsClient`中硬编码了`apiUrl`常量，忽略了Admin Console配置通道。解决方案：API地址必须通过`context.getAuthenticatorConfig()`从Admin Console读取，绝对不硬编码在源码中。

### 思考题

1. **语音验证码降级**：如何在现网短信验证码Authenticator的基础上，添加"语音验证码"作为备选通道？用户可以在FTL页面上点击"收不到短信？点击获取语音验证码"按钮，触发的`action()`中调用语音平台的API（通过TTS播报验证码）。语音通道的验证码需要独立生成（不能用短信验证码以规避安全风险），且语音平台与短信平台的API地址和密钥需要分别通过Factory配置。请设计FTL模板的交互、`action()`中判断本次请求走语音还是短信通道的逻辑、以及两个通道的配置项在`getConfigProperties()`中如何组织。

2. **短信平台不稳定时的降级设计**：如果短信平台在业务高峰期（如每天上午9:00-10:00）成功率骤降至80%，如何设计降级方案？请从三个层面设计——服务端降级（在`authenticate()`中判断短信API成功率，低于阈值时自动跳过短信验证，在事件日志中记录降级原因，并通知运维团队）、用户端感知（FTL模板中展示"系统繁忙，已为您简化验证流程"的提示）、恢复机制（成功率回升到95%以上后自动恢复短信验证，无需管理员手工干预）。考虑如何在不侵入Authenticator主体逻辑的前提下实现降级策略——比如通过一个独立的Conditional Authenticator在调用短信Authenticator之前判断是否需要降级。
