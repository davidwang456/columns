# 20-TLS握手与SslHandler

## 1. 项目背景

在网关、IM、支付回调和内部 RPC 场景里，明文 TCP 的问题从来不是“会不会被攻击”，而是“什么时候被抓包复现”。很多团队第一次上 TLS 时，往往只停留在“把证书挂上去”，上线后才发现握手时延高、连接抖动时 CPU 飙升、证书轮转中断连接、双向认证把调试复杂度拉满。Netty 的 `SslHandler` 不是一个简单的“加密开关”，它连接了 `SSLEngine`、事件循环、ByteBuf 生命周期和异常传播链路。

本仓库里，`handler/src/main/java/io/netty/handler/ssl/SslHandler.java`、`example/src/main/java/io/netty/example/securechat`、`example/src/main/java/io/netty/example/http2/helloworld/frame/server/Http2ServerInitializer.java` 给了三条学习路径：第一条是握手与数据帧如何交错；第二条是应用层如何感知 `SslHandshakeCompletionEvent`；第三条是 TLS 放在不同协议栈位置时，对 pipeline 排序和错误处理的影响。真实生产里，TLS 失败并不总是证书问题，常见根因还包括：客户端 SNI 不匹配、服务端 cipher suite 不重叠、中间设备做 TLS 透传却插入非 TLS 字节、或者握手超时阈值跟发布窗口策略冲突。

所以这一章目标不是“让连接变绿锁”，而是把 TLS 当成可观测、可回滚、可演进的工程能力。你将把 `SslHandler` 的关键机制映射到操作步骤：如何生成测试证书、如何在示例里启用双向认证、如何验证握手事件、如何识别 `NotSslRecordException` 与 `SSLHandshakeException` 的差异，以及如何把这些信号纳入发布流程。

## 2. 项目设计（剧本式交锋对话）

| 角色 | 定位 |
| --- | --- |
| 小胖 | 业务视角提出诉求与约束 |
| 小白 | 工程实现与落地执行 |
| 大师 | 架构把关与取舍决策 |

### 第一轮

小胖：我们就做内网服务，真的要全链路 TLS 吗？感觉像给食堂窗口装银行金库门。

小白：内网也有横向移动风险，而且现在很多链路经过 sidecar、代理、mesh，抓包面比以前大。问题是 TLS 一开，吞吐会掉多少？

大师：先把目标讲清楚：不是“零损耗加密”，而是“可接受损耗换可审计安全”。Netty 里 TLS 主要成本在握手和加解密，不在业务 handler。你们先在 `SecureChatServer` 跑基线，再用相同消息规模对比明文。

大师技术映射：银行金库门对应 TLS 记录层；排队速度下降对应握手和对称加密 CPU 开销。

### 第二轮

小胖：那我是不是只要把 `SslHandler` 放在 pipeline 最前面就完了？

小白：我看 `PortUnificationServerHandler` 里会先 sniff 协议，如果先加 `SslHandler` 会不会把探测逻辑吃掉？

大师：对，顺序不是一刀切。纯 TLS 端口上 `SslHandler` 应该最前；端口统一场景要先做前几个字节探测，再决定是否替换为 TLS 子 pipeline。你们要区分“专用 443”与“统一入口 8443”。

大师技术映射：先验身份再开门是专用端口；先看来客证件再分流是端口统一协议嗅探。

### 第三轮

小胖：证书快过期时怎么不停机换？每次重启都像切总闸。

小白：可以热更新 `SslContext` 吗？旧连接怎么办？

大师：新连接切到新 `SslContext`，老连接保持原会话直到自然结束，这叫“连接级平滑过渡”。实现上通过原子引用持有当前 `SslContext`，`ChannelInitializer` 每次 init 时读取最新值。发布窗口里要监控握手失败率、握手耗时 p99、`SSLException` 分类占比。

大师技术映射：换闸刀会断全楼；分批换住户门锁不会影响已在屋内的人。

## 3. 项目实战

### 3.1 环境准备

- JDK 8/11/17 任一 LTS。
- Maven 3.8+。
- 在仓库根目录执行：

命令示例：
```bash
mvn -pl example -DskipTests compile
```

预期输出（节选）：

```text
[INFO] --- maven-compiler-plugin:...:compile (default-compile) @ netty-example ---
[INFO] BUILD SUCCESS
```

### 3.2 分步实现

1) 运行 `SecureChatServer` 验证握手。
2) 运行 `SecureChatClient` 并制造证书不信任，观察 `SSLHandshakeException`。
3) 对照 `Http2ServerInitializer` 验证 TLS 与 HTTP2 handler 顺序。
4) 监听 `SslHandshakeCompletionEvent`，记录成功率与失败分类。

命令：

命令示例：
```bash
mvn -pl example -DskipTests exec:java -Dexec.mainClass=io.netty.example.securechat.SecureChatServer
mvn -pl example -DskipTests exec:java -Dexec.mainClass=io.netty.example.securechat.SecureChatClient
openssl s_client -connect 127.0.0.1:8992 -servername localhost
```

常见坑：端口占用、证书链不完整、pipeline 顺序错误、把握手失败误判为网络波动。

### 3.3 完整代码清单

- `handler/src/main/java/io/netty/handler/ssl/SslHandler.java`
- `example/src/main/java/io/netty/example/securechat/SecureChatServer.java`
- `example/src/main/java/io/netty/example/securechat/SecureChatServerInitializer.java`
- `example/src/main/java/io/netty/example/http2/helloworld/frame/server/Http2ServerInitializer.java`

### 3.4 测试验证

- 握手成功率 > 99.9%
- 失败按异常类型分桶
- 发布窗口内 p99 握手时延稳定

#### 验证口径

- [ ] **功能**：核心用例可复现，关键输入输出与预期一致。
- [ ] **稳定性**：连续压测或重复执行无异常抖动、无明显长尾退化。
- [ ] **可观测性**：日志、指标与关键错误信号可定位并支持问题回溯。

## 4. 项目总结

TLS 接入在 Netty 中的正确姿势，是把 `SslHandler` 当作连接状态机而不是静态过滤器。优点是保密合规、支持双向认证、可和 HTTP2 升级协同；缺点是握手增加时延、证书管理复杂、排障跨网络与安全边界。适用在公网 API、跨机房调用、账号和支付链路；不适用于极短生命周期且绝对低时延探针。

常见踩坑：证书过期巡检缺失；代理误插明文字节触发 `NotSslRecordException`；客户端时间漂移导致证书尚未生效。推广上建议开发先掌握握手事件，运维接入证书生命周期告警，测试覆盖证书边界和协议错序。

### 4.5 思考题

1. 如果网关同时支持明文探活与 TLS 业务流量，如何设计嗅探窗口避免误判？
2. 当握手 p99 升高但失败率未上升时，你优先排查哪些层面？

答案见：[附录-思考题答案索引](附录-思考题答案索引.md)
