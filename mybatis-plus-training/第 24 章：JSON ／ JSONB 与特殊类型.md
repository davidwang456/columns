# 第 24 章：JSON / JSONB 与特殊类型

示例模块：`mybatis-plus-sample-jsonb`（**选修**，PostgreSQL；测试默认 `@Disabled`，需本地 PG 后启用）。

## 1）项目背景

PostgreSQL **JSONB** 提供二进制 JSON 与索引能力，适合半结构化扩展字段。MP 示例演示将值对象写入 JSONB 列并读回；需 **数据库、驱动、测试环境** 同时就绪。

**痛点放大**：在 H2/MySQL 团队强行用 JSONB 示例会**环境不匹配**；JSON 查询与索引需 DBA 参与，不是 ORM 单方面能兜底。

**本章目标**：阅读 `JsonbTest` 与实体映射；知道启用测试的前置条件；建立与第 23 章 TypeHandler 的联想。

## 2）项目设计：小胖、小白与大师的对话

**小胖**：有 JSON 还要表字段吗？

**大师**：**检索、约束、关联**仍需要关系列；JSON 适合扩展属性，不是万能垃圾桶。

**技术映射**：**JSONB** ≈ 带索引的便签墙；**关系列** ≈ 档案柜抽屉。

---

**小白**：为啥测试默认禁用？

**大师**：CI 未必有 PG；**选修**是让你本地有环境再开。

**本章金句**：JSON 能力是**数据库能力**，ORM 只负责搬运与映射。

## 3）项目实战

**环境准备**：本地 PostgreSQL；去掉 `JsonbTest` 上 `@Disabled`（以团队规范为准）。

**步骤 1：测试骨架**

```14:28:d:\software\workspace\mybatis-plus\mybatis-plus-samples\mybatis-plus-sample-jsonb\src\test\java\com\baomidou\mybatisplus\samples\jsonb\JsonbTest.java
@Disabled
@SpringBootTest
public class JsonbTest {
    @Autowired
    private TestDataMapper testDataMapper;

    @Test
    public void test() {
        TestData testData = new TestData();
        testData.setContent(TestContent.of("hi", "flowlong"));
        testData.setContentList(Arrays.asList(TestContent.of("name", "秋秋"), TestContent.of("name", "哈哈")));
        testDataMapper.insert(testData);
        TestData dbTestData = testDataMapper.selectById(testData.getId());
        System.out.println(dbTestData.getContent());
        Assertions.assertEquals(testData.getContent().getTitle(), dbTestData.getContent().getTitle());
```

**验证命令**：环境就绪后 `mvn -pl mybatis-plus-sample-jsonb -am test`

**完整代码清单**：`mybatis-plus-samples/mybatis-plus-sample-jsonb/`

## 4）项目总结

| 优点 | 缺点 / 边界 |
|------|-------------|
| 灵活扩展字段 | 依赖 PG 生态 |
| 可建 GIN 索引 | 查询复杂度高 |

**适用场景**：PG 技术栈、半结构化扩展。

**不适用场景**：无 PG、强范式团队。

**注意事项**：驱动版本；JSON 等值查询与包含查询差异。

**常见踩坑（案例化）**

1. **现象**：CI 全跳过。**根因**：`@Disabled`。**处理**：Testcontainers 或分 Profile。
2. **现象**：写入成功读失败。**根因**：TypeHandler 与列类型。**处理**：对照 PG 类型映射。
3. **现象**：大 JSON 拖垮缓冲。**根因**：无分页与限制。**处理**：拆表或对象存储。

**思考题**

1. JSONB 与 MySQL JSON 函数在 ORM 层的差异？
2. 何时应将 JSON 字段拆为子表？

**课后动作**：[LABS_CHECKLIST.md](LABS_CHECKLIST.md) 第 24 章（选做）。
