# 第 14 章：External Task 模式：解耦与弹性伸缩

## 元信息

| 项目 | 内容 |
|------|------|
| 章节编号 | 第 14 章 |
| 标题 | External Task 模式：解耦与弹性伸缩 |
| 难度 | 进阶 |
| 预计阅读 | 40～45 分钟 |
| 受众侧重 | 开发 + 运维 |
| 依赖章节 | 第 6 章 |
| 环境版本 | `baseline-2026Q1` |

---

## 1. 项目背景

同步 Delegate 适合短逻辑；当任务涉及**长耗时调用、异构语言、独立扩缩**时，把执行从引擎进程挪到 **External Task Worker** 是常见架构。引擎只负责记录待处理外部任务，Worker **拉取—加锁—执行—完成**。本章要解决的**一条主线问题**是：理解 **topic、lock、重试** 语义，并规划 **Worker 部署与监控**（与第 25 章架构呼应）。

---

## 2. 项目设计（三角色对话）

### 2.1 小胖开球

小胖说：「这不就是消息队列消费者吗？」

相似但不等同：External Task 由引擎维护**任务状态与重试**，Worker 用 **fetchAndLock** 拉取；MQ 由中间件保证投递语义。选型看组织中间件成熟度与运维偏好。

### 2.2 小白追问

小白问：「第一，**lockDuration** 过短会怎样？第二，Worker 崩溃任务谁接管？第三，与第 15 章 Kafka 集成关系？」

### 2.3 大师定调

大师归纳：

- **锁**：防止多 Worker 重复执行；过期可回收重试。
- **崩溃**：任务解锁后被其他 Worker 拉取；业务必须**幂等**。
- **Kafka**：常见模式是 Kafka 消费后调用业务，再 `complete` external task；或 Kafka 仅作事件总线，与 External Task 并行存在。

### 2.4 背压：拉取速率与下游 QPS

小白追问：「Worker 拉太猛会不会把支付渠道打挂？」大师答：需要 **限流**、**批量**与 **熔断**；External Task 解决的是「工作分配」，不是「下游无限吞吐」。

---

## 3. 项目实战

### 3.1 环境前提

- BPMN 中服务任务类型为 `external`，指定 `topic`。
- 使用官方或自研 Worker SDK。

### 3.2 步骤说明

1. 建模 external 任务 `topic=charge`。
2. 启动 Worker：订阅 `charge`，`fetchAndLock`。
3. 发起流程，观察 Worker 收到任务；执行成功后 `complete`。
4. 模拟处理超时（不 complete），观察锁过期与重试。
5. 运维侧记录：Worker 副本数、处理延迟指标（第 21 章）。

### 3.3 源码与说明

Worker 伪代码：

```java
List<LockedExternalTask> tasks = externalTaskService.fetchAndLock(10, "worker-1", true)
  .topic("charge", 60000)
  .execute();
for (LockedExternalTask t : tasks) {
  try {
    chargeClient.charge(t.getVariable("orderId"));
    externalTaskService.complete(t.getId(), "worker-1", null);
  } catch (Exception ex) {
    externalTaskService.handleFailure(t.getId(), "worker-1", ex.getMessage(), 0, 0);
  }
}
```

**为什么 handleFailure**：可配置重试次数与退避；避免无限打爆下游。

### 3.4 验证

- 多 Worker 仅一条执行成功；失败可重试；Cockpit 可见外部任务状态。

### 3.5 运维面板建议

- Worker **拉取速率**、**处理耗时**、**失败率**  
- **锁等待**与 **重试队列**  
- 下游 HTTP **429/5xx** 比例  

---

## 4. 项目总结

| 维度 | 内容 |
|------|------|
| 优点 | 弹性伸缩；进程解耦；技术栈自由。 |
| 缺点 / 代价 | 网络、锁、幂等与运维复杂度上升。 |
| 适用场景 | 长耗时、异构、需 HPA 的工作负载。 |
| 不适用场景 | 极短同步逻辑仍在同进程 Delegate。 |
| 注意事项 | 幂等、超时、监控、背压。 |
| 常见踩坑 | 忘记 complete；锁过短导致风暴重试。 |

**延伸阅读**：第 15 章消息边界；第 25 章架构。

## 5. 附录：与消息队列选型

若已有成熟 MQ 运维体系，可用 MQ 做 **事件总线**，External Task 做 **引擎工作项**；不要强行二选一，但要统一 **幂等键** 与 **监控**。

