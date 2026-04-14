# 第 13 章：用户级持久记忆 —— ChatMemoryStore

## 1. 项目背景

`TokenWindowChatMemory` 在 **进程内**有效，一旦重启或扩容到多实例，用户对话就会丢失或分裂。`ChatMemoryStore` 把 **序列化后的消息**存到数据库、Redis 等外置介质，用 **`memoryId`**（常为用户 id 或 session id）索引。于是 **有状态聊天**才具备 **容灾、审计与合规删除** 的基础。

教程 `_09_ServiceWithPersistentMemoryForEachUserExample.java`（路径见 `langchain4j-examples/tutorials/...`）演示 **每用户独立记忆** 的装配方式。本章强调：**技术之外**还有 **法务**（留存周期、被遗忘权）与 **安全**（越权读他人记忆）。

## 2. 项目设计：大师与小白的对话

**小白**：直接用 userId 当 memoryId 行不行？

**大师**：多数情况行，但要确认 **userId 不可被客户端伪造**——必须来自 **已认证主体**。

**小白**：会话中途换设备呢？

**大师**：同一逻辑 `memoryId` 应对齐你们 **会话亲和策略**；否则用户感觉「换手机就失忆」。

**小白**：存明文合规吗？

**大师**：按字段分级：**PII 应加密-at-rest**；密钥走 KMS；日志 **禁止**打印完整消息。

**小白**：多实例下还有并发写冲突吗？

**大师**：有。需要 **乐观锁/版本号** 或 **单 writer 队列**；或用 **CRDT** 级别需求要看业务是否值得。

**小白**：如何做「遗忘我」？

**大师**：**硬删除**某 `memoryId` 下所有分段，并 **联动向量库**里该用户相关的分段（若分租户索引）。

**小白**：存储量大怎么办？

**大师**：**滚动摘要** + 丢弃早期细节；或 **外置摘要 embedding**。

## 3. 项目实战：主代码片段

> **场景入戏**：进程内 `ChatMemory` 是 **便签纸**；`ChatMemoryStore` 是 **银行保险柜**——可以 **灾备恢复**，也可以 **被柜员错拿钥匙**（越权）。**memoryId** 就是 **保险柜编号**。

请在仓库打开：

[`_09_ServiceWithPersistentMemoryForEachUserExample.java`](../../langchain4j-examples/tutorials/src/main/java/_09_ServiceWithPersistentMemoryForEachUserExample.java)

#### 三件事（必须能讲给别人听）

1. **`memoryId` 从哪来**：`ThreadLocal`？`SecurityContext`？**JWT sub**？——**绝不能**信任客户端 URL 直传。  
2. **存什么格式**：`ChatMemoryStore` 用的是 **JSON**、**CBOR** 还是 **blob**？升级消息模型时要 **迁移脚本**。  
3. **builder 怎样绑**：**窗口大小** 与 **store** 谁是 **主**——换 store 要不要 **重建索引**。

#### 闯关速描（纸上即可）

在 A4 纸侧视图画：**HTTP → Filter(鉴权) → Service(memoryId) → AiServices → Store**，标出 **哪里能做 horizontal scale**（通常 **Store** 与 **会话亲和**冲突）。

#### 挖深一层

- **GDPR「被遗忘权」**：删用户 = **DB + 向量 + 对象存储** 三连删，缺一即 **合规洞**。  
- **一致性**：消息写库成功、向量 **未完成** 删除——需要 **补偿任务**。  
- **趣味冷知识**：memoryId **不要用可枚举顺序整数**——容易被 **遍历偷窥**。

## 4. 项目总结

### 优点

- **会话可恢复**，利于 **人机协作长流程**。  
- 为 **审计**提供数据基础。

### 缺点

- **存储成本**与 **延迟**。  
- **schema 迁移**复杂（消息格式升级）。

### 适用场景

- 客服、导购、实施顾问等多轮场景。  
- 强 **合规**行业需要保存证据链时。

### 注意事项

- **最小化** 存储字段；定期 **归档**。  
- **跨地域**复制与数据主权。

### 常见踩坑

1. **伪造 memoryId** 读到他人会话。  
2. **只备份 DB 忘了向量库**，导致 RAG 与对话不一致。  
3. **未设 TTL** 导致存储无限膨胀。

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
