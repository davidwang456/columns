# 第十三章（分篇三）：Helidon——`redisson-helidon` 与 CDI

[← 第十三章导览](33-框架矩阵速览.md)｜[目录](README.md)

---

## 1. 项目背景

服务运行在 **Helidon**（MP / SE），希望通过 **CDI `@Inject`** 使用 `RedissonClient`，并把连接参数写在 **`META-INF/microprofile-config.properties`**，与微服务配置规范一致。Helidon **1.4–4.x** 对应 **`redisson-helidon-20` / `30` / `40`** 等构件。

---

## 2. 项目设计（大师 × 小白）

**小白**：Helidon 也注入 `RedissonClient`，和 Quarkus 一样？  
**大师**：**入口相似，配置前缀不同**——这里是 **`org.redisson.Redisson.<实例名>.*`**，还支持 **`@Named`** 多实例。

**小白**：我忘了 `@Named` 会怎样？  
**大师**：文档写：**不带 `@Named` 时用实例名 `default`**——多实例时 **注错 Bean** 等于 **连错 Redis**。

---

## 3. 项目实战（主代码片段）

**依赖**：见 [microservices-integration.md#helidon](../microservices-integration.md#helidon)（按 Helidon 版本选 `redisson-helidon-20` / `30` / `40`）。

**配置**（`META-INF/microprofile-config.properties` 片段，示例实例名 `simple`）：

```properties
org.redisson.Redisson.simple.singleServerConfig.address=redis://127.0.0.1:6379
org.redisson.Redisson.simple.singleServerConfig.connectionPoolSize=64
org.redisson.Redisson.simple.threads=16
org.redisson.Redisson.simple.nettyThreads=32
```

**注入**：

```java
import jakarta.inject.Inject;
import jakarta.inject.Named;
import org.redisson.api.RedissonClient;

public class InventoryResource {

    @Inject
    @Named("simple")
    private RedissonClient redisson;
}
```

**默认实例**：去掉 `@Named` 时使用名为 **`default`** 的配置（以官方说明为准）。

---

## 4. 项目总结

| 维度 | 说明 |
|------|------|
| **优点** | 与 **MicroProfile Config** 自然结合；支持 **命名多客户端**（`@Named`）。 |
| **缺点** | 配置键 **较长**，易打错；Helidon **大版本** 与 artifact 需 **手动对齐**。 |
| **适用场景** | Helidon MP 服务、需要与 JNDI/CDI 风格一致的 Redis 访问。 |
| **注意事项** | 扁平属性与 **YAML 配置章节** 的对应关系见官方 **configuration** 文档。 |
| **常见踩坑** | **`simple` / `default`** 命名与代码不一致；**nettyThreads** 驼峰在 properties 中的写法与文档不一致导致 **启动失败**。 |

---

## 本章实验室（约 40～60 分钟）

**环境**：Helidon MP；`microprofile-config.properties` 或 `application.yaml` 按官方键名配置。

### 步骤

1. **单客户端**：注入默认 `RedissonClient`，`getBucket("lab:helidon:1").set("x")` 成功。  
2. （若文档支持）配置 **第二个** `@Named` 客户端指向 **同一 Redis 不同 logical 前缀** 或第二个 DB index，各写一 key，`SCAN` 区分。  
3. **故意**写错 **`nettyThreads` 或 redis 地址** 键名，记录 **Config 解析错误** vs **连接超时错误** 的差异。  
4. 将 **可启动的最小配置块** 复制到团队 Wiki（脱敏密码）。

### 验证标准

- 实验 1 通过；实验 3 有 **两类错误** 样本之一即可。  
- Wiki 上有 **Helidon 大版本 ↔ redisson-helidon artifact** 一行。

### 记录建议

- 与第十三章导览 **综合实验室** 合并：**CI 冒烟** 引用本实验的 `getBucket` 用例。

**上一篇**：[第十三章（分篇二）Micronaut](35-Micronaut.md)｜**下一篇**：[第十三章（分篇四）Hibernate 二级缓存](37-Hibernate二级缓存.md)｜**下一章**：[第十四章](40-可观测与上线清单.md)
