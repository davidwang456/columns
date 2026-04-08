# 第十二章：Spring 生态集成——Starter、Cache、Session 与职责切分

[← 目录](README.md)

---

## 趣味：Spring 是镖局，Redisson 是暗器

**小白**：我全家桶 Spring，还要自己 `Redisson.create`？  
**大师**：日常 **Starter 注入**；只有 **多集群、多租户客户端、运行时换配置** 才玩底层 `Config`。镖局走镖，暗器仍要会 **自己收鞘（shutdown）**。  
Spring 小哥路过：「我 `@Cacheable` 一把梭！」大师：**「注解爽三分钟，击穿教做人。」**

---

## 需求落地（由浅入深）

1. **浅**：项目已 Spring Boot——优先 **Starter + `application.yml` 指 YAML**；目标是 **一个 `RedissonClient` Bean、销毁时 shutdown**。  
2. **中**：划分 **Spring Data Redis**（CRUD 缓存）与 **Redisson**（锁、队列、高级结构）的 **key 所有权**；Session 外置要评估 **粘性、序列化、Codec 升级面**。  
3. **深**：`@Cacheable` 与 **击穿、雪崩、穿透** 联动第四章、第八章；**别把 Redis 注册成 XA 资源**（第十章）——**声明式缓存下面是分布式系统**。

---

## 对话钩子

**小白**：Starter 和手动 `create` 能共存吗？  
**大师**：技术上能，组织上 **极易双客户端互踩**。要么统一入口，要么 **书面 key 空间条约**。

---

## Spring Boot Starter

[integration-with-spring.md](../integration-with-spring.md)：

- `RedissonClient` Bean 与 **配置外置**（YAML / `spring.redis.redisson.file`）。  
- 生命周期：**随容器销毁 shutdown**。

---

## Spring Cache

- `@Cacheable` / `@CacheEvict` 等与 Redisson Cache 实现。  
- **深度**：**缓存穿透**（不存在的 key）、**击穿**（热点过期瞬间）、**雪崩**（集体过期）——  
  - 朴素手段：**空值短期缓存、互斥重建、随机 TTL**。  
- **null 缓存**：注意 **Optional 与序列化**（第四章联动）。

---

## Spring Session

- 会话外置；**粘性会话 vs 无状态 JWT** 要架构层先定。  
- **序列化**：会话对象变更 → **Codec 升级** 影响面巨大。

---

## Spring Transaction 与 Redis

- **别把 Redis 当 XA 资源**；跨 DB 仍按第十章思路。

---

## 与 Spring Data Redis 共存

- **职责切分**：Repository/Template 管 **CRUD 缓存**；Redisson 管 **锁、队列、高级结构**。  
- **反模式**：两套客户端 **无约定写同一 key 前缀** → 互相覆盖、互相 TTL。

---

## 实战：`application.yml` 思路（示意）

```yaml
spring:
  redis:
    redisson:
      file: classpath:redisson.yaml
```

具体属性以 **当前 starter 文档** 为准。

---

## 本章实验室

给 `@Service` 加 `@Cacheable`，压测命中；再 **热点 key 同时过期** 模拟击穿，试 **单飞锁（local）或 Redisson 锁** 保护重建（权衡延迟）。

---

## 大师私房话

Spring **装配** 不替代 **key 空间设计**。团队 Wiki 里写清：**谁拥有哪类 key、TTL 策略、禁止 `KEYS *`**。

**上一章**：[第十一章](第十一章-分布式服务.md)｜**下一章**：[第十三章](第十三章-框架矩阵速览.md)
