# 第 20 章：定时与批处理——Scheduling 与任务幂等

> **业务线**：电商 / 订单履约微服务（拟真场景）。本章可独立阅读；与全书案例弱关联。

## 上一章思考题回顾

1. **`@Scheduled` 默认线程池**：单线程 **`ScheduledExecutorService`**（同一调度器串行）；长任务会**阻塞**后续任务 → 应配置 **`TaskScheduler` Bean** 或 **`ThreadPoolTaskScheduler`**。  
2. **幂等键**：用 **业务主键**（如 `orderId`）+ **状态机**；分布式场景用 **数据库唯一约束** 或 **Redis SETNX** 防重。

---

## 1 项目背景

订单 **超时未支付自动关闭**、**每日对账** 需要定时任务。多实例部署时 **重复执行** 会导致**重复关单**或**重复扣款**。

**痛点**：  
- **cron 表达式** 误配导致**瞬间洪峰**。  
- **任务重叠** 未防护。  
- **失败重试** 无上限。

---

## 2 项目设计（剧本式对话）

**角色**：小胖 / 小白 / 大师。  
**结构**：先讲单机调度 → 多实例重复执行 → 幂等与锁。

**小胖**：我用 `while(true) sleep` 也行，为啥要 `@Scheduled`？

**大师**：`@Scheduled` 把 **触发策略**（cron/fixedDelay）从业务里拆出来，并能统一配 **线程池**、**监控**；裸 `sleep` 像**手工敲钟**，不可运维。

**技术映射**：**SchedulingConfigurer** + **cron** + **fixedDelay**。

**小白**：`fixedRate` 和 `fixedDelay` 差啥？

**大师**：**fixedRate** 是「上次开始**后**」多久再跑；**fixedDelay** 是「上次**结束**后」多久再跑。长任务场景选错会**重叠**。

**大师**：单机用 **`@Scheduled`**；多实例需 **分布式锁**（Redis/ZK）或 **分区调度**（Quartz JDBC）。

**小胖**：关单任务跑两次，用户会不会收到两条短信？

**小白**：会，所以要有 **幂等键**（`orderId` + 状态机）+ **唯一约束**；重复执行应变成**无害**。

**大师**：定时任务像**闹钟**：你可以设很多个，但**起床动作**必须是幂等的——别重复扣款。

---

## 3 项目实战

### 3.1 环境准备

| 项 | 说明 |
|----|------|
| 依赖 | `spring-boot-starter`（含 scheduling） |
| 注意 | 多实例必须加 **分布式锁**（Redis/ZK）或 **分区**（超出本章代码，但要在设计里占位） |

### 3.2 分步实现

```java
@Configuration
@EnableScheduling
public class ScheduleConfig { }

@Component
public class OrderTimeoutJob {
    @Scheduled(cron = "0 */1 * * * *")
    public void closeExpired() {
        // 查询 + 更新，配合幂等
    }
}
```

**配置线程池**

```java
@Bean
TaskScheduler taskScheduler() {
    ThreadPoolTaskScheduler s = new ThreadPoolTaskScheduler();
    s.setPoolSize(4);
    s.setThreadNamePrefix("sched-");
    s.initialize();
    return s;
}
```

**步骤 3 — 目标（对照）**：把 `cron` 改成 `fixedDelay=5000`，观察任务重叠与线程池占用（日志打印线程名）。

**运行结果（文字描述）**：日志中出现 `sched-*` 线程执行任务；长任务时观察是否阻塞后续调度（若单线程调度器）。

### 3.3 完整代码清单与仓库

`chapter20-schedule`。

### 3.4 测试验证

使用 **`@SpyBean` + 手动触发** 或 **Awaitility** 等待异步完成；或用 **`TaskScheduler` 的 `schedule` 方法**在测试中显式触发。

**命令**：`mvn -q test`。

**可能遇到的坑**

| 现象 | 原因 | 处理 |
|------|------|------|
| 任务不跑 | 未 `@EnableScheduling` | 检查配置类 |
| 时区错 | cron 按服务器时区 | 用 `zone` 指定 |

---

## 4 项目总结

### 常见踩坑经验

1. **时区**：服务器与业务时区不一致。  
2. **多实例** 重复跑。  
3. **长事务** 占锁。

---

## 思考题

1. `JmsTemplate` 与 `RabbitTemplate` 抽象共性？（第 21 章。）  
2. **本地事务** 与 **消息发送** 一致性？（第 21 章。）

---

## 推广协作提示

| 角色 | 建议 |
|------|------|
| **运维** | 监控任务耗时与失败率。 |

**下一章预告**：Spring Messaging、事务性发件箱。
