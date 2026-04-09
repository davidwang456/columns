# 第十三章（分篇一）：Quarkus——`redisson-quarkus` 与 Native

[← 第十三章导览](33-框架矩阵速览.md)｜[目录](README.md)

---

## 1. 项目背景

团队采用 **Quarkus** 做云原生微服务，需要 **CDI 注入 `RedissonClient`**，并与 **Quarkus Cache** 扩展配合；部分服务要编 **GraalVM Native Image**，要求 Redisson 相关 **反射、动态代理** 按文档补齐。

---

## 2. 项目设计（大师 × 小白）

**小白**：Quarkus 了，Redis 定律变了吗？  
**大师**：**没变**——仍是 **拓扑、Codec、key 设计**；变的是 **打包方式**：Native 下 **少一行反射配置就多一个运行时炸雷**。

**小白**：Remote Service 也要上 Native？  
**大师**：官方单独写了 **dynamic-proxy.json / reflection-config.json** 示例——**按文档抄作业**，别凭感觉关警告。

---

## 3. 项目实战（主代码片段）

**依赖**：按 Quarkus 主版本选择 **`redisson-quarkus-16` / `20` / `30` / `33`** 等（见 [microservices-integration.md#quarkus](../microservices-integration.md#quarkus)）。

**配置**（`application.properties` 片段，扁平 Redisson 配置，驼峰转连字符）：

```properties
quarkus.redisson.single-server-config.address=redis://localhost:6379
quarkus.redisson.threads=16
quarkus.redisson.netty-threads=32
# 或使用 quarkus.redisson.file 指向配置文件
```

**注入**（Quarkus 3 多为 `jakarta.inject`；旧版本可能是 `javax.inject`，以工程为准）：

```java
import jakarta.inject.Inject;
import org.redisson.api.RedissonClient;

public class PriceService {

    @Inject
    RedissonClient redisson;

    public void ping() {
        redisson.getBucket("quarkus:ping").set("ok");
    }
}
```

**Quarkus Cache**：见 [cache-api-implementations.md#quarkus-cache](../cache-api-implementations.md#quarkus-cache)。

---

## 4. 项目总结

| 维度 | 说明 |
|------|------|
| **优点** | 与 Quarkus **扩展模型**一致；**快速启动**（JVM 模式）；官方文档覆盖 **版本矩阵**。 |
| **缺点** | **Native** 配置成本高；升级 Quarkus **大版本** 常要换 **`redisson-quarkus-*` artifact**。 |
| **适用场景** | Quarkus 栈内缓存、分布式锁、与 Redis 共栈的中间件能力。 |
| **注意事项** | 对齐 [dependency-list.md](../dependency-list.md) / BOM；CI 增加 **Native 或 Testcontainers 冒烟**。 |
| **常见踩坑** | **反射/代理未注册** 导致 Native 运行期失败；把 Spring 第十二章配置 **原样粘贴** 到 Quarkus；忽略 **Codec** 在 Native 下的注册。 |

---

## 本章实验室（约 45～90 分钟）

**环境**：Quarkus **JVM 模式** 先跑通；可选再试 **Native**（时间不足则只记文档勾选项）。参考 [microservices-integration.md](../microservices-integration.md)。

### 步骤

1. 按文档引入 **`redisson-quarkus-*`** 与版本矩阵，启动时日志打印 **Redisson 版本**。  
2. `@ApplicationScoped` Bean 注入 `RedissonClient`，`getBucket("lab:quarkus:smoke").set("ok")`，`@GET` 资源读出 **ok**。  
3. **Testcontainers** 或 test profile 指向临时 Redis，跑 **1 个** `@QuarkusTest`。  
4. （可选）`mvn package -Dquarkus.native.enabled=true`（或项目等价命令），记录 **Native 构建失败** 时第一条 **reflective class** 提示。

### 验证标准

- 集成测试 **绿**；HTTP 或单测能证明 **读写 Redis 成功**。  
- `dependency-list.md` 中 **artifact 后缀** 与当前 Quarkus 大版本 **手写对照表** 一行。

### 记录建议

- Native：**已注册 / 待注册** 的反射列表贴 Wiki。

**上一章**：[第十二章（分篇三）Spring Session](32-SpringSession.md)｜[第十二章导览](29-Spring生态集成.md)｜**下一篇**：[第十三章（分篇二）Micronaut](35-Micronaut.md)｜**下一章**（读完全部分篇后）：[第十四章 可观测与上线清单](40-可观测与上线清单.md)
