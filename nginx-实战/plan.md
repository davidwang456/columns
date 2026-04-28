# 7 天 Nginx 学习计划

> 基于《Nginx 源码剖析与实战修炼》专栏 40 章内容，聚焦最核心的实战能力，每天都有可验证的成果。

---

## 第 1 天：环境搭建与架构认知

**对应章节**：第 1、2 章

| 学习内容 | 要点 |
|---------|------|
| master-worker 进程模型 | 信号处理、热升级机制、模块化架构 |
| 源码目录结构 | `src/core/`、`src/event/`、`src/http/` 职责 |
| 源码编译安装 | `configure --with-debug`、`--add-module` |

**✅ 可验证成果**：
- `nginx -V` 输出完整编译参数和版本号
- `ps aux | grep nginx` 看到 1 个 master + N 个 worker 进程
- 用 `kill -HUP` 触发 reload，用 `kill -USR2` 触发热升级，进程 ID 变化可观察

---

## 第 2 天：配置文件与静态资源服务

**对应章节**：第 3、5 章

| 学习内容 | 要点 |
|---------|------|
| 配置语法与上下文 | main/events/http/server/location 嵌套规则 |
| location 匹配优先级 | `=` → `^~` → `~` / `~*` → 前缀匹配 |
| 静态文件服务 | `root` vs `alias`、`try_files`、`autoindex`、`sendfile` |

**✅ 可验证成果**：
- 搭建一个静态站点，包含多级 location（如 `/api/`、`/images/`、`/`）
- `curl -I http://localhost/` 返回正确的 `Content-Type` 和 `Last-Modified`
- 访问不存在的路径时，`try_files` 能正确 fallback 到 `index.html`（SPA history 模式）
- `nginx -t` 语法检查通过

---

## 第 3 天：反向代理与负载均衡

**对应章节**：第 6、7 章

| 学习内容 | 要点 |
|---------|------|
| 反向代理基础 | `proxy_pass` 带/不带斜杠的 URI 传递规则 |
| 请求头透传 | `proxy_set_header`（Host、X-Real-IP、X-Forwarded-For） |
| 负载均衡 | 加权 Round Robin、IP Hash、`backup`/`down` 参数 |

**✅ 可验证成果**：
- 启动 2-3 个后端服务（如 `python -m http.server` 不同端口）
- 配置 upstream + proxy_pass，`curl` 多次请求，观察到轮询分发
- 切换为 IP Hash，同一 IP 始终路由到同一后端（会话保持）
- 停掉一个后端，验证请求自动跳过故障节点

---

## 第 4 天：URL 重写与 HTTPS 安全

**对应章节**：第 9、12 章

| 学习内容 | 要点 |
|---------|------|
| Rewrite 模块 | `rewrite` + regex + flag（last/break/redirect/permanent） |
| 301 重定向 | 旧 URL → 新 URL 的迁移规则 |
| SSL/TLS 配置 | 自签证书、`ssl_protocols`、`ssl_ciphers`、HSTS |
| HTTP 自动跳转 HTTPS | 80 端口 return 301 到 443 |

**✅ 可验证成果**：
- 生成自签名证书，配置 HTTPS 站点
- `curl -k https://localhost/` 成功访问
- `curl -I http://localhost/` 返回 `301 Moved Permanently` 到 HTTPS
- 编写 5+ 条 rewrite 规则（如 `/old-blog/123` → `/posts/123`），逐个 `curl` 验证

---

## 第 5 天：性能优化——压缩与缓存

**对应章节**：第 11、14 章

| 学习内容 | 要点 |
|---------|------|
| Gzip 压缩 | `gzip_types`、`gzip_min_length`、`gzip_comp_level`、`gzip_static` |
| 浏览器缓存 | `expires`、`Cache-Control`、`ETag`、`Last-Modified` |
| 代理缓存 | `proxy_cache_path`、`proxy_cache_key`、`proxy_cache_valid` |

**✅ 可验证成果**：
- 对比开启/关闭 Gzip：`curl -s -o /dev/null -w "%{size_download}"` 测量 body 大小，至少减少 60%
- 静态资源配置 `expires 1y`，浏览器第二次访问返回 `304 Not Modified`
- 配置 proxy_cache，连续请求同一 API，第二次返回 `X-Cache-Status: HIT`
- 用 `curl -H "Accept-Encoding: gzip"` 验证 Gzip 生效

---

## 第 6 天：限流熔断与高可用

**对应章节**：第 19、23 章

| 学习内容 | 要点 |
|---------|------|
| 限流（漏桶） | `limit_req_zone` + `limit_req`、`burst`、`nodelay` |
| 连接数限制 | `limit_conn_zone` + `limit_conn` |
| 故障转移 | `max_fails`、`fail_timeout`、`proxy_next_upstream` |

**✅ 可验证成果**：
- 对某接口配置 `rate=5r/s`，用 `ab -n 50 -c 10` 并发压测，观察到部分请求返回 `503`
- 添加 `burst=10 nodelay` 后，同样压测不再出现 503（排队消费）
- 设置 `max_fails=2 fail_timeout=30s`，手动 kill 一个后端，30 秒内该节点被自动摘除
- `tail -f error.log` 能看到节点被标记为 down 的日志

---

## 第 7 天：综合实战——搭建企业级站点

**对应章节**：第 13、16 章（综合）

| 学习内容 | 要点 |
|---------|------|
| 整合前 6 天所有能力 | 静态服务 + 反向代理 + HTTPS + Gzip + 缓存 + 限流 |
| 结构化日志 | JSON 格式 `log_format`，`access_log` 条件日志 |
| 压测验证 | 用 `wrk` 或 `ab` 进行性能基准测试 |

**✅ 可验证成果**：
- 一份完整的 `nginx.conf`，包含：
  - 静态资源（`expires 1y` + Gzip）
  - 反向代理到后端 API（带缓存 + 限流 10r/s）
  - HTTPS（A+ 级配置：TLS 1.2+、HSTS、OCSP Stapling）
  - JSON 结构化日志（可用 `jq` 解析）
- `wrk -t4 -c100 -d30s https://localhost/` 压测结果：QPS > 1000，错误率 < 1%
- 用 `jq '.status' access.log | sort | uniq -c` 分析状态码分布

---

## 学习节奏建议

| 时间分配 | 内容 |
|---------|------|
| 上午 1.5h | 阅读对应章节 + 理解概念 |
| 下午 2h | 动手配置 + 调试验证（产出可验证成果） |
| 晚上 30min | 记录踩坑笔记 + 复盘今日成果截图 |

> 每天结束后，你应该能拿出一张截图或一段终端输出来证明你完成了当天的目标。
