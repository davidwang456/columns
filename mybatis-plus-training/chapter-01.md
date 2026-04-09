# 第 1 章：从 MyBatis 到 MyBatis-Plus——心智模型与快速上手

示例模块：`mybatis-plus-sample-quickstart`（可选：`mybatis-plus-sample-quickstart-springmvc`）。

## 1）项目背景

- **痛点**：原生 MyBatis 在单表场景重复编写 XML 或注解 CRUD；新人易在「映射文件放哪、命名空间怎么对齐」上耗时。
- **定位**：MyBatis-Plus（MP）是 **MyBatis 的增强工具**，在保留 MyBatis 生态的前提下，提供通用 Mapper、条件构造器、插件（分页、租户等），**不是**完整替代 Hibernate/JPA 的 ORM。
- **本章目标**：能说出 MP 与 MyBatis 的分工边界；本地跑通「Boot + `@MapperScan` + `BaseMapper` + 无 XML 查询」；知道自动配置入口在哪（便于以后排错）。

## 2）项目设计：大师与小白的对话

**小白**：我们上了 MyBatis-Plus，是不是以后都不用写 SQL 了？

**大师**：单表增删改查、简单条件，可以少写很多模板；报表、多表关联、复杂子查询，照样写 XML 或 `@Select`，MP 不拦你。

**小白**：那和 JPA 有啥区别？

**大师**：底层还是 MyBatis 的执行模型：你自己掌控 SQL 长什么样；MP 帮你生成**可预期**的单表语句，复杂场景别硬塞进 Wrapper。

**小白**：我实体类没加注解也能查吗？

**大师**：很多项目靠默认驼峰、表名与类名映射能跑起来；但一旦表名、主键和约定不一致，就要回到第 2 章的注解。先跑通 quickstart，再谈规范。

**本章金句**：MP 省的是样板代码，不是业务 SQL 责任。

## 3）项目实战：主代码片段

**启动类与 Mapper 扫描**：

```7:14:d:\software\workspace\mybatis-plus\mybatis-plus-samples\mybatis-plus-sample-quickstart\src\main\java\com\baomidou\mybatisplus\samples\quickstart\QuickstartApplication.java
@SpringBootApplication
@MapperScan("com.baomidou.mybatisplus.samples.quickstart.mapper")
public class QuickstartApplication {

    public static void main(String[] args) {
        SpringApplication.run(QuickstartApplication.class, args);
    }

}
```

**Mapper 继承 `BaseMapper`**（零 XML 即可获得 `selectList` 等方法）：

```1:8:d:\software\workspace\mybatis-plus\mybatis-plus-samples\mybatis-plus-sample-quickstart\src\main\java\com\baomidou\mybatisplus\samples\quickstart\mapper\SysUserMapper.java
package com.baomidou.mybatisplus.samples.quickstart.mapper;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import com.baomidou.mybatisplus.samples.quickstart.entity.SysUser;

public interface SysUserMapper extends BaseMapper<SysUser> {

}
```

**验收用例**：

```12:24:d:\software\workspace\mybatis-plus\mybatis-plus-samples\mybatis-plus-sample-quickstart\src\test\java\com\baomidou\mybatisplus\samples\quickstart\QuickStartTest.java
@SpringBootTest
public class QuickStartTest {
    @Autowired
    private SysUserMapper userMapper;

    @Test
    public void testSelect() {
        System.out.println(("----- selectAll method test ------"));
        List<SysUser> userList = userMapper.selectList(null);
        Assertions.assertEquals(5, userList.size());
        userList.forEach(System.out::println);
    }
}
```

**配置**：同模块 `application.yml` 中数据源与 `spring.sql.init`（H2 内存库 + schema/data）。

**进阶（讲师可选）**：`MybatisPlusAutoConfiguration` 位于源码  
`mybatis-plus/spring-boot-starter/mybatis-plus-spring-boot3-starter/.../MybatisPlusAutoConfiguration.java`。

## 4）项目总结

| 维度 | 说明 |
|------|------|
| **优点** | 上手快；单表 CRUD 与团队规范易统一；与现有 MyBatis XML 可共存。 |
| **缺点 / 边界** | 团队需约定「何时用 MP、何时必须写 SQL」；否则易出现「Wrapper 拼出难以维护的巨 SQL」。 |
| **适用场景** | 新业务 Spring Boot 服务、以关系库为主的 CRUD 型接口。 |
| **注意事项** | `@MapperScan` 包路径；多数据源时每个 `SqlSessionFactory` 的 Mapper 集要分清。 |
| **常见踩坑** | `mapper-locations` 配错导致 XML 找不到；实体与表名不一致却未配注解导致误操作错误表；把复杂报表全塞进动态 Wrapper。 |

**课后动作**：在 `mybatis-plus-samples` 根目录执行 `mvn -pl mybatis-plus-sample-quickstart test`。详见 [LABS_CHECKLIST.md](LABS_CHECKLIST.md)。
