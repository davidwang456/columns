# 第十三章（分篇四）：Hibernate——二级缓存与 `redisson-hibernate-*`

[← 第十三章导览](33-框架矩阵速览.md)｜[目录](README.md)

---

## 1. 项目背景

ORM 层 **读多写少**，已在实体上开启二级缓存，希望 **Region 落在 Redis**，多应用实例 **共享缓存区**，并支持 Hibernate 的 **READ_ONLY / READ_WRITE** 等策略。Redisson 提供 **`redisson-hibernate-4` … `redisson-hibernate-7`** 等与 **Hibernate 4–7** 对应的工厂实现。

---

## 2. 项目设计（大师 × 小白）

**小白**：上了 Redis 二级缓存，还要不要本地一级缓存？  
**大师**：**一级仍在 Session 内**；二级是 **跨 Session / 跨 JVM**。别指望二级缓存解决 **写路径一切问题**。

**小白**：Redis 挂了查询会挂吗？  
**大师**：文档有 **`hibernate.cache.redisson.fallback`**——是否 **回落数据库** 是 **产品决策**，不是默认玄学。

---

## 3. 项目实战（主代码片段）

**依赖**：按 Hibernate 版本选择 **`redisson-hibernate-*`**（[cache-api-implementations.md#hibernate-cache](../cache-api-implementations.md#hibernate-cache)）。

**典型 `persistence.xml` / `hibernate.cfg.xml` 属性**（节选）：

```xml
<property name="hibernate.cache.use_second_level_cache" value="true"/>
<property name="hibernate.cache.use_query_cache" value="true"/>
<property name="hibernate.cache.redisson.fallback" value="true"/>
<property name="hibernate.cache.redisson.config" value="/redisson.yaml"/>

<property name="hibernate.cache.region.factory_class"
          value="org.redisson.hibernate.RedissonRegionFactory"/>
```

**工厂类** 还有 **Native / LocalCached / Clustered** 等变体（PRO 能力见官方表格），按 **集群、驱逐策略、是否本地 near-cache** 选型。

**实体**：对需进二级的实体配置 **`@Cache`** / **`@Cacheable`**（以 Hibernate 版本 API 为准）。

---

## 4. 项目总结

| 维度 | 说明 |
|------|------|
| **优点** | **跨实例共享**二级缓存；与 Redisson **统一运维**；支持多种 **RegionFactory** 能力梯度。 |
| **缺点** | **缓存与 DB 一致性** 仍要开发者理解 Hibernate 策略；错误配置易导致 **脏读或风暴查库**。 |
| **适用场景** | 读密集、实体相对稳定、可接受 **最终一致** 的缓存语义。 |
| **注意事项** | **`redisson-hibernate-*` 与 Hibernate 小版本** 严格对齐；生产开启 **fallback** 与 **监控**。 |
| **常见踩坑** | **选错 artifact**（如 5.2 vs 5.3 工厂）；与 **Spring Data `@Cacheable`** 叠床架屋；更新实体 **未失效** 导致长期脏数据。 |

---

## 本章实验室（约 60～90 分钟）

**环境**：最小 Hibernate + JPA 实体 + **`redisson-hibernate-*` RegionFactory**；H2 或测试库；两 JVM 可选。

### 步骤

1. 实体开启 **二级缓存**，`findById` **两次**，统计 **SQL 条数**（应 **第 2 次无 SELECT** 或仅命中缓存，以统计为准）。  
2. **进程 B** `findById` 同 id，确认 **命中 Redis 域缓存**（观察 SQL / 日志）。  
3. 在 **进程 A** 更新该实体并 **commit**，进程 B **无失效配置时** 再读，记录 **是否脏读**；再按文档加 **`@Cache` 失效或 region evict`** 复测。  
4. 对照 `pom.xml`：**hibernate-core 小版本** 与 **`redisson-hibernate-*`** 后缀 **一致**。

### 验证标准

- 实验 1：**有 SQL 计数** 前后对比。  
- 实验 3：**能说明** 更新路径与 **缓存一致性** 的配置关系。

### 记录建议

- 决策：**本实体是否进 2LCache** 三条规则（变更频率、大小、一致性要求）。

**上一篇**：[第十三章（分篇三）Helidon](36-Helidon.md)｜**下一篇**：[第十三章（分篇五）MyBatis](38-MyBatis.md)｜**下一章**：[第十四章](40-可观测与上线清单.md)
