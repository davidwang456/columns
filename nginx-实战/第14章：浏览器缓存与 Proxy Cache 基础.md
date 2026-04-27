# 第14章：浏览器缓存与 Proxy Cache 基础

> 源码关联：`src/http/ngx_http_file_cache.c`、`src/http/modules/ngx_http_headers_filter_module.c`

---

## 1. 项目背景

"青云商城"最近遇到一个典型矛盾：业务量上涨了，但机器预算没涨。运营部门希望首页加载更快，研发团队希望接口压力更小，运维团队则希望晚高峰少报警。结果三方在同一件事上反复拉扯：缓存到底怎么配，配到哪里，谁来兜底。

上线初期，青云商城把静态资源交给Nginx托管，所有请求都走同一层反向代理。随着活动页面增多，前端构建产物越来越大，用户每次打开首页都会重新下载几十个JS/CSS文件。明明文件名里已经带了hash，浏览器却还是频繁回源。与此同时，商品列表和首页推荐接口请求量暴增，后端Java服务和MySQL实例都出现了短时抖动，P99延迟从120ms飙到450ms。

小胖第一反应是"再加机器"，但财务不批；小白担心"缓存会不会把脏数据缓存住"；大师则强调：缓存不是一个开关，而是一套层次化治理。浏览器缓存负责"减少重复下载"，代理缓存负责"减少重复计算"。两者作用点不同、风险不同、失效策略也不同。

这章要解决的问题很具体：

- 静态资源如何做到"一年强缓存 + 文件名变更即失效"；
- API如何做到"短TTL缓存 + 可观测命中率"；
- 如何避免缓存击穿和错误响应被长期缓存；
- 如何从源码视角理解Nginx文件缓存的基本机制。

---

## 2. 项目设计

周一早会，三人围着白板讨论缓存方案。

**小胖**："我看别人配置里直接写`expires 1y`，那我给整个站都配一年不就行了？一劳永逸。"

**小白**："这会把动态接口也缓存住吧？比如购物车数量、库存状态，这些都是实时变的。"

**大师**："你俩说的都对了一半。缓存要分层：静态资源适合长缓存，动态接口通常短缓存甚至不缓存。先分清对象，再谈策略。"

**技术映射**：缓存策略的核心是"对象分类"，不是"参数堆叠"。

---

**小胖**："那浏览器缓存具体看什么？我只记得`Cache-Control`。"

**大师**："浏览器主要看四类信息：`Cache-Control`、`Expires`、`ETag`、`Last-Modified`。现代实践优先`Cache-Control`，`Expires`作为兼容。"

**小白**："如果静态文件用了hash文件名，比如`app.8f3a1.js`，是不是就可以设置`Cache-Control: public, max-age=31536000, immutable`？"

**大师**："对。hash一变，URL就变，浏览器自然会拉新文件。`immutable`告诉浏览器：在有效期内别发条件请求，直接用本地副本。"

**技术映射**：文件名版本化 + 长缓存，是前端静态资源最稳妥的组合。

---

**小白**："代理缓存怎么设计？我怕把登录态、用户个性化数据缓存串了。"

**大师**："Proxy Cache要先定义缓存键。默认可用`$scheme$proxy_host$request_uri`，但如果接口跟用户或设备强相关，要把`Authorization`、`Cookie`或相关头纳入判断，很多时候直接`proxy_no_cache`更安全。"

**小胖**："那命中率怎么观察？"

**大师**："给响应头加`X-Cache-Status $upstream_cache_status`，常见状态有`MISS`、`HIT`、`BYPASS`、`EXPIRED`、`STALE`。上线后先看状态分布，再调TTL。"

**技术映射**：缓存的第一性原理是"可观测"，没有命中率就没有优化闭环。

---

**小白**："失效策略怎么做？用户改了商品价格，缓存里还是旧值怎么办？"

**大师**："基础篇先用TTL治理：热点列表5分钟，详情页1分钟。主动清理可留到中级篇（Purge模块或业务侧版本号）。"

**小胖**："如果后端短暂报500，会不会被缓存住？"

**大师**："默认不要缓存5xx；即使开启`use_stale`，也要明确只在上游故障时兜底，且时间窗口可控。"

**技术映射**：缓存是性能手段，不是正确性手段；先保正确，再追命中。

---

## 3. 项目实战

### 环境准备

- Nginx 1.24+（mainline或stable均可）
- 后端服务：`127.0.0.1:9000`（示例API）
- 静态目录：`/srv/www/mall/static`
- 缓存目录：`/var/cache/nginx/mall_api`

准备目录：

```bash
sudo mkdir -p /srv/www/mall/static
sudo mkdir -p /var/cache/nginx/mall_api
sudo chown -R nginx:nginx /var/cache/nginx
```

---

### 步骤一：配置静态资源浏览器缓存

步骤目标：让带hash静态资源实现一年强缓存。

```nginx
server {
    listen 80;
    server_name mall.local;

    root /srv/www/mall/static;

    # 带hash的构建产物：长期缓存
    location ~* \.(?:js|css|png|jpg|jpeg|gif|svg|woff2?)$ {
        expires 365d;
        add_header Cache-Control "public, max-age=31536000, immutable";
        try_files $uri =404;
    }

    # HTML入口文件：不建议长缓存
    location = /index.html {
        expires -1;
        add_header Cache-Control "no-cache";
    }
}
```

验证：

```bash
curl -I http://mall.local/assets/app.8f3a1.js
curl -I http://mall.local/index.html
```

预期：前者返回长缓存头，后者返回`no-cache`。

常见坑：

- `index.html`被错误设置长缓存，导致前端发布后用户仍加载旧入口；
- 未使用hash文件名却配一年缓存，导致资源更新无法即时生效。

---

### 步骤二：启用Proxy Cache基础能力

步骤目标：为商品列表API提供5分钟缓存。

```nginx
http {
    proxy_cache_path /var/cache/nginx/mall_api
        levels=1:2
        keys_zone=mall_api_cache:100m
        max_size=5g
        inactive=30m
        use_temp_path=off;

    upstream mall_backend {
        server 127.0.0.1:9000;
        keepalive 64;
    }

    server {
        listen 80;
        server_name mall.local;

        location /api/products {
            proxy_pass http://mall_backend;

            proxy_cache mall_api_cache;
            proxy_cache_key "$scheme$proxy_host$request_uri";
            proxy_cache_valid 200 302 5m;
            proxy_cache_valid 404 1m;
            proxy_cache_valid any 0;

            add_header X-Cache-Status $upstream_cache_status always;
            add_header X-Cache-Key $scheme$proxy_host$request_uri always;
        }
    }
}
```

验证：

```bash
curl -i http://mall.local/api/products?page=1
curl -i http://mall.local/api/products?page=1
```

预期：第一次`X-Cache-Status: MISS`，第二次`HIT`。

常见坑：

- 忘记给缓存目录授权，Nginx报`permission denied`；
- `proxy_cache_key`过于粗糙，导致不同参数请求互相污染。

---

### 步骤三：为动态和登录态请求设置绕过规则

步骤目标：防止个性化接口被误缓存。

```nginx
map $http_authorization $skip_cache_by_auth {
    default 1;
    ""      0;
}

map $cookie_session_id $skip_cache_by_cookie {
    default 1;
    ""      0;
}

server {
    listen 80;
    server_name mall.local;

    location /api/ {
        proxy_pass http://mall_backend;

        proxy_cache mall_api_cache;
        proxy_cache_key "$scheme$proxy_host$request_uri";
        proxy_cache_valid 200 3m;

        proxy_no_cache     $skip_cache_by_auth $skip_cache_by_cookie;
        proxy_cache_bypass $skip_cache_by_auth $skip_cache_by_cookie;

        add_header X-Cache-Status $upstream_cache_status always;
    }
}
```

验证：

```bash
curl -i http://mall.local/api/recommend
curl -i -H "Authorization: Bearer token" http://mall.local/api/recommend
```

预期：匿名请求可能命中缓存；带授权头请求`BYPASS`。

---

### 步骤四：加上故障兜底与缓存可观测性

步骤目标：上游抖动时尽量返回可用旧缓存。

```nginx
location /api/products {
    proxy_pass http://mall_backend;
    proxy_cache mall_api_cache;
    proxy_cache_valid 200 5m;

    proxy_cache_use_stale error timeout invalid_header http_500 http_502 http_503 http_504;
    proxy_cache_background_update on;

    add_header X-Cache-Status $upstream_cache_status always;
}
```

说明：

- `use_stale`：上游异常时可返回陈旧缓存，减轻雪崩；
- `background_update`：后台更新缓存，前台继续快速返回旧值。

注意：价格、库存这类强一致接口要谨慎启用。

---

### 步骤五：语法检查与上线

```bash
nginx -t
nginx -s reload
```

建议上线后观察：

- `HIT/MISS/BYPASS/EXPIRED`比例；
- 上游QPS和P99延迟变化；
- 缓存目录增长速度与磁盘水位。

---

### 步骤六：源码级理解（入门）

`ngx_http_file_cache.c`负责磁盘缓存的核心行为：键计算、元数据读写、过期判断、文件命名。简化理解：

1. 根据`proxy_cache_key`生成cache key；
2. 从共享内存索引区定位缓存项；
3. 命中且未过期则直接回包；
4. 未命中或过期则回源，并按策略更新缓存文件。

这也是为什么`keys_zone`大小很关键：它保存的是缓存元信息，不是完整响应体。

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

本章的核心不是"把缓存打开"，而是建立一套能长期运行的缓存治理规则：静态资源长缓存、动态接口短缓存、登录态绕过、命中可观测、故障可兜底。做到这五件事，通常就能在不改业务代码的前提下把网关层性能抬高一个台阶。

### 优点与缺点

| 维度 | 浏览器缓存 | Proxy Cache |
|------|------------|-------------|
| 主要收益 | 减少客户端下载 | 减少后端计算与IO |
| 生效位置 | 用户端浏览器 | Nginx网关层 |
| 一致性风险 | 中等（发布策略影响） | 较高（键设计失误会串数据） |
| 见效速度 | 快 | 快 |
| 运维复杂度 | 低 | 中 |

### 适用场景

- 前端构建产物（JS/CSS/字体/图片）长期缓存；
- 商品列表、推荐流、门户配置等可容忍短时延迟数据；
- 上游高峰易抖动，需要网关层削峰。

### 不适用场景

- 账户余额、支付状态、库存扣减等强一致接口；
- 高度个性化且命中率极低的接口。

### 三个生产踩坑案例

1. **错误缓存登录态页面**：未绕过`Cookie`导致用户A看到用户B信息。  
2. **缓存键遗漏查询参数**：`/list?page=1`与`/list?page=2`混用同一缓存。  
3. **盲目缓存5xx**：短时故障被放大成持续错误响应。

### 推广计划提示

- **开发团队**：先复用本章最小配置与脚本，按“单变量”方式做参数实验并沉淀变更记录。
- **测试团队**：优先补齐异常路径用例（超时、重试、限流、故障转移），并固化回归清单。
- **运维团队**：将监控阈值、告警策略与回滚脚本纳入发布流程，确保高峰期可快速止损。
- **协作顺序建议**：开发先完成方案基线 -> 测试做功能/压力/故障验证 -> 运维执行灰度与上线守护。

### 思考题

1. 你所在业务中，哪些接口可以接受"最多5分钟旧数据"？如何分级？  
2. 如果要求某个接口发布后10秒内全网生效，你会选择TTL、版本号，还是主动清理？
