# 第28章：日志分析与 ELK Stack 实战

> 源码关联：src/http/modules/ngx_http_log_module.c、Filebeat、Logstash、Kibana
> 批次说明：第 3 批（精修版）

**关键词摘要**：结构化日志、ELK、慢请求分析、错误码分布、日志索引

---

## 1. 项目背景

线上慢请求时有发生，但团队每次都靠 grep 海量文本日志“手搓排查”，效率极低。改成结构化日志后，问题从“找不到”变成“几秒定位”。

进入这一阶段后，Nginx 不再只是“转发工具”，而是稳定性、性能与工程效率的共同支点。仅靠参数经验已经不足以支撑复杂系统，必须把“设计假设、实验数据、故障演练、回滚策略”绑定成一个闭环。
本章沿着“问题定义 -> 方案取舍 -> 实验验证 -> 上线守护”的路径展开，确保方案可复制、可复盘、可演进。

---

从知识路径上看，本章与上一章《监控体系与 Prometheus 集成》形成前后呼应：上一章解决的是“局部能力成立”，本章要进一步回答“在真实流量和复杂协作下如何稳定成立”。

## 2. 项目设计

**场景**：线上故障复盘会，白板上贴着上周某次慢请求的排查时间线——从用户投诉到定位根因花了 4 个小时。

---

**小胖**：（嗦着奶茶）查个慢请求我还要登服务器 `grep` 半天，简直是用筷子捞汤圆——费劲死了！能不能像美团外卖那样，搜个订单号一秒就出来？

**小白**：日志格式化了确实好查，但海量日志存哪里？怎么保证查得快？日志文件每天都在涨，磁盘写爆了怎么办？

**大师**：（在白板上画了一条流水线）你俩的问题合起来就是 ELK 要解决的核心。整个链路分四段：Nginx 产生结构化 JSON 日志 → Filebeat 轻量采集 → Logstash 解析加工 → Elasticsearch 存储索引 → Kibana 可视化。类比开火锅店：每桌点菜产生一张小票（JSON 日志），传菜员（Filebeat）及时把小票送到后厨传菜口（Logstash），厨师按菜品分类归档到冷柜（ES），客人加菜直接查平板菜单就行（Kibana）。

**技术映射**：Nginx 配置 `log_format json escape=json '{"time":"$time_iso8601","rid":"$request_id","uri":"$uri","status":$status,"rt":$request_time,"urt":"$upstream_response_time"}';` 输出 JSON。Filebeat 用 multiline 配置合并同一请求的多行日志，保证完整性。

---

**小胖**：那我不走 Logstash，直接把 JSON 日志怼到 ES 行不行？少一层不就少个故障点吗？

**小白**：直接送 ES 确实简化了链路，但原始日志里的字段类型、时区、IP 格式都不统一。还有敏感信息（手机号、身份证）不做脱敏就入库，合规风险很大。

**大师**：不错，小白说到点子上了。Logstash 就是中央厨房的切配台——进来的是整条鱼（raw JSON），出去的是片好的鱼片（标准化字段）。它负责：① `grok` 拆解非标准字段；② `date` 统一时区到 UTC；③ `mutate` 类型转换（字符串转 long）；④ `geoip` 解析客户端 IP 地理位置；⑤ `drop` 过滤健康检查等噪音请求。没有这层处理，ES 里存的是一锅乱炖，查起来慢还容易类型冲突。

**技术映射**：Logstash pipeline 示例：`input { beats { port => 5044 } }` → `filter { grok { match => { "rt" => "%{NUMBER:request_time:float}" } }; mutate { convert => { "status" => "integer" } } }` → `output { elasticsearch { hosts => ["localhost:9200"]; index => "nginx-%{+YYYY.MM.dd}" } }`。

---

**小白**：数据量上去之后 ES 查询明显变慢了，索引怎么规划才能既省钱又快？

**大师**：这就涉及索引生命周期管理（ILM）。好比超市生鲜区——今天到的鲜肉放冷柜（hot 节点，SSD 高性能），昨天的肉打折处理放到常温架（warm 节点，HDD 压缩存储），前天的直接下架销毁（delete）。按天滚动索引 `nginx-2026.04.28`，ILM 策略：hot 阶段存 3 天、50GB 滚动；warm 阶段存 7 天、强制 merge 降低 segment 数；30 天后自动删除。配合 Kibana 的 Dashboard 搞定慢请求 TOP10、错误码分布、热点 URI 排行榜。

**技术映射**：ILM policy JSON：`{"policy":{"phases":{"hot":{"actions":{"rollover":{"max_size":"50GB","max_age":"1d"}}}},"delete":{"min_age":"30d","actions":{"delete":{}}}}}`。查询用 Kibana Discover 配合 KQL 语法：`status:>=500 and request_time > 3`。

---

## 3. 项目实战

### 环境准备

- 系统：Ubuntu 22.04（或同等级 Linux 发行版）
- Nginx：1.31.0（建议保留 debug 能力）
- 工具：`curl`、`wrk`、`ss`、`jq`
- 按章补充：数据库/DNS/WebSocket/K8s/profiling 工具

```bash
sudo nginx -V
sudo nginx -t
```

### 步骤一：最小配置落地

**步骤目标**：构建可运行、可验证、可回滚的最小基线。

```nginx
log_format json escape=json '{"time":"$time_iso8601","rid":"$request_id","uri":"$uri","status":$status,"rt":$request_time,"urt":"$upstream_response_time","ua":"$http_user_agent"}';
access_log /var/log/nginx/access.json json;
error_log  /var/log/nginx/error.log warn;
```

**运行结果（预期）**：
- 配置检查通过；
- 关键路径连通；
- `error.log` 无持续异常。

**可能遇到的坑**：仅做功能验证，不做压力与异常验证，导致上线后才暴露边界问题。

### 步骤二：基线压测与指标采集

**步骤目标**：用数据而非主观感受评估改动价值。

```bash
jq -r '.uri + " " + (.rt|tostring)' /var/log/nginx/access.json | head
rg '"status":5' /var/log/nginx/access.json | head
```

**建议观测指标**：
- 吞吐：QPS/TPS
- 时延：P95/P99
- 可靠性：5xx、超时率、重试率
- 资源：CPU、内存、活动连接

**可能遇到的坑**：压测时间太短、场景太单一。建议至少覆盖平峰/高峰/故障三种情形。

### 步骤三：故障注入与回滚演练

**步骤目标**：确保故障发生时可快速止损。

```bash
# 应用变更
sudo nginx -t && sudo nginx -s reload
# 回滚示例
sudo cp /etc/nginx/nginx.conf.bak /etc/nginx/nginx.conf
sudo nginx -t && sudo nginx -s reload
```

**故障注入建议**：
1. 下线单个上游节点；
2. 注入后端延迟或错误码；
3. 叠加突发流量验证尾延迟。

**本章实战目标**：建立结构化日志链路并沉淀慢请求、错误码、热点 URI 分析模板。

### 完整代码清单

- 目录建议：`ops/nginx/ch28/`
- 文件建议：`nginx.conf`、`upstream.conf`、`bench.sh`、`fault_inject.sh`、`rollback.sh`
- 记录建议：`result.md`（参数、指标、结论、回滚点）

---

### 测试验证

```bash
# 基础连通性验证（示例）
curl -i http://127.0.0.1:8080/health
```

验证要点：
- 关键接口返回码符合预期（2xx/4xx/5xx与设计一致）；
- 关键日志字段完整（如 request_id、upstream 耗时、状态码）；
- 在小流量压测下无异常错误峰值。

## 4. 项目总结

### 优点与缺点

| 维度 | 方案优势 | 潜在代价 |
|---|---|---|
| 稳定性 | 有明确的演练与回滚机制 | 前期建设成本增加 |
| 性能 | 可持续、可量化优化 | 依赖监控与压测体系 |
| 团队协作 | 结论标准化、可传承 | 对执行纪律要求高 |

### 适用场景

1. 高峰明显、故障成本高的核心业务。
2. 网关承载治理能力的平台化系统。
3. 希望把经验运维升级为工程化运维的团队。

**不适用场景**：
- 低流量 demo 或短生命周期项目。
- 缺少基础观测能力、无法形成验证闭环的团队。

### 注意事项

1. 无基线不优化：先量化现状再改动。
2. 单变量优先：保障结论可归因。
3. 回滚先行：上线前先验证止损路径。

### 常见踩坑经验

- 只看平均值，不看尾延迟。
- 只做成功路径，不做故障演练。
- 变更记录缺失，导致后续无法复盘。

### 推广计划提示

- **开发团队**：先复用本章最小配置与脚本，按“单变量”方式做参数实验并沉淀变更记录。
- **测试团队**：优先补齐异常路径用例（超时、重试、限流、故障转移），并固化回归清单。
- **运维团队**：将监控阈值、告警策略与回滚脚本纳入发布流程，确保高峰期可快速止损。
- **协作顺序建议**：开发先完成方案基线 -> 测试做功能/压力/故障验证 -> 运维执行灰度与上线守护。

### 思考题

1. 本章最关键的“上线守护指标”应该是哪一个？为什么？
2. 如果资源有限，你会优先保留哪一类演练（性能/故障/回滚）？

---

在阅读顺序上，建议你先完成本章的最小实验与故障演练，再进入《容器化与 K8s Ingress 实践》。这样能把“配置会写”升级为“结论可验证、变更可回退”的工程能力。

> **下一章预告**：容器化与 K8s Ingress 实践
