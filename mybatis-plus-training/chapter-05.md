# 第 5 章：分页与排序（内置分页插件）

示例模块：`mybatis-plus-sample-pagination`。配置类：`MybatisPlusConfig` 注册 `PaginationInnerInterceptor`。

## 1）项目背景

- 列表接口必须在数据库侧做**物理分页**，避免大结果集进内存。
- MP 通过分页插件改写 SQL、执行 count，并与 `Page`/`IPage` 统一封装页码、总条数、排序。
- **本章目标**：会用 `Page` + `selectPage`；理解必须注册分页插件；了解自定义 XML 分页扩展点。

## 2）项目设计：大师与小白的对话

**小白**：我先 `selectList` 再 `subList` 分页。

**大师**：数据量一上来内存和网络都扛不住；产品还会让你翻最后一页。

**小白**：分页插件会自动 count 吗？

**大师**：默认会；大表场景要关注 count SQL 是否合理，必要时自定义优化（见官方文档与 `Page` 相关开关）。

**小白**：换数据库怎么办？

**大师**：分页方言由 `DbType` 等与插件配置决定；业务代码仍只传 `Page` 对象。

**本章金句**：分页是**插件能力**，不是 `BaseMapper` 自带的「默认可用魔法」——**没加插件就没分页**。

## 3）项目实战：主代码片段

**注册分页插件（H2 示例）**：

```19:24:d:\software\workspace\mybatis-plus\mybatis-plus-samples\mybatis-plus-sample-pagination\src\main\java\com\baomidou\mybatisplus\samples\pagination\config\MybatisPlusConfig.java
    @Bean
    public MybatisPlusInterceptor mybatisPlusInterceptor() {
        MybatisPlusInterceptor interceptor = new MybatisPlusInterceptor();
        interceptor.addInnerInterceptor(new PaginationInnerInterceptor(DbType.H2));
        return interceptor;
    }
```

**Lambda 条件分页**：

```40:46:d:\software\workspace\mybatis-plus\mybatis-plus-samples\mybatis-plus-sample-pagination\src\test\java\com\baomidou\mybatisplus\samples\pagination\PaginationTest.java
    @Test
    void lambdaPagination() {
        Page<User> page = new Page<>(1, 3);
        Page<User> result = mapper.selectPage(page, Wrappers.<User>lambdaQuery().ge(User::getAge, 1).orderByAsc(User::getAge));
        assertThat(result.getTotal()).isGreaterThan(3);
        assertThat(result.getRecords().size()).isEqualTo(3);
    }
```

**排序与自定义 XML 分页**：同文件 `tests1()` 中 `page.addOrder(OrderItem.asc("age"))` 与 `mapper.mySelectPage` 示例。

**深度阅读（选修）**：`mybatis-plus-extension/.../plugins/pagination/Page.java`、`DialectFactory`；插件实现见 [CHAPTER_15_SOURCE_DEEP_DIVE.md](CHAPTER_15_SOURCE_DEEP_DIVE.md)。

## 4）项目总结

| 维度 | 说明 |
|------|------|
| **优点** | 与 `BaseMapper` 无缝集成；多数据库方言封装；支持自定义分页 XML。 |
| **缺点 / 边界** | count 可能成为热点；极复杂 SQL 需人工优化或拆分。 |
| **适用场景** | 绝大多数后台分页列表、导出前预览。 |
| **注意事项** | 多插件时顺序（如租户插件与分页插件）；`useDeprecatedExecutor` 等与缓存相关配置见示例注释。 |
| **常见踩坑** | 忘记注册 `PaginationInnerInterceptor`；`DbType` 与实际库不一致；与 PageHelper 混用策略不清（第 6 章）。 |

**课后动作**：`mvn -pl mybatis-plus-sample-pagination test`。详见 [LABS_CHECKLIST.md](LABS_CHECKLIST.md)。
