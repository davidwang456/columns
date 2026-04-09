# 第 13 章：DDL 辅助、代码生成与多模块工程装配

示例模块：`mybatis-plus-sample-ddl-mysql`（需 MySQL）、`mybatis-plus-sample-assembly`；代码生成见源码仓 `mybatis-plus-generator` 中 `FastAutoGeneratorTest` 等。

## 1）项目背景

- **DDL**：在可控环境下执行版本化脚本、初始化结构，与 MP 的扩展 DDL 能力配合（注意权限与环境隔离）。
- **代码生成器**：从元数据批量生成 Entity、Mapper、Service、XML 等，加速脚手架；生成物应纳入评审与后续维护。
- **多模块装配**：Mapper XML 位于非主模块时，常见问题是 **打包资源未包含 XML** 或 **`mapper-locations` 未指向依赖 jar**。
- **本章目标**：能运行/阅读生成器入口；理解 `assembly` 工程的 Web + Service + Mapper 分层；知道 DDL 示例的脚本组织方式。

## 2）项目设计：大师与小白的对话

**小白**：代码生成器跑一遍，以后就不用管了。

**大师**：生成的是**起点**；表结构一变，增量合并与手写代码冲突要靠流程解决。

**小白**：为什么打包后线上找不到 Mapper XML？

**大师**：Maven 资源过滤、多模块路径、`spring-boot-maven-plugin` 打包规则，任一不对都会「本地能跑、线上没 XML」。

**小白**：DDL 能在生产随便跑吗？

**大师**：要有变更评审、备份与回滚；示例里的脚本组织方式学的是**结构**，不是让你直连生产乱执行。

**本章金句**：生成器提升**第一公里**；工程装配决定**能不能稳定跑完全程**。

## 3）项目实战：主代码片段

**DDL 脚本列表（`SimpleDdl`）**：

```9:28:d:\software\workspace\mybatis-plus\mybatis-plus-samples\mybatis-plus-sample-ddl-mysql\src\main\java\com\baomidou\mybatisplus\samples\ddl\mysql\MysqlDdl.java
@Component
public class MysqlDdl extends SimpleDdl {

    /**
     * 执行 SQL 脚本方式
     */
    @Override
    public List<String> getSqlFiles() {
        return Arrays.asList(
                // 测试存储过程
                "db/test_procedure.sql#$$",

                // 内置包方式
                "db/tag-schema.sql",
                "db/tag-data.sql"

                // 文件绝对路径方式（修改为你电脑的地址）
                // "D:\\sql\\tag-data.sql"
        );
    }
}
```

**快速生成器入口（源码仓）**：

```28:46:d:\software\workspace\mybatis-plus\mybatis-plus\mybatis-plus-generator\src\test\java\com\baomidou\mybatisplus\generator\samples\FastAutoGeneratorTest.java
    public static void main(String[] args) throws SQLException {
        // 初始化数据库脚本
        initDataSource(DATA_SOURCE_CONFIG.build());
        FastAutoGenerator.create(DATA_SOURCE_CONFIG)
            // 数据库配置
            .dataSourceConfig((scanner, builder) -> builder.schema(scanner.apply("请输入表名称")))
            // 全局配置
            .globalConfig((scanner, builder) -> builder.author(scanner.apply("请输入作者名称")))
            // 包配置
            .packageConfig((scanner, builder) -> builder.parent(scanner.apply("请输入包名")))
            // 策略配置
            .strategyConfig((scanner, builder) -> builder.addInclude(scanner.apply("请输入表名，多个表名用,隔开")))
            .execute();
    }
```

**多模块 Web 层调用 Service + Wrapper**：

```26:37:d:\software\workspace\mybatis-plus\mybatis-plus-samples\mybatis-plus-sample-assembly\src\main\java\com\baomidou\mybatisplus\samples\assembly\controller\UserController.java
    // 测试地址 http://localhost:8080/test
    @RequestMapping(value = "test")
    public String test(){
        User user = new User();
        user.setEmail("papapapap@qq.com");
        user.setAge(18);
        user.setName("啪啪啪");
        userService.save(user);
        List<User> list = userService.list(new LambdaQueryWrapper<>(new User()).select(User::getId, User::getName));
        list.forEach(u -> LOGGER.info("当前用户数据:{}", u));
        return "papapapap@qq.com";
    }
```

## 4）项目总结

| 维度 | 说明 |
|------|------|
| **优点** | 生成器极大提速；DDL 与脚本列表便于环境初始化；assembly 展示典型分层。 |
| **缺点 / 边界** | 生成策略与模板需团队统一；DDL 误用有环境风险。 |
| **适用场景** | 新服务脚手架、表多且结构规整；教学与内部平台封装。 |
| **注意事项** | 生成目录与 Git 忽略策略；多模块 `mapperLocations`；生产变更流程。 |
| **常见踩坑** | 重复生成覆盖手写代码；XML 未打进 jar；生成器连接串提交到仓库。 |

**课后动作**：本地阅读或运行 `FastAutoGeneratorTest`（H2）；`assembly` 启动 `AssemblyApplication` 访问 `/test`；DDL 模块在具备 MySQL 时再启用。详见 [LABS_CHECKLIST.md](LABS_CHECKLIST.md)。
