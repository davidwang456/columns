# 第 8 章：乐观锁、逻辑删除与字段自动填充

示例模块：`mybatis-plus-sample-optimistic-locker`、`mybatis-plus-sample-logic-delete`、`mybatis-plus-sample-auto-fill-metainfo`。

## 1）项目背景

- **乐观锁**：并发更新时防止「后写覆盖先写」；通常配合 `@Version` 与更新时版本校验。
- **逻辑删除**：「删」变为更新删除标记列；影响唯一索引、关联查询与报表。
- **自动填充**：创建人、创建时间、更新人等审计字段在插入/更新时统一填充，减少重复 `set`。
- **本章目标**：能配置三类插件/处理器；知道「客户端回传版本号」「逻辑删下手写 SQL」等典型坑。

## 2）项目设计：大师与小白的对话

**小白**：逻辑删不就是改个字段吗？

**大师**：唯一约束、外键语义、历史报表、运维导数都要跟着变；否则会出现「以为删了其实还能查到」的错觉。

**小白**：乐观锁版本号我忘了带回去行不行？

**大师**：那更新条件对不上，影响行数为 0，业务上要当作冲突处理，而不是静默成功。

**小白**：自动填充能从 Session 里取当前用户吗？

**大师**：可以，但要清楚线程模型；误用静态变量或错误的 ThreadLocal 生命周期会串数据。

**本章金句**：三类能力都是**横切规则**，统一配置比每个接口复制粘贴可靠。

## 3）项目实战：主代码片段

**乐观锁更新成功与版本递增**：

```25:42:d:\software\workspace\mybatis-plus\mybatis-plus-samples\mybatis-plus-sample-optimistic-locker\src\test\java\com\baomidou\mybatisplus\samples\optlocker\OptLockerTest.java
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

**逻辑删除多种入口**（节选）：

```31:50:d:\software\workspace\mybatis-plus\mybatis-plus-samples\mybatis-plus-sample-logic-delete\src\test\java\com\baomidou\mybatisplus\samples\logic\LogicDeleteTest.java
    @Test
    public void tCommon() {
        List<Long> ids = new ArrayList<>();
        for (int i = 0; i < 20; i++) {
            Common common = new Common().setName("" + i);
            commonMapper.insert(common);
            ids.add(common.getId());
        }
        log.info("------------------------------------------------deleteById--------------------------------------------------------");
        commonMapper.deleteById(ids.remove(0));
        log.info("------------------------------------------------deleteByMap--------------------------------------------------------");
        commonMapper.deleteByMap(Maps.newHashMap("id", ids.remove(0)));
        log.info("------------------------------------------------delete--------------------------------------------------------");
        commonMapper.delete(Wrappers.<Common>query().eq("id", ids.remove(0)));
        log.info("------------------------------------------------deleteBatchIds--------------------------------------------------------");
        commonMapper.deleteBatchIds(Arrays.asList(ids.remove(0), ids.remove(0)));
        log.info("------------------------------------------------updateById--------------------------------------------------------");
        commonMapper.updateById(new Common().setId(ids.remove(0)).setName("老王"));
        log.info("------------------------------------------------update--------------------------------------------------------");
        commonMapper.update(new Common().setName("老王"), Wrappers.<Common>update().eq("id", ids.remove(0)));
```

**自动填充**：

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

## 4）项目总结

| 维度 | 说明 |
|------|------|
| **优点** | 企业级横切能力开箱即用；与 Wrapper、Service 批量方法可组合。 |
| **缺点 / 边界** | 逻辑删改变「删除」语义；乐观锁需业务定义冲突处理；填充器错误难排查。 |
| **适用场景** | 后台 CRUD、审计字段、并发写同一行的业务。 |
| **注意事项** | 插件顺序；与原生 SQL/XML 混用时是否绕过插件逻辑。 |
| **常见踩坑** | 更新未带 `version`；逻辑删字段与唯一索引设计冲突；填充器取用户上下文为 null；报表直连库忽略删除标记。 |

**课后动作**：三个模块分别 `mvn -pl … test`。详见 [LABS_CHECKLIST.md](LABS_CHECKLIST.md)。
