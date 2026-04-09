# 参训对象与章节范围确认

本文供**培训组织方在开课前**填写或评审，用于对齐学员基础与是否纳入选修章节。无需全员一致，可按小组分流。

## 1. 建议前置能力（默认「标准班」）

| 能力项 | 最低要求 | 说明 |
|--------|----------|------|
| Java | JDK 8+ 语法熟练；标准班建议 JDK 17（与 samples 默认 Spring Boot 3 一致） | 需理解接口、泛型、Lambda |
| Spring | 理解 IoC、`@Autowired`、分层（Controller / Service） | 无 Spring 经验者需先补 1 天 Spring 入门 |
| MyBatis | 知道 `SqlSessionFactory`、Mapper 接口与 XML 大致关系即可 | 不要求精通动态 SQL |
| 构建工具 | 能使用 Maven 单模块测试：`mvn -pl <module> test` | samples 为 Maven 聚合工程 |
| SQL | 能读写 `SELECT`/`INSERT`/`UPDATE`/`DELETE`、理解主键与索引概念 | 与第 4～5 章 Wrapper、分页强相关 |

**「标准班」结论**：具备 Java + Spring Boot 入门 + 基本 SQL 的初中级后端，可直接从第 1 章按顺序学习。

## 2. 开课前问卷（可复制到表单）

1. 你最近一年主要使用的 JDK 版本是？（8 / 11 / 17 / 21 / 其他）
2. 是否独立搭建过 Spring Boot 项目并跑通过单元测试？（是 / 否）
3. 是否写过原生 MyBatis Mapper XML？（熟练 / 用过 / 未用过）
4. 当前项目是否使用 Kotlin？（是 / 否 / 计划引入）
5. 当前项目是否存在「无 Spring」或「仅 Spring MVC 无 Boot」模块？（是 / 否）
6. 最希望培训后能解决的三类问题：___________

## 3. 选修章节决策矩阵

| 计划章节 | 建议纳入条件 | 可跳过条件 |
|----------|--------------|------------|
| 第 1 章中的 **Spring MVC 快速上手**（`mybatis-plus-sample-quickstart-springmvc`） | 团队仍有非 Boot 的 MVC 遗留项目 | 全栈已 Boot 化 |
| 第 6 章 **PageHelper** | 仓库里同时存在 PageHelper | 统一只用 MP 分页 |
| 第 14 章 **无 Spring**（`mybatis-plus-sample-no-spring`） | 存在控制台、测试进程、非 Spring 宿主 | 全部为 Spring 应用 |
| 第 14 章 **Kotlin**（`mybatis-plus-sample-kotlin`） | 已有或计划 Kotlin 模块 | 纯 Java 团队可标记为自学 |
| 第 15 章 **性能与执行分析** | 中级以上、需排障与 SQL 治理 | 初级班可改为录播或自学 |

**组织方填写**：本期培训是否必须覆盖以下模块（打勾）：

- [ ] Spring MVC 示例（非 Boot）
- [ ] 无 Spring 启动方式
- [ ] Kotlin 示例
- [ ] 第 15 章 源码/插件链深度（现场 45～60 分钟）

**本期默认 JDK / Spring 版本**：_____________（建议与生产一致，并切换 samples 的 Maven Profile：`spring-boot3` / `spring-boot2` / `spring-boot4`）

## 4. 分班建议

- **A 班（初级）**：第 1～8 章 + 第 10 章前半（枚举）；第 14～15 章选修或自学。
- **B 班（中高级）**：第 1～13 章全讲；第 14 章选 Kotlin 或无 Spring 之一；第 15 章现场导读插件链。

---

确认完成后，将勾选结果同步给讲师，并据此裁剪 [LABS_CHECKLIST.md](LABS_CHECKLIST.md) 中的「必做实验」行。
