# 第 15 章：AOP 进阶——切点性能与环绕通知

> **业务线**：电商 / 订单履约微服务（拟真场景）。本章可独立阅读；与全书案例弱关联。

## 上一章思考题回顾

1. **同一切面** 多 advice 顺序由 **`@Order`** 与 **声明顺序**（AspectJ 规则）共同影响；**不同切面** 用 `@Order` 明确。`@Around` 应最先考虑是否**必要**（性能与异常语义）。  
2. **Lazy 事务外**：Open Session In View 关闭后，**懒加载**代理无法初始化 → `LazyInitializationException`（第 16 章用 **@Transactional(readOnly)** 或 **JOIN FETCH** 解决）。

---

## 1 项目背景

审计切面若匹配 **`execution(* *(..))`** 过宽，**每个方法**都代理，**CPU** 与 **GC** 压力上升。大促时需要 **精准切点** 与 **短路** 策略。

**痛点**：  
- 切面内 **远程调用** 导致超时放大。  
- **`proceed()` 多次调用** 语义错误。  
- **异常类型** 改变事务回滚行为。

---

## 2 项目设计（剧本式对话）

**角色**：小胖 / 小白 / 大师。  
**结构**：性能视角切入 → 切点语言选择 → `@Around` 责任边界。

**小胖**：我把切点写成 `execution(* *(..))`，全项目监控，多酷。

**大师**：酷的是**账单**：CPU、代理对象、日志量都会上来。切点表达式能用 **`@annotation` / `within` / `bean()`** 就别全包扫描；**编译期织入** AspectJ 另当别论。

**技术映射**：**Pointcut** 设计 = 性能与可维护性平衡；**AspectJ 表达式**在启动期解析，过宽会放大候选连接点。

**小白**：`@Around` 和 `@Before` + `@After` 组合有啥取舍？

**大师**：`Around` 强在**包裹**与**计时**；但滥用会让排障困难（异常栈多一层）。很多团队规定：**审计/指标用 Around**，纯日志用 **Before/AfterReturning**。

**小白**：`@Around` 里能 `return null` 吗？

**大师**：可以，但要清楚 **调用方** 与 **空指针** 风险；审计类切面通常**不替代**业务返回。

**小胖**：切面里调下游 RPC 做风控呢？

**大师**：谨慎：你会把 **横切** 变成 **同步阻塞**，并且让失败策略难定义（阻断业务 vs 仅告警）。更常见：**发事件** 或 **异步**（与超时/熔断配合）。

---

## 3 项目实战

### 3.1 环境准备

| 项 | 说明 |
|----|------|
| 依赖 | `spring-boot-starter-aop` |
| 目标 | 用 **注解切点** 替代「全包 execution」 |

### 3.2 分步实现

**步骤 1 — 目标**：定义标记注解 `@Audited`。

```java
package com.example.audit;

import java.lang.annotation.*;

@Target({ElementType.METHOD})
@Retention(RetentionPolicy.RUNTIME)
public @interface Audited { }
```

**步骤 2 — 目标**：切面只拦截标注方法。

```java
@Around("@annotation(com.example.audit.Audited)")
public Object audit(ProceedingJoinPoint pjp) throws Throwable {
    long t0 = System.nanoTime();
    try {
        return pjp.proceed();
    } finally {
        long ns = System.nanoTime() - t0;
        System.out.println("[AUDITED] " + pjp.getSignature().toShortString() + " ns=" + ns);
    }
}
```

**步骤 3 — 目标（对照实验）**：再写一个 `execution(*..service..*(..))` 的临时切面，压测前后对比 **QPS/CPU**（可用简单 for 循环 + `System.nanoTime()`）。

**验证**：仅标注 `@Audited` 的方法进入切面；未标注的不应输出 `[AUDITED]`。

### 3.3 完整代码清单与仓库

`chapter15-aop-advanced`。

### 3.4 测试验证

- **Spring AopUtils**：判断 Bean 是否代理（理解代理类型）。  
- **单测**：对未标注方法调用，不应出现审计副作用（可用 **OutputCapture** 或自定义 `Metrics` mock）。

**命令**：`mvn -q test`。

**可能遇到的坑**

| 现象 | 原因 | 处理 |
|------|------|------|
| 切面偶发不生效 | 非 public / final | 调整可见性或使用 AspectJ compile-time |
| 指标爆炸 | 高基数 label | 限制 tag（用户ID全量打标） |

---

## 4 项目总结

### 常见踩坑经验

1. **切点过宽** → 性能问题。  
2. **Around 未 proceed** → 业务未执行。  
3. **同类自调用** → 切面不生效。

---

## 思考题

1. `EntityManager` 与 `Repository` 抽象关系？（第 16 章。）  
2. `@Query` 原生 SQL 与 JPQL 选型？（第 16 章。）

---

## 推广协作提示

| 角色 | 建议 |
|------|------|
| **运维** | 指标与 traceId 在切面中统一注入。 |

**下一章预告**：Spring Data JPA、实体映射、懒加载与 N+1。
