# 第15章：Spring Boot + Keycloak安全集成实战

## 1 项目背景

某公司的核心业务系统正在进行第三代微服务架构升级,技术选型最终敲定为Spring Boot 3 + Vue 3 + Keycloak。一期目标是"用户管理后台"——一个典型的前后端分离项目:前端Vue 3单页应用承载业务交互,后端Spring Boot 3提供RESTful API,Keycloak作为统一认证中心。

技术经理在启动会上强调了一个硬性约束:**必须使用Spring Security OAuth2 Resource Server标准方案,禁止引入已被标记为Deprecated的Keycloak Spring Boot Adapter**。这个约束看似简单,却让团队在初期踩了不少坑。

第一个坑是版本迁移的"断裂感"。旧版Keycloak Adapter(现已停止维护)提供了`keycloak-spring-boot-starter`,只需在`application.yml`中配置`keycloak.realm`、`keycloak.auth-server-url`等属性,就能自动完成OIDC登录流程和Token校验。但Spring Security 6 / Spring Boot 3的标准方案需要开发者显式配置`spring.security.oauth2.resourceserver.jwt.issuer-uri`、`JwtAuthenticationConverter`、`SecurityFilterChain`等一整套链路,概念更底层,配置项更分散。很多从旧版Adater迁移过来的开发者发现:原来一行配置搞定的JWT校验,现在需要手写一个`@Configuration`类。

第二个坑是前后端分离场景下的Token管理混乱。前端拿到access_token后存哪里?localStorage方案简单但面临XSS攻击风险;cookie方案安全但涉及SameSite、HttpOnly等配置,且跨域场景需要特殊处理。Token过期后如何实现"无感刷新"——用户在操作过程中Token自动续期而不打断工作流?refresh_token由前端保管还是后端BFF(Bacｋend for Frontend)保管?团队内部讨论了整整一个下午没有定论。

第三个坑是角色映射的"阻抗失配"。Keycloak的realm_access.roles和resource_access.roles是嵌套JSON结构,Spring Security的`@PreAuthorize("hasRole('admin')")`期望的却是扁平化的`ROLE_`前缀GrantedAuthority。如果`JwtAuthenticationConverter`配置不当,角色名永远对不上,所有权限注解形同虚设。

第四个坑是CORS配置。Vue 3开发环境运行在`localhost:3000`,Spring Boot运行在`localhost:8081`,Keycloak运行在`localhost:8080`——三域跨域,任何一处遗漏`Access-Control-Allow-Origin`都会导致前端请求被浏览器静默拦截,控制台一片红色。团队中有成员在Spring Boot配了CORS,却忘了Keycloak的Token端点也需要Web Origins允许,导致拿到code后换Token时被CORS阻断。

本章将从头到尾演示一个生产级Spring Boot 3 + Keycloak + Vue 3的集成方案,解决以上所有痛点。

---

## 2 项目设计——剧本式交锋对话

**小胖**(举着工牌冲进会议室):大师!我昨天研究了一晚上,终于想明白了——前后端分离的认证,跟咱小区门禁系统一模一样!你看:进小区大门要刷门禁卡(Keycloak登录获取Token),进单元楼还要再刷卡(Sprｉng Security校验Token),最后到了家门口用钥匙开门(业务逻辑判断)。前端拿到Token就像我拿到门禁卡——问题是,为啥进单元楼时还得掏出来刷?Spring Boot怎么知道我有卡的?

**大师**(在白板上画了三道关口):小胖你这个三段比喻非常精准,我借它展开。

第一关——小区大门(Keycloak认证):用户在前端输入账号密码,Keycloak验证后签发JWT Token。这相当于小区保安核实了你的业主身份,给了你一张带防伪水印的门禁卡。

第二关——单元楼门禁(Spring Security Resource Server):每个API请求携带JWT到后端,Spring Security拦截后做两件事——(1)验签名:用Keycloak的公钥验证Token是否被篡改,相当于单元楼门禁的系统扫描你的门禁卡水印;(2)看内容:提取Token中的角色信息(如`platform_admin`),判断你能否进这扇门。等价于门禁系统读取卡中的权限区域——普通业主卡刷不开消防通道。

第三关——家门口(业务授权):`@PreAuthorize`注解做更细粒度的权限控制,比如"只有财务部高级经理可以查看部门薪资报表"。

**小白**(拧开保温杯):那大师,我看了Spring Security 6的官方文档,发现有OAuth2 Client和Resource Server两种配置方式。这两个到底有什么区别?我们后端只负责校验前端传来的Token,应该配哪个?

**大师**:这是Spring Security OAuth2模块最基础也最容易搞混的概念。OAuth2 Client和Resource Server解决的是两个完全不同的问题。

**OAuth2 Client**扮演的是"代表用户去获取Token"的角色。它在用户未登录时,自动重定向到Keycloak登录页,等用户完成认证后,用回调中拿到的authorization_code去Token端点换取access_token和id_token,然后把Token存入SecurityContext——这是传统MVC架构(Thymeleaf/JSP)的做法,登录流程由后端驱动,用户只看到页面跳转。

**Resource Server**扮演的是"你带着Token来,我检查Token"的角色。它不做登录、不发起重定向、不保管Session,只做一件事——从每个API请求的`Authorization: Bearer <token>`头中提取JWT,验证签名和有效期,然后将JWT中的claims转换为Spring Security的Authentication对象。这正是前后端分离场景下后端应扮演的角色——一个纯粹的无状态API网关。

如果你的Spring Boot应用是一个RESTful API且前端已经通过Keycloak完成了登录并拿到了Token,那么你**只需要Resource Server配置,不需要OAuth2 Client**。两者若混用,Spring Security在Filter Chain中既尝试OAuth2登录(基于Session),又尝试JWT校验(无状态),会导致行为混乱——这就是很多开发者踩过的"配置了Resource Server但每次请求都被302重定向到Keycloak登录页"的坑。

> **大师技术映射**:OAuth2 Client → 你去办门禁卡的前台,需要提供身份证明(用户名密码)才能拿到卡。Resource Server → 各单元楼的门禁刷卡器,只负责验证卡的真伪和权限,不关心卡是怎么办出来的。

---

**小胖**:那我懂了!但application.yml里那些配置项到底是啥意思?`issuer-uri`、`jwk-set-uri`,还有`token-uri`,每个指向什么端点?

**大师**(在白板上列出完整URL树):这些配置背后是一套标准化的OIDC Discovery协议。以我们Keycloak的Realm `demo-realm`为例,访问`http://localhost:8080/realms/demo-realm/.well-known/openid-configuration`就能看到所有端点:

```
issuer (issuer-uri):              http://localhost:8080/realms/demo-realm
                                  签发者标识,JWT的iss claim必须与此一致,否则校验失败

jwks_uri (jwk-set-uri):           .well-known/openid-configuration中自动发现
                                  返回JSON Web Key Set——RSA公钥列表,JWT签名验证的依据

token_endpoint (token-uri):       有OAuth2 Client时使用,用code换Token的HTTP端点

authorization_endpoint:            /protocol/openid-connect/auth——授权码流程的起始URL
introspection_endpoint:            /protocol/openid-connect/token/introspect——Token内省
userinfo_endpoint:                 /protocol/openid-connect/userinfo——获取用户信息
```

核心原理是:**当你只配置了`issuer-uri`,Spring Security会自动追加`/.well-known/openid-configuration`路径去发现所有其他端点**(包括`jwk-set-uri`),这就是OIDC Discovery的价值——一行配置替代五六行。但你也可以显式指定`jwk-set-uri`来覆盖自动发现的结果,这在Keycloak使用自定义域名或前置网关时非常有用——比如内部网络通过`http://keycloak-internal:8080`访问但Issuer对外暴露为`https://auth.company.com`。

**小白**:那Spring Security拿到JWT后,是每次请求都去Keycloak校验,还是在本地用公钥验签?

**大师**:这正是Resource Server的两种Token校验策略,各有适用场景。

**方案一:本地JWT验签(推荐)**——Spring Security从Keycloak的jwks_uri端点拉取公钥,缓存到内存中,每次API请求直接用本地公钥验证JWT的签名和有效期,**不发出任何网络请求**。公钥会按`Cache-Control`响应头的指示定期刷新(默认每5分钟)。这是性能最优的方案,也是前后端分离场景的标准做法。配置方式就是本章实战部分的`jwt.issuer-uri`方案。

**方案二:远程Token内省(Introspection)**——每次API请求,Resource Server都调Keycloak的introspection端点(`/token/introspect`)确认Token是否有效。Keycloak查数据库、校验收回状态、返回Token元信息。好处是Token被管理员Revoke后能**立即感知**(本地验签只知道Token过期时间到没到);代价是**每个API请求多出一次网络往返**,延迟增加20-50ms,且Keycloak成为性能瓶颈。只有在高安全等级场景(如金融交易)需要实时感知Token吊销时,才会选择内省模式。

> **大师技术映射**:本地JWT验签 → 用门禁卡系统的本地数据库离线验证,速度快但吊销信息同步有延迟。远程内省 → 每次刷卡都连线安保中心(Keycloak)查询"这张卡有没有被挂失",实时准确但网络延迟增加。

---

**小胖**(第二轮,掏出手机给大师看一段代码):大师你看,我昨天试着配了`@PreAuthorize("hasRole('admin')")`,但明明我Keycloak用户有admin角色,后端却一直返回403!我在调试信息里发现Spring Security拿到的角色不是`ROLE_admin`而是`SCOPE_openid`——这不是牛头不对马嘴吗?

**大师**:这是**JWT角色映射的第一大坑**。Spring Security的`hasRole()`方法会自动加上`ROLE_`前缀,所以`hasRole('admin')`实际匹配的是`ROLE_admin`。问题在于,默认的`JwtAuthenticationConverter`会去读JWT的`scope`或`scp` claim来构建角色列表——你Token里的角色明明在`realm_access.roles`这个嵌套JSON结构里,它却读错了地方。

Keycloak的JWT Payload结构是这样的:

```json
{
  "realm_access": {
    "roles": ["user", "platform_admin"]
  },
  "resource_access": {
    "oms-backend": {
      "roles": ["reader", "writer"]
    }
  },
  "scope": "openid profile email"
}
```

你需要自定义`JwtGrantedAuthoritiesConverter`,告诉它读`realm_access.roles`而不是默认的`scope`:

```java
grantedAuthorities.setAuthorityPrefix("ROLE_");
grantedAuthorities.setAuthoritiesClaimName("realm_access.roles");
```

这样Keycloak的角色`platform_admin`就会被转换为`ROLE_platform_admin`,然后`@PreAuthorize("hasRole('platform_admin')")`就能正确匹配了。如果你还想用`resource_access.oms-backend.roles`里的角色,可以写一个更复杂的converter把两者合并。

**小白**:那前后端的Token传递方案呢?我看了很多文章,有的说存localStorage,有的说存cookie,有的还用BFF做Token Relay——到底哪种才算"正确"?

**大师**:没有唯一的正确答案,只有最适合场景的方案,我画张表:

| 方案 | 安全性 | 实现复杂度 | Token刷新 | 适合场景 |
|------|--------|-----------|----------|---------|
| **localStorage + Authorization头** | 中等(XSS可读) | 低 | 前端控制,`setInterval`轮询刷新 | 纯SPA,API同域或已配置CORS |
| **HttpOnly Cookie** | 高(XSS不可读) | 中 | 后端写入Cookie,前端无感知 | 同域下的前后端分离,需要BFF |
| **BFF Token Relay** | 最高(Token不出浏览器) | 高 | BFF统一管理refresh_token | 微前端/多后端/高安全等级 |

对于本章的"用户管理后台"这类管理后台项目,推荐**localStorage + Axios拦截器**方案——实现简单、调试方便,前提是做好XSS防护(Content-Security-Policy + 输入消毒)。如果是面向公网的C端应用(银行、电商),则建议BFF模式:Token只存在于BFF服务端Session中,前端只持有一个Session Cookie,由BFF转发API请求时代入Token——前端完全接触不到Token。

**小胖**(突然想起来):那前端Token过期怎么办?我们做用户管理后台,总不能用户在填写表单时突然被弹到登录页,刚填的数据全丢了吧?

**大师**:这就是"无感刷新"(Silent Refresh)要解决的问题。keycloak-js适配器内置了一个机制:定时调用`keycloak.updateToken(minValidity)`——它会检查access_token的剩余有效期,如果不够(比如不足30秒),就用refresh_token去Keycloak静默换取新Token。整个过程中前端无弹窗、无跳转、用户无感知。

但有两个常见陷阱:一是**iframe渲染**——keycloak-js默认在页面中插入一个隐藏iframe来检查SSO状态,如果前端路由配置为history模式且没有`silent-check-sso.html`文件,会产生控制台报错。关闭方案是设置`checkLoginIframe: false`。二是**refresh_token过期**——refresh_token也有自己的有效期,一旦过期,`updateToken()`会抛异常,此时需要调用`keycloak.login()`让用户重新认证。可以在catch块中先保存当前表单数据到sessionStorage,重新登录后恢复,实现"用户回到原地"的体验。

> **大师技术映射**:无感刷新 → 游乐场通票快过期时,凭闸机记录自动续票,你还在排队享受项目,不会被打断。只有完全过期(票被撕毁)才需要重新买票。

---

**小胖**(第三轮):大师,那微服务场景呢?假如我们有OMS后端、CMS后端两个微服务,它们之间的调用(Dubbo/gRPC)也需要认证吗?还是说内部调用可以裸奔?

**大师**:内部调用绝对不能裸奔。微服务间的认证有两种成熟方案:

**方案一:Service Account(Client Credentials Grant)——** 在Keycloak中为每个后端微服务注册为Confidential客户端,分配一个Client ID和Client Secret。微服务启动时用Client Credentials换取一个JWT(不包含用户上下文,只有service-name等相关claims),调用下游时携带在`Authorization`头中。下游的Resource Server同样配置JWT校验,就能认证请求来源是合法的内部服务。

**方案二:Token Relay(令牌中继)——** 上游服务把接收到的用户JWT原样传递给下游。这要求下游也信任同一个Keycloak Realm签发的Token。Spring Cloud Gateway或BFF层可以自动完成Token Relay,下游服务既能验证调用者身份(User),也能验证上游服务身份(Client)。

对于本章的一期项目(单体Spring Boot API),暂时不深入微服务间的认证。但架构上要预留扩展点——在SecurityConfig中明确Resource Server模式,不依赖Session,确保未来拆分为微服务时,Token校验逻辑可以直接复制到每个服务。

**小白**:最后一个问题:前端Vue 3是纯静态部署,Token存在localStorage里,CSRF攻击还有效吗?要不要禁用Spring Security的CSRF?

**大师**:CSRF攻击的前提是浏览器自动携带Cookie向目标站点发起非自愿请求。前后端分离场景中,API请求通过JavaScript设置`Authorization: Bearer <token>`头来携带Token,**不依赖Cookie做认证凭证**。因此,传统的CSRF攻击(利用Cookie自动发送)对这种架构完全无效。Spring Security的CSRF保护默认基于Cookie中的CSRF Token校验,在你这种场景下不仅没用,还会导致POST/PUT/DELETE请求被拦截——所以本章实战的SecurityConfig中明确禁用了CSRF。但如果你后续加入了BFF层并使用了Session Cookie做认证,就需要重新评估CSRF防护方案。

> **大师技术映射**:CSRF Protection → 老式食堂窗口:你点菜后必须出示原来的取餐小票才能取餐,防止别人冒充你取餐。而刷工卡直接消费的系统,卡在你手里,根本不存在"冒充取餐"的问题。

---

## 3 项目实战

### 环境准备

| 组件 | 版本/说明 |
|------|----------|
| Keycloak | 26.x,基于第2章环境,Realm=**demo-realm**,运行在`localhost:8080` |
| JDK | 17+(Spring Boot 3.x最低要求) |
| Maven | 3.8+ |
| Node.js | 18+ |
| Spring Boot | 3.3+ |
| Vue 3 | 3.4+ |
| keycloak-js | 26.x(与Keycloak服务端版本一致) |

前置准备:确认第4章创建的`oms-frontend`(Public客户端,用于Vue 3前端)和`oms-backend`(Confidential客户端,用于Service Account场景,本章可选)已在demo-realm中配置。确认Keycloak服务已启动。

### 步骤1:创建Spring Boot 3项目骨架

**目标**:搭建Spring Boot项目,引入OAuth2 Resource Server和安全依赖。

使用Spring Initializr或在已有项目中添加以下Maven依赖:

```xml
<!-- pom.xml 关键依赖 -->
<parent>
    <groupId>org.springframework.boot</groupId>
    <artifactId>spring-boot-starter-parent</artifactId>
    <version>3.3.5</version>
</parent>

<dependencies>
    <!-- Web层,提供REST API -->
    <dependency>
        <groupId>org.springframework.boot</groupId>
        <artifactId>spring-boot-starter-web</artifactId>
    </dependency>

    <!-- Spring Security核心 -->
    <dependency>
        <groupId>org.springframework.boot</groupId>
        <artifactId>spring-boot-starter-security</artifactId>
    </dependency>

    <!-- OAuth2 Resource Server:JWT解码与校验 -->
    <dependency>
        <groupId>org.springframework.boot</groupId>
        <artifactId>spring-boot-starter-oauth2-resource-server</artifactId>
    </dependency>
</dependencies>
```

依赖关系说明:这三个starter形成一条完整的认证链路——`spring-boot-starter-web`提供Controller和Servlet容器;`spring-boot-starter-security`提供Filter Chain、AuthenticationManager等安全基础设施;`spring-boot-starter-oauth2-resource-server`在Security基础上叠加了JWT Decoder、Bearer Token解析等OAuth2专用组件。

**注意**:不需要引入`keycloak-spring-boot-starter`或任何Keycloak专用适配器——Spring Security 6的OAuth2标准模块已完全覆盖Keycloak集成。

### 步骤2:配置Resource Server

**目标**:在application.yml中配置JWT校验参数,告诉Spring Security如何验证Token。

```yaml
# src/main/resources/application.yml
spring:
  security:
    oauth2:
      resourceserver:
        jwt:
          issuer-uri: http://localhost:8080/realms/demo-realm
          # issuer-uri是核心配置,Spring Security会自动发现jwks_uri等端点
          # 如果你的Keycloak自定义了域名或前置了网关,可额外显式指定:
          # jwk-set-uri: http://localhost:8080/realms/demo-realm/protocol/openid-connect/certs

server:
  port: 8081

# 自定义Keycloak配置(用于业务代码中引用,非Spring Security强制要求)
keycloak:
  realm: demo-realm
  auth-server-url: http://localhost:8080
  resource: oms-backend
```

**关键配置解析**:
- `issuer-uri`:定义JWT签发者。Spring Security启动时会访问`{issuer-uri}/.well-known/openid-configuration`,自动发现jwks_uri、token_endpoint等端点地址。JWT Payload中的`iss` claim必须与此值完全一致,包括末尾不能带斜杠——`http://localhost:8080/realms/demo-realm/`会导致`iss` claim不匹配,校验直接失败。
- `jwk-set-uri`:当Issuer URI对应的OIDC Discovery端点不可达(如网络隔离)或需要覆盖时,才显式配置。大多数场景只需要`issuer-uri`即可。

### 步骤3:编写Security配置类

**目标**:自定义Spring Security的安全策略——哪些路径需要什么角色、如何从JWT中提取角色、如何配置CORS。

```java
// src/main/java/com/company/oms/config/SecurityConfig.java
package com.company.oms.config;

import jakarta.servlet.http.HttpServletResponse;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.security.config.annotation.method.configuration.EnableMethodSecurity;
import org.springframework.security.config.annotation.web.builders.HttpSecurity;
import org.springframework.security.config.annotation.web.configuration.EnableWebSecurity;
import org.springframework.security.config.annotation.web.configurers.AbstractHttpConfigurer;
import org.springframework.security.config.http.SessionCreationPolicy;
import org.springframework.security.oauth2.server.resource.authentication.JwtAuthenticationConverter;
import org.springframework.security.oauth2.server.resource.authentication.JwtGrantedAuthoritiesConverter;
import org.springframework.security.web.SecurityFilterChain;
import org.springframework.web.cors.CorsConfiguration;
import org.springframework.web.cors.CorsConfigurationSource;
import org.springframework.web.cors.UrlBasedCorsConfigurationSource;

import java.util.List;

@Configuration
@EnableWebSecurity
@EnableMethodSecurity  // 启用@PreAuthorize注解支持
public class SecurityConfig {

    @Bean
    public SecurityFilterChain filterChain(HttpSecurity http) throws Exception {
        http
            // 1. 路径权限规则
            .authorizeHttpRequests(auth -> auth
                .requestMatchers("/api/public/**").permitAll()
                .requestMatchers("/api/admin/**").hasRole("platform_admin")
                .requestMatchers("/api/user/**").hasAnyRole("user", "platform_admin")
                .anyRequest().authenticated()
            )
            // 2. 配置Resource Server -> JWT模式
            .oauth2ResourceServer(oauth2 -> oauth2
                .jwt(jwt -> jwt
                    .jwtAuthenticationConverter(jwtAuthenticationConverter())
                )
            )
            // 3. CORS配置
            .cors(cors -> cors.configurationSource(corsConfigurationSource()))
            // 4. 前后端分离API不需要CSRF
            .csrf(AbstractHttpConfigurer::disable)
            // 5. 无状态会话策略——不创建HttpSession
            .sessionManagement(session ->
                session.sessionCreationPolicy(SessionCreationPolicy.STATELESS)
            )
            // 6. 未授权时返回401而非302重定向
            .exceptionHandling(ex -> ex
                .authenticationEntryPoint((request, response, authException) ->
                    response.sendError(HttpServletResponse.SC_UNAUTHORIZED, "Unauthorized"))
            );

        return http.build();
    }

    /**
     * 自定义角色映射:将Keycloak JWT中的realm_access.roles
     * 转换为Spring Security的ROLE_前缀GrantedAuthority
     */
    @Bean
    public JwtAuthenticationConverter jwtAuthenticationConverter() {
        JwtGrantedAuthoritiesConverter grantedAuthorities =
            new JwtGrantedAuthoritiesConverter();
        // 角色前缀:hasRole("admin")匹配的是ROLE_admin
        grantedAuthorities.setAuthorityPrefix("ROLE_");
        // 指定从JWT的哪个claim提取角色
        // Keycloak的角色存储在realm_access.roles(嵌套JSON)
        grantedAuthorities.setAuthoritiesClaimName("realm_access.roles");

        JwtAuthenticationConverter converter = new JwtAuthenticationConverter();
        converter.setJwtGrantedAuthoritiesConverter(grantedAuthorities);
        return converter;
    }

    /**
     * CORS配置:允许Vue 3前端跨域请求
     */
    @Bean
    public CorsConfigurationSource corsConfigurationSource() {
        CorsConfiguration config = new CorsConfiguration();
        // 允许的前端域名(生产环境替换为实际域名)
        config.setAllowedOrigins(List.of("http://localhost:3000"));
        config.setAllowedMethods(List.of("GET", "POST", "PUT", "DELETE", "OPTIONS"));
        config.setAllowedHeaders(List.of("*"));
        config.setAllowCredentials(true);

        UrlBasedCorsConfigurationSource source = new UrlBasedCorsConfigurationSource();
        source.registerCorsConfiguration("/**", config);
        return source;
    }
}
```

**关键设计决策说明**:
- `SessionCreationPolicy.STATELESS`:这是前后端分离架构的核心配置。设置为STATELESS后,Spring Security不会创建或使用`HttpSession`,每个请求都是独立的,完全依赖JWT进行认证——实现了真正的无状态REST API。
- `exceptionHandling`:默认情况下,Spring Security在未认证时返回302重定向到登录页。前后端分离场景中,前端期望的是401状态码。这个配置确保API返回统一的HTTP错误码而非HTML重定向。

### 步骤4:编写测试API

**目标**:创建三个层级的API端点,验证公开访问、用户权限、管理员权限的鉴权效果。

```java
// src/main/java/com/company/oms/controller/UserController.java
package com.company.oms.controller;

import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.Map;

@RestController
@RequestMapping("/api")
public class UserController {

    /**
     * 公开端点:不需要任何认证
     */
    @GetMapping("/public/health")
    public Map<String, String> health() {
        return Map.of("status", "ok", "timestamp", java.time.Instant.now().toString());
    }

    /**
     * 用户端点:需要user或platform_admin角色
     * @AuthenticationPrincipal 自动注入当前请求的JWT对象
     */
    @GetMapping("/user/profile")
    @PreAuthorize("hasAnyRole('user', 'platform_admin')")
    public Map<String, Object> profile(@AuthenticationPrincipal Jwt jwt) {
        return Map.of(
            "username", jwt.getClaimAsString("preferred_username"),
            "email", jwt.getClaimAsString("email"),
            "roles", jwt.getClaimAsMap("realm_access").get("roles"),
            "subject", jwt.getSubject()
        );
    }

    /**
     * 管理员端点:仅platform_admin角色可访问
     */
    @GetMapping("/admin/users")
    @PreAuthorize("hasRole('platform_admin')")
    public Map<String, Object> adminUsers(@AuthenticationPrincipal Jwt jwt) {
        return Map.of(
            "message", "Admin access granted",
            "admin_user", jwt.getClaimAsString("preferred_username"),
            "access_time", java.time.Instant.now().toString()
        );
    }
}
```

**注解机制说明**:
- `@PreAuthorize("hasRole('ROLE_NAME')")`:Spring Security在方法执行前校验。注意写的是`platform_admin`而非`ROLE_platform_admin`——`hasRole()`自动加前缀。
- `@AuthenticationPrincipal Jwt jwt`:直接注入解析后的JWT对象,可以读取任意claim信息(用户名、邮箱、自定义属性等)。

### 步骤5:编写Vue 3前端集成

**目标**:实现前端Keycloak登录初始化、Axios拦截器自动带Token、Token自动刷新。

```javascript
// src/auth.js —— Keycloak JS Adapter配置
import Keycloak from 'keycloak-js';

const keycloak = new Keycloak({
    url: 'http://localhost:8080',
    realm: 'demo-realm',
    clientId: 'oms-frontend'
});

export default keycloak;
```

```javascript
// src/main.js —— Vue3应用初始化
import { createApp } from 'vue';
import axios from 'axios';
import App from './App.vue';
import keycloak from './auth';
import router from './router';

keycloak.init({
    onLoad: 'login-required',
    checkLoginIframe: false  // 禁用iframe SSO检查,避免silent-check-sso.html报错
}).then(authenticated => {
    if (!authenticated) {
        window.location.reload();
        return;
    }

    const app = createApp(App);

    // 全局注入keycloak实例,所有组件可通过inject访问
    app.provide('keycloak', keycloak);

    app.use(router);
    app.mount('#app');

    // 配置Axios拦截器:每个请求自动携带Bearer Token
    axios.interceptors.request.use(config => {
        if (keycloak.token) {
            config.headers.Authorization = `Bearer ${keycloak.token}`;
        }
        return config;
    }, error => Promise.reject(error));

    // 全局401处理:Token过期时尝试刷新或重新登录
    axios.interceptors.response.use(
        response => response,
        async error => {
            if (error.response?.status === 401) {
                try {
                    await keycloak.updateToken(30);
                    // 刷新成功,用新Token重试原请求
                    error.config.headers.Authorization = `Bearer ${keycloak.token}`;
                    return axios.request(error.config);
                } catch {
                    keycloak.login();
                }
            }
            return Promise.reject(error);
        }
    );

    // Token自动刷新:每60秒检查一次有效期
    setInterval(() => {
        keycloak.updateToken(60).catch(() => {
            console.warn('Token refresh failed, redirecting to login...');
            keycloak.login();
        });
    }, 60000);

}).catch(err => {
    console.error('Keycloak initialization failed:', err);
});
```

```javascript
// src/router/index.js —— 路由守卫(可选,基于角色的页面控制)
import { createRouter, createWebHistory } from 'vue-router';
import keycloak from '@/auth';

const routes = [
    {
        path: '/',
        component: () => import('@/views/Dashboard.vue')
    },
    {
        path: '/admin',
        component: () => import('@/views/AdminPanel.vue'),
        meta: { requiresRole: 'platform_admin' }
    },
    {
        path: '/profile',
        component: () => import('@/views/UserProfile.vue'),
        meta: { requiresRole: 'user' }
    }
];

const router = createRouter({
    history: createWebHistory(),
    routes
});

router.beforeEach((to, from, next) => {
    const requiredRole = to.meta.requiresRole;
    if (requiredRole && !keycloak.hasRealmRole(requiredRole)) {
        next('/'); // 无权限的用户重定向到首页
    } else {
        next();
    }
});

export default router;
```

### 步骤6:测试验证

**目标**:使用curl逐层验证公开访问、未授权访问、授权用户访问、权限不足的完整鉴权链路。

```bash
# 测试1:公开端点(无需Token)
curl -s http://localhost:8081/api/public/health | jq .
# 预期输出:
# {
#   "status": "ok",
#   "timestamp": "2026-05-12T10:30:00Z"
# }

# 测试2:未授权访问(无Token)——应返回401
curl -s -o /dev/null -w "%{http_code}" http://localhost:8081/api/user/profile
# 预期输出: 401

# 测试3:获取Token(使用Resource Owner Password Grant,仅测试用)
TOKEN_RESP=$(curl -s -X POST \
  "http://localhost:8080/realms/demo-realm/protocol/openid-connect/token" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "client_id=oms-backend" \
  -d "client_secret=<YOUR_CLIENT_SECRET>" \
  -d "username=testuser" \
  -d "password=test123" \
  -d "grant_type=password")

ACCESS_TOKEN=$(echo "$TOKEN_RESP" | jq -r '.access_token')
echo "Token acquired: ${ACCESS_TOKEN:0:50}..."

# 测试4:正常用户访问(带有效Token)
curl -s http://localhost:8081/api/user/profile \
  -H "Authorization: Bearer $ACCESS_TOKEN" | jq .
# 预期输出:
# {
#   "username": "testuser",
#   "email": "testuser@company.com",
#   "roles": ["user"],
#   "subject": "a1b2c3d4-..."
# }

# 测试5:管理员端点——普通用户无权访问,应返回403
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" \
  http://localhost:8081/api/admin/users \
  -H "Authorization: Bearer $ACCESS_TOKEN")
echo "Admin endpoint HTTP status: $HTTP_CODE"
# 预期输出: 403

# 测试6:使用管理员账号Token访问管理端点
ADMIN_TOKEN=$(curl -s -X POST \
  "http://localhost:8080/realms/demo-realm/protocol/openid-connect/token" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "client_id=oms-backend" \
  -d "client_secret=<YOUR_CLIENT_SECRET>" \
  -d "username=adminuser" \
  -d "password=admin123" \
  -d "grant_type=password" | jq -r '.access_token')

curl -s http://localhost:8081/api/admin/users \
  -H "Authorization: Bearer $ADMIN_TOKEN" | jq .
# 预期输出:
# {
#   "message": "Admin access granted",
#   "admin_user": "adminuser",
#   "access_time": "2026-05-12T10:31:00Z"
# }

# 测试7:验证JWT被篡改后校验失败
MODIFIED_TOKEN="${ACCESS_TOKEN}xyz"  # 故意破坏Token
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" \
  http://localhost:8081/api/user/profile \
  -H "Authorization: Bearer $MODIFIED_TOKEN")
echo "Tampered token HTTP status: $HTTP_CODE"
# 预期输出: 401
```

### 可能遇到的坑

**1. jwtAuthenticationConverter中claim路径写错**

最常见的错误是`setAuthoritiesClaimName("realm_roles")`。Keycloak的角色存储在`realm_access.roles`(注意中间是点号分隔的嵌套路径,不是下划线)。正确的写法是`"realm_access.roles"`——Spring Security会自动解析嵌套JSON路径。

**2. CORS配置与Keycloak Web Origins不一致**

这是一个"三端CORS"问题:Vue 3(`localhost:3000`) → Keycloak(`localhost:8080`)用于认证,Vue 3(`localhost:3000`) → Spring Boot(`localhost:8081`)用于API调用。两处都需要正确配置CORS:在Keycloak的Client → Settings → Web Origins中添加`http://localhost:3000`;在Spring Security中配置`CorsConfigurationSource`。任缺一处,前端控制台就会出现经典的"No 'Access-Control-Allow-Origin' header is present on the requested resource"错误。

**3. issuer-uri末尾带斜杠导致iss claim不匹配**

`issuer-uri`配置的是字符串精确匹配,不是URL前缀匹配——`http://localhost:8080/realms/demo-realm/`(带末尾斜杠)和JWT中的`iss: "http://localhost:8080/realms/demo-realm"`(不带末尾斜杠)会被视为不同值,校验直接失败。而Spring Security的报错信息非常隐晦——"An error occurred while attempting to decode the Jwt: The iss claim is not valid"——不提示具体是什么不匹配,排Debug费时费力。

**4. keycloak-js的silent-check-sso.html文件路径问题**

keycloak-js默认在页面中插入一个不可见的iframe,加载`{keycloak-url}/realms/{realm}/protocol/openid-connect/3p-cookies/step1.html`来检测第三方Cookie是否可用。某些部署环境下(如反向代理、CDN),这个iframe的src路径与实际Keycloak地址不匹配,导致控制台报错且checkLoginIframe超时。如果不需要这个特性,设置`checkLoginIframe: false`直接关闭。

**5. Token刷新失败时的用户体验处理**

当`keycloak.updateToken()`失败时(通常是refresh_token也过期了),需要引导用户重新登录。一个常见错误是直接调用`keycloak.login()`——这会导致当前页面被清空,用户正在编辑的表单数据全部丢失。更好的做法是先弹出提示"登录已过期,请保存数据后重新登录",或在调用`login()`前自动将表单数据存入sessionStorage,登录回来后恢复。

---

## 4 项目总结

### 方案对比

| 维度 | Spring Security标准方案(本章) | Keycloak Adapter(已废弃) | 自建Spring Security |
|------|----------------------------|-------------------------|-------------------|
| 标准化程度 | ✅ OAuth2/OIDC标准,无厂商锁定 | ❌ Keycloak私有配置 | 可标准可自定义 |
| 版本兼容性 | ✅ Spring Boot 3.x原生支持 | ❌ 不支持Spring Boot 3 | 需手动升级 |
| 配置复杂度 | 中等(需理解JWT Converter) | 低(开箱即用) | 高(自己写认证逻辑) |
| 可定制性 | ✅ 完整控制Filter Chain | 受限于Adapter行为 | ✅ 完全自由 |
| 未来维护 | ✅ Red Hat/Spring社区长期支持 | ❌ 2023年停止维护 | 团队自担 |
| Token校验性能 | ✅ 本地JWT验签,无网络开销 | ✅ 本地JWT验签 | 取决于实现 |
| 多Realm支持 | 需自定义 | 内置支持 | 需自定义 |

### 适用场景

1. **Spring Boot微服务集群**:每个服务都是独立的Resource Server,统一校验Keycloak签发的JWT,不依赖Session复制。
2. **前后端分离项目**:Vue/React前端通过keycloak-js完成认证,后端Spring Boot作为无状态API网关。
3. **API Gateway鉴权**:Spring Cloud Gateway配置为Resource Server,在网关层统一校验Token并路由到下游服务。
4. **BFF架构**:Backend for Frontend层做Token管理,前端只接触Session Cookie,Token对外不可见。

### 不适用场景

- **传统MVC应用**(Thymeleaf/JSP):应使用Spring Security的OAuth2 Client模式,由后端驱动完整的登录→回调→Token获取流程,而非本章的Resource Server模式。
- **纯静态站点**:无后端API的场景不需要Spring Security。

### 注意事项

1. **放弃Keycloak Adapter,拥抱标准方案**:Keycloak官方已停止维护`keycloak-spring-boot-starter`和`keycloak-spring-security-adapter`。Spring Security 6提供的`spring-boot-starter-oauth2-resource-server`和`spring-boot-starter-oauth2-client`是官方推荐替代方案,与Keycloak完全兼容。
2. **Token刷新策略**:access_token建议有效期5-15分钟,refresh_token建议1-24小时。刷新间隔不宜过短(避免频繁网络请求)也不宜过长(避免Token泄露后被长时间利用)。
3. **无状态设计原则**:Resource Server模式下,切勿在Controller中手动调用`request.getSession()`或注入`HttpSession`——这会在无状态架构中引入状态依赖,破坏水平扩展能力。

### 常见踩坑经验

1. **Security Filter Chain顺序问题**:Spring Security的Filter是按注册顺序执行的。如果自定义Filter需要在JWT认证之后执行(如读取Authentication中的用户信息),必须放在`BearerTokenAuthenticationFilter`之后。配置不当会导致自定义Filter中拿到的Authentication为null。

2. **JWT Decoder缓存失效**:NimbusJwtDecoder从jwks_uri拉取公钥后会缓存。如果Keycloak发生了密钥轮换(Realm Settings → Keys → Rotate),缓存中的旧公钥会导致新Token校验失败直到缓存过期(默认5分钟)。解决方案:配置`NimbusJwtDecoder`的`jwkSetUri`时设置较短的cache TTL,或监听Keycloak密钥轮换事件主动清缓存。

3. **@PreAuthorize SpEL表达式写错**:`@PreAuthorize("hasRole('admin')")`和`@PreAuthorize("hasAnyRole('user','admin')")`看似简单,但角色名必须与`JwtGrantedAuthoritiesConverter`中配置的完全一致(包括大小写)。Keycloak角色默认小写,所以`hasRole('Admin')`可能匹配不到`platform_admin`。建议所有角色名统一用小写加下划线命名。

### 思考题

1. **在微服务架构中,如果每个微服务都独立校验JWT签名,与API Gateway统一校验Token相比,各有什么优缺点?** 请从网络延迟(内省开销)、信任边界(零信任vs边界信任)、密钥管理(集中式vs分布式)、故障隔离(Gateway单点vs服务自治)四个维度展开分析。

2. **如何实现前端Token的"无感刷新"(用户在使用过程中Token自动续期而不中断操作)?** 请具体描述:(1)keycloak-js的updateToken机制的工作原理;(2)当refresh_token也过期时的降级策略(如何保存用户当前的页面状态并在重新登录后恢复);(3)ifame模式下第三方Cookie被浏览器禁用后的替代方案(如SPA静默刷新改用Fetch + redirect_uri模式)。

