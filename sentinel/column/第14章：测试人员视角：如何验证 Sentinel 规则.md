# 第14章：测试人员视角：如何验证 Sentinel 规则

## 1 项目背景

第 13 章我们搭建了一个完整的下单链路，配置了 6 类 Sentinel 规则。测试团队接手后第一件事就是验证："这些规则到底生不生效？怎么证明 QPS=50 的限流确实工作了？熔断的恢复时间真的在 10 秒吗？"

传统的手工 curl 测试显然不够。测试团队需要能**重复执行**、**自动验证**的测试方案——包括功能验收（规则是否触发）、性能验收（阈值是否准确）、恢复验收（熔断是否自动恢复）。

而且 Sentinel 的测试有它的特殊性：

- **限流测试**：不能用简单的"单次请求验证"，需要构造并发场景。边界值测试尤其重要——阈值是 50，那么 49、50、51 分别应该得到什么结果？
- **熔断测试**：需要注入异常/延迟来触发，验证熔断打开、半开探测、恢复关闭的完整状态机。
- **热点参数测试**：需要参数化压测，验证不同参数值走的阈值不同。
- **系统保护测试**：需要模拟高负载，对测试环境有要求。

另外，如何把 Sentinel 测试融入 CI/CD 流水线？每次代码变更后自动验收 Sentinel 规则是否仍然生效？这需要自动化测试脚本的支持。

## 2 项目设计

**小胖**（一脸茫然）："测试师姐让我写 Sentinel 的验收用例，我不知道怎么写啊。'验证限流 QPS=50 生效'——这怎么用 JUnit 测？"

**大师**："Sentinel 的测试确实和普通接口测试不一样。它需要并发场景、时间窗口、状态变化的验证。我给你一个框架：**每条规则类型做 3 类测试——功能测试（规则是否触发）、边界测试（阈值前后行为）、恢复测试（熔断/限流后是否自动恢复）**。"

**小白**："边界测试确实重要。我测过一个服务，配的 QPS=100，但实际 95 就被限了。排查发现是统计窗口的边界效应。"

**大师**："对，所以测试时要给 10-20% 的误差容忍度。滑动窗口不是精确计数器，不能像单元测试那样'断言严格等于'。"

**小胖**："那自动化回归怎么做？每次部署都手动压测一次吗？"

**大师**："两种方式：一是用 JMeter + Jenkins 做定时压测，二是用 JUnit + `SphU.entry()` 的纯代码测试。后者更快，适合 CI 流水线快速验证规则是否配置正确。"

**小胖**："大师，熔断的 Half-Open 状态怎么用 JUnit 测试？那是个时间敏感的中间状态。"

**大师**："需要在测试中控制时间。你可以用 `Thread.sleep()` 等待熔断窗口过期，然后发一个探测请求——如果这个请求成功，熔断器会自动关闭；如果失败，继续 OPEN。关键是：探测请求只有一个，多了会干扰判断。建议用单线程 + CountDownLatch 控制。"

**小白**："那限流规则的'边界值'测试——QPS 设 50，并发 50——是不是很难用 JUnit 精确验证？滑动窗口有误差，加上 JVM 的 GC 抖动。"

**大师**："边界值测试的核心不是'精确到个位数'，而是'验证规则在阈值附近的行为是否符合预期'。正确做法是：并发 100 个请求 → 统计通过数和拒绝数 → 断言通过数在 [35, 65] 之间（±30% 容忍度）。不要断言通过数等于 50。Sentinel 不是精确计数器。"

**小胖**："还有个场景：'限流恢复后，被阻塞的请求不会自动放行'——这个测试用例的关键验证点是什么？"

**大师**："关键词是'不会排队等待'。Sentinel 的默认流控模式是'快速失败'——超限后直接拒绝，不排队。测试时：先用高并发触发限流 → 立即停止并发 → 等待 1 秒 → 再发一个单请求 → 应该成功。如果这个单请求等了很久才返回，说明某个地方有排队（可能是 Tomcat 连接池排队，不是 Sentinel 排队）。"

**技术映射**：
- Sentinel 测试的核心验证点：`BlockException` 的触发、规则命中、状态流转、阈值偏差容忍度。
- 推荐的测试工具组合：JUnit（功能验证）+ JMeter/wrk/hey（性能压测）+ Jenkins/GitHub Actions（CI 集成）。
- 滑动窗口测试的注意事项：需要等待至少一个完整统计窗口（默认 1 秒）才能获取有效指标。

## 3 项目实战

### 3.1 环境准备

压测工具对比：

| 工具 | 优势 | 适用场景 |
|------|------|---------|
| JMeter | 图形化界面，丰富插件，CSV 参数化 | 多线程组复杂场景，团队成员友好 |
| wrk | 性能极高，Lua 脚本扩展 | 快速性能基准测试 |
| hey | Go 编写，简单易用 | 快速压测单接口 |
| Gatling | 代码化测试，报告精美 | 专业性能工程团队 |

### 3.2 分步实现

**步骤一：验收用例清单设计**

| 用例编号 | 规则类型 | 测试场景 | 输入 | 期望输出 | 容忍度 |
|---------|---------|---------|------|---------|-------|
| TC-01 | QPS 限流 | 正常流量（QPS < 阈值） | QPS=10（阈值 20） | 全部通过 | 0% |
| TC-02 | QPS 限流 | 超阈值流量 | QPS=30（阈值 20） | ≥30% 被拒绝 | ±15% |
| TC-03 | QPS 限流 | 边界值测试 | QPS=20（精确阈值） | 大部分通过，少量拒绝 | ±20% |
| TC-04 | 线程数限流 | 并发超限 | 10 线程并发（限制 5） | ≥5 个被拒绝 | ±1 |
| TC-05 | 慢调用熔断 | 下游延迟触发熔断 | RT 200ms（阈值 50ms） | 10 秒内触发 Open | ±2 秒 |
| TC-06 | 慢调用熔断 | 熔断后恢复 | 停止延迟注入 | 15 秒内恢复 Closed | ±3 秒 |
| TC-07 | 异常比例熔断 | 异常触发熔断 | 异常率 60%（阈值 30%） | 触发 Open | 时间 ±2 秒 |
| TC-08 | 热点参数 | VIP 用户高阈值 | VIP userId QPS=25 | 通过率 > 80% | ±15% |
| TC-09 | 热点参数 | 普通用户低阈值 | normal userId QPS=10（阈值 5） | 通过率 < 60% | ±15% |
| TC-10 | 系统保护 | CPU 过载保护 | CPU > 80% | 入口请求被拒绝 | 时间 ±2 秒 |
| TC-11 | 授权规则 | 白名单来源 | X-App-Id: admin-app | 允许访问 | 0% |
| TC-12 | 授权规则 | 黑名单来源 | X-App-Id: crawler | 拒绝访问 | 0% |
| TC-13 | 关联流控 | 关联资源流量大 | payCallback QPS > 阈值 | queryOrder 被限制 | ±15% |
| TC-14 | 链路流控 | 不同入口不同阈值 | /goods/detail vs /order/create | 差异化限制 | ±15% |

**步骤二：JUnit 自动化验证（TC-11/12 示例）**

```java
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)
class SentinelRuleVerificationTest {

    @Autowired
    private TestRestTemplate restTemplate;

    @Test
    void testWhiteListAccess() {
        HttpHeaders headers = new HttpHeaders();
        headers.set("X-App-Id", "admin-app");
        HttpEntity<String> entity = new HttpEntity<>("[\"ORD001\"]", headers);

        ResponseEntity<String> response = restTemplate.postForEntity(
            "/order/admin/batch-cancel", entity, String.class);

        assertEquals(200, response.getStatusCodeValue());
        assertTrue(response.getBody().contains("已取消"),
            "白名单来源应该被允许访问");
    }

    @Test
    void testBlackListAccess() {
        HttpHeaders headers = new HttpHeaders();
        headers.set("X-App-Id", "crawler");
        HttpEntity<String> entity = new HttpEntity<>("[\"ORD001\"]", headers);

        ResponseEntity<String> response = restTemplate.postForEntity(
            "/order/admin/batch-cancel", entity, String.class);

        assertTrue(response.getBody().contains("Access Denied"),
            "黑名单来源应该被拒绝访问");
    }

    @Test
    void testFlowRuleBoundary() throws InterruptedException {
        // 边界值测试：QPS=10
        int threshold = 10;
        int totalRequests = 15;
        CountDownLatch latch = new CountDownLatch(totalRequests);
        AtomicInteger passCount = new AtomicInteger(0);
        AtomicInteger blockCount = new AtomicInteger(0);

        ExecutorService executor = Executors.newFixedThreadPool(20);
        for (int i = 0; i < totalRequests; i++) {
            executor.submit(() -> {
                try {
                    ResponseEntity<String> resp = restTemplate.getForEntity(
                        "/order/create?skuId=TEST&userId=NORMAL", String.class);
                    if (resp.getBody().contains("成功") || resp.getBody().contains("订单")) {
                        passCount.incrementAndGet();
                    } else {
                        blockCount.incrementAndGet();
                    }
                } finally {
                    latch.countDown();
                }
            });
        }

        latch.await(5, TimeUnit.SECONDS);
        executor.shutdown();

        System.out.println("通过: " + passCount.get() + ", 拒绝: " + blockCount.get());
        assertTrue(passCount.get() >= threshold * 0.7,
            "通过数应接近或等于阈值，允许滑动窗口误差");
        assertTrue(blockCount.get() > 0,
            "超阈值请求应该被部分拒绝");
    }
}
```

**步骤三：JMeter 压测脚本（TC-08/09 热点参数示例）**

JMeter 测试计划配置：

```xml
<!-- 关键配置项 -->
<ThreadGroup>
    <num_threads>30</num_threads>
    <ramp_time>1</ramp_time>
    <duration>60</duration>
</ThreadGroup>

<CSVDataSet>
    <filename>userIds.csv</filename>
    <!-- userIds.csv 内容：
    VIP_USER_001,200
    NORMAL_001,50
    NORMAL_002,50
    -->
</CSVDataSet>

<HTTPSampler>
    <path>/order/create</path>
    <method>POST</method>
    <body>{"userId":"${userId}","skuId":"SKU_001","quantity":1}</body>
</HTTPSampler>

<ResponseAssertion>
    <!-- 验证 VIP 用户大部分应成功 -->
    <test_field>response_data</test_field>
    <pattern>成功|排队</pattern>
</ResponseAssertion>
```

**步骤四：测试报告模板**

```markdown
## Sentinel 规则验收报告

### 测试环境
- 服务：order-service v2.3.1
- Sentinel：1.8.6
- 测试时间：2024-06-01 14:00-14:30

### 测试结果汇总

| 用例 | 描述 | 状态 | 备注 |
|-----|------|------|------|
| TC-01 | 正常流量通过 | ✅ PASS | 全部通过 |
| TC-02 | 超阈值流控 | ✅ PASS | 拒绝率 55% |
| TC-03 | 边界值 | ⚠️ WARN | 略有过冲（+15%），QPS=23，在容忍范围内 |
| TC-05 | 熔断触发 | ✅ PASS | 9.2 秒后熔断打开 |
| TC-06 | 熔断恢复 | ✅ PASS | 12.5 秒后恢复 Closed |
| TC-08 | 热点 VIP | ✅ PASS | VIP 用户通过率 88% |
| TC-09 | 热点普通 | ✅ PASS | 普通用户通过率 45% |

### 性能指标
- 平均 RT（限流前）: 45ms
- 平均 RT（限流后）: 3ms（BlockException 快速返回）
- P99 RT（限流前）: 120ms
- P99 RT（限流后）: 8ms

### 结论
全部 14 个验收用例通过。阈值边界有 15% 的统计过冲，在容忍范围内。
建议生产环境流控阈值设为压测拐点的 75%（而非 80%）来补偿统计过冲。
```

**踩坑记录**：

1. **熔断恢复测试中"等多久"**：熔断窗口 + 统计窗口 + 探测请求耗时。timeWindow=10s 时，实际恢复可能需要 11-12 秒。测试脚本中的等待时间应设置为 `timeWindow + 3秒`。
2. **并发测试中的资源名共享**：如果用同一个 JVM 运行多个测试类，Sentinel 全局资源名可能冲突。建议每个测试类用独立的资源名，或在 `@Before` 中清理规则。
3. **限流测试中 JUnit 的局限性**：JUnit 的 `@Test` 方法默认在单线程执行，不能真正模拟并发。需要借助 `ExecutorService` + `CountDownLatch` 实现并发。

**步骤五：熔断状态机自动化测试**

```java
@Test
void testDegradeStateMachine() throws InterruptedException {
    // Step 1: 配熔断规则 — 异常比例 > 30%，窗口 5s
    DegradeRule rule = new DegradeRule("testDegradeResource")
            .setGrade(RuleConstant.DEGRADE_GRADE_EXCEPTION_RATIO)
            .setCount(0.3)
            .setMinRequestAmount(5)
            .setStatIntervalMs(10000)
            .setTimeWindow(5);
    DegradeRuleManager.loadRules(Collections.singletonList(rule));

    // Step 2: 注入异常触发熔断
    for (int i = 0; i < 10; i++) {
        try (Entry e = SphU.entry("testDegradeResource")) {
            if (i < 6) throw new RuntimeException("forced error"); // 6/10 = 60%
        } catch (BlockException ignored) {}
    }

    // Step 3: 等待熔断打开
    Thread.sleep(2000);
    assertThrows(DegradeException.class, () -> {
        try (Entry e = SphU.entry("testDegradeResource")) {}
    });

    // Step 4: 等待熔断窗口结束，验证 Half-Open
    Thread.sleep(6000); // timeWindow=5s + margin
    try (Entry e = SphU.entry("testDegradeResource")) {
        // 这个探测请求应该成功
    } catch (BlockException ex) {
        fail("Half-Open 状态应该允许探测请求通过");
    }

    // Step 5: 清理规则
    DegradeRuleManager.loadRules(Collections.emptyList());
}
```

**步骤六：CI/CD 集成 — GitHub Actions 中的 Sentinel 验收**

```yaml
name: Sentinel Rule Validation
on: [push, pull_request]
jobs:
  sentinel-test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Set up JDK 17
        uses: actions/setup-java@v4
        with:
          java-version: '17'
          distribution: 'temurin'
      - name: Run Sentinel tests
        run: mvn test -pl order-service -Dtest="SentinelRuleVerificationTest"
      - name: Generate Sentinel test report
        if: always()
        run: cat target/surefire-reports/*.txt >> $GITHUB_STEP_SUMMARY
```

## 4 项目总结

### 4.1 优点与缺点

| 测试方式 | 优点 | 缺点 |
|---------|------|------|
| JUnit 纯代码测试 | 快速、CI/CD 友好 | 无法模拟真实网络延迟和并发 |
| JMeter 压测 | 真实流量模拟、丰富插件 | 需独立部署测试环境 |
| wrk/hey 快速压测 | 单命令执行、结果清晰 | 无法构建复杂场景 |
| Gatling 代码化压测 | 可版本控制、报告精美 | 学习成本较高 |

### 4.2 适用场景

- CI/CD 流水线：JUnit 验证规则配置存在且格式正确
- 预发布验证：JMeter 完整压测所有 Sentinel 规则
- 性能基准：wrk 快速测试新增接口的极限 QPS
- 回归测试：Gatling 代码化脚本版本控制，每次发布前执行

### 4.3 注意事项

1. 测试环境与生产环境的规则阈值应独立维护（建议比例 1:5 或 1:10）。
2. 熔断恢复时间测试需要等待足够长，建议超时时间设置为理论值的 2 倍。
3. 系统保护测试在 CI 容器中可能无法触发（容器 CPU 受限制），需在专用性能环境中进行。

### 4.4 Sentinel 测试 check-list（上线前必检）

| 类别 | 检查项 | 验证方法 | 通过标准 |
|------|-------|---------|---------|
| 流控 | QPS 阈值生效 | JMeter 并发 > 阈值 | 拦截率 > 50% |
| 流控 | 线程数阈值生效 | 慢接口 + 多线程并发 | 超阈值请求被拒绝 |
| 熔断 | 慢调用触发熔断 | 注入 500ms 延迟 | 10s 内熔断打开 |
| 熔断 | 熔断自动恢复 | 关闭延迟注入 | 15s 内恢复 Closed |
| 熔断 | 异常比例熔断 | 注入 > 50% 异常 | 触发熔断打开 |
| 热点 | 普通参数限流 | CSV 参数化压测 | 默认 QPS 生效 |
| 热点 | VIP 例外项高阈值 | VIP 参数较高 QPS | 通过率 > 80% |
| 系统 | CPU 保护触发 | CPU 负载工具 | 入口请求被 SystemBlock |
| 授权 | 白名单通过 | 带正确 Header | 200 返回 |
| 授权 | 黑名单拒绝 | 带非法 Header | 被 AuthorityException 拦截 |

### 4.5 测试数据与生产数据的隔离原则

| 维度 | 测试环境 | 生产环境 |
|------|---------|---------|
| 规则阈值 | 生产 × 0.1~0.2 | 拐点 × 0.8 |
| 压测标识 | X-Benchmark: true | 无特殊 Header |
| 日志隔离 | 独立日志文件或目录 | 正常日志路径 |
| 熔断窗口 | 3-5s（快速观察恢复） | 10-30s（避免频繁切换） |
| Dashboard | 独立实例 | 独立实例 + 鉴权 |

### 4.4 常见踩坑经验

| 故障现象 | 根因 | 解决方案 |
|---------|------|---------|
| JUnit 测试中限流不由发 | 测试执行太快，同一秒内的请求落在了不同统计窗口 | 在测试中加 `Thread.sleep(1000)` 确保落在同一窗口 |
| JMeter 聚合报告限流率不准 | JMeter 默认不等待响应即发送下一请求 | 增加断言/定时器控制请求节奏 |
| 熔断恢复测试一直不恢复 | Half-Open 的探测请求本身也被熔断 | 确保故障注入已关闭后再等一个时间窗口 |

### 4.5 思考题

1. 如果一条限流规则配置为 QPS=100，你用 JMeter 以 200 并发线程压测，聚合报告显示的 Throughput 是 105（而非 100），这是 Sentinel 的 Bug 还是预期行为？为什么？
2. 自动化测试中，如何校验"系统保护规则只在入口流量生效"这个行为？

### 4.6 推广计划

- **测试团队**：以本章的验收用例清单为模板，为每个服务的 Sentinel 规则构建验收脚本，纳入 CI/CD 流水线。
- **开发团队**：在 Code Review 中检查"新增 Sentinel 规则是否同步新增了对应的验收用例"。
- **运维团队**：利用 JMeter 脚本在每次发布时做一次快速压测（5 分钟），验证规则仍然生效。
