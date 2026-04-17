# 第 23 章：自定义 `TypeHandler`

示例模块：`mybatis-plus-sample-typehandler`。

## 1）项目背景

JDBC 类型与 Java 类型不一致时（JSON 列、几何类型、加密字段），需要 **`TypeHandler`** 完成互转。MP 常与 `@TableField(typeHandler = …)`、`autoResultMap = true` 配合，将集合、值对象持久化为单列 JSON。

**痛点放大**：忘记 `autoResultMap` 会导致查询映射不到嵌套类型；序列化库升级可能**破坏历史 JSON** 格式。

**本章目标**：阅读示例实体中 `FastjsonTypeHandler`；理解 `autoResultMap` 含义；跑通 `TypeHandler` 相关测试。

## 2）项目设计：小胖、小白与大师的对话

**小胖**：JSON 列当 String 存不行吗？

**大师**：能跑，但**改结构**时全项目字符串解析会炸；类型化 + Handler 更可维护。

**技术映射**：**TypeHandler** ≈ 海关检疫；**String** ≈ 黑箱打包。

---

**小白**：Fastjson 和 Jackson 混用会怎样？

**大师**：**格式与特性**可能不一致；团队应统一序列化栈与版本。

**本章金句**：Handler 与 **ObjectMapper 配置**要同源。

## 3）项目实战

**环境准备**：`mvn -pl mybatis-plus-sample-typehandler -am test`。

**步骤 1：实体与 `autoResultMap`**

```20:45:d:\software\workspace\mybatis-plus\mybatis-plus-samples\mybatis-plus-sample-typehandler\src\main\java\com\baomidou\mybatisplus\samples\typehandler\entity\User.java
@Data
@Accessors(chain = true)
@TableName(value = "sys_user", autoResultMap = true)
public class User {
    private Long id;
    private String name;
    private Integer age;
    private String email;
// ...
    @TableField(typeHandler = FastjsonTypeHandler.class)
    private List<Wallet> wallets;
```

**验证命令**：`mvn -pl mybatis-plus-sample-typehandler -am test`

**完整代码清单**：`mybatis-plus-samples/mybatis-plus-sample-typehandler/`

## 4）项目总结

| 优点 | 缺点 / 边界 |
|------|-------------|
| 类型安全访问 JSON 列 | 大 JSON 与索引问题 |
| 可复用 Handler | 版本升级需回归 |

**适用场景**：扩展属性、列表型弱结构字段。

**不适用场景**：强关系型应范式化拆表。

**注意事项**：`autoResultMap = true`；与第 24 章 PG JSONB 对照。

**常见踩坑（案例化）**

1. **现象**：查询 wallets 为 null。**根因**：未 `autoResultMap`。**处理**：打开或 XML resultMap。
2. **现象**：反序列化失败。**根因**：历史数据格式变。**处理**：兼容反序列化器。
3. **现象**：写入超长。**根因**：列类型与压缩。**处理**：LOB 或外部存储。

**思考题**

1. 何时应使用 `JacksonTypeHandler` 替代 `FastjsonTypeHandler`？
2. TypeHandler 与 JPA `AttributeConverter` 职责对比？

**课后动作**：[LABS_CHECKLIST.md](LABS_CHECKLIST.md) 第 23 章。
