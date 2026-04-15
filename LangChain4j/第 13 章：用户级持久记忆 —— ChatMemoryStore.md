# 第 13 章：用户级持久记忆 —— ChatMemoryStore

## 1. 项目背景

### 业务场景（拟真）

`TokenWindowChatMemory` 仅在 **进程内**有效：重启或多实例扩容后，用户对话 **丢失或分裂**。`ChatMemoryStore` 将 **序列化消息** 存到 DB、Redis 等，用 **`memoryId`**（常为用户 id 或 session id）索引——**有状态聊天** 才具备 **容灾、审计与合规删除**。

### 痛点放大

若 **memoryId 来自 URL 参数**：**安全**上可 **越权读他人会话**；若 **只存 DB 不删向量**：**合规**上「被遗忘权」落空；**并发写** 无版本控制则 **消息乱序或覆盖**。技术之外：**留存周期、被遗忘权、PII 加密** 是法务必答题。教程 `_09_ServiceWithPersistentMemoryForEachUserExample.java` 演示 **每用户独立记忆**。

## 2. 项目设计：小胖、小白与大师的对话

**小胖**：memoryId 就像微信聊天记录存在云端？

**小白**：**直接用 userId 当 memoryId** 行吗？**换设备** 呢？

**大师**：userId 多数可行，但必须来自 **已认证主体**，不可客户端伪造。换设备要对齐 **会话亲和** 与 **同一逻辑 memoryId**，否则「换手机失忆」。**技术映射**：**memoryId = 租户/会话主键，属安全边界**。

**小胖**：存明文行吗？删用户要删啥？

**小白**：**多实例并发写** 冲突咋办？**存储量大** 呢？

**大师**：PII **加密-at-rest**，密钥 **KMS**；日志 **禁打全文**。删用户要 **DB + 向量 + 对象存储** 三连（GDPR）。并发需 **乐观锁/单 writer 队列** 等。体积大：**滚动摘要** 或 **外置摘要 embedding**。**技术映射**：**持久记忆 = 分布式一致 + 合规**。

---

## 3. 项目实战

### 环境准备

- [`_09_ServiceWithPersistentMemoryForEachUserExample.java`](../../langchain4j-examples/tutorials/src/main/java/_09_ServiceWithPersistentMemoryForEachUserExample.java)。

### 分步任务

| 步骤 | 目标 | 自检 |
|------|------|------|
| 1 | memoryId 来源 | 来自 **SecurityContext/JWT**，非 URL 直传 |
| 2 | 存储格式 | JSON/blob 升级需 **迁移脚本** |
| 3 | 拓扑 | 画 **HTTP → Filter → Service(memoryId) → AiServices → Store** |

**可能遇到的坑**：**可枚举 memoryId** 被遍历；**删 DB 忘删向量**；**无 TTL** 膨胀。

### 测试验证

- 越权 **403**；删用户后 **DB 与索引无残留**。

### 完整代码清单

[`_09_ServiceWithPersistentMemoryForEachUserExample.java`](../../langchain4j-examples/tutorials/src/main/java/_09_ServiceWithPersistentMemoryForEachUserExample.java)。

---

## 4. 项目总结

### 优点与缺点（与同类做法对比）

| 维度 | ChatMemoryStore | 仅进程内 memory | 自建会话服务 |
|------|-----------------|-----------------|--------------|
| 容灾 | 高 | 低 | 视实现 |
| 合规基础 | 高 | 低 | 视实现 |
| 运维成本 | 中 | 低 | 高 |
| 典型缺点 | schema 迁移 | 无持久化 | 重复造轮 |

### 适用场景

- 客服、导购、实施顾问等多轮；强合规 **审计**。

### 不适用场景

- **无状态一次性问答**——不必上 Store。  
- **无法承担加密与删除流程** 的组织——先补治理再上。

### 注意事项

- **最小化** 存储；**跨地域** 与数据主权。

### 常见踩坑经验（生产向根因）

1. **伪造 memoryId** 读他人会话。  
2. **只备份 DB 忘向量** → RAG 与对话不一致。  
3. **未设 TTL** 无限膨胀。

### 进阶思考题

1. **乐观锁版本号** 与 **消息追加** 的冲突如何解决？  
2. 删用户时 **异步向量删除失败** 的补偿任务如何设计？

### 推广计划提示（多部门）

| 角色 | 建议阅读顺序 | 协作要点 |
|------|----------------|----------|
| **开发** | 本章 + 鉴权链 | **memoryId 只从认证上下文** |
| **运维** | 备份 RPO/RTO | **单 memory 大小** 与慢查询 |
| **合规** | 留存策略 | **硬删除 + 审计日志** |

---

### 本期给测试 / 运维的检查清单

**测试**：越权用例 **必读 403**；**删除用户**后内存与索引都无残留。  
**运维**：备份 **RPO/RTO**；监控 **单 memory 大小** 与慢查询；密钥 **轮换 Runbook**。

### 附录：相关 Maven 模块与源码类

| 模块 | 说明 |
|------|------|
| `langchain4j-core` | `ChatMemoryStore` 接口 |
| 各存储模块 | 例如 Redis / JDBC 适配（以官方文档为准） |

推荐阅读：`_09_ServiceWithPersistentMemoryForEachUserExample.java`、`ChatMemoryStore`。
