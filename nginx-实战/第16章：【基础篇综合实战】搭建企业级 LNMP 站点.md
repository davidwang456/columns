# 第16章：【基础篇综合实战】搭建企业级 LNMP 站点

> 源码关联：`src/core/nginx.c`、`src/http/ngx_http_request.c`、`src/http/ngx_http_upstream.c`

---

## 1. 项目背景

基础篇走到这里，青云商城准备把"能访问"升级成"可上线、可扩展、可运维"。业务要求很明确：同时承载PC和移动端流量，支持静态资源快速分发，PHP接口稳定响应，HTTPS默认启用，日志可观测，出现流量波峰时要尽量扛住。

当前现状并不理想：前端资源、PHP应用、数据库和缓存散落在几台机器上，配置无统一规范，发布靠人工拷文件。每次活动前，团队都要临时加班排查配置差异，故障恢复严重依赖熟手。随着订单量增长，这套方案已经触到天花板。

公司决定以"企业级但不过度复杂"为目标做一次重构：以Nginx为统一入口，后接PHP-FPM与MySQL，Redis承担热点缓存，使用Docker Compose把环境标准化，确保开发、测试、预发、生产配置差异最小化。

本章综合演练目标：

- 基于LNMP搭建可复用的电商站点骨架；
- 分层配置静态、动态、缓存、日志、访问控制；
- 完成HTTPS、Gzip、Proxy Cache基础策略；
- 给出压测与验收方法，达成"1000并发，错误率<0.1%"的基础目标。

---

## 2. 项目设计

上线评审会上，三人开始最后一轮方案交锋。

**小胖**："前15章学了很多点，但我总觉得是散的。到了真实项目，第一步到底该先搭什么？"

**大师**："先搭骨架。骨架就是流量路径：`Client -> Nginx -> PHP-FPM -> MySQL/Redis`。把路径打通，再逐层加能力。"

**小白**："那是不是先不追求'最优参数'，而是先得到一套可验证、可迭代的最小可行架构？"

**大师**："完全正确。企业级不是参数堆满，而是稳定演进。"

**技术映射**：架构设计先保可交付，再做局部最优。

---

**小胖**："Nginx这次要承担哪些职责？"

**大师**："五件事：TLS终止、静态分发、反向代理、缓存加速、日志审计。"

**小白**："访问控制也要有吧？比如`/admin`只开放内网。"

**大师**："对，安全边界至少要有基础防线：IP访问限制、上传体积限制、错误页规范。"

**技术映射**：网关层职责是性能与安全的第一道控制面。

---

**小白**："如果活动流量突然翻倍，系统先扛不住哪一层？"

**大师**："通常是应用和数据库。Nginx可以先通过静态缓存、短TTL代理缓存、keepalive复用把压力削掉一部分，再配合Redis顶热点。"

**小胖**："所以Nginx不是万能药，但它能把后端从'全量直击'变成'有缓冲带'。"

**大师**："这就是网关价值。"

**技术映射**：Nginx的核心价值是削峰、隔离、观测，不是替代业务服务。

---

## 3. 项目实战

### 环境准备

- Docker 24+，Docker Compose v2
- Nginx 1.24+
- PHP 8.2-fpm
- MySQL 8.0
- Redis 7

目录结构建议：

```text
lnmp/
├── docker-compose.yml
├── nginx/
│   ├── nginx.conf
│   └── conf.d/
│       └── mall.conf
├── php/
│   └── www.conf
└── app/
    ├── public/index.php
    └── public/health.php
```

---

### 步骤一：用 Docker Compose 拉起基础服务

步骤目标：一次命令启动Nginx/PHP/MySQL/Redis。

```yaml
version: "3.9"
services:
  nginx:
    image: nginx:1.24-alpine
    container_name: mall-nginx
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./nginx/nginx.conf:/etc/nginx/nginx.conf:ro
      - ./nginx/conf.d:/etc/nginx/conf.d:ro
      - ./app/public:/var/www/html/public:ro
      - ./logs/nginx:/var/log/nginx
      - ./certs:/etc/nginx/certs:ro
    depends_on:
      - php
      - redis
    networks:
      - mall-net

  php:
    image: php:8.2-fpm-alpine
    container_name: mall-php
    volumes:
      - ./app:/var/www/html
    networks:
      - mall-net

  mysql:
    image: mysql:8.0
    container_name: mall-mysql
    environment:
      MYSQL_ROOT_PASSWORD: root123
      MYSQL_DATABASE: mall
      MYSQL_USER: mall
      MYSQL_PASSWORD: mall123
    volumes:
      - ./data/mysql:/var/lib/mysql
    networks:
      - mall-net

  redis:
    image: redis:7-alpine
    container_name: mall-redis
    networks:
      - mall-net

networks:
  mall-net:
    driver: bridge
```

启动：

```bash
docker compose up -d
docker compose ps
```

---

### 步骤二：编写Nginx主配置（全局性能与日志）

步骤目标：提供通用网关基础能力。

`nginx/nginx.conf`：

```nginx
user  nginx;
worker_processes auto;

events {
    worker_connections 4096;
    multi_accept on;
}

http {
    include       /etc/nginx/mime.types;
    default_type  application/octet-stream;
    sendfile      on;
    tcp_nopush    on;
    tcp_nodelay   on;
    keepalive_timeout 65;

    log_format json_main escape=json
      '{"time":"$time_iso8601","request_id":"$request_id","remote_addr":"$remote_addr",'
      '"method":"$request_method","uri":"$uri","status":$status,'
      '"request_time":$request_time,"upstream_time":"$upstream_response_time",'
      '"upstream_addr":"$upstream_addr","cache":"$upstream_cache_status"}';

    access_log /var/log/nginx/access.log json_main;
    error_log  /var/log/nginx/error.log warn;

    gzip on;
    gzip_comp_level 5;
    gzip_min_length 1k;
    gzip_types text/plain text/css application/json application/javascript application/xml;

    proxy_cache_path /var/cache/nginx/mall levels=1:2 keys_zone=mall_cache:100m max_size=2g inactive=20m use_temp_path=off;

    include /etc/nginx/conf.d/*.conf;
}
```

---

### 步骤三：编写业务站点配置（静态/动态/缓存/安全）

步骤目标：完成电商站点主流量规则。

`nginx/conf.d/mall.conf`：

```nginx
upstream php_fpm {
    server php:9000;
    keepalive 32;
}

server {
    listen 80;
    server_name mall.local;
    root /var/www/html/public;
    index index.php index.html;

    # 静态资源：一年缓存
    location ~* \.(?:js|css|png|jpg|jpeg|gif|svg|woff2?)$ {
        expires 365d;
        add_header Cache-Control "public, max-age=31536000, immutable";
        try_files $uri =404;
    }

    # API代理缓存（匿名）
    location /api/catalog/ {
        proxy_pass http://php_fpm;
        proxy_cache mall_cache;
        proxy_cache_key "$scheme$proxy_host$request_uri";
        proxy_cache_valid 200 5m;
        proxy_no_cache $http_authorization $cookie_session_id;
        proxy_cache_bypass $http_authorization $cookie_session_id;
        add_header X-Cache-Status $upstream_cache_status always;
    }

    # 管理后台仅内网可访问
    location /admin/ {
        allow 10.0.0.0/8;
        allow 192.168.0.0/16;
        deny all;
        try_files $uri $uri/ /index.php?$query_string;
    }

    # PHP入口
    location ~ \.php$ {
        include fastcgi_params;
        fastcgi_param SCRIPT_FILENAME $document_root$fastcgi_script_name;
        fastcgi_pass php_fpm;
        fastcgi_read_timeout 60s;
    }

    # 上传限制
    client_max_body_size 20m;

    # 健康检查
    location = /health {
        access_log off;
        return 200 "ok\n";
    }

    location / {
        try_files $uri $uri/ /index.php?$query_string;
    }
}
```

说明：示例中`/api/catalog/`用于演示缓存路径，真实项目中可改成`proxy_pass`到独立HTTP应用。

---

### 步骤四：最小PHP应用与连通验证

步骤目标：验证LNMP链路闭环。

`app/public/index.php`（示例）：

```php
<?php
header('Content-Type: application/json; charset=utf-8');
echo json_encode([
    'service' => 'mall-app',
    'ts' => date('c'),
    'path' => $_SERVER['REQUEST_URI'] ?? '/',
]);
```

验证：

```bash
curl -i http://localhost/health
curl -i http://localhost/
curl -i http://localhost/index.php
```

---

### 步骤五：HTTPS与基础安全加固

步骤目标：启用TLS并增强默认安全基线。

示例（自签名环境）：

```bash
openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
  -keyout certs/mall.key -out certs/mall.crt \
  -subj "/CN=mall.local"
```

在`server`中追加：

```nginx
listen 443 ssl http2;
ssl_certificate     /etc/nginx/certs/mall.crt;
ssl_certificate_key /etc/nginx/certs/mall.key;
ssl_protocols TLSv1.2 TLSv1.3;
add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
```

验证：

```bash
curl -k -I https://localhost/
```

---

### 步骤六：压测与验收

步骤目标：验证基础篇综合成果。

使用`wrk`压测首页：

```bash
wrk -t4 -c200 -d60s http://localhost/
```

使用`hey`模拟更高并发：

```bash
hey -n 50000 -c 1000 http://localhost/
```

验收建议：

- 平均响应时间稳定，P95/P99无明显抖动；
- 错误率<0.1%；
- `access.log`可看到请求时间和缓存状态；
- 高并发下容器无频繁重启，CPU/内存处于可控区间。

---

### 步骤七：故障演练（基础篇收官）

建议至少做以下5种演练：

1. 关闭PHP容器，观察`502`并验证SOP；  
2. 人为制造慢SQL，观察`504`趋势；  
3. 上传超大文件触发`413`；  
4. 模拟客户端短超时触发`499`；  
5. 配置写错后用`nginx -t`拦截错误发布。  

经过演练，团队才能把"会配Nginx"升级成"会守Nginx"。

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

本章把基础篇知识完成了系统串联：从Nginx配置语法、反向代理、缓存、压缩、HTTPS、日志、访问控制到运维排障，形成了一套可交付的LNMP落地方案。它未必是最终形态，但已经具备企业上线所需的基本工程属性：标准化、可观测、可扩展、可演练。

### 方案优缺点

| 维度 | 优点 | 缺点 |
|------|------|------|
| 交付速度 | Compose一键拉起，环境统一 | 单机Compose不等于生产高可用 |
| 性能 | 静态长缓存 + Gzip + Keepalive收益明显 | 动态热点仍依赖应用优化 |
| 运维 | 日志结构化，排障链路清晰 | 需持续维护告警与SOP |
| 安全 | HTTPS与访问控制具备基础防线 | 更细粒度鉴权仍需应用/网关插件 |

### 适用场景

- 中小型业务快速搭建稳定Web入口；
- 研发/测试/运维共用一套标准化本地与预发环境；
- 需要在短周期内交付可运行电商或内容站点。

### 不适用场景

- 多地域多活、超大规模集群（需K8s与服务治理体系）；  
- 强依赖复杂鉴权、动态路由、服务发现的微服务平台。

### 基础篇收官思考题

1. 如果业务进入大促，Nginx层你会优先优化哪三项参数？为什么？  
2. 当缓存命中率和数据实时性冲突时，你会如何给不同接口做分级策略？

---

> **基础篇阶段总结**：你已经完成从"会用Nginx"到"能交付Nginx方案"的跨越。下一阶段将进入中级篇，重点攻克事件驱动、连接池、上游高可用、高级负载均衡、四层代理与可观测体系，逐步走向架构级实践。

### 推广计划提示

- **开发团队**：先复用本章最小配置与脚本，按“单变量”方式做参数实验并沉淀变更记录。
- **测试团队**：优先补齐异常路径用例（超时、重试、限流、故障转移），并固化回归清单。
- **运维团队**：将监控阈值、告警策略与回滚脚本纳入发布流程，确保高峰期可快速止损。
- **协作顺序建议**：开发先完成方案基线 -> 测试做功能/压力/故障验证 -> 运维执行灰度与上线守护。
