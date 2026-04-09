# 第 3 章：`BaseMapper` 与 Service 层模式（含与 ActiveRecord 对比）

示例模块：`mybatis-plus-sample-crud`；对照 `mybatis-plus-sample-active-record`。源码接口：`mybatis-plus-core/.../BaseMapper.java`。

## 1）项目背景

- DAO 层通常定义为 Mapper 接口；MP 提供 `BaseMapper<T>` 内置一批通用方法，避免重复 XML。
- 业务项目常在 Service 中组合多个 Mapper、事务、领域规则；**Mapper 保持薄**有利于单测与复用。
- **ActiveRecord（AR）**：实体继承 `Model<T>`，在实体上直接 `insert`/`updateById`，适合小 demo，企业项目需权衡可读性与分层。

## 2）项目设计：大师与小白的对话

**小白**：有了 `BaseMapper`，还要 Service 吗？

**大师**：要。事务边界、跨表流程、调用外部接口，放在 Service；Mapper 只做持久化。

**小白**：我看到有人让实体 `insert()`，好酷。

**大师**：那是 AR。爽在 demo，痛在「业务逻辑散落在实体里」和「大对象图难测」。团队项目更推荐 **Mapper + ServiceImpl**（后续可结合 `IService`/`ServiceImpl`）。

**小白**：`BaseMapper` 方法从哪来的？

**大师**：MP 在启动期注入 SQL 与实现，接口层你看到的就是契约；好奇可打开源码里的 `BaseMapper` 看方法列表（不要求背）。

**本章金句**：`BaseMapper` 解决重复 DAO；Service 解决重复业务。

## 3）项目实战：主代码片段

**Mapper 继承 `BaseMapper`（无 XML）**：

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

**典型 CRUD**（摘自 `CrudTest`）：

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

**AR 对照**（须实现 `pkVal()`）：

```20:37:d:\software\workspace\mybatis-plus\mybatis-plus-samples\mybatis-plus-sample-active-record\src\main\java\com\baomidou\mybatisplus\samples\ar\entity\User.java
@EqualsAndHashCode(callSuper = true)
@Data
@Accessors(chain = true)
@TableName("sys_user")
public class User extends Model<User> {
    private Long id;
    private String name;
    private Integer age;
    private String email;

    @Override
    public Serializable pkVal() {
        /**
         * AR 模式这个必须有，否则 xxById 的方法都将失效！
         * 另外 UserMapper 也必须 AR 依赖该层注入，有可无 XML
         */
        return id;
    }
}
```

## 4）项目总结

| 维度 | 说明 |
|------|------|
| **优点** | `BaseMapper` 极大减少样板 DAO；与 Lambda Wrapper 组合可类型安全查询（第 4 章展开）。 |
| **缺点 / 边界** | 方法过多易让新人「啥都想用内置方法硬凑」；复杂语义仍应自定义 SQL。 |
| **适用场景** | 标准分层：接口 → Service → `BaseMapper`；工具脚本、示例可用 AR。 |
| **注意事项** | AR 必须实现 `pkVal()`；Spring 需能注入对应 `Mapper` 供 `Model` 使用。 |
| **常见踩坑** | 在 Mapper 里堆业务逻辑；AR 与贫血模型混用导致风格分裂；批量插入与主键回填策略未对齐（第 7 章）。 |

**课后动作**：分别运行 `mybatis-plus-sample-crud` 与 `mybatis-plus-sample-active-record` 的测试。详见 [LABS_CHECKLIST.md](LABS_CHECKLIST.md)。
