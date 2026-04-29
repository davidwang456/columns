# 第 13 章：用户级持久记忆 —— ChatMemoryStore

## 1. 项目背景

### 业务场景（拟真）

第 10 章的 `TokenWindowChatMemory` 只能在 **单进程的内存中** 工作。这意味着两件事做不了：一是服务重启后所有对话历史丢失；二是水平扩展到多实例后，用户请求可能落到不同实例上（实例 A 有第 1-3 轮对话，实例 B 是空的）。用户在产品上感受到的体验就是：**刷新页面后机器人不认识自己了**。

产品经理问技术团队：**「微信换了手机登录还能看到聊天记录，我们的机器人为什么浏览器一刷新就失忆？」**

要回答这个问题，就需要把记忆从「进程内存」搬到「持久化存储」中——`ChatMemoryStore`。它把消息序列化后存到 DB、Redis 等外部存储中，用 `memoryId`（通常是用户 ID 或会话 ID）做索引。服务重启后，根据 `memoryId` 从 store 中恢复对话。

### 痛点放大

没有持久化的记忆，你遇到的三座大山：

- **容灾**：实例挂了，那一台机器上的所有对话全部丢失。
- **多实例扩容**：K8s 里两个 Pod 来回切——用户连续两个请求落在不同 Pod，每个 Pod 各自独立维护一段记忆，谁也不完整。
- **合规**：GDPR 给了用户「被遗忘权」——用户要求删账户，你的进程内记忆重启后就没了（这反而是好事），但如果你只是没存外部存储，你根本回答不出「用户的数据在哪里、删干净了没有」。

`ChatMemoryStore` 就是用来解决这三个问题的——把记忆从内存搬到持久化介质，用 `memoryId` 做唯一索引。

## 2. 项目设计：小胖、小白与大师的对话

**小胖**：memoryId 是不是像微信的聊天记录存在云端——换手机登录还能接着聊？

**大师**：对的——memoryId 就是微信账号（userId），你的聊天记录（messages）存在服务器的数据库里。你换设备登录同一个账号，云端把历史拉下来。但有一个关键区别：**微信的聊天记录不会因为太久远就被自动截断**，而 `ChatMemoryStore` 里的记录在拉出来后仍然要经过 `ChatMemory` 的窗口策略（比如保留最近 1000 tokens）来截断——持久化只是保证「不丢」，但不保证「不全量给模型」。模型每次看到的仍然是最近 N 轮 / 最近 N tokens 的内容。

**小白**：memoryId 直接传 `userId` 行不行？会不会有安全问题？比如用户改了 URL 中的 userId 参数就直接看别人的对话了？

**大师**：用 `userId` 当 `memoryId` 在技术上是可行的，但必须遵守一条 **铁律**：`memoryId` **只能从服务端的认证上下文（SecurityContext / JWT token / Session）中提取**，**绝不能**从客户端传来的请求参数或请求体中读取。如果一个攻击者把 `memoryId` 从 `user-123` 改成 `user-456`，而你的服务端没有验证当前登录的用户是不是 `user-456`——那他就看到了别人的完整对话记录。这就是一个 IDOR（不安全的直接对象引用）漏洞。**技术映射**：**memoryId = 租户/会话在持久层的唯一主键，它首先是一个安全边界，其次才是一个业务参数——在写 ChatMemoryStore 之前，先确保 memoryId 的来源是可信的**。

**小白**：如果用户要求删除账户（GDPR 被遗忘权），我需要删哪些地方？只删 ChatMemoryStore 够吗？

**大师**：这是一个很好的问题——很多团队踩过这个坑。ChatMemoryStore 只存了对话消息。但用户的「数据」还包括：**向量库里该用户的文档 embedding**（如果用户上传过文档做 RAG）、**对象存储里该用户上传的源文件**（PDF、图片等）。如果只删了 ChatMemoryStore 但没删向量库——外部搜索还能搜到该用户的文档片段。如果向量库删了但对象存储没删——源文件还留着。所以标准的 **GDPR 三连删** 是：**① ChatMemoryStore 中该 memoryId 的消息记录；② EmbeddingStore 中该用户的文档 segments；③ 对象存储中该用户上传的原始文件**。**技术映射**：**持久记忆 = 分布式状态管理 + 合规数据治理 + PII 加密存储三件事同时落地——它不是「把内存里的东西 dump 到 DB」那么简单，缺了任何一环都上不了生产**。

## 3. 项目实战

### 环境准备

```bash
cd langchain4j-examples/tutorials
export OPENAI_API_KEY="sk-your-key-here"
```

### 步骤 1：实现一个内存版的 ChatMemoryStore

```java
import dev.langchain4j.memory.ChatMemory;
import dev.langchain4j.memory.chat.ChatMemoryStore;
import dev.langchain4j.memory.chat.MessageWindowChatMemory;
import dev.langchain4j.data.message.ChatMessage;
import java.util.*;
import java.util.concurrent.ConcurrentHashMap;

// 简单内存实现（生产换 DB/Redis）
class InMemoryStore implements ChatMemoryStore {
    
    private final Map<String, List<ChatMessage>> store = new ConcurrentHashMap<>();
    
    @Override
    public List<ChatMessage> getMessages(Object memoryId) {
        return store.getOrDefault(memoryId.toString(), new ArrayList<>());
    }
    
    @Override
    public void updateMessages(Object memoryId, List<ChatMessage> messages) {
        store.put(memoryId.toString(), new ArrayList<>(messages));
    }
    
    @Override
    public void deleteMessages(Object memoryId) {
        store.remove(memoryId.toString());
    }
}

public class PersistentMemoryDemo {

    public static void main(String[] args) {

        ChatMemoryStore store = new InMemoryStore();

        // 用户 A 的记忆
        ChatMemory userAMemory = MessageWindowChatMemory.builder()
                .id("user-A")
                .maxMessages(10)
                .chatMemoryStore(store)  // 注入 store
                .build();

        // 即使重启 JVM（换真实 DB 实现），memoryId = "user-A" 的数据仍可恢复
        userAMemory.add(UserMessage.from("Hello, my name is Alice"));
        
        // 查询 user-A 的记忆
        List<ChatMessage> history = store.getMessages("user-A");
        System.out.println("Stored messages count: " + history.size());
        System.out.println("Last message: " + history.get(0).toString());
    }
}
```

### 步骤 2：用 Redis 存储

```java
// 伪代码——实际集成 Redis 需要引入对应模块
// ChatMemoryStore redisStore = new RedisChatMemoryStore(
//     RedisClient.create("redis://localhost:6379"));
```

### 步骤 3：memoryId 安全验证

```java
// ❌ 错误做法：从 URL 参数取 memoryId
String memoryId = request.getParameter("userId");  // 用户可篡改

// ✅ 正确做法：从认证上下文取 memoryId
String memoryId = SecurityContextHolder.getContext()
    .getAuthentication().getName();  // 来自 JWT/Session
```

### 步骤 4：GDPR 删除流程

```java
// 当用户要求删除账户时：
String targetUserId = "user-A";

// 1. 删除对话记忆
store.deleteMessages(targetUserId);

// 2. 删除向量库中的 embedding
// embeddingStore.removeAllByUserId(targetUserId);

// 3. 删除对象存储中的文件
// storageService.deleteDirectory("documents/" + targetUserId);

System.out.println("All data for " + targetUserId + " deleted.");
```

### 闯关任务

| 难度 | 动手 | 过关标准 |
|------|------|----------|
| ★ | memoryId 从 SecurityContext 取而非 URL | 越权 403 |
| ★★ | 画拓扑：HTTP → Filter → Service(memoryId) → AiServices → Store | 理解完整链路 |
| ★★★ | DB + 向量 + 对象存储三处删除 | 至少有两处实现 |

### 可能遇到的坑

| 坑 | 表现 | 解法 |
|----|------|------|
| 可枚举 memoryId 被遍历 | 越权读取他人会话 | 硬校验 userId |
| 删除 DB 忘删向量 | 向量还能搜到 | 三处联动删除 |
| 无 TTL | 存储无限膨胀 | 设自动过期策略 |
| 明文存储 PII | 合规事故 | 加密存储 |

### 测试验证

```java
// 越权测试：用他人 memoryId 访问应返回 403
assertThrows(AccessDeniedException.class, () -> {
    service.getChatHistory("other-user-id", currentUser);
});

// 删除后无残留
store.deleteMessages("user-A");
assertTrue(store.getMessages("user-A").isEmpty());
```

### 完整代码清单

[`_09_ServiceWithPersistentMemoryForEachUserExample.java`](../../langchain4j-examples/tutorials/src/main/java/_09_ServiceWithPersistentMemoryForEachUserExample.java)

## 4. 项目总结

### 优点与缺点

| 维度 | ChatMemoryStore | 仅进程内 memory | 自建会话服务 |
|------|----------------|----------------|-------------|
| 容灾 | 高 | 低 | 视实现 |
| 合规基础 | 高 | 低 | 视实现 |
| 运维成本 | 中 | 低 | 高 |

### 适用 / 不适用场景

**适用**：客服、导购、实施顾问等多轮场景、强合规审计要求。

**不适用**：无状态一次性问答、无法承担加密与删除流程的组织。

### 常见踩坑

1. 伪造 memoryId 读他人会话
2. 只备份 DB 忘备份向量 → 恢复后 RAG 不一致
3. 未设 TTL 无限膨胀

### 进阶思考题

1. 乐观锁版本号与消息追加的冲突如何解决？
2. 删用户时异步向量删除失败的补偿任务如何设计？

### 推广计划

| 角色 | 建议阅读顺序 | 协作要点 |
|------|-------------|----------|
| 开发 | 本章 + 鉴权链 | memoryId 只从认证上下文取 |
| 运维 | 备份 RPO/RTO | 单 memory 大小与慢查询 |
| 合规 | 留存策略 | 硬删除 + 审计日志 |

### 检查清单

- **测试**：越权用例必 403；删用户后内存与索引都无残留
- **运维**：备份 RPO/RTO；监控单 memory 大小与慢查询；密钥轮换 Runbook

### 附录

| 模块 | 说明 |
|------|------|
| `langchain4j-core` | `ChatMemoryStore` 接口 |
| 各存储模块 | 例如 Redis / JDBC 适配 |

推荐阅读：`_09_ServiceWithPersistentMemoryForEachUserExample.java`、`ChatMemoryStore`。
