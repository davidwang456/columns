# 第2章：搭建本地实验环境与 Sentinel Dashboard

## 1 项目背景

上一章我们用一个简单的 curl 命令体验了 Sentinel 的 QPS 限流，但问题来了：规则是硬编码在 Java 代码里的。小胖尝试把 QPS 阈值从 2 调成 5，结果需要改代码 → 重新编译 → 重新部署 → 重新压测，一个来回就是 10 分钟。更麻烦的是，他想知道"当前到底有多少请求被拦截了"，只能看日志数行数，完全没有可视化的方式。

测试团队也反馈："我们压测的时候看不到实时的流量曲线，只知道最后多少成功多少失败，根本没法判断限流是'平滑触发'还是'突然卡死'。"

运维团队更头疼："这套东西上线后，半夜流量突然涨了，谁来看？看什么？难道让开发 SSH 上去 tail -f 日志？"

这些痛点的根源是：**只有运行时规则，没有控制台，没有可视化的指标反馈**。Sentinel Dashboard 正是为了解决这些问题而生——它提供了一个 Web 控制台，支持实时监控流量、动态配置规则、查看簇点链路，而且与应用完全解耦。

本章将带你在本地用 Docker Compose 一键启动 Sentinel Dashboard 和示例订单服务，让"写规则 → 看指标 → 验证效果"的闭环缩短到 30 秒以内。

## 2 项目设计

**小胖**（兴奋地盯着屏幕）："大师大师！我看到 Sentinel 有个 Dashboard，是不是像《星际迷航》里的飞船控制面板？各种仪表盘，还能一键发射光子鱼雷那种？"

**大师**（笑）："差不多，不过它不能打外星人，只能帮你管理流量。Dashboard 本质上是一个 Spring Boot 应用，独立部署，跟你保护的服务通过 HTTP 通信。"

**小白**："我看了 Dashboard 的 GitHub 仓库，它用的是 Spring MVC + 内存存储，这意味着规则是存在 Dashboard 的 JVM 内存里的？"

**大师**："没错。这是 Dashboard 最大的'坑'：它默认把规则存内存，Dashboard 重启后所有规则就丢了。我们第 17 章会解决这个问题，用 Nacos 做持久化。"

**小胖**："那客户端怎么连到 Dashboard？总不能靠心灵感应吧？"

**大师**："靠一个简单的配置：`spring.cloud.sentinel.transport.dashboard`。Sentinel 客户端会启动一个 Netty HTTP Server，Dashboard 通过这个端口向客户端下发规则，同时也从客户端拉取指标数据。"

**小白**："所以是双向通信？Dashboard → 客户端推送规则，客户端 → Dashboard 上报指标？"

**大师**："对，但有一个细节很重要：指标拉取是从 Dashboard 主动发起的 HTTP 请求去客户端拉，而不是客户端推。默认每秒拉一次。"

**小胖**："那我在 Docker 里部署，网络会有问题吗？"

**大师**："这就是本章要解决的。我们用一个 docker-compose.yml 把 Dashboard 和订单服务放在同一个网络中，访问没问题。而且我会教你们常见的启动问题和排查方法。"

**小白**："还有个问题，Dashboard 鉴权呢？总不能谁都能改规则吧？"

**大师**："Sentinel Dashboard 1.8.6 支持简单的用户名密码登录，通过启动参数 `-Dsentinel.dashboard.auth.username=admin -Dsentinel.dashboard.auth.password=sentinel` 开启。生产环境建议前面再套一层 Nginx 反向代理做更细粒度的权限控制。"

**小胖**（看着应用列表）："诶大师，我开了 3 个机器上的 order-service，Dashboard 里显示的是 3 台机器还是 1 台？"

**大师**："3 台。Dashboard 按 IP + 端口区分机器。所以每台机器的 8719 端口都要能被 Dashboard 访问到。如果同 IP 多个实例，端口号会自动递增。"

**小白**："那如果我用了 `@SentinelResource` 注解但忘记配 `blockHandler`，被限流时会怎么样？"

**大师**："那就直接抛 `FlowException` 到前端——用户会看到 Spring Boot 默认的 500 错误页，体验很差。所以 `@SentinelResource` 的 blockHandler 不是可选的，是必须的。另一个替代方案是用全局异常处理器 `@ControllerAdvice` 统一捕获 `BlockException` 并返回友好提示。"

**小胖**："还有个问题——如果我在本地 IDEA 中启动服务，Dashboard 在 Docker 容器里，它们在两个网络里，能通信吗？"

**大师**："如果是 Docker Desktop，容器通过 `host.docker.internal` 可以访问宿主机。你反过来——容器里 Dashboard 要访问宿主机的 8719 端口，需要确保宿主机端口没有被防火墙拦截，且客户端配置的 `dashboard` 地址是容器的映射端口。最简方案是宿主机也跑在 Docker 里——我们在步骤一就是这么设计的。"

**技术映射**：
- Dashboard 通信架构：Sentinel Client（应用）启动一个 Netty SimpleHttpServer，监听在 `csp.sentinel.api.port` 端口（默认 8719）。Dashboard 通过这个端口下发规则和拉取指标。心跳每 10 秒一次，指标每 1 秒拉一次。
- 客户端关键配置项：
  - `spring.cloud.sentinel.transport.dashboard`：Dashboard 地址，如 `localhost:8080`
  - `spring.cloud.sentinel.transport.port`：客户端 API 端口，默认 8719
  - `spring.cloud.sentinel.eager`：是否提前初始化 Sentinel（Spring Cloud Alibaba 下建议设为 true）

## 3 项目实战

### 3.1 环境准备

| 工具 | 版本 | 说明 |
|------|------|------|
| Docker | 24+ | 容器运行时 |
| Docker Compose | v2.23+ | 多容器编排 |
| JDK | 17 | Spring Boot 3.x 最低要求 |
| Maven | 3.8+ | 项目构建 |
| Sentinel Dashboard | 1.8.6 | 控制台 |
| JMeter | 5.6 | 压测触发流量 |

### 3.2 分步实现

**步骤一：下载并启动 Sentinel Dashboard**

创建工作目录：

```bash
mkdir sentinel-lab && cd sentinel-lab
```

编写 `docker-compose.yml`：

```yaml
version: '3.8'
services:
  sentinel-dashboard:
    image: bladex/sentinel-dashboard:1.8.6
    container_name: sentinel-dashboard
    ports:
      - "8080:8080"
    environment:
      - JAVA_OPTS=-Dserver.port=8080 -Dcsp.sentinel.dashboard.server=localhost:8080
      - SENTINEL_DASHBOARD_USERNAME=admin
      - SENTINEL_DASHBOARD_PASSWORD=sentinel

  order-service:
    build: ./order-service
    container_name: order-service
    ports:
      - "8090:8090"
    environment:
      - JAVA_OPTS=-Dserver.port=8090
          -Dcsp.sentinel.dashboard.server=sentinel-dashboard:8080
          -Dproject.name=order-service
    depends_on:
      - sentinel-dashboard
```

启动 Dashboard：

```bash
docker-compose up -d sentinel-dashboard
```

访问 `http://localhost:8080`，使用 `admin/sentinel` 登录。此时控制台是空白的——因为还没有客户端连接。

**步骤二：创建订单服务并连接 Dashboard**

创建 `order-service` 目录，编写 `pom.xml`：

```xml
<parent>
    <groupId>org.springframework.boot</groupId>
    <artifactId>spring-boot-starter-parent</artifactId>
    <version>3.2.5</version>
</parent>
<dependencies>
    <dependency>
        <groupId>org.springframework.boot</groupId>
        <artifactId>spring-boot-starter-web</artifactId>
    </dependency>
    <!-- Spring Cloud Alibaba Sentinel Starter -->
    <dependency>
        <groupId>com.alibaba.cloud</groupId>
        <artifactId>spring-cloud-starter-alibaba-sentinel</artifactId>
        <version>2023.0.1.0</version>
    </dependency>
</dependencies>
```

配置 `application.yml`：

```yaml
server:
  port: 8090

spring:
  application:
    name: order-service
  cloud:
    sentinel:
      transport:
        dashboard: sentinel-dashboard:8080
        port: 8719
      eager: true
```

编写 Controller：

```java
@RestController
public class OrderController {

    @GetMapping("/order/create")
    @SentinelResource(value = "createOrder", blockHandler = "createOrderBlock")
    public String createOrder(@RequestParam String skuId) {
        return "订单创建成功, skuId=" + skuId;
    }

    public String createOrderBlock(String skuId, BlockException e) {
        return "系统繁忙，请稍后重试";
    }

    @GetMapping("/order/query")
    public String queryOrder() {
        return "订单查询结果";
    }

    @GetMapping("/hello")
    public String hello() {
        return "Hello Sentinel!";
    }
}
```

构建并启动：

```bash
mvn clean package -DskipTests
docker-compose up -d --build order-service
```

**步骤三：验证 Dashboard 连接**

1. 打开 `http://localhost:8080`，左侧菜单会出现 `order-service` 应用。
2. 点击进入"簇点链路"，会看到 `createOrder`、`queryOrder`、`hello` 三个资源。
3. 用 curl 多次访问接口：

```bash
curl http://localhost:8090/order/create?skuId=1001
curl http://localhost:8090/order/query
curl http://localhost:8090/hello
```

4. 返回 Dashboard，查看"实时监控"页面，能看到请求的通过 QPS 曲线。

**步骤四：通过 Dashboard 配置流控规则**

1. 进入"簇点链路" → 点击 `createOrder` 的"流控"按钮。
2. 阈值类型选择"QPS"，阈值设为 2，流控模式为"直接"。
3. 点击新增。
4. 用 JMeter 或连续 curl 快速调用 `/order/create`：

```bash
for i in {1..5}; do curl -s http://localhost:8090/order/create?skuId=1001 &; done
```

你会看到前 2 个请求返回成功，其余返回"系统繁忙，请稍后重试"。

**步骤五：常见启动问题排查**

| 现象 | 原因 | 解决方法 |
|------|------|---------|
| Dashboard 启动后页面空白 | 未登录或 Cookie 问题 | 用 admin/sentinel 登录，清理浏览器缓存 |
| Dashboard 看不到应用 | 客户端 dashboard 地址配错 | 检查 Docker 网络，容器内用服务名互访 |
| 应用日志报 `connection refused` | Dashboard 端口未开放或防火墙拦截 | `docker-compose logs sentinel-dashboard` 检查 |
| 应用频繁掉线 | 心跳超时，可能是容器资源不足 | 增加 Docker 内存限制 |
| JDK 版本不兼容 | Sentinel 1.8.6 对 JDK 17+ 部分反射 API 有兼容问题 | 添加 JVM 参数 `--add-opens java.base/java.lang.reflect=ALL-UNNAMED` |

### 3.3 测试验证

创建一个简单的压测脚本 `test-dashboard.sh`：

```bash
#!/bin/bash
echo "=== 1. 正常访问 ==="
for i in {1..3}; do
  curl -s http://localhost:8090/order/create?skuId=TEST_$i
  echo ""
done

echo "=== 2. 高并发触发限流 ==="
ab -n 20 -c 5 http://localhost:8090/order/create?skuId=BURST

echo "=== 3. Dashboard 状态检查 ==="
curl -s http://localhost:8080/api/info | python3 -m json.tool
```

验证标准：
- Dashboard 上能看到实时 QPS 曲线
- 超过阈值的请求返回 BlockHandler 兜底文案
- 重启 order-service 后规则消失（证明默认内存态）

### 3.4 Dashboard API 编程式操作

Sentinel Dashboard 提供了 REST API，可以不通过界面直接操作规则和查询指标：

```bash
# 1. 查询所有已连接的应用
curl -s http://localhost:8080/app/basicInfo.json | python3 -m json.tool

# 2. 查询指定应用的流控规则
curl -s "http://localhost:8080/v2/flow/rules?app=order-service" | python3 -m json.tool

# 3. 通过 API 新增流控规则
curl -X POST http://localhost:8080/v2/flow/rule \
  -H "Content-Type: application/json" \
  -d '{
    "app": "order-service",
    "resource": "hello",
    "grade": 1,
    "count": 5,
    "limitApp": "default",
    "controlBehavior": 0
  }'

# 4. 查询实时监控指标
curl -s "http://localhost:8080/metric/queryTopResourceMetric.json?app=order-service&pageSize=5" \
  | python3 -m json.tool
```

### 3.5 全局 BlockException 处理器

除了 `@SentinelResource` 的 blockHandler，还可以用全局方式处理 BlockException：

```java
@RestControllerAdvice
public class SentinelBlockHandlerAdvice {

    @ExceptionHandler(BlockException.class)
    @ResponseStatus(HttpStatus.TOO_MANY_REQUESTS)
    public Map<String, Object> handleBlock(BlockException e) {
        Map<String, Object> result = new HashMap<>();
        result.put("code", "TOO_MANY_REQUESTS");
        result.put("msg", e instanceof FlowException ? "流控限制"
                : e instanceof DegradeException ? "服务熔断"
                : e instanceof ParamFlowException ? "热点参数限流"
                : e instanceof SystemBlockException ? "系统保护"
                : e instanceof AuthorityException ? "授权限制"
                : "未知拦截");
        result.put("resource", e.getRule().getResource());
        return result;
    }
}
```

这种方式的优势是：不需要在每个 Controller 方法上配置 blockHandler，统一返回格式。但缺点是：没法针对不同资源返回不同的 fallback 业务逻辑。

## 4 项目总结

### 4.1 优点与缺点

| 维度 | Sentinel Dashboard | Hystrix Dashboard | 自建日志看板 |
|------|-------------------|------------------|------------|
| 接入成本 | 极低（Docker 一键启动） | 需集成 Turbine | 需要 ELK/Loki 全家桶 |
| 实时性 | 秒级（1s 拉取一次） | 秒级 | 取决于采集间隔 |
| 规则管理 | 可视化增删改 | 需 Hystrix 动态配置支持 | 无 |
| 持久化 | 默认内存态，依赖外部数据源 | 不持久化 | 天然持久化 |
| 集群支持 | 需 Token Server | 无 | 无 |
| 稳定性 | Dashboard 挂了不影响客户端 | 同 | Dashboard 本身稳定 |
| 鉴权 | 基础用户名密码 | 无 | 可自定义 |

### 4.2 适用场景

- **本地开发和调试**：秒级反馈，快速验证规则效果
- **测试环境持续压测**：配合 JMeter 实时观察限流/熔断效果
- **小规模生产环境**：单机 Dashboard（前提：规则已持久化到 Nacos）
- **演示和培训**：可视化展示流量防护能力，降低新人学习曲线
- **不适用场景**：超大规模集群（Dashboard 单机有性能瓶颈）；对安全要求极高且无内外网隔离的环境

### 4.3 注意事项

1. **Dashboard 端口**：默认 8080，如果冲突可在启动参数中修改 `-Dserver.port=9090`。
2. **客户端端口 8719**：如果被占用会自动 +1（8720、8721...），注意防火墙规则。
3. **Docker 网络**：如果 Dashboard 和应用在不同宿主机，`dashboard` 地址必须用实际 IP，不能用 `localhost`。
4. **生产鉴权**：Dashboard 应部署在内网，外加 Nginx 反向代理 + IP 白名单 + 企业 SSO。

### 4.4 常见踩坑经验

| 故障现象 | 根因 | 解决方案 |
|---------|------|---------|
| Dashboard 规则修改后不生效 | 客户端与 Dashboard 版本不一致 |统一 Sentinel 版本为 1.8.6 |
| 指标数据断断续续 | 客户端端口 8719 被防火墙拦截 | 放行 8719 端口或指定固定端口范围 |
| Dashboard 内存溢出 | 指标数据积累过多 | 定期重启 Dashboard 或接入外部存储 |
| Spring Cloud Alibaba 版本冲突 | 版本矩阵不匹配 | 参考 Spring Cloud Alibaba 官方版本说明 |

### 4.5 开发环境快速排障口诀

| 现象 | 第一个检查项 | 第二个检查项 |
|------|------------|------------|
| Dashboard 无应用列表 | `csp.sentinel.dashboard.server` 配置是否正确 | 客户端是否已发至少一次请求 |
| 实时监控无数据 | 是否用了 `curl` 等工具发过请求 | `csp.sentinel.api.port`（8719）是否可达 |
| 流控规则点不动 | 资源是否已出现在簇点链路中 | 浏览器是否缓存了旧页面（Ctrl+F5） |
| 规则推了不生效 | 规则 QPS 阈值是否设为了 0 | 客户端日志是否有 `CommandCenterHandler` 报错 |

### 4.6 Dashboard 部署架构速查

| 部署方式 | 适用环境 | 优点 | 缺点 |
|---------|---------|------|------|
| Docker 单机 | 本地开发 | 一键启动，零配置 | 无持久化，重启丢数据 |
| Docker Compose（本章方案） | 开发 + 测试 | 多服务编排，网络隔离 | 单机资源限制 |
| K8s Deployment | 预发布 + 生产 | 自动重启，滚动升级 | 需额外配置 Service/Ingress |
| 嵌入应用同 JVM | 仅限 Demo | 零网络开销 | 应用和 Dashboard 生命周期绑定 |

### 4.5 思考题

1. 如果 Dashboard 挂了，客户端的流控规则还会生效吗？为什么？
2. 为什么 Dashboard 采用"主动拉取"而非"客户端推送"的方式采集指标？这两种方式各有什么优缺点？

### 4.6 推广计划

- **开发团队**：每人本地搭建一套 Sentinel Lab，养成在本地验证规则的习惯。
- **测试团队**：将 Dashboard 部署到测试环境，方便在压测时实时观察限流效果。
- **运维团队**：在预发布环境部署 Dashboard，配合监控验证规则配置的合理性。
