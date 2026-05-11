# 第31章：【中级篇综合实战】构建高可用 LLM 应用平台

## 1. 项目背景

公司决定将 Dify 作为统一的 AI 能力中台，支撑 50+ 业务线的 AI 需求——客服、营销文案、数据分析、内部知识管理、代码助手。预计日均请求量 500 万次，峰值 QPS 需要达到 2000。CTO 定了硬指标：**多租户严格隔离**（市场部的 Agent 跑飞了不能影响研发部）、**模型统一治理**（15 个 Provider、100+ API Key 的配额和成本控制）、**知识库分级管理**（核心知识库 vs 非核心知识库，检索精度和更新频率区别对待）、**99.9% 可用性**（单月停机 < 43 分钟）、**P99 延迟 < 3s**、**成本按租户可追溯**（每月给 CFO 出成本报表）。

这不是"能跑就行"——这是"企业级平台"的要求。这个综合实战需要你调用中级篇学到的全部知识：DDD 架构理解（第 17 章）保证代码修改在正确的层、Gunicorn/Celery 调优（第 18-19 章）保证高并发、PostgreSQL 索引与分区（第 20 章）保证数据库不拖后腿、Redis 五角色（第 21 章）保证分布式状态一致、Workflow 引擎原理（第 22 章）保证流程编排不出错、监控告警（第 28 章）保证问题第一时间发现、K8s 部署（第 30 章）保证自动扩缩容。

## 2. 项目设计——剧本式交锋对话

**小胖**：（看着 50 条业务线的需求清单，眉头紧锁）"大师！50 条业务线共用一套 Dify。万一市场部的 Agent 陷入循环，一条消息烧掉 5 万 Token——研发部的 AI 代码助手全被拖垮了。怎么隔离才能互不影响？"

**大师**："三层隔离是底线。**第一层：租户级数据隔离**——每个业务线独立的 Workspace（Tenant）。Dify 的所有 SQL 查询自动注入 `WHERE tenant_id = current_tenant_id`，这条是 Dify 自带的，不需要你额外开发。但你要确保每个业务线的 API Key 也是独立的——不能多个 Workspace 共享一把 Key。**第二层：队列级任务隔离**——Celery 中每个租户有独立的任务队列（`tenant_self_indexing_task_queue:{tenant_id}`）。市场部上传 100 个文档做索引，这些任务进入市场部的队列，研发部 Worker 照常处理研发部的任务，两不相干。**第三层：配额级资源隔离（需要你自己实现）**——在 ModelManager 中增加租户每天 Token 配额检查。市场部每天 50 万 Token 上限，到了就拒绝并告警。三层加起来，即使一个租户'发疯'，也不会波及其他租户。"

**技术映射**：多租户隔离三层 = 数据层（SQL tenant_id）+ 队列层（Celery 独立 List）+ 配额层（Redis 日计数器），层数越多隔离越好，但运维复杂度也越高。

**小白**："99.9% 可用性怎么算？单月只能停机 43 分钟。靠 K8s 滚动更新能做到吗？"

**大师**："滚动更新 + `maxUnavailable=0` 能做到**更新期间**零停机（始终至少有一个老版本 Pod 在服务，新 Pod ready 后再切流量）。但可用性不止看更新——还要看数据库故障、Redis 故障、网络分区、甚至机房断电。我的建议：

1. **数据库**：用 Patroni 做主从自动故障转移。PostgreSQL 主库挂了，从库在 30 秒内自动升级为主库。
2. **Redis**：用 Sentinel 哨兵模式。3 个哨兵节点监控 Redis 主从，主挂了自动选举新主。
3. **K8s Pod 打散**：配置 `podAntiAffinity`，同一个 Deployment 的 Pod 不能调度到同一台 Node 上。更进一步——打散到不同 AZ。"

**技术映射**：高可用 = 冗余 + 自动故障转移 + 打散部署。单点故障不导致全局不可用。

**小胖**："成本按租户追溯呢？财务要求每月 5 号前出成本报表，按业务线分。"

**大师**："Dify 原生的 `messages` 表只有 `message_tokens` 字段（存 Token 数），但**没有 `cost_usd` 字段**——你需要自己加。我建议新建一张 `model_usage_logs` 表：`{id, tenant_id, model_name, prompt_tokens, completion_tokens, cost_usd, created_at}`。每次 LLM 调用后插入一条。月底一条 SQL 按租户汇总：`SELECT tenant_id, SUM(cost_usd) FROM model_usage_logs WHERE created_at BETWEEN '2026-05-01' AND '2026-05-31' GROUP BY tenant_id`。注意 Token→成本的换算公式——不同模型不同价格，GPT-4 和 GPT-3.5 的单价差 20 倍。"

## 3. 项目实战

### 架构总图

```
┌─────────────────────────────────────────────────────┐
│  Nginx Ingress (K8s, 多 AZ, SSL 终止)                │
├─────────────────────────────────────────────────────┤
│  3× API Pod (HPA 3-15, 跨 AZ, antiaffinity)         │
│  2× Worker Pod (独立 HPA 2-8)                        │
│  2× Web Pod                                          │
├─────────────────────────────────────────────────────┤
│  PostgreSQL (Patroni 主从, 1 主 + 2 从 + etcd)       │
│  Redis Sentinel (3 哨兵 + 1 主 + 2 从)               │
│  Weaviate 集群 (3 节点)                               │
│  S3 对象存储 (多 AZ 自动复制)                         │
├─────────────────────────────────────────────────────┤
│  模型治理层                                           │
│  - 15 Provider → 100+ Key → 按租户 Quota             │
│  - 成本表 model_usage_logs → 月度报表                 │
│  - 智能路由：简单任务用 GPT-3.5，复杂任务用 GPT-4     │
├─────────────────────────────────────────────────────┤
│  可观测性                                             │
│  - Grafana 统一大盘 (QPS/P99/5xx)                    │
│  - Langfuse (每次 LLM 调用的 Trace)                   │
│  - Sentry (代码异常实时告警)                          │
│  - ELK (审计日志, 全链路检索)                         │
└─────────────────────────────────────────────────────┘
```

### 关键实现

**配额管理器**：

```python
# api/core/quota_manager.py
import redis
from datetime import date

class TenantQuotaManager:
    def __init__(self):
        self.redis = redis.Redis()
    
    def check_and_consume(self, tenant_id: str, model: str, tokens: int, cost: float) -> bool:
        """检查配额并消耗。返回 True 表示配额内，False 表示超额"""
        today_key = f"tenant_quota:{tenant_id}:{date.today()}"
        daily_used_tokens = int(self.redis.get(f"{today_key}:tokens") or 0)
        daily_used_cost = float(self.redis.get(f"{today_key}:cost") or 0)
        
        daily_limit = self._get_tenant_quota(tenant_id)
        
        # 检查 Token 和费用两个维度
        if daily_used_tokens + tokens > daily_limit['max_tokens']:
            raise QuotaExceededError(f"Token 配额超额: {daily_used_tokens+tokens}/{daily_limit['max_tokens']}")
        if daily_used_cost + cost > daily_limit['max_cost']:
            raise QuotaExceededError(f"费用配额超额: ${daily_used_cost+cost:.2f}/${daily_limit['max_cost']:.2f}")
        
        # 原子递增（Redis INCRBY 是原子的）
        self.redis.incrby(f"{today_key}:tokens", tokens)
        self.redis.incrbyfloat(f"{today_key}:cost", cost)
        self.redis.expire(f"{today_key}:tokens", 86400)  # 次日自动清零
        
        return True
    
    def _get_tenant_quota(self, tenant_id: str) -> dict:
        """从数据库或配置中获取租户配额"""
        tenant = Tenant.query.get(tenant_id)
        return {
            'max_tokens': tenant.quota_tokens or 500000,  # 默认 50万 Token/天
            'max_cost': tenant.quota_cost or 5.0,          # 默认 $5/天
        }
```

**成本记录表**：

```sql
CREATE TABLE model_usage_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL,
    model_name VARCHAR(255) NOT NULL,
    prompt_tokens INT DEFAULT 0,
    completion_tokens INT DEFAULT 0,
    total_tokens INT GENERATED ALWAYS AS (prompt_tokens + completion_tokens) STORED,
    cost_usd DECIMAL(10, 6) DEFAULT 0,
    created_at TIMESTAMP DEFAULT NOW(),
    INDEX idx_tenant_created (tenant_id, created_at DESC),
    INDEX idx_model_created (model_name, created_at DESC)
);

-- 月度成本报表
SELECT 
    t.name AS tenant_name,
    DATE_TRUNC('month', l.created_at) AS month,
    l.model_name,
    SUM(l.total_tokens) AS total_tokens,
    SUM(l.cost_usd) AS total_cost
FROM model_usage_logs l
JOIN tenants t ON t.id = l.tenant_id
WHERE l.created_at >= '2026-05-01'
GROUP BY t.name, DATE_TRUNC('month', l.created_at), l.model_name
ORDER BY total_cost DESC;
```

### 验收标准

| 指标 | 目标 | 验证方式 |
|------|------|---------|
| 可用性 | 99.9%（单月停机 <43min） | Grafana + K8s 滚动更新时 /health 持续 200 |
| P99 延迟 | < 3s | Grafana 分位数面板，取 30 天数据 |
| 租户隔离 | 100%（无跨租户数据泄露） | 渗透测试：租户 A 的 API Key 无法访问租户 B 的数据 |
| 成本追溯 | 日/周/月可查 | `model_usage_logs` 表 SQL 查询 |
| 自动扩缩容 | QPS ×2 时 Pod 自动 +1 | K6 压测验证 HPA 触发 |

## 4. 项目总结

中级篇到此结束。回顾 31 章的旅程——你从"一个 Chat App"到"深入架构内核"，现在你已经具备了在企业级环境中设计、部署、调优 Dify 平台的全部能力。下一个阶段——高级篇——将进入源码级改造和自定义扩展的世界。

**思考题**：如果某个业务线流量暴增 10 倍，HPA 扩容需要 2 分钟。这 2 分钟内的额外请求会超时吗？如何设计"预热扩容"策略？（提示：定时 HPA 调度 + 提前扩）

> **参考答案**：见附录 D
