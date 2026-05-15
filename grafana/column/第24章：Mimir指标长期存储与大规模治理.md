# 第24章：Mimir指标长期存储与大规模治理

## 1. 项目背景

"Prometheus存了3个月的数据后磁盘占用超过2TB，查询一个月的QPS趋势要40秒才出结果。现在合规要求所有监控数据保留1年——按现在的增长速度，1年后光存储就要20TB，Prometheus显然撑不住。"

SRE负责人老王正在做一个艰难的技术决策。Prometheus单机存储方案在数据量小时够用，但随着公司业务增长——200+个微服务、每天产生约200万条active time series、每月新增约500GB数据——Prometheus的本地TSDB已经成了瓶颈。查询变慢、磁盘不足、无法水平扩展。

Grafana Mimir就是为解决这个问题诞生的。它是Grafana Labs开源的Prometheus长期存储方案，支持水平扩展、对象存储后端、多租户隔离。不仅解决了"存储成本"问题，还提供了Query分片、压缩、去重等大规模治理能力。本章将从Prometheus Remote Write入手，带你搭建Mimir并实现"一个月的性能查询也变得像1小时一样快"。

## 2. 项目设计

**小胖**（盯着Prometheus的磁盘告警）：大师，Prometheus的磁盘又满了！每次满了就得调retention time从30天降到15天，再降到7天。再过两个月是不是只能保留3天了？合规要求保留1年的数据，Prometheus根本撑不住。

**大师**：Prometheus单机存储的天花板是显而易见的。它的设计哲学是"单节点高性能"，瓶颈在于：

1. TSDB Block的compact很耗CPU和IO
2. 磁盘容量线性增长不可水平扩展
3. 查询大时间范围时内存消耗巨大（需要加载多个Block）

而Mimir的思路是"把Prometheus的水平扩展问题分解为多个微服务"。

**小白**：Mimir的具体架构是怎样的？

**大师**（画图）：Mimir是一个微服务集群，核心组件包括：

```
Prometheus → Remote Write → Distributor → Ingester → S3/MinIO/GCS
                              ↑               ↓
                          Querier ← ← ← ← Store-gateway
                              ↓
                       Query Frontend → Grafana
                              ↓
                         Compactor (后台)
```

**Distributor**：接收Prometheus的Remote Write数据，先验证（如时间戳是否乱序），再通过一致性哈希分发给Ingester。

**Ingester**：数据写入S3前的缓存层。它在内存中累积数据（默认每2小时或达到block大小），然后flush到对象存储。

**Querier**：查询引擎。接收Grafana的PromQL查询 → 从Ingester获取最新数据 + 从Store-gateway获取历史数据 → 合并结果返回。

**Store-gateway**：管理S3中的长期数据。它只加载索引（不加载原始数据），查询时精确定位需要的chunk。

**Compactor**：后台压缩任务。把多个小Block合并为一个大Block，同时合并索引提高查询效率。

**Query Frontend**：查询缓存和分片。把一个大查询拆成多个小查询并行执行。

**小胖**（挠头）：这么复杂……和Thanos比有什么优势？

**大师**：Mimir和Thanos都是Prometheus长期存储方案，核心差异：

| 对比维度 | Mimir | Thanos |
|---------|-------|--------|
| 架构 | 微服务（组件更多但更灵活） | 组件较少 |
| 多租户 | 原生支持 | 需额外配置 |
| 查询缓存 | 内置Query Frontend | 需单独部署 |
| Grafana集成 | 原生（同为Grafana Labs产品） | 社区集成 |
| 对象存储支持 | S3/GCS/Azure/Swift | S3/GCS/Azure |
| 学习曲线 | 中-高 | 中 |

如果你的团队已经深度使用Grafana生态（Loki、Tempo），Mimir是自然的选择——统一的操作体验、统一的标签体系、统一的租户隔离。

**小白**：那Recording rules和Relabel在Mimir里怎么处理？

**大师**：两种方式：

**Prometheus侧**：继续在Prometheus实例中配置Recording rules。计算后的结果通过Remote Write发送给Mimir保存。优势：计算靠近数据源，低延迟。

**Mimir Ruler**：在Mimir中直接配置Recording rules。Mimir的Ruler组件查询Ingester/Store-gateway获取数据，计算后写入Mimir。优势：不需要维护额外的Prometheus规则实例。

一般建议：小型集群用Prometheus侧Recording rules，大型集群（多个Prometheus实例写入同一个Mimir）用Mimir Ruler做全局聚合。

**小胖**：多租户隔离怎么做？

**大师**：Mimir的租户隔离通过HTTP Header `X-Scope-OrgID`实现。不同的数据源在Remote Write时带上不同的OrgID，数据在Mimir内部完全隔离。在Grafana中配置Mimir数据源时，设置Custom HTTP Header `X-Scope-OrgID: team-a`——这样Team A的Dashboard只能看到Team A的指标。

**技术映射**：Mimir Ingester = 快递分拣中心（收数据暂存后送仓库），Store-gateway = 仓库管理员（知道每个包裹在哪个货架），Compactor = 打包工（把小包裹合并成大箱子节省空间），Query Frontend = 前台查询处（把查询拆开并行处理）。

## 3. 项目实战

**环境准备**

在Docker Compose中添加Mimir：

```yaml
  mimir:
    image: grafana/mimir:2.13.0
    container_name: mimir
    volumes:
      - ./mimir-config.yaml:/etc/mimir/mimir.yaml
      - mimir_data:/data
    ports:
      - "9009:9009"  # API
      - "9095:9095"  # gRPC
    command: ["-ingester", "-distributor", "-querier", "-query-frontend",
              "-store-gateway", "-compactor", "-ruler",
              "-config.file=/etc/mimir/mimir.yaml"]

  minio:
    image: minio/minio:latest
    container_name: minio
    environment:
      MINIO_ROOT_USER: mimir
      MINIO_ROOT_PASSWORD: mimir123
    ports:
      - "9000:9000"
    command: server /data --console-address ":9001"
    volumes:
      - minio_data:/data

volumes:
  mimir_data:
  minio_data:
```

创建 `mimir-config.yaml`：

```yaml
multitenancy_enabled: false  # 简化，单租户模式

server:
  http_listen_port: 9009

# 块存储到MinIO（S3兼容）
blocks_storage:
  backend: s3
  s3:
    endpoint: minio:9000
    access_key_id: mimir
    secret_access_key: mimir123
    insecure: true
    bucket_name: mimir-blocks
  tsdb:
    dir: /data/tsdb
  bucket_store:
    sync_dir: /data/tsdb-sync

# 接收Prometheus数据
distributor:
  pool:
    health_check_ingesters: true

# Ingester在内存中缓存数据
ingester:
  ring:
    kvstore:
      store: inmemory
    replication_factor: 1

ruler:
  alertmanager_url: http://alertmanager:9093

compactor:
  data_dir: /data/compactor

limits:
  max_global_series_per_user: 3000000
```

**步骤一：Prometheus Remote Write到Mimir**

修改Prometheus配置（prometheus.yml），添加Remote Write：

```yaml
remote_write:
  - url: http://mimir:9009/api/v1/push
    write_relabel_configs:
      # 排除不需要长期保存的指标（如短期Buffer指标）
      - source_labels: [__name__]
        regex: 'go_.*|process_.*|prometheus_.*'
        action: drop
    queue_config:
      max_samples_per_send: 5000
      batch_send_deadline: 5s
      max_shards: 200
      capacity: 2500
```

重启Prometheus使配置生效：
```bash
docker compose restart prometheus
```

验证数据写入：
```bash
# 检查Mimir是否接收数据
curl http://localhost:9009/ready

# 查看活跃的Time Series数量
curl http://localhost:9009/api/v1/status/tsdb | jq
```

**步骤二：Grafana配置Mimir数据源**

Grafana → Data Sources → Add data source → Prometheus。

虽然Mimir兼容Prometheus API，但Grafana中仍用Prometheus Data Source类型：

| 参数 | 值 |
|------|-----|
| Name | `Mimir` |
| URL | `http://mimir:9009/prometheus` |
| Access | Server (proxy) |
| Custom HTTP Headers | (多租户时才加`X-Scope-OrgID`) |
| Prometheus type | `Mimir` |
| Prometheus version | `2.13.x` |

Save & test → 确认连接成功。

在Explore中验证：查询`up`，应该看到Prometheus写入Mimir的指标。

**步骤三：数据保留和生命周期管理**

Mimir中数据保留通过Compactor配置控制：

```yaml
compactor:
  deletion_delay: 2h  # 标记删除后等待2小时再删除
  compaction_interval: 1h
  blocks_retention_period: 4320h  # 180天（6个月）

limits:
  # 租户级别的系列数限制（防爆炸）
  max_global_series_per_user: 3000000
  max_global_series_per_metric: 20000
  # Ingester中数据的保留时间（过期后忽略）
  max_sample_age: 1h
```

监控Mimir自身的健康状态：
```promql
# Mimir Ingester内存使用
cortex_ingester_memory_series

# Mimir活跃系列数
cortex_distributor_received_samples_total

# S3操作延迟
cortex_blocks_storage_s3_request_duration_seconds
```

**步骤四：Recording Rules在Mimir中配置**

Mimir支持Prometheus语法规则的YAML文件：

```yaml
# mimir-rules.yaml
groups:
  - name: aggregated_metrics
    interval: 1m  # 规则评估间隔
    rules:
      - record: job:http_requests:rate5m
        expr: sum(rate(http_requests_total[5m])) by (job)
      
      - record: instance:node_cpu:avg5m
        expr: avg by (instance) (rate(node_cpu_seconds_total{mode!="idle"}[5m]))
      
      - record: service:error_rate:rate5m
        expr: |
          sum by (service) (rate(http_requests_total{status=~"5.."}[5m]))
          / sum by (service) (rate(http_requests_total[5m]))
```

将规则文件挂载到Mimir Ruler：
```yaml
ruler:
  rule_path: /etc/mimir/rules
  alertmanager_url: http://alertmanager:9093
```

在Grafana中使用预聚合指标（而非源数据）：
```promql
# 直接查询Recording规则的结果，比源数据查询快10倍+
job:http_requests:rate5m{job="api-gateway"}
```

**步骤五：查询性能优化**

优化Grafana查询性能的核心配置：

**Dashboard级优化**：
1. 用Recording Rules预聚合高频查询的PromQL
2. 控制Max data points（不超过2000）避免Mimir返回海量数据
3. 长期查询（30d+）使用较大的Min interval（如5m或15m）

**Mimir侧优化**：
```yaml
query_frontend:
  # 查询结果缓存（命中率高时大幅加速）
  results_cache:
    backend: memcached  # 或redis
  # 查询分片（大查询拆成小查询并行执行）
  split_queries_by_interval: 24h
  
frontend_worker:
  # 并发处理查询的goroutines数
  match_max_concurrent: 100

querier:
  # 单个查询扫描的最大样本数
  max_samples: 50000000
```

**Grafana缓存配置**：
Dashboard Settings → Cache timeout → 设60s。多人同时看同一个Dashboard时，缓存显著减少Mimir压力。

**常见坑点**
1. **Prometheus Remote Write积压**：如果Mimir写入跟不上Prometheus产生数据的速度，Remote Write队列会堆积。监控`prometheus_remote_storage_queue_highest_sent_timestamp_seconds`指标。
2. **Ingester OOM**：大量数据先进入Ingester内存缓存。调大`ingester.max_ingestion_rate`或增加Ingester实例数。
3. **对象存储费用**：频繁的Compaction会产生大量S3 API调用。使用Intelligent-Tiering或Lifecycle Policy降低费用。
4. **多租户Header缺失**：如果开启了multi-tenancy但Grafana没配Header，所有查询会返回401。

## 4. 项目总结

**Mimir核心组件职责**

| 组件 | 职责 | 故障影响 |
|------|------|---------|
| Distributor | 接收和验证写入，分发到Ingester | 写入中断 |
| Ingester | 内存缓存，刷新到对象存储 | 丢失未flush的数据 |
| Querier | 执行PromQL查询，合并结果 | 查询中断 |
| Store-gateway | 管理S3中Block的索引 | 历史数据查询中断 |
| Compactor | Block合并和压缩 | 查询变慢 |
| Query Frontend | 缓存、分片、限流 | 查询变慢或无缓存 |

**优点**
| 特性 | 说明 |
|------|------|
| 水平扩展 | 所有组件都可以单独扩展 |
| 低成本 | S3对象存储成本远低于SSD |
| 多租户 | 原生多租户隔离 |
| Prometheus兼容 | 完全兼容PromQL和Remote Write协议 |

**适用场景**
1. 长期指标存储：合规要求保留6-12个月的监控数据
2. 多集群聚合：多个Prometheus实例统一写入Mimir
3. 大规模查询：数百个用户同时查询Prometheus指标
4. 多租户平台：SaaS场景，每个租户隔离指标

**注意事项**
1. Mimir不替代Prometheus——它补充Prometheus的长期存储短板。你仍需要Prometheus做近期数据采集和告警。
2. 对象存储的延迟（S3通常50-200ms）决定了Mimir的查询比本地Prometheus慢。通过缓存和Recording rules补偿。
3. Mimir集群至少需要3个节点以保证高可用（各组件分散部署）。

**思考题**
1. 如果Prometheus每分钟产生10万个sample（每个sample = 指标名+标签+值+时间戳），写入Mimir需要多大的网络带宽？Ingester需要多大的内存？
2. Mimir的Compactor在压缩一个10GB的Block时，需要读取整个Block并重写。如果S3的PUT操作单价是$0.005/1000次，每天处理1TB数据需要多少S3费用？
