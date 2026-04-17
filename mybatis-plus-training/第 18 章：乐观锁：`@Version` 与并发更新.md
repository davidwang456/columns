# 第 18 章：乐观锁：`@Version` 与并发更新

示例模块：`mybatis-plus-sample-optimistic-locker`。

## 1）项目背景

并发更新同一行时，**后写覆盖先写**会导致静默丢更新。乐观锁通过 **`@Version`** 字段，在 UPDATE 时校验版本号并递增，使冲突可检测。MP 与插件配合，将版本条件注入 `updateById` 等路径。

**痛点放大**：若前端不回传版本号、或更新路径绕过 MP 走原生 SQL，乐观锁**形同虚设**；业务需定义**冲突时重试还是提示用户**。

**本章目标**：配置乐观锁插件；跑通 `OptLockerTest` 成功/失败分支；理解影响行数为 0 时的语义。

## 2）项目设计：小胖、小白与大师的对话

**小胖**：加锁为啥叫「乐观」？

**大师**：先假设**很少冲突**，冲突再重试；与数据库悲观锁（`SELECT FOR UPDATE`）相对。

**技术映射**：**version 列** ≈ 文章修订号；对不上就拒绝覆盖。

---

**小白**：失败时为啥不抛异常？

**大师**：由**更新影响行数**表达；是否转异常是业务策略。

**本章金句**：乐观锁解决**检测**，不自动解决**业务合并**。

## 3）项目实战

**环境准备**：`mvn -pl mybatis-plus-sample-optimistic-locker -am test`。

**步骤 1：更新成功与版本递增**

```25:50:d:\software\workspace\mybatis-plus\mybatis-plus-samples\mybatis-plus-sample-optimistic-locker\src\test\java\com\baomidou\mybatisplus\samples\optlocker\OptLockerTest.java
    @Order(0)
    @Test
    public void testUpdateByIdSucc() {
        User user = new User();
        user.setAge(18);
        user.setEmail("test@baomidou.com");
        user.setName("optlocker");
        user.setVersion(1);
        userMapper.insert(user);
        Long id = user.getId();

        User userUpdate = new User();
        userUpdate.setId(id);
        userUpdate.setAge(19);
        userUpdate.setVersion(1);
        assertThat(userMapper.updateById(userUpdate)).isEqualTo(1);
        assertThat(userUpdate.getVersion()).isEqualTo(2);
    }
```

**步骤 2**：继续阅读同文件失败用例（版本不匹配）。

**验证命令**：`mvn -pl mybatis-plus-sample-optimistic-locker -am test`

**完整代码清单**：`mybatis-plus-samples/mybatis-plus-sample-optimistic-locker/`

## 4）项目总结

| 优点 | 缺点 / 边界 |
|------|-------------|
| 无长事务锁表 | 高冲突场景重试成本高 |
| 与 MP 更新路径集成 | 原生 SQL 需手写版本条件 |

**适用场景**：后台编辑、购物车数量变更等读多写少冲突。

**不适用场景**：强一致金融转账（需事务与隔离级别方案）。

**注意事项**：与第 19 章逻辑删、第 33 章全表更新防护的关系。

**常见踩坑（案例化）**

1. **现象**：版本永远不增。**根因**：未注册乐观锁插件。**处理**：检查 `MybatisPlusInterceptor`。
2. **现象**：DTO 丢版本字段。**根因**：前端不传。**处理**：接口契约。
3. **现象**：批量更新部分成功。**根因**：未逐条处理冲突。**处理**：批量 API 策略。

**思考题**

1. 乐观锁与 `UPDATE … WHERE id=? AND version=?` 手写有何差异？
2. 高冲突下为何考虑悲观锁或队列？

**课后动作**：[LABS_CHECKLIST.md](LABS_CHECKLIST.md) 第 18 章。
