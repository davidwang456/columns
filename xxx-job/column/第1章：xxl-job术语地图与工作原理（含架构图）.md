# 第1章：XXL-JOB 术语地图与工作原理（含架构图）

## 1. 项目背景

你们团队接手了一个老系统：订单超时关单靠人工 SQL，营销券过期靠凌晨脚本，库存补偿靠同事记闹钟。系统业务量小时看不出问题，活动峰值一来，问题就集中爆发：任务漏执行、重复执行、执行失败没人知道、线上故障后无法快速回放。更糟的是，调度逻辑分散在不同服务、不同语言、不同机器里，谁都说不清“这条任务到底是谁在调，在哪跑，失败后谁处理”。

这类场景的本质不是“写一个定时器”那么简单，而是“建立一套可管理、可观测、可扩展的分布式任务调度体系”。XXL-JOB 解决的正是这个问题：它把“调度”与“执行”解耦，把任务生命周期标准化，把日志、重试、告警、路由、分片这些原本散落在业务代码里的能力收敛为平台能力。你可以理解为：业务系统只负责“我要做什么”，XXL-JOB 负责“何时做、在哪做、失败怎么办、怎么追踪”。

## 2. 项目设计（剧本式交锋对话）

### 第一轮：先把“系统角色”讲明白

**小胖**：这不就是定时执行方法吗？为啥要搞个调度中心，还要搞执行器，听着就复杂。  
**小白**：我也有疑问。如果我在 Spring 里写 `@Scheduled`，不是也能跑吗？  
**大师**：`@Scheduled` 适合单应用、本地任务。XXL-JOB 的目标是跨应用、跨机器、可治理。你把它想成“总调度台 + 任务工人队”：
- 调度中心（Admin）负责统一编排：任务配置、触发时机、执行记录、告警；
- 执行器（Executor）负责真正执行业务代码；
- 两者通过协议通信，形成任务闭环。

**技术映射**：  
“总调度台”= Admin；“工人队”= Executor；“工单”= Job；“执行记录”= JobLog。

### 第二轮：术语地图怎么记？

**小胖**：术语太多了，JobHandler、路由、分片、阻塞策略，看得我头大。  
**小白**：而且很多名字很像，容易混。有没有记忆方法？  
**大师**：用“三层四类”记忆法。

**三层**：
1. 管理层（管理台看到的配置）：执行器、任务、触发规则、告警；
2. 调度层（Admin 内部线程）：预读、触发池、注册发现、完成处理；
3. 执行层（Executor 内部线程）：接收 run、任务线程执行、回调、日志输出。

**四类术语**：
- **对象类**：执行器、任务、日志、注册信息；
- **时间类**：Cron、FixRate、Misfire（错过触发）；
- **并发类**：路由策略、阻塞策略、失败重试、超时控制；
- **治理类**：告警、权限 accessToken、日志保留、离线摘除。

**技术映射**：  
“术语太多”本质是“没有分层”。分层以后，每个术语都有归属。

### 第三轮：任务到底怎么跑起来？

**小胖**：我最想知道的是，点了“执行一次”以后，底层发生了啥？  
**小白**：对，最好按时间顺序说。  
**大师**：按“7步主链路”记就行：

1. 在管理台创建任务（配置执行器、JobHandler、调度规则）。  
2. Admin 调度线程扫描到任务到期。  
3. Admin 把触发请求丢到触发线程池。  
4. 根据路由策略选择目标执行器地址。  
5. 远程调用执行器的 `run` 接口。  
6. 执行器把请求放入对应 JobThread 执行。  
7. 执行结束后回调 Admin，更新日志与状态，必要时触发告警/子任务。

**技术映射**：  
“点按钮就跑”并不是直接调用方法，而是一条分布式链路。

---

## 3. 项目实战

本节不追求复杂业务，只做“术语 -> 配置 -> 执行 -> 观测”的最小实验，帮助你把概念落地到动作。

### 3.1 环境准备

- JDK：17+
- Maven：3.8+
- MySQL：8.x
- 仓库：`xxl-job`
- 数据初始化脚本：`doc/db/tables_xxl_job.sql`

建议最小化配置：
- Admin 连接本地 MySQL；
- Executor 使用示例工程 `xxl-job-executor-sample-springboot`；
- Admin 与 Executor 统一 `accessToken`。

### 3.2 步骤一：建立“时间类术语”认知（Cron vs FixRate）

**步骤目标**：理解 Cron 与 FixRate 的行为差异。

创建两个测试任务：
- 任务 A：Cron，每分钟触发；
- 任务 B：FixRate，每 30 秒触发。

观察 5 分钟后日志，重点记录：
- 触发间隔是否符合预期；
- 服务重启后是否出现“错过窗口”的补偿行为（Misfire）。

**运行结果（文字描述）**：  
Cron 更偏向“日历语义”，FixRate 更偏向“固定间隔语义”。在高负载或停机恢复时，两者可观测行为会有区别。

**可能遇到的坑**：
- 把 Cron 当成“每隔 N 秒”表达，导致规则理解偏差；
- 忽略时区与服务器时间同步，导致触发时间漂移。

### 3.3 步骤二：建立“并发类术语”认知（路由与阻塞）

**步骤目标**：通过人为制造冲突理解策略意义。

准备一个慢任务（执行 90 秒），调度间隔设为 30 秒，然后分别测试阻塞策略：
- 串行；
- 丢弃后续；
- 覆盖之前。

再在同一 `AppName` 下部署两个执行器实例，观察路由策略在多实例下的分配行为。

**运行结果（文字描述）**：  
你会看到“同样的任务代码，不同阻塞策略导致完全不同运行轨迹”；多实例场景下，路由策略决定任务是否均衡、是否偏向健康节点。

**可能遇到的坑**：
- 本地只有一个执行器实例却讨论路由均衡，结论会失真；
- 慢任务未加业务幂等，覆盖策略下可能出现数据副作用。

### 3.4 步骤三：建立“治理类术语”认知（重试、超时、告警）

**步骤目标**：理解失败后的平台行为，不只关注成功路径。

1. 人为抛出异常，设置失败重试次数；
2. 人为 sleep 超过超时阈值；
3. 配置告警通道（如邮件）并验证通知。

**运行结果（文字描述）**：  
日志中可看到重试次数、失败原因、超时状态变化；告警触发后能形成运维闭环。

**可能遇到的坑**：
- 以为“重试一定成功”，但失败根因若是数据脏状态，可能持续失败；
- 告警未分级，导致通知噪声过高。

### 3.5 关键代码片段（可直接运行思路）

```java
@XxlJob("demoJobHandler")
public void demoJobHandler() throws Exception {
    String param = XxlJobHelper.getJobParam();
    XxlJobHelper.log("job start, param={0}", param);
    // 模拟业务
    Thread.sleep(1000);
    XxlJobHelper.log("job done");
}
```

这个片段对应了本章三个关键术语：
- JobHandler：任务执行入口；
- JobParam：任务参数；
- XxlJobHelper.log：执行日志可观测入口。

### 3.6 测试验证建议

- 功能验证：手动触发，确认任务成功与日志可见；
- 时间验证：Cron/FixRate 各跑 5 分钟，核对触发间隔；
- 并发验证：慢任务 + 高频触发，观察阻塞策略差异；
- 稳定性验证：停掉一个执行器实例，观察路由与 failover。

### A. 源码路径映射表

| 模块 | 路径 | 关注点 |
|---|---|---|
| Admin 启动入口 | `xxl-job-admin/src/main/java/com/xxl/job/admin/XxlJobAdminApplication.java` | 管理端如何装配调度组件 |
| 调度主线程 | `xxl-job-admin/src/main/java/com/xxl/job/admin/scheduler/thread/JobScheduleHelper.java` | 到期扫描与预读窗口 |
| 触发入口 | `xxl-job-admin/src/main/java/com/xxl/job/admin/scheduler/trigger/JobTrigger.java` | 路由、阻塞、重试参数拼装 |
| 执行线程 | `xxl-job-core/src/main/java/com/xxl/job/core/thread/JobThread.java` | 单 JobHandler 串行消费与超时控制 |
| 回调收敛 | `xxl-job-admin/src/main/java/com/xxl/job/admin/scheduler/thread/JobCompleteHelper.java` | 执行结果回写与状态闭环 |

### B. 验证命令块

```bash
# 步骤1：进入仓库并构建 Admin
cd d:/software/workspace/xxl-job
mvn -pl xxl-job-admin -am clean package -DskipTests

# 步骤2：启动 Admin（端口统一为 8080，示例库名 xxl_job）
mvn -pl xxl-job-admin spring-boot:run -Dspring-boot.run.arguments="--server.port=8080 --spring.datasource.url=jdbc:mysql://127.0.0.1:3306/xxl_job?useUnicode=true&characterEncoding=UTF-8&autoReconnect=true&serverTimezone=Asia/Shanghai"

# 步骤3：启动 Executor（端口统一为 9999，AppName 统一）
mvn -pl xxl-job-executor-samples/xxl-job-executor-sample-springboot -am spring-boot:run -Dspring-boot.run.arguments="--server.port=9999 --xxl.job.executor.appname=xxl-job-executor-sample --xxl.job.accessToken=default_token"

# 步骤4：核验调度日志落库
mysql -uroot -p -e "select id,job_id,trigger_code,handle_code from xxl_job.xxl_job_log order by id desc limit 10;"
```

---

## 4. 项目总结

### C. 故障复盘表格

| 现象 | 根因 | 处置 | 预防 |
|---|---|---|---|
| 控制台触发成功但执行器无日志 | 执行器注册地址错误或 token 不一致 | 修正 `xxl.job.admin.addresses` 与 `accessToken`，重启后复测 | 发布前固定做“地址+token+端口”三项检查 |
| 出现重复执行 | 任务未做幂等，重试叠加副作用 | 增加幂等键与条件更新，核对历史脏数据 | 任务评审必须包含“重试副作用”检查项 |
| Running 长时间不结束 | 下游调用卡住且未限时 | 设置超时、拆批执行、失败快速返回 | 基于 p95/p99 周期调整超时阈值 |

### 优点
- 调度与执行解耦，适合多服务、多团队协作；
- 内置日志、重试、告警、路由、分片，治理能力完整；
- 可从“能跑”平滑演进到“可观测、可运维、可扩展”。
### 缺点
- 分布式链路增加了配置和排障复杂度；
- 任务代码若不具备幂等与容错，平台能力会被抵消。
### 适用场景
- 数据定时同步、报表生成、批处理任务；
- 多服务共享调度能力、需要集中治理和审计的任务系统；
- 需要失败重试、执行日志追踪、任务分片并行场景。

## 本章一页纸速记
### 核心结论
- XXL-JOB 的本质是“中心化调度 + 分布式执行 + 结果回调闭环”。
- Admin 负责编排治理，Executor 负责执行落地，JobHandler 负责业务逻辑。
- 稳定运行依赖术语统一、链路清晰和证据化验证。

### 落地清单
- 固化启动命令参数：8080（Admin）、9999（Executor）、`xxl_job`、`xxl-job-executor-sample`。
- 每个任务评审至少覆盖触发、路由、阻塞、重试、超时五维。
- 发布前执行一次手动触发并核对 `xxl_job_log` 回写结果。
- 故障后按复盘表补齐根因、处置、预防，持续更新团队模板。

### 常见误区
- 混用术语导致开发、测试、运维沟通偏差。
- 忽略 accessToken 与时间同步，导致隐性稳定性问题。

