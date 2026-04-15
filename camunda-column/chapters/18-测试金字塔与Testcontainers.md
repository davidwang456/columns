# 第 18 章：测试金字塔：单元、集成、端到端（含 Testcontainers 思路）

## 元信息

| 项目 | 内容 |
|------|------|
| 章节编号 | 第 18 章 |
| 标题 | 测试金字塔：单元、集成、端到端（含 Testcontainers 思路） |
| 难度 | 进阶 |
| 预计阅读 | 35～40 分钟 |
| 受众侧重 | 测试 + 开发（CI 维护者） |
| 依赖章节 | 第 12 章端到端概念；第 13 章 REST 基础 |
| 环境版本 | JUnit 5；可选用 Testcontainers；数据库以 `VERSIONS.md` 为准 |

---

## 1. 项目背景

流程系统最怕两件事：**改一个网关条件，线上实例行为变了**；**升级引擎小版本后，历史数据或作业执行异常**。纯界面点点无法进 CI，而全链路 UI 测试又慢又脆。本章主线问题是：**如何用「测试金字塔」给 Camunda 项目建立可重复、可进流水线的质量护栏**，并在需要时引入 **Testcontainers** 提供真实数据库行为，而不是「只在 H2 上绿、上 PostgreSQL 就炸」。

---

## 2. 项目设计（三角色对话）

### 2.1 小胖开球

小胖挠头：「我们不就是测接口吗？Postman 一跑，200 就行。流程引擎还要**专门测**？它又不是我自己写的代码。」

这句话暴露了常见误区：引擎虽是第三方，但**你的模型、表达式、监听器、外部任务客户端**都是交付物；任何一层错了，表现都是「流程怪怪的」。测试要分层对准**责任边界**。

### 2.2 小白追问

小白追问：

「第一，**@Deployment** 这类工具在测什么？和 Spring 集成测试会不会重复？

第二，Delegate 里如果调了外部 HTTP，是用 WireMock 还是在集成测试里打真实服务？

第三，Testcontainers 拉 PostgreSQL，CI 变慢多少？有没有**最小集**策略，比如只在 nightly 跑？」

### 2.3 大师定调

大师归纳：**单元测试**验证纯 Java 规则与工具函数；**流程单元/组件测试**（Camunda 提供的 JUnit 规则或流程引擎测试框架）验证 **BPMN 部署、路径可达性、变量传递**；**集成测试**验证 **Spring 上下文、事务边界、数据源**；**端到端**验证 **REST → 引擎 → 数据库** 全链路，数量应少而关键。

对外部 HTTP：**默认单元/轻集成用替身**（Mock/Stub），契约稳定后再考虑有限真实调用。Testcontainers 的价值是**消除「H2 与生产库语义差」**这类风险，而不是每个用例都启容器——可采用「代表用例 + 分层流水线」：PR 上跑快测，合并后跑容器化集成。

---

## 3. 项目实战

### 3.1 环境前提

- Maven/Gradle 工程，已引入 `camunda-bpm-junit5` 或项目惯用测试栈。
- 规划示例路径：`examples/camunda-column-examples/part2-intermediate/ch18-testing/`（代码随专栏进度填充）。

### 3.2 步骤说明：搭三层

1. **流程断言骨架**：选一个第 12 章的 BPMN，在测试中 `repositoryService.createDeployment().addClasspathResource(...).deploy()`，用 `RuntimeService` 启动实例，断言当前活动 id 与任务候选是否符合预期。
2. **变量与路径**：构造多组变量，覆盖网关两侧；对「拒绝」路径断言流程结束或进入特定事件（依模型）。
3. **Spring 集成测试**：`@SpringBootTest` 启动最小应用上下文，开启 Camunda 自动配置，验证 REST 或 Service Bean 调用后数据库中流程实例状态（注意事务与测试隔离）。
4. **Testcontainers（可选）**：在集成测试 profile 中启用 PostgreSQL 容器，数据源指向容器 JDBC URL；同套用例在 H2 与 PG 各跑一遍，观察差异（例如锁、索引、类型）。

### 3.3 源码与说明

伪代码结构（示意，非完整可运行）：

```java
// 1) 流程组件测试：部署 + 启动 + 查询任务
@Test
void should_route_to_hr_when_days_large() {
  // deployment, start with variables days=5
  // assert single task definition key == "hrRegister" 或等价断言
}
```

**为什么先写组件测试**：它比 E2E 快，能精确定位「模型表达式写错」还是「业务服务错了」。

```java
// 2) SpringBootTest：验证 @Transactional 边界（示意）
@SpringBootTest
class LeaveProcessIT {
  // autowired RuntimeService, TaskService
}
```

**为什么 Spring 层要测事务**：业务服务与引擎同事务时，一旦配置不当，会出现「业务写库回滚但流程已前进」等一致性问题（第 23 章深入）。

**Testcontainers 要点**：固定镜像版本；复用容器（若测试框架支持）减少启动时间；失败时输出容器日志。

### 3.4 验证

- 本地执行 `mvn test`：核心用例应在分钟级内完成。
- CI：拆分 `test` 与 `integration-test`（或 surefire/failsafe 分阶段）；容器化测试可设超时与重试策略。
- 测试报告应让**非开发**也能读懂：至少有一份**路径覆盖表**（业务场景 × 是否自动化）。

---

## 4. 项目总结

| 维度 | 内容 |
|------|------|
| 优点 | 分层测试让缺陷定位更快；Testcontainers 提升与生产数据库一致性。 |
| 缺点 / 代价 | 集成与 E2E 增加维护成本；容器测试消耗 CI 资源与时间。 |
| 适用场景 | 长期演进的流程项目；多环境部署；有回归压力的业务。 |
| 不适用场景 | 一次性脚本型流程且不再变更——可只做最小冒烟。 |
| 注意事项 | 测试数据与流程版本绑定；清理历史避免用例互相污染。 |
| 常见踩坑 | 在 H2 上依赖 MySQL 专有 SQL；忘记断言**异步作业**完成导致偶发绿；时间与定时器测试未冻结时钟。 |

**延伸阅读**：Camunda 官方 Testing 文档；Failsafe 插件与 CI 分阶段。下一类关联章节为第 19 章（版本升级对测试基线的影响）与第 24 章（典型坑合集）。
