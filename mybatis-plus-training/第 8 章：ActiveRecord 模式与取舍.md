# 第 8 章：ActiveRecord 模式与取舍

示例模块：`mybatis-plus-sample-active-record`（**选修**）。

## 1）项目背景

**ActiveRecord（AR）** 让实体继承 `Model<T>`，在实体上直接调用 `insert`/`updateById` 等，省去显式 Mapper 调用。小项目或 demo 很顺手；企业项目常面临**领域逻辑是否应进实体**的争议。

**痛点放大**：AR 与**贫血模型 + Service** 混用时，风格分裂、Code Review 困难；`pkVal()` 遗漏会导致按主键操作失效。

**本章目标**：能跑通 AR 示例；能向团队说明「何时禁用 AR」；理解 `Model` 与 `Mapper` 的协作关系。

## 2）项目设计：小胖、小白与大师的对话

**小胖**：实体上点 `insert()` 多酷，为啥企业不用？

**大师**：酷的是**写法**；痛的是**可测性**与**职责边界**——业务规则塞进实体，单测要起 Spring 或 mock 静态依赖。

**技术映射**：**AR** ≈ 自助结账机；**分层 Service** ≈ 收银员处理异常与促销规则。

---

**小白**：AR 还需要 Mapper 吗？

**大师**：需要；`Model` 内部仍走 `Mapper` 注入，Spring 要能把 Mapper 交给 AR 使用。

**本章金句**：AR 适合**教学与脚手架**，生产要团队公约。

## 3）项目实战

**环境准备**：`mvn -pl mybatis-plus-sample-active-record -am test`。

**步骤 1：实体继承 `Model`**

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

**步骤 2**：阅读 `ActiveRecordTest` 并运行。

**验证命令**：`mvn -pl mybatis-plus-sample-active-record -am test`

**完整代码清单**：`mybatis-plus-samples/mybatis-plus-sample-active-record/`

## 4）项目总结

| 优点 | 缺点 / 边界 |
|------|-------------|
| 写法紧凑 | 领域逻辑易污染实体 |
| 适合 demo | 大型团队难统一风格 |

**适用场景**：内部工具、示例、个人项目。

**不适用场景**：强 DDD、严格分层审计的金融核心。

**注意事项**：必须实现 `pkVal()`；与 Spring 上下文关系。

**常见踩坑（案例化）**

1. **现象**：`updateById` 无效。**根因**：`pkVal` 返回 null。**处理**：保证 id 已设。
2. **现象**：单元测起不来。**根因**：AR 依赖容器注入 Mapper。**处理**：`@SpringBootTest` 或重构为非 AR。
3. **现象**：与 `BaseMapper` 混用风格冲突。**根因**：无公约。**处理**：模块级统一。

**思考题**

1. AR 与 `ServiceImpl` 在事务边界声明上有何差异？
2. 若必须 AR，如何降低实体中的业务逻辑？

**课后动作**：[LABS_CHECKLIST.md](LABS_CHECKLIST.md) 第 8 章（选做）。
