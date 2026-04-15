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

*监控里 External Task 队列在涨，旁边有人喊「这不跟我们 Kafka 消费者一样嘛」。*
**小胖**：都是拉活干，**跟 MQ consumer 有啥本质区别**？  
**小白**：MQ 保的是**投递语义**；External Task 保的是**引擎工作项 + 锁 + 重试**——状态活在引擎表里，`fetchAndLock` 抢锁，不是一种宗教。  
**大师**：`lockDuration` 太短？锁老过期，**重复执行**风险上来；太长？Worker 挂了别人半天接不了。**崩了谁接盘**——解锁后别的 Worker 捡到，所以业务必须**幂等**。Kafka 常见两种姿势：消息驱动下游再去 `complete`，或者 Kafka 只做总线，和 External Task **并行存在**，别强行二选一神话。  
**小白**：Worker 疯狂拉取，支付渠道不会被打挂？  
**大师**：ET 管**任务分配**，不管你下游 QPS。限流、批量、熔断自己挂——别把支付公司对不住。  

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

## 6. 课后作业（可选）

1. 起一个最小 **Worker**，打印 topic、lock、重试日志。  
2. 调三档 **lockDuration**，观察吞吐与重复执行风险。  
3. 压测 **fetch 批大小** 与下游承受能力。  
4. 写 **故障注入**：Worker 崩溃后任务是否可被其他副本拾取。  
5. 与 SRE 对齐 **HPA** 指标：CPU 还是队列深度。  

## 7. 章末提要（面向推广口播）

External Task 的本质是把「**执行**」从引擎进程剥离，用 **topic+锁** 做分布式队列语义。对业务方可以说：Worker 就像外包团队，引擎发工单；对运维可以说：扩 Worker=扩产能，但要盯 **锁与重试**。**记住 complete 不调用，工单永远挂。**

## 8. 深度追问（写给半年后的自己）

1. topic 命名是否纳入 **架构治理**（前缀/环境）？  
2. Worker **语言/运行时**是否多样化？标准镜像？  
3. lockDuration 与 **下游超时**关系是否表驱动？  
4. **跨区域**部署时，时钟与网络 RTT 是否评估？  
5. **死信**与 **人工补偿**如何挂钩？  
6. 若 Worker 使用 **k8s job**，与长驻 Deployment 选型依据？  

**补白**：External Task 把运维边界往外推了一米；这一米常常最疼。 
