# 第 10 章：枚举、`TypeHandler` 与 JSON/JSONB

示例模块：`mybatis-plus-sample-enum`、`mybatis-plus-sample-typehandler`、`mybatis-plus-sample-jsonb`（PostgreSQL JSONB；测试类带 `@Disabled`，需本地环境再启用）。

## 1）项目背景

- 数据库存储枚举常量比存「枚举名」更利于报表与多语言；Java 侧需要可逆映射。
- 复杂列（JSON 列表、值对象）需 **TypeHandler** 完成 JDBC 类型与 Java 类型互转。
- MP 提供 `IEnum`、`FastjsonTypeHandler`、`JacksonTypeHandler` 等与 `@TableField` 的组合。
- **本章目标**：能配置枚举入库策略；知道 `autoResultMap` 与 `typeHandler` 的配套关系；了解 JSONB 示例的启用条件。

## 2）项目设计：大师与小白的对话

**小白**：枚举我 `name()` 存字符串多直观。

**大师**：重构枚举名会改历史数据含义；应用层与报表层对「码表」要有统一约定。

**小白**：JSON 列直接当 String 读写行不行？

**大师**：能跑，但失去类型安全；换字段结构时全项目字符串解析会炸一片。

**小白**：TypeHandler 和全局 ObjectMapper 啥关系？

**大师**：注意序列化库版本与配置一致；升级 Jackson/Fastjson 要做回归测试。

**本章金句**：**库内表示**与 **Java 表示**要在团队层面对齐，MP 只负责桥接。

## 3）项目实战：主代码片段

**多枚举插入与查询**（节选）：

```37:48:d:\software\workspace\mybatis-plus\mybatis-plus-samples\mybatis-plus-sample-enum\src\test\java\com\baomidou\mybatisplus\samples\enums\EnumTest.java
    @Test
    public void insert() {
        User user = new User();
        user.setName("K神");
        user.setAge(AgeEnum.ONE);
        user.setGrade(GradeEnum.HIGH);
        user.setGender(GenderEnum.MALE);
        user.setStrEnum(StrEnum.ONE);
        user.setEmail("abc@mp.com");
        Assertions.assertTrue(mapper.insert(user) > 0);
        // 成功直接拿回写的 ID
        System.err.println("\n插入成功 ID 为：" + user.getId());
```

**JSON 列 + `FastjsonTypeHandler`（注意 `autoResultMap`）**：

```20:45:d:\software\workspace\mybatis-plus\mybatis-plus-samples\mybatis-plus-sample-typehandler\src\main\java\com\baomidou\mybatisplus\samples\typehandler\entity\User.java
@Data
@Accessors(chain = true)
@TableName(value = "sys_user", autoResultMap = true)
public class User {
    private Long id;
    private String name;
    private Integer age;
    private String email;
// ...
    @TableField(typeHandler = FastjsonTypeHandler.class)
    private List<Wallet> wallets;
```

**JSONB 示例**（默认禁用测试）：

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

## 4）项目总结

| 维度 | 说明 |
|------|------|
| **优点** | 枚举与 JSON 映射声明式、可复用 TypeHandler。 |
| **缺点 / 边界** | 序列化格式变更影响线上数据；大 JSON 列与索引、查询性能需单独设计。 |
| **适用场景** | 状态类字段、扩展属性 JSON、PostgreSQL JSONB。 |
| **注意事项** | `autoResultMap = true`；依赖包与实体模块一致；JSONB 需 DB 与驱动支持。 |
| **常见踩坑** | 反序列化失败导致查询抛异常；历史脏数据无法映射到新枚举；忘记 `autoResultMap` 导致 Map 不到嵌套类型。 |

**课后动作**：`mvn -pl mybatis-plus-sample-enum,mybatis-plus-sample-typehandler test`；JSONB 在具备 PG 环境后去掉 `@Disabled` 再跑。详见 [LABS_CHECKLIST.md](LABS_CHECKLIST.md)。
