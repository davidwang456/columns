# 第25章：Grafana高可用与横向扩展

## 1. 项目背景

"公司最近做了一次全链路压测，500个开发同时打开Grafana看Dashboard，结果Grafana页面加载时间从2秒变成了30秒，部分用户直接白屏。运维查了下服务器，CPU才用了30%——问题不在服务器，而在架构。"

这是Grafana从"个人工具"升级为"企业平台"时必然遇到的门槛。Grafana的单实例部署足够支撑几十人的小团队，但当用户数增长到上百人、Dashboard数过千、查询并发上百时，单实例的瓶颈就暴露了：SQLite不支持并发写入、Go后端单进程处理所有请求、所有Session存在本地内存、前端静态资源无缓存……

解决这些瓶颈需要架构级别的优化：外部数据库、Session共享、负载均衡、CDN缓存。本章将把Grafana从"单机玩具"变成"企业级平台"，实现真正的高可用。

## 2. 项目设计

**小胖**（焦急地看着Loading圈圈转了30秒）：大师，我们团队现在有200个人用Grafana，高峰时段打开首页都要15秒。老板说要扩容，但我把Grafana从2核4G升到8核16G，效果不明显。是不是Grafana本身撑不住？

**大师**：先别急着堆资源。Grafana是Go写的，单进程处理请求，CPU利用率通常上不去。你真正的问题在于单实例架构有五个瓶颈点：

1. **数据库瓶颈**：SQLite不支持并发写入。200人同时打开Dashboard，每个Dashboard读取配置都要访问数据库，SQLite排队等锁。

2. **Session瓶颈**：Grafana把用户Session存在本地文件或本地数据库。如果你多实例部署（负载均衡后面两个Grafana），用户在实例A登录后，下一次请求被负载均衡到实例B，B的本地没有这个Session→用户被踢回登录页。

3. **查询瓶颈**：所有数据源查询都经过Grafana代理。100个用户×每个Dashboard 10个面板=1000个并发查询。Grafana的Go后端撑不住这么多并发HTTP代理连接。

4. **静态资源瓶颈**：Grafana前端是一个React SPA（约5MB）。在局域网还好，跨地域访问时首屏加载慢。

5. **配置瓶颈**：单机grafana.ini和provisioning，改配置要登到具体服务器上改。多实例时配置同步是个问题。

**小白**：那怎么逐个击破？

**大师**：标准的Grafana高可用架构长这样：

```
用户 → CDN (静态资源)
         ↓
     Nginx/ALB (负载均衡)
       ↙        ↘
  Grafana-1   Grafana-2  (多副本)
       ↘        ↙
    PostgreSQL (外部数据库)
         ↓
     Redis (Session/缓存共享)
```

**步骤一：迁移到PostgreSQL**

这是最重要的第一步。SQLite → PostgreSQL是Grafana从"单机工具"到"企业平台"的质变。

```ini
[database]
type = postgres
host = postgres:5432
name = grafana
user = grafana
password = grafana_secret
ssl_mode = disable
max_open_conn = 100
max_idle_conn = 10
conn_max_lifetime = 14400
```

迁移后效果：多个Grafana实例共享同一份Dashboard/DataSource/用户数据。1000并发读取也不会因为数据库锁而排队。

**步骤二：Redis共享Session**

默认Grafana的Session存在数据库中。但频繁读写Session会占用数据库连接。改成Redis：

```ini
[remote_cache]
type = redis
connstr = redis://redis:6379/0

[session]
provider = redis
provider_config = redis://redis:6379/1
cookie_name = grafana_session
cookie_secure = true
```

现在两个Grafana实例通过Redis共享Session和缓存。用户登录后无论被路由到哪个实例都保持登录状态。

**步骤三：负载均衡**

Nginx反向代理配置：

```nginx
upstream grafana_backend {
    server grafana-1:3000 weight=1 max_fails=3 fail_timeout=30s;
    server grafana-2:3000 weight=1 max_fails=3 fail_timeout=30s;
    
    keepalive 32;  # 长连接复用
}

server {
    listen 443 ssl;
    server_name grafana.example.com;
    
    # 静态资源走短期缓存
    location /public/ {
        proxy_pass http://grafana_backend;
        expires 1y;
        add_header Cache-Control "public, immutable";
    }
    
    # API和动态请求
    location / {
        proxy_pass http://grafana_backend;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        
        # WebSocket支持（用于实时刷新）
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }
}
```

**小胖**：那多实例的provisioning配置怎么同步？

**大师**：三种方案。

方案A：所有实例共享一个NFS目录挂载provisioning文件。

方案B：用ConfigMap/Kubernetes Secret管理，K8s环境下最佳。

方案C：CD流水线同步——Git Push → Jenkins → Ansible playbook同步到所有Grafana服务器的provisioning目录。

推荐方案C，因为配置文件纳入Git版本管理，可追溯。

**技术映射**：PostgreSQL = 公司数据库（所有部门共享，单人容量是瓶颈），Redis = 公共休息室（不用每次回家休息，就近就有），Nginx LB = 前台接待（把访客分流到空闲的接待员），CDN = 快递站（静态资源就近取，不用每次回总部拿）。

## 3. 项目实战

**环境准备**

在Docker Compose中添加Redis和第二Grafana实例：

```yaml
  redis:
    image: redis:7-alpine
    container_name: grafana-redis
    command: redis-server --appendonly yes
    volumes:
      - redis_data:/data

  grafana-2:
    image: grafana/grafana:11.0.0
    container_name: grafana-2
    environment:
      - GF_DATABASE_TYPE=postgres
      - GF_DATABASE_HOST=postgres:5432
      - GF_DATABASE_NAME=grafana
      - GF_DATABASE_USER=grafana
      - GF_DATABASE_PASSWORD=grafana_secret
      - GF_REMOTE_CACHE_TYPE=redis
      - GF_REMOTE_CACHE_CONNSTR=redis://redis:6379/0
      - GF_SESSION_PROVIDER=redis
      - GF_SESSION_PROVIDER_CONFIG=redis://redis:6379/1
      - GF_SERVER_HTTP_PORT=3001
    ports:
      - "3001:3001"
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_started

  nginx:
    image: nginx:alpine
    container_name: grafana-lb
    volumes:
      - ./nginx.conf:/etc/nginx/conf.d/default.conf
    ports:
      - "80:80"
    depends_on:
      - grafana
      - grafana-2

volumes:
  redis_data:
```

创建 `nginx.conf`：

```nginx
upstream grafana_backend {
    ip_hash;  # Session亲和性（有Redis时可关闭）
    server grafana:3000;
    server grafana-2:3001;
}

server {
    listen 80;
    server_name grafana.local;
    
    # 静态资源缓存
    location ~* \.(js|css|png|jpg|jpeg|gif|ico|woff|woff2|svg)$ {
        proxy_pass http://grafana_backend;
        proxy_cache_valid 200 1y;
        expires 1y;
        add_header Cache-Control "public, immutable";
    }
    
    # WebSocket
    location /api/live/ {
        proxy_pass http://grafana_backend;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }
    
    # 其他请求
    location / {
        proxy_pass http://grafana_backend;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_buffering off;
    }
}
```

**步骤一：验证PostgreSQL共享**

在两个Grafana实例上测试：
```bash
# 在grafana-1上创建Dashboard
curl -X POST -H "Content-Type: application/json" \
  -d '{"dashboard":{"title":"HA测试","panels":[]},"overwrite":true}' \
  http://localhost:3000/api/dashboards/db

# 在grafana-2上查询Dashboard
curl http://localhost:3001/api/search?type=dash-db
```

确认grafana-2能查询到grafana-1创建的Dashboard。

**步骤二：验证Redis Session共享**

```bash
# 在grafana-1上登录，记下cookie
SESSION_COOKIE=$(curl -s -c - http://localhost:3000/login \
  -d '{"user":"admin","password":"admin"}')

# 用同一个cookie访问grafana-2，应保持登录状态
curl -b "$SESSION_COOKIE" http://localhost:3001/api/user | jq '.login'
```

**步骤三：Grafana自身监控与告警**

为Grafana自身上下创建监控Dashboard：

**关键指标**：
```promql
# 各实例的活跃用户数
grafana_stat_active_users{instance=~"$instance"}

# API请求QPS
sum(rate(grafana_http_request_duration_seconds_count[5m])) by (instance)

# P99 API延迟
histogram_quantile(0.99,
  sum(rate(grafana_http_request_duration_seconds_bucket[5m])) by (le, instance))

# 数据库查询P99
histogram_quantile(0.99,
  sum(rate(grafana_db_request_duration_seconds_bucket[5m])) by (le))

# Redis缓存命中率
grafana_cache_redis_hits_total / 
  (grafana_cache_redis_hits_total + grafana_cache_redis_misses_total)
```

创建Grafana自身健康检查：
```bash
#!/bin/bash
# 监控健康脚本
TIMEOUT=5
GRAFANA_URL=${1:-http://localhost:3000}

# 健康检查
HEALTH=$(curl -s --max-time $TIMEOUT $GRAFANA_URL/api/health | jq -r '.database')

if [ "$HEALTH" != "ok" ]; then
    echo "CRITICAL: Grafana database health is $HEALTH"
    exit 2
fi

# 检查Dashboard查询延迟
LATENCY=$(curl -s --max-time $TIMEOUT -o /dev/null -w '%{time_total}' \
  $GRAFANA_URL/api/search?type=dash-db\&limit=1)
if (( $(echo "$LATENCY > 2.0" | bc -l) )); then
    echo "WARNING: API latency ${LATENCY}s > 2s"
    exit 1
fi

echo "OK: Grafana is healthy (API latency: ${LATENCY}s)"
exit 0
```

**步骤四：压测验证**

使用`hey`或`wrk`做简单压测：
```bash
# 安装hey
go install github.com/rakyll/hey@latest

# 压测Dashboard列表API（100并发×60秒）
hey -n 10000 -c 100 \
  -H "Authorization: Bearer $TOKEN" \
  http://localhost/api/search?type=dash-db

# 观察结果
# Total: 10000 requests
# Average: ~150ms (单实例可能200ms+)
# 99% in: ~500ms
```

**常见坑点**
1. **Redis连接失败导致无法登录**：如果Redis挂了，Grafana无法写入Session。配置Redis Sentinel或Cluster做高可用。
2. **PostgreSQL连接池耗尽**：默认max_open_conn=100，高并发下可能不够。监控`grafana_db_request_duration_seconds`，如果P99持续>1s，增加连接池。
3. **WebSocket断开**：在负载均衡（特别是K8s Ingress）后面，WebSocket连接可能在proxy超时后断开。配置更长的超时。
4. **Cache Keys冲突**：多实例通过Redis做缓存时，确保缓存Key包含了足够的信息（如实例ID），避免不同实例缓存互相覆盖。

## 4. 项目总结

**高可用架构组件清单**

| 组件 | 作用 | 单点故障影响 |
|------|------|------------|
| PostgreSQL | Dashboard/用户/数据源/告警数据存储 | 全部不可用 |
| Redis | Session/缓存 | 用户需重新登录 |
| Nginx/ALB | 负载均衡+SSL终止+静态缓存 | 不可访问 |
| Grafana × N | 应用实例（无状态） | 部分容量损失 |
| CDN | 静态资源加速 | 首屏加载变慢 |

**优点**
| 特性 | 说明 |
|------|------|
| 水平扩展 | 增加Grafana实例即可处理更多用户 |
| 无单点故障 | 数据库和Redis本身也可做HA |
| 滚动升级 | 逐个重启实例，用户无感知 |
| 统一配置 | 所有实例共享数据库，配置一致 |

**适用场景**
1. 超过50个用户的企业Grafana平台
2. 需要99.9%以上可用性的监控系统
3. 跨地域部署（Grafana就近访问、数据库统一）

**注意事项**
1. `secret_key`在所有Grafana实例中必须一致——它用于加密Session和API Token
2. 数据库性能是系统瓶颈——PostgreSQL比Grafana实例更早成为瓶颈
3. Grafana自身指标需要单独采集（每个实例独立暴露/metrics）
4. 升级Grafana版本时，所有实例必须同步升级（数据库schema可能变化）

**常见踩坑经验**
1. **两个Grafana实例同时运行Database Migration**：启动时如果同时检测到需要数据库迁移，会冲突。解决方案：逐个启动，Staggered restart。
2. **Nginx WebSocket超时**：Grafana的Live功能依赖WebSocket长连接。配置`proxy_read_timeout 3600s`防止超时断开。
3. **Session丢失**：`cookie_secure=true`但使用了HTTP访问→Cookie不设置→每次请求都是新Session。

**思考题**
1. 如果PostgreSQL数据库挂了，Grafana集群是否还能部分工作？哪些功能受影响，哪些不受影响？
2. 多实例Grafana如何使用Grafana自身的告警？如果告警规则评估在每个实例上都运行一次，会不会重复告警？
