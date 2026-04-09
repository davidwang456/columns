# 第十一章（分篇一）：Remote Service——`RRemoteService` 与轻量 RPC

[← 第十一章导览](24-分布式服务.md)｜[目录](README.md)

---

## 1. 项目背景

两个 Java 服务共用同一套 Redis，希望 **B 服务调用 A 服务的一个方法**（例如「触发对账」），又不想为此 **单独部署 HTTP/gRPC 网关**。  
Redisson **Remote Service** 把 **接口方法调用** 序列化为 **请求/响应队列**，由 **服务端注册实现**、**客户端拿动态代理** 发起调用——适合 **低耦合、小流量、已与 Redis 共栈** 的内部协调。

---

## 2. 项目设计（大师 × 小白）

**小白**：这不就是 RPC 吗？我为啥不用 gRPC？  
**大师**：gRPC 有 **IDL、治理、观测、多语言** 全套生态；Remote Service 是 **Redis 上的胶水**。你要 **跨语言、大吞吐、长链路追踪**，别用信鸽运集装箱。

**小白**：客户端调用了，服务端没起来会怎样？  
**大师**：会面临 **ack 超时、排队、异常类型**——和第七章消息语义类似，要配 **`RemoteInvocationOptions`**，并在业务上 **超时与降级**。

---

## 3. 项目实战（主代码片段）

**约定**：客户端与服务端须使用 **同一远程接口**，且 Redisson **连的是同一 Redis 拓扑**（见官方说明）。

```java
import org.redisson.api.RRemoteService;
import org.redisson.api.RedissonClient;

// --- 共享模块中的接口 ---
public interface BillingCommands {
    void requestReconcile(long orderId);
}

// --- 服务端 JVM：注册实现，第二个参数为并发 worker 数 ---
public class BillingCommandsImpl implements BillingCommands {
    @Override
    public void requestReconcile(long orderId) {
        // 调用内部领域服务…
    }
}

RedissonClient serverRedisson = /* ... */;
RRemoteService serverRemote = serverRedisson.getRemoteService();
serverRemote.register(BillingCommands.class, new BillingCommandsImpl(), 4);

// --- 客户端 JVM ---
RedissonClient clientRedisson = /* ... */;
RRemoteService clientRemote = clientRedisson.getRemoteService();
BillingCommands stub = clientRemote.get(BillingCommands.class);
stub.requestReconcile(9001L);
```

**进阶**：`RemoteInvocationOptions` 可调整 **ack/执行超时**、**noAck / noResult（fire-and-forget）**；异步接口配合 **`@RRemoteAsync`** 等（见 [services.md](../data-and-services/services.md)）。

---

## 4. 项目总结

| 维度 | 说明 |
|------|------|
| **优点** | **零额外端口**（走 Redis）；API 像本地接口；可 **并行 worker** 扩展单服务吞吐。 |
| **缺点** | **依赖 Redis 可用性**；可观测、版本治理、多语言 **弱于专业 RPC**；大对象/长参数列表要当心 **序列化成本**。 |
| **适用场景** | 同 Redis 集群内 **少量内部命令**、灰度触发、运维型回调。 |
| **注意事项** | **接口与 DTO 演进** 与第四章 Codec 一致；注册与反注册 **生命周期** 随应用启停。 |
| **常见踩坑** | 服务端 **未 register** 或 worker 数过小导致 **排队雪崩**；把 Remote 当 **公网 API**；忽略 **ack 超时异常** 处理。 |

---

## 本章实验室（约 45～60 分钟）

**环境**：同一 Redis；**进程 A** 注册实现，**进程 B** 只调客户端（或同进程两 `RedissonClient` 仅作理解，生产勿混用拓扑）。

### 步骤

1. 按上文片段定义 `BillingCommands`，A `register(..., 2)`，B `get` 后调用 `requestReconcile(1L)`，确认 **服务端日志出现调用**。  
2. 停掉 A，B 再调，记录 **异常类型与耗时**；对照 `RemoteInvocationOptions` **收紧/放宽 ack 超时** 各试一次。  
3. （可选）使用 **fire-and-forget** 选项（若 API 支持），观察 **无返回值** 时如何 **观测成功**（日志、副作用 key）。

### 验证标准

- 有 **成功调用** 与 **服务端不存在/超时** 两类日志样本。  
- 能口述：**与 gRPC 相比缺了哪些治理能力**（至少 2 点）。

### 记录建议

- 一页：**接口演进** 时 DTO 与 Codec（第四章）的 **兼容策略**。

**上一章**：[第十章 事务批处理与 Lua](23-事务批处理与Lua.md)｜**下一篇**：[第十一章（分篇二）RExecutorService](26-RExecutorService.md)｜**下一章**：[第十二章导览](29-Spring生态集成.md)
