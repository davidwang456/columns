# 第 13 章：排序、自定义 XML 分页与 count 优化意识

示例模块：`mybatis-plus-sample-pagination`（与第 12 章同一模块，本章侧重 `OrderItem`、XML 自定义分页、join 场景下的 count 行为）。

## 1）项目背景

接上章：列表接口除了「第几页、每页几条」，还几乎总有**按创建时间、按金额、按优先级**排序；部分场景还要**连表**查子单或标签。MP 内置分页会同时生成**列表 SQL**与 **count SQL**；一旦 SQL 里出现 **left join** 且 where 里混用了驱动表与被驱动表条件，优化器对 count 的路径可能与预期不同——表现为**count 慢**或**count 结果与列表筛选语义不一致**。

**痛点放大**：若团队不了解 `Page` 上 `addOrder` 与 Wrapper 里 `orderBy` 的分工，会出现「排序重复」或「漏排序」；若把复杂 join 全塞进动态 Wrapper，又失去 XML 的可读性与 DBA 评审入口。本章在**同一示例模块**中展示：**显式排序**、**自定义 XML 分页**、以及**注释里写清的 count 优化分支**（见 `PaginationTest#tests2`）。

**本章目标**：会用 `OrderItem` 与 Wrapper 组合排序；能读自定义 `MyPage` + Mapper XML 的分页写法；建立「join + count」场景的优化意识。

## 2）项目设计：小胖、小白与大师的对话

**小胖**：排序不就是 SQL 里加个 `ORDER BY` 吗？我在 Java 里 `Collections.sort` 不行吗？

**大师**：数据库排序可以利用**索引**；数据一大，内存排序既慢又占堆。业务上优先让**数据库完成 order by**，分页插件再套 limit。

**技术映射**：**OrderBy 下推** ≈ 让厨房按窗口顺序出菜，而不是端到桌上再排队。

---

**小白**：`page.addOrder(OrderItem.asc("age"))` 和 `lambdaQuery().orderByAsc(User::getAge)` 会冲突吗？

**大师**：两者都是往分页上下文里加排序项，**职责重叠时要统一风格**——小团队建议以 **Wrapper/Lambda 一条链**为主，避免同一请求里两处各加一遍。

**小白**：自定义 XML 分页和 `selectPage` 有啥区别？

**大师**：`selectPage` 走 MP 注入的单表/Wrapper SQL；**自定义 XML** 适合复杂 where、连表、数据库函数——仍然把 `Page` 或自定义 `MyPage` 作为参数传入，由插件改写 boundSql。

---

**小胖**：注释里写「下面的 left join 不会对 count 进行优化」是啥意思？

**大师**：当 **where 里用到了 join 表字段** 时，count 往往必须保留 join，优化空间小；若 where 只约束驱动表，插件有机会**简化 count SQL**。这是**执行计划**层面的事，需要结合 `EXPLAIN` 与业务条件一起看。

**本章金句**：分页不止「切页」，**排序与 count 同源**，join 场景要单独审一遍 count。

## 3）项目实战

**环境准备**：同第 12 章；命令 `mvn -pl mybatis-plus-sample-pagination -am test`。

**步骤 1：BaseMapper 分页 + 显式排序**

目标：`Page` 上 `addOrder`，再带条件查询。

同模块 `PaginationTest#tests1` 中：`page.addOrder(OrderItem.asc("age"))` 与 `mapper.selectPage(page, Wrappers...)` 配合；并演示 `Page` 的 JSON 序列化反序列化（前后端传参场景）。

**步骤 2：自定义 XML 分页**

目标：使用 `MyPage` 与 Mapper XML 中声明的 `mySelectPage`，参数对象 `ParamSome` 传业务条件。

见 `PaginationTest#tests1` 中「自定义 XML 分页」日志段；运行后日志打印总条数、当前页、每页条数。

**步骤 3：join 场景下 count 优化对比**

目标：阅读并运行 `tests2()` 中两段 `userChildrenPage` 调用，对照注释理解 **where 是否引用 join 表** 对 count 的影响。

```82:94:d:\software\workspace\mybatis-plus\mybatis-plus-samples\mybatis-plus-sample-pagination\src\test\java\com\baomidou\mybatisplus\samples\pagination\PaginationTest.java
        /* 下面的 left join 不会对 count 进行优化,因为 where 条件里有 join 的表的条件 */
        MyPage<UserChildren> myPage = new MyPage<>(1, 5);
        myPage.setSelectInt(18).setSelectStr("Jack");
        MyPage<UserChildren> userChildrenMyPage = mapper.userChildrenPage(myPage);
        List<UserChildren> records = userChildrenMyPage.getRecords();
        records.forEach(System.out::println);

        /* 下面的 left join 会对 count 进行优化,因为 where 条件里没有 join 的表的条件 */
        myPage = new MyPage<UserChildren>(1, 5).setSelectInt(18);
        userChildrenMyPage = mapper.userChildrenPage(myPage);
        records = userChildrenMyPage.getRecords();
        records.forEach(System.out::println);
```

**步骤 4：仅查当前页、不查总条数**

目标：`new Page<>(1, 3, false)` 关闭 count，适用于**无需总页数**的滚动加载等（见 `currentPageListTest`）。

**可能遇到的坑**：自定义 XML 中参数名与 `MyPage` 泛型不一致导致绑定失败；join 场景 count 与 list 语义不一致——需 DBA 协助 `EXPLAIN`。

**验证命令**：同第 12 章 `PaginationTest` 全量运行。

**完整代码清单**：`mybatis-plus-samples/mybatis-plus-sample-pagination/`。

## 4）项目总结

| 优点 | 缺点 / 边界 |
|------|-------------|
| `OrderItem` 与 Wrapper 灵活表达排序 | 多处重复写排序易混乱，需团队规范 |
| 自定义 XML 保留复杂 SQL 可读性 | XML 与 Wrapper 混用时职责要划清 |
| count 优化分支可随版本演进 | 强依赖数据库与 MP 版本，需回归测试 |

**适用场景**：需要显式列名排序（与索引对齐）；需要 XML 控制连表分页；移动端滚动加载只需「下一页」、可关 count。

**不适用场景**：要求 ORM 完全隐藏 SQL 且不允许 XML；全表导出（应换游标/流式 API）。

**注意事项**：`Page` 序列化到前端时，注意泛型擦除与 `TypeReference`（示例 `tests1` 已演示）。

**常见踩坑（案例化）**

1. **现象**：列表有数据、total 为 0。**根因**：使用了 `new Page<>(current, size, false)` 关闭 count 却仍展示总页数。**处理**：前端协议与 `searchCount` 开关对齐。
2. **现象**：count 比 list 慢一个数量级。**根因**：join 后 count 未走合适索引。**处理**：改写 where、拆查询、物化视图或缓存 count。
3. **现象**：排序在翻页后「抖」。**根因**：未指定稳定排序键（如 id）。**处理**：`orderBy` 追加唯一键。

**思考题**

1. `IPage` 与 `Page` 在自定义 XML 返回时，如何保证 `total` 被正确回填？
2. 与 PageHelper 相比，MP 分页插件在「多租户条件注入」上的协作点是什么？（答案提示：第 21、35 章。）

**课后动作**：[LABS_CHECKLIST.md](LABS_CHECKLIST.md) 第 13 章与第 12 章共用模块，建议连续完成。
