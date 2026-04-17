# 第 12 章：分页插件：`Page`、`selectPage` 与注册拦截器

示例模块：`mybatis-plus-sample-pagination`。配置类：`MybatisPlusConfig` 中注册 `MybatisPlusInterceptor` 与 `PaginationInnerInterceptor`。

## 1）项目背景

运营后台的「用户列表」接口若一次性 `selectList` 拉回十万行，会在**应用内存、JDBC 缓冲、网络传输**三层同时爆炸；产品还会要求「翻到最后一页」「按年龄排序」。正确做法是**在数据库侧做物理分页**（`LIMIT`/`OFFSET` 或等价语法），并单独执行 **count** 以支持前端分页组件。原生 MyBatis 要自己写两条 SQL 或在插件里拼接；MyBatis-Plus 则通过 **`PaginationInnerInterceptor`** 在运行时改写 `BoundSql`，对业务暴露统一的 `Page` / `IPage` 模型。

**痛点放大**：若不使用分页插件而手写分页，团队要在每个数据库上维护不同方言；若忘记注册插件却调用了 `selectPage`，会出现**分页不生效**或行为与预期不符。多插件并存时（租户、乐观锁、分页），**顺序错误**还会导致 count 或 limit 条件缺失——这类问题往往在**联调或生产**才暴露。

**本章目标**：理解「分页是插件能力，不是 BaseMapper 自带魔法」；能注册 `PaginationInnerInterceptor` 并跑通 `Page` + `selectPage`；知道 `DbType` 与实际数据库一致的重要性。

```mermaid
flowchart TB
  subgraph must [使用分页API前必须满足]
    R[注册 MybatisPlusInterceptor]
    P[添加 PaginationInnerInterceptor]
    D[DbType 与真实库一致或能推断]
  end
  R --> P --> D
```

## 2）项目设计：小胖、小白与大师的对话

**小胖**：我先 `selectList` 再在 Java 里 `subList` 切一页，不就分页了吗？跟翻书一样翻一下嘛。

**小白**：数据量上万、请求一多，内存和 GC 会先扛不住；而且数据库已经排序好了，你在内存切片，**排序语义**还可能错。

**大师**：分页必须发生在**离数据最近**的一层——通常是数据库执行计划里只拉当前页。`subList` 只适合演示或极小数据量。

**技术映射**：**物理分页** ≈ 食堂只盛出这一盘菜；**内存分页** ≈ 先整锅端走再倒掉多余的。

---

**小白**：我调了 `mapper.selectPage`，为啥有的项目能分页、有的不能？

**大师**：**必须**在 Spring 里注册 `MybatisPlusInterceptor`，并向其中 `addInnerInterceptor(new PaginationInnerInterceptor(...))`。没这一步，`Page` 参数可能被当成普通对象忽略，表现就像普通查询。

**小白**：`DbType.H2` 和线上 MySQL 不一致会怎样？

**大师**：方言负责拼分页与 count 的 SQL 片段；配错可能导致**语法错误**或**分页错位**。示例用 H2，生产要改成 `DbType.MYSQL` 等真实类型，或由连接与驱动推断（视版本而定）。

---

**小胖**：分页插件会自动 count 吗？count 很慢怎么办？

**大师**：默认会对列表查询配合 **count**；大表场景要关注 count SQL 是否合理，可用 `Page` 的 **`searchCount`**、自定义 count 或优化 where（见第 13 章）。**不要**在业务里默认「关掉 count」来掩盖索引问题而不被看见。

**本章金句**：分页是**插件能力**，不是 `BaseMapper` 自带的「默认可用魔法」——**没加插件就没有可靠分页**。

## 3）项目实战

**环境准备**

- 模块：`mybatis-plus-sample-pagination`；JDK / Maven 同仓库约定；`mvn -pl mybatis-plus-sample-pagination -am test`。

**步骤 1：注册分页插件**

目标：让 MP 能识别 `Page` 参数并改写 SQL。

```19:24:d:\software\workspace\mybatis-plus\mybatis-plus-samples\mybatis-plus-sample-pagination\src\main\java\com\baomidou\mybatisplus\samples\pagination\config\MybatisPlusConfig.java
    @Bean
    public MybatisPlusInterceptor mybatisPlusInterceptor() {
        MybatisPlusInterceptor interceptor = new MybatisPlusInterceptor();
        interceptor.addInnerInterceptor(new PaginationInnerInterceptor(DbType.H2));
        return interceptor;
    }
```

**运行预期**：应用启动无异常；若注释掉整个 `Bean`，同模块分页用例会暴露异常或分页失效（可自行对比实验）。

**步骤 2：`Lambda` 条件分页**

目标：`Page` 指定页码与条数，`Wrapper` 写类型安全条件。

```40:46:d:\software\workspace\mybatis-plus\mybatis-plus-samples\mybatis-plus-sample-pagination\src\test\java\com\baomidou\mybatisplus\samples\pagination\PaginationTest.java
    @Test
    void lambdaPagination() {
        Page<User> page = new Page<>(1, 3);
        Page<User> result = mapper.selectPage(page, Wrappers.<User>lambdaQuery().ge(User::getAge, 1).orderByAsc(User::getAge));
        assertThat(result.getTotal()).isGreaterThan(3);
        assertThat(result.getRecords().size()).isEqualTo(3);
    }
```

**运行结果（文字）**：`result.getRecords().size()` 为 3；`getTotal()` 大于 3 表示 count 已执行。

**可能遇到的坑**

| 现象 | 根因 | 处理 |
|------|------|------|
| 分页不生效、全表查出 | 未注册 `PaginationInnerInterceptor` | 检查 `MybatisPlusInterceptor` Bean |
| Oracle/MySQL 语法错误 | `DbType` 与库不一致 | 显式传入正确 `DbType` |
| 与租户插件叠加 count 异常 | 插件顺序导致租户条件未进 count | 调整 `addInnerInterceptor` 顺序（见第 21、35 章） |

**验证命令**：

```bash
cd mybatis-plus-samples
mvn -pl mybatis-plus-sample-pagination -am test
```

**完整代码清单**：`mybatis-plus-samples/mybatis-plus-sample-pagination/`。

**深度阅读**：插件链与方言见 [第 35 章](<第 35 章：插件链与分页方言——源码导读（选修）.md>) 与 [CHAPTER_15_SOURCE_DEEP_DIVE.md](CHAPTER_15_SOURCE_DEEP_DIVE.md)。

## 4）项目总结

| 优点 | 缺点 / 边界 |
|------|-------------|
| 与 `BaseMapper` 无缝集成，业务只传 `Page` | 大表 count 可能成为热点，需索引与 SQL 治理 |
| 多数据库方言由插件隔离 | 极复杂动态 SQL 的 count 需人工优化或自定义 |
| 与 Service 层 `page` 方法一致（见示例 `IUserService`） | 多插件时顺序与数据源隔离配置成本高 |

**适用场景**：绝大多数后台分页列表；需要统一分页模型与前端组件对接的项目。

**不适用场景**：已统一使用 PageHelper 且短期无法迁移的栈（见第 14 章）；流式导出全量数据（应使用游标/分批而非分页模型）。

**注意事项**：`MybatisConfiguration#useDeprecatedExecutor` 与缓存相关配置见示例注释；升级 MP 版本后核对分页插件包名（jsqlparser 模块拆分）。

**常见踩坑（案例化）**

1. **现象**：测试环境正常、生产分页错乱。**根因**：多数据源只给主库注册了拦截器。**处理**：每个 `SqlSessionFactory` 各自注册插件链。
2. **现象**：join 查询 count 极慢。**根因**：count SQL 未优化或与列表 SQL 复杂度不一致。**处理**：自定义 count SQL 或调整 join 条件位置（第 13 章 `tests2`）。
3. **现象**：与 PageHelper 同时开启行为诡异。**根因**：两套分页拦截逻辑冲突。**处理**：团队约定只保留一套或严格隔离 Mapper（第 14 章）。

**思考题**

1. `Page` 构造第三个参数为 `false` 时表示什么？适用于哪些接口？（答案见第 13 章 `currentPageListTest`。）
2. 若租户插件在分页插件**之后**执行，可能出现什么安全问题？（答案提示：count/limit 未带租户条件。）

**课后动作**：[LABS_CHECKLIST.md](LABS_CHECKLIST.md) 第 12 章必做实验。
