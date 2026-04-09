# 第十二章（分篇二）：Spring Cache——`@Cacheable` 与 Redisson `CacheManager`

[← 第十二章导览](29-Spring生态集成.md)｜[目录](README.md)

---

## 1. 项目背景

商品详情、用户基础信息等 **读多写少**，希望在 Service 层用 **`@Cacheable`** 声明缓存，由 **Redis** 承载，并与现有 **`RedissonClient`** 共用连接与 Codec 策略。  
需引入 **`redisson-spring-cache`**，注册 **`RedissonSpringCacheManager`**（或 YAML 配置的管理器变体），再开启 **`@EnableCaching`**。

---

## 2. 项目设计（大师 × 小白）

**小白**：我 `@Cacheable` 一把梭！  
**大师**：注解下面是 **分布式缓存**——**穿透**（不存在的 id）、**击穿**（热点 key 同时过期）、**雪崩**（集体过期）都会来敲门。

**小白**：加随机 TTL 就够？  
**大师**：常还要 **空值短期缓存**、热点 **互斥重建**（本地单飞锁或第八章 **RLock**），并按业务选 **允许 null 缓存** 与序列化策略（第四章）。

---

## 3. 项目实战（主代码片段）

**依赖**（Community）：

```xml
<dependency>
    <groupId>org.redisson</groupId>
    <artifactId>redisson-spring-cache</artifactId>
    <version><!-- 与 redisson 主版本一致 --></version>
</dependency>
```

**最小 `CacheManager`（Community 常用 `RedissonSpringCacheManager`）**：

```java
import org.redisson.api.RedissonClient;
import org.redisson.spring.cache.RedissonSpringCacheManager;
import org.springframework.cache.CacheManager;
import org.springframework.cache.annotation.EnableCaching;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

@Configuration
@EnableCaching
public class CacheConfiguration {

    @Bean
    public CacheManager cacheManager(RedissonClient redissonClient) {
        return new RedissonSpringCacheManager(redissonClient);
    }
}
```

**业务**：

```java
import org.springframework.cache.annotation.Cacheable;
import org.springframework.stereotype.Service;

@Service
public class ProductQueryService {

    @Cacheable(cacheNames = "products", key = "#id")
    public ProductDto getById(long id) {
        return loadFromDb(id);
    }
}
```

**进阶**：按缓存名配置 **ttl / maxIdle**、使用 **YAML 加载**、或 PRO 的 **Local cache / 分区** 等，见 [cache/Spring-cache.md](../cache/Spring-cache.md)。

---

## 4. 项目总结

| 维度 | 说明 |
|------|------|
| **优点** | **声明式**，与 Spring Cache 抽象一致；与 Redisson **同一客户端**；可外置 **cache-config.yaml**。 |
| **缺点** | **事务缓存、null、序列化** 边界要设计；高阶能力（本地缓存等）多属 **PRO**；击穿需 **额外手段**。 |
| **适用场景** | 接口级 / 方法级 **读缓存**、与 DB 搭配的 **减轻读压力**。 |
| **注意事项** | **`@CacheEvict` / `@CachePut`** 与写路径一致；**`cacheNames`** 与运维 key 规范对齐；第四章 **Codec** 与升级评审。 |
| **常见踩坑** | **缓存与 DB 双写顺序** 导致脏读；**Optional** 与 **null 值** 序列化翻车；无 **TTL** 导致 Redis **无限涨**；把 `@Cacheable` 当 **强一致**。 |

---

## 本章实验室（约 45～60 分钟）

**环境**：已完成 [第十二章（分篇一）](30-SpringBoot-Starter.md) 的 `CacheManager` Bean；`ProductQueryService` 或等价；**mock DB**（计数器即可）。

### 步骤

1. `getById(1L)` 连续调用 **5** 次，断言底层 loader **只执行 1 次**（用 `AtomicInteger`）。  
2. `@CacheEvict(cacheNames = "products", key = "#id")` 在更新方法上调用后，再 `getById(1L)`，loader **应再执行 1 次**。  
3. 配置某 cache name **TTL 5s**（若用 `cache-config.yaml` 或编程配置），`get` 后等待 **6s** 再 `get`，loader **应再执行**。  
4. 对 **不存在 id** 压测 **20** 次：无空值缓存时 loader **20 次**；加 **短 TTL null 缓存** 后 **1 次**（或文档允许行为）。

### 验证标准

- 实验 1～2：**计数器数字** 与预期一致。  
- 实验 4：**有前后对比** 数字。

### 记录建议

- 与第十二章导览 **综合实验室** 第 3 步衔接：在此验证 **击穿防护** 前的基线。

**上一篇**：[第十二章（分篇一）Spring Boot Starter](30-SpringBoot-Starter.md)｜**下一篇**：[第十二章（分篇三）Spring Session](32-SpringSession.md)｜**下一章**：[第十三章导览](33-框架矩阵速览.md)
