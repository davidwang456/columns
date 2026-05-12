# 第19章：Docker Compose多节点集群生产实践

## 1 项目背景

某创业公司，技术团队仅三人，却需要支撑日均10万+登录请求的认证服务。业务处于高速增长期，新功能频繁上线，用户规模和并发量每月都在攀升。核心挑战是：搭建一套生产可用的Keycloak集群，确保高可用、可维护、可扩展——但团队没有Kubernetes集群，预算也撑不起采购容器编排平台。手头只有几台云服务器，需要靠Docker Compose快速交付一套“够用”的生产级集群。

Docker Compose给中小团队带来极大便利——不用学K8s那套复杂的API和控制器概念，一份YAML文件定义所有服务，`docker compose up -d`一条命令拉起整套集群。但“能用”和“好用”之间隔着一条深沟：容器挂了怎么自愈？PostgreSQL挂了Keycloak会怎样？健康检查怎么配才能让Nginx不把流量打到故障节点？日志写到哪才不会撑爆磁盘？开发/测试/生产三套环境变量怎么管理？数据库连接池配小了高峰期请求排队，配大了把PG连接数吃满——光靠默认值，迟早出事。

本质问题在于：Docker Compose的诞生场景是本地开发和单机部署，要让它扛起生产流量，需要补充大量非功能性需求——自动重启、健康检查、日志收集、资源限制、连接池调优、多环境配置模板化。这些在K8s里是生态标配（liveness/readiness探针、HPA、ConfigMap、日志驱动），但在Docker Compose体系下只能靠谨慎的编排文件和运维脚本来弥补。

环境变量管理是第一个坑。.env文件散落在项目各处，开发改一下、预发布改一下、生产再改一下，时间一长没人知道哪个文件是“真相来源”。容器重启后日志随容器消亡而丢失，出问题想回溯根本不可能。健康检查配得不完善，`depends_on`只检查容器状态而非服务就绪状态，负载均衡器会在Keycloak还没完全启动时就把请求打过去，返回502。数据库连接池参数全靠猜，并发一上来连接耗尽，认证请求大面积超时。这些都是Docker Compose生产化的典型痛点，也是本章要逐一击破的问题。

一句话概括本章目标：用Docker Compose编排三节点Keycloak集群 + PostgreSQL + Nginx，在不引入K8s的前提下，达到生产可用标准。

## 2 项目设计——剧本式交锋对话

**小胖**（兴奋地比划着）：大师，我看Docker Compose不就是把`docker run`的参数写成YAML吗？就像搭积木——先把数据库搭好，再把Keycloak搭上去，最后放个Nginx当门面。为啥要专门写个`docker-compose.yml`？我挨个`docker run`不也能跑吗？

**大师**（笑了笑）：小胖，你想想，如果你有三台服务器、每个要起5个容器、每个容器10个环境变量、还要挂载3个卷——你准备写多少行`docker run`？而且数据库挂了Keycloak怎么处理？节点重启顺序谁来保证？Docker Compose的YAML本质上是在“声明式地描述系统的期望状态”，而`docker run`是“命令式地逐个执行”。前者的价值在于：一键拉起、一键销毁、可版本管理、可团队协作、可复用环境。你今天的YAML，明天换台机器照跑；你的`docker run`脚本，明天可能就忘了加哪个参数。

**小白**（认真地追问）：大师，我看了官方文档，有几个地方很困惑。第一，`depends_on`和`healthcheck`到底有什么区别？是不是配了`depends_on`就保证数据库启动了Keycloak才启动？第二，`restart: always`和`restart: unless-stopped`到底选哪个？什么场景用什么？第三，`.env`文件和`docker-compose.override.yml`文件各自的职责是什么？感觉都能覆盖配置？第四，Keycloak的`KC_DB_POOL_INITIAL_SIZE`、`KC_DB_POOL_MIN_SIZE`、`KC_DB_POOL_MAX_SIZE`这三个参数到底怎么设置？我对HikariCP连接池不了解。

**大师**（正色道）：问得好！这四个问题恰恰是Docker Compose生产化的核心。

第一，`depends_on`和`healthcheck`的关系。`depends_on`的默认行为只检查容器状态——容器启动了就算完成，不等服务就绪。比如PostgreSQL容器可能启动了，但pg进程还在做WAL恢复，此时Keycloak连上去直接失败。所以必须配合`condition: service_healthy`使用，让Docker Compose等待健康检查通过后才启动下游服务。这是生产配置的第一道防线。

第二，重启策略。`restart: always`意味着无论容器以什么方式退出（包括正常停止后重启Docker），都会自动重启，适合生产环境所有核心服务。`restart: unless-stopped`比always多一层克制——如果你手动`docker compose stop`了容器，它不会在重启Docker Engine时自动复活，更符合运维直觉，这也是生产环境推荐策略。`restart: on-failure`只在容器以非零退出码终止时重启，适合批处理/初始化任务（如数据库初始化脚本）——执行完就退出，不要反复重启。

第三，`.env` vs `docker-compose.override.yml`。`.env`只负责注入环境变量，它影响的是YAML中`${VAR}`引用，不改变服务结构。`override.yml`可以做结构性覆盖——添加/删除服务、修改端口映射、挂载额外卷。两者的职责边界是：值用`.env`，结构用`override.yml`。生产实践通常是：`.env`存公共默认值，`.env.prod`存生产密码等敏感值（不提交Git），`docker-compose.override.yml`在本地挂载源码目录开发用，生产不上传。

第四，HikariCP连接池参数。`KC_DB_POOL_INITIAL_SIZE`是连接池启动时预先创建的连接数，建议10-20，应对冷启动。`KC_DB_POOL_MIN_SIZE`是连接池维持的最小空闲连接数，建议等于`INITIAL_SIZE`，避免连接被频繁建立和销毁。`KC_DB_POOL_MAX_SIZE`是连接池最大连接数——这是最关键的参数。按公式估算：最大连接数 ≈（Core数 × 2 + 有效磁盘数），但实际经验是：每个Keycloak节点建议20-50个连接，三节点集群就是60-150个总连接。PostgreSQL默认max_connections是100，所以Keycloak集群总连接数不要超过PG的max_connections减去10（预留超级用户连接和复制连接）。我的建议是：PostgreSQL侧调整`max_connections = 200`，Keycloak每节点`MAX_SIZE = 50`，三节点总计150，留50给其他操作。

**小胖**（若有所思）：好像明白了……那Docker Compose到底能不能替代K8s啊？我们公司也没人懂K8s，就靠这个行不行？

**大师**：这个问题要诚实回答。Docker Compose能跑，但有硬天花板。首当其冲就是**单主机限制**——Docker Compose编排的所有容器都跑在同一台Docker Engine上，无法跨主机。你想把三个Keycloak节点分散到三台机器上互备？Compose做不到。其次，**无原生服务发现**——节点之间如何相互感知？K8s有DNS服务发现，Compose下只能硬编码容器名或借助Docker DNS（同一个compose网络下容器名可解析，但跨compose就不行）。再次，**没有声明式扩缩容**——流量激增时不能自动增加Keycloak节点。还有**滚动更新靠脚本**（本章实战部分会演示），不像K8s的RollingUpdate那样有原生支持。

不过别灰心。如果你的流量稳定、节点数固定、机器规模在3-5台以内，Compose完全胜任。而且Compose到K8s有一条清晰的迁移路径：环境变量改ConfigMap/Secret、卷改PVC、健康检查改探针、Compose网络改Service。把配置管理做好（就像本章的`.env`分层设计），迁移时大部分配置可以直接平移。Kompose等工具甚至能自动转换compose文件为K8s资源，只是生产级迁移还需手动调整。

**关键映射总结**：`depends_on` + `healthcheck` → K8s的initContainers + readinessProbe；`restart: unless-stopped` → K8s的Deployment控制器自动重建Pod；日志配置 → K8s的logging driver/Fluentd；环境变量分层 → ConfigMap（非敏感） + Secret（敏感）；Volume → PVC/PV。

## 3 项目实战

### 环境准备

- Docker Engine ≥ 24.x 或 Docker Desktop 最新版
- Docker Compose ≥ v2.20（`docker compose`插件形式，非`docker-compose`独立二进制）
- 服务器内存 ≥ 8GB（三节点Keycloak + PostgreSQL + Nginx）
- 操作系统：Ubuntu 22.04 / CentOS Stream 9 （也可Windows/macOS开发环境）

### 步骤1：目录结构设计

```
keycloak-cluster/
├── .env                          # 公共环境变量（默认值）
├── .env.dev                      # 开发环境覆盖
├── .env.prod                     # 生产环境覆盖（敏感信息）
├── docker-compose.yml            # 主编排文件
├── docker-compose.override.yml   # 本地开发覆盖（挂载源码等）
├── docker-compose.prod.yml       # 生产环境额外服务
├── config/
│   ├── nginx.conf                # Nginx负载均衡配置
│   ├── cache-ispn.xml            # Infinispan集群缓存配置（JDBC-PING）
│   └── keycloak.conf             # Keycloak SPI配置
├── themes/
│   └── my-brand/                 # 自定义登录主题
├── scripts/
│   ├── init-db.sh                # PostgreSQL初始化脚本
│   └── health-check.sh           # 集群健康检查脚本
├── logs/                         # Nginx和Keycloak日志持久化
├── metrics/                      # Prometheus指标存储
└── backups/                      # 数据库备份目录
```

### 步骤2：`.env`文件设计

这是配置管理的核心。采用分层覆盖策略：`.env`为公共默认值，`.env.prod`为生产环境覆盖，`.env.dev`为本地开发覆盖。Docker Compose加载优先级是：命令行`--env-file`指定的文件 > `docker-compose.override.yml`中的值 > `docker-compose.yml`中的值 > `.env`文件的默认值。

```bash
# .env - 公共默认配置（提交到Git仓库）
KEYCLOAK_VERSION=26.1
POSTGRES_VERSION=16

# === 数据库 ===
DB_NAME=keycloak
DB_USER=keycloak
DB_PASSWORD=change_me_in_prod

# === Keycloak管理账号 ===
KC_ADMIN_USER=admin
KC_ADMIN_PASSWORD=change_me_in_prod
KC_HOSTNAME=localhost

# === 数据库连接池（HikariCP） ===
DB_POOL_INITIAL_SIZE=5
DB_POOL_MIN_SIZE=5
DB_POOL_MAX_SIZE=50

# === 日志级别 ===
LOG_LEVEL=INFO

# === 负载均衡端口 ===
LB_PORT=80
```

```bash
# .env.prod - 生产环境覆盖（不提交到Git仓库）
DB_PASSWORD=Secure@Prod#2024!
KC_ADMIN_PASSWORD=SuperSecure@Admin123!
KC_HOSTNAME=auth.mycompany.com
DB_POOL_MAX_SIZE=100
LOG_LEVEL=WARN
```

```bash
# .env.dev - 开发环境覆盖
DB_PASSWORD=devpass
KC_ADMIN_PASSWORD=devpass
KC_HOSTNAME=localhost
DB_POOL_MAX_SIZE=10
LOG_LEVEL=DEBUG
```

### 步骤3：生产级`docker-compose.yml`

```yaml
version: '3.8'

services:
  # ========== PostgreSQL ==========
  postgres:
    image: postgres:${POSTGRES_VERSION:-16}-alpine
    container_name: kc-postgres
    restart: unless-stopped
    environment:
      POSTGRES_DB: ${DB_NAME}
      POSTGRES_USER: ${DB_USER}
      POSTGRES_PASSWORD: ${DB_PASSWORD}
    volumes:
      - pgdata:/var/lib/postgresql/data
      - ./scripts/init-db.sh:/docker-entrypoint-initdb.d/init.sh:ro
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${DB_USER} -d ${DB_NAME}"]
      interval: 10s
      timeout: 5s
      retries: 5
      start_period: 15s
    networks:
      - kc-network
    deploy:
      resources:
        limits:
          memory: 1G
          cpus: '1'
    logging:
      driver: "json-file"
      options:
        max-size: "50m"
        max-file: "5"

  # ========== Keycloak节点1 ==========
  keycloak-1:
    image: quay.io/keycloak/keycloak:${KEYCLOAK_VERSION}
    container_name: kc-node-1
    restart: unless-stopped
    command:
      - start
      - --optimized
      - --hostname=${KC_HOSTNAME}
      - --proxy-headers=xforwarded
    environment:
      KC_DB: postgres
      KC_DB_URL_HOST: postgres
      KC_DB_URL_DATABASE: ${DB_NAME}
      KC_DB_USERNAME: ${DB_USER}
      KC_DB_PASSWORD: ${DB_PASSWORD}
      KC_BOOTSTRAP_ADMIN_USERNAME: ${KC_ADMIN_USER}
      KC_BOOTSTRAP_ADMIN_PASSWORD: ${KC_ADMIN_PASSWORD}
      KC_HOSTNAME: ${KC_HOSTNAME}
      KC_PROXY_HEADERS: xforwarded
      KC_HTTP_ENABLED: "true"
      KC_HEALTH_ENABLED: "true"
      KC_METRICS_ENABLED: "true"
      KC_LOG_LEVEL: ${LOG_LEVEL:-INFO}
      KC_LOG_CONSOLE_OUTPUT: json
      KC_CACHE: ispn
      KC_CACHE_STACK: jdbc-ping
      KC_DB_POOL_INITIAL_SIZE: ${DB_POOL_INITIAL_SIZE}
      KC_DB_POOL_MIN_SIZE: ${DB_POOL_MIN_SIZE}
      KC_DB_POOL_MAX_SIZE: ${DB_POOL_MAX_SIZE}
    volumes:
      - ./themes/my-brand:/opt/keycloak/themes/my-brand:ro
      - ./config/cache-ispn.xml:/opt/keycloak/conf/cache-ispn.xml:ro
    depends_on:
      postgres:
        condition: service_healthy
    healthcheck:
      test: ["CMD-SHELL", "curl -f -s http://localhost:8080/health/ready || exit 1"]
      interval: 15s
      timeout: 5s
      retries: 3
      start_period: 60s
    networks:
      - kc-network
    deploy:
      resources:
        limits:
          memory: 2G
          cpus: '2'
        reservations:
          memory: 512M
          cpus: '0.5'
    logging:
      driver: "json-file"
      options:
        max-size: "100m"
        max-file: "3"

  # ========== Keycloak节点2 ==========
  keycloak-2:
    image: quay.io/keycloak/keycloak:${KEYCLOAK_VERSION}
    container_name: kc-node-2
    restart: unless-stopped
    command:
      - start
      - --optimized
      - --hostname=${KC_HOSTNAME}
      - --proxy-headers=xforwarded
    environment:
      KC_DB: postgres
      KC_DB_URL_HOST: postgres
      KC_DB_URL_DATABASE: ${DB_NAME}
      KC_DB_USERNAME: ${DB_USER}
      KC_DB_PASSWORD: ${DB_PASSWORD}
      KC_BOOTSTRAP_ADMIN_USERNAME: ${KC_ADMIN_USER}
      KC_BOOTSTRAP_ADMIN_PASSWORD: ${KC_ADMIN_PASSWORD}
      KC_HOSTNAME: ${KC_HOSTNAME}
      KC_PROXY_HEADERS: xforwarded
      KC_HTTP_ENABLED: "true"
      KC_HEALTH_ENABLED: "true"
      KC_METRICS_ENABLED: "true"
      KC_LOG_LEVEL: ${LOG_LEVEL:-INFO}
      KC_LOG_CONSOLE_OUTPUT: json
      KC_CACHE: ispn
      KC_CACHE_STACK: jdbc-ping
      KC_DB_POOL_INITIAL_SIZE: ${DB_POOL_INITIAL_SIZE}
      KC_DB_POOL_MIN_SIZE: ${DB_POOL_MIN_SIZE}
      KC_DB_POOL_MAX_SIZE: ${DB_POOL_MAX_SIZE}
    volumes:
      - ./themes/my-brand:/opt/keycloak/themes/my-brand:ro
      - ./config/cache-ispn.xml:/opt/keycloak/conf/cache-ispn.xml:ro
    depends_on:
      postgres:
        condition: service_healthy
    healthcheck:
      test: ["CMD-SHELL", "curl -f -s http://localhost:8080/health/ready || exit 1"]
      interval: 15s
      timeout: 5s
      retries: 3
      start_period: 60s
    networks:
      - kc-network
    deploy:
      resources:
        limits:
          memory: 2G
          cpus: '2'
        reservations:
          memory: 512M
          cpus: '0.5'
    logging:
      driver: "json-file"
      options:
        max-size: "100m"
        max-file: "3"

  # ========== Keycloak节点3 ==========
  keycloak-3:
    image: quay.io/keycloak/keycloak:${KEYCLOAK_VERSION}
    container_name: kc-node-3
    restart: unless-stopped
    command:
      - start
      - --optimized
      - --hostname=${KC_HOSTNAME}
      - --proxy-headers=xforwarded
    environment:
      KC_DB: postgres
      KC_DB_URL_HOST: postgres
      KC_DB_URL_DATABASE: ${DB_NAME}
      KC_DB_USERNAME: ${DB_USER}
      KC_DB_PASSWORD: ${DB_PASSWORD}
      KC_BOOTSTRAP_ADMIN_USERNAME: ${KC_ADMIN_USER}
      KC_BOOTSTRAP_ADMIN_PASSWORD: ${KC_ADMIN_PASSWORD}
      KC_HOSTNAME: ${KC_HOSTNAME}
      KC_PROXY_HEADERS: xforwarded
      KC_HTTP_ENABLED: "true"
      KC_HEALTH_ENABLED: "true"
      KC_METRICS_ENABLED: "true"
      KC_LOG_LEVEL: ${LOG_LEVEL:-INFO}
      KC_LOG_CONSOLE_OUTPUT: json
      KC_CACHE: ispn
      KC_CACHE_STACK: jdbc-ping
      KC_DB_POOL_INITIAL_SIZE: ${DB_POOL_INITIAL_SIZE}
      KC_DB_POOL_MIN_SIZE: ${DB_POOL_MIN_SIZE}
      KC_DB_POOL_MAX_SIZE: ${DB_POOL_MAX_SIZE}
    volumes:
      - ./themes/my-brand:/opt/keycloak/themes/my-brand:ro
      - ./config/cache-ispn.xml:/opt/keycloak/conf/cache-ispn.xml:ro
    depends_on:
      postgres:
        condition: service_healthy
    healthcheck:
      test: ["CMD-SHELL", "curl -f -s http://localhost:8080/health/ready || exit 1"]
      interval: 15s
      timeout: 5s
      retries: 3
      start_period: 60s
    networks:
      - kc-network
    deploy:
      resources:
        limits:
          memory: 2G
          cpus: '2'
        reservations:
          memory: 512M
          cpus: '0.5'
    logging:
      driver: "json-file"
      options:
        max-size: "100m"
        max-file: "3"

  # ========== Nginx负载均衡 ==========
  nginx:
    image: nginx:1.25-alpine
    container_name: kc-lb
    restart: unless-stopped
    ports:
      - "${LB_PORT:-80}:80"
      - "443:443"
    volumes:
      - ./config/nginx.conf:/etc/nginx/nginx.conf:ro
      - ./logs/nginx:/var/log/nginx
    depends_on:
      keycloak-1:
        condition: service_healthy
      keycloak-2:
        condition: service_healthy
      keycloak-3:
        condition: service_healthy
    networks:
      - kc-network
    deploy:
      resources:
        limits:
          memory: 256M
          cpus: '0.5'
    logging:
      driver: "json-file"
      options:
        max-size: "50m"
        max-file: "3"

networks:
  kc-network:
    driver: bridge
    ipam:
      config:
        - subnet: 172.28.0.0/16

volumes:
  pgdata:
    driver: local
```

### 步骤4：关键配置文件

**`config/nginx.conf`**——Nginx负载均衡配置，使用`least_conn`算法将流量均匀分发到三个Keycloak节点，同时配置upstream健康检查：

```nginx
events {
    worker_connections 4096;
    use epoll;
}

http {
    upstream keycloak_cluster {
        least_conn;
        server kc-node-1:8080 max_fails=3 fail_timeout=30s;
        server kc-node-2:8080 max_fails=3 fail_timeout=30s;
        server kc-node-3:8080 max_fails=3 fail_timeout=30s;
    }

    server {
        listen 80;
        server_name _;

        # 健康检查端点（Nginx自身）
        location /lb-health {
            access_log off;
            return 200 "OK\n";
            add_header Content-Type text/plain;
        }

        location / {
            proxy_pass http://keycloak_cluster;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto $scheme;
            proxy_buffer_size 128k;
            proxy_buffers 4 256k;
            proxy_busy_buffers_size 256k;
            proxy_read_timeout 60s;
            proxy_send_timeout 60s;
        }
    }
}
```

**`config/cache-ispn.xml`**——使用JDBC-PING发现机制让Keycloak节点通过数据库相互发现，实现分布式缓存。核心配置是`jdbc-ping`的`connection_driver`和`connection_url`指向同一PostgreSQL数据库。

**`scripts/init-db.sh`**——数据库初始化脚本：

```bash
#!/bin/bash
# PostgreSQL初始化脚本
set -e

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    -- 调整连接数配置
    ALTER SYSTEM SET max_connections = '200';
    ALTER SYSTEM SET shared_buffers = '256MB';
    ALTER SYSTEM SET effective_cache_size = '512MB';
    SELECT pg_reload_conf();
EOSQL

echo "PostgreSQL初始化完成: max_connections=200"
```

### 步骤5：多环境启动命令

```bash
# === 本地开发环境 ===
# Docker Compose自动加载 docker-compose.yml + docker-compose.override.yml + .env
cd keycloak-cluster
docker compose up -d
docker compose ps
docker compose logs -f keycloak-1

# === 生产环境启动 ===
# 指定生产环境变量和额外编排文件
docker compose -f docker-compose.yml -f docker-compose.prod.yml \
  --env-file .env.prod up -d

# === 查看所有服务状态 ===
docker compose ps

# === 验证节点启动时间 ===
docker compose logs keycloak-1 2>&1 | grep -i "started in"
docker compose logs keycloak-2 2>&1 | grep -i "started in"
docker compose logs keycloak-3 2>&1 | grep -i "started in"

# === 测试健康端点 ===
curl -f http://localhost/realms/master/.well-known/openid-configuration
```

**启动日志示例**（生产环境）：

```
[+] Running 5/5
 ✔ Network keycloak-cluster_kc-network  Created
 ✔ Container kc-postgres                Healthy
 ✔ Container kc-node-1                  Healthy   (started in 18456ms)
 ✔ Container kc-node-2                  Healthy   (started in 19230ms)
 ✔ Container kc-node-3                  Healthy   (started in 19102ms)
 ✔ Container kc-lb                      Started
```

### 步骤6：数据库连接池验证

```bash
# 查看PostgreSQL当前活跃连接数
docker exec kc-postgres psql -U keycloak -d keycloak \
  -c "SELECT count(*) AS active_connections FROM pg_stat_activity WHERE datname='keycloak';"

# 输出示例:
#  active_connections
# ---------------------
#                  38

# 查看Keycloak节点暴露的HikariCP指标
curl -s http://localhost:8080/metrics | grep "vendor_hikaricp"
```

**典型运行结果分析**：

- 空闲时，三个Keycloak节点各维持约5个空闲连接（对应`DB_POOL_MIN_SIZE=5`），总连接约15个
- 高峰期（压测500并发登录），单节点活跃连接攀升至30-40，三节点总连接约100-120，仍在PG的200限制内
- `vendor_hikaricp_connections_active` 指标可用于Grafana告警，阈值为max_size的80%

### 步骤7：滚动重启（零停机）

```bash
#!/bin/bash
# rolling-restart.sh - 逐个重启Keycloak节点，不中断服务
set -e

restart_node() {
    local node=$1
    echo ">>> 重启节点: $node"
    docker compose restart $node
    echo ">>> 等待健康检查通过（最多90秒）..."
    for i in $(seq 1 18); do
        if curl -f -s http://localhost:8080/health/ready > /dev/null 2>&1; then
            # 验证具体节点
            local container_name=$(docker compose ps -q $node)
            if docker inspect --format='{{.State.Health.Status}}' $container_name | grep -q "healthy"; then
                echo ">>> 节点 $node 已恢复健康"
                return 0
            fi
        fi
        sleep 5
    done
    echo "!!! 节点 $node 健康检查超时"
    return 1
}

restart_node "keycloak-1"
sleep 10
restart_node "keycloak-2"
sleep 10
restart_node "keycloak-3"
echo "=== 滚动重启完成 ==="
```

**执行效果**：重启keycloak-1期间，Nginx自动剔除该节点（`max_fails=3 fail_timeout=30s`），流量由keycloak-2和keycloak-3承担；节点恢复健康后Nginx重新加入upstream。

### 常见坑与解决方案

**坑1：`depends_on`不等服务就绪**。默认行为下，PostgreSQL容器启动后Keycloak立即启动，但PG可能还在恢复。解决：所有依赖PostgreSQL的服务必须加`condition: service_healthy`，并为PostgreSQL配置`healthcheck`。

**坑2：日志撑爆磁盘**。`json-file`驱动默认不限制日志大小，生产环境运行几个月后`/var/lib/docker/containers`可能积累数十GB日志。解决：为每个服务配置`logging.options.max-size`和`max-file`。

**坑3：密码中的特殊字符**。`.env`文件中的`#`会被解析为注释，`$`被解析为变量引用，`!`在Shell中有特殊含义。解决：Base64编码存储密码，或使用Docker Secrets（Swarm模式），或确保密码不含`#`和`$`符号。如果必须用特殊字符，使用单引号包裹。

**坑4：`docker compose down -v`清空数据**。`-v`参数会删除所有匿名和命名卷，pgdata中的数据将永久丢失。解决：生产环境禁止使用`-v`参数；定期备份`/var/lib/docker/volumes/`目录；在`docker-compose.prod.yml`中将卷配置为`external: true`。

**坑5：环境变量覆盖顺序混乱**。`.env`、`--env-file`、Shell环境变量、Compose文件中的`environment`、`override.yml`都有各自的优先级。解决：统一使用本章的分层策略——`.env`公共默认值，`--env-file .env.prod`生产覆盖，不在Shell中导出Keycloak环境变量。

### 测试验证清单

| 验证项 | 操作 | 预期结果 |
|--------|------|----------|
| 集群启动 | `docker compose up -d` | 5个容器全部Healthy |
| 单节点故障 | `docker compose stop keycloak-1` | Nginx自动剔除，服务无中断 |
| 滚动重启 | 执行`rolling-restart.sh` | 全程可用，无502错误 |
| 数据库连接池 | 压测500并发登录 | 连接数不超过max_size |
| 日志轮转 | 运行72小时后检查 | 日志文件大小不超过max-size |
| 健康检查 | 访问`/lb-health` | 返回200 OK |
| OpenID端点 | 访问`/.well-known/openid-configuration` | 返回正确配置JSON |

## 4 项目总结

### Docker Compose vs K8s vs 裸机部署对比

| 维度 | Docker Compose | Kubernetes | 裸机部署 |
|------|---------------|------------|----------|
| 部署复杂度 | 低（单一YAML） | 高（多资源对象） | 中（脚本+手工） |
| 自动扩缩容 | 不支持 | 原生HPA | 不支持 |
| 跨主机集群 | 不支持（单机） | 原生支持 | 支持（多机配置复杂） |
| 滚动更新 | 脚本手动 | 原生RollingUpdate | 脚本手动 |
| 服务发现 | Docker DNS（同网络） | CoreDNS/Service | 反向代理手动配置 |
| 配置管理 | `.env` + override | ConfigMap + Secret | 配置文件+环境变量 |
| 健康检查 | Docker healthcheck | liveness/readiness探针 | 自定义脚本 |
| 日志收集 | 驱动配置（有限） | 生态丰富（EFK/Loki） | syslog/文件 |
| 学习成本 | 低（1天入门） | 高（数周至数月） | 中 |
| 资源开销 | 低（仅Docker） | 高（控制面+Etcd） | 最低 |
| 适用团队规模 | 1-5人 | 5人以上 | 不限 |

### 适用场景

- **中小规模生产**：日均10万-50万登录请求，3-5台服务器，团队具备Docker基础但无K8s经验
- **开发和测试环境**：开发人员本地一键启动完整集群，实时调试Keycloak功能
- **POC验证**：快速搭建集群验证Keycloak新特性（如多站点复制、自定义SPI）
- **边缘部署**：客户现场部署，机器资源有限，需要简洁的交付和运维方式

### 不适用场景

- **大型生产环境**：日均百万级请求、需要根据流量自动扩缩容、跨机房/跨区域部署——请直接上Kubernetes或OpenShift
- **多主机集群**：需要Keycloak节点分布在多台物理/云服务器上互为容灾——请使用Docker Swarm（兼容Compose语法）或Kubernetes
- **严格多租户隔离**：需要网络策略、资源配额、RBAC——只有Kubernetes能覆盖这些需求

### 注意事项

1. **生产密码管理**：永远不要在Git仓库中提交明文密码。推荐使用Docker Secrets（Swarm模式）、HashiCorp Vault注入环境变量，或至少将`.env.prod`放在`.gitignore`中并通过CI/CD安全注入。
2. **日志轮转**：本章配置的`max-size`和`max-file`仅管理Docker容器日志。Keycloak应用日志（通过`KC_LOG_CONSOLE_OUTPUT=json`输出的JSON格式）建议通过Fluentd或Filebeat采集到集中式日志平台。
3. **备份策略**：PostgreSQL卷数据需要定期备份。推荐使用`pg_dump`脚本配合cron定时任务，备份文件同步到S3或NAS。备份频率：每日全量 + 每小时WAL归档。
4. **SSL/TLS终止**：本章Nginx配置仅监听80端口。生产环境必须在Nginx层终止TLS，使用Let's Encrypt证书，配置HTTP/2。
5. **Infinispan缓存**：本章使用JDBC-PING作为节点发现机制。生产环境建议使用JDBC-PING（不依赖多播），或切换到TCP-PING并静态列出所有节点IP。

### 从Docker Compose到Kubernetes的迁移路径

**可直接复用的配置（平移）**：
- 环境变量（`KC_*`前缀的Keycloak配置）→ K8s ConfigMap / Secret
- 数据库连接池参数（`KC_DB_POOL_*`）→ 环境变量不变
- 健康检查逻辑 → K8s的`livenessProbe` + `readinessProbe`
- Nginx upstream规则 → K8s Ingress / Service
- 日志输出格式（JSON）→ 日志采集管道无需修改

**需要重构的部分**：
- `depends_on` + `condition: service_healthy` → K8s的`initContainers`验证数据库可用性
- `restart: unless-stopped` → K8s Deployment控制器自动处理，无需显式配置
- Docker卷挂载（主题/缓存配置）→ K8s PVC/PV（或直接构建到自定义镜像中）
- 固定端口映射 → K8s Service（ClusterIP/LoadBalancer/NodePort）
- `docker-compose.override.yml` → K8s Kustomize的overlay分层策略

**共享配置管理策略**：建议使用`.env`文件作为单一抽象层，然后通过不同工具消费同一份配置。例如：`.env` → Kompose转换生成K8s YAML基础版 → Kustomize做差异化。也可以反向：维护K8s ConfigMap为源，生成`.env`文件给Compose用。推荐后者，因为K8s对配置的建模更精细（ConfigMap/Secret/TLS cert分离）。

### 思考题

如果需要将本章的Docker Compose集群迁移到Kubernetes：

1. 哪些配置可以直接复用？环境变量（`KC_*`）和数据库连接池参数（`KC_DB_POOL_*`）可以直接搬进ConfigMap/Secret，健康检查路径（`/health/ready`）可以直接写入探针配置。

2. 哪些需要重构？`depends_on`的逻辑需要用`initContainers`重建（启动PostgreSQL之后执行一个等PG就绪的脚本）；主题和缓存配置从Volume挂载改为自定义镜像（`Dockerfile`中`COPY config/ /opt/keycloak/conf/`）；Nginx负载均衡改为K8s Service + Nginx Ingress Controller。

3. 如何设计Docker Compose和K8s共享的配置管理策略？维护一个`config/`目录存放所有环境变量模板（按`.env.dev`、`.env.staging`、`.env.prod`分层），Docker Compose通过`--env-file`消费，K8s通过`kubectl create configmap --from-env-file`或Kustomize的`configMapGenerator`消费。不敏感配置提交Git，敏感配置通过CI/CD变量注入或SealedSecrets管理。核心原则：一份配置源，多种分发方式，统一来源保证一致性。
