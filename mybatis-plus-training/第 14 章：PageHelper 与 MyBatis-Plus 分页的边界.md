# 第 14 章：PageHelper 与 MyBatis-Plus 分页的边界

示例模块：`mybatis-plus-sample-pagehelper`（**选修**）。

## 1）项目背景

老项目普遍使用 [PageHelper](https://github.com/pagehelper/Mybatis-PageHelper)；迁移 MP 分页时会出现**两套分页 API、两套拦截逻辑**。MP 与 PageHelper 都依赖 SQL 解析，历史上存在 **jsqlparser 版本冲突** 等集成成本。团队必须明确：**默认分页方案只有一种**，共存只是迁移策略。

**痛点放大**：同一查询链上若既 `PageHelper.startPage` 又传 MP `Page`，可能出现 SQL 被改两次、count 翻倍、依赖冲突随机红。

**本章目标**：读懂示例中「注释掉 MP 分页、启用 PageHelper」的意图；能书面约定团队分页标准；了解与 MP 插件混用的风险。

## 2）项目设计：小胖、小白与大师的对话

**小胖**：两个都配上，总有一个能用吧？

**大师**：那是**双倍拦截**——排障时你不知道谁改的 SQL。

**技术映射**：**PageHelper** ≈ 老派分页栏；**MP PaginationInnerInterceptor** ≈ 新派一体分页。

---

**小白**：新接口 MP、老接口 PageHelper，能阶段性共存吗？

**大师**：可以，但要**划清包/模块边界**，并锁定 jsqlparser 等传递依赖版本。

**本章金句**：共存是**迁移策略**，收敛为一种才是目标。

## 3）项目实战

**环境准备**：`mvn -pl mybatis-plus-sample-pagehelper -am test`。

**步骤 1：阅读 `PagehelperTest`**

```22:51:d:\software\workspace\mybatis-plus\mybatis-plus-samples\mybatis-plus-sample-pagehelper\src\test\java\com\baomidou\mybatisplus\samples\pagehelper\PagehelperTest.java
@SpringBootTest
class PagehelperTest {
    @Autowired
    private UserMapper mapper;

    // mp 与 pagehelper 存在依赖 jsqlparser 冲突，不建议混用

    @Test
    void test() {
//        Page<User> mpPage = mapper.selectPage(new Page<>(1, 2), Wrappers.<User>query().eq("id", 1));
//        assertThat(mpPage.getTotal()).isEqualTo(1L);
//        List<User> records = mpPage.getRecords();
//        assertThat(records).isNotEmpty();
//        assertThat(records.size()).isEqualTo(1);

        // pagehelper
        PageInfo<User> info = PageHelper.startPage(1, 2).doSelectPageInfo(() -> mapper.selectById(1));
        assertThat(info.getTotal()).isEqualTo(1L);
        List<User> list = info.getList();
        assertThat(list).isNotEmpty();
        assertThat(list.size()).isEqualTo(1);
    }
```

**步骤 2**：阅读同模块中与防全表删除等相关测试（若有），与第 33 章呼应。

**验证命令**：`mvn -pl mybatis-plus-sample-pagehelper -am test`

**完整代码清单**：`mybatis-plus-samples/mybatis-plus-sample-pagehelper/`

## 4）项目总结

| 优点 | 缺点 / 边界 |
|------|-------------|
| PageHelper 对老代码侵入小 | 与 MP 混用复杂度高 |
| MP 分页与租户等插件一体化好 | 需统一版本矩阵 |

**适用场景**：迁移期短期共存；最终应统一。

**不适用场景**：已全量 MP 且无历史 PageHelper（本章可跳过）。

**注意事项**：`pom` 中 jsqlparser、pagehelper 与 MP 版本；升级前集成测试。

**常见踩坑（案例化）**

1. **现象**：同一方法两种分页。**根因**：复制粘贴遗留。**处理**：CR 禁止。
2. **现象**：构建随机红。**根因**：传递依赖冲突。**处理**：`dependencyManagement` 锁定。
3. **现象**：分页 total 不对。**根因**：拦截器顺序。**处理**：只保留一套分页。

**思考题**

1. 若必须共存，如何用包结构隔离两套分页入口？
2. PageHelper 的线程变量与异步任务结合时有何风险？

**课后动作**：[LABS_CHECKLIST.md](LABS_CHECKLIST.md) 第 14 章（选做）。
