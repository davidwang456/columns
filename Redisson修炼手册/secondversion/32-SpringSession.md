# 第十二章（分篇三）：Spring Session——Redis 外置会话与 Redisson

[← 第十二章导览](29-Spring生态集成.md)｜[目录](README.md)

---

## 1. 项目背景

多实例 Web 要 **共享 HttpSession**（登录态、购物车草稿等），不能依赖单机内存；希望沿用 **Spring Session** 编程模型，底层用 **Redisson** 作为 Redis 客户端（**`RedissonConnectionFactory`** 实现 Spring Data 的 `RedisConnectionFactory`，见 [integration-with-spring.md](../integration-with-spring.md)）。

---

## 2. 项目设计（大师 × 小白）

**小白**：Session 放 Redis，我就不用 JWT 了？  
**大师**：先定 **粘性会话 vs 无状态**：Session 外置 **有状态、有 Redis 依赖**；JWT **无服务端会话存储** 但换 **吊销与体积** 问题。架构选型 **没有免费午餐**。

**小白**：我改个字段就发布，用户要重新登录吗？  
**大师**：若 **会话对象序列化格式** 变了，可能出现 **全员掉线**——**Codec 与 DTO 演进** 要和第四章、第十二章导览一起评审。

---

## 3. 项目实战（主代码片段）

以下思路来自官方文档，**具体注解与依赖版本**（Servlet / WebFlux、`spring-session-data-redis`、可选 `redisson-spring-session-*`）请对照当前 Spring Session 与 Redisson 说明。

**依赖**：`spring-session-data-redis` + `redisson-spring-boot-starter`（或文档要求的 **redisson-spring-session-33** 等与 Session 大版本匹配的模块）。

**典型配置项（示例）**：

```properties
spring.session.store-type=redis
spring.session.timeout=15m
```

**Servlet 侧**（示例风格）：使用 **`@EnableRedisHttpSession`** 的配置类，并继承 **`AbstractHttpSessionApplicationInitializer`**（见官方 [Spring Session 小节](../integration-with-spring.md#spring-session)）。

**WebFlux 侧**：对应 **`@EnableRedisWebSession`** 与 **`AbstractReactiveWebInitializer`**。

**运维**：Redis **`notify-keyspace-events`** 需包含文档要求的字母（如 **`Exg`**），否则 Session 集成可能异常。

**PRO**：本地缓存 Session、广播更新等见官网 **Spring Session** 与 **PRO** 说明。

---

## 4. 项目总结

| 维度 | 说明 |
|------|------|
| **优点** | **多实例共享登录态**；与 Spring Session **生态一致**；Redisson 统一连接与配置。 |
| **缺点** | **Redis 故障影响登录**；序列化体量大时 **网络与延迟** 上升；调试 **比纯 JWT 绕**。 |
| **适用场景** | 传统 Session 模型上云、需要 **快速水平扩展** 的 Web 应用。 |
| **注意事项** | **超时、Cookie 域、HTTPS**；**敏感属性** 不要塞进 Session；大对象 **拆分或外置引用**。 |
| **常见踩坑** | **notify-keyspace-events** 未配；**多环境共用 Redis** 导致 Session 串台；升级 **序列化不兼容**；与 **Spring Data Redis Template** 乱用同一 key 前缀。 |

---

## 本章实验室（约 45～60 分钟）

**环境**：已按官方文档启用 **Redis Session**；浏览器或 `curl` 带 Cookie；`redis-cli`；Redis `CONFIG GET notify-keyspace-events`。

### 步骤

1. 登录（或访问会创建 session 的接口），记下 **`Set-Cookie`** 中的 session id。  
2. `redis-cli` **KEYS** 或 **SCAN** 找到 session 相关 key（勿在生产用 KEYS），`TTL` 应与 `spring.session.timeout` **量级一致**。  
3. 验证 **`notify-keyspace-events`**：若当前值为空或缺字母，按文档改为 **`Exg`**（或文档要求值），**重启 Redis 后** 再测 **过期销毁** 是否正常（观察 key 消失与登出行为）。  
4. （可选）**两实例** 负载均衡 **无粘性**，同一 Cookie 轮询请求，**均应 200**（Session 外置成功标志之一）。

### 验证标准

- 能指出 **Redis 中 session key 的命名模式**（截图或抄录一条）。  
- 实验 3：**配置前/后** 行为差异一句话。

### 记录建议

- Runbook：**升级 Spring Session 大版本** 时的序列化回归检查项。

**上一篇**：[第十二章（分篇二）Spring Cache](31-SpringCache.md)｜**下一章**：[第十三章导览](33-框架矩阵速览.md)
