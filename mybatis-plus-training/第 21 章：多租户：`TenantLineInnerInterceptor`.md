# 第 21 章：多租户：`TenantLineInnerInterceptor`

示例模块：`mybatis-plus-sample-tenant`。

## 1）项目背景

SaaS 场景下需在 SQL 层自动拼接 **租户条件**，降低漏写 `tenant_id` 的风险。MP 通过 `TenantLineInnerInterceptor` + `TenantLineHandler` 解析并改写 SQL。**插件不能替代认证授权**，防的是正常路径下的疏漏。

**痛点放大**：与分页插件顺序错误会导致 **count 或 limit 未带租户**；报表直连从库若绕过插件，会**跨租户读**。

**本章目标**：配置 `getTenantId` 与 `ignoreTable`；理解「先租户后分页」的注释要求；跑通 `TenantTest`。

## 2）项目设计：小胖、小白与大师的对话

**小胖**：有租户插件还要权限系统吗？

**大师**：插件防**疏漏**；恶意直连、越权 API 仍要认证与鉴权。

**技术映射**：**租户插件** ≈ 楼宇门禁；**权限系统** ≈ 工牌与岗位。

---

**小白**：字典表也要 tenant_id 吗？

**大师**：用 `ignoreTable` 排除全局共享表；设计阶段分清隔离与共享。

**本章金句**：租户条件是**数据隔离安全带**，不是身份认证。

## 3）项目实战

**环境准备**：`mvn -pl mybatis-plus-sample-tenant -am test`。

**步骤 1：`MybatisPlusConfig`**

```26:48:d:\software\workspace\mybatis-plus\mybatis-plus-samples\mybatis-plus-sample-tenant\src\main\java\com\baomidou\mybatisplus\samples\tenant\config\MybatisPlusConfig.java
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

**验证命令**：`mvn -pl mybatis-plus-sample-tenant -am test`

**完整代码清单**：`mybatis-plus-samples/mybatis-plus-sample-tenant/`

## 4）项目总结

| 优点 | 缺点 / 边界 |
|------|-------------|
| SQL 层统一补条件 | 复杂子查询需专项方案 |
| 与 MP 生态一体 | `ignoreTable` 错误会扩大暴露面 |

**适用场景**：表结构统一带 `tenant_id` 的多租户库。

**不适用场景**：强跨租户报表（需提权与审计）。

**注意事项**：生产 `getTenantId` 应来自登录态，而非写死 `LongValue(1)`。

**常见踩坑（案例化）**

1. **现象**：分页串租户。**根因**：插件顺序。**处理**：先租户后分页。
2. **现象**：原生 SQL 漏租户。**根因**：绕过拦截。**处理**：规范与审查。
3. **现象**：测试通过、生产失败。**根因**：线程上下文未传租户。**处理**：过滤器与 MDC。

**思考题**

1. 子查询中的租户条件在何版本行为需对照源码？
2. 与 ShardingSphere 分片键同时存在时的职责划分？

**课后动作**：[LABS_CHECKLIST.md](LABS_CHECKLIST.md) 第 21 章。
