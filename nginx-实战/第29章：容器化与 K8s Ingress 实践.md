# 第29章：容器化与 K8s Ingress 实践

> 源码关联：Nginx Ingress Controller、Kubernetes Ingress API
> 批次说明：第 3 批（精修版）

**关键词摘要**：K8s Ingress、Ingress Controller、多域路由、自动 HTTPS、金丝雀发布

---

## 1. 项目背景

多团队共享 K8s 集群后，入口规则分散在各服务仓库，证书续签和灰度策略靠人工执行，发布窗口频繁拉长。团队决定统一接入 Nginx Ingress。

进入这一阶段后，Nginx 不再只是“转发工具”，而是稳定性、性能与工程效率的共同支点。仅靠参数经验已经不足以支撑复杂系统，必须把“设计假设、实验数据、故障演练、回滚策略”绑定成一个闭环。
本章沿着“问题定义 -> 方案取舍 -> 实验验证 -> 上线守护”的路径展开，确保方案可复制、可复盘、可演进。

---

从知识路径上看，本章与上一章《日志分析与 ELK Stack 实战》形成前后呼应：上一章解决的是“局部能力成立”，本章要进一步回答“在真实流量和复杂协作下如何稳定成立”。

## 2. 项目设计

**场景**：运维周会，集群负责人诉苦——三个项目组、五个集群、七套 nginx.conf 模板，每次加服务都要改配置文件发工单。

---

**小胖**：（嚼着牛肉干）现在每个项目都各自维护一套 nginx 配置，改个 upstream 还要发工单等审批，比食堂排队打饭还慢！能不能像小区快递柜那样，各家快递自己扫码开箱就行？

**小白**：容器化之后 Pod IP 是动态分配的，nginx upstream 里没法写死 IP。而且每个服务都得配 SSL 证书，手动续签 90 天一次，漏了就是证书过期事故。

**大师**：（在白板上画了一个 K8s 集群拓扑）小胖的快递柜比喻很形象。K8s Ingress 就是这个"智能快递柜"——每个服务只需要声明自己的路由规则（一个 Ingress YAML），不需要知道其他服务的存在。Ingress Controller 自动监听 API Server 的 Service/Endpoint 变化，动态生成 nginx 配置，连 reload 都不用手动。证书问题交给 cert-manager，自动对接 Let's Encrypt 签发和续期，彻底消灭"证书过期"事故。

**技术映射**：Ingress Controller 本质是运行在 K8s 内的定制 nginx 实例，通过 informer 机制 watch Ingress/Service/Endpoint 资源。核心组件：nginx-ingress-controller（nginx + lua）+ ConfigMap（全局配置）+ Secret（TLS 证书）。

---

**小胖**：灰度发布怎么搞？我现在上线新版本都是半夜爬起来改 nginx upstream 权重，万一改错了手一抖全崩了！

**小白**：灰度最怕的是流量切过去才发现 bug，回滚又慢。能不能像奶茶店推新品那样——先让 10% 的客人免费试饮，没问题再全量上架？

**大师**：你俩说到一块儿了。Nginx Ingress 的 Annotation 原生支持金丝雀发布。在原 Ingress 基础上，加一个 canary Ingress：`nginx.ingress.kubernetes.io/canary: "true"` + `canary-weight: "10"`，10% 的流量自动导到新版本。更高级的玩法是按 header 灰度——`canary-by-header: "X-Canary"`，只有内部测试账号才走灰度版本。回滚超级简单：把 canary-weight 改成 0 或者直接删掉 canary Ingress，一秒回滚。

**技术映射**：两个 Ingress 对象共用同一 host 和 path，一个标记 `canary: "true"`，nginx-ingress 自动合并路由并根据权重/header/cookie 分配流量。底层通过 `split_clients` 或 lua 脚本实现一致性哈希分流。

---

**小白**：Ingress Controller 本身挂了呢？它岂不是成了新的单点？

**大师**：好问题。Ingress Controller 必须高可用部署：① 至少 2 个副本，用 PodAntiAffinity 打散到不同节点；② 前面挂 LB（阿里云 SLB/AWS NLB），LB 做健康检查，不健康的 Pod 自动摘除；③ 配置 PDB（PodDisruptionBudget）保证最少 1 个副本在线；④ 用 HPA 自动扩缩容应对流量波动。更关键的是——Ingress Controller 挂了不影响已有 TCP 连接，nginx worker 进程继续跑，只是新配置变更无法生效。重启也是优雅关闭，现有请求处理完才退出。

**技术映射**：高可用架构：External LB → Ingress Controller (Deployment + HPA + PDB + AntiAffinity) → Service → Pod。监控通过 Prometheus Operator 采集 Ingress Controller 自带的 nginx_ingress_controller_requests 等指标。

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
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: app-gateway
  annotations:
    nginx.ingress.kubernetes.io/rewrite-target: /
    nginx.ingress.kubernetes.io/canary: "true"
    nginx.ingress.kubernetes.io/canary-weight: "10"
spec:
  ingressClassName: nginx
  rules:
    - host: api.demo.local
      http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                name: api-svc
                port:
                  number: 80
```

**运行结果（预期）**：
- 配置检查通过；
- 关键路径连通；
- `error.log` 无持续异常。

**可能遇到的坑**：仅做功能验证，不做压力与异常验证，导致上线后才暴露边界问题。

### 步骤二：基线压测与指标采集

**步骤目标**：用数据而非主观感受评估改动价值。

```bash
kubectl get ingress
kubectl describe ingress app-gateway
kubectl get svc -n ingress-nginx
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

**本章实战目标**：落地 Ingress 统一入口，支持多域路由、自动 HTTPS 与灰度能力。

### 完整代码清单

- 目录建议：`ops/nginx/ch29/`
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

在阅读顺序上，建议你先完成本章的最小实验与故障演练，再进入《Lua/NJS 动态扩展入门》。这样能把“配置会写”升级为“结论可验证、变更可回退”的工程能力。

> **下一章预告**：Lua/NJS 动态扩展入门
