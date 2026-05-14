# 第13章：Pushgateway——短生命周期任务监控

## 一、项目背景

数据平台团队管理着200多个定时ETL任务，每天凌晨通过Shell脚本配合Crontab自动执行。这些任务从多个业务库抽取数据、清洗转换后写入数据仓库，是公司报表体系的命脉。

直到某个周一早晨，运营总监在群里发火："为什么上周五的营收报表还是空白的？"团队排查了整整两个小时才发现，凌晨3点的一个核心ETL任务静默失败了——Shell脚本因为数据库连接池耗尽直接退出，Crontab照常记录"执行完成"，日志里只留下一行模糊的`connection refused`，淹没在几十万行日志中，没人注意到。数据延迟了整整6个小时，直接影响了管理层的周会决策。

痛定思痛，团队决定引入Prometheus监控所有定时任务。但第一个问题就卡住了：Prometheus采用的是Pull模型——它定期向目标服务发起HTTP请求拉取`/metrics`端点数据。而ETL任务的生命周期通常是"启动→执行→结束→消失"，整个过程可能只有几十秒，远小于Prometheus默认的`scrape_interval`（15秒）。当Prometheus发起采集请求时，任务进程早已退出，`/metrics`端点自然也不复存在。临时为每个短任务起一个HTTP服务暴露指标？太重了，也不现实。

这暴露了Prometheus Pull模型的天然局限：它假设监控目标是一个常驻进程（如Web Server、数据库），而短生命周期任务（CronJob、ETL、备份脚本、CI/CD Pipeline）执行完就消失了，根本等不到Prometheus来"Pull"。

Pushgateway正是为解决这个场景而生的。它的工作原理是：短任务在执行期间主动将指标Push到Pushgateway，Prometheus再按照自己的节奏从Pushgateway Pull走这些指标。注意，Pushgateway的角色不是"队列"，而是一个"指标中转站"——任务把指标写入这里，Prometheus从这里周期性采集。

但Pushgateway有一个著名的"只增不减"特性：一旦指标被Push上去，除非手动调用API删除或重启Pushgateway进程，否则这些指标永久存在。这不是Bug，而是一个深思熟虑的设计选择——Prometheus本身的TSDB也遵循时间序列一旦写入便不可变的原则。然而这意味着，如果一个定时任务每天执行一次，每次用一个不同的`instance`标签推送，一个月后Pushgateway里就会堆积30组旧数据，不仅占用内存，还会让Prometheus的查询结果被过期数据污染。

此外，Pushgateway提供了PUT和POST两种推送方式，它们的语义差异极易被搞混：PUT按分组key覆盖该group下的**所有**指标，POST只替换**同名**指标。用错一种方式，轻则数据叠加，重则整组指标被意外清空。

## 二、剧本式交锋对话

**小胖**：大师，我快被Pushgateway搞疯了！昨晚写了个监控脚本，用PUT方式把ETL任务的耗时推上去，结果今天早上发现同一个group下其他指标全不见了。这玩意怎么跟橡皮擦似的，推一个擦一片？

**大师**：哈哈，你踩中PUT的坑了。先说个更根本的问题——你知不知道为什么ETL这种短任务不能直接用Prometheus的Pull模型采集？

**小胖**：嗯……因为任务跑完进程就没了，Prometheus来Pull的时候已经抓不到了？

**大师**：没错。一个ETL任务可能30秒就跑完了，而Prometheus默认每15秒才采集一次。运气好的话任务正好赶在采集窗口内，运气不好Prometheus永远看不到它。这就是Pull模型的结构性盲区。Pushgateway的作用就是填补这个盲区——任务主动把指标"推到"中转站，Prometheus再从转运站"拉走"。

**小白**：那Pushgateway岂不是类似于消息队列？任务Producer→Pushgateway→Prometheus Consumer？

**大师**：这个类比很危险。Pushgateway**不是**消息队列。消息队列的数据被消费后就移除了，但Pushgateway里的指标Push上去就不会自动消失——这就是它最让人头疼的"只增不减"特性。如果你的ETL任务每天凌晨执行，每次用一个带时间戳的`instance`标签推送，一个月后30组旧数据全堆在Pushgateway里，没人会替你清理。

**小胖**：难怪我昨天测试的时候，Prometheus UI里出现了十几个旧的instance数据！那我把instance固定下来不就解决了？

**小白**：等等，如果instance固定，那每次推送不就覆盖前一次的数据了？这样不就丢掉了历史记录？

**大师**：小白问得好。这里就涉及到PUT和POST的本质区别了。小胖你先回忆一下，你刚才说PUT把同组其他指标擦掉了，这是PUT的预期行为——PUT按`job`和`instance`组成的grouping key，**整体替换**该group下的全部指标。你现在推只带了一个指标，那组里其他的自然没了。

**小胖**（挠头）：那POST呢？

**大师**：POST只替换**同名**指标。比如你用POST推`etl_duration_seconds`，同组里已有的`etl_success_total`完全不受影响。日常脚本监控，用POST更安全。

**小白**：那grouping key到底是什么？我看URL里写了`/job/xxx/instance/yyy`。

**大师**：`job`+`instance`（以及你自定义的其他label）组合起来就是grouping key，可以理解成"分组身份证"。同一个key下的指标彼此覆盖，不同key之间完全独立。比如说你有10台服务器同时跑备份任务，如果都推到`/job/backup/instance/prod`，那最后一台推送的会覆盖前面9台的数据——Pushgateway不是聚合器，它不做求和，只做覆盖。正确的做法是每台服务器用不同的instance值。

**小胖**：那如果我只关心所有备份的汇总结果怎么办？

**大师**：Pushgateway不管聚合。你应该在Prometheus查询层用`sum(backup_success_total)`来聚合。记住一个原则：Pushgateway做存储转发，Prometheus做聚合计算。

**小白**：对了大师，我听说Pushgateway不能扛高频写入？

**大师**：对，Pushgateway是Go写的单进程服务，所有数据存在内存里。每秒推几百次还能应付，但如果每秒成千上万次，不仅响应变慢，还可能OOM。高频指标发布应该直接用Prometheus的Remote Write，或者考虑VictoriaMetrics这类方案。Pushgateway定位很明确——"偶尔Push一次的短任务"。

## 三、项目实战

### 环境准备

在已有的Docker Compose中添加Pushgateway服务：

```yaml
# docker-compose.yml
version: '3'
services:
  prometheus:
    image: prom/prometheus:latest
    ports:
      - "9090:9090"
    volumes:
      - ./prometheus.yml:/etc/prometheus/prometheus.yml

  pushgateway:
    image: prom/pushgateway:latest
    ports:
      - "9091:9091"
```

Prometheus采集配置：

```yaml
# prometheus.yml
global:
  scrape_interval: 15s

scrape_configs:
  - job_name: 'pushgateway'
    honor_labels: true
    static_configs:
      - targets: ['pushgateway:9091']
```

`honor_labels: true`是关键配置。它的含义是：当Push到Pushgateway的指标自身带有label时（比如你在Push时自己指定了`job`和`instance`），优先使用Push数据中的label值，而不是Prometheus采集时自动附加的`job="pushgateway"`和`instance="pushgateway:9091"`。如果不开启此选项，你精心设计的`instance`标签会被采集端的默认值覆盖，导致所有推送的数据都挤在同一个instance下。

启动服务：

```bash
docker-compose up -d
```

### 步骤1：命令行推送第一个指标

```bash
echo "backup_success_total{server=\"db-master\"} 42" | \
  curl --data-binary @- http://localhost:9091/metrics/job/backup/instance/db-master
```

验证数据已到达Pushgateway：

```bash
curl http://localhost:9091/metrics | grep backup_success_total
```

在Prometheus Web UI（http://localhost:9090）中查询`backup_success_total`，确认数据可以从Pushgateway被Prometheus采集到。

### 步骤2：编写完整的备份监控脚本

创建`backup_monitor.sh`：

```bash
#!/bin/bash
set -euo pipefail

PUSHGATEWAY_URL="http://localhost:9091"
JOB_NAME="db_backup"
INSTANCE="db-master-$(date +%Y%m%d-%H%M%S)"

START_TIME=$(date +%s)

# 执行备份
if pg_dump -U postgres mydb > /backup/mydb.sql 2>/tmp/backup_error.log; then
    STATUS="success"
    EXIT_CODE=0
else
    STATUS="failure"
    EXIT_CODE=1
fi

END_TIME=$(date +%s)
DURATION=$((END_TIME - START_TIME))

# 推送指标到Pushgateway（使用POST确保只替换同名指标）
cat <<EOF | curl --data-binary @- "${PUSHGATEWAY_URL}/metrics/job/${JOB_NAME}/instance/${INSTANCE}"
# TYPE backup_duration_seconds gauge
backup_duration_seconds ${DURATION}
# TYPE backup_last_success_timestamp_seconds gauge
backup_last_success_timestamp_seconds ${END_TIME}
# TYPE backup_status gauge
backup_status{status="${STATUS}"} 1
backup_status{status="$([ "$STATUS" = "success" ] && echo "failure" || echo "success")"} 0
EOF

exit ${EXIT_CODE}
```

脚本使用Gauge类型记录任务状态：`backup_duration_seconds`记录耗时，`backup_last_success_timestamp_seconds`记录最后成功的时间戳，`backup_status`用0/1标记成功/失败状态（同时把相反状态置0，避免之前失败的状态残留为1）。

### 步骤3：Prometheus查询与告警

在Prometheus Web UI中执行以下查询：

```promql
# 检查最近备份是否成功
backup_status{status="success"}

# 查看备份耗时
backup_duration_seconds

# 距离上次成功备份的秒数（超过86400秒即24小时则告警）
time() - backup_last_success_timestamp_seconds
```

告警规则配置：

```yaml
# prometheus_rules.yml
groups:
  - name: backup_alerts
    rules:
      - alert: BackupNotExecuted
        expr: time() - backup_last_success_timestamp_seconds > 86400
        for: 5m
        labels:
          severity: critical
        annotations:
          summary: "数据库备份超过24小时未执行"
          description: "实例 {{ $labels.instance }} 的最后一次成功备份在 {{ $value }} 秒前"
```

### 步骤4：PUT vs POST对比实验

先用PUT推送第一个指标：

```bash
echo "test_metric 100" | curl --data-binary @- \
  http://localhost:9091/metrics/job/test_job/instance/test1
```

再用PUT推送第二个不同名的指标到**同一个**job/instance：

```bash
echo "test_metric2 200" | curl --data-binary @- \
  http://localhost:9091/metrics/job/test_job/instance/test1
```

查看Pushgateway，发现`test_metric`消失了——PUT以整个group为单位替换，第二次PUT只推了`test_metric2`，所以`test_metric`被清除。改用POST重试：

```bash
echo "test_metric 100" | curl --data-binary @- \
  http://localhost:9091/metrics/job/test_job/instance/test1

echo "test_metric2 200" | curl -X POST --data-binary @- \
  http://localhost:9091/metrics/job/test_job/instance/test1
```

此时两个指标共存。**日常脚本推荐使用POST**（curl默认是PUT，需要显式加`-X POST`）。

### 步骤5：测试验证

运行备份脚本3次模拟3次备份：

```bash
bash backup_monitor.sh
sleep 2
bash backup_monitor.sh
sleep 2
bash backup_monitor.sh
```

在Prometheus Web UI中验证：
- 查询`backup_status`可以看到3个不同instance的数据
- 查询`changes(backup_status{status="success"}[1h])`观察1小时内成功状态的变化次数
- 注意时间窗口不宜过小，确保Prometheus已采集到不同instance的数据

### 常见踩坑

**坑1：旧instance数据堆积。** Pushgateway不会自动清理过期指标，每次带时间戳instance的推送都会新增一组数据。解决方案：在任务脚本末尾主动删除自身指标，示例如下：

```bash
# 任务完成后清理本次推送的指标
curl -X DELETE "http://localhost:9091/metrics/job/${JOB_NAME}/instance/${INSTANCE}"
```

或者在Prometheus告警中利用`push_time_seconds`（Pushgateway自动生成的时间戳）判断数据新鲜度。

**坑2：高频推送OOM。** Pushgateway所有数据存在内存，适合低频场景（每秒几十到几百次）。高频写入请用Prometheus Remote Write或VictoriaMetrics。

**坑3：honor_labels导致标签冲突。** 开启`honor_labels: true`后，如果Push数据中的`job`或`instance`标签与scrape配置冲突，以Push数据为准。这可能导致Prometheus UI中该指标的`job`不再是`pushgateway`。

**坑4：多Worker覆盖问题。** 多个Worker推到同一grouping key时，后推的覆盖先推的。需要区分时应在instance中加入Worker标识（如`instance="worker-${HOSTNAME}"`）。

## 四、项目总结

### Pushgateway使用决策树

遇到以下问题请自问：**任务运行时间是否远小于scrape_interval？**
- **是** → 使用Pushgateway
- **否** → 任务常驻，直接暴露`/metrics`端点让Prometheus Pull

### PUT vs POST 对比

| 对比维度 | PUT | POST |
|---------|-----|------|
| 替换范围 | 整个group下的所有指标 | 仅同名指标 |
| 使用场景 | 任务只有一个指标，或需要整体重置 | 任务有多个指标，逐一追加 |
| 风险 | 误用会删除同组其他指标 | 旧指标如果不再推送会残留 |
| 推荐指数 | ⭐⭐ | ⭐⭐⭐⭐⭐ |

### 适用场景
- CronJob定时任务监控（ETL、数据备份、日志轮转）
- CI/CD Pipeline执行状态追踪
- 一次性批处理任务的耗时和结果记录
- 无法暴露HTTP端点的嵌入式脚本

### 不适用场景
- 常驻服务（直接使用Prometheus client library暴露/metrics）
- 高频指标发布（每秒100+次写入，Pushgateway扛不住）
- 需要精确聚合的场景（Pushgateway不做聚合，应由Prometheus查询层完成）
- 持久化存储需求（Pushgateway重启后内存数据全部丢失）

### 核心注意事项
1. Pushgateway是内存存储，重启即丢失所有数据，不适合作为长期指标存储
2. 务必建立过期指标清理机制：利用`push_time_seconds`判断数据新鲜度，或任务结束时主动DELETE
3. `honor_labels: true`要谨慎配置，理解其标签优先级语义
4. 同一grouping key内，多个推送者会互相覆盖——设计instance命名规范很重要

### 踩坑案例回顾
- **案例一**：某团队将CI Pipeline每次构建都用一个UUID做instance推送到Pushgateway，三个月后Pushgateway内存占用超过8GB，Prometheus查询变得极其缓慢——根本原因是没有设置清理策略，导致几十万个过期时间序列堆积。解决方法是利用`push_time_seconds`在Prometheus侧过滤掉超过24小时的旧数据。
- **案例二**：一个同事写了两个备份脚本A和B，都推到`/job/backup/instance/prod`，A用PUT，B也用了PUT，结果B推送后A的所有指标丢失——原因是PUT按group整体替换，两个脚本互不知情地使用了同一个grouping key。改POST后问题解决。
- **案例三**：某团队配置了Pushgateway的scrape job但忘了设置`honor_labels: true`，结果Prometheus中所有Push过来的指标都被打上了`job="pushgateway"`而不是推送时指定的job名，告警规则完全失效——排查了整整一个下午才发现是这个配置缺失。

### 思考题
1. **如何设计一个自动清理Pushgateway过期指标的方案？** 提示：Pushgateway会给每个推送的指标自动添加`push_time_seconds`标签（Unix时间戳）。你可以结合这个标签，在Prometheus的recording rule或告警规则中判断数据新鲜度，或者编写一个定期脚本，遍历Pushgateway的`/metrics`端点，删除`push_time_seconds`超过阈值的指标组。

2. **如果100个Worker同时推送指标到同一个`/job/batch`，如何保证每个Worker的数据独立不被覆盖？** 提示：利用grouping key的设计——为每个Worker分配唯一的`instance`标签（如`instance="worker-${HOSTNAME}-${PID}"`），这样每个Worker拥有独立的指标分组，互不干扰。如果需要在查询时汇总，使用PromQL的`sum by(job)`聚合即可。
