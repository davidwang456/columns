---
title: "第13章 RequestAuthentication：终端用户身份与 JWT 验证"
part: "第二部分：核心能力篇（第11-22章）"
chapter: 13
---

# 第13章 RequestAuthentication：终端用户身份与 JWT 验证

## 13.1 项目背景

**东西向 mTLS 与南北向用户身份是两件事**

PeerAuthentication 解决的是**服务与服务之间**的传输层身份（SPIFFE），但许多攻击面来自**终端用户**——浏览器、移动 App、合作伙伴系统调用的 API。仅依赖网络位置或 IP 白名单已无法满足零信任要求，需要把 **JWT / OIDC** 等可验证的终端身份纳入策略体系。

**应用内鉴权与网格鉴权的边界**

传统做法是在每个服务内解析 JWT、校验签名与过期时间，导致库版本分裂、密钥轮换困难。Istio 的 RequestAuthentication 将**身份验证**（Authentication）与**授权**（Authorization，见 AuthorizationPolicy）分层：前者验证“令牌是否可信、主体是谁”，后者决定“允许执行哪些操作”。

## 13.2 项目设计：大师区分“你是谁”和“你能做什么”

**场景设定**：管理后台与开放 API 共用同一 `api-gateway` 入口，小白希望先统一校验 OAuth2 颁发的 JWT，再用 AuthorizationPolicy 限制 `/admin` 路径仅内部员工身份可访问。

**核心对话**：

> **小白**：我们已经启用了 mTLS，为什么还要 JWT？
>
> **大师**：mTLS 回答的是**服务身份**；JWT 回答的是**用户或客户端身份**。没有 JWT，网格只知道“A 服务调用了 B 服务”，不知道“是哪位用户发起的”。
>
> **小白**：RequestAuthentication 具体做什么？
>
> **大师**：它告诉 Sidecar：去哪个 JWKS 拉公钥、信任哪些 issuer、audience 要匹配什么。验证通过后，会把身份声明注入请求上下文，供后续的 AuthorizationPolicy 使用。

**类比阐释**：mTLS 像员工工牌进出园区；JWT 像具体业务系统的登录会话——园区门禁通过了，还要看你有没有进财务室的授权。

## 13.3 项目实战：RequestAuthentication 与 AuthorizationPolicy 组合

```yaml
apiVersion: security.istio.io/v1beta1
kind: RequestAuthentication
metadata:
  name: api-jwt
  namespace: production
spec:
  selector:
    matchLabels:
      app: api-gateway
  jwtRules:
  - issuer: "https://auth.example.com/"
    jwksUri: "https://auth.example.com/.well-known/jwks.json"
    audiences:
    - "api.example.com"
    forwardOriginalToken: true
---
apiVersion: security.istio.io/v1beta1
kind: AuthorizationPolicy
metadata:
  name: admin-api
  namespace: production
spec:
  selector:
    matchLabels:
      app: api-gateway
  action: ALLOW
  rules:
  - to:
    - operation:
        paths: ["/admin/*"]
    when:
    - key: request.auth.claims[groups]
      values: ["employees"]
  - to:
    - operation:
        paths: ["/public/*"]
```

```bash
# 验证 JWT 校验是否生效（需携带有效 Bearer Token）
curl -H "Authorization: Bearer $TOKEN" https://api.example.com/admin/health -vk
```

## 13.4 项目总结

| 维度 | 详细分析 |
|:---|:---|
| **核心优点** | **集中验证**、**与授权解耦**、**减少应用重复代码** |
| **主要缺点** | **JWKS 可用性**、**时钟偏差**、**令牌转发与隐私** |
| **典型使用场景** | **API 网关统一登录态**、**多租户 SaaS**、**与 IdP 集成** |
| **关键注意事项** | **issuer/aud 严格匹配**；**轮换密钥时的缓存**；**OPTIONS 预检与 JWT 共存** |
| **常见踩坑经验** | **401/403 混淆**：认证失败与授权拒绝日志不同；**未 forwardOriginalToken 导致后端仍需自行解析** |

---

## 编者扩展

> **本章导读**：终端用户身份进网格：JWT 校验是 API 现代化的标配。

### 趣味角

没有 RequestAuthentication 的 JWT 校验，就像只认复印件不认公章——网关验了，服务间转发时 claim 可能被伪造或丢失。

### 实战演练

用 Keycloak/Okta 或 `jwt.io` 生成的 token 测一条合法与过期请求；在 Envoy 访问日志里找 `authorization` 相关字段（注意脱敏）。

### 深度延伸

对比 **网关终止 JWT** 与 **每跳传递** 的 trust 边界；简述 bearer token 在服务间传递时的最小暴露原则。

---

上一章：[第12章 AuthorizationPolicy：零信任的访问控制](第12章 AuthorizationPolicy：零信任的访问控制.md) | 下一章：[第14章 金丝雀发布：渐进式交付的艺术](第14章 金丝雀发布：渐进式交付的艺术.md)

*返回 [专栏目录](README.md)*
