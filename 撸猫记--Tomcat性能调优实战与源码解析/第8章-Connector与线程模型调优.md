# 第8章 Connector 与线程模型调优（正文初稿）

> 对应总纲：**驯兽师修炼 · Connector**。读完本章，你应能解释 **NIO Connector 下连接数与线程数为何脱钩**、关键 **`server.xml` 参数** 的含义与联动，并会用 **「场景 → 参数 → 风险」矩阵** 做有回滚的调参。  
> **版本提示**：Tomcat 主版本不同，默认 **protocol 类名**、是否仍提供 **BIO** 实现会有差异，**以你安装的 `server.xml` 注释与官方文档为准**。

---

## 本章导读

- **你要带走的三件事**
  1. **`maxConnections`** 管「最多允许多少条 TCP 连接在处理管道里」，**`maxThreads`** 管「同时有多少个请求在跑 Servlet」；二者 **不相等**。
  2. **`acceptCount`** 是 **全连接队列**（OS `listen backlog` 与 Tomcat 侧逻辑的组合直觉）：高并发下过小会 **拒绝连接**。
  3. **超时族**：`connectionTimeout`、`keepAliveTimeout`、`asyncTimeout`（应用层）要 **与上游 LB/API 网关对齐**，否则出现「一端以为断了、一端还在等」。

- **阅读建议**：先打开本机 **`server.xml` 的 `<Connector>`**，对照 **8.2 参数表** 标一遍；再按 **8.6 矩阵** 只改 **一个** 参数做压测（呼应第6章）。

---

## 课程版扩展（对应 `优化1.md` 第5章）

> 对应原课纲：`【Tomcat调优】训猫师养成记之解放双手`

| 小节 | 时长（分钟） | 详细说明 |
|:---|:---:|:---|
| 调教Connector第一式：Connector如何选型？ | 30 | BIO/NIO/NIO2/APR 场景选型，协议栈差异 |
| 调教Connector第二式：如何配置最大线程数？ | 10 | `maxThreads` 与 CPU、RT、下游依赖的关系 |
| 调教Connector第三式：如何配置响应超时时间？ | 10 | 请求超时与网关超时对齐，避免误判 |
| 调教Connector第四式：如何配置连接超时时间？ | 10 | `connectionTimeout`、`keepAliveTimeout` 实战 |

### 课程安排建议（60分钟）

- 30 分钟做选型和线程模型直觉建立
- 30 分钟做三类超时参数与压测对比演示

---

## 8.1 与第6章的衔接：Connector 的四段式

| 段落 | Connector 典型内容 |
|------|---------------------|
| **指标** | RT、TPS、拒绝连接数、`503`、`SocketTimeout`、活跃线程、`currentThreadBusy`、队列长度 |
| **观察** | Access log、JMX（`ThreadPool` / `RequestProcessor`）、`netstat`、压测错误信息、线程 dump |
| **参数** | `protocol`、`maxThreads`、`minSpareThreads`、`maxConnections`、`acceptCount`、各类 timeout、`compression` |
| **风险** | 线程过大 → 上下文切换与堆栈内存；连接过大 → FD 与内存；超时过短 → 误杀长请求 |

---

## 8.2 `<Connector>` 核心参数速查

以下名称以 **Tomcat 9/10 常见属性** 为参考；若 IDE/文档提示废弃或改名，以官方 **Configuration Reference** 为准。

### 8.2.1 线程与连接

| 属性 | 含义（直觉） | 调大/调小影响 |
|------|--------------|----------------|
| **`maxThreads`** | 处理请求的 **工作线程上限**（常见线程名 `http-nio-8080-exec-*`） | 过大：CPU 抖动、栈内存上涨；过小：排队、RT 上升 |
| **`minSpareThreads`** | 空闲时保留的线程数 | 影响冷启动后首批请求延迟 |
| **`maxConnections`** | NIO/NIO2：**同时处理的连接数上限**（含 Keep-Alive 空闲连接等，具体语义见文档） | 过小：新连接被拒绝；过大：FD、内存压力 |
| **`acceptCount`** | **等待 accept 的队列长度**（常与 OS backlog 相关） | 过小：瞬时尖峰下 **Connection refused** |

**关键关系（教学版）**：

- 许多场景下：**活跃请求处理** 受 **`maxThreads`** 限制；**更多连接** 可以 **挂着**，但 **不能无限**——受 **`maxConnections`** 与系统资源约束。
- **「连接很多、线程很满」** 时，新请求在队列里等线程 → P99 飙升。

### 8.2.2 超时与 Keep-Alive

| 属性 | 含义（直觉） |
|------|--------------|
| **`connectionTimeout`** | 等待 **新请求**（如下一个 HTTP 请求行）的最长时间（毫秒）；常用于 **建立连接后等首包/下一请求** |
| **`keepAliveTimeout`** | **长连接** 上两次请求之间的最大空闲时间 |
| **`maxKeepAliveRequests`** | 一条连接上最多处理多少个请求后关闭（`-1` 常表示不限制，以文档为准） |

**工程要点**：前面 **Nginx / SLB / API 网关** 的 `proxy_read_timeout` 等，应 **大于或协调** 应用侧超时，避免 **网关先断、Tomcat 仍算活着**。

### 8.2.3 其它常用项

| 属性 | 说明 |
|------|------|
| **`port` / `protocol` / `address`** | 监听端口、协议实现类、绑定地址 |
| **`compression`** | 压缩响应；CPU 换带宽 |
| **`URIEncoding`** | GET 查询串编码；中文参数必查 |
| **`executor`** | 共享线程池（多个 Connector 复用） |

### 8.2.4 `server.xml` 示例片段（仅示意）

```xml
<Connector port="8080"
           protocol="HTTP/1.1"
           maxThreads="200"
           minSpareThreads="10"
           maxConnections="8192"
           acceptCount="100"
           connectionTimeout="20000"
           keepAliveTimeout="60000"
           maxKeepAliveRequests="100"
           URIEncoding="UTF-8"
           redirectPort="8443" />
```

> `protocol="HTTP/1.1"` 时，Tomcat 会选 **默认实现**（多为 NIO）；若要 **显式** 指定，可用文档中的 **`org.apache.coyote.http11.Http11NioProtocol`** 等形式。

---

## 8.3 协议选型：NIO / NIO2 / APR（概念）

| 实现倾向 | 特点 | 适用直觉 |
|----------|------|----------|
| **NIO** | 成熟、默认常见 | 一般 Web 应用首选 |
| **NIO2** | 依赖 JDK 异步通道 | 部分场景可对比压测 |
| **APR/native** | 可走 **OpenSSL**、部分路径 **sendfile** 更贴近 OS | 静态大流量、TLS 终结在 Tomcat 时值得评估 |

**注意**：APR 需 **native 库** 与部署成本；**TLS 常终止在 LB** 时，APR 优势未必显现。

---

## 8.4 源码锚点：`org.apache.tomcat.util.net.NioEndpoint`

**读什么**（第一遍目标）：

1. **Acceptor**：接受新连接，把 **SocketChannel** 交给后续阶段。
2. **Poller**（或等价角色）：**Selector** 监听 **可读/可写** 事件，把就绪连接交给处理器。
3. **Executor**：**工作线程池** 执行 **`SocketProcessor`**（或等价 Runnable），最终进入 **Coyote Processor → Adapter → Servlet**。

**你要建立的直觉图**：

```mermaid
flowchart LR
  acc[Acceptor]
  poller[Poller_Selector]
  exec[Executor_maxThreads]
  proc[Processor_Servlet]
  acc --> poller
  poller --> exec
  exec --> proc
```

**读法提示**：不必第一遍就啃完 `NioEndpoint` 每个字段；抓住 **「连接何时从 poller 进线程池」** 与 **`maxThreads` 打满时队列行为**。

---

## 8.5 典型故障与参数方向（非绝对）

| 现象 | 可能方向 | 提醒 |
|------|----------|------|
| 压测大量 **`Connection refused`** | 提高 **`acceptCount`**、检查 **`maxConnections`**、系统 `ulimit -n` | 同时查 SYN 队列、防火墙 |
| **P99 高**、线程 dump 大量 **`http-nio-*-exec` RUNNABLE** 在业务栈 | 先 **优化代码/SQL**；再评估 **`maxThreads`** 与机器核数 | 盲目加线程可能更差 |
| **线程很多但 CPU 低** | **阻塞在 IO**（DB/Redis/锁）；考虑异步化或扩容依赖 | 与第3章、第10章联动 |
| **Keep-Alive 连接过多** 占满 `maxConnections` | 调 **`keepAliveTimeout` / `maxKeepAliveRequests`**；LB 侧协调 | 观察是否 **慢客户端** |
| **上传/长轮询** 超时 | 调大 **`connectionTimeout`** 或应用侧 **异步**；对齐网关 | 过大易被 **慢攻击** 利用 |

---

## 8.6 关键产出：高并发参数调优矩阵（场景 → 参数 → 风险）

> 使用方式：选一行 → **只改「建议首要参数」** → 按第6章做回归 → 记录副作用。

| 场景 | 典型症状 | 建议首要参数（方向） | 常见风险 / 回滚信号 |
|------|----------|----------------------|----------------------|
| **瞬时尖峰 QPS** | `refused`、connect 失败 | ↑ `acceptCount`；必要时 ↑ `maxConnections` | FD 耗尽；回滚若 **RSS 暴涨** 或 **握手延迟** |
| **CPU 充足、线程成为瓶颈** | `currentThreadsBusy` 长期贴 `maxThreads`、P99 排队 | ↑ `maxThreads`（小步，如 +50） | 上下文切换加剧；**P99 变差** 则回滚 |
| **长连接 + 高并发** | 连接数高、处理慢 | `maxConnections` 与 **`maxThreads` 解耦** 审视；调 Keep-Alive | 调错导致 **频繁建连**，CPU 上涨 |
| **慢请求多** | 线程占满、RT 尾部长 | **先** 异步/削峰；Connector 仅缓解 | 加线程 **掩盖** 下游问题 |
| **小报文 API** | 吞吐 OK、延迟敏感 | 适度 ↑ `minSpareThreads` 减少冷启动 | 空闲线程占用栈内存 |
| **HTTPS 在 Tomcat** | CPU 在 TLS | APR/OpenSSL 或 **前置 LB 卸载 TLS** | 架构变更成本 |
| **容器 2c4g** | 线程过大 OOM 风险 | **限制** `maxThreads`（如 100～200 量级起）配合压测 | 与第7章 **`-Xss`、堆** 联动 |

---

## 8.7 `Executor` 共享线程池（进阶）

多个 `<Connector>` 指向同一 **`executor`** 可避免 **线程池碎片化**，但也会 **共享瓶颈**：一个 Connector 把线程打满会影响另一个。

```xml
<Executor name="tomcatThreadPool" maxThreads="300" minSpareThreads="20"/>
<Connector ... executor="tomcatThreadPool" />
```

**风险**：调试时问题归因变难；需 **统一监控** 线程池队列与活跃数。

---

## 8.8 与第7章、第9～10章的边界

- **线程很多 + Full GC**：可能是 **请求对象大** 或 **Session 大**（第7章、第11章），不是单纯加 `maxThreads`。
- **静态资源吃满连接**：优先 **CDN / 缓存 / sendfile**（第9章），而不是无限加连接。
- **线程 block 在 JDBC**：调 **连接池**（第10章）与 SQL。

---

## 本章小结

- **Connector** 调优 = **连接维度** + **线程维度** + **超时维度**，必须配合 **压测与 JMX/日志**。
- **`NioEndpoint`** 帮你把 **参数与现象** 对上号：连接 ≠ 线程。
- 用 **8.6 矩阵** 做 **有纪律的实验**，避免「抄一组 maxThreads」。

---

## 自测练习题

1. **`maxConnections` 达到上限** 时，客户端更可能看到什么现象？（连接拒绝 / 等待 / 503，结合你环境思考）
2. **`connectionTimeout` 与 `keepAliveTimeout`** 分别解决什么问题？各举一个场景。
3. 为什么 **「maxThreads = CPU 核数 × 100」** 这类公式 **不可靠**？

---

## 课后作业

### 必做

1. 导出当前 **`server.xml` 中所有 Connector** 的 **8.2.1～8.2.2** 相关属性表，标注 **默认值**（查文档）与 **当前值**。
2. 选 **8.6 中一行** 与你的系统最接近的场景，按 **第6章变更单** 做一次 **单参数** 压测对比（附前后数字）。
3. 在 `NioEndpoint` 中 **搜索 `maxThreads` 或 `executor`**，写 5 行：**谁在读这些配置**。

### 选做

1. 画 **线程状态图**：`Acceptor`、`Poller`、工作线程在 **一次 Keep-Alive 双请求** 中的协作（可手绘拍照）。
2. 对比 **开启/关闭 compression** 的 **TPS 与 CPU**，写结论。
3. 预习第9章：列出 **3 个** 适合用 **sendfile** 的资源类型。

---

## 延伸阅读

- Tomcat 官方：**Connector** Configuration Reference（对应你的主版本号）。
- 第2章：[`第2章-驯猫架构课-Tomcat内核拆解.md`](第2章-驯猫架构课-Tomcat内核拆解.md)（Connector 与 Pipeline 关系）。

---

*本稿为专栏第8章初稿，可与总纲 [`专栏.md`](专栏.md)、第6～7章对照使用。*
