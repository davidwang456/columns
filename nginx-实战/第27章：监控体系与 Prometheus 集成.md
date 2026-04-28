# 第27章：监控体系与 Prometheus 集成

> 源码关联：src/http/modules/ngx_http_stub_status_module.c、nginx-prometheus-exporter
> 批次说明：第 3 批（精修版）

**关键词摘要**：stub_status、Prometheus、RED 指标、告警规则、可观测闭环

---

## 1. 项目背景

一次晚高峰故障里，网关 5xx 飙升但值班同学只看到 CPU 正常，定位延迟了 40 分钟。根因不是没有监控，而是指标碎片化，缺少以 RED 为核心的统一观测体系。

进入这一阶段后，Nginx 不再只是“转发工具”，而是稳定性、性能与工程效率的共同支点。仅靠参数经验已经不足以支撑复杂系统，必须把“设计假设、实验数据、故障演练、回滚策略”绑定成一个闭环。
本章沿着“问题定义 -> 方案取舍 -> 实验验证 -> 上线守护”的路径展开，确保方案可复制、可复盘、可演进。

---

从知识路径上看，本章与上一章《流量镜像与 A/B 测试》形成前后呼应：上一章解决的是“局部能力成立”，本章要进一步回答“在真实流量和复杂协作下如何稳定成立”。

## 2. 项目设计

**场景**：性能压测后复盘，三人盯着 Grafana 大屏上红红绿绿的曲线，白板上写满了指标。

---

**小胖**：（啃着苹果）大师，这监控面板花花绿绿的，跟火锅店排队屏似的——红线是等位人数超预期，绿线是上菜快。是不是我们也照着搞一套，看哪个接口慢了就加机器？

**小白**：上次事故 CPU、内存全绿，但接口 P99 飙到了 5 秒。光看连接数根本发现不了问题。到底该盯哪些指标才能不遗漏？

**大师**：（在白板上写下 R、E、D 三个字母）小胖你的直觉方向没错，但监控不能眉毛胡子一把抓。监控体系的核心是 RED 三原则——Rate（流量）、Errors（错误）、Duration（延迟）。你把它理解成食堂打饭：你得同时知道多少人排队（Rate）、有没有人打翻餐盘洒了汤（Errors）、每个人从排队到吃上饭花了多久（Duration）。只看一个指标就像只看排队人数，后厨已经着火了你都不知道。

**技术映射**：RED 方法论对应 Prometheus 的核心指标：Rate 用 `nginx_http_requests_total` 速率，Errors 用 `status=~"5.."` 错误计数，Duration 用 `request_time` 的 histogram 分位值。Prometheus 通过 pull 模型定期 scrape exporter 暴露的 `/metrics` 端点。

---

**小胖**：那我直接 curl 一下 `stub_status` 不也有连接数吗？为啥非要搞个 exporter？

**小白**：单机咋看都行，十个节点你怎么挨个 curl？而且历史趋势怎么回溯？告警阈值怎么设？

**大师**：问得好。`stub_status` 好比每家小饭馆自己手写的每日流水账——自己看看还行，总部要汇总 50 家店的数据就得疯。nginx-prometheus-exporter 就是统一收银系统，把每家店（每台 Nginx）的流水自动翻译成标准格式（Prometheus metrics），总部的数据中心（Prometheus Server）定时来拉。它的核心指标包括：`nginx_connections_active`、`nginx_connections_reading`、`nginx_connections_writing`、`nginx_connections_waiting`。

**技术映射**：架构链路：Nginx `stub_status` → nginx-prometheus-exporter（`:9113/metrics`） → Prometheus Server（`scrape_configs`） → Grafana dashboard（预置 11333-1.json）。Exporter 用 Go 编写，每秒抓取一次 stub_status 并缓存，开销极小。

---

**小白**：指标都进来了，但什么时候该告警？上次我们设了个 CPU > 80% 告警，结果天天报，大家都麻木了。

**大师**：告警不是"超过阈值就报"，而是"业务受损了才报"。好比冰箱里的菜——你不会每天定时检查一遍扔不扔，而是等它发出臭味了再处理。正确的做法：先用一周时间采集基线数据，P99 延迟的 2 倍作为告警线，错误率超过 5% 且持续 5 分钟以上才触发。同时配合 Alertmanager 做告警分级：P0（核心接口挂了）打电话，P1（延迟劣化）发钉钉，P2（资源水位高）发邮件。

**技术映射**：Prometheus Rule 示例：`rate(nginx_http_requests_total{status=~"5.."}[5m]) / rate(nginx_http_requests_total[5m]) > 0.05`。Alertmanager 配置 route：`group_by: ['alertname']` + `repeat_interval: 4h` 防止告警风暴。

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
server {
    listen 8080;
    location /nginx_status {
        stub_status;
        allow 127.0.0.1;
        allow 10.0.0.0/8;
        deny all;
    }
}
# exporter 示例
# ./nginx-prometheus-exporter -nginx.scrape-uri http://127.0.0.1:8080/nginx_status
```

**运行结果（预期）**：
- 配置检查通过；
- 关键路径连通；
- `error.log` 无持续异常。

**可能遇到的坑**：仅做功能验证，不做压力与异常验证，导致上线后才暴露边界问题。

### 步骤二：基线压测与指标采集

**步骤目标**：用数据而非主观感受评估改动价值。

```bash
curl -s http://127.0.0.1:8080/nginx_status
curl -s http://127.0.0.1:9113/metrics | rg nginx_connections
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

**本章实战目标**：搭建 Nginx 指标采集、看板和告警闭环，缩短故障发现到定位时间。

### 完整代码清单

- 目录建议：`ops/nginx/ch27/`
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

在阅读顺序上，建议你先完成本章的最小实验与故障演练，再进入《日志分析与 ELK Stack 实战》。这样能把“配置会写”升级为“结论可验证、变更可回退”的工程能力。

> **下一章预告**：日志分析与 ELK Stack 实战
