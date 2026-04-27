# 第5章：静态资源服务与 Location 匹配规则

> 源码关联：src/http/ngx_http_core_module.c、src/http/modules/ngx_http_static_module.c、src/http/modules/ngx_http_autoindex_module.c

---

## 1. 项目背景

鲜果园的前端团队最近完成了商城页面的大改版，新上线的 SPA（单页应用）使用了 React 框架，打包后生成了一堆带 hash 的文件：`main.a3f2b1c.js`、`vendor.9e8d7a6.js`、`styles.4c5d8e2.css`。后端新人小胖负责部署，他直接把文件丢到了 Tomcat 的 webapps 目录下，结果产品经理在验收时崩溃了：

- 图片加载慢得像拨号上网
- 刷新页面时 404 错误频出
- 旧版本浏览器的缓存导致样式错乱
- 直接访问 `/orders/123` 时 Tomcat 报找不到文件

CTO 老张把小李叫进办公室："咱们不是有 Nginx 吗？为什么不拿 Nginx 做静态资源服务器？Tomcat 是跑 Java 的，让它干这事儿就像让外科医生去炒菜——能干，但不好吃。"

小李恍然大悟。Nginx 的静态资源服务看似简单，实则暗藏玄机：location 匹配规则、try_files 的 fallback 机制、sendfile 零拷贝、浏览器缓存策略、history 路由支持……每一项都直接影响用户体验和服务器性能。本章将带领团队从零搭建一个生产级的静态资源服务器，彻底告别 Tomcat 的"慢和乱"。

---

## 2. 项目设计

**场景**：鲜果园的前端-后端联调室，白板前站着小胖、小白和大师，屏幕上显示着 404 报错页面。

---

**小胖**：（挠头）大师，我把前端打包的 dist 目录放到 Nginx 的 html 目录下了，但访问 `/orders/123` 就 404。这是咋回事？

**大师**：（看了一眼配置）你的 location 只匹配了 `/`，请求 `/orders/123` 时，Nginx 会去文件系统找 `/var/www/orders/123`，当然找不到。

**小胖**：不对啊，React 的路由是在前端处理的，Nginx 应该不管才对。

**大师**：浏览器地址栏里输入 `/orders/123`，浏览器会把这个路径发给 Nginx。Nginx 默认认为这是一个服务器端的文件路径。如果你不做特殊处理，它当然会去磁盘上找这个文件。你要告诉 Nginx：如果文件不存在，就返回 index.html，让 React Router 自己去处理。

**小白**：这就是 SPA 的 history 路由模式问题吧？

**大师**：对。解决方案是 `try_files` 指令：`try_files $uri $uri/ /index.html;`。它的意思是：先尝试找 URI 对应的文件，如果找不到，尝试找 URI 对应的目录，如果还找不到，就返回 `/index.html`。

**小胖**：哦！这不就是"兜底"吗？先试试，不行就回主页。

**大师**：正是。但 `try_files` 只是静态资源服务的冰山一角。你们先把 location 匹配规则搞明白，这是地基。

**大师**：（在白板上写下 location 的五种匹配类型）

| 修饰符 | 匹配类型 | 优先级 | 示例 |
|--------|---------|--------|------|
| `=` | 精确匹配 | 最高（1） | `location = / { ... }` |
| `^~` | 前缀匹配（停止正则搜索） | 高（2） | `location ^~ /images/ { ... }` |
| `~` | 正则匹配（区分大小写） | 中（3） | `location ~ \.css$ { ... }` |
| `~*` | 正则匹配（不区分大小写） | 中（3） | `location ~* \.jpg$ { ... }` |
| 无 | 普通前缀匹配 | 低（4） | `location /api/ { ... }` |

**大师**：匹配顺序是：先找 `=`，再找 `^~`，再找普通前缀（最长的优先），最后按配置文件顺序找正则。如果一个请求能同时匹配多个 location，最终生效的是优先级最高的那个。

**小白**：那 `^~` 和普通的 `/images/` 有什么区别？

**大师**：`^~` 是"霸道前缀"——一旦匹配成功，就**不再尝试后面的正则 location**。而普通前缀匹配成功后，还会继续检查后面的正则，如果正则也匹配，正则胜出。比如：

```nginx
location /images/ { ... }           # 普通前缀
location ~* \.png$ { ... }          # 正则
```

请求 `/images/logo.png`，先匹配 `/images/`，然后还会检查 `\.png$`，最终生效的是正则 location。但如果把 `/images/` 改成 `^~ /images/`，正则就被跳过了。

**小胖**：`alias` 和 `root` 到底有什么区别？我看网上说的都云里雾里的。

**大师**：（画了两张图）

- `root /var/www;` + `location /images/ { ... }`：请求 `/images/logo.png` 时，Nginx 找的是 `/var/www/images/logo.png`——**root 会把 location 匹配的路径追加到 root 后面**。

- `location /images/ { alias /var/www/static/; }`：请求 `/images/logo.png` 时，Nginx 找的是 `/var/www/static/logo.png`——**alias 会把 location 匹配的部分替换为 alias 指定的路径**。

**小白**：所以 `alias` 后面要带斜杠，`root` 后面带不带都行？

**大师**：`alias` 必须带斜杠！如果你写 `alias /var/www/static;`（没有末尾斜杠），请求 `/images/logo.png` 会变成 `/var/www/staticlogo.png`——少了一个斜杠，文件就找不到了。这是 Nginx 配置中最经典的坑之一。

**小胖**：那静态资源的性能优化呢？我听说 Nginx 有个 sendfile 很猛。

**大师**：`sendfile` 是 Linux 提供的零拷贝系统调用。正常的数据传输流程是：磁盘 -> 内核缓冲区 -> 用户空间缓冲区 -> 内核 socket 缓冲区 -> 网卡。而 sendfile 直接把数据从内核的文件缓冲区发送到 socket 缓冲区，**跳过了用户空间的两次拷贝**，CPU 占用和延迟都大幅降低。

**小白**：`tcp_nopush` 和 `tcp_nodelay` 又是干嘛的？

**大师**：这是一对"矛盾"的优化指令。`tcp_nopush on` 配合 sendfile，会把多个小文件合并成一个大数据包发送，减少网络包数量；`tcp_nodelay on` 则相反，它会立刻发送小数据包，减少延迟。它们的使用场景不同：

- 静态大文件（视频、图片）：`sendfile on; tcp_nopush on; tcp_nodelay off;`
- 动态小数据（API、HTML）：`sendfile off; tcp_nodelay on;`

**小胖**：那浏览器缓存呢？前端总抱怨用户看到的是旧版本。

**大师**：这就涉及 HTTP 缓存头了。对于带 hash 的文件名（如 `main.a3f2b1c.js`），文件名本身就代表了版本，可以设置极长的缓存时间：`expires 1y; add_header Cache-Control "public, immutable";`。对于不带 hash 的文件（如 index.html），则应该禁用缓存或设置极短缓存，确保用户总能拿到最新版本。

---

## 3. 项目实战

### 环境准备

- **Nginx 版本**：1.31.0
- **前端产物**：React SPA 打包后的 dist 目录
- **工作目录**：`/var/www/xianguoyuan/`

### 步骤一：准备前端静态资源

```bash
# 创建目录结构
sudo mkdir -p /var/www/xianguoyuan/{desktop,mobile,tablet,assets}

# 模拟前端打包产物（实际应由前端 CI 生成）
sudo tee /var/www/xianguoyuan/index.html << 'EOF'
<!DOCTYPE html>
<html>
<head><title>鲜果园 - 首页</title></head>
<body>
<h1>欢迎访问鲜果园</h1>
<script src="/assets/v1/main.a3f2b1c.js"></script>
</body>
</html>
EOF

sudo tee /var/www/xianguoyuan/desktop/index.html << 'EOF'
<!DOCTYPE html>
<html><head><title>鲜果园 - 桌面版</title></head>
<body><h1>鲜果园桌面版</h1></body></html>
EOF

sudo tee /var/www/xianguoyuan/mobile/index.html << 'EOF'
<!DOCTYPE html>
<html><head><title>鲜果园 - 移动版</title></head>
<body><h1>鲜果园移动版</h1></body></html>
EOF

sudo tee /var/www/xianguoyuan/tablet/index.html << 'EOF'
<!DOCTYPE html>
<html><head><title>鲜果园 - 平板版</title></head>
<body><h1>鲜果园平板版</h1></body></html>
EOF

# 模拟带 hash 的静态资源
sudo dd if=/dev/zero of=/var/www/xianguoyuan/assets/v1/main.a3f2b1c.js bs=1K count=100
sudo dd if=/dev/zero of=/var/www/xianguoyuan/assets/v1/styles.4c5d8e2.css bs=1K count=50

# 设置权限
sudo chown -R www-data:www-data /var/www/xianguoyuan
```

### 步骤二：编写 Nginx 静态资源配置

编辑 `/usr/local/nginx/conf/nginx.conf`：

```nginx
user www-data;
worker_processes auto;
error_log /var/log/nginx/error.log warn;
pid /var/run/nginx.pid;

events {
    use epoll;
    worker_connections 4096;
    multi_accept on;
}

http {
    include       /usr/local/nginx/conf/mime.types;
    default_type  application/octet-stream;

    # 日志格式
    log_format main '$remote_addr - $remote_user [$time_local] "$request" '
                    '$status $body_bytes_sent "$http_referer" '
                    '"$http_user_agent"';
    access_log /var/log/nginx/access.log main;

    # 零拷贝与 TCP 优化
    sendfile on;
    tcp_nopush on;
    tcp_nodelay on;

    # 连接保持
    keepalive_timeout 65;

    # Gzip 压缩（文本类资源）
    gzip on;
    gzip_vary on;
    gzip_proxied any;
    gzip_comp_level 6;
    gzip_types text/plain text/css text/xml application/json 
               application/javascript application/rss+xml 
               application/atom+xml image/svg+xml;

    # 设备类型识别
    map $http_user_agent $device_type {
        ~*iphone|android.*mobile    mobile;
        ~*ipad|android(?!.*mobile)  tablet;
        default                      desktop;
    }

    server {
        listen       80;
        server_name  www.xianguoyuan.com xianguoyuan.com;
        root         /var/www/xianguoyuan;
        index        index.html;

        # ========== 精确匹配：根路径 ==========
        location = / {
            # 根据设备类型重定向到不同首页
            if ($device_type = mobile) {
                rewrite ^ /mobile/index.html last;
            }
            if ($device_type = tablet) {
                rewrite ^ /tablet/index.html last;
            }
            try_files /desktop/index.html =404;
        }

        # ========== 带 hash 的静态资源（永久缓存） ==========
        location ~ ^/assets/v\d+/ {
            expires 1y;
            add_header Cache-Control "public, immutable";
            add_header X-Content-Type-Options "nosniff";
            access_log off;
        }

        # ========== 图片资源（长期缓存） ==========
        location ~* \.(jpg|jpeg|png|gif|ico|svg|webp)$ {
            expires 6M;
            add_header Cache-Control "public";
            access_log off;
        }

        # ========== CSS/JS（版本化缓存） ==========
        location ~* \.(css|js)$ {
            expires 1M;
            add_header Cache-Control "public";
        }

        # ========== 字体文件（跨域支持） ==========
        location ~* \.(woff|woff2|ttf|otf|eot)$ {
            expires 1y;
            add_header Cache-Control "public";
            add_header Access-Control-Allow-Origin "*";
        }

        # ========== SPA history 路由兜底 ==========
        location / {
            try_files $uri $uri/ /index.html;
        }

        # ========== 安全头 ==========
        add_header X-Frame-Options "SAMEORIGIN" always;
        add_header X-Content-Type-Options "nosniff" always;
        add_header Referrer-Policy "strict-origin-when-cross-origin" always;
    }
}
```

### 步骤三：语法验证与启动

```bash
# 测试配置
sudo /usr/local/nginx/sbin/nginx -t

# 启动或重载
sudo /usr/local/nginx/sbin/nginx -s reload
```

### 步骤四：功能验证

**4.1 设备适配测试**

```bash
# 模拟桌面访问
curl -H "User-Agent: Mozilla/5.0" http://localhost/
# 预期返回 desktop/index.html 的内容

# 模拟手机访问
curl -H "User-Agent: iPhone" http://localhost/
# 预期返回 mobile/index.html 的内容

# 模拟平板访问
curl -H "User-Agent: iPad" http://localhost/
# 预期返回 tablet/index.html 的内容
```

**4.2 SPA 路由测试**

```bash
# 直接访问前端路由（文件不存在，应返回 index.html）
curl -I http://localhost/orders/123
# 预期返回 200，且内容应为 index.html

# 访问真实存在的文件
curl -I http://localhost/assets/v1/main.a3f2b1c.js
# 预期返回 200，且 Cache-Control 包含 max-age=31536000
```

**4.3 缓存头验证**

```bash
# 验证静态资源缓存头
curl -I http://localhost/assets/v1/main.a3f2b1c.js | grep -i cache
# 预期：Cache-Control: public, immutable
# 预期：Expires: <一年后>

# 验证 HTML 不缓存（或短期缓存）
curl -I http://localhost/ | grep -i cache
# 预期无强缓存头（或较短的 max-age）
```

**4.4 零拷贝验证**

```bash
# 使用 strace 验证 sendfile 是否生效
sudo strace -e trace=sendfile -p $(pgrep -f "nginx: worker" | head -n 1) &
curl http://localhost/assets/v1/main.a3f2b1c.js > /dev/null
# 预期输出包含 sendfile 系统调用
```

### 步骤五：源码速览——静态文件处理

打开 `src/http/modules/ngx_http_static_module.c`，查看静态文件处理的核心逻辑：

```c
// src/http/modules/ngx_http_static_module.c
static ngx_int_t
ngx_http_static_handler(ngx_http_request_t *r)
{
    u_char                    *last;
    size_t                     root;
    ngx_str_t                  path;
    ngx_chain_t                out;
    ngx_open_file_info_t       of;
    ngx_http_core_loc_conf_t  *clcf;

    // 只处理 GET 和 HEAD 请求
    if (!(r->method & (NGX_HTTP_GET|NGX_HTTP_HEAD))) {
        return NGX_DECLINED;
    }

    // 忽略以 / 结尾的请求（应由 index 或 autoindex 处理）
    if (r->uri.data[r->uri.len - 1] == '/') {
        return NGX_DECLINED;
    }

    // 计算文件路径（root 或 alias 转换）
    last = ngx_http_map_uri_to_path(r, &path, &root, 0);
    if (last == NULL) {
        return NGX_HTTP_INTERNAL_SERVER_ERROR;
    }

    path.len = last - path.data;

    // 获取文件信息
    ngx_memzero(&of, sizeof(ngx_open_file_info_t));
    of.read_ahead = clcf->read_ahead;
    of.directio = clcf->directio;
    of.valid = clcf->open_file_cache_valid;
    of.min_uses = clcf->open_file_cache_min_uses;
    of.errors = clcf->open_file_cache_errors;
    of.events = clcf->open_file_cache_events;

    if (ngx_http_set_disable_symlinks(r, clcf, &path, &of) != NGX_OK) {
        return NGX_HTTP_INTERNAL_SERVER_ERROR;
    }

    if (ngx_open_cached_file(clcf->open_file_cache, &path, &of, r->pool) != NGX_OK) {
        // 文件不存在，返回 NGX_DECLINED，让后续 handler 处理（如 try_files）
        return NGX_DECLINED;
    }

    // ... 设置响应头、内容长度、Last-Modified ...

    // 构造输出链
    out.buf = ngx_calloc_buf(r->pool);
    out.buf->file = ngx_pcalloc(r->pool, sizeof(ngx_file_t));
    out.buf->file->fd = of.fd;
    out.buf->file->name = path;
    out.buf->file->log = r->connection->log;
    out.buf->file_pos = 0;
    out.buf->file_last = of.size;
    out.buf->in_file = 1;

    // 发送响应（如果 sendfile 开启，这里会触发零拷贝）
    return ngx_http_output_filter(r, &out);
}
```

**代码注释**：
- `ngx_http_map_uri_to_path` 负责将 URI 转换为物理文件路径，处理 root 和 alias 的逻辑差异
- `ngx_open_cached_file` 使用 Nginx 的文件缓存机制，避免重复 stat 系统调用
- `out.buf->in_file = 1` 标记这个 buffer 的数据来自文件而非内存，下游的 write_filter 会据此选择 sendfile 发送

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

| 维度 | Nginx 静态资源服务 | 传统 Tomcat/Apache 静态服务 |
|------|------------------|---------------------------|
| 并发性能 | 极高（事件驱动 + sendfile） | 中等（线程模型） |
| 内存占用 | 极低（不缓存文件内容到用户态） | 较高 |
| 缓存控制 | 精细（按文件类型、按路径） | 较粗 |
| SPA 支持 | 原生支持（try_files fallback） | 需额外配置或代码 |
| 零拷贝 | 原生支持 sendfile | 通常不支持 |
| 压缩 | 原生 gzip 模块 | 通常需额外模块 |

### 适用场景

1. **前端 SPA 部署**：React/Vue/Angular 打包产物的托管，history 路由兜底
2. **CDN 源站**：作为 CDN 的回源服务器，提供高并发静态文件下载
3. **图片/视频服务器**：配合 sendfile 和合理的缓存策略，高效分发多媒体
4. **混合部署**：同一台服务器既跑 API（反向代理到后端），又托管前端静态资源
5. **离线包/APP 升级包分发**：大文件下载，配合断点续传（ngx_http_range_filter_module）

**不适用场景**：
1. **需要服务端渲染（SSR）的页面**：如 Next.js、Nuxt.js 的 SSR 模式，需要 Node.js 服务端执行
2. **动态生成内容的场景**：如根据用户权限动态生成图片水印，应由后端应用处理
3. **需要复杂文件操作的场景**：如文件上传后的即时缩略图生成，Nginx 本身不处理业务逻辑

### 注意事项

1. **alias 末尾必须带斜杠**：`alias /var/www/static/` 正确，`alias /var/www/static` 会导致路径拼接错误
2. **try_files 的最后一个参数是内部重定向**：`try_files $uri $uri/ /index.html;` 中的 `/index.html` 会触发 Nginx 内部重新匹配 location，如果 `/index.html` 被其他 location 拦截，结果可能不符合预期
3. **sendfile 与 gzip 不兼容**：如果同时开启 sendfile 和 gzip，Nginx 会自动对需要压缩的文件禁用 sendfile（因为压缩必须在用户态进行）
4. **Cache-Control "immutable" 要慎用**：只对文件名包含 hash 的资源使用，普通文件（如 index.html）不能设 immutable，否则用户永远无法获取新版本

### 常见踩坑经验

**案例一：SPA 刷新 404**
- **现象**：用户从首页进入正常，但直接刷新 `/orders/123` 时返回 404
- **根因**：Nginx 未配置 try_files fallback，把前端路由当成了文件路径
- **解决**：`location / { try_files $uri $uri/ /index.html; }`

**案例二：静态资源缓存不更新**
- **现象**：前端发布了新版本，但用户浏览器仍显示旧页面
- **根因**：index.html 被浏览器缓存，而 index.html 中引用的 JS/CSS 路径未变化
- **解决**：为 index.html 设置 `Cache-Control: no-cache`，同时为带 hash 的资源设置长期缓存；或者前端构建时启用 filename hashing

**案例三：alias 路径拼接错误导致文件泄露**
- **现象**：请求 `/images/../../../etc/passwd` 成功读取了系统文件
- **根因**：早期 Nginx 版本（< 1.17.6）中 alias 与 location 长度计算存在漏洞（CVE-2021-23017）
- **解决**：升级 Nginx 到最新稳定版，使用 `location /images/ { alias /var/www/images/; }` 时确保末尾斜杠正确，并启用 `ngx_http_set_disable_symlinks`

### 推广计划提示

- **开发团队**：先复用本章最小配置与脚本，按“单变量”方式做参数实验并沉淀变更记录。
- **测试团队**：优先补齐异常路径用例（超时、重试、限流、故障转移），并固化回归清单。
- **运维团队**：将监控阈值、告警策略与回滚脚本纳入发布流程，确保高峰期可快速止损。
- **协作顺序建议**：开发先完成方案基线 -> 测试做功能/压力/故障验证 -> 运维执行灰度与上线守护。

### 思考题

1. **进阶题**：`try_files` 指令的最后一个参数如果是 URI（如 `/index.html`），会触发 Nginx 的内部重定向（internal redirect）。请从源码角度分析：内部重定向后，Nginx 是如何重新进入 location 匹配流程的？`r->internal` 标志在此过程中起什么作用？

2. **进阶题**：`sendfile` 零拷贝在 Nginx 中是如何与 `ngx_http_write_filter_module` 协作的？如果客户端请求带有 `Range: bytes=0-1023` 头，sendfile 还能使用吗？请结合 `ngx_http_range_filter_module.c` 的源码逻辑分析。

> 答案提示：第 1 题关注 `ngx_http_internal_redirect` 函数和 `ngx_http_core_find_location` 的重新调用；第 2 题涉及 range filter 对 in_file buffer 的处理，以及 `ngx_output_chain` 中的 sendfile 判定逻辑。

---

> **下一章预告**：我们将进入 Nginx 最经典的使用场景——反向代理。从 proxy_pass 的 URI 传递规则到请求头改写，从缓冲区调优到超时重试，搭建你的第一个 Nginx 反向代理集群。
