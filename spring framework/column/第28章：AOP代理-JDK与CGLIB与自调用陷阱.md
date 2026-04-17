# 第 28 章：AOP 代理——JDK 与 CGLIB 与自调用陷阱

> **业务线**：电商 / 订单履约微服务（拟真场景）。本章可独立阅读；与全书案例弱关联。

## 上一章思考题回顾

1. **JDK 动态代理**：基于 **接口** 反射生成代理类；**目标类** 必须是接口实现。  
2. **`exposeProxy=true`**：把当前代理绑到 **`AopContext.currentProxy()`**，**同类自调用** 可走代理（需 **`@EnableAspectJAutoProxy(exposeProxy=true)`**），但仍应 **优先拆类**。

---

## 1 项目背景

`OrderService` 内 `place()` 调 `validate()`，**`@Transactional` 在 `validate`** 上无效——**自调用** 不经过代理。事务与审计切面 **全部失效**。

---

## 2 项目设计（剧本式对话）

**角色**：小胖 / 小白 / 大师。  
**结构**：`this` vs 代理 → JDK/CGLIB → 自调用为什么踩坑。

**小胖**：`@Transactional` 我写了啊，为啥数据库没回滚？

**大师**：你调用的是 **`this.validate()`**，不是 **代理对象的 `validate()`**。容器里注册的是 **代理对象**，而 `this` 指向 **原始目标**。

**技术映射**：容器中的 Bean 通常是 **代理对象**；`this` 指向目标类。**JdkDynamicAopProxy**（接口）与 **ObjenesisCglibAopProxy**（子类）两条实现路径。

**小白**：JDK 代理为啥一定要接口？

**大师**：JDK 动态代理基于 **接口** 生成代理类；**CGLIB** 通过 **子类** 代理类（`final` 方法/类会受限）。

**小胖**：我把切点改成 `public` 方法，事务还是不行，咋回事？

**大师**：再看是不是 **自调用**；再看是不是 **private**（代理不可见）；再看是不是 **异常类型** 导致不回滚。

---

## 3 项目实战

### 3.1 验证

```java
@Service
public class OrderService {
    public void place() { validate(); }
    @Transactional
    public void validate() { }
}
```

**事务不生效** → 拆到 **`OrderValidator` Bean`** 或 **自注入 `OrderService` 代理**（次选）。

### 3.2 分步实现（推荐修复）

```java
@Service
public class OrderService {
    private final OrderService self;

    public OrderService(@Lazy OrderService self) {
        this.self = self;
    }

    public void place() { self.validate(); }

    @Transactional
    public void validate() { /* ... */ }
}
```

> 注：`@Lazy` 自注入是**演示**手段；**拆类**更干净。

### 3.3 完整代码清单与仓库

`chapter28-aop-proxy`。

### 3.4 测试验证

断言 **TransactionSynchronizationManager.isActualTransactionActive()** 在 `validate()` 内为 **true**（通过代理调用时）。

**命令**：`mvn -q test`。

**可能遇到的坑**

| 现象 | 原因 | 处理 |
|------|------|------|
| 仍无事务 | 非 public / 非代理类 | 调整可见性与代理类型 |

---

## 4 项目总结

### 常见踩坑经验

1. **final 方法** 无法代理。  
2. **类内部** 调 protected 方法。  
3. **同类** 切面顺序误判。

---

## 思考题

1. **`TransactionInterceptor`** 与 **`PlatformTransactionManager`** 协作？（第 29 章。）  
2. **挂起事务** 数据结构？（第 29 章。）

---

## 推广协作提示

| 角色 | 建议 |
|------|------|
| **开发** | 事务方法放 public 且经代理调用。 |

**下一章预告**：事务拦截器源码、`TransactionSynchronizationManager`。
