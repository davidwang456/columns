# 第 7 章：主键策略、`IdWorker` 与序列/字符串 ID

示例模块：`mybatis-plus-sample-id-generator`、`mybatis-plus-sample-id-string`、`mybatis-plus-sample-sequence`（Oracle 等序列场景）。

## 1）项目背景

- 主键选型影响：单库自增、分布式唯一、业务可读性、数据合并与脱敏。
- MP 通过 `@TableId(type = ...)` 与全局配置配合**雪花、自增、赋值、输入**等策略；序列型数据库有专门示例。
- **本章目标**：能说明自增与分配 ID 的差异；了解批量插入与回填；按环境选对模块阅读。

## 2）项目设计：大师与小白的对话

**小白**：主键用 `long` 自增最省事。

**大师**：合并数据集、分库分表、对外暴露顺序（可预测性）都要提前想；自增不是永远答案。

**小白**：雪花 ID 有什么好处？

**大师**：趋势有序、分布式可生成；但要处理时钟回拨与存储长度，并与前端/long 精度问题对齐。

**小白**：批量插入为什么 ID 有时拿不到？

**大师**：与驱动、回填策略、是否使用 JDBC batch 有关；要对照官方说明做集成测试。

**本章金句**：主键是**数据架构**问题，MP 只是帮你少写配置。

## 3）项目实战：主代码片段

**插入与批量（`id-generator` 示例）**：

```24:48:d:\software\workspace\mybatis-plus\mybatis-plus-samples\mybatis-plus-sample-id-generator\src\test\java\com\baomidou\samples\IdGeneratorTest.java
    @Test
    public void test() {
        User user = new User();
        user.setName("靓仔");
        user.setAge(18);
        userMapper.insert(user);
        Assertions.assertEquals(Long.valueOf(1L), user.getId());

        testBatch();
    }

    /**
     * 批量插入
     */
    public void testBatch() {
        List<User> users = new ArrayList<>();
        for (int i = 1; i <= 10; i++) {
            User user = new User();
            user.setName("靓仔" + i);
            user.setAge(18 + i);
            users.add(user);
        }
        boolean result = userService.saveBatch(users);
        Assertions.assertEquals(true, result);
    }
```

**字符串主键**：见 `mybatis-plus-sample-id-string` 中实体 `@TableId` 与 `IdStringTest`。  
**序列**：见 `mybatis-plus-sample-sequence` 中 `SequenceTest` 与数据源配置。

**深度**：`IdentifierGenerator`、`TableInfo` 等与主键生成相关的逻辑在 `mybatis-plus-core` 中，讲师可按需点到为止。

## 4）项目总结

| 维度 | 说明 |
|------|------|
| **优点** | 策略声明式配置；与插入回填配合减少样板代码。 |
| **缺点 / 边界** | 策略选错会导致线上 ID 冲突或回填为 null；需与 DBA 规范一致。 |
| **适用场景** | 新业务选型；迁移期并存多种主键类型时分模块约定。 |
| **注意事项** | 批量插入、多数据源、读写分离下的 ID 生成一致性。 |
| **常见踩坑** | 插入后实体 `id` 仍为 null；字符串 ID 与数字列类型混用；Oracle/PG 序列与 `@TableId` 类型不匹配。 |

**课后动作**：按环境运行 `id-generator` / `id-string` / `sequence` 中至少一个模块的 `test`。详见 [LABS_CHECKLIST.md](LABS_CHECKLIST.md)。
