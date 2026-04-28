# 第37章：Filter 链机制与数据流处理源码

> 源码关联：src/http/ngx_http_core_module.c、src/http/ngx_http_write_filter_module.c
> 批次说明：第 5 批（精修版）

**关键词摘要**：filter chain、header/body filter、响应改写、Content-Length、注入埋点

---

## 1. 项目背景

前端监控要求所有 HTML 页面统一注入埋点脚本，业务侧不愿逐个改造。网关层 body filter 成为最小成本方案。

进入这一阶段后，Nginx 不再只是“转发工具”，而是稳定性、性能与工程效率的共同支点。仅靠参数经验已经不足以支撑复杂系统，必须把“设计假设、实验数据、故障演练、回滚策略”绑定成一个闭环。
本章沿着“问题定义 -> 方案取舍 -> 实验验证 -> 上线守护”的路径展开，确保方案可复制、可复盘、可演进。

---

从知识路径上看，本章与上一章《Upstream 源码——连接池与负载均衡》形成前后呼应：上一章解决的是“局部能力成立”，本章要进一步回答“在真实流量和复杂协作下如何稳定成立”。

## 2. 项目设计

**场景**：前端团队要求在网关层给所有 HTML 响应注入一段埋点 JS，但不改业务代码。小白提议用 body filter 实现，三人开始评审方案。

---

**小胖**：（啃着苹果）这不就跟流水线上的贴标机一样嘛——不管什么牌子的方便面从线上过，自动贴一张"扫码抽奖"的贴纸上去。

**小白**：问题是 body filter 可能会破坏 Content-Length。如果我们在响应体里加了 200 字节脚本，原来 Content-Length: 1024 就错了。上游返回时写了固定长度，网关一改长度就 mismatch。

**大师**：先理解 filter 链的结构：响应从 content handler 出来后，依次经过 header_filter 链和 body_filter 链。header_filter 链负责修改响应头（比如设置 Content-Length、Cache-Control），body_filter 链负责逐块处理响应体。整个链是一个**单向链表**，调用顺序由 ngx_http_top_header_filter 和 ngx_http_top_body_filter 这两个全局指针串联。

**小胖**：那跟食堂的餐盘传送带一模一样——盘子经过洗盘区（header_filter）、装菜区（body_filter）、加热区（another filter）、盖章区（yet another filter），每个区都能加东西。

---

**小白**：自定义 filter 怎么插到这个链里？

**大师**：在模块的 postconfiguration 钩子里调用 ngx_http_add_*_filter。比如注册 body filter：先把原来的 ngx_http_next_body_filter 保存下来，然后把 ngx_http_top_body_filter 替换成你的函数入口。在你的函数里处理好响应体后调用 next 继续传递。这就是**责任链模式**——每个 filter 只关注自己的逻辑，不关心上下游是谁。

**小白**：那 Content-Length 不一致的问题到底怎么解决？

**大师**：两种策略。第一，在 header_filter 里设 Content-Length 为 -1，让 Nginx 用 chunked 传输编码——相当于告诉客户端"我边做边发，你别数长度"。第二，在 body_filter 里用 ngx_http_set_ctx 保存累积添加的字节数，在最后一个 buf 标记为 last_buf 时，通过 header_filter 修正 Content-Length 的最终值。

**小胖**：哦，就跟快递包裹上的重量标签一样——要么先不写重量（chunked），最后一并称；要么先写个估算值，打包完再改。

**技术映射**：**ngx_http_output_filter** 触发 filter 链，**header_filter** 链处理响应头，**body_filter** 链以 ngx_buf_t 为单位逐块处理；自定义 filter 通过**责任链替换**方式插入；需注意 **Content-Length vs chunked** 的兼容性。

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
if (r->headers_out.content_type.len
    && ngx_strnstr((char*) r->headers_out.content_type.data, "text/html", r->headers_out.content_type.len)) {
    /* append script in body filter */
}
```

**运行结果（预期）**：
- 配置检查通过；
- 关键路径连通；
- `error.log` 无持续异常。

**可能遇到的坑**：仅做功能验证，不做压力与异常验证，导致上线后才暴露边界问题。

### 步骤二：基线压测与指标采集

**步骤目标**：用数据而非主观感受评估改动价值。

```bash
curl -s http://127.0.0.1:8080/ | rg monitor.js
curl -I http://127.0.0.1:8080/
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

**本章实战目标**：完成自定义 filter 注入并保证响应完整性与兼容性。

### 完整代码清单

- 目录建议：`ops/nginx/ch37/`
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

在阅读顺序上，建议你先完成本章的最小实验与故障演练，再进入《自定义 HTTP 模块开发实战》。这样能把“配置会写”升级为“结论可验证、变更可回退”的工程能力。

> **下一章预告**：自定义 HTTP 模块开发实战
