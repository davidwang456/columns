# 第十二章：Spring 生态集成（导览）

[← 目录](README.md)

本章拆成 **三篇**，统一结构：**项目背景 → 大师×小白 → 项目实战（主代码片段）→ 项目总结**。建议顺序：**Starter → Spring Cache → Spring Session**。

| 分篇 | 文件 | 主题 |
|------|------|------|
| 分篇一 | [30-SpringBoot-Starter.md](30-SpringBoot-Starter.md) | `redisson-spring-boot-starter`、`RedissonClient` |
| 分篇二 | [31-SpringCache.md](31-SpringCache.md) | `redisson-spring-cache`、`@Cacheable` |
| 分篇三 | [32-SpringSession.md](32-SpringSession.md) | Spring Session + Redisson |

**权威文档**：[integration-with-spring.md](../integration-with-spring.md)；Spring Cache 细节：[cache/Spring-cache.md](../cache/Spring-cache.md)。

---

## 需求落地（由浅入深）

1. **浅**：Boot 项目优先 **Starter + YAML**；目标 **一个 `RedissonClient`、销毁时 shutdown**。  
2. **中**：划分 **Spring Data Redis** 与 **Redisson** 的 **key 所有权**；Session 外置先定 **粘性 vs 无状态**。  
3. **深**：`@Cacheable` 与 **击穿、雪崩、穿透** 联动第四章、第八章；**别把 Redis 当 XA**（第十章）。

---

## Spring Transaction 与 Redis

**别把 Redis 注册成 XA 资源**；跨 DB 一致性仍按 **第十章**（Outbox / Saga / 对账）。

---

## 与 Spring Data Redis 共存

- **职责切分**：Repository/Template 管 **CRUD 缓存**；Redisson 管 **锁、队列、高级结构**。  
- **反模式**：两套客户端 **无约定写同一 key 前缀** → 互相覆盖、互相 TTL。

---

## 综合实验室（约 90～120 分钟）

**环境**：Spring Boot + `redisson-spring-boot-starter` + `redisson-spring-cache`（Cache 实验）；可选 Spring Session 模块（Session 实验）；单 Redis，**JMeter 或 wrk** 或简单多线程客户端均可。

### 步骤

1. **Starter 生命周期**  
   - 启动应用，`RedissonClient` 注入后 `getBucket("lab:boot:ping").set("1")`。  
   - **正常停止** 应用（非 kill -9），用 `redis-cli` 或日志确认 **无泄漏连接**（若运维有 `CLIENT LIST` 可看）。  
   - 故意写错 `redisson.yaml` 地址，记录 **启动失败日志** 关键词，便于以后排障。

2. **@Cacheable 命中与穿透**  
   - `getById` 对 **存在 / 不存在** id 各压测 1000 次；统计 **DB 或 mock 层调用次数**（应大量减少）。  
   - 对 **固定不存在** id 不加空值缓存，观察 **是否每次打穿 DB**；再加 **短 TTL 空值缓存** 复测。

3. **击穿（热点同时过期）**  
   - 令 `products::1` 等高热点 key **同一秒过期**（可先删再并发请求）。  
   - 无保护：记录 **重建风暴**（DB QPS 尖峰）。  
   - 加 **Redisson `RLock`** 或文档推荐的 **互斥重建**，复测尖峰是否 **被削平**（记录延迟 P99 变化）。

4. **Session（若已集成）**  
   - 登录后 **水平扩一台** 新实例（或换 upstream），同一 Cookie 请求 **是否仍认证通过**。  
   - `redis-cli` 找到 session key（注意前缀），对照 **TTL** 与 `spring.session.timeout`。

### 验证标准

- 实验 2：**有数字**（缓存命中比例或 DB 调用下降比例）。  
- 实验 3：**有前后对比**（DB 尖峰或线程阻塞日志）。  
- 实验 4：能口述 **粘性 vs 无状态** 与本实验的关系。

### 记录建议

- 一页：**cacheNames → key 前缀 → TTL** 表；**禁止与 Template 共写的 key 模式**。  
- 击穿防护：**选锁还是本地单飞** 的团队结论 + 取舍（延迟 vs 保护 DB）。

---

## 大师私房话

Spring **装配** 不替代 **key 空间设计**。团队 Wiki 里写清：**谁拥有哪类 key、TTL 策略、禁止 `KEYS *`**。

**上一章**：[第十一章（分篇四）Live Object](28-LiveObject.md)｜[第十一章导览](24-分布式服务.md)｜**开始阅读**：[第十二章（分篇一）Spring Boot Starter](30-SpringBoot-Starter.md)｜**下一章**：[第十三章导览](33-框架矩阵速览.md)
