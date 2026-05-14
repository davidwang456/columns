# 第23章：自定义Exporter开发（Go语言篇）

> 写一个"能用"的 exporter 容易，写一个"生产可用"的才是本事。

---

## 一、项目背景

数据平台团队用自研的 DataSync 工具每天同步数 TB 的数据，涉及 MySQL、Redis、Kafka 等多类数据源。他们希望能像监控 MySQL 那样，实时看到每次同步任务的数据量、耗时、成功/失败状态。问题来了：官方的 mysqld_exporter、redis_exporter 能用，但并没有针对 DataSync 的 exporter。

"那就自己写一个呗。"——听起来很简单：起一个 HTTP 服务，暴露 `/metrics` 端点，返回 Prometheus 能解析的文本格式就行了。但真要做到生产可用，坑可不少：多个 collector 的并发采集怎么协调？数据库连接池怎么管理，才能避免资源泄漏？Prometheus 那边已经超时了，exporter 还在傻傻查数据库怎么办？指标注册如果重名，直接一个 panic 把服务搞崩又怎么办？还有，exporter 自身曝没暴露健康状态，出了故障谁知道？

好在 Prometheus 生态对 Go 语言极其友好。Prometheus 本身就用 Go 写的，社区有官方的 `client_golang` 库，Go 的 goroutine 并发模型和 `net/http` 标准库天然适合写 exporter。本章就带你从零写一个生产级的 MySQL 监控 Exporter，虽然精简，但覆盖了所有核心设计模式——读完你就能举一反三，给自己的自研服务写 exporter。

我们在实战中使用的业务场景虽然是 DataSync，但示例代码会以 MySQL 监控为题材——因为读者更容易复现，而且数据库监控是所有自研 exporter 中最经典的范本。本质上，掌握了 MySQL Exporter 的写法，无论是监控 DataSync、监控 API 网关、还是监控硬件设备，套路完全一样。

---

## 二、剧本式交锋对话

**小胖**：（搓手）大师，我们那个 DataSync 想接入 Prometheus 监控，我看了看文档，说要写一个 exporter。这东西本质上就是起个 HTTP 服务，等 Prometheus 过来 `GET /metrics`，然后返回一堆指标数据，对吧？

**大师**：没错，exporter 的本质就是 Prometheus 和你被监控系统之间的"翻译官"。Prometheus 只认一种格式——就是你在 `/metrics` 看到的那种带 `# HELP`、`# TYPE` 注释的文本格式。exporter 负责把业务数据"翻译"成这种格式。Go 特别适合干这事，标准库自带高性能 HTTP 服务器，`prometheus/client_golang` 这个库把 Counter、Gauge、Histogram 这些指标类型全封装好了，你只需要实现 `Collector` 接口就行。

**小白**：（插嘴）Collector 接口？我看文档里有什么 `MustNewConstMetric`、`NewDesc`，还有 `prometheus.Register`，到底怎么组织代码结构比较好？

**大师**：问得好。标准做法是**一个 collector 负责一类指标**。比如我们写 MySQL exporter，就定义一个 `MySQLCollector` 结构体，把所有指标描述符（Desc）挂在这个结构体上，然后实现 `Describe` 和 `Collect` 两个方法。启动时用 `registry.MustRegister(collector)` 注册进去，Prometheus 每次来拉数据，就自动调你的 `Collect` 方法。

至于 `MustNewConstMetric` 和 `NewConstMetric`，区别很简单：带 Must 的版本在出错时直接 panic，不带 Must 的返回 error。生产环境我建议用 `MustNewConstMetric` + 前置校验，因为如果指标定义本身写错了（比如 label 数量对不上），那属于代码 bug，panic 暴露出来反而是好事，比静默吞错强。

**小胖**：我还有个担心——生产环境的 MySQL 连接数有限，exporter 每次 scrape 不会给我开一堆连接不释放吧？

**大师**：这就涉及连接池管理了。很多新手以为 `sql.Open` 返回的是一条连接，其实 `sql.DB` 是一个连接池。你需要在 `init` 阶段配置 `SetMaxOpenConns` 和 `SetMaxIdleConns`，比如最多开 3 个连接。更关键的是 **scrape timeout 的处理**——Prometheus 的 scrape 请求自带一个 HTTP Request Context，如果超时了，这个 Context 就会被取消。你的 collector 里所有数据库查询必须用带 Context 的版本（`PingContext`、`QueryRowContext`），这样才能在超时发生时优雅退出，而不是继续占用连接。

**小白**：那 exporter 自身状态怎么暴露？比如数据库连不上的时候，难道 `/metrics` 直接返回 500 吗？

**大师**：绝对不行。Prometheus 的标准做法是暴露一个 `mysql_up` 指标——这是一个 Gauge，值为 1 表示正常，0 表示挂了。即使数据库挂了，exporter 也应该返回 200，只是 `mysql_up` 显示为 0。另外，每个采集周期内的错误不要直接返回给 `MustNewConstMetric` 导致 panic，而要记录到一个专门的 `scrape_errors_total` 计数器上。还有一点容易被忽略：**scrape 耗时**也要暴露成一个 `Gauge`，这样你就能在 Grafana 上看到 exporter 本身的性能趋势。

**小胖**：（恍然大悟）原来如此！那命名上有什么讲究？我看社区里有 mysqld_exporter、redis_exporter，命名好像有规律？

**大师**：对。Go 写的 exporter 通常命名为 `xxx_exporter` 格式，binary 文件叫 `xxx_exporter`（也有用下划线连的 `xxx_exporter`）。指标名前缀要和 exporter 名保持一致，方便区分来源——比如 `mysql_up`、`mysql_global_status_threads_connected`。如果指标是 exporter 自身的元信息，就用 `mysql_exporter_` 前缀，比如 `mysql_exporter_build_info`、`mysql_exporter_scrape_duration_seconds`，这样在 Grafana 里一眼就能分清哪些是业务指标，哪些是 exporter 自监控指标。

---

## 三、项目实战：从零构建 MySQL Exporter

### 3.1 环境准备

- **Go 1.21+**：确保 `go version` 输出满足要求
- **MySQL 8.0**：本地或 Docker 均可，需有可访问的测试实例
- **项目初始化**：

```bash
mkdir mysql-exporter && cd mysql-exporter
go mod init mysql-exporter
go get github.com/prometheus/client_golang
go get github.com/go-sql-driver/mysql
```

### 3.2 步骤一：项目骨架

创建 `main.go`，引入核心依赖：

```go
package main

import (
    "database/sql"
    "log"
    "net/http"
    "os"
    "runtime"
    "time"

    "github.com/prometheus/client_golang/prometheus"
    "github.com/prometheus/client_golang/prometheus/promhttp"
    _ "github.com/go-sql-driver/mysql"
)

var (
    db  *sql.DB
    dsn = os.Getenv("MYSQL_DSN")
)
```

用环境变量传 DSN（Data Source Name），避免密码写死在代码里。`_ "github.com/go-sql-driver/mysql"` 这个空白导入将 MySQL 驱动注册到 `database/sql`。

### 3.3 步骤二：定义 Collector 和指标描述符

```go
type MySQLCollector struct {
    mysqlUp             *prometheus.Desc
    mysqlQueries        *prometheus.Desc
    mysqlConnections    *prometheus.Desc
    mysqlSlowQueries    *prometheus.Desc
    mysqlReplicationLag *prometheus.Desc
    scrapeDuration      *prometheus.Desc
    scrapeErrors        *prometheus.Desc
}

func NewMySQLCollector() *MySQLCollector {
    return &MySQLCollector{
        mysqlUp: prometheus.NewDesc(
            "mysql_up",
            "Whether MySQL is reachable (1=up, 0=down)",
            []string{}, nil,
        ),
        mysqlQueries: prometheus.NewDesc(
            "mysql_global_status_queries_total",
            "Total number of queries executed",
            []string{}, nil,
        ),
        mysqlConnections: prometheus.NewDesc(
            "mysql_global_status_threads_connected",
            "Current number of open connections",
            []string{}, nil,
        ),
        mysqlSlowQueries: prometheus.NewDesc(
            "mysql_global_status_slow_queries_total",
            "Total number of slow queries",
            []string{}, nil,
        ),
        mysqlReplicationLag: prometheus.NewDesc(
            "mysql_slave_status_seconds_behind_master",
            "Replication lag in seconds (0 if master or SQL thread not running)",
            []string{"channel"}, nil,
        ),
        scrapeDuration: prometheus.NewDesc(
            "mysql_exporter_scrape_duration_seconds",
            "Time taken to scrape MySQL metrics",
            []string{}, nil,
        ),
        scrapeErrors: prometheus.NewDesc(
            "mysql_exporter_scrape_errors_total",
            "Total number of scrape errors",
            []string{}, nil,
        ),
    }
}
```

`prometheus.NewDesc(name, help, labels, constLabels)` 的四个参数释义：`name` 是指标全名（包括前缀），`help` 是说明文本，`labels` 是动态标签列表，`constLabels` 是固定标签（本示例未使用）。注意指标类型的选择：`_total` 后缀暗示 Counter，`threads_connected` 是 Gauge，`seconds_behind_master` 是 Gauge。

### 3.4 步骤三：实现 Describe 和 Collect 接口

```go
func (c *MySQLCollector) Describe(ch chan<- *prometheus.Desc) {
    ch <- c.mysqlUp
    ch <- c.mysqlQueries
    ch <- c.mysqlConnections
    ch <- c.mysqlSlowQueries
    ch <- c.mysqlReplicationLag
    ch <- c.scrapeDuration
    ch <- c.scrapeErrors
}

func (c *MySQLCollector) Collect(ch chan<- prometheus.Metric) {
    startTime := time.Now()
    defer func() {
        ch <- prometheus.MustNewConstMetric(
            c.scrapeDuration,
            prometheus.GaugeValue,
            time.Since(startTime).Seconds(),
        )
    }()

    err := db.Ping()
    if err != nil {
        ch <- prometheus.MustNewConstMetric(c.mysqlUp, prometheus.GaugeValue, 0)
        ch <- prometheus.MustNewConstMetric(c.scrapeErrors, prometheus.CounterValue, 1)
        return
    }
    ch <- prometheus.MustNewConstMetric(c.mysqlUp, prometheus.GaugeValue, 1)

    var queries float64
    if err := db.QueryRow(
        "SELECT VARIABLE_VALUE FROM information_schema.GLOBAL_STATUS WHERE VARIABLE_NAME='Queries'",
    ).Scan(&queries); err == nil {
        ch <- prometheus.MustNewConstMetric(c.mysqlQueries, prometheus.CounterValue, queries)
    } else {
        ch <- prometheus.MustNewConstMetric(c.scrapeErrors, prometheus.CounterValue, 1)
    }

    var threads float64
    if err := db.QueryRow(
        "SELECT VARIABLE_VALUE FROM information_schema.GLOBAL_STATUS WHERE VARIABLE_NAME='Threads_connected'",
    ).Scan(&threads); err == nil {
        ch <- prometheus.MustNewConstMetric(c.mysqlConnections, prometheus.GaugeValue, threads)
    } else {
        ch <- prometheus.MustNewConstMetric(c.scrapeErrors, prometheus.CounterValue, 1)
    }

    var slowQueries float64
    if err := db.QueryRow(
        "SELECT VARIABLE_VALUE FROM information_schema.GLOBAL_STATUS WHERE VARIABLE_NAME='Slow_queries'",
    ).Scan(&slowQueries); err == nil {
        ch <- prometheus.MustNewConstMetric(c.mysqlSlowQueries, prometheus.CounterValue, slowQueries)
    } else {
        ch <- prometheus.MustNewConstMetric(c.scrapeErrors, prometheus.CounterValue, 1)
    }

    rows, err := db.Query("SHOW SLAVE STATUS")
    if err != nil {
        ch <- prometheus.MustNewConstMetric(c.scrapeErrors, prometheus.CounterValue, 1)
        return
    }
    defer rows.Close()
    if rows.Next() {
        cols, _ := rows.Columns()
        values := make([]interface{}, len(cols))
        for i := range values {
            values[i] = new(sql.NullString)
        }
        rows.Scan(values...)
        for i, col := range cols {
            if col == "Seconds_Behind_Master" {
                val := values[i].(*sql.NullString)
                if val.Valid {
                    var lag float64
                    fmt.Sscanf(val.String, "%f", &lag)
                    ch <- prometheus.MustNewConstMetric(
                        c.mysqlReplicationLag, prometheus.GaugeValue, lag, "default",
                    )
                } else {
                    ch <- prometheus.MustNewConstMetric(
                        c.mysqlReplicationLag, prometheus.GaugeValue, 0, "default",
                    )
                }
            }
        }
    }
}
```

核心要点：每个数据库查询都检查 error，出错时递增 `scrapeErrors` 而非让请求直接失败。`defer` 确保 scrape 耗时一定被记录。`SHOW SLAVE STATUS` 返回的 `Seconds_Behind_Master` 字段可能为 NULL（表示不是从库或 SQL 线程已停止），需要用 `sql.NullString` 安全处理。

### 3.5 步骤四：连接池管理

```go
func initDB() {
    var err error
    db, err = sql.Open("mysql", dsn)
    if err != nil {
        log.Fatalf("Failed to open database: %v", err)
    }

    db.SetMaxOpenConns(3)
    db.SetMaxIdleConns(2)
    db.SetConnMaxLifetime(5 * time.Minute)

    if err = db.Ping(); err != nil {
        log.Fatalf("Failed to ping database: %v", err)
    }
    log.Println("MySQL connection pool initialized")
}
```

`SetMaxOpenConns(3)` 是关键配置。为什么不设大一点？因为 exporter 通常被多个 Prometheus 实例 scrape，但同一时刻并发 scrape 不会很多。设太大反而可能耗尽 MySQL 的 `max_connections`。`SetConnMaxLifetime` 让长连接定期回收，避免 MySQL 端的 `wait_timeout` 断开导致 exporter 报错。

### 3.6 步骤五：完成 main 函数并构建运行

```go
func main() {
    if dsn == "" {
        log.Fatal("MYSQL_DSN environment variable is required")
    }

    initDB()

    buildInfo := prometheus.NewGaugeVec(
        prometheus.GaugeOpts{
            Name: "mysql_exporter_build_info",
            Help: "MySQL Exporter build information",
        },
        []string{"version", "go_version"},
    )
    buildInfo.WithLabelValues("1.0.0", runtime.Version()).Set(1)

    registry := prometheus.NewRegistry()
    registry.MustRegister(buildInfo)
    registry.MustRegister(NewMySQLCollector())

    http.Handle("/metrics", promhttp.HandlerFor(registry, promhttp.HandlerOpts{}))
    http.HandleFunc("/health", func(w http.ResponseWriter, r *http.Request) {
        w.WriteHeader(http.StatusOK)
        w.Write([]byte("OK"))
    })

    log.Println("MySQL Exporter listening on :9104")
    log.Fatal(http.ListenAndServe(":9104", nil))
}
```

构建与验证：

```bash
go build -o mysql-exporter .
$env:MYSQL_DSN="user:password@tcp(127.0.0.1:3306)/"
./mysql-exporter

# 另一个终端验证
curl http://localhost:9104/metrics | Select-String "mysql_"
curl http://localhost:9104/health
```

Prometheus 采集配置（`prometheus.yml`）：

```yaml
scrape_configs:
  - job_name: "mysql-exporter"
    static_configs:
      - targets: ["localhost:9104"]
    scrape_timeout: 10s
```

### 3.7 可能遇到的坑

1. **指标重名 panic**：`prometheus.Register` 遇到同名指标会返回 error，而 `MustRegister` 直接 panic。解决方案：要么确保全局只注册一次，要么在注册前用 `registry.Unregister` 清理。开发阶段用 `Register` 并打印 error 更安全。

2. **sql.DB 不是单连接**：很多新手在 goroutine 中执行 `db.Close()`，以为是在关一条连接，实际上关了整个池。`QueryRow` 返回的 `Row` 无需手动 Close，但 `Query` 返回的 `Rows` 必须 `defer rows.Close()`，否则连接永不归还。

3. **Counter 和 Gauge 选错**：`mysql_global_status_queries_total` 应该用 Counter（只增不减），`mysql_up` 应该用 Gauge（1↔0 双向变化）。如果 `queries_total` 用 Gauge，PromQL 的 `rate()` 函数行为会完全错误。

4. **Registry 共享问题**：如果在 handler 里每次新建 registry 并注册 collector，collector 的内部状态（如错误计数）会丢失。正确做法是在 `main` 中创建一个全局 registry，所有 handler 共用。

---

## 四、项目总结

### 设计模式回顾

一个标准的 Go Exporter 遵循三层结构：**Collector 定义层**（指标描述符 + 采集逻辑）、**Registry 注册层**（指标注册与管理）、**HTTP Handler 暴露层**（`/metrics` + `/health`）。Collector 接口的 `Describe` 方法负责声明指标元数据，`Collect` 方法负责实际采集并写入 channel。这种设计将元数据声明和运行时采集解耦，使多个 collector 可以同时工作而互不干扰。

### 指标类型选型指南

| 场景 | 指标类型 | 示例 |
|------|----------|------|
| 只增不减的累计值 | Counter | `queries_total`、`errors_total` |
| 可上可下的瞬时值 | Gauge | `threads_connected`、`seconds_behind_master` |
| 请求耗时分布 | Histogram | `http_request_duration_seconds` |
| 分位数统计 | Summary | `rpc_duration_quantile`（不推荐客户端计算） |

### MustNewConstMetric vs NewConstMetric

- **NewConstMetric**：返回 `(Metric, error)`，适合 label 值来自外部输入、可能不合法时需要捕获 error 的场景。
- **MustNewConstMetric**：直接返回 `Metric`，非法输入时 panic。适合 label 值由代码生成（编译期可保证正确）的场景，生产代码中更常用，因为如果 label 数量和 Desc 定义对不上，那是必然的 bug，早崩溃早发现。

### 生产级 Exporter 自检清单

- [ ] 暴露 `up` 指标（Gauge，1/0），反映目标系统是否可达
- [ ] 暴露 `build_info`（Gauge，标签化版本号），用于标识部署版本
- [ ] 暴露 `scrape_errors_total`（Counter），记录每次采集中的错误次数
- [ ] 暴露 `scrape_duration_seconds`（Gauge），记录每次采集耗时
- [ ] 数据库/MQ 连接配置连接池参数（MaxOpenConns、MaxIdleConns、ConnMaxLifetime）
- [ ] 所有外部调用支持 Context 传递，确保 scrape timeout 时能优雅退出
- [ ] 提供 `/health` 端点（返回 200），用于 K8s 或负载均衡的健康检查
- [ ] 使用环境变量或配置文件传递敏感信息，不硬编码密码
- [ ] 指标命名遵循 `<子系统>_<指标名>_<单位>` 规范

### 适用场景与注意事项

自研服务监控、数据库/中间件监控、硬件设备 SNMP 数据暴露、第三方 API 调用结果监控——只要 Prometheus 不能直接拉取数据源，就需要 exporter。但要注意：不要在 collector 中做长时间的阻塞操作（如大表全表扫描），宁可简化指标精度；数据库查询结果务必验证 error，哪怕只是日志记录，也比静默吞错强；大规模部署时，务必计算好 `max_connections = exporter实例数 × MaxOpenConns`，避免打爆数据库。

### 常见踩坑案例

**案例一**：某团队在 `init()` 中 `prometheus.MustRegister(collector)`，然后写单元测试时 `go test` 再次注册同名 collector，直接 panic。根因是 `init()` 在包导入时执行，测试中重复导入导致重复注册。解决办法：将注册逻辑放在 `NewRegistry()` 之后，避免全局注册。

**案例二**：一位同事写了 `db.Query("SELECT * FROM huge_table")` 后忘了 `rows.Close()`，一周后 MySQL 连接池耗尽，应用全挂。教训：永远用 `defer rows.Close()`，或者用 `QueryRow` 替代单行查询。

**案例三**：某 exporter 的 scrape 耗时稳定在 9 秒，Prometheus scrape timeout 设为 10 秒。某天数据库慢了 2 秒，全部 scrape 超时，指标全部断崖。解决方案：采集耗时通过 `scrape_duration_seconds` 可视化监控，并在接近 timeout 时触发告警。

### 思考题

1. 如果一个 exporter 需要同时采集 MySQL 和 Redis，应该如何设计 Collector 结构？是否应该放在同一个 exporter 中？
2. 如何为 exporter 编写单元测试？提示：使用 `httptest.NewServer` 模拟 Prometheus 采集请求，用 `testify/mock` 模拟数据库查询。

---

> 下一篇预告：第24章《Prometheus 告警规则编写实战》——从阈值告警到基于预测的告警，让你的告警少一点噪、多一点准。
