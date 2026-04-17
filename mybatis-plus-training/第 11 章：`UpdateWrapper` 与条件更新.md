# 第 11 章：`UpdateWrapper` 与条件更新

示例模块：`mybatis-plus-sample-wrapper`（`UpdateWrapperTest`）。

## 1）项目背景

更新场景常分两类：**带实体对象**（只更新非 null 字段策略因版本而异）与 **纯条件更新**（`set` 列与 `where` 分离）。`UpdateWrapper` / `LambdaUpdateWrapper` 支持「不建实体、只改两列」「按表达式更新」等写法，减少先查再改的往返。

**痛点放大**：误用空实体 + 空条件会导致**全表更新**风险（与第 33 章拦截策略联动）；`setSql` 拼接需防注入。

**本章目标**：掌握 `update(entity, wrapper)` 与 `update(null, wrapper)` 两种模式；跑通 `UpdateWrapperTest`。

## 2）项目设计：小胖、小白与大师的对话

**小胖**：更新不就是 `set name=? where id=?` 吗？

**大师**：业务上常要 `set status=2 where id in (…)` 且**不带实体**，这时 Wrapper 更直接。

**技术映射**：**UpdateWrapper** ≈ 带闸门的调温；**实体 update** ≈ 整对象替换若干字段。

---

**小白**：`update(null, wrapper)` 空实体安全吗？

**大师**：**WHERE 必须可信**；空条件会被框架拦截或酿成事故——团队要有规范与测试。

**本章金句**：更新先问 **WHERE 是否足够窄**。

## 3）项目实战

**环境准备**：`mvn -pl mybatis-plus-sample-wrapper -am test`。

**步骤 1：`UpdateWrapperTest`**

```24:38:d:\software\workspace\mybatis-plus\mybatis-plus-samples\mybatis-plus-sample-wrapper\src\test\java\com\baomidou\mybatisplus\samples\wrapper\UpdateWrapperTest.java
    @Test
    public void tests() {

        //方式一：
        User user = new User();
        user.setAge(29);
        user.setEmail("test3update@baomidou.com");

        userMapper.update(user,new UpdateWrapper<User>().eq("name","Tom"));

        //方式二：
        //不创建User对象
        userMapper.update(null,new UpdateWrapper<User>()
                .set("age",29).set("email","test3update@baomidou.com").eq("name","Tom"));

    }
```

**验证命令**：`mvn -pl mybatis-plus-sample-wrapper -am test`

**完整代码清单**：`mybatis-plus-samples/mybatis-plus-sample-wrapper/`

## 4）项目总结

| 优点 | 缺点 / 边界 |
|------|-------------|
| 灵活表达 SET/WHERE | 复杂链难读 |
| 支持列表达式 | 需防 SQL 注入 |

**适用场景**：状态流转、批量条件更新、无实体补丁更新。

**不适用场景**：大字段 JSON 合并等需数据库函数时优先 XML。

**注意事项**：乐观锁、填充字段与 `updateById` 差异。

**常见踩坑（案例化）**

1. **现象**：更新 0 行。**根因**：WHERE 无匹配或版本不对。**处理**：断言影响行数。
2. **现象**：误更新多行。**根因**：`eq` 条件过宽。**处理**：先 `select count` 或加唯一键。
3. **现象**：`setSql` 注入。**根因**：拼接用户输入。**处理**：参数化。

**思考题**

1. `LambdaUpdateWrapper` 在哪些场景比字符串列名更安全？
2. 与 `ServiceImpl.update(Wrapper)` 组合时的事务边界？

**课后动作**：[LABS_CHECKLIST.md](LABS_CHECKLIST.md) 第 11 章。
