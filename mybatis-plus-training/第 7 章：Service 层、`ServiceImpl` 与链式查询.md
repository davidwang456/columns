# 第 7 章：Service 层、`ServiceImpl` 与链式查询

示例模块：`mybatis-plus-sample-id-generator`（演示 `ServiceImpl` + `saveBatch`；与 CRUD 分层理念一致）。

## 1）项目背景

仅有 `BaseMapper` 时，事务、领域规则、跨 Mapper 编排仍要写在别处。MP 提供 `IService` / `ServiceImpl<M, T>`，在 **Service 层**封装 `save`/`update`/`page`/`lambdaQuery` 等，减少「Service 里只调一行 Mapper」的样板，并与 **链式条件**组合。

**痛点放大**：若团队把业务规则散落在 Controller 或直接 Mapper，会导致**事务边界不清**、**单测难写**；若滥用 `ServiceImpl` 默认方法而不读文档，会踩批量与回填边界。

**本章目标**：能写 `extends ServiceImpl<UserMapper, User>`；理解 `saveBatch` 与 Mapper 批量差异；知道链式 `lambdaQuery` 入口（完整分页见第 12 章）。

## 2）项目设计：小胖、小白与大师的对话

**小胖**：Service 不就是包一层吗？

**大师**：包的是**事务边界**和**领域步骤**——跨表、重试、幂等，不该进 Mapper。

**技术映射**：**ServiceImpl** ≈ 业务前台；**BaseMapper** ≈ 仓库管理员。

---

**小白**：`saveBatch` 和循环 `insert` 啥区别？

**大师**：批量走 JDBC batch 与 MP 封装策略，性能与回填行为不同；要以集成测试为准。

**本章金句**：**Mapper 薄、Service 厚**，不是「多一个类名」。

## 3）项目实战

**环境准备**：`mvn -pl mybatis-plus-sample-id-generator -am test`。

**步骤 1：`UserService`**

```1:15:d:\software\workspace\mybatis-plus\mybatis-plus-samples\mybatis-plus-sample-id-generator\src\main\java\com\baomidou\samples\service\UserService.java
package com.baomidou.samples.service;

import com.baomidou.mybatisplus.extension.service.impl.ServiceImpl;
import com.baomidou.samples.entity.User;
import com.baomidou.samples.mapper.UserMapper;
import org.springframework.stereotype.Service;

/**
 * @author sundongkai 2021/1/12
 */
@Service
public class UserService extends ServiceImpl<UserMapper, User> {

}
```

**步骤 2：批量插入（`IdGeneratorTest`）**

见 `IdGeneratorTest#test` 与 `testBatch`，验证 `saveBatch` 返回值与实体列表状态。

**验证命令**：`mvn -pl mybatis-plus-sample-id-generator -am test`

**完整代码清单**：`mybatis-plus-samples/mybatis-plus-sample-id-generator/`

## 4）项目总结

| 优点 | 缺点 / 边界 |
|------|-------------|
| 减少 Service 样板代码 | 默认方法多，易误用 |
| 与 `lambdaQuery`/`page` 一致 | 复杂事务需显式拆分 |

**适用场景**：Spring 管理的事务型业务服务。

**不适用场景**：纯查询报表服务可只用 Mapper + DSL。

**注意事项**：`@Transactional` 自调用陷阱；只读操作标记只读事务。

**常见踩坑（案例化）**

1. **现象**：`saveBatch` 部分成功。**根因**：默认批大小与异常策略。**处理**：阅读版本文档与 `saveBatch` 重载。
2. **现象**：链式查询条件为 null。**根因**：动态条件未判空。**处理**：单元测试覆盖分支。
3. **现象**：循环依赖 Service。**根因**：分层不清。**处理**：抽领域服务或事件。

**思考题**

1. `ServiceImpl#getBaseMapper()` 与直接注入 `UserMapper` 在测试替身设计上有何差别？
2. 何时应拆 `UserService` 为领域服务 + 应用服务？

**课后动作**：[LABS_CHECKLIST.md](LABS_CHECKLIST.md) 第 7 章。
