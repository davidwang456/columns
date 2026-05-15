# 第19章：Elasticsearch日志可视化

## 1. 项目背景

"ELK那套太重了。我们就想简单看个Nginx访问日志的Top10 URL，结果光Logstash配置就搞了两天，Kibana的查询语法也难用到怀疑人生。如果能直接在Grafana上看ES数据就好了。"

基础架构工程师小林面对公司的"日志即数据"需求，感到进退两难。公司用Elasticsearch集群存储了所有应用的日志数据（每天约500GB），但团队习惯于在Grafana中查看一切。老板的诉求很简单——"同一个平台上既能看到CPU曲线，又能看到错误日志趋势，还能点击跳转查日志原文"。

Grafana对Elasticsearch的支持已经非常成熟。支持Lucene Query和PPL（Piped Processing Language）两种查询语法、支持多种聚合（Terms/Date Histogram/Filters/Geo）、Logs面板可以直接浏览原始日志。特别重要的是GraphQL 10+之后引入的PPL语法——它比Lucene更接近SQL，学习成本大幅降低。

本章将带你打通Grafana→Elasticsearch的"日志到可视化"全链路，让你在Grafana上实现对ES日志的控制台级操作。

## 2. 项目设计

**小胖**（在Kibana Dev Tools控制台中迷茫地输入JSON）：大师，为什么查ES数据要写这么复杂的JSON？你看这：
```json
{"query": {"bool": {"must": [{"match": {"status": "500"}}], "filter": [{"range": {"@timestamp": {"gte": "now-1h"}}}]}}, "aggs": {"by_url": {"terms": {"field": "url.keyword"}}}}
```
我就想知道过去1小时哪些URL返回了500错误，结果得写这么一长串。有没有更简洁的方式？

**大师**（点头）：Elasticsearch的原生查询DSL确实对新手不友好。好消息是Grafana提供了两种更友好的查询方式。

**Lucene语法**：直接写搜索字符串，Elasticsearch背后的查询引擎解析。比如你的需求用Lucene就是：
```
status:500 AND @timestamp:>now-1h
```
简单清晰。

**PPL语法**（推荐）：Grafana 10+支持OpenSearch的PPL。同样的查询：
```
source = nginx-logs* | where status = '500' and @timestamp > 'now-1h' | stats count() by url
```
这语法是不是很像SQL的`SELECT ... WHERE ... GROUP BY`？而且它支持管道操作，一个查询结果传给下一个命令。

**小白**：那Grafana的ES面板有哪些类型？和Kibana比有什么差异？

**大师**：Grafana的ES面板可以分为三大类：

**Logs面板**：专门用来浏览原始日志。类似于Kibana的Discover。支持列选择、高亮、时间线分布图。优势是和Grafana的Dashboard联动——比如你在Table上看到一个异常的URL，点一下就能跳转到Logs面板查看该URL的所有日志原文。

**聚合面板**：Time series/Bar chart/Pie chart/Table等通用面板 + ES的聚合查询。你可以做Terms聚合做TopN分布、Date Histogram聚合做时间趋势、Filters聚合做多条件对比。

**特殊面板**：Geomap面板配合ES的Geo聚合可以做地理位置可视化（如用户来源分布）。

**小胖**：听起来不错。但Lucene和PPL有什么区别？我该选哪个？

**大师**：

Lucene：语法简单，适合简单的全文搜索和过滤。但做复杂聚合时需要切换到"Raw Data"模式写JSON DSL——又回到了你讨厌的状态。

PPL：统一的管道语法，搜索、过滤、聚合、排序全部用管道连接。更适合复杂逻辑。而且PPL的结果可以直接做Transform处理（无需回到JSON DSL）。

我建议：简单搜索用Lucene，复杂分析用PPL。如果团队有SQL经验，PPL学习成本几乎为零。

**小白**：ES数据源配置有什么要点？

**大师**：四个关键点：

1. **版本兼容**：Grafana 11支持Elasticsearch 7.10+和OpenSearch 1.0+。部署前确认版本匹配。
2. **索引模式**：在数据源配置中指定Time field name（通常是`@timestamp`）。这个字段决定了时间范围过滤是否能正确应用。
3. **最大并发请求**：ES集群的连接数有限制，Grafana的`Max concurrent shard requests`不要设太大。
4. **SigV4认证**：如果ES部署在AWS Opensearch Service上，需要配置AWS SigV4认证。

**小胖**：最后一个问题——ES的日志可视化和之前学的Loki有什么不同？我该用哪个？

**大师**：这是个重要的选型问题。

ES：功能最全面，支持全文搜索、复杂聚合、地理位置分析。但资源占用大（Java + 反向索引），运维成本高。

Loki：轻量级，专为日志设计的对象存储优化方案。不索引日志内容，只索引标签。查询时做暴力搜索。资源占用小但全文搜索慢。

简单规则：如果你已经有ES集群，用ES；如果你想新建日志方案且已经有Prometheus，用Loki（下章讲）。ES适合"分析型"日志查询（如"上周错误日志的地域分布"），Loki适合"搜索型"日志查询（如"查一下最近15分钟某个TraceID的所有日志"）。

**技术映射**：Lucene = 关键词搜索（Google搜索框那样的字符串），PPL = 管道命令（像Linux的 `cat | grep | sort`），Logs面板 = 日志阅读器，Aggregation面板 = 日志统计仪表盘。

## 3. 项目实战

**环境准备**

在Docker Compose中添加Elasticsearch：

```yaml
  elasticsearch:
    image: elasticsearch:8.12.0
    container_name: elasticsearch
    environment:
      - discovery.type=single-node
      - xpack.security.enabled=false
      - "ES_JAVA_OPTS=-Xms512m -Xmx512m"
    ports:
      - "9200:9200"
    volumes:
      - es_data:/usr/share/elasticsearch/data

volumes:
  es_data:
```

**步骤一：配置ES数据源**

在Grafana → Data Sources → Add data source → Elasticsearch：

| 参数 | 值 | 说明 |
|------|-----|------|
| URL | `http://elasticsearch:9200` | |
| Access | Server (proxy) | |
| Index name | `[nginx-logs-]YYYY.MM.DD` | 支持日期模式匹配 |
| Pattern | `Daily` | 按天分索引 |
| Time field name | `@timestamp` | 时间字段 |
| Version | `8.0+` | |
| Max concurrent shard requests | `5` | 并发查询分片数 |

Save & test → 确认连接成功。

**步骤二：准备日志数据**

用Python批量写入Nginx格式日志到ES：

```python
from elasticsearch import Elasticsearch, helpers
import random, datetime, uuid

es = Elasticsearch("http://localhost:9200")

urls = ["/api/orders", "/api/users", "/api/products", "/api/payment", "/api/search"]
methods = ["GET", "GET", "GET", "POST", "GET"]
statuses = [200]*80 + [201]*5 + [400]*5 + [404]*5 + [500]*5  # 加权分布
response_times = [random.uniform(0.01, 0.5) for _ in range(80)] + \
                 [random.uniform(1, 5) for _ in range(20)]  # 80%快、20%慢

actions = []
for i in range(1000):
    idx = random.randint(0, len(urls)-1)
    actions.append({
        "_index": f"nginx-logs-{datetime.date.today():%Y.%m.%d}",
        "_source": {
            "@timestamp": (datetime.datetime.utcnow() - 
                          datetime.timedelta(hours=random.randint(0, 24))).isoformat(),
            "remote_addr": f"192.168.1.{random.randint(1, 255)}",
            "method": methods[idx],
            "url": urls[idx],
            "status": random.choice(statuses),
            "response_time": random.choice(response_times),
            "body_bytes_sent": random.randint(100, 50000),
            "http_user_agent": random.choice([
                "Mozilla/5.0 Chrome/120",
                "Mozilla/5.0 Safari/17",
                "python-requests/2.31"
            ]),
            "request_id": str(uuid.uuid4())[:8]
        }
    })

helpers.bulk(es, actions)
print(f"写入 {len(actions)} 条日志")
```

**步骤三：Logs面板——浏览原始日志**

创建Dashboard → Add panel → 面板类型选`Logs`，数据源选Elasticsearch。

Query（Lucene语法）：
```
status:500
```

面板配置：
- **Time**：按@timestamp排序
- **Deduplication**：关闭（看全部日志）
- **Columns**：选择显示的列（`@timestamp | status | method | url | response_time | request_id`）
- **Wrap Lines**：开启（长日志换行显示）

效果：一个交互式日志浏览器，支持时间线分布图、点击展开查看单条日志详情、高亮关键词。

**步骤四：聚合查询——错误日志趋势**

添加新Panel → Time series → Elasticsearch数据源。

Query（Lucene语法）：
```
status:500 OR status:502 OR status:503
```

Metric配置：
- Metric: `Count`
- Group by: `Date Histogram` → Field: `@timestamp` → Interval: `Auto`

效果：过去24小时的5xx错误趋势图。

**步骤五：Terms聚合——Top10 URL错误排行**

添加Bar chart面板。Query（Lucene语法）：
```
status:500
```

Metric配置：
- Metric: `Count`
- Group by: `Terms` → Field: `url.keyword` → Order: `Top` → Size: `10`

效果：出现500错误最多的10个URL排行柱状图。

**步骤六：Filters聚合——状态码分布**

添加Pie chart面板。使用Filters聚合做多条件对比。

Filters：
1. `status:200` → Label: "2xx Success"
2. `status:301 OR status:302` → Label: "3xx Redirect"
3. `status:400 OR status:404` → Label: "4xx Client Error"
4. `status:500 OR status:502 OR status:503` → Label: "5xx Server Error"

Metric: `Count`。

效果：饼图显示各状态码类别的占比。

**步骤七：PPL高级查询实战**

切换到PPL语法（Query类型选PPL）：

```ppl
# 各URL的平均响应时间
source = nginx-logs-*
| where @timestamp >= '$__from' and @timestamp <= '$__to'
| stats avg(response_time) as avg_rt, count() as cnt by url
| sort - avg_rt
| head 5
```

```ppl
# 5xx错误的URL分布（按小时）
source = nginx-logs-*
| where status >= 500
| stats count() as error_count by span(@timestamp, 1h), url
| sort - error_count
```

**步骤八：Dashboard联动——从统计图跳转日志详情**

在步骤五的Bar chart面板中设置Data Link：
- Type: `Dashboard`
- 目标: 同一个Dashboard（内联跳转到Logs面板）
- 传递参数: URL值

在步骤三的Logs面板中添加变量配置，使接收到URL参数时自动过滤：
```
url.keyword:"$url_from_link"
```

**常见坑点**
1. **ES索引模式不匹配**：`[nginx-logs-]YYYY.MM.DD`模式要求索引名严格遵循`nginx-logs-2025.01.15`格式，差一个字符都匹配不上。
2. **Keyword vs Text字段**：聚合（Terms/Histogram）必须在`keyword`类型字段上操作，不要在`text`字段上聚合——会报错。
3. **Time field时区问题**：ES中存储的`@timestamp`是UTC格式，Grafana需要知道这一点。在数据源配置中正确设置Time field name和Interval。
4. **ES内存不足**：大量Date Histogram聚合可能触发ES的`circuit_breaking_exception`。解决：减少Dashboard的面板数量或增加ES内存。
5. **Lucene特殊字符转义**：Lucene中`+ - = && || > < ! ( ) { } [ ] ^ " ~ * ? : \ /`需要转义。如URL中有`/`，查询要写`url:"\/api\/orders"`。

## 4. 项目总结

**Lucene vs PPL 对比**

| 特性 | Lucene | PPL |
|------|--------|-----|
| 语法风格 | 搜索引擎风格 | SQL-like管道风格 |
| 搜索 | ✅ 高效 | ✅ 高效 |
| 过滤 | ✅ 高效 | ✅ 高效 |
| 聚合 | ❌ 需要配合JSON DSL | ✅ 原生支持 |
| 排序 | ❌ 需要JSON DSL | ✅ `\| sort` |
| 学习曲线 | 低 | 中（需要学习命令） |
| Grafana兼容性 | 全版本 | Grafana 10+ |

**优点**
| 特性 | 说明 |
|------|------|
| 全文搜索 | Lucene语法强大且灵活 |
| 可视化丰富 | Logs面板+聚合面板全覆盖 |
| Dashboard联动 | 统计图点击跳日志详情 |
| 多源统一 | 日志和指标在同一Grafana看 |

**缺点**
| 特性 | 说明 |
|------|------|
| ES资源占用 | 比Loki重很多（Java+倒排索引） |
| 查询性能 | 大量数据聚合可能超时 |
| 版本兼容 | ES大版本升级Grafana也需要升级 |

**适用场景**
1. Nginx/Apache访问日志分析：Top URL、错误分布、响应时间分位
2. 应用错误日志聚合：按错误类型/服务/时间聚合
3. 安全审计：用户操作日志的搜索和统计
4. 与指标联动：ES日志 + Prometheus指标联合排查

**注意事项**
1. ES索引按天轮转时，Grafana的Index pattern会自动匹配多个索引
2. 高基数Terms聚合会影响ES集群性能，限制Size值
3. PPL和Lucene不能混写——一个Query只能选一种语法
4. Grafana的ES查询默认超时30s，复杂聚合需调大

**常见踩坑经验**
1. **聚合返回空**：检查字段名是`url.keyword`不是`url`（text字段不支持聚合，必须是keyword子字段）。
2. **Date Histogram不显示**：确认Time field在ES中的mapping是`date`类型。
3. **Dashboard时间范围与ES查询时间不一致**：ES查询默认也使用Dashboard的时间范围，但Lucene语法中`>now-1h`是相对时间，两者叠加可能导致数据翻倍或为空。

**思考题**
1. 如果一个ES集群存储了200+个应用的所有日志（每天1TB），如何在Grafana上设计一套"快速定位问题应用"的Dashboard？
2. Lucene语法中如何实现"查询过去1小时内访问/api/orders接口且响应时间>2秒的所有日志"？
