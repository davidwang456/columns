# 第 15 章：分布式主键：雪花算法与 `IdWorker`

示例模块：`mybatis-plus-sample-id-generator`。

## 1）项目背景

单库自增在分库分表、数据合并场景下受限。**雪花（Snowflake）类算法**可在应用侧生成趋势有序的长整型 ID，MP 通过 `IdType.ASSIGN_ID` 等与内置生成器配合。需理解**时钟回拨**、**ID 长度**与**前端精度**问题。

**痛点放大**：多实例部署若各自发号规则不一致会冲突；批量插入时若未验证回填，会出现业务关联表外键为 null。

**本章目标**：跑通 `IdGeneratorTest`；理解 `saveBatch` 与单条 `insert` 回填差异；建立与全局 `id-type` 配置的联系。

## 2）项目设计：小胖、小白与大师的对话

**小胖**：雪花比 UUID 好在哪？

**大师**：一般**更短、有序、索引友好**；UUID 随机性高，索引碎片更明显——视业务与暴露需求而定。

**技术映射**：**ASSIGN_ID** ≈ 分布式发号机；**自增** ≈ 单窗口顺序号。

---

**小白**：时钟回拨怎么办？

**大师**：依赖实现与版本；生产要 NTP 监控、必要时切换策略或引入外部发号服务。

**本章金句**：主键是**数据架构**问题，MP 只减少配置样板。

## 3）项目实战

**环境准备**：`mvn -pl mybatis-plus-sample-id-generator -am test`。

**步骤 1：插入与批量**

```24:56:d:\software\workspace\mybatis-plus\mybatis-plus-samples\mybatis-plus-sample-id-generator\src\test\java\com\baomidou\samples\IdGeneratorTest.java
    @Test
    public void test() {
        User user = new User();
        user.setName("靓仔");
        user.setAge(18);
        userMapper.insert(user);
        Assertions.assertEquals(Long.valueOf(1L), user.getId());

        testBatch();
    }

    /**
     * 批量插入
     */
    public void testBatch() {
        List<User> users = new ArrayList<>();
        for (int i = 1; i <= 10; i++) {
            User user = new User();
            user.setName("靓仔" + i);
            user.setAge(18 + i);
            users.add(user);
        }
        boolean result = userService.saveBatch(users);
        Assertions.assertEquals(true, result);
    }
```

**验证命令**：`mvn -pl mybatis-plus-sample-id-generator -am test`

**完整代码清单**：`mybatis-plus-samples/mybatis-plus-sample-id-generator/`

## 4）项目总结

| 优点 | 缺点 / 边界 |
|------|-------------|
| 分布式友好 | 时钟与运维要求 |
| 与 Service 批量配合 | 需回归升级版本 |

**适用场景**：分库分表、长整型主键的新业务。

**不适用场景**：强依赖连续自增对外暴露顺序的场景。

**注意事项**：与第 5 章全局策略、第 16 章字符串主键对照。

**常见踩坑（案例化）**

1. **现象**：插入后 id 仍 null。**根因**：驱动回填关闭。**处理**：查驱动与 MP 版本说明。
2. **现象**：前端 id 精度丢。**根因**：JSON Number。**处理**：字符串。
3. **现象**：多实例 id 冲突。**根因**：机器号未区分。**处理**：检查 workerId/datacenter 配置（若使用可配实现）。

**思考题**

1. `IdentifierGenerator` 自定义 Bean 如何接入 Spring？
2. 批量插入与单条插入在回填上的行为为何可能不同？

**课后动作**：[LABS_CHECKLIST.md](LABS_CHECKLIST.md) 第 15 章。
