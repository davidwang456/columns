# 第26章：自定义SPI实战——自定义事件监听器

## 1 项目背景

某金融科技公司（以下简称"XX金科"）正在冲刺ISO 27001信息安全认证。评审组扔出一份68页的审计核查清单，其中第3.2.7条赫然写着："认证系统必须对所有用户的登录、登出、权限变更操作生成完整的审计日志，审计日志须具备防篡改能力，并能实时推送至SIEM（安全信息和事件管理）系统"。与之配套的还有第3.2.8条——"异常登录行为（如暴力破解）须在5分钟内触发安全告警通知到运维值班人员"。

IT团队翻开Keycloak自带的Events功能页，三分钟后所有人的表情从"这功能不就有了吗"变成了"麻烦了"。Keycloak内置的Event系统存在三个硬伤：第一，事件存储在数据库中——公司的日均活跃用户约80万，高峰时段每分钟产生超过3000条LOGIN事件，`EVENT_ENTITY`表以每天150万条的速度膨胀，不出一个月数据库就扛不住了，运维同事已经开始每周手动执行`DELETE FROM EVENT_ENTITY WHERE EVENT_TIME < ...`的定时清理脚本，但磁盘IO依然被大量写入拖得苦不堪言。第二，Event只记录已配置的"成功"事件——默认配置下登录失败（`LOGIN_ERROR`）、Token刷新失败（`REFRESH_TOKEN_ERROR`）等异常事件全部被丢弃，而恰恰是这些异常事件才是安全审计中最有价值的"信号"。第三，Event没有主动推送能力——安全团队需要监控的登录行为分散在十几个Realm中，唯一的查看方式是让审计员登录Admin Console → 选择Realm → 点击Events → 筛选事件类型 → 手动导出CSV，这显然不满足"实时推送至SIEM"的硬性要求。

痛点的放大效应随业务增长加速显现：数据库膨胀不只是存储成本的问题，`EVENT_ENTITY`与Session、Client、User等核心表共用同一个数据库连接池，大量Event写入操作占用连接资源，间接导致登录认证的P99延迟从120ms飙升至850ms。缺少自定义事件通道意味着大数据团队无法将登录数据灌入Kafka做用户行为分析（如登录时间分布热力图、异地登录关联检测）。ADMIN EVENT和LOGIN EVENT的数据结构不统一，审计人员在做"某管理员在某时刻修改了某用户的角色"这类追溯查询时，需要在两种事件类型之间来回切换，效率极低。

解决思路很明确：Keycloak的EventListenerProvider SPI允许你在不修改源码的前提下，将事件流引导至任意外部系统。本章将实现三个核心能力——Kafka事件上报（解决大数据分析需求）、钉钉实时告警（解决暴力破解通知需求）、ELK结构化日志输出（解决合规审计查询需求）。

---

## 2 项目设计——剧本式交锋对话

**小胖**（端着一杯奶茶，屏幕上开着小区物业监控画面截图）：大师你看，我们小区的摄像头系统多牛啊——有人进门就自动录像，保安坐监控室盯着屏幕就能看到谁在闯门禁。我觉得这跟Keycloak的Event一模一样啊——用户登录就是"进门"，Keycloak记录Event就是"录像"，管理员在Admin Console看Event就是"保安看屏幕"。既然Keycloak都已经有Event了，为啥还要自己写个监听器？是不是过度设计了？

**大师**（放下手中的键盘，拧开保温杯）：小胖，你这个比方抓到皮了，但没碰到肉。保安看屏幕的前提是——他必须坐在监控室里，而且只能看到一个摄像头的画面。你们公司的SIEM系统就像是市政的110指挥中心，它需要同时接入全市所有小区的摄像头，而且不能是"事后人工去物业调录像"——必须是"有人在门口掏刀子的那一刻，110指挥中心的大屏幕上自动弹出告警"。

Keycloak内置的Event机制只能做到"录像存硬盘"（写数据库），和"保安回看录像"（Admin Console手动查询）。但ISO 27001要的是：
1. **实时推送**——事件发生后立即送到SIEM，不是等审计员去翻；
2. **多样化通道**——同一个事件可能要同时发给Kafka（大数据组做行为分析）、Elasticsearch（安全组做日志检索）、钉钉（运维组收告警）；
3. **异常事件的捕获**——成功的登录你不一定每条都关心，但登录失败这件事必须立刻告警，而默认的Event配置下`LOGIN_ERROR`往往被排除在存储范围之外。

事件的真正价值不在"记录"，而在"响应"。EventListenerProvider SPI就是你写一个"看门狗"，在Keycloak每产生一个事件时立刻收到回调，你可以在这个回调里做任何事——发Kafka、调Webhook、写文件、记Redis——而Keycloak自己毫不知情。

> **大师技术映射**：物业监控录像 = Keycloak Event存储在数据库（事后回看）。EventListenerProvider = 给每个摄像头加装一个物联网传感器，检测到异常行为后自动拨110。两者的本质区别是"被动存储"和"主动响应"。

---

**小白**（在笔记本上画了一张事件类型分类图，眉头微皱）：我梳理了一下，发现问题比想象中复杂。Keycloak的事件分为Login Events和Admin Events两大类，Login Events里又有LOGIN、REGISTER、LOGOUT、CODE_TO_TOKEN、REFRESH_TOKEN等几十种——这些事件的触发时机和数据字段都不一样。我查了`EventListenerProvider`接口，只有`onEvent(Event event)`这一个方法。那问题来了：

第一，`onEvent()`是同步调用还是异步调用？如果我在监听器里写了一个Kafka Producer的发送逻辑，而Kafka恰好连接中断了，`producer.send()`阻塞了5秒钟——这会不会导致用户的正常登录也跟着卡5秒？第二，Kafka连接恢复后，中断期间丢失的那些事件，Keycloak会自动补发吗？这关系到"事件不丢失"这个审计合规的底线。

**大师**：这两个问题正好踩到了Event Listener设计中最关键的两个决策点——同步/异步语义和可靠性保障。

先说`onEvent()`的调用模型。Keycloak在认证流程的末端（`AuthenticationManager`中Token签发成功后）调用`session.getKeycloakSessionFactory().createEventListeners()`获取所有注册的`EventListenerProvider`实例，然后逐个调用它们的`onEvent()`方法。调用是在**当前请求线程**中**同步**进行的——这意味着所有Event Listener的`onEvent()`全部执行完毕后，Keycloak才会将HTTP响应返回给客户端。也就是说，如果你在`onEvent()`里用`producer.send().get()`同步等待Kafka的ACK，Kafka连不上，用户的登录操作确实会被卡住。

解决方案有三层递进：最轻量的是**KafkaProducer异步发送**——`producer.send(record, callback)`不等待结果，`onEvent()`几乎零延迟返回，用户的登录不受影响。中间层是**独立线程池**——在`EventListenerProvider`内部维护一个`ExecutorService`，`onEvent()`把事件对象丢进`LinkedBlockingQueue`后立即返回，后台线程池消费队列并发送Kafka。最重量的是**Keycloak Session的Transaction回调**——利用`session.getTransactionManager().enlistAfterCompletion()`注册一个事务提交后的回调，Event Listener的核心逻辑只在事务成功提交后执行，避免了"事件发出去了但用户认证其实失败了（事务回滚）"的尴尬。

关于事件重放：Keycloak本身不会为你缓存和重发事件。你需要在监听器内部自己实现重试和持久化机制。一个常见的轻量方案是：异步发送Kafka时，如果回调收到异常，将事件对象序列化写入本地文件（作为"死信队列"），后台线程定期重试。这是下一章"思考题"的核心话题。

> **大师技术映射**：同步发送Kafka = 银行柜员每办一笔业务就给总部打一个电话汇报，电话占线就放下客户的笔等他打完。异步发送 = 柜员把业务单放进出件箱，后台有人统一寄送总部——当前客户不用等。事务回调 = 只有客户签完字（事务提交）了，才把单据归档——避免"单子寄出去结果客户撕了"。

---

**小胖**（吞下嘴里的饼干，眼睛一亮）：我懂了！那我再加一个有趣的——事件监听器不只是送外部系统，能不能在Keycloak内部做"事件驱动的自动化运维"？比如检测到某用户连续10次登录失败，Keycloak自动把这个用户禁用掉，不给黑客继续试密码的机会？

**大师**：问得好——这正是"事件驱动安全自动化"的经典落地模式。但实现上有一点需要特别小心：如果直接在`onEvent()`里调用`session.users().getUserById().setEnabled(false)`来禁用用户，这样做是写在当前请求的事务内的——如果原认证流程的回滚导致这个禁用操作也被回滚，安全策略就失效了。更稳妥的做法是：在`onEvent()`中只做"判定"（分析失败次数是否超过阈值），真正的"动作"（禁用用户、发送告警）放到事务提交回调中执行，或者通过一个独立的管理员Session执行（即`session.getKeycloakSessionFactory().create()`新建一个Session来操作UserModel）。这样即便原请求回滚，安全动作不受影响。

不过本章先聚焦"告警"——自动禁用用户是策略引擎的职责，在定制Authenticator（第25章）和授权服务（第22章）的组合拳中实现更合适。

> **大师技术映射**：事件驱动自动化 = 烟雾报警器检测到烟雾 → 自动喷淋系统启动 + 拨打119 + 切断电源。三个动作需要三个独立电路（SSR、电话线、断路器），即使烟雾报警器自己故障，备用手动开关也能触发。

---

## 3 项目实战

### 环境准备

| 组件 | 版本/说明 |
|------|----------|
| JDK | 17+ |
| Maven | 3.8+ |
| Keycloak | 26.1.0，基于第2章Docker Compose环境 |
| Docker | 用于本地启动Kafka（可选，若无需Kafka可跳过） |
| curl | API调试工具 |

> **可选环境**：若要完整验证Kafka事件上报，需在本地用Docker启动Kafka：
> ```bash
> docker run -d --name kafka -p 9092:9092 \
>   -e KAFKA_ADVERTISED_LISTENERS=PLAINTEXT://localhost:9092 \
>   apache/kafka:latest
> ```

目标：开发两个EventListenerProvider——一个将事件上报至Kafka，一个检测暴力破解并通过钉钉发送实时告警。

---

### 步骤1：创建Maven项目结构

**目标**：按Keycloak SPI规范搭建项目骨架。

```bash
New-Item -ItemType Directory -Force -Path event-listener-extensions/src/main/java/com/mycompany/keycloak
New-Item -ItemType Directory -Force -Path event-listener-extensions/src/main/resources/META-INF/services
New-Item -ItemType Directory -Force -Path event-listener-extensions/src/test/java/com/mycompany/keycloak
```

最终目录结构：

```
event-listener-extensions/
├── pom.xml
├── src/main/java/com/mycompany/keycloak/
│   ├── KafkaEventListenerProvider.java
│   ├── KafkaEventListenerFactory.java
│   ├── DingTalkAlertEventListenerProvider.java
│   └── DingTalkAlertEventListenerFactory.java
└── src/main/resources/META-INF/services/
    └── org.keycloak.events.EventListenerProviderFactory
```

---

### 步骤2：配置pom.xml

**目标**：引入Keycloak SPI依赖和Kafka/钉钉所需的外部库。

编写 `event-listener-extensions/pom.xml`：

```xml
<project xmlns="http://maven.apache.org/POM/4.0.0"
         xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
         xsi:schemaLocation="http://maven.apache.org/POM/4.0.0
         http://maven.apache.org/xsd/maven-4.0.0.xsd">
    <modelVersion>4.0.0</modelVersion>

    <groupId>com.mycompany.keycloak</groupId>
    <artifactId>event-listener-extensions</artifactId>
    <version>1.0.0</version>
    <packaging>jar</packaging>

    <properties>
        <keycloak.version>26.1.0</keycloak.version>
        <kafka.version>3.6.1</kafka.version>
        <maven.compiler.source>17</maven.compiler.source>
        <maven.compiler.target>17</maven.compiler.target>
        <project.build.sourceEncoding>UTF-8</project.build.sourceEncoding>
    </properties>

    <dependencies>
        <dependency>
            <groupId>org.keycloak</groupId>
            <artifactId>keycloak-core</artifactId>
            <version>${keycloak.version}</version>
            <scope>provided</scope>
        </dependency>
        <dependency>
            <groupId>org.keycloak</groupId>
            <artifactId>keycloak-server-spi</artifactId>
            <version>${keycloak.version}</version>
            <scope>provided</scope>
        </dependency>
        <dependency>
            <groupId>org.keycloak</groupId>
            <artifactId>keycloak-server-spi-private</artifactId>
            <version>${keycloak.version}</version>
            <scope>provided</scope>
        </dependency>
        <dependency>
            <groupId>org.keycloak</groupId>
            <artifactId>keycloak-services</artifactId>
            <version>${keycloak.version}</version>
            <scope>provided</scope>
        </dependency>

        <!-- Kafka -->
        <dependency>
            <groupId>org.apache.kafka</groupId>
            <artifactId>kafka-clients</artifactId>
            <version>${kafka.version}</version>
        </dependency>

        <!-- Jackson for JSON serialization -->
        <dependency>
            <groupId>com.fasterxml.jackson.core</groupId>
            <artifactId>jackson-databind</artifactId>
            <version>2.16.1</version>
        </dependency>

        <!-- Spring RestTemplate for DingTalk webhook -->
        <dependency>
            <groupId>org.springframework</groupId>
            <artifactId>spring-web</artifactId>
            <version>6.1.3</version>
        </dependency>

        <!-- Test -->
        <dependency>
            <groupId>junit</groupId>
            <artifactId>junit</artifactId>
            <version>4.13.2</version>
            <scope>test</scope>
        </dependency>
    </dependencies>

    <build>
        <plugins>
            <plugin>
                <groupId>org.apache.maven.plugins</groupId>
                <artifactId>maven-shade-plugin</artifactId>
                <version>3.5.1</version>
                <executions>
                    <execution>
                        <phase>package</phase>
                        <goals><goal>shade</goal></goals>
                        <configuration>
                            <transformers>
                                <transformer implementation="org.apache.maven.plugins.shade.resource.ServicesResourceTransformer"/>
                            </transformers>
                        </configuration>
                    </execution>
                </executions>
            </plugin>
        </plugins>
    </build>
</project>
```

> **关键解释**：Kafka和Jackson依赖声明为`provided`之外（运行时需打入JAR），使用`maven-shade-plugin`将第三方依赖合并到JAR中，避免依赖缺失。`ServicesResourceTransformer`确保SPI注册文件在shade过程中不被覆盖。

---

### 步骤3：实现Kafka事件监听器Provider

**目标**：编写核心——将Keycloak事件序列化为JSON并异步发送至Kafka。

编写 `KafkaEventListenerProvider.java`：

```java
package com.mycompany.keycloak;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.apache.kafka.clients.producer.KafkaProducer;
import org.apache.kafka.clients.producer.ProducerConfig;
import org.apache.kafka.clients.producer.ProducerRecord;
import org.jboss.logging.Logger;
import org.keycloak.events.Event;
import org.keycloak.events.EventListenerProvider;
import org.keycloak.events.EventType;
import org.keycloak.models.KeycloakSession;

import java.util.*;
import java.util.concurrent.*;

public class KafkaEventListenerProvider implements EventListenerProvider {

    private static final Logger logger = Logger.getLogger(KafkaEventListenerProvider.class);

    private final KafkaProducer<String, String> producer;
    private final String topic;
    private final ObjectMapper mapper;
    private final ExecutorService executor;

    public KafkaEventListenerProvider(String bootstrapServers, String topic) {
        this.topic = topic;
        this.mapper = new ObjectMapper();

        Properties props = new Properties();
        props.put(ProducerConfig.BOOTSTRAP_SERVERS_CONFIG, bootstrapServers);
        props.put(ProducerConfig.KEY_SERIALIZER_CLASS_CONFIG,
                "org.apache.kafka.common.serialization.StringSerializer");
        props.put(ProducerConfig.VALUE_SERIALIZER_CLASS_CONFIG,
                "org.apache.kafka.common.serialization.StringSerializer");
        props.put(ProducerConfig.ACKS_CONFIG, "1");
        props.put(ProducerConfig.RETRIES_CONFIG, 2);
        props.put(ProducerConfig.MAX_IN_FLIGHT_REQUESTS_PER_CONNECTION, 1);
        props.put(ProducerConfig.COMPRESSION_TYPE_CONFIG, "lz4");
        props.put(ProducerConfig.LINGER_MS_CONFIG, 10);

        this.producer = new KafkaProducer<>(props);
        this.executor = Executors.newFixedThreadPool(2, r -> {
            Thread t = new Thread(r, "kafka-event-sender");
            t.setDaemon(true);
            return t;
        });
    }

    @Override
    public void onEvent(Event event) {
        Map<String, Object> eventData = new LinkedHashMap<>();
        eventData.put("type", event.getType() != null ? event.getType().name() : "UNKNOWN");
        eventData.put("realmId", event.getRealmId());
        eventData.put("clientId", event.getClientId());
        eventData.put("userId", event.getUserId());
        eventData.put("sessionId", event.getSessionId());
        eventData.put("ipAddress", event.getIpAddress());
        eventData.put("time", event.getTime());
        eventData.put("error", event.getError());

        Map<String, String> details = event.getDetails();
        if (details != null && !details.isEmpty()) {
            Map<String, String> filteredDetails = new LinkedHashMap<>(details);
            filteredDetails.remove("redirect_uri");
            filteredDetails.remove("code_id");
            eventData.put("details", filteredDetails);
        }

        executor.submit(() -> {
            try {
                String key = (event.getRealmId() != null ? event.getRealmId() : "unknown")
                        + ":" + (event.getUserId() != null ? event.getUserId() : "anonymous");
                String value = mapper.writeValueAsString(eventData);

                ProducerRecord<String, String> record = new ProducerRecord<>(topic, key, value);
                record.headers().add("event_type",
                        (event.getType() != null ? event.getType().name() : "UNKNOWN").getBytes());

                producer.send(record, (metadata, exception) -> {
                    if (exception != null) {
                        logger.errorf("Kafka send failed for event %s: %s",
                                event.getType(), exception.getMessage());
                    } else {
                        logger.debugf("Event sent to topic=%s partition=%d offset=%d",
                                metadata.topic(), metadata.partition(), metadata.offset());
                    }
                });
            } catch (Exception e) {
                logger.errorf("Event serialization failed: %s", e.getMessage());
            }
        });
    }

    @Override
    public void onEvent(org.keycloak.events.admin.AdminEvent event, boolean includeRepresentation) {
        Map<String, Object> eventData = new LinkedHashMap<>();
        eventData.put("eventType", "ADMIN_EVENT");
        eventData.put("realmId", event.getRealmId());
        eventData.put("operationType", event.getOperationType() != null
                ? event.getOperationType().name() : "UNKNOWN");
        eventData.put("resourceType", event.getResourceType() != null
                ? event.getResourceType().name() : "UNKNOWN");
        eventData.put("resourcePath", event.getResourcePath());
        eventData.put("authRealmId", event.getAuthDetails() != null
                ? event.getAuthDetails().getRealmId() : null);
        eventData.put("authUserId", event.getAuthDetails() != null
                ? event.getAuthDetails().getUserId() : null);
        eventData.put("authIpAddress", event.getAuthDetails() != null
                ? event.getAuthDetails().getIpAddress() : null);
        eventData.put("time", event.getTime());
        eventData.put("error", event.getError());

        executor.submit(() -> {
            try {
                String key = (event.getRealmId() != null ? event.getRealmId() : "unknown")
                        + ":admin:" + event.getTime();
                String value = mapper.writeValueAsString(eventData);

                ProducerRecord<String, String> record = new ProducerRecord<>(topic, key, value);
                record.headers().add("event_type", "ADMIN_EVENT".getBytes());

                producer.send(record, (metadata, exception) -> {
                    if (exception != null) {
                        logger.errorf("Kafka admin event send failed: %s", exception.getMessage());
                    }
                });
            } catch (Exception e) {
                logger.errorf("Admin event serialization failed: %s", e.getMessage());
            }
        });
    }

    @Override
    public void close() {
        executor.shutdown();
        try {
            if (!executor.awaitTermination(5, TimeUnit.SECONDS)) {
                executor.shutdownNow();
            }
        } catch (InterruptedException e) {
            executor.shutdownNow();
            Thread.currentThread().interrupt();
        }
        producer.flush();
        producer.close();
        logger.info("KafkaEventListenerProvider closed");
    }
}
```

代码拆解说明：

- **异步线程池**：`onEvent()`将事件数据构建后提交到独立的`ExecutorService`处理，不阻塞Keycloak的认证请求线程。线程池大小建议为2-4，避免过多线程抢占Keycloak自身的CPU资源。
- **Kafka发送参数**：`ACKS_CONFIG=1`表示Leader确认即可（兼顾吞吐量与可靠性）；`MAX_IN_FLIGHT_REQUESTS_PER_CONNECTION=1`保证顺序；`LINGER_MS_CONFIG=10`允许10ms的小批量窗口提升吞吐量；`COMPRESSION_TYPE_CONFIG=lz4`减少网络带宽消耗。
- **敏感信息过滤**：`redirect_uri`和`code_id`在事件发送前被移除，避免URL中的敏感参数泄露到Kafka。生产环境应根据数据安全规范增加更完善的字段脱敏逻辑。
- **AdminEvent支持**：重载的`onEvent(AdminEvent, boolean)`与Login Event共用一个Kafka Topic，通过`eventType`字段区分，通过header标记类别。

---

### 步骤4：实现钉钉告警监听器Provider

**目标**：检测暴力破解行为（5分钟内同一用户连续登录失败≥10次），通过钉钉Webhook发送告警。

编写 `DingTalkAlertEventListenerProvider.java`：

```java
package com.mycompany.keycloak;

import org.jboss.logging.Logger;
import org.keycloak.events.Event;
import org.keycloak.events.EventListenerProvider;
import org.keycloak.events.EventType;
import org.keycloak.models.KeycloakSession;
import org.springframework.web.client.RestTemplate;

import java.util.*;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;

public class DingTalkAlertEventListenerProvider implements EventListenerProvider {

    private static final Logger logger = Logger.getLogger(DingTalkAlertEventListenerProvider.class);

    private final String webhookUrl;
    private final RestTemplate restTemplate;
    private final int failureThreshold;
    private final long windowMs;

    private final ConcurrentHashMap<String, List<Long>> failureTimestamps = new ConcurrentHashMap<>();
    private final ScheduledExecutorService cleanupExecutor;

    public DingTalkAlertEventListenerProvider(String webhookUrl) {
        this.webhookUrl = webhookUrl;
        this.restTemplate = new RestTemplate();
        this.failureThreshold = 10;
        this.windowMs = 5 * 60 * 1000L;

        this.cleanupExecutor = Executors.newSingleThreadScheduledExecutor(r -> {
            Thread t = new Thread(r, "dingtalk-alert-cleanup");
            t.setDaemon(true);
            return t;
        });
        this.cleanupExecutor.scheduleAtFixedRate(this::cleanupExpiredRecords, 1, 1, TimeUnit.MINUTES);
    }

    @Override
    public void onEvent(Event event) {
        if (event.getType() != EventType.LOGIN_ERROR) return;

        String userId = getUserId(event);
        if (userId == null) return;

        failureTimestamps.compute(userId, (key, timestamps) -> {
            if (timestamps == null) timestamps = Collections.synchronizedList(new ArrayList<>());
            long now = System.currentTimeMillis();
            timestamps.add(now);
            timestamps.removeIf(t -> now - t > windowMs);

            if (timestamps.size() >= failureThreshold) {
                sendDingTalkAlert(userId, timestamps.size(), event.getIpAddress());
                timestamps.clear();
            }
            return timestamps;
        });
    }

    private String getUserId(Event event) {
        if (event.getUserId() != null) {
            return event.getUserId();
        }
        Map<String, String> details = event.getDetails();
        if (details != null && details.containsKey("username")) {
            return details.get("username");
        }
        return null;
    }

    private void sendDingTalkAlert(String userId, int count, String ip) {
        try {
            String text = String.format(
                    "## ⚠ 异常登录告警\n\n" +
                    "- **用户账号**: `%s`\n" +
                    "- **5分钟内失败次数**: **%d**\n" +
                    "- **来源IP**: %s\n\n" +
                    "> 请立即核实是否为暴力破解攻击\n" +
                    "> 告警时间: %s",
                    userId, count,
                    ip != null ? ip : "未知",
                    new Date().toString()
            );

            Map<String, Object> markdown = new LinkedHashMap<>();
            markdown.put("title", "异常登录告警");
            markdown.put("text", text);

            Map<String, Object> alert = new LinkedHashMap<>();
            alert.put("msgtype", "markdown");
            alert.put("markdown", markdown);

            restTemplate.postForEntity(webhookUrl, alert, String.class);
            logger.infof("DingTalk alert sent for user: %s, failures: %d", userId, count);
        } catch (Exception e) {
            logger.errorf("Failed to send DingTalk alert: %s", e.getMessage());
        }
    }

    private void cleanupExpiredRecords() {
        long now = System.currentTimeMillis();
        failureTimestamps.entrySet().removeIf(entry -> {
            entry.getValue().removeIf(t -> now - t > windowMs * 2);
            return entry.getValue().isEmpty();
        });
    }

    @Override
    public void onEvent(org.keycloak.events.admin.AdminEvent event, boolean includeRepresentation) {
    }

    @Override
    public void close() {
        cleanupExecutor.shutdown();
        failureTimestamps.clear();
        logger.info("DingTalkAlertEventListenerProvider closed");
    }
}
```

代码拆解说明：

- **滑动窗口算法**：每个用户维护一个时间戳列表，每次`LOGIN_ERROR`追加当前时间，然后移除5分钟窗口以外的旧记录。当窗口内记录数达到10时触发告警并清空列表（避免重复告警）。
- **用户识别**：优先使用`event.getUserId()`（UUID），如果为空则回退到`event.getDetails().get("username")`（适用于用户不存在的场景——暴力破解常用不存在的用户名试错）。
- **定时清理**：`cleanupExecutor`每分钟执行一次过期记录清理，移除超过两倍窗口时间的旧纪录——防止内存中的`failureTimestamps`无限增长。
- **异步发送**：`sendDingTalkAlert()`在`onEvent()`所在线程中调用，但由于钉钉Webhook通常响应很快（≈50ms），且告警触发频率远低于正常登录事件，不会构成性能瓶颈。

> **优化提示**：在极高并发场景下（如单Realm日活千万级），`ConcurrentHashMap`的粒度可能不够。可以通过为每个分片（如按userId.hashCode() % 16分片）创建独立的`ConcurrentHashMap`来减少锁竞争。

---

### 步骤5：实现Factory类

**目标**：编写Factory，将两个Provider注册到Keycloak的SPI容器中，并在管理控制台提供可配置参数。

编写 `KafkaEventListenerFactory.java`：

```java
package com.mycompany.keycloak;

import org.keycloak.Config;
import org.keycloak.events.EventListenerProvider;
import org.keycloak.events.EventListenerProviderFactory;
import org.keycloak.models.KeycloakSession;
import org.keycloak.models.KeycloakSessionFactory;
import org.keycloak.provider.ProviderConfigProperty;
import org.keycloak.provider.ProviderConfigurationBuilder;

import java.util.List;

public class KafkaEventListenerFactory implements EventListenerProviderFactory {

    public static final String PROVIDER_ID = "kafka-event-listener";

    @Override
    public EventListenerProvider create(KeycloakSession session) {
        org.keycloak.component.ComponentModel model = session.getContext().getRealm()
                .getComponentsStream(session.getContext().getRealm().getId())
                .filter(c -> PROVIDER_ID.equals(c.getProviderId()))
                .findFirst()
                .orElse(null);

        if (model == null) {
            return new KafkaEventListenerProvider("localhost:9092", "keycloak-events");
        }

        String servers = model.getConfig() != null
                ? model.getConfig().getFirst("bootstrapServers")
                : null;
        String topic = model.getConfig() != null
                ? model.getConfig().getFirst("topic")
                : null;

        return new KafkaEventListenerProvider(
                servers != null ? servers : "localhost:9092",
                topic != null ? topic : "keycloak-events"
        );
    }

    @Override
    public String getId() {
        return PROVIDER_ID;
    }

    @Override
    public List<ProviderConfigProperty> getConfigProperties() {
        return ProviderConfigurationBuilder.create()
                .property()
                    .name("bootstrapServers")
                    .label("Kafka Bootstrap Servers")
                    .helpText("逗号分隔的Kafka Broker地址列表，如 kafka1:9092,kafka2:9092")
                    .type(ProviderConfigProperty.STRING_TYPE)
                    .defaultValue("localhost:9092")
                    .add()
                .property()
                    .name("topic")
                    .label("Kafka Topic")
                    .helpText("Event消息发送的目标Topic名称")
                    .type(ProviderConfigProperty.STRING_TYPE)
                    .defaultValue("keycloak-events")
                    .add()
                .build();
    }

    @Override
    public void init(Config.Scope config) {}
    @Override
    public void postInit(KeycloakSessionFactory factory) {}
    @Override
    public void close() {}
}
```

编写 `DingTalkAlertEventListenerFactory.java`：

```java
package com.mycompany.keycloak;

import org.keycloak.Config;
import org.keycloak.events.EventListenerProvider;
import org.keycloak.events.EventListenerProviderFactory;
import org.keycloak.models.KeycloakSession;
import org.keycloak.models.KeycloakSessionFactory;
import org.keycloak.provider.ProviderConfigProperty;
import org.keycloak.provider.ProviderConfigurationBuilder;

import java.util.List;

public class DingTalkAlertEventListenerFactory implements EventListenerProviderFactory {

    public static final String PROVIDER_ID = "dingtalk-alert-listener";

    @Override
    public EventListenerProvider create(KeycloakSession session) {
        org.keycloak.component.ComponentModel model = session.getContext().getRealm()
                .getComponentsStream(session.getContext().getRealm().getId())
                .filter(c -> PROVIDER_ID.equals(c.getProviderId()))
                .findFirst()
                .orElse(null);

        String webhookUrl = (model != null && model.getConfig() != null)
                ? model.getConfig().getFirst("webhookUrl")
                : null;

        if (webhookUrl == null || webhookUrl.isEmpty()) {
            webhookUrl = "https://oapi.dingtalk.com/robot/send?access_token=YOUR_TOKEN";
        }

        return new DingTalkAlertEventListenerProvider(webhookUrl);
    }

    @Override
    public String getId() {
        return PROVIDER_ID;
    }

    @Override
    public List<ProviderConfigProperty> getConfigProperties() {
        return ProviderConfigurationBuilder.create()
                .property()
                    .name("webhookUrl")
                    .label("钉钉Webhook地址")
                    .helpText("钉钉群机器人的Webhook完整URL")
                    .type(ProviderConfigProperty.STRING_TYPE)
                    .add()
                .build();
    }

    @Override
    public void init(Config.Scope config) {}
    @Override
    public void postInit(KeycloakSessionFactory factory) {}
    @Override
    public void close() {}
}
```

---

### 步骤6：创建SPI注册文件

**目标**：通过Java SPI机制告诉Keycloak去何处寻找Factory实现。

编写 `src/main/resources/META-INF/services/org.keycloak.events.EventListenerProviderFactory`：

```
com.mycompany.keycloak.KafkaEventListenerFactory
com.mycompany.keycloak.DingTalkAlertEventListenerFactory
```

> **关键提示**：文件名必须与SPI接口的全限定类名完全一致。这里是`org.keycloak.events.EventListenerProviderFactory`（注意是EventListener而非EventListenerProvider，Factory的SPI注册文件以Factory接口为准）。

---

### 步骤7：编译打包

**目标**：将项目编译为可部署的JAR包。

```bash
# 在 event-listener-extensions/ 目录下执行
mvn clean package
```

编译成功后，JAR包位于 `target/event-listener-extensions-1.0.0.jar`。使用以下命令验证SPI注册文件是否正确打包：

```bash
jar tf target/event-listener-extensions-1.0.0.jar | grep services
```

预期输出应包含：

```
META-INF/services/org.keycloak.events.EventListenerProviderFactory
```

---

### 步骤8：部署到Keycloak

**目标**：将JAR包放入Keycloak的providers目录并重建。

```bash
# 将JAR拷贝到providers目录
cp target/event-listener-extensions-1.0.0.jar /opt/keycloak/providers/

# Quarkus模式下必须重新构建
/opt/keycloak/bin/kc.sh build

# 启动Keycloak
/opt/keycloak/bin/kc.sh start
```

Docker Compose方式：

```yaml
services:
  keycloak:
    image: quay.io/keycloak/keycloak:26.1
    volumes:
      - ./event-listener-extensions/target/event-listener-extensions-1.0.0.jar:/opt/keycloak/providers/event-listener.jar
    environment:
      KC_BOOTSTRAP_ADMIN_USERNAME: admin
      KC_BOOTSTRAP_ADMIN_PASSWORD: admin
    command: start-dev
```

---

### 步骤9：在管理控制台配置事件策略

**目标**：配置Event存储策略，启用自定义事件监听器。

1. 登录Admin Console → 选择目标Realm → 进入 **Realm Settings** → **Events** 选项卡。
2. **Save Events**：切换为 **ON**。
3. **Saved Event Types**：勾选需要持久化的事件类型。结合Kafka已接管全量事件，数据库只保留用于快速查询的关键类型：
   - `LOGIN`、`LOGIN_ERROR`、`LOGOUT`、`REGISTER`、`CODE_TO_TOKEN`、`REFRESH_TOKEN`、`UPDATE_PASSWORD`、`UPDATE_EMAIL`、`SEND_VERIFY_EMAIL`
4. **Expiration**：设置为 **7天**（events被Kafka和ELK长期存储后，数据库只需保留近期用于故障排查窗口）。
5. **Admin Events** → **Save Events**：切换为 **ON**。**Include Representation**：设置为 **OFF**（不保存变更前后的完整实体JSON，减少存储开销）。
6. **Event Listeners**：在下拉列表中勾选 `kafka-event-listener` 和 `dingtalk-alert-listener`。点击 **Save**。

> **提示**：若在Event Listeners下拉列表中找不到你的监听器，首先检查JAR中的SPI注册文件是否正确，其次确认执行了`kc.sh build`。开发模式（`start-dev`）下每次重启会自动重新索引，生产模式必须手工build。

---

### 步骤10：测试验证

**目标**：从Kafka消息接收和钉钉告警两个维度验证功能。

**验证1：Kafka事件上报**

```bash
# 启动Kafka消费者监听目标Topic
docker exec kafka /opt/kafka/bin/kafka-console-consumer.sh \
  --bootstrap-server localhost:9092 \
  --topic keycloak-events \
  --from-beginning
```

在浏览器中触发一次正常登录（使用测试用户`testuser / Test1234!`），预期Kafka消费者输出：

```json
{
  "type": "LOGIN",
  "realmId": "demo-realm",
  "clientId": "account-console",
  "userId": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "sessionId": "abc-def-ghi",
  "ipAddress": "172.17.0.1",
  "time": 1715414400000,
  "error": null,
  "details": {
    "username": "testuser",
    "auth_method": "openid-connect",
    "auth_type": "code",
    "response_type": "code"
  }
}
```

**验证2：钉钉暴力破解告警**

```bash
# 使用错误密码连续登录10次
for ($i=0; $i -lt 10; $i++) {
    curl -s -X POST http://localhost:8080/realms/demo-realm/protocol/openid-connect/token \
      -d "client_id=account-console" \
      -d "grant_type=password" \
      -d "username=testuser" \
      -d "password=WrongPassword123!"
}
```

第10次登录失败后，钉钉群应收到如下格式的消息：

```
## ⚠ 异常登录告警

- **用户账号**: `a1b2c3d4-e5f6-7890-abcd-ef1234567890`
- **5分钟内失败次数**: **10**
- **来源IP**: 172.17.0.1

> 请立即核实是否为暴力破解攻击
> 告警时间: Sun May 12 15:30:22 CST 2026
```

**验证3：Admin Event上报**

通过Admin Console修改任意用户的角色，在Kafka消费者中应看到：

```json
{
  "eventType": "ADMIN_EVENT",
  "realmId": "demo-realm",
  "operationType": "CREATE",
  "resourceType": "REALM_ROLE_MAPPING",
  "resourcePath": "users/a1b2c3d4-.../role-mappings/realm",
  "authRealmId": "demo-realm",
  "authUserId": "admin-user-uuid",
  "authIpAddress": "172.17.0.1",
  "time": 1715414500000,
  "error": null
}
```

---

### 可能遇到的坑

1. **Event Listener中执行耗时操作阻塞用户登录**：`onEvent()`是在Keycloak的请求处理线程中同步调用的。即使本章使用了线程池异步化，线程池的`executor.submit()`本身也需要约1-5微秒——在极端负载下可以进一步优化为将事件对象直接丢入`LinkedBlockingQueue`，由后台线程消费，彻底消除`onEvent()`路径上的任何非确定性延迟。

2. **Kafka Producer连接断开后的重连机制**：`KafkaProducer`默认会自动重连，但`max.block.ms`（默认60秒）决定了元数据刷新和缓冲区空间等待的超时时间。如果Kafka长时间不可用，Producer内部缓冲队列可能被填满，导致`send()`阻塞。解决方案是在Producer配置中加入`max.block.ms=5000`并配合`buffer.memory`合理设置缓冲区大小。

3. **内存中的失败计数器未清理**：`DingTalkAlertEventListenerProvider`的`failureTimestamps`在处理不存在用户的暴力破解时，会积累大量无效用户条目的空列表。虽然`cleanupExpiredRecords`每分钟清理一次，但如果攻击者使用大量随机用户名，清理前的Map大小可能膨胀。增强方案：限制Map最大大小为10000，超出后使用LRU算法淘汰最旧的条目。

4. **Event details中敏感信息泄露**：`LOGIN_ERROR`事件的details中包含`username`字段——攻击者可以通过暴力破解探测系统中存在哪些用户名（区分"密码错误"和"用户不存在"两种错误类型）。发送到Kafka/钉钉前需评估字段脱敏需求，或通过Webhook地址的IP白名单限制接收方。

5. **多个Event Listener的调用顺序**：Keycloak按SPI注册顺序调用所有注册的EventListener——顺序不可控，且一个Listener的异常不会阻止后续Listener的执行（每个Listener被`try-catch`包裹）。这意味着你不能依赖顺序实现"先Kafka后告警"之类的编排逻辑。

---

## 4 项目总结

### 方案对比

| 维度 | SPI事件监听 | 日志文件解析 | 数据库轮询 |
|------|-----------|------------|----------|
| 实时性 | ✅ 近乎实时（毫秒级） | ⚠️ Filebeat采集约1-5秒延迟 | ❌ 轮询间隔通常≥30秒 |
| 可靠性 | ⚠️ 监听器异常可能丢事件 | ✅ 日志文件有操作系统级持久化 | ✅ 数据库保证持久化 |
| 开发成本 | ⚠️ 需理解SPI体系，JAR打包部署 | ✅ 仅需Filebeat/Logstash配置 | ✅ 纯SQL定时任务即可 |
| 对Keycloak影响 | ⚠️ 监听器阻塞会拖慢认证 | ✅ 对Keycloak零侵入 | ❌ 轮询增加DB负载 |
| 维护成本 | ⚠️ Keycloak升级需验证SPI兼容性 | ✅ 解析规则可能需调整 | ✅ 版本升级无影响 |
| 事件类型覆盖 | ✅ 全部Login + Admin事件 | ⚠️ 依赖日志格式完整性 | ✅ 与内建Event一致 |
| 多通道分发 | ✅ 一个SPI内可发多目标 | ❌ 需多个采集Agent | ❌ 需多个轮询任务 |
| 异常事件捕获 | ✅ `LOGIN_ERROR`等错误事件 | ⚠️ 需解析日志中的error字段 | ✅ 与内建Event一致 |

### 适用场景

1. **安全审计与合规（ISO 27001 / SOC2 / 等保）**：所有认证操作的完整审计日志实时推送到SIEM系统，满足审计核查中的实时性和不可抵赖性要求。
2. **实时安全告警**：暴力破解、异常地理位置登录、非工作时间管理操作等异常行为的即时检测与通知。
3. **大数据行为分析**：将用户登录时间、频率、地理位置数据灌入Kafka，供数据团队构建用户画像、异地登录风险模型。
4. **跨系统事件联动**：用户登录成功后自动触发下游系统初始化（如创建个人工作区、分配默认权限），实现事件驱动的账号生命周期管理。
5. **多维度运维可视化**：将Event数据导入Elasticsearch+Grafana，构建登录QPS曲线、按Realm/Client维度的登录分布热力图。

**不适用场景**：对事件丢失零容忍的场景（如金融交易审计）——SPI监听器的设计假设是"best-effort"，需要额外的持久化和重试机制来保证at-least-once语义；需要在Event数据中携带完整实体快照的场景（如Admin Event中的representation）——打开`includeRepresentation`会显著增加存储开销，应单独设计变更记录机制。

### 注意事项

- **监听器性能开销**：`onEvent()`中的任何耗时操作都会直接拖慢Keycloak的认证响应时间。异步化（独立线程池）是生产环境的必选项。可在监听器中接入Micrometer指标，监控Queue的积压深度和线程池的RejectedExecution比率。
- **事件丢失容忍度**：Kafka Producer的`send()`是异步的（callback在另一个线程执行），如果JVM进程在send后、broker确认前崩溃（如OOM、Kill -9），该事件将丢失。对于审计合规场景，建议将事件先写入本地文件（追加写JSON行），再用Filebeat采集到Kafka——双重通道互为备份。
- **敏感信息脱敏**：Event details中可能包含username、email等个人信息。发送到外部系统前应实现字段白名单或脱敏规则——例如只保留username的首尾字符（`t***r@***.com`）。
- **配置热更新**：Keycloak的Realm级EventListener配置变更后即时生效（下一个请求即使用新配置），无需重启。但Provider级别的初始化参数（如Kafka bootstrap servers）在Factory的`init()`中读取，需要重启才能生效。建议Kafka连接参数放在Realm级Component配置中（`getConfigProperties()`），实现动态更新。

### 常见踩坑经验

1. **问题**：Kafka集群迁移后，Keycloak仍然向旧Broker发送数据，导致事件大量丢失。**根因**：KafkaProducer实例在`KafkaEventListenerProvider`初始化时创建，缓存在Provider实例中，Keycloak不重启就不会重建Producer——Producer未感知Broker地址变更。**解决**：重启Keycloak或实现Producer的健康检查和自动重建机制。

2. **问题**：钉钉告警在暴力破解时触发了几百条消息，瞬间触发Webhook频率限制导致后续告警被盾。**根因**：滑动窗口清理后计数器归零，下次失败又触发新告警——若攻击持续30分钟，10次失败一轮，共3轮告警是正常的，但如果多个用户同时被攻击，告警洪水会把Webhook冲垮。**解决**：在`sendDingTalkAlert()`中加入全局发送限流（`RateLimiter`，每分钟最多5条），超出部分合并为摘要告警："过去1分钟内有8个用户触发暴力破解告警"。

3. **问题**：Keycloak内存持续增长，最终OOM。**根因**：`failureTimestamps`中的List持有大量未过期的timestamp对象，在攻击者用数千个不同用户名快速发起暴力探测时，每个用户名一条List，每个List存几十个时间戳，Map总条目数爆炸。**解决**：`compute()`后检查Map大小，超过阈值（如10000条目）时清理最旧的50%条目（LRU驱逐），配合监控Metric暴露Map大小变化。

### 思考题

1. **at-least-once语义设计**：如果需要保证事件"不丢失"（即每条Event至少被Kafka消费一次），你如何设计监听器的重试和持久化机制？提示：考虑在`onEvent()`中将Event序列化后追加写入本地WAL（Write-Ahead Log）文件，后台线程从WAL读取并发送Kafka，Kafka ACK后标记WAL记录为已发送；同时设计WAL的segment机制防止单文件过大。

2. **全链路用户行为关联**：如何实现跨多个事件类型的关联分析——将用户的`LOGIN`事件和后续的`CODE_TO_TOKEN`、`REFRESH_TOKEN`、`LOGOUT`事件串联成完整的用户行为链路？提示：利用`sessionId`作为关联键，在ELK中通过`sessionId`字段聚合查询；对于跨会话的行为关联（如注册后一周的首次购买），需要通过`userId`将所有行为串联，结合时间窗口判定"会话边界"。
