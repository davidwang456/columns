# 第23章：自定义SPI实战——Required Action扩展

## 1 项目背景

某金融App（以下简称"XX金融"）在通过等保三级测评后，法务部甩出一份2cm厚的合规整改清单。核心要求只有一条：所有用户必须在首次登录时阅读并同意《用户隐私协议》和《风险告知书》，未同意者不得进入系统。更棘手的是，协议每次更新迭代（如从v2.0升级到v2.1）时，所有老用户也必须重新签署，且每次签署必须留下不可篡改的审计记录——签署时间、IP地址、协议版本号，三要素缺一不可。

开发组最初的方案轻车熟路——前端写个弹窗，用户点击"同意"后往localStorage里写一个标记，下次登录检测到标记存在就跳过弹窗。三天后安全测试团队打回来一份致命报告：用户清除浏览器缓存/localStorage后弹窗重新出现但无法阻止用户直接操作DOM移除弹窗遮罩层；更换设备或浏览器后弹窗再次触发但用户可能已经通过API直接获取Token绕过前端；最关键的是——localStorage里的标记只是一条客户端记录，法务部门要求提供"张三于2026年3月15日14:32:18通过IP 10.0.1.5同意了v2.1版隐私协议"这种级别的审计日志时，前端方案两手一摊、无据可查。

问题的本质是：**合规流程必须发生在认证服务端、在Token签发之前**。用户在通过用户名密码验证之后、拿到Access Token之前，Keycloak必须拦截并强制用户完成一系列"必修动作"——这就是Required Action机制。Keycloak内置了配置OTP、更新密码、验证邮箱等标准Required Action，但业务特有的合规需求必须走SPI（Service Provider Interface）自定义扩展。

然而，SPI扩展的门槛并不低。Keycloak的插件体系涉及Provider接口、ProviderFactory工厂、ProviderLoader加载机制三层架构，META-INF/services/的Java SPI注册方式对不熟悉底层机制的开发者来说像黑魔法。FTL（FreeMarker）模板的渲染调试缺乏IDE支持，JAR包部署后的类加载顺序和版本兼容问题更是生产环境中的常见噩梦。本章从零开始，带你一步步实现一个完整的自定义Required Action扩展，让合规流程真正落地。

---

## 2 项目设计——剧本式交锋对话

**小胖**（屏幕上开着Keycloak管理控制台，手边放着一包薯片）：大师，我昨晚打游戏突然悟了。你看啊，进游戏之前弹一个用户协议窗口，不同意就不让你进——这不就是"Required Action"嘛！那为什么还要写SPI这么复杂？在登录页前面加个前端弹窗不就完了？用户点"同意"再提交表单，点"拒绝"就返回首页，多简单！

**大师**（放下手中的马克杯）：小胖，你打的那些游戏的用户协议弹窗，有几个玩家真读了？99%的人直接拖到底部点"同意"。但金融App的合规场景不一样——法务部门需要的是"不可否认的同意记录"，你能拿localStorage里的数据上法庭吗？前端控制的本质问题是**客户端不可信**——浏览器是用户的领地，脚本、存储、网络请求全可以被操纵。Keycloak的Required Action之所以设计在服务端，核心逻辑只有一条：**Token签发前的最后一道闸门**。用户在登录流程中通过了密码验证，但Keycloak不立即签发Token，而是先检查用户是否还有未完成的Required Action。如果有，强制跳转到相关的表单页，表单提交后服务端校验通过才放行。全程用户拿不到Token，前端自然无从绕过。

> **大师技术映射**：游戏登录 → 前端弹窗 = 地铁闸机前面站个保安口头问你"买票没"，可问可不问，绕过去也没记录。Required Action = 旋转闸门，不刷卡（不同意协议）就卡住不动，而且每次刷卡都有后台日志。

---

**小白**（在白板上画了一个流程时序图，标注了几个问号）：我理解了服务端校验的必要性，但SPI这三个字母到底是什么意思？Provider、ProviderFactory、ProviderLoader这三层是什么关系？还有，`requiredActionChallenge()`和`processAction()`这两个方法分别在什么时机被调用？FTL模板是怎么被触发渲染的？

**大师**：问得好，这正是SPI体系最容易让人困惑的三个核心问题。

首先，SPI全称Service Provider Interface，是Java平台内置的一套插件发现机制。核心原理只有一句话：**接口定义在平台层，实现在插件层，运行时通过META-INF/services/目录下的配置文件动态发现和加载实现类**。Keycloak几乎所有的功能——认证器、用户存储、主题、事件监听器——都是SPI实现，这意味着你可以在不修改Keycloak一行源码的前提下，通过添加JAR包来扩展Keycloak的行为。

三层角色的分工是这样的：**Provider接口**（如`RequiredActionProvider`）定义了"能做什么"——`requiredActionChallenge()`是什么时候弹出表单、`processAction()`是表单提交后怎么处理；**ProviderFactory接口**（如`RequiredActionFactory`）定义了"怎么创建"——`create()`方法负责实例化Provider，`getId()`返回Provider的唯一标识字符串，`getDisplayText()`提供管理控制台中显示的名称；**ProviderLoader负责"怎么发现"**——Keycloak启动时扫描classpath下所有`META-INF/services/`目录，根据SPI配置文件找到所有Factory实现类，调用它们的`init()`方法完成注册。

`requiredActionChallenge()`和`processAction()`的执行时机可以映射到HTTP的请求-响应模型：当Keycloak的认证引擎（`AuthenticationManager.nextActionAfterAuthentication()`）判定当前用户需要执行某个Required Action时，调用`requiredActionChallenge()`——此时你应该构造一个Challenge响应（通常是一个表单页面），Keycloak将这个响应返回给浏览器。用户在表单中填写/确认后点击提交，浏览器发POST请求到Keycloak，Keycloak解析表单参数后调用`processAction()`——此时你读取用户的选择，执行业务逻辑，最终调用`context.success()`放行或`context.failure()`拒绝。

FTL模板的渲染机制也按这个思路来：在`requiredActionChallenge()`中，你调用`context.form().createForm("terms-consent.ftl")`，Keycloak内部会查找主题资源路径下的对应FTL文件，用FreeMarker引擎将模板变量（如`username`、`termsVersion`）替换为实际值后生成HTML，返回给浏览器。

> **大师技术映射**：SPI三层架构 = 餐厅的后厨体系。Provider接口 = 菜单（定义了"能做什么菜"）。ProviderFactory = 厨师（知道怎么做菜，需要什么食材）。ProviderLoader = 餐厅经理（根据当天的预定自动安排对应的厨师上岗）。

---

**小胖**（嚼着薯片，凑到白板前）：那万一用户点了同意按钮之后网络断了怎么办？或者用户打开协议页面放了一小时才点同意，这期间会不会超时？还有，这个SPI开发完了打成JAR包，我扔到Keycloak的providers目录里，它怎么就自己加载了？万一我同时扔了两个版本的JAR在里面会不会打架？

**大师**：这两个问题一个关于流程鲁棒性，一个关于类加载机制，都是生产环境中的真实痛点。

先说超时问题。Keycloak的认证流程有全局超时机制——默认的认证会话（Authentication Session）有效期为300秒（5分钟）。如果用户打开协议页面后超过5分钟不做任何操作，认证会话从服务端过期，此时点同意按钮会返回"登录超时，请重新登录"的通用错误页面。这个超时时间可以在Realm Settings → Tokens → **Access Token Lifespan**下的**Client login timeout**中调整。但这里有一个容易被忽略的点：FTL模板渲染只是返回一个HTML表单，表单本身不维护客户端计时器——如果业务要求"必须在60秒内完成签署"，你需要在FTL中嵌入JavaScript的倒计时逻辑，并在`processAction()`中额外校验提交时间是否超出业务窗口。

类加载问题更微妙。Keycloak基于Quarkus框架，Quarkus的类加载器使用"父优先"委托模型。你打成JAR放在`providers/`目录下，Quarkus在启动时通过Jandex索引扫描所有JAR中的bean和SPI配置。如果同一个Factory ID被多个JAR注册（比如你扔了`terms-consent-1.0.jar`和`terms-consent-2.0.jar`两个版本），Quarkus会抛出`AmbiguousResolutionException`——因为两个JAR提供了相同的Provider ID，容器不知道该实例化哪一个。正确的版本管理策略是：**旧版本JAR先删除，再放入新版本JAR，然后执行`kc.sh build`重新构建**。千万别让两个版本的JAR在providers目录下共存。

关于类加载顺序的另一个陷阱：你的SPI依赖的第三方库（比如Apache HttpClient）如果Keycloak本身也带了但版本不同，可能触发`NoSuchMethodError`或`ClassNotFoundException`。解决方案是把不兼容的依赖和你的SPI一起打包成Fat JAR（用maven-assembly-plugin），或者使用Quarkus的`quarkus.class-loading.parent-first-artifacts`配置项调整类加载优先级。

> **大师技术映射**：认证超时 = 银行取号排队，过号不候。双版本JAR冲突 = 一个工位来了两个员工拿着同一张工牌，保安（Quarkus）懵了不知道该放谁进去。Fat JAR隔离 = 每个员工自带工具箱，不跟别人共用，工具版本冲突不存在。

---

**小白**（第二轮）：SPI扩展的版本兼容性怎么处理？比如我们用Keycloak 26.1开发了SPI，未来升级到27.x时会不会直接炸？还有测试策略——总不能每次都部署到真实Keycloak验证吧？有没有更轻量的本地测试方案？

**大师**：版本兼容是SPI扩展生命周期中最大的变量。Keycloak的SPI接口遵循语义版本约定——major版本号变化时接口可能不兼容（比如Keycloak 25.x到26.x期间`RequiredActionProvider`接口新增了`requiredActionChallenge(RequiredActionContext)`的变体签名）。在你升级Keycloak大版本时，必须先检查SPI接口的变更日志（Keycloak官方GitHub的Upgrading Guide），重点看三个变化点：接口方法签名是否变化、Context对象的API是否增减、配置项的默认值是否调整。如果SPI接口没有breaking change，你的JAR通常可以无缝运行；但安全起见，建议在升级Keycloak的同时用新的Keycloak依赖重新编译一次，确保编译时类型检查覆盖所有变更。

关于测试策略，分三层推进：第一层是**单元测试**——你可以在Maven项目中引入`keycloak-server-spi`依赖后，用Mockito模拟`RequiredActionContext`和`KeycloakSession`，直接测试`requiredActionChallenge()`和`processAction()`的逻辑，全程不需要启动Keycloak。第二层是**集成测试**——用Testcontainers启动一个真实的Keycloak容器，通过Admin REST API注册你的SPI并触发认证流程，验证端到端行为。第三层才是**手工验收**——将JAR部署到开发环境，打开浏览器完成一次真实的登录流程，验证FTL模板渲染、表单提交和用户属性写入全部正常。三层递进，越底层越快，越顶层越真实。

---

## 3 项目实战

### 环境准备

| 组件 | 版本/说明 |
|------|----------|
| JDK | 17+ |
| Maven | 3.8+ |
| Keycloak | 26.1.0，基于第2章Docker Compose环境 |
| IDE | VS Code或IntelliJ IDEA |
| curl | API调试工具 |

目标：开发一个自定义Required Action，强制用户在登录后阅读并签署用户协议，记录签署版本、时间和IP到用户属性，作为合规审计凭据。

---

### 步骤1：创建Maven项目结构

**目标**：按Keycloak SPI规范搭建项目骨架。

```bash
# 创建项目目录结构
New-Item -ItemType Directory -Force -Path custom-required-action/src/main/java/com/mycompany/keycloak
New-Item -ItemType Directory -Force -Path custom-required-action/src/main/resources/META-INF/services
New-Item -ItemType Directory -Force -Path custom-required-action/src/main/resources/theme-resources/templates
New-Item -ItemType Directory -Force -Path custom-required-action/src/test/java/com/mycompany/keycloak
```

最终目录结构：

```
custom-required-action/
├── pom.xml
├── src/main/java/com/mycompany/keycloak/
│   ├── TermsConsentRequiredAction.java
│   └── TermsConsentRequiredActionFactory.java
├── src/main/resources/
│   ├── META-INF/services/
│   │   └── org.keycloak.authentication.RequiredActionFactory
│   └── theme-resources/
│       └── templates/
│           └── terms-consent.ftl
└── src/test/java/com/mycompany/keycloak/
    └── TermsConsentRequiredActionTest.java
```

> **提示**：`META-INF/services/`目录下的文件名为SPI接口的全限定类名（`org.keycloak.authentication.RequiredActionFactory`），文件内容是Factory实现类的全限定类名。注意文件名中间的分隔符是`/`而非`.`——`META-INF/services/`是目录，不是文件名的一部分。

---

### 步骤2：配置pom.xml

**目标**：引入Keycloak SPI依赖，配置JAR打包和主题资源输出。

编写 `custom-required-action/pom.xml`：

```xml
<project xmlns="http://maven.apache.org/POM/4.0.0"
         xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
         xsi:schemaLocation="http://maven.apache.org/POM/4.0.0
         http://maven.apache.org/xsd/maven-4.0.0.xsd">
    <modelVersion>4.0.0</modelVersion>

    <groupId>com.mycompany.keycloak</groupId>
    <artifactId>custom-required-action</artifactId>
    <version>1.0.0</version>
    <packaging>jar</packaging>

    <properties>
        <keycloak.version>26.1.0</keycloak.version>
        <maven.compiler.source>17</maven.compiler.source>
        <maven.compiler.target>17</maven.compiler.target>
        <project.build.sourceEncoding>UTF-8</project.build.sourceEncoding>
    </properties>

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

        <!-- 测试依赖 -->
        <dependency>
            <groupId>junit</groupId>
            <artifactId>junit</artifactId>
            <version>4.13.2</version>
            <scope>test</scope>
        </dependency>
        <dependency>
            <groupId>org.mockito</groupId>
            <artifactId>mockito-core</artifactId>
            <version>5.8.0</version>
            <scope>test</scope>
        </dependency>
    </dependencies>

    <build>
        <plugins>
            <plugin>
                <groupId>org.apache.maven.plugins</groupId>
                <artifactId>maven-jar-plugin</artifactId>
                <configuration>
                    <archive>
                        <manifestEntries>
                            <Dependencies>org.keycloak.keycloak-services</Dependencies>
                        </manifestEntries>
                    </archive>
                </configuration>
            </plugin>
        </plugins>
    </build>
</project>
```

> **关键解释**：所有Keycloak依赖声明为`<scope>provided</scope>`，表示编译时需要但运行时由Keycloak容器提供——避免将Keycloak自身类打入你的JAR导致类冲突。`manifestEntries`中的`Dependencies`声明是WildFly时代的遗留配置，Quarkus模式下实际影响较小，但仍建议保留以兼容文档规范。

---

### 步骤3：实现RequiredActionProvider

**目标**：编写核心业务逻辑——检查用户签署状态、渲染协议表单、处理用户的接受/拒绝操作。

编写 `TermsConsentRequiredAction.java`：

```java
package com.mycompany.keycloak;

import jakarta.ws.rs.core.Response;
import org.keycloak.authentication.RequiredActionContext;
import org.keycloak.authentication.RequiredActionProvider;
import org.keycloak.events.EventBuilder;
import org.keycloak.events.EventType;
import org.keycloak.models.KeycloakSession;

public class TermsConsentRequiredAction implements RequiredActionProvider {

    private static final String TERMS_VERSION = "v2.1";

    @Override
    public void requiredActionChallenge(RequiredActionContext context) {
        String consentVersion = context.getUser()
                .getFirstAttribute("terms_consent_version");

        if (TERMS_VERSION.equals(consentVersion)) {
            context.success();
            return;
        }

        Response challenge = context.form()
                .setAttribute("username", context.getUser().getUsername())
                .setAttribute("termsVersion", TERMS_VERSION)
                .createForm("terms-consent.ftl");
        context.challenge(challenge);
    }

    @Override
    public void processAction(RequiredActionContext context) {
        String action = context.getHttpRequest()
                .getDecodedFormParameters()
                .getFirst("terms_action");

        if ("accept".equals(action)) {
            context.getUser().setSingleAttribute(
                    "terms_consent_version", TERMS_VERSION);
            context.getUser().setSingleAttribute(
                    "terms_consent_time",
                    String.valueOf(System.currentTimeMillis()));
            context.getUser().setSingleAttribute(
                    "terms_consent_ip",
                    context.getSession().getContext()
                            .getConnection().getRemoteAddr());

            new EventBuilder(context.getRealm(), context.getSession(),
                    context.getSession().getContext().getConnection())
                    .event(EventType.CUSTOM_REQUIRED_ACTION)
                    .detail("terms_version", TERMS_VERSION)
                    .detail("terms_action", "accepted")
                    .detail("username", context.getUser().getUsername())
                    .success();

            context.success();
        } else if ("decline".equals(action)) {
            new EventBuilder(context.getRealm(), context.getSession(),
                    context.getSession().getContext().getConnection())
                    .event(EventType.CUSTOM_REQUIRED_ACTION)
                    .detail("terms_version", TERMS_VERSION)
                    .detail("terms_action", "declined")
                    .detail("username", context.getUser().getUsername())
                    .error("User declined terms consent");

            context.failure();
        }
    }

    @Override
    public void evaluateTriggers(RequiredActionContext context) {
    }

    @Override
    public RequiredActionProvider create(KeycloakSession session) {
        return this;
    }

    @Override
    public void close() {
    }
}
```

代码拆解说明：

- **`requiredActionChallenge()`**：每次用户进入这个Required Action时调用。首先检查用户属性中是否已存在当前版本协议的同意标记，若存在则直接`context.success()`跳过；否则调用`context.form().createForm("terms-consent.ftl")`渲染FTL模板并通过`context.challenge()`返回给用户。
- **`processAction()`**：用户提交表单后调用。从POST参数中读取`terms_action`的值——`"accept"`表示同意，将协议版本、签署时间戳和客户端IP写入用户属性，并触发审计事件；`"decline"`表示拒绝，记录拒绝事件后调用`context.failure()`阻止Token签发。
- **`evaluateTriggers()`**：Keycloak每次认证时调用此方法判断是否应该触发该Required Action。留空表示不自动触发（通过管理控制台手工分配给用户或通过默认Required Action机制全局启用）。

> **安全提示**：IP地址记录使用了`context.getSession().getContext().getConnection().getRemoteAddr()`，这在有反向代理的环境下获取的是代理IP而非真实客户端IP。生产环境中需要配置Quarkus的`proxy-address-forwarding=true`并从`X-Forwarded-For`头部提取真实IP。

---

### 步骤4：实现RequiredActionFactory

**目标**：编写Factory类，将Provider注册到Keycloak的SPI容器中。

编写 `TermsConsentRequiredActionFactory.java`：

```java
package com.mycompany.keycloak;

import org.keycloak.Config;
import org.keycloak.authentication.RequiredActionFactory;
import org.keycloak.authentication.RequiredActionProvider;
import org.keycloak.models.KeycloakSession;
import org.keycloak.models.KeycloakSessionFactory;

public class TermsConsentRequiredActionFactory
        implements RequiredActionFactory {

    public static final String PROVIDER_ID =
            "terms_consent_required_action";

    @Override
    public RequiredActionProvider create(KeycloakSession session) {
        return new TermsConsentRequiredAction();
    }

    @Override
    public String getDisplayText() {
        return "同意用户协议";
    }

    @Override
    public String getId() {
        return PROVIDER_ID;
    }

    @Override
    public void init(Config.Scope config) {
    }

    @Override
    public void postInit(KeycloakSessionFactory factory) {
    }

    @Override
    public void close() {
    }
}
```

关键说明：

- **`getId()`**：返回Provider的唯一标识符（`PROVIDER_ID`），这个值将在管理控制台的Required Action列表中使用，也在SPI注册时作为provider ID。
- **`getDisplayText()`**：返回管理控制台中显示的中文名称，管理员通过此名称在认证流配置中找到你的Required Action。
- **`create()`**：Keycloak在每个请求中通过Factory创建新的Provider实例（或返回单例，取决于实现）。注意这里创建的是新实例，因为Provider字段可能携带请求级别的状态，不建议共享单例。

---

### 步骤5：创建SPI注册文件

**目标**：通过Java SPI机制告诉Keycloak去何处寻找Factory实现。

编写 `src/main/resources/META-INF/services/org.keycloak.authentication.RequiredActionFactory`：

```
com.mycompany.keycloak.TermsConsentRequiredActionFactory
```

> **关键提示**：文件名必须是SPI接口的全限定类名——`org.keycloak.authentication.RequiredActionFactory`——不能多一个空格、不能少一个字母。文件内容填写你的Factory实现类的全限定类名，可以有多个实现类（每行一个）。这个文件是Java SPI的标准发现机制，Keycloak通过`ServiceLoader`读取并实例化所有注册的Factory。

---

### 步骤6：编写FTL模板

**目标**：创建协议签署页面的FreeMarker模板，用户可以阅读协议内容并选择接受或拒绝。

编写 `src/main/resources/theme-resources/templates/terms-consent.ftl`：

```html
<#import "template.ftl" as layout>
<@layout.registrationLayout displayMessage=false; section>
    <#if section = "header">
        ${msg("termsConsentTitle", termsVersion)}
    <#elseif section = "form">
    <div class="${properties.kcFormGroupClass!}">
        <div class="terms-consent-container" style="max-height:400px;
                overflow-y:auto;border:1px solid #ddd;padding:20px;
                margin-bottom:24px;background:#fafafa;border-radius:8px">
            <h3>用户隐私协议（版本 ${termsVersion}）</h3>
            <p>尊敬的用户（${username}）：</p>
            <p>欢迎使用XX金融平台。为保障您的合法权益，请您仔细阅读以下协议条款：</p>
            <h4>一、信息收集</h4>
            <p>我们将在您使用本服务的过程中收集必要的个人信息，包括但不限于：
            姓名、手机号码、电子邮箱、设备信息、日志信息。</p>
            <h4>二、信息使用</h4>
            <p>我们收集的信息将用于：提供核心金融服务、改善用户体验、
            向您推送个性化的产品和服务信息、完成监管机构的合规要求。</p>
            <h4>三、信息存储与保护</h4>
            <p>您的个人信息将存储于中华人民共和国境内的安全服务器中。
            我们采用业界通行的加密传输和存储技术保护您的信息。</p>
            <h4>四、您的权利</h4>
            <p>您有权随时查询、更正、删除您的个人信息，
            有权撤回对信息收集的同意。</p>
        </div>

        <div class="risk-disclosure" style="border:1px solid #ff9800;
                background:#fff8e1;padding:16px;border-radius:8px;
                margin-bottom:24px">
            <h3 style="color:#e65100">⚠ 风险告知书（版本 ${termsVersion}）</h3>
            <p>金融投资存在风险，请您充分了解以下内容：</p>
            <ul>
                <li>市场风险：投资标的的价格波动可能导致本金损失</li>
                <li>流动性风险：特定产品在短期内可能无法按理想价格变现</li>
                <li>信用风险：交易对手方可能无法履行合约义务</li>
            </ul>
        </div>

        <form id="terms-consent-form" action="${url.loginAction}"
                method="post">
            <div style="display:flex;gap:16px;justify-content:center">
                <button type="submit" name="terms_action" value="accept"
                        class="${properties.kcButtonClass!}
                        ${properties.kcButtonPrimaryClass!}
                        ${properties.kcButtonLargeClass!}"
                        style="background-color:#4caf50;border-color:#4caf50;
                        min-width:160px">
                    ${msg("doAccept")}
                </button>
                <button type="submit" name="terms_action" value="decline"
                        class="${properties.kcButtonClass!}
                        ${properties.kcButtonDefaultClass!}
                        ${properties.kcButtonLargeClass!}"
                        style="min-width:160px">
                    ${msg("doDecline")}
                </button>
            </div>
        </form>
    </div>
    </#if>
</@layout.registrationLayout>
```

模板说明：

- **`${url.loginAction}`**：Keycloak注入的当前认证流程的POST目标URL，表单提交后由Keycloak路由到`processAction()`。
- **`${properties.kcButtonClass!}`**：从父主题继承的CSS类名变量，`!`后缀表示变量不存在时使用空字符串。
- **FTL模板路径问题**：模板文件放在`theme-resources/templates/`下是因为Keycloak的主题资源加载器会扫描此路径。如果你的Keycloak环境使用自定义主题，需要将FTL复制到对应主题的`login/`目录。

---

### 步骤7：消息国际化（可选）

**目标**：为协议页面提供中英文的消息配置。

如果使用自定义主题，在主题的`login/messages/messages_zh-CN.properties`中添加以下键值：

```properties
termsConsentTitle=用户协议与风险告知 - 版本 {0}
doAccept=同意并继续
doDecline=拒绝并退出
```

如果未使用自定义主题，可以在`requiredActionChallenge()`方法中通过`context.form().setAttribute()`将文案直接注入模板上下文，但推荐通过主题的messages机制统一管理国际化文案。

---

### 步骤8：编译打包

**目标**：将项目编译为可部署的JAR包。

```bash
# 在 custom-required-action/ 目录下执行
mvn clean package
```

运行结果：

```
[INFO] BUILD SUCCESS
[INFO] ------------------------------------------------------------------------
[INFO] Total time:  3.245 s
[INFO] Finished at: 2026-05-12T10:23:45+08:00
```

编译成功后，JAR包位于 `target/custom-required-action-1.0.0.jar`。

---

### 步骤9：部署到Keycloak

**目标**：将JAR包放入Keycloak的providers目录并重建配置。

**方式一：Docker卷挂载**（推荐开发环境）

修改 `docker-compose.yml`：

```yaml
services:
  keycloak:
    image: quay.io/keycloak/keycloak:26.1
    container_name: keycloak-dev
    ports:
      - "8080:8080"
    volumes:
      - ./custom-required-action/target/custom-required-action-1.0.0.jar:/opt/keycloak/providers/custom-required-action.jar
    environment:
      KC_BOOTSTRAP_ADMIN_USERNAME: admin
      KC_BOOTSTRAP_ADMIN_PASSWORD: admin
    command: start-dev
```

```bash
# 重新启动Keycloak
docker compose down
docker compose up -d

# 确认SPI已注册
docker logs keycloak-dev 2>&1 | Select-String "terms_consent"
```

**方式二：手动拷贝**（裸机部署）

```bash
cp target/custom-required-action-1.0.0.jar /opt/keycloak/providers/
/opt/keycloak/bin/kc.sh build
/opt/keycloak/bin/kc.sh start
```

> **重要**：Quarkus模式下新增/删除provider JAR后必须执行`kc.sh build`重新构建（开发模式除外，`start-dev`会自动检测classpath变化）。如果只重启Keycloak而不重新build，新的SPI实现不会被Quarkus索引扫描到，管理控制台中不会出现你的Required Action。

---

### 步骤10：在认证流中配置Required Action

**目标**：在管理控制台中将自定义Required Action注册为默认动作，使所有用户在登录时触发。

操作步骤：

1. 登录管理控制台 → 选择目标Realm → 进入 **Authentication** → **Flows** 选项卡。
2. 选择 **Browser** 认证流（或其他你使用的认证流）。
3. 在认证流的表单节点（通常是"Browser - Conditional OTP"子流中的"OTP Form"之后）点击 **Add step**（添加步骤）。
4. 在弹出窗口中搜索"同意用户协议"（即Factory中`getDisplayText()`返回的值），选中后点击 **Add**。
5. 将新添加的步骤拖拽到需要的位置（一般在密码验证之后、OTP验证之前）。
6. 将Required Action的Requirement设置为 **Required**。

也可以通过 **Realm Settings → User Profile → Required Actions** 将"同意用户协议"添加到默认Required Actions列表中，这样所有新用户登录时都会自动触发。

---

### 步骤11：测试验证

**目标**：从新用户登录、老用户跳过、签署记录审计三个维度验证功能完整性。

**验证1：新用户触发协议签署**

```bash
# 创建测试用户
TOKEN=$(curl -s -X POST http://localhost:8080/realms/master/protocol/openid-connect/token \
  -d "client_id=admin-cli&username=admin&password=admin&grant_type=password" | jq -r '.access_token')

curl -X POST http://localhost:8080/admin/realms/demo-realm/users \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"username":"testuser","enabled":true,"credentials":[{"type":"password","value":"Test1234!","temporary":false}]}'
```

在浏览器中访问 `http://localhost:8080/realms/demo-realm/account`，使用`testuser / Test1234!`登录。预期结果：密码验证通过后，浏览器跳转到协议签署页面，显示版本号和风险告知内容。

**验证2：接受与拒绝操作**

- 点击"同意并继续" → 进入系统 → 调用API验证用户属性：

```bash
curl -X GET http://localhost:8080/admin/realms/demo-realm/users?username=testuser \
  -H "Authorization: Bearer $TOKEN" | jq '.[0].attributes'
```

预期输出中应包含：

```json
{
  "terms_consent_version": ["v2.1"],
  "terms_consent_time": ["1747023825000"],
  "terms_consent_ip": ["172.17.0.1"]
}
```

- 用另一个用户登录，点击"拒绝并退出" → 页面显示错误提示，用户无法进入系统，Token未被签发。

**验证3：已同意用户跳过协议**

使用已签署协议的用户再次登录，预期结果：密码验证通过后直接进入系统，不再显示协议页面。因为`requiredActionChallenge()`检测到用户属性中的`terms_consent_version`等于当前`TERMS_VERSION`后直接调用了`context.success()`。

**验证4：协议版本升级后重新触发**

修改`TERMS_VERSION`为`"v2.2"`，重新编译部署，用之前已签署v2.1的用户登录。预期结果：再次弹出协议页面要求重新签署，因为用户属性中存储的版本号与当前版本不匹配。

---

### 可能遇到的坑

1. **META-INF/services文件名错误**：文件名必须是SPI接口的全限定类名`org.keycloak.authentication.RequiredActionFactory`。常见的错误是把文件放进一个叫`services/`的目录但实际上路径不对，或者文件名多了一个`.txt`后缀（Windows隐藏扩展名导致）。验证方法：解压JAR包检查`jar tf custom-required-action-1.0.0.jar | grep services`。

2. **FTL模板找不到**：Keycloak控制台日志中出现`TemplateNotFoundException: terms-consent.ftl not found`。根因是FTL模板的存放路径不正确。模板必须放在主题资源路径的`templates/`子目录下。如果你的JAR中FTL在`theme-resources/templates/`，需要确认Keycloak的主题引擎是否能够扫描到你的JAR中的资源。也可以通过将FTL模板放入Keycloak主题目录（如`themes/base/login/`或自定义主题的`login/`目录下来临时绕过。

3. **Provider依赖Keycloak版本不一致**：如果编译时使用的是Keycloak 26.x的依赖，但运行环境是Keycloak 24.x，会抛出`NoSuchMethodError`或`ClassNotFoundException`。确保`pom.xml`中的`${keycloak.version}`与目标运行环境的版本完全匹配，或者降级依赖版本重新编译。

4. **Quarkus未执行build导致SPI未生效**：将JAR放入providers目录后直接重启Keycloak，但管理控制台的Required Action列表中找不到"同意用户协议"。执行`kc.sh build`后问题解决。开发模式（`start-dev`）会忽略此问题，但生产模式（`start`或`start --optimized`）必须build。

5. **事件日志写入失败**：`EventBuilder().success()`或`error()`调用后，在Admin Console的Events页面找不到对应事件。根因可能是事件存储配置——检查Realm Settings → Events，确保"Save Events"已启用，且`CUSTOM_REQUIRED_ACTION`事件类型未被排除。

---

## 4 项目总结

### 方案对比

| 维度 | SPI扩展（Required Action） | 修改Keycloak源码 | 外部网关/过滤器处理 |
|------|--------------------------|-----------------|-------------------|
| 开发成本 | ⚠️ 中——需理解SPI体系、FTL模板 | ❌ 高——Fork源码，跟踪上游变更 | ✅ 低——在网关层添加拦截逻辑 |
| 安全性 | ✅ 服务端强制，无法绕过 | ✅ 服务端强制 | ⚠️ API路径存在绕过风险 |
| 审计能力 | ✅ 内建Event系统，完整审计日志 | ✅ 同等 | ❌ 需自行实现审计存储 |
| 升级兼容 | ⚠️ 大版本可能需调整SPI接口 | ❌ 每次升级需手动合并冲突 | ✅ 与Keycloak版本解耦 |
| 维护成本 | ⚠️ 中——需跟踪SPI接口变更 | ❌ 高——升级维护工作量大 | ✅ 低——网关层独立维护 |
| 集成深度 | ✅ 与认证流程原生结合 | ✅ 同等 | ❌ 游离于认证体系之外 |

### 适用场景

1. **合规协议签署**：金融、医疗、保险等强监管行业，要求用户在获取系统权限前签署协议并留审计痕迹。
2. **数据采集补充**：用户首次登录后强制完善个人信息（手机号、部门、职位），确保下游应用能获取完整的用户画像。
3. **自定义多因素认证**：集成企业内部的安全验证系统（如自定义OTP算法、硬件UKey校验），在两个内置认证器之间插入自定义验证步骤。
4. **工作流审批通知**：用户登录后展示待审批任务数量、未读消息等，作为进入系统的"信息仪表盘"。
5. **定期安全确认**：每90天强制用户确认安全设置（如恢复码、备用邮箱），确保用户知晓并管理自己的安全凭证。

**不适用场景**：需要持久化多步骤向导（用户中途关闭浏览器后能恢复进度）的场景——Required Action的认证会话过期后所有状态丢失；需要在用户已登录期间动态触发的场景（如检测到异地登录后实时插入二次验证）——Required Action只在认证流程中触发一次，认证完成后无法再次拦截。

### 注意事项

- **SPI版本兼容**：升级Keycloak大版本前，检查`RequiredActionProvider`和`RequiredActionFactory`接口的变更日志。如果新增了方法，你的实现类需要补充；如果删除了方法，编译会直接报错，是好事——至少不会在运行时静默异常。
- **FTL模板调试**：FreeMarker的异常在Keycloak日志中通常表现为`freemarker.core.InvalidReferenceException`或模板渲染失败后的通用500错误页面。调试时在`requiredActionChallenge()`中临时用`context.challenge(context.form().createErrorPage(Status.INTERNAL_SERVER_ERROR))`替换模板渲染，可以快速定位是模板语法问题还是业务逻辑问题。
- **JAR隔离**：不要将Keycloak自身依赖打入JAR（scope保持provided），避免`LinkageError`。如果SPI需要引入Keycloak未带的第三方库，使用maven-assembly-plugin将依赖合并到一个独立命名空间，或使用Quarkus的`quarkus.class-loading.parent-first-artifacts`配置。
- **触发条件设计**：`evaluateTriggers()`方法决定Required Action在什么条件下触发。可以通过`context.getUser().getFirstAttribute()`检查用户状态按条件触发，或完全不触发（靠管理控制台手工分配）。常见的错误是在`evaluateTriggers()`中间接调用了需要事务支持的方法导致并发问题——Keycloak的UserModel修改必须在事务上下文中进行。

### 常见踩坑经验

1. **问题**：部署JAR后重启，Keycloak启动卡死或报`NoClassDefFoundError`。**根因**：JAR中打包了与Keycloak自身冲突的库（如另一版本的Jackson或Hibernate）。**解决**：检查依赖树（`mvn dependency:tree`），确认所有Keycloak依赖scope为provided，第三方库使用maven-shade-plugin重定位包名（relocation）。

2. **问题**：Required Action在生产环境不触发。**根因**：管理员只将它添加到Browser认证流中，但生产环境的认证请求可能走了Direct Grant（Resource Owner Password Credentials）流程或其他自定义认证流。**解决**：确保在所有可能使用的认证流中都添加该Required Action步骤，或者在Realm的默认Required Actions列表中全局配置。

3. **问题**：`processAction()`中对用户属性的写入未持久化。**根因**：某些场景下（如用户存储联邦），`setSingleAttribute()`的修改被缓存在UserModel中但未刷新到后端存储。**解决**：在事务提交后验证属性是否写入成功，必要时使用`KeycloakSession.users().searchForUserByUserAttribute()`查询确认。

### 思考题

1. **多步骤向导实现**：如果需要实现"用户登录后强制完善个人信息（手机号、部门、职位）"，这个Required Action涉及多个表单页（第一步填手机号、第二步选部门、第三步填职位），如何实现多步骤向导？提示：考虑在`requiredActionChallenge()`中根据请求参数（如`step`）返回不同的FTL模板；在`processAction()`中暂存中间步骤的数据到认证会话上下文（`context.getAuthenticationSession().setAuthNote()`）而非直接写入用户属性，所有步骤完成后一次性提交。分析这种方案在"用户填写到第二步时关闭浏览器"场景下的数据一致性问题。

2. **完整审计日志设计**：如何记录用户每次同意协议的完整审计日志（时间、IP、协议版本），满足法务合规审查？提示：除了现有的用户属性记录外，考虑使用Keycloak的Event体系作为审计事件流，将每次签署事件发送到外部审计系统（如Elasticsearch或审计数据库）；同时设计"协议版本差异表"，记录每次版本升级时从旧版本到新版本的变化摘要，使用户签署记录能与具体版本内容一一对应，防止"用户同意了v2.1，但v2.1的协议内容事后被篡改"的法律风险。
