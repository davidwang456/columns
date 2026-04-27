import textwrap

content = textwrap.dedent('''
# 第7章：负载均衡入门——Round Robin 与 IP Hash

> 源码关联：src/http/ngx_http_upstream_round_robin.c、src/http/modules/ngx_http_upstream_ip_hash_module.c、src/http/ngx_http_upstream.c

---

## 1. 项目背景

鲜果园的反向代理搭建完成后，订单服务顺利上线。然而，双十二大促第一天，事故就发生了——唯一的订单服务节点在流量洪峰下 CPU 飙到 100%，响应时间从 50ms 暴涨到 15 秒，大量用户卡在支付页面，投诉电话打爆了客服中心。

CTO 老张冲进技术部，拍着桌子问："不是早就说要上集群吗？为什么只有一个节点在跑？"小李解释道："上游服务确实部署了三台，但 Nginx 只配置了 proxy_pass 到一台机器的 IP，另外两台在闲着。"

这是典型的"有集群不会用"问题。Nginx 的 upstream 模块可以将请求分发到多个后端节点，实现负载均衡。但鲜果园的团队面对 upstream 配置时又犯了难：
- Round Robin（轮询）是最简单的，但每个请求都换节点，用户的购物车数据在不同节点间飘来飘去
- IP Hash 能保证同一个用户始终落在同一台机器上，但某个 IP 流量特别大时会造成"热点"
- 三台机器配置不一样（8 核/4 核/4 核），如何按能力分配流量？
- 某台机器宕机了，Nginx 能不能自动把它摘掉？

本章将从这场大促事故出发，深入讲解 Nginx 负载均衡的核心算法和配置细节，最终搭建一个支持故障转移的多节点上游集群。

---

## 2. 项目设计

**场景**：鲜果园的作战指挥室（其实就是会议室挂了块红布），小胖、小白和大师正在复盘双十二故障。

---

**小胖**：（看着监控大屏）大师，我按照文档配了三台机器到 upstream 里，但用户说购物车里的东西一会儿有一会儿没的，刷新页面就变了。

**大师**：你的 upstream 用的什么算法？

**小胖**：就默认的啊，我抄的示例代码。

**大师**：Nginx 默认的负载均衡算法是**加权轮询（Weighted Round Robin）**。它的逻辑很简单：按顺序把每个请求发给下一个节点。请求 1 给 A，请求 2 给 B，请求 3 给 C，请求 4 又给 A……

**小白**：那用户的购物车数据为什么不同步？

**大师**：因为三台后端节点各自存储了会话状态（Session）。用户的第一次请求落在 A 节点，购物车存在 A 的内存里；第二次请求落在 B 节点，B 的内存里没有这个购物车，就显示为空。

**小胖**：哦！所以轮询适合"无状态服务"，不适合有会话状态的服务？

**大师**：对。解决 Session 不一致有三种思路：

1. **会话保持（Session Affinity）**：让同一个用户的请求始终落到同一台后端，Nginx 的 `ip_hash` 就是干这个的
2. **共享 Session**：把 Session 放到 Redis 或数据库里，所有节点共享
3. **JWT Token**：会话状态完全放在客户端，后端不存储任何状态

**小白**：先讲讲 ip_hash 吧，这个听起来最直接。

**大师**：`ip_hash` 的原理是：对客户端 IP 地址计算哈希值，然后对后端节点数取模。`hash(IP) % N = 节点索引`。只要 IP 不变，算出来的节点索引就不变。

**小胖**：那如果某个 IP 是公司的出口 IP，后面有几百号人共用呢？

**大师**：这就是 ip_hash 的致命弱点——**热点问题**。几百个请求来自同一个 IP，全部砸到一台后端上，那台机器会被压垮。而且如果后端节点数量变化（增加或减少），哈希取模的结果会大面积变化，导致大量用户的会话迁移——这就是**哈希漂移**。

**小白**：有没有更好的方案？

**大师**：对于 Session 保持，更现代的做法是使用**一致性哈希（Consistent Hashing）**或**Sticky Cookie**。一致性哈希在节点变化时只影响一小部分 key，大大减少漂移。Sticky Cookie 则是 Nginx 在第一次响应时给客户端植入一个 Cookie，后续请求凭 Cookie 路由，不依赖 IP。

**小胖**：这些以后再说，先解决眼前的问题。三台机器的配置不一样，怎么按能力分流量？

**大师**：用**权重（weight）**。Nginx 的 Round Robin 支持加权轮询：

```nginx
upstream backend {
    server 10.0.1.10 weight=5;   # 8 核机器，权重 5
    server 10.0.1.11 weight=3;   # 4 核机器，权重 3
    server 10.0.1.12 weight=2;   # 4 核机器，权重 2
}
```

总权重是 10，第一台机器分到 50% 的流量，第二台 30%，第三台 20%。

**小白**：如果某台机器挂了，Nginx 会自动跳过它吗？

**大师**：Nginx 有**被动健康检查**机制：

```nginx
server 10.0.1.10 weight=5 max_fails=3 fail_timeout=30s;
```

含义是：如果这台机器在 30 秒内连续失败 3 次（连接失败或超时），Nginx 会把它标记为 `down`，暂时不再向它发送请求。30 秒后再次尝试，如果恢复了就重新加入轮询。

**小胖**：那备份节点呢？我想留一台机器平时不干活，等主节点挂了再上。

**大师**：用 `backup` 参数：

```nginx
upstream backend {
    server 10.0.1.10;
    server 10.0.1.11;
    server 10.0.1.12 backup;   # 备用节点，只有主节点全挂时才启用
}
```

**小白**：还有一个问题，Round Robin 的分配真的均匀吗？会不会出现某台机器连续收到多个请求的情况？

**大师**：加权轮询的实现不是简单的"每来 10 个请求分 5/3/2"，而是使用**平滑加权轮询（Smooth Weighted Round Robin）**算法。每个节点都有一个当前权重，每次选择当前权重最大的节点，然后减去总权重。这样能保证流量分配既符合权重比例，又不会出现连续的请求扎堆。

---

## 3. 项目实战

### 环境准备

- **Nginx 版本**：1.31.0
- **后端服务**：四个 Python Flask 节点（模拟不同配置的服务器）
- **操作系统**：Ubuntu 22.04 / WSL2

### 步骤一：启动多个后端节点

```bash
mkdir -p /var/www/backend/cluster

# 生成节点脚本（带权重标识）
for port in 8081 8082 8083 8084; do
    weight=$(( port == 8081 ? 5 : port == 8082 ? 3 : port == 8083 ? 2 : 1 ))
    role=$(( port == 8084 ? "backup" : "normal" ))
    cat > /var/www/backend/cluster/app_${port}.py << PYEOF
from flask import Flask, request
import socket
app = Flask(__name__)

@app.route('/')
def index():
    return {
        "node": socket.gethostname(),
        "port": ${port},
        "weight": ${weight},
        "role": "${role}",
        "from": request.headers.get('X-Real-IP', 'unknown')
    }

@app.route('/health')
def health():
    return {"status": "ok"}

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=${port})
PYEOF
    nohup python3 /var/www/backend/cluster/app_${port}.py > /tmp/backend_${port}.log 2>&1 &
done

sleep 2

# 验证所有节点
for port in 8081 8082 8083 8084; do
    curl -s http://localhost:${port}/
    echo
done
```

### 步骤二：Round Robin 负载均衡配置

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

    log_format upstream_log '$remote_addr - [$time_local] '
                            '"$request" $status '
                            'upstream=$upstream_addr '
                            'response_time=$upstream_response_time '
                            'upstream_status=$upstream_status';

    access_log /var/log/nginx/access.log upstream_log;

    # ========== Round Robin 上游集群 ==========
    upstream roundrobin_backend {
        server 127.0.0.1:8081 weight=5 max_fails=2 fail_timeout=10s;
        server 127.0.0.1:8082 weight=3 max_fails=2 fail_timeout=10s;
        server 127.0.0.1:8083 weight=2 max_fails=2 fail_timeout=10s;
        server 127.0.0.1:8084 backup;
        keepalive 32;
    }

    # ========== IP Hash 上游集群 ==========
    upstream iphash_backend {
        ip_hash;
        server 127.0.0.1:8081 weight=5;
        server 127.0.0.1:8082 weight=3;
        server 127.0.0.1:8083 weight=2;
    }

    server {
        listen       80;
        server_name  lb.xianguoyuan.com;

        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;

        # Round Robin 入口
        location /rr/ {
            proxy_pass http://roundrobin_backend/;
            proxy_http_version 1.1;
            proxy_set_header Connection "";
        }

        # IP Hash 入口
        location /ip/ {
            proxy_pass http://iphash_backend/;
            proxy_http_version 1.1;
            proxy_set_header Connection "";
        }

        # 健康检查端点
        location /health {
            access_log off;
            return 200 '{"status":"ok"}\\n';
            add_header Content-Type application/json;
        }
    }
}
```

### 步骤三：验证与测试

**3.1 验证 Round Robin 分发**

```bash
# 连续请求 10 次，观察节点分布
for i in {1..10}; do
    curl -s http://localhost/rr/ | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['port'], d['weight'])"
done
```

预期输出（近似比例 5:3:2）：

```
8081 5
8082 3
8081 5
8083 2
8081 5
8082 3
8081 5
8081 5
8082 3
8083 2
```

**3.2 验证 IP Hash 会话保持**

```bash
# 同一 IP 连续请求，应始终落在同一节点
for i in {1..5}; do
    curl -s http://localhost/ip/ | python3 -c "import sys,json; d=json.load(sys.stdin); print('port:', d['port'])"
done
```

预期输出（同一端口重复 5 次）：

```
port: 8081
port: 8081
port: 8081
port: 8081
port: 8081
```

**3.3 验证故障转移**

```bash
# 停止 8081 节点
kill $(lsof -t -i:8081)

# 继续请求，应自动切换到其他节点
for i in {1..5}; do
    curl -s http://localhost/rr/ | python3 -c "import sys,json; d=json.load(sys.stdin); print('port:', d['port'])"
done

# 查看 Nginx 错误日志
sudo tail -n 5 /var/log/nginx/error.log
# 应能看到 upstream connect failed 和节点被标记 down 的记录

# 恢复 8081
nohup python3 /var/www/backend/cluster/app_8081.py > /tmp/backend_8081.log 2>&1 &
```

**3.4 验证备份节点**

```bash
# 停止所有主节点（8081、8082、8083）
kill $(lsof -t -i:8081)
kill $(lsof -t -i:8082)
kill $(lsof -t -i:8083)

# 请求应落到备份节点 8084
curl -s http://localhost/rr/
# 预期返回中包含 "port": 8084, "role": "backup"

# 恢复所有节点
for port in 8081 8082 8083; do
    nohup python3 /var/www/backend/cluster/app_${port}.py > /tmp/backend_${port}.log 2>&1 &
done
```

**3.5 压测验证负载分布**

```bash
sudo apt install -y apache2-utils

# 对 Round Robin 接口压测 10000 请求
ab -n 10000 -c 100 http://localhost/rr/

# 统计日志中的 upstream 分布
sudo awk '{print $6}' /var/log/nginx/access.log | sort | uniq -c | sort -rn
# 预期 8081 约占 50%，8082 约占 30%，8083 约占 20%
```

### 步骤四：源码速览——Round Robin 算法

打开 `src/http/ngx_http_upstream_round_robin.c`，查看平滑加权轮询的核心逻辑：

```c
// src/http/ngx_http_upstream_round_robin.c
ngx_int_t
ngx_http_upstream_get_round_robin_peer(ngx_peer_connection_t *pc, void *data)
{
    ngx_http_upstream_rr_peer_data_t  *rrp = data;
    ngx_http_upstream_rr_peer_t       *peer;
    ngx_http_upstream_rr_peers_t      *peers;

    peers = rrp->peers;
    peer = peers->peer;

    // 如果只有一个节点，直接返回
    if (peers->number == 1) {
        pc->cached = 0;
        pc->connection = NULL;
        rrp->current = peer;
        pc->sockaddr = peer->sockaddr;
        pc->socklen = peer->socklen;
        pc->name = &peer->name;
        return NGX_OK;
    }

    // 平滑加权轮询算法
    // 1. 找到当前权重最大的节点
    // 2. 减去总权重
    // 3. 所有节点加上自身权重
    // 这样能保证既按权重分配，又避免连续请求扎堆

    ngx_uint_t  total = 0;
    ngx_http_upstream_rr_peer_t *best = NULL;

    for (i = 0; i < peers->number; i++) {
        peer = &peers->peer[i];

        // 跳过不健康的节点
        if (peer->down) {
            continue;
        }

        // 跳过当前失败次数过多的节点
        if (peer->max_fails && peer->fails >= peer->max_fails) {
            // 检查 fail_timeout 是否已过
            if (ngx_time() - peer->checked <= peer->fail_timeout) {
                continue;
            }
            peer->fails = 0;  // 超时后重置失败计数
        }

        // 平滑加权轮询的核心计算
        peer->current_weight += peer->effective_weight;
        total += peer->effective_weight;

        if (peer->effective_weight < peer->weight) {
            peer->effective_weight++;
        }

        if (best == NULL || peer->current_weight > best->current_weight) {
            best = peer;
        }
    }

    if (best == NULL) {
        return NGX_BUSY;  // 所有节点都不可用
    }

    // 选中的节点减去总权重
    best->current_weight -= total;
    best->checked = ngx_time();
    rrp->current = best;

    pc->sockaddr = best->sockaddr;
    pc->socklen = best->socklen;
    pc->name = &best->name;

    return NGX_OK;
}
```

**代码注释**：
- `peer->current_weight` 是当前权重，每次选择前所有健康节点都会加上自身的 `effective_weight`
- `best->current_weight -= total` 确保选中的节点不会一直被选中，实现平滑分配
- `peer->fails` 和 `peer->max_fails` 实现被动健康检查，超过阈值后节点被跳过
- `peer->checked` 记录最后一次检查时间，用于判断 `fail_timeout` 是否过期

---

## 4. 项目总结

### 优点与缺点

| 维度 | Round Robin | IP Hash |
|------|-------------|---------|
| 分配均匀性 | 高（平滑算法） | 中（依赖 IP 分布） |
| 会话保持 | 不支持 | 支持 |
| 热点风险 | 低 | 高（共享出口 IP） |
| 节点变化影响 | 无 | 大（哈希漂移） |
| 权重支持 | 支持 | 支持但有限 |
| 适用场景 | 无状态服务 | 有状态会话（临时方案） |

### 适用场景

1. **无状态 API 服务**：订单查询、商品列表、用户资料等无需会话保持的接口
2. **文件上传/下载服务**：大文件分发，利用多节点分散带宽压力
3. **计算密集型任务**：图片处理、数据清洗，按权重分配到不同配置的机器
4. **灰度发布**：通过权重控制新版本节点的流量比例
5. **灾备切换**：backup 节点在主集群故障时自动接管

**不适用场景**：
1. **需要强一致性的状态服务**：如数据库主从、分布式锁服务
2. **长连接 WebSocket**：Round Robin 会导致同一用户的 WebSocket 连接分散到不同节点
3. **对会话一致性要求高的电商购物车**：IP Hash 只是权宜之计，应使用共享 Session 或 JWT

### 注意事项

1. **max_fails 和 fail_timeout 要合理设置**：内网服务可以设置较小的 fail_timeout（5-10 秒），外网服务可以适当放宽
2. **backup 节点不是冷备**：当主节点全部恢复后，backup 节点会自动停止接收新请求，但已有连接会继续处理完
3. **keepalive 连接池很重要**：upstream 中开启 `keepalive` 能显著减少 TCP 握手开销，提高整体吞吐量
4. **权重调整后需 reload**：修改 upstream 的 weight 不需要重启 Nginx，reload 即可生效

### 常见踩坑经验

**案例一：IP Hash 后某节点 CPU 100%**
- **现象**：使用 ip_hash 后，8081 节点 CPU 长期 100%，其他节点空闲
- **根因**：公司出口 IP 段经过 NAT 转换，大量用户共享同一个公网 IP，全部落到同一节点
- **解决**：放弃 ip_hash，改用共享 Session（Redis）或 Sticky Cookie

**案例二：节点恢复后流量不均**
- **现象**：某节点宕机 5 分钟后恢复，但流量分配明显不均，该节点接收的请求远少于其他节点
- **根因**：Nginx 的平滑加权轮询算法中，故障节点的 current_weight 在故障期间没有更新，恢复后需要一段时间重新积累权重
- **解决**：这是算法的正常行为，等待数十秒到数分钟即可自动平衡；或手动 reload 配置重置权重状态

**案例三：backup 节点在主节点恢复后仍接收请求**
- **现象**：backup 节点在主节点恢复后继续收到新请求
- **根因**：客户端的长连接（Keep-Alive）仍然连在 backup 节点上，新请求复用了这些连接
- **解决**：这是正常行为，等待客户端连接自然关闭，或设置较短的 keepalive_timeout

### 思考题

1. **进阶题**：平滑加权轮询（Smooth Weighted Round Robin）算法中，`peer->effective_weight` 在什么情况下会小于 `peer->weight`？请结合 `ngx_http_upstream_get_round_robin_peer` 的源码，分析 `effective_weight` 的衰减和恢复机制。

2. **进阶题**：`ip_hash` 算法使用了哪种哈希函数？当后端节点数量变化时，为什么会导致大量会话漂移？请对比 `ip_hash` 和一致性哈希（`hash` 指令 + consistent 参数）在源码实现上的差异。

> 答案提示：第 1 题关注 `effective_weight` 在节点失败时的递减逻辑（`peer->effective_weight -= peer->weight / peer->max_fails`）；第 2 题对比 `ngx_http_upstream_ip_hash_module.c` 的简单取模哈希与 `ngx_http_upstream_hash_module.c` 的一致性哈希（虚拟节点）实现。

---

> **下一章预告**：我们将跨越 HTTP 代理的边界，进入经典 LNMP/LAMP 架构的核心——FastCGI 协议。从协议帧结构到 PHP-FPM 协同，搭建一个完整的动态 Web 服务栈。
''').strip()

with open('column/chapter07.md', 'w', encoding='utf-8') as f:
    f.write(content)
print('Chapter 7 written, length:', len(content))
