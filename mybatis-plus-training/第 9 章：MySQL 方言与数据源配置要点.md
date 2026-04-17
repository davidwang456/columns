# 第 9 章：MySQL 方言与数据源配置要点

示例模块：`mybatis-plus-sample-mysql`（**选修**，需本地 MySQL 或按模块 README 调整）。

## 1）项目背景

示例默认多为 H2；真实生产以 **MySQL** 为主流。切换时需对齐：**驱动类**、**JDBC URL 参数**、**大小写/反引号**、以及 MP 侧 **`DbType`**（分页、关键字转义等插件行为）。本章建立「**示例模块 = MySQL 特化配置**」的阅读方式。

**痛点放大**：同一套代码在 H2 通过、在 MySQL 因 `ONLY_FULL_GROUP_BY`、时区、`utf8mb4` 而失败；若未在样本环境验证，上线才爆。

**本章目标**：能对照 `MysqlTest` 与 `application.yml`；列出从 H2 迁 MySQL 的检查清单；知道何时在插件里显式 `DbType.MYSQL`。

## 2）项目设计：小胖、小白与大师的对话

**小胖**：数据库不就是连上就行吗？

**大师**：连上是**第一步**；字符集、时区、隔离级别、SQL 模式，都会影响 MP 生成的语句能不能执行。

**技术映射**：**DbType** ≈ 方言开关；**JDBC URL** ≈ 线路与带宽协商。

---

**小白**：H2 能模拟 MySQL 吗？

**大师**：能模拟一部分；**执行计划与索引行为**仍要在真库验。

**本章金句**：**方言与驱动对齐**，是分页与关键字处理的底线。

## 3）项目实战

**环境准备**：按模块内说明配置数据源；`mvn -pl mybatis-plus-sample-mysql -am test`（若需真实库）。

**步骤 1**：阅读 `MysqlTest` 与实体 `TestData`、枚举映射。

**步骤 2**：对比 `mybatis-plus-sample-quickstart` 的 H2 配置差异（URL、`spring.sql.init`）。

**可能遇到的坑**：本机无 MySQL 时测试跳过——在 CI 用 Testcontainers 或标记 `@Disabled` 并文档说明。

**验证命令**：`mvn -pl mybatis-plus-sample-mysql -am test`

**完整代码清单**：`mybatis-plus-samples/mybatis-plus-sample-mysql/`

## 4）项目总结

| 优点 | 缺点 / 边界 |
|------|-------------|
| 贴近生产 | 环境依赖重 |
| 便于验证方言 | 与 H2 快测互补 |

**适用场景**：团队以 MySQL 为主；迁移前验证。

**不适用场景**：仅 H2 嵌入式教学（可跳过）。

**注意事项**：生产账号密码不进仓库；连接池参数与 K8s 探针。

**常见踩坑（案例化）**

1. **现象**：分页 limit 异常。**根因**：`DbType` 未设对。**处理**：显式 `PaginationInnerInterceptor(DbType.MYSQL)`。
2. **现象**：时区差一天。**根因**：JDBC 与 JVM 时区不一致。**处理**：URL 参数 `serverTimezone`。
3. **现象**：emoji 写入失败。**根因**：非 `utf8mb4`。**处理**：库表字符集升级。

**思考题**

1. MySQL 8 驱动类名与 5.x 有何常见差异？
2. 为何 MP 文档常建议生产显式指定 `DbType`？

**课后动作**：[LABS_CHECKLIST.md](LABS_CHECKLIST.md) 第 9 章（选做）。
