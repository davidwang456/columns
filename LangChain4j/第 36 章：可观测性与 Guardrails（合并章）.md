# 第 36 章：可观测性与 Guardrails（合并章）

## 1. 项目背景

### 业务场景（拟真）

**可观测性** 回答 **多慢、多贵、错在哪**：`langchain4j-micrometer-metrics`、`langchain4j-observation` 把 **模型调用、工具、向量检索** 纳入指标与追踪。**Guardrails** 回答 **该不该回**：输入/输出策略、正则拦截、二次审核——`langchain4j-guardrails`。

### 痛点放大

无指标 → **账单事故** 失明；无护栏 → **合规事故** 失守。本章合并原 **第 38～39 章** 主题，便于 **运维与风控** 同页落地。

## 2. 项目设计：小胖、小白与大师的对话

**小胖**：Micrometer 和 OTel 会打架吗？

**小白**：要采哪些指标？**护栏放调用前还是后**？**误杀**？

**大师**：**单一真理源** 做 **trace id** 串联。指标：**P95/P99、token、工具率、空检索率、429/5xx、解析失败率**（第 17 章）。护栏通常 **入站清洗 + 出站审查**；高风险 **人工**。**误杀** → **申诉 + 灰度**。**技术映射**：**结构化日志字段级 Redaction**。

**小胖**：和网关风控区别？

**小白**：**成本**？**护栏日志** 会不会更泄露？

**大师**：网关防 **传统攻击**；护栏防 **提示注入/敏感生成**。**二次模型审核** 最贵；**轻量分类器 + 抽样**。**禁止 stdout 全文 prompt**。**技术映射**：**指标基数** 勿按用户 id 打 tag。

---

## 3. 项目实战

### 环境准备

- Spring [`MyChatModelListener.java`](../../langchain4j-examples/spring-boot-example/src/main/java/dev/langchain4j/example/aiservice/MyChatModelListener.java) 思路；`langchain4j-guardrails` 文档。

### 分步任务

列出 **Micrometer/OTel tags**：`model`, `route`, `tenant`, `approxTokenTotal`。

**草拟策略表**：

| 阶段 | 策略 | 失败动作 |
|------|------|----------|
| 入站 | 邮箱/身份证正则 | 422 + 提示 |
| 出站 | 违禁词 | 替换安全文案 |

**延伸**：**账单突增** → Listener 补 **modelId、tenant** → 发现 **批量重放**；**身份证误杀教程** → **规则版本化 + 灰度 + 申诉**。

### 测试验证

- **对抗样本集** 定期跑；**指标告警** 与 **发布** 联动。

### 完整代码清单

模块：`langchain4j-micrometer-metrics`、`langchain4j-observation`、`langchain4j-guardrails`。

---

## 4. 项目总结

### 优点与缺点（与同类做法对比）

| 维度 | Listener + Guardrails | 仅 HTTP 日志 | 仅网关 |
|------|----------------------|--------------|--------|
| 可观测 | 全链路 LLM | 缺 token | 缺语义 |
| 安全 | 语义层 | 无 | 传统 |
| 典型缺点 | 需调参 | 失明 | 漏注入 |

### 适用场景

- 所有 **对公 Bot**；**金融/未成年人** 相关。

### 不适用场景

- **内网无敏感** 且 **无计费**——可极简。

### 注意事项

- **审计**：谁改了哪条规则；**跨国**法域。

### 常见踩坑经验（生产向根因）

1. **指标基数爆炸**。  
2. **护栏日志** 泄露更多隐私。  
3. **只看黄金指标** 忽略 **解析失败**。

### 进阶思考题

1. **Grafana dashboard as code** 与 **发布** 的联动？  
2. **高敏渠道** 分级规则如何 **灰度**？

### 推广计划提示（多部门）

| 角色 | 建议阅读顺序 | 协作要点 |
|------|----------------|----------|
| **开发** | 第 5、34 章 → 本章 | **Listener 脱敏** |
| **运维** | ONCALL Runbook | **降级开关** |
| **风控** | 护栏规则 | **产品评审** |

---

### 本期给测试 / 运维的检查清单

**测试**：**对抗样本集**定期跑；**指标告警**与 **发布**联动验证。  
**运维**：Grafana **dashboard as code**；**ONCALL Runbook** 含 **降级开关**。

### 附录

模块：`langchain4j-micrometer-metrics`、`langchain4j-observation`、`langchain4j-guardrails`。类：`MyChatModelListener`（示例）、Micrometer 文档。
