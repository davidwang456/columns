# 第 31 章：多模块装配与 Bean 覆盖

示例模块：`mybatis-plus-sample-assembly`。

## 1）项目背景

典型企业分层：**Web**、**Service**、**Mapper/XML** 分属多模块。常见问题：Mapper XML 在依赖 jar 中但**未被打包扫描**、`mapper-locations` 未包含 `classpath*:`、或 **Bean 重复定义**导致启动失败。

**痛点放大**：本地 IDE 能跑、线上 jar 缺 XML——多半是 **Maven 资源与路径**问题，而非 MP 本身。

**本章目标**：启动 `AssemblyApplication` 并访问 `/test`；理解多模块下 MP 配置要点；能排查「找不到 MappedStatement」类错误。

## 2）项目设计：小胖、小白与大师的对话

**小胖**：多模块不就是多几个文件夹吗？

**大师**：多的是 **classpath 边界**——XML 在哪、谁加载、谁先谁后。

**技术映射**：**多模块** ≈ 连锁分店；**mapper-locations** ≈ 总仓配送清单。

---

**小白**：为啥打包后找不到 XML？

**大师**：`resources` 未进 jar、或 `classpath*` 通配未写对。

**本章金句**：**装配问题**先看构建产物，再看运行时配置。

## 3）项目实战

**环境准备**：`mvn -pl mybatis-plus-sample-assembly -am spring-boot:run` 或运行 `AssemblyApplication`。

**步骤 1：Controller 调用链**

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

**验证方式**：HTTP 访问 `/test` 或自写集成测试。

**完整代码清单**：`mybatis-plus-samples/mybatis-plus-sample-assembly/`

## 4）项目总结

| 优点 | 缺点 / 边界 |
|------|-------------|
| 分层清晰 | 装配复杂度高 |
| 贴近企业结构 | 排查路径长 |

**适用场景**：中大型单体或多模块微服务基础工程。

**不适用场景**：单模块小服务（可简化）。

**注意事项**：`spring-boot-maven-plugin` 打包；`mapper-locations` 使用 `classpath*:`。

**常见踩坑（案例化）**

1. **现象**：`Invalid bound statement`。**根因**：XML 未进 classpath。**处理**：检查 `resources` 与打包。
2. **现象**：重复 Bean。**根因**：多模块 `@MapperScan` 重叠。**处理**：收敛扫描包。
3. **现象**：依赖版本不一致。**根因**：子模块未继承 BOM。**处理**：父 POM 管理。

**思考题**

1. `classpath*` 与 `classpath` 在扫描 Mapper XML 时的差异？
2. 多数据源下 assembly 模式如何复制？

**课后动作**：[LABS_CHECKLIST.md](LABS_CHECKLIST.md) 第 31 章。
