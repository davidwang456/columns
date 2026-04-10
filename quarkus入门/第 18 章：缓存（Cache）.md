# 第 18 章：缓存（Cache）

## 0. 课程卡片

| 项目 | 说明 |
|------|------|
| **建议课时** | 45 分钟 |
| **学习目标** | 使用 `quarkus-cache`；配置 Caffeine；说明进程内缓存与多副本一致性 |
| **先修** | 第 4、16 章 |

---

## 1. 项目背景

读多写少场景用缓存降低延迟与 DB 压力。**进程内缓存**在 K8s 多副本下**彼此不一致**，需业务容忍或使用分布式缓存（Redis 等，另专题）。

---

## 2. 项目设计：大师与小白的对话

**小白**：「加 `@CacheResult` 就行？」

**大师**：「还要 **TTL、key、淘汰策略**，以及**失效**时业务是否可接受旧数据。」

**测试**：「我怎么测缓存命中？」

**大师**：「打两次相同请求，第二次应更快；或用 metrics（若暴露）。」

**运维**：「缓存导致内存涨？」

**大师**：「`maximum-size` 与对象大小要估算。」

---

## 3. 知识要点

- `@CacheResult` / `@CacheInvalidate`  
- `quarkus.cache.caffeine."cache-name".*` 配置

---

## 4. 项目实战

### 4.1 `pom.xml`

```xml
<dependency>
  <groupId>io.quarkus</groupId>
  <artifactId>quarkus-cache</artifactId>
</dependency>
<dependency>
  <groupId>io.quarkus</groupId>
  <artifactId>quarkus-rest</artifactId>
</dependency>
```

### 4.2 `application.properties`

```properties
quarkus.cache.caffeine."books".maximum-size=500
quarkus.cache.caffeine."books".expire-after-access=5M
```

### 4.3 服务

`src/main/java/org/acme/CatalogService.java`：

```java
package org.acme;

import io.quarkus.cache.CacheResult;
import jakarta.enterprise.context.ApplicationScoped;

@ApplicationScoped
public class CatalogService {

    @CacheResult(cacheName = "books")
    public String titleById(String id) {
        // 模拟昂贵调用
        return "Book-" + id;
    }
}
```

### 4.4 Kubernetes：无强制 YAML

缓存为进程内；若用 Redis 可追加 `Deployment` env 指向 Redis Service。

---

## 5. 课堂实验

| 步骤 | 操作 | 预期结果 |
|------|------|----------|
| 1 | 资源中两次调用同一 id | 第二次命中缓存（可加日志计数） |
| 2 | 调小 `maximum-size`，压测大量 key | 观察淘汰 |
| 3 | 讨论：三副本时用户 A 打到 Pod1 更新数据，Pod2 仍旧 | 理解最终一致 |

---

## 6. 项目总结

| 维度 | 说明 |
|------|------|
| **优点** | 接入快。 |
| **缺点** | 多副本不一致。 |
| **适用场景** | 读多、可容忍短不一致。 |
| **注意事项** | key 设计；内存。 |
| **常见踩坑** | 把强一致当目标；缓存 null。 |

**延伸阅读**：<https://quarkus.io/guides/cache>
