# 第11章：Dashboard 操作指南与规则生命周期

## 1 项目背景

前 10 章我们把 Sentinel 的六种规则（流控、关联/链路、熔断、热点参数、系统保护、授权）都学了一遍。但在实际操作中，团队遇到了一个令人困惑的问题：

测试团队配好的 15 条流控规则，在晚上 10 点服务器自动重启后就全部消失了。第二天早上压测时才发现所有保护都"静默失效"——订单服务在毫无防护的情况下跑了 8 个小时，幸好半夜流量低，没出事故。

运维团队排查发现，这 15 条规则全是通过 Dashboard 界面手动添加的。Dashboard 默认将规则存储在 JVM 内存中，重启即丢失。这就解释了为什么重启后规则消失了。

另一个问题是：开发在 Dashboard 上修改了一条流控规则（QPS 从 100 改为 200），但服务侧 5 分钟后才生效？查看源码发现，Dashboard 推送规则不是实时的——它依赖客户端每隔一段时间拉取，或者通过心跳通道逐条下发。

更隐蔽的是，有人在 Dashboard 上删除了一个规则，但客户端因为网络抖动没收到删除通知，导致这条"已删除"的规则在客户端内存中继续生效。

这些问题都指向一个核心知识：**Sentinel 规则的生命周期管理**。规则从"创建"到"生效"中间经过了什么步骤？Dashboard 的规则和客户端的规则是什么关系？规则修改、删除后如何保证一致性？

## 2 项目设计

**小胖**（一大早看着监控面板）："我去！昨晚我辛辛苦苦配的 15 条规则全没了！谁删的？"

**大师**："没人删。是昨天半夜服务器自动重启了。Dashboard 的规则存在内存中，重启就没了。"

**小白**："那 Dashboard 不是一个持久化系统？它就是一个暂时的管理界面？"

**大师**："对。你需要把 Dashboard 理解为一个'遥控器'，而不是'存储柜'。遥控器可以控制电视换台，但遥控器电池拔了不影响电视。Dashboard 也是这样——它推送规则到客户端，客户端保存在自己的内存中。Dashboard 重启不影响已推送的规则在客户端的生效。"

**小胖**："那我的规则到底去哪了？"

**大师**："客户端还保留着推送的规则。但昨天重启的是客户端（订单服务），不是 Dashboard。客户端重启后内存清空，规则就没了。Dashboard 还傻傻地以为客户端有规则，因为它自己内存里还有——但客户端已经失忆了。"

**小白**："所以真正的问题是：客户端重启后，需要重新从某个持久化存储中加载规则？"

**大师**："对。这就是规则持久化的核心需求。在基础篇我们先用代码 `FlowRuleManager.loadRules()` 在启动时加载规则来兜底。到第 17 章我们再讲 Nacos 动态规则——那时候 Dashboard 修改会写入 Nacos，客户端从 Nacos 加载，重启也不丢。"

**小胖**："那 Dashboard 本身有哪些功能？我目前只用过流控规则，其他页面都没碰过。"

**大师**："Dashboard 有四个核心模块：**实时监控**（看每个资源的实时 QPS/RT/线程数曲线）、**簇点链路**（列出所有被 Sentinel 保护的资源及调用关系）、**规则管理**（增删改查各类规则）、**机器列表**（查看有哪些客户端连接了 Dashboard）。"

**小白**："簇点链路里的'链路'是怎么生成的？我可以看到 A 资源调用了 B 资源？"

**大师**："是的。簇点链路是根据 Sentinel 内部构建的调用树（DefaultNode 和 ClusterNode）生成的。你点开一个资源，可以看到它的上游入口和下游调用。不过注意：默认情况下 Web 场景会收敛 Context，链路只有一层。"

**小胖**："大师，如果我同时用 Dashboard 和 Nacos 数据源管理规则，以谁为准？我之前试过，Dashboard 改了一条规则，Nacos 里也有同一条，结果两个不一样——到底哪个生效了？"

**大师**："这取决于加载时机和推送顺序。如果 Nacos 规则先加载、Dashboard 规则后推送，Dashboard 的会覆盖 Nacos 的——但这只在客户端内存中。下一次重启，Nacos 的又会覆盖回来。所以生产环境必须统一管理入口：要么全用 Nacos，要么全用 Dashboard+Nacos writableDataSource。不要让两个源同时维护同一条规则。"

**小白**："那如果我想要'规则的历史版本对比'——比如昨天和今天的规则对比，Dashboard 能看吗？"

**大师**："原生 Dashboard 不支持。你需要借助 Nacos 的配置历史版本功能（每个 DataId 都有历史版本列表），或者自建审计表记录每次变更。这也是第 17 章和第 28 章要解决的问题。"

**小胖**："还有个细节问题——为什么有时候我在 Dashboard 删了一条流控规则，但客户端还在拦截？"

**大师**："两种可能。一是推送失败——Dashboard 尝试推送删除命令到客户端 8719 端口，但网络不通。二是客户端已经加载了同样的 Nacos 规则——你 Dashboard 删了，但 Nacos 规则还在，客户端每 10 秒从 Nacos 拉一次，又加载回来了。排查方法是看客户端日志：`grep 'Flow rules loaded' ~/logs/csp/sentinel-record.log`。"

**技术映射**：
- Dashboard 推送模式：客户端启动时向 Dashboard 注册 → Dashboard 将内存中的规则通过 HTTP POST 推送到客户端 `http://client-ip:8719/setRules` → 客户端 `CommandHandler` 接收并写入 `RuleManager`。
- 规则同步延迟：Dashboard 新增规则后的推送是准实时的（依赖心跳通道），但修改/删除操作在某些旧版本中可能有 1-5 秒延迟。
- 客户端心跳：每 10 秒向 Dashboard 发送一次心跳 `/registry/machine`，Dashboard 据此判断客户端是否在线。

## 3 项目实战

### 3.1 环境准备

确认第 2 章搭建的 Dashboard（`http://localhost:8080`）正常运行，订单服务已连接。

### 3.2 分步实现

**步骤一：Dashboard 四大功能模块实操**

**1. 机器列表页面**：

访问 Dashboard → 左侧 "机器列表"。你会看到类似：

```
order-service @ 192.168.1.100 (10.0.0.1:8719)
  ├── 版本: 1.8.6
  ├── 健康状态: 正常
  ├── 最后心跳: 2024-06-01 10:30:25
  └── IP@端口: 192.168.1.100:8719
```

如果客户端掉线，"最后心跳"会超过 30 秒，状态变为"离线"。

**2. 簇点链路页面**：

进入 Dashboard → 簇点链路 → 选择 `order-service`。可以看到：

```
sentinel_web_servlet_context  (入口)
├── GET:/order/create         (QPS: 5, RT: 50ms)
├── GET:/order/query          (QPS: 20, RT: 10ms)
├── GET:/goods/detail         (QPS: 0, RT: 0ms)
└── createOrder               (QPS: 5, RT: 50ms) — 自定义资源名
```

点击某个资源，可以查看该资源的实时 QPS 曲线、调用链路。

**3. 流控规则页面**：

新增一条规则：

- 资源名：`createOrder`
- 阈值类型：QPS
- 阈值：10
- 流控模式：直接
- 流控效果：快速失败
- 是否集群：否

点击"新增"后，规则立即推送到客户端。验证：

```bash
# 快速连续请求
for i in {1..15}; do curl -s http://localhost:8090/order/create?skuId=1001 & done
```

约 10 个请求成功，5 个被限流。

**4. 实时监控页面**：

进入 Dashboard → 实时监控 → 选择 `order-service`。

可以看到多条实时曲线：
- **通过 QPS**（p-pass）：每秒通过的请求数
- **拒绝 QPS**（p-block）：每秒被拦截的请求数
- **响应时间**（p-rt）：平均响应时间（毫秒）

用 JMeter 压测时观察曲线变化。

**步骤二：演示规则内存态丢失**

```bash
# 1. 确认规则生效
curl http://localhost:8090/order/create?skuId=TEST  # 正常返回

# 2. 重启订单服务
docker-compose restart order-service

# 3. 再次请求
for i in {1..10}; do curl -s http://localhost:8090/order/create?skuId=TEST & done
# ❗ 全部成功！流控规则失效了！
```

**为什么**：客户端重启后内存清空，而 Dashboard 没有主动推送规则（它不知道客户端重启了，除非重连后触发重新推送——但这可能有时延）。

**解决方案**（暂时的）：

```java
@PostConstruct
public void initFallbackRules() {
    // 兜底：如果 Dashboard 规则没推送过来，默认加载硬编码规则
    if (FlowRuleManager.getRules().isEmpty()) {
        List<FlowRule> rules = new ArrayList<>();
        FlowRule rule = new FlowRule("createOrder")
                .setGrade(RuleConstant.FLOW_GRADE_QPS)
                .setCount(10);
        rules.add(rule);
        FlowRuleManager.loadRules(rules);
    }
}
```

**步骤三：Dashboard 规则修改后的同步过程**

用 Wireshark 或日志观察规则推送过程（需要开启 DEBUG 日志）：

```yaml
logging:
  level:
    com.alibaba.csp.sentinel: DEBUG
```

在 Dashboard 修改一条规则的阈值（比如从 10 改为 20），观察客户端日志：

```log
DEBUG c.a.c.s.t.command.CommandCenterHandler - Receive command: SET_RULES
DEBUG c.a.c.s.s.block.flow.FlowRuleManager - Flow rules loaded: [FlowRule{...}]
```

规则即时生效，不需要重启服务。

**步骤四：多环境 Dashboard 隔离策略**

| 环境 | Dashboard 端口 | 数据隔离方式 | 备注 |
|------|-------------|------------|------|
| dev | 8081 | 独立 Dashboard 实例 | 开发自测 |
| test | 8082 | 独立 Dashboard 实例 | 测试压测 |
| staging | 8083 | 独立 Dashboard 实例 + Nacos | 预发布验证 |
| prod | 内网专有端口 | 独立 Dashboard + Nacos + 权限 | 严格权限 |

每个环境的客户端配置不同的 `spring.cloud.sentinel.transport.dashboard` 地址，确保隔离。

**踩坑记录**：

1. **Dashboard 8080 端口冲突**：多个开发者本地启动 Dashboard 都用 8080，会冲突。用 `-Dserver.port=xxxx` 指定不同端口。
2. **客户端频繁掉线**：容器环境中客户端 IP 可能变化（Pod 重启后 IP 变了），Dashboard 认为是新实例。解决方案：配置 `spring.cloud.sentinel.transport.client-ip` 使用固定 IP 或使用 Pod Name。
3. **Dashboard 登录后立即跳回登录页**：Cookie domain 问题。确保访问 Dashboard 的域名/端口和配置一致。
4. **修改规则后客户端没生效**：检查客户端 8719 端口是否可达。如果客户端和 Dashboard 不在同一网络（如跨 K8s namespace），可能推送失败。

**步骤五：Dashboard 规则批量导入导出**

Dashboard 没有内置导入导出功能，但可以通过 API 实现：

```bash
# 导出某应用的所有流控规则
curl -s "http://localhost:8080/v2/flow/rules?app=order-service" \
  > order-service-flow-rules.json

# 导出熔断规则
curl -s "http://localhost:8080/v2/degrade/rules?app=order-service" \
  > order-service-degrade-rules.json

# 批量导入到另一环境（需先切换目标环境）
curl -X POST http://localhost:8081/v2/flow/rule \
  -H "Content-Type: application/json" \
  -d @order-service-flow-rules.json
```

**步骤六：Dashboard 健康检查与监控**

```bash
#!/bin/bash
# dashboard-health.sh — 定时监控 Dashboard 状态

DASHBOARD_URL="http://localhost:8080"

# 检查 Dashboard 是否存活
STATUS=$(curl -s -o /dev/null -w "%{http_code}" "$DASHBOARD_URL/auth/login")

# 检查连接的客户端数量
CLIENTS=$(curl -s "$DASHBOARD_URL/app/basicInfo.json" | python3 -c "import sys,json; print(len(json.load(sys.stdin)))" 2>/dev/null)

if [ "$STATUS" -ne 200 ] || [ "$CLIENTS" -eq 0 ]; then
    echo "[ALERT] Dashboard status: HTTP $STATUS, Connected clients: $CLIENTS"
    # 触发告警
fi
```

## 4 项目总结

### 4.1 优点与缺点

| 维度 | Sentinel Dashboard | Apollo/自建规则平台 | Nacos 控制台 |
|------|-------------------|-------------------|-------------|
| 部署成本 | 极低（Docker 一键） | 高（需开发） | 中 |
| 规则可视化 | 专业（流控/熔断/热点/系统/授权） | 可定制 | 通用配置管理 |
| 实时监控 | 内置 | 需集成 Grafana | 无 |
| 持久化 | 默认不支持 | 支持 | 支持 |
| 多环境支持 | 需多实例 | 原生支持 | 原生支持 |

### 4.2 适用场景

- Dashboard 适合开发和测试环境，提供快速验证规则的能力
- 生产环境应以 Nacos/Apollo 持久化为主，Dashboard 作为可视化管理界面
- Dashboard + Nacos 组合是 Spring Cloud Alibaba 官方推荐的方案

### 4.3 注意事项

1. Dashboard 默认存储是内存态，重启后规则丢失（但已推送客户端的规则不丢，除非客户端也重启）。
2. Dashboard 与客户端的通信依赖客户端的 8719 端口，确保网络可达、防火墙放行。
3. Dashboard 的"规则推送"是推送整批规则，不是增量推送。如果客户端和 Dashboard 各自有不同的规则集合，最后推送的会覆盖。
4. 生产环境务必启用 Dashboard 鉴权（`sentinel.dashboard.auth.username/password`）。

### 4.4 规则生命周期完整流程

```
创建规则 → 审批 → 保存到数据源(Nacos) → 推送到客户端 → 客户端加载到 RuleManager
                                                              ↓
审计日志 ← 回滚操作 ← 发现异常 ← 监控指标变化 ← 规则生效(FlowSlot校验)
```

### 4.5 Dashboard 与 Nacos 协同模式对比

| 模式 | 规则创建入口 | 持久化 | 动态更新 | 适用阶段 |
|------|-----------|-------|---------|---------|
| Dashboard only | Dashboard | ❌ 内存 | Dashboard → Client | 本地开发 |
| Nacos only | Nacos 控制台 | ✅ | Nacos → Client | 测试环境 |
| Dashboard + Nacos(Read) | Nacos 控制台 | ✅ | Nacos → Client | 生产环境 |
| Dashboard + Nacos(Read+Write) | Dashboard | ✅ | Dashboard → Nacos → Client | 治理平台

### 4.4 常见踩坑经验

| 故障现象 | 根因 | 解决方案 |
|---------|------|---------|
| Dashboard 页面空白 | Cookie/JWT 鉴权失败 | 清理缓存，确认登录账号密码 |
| 应用在 Dashboard 反复上下线 | 心跳超时或客户端 IP 变化 | 延长心跳间隔或固定客户端 IP |
| 规则配置界面 Loading 不出来 | Dashboard 对客户端 API 请求超时 | 检查 8719 端口可达性 |
| 规则推了但客户端没变化 | 客户端 CommandCenter 端口冲突 | 检查日志 `CommandCenterHandler` 是否报错 |

### 4.5 思考题

1. Dashboard 推送规则到客户端是通过 HTTP 协议，如果推送时客户端正在处理高并发请求，会有什么影响？Sentinel 内部是怎么处理这个并发问题的？
2. 如果同时使用 Dashboard 和 Nacos 动态数据源，两者对同一资源配置了不同的规则，客户端的最终行为是什么？谁优先生效？

### 4.6 推广计划

- **开发团队**：每人本地部署 Dashboard，养成"配规则前先在本地验证"的习惯。
- **测试团队**：将 Dashboard 作为验收标准的一部分——规则配置后截图留存，回归时对比。
- **运维团队**：负责 Dashboard 的日常巡检（心跳状态、磁盘空间、内存使用），制定 Dashboard 故障应急预案。

### 4.7 Dashboard 生产运维速查

| 运维项 | 命令/方法 | 频率 |
|-------|----------|------|
| 心跳检查 | `curl http://dashboard:8080/machine` 查看在线机器数 | 每 5 分钟 |
| 磁盘空间 | `df -h ~/logs/csp/` 检查指标日志大小 | 每天 |
| 内存使用 | `jstat -gcutil <pid> 1000` 监控 GC 频率 | 持续 |
| 规则备份 | 定期导出 Dashboard 规则 JSON 到 Git | 每天 |
| 鉴权状态 | 确认 `sentinel.dashboard.auth.enabled=true` | 每次部署 |

Dashboard 故障应急 SOP：
1. **Dashboard 宕机** → 客户端规则不受影响 → 使用 Nacos 控制台临时管理规则 → 恢复 Dashboard
2. **Dashboard 内存溢出** → 增大 JVM 堆内存 `-Xmx512m` → 或部署多实例 + Nginx 负载均衡
3. **客户端大面积离线** → 检查网络策略 → 确认 8719 端口放行 → 检查客户端心跳日志
