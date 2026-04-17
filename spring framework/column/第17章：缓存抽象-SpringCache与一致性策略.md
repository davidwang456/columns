# 第 17 章：缓存抽象——Spring Cache 与一致性策略

> **业务线**：电商 / 订单履约微服务（拟真场景）。本章可独立阅读；与全书案例弱关联。

## 上一章思考题回顾

1. **`@Cacheable` 与事务**：建议在 **事务提交后** 再写入缓存（可用 **事务同步** 或 **事件**），否则可能缓存**未提交数据**。读缓存可在只读事务内。  
2. **一致性**：**Cache-Aside**（旁路）、**失效**、**双写**；高一致需 **分布式锁** 或 **消息** 补偿。

---

## 1 项目背景

商品详情 **读多写少**，团队引入 **Redis**。若业务代码散落 `redisTemplate`，**键规范**混乱；**Spring Cache** 提供 **注解式** 抽象。

**痛点**：  
- **缓存击穿**（热点 key 失效）。  
- **缓存雪崩**（TTL 同时过期）。  
- **更新后** 仍读到旧值。

---

## 2 项目设计（剧本式对话）

**角色**：小胖 / 小白 / 大师。  
**结构**：从「RedisTemplate 满天飞」→ 缓存注解语义 → 一致性策略。

**小胖**：我直接 `redisTemplate.opsForValue()`，想写啥 key 写啥，多自由。

**大师**：自由=**不可控**：键规范、TTL、序列化、版本升级全乱。`@Cacheable` / `@CacheEvict` / `@CachePut` 把**读/失效/写回**变成可评审的声明。

**技术映射**：**CacheManager** + **RedisCacheManager**（Boot 自动配置）。

**小白**：`@CachePut` 和 `@Cacheable` 放一起会不会打架？

**大师**：语义不同：**Cacheable** 是「有则直接返回」；**CachePut** 是「**总是执行方法**并更新缓存」。乱用会导致**旧值覆盖**或**多余写**。

**小白**：更新 DB 后缓存还是旧的，怎么办？

**大师**：典型 **Cache-Aside**：先写库，再 **evict** 或 **双写**（风险更高）；高一致用 **版本号**、**分布式锁** 或 **消息补偿**。

**小胖**：热点 key 过期瞬间被打穿，像不像食堂开门？

**大师**：像**秒杀开门**——要用 **互斥重建**、**随机 TTL**、**永不过期+异步刷新** 等策略，配合 **限流**（第 24/36 章）。

---

## 3 项目实战

### 3.1 环境准备

| 项 | 说明 |
|----|------|
| 依赖 | `spring-boot-starter-cache` + `spring-boot-starter-data-redis` |
| 本地 | Docker 起 Redis，或 Testcontainers |

### 3.2 分步实现

**步骤 1 — 目标**：`@EnableCaching` + 最小 `ProductService`。

```java
@Service
public class ProductService {
    @Cacheable(cacheNames = "products", key = "#id")
    public ProductDto get(String id) { /* load from db */ return new ProductDto(id, "x"); }

    @CacheEvict(cacheNames = "products", key = "#id")
    public void evict(String id) { }
}
```

**步骤 2 — 目标**：在 `application.yml` 配置 **Redis** 连接与 **TTL 前缀**（按团队规范）。

**步骤 3 — 目标（趣味加深）**：用 **Spring 测试** 打印缓存命中：第一次调用慢、第二次快（可用 `Thread.sleep` 模拟 DB）。

**启用缓存**：`@EnableCaching`。

### 3.3 完整代码清单与仓库

`chapter17-cache`。

### 3.4 测试验证

`@SpringBootTest` + **Testcontainers Redis**；或 **`@MockBean` CacheManager** 做纯单元测试。

**命令**：`mvn -q test`。

**可能遇到的坑**

| 现象 | 原因 | 处理 |
|------|------|------|
| 注解不生效 | 自调用 / 非 public | 通过代理调用 |
| 反序列化失败 | Jackson/Kryo 变更 | 版本化缓存 key |

---

## 4 项目总结

### 常见踩坑经验

1. **自调用** 缓存注解不生效。  
2. **序列化** 方式变更导致反序列化失败。  
3. **大对象** 进缓存撑爆内存。

---

## 思考题

1. `SecurityFilterChain` 与 `WebSecurityConfigurerAdapter` 变迁？（第 18 章，Boot 3。）  
2. JWT 放 Cookie 还是 Header？（第 18 章。）

---

## 推广协作提示

| 角色 | 建议 |
|------|------|
| **运维** | 监控 Redis 内存与键数量。 |

**下一章预告**：Spring Security、JWT、资源服务器初识。
