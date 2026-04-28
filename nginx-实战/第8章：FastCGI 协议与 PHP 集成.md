# 第8章：FastCGI 协议与 PHP 集成

> 源码关联：src/http/modules/ngx_http_fastcgi_module.c

---

## 1. 项目背景

鲜果园的官网一直使用静态 HTML 页面，但随着业务扩展，需要上线会员系统、优惠券中心和订单追踪功能。CTO 老张拍板：上 PHP！团队里正好有位老王是 PHP 老手，三天就写出了会员中心的原型。然而，当他把代码部署到 Nginx 上时，问题一个接一个地冒了出来：

- 浏览器访问 `index.php` 时，Nginx 直接把 PHP 源码当文本下载了，而不是执行
- 配置了 `fastcgi_pass` 后，PHP 页面显示 `File not found`
- 上传头像时，PHP 报 `$_FILES` 为空，但 Nginx 日志显示请求已到达
- 高峰期偶尔出现 502 Bad Gateway，刷新一下又好了

老王急得满头大汗："我在 Apache 上配了十年 PHP，从来没这些问题！Nginx 怎么这么难搞？"

架构师小李解释道："Apache 有 mod_php，PHP 解释器直接跑在 Apache 进程里。但 Nginx 不走这条路，它通过 **FastCGI 协议** 与 PHP-FPM 通信。这个协议比传统的 CGI 高效得多，但配置也更讲究。"

FastCGI（Fast Common Gateway Interface）是 CGI 的升级版本。传统 CGI 每来一个请求就 fork 一个进程，请求结束进程销毁，开销巨大。FastCGI 则让 PHP-FPM 进程长期驻留内存，Nginx 通过 TCP 或 Unix Socket 把 HTTP 请求封装成 FastCGI 协议帧发送给 PHP-FPM，PHP-FPM 执行完再把响应发回 Nginx。这个过程中，Nginx 充当的是"传话筒"，而 PHP-FPM 才是真正的"执行者"。

本章将从鲜果园的 PHP 部署之痛出发，深入讲解 FastCGI 协议的工作原理、Nginx 的 FastCGI 模块配置，以及 LNMP 架构的完整搭建流程。

---

## 2. 项目设计

**场景**：鲜果园的开发工位区，小胖、小白和大师围着显示器，屏幕上显示着 "File not found" 的报错。

---

**小胖**：（抓头发）大师，我在 Nginx 里配了 `fastcgi_pass 127.0.0.1:9000;`，但一访问 PHP 页面就报 404。PHP-FPM 明明在跑啊！这不跟食堂打饭一样吗——菜已经做好了，窗口却说没这道菜？

**大师**：（走过来）这是 FastCGI 配置中最经典的坑。Nginx 和 PHP-FPM 是两个独立的进程，Nginx 收到请求后，必须把"去哪找文件"这个信息通过 FastCGI 参数传递给 PHP-FPM。关键参数就是 `SCRIPT_FILENAME`。

**小白**：那为什么 Apache 不用设这个参数？

**大师**：因为 mod_php 跑在 Apache 进程内部，Apache 自己就知道文件在哪。但 Nginx 和 PHP-FPM 是跨进程通信，Nginx 必须显式告诉 PHP-FPM：你要执行的文件是 `/var/www/html/index.php`。

**大师**：（写下标准配置）

```nginx
location ~ \\.php$ {
    fastcgi_pass   127.0.0.1:9000;
    fastcgi_index  index.php;
    fastcgi_param  SCRIPT_FILENAME  $document_root$fastcgi_script_name;
    include        fastcgi_params;
}
```

这里 `SCRIPT_FILENAME` 由两部分组成：`$document_root`（也就是 `root` 指令的值）和 `$fastcgi_script_name`（URI 中的脚本名称）。拼接起来就是完整的物理文件路径。

**技术映射**：跨进程通信需要显式约定接口契约，Nginx 和 PHP-FPM 之间的契约就是 FastCGI 参数。就像食堂打饭，窗口和厨房是两个地方，你必须把菜单传到厨房才行。

**小白**：那 `fastcgi_params` 又是什么？

**大师**：`fastcgi_params` 是 Nginx 自带的参数集合，定义了常用的 CGI/FastCGI 环境变量，比如 `QUERY_STRING`、`REQUEST_METHOD`、`CONTENT_TYPE`、`REMOTE_ADDR` 等。PHP 依赖这些变量来获取请求信息。

---

**小胖**：还有一个问题，用户上传头像时，PHP 的 `$_FILES` 数组是空的，但 Nginx access_log 显示请求确实到了。

**大师**：这是因为 Nginx 默认会把整个请求体缓冲到磁盘，然后再转发给 PHP-FPM。但 PHP-FPM 接收文件上传时，需要从标准输入（stdin）读取数据。如果 Nginx 的 `client_body_buffer_size` 太小，或者 `client_max_body_size` 限制了上传大小，就会导致文件上传失败。

**小胖**：那怎么解决？感觉就像食堂阿姨说我打了饭，但餐盘是空的……

**大师**：三个关键点：

1. `client_max_body_size`：必须大于允许上传的最大文件大小
2. `client_body_temp_path`：确保磁盘空间充足，权限正确
3. `fastcgi_request_buffering`：PHP 场景建议开启（默认就是开启的），让 Nginx 先完整接收请求再转发给 PHP-FPM

```nginx
client_max_body_size 20M;
client_body_buffer_size 128k;
```

**小白**：502 Bad Gateway 又是怎么回事？时有时无的。

**大师**：502 表示 Nginx 无法从 PHP-FPM 获取有效响应。常见原因：

1. **PHP-FPM 进程耗尽**：所有 worker 进程都在忙，新请求进入等待队列，超时后 Nginx 返回 502
2. **PHP 执行超时**：PHP 脚本执行时间超过 `max_execution_time`，PHP-FPM 杀死了进程
3. **PHP-FPM 崩溃**：某个 PHP 扩展导致段错误（SIGSEGV），worker 进程崩溃重启
4. **连接数不足**：PHP-FPM 的 `pm.max_children` 设置太小

**技术映射**：502 就像是食堂的厨房忙不过来——厨师要么全在炒菜顾不上，要么某个灶台炸了，要么备菜工不够。

**小白**：PHP-FPM 的进程管理模式应该怎么配？

**大师**：PHP-FPM 有三种进程管理模式：

| 模式 | 说明 | 适用场景 |
|------|------|---------|
| `static` | 固定子进程数 | 内存充足、流量稳定的场景 |
| `dynamic` | 动态调整子进程数 | 大多数生产环境 |
| `ondemand` | 按需创建，空闲时销毁 | 低流量、内存敏感的场景 |

对于鲜果园这种有峰谷流量的电商网站，推荐 `dynamic`：

```ini
pm = dynamic
pm.max_children = 50          ; 最大子进程数
pm.start_servers = 10          ; 启动时创建的子进程数
pm.min_spare_servers = 5       ; 空闲进程最小值
pm.max_spare_servers = 20      ; 空闲进程最大值
pm.max_requests = 500          ; 每个子进程处理多少请求后重启（防内存泄漏）
```

**小白**：还有一个问题，PATH_INFO 是什么？有些框架（如 Laravel）的路由依赖它。

**大师**：`PATH_INFO` 是 CGI 标准中的一个环境变量，表示脚本名称之后的额外路径信息。比如请求 `/index.php/user/profile`，`SCRIPT_NAME` 是 `/index.php`，`PATH_INFO` 就是 `/user/profile`。PHP 框架常用 PATH_INFO 来实现优雅路由。

Nginx 的 `fastcgi_split_path_info` 指令可以自动分割：

```nginx
location ~ ^(.+\\.php)(.*)$ {
    fastcgi_split_path_info ^(.+\\.php)(.*)$;
    fastcgi_param  PATH_INFO  $fastcgi_path_info;
    fastcgi_param  SCRIPT_FILENAME  $document_root$fastcgi_script_name;
    fastcgi_pass   127.0.0.1:9000;
}
```

**技术映射**：PATH_INFO 就像信封上的"转交"字样——收件人（PHP 文件）先拿到信，再根据"转交"信息交给具体部门处理。

---

## 3. 项目实战

### 环境准备

- **Nginx 版本**：1.31.0
- **PHP 版本**：8.2+
- **PHP-FPM**：php8.2-fpm
- **操作系统**：Ubuntu 22.04

### 步骤一：安装 PHP 和 PHP-FPM

```bash
# 更新软件源并安装 PHP-FPM
sudo apt update
sudo apt install -y php8.2-fpm php8.2-mysql php8.2-gd php8.2-curl php8.2-mbstring

# 查看 PHP-FPM 状态
sudo systemctl status php8.2-fpm

# 查看 PHP-FPM 监听地址（默认 Unix Socket）
cat /etc/php/8.2/fpm/pool.d/www.conf | grep "listen ="
# 预期输出：listen = /run/php/php8.2-fpm.sock
```

### 步骤二：编写 Nginx FastCGI 配置

编辑 `/usr/local/nginx/conf/nginx.conf`：

```nginx
user www-data;
worker_processes auto;
error_log /var/log/nginx/error.log warn;
pid /var/run/nginx.pid;

events {
    use epoll;
    worker_connections 4096;
}

http {
    include       /usr/local/nginx/conf/mime.types;
    default_type  application/octet-stream;

    log_format main '$remote_addr - $remote_user [$time_local] "$request" '
                    '$status $body_bytes_sent "$http_referer" '
                    '"$http_user_agent"';

    access_log /var/log/nginx/access.log main;

    # 文件上传限制
    client_max_body_size 20M;
    client_body_buffer_size 128k;

    server {
        listen       80;
        server_name  php.xianguoyuan.com;
        root         /var/www/php;
        index        index.php index.html;

        location / {
            try_files $uri $uri/ =404;
        }

        # PHP 处理
        location ~ \\.php$ {
            fastcgi_pass   unix:/run/php/php8.2-fpm.sock;
            fastcgi_index  index.php;
            fastcgi_param  SCRIPT_FILENAME  $document_root$fastcgi_script_name;
            include        fastcgi_params;

            # FastCGI 优化参数
            fastcgi_connect_timeout 300s;
            fastcgi_send_timeout    300s;
            fastcgi_read_timeout    300s;
            fastcgi_buffer_size     64k;
            fastcgi_buffers         4 64k;
        }

        # 禁止访问敏感文件
        location ~ /\\.(git|svn|htaccess|env) {
            deny all;
        }
    }
}
```

### 步骤三：创建 PHP 测试文件

```bash
# 创建网站根目录
sudo mkdir -p /var/www/php
sudo chown -R www-data:www-data /var/www/php

# 创建 phpinfo 页面
sudo tee /var/www/php/index.php << 'EOF'
<?php
phpinfo();
EOF

# 创建文件上传测试页面
sudo tee /var/www/php/upload.php << 'EOF'
<?php
header('Content-Type: application/json');
if ($_SERVER['REQUEST_METHOD'] === 'POST') {
    if (isset($_FILES['avatar'])) {
        $file = $_FILES['avatar'];
        echo json_encode([
            'success' => true,
            'name' => $file['name'],
            'size' => $file['size'],
            'type' => $file['type'],
            'tmp_name' => $file['tmp_name']
        ]);
    } else {
        echo json_encode(['success' => false, 'error' => 'No file uploaded']);
    }
} else {
    echo json_encode(['method' => $_SERVER['REQUEST_METHOD'], 'message' => 'Send POST with avatar']);
}
EOF

# 创建 PATH_INFO 测试页面
sudo tee /var/www/php/api.php << 'EOF'
<?php
header('Content-Type: application/json');
echo json_encode([
    'script_name' => $_SERVER['SCRIPT_NAME'] ?? '',
    'path_info' => $_SERVER['PATH_INFO'] ?? '',
    'request_uri' => $_SERVER['REQUEST_URI'] ?? '',
    'query_string' => $_SERVER['QUERY_STRING'] ?? ''
]);
EOF
```

### 步骤四：语法验证与启动

```bash
sudo /usr/local/nginx/sbin/nginx -t
sudo /usr/local/nginx/sbin/nginx -s reload
```

### 步骤五：功能验证

**5.1 测试 PHP 解析**

```bash
curl -s http://localhost/index.php | grep -o "PHP Version [0-9]\\+\\.[0-9]\\+"
# 预期输出：PHP Version 8.2
```

**5.2 测试文件上传**

```bash
# 创建测试图片
dd if=/dev/zero of=/tmp/test_avatar.jpg bs=1K count=500

# 上传文件
curl -X POST -F "avatar=@/tmp/test_avatar.jpg" http://localhost/upload.php
# 预期返回 JSON，包含 success: true 和 size: 512000
```

**5.3 测试 PATH_INFO**

先修改 Nginx 配置，添加 path_info 支持：

```nginx
location ~ ^(.+\\.php)(.*)$ {
    fastcgi_split_path_info ^(.+\\.php)(.*)$;
    fastcgi_pass   unix:/run/php/php8.2-fpm.sock;
    fastcgi_param  SCRIPT_FILENAME  $document_root$fastcgi_script_name;
    fastcgi_param  PATH_INFO  $fastcgi_path_info;
    include        fastcgi_params;
}
```

然后测试：

```bash
curl -s http://localhost/api.php/users/123?foo=bar
# 预期返回中包含 path_info: "/users/123" 和 query_string: "foo=bar"
```

**5.4 模拟 502 错误并排查**

```bash
# 停止 PHP-FPM
sudo systemctl stop php8.2-fpm

# 请求 PHP 页面
curl -I http://localhost/index.php
# 预期返回：HTTP/1.1 502 Bad Gateway

# 查看 Nginx 错误日志
sudo tail /var/log/nginx/error.log
# 应能看到 "connect() to unix:/run/php/php8.2-fpm.sock failed"

# 恢复 PHP-FPM
sudo systemctl start php8.2-fpm
```

**5.5 压测验证**

```bash
sudo apt install -y apache2-utils

# 对 PHP 页面压测
ab -n 5000 -c 100 http://localhost/index.php

# 观察 PHP-FPM 进程状态
sudo systemctl status php8.2-fpm
# 或使用 ps 查看进程数
ps aux | grep "php-fpm" | wc -l
```

### 步骤六：源码速览——FastCGI 模块

打开 `src/http/modules/ngx_http_fastcgi_module.c`，查看 FastCGI 请求构建的核心逻辑：

```c
// src/http/modules/ngx_http_fastcgi_module.c
static ngx_int_t
ngx_http_fastcgi_create_request(ngx_http_request_t *r)
{
    ngx_http_fastcgi_ctx_t       *f;
    ngx_http_fastcgi_loc_conf_t  *flcf;
    ngx_chain_t                  *cl;
    ngx_buf_t                    *b;

    flcf = ngx_http_get_module_loc_conf(r, ngx_http_fastcgi_module);
    f = ngx_http_get_module_ctx(r, ngx_http_fastcgi_module);

    // 构建 FastCGI 请求头（BEGIN_REQUEST 记录）
    // 包含 role (RESPONDER) 和 flags

    // 构建 FastCGI 参数（PARAMS 记录）
    // 遍历 fastcgi_param 配置，将每个参数编码为 FastCGI 帧
    // 例如：SCRIPT_FILENAME=/var/www/php/index.php

    // 构建 FastCGI 标准输入（STDIN 记录）
    // 将 HTTP 请求体封装为 FastCGI 数据帧发送给 PHP-FPM

    // FastCGI 协议帧格式：
    // +----------------+----------------+
    // | version (1)    | type (1)       |
    // +----------------+----------------+
    // | requestId (2)  | contentLength (2)|
    // +----------------+----------------+
    // | paddingLength (1) | reserved (1) |
    // +----------------+----------------+
    // | contentData (variable)          |
    // +---------------------------------+
    // | paddingData (variable)          |
    // +---------------------------------+

    return NGX_OK;
}
```

**代码注释**：
- FastCGI 协议基于二进制帧结构，每种帧类型（BEGIN_REQUEST、PARAMS、STDIN、STDOUT、STDERR、END_REQUEST）有固定的头部格式
- `ngx_http_fastcgi_create_request` 负责把 HTTP 请求转换为 FastCGI 协议帧序列
- PARAMS 帧携带所有 `fastcgi_param` 定义的键值对，PHP-FPM 通过解析这些参数重建 CGI 环境
- STDIN 帧携带 HTTP 请求体（POST 数据、文件上传内容），PHP 通过 `php://input` 和 `$_FILES` 访问

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

| 维度 | Nginx + PHP-FPM (FastCGI) | Apache + mod_php |
|------|--------------------------|-----------------|
| 内存占用 | 低（PHP-FPM 进程独立管理） | 高（每个 Apache 进程都加载 PHP） |
| 并发能力 | 高（事件驱动 + PHP-FPM 池） | 中（进程/线程模型） |
| 进程隔离 | 好（Nginx 和 PHP 进程分离） | 差（耦合在一起） |
| 配置复杂度 | 较高（需理解 FastCGI 协议） | 低（开箱即用） |
| 故障恢复 | 好（单个 PHP-FPM worker 崩溃不影响 Nginx） | 差（Apache 进程崩溃影响整个服务） |
| 动态重载 | 支持（PHP-FPM 支持 graceful reload） | 支持 |

### 适用场景

1. **LNMP/LAMP 架构**：WordPress、Drupal、Laravel、ThinkPHP 等 PHP 框架的标准部署方式
2. **高并发 Web 应用**：PHP-FPM 的进程池可以动态伸缩，应对流量峰谷
3. **微服务中的遗留 PHP 系统**：通过 Nginx 代理将 PHP 服务接入现代架构
4. **多版本 PHP 共存**：不同站点使用不同版本的 PHP-FPM，通过 Nginx 路由隔离
5. **文件上传服务**：利用 Nginx 的 client_body_buffer 优化大文件上传性能

**不适用场景**：
1. **纯静态网站**：不需要 PHP 解析，直接用 Nginx 静态服务即可
2. **实时通信应用**：PHP 不适合 WebSocket 长连接，应使用 Node.js/Go 等
3. **计算密集型任务**：PHP 的执行模型不适合长时间运行的后台任务

### 注意事项

1. **SCRIPT_FILENAME 必须正确设置**：这是最常见的 404/No input file specified 错误根源
2. **PHP-FPM 的 listen 权限**：Unix Socket 模式下，确保 Nginx 的 worker 用户（如 www-data）有权限访问 `/run/php/php8.2-fpm.sock`
3. **pm.max_children 不要设置过大**：每个 PHP-FPM 进程占用 20-50MB 内存，50 个进程就是 1-2.5GB，要根据服务器内存合理配置
4. **文件上传目录权限**：`client_body_temp_path` 和 PHP 的 `upload_tmp_dir` 都要确保 www-data 用户可读写

### 常见踩坑经验

**案例一：File not found / No input file specified**
- **现象**：访问 PHP 页面显示 "File not found" 或 "No input file specified"
- **根因**：`fastcgi_param SCRIPT_FILENAME` 未设置或设置错误，导致 PHP-FPM 找不到文件
- **解决**：确保 `SCRIPT_FILENAME` 拼接了正确的物理路径：`$document_root$fastcgi_script_name`

**案例二：502 Bad Gateway 间歇性出现**
- **现象**：网站大部分正常，但偶尔出现 502，刷新后恢复
- **根因**：PHP-FPM 的 `pm.max_children` 太小，高峰期进程耗尽，新请求排队超时
- **解决**：增大 `pm.max_children`，或优化 PHP 代码减少执行时间，或开启 PHP-FPM 的慢日志定位慢请求

**案例三：大文件上传失败**
- **现象**：上传 5MB 以上的文件时，PHP 返回空或报错
- **根因**：Nginx 的 `client_max_body_size` 或 PHP 的 `upload_max_filesize` / `post_max_size` 限制
- **解决**：同步调整三个参数：`client_max_body_size`（Nginx）、`upload_max_filesize`（PHP）、`post_max_size`（PHP）

### 推广计划提示

- **开发团队**：先复用本章最小配置与脚本，按“单变量”方式做参数实验并沉淀变更记录。
- **测试团队**：优先补齐异常路径用例（超时、重试、限流、故障转移），并固化回归清单。
- **运维团队**：将监控阈值、告警策略与回滚脚本纳入发布流程，确保高峰期可快速止损。
- **协作顺序建议**：开发先完成方案基线 -> 测试做功能/压力/故障验证 -> 运维执行灰度与上线守护。

### 思考题

1. **进阶题**：FastCGI 协议中的 `FCGI_BEGIN_REQUEST`、`FCGI_PARAMS`、`FCGI_STDIN` 三种记录类型分别对应 HTTP 请求的哪些部分？请结合 `ngx_http_fastcgi_create_request` 的源码，描述 Nginx 是如何把 HTTP 请求映射到 FastCGI 协议帧的。

2. **进阶题**：当 `fastcgi_buffering` 开启时，Nginx 是如何接收 PHP-FPM 的响应并进行缓冲的？如果 PHP 脚本输出大量数据（如 100MB CSV 导出），关闭 `fastcgi_buffering` 会带来什么好处？请结合 `ngx_http_fastcgi_process_record` 的源码分析。

> 答案提示：第 1 题关注 FastCGI 协议帧的编码方式和 PARAMS 键值对的构造过程；第 2 题涉及 `ngx_chain_t` 缓冲区链表的管理和 `ngx_http_write_filter` 的流式输出机制。

---

> **下一章预告**：我们将进入 Nginx 的 Rewrite 世界，探索 URL 重写的魔法——从 SEO 优化到旧站迁移，从伪静态到请求重定向，让 Nginx 成为你的路由治理专家。
