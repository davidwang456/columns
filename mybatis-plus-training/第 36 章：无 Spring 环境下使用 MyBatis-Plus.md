# 第 36 章：无 Spring 环境下使用 MyBatis-Plus

示例模块：`mybatis-plus-sample-no-spring`（**选修**）。

## 1）项目背景

批处理、单测脚手架、遗留容器内可能**没有 Spring IoC**，但仍需 MyBatis 访问数据库。MP 提供 `MybatisSqlSessionFactoryBuilder` 与 `MybatisConfiguration`，由开发者自行管理 **DataSource、事务、插件** 生命周期。

**痛点放大**：无自动配置时，分页/租户等插件需**手动** `addInnerInterceptor`；资源泄露（未关闭 `SqlSession`）在脚本里更常见。

**本章目标**：跑通 `NoSpring` 的 `main`；口述最小启动步骤；明确与 Spring Boot 集的差异。

## 2）项目设计：小胖、小白与大师的对话

**小胖**：没有 Spring 就不能用 MP 吧？

**大师**：能用，**代价**是你自己组装工厂与插件；Spring 只是默认宿主。

**技术映射**：**无 Spring** ≈ 手动拧螺丝；**Boot** ≈ 成品机组装。

---

**小白**：生产会这样用吗？

**大师**：少见，但**工具链、遗留系统**会遇到；懂这条路径排障更快。

**本章金句**：**Spring 是默认姿势，不是存在前提。**

## 3）项目实战

**环境准备**：`cd mybatis-plus-samples`；运行 `NoSpring#main`（或 `mvn -pl mybatis-plus-sample-no-spring compile exec:java` 视模块配置而定）。

**步骤 1：最小启动**

```31:51:d:\software\workspace\mybatis-plus\mybatis-plus-samples\mybatis-plus-sample-no-spring\src\main\java\com\baomidou\mybatisplus\no\spring\NoSpring.java
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

**验证方式**：控制台打印插入与查询结果。

**完整代码清单**：`mybatis-plus-samples/mybatis-plus-sample-no-spring/`

## 4）项目总结

| 优点 | 缺点 / 边界 |
|------|-------------|
| 边界清晰 | 一切手动 |
| 适合脚本/教学 | 生产缺少统一事务与观测 |

**适用场景**：控制台工具、非 Spring 宿主遗留系统。

**不适用场景**：已统一 Spring Boot 的团队（可跳过）。

**注意事项**：内存 H2 仅演示；生产需真实连接与迁移。

**常见踩坑（案例化）**

1. **现象**：插件不生效。**根因**：未注册到 `MybatisConfiguration`。**处理**：对照 Spring 配置迁移。
2. **现象**：连接泄漏。**根因**：未 `try-with-resources`。**处理**：规范关闭。
3. **现象**：与 Spring 混用两套工厂。**根因**：边界不清。**处理**：进程级隔离。

**思考题**

1. 无 Spring 时如何实现与 `MetaObjectHandler` 等效的能力？
2. 与 Testcontainers 集成测试如何复用该模式？

**课后动作**：[LABS_CHECKLIST.md](LABS_CHECKLIST.md) 第 36 章（选做）。
