# 第16章：【基础篇综合实战】搭建企业级日志分析平台

> **版本**：ClickHouse 25.x LTS
> **定位**：基础篇收官之战——融会贯通前15章全部知识，从零搭建可落地的企业级日志分析平台。
> **前置阅读**：第4章（MergeTree 家族）、第5章（分区与排序键）、第6章（数据导入导出）、第9章（物化视图）、第10章（TTL与数据生命周期）、第14章（SQL优化入门）
> **预计阅读**：50 分钟 | **实战耗时**：90 分钟

---

## 1. 项目背景

某中型电商公司坐拥 200+ 微服务，日均产生 **50 亿条** Nginx 访问日志。过去两年，这家公司的日志分析一直跑在 ELK Stack（Logstash + Elasticsearch + Kibana）上，每月基础设施成本高达 **3 万美元**，而且稳定性堪忧——每逢大促，Kibana 查询超过 7 天的数据必然超时，错误率告警延迟动辄 10 分钟以上。

运维团队的痛点清单已经排了整整一页：

1. **实时流量监控缺失**：大促期间想做实时 PV/UV/QPS 看板，Elasticsearch 的聚合查询在亿级数据量下直接 OOM，运维总监只能每 10 分钟手动刷一次 "wc -l"——简直回到石器时代。
2. **错误率无法按服务维度下钻**：当用户投诉"支付失败"，排查需要同时拉取 user-service、order-service、payment-service 三套日志，跨索引查询 Kibana 直接卡死，问题定位平均耗时 45 分钟。
3. **慢 API 发现全靠投诉**：没有 Top N 慢接口排行榜，性能退化只有等客户端报 "请求超时" 才知道——而上个月一次支付接口从 50ms 退化到 3s 的故障，竟然是客户端团队先发现的，运维被打了个措手不及。
4. **Trace ID 检索形同虚设**：全链路追踪系统生成的 trace_id 有记录，但在 Elasticsearch 里查一条 trace_id 的全链路日志需要扫描 3 天的全部索引，平均耗时 8 秒，排查一个异常请求的完整调用链需要半小时。
5. **存储成本失控**：Elasticsearch 的倒排索引膨胀率高达 300%，日增 500GB 的日志存 7 天就要 3.5TB 磁盘，加 SSD 加到财务部发飙。

CTO 拍板：**用 ClickHouse 替换 Elasticsearch 做日志存储与分析引擎，Kibana 换 Grafana 做可视化，目标是将月度成本压缩到 \$3000 以下，同时将聚合查询延迟控制在 1 秒以内。**

这就是本章要交付的系统——从日志采集到看板呈现，全链路贯通。

---

## 2. 项目设计：剧本式交锋对话

周五下午，大师把小白和小胖叫到会议室，白板上只写了一行字：**"Nginx Log → ClickHouse → Grafana"**。

**小胖**（拎着一袋薯片走进来，看了一眼白板就皱眉）："大师，ELK 不是挺好的吗？Logstash 收日志、Elasticsearch 存数据、Kibana 出图表，一条龙全家桶。换 ClickHouse 干嘛？再说那玩意儿不是做数据分析的吗，存日志能行？"

**大师**（把薯片从小胖手里拿过来放到桌角）："小胖，我问你——Elasticsearch 存 50 亿条日志，一条日志假设 500 字节，存 7 天要多少磁盘？"

**小胖**（掏出手机飞快按了按）："50 亿 × 500 字节 = 2500 亿字节……大概是 250GB？"

**大师**："那是原始大小。Elasticsearch 的倒排索引会为每个字段建立索引，膨胀系数通常在 1.5 到 3 倍之间，加上副本，实际磁盘占用轻松超过 1.5TB——而且这是在你不开 `_source` 压缩优化的情况下。你再看 ClickHouse：列式存储天然适合日志的表格化数据，LZ4 压缩比通常在 5-10 倍，`LowCardinality` 对 service、status、method 这种高重复字段还能进一步压缩。同样 250GB 的原始日志，ClickHouse 存下来大概只要 30-50GB。"

**小胖**（瞪大眼睛）："差了十倍？！"

**大师**："这还只是存储。更大的差距在查询上——Elasticsearch 做 `GROUP BY service` 聚合，需要从倒排索引里把所有文档的 service 字段值读出来再计数；ClickHouse 呢？列式存储下，service 列的数据在磁盘上是连续存放的，读出来直接走向量化聚合，IO 量少了一个数量级。我给你看一组实测数据：50 亿行日志里查 '过去 24 小时各服务的错误率'，Elasticsearch 耗时 45 秒，ClickHouse 耗时 0.8 秒。"

**技术映射 #1**：列式存储 + 压缩 + 向量化计算是 ClickHouse 在日志分析场景碾压 Elasticsearch 的三大核心优势。对于 OLAP 聚合查询，列存减少 IO、压缩减少磁盘占用、SIMD 加速计算——三管齐下，性能差一个数量级。

---

**小白**（在笔记本上飞快做着笔记，头也不抬）："大师，ELK 最大的优势是 Elasticsearch 的倒排索引能做任意文本搜索——比如查 `message` 字段里包含 'OutOfMemoryError' 的日志。ClickHouse 没有倒排索引，这种模糊匹配怎么办？还有，Nginx 日志有些是纯文本格式、有些是 JSON 格式，表结构怎么设计才通用？"

**大师**："好问题，一个个来。**第一个：全文搜索**。ClickHouse 确实没有倒排索引，但我们可以用 `ngrambf_v1` 或 `tokenbf_v1` 跳数索引来加速 LIKE 或 `hasToken()` 查询——第 21 章会详细讲。不过你仔细想想，**日志分析场景里真正的全文搜索需求很少**。大多数查询是结构化的：'某个服务在某个时间段的状态码分布'、'某个 URL 的 P99 延迟'、'trace_id = xxx 的所有日志'。这些全部是主键定位或聚合查询——恰恰是 ClickHouse 最擅长的。你唯一需要 LIKE 查询的可能是错误日志的 message 字段，这时候用一个 bloom_filter 跳数索引就够了，配合 `positionCaseInsensitive` 函数，比 ES 的全文检索慢一点，但绝对够用。"

**大师**（站起来在白板上画了一张表结构）："**第二个：表结构设计**。Nginx 日志不管是纯文本还是 JSON，核心字段无非这些：timestamp、service、remote_addr、method、path、status、body_bytes_sent、request_time、http_user_agent、trace_id。我们把它们拆成列存进来。注意三个关键设计：

1. **PARTITION BY toYYYYMM(timestamp)**——按月分区。日志查询的 WHERE 条件 99% 带时间范围，按月分区让分区裁剪精准命中。
2. **ORDER BY (service, timestamp, path)**——排序键就是稀疏索引，把高频过滤的 service 放在最前面，查询 `WHERE service = 'payment-service' AND timestamp > now() - INTERVAL 1 HOUR` 时只扫描命中的 Granule。
3. **LowCardinality**——service、method、status 这种枚举值字段，用 LowCardinality 修饰符，存储可以压缩到几十 KB，查询时字典解码几乎零开销。"

**技术映射 #2**：日志表 = 按时间分区 + 按过滤字段排序 + 高基数字符串 LowCardinality 化。分区解决数据裁剪，排序键解决索引命中，LowCardinality 解决存储膨胀——三者合一，就是 ClickHouse 日志建模的"三件套"。

---

**小白**（若有所思）："那数据怎么进来呢？ELK 用 Logstash 做 ETL 管道很成熟，ClickHouse 这边……直接 FileBeat 写 HTTP 接口？"

**大师**："对，核心链路就三环：**FileBeat 采集 → ClickHouse HTTP 接口（FORMAT JSONEachRow）→ MergeTree 存储**。不需要中间 Logstash 做清洗——ClickHouse 的物化视图就是天然的计算层。原始日志直接入 raw 表，然后物化视图异步触发，按分钟/小时维度预聚合，生成聚合表。这样做的好处是：**原始日志保留完整信息（可以查 trace_id），聚合表支撑看板查询（KV 秒级响应）**——读写分离在存储层就做好了。"

**小胖**（挠头）："物化视图会不会拖慢写入速度？50 亿条日志一天，写入吞吐量要多大？"

**大师**："物化视图会增加大约 5%-10% 的 INSERT 延迟——因为它本质上是 INSERT 的同步触发器。不过我们有 `async_insert=1` 做批量缓冲，原始表写一批触发一次物化视图，而不是每条触发一次。写入吞吐方面，单节点 ClickHouse 轻松跑到每秒 30 万-50 万行——50 亿条一天，平摊到每秒大概 5.8 万行，单节点完全扛得住。数据量大起来的话，加分布式表搞分片就行。"

**小白**（翻了一页笔记）："数据还有生命周期管理——7 天内的日志查得频繁，30 天内的偶尔查，90 天以上的可以丢了。TTL 怎么设？"

**大师**（赞许地点头）："TTL 策略分三档——这是第 10 章的核心内容。表级 TTL 设置 `timestamp + INTERVAL 90 DAY DELETE`，90 天以上的日志自动清理；如果预算宽裕，还可以做冷热分层，7 天内放 SSD，7-30 天放 HDD。MergeTree 的后台 Merge 进程会在合并 Part 时触发 TTL 删除——不是即时生效，但 90 天的数据晚删几个小时也无所谓。"

**技术映射 #3**：TTL = 存储成本的自动调控器。日志场景天然带时间衰减热度，配上分区裁剪，90 天前的分区直接整个删除，零碎 IO 都没有——这就是分区键和 TTL 的协同价值。

---

**小胖**（突然来了精神）："大师，你说了这么多——架构到底长什么样？画个图看看！"

**大师**（转身在白板上画了起来）：

```
┌──────────┐    ┌──────────────┐    ┌──────────────┐
│  Nginx   │───▶│  Python Log  │───▶│  ClickHouse  │
│ 日志文件  │    │  Generator   │    │  nginx_logs  │
└──────────┘    │  (模拟采集)   │    │   _raw       │
                └──────────────┘    └──────┬───────┘
                                           │
                              ┌────────────┼────────────┐
                              │            │            │
                              ▼            ▼            ▼
                         ┌────────┐ ┌────────┐  ┌──────────┐
                         │MV      │ │MV      │  │Raw Query │
                         │minute  │ │hour    │  │(trace_id)│
                         └────┬───┘ └────┬───┘  └────┬─────┘
                              │          │            │
                              ▼          ▼            ▼
                         ┌──────────────────────────────┐
                         │       Grafana Dashboard       │
                         │  PV/UV · 错误率 · P99 · QPS  │
                         └──────────────────────────────┘
```

**大师**把马克笔搁下："这套架构的核心思想就四个字：**读写分离**。写入链路只管快速地往 raw 表里灌数据；物化视图在后台把数据预聚合好；查询链路直接读聚合表——一次 INSERT 的成本换来上千次 SELECT 的加速。这就是 ClickHouse 做日志分析的本质优势：**用空间（物化视图）换时间（查询延迟）**，而且因为压缩率高，空间成本极低。"

---

## 3. 项目实战

### 环境准备

本章实战需要一个完整的 Docker Compose 环境，包含 ClickHouse、Grafana 和一个 Python 日志生成器。确保你的机器至少有 4GB 可用内存和 10GB 可用磁盘。

**项目目录结构**：

```
log-platform/
├── docker-compose.yml
├── clickhouse/
│   └── config/
│       └── custom.xml
├── grafana/
│   └── dashboards/
│       └── nginx-logs.json
└── log_generator/
    ├── Dockerfile
    ├── generate.py
    └── requirements.txt
```

**第一步：创建 Docker Compose 编排文件**。

```yaml
# docker-compose.yml
version: '3.8'

services:
  clickhouse:
    image: clickhouse/clickhouse-server:25.3
    container_name: ch-log-platform
    ports:
      - "8123:8123"   # HTTP 接口
      - "9000:9000"   # Native TCP 接口
    volumes:
      - ./clickhouse/config:/etc/clickhouse-server/config.d
      - ./clickhouse/data:/var/lib/clickhouse
      - ./clickhouse/logs:/var/log/clickhouse-server
    ulimits:
      nofile:
        soft: 262144
        hard: 262144  # 文件句柄上限，大量 Part 时需要
    healthcheck:
      test: ["CMD", "clickhouse-client", "--query", "SELECT 1"]
      interval: 10s
      timeout: 5s
      retries: 5

  grafana:
    image: grafana/grafana:11.0
    container_name: grafana-log-platform
    ports:
      - "3000:3000"
    environment:
      - GF_INSTALL_PLUGINS=vertamedia-clickhouse-datasource
      - GF_AUTH_ANONYMOUS_ENABLED=true
    volumes:
      - ./grafana/dashboards:/etc/grafana/provisioning/dashboards
      - ./grafana/datasources:/etc/grafana/provisioning/datasources

  log_generator:
    build: ./log_generator
    container_name: log-generator
    depends_on:
      clickhouse:
        condition: service_healthy
    environment:
      - CLICKHOUSE_URL=http://clickhouse:8123
```

**第二步：创建 ClickHouse 自定义配置**。

```xml
<!-- clickhouse/config/custom.xml -->
<clickhouse>
    <async_insert>1</async_insert>
    <wait_for_async_insert>0</wait_for_async_insert>
    <max_partitions_per_insert_block>0</max_partitions_per_insert_block>
</clickhouse>
```

说明：`async_insert=1` 开启异步批量写入，将客户端的小批量 INSERT 在服务端合并成大 Batch 后再写入——这对日志生成器的高频小批次写入场景至关重要，可将 INSERT 延迟从毫秒级压缩到微秒级。

**第三步：创建 Python 日志生成器**。

```python
# log_generator/generate.py
"""
Nginx 访问日志模拟生成器
生成 JSONEachRow 格式日志，批量写入 ClickHouse HTTP 接口
"""
import json
import random
import time
import uuid
from datetime import datetime, timedelta

import requests

# ── 模拟的微服务列表 ──
SERVICES = [
    'user-service', 'order-service', 'payment-service',
    'product-service', 'inventory-service', 'notification-service',
    'gateway-service', 'search-service', 'cart-service', 'coupon-service'
]

# ── 每个服务的 API 路径 ──
PATHS = {
    'user-service':      ['/api/v1/users', '/api/v1/users/login', '/api/v1/users/profile'],
    'order-service':     ['/api/v1/orders', '/api/v1/orders/create', '/api/v1/orders/{id}'],
    'payment-service':   ['/api/v1/pay', '/api/v1/pay/callback', '/api/v1/pay/refund'],
    'product-service':   ['/api/v1/products', '/api/v1/products/search', '/api/v1/products/{id}'],
    'inventory-service': ['/api/v1/stock/query', '/api/v1/stock/deduct', '/api/v1/stock/restore'],
    'notification-service': ['/api/v1/notify/send', '/api/v1/notify/status'],
    'gateway-service':   ['/api/v1/routes', '/api/v1/health'],
    'search-service':    ['/api/v1/search', '/api/v1/suggest'],
    'cart-service':      ['/api/v1/cart', '/api/v1/cart/add', '/api/v1/cart/remove'],
    'coupon-service':    ['/api/v1/coupons', '/api/v1/coupons/validate', '/api/v1/coupons/apply'],
}

METHODS = ['GET', 'POST', 'PUT', 'DELETE']

# ── HTTP 状态码权重：模拟真实分布（95% 成功 + 5% 各类错误）──
STATUS_WEIGHTS = [
    (200, 0.80), (201, 0.05), (204, 0.03),
    (301, 0.02), (302, 0.01),
    (400, 0.03), (401, 0.01), (403, 0.005), (404, 0.02),
    (500, 0.01), (502, 0.005), (503, 0.005), (504, 0.005),
]

STATUS_LIST = [s[0] for s in STATUS_WEIGHTS]
STATUS_PROBS = [s[1] for s in STATUS_WEIGHTS]

CH_URL = "http://clickhouse:8123"


def generate_log():
    """生成一条模拟的 Nginx 访问日志"""
    service = random.choice(SERVICES)

    # 时间戳：均匀分布在过去 7 天
    ts = datetime.now() - timedelta(seconds=random.randint(0, 86400 * 7))

    # 状态码
    status = random.choices(STATUS_LIST, weights=STATUS_PROBS, k=1)[0]

    # 响应时间：指数分布，均值 50ms
    request_time = round(random.expovariate(1 / 0.05), 4)

    # 错误请求的响应时间偏高（10-100 倍）
    if status >= 500:
        request_time *= random.uniform(10, 100)
    elif status >= 400:
        request_time *= random.uniform(2, 10)

    # 上游响应时间略短于总请求时间
    upstream_time = round(request_time * random.uniform(0.75, 0.95), 4)

    # IP 地址
    ip = f"{random.randint(1, 223)}.{random.randint(0, 255)}.{random.randint(0, 255)}.{random.randint(1, 254)}"

    # User-Agent 池
    user_agents = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148",
        "Mozilla/5.0 (Linux; Android 13; SM-S9080) AppleWebKit/537.36 Chrome/120.0.0.0 Mobile Safari/537.36",
        "PostmanRuntime/7.36.0",
        "python-requests/2.31.0",
        "Apache-HttpClient/4.5.14 (Java/17.0.9)",
        "Go-http-client/2.0",
    ]

    return {
        "timestamp":          ts.strftime('%Y-%m-%d %H:%M:%S'),
        "service":            service,
        "host":               f"{service}.example.com",
        "remote_addr":        ip,
        "method":             random.choice(METHODS),
        "path":               random.choice(PATHS[service]),
        "query_string":       f"page={random.randint(1, 100)}&size={random.randint(10, 50)}",
        "status":             status,
        "body_bytes_sent":    random.randint(100, 80000),
        "request_time":       request_time,
        "upstream_response_time": upstream_time,
        "http_referer":       "https://www.myshop.com/",
        "http_user_agent":    random.choice(user_agents),
        "trace_id":           f"{random.randint(10000000, 99999999)}-{uuid.uuid4().hex[:8]}",
    }


def ensure_table_exists():
    """确保 ClickHouse 中日志表已创建"""
    ddl = """
    CREATE TABLE IF NOT EXISTS log_analytics.nginx_logs_raw (
        timestamp DateTime,
        service LowCardinality(String),
        host String,
        remote_addr IPv4,
        method LowCardinality(String),
        path String,
        query_string String,
        status UInt16,
        body_bytes_sent UInt64,
        request_time Float32,
        upstream_response_time Float32,
        http_referer String,
        http_user_agent String,
        trace_id String
    ) ENGINE = MergeTree()
    ORDER BY (service, timestamp, path)
    PARTITION BY toYYYYMM(timestamp)
    TTL timestamp + INTERVAL 90 DAY DELETE
    SETTINGS index_granularity = 8192
    """
    # 先建库
    requests.post(CH_URL, params={"query": "CREATE DATABASE IF NOT EXISTS log_analytics"})
    resp = requests.post(CH_URL, params={"query": ddl})
    return resp.status_code == 200


def send_batch(batch):
    """批量写入 ClickHouse"""
    payload = "\n".join(json.dumps(log, ensure_ascii=False) for log in batch)
    try:
        resp = requests.post(
            CH_URL,
            params={"query": "INSERT INTO log_analytics.nginx_logs_raw FORMAT JSONEachRow"},
            data=payload.encode("utf-8"),
            timeout=10,
        )
        return resp.status_code == 200
    except requests.exceptions.RequestException:
        return False


def main():
    """主循环：持续生成并写入日志"""
    print("Waiting for ClickHouse to be ready...")
    time.sleep(5)

    if not ensure_table_exists():
        print("Failed to create table!")
        return

    print("Table ready. Starting log generation...")

    total_sent = 0
    batch_size = 500

    while True:
        batch = [generate_log() for _ in range(batch_size)]
        if send_batch(batch):
            total_sent += batch_size
            if total_sent % 50000 == 0:
                print(f"[{datetime.now().strftime('%H:%M:%S')}] Sent {total_sent:,} logs, "
                      f"sleeping to simulate real traffic...")
        else:
            print(f"[ERROR] Failed to send batch at {total_sent}")

        # 控制写入速率：每秒约 8000 条（模拟 10 个服务的 Nginx 日志量）
        time.sleep(0.06)


if __name__ == "__main__":
    main()
```

**第四步：创建 Dockerfile**。

```dockerfile
# log_generator/Dockerfile
FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY generate.py .

CMD ["python", "generate.py"]
```

```txt
# log_generator/requirements.txt
requests>=2.31.0
```

**第五步：创建 Grafana 数据源配置**。

```yaml
# grafana/datasources/clickhouse.yml
apiVersion: 1

datasources:
  - name: ClickHouse-Log
    type: vertamedia-clickhouse-datasource
    access: proxy
    url: http://clickhouse:8123
    isDefault: true
    editable: true
    jsonData:
      defaultDatabase: log_analytics
      usePost: true
```

### 分步实现

#### Step 1：建表——日志表结构设计

启动环境后，首先在 ClickHouse 中创建核心表结构。

```shell
# 启动全部服务
docker compose up -d

# 等待 ClickHouse 就绪
sleep 10

# 进入 ClickHouse 客户端
docker exec -it ch-log-platform clickhouse-client
```

```sql
-- 创建数据库
CREATE DATABASE IF NOT EXISTS log_analytics;

-- Step 1.1: 原始日志表（Raw 层）
-- 这是所有日志的真相来源，保留原始粒度
CREATE TABLE log_analytics.nginx_logs_raw (
    timestamp             DateTime,
    service               LowCardinality(String),   -- 服务名，枚举值 < 20
    host                  String,
    remote_addr           IPv4,                     -- IP 类型自带 4 字节紧凑存储
    method                LowCardinality(String),   -- GET/POST/PUT/DELETE
    path                  String,
    query_string          String,
    status                UInt16,                   -- HTTP 状态码
    body_bytes_sent       UInt64,
    request_time          Float32,                  -- 请求总耗时（秒）
    upstream_response_time Float32,                 -- 上游响应耗时
    http_referer          String,
    http_user_agent       String,
    trace_id              String                    -- 全链路追踪 ID
) ENGINE = MergeTree()
ORDER BY (service, timestamp, path)                 -- 排序键 = 稀疏索引
PARTITION BY toYYYYMM(timestamp)                     -- 按月分区，方便裁剪和清理
TTL timestamp + INTERVAL 90 DAY DELETE               -- 90 天自动删除
SETTINGS index_granularity = 8192;                    -- 每个 Granule 8192 行

-- 验证建表
DESCRIBE TABLE log_analytics.nginx_logs_raw;
```

**设计要点解读**：

| 设计决策 | 原因  | 知识来源 |
| --- | --- | --- |
| `ORDER BY (service, timestamp, path)` | service 是高频过滤字段，放首位使查询只扫描相关 Granule | 第5章 分区与排序键 |
| `PARTITION BY toYYYYMM(timestamp)` | 按月分区，查询 99% 带时间范围，分区裁剪精准命中 | 第5章 分区裁剪 |
| `LowCardinality(String)` | service/method/status 基数极低，字典编码压缩到几十 KB | 第3章 数据类型 |
| `IPv4` 类型 | 用 4 字节紧凑存储 IP，而非 15 字节字符串 | 第3章 数据类型 |
| `TTL 90 DAY DELETE` | 控制存储增长，老分区自动清理 | 第10章 TTL |
| `index_granularity = 8192` | 日志表行宽中等（~500B/行），8192 是默认值，无需调整 | 第7章 稀疏索引 |

---

#### Step 2：创建物化视图——分钟级与小时级预聚合

物化视图是日志分析平台的性能核心。我们设计两级级联聚合：**分钟级（详情）→ 小时级（汇总）**。

```sql
-- Step 2.1: 分钟级物化视图（一级聚合）
-- 用途：支撑 Grafana 实时看板、QPS 曲线、延迟分位数
CREATE MATERIALIZED VIEW log_analytics.mv_nginx_minute
ENGINE = SummingMergeTree()                          -- SummingMergeTree 合并相同排序键的行
ORDER BY (minute, service, status)                   -- 排序键决定了 Merge 去重范围
PARTITION BY toYYYYMM(minute)
AS SELECT
    toStartOfMinute(timestamp)        AS minute,
    service,
    status,
    count()                           AS request_count,
    sum(body_bytes_sent)              AS total_bytes,
    avg(request_time)                 AS avg_request_time,
    quantile(0.50)(request_time)      AS p50,
    quantile(0.95)(request_time)      AS p95,
    quantile(0.99)(request_time)      AS p99,
    countIf(request_time > 1.0)       AS slow_count,         -- 慢请求（>1秒）
    countIf(status >= 500)            AS error_5xx,
    countIf(status >= 400 AND status < 500) AS error_4xx
FROM log_analytics.nginx_logs_raw
GROUP BY minute, service, status;
```

**为什么用 SummingMergeTree？** 分钟级聚合里，同一分钟同一服务同一状态码的数据可能来自多次 INSERT（每次触发物化视图都会产生新行）。SummingMergeTree 在后台 Merge 时自动合并相同排序键的行，将 `request_count`、`total_bytes` 等数值列累加——查询时用 `SELECT ... FINAL` 或依赖 `sum()` 函数保证正确性。

```sql
-- Step 2.2: 小时级物化视图（二级聚合，级联自分钟表）
-- 用途：支撑 T+1 日报、周报、趋势分析
CREATE MATERIALIZED VIEW log_analytics.mv_nginx_hour
ENGINE = SummingMergeTree()
ORDER BY (hour, service)
PARTITION BY toYYYYMM(hour)
AS SELECT
    toStartOfHour(minute)             AS hour,
    service,
    sum(request_count)                AS request_count,
    sum(total_bytes)                  AS total_bytes,
    sum(slow_count)                   AS slow_count,
    sum(error_5xx)                    AS error_5xx,
    sum(error_4xx)                    AS error_4xx,
    -- 注意：分位数不能简单地 avg，这里需要用 quantileState 合并
    -- 为了简洁，小时级别只保留计数指标，分位数查询走分钟表
FROM log_analytics.mv_nginx_minute
GROUP BY hour, service;
```

> **踩坑提示**：分位数（p50/p95/p99）不是线性可加的！`avg(p50)` 不等于真正的 p50。小时级别的分位数需要回到原始表或用 `quantileState`/`quantileMerge` 组合函数。本实战中，小时表只存计数指标，分位数查询直接走分钟表——分钟表的数据量级（每天 1440 × 10 服务 × 10 状态码 ≈ 14 万行），查询 P99 耗时仍 < 50ms。

```sql
-- 验证物化视图是否正常工作
SELECT count() AS row_count FROM log_analytics.mv_nginx_minute;
-- 如果有日志生成器在跑，应该能看到数据在增长
```

---

#### Step 3：核心查询 SQL 集合

这些 SQL 就是 Grafana 看板的底层查询。建议在 `clickhouse-client` 中逐条验证。

```sql
-- 【查询 1】实时 QPS（最近 1 分钟，按服务拆分）
-- 用途：Grafana 时序图——流量总览
SELECT
    service,
    sum(request_count) / 60                AS qps,
    sum(error_5xx)                         AS errors
FROM log_analytics.mv_nginx_minute
WHERE minute >= now() - INTERVAL 1 MINUTE
GROUP BY service
ORDER BY qps DESC;

-- 【查询 2】Top 10 慢 API（最近 1 小时，按耗时排序）
-- 用途：性能优化——定位长尾慢接口
-- 注意：这里直接从 raw 表查询，因为需要 path 维度的明细
SELECT
    service,
    path,
    count()                                AS hits,
    round(avg(request_time) * 1000, 2)     AS avg_ms,
    round(quantile(0.99)(request_time) * 1000, 2) AS p99_ms,
    round(max(request_time) * 1000, 2)     AS max_ms
FROM log_analytics.nginx_logs_raw
WHERE timestamp >= now() - INTERVAL 1 HOUR
GROUP BY service, path
HAVING hits > 50                           -- 过滤掉偶发请求
ORDER BY avg_ms DESC
LIMIT 10;

-- 【查询 3】错误率按服务排名（最近 24 小时）
-- 用途：告警——哪个服务的错误率异常
SELECT
    service,
    count()                                AS total_requests,
    countIf(status >= 500)                 AS server_errors,
    countIf(status >= 400 AND status < 500) AS client_errors,
    round(server_errors * 100.0 / total_requests, 2) AS error_rate_5xx,
    round(client_errors * 100.0 / total_requests, 2) AS error_rate_4xx
FROM log_analytics.nginx_logs_raw
WHERE timestamp >= now() - INTERVAL 24 HOUR
GROUP BY service
HAVING error_rate_5xx > 0.5               -- 5xx 错误率超过 0.5% 才展示
ORDER BY error_rate_5xx DESC;

-- 【查询 4】按 trace_id 精准搜索全链路日志
-- 用途：故障排查——输入 trace_id，查出所有关联日志
SELECT
    timestamp,
    service,
    method,
    path,
    status,
    round(request_time * 1000, 2)         AS request_time_ms,
    trace_id
FROM log_analytics.nginx_logs_raw
WHERE trace_id = '12345678-abcdef01'       -- 替换为实际 trace_id
ORDER BY timestamp ASC;

-- 【查询 5】小时级 UV/PV 趋势（最近 7 天）
-- 用途：运营报表——用户活跃度变化
SELECT
    toStartOfHour(timestamp)               AS hour,
    uniq(remote_addr)                      AS uv,
    count()                                AS pv,
    round(pv / greatest(uv, 1), 2)         AS pv_per_uv
FROM log_analytics.nginx_logs_raw
WHERE timestamp >= now() - INTERVAL 7 DAY
GROUP BY hour
ORDER BY hour;

-- 【查询 6】状态码分布（饼图数据）
-- 用途：快速了解服务健康状况
SELECT
    CASE
        WHEN status >= 500 THEN '5xx Server Error'
        WHEN status >= 400 THEN '4xx Client Error'
        WHEN status >= 300 THEN '3xx Redirect'
        WHEN status >= 200 THEN '2xx Success'
        ELSE 'Other'
    END                                    AS status_category,
    sum(request_count)                     AS request_count
FROM log_analytics.mv_nginx_minute
WHERE minute >= now() - INTERVAL 1 HOUR
GROUP BY status_category
ORDER BY request_count DESC;
```

---

#### Step 4：Grafana 看板配置

在浏览器中访问 `http://localhost:3000`，使用默认账号 `admin/admin` 登录。进入 **Dashboards → New → Add visualization**，选择 `ClickHouse-Log` 数据源，粘贴以下 SQL 配置四个核心面板。

**Panel 1：实时流量曲线（Time Series）**

```sql
-- 各服务每分钟的请求量趋势
SELECT
    $__timeInterval(minute)                AS time,
    service,
    sum(request_count)                      AS requests
FROM log_analytics.mv_nginx_minute
WHERE minute >= $__timeFrom()
  AND minute < $__timeTo()
GROUP BY time, service
ORDER BY time ASC
```

Grafana 配置：可视化类型选 **Time series**，Legend 设为 `{{service}}`，Panel title 为 "服务流量总览"。

**Panel 2：错误率分布（Pie Chart）**

```sql
-- 最近 1 小时状态码分布
SELECT
    CASE
        WHEN status >= 500 THEN '5xx Server Error'
        WHEN status >= 400 THEN '4xx Client Error'
        WHEN status >= 300 THEN '3xx Redirect'
        WHEN status >= 200 THEN '2xx Success'
        ELSE 'Other'
    END                                    AS category,
    sum(request_count)                      AS count
FROM log_analytics.mv_nginx_minute
WHERE minute >= now() - INTERVAL 1 HOUR
GROUP BY category
```

可视化类型选 **Pie chart**，Panel title 为 "状态码分布"。

**Panel 3：P50/P95/P99 延迟趋势（Time Series）**

```sql
SELECT
    $__timeInterval(minute)                AS time,
    avg(p50) * 1000                        AS p50_ms,
    avg(p95) * 1000                        AS p95_ms,
    avg(p99) * 1000                        AS p99_ms
FROM log_analytics.mv_nginx_minute
WHERE minute >= $__timeFrom()
  AND minute < $__timeTo()
GROUP BY time
ORDER BY time ASC
```

可视化类型选 **Time series**，Panel title 为 "请求延迟分位数"。

**Panel 4：Top 10 慢接口（Table）**

```sql
SELECT
    service,
    path,
    count()                                AS hits,
    round(avg(request_time) * 1000, 2)     AS avg_ms,
    round(quantile(0.99)(request_time) * 1000, 2) AS p99_ms
FROM log_analytics.nginx_logs_raw
WHERE timestamp >= now() - INTERVAL 1 HOUR
GROUP BY service, path
HAVING hits > 30
ORDER BY avg_ms DESC
LIMIT 10
```

可视化类型选 **Table**，Panel title 为 "慢请求 TOP 10"。

> **Grafana 宏变量说明**：`$__timeFrom()` 和 `$__timeTo()` 是 Grafana 自动注入的时间范围参数；`$__timeInterval(column)` 根据面板的时间粒度自适应调整聚合窗口。例如看板选择"最近 12 小时"，`$__timeInterval(minute)` 可能变为 `toStartOfFiveMinute(minute)`，避免点太多卡死浏览器。

---

#### Step 5：存储估算与成本对比

```sql
-- 查看各表的实际存储占用
SELECT
    table,
    formatReadableSize(sum(bytes_on_disk))    AS compressed_size,
    sum(rows)                                 AS total_rows,
    formatReadableSize(sum(data_uncompressed_bytes)) AS uncompressed_size,
    round(sum(data_uncompressed_bytes) /
          greatest(sum(bytes_on_disk), 1), 2) AS compression_ratio,
    count()                                   AS part_count
FROM system.parts
WHERE database = 'log_analytics'
  AND active
GROUP BY table
ORDER BY sum(bytes_on_disk) DESC;
```

预期输出示例（运行 30 分钟后）：

| table | compressed_size | total_rows | uncompressed_size | compression_ratio |
| --- | --- | --- | --- | --- |
| nginx_logs_raw | 245.30 MiB | 3,200,000 | 1.52 GiB | 6.34 |
| mv_nginx_minute | 1.25 MiB | 18,400 | 8.90 MiB | 7.12 |
| mv_nginx_hour | 0.08 MiB | 560 | 0.45 MiB | 5.63 |

压缩比 6-7 倍符合预期。按日均 50 亿条估算：

| 指标  | Elasticsearch | ClickHouse |
| --- | --- | --- |
| 日增原始数据 | 500 GB | 500 GB |
| 磁盘占用（含索引） | ~1.5 TB（倒排索引 3x 膨胀） | ~80 GB（列存 + LZ4 压缩 6x） |
| 7 天存储总成本 | ~10.5 TB × $0.08/GB/月 = $840 | ~560 GB × $0.08/GB/月 = $45 |
| 月总成本（计算 + 存储） | ~$30,000 | ~$3,000 |

10 倍的存储效率带来 10 倍的成本优势——这就是列存压倒倒排索引的经典案例。

---

### 测试验证

运行以下验证步骤，确保整个链路正常工作：

```shell
# 1. 确认日志生成器正常写入
docker logs log-generator --tail 20
# 应看到: [14:32:05] Sent 50,000 logs...

# 2. 确认 raw 表有数据
docker exec ch-log-platform clickhouse-client --query "
SELECT count() AS total, min(timestamp) AS earliest, max(timestamp) AS latest
FROM log_analytics.nginx_logs_raw
"

# 3. 确认物化视图在聚合
docker exec ch-log-platform clickhouse-client --query "
SELECT count() FROM log_analytics.mv_nginx_minute
"
# 应该 > 0

# 4. 运行核心查询，验证返回结果
docker exec ch-log-platform clickhouse-client --query "
SELECT service, sum(request_count) / 60 AS qps
FROM log_analytics.mv_nginx_minute
WHERE minute >= now() - INTERVAL 5 MINUTE
GROUP BY service
ORDER BY qps DESC
"

# 5. 验证 trace_id 查询
docker exec ch-log-platform clickhouse-client --query "
SELECT timestamp, service, path, status, request_time
FROM log_analytics.nginx_logs_raw
ORDER BY timestamp DESC
LIMIT 5
"
# 复制一条的 trace_id，再精确搜索

# 6. 访问 Grafana
echo "打开浏览器访问 http://localhost:3000"
# 登录 admin/admin，确认看板数据正常渲染
```

---

## 4. 项目总结

### 架构对比一览

| 维度  | ELK Stack | ClickHouse Stack | 差距  |
| --- | --- | --- | --- |
| 存储引擎 | 倒排索引（Lucene） | 列式存储（MergeTree） | **存储空间 1/10** |
| 写入吞吐 | ~5 万 docs/s/节点 | ~30 万 rows/s/节点 | **6x** |
| 聚合查询（10 亿行） | 45s+（ES Aggregation） | <1s（物化视图 + 向量化） | **50x** |
| 全文搜索 | 原生支持，极快 | 需跳数索引辅助 | ELK 胜出 |
| 压缩比 | 1.5-3x（含索引） | 5-10x（列存 LZ4） | **3-5x** |
| 可视化工 conn | Kibana | Grafana | 各有千秋 |
| 运维复杂度 | 中等（GC 调优、Shard 规划） | 低（无 JVM，单进程） | ClickHouse 更简单 |
| 7 天存储成本（50 亿/天） | ~$2,500 | ~$250 | **10x** |

### 适用场景

✅ **最适合 ClickHouse 日志分析的场景**：

1. **微服务 API 网关监控**——按服务维度聚合 PV/UV/QPS/错误率，完美匹配列存聚合优势。
2. **CDN 访问日志分析**——下行流量统计、热点资源排行、地理分布分析。
3. **安全审计日志**——基于 IP/用户/操作的聚合分析，异常行为检测。
4. **广告投放日志**——曝光/点击/转化漏斗，实时 ROI 计算。
5. **IoT 设备遥测数据**——海量时序数据存储与降采样聚合。

❌ **不太适合的场景**：

1. **纯文本日志全文搜索**（如 grep 错误堆栈）——ES 的倒排索引仍然是最优解。
2. **需要实时更新的小批量数据**（如配置表）——MergeTree 的异步 Merge 不适合高频 UPDATE。

### 注意事项

1. **LowCardinality 不是银弹**：只适用于基数 < 10 万的字段。如果某个字段有数百万个不同值，LowCardinality 反而会增加内存开销——字典本身太大。
2. **物化视图会轻微拖慢写入**：每多一个物化视图，INSERT 延迟增加约 5-10%。建议单张原始表挂载的物化视图 ≤ 5 个——如果需要更多维度聚合，通过级联实现而非并行。
3. **分区粒度不是越细越好**：按小时分区会导致 Part 数量爆炸（每月 720 个分区 × N 个 Columns），Merge 调度器不堪重负。日志场景建议按月或按日分区。
4. **Grafana 时间宏必须匹配聚合粒度**：如果物化视图是按分钟聚合，`$__timeInterval` 不应设为按秒——会导致数据点重复计数。在 Grafana Query 中设置 `Min time interval = 1m` 避免此问题。
5. **trace_id 查询走全表扫描**：如果没有为 trace_id 建跳数索引，在 50 亿行中搜一个 trace_id 会扫全部分区。生产环境建议为 trace_id 添加 bloom_filter 跳数索引或创建物化视图做 trace 索引加速。如果需要使用 bloom_filter:
  
  ```sql
  ALTER TABLE log_analytics.nginx_logs_raw
  ADD INDEX idx_trace_id trace_id TYPE bloom_filter(0.01) GRANULARITY 4;
  ```
  

### 常见踩坑经验

**坑 1：JSONEachRow 格式的转义问题**

日志中如果包含换行符、双引号或反斜杠，直接拼 JSON 会导致 ClickHouse 解析失败。解决方案是使用 `json.dumps()`（Python）的默认转义行为，它能正确处理所有特殊字符。千万别自己拼接 JSON 字符串——那是生产事故的温床。

**坑 2：物化视图过多导致 INSERT 积压**

某团队曾为一张 raw 表挂了 8 个物化视图（按分钟/小时/天 × 按服务/状态/URL 排列组合），结果 INSERT 延迟从 5ms 飙升至 200ms，写入队列严重积压。解决方案：**用级联替代并行**——raw → 分钟聚合视图 → 小时聚合视图 → 天聚合视图，减少 raw 表直接挂载的物化视图数量。

**坑 3：Grafana 变量查询触发全表扫描**

在 Grafana 中配置 `$service` 变量，使用 `SELECT DISTINCT service FROM nginx_logs_raw` 取值——这个查询在 50 亿行表上会触发全表扫描，耗时数十秒。正确做法是为 service 维护一张字典表或从物化视图中取值：`SELECT DISTINCT service FROM mv_nginx_minute WHERE minute >= now() - INTERVAL 1 DAY`——数据量差 1000 倍。

### 思考题

1. **分布式写入方案设计**：如果每天有 500 亿条日志（比本章场景高 10 倍），单节点的 HTTP 接口写入已经扛不住（约 58 万 rows/s，接近单节点瓶颈）。请设计一套分布式写入方案，要求：
  
  - 日志采集端无状态、可水平扩展
  - 数据均匀分布到多个分片，避免写入热点
  - 分布式表查询时结果正确不丢不重
  - 提示：考虑使用 Distributed 表 + rand() 分片键，还是按 service 一致性哈希？写入代理层用什么？（参考答案见附录 D）
2. **基于 trace_id 的全链路追踪加速**：本章的 trace_id 查询在 raw 表上是全表扫描。请设计一套方案，使得 trace_id 查询延迟降至 100ms 以内（在 50 亿行数据中）。要求：
  
  - 不能对单条 trace_id 建主键（基数太高）
  - 考虑使用物化视图 + bloom_filter 跳数索引的组合
  - 分析你的方案中查询是如何定位到目标 Part 的
  - 提示：`ngrambf_v1` 跳数索引 + `has()` 函数配合物化视图做 trace 索引表

---

> **本章完**。恭喜你完成了基础篇全部 16 章的学习！从 ClickHouse 的术语概念（第 1 章），到单机部署（第 2 章）、数据建模（第 3-5 章）、导入导出（第 6 章）、索引优化（第 7、14 章）、SQL 实战（第 8 章）、物化视图（第 9 章）、TTL 管理（第 10 章）、权限安全（第 11 章）、备份恢复（第 12 章）、系统监控（第 13 章）、故障排查（第 15 章），到今天用所有这些知识搭建出一个企业级日志分析平台——你已经具备了独立完成 ClickHouse 单机场景下的数据建模、查询优化和运维排障的能力。**下一阶段，我们将进入中级篇，深入分布式架构、Kafka 实时管道和高阶性能调优——准备好了吗？**