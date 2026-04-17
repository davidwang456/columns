# 第 38 章：事务源码——TransactionManager 与同步器

> **业务线**：电商 / 订单履约微服务（拟真场景）。本章可独立阅读；与全书案例弱关联。

> **篇章**：高级篇（全书第 36–50 章；源码、极端场景、扩展、SRE）

## 上一章思考题回顾

1. **`TransactionInterceptor`** 调用 **`invokeWithinTransaction`**，内部根据 **`TransactionAttribute`** 从 **`PlatformTransactionManager`** 获取/提交/回滚事务。  
2. **挂起**：`TransactionSynchronizationManager` 维护 **ThreadLocal** 栈式事务；`REQUIRES_NEW` 将外层 **挂起** 为 **SuspendedResourcesHolder**。

---

## 1 项目背景

**`readOnly` 事务** 为何偶发写失败？**`rollback-only`** 标记被内层设置后外层提交失败。需从源码理解 **事务状态传播**。

---

## 2 项目设计（剧本式对话）

**角色**：小胖 / 小白 / 大师。  
**结构**：连接与线程绑定 → `rollback-only` 传播 → 挂起/恢复直觉。

**小胖**：事务不就是 `commit/rollback` 吗？为啥还要 `TransactionSynchronizationManager`？

**大师**：Spring 要把 **同一数据源连接** 绑定到 **当前线程**，并支持 **嵌套事务** 的 **挂起/恢复**；`TransactionSynchronizationManager` 维护 **资源与同步回调**。

**技术映射**：**TransactionSynchronizationManager.getResource(dataSource)**。

**小白**：`rollback-only` 是啥？

**大师**：内层标记「**最终只能回滚**」，外层如果还想 `commit`，会被拒绝或转化为回滚——**错误吞掉**时最常见。落地实现上，`AbstractPlatformTransactionManager` 负责 **begin/commit/rollback**；**DataSourceTransactionManager** 把 **Connection** 绑定到线程。

---

## 3 项目实战

### 3.1 阅读

1. `TransactionAspectSupport`  
2. `DataSourceTransactionManager.doBegin`  
3. `TransactionSynchronizationManager` 绑定/解绑

### 3.2 调试

在 `commit` / `rollback` 下断点，配合 **第 23 章** 的 `REQUIRES_NEW` 示例，观察 **suspend/resume** 日志。

### 3.3 完整代码清单与仓库

本地源码 + **chapter14-tx-advanced** 示例联动阅读。

### 3.4 测试验证

调试 **`REQUIRES_NEW`** 嵌套调用，观察 **suspend/resume**。

**可能遇到的坑**

| 现象 | 原因 | 处理 |
|------|------|------|
| 多数据源事务乱 | 多 `PlatformTransactionManager` | 明确 `transactionManager` |

---

## 4 项目总结

### 常见踩坑经验

1. **多数据源** 未正确路由 `TransactionManager`。  
2. **只读事务** 路由错误。  
3. **异常被吞** 导致 `rollback-only` 未生效。

---

## 思考题

1. **`HandlerMapping`** 与 **`HandlerAdapter`** 分工？（第 29 章。）  
2. **`@RequestBody`** 解析时机？（第 29 章。）

---

## 推广协作提示

| 角色 | 建议 |
|------|------|
| **资深开发** | 结合 ARTHAS watch 事务方法。 |

**下一章预告**：`DispatcherServlet`、映射、参数解析链。
