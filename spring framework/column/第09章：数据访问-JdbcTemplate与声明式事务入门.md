# 第 09 章：数据访问——JdbcTemplate 与声明式事务入门

> **业务线**：电商 / 订单履约微服务（拟真场景）。本章可独立阅读；与全书案例弱关联。

> **篇章**：基础篇（全书第 1–18 章；核心概念、单机、简单 API、初级实战）

## 上一章思考题回顾

1. **`JdbcTemplate`**：经典同步 API；**`JdbcClient`**（Spring Framework 6.1+）提供更现代的流式/可选风格，底层仍基于 JDBC。简单 CRUD 可优先 **JdbcClient**，存量项目多用 **JdbcTemplate**。  
2. **`@Transactional` 默认传播**：`Propagation.REQUIRED`——已存在事务则加入，否则新建。

---

## 1 项目背景

订单落库需要 **插入订单行** 与 **扣减库存** 两步。若第一步成功、第二步失败，**数据不一致**。手工 `conn.setAutoCommit(false)` 易错；**声明式事务**把边界声明在 Service 上，**可读性**与**一致性**更好。

**痛点**：  
- 忘记关闭连接。  
- 异常被捕获导致**事务未回滚**。  
- 只读事务未标记 **`readOnly=true`**，数据库失去优化机会。

---

## 2 项目设计（剧本式对话）

**角色**：小胖 / 小白 / 大师。  
**结构**：从 ORM 之争 → JDBC 可控性 → 事务边界与异常语义。

**小胖**：为啥不用 Hibernate？我只会写对象，不会写 SQL。

**大师**：ORM 擅长**对象图导航**与**变更追踪**；但如果团队 SQL 很清晰、性能要可控（批量、hint、执行计划），**JdbcTemplate/JdbcClient** 更直接。别把「不会 SQL」当成架构理由——该补的课要补。

**技术映射**：**JdbcTemplate** = 模板方法 + 资源管理；**声明式事务** = AOP 代理 + `TransactionInterceptor`。

**小白**：`rollbackFor` 默认值？

**大师**：默认 **RuntimeException / Error** 回滚；**checked exception** 默认不回滚，需显式 `rollbackFor`。

**技术映射**：**rollback-only 标记** 会沿着事务边界传播；吞掉异常要小心「提交成功但业务失败」。

**小胖**：那我 `try/catch` 住异常，打印日志再继续，算不算英雄？

**小白**：算**事故预备役**：异常被你吃掉，事务以为一切顺利，**数据库提交了**，外面却返回「成功」。要么 **重新抛出**，要么 **显式标记 rollback**。

**大师**：可以类比：**收银机报错你按住不出声**，老板以为钱收到了——这就是**一致性**问题的根源。

**小白**：只读操作为啥要 `@Transactional(readOnly = true)`？

**大师**：给底层 **连接/ORM** 以优化提示；在读写分离路由里，也常作为**读走从库**的开关（视基础设施而定）。

---

## 3 项目实战

### 3.1 环境准备

```xml
<dependency>
  <groupId>org.springframework.boot</groupId>
  <artifactId>spring-boot-starter-jdbc</artifactId>
</dependency>
<dependency>
  <groupId>com.h2database</groupId>
  <artifactId>h2</artifactId>
  <scope>runtime</scope>
</dependency>
```

**`schema.sql`**

```sql
CREATE TABLE orders (id VARCHAR(36) PRIMARY KEY, sku_id VARCHAR(64), qty INT);
```

### 3.2 分步实现

```java
package com.example.repo;

import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Repository;

@Repository
public class OrderRepository {
    private final JdbcTemplate jdbc;

    public OrderRepository(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    public void insert(String id, String skuId, int qty) {
        jdbc.update("INSERT INTO orders(id, sku_id, qty) VALUES(?,?,?)", id, skuId, qty);
    }
}
```

```java
package com.example.service;

import com.example.repo.OrderRepository;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
public class OrderTxService {
    private final OrderRepository repo;

    public OrderTxService(OrderRepository repo) {
        this.repo = repo;
    }

    @Transactional
    public void place(String id, String skuId, int qty) {
        if (qty < 0) {
            throw new IllegalArgumentException("bad qty");
        }
        repo.insert(id, skuId, qty);
    }
}
```

**验证逻辑**：`qty < 0` 时在 **insert 之前** 抛运行时异常 → 事务 **整体回滚**，表中无脏数据。

**步骤 3 — 目标（对照实验）**：再写一个 `placeBuggy`：**先 insert 再校验**（演示错误写法），观察「坏数据已落库」与事务语义，强化 **边界设计** 的重要性（可用集成测试断言行数）。

### 3.3 完整代码清单与仓库

`chapter09-jdbc-tx`。

### 3.4 测试验证

`@SpringBootTest` + `@Transactional` 测试默认 **自动回滚**（注意与「验证事务提交」类测试冲突）；要验证提交，可用 **`@Commit`** 或单独测试类。

**SQL 断言思路（示例）**

```java
@Autowired
JdbcTemplate jdbc;

@Test
void rolls_back_on_bad_qty() {
    assertThrows(IllegalArgumentException.class, () -> svc.place("1", "SKU", -1));
    Integer c = jdbc.queryForObject("SELECT COUNT(*) FROM orders WHERE id='1'", Integer.class);
    assertEquals(0, c);
}
```

**命令**：`mvn -q test`。

---

## 4 项目总结

### 优点与缺点

| 维度 | 声明式事务 | 手工 JDBC |
|------|------------|-----------|
| 一致性 | 好 | 易错 |
| 可控性 | 需理解传播 | 完全可控 |

### 常见踩坑经验

1. **同类自调用** 事务不生效。  
2. **吞异常** 导致提交。  
3. **只读事务** 未开，从库路由失效（读写分离场景）。

---

## 思考题

1. `spring.factories` 与 `AutoConfiguration.imports` 区别？（第 10/31 章。）  
2. `@SpringBootTest` 与 `@DataJpaTest` 切片差异？（第 11 章。）

---

## 推广协作提示

| 角色 | 建议 |
|------|------|
| **DBA** | 与默认隔离级别、锁表现对齐。 |

**下一章预告**：Spring Boot 依赖管理、`@SpringBootApplication`、自动配置初探。
