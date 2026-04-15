# 第 3 章：第一条流程——从 Modeler 到部署运行（含版本）

> 本章对象：`RepositoryService`、Deployment、ProcessDefinition、流程定义 **key/version**、与运行时启动的关系。

---

## 1）项目背景

采购 MVP 定了：先跑通「提交申请 → 经理审批 → 结束」。团队拿着 Modeler 导出的 BPMN 文件开会：「文件放哪？部署是一次性的吗？以后改流程会覆盖吗？」——需要讲清 **部署**与**定义版本**、**运行中实例**三者关系。

本章沿用主线「采购申请」，完成：**资源入库（部署）→ 按 key 启动实例 → 看到第一个用户任务**。

---

## 2）项目设计（小胖 × 小白 × 大师）

> **角色分工**：**小胖**（爱吃爱玩、不求甚解）用生活化、口语化的方式把话题「开球」，先把问题问出来；**小白**（喜静、喜深入）负责追问原理、边界、风险与可比方案；**大师**（资深技术 Leader）把业务约束与技术选型说透，善打比方、由浅入深。

**小胖**：那我是不是把 `VacationRequest.bpmn20.xml` 丢进 `resources` 里就行？像零食扔进背包，背起来就能跑？

**大师**：常见做法两种：**classpath 自动部署**（目录如 `processes/`）或 **API 部署**（上传 ZIP/字节流）。自动部署适合 CI；手工热更新可走 API。要区分 **deploy** 与 **start**。

**小白**：一次部署会更新正在跑的流程吗？

**大师**：**不会自动掐断**。已有实例绑定到**当时**的流程定义版本；新启动的实例默认可走「最新版本」（API 选择决定）。旧实例仍按旧定义推进，除非你上**迁移工具**（第 26 章）。

**小胖**：`key` 和 `id` 有啥区别？

**大师**：`processDefinitionKey` 是你在模型里定死的业务键；`processDefinitionId` 是引擎内部 **key:version:deploymentId** 形态的唯一标识。**启动多用 key**，排障截图多用 id。

**小白**：Deployment 里能放别的吗？

**大师**：能。表单、图片、脚本……一次性打成一个 deployment 包，**原子可见**。

**小胖**：版本号谁涨？

**大师**：同一 key 再部署，**version +1**。别手改数据库。

**小白**：我们删了旧部署会怎样？

**大师**：危险话题。删部署有级联语义与数据完整性约束，生产禁止当作「清垃圾」随便删。用流程治理策略取代。

**小胖**：`resources/processes` 下好几个 BPMN，会打成一个大 deployment 吗？

**大师**：取决于你**一次** `createDeployment().addClasspathResource(...)` 加了几个，以及 Spring Boot 的 **自动部署策略**。要控制粒度，就用 API 显式命名与分组；否则审计时很难解释「这一包到底是谁发的」。

**小白**：流程图里能不能写中文 id？

**大师**：**事件/活动的 id** 建议英文或与代码常量一致，中文放在 `name` 上当「门牌别名」——否则 EL、日志、脚本里容易大小写打架，像微信名和身份证不是一回事。

**小胖**：同 key 连发两版，旧实例变量结构和新图不一致呢？

**大师**：这是**迁移**问题，不是 deploy 一锤子。研发要定义**兼容窗口**：要么禁止改字段语义，要么写迁移（见第 26 章），要么只允许新建流程 key。

---

### 一页纸决策表

| 输入 | 输出 | 适用场景 | 不适用/慎用 | 与后续章节关系 |
|------|------|----------|-------------|----------------|
| BPMN 资源与可选 DMN/表单 | Deployment；生成若干 ProcessDefinition | 上线新流程、修订模型 | 大文件热更无回滚策略 | 第 5～8 章补网关与任务；第 26 章迁移 |

---

## 3）项目实战（主代码片段）

### 3.1 最小 BPMN（`src/main/resources/processes/procurement-request.bpmn20.xml`）

下列为**教学简化版**：单用户任务 `managerApprove`，流程 key `procurementRequest`。

```xml
<?xml version="1.0" encoding="UTF-8"?>
<definitions xmlns="http://www.omg.org/spec/BPMN/20100524/MODEL"
             xmlns:flowable="http://flowable.org/bpmn"
             targetNamespace="http://neuratech.column.procurement">

  <process id="procurementRequest" name="采购申请" isExecutable="true">

    <startEvent id="start" name="开始"/>

    <userTask id="managerApprove" name="经理审批" flowable:candidateGroups="managers"/>

    <endEvent id="end" name="结束"/>

    <sequenceFlow id="f1" sourceRef="start" targetRef="managerApprove"/>
    <sequenceFlow id="f2" sourceRef="managerApprove" targetRef="end"/>

  </process>
</definitions>
```

> 实际项目请用 Flowable Modeler 校验；本段突出 **process id** 即 **definition key** 的常规约定。

### 3.2 classpath 自动部署（无额外代码）

第 2 章工程已具备时，启动应用即部署 `processes/` 下资源。可在测试中验证定义是否存在：

```java
@Autowired
private RepositoryService repositoryService;

@Test
void definitionShouldBeDeployed() {
    var def = repositoryService.createProcessDefinitionQuery()
        .processDefinitionKey("procurementRequest")
        .latestVersion()
        .singleResult();
    assertThat(def).isNotNull();
    assertThat(def.getVersion()).isGreaterThanOrEqualTo(1);
}
```

### 3.3 启动流程实例（主路径）

```java
@Autowired
private RuntimeService runtimeService;
@Autowired
private TaskService taskService;

public String startSampleRequest() {
    var instance = runtimeService.startProcessInstanceByKey(
        "procurementRequest",
        "PO-2026-0001",
        Map.of("amount", 38000)
    );
    var task = taskService.createTaskQuery()
        .processInstanceId(instance.getId())
        .singleResult();
    return task.getId();
}
```

### 3.4 可选：编程式部署（CI 或动态更新）

```java
@Autowired
private RepositoryService repositoryService;

public void deployFromClasspath(String classpathResource) {
    repositoryService.createDeployment()
        .name("procurement-column-ch03")
        .addClasspathResource(classpathResource)
        .deploy();
}
```

### 3.5 curl（REST 路线）

若 REST 可用，列出流程定义（路径以实际模块为准）：

```bash
curl -s -u demo:demo \
  "http://localhost:8080/flowable-rest/service/repository/process-definitions?latest=true&key=procurementRequest"
```

---

## 4）项目总结

### 优点

- **部署单元清晰**：一次 Deployment 可追踪审计「谁发布了什么」。
- **版本并存**：新旧定义可共存，降低大版本 bang 切换风险。
- **Classpath 部署**对开发者友好，与 Git 版本一致。

### 缺点

- 定义多了之后，**治理**（命名、owner、废弃策略）必须跟上。
- 自动部署若未控制目录，容易把**实验 BPMN**带上生产。

### 典型使用场景

- 首次上线、灰度发布、按业务线拆分多个 deployment 名称。

### 注意事项

- **deployment 名称**要有意义（发版号/工单号），别全是 `deployment`。
- 与业务发版流水线对齐（见第 34 章）。

### 反例

工程师本地改了 BPMN，忘提交仓库，只在个人环境 classpath 里放着——预发生产死活不一致。**纠正**：**BPMN 即代码**，进同一 Git，走同一 PR。

### 常见踩坑

| 现象 | 根因 | 处理 |
|------|------|------|
| 查不到最新定义 | 缓存/未 `latestVersion()` | 查询 API 指定 latest 或明确 version |
| 启动走旧版 | `startProcessInstanceByKey` 默认最新，但存在租户/部署过滤 | 查过滤条件与租户字段 |
| 同名 key 冲突 | 不同业务复制粘贴 id | 公司级命名空间前缀 |
| 部署成功任务不现 | 模型未 executable、或路径未扫描 | 检查 `isExecutable` 与 `processes` 目录 |

### 给测试的一句话

断言 **`processDefinitionKey` + `deploymentId` + version** 三者的组合是否符合发布清单，不要只点「流程能跑」。

### 给运维一句话

保留 **deployment 记录与制品包**（或 Git tag），回滚时才知道该回哪一版 BPMN。

---

### 附：与数据库的两张「定义表」心智图（扩写可配表结构截图）

- **`ACT_RE_DEPLOYMENT`**：一次**发布动作**（who/when/名称）。  
- **`ACT_RE_PROCDEF`**：从中展开的具体 **process definition**，含 **KEY**、**VERSION**、**RESOURCE_NAME** 等。

新人常混：**deployment 多对一**指向多个 procdef（一次包里多个流程），或一个 BPMN 多个 process（少见但可能）。排障时先看 deploymentId 再追到 resource。

---

*（配图建议：Modeler 导出示意图 + `ACT_RE_DEPLOYMENT` / `ACT_RE_PROCDEF` 关键列说明。成稿字数以 3000～5000 字为目标继续补充场景与踩坑案例。）*
