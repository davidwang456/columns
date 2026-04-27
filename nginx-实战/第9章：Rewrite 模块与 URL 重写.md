# 第9章：Rewrite 模块与 URL 重写

> 源码关联：src/http/modules/ngx_http_rewrite_module.c

---

## 1. 项目背景

鲜果园成立八周年，决定全面升级品牌形象，从 xianguoyuan.com 焕新为 xgy.com。市场部花了三个月做 SEO 迁移方案，技术团队却在一周内就完成了域名切换——除了一个致命问题：旧域名的三千多条 URL 没有正确跳转，搜索引擎收录的链接全部变成了 404。百度权重从 6 暴跌到 2，自然搜索流量腰斩。

CTO 老张紧急召集会议，SEO 顾问在会上拍桌子："你们的技术方案里必须有 301 永久重定向！每一个旧 URL 都要对应到新 URL，权重才能传递过去！"

后端新人小胖接下了这个任务。他打开 Nginx 配置文件，面对 rewrite、return、if、break、last、permanent 这些指令，彻底懵了：
- rewrite ^/old/(.*)$ /new/$1 permanent; 和 rewrite ^/old/(.*)$ /new/$1 redirect; 有什么区别？
- last 和 break 到底停在哪？
- if 为什么被称为 evil？
- 三千条规则写在配置文件里，Nginx 启动会不会卡死？

架构师小李安慰他："Rewrite 模块是 Nginx 最强大也最危险的武器。用好了是 SEO 神器，用不好是生产事故的罪魁祸首。"

本章将从鲜果园的域名迁移之痛出发，深入讲解 Nginx Rewrite 模块的工作原理、正则匹配技巧和性能优化策略，最终完成一套可维护的大规模 URL 迁移方案。

---

## 2. 项目设计

**场景**：鲜果园技术部，小胖、小白和大师围坐在显示器前，屏幕上打开着密密麻麻的 Excel——三千条旧 URL 映射表。

---

**小胖**：（揉着眼睛）大师，这三千条 URL 怎么迁啊？我一条条写 rewrite 吗？

**大师**：（喝了口咖啡）先别急着写规则，先理解 rewrite 的四种 flag。这是地基，地基不稳，上面盖再漂亮的楼也会塌。

**大师**：（在白板上画了一张表）

| Flag | HTTP 状态码 | 行为 | 使用场景 |
|------|-----------|------|---------|
| last | 无（内部重定向） | 重写 URI 后，重新匹配 location | 内部路由调整 |
| break | 无（停止处理） | 重写 URI 后，不再匹配其他 rewrite | 内部路径映射 |
| redirect | 302 | 临时重定向，URL 会变 | 短期维护、A/B 测试 |
| permanent | 301 | 永久重定向，URL 会变 | SEO 迁移、域名更换 |

**小胖**：last 和 break 都是内部处理，用户看不到 URL 变化？

**大师**：对。last 会触发 Nginx 重新进行 location 匹配，就像用户发了一个新请求；break 则直接在当前 location 继续处理，不再检查其他 rewrite 规则。redirect 和 permanent 会给浏览器返回 302/301，浏览器地址栏会显示新 URL。

**小白**：那 SEO 迁移必须用 permanent（301），因为搜索引擎会把旧 URL 的权重传递给新 URL？

**大师**：对。301 表示永久搬家，搜索引擎会更新索引，把排名权重传递到新地址。302 表示临时出差，搜索引擎保留旧地址的索引，不传递权重。如果 SEO 迁移用了 302，等于白干。

**小胖**：if 指令呢？我看到文档说它是 evil。

**大师**：Nginx 的 if 不是真正的条件分支语句，它是 rewrite 模块的一部分，只在 rewrite 阶段执行。它的坑包括：

1. if 里不能直接用某些指令：比如 try_files、alias、return 以外的很多指令在 if 里有隐式行为
2. if 会创建隐式的内部 location：这会导致配置继承混乱
3. if 内的 rewrite 规则作用域不明：有时候 break 了，有时候没 break

**大师**：官方文档的建议是：能用 map 就不用 if，能用 location 就不用 rewrite。map 是声明式的、无副作用的；if 是命令式的、充满副作用的。

**小白**：三千条规则写在 nginx.conf 里，文件会膨胀到几 MB，启动会不会变慢？

**大师**：这就是性能问题。每个 rewrite 规则都是一条正则表达式，Nginx 启动时会编译这些正则。三千条规则意味着三千次 regex 编译，确实会影响启动时间，但影响的是启动，不是运行时——因为运行时 rewrite 是按需匹配的。

**大师**：对于大规模规则，更好的方案是：

1. 使用 map 模块做批量映射：把 URL 映射放到一个外部文件里，Nginx 用哈希表查找，O(1) 复杂度
2. 使用 Lua/NJS 做动态路由：规则存储在 Redis 或数据库里，运行时查询
3. 分级处理：常用规则放 Nginx，长尾规则放后端应用处理

**小胖**：map 怎么用来替代 rewrite？

**大师**：比如你把旧 URL 到新 URL 的映射写到 /etc/nginx/redirects.map：

```
/old/apple /fruit/apple;
/old/orange /fruit/orange;
```

然后在 nginx.conf 里：

```nginx
map $uri $new_uri {
    include /etc/nginx/maps/redirects.map;
    default "";
}

server {
    if ($new_uri) {
        return 301 $new_uri;
    }
}
```

这样 Nginx 启动时会把这个 map 编译成哈希表，查找效率远高于正则匹配。

---

## 3. 项目实战

### 环境准备

- **Nginx 版本**：1.31.0
- **工具**：ab（压测）、curl（测试）

### 步骤一：创建 URL 映射文件

```bash
# 创建映射文件目录
sudo mkdir -p /etc/nginx/maps

# 创建旧 URL 到新 URL 的映射（模拟 3000 条中的部分）
sudo tee /etc/nginx/maps/redirects.map << 'EOF'
/old/apple        /fruit/apple;
/old/orange       /fruit/orange;
/old/banana       /fruit/banana;
/old/fruit-basket /gift/basket;
/old/vip          /member/premium;
/old/cart         /checkout/cart;
/old/order        /orders/list;
/old/product/1001 /p/1001;
/old/product/1002 /p/1002;
/old/product/1003 /p/1003;
EOF

# 创建设备类型映射
sudo tee /etc/nginx/maps/device.map << 'EOF'
~*iphone|android.*mobile    mobile;
~*ipad|android(?!.*mobile)  tablet;
default                      desktop;
EOF
```

### 步骤二：编写 Nginx Rewrite 配置

编辑 /usr/local/nginx/conf/nginx.conf：

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

    log_format rewrite_log '$remote_addr - [$time_local] '
                           '"$request" $status '
                           'uri=$uri rewrite_uri=$request_uri';

    access_log /var/log/nginx/access.log rewrite_log;

    # 旧 URL 到新 URL 的映射
    map $uri $new_uri {
        include /etc/nginx/maps/redirects.map;
        default "";
    }

    # 设备类型映射
    map $http_user_agent $device_type {
        include /etc/nginx/maps/device.map;
    }

    server {
        listen       80;
        server_name  old.xianguoyuan.com;
        root         /var/www/old;

        # 方案一：map + return（推荐，性能最好）
        if ($new_uri) {
            return 301 $new_uri;
        }

        # 方案二：rewrite 规则（小规模场景）
        # rewrite ^/old/apple$ /fruit/apple permanent;
        # rewrite ^/old/orange$ /fruit/orange permanent;

        # 方案三：正则批量匹配（中等规模）
        # rewrite ^/old/product/(\d+)$ /p/$1 permanent;

        location / {
            try_files $uri $uri/ =404;
        }
    }

    # 新域名服务器
    server {
        listen       80;
        server_name  xgy.com www.xgy.com;
        root         /var/www/xgy;

        # 根据设备类型分发到不同目录
        location = / {
            if ($device_type = mobile) {
                rewrite ^ /mobile/index.html last;
            }
            if ($device_type = tablet) {
                rewrite ^ /tablet/index.html last;
            }
            try_files /desktop/index.html =404;
        }

        # API 版本控制：/api/v1/xxx -> /api/xxx?v=1
        location ~ ^/api/v(\d+)/(.*)$ {
            rewrite ^/api/v(\d+)/(.*)$ /api/$2?v=$1 last;
        }

        # 伪静态：/p/1001 -> /product.php?id=1001
        location ~ ^/p/(\d+)$ {
            rewrite ^/p/(\d+)$ /product.php?id=$1 last;
        }

        # 禁止直接访问 .php 文件（只允许伪静态入口）
        location ~* \\.php$ {
            internal;
            fastcgi_pass unix:/run/php/php8.2-fpm.sock;
            fastcgi_param SCRIPT_FILENAME $document_root$fastcgi_script_name;
            include fastcgi_params;
        }
    }
}
```

### 步骤三：创建测试页面

```bash
# 旧站目录
sudo mkdir -p /var/www/old
sudo tee /var/www/old/index.html << 'EOF'
<!DOCTYPE html><html><body><h1>旧站首页</h1></body></html>
EOF

# 新站目录
sudo mkdir -p /var/www/xgy/{desktop,mobile,tablet}
sudo tee /var/www/xgy/desktop/index.html << 'EOF'
<!DOCTYPE html><html><body><h1>新站桌面版</h1></body></html>
EOF
sudo tee /var/www/xgy/mobile/index.html << 'EOF'
<!DOCTYPE html><html><body><h1>新站移动版</h1></body></html>
EOF
sudo tee /var/www/xgy/tablet/index.html << 'EOF'
<!DOCTYPE html><html><body><h1>新站平板版</h1></body></html>
EOF

sudo tee /var/www/xgy/product.php << 'EOF'
<?php
header('Content-Type: application/json');
echo json_encode(['product_id' => $_GET['id'] ?? 'unknown', 'name' => '泰国金枕榴莲']);
EOF
```

### 步骤四：语法验证与启动

```bash
sudo /usr/local/nginx/sbin/nginx -t
sudo /usr/local/nginx/sbin/nginx -s reload
```

### 步骤五：功能验证

**5.1 测试 301 重定向（旧 URL -> 新 URL）**

```bash
# 测试 map 驱动的 301 重定向
curl -I -H "Host: old.xianguoyuan.com" http://localhost/old/apple
# 预期返回：HTTP/1.1 301 Moved Permanently
# Location: /fruit/apple

curl -I -H "Host: old.xianguoyuan.com" http://localhost/old/product/1001
# 预期返回：HTTP/1.1 301 Moved Permanently
# Location: /p/1001
```

**5.2 测试伪静态**

```bash
# 测试 /p/1001 -> /product.php?id=1001
curl -s http://localhost/p/1001
# 预期返回 JSON：{"product_id":"1001","name":"泰国金枕榴莲"}
```

**5.3 测试 API 版本控制**

```bash
# 测试 /api/v2/users -> /api/users?v=2
curl -s http://localhost/api/v2/users
# 预期 Nginx 内部重定向到 /api/users?v=2（如果后端存在的话）
```

**5.4 测试设备适配**

```bash
# 桌面
curl -s -H "User-Agent: Mozilla/5.0" http://localhost/
# 预期返回桌面版首页

# 手机
curl -s -H "User-Agent: iPhone" http://localhost/
# 预期返回移动版首页

# 平板
curl -s -H "User-Agent: iPad" http://localhost/
# 预期返回平板版首页
```

**5.5 压测验证重定向性能**

```bash
sudo apt install -y apache2-utils

# 对 301 重定向接口压测
ab -n 10000 -c 1000 -H "Host: old.xianguoyuan.com" http://localhost/old/apple

# 观察响应时间分布
# 使用 map 的 O(1) 查找，10000 请求应在 1 秒内完成
```

### 步骤六：源码速览——Rewrite 模块

打开 src/http/modules/ngx_http_rewrite_module.c，查看 rewrite 指令的处理逻辑：

```c
// src/http/modules/ngx_http_rewrite_module.c
static ngx_int_t
ngx_http_rewrite_handler(ngx_http_request_t *r)
{
    ngx_http_rewrite_loc_conf_t  *rlcf;
    ngx_array_t                  *codes;
    ngx_http_script_code_pt       code;
    ngx_http_script_engine_t      e;

    rlcf = ngx_http_get_module_loc_conf(r, ngx_http_rewrite_module);
    codes = rlcf->codes;

    if (codes == NULL) {
        return NGX_DECLINED;
    }

    // 初始化脚本引擎
    ngx_memzero(&e, sizeof(ngx_http_script_engine_t));
    e.ip = codes->elts;
    e.request = r;

    // 按顺序执行 rewrite 规则
    while (*(uintptr_t *) e.ip) {
        code = *(ngx_http_script_code_pt *) e.ip;
        code(&e);  // 执行一条 rewrite 规则
    }

    // 如果 e.status 被设置，返回对应的 HTTP 状态码
    if (e.status) {
        return e.status;
    }

    return NGX_DECLINED;
}
```

**代码注释**：
- Nginx 的 rewrite 模块实现了一套"脚本引擎"（script engine），把配置中的 rewrite 规则编译成字节码序列
- 运行时按顺序执行字节码，每条规则对应一个 code 函数指针
- last 和 break 的实现，是通过修改 e.ip（指令指针）来控制流程跳转
- permanent 和 redirect 的实现，是通过设置 e.status 为 301/302，然后由 HTTP 核心模块返回重定向响应

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

| 维度 | Nginx Rewrite | 后端应用层路由 |
|------|--------------|---------------|
| 执行效率 | 极高（C 语言实现，内核态） | 中（应用层解释执行） |
| 功能丰富度 | 中（正则、变量、if、return） | 高（任意业务逻辑） |
| 可维护性 | 低（规则分散在配置文件中） | 高（代码版本控制、单元测试） |
| SEO 效果 | 好（301 重定向由服务器首包返回） | 好 |
| 动态更新 | 支持热重载 | 天然支持 |
| 调试难度 | 高（error_log 信息有限） | 低（断点、日志丰富） |

### 适用场景

1. **SEO URL 迁移**：旧站换新站，301 重定向传递权重
2. **伪静态化**：/product/1001 -> /product.php?id=1001，提升 URL 可读性
3. **域名统一**：不带 www 跳转带 www，或 HTTP 跳转 HTTPS
4. **A/B 测试分流**：按 Cookie/IP/Header 将流量分发到不同版本
5. **API 版本兼容**：/api/v1/users -> 内部路由到 /api/users?v=1

**不适用场景**：
1. **复杂的业务路由逻辑**：如根据用户权限、库存状态动态决定路由，应放在后端
2. **需要数据库查询的路由**：如短链接服务（/abc -> 查数据库得长链接），应使用后端或 Lua
3. **频繁变更的路由规则**：每次变更都要 reload，不如后端灵活

### 注意事项

1. **permanent 和 redirect 的区别**：SEO 场景必须用 permanent（301），测试场景可用 redirect（302）
2. **last 会重新匹配 location**：如果新 URI 匹配到了不同的 location，该 location 中的配置会生效，包括新的 rewrite 规则
3. **break 不会重新匹配 location**：新 URI 只在当前 location 中继续处理，适合简单的路径替换
4. **避免 rewrite 循环**：如果规则 A 把 /a 改写成 /b，规则 B 又把 /b 改写成 /a，Nginx 会检测循环并返回 500

### 常见踩坑经验

**案例一：rewrite 循环导致 500**
- **现象**：配置了 rewrite 后，访问某些 URL 返回 500 Internal Server Error
- **根因**：rewrite 规则形成了循环，如 `rewrite ^/a /b last;` 和 `rewrite ^/b /a last;`
- **解决**：检查 rewrite 规则的覆盖范围，使用更精确的正则，或添加 break 停止继续匹配

**案例二：map 文件格式错误导致启动失败**
- **现象**：include 了外部 map 文件后，nginx -t 报错
- **根因**：map 文件中缺少分号、引号不匹配或存在空行
- **解决**：确保每行以分号结尾，正则表达式用引号包围，删除空行

**案例三：if 与 try_files 混用导致意外行为**
- **现象**：在 location 中同时使用了 if 和 try_files，结果 try_files 不生效
- **根因**：if 会创建隐式的内部 location，try_files 在这个隐式 location 中行为异常
- **解决**：用 map 替代 if，或把 try_files 放到另一个不含 if 的 location 中

### 推广计划提示

- **开发团队**：先复用本章最小配置与脚本，按“单变量”方式做参数实验并沉淀变更记录。
- **测试团队**：优先补齐异常路径用例（超时、重试、限流、故障转移），并固化回归清单。
- **运维团队**：将监控阈值、告警策略与回滚脚本纳入发布流程，确保高峰期可快速止损。
- **协作顺序建议**：开发先完成方案基线 -> 测试做功能/压力/故障验证 -> 运维执行灰度与上线守护。

### 思考题

1. **进阶题**：`ngx_http_rewrite_handler` 中的脚本引擎（script engine）是如何将配置文件中的 `rewrite ^/old/(.*)$ /new/$1 permanent;` 编译为可执行的字节码的？请分析 `ngx_http_rewrite_compile` 函数中对正则表达式和变量替换的处理逻辑。

2. **进阶题**：为什么 `map` 模块的查找效率是 O(1)，而 `rewrite` 模块的正则匹配是 O(n) 或更差？请结合 `ngx_http_map.c` 中的哈希表构建过程和 `ngx_http_rewrite_module.c` 中的顺序匹配机制，从源码角度解释。

> 答案提示：第 1 题关注 `ngx_http_script_compile_t` 结构体和 `ngx_http_script_regex_start_code` 的实现；第 2 题对比 `ngx_hash_find` 与顺序遍历 `rlcf->codes` 数组的差异。

---

> **下一章预告**：我们将深入 Nginx 的 HTTP 核心模块，探索内置变量体系、请求处理阶段（phase）和 location 匹配的完整链路——这是理解 Nginx HTTP 处理的钥匙。
