# 第 08 章：Spring AOP 入门——日志与横切关注点

> **业务线**：电商 / 订单履约微服务（拟真场景）。本章可独立阅读；与全书案例弱关联。

> **篇章**：基础篇（全书第 1–18 章；核心概念、单机、简单 API、初级实战）

## 上一章思考题回顾

1. **JDK 动态代理**：要求目标实现**接口**；**CGLIB** 基于子类，可代理**类**（`proxyTargetClass=true` 时强制 CGLIB）。  
2. **`@Transactional` on private**：**无效**——Spring AOP 代理无法拦截，事务注解需打在**可代理的 public** 方法上（同类自调用也不走代理，见第 28 章）。

---

## 1 项目背景

订单核心方法需要**审计日志**：谁、何时、耗时、是否成功。若每个 Service 手写 `log.info`，**重复**且易漏；若用过滤器，又拿不到**方法级**业务语义。

**痛点**：  
- 日志格式不统一，**检索困难**。  
- 敏感参数（手机号）未脱敏。  
- 与**事务、安全**横切逻辑交织，**顺序**难控。

---

## 2 项目设计（剧本式对话）

**角色**：小胖 / 小白 / 大师。  
**结构**：先澄清「拦截器 vs AOP」→ 再落到代理与切点表达式。

**小胖**：AOP 不就是拦截器吗？Servlet Filter 我也玩过。

**大师**：Filter 在 **Web 层最外圈**，看到的是 **HTTP**；AOP 在 **Spring Bean 调用链**上，看到的是 **方法签名**。一个管「请求进不进得来」，一个管「业务方法前后要不要织入逻辑」。

**技术映射**：**AspectJ 注解风格** + **Spring AOP 代理**（默认运行时织入）。

**小白**：`@Around` 和 `@Before` 区别？

**大师**：`Around` 可**控制是否调用**原方法、修改返回值、计时；`Before` 只在进入前执行，**包不住** `proceed()` 的异常语义与返回值改写。

**技术映射**：**ProceedingJoinPoint#proceed()** 是 `@Around` 的核心；滥用 `Around` 会把业务逻辑包成「洋葱」，调试困难。

**小胖**：那我给所有 `com.example..*` 都切一刀，日志多详细！

**小白**：切点太宽，**性能**和**噪声**先爆炸；另外你把 **private 工具方法**也切进去，排障时日志像瀑布。

**大师**：切点要像**安检门**：只拦「该拦的」。生产常用 **`@annotation`**、**`within`**、**`bean()`** 组合；并给切面 **`@Order`**，让 **事务/安全** 这类「硬横切」顺序可控。

**技术映射**：**Advisor 顺序** 影响 **事务切面** 与 **审计切面** 谁先包裹谁；一般**事务更外层**（先开启事务，再记录「事务内调用」）。

**小胖**：切面里抛异常会怎样？

**大师**：可能**短路**原方法；也可能把真实异常**包装**成 `UndeclaredThrowableException`。团队规范：**审计失败默认不阻断业务**（除非安全审计），日志要异步落盘。

---

## 3 项目实战

本章在 **订单服务**上加一层「**方法耗时审计**」，要求：**不切 Controller**（避免重复记录过滤器日志），只切 **`com.example.order.service`**。

### 3.1 环境准备

| 项 | 说明 |
|----|------|
| 依赖 | `spring-boot-starter-aop` |
| 可选 | `spring-boot-starter-test` |

```xml
<dependency>
  <groupId>org.springframework.boot</groupId>
  <artifactId>spring-boot-starter-aop</artifactId>
</dependency>
```

### 3.2 分步实现

**步骤 1 — 目标**：定义 **切点** 仅匹配 `service` 包（示例）。

```java
package com.example.aop;

import org.aspectj.lang.ProceedingJoinPoint;
import org.aspectj.lang.annotation.*;
import org.springframework.core.annotation.Order;
import org.springframework.stereotype.Component;

@Aspect
@Component
@Order(1)
public class AuditAspect {

    @Around("execution(* com.example.order.service..*(..))")
    public Object logAround(ProceedingJoinPoint pjp) throws Throwable {
        long t0 = System.nanoTime();
        try {
            return pjp.proceed();
        } finally {
            long ms = (System.nanoTime() - t0) / 1_000_000;
            System.out.println("[AUDIT] " + pjp.getSignature().toShortString() + " cost=" + ms + "ms");
        }
    }
}
```

**步骤 2 — 目标**：准备 `com.example.order.service.OrderService#place` 之类的方法，启动后调用一次。

**运行结果（文字描述）**：控制台出现 `[AUDIT] OrderService.place(..) cost=Xms`（X 取决于机器）。

**步骤 3 — 目标（趣味加深）**：故意在 **同类内部** `this.place()` 自调用一刀，观察切面是否生效（通常 **不生效**），把「代理」概念牢牢记住。

### 3.3 完整代码清单与仓库

`chapter08-aop`。

### 3.4 测试验证

`@SpringBootTest` 启动后调用 service bean（从上下文获取），断言日志含 `[AUDIT]`；或用 **AOP 测试**验证切点匹配（进阶）。

**可能遇到的坑**

| 现象 | 原因 | 处理 |
|------|------|------|
| 切面完全不走 | 自调用 / 非 Spring 管理 Bean | 拆 Bean 或 `AopContext`（谨慎） |
| 事务不生效 | 与事务代理顺序/可见性有关 | 调整 `@Order`、public 方法 |
| 性能抖动 | 切面里做 IO | 改异步/采样 |

---

## 4 项目总结

### 优点与缺点

| 维度 | AOP | 复制粘贴日志 |
|------|-----|----------------|
| DRY | 好 | 差 |
| 调试 | 需理解代理 | 直观 |

### 常见踩坑经验

1. **自调用** 不经过代理 → 切面不生效。  
2. **final 类/方法** 无法 CGLIB。  
3. **切面内异常** 吞掉原异常。

---

## 思考题

1. `JdbcTemplate` 与 `JdbcClient`（Spring 6.1+）选型？（第 9 章。）  
2. `@Transactional` 默认传播行为？（第 9 章。）

---

## 推广协作提示

| 角色 | 建议 |
|------|------|
| **运维** | 日志字段与链路 traceId 对齐。 |

**下一章预告**：`JdbcTemplate`、声明式事务、`@Transactional`。
