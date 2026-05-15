# 第21章：Spring Cloud Gateway 网关流控

## 1 项目背景

第 19-20 章我们在微服务间（Feign/Dubbo）加入了 Sentinel 保护，故障不再沿调用链无限扩散了。但安全团队在一次渗透测试中发现了一个严重问题：

攻击者绕过了前端的所有限流逻辑，直接用 IP 地址调用了后端服务的 REST API。虽然服务层面有 Sentinel 限流保护，但这些限流规则是按"全局 QPS"设计的——攻击者只要用低频（低于阈值）持续调用管理接口，就能慢慢窃取数据或破坏数据。**限流不能替代安全，但安全也不能没有限流。**

而且运维团队观察到一个现象：Nginx 网关层的 QPS 是 5000，但后端订单服务的 QPS 只有 3000——中间 2000 QPS 被谁消耗了？排查发现是爬虫流量、健康检查、不存在的路径等"垃圾流量"全打到了后端服务。

结论很明显：**Sentinel 的防护应该前移到网关层**。在网关统一入口处做一层流量防护——挡掉非法请求、限制高频 IP、对不同的 API 路由做差异化限流——可以大幅减轻后端服务的压力。

Spring Cloud Gateway 作为 Spring 生态的主流网关，天然与 Sentinel 集成。本章将带你搭建一个 Sentinel + Gateway 的网关层防护方案。

## 2 项目设计

**小胖**（看着 Nginx 和后端的流量差异）："网关 QPS 5000，后端才 3000，那 2000 QPS 跑哪里去了？"

**大师**："被网关自己消化了——404、401、爬虫、恶意扫描。这些请求虽然在后端服务没有记录，但确实消耗了网关的资源。"

**小白**："所以应该在网关层就把这些垃圾流量挡掉？Sentinel 的 Gateway Adapter 能做到吗？"

**大师**："能。Gateway 的 Sentinel 集成有两个层面：一是按**路由**（Route ID）限流，二是按**API 分组**限流。路由是 Gateway 的基本转发单元，API 分组是多个路由的逻辑集合。"

**小胖**："那怎么区分'正常用户的请求'和'爬虫的请求'？"

**大师**："通过 Gateway 的 `RequestOriginParser`，你可以从 Header、Query、Cookie 中提取请求特征。比如某个来源的 User-Agent 是空，或者某个 IP 在短时间内请求了不存在的路径——这些都可以作为限流和授权的依据。"

**小胖**："网关的限流和服务的限流如果阈值冲突了怎么办？比如网关限 200 QPS，服务层限 100 QPS——最终谁会挡掉更多的请求？"

**大师**："网关层先挡。请求先经过网关 → 网关放行 200 QPS → 再到服务层 → 服务层只收 100 QPS。最终结果是：网关挡掉了 300 QPS（假设实际 500 QPS），服务层又挡掉了 100 QPS。但用户看到的都是网关返回的 429——因为服务层挡掉的请求在网关层已经返回了，不会到达用户。"

**小白**："网关热点参数限流的 `parseStrategy` 有几种？文档上我没找到完整说明。"

**大师**："五种解析策略。0=从 URL 参数解析，1=从 Header 解析，2=从 Cookie 解析，3=从客户端 IP 解析，4=从 Host 解析。最常用的是 3（IP 解析）——防爬虫和恶意扫描。注意 parseStrategy=3 时 `fieldName` 字段会被忽略。"

**小胖**："大师，网关层做授权规则（黑白名单）和 Nginx 的 `allow/deny` 有什么区别？不都是挡 IP 吗？"

**大师**："Nginx 的 IP 黑白名单是静态的——写在配置文件里，改了要 reload。Sentinel 网关授权规则可以动态更新（通过 Nacos 热更新），而且可以和 Sentinel 的流控、熔断联动。比如：某个 IP 在 1 分钟内被限流了 50 次 → 自动加入黑名单 10 分钟 → 10 分钟后自动移除。这是 Nginx 做不到的。"

**技术映射**：
- `sentinel-spring-cloud-gateway-adapter`：提供了 Gateway 专用的过滤器 `SentinelGatewayFilter`，在请求进入 Gateway 的过滤器链时执行 Sentinel 规则校验。
- 网关层的资源维度：
  - **Route ID**：例如 `order-service-route`，对应到 Gateway 的 `spring.cloud.gateway.routes[0].id`
  - **API 分组**：通过 `GatewayApiDefinitionManager` 将多个路由组合为一个逻辑 API 组
- 网关层限流与后端限流的关系：网关是**粗粒度**（按路由/API 组），后端是**细粒度**（按接口/方法）。两者共同构成了**分层流量防护**。

## 3 项目实战

### 3.1 环境准备

`pom.xml`：

```xml
<dependency>
    <groupId>org.springframework.cloud</groupId>
    <artifactId>spring-cloud-starter-gateway</artifactId>
</dependency>
<dependency>
    <groupId>com.alibaba.cloud</groupId>
    <artifactId>spring-cloud-alibaba-sentinel-gateway</artifactId>
    <version>2023.0.1.0</version>
</dependency>
<dependency>
    <groupId>com.alibaba.cloud</groupId>
    <artifactId>spring-cloud-starter-alibaba-sentinel</artifactId>
</dependency>
```

### 3.2 分步实现

**步骤一：Gateway 路由配置**

`application.yml`：

```yaml
server:
  port: 9000

spring:
  application:
    name: api-gateway
  cloud:
    gateway:
      routes:
        - id: order-service-route
          uri: http://localhost:8090
          predicates:
            - Path=/order/**
          filters:
            - StripPrefix=0
        - id: inventory-service-route
          uri: http://localhost:8082
          predicates:
            - Path=/inventory/**
    sentinel:
      transport:
        dashboard: localhost:8080
        port: 8720
      eager: true
      # Gateway 的 Sentinel 规则数据源配置
      datasource:
        gw-flow:
          nacos:
            server-addr: localhost:8848
            data-id: api-gateway-flow-rules
            group-id: SENTINEL_GATEWAY
            rule-type: gw-flow
        gw-api-group:
          nacos:
            server-addr: localhost:8848
            data-id: api-gateway-api-groups
            group-id: SENTINEL_GATEWAY
            rule-type: gw-api-group
```

**步骤二：按路由限流**

在 Nacos 中创建 `api-gateway-flow-rules`：

```json
[
  {
    "resource": "order-service-route",
    "resourceMode": 0,
    "grade": 1,
    "count": 100,
    "intervalSec": 1,
    "controlBehavior": 0,
    "burst": 0
  },
  {
    "resource": "inventory-service-route",
    "resourceMode": 0,
    "grade": 1,
    "count": 50,
    "intervalSec": 1
  }
]
```

`resourceMode=0` 表示按路由 ID 匹配。

**步骤三：按 API 分组限流**

在 Nacos 中创建 `api-gateway-api-groups`：

```json
[
  {
    "apiName": "order-api",
    "predicateItems": [
      {
        "pattern": "/order/create",
        "matchStrategy": 0
      },
      {
        "pattern": "/order/query/**",
        "matchStrategy": 0
      }
    ]
  },
  {
    "apiName": "admin-api",
    "predicateItems": [
      {
        "pattern": "/order/admin/**",
        "matchStrategy": 0
      }
    ]
  }
]
```

然后在流控规则中按 API 组名限流：

```json
[
  {
    "resource": "order-api",
    "resourceMode": 1,
    "grade": 1,
    "count": 200,
    "intervalSec": 1
  },
  {
    "resource": "admin-api",
    "resourceMode": 1,
    "grade": 1,
    "count": 10,
    "intervalSec": 1
  }
]
```

`resourceMode=1` 表示按 API 分组名匹配。

**步骤四：自定义网关限流异常响应**

默认限流响应返回 `429 Too Many Requests` + 空白 body，体验很差。自定义：

```java
@Configuration
public class GatewaySentinelConfig {

    @PostConstruct
    public void initBlockHandlers() {
        // 自定义限流响应
        GatewayCallbackManager.setBlockHandler(
            new BlockRequestHandler() {
                @Override
                public Mono<ServerResponse> handleRequest(
                        ServerWebExchange exchange, Throwable t) {

                    Map<String, Object> result = new HashMap<>();
                    if (t instanceof FlowException) {
                        result.put("code", "FLOW_LIMIT");
                        result.put("msg", "网关流控：请求太频繁，请稍后再试");
                    } else if (t instanceof DegradeException) {
                        result.put("code", "DEGRADE");
                        result.put("msg", "网关熔断：服务暂不可用");
                    } else if (t instanceof ParamFlowException) {
                        result.put("code", "PARAM_FLOW");
                        result.put("msg", "网关热点限流：该资源访问过热");
                    } else {
                        result.put("code", "BLOCK");
                        result.put("msg", "请求被网关拦截");
                    }
                    result.put("timestamp", System.currentTimeMillis());

                    return ServerResponse.status(429)
                        .contentType(MediaType.APPLICATION_JSON)
                        .bodyValue(result);
                }
            }
        );
    }
}
```

**步骤五：按用户 ID / IP 做网关热点限流**

```java
@Configuration
public class GatewayParamFlowConfig {

    @PostConstruct
    public void init() {
        // 将某些参数提取为 Sentinel 的热点参数
        GatewayCallbackManager.setRequestOriginParser(exchange -> {
            // 从 Header 中提取 userId 作为热点参数维度
            String userId = exchange.getRequest()
                .getHeaders().getFirst("X-User-Id");
            return userId != null ? userId : "anonymous";
        });
    }
}
```

然后在网关流控规则中使用 `paramItem` 配置热点参数：

```json
[
  {
    "resource": "order-api",
    "resourceMode": 1,
    "grade": 1,
    "count": 10,
    "intervalSec": 1,
    "paramItem": {
      "parseStrategy": 3,
      "fieldName": "X-User-Id",
      "pattern": null,
      "matchStrategy": 0
    }
  }
]
```

**步骤六：分层策略——网关层 vs 服务层**

| 保护层 | 规则粒度 | 典型阈值 | 职责 |
|-------|---------|---------|------|
| 网关层 | 路由/API 分组 | QPS=500 | 粗粒度入口保护、拦截垃圾流量 |
| 网关层 | 用户 ID 热点 | QPS=10 | 防单用户高频刷接口 |
| 服务层 | 接口/方法 | QPS=100 | 细粒度业务保护、内部调用熔断 |
| 服务层 | 热点参数 | QPS=20 | 防爆款商品/热点用户 |

**验证**：

```bash
# 1. 正常请求（通过网关）
curl -H "X-User-Id: USER_001" http://localhost:9000/order/create
# 期望：正常返回（接口返回结果）

# 2. 触发网关限流（快速连续请求）
for i in {1..20}; do
  curl -H "X-User-Id: USER_001" http://localhost:9000/order/create &
done
# 期望：部分返回 429 + 自定义 JSON

# 3. 触发路由级限流
ab -n 200 -c 50 http://localhost:9000/inventory/query?skuId=SKU001
# 期望：超过 50 QPS 的部分返回 429

# 4. 验证限流后后端没有收到请求
# 在订单服务日志中观察实际收到的请求数
```

**踩坑记录**：

1. **Gateway 与普通服务的 Sentinel 配置隔离**：Gateway 的 Sentinel 端口配置和规则类型与普通服务不同，`rule-type` 是 `gw-flow` 和 `gw-api-group` 而非 `flow`。
2. **API 分组和路由规则的优先级**：如果同一个请求匹配了多个规则，Sentinel 会按规则加载顺序判断。建议先配 API 分组规则（粗粒度），再配路由规则（细粒度）。
3. **Gateway 和 Nacos 的网络连通性**：Gateway 也需要连接 Nacos 加载规则，确保 Gateway 所在网络能访问 Nacos。

**步骤七：网关层动态 IP 黑名单（基于 Sentinel 授权规则）**

```java
@Configuration
public class DynamicIpBlacklistConfig {

    @PostConstruct
    public void init() {
        // 自定义请求来源解析器 —— 解析 IP
        GatewayCallbackManager.setRequestOriginParser(exchange -> {
            return Objects.requireNonNull(
                exchange.getRequest().getRemoteAddress()).getHostString();
        });
    }

    // 配合 Nacos 中的授权规则，可动态更新黑名单 IP
    // Nacos DataId: api-gateway-authority-rules
    // 规则类型: gw-authority (需要自定义 gw-api-group 配合)
    // 内容示例:
    // [{"resource":"order-api","limitApp":"192.168.1.100,10.0.0.55",
    //   "strategy":1}] // strategy=1 表示黑名单
}
```

**步骤八：网关层 Sentinel 与 Nginx 限流的分工建议**

| 能力 | Nginx | Sentinel Gateway | 建议 |
|------|-------|-----------------|------|
| IP 连接数限制 | ✅ `limit_conn_zone` | ✅ 可自定义 | Nginx 做第一层 |
| 静态 IP 黑名单 | ✅ `deny` 指令 | ✅ 授权规则 | Nginx（性能更高） |
| 动态规则热更新 | ❌ 需 reload | ✅ Nacos 推送 | Sentinel |
| 按 API 路径限流 | ✅ `limit_req` with map | ✅ API 分组 | Sentinel（更灵活） |
| 按用户 ID 限流 | ❌ | ✅ 热点参数 | Sentinel |
| JSON 格式错误响应 | ❌ 默认 HTML | ✅ 自定义 | Sentinel |
| 性能 | 极高（C 实现） | 高（Java Netty） | Nginx 在最外层 |

## 4 项目总结

### 4.1 优点与缺点

| 维度 | 网关层 Sentinel | 服务层 Sentinel | 纯 Nginx 限流 |
|------|----------------|----------------|-------------|
| 规则灵活性 | 高（支持热点、熔断） | 极高 | 低（仅 IP/连接数） |
| 动态变更 | 支持（Nacos 热更新） | 支持 | 需 reload |
| 异常响应定制 | JSON 自定义 | JSON 自定义 | HTML |
| 性能开销 | 低（网关路由链增加一个过滤器） | 极低 | 极低 |
| 运维复杂度 | 中 | 低 | 低 |

### 4.2 适用场景

- API 网关统一入口，对所有后端服务做第一层流量防护
- 多端（App/Web/H5/小程序）差异化的限流策略
- 防爬虫和恶意扫描——通过 IP/Header 识别并限流
- 后端服务"软着陆"——新服务上线时先在网关限流，逐步放量

### 4.3 注意事项

1. 网关的 Sentinel 数据源 `rule-type` 必须用 `gw-flow` 和 `gw-api-group`，不能用普通的 `flow`。
2. 网关的流控规则没有线程数模式、没有预热模式、没有匀速排队——只支持 QPS 直接拒绝。
3. API 分组的 `matchStrategy`：0=精确匹配，1=正则匹配，2=前缀匹配。
4. 网关层限流 + 服务层限流是累加效果，如果网关限 100、服务层限 50，实际可用 QPS 是 min(100, 50)=50（但分别在两层被拦截的返回信息不同）。

### 4.4 Gateway Sentinel 与普通 Sentinel 配置差异

| 配置项 | Gateway Sentinel | 普通 Sentinel |
|-------|-----------------|-------------|
| 依赖 | `spring-cloud-alibaba-sentinel-gateway` | `spring-cloud-starter-alibaba-sentinel` |
| rule-type | `gw-flow`, `gw-api-group` | `flow`, `degrade`, `param-flow` 等 |
| 流控模式 | 仅 QPS 直接拒绝 | QPS/线程/预热/排队 |
| 资源维度 | 路由 ID / API 分组 | 接口方法 / 自定义资源名 |
| 异常响应 | `BlockRequestHandler` (ServerResponse) | `BlockException` + blockHandler |

### 4.5 网关层防护的分层设计

```
用户请求 → Nginx (IP 连接数限制 + 静态黑名单)
          → Sentinel Gateway (路由限流 + API 分组限流 + 热点参数限流 + 授权规则)
          → 后端服务 Sentinel (接口限流 + 熔断 + 系统保护)
```

每层的拦截返回码建议不同：
- Nginx: 503 Service Unavailable
- Gateway: 429 Too Many Requests (JSON body)
- 服务层: 含 blockHandler 的业务响应

### 4.4 思考题

1. 如果网关层配置了 QPS=100 的限流，服务层配置了 QPS=200 的限流。当实际有 150 QPS 打到网关（假设请求均匀分布到 1 个后端实例）时，最终服务层实际接收到多少请求？
2. 网关层的热点参数限流和服务层的热点参数限流有什么区别？如果一个 skuId 在网关层就达到了热点限制，它还会到达服务层吗？

### 4.5 推广计划

- **运维团队**：负责网关层 Sentinel 规则的管理，与 Nginx 限流合理分工。
- **开发团队**：所有新上线的服务必须在网关层配置基础的限流规则。
- **测试团队**：设计网关层压测方案——模拟多种 Header/Query/Cookie 组合验证限流规则。
