# Grafana 实战修炼专栏大纲

> **版本**：Grafana 11.x
> **面向人群**：新人开发、测试、核心开发、运维、架构师
> **总章节**：40 章（基础篇 16 章 / 中级篇 15 章 / 高级篇 9 章）
> **每章独立成文件，字数 3000-5000 字**

---

## 专栏定位

以 Grafana 11.x 为蓝本，从安装部署到架构设计，从面板可视化到自定义插件开发，从单机监控到大规模可观测性平台落地，全链路贯通。每一章均采用「业务痛点 → 三人剧本对话 → 代码实战 → 总结思考」的四段式结构，兼顾趣味性、实战性与深度。

---

## 阅读路线建议

| 角色 | 建议阅读顺序 | 重点章节 |
|------|-------------|---------|
| 新人开发/测试 | 基础篇全读 → 中级篇选读 | 第 1-16 章 |
| 核心开发/运维 | 基础篇速读 → 中级篇精读 → 高级篇选读 | 第 17-31、32-40 章 |
| 架构师/资深开发 | 高级篇为主线，按需回溯中级篇 | 第 32-40 章，辅以 17-31 章 |

---

# 基础篇（第 1-16 章）

> **核心目标**：建立 Grafana 核心概念，掌握单机部署、面板可视化、数据源接入与初级告警。
> **技术关联**：Grafana UI 操作、Dashboard JSON 模型、Prometheus 数据源、Provisioning 配置。

---

## 第1章：Grafana术语全景与工作原理
**定位**：专栏总览与开篇，建立统一语系。
**核心内容**：
- 术语词典：Dashboard、Panel、DataSource、Organization、Plugin、Alert Rule、Contact Point
- Grafana 整体架构图解：前端 React + 后端 Go + 数据源代理层
- 请求处理链路：浏览器 → Grafana Server → Data Source Proxy → 后端数据库
- 插件体系概述：Panel Plugin、DataSource Plugin、App Plugin
- 10 年发展简史：从 Kibana fork 到 LGTM 可观测性栈

## 第2章：安装部署与初体验
**定位**：动手跑起来，建立直观感受。
**核心内容**：
- 三种安装方式对比：Docker Compose、二进制包、Kubernetes Helm
- 配置文件 grafana.ini 核心段解析
- 首次登录向导与默认数据源设置
- 目录结构：conf/、data/、plugins/、public/
- 实战：5 分钟启动 Grafana + Prometheus，导入首个 Dashboard

## 第3章：仪表盘Dashboard核心概念
**定位**：理解 Grafana 最核心的抽象——Dashboard。
**核心内容**：
- Dashboard JSON 模型：面板数组、时间范围、刷新间隔、模板变量
- Row 折叠行与 Grid 栅格布局
- 时间范围控制：from/to、时间偏移、自动刷新
- Dashboard 版本管理与回滚
- 导出/导入 Dashboard JSON，社区大盘（grafana.com/dashboards）

## 第4章：面板Panel类型详解（上）——时序与统计
**定位**：掌握最常用的可视化面板。
**核心内容**：
- Time series 面板：多序列、梯度着色、阈值线、图例定制、Tooltip 优化
- Stat 面板：阈值驱动着色、Sparkline 迷你图、Value mapping 值映射
- Gauge 面板：范围分段、阈值标记、方向设置
- Bar chart 面板：横向/纵向、分组/堆叠、颜色方案

## 第5章：面板Panel类型详解（下）——表格与特殊面板
**定位**：覆盖剩余常用面板，建立可视化选型能力。
**核心内容**：
- Table 面板：列过滤、排序、分页、Cell 着色、URL 链接
- Pie chart 饼图：标签位置、Donut 模式、钻取交互
- Geomap 地理面板：Marker/Heatmap Layer、GeoJSON 数据
- Text/Markdown 面板：信息聚合展示
- 可视化选型决策树：数据类型 → 面板类型推荐

## 第6章：数据源Data Source基本配置与使用
**定位**：打通 Grafana 与后端数据的桥梁。
**核心内容**：
- Data Source 概念与连接池机制
- 查询编辑器（Query Editor）通用用法
- 常用数据源对比：Prometheus、InfluxDB、MySQL、Elasticsearch、Loki
- 数据源健康检查与探测
- 自定义 HTTP Headers 与 TLS 配置

## 第7章：Prometheus集成与监控可视化
**定位**：Grafana 最经典的搭档，监控可视化的核心。
**核心内容**：
- Prometheus 数据模型：Metric Name、Labels、Samples、四种指标类型
- PromQL 快速入门：rate、increase、sum、by、histogram_quantile
- Metrics Browser 指标浏览器
- Alert State 面板与告警状态可视化
- Node Exporter 主机监控实战

## 第8章：变量与模板化Dashboard
**定位**：一个 Dashboard 适配多环境、多服务。
**核心内容**：
- 变量类型：Query、Custom、Constant、Text box、Interval、Data source、Ad hoc filter
- 变量联动与多选
- Repeat panel/direction 重复面板
- 变量语法：$variable、${variable:queryparam}、${__from} 等内置变量
- 实战：同一个 Dashboard 切换多集群、多命名空间

## 第9章：Transform数据转换实战
**定位**：不写查询，用 Transform 重塑数据。
**核心内容**：
- Transform 链式处理模型
- Filter by name/value、Group by、Join by field、Merge、Sort、Calculate field
- Reduce、Rename by regex、Partition by values
- Organize fields、Extract fields
- 实战：多数据源数据合并与联表

## 第10章：时间序列与查询选项
**定位**：控制数据查询的粒度与性能。
**核心内容**：
- 时间范围：相对时间（$__range）、绝对时间、时间偏移
- Query options：Max data points、Min interval、Interval、Resolution
- 缓存策略：Query caching 工作原理与配置
- 采样与降精度：avg/max/min 聚合选择
- 实战：对比不同 interval 对图表精度的影响

## 第11章：用户、团队与组织管理
**定位**：多租户环境下的权限隔离。
**核心内容**：
- Organization 组织隔离模型（数据源/Dashboard 完全隔离）
- 角色体系：Admin、Editor、Viewer
- Team 团队与 Dashboard 权限
- Service Account 服务账号与 API 自动化
- 实战：为开发/运维/业务三个部门创建隔离的监控空间

## 第12章：告警基础——规则与通知渠道
**定位**：从可视化到主动通知，监控闭环第一步。
**核心内容**：
- Grafana Alerting 新架构：Alert Rule → Contact Point → Notification Policy → Silence
- 告警规则类型：阈值告警、NoData 告警、Error 告警
- 评估行为与告警状态（Normal、Pending、Alerting、NoData、Error）
- 通知渠道：Email、Slack、Webhook、钉钉、飞书
- 实战：CPU > 80% 持续 5 分钟 → Slack 通知

## 第13章：Grafana HTTP API
**定位**：用 API 管理 Grafana 一切资源。
**核心内容**：
- API 认证方式：Bearer Token、Basic Auth、API Key、Service Account Token
- Dashboard API：创建/更新/查询/删除、Home Dashboard 设置
- Data Source API：增删改查、代理查询
- Alerting API：规则、通知策略、Silence 管理
- 实战：用 Python 脚本批量导入 100 个 Dashboard

## 第14章：Provisioning配置即代码
**定位**：将 Grafana 配置纳入 GitOps 流程。
**核心内容**：
- Provisioning 体系：Data Source、Dashboard、Alerting、Plugin、Notifier
- YAML/JSON 配置文件格式与目录结构
- Dashboard Provisioning：自动发现 json 文件并导入
- 与 Infrastructure as Code 结合（Ansible、Terraform）
- 实战：Git 仓库管理 Dashboard → CI/CD 自动同步到 Grafana

## 第15章：运维基础——日志、诊断与备份
**定位**：保证 Grafana 自身稳定运行。
**核心内容**：
- Grafana 自身日志配置与级别
- 数据库选型：SQLite vs MySQL vs PostgreSQL，切换与迁移
- 备份策略：数据库 dump + Dashboard JSON 导出
- 常见故障：登录失败、数据源连接超时、Dashboard 加载慢
- 实战：从 SQLite 迁移到 PostgreSQL，备份恢复到新实例

## 第16章：【基础篇综合实战】企业级运维监控大盘搭建
**定位**：融会贯通基础篇知识。
**核心内容**：
- 场景：为一家中型电商公司搭建完整运维监控体系
- 需求拆解：主机监控、应用 QPS 监控、数据库慢查询监控、自定义业务指标
- 分步实现：Prometheus + Node Exporter + Grafana，模板化 Dashboard
- 验收标准：覆盖 50+ 台主机，100+ 个应用实例，5 个核心业务大盘

---

# 中级篇（第 17-31 章）

> **核心目标**：掌握分布式场景下的可观测性体系，三支柱（Metrics/Logs/Traces）联动作战。
> **技术关联**：Loki、Tempo、Mimir、Pyroscope、统一告警、Kubernetes 监控。

---

## 第17章：认证体系——LDAP/OAuth/SAML/JWT
**定位**：企业级单点登录全方案。
**核心内容**：
- 各认证协议对比与选型
- LDAP 配置：服务器连接、组映射（group_mappings）、角色同步
- OAuth 2.0：GitHub/Google/Generic OAuth
- SAML：Okta/Auth0/Keycloak 企业 SSO
- JWT 认证与 auto-login 自动登录

## 第18章：MySQL/PostgreSQL数据源实战
**定位**：Grafana 不只是监控，业务数据可视化同样强大。
**核心内容**：
- SQL 查询编辑器：Table 模式、Time series 模式
- 时间序列宏：$__timeGroup、$__timeFilter、$__unixEpochFilter
- 变量与 SQL 查询联动
- 慢查询 Dashboard 设计
- 实战：订单量趋势、用户增长曲线、数据库连接池监控

## 第19章：Elasticsearch日志可视化
**定位**：将日志数据变成可视化洞察。
**核心内容**：
- ES 数据源配置：节点连接、版本兼容、索引模式
- Lucene Query 语法与 Piped Processing Language (PPL)
- Terms/Aggregation/Bucket 聚合
- Logs 面板与日志搜索
- 实战：Nginx 访问日志 Top10 URL、HTTP 状态码分布、错误日志趋势

## 第20章：Loki日志聚合平台集成
**定位**：Grafana Loki——轻量级日志方案。
**核心内容**：
- Loki 架构：Distributor → Ingester → Querier → 对象存储
- LogQL 语法：Log stream selector + Log pipeline + Metric queries
- Label 索引设计：静态 Label vs 动态 Label，高基数陷阱
- Logs → Metrics 转换（rate、count_over_time）
- 实战：Kubernetes Pod 日志聚合与实时搜索

## 第21章：Tempo分布式链路追踪
**定位**：解决微服务调用链的"黑盒"问题。
**核心内容**：
- Tempo 架构与部署模式（Monolithic、Microservices）
- TraceQL 查询语言
- Span 瀑布图与 Service Graph 拓扑图
- Metric → Log → Trace 三柱关联跳转（Exemplar 纽带）
- 实战：在 HTTP 中间件注入 TraceID，追踪一次慢请求的完整链路

## 第22章：高级告警——静默、分组与模板
**定位**：告警治理，告别告警风暴。
**核心内容**：
- Notification templates 模板化消息
- 分组告警：Group by、Group wait、Group interval
- Silence 静默规则与定时静默
- 告警状态历史与回放
- Notification Policy Tree 通知策略树
- 实战：按服务/环境分组告警，合并 5 分钟内同类告警

## 第23章：统一告警中心——Grafana Alerting + Prometheus Alertmanager
**定位**：两类告警体系的融合实战。
**核心内容**：
- Grafana Alerting vs Prometheus Alertmanager 架构对比
- 双写策略与告警去重
- 使用 Alertmanager 作为 Grafana Alerting 的 Contact Point
- Grafana Alerts → Alertmanager → 多通道分发
- 实战：统一告警中心，覆盖应用/基础设施/业务三类告警

## 第24章：Mimir指标长期存储与大规模治理
**定位**：解决 Prometheus 存储瓶颈。
**核心内容**：
- Mimir 架构：Ingester、Distributor、Querier、Store-gateway、Compactor
- 对象存储后端：S3/MinIO/GCS
- Recording Rules 预聚合与 Relabel
- 租户隔离与限制（limits）
- 实战：Prometheus Remote Write → Mimir，保留 3 个月指标数据

## 第25章：Grafana高可用与横向扩展
**定位**：从单点到集群，支撑万人规模。
**核心内容**：
- Grafana 无状态架构设计
- 统一外部数据库（PostgreSQL/MySQL）
- 共享会话缓存（Redis/Memcached）
- 多副本负载均衡：Nginx / HAProxy / K8s Service
- 实战：Docker Compose 部署 2 节点 Grafana + PostgreSQL + Redis

## 第26章：Dashboard性能优化
**定位**：页面秒开，告别白屏等待。
**核心内容**：
- 慢查询根源分析：Query Inspector 工具使用
- 面板级优化：减少面板数量、合并查询
- 模板变量性能：减少下拉选项、开启缓存
- 浏览器端优化：查询并发控制、Render 模式
- Alerting 规则性能：减少高频率规则
- 实战：将 50 面板 Dashboard 加载时间从 15s 优化到 3s

## 第27章：Grafana + Pyroscope持续性能分析
**定位**：Profiling 成为可观测性第四支柱。
**核心内容**：
- Pyroscope 架构与 SDK 集成（Go/Java/Python/Node.js）
- Flamegraph 火焰图解读
- CPU、Memory、Alloc、Block、Mutex 分析
- Continuous Profiling vs 传统 pprof 采样
- 实战：定位 Go 微服务内存泄漏，从火焰图找到泄漏根因

## 第28章：SLO与Burn Rate可观测实战
**定位**：用 SLO 衡量服务质量，用 Error Budget 驱动决策。
**核心内容**：
- SLI/SLO/SLA 概念与设定方法
- Error Budget 燃烧速率（Burn Rate）计算
- Multi-window、Multi-burn-rate 告警
- Grafana SLO Dashboard 设计
- 实战：为订单 API 设定 99.9% SLO，配置 4 级 Burn Rate 告警

## 第29章：Grafana OnCall告警值班
**定位**：从告警到响应，全流程闭环。
**核心内容**：
- OnCall 架构：Escalation Chain、Schedule、Shift Swap
- 排班管理：iCal 导入、多时区支持
- 告警升级链：短信 → 电话 → 二线值班
- OnCall 与 Grafana Alerting 集成
- 实战：配置一个 7×24 三级排班，含自动升级与手动接管

## 第30章：Kubernetes监控与Grafana Operator
**定位**：云原生环境下的 Grafana 最佳实践。
**核心内容**：
- kube-prometheus-stack 全家桶部署
- Grafana Operator CRD：Grafana、GrafanaDashboard、GrafanaDataSource
- Dashboard as Code：Dashboard 即 CR 资源
- PrometheusRule 与 ServiceMonitor
- 实战：Operator 自动化管理 Grafana，Dashboard 跟随应用部署自动创建

## 第31章：【中级篇综合实战】微服务全栈可观测性平台
**定位**：融会贯通中级篇，构建生产级可观测性体系。
**核心内容**：
- 场景：50+ 微服务电商中台的可观测性建设
- 四支柱落地：Metrics（Mimir）+ Logs（Loki）+ Traces（Tempo）+ Profiles（Pyroscope）
- 架构设计：Agent（Grafana Alloy）采集 → 各后端存储 → Grafana 统一可视化
- 验收标准：故障 MTTR 从 30 分钟降至 5 分钟，P99 查询延迟 < 2s

---

# 高级篇（第 32-40 章）

> **核心目标**：源码级理解 Grafana，掌握插件开发与极端场景优化。
> **技术关联**：Go 后端源码、React 前端源码、Plugin SDK、大规模部署。

---

## 第32章：Grafana源码结构与环境搭建
**定位**：从使用者到贡献者的第一步。
**核心内容**：
- 仓库整体结构：pkg/、public/、devenv/、plugins-bundled/
- 后端编译：Go 1.22+、wire 依赖注入、air 热重载
- 前端启动：Yarn/Turborepo、React + Redux Toolkit + Emotion
- 开发模式：Docker Compose 本地开发环境
- 调试技巧：Go delve、React DevTools、Grafana 自身日志

## 第33章：后端核心——Go服务架构剖析
**定位**：理解 Grafana 的"发动机"。
**核心内容**：
- 模块化设计：pkg/api、pkg/services、pkg/models、pkg/plugins
- 依赖注入：Google Wire 原理与 wire.go 文件
- API 路由注册（Macaron → 自定义路由）
- 中间件链：认证、日志、Recovery、CORS
- 数据库层：xorm → SQL 抽象，Migration 机制

## 第34章：前端架构——React组件与状态管理
**定位**：理解 Grafana 的"驾驶舱"。
**核心内容**：
- 组件树：App → Page → Panel → Visualization
- Redux Toolkit 状态管理：store、slice、selector
- @grafana/data / @grafana/ui / @grafana/runtime 核心包
- Scenes 框架：新一代 Dashboard 运行时
- 插件加载机制：SystemJS → Module Federation

## 第35章：数据源插件开发实战（上）——Go后端
**定位**：从零开发一个自定义数据源。
**核心内容**：
- @grafana/create-plugin 脚手架
- Grafana Plugin SDK for Go（backend package）
- QueryData、CheckHealth、Streaming 接口实现
- Data Frame 数据结构：Field、Vector、Frame
- 代码签名与插件打包（Grafana Plugin Validator）
- 实战：开发一个"天气 API 数据源"

## 第36章：数据源插件开发实战（下）——React前端
**定位**：构建数据源的可视化配置界面。
**核心内容**：
- QueryEditor 组件：查询条件编辑界面
- ConfigEditor 组件：数据源连接配置界面
- DataSourceWithBackend 类与后端通信
- MetricFindValue：变量支持的查询实现
- Plugin E2E 测试（@grafana/plugin-e2e）
- 实战：为"天气 API 数据源"添加城市选择器和温度单位配置

## 第37章：Panel面板插件开发实战
**定位**：自定义可视化，满足特殊业务需求。
**核心内容**：
- Panel Plugin 项目结构：plugin.json、module.tsx、Panel component
- PanelProps 与 FieldConfig API
- Canvas、SVG、D3 集成方案
- Theme 适配（深色/浅色模式）
- 从 React Panel 迁移到 Scenes Panel
- 实战：开发"SVG 拓扑图"面板，展示微服务调用关系

## 第38章：App插件与Scenes应用开发
**定位**：构建完整的 Grafana 内嵌应用。
**核心内容**：
- App Plugin 结构：RootPage、ConfigPage、SubPage
- Scenes 框架：SceneApp、SceneFlexLayout、SceneQueryRunner
- 自定义 App 页面路由与导航
- 多 Tab 页面与共享状态
- 实战：开发"发布日历"App，在 Grafana 内管理变更窗口

## 第39章：大规模场景优化——千万级指标与架构调优
**定位**：极端场景下 Grafana 的生产化改造。
**核心内容**：
- 数据库优化：连接池、读写分离、索引优化
- 渲染服务：PhantomJS → Chromium → Remote Rendering Service
- 缓存层：Redis 分布式缓存策略
- 查询分片与并发控制
- Dashboard/DataSource 数量膨胀治理
- Alerting 评估引擎调优
- 实战：支撑 10 万+ Dashboard、1000+ 用户并发访问

## 第40章：【高级篇综合实战】自研企业级可观测性平台
**定位**：融会贯通全书，交付可落地的平台级方案。
**核心内容**：
- 场景：为金融科技公司自研统一可观测性平台
- 功能规划：统一大盘门户、自定义品牌（White-labeling）、告警聚合路由
- 架构设计：多云数据联邦、Grafana 集群 + Mimir + Loki + Tempo
- 扩展开发：审计日志插件、合规报表、自定义 RBAC
- 性能指标：单集群 500 万 events/min，P99 查询 < 3s
- 验收标准：满足 SOC2 合规要求，支撑 200+ 研发团队

---

## 附录

### 附录 A：源码阅读路线图
1. 入口：pkg/cmd/grafana-server/main.go
2. 初始化：pkg/server/server.go → wire 注入
3. API 注册：pkg/api/api.go → Route Table
4. 数据源查询：pkg/tsdb/ → 各数据源实现

### 附录 B：本地开发环境搭建指南
- Go 1.22+ + Node.js 20+ + Docker
- make run / yarn start 开发模式
- devenv/docker-compose.yaml 本地依赖

### 附录 C：推荐工具链
- 数据采集：Grafana Alloy、Prometheus、OpenTelemetry Collector
- 存储：Mimir、Loki、Tempo、Pyroscope
- 测试：k6、Playwright、@grafana/plugin-e2e
- CI/CD：GitHub Actions、ArgoCD、Terraform

### 附录 D：思考题参考答案索引
- 基础篇思考题答案：见各章末尾或本附录对应小节
- 中级篇思考题答案：见各章末尾或本附录对应小节
- 高级篇思考题答案：见各章末尾或本附录对应小节

---

> **版权声明**：本专栏基于 Grafana 11.x（AGPLv3 License）编写，所有代码示例均遵循原许可证条款。
