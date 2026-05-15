# 第39章：多机房与 SRE 落地：预案、演练和值班手册

## 1 项目背景

前 38 章我们掌握了从源码到生产的所有技术细节，但 Sentinel 的真正价值要在**组织级稳定性工程**中才能体现。某大型电商有 3 个机房（北京、上海、广州），每个机房独立部署 Sentinel Dashboard 和 Nacos。但在一次光纤中断事故中，广州机房的 Sentinel Dashboard 无法连接北京机房的 Nacos，导致规则无法热更新——广州机房的限流阈值停留在 3 天前的配置，在双十一流量冲击下完全失效。

这就是多机房场景的典型挑战：规则一致性如何保证？机房故障时如何快速切换规则？大促期间如何制定分级预案？

本章将从 SRE 的视角，给出 Sentinel 在多机房、大促、故障演练场景下的落地实践。

## 2 项目设计

**大师**："多机房场景下，Sentinel 的核心问题是'规则的一致性 vs 物理隔离'。每个机房可以有自己的 Nacos 集群，但规则基准应该保持一致。"

**小白**："那怎么实现？在每个机房各部署一套 Nacos，然后用脚本同步规则？"

**大师**："对。推荐的做法是：用 Git 仓库管理规则的'黄金版本'，通过 CI/CD 推送到各机房的 Nacos。机房的 Dashboard 只做监控查看，不做规则修改。规则修改统一走 Git PR → 审批 → CI/CD 推送。"

**小胖**："那如果 CI/CD Pipeline 本身挂了怎么办？比如代码合并后推规则的过程中，某个机房的 Nacos 网络不通？"

**大师**："需要做两件事：第一，CI/CD 推送规则时要有重试和超时机制，并且推送完成后验证规则是否真的在城市了。第二，Sentinel 客户端本地有规则缓存——即使 Nacos 暂时不可达，客户端仍会使用上一次同步到的本地缓存规则继续工作。"

**小白**："我看到 Sentinel Dashboard 有个'集群流控规则推送'的功能，多机房场景下这个功能能用吗？"

**大师**："不建议。Dashboard 的推送功能在设计上假设所有客户端都在同一个 Nacos 集群。多机房场景下如果直接使用 Dashboard 推送，会导致只有连接了特定 Nacos 集群的客户端收到更新，其他机房的客户端收不到。正确做法是让 Dashboard 只读、所有变更走 GitOps。"

**小胖**："大促预案的四级分级（GREEN/YELLOW/ORANGE/RED），具体触发条件怎么自动化判断？人工盯着 Grafana 不现实啊。"

**大师**："自动化的关键是把触发条件编码到 Prometheus 的告警规则中。比如："

```
- alert: SentinelYellowLevel
  expr: sum(rate(sentinel_pass_qps[1m])) > baseline_qps * 1.5
  for: 2m
  annotations:
    severity: yellow
    action: "执行YELLOW预案: 流控阈值上调至1.5x"
```

**大师**："Prometheus AlertManager 收到告警后，可以通过 webhook 调用预案执行脚本（如通过 Ansible 或 K8s Job 批量推送规则）。这样从监控触发到规则变更可以在 30 秒内自动完成。"

**小白**："那回滚呢？万一自动扩容后流量下来了，怎么自动恢复基线规则？"

**大师**："大促预案是'单向升级'机制——GREEN→YELLOW→ORANGE→RED。降级操作必须人工确认。但你可以在非大促期间设计'弹性阈值'——用 Sentinel 的集群流控 + Token Server 做全局自适应限流，根据系统负载自动调整阈值，无需人工干预。"

**小胖**："故障演练中的 Nacos 故障场景，Sentinel 客户端的本地缓存能撑多久？会不会缓存过期？"

**大师**："Sentinel 客户端的本地缓存不会过期——只要不重启，缓存一直有效。但如果客户端重启而 Nacos 不可用，新启动的实例拿不到规则,会使用 Sentinel 的默认资源规则（空规则，即无限流）。所以建议在 Sentinel 客户端启动时配置备用的本地文件规则源作兜底。"

## 3 项目实战

### 3.1 多机房规则同步架构

```
              ┌─────────────┐
              │  Git 仓库    │  (规则黄金版本)
              │  rules/      │
              └──────┬──────┘
                     │ CI/CD Pipeline
        ┌────────────┼────────────┐
        ▼            ▼            ▼
   ┌─────────┐ ┌─────────┐ ┌─────────┐
   │ 北京机房  │ │ 上海机房  │ │ 广州机房  │
   │ Nacos    │ │ Nacos    │ │ Nacos    │
   └────┬─────┘ └────┬─────┘ └────┬─────┘
        ▼            ▼            ▼
   ┌─────────┐ ┌─────────┐ ┌─────────┐
   │ Sentinel │ │ Sentinel │ │ Sentinel │
   │ Clients  │ │ Clients  │ │ Clients  │
   └─────────┘ └─────────┘ └─────────┘
```

### 3.2 大促 Sentinel 预案模板

```yaml
# sentinel-plan-double11.yaml
活动: 双十一
日期: 2024-11-11
负责人: 张三 (开发), 李四 (SRE)

预案等级:
  - 等级: GREEN (正常)
    触发条件: QPS < 日常峰值 1.5x
    Sentinel策略: 日常基线规则
    操作: 无需操作

  - 等级: YELLOW (警告)
    触发条件: QPS > 日常峰值 1.5x, 或 Block QPS > 5%
    Sentinel策略:
      - 流控阈值上调至基线的 1.5x
      - 非核心接口熔断规则放宽 (timeWindow 从 10s 降到 5s)
      - 开启预热模式 (warmUpPeriodSec=120)
    操作: SRE 值班人员执行预案脚本

  - 等级: ORANGE (严重)
    触发条件: 订单服务 RT > 500ms, 或 CPU > 70%
    Sentinel策略:
      - 核心接口流控收紧 (QPS = 基线 × 80%)
      - 所有非核心查询接口熔断
      - 系统保护 CPU 阈值降到 70%
    操作: SRE + 开发联合值守

  - 等级: RED (紧急)
    触发条件: 订单服务不可用, 或下游全熔断
    Sentinel策略:
      - 核心接口保留最低保障 QPS = 50
      - 所有写操作降级为异步队列
    操作: 启动全局降级开关, 通知业务方
```

### 3.3 故障演练脚本

```bash
#!/bin/bash
# chaos-sentinel-test.sh — Sentinel 故障演练

SCENARIO=$1

case $SCENARIO in
  "downstream-slow")
    echo "=== 注入下游延迟 2s ==="
    curl -X POST http://inventory-service:8082/chaos/slow?duration=120000
    echo "预期：10s 内订单服务触发熔断，15s 后订单恢复"
    ;;
  "hotspot-burst")
    echo "=== 注入热点商品突增流量 ==="
    hey -z 60s -c 500 http://order-service/seckill/order?skuId=HOT_BURST
    echo "预期：热点参数限流生效，拒绝率 > 50%"
    ;;
  "nacos-failure")
    echo "=== 模拟 Nacos 不可用 ==="
    kubectl scale deployment nacos --replicas=0
    echo "预期：客户端使用本地缓存规则，服务不受影响"
    sleep 120
    kubectl scale deployment nacos --replicas=1
    ;;
  *)
    echo "Usage: $0 {downstream-slow|hotspot-burst|nacos-failure}"
    ;;
esac
```

### 3.4 值班操作手册

| 告警 | 判断方法 | 操作步骤 | 回滚步骤 |
|------|---------|---------|---------|
| 某接口 Block QPS 突增 | Grafana 面板 → Block Rate | 1. 检查是否是正常流量增长 2. 如是，调整阈值 3. 如否，排查是否有异常流量 | 恢复原阈值 |
| 某资源持续熔断 | Grafana → Degrade State = OPEN > 5min | 1. 检查下游服务状态 2. 下游恢复后手动发探测请求 3. 如下游无法恢复，启动降级预案 | 恢复熔断规则 |
| 集群流控 Token Server 离线 | Dashboard 机器列表 | 1. 检查 Token Server Pod 状态 2. 确认 Client 已降级为单机 3. 恢复 Token Server | 恢复集群模式 |

### 3.5 SRE 值班 Dash 面板关键指标

```
┌──────────────────────────────────────────────┐
│  Sentinel SRE Dashboard                      │
├──────────────────────────────────────────────┤
│  Top 5 Block Resources (实时)                │
│  1. createOrder: 45 block/s                  │
│  2. queryStock: 12 block/s                   │
├──────────────────────────────────────────────┤
│  当前熔断资源: queryInventory (OPEN, 3m ago)  │
├──────────────────────────────────────────────┤
│  全局通过 QPS: 8,500   全局拒绝 QPS: 320     │
│  平均 RT: 45ms         P99 RT: 150ms         │
├──────────────────────────────────────────────┤
│  Token Server: 在线, 令牌发放率: 85%         │
│  Nacos 规则同步: 正常 (last: 3s ago)         │
└──────────────────────────────────────────────┘
```

**踩坑记录**：

1. **跨机房 Nacos 同步延迟**：如果使用 Nacos-Sync 做跨机房同步，延迟可能达到 5-10 秒。紧急规则变更应直接在目标机房操作。
2. **值班手册的维护**：手册内容会随系统演进过时，建议每季度做一次"盲演"（blind drill）来验证手册的有效性。

### 3.6 规则变更自动化——Prometheus告警触发预案

```yaml
# prometheus-rules-sentinel.yaml
groups:
  - name: sentinel_double11
    rules:
      - alert: SentinelQPSYellow
        expr: |
          sum(rate(sentinel_block_qps{resource="createOrder"}[1m]))
          / sum(rate(sentinel_pass_qps{resource="createOrder"}[1m])) > 0.05
        for: 2m
        labels:
          severity: yellow
        annotations:
          summary: "createOrder Block率超过5%"
          action: "kubectl apply -f /plans/yellow-flow-rules.yaml"

      - alert: SentinelCPUOrange
        expr: avg(container_cpu_usage_seconds_total{container="order-service"}) > 0.7
        for: 1m
        labels:
          severity: orange
        annotations:
          summary: "订单服务CPU超过70%"
          action: "ansible-playbook /plans/orange-degrade.yaml"
```

### 3.7 自动化预案执行器（Ansible Playbook）

```yaml
# orange-degrade.yaml
- name: 执行ORANGE级别预案
  hosts: nacos_servers
  tasks:
    - name: 推送收紧后的流控规则
      uri:
        url: "http://{{ nacos_host }}:8848/nacos/v1/cs/configs"
        method: POST
        body:
          dataId: "sentinel-flow-rules.yaml"
          group: "SENTINEL_GROUP"
          content: "{{ lookup('file', 'templates/orange-flow-rules.yaml') }}"
        body_format: form-urlencoded
      register: result
      until: result.status == 200
      retries: 3
      delay: 5

    - name: 验证规则生效
      uri:
        url: "http://{{ item }}:8719/getRules?type=flow"
      loop:
        - order-service-1:8719
        - order-service-2:8719
      register: verify
      failed_when: "'count' not in verify.json"
```

### 3.8 多机房规则一致性校验脚本

```bash
#!/bin/bash
# verify-rule-consistency.sh — 校验所有机房的 Sentinel 规则一致性

NACOS_ENDPOINTS=(
    "nacos-bj.internal:8848"
    "nacos-sh.internal:8848"
    "nacos-gz.internal:8848"
)

DATA_ID="sentinel-flow-rules.yaml"
GROUP="SENTINEL_GROUP"

declare -A RULE_CHECKSUMS

for endpoint in "${NACOS_ENDPOINTS[@]}"; do
    CONTENT=$(curl -s "http://${endpoint}/nacos/v1/cs/configs?dataId=${DATA_ID}&group=${GROUP}")
    CHECKSUM=$(echo "$CONTENT" | sha256sum | cut -d' ' -f1)
    RULE_CHECKSUMS[$endpoint]=$CHECKSUM
    echo "$endpoint: $CHECKSUM"
done

# 检查是否所有机房规则一致
FIRST_CHECKSUM=${RULE_CHECKSUMS[${NACOS_ENDPOINTS[0]}]}
for endpoint in "${NACOS_ENDPOINTS[@]}"; do
    if [ "${RULE_CHECKSUMS[$endpoint]}" != "$FIRST_CHECKSUM" ]; then
        echo "ALERT: $endpoint 规则与其他机房不一致!"
        exit 1
    fi
done
echo "OK: 所有机房规则一致"
```

### 3.9 大促规则一键回滚脚本

```bash
#!/bin/bash
# rollback-sentinel-rules.sh — 大促结束后回滚所有Sentinel规则到基线

BASELINE_TAG="baseline-2024-q4"
GIT_REPO="git@gitlab.internal:sre/sentinel-rules.git"
WORK_DIR="/tmp/sentinel-rules-rollback-$$"

echo "=== 克隆规则仓库 ==="
git clone --branch "$BASELINE_TAG" "$GIT_REPO" "$WORK_DIR"

echo "=== 推送基线规则到所有机房 ==="
for env in bj sh gz; do
    NACOS_URL="http://nacos-${env}.internal:8848"
    echo "推送至 ${env} 机房..."

    curl -X POST "${NACOS_URL}/nacos/v1/cs/configs" \
         -d "dataId=sentinel-flow-rules.yaml" \
         -d "group=SENTINEL_GROUP" \
         -d "content=$(cat ${WORK_DIR}/flow-rules.yaml)"

    echo "${env} 机房规则回滚完成"
done

echo "=== 验证回滚结果 ==="
sleep 5  # 等待规则热更新生效
bash ./verify-rule-consistency.sh

rm -rf "$WORK_DIR"
echo "=== 回滚操作完成 ==="
```

## 4 项目总结

### 4.1 SRE 落地清单

- [ ] 规则变更审批流（Git PR + CI/CD）
- [ ] 大促分级预案（GREEN/YELLOW/ORANGE/RED）
- [ ] 季度故障演练（下游慢、热点突增、Nacos 故障）
- [ ] 值班操作手册（更新频率：每季度一次）
- [ ] 监控告警（Block 率、熔断时长、Token Server 存活）
- [ ] 多机房规则一致性自动校验（CronJob 每小时执行）
- [ ] 规则变更审计日志（谁、什么时间、改了什么规则、推送到了哪些机房）
- [ ] 大促预案自动化执行（Prometheus AlertManager → Webhook → Ansible）
- [ ] 客户端本地兜底规则文件（Nacos 不可用 + 实例重启场景）

### 4.2 四级预案响应时间目标

| 等级 | 发现到响应 | 响应方式 | 执行到生效 | 回滚方式 |
|------|-----------|---------|-----------|---------|
| GREEN | N/A | 无需操作 | N/A | N/A |
| YELLOW | < 2 分钟 | 自动（AlertManager Webhook） | < 30 秒 | 人工确认 |
| ORANGE | < 1 分钟 | 自动 + 人工并行 | < 15 秒 | 人工确认 |
| RED | < 30 秒 | 人工值守 + 一键执行 | < 5 秒 | 仅限总指挥 |

### 4.3 故障演练场景矩阵

| 演练场景 | 频率 | 预期结果 | 验证指标 |
|---------|------|---------|---------|
| Nacos 单机房宕机 | 每月 | 客户端平滑切换到本地缓存 | Block 率不突增 |
| Token Server 宕机 | 每季 | Client 30s 内降级为单机限流 | 单机限流 QPS 稳定 |
| 下游服务全熔断 | 每季 | 熔断器 15s 内触发 Open | 上游服务 RT < 100ms |
| 光纤中断（跨机房） | 每半年 | 机房间规则独立生效 | 各机房正常限流 |
| 大促全链路压测 | 大促前 2 周 | 验证预案各等级触发逻辑 | 无意外 Block |

### 4.4 思考题

1. 在多机房架构中，如果北京机房的 Nacos 宕机，上海机房的 Sentinel 客户端应该切换到自己机房的 Nacos 还是使用本地缓存的规则？切换策略如何设计？
2. 如果一年有 10 次大促活动，每次都需要修改 Sentinel 规则。如何设计一套"大促模板"系统来减少人工操作？

### 4.5 推广计划

- **SRE 团队**：基于本章模板，制定团队的 Sentinel 大促预案和值班手册。
- **运维团队**：部署跨机房的 Nacos 规则同步，并定期验证同步延迟。
- **测试团队**：将故障演练脚本纳入季度演习流程。
