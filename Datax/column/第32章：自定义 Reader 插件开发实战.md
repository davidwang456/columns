# 第32章：自定义 Reader 插件开发实战

## 1. 项目背景

某金融科技公司需要从内部风控系统的 HTTP REST API 同步黑名单数据到数据仓库。API 返回分页 JSON 格式，鉴权方式为 OAuth 2.0 Client Credentials 模式——每 2 小时需要刷新 AccessToken。现有方案是用 Python 写了一个"定时拉取+写 CSV+DataX 导入"的三段式脚本，痛点很明确：

1. **中间落盘浪费**：API → JSON 文件 → DataX 读取 → 目标库——两道磁盘 IO，数据量 5000 万条/天时磁盘成为瓶颈
2. **OAuth Token 刷新逻辑复杂**：Python 脚本需要维护 Token 生命周期、处理 401 重试、处理多线程安全——这些逻辑与 DataX 的 Reader 机制天然契合
3. **无法享受 DataX 生态**：自带限速、流控、脏数据收集、监控上报——Python 脚本要全部重写一遍

团队决定直接开发一个自定义 DataX Reader 插件 `httpapireader`，让 DataX 原生支持从 HTTP 分页 API 拉取数据，省去中间 CSV 步骤，同时享受 DataX 全套治理能力。

本章从 Reader 插件的 Maven 项目结构、Job/Task 类生命周期、插件编译部署一条龙讲下来，最终产出一个可投入生产使用的 `httpapireader` 插件。

## 2. 项目设计——剧本式交锋对话

**（开发工位区，小胖正对着 Python 脚本改 Token 刷新逻辑，已经改了 3 个小时）**

**小胖**：（烦躁地摔了下键盘）这个 OAuth Token 刷新逻辑太恶心了！401 了要重试、多线程要加锁、Token 过期时间要提前去刷新——我一个写 SQL 的为什么要搞这些？

**小白**：（侧头看了一眼屏幕）你应该直接写一个 DataX Reader 插件。DataX 的 Reader 接口天然有 `init()` → `prepare()` → `startRead()` 的生命周期——Token 刷新的逻辑放在 `prepare()` 里，多线程安全交给 DataX 的 Task 隔离，不比你现在这个脚本强？

**小胖**：Reader 插件怎么写？我以前只改过别人的 JSON 配置。

**大师**：（拉了一把椅子坐过来）Reader 插件的本质就是实现 DataX 的 `Reader` 抽象类。关键就两个内部类——`Job` 和 `Task`。

**Job 的职责**（运行在 DataX 主进程）：
1. `init()`：解析 JSON 配置参数（URL、Headers、分页策略）
2. `prepare()`：执行全局准备工作（获取 OAuth Token、校验 API 连通性）
3. `split(adviceNumber)`：将一个大任务切分成 `adviceNumber` 个子任务，返回 `List<Configuration>`
4. `post()`：全局清理工作
5. `destroy()`：销毁资源

**Task 的职责**（运行在 TaskGroup 线程池中，每个 Task 独立线程）：
1. `init()`：从 `Configuration` 中解析本 Task 的分片参数（如"第 3 个分页段"）
2. `prepare()`：准备本 Task 的资源（如独立的 HTTP 连接池）
3. `startRead(RecordSender)`：核心——循环拉取数据，每拉一条就调 `recordSender.send(record)` 发给 Channel
4. `post()`：清理本 Task 资源
5. `destroy()`：销毁

**技术映射**：Job = 包工头。他在开工前把一整面墙（数据源）量好尺寸（parse config）、检查材料（preCheck）、画好瓷砖切割线（split），然后分配工人（Task）各自贴一片。Task = 贴砖工人——他拿到自己那一片的图纸（Configuration），一张张把瓷砖（Record）贴上去（send to Channel）。

**小胖**：（若有所思）也就是说，我的 Token 刷新逻辑放在 `Job.prepare()` 里执行一次，然后所有 Task 共享这个 Token？

**大师**：对，但要注意——Task 运行在独立线程中，需要保证 Token 的线程安全访问。推荐做法是 `Job.prepare()` 获取好 Token 后，通过 `Configuration` 传给每个 Task，Task 在自己的 `prepare()` 里重新解析。

**小白**：（追问）那 `split()` 方法怎么实现分页切分？我的 API 支持 `?page=N&size=1000` 这种分页参数。

**大师**：典型的分页 split 逻辑是这样的：

```java
@Override
public List<Configuration> split(int adviceNumber) {
    // 1. 先发一次请求获取总页数 totalPages
    String firstPage = fetchApi(1, 1);  // 拉 1 条拿 total
    int totalRecords = parseTotal(firstPage);
    int totalPages = (int) Math.ceil((double) totalRecords / pageSize);
    
    // 2. 将 totalPages 按 adviceNumber 均分成 adviceNumber 段
    List<Configuration> configs = new ArrayList<>();
    int pagesPerTask = (int) Math.ceil((double) totalPages / adviceNumber);
    
    for (int i = 0; i < adviceNumber; i++) {
        int startPage = i * pagesPerTask + 1;
        int endPage = Math.min(startPage + pagesPerTask - 1, totalPages);
        if (startPage > totalPages) break;
        
        Configuration taskConfig = this.configuration.clone();
        taskConfig.set("startPage", startPage);
        taskConfig.set("endPage", endPage);
        configs.add(taskConfig);
    }
    return configs;
}
```

每个 Task 根据 `startPage` 和 `endPage` 在自己的 `startRead()` 里循环拉取。

**小胖**：（眼睛一亮）这个我懂了！那 `startRead` 里面的主循环怎么写？

**大师**：

```java
@Override
public void startRead(RecordSender recordSender) {
    int startPage = this.taskConfig.getInt("startPage");
    int endPage = this.taskConfig.getInt("endPage");
    
    for (int page = startPage; page <= endPage; page++) {
        String responseJson = httpGet(apiUrl + "?page=" + page + "&size=" + pageSize, accessToken);
        List<JSONObject> records = parseJsonArray(responseJson);
        
        for (JSONObject item : records) {
            Record record = recordSender.createRecord();
            // 按 column 配置顺序填充字段
            for (ColumnMeta col : columnMetas) {
                record.addColumn(createColumn(item, col));
            }
            recordSender.sendToWriter(record);
        }
    }
}
```

**小胖**：还有一个问题——如果 API 中途挂了怎么办？比如拉到第 50 页时 404 了？

**大师**：这就是 `dirtyRecord` 脏数据机制的用武之地。在 `startRead()` 中 catch 异常：

```java
try {
    response = httpGet(url, token);
} catch (HttpException e) {
    // 记一条脏数据，但不中断任务
    Record dirty = recordSender.createRecord();
    dirty.addColumn(new StringColumn("HTTP_ERROR:" + e.getMessage()));
    recordSender.sendToWriter(dirty);
    continue;  // 跳过分页继续
}
```

配合 DataX Job 配置的 `errorLimit`——如果脏数据超过阈值（如 0.01%），整个 Job 才标记为失败。

**小白**：（追问）插件编译后怎么部署到 DataX？直接扔到 plugins 目录就行？

**大师**：两个关键点：

1. **目录结构**：`$DATAX_HOME/plugin/reader/httpapireader/` 下面必须有 `plugin.json` 和 `plugin.jar`（及依赖）
2. **plugin.json** 必须声明插件名和主类：

```json
{
    "name": "httpapireader",
    "class": "com.example.datax.plugin.reader.httpapi.HttpApiReader",
    "description": "HTTP API Reader - fetch paginated JSON from REST endpoints",
    "developer": "data-platform-team"
}
```

**ClassLoader 隔离**：DataX 的每个插件使用独立的 `PluginClassLoader` 加载，目的是避免不同插件的依赖冲突（如 Reader 用 fastjson 1.x、Writer 用 fastjson 2.x）。但有一个常见坑——如果插件依赖了 Hadoop 的 jar 包，会与 DataX 引擎层的 Hadoop 类冲突，需要 `ClassLoader` 隔离策略。

## 3. 项目实战

### 3.1 步骤一：创建 Maven 项目结构

**目标**：建立标准的 DataX Reader 插件 Maven 项目。

```powershell
New-Item -ItemType Directory -Path "httpapireader\src\main\java\com\example\datax\plugin\reader\httpapi" -Force
New-Item -ItemType Directory -Path "httpapireader\src\main\resources" -Force
New-Item -ItemType Directory -Path "httpapireader\src\main\assembly" -Force
```

**最终项目结构**：

```
httpapireader/
├── pom.xml
├── src/main/
│   ├── java/com/example/datax/plugin/reader/httpapi/
│   │   ├── HttpApiReader.java          # Reader 主类（继承 Reader）
│   │   ├── HttpApiConstant.java        # KEY 常量定义
│   │   └── HttpApiUtil.java            # HTTP 工具类（分页拉取、Token管理）
│   ├── resources/
│   │   └── plugin.json                 # 插件描述文件
│   └── assembly/
│       └── package.xml                 # maven-assembly 打包配置
```

**`pom.xml`** 核心内容：

```xml
<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0"
         xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
         xsi:schemaLocation="http://maven.apache.org/POM/4.0.0
         http://maven.apache.org/xsd/maven-4.0.0.xsd">
    <modelVersion>4.0.0</modelVersion>

    <groupId>com.example.datax</groupId>
    <artifactId>httpapireader</artifactId>
    <version>1.0.0</version>
    <packaging>jar</packaging>

    <properties>
        <maven.compiler.source>8</maven.compiler.source>
        <maven.compiler.target>8</maven.compiler.target>
        <datax.version>3.0.0</datax.version>
    </properties>

    <dependencies>
        <!-- DataX 核心 API（scope=provided，因为 DataX 引擎已包含） -->
        <dependency>
            <groupId>com.alibaba.datax</groupId>
            <artifactId>datax-common</artifactId>
            <version>${datax.version}</version>
            <scope>provided</scope>
        </dependency>
        <dependency>
            <groupId>com.alibaba.datax</groupId>
            <artifactId>plugin-rdbms-util</artifactId>
            <version>${datax.version}</version>
            <scope>provided</scope>
        </dependency>

        <!-- HTTP 客户端 -->
        <dependency>
            <groupId>org.apache.httpcomponents</groupId>
            <artifactId>httpclient</artifactId>
            <version>4.5.14</version>
        </dependency>

        <!-- JSON 解析 -->
        <dependency>
            <groupId>com.alibaba</groupId>
            <artifactId>fastjson</artifactId>
            <version>1.2.83</version>
            <scope>provided</scope>
        </dependency>

        <!-- 工具库 -->
        <dependency>
            <groupId>org.apache.commons</groupId>
            <artifactId>commons-lang3</artifactId>
            <version>3.12.0</version>
        </dependency>
    </dependencies>

    <build>
        <plugins>
            <plugin>
                <groupId>org.apache.maven.plugins</groupId>
                <artifactId>maven-assembly-plugin</artifactId>
                <version>3.3.0</version>
                <configuration>
                    <descriptors>
                        <descriptor>src/main/assembly/package.xml</descriptor>
                    </descriptors>
                </configuration>
                <executions>
                    <execution>
                        <phase>package</phase>
                        <goals><goal>single</goal></goals>
                    </execution>
                </executions>
            </plugin>
        </plugins>
    </build>
</project>
```

### 3.2 步骤二：定义插件常量与 Key

**目标**：定义 JSON 配置文件中各参数的 Key 常量，避免硬编码字符串。

**`HttpApiConstant.java`**：

```java
package com.example.datax.plugin.reader.httpapi;

public class HttpApiConstant {
    // 必填参数
    public static final String KEY_URL            = "url";
    public static final String KEY_COLUMN         = "column";
    public static final String KEY_PAGE_SIZE      = "pageSize";
    public static final String KEY_USERNAME       = "username";
    public static final String KEY_PASSWORD       = "password";

    // 鉴权参数（OAuth 2.0）
    public static final String KEY_AUTH_TYPE      = "authType";
    public static final String KEY_TOKEN_URL      = "tokenUrl";
    public static final String KEY_CLIENT_ID      = "clientId";
    public static final String KEY_CLIENT_SECRET  = "clientSecret";
    public static final String KEY_ACCESS_TOKEN   = "accessToken";

    // 分页参数名（API 可能用 page/pageNum）
    public static final String KEY_PAGE_PARAM     = "pageParam";
    public static final String KEY_SIZE_PARAM     = "sizeParam";
    public static final String KEY_TOTAL_PATH     = "totalDataPath";

    // 可选参数
    public static final String KEY_HEADERS        = "headers";
    public static final String KEY_CONNECT_TIMEOUT = "connectTimeout";
    public static final String KEY_READ_TIMEOUT   = "readTimeout";

    // 默认值
    public static final int    DEFAULT_PAGE_SIZE  = 1000;
    public static final String DEFAULT_AUTH_TYPE  = "none";
    public static final String DEFAULT_PAGE_PARAM = "page";
    public static final String DEFAULT_SIZE_PARAM = "size";
    public static final int    DEFAULT_CONNECT_TIMEOUT = 5000;
    public static final int    DEFAULT_READ_TIMEOUT    = 30000;
}
```

### 3.3 步骤三：实现 Reader 主类——Job 内部类

**目标**：实现 `Reader.Job` 的核心生命周期方法。

**`HttpApiReader.java`**：

```java
package com.example.datax.plugin.reader.httpapi;

import com.alibaba.datax.common.exception.DataXException;
import com.alibaba.datax.common.plugin.RecordSender;
import com.alibaba.datax.common.spi.Reader;
import com.alibaba.datax.common.util.Configuration;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.util.ArrayList;
import java.util.List;

public class HttpApiReader extends Reader {

    public static class Job extends Reader.Job {
        private static final Logger LOG = LoggerFactory.getLogger(Job.class);
        private Configuration originalConfig;

        @Override
        public void init() {
            this.originalConfig = super.getPluginJobConf();
            
            // 1. 校验必填参数
            String url = originalConfig.getString(HttpApiConstant.KEY_URL);
            if (url == null || url.isEmpty()) {
                throw DataXException.asDataXException(
                    HttpApiErrorCode.REQUIRED_PARAM_MISSING, "url 参数不能为空");
            }
            LOG.info("HttpApiReader Job init: url={}", url);

            // 2. 校验 column 配置
            List<Configuration> columns = originalConfig.getListConfiguration(
                HttpApiConstant.KEY_COLUMN);
            if (columns == null || columns.isEmpty()) {
                throw DataXException.asDataXException(
                    HttpApiErrorCode.REQUIRED_PARAM_MISSING, "column 参数不能为空");
            }
            LOG.info("HttpApiReader Job init: column count={}", columns.size());

            // 3. 设置默认值
            originalConfig.set(HttpApiConstant.KEY_PAGE_SIZE,
                originalConfig.getInt(HttpApiConstant.KEY_PAGE_SIZE,
                    HttpApiConstant.DEFAULT_PAGE_SIZE));
        }

        @Override
        public void prepare() {
            // OAuth 2.0 Token 获取
            String authType = originalConfig.getString(
                HttpApiConstant.KEY_AUTH_TYPE, HttpApiConstant.DEFAULT_AUTH_TYPE);
            
            if ("oauth2".equalsIgnoreCase(authType)) {
                String tokenUrl = originalConfig.getString(
                    HttpApiConstant.KEY_TOKEN_URL);
                String clientId = originalConfig.getString(
                    HttpApiConstant.KEY_CLIENT_ID);
                String clientSecret = originalConfig.getString(
                    HttpApiConstant.KEY_CLIENT_SECRET);
                
                LOG.info("HttpApiReader: requesting OAuth2 token from {}", tokenUrl);
                String accessToken = HttpApiUtil.fetchOAuth2Token(
                    tokenUrl, clientId, clientSecret);
                originalConfig.set(HttpApiConstant.KEY_ACCESS_TOKEN, accessToken);
                LOG.info("HttpApiReader: OAuth2 token obtained successfully");
            } else if ("basic".equalsIgnoreCase(authType)) {
                String username = originalConfig.getString(
                    HttpApiConstant.KEY_USERNAME);
                String password = originalConfig.getString(
                    HttpApiConstant.KEY_PASSWORD);
                String basicAuth = HttpApiUtil.encodeBasicAuth(username, password);
                originalConfig.set("Authorization", "Basic " + basicAuth);
            }
        }

        @Override
        public List<Configuration> split(int adviceNumber) {
            String url = originalConfig.getString(HttpApiConstant.KEY_URL);
            int pageSize = originalConfig.getInt(HttpApiConstant.KEY_PAGE_SIZE);
            String accessToken = originalConfig.getString(
                HttpApiConstant.KEY_ACCESS_TOKEN);

            // 发一个 pageSize=1 的请求获取 total
            long totalRecords = HttpApiUtil.fetchTotalCount(url, accessToken, 
                originalConfig.getString(HttpApiConstant.KEY_TOTAL_PATH, "data.total"));

            if (totalRecords == 0) {
                LOG.warn("HttpApiReader: API returned 0 records");
                return new ArrayList<>();
            }
            
            int totalPages = (int) Math.ceil((double) totalRecords / pageSize);
            int pagesPerTask = (int) Math.ceil((double) totalPages / adviceNumber);
            
            LOG.info("HttpApiReader split: total={}, pageSize={}, totalPages={}, "
                + "adviceNumber={}, pagesPerTask={}",
                totalRecords, pageSize, totalPages, adviceNumber, pagesPerTask);

            List<Configuration> taskConfigs = new ArrayList<>();
            for (int i = 0; i < adviceNumber; i++) {
                int startPage = i * pagesPerTask + 1;
                int endPage = Math.min(startPage + pagesPerTask - 1, totalPages);
                if (startPage > totalPages) break;

                Configuration taskConfig = this.originalConfig.clone();
                taskConfig.set("startPage", startPage);
                taskConfig.set("endPage", endPage);
                taskConfig.set("taskIndex", i);
                taskConfigs.add(taskConfig);
            }
            return taskConfigs;
        }

        @Override
        public void post() {
            LOG.info("HttpApiReader Job post: all tasks completed");
        }

        @Override
        public void destroy() {
            LOG.info("HttpApiReader Job destroy");
        }
    }

    // ==== Task inner class continues below ====
```

### 3.4 步骤四：实现 Task 内部类——startRead 主循环

**目标**：实现 `Reader.Task` 的核心数据拉取循环。

```java
    public static class Task extends Reader.Task {
        private static final Logger LOG = LoggerFactory.getLogger(Task.class);

        private String url;
        private String accessToken;
        private int pageSize;
        private int startPage;
        private int endPage;
        private String pageParam;
        private String sizeParam;
        private List<Configuration> columnMetas;
        private int connectTimeout;
        private int readTimeout;

        @Override
        public void init() {
            Configuration taskConfig = super.getPluginJobConf();
            
            this.url = taskConfig.getString(HttpApiConstant.KEY_URL);
            this.accessToken = taskConfig.getString(HttpApiConstant.KEY_ACCESS_TOKEN);
            this.pageSize = taskConfig.getInt(HttpApiConstant.KEY_PAGE_SIZE);
            this.startPage = taskConfig.getInt("startPage", 1);
            this.endPage = taskConfig.getInt("endPage", 1);
            this.pageParam = taskConfig.getString(
                HttpApiConstant.KEY_PAGE_PARAM, HttpApiConstant.DEFAULT_PAGE_PARAM);
            this.sizeParam = taskConfig.getString(
                HttpApiConstant.KEY_SIZE_PARAM, HttpApiConstant.DEFAULT_SIZE_PARAM);
            this.columnMetas = taskConfig.getListConfiguration(
                HttpApiConstant.KEY_COLUMN);
            this.connectTimeout = taskConfig.getInt(
                HttpApiConstant.KEY_CONNECT_TIMEOUT, 
                HttpApiConstant.DEFAULT_CONNECT_TIMEOUT);
            this.readTimeout = taskConfig.getInt(
                HttpApiConstant.KEY_READ_TIMEOUT, 
                HttpApiConstant.DEFAULT_READ_TIMEOUT);

            LOG.info("HttpApiReader Task[{}] init: pages {} → {}",
                taskConfig.getInt("taskIndex"), startPage, endPage);
        }

        @Override
        public void startRead(RecordSender recordSender) {
            LOG.info("HttpApiReader Task startRead: begin pulling pages {} → {}",
                startPage, endPage);

            for (int page = startPage; page <= endPage; page++) {
                String fullUrl = buildPageUrl(url, page, pageSize);
                LOG.debug("Fetching page {}: {}", page, fullUrl);

                try {
                    String responseJson = HttpApiUtil.httpGet(
                        fullUrl, accessToken, connectTimeout, readTimeout);
                    List<Object> dataList = HttpApiUtil.parseDataList(responseJson);

                    for (Object item : dataList) {
                        Record record = recordSender.createRecord();
                        JSONObject jsonObj = (JSONObject) item;
                        
                        for (Configuration colMeta : columnMetas) {
                            String colName = colMeta.getString("name");
                            String colType = colMeta.getString("type", "string");
                            record.addColumn(
                                HttpApiUtil.createColumn(jsonObj, colName, colType));
                        }
                        recordSender.sendToWriter(record);
                    }
                    LOG.info("Page {} fetched: {} records", page, dataList.size());
                    
                } catch (Exception e) {
                    LOG.warn("Page {} failed: {}", page, e.getMessage());
                    // 异常记录为脏数据，不中断任务
                    Record dirtyRecord = recordSender.createRecord();
                    dirtyRecord.addColumn(new StringColumn("FETCH_ERROR:" + e.getMessage()));
                    recordSender.sendToWriter(dirtyRecord);
                }
            }
            LOG.info("HttpApiReader Task startRead: finished pages {} → {}", 
                startPage, endPage);
        }
    }
}
```

### 3.5 步骤五：实现 HTTP 工具类

**目标**：封装 HTTP GET、OAuth2 Token 获取、JSON 解析等通用逻辑。

**`HttpApiUtil.java`**（核心方法）：

```java
package com.example.datax.plugin.reader.httpapi;

import com.alibaba.datax.common.element.*;
import com.alibaba.datax.common.exception.DataXException;
import com.alibaba.fastjson.JSON;
import com.alibaba.fastjson.JSONArray;
import com.alibaba.fastjson.JSONObject;
import org.apache.http.HttpResponse;
import org.apache.http.client.config.RequestConfig;
import org.apache.http.client.methods.HttpGet;
import org.apache.http.client.methods.HttpPost;
import org.apache.http.entity.StringEntity;
import org.apache.http.impl.client.CloseableHttpClient;
import org.apache.http.impl.client.HttpClients;
import org.apache.http.util.EntityUtils;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.util.Base64;
import java.util.List;

public class HttpApiUtil {
    private static final Logger LOG = LoggerFactory.getLogger(HttpApiUtil.class);

    public static String httpGet(String url, String accessToken,
            int connectTimeout, int readTimeout) {
        try (CloseableHttpClient client = HttpClients.createDefault()) {
            HttpGet get = new HttpGet(url);
            get.setConfig(RequestConfig.custom()
                .setConnectTimeout(connectTimeout)
                .setSocketTimeout(readTimeout)
                .build());
            get.setHeader("Content-Type", "application/json");
            if (accessToken != null && !accessToken.isEmpty()) {
                get.setHeader("Authorization", "Bearer " + accessToken);
            }
            
            HttpResponse response = client.execute(get);
            int statusCode = response.getStatusLine().getStatusCode();
            String body = EntityUtils.toString(response.getEntity(), "UTF-8");
            
            if (statusCode == 401) {
                throw DataXException.asDataXException(
                    HttpApiErrorCode.AUTH_FAILED, "HTTP 401: token expired");
            }
            if (statusCode >= 400) {
                throw DataXException.asDataXException(
                    HttpApiErrorCode.HTTP_ERROR,
                    "HTTP " + statusCode + ": " + body.substring(0, Math.min(200, body.length())));
            }
            return body;
        } catch (DataXException e) {
            throw e;
        } catch (Exception e) {
            throw DataXException.asDataXException(
                HttpApiErrorCode.NETWORK_ERROR, "HTTP request failed: " + e.getMessage());
        }
    }

    public static String fetchOAuth2Token(String tokenUrl, String clientId, String clientSecret) {
        try (CloseableHttpClient client = HttpClients.createDefault()) {
            HttpPost post = new HttpPost(tokenUrl);
            post.setHeader("Content-Type", "application/x-www-form-urlencoded");
            
            String body = "grant_type=client_credentials&client_id=" + clientId 
                + "&client_secret=" + clientSecret;
            post.setEntity(new StringEntity(body, "UTF-8"));
            
            HttpResponse response = client.execute(post);
            String respJson = EntityUtils.toString(response.getEntity(), "UTF-8");
            JSONObject json = JSON.parseObject(respJson);
            
            String accessToken = json.getString("access_token");
            if (accessToken == null) {
                throw DataXException.asDataXException(
                    HttpApiErrorCode.AUTH_FAILED,
                    "OAuth2 response missing access_token: " + respJson);
            }
            return accessToken;
        } catch (DataXException e) {
            throw e;
        } catch (Exception e) {
            throw DataXException.asDataXException(
                HttpApiErrorCode.NETWORK_ERROR,
                "OAuth2 token fetch failed: " + e.getMessage());
        }
    }

    public static long fetchTotalCount(String url, String accessToken, String totalPath) {
        // 发送 pageSize=1 的请求，从响应 JSON 中按 totalPath 提取 total
        String testUrl = url + (url.contains("?") ? "&" : "?") + "page=1&size=1";
        String response = httpGet(testUrl, accessToken, 5000, 10000);
        JSONObject json = JSON.parseObject(response);
        
        // totalPath 如 "data.total"，按 "." 分割导航 JSON
        String[] keys = totalPath.split("\\.");
        JSONObject current = json;
        for (int i = 0; i < keys.length - 1; i++) {
            current = current.getJSONObject(keys[i]);
        }
        return current.getLongValue(keys[keys.length - 1]);
    }

    public static List<Object> parseDataList(String responseJson) {
        JSONObject root = JSON.parseObject(responseJson);
        // 兼容多种分页响应格式："data.list"、"data"、"records"
        JSONArray arr = root.getJSONArray("data");
        if (arr == null) {
            arr = root.getJSONObject("data").getJSONArray("list");
        }
        if (arr == null) {
            arr = root.getJSONArray("records");
        }
        if (arr == null) {
            throw DataXException.asDataXException(
                HttpApiErrorCode.PARSE_ERROR, 
                "Cannot find data array in response: " + responseJson.substring(0, 200));
        }
        return arr;
    }

    public static Column createColumn(JSONObject jsonObj, String colName, String colType) {
        Object value = jsonObj.get(colName);
        if (value == null) {
            return new StringColumn(null);
        }
        switch (colType.toLowerCase()) {
            case "long":
                return new LongColumn(Long.parseLong(value.toString()));
            case "double":
                return new DoubleColumn(Double.parseDouble(value.toString()));
            case "date":
                return new DateColumn(Long.parseLong(value.toString()));
            case "bool":
                return new BoolColumn(Boolean.parseBoolean(value.toString()));
            case "bytes":
                return new BytesColumn(value.toString().getBytes());
            case "string":
            default:
                return new StringColumn(value.toString());
        }
    }

    public static String encodeBasicAuth(String username, String password) {
        String credentials = username + ":" + password;
        return Base64.getEncoder().encodeToString(credentials.getBytes());
    }

    static String buildPageUrl(String baseUrl, int page, int pageSize) {
        String separator = baseUrl.contains("?") ? "&" : "?";
        return baseUrl + separator + "page=" + page + "&size=" + pageSize;
    }
}
```

### 3.6 步骤六：plugin.json 与打包部署

**目标**：配置 plugin.json，用 Maven Assembly 打包，部署到 DataX。

**`src/main/resources/plugin.json`**：

```json
{
    "name": "httpapireader",
    "class": "com.example.datax.plugin.reader.httpapi.HttpApiReader",
    "description": "HTTP API Reader Plugin - fetch paginated JSON from REST endpoints with OAuth 2.0 support",
    "developer": "data-platform"
}
```

**`src/main/assembly/package.xml`**：

```xml
<assembly>
    <id>plugin</id>
    <formats>
        <format>dir</format>
    </formats>
    <includeBaseDirectory>false</includeBaseDirectory>
    <fileSets>
        <fileSet>
            <directory>target/classes</directory>
            <outputDirectory>plugin/reader/httpapireader</outputDirectory>
            <includes><include>plugin.json</include></includes>
        </fileSet>
    </fileSets>
    <dependencySets>
        <dependencySet>
            <useProjectArtifact>true</useProjectArtifact>
            <outputDirectory>plugin/reader/httpapireader/libs</outputDirectory>
            <scope>runtime</scope>
        </dependencySet>
    </dependencySets>
</assembly>
```

**编译与部署**：

```powershell
# 编译插件
mvn clean package -DskipTests

# 部署到 DataX
$DATAX_HOME = "D:\software\workspace\bigdata-hub\datax"
$targetDir = "$DATAX_HOME\plugin\reader\httpapireader"

# 创建插件目录
New-Item -ItemType Directory -Path "$targetDir\libs" -Force

# 复制 jar 和依赖
Copy-Item "target\httpapireader-1.0.0.jar" -Destination "$targetDir\"
Copy-Item "target\classes\plugin.json" -Destination "$targetDir\"

# 复制依赖 jar（maven-assembly 会自动处理，这里确保 libs 目录存在）
# libs/ 目录下应包含所有 runtime scope 的依赖 jar 包

# 验证插件安装
Get-ChildItem -Recurse "$DATAX_HOME\plugin\reader\httpapireader\" | Select-Object FullName
```

**使用示例——DataX Job JSON**：

```json
{
    "job": {
        "content": [{
            "reader": {
                "name": "httpapireader",
                "parameter": {
                    "url": "https://api.internal.com/v2/blacklist",
                    "authType": "oauth2",
                    "tokenUrl": "https://auth.internal.com/oauth/token",
                    "clientId": "datax_reader",
                    "clientSecret": "sec_xxx",
                    "pageSize": 1000,
                    "totalDataPath": "data.total",
                    "column": [
                        {"name": "id", "type": "long"},
                        {"name": "name", "type": "string"},
                        {"name": "id_number", "type": "string"},
                        {"name": "risk_level", "type": "string"},
                        {"name": "add_time", "type": "date"}
                    ],
                    "connectTimeout": 5000,
                    "readTimeout": 30000
                }
            },
            "writer": {
                "name": "mysqlwriter",
                "parameter": {
                    "username": "root",
                    "password": "root",
                    "writeMode": "replace",
                    "column": ["id", "name", "id_number", "risk_level", "add_time"],
                    "connection": [{
                        "table": ["blacklist_dw"],
                        "jdbcUrl": ["jdbc:mysql://localhost:3306/risk_db"]
                    }]
                }
            }
        }],
        "setting": {
            "speed": {"channel": 5},
            "errorLimit": {"record": 100, "percentage": 0.01}
        }
    }
}
```

### 3.7 可能遇到的坑及解决方法

**坑1：ClassLoader 隔离导致 fastjson 找不到类**

DataX 引擎层和插件层的 ClassLoader 是隔离的。如果插件依赖了 fastjson 但 DataX 引擎已经加载了不同版本的 fastjson，可能会出现 `NoClassDefFoundError`。

```
报错: java.lang.NoClassDefFoundError: com/alibaba/fastjson/JSON
解决: 
1. 在 pom.xml 中将 fastjson 的 scope 设为 compile（不打 provided）
2. 确保 libs/ 目录下包含 fastjson jar
3. 避免同时引入两个版本的 fastjson
```

**坑2：plugin.json 路径错误导致插件无法发现**

DataX 通过 `plugin.json` 的路径推断插件 jar。如果 `plugin.json` 放在了 `libs/` 而不是插件根目录，插件加载失败。

```
报错: Cannot find plugin: httpapireader
检查: plugin.json 必须在 plugin/reader/httpapireader/ 目录下（与 libs/ 同级）
```

**坑3：startRead() 中异常未捕获导致 Task 挂掉**

DataX 的 Task 执行逻辑中，`startRead()` 抛出的异常会导致整个 Task 终止，Channel 中未消费的数据标记为脏数据。务必在循环内 catch 异常。

```
错误写法:
for (page...) {
    String data = httpGet(url);  // 抛异常 → Task 终止
}

正确写法:
for (page...) {
    try { String data = httpGet(url); } catch (Exception e) { ... }
}
```

**坑4：OAuth2 Token 在多 Task 间共享失效**

如果 Token 有效期 2 小时，但任务跑了 3 小时，后续 Task 会收到 401。Job 的 `prepare()` 只执行一次，无法感知 Token 过期。

```
解决方案:
1. 在 Task 端实现 Token 缓存 + 过期前 5 分钟自动刷新
2. 或调大 Task 的 split adviceNumber（减少单 Task 执行时间）
3. 或在重试次数内处理 401 → 刷新 Token → 重新拉取
```

## 4. 项目总结

### 4.1 Reader 插件开发速查表

| 阶段 | 核心类/方法 | 职责 | 注意事项 |
|------|------------|------|---------|
| 项目初始化 | pom.xml | 依赖声明 | core API 用 provided，自有用 compile |
| 插件注册 | plugin.json | 声明 name + mainClass | 必须放在插件根目录 |
| Job.init() | Reader.Job | 校验参数、设默认值 | 参数校验要严格，防止运行时异常 |
| Job.prepare() | Reader.Job | 全局准备（Token等） | Token 通过 Configuration 传递给 Task |
| Job.split() | Reader.Job | 切分任务 | 返回 List<Configuration> |
| Task.init() | Reader.Task | 解析分片参数 | 不共享可变状态、线程安全 |
| Task.startRead() | Reader.Task | 拉取数据并 send | 循环内 catch 异常、记录脏数据 |

### 4.2 优点

1. **生命周期清晰**：Job.prepare/Task.startRead 分离了全局准备与局部拉取的职责
2. **天然并发**：split 返回 N 个 Configuration → DataX 自动创建 N 个 Task 并发运行
3. **享受 DataX 生态**：编译成插件即可自动获得限速、流控、脏数据、监控能力
4. **ClassLoader 隔离**：插件依赖与引擎依赖互不干扰，版本升级安全
5. **扩展性强**：同模式可复用到 FTP/Redis/ES 等任何可编程数据源

### 4.3 缺点

1. **插件加载机制隐式**：plugin.json 路径错误没有明确的报错提示，排查耗时
2. **split 前需先拉一次 total**：对 API 算力有额外消耗（可通过缓存或统计表优化）
3. **OAuth Token 生命周期管理**：Job 级别获取、Task 级别使用，过期需自行处理
4. **不支持流式 API**：当前设计基于分页，无法对接 Server-Sent Events 或 WebSocket

### 4.4 适用场景

1. 内部 REST API 批量同步（风控、客户信息、配置中心）
2. 第三方 SaaS 平台数据回传（Salesforce、Jira、钉钉审批数据）
3. 微服务治理平台的数据聚合（多个服务的 API 统一同步到数仓）
4. 公共数据开放平台的定时抓取（交通数据、天气数据）

### 4.5 不适用场景

1. 实时数据流（Kafka/Flink 更合适）
2. GraphQL 端点（需要适配多层嵌套查询）

### 4.6 思考题

1. 如果 API 不支持 `total` 参数，无法提前知道总页数，split 方法如何设计？是分页到遇到空页为止，还是用采样估算？

2. 在 Task.startRead() 中如果 API 返回 429 (Rate Limited)，应该如何处理？内置的限速机制（`byte/speed`）与 API 限制如何协同？

（答案见附录）
