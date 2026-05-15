# 第18章：Nacos 动态规则生产实践

## 1 项目背景

第 17 章我们打通了 Sentinel 到 Nacos 的规则持久化链路，规则终于不会重启丢失了。但刚上线不到两周，运维团队就反馈了三个新问题：

第一，生产环境的 Nacos 中已经有 5 个微服务的 Sentinel 配置，每个服务有 5 种规则类型（flow/degrade/param-flow/system/authority），也就是说 Nacos 中至少有 25 个配置文件。运维在配置列表页面找配置时经常眼花，而且没有一种清晰的命名规范——有的服务叫 `order-flow-rules`，有的叫 `flow-rules-order`。

第二，某次线上流量异常，运维紧急在 Nacos 中将 `order-service` 的流控阈值从 500 调到 200，但等了 30 秒还没生效。排查发现运维修改了 `test` namespace 下的配置，而生产服务连的是 `prod` namespace——典型的"改错了环境"的故障。

第三，一个新人开发在调试时不小心在 Nacos 的 `prod` namespace 中修改了一条规则，导致线上短暂的限流失效。因为没有审计记录、没有变更通知，团队 2 小时后才发现。

这些问题说明：**把规则存到 Nacos 只是第一步，真正的生产实践还包括命名规范、环境隔离、权限控制、变更审计和误删恢复等多个维度**。

## 2 项目设计

**小胖**（指着 Nacos 配置列表）："这 25 个配置文件看得我眼花！flow、degrade、param-flow、system、authority 各有 5 个服务，能不能合并成一个配置？"

**大师**："可以，但我不建议。合并了之后你改一条流控规则，整个 5 种规则的配置都要重新下发，增加了变更风险和影响面。我倾向于每个服务、每种规则类型一个配置文件。但命名必须规范。"

**小白**："命名规范应该怎么定？"

**大师**："我推荐的格式是：`{service-name}-{rule-type}-rules`。比如 `order-service-flow-rules`、`inventory-service-degrade-rules`。这样一眼就能看出是哪个服务、哪种规则。"

**小胖**："那 namespace 怎么规划？"

**大师**："标准的四层隔离：

- `dev`：开发环境，每个开发可以有自己的 namespace（dev-zhangsan、dev-lisi）
- `test`：测试环境共享 namespace
- `staging`：预发布 namespace（与生产同级配置但低流量验证）
- `prod`：生产 namespace，严格权限控制

每个服务的 `application.yml` 中通过 `${NACOS_NAMESPACE:dev}` 引用，部署时通过环境变量注入。"

**小白**："权限控制呢？Nacos 的控制台任何人都能改配置，这是个隐患。"

**大师**："Nacos 支持 RBAC。可以给运维分配 `ROLE_ADMIN`（全局管理），给开发分配自定义角色（只读 `dev` 和 `test`，无法访问 `prod`）。生产环境的任何修改都应该走审批 + 灰度发布流程。"

**小胖**："灰度发布规则？规则又不是代码，怎么灰度？"

**大师**："规则灰度比代码灰度更简单——你不需要切流量，只需要小范围验证。比如先只对 1 个 Pod 下发新规则（通过不同的 group-id），观察 10 分钟没有异常后再全量下发。更严谨的做法是：在 staging namespace 中模拟生产流量验证规则，验证通过后由 SRE 手工同步到 prod。"

**小白**："万一 Nacos 挂了怎么办？Sentinel 客户端会丢规则吗？"

**大师**："不会丢。Nacos DataSource 在初始化时会拉取规则到本地缓存文件（`~/logs/csp/`）。即使 Nacos 宕机，Sentinel 客户端会继续使用本地缓存。但新启动的客户端就拉不到规则了——这是唯一的风险点。所以我们要求 Nacos 本身也要做高可用：3 节点集群部署，加监控告警。"

**小胖**："审计日志呢？Nacos 有原生的审计功能吗？"

**大师**："Nacos 2.3+ 支持操作审计日志，会记录谁在什么时间改了什么配置。但自带的审计日志比较简陋——没有 diff 对比。我们的方案是：额外部署一个 Nacos Config Listener Service，监听生产 namespace 的配置变更，收到变更事件后自动做 diff，并把 diff 内容 + 操作人推送到企业微信/钉钉通知群。这样出了事能 1 分钟内定位到谁改了什么。"

**小白**："如果多个团队共用一个 Nacos 集群，怎么防止配置互相覆盖？"

**大师**："关键是用好 namespace 和 group 的隔离。每个团队至少一个独立的 namespace（或 naming space 前缀）。group 按功能域划分——`SENTINEL_FLOW`、`SENTINEL_DEGRADE` 等。跨团队共享的配置必须走审批流程，不允许直接修改。建议给每个服务的配置加上 `metadata.owner` 字段，标记责任团队。"

**小胖**："还有一个实际问题：Nacos 配置内容变更后，怎么确认所有 Sentinel 客户端都收到了？总不能挨个 Pod 看日志吧。"

**大师**："这正是我们要做的。在 Sentinel 客户端暴露一个 `/actuator/sentinel-rules` 端点，返回当前生效的规则版本号和内容 Hash。然后用 Prometheus Blackbox Exporter 定期探测所有 Pod 的规则一致性。如果某个 Pod 的规则 Hash 与 Nacos 不一致，就告警。"

## 3 项目实战

### 3.1 环境准备

Nacos 2.3+ 支持完善的身份认证与 RBAC 权限控制。

### 3.2 分步实现

**步骤一：规则命名规范与目录设计**

```
Nacos 配置目录结构（推荐）：

Namespace: prod
  ├── GROUP: SENTINEL_FLOW
  │   ├── order-service-flow-rules       (JSON)
  │   ├── inventory-service-flow-rules   (JSON)
  │   └── payment-service-flow-rules     (JSON)
  ├── GROUP: SENTINEL_DEGRADE
  │   ├── order-service-degrade-rules
  │   ├── inventory-service-degrade-rules
  │   └── payment-service-degrade-rules
  ├── GROUP: SENTINEL_PARAM_FLOW
  │   └── order-service-param-flow-rules
  ├── GROUP: SENTINEL_SYSTEM
  │   └── order-service-system-rules
  └── GROUP: SENTINEL_AUTHORITY
      └── order-service-authority-rules
```

**对应 application.yml**：

```yaml
spring:
  cloud:
    sentinel:
      datasource:
        flow:
          nacos:
            server-addr: ${NACOS_ADDR:localhost:8848}
            namespace: ${NACOS_NAMESPACE:prod}
            group-id: SENTINEL_FLOW
            data-id: ${spring.application.name}-flow-rules
            rule-type: flow
            data-type: json
        degrade:
          nacos:
            server-addr: ${NACOS_ADDR:localhost:8848}
            namespace: ${NACOS_NAMESPACE:prod}
            group-id: SENTINEL_DEGRADE
            data-id: ${spring.application.name}-degrade-rules
            rule-type: degrade
            data-type: json
        param-flow:
          nacos:
            server-addr: ${NACOS_ADDR:localhost:8848}
            namespace: ${NACOS_NAMESPACE:prod}
            group-id: SENTINEL_PARAM_FLOW
            data-id: ${spring.application.name}-param-flow-rules
            rule-type: param-flow
            data-type: json
```

**步骤二：Nacos 权限控制配置**

在 Nacos 的 `application.properties` 中开启鉴权：

```properties
nacos.core.auth.enabled=true
nacos.core.auth.system.type=nacos
nacos.core.auth.plugin.nacos.token.secret.key=VGhpc0lzQXRva2VuMzI3MTk2MzQ5MQ==
```

创建自定义角色和用户：

```bash
# 通过 Nacos API 创建只读角色
curl -X POST 'http://localhost:8848/nacos/v1/auth/roles' \
  -d 'username=developer&role=ROLE_SENTINEL_DEV'

# 通过 Nacos API 绑定权限（只允许访问 dev namespace）
curl -X POST 'http://localhost:8848/nacos/v1/auth/permissions' \
  -d "role=ROLE_SENTINEL_DEV&resource=dev:*:*&action=r"
```

权限矩阵：

| 角色 | 权限范围 | 操作 |
|------|---------|------|
| ROLE_ADMIN | 所有 namespace | CRUD |
| ROLE_SRE | prod namespace | R（只读） |
| ROLE_DEVELOPER | dev, test namespace | CRUD |
| ROLE_TESTER | test namespace | R |

**步骤三：多规则类型 JSON 格式汇总**

在 Nacos 中维护规则的团队需要对以下格式熟悉：

**流控规则（`order-service-flow-rules`）**：

```json
[
  {
    "resource": "createOrder",
    "limitApp": "default",
    "grade": 1,           // 1=QPS, 0=线程数
    "count": 100,
    "strategy": 0,        // 0=直接, 1=关联, 2=链路
    "controlBehavior": 0, // 0=快速失败, 1=预热, 2=排队等待
    "warmUpPeriodSec": 10,
    "maxQueueingTimeMs": 500,
    "clusterMode": false,
    "refResource": ""     // 关联/链路模式下的参考资源
  }
]
```

**熔断规则（`order-service-degrade-rules`）**：

```json
[
  {
    "resource": "queryStock",
    "grade": 0,           // 0=慢调用比例, 1=异常比例, 2=异常数
    "count": 200,         // grade=0:maxAllowedRt(ms); grade=1:异常比例; grade=2:异常数
    "timeWindow": 10,     // 熔断时长（秒）
    "minRequestAmount": 5,
    "statIntervalMs": 10000,
    "slowRatioThreshold": 0.5  // 仅 grade=0 时有效
  }
]
```

**热点参数规则（`order-service-param-flow-rules`）**：

```json
[
  {
    "resource": "goodsDetail",
    "paramIdx": 0,
    "count": 10,
    "grade": 1,
    "durationInSec": 1,
    "paramFlowItemList": [
      {
        "classType": "java.lang.String",
        "object": "SKU_HOT_001",
        "count": 50
      }
    ],
    "clusterMode": false
  }
]
```

**步骤四：配置变更监听与客户端热更新验证**

在订单服务中添加日志监听：

```java
@Component
public class RuleChangeListener {

    @PostConstruct
    public void init() {
        // 监听流控规则变化
        FlowRuleManager.getProperty().addListener(event -> {
            log.info("===== 流控规则热更新 =====");
            log.info("变更前规则数: {}", event.getOldValue().size());
            log.info("变更后规则数: {}", event.getNewValue().size());
            log.info("规则详情: {}", event.getNewValue());
        });

        // 监听熔断规则变化
        DegradeRuleManager.getProperty().addListener(event -> {
            log.info("===== 熔断规则热更新 =====");
            log.info("变更后规则数: {}", event.getNewValue().size());
        });
    }
}
```

验证热更新：在 Nacos 中修改 `order-service-flow-rules` 的阈值 → 观察订单服务日志 1-3 秒内出现"流控规则热更新"。

**步骤五：Nacos 配置误删恢复**

```bash
#!/bin/bash
# backup-nacos-rules.sh — 备份所有 Sentinel 规则

NACOS_ADDR="localhost:8848"
NAMESPACES=("dev" "test" "staging" "prod")
BACKUP_DIR="/backup/nacos-sentinel-rules/$(date +%Y%m%d)"

mkdir -p "$BACKUP_DIR"

for ns in "${NAMESPACES[@]}"; do
    # 获取 namespace 下所有配置
    curl -s "http://$NACOS_ADDR/nacos/v1/cs/configs?pageNo=1&pageSize=500&tenant=$ns" \
        | jq -r '.pageItems[] | "\(.dataId) \(.group)"' \
        | while read dataId group; do
            content=$(curl -s "http://$NACOS_ADDR/nacos/v1/cs/configs?dataId=$dataId&group=$group&tenant=$ns")
            echo "$content" > "$BACKUP_DIR/${ns}__${group}__${dataId}.json"
        done
done

echo "备份完成: $BACKUP_DIR"
```

建议加入 crontab 每小时执行一次。

**步骤六：变更通知与审计推送（钉钉/企业微信 Webhook）**

```java
@Component
public class NacosConfigChangeNotifier {

    private final RestTemplate restTemplate = new RestTemplate();
    
    @Value("${dingtalk.webhook.url:}")
    private String dingtalkWebhook;
    
    @Value("${spring.application.name}")
    private String appName;

    // 注册 Nacos DataSource 变更监听
    @PostConstruct
    public void init() {
        // 这个监听器应在 Nacos DataSource 初始化时注册
        // 监听 group=SENTINEL_FLOW, SENTINEL_DEGRADE 等所有规则类型
        FlowRuleManager.getProperty().addListener(event -> {
            List<FlowRule> oldRules = event.getOldValue();
            List<FlowRule> newRules = event.getNewValue();
            
            String diff = computeDiff(oldRules, newRules);
            if (!diff.isEmpty()) {
                sendDingtalkNotification(
                    String.format("⚠️ [%s] 流控规则变更\n变更内容:\n%s", appName, diff));
            }
        });
        
        DegradeRuleManager.getProperty().addListener(event -> {
            List<DegradeRule> oldRules = event.getOldValue();
            List<DegradeRule> newRules = event.getNewValue();
            String diff = computeDiff(oldRules, newRules);
            if (!diff.isEmpty()) {
                sendDingtalkNotification(
                    String.format("⚠️ [%s] 熔断规则变更\n变更内容:\n%s", appName, diff));
            }
        });
    }
    
    private String computeDiff(List<?> oldList, List<?> newList) {
        StringBuilder sb = new StringBuilder();
        // 规则新增
        for (Object rule : newList) {
            if (!oldList.contains(rule)) {
                sb.append("  + 新增: ").append(rule).append("\n");
            }
        }
        // 规则删除
        for (Object rule : oldList) {
            if (!newList.contains(rule)) {
                sb.append("  - 删除: ").append(rule).append("\n");
            }
        }
        return sb.toString();
    }
    
    private void sendDingtalkNotification(String message) {
        if (StringUtils.isEmpty(dingtalkWebhook)) return;
        Map<String, Object> body = Map.of(
            "msgtype", "text",
            "text", Map.of("content", message)
        );
        restTemplate.postForEntity(dingtalkWebhook, body, String.class);
    }
}
```

**步骤七：规则一致性校验（Prometheus + Blackbox Exporter）**

暴露规则版本端点：

```java
@RestController
@RequestMapping("/actuator")
public class SentinelRulesEndpoint {

    @GetMapping("/sentinel-rules")
    public Map<String, Object> getRulesStatus() {
        Map<String, Object> status = new LinkedHashMap<>();
        
        // 流控规则
        List<FlowRule> flowRules = FlowRuleManager.getRules();
        status.put("flow_rules_count", flowRules.size());
        status.put("flow_rules_hash", 
            Integer.toHexString(flowRules.hashCode()));
        
        // 熔断规则
        List<DegradeRule> degradeRules = DegradeRuleManager.getRules();
        status.put("degrade_rules_count", degradeRules.size());
        status.put("degrade_rules_hash",
            Integer.toHexString(degradeRules.hashCode()));
        
        // 规则加载来源
        status.put("rule_source", "nacos");
        status.put("last_load_time", System.currentTimeMillis());
        
        return status;
    }
}
```

Prometheus 告警规则检测不一致：

```yaml
# 检测任意 Pod 的规则 Hash 与多数 Pod 不一致
- alert: SentinelRuleInconsistency
  expr: |
    count(count by (pod) (sentinel_flow_rules_hash)) > 1
    and
    count(count by (flow_rules_hash) (sentinel_flow_rules_hash)) > 1
  for: 5m
  labels:
    severity: warning
  annotations:
    summary: "Sentinel 规则在 Pod 间不一致"
    description: "请检查 Nacos 配置是否下发完整"
```

**步骤八：批量规则操作（紧急降级场景）**

```bash
#!/bin/bash
# emergency-disable-flow-rules.sh — 紧急关闭所有流控规则（保留熔断）

NACOS_ADDR="nacos.default:8848"
NAMESPACE="prod"

# 遍历所有 SENTINEL_FLOW group 下的配置
curl -s "http://${NACOS_ADDR}/nacos/v1/cs/configs?pageNo=1&pageSize=100&tenant=${NAMESPACE}" \
  | jq -r '.pageItems[] | select(.group=="SENTINEL_FLOW") | "\(.dataId) \(.group)"' \
  | while read dataId group; do
      # 获取当前规则
      content=$(curl -s "http://${NACOS_ADDR}/nacos/v1/cs/configs?dataId=${dataId}&group=${group}&tenant=${NAMESPACE}")
      # 备份到本地
      echo "$content" > "/backup/pre_disable_${dataId}_$(date +%s).json"
      # 发布空规则（等于关闭所有流控）
      curl -X POST "http://${NACOS_ADDR}/nacos/v1/cs/configs" \
        -d "dataId=${dataId}&group=${group}&tenant=${NAMESPACE}&content=[]"
      echo "已关闭: ${group}/${dataId}"
    done
```

**步骤九：灰度规则发布流程**

```
生产规则灰度发布 SOP：

1. Dev 在 staging namespace 中创建待验证规则
   dataId: order-service-flow-rules
   group: SENTINEL_FLOW
   namespace: staging
2. 验证：staging 环境模拟生产流量 10 分钟
3. 审批：TAPD/Jira 工单审批通过
4. 备份：自动备份当前 prod 规则到 Git
5. 发布：将 staging 的规则内容复制到 prod namespace
6. 观察：告警 + Grafana 面板观察 5 分钟
7. 确认：无异常告警 → 灰度完成
8. 回滚：如异常，从 Git 还原 30 天内的任一版本
```

**踩坑记录**：

1. **namespace ID vs 名称**：Nacos namespace 有名称（如 `prod`）和 ID（如 `a1b2c3d4-...`）。Sentinel 配置中 `namespace` 字段填的是 ID，不是名称。
2. **group-id 含特殊字符**：group-id 中不要有空格、中文等特殊字符。
3. **多个服务共享同一 dataId**：如果两个服务配置了同一个 dataId（比如都用 `common-flow-rules`），规则会互相覆盖。务必每个服务独立 dataId。
4. **Nacos 配置发布延迟**：Nacos 客户端默认 1 秒长轮询，极端情况下 3-5 秒延迟。不要在一次变更后立刻做第二次变更——等 5 秒确认规则已生效。
5. **空规则 vs 删除配置**：在 Nacos 中"删除配置"和"发布空数组 `[]`"是不同的。删除配置后 Sentinel 客户端会保留上次拉取的规则；发布空数组会清空规则。紧急降级时应该发布空数组而非删除。

## 4 项目总结

### 4.1 生产实践清单

| 实践项 | 要求 | 优先级 | 实施难度 | 失效后果 |
|-------|------|-------|---------|---------|
| 命名规范 | `{service}-{type}-rules`（dataId），按类型分 group | P0 | 低 | 配置混乱，无法定位 |
| 环境隔离 | dev/test/staging/prod 四个 namespace | P0 | 低 | 改错环境导致生产故障 |
| 权限控制 | 按角色分配 namespace 读写权限 | P0 | 中 | 误操作无追溯 |
| 变更审计 | Nacos 操作日志 + 业务通知（企微/钉钉） | P1 | 中 | 故障无法溯源 |
| 配置备份 | 每小时自动备份所有 Sentinel 规则到 Git | P1 | 低 | 误删无法恢复 |
| 规则一致性校验 | Prometheus 探测所有 Pod 规则 Hash | P1 | 中 | 部分 Pod 规则不一致 |
| 灰度发布 | 新规则先在 staging 验证，再推 prod | P2 | 中 | 新规则直接导致生产故障 |
| 版本管理 | Nacos 配置历史版本保留 30 天 | P1 | 低 | 无法回滚 |
| 监控告警 | 规则变更后 5 分钟内监控流量是否符合预期 | P2 | 低 | 故障发现滞后 |
| Nacos 高可用 | Nacos 3 节点集群 + 监控 | P0 | 高 | 新启动客户端无法加载规则 |

### 4.2 Nacos 高可用场景矩阵

| 场景 | Nacos 状态 | Sentinel 客户端行为 | 影响 | 恢复方式 |
|------|-----------|-------------------|------|---------|
| 正常运行 | 所有节点正常 | 长轮询拉取配置 | 无影响 | N/A |
| 单节点宕机 | 2/3 节点正常 | 自动切换到其他节点 | 无影响 | 恢复故障节点 |
| Nacos 全宕 | 0 节点可用 | 使用本地缓存文件 | 新客户端无法启动 | Nacos 集群恢复后自动重连 |
| 网络分区 | 部分客户端不可达 | 本地缓存继续生效 | 规则无法热更新 | 网络恢复后自动同步 |
| 磁盘满 | Nacos 写入失败 | 内存中规则正常 | 无法发布新规则 | 清理磁盘，新规则自动重新发布 |

### 4.3 规则发布 Checklist

- [ ] 确认当前 namespace（dev/test/staging/prod）
- [ ] 备份当前规则到 Git（自动化）
- [ ] 变更内容已经过团队评审
- [ ] 灰度发布：先在 1 个 Pod 验证 5 分钟
- [ ] 观察 Grafana：拒绝率、RT 无异常
- [ ] 确认所有 Pod 规则一致性（Hash 校验）
- [ ] 记录变更原因和操作人

### 4.4 规则紧急降级流程（P0 SOP）

```
告警触发 → 值班 SRE 确认
               ↓
         判断：需要全量降级 or 部分降级？
               ↓
         【全量降级】发布空流控规则 → 观察 3 分钟
         【部分降级】关闭特定资源的流控 → 观察 3 分钟
               ↓
         确认流量恢复 → 记录事故报告
         确认未恢复 → 执行 Plan B（扩容/切流）
```

### 4.5 思考题

1. 如果 Nacos Server 完全不可用（宕机），已经在运行的 Sentinel 客户端规则会丢失吗？新启动的客户端呢？
2. 假设生产环境需要紧急降级——批量关闭 20 条流控规则（但保留熔断规则）。在 Nacos 中如何快速操作？恢复时如何保证数据一致性？

### 4.6 推广计划

- **开发团队**：遵守命名规范，在 Nacos 中维护规则而非代码中硬编码。
- **运维团队**：负责 namespace 创建、权限分配、配置备份和变更通知。
- **测试团队**：在 test namespace 中维护和验证规则配置的正确性。
