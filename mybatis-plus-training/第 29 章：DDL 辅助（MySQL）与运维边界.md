# 第 29 章：DDL 辅助（MySQL）与运维边界

示例模块：`mybatis-plus-sample-ddl-mysql`（需本机 MySQL 与配置）。

## 1）项目背景

在**可控环境**下执行版本化 DDL、初始化结构，可与 MP 扩展能力配合。示例通过 `SimpleDdl` 列出待执行脚本，强调**权限隔离**与**变更评审**——学的是脚本组织方式，不是鼓励直连生产乱执行。

**痛点放大**：把示例脚本直接拷到生产，无备份与回滚，会造成**不可逆事故**。

**本章目标**：阅读 `MysqlDdl#getSqlFiles`；理解脚本拆分与注释；明确「仅演示环境执行」的边界。

## 2）项目设计：小胖、小白与大师的对话

**小胖**：DDL 让应用在启动时跑一下多省事。

**大师**：省事的是**演示**；生产要有**迁移工具与审批**，而不是应用内偷偷改表。

**技术映射**：**SimpleDdl** ≈ 施工图纸列表；**Flyway/Liquibase** ≈ 监理与验收流程。

---

**小白**：示例里的存储过程脚本能直接上生产吗？

**大师**：要经过 **DBA 评审、性能测试、回滚脚本**。

**本章金句**：DDL 能力学**结构**，责任在**变更流程**。

## 3）项目实战

**环境准备**：本地 MySQL；配置数据源；运行 `DdlMysqlApplication`（以模块 README 为准）。

**步骤 1：`MysqlDdl`**

```9:28:d:\software\workspace\mybatis-plus\mybatis-plus-samples\mybatis-plus-sample-ddl-mysql\src\main\java\com\baomidou\mybatisplus\samples\ddl\mysql\MysqlDdl.java
@Component
public class MysqlDdl extends SimpleDdl {

    /**
     * 执行 SQL 脚本方式
     */
    @Override
    public List<String> getSqlFiles() {
        return Arrays.asList(
                // 测试存储过程
                "db/test_procedure.sql#$$",

                // 内置包方式
                "db/tag-schema.sql",
                "db/tag-data.sql"

                // 文件绝对路径方式（修改为你电脑的地址）
                // "D:\\sql\\tag-data.sql"
        );
    }
}
```

**验证方式**：主类运行 + 观察日志（无单元测试以模块说明为准）。

**完整代码清单**：`mybatis-plus-samples/mybatis-plus-sample-ddl-mysql/`

## 4）项目总结

| 优点 | 缺点 / 边界 |
|------|-------------|
| 脚本列表清晰 | 生产误用风险高 |
| 便于演示初始化 | 需专业迁移工具配套 |

**适用场景**：内部平台演示、环境初始化教学。

**不适用场景**：无 DBA 评审的线上变更。

**注意事项**：账号密码；脚本幂等性；大表 DDL 锁表。

**常见踩坑（案例化）**

1. **现象**：脚本执行一半失败。**根因**：无事务边界。**处理**：可重复执行设计。
2. **现象**：字符集乱码。**根因**：客户端编码。**处理**：统一 UTF-8。
3. **现象**：存储过程权限不足。**根因**：账号权限。**处理**：最小权限原则。

**思考题**

1. 应用内 DDL 与独立迁移 Job 的优劣？
2. 与 K8s Job 执行 DDL 如何衔接？

**课后动作**：[LABS_CHECKLIST.md](LABS_CHECKLIST.md) 第 29 章。
