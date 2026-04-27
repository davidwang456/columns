# Nginx 专栏目录与关键词索引

> 版本：出版排版索引

> 说明：用于编辑校对、目录制作与关键词检索

---

## 按章节索引

| 章次 | 篇章 | 标题 | 关键词摘要 | 文件 |
|---|---|---|---|---|
| 第1章 | 基础篇 | Nginx 术语全景与 master-worker 架构原理 | （待补充） | 第1章：Nginx 术语全景与 master-worker 架构原理.md |
| 第2章 | 基础篇 | 源码目录解析与编译安装 | （待补充） | 第2章：源码目录解析与编译安装.md |
| 第3章 | 基础篇 | 配置文件语法与指令系统 | （待补充） | 第3章：配置文件语法与指令系统.md |
| 第4章 | 基础篇 | 进程模型——信号处理与进程间通信 | （待补充） | 第4章：进程模型——信号处理与进程间通信.md |
| 第5章 | 基础篇 | 静态资源服务与 Location 匹配规则 | （待补充） | 第5章：静态资源服务与 Location 匹配规则.md |
| 第6章 | 基础篇 | 反向代理基础与 Proxy 模块 | （待补充） | 第6章：反向代理基础与 Proxy 模块.md |
| 第7章 | 基础篇 | 负载均衡入门——Round Robin 与 IP Hash | （待补充） | 第7章：负载均衡入门——Round Robin 与 IP Hash.md |
| 第8章 | 基础篇 | FastCGI 协议与 PHP 集成 | （待补充） | 第8章：FastCGI 协议与 PHP 集成.md |
| 第9章 | 基础篇 | Rewrite 模块与 URL 重写 | （待补充） | 第9章：Rewrite 模块与 URL 重写.md |
| 第10章 | 基础篇 | HTTP 核心模块——变量与请求处理 | （待补充） | 第10章：HTTP 核心模块——变量与请求处理.md |
| 第11章 | 基础篇 | Gzip 压缩与内容过滤链 | （待补充） | 第11章：Gzip 压缩与内容过滤链.md |
| 第12章 | 基础篇 | SSL/TLS 配置与 HTTPS 实战 | （待补充） | 第12章：SSL-TLS 配置与 HTTPS 实战.md |
| 第13章 | 基础篇 | 日志系统与访问控制 | （待补充） | 第13章：日志系统与访问控制.md |
| 第14章 | 基础篇 | 浏览器缓存与 Proxy Cache 基础 | （待补充） | 第14章：浏览器缓存与 Proxy Cache 基础.md |
| 第15章 | 基础篇 | Nginx 日常运维与故障排查 | （待补充） | 第15章：Nginx 日常运维与故障排查.md |
| 第16章 | 基础篇 | 【基础篇综合实战】搭建企业级 LNMP 站点 | （待补充） | 第16章：【基础篇综合实战】搭建企业级 LNMP 站点.md |
| 第17章 | 中级篇 | 事件驱动模型——epoll/kqueue/select 深度对比 | 事件驱动、epoll vs select、高并发连接、I/O 多路复用、性能基线 | 第17章：事件驱动模型——epoll-kqueue-select 深度对比.md |
| 第18章 | 中级篇 | 连接池管理与 Keepalive 优化 | upstream keepalive、连接复用、握手开销、连接池上限、P99 优化 | 第18章：连接池管理与 Keepalive 优化.md |
| 第19章 | 中级篇 | Upstream 高级配置与故障转移 | 故障转移、max_fails、proxy_next_upstream、幂等性、backup 节点 | 第19章：Upstream 高级配置与故障转移.md |
| 第20章 | 中级篇 | 高级负载均衡算法实战 | 一致性哈希、least_conn、负载方差、会话粘性、算法选型 | 第20章：高级负载均衡算法实战.md |
| 第21章 | 中级篇 | HTTP/2 与 gRPC 代理 | HTTP/2、gRPC 代理、多路复用、ALPN、流式超时 | 第21章：HTTP-2 与 gRPC 代理.md |
| 第22章 | 中级篇 | 高级缓存架构与 Cache Purge | proxy_cache、cache key、slice 缓存、Cache Purge、缓存一致性 | 第22章：高级缓存架构与 Cache Purge.md |
| 第23章 | 中级篇 | 限流熔断——limit_req 与 limit_conn | limit_req、limit_conn、突发流量、分维度限流、429/503 | 第23章：限流熔断——limit_req 与 limit_conn.md |
| 第24章 | 中级篇 | Stream 四层代理——TCP/UDP | stream 模块、TCP 代理、UDP 代理、四层转发、无感切流 | 第24章：Stream 四层代理——TCP-UDP.md |
| 第25章 | 中级篇 | WebSocket 与长连接管理 | WebSocket、长连接治理、心跳策略、proxy_read_timeout、连接稳定性 | 第25章：WebSocket 与长连接管理.md |
| 第26章 | 中级篇 | 流量镜像与 A/B 测试 | mirror、split_clients、A/B 测试、灰度发布、影子流量 | 第26章：流量镜像与 A-B 测试.md |
| 第27章 | 中级篇 | 监控体系与 Prometheus 集成 | stub_status、Prometheus、RED 指标、告警规则、可观测闭环 | 第27章：监控体系与 Prometheus 集成.md |
| 第28章 | 中级篇 | 日志分析与 ELK Stack 实战 | 结构化日志、ELK、慢请求分析、错误码分布、日志索引 | 第28章：日志分析与 ELK Stack 实战.md |
| 第29章 | 中级篇 | 容器化与 K8s Ingress 实践 | K8s Ingress、Ingress Controller、多域路由、自动 HTTPS、金丝雀发布 | 第29章：容器化与 K8s Ingress 实践.md |
| 第30章 | 中级篇 | Lua/NJS 动态扩展入门 | NJS/Lua、动态扩展、规则热更新、脚本风控、运行时约束 | 第30章：Lua-NJS 动态扩展入门.md |
| 第31章 | 中级篇 | 【中级篇综合实战】构建高可用微服务网关 | 统一网关、认证鉴权、限流灰度、链路追踪、平台化治理 | 第31章：【中级篇综合实战】构建高可用微服务网关.md |
| 第32章 | 高级篇 | 内存池设计与核心数据结构源码剖析 | ngx_pool_t、内存池、核心数据结构、生命周期、性能对比 | 第32章：内存池设计与核心数据结构源码剖析.md |
| 第33章 | 高级篇 | 模块系统与配置解析器源码 | ngx_module_t、ngx_command_t、配置合并、模块生命周期、自定义指令 | 第33章：模块系统与配置解析器源码.md |
| 第34章 | 高级篇 | 事件循环源码——epoll 实现原理 | 事件循环、epoll_wait、定时器红黑树、posted events、调度时序 | 第34章：事件循环源码——epoll 实现原理.md |
| 第35章 | 高级篇 | HTTP 请求生命周期完整源码链路 | 请求生命周期、phase handler、finalize_request、499 排查、调用链追踪 | 第35章：HTTP 请求生命周期完整源码链路.md |
| 第36章 | 高级篇 | Upstream 源码——连接池与负载均衡 | upstream peer、动态权重、连接池管理、负载均衡、慢节点治理 | 第36章：Upstream 源码——连接池与负载均衡.md |
| 第37章 | 高级篇 | Filter 链机制与数据流处理源码 | filter chain、header/body filter、响应改写、Content-Length、注入埋点 | 第37章：Filter 链机制与数据流处理源码.md |
| 第38章 | 高级篇 | 自定义 HTTP 模块开发实战 | 自定义模块、共享内存计数、handler 开发、状态接口、模块配置 | 第38章：自定义 HTTP 模块开发实战.md |
| 第39章 | 高级篇 | 百万并发调优与 QUIC/HTTP3 源码剖析 | 百万并发、系统调优、火焰图、QUIC/HTTP3、尾延迟 | 第39章：百万并发调优与 QUIC-HTTP3 源码剖析.md |
| 第40章 | 高级篇 | 【高级篇综合实战】从零构建高性能 API 网关 | API 网关、动态路由、鉴权限流、生产交付、高可用架构 | 第40章：【高级篇综合实战】从零构建高性能 API 网关.md |

---

## 阅读建议

- 新人：优先基础篇，再读中级篇中的网关治理章节。
- 核心开发/运维：以中级篇为主线，按需回溯基础篇。
- 架构师：从高级篇切入，再回看中级篇综合实战。

## 出版前校对清单

- 章节标题与目录是否一一对应。
- 代码块是否成对闭合、命令是否可执行。
- 表格列宽与标点风格是否统一。
- 下一章预告与实际下一章标题是否一致。
