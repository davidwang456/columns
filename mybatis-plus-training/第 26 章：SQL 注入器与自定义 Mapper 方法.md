# 第 26 章：SQL 注入器与自定义 Mapper 方法

示例模块：`mybatis-plus-sample-sql-injector`。

## 1）项目背景

`BaseMapper` 内置方法不满足团队规范时，可通过 **`SqlInjector`** 向 Mapper **批量注入**自定义 `AbstractMethod`（如 `deleteAll`、`findOne`、选装批量插入）。此处「注入」是 **MyBatis 映射语句注入**，不是 Spring IoC。

**痛点放大**：方法名与内置冲突、或全局重复注册 `SqlInjector` Bean，会导致**启动失败**或**语义覆盖**。

**本章目标**：阅读 `MySqlInjector` 扩展 `DefaultSqlInjector`；理解 `MyBaseMapper` 聚合选装件；跑通注入器模块测试。

## 2）项目设计：小胖、小白与大师的对话

**小胖**：注入器和 Spring `@Autowired` 注入啥关系？

**大师**：完全是两件事：这里是给 **Mapper 接口多挂几条 MP 生成的 SQL**。

**技术映射**：**SqlInjector** ≈ 给工具箱加定制批头；**IoC 注入** ≈ 给工人发工具。

---

**小白**：自定义方法会和内置重名吗？

**大师**：**会**，所以要有命名规范与 Code Review。

**本章金句**：扩展的是 **MappedStatement**，不是 Spring Bean。

## 3）项目实战

**环境准备**：`mvn -pl mybatis-plus-sample-sql-injector -am test`。

**步骤 1：`MySqlInjector`**

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

**步骤 2**：阅读 `MyBaseMapper` 与 Mapper 接口声明。

**验证命令**：`mvn -pl mybatis-plus-sample-sql-injector -am test`

**完整代码清单**：`mybatis-plus-samples/mybatis-plus-sample-sql-injector/`

## 4）项目总结

| 优点 | 缺点 / 边界 |
|------|-------------|
| 团队级方法复用 | 接口方法过多 |
| 选装件灵活 | 升级 MP 需回归 |

**适用场景**：统一批量插入策略、项目级 `findOne` 约束。

**不适用场景**：仅个别 Mapper 需要时（可直接 XML）。

**注意事项**：全局单例 `SqlInjector`；与代码生成器模板协同。

**常见踩坑（案例化）**

1. **现象**：启动报方法重复。**根因**：多 `SqlInjector` Bean。**处理**：合并或 `@Primary`。
2. **现象**：生成 SQL 与方言不兼容。**根因**：选装件假设。**处理**：按库测试。
3. **现象**：新人滥用 `deleteAll`。**根因**：缺少权限封装。**处理**：服务层拦截。

**思考题**

1. `AbstractMethod` 与手写 XML 的维护成本如何权衡？
2. 选装件与插件链执行顺序关系？

**课后动作**：[LABS_CHECKLIST.md](LABS_CHECKLIST.md) 第 26 章。
