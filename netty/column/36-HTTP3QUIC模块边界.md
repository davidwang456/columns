# 36-HTTP3QUIC模块边界

## 1. 项目背景

随着移动网络与弱网场景增多，某内容分发平台计划引入 HTTP/3 提升首包时延与丢包恢复能力。团队在 PoC 阶段很快跑通了 QUIC demo，却在落地时遇到大量边界问题：现有四层 LB 不支持 UDP 粘性、证书与密钥轮换策略不同、观测体系仍按 TCP 指标建模、部分中间件只认 HTTP/1.1 语义。结果是“能跑”但“不可运营”。

HTTP/3 不只是协议升级，而是传输层到应用层的系统工程变更。Netty 提供了 QUIC 相关模块（基于 `netty-incubator-codec-quic`），但它与传统 `Channel` 栈在连接建立、流控、拥塞、迁移等方面存在显著差异。高级篇要回答的是模块边界：哪些逻辑可复用，哪些必须重写；哪些指标沿用，哪些要新增；灰度如何设计，故障如何回退。

本章给出“最小可运营”的边界实践：先分层识别改造面，再做双栈并存演进，避免一刀切迁移引发全链路风险。

## 2. 项目设计（剧本式交锋对话）

| 角色 | 定位 |
| --- | --- |
| 小胖 | 业务视角提出诉求与约束 |
| 小白 | 工程实现与落地执行 |
| 大师 | 架构把关与取舍决策 |

### 第一轮

小胖：HTTP/3 不就是 HTTP/2 换个壳吗？  
小白：多路复用不是早就有了，为什么还要 QUIC？  
大师：QUIC 的关键是基于 UDP、自带拥塞与重传控制、连接迁移能力。它把很多传输控制从内核挪到用户态，收益与复杂度同时上升。

技术映射：从坐火车变成自驾，路线更灵活，但你得自己管导航和油耗。

### 第二轮

小胖：我把原有 HTTP handler 直接复用不行吗？  
小白：流级别控制和连接级别控制边界怎么划？  
大师：应用语义可部分复用，但连接管理、流控策略、观测模型都要重做，尤其是 UDP 链路治理。

技术映射：同一家餐厅菜单可复用，但外卖配送体系要重建。

### 第三轮

小胖：上线怎么控风险？  
小白：是按用户灰度还是按地域灰度？  
大师：建议双维灰度：先地域小流量，再用户分层；并保留 HTTP/2 回退。任何指标异常都要秒级切回。

技术映射：新航线先试飞短途，稳定后再放大航班。

## 3. 项目实战

### 3.1 环境准备

- JDK 17、Netty 4.1.x、`netty-incubator-codec-quic`
- 支持 UDP 的 LB/防火墙策略
- TLS 证书与 ALPN 配置
- 指标：握手成功率、0-RTT 命中率、流重传率、回退率

### 3.2 分步实现

**步骤目标 1：搭建 HTTP/2 + HTTP/3 双栈入口。**

```java
if (enableHttp3) {
    startQuicServer(quicPort);
}
startHttp2Server(tcpPort);
```

**步骤目标 2：QUIC 流处理与应用 handler 对接。**

```java
QuicChannelBootstrap bootstrap = QuicChannel.newBootstrap(channel)
    .streamHandler(new ChannelInitializer<QuicStreamChannel>() {
        @Override
        protected void initChannel(QuicStreamChannel ch) {
            ch.pipeline().addLast(new Http3ServerConnectionHandler(...));
            ch.pipeline().addLast(new BizHandler());
        }
    });
```

**步骤目标 3：实现可回退路由。**

```java
if (metrics.http3ErrorRate() > threshold) {
    featureFlags.disable("http3");
}
```

命令：

命令示例：
```bash
curl --http3 -I https://example.com/
curl --http2 -I https://example.com/
```

**步骤目标 4：弱网回放验证。**

命令示例：
```bash
tc qdisc add dev eth0 root netem loss 2% delay 40ms
```

对比 HTTP/2 与 HTTP/3 在丢包场景下的 tail latency。

可能遇到的坑：

1. UDP 被中间网络设备限流或丢弃。  
2. 证书/ALPN 配置不一致导致握手失败。  
3. 观测仍按 TCP 习惯，只看连接数不看流层异常。

### 3.3 完整代码清单

- `codec-http3` 与 `netty-incubator-codec-quic` 相关模块
- 业务入口：`gateway/http3/Http3Bootstrap.java`
- 回退控制：`gateway/feature/TransportFlagService.java`
- 弱网脚本：`bench/netem_http3_compare.sh`

### 3.4 测试验证

命令示例：
```bash
curl --http3 -I https://127.0.0.1:8443/
```

命令示例：
```bash
for i in {1..100}; do curl --http3 -s -o /dev/null https://127.0.0.1:8443/ping; done
```

验收：

- 握手成功率达标
- 弱网下 P99 优于或不劣于 HTTP/2
- 回退开关可在异常时快速生效

#### 验证口径

- [ ] **功能**：核心用例可复现，关键输入输出与预期一致。
- [ ] **稳定性**：连续压测或重复执行无异常抖动、无明显长尾退化。
- [ ] **可观测性**：日志、指标与关键错误信号可定位并支持问题回溯。

## 4. 项目总结

### 4.1 优点&缺点

| 维度 | 优点 | 缺点 |
| --- | --- | --- |
| 时延 | 弱网与丢包场景恢复更快 | 基础设施改造成本高 |
| 传输能力 | 连接迁移与流级控制更灵活 | 观测与调试复杂度提升 |
| 架构演进 | 与现代浏览器生态匹配 | 双栈期维护成本增加 |

### 4.2 适用场景

适用：

1. 移动网络访问占比高的平台
2. 对首包和长尾时延敏感的内容服务
3. 能承接 UDP 运维治理的团队

不适用：

1. 网络路径大量屏蔽 UDP 的企业内网
2. 无灰度回退能力的单体入口

### 4.3 注意事项

- 协议升级必须与网络团队联动，不是应用单方可完成。
- 双栈阶段要统一鉴权、限流、日志口径，避免对账混乱。
- 0-RTT 需评估重放风险，不可直接用于非幂等接口。

### 4.4 常见踩坑经验

1. **故障案例：HTTP/3 成功率低于 60%**  
   根因：边缘防火墙默认丢弃 UDP 大包。
2. **故障案例：灰度后投诉增多**  
   根因：回退条件过宽，错误流量持续停留在 HTTP/3。
3. **故障案例：监控“看起来正常”但业务超时**  
   根因：缺少流级别重传与阻塞指标，盲区严重。

### 4.5 思考题

1. 你会如何定义 HTTP/3 灰度的“放量门槛”与“自动回退门槛”？
2. 在 QUIC 场景下，哪些传统 TCP 指标应被替换或弱化？

答案见：[附录-思考题答案索引](附录-思考题答案索引.md)
