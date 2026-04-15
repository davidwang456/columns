# 第 4 章：事件与顺序流——Start／End、SequenceFlow、默认流

> 版本说明：与 [VERSION_MATRIX.md](../VERSION_MATRIX.md) 矩阵 A 对齐；BPMN 2.0 语义。

---

## 1）项目背景

星云科技的采购申请流程里，业务方最初只关心「谁批、批完就结束了」。但平台组要求：**每个可执行流程都必须有清晰入口与出口**，且所有活动节点在图上**可达**，否则上线后会出现「流程实例已创建却永远不前移」的假死。采购申请在提交后，先要经过一次**同步校验**（本章先用 `serviceTask` 占位，第 6 章展开），再进入经理审批。

**本章聚焦的技术对象**是：**开始/结束事件**、**顺序流（SequenceFlow）**、以及为后续排他网关做铺垫的**默认流（default）** 概念。读者将理解：BPMN 图不只是美术作品，而是被引擎解析为**可执行的有向图**；顺序流的箭头方向决定令牌的传递。

---

## 2）项目设计（小胖 × 小白 × 大师）

> **角色分工**：**小胖**（爱吃爱玩、不求甚解）用生活化、口语化的方式把话题「开球」，先把问题问出来；**小白**（喜静、喜深入）负责追问原理、边界、风险与可比方案；**大师**（资深技术 Leader）把业务约束与技术选型说透，善打比方、由浅入深。

**小胖**：开始事件和结束事件不就是个圆吗？有啥好讲的？

**大师**：圆也分种类。**空圈圈**一般是开始/结束；带图标的还有**消息开始**、**定时开始**、**错误结束**等。你若选错类型，引擎部署能通过，但**触发方式**完全不同——比如消息开始要能 `correlate`，定时开始要等 Job。

**小白**：我们就普通点击提交，选「无」开始行吗？

**大师**：最常见就是 **None Start Event**。业务入口在应用里调用 `startProcessInstanceByKey`。

**小胖**：结束事件可以有很多个吗？

**大师**：可以。多个结束代表**不同业务结果**（通过/驳回/撤回），利于读图与报表口径拆分。别把所有出口硬捏到一个结束节点，除非你真的只有同一种终态。

**小白**：顺序流就是箭头？能交叉吗？

**大师**：视觉可以交叉，但**语义**上是「从 sourceRef 到 targetRef」的有向边。模型要满足：**从 Start 能走到至少一个 End**；常见坑是忘了连某条出路，或网关少画一条 outgoing。

**小胖**：条件写在线上还是写在网关上？

**大师**：**排他网关**上常集中放条件；顺序流也可以带 `conditionExpression`（第 8 章细讲）。现在先记住：**默认流**用于「其它条件都不满足时走哪条」，减少 NPE 与悬空。

**小白**：默认流是必须的吗？

**大师**：在**排他网关**语境里，强烈建议有默认，否则全部条件为 false 时引擎可能抛异常或行为依赖实现细节。包容/并行另说。

**小胖**：什么叫「不可达节点」？

**大师**：画在图里但**从 Start 走不到**的活动。部署阶段有的工具会警告；Flowable 未必全部拦截——但审计与代码生成会很难看。

**小白**：Terminate 结束和普通结束区别？

**大师**：**Terminate End Event** 会**终止整个流程实例**（含并行分支）。普通结束只结束当前分支路径。乱用会「一键掐断」还在跑的并行审批——慎用。

**小胖**：我们流程里要接子流程吗？

**大师**：本章不讲子流程边界；只要保证**主流程的 Start→…→End** 闭环。子流程第 14 章讲。

---

### 一页纸决策表

| 输入 | 输出 | 适用场景 | 不适用/慎用 | 与上下游章节关系 |
|------|------|----------|-------------|----------------|
| 业务触发与终态集合 | 合法的 BPMN 拓扑 + 顺序流 | 标准审批、分支结果清晰的多终态 | 极复杂状态需 CMMN 或编排引擎另案 | 第 5 章用户任务；第 8 章排他网关 |

---

## 3）项目实战（主代码片段）

### 3.1 BPMN 片段（最小闭环）

下列流程：`start` → `validateRequest`（服务任务占位）→ `endSuccess`。

```xml
<?xml version="1.0" encoding="UTF-8"?>
<definitions xmlns="http://www.omg.org/spec/BPMN/20100524/MODEL"
             xmlns:flowable="http://flowable.org/bpmn"
             targetNamespace="http://neuratech.column/ch04">
  <process id="procurementEventFlow" name="采购-事件与顺序流演练" isExecutable="true">

    <startEvent id="start" name="申请提交"/>

    <serviceTask id="validateRequest" name="校验申请"
                 flowable:delegateExpression="${noopDelegate}"/>

    <endEvent id="endSuccess" name="校验通过待审批"/>

    <sequenceFlow id="s1" sourceRef="start" targetRef="validateRequest"/>
    <sequenceFlow id="s2" sourceRef="validateRequest" targetRef="endSuccess"/>

  </process>
</definitions>
```

> `noopDelegate` 需在 Spring 中注册为合法委托（第 6 章也可用 `class` 指 JavaDelegate）。此处强调：**顺序流 id 唯一、source/target 引用存在**。

### 3.2 Java：启动并断言流程已到结束边界

```java
@Autowired
private RuntimeService runtimeService;
@Autowired
private HistoryService historyService;

@Test
void sequenceFlowShouldReachEndAfterServiceTask() {
    String key = "procurementEventFlow";
    ProcessInstance pi = runtimeService.startProcessInstanceByKey(key, "BK-CH04-001");

    HistoricProcessInstance historic =
        historyService.createHistoricProcessInstanceQuery()
            .processInstanceId(pi.getId())
            .singleResult();

    assertThat(historic.getEndTime()).isNotNull();
}
```

### 3.3 curl（若启用 flowable-rest）

```bash
curl -s -u demo:demo -X POST \
  "http://localhost:8080/flowable-rest/service/runtime/process-instances" \
  -H "Content-Type: application/json" \
  -d '{"processDefinitionKey":"procurementEventFlow","businessKey":"BK-CH04-001"}'
```

---

## 4）项目总结

### 优点

- 顺序流显式表达**控制流**，比代码里隐式跳转更利于评审与合规留痕。  
- 多结束事件让**业务成果语义**在图上可分。  
- 提前理解默认流与终止结束，为网关与并行打下基础。

### 缺点

- 大图上箭头过多时，可读性下降，需要子流程或模块化（第 14 章）。  
- 仅靠 BPMN 拓扑无法表达数据合法性，仍需服务任务与用户任务校验。

### 典型使用场景

- 任意线性或即将分叉的采购、合同、售后流程建模基线。

### 注意事项

- 部署前用 **Modeler「部署前校验」** 与团队评审检查可达性。  
- 结束事件类型与实际业务是否允许「硬终止」一致。

### 反例

团队在图上画了「校验失败」支路却**未连到任何结束或处理节点**，指望在 Java 里 `throw` 完事——结果引擎记录与业务口径不一致，报表无法解释「失败去哪了」。**纠正**：失败路径也应在图上有**显式终态或处理活动**（或错误边界，见第 19 章）。

### 常见踩坑

| 现象 | 根因 | 处理 |
|------|------|------|
| 部署报错 sourceRef 无效 | 复制粘贴导致 id 不一致 | 全局搜索 id，保证引用存在 |
| 实例已完成但业务以为还在跑 | 误解结束事件含义 / 异步未考虑 | 核对历史表；引入异步时在 17 章建模 |
| 并行网关前后顺序流连错 | fork/join 不配对 | 第 9 章专门练习；本章避免半吊子并行 |
| 终止结束误杀并行任务 | 选错 end 类型 | 改成普通 end 或调整模型 |

### 给测试的一句话

断言 **`HistoricProcessInstance.endTime` 非空** 前，确认历史级别与定义 key；并行场景勿只用单线思维验状态。

### 给运维的一句话

本章通常无独立作业负载；若使用**消息/定时开始**，时钟与订阅（第 15～16 章）才会上运维清单。
