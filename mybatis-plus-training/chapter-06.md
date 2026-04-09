# 第 6 章：与 PageHelper 共存及「只选一个」的边界

示例模块：`mybatis-plus-sample-pagehelper`。

## 1）项目背景

- 老项目普遍使用 [PageHelper](https://github.com/pagehelper/Mybatis-PageHelper)；迁移 MP 分页时会出现「两套分页 API、两套拦截逻辑」。
- MP 与 PageHelper 都依赖 SQL 解析能力，历史上存在 **jsqlparser 版本冲突** 等集成成本。
- **本章目标**：理解团队应明确**默认分页方案**；能读懂本示例中「注释掉的 MP 分页 + 启用的 PageHelper」在表达什么。

## 2）项目设计：大师与小白的对话

**小白**：两个分页插件都配上，总有一个能用吧？

**大师**：那是双倍拦截、双倍意外：SQL 被改两次、依赖冲突、排错时不知道谁动的 SQL。

**小白**：我们新接口用 MP，老接口用 PageHelper 行不行？

**大师**：可以阶段性共存，但要**划清边界**（包、模块或规范），并统一升级 jsqlparser 等传递依赖，避免构建随机红。

**本章金句**：共存是**迁移策略**，不是**架构目标**；目标应是收敛为一种分页标准。

## 3）项目实战：主代码片段

模块注释直接点明风险：

```22:42:d:\software\workspace\mybatis-plus\mybatis-plus-samples\mybatis-plus-sample-pagehelper\src\test\java\com\baomidou\mybatisplus\samples\pagehelper\PagehelperTest.java
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

同文件 `testBlockAttackInner` 演示 MP 侧**防全表删除**等与插件相关的安全行为，可与分页讨论一并提及。

## 4）项目总结

| 维度 | 说明 |
|------|------|
| **优点** | PageHelper 对老代码侵入小；MP 分页与 Wrapper、租户等插件一体化好。 |
| **缺点 / 边界** | 混用增加依赖与行为复杂度；新人易在同一方法链上叠两种分页。 |
| **适用场景** | 中长期应统一为一种；短期按模块迁移时可共存。 |
| **注意事项** | 关注 `pom` 中 jsqlparser、pagehelper 与 MP 版本矩阵；升级前做集成测试。 |
| **常见踩坑** | 同一查询既 `PageHelper.startPage` 又传 `Page`；拦截器顺序导致分页失效或 SQL 异常。 |

**课后动作**：`mvn -pl mybatis-plus-sample-pagehelper test`；团队内书面确定「新代码默认分页方案」。详见 [LABS_CHECKLIST.md](LABS_CHECKLIST.md)。
