# 第 6 章：服务任务——JavaDelegate 与 Spring Bean 委托

> 版本说明：`delegateExpression` 依赖 Flowable 与 Spring 的集成 Bean 可见性。

---

## 1）项目背景

采购申请在到达经理审批前，星云科技要求自动调用「预算占用校验」与「供应商黑名单查询」。这类**无 UI、需同步调用业务服务**的步骤，适合建模为 **Service Task**。若校验失败，后续章节可用错误边界中断；本章只做**同步成功路径**与**委托表达式**两种接入方式。

**本章聚焦**：`serviceTask` 的 **class** / **delegateExpression**、`JavaDelegate` 接口、以及 Spring Bean 名称与 **字段注入（field injection）** 的配置方式。

---

## 2）项目设计（小胖 × 小白 × 大师）

> **角色分工**：**小胖**（爱吃爱玩、不求甚解）用生活化、口语化的方式把话题「开球」，先把问题问出来；**小白**（喜静、喜深入）负责追问原理、边界、风险与可比方案；**大师**（资深技术 Leader）把业务约束与技术选型说透，善打比方、由浅入深。

**小胖**：服务任务和 REST 调用外部系统是一回事吗？

**大师**：都可，但服务任务通常指**在当前 JVM 内执行委托类**；调外部常写进 Delegate 里再调 HTTP/OA。别把「巨长阻塞 HTTP」无节制堆在同步路径——必要时改异步（第 17 章）。

**小白**：用 class 还是 expression？

**大师**：**纯 Java 类名**用 `flowable:class`；要注入 Spring Bean、单测 mock，用 **`delegateExpression="${budgetCheckDelegate}"`** 更常见。

**小胖**：Delegate 里能 Autowired 吗？

**大师**：在 Spring 环境下，Delegate 作为 Bean 可用 `@Autowired`；非 Spring 环境要用 **ApplicationContext** 静态持有（不推荐）。确保 Flowable **SpringJobExecutor** / 引擎 Bean 扫描到该类。

**小白**：字段注入 `${}` 哪里来的？

**大师**：来自 **BPMN 上的 `flowable:field`**，会注入到 Delegate 成员。注意**类型与表达式求值**。

**小胖**：异常了会怎样？

**大师**：同步异常会**回滚当前事务**（按 Spring 事务传播）；若需走业务分支，应用 **边界错误**（第 19 章），而不是到处 `catch` 吃光。

**小白**：能访问流程变量吗？

**大师**：`DelegateExecution.getVariable`** / setVariable**。大量读写要考虑性能与序列化（第 7 章）。

**小胖**：同一服务被多处复用？

**大师**：Delegate 里写纯业务，流程 id 与变量名当参数；或封装为 domain service 注入。

**小白**：与 Camel/外部路由集成？

**大师**：可扩展；主线先把 **JavaDelegate** 跑稳。

---

### 一页纸决策表

| 输入 | 输出 | 适用场景 | 不适用/慎用 | 与上下游章节关系 |
|------|------|----------|-------------|----------------|
| 业务规则计算、RPC、DB 校验 | 原子业务步骤执行业务 | 同步校验、积分扣减 | 长事务、重试需消息 | 第 7 章变量；第 19 章错误 |

---

## 3）项目实战（主代码片段）

### 3.1 JavaDelegate 实现

```java
@Component("budgetCheckDelegate")
public class BudgetCheckDelegate implements JavaDelegate {
    @Override
    public void execute(DelegateExecution execution) {
        Long amount = (Long) execution.getVariable("amount");
        boolean ok = amount != null && amount > 0;
        execution.setVariable("budgetOk", ok);
    }
}
```

### 3.2 BPMN 片段

```xml
<serviceTask id="checkBudget" name="预算校验"
             flowable:delegateExpression="${budgetCheckDelegate}"/>
```

### 3.3 JUnit 片段

```java
runtimeService.startProcessInstanceByKey("procurementWithService",
    "BK-CH06", Map.of("amount", 15000L));
// 断言后续节点变量 budgetOk
```

### 3.4 curl

若仅服务端任务无可直接 curl 用户步；可用 REST 启流程后查变量：

```bash
curl -s -u demo:demo \
  "http://localhost:8080/flowable-rest/service/runtime/process-instances/{id}/variables"
```

---

## 4）项目总结

### 优点

- 同步、直观，易于单元测试 Delegate 内逻辑。  
- Spring Bean 与全栈统一事务（在 `@Transactional` 边界内时需注意）。

### 缺点

- 阻塞 IO 易拖慢线程池；  
- 委托类异常若不分类，问题都变成「流程失败」难区分_retry。

### 典型使用场景

- 预算、库存预占、费率试算。

### 注意事项

- **幂等**：同一 execution 重试时 Delegate 是否可重入。  
- **事务**：Listener + ServiceTask 组合时的提交顺序。

### 反例

在 Delegate 里直接 `Thread.sleep(30000)` 调外部——压测时线程池打满。**纠正**：短超时 + 异步重试或消息驱动。

### 常见踩坑

| 现象 | 根因 | 处理 |
|------|------|------|
| Bean 找不到 | 名称与表达式不一致 | 检查 `@Component("name")` |
| 变量 ClassCastException | 存了 Integer 当 Long | 统一类型或在 EL 中转 |
| 重复执行 | 重试与业务未幂等 | 业务 token/去重表 |
| 事务不回滚 | 自己 try-catch 吞异常 | 需要失败边界则抛出或映射错误事件 |

### 给测试的一句话

对 **Delegate 单测** + **流程集成测** 分层；集成测断言变量 **`budgetOk`** 与后续网关路径。

### 给运维一句话

关注 **同步步骤耗时** P99；与数据库/下游 SLA 放同一监控仪表盘。
