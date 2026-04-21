# 38-自定义Codec与一致性测试收官

## 1. 项目背景

经过前面章节的性能、模型和源码优化，团队最终会回到一个决定系统上限的问题：协议编解码是否稳健。某交易网关曾因自定义 Codec 的一个长度字段处理错误，导致少量半包请求被误判为非法包，进而触发重试风暴。问题只在高并发 + 弱网 + 特定消息组合下出现，单元测试和简单联调都没暴露。事故后复盘发现：编码器/解码器实现了，但一致性测试体系并未建立。

自定义 Codec 不是“能跑就行”，而是“必须可证明地正确”：编码后解码应等价、边界输入应有确定行为、异常输入应快速失败且不污染连接状态、跨版本兼容要可回归验证。尤其在 Netty 场景，半包粘包、引用计数、Pipeline 顺序都可能让一个小 bug 放大为系统事故。

作为高级篇收官，本章给出一套可发布的 Codec 工程模板：协议设计约束、实现分层、自动化一致性测试、故障注入与发布验收闭环。

## 2. 项目设计（剧本式交锋对话）

| 角色 | 定位 |
| --- | --- |
| 小胖 | 业务视角提出诉求与约束 |
| 小白 | 工程实现与落地执行 |
| 大师 | 架构把关与取舍决策 |

### 第一轮

小胖：Codec 不就是 `encode`/`decode` 两个方法吗？  
小白：为什么线上总是“偶现解析失败”？  
大师：因为真实流量不是理想输入。半包、粘包、乱序、脏数据、版本混发，都会把简单实现打穿。

技术映射：会做一道菜不等于能开餐厅，后厨流程才是稳定性的关键。

### 第二轮

小胖：我写几个单测不就够了？  
小白：需要做到什么程度才算一致性？  
大师：至少要有：往返等价测试、边界值测试、模糊测试、跨版本兼容测试、长连接稳态测试。缺一项都可能留隐患。

技术映射：试驾一圈不等于耐久验证，车辆要跑全工况。

### 第三轮

小胖：上线怎么保证不翻车？  
小白：有没有发布前硬门槛？  
大师：建立发布闸门：一致性测试全绿、故障注入通过、灰度错误率达标、可快速回滚。把“经验”变成“机制”。

技术映射：飞机起飞前不是看感觉，而是按清单逐项确认。

## 3. 项目实战

### 3.1 环境准备

- JDK 17、Netty 4.1.x、JUnit 5
- 模糊测试工具：jqf/quickcheck（或自研随机报文生成器）
- 抓包工具：Wireshark/tcpdump
- CI 要求：Codec 测试集单独任务，失败即阻断发布

### 3.2 分步实现

**步骤目标 1：定义协议与版本字段。**

```java
// 协议头: magic(2) + version(1) + type(1) + length(4)
public record FrameHeader(short magic, byte version, byte type, int length) {}
```

约束：`length` 不得超过配置上限，`version` 必须在兼容列表中。

**步骤目标 2：实现 Encoder/Decoder 分层。**

```java
public class BizMessageEncoder extends MessageToByteEncoder<BizMessage> {
    @Override
    protected void encode(ChannelHandlerContext ctx, BizMessage msg, ByteBuf out) {
        byte[] body = serializer.serialize(msg);
        out.writeShort(0xCAFE);
        out.writeByte(msg.version());
        out.writeByte(msg.type());
        out.writeInt(body.length);
        out.writeBytes(body);
    }
}
```

```java
public class BizFrameDecoder extends ByteToMessageDecoder {
    @Override
    protected void decode(ChannelHandlerContext ctx, ByteBuf in, List<Object> out) {
        if (in.readableBytes() < 8) return;
        in.markReaderIndex();
        short magic = in.readShort();
        byte version = in.readByte();
        byte type = in.readByte();
        int length = in.readInt();
        if (magic != (short) 0xCAFE || length < 0 || length > 1024 * 1024) {
            ctx.close();
            return;
        }
        if (in.readableBytes() < length) {
            in.resetReaderIndex();
            return;
        }
        ByteBuf payload = in.readRetainedSlice(length);
        out.add(new BizFrame(version, type, payload));
    }
}
```

**步骤目标 3：建立一致性测试矩阵。**

```java
@Test
void roundTripShouldKeepSemanticEqual() { ... }
@Test
void shouldRejectIllegalLength() { ... }
@Property
void fuzzInputShouldNotCorruptPipeline(byte[] randomBytes) { ... }
```

**步骤目标 4：故障注入与灰度验收。**

命令示例：
```bash
mvn -q test -Dtest=*Codec*
python scripts/replay_bad_packets.py --target 127.0.0.1:9000
```

可能遇到的坑：

1. `mark/resetReaderIndex` 使用不当，导致半包丢字节。  
2. 异常分支未释放 payload，触发内存泄漏。  
3. 版本兼容策略缺失，滚动发布期间互通失败。

### 3.3 完整代码清单

- `codec/src/main/java/.../BizMessageEncoder.java`
- `codec/src/main/java/.../BizFrameDecoder.java`
- 测试：`codec/src/test/java/.../CodecConsistencyTest.java`
- 回放脚本：`scripts/replay_bad_packets.py`

### 3.4 测试验证

命令示例：
```bash
curl http://127.0.0.1:8080/codec/health
```

命令示例：
```bash
mvn -q test -Dtest=*CodecConsistencyTest
```

验收：

- 一致性测试通过率 100%
- 模糊测试无崩溃、无资源泄漏
- 灰度期间解析错误率低于阈值并可回滚

#### 验证口径

- [ ] **功能**：核心用例可复现，关键输入输出与预期一致。
- [ ] **稳定性**：连续压测或重复执行无异常抖动、无明显长尾退化。
- [ ] **可观测性**：日志、指标与关键错误信号可定位并支持问题回溯。

## 4. 项目总结

### 4.1 优点&缺点

| 维度 | 优点 | 缺点 |
| --- | --- | --- |
| 正确性 | 协议行为可验证，减少线上偶发事故 | 测试体系建设成本较高 |
| 演进性 | 版本兼容有据可依，发布更稳 | 需要维护测试样本与脚本 |
| 运维 | 故障定位更快，可回放复现 | 前期投入时间较多 |

### 4.2 适用场景

适用：

1. 自定义二进制协议服务
2. 高并发长连接网关
3. 需要跨版本滚动发布的系统

不适用：

1. 临时性脚本服务、生命周期很短的项目
2. 协议由外部强约束且不可自定义的场景

### 4.3 注意事项

- 先写协议约束文档，再写 Codec 代码，避免“实现即规范”。
- 每次协议字段变更必须同步新增兼容测试。
- 线上故障包要沉淀为回放样本，进入回归集。

### 4.4 常见踩坑经验

1. **故障案例：半包时偶发解析错位**  
   根因：长度字段读取后未正确回滚 readerIndex。
2. **故障案例：滚动发布互通失败**  
   根因：新老版本 type 枚举冲突且无兼容层。
3. **故障案例：压力下 DirectMemory 上涨**  
   根因：异常路径未释放 `readRetainedSlice` 产生的引用。

### 4.5 思考题

1. 如果协议即将支持可选压缩字段，你会如何设计“向后兼容”的解码流程？
2. 如何把线上抓到的异常报文自动转化为回归测试用例，形成长期收益？

答案见：[附录-思考题答案索引](附录-思考题答案索引.md)
