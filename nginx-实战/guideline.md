# Nginx 源码剖析与实战修炼专栏大纲

> 版本：Nginx 1.31.0
> 面向人群：开发、运维、测试、架构师
> 总章节：40 章（基础篇 16 章 / 中级篇 15 章 / 高级篇 9 章）
> 每章独立成文件，字数 3000-5000 字

---

## 专栏定位

以 Nginx 1.31.0 官方源码为骨架，从配置使用到架构设计，从源码实现到二次开发，从性能调优到生产落地，全链路贯通。每一章均采用「业务痛点 → 三人剧本对话 → 代码实战 → 总结思考」的四段式结构，兼顾趣味性、实战性与深度。

---

## 阅读路线建议

| 角色 | 建议阅读顺序 | 重点章节 |
|------|-------------|---------|
| 新人开发/测试 | 基础篇全读 → 中级篇选读 | 第 1-16 章 |
| 核心开发/运维 | 基础篇速读 → 中级篇精读 → 高级篇选读 | 第 17-31、32-40 章 |
| 架构师/资深开发 | 高级篇为主线，按需回溯中级篇 | 第 32-40 章，辅以 17-31 章 |

---

# 基础篇（第 1-16 章）

> **核心目标**：建立 Nginx 核心概念，掌握单机部署、常用模块配置与初级故障排查。
> **源码关联**：src/core/ 基础结构、src/http/ 核心配置、conf/ 示例配置。

---

## 第1章：Nginx 术语全景与 master-worker 架构原理
**定位**：专栏总览与开篇，建立统一语系。
**核心内容**：
- 术语词典：master、worker、connection、request、upstream、location、server、context、phase handler、filter chain
- master-worker 进程模型图解：启动流程、信号处理、热升级机制
- 模块化架构：core / event / http / stream / mail / third-party
- 事件驱动与非阻塞 IO：Reactor 模式在 Nginx 中的体现
- 源码文件关联：src/core/nginx.c、src/core/ngx_cycle.h、src/os/unix/ngx_process_cycle.c
**实战目标**：绘制一张可讲解的 Nginx 整体架构图，输出到团队 Wiki。

---

## 第2章：源码目录解析与编译安装
**定位**：从源码视角理解 Nginx 的五脏六腑。
**核心内容**：
- 目录结构全览：src/core/、src/event/、src/http/、src/stream/、src/mail/、src/os/、auto/
- configure 脚本工作原理与常用编译参数（--with-*、--add-module）
- auto/ 目录：feature 检测、模块注册、Makefile 生成
- 源码关联：auto/configure、auto/options、auto/sources、auto/modules
**实战目标**：自定义编译一个带 debug 符号的 Nginx，并验证 --with-http_v3_module。

---

## 第3章：配置文件语法与指令系统
**定位**：理解 Nginx 配置的语法糖与上下文。
**核心内容**：
- 指令类型：简单指令、块指令、上下文（main / events / http / server / location）
- 配置继承与合并规则
- ngx_conf_file.c 解析流程：tokenize -> parse -> merge
- 变量系统：set、map、geo 的底层实现
- 源码关联：src/core/ngx_conf_file.c、src/core/ngx_conf_file.h
**实战目标**：编写一份包含多级 location 嵌套、变量继承、map 映射的复杂配置，并用 nginx -t 验证。

---

## 第4章：进程模型——信号处理与进程间通信
**定位**：理解 Nginx 进程管理的操作系统原理。
**核心内容**：
- master 进程职责：启动 worker、管理信号、执行热升级
- worker 进程职责：事件循环、请求处理
- 信号机制：SIGHUP、SIGUSR1、SIGUSR2、SIGWINCH、SIGTERM
- 跨进程通信：共享内存、socketpair、channel
- 源码关联：src/os/unix/ngx_process_cycle.c、src/os/unix/ngx_process.c
**实战目标**：通过 kill 信号触发 reload、reopen、upgrade，观察进程状态变化。

---

## 第5章：静态资源服务与 Location 匹配规则
**定位**：Nginx 最基础也是最常用的能力。
**核心内容**：
- location 匹配优先级：=、^~、~、~*、无前缀
- try_files、alias、root 的差异与陷阱
- 目录索引：autoindex、index、默认首页
- sendfile、tcp_nopush、tcp_nodelay 的协同
- 源码关联：src/http/ngx_http_core_module.c、src/http/modules/ngx_http_static_module.c
**实战目标**：搭建一个支持多版本前端资源（带 hash 文件名）的静态站点，并实现 history 路由 fallback。

---

## 第6章：反向代理基础与 Proxy 模块
**定位**：理解 Nginx 作为网关的第一能力。
**核心内容**：
- 正向代理 vs 反向代理
- proxy_pass 的 URI 传递规则（带斜杠 vs 不带斜杠）
- 请求头改写：proxy_set_header、proxy_hide_header
- 缓冲区：proxy_buffering、proxy_buffer_size、proxy_buffers
- 超时与重试：proxy_connect_timeout、proxy_read_timeout、proxy_next_upstream
- 源码关联：src/http/modules/ngx_http_proxy_module.c、src/http/ngx_http_upstream.c
**实战目标**：搭建一个带请求头透传、超时熔断、错误页自定义的反向代理集群。

---

## 第7章：负载均衡入门——Round Robin 与 IP Hash
**定位**：从单点到集群的桥梁。
**核心内容**：
- 负载均衡算法概览：RR、加权 RR、IP Hash、Least Conn
- upstream 块语法与 server 参数（weight、max_fails、fail_timeout、backup、down）
- IP Hash 的会话保持原理与缺陷
- 后端健康检查基础机制
- 源码关联：src/http/ngx_http_upstream_round_robin.c、src/http/modules/ngx_http_upstream_ip_hash_module.c
**实战目标**：配置一个 3 节点后端的上游组，验证加权轮询与 IP Hash 的会话保持效果。

---

## 第8章：FastCGI 协议与 PHP 集成
**定位**：经典 LNMP/LAMP 架构的核心纽带。
**核心内容**：
- FastCGI 协议帧结构：FCGI_BEGIN_REQUEST、FCGI_PARAMS、FCGI_STDIN、FCGI_STDOUT
- fastcgi_pass 与 fastcgi_param 配置
- PATH_INFO、SCRIPT_FILENAME 等关键参数
- PHP-FPM 的 pm 模式与 Nginx 的协同
- 源码关联：src/http/modules/ngx_http_fastcgi_module.c
**实战目标**：搭建 LNMP 环境，配置 WordPress 站点，解决常见 502/504 错误。

---

## 第9章：Rewrite 模块与 URL 重写
**定位**：SEO 与路由治理的瑞士军刀。
**核心内容**：
- rewrite 指令的 regex、replacement、flag（last、break、redirect、permanent）
- if 指令的可用变量与条件判断
- return 指令与重定向
- Rewrite 与 Location 的协作关系
- 源码关联：src/http/modules/ngx_http_rewrite_module.c
**实战目标**：实现一套旧站 URL 迁移规则（300+ 条旧 URL 301 到新 URL），并验证 SEO 权重无损。

---

## 第10章：HTTP 核心模块——变量与请求处理
**定位**：深入理解 Nginx 的 HTTP 处理管道。
**核心内容**：
- HTTP 处理阶段：NGX_HTTP_POST_READ -> NGX_HTTP_CONTENT -> NGX_HTTP_LOG
- 内置变量：、、System.Management.Automation.Internal.Host.InternalHost、*、
- 自定义变量：set、map、geo
- ngx_http_core_module 的职责与钩子
- 源码关联：src/http/ngx_http_core_module.c、src/http/ngx_http_variables.c
**实战目标**：编写一套基于变量的灰度发布规则（按 Cookie、Header、IP 段分流）。

---

## 第11章：Gzip 压缩与内容过滤链
**定位**：性能优化的第一道闸门。
**核心内容**：
- Gzip 压缩原理：gzip、gzip_types、gzip_min_length、gzip_comp_level
- gzip_static：预压缩与动态压缩的选择
- gunzip：解压缩下游内容
- Nginx Filter Chain 模型：header_filter -> body_filter
- 源码关联：src/http/modules/ngx_http_gzip_filter_module.c、src/http/modules/ngx_http_gzip_static_module.c
**实战目标**：对比开启/关闭 Gzip 对 1MB JSON 接口的 TTFB 影响，给出最优压缩级别建议。

---

## 第12章：SSL/TLS 配置与 HTTPS 实战
**定位**：现代 Web 的安全基线。
**核心内容**：
- SSL 握手流程与 Nginx 中的加速（ssl_session_cache、ssl_session_tickets）
- 协议版本与加密套件：ssl_protocols、ssl_ciphers、ssl_prefer_server_ciphers
- OCSP Stapling：ssl_stapling、ssl_stapling_verify
- HTTP Strict Transport Security（HSTS）
- 源码关联：src/event/ngx_event_openssl.c、src/http/modules/ngx_http_ssl_module.c
**实战目标**：配置 A+ 评级的 HTTPS（Qualys SSL Labs 测试），并实现 HTTP/2 + OCSP Stapling。

---

## 第13章：日志系统与访问控制
**定位**：可观测性的起点。
**核心内容**：
- access_log 与 error_log 的格式定义与条件日志
- log_format 变量与 JSON 结构化日志
- 访问控制：allow、deny、auth_basic、auth_request
- 限速日志与错误码分析
- 源码关联：src/http/modules/ngx_http_log_module.c、src/http/modules/ngx_http_access_module.c
**实战目标**：设计一套 JSON 格式的结构化日志，并接入 jq 命令行分析工具。

---

## 第14章：浏览器缓存与 Proxy Cache 基础
**定位**：从服务端到客户端的缓存治理。
**核心内容**：
- HTTP 缓存头：Cache-Control、Expires、ETag、Last-Modified
- expires 指令与浏览器缓存策略
- proxy_cache 基础：proxy_cache_path、proxy_cache_key、proxy_cache_valid
- 缓存失效：主动清理 vs TTL 过期
- 源码关联：src/http/ngx_http_file_cache.c、src/http/modules/ngx_http_headers_filter_module.c
**实战目标**：为静态资源配置 1 年浏览器缓存，为 API 配置 5 分钟代理缓存，并验证缓存命中率。

---

## 第15章：Nginx 日常运维与故障排查
**定位**：从能跑到稳跑。
**核心内容**：
- 常用运维命令：nginx -t、nginx -s reload、nginx -V
- 配置文件语法检查与热重载
- 常见错误码排查：502、504、499、413、444
- 进程异常：worker 进程 CPU 100%、内存泄漏、连接耗尽
- 日志诊断与 strace 基础使用
**实战目标**：模拟 5 种生产常见故障（502/504/499/配置错误/权限问题），给出排查 SOP。

---

## 第16章：【基础篇综合实战】搭建企业级 LNMP 站点
**定位**：融会贯通基础篇知识。
**核心内容**：
- 场景：为一家电商公司搭建完整的 Web 服务栈（Nginx + PHP-FPM + MySQL + Redis）
- 需求拆解：静态资源服务、PHP 动态请求、HTTPS、Gzip、缓存、日志、访问控制
- 分步实现：Docker Compose 编排、配置分层、性能基准测试
- 验收标准：压测 1000 并发下响应时间 < 100ms，错误率 < 0.1%

---

# 中级篇（第 17-31 章）

> **核心目标**：掌握分布式场景下的架构设计、性能调优、可观测性与容器化实践。
> **源码关联**：src/event/ 事件模块、src/http/upstream/ 负载均衡、src/stream/ 四层代理。

---

## 第17章：事件驱动模型——epoll/kqueue/select 深度对比
**定位**：理解 Nginx 高性能的 IO 根基。
**核心内容**：
- 阻塞 IO -> 非阻塞 IO -> IO 多路复用 -> 异步 IO 的演进
- select/poll 的 FD_SETSIZE 瓶颈与遍历开销
- epoll：epoll_create、epoll_ctl、epoll_wait、ET vs LT 模式
- kqueue：EVFILT_READ / EVFILT_WRITE / EVFILT_TIMER
- Nginx 的事件抽象层：ngx_event_actions_t
- 源码关联：src/event/modules/ngx_epoll_module.c、src/event/modules/ngx_kqueue_module.c、src/event/ngx_event.c
**实战目标**：在同一台机器上对比 select/epoll 的并发连接上限，绘制 C10K 到 C100K 的性能曲线。

---

## 第18章：连接池管理与 Keepalive 优化
**定位**：减少 TCP 握手开销，提升后端吞吐量。
**核心内容**：
- TCP 连接生命周期：三次握手 -> 数据传输 -> 四次挥手
- keepalive 指令：keepalive_timeout、keepalive_requests
- Upstream Keepalive：keepalive 连接池的工作原理
- ngx_http_upstream_keepalive_module 源码解析
- 连接复用 vs 连接耗尽的场景分析
- 源码关联：src/http/modules/ngx_http_upstream_keepalive_module.c
**实战目标**：对比开启/关闭 Upstream Keepalive 对 QPS 的影响，定位连接耗尽问题。

---

## 第19章：Upstream 高级配置与故障转移
**定位**：构建高可用的后端集群。
**核心内容**：
- proxy_next_upstream 的触发条件与风险（幂等性）
- 被动健康检查：max_fails、fail_timeout
- 主动健康检查：第三方模块 nginx_upstream_check_module
- 备份节点与慢启动：backup、slow_start
- 连接超时与读取超时的差异化配置
- 源码关联：src/http/ngx_http_upstream.c、src/http/ngx_http_upstream.h
**实战目标**：搭建 3 主 1 备的上游集群，模拟节点故障，验证故障转移时间与请求零丢失。

---

## 第20章：高级负载均衡算法实战
**定位**：从均分到智能调度。
**核心内容**：
- 一致性哈希：ngx_http_upstream_hash_module，虚拟节点与最小偏差
- 最少连接：ngx_http_upstream_least_conn_module
- 随机负载均衡与加权随机
- Session Sticky：ngx_http_upstream_sticky_module，Cookie 注入与植入
- 各算法适用场景与对比表
- 源码关联：src/http/modules/ngx_http_upstream_hash_module.c、src/http/modules/ngx_http_upstream_least_conn_module.c、src/http/modules/ngx_http_upstream_sticky_module.c
**实战目标**：为文件上传服务选择一致性哈希，为短连接 API 选择 least_conn，并对比各算法下的后端负载方差。

---

## 第21章：HTTP/2 与 gRPC 代理
**定位**：现代协议栈的网关支持。
**核心内容**：
- HTTP/2 核心特性：二进制分帧、多路复用、头部压缩（HPACK）、服务器推送
- Nginx HTTP/2 配置：http2、http2_push、http2_max_field_size
- gRPC 协议：HTTP/2 + Protobuf，四种服务类型
- grpc_pass、grpc_set_header、grpc_read_timeout
- gRPC 与 REST 的网关转换思路
- 源码关联：src/http/v2/、src/http/modules/ngx_http_grpc_module.c
**实战目标**：部署一个支持 HTTP/2 和 gRPC 的双协议网关，验证流式 RPC 的代理稳定性。

---

## 第22章：高级缓存架构与 Cache Purge
**定位**：构建可治理的缓存层。
**核心内容**：
- proxy_cache_path 的 levels、keys_zone、max_size、inactive 参数调优
- 缓存键设计：proxy_cache_key 与缓存穿透/击穿/雪崩
- 缓存状态：（MISS、HIT、EXPIRED、BYPASS）
- 缓存清理：proxy_cache_purge（第三方模块）与 ngx_cache_purge
- 切片缓存：slice 模块与大文件分片
- 源码关联：src/http/ngx_http_file_cache.c、src/http/modules/ngx_http_slice_filter_module.c
**实战目标**：为一个视频网站配置切片缓存，实现热点视频 90%+ 缓存命中率，并支持后台主动刷新。

---

## 第23章：限流熔断——limit_req 与 limit_conn
**定位**：保护后端服务的防洪堤。
**核心内容**：
- 漏桶算法：limit_req_zone + limit_req，burst 与 nodelay
- 令牌桶 vs 漏桶：Nginx 的选择与边界
- 连接数限制：limit_conn_zone + limit_conn
- 限流返回值：503 与自定义错误页
- 分布式限流的局限与扩展思路
- 源码关联：src/http/modules/ngx_http_limit_req_module.c、src/http/modules/ngx_http_limit_conn_module.c
**实战目标**：为一个登录接口配置 10r/s 的漏桶限流，模拟突发流量，验证排队与丢弃行为。

---

## 第24章：Stream 四层代理——TCP/UDP
**定位**：突破七层的边界，进入网络层。
**核心内容**：
- OSI 四层 vs 七层：Nginx Stream 模块的设计哲学
- stream 上下文与 server、listen、proxy_pass
- TCP 代理：proxy_timeout、proxy_connect_timeout
- UDP 代理：proxy_requests、proxy_responses
- SSL 终止与透传：ssl_preread
- 源码关联：src/stream/ngx_stream_core_module.c、src/stream/ngx_stream_proxy_module.c
**实战目标**：搭建一个 MySQL 负载均衡代理（TCP）和一个 DNS 负载均衡代理（UDP），验证连接透传。

---

## 第25章：WebSocket 与长连接管理
**定位**：实时通信场景的网关支持。
**核心内容**：
- WebSocket 握手：Upgrade: websocket、Connection: Upgrade
- proxy_http_version 1.1 与 proxy_set_header Upgrade
- 长连接的保活：proxy_read_timeout、proxy_send_timeout
- WebSocket 代理的负载均衡挑战
- 与 SSE（Server-Sent Events）的对比
- 源码关联：src/http/modules/ngx_http_proxy_module.c
**实战目标**：部署一个支持 WebSocket 的聊天室网关，验证 10 万长连接的内存占用与消息广播延迟。

---

## 第26章：流量镜像与 A/B 测试
**定位**：生产环境的安全实验。
**核心内容**：
- mirror 模块：无侵入流量复制，用于预发布验证
- split_clients：按比例分流，用于灰度发布
- A/B 测试的变量设计：、、
- 镜像流量的副作用与资源隔离
- 源码关联：src/http/modules/ngx_http_mirror_module.c、src/http/modules/ngx_http_split_clients_module.c
**实战目标**：为订单接口配置 5% 流量镜像到预发布环境，并按用户 ID 哈希实现 A/B 测试分流。

---

## 第27章：监控体系与 Prometheus 集成
**定位**：从黑盒到白盒的可观测性。
**核心内容**：
- stub_status：Active connections、accepts、handled、requests、Reading/Writing/Waiting
- nginx-prometheus-exporter 的部署与指标映射
- 关键监控指标：QPS、Latency、Error Rate、CPU、Memory、Connections
- Grafana 大盘设计：RED 方法（Rate、Errors、Duration）
- 告警规则：连接数阈值、5xx 率突增、上游健康状态
**实战目标**：搭建 stub_status + Prometheus + Grafana 监控栈，配置 3 条核心告警规则。

---

## 第28章：日志分析与 ELK Stack 实战
**定位**：从日志中挖掘价值。
**核心内容**：
- Nginx 日志格式优化：JSON 结构化、少即是多
- Filebeat 采集：多行合并、字段提取、索引策略
- Logstash/Fluentd 解析：GeoIP、UA 解析、状态码聚合
- Kibana 可视化：UV/PV、热点 URL、错误分布、响应时间分位图
- 慢请求分析：按  排序定位长尾
**实战目标**：为一个日 PV 千万级的站点设计 ELK 日志分析方案，输出 Top 10 慢接口报告。

---

## 第29章：容器化与 K8s Ingress 实践
**定位**：云原生时代的 Nginx 形态。
**核心内容**：
- Nginx 官方 Docker 镜像的构建与优化（多阶段构建、非 root 运行）
- nginx:alpine vs nginx:mainline 的选择
- K8s Ingress 原理：Ingress -> Ingress Controller -> Service -> Pod
- Nginx Ingress Controller：Annotations、Rewrite、SSL、Rate Limit
- 与 Service Mesh（Istio）的协作边界
**实战目标**：在 K8s 集群中部署 Nginx Ingress Controller，配置基于 Host 的多租户路由与自动 HTTPS。

---

## 第30章：Lua/NJS 动态扩展入门
**定位**：给 Nginx 装上动态大脑。
**核心内容**：
- ngx_http_js_module（NJS）：JavaScript 子集在 Nginx 中的执行
- OpenResty 生态：ngx_lua、lua-nginx-module、ngx_stream_lua_module
- 典型场景：动态路由、JWT 校验、WAF 规则、限流算法自定义
- NJS vs Lua：性能、生态、学习曲线对比
- 源码关联：src/http/modules/ngx_http_js_module.c（如编译开启）
**实战目标**：使用 NJS 编写一个动态黑名单插件（基于 Redis），或使用 OpenResty 实现自定义限流。

---

## 第31章：【中级篇综合实战】构建高可用微服务网关
**定位**：融会贯通中级篇知识。
**核心内容**：
- 场景：为一个 50+ 微服务的电商中台设计 API 网关
- 功能需求：统一入口、认证鉴权、限流熔断、灰度发布、协议转换、监控告警
- 架构设计：Nginx + Lua/OpenResty + Consul + Prometheus
- 分步实现：上游发现、JWT 校验、限流配置、日志追踪（OpenTelemetry）、健康检查
- 验收标准：99.99% 可用性、P99 延迟 < 50ms、单实例 5 万 QPS

---

# 高级篇


# 高级篇（第 32-40 章）

> **核心目标**：源码级理解 Nginx 的实现原理，掌握自定义模块开发与极端场景优化。
> **源码关联**：src/core/ 数据结构、src/http/ 请求处理、src/event/ 事件循环。

---

## 第32章：内存池设计与核心数据结构源码剖析
**定位**：理解 Nginx 的内存哲学。
**核心内容**：
- 内存池 ngx_pool_t：小块分配（ngx_palloc）、大块分配（ngx_pnalloc）、清理回调（ngx_pool_cleanup_add）
- 为什么不用 malloc/free：减少碎片、提高局部性、简化生命周期管理
- 核心数据结构源码：
  - ngx_array_t：动态数组的扩容与内存布局
  - ngx_list_t：链表数组的批量分配
  - ngx_queue_t：侵入式双向链表
  - ngx_hash_t：静态哈希表的构建与查找
  - ngx_rbtree_t：红黑树的旋转与插入删除
  - ngx_radix_tree_t：基数树用于 IP 前缀匹配
- 源码关联：src/core/ngx_palloc.c、src/core/ngx_array.c、src/core/ngx_list.c、src/core/ngx_queue.c、src/core/ngx_hash.c、src/core/ngx_rbtree.c、src/core/ngx_radix_tree.c
**实战目标**：编写一个独立程序，使用 Nginx 内存池管理 100 万个短生命周期对象，对比 glibc malloc 的性能差异。

---

## 第33章：模块系统与配置解析器源码
**定位**：理解 Nginx 的插件架构。
**核心内容**：
- ngx_module_t 结构体：index、ctx、commands、type、init 等字段
- 模块类型：NGX_CORE_MODULE、NGX_EVENT_MODULE、NGX_HTTP_MODULE、NGX_STREAM_MODULE
- 配置指令解析：ngx_command_t 的 set 回调函数表
- 配置合并：create_conf -> init_conf -> merge（ngx_conf_merge_* 宏）
- 模块加载顺序与 ngx_modules[] 数组
- 源码关联：src/core/ngx_module.c、src/core/ngx_module.h、src/core/ngx_conf_file.c
**实战目标**：编写一个最小的 NGX_HTTP_MODULE，注册一条自定义指令 hello_world，输出 Hello from Nginx。

---

## 第34章：事件循环源码——epoll 实现原理
**定位**：深入 Reactor 模式的内核实现。
**核心内容**：
- ngx_event_t 结构体：handler、data、write、active、ready
- 事件循环入口：ngx_process_events_and_timers
- epoll 模块：ngx_epoll_init、ngx_epoll_add_event、ngx_epoll_del_event、ngx_epoll_process_events
- 定时器管理：ngx_event_timer_rbtree，红黑树实现的时间轮
- Posted 事件队列：ngx_posted_accept_events、ngx_posted_events
- 惊群问题与 accept_mutex 的历史与现状
- 源码关联：src/event/ngx_event.c、src/event/ngx_event_timer.c、src/event/modules/ngx_epoll_module.c
**实战目标**：在 ngx_epoll_process_events 中插入日志，打印每次 epoll_wait 返回的事件数与耗时。

---

## 第35章：HTTP 请求生命周期完整源码链路
**定位**：从 TCP 连接到 HTTP 响应的完整代码之旅。
**核心内容**：
- 连接建立：ngx_event_accept -> ngx_http_init_connection
- 请求读取：ngx_http_wait_request_handler -> ngx_http_process_request_line -> ngx_http_process_request_header
- 请求处理：ngx_http_handler -> Phase Handler 链（NGX_HTTP_POST_READ -> NGX_HTTP_CONTENT）
- 响应发送：ngx_http_output_filter（Header Filter Chain + Body Filter Chain）
- 请求收尾：ngx_http_finalize_request -> ngx_http_close_request
- ngx_http_request_t 结构体关键字段解析
- 源码关联：src/http/ngx_http_request.c、src/http/ngx_http_core_module.c
**实战目标**：在关键函数插入 ngx_log_error，追踪一个完整请求的处理流程，输出调用链日志。

---

## 第36章：Upstream 源码——连接池与负载均衡
**定位**：理解反向代理的发动机。
**核心内容**：
- ngx_http_upstream_t 结构体：peer、request_bufs、conf、headers_in
- Upstream 初始化：ngx_http_upstream_init -> ngx_http_upstream_connect
- 负载均衡初始化：ngx_http_upstream_init_round_robin、ngx_http_upstream_init_hash
- 连接获取：ngx_http_upstream_get_peer、ngx_http_upstream_free_peer
- 请求发送与响应接收：ngx_http_upstream_send_request、ngx_http_upstream_process_header
- 故障转移：ngx_http_upstream_next 的决策逻辑
- 源码关联：src/http/ngx_http_upstream.c、src/http/ngx_http_upstream_round_robin.c、src/http/modules/ngx_http_upstream_hash_module.c
**实战目标**：修改 Round Robin 算法，实现基于后端响应时间的自适应权重调整，并验证效果。

---

## 第37章：Filter 链机制与数据流处理源码
**定位**：理解 Nginx 的响应管道模型。
**核心内容**：
- Filter Chain 架构：ngx_http_output_filter、ngx_http_header_filter、ngx_http_body_filter
- Header Filter 链：ngx_http_not_modified_filter -> ngx_http_headers_filter -> ngx_http_chunked_filter -> ngx_http_header_filter
- Body Filter 链：ngx_http_copy_filter -> ngx_http_range_filter -> ngx_http_gzip_filter -> ngx_http_write_filter
- 子请求与 postponed filter：ngx_http_postpone_filter_module
- 如何插入自定义 Filter：模块初始化顺序与链表插入
- 源码关联：src/http/ngx_http_core_module.c、src/http/ngx_http_copy_filter_module.c、src/http/ngx_http_write_filter_module.c、src/http/ngx_http_postpone_filter_module.c
**实战目标**：编写一个自定义 Body Filter，在响应体末尾注入一段 JavaScript 代码（用于前端监控埋点）。

---

## 第38章：自定义 HTTP 模块开发实战
**定位**：从源码读者到源码作者。
**核心内容**：
- HTTP 模块开发框架：模块定义、配置结构、命令表、上下文结构
- Handler 模块：在 NGX_HTTP_CONTENT_PHASE 拦截请求
- Filter 模块：修改响应头/响应体
- Upstream 模块：自定义负载均衡算法
- 模块编译：config 文件编写与 --add-module
- 调试技巧：GDB、日志、Valgrind
- 源码关联：src/http/ngx_http_config.h、src/core/ngx_module.h
**实战目标**：开发一个完整的 Nginx 模块 ngx_http_hello_counter，统计每个 IP 的访问次数，支持配置开关和持久化存储。

---

## 第39章：百万并发调优与 QUIC/HTTP3 源码剖析
**定位**：极端场景下的性能极限与协议前沿。
**核心内容**：
- 系统级调优：ulimit、net.core.somaxconn、tcp_tw_reuse、tcp_fastopen
- Nginx 参数调优：worker_processes、worker_connections、multi_accept、use epoll
- 零拷贝：sendfile、sendfile_max_chunk、tcp_nopush 的协同
- 性能剖析：perf、bpftrace、火焰图生成
- QUIC 协议栈：ngx_event_quic.c 的连接管理、帧处理、拥塞控制
- HTTP/3 的 0-RTT 与连接迁移
- 源码关联：src/event/quic/、src/core/nginx.c
**实战目标**：使用 wrk/wrk2 压测至单实例百万并发，生成 CPU 火焰图定位热点函数；搭建 HTTP/3 实验环境并抓包分析。

---

## 第40章：【高级篇综合实战】从零构建高性能 API 网关
**定位**：融会贯通高级篇知识，产出可交付的生产级组件。
**核心内容**：
- 场景：为一家金融科技公司自研 API 网关，替代商业方案
- 架构设计：Nginx 核心 + 自定义模块（认证、限流、路由、日志）+ 共享内存（slab + rbtree）
- 功能实现：
  - 动态路由：基于 Host + Path + Header 的多维度匹配（radix tree）
  - 分布式限流：基于共享内存的令牌桶（每秒 10 万 QPS）
  - JWT 校验：OpenSSL 异步非阻塞验签
  - 请求染色：TraceID 注入与全链路追踪
- 性能指标：单实例 10 万 QPS、P99 < 5ms、内存 < 2GB
- 部署方案：热升级、多活、蓝绿发布

---

# 附录与资源

## 附录 A：源码阅读路线图
1. 入口：src/core/nginx.c 的 main 函数
2. 初始化：ngx_init_cycle -> ngx_http_init -> ngx_event_init
3. 运行时：ngx_process_events_and_timers -> ngx_http_request_handler
4. 收尾：ngx_http_finalize_request -> ngx_http_close_connection

## 附录 B：编译调试指南
- 带 debug 的编译参数：--with-debug --with-cc-opt=\"-O0 -g\"
- GDB 常用断点：ngx_http_process_request、ngx_http_upstream_connect
- 日志级别：debug、debug_core、debug_alloc、debug_mutex、debug_event、debug_http、debug_mail、debug_stream

## 附录 C：推荐工具链
- 压测：wrk、wrk2、ab、locust、hey
- 抓包：tcpdump、Wireshark、tshark
- 剖析：perf、bcc/bpftrace、flamegraph
- 容器：Docker、Kubernetes、Helm
- 监控：Prometheus、Grafana、Jaeger

## 附录 D：思考题参考答案索引
- 基础篇思考题答案：见各章末尾或本附录对应小节
- 中级篇思考题答案：见各章末尾或本附录对应小节
- 高级篇思考题答案：见各章末尾或本附录对应小节

---

> **版权声明**：本专栏基于 Nginx 1.31.0 官方源码（BSD-2-Clause License）编写，所有源码引用均遵循原许可证条款。
