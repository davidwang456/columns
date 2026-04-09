# 第 11 章：动态表名、SQL 注入器与自定义 Mapper 方法

示例模块：`mybatis-plus-sample-dynamic-tablename`、`mybatis-plus-sample-sql-injector`。

## 1）项目背景

- **动态表名**：分表、按年归档、多租户分库等场景需要在运行时切换逻辑表名，仍希望复用同一套 Mapper 方法。
- **SQL 注入器**：在 MP 内置方法之外，为所有或部分实体 **批量注入** 自定义 `AbstractMethod`（如 `findOne`、`deleteAll`、选装批量插入等）。
- **本章目标**：理解动态表名应配合**白名单或明确规则**；能阅读 `MySqlInjector` 如何扩展 `DefaultSqlInjector`；区分「表名注入」与「SQL 注入攻击」两个概念。

## 2）项目设计：大师与小白的对话

**小白**：动态表名就是字符串拼到 SQL 里。

**大师**：拼的是**标识符**不是**值**；必须有规则约束，否则等于给误用和攻击留口子。

**小白**：注入器和 Spring 的 `@Bean` 注入啥关系？

**大师**：这里的注入是 **MyBatis 映射语句注入**——给 Mapper 接口多挂几条 MP 生成的 SQL，不是 IoC 注入。

**小白**：自定义方法会和内置方法重名吗？

**大师**：会，所以要有命名规范与 Code Review，避免覆盖或语义冲突。

**本章金句**：动态表名管的是**访问哪张表**；参数绑定管的是**行级数据**——两件事别混谈「防注入」。

## 3）项目实战：主代码片段

**运行时切换表名（配合 ThreadLocal 上下文）**：

```30:45:d:\software\workspace\mybatis-plus\mybatis-plus-samples\mybatis-plus-sample-dynamic-tablename\src\test\java\com\baomidou\mybatisplus\samples\dytablename\DynamicTableNameTest.java
    @Test
    void test() {
        try {
            RequestDataHelper.setRequestData(new HashMap<String, Object>() {{
                put("id", 123);
                put("hello", "tomcat");
                put("name", "汤姆凯特");
            }});
            // 自己去观察打印 SQL 目前随机访问 user_2018  user_2019 表
            for (int i = 0; i < 6; i++) {
                User user = userMapper.selectById(1);
                LOGGER.info("userName:{}", user.getName());
            }
        } finally {
            RequestDataHelper.removeRequestData();
        }
    }
```

**自定义 `SqlInjector` 注册方法**：

```20:37:d:\software\workspace\mybatis-plus\mybatis-plus-samples\mybatis-plus-sample-sql-injector\src\main\java\com\baomidou\samples\injector\base\MySqlInjector.java
public class MySqlInjector extends DefaultSqlInjector {

    @Override
    public List<AbstractMethod> getMethodList(Class<?> mapperClass, TableInfo tableInfo) {
        List<AbstractMethod> methodList = super.getMethodList(mapperClass, tableInfo);
        //增加自定义方法
        methodList.add(new DeleteAll("deleteAll"));
        methodList.add(new FindOne("findOne"));
        /**
         * 以下 3 个为内置选装件
         * 头 2 个支持字段筛选函数
         */
        // 例: 不要指定了 update 填充的字段
        methodList.add(new InsertBatchSomeColumn(i -> i.getFieldFill() != FieldFill.UPDATE));
        methodList.add(new AlwaysUpdateSomeColumnById());
        methodList.add(new LogicDeleteByIdWithFill());
        return methodList;
    }
}
```

**Mapper 基接口**（`MyBaseMapper` 聚合选装件与链式 Wrapper）：见同模块 `MyBaseMapper.java`。

## 4）项目总结

| 维度 | 说明 |
|------|------|
| **优点** | 动态表名减少重复 Mapper；注入器适合团队级方法复用与规范落地。 |
| **缺点 / 边界** | 动态表名调试难；注入器方法过多会让接口「臃肿」。 |
| **适用场景** | 明确分表规则；需要 `findOne`、`批量插入列裁剪` 等横切能力。 |
| **注意事项** | 动态表名解析器内做校验；全局只配置一份 `SqlInjector` Bean，避免重复注入。 |
| **常见踩坑** | ThreadLocal 未清理导致串表；多数据源下动态表名上下文错乱；方法名与内置冲突。 |

**课后动作**：`mvn -pl mybatis-plus-sample-dynamic-tablename,mybatis-plus-sample-sql-injector test`。详见 [LABS_CHECKLIST.md](LABS_CHECKLIST.md)。
