# 第33章：自定义 Writer 插件开发实战

## 1. 项目背景

某搜索推荐团队的 Elasticsearch 集群承载着全站的商品搜索倒排索引——13 亿条商品文档，分布在 60 个数据节点上。每晚凌晨，数据仓库产出当天的商品全量更新数据（约 5000 万条），需要批量写入 ES。

当前方案是 DataX 将 MySQL 数据导出为 CSV，再用 Logstash 导入 ES。问题在于：

1. **链路太长**：MySQL → DataX → CSV 文件 → Logstash → ES，每一步都可能失败
2. **批量写入性能差**：Logstash 的 ES output 默认一条一条 index——5000 万条需要 4 小时
3. **错误处理弱**：写入失败的文档被 Logstash DLQ 吞掉，无法与 DataX 的脏数据体系统一管理
4. **动态索引不支持**：商品数据需要按日期分索引（`products_20260506`），Logstash 配置需要每天手动改

架构师拍板：**开发一个 `elasticsearchwriter` 插件**，直接在 DataX 内部完成 ES 写入——省去 CSV 中间落盘和 Logstash 中转。要求支持动态索引名、_bulk 批量写入、自动重试、脏数据归集。

本章从 Writer 插件架构入手，手把手构建一个完整的 Elasticsearch Writer 插件。

## 2. 项目设计——剧本式交锋对话

**（凌晨 2:00，ES 集群的监控大盘上，写入延迟从 50ms 飙升到 8000ms）**

**小胖**：（揉着眼睛）我凌晨 3 点发版又要延期了——商品同步脚本还在跑，5000 万条要写到 4 点。ES 的 `_bulk` API 一次最多写 1000 条，我有 50000 次 `_bulk` 请求……

**小白**：（在自己的终端上 grep ES 日志）你是一次发一条 `_bulk` 还是并发发？

**小胖**：单线程啊——Logstash 默认只有一个 pipeline worker。

**小白**：这就是问题。DataX 的多 channel 并发优势你完全没用到。如果直接写一个 ES Writer 插件，DataX 的 TaskGroup 会自动把 5000 万条分给 N 个 Task 并发写——你只需要关心单个 Task 里面的批量写入逻辑。

**大师**：（走过来）Writer 插件的结构跟 Reader 对称，但职责更重——它要负责"可靠地把数据送到目标端"。核心生命周期更长：

```
Reader Job:    init → preCheck → split → post
Writer Job:    init → writerPreCheck → prepare → split → post → destroy
Reader Task:   init → prepare → startRead(RecordSender) → post
Writer Task:   init → prepare → startWrite(RecordReceiver) → post → destroy
```

**Writer 独有的关键方法**：

**`writerPreCheck()`**（Job 级别）——在正式写入前做一次目标系统连通性检查。比如 ES Writer 可以在这里调一次 `GET /_cluster/health`，确保集群是 green/yellow 状态，如果是 red 状态直接 fail-fast。

**`supportFailOver()`**（Job 级别）——返回 `true` 表示当某些 Task 失败时，Writer 支持重试机制。对于 ES 这种幂等写入的场景（`_bulk` 天然幂等），应该返回 `true`。

**`startWrite(RecordReceiver receiver)`**（Task 级别）——这是核心。Receiver 是 Channel 的输出端，调用 `receiver.getFromReader()` 从 Channel 拉一条 Record，处理完后写入目标系统。循环终止条件是 `receiver.getFromReader() == null`（表示 Channel 没有更多数据了）。

**技术映射**：Reader = 进货卡车把货送进仓库（Channel），Writer = 出库搬运工把货从仓库搬到货架（目标端）。`startRead` 是卡车卸货（→ Channel），`startWrite` 是搬运工码货（← Channel）。中间 Channel 的容量（capacity）决定了仓库的缓冲深度。

**小胖**：那我最关心的批量写入怎么实现？总不能 5000 万条按一条一条发 HTTP 请求吧？

**大师**：ES Writer 的批量写入是一个"本地缓冲 → 攒够一批 → 批量提交"的循环：

```java
@Override
public void startWrite(RecordReceiver receiver) {
    List<Record> buffer = new ArrayList<>(batchSize);      // 本地缓冲
    Record record;
    
    while ((record = receiver.getFromReader()) != null) {
        buffer.add(record);
        
        if (buffer.size() >= batchSize) {
            flushBulk(buffer);      // 攒满一批，批量提交
            buffer.clear();
        }
    }
    
    if (!buffer.isEmpty()) {
        flushBulk(buffer);          // 剩余数据兜底提交
    }
}
```

**`batchSize` 的取值**：ES 官方建议 `_bulk` 单次提交 5~15MB（约 1000~5000 条中等大小的文档）。设太小 → HTTP 连接开销大、设太大 → 单次请求可能超时。

**小白**：（追问）那动态索引名怎么实现？比如今天的索引叫 `products_20260506`，明天叫 `products_20260507`。

**大师**：在 ES Writer 的配置中支持占位符：

```json
{
    "index": "products_${date}",
    "indexDateFormat": "yyyyMMdd",
    "indexDateOffset": 0
}
```

Writer 内部在 `Task.init()` 时用 `SimpleDateFormat` 解析当前日期并替换占位符——这样就实现了动态索引。如果数据中需要自定义路由（如 `_routing`），可以在 column 配置中指定 `routingColumn`。

**小胖**：（举手）还有自动重试呢？ES 集群偶尔会有节点 GC 抖动导致写入超时。

**大师**：在 `flushBulk()` 方法中加 retry 逻辑：

```java
private void flushBulk(List<Record> buffer) {
    int maxRetries = 3;
    int retryDelayMs = 1000;
    
    for (int attempt = 0; attempt < maxRetries; attempt++) {
        try {
            String bulkBody = buildBulkBody(buffer);
            HttpResponse resp = esClient.bulk(bulkBody);
            
            if (resp.isOK()) return;  // 成功
            
            // 如果是可重试的错误（429 Too Many Requests / 503）
            if (isRetryable(resp.getStatusCode())) {
                Thread.sleep(retryDelayMs * (attempt + 1));  // 退避
                continue;
            }
            // 不可重试的错误（如 mapping 冲突）→ 记脏数据
            markDirtyRecords(buffer, resp.getErrorMessage());
            return;
        } catch (Exception e) {
            if (attempt == maxRetries - 1) {
                markDirtyRecords(buffer, e.getMessage());
            }
        }
    }
}
```

**小白**：（追问）脏数据收集呢？DataX 怎么知道 Writer 写入失败了？

**大师**：Writer 调用 `getTaskPluginCollector().collectDirtyRecord(record, errorMsg)` 将失败的 Record 标记为脏数据。DataX 引擎层会汇总所有 Task 的脏数据量，跟 Job 配置中 `errorLimit` 的阈值比较——超过就标记 Job 失败。

## 3. 项目实战

### 3.1 步骤一：创建 ES Writer Maven 项目

**目标**：建立标准的 DataX Writer 插件项目结构。

```powershell
New-Item -ItemType Directory -Path "elasticsearchwriter\src\main\java\com\example\datax\plugin\writer\elasticsearch" -Force
New-Item -ItemType Directory -Path "elasticsearchwriter\src\main\resources" -Force
```

**项目结构**：

```
elasticsearchwriter/
├── pom.xml
├── src/main/
│   ├── java/com/example/datax/plugin/writer/elasticsearch/
│   │   ├── ESWriter.java             # Writer 主类
│   │   ├── ESConstant.java           # 参数 Key 常量
│   │   ├── ESClient.java             # ES REST 客户端封装
│   │   └── ESWriterErrorCode.java    # 错误码枚举
│   └── resources/
│       └── plugin.json
```

**`pom.xml`**：

```xml
<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0"
         xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
         xsi:schemaLocation="http://maven.apache.org/POM/4.0.0
         http://maven.apache.org/xsd/maven-4.0.0.xsd">
    <modelVersion>4.0.0</modelVersion>

    <groupId>com.example.datax</groupId>
    <artifactId>elasticsearchwriter</artifactId>
    <version>1.0.0</version>

    <dependencies>
        <dependency>
            <groupId>com.alibaba.datax</groupId>
            <artifactId>datax-common</artifactId>
            <version>3.0.0</version>
            <scope>provided</scope>
        </dependency>

        <!-- ES 7.x High Level REST Client -->
        <dependency>
            <groupId>org.elasticsearch.client</groupId>
            <artifactId>elasticsearch-rest-high-level-client</artifactId>
            <version>7.17.10</version>
        </dependency>

        <dependency>
            <groupId>org.apache.httpcomponents</groupId>
            <artifactId>httpasyncclient</artifactId>
            <version>4.1.5</version>
        </dependency>

        <dependency>
            <groupId>com.alibaba</groupId>
            <artifactId>fastjson</artifactId>
            <version>1.2.83</version>
        </dependency>

        <dependency>
            <groupId>org.apache.commons</groupId>
            <artifactId>commons-lang3</artifactId>
            <version>3.12.0</version>
        </dependency>

        <dependency>
            <groupId>org.slf4j</groupId>
            <artifactId>slf4j-api</artifactId>
            <version>1.7.36</version>
            <scope>provided</scope>
        </dependency>
    </dependencies>
</project>
```

### 3.2 步骤二：定义常量与错误码

**`ESConstant.java`**：

```java
package com.example.datax.plugin.writer.elasticsearch;

public class ESConstant {
    // 连接配置
    public static final String KEY_ENDPOINT         = "endpoint";
    public static final String KEY_USERNAME         = "username";
    public static final String KEY_PASSWORD         = "password";
    public static final String KEY_CONNECT_TIMEOUT  = "connectTimeout";
    public static final String KEY_SOCKET_TIMEOUT   = "socketTimeout";

    // 索引配置
    public static final String KEY_INDEX            = "index";
    public static final String KEY_TYPE             = "type";         // ES 7.x 已废弃
    public static final String KEY_INDEX_DATE_FMT   = "indexDateFormat";
    public static final String KEY_INDEX_DATE_OFFSET = "indexDateOffset";

    // 写入配置
    public static final String KEY_COLUMN           = "column";
    public static final String KEY_BATCH_SIZE       = "batchSize";
    public static final String KEY_MAX_RETRIES      = "maxRetries";
    public static final String KEY_TRY_INTERVAL     = "tryInterval";
    public static final String KEY_ID_COLUMN        = "idColumn";      // _id
    public static final String KEY_ROUTING_COLUMN   = "routingColumn"; // _routing

    // 默认值
    public static final int    DEFAULT_BATCH_SIZE    = 1000;
    public static final int    DEFAULT_MAX_RETRIES   = 3;
    public static final int    DEFAULT_TRY_INTERVAL  = 1000;
    public static final int    DEFAULT_CONNECT_TIMEOUT = 5000;
    public static final int    DEFAULT_SOCKET_TIMEOUT  = 60000;
}
```

**`ESWriterErrorCode.java`**：

```java
package com.example.datax.plugin.writer.elasticsearch;

import com.alibaba.datax.common.spi.ErrorCode;

public enum ESWriterErrorCode implements ErrorCode {
    REQUIRED_PARAM_MISSING("ESWriter-01", "必须参数缺失"),
    CLUSTER_UNHEALTHY("ESWriter-02", "ES 集群状态异常"),
    INDEX_CREATE_FAILED("ESWriter-03", "索引创建失败"),
    BULK_WRITE_FAILED("ESWriter-04", "批量写入失败"),
    MAPPING_CONFLICT("ESWriter-05", "文档 Mapping 冲突"),
    NETWORK_ERROR("ESWriter-06", "网络连接异常");

    private final String code;
    private final String description;

    ESWriterErrorCode(String code, String description) {
        this.code = code;
        this.description = description;
    }

    @Override
    public String getCode() { return code; }

    @Override
    public String getDescription() { return description; }
}
```

### 3.3 步骤三：ES 客户端封装

**`ESClient.java`**（核心方法）：

```java
package com.example.datax.plugin.writer.elasticsearch;

import com.alibaba.datax.common.exception.DataXException;
import org.apache.http.HttpHost;
import org.apache.http.auth.AuthScope;
import org.apache.http.auth.UsernamePasswordCredentials;
import org.apache.http.client.CredentialsProvider;
import org.apache.http.impl.client.BasicCredentialsProvider;
import org.elasticsearch.action.bulk.BulkRequest;
import org.elasticsearch.action.bulk.BulkResponse;
import org.elasticsearch.action.index.IndexRequest;
import org.elasticsearch.client.*;
import org.elasticsearch.cluster.health.ClusterHealthStatus;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.io.IOException;
import java.util.List;
import java.util.Map;

public class ESClient {
    private static final Logger LOG = LoggerFactory.getLogger(ESClient.class);
    
    private RestHighLevelClient client;
    private String index;

    public ESClient(String endpoint, String username, String password,
                    int connectTimeout, int socketTimeout) {
        HttpHost host = HttpHost.create(endpoint);
        RestClientBuilder builder = RestClient.builder(host)
            .setRequestConfigCallback(rc -> rc
                .setConnectTimeout(connectTimeout)
                .setSocketTimeout(socketTimeout));

        if (username != null && !username.isEmpty()) {
            CredentialsProvider cp = new BasicCredentialsProvider();
            cp.setCredentials(AuthScope.ANY,
                new UsernamePasswordCredentials(username, password));
            builder.setHttpClientConfigCallback(hcb ->
                hcb.setDefaultCredentialsProvider(cp));
        }

        this.client = new RestHighLevelClient(builder);
    }

    public boolean checkClusterHealth() throws IOException {
        ClusterHealthStatus status = client.cluster()
            .health(RequestOptions.DEFAULT)
            .getStatus();
        LOG.info("ES cluster health: {}", status);
        // red 状态 = 不可写入
        return status != ClusterHealthStatus.RED;
    }

    public BulkResponse bulkWrite(List<Map<String, Object>> documents,
                                  String indexBase, String idField,
                                  String routingField) throws IOException {
        BulkRequest bulkRequest = new BulkRequest();
        String resolvedIndex = resolveIndexName(indexBase);

        for (Map<String, Object> doc : documents) {
            IndexRequest indexRequest = new IndexRequest(resolvedIndex);
            
            if (idField != null && doc.containsKey(idField)) {
                indexRequest.id(String.valueOf(doc.get(idField)));
            }
            if (routingField != null && doc.containsKey(routingField)) {
                indexRequest.routing(String.valueOf(doc.get(routingField)));
            }
            
            indexRequest.source(doc);
            bulkRequest.add(indexRequest);
        }

        return client.bulk(bulkRequest, RequestOptions.DEFAULT);
    }

    public boolean isRetryable(int statusCode) {
        // 429 (too many requests) → 退避重试
        // 503 (service unavailable) → 集群抖动
        return statusCode == 429 || statusCode == 503 || statusCode >= 500;
    }

    public void close() throws IOException {
        if (client != null) {
            client.close();
        }
    }

    private String resolveIndexName(String indexBase) {
        if (indexBase.contains("${date}")) {
            java.text.SimpleDateFormat sdf = new java.text.SimpleDateFormat("yyyyMMdd");
            return indexBase.replace("${date}", sdf.format(new java.util.Date()));
        }
        return indexBase;
    }
}
```

### 3.4 步骤四：实现 Writer 主类——Job 内部类

**`ESWriter.java`——Job 内部类**：

```java
package com.example.datax.plugin.writer.elasticsearch;

import com.alibaba.datax.common.plugin.TaskPluginCollector;
import com.alibaba.datax.common.spi.Writer;
import com.alibaba.datax.common.util.Configuration;
import com.alibaba.datax.common.element.Record;
import com.alibaba.datax.common.exception.DataXException;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.util.ArrayList;
import java.util.List;

public class ESWriter extends Writer {

    public static class Job extends Writer.Job {
        private static final Logger LOG = LoggerFactory.getLogger(Job.class);
        private Configuration originalConfig;

        @Override
        public void init() {
            this.originalConfig = super.getPluginJobConf();

            // 校验必填参数
            String endpoint = originalConfig.getString(ESConstant.KEY_ENDPOINT);
            if (endpoint == null || endpoint.isEmpty()) {
                throw DataXException.asDataXException(
                    ESWriterErrorCode.REQUIRED_PARAM_MISSING,
                    "endpoint 参数不能为空");
            }

            List<Configuration> columns = originalConfig.getListConfiguration(
                ESConstant.KEY_COLUMN);
            if (columns == null || columns.isEmpty()) {
                throw DataXException.asDataXException(
                    ESWriterErrorCode.REQUIRED_PARAM_MISSING,
                    "column 参数不能为空");
            }

            String index = originalConfig.getString(ESConstant.KEY_INDEX);
            if (index == null || index.isEmpty()) {
                throw DataXException.asDataXException(
                    ESWriterErrorCode.REQUIRED_PARAM_MISSING,
                    "index 参数不能为空");
            }

            LOG.info("ESWriter Job init: endpoint={}, index={}, columns={}",
                endpoint, index, columns.size());
        }

        @Override
        public void prepare() {
            String endpoint = originalConfig.getString(ESConstant.KEY_ENDPOINT);
            String username = originalConfig.getString(ESConstant.KEY_USERNAME);
            String password = originalConfig.getString(ESConstant.KEY_PASSWORD);
            int connectTimeout = originalConfig.getInt(
                ESConstant.KEY_CONNECT_TIMEOUT, ESConstant.DEFAULT_CONNECT_TIMEOUT);
            int socketTimeout = originalConfig.getInt(
                ESConstant.KEY_SOCKET_TIMEOUT, ESConstant.DEFAULT_SOCKET_TIMEOUT);

            ESClient client = new ESClient(endpoint, username, password,
                connectTimeout, socketTimeout);
            try {
                boolean healthy = client.checkClusterHealth();
                if (!healthy) {
                    throw DataXException.asDataXException(
                        ESWriterErrorCode.CLUSTER_UNHEALTHY,
                        "ES 集群 RED 状态，不可写入");
                }
                LOG.info("ESWriter Job prepare: cluster healthy");
            } catch (Exception e) {
                throw DataXException.asDataXException(
                    ESWriterErrorCode.NETWORK_ERROR,
                    "ES 集群连接失败: " + e.getMessage());
            } finally {
                try { client.close(); } catch (Exception ignored) {}
            }
        }

        @Override
        public List<Configuration> split(int adviceNumber) {
            List<Configuration> taskConfigs = new ArrayList<>();
            for (int i = 0; i < adviceNumber; i++) {
                taskConfigs.add(this.originalConfig.clone());
            }
            return taskConfigs;
        }

        @Override
        public void post() {
            LOG.info("ESWriter Job post: all tasks completed");
        }

        @Override
        public void destroy() {
            LOG.info("ESWriter Job destroy");
        }
    }
```

### 3.5 步骤五：实现 Task 内部类——startWrite 主循环

**`ESWriter.java`——Task 内部类**：

```java
    public static class Task extends Writer.Task {
        private static final Logger LOG = LoggerFactory.getLogger(Task.class);

        private ESClient esClient;
        private List<Configuration> columnMetas;
        private String index;
        private int batchSize;
        private int maxRetries;
        private int tryInterval;
        private String idColumn;
        private String routingColumn;

        @Override
        public void init() {
            Configuration taskConfig = super.getPluginJobConf();

            this.columnMetas = taskConfig.getListConfiguration(ESConstant.KEY_COLUMN);
            this.index = taskConfig.getString(ESConstant.KEY_INDEX);
            this.batchSize = taskConfig.getInt(
                ESConstant.KEY_BATCH_SIZE, ESConstant.DEFAULT_BATCH_SIZE);
            this.maxRetries = taskConfig.getInt(
                ESConstant.KEY_MAX_RETRIES, ESConstant.DEFAULT_MAX_RETRIES);
            this.tryInterval = taskConfig.getInt(
                ESConstant.KEY_TRY_INTERVAL, ESConstant.DEFAULT_TRY_INTERVAL);
            this.idColumn = taskConfig.getString(ESConstant.KEY_ID_COLUMN);
            this.routingColumn = taskConfig.getString(ESConstant.KEY_ROUTING_COLUMN);

            String endpoint = taskConfig.getString(ESConstant.KEY_ENDPOINT);
            String username = taskConfig.getString(ESConstant.KEY_USERNAME);
            String password = taskConfig.getString(ESConstant.KEY_PASSWORD);
            int connectTimeout = taskConfig.getInt(
                ESConstant.KEY_CONNECT_TIMEOUT, ESConstant.DEFAULT_CONNECT_TIMEOUT);
            int socketTimeout = taskConfig.getInt(
                ESConstant.KEY_SOCKET_TIMEOUT, ESConstant.DEFAULT_SOCKET_TIMEOUT);

            this.esClient = new ESClient(endpoint, username, password,
                connectTimeout, socketTimeout);

            LOG.info("ESWriter Task init: index={}, batchSize={}, maxRetries={}",
                index, batchSize, maxRetries);
        }

        @Override
        public void startWrite(RecordReceiver receiver) {
            List<Record> buffer = new ArrayList<>(batchSize);
            List<Map<String, Object>> docBuffer = new ArrayList<>(batchSize);
            long totalWritten = 0;
            long totalDirty = 0;

            Record record;
            while ((record = receiver.getFromReader()) != null) {
                buffer.add(record);

                Map<String, Object> doc = buildDocument(record);
                docBuffer.add(doc);

                if (buffer.size() >= batchSize) {
                    int dirty = flushBulk(buffer, docBuffer);
                    totalWritten += (buffer.size() - dirty);
                    totalDirty += dirty;
                    buffer.clear();
                    docBuffer.clear();
                }
            }

            // 兜底提交剩余数据
            if (!buffer.isEmpty()) {
                int dirty = flushBulk(buffer, docBuffer);
                totalWritten += (buffer.size() - dirty);
                totalDirty += dirty;
            }

            LOG.info("ESWriter Task finished: written={}, dirty={}", totalWritten, totalDirty);
        }

        private Map<String, Object> buildDocument(Record record) {
            Map<String, Object> doc = new java.util.LinkedHashMap<>();
            for (int i = 0; i < columnMetas.size(); i++) {
                Configuration colMeta = columnMetas.get(i);
                String colName = colMeta.getString("name");
                String colType = colMeta.getString("type", "string");

                com.alibaba.datax.common.element.Column col = record.getColumn(i);
                if (col == null || col.getRawData() == null) {
                    doc.put(colName, null);
                    continue;
                }

                switch (colType.toLowerCase()) {
                    case "long":
                        doc.put(colName, col.asLong());
                        break;
                    case "double":
                        doc.put(colName, col.asDouble());
                        break;
                    case "boolean":
                        doc.put(colName, col.asBoolean());
                        break;
                    case "date":
                        doc.put(colName, col.asDate());
                        break;
                    case "string":
                    default:
                        doc.put(colName, col.asString());
                        break;
                }
            }
            return doc;
        }

        private int flushBulk(List<Record> records, List<Map<String, Object>> docs) {
            int dirtyCount = 0;

            for (int attempt = 0; attempt < maxRetries; attempt++) {
                try {
                    BulkResponse response = esClient.bulkWrite(
                        docs, index, idColumn, routingColumn);

                    if (!response.hasFailures()) {
                        return 0;  // 全部成功
                    }

                    // 逐个检查失败的文档
                    for (org.elasticsearch.action.bulk.BulkItemResponse item : response.getItems()) {
                        if (item.isFailed()) {
                            int idx = item.getItemId();
                            String errorMsg = item.getFailureMessage();
                            String errorType = item.getFailure().getStatus().name();

                            if (esClient.isRetryable(item.getFailure().getStatus().getStatus())) {
                                // 可重试的失败（429/503），重试这个文档
                                docs.set(idx, null); // 标记已处理
                                continue;
                            }

                            // 不可重试——记脏数据
                            getTaskPluginCollector().collectDirtyRecord(
                                records.get(idx),
                                String.format("ES write failed: %s - %s", errorType, errorMsg));
                            dirtyCount++;
                        }
                    }

                    // 如果全部脏数据都是可重试的 → 退避后重试
                    if (dirtyCount == 0) {
                        long delay = tryInterval * (attempt + 1);
                        LOG.info("Retrying bulk write in {} ms (attempt {}/{})",
                            delay, attempt + 1, maxRetries);
                        Thread.sleep(delay);
                        continue;
                    }
                    return dirtyCount;

                } catch (Exception e) {
                    LOG.warn("Bulk write attempt {} failed: {}", attempt + 1, e.getMessage());
                    if (attempt == maxRetries - 1) {
                        // 最后一次重试仍失败 → 全部记脏数据
                        for (Record record : records) {
                            getTaskPluginCollector().collectDirtyRecord(
                                record, "Bulk write exhausted retries: " + e.getMessage());
                        }
                        return records.size();
                    }
                    try {
                        Thread.sleep(tryInterval * (attempt + 1));
                    } catch (InterruptedException ignored) {}
                }
            }
            return dirtyCount;
        }

        @Override
        public void destroy() {
            if (esClient != null) {
                try { esClient.close(); } catch (Exception ignored) {}
            }
        }
    }
}
```

### 3.6 步骤六：plugin.json 与使用示例

**`plugin.json`**：

```json
{
    "name": "elasticsearchwriter",
    "class": "com.example.datax.plugin.writer.elasticsearch.ESWriter",
    "description": "Elasticsearch Writer Plugin - bulk write with dynamic index and retry support",
    "developer": "data-platform"
}
```

**编译部署**：

```powershell
mvn clean package -DskipTests
# 将 jar 和 libs 部署到 $DATAX_HOME/plugin/writer/elasticsearchwriter/
```

**DataX Job 配置示例（MySQL → ES 全量同步）**：

```json
{
    "job": {
        "content": [{
            "reader": {
                "name": "mysqlreader",
                "parameter": {
                    "username": "root",
                    "password": "root",
                    "column": ["product_id", "title", "category", "price", "shop_id", "update_time"],
                    "splitPk": "product_id",
                    "connection": [{
                        "table": ["products"],
                        "jdbcUrl": ["jdbc:mysql://localhost:3306/shop"]
                    }]
                }
            },
            "writer": {
                "name": "elasticsearchwriter",
                "parameter": {
                    "endpoint": "http://es-node01:9200",
                    "username": "elastic",
                    "password": "changeme",
                    "index": "products_${date}",
                    "indexDateFormat": "yyyyMMdd",
                    "batchSize": 2000,
                    "maxRetries": 3,
                    "tryInterval": 2000,
                    "idColumn": "product_id",
                    "routingColumn": "shop_id",
                    "column": [
                        {"name": "product_id", "type": "long"},
                        {"name": "title", "type": "string"},
                        {"name": "category", "type": "string"},
                        {"name": "price", "type": "double"},
                        {"name": "shop_id", "type": "long"},
                        {"name": "update_time", "type": "date"}
                    ],
                    "connectTimeout": 5000,
                    "socketTimeout": 60000
                }
            }
        }],
        "setting": {
            "speed": {"channel": 8},
            "errorLimit": {"record": 100, "percentage": 0.001}
        }
    }
}
```

**执行命令与日志**：

```powershell
python datax.py jobs/mysql_to_es.json

# 日志关键行：
# ESWriter Job init: endpoint=http://es-node01:9200, index=products_20260506, columns=6
# ESWriter Job prepare: cluster healthy
# ESWriter Task[0] startWrite: writing to index products_20260506
# ESWriter Task[0] finished: written=6250000, dirty=3
# ...
# Job finished. Total read: 50,000,000, total write: 49,999,997, dirty: 3
```

**ES 验证命令**：

```powershell
# 检查索引是否存在
curl -u elastic:changeme "http://es-node01:9200/products_20260506/_count"
# 输出: {"count":49999997,"_shards":{"total":5,"successful":5,"failed":0}}

# 验证文档内容
curl -u elastic:changeme "http://es-node01:9200/products_20260506/_search?size=1&pretty"
```

### 3.7 可能遇到的坑及解决方法

**坑1：ES 集群写压力过大触发 CircuitBreaker**

channel=8 时 8 个 Task 同时 `_bulk`，ES 的内存压力激增——可能触发 `parent circuit breaker trip`。

```
报错: [parent] Data too large, data for [<http_request>] would be [2.3gb]
解决:
1. 降低 channel 数（4~6）
2. 增大 ES 的 indices.breaker.total.limit（默认 JVM heap 的 95%）
3. 增大 JVM heap（建议不超过 32G，超过后指针压缩失效）
```

**坑2：动态索引 `products_${date}` 中 `${date}` 在多个 Task 间计算不一致**

如果在 `flushBulk()` 中计算日期，Task 1 在 23:59:59 提交的 bulk 写入了 `products_20260505`，Task 2 在 00:00:01 提交的写入了 `products_20260506`——同一次任务的数据分到了两个索引里。

```
解决: 在 Job.init() 中预先计算索引名，写入 Configuration 传给所有 Task
     String resolvedIndex = resolveIndex(originalConfig);
     originalConfig.set(ESConstant.KEY_INDEX_RESOLVED, resolvedIndex);
```

**坑3：ES 7.x 移除了 `_type` 但旧版客户端仍要求设置**

```
报错: Types are deprecated in 7.x
解决: ES 7.x 的 IndexRequest 不需要设置 type 参数，直接 new IndexRequest(indexName) 即可
```

**坑4：bulk 请求体过大导致 OOM**

如果一个 `batchSize=10000` 且每条文档 5KB，buildBulkBody 生成的请求体 = 50MB——不仅占用 ES 节点内存，DataX 的 JVM 也可能 OOM。

```
解决: 
1. batchSize 保持 1000~3000
2. 加一个 maxBulkSize 参数（以 MB 为单位），buffer 大小按字节数限制而非条数
```

## 4. 项目总结

### 4.1 Writer 插件开发要点对比

| 特性 | 标准 Writer（如 mysqlwriter） | ES Writer（本章实现） |
|------|---------------------------|---------------------|
| 写入接口 | JDBC executeBatch() | ES _bulk API |
| 批量提交 | batchSize (PreparedStatement batch) | buffer.size() >= batchSize |
| 幂等保证 | REPLACE INTO / ON DUPLICATE KEY | IndexRequest(id=xxx) 天然幂等 |
| 失败重试 | JDBC 异常 → 脏数据 | 429/503 → 退避重试, 4xx → 脏数据 |
| 连接管理 | DriverManager.getConnection() | ES RestHighLevelClient |
| 脏数据归集 | getTaskPluginCollector() | 同左 |

### 4.2 优点

1. **并发批量写入**：天然利用 DataX 的 TaskGroup 并发，N 个 Task 同时 `_bulk`
2. **动态索引支持**：`${date}` 占位符自动解析，无需每天手动改配置
3. **智能重试**：区分可重试（429/503）和不可重试（400/403）错误，退避策略
4. **脏数据归集**：写入失败的文档统一走 DataX 的 errorLimit 体系
5. **跳过中间件**：省去 Logstash/Filebeat 中转，链路更短

### 4.3 缺点

1. **依赖 ES Java Client 版本**：ES 7.x 与 8.x 的 Client API 不兼容，需要维护多个版本
2. **无事务支持**：bulk 部分失败时，无法原子回滚已成功的文档（ES 本身不支持事务）
3. **动态 index mapping**：需提前创建索引模板（index template），否则自动映射可能不符合预期
4. **JVM 堆压力**：Java Client 的 BulkRequest 在 JVM 中构建完整的 JSON 请求体（含大文档时压力大）

### 4.4 适用场景

1. 搜索类数据库全量/增量重建（商品、用户、文档）
2. 日志类数据定时归档到 ES（NGINX 日志分析）
3. MySQL 实时订单 → ES 搜索引擎（T+1 准实时）
4. 数据仓库聚合结果发布到 ES（BI 报表加速查询）
5. A/B 测试埋点数据 → ES（Kibana 可视化）

### 4.5 不适用场景

1. 实时毫秒级写入 → 用 Kafka Connect ES Sink
2. ES 作为唯一数据源 → 需评估数据丢失风险

### 4.6 思考题

1. ES 的 `_bulk` API 返回部分失败（如 1000 条中 3 条因 mapping 冲突失败），DataX 应如何处理？是将整个 batch 标记为脏数据还是只标记失败的 3 条？

2. 如果 ES 集群由多个节点组成（node01~node10），ESClient 的 endpoint 只配了一个 `node01`——当 node01 宕机时 Task 会全部失败吗？如何利用 ES Java Client 的节点自动发现机制？

（答案见附录）
