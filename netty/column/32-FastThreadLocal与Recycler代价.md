# 32-FastThreadLocal与Recycler代价

## 1. 项目背景

某支付核心服务升级后，吞吐提升了 12%，但内存占用也上升了 25%，且在流量下降后长期不回落。排查时大家第一反应是“JVM 没调好”，结果最终定位到 Netty 的 `FastThreadLocal` 与 `Recycler` 使用方式：对象复用提高了性能，却把短峰值压力转成了较长时间的驻留内存。这个现象在高并发、线程池规模较大、对象生命周期极短的场景里非常典型。

`FastThreadLocal` 通过索引数组替代标准 `ThreadLocal` 的哈希寻址，降低访问开销；`Recycler` 则通过局部栈与跨线程回收队列复用对象实例，减少 GC。两者是 Netty 性能体系的重要基石，但“更快”不等于“免费”。当线程数量膨胀、对象池缓存上限过高、跨线程回收比例增加时，性能收益会被内存与复杂度成本吞噬。

本章目标是讲透这套机制的收益边界：源码上看访问路径与回收路径，工程上看何时该用、何时该关、如何配指标防止“优化变事故”。

## 2. 项目设计（剧本式交锋对话）

| 角色 | 定位 |
| --- | --- |
| 小胖 | 业务视角提出诉求与约束 |
| 小白 | 工程实现与落地执行 |
| 大师 | 架构把关与取舍决策 |

### 第一轮

小胖：都叫“复用”，为什么还会更耗内存？  
小白：是不是缓存越大命中越高就越好？  
大师：命中率高只是局部指标。缓存本质是“用空间换时间”，如果流量回落后缓存不收敛，系统会背着历史包袱长期运行。

技术映射：你为了快递时效囤了很多周转箱，淡季时仓库还是被占着。

### 第二轮

小胖：`FastThreadLocal` 比 `ThreadLocal` 快多少，值不值得全量改？  
小白：如果业务线程不是 Netty 线程，会不会收益变小？  
大师：收益与访问频率和线程模型强相关。I/O 热路径高频访问值得用；低频管理代码收益有限。并且脱离 `FastThreadLocalThread` 场景后，优势会被削弱。

技术映射：高速路只有在车流密集且路线匹配时才明显提速。

### 第三轮

小胖：`Recycler` 我能拿来复用所有对象吗？  
小白：复杂对象复用会不会带来脏数据风险？  
大师：对象复用要满足“可重置、低耦合、生命周期短”。状态复杂、跨层共享、线程安全要求高的对象，盲目复用反而更危险。

技术映射：可重复餐盒适合标准餐，不适合每次都要特殊消毒的器皿。

## 3. 项目实战

### 3.1 环境准备

- JDK 17、Netty 4.1.x  
- JVM 参数建议：`-Xms2g -Xmx2g -XX:MaxDirectMemorySize=1g`  
- 关键开关：
  - `-Dio.netty.recycler.maxCapacityPerThread=4096`
  - `-Dio.netty.recycler.ratio=8`
  - `-Dio.netty.recycler.linkCapacity=16`
- 指标：对象创建速率、老年代占用、线程数、P99 延迟

### 3.2 分步实现

**步骤目标 1：实现可复用对象并接入 Recycler。**

```java
final class RecyclableCommand {
    private static final Recycler<RecyclableCommand> RECYCLER =
        new Recycler<>() {
            @Override
            protected RecyclableCommand newObject(Handle<RecyclableCommand> handle) {
                return new RecyclableCommand(handle);
            }
        };

    private final Recycler.Handle<RecyclableCommand> handle;
    private String traceId;
    private byte[] payload;

    private RecyclableCommand(Recycler.Handle<RecyclableCommand> handle) {
        this.handle = handle;
    }

    static RecyclableCommand acquire() { return RECYCLER.get(); }

    void recycle() {
        traceId = null;
        payload = null;
        handle.recycle(this);
    }
}
```

**步骤目标 2：对比“新建对象”与“复用对象”延迟。**

命令示例：
```bash
mvn -q -DskipTests package
java -jar target/app.jar --mode=new
java -jar target/app.jar --mode=recycle
```

记录：吞吐、Young GC、平均分配速率。

**步骤目标 3：演示 FastThreadLocal 热路径缓存。**

```java
private static final FastThreadLocal<DateTimeFormatter> FMT = new FastThreadLocal<>() {
    @Override
    protected DateTimeFormatter initialValue() {
        return DateTimeFormatter.ofPattern("yyyyMMddHHmmss");
    }
};
```

在日志编解码热路径调用 `FMT.get()`，对比标准 `ThreadLocal`。

**步骤目标 4：验证流量回落后的内存收敛。**

- 先跑 10 分钟高峰压测，再降到 10% 流量。
- 观察 30 分钟内老年代和 RSS 是否回落到可接受区间。

可能遇到的坑：

1. `recycle()` 前未重置字段，造成脏数据串请求。  
2. 业务线程池过大，导致每线程缓存总量过高。  
3. 过度追求复用，代码可读性与可维护性明显下降。

### 3.3 完整代码清单

- `common/src/main/java/io/netty/util/concurrent/FastThreadLocal.java`
- `common/src/main/java/io/netty/util/Recycler.java`
- 样例：`example/recycler/RecyclerBenchmark.java`
- 观测脚本：`bench/recycler_gc_compare.sh`

### 3.4 测试验证

命令示例：
```bash
curl -X POST http://127.0.0.1:8080/command -d '{"id":"1"}'
```

压测：

命令示例：
```bash
wrk -t8 -c256 -d120s http://127.0.0.1:8080/command
```

验收：

- 复用模式下 GC 次数下降
- P99 不劣化
- 流量回落后内存可收敛

#### 验证口径

- [ ] **功能**：核心用例可复现，关键输入输出与预期一致。
- [ ] **稳定性**：连续压测或重复执行无异常抖动、无明显长尾退化。
- [ ] **可观测性**：日志、指标与关键错误信号可定位并支持问题回溯。

## 4. 项目总结

### 4.1 优点&缺点

| 维度 | 优点 | 缺点 |
| --- | --- | --- |
| FastThreadLocal | 访问开销低，适合热路径 | 与线程模型耦合更强 |
| Recycler | 降低对象创建与 GC 压力 | 驻留内存增加，状态重置易出错 |
| 综合 | 在高并发场景收益明显 | 参数和边界复杂，调试难度上升 |

### 4.2 适用场景

适用：

1. 高频对象创建的协议编解码链路
2. 生命周期短且易重置的对象
3. 对长尾延迟敏感的服务

不适用：

1. 低并发后台任务
2. 状态复杂且跨线程共享严重的对象

### 4.3 注意事项

- “先可观测再优化”：没有对象创建与内存曲线，不要盲目开复用。
- 为每个可回收对象写重置单测，防止脏状态泄漏。
- 线程池规模与缓存上限联动评估，避免总量失控。

### 4.4 常见踩坑经验

1. **故障案例：请求串号**  
   根因：复用对象字段未清空，traceId 污染下一请求。
2. **故障案例：内存长期高位**  
   根因：线程池扩容后每线程缓存累积，流量回落不回收。
3. **故障案例：性能无提升反降**  
   根因：对象本身很轻，复用引入额外同步与复杂分支。

### 4.5 思考题

1. 你会如何设计一个“自动降级关闭 Recycler”的运行时保护开关？
2. 当 `FastThreadLocal` 与业务线程池混用时，如何评估真实收益而不是微基准幻觉？

答案见：[附录-思考题答案索引](附录-思考题答案索引.md)
