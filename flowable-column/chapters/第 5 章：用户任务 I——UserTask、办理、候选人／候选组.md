# 第 5 章：用户任务 I——UserTask、办理、候选人／候选组

> 版本说明：矩阵 A；身份数据可用引擎自带 Idm 或自定义同步（第 22 章深入）。

---

## 1）项目背景

星云采购申请在进入「经理审批」后，需要把待办放进**共享任务池**或派给**具体人**。业务口径是：金额小于 10 万的单子由「部门经理组」任选一人处理；审计要求能回答「谁在什么时刻从任务池认领了待办」。**本章聚焦** `UserTask` 的 **assignee**、**candidateUsers**、**candidateGroups**，以及 `TaskService` 上**查询、认领（claim）、完成（complete）** 的主路径。

---

## 2）项目设计（小胖 × 小白 × 大师）

> **角色分工**：**小胖**（爱吃爱玩、不求甚解）用生活化、口语化的方式把话题「开球」，先把问题问出来；**小白**（喜静、喜深入）负责追问原理、边界、风险与可比方案；**大师**（资深技术 Leader）把业务约束与技术选型说透，善打比方、由浅入深。

**小胖**：直接写死 `assignee="zhangsan"` 不就行？

**大师**：演示可以，生产常变。组任务、轮值、组织架构调整后，硬编码(assignee) 要频繁改 BPMN。多数企业用 **candidateGroups** 对齐 LDAP/HR 的组。

**小白**：候选人和候选组同时设会怎样？

**大师**：语义是「满足其一即可见」。实际建模**建议二选一为主**，避免读者误解「又要组又要人」是 AND。

**小胖**：待办列表 SQL 我们自己写吗？

**大师**：用引擎 **`TaskService` 查询 API**，别直接 `SELECT * FROM ACT_RU_TASK` 当长期方案——除非你清楚租户、权限、软删除策略都与之一致。

**小白**：认领（claim）是必须步骤吗？

**大师**：当任务只有候选人而没有 assignee 时，通常用户要 **claim** 成自己，其它人不可再办（除非你释放/委派流程，后续章）。若启动时就指定 assignee，则无需认领。

**小胖**：经理组在 Idm 里叫什么？

**大师**：组名要与 BPMN 里 `flowable:candidateGroups` **字符串完全一致**，大小写敏感要看数据库与配置——这是踩坑重灾区。

**小白**：候选人很多时性能怎么办？

**大师**：任务表有索引；更关键是**分页查询**与合理的 candidate 规模。大批量用组比列举一万个用户名靠谱。

**小胖**：前端怎么查「我的待办」？

**大师**：`taskCandidateUser(userId)`、`taskCandidateGroupIn(groups)`、`taskAssignee`，或组合；鉴权在网关/服务层做。

**小白**：办理时需要带变量吗？

**大师**：`complete(taskId, variables)` 可把**审批结论、意见**写回流程变量，供网关条件用（第 8 章）。

**小胖**：任务标题能显示业务单号吗？

**大师**：用 `name`/`documentation` 或在创建监听里动态设名称；与业务主键展示建议走**业务侧列表**，引擎 task name 辅助。

---

### 一页纸决策表

| 输入 | 输出 | 适用场景 | 不适用/慎用 | 与上下游章节关系 |
|------|------|----------|-------------|----------------|
| 组织架构、候选池规则 | 用户任务 + 查询/认领/完成 API | 部门池审批、公共队列 | 极低延迟自动决策无需人 | 第 7 章变量；第 8 章条件分支 |

---

## 3）项目实战（主代码片段）

### 3.1 BPMN 片段

```xml
<userTask id="managerApprove" name="经理审批"
          flowable:candidateGroups="deptManagers"/>
```

流程 key 沿用 `procurementRequest`，部署略。

### 3.2 测试数据：创建组与用户（示意，Idm API）

```java
identityService.saveGroup(
    identityService.newGroup("deptManagers"));
identityService.saveUser(
    identityService.newUser("manager.wang"));
identityService.createMembership("manager.wang", "deptManagers");

ProcessInstance pi = runtimeService.startProcessInstanceByKey(
    "procurementRequest", "BK-CH05-001", Map.of("amount", 9000));

Task task = taskService.createTaskQuery()
    .taskCandidateUser("manager.wang")
    .singleResult();

taskService.claim(task.getId(), "manager.wang");
taskService.complete(task.getId(), Map.of("approved", true));
```

### 3.3 curl（REST）

```bash
curl -s -u demo:demo \
  "http://localhost:8080/flowable-rest/service/runtime/tasks?candidateUser=manager.wang"
```

---

## 4）项目总结

### 优点

- 候选组模型与组织权限天然对齐。  
- `TaskService` 查询抽象了多租户、排序、分页等共性需求。  
- 认领机制支持**抢办**与责任到人。

### 缺点

- 组名不一致会导致「全公司看不到待办」。  
- 高并发抢办要考虑**乐观锁/重试**（任务已被他人认领）。

### 典型使用场景

- 部门经理池、财务双岗、值班长队列。

### 注意事项

- 生产环境 **Idm 与业务目录同步**要有失败告警与对账。  
- 审计日志记录 **assignee 变更**（认领/委派）满足合规。

### 反例

BPMN 写 `candidateGroups="managers"`，LDAP 同步下来的组叫 `DEPT_MANAGER`——待办永远为空。**纠正**：建立**映射表**或统一命名规范，并在 CI 中校验 BPMN 常量与目录。

### 常见踩坑

| 现象 | 根因 | 处理 |
|------|------|------|
| 候选用户查不到任务 | 未 membership 或租户过滤 | 查 Idm；核对 tenantId |
| claim 报乐观锁 | 已被他人认领 | 前端刷新；提示重取列表 |
| 完成时变量丢失 | 只用了 `complete(id)` 未传 Map | 传审批结果变量 |
| REST 暴露过度 | 未鉴权 candidate 查询 | 网关鉴权、数据范围 |

### 给测试的一句话

用固定测试用户 + **已知 membership** 造数据；断言 **`taskAssignee` 在 claim 前后变化**。

### 给运维一句话

关注 **`ACT_RU_TASK` 积压** 与任务完成速率；非引擎问题常常是**组织数据未同步**。
