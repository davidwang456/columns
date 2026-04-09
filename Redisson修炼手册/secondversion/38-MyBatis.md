# 第十三章（分篇五）：MyBatis——`redisson-mybatis` 与 Mapper 缓存

[← 第十三章导览](33-框架矩阵速览.md)｜[目录](README.md)

---

## 1. 项目背景

MyBatis Mapper 上启用了 **`<cache/>`**，希望缓存实现 **落到 Redis**，使多实例共享 **同一命名空间缓存**，并配置 **TTL、maxIdle、maxSize** 等；Redisson 提供 **`redisson-mybatis`** 及多种 **`RedissonCache*`** 实现类（含 Native、LocalCached、Clustered 等，见官方）。

---

## 2. 项目设计（大师 × 小白）

**小白**：Mapper 缓存和 Spring Cache 重复吗？  
**大师**：**可能重复**——若同一查询既 **Mapper cache** 又 **Service `@Cacheable`**，要搞清楚 **谁失效、谁更新**，否则 **幽灵数据**。

**小白**：一个 `redisson.yaml` 全项目共用？  
**大师**：常见，但 **不同业务缓存** 可用 **不同 cache 配置块**；**key 空间** 仍要 **前缀隔离**。

---

## 3. 项目实战（主代码片段）

**依赖**：

```xml
<dependency>
    <groupId>org.redisson</groupId>
    <artifactId>redisson-mybatis</artifactId>
    <version><!-- 与 Redisson 主版本一致 --></version>
</dependency>
```

**Mapper XML**（示例，Community 常用入口类之一）：

```xml
<cache type="org.redisson.mybatis.RedissonCache">
    <property name="timeToLive" value="200000"/>
    <property name="maxIdleTime" value="100000"/>
    <property name="maxSize" value="100000"/>
    <property name="redissonConfig" value="redisson.yaml"/>
</cache>
```

更多 **`type`**（`RedissonCacheNative`、`RedissonLocalCachedCache` 等）见 [cache-api-implementations.md#mybatis-cache](../cache-api-implementations.md#mybatis-cache)。

---

## 4. 项目总结

| 维度 | 说明 |
|------|------|
| **优点** | 与 MyBatis **`<cache>` 模型**贴合；可 **细调 TTL/容量**；多实例 **共享命名空间缓存**。 |
| **缺点** | **失效粒度** 粗（命名空间级 `clear` 等需理解）；复杂拓扑下要选 **Clustered / Native** 等正确实现。 |
| **适用场景** | 读多写少、Mapper 级缓存即可满足的业务；与现有 MyBatis XML 流程兼容。 |
| **注意事项** | **写 Mapper** 必须触发 **合理 flush**；Redis 不可用时的行为要 **压测**；与 Hibernate 二级缓存 **不要混用同一套 key 胡写**。 |
| **常见踩坑** | 忘记 **`evict`/`flushCache`** 导致更新不可见；**maxSize** 过小频繁驱逐；**redissonConfig** 路径在打包后 **找不到**。 |

---

## 本章实验室（约 45～60 分钟）

**环境**：MyBatis + `redisson-mybatis`；Mapper XML 开启 `<cache/>`；底层表 **id → name** 即可。

### 步骤

1. `selectById` **连续两次**，日志中 **预编译语句执行次数** 或 JDBC 代理计数 **应为 1**（第二次命中缓存）。  
2. `updateNameById` **无** `flushCache` 时再 `selectById`，记录 **是否读到旧值**；给 update 加 **`flushCache=true`** 复测。  
3. **两实例** 同 Mapper 缓存命名空间，A 更新并 flush 后，B **是否可见**（理解 **命名空间级** 语义）。  
4. 将 `redisson.yaml` 打成 jar 内资源，`java -jar` 验证 **路径可读**（避免「本地 IDE 能跑、容器不能跑」）。

### 验证标准

- 实验 1～2：**有日志或计数** 证明命中与失效。  
- 实验 4：**jar 内配置** 启动成功截图或日志一行。

### 记录建议

- **哪些 Mapper 禁止二级缓存**（写多读少、强一致）列表。

**上一篇**：[第十三章（分篇四）Hibernate 二级缓存](37-Hibernate二级缓存.md)｜**下一篇**：[第十三章（分篇六）Tomcat Session](39-Tomcat-Session.md)｜**下一章**：[第十四章](40-可观测与上线清单.md)
