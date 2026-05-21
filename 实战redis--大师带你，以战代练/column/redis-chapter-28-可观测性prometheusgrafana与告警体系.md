# 第28章：可观测性prometheusgrafana与告警体系

## 1. 项目背景

过去团队使用 Redis 的方式很“黑盒”：接口慢了才登录机器看 `INFO`，内存满了才查哪个 key 大，主从断了才发现告警没有覆盖。一次促销活动中，Redis 命中率从 92% 掉到 61%，数据库连接池被打满，值班同学却只收到应用超时告警，没有提前看到缓存层已经异常。

生产级 Redis 不能只依赖人工执行命令。它需要指标、日志、告警和看板形成闭环：指标告诉我们内存、连接、QPS、命中率、复制、集群槽位是否健康；日志帮助定位配置变更和故障时间点；告警把风险提前推到值班人面前；Grafana 看板让开发、测试、运维在同一张图上讨论。

本章目标是搭建 Redis + Redis Exporter + Prometheus + Grafana 的最小监控栈，并配置 8 条常用生产告警。实战重点不是把界面做漂亮，而是让每条告警都有业务含义、处理人和排查流程。

## 2. 项目设计

小胖先问：“Redis 有 `INFO`，出问题我手动敲一下不就行了吗？为啥还要 Prometheus 和 Grafana？”

小白反问：“手动敲只能看到当下。命中率是什么时候掉的？内存是线性涨还是突然涨？主从延迟持续多久？没有历史曲线就很难复盘。”

大师说：“可观测性就是给系统装仪表盘。`INFO` 是原始仪表，Redis Exporter 负责把它转换成 Prometheus 能采集的指标，Grafana 负责展示趋势，Alertmanager 负责把异常通知出去。技术映射：不是新增业务功能，而是让 Redis 的运行状态可量化、可追踪、可告警。”

小胖继续问：“那是不是指标越多越好？我把所有指标都告警。”

小白摇头：“告警太多会疲劳。比如瞬时 QPS 波动不一定需要叫醒人，但主从断链、内存水位超过 90%、集群槽异常就必须处理。”

大师补充：“告警要分级。P0 是影响核心链路，例如主库不可用、集群槽不可服务；P1 是有明确风险，例如内存水位高、复制延迟大；P2 是趋势问题，例如命中率连续下降、慢查询增加。技术映射：告警不是报错收集器，而是值班动作触发器。”

小胖又看着大盘问：“命中率下降是 Redis 的锅吗？”

大师回答：“不一定。可能是 TTL 太短、发布了新 key 版本、缓存预热失败、热点数据被淘汰，也可能是业务请求结构变了。可观测性的价值是把 Redis 指标和应用指标、数据库指标放在一起看。”

## 3. 项目实战

### 3.1 Docker Compose 监控栈

创建实验目录后准备 `docker-compose.yml`：

```yaml
services:
  redis:
    image: redis:8.6
    command: ["redis-server", "--appendonly", "yes"]
    ports:
      - "6379:6379"
  redis-exporter:
    image: oliver006/redis_exporter:latest
    environment:
      REDIS_ADDR: "redis://redis:6379"
    ports:
      - "9121:9121"
    depends_on:
      - redis
  prometheus:
    image: prom/prometheus:latest
    volumes:
      - ./prometheus.yml:/etc/prometheus/prometheus.yml
      - ./alert-rules.yml:/etc/prometheus/alert-rules.yml
    ports:
      - "9090:9090"
  grafana:
    image: grafana/grafana:latest
    ports:
      - "3000:3000"
```

Prometheus 配置：

```yaml
global:
  scrape_interval: 15s
rule_files:
  - /etc/prometheus/alert-rules.yml
scrape_configs:
  - job_name: redis
    static_configs:
      - targets: ["redis-exporter:9121"]
```

启动：

```bash
docker compose up -d
curl http://localhost:9121/metrics | grep redis_up
```

### 3.2 Redis 指标分组

在 Redis 中制造一点流量：

```bash
redis-cli SET product:1 "demo" EX 60
redis-cli GET product:1
redis-cli GET product:not-exists
redis-cli INFO stats
```

Grafana 大盘建议至少放这些面板：

```text
可用性：redis_up
吞吐：redis_commands_processed_total 增速
命中率：keyspace_hits / (keyspace_hits + keyspace_misses)
内存：used_memory、maxmemory、mem_fragmentation_ratio
连接：connected_clients、blocked_clients
慢查询：slowlog_length 或慢日志采集结果
复制：master_link_status、master_last_io_seconds_ago、replication_lag
集群：cluster_state、cluster_slots_fail
```

### 3.3 8 条告警规则

示例 `alert-rules.yml`：

```yaml
groups:
  - name: redis.rules
    rules:
      - alert: RedisDown
        expr: redis_up == 0
        for: 1m
        labels: { severity: critical }
        annotations: { summary: "Redis exporter reports instance down" }
      - alert: RedisMemoryHigh
        expr: redis_memory_used_bytes / redis_memory_max_bytes > 0.85
        for: 5m
        labels: { severity: warning }
      - alert: RedisRejectedConnections
        expr: increase(redis_rejected_connections_total[5m]) > 0
        for: 1m
        labels: { severity: critical }
      - alert: RedisLowHitRate
        expr: rate(redis_keyspace_hits_total[5m]) / (rate(redis_keyspace_hits_total[5m]) + rate(redis_keyspace_misses_total[5m])) < 0.8
        for: 10m
        labels: { severity: warning }
      - alert: RedisEvictionGrowing
        expr: increase(redis_evicted_keys_total[5m]) > 1000
        for: 5m
        labels: { severity: warning }
      - alert: RedisBlockedClients
        expr: redis_blocked_clients > 0
        for: 3m
        labels: { severity: warning }
      - alert: RedisReplicationBroken
        expr: redis_connected_slaves < 1
        for: 2m
        labels: { severity: critical }
      - alert: RedisClusterSlotsFail
        expr: redis_cluster_slots_fail > 0
        for: 1m
        labels: { severity: critical }
```

实际生产要根据 exporter 指标名校准，因为不同版本和部署方式会有差异。规则上线前先在测试环境制造故障，确认 Prometheus 能触发，通知链路能到人。

### 3.4 告警处理 SOP

内存高：先查 `INFO memory`、`MEMORY STATS`、淘汰数量和碎片率，再判断是否扩容、拆实例或调整 TTL。

命中率低：先查是否发布新版本、预热是否完成、是否大批 key 过期，再看数据库压力和缓存回源限流。

复制异常：执行：

```bash
redis-cli INFO replication
redis-cli ROLE
redis-cli CLIENT LIST
```

确认是网络、认证、磁盘、主库压力还是从库重启造成。

常见坑：第一，只部署 Grafana 大盘但没有告警，相当于没人值守的仪表盘。第二，告警没有 `for` 持续时间，瞬时抖动造成噪声。第三，只监控 Redis，不监控客户端连接池和数据库回源。第四，使用默认模板却不理解每个指标含义。

## 4. 项目总结

可观测性让 Redis 从“能用”变成“可运营”。最小闭环包括指标采集、趋势看板、告警规则、通知链路和处理 SOP。看板服务于分析，告警服务于行动，复盘服务于改进。

优点：Prometheus 生态成熟，Redis Exporter 接入简单，Grafana 便于跨团队沟通。缺点：指标名和版本可能差异较大，告警阈值需要结合业务调优，过度告警会消耗值班注意力。

适用场景包括生产 Redis 实例、压测环境、大促保障、容量治理和故障复盘。不适合只为了“有大盘”而堆指标，却没有负责人和处置流程。

思考题：
1. 命中率下降时，为什么要同时查看应用发布、TTL 分布和数据库 QPS？
2. 一条好的 Redis 告警应该包含哪些信息，才能让值班人快速行动？

推广建议：开发团队负责解释业务 key 和命中率目标，测试团队负责故障注入验证告警，运维团队负责监控栈和通知链路，架构团队负责定义 SLO 和告警分级。
