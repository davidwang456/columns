# 第40章：【高级篇综合实战】从零构建企业级 LLM 应用平台

## 1. 项目背景

恒通金融——持牌金融机构，AI 应用有三条不能碰的红线：**数据不出内网**（所有模型推理、文档存储、日志必须在企业内网完成）、**所有操作可审计**（谁、什么时间、调了什么模型、输入了什么 Prompt、输出了什么——全链路记录，保存 5 年）、**AI 回复合规性 100%**（不会承诺收益、不会暗示内幕、所有风险类回复自动附带免责声明）。

CTO 发现了 Dify 的潜力，但原生版本不满足这三条红线。于是决定**深度定制**一套"恒通金融 AI 平台"——自研合规审查节点、自定义金融安全 Agent 策略、集成私有化 GPU 集群部署的 vLLM 金融大模型、基于 ELK 的全链路审计日志系统。

这个综合实战是全专栏的"终极考试"——你需要动用 39 章学到的全部知识，不是"用" Dify，而是"改" Dify。

## 2. 项目设计——剧本式交锋对话

**小胖**："大师，金融客户的合规要求太变态了——AI 回复里不能说'保证收益'、不能说'稳赚不赔'，如果提到年化收益率必须加'投资有风险'的免责声明。我试了 System Prompt 约束，但 GPT 还是偶尔违法——因为它太'热心'了。"

**大师**："System Prompt 是道德约束（软约束），不能替代合规审查（硬约束）。你需要一个**合规审查节点**——放在 LLM 节点和结束节点之间。在 AI 回复发送给用户之前，先经过合规审查节点的正则扫描。命中违规规则就拦截，自动改写或转人工。这是 100% 的拦截率——比 Prompt 约束可靠得多。"

**技术映射**：软约束（Prompt） vs 硬约束（代码审查）= 前者有概率失败，后者 100% 拦截。

**小白**："Agent 策略呢？金融场景的 Agent 不能随便调工具——得先检索合规知识库。"

**大师**："自定义一个 **'金融安全决策链'** Agent 策略——继承 `BaseAgentRunner`，覆盖 `run()` 方法：
1. **Step 1**: 强制检索合规知识库（无论用户问什么，先看合规要求）
2. **Step 2**: 风险预评估——如果用户的 query 涉及股票/基金/投资建议，标记为 HIGH RISK
3. **Step 3**: HIGH RISK 直接转人工，LOW/MEDIUM RISK 走正常的 FC/CoT Agent 流程
4. **Step 4**: Agent 输出最终回复前再跑合规审查"

**小胖**："审计日志呢？监管要求所有操作留痕 5 年。"

**大师**："用 **ELK**（Elasticsearch + Logstash + Kibana）。每次 LLM 调用后，把 `{user_id, trace_id, model, prompt_hash, response, tokens, cost, timestamp}` 写入 ES。Kibana 中可按任意字段组合检索——'3 月 15 日用户 12345 的所有 LLM 调用记录'——5 秒出结果。Prompt 存 hash 而非原文（隐私合规），但可通过 hash 回溯。"

## 3. 项目实战

### 架构总图

```
┌─────────────────────────────────────────────────────┐
│  Nginx Ingress (内网, SSL, 双向证书认证)              │
├─────────────────────────────────────────────────────┤
│  自定义 Dify API (3× Pod, 含自研节点)                │
│  ├── 合规审查节点 (ComplianceCheckNode)              │
│  ├── 风险评分节点 (RiskScoringNode)                   │
│  └── 审计日志中间件 (AuditMiddleware)                 │
├─────────────────────────────────────────────────────┤
│  Model Proxy (私有化 GPU 集群)                        │
│  ├── vLLM 金融大模型 (8× A100, 内网)                 │
│  └── OpenAI (经合规代理外发, 内容过滤)                │
├─────────────────────────────────────────────────────┤
│  PostgreSQL 主从 (加密传输)                          │
│  Redis Sentinel (3 哨兵 + 1 主 + 2 从)               │
│  MinIO (私有对象存储, 替代 S3)                        │
├─────────────────────────────────────────────────────┤
│  ELK (Elasticsearch + Logstash + Kibana)             │
│  - 审计日志: 全量 LLM 调用输入/输出 (保留 5 年)       │
│  - 合规事件: 拦截日志、改写日志                       │
│  - 操作日志: 谁创建/修改/删除了什么 App               │
└─────────────────────────────────────────────────────┘
```

### 自研合规审查节点

```python
# api/core/workflow/nodes/compliance_check/compliance_check_node.py
import re
from core.workflow.nodes.base import BaseNode, NodeRuntime

class ComplianceCheckNode(BaseNode):
    """金融合规审查节点——在 AI 回复发出前强制执行"""
    
    # 合规规则矩阵：正则 → 违规描述 → 严重等级
    COMPLIANCE_RULES = [
        (r'(保证|包|稳).{0,3}(收益|赚|盈利)', '禁止承诺收益', 'HIGH'),
        (r'(年化|收益).{0,5}\d{2,}%', '提及年化收益率需加风险提示', 'MEDIUM'),
        (r'内部消息|内幕|独家渠道|小道消息', '禁止暗示内幕信息', 'HIGH'),
        (r'建议.{0,10}(买入|卖出|做空|做多)', '禁止提供具体交易建议', 'HIGH'),
        (r'风险.{0,2}(为零|极小|没有)', '禁止淡化风险', 'MEDIUM'),
    ]
    
    @classmethod
    def get_default_config(cls) -> dict:
        return {
            "type": "compliance_check",
            "title": "合规审查",
            "description": "对 AI 生成的回复进行金融合规审查，拦截违规内容",
            "inputs": [
                {"name": "ai_response", "type": "string", "required": True, "label": "AI 回复内容"},
                {"name": "strict_mode", "type": "boolean", "default": True, "label": "严格模式"},
            ],
            "outputs": [
                {"name": "passed", "type": "boolean", "label": "是否通过审查"},
                {"name": "violations", "type": "array[object]", "label": "违规详情"},
                {"name": "action", "type": "string", "label": "处理动作: PASS/BLOCK/REWRITE"},
                {"name": "rewritten_response", "type": "string", "label": "改写后的回复（仅 action=REWRITE 时）"},
            ]
        }
    
    def _run(self, runtime: NodeRuntime) -> dict:
        ai_response = runtime.get_input('ai_response')
        strict_mode = runtime.get_input('strict_mode', True)
        
        violations = []
        for pattern, description, severity in self.COMPLIANCE_RULES:
            matches = re.findall(pattern, ai_response, re.IGNORECASE)
            if matches:
                violations.append({
                    'rule': description,
                    'severity': severity,
                    'matched_text': matches[:3],  # 只保留前 3 个匹配（别泄露太多原文）
                    'count': len(matches),
                })
        
        if not violations:
            return {'passed': True, 'violations': [], 'action': 'PASS', 'rewritten_response': ''}
        
        # 有 HIGH 级违规 → 强制拦截
        if any(v['severity'] == 'HIGH' for v in violations):
            return {
                'passed': False,
                'violations': violations,
                'action': 'BLOCK',
                'rewritten_response': f'[系统提示] 回复因合规原因被拦截。发现 {len(violations)} 条违规，包括: {", ".join(v["rule"] for v in violations)}。如有疑问请联系合规部门。'
            }
        
        # 只有 MEDIUM 违规 → 自动改写
        rewritten = ai_response
        for v in violations:
            rewritten += f"\n\n⚠️ 风险提示: {v['rule']}"
        
        return {
            'passed': True,
            'violations': violations,
            'action': 'REWRITE',
            'rewritten_response': rewritten
        }
```

### 自定义金融安全 Agent 策略

```python
# api/core/agent/strategy/finance_safety_agent.py
from core.agent.base_agent_runner import BaseAgentRunner

class FinanceSafetyAgentRunner(BaseAgentRunner):
    """
    金融安全决策链：
    1. 强制检索合规知识库
    2. 风险预评估
    3. HIGH RISK → 转人工
    4. LOW/MEDIUM → FC Agent
    5. 最终回复前合规审查
    """
    
    RISK_PATTERNS = [
        (r'(股票|基金|期货|外汇|加密货币)', '投资咨询', 'MEDIUM'),
        (r'(买入|卖出|满仓|清仓|加仓)', '交易建议', 'HIGH'),
        (r'(杠杆|配资|借贷.{0,5}投资)', '杠杆交易', 'HIGH'),
    ]
    
    def run(self, query: str) -> str:
        # Step 1: 检索合规知识库
        compliance_context = self.compliance_kb.search(query, top_k=3)
        
        # Step 2: 风险评估
        risk_level = 'LOW'
        risk_reasons = []
        for pattern, reason, level in self.RISK_PATTERNS:
            if re.search(pattern, query):
                risk_level = max(risk_level, level, key=lambda x: {'LOW':0,'MEDIUM':1,'HIGH':2}[x])
                risk_reasons.append(reason)
        
        # Step 3: HIGH RISK → 直接转人工，不调用 LLM
        if risk_level == 'HIGH':
            self.audit_logger.log_risk_event(query, risk_level, risk_reasons)
            return (
                "您的问题涉及高风险金融操作，为确保合规与您的权益，"
                "已转交专业顾问处理。我们的顾问将在 1 个工作日内与您联系。"
            )
        
        # Step 4: 正常 FC Agent 流程（注入合规上下文）
        prompt = self._build_prompt(query, compliance_context, risk_level)
        response = super().run_with_context(prompt)
        
        # Step 5: 合规审查
        compliance_result = self.compliance_checker.check(response)
        if not compliance_result.passed:
            return self._auto_correct(response, compliance_result.violations)
        
        return self._add_disclaimer(response)
```

### 审计日志

```python
# api/core/audit/audit_logger.py
import hashlib
from datetime import datetime
from elasticsearch import Elasticsearch

class AuditLogger:
    """
    全链路审计日志——写入 Elasticsearch，保留 5 年
    """
    def __init__(self):
        self.es = Elasticsearch(ELASTICSEARCH_URL)
        self.index_prefix = 'dify-audit'
    
    def log_model_call(self, user_id: str, trace_id: str, tenant_id: str,
                       model: str, prompt: str, response: str, tokens: int, cost: float):
        """★ 核心审计事件：LLM 调用全记录"""
        doc = {
            'timestamp': datetime.utcnow(),
            'event_type': 'model_call',
            'user_id': user_id,
            'tenant_id': tenant_id,
            'trace_id': trace_id,
            'model': model,
            'prompt_hash': hashlib.sha256(prompt.encode()).hexdigest(),  # 脱敏
            'prompt_length': len(prompt),
            'response': response,  # 原文留存（监管要求）
            'response_length': len(response),
            'tokens': tokens,
            'cost_usd': cost,
        }
        
        # 按年分索引（如 dify-audit-2026）
        index_name = f"{self.index_prefix}-{datetime.utcnow().year}"
        self.es.index(index=index_name, body=doc)
    
    def log_compliance_event(self, trace_id: str, result: dict):
        """合规审查事件"""
        self.es.index(index=f"{self.index_prefix}-{datetime.utcnow().year}", body={
            'timestamp': datetime.utcnow(),
            'event_type': 'compliance_check',
            'trace_id': trace_id,
            'passed': result['passed'],
            'violations_count': len(result['violations']),
            'action': result['action'],
        })
    
    def search(self, user_id: str, date_from: str, date_to: str, keyword: str = None):
        """全链路审计查询——5 秒出结果"""
        query = {
            'bool': {
                'must': [
                    {'term': {'user_id': user_id}},
                    {'range': {'timestamp': {'gte': date_from, 'lte': date_to}}},
                ]
            }
        }
        if keyword:
            query['bool']['must'].append({'match': {'response': keyword}})
        
        return self.es.search(index=f"{self.index_prefix}-*", body={'query': query})
```

### 验收标准

| 红线 | 目标 | 验证方式 |
|------|------|---------|
| 数据不出内网 | 100% 流量内网 | tcpdump 抓包验证无外网 IP |
| 操作可审计 | 100% 可追溯 | ES 查询 5 年内任意操作 < 5s |
| 合规审查覆盖率 | 100% | 所有 LLM 回复必经 ComplianceCheck 节点 |
| 回答准确率 | > 98% | 1000 条金融领域测试用例 |
| P99 延迟 | < 2s | Grafana 30 天数据 |
| 安全 | 通过第三方渗透测试 | 漏洞报告为零高危 |

## 4. 专栏终章——回顾与展望

从第 1 章"术语全景"到第 40 章"金融合规平台"——你完成了从 Dify 零基础到企业级深度定制者的 40 章修炼之旅。

**基础篇（1-16章）**：你学会了用 Dify 搭建 Chat、Workflow、Agent、RAG 四种应用模式，独立交付了一个智能客服系统。

**中级篇（17-31章）**：你深入了 Dify 的架构内核——DDD 分层、Gunicorn/Gevent 部署、Celery 异步、PostgreSQL 调优、Redis 五角色、Workflow 引擎源码、监控告警——能在企业级环境中设计高可用平台。

**高级篇（32-40章）**：你获得了源码级改造能力——请求链路追踪、图引擎修改、变量系统扩展、自定义节点开发、沙箱安全评估、百万级优化、多租户审计——能为特定行业深度定制 Dify。

**三个精进方向**：
1. **向社区贡献**——你的数据脱敏节点、合规审查节点、金融安全 Agent 策略都可以贡献给 Dify 社区，帮助更多用户
2. **向业务落地**——用你学到的架构能力为公司搭建 AI 中台，这是最有价值的实战
3. **向产品创新**——Dify 是开源平台，你可以基于它构建自己的 AI SaaS 产品

> **感谢阅读本专栏。全部 40 章的思考题参考答案见附录 D。**
