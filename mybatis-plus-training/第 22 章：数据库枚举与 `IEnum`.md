# 第 22 章：数据库枚举与 `IEnum`

示例模块：`mybatis-plus-sample-enum`。

## 1）项目背景

数据库存枚举常量（整型/字符串）与 Java `enum` 需可逆映射，便于报表与多语言。MP 支持 **`IEnum`** 等策略，将枚举与存储值解耦。重构枚举名与变更码表要团队公约，否则历史数据语义漂移。

**痛点放大**：若仅存 `name()` 字符串，重构枚举会改历史含义；若整型与表字段类型不一致，会隐性转换失败。

**本章目标**：跑通 `EnumTest`；理解多枚举字段插入与查询；知道与全局枚举处理配置的关系。

## 2）项目设计：小胖、小白与大师的对话

**小胖**：枚举存字符串多直观。

**大师**：直观的是**人**；报表与对账要的是**稳定码**。选存储形态要联合 DBA。

**技术映射**：**IEnum** ≈ 码表翻译器；**ordinal** ≈ 座位号（易碎）。

---

**小白**：能改已上线枚举的 code 吗？

**大师**：等于**改历史数据含义**——要迁移脚本与兼容期。

**本章金句**：**库内表示**与 **Java 枚举**要在团队层面对齐。

## 3）项目实战

**环境准备**：`mvn -pl mybatis-plus-sample-enum -am test`。

**步骤 1：`EnumTest#insert`**

```37:45:d:\software\workspace\mybatis-plus\mybatis-plus-samples\mybatis-plus-sample-enum\src\test\java\com\baomidou\mybatisplus\samples\enums\EnumTest.java
    @Test
    public void insert() {
        User user = new User();
        user.setName("K神");
        user.setAge(AgeEnum.ONE);
        user.setGrade(GradeEnum.HIGH);
        user.setGender(GenderEnum.MALE);
        user.setStrEnum(StrEnum.ONE);
        user.setEmail("abc@mp.com");
        Assertions.assertTrue(mapper.insert(user) > 0);
        // 成功直接拿回写的 ID
        System.err.println("\n插入成功 ID 为：" + user.getId());
```

**验证命令**：`mvn -pl mybatis-plus-sample-enum -am test`

**完整代码清单**：`mybatis-plus-samples/mybatis-plus-sample-enum/`

## 4）项目总结

| 优点 | 缺点 / 边界 |
|------|-------------|
| 声明式映射 | 码表变更影响面大 |
| 与实体字段类型一致 | 历史脏数据难映射 |

**适用场景**：状态类字段、固定码表。

**不适用场景**：高频变更的配置项（应独立配置表）。

**注意事项**：与第 23 章 `TypeHandler`、全局枚举扫描配置。

**常见踩坑（案例化）**

1. **现象**：查询抛反序列化异常。**根因**：库内值无对应枚举。**处理**：兼容层或清洗数据。
2. **现象**：不同服务枚举版本不一致。**根因**：jar 未对齐。**处理**：共享枚举模块。
3. **现象**：报表展示 code 而非 label。**根因**：仅存 code。**处理**：视图或应用层翻译。

**思考题**

1. `IEnum` 与 MyBatis 原生 `EnumTypeHandler` 如何取舍？
2. 枚举与字典表二选一的边界？

**课后动作**：[LABS_CHECKLIST.md](LABS_CHECKLIST.md) 第 22 章。
