# 第20章：JobTrigger 与 TriggerPool——一次触发请求如何并发发射

## 1. 项目背景

从调度线程挑出任务只是第一步，真正把执行命令送到执行器，要经过触发构建、路由选择、线程池提交、远程调用和结果回写。这里的核心是 `JobTrigger` 与 `JobTriggerPoolHelper`。  

线上常见问题包括：同一时刻大量任务触发造成线程池拥塞、路由策略配置不当导致某台执行器过载、失败重试触发放大雪崩。你如果不理解触发链路，就很难解释“为什么任务明明被调度到了，却迟迟没执行”。  

本章重点走读路径（文本路径）：
- `xxl-job-admin/src/main/java/com/xxl/job/admin/core/trigger/XxlJobTrigger.java`
- `xxl-job-admin/src/main/java/com/xxl/job/admin/core/thread/JobTriggerPoolHelper.java`
- `xxl-job-admin/src/main/java/com/xxl/job/admin/core/route/ExecutorRouteStrategyEnum.java`
- `xxl-job-admin/src/main/java/com/xxl/job/admin/core/complete/XxlJobCompleter.java`

目标：掌握“触发请求从生成到下发再到结果落地”的关键节点，并能做并发与路由验证。

---

## 2. 项目设计（剧本式交锋对话）

### 第一轮：触发动作拆解

**小胖**：触发不就是发个 RPC？  
**小白**：前面还有参数组装、分片参数、重试次数和路由决策。  
**大师**：`XxlJobTrigger` 先构建 `TriggerParam`，再依据路由策略选执行器地址，最后提交到触发池并发执行。  
**技术映射**：触发不是“打电话”，而是“组包 + 选线 + 派单”。  

### 第二轮：为什么要分快慢线程池

**小胖**：一个线程池不就行了？  
**小白**：慢任务会拖住快任务，造成队头阻塞。  
**大师**：触发池通常会区分 fast/slow 路径，避免长耗时调用拖垮整体吞吐。  
**技术映射**：把快车道和慢车道分开，整体通行效率更高。  

### 第三轮：路由策略怎么选

**小胖**：默认随机行不行？  
**小白**：要看任务类型。状态型任务更适合一致性路由，无状态任务适合轮询或随机。  
**大师**：路由策略不是“性能按钮”，是“业务一致性与负载均衡”折中。  
**技术映射**：选路由等于选稳定性模型。  

### 第四轮：失败重试会不会放大故障

**小胖**：失败就多重试几次，总能成功吧？  
**小白**：如果执行器整体故障，重试只会放大流量。  
**大师**：重试要配合熔断思路和告警，先判断局部故障还是系统性故障。  
**技术映射**：重试是止损工具，不是万能药。  

---

## 3. 项目实战

### 3.1 环境准备

- 启动 admin + 2 个 executor 实例（同 appname，不同地址）  
- 创建一个短周期任务，路由策略可切换  
- 准备观察 admin 触发日志与 executor 执行日志  

### 3.2 可运行步骤与验证

**步骤目标 1：验证路由策略效果**  
先用轮询，再切到随机，再切一致性哈希（如果业务允许）。  
验证点：不同策略下执行器命中分布变化是否符合预期。

**步骤目标 2：验证触发池并发能力**  
同一时间触发多个任务，观察日志中的触发耗时。  
验证点：任务触发是否出现明显排队；慢任务是否影响快任务触发。

**步骤目标 3：验证失败重试链路**  
手动停掉一个 executor，再触发任务。  
验证点：是否按路由策略与失败策略进行回退、重试或失败落库。

### 3.3 命令示例

```bash
mvn -pl xxl-job-admin spring-boot:run
```

```bash
mvn -pl xxl-job-executor-samples/xxl-job-executor-sample-springboot spring-boot:run
```

### 3.4 常见坑

- 坑 1：忽略执行器负载差异，盲目轮询  
  - 建议：高耗时任务优先用故障转移或带状态感知策略。  
- 坑 2：慢任务与快任务共用参数配置  
  - 建议：按任务类型拆分执行器组或拆分 handler。  
- 坑 3：失败重试过高，故障时雪上加霜  
  - 建议：控制重试上限并配置告警，必要时人工介入。  

### 3.5 关键类职责与调用链（重点）

- `XxlJobTrigger`：触发编排核心。负责读取任务配置、组装 `TriggerParam`、计算分片参数、应用路由策略。  
- `JobTriggerPoolHelper`：触发执行器。负责把触发动作提交到快慢线程池，避免单一慢调用拖慢全局。  
- `ExecutorRouteStrategyEnum`：路由决策器。根据策略选择执行器地址，体现稳定性与均衡性的取舍。  
- `XxlJobCompleter`：结果收敛器。回写执行结果并驱动后续重试/告警逻辑。

主调用链：  
`JobScheduleHelper -> XxlJobTrigger.trigger -> JobTriggerPoolHelper.addTrigger -> route choose address -> ExecutorBiz.run -> XxlJobCompleter`

### 3.6 源码路径映射表

| 模块 | 源码路径 | 说明 |
| --- | --- | --- |
| 触发编排 | `xxl-job-admin/src/main/java/com/xxl/job/admin/core/trigger/XxlJobTrigger.java` | 触发参数与请求构建 |
| 触发池 | `xxl-job-admin/src/main/java/com/xxl/job/admin/core/thread/JobTriggerPoolHelper.java` | 快慢池队列与并发 |
| 路由策略 | `xxl-job-admin/src/main/java/com/xxl/job/admin/core/route/ExecutorRouteStrategyEnum.java` | 目标执行器选择逻辑 |
| 路由实现 | `xxl-job-admin/src/main/java/com/xxl/job/admin/core/route/strategy/` | 各路由算法细节 |
| 执行结果落库 | `xxl-job-admin/src/main/java/com/xxl/job/admin/core/complete/XxlJobCompleter.java` | 执行结果回写 |

### 3.7 验证命令块（最小可验证）

```bash
mvn -pl xxl-job-admin spring-boot:run
```

```bash
mvn -pl xxl-job-executor-samples/xxl-job-executor-sample-springboot spring-boot:run
```

```bash
# 第二个终端再起一个 executor 实例后，观察同任务路由分布
mvn -pl xxl-job-executor-samples/xxl-job-executor-sample-springboot -Dserver.port=9998 spring-boot:run
```

```bash
# 查看触发与执行结果码
mysql -h127.0.0.1 -uroot -p -e "select id,job_id,executor_address,trigger_code,handle_code from xxl_job_log order by id desc limit 20;" xxl_job
```

### 3.8 故障复盘表格

| 故障表现 | 定位点 | 根因 | 改进措施 |
| --- | --- | --- | --- |
| 慢任务拖慢全局触发 | 触发池 | 快慢任务未隔离 | 拆分快慢池阈值与任务分组 |
| 某台执行器被打满 | 路由层 | 路由策略与任务特征不匹配 | 按任务类型选择路由 |
| 重试风暴导致雪崩 | 结果回写层 | 重试策略激进且无熔断 | 下调重试上限+告警分级 |

---

## 4. 项目总结

`XxlJobTrigger` 和 `JobTriggerPoolHelper` 共同决定了调度指令的“发射质量”。前者重在正确组装与路由，后者重在并发隔离与吞吐保障。  

理解这一层后，你可以系统回答三个问题：任务为何发到了这台机器、为何发得慢、为何失败后恢复不符合预期。这比“调大线程池试试看”更可靠。  

思考题：
1) 如果任务需要固定命中同一执行器实例，你会怎么配置路由并验证？  
2) 快慢池阈值如何依据业务负载做动态调整？  

## 本章一页纸速记

**3条结论**
- `XxlJobTrigger` 决定触发请求质量，`JobTriggerPoolHelper` 决定并发发射效率。
- 路由策略本质是稳定性与均衡性的权衡，必须与任务状态特征匹配。
- 失败重试需要边界控制与告警协同，否则会把局部故障放大为系统性故障。

**5条落地清单**
- 统一术语：统一使用“触发编排、路由决策、快慢池隔离、结果回写”。
- 统一参数：任务配置统一审核 `routeStrategy`、`failRetryCount`、`timeoutSeconds`。
- 统一命令：固定“启动 Admin、启动双 Executor、触发任务、查询 `xxl_job_log` 地址分布”。
- 统一注释：命令注释统一为“步骤N：动作 + 观测指标”。
- 统一演练：每次变更至少演练一次“节点下线 + 重试 + 告警”链路。

**3条误区**
- 误区一：慢任务与快任务共用同一触发池策略，导致队头阻塞。
- 误区二：只看路由命中是否均匀，不看业务一致性要求。
- 误区三：将重试次数上调作为默认修复手段，忽略熔断与人工介入阈值。
