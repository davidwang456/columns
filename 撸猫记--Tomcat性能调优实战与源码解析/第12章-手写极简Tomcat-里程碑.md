# 第12章 手写极简 Tomcat：里程碑式综合实战（正文初稿）

> 对应总纲：**第五阶段综合实战**。目标不是复制一个生产级 Tomcat，而是 **用最小代码走通「连接 → 协议 → 路由 → 扩展」**，并与第2～5、7～11 章的概念 **一一对照**。  
> **建议工程名**：`mini-cat`（单模块 Maven/Gradle 均可）；语言 **Java 8+**，便于使用 **NIO** 与 **Base64**（握手）。

---

## 本章导读

- **你要带走的三件事**
  1. **M1** 证明你理解 **HTTP 作为文本协议** 与 **阻塞 IO 的成本**。
  2. **M2** 证明你理解 **Selector、OP_READ、ByteBuffer 半包**——这是读懂 **`NioEndpoint`** 的台阶。
  3. **M3/M4** 把 **Upgrade** 与 **有状态会话** 从「配置项」还原成 **可实现的机制**。

- **阅读建议**：每完成一个里程碑，填 **「与真 Tomcat 对照表」**（文末模板），再进入下一里程碑。

---

## 12.0 工程与包结构建议

```text
mini-cat/
├── pom.xml
└── src/main/java/com/minicat/
    ├── M1BlockingServer.java
    ├── M2NioServer.java
    ├── http/
    │   ├── HttpRequest.java
    │   ├── HttpResponse.java
    │   └── HttpParser.java
    ├── M3WebSocketServer.java      （或拆 ws 子包）
    └── M4SessionIntegration.java   （说明文档 + 对接 Redis 的示例类）
```

**依赖**：M1～M2 可 **零依赖**；M4 若用 Redis，增加 **Jedis/Lettuce** 即可。

---

## 12.1 里程碑 M1：阻塞式 HTTP Server

### 12.1.1 目标映射（对照真 Tomcat）

| 迷你实现 | 真 Tomcat |
|----------|-----------|
| `ServerSocket.accept()` | Acceptor + `Endpoint` |
| 一线程处理一连接（或线程池） | `Executor` + `SocketProcessor` |
| 读字节 → 解析请求行/头 | `Http11Processor` |
| 写 `HTTP/1.1 200` + body | `OutputBuffer` |

### 12.1.2 最小功能清单

- [ ] 监听 **单端口**（如 `8080`）。
- [ ] 解析 **`GET /path HTTP/1.1`** 与 **`Host`** 头（可简化：只读到第一个 `\r\n\r\n`）。
- [ ] **路由表**：`Map<String, Handler>`，`/hello` 返回 `text/plain`。
- [ ] **POST**：读 `Content-Length` 体（上限防 OOM，如 1MB）。
- [ ] **错误**：未知路径 `404`，异常 `500`（返回简单 HTML 文本即可）。

### 12.1.3 代码骨架（阻塞版主循环）

```java
// 教学伪代码：线程池 + 每连接 Runnable
ServerSocket ss = new ServerSocket(8080);
ExecutorService pool = Executors.newFixedThreadPool(200);
while (true) {
    Socket s = ss.accept();
    pool.submit(() -> handle(s)); // handle 内读请求、写响应、finally close
}
```

### 12.1.4 验收标准

| 项 | 标准 |
|----|------|
| 功能 | GET/POST、路由、404/500 |
| 稳定性 | **`ab -n 10000 -c 100`** 无大面积异常（允许自行调线程池） |
| 文档 | README 一条 **启动命令** + **curl 示例** |

### 12.1.5 性能门槛说明

「**100 并发稳定**」指：**错误率可接受、进程不崩溃**；RT 不必优化。重点观察 **线程数与 CPU**——为 M2 铺垫。

---

## 12.2 里程碑 M2：非阻塞式 HTTP Server

### 12.2.1 目标映射

| 迷你实现 | 真 Tomcat |
|----------|-----------|
| `Selector` + `ServerSocketChannel` | `NioEndpoint` |
| `OP_ACCEPT` / `OP_READ` | Poller 事件 |
| 读缓冲累积直到 `\r\n\r\n` | 输入缓冲与半包 |
| 业务处理放 **另一线程池**（可选） | Worker 与 IO 线程分离 |

### 12.2.2 最小功能清单

- [ ] **单线程 `Selector`** 处理 **accept + read**（或 accept 单线程 + read 多 selector，进阶）。
- [ ] 每通道 **附加 `ByteBuffer`/`Attachment`** 保存半包。
- [ ] 同一连接 **Keep-Alive**：处理完一个请求后 **不关闭**，继续读下一请求（简化可做 **HTTP/1.0 短连接** 先跑通，再升级）。
- [ ] **长请求演示**：某路径 `sleep 2s` 再响应；对比 M1 **线程占用**（M2 若用单线程 Selector 处理 sleep 会阻塞整个服务器——因此必须 **把耗时逻辑提交 `Executor`**，体会 **IO 线程与业务线程分离**）。

### 12.2.3 关键难点（必读）

1. **半包**：一次 `read` 可能只有半个头；需 **compact/flip** 与 **累积解析**。
2. **写半包**：`write` 可能只写了一部分，需 **OP_WRITE** 继续写（可第二版再做）。
3. **背压**：读太快、业务太慢 → **队列有界**。

### 12.2.4 验收标准

| 项 | 标准 |
|----|------|
| 功能 | 至少 **GET** + 简单路由；**Keep-Alive** 或文档说明为何分阶段实现 |
| 对比实验 | 同 **长请求路径**，M1 vs M2 **工作线程/Selector 线程** 占用文字说明 + 截图或 `jstack` 摘要 |
| 文档 | 说明 **哪段代码对应 `NioEndpoint` 哪类逻辑**（自评即可） |

---

## 12.3 里程碑 M3：WebSocket Server

### 12.3.1 目标映射

| 迷你实现 | 真 Tomcat |
|----------|-----------|
| 识别 `Upgrade: websocket` | `WsHttpUpgradeHandler` 前序 HTTP |
| `Sec-WebSocket-Accept` | RFC6455 握手 |
| 帧解析（文本帧即可） | `WsFrame` 处理 |
| 房间广播 | `ServerContainer` + Endpoint 管理 |

### 12.3.2 握手最小步骤（复习第5章）

1. 读 HTTP 请求，校验 **`GET`**、`Upgrade: websocket`、`Sec-WebSocket-Key`。
2. 计算 **`Sec-WebSocket-Accept = Base64(SHA1(key + GUID))`**，GUID 固定为 RFC 规定常量。
3. 返回 **`101 Switching Protocols`**，之后 **同一 TCP** 上按 **WebSocket 帧** 读写。

### 12.3.3 实现建议

- **M2 的 NIO 连接**上，握手完成后 **切换解码器**：从 HTTP 解析切到 **帧解析状态机**。
- 先支持 **客户端文本帧** `FIN=1、opcode=1`，**掩码**按 RFC 读 4 字节 masking key。
- **广播**：`CopyOnWriteArraySet<SocketChannel>` 或 **Session id → Channel** 映射。

### 12.3.4 验收标准

| 项 | 标准 |
|----|------|
| 功能 | 浏览器 **`new WebSocket`** 能连上；服务端 **echo** 或 **房间广播** 二选一 |
| 断线 | 对端关闭后 **移除会话**，无 **NPE 风暴** |
| 压测 | **≥100 连接** 保持 5 分钟，堆与句柄 **无明显线性暴涨**（粗测即可） |

---

## 12.4 里程碑 M4：Session 共享（分布式）

### 12.4.1 教学定义

「分布式 Session」在迷你实现里 **不必** 复制 Tomcat `DeltaManager`；推荐二选一：

| 方案 | 做法 | 对照生产 |
|------|------|----------|
| **A. 有状态共享存储** | `sessionId → JSON` 存 **Redis**，每次请求带 Cookie | 外置 Session |
| **B. 无状态签名 Cookie** | **JWT** 或 **HMAC 签名** 的 Session Cookie，各节点只校验签名 | 无服务器亲和 |

### 12.4.2 最小功能清单（方案 A 示例）

- [ ] 登录接口（可假登录）返回 **`Set-Cookie: MCSESSIONID=...`**。
- [ ] 后续请求解析 Cookie，**Redis `GET`** 取用户；不存在则 **401**。
- [ ] **第二个实例**（或第二个端口进程）读 **同一 Redis**，验证 **切换节点仍登录**。

### 12.4.3 可观测性（验收）

- [ ] 日志或指标：**Redis 命中/未命中次数**、**session 创建数**。
- [ ] **告警规则**（纸面即可）：例如 Redis **连续失败** N 次 → 降级策略说明。

### 12.4.4 验收标准

| 项 | 标准 |
|----|------|
| 功能 | **节点切换会话不丢**（演示：LB 或手动换端口访问） |
| 文档 | **序列化格式、TTL、续期策略** 写清 |

---

## 12.5 扩展挑战（选做）

| 挑战 | 说明 |
|------|------|
| **Filter 链** | 在调用 Handler 前插入 **`List<Filter>`**，类似 `ApplicationFilterChain` |
| **迷你 Pipeline/Valve** | `List<Valve>`，`invoke(ctx)` 传递 **`MiniRequest/MiniResponse`** |
| **配置热更新** | 监听文件 `routes.json` 变更，`reload` 路由表（注意线程安全） |
| **HTTP/1.1 chunked** | 解析 `Transfer-Encoding: chunked`（进阶） |

---

## 12.6 与真 Tomcat 对照表（每里程碑自填）

```markdown
| 模块 | mini-cat 类/方法 | Tomcat 类（参考） |
|------|------------------|-------------------|
| 阻塞 IO | | NioEndpoint 之前的 BIO 实现思路 |
| NIO | | org.apache.tomcat.util.net.NioEndpoint |
| HTTP 解析 | | Http11Processor |
| WebSocket | | WsHttpUpgradeHandler |
| Session | | Manager / 外置 Session 插件 |
```

---

## 本章小结

- **M1→M2**：从 **「一连接一线程」** 到 **「IO 多路复用 + 可选业务池」**。
- **M3**：把 **HTTP 与 WebSocket** 统一在 **一条 TCP 生命周期** 上。
- **M4**：Session **要么集中存，要么无状态签**，避免 **魔法默认**。

---

## 自测练习题

1. M2 若 **所有逻辑都在 Selector 线程**里执行，长请求会造成什么 **全局现象**？如何改？
2. WebSocket **掩码**为什么 **仅客户端→服务端** 帧需要？（RFC 规定）
3. 方案 B（JWT）下，**登出** 通常如何实现？与 **Redis Session** 对比优缺点。

---

## 课后作业

### 必做

1. 完成 **M1**，提交 **README + ab 结果截图**。
2. 完成 **M2** 最小 GET，提交 **与 M1 对比说明**（不少于 200 字）。
3. 填写 **12.6 对照表** 至少 **4 行**。

### 选做

1. 实现 **M3 echo**，用浏览器控制台验收。
2. 实现 **M4 方案 A**，录 **30 秒** 切换节点仍登录的演示（可用两个端口模拟两节点）。
3. 实现 **12.5** 中任一项，附设计说明。

---

## 延伸阅读

- RFC 7230（HTTP/1.1）、RFC 6455（WebSocket）。
- 第2章：[`第2章-驯猫架构课-Tomcat内核拆解.md`](第2章-驯猫架构课-Tomcat内核拆解.md)
- 第5章：[`第5章-WebSocket贪吃蛇.md`](第5章-WebSocket贪吃蛇.md)

---

*本稿为专栏第12章初稿，可与总纲 [`专栏.md`](专栏.md) 对照使用。*
