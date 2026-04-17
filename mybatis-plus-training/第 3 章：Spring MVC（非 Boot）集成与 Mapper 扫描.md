# 第 3 章：Spring MVC（非 Boot）集成与 Mapper 扫描

示例模块：`mybatis-plus-sample-quickstart-springmvc`（**选修**）。

## 1）项目背景

大量企业仍有 **Spring MVC + XML** 或注解配置的非 Boot 应用：批处理挂载在旧 Tomcat、政企内网固定中间件版本。此类宿主**没有** `SpringApplication` 与 Boot 自动配置，需要手动注册 `MapperScannerConfigurer` 或等价机制，并自行引入 MP 的 `GlobalConfig`、`SqlSessionFactoryBean` 等。

**痛点放大**：新人习惯 Boot「加依赖即能用」，碰到 MVC 工程时容易把 **Bean 定义顺序、数据源、事务管理器**配错，表现为 Mapper 能编译不能运行、或事务不生效。

**本章目标**：能说出非 Boot 下 MP 的最小 Bean 组合；能读懂示例中 `MpConfig` 的用途；明确团队是否仍需维护此类工程。

## 2）项目设计：小胖、小白与大师的对话

**小胖**：没有 Spring Boot，不就是少个 main 吗？

**大师**：少的是**约定优于配置**那一整层：数据源、事务、扫描路径都要你显式声明。

**技术映射**：**Boot 自动配置** ≈ 精装交付；**纯 MVC** ≈ 毛坯房自己走水电。

---

**小白**：我们新服务都 Boot 了，这章还要讲吗？

**大师**：不讲可以，但**仓库里若还有 MVC 模块**，至少要有一个人能接工单；选修的意义在这。

**本章金句**：**集成方式随宿主变，MP 核心仍是 SqlSessionFactory + Mapper 代理。**

## 3）项目实战

**环境准备**：`mvn -pl mybatis-plus-sample-quickstart-springmvc -am test`。

**步骤 1：阅读 `MpConfig`**

目标：理解非 Boot 下仍可声明 `GlobalConfig`、自定义 `IKeyGenerator` 等（示例为 H2/PostgreSQL 序列演示片段）。

```13:33:d:\software\workspace\mybatis-plus\mybatis-plus-samples\mybatis-plus-sample-quickstart-springmvc\src\main\java\com\baomidou\mybatisplus\samples\quickstart\springmvc\MpConfig.java
@Configuration
public class MpConfig {

    @Bean
    public GlobalConfig globalConfiguration() {
        GlobalConfig conf = new GlobalConfig();
        conf.setDbConfig(new GlobalConfig.DbConfig().setKeyGenerators(Arrays.asList(
                // h2 1.x 的写法（默认 2.x 的写法）
                new IKeyGenerator() {

                    @Override
                    public String executeSql(String incrementerName) {
                        return "select " + incrementerName + ".nextval";
                    }

                    @Override
                    public DbType dbType() {
                        return DbType.POSTGRE_SQL;
                    }
                }
        )));
        return conf;
    }
}
```

**步骤 2：运行 `SpringMvcTest`**

目标：验证在测试上下文中 Mapper 可注入并访问数据库。

**可能遇到的坑**：缺少 `MapperScannerConfigurer` 或组件扫描路径；与遗留 `DataSource` Bean 名称冲突。

**验证命令**：`mvn -pl mybatis-plus-sample-quickstart-springmvc -am test`

**完整代码清单**：`mybatis-plus-samples/mybatis-plus-sample-quickstart-springmvc/`

## 4）项目总结

| 优点 | 缺点 / 边界 |
|------|-------------|
| 兼容遗留栈 | 配置冗长、易错 |
| 与 Boot 共用 MP 核心知识 | 新人学习曲线陡 |

**适用场景**：维护期 MVC 项目；内网固定容器版本。

**不适用场景**：全新项目已统一 Boot（可跳过本章）。

**注意事项**：与团队统一 MP 版本；升级时同时升级 Spring 与 MyBatis。

**常见踩坑（案例化）**

1. **现象**：Bean 重复定义。**根因**：XML 与 Java 配置双注册。**处理**：统一配置入口。
2. **现象**：事务不回滚。**根因**：未走 Spring 管理的事务代理。**处理**：检查 `DataSourceTransactionManager`。
3. **现象**：本地能跑、WAR 部署失败。**根因**：资源未打进 WAR。**处理**：Maven `resources` 与 `packaging`。

**思考题**

1. Boot 的 `MybatisPlusAutoConfiguration` 在非 Boot 里对应哪些手动 Bean？
2. 为何示例在 `GlobalConfig` 中注册 `IKeyGenerator`？与 `@TableId` 类型如何配合？

**课后动作**：[LABS_CHECKLIST.md](LABS_CHECKLIST.md) 第 3 章（选做）。
