# 第 10 章：`LambdaQueryWrapper` 与类型安全条件

示例模块：`mybatis-plus-sample-wrapper`（`WrapperTest`）。

## 1）项目背景

业务列表筛选条件组合多，字符串列名 `eq("name", x)` 在字段改名后**运行期**才报错。`LambdaQueryWrapper` 用方法引用 `User::getName`，重构时**编译期**暴露问题，并与 IDE 联动。

**痛点放大**：`inSql`、`nested` 等能力强大，误用会产生**优先级错误**或**注入风险**；团队若禁止 Lambda 只用字符串，会长期付维护成本。

**本章目标**：熟练 `lambda()`/`LambdaQueryWrapper`；理解 `nested`/`and` 与 `inSql` 风险；跑通 `WrapperTest`。

## 2）项目设计：小胖、小白与大师的对话

**小胖**：我写 `eq("name")` 一把梭最快。

**大师**：快在**当下**，慢在**下一次改名**——重构时全仓库搜字符串。

**技术映射**：**Lambda 列引用** ≈ GPS 导航；**字符串列名** ≈ 口头指路。

---

**小白**：`inSql` 为啥标注注入？

**大师**：拼的是 SQL 片段，**值**仍要走参数绑定；不可拼接用户输入。

**本章金句**：Lambda Wrapper 买的是**重构安全**。

## 3）项目实战

**环境准备**：`mvn -pl mybatis-plus-sample-wrapper -am test`。

**步骤 1：普通查询与 Lambda 对照**

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
    }
```

**验证命令**：`mvn -pl mybatis-plus-sample-wrapper -am test`

**完整代码清单**：`mybatis-plus-samples/mybatis-plus-sample-wrapper/`

## 4）项目总结

| 优点 | 缺点 / 边界 |
|------|-------------|
| 类型安全、重构友好 | 极复杂链可读性下降 |
| 与 `QueryWrapper` 互通 | `inSql` 需防注入 |

**适用场景**：后台动态筛选、运营配置条件。

**不适用场景**：极复杂报表 SQL（应回 XML）。

**注意事项**：`or()` 括号优先级；与索引、执行计划核对。

**常见踩坑（案例化）**

1. **现象**：条件 OR 范围过大。**根因**：`or` 未嵌套。**处理**：用 `nested`。
2. **现象**：Lambda 序列化问题。**根因**：匿名环境。**处理**：避免过度缓存 Wrapper。
3. **现象**：把报表 SQL 硬堆进 Wrapper。**根因**：职责错置。**处理**：下推 XML。

**思考题**

1. `QueryWrapper` 的 `lambda()` 与 `LambdaQueryWrapper` 直接 new 有何差异？
2. 何时应禁止 `apply` 拼接用户输入？

**课后动作**：[LABS_CHECKLIST.md](LABS_CHECKLIST.md) 第 10 章。
