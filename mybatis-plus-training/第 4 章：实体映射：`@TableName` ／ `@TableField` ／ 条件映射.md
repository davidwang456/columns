# 第 4 章：实体映射：`@TableName` / `@TableField` / 条件映射

示例模块：`mybatis-plus-sample-crud`（可选对照：`mybatis-plus-sample-mysql`）。

## 1）项目背景

表名带 `sys_` 前缀、列名是 `snake_case`，而 Java 类习惯 `User` 与驼峰字段——若全靠 XML `resultMap`，每一处改名都是一次全仓库搜索。MP 用**注解 + 全局策略**把映射关系收拢到实体上，并与 Lambda Wrapper 联动，实现**重构期编译器报错**而非运行期才发现列名写错。

**痛点放大**：若实体与表随意对齐、靠「碰巧能跑」，上线后一次加字段就可能写入错误列；`exist = false` 的展示字段若忘记标注，会变成 insert 多一列而报错。

**本章目标**：熟练使用 `@TableName`、`@TableField`；理解 `exist = false` 与 `SqlCondition` 场景；能对照 `CrudTest` 验收。

## 2）项目设计：小胖、小白与大师的对话

**小胖**：我类名叫 `User`，表就叫 `user`，还要注解干嘛？

**大师**：演示环境可以；一旦表名是 `sys_user`、或分库前缀，就要 `@TableName` 显式对齐**真相来源**。

**技术映射**：**注解** ≈ 门牌与户型图；**全局配置** ≈ 小区统一门牌规则。

---

**小白**：临时字段也要进表吗？

**大师**：展示用、统计用、DTO 混在实体里时，用 `@TableField(exist = false)` 告诉 MP **别当列写**。

**小白**：`User2` 里 `SqlCondition.LIKE` 是干啥的？

**大师**：给**默认条件策略**，减少到处手写 `like` 的重复；仍要警惕与业务真实语义是否一致。

**本章金句**：映射写清「哪些是列、默认怎么比」，比复制 XML 更省钱。

## 3）项目实战

**环境准备**：`mvn -pl mybatis-plus-sample-crud -am test`。

**步骤 1：`@TableName` 与非持久化字段**

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

**步骤 2：列级条件（`User2`）**

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

**步骤 3**：阅读 `CrudTest` 中 `testTableFieldExistFalse`、`testSqlCondition`。

**验证命令**：`mvn -pl mybatis-plus-sample-crud -am test`

**完整代码清单**：`mybatis-plus-samples/mybatis-plus-sample-crud/`

## 4）项目总结

| 优点 | 缺点 / 边界 |
|------|-------------|
| 映射集中、与 Lambda 联动安全 | 注解过多时实体臃肿 |
| 非持久化字段不易误写库 | 需与 DTO 分层策略统一 |

**适用场景**：新表、团队统一命名；需在实体层表达默认查询行为时。

**不适用场景**：强约束用数据库视图 + 只读 DTO，实体完全不暴露持久化细节时。

**注意事项**：主键、逻辑删除注解见第 5、19 章。

**常见踩坑（案例化）**

1. **现象**：插入报未知列。**根因**：`exist = false` 遗漏。**处理**：标注或移出实体。
2. **现象**：`User2` 走错表。**根因**：未配 `@TableName` 且默认规则与库不一致。**处理**：以库为准显式声明。
3. **现象**：条件 LIKE 全表扫。**根因**：`SqlCondition` 滥用。**处理**：索引与业务评审。

**思考题**

1. `@TableField("column_name")` 与全局 `map-underscore-to-camel-case` 同时存在时优先级如何理解？
2. 何时应把 `User2` 这类「行为型映射」改为 Wrapper 显式拼条件？

**课后动作**：[LABS_CHECKLIST.md](LABS_CHECKLIST.md) 第 4 章。
