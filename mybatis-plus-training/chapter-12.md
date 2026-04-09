# 第 12 章：关联查询、`ResultMap` 与 XML 协作

示例模块：`mybatis-plus-sample-association`、`mybatis-plus-sample-resultmap`。

## 1）项目背景

- 多表关系在 MyBatis 中通常用 **嵌套查询 / 嵌套结果** + `ResultMap` 表达；MP 不试图用单表 Wrapper 替代所有关联场景。
- 业务上常见「用户带公司」「子女带父母」等对象图，需要在 **N+1** 与 **一次大 JOIN** 之间权衡。
- **本章目标**：能读 association 示例中 Service + 自定义分页；能读 resultmap 示例中 XML 嵌套映射。

## 2）项目设计：大师与小白的对话

**小白**：MP 有没有像 JPA 那样 `@OneToMany` 全自动？

**大师**：MyBatis 系核心是 SQL 可控；关联加载策略要你自己在 XML/注解里设计，MP 提供的是单表与插件增强。

**小白**：我 `list()` 一下用户，关联公司全出来了，是不是很爽？

**大师**：注意是不是发了 N+1 条 SQL；爽的是 demo，痛的是生产流量。

**小白**：复杂 ResultMap 太难写。

**大师**：难写往往说明 SQL 本身复杂；这时更不该用 Wrapper 硬凑。

**本章金句**：**对象图有多丰满，SQL 与 ResultMap 就要有多清醒**。

## 3）项目实战：主代码片段

**关联 + 自定义分页查询**（Service 层）：

```27:58:d:\software\workspace\mybatis-plus\mybatis-plus-samples\mybatis-plus-sample-association\src\test\java\com\baomidou\mybatisplus\samples\association\AssociationTest.java
    @Test
    public void testSelectList() {
        userService.list().forEach(t -> System.out.println(t.getCompany()));
    }

    @Test
    public void testInsert() {
        List<User> userList = new ArrayList<>();
        for (int i = 0; i < 100; ++i) {
            Company cmp = new Company();
            cmp.setId(1L);
            User user = new User();
            user.setId(100L + i);
            user.setCompany(cmp);
            user.setName("Han Meimei" + i);
            user.setEmail(user.getName() + "@baomidou.com");
            user.setAge(18);
            userList.add(user);
        }
        userService.saveBatch(userList);
        userService.list().forEach(t -> System.out.println(t));
        testSelect();
        testUpdate();
    }


    private void testSelect() {
        QueryWrapper<User> wrapper = new QueryWrapper<>();
        wrapper.eq("t.company_id", 1);
        int pageSize = 5;
        IPage<User> page = new Page<User>(1, pageSize);
        List<User> userList = userService.selectUserPage(page, wrapper);
```

**XML `ResultMap` 嵌套（子女 ↔ 父母）**：

```32:43:d:\software\workspace\mybatis-plus\mybatis-plus-samples\mybatis-plus-sample-resultmap\src\test\java\com\baomidou\mybatisplus\samples\resultmap\ResultmapTest.java
    @Test
    void t_c() {
        final Child child = childMapper.selectLinkById(1L);
        log.info("child: {}", child);
        assertThat(child).isNotNull();
        final Man laoHan = child.getLaoHan();
        assertThat(laoHan).isNotNull();
        assertThat(laoHan.getName()).isNotBlank();
        final Woman laoMa = child.getLaoMa();
        assertThat(laoMa).isNotNull();
        assertThat(laoMa.getName()).isNotBlank();
    }
```

具体 SQL 与 `resultMap` 定义见各模块下 `mapper/*.xml`。

## 4）项目总结

| 维度 | 说明 |
|------|------|
| **优点** | XML + ResultMap 表达力强；与 MP 单表能力互补。 |
| **缺点 / 边界** | 维护成本高；深层嵌套影响可读性与性能。 |
| **适用场景** | 明确需要对象图、关联筛选、复杂列映射。 |
| **注意事项** | 分页与 count 在关联 SQL 上的行为；懒加载（若使用）带来的会话问题。 |
| **常见踩坑** | N+1；一次性 JOIN 过大宽表；列别名与 `resultMap` 不匹配；逻辑删条件在关联 SQL 中遗漏。 |

**课后动作**：`mvn -pl mybatis-plus-sample-association,mybatis-plus-sample-resultmap test`。详见 [LABS_CHECKLIST.md](LABS_CHECKLIST.md)。
