# 第 6 章：`BaseMapper` 与无 XML 单表 CRUD

示例模块：`mybatis-plus-sample-crud`。

## 1）项目背景

DAO 层若每个表手写 `insert`/`update`/`selectById` 的 XML，重复度极高。`BaseMapper<T>` 在接口层提供一批通用方法，MP 在启动期注入对应 SQL，**无 XML** 即可完成典型单表操作。团队规范通常要求：**Mapper 保持薄**，复杂 SQL 再下放到 XML。

**痛点放大**：若新人「只会 `selectList(null)`」，容易在产线写出无 WHERE 的查询；若把业务规则写进 Mapper，则单测与复用都困难。

**本章目标**：熟练使用 `insert`/`delete`/`update`/`selectById`/`selectList`；理解 `QueryWrapper` 与 Lambda 写法入口（第 10～11 章展开）；能运行 `CrudTest` 全量用例。

## 2）项目设计：小胖、小白与大师的对话

**小胖**：有了 `BaseMapper`，DAO 文件是不是可以删到只剩一行 `extends`？

**大师**：**接口可以薄**，但**责任不能薄**——谁允许无 WHERE 查询、谁允许批量删，要在 Service 层说清。

**技术映射**：**BaseMapper** ≈ 标准工具箱；**业务规则** ≈ 使用说明书。

---

**小白**：方法从哪来的？

**大师**：启动期根据 `TableInfo` 注入；想深究可打开源码 `BaseMapper` 看方法列表，不必背诵。

**本章金句**：`BaseMapper` 解决重复 DAO，不解决重复业务。

## 3）项目实战

**环境准备**：`mvn -pl mybatis-plus-sample-crud -am test`。

**步骤 1：Mapper 接口**

```1:17:d:\software\workspace\mybatis-plus\mybatis-plus-samples\mybatis-plus-sample-crud\src\main\java\com\baomidou\mybatisplus\samples\crud\mapper\UserMapper.java
package com.baomidou.mybatisplus.samples.crud.mapper;


import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import com.baomidou.mybatisplus.samples.crud.entity.User;

/**
 * <p>
 * MP 支持不需要 UserMapper.xml 这个模块演示内置 CRUD 咱们就不要 XML 部分了
 * </p>
 */
public interface UserMapper extends BaseMapper<User> {

}
```

**步骤 2：典型 CRUD（`CrudTest` 节选）**

```37:52:d:\software\workspace\mybatis-plus\mybatis-plus-samples\mybatis-plus-sample-crud\src\test\java\com\baomidou\mybatisplus\samples\crud\CrudTest.java
    @Test
    public void aInsert() {
        User user = new User();
        user.setName("小羊");
        user.setAge(3);
        user.setEmail("abc@mp.com");
        assertThat(mapper.insert(user)).isGreaterThan(0);
        // 成功直接拿回写的 ID
        assertThat(user.getId()).isNotNull();
    }


    @Test
    public void bDelete() {
        assertThat(mapper.deleteById(3L)).isGreaterThan(0);
        assertThat(mapper.delete(new QueryWrapper<User>()
                .lambda().eq(User::getName, "Sandy"))).isGreaterThan(0);
    }
```

**验证命令**：`mvn -pl mybatis-plus-sample-crud -am test`

**完整代码清单**：`mybatis-plus-samples/mybatis-plus-sample-crud/`

## 4）项目总结

| 优点 | 缺点 / 边界 |
|------|-------------|
| 极大减少样板 XML | 易滥用通用方法拼复杂语义 |
| 与 Wrapper 组合灵活 | 复杂报表仍应回 XML |

**适用场景**：标准分层项目的持久化层。

**不适用场景**：希望完全无 SQL 可控性的团队（应评估是否选错技术）。

**注意事项**：批量、主键策略与第 5、15 章联动。

**常见踩坑（案例化）**

1. **现象**：`selectList(null)` 拖全表。**根因**：缺省条件未禁止。**处理**：Service 层强制 Wrapper。
2. **现象**：更新影响行数不对。**根因**：乐观锁、逻辑删条件未带。**处理**：见第 18～19 章。
3. **现象**：与 XML 同名 statement 冲突。**根因**：命名空间或 id 重复。**处理**：规范命名。

**思考题**

1. `BaseMapper` 方法与 `SqlSession` 手写 statement 在事务边界上有何异同？
2. 何时应为 Mapper 增加自定义方法而非只用内置方法？

**课后动作**：[LABS_CHECKLIST.md](LABS_CHECKLIST.md) 第 6 章。
