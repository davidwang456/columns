# 第 2 章：实体映射与注解体系（`@TableName` / `@TableField` / 条件映射）

示例模块：`mybatis-plus-sample-crud`（可选：`mybatis-plus-sample-mysql`）。注解定义见源码包 `com.baomidou.mybatisplus.annotation`（`mybatis-plus-annotation` 模块）。

## 1）项目背景

- 数据库表名、列名与 Java 类/字段往往不完全一致（前缀、分库分表、遗留命名）。
- MP 通过 **注解 + 全局配置** 表达映射关系，减少 XML 里的 `resultMap` 噪音。
- **本章目标**：会用 `@TableName`、`@TableField(exist = false)`；了解字段参与条件构造时的 `@TableField(condition = ...)`（结合 `User2` 示例）。

## 2）项目设计：大师与小白的对话

**小白**：我 Java 字段名必须和数据库列一模一样吗？

**大师**：默认有下划线转驼峰；不一致时用 `@TableField("column_name")` 显式声明。

**小白**：实体里想带个临时统计字段 `count`，不能插入表吧？

**大师**：用 `@TableField(exist = false)`，MP 就不会把它当持久化列。

**小白**：为什么有的地方还要 `User2` 这种实体？

**大师**：演示 **列级条件注解**（如 `SqlCondition.LIKE`）如何影响 Wrapper 生成的 WHERE，属于「映射 + 行为」的组合，比全局瞎 `like` 更可控。

**本章金句**：注解写清「表叫什么、哪些字段不算列、列上默认怎么比」，比到处复制 XML 更省钱。

## 3）项目实战：主代码片段

**表名与非持久化字段**：

```16:30:d:\software\workspace\mybatis-plus\mybatis-plus-samples\mybatis-plus-sample-crud\src\main\java\com\baomidou\mybatisplus\samples\crud\entity\User.java
@Data
@Accessors(chain = true)
@TableName("sys_user")
public class User {

    private Long id;
    private String name;
    private Integer age;
    private String email;
    @TableField(exist = false)
    private String ignoreColumn = "ignoreColumn";

    @TableField(exist = false)
    private Integer count;
}
```

**字段级 SQL 条件**（与 `CrudTest#testSqlCondition` 对照）：

```20:28:d:\software\workspace\mybatis-plus\mybatis-plus-samples\mybatis-plus-sample-crud\src\main\java\com\baomidou\mybatisplus\samples\crud\entity\User2.java
@Data
@Accessors(chain = true)
public class User2 {
    private Long id;
    @TableField(condition = SqlCondition.LIKE, jdbcType = JdbcType.VARCHAR)
    private String name;
    private Integer age;

}
```

## 4）项目总结

| 维度 | 说明 |
|------|------|
| **优点** | 映射集中、可重构（配合 LambdaWrapper 更安全）；非持久化字段不会误写库。 |
| **缺点 / 边界** | 注解过多会让实体「臃肿」；可考虑 DTO/VO 与持久化实体分离。 |
| **适用场景** | 新表设计、团队统一命名规范；需在实体层表达默认查询行为时。 |
| **注意事项** | `@TableId`、主键策略、逻辑删除等在第 7～8 章展开，本章先建立映射概念。 |
| **常见踩坑** | 忘记 `exist = false` 导致插入报错；`User2` 依赖默认表名规则时与真实表名不一致会走错表——**以库为准核对**。 |

**课后动作**：阅读 `CrudTest` 中 `testTableFieldExistFalse`、`testSqlCondition`；运行 `mvn -pl mybatis-plus-sample-crud test`。详见 [LABS_CHECKLIST.md](LABS_CHECKLIST.md)。
