# 第三章：线程模型与三种 API——同步、异步、Reactive、RxJava

[← 目录](README.md)

---

## 趣味：茶馆线程池惨案

小白把 `redisson.getMap(...).get` 写在 Netty 的 **event loop** 里，大师看了一眼线程 dump：「你这是让**店小二一边算账一边炒菜**，锅不糊才怪。」  
隔壁桌 Spring MVC 老哥附和：「我这儿线程池也红，可 `top` 里 CPU 很闲——**人在等网，网在等谁？**」

---

## 需求落地（由浅入深）

1. **浅**：接口一压测，**Tomcat active threads** 顶满——业务代码里全是 `get`/`set` 同步等 Redis，**线程都挂在 I/O 上**。  
2. **中**：选择 **同步够用**（后台任务、QPS 可控）还是 **异步/响应式**（网关、WebFlux）；若异步，要配 **独立线程池** 与 **拒绝策略**，别把 `commonPool` 当万能背锅侠。  
3. **深**：`CompletionStage` 链或 `RedissonReactiveClient` 把 **非阻塞段** 与 **阻塞 Redis 调用** 切开；大批量用 **Pipeline** 换 **RTT**，再和 **失败语义** 一起设计（见第十章）。

---

## 对话钩子

**小白**：Tomcat 线程池飙红，CPU 不高，Redis 也不慢……  
**大师**：**阻塞点**在哪？同步 Redisson 在**等网络**；线程数 = 并发请求时，就把容器线程当「等人专用椅」了。

**小白**：我全改成 `getAsync` 是不是就赢了？  
**大师**：异步换的是 **线程**，不换 **Redis 耗时**。若慢在 **大 key、热 key、Lua 太重**，异步只会让 **排队更隐蔽**——**指标里照样露馅**。

---

## 同步 API

- `RedissonClient` 的 `get*()` 同步方法，**简单、直观**。  
- **反模式**：在 Netty I/O 线程、Reactive 链的「非阻塞」段里调同步 Redis。  
- **适用**：后台任务、批处理、QPS 可控的 CRUD。

---

## 异步 API

- 返回 `CompletionStage` / `CompletableFuture`（见 [api-models.md](../api-models.md)）。  
- **实战**：Servlet 里 `supplyAsync` + 组合，避免长时间占满 Tomcat worker（注意**业务线程池**隔离，别用 `ForkJoinPool.commonPool()` 扛一切）。

```java
// 伪代码：先表达「链式」思想，API 以当前版本为准
redisson.getBucket("k").getAsync()
    .thenApply(v -> transform(v))
    .whenComplete((r, ex) -> { /* 记录指标 */ });
```

---

## Reactive（`RedissonReactiveClient`）与 RxJava3

- 适合 **WebFlux** 等已响应式化的栈。  
- **深度**：背压、订阅取消、错误传播是**模型问题**，不是 Redisson 一家能替你省掉的课。

---

## Pipelining（批处理）

见 [pipelining.md](../pipelining.md)。**口诀**：能批量就别 Chatty；**注意**：pipeline 里命令失败时的**部分成功语义**要读文档。

---

## 生产清单

- [ ] 同步路径：**Redis 慢查询** + **客户端超时** 同时看，避免只加线程。  
- [ ] 异步路径：**线程池队列长度** + **拒绝策略** 可观测。  
- [ ] 压测：**连接池水位** 与 **GC** 是否突刺。

---

## 本章实验室

1. 写 1000 个 key：**for 循环同步** vs **pipeline/批量**，对比耗时。  
2. 同一接口：**同步实现** vs **异步实现**，用压测工具看 **Tomcat active threads**。  
3. **追问**：异步快了，**错误处理**变复杂了——你的日志里能拼出 `traceId` 吗？

---

## 大师私房话

异步不是「更快」，是**用另一种资源换线程**。CPU 已打满时，异步救不了；**算法与数据局部性**仍是第一性原理。

**上一章**：[第二章](第二章-配置-拓扑与调参.md)｜**下一章**：[第四章](第四章-Codec与序列化.md)
