# 第36章：EventBus、Audit 与 Webhook 源码剖析

## 1. 项目背景

云鲸科技的架构组在第 34 和 35 章追踪了上传和下载链路后，发现了一个悬而未决的问题——为什么某些组件的删除操作在 audit.log 中有记录，但对应的 Webhook 却没有发送？反之，有一些组件的上传触发了 Webhook，但是在高并发场景下，Webhook 接收端偶尔收到重复的事件。

小李在 `WebhookServiceImpl` 中设了断点，发现 Webhook 的发送逻辑在一个独立的线程池中执行——但它的触发条件是"Guava EventBus 上发布了特定类型的 Event"。而 EventBus 的订阅者注册在 `GlobalAuditWebhook` 类中——这个类标注了 `@Subscribe` 注解，监听 `AuditDataRecordedEvent` 事件。"所以 Webhook 的发送可靠性完全依赖于 EventBus 的事件投递可靠性，"小李得出结论，"而 EventBus 的默认行为是同步投递——如果订阅者抛异常了，事件就丢失了。"

更深层的问题是——Nexus 的整个"事件驱动"体系（审计日志记录、Webhook 通知、索引更新、缓存刷新）都建立在 Guava EventBus 之上。理解 EventBus 的投递语义（同步 vs 异步）、订阅者的注册时机、以及 Webhook 的构造和签名逻辑，是排查"事件丢失"和"重复通知"问题的前提。

本章将剖析从"一次上传操作"到"审计日志写入 + Webhook 发送"的完整事件链路，理解 EventBus 在 Nexus 中的使用方式、Audit 事件的构造与传递、Webhook payload 的序列化与签名，以及工程上如何处理"at-least-once"语义带来的幂等性挑战。

## 2. 项目设计

小李把一张画满箭头的事件流转图投影出来，大师逐层补充。

**小李**："大师，我梳理出了事件的触发和传播路径——上传一个 jar → `StorageFacet.put()` 完成后调用 `eventBus.post(new AssetCreatedEvent(...))` → `AuditDataRecordedEvent` 被 `GlobalAuditWebhook.on()` 订阅 → `WebhookServiceImpl` 用线程池异步发送 HTTP POST 到外部 URL。这条链路里，如果中间任何一步失败了会怎样？"

**大师**："分情况。**EventBus 投递阶段**——Guava EventBus 默认同步调用订阅者。如果 `@Subscribe` 方法抛异常，异常会传播回 `eventBus.post()` 的调用者——在上传场景中就是 `StorageFacet`。Nexus 的做法是——在 `eventBus.post()` 外套了一个 `try-catch`，防止审计事件处理失败导致上传本身失败。这意味着**事件投递失败是静默的**——上传成功了，但审计记录可能丢失。**Webhook 发送阶段**——`WebhookServiceImpl` 在独立的线程池中执行 HTTP 请求，有重试机制。但如果重试全部失败，事件永久丢失。"

> **技术映射**：Nexus 事件链路的可靠性策略 = EventBus 同步投递（vs 异步队列）+ try-catch 保护业务操作 + Webhook 异步重试。不保证 100% 事件不丢失，但保证事件丢失不影响核心业务。

**小胖**："那重复事件呢？你们刚才说高并发下收到重复的 Webhook。"

**大师**："重复事件的根因在 Webhook 的重试机制。`WebhookServiceImpl` 内置了指数退避重试——1 分钟、5 分钟、15 分钟……每次重试都是一个新的 HTTP POST 请求，带同样的 payload 但不同的 `X-Nexus-Webhook-Delivery` ID。如果接收端处理成功但 HTTP 响应因网络原因未送达 Nexus——Nexus 认为失败，重试；接收端认为成功，但收到重复的 payload。解决方案是接收端基于 `X-Nexus-Webhook-Delivery` 做幂等去重——处理过的 delivery ID 不再处理。"

**小白**："审计日志（audit.log）的事件是怎么被触发的？不是 Webhook 触发它，而是它也依赖 EventBus？"

**大师**："对。审计日志有两个层次的记录。**第一层——audit.log 文件**：由 `AuditRecorder` 实现，它也订阅 `AuditDataRecordedEvent`，但优先级在 Webhook 之前。审计记录写入是**同步**的——在 EventBus 的同一次投递中先写审计日志，再触发 Webhook。**第二层——数据库审计**：`AuditStore` 将审计数据持久化到 OrientDB 中，用于 PRO 版的审计页面查询。OSS 版没有数据库审计，只有文件审计。"

> **技术映射**：审计事件订阅链 = AuditRecorder（audit.log 文件写入，同步）→ GlobalAuditWebhook（Webhook 触发，异步线程池）。文件写入失败不影响 Webhook 发送，反之亦然。

**小李**："那 Webhook 的 HMAC-SHA1 签名具体怎么算的？secret 存在哪？"

**大师**："Webhook 创建时配置的 secret 被加密存储在 OrientDB 中。签名的计算过程是——`WebhookServiceImpl` 取 payload JSON 字符串的 UTF-8 字节，用 secret 做 HMAC-SHA1，结果转为十六进制字符串，放在 `X-Nexus-Webhook-Signature` 头中。注意——签名是针对 payload body 计算的，**不包括** URL 和 header。所以接收端也需要用相同的 payload body 和 secret 重新计算来验证。"

## 3. 项目实战

### 3.1 环境准备

- Nexus 源码已导入 IDE
- Nexus 实例运行中
- 已创建至少一个 Webhook（参考第 27 章）

### 3.2 分步实战

#### 步骤一：定位 EventBus 的核心组件

**目标**：找到 Nexus 中 EventBus 的创建、订阅者注册和事件发布的关键代码。

```bash
# 搜索 Guava EventBus 的使用
grep -r "EventBus\|@Subscribe\|AsyncEventBus" --include="*.java" components/nexus-core/src/main/java/ | head -15

# 关键类：
# 1. EventBus 创建: EventBusModule.java
# 2. 订阅者基类: Subscriber.java（Nexus 抽象）
# 3. Webhook 订阅者: GlobalAuditWebhook.java
# 4. 审计订阅者: AuditRecorderImpl.java
```

**EventBus 初始化（简化示意）**：

```java
// EventBusModule.java — EventBus 的创建和配置
@Named
public class EventBusModule extends AbstractModule {
    
    @Provides
    @Singleton
    public EventBus provideEventBus() {
        // Nexus 使用 Guava 的 EventBus（同步投递）
        EventBus eventBus = new EventBus("nexus");
        // 订阅者将通过 @Subscribe 注解自动注册
        return eventBus;
    }
}
```

**订阅者注册（简化示意）**：

```java
// GlobalAuditWebhook.java — Webhook 的订阅者
@Named
@Singleton
public class GlobalAuditWebhook {
    
    @Inject
    private WebhookService webhookService;
    
    @Subscribe  // ← Guava EventBus 的订阅注解
    public void on(AuditDataRecordedEvent event) throws Exception {
        // 构造 Webhook payload
        AuditWebhookPayload payload = buildPayload(event);
        // 委托 WebhookService 发送
        webhookService.queue(payload);
    }
}
```

#### 步骤二：追踪一次仓库创建到 Webhook 发送的完整链路

**目标**：以创建仓库操作为例，验证事件从产生到 Webhook 发送的全过程。

```bash
# 创建测试仓库触发全局 Webhook（需预先创建好 global webhook）
curl -u admin:admin123 -X POST \
  "http://localhost:8081/service/rest/v1/repositories/raw/hosted" \
  -H "Content-Type: application/json" \
  -d '{"name":"test-eventbus-36","online":true,"storage":{"blobStoreName":"default","writePolicy":"ALLOW"}}'

# 1. 检查 audit.log 中是否有对应记录
docker compose exec nexus grep "test-eventbus-36" /nexus-data/log/audit/audit.log | tail -3

# 预期格式：
# {
#   "domain": "repository",
#   "type": "repository",
#   "context": "test-eventbus-36",
#   "attributes": {"action": "CREATED", "repository.name": "test-eventbus-36", "repository.format": "raw"},
#   "initiator": "admin",
#   "timestamp": "2025-06-15T10:30:15.123Z"
# }

# 2. 检查 Webhook 发送日志
docker compose exec nexus grep -i "webhook" /nexus-data/log/nexus.log | grep "test-eventbus-36"
```

**事件流转完整路径**：

```
HTTP 请求: POST /v1/repositories/raw/hosted
  → RepositoryResource.createRepository()
    → RepositoryManagerImpl.create()
      → eventBus.post(new RepositoryCreatedEvent(repository))
        ↓ (同步调用)
        → AuditRecorderImpl.on(RepositoryCreatedEvent)
          → 写入 audit.log（JSON 一行）
        → GlobalAuditWebhook.on(RepositoryCreatedEvent)
          → 构造 RepositoryWebhookPayload
          → webhookService.queue(payload)
            → 异步线程池
              → HTTP POST → 外部 URL
                → HMAC-SHA1 签名 → X-Nexus-Webhook-Signature 头
```

#### 步骤三：观察 EventBus 的同步特性导致的阻塞问题

**目标**：通过实验验证 EventBus 同步投递对业务操作的影响。

```bash
echo "=== EventBus 同步投递实验 ==="

# 场景：如果 Webhook 接收端响应很慢（模拟 10 秒延迟）
# 观察 Nexus 的仓库创建操作是否被阻塞
echo ""
echo "问题: 如果 Webhook 接收端响应 10 秒，createRepository() 会等 10 秒吗？"
echo ""
echo "答案: 不会——因为 Webhook 的发送是在独立的线程池中异步执行的。"
echo "      但 audit.log 的写入是同步的——如果磁盘 IO 极慢，可能阻塞。"
echo ""
echo "Nexus 的设计取舍:"
echo "  - audit.log 写入: 同步（确保审计记录不丢失）"
echo "  - Webhook 发送: 异步（避免外部系统拖慢核心业务）"
echo "  - 索引更新: 异步（搜索索引不要求实时一致）"
```

#### 步骤四：解析 Webhook Payload 的构造和签名

**目标**：理解 Webhook 的 payload JSON 如何构造、如何签名。

```java
// WebhookServiceImpl 签名逻辑简化示意
public class WebhookServiceImpl implements WebhookService {
    
    private static final String HMAC_ALGO = "HmacSHA1";
    private ExecutorService executor; // 独立线程池
    
    @Override
    public void queue(WebhookRequest request) {
        executor.submit(() -> {
            try {
                // 1. 序列化 payload 为 JSON
                String payloadJson = objectMapper.writeValueAsString(request.getPayload());
                
                // 2. 计算 HMAC-SHA1 签名
                Mac mac = Mac.getInstance(HMAC_ALGO);
                mac.init(new SecretKeySpec(request.getSecret().getBytes(UTF_8), HMAC_ALGO));
                String signature = HexFormat.of().formatHex(mac.doFinal(payloadJson.getBytes(UTF_8)));
                
                // 3. 发送 HTTP POST
                HttpPost post = new HttpPost(request.getUrl());
                post.setHeader("Content-Type", "application/json");
                post.setHeader("X-Nexus-Webhook-ID", request.getWebhookId());
                post.setHeader("X-Nexus-Webhook-Delivery", UUID.randomUUID().toString());
                post.setHeader("X-Nexus-Webhook-Signature", signature);
                post.setEntity(new StringEntity(payloadJson, UTF_8));
                
                // 4. 执行 HTTP 请求（带重试）
                executeWithRetry(post, request.getRetries());
            } catch (Exception e) {
                log.warn("Webhook delivery failed: {}", request.getWebhookId(), e);
            }
        });
    }
}
```

#### 步骤五：用 Spring Boot 接收端验证 payload 签名

**目标**：在接收端独立验证 HMAC-SHA1 签名，确保没有被篡改。

```java
// 接收端签名验证（简化示意，见第 27 章完整代码）
@PostMapping("/nexus/events")
public ResponseEntity<String> handle(
    @RequestBody String payload,
    @RequestHeader("X-Nexus-Webhook-Signature") String signature,
    @RequestHeader("X-Nexus-Webhook-Delivery") String deliveryId) {
    
    // 1. 幂等检查
    if (processedDeliveries.contains(deliveryId)) {
        return ResponseEntity.ok("Duplicate");
    }
    
    // 2. 签名验证
    Mac mac = Mac.getInstance("HmacSHA1");
    mac.init(new SecretKeySpec(WEBHOOK_SECRET.getBytes(), "HmacSHA1"));
    String expected = HexFormat.of().formatHex(mac.doFinal(payload.getBytes()));
    
    if (!expected.equals(signature)) {
        log.warn("Webhook signature mismatch! Possible tampering.");
        return ResponseEntity.status(403).body("Invalid signature");
    }
    
    // 3. 处理事件
    processedDeliveries.add(deliveryId);
    processEvent(payload);
    return ResponseEntity.ok("OK");
}
```

### 3.3 常见坑点

| 坑点 | 现象 | 解决方法 |
|------|------|----------|
| EventBus 订阅者未注册 | 事件发布后无响应 | 确保订阅者 class 被 `@Named` + `@Singleton` 标注并被 Sisu 扫描 |
| Webhook 接收端收到重复事件 | 同一 deliveryId 被多次投递 | 接收端基于 `X-Nexus-Webhook-Delivery` 做幂等去重 |
| audit.log 和 Webhook 数据不一致 | 日志有记录但 Webhook 没收到 | 事件投递是"尽力而为"——检查 `GlobalAuditWebhook` 的 `@Subscribe` 方法是否抛异常 |
| 事件在 `@Subscribe` 中抛异常导致业务回滚 | 上传成功但事件处理抛异常 → 上传被回滚 | EventBus 默认行为——需要确保订阅者 catch 自己的异常 |

## 4. 项目总结

### 4.1 事件链路速查

| 组件 | 职责 | 投递方式 | 丢失风险 | 关键注解 |
|------|------|---------|---------|---------|
| EventBus | 事件发布/订阅总线 | 同步（默认） | 订阅者异常 → 事件丢失 | `@Subscribe` |
| AuditRecorder | 审计日志写入（audit.log） | 同步 | 磁盘满导致写入失败 | `@Subscribe` |
| GlobalAuditWebhook | Webhook 触发（全局审计） | 异步线程池 | 重试耗尽后丢失 | `@Subscribe` |
| WebhookServiceImpl | HTTP 发送 + HMAC 签名 + 重试 | 异步线程池 | 重试耗尽后丢失 | — |

### 4.2 适用场景

1. **排查"Webhook 漏发"**：从 EventBus 投递到 HTTP 发送逐步排查
2. **事件驱动插件开发**：利用 EventBus 订阅内部事件，扩展 Nexus 功能
3. **审计系统对接**：通过订阅 `AuditDataRecordedEvent` 将审计事件转发到外部系统
4. **自定义通知**：订阅 `ComponentCreatedEvent` / `ComponentDeletedEvent` 发送自定义通知格式

**不适用场景**：
1. 需要保证"精确一次"的事件投递——EventBus 不提供事务性和持久化
2. 高性能事件流（每秒万级）——用 Kafka/Pulsar 外部事件总线代替

### 4.3 注意事项

- **事件订阅者的执行顺序不保证**：多个 `@Subscribe` 方法订阅同一事件时，调用顺序不确定
- **不要阻塞 EventBus 线程**：耗时的订阅者应该自己在内部使用线程池异步处理
- **Webhook secret 丢失后无法恢复**：只能重新创建 Webhook
- **EventBus 的生命周期随 Nexus 关闭**：Nexus shutdown 时未完成的事件处理会被中断

### 4.4 思考题

1. 当前 Nexus 的 EventBus 是同步的——如果审计日志写入磁盘耗时 200ms，上传请求的响应时间就增加了 200ms。设计一个"审计日志写入异步化同时保证不丢失"的方案——用内存队列 + 异步刷盘的方式。需要处理哪些边界情况（如 Nexus 崩溃时队列中未刷盘的事件）？
2. `WebhookServiceImpl` 的重试策略是固定的指数退避。如果需要支持"按 Webhook 的 type 配置不同的重试策略"——比如 Repository Webhook 重试 3 次后放弃，Audit Webhook 重试 10 次——在不修改 Nexus 源码的情况下，能否通过外部手段实现差异化重试？

（第35章思考题答案：1. 包含。group 的 `maven-metadata.xml` 合并逻辑会遍历所有成员仓库——不管其 versionPolicy 是 RELEASE 还是 SNAPSHOT。`<versions>` 列表包含所有成员仓库中出现的所有版本名——所以 SNAPSHOT 仓库中带 `-SNAPSHOT` 后缀的版本也会出现。`<latest>` 和 `<release>` 只取第一个成员仓库的值（`maven-releases` 中不含 SNAPSHOT 版本，所以 SNAPSHOT 不会成为 latest/release）。2. Nexus 的 Proxy Handler 在处理条件请求时，会向远程仓库发送 `If-Modified-Since` 和 `If-None-Match` 请求头。如果远程返回 `304 Not Modified`，Nexus 不会把这个 `304` 返回给客户端——而是从本地缓存中取出内容，以 `200` 状态码返回给客户端。`304` 只在 Nexus 和远程仓库之间的通信中使用，对客户端是透明的——客户端始终看到 `200` + 完整文件内容。）

### 4.5 推广计划提示

- **核心开发**：以 `GlobalAuditWebhook.on()` 为起点，在 IDE 中走一遍从 EventBus 发布到 HTTP 发送的完整路径
- **安全团队**：验证 Webhook HMAC 签名的正确性，防止伪造事件注入
- **架构组**：评估当前 EventBus 同步投递对上传响应时间的影响，考虑是否需要引入异步审计写入
