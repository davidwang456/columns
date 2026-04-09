# 第十三章（分篇二）：Micronaut——`redisson-micronaut`、Cache 与 Session

[← 第十三章导览](33-框架矩阵速览.md)｜[目录](README.md)

---

## 1. 项目背景

项目在 **Micronaut** 下需要 **Redis 客户端 Bean**，并可能同时使用 **Micronaut Cache** 与 **HTTP Session 外置**；希望配置形态与 Redisson **YAML 语义**一致，且 **Micronaut 2/3/4** 选用对应的 **`redisson-micronaut-20` / `30` / `40`** 构件。

---

## 2. 项目设计（大师 × 小白）

**小白**：Micronaut 配置也写 `spring.redis`？  
**大师**：**不写。** 这里是 **`redisson:`** 下的 **Redisson 自有结构**（见官方示例），别从 Spring Boot **机械搬运**。

**小白**：Session 用 Redisson，默认 Kryo？  
**大师**：文档列了 **`micronaut.session.http.redisson.codec`** 等——**序列化即契约**，要和第四章一样评审。

---

## 3. 项目实战（主代码片段）

**依赖**：按 Micronaut 版本选 **`redisson-micronaut-20` / `30` / `40`**（[microservices-integration.md#micronaut](../microservices-integration.md#micronaut)）。

**Redisson 配置**（`application.yml` 片段）：

```yaml
redisson:
  single-server-config:
    address: "redis://127.0.0.1:6379"
  threads: 16
  netty-threads: 32
```

**Session 外置**（示例）：

```yaml
micronaut:
  session:
    http:
      redisson:
        enabled: true
        update-mode: "WRITE_BEHIND"
        broadcast-session-updates: false
```

**Cache**：见 [Micronaut Cache](../cache-api-implementations.md#micronaut-cache)。

---

## 4. 项目总结

| 维度 | 说明 |
|------|------|
| **优点** | **一体化**：客户端 + Cache + Session 文档在同栈；配置即 Redisson YAML。 |
| **缺点** | **版本后缀 artifact** 多，易选错；Session **写模式** 选错会带来一致性与延迟问题。 |
| **适用场景** | Micronaut 微服务、边缘服务、需要 Session 共享的 BFF。 |
| **注意事项** | **`WRITE_BEHIND` vs `AFTER_REQUEST`** 与业务一致性对齐；多实例 **`broadcastSessionUpdates`** 评估流量。 |
| **常见踩坑** | Micronaut **大版本升级** 未换 **`redisson-micronaut-*`**；与 **Spring Data Redis** 混用同一 Redis 无 **key 前缀**；Codec 变更导致 **全员登出**。 |

---

## 本章实验室（约 45～60 分钟）

**环境**：Micronaut 应用 + `application.yml` 中 Redisson 段；Testcontainers 可选。

### 步骤

1. 启动后注入 `RedissonClient`，写入 `lab:mn:ping`，`@Controller` 或 CLI 读出验证。  
2. 若使用 **Session 集成**：登录一次，`redis-cli` 确认 **session key 前缀** 与文档一致。  
3. 切换 **Cache 写模式**（文档中的 `WRITE_BEHIND` / `AFTER_REQUEST` 之一）做 **A/B**：同一写请求后 **立即读** 是否可见（记录 **一致性差异**）。  
4. 故意选错 **`redisson-micronaut-XX` 后缀**，收集 **依赖解析或启动错误**，纠正后复测。

### 验证标准

- 实验 1：**读写成功** 日志或 HTTP 200 + body。  
- 实验 3：**一句话结论** 本业务应选哪种写模式。

### 记录建议

- **broadcastSessionUpdates** 对 **Redis 扇出** 的估算（实例数 × QPS 量级）。

**上一篇**：[第十三章（分篇一）Quarkus](34-Quarkus.md)｜**下一篇**：[第十三章（分篇三）Helidon](36-Helidon.md)｜**下一章**：[第十四章](40-可观测与上线清单.md)
