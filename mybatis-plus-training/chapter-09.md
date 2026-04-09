# 第 9 章：多租户插件（TenantLineInnerInterceptor）

示例模块：`mybatis-plus-sample-tenant`（`TenantTest`、`MybatisPlusConfig`）。

## 1）项目背景

- SaaS 场景下需在 SQL 层自动拼接 **租户条件**，降低「漏写租户 ID」的人为失误。
- MP 通过 `TenantLineInnerInterceptor` + `TenantLineHandler` 解析并改写 SQL。
- **本章目标**：能配置 `getTenantId` 与 `ignoreTable`；理解**插件不能替代认证授权**；知道与分页插件的顺序要求。

## 2）项目设计：大师与小白的对话

**小白**：有了多租户插件，是不是不用做权限了？

**大师**：插件防的是**正常请求路径下的疏漏**；恶意拼接 SQL、直连库、绕过 Mapper 的通道仍要系统级防护。

**小白**：所有表都要加租户字段吗？

**大师**：用 `ignoreTable` 排除字典表、全局配置表等；设计阶段就要分清「租户隔离」与「全局共享」。

**小白**：和分页一起用时要注意啥？

**大师**：示例注释写明：**先加租户插件，再加分页插件**，并关注执行器/缓存相关配置，避免诡异缓存问题。

**本章金句**：租户插件是**数据隔离安全带**，不是**安全认证替代品**。

## 3）项目实战：主代码片段

```26:44:d:\software\workspace\mybatis-plus\mybatis-plus-samples\mybatis-plus-sample-tenant\src\main\java\com\baomidou\mybatisplus\samples\tenant\config\MybatisPlusConfig.java
    @Bean
    public MybatisPlusInterceptor mybatisPlusInterceptor() {
        MybatisPlusInterceptor interceptor = new MybatisPlusInterceptor();
        interceptor.addInnerInterceptor(new TenantLineInnerInterceptor(new TenantLineHandler() {
            @Override
            public Expression getTenantId() {
                return new LongValue(1);
            }

            // 这是 default 方法,默认返回 false 表示所有表都需要拼多租户条件
            @Override
            public boolean ignoreTable(String tableName) {
                return !"sys_user".equalsIgnoreCase(tableName);
            }
        }));
        // 如果用了分页插件注意先 add TenantLineInnerInterceptor 再 add PaginationInnerInterceptor
        // 用了分页插件必须设置 MybatisConfiguration#useDeprecatedExecutor = false
//        interceptor.addInnerInterceptor(new PaginationInnerInterceptor());
        return interceptor;
    }
```

**验收**：`TenantTest` 中断言 SQL 或数据是否仅在租户 1 下可见（按示例具体实现阅读）。

## 4）项目总结

| 维度 | 说明 |
|------|------|
| **优点** | 统一在 SQL 层补租户条件，减少重复代码。 |
| **缺点 / 边界** | 复杂子查询、多租户报表、跨租户运维需单独方案；错误 `ignoreTable` 会扩大数据暴露面。 |
| **适用场景** | 表结构统一带 `tenant_id` 的多租户业务库。 |
| **注意事项** | 与分页、动态表名等插件顺序；线程内租户上下文（生产应来自登录态，而非写死 `LongValue(1)`）。 |
| **常见踩坑** | 原生 SQL 漏租户；报表直连从库无插件；测试写死租户 ID 误上生产。 |

**课后动作**：`mvn -pl mybatis-plus-sample-tenant test`。详见 [LABS_CHECKLIST.md](LABS_CHECKLIST.md)。
