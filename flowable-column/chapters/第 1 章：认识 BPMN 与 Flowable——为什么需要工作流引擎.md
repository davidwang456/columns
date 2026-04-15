# 第 1 章：认识 BPMN 与 Flowable——为什么需要工作流引擎

> 版本说明：概念章；示例依赖与 [VERSION_MATRIX.md](../VERSION_MATRIX.md) 默认矩阵 A 对齐。

---

## 1）项目背景

星云科技正在为制造企业客户提供「采购与合同」一体化能力。销售侧抛出需求：「采购单要能按金额走不同审批路径，超过 50 万要副总批，还要能随时看到这单卡在谁那儿——最好系统里一眼能看出来。」

如果只在业务表里用 `status` 字段加若干 if-else，团队很快会发现：路径一改就要动代码；合规部门要审计「谁在何时点了同意」时，日志补不全；跨系统回调失败时，状态机还容易「半中间」卡住。**本章要引入的对象**是：用 **BPMN** 描述业务流程，用 **Flowable** 这类引擎在运行时驱动流程、产生待办与历史，而不是把流程硬编码在应用里。

这一章不急着写完整 Spring 工程，而是建立心智模型：**流程定义 / 流程实例 / 任务 / 引擎服务**各是什么，以及 Flowable 在系统里扮演何种角色。

---

## 2）项目设计（小胖 × 小白 × 大师）

> **角色分工**：**小胖**（爱吃爱玩、不求甚解）用生活化、口语化的方式把话题「开球」，先把问题问出来；**小白**（喜静、喜深入）负责追问原理、边界、风险与可比方案；**大师**（资深技术 Leader）把业务约束与技术选型说透，善打比方、由浅入深。

**小胖**：老板就说了一句话，加几个状态字段不够吗？搞什么 BPMN？

**大师**：够的前提是——流程永远不变、不需要留痕、没有「退回、转办、并行、子流程」。你们一旦要改审批链，就要发版改代码，这就是技术债。BPMN 是用标准图形把**控制流**画清楚：谁先后、谁并行、遇条件走哪条。字段只管业务数据，不管控制流。

**小白**：那和我们自己画个流程图有啥区别？

**大师**：区别在**可执行**。BPMN 图配上引擎，就变成「运行中的状态机 + 待办池 + 历史审计」。图画完不是挂在 Wiki 上，而是部署成 **流程定义**，每次业务触发就是起一个 **流程实例**。

**小胖**：Flowable 是干嘛的？

**大师**：它是 **BPMN 2.0 运行时**的一种实现。你部署 XML（或模型导出的 BPMN），它就解析成内部对象图；你调用 API 启动实例、完成任务，它负责落库、推进令牌、触发服务任务、记历史。

**小白**：Activiti、Camunda 呢？

**大师**：同属一类。选型看许可证、社区活跃度、和你们栈（Spring）的集成、运维成本。本专栏以 Flowable 为主线，概念大多可平移。**本章只要记住**：先有标准语义（BPMN），再谈具体引擎。

**小胖**：「流程实例」和「工单」是一个东西吗？

**大师**：口语里常混着说。严格讲：**流程实例**是引擎里的运行实体；**用户任务**在待办列表里呈现为「待办工单」。一个实例里可能有多个用户任务依次或并行出现。

**小白**：我们系统的 `orderId` 怎么和引擎对上？

**大师**：用 **businessKey**（业务键）把业务主键挂到流程实例上，查询时按 businessKey 找实例，比在变量里瞎存稳。

**小胖**：听起来表会很多？

**大师**：会。那是用**数据库存状态**换**业务代码里的隐式状态机**——像你把衣柜腾给换季棉被：占地，但比你每次翻箱倒柜找状态更省心。后面章节会讲历史级别、归档——现在先接受这笔「空间换秩序」的交易。

**小白**：那我作为产品要学画 BPMN 吗？

**大师**：要会读、会提需求。细节可以由业务和开发一起在 Modeler 上对齐，减少「我以为你懂了」的翻车。

---

### 一页纸决策表

| 输入 | 输出 | 适用场景 | 不适用/慎用 | 与后续章节关系 |
|------|------|----------|-------------|----------------|
| 业务规则多变的审批、履约、售后；强审计 | 可执行的流程定义、可查询的实例与历史 | 多级审批、并行会签、定时升级、跨系统编排 | 极简两步且永不改；极高频短事务若未做异步可能不合适 | 第 2 章搭环境；第 3 章首次部署运行 |

---

## 2.1）附：BPMN 2.0 最小可读片段（与引擎的对齐关系）

下图为「文字版 BPMN」：**圆形**通常为开始/结束事件，**圆角矩形**多为活动（含用户任务），**菱形**为网关（后续章节展开）。Flowable 读取的是符合 BPMN 2.0 的 XML（`.bpmn20.xml` 等为惯例命名）。引擎解析后，会在内部建立**执行树**：并行处会产生多个**执行分支**；用户任务处会创建**任务实例**并进入待办。

一个可执行流程至少要：**可开始的入口**、**可达的终点**、活动之间的**顺序流**合法。初学者易犯：漏连顺序流、网关出向不全、把业务规则写在脚本里却未处理异常路径——都会在运行期表现为「卡住」或「走错分支」，这些问题在第 4～12 章会逐项拆掉。

---

## 3）项目实战（主代码片段）

下列片段为**示意**：展示引擎门面与一次「启动—完成用户任务」的主路径。完整工程见第 2～3 章；此处强调**对象出现顺序**。

**（1）引擎与核心服务（概念代码）**

```java
// 假设已通过 Spring Boot 注入 ProcessEngine（矩阵 A）
ProcessEngine processEngine = ...;

RuntimeService runtimeService = processEngine.getRuntimeService();
TaskService taskService = processEngine.getTaskService();
HistoryService historyService = processEngine.getHistoryService();

// 按流程定义的 key 启动流程实例，并挂上业务键
String processDefinitionKey = "procurementRequest";
String businessKey = "PO-2026-0001";
Map<String, Object> variables = Map.of("amount", 120000);

var processInstance = runtimeService
    .startProcessInstanceByKey(processDefinitionKey, businessKey, variables);

String processInstanceId = processInstance.getId();
```

**（2）查询待办并完成（概念代码）**

```java
var task = taskService.createTaskQuery()
    .processInstanceId(processInstanceId)
    .singleResult();

taskService.complete(task.getId(), Map.of("approved", true));
```

**读者可自查**：完成后 `act_ru_task` 中该任务应消失（取决于模型是否还有后续节点），历史表中出现已完成记录（视历史级别而定）。

**（3）JUnit 断言（二选一中的「测试」示例）**

```java
import static org.assertj.core.api.Assertions.assertThat;

@Test
void processShouldReachFirstUserTask() {
    var instance = runtimeService.startProcessInstanceByKey("procurementRequest", "PO-TEST-1",
        Map.of("amount", 10000));
    var task = taskService.createTaskQuery()
        .processInstanceId(instance.getId())
        .singleResult();
    assertThat(task).isNotNull();
    assertThat(task.getTaskDefinitionKey()).isEqualTo("managerApprove");
}
```

**curl（REST 路线预览，第 11 章展开）**

```bash
# 仅为示意：具体路径与安全策略以你们启用的 flowable-rest 为准
curl -s -u demo:demo \
  -H "Content-Type: application/json" \
  -d '{"processDefinitionKey":"procurementRequest","businessKey":"PO-2026-0001"}' \
  "http://localhost:8080/flowable-rest/service/runtime/process-instances"
```

---

## 4）项目总结

### 优点

- **关注点分离**：业务数据在业务服务，**流程控制流**在 BPMN，减少巨型 switch。
- **可审计**：实例与任务历史为合规提供原始材料（配合历史级别配置）。
- **可演进**：改流程定义可走后门迁移策略（见第 26 章），而非处处改代码。

### 缺点

- **学习曲线**：团队需建立 BPMN 与引擎 API 的共同语言。
- **存储与运维成本**：多表、作业线程、升级脚本需纳入生命周期。
- **滥用风险**：不该编排的长事务被画成庞大流程，导致难以测试与排障。

### 典型使用场景

- 采购、合同、人事、IT 服务台等多级审批与例外升级。
- 需要**谁何时办了什么**留痕的场景。

### 注意事项

- 尽早约定 **businessKey** 与业务主键映射规范。
- 流程变更与发版策略要写清（尤其线上多版本实例并存）。

### 反例（本章）

在一张 `purchase_order` 表上用 `status` + 多个时间戳字段模拟多级审批，看似开发快；三个月后业务加「并行会签」和「超时收回」，技术方案被迫推倒重来。**纠正**：从第一个可被接受的复杂度起就引入可执行 BPMN，并控制模型规模。

### 常见踩坑

| 现象 | 根因 | 处理 |
|------|------|------|
| 「流程没走下去」但无报错 | 网关缺省流、条件表达返回 null | 检查 BPMN 条件与变量类型，为网关提供默认分支 |
| 待办查不到 | 任务实际在并行分支，query 条件过窄 | 用 `processInstanceId` 或 `candidateUser` 正确查询 |
| 历史对不上 | 历史级别过低或未持久化 | 配置 `history` 级别并理解 `full`/`audit` 差异（见第 10 章） |
| 业务键重复 | 同一业务键重复起实例 | 业务层幂等：先查是否已有运行中实例 |

### 给测试的一句话

用「**流程实例存在 + 当前活动节点符合预期 + 关键变量快照正确**」分层断言，不要只截屏待办。

### 给运维的一句话

本章无独立运维动作；后续请把 **`processInstanceId`/`businessKey`** 与业务日志 **traceId** 在同一条日志里打通，排障会省一半时间。

---

*（正文约三千字量级；若需对外发布可按模板补充图示与你们环境截图。）*
