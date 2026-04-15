# 第 8 章：网关入门——ExclusiveGateway（排他）

> 版本说明：条件表达式默认 **UEL**；默认流强烈建议配置。

---

## 1）项目背景

星云采购规则：金额 **&lt; 10 万**仅需部门经理审批；**≥ 10 万**需加财务会签。业务方用一句话描述，研发要在 BPMN 里落成 **互斥分支**。**本章聚焦** `exclusiveGateway`：**条件顺序流**、**默认流（defaultFlow）**，以及 `approveAmount`、`approved` 等变量在条件里的**类型安全**。

---

## 2）项目设计（小胖 × 小白 × 大师）

> **角色分工**：**小胖**（爱吃爱玩、不求甚解）用生活化、口语化的方式把话题「开球」，先把问题问出来；**小白**（喜静、喜深入）负责追问原理、边界、风险与可比方案；**大师**（资深技术 Leader）把业务约束与技术选型说透，善打比方、由浅入深。

**小胖**：if-else 写代码里不行吗？

**大师**：行，但那变成**黑盒规则**；图上没有「为什么走财务」的证据。排他网关让合规与审计看得懂。

**小白**：排他怎么「排」？

**大师**：引擎按**出序顺序**评估条件，**首个为 true** 即走该路（实现细节以版本文档为准）；所以**条件互斥**很重要，别两条都 true。

**小胖**：都不 true 呢？

**大师**：若配置了 **默认流**，走默认；否则常见**异常**。线上事故多来自这里。

**小白**：能和 Spring EL 混用吗？

**大师**：UEL 可调用注册 Bean（视配置），但别把巨石逻辑塞进表达式—— Delegate 更适合。

**小胖**：字符串比较注意啥？

**大师**：`==` 与 `.equals` 在 EL 里行为要留心；统一用大写枚举或常量。

**小白**：默认流箭头画在哪？

**大师**：在 **gateway** 上设 `default` 属性指向某条 outgoing 的 id。

**小胖**：驳回怎么走？

**大师**：可用变量 `approved=false` 连到**重新填写**或**结束**（见模型）；本质是另一条条件分支。

---

### 一页纸决策表

| 输入 | 输出 | 适用场景 | 不适用/慎用 | 与上下游章节关系 |
|------|------|----------|-------------|----------------|
| 金额等决策变量 | 单一路由分支 | 金额/等级/地区路由 | 需多路同时执行 | 第 9 章并行 |

---

## 2.1）场景深化：互斥条件与产品语言的鸿沟

业务方常口头说「大于等于十万走财务」，研发写成 `${amount >= 100000}`，却忘了采购单在草稿态 `amount` 可能尚未回填，结果为 `null`，条件整体判为 false，订单默默流向「经理独占」支路，造成后续 **预算失控**。大师在星云落地中的建议是：在网关前增加**显式预处理**——或在 `start` 后首个服务任务把 `amount` 规范化为 `long` 并设 **默认值策略**；排他网关只消费「已经过校验」的事实。若同一网关混用「客户等级」「地区」「币种」等多维条件，评审时要用 **真值表**（truth table）覆盖，而不是只看一条 happy path。

---

## 3）项目实战（主代码片段）

### 3.1 BPMN 核心

```xml
<exclusiveGateway id="gwAmount" name="金额判断" default="toManagerOnly"/>

<sequenceFlow id="toManagerOnly" sourceRef="gwAmount" targetRef="taskManager">
  <conditionExpression xsi:type="tFormalExpression">
    ${amount &lt; 100000}
  </conditionExpression>
</sequenceFlow>

<sequenceFlow id="toFinance" sourceRef="gwAmount" targetRef="taskFinance">
  <conditionExpression xsi:type="tFormalExpression">
    ${amount &gt;= 100000}
  </conditionExpression>
</sequenceFlow>
```

> `xsi` 命名空间需在 `definitions` 声明 `xmlns:xsi`；上面为示意。

### 3.2 启动与驱动

```java
ProcessInstance pi = runtimeService.startProcessInstanceByKey(
    "procurementBranch", "BK-CH08", Map.of("amount", 150000L));
```

### 3.3 JUnit

```java
Task task = taskService.createTaskQuery().processInstanceId(pi.getId()).singleResult();
assertThat(task.getTaskDefinitionKey()).isIn("taskFinance", "taskManager");
```

### 3.4 curl

启动同前；查询 task：

```bash
curl -s -u demo:demo \
  "http://localhost:8080/flowable-rest/service/runtime/tasks?processInstanceId={id}"
```

---

## 4）项目总结

### 优点

- 规则显式、易评审。  
- 配合默认流可降低运行期异常。

### 缺点

- 条件复杂时图难读——可下沉 Delegate 产生 **routing 变量**。  
- 顺序与互斥需团队纪律。

### 典型使用场景

- 金额路由、地区、客户等级、风险评级分流。

### 注意事项

- **变量缺省值**与 null。  
- 部署新版本时**条件语义变更**需迁移策略。

### 反例

两条条件**重叠**（都 true），依赖引擎「选第一条」——升级后顺序若变则事故。**纠正**：条件**互斥**或改为 **包容网关** 意图明确。

### 常见踩坑

| 现象 | 根因 | 处理 |
|------|------|------|
| 抛异常无出向 | 无默认且全 false | 加默认或覆盖所有情况 |
| 金额比对失败 | Long vs Integer | 统一类型 |
| 升级后行为变 | 出序依赖 | 显式优先级或独立变量 |
| EL 调 Bean 失败 | Bean 名错误或 null | 集成测试覆盖 |

### 给测试一句话

矩阵用例：**临界值 99999/100000/100001** + **变量缺失**。

### 给运维一句话

无特殊作业；关注 **因异常未被捕获** 导致的流程失败日志。

---

## 附：延展阅读——默认流与「未覆盖分支」治理

建议在团队模板库中为排他网关附带 **Checklist**：是否配置默认流、是否列出全部枚举、是否对 null 有显式分支说明。星云在季度内审时会把「条件总数」与「业务规则台账」做交叉比对，防止 **影子规则** 只活在某个开发笔记本里。对外部监管答疑时，这两页材料往往比截图 Modeler 更能说明**可控性**。
