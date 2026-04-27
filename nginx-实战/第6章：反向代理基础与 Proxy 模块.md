# 第6章：反向代理基础与 Proxy 模块

> 源码关联：src/http/modules/ngx_http_proxy_module.c、src/http/ngx_http_upstream.c

---

## 1. 项目背景

鲜果园的商城业务发展迅猛，单体 Java 应用已经不堪重负。CTO 老张决定拆分为微服务架构：订单服务、商品服务、用户服务分别独立部署。但新的问题接踵而至——前端页面需要同时调用三个后端服务，而浏览器的同源策略（Same-Origin Policy）让前端团队苦不堪言。更麻烦的是，三个服务各自暴露端口（8081、8082、8083），运维老王看着防火墙规则直摇头："这么多端口对外开放，安全审计怎么过？"

架构师小李提出了一个经典方案：在客户端和后端服务之间架设一层**反向代理**。所有前端请求统一发往 Nginx 的 80/443 端口，由 Nginx 根据 URL 路径分发到不同的后端服务。这样既能解决跨域问题，又能隐藏后端服务的真实地址，还能统一做日志、限流、SSL 终止。

但小胖在配置时遇到了一堆怪事：

- `proxy_pass http://localhost:8081/` 带斜杠和不带斜杠，行为完全不同
- 后端服务收到的 Host 头变成了 `localhost`，导致虚拟主机匹配失败
- 上传图片时，Nginx 报了 `413 Request Entity Too Large`
- 某个后端节点宕机时，Nginx 等了 60 秒才返回错误，用户体验极差

这些问题看似零散，实则都指向同一个核心——Nginx 的 Proxy 模块。本章将从鲜果园的拆分之痛出发，深入讲解反向代理的工作原理和配置细节，最终搭建一个生产级的多服务代理网关。

---

## 2. 项目设计

**场景**：鲜果园技术部，白板前画着微服务架构图，小胖、小白和大师正在讨论。

---

**小胖**：（指着架构图）大师，我按照文档配了 proxy_pass，但后端服务收到的 URL 总是不对。比如前端请求 `/api/orders/123`，后端收到的却是 `/orders/123`，前面的 `/api` 被吃掉了！

**大师**：这是因为 `proxy_pass` 的 URI 传递规则。来，我给你画张表：

| proxy_pass 写法                    | 前端请求          | 后端收到          | 说明                |
| -------------------------------- | ------------- | ------------- | ----------------- |
| `proxy_pass http://backend;`     | `/api/orders` | `/api/orders` | 原封不动传递            |
| `proxy_pass http://backend/;`    | `/api/orders` | `/orders`     | 去掉 location 匹配的部分 |
| `proxy_pass http://backend/v2/;` | `/api/orders` | `/v2/orders`  | 替换 location 匹配的部分 |

**小胖**：哦！带斜杠就替换，不带斜杠就原样传？

**大师**：准确地说，**带斜杠时，proxy_pass 的 URI 部分会替换掉 location 匹配的路径**。你的 location 是 `/api/`，proxy_pass 带了 `/`，所以 Nginx 把 `/api/` 去掉了，只把后面的 `orders/123` 发给后端。

**小白**：那如果我想保留 `/api` 呢？

**大师**：有两种方法：一是 `proxy_pass http://backend;`（不带斜杠），二是用 `proxy_pass http://backend/api/;` 显式指定替换后的前缀。

**小胖**：还有一个问题，后端服务说收到的 Host 头是 `localhost:8081`，但它的虚拟主机配置的是 `api.xianguoyuan.com`，匹配不上。

**大师**：这是 Proxy 模块的默认行为——Nginx 会把 Host 头设为 proxy_pass 中的域名。如果你在 proxy_pass 里写的是 IP:端口，Host 头就是那个 IP:端口。你需要显式设置：

```nginx
proxy_set_header Host $host;
```

这样后端收到的 Host 头就是用户原始请求中的域名。

**小白**：那 `X-Real-IP` 和 `X-Forwarded-For` 呢？我看很多文章都推荐设置这两个头。

**大师**：这是为了传递客户端的真实 IP。当请求经过 Nginx 反向代理后，后端服务看到的 `remote_addr` 其实是 Nginx 的 IP，而不是用户的真实 IP。通过这两个头可以传递原始信息：

```nginx
proxy_set_header X-Real-IP $remote_addr;
proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
```

`$proxy_add_x_forwarded_for` 会在已有的 X-Forwarded-For 值后面追加当前代理的 IP，形成完整的代理链路。

**小胖**：我上传图片时报 413，这是什么鬼？

**大师**：`413 Request Entity Too Large`，意思是请求体超过了 Nginx 的限制。Proxy 模块有两个关键参数：

- `client_max_body_size`：Nginx 允许客户端上传的最大请求体（默认 1MB）
- `proxy_request_buffering`：是否先把整个请求体缓冲到磁盘再发给后端

上传图片时，如果图片超过 1MB，就会触发 413。解决方法是：

```nginx
client_max_body_size 50M;
proxy_request_buffering off;  # 大文件上传时建议关闭，减少磁盘 IO 和延迟
```

**小白**：关闭 buffering 会不会影响性能？

**大师**：会有一点，但对于大文件上传来说，关闭 buffering 能让数据**流式传输**——客户端每发一块数据，Nginx 就立刻转发给后端，而不需要等整个文件接收完。这降低了延迟和磁盘占用。

**小胖**：最后一个问题，有个后端节点挂了，Nginx 等了整整一分钟才告诉我错误。能不能快一点？

**大师**：这是超时配置的问题。Proxy 模块有三组超时参数：

- `proxy_connect_timeout`：与后端建立 TCP 连接的超时（默认 60 秒）
- `proxy_send_timeout`：向后端发送数据的超时（默认 60 秒）
- `proxy_read_timeout`：等待后端响应的超时（默认 60 秒）

对于内网服务，连接超时通常 3-5 秒就够了；对于外网或跨可用区，可以适当放宽。

**大师**：还有 `proxy_next_upstream`，这是故障转移的关键。当后端返回特定错误码（如 502、503、504）或连接失败时，Nginx 可以自动尝试下一个后端节点。但要注意：**只应对幂等请求开启重试**（如 GET、HEAD），POST/PUT 请求重试可能导致数据重复写入。

---

## 3. 项目实战

### 环境准备

- **Nginx 版本**：1.31.0
- **后端服务**：三个 Python Flask 应用模拟微服务
- **操作系统**：Ubuntu 22.04 / WSL2

### 步骤一：启动模拟后端服务

```bash
# 创建三个简单的 Flask 应用
mkdir -p /var/www/backend/{orders,products,users}

# 订单服务（端口 8081）
cat > /var/www/backend/orders/app.py << 'PYEOF'
from flask import Flask, request
app = Flask(__name__)

@app.route('/orders/<id>')
def get_order(id):
    return {"order_id": id, "status": "paid", "from": "orders-service"}

@app.route('/orders', methods=['POST'])
def create_order():
    return {"result": "order_created", "size": request.content_length}, 201

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8081)
PYEOF

# 商品服务（端口 8082）
cat > /var/www/backend/products/app.py << 'PYEOF'
from flask import Flask
app = Flask(__name__)

@app.route('/products/<id>')
def get_product(id):
    return {"product_id": id, "name": "泰国金枕榴莲", "from": "products-service"}

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8082)
PYEOF

# 用户服务（端口 8083）
cat > /var/www/backend/users/app.py << 'PYEOF'
from flask import Flask
app = Flask(__name__)

@app.route('/users/<id>')
def get_user(id):
    return {"user_id": id, "name": "李小胖", "from": "users-service"}

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8083)
PYEOF

# 安装 Flask 并启动服务（需要 Python3）
pip3 install flask -q

nohup python3 /var/www/backend/orders/app.py > /tmp/orders.log 2>&1 &
nohup python3 /var/www/backend/products/app.py > /tmp/products.log 2>&1 &
nohup python3 /var/www/backend/users/app.py > /tmp/users.log 2>&1 &

# 验证后端服务
sleep 2
curl -s http://localhost:8081/orders/123
curl -s http://localhost:8082/products/456
curl -s http://localhost:8083/users/789
```

### 步骤二：编写 Nginx 反向代理配置

编辑 `/usr/local/nginx/conf/nginx.conf`：

```nginx
user www-data;
worker_processes auto;
error_log /var/log/nginx/error.log warn;

events {
    use epoll;
    worker_connections 4096;
}

http {
    include       /usr/local/nginx/conf/mime.types;
    default_type  application/octet-stream;

    log_format upstream_log '$remote_addr - $remote_user [$time_local] '
                            '"$request" $status $body_bytes_sent '
                            '"$http_referer" "$http_user_agent" '
                            'upstream=$upstream_addr '
                            'response_time=$upstream_response_time';

    access_log /var/log/nginx/access.log upstream_log;

    # 全局代理优化参数
    proxy_connect_timeout 5s;
    proxy_send_timeout 10s;
    proxy_read_timeout 30s;

    proxy_buffering on;
    proxy_buffer_size 4k;
    proxy_buffers 8 4k;
    proxy_busy_buffers_size 8k;

    # 上传文件大小限制
    client_max_body_size 50M;

    # 上游服务定义
    upstream orders_backend {
        server 127.0.0.1:8081 weight=5 max_fails=3 fail_timeout=30s;
        keepalive 32;
    }

    upstream products_backend {
        server 127.0.0.1:8082 weight=5 max_fails=3 fail_timeout=30s;
        keepalive 32;
    }

    upstream users_backend {
        server 127.0.0.1:8083 weight=5 max_fails=3 fail_timeout=30s;
        keepalive 32;
    }

    server {
        listen       80;
        server_name  api.xianguoyuan.com;

        # 通用代理头
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # ========== 订单服务代理 ==========
        location /api/orders/ {
            proxy_pass http://orders_backend/;
            proxy_http_version 1.1;
            proxy_set_header Connection "";

            # 订单创建接口（POST）不重试，避免重复下单
            limit_except GET HEAD {
                proxy_next_upstream off;
            }
        }

        # ========== 商品服务代理 ==========
        location /api/products/ {
            proxy_pass http://products_backend/;
            proxy_http_version 1.1;
            proxy_set_header Connection "";
        }

        # ========== 用户服务代理 ==========
        location /api/users/ {
            proxy_pass http://users_backend/;
            proxy_http_version 1.1;
            proxy_set_header Connection "";
        }

        # ========== 文件上传专用路径 ==========
        location /api/upload/ {
            proxy_pass http://orders_backend/;
            proxy_request_buffering off;
            proxy_buffering off;
            client_max_body_size 100M;
        }

        # ========== 健康检查 ==========
        location /health {
            access_log off;
            return 200 '{"status":"ok"}\n';
            add_header Content-Type application/json;
        }
    }
}
```

### 步骤三：语法验证与启动

```bash
sudo /usr/local/nginx/sbin/nginx -t
sudo /usr/local/nginx/sbin/nginx -s reload
```

### 步骤四：功能验证

**4.1 基本代理验证**

```bash
# 测试订单服务（注意：/api/orders/ 后面的路径会被传递给后端）
curl -s http://localhost/api/orders/123
# 预期返回：{"order_id": "123", "status": "paid", "from": "orders-service"}

# 测试商品服务
curl -s http://localhost/api/products/456
# 预期返回：{"product_id": "456", "name": "泰国金枕榴莲", ...}

# 测试用户服务
curl -s http://localhost/api/users/789
```

**4.2 请求头验证**

在后端服务中加入日志打印，验证收到的请求头：

```bash
# 查看后端日志
tail /tmp/orders.log
# 应能看到 Host=api.xianguoyuan.com, X-Real-IP=127.0.0.1 等头
```

**4.3 大文件上传测试**

```bash
# 生成 20MB 测试文件
dd if=/dev/zero of=/tmp/test_upload.bin bs=1M count=20

# 测试上传
curl -X POST -F "file=@/tmp/test_upload.bin" http://localhost/api/upload/test
# 预期返回 201，且 response 中包含 size=20971520
```

**4.4 故障转移测试**

```bash
# 停止订单服务
kill $(lsof -t -i:8081)

# 再次请求（Nginx 应返回 502）
curl -I http://localhost/api/orders/123
# 预期：HTTP/1.1 502 Bad Gateway

# 查看 Nginx 错误日志
sudo tail /var/log/nginx/error.log
# 应能看到 upstream connect failed 的记录

# 重新启动订单服务
nohup python3 /var/www/backend/orders/app.py > /tmp/orders.log 2>&1 &

# 再次请求，应恢复正常
curl -s http://localhost/api/orders/123
```

**4.5 压测验证**

```bash
sudo apt install -y apache2-utils

# 对代理接口进行压测
ab -n 10000 -c 1000 http://localhost/api/products/1

# 观察 Nginx 日志中的 upstream_response_time
tail /var/log/nginx/access.log | awk '{print $NF}'
```

### 步骤五：源码速览——Proxy 模块

打开 `src/http/modules/ngx_http_proxy_module.c`，查看 proxy_pass 的处理逻辑：

```c
// src/http/modules/ngx_http_proxy_module.c
static ngx_int_t
ngx_http_proxy_handler(ngx_http_request_t *r)
{
    ngx_int_t                   rc;
    ngx_http_upstream_t        *u;
    ngx_http_proxy_ctx_t       *ctx;
    ngx_http_proxy_loc_conf_t  *plcf;

    plcf = ngx_http_get_module_loc_conf(r, ngx_http_proxy_module);

    // 创建 upstream 上下文
    if (ngx_http_upstream_create(r) != NGX_OK) {
        return NGX_HTTP_INTERNAL_SERVER_ERROR;
    }

    u = r->upstream;

    // 设置 upstream 的 URI
    // 如果 proxy_pass 带有 URI（如 http://backend/），
    // 则用 proxy_pass 的 URI 替换 location 匹配部分
    if (plcf->uri.len) {
        u->uri = plcf->uri;  // 使用 proxy_pass 中指定的 URI
    } else {
        u->uri = r->uri;      // 原样传递请求 URI
    }

    // 设置请求头
    ngx_http_proxy_set_headers(r, u, plcf->headers);

    // 初始化 upstream 连接
    u->create_request = ngx_http_proxy_create_request;
    u->reinit_request = ngx_http_proxy_reinit_request;
    u->process_header = ngx_http_proxy_process_status_line;
    u->abort_request = ngx_http_proxy_abort_request;
    u->finalize_request = ngx_http_proxy_finalize_request;

    // 启动 upstream 连接
    rc = ngx_http_read_client_request_body(r, ngx_http_upstream_init);
    if (rc >= NGX_HTTP_SPECIAL_RESPONSE) {
        return rc;
    }

    return NGX_DONE;
}
```

**代码注释**：

- `plcf->uri.len` 判断 proxy_pass 是否带有 URI 部分。如果有，用 proxy_pass 的 URI 替换 location 匹配的路径；否则原样传递
- `ngx_http_upstream_create` 初始化 upstream 结构体，后续由 `ngx_http_upstream.c` 负责连接池管理、负载均衡和故障转移
- `ngx_http_read_client_request_body` 读取客户端请求体（如果 buffering 开启，会写入临时文件）

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

| 维度     | Nginx 反向代理      | 客户端直连后端    |
| ------ | --------------- | ---------- |
| 安全性    | 高（隐藏后端 IP 和端口）  | 低（后端直接暴露）  |
| 跨域解决   | 原生支持（统一域名入口）    | 需后端配置 CORS |
| 负载均衡   | 原生支持多种算法        | 需客户端实现     |
| SSL 终止 | 集中管理证书          | 每个后端都要配证书  |
| 日志统一   | 单点收集分析          | 分散在各后端     |
| 性能开销   | 增加一层网络跳转（< 1ms） | 无额外开销      |

### 适用场景

1. **微服务网关**：统一入口，按路径路由到不同服务
2. **前后端分离**：解决浏览器跨域限制
3. **SSL 统一终止**：在 Nginx 层集中处理 HTTPS，后端使用纯 HTTP
4. **灰度发布**：按 Cookie、Header、IP 等条件路由到不同版本的后端
5. **老旧系统过渡**：新系统通过 Nginx 代理逐步替换旧系统的接口

**不适用场景**：

1. **对延迟极度敏感的内网调用**：如高频交易系统，增加一层代理可能 unacceptable
2. **需要端到端加密的场景**：某些合规要求后端也必须使用 HTTPS，不能仅在前端终止 SSL
3. **WebSocket 长连接**：需要额外配置 `proxy_set_header Upgrade` 和 `proxy_read_timeout`

### 注意事项

1. **proxy_pass 的斜杠陷阱**：带斜杠会替换 location 匹配部分，不带斜杠原样传递。生产环境务必测试验证。
2. **proxy_set_header 不是继承的**：子 location 中如果声明了新的 proxy_set_header，会覆盖而非追加父级的头。如果需要保留父级头，必须显式重写。
3. **buffering 的双刃剑**：开启 buffering 能提高吞吐量，但会增加延迟和磁盘 IO；关闭 buffering 降低延迟，但会降低吞吐量。大文件上传建议关闭。
4. **proxy_next_upstream 的幂等性风险**：默认对 502/504/timeout 会重试。POST/PUT 请求重试可能导致重复写入，务必谨慎配置。

### 常见踩坑经验

**案例一：proxy_pass 斜杠导致 404**

- **现象**：前端请求 `/api/users/123`，后端返回 404
- **根因**：`location /api/users/ { proxy_pass http://backend/; }`，后端收到的是 `/123` 而不是 `/users/123`
- **解决**：根据后端路由设计选择 proxy_pass 写法，或调整后端路由以匹配 Nginx 传递的路径

**案例二：Host 头错误导致虚拟主机匹配失败**

- **现象**：后端有多个虚拟主机，代理后总是匹配到默认站点
- **根因**：Nginx 默认把 proxy_pass 中的域名作为 Host 头传给后端，如 `proxy_pass http://127.0.0.1:8081` 时 Host=127.0.0.1:8081
- **解决**：显式设置 `proxy_set_header Host $host;`

**案例三：上传大文件 413 错误**

- **现象**：用户上传 10MB 图片时返回 413
- **根因**：`client_max_body_size` 默认为 1MB
- **解决**：在 server 或 location 中设置 `client_max_body_size 50M;`，并考虑关闭 `proxy_request_buffering`

### 推广计划提示

- **开发团队**：先复用本章最小配置与脚本，按“单变量”方式做参数实验并沉淀变更记录。
- **测试团队**：优先补齐异常路径用例（超时、重试、限流、故障转移），并固化回归清单。
- **运维团队**：将监控阈值、告警策略与回滚脚本纳入发布流程，确保高峰期可快速止损。
- **协作顺序建议**：开发先完成方案基线 -> 测试做功能/压力/故障验证 -> 运维执行灰度与上线守护。

### 思考题

1. **进阶题**：`ngx_http_proxy_module` 中，`proxy_pass` 带有 URI 时，URI 替换的具体逻辑在源码中是如何实现的？请分析 `ngx_http_proxy_set_vars` 函数中对 `plcf->vars.uri` 和 `r->uri` 的处理。

2. **进阶题**：当 `proxy_buffering on` 时，Nginx 是如何将客户端请求体临时存储到磁盘的？如果磁盘空间不足，会发生什么？请结合 `ngx_http_read_client_request_body` 和 `ngx_http_proxy_create_request` 的源码逻辑分析。

> 答案提示：第 1 题关注 `ngx_http_proxy_loc_conf_t` 中的 `url` 和 `uri` 字段差异；第 2 题涉及 `r->request_body->temp_file` 的创建和 `ngx_write_chain_to_temp_file` 的错误处理。

---

> **下一章预告**：我们将从单后端走向多后端，探索 Nginx 的负载均衡世界——Round Robin、IP Hash、权重分配，以及如何构建一个高可用的上游服务集群。
