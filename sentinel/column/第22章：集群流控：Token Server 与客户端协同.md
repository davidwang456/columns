# 第22章：集群流控：Token Server 与客户端协同

## 1 项目背景

第 21 章的网关流控把流量的第一道防线前移了，但基础篇埋下的第二个问题依然存在——单机限流在多副本部署下的"阈值不准"。

订单服务部署了 3 个 Pod，每个 Pod 配置了 QPS=100 的流控规则。理论上整体 QPS 上限是 300。但在实际流量分配中，Kubernetes Service 的负载均衡并不均匀——Pod-1 可能承担了 150 QPS，Pod-2 只有 60 QPS，Pod-3 是 90 QPS。Pod-1 的 50 个请求被限流拒绝，但 Pod-2 和 Pod-3 分别还剩 40 和 10 的容量。**从全局看，服务只处理了 200 QPS（100+60+90），但已经有 50 个用户被拒绝了**——实际可用容量比配置少了 16%。

更严重的问题是容量规划。假设业务需要支持全局 500 QPS，你打算部署 5 个副本，每个配 QPS=100。但如果上游负载均衡不均匀，整体可能只能处理 350-400 QPS。运维只能不断加机器来弥补"阈值不准"带来的容量损失。

这就是**集群流控**要解决的核心问题：多实例共享一个全局阈值，由 Token Server 统一分配令牌，每个客户端向 Token Server 申请令牌而非自行判断。

## 2 项目设计

**小胖**（看着监控）："一个 Pod CPU 75%，另外两个才 30%？为什么流量不均匀？"

**大师**："Kubernetes Service 的 iptables 随机负载均衡，长连接下不均匀是常态。这意味着你的单机限流规则配置的是 100，但有些 Pod 被限了、有些还没满——全局阈值实际上是 200-280 而不是 300。"

**小白**："所以需要一个集中式的令牌服务器？所有 Pod 向它申请令牌，它来确保全局 QPS 不超过阈值？"

**大师**："对，这就是 Sentinel 集群流控的 Token Server 模式。架构上分两部分：

- **Token Server**：独立部署（可以是一个独立进程或某个客户端兼任），负责维护全局的令牌计数。
- **Token Client**：嵌入在每个订单服务 Pod 中，每次请求需要向 Token Server 申请令牌，拿到令牌才能放行。"

**小胖**："那如果 Token Server 挂了怎么办？所有 Pod 都不能处理请求了？"

**大师**："这是一个好问题。Sentinel 的集群流控有降级机制：当 Token Client 无法连接到 Token Server 时，可以自动切换回单机限流模式，用本地规则兜底。此外还有 Token Server 的故障转移和选主机制。"

**小白**："全局阈值模式和均摊阈值模式有什么区别？"

**大师**：""全局阈值"是你配 500 QPS，Token Server 就按 500 发令牌，不管有多少个 Client。"均摊阈值"是你配 500 QPS，Token Server 自动除以 Client 数量，每个 Client 分配 500/N——这就有单机限流类似的问题了。生产环境推荐使用全局阈值模式。"

**小胖**："Token Client 每次请求都要向 Token Server 发一次 RPC 申请令牌？那延迟不是很高吗？"

**大师**："默认情况下 Token Client 会预取一批令牌（batch count），而不是每个请求都发 RPC。Client 维护一个本地令牌缓存池——用完了一批再向 Server 申请下一批。预取数量（`acquireCount`）可以配置，默认是 1，建议设为 QPS 的 1/10 到 1/5。比如 QPS=500，预取 50-100 个令牌，网络交互次数降到每秒 5-10 次。"

**小白**："如果 Client 预取了 100 个令牌但实际只用了 30 个，剩下的 70 个令牌怎么办？"

**大师**："令牌有过期机制。Client 本地缓存的令牌有 TTL（默认 1 秒），过期后自动作废但不会退回 Server。所以预取数量不能设太大——太大会导致令牌浪费（某些 Client 多取了令牌，其他 Client 拿不到）。这就是全局阈值模式下令牌分配的'公平性'问题。"

**小胖**："Token Server 的选主是怎么做的？用了 ZooKeeper 还是 etcd？"

**大师**："Sentinel 内置的选主基于存储介质决定——如果用 Nacos 做配置中心，选主就基于 Nacos 的配置创建（类似 create-if-not-exists + lease）；如果用 Apollo，就基于 Apollo 的配置。也可以自定义 `ClusterTokenServerAssigner` 用 Redis 或数据库实现。选主的核心思想是：多个 Server 候选竞争创建同一个唯一的 node/key → 创建成功的成为 Master → Master 定期续约 → 失去 Master 后其他候选接管。"

**小胖**："那如果 Master 和 Client 之间的网络不稳定，Client 反复切换 Token Server，会不会导致限流失效？"

**大师**："这正是最危险的场景。Client 切换 Token Server 时，本地令牌缓存可能已经耗尽或过期。在没有令牌的情况下，`fallbackToLocalWhenFail=true` 会让 Client 切回单机限流模式——但单机阈值是多少？如果之前集群阈值是 500，3 个 Client 切回单机后每个 Client 配多少？`fallbackLocalThreshold` 需要预先设定，通常设为集群阈值 / Client 数量。"

## 3 项目实战

### 3.1 环境准备

集群流控需要额外的依赖：

```xml
<dependency>
    <groupId>com.alibaba.csp</groupId>
    <artifactId>sentinel-cluster-client-default</artifactId>
    <version>1.8.6</version>
</dependency>
<dependency>
    <groupId>com.alibaba.csp</groupId>
    <artifactId>sentinel-cluster-server-default</artifactId>
    <version>1.8.6</version>
</dependency>
```

### 3.2 分步实现

**步骤一：启动 Token Server**

方式一——嵌入模式（某个客户端兼任）：

```java
@Component
public class ClusterTokenServerInit {

    @PostConstruct
    public void init() throws Exception {
        // 初始化集群规则
        ClusterFlowRuleManager.loadRules(/* 从 Nacos 或其他数据源 */);

        // 启动 Token Server（嵌入模式）
        ClusterTokenServer tokenServer =
            new SentinelDefaultTokenServer();
        tokenServer.start();  // 默认监听在 18730 端口
    }
}
```

方式二——独立部署（推荐生产环境）：

```bash
java -Dcsp.sentinel.cluster.server.port=18730 \
     -jar sentinel-cluster-server.jar
```

**步骤二：配置集群流控规则**

```java
@Component
public class ClusterFlowConfig {

    @PostConstruct
    public void init() {
        List<FlowRule> rules = new ArrayList<>();

        FlowRule rule = new FlowRule("createOrder")
                .setGrade(RuleConstant.FLOW_GRADE_QPS)
                .setCount(300)             // 全局 QPS 阈值
                .setClusterMode(true)      // 开启集群模式
                .setClusterConfig(
                    new ClusterFlowConfig()
                        .setFlowId(1L)     // 全局唯一 ID
                        .setThresholdType(ClusterRuleConstant.FLOW_THRESHOLD_GLOBAL) // 全局阈值
                        .setFallbackToLocalWhenFail(true)  // Token Server 不可用时降级为单机
                        .setSampleCount(10)
                        .setWindowIntervalMs(1000)
                );

        rules.add(rule);
        FlowRuleManager.loadRules(rules);
    }
}
```

**步骤三：配置 Token Client**

```java
@Component
public class ClusterTokenClientInit {

    @PostConstruct
    public void init() {
        // 配置 Token Server 地址
        ClusterClientConfigManager.applyNewConfig(
            new ClusterClientConfig()
                .setRequestTimeout(1000)     // 向 Token Server 请求令牌的超时时间
        );

        // 指定 Token Server 地址（可以多个实现故障转移）
        ClusterClientConfigManager.loadServerConfig(
            new ClusterClientAssignConfig()
                .setServerHost("192.168.1.100")
                .setServerPort(18730)
        );

        // 初始化 Token Client
        ClusterTokenClient tokenClient = new SentinelDefaultTokenClient();
        tokenClient.start();
    }
}
```

**步骤四：验证集群流控**

1. 启动 3 个订单服务实例（Pod-1/2/3）+ 1 个 Token Server
2. 在 Nacos 中配置全局流控 QPS=300
3. 用 JMeter 对 3 个实例各压测 150 QPS（总计 450 QPS）
4. 观察结果：
   - 3 个实例的整体通过量约 300 QPS（不管流量怎么分布）
   - 拒绝量约 150 QPS
   - 在 Dashboard 可以看到 Token Server 的实时令牌发放情况

**步骤五：Token Server 选主与故障转移**

```yaml
# 指定多个 Token Server 候选
sentinel:
  cluster:
    server:
      - host: 192.168.1.100
        port: 18730
      - host: 192.168.1.101
        port: 18730
      - host: 192.168.1.102
        port: 18730
    client:
      request-timeout: 1000
      fallback-to-local: true  # 所有 Server 不可用时降级为单机
```

选主流程：所有候选 Server 竞争创建相同路径 → 创建成功的成为 Master → Master 定期续约 → Master 宕机后其他候选自动竞选。

### 3.6 Token Server 性能压测

```java
@Test
public void benchmarkTokenServer() throws Exception {
    // Token Server 单机性能测试
    int concurrency = 100;
    int totalRequests = 1_000_000;
    AtomicLong passed = new AtomicLong(0);
    AtomicLong blocked = new AtomicLong(0);

    // 启动 Token Server（嵌入模式）
    SentinelDefaultTokenServer server = new SentinelDefaultTokenServer();
    server.start();

    // 配置全局 QPS=50000
    ClusterFlowRuleManager.loadRules(createClusterRule("benchmark", 50000));

    ExecutorService pool = Executors.newFixedThreadPool(concurrency);
    long start = System.currentTimeMillis();

    List<Future<?>> futures = new ArrayList<>();
    for (int i = 0; i < concurrency; i++) {
        futures.add(pool.submit(() -> {
            ClusterTokenClient client = createClient();
            for (int j = 0; j < totalRequests / concurrency; j++) {
                if (client.requestToken("benchmark", 1, false)) {
                    passed.incrementAndGet();
                } else {
                    blocked.incrementAndGet();
                }
            }
        }));
    }

    for (Future<?> f : futures) f.get();
    long elapsed = System.currentTimeMillis() - start;

    System.out.printf("Total: %d, Passed: %d, Blocked: %d%n",
        totalRequests, passed.get(), blocked.get());
    System.out.printf("Throughput: %.0f req/s, Elapsed: %d ms%n",
        1000.0 * totalRequests / elapsed, elapsed);

    server.stop();
}
```

### 3.7 令牌预取大小调优测试

```java
@Test
public void testPreFetchBatchSize() {
    // 不同预取大小对令牌分配公平性的影响
    int[] batchSizes = {1, 10, 50, 200};
    int globalThreshold = 500;

    for (int batch : batchSizes) {
        int[] clientRequests = {300, 150, 50};
        int[] clientTokens = simulateTokenAllocation(
            globalThreshold, clientRequests, batch);

        System.out.printf("Batch=%d: Client tokens = [%d, %d, %d]%n",
            batch, clientTokens[0], clientTokens[1], clientTokens[2]);
    }
    // 结论：batch=1 最公平但 RPC 次数最多
    //       推荐 batch = QPS / 10 ~ QPS / 5
}
```

### 3.8 Client 降级行为验证

```java
@Test
public void testClientFallbackBehavior() throws Exception {
    // 场景：Token Server 宕机 → Client 降级为单机限流
    // 配置 fallbackToLocalWhenFail=true

    // 1. 正常模式：Token Server 在线
    assertTrue(client.requestToken("testResource", 1, false));

    // 2. 关闭 Token Server → Client 降级
    shutdownTokenServer();
    Thread.sleep(5000);

    int fallbackPassed = 0;
    for (int i = 0; i < 200; i++) {
        if (client.requestToken("testResource", 1, false)) {
            fallbackPassed++;
        }
    }
    System.out.printf("降级模式: 通过=%d%n", fallbackPassed);
}
```

**踩坑记录**：

1. **Token Server 自身性能瓶颈**：单 Token Server 约能支撑 5-10 万 QPS。如果全局阈值更高，需要 Token Server 集群或者改用均摊模式（每个 Server 管一部分）。
2. **网络延迟影响**：Token Client 每次请求要跨网络申请令牌，增加 1-3ms 延迟。对于延迟敏感的场景，建议把 Token Server 部署在和 Client 同一机房/同一 K8s 集群。
3. **令牌预取**：Client 可以一次向 Server 申请多个令牌（`acquireCount`），减少网络交互次数。但预取过多会导致令牌分配不均。

## 4 项目总结

### 4.1 优点与缺点

| 方案 | 优点 | 缺点 | 适用场景 |
|------|------|------|---------|
| 集群全局阈值 | 整体精确限流 | 需部署 Token Server，增加网络延迟 | 生产环境，多副本 |
| 集群均摊阈值 | 无需集中式 Server | 与单机限流类似，负载不均时浪费容量 | 临时过渡方案 |
| 单机限流 | 简单、零依赖 | 多副本阈值不准 | 单机部署、开发环境 |

### 4.2 集群流控关键配置决策表

| 配置项 | 推荐值 | 影响 | 备注 |
|-------|-------|------|------|
| thresholdType | GLOBAL | 全局精确限流 | 生产必选 GLOBAL |
| fallbackToLocalWhenFail | true | Server 宕机时 Client 能继续工作 | 必须配置 fallbackLocalThreshold |
| acquireCount (令牌预取) | QPS / 10 ~ QPS / 5 | 影响公平性和 RPC 频率 | 500 QPS → 预取 50-100 |
| requestTimeout | 1000ms | 令牌请求超时 | 内网建议 500ms，跨机房 2000ms |
| sampleCount | 10 | 统计精度 | 集群流控默认 10 |
| fallbackLocalThreshold | 集群阈值 / Client数 | 降级后的单机阈值 | 配合 K8s HPA 自动更新 |

### 4.3 故障场景与恢复时间表

| 故障场景 | 影响 | 恢复方式 | 恢复时间 |
|---------|------|---------|---------|
| Token Server 宕机 | Client 降级为单机限流 | fallbackToLocalWhenFail=true | 5-10 秒（检测 + 切换） |
| Token Server Master 切换 | Client 短暂无 Token | 新 Master 接管 | 15-30 秒（选主 + 客户端重连） |
| Client-Server 网络闪断 | 令牌请求超时 | 重试 + 本地缓存兜底 | 1-3 秒 |
| 全部 Token Server 不可用 | 所有 Client 降级单机 | fallbackToLocalWhenFail=true | 5-10 秒 |
| Client 启动时 Server 不可用 | Client 使用本地规则文件兜底 | 本地文件 DataSource | 立即 |

### 4.4 注意事项

1. Token Server 的 failover 策略必须配好，否则 Server 宕机会导致所有 Client 无法获取令牌（或降级为单机）。
2. 集群流控不支持预热和排队等待效果，只支持 QPS 直接拒绝。
3. 令牌预取数量应根据实际 QPS 合理设置，太大导致不均，太小导致频繁 RPC。

### 4.5 思考题

1. 如果 Token Server 配置了全局 QPS=300，3 个 Client 中有一个 Client 因为 Bug 发了 500 QPS 的请求，其他 2 个 Client 正常发 100 QPS。最终令牌分配会怎样？正常 Client 会受影响吗？
2. Global 阈值模式和均摊阈值模式在"Client 动态增减"场景下的行为有什么不同？

### 4.6 推广计划

- **开发团队**：在集群多副本部署的服务中启用集群流控，替代单机限流。
- **运维团队**：负责 Token Server 的部署、选主监控和故障转移演练。
- **测试团队**：验证 Token Server 宕机 → Client 降级 → Token Server 恢复 → Client 切回的完整流程。

### 4.7 Token Server 容量规划与高可用

**Token Server 容量速算**：
- 单 Token Server（4C8G）可支撑 **5-10 万 QPS** 的令牌发放
- 每个令牌请求约占用 0.5-1KB 网络带宽（含序列化开销）
- 推荐 Client 每次预取 10-20 个令牌，减少 RPC 频率

**高可用部署拓扑**：

| 部署方式 | 故障恢复时间 | 适用规模 |
|---------|------------|---------|
| 单 Server + Client 本地降级 | 0ms（自动降级） | < 20 个 Client |
| 嵌入式 Server（Client 兼任） | 0ms（选主切换） | 20-50 个 Client |
| 独立 Server 集群（3 节点） | < 3s（选主切换） | > 50 个 Client |

**Token Server 监控指标**：

```yaml
# Prometheus 监控项
- token_server_qps: 令牌发放速率
- token_server_latency_p99: 令牌请求 P99 延迟
- token_server_active_clients: 活跃 Client 数
- token_server_leader_election: 选主状态（0=备, 1=主）
```

**Token Server 故障演练清单**：
- [ ] 主 Server 宕机 → 备 Server 自动选主（< 3 秒）
- [ ] 所有 Server 宕机 → Client 降级为单机限流（< 1 秒）
- [ ] Server 恢复 → Client 自动切回集群模式（< 10 秒）
- [ ] 网络分区 → Client 检测超时后降级，不丢流量
