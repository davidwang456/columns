# 第 16 章：Spring Data JPA——实体映射与 Repository

> **业务线**：电商 / 订单履约微服务（拟真场景）。本章可独立阅读；与全书案例弱关联。

## 上一章思考题回顾

1. **`EntityManager`**：JPA **持久化上下文** API；**`Repository`**（Spring Data）在其之上提供 **按方法名派生查询**、**分页**、**投影**。  
2. **原生 SQL**：复杂报表、数据库特性；**JPQL**：面向实体、可移植。只读报表可 `@Query(nativeQuery=true)`。

---

## 1 项目背景

订单、订单行、用户地址构成 **对象图**。手写 JDBC 繁琐；**JPA** 提升效率，但带来 **N+1 查询**、**懒加载**、**级联**误用等风险。

**痛点**：  
- `OneToMany` **默认 LAZY**，JSON 序列化触发懒加载。  
- **`CascadeType.ALL`** 误删子表。  
- **分页** `count` 查询过慢。

---

## 2 项目设计（剧本式对话）

**角色**：小胖 / 小白 / 大师。  
**结构**：先承认 JPA「省 SQL」→ 再讨论懒加载与 N+1 → 最后用 DTO 收口 API。

**小胖**：JPA 不就是省写 SQL 吗？我 `findAll()` 一把梭。

**大师**：省的是**样板**；付的代价是 **N+1**、**懒加载**、**脏检查**与 **事务边界**。Repository 是**仓储抽象**——Spring Data 帮你生成实现，但**查询语义**仍要你设计。

**技术映射**：**JpaRepository** + **Specification**（动态查询） + **EntityGraph**（抓取策略）。

**小白**：懒加载到底啥时加载？

**大师**：第一次访问集合/关联字段时，如果 Session 还在，会触发 SQL；否则 **LazyInitializationException**。

**技术映射**：**Open EntityManager In View** 能「延迟到视图层」，但常把长事务带到 Web 层——**不推荐**作为默认。

**小胖**：我把 `OrderEntity` 直接返回给前端，字段挺全的啊。

**小白**：全的是**事故**：循环引用 JSON 爆炸、敏感字段泄露、懒加载触发不可控 SQL。

**大师**：API 层用 **DTO/投影**；实体只在**事务边界内**流转。像**后厨配菜**——前厅端出去的是**摆盘后的菜品**，不是整袋土豆。

---

## 3 项目实战

### 3.1 环境准备

| 项 | 说明 |
|----|------|
| 依赖 | `spring-boot-starter-data-jpa` |
| 数据库 | H2（测试）或本地 MySQL（对齐生产方言） |

```xml
<dependency>
  <groupId>org.springframework.boot</groupId>
  <artifactId>spring-boot-starter-data-jpa</artifactId>
</dependency>
```

### 3.2 分步实现

**步骤 1 — 目标**：建模 `OrderEntity` + `LineItemEntity`（`mappedBy` 正确）。

```java
@Entity
public class OrderEntity {
    @Id
    private String id;
    @OneToMany(mappedBy = "order", cascade = CascadeType.PERSIST, fetch = FetchType.LAZY)
    private List<LineItemEntity> lines;
}
```

**步骤 2 — 目标**：用 **`@EntityGraph`** 一次拉取行项目，避免 N+1。

```java
public interface OrderRepository extends JpaRepository<OrderEntity, String> {
    @EntityGraph(attributePaths = "lines")
    Optional<OrderEntity> findWithLinesById(String id);
}
```

**步骤 3 — 目标（对照实验）**：先写 `findById` 默认懒加载，在 **事务外**访问 `lines` 触发异常；再切到 `findWithLinesById` 对比 SQL 条数（`spring.jpa.show-sql=true`）。

**运行结果（文字描述）**：对照实验下，第二种查询应显著减少 **额外 SELECT** 次数。

### 3.3 完整代码清单与仓库

`chapter16-jpa`。

### 3.4 测试验证

`@DataJpaTest` + H2；可用 **`@Sql`** 预置数据；进阶用 **p6spy** / **integration test** 统计 SQL。

**命令**：`mvn -q test`。

**可能遇到的坑**

| 现象 | 原因 | 处理 |
|------|------|------|
| LazyInitializationException | 事务外访问懒加载 | 加只读事务 + fetch join / DTO |
| 删除父带子误删 | Cascade 配置不当 | 明确 cascade 与 orphanRemoval |

---

## 4 项目总结

### 常见踩坑经验

1. **OSIV** 打开导致长事务。  
2. **equals/hashCode** 与 **id 生成策略** 问题。  
3. **大对象** 未用 DTO 暴露给 API。

---

## 思考题

1. `@Cacheable` 与事务边界关系？（第 17 章。）  
2. 缓存与 DB **一致性**策略？（第 17 章。）

---

## 推广协作提示

| 角色 | 建议 |
|------|------|
| **DBA** | 评审索引与 `fetch join`。 |

**下一章预告**：Spring Cache、TTL、缓存穿透。
