# 第十二章（分篇一）：Spring Boot Starter——`RedissonClient` 与配置外置

[← 第十二章导览](29-Spring生态集成.md)｜[目录](README.md)

---

## 1. 项目背景

团队已采用 **Spring Boot**，希望 **注入即用** `RedissonClient`，配置随 **profile** 切换，并在应用关闭时 **自动 shutdown**，而不是在业务类里散落 `Redisson.create`。  
**`redisson-spring-boot-starter`** 与 Spring Data Redis 模块配合，自动装配客户端并完成生命周期绑定（细节以 [integration-with-spring.md](../integration-with-spring.md) 为准）。

---

## 2. 项目设计（大师 × 小白）

**小白**：我全家桶 Spring，还要自己 `Redisson.create`？  
**大师**：日常 **Starter 注入**；只有 **多集群、多租户、运行时换 Config** 才需要自定义 `RedissonClient` Bean 或底层 API。

**小白**：Starter 和手写 `create` 能共存吗？  
**大师**：技术上能，组织上 **极易双客户端互踩 key**。要么 **统一入口**，要么 **书面 key 空间条约**（见第十二章导览「与 Spring Data Redis 共存」）。

---

## 3. 项目实战（主代码片段）

**依赖**（Community，版本与 Spring Boot 对齐，见官方矩阵）：

```xml
<dependency>
    <groupId>org.redisson</groupId>
    <artifactId>redisson-spring-boot-starter</artifactId>
    <version><!-- 与 BOM 对齐 --></version>
</dependency>
```

**配置**：常用方式之一是指向 **独立 `redisson.yaml`**（字段以官方为准）：

```yaml
spring:
  redis:
    redisson:
      file: classpath:redisson.yaml
```

**使用**：在任意 Bean 中注入：

```java
import org.redisson.api.RedissonClient;
import org.springframework.stereotype.Service;

@Service
public class OrderService {

    private final RedissonClient redisson;

    public OrderService(RedissonClient redisson) {
        this.redisson = redisson;
    }

    public void demo() {
        redisson.getBucket("demo").set("ok");
    }
}
```

**要点**：Bean **destroyMethod / 生命周期** 由 Starter 处理；升级 Spring Boot 大版本时核对 **`redisson-spring-data-*`** 兼容表（见 [integration-with-spring.md](../integration-with-spring.md)）。

---

## 4. 项目总结

| 维度 | 说明 |
|------|------|
| **优点** | **零样板**接入；配置外置；与 Spring **生命周期一致**；便于多环境。 |
| **缺点** | 复杂拓扑仍要 **读懂生成的 Config**；错误依赖版本会导致 **启动期失败**。 |
| **适用场景** | 标准 Spring Boot 服务使用 Redisson 的 **默认路径**。 |
| **注意事项** | **单例 Client**；不要在请求作用域反复创建；敏感信息走 **配置中心 / 密钥管理**。 |
| **常见踩坑** | 与 Lettuce/Jedis **双连同一 Redis** 且无 key 约定；**未读兼容表**强升 Boot；本地能连、容器不能连 **DNS/网络策略** 未排查。 |

---

## 本章实验室（约 30～45 分钟）

**环境**：最小 Spring Boot + `redisson-spring-boot-starter`；`application-{profile}.yml` 至少 **dev / test** 两套 Redis 地址（可用同一实例不同 **key 前缀** 区分）。

### 步骤

1. **dev** profile 启动，`OrderService`（或 `@Component`）里 `redisson.getBucket("lab:starter:env").set("dev")`。  
2. 切换 **test** profile 重启，写入 `"test"`，用 `redis-cli` **SCAN** 找到 key，确认 **前缀或命名空间** 符合预期（若使用 `nameMapper` 一并验证）。  
3. **优雅停机**：发送 SIGTERM（IDE Stop 或 `kill`），前后 `CLIENT LIST`（若有权限）对比 **连接是否释放**。  
4. 故意将 `redisson.yaml` 中地址改为 **不可达 IP**，收集 **完整启动栈**，标出 **第一条根因行**。

### 验证标准

- 两 profile **各写成功** 且 key **不互相覆盖**（若设计为应覆盖则说明理由）。  
- 有一份 **错误配置** 启动失败日志样本。

### 记录建议

- 团队 Wiki：**Spring Boot 大版本 ↔ redisson-spring-data-* 兼容行** 粘贴链接。

**上一章**：[第十一章（分篇四）Live Object](28-LiveObject.md)｜[第十一章导览](24-分布式服务.md)｜**下一篇**：[第十二章（分篇二）Spring Cache](31-SpringCache.md)｜**下一章**：[第十三章导览](33-框架矩阵速览.md)
