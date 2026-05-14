# 第38章：自定义Exporter高阶——并发安全、连接池与生产落地

## 一、项目背景

某团队在第23章的基础上开发了一个MySQL Exporter，灰度上线后一切正常。直到某个周三凌晨，数据库主库发生切换——DBA执行了计划内的主从切换，新主库的DNS记录指向了新的IP地址。然而监控大屏上所有MySQL相关指标突然归零。值班同事排查后发现，Exporter进程本身运行正常，但它持有的数据库连接仍指向旧IP——那个已经变成只读从库、并且拒绝连接的旧地址。原来Go的`database/sql`连接池在`sql.Open`阶段完成了DNS解析，之后一直复用已建立的TCP连接，完全不知道远端IP已经发生了变化。最终通过紧急重启Exporter才恢复监控，整个过程持续了12分钟，期间告警系统完全失明，运维团队错过了三次关键的业务告警。

更严重的问题还在后面。一次例行压测期间，Prometheus的scrape interval设置为15秒，scrape timeout为10秒。但由于某条SQL的执行计划走偏（全表扫描），MySQL花了30秒才返回结果。令人惊讶的是，Exporter并没有在10秒时中止查询——因为代码中根本没有传递context超时机制。于是Prometheus放弃等待后，Exporter的SQL仍在MySQL侧继续执行，白白消耗数据库资源。压测叠加这些"僵尸查询"，同时堆积了十几个未取消的慢查询线程，最终拖慢了整个数据库集群，导致业务请求出现大面积延迟。

安全审计还揭示了一个低级但危险的错误：Exporter使用的数据库用户拥有`ALL PRIVILEGES`，包括`DROP DATABASE`和`DELETE`。这意味着万一Exporter的配置文件被攻击者篡改（比如在DSN中注入恶意SQL），整个数据库都有被清空的风险。监控账号本应只具备只读权限，这是安全基线的基本要求。

这三个事故清晰地揭示了一个事实：**写一个能跑起来的Exporter容易，写出生产级的Exporter需要系统性地处理**：连接池的动态刷新（应对主从切换）、scrape timeout的context传递、并发安全的指标采集、合理的最小权限、Exporter自身的健康检查和自监控、优雅的启动和停止。本章将在第23章MySQL Exporter的基础上，逐项讲解如何将其升级为生产可靠版本。

## 二、剧本式交锋对话

**小胖**：（满脸愁容）大师，我们的MySQL Exporter又出问题了。上次数据库切换，Exporter死抱着旧IP不放，所有指标变成0，告警全哑了。我重启一下就好了，但这总不是办法吧？

**大师**：（放下手中的咖啡）你这个Exporter的连接是一个`*sql.DB`从头用到尾，对吧？你设置`SetConnMaxLifetime`了吗？

**小胖**：没有……我就`sql.Open`了一下，然后就用了。

**大师**：这就是问题所在。MySQL的Go驱动默认不会自动检测DNS变更——一旦连接建立，IP就固定了。你得给连接设置一个最大生命周期，比如10分钟，到时间后连接池会自动关闭旧连接、创建新连接，新连接就会解析到新IP。

**小白**：（快速记笔记）那是不是设个`SetConnMaxLifetime`就万事大吉了？

**大师**：还不够。你需要一套完整的连接池管理策略。首先是容量控制：`SetMaxOpenConns(5)`限制最大连接数——Prometheus通常只有一个scrape loop，加上潜在的重试和后台健康检查，5个连接绰绰有余。过大的连接池不仅浪费MySQL的`max_connections`配额，在故障场景下还可能放大问题——比如Exporter内部出现goroutine泄露，每个泄露的goroutine都占用一个连接，很快就能把MySQL的连接池耗尽。其次是回收策略：`SetMaxIdleConns(2)`保持两个空闲连接降低TCP握手开销，`SetConnMaxIdleTime(5 * time.Minute)`让空闲太久的连接自动回收，释放MySQL端的资源。最关键的是——后台goroutine每30秒ping一次数据库，如果连续失败立即触发`connect()`重建整个连接池。这样即使数据库凌晨静默切换，最坏情况30秒内就能自动恢复，完全无需人工介入和重启。

**小胖**：说到这个，我还遇到一个问题——Prometheus scrape超时了，但Exporter那边的SQL查询还在跑，白白消耗数据库资源。

**大师**：（严肃起来）这是context传递的经典疏漏。Prometheus发起的HTTP scrape请求本身携带一个context，超时后context会`Done()`。你的Collector必须接住这个context，并传递给每个数据库操作。像这样：

```go
func (c *MySQLCollector) Collect(ch chan<- prometheus.Metric) {
    select {
    case <-c.ctx.Done():
        // scrape timeout了，立即返回，不做任何查询
        ch <- prometheus.MustNewConstMetric(c.mysqlUp, prometheus.GaugeValue, 0)
        return
    default:
    }
    // 将context传递给所有数据库操作
    rows, err := c.pool.QueryContext(c.ctx, "SELECT ...")
}
```

这样Prometheus的10秒scrape timeout一到，context立即取消，`QueryContext`会马上返回`context.DeadlineExceeded`，MySQL侧的查询也会被KILL掉。双层保险是：Exporter内部采集timeout应该设为scrape timeout的80%左右（留出网络和序列化开销），同时在MySQL侧设置`max_execution_time`作为最后兜底。

**小白**：我还有个疑问——如果多个Prometheus实例同时scrape同一个Exporter怎么办？会不会产生并发写入冲突？

**大师**：好问题！这就是并发安全的核心。Prometheus的client_golang库中，`Collect()`方法是串行调用的——同一个registry同一时间只有一个collector在执行。但如果你自己实现了缓存层（比如缓存MySQL的GLOBAL_STATUS结果30秒），那读写缓存的goroutine就可能与collect goroutine并发。此时该用`sync.RWMutex`：读缓存时加读锁（允许多个读并发），刷新缓存时加写锁（排他）。切记——**Counter类型绝对不能缓存**，因为Prometheus依赖Counter的单调递增来计算rate()，缓存会导致增量丢失。

**小胖**：还有一个让我困惑的点——健康检查端点，我看Kubernetes里面区分`livenessProbe`和`readinessProbe`，Exporter里需要两个端点吗？

**大师**：在Exporter场景下通常合并为一个`/health`就够了。标准的`/-/healthy`回答"进程是否存活"，`/-/ready`回答"是否准备好接收请求"。但对于Exporter，如果数据库ping不通，Exporter返回503让K8s杀死Pod重启有意义吗？没有——因为问题在数据库侧，重启Exporter一千次也没用。所以Exporter的`/health`应该只做轻量级的数据库连通性检查，返回200或503即可。Prometheus会自动根据`mysql_up`指标判断数据库是否可达，并根据指标缺失触发告警。健康检查端点的设计原则是：**暴露越少内部细节越安全**，不要返回数据库版本、连接数等敏感信息。

**小胖**：（若有所思）所以总结一下，生产级Exporter需要：连接池自动刷新、context超时传递、读写锁保护并发、Counter不缓存、数据库用户最小权限、健康检查轻量化。这么说来，我之前那个Exporter基本是个"裸奔"状态……

**大师**：（笑）是的，但这正是成长的过程。先让代码跑起来，再让它可靠地跑下去。记住一句话：**Exporter的可靠性上限，就是你的监控系统的可靠性上限**——Exporter挂了，Prometheus再有本事也采不到数据。

## 三、项目实战

### 环境准备

- Go 1.21+
- MySQL 8.0 测试实例
- 基于第23章MySQL Exporter代码改造

### 步骤1：连接池管理与健康检查

连接池是Exporter的"心脏"，必须妥善管理，核心文件`database.go`：

```go
package main

import (
    "database/sql"
    "log"
    "sync"
    "time"

    _ "github.com/go-sql-driver/mysql"
)

type DatabasePool struct {
    mu   sync.RWMutex
    db   *sql.DB
    dsn  string
    stop chan struct{}
}

func NewDatabasePool(dsn string) (*DatabasePool, error) {
    pool := &DatabasePool{dsn: dsn, stop: make(chan struct{})}
    if err := pool.connect(); err != nil {
        return nil, err
    }
    go pool.healthCheck()
    return pool, nil
}

func (p *DatabasePool) connect() error {
    db, err := sql.Open("mysql", p.dsn)
    if err != nil {
        return err
    }

    // 生产级连接池参数
    db.SetMaxOpenConns(5)
    db.SetMaxIdleConns(2)
    db.SetConnMaxLifetime(10 * time.Minute)
    db.SetConnMaxIdleTime(5 * time.Minute)

    if err := db.Ping(); err != nil {
        db.Close()
        return err
    }

    p.mu.Lock()
    if p.db != nil {
        p.db.Close()
    }
    p.db = db
    p.mu.Unlock()
    return nil
}

// 后台健康检查：每30秒ping一次，失败自动重连
func (p *DatabasePool) healthCheck() {
    ticker := time.NewTicker(30 * time.Second)
    defer ticker.Stop()
    for {
        select {
        case <-ticker.C:
            p.mu.RLock()
            err := p.db.Ping()
            p.mu.RUnlock()
            if err != nil {
                log.Printf("[WARN] DB health check failed: %v, reconnecting...", err)
                if reconnErr := p.connect(); reconnErr != nil {
                    log.Printf("[ERROR] Reconnection failed: %v", reconnErr)
                } else {
                    log.Printf("[INFO] DB reconnection successful")
                }
            }
        case <-p.stop:
            return
        }
    }
}

func (p *DatabasePool) GetDB() *sql.DB {
    p.mu.RLock()
    defer p.mu.RUnlock()
    return p.db
}

func (p *DatabasePool) Close() {
    close(p.stop)
    p.mu.Lock()
    defer p.mu.Unlock()
    if p.db != nil {
        p.db.Close()
    }
}
```

要点说明：`SetConnMaxLifetime(10 * time.Minute)`确保连接定期被替换——即使DNS在连接存活期间发生了变化，10分钟后新连接将解析到正确的IP地址。`SetMaxOpenConns(5)`则防止Exporter在异常情况下耗尽MySQL的`max_connections`。

### 步骤2：Context超时传递

```go
// collector.go —— 支持context的Collect方法
type MySQLCollector struct {
    pool  *DatabasePool
    cache *MetricsCache
    ctx   context.Context // 从HTTP handler传入
}

func (c *MySQLCollector) Collect(ch chan<- prometheus.Metric) {
    ctx := c.ctx

    // 快速检查context是否已取消
    select {
    case <-ctx.Done():
        ch <- prometheus.MustNewConstMetric(
            prometheus.NewDesc("mysql_up", "MySQL instance is reachable", nil, nil),
            prometheus.GaugeValue, 0,
        )
        return
    default:
    }

    db := c.pool.GetDB()

    if err := db.PingContext(ctx); err != nil {
        ch <- prometheus.MustNewConstMetric(
            prometheus.NewDesc("mysql_up", "MySQL instance is reachable", nil, nil),
            prometheus.GaugeValue, 0,
        )
        return
    }

    var slowQueries float64
    err := db.QueryRowContext(ctx,
        "SELECT VARIABLE_VALUE FROM performance_schema.global_status WHERE VARIABLE_NAME='Slow_queries'",
    ).Scan(&slowQueries)
    if err != nil {
        if ctx.Err() != nil {
            // context超时，不视为错误，直接返回
            return
        }
        log.Printf("[ERROR] query slow_queries failed: %v", err)
        return
    }

    ch <- prometheus.MustNewConstMetric(
        prometheus.NewDesc("mysql_slow_queries_total", "Total slow queries", nil, nil),
        prometheus.CounterValue, slowQueries,
    )

    // 更多指标采集...
}
```

HTTP handler中完成context注入：

```go
func metricsHandler(collector *MySQLCollector) http.HandlerFunc {
    return func(w http.ResponseWriter, r *http.Request) {
        registry := prometheus.NewRegistry()
        collector.ctx = r.Context()
        registry.MustRegister(collector)

        h := promhttp.HandlerFor(registry, promhttp.HandlerOpts{
            Timeout: 10 * time.Second,
        })
        h.ServeHTTP(w, r)
    }
}
```

关键链路：Prometheus scrape timeout → HTTP request context → `r.Context()` → `db.QueryRowContext(ctx, ...)`。当Prometheus超时放弃请求，整个调用链上的context全部级联取消。

### 步骤3：并发安全——指标缓存模式

并非所有指标都需要每次scrape实时查询。例如MySQL的`Threads_connected`是Gauge类型，缓存30秒完全可接受：

```go
type MetricsCache struct {
    mu      sync.RWMutex
    data    map[string]float64
    updated time.Time
    ttl     time.Duration
}

func NewMetricsCache(ttl time.Duration) *MetricsCache {
    return &MetricsCache{
        data: make(map[string]float64),
        ttl:  ttl,
    }
}

func (mc *MetricsCache) Get(key string) (float64, bool) {
    mc.mu.RLock()
    defer mc.mu.RUnlock()
    if time.Since(mc.updated) > mc.ttl {
        return 0, false
    }
    v, ok := mc.data[key]
    return v, ok
}

func (mc *MetricsCache) Set(data map[string]float64) {
    mc.mu.Lock()
    defer mc.mu.Unlock()
    mc.data = data
    mc.updated = time.Now()
}

// 在Collect中使用缓存
func (c *MySQLCollector) collectThreadsConnected(ch chan<- prometheus.Metric, ctx context.Context) {
    metric := prometheus.NewDesc("mysql_threads_connected", "Current connected threads", nil, nil)

    if v, ok := c.cache.Get("threads_connected"); ok {
        ch <- prometheus.MustNewConstMetric(metric, prometheus.GaugeValue, v)
        return
    }

    // 缓存未命中，查询数据库并刷新缓存
    var val float64
    err := c.pool.GetDB().QueryRowContext(ctx, "SELECT COUNT(*) FROM performance_schema.threads").Scan(&val)
    if err != nil {
        return
    }
    c.cache.Set(map[string]float64{"threads_connected": val})
    ch <- prometheus.MustNewConstMetric(metric, prometheus.GaugeValue, val)
}
```

**重要提醒**：`sync.Mutex`与`sync.RWMutex`的选择原则——读多写少的场景（如缓存读取）用`RWMutex`；读写均衡或写操作非常快时用`Mutex`（避免RWMutex的额外开销）。`sync.Map`适合key值固定且写入一次读取多次的场景，不适合频繁更新。channel适合goroutine间的数据传递和通知，不适用于指标缓存的并发控制。

### 步骤4：最小权限和安全配置

```sql
-- 创建只读监控用户（最小权限原则）
CREATE USER 'exporter'@'%' IDENTIFIED BY 'secure_password_here';
GRANT SELECT, PROCESS, REPLICATION CLIENT ON *.* TO 'exporter'@'%';
FLUSH PRIVILEGES;

-- 验证权限：尝试写操作将被拒绝
-- INSERT INTO test_table VALUES (1);  → ERROR 1142 (42000): INSERT command denied
```

Exporter敏感信息通过环境变量注入：

```go
dsn := os.Getenv("MYSQL_DSN")
if dsn == "" {
    log.Fatal("MYSQL_DSN environment variable is required")
}
```

健康检查端点不暴露内部状态细节：

```go
http.HandleFunc("/health", func(w http.ResponseWriter, r *http.Request) {
    if err := pool.GetDB().Ping(); err != nil {
        w.WriteHeader(http.StatusServiceUnavailable)
        return
    }
    w.WriteHeader(http.StatusOK)
})
```

### 步骤5：生产级Exporter完整清单与部署

生产部署核心检查清单：

| 检查项 | 说明 |
|--------|------|
| 连接池参数 | MaxOpenConns / MaxIdleConns / ConnMaxLifetime 全部显式配置 |
| Context超时 | HTTP ctx → DB ctx 全链路传递 |
| 并发安全 | mutex/RWMutex 覆盖所有共享状态 |
| 最小权限 | 数据库用户仅有 SELECT / PROCESS / REPLICATION CLIENT |
| 健康检查 | `/health` 返回 200/503 |
| 自身指标 | `build_info` / `scrape_duration_seconds` / `scrape_errors_total` |
| 优雅关闭 | SIGTERM → 等待 scrape 完成 → 关闭 DB 连接池 |
| 日志可配 | 日志级别通过环境变量控制 |
| 指标描述 | HELP 文本清晰描述指标含义和单位 |

Dockerfile（多阶段构建）：

```dockerfile
FROM golang:1.21-alpine AS builder
WORKDIR /app
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 go build -ldflags="-s -w" -o mysql-exporter .

FROM alpine:3.19
RUN adduser -D exporter
COPY --from=builder /app/mysql-exporter /usr/local/bin/
USER exporter
EXPOSE 9104
HEALTHCHECK --interval=30s --timeout=3s \
    CMD wget -qO- http://localhost:9104/health || exit 1
ENTRYPOINT ["mysql-exporter"]
```

**可能遇到的坑**：

1. **Counter缓存导致增量丢失**：Prometheus的`rate()`依赖Counter单调递增特性，缓存后两次scrape看到同一个值，计算出的rate为0。Counter永远不应被缓存。

2. **空闲连接被MySQL服务端关闭**：MySQL默认`wait_timeout`为8小时，但有些环境设置为更短。`SetConnMaxLifetime`确保在服务端关闭之前，客户端主动重建连接。

3. **context超时后SQL仍在执行**：Go的`database/sql`在context取消后会关闭底层连接，但MySQL侧可能尚未感知。双重保险是在DSN中设置`max_execution_time=8000`（8秒，略小于scrape timeout的10秒）。

**测试验证**：

```bash
# 并发测试
ab -n 1000 -c 10 http://localhost:9104/metrics

# 超时测试：设置短scrape timeout，执行慢查询
# 在Collect中插入 time.Sleep(15 * time.Second)，观察context是否在10秒内取消

# 主从切换测试：修改/etc/hosts模拟DNS变更
# 观察30秒内健康检查日志是否触发重连
```

## 四、项目总结

**生产级Exporter检查清单（10项必查）**：

1. 连接池参数显式配置（MaxOpen/MaxIdle/MaxLifetime）
2. 后台健康检查+自动重连机制
3. Context从HTTP handler全链路传递至DB操作
4. 共享状态使用适当的同步原语保护
5. Counter/Gauge区分对待（Counter不缓存）
6. 数据库用户最小权限（SELECT+PROCESS+REPLICATION CLIENT）
7. `/health`健康检查端点
8. 自身Prometheus指标暴露
9. SIGTERM优雅关闭
10. 敏感信息通过环境变量注入

**并发安全选择决策表**：

| 场景 | 推荐同步原语 | 原因 |
|------|-------------|------|
| 读多写少（缓存读取） | sync.RWMutex | 允许多个并发读，提升吞吐 |
| 读写均衡、写很快 | sync.Mutex | 避免RWMutex的额外管理开销 |
| key固定、写一次读多次 | sync.Map | 内部优化了只读路径 |
| goroutine间传递数据 | channel | 天然的通信原语 |

**数据库连接池参数推荐**：

| 并发级别 | MaxOpenConns | MaxIdleConns | ConnMaxLifetime |
|---------|-------------|-------------|-----------------|
| 低（1-2 scrape） | 3 | 1 | 10min |
| 中（3-5 scrape） | 5 | 2 | 10min |
| 高（Prometheus联邦/多实例） | 10 | 5 | 10min |

**适用场景**：本章的生产级实践适用于三类典型场景。第一类是生产级中间件监控（MySQL、Redis、Kafka、Elasticsearch），这些组件是业务的关键依赖，Exporter的稳定性直接影响告警质量，本章的连接池管理和context超时传递是刚需。第二类是自定义应用指标暴露（业务QPS、订单延迟分布、用户活跃度），应用自身可能就是Go写的，可以直接集成Prometheus client而非独立Exporter，但连接池和并发安全的思路完全通用。第三类是硬件设备监控（通过SNMP/IPMI采集服务器温度、风扇转速、电源状态），这类采集往往耗时较长（SNMP walk可能数十秒），超时控制和并发采集尤为重要。

**连接池的深层考量**：很多人认为连接池越大越好，这是一个常见误区。连接池大小受到两个硬约束：一是MySQL的`max_connections`上限（通常默认151，扣除系统预留5个，实际可用约146个），如果多个Exporter实例加上业务应用共享这个限额，Exporter的`MaxOpenConns`必须控制；二是操作系统层面的文件描述符限制，每个TCP连接消耗一个fd。推荐的配置不是拍脑袋决定的，而是基于实际压测——用`mysqladmin status`观察`Threads_connected`在scrape峰值时的值，设置为峰值×1.5即可。

**缓存策略的边界**：指标缓存虽然能降低数据库负载，但有明确的使用边界。Gauge类型（如当前连接数、缓冲池命中率）适合缓存，因为这些指标值本身就在持续波动，30秒的缓存延迟在监控视角下完全可接受。Counter类型（如查询总数、慢查询数）严禁缓存，因为Prometheus的`rate()`函数依赖Counter的单调递增特性——两次scrape之间看到同一个值意味着增量为零，这是错误的。Histogram和Summary的`_bucket`和`_count`本质上也是Counter，同样不应缓存。如果不确定一个指标的类型，保守策略是不缓存。

**常见踩坑经验**：

- *案例1：goroutine泄露导致OOM*。某Exporter上线后运行平稳，一周后突然OOM重启。排查发现连接池的`Close()`方法忘记在`main()`退出时调用，导致后台健康检查goroutine永远无法退出。由于这个Exporter被Supervisor管理，每次异常退出后自动拉起，旧的goroutine继续存活，新的goroutine又创建，一周内累计数千个goroutine。修改方案：确保`main()`中注册`signal.Notify`监听SIGTERM，收到信号后依次调用`pool.Close()`和`server.Shutdown()`，利用`stop` channel通知所有后台goroutine退出。

- *案例2：不合适的锁导致性能骤降*。最初使用`sync.Mutex`保护一个读多写少（读写比例约100:1）的指标缓存，在单Prometheus实例scrape时完全正常。当团队搭建了Prometheus联邦后，4个Prometheus实例同时scrape同一个Exporter，`Mutex`的锁竞争急剧恶化，scrape延迟从50ms飙升到2秒。修改方案：分析访问模式后替换为`sync.RWMutex`，所有缓存读取用`RLock()`（允许多个并发读），仅在缓存刷新时使用`Lock()`（排他写）。修改后延迟回落到80ms。

- *案例3：context传递断裂导致超时无效*。开发者在`Collect()`方法中为了方便调试，创建了一个`context.WithTimeout(context.Background(), 30*time.Second)`来执行数据库查询，而不是使用HTTP handler传入的`r.Context()`。结果Prometheus的10秒scrape timeout完全失效——因为这个独立的context不受HTTP请求取消的影响。数据库慢查询永远不会被取消。修改方案：在HTTP handler中通过collector的字段传递`r.Context()`，确保整个调用链路共享同一个context树。

**思考题**：

1. 如果你的Exporter需要同时监控10个MySQL实例，连接池怎么管理？每个实例一个独立连接池还是共享一个？各自的优劣是什么？（提示：考虑故障隔离、连接数上限、代码复杂度）

2. Exporter的指标缓存如果设置为5分钟——即每5分钟才真正查询一次MySQL——那么PromQL的`rate(mysql_queries_total[1m])`会得到什么结果？为什么？
