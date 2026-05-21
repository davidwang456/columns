# 第27章：Webhook 实战：事件驱动制品治理

## 1. 项目背景

云鲸科技的安全团队在上周的安全事件复盘中提出了一个尖锐的问题："为什么交易团队的 CI 机器人账号异地登录、删除了 3 个生产仓库的制品，从发生到被发现花了整整 6 个小时？"炮哥解释说 audit.log 中有记录但没有人实时盯着日志看。安全总监直接拍了桌子："我要的是——制品被删除的 30 秒内，运维和开发组长同时收到告警通知。能不能做到？"

同一个周五，测试组的阿玲提出了另一个需求："每次有新版本 `@cloudwhale/ui-components` 发布到 Nexus，我们的自动化回归测试套件要能自动触发执行。现在是我每隔一小时手动查一次——人肉 Webhook。"前端组长吴凡也补充道："我们想做依赖安全扫描——每当 npm proxy 缓存了一个新的第三方包，自动触发 Snyk 扫描。靠轮询 API 太慢了。"

这三个需求的共同点是：**不要轮询（Pull），要推送（Push）**。Nexus 的 Webhook 机制正是为此设计的——当特定事件发生时（组件上传、资产删除、仓库变更、审计事件），Nexus 主动向外部 URL 发送 HTTP POST 请求，携带结构化的 payload 数据，让外部系统即时响应。本章将全面解析 Global Webhook、Repository Webhook、Audit Webhook 的创建、payload 结构、签名验证和接收端设计，并实现一个 Spring Boot 接收端将 Nexus 事件接入企业事件总线。

## 2. 项目设计

大师在会议室的白板上画了三层事件流，安全总监和前端组长围坐在前。

**大师**："Nexus Webhook 分两大类。第一类——**Repository Webhook**，绑定到单个仓库，当仓库内有组件/资产的操作（创建、更新、删除）时触发。第二类——**Global Webhook**，绑定到整个 Nexus 实例，当仓库本身被创建/更新/删除时触发，Audit Webhook（审计事件）也属于 Global 级别。"

**小胖**："那 Webhook 发送的 payload 里有什么？不会是空的吧？"

**大师**："四类标准 payload——**Repository payload**（仓库的 name/format/type/online 状态）、**Component payload**（组件的 GAV/name/version 及其所属仓库）、**Asset payload**（文件的 path/contentType/downloadUrl/checksum）、**Audit payload**（domain/type/context/attributes，和 audit.log 同结构）。payload 是一个 JSON 对象，`action` 字段标记是 `CREATED`、`UPDATED` 还是 `DELETED`。"

> **技术映射**：Webhook payload = 事件的 JSON 序列化快照，包含触发事件的资源对象 + 操作类型（action）。接收端需要基于 `action` 字段做路由分发。

**安全总监**："安全方面呢？外部系统怎么确认这个 Webhook 真的是 Nexus 发的，不是伪造的？"

**大师**："Nexus Webhook 支持 HMAC-SHA1 签名验证。创建 Webhook 时配置一个 secret，Nexus 会用这个 secret 对 payload 计算 HMAC-SHA1，把签名结果放在 `X-Nexus-Webhook-Signature` 请求头中。接收端用同样的 secret 重新计算签名，与请求头比对——一致则放行，不一致则拒绝。另外，`X-Nexus-Webhook-ID` 和 `X-Nexus-Webhook-Delivery` 是防重放的两个关键头。"

**小白**："接收端怎么保证可靠处理？Nexus 会重试失败的 Webhook 吗？"

**大师**："Nexus 有内置重试机制——如果接收端返回非 2xx 状态码，Nexus 会按指数退避重试（1 分钟→5 分钟→15 分钟→1 小时），最多重试一定次数后标记为失败。但**不保证恰好一次投递**（at-least-once），所以接收端必须实现幂等——基于 `X-Nexus-Webhook-Delivery` 去重。"

> **技术映射**：Webhook 可靠性 = HMAC-SHA1（防伪造）+ Delivery ID（幂等去重）+ 指数退避重试（at-least-once 语义）。接收端需要处理重复投递。

**阿玲**："测试触发——我想在 `npm-hosted` 仓库收到新组件时触发回归测试。怎么过滤只监听 `@cloudwhale/ui-components` 而不是所有 npm 包？"

**大师**："Repository Webhook 的 payload 中包含 `component.name` 和 `component.group`——在你的接收端中解析 payload JSON，用 `component.name == "@cloudwhale/ui-components"` 做过滤。如果事件不是你关注的，直接返回 200（但不做任何处理），避免 Nexus 重试。"

## 3. 项目实战

### 3.1 环境准备

- 已部署 Nexus 实例
- JDK 17+、Maven 3.8+（用于编写 Webhook 接收端）
- curl、jq
- 可选：ngrok（用于本地接收端接收公网 Webhook 测试）

### 3.2 分步实战

#### 步骤一：创建 Repository 级和 Global 级 Webhook

**目标**：创建 Webhook，验证不同级别的作用范围。

```bash
NEXUS="http://localhost:8081"
AUTH="admin:admin123"
WEBHOOK_SECRET="my-secret-key-change-me"
RECEIVER_URL="http://webhook-receiver.internal:8080/nexus/events"

# 1. 创建 Repository Webhook（绑定到 maven-releases）
curl -u $AUTH -X POST "$NEXUS/service/rest/v1/webhook/repository" \
  -H "Content-Type: application/json" \
  -d "{
    \"name\": \"wh-maven-releases\",
    \"repositoryName\": \"maven-releases\",
    \"webhook\": {
      \"url\": \"${RECEIVER_URL}\",
      \"secret\": \"${WEBHOOK_SECRET}\",
      \"contentType\": \"application/json\"
    },
    \"enabled\": true,
    \"eventTypes\": [\"COMPONENT\", \"ASSET\"]
  }"

# 2. 创建 Global Webhook（监听仓库变更和审计事件）
curl -u $AUTH -X POST "$NEXUS/service/rest/v1/webhook/global" \
  -H "Content-Type: application/json" \
  -d "{
    \"name\": \"wh-global-audit\",
    \"webhook\": {
      \"url\": \"${RECEIVER_URL}/audit\",
      \"secret\": \"${WEBHOOK_SECRET}\",
      \"contentType\": \"application/json\"
    },
    \"enabled\": true,
    \"eventTypes\": [\"REPOSITORY\", \"AUDIT\"]
  }"

# 3. 查看已创建的 Webhook
curl -u $AUTH "$NEXUS/service/rest/v1/webhook/repository" | jq '.[] | {name, repositoryName, enabled}'
curl -u $AUTH "$NEXUS/service/rest/v1/webhook/global" | jq '.[] | {name, enabled, eventTypes}'
```

#### 步骤二：测试 Webhook 触发并观察 payload

**目标**：上传一个测试制品，观察 Webhook 的完整请求内容。

```bash
# 1. 启动一个简易 HTTP 接收端（用 netcat 或 ncat 监听）
# 在另一个终端中：
# nc -l -p 8080 或 python -m http.server 8080
# （这仅用于观察原始 HTTP 请求，正式接收端用 Spring Boot）

# 2. 上传测试制品到 maven-releases，触发 COMPONENT Webhook
echo "webhook-test" > /tmp/webhook-test.jar
curl -u $AUTH -X PUT \
  "http://localhost:8081/repository/maven-releases/com/test/webhook/1.0/webhook-1.0.jar" \
  --data-binary @/tmp/webhook-test.jar

# 3. 观察 Nexus 的 Webhook 发送日志
docker compose exec nexus tail -20 /nexus-data/log/nexus.log | grep -i webhook

# 预期日志示例：
# DEBUG ... WebhookServiceImpl - Sending webhook 'wh-maven-releases' to http://receiver:8080/nexus/events
# DEBUG ... WebhookServiceImpl - Response from http://receiver:8080/nexus/events: 200 OK
```

**典型 COMPONENT payload 结构**：

```json
{
  "action": "CREATED",
  "timestamp": "2025-06-15T10:30:15.123Z",
  "repositoryName": "maven-releases",
  "format": "maven2",
  "component": {
    "id": "abc123",
    "repository": "maven-releases",
    "format": "maven2",
    "group": "com.test",
    "name": "webhook",
    "version": "1.0"
  },
  "assets": [
    {
      "id": "def456",
      "path": "com/test/webhook/1.0/webhook-1.0.jar",
      "downloadUrl": "http://nexus:8081/repository/maven-releases/com/test/webhook/1.0/webhook-1.0.jar",
      "checksum": { "sha1": "...", "md5": "..." }
    }
  ]
}
```

#### 步骤三：Spring Boot Webhook 接收端实现

**目标**：编写一个带签名验证、幂等去重和事件分发的接收端。

```java
// NexusWebhookReceiverApplication.java
@SpringBootApplication
@RestController
public class NexusWebhookReceiverApplication {

    private static final String WEBHOOK_SECRET = "my-secret-key-change-me";
    // 去重缓存（已处理的 delivery ID）
    private final Set<String> processedDeliveries = ConcurrentHashMap.newKeySet();

    public static void main(String[] args) {
        SpringApplication.run(NexusWebhookReceiverApplication.class, args);
    }

    @PostMapping("/nexus/events")
    public ResponseEntity<String> handleWebhook(
            @RequestBody String payload,
            @RequestHeader("X-Nexus-Webhook-Signature") String signature,
            @RequestHeader("X-Nexus-Webhook-ID") String webhookId,
            @RequestHeader("X-Nexus-Webhook-Delivery") String deliveryId) {

        // 1. 签名验证
        if (!verifySignature(payload, signature)) {
            return ResponseEntity.status(403).body("Invalid signature");
        }

        // 2. 幂等去重
        if (!processedDeliveries.add(deliveryId)) {
            return ResponseEntity.ok("Duplicate delivery, skipped");
        }

        // 3. 解析事件并处理
        try {
            JsonNode event = new ObjectMapper().readTree(payload);
            String action = event.get("action").asText();
            String repoName = event.get("repositoryName").asText();

            // 按 action 分发处理
            switch (action) {
                case "CREATED":
                    handleComponentCreated(event);
                    break;
                case "DELETED":
                    handleComponentDeleted(event);
                    break;
            }

            log.info("Webhook processed: {} | {} | {}", webhookId, deliveryId, action);
            return ResponseEntity.ok("OK");
        } catch (Exception e) {
            log.error("Failed to process webhook", e);
            return ResponseEntity.status(500).body("Processing error");
        }
    }

    private boolean verifySignature(String payload, String signature) {
        try {
            Mac mac = Mac.getInstance("HmacSHA1");
            mac.init(new SecretKeySpec(WEBHOOK_SECRET.getBytes(), "HmacSHA1"));
            byte[] computed = mac.doFinal(payload.getBytes());
            String computedHex = HexFormat.of().formatHex(computed);
            return computedHex.equals(signature);
        } catch (Exception e) {
            return false;
        }
    }

    private void handleComponentDeleted(JsonNode event) {
        // 高危操作：发送告警到企业微信/钉钉
        String componentName = event.get("component").get("name").asText();
        String repoName = event.get("repositoryName").asText();
        if (repoName.contains("production") || repoName.contains("prod")) {
            sendAlert("生产仓库制品被删除: " + componentName);
        }
    }

    private void handleComponentCreated(JsonNode event) {
        String componentName = event.get("component").get("name").asText();
        // 如果是 ui-components，触发回归测试
        if ("@cloudwhale/ui-components".equals(componentName)) {
            triggerRegressionTest(event);
        }
    }

    // 简化示例
    private void sendAlert(String msg) { log.warn("ALERT: {}", msg); }
    private void triggerRegressionTest(JsonNode e) { log.info("Trigger test for {}", e); }
    private static final org.slf4j.Logger log = 
        org.slf4j.LoggerFactory.getLogger(NexusWebhookReceiverApplication.class);
}
```

**`pom.xml` 依赖**：

```xml
<dependencies>
    <dependency>
        <groupId>org.springframework.boot</groupId>
        <artifactId>spring-boot-starter-web</artifactId>
    </dependency>
    <dependency>
        <groupId>com.fasterxml.jackson.core</groupId>
        <artifactId>jackson-databind</artifactId>
    </dependency>
</dependencies>
```

#### 步骤四：验证 Webhook 端到端流程

**目标**：完整走通 Nexus → Webhook → 接收端 → 告警通知的全链路。

```bash
echo "=== Webhook 端到端测试 ==="

# 1. 确保接收端运行（mvn spring-boot:run）
echo "[1] 启动接收端..."

# 2. 上传测试组件触发 Webhook
echo "[2] 触发 COMPONENT CREATED 事件..."
curl -u $AUTH -X PUT \
  "http://localhost:8081/repository/maven-releases/com/test/e2e/1.0/e2e-1.0.jar" \
  --data-binary @/tmp/webhook-test.jar

# 3. 检查接收端日志
echo "[3] 查看接收端日志（应显示 'Webhook processed'）..."

# 4. 删除组件触发 DELETED 告警
echo "[4] 触发 COMPONENT DELETED 事件..."
COMP_ID=$(curl -s -u $AUTH \
  "$NEXUS/service/rest/v1/search?repository=maven-releases&name=e2e&version=1.0" | \
  jq -r '.items[0].id')
curl -u $AUTH -X DELETE "$NEXUS/service/rest/v1/components/$COMP_ID"

# 5. 验证删除告警
echo "[5] 检查接收端日志（应显示 ALERT: 制品被删除）..."
```

### 3.3 常见坑点

| 坑点 | 现象 | 解决方法 |
|------|------|----------|
| Webhook URL 不可达 | Nexus 日志中 Webhook 发送失败 | 确保 Nexus 容器能访问接收端 URL（检查 Docker 网络、防火墙） |
| HMAC 签名验证失败 | 接收端始终拒绝请求 | 确认 secret 在创建 Webhook 和接收端验证时完全一致（无前导/尾随空格） |
| 重复投递引发副作用 | 同一事件处理了两次，触发重复告警 | 接收端用 `ConcurrentHashMap` 或 Redis 实现 `deliveryId` 去重 |
| payload 中文字段乱码 | 组件名称含中文时接收端显示乱码 | Webhook 设置 `contentType: "application/json;charset=UTF-8"` |

## 4. 项目总结

### 4.1 Webhook 类型速查

| 类型 | 绑定范围 | 触发事件 | Payload 主体 |
|------|---------|---------|-------------|
| Repository Webhook | 单个仓库 | COMPONENT、ASSET | `component` + `assets[]` |
| Global Repository Webhook | 全实例 | REPOSITORY | `repository` |
| Global Audit Webhook | 全实例 | AUDIT | `audit`（与 audit.log 同结构） |

### 4.2 适用场景

1. **实时安全告警**：生产仓库制品删除 → 30 秒内通知安全团队
2. **CI 流水线触发**：新版本上传 → 自动触发回归测试
3. **依赖安全扫描**：新包缓存到 proxy → 自动触发 Snyk/Trivy 扫描
4. **跨系统数据同步**：Nexus 事件 → Kafka → 数据平台
5. **合规审计**：关键操作实时写入外部审计数据库

**不适用场景**：
1. 历史数据回溯——Webhook 仅发送实时事件，不覆盖历史
2. 大体积数据传输——payload 中只含元数据（组件坐标），不含实际文件

### 4.3 注意事项

- **内网可达性**：Nexus 必须能访问接收端 URL，Docker 网络环境下注意容器间网络
- **签名 secret 安全存储**：secret 不在代码中硬编码，使用环境变量或 Vault
- **接收端幂等性是必须的**：Nexus 的 Webhook 是 at-least-once 语义
- **Webhook 数量限制**：单个 Nexus 实例建议不超过 50 个活跃 Webhook

### 4.4 思考题

1. 如何在 Spring Boot 接收端中实现一个"死信队列"——当某个特定 Webhook 事件处理超过 3 次重试仍然失败时，将其持久化到数据库供人工处理，同时避免影响其他正常事件的接收？
2. 设计一个方案：使用 Webhook + 对象存储实现"上传即审计快照"。每当有新组件上传到 Nexus，Webhook 触发一个服务自动检查该组件的所有依赖的许可证类型，并生成一份 PDF 审计快照存入 Raw 仓库。

（第26章思考题答案：1. 方案：在 Elasticsearch 上创建 Watcher（Alerting）规则——监控 `domain: "repository" AND action: "DELETED" AND repository: "*production*"` 事件。当命中时，Watcher 通过 Webhook/Email 发送删除详情给审批人。审批人确认后，API 调用 Nexus 恢复或确认删除。但 audit.log 采集有延迟（> 10s），真正实时的方案应走 Nexus Webhook（第 27 章）。2. 减少 READ 审计日志量的方法：在 `logback.xml` 中为 audit logger 单独配置 filter——过滤掉 `action == "READ"` 的记录。或者在 Filebeat 采集端用 `drop_event` processor 丢弃 READ 事件。但合规审计要求所有操作可追溯——更推荐的方案是将 READ 事件写到一个独立的高吞吐日志通道（如 Kafka），与核心审计事件分离。）

### 4.5 推广计划提示

- **安全团队**：基于 Webhook 建立实时告警规则，重点关注生产仓库的 DELETE 和权限变更
- **开发团队**：将自动化测试、安全检查集成到 Webhook 接收端的事件处理链中
- **运维团队**：监控 Webhook 发送失败率，失败率 > 5% 时告警
