# 第 2 章：Spring Boot 下自动配置、Starter 与常见排错

示例模块：`mybatis-plus-sample-quickstart`（与第 1 章相同，本章侧重**排错心智**与自动配置入口）。

## 1）项目背景

第 1 章跑通后，日常问题往往变成：「本地能查、测试环境连不上」「升级 Spring Boot 后 Mapper 突然没了」「多数据源时注入的总是主库的 Mapper」。这些都指向同一件事：**MyBatis-Plus 在 Spring Boot 里由自动配置装配**，不理解装配边界就只能靠试配置项。

**痛点放大**：自动配置**静默失败**时，表现可能是空指针、找不到 Bean、或 SQL 走了错误数据源；若没有「从 `@MapperScan` → `SqlSessionFactory` → `MybatisPlusAutoConfiguration`」这条排查链，排障时间会指数上升。

**本章目标**：能说清 Starter 大致做了什么；知道去哪个类看自动配置；列出多数据源、Profile、包扫描三类最常见故障的检查顺序。

## 2）项目设计：小胖、小白与大师的对话

**小胖**：我加了依赖就能用，为啥还要看自动配置类？我又不写框架。

**大师**：你不写，但要**认领**——升级版本、换 Boot 2/3、多模块时，**谁改坏了 Bean 定义**要靠入口类定位。

**技术映射**：**自动配置类** ≈ 设备总闸；跳闸先找闸，不要先换灯泡。

---

**小白**：`@MapperScan` 和 `@Mapper` 每个接口打一个，有啥取舍？

**大师**：`@MapperScan` 一次扫包适合成批 Mapper；零散文案可用 `@Mapper`。混用时注意**不要重复注册**。

**小白**：两个数据源两套 Mapper，怎么保证不串？

**大师**：通常 **两套 `SqlSessionFactory` + 两套 `@MapperScan` 的 `sqlSessionFactoryRef`**（或等价配置）；单元测试里 `@Autowired` 的接口全限定名要对上对应工厂。

**本章金句**：排错顺序是 **扫描范围 → 工厂 → 数据源**，别从 SQL 语法倒推。

## 3）项目实战

**环境准备**：JDK 17+；`mybatis-plus-samples` 根目录；`-pl mybatis-plus-sample-quickstart -am test`。

**步骤 1：对照第 1 章启动类**

目标：确认 `@MapperScan` 包路径覆盖 `SysUserMapper`。

见 [第 1 章](<第 1 章：从 MyBatis 到 MyBatis-Plus——心智模型与快速上手.md>) 中 `QuickstartApplication` 与 `SysUserMapper` 引用。

**步骤 2：打开源码中的自动配置（本地 IDE）**

目标：建立「入口类」印象：`mybatis-plus-spring-boot3-starter` 模块下的 `MybatisPlusAutoConfiguration`（包名以实际版本为准）。

**步骤 3：故意制造一次「扫不到」**

目标：把 `@MapperScan` 改成错误包名，运行 `QuickStartTest`，观察失败栈，再改回。

**可能遇到的坑**

| 现象 | 根因 | 处理 |
|------|------|------|
| `Invalid bound statement` | XML 未打包或路径未配置 | 检查 `mapper-locations` 与 `resources` |
| 多数据源注入错 Mapper | 未指定 `sqlSessionFactoryRef` | 按官方多数据源示例拆分 |
| Boot 3 与 Boot 2 Starter 混用 | 依赖坐标错误 | 统一 `-P spring-boot3` 或 `spring-boot2` |

**验证命令**：`mvn -pl mybatis-plus-sample-quickstart -am test`

**完整代码清单**：`mybatis-plus-samples/mybatis-plus-sample-quickstart/`

## 4）项目总结

| 优点 | 缺点 / 边界 |
|------|-------------|
| Spring Boot 零配置起步快 | 复杂装配时「黑盒感」强 |
| 与社区文档路径一致 | 大版本升级需对照 Release Note |

**适用场景**：所有 Boot 集成 MP 的项目。

**不适用场景**：非 Spring 宿主（见第 36 章）。

**注意事项**：公司私服 BOM 与 MP 版本对齐；CI 与本地 Profile 一致。

**常见踩坑（案例化）**

1. **现象**：仅测试类失败。**根因**：`@SpringBootTest` 未扫到主类所在包。**处理**：指定 `classes` 或调整包结构。
2. **现象**：偶发连错库。**根因**：动态数据源路由与事务传播。**处理**：显式 `@Transactional` 与路由键规范。
3. **现象**：升级后分页失效。**根因**：拦截器 Bean 条件变化。**处理**：对照新版本自动配置说明。

**思考题**

1. `MybatisPlusAutoConfiguration` 与原生 `MybatisAutoConfiguration` 同时存在时会怎样？（提示：以当前 Spring Boot 与 MP 版本文档为准。）
2. 为何多数据源场景下不建议多个模块共用同一 Mapper 接口全限定名？

**课后动作**：[LABS_CHECKLIST.md](LABS_CHECKLIST.md) 第 1～2 章。
