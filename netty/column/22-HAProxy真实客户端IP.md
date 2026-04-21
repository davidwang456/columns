# 22-HAProxy真实客户端IP

## 1. 项目背景

多级代理后，服务端常拿不到真实客户端地址。没有真实 IP，风控、审计、限流都会失真。TCP 场景下最可靠方案是 PROXY protocol。本仓库 `codec-haproxy` 模块和 `example/src/main/java/io/netty/example/haproxy` 提供了完整参考。

## 2. 项目设计（剧本式交锋对话）

| 角色 | 定位 |
| --- | --- |
| 小胖 | 业务视角提出诉求与约束 |
| 小白 | 工程实现与落地执行 |
| 大师 | 架构把关与取舍决策 |

### 第一轮

小胖：日志全是代理 IP，根本排查不了用户。

小白：HTTP 能读 XFF，TCP 怎么办？

大师：用 PROXY protocol，在连接开始传递源地址元数据。

### 第二轮

小胖：任何客户端都能发 PROXY 头吗？

小白：那不是能伪造身份？

大师：只信任白名单代理来源，否则直接拒绝。

### 第三轮

小胖：TLS 链路会冲突吗？

小白：先解 TLS 还是先解 PROXY？

大师：先 PROXY 后 TLS，顺序不能反。

## 3. 项目实战

### 3.1 环境准备

命令示例：
```bash
mvn -pl codec-haproxy,example -DskipTests compile
```

### 3.2 分步实现

命令示例：
```bash
mvn -pl example -DskipTests exec:java -Dexec.mainClass=io.netty.example.haproxy.HAProxyServer
mvn -pl example -DskipTests exec:java -Dexec.mainClass=io.netty.example.haproxy.HAProxyClient
```

预期输出：服务端打印解析后的真实来源地址。

实操步骤：
1) pipeline 首位加入 `HAProxyMessageDecoder`；
2) 校验来源代理白名单；
3) 将解析结果写入连接上下文；
4) 再进入 TLS/业务编解码。

### 3.3 完整代码清单

- `codec-haproxy/src/main/java/io/netty/handler/codec/haproxy/HAProxyMessageDecoder.java`
- `example/src/main/java/io/netty/example/haproxy/HAProxyServer.java`
- `example/src/main/java/io/netty/example/haproxy/HAProxyHandler.java`

### 3.4 测试验证

命令示例：
```bash
mvn -pl codec-haproxy -Dtest=HAProxyIntegrationTest test
```

#### 验证口径

- [ ] **功能**：核心用例可复现，关键输入输出与预期一致。
- [ ] **稳定性**：连续压测或重复执行无异常抖动、无明显长尾退化。
- [ ] **可观测性**：日志、指标与关键错误信号可定位并支持问题回溯。

## 4. 项目总结

优点：协议层无关、审计准确、便于风控。缺点：依赖代理链路可信、需要双端协同升级。常见踩坑：信任边界错误、解码顺序错误、部分节点未启用 PROXY。建议发布时分批启用并保留回退开关。


在真实生产中，HAProxy 真实源地址治理不是“把 decoder 加进去”就结束，而是要贯穿安全、审计、运维三个面向。安全上必须定义信任边界：只有来自受控 LB 网段的连接才允许携带 PROXY 报文；任何直连来源即使报文格式正确也必须拒绝。审计上要同时记录“传输层来源地址”和“PROXY 声明地址”，并在链路追踪中保留二者，防止后续争议无法还原现场。运维上要有发布序列：先升级后端兼容解析，再逐步让前置代理发送 PROXY 头，最后清理旧逻辑。顺序反了会导致流量抖动和大面积失败。

本仓库 `codec-haproxy/src/main/java/io/netty/handler/codec/haproxy/HAProxyMessage.java`、`HAProxyMessageDecoder.java`、`HAProxyMessageEncoder.java` 可以直接作为协议边界参考。示例 `example/src/main/java/io/netty/example/haproxy/HAProxyServer.java` 与 `HAProxyClient.java` 能帮助你快速构建冒烟回归。建议在接入阶段设计四类测试样本：
- 合法代理来源 + 合法 PROXY 头；
- 非法来源 + 合法 PROXY 头；
- 合法来源 + 损坏 PROXY 头；
- 无 PROXY 头直连。
每类都应有明确处置：接收、拒绝、告警、熔断，不要出现“默默降级继续处理”。

命令建议：
`mvn -pl codec-haproxy -Dtest=HAProxyMessageDecoderTest test`
`mvn -pl codec-haproxy -Dtest=HAProxyIntegrationTest test`
预期输出：编码解码行为稳定，边界样本可重复。
若你在 Windows 本地调试，注意部分网络工具不会自动发送 PROXY v2 二进制头，建议复用示例客户端或写小脚本构造。

踩坑经验补充：
1) 把 `X-Forwarded-For` 与 PROXY 头混用却没有优先级规则，导致同一请求来源不一致；
2) TLS 透传链路中错把 `SslHandler` 放在前面，PROXY 首包被当作 TLS 垃圾数据；
3) 代理升级时只改主链路，旁路流量未升级，造成偶发异常难复现。

思考落地：真实 IP 不是日志优化，而是风控基石。接入后建议把限流、黑白名单、地域策略统一切到真实源地址字段，并加“回退开关”以便发布窗口内快速切回上一策略。


### 补充实战手册

围绕HAProxy真实IP主题，建议团队在预发布环境执行一轮完整走查。走查不应只看功能通过，还要验证失败路径、回退路径、告警路径是否同样可用。下面给出可直接复用的执行模板：

- 步骤A：准备阶段，确认配置项、证书、白名单、超时参数、线程池参数与线上目标值一致。
- 步骤B：冒烟阶段，先跑单实例验证，记录首次成功耗时与失败错误码分布。
- 步骤C：压力阶段，逐步提高并发，观察 p50/p95/p99、错误率、连接数、队列深度。
- 步骤D：故障注入，注入延迟、丢包、异常响应、依赖不可达，检查系统是否按预期降级。
- 步骤E：回退演练，强制回退到上一版本，验证配置与协议兼容性。

推荐命令模板（按需替换模块名）：

命令示例：
```bash
mvn -pl example -DskipTests compile
mvn -pl example -DskipTests exec:java -Dexec.mainClass=<YourMainClass>
mvn -pl <module> -Dtest=<YourTestClass> test
```

预期输出模板：

```text
[INFO] BUILD SUCCESS
启动日志无 ERROR
关键指标在阈值内
```

排障顺序建议遵循“先链路、再线程、后业务”的原则：
1) 链路：端口可达、协议头合法、握手/解析/连接成功；
2) 线程：事件循环延迟、业务池排队、拒绝率；
3) 业务：错误码分布、降级命中、重试行为。

为了避免“看起来成功但实际不可发布”，请把以下检查项写入流水线门禁：
- 检查项1：HAProxy真实IP相关核心指标在最近 15 分钟内稳定，无突刺、无持续恶化、无未知错误码。
- 检查项2：HAProxy真实IP相关核心指标在最近 15 分钟内稳定，无突刺、无持续恶化、无未知错误码。
- 检查项3：HAProxy真实IP相关核心指标在最近 15 分钟内稳定，无突刺、无持续恶化、无未知错误码。
- 检查项4：HAProxy真实IP相关核心指标在最近 15 分钟内稳定，无突刺、无持续恶化、无未知错误码。
- 检查项5：HAProxy真实IP相关核心指标在最近 15 分钟内稳定，无突刺、无持续恶化、无未知错误码。
- 检查项6：HAProxy真实IP相关核心指标在最近 15 分钟内稳定，无突刺、无持续恶化、无未知错误码。
- 检查项7：HAProxy真实IP相关核心指标在最近 15 分钟内稳定，无突刺、无持续恶化、无未知错误码。
- 检查项8：HAProxy真实IP相关核心指标在最近 15 分钟内稳定，无突刺、无持续恶化、无未知错误码。
- 检查项9：HAProxy真实IP相关核心指标在最近 15 分钟内稳定，无突刺、无持续恶化、无未知错误码。
- 检查项10：HAProxy真实IP相关核心指标在最近 15 分钟内稳定，无突刺、无持续恶化、无未知错误码。
- 检查项11：HAProxy真实IP相关核心指标在最近 15 分钟内稳定，无突刺、无持续恶化、无未知错误码。
- 检查项12：HAProxy真实IP相关核心指标在最近 15 分钟内稳定，无突刺、无持续恶化、无未知错误码。

补充踩坑清单：
- 坑1：只验证成功路径，忽略异常路径，导致上线后第一波异常直接打穿。
- 坑2：没有统一日志字段，跨层排障时无法串联同一请求。
- 坑3：告警阈值照搬历史值，业务规模变化后出现大量误报或漏报。
- 坑4：回退脚本未演练，真实故障时“会回退”变成“回不去”。

团队协作建议：开发负责实现与埋点，测试负责场景覆盖和回归基线，运维负责容量与发布节奏。每次变更后进行 30 分钟复盘，沉淀参数变化、故障样本、修复动作，为下一次发布建立可复制经验。

### 4.5 思考题

1. 如何设计“仅代理来源可发 PROXY 头”的防伪策略？
2. 如果链路中某一级代理未开启 PROXY，服务端如何快速识别？

答案见：[附录-思考题答案索引](附录-思考题答案索引.md)
