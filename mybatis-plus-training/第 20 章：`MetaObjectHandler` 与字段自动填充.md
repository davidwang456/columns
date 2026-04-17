# 第 20 章：`MetaObjectHandler` 与字段自动填充

示例模块：`mybatis-plus-sample-auto-fill-metainfo`。

## 1）项目背景

创建人、创建时间、更新人、更新时间等审计字段若在每处 `set`，重复且易漏。**自动填充**在插入/更新时由 `MetaObjectHandler` 统一写入，常与 **Security 上下文**或 **操作员 ID** 结合。

**痛点放大**：填充器取用户上下文为 null、或错误使用 ThreadLocal，会导致**串写操作人**；与多租户、逻辑删组合时要明确**填充时机**。

**本章目标**：跑通 `AutoFillTest`；能阅读模块内 `MetaObjectHandler` 实现；知道 `FieldFill` 与实体注解的配合。

## 2）项目设计：小胖、小白与大师的对话

**小胖**：我在每个接口里 `setCreateTime` 不行吗？

**大师**：行，直到有人忘在**某一个**入口，审计就穿帮。

**技术映射**：**MetaObjectHandler** ≈ 门卫登记；**业务 set** ≈ 每人自己填登记表。

---

**小白**：异步线程里操作人是谁？

**大师**：**上下文传递**要显式封装（如 `DelegatingSecurityContextRunnable`），否则填充为 null 或错人。

**本章金句**：自动填充信的是**线程内上下文**，不是魔法。

## 3）项目实战

**环境准备**：`mvn -pl mybatis-plus-sample-auto-fill-metainfo -am test`。

**步骤 1：`AutoFillTest`**

```25:35:d:\software\workspace\mybatis-plus\mybatis-plus-samples\mybatis-plus-sample-auto-fill-metainfo\src\test\java\com\baomidou\samples\metainfo\AutoFillTest.java
    @Test
    public void test() {
        User user = new User(null, "Tom", 1, "tom@qq.com", null);
        userMapper.insert(user);
        log.info("query user:{}", userMapper.selectById(user.getId()));
        User beforeUser = userMapper.selectById(1L);
        log.info("before user:{}", beforeUser);
        beforeUser.setAge(12);
        userMapper.updateById(beforeUser);
        log.info("query user:{}", userMapper.selectById(1L));
    }
```

**步骤 2**：阅读同模块 `MetaObjectHandler` 实现类与实体 `@TableField(fill = ...)`。

**验证命令**：`mvn -pl mybatis-plus-sample-auto-fill-metainfo -am test`

**完整代码清单**：`mybatis-plus-samples/mybatis-plus-sample-auto-fill-metainfo/`

## 4）项目总结

| 优点 | 缺点 / 边界 |
|------|-------------|
| 审计一致 | 上下文缺失难排查 |
| 与插件体系一致 | 与原生 SQL 混用需自觉 |

**适用场景**：统一审计字段的企业项目。

**不适用场景**：无登录态的批处理任务（需单独策略）。

**注意事项**：与 Kotlin 示例 `ApplicationTests` 中断言填充字段对照（第 37 章）。

**常见踩坑（案例化）**

1. **现象**：创建人为 null。**根因**：异步未传上下文。**处理**：任务参数显式带操作人。
2. **现象**：更新时间不刷新。**根因**：`FieldFill` 策略与 `update` 路径不匹配。**处理**：查文档与版本。
3. **现象**：单元测试全 null。**根因**：未 mock Security。**处理**：测试夹具。

**思考题**

1. 填充器与数据库 `DEFAULT CURRENT_TIMESTAMP` 如何二选一？
2. 为何批量 `saveBatch` 时填充行为需单独验证？

**课后动作**：[LABS_CHECKLIST.md](LABS_CHECKLIST.md) 第 20 章。
