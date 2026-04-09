# MyBatis-Plus 培训实验检查清单

在仓库根目录 **`mybatis-plus-samples`** 下执行 Maven 命令（Windows 可改用 `mvnw.cmd`）。默认激活 `spring-boot3` Profile；若团队使用 Boot 2，请加 `-P spring-boot2`。

**通用命令模板**：

```bash
cd mybatis-plus-samples
mvn -pl <模块artifactId> -am test
```

仅编译不跑测试时可将 `test` 换为 `compile`。部分模块无测试，以「启动主类 / 手工验证」标注。

---

## 按培训章节绑定

| 章 | 主题 | Maven 模块 (`artifactId`) | 推荐验收方式 |
|----|------|---------------------------|--------------|
| 1 | 快速上手 | `mybatis-plus-sample-quickstart` | `QuickStartTest` |
| 1（选） | Spring MVC | `mybatis-plus-sample-quickstart-springmvc` | `SpringMvcTest` |
| 2～3 | 注解 + BaseMapper CRUD | `mybatis-plus-sample-crud` | `CrudTest`（多方法，可分两次课跑） |
| 3（选） | ActiveRecord | `mybatis-plus-sample-active-record` | `ActiveRecordTest` |
| 2（选） | MySQL 方言/配置 | `mybatis-plus-sample-mysql` | `MysqlTest` |
| 4 | Wrapper | `mybatis-plus-sample-wrapper` | `WrapperTest`、`UpdateWrapperTest` |
| 5 | 分页 | `mybatis-plus-sample-pagination` | `PaginationTest` |
| 6 | PageHelper | `mybatis-plus-sample-pagehelper` | `PagehelperTest` |
| 7 | 主键生成 | `mybatis-plus-sample-id-generator` | `IdGeneratorTest` |
| 7 | 字符串主键 | `mybatis-plus-sample-id-string` | `IdStringTest` |
| 7 | 序列 | `mybatis-plus-sample-sequence` | `SequenceTest` |
| 8 | 乐观锁 | `mybatis-plus-sample-optimistic-locker` | `OptLockerTest` |
| 8 | 逻辑删除 | `mybatis-plus-sample-logic-delete` | `LogicDeleteTest` |
| 8 | 自动填充 | `mybatis-plus-sample-auto-fill-metainfo` | `AutoFillTest` |
| 9 | 多租户 | `mybatis-plus-sample-tenant` | `TenantTest` |
| 10 | 枚举 | `mybatis-plus-sample-enum` | `EnumTest` |
| 10 | TypeHandler | `mybatis-plus-sample-typehandler` | `TypeHandlerTest` |
| 10 | JSONB | `mybatis-plus-sample-jsonb` | `JsonbTest` |
| 11 | 动态表名 | `mybatis-plus-sample-dynamic-tablename` | `DynamicTableNameTest` |
| 11 | SQL 注入器 | `mybatis-plus-sample-sql-injector` | `InjectorTest` |
| 12 | 关联 | `mybatis-plus-sample-association` | `AssociationTest` |
| 12 | ResultMap | `mybatis-plus-sample-resultmap` | `ResultmapTest` |
| 13 | DDL（MySQL） | `mybatis-plus-sample-ddl-mysql` | 无单元测试：运行 `DdlMysqlApplication`（需本机 MySQL 与配置） |
| 13 | 多模块装配 | `mybatis-plus-sample-assembly` | 无单元测试：运行 `AssemblyApplication` + 调接口或自写断言 |
| 13 | 代码生成器 | `mybatis-plus-generator`（**源码仓** `mybatis-plus` 内） | 运行 `FastAutoGeneratorTest` / `MySQLGeneratorTest` 等（需 DB）；或阅读生成逻辑 |
| 14 | 无 Spring | `mybatis-plus-sample-no-spring` | 运行 `NoSpring` 的 `main` |
| 14 | Kotlin | `mybatis-plus-sample-kotlin` | `ApplicationTests.kt` |
| 14 | 综合 | `mybatis-plus-sample-deluxe` | `DeluxeTest` |
| 15 | 性能分析 | `mybatis-plus-sample-performance-analysis` | `PerformanceTest` |
| 15 | 执行分析 | `mybatis-plus-sample-execution-analysis` | `ExecutionTest` |
| 15 | 启动分析 | `mybatis-plus-startup-analysis` | 阅读 `GeneratorCode.java` + 运行 `StartupAnalysisApplication` |

---

## 逐章必做 / 选做建议

### 第 1 章

- **必做**：`-pl mybatis-plus-sample-quickstart test`
- **选做**：`-pl mybatis-plus-sample-quickstart-springmvc test`

### 第 2～3 章

- **必做**：`-pl mybatis-plus-sample-crud test`
- **选做**：`-pl mybatis-plus-sample-active-record test`；`-pl mybatis-plus-sample-mysql test`（需 MySQL）

### 第 4 章

- **必做**：`-pl mybatis-plus-sample-wrapper test`

### 第 5～6 章

- **必做**：`-pl mybatis-plus-sample-pagination test`
- **选做**：`-pl mybatis-plus-sample-pagehelper test`

### 第 7 章

- **必做**（三选一或按环境）：`id-generator` / `id-string` / `sequence`（Oracle 等序列场景选 `sequence`）

### 第 8 章

- **必做**：三个模块各跑一次 `test`（可拆到两天）

### 第 9 章

- **必做**：`-pl mybatis-plus-sample-tenant test`

### 第 10 章

- **必做**：`enum` + `typehandler`
- **选做**：`jsonb`（PostgreSQL 团队优先）

### 第 11 章

- **必做**：`dynamic-tablename`、`sql-injector` 各 `test`

### 第 12 章

- **必做**：`association`、`resultmap` 各 `test`

### 第 13 章

- **必做（课堂演示）**：讲师演示 `FastAutoGeneratorTest` 或文档化生成步骤
- **实操**：学员本地二选一：`ddl-mysql` 主类 或 `assembly` 启动（依赖环境）

### 第 14 章

- **按分班**（见 [AUDIENCE_AND_SCOPE.md](AUDIENCE_AND_SCOPE.md)）：`no-spring` / `kotlin` / `deluxe`

### 第 15 章

- **必做**：`-pl mybatis-plus-sample-performance-analysis test` 或 `execution-analysis`
- **选修**：结合 [CHAPTER_15_SOURCE_DEEP_DIVE.md](CHAPTER_15_SOURCE_DEEP_DIVE.md) 本地打开源码跟读

---

## 结业作业（与培训计划一致）

在 **`mybatis-plus-sample-deluxe`** 基础上扩展，或自建最小表，实现并提交：

1. 分页列表查询  
2. 条件查询（Lambda Wrapper）  
3. 逻辑删除  
4. 乐观锁更新  
5. 至少一个 `@SpringBootTest` 用例  

验收命令示例：

```bash
mvn -pl mybatis-plus-sample-deluxe test
```

---

## 测试类全索引（便于 Ctrl+O 查找）

| 模块 | 测试类（`src/test/...`） |
|------|--------------------------|
| quickstart | `QuickStartTest` |
| quickstart-springmvc | `SpringMvcTest` |
| crud | `CrudTest` |
| active-record | `ActiveRecordTest` |
| mysql | `MysqlTest` |
| wrapper | `WrapperTest`, `UpdateWrapperTest` |
| pagination | `PaginationTest` |
| pagehelper | `PagehelperTest` |
| id-generator | `IdGeneratorTest` |
| id-string | `IdStringTest` |
| sequence | `SequenceTest` |
| optimistic-locker | `OptLockerTest` |
| logic-delete | `LogicDeleteTest` |
| auto-fill-metainfo | `AutoFillTest` |
| tenant | `TenantTest` |
| enum | `EnumTest` |
| typehandler | `TypeHandlerTest` |
| jsonb | `JsonbTest` |
| dynamic-tablename | `DynamicTableNameTest` |
| sql-injector | `InjectorTest` |
| association | `AssociationTest` |
| resultmap | `ResultmapTest` |
| deluxe | `DeluxeTest` |
| performance-analysis | `PerformanceTest` |
| execution-analysis | `ExecutionTest` |
| kotlin | `ApplicationTests.kt` |
