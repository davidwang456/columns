# 第 17 章：数据库序列与 Oracle 等场景

示例模块：`mybatis-plus-sample-sequence`。

## 1）项目背景

Oracle、PostgreSQL 等常用 **数据库序列** 发号，与 MySQL 自增心智不同。MP 示例通过插入触发序列取值，并校验**步长与起始值**。迁移团队常在「应用发号」与「库序列」之间摇摆，本章建立**序列型主键**的验证方式。

**痛点放大**：序列与 `@TableId` 类型、驱动 `getGeneratedKeys` 行为不一致时，表现为**取不到 id** 或**跳号**误解。

**本章目标**：跑通 `SequenceTest`；理解断言中「从 1000 起、步长 1」的示例语义；知道与 `GlobalConfig` 中 `IKeyGenerator` 扩展的关系（参见第 3 章）。

## 2）项目设计：小胖、小白与大师的对话

**小胖**：有雪花了还要序列干嘛？

**大师**：**监管与 DBA 规范**有时要求号段在库内；或历史库已用序列，应用不能擅自改。

**技术映射**：**序列** ≈ 银行取号机；**雪花** ≈ 自带生成器。

---

**小白**：跳号是不是丢数据？

**大师**：序列**不保证连续**——回滚、失败、缓存都可能跳号；业务若强依赖连续，要另设号段表。

**本章金句**：序列语义以**数据库文档**为准，不要套用自增直觉。

## 3）项目实战

**环境准备**：`mvn -pl mybatis-plus-sample-sequence -am test`。

**步骤 1：`SequenceTest`**

```22:39:d:\software\workspace\mybatis-plus\mybatis-plus-samples\mybatis-plus-sample-sequence\src\test\java\com\baomidou\mybatisplus\samples\sequence\SequenceTest.java
    @Test
    public void testInsert() {
        User user = new User();
        user.setAge(18);
        user.setEmail("test@baomidou.com");
        user.setName("sequence");
        userMapper.insert(user);
        Long id1 = user.getId();
        System.out.println(id1);
        Assertions.assertTrue(id1 >= 1000, "sequence start with 1000");
        user = new User();
        user.setAge(19);
        user.setEmail("test2@baomidou.com");
        user.setName("sequence2");
        userMapper.insert(user);
        Long id2 = user.getId();
        Assertions.assertTrue(id2 - id1 == 1, "squence increment by 1");
    }
```

**验证命令**：`mvn -pl mybatis-plus-sample-sequence -am test`

**完整代码清单**：`mybatis-plus-samples/mybatis-plus-sample-sequence/`

## 4）项目总结

| 优点 | 缺点 / 边界 |
|------|-------------|
| 与 DBA 规范对齐 | 环境依赖重 |
| 中心化发号 | 与应用集群时钟/缓存无关 |

**适用场景**：Oracle/PG 为主；已有序列资产。

**不适用场景**：全新 MySQL-only 且已统一雪花。

**注意事项**：权限、schema、搜索路径；读写分离下序列可见性。

**常见踩坑（案例化）**

1. **现象**：id 不递增 1。**根因**：缓存步长或 RAC。**处理**：问 DBA。
2. **现象**：迁移后序列未建。**根因**：脚本遗漏。**处理**：基线迁移工具。
3. **现象**：与 `INPUT` 策略混用。**根因**：配置分裂。**处理**：统一 `IdType`。

**思考题**

1. `KeyGenerator` 与数据库原生序列在事务回滚时的行为差异？
2. 多租户下序列是否应分 schema？

**课后动作**：[LABS_CHECKLIST.md](LABS_CHECKLIST.md) 第 17 章。
