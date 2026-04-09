# 第 4 章：条件构造器 `Wrapper`（`QueryWrapper` / `LambdaQueryWrapper` / `UpdateWrapper`）

示例模块：`mybatis-plus-sample-wrapper`（`WrapperTest`、`UpdateWrapperTest`）。

## 1）项目背景

- 业务查询条件组合多，手写拼接 SQL 易错且难测。
- `QueryWrapper` 用字符串列名，`LambdaQueryWrapper` 用方法引用，**重构时编译期可发现列名错误**。
- `UpdateWrapper` 支持「只改部分列、WHERE 与 SET 分离」等写法。
- **本章目标**：能区分三种 Wrapper 的适用场景；理解 `nested`/`and`/`apply`/`inSql` 的风险边界。

## 2）项目设计：大师与小白的对话

**小白**：字符串写 `eq("name", x)` 最快。

**大师**：字段改名后 Lambda 写法编译不过；字符串写法可能运行期才暴露，排查成本高。

**小白**：Wrapper 能替代所有报表 SQL 吗？

**大师**：复杂子查询、方言特性、性能敏感的大 SQL，该回 XML 就回 XML；Wrapper 是利器不是万能胶。

**小白**：`apply` 里拼字符串行不行？

**大师**：可以，但要当作**手写 SQL 片段**对待：注意参数占位与注入风险，示例里注释也标了「sql注入」警示。

**本章金句**：Lambda Wrapper 买的是**重构安全**；`last()`、`apply` 拼的是**责任**。

## 3）项目实战：主代码片段

**查询：`QueryWrapper` 与 Lambda 对照、嵌套条件**：

```29:52:d:\software\workspace\mybatis-plus\mybatis-plus-samples\mybatis-plus-sample-wrapper\src\test\java\com\baomidou\mybatisplus\samples\wrapper\WrapperTest.java
    @Test
    public void tests() {
        System.out.println("----- 普通查询 ------");
        List<User> plainUsers = userMapper.selectList(new QueryWrapper<User>().eq("role_id", 2L));
        List<User> lambdaUsers = userMapper.selectList(new QueryWrapper<User>().lambda().eq(User::getRoleId, 2L));
        Assertions.assertEquals(plainUsers.size(), lambdaUsers.size());
        print(plainUsers);

        System.out.println("----- 带子查询(sql注入) ------");
        List<User> plainUsers2 = userMapper.selectList(new QueryWrapper<User>()
                .inSql("role_id", "select id from role where id = 2"));
        List<User> lambdaUsers2 = userMapper.selectList(new QueryWrapper<User>().lambda()
                .inSql(User::getRoleId, "select id from role where id = 2"));
        Assertions.assertEquals(plainUsers2.size(), lambdaUsers2.size());
        print(plainUsers2);

        System.out.println("----- 带嵌套查询 ------");
        List<User> plainUsers3 = userMapper.selectList(new QueryWrapper<User>()
                .nested(i -> i.eq("role_id", 2L).or().eq("role_id", 3L))
                .and(i -> i.ge("age", 20)));
        List<User> lambdaUsers3 = userMapper.selectList(new QueryWrapper<User>().lambda()
                .nested(i -> i.eq(User::getRoleId, 2L).or().eq(User::getRoleId, 3L))
                .and(i -> i.ge(User::getAge, 20)));
        Assertions.assertEquals(plainUsers3.size(), lambdaUsers3.size());
        print(plainUsers3);
```

**更新：`UpdateWrapper` / `LambdaUpdateWrapper`**：

```24:38:d:\software\workspace\mybatis-plus\mybatis-plus-samples\mybatis-plus-sample-wrapper\src\test\java\com\baomidou\mybatisplus\samples\wrapper\UpdateWrapperTest.java
    @Test
    public void tests() {

        //方式一：
        User user = new User();
        user.setAge(29);
        user.setEmail("test3update@baomidou.com");

        userMapper.update(user,new UpdateWrapper<User>().eq("name","Tom"));

        //方式二：
        //不创建User对象
        userMapper.update(null,new UpdateWrapper<User>()
                .set("age",29).set("email","test3update@baomidou.com").eq("name","Tom"));

    }
```

## 4）项目总结

| 维度 | 说明 |
|------|------|
| **优点** | 可读、可组合；Lambda 列名与 IDE 重构友好。 |
| **缺点 / 边界** | 极复杂条件链可读性下降；与索引、执行计划需人工核对。 |
| **适用场景** | 后台列表筛选、动态 WHERE、批量条件更新。 |
| **注意事项** | `inSql`、`apply` 等拼接片段须防注入；团队规范是否允许 `last()`。 |
| **常见踩坑** | 滥用 `or()` 导致括号与优先级不符合预期；字符串列名与 DB 保留字未转义；把报表级 SQL 硬堆进 Wrapper。 |

**课后动作**：`mvn -pl mybatis-plus-sample-wrapper test`。详见 [LABS_CHECKLIST.md](LABS_CHECKLIST.md)。
