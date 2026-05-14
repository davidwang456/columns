# 第24章：自定义Exporter开发（Python／Java篇）

## 一、项目背景

后端团队的技术栈以Python和Java为主，分别对应Flask/Django和Spring Boot两大阵营。随着微服务化推进，团队需要为所有Web应用接入Prometheus监控——QPS、P99延迟、错误率是三项核心指标。然而上一章讲的Go exporter模式在这两个语言里并不能直接套用。

Python团队第一个踩坑：直接使用`prometheus_client`库暴露出`/metrics`端点，开发环境跑起来没问题。一上Gunicorn多Worker模式就出怪事——Prometheus每次scrape到的数据都不一样，QPS曲线像心电图一样上下跳。根源在于每个Worker进程拥有独立的内存空间，各自维护自己的Counter实例，scrape请求随机落到不同Worker，自然得到不同的快照值。

Java团队的问题则是另一个方向。Spring Boot Actuator + Micrometer + Prometheus这套组合拳搭起来非常快，开箱即用。但运维很快找上门——一个服务实例竟然暴露了200多个JVM指标（GC、内存池、线程、缓冲区等），10个实例就是2000多条time series，Prometheus存储压力陡增。而且多数指标实际上并没有配置告警，属于"采集了但从不看"的浪费。

更深层的痛点还包括：短生命周期任务（Celery异步任务、K8s CronJob）如何上报指标？业务代码的埋点怎样才能写得优雅、不侵入核心逻辑？Python和Java的Prometheus生态各有特色和暗坑，本章分别覆盖两套技术栈的最佳实践。

## 二、剧本式交锋对话

**小胖**：（抓头发）大师救命！我昨天把Python服务上了Gunicorn 4个worker，Prometheus里看到`http_requests_total`这个指标一秒钟一个值，完全没法用。我用`prometheus_client`明明只创建了一个Counter啊？

**小白**：我也遇到类似的问题。不过我这边是Java Spring Boot，导入了`micrometer-registry-prometheus`之后，`/actuator/prometheus`一口气吐出300多个指标，Prometheus那边存储告警了。老板问我能不能把没用的JVM指标关掉，我搜了半天文档也没找到开关在哪。

**大师**：你们两个碰到的恰好是Python和Java在Prometheus生态中最经典的两个坑。小胖你先说，你在Gunicorn里用`--preload`了吗？

**小胖**：用了，`gunicorn -w 4 --preload app:app`，这样worker启动更快。

**大师**：那就对了。Python的`prometheus_client`在多进程模式下有个本质问题——每个Worker是独立的进程，内存空间完全隔离。你用`Counter('http_requests_total', ...)`创建的是进程内的对象，4个Worker就有4个互不相干的Counter。Prometheus每次scrape随机命中一个Worker，自然数据对不上。

**小胖**：那怎么办？总不能把服务退化成单进程吧？

**大师**：`prometheus_client`专门提供了multiprocess模式。核心思路是：每个Worker不再直接暴露`/metrics`，而是把指标写入由环境变量`PROMETHEUS_MULTIPROC_DIR`指定的共享目录。然后`MultiProcessCollector`读取这个目录下所有Worker的指标文件，聚合后再返回给Prometheus。

**小白**：等会儿，这个"聚合"是什么意思？4个Counter的值加起来吗？那Gauge怎么办，比如"当前内存使用量"这种指标，总不能把4个进程的内存值加起来吧？

**大师**：问到关键了。Counter和Histogram本身是单调递增的，所以跨进程聚合就是把所有Worker的数据加起来，语义上是正确的。但Gauge是可增可减的快照值，跨进程求和没有意义。**multiprocess模式不支持Gauge**——这是个大限制。如果你需要暴露Gauge类型的指标（比如队列长度、连接数），要么改用Pushgateway单独推送，要么起一个独立的单进程端点。

**小胖**：那Java呢？小白说的JVM指标爆炸怎么处理？

**大师**：Java这边是另一个思路。Micrometer是一个指标抽象层，它的设计哲学是"先收集所有，再按需过滤"。Spring Boot Actuator自动注册了大量JVM指标，需要通过`MeterFilter`来做减法。最简单的做法是在`application.yml`里全局关闭JVM指标：

```yaml
management:
  metrics:
    enable:
      jvm: false
```

但我不建议全部关闭。像`jvm.memory.used`、`jvm.memory.max`、`jvm.threads.live`这三项是出问题时排查的黄金指标，值得保留。更精细的做法是用Java配置类写一个`MeterFilter`，按指标名前缀白名单/黑名单过滤。

**小白**：那Python里的短任务呢？比如Celery异步任务，执行完进程就退出了，这怎么暴露指标？

**大师**：短生命周期任务不能用pull模式（暴露/metrics等Prometheus来拉），得用Pushgateway主动推送。思路是在任务执行结束时，创建一个独立的`CollectorRegistry`，把指标注册上去，然后`push_to_gateway`推到Pushgateway。但有两个注意点：第一，push操作本身可能失败，必须包在try/except里防止影响业务；第二，Pushgateway不会自动清理过期数据，要配置好TTL或者定期清理，否则脏数据会一直残留。

**小胖**：这么说来，埋点的代码设计也很重要吧？不能在每个函数里散落一堆`counter.inc()`吧？

**大师**：Python和Java各有优雅的做法。Python可以用装饰器模式封装计时和计数的逻辑，业务代码只需要加一个`@monitor("order_create")`。Java则可以借助AOP切面或者Micrometer自带的`@Timed`注解。不过注意`@Timed`默认依赖AspectJ weaving，在非Spring管理的Bean上不生效，需要额外配置。

## 三、项目实战

### 环境准备

- Python 3.10+，安装依赖：`pip install prometheus-client flask gunicorn celery`
- Java 17+，Spring Boot 3.x，在`pom.xml`中添加`micrometer-registry-prometheus`和`spring-boot-starter-actuator`依赖

---

### Python篇

#### 步骤1P：单进程应用的指标暴露

```python
# app.py
from flask import Flask, request
from prometheus_client import Counter, Histogram, Gauge, generate_latest
import time, random

app = Flask(__name__)

REQUEST_COUNT = Counter('http_requests_total', 'Total HTTP requests',
                        ['method', 'endpoint', 'status'])
REQUEST_DURATION = Histogram('http_request_duration_seconds', 'HTTP request duration',
                             ['method', 'endpoint'],
                             buckets=[0.01, 0.05, 0.1, 0.5, 1, 5])
IN_PROGRESS = Gauge('http_requests_in_progress', 'Requests currently in progress')

@app.route('/metrics')
def metrics():
    return generate_latest(), 200, {'Content-Type': 'text/plain; charset=utf-8'}

@app.route('/api/orders')
@app.route('/api/products')
def handle_request():
    endpoint = request.path
    IN_PROGRESS.inc()
    start = time.time()
    try:
        time.sleep(random.uniform(0.01, 0.5))
        status = '200'
        return {'status': 'ok'}
    except Exception:
        status = '500'
        raise
    finally:
        REQUEST_DURATION.labels(method=request.method,
                                endpoint=endpoint).observe(time.time() - start)
        REQUEST_COUNT.labels(method=request.method,
                             endpoint=endpoint, status=status).inc()
        IN_PROGRESS.dec()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8000)
```

验证：`curl http://localhost:8000/metrics | grep http_`

#### 步骤2P：Gunicorn多进程模式配置

问题：默认每个Gunicorn worker内存空间独立，Prometheus每次scrape随机落到某个worker，数据不准确。

解决方案——启用`prometheus_client`的multiprocess模式：

```bash
# 创建共享目录
mkdir -p /tmp/prometheus_multiproc

# 设置环境变量
export PROMETHEUS_MULTIPROC_DIR=/tmp/prometheus_multiproc

# 启动Gunicorn（不要用--preload，会影响multiprocess模式）
gunicorn -w 4 -b 0.0.0.0:8000 app:app
```

修改metrics端点代码：

```python
from prometheus_client import multiprocess, CollectorRegistry, generate_latest

@app.route('/metrics')
def metrics():
    registry = CollectorRegistry()
    multiprocess.MultiProcessCollector(registry)
    return generate_latest(registry), 200, {'Content-Type': 'text/plain; charset=utf-8'}
```

**工作原理**：每个Worker把指标数据写入`PROMETHEUS_MULTIPROC_DIR`下的独立文件（文件名包含Worker PID），`MultiProcessCollector`读取目录下所有文件，对Counter和Histogram进行跨进程求和聚合后返回。**重要提醒**：multiprocess模式不支持Gauge（跨进程求和没有语义），需要Gauge的场景（如队列长度）请改用Pushgateway。

#### 步骤3P：短任务（Celery）的指标推送

```python
# celery_app.py
from celery import Celery
from prometheus_client import CollectorRegistry, Gauge, push_to_gateway
import time

app = Celery('tasks', broker='redis://localhost:6379/0')

@app.task
def process_report(report_id):
    start_time = time.time()
    status = 'success'
    try:
        time.sleep(30)
    except Exception:
        status = 'failure'

    duration = time.time() - start_time

    try:
        registry = CollectorRegistry()
        g = Gauge('report_task_duration_seconds', 'Task duration',
                  ['task_name', 'status'], registry=registry)
        g.labels(task_name='process_report', status=status).set(duration)
        push_to_gateway('localhost:9091', job='celery_tasks', registry=registry)
    except Exception:
        pass  # push失败不影响业务
```

**核心原则**：短任务用Pushgateway推送（执行完即退出，没法暴露端点）；长服务直接暴露`/metrics`由Prometheus拉取。

---

### Java篇

#### 步骤1J：Spring Boot + Micrometer集成

`pom.xml`添加依赖：

```xml
<dependency>
    <groupId>org.springframework.boot</groupId>
    <artifactId>spring-boot-starter-actuator</artifactId>
</dependency>
<dependency>
    <groupId>io.micrometer</groupId>
    <artifactId>micrometer-registry-prometheus</artifactId>
</dependency>
```

`application.yml`配置：

```yaml
management:
  endpoints:
    web:
      exposure:
        include: health,info,prometheus
  metrics:
    export:
      prometheus:
        enabled: true
    tags:
      application: order-service
```

验证：访问 `http://localhost:8080/actuator/prometheus`，查看默认JVM指标。

#### 步骤2J：自定义业务指标

```java
@RestController
@RequestMapping("/api/orders")
public class OrderController {

    private final MeterRegistry registry;
    private final Counter orderCounter;
    private final Timer orderTimer;

    public OrderController(MeterRegistry registry) {
        this.registry = registry;
        this.orderCounter = Counter.builder("orders_created_total")
            .description("Total orders created")
            .tag("service", "order-service")
            .register(registry);
        this.orderTimer = Timer.builder("order_creation_duration_seconds")
            .description("Order creation duration")
            .publishPercentiles(0.5, 0.95, 0.99)
            .publishPercentileHistogram()
            .register(registry);
    }

    @GetMapping("/create")
    public Order createOrder() {
        return orderTimer.record(() -> {
            orderCounter.increment();
            // 业务逻辑
            return new Order();
        });
    }
}
```

Micrometer的`Timer`自动记录分布，等价于Prometheus的Histogram + Counter组合，自动暴露以下指标：
- `orders_created_total`（Counter）
- `order_creation_duration_seconds_count`（调用次数）
- `order_creation_duration_seconds_sum`（总耗时）
- `order_creation_duration_seconds_bucket`（分桶统计）

#### 步骤3J：JVM指标瘦身

默认Spring Boot会暴露200多个JVM指标。推荐两种瘦身方式：

**全局关闭（粗粒度）**：
```yaml
management:
  metrics:
    enable:
      jvm: false
```

**精细化过滤（推荐）**：
```java
@Configuration
public class MetricsConfig {
    @Bean
    public MeterFilter jvmMetricFilter() {
        return MeterFilter.deny(id ->
            id.getName().startsWith("jvm.gc") ||
            id.getName().startsWith("jvm.buffer") ||
            id.getName().startsWith("jvm.classes")
        );
    }
}
```

建议保留的关键JVM指标：`jvm.memory.used`、`jvm.memory.max`、`jvm.threads.live`，其余按需取舍。

---

### 常见踩坑汇总

| 类型 | 现象 | 原因 | 解决方案 |
|------|------|------|----------|
| **Python** | Gunicorn多Worker下指标数据不准 | 未配置multiprocess模式 | 设置`PROMETHEUS_MULTIPROC_DIR`，挂载`MultiProcessCollector` |
| **Python** | multiprocess模式下Gauge值异常 | Gauge不支持跨进程聚合 | Gauge场景改用Pushgateway单独推送 |
| **Python** | Celery任务偶尔失败 | `push_to_gateway`网络异常未被捕获 | 用try/except包裹push操作 |
| **Java** | Security拦截/actuator | Spring Security默认保护actuator端点 | 配置`SecurityFilterChain`放行`/actuator/**` |
| **Java** | Timer分桶不符合业务 | Micrometer默认bucket范围过大 | 通过`.publishPercentileHistogram()`或自定义SLO |
| **Java** | `@Timed`注解不生效 | 需要AspectJ weaving | 改用`Timer.record()`显式调用，或配置AspectJ |

### 测试验证

```bash
# Python 单进程
curl http://localhost:8000/metrics | grep -E "http_requests_total|http_request_duration"

# Python 多进程（Gunicorn升级后）
curl http://localhost:8000/metrics | grep http_

# Java
curl http://localhost:8080/actuator/prometheus | grep -E "orders_created|order_creation"
```

在Prometheus配置文件中添加对应target，验证数据采集中指标数值是否连续、合理。

## 四、项目总结

### Python vs Go vs Java Exporter对比

| 维度 | Python | Go | Java |
|------|--------|----|------|
| 开发效率 | 高（prometheus_client库API简洁） | 中（需理解client_golang设计） | 高（Micrometer抽象层+自动装配） |
| 运行时性能 | 低（GIL+解释执行） | 高（编译型、原生并发） | 高（JIT编译，但启动慢） |
| 生态成熟度 | 中（multiprocess模式较复杂） | 高（官方支持最优） | 高（Spring Boot开箱即用） |
| 多进程支持 | 需显式配置multiprocess | N/A（Go用goroutine） | 天然支持（JVM单进程多线程） |
| 指标基数控制 | 手动 | 手动 | 需主动裁剪（默认过多） |

### multiprocess模式注意事项

| 要点 | 说明 |
|------|------|
| 环境变量 | 必须设置`PROMETHEUS_MULTIPROC_DIR`，且所有Worker共享同一目录 |
| 指标类型限制 | 仅支持Counter和Histogram，不支持Gauge和Summary |
| `--preload`冲突 | Gunicorn使用`--preload`可能导致multiprocess模式异常，建议关闭 |
| 文件清理 | Worker异常退出后，残留的db文件可能导致数据虚高，建议定期清理 |

### Micrometer → Prometheus 指标映射

| Micrometer类型 | Prometheus类型 | 暴露指标示例 |
|---------------|---------------|-------------|
| Counter | Counter | `orders_total` |
| Gauge | Gauge | `queue_size` |
| Timer | Histogram + Counter | `duration_seconds_count/sum/bucket` |
| DistributionSummary | Summary | `response_size_count/sum` |

### 适用场景

- Python Web应用（Flask/Django/FastAPI）接入Prometheus监控
- Java微服务（Spring Boot）通过Actuator暴露业务指标
- 批处理任务（Celery/K8s CronJob）通过Pushgateway上报
- 数据管道（ETL任务）的执行时长和成功率监控

### 思考题

1. Python multiprocess模式中，如果Worker异常退出，写入`PROMETHEUS_MULTIPROC_DIR`的db文件会怎样？Prometheus采集的数据会受到什么影响？应该如何设计清理策略？
2. Java Micrometer中，如何自定义Histogram的bucket分布？如果想实现"预热后的P99查询"（即查询最近5分钟的P99而非全时间范围），在PromQL层面和Micrometer层面分别应该怎么做？
