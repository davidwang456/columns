# 第 38 章：Agentic、实验特性与源码贡献（合并章）

## 1. 项目背景

### 业务场景（拟真）

**`langchain4j-agentic*`**、`experimental/langchain4j-experimental-sql` 等代表 **前沿/尝试性**能力：**API 可能变动**、**行为未完全冻结**。高级开发者应能从 **`AiServices` public API** 逆读 **`AiServicesFactory`、`ServiceHelper`（SPI）**，区分 **稳定契约** 与 **实现细节**。开源协作遵循 **`CONTRIBUTING.md`**。

### 痛点放大

**实验模块进生产** 无 **版本锁、降级** → **静默行为改变**。**大 PR** 难合并；**无测试** 难获信任。本教材 **第 38 章收尾**：**技术展望 + 社区参与**。

## 2. 项目设计：小胖、小白与大师的对话

**小胖**：实验模块能服务生产吗？

**小白**：**Agentic** 和普通 **Tools** 差啥？**读源码** 从哪开始？

**大师**：**仅当** 锁版本、降级、接受 API 变更——多数 **隔离独立服务**。**Agentic** 偏 **多步规划/自主循环**——**预算与人工闸**。**自顶向下**：`AiServices` → `DefaultAiServices` → HTTP/Tools；**Call Hierarchy**。**技术映射**：**贡献从文档/测试/最小复现开始**。

**小胖**：本地咋编译？

**小白**：**Discord/GitHub** 咋提问？

**大师**：`mvn -pl ... -am` **只编相关模块**；全量 **耗时且易抖动**。**可复现步骤 + 版本 + 最小代码**；**期望/实际** 冷静描述。**技术映射**：**SNAPSHOT 与 fork 同步策略**。

---

## 3. 项目实战

### 环境准备

- 上游仓库；IDE **Navigate → Class** [`AiServices.java`](../../langchain4j/langchain4j/src/main/java/dev/langchain4j/service/AiServices.java)。

### 分步任务

1. 记录 **`create`/`builder`** 等对外入口。  
2. 读 **`ServiceHelper` / `loadFactories`**（或当前 SPI）——谁 **classpath** 注入 `ChatModel`？  
3. Issues 过滤 **`good first issue`**，写一条理解（不必 PR）。

| 闯关 | 任务 |
|------|------|
| ★ | fork **sync upstream** 策略 |
| ★★ | 依赖 **SNAPSHOT** 的 **一条回滚预案** |
| ★★★ | CONTRIBUTING 里哪条测试 **曾救命** |

**延伸**：**业务 monorepo 依赖 SNAPSHOT** → **`ToolExecutionResult` 字段缺失**；**实验代码独立 repo**。**改 ServiceHelper 日志级别被拒** → 改 **加 traceId** 而非改全局级别。

### 测试验证

- 实验特性 **feature flag + 金丝雀**。

### 完整代码清单

模块：`langchain4j-agentic`、`langchain4j-agentic-mcp`、`langchain4j-experimental-sql` 等；`CONTRIBUTING.md`。

---

## 4. 项目总结

### 优点与缺点（与同类做法对比）

| 维度 | 社区 + 实验模块 | 仅稳定 API | 闭源 fork |
|------|-----------------|------------|-----------|
| 演进速度 | 快 | 慢 | 视团队 |
| 风险 | 高 | 低 | 高 |
| 典型缺点 | 稳定性自管 | 功能滞后 | 合并成本 |

### 适用场景

- 创新项目、**PoC**、**内生工具**。

### 不适用场景

- **强合规要求**、**无能力锁版本**——勿上实验。

### 注意事项

- **法律审查**第三方许可；**fork 与上游同步**。

### 常见踩坑经验（生产向根因）

1. **实验 API** 随 MINOR 变化 → **静默行为改变**。  
2. **无测试 PR** 难合并。  
3. **大 PR** 审不动。

### 进阶思考题

1. **侧车服务 + feature flag** 的 **发布列车** 如何设计？  
2. **SPI 加载顺序** 在 **native-image** 下的差异？

### 推广计划提示（多部门）

| 角色 | 建议阅读顺序 | 协作要点 |
|------|----------------|----------|
| **开发** | 本章 + 第 3 章 | **稳定模块边界** |
| **架构** | 实验隔离 | **SLO 分级** |
| **社区** | CONTRIBUTING | **小步 PR** |

---

### 本期给测试 / 运维的检查清单

**测试**：对实验特性 **特性开关** + **金丝雀**。**运维**：隔离 **命名空间**与 **SLO 分级**。

### 附录

模块：`langchain4j-agentic`、`langchain4j-agentic-mcp`、`langchain4j-experimental-sql` 等。文档：上游 `CONTRIBUTING.md`。
