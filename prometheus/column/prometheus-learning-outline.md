# Prometheus 实战与原理修炼专栏大纲

> 版本：Prometheus 2.55+ / 3.x LTS
> 面向人群：开发、运维、测试、架构师
> 总章节：40 章（基础篇 16 章 / 中级篇 15 章 / 高级篇 9 章）
> 每章独立成文件，字数 3000-5000 字
> 理念：**实战为主，理论为辅，由浅入深**

---

## 专栏定位

以 Prometheus 官方生态为核心，从核心概念到架构设计，从 PromQL 到源码实现，从单机监控到全球级可观测性平台落地，全链路贯通。每章采用「业务痛点 → 三人剧本对话（小胖/小白/大师）→ 代码实战 → 总结思考」四段式结构。

---

## 阅读路线建议

| 角色 | 建议阅读顺序 | 重点章节 |
|------|-------------|---------|
| 新人开发/测试 | 基础篇全读 → 中级篇选读 | 第 1-16 章 |
| 核心开发/运维 | 基础篇速读 → 中级篇精读 → 高级篇选读 | 第 17-31、32-40 章 |
| 架构师/SRE | 高级篇为主线，按需回溯中级篇 | 第 32-40 章，辅以 17-31 章 |

---

# 基础篇（第 1-16 章）

> **核心目标**：建立 Prometheus 核心概念，掌握单机部署、常用 Exporters、PromQL 与 Grafana 可视化。
> **生态关联**：Prometheus Server、Node Exporter、Blackbox Exporter、Pushgateway、Grafana。

---

### 第1章：Prometheus 术语全景与 Pull 模型架构原理
**定位**：专栏总览与开篇，建立统一语系。
**核心内容**：
- 术语词典：metric / label / time series / sample / scrape / target / recording rule / alerting rule / exporter / instance / job
- Pull vs Push 模型对比：Prometheus 为何选择主动拉取？
- 整体架构图：Prometheus Server → Service Discovery → Exporters → Alertmanager → Grafana
- 时序数据库 TSDB 核心概念：Head、Block、WAL、Compaction
**实战目标**：手绘一张 Prometheus 生态架构图，标注数据流向，输出到团队 Wiki。

### 第2章：环境搭建与十分钟快速启动
**定位**：从零到一，跑起第一个 Prometheus。
**核心内容**：
- 三种安装方式：二进制部署 / Docker / Docker Compose
- prometheus.yml 最小化配置逐行解读
- Web UI（Graph / Table 视图）初探
- Expression Browser 第一个 PromQL：`up`、`prometheus_http_requests_total`
**实战目标**：Docker Compose 一键启动 Prometheus + Node Exporter + Grafana 三件套，验证 `up` 指标。

### 第3章：数据模型与四大指标类型
**定位**：理解 Prometheus 如何描述世界。
**核心内容**：
- Metric Name / Labels 命名规范与非规范陷阱
- 四大指标类型：Counter、Gauge、Histogram、Summary 的语义与适用场景
- Histogram vs Summary 的分位数对比（client-side vs server-side）
- 暴露格式讲解：`/metrics` 端点文本格式
**实战目标**：编写一个 Python/Go 程序暴露自定义四种指标，用 curl 查看原始数据，理解 label cardinality。

### 第4章：PromQL 入门——选择器与操作符
**定位**：掌握 Prometheus 的查询语言基础。
**核心内容**：
- Instant Vector vs Range Vector：一次查询点 vs 时间窗口
- 选择器：`=`、`!=`、`=~`、`!~` 正则匹配实战
- Offset 修饰符：对比昨天同一时刻的数据
- 算术/比较/逻辑运算符：`+`、`-`、`>`、`<`、`and`、`or`、`unless`
**实战目标**：在 Expression Browser 中完成 10 道选择器练习题，对比不同 label 过滤结果。

### 第5章：PromQL 函数——从统计到预测
**定位**：用函数挖掘指标背后的故事。
**核心内容**：
- 增长率全家桶：`rate`、`irate`、`increase`、`delta`、`idelta`
- 时间聚合：`avg_over_time`、`max_over_time`、`quantile_over_time`
- 预测函数：`predict_linear`、`deriv`、`holt_winters`
- 数学与标签操作：`abs`、`round`、`label_replace`、`label_join`
**实战目标**：针对 Node Exporter 的 CPU/内存指标，写出 5 条典型运维 PromQL。

### 第6章：Node Exporter——主机监控从入门到精通
**定位**：最核心的 Exporter，监控一切的起点。
**核心内容**：
- Node Exporter 架构与 Collector 机制
- 核心 Collector 指标解读：cpu、memory、disk、network、filesystem、systemd
- `--collector.textfile` directory：自定义文本指标采集
- `--no-collector.*` 按需启停 Collector（减少基数）
**实战目标**：在 3 台异构服务器（Linux/Windows）部署 Node Exporter，配置 Prometheus 采集，输出一份主机巡检指标清单。

### 第7章：Prometheus 配置文件深度解析
**定位**：理解 prometheus.yml 的每一行。
**核心内容**：
- `global`：scrape_interval、evaluation_interval、external_labels
- `scrape_configs`：job_name、metrics_path、scheme、basic_auth、bearer_token
- scrape timeout 与 scrape interval 的协同关系
- `rule_files` 与 `alerting` 的关联
**实战目标**：编写一份生产级 prometheus.yml（含多 job、多 target、不同采集间隔），用 `promtool check config` 校验。

### 第8章：Grafana 可视化——从图表到大屏
**定位**：让数据会说话。
**核心内容**：
- Data Source 配置与权限控制
- 面板类型：Time series、Stat、Gauge、Table、Bar chart
- Transform 函数：数据裁剪、合并、排序
- Dashboard 变量：`$instance`、`$job`、动态过滤
- 导入社区大盘：Node Exporter Full Dashboard (ID: 1860)
**实战目标**：从零搭建一张生产环境巡检大盘，包含 CPU/内存/磁盘/网络的实时状态与 24h 趋势。

### 第9章：告警规则——从指标异常到通知触达
**定位**：监控的最后一公里。
**核心内容**：
- Rules 文件结构：groups、rules、labels、annotations
- 告警阈值设计原则：基线 + 容差 + 抖动窗口
- `for` 参数：避免毛刺误告
- 常用告警模板：node down、CPU > 90%、磁盘 > 85%、service gone
**实战目标**：编写 5 条告警规则，覆盖主机/应用/服务健康检查，用 `promtool test rules` 验证。

### 第10章：Alertmanager——告警路由、分组与静默
**定位**：告警治理，不多报、不漏报、不乱报。
**核心内容**：
- 告警生命周期：firing → resolved → notification
- 路由树：`route` 的 `match`/`match_re` 与子路由嵌套
- 分组机制：`group_by`、`group_wait`、`group_interval`、`repeat_interval`
- 静默规则（Silences）与抑制规则（Inhibition）
- 通知渠道：Email、Webhook、钉钉/飞书/企业微信
**实战目标**：配置 Alertmanager 路由树，实现「业务告警 → 开发钉钉群」「基础设施告警 → 运维飞书群」的分级分发。

### 第11章：服务发现基础——静态与文件发现
**定位**：告别手动改 IP，动态发现目标。
**核心内容**：
- 静态服务发现（static_configs）的局限
- 文件服务发现（file_sd_configs）：JSON/YAML 文件的 reload 机制
- 基于文件 SD 实现简单的目标注册中心
- relabeling 基础：`source_labels`、`target_label`、`replacement`、`keep`/`drop`
**实战目标**：构建一个「文件 SD + 定时刷新脚本」的简易 CMDB 驱动采集方案。

### 第12章：Recording Rules——查询加速与指标预计算
**定位**：用空间换时间，优化大盘加载速度。
**核心内容**：
- Recording Rules vs Alerting Rules 的异同
- 典型场景：高成本 PromQL 预计算、跨时间聚合
- 命名规范与最佳实践：`level:metric:operations`
- 对 TSDB 存储的影响：新时间序列的基数评估
**实战目标**：为一张加载 30s+ 的 Grafana 大盘添加 3 条 Recording Rules，对比优化前后加载时间。

### 第13章：Pushgateway——短生命周期任务监控
**定位**：批处理作业、CronJob 的监控救星。
**核心内容**：
- Push vs Pull 的边界：何时该用 Pushgateway？
- Pushgateway 的「只增不减」特性与清理策略
- Push 方式：HTTP API `PUT` vs `POST` 的区别与陷阱
- 典型场景：数据库备份脚本、ETL 任务、CI/CD Pipeline
**实战目标**：编写一个定时备份脚本，通过 Pushgateway 上报执行状态与耗时，配置 Grafana 监控面板。

### 第14章：Blackbox Exporter——黑盒监控实战
**定位**：从外面看，服务还活着吗？
**核心内容**：
- 黑盒 vs 白盒监控：互补关系
- 探测类型：HTTP/HTTPS、TCP、DNS、ICMP、gRPC
- HTTP Probe：状态码、SSL 证书过期、响应时间、body 正则匹配
- 多地域探测：多 Prometheus 实例 + external_labels 区分
**实战目标**：配置 10 个关键域名/端口的黑盒监控，SSL 证书过期提前 30 天告警。

### 第15章：Prometheus 日常运维与故障排查
**定位**：从能跑到稳跑。
**核心内容**：
- 常用命令行工具：`promtool`（check config / check rules / test rules / tsdb）
- Prometheus HTTP API：`/api/v1/query`、`/api/v1/query_range`、`/api/v1/targets`、`/api/v1/rules`
- 自身监控指标：`prometheus_tsdb_*`、`prometheus_engine_*`、`prometheus_target_*`
- 常见故障：OOM Kill、WAL Corruption、Scrape timeout、Disk full
**实战目标**：模拟 5 种常见故障（配置错误/目标不可达/磁盘满/WAL损坏/内存OOM），给出排查 SOP。

### 第16章：【基础篇综合实战】搭建企业级全栈监控平台
**定位**：融会贯通基础篇知识。
**核心内容**：
- 场景：为一家电商公司搭建从基础设施到应用的统一监控
- 监控对象：Linux 主机 ×5、MySQL、Redis、Nginx、Spring Boot 应用
- 功能涵盖：指标采集（5 类 Exporter）→ PromQL 告警规则（15+ 条）→ Alertmanager 分级通知 → Grafana 大屏
- 验收标准：主机宕机 30s 内告警，应用 5xx 率突增 1min 内告警，核心大盘 P99 加载 < 5s

---

# 中级篇（第 17-31 章）

> **核心目标**：掌握分布式场景下的架构设计、PromQL 进阶、自定义 Exporter 开发与容器化/云原生实践。
> **生态关联**：Prometheus Operator、Thanos、VictoriaMetrics、Kubernetes、Remote Storage。

---

### 第17章：TSDB 存储引擎——Write-Ahead Log 与 Block 机制
**定位**：理解 Prometheus 数据持久化的核心。
**核心内容**：
- TSDB 目录结构：`wal/`、`chunks_head/`、`01XXXXXX/` 的职责
- WAL 写入流程：sample → head append → wal log → checkpoint
- Block 结构：`meta.json`、`chunks/`、`index`、`tombstones`
- Compaction 策略：Level-based Compaction 的触发与合并
**实战目标**：使用 `promtool tsdb` 工具分析生产数据，输出 Block 数量、大小与时间范围。

### 第18章：Relabeling 深度实战
**定位**：掌握 Prometheus 最灵活也最易踩坑的配置。
**核心内容**：
- `relabel_configs`（采集前）vs `metric_relabel_configs`（采集后）
- Actions 全家桶：`replace`、`keep`、`drop`、`labelmap`、`labeldrop`、`labelkeep`、`hashmod`
- 典型场景：多租户标签注入、高基数标签裁剪、分片采集（hashmod）
- 调试点：Prometheus Web UI Status → Service Discovery → Targets
**实战目标**：配置 `hashmod` 实现 3 分片采集，配置 `metric_relabel_configs` 裁掉冗余 label，验证基数下降 50%+。

### 第19章：PromQL 进阶——子查询、向量匹配与数学建模
**定位**：PromQL 从入门到精通的分水岭。
**核心内容**：
- 子查询（Subquery）：`rate(metric[5m])[1h:1m]`
- @修饰符：精确时刻查询，用于历史数据分析
- 向量匹配：`on`/`ignoring` + `group_left`/`group_right`
- 多对一 / 一对多匹配的实战案例（如 K8s Pod label 与 Node label 关联）
**实战目标**：用 5 条进阶 PromQL 解决生产问题（如按业务线统计 QPS、Pod → Node → AZ 级联聚合）。

### 第20章：服务发现进阶——Kubernetes、Consul、DNS
**定位**：云原生时代的动态目标管理。
**核心内容**：
- Kubernetes SD 机制：Pod、Service、Endpoints、Node、Ingress 角色
- `kubernetes_sd_configs` 配置详解与 relabeling 最佳实践
- Consul SD：服务注册中心的集成
- DNS SD：`dns_sd_configs` 的 SRV/A 记录发现
**实战目标**：在 K8s 集群中配置 Prometheus 自动发现所有 Pod 并打上 namespace/deployment 标签。

### 第21章：Alertmanager 进阶——路由调优与告警治理
**定位**：告别告警风暴，实现精准通知。
**核心内容**：
- 路由树进阶：多层嵌套路由、故障升级策略（escalation）
- 模板定制：使用 Go Template 自定义通知内容（钉钉 Markdown、飞书卡片）
- 静默管理 API：编程式创建/删除 silences
- 告警聚合：基于时间的滚动窗口去重
**实战目标**：编写钉钉 Markdown 告警模板，实现「30min 未确认 → 升级到 TL」的告警升级链路。

### 第22章：Grafana 进阶——变量、注解与 Provisioning
**定位**：从手工配图到代码化运维。
**核心内容**：
- 变量类型：Query、Custom、Constant、Interval、Data link
- 注解（Annotations）：关联变更事件（上线/扩容/故障）
- Dashboard Provisioning：JSON 文件 + Grafana API 全自动化
- Loki + Prometheus 联动：日志 ↔ 指标一键下钻
**实战目标**：将 3 张手工 Grafana 大盘改造为 Provisioning 代码化，用 API 自动导入。

### 第23章：自定义 Exporter 开发（Go 语言篇）
**定位**：当官方 Exporter 不够用时，自己写一个。
**核心内容**：
- Exporter 设计模式：Collector 接口、MustNewConstMetric
- Metrics 注册与 HTTP handler：`/metrics` 端点暴露
- 连接池管理：数据库/Redis 连接复用与并发安全
- 错误处理：`Up` 指标与 scrape_error 的最佳实践
**实战目标**：开发一个 MySQL Exporter 精简版（QPS / 连接数 / 慢查询 / 主从延迟），对比官方 mysqld_exporter。

### 第24章：自定义 Exporter 开发（Python/Java 篇）
**定位**：多语言生态的监控接入。
**核心内容**：
- Python：`prometheus_client` 库的 Counter/Gauge/Histogram + HTTP Server
- Java：`micrometer` + `prometheus-registry` 在 Spring Boot 中的集成
- Python multiprocess 模式：Gunicorn/uWSGI 多 worker 共享指标
- 与 Pushgateway 的配合：短任务指标推送
**实战目标**：为现有 Python/Java 应用添加 Prometheus 指标埋点，暴露 QPS / P99 延迟 / 错误率。

### 第25章：Federation 与层级联邦
**定位**：跨数据中心、跨集群的全局聚合查询。
**核心内容**：
- Federation 工作模式：`/federate` 端点原理
- 横向联邦：多 Prometheus 并行采集，Grafana 聚合展示
- 纵向联邦（Hierarchical）：全局 Prometheus 选择性拉取区域实例
- 联邦的 match 过滤与数据精度损失
**实战目标**：搭建「2 个区域 Prometheus + 1 个全局 Prometheus」三层联邦架构，验证全球视角查询。

### 第26章：Remote Storage——远程读写协议
**定位**：突破本地 TSDB 的存储天花板。
**核心内容**：
- Remote Write / Remote Read 协议：Protocol Buffers 定义与 Snappy 压缩
- 写入路径：wal → remote write queue → shard → remote storage
- 读取路径：PromQL → querier → remote read → merge
- 主流远程存储适配：VictoriaMetrics、Thanos Receive、InfluxDB、Cortex
**实战目标**：配置 Prometheus Remote Write 到 VictoriaMetrics 单机版，对比本地 TSDB 与远程存储的查询性能。

### 第27章：Prometheus Operator——K8s 原生部署与管理
**定位**：让 Prometheus 在 Kubernetes 中一等公民。
**核心内容**：
- Operator 模式简介：CRD → Controller → Desired State
- 核心 CRD：Prometheus、ServiceMonitor、PodMonitor、PrometheusRule、AlertmanagerConfig
- `additionalScrapeConfigs` 与 `relabelings` 的 Operator 化
- Prometheus Operator 的自动 reload 机制
**实战目标**：用 Helm 部署 kube-prometheus-stack，配置 ServiceMonitor 采集自定义应用，验证自动发现。

### 第28章：Thanos——全局视图与长期存储
**定位**：Prometheus 的高可用 + 全局查询 + 长期存储方案。
**核心内容**：
- Thanos 核心组件：Sidecar、Querier、Store、Compactor、Ruler、Receiver
- Querier 的 Deduplication 与 Partial Response 策略
- Store Gateway 的 Downsampling：raw / 5m / 1h 精度
- Compactor 的跨时间块合并
**实战目标**：搭建 Thanos Sidecar + MinIO 对象存储，验证「1 年历史数据秒级查询」。

### 第29章：VictoriaMetrics——高性能时序数据库
**定位**：更省资源、更快的 Prometheus 替代/补充方案。
**核心内容**：
- VictoriaMetrics 与 Prometheus 的架构差异
- 集群模式：vminsert / vmselect / vmstorage 三件套
- PromQL 兼容性：支持与不支持的函数清单
- MetricsQL 扩展功能（如 `rollup_*`、`*_over_time` 高级聚合）
**实战目标**：搭建 VictoriaMetrics 集群版，将 100 万 active series 的查询延迟从 Prometheus 的 3s 降至 100ms。

### 第30章：Kubernetes 监控体系深度实践
**定位**：云原生时代监控的完整解决方案。
**核心内容**：
- 核心组件的指标：kube-state-metrics（资源状态）、cAdvisor（容器资源）、kubelet metrics（节点）
- Kubernetes 混合监控架构：核心组件 + 自定义应用 + CRD 状态
- USE / RED / Four Golden Signals 方法论在 K8s 中的应用
- K8s 事件监控：Kubernetes Events → Exporter → Prometheus → Grafana
**实战目标**：为生产 K8s 集群构建统一监控大盘，覆盖集群资源/节点/命名空间/工作负载四个维度。

### 第31章：【中级篇综合实战】多数据中心统一监控架构设计
**定位**：融会贯通中级篇知识。
**核心内容**：
- 场景：为一家拥有 3 个数据中心、50+ 微服务的跨国公司设计监控架构
- 架构：Prometheus → Thanos → Grafana + Prometheus Operator on K8s
- 功能实现：多集群指标聚合、跨 DC 告警路由、Remote Write 到 S3、SLI/SLO 面板
- 验收标准：全球 Dashboards P99 加载 < 3s，告警延迟 < 30s，99.9% 存储可用性

---

# 高级篇（第 32-40 章）

> **核心目标**：源码级理解 Prometheus 实现原理，掌握极端场景优化、自定义组件开发与 SRE 落地。
> **源码关联**：TSDB、PromQL Engine、Scrape Manager、Service Discovery、Remote Storage。

---

### 第32章：TSDB 源码深度剖析——Head 与 WAL
**定位**：理解时序数据库的内核。
**核心内容**：
- Head Block 源码：`headAppender`、`memSeries`、`memChunk` 的数据结构
- WAL 编码格式：Record 类型（Series / Samples / Tombstones）
- WAL 截断与检查点（Checkpoint）的生成逻辑
- 内存映射（mmap）：chunks 的热数据分层
**实战目标**：编写一段 Go 程序，直接读取 WAL 文件并解析出 series 和 sample 数量。

### 第33章：TSDB 源码深度剖析——Compaction 与索引
**定位**：理解 Block 合并与倒排索引。
**核心内容**：
- Level-based Compaction 的合并策略与触发条件
- 倒排索引（Postings Index）：从 label 到 series ID 的映射
- 正排索引：从 series ID 到 chunk 位置的映射
- Tombstone 机制：逻辑删除与物理清理的延迟
**实战目标**：手动触发一次 compaction，对比合并前后 Block 文件的变化。

### 第34章：PromQL 引擎源码解析
**定位**：理解一条 PromQL 从解析到执行的全流程。
**核心内容**：
- Lexer/Parser：文本 → AST（`Node` 接口树的构建）
- Query Engine：`query.exec` 的执行流程与算子实现
- 查询优化：`MatrixSelector` 的预加载、函数下推
- Duplicate samples 处理：何时丢弃重复样本
**实战目标**：在 PromQL Engine 源码中插入耗时日志，分析一条复杂 Range Query 的热点函数。

### 第35章：Scrape 引擎——Discovery → Scrape → Append 全链路
**定位**：理解 Prometheus 的数据采集核心管道。
**核心内容**：
- Discovery Manager：SD Provider 的发现与同步
- Scrape Manager：target 的哈希一致性分配（sharding）
- Scrape Loop：HTTP fetch → text parse → staleness marking → append
- Staleness 机制：目标消失后，指标何时从查询中消失？
**实战目标**：修改 scrape loop 源码，添加 scrape body 大小、耗时分布的 Histogram 指标。

### 第36章：Remote Storage 协议与性能调优
**定位**：突破单机存储瓶颈的生产实践。
**核心内容**：
- Remote Write 协议源码解析：WriteRequest 的构造与发送
- WAL Watcher 与 Remote Write 的协同：队列、分片、退避重试
- 大数据量下的调优：`max_shards`、`max_samples_per_send`、`capacity`
- Remote Read 的 streaming 模式：避免 OOM 的分批传输
**实战目标**：配置 Remote Write 到 ClickHouse，对比 Snappy vs Zstd 压缩比与 CPU 开销。

### 第37章：高基数问题——根源、检测与治理
**定位**：Prometheus 运维的头号杀手。
**核心内容**：
- 高基数的根源：user_id、request_id、trace_id 等高基数值作为 label
- 检测工具：`prometheus_tsdb_head_series`、`count by (__name__)`、Cardinality Explorer
- 治理策略：`metric_relabel_configs` 裁剪、Recording Rules 降基数、Exporter 侧聚合
- TSDB 在高基数下的性能退化曲线（查询延迟 / 内存占用 / Compaction 耗时）
**实战目标**：模拟 100 万+ 高基数 series 场景，对比治理前后内存从 16GB → 2GB，查询延迟从 30s → 500ms。

### 第38章：自定义 Exporter 高阶——并发安全、连接池与生产落地
**定位**：写出经得起生产考验的 Exporter。
**核心内容**：
- 并发安全：`sync.Mutex`、`sync.Map`、channel 的选择
- 连接池管理：`sql.DB` 的 MaxOpenConns / MaxIdleConns / ConnMaxLifetime
- scrape timeout 防御：Context 超时传递 → 子查询取消
- Exporter 自监控：`exporter_build_info`、`exporter_scrape_duration_seconds`
- 集成测试：使用 `promtool test rules` 验证 Exporter 输出
**实战目标**：将第 23-24 章的 Exporter 升级为生产版本，添加连接池、超时控制、自监控，并压测至 100 并发 scrape。

### 第39章：SRE 实践——SLI/SLO/SLA 与错误预算
**定位**：从技术监控到业务可靠性保障。
**核心内容**：
- SLI（Service Level Indicator）：可用性、延迟、吞吐量的 PromQL 定义
- SLO（Service Level Objective）：目标值设定与合规窗口
- Error Budget（错误预算）：当 burn rate 超过阈值时的告警
- Multi-window, Multi-burn-rate Alert：Google SRE 经典算法在 Prometheus 中的实现
- Grafana SLO Dashboard：错误预算剩余量、燃尽速率、合规趋势
**实战目标**：为核心 API 定义 SLI（99.9% 可用性），编写多窗口燃尽告警规则，验证错误预算耗尽时的升级通知。

### 第40章：【高级篇综合实战】企业级可观测性平台——自研 API 网关的监控体系
**定位**：融会贯通全专栏知识，产出可交付的生产级可观测性方案。
**核心内容**：
- 场景：为一家金融科技公司自研 API 网关构建全栈可观测性平台
- 架构设计：指标（Prometheus）→ 日志（Loki）→ 追踪（Tempo/Jaeger）三柱联动
- 功能实现：
  - 自定义 Exporter 采集网关关键指标
  - Remote Write → VictoriaMetrics 长期存储（2 年数据留存）
  - SLO Dashboard（可用性 + 延迟 + 错误预算燃尽图）
  - 熔断/限流事件自动标注至 Grafana 注解
- 性能指标：日均 500 亿 active series 存储，P99 查询 < 200ms，全年可用性 99.99%
- 部署方案：K8s Helm + 跨 AZ 多活 + GitOps 管理

---

# 附录与资源

### 附录 A：Prometheus 源码阅读路线图
1. 入口：`cmd/prometheus/main.go` 的 `main` 函数
2. 初始化：Config → SD Manager → TSDB Open → Scrape Manager
3. 运行时：Scrape Loop → TSDB Append → Rule Evaluation → Notifier
4. 查询：HTTP API → PromQL Engine → TSDB Querier

### 附录 B：常用命令速查表
- `promtool check config prometheus.yml`
- `promtool check rules rules.yml`
- `promtool test rules test.yml`
- `promtool tsdb list /data`
- `promtool tsdb analyze /data`

### 附录 C：推荐工具链
- Prometheus & Alertmanager & Node Exporter & Blackbox Exporter
- Grafana & Loki & Tempo / Jaeger
- Thanos & VictoriaMetrics
- Prometheus Operator + kube-prometheus-stack
- Docker & Kubernetes & Helm

### 附录 D：思考题参考答案索引

---

> **版权声明**：本专栏基于 Prometheus 官方源码（Apache 2.0 License）编写，所有源码引用均遵循原许可证条款。
