# 第 14 章：无 Spring、Kotlin 与综合演练（Deluxe）

示例模块：`mybatis-plus-sample-no-spring`、`mybatis-plus-sample-kotlin`、`mybatis-plus-sample-deluxe`。

## 1）项目背景

- **无 Spring**：部分任务（批处理、单测脚手架、遗留容器）仍需 MyBatis，但无 IoC；需自行构建 `MybatisSqlSessionFactory` 与 `MybatisConfiguration`。
- **Kotlin**：空安全、数据类与 MP 的 `@TableName` 等注解混用需注意 **无参构造**、**open** 等与 MyBatis 代理的要求（以当前版本文档与示例为准）。
- **Deluxe**：综合自定义 XML 分页、`BaseMapper` 分页、批量等，适合作为**结业串讲**。
- **本章目标**：能说出无 Spring 时的最小启动步骤；能读 Kotlin 测试中的分页与填充；能运行 Deluxe 测试作为能力验收。

## 2）项目设计：大师与小白的对话

**小白**：没有 Spring 就不能用 MyBatis-Plus 吧？

**大师**：可以，用 `MybatisSqlSessionFactoryBuilder` + `MybatisConfiguration` 自己组装；代价是你要自己管生命周期与插件。

**小白**：Kotlin 里数据类当实体行不行？

**大师**：要注意 MyBatis 创建代理与实例化的要求；以官方示例 `mybatis-plus-sample-kotlin` 为准，别凭感觉省构造函数。

**小白**：Deluxe 和前面章节重复吗？

**大师**：重复的是 API，不重复的是**组合场景**——用来检验你是否能把分页、条件、XML 放在同一脑子里。

**本章金句**：Spring 是**默认姿势**，不是**存在前提**；Kotlin 是**语法糖衣**，底层仍是 MyBatis 规则。

## 3）项目实战：主代码片段

**无 Spring 最小启动**：

```31:47:d:\software\workspace\mybatis-plus\mybatis-plus-samples\mybatis-plus-sample-no-spring\src\main\java\com\baomidou\mybatisplus\no\spring\NoSpring.java
    public static void main(String[] args) {
        try (SqlSession session = sqlSessionFactory.openSession(true)) {
            PersonMapper mapper = session.getMapper(PersonMapper.class);
            Person person = new Person().setName("老李");
            mapper.insert(person);
            System.out.println("结果: " + mapper.selectById(person.getId()));
        }
    }

    public static SqlSessionFactory initSqlSessionFactory() {
        DataSource dataSource = dataSource();
        TransactionFactory transactionFactory = new JdbcTransactionFactory();
        Environment environment = new Environment("Production", transactionFactory, dataSource);
        MybatisConfiguration configuration = new MybatisConfiguration(environment);
        configuration.addMapper(PersonMapper.class);
        configuration.setLogImpl(StdOutImpl.class);
        return new MybatisSqlSessionFactoryBuilder().build(configuration);
    }
```

**Kotlin 分页与插入（含填充字段断言）**：

```23:38:d:\software\workspace\mybatis-plus\mybatis-plus-samples\mybatis-plus-sample-kotlin\src\test\kotlin\com.baomidou.mybatisplus.samples.kotlin\ApplicationTests.kt
    @Test
    fun test() {
        logger.info("--------------演示分页查询--------------------")
        val page = userMapper.selectPage(Page(1, 3), null)
        Assertions.assertEquals(5, page.total)
        Assertions.assertEquals(3, page.size)
        val insertUser = User()
        insertUser.name = "demo"
        insertUser.age = 10
        insertUser.email = "demo@example.com"
        logger.info("--------------演示写入--------------------")
        Assertions.assertTrue(userMapper.insert(insertUser) > 0)
        Assertions.assertNotNull(insertUser.createUserId)
        Assertions.assertNotNull(insertUser.createTime)
        Assertions.assertNull(insertUser.updateTime)
        Assertions.assertNull(insertUser.updateTime)
```

**Deluxe：XML 分页与 `BaseMapper` 分页对照**：

```28:46:d:\software\workspace\mybatis-plus\mybatis-plus-samples\mybatis-plus-sample-deluxe\src\test\java\com\baomidou\mybatisplus\samples\deluxe\DeluxeTest.java
    @Test
    public void testPage() {
        System.out.println("------ 自定义 xml 分页 ------");
        UserPage selectPage = new UserPage(1, 5).setSelectInt(20);
        UserPage userPage = mapper.selectUserPage(selectPage);
        Assertions.assertSame(userPage, selectPage);
        System.out.println("总条数 ------> " + userPage.getTotal());
        System.out.println("当前页数 ------> " + userPage.getCurrent());
        System.out.println("当前每页显示数 ------> " + userPage.getSize());
        print(userPage.getRecords());

        System.out.println("------ baseMapper 自带分页 ------");
        Page<User> page = new Page<>(1, 5);
        IPage<User> userIPage = mapper.selectPage(page, new QueryWrapper<User>().eq("age", 20));
        Assertions.assertSame(userIPage, page);
        System.out.println("总条数 ------> " + userIPage.getTotal());
        System.out.println("当前页数 ------> " + userIPage.getCurrent());
        System.out.println("当前每页显示数 ------> " + userIPage.getSize());
        print(userIPage.getRecords());
    }
```

## 4）项目总结

| 维度 | 说明 |
|------|------|
| **优点** | 无 Spring 路径澄清 MP 边界；Kotlin 示例贴近现代栈；Deluxe 适合结业复盘。 |
| **缺点 / 边界** | 无 Spring 时插件与配置全靠手写；Kotlin 与 Java 混用模块要统一编译与注解处理。 |
| **适用场景** | 工具链、脚本、Gradle 任务；Kotlin 服务；培训结业大练习。 |
| **注意事项** | 无 Spring 示例自建表在内存 H2，仅作演示；生产需连接真实库与迁移工具。 |
| **常见踩坑** | Kotlin data class 缺省构造导致 MyBatis 实例化失败；无 Spring 下忘记 `openSession` 事务；Deluxe 与业务项目包路径复制错误。 |

**课后动作**：运行 `NoSpring` 的 `main`；`mvn -pl mybatis-plus-sample-kotlin test`；`mvn -pl mybatis-plus-sample-deluxe test`。结业作业见 [LABS_CHECKLIST.md](LABS_CHECKLIST.md) 与培训计划「产出物清单」。
