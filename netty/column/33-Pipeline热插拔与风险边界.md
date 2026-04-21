# 33-Pipeline热插拔与风险边界

## 1. 项目背景

某 API 网关为了支持灰度鉴权、临时限流和紧急降级，采用了运行时动态修改 `ChannelPipeline` 的方案：在线插入/移除 `ChannelHandler`。上线初期效果很好，后续却出现间歇性故障：部分连接突然不鉴权、少量请求重复解码、极端情况下抛 `NoSuchElementException`。根因并非单个 Handler 逻辑错误，而是“热插拔时序与线程边界”没有被系统化设计。

`ChannelPipeline` 是 Netty 的责任链中枢，支持运行时变更是其强大之处，也是高风险之处。因为链路变更涉及两个层面：一是结构一致性（前驱/后继指针、名称唯一性）；二是事件一致性（当前事件是否应经过新节点、旧节点何时彻底退出）。如果在错误线程执行变更、在不安全阶段替换编解码器，故障就会隐蔽且难复现。

本章目标：建立“可热插拔，但有边界”的工程实践，做到动态能力可用、回滚可控、故障可定位。

## 2. 项目设计（剧本式交锋对话）

| 角色 | 定位 |
| --- | --- |
| 小胖 | 业务视角提出诉求与约束 |
| 小白 | 工程实现与落地执行 |
| 大师 | 架构把关与取舍决策 |

### 第一轮

小胖：能热插拔不就说明随时都能改链路吗？  
小白：如果请求正在半包解码，突然替换 decoder 会怎样？  
大师：这就是边界。结构上能改，不代表语义上安全。半状态机 handler（如协议解码）不能在任意时刻无损替换。

技术映射：高速行驶中可以换导航，但不能随便拆发动机。

### 第二轮

小胖：那我把所有改动都包在 `synchronized` 里是不是就稳了？  
小白：Pipeline 不是本来就线程安全吗？  
大师：线程安全不等于业务安全。Netty 保证内部结构并发安全，但你仍要保证“在对应 EventLoop 上修改”和“事件迁移语义正确”。

技术映射：门禁系统再安全，错峰放人策略错了照样拥堵。

### 第三轮

小胖：怎么把风险降到可控？  
小白：有没有可回滚的标准动作？  
大师：用阶段化策略：预插入旁路 handler -> 双写观测 -> 切主 -> 延迟摘除旧 handler。每一步都有指标阈值和回滚点。

技术映射：道路改造先修辅路分流，再封主路，不是一锤子切换。

## 3. 项目实战

### 3.1 环境准备

- JDK 17、Netty 4.1.x  
- 测试场景：长连接 + 心跳 + 自定义协议解码  
- 压测工具：`wrk` + 自定义 TCP 半包脚本  
- 观测指标：decode error rate、pipeline mutation count、P99

### 3.2 分步实现

**步骤目标 1：封装在 EventLoop 上执行的变更入口。**

```java
static void safeModify(Channel ch, Consumer<ChannelPipeline> op) {
    EventLoop loop = ch.eventLoop();
    if (loop.inEventLoop()) {
        op.accept(ch.pipeline());
    } else {
        loop.execute(() -> op.accept(ch.pipeline()));
    }
}
```

**步骤目标 2：实现“插入新 handler，不立即移除旧 handler”的双轨。**

```java
safeModify(ch, p -> {
    if (p.get("newAuth") == null) {
        p.addAfter("oldAuth", "newAuth", new NewAuthHandler());
    }
});
```

运行 10 分钟仅观测，不切主。

**步骤目标 3：切主与延迟摘除。**

```java
safeModify(ch, p -> {
    if (p.get("router") != null) {
        p.replace("router", "router", new RouterHandler("newAuth"));
    }
});
// 延迟窗口后再摘除旧链路
ch.eventLoop().schedule(() -> safeModify(ch, p -> p.remove("oldAuth")),
        30, TimeUnit.SECONDS);
```

**步骤目标 4：构造半包和乱序场景。**

命令示例：
```bash
python scripts/send_fragment_packets.py --host 127.0.0.1 --port 9000
```

验证是否出现：

- 重复解码
- 状态机错位
- 心跳误判断线

可能遇到的坑：

1. handler 名称冲突导致替换失败。  
2. 切主后立即摘旧，导致在途请求失配。  
3. 多连接批量变更无节流，瞬时抖动放大。

### 3.3 完整代码清单

- `transport/src/main/java/io/netty/channel/DefaultChannelPipeline.java`
- 业务封装：`gateway/pipeline/PipelineHotSwapManager.java`
- 测试脚本：`scripts/send_fragment_packets.py`
- 压测脚本：`bench/pipeline_hotswap.sh`

### 3.4 测试验证

命令示例：
```bash
curl -i http://127.0.0.1:8080/health
```

命令示例：
```bash
wrk -t8 -c300 -d120s http://127.0.0.1:8080/api
```

验证口径：

- 热插拔期间错误率无显著上升
- P99 波动在阈值内
- 回滚命令可在 1 分钟内恢复旧链路

#### 验证口径

- [ ] **功能**：核心用例可复现，关键输入输出与预期一致。
- [ ] **稳定性**：连续压测或重复执行无异常抖动、无明显长尾退化。
- [ ] **可观测性**：日志、指标与关键错误信号可定位并支持问题回溯。

## 4. 项目总结

### 4.1 优点&缺点

| 维度 | 优点 | 缺点 |
| --- | --- | --- |
| 动态能力 | 快速灰度与回滚，减少重启 | 变更时序复杂，容易引入隐性 bug |
| 架构弹性 | 可按连接特征装配 handler | 观测与治理成本高 |
| 交付效率 | 紧急修复响应快 | 需要严格变更流程和演练 |

### 4.2 适用场景

适用：

1. 网关鉴权/限流策略灰度
2. 协议插件化扩展
3. 紧急降级开关注入

不适用：

1. 状态机高度耦合、不可中断的编解码过程
2. 无监控与回滚体系的团队

### 4.3 注意事项

- 所有 pipeline 变更统一走安全入口，禁止业务散落直接修改。
- 关键 handler 变更采用双轨与延迟摘除策略。
- 每次热插拔都要有变更批次号和审计日志。

### 4.4 常见踩坑经验

1. **故障案例：请求漏鉴权**  
   根因：切主时路由指向新 handler，但新 handler 尚未完成初始化。
2. **故障案例：偶发解码异常**  
   根因：半包处理期间替换 decoder，状态机丢失。
3. **故障案例：全站抖动**  
   根因：十万连接同时改 pipeline，事件循环瞬时堆积。

### 4.5 思考题

1. 如果必须在线替换有状态 decoder，你会如何设计“状态迁移”与“幂等重放”机制？
2. 如何用指标区分“业务流量异常”与“热插拔引入的链路异常”？

答案见：[附录-思考题答案索引](附录-思考题答案索引.md)
