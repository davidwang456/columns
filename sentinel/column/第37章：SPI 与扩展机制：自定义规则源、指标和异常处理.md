# 第37章：SPI 与扩展机制：自定义规则源、指标和异常处理

## 1 项目背景

第 33 章我们通过 Slot 扩展了解了 Sentinel 的 SPI 机制，但那只是冰山一角。Sentinel 的 SPI 机制覆盖了从数据源、指标统计到异常处理的方方面面。

某金融公司有这样一个需求：所有 Sentinel 规则需要存储在内部的关系型数据库（MySQL）中，由 DBA 统一管理变更，而非 Nacos。同时，所有限流拦截的响应需要包含特定的错误码（如 `BIZ_429_FLOW_LIMIT`），以满足前端错误处理规范。

这些需求的本质是：**使用 Sentinel 的扩展点，在不修改源码的前提下，定制规则来源、指标上报和异常输出**。

本章将覆盖三个核心扩展点：自定义 DataSource、自定义 BlockExceptionHandler、自定义指标 Exporter。

## 2 项目设计

**小白**："Sentinel 的 SPI 机制和 Java 标准的 SPI 一样吗？"

**大师**："类似但更灵活。Sentinel 的 SPI 支持别名（alias）、优先级排序（@SpiOrder）、懒加载，而且兼容 Java 标准 SPI（`META-INF/services/`）。"

**小胖**："自定义 DataSource 是不是实现 `ReadableDataSource` 接口就行？"

**大师**："对。实现 `readSource()` 方法从你的数据源读取原始数据，然后在构造函数或初始化方法中调用 `loadConfig()` 把解析后的规则写入 RuleManager。"

**小白**："那如果有多个同类型的 SPI 实现同时存在——比如我自定义了一个 JdbcDataSource 和系统默认的 NacosDataSource——Sentinel 会选哪个？"

**大师**："取决于 `@SpiOrder` 注解的值。`@SpiOrder(-100)` 的实现会优先于 `@SpiOrder(0)`。如果你想让自定义 DataSource 覆盖默认的，就给自定义的实现设定更小的 order 值。但更常见的做法不是覆盖而是共存——不同 DataSource 负责不同类型的规则：JdbcDataSource 负责流控规则，NacosDataSource 负责熔断规则。"

**小胖**："自定义 Slot 链怎么调整顺序？比如我写了一个 `CustomAuditSlot`，想插在 `FlowSlot` 之后、`DegradeSlot` 之前。"

**大师**："通过 SPI 配置文件 + `@SpiOrder` 控制。在 `META-INF/services/com.alibaba.csp.sentinel.slotchain.ProcessorSlot` 中列出你的 Slot，设置 order 值在 FlowSlot（order=-2000）和 DegradeSlot（order=-1000）之间，比如 order=-1500。Sentinel 启动时会按 order 升序排列所有 Slot。"

**小胖**："那自定义 MetricExporter 呢？比如我想把每次 PASS/BLOCK 的计数器导出到公司内部的监控系统。"

**大师**："实现 `MetricExporter` 接口并注册。Sentinel 的 `SentinelEventBus` 在每次 PASS/BLOCK 时会通知所有注册的 Subscriber。你还可以实现 `InitFunc` 接口（SPI 加载），在 `init()` 方法中注册你的 Exporter——这样 Sentinel 启动时自动加载，无需代码侵入。"

**小白**："SPI 的性能开销怎么样？比如每个请求都要经过自定义的 Slot，会不会有明显的性能损耗？"

**大师**："这取决于你的实现。Sentinel 内置的 Slot 链经过高度优化，每个 Slot 耗时在微秒级。自定义 Slot 如果要频繁访问数据库或做复杂计算，建议加异步缓冲（RingBuffer）或缓存，避免阻塞 Slot 链的主流程。可以监控 `SphU.entry()` 的耗时分布来判断自定义 Slot 的性能影响。"

## 3 项目实战

### 3.1 自定义数据库规则数据源

```java
public class JdbcDataSource implements ReadableDataSource<String, List<FlowRule>> {

    private final DataSource dataSource;
    private final String serviceName;
    private final Property<List<FlowRule>> property;

    public JdbcDataSource(DataSource dataSource, String serviceName) {
        this.dataSource = dataSource;
        this.serviceName = serviceName;
        this.property = new DynamicSentinelProperty<>();
        // 注册规则变更监听
        FlowRuleManager.register2Property(property);

        // 首次加载
        loadAndUpdateRules();

        // 定时拉取（每 30 秒）
        Executors.newSingleThreadScheduledExecutor()
            .scheduleAtFixedRate(this::loadAndUpdateRules, 30, 30, TimeUnit.SECONDS);
    }

    private void loadAndUpdateRules() {
        try {
            String config = readSource();
            List<FlowRule> rules = JSON.parseArray(config, FlowRule.class);
            property.updateValue(rules);
        } catch (Exception e) {
            log.error("从数据库加载 Sentinel 规则失败", e);
        }
    }

    @Override
    public String readSource() throws Exception {
        // 从数据库读取规则
        try (Connection conn = dataSource.getConnection();
             PreparedStatement ps = conn.prepareStatement(
                 "SELECT rule_content FROM sentinel_rules WHERE service_name = ? AND rule_type = 'flow'")) {
            ps.setString(1, serviceName);
            ResultSet rs = ps.executeQuery();
            if (rs.next()) {
                return rs.getString("rule_content");
            }
        }
        return "[]";  // 默认空规则
    }

    @Override
    public Property<List<FlowRule>> getProperty() {
        return property;
    }

    @Override
    public void close() throws Exception {
        // 清理资源
    }
}
```

### 3.2 自定义统一 JSON 异常处理器

```java
@Component
public class UnifiedBlockExceptionHandler implements BlockExceptionHandler {

    @Override
    public void handle(HttpServletRequest request, HttpServletResponse response,
                       BlockException e) throws Exception {
        response.setContentType("application/json;charset=UTF-8");

        SentinelError error = new SentinelError();
        error.setTimestamp(System.currentTimeMillis());
        error.setPath(request.getRequestURI());
        error.setResource(e.getRule().getResource());

        if (e instanceof FlowException) {
            response.setStatus(429);
            error.setCode("SENTINEL_FLOW_LIMIT");
            error.setMsg("请求频率超限");
            error.setSuggestion("请降低请求频率或联系管理员提升配额");
        } else if (e instanceof DegradeException) {
            response.setStatus(503);
            error.setCode("SENTINEL_DEGRADE");
            error.setMsg("服务暂时不可用，已自动熔断");
            error.setSuggestion("请稍后重试，系统将自动恢复");
        } else if (e instanceof ParamFlowException) {
            response.setStatus(429);
            error.setCode("SENTINEL_HOT_PARAM");
            error.setMsg("该资源访问过热");
        } else if (e instanceof SystemBlockException) {
            response.setStatus(503);
            error.setCode("SENTINEL_SYSTEM_BLOCK");
            error.setMsg("系统过载保护已启动");
            error.setSuggestion("请稍后重试");
        } else if (e instanceof AuthorityException) {
            response.setStatus(403);
            error.setCode("SENTINEL_FORBIDDEN");
            error.setMsg("无权访问该资源");
        } else {
            response.setStatus(429);
            error.setCode("SENTINEL_BLOCK");
            error.setMsg("请求被拦截");
        }

        response.getWriter().write(JSON.toJSONString(error));
    }

    @Data
    static class SentinelError {
        private String code;
        private String msg;
        private String suggestion;
        private String resource;
        private String path;
        private Long timestamp;
    }
}
```

### 3.3 自定义 URL 清洗器

多参数 URL 导致 Dashboard 资源过多：

```java
@Component
public class CustomUrlCleaner implements UrlCleaner {

    @Override
    public String clean(String originUrl) {
        // /order/query?orderId=12345&userId=888 → /order/query
        // /goods/detail/SKU_001 → /goods/detail/{skuId}
        if (originUrl.startsWith("/order/query")) {
            return "/order/query";
        }
        if (originUrl.startsWith("/goods/detail/")) {
            return "/goods/detail/{skuId}";
        }
        return originUrl;
    }
}
```

### 3.4 自定义 RequestOriginParser

```java
@Component
public class JwtOriginParser implements RequestOriginParser {

    @Override
    public String parseOrigin(HttpServletRequest request) {
        // 从 JWT Token 中提取 appId
        String authHeader = request.getHeader("Authorization");
        if (authHeader != null && authHeader.startsWith("Bearer ")) {
            try {
                String token = authHeader.substring(7);
                Jws<Claims> claims = Jwts.parser()
                    .parseSignedClaims(token);  // 需引入 jjwt 库
                return claims.getPayload().get("appId", String.class);
            } catch (Exception e) {
                log.warn("JWT 解析失败", e);
            }
        }
        return "unknown";
    }
}
```

### 3.5 集成测试

```java
@SpringBootTest
@AutoConfigureMockMvc
class CustomExtTest {

    @Test
    void testCustomBlockHandler() throws Exception {
        // 打满流控规则
        for (int i = 0; i < 50; i++) {
            mockMvc.perform(get("/order/create").param("skuId", "SKU_001"));
        }
        MvcResult result = mockMvc.perform(get("/order/create")
                .param("skuId", "SKU_001"))
                .andExpect(status().is(429))
                .andExpect(jsonPath("$.code").value("SENTINEL_FLOW_LIMIT"))
                .andExpect(jsonPath("$.suggestion").exists())
                .andReturn();
    }
}
```

### 3.6 自定义 Slot —— 请求审计日志 Slot

```java
@SpiOrder(-1500) // 在 FlowSlot(-2000) 之后, DegradeSlot(-1000) 之前
public class AuditSlot extends AbstractLinkedProcessorSlot<DefaultNode> {

    private static final Logger auditLog = LoggerFactory.getLogger("sentinel-audit");

    @Override
    public void entry(Context context, ResourceWrapper resourceWrapper,
                      DefaultNode node, int count, boolean prioritized,
                      Object... args) throws Throwable {
        String resourceName = resourceWrapper.getName();
        String origin = context.getOrigin();
        long startTime = System.currentTimeMillis();

        try {
            fireEntry(context, resourceWrapper, node, count, prioritized, args);
        } catch (BlockException e) {
            // 记录被拦截的请求
            auditLog.info("BLOCK|resource={}|origin={}|rule={}|exception={}",
                resourceName, origin, e.getRule(), e.getClass().getSimpleName());
            throw e;
        } finally {
            long rt = System.currentTimeMillis() - startTime;
            auditLog.info("PASS|resource={}|origin={}|rt={}ms", resourceName, origin, rt);
        }
    }

    @Override
    public void exit(Context context, ResourceWrapper resourceWrapper,
                     int count, Object... args) {
        fireExit(context, resourceWrapper, count, args);
    }
}
```

SPI 注册文件 `META-INF/services/com.alibaba.csp.sentinel.slotchain.ProcessorSlot`:
```
com.example.sentinel.ext.AuditSlot
```

### 3.7 自定义 MetricExporter —— 指标推送到 InfluxDB

```java
@SpiOrder(0)
public class InfluxDbMetricExporter implements MetricExporter, InitFunc {

    private InfluxDB influxDB;
    private final BlockingQueue<MetricPoint> buffer = new LinkedBlockingQueue<>(10000);

    @Override
    public void init() throws Exception {
        influxDB = InfluxDBFactory.connect("http://influxdb:8086", "admin", "password");
        influxDB.setDatabase("sentinel_metrics");

        // 后台线程批量写入
        new Thread(() -> {
            List<MetricPoint> batch = new ArrayList<>(100);
            while (true) {
                try {
                    buffer.drainTo(batch, 100);
                    if (!batch.isEmpty()) {
                        writeBatch(batch);
                        batch.clear();
                    }
                    Thread.sleep(1000);
                } catch (Exception e) {
                    log.error("InfluxDB 写入失败", e);
                }
            }
        }, "sentinel-influx-exporter").start();
    }

    @Override
    public void export(String resource, long passQps, long blockQps,
                       long successQps, long exceptionQps, long avgRt,
                       Map<Long, MetricBucket> metrics) {
        Point point = Point.measurement("sentinel_resource")
            .time(System.currentTimeMillis(), TimeUnit.MILLISECONDS)
            .tag("resource", resource)
            .addField("passQps", passQps)
            .addField("blockQps", blockQps)
            .addField("successQps", successQps)
            .addField("exceptionQps", exceptionQps)
            .addField("avgRt", avgRt)
            .build();
        buffer.offer(new MetricPoint(point));
    }

    private void writeBatch(List<MetricPoint> batch) {
        influxDB.write(batch.stream().map(mp -> mp.point).collect(Collectors.toList()));
    }

    static class MetricPoint {
        final Point point;
        MetricPoint(Point p) { this.point = p; }
    }
}
```

### 3.8 自定义规则源 —— Redis DataSource

```java
public class RedisDataSource implements ReadableDataSource<String, List<DegradeRule>> {

    private final JedisPool jedisPool;
    private final String redisKey;
    private final Property<List<DegradeRule>> property;

    public RedisDataSource(JedisPool jedisPool, String redisKey) {
        this.jedisPool = jedisPool;
        this.redisKey = redisKey;
        this.property = new DynamicSentinelProperty<>();
        DegradeRuleManager.register2Property(property);

        loadAndUpdate();
        Executors.newSingleThreadScheduledExecutor()
            .scheduleAtFixedRate(this::loadAndUpdate, 10, 10, TimeUnit.SECONDS);
    }

    private void loadAndUpdate() {
        try (Jedis jedis = jedisPool.getResource()) {
            String content = jedis.get(redisKey);
            if (content != null && !content.isEmpty()) {
                List<DegradeRule> rules = JSON.parseArray(content, DegradeRule.class);
                property.updateValue(rules);
            }
        } catch (Exception e) {
            log.error("从 Redis 加载熔断规则失败", e);
        }
    }

    @Override
    public String readSource() throws Exception {
        try (Jedis jedis = jedisPool.getResource()) {
            return jedis.get(redisKey);
        }
    }

    @Override
    public Property<List<DegradeRule>> getProperty() { return property; }

    @Override
    public void close() throws Exception { jedisPool.close(); }
}
```

### 3.9 Sentinel SPI 加载机制深入

```java
// Sentinel SPI 加载原理（简化版）
public class SpiLoader {
    public static <T> T loadFirstInstance(Class<T> clazz) {
        // 1. 从 META-INF/services/<fully-qualified-class-name> 读取所有实现类
        ServiceLoader<T> loader = ServiceLoader.load(clazz);

        // 2. 按 @SpiOrder 排序（值越小优先级越高）
        List<T> instances = new ArrayList<>();
        for (T instance : loader) {
            instances.add(instance);
        }
        instances.sort(Comparator.comparingInt(
            i -> i.getClass().isAnnotationPresent(SpiOrder.class)
                ? i.getClass().getAnnotation(SpiOrder.class).value()
                : 0));

        // 3. 返回排序后的第一个实例
        return instances.isEmpty() ? null : instances.get(0);
    }
}
```

**踩坑记录**：

1. **自定义 DataSource 的线程安全**：如果多个线程同时读取/更新规则，确保 `readSource()` 和 `updateValue()` 的并发安全。
2. **SPI 优先级冲突**：如果有多个同类型的 SPI 实现，Sentinel 会选择 `@SpiOrder` 值最小的。确保自定义实现的 order 不与内置冲突。
3. **Dashboard 扩展 vs 客户端扩展**：自定义 BlockExceptionHandler 只在客户端生效，Dashboard 看不到。如果需要 Dashboard 也展示自定义信息，需扩展 Dashboard 源码。

## 4 项目总结

### 4.1 Sentinel 核心扩展点列表

| 扩展接口 | 用途 | 优先级 | 内置实现 | 注册方式 |
|---------|------|-------|---------|---------|
| `ReadableDataSource` | 自定义规则源（DB/Redis/文件） | P0 | Nacos/Apollo/Zookeeper | `RuleManager.register2Property()` |
| `BlockExceptionHandler` | 自定义限流响应 | P0 | DefaultBlockExceptionHandler | Web 回调注册 |
| `RequestOriginParser` | 自定义来源标识 | P1 | DefaultRequestOriginParser | Web 回调注册 |
| `UrlCleaner` | URL 聚合清洗 | P1 | DefaultUrlCleaner | Web 回调注册 |
| `ProcessorSlot` | 自定义 Slot | P2 | 8 个内置 Slot | META-INF/services SPI |
| `MetricExporter` | 自定义指标导出 | P2 | 无默认实现 | SPI + InitFunc |
| `InitFunc` | 启动初始化逻辑 | P2 | CommandCenterInitFunc | META-INF/services SPI |
| `CircuitBreaker` | 自定义熔断器 | P2 | 3 种内置实现 | `DegradeRuleManager.registerCircuitBreaker()` |
| `TrafficShapingController` | 自定义流控效果 | P2 | 3 种内置实现 | `FlowRule.setRater()` |

### 4.2 Sentinel SPI vs Java SPI 对比

| 维度 | Sentinel SPI | Java SPI (ServiceLoader) |
|------|------------|------------------------|
| 加载方式 | 懒加载（首次使用时初始化） | 遍历时立即实例化所有实现 |
| 排序支持 | `@SpiOrder` 注解 | 无原生排序 |
| 别名支持 | `@Spi` 注解的 value 属性 | 不支持 |
| 单例/多例 | 默认单例 | 每次遍历新建实例 |
| 实现选择 | `SpiLoader.loadFirstInstance()` | 需要手动遍历选择 |
| 失败处理 | 跳过失败实现继续加载 | 抛出 ServiceConfigurationError |

### 4.3 自定义扩展开发检查清单

- [ ] 确认扩展接口的完整签名和方法契约
- [ ] 实现所有必须方法（不要遗漏 close/cleanup）
- [ ] 添加 `@SpiOrder` 注解避免优先级冲突
- [ ] 创建 `META-INF/services/<接口全限定名>` 文件
- [ ] 编写单元测试验证扩展被正确加载
- [ ] 测试并发场景（多个线程同时触发扩展逻辑）
- [ ] 评估性能开销（压测对比启用/禁用扩展的 QPS 差异）
- [ ] 编写扩展文档（配置项、降级策略、已知限制）

### 4.4 内置 Slot Chain 顺序参考

| Order | Slot | 职责 |
|-------|------|------|
| -10000 | NodeSelectorSlot | 构建调用树 |
| -9000 | ClusterBuilderSlot | 创建 ClusterNode |
| -8000 | LogSlot | 记录 block 日志 |
| -7000 | StatisticSlot | 统计 PASS/BLOCK/RT |
| -6000 | AuthoritySlot | 黑白名单 |
| -5000 | SystemSlot | 系统保护 |
| -4000 | ParamFlowSlot | 热点参数限流 |
| -2000 | FlowSlot | 流控 |
| -1000 | DegradeSlot | 熔断降级 |

### 4.5 思考题

1. 如果你自定义了一个基于数据库的 DataSource，当数据库暂时不可用时，应该如何设计降级策略？
2. Sentinel 的 SPI 机制和 Java SPI 有什么区别？为什么 Sentinel 要自己实现一套？

### 4.6 推广计划

- **核心开发**：团队建立 Sentinel 扩展库（如 `sentinel-ext-jdbc`、`sentinel-ext-json`），供所有服务复用。
- **架构师**：审核自定义扩展的线程安全性和性能开销。
