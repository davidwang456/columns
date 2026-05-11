# 第37章：自定义Task Plugin开发实战

## 1. 项目背景

Damai 数据团队最近踩了个大坑。那天凌晨三点，ETL 调度按惯例跑完 `dwd.daily_sales` 表的产出，下游 Spark ML 训练任务准时启动——结果全部崩溃。排查半天才发现，"amount" 字段里有几十行 NULL 值悄悄溜进来了，导致特征工程里的除零异常。运维群响个不停，业务方 Dashboard 空白了整整四个小时。老板第二天开会第一句话："数据质量谁在管？"

其实数据团队不是没做质量检查。每天 ETL 跑完后，小胖会手动连上 DBeaver，写一堆 `SELECT COUNT(*)`、`SELECT COUNT(col) / COUNT(*) AS null_ratio`，盯着屏幕看五分钟。问题是——靠人肉检查，迟早会漏。小胖自己都承认："哪天我请假了，质量检查就等于没做。"

团队后来引入了 Great Expectations（GE），一个业界流行的数据质量框架。在 GE 里，他们定义了一套预期规则（Expectation Suite）来验证 sales 表：`expect_column_values_to_not_be_null("amount")`、`expect_column_values_to_be_between("amount", 0, 100000)`、`expect_column_unique_value_count_to_be_between("order_id", 100, 10000)`。规则本身没问题，但 GE 和 Dolphinscheduler 是两座孤岛：运维先在 GE 的 Web UI 上手动触发验证，看报告，确认"pass"后才去 DolphinScheduler 上点击"继续运行"。一次调度链被打断成三个人工操作节点，自动化能力大打折扣。

更致命的是，随着业务扩张，Damai 每天有 47 张核心表需要质量把关。领导下了死命令：**"数据质量必须作为 DS 工作流的原生环节——质量不过关，下游绝不放行。"**

这恰好是 DS 自定义 Task Plugin 的典型应用场景：将 Great Expectations 的 API 能力封装成一个"DataQualityCheck"任务插件，把它变成 DS 调度链上一个原生的质量闸门（Quality Gate）。Worker 运行时自动调用 GE API，拿到 pass/fail 结果，按阈值决定放行还是阻断，整个过程无人值守。

这个需求背后，沉淀的是数据工程团队对"调度能力边界"的再思考：DS 内置的 Shell、SQL、Spark 等任务类型，解决的是"如何执行"的问题；但生产环境真正缺的，是"执行结果是否可信"的验证环节。把数据质量检查嵌入调度 DAG，就是把"事后救火"变成"事前拦截"。

## 2. 项目设计——剧本式交锋对话

**小胖**：翻着 DS 源码，手一指屏幕上的 `TaskChannel` 接口，自信满满。"写插件不就是实现个接口嘛！你看，`getTaskPluginName()` 返回个名字，`createTask()` 返回个 Task 对象——两行代码的事。Maven 建个 module，SPI 文件里写一行全限定类名，编译打包丢到 Worker 的 lib 目录，齐活！十分钟搞定。"

**小白**：托着腮，连珠炮似的追问。

"第一个问题——异步咋处理？你那个 Great Expectations 的 validation API，如果是几张 GB 级的大表，跑一次检查要五分钟。Worker 线程一直阻塞等着？那同一台 Worker 上的其他任务还跑不跑了？DS 的 Worker 线程池是有上限的。"

"第二个问题——失败了怎么报？你在 `handle()` 方法里 catch 到异常，是 printStackTrace 就算完事？Master 怎么知道任务失败了？告警怎么触发？`TaskCallBack.onFailure()` 你打算传什么消息回去？能把 GE 的报告链接一起带回去吗，让运维在 DS UI 上直接点开看？"  

"第三个问题——参数配置。DataQualityTask 需要传 Great Expectations 的 API URL、需要校验的表名、期望套件名称、失败阈值……这些参数用户怎么在 DS 的 UI 上配置？你总不能让用户写个 JSON 往里粘吧？DS 的任务定义页面，是怎么给每个 Task Plugin 生成专属配置表单的？"

"第四个问题——部署。研发环境编译好了，生产集群十几个 Worker 节点，你怎么把 JAR 发上去？重启 Worker 会不会造成正在跑的任务中断？滚动重启的顺序是什么？"

小胖挠了挠头，不那么自信了。

**大师**：放下茶杯，在白板上画了个示意图。

"写 DS 插件，本质上就像给一辆汽车装一个新的传感器。"

"DS 本身就是那台车，它定义了一套标准的'传感器接口'——这就是 SPI（Service Provider Interface）。不管你装的是温度传感器、压力传感器还是氧气传感器，只要你的接线端子（TaskChannel）符合接口规范，车载电脑（DS Worker）就能识别你、读取你、用你的数据做决策。"

"传感器怎么工作——你的 Task 内部逻辑，是调用 HTTP API 也好，起一个子进程执行 Python 脚本也好，你自己决定。客户（Worker）不在意你的实现细节。但有三件事你必须按规范来："

"第一，**报数**——检查完了，结果是好是坏，必须通过 `TaskCallBack` 回调给 Worker，Worker 再汇报给 Master。Master 拿着这个结果决定下游任务走 success 分支还是 failure 分支。"

"第二，**供电**——你的 Task 需要的参数（API 地址、表名、阈值），不是你自己去配置文件里读的，而是 DS 运行时通过 `TaskChannelContext` 注入给你的。DS 把用户在 UI 上填的参数序列化成 JSON，放进 `taskParams`，你的 Task 自己反序列化出 `DataQualityTaskParams` 对象。"  

"第三，**安装位置**——插件编译成 JAR 后，放到 Worker 的 `libs/` 目录下。Worker 启动时会扫描所有 JAR 里的 `META-INF/services/` 目录，通过 Java SPI 机制自动发现并注册你的 `TaskChannel`。看一眼 Worker 启动日志里有没有 `Registering TaskPlugin: DATA_QUALITY`，就知道装没装好。"

大师在白板上补充了一句：**"在 DS 里，插件 = 一个符合 SPI 协议的 TaskChannel Factory + 一个承载业务逻辑的 AbstractTask 子类 + 一个描述参数结构的 POJO。"**

小胖听懂了："所以关键是这三块：TaskChannel 负责注册，Task 负责干活，Params 负责配置。"

小白也接上："异步调用……我可以在 Task 里用 Apache HttpClient 发 HTTP POST，设个 socket timeout。如果真的需要长时间异步（比如跑 Spark Job），DS 有专门的 `AbstractYarnTask` 基类，用 `-status` 命令轮询，不占线程。"

大师点点头："正是这样。实际开发时，你的 Task 可以分三种模式——同步 HTTP 调用（适合秒级检查）、异步 JVM 调用（适合分钟级检查，用 `ScheduledExecutorService` 轮询）、子进程调用（适合重度计算，DS 已有 `AbstractShellTask` 可参考）。DataQualityCheck 通常选第一种就够了，但要加上超时和重试。"

## 3. 项目实战

### 3.1 创建 Maven 模块

首先在 `dolphinscheduler-task-plugin/` 目录下创建子模块的标准目录结构。

```bash
# 进入插件父目录
cd dolphinscheduler-task-plugin/

# 创建模块目录和包路径
mkdir -p dolphinscheduler-task-dataquality/src/main/java/org/apache/dolphinscheduler/plugin/task/dataquality
mkdir -p dolphinscheduler-task-dataquality/src/main/resources/META-INF/services
mkdir -p dolphinscheduler-task-dataquality/src/test/java/org/apache/dolphinscheduler/plugin/task/dataquality

# 创建 pom.xml
cat > dolphinscheduler-task-dataquality/pom.xml << 'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0"
         xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
         xsi:schemaLocation="http://maven.apache.org/POM/4.0.0 
         http://maven.apache.org/xsd/maven-4.0.0.xsd">
    <parent>
        <groupId>org.apache.dolphinscheduler</groupId>
        <artifactId>dolphinscheduler-task-plugin</artifactId>
        <version>${project.version}</version>
    </parent>
    <modelVersion>4.0.0</modelVersion>

    <artifactId>dolphinscheduler-task-dataquality</artifactId>

    <dependencies>
        <dependency>
            <groupId>org.apache.dolphinscheduler</groupId>
            <artifactId>dolphinscheduler-spi</artifactId>
        </dependency>
        <dependency>
            <groupId>org.apache.dolphinscheduler</groupId>
            <artifactId>dolphinscheduler-task-api</artifactId>
        </dependency>
        <!-- HTTP 客户端，用于调用 Great Expectations REST API -->
        <dependency>
            <groupId>org.apache.httpcomponents</groupId>
            <artifactId>httpclient</artifactId>
        </dependency>
        <!-- 测试依赖 -->
        <dependency>
            <groupId>junit</groupId>
            <artifactId>junit</artifactId>
            <scope>test</scope>
        </dependency>
        <dependency>
            <groupId>org.mockito</groupId>
            <artifactId>mockito-core</artifactId>
            <scope>test</scope>
        </dependency>
    </dependencies>
</project>
EOF
```

### 3.2 定义任务参数类

用户在 DS UI 上配置的参数，在 Task 内部会被反序列化成这个 POJO。JSON 字段名（`@JsonProperty`）必须和前端约定一致。

```java
package org.apache.dolphinscheduler.plugin.task.dataquality;

import com.fasterxml.jackson.annotation.JsonProperty;

/**
 * 数据质量检查任务的参数定义。
 * 用户在 DolphinScheduler UI 的任务定义页面填写这些字段，
 * DS 会将它们序列化为 JSON 存入 taskParams 字段。
 */
public class DataQualityTaskParams {

    @JsonProperty("dataQualityServiceUrl")
    private String dataQualityServiceUrl;   // Great Expectations API 地址

    @JsonProperty("expectationSuiteName")
    private String expectationSuiteName;     // 要执行的验证套件名称

    @JsonProperty("datasourceName")
    private String datasourceName;           // 数据源标识（如 hive_prod）

    @JsonProperty("tableName")
    private String tableName;                // 待校验表名

    @JsonProperty("scheduleDate")
    private String scheduleDate;             // 业务日期（DS 内置变量 ${biz_date}）

    @JsonProperty("timeoutSeconds")
    private int timeoutSeconds = 300;        // HTTP 超时时间（秒），默认5分钟

    @JsonProperty("failureThreshold")
    private double failureThreshold = 0.95;  // 及格线：95% 的期望通过才算通过

    // --- 标准 getter/setter ---

    public String getDataQualityServiceUrl() {
        return dataQualityServiceUrl;
    }

    public void setDataQualityServiceUrl(String dataQualityServiceUrl) {
        this.dataQualityServiceUrl = dataQualityServiceUrl;
    }

    public String getExpectationSuiteName() {
        return expectationSuiteName;
    }

    public void setExpectationSuiteName(String expectationSuiteName) {
        this.expectationSuiteName = expectationSuiteName;
    }

    public String getDatasourceName() {
        return datasourceName;
    }

    public void setDatasourceName(String datasourceName) {
        this.datasourceName = datasourceName;
    }

    public String getTableName() {
        return tableName;
    }

    public void setTableName(String tableName) {
        this.tableName = tableName;
    }

    public String getScheduleDate() {
        return scheduleDate;
    }

    public void setScheduleDate(String scheduleDate) {
        this.scheduleDate = scheduleDate;
    }

    public int getTimeoutSeconds() {
        return timeoutSeconds;
    }

    public void setTimeoutSeconds(int timeoutSeconds) {
        this.timeoutSeconds = timeoutSeconds;
    }

    public double getFailureThreshold() {
        return failureThreshold;
    }

    public void setFailureThreshold(double failureThreshold) {
        this.failureThreshold = failureThreshold;
    }

    @Override
    public String toString() {
        return String.format(
            "DataQualityTaskParams{url=%s, suite=%s, table=%s, threshold=%.2f}",
            dataQualityServiceUrl, expectationSuiteName, tableName, failureThreshold
        );
    }
}
```

### 3.3 实现 Task 核心逻辑

`AbstractTask` 是 DS 任务的生命周期基类。Worker 为你准备了上下文（`TaskRequest` 里包含了 `taskParams` 的 JSON 字符串、进程实例 ID、资源路径等），你只需重写 `handle()` 方法，把业务逻辑填进去，最后通过 `TaskCallBack` 通知 DS 框架结果。

```java
package org.apache.dolphinscheduler.plugin.task.dataquality;

import org.apache.dolphinscheduler.plugin.task.api.AbstractTask;
import org.apache.dolphinscheduler.plugin.task.api.TaskCallBack;
import org.apache.dolphinscheduler.plugin.task.api.TaskConstants;
import org.apache.dolphinscheduler.plugin.task.api.TaskException;
import org.apache.dolphinscheduler.plugin.task.api.model.TaskExecutionResult;
import org.apache.dolphinscheduler.spi.utils.JSONUtils;

import org.apache.http.client.config.RequestConfig;
import org.apache.http.client.methods.HttpPost;
import org.apache.http.entity.StringEntity;
import org.apache.http.impl.client.CloseableHttpClient;
import org.apache.http.impl.client.HttpClients;
import org.apache.http.util.EntityUtils;

import com.fasterxml.jackson.databind.JsonNode;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.nio.charset.StandardCharsets;

/**
 * DataQualityCheck 任务插件核心实现。
 * 
 * 工作流程：
 *   1. 从 TaskRequest 中解析用户配置的 DataQualityTaskParams
 *   2. 通过 HTTP POST 调用 Great Expectations 的 validation API
 *   3. 解析返回的 pass_rate 和 report_url
 *   4. 将 pass_rate 与 failureThreshold 比较，决定任务成功或失败
 *   5. 通过 TaskCallBack 将结果上报给 Worker/Master
 */
public class DataQualityTask extends AbstractTask {

    private static final Logger logger = LoggerFactory.getLogger(DataQualityTask.class);

    private DataQualityTaskParams taskParams;
    private volatile boolean cancelled = false;

    public DataQualityTask(TaskChannelContext context) {
        super(context);
    }

    @Override
    public void handle(TaskCallBack callback) throws TaskException {
        try {
            // ---- 第一步：解析参数 ----
            taskParams = JSONUtils.parseObject(
                getTaskRequest().getTaskParams(),
                DataQualityTaskParams.class
            );

            if (taskParams == null) {
                throw new TaskException("DataQualityTaskParams 解析失败，请检查任务配置");
            }

            logger.info("开始执行数据质量检查: {}", taskParams);

            // ---- 第二步：调用 Great Expectations API ----
            String responseBody = callDataQualityService();

            logger.info("Great Expectations 返回: {}", responseBody);

            // ---- 第三步：解析返回结果 ----
            JsonNode resultJson = JSONUtils.parseObject(responseBody);
            double passRate = resultJson.get("pass_rate").asDouble();
            String reportUrl = resultJson.has("report_url")
                ? resultJson.get("report_url").asText()
                : "N/A";

            // ---- 第四步：设置输出参数（供下游任务引用） ----
            setOutputParameter("quality_pass_rate", String.valueOf(passRate));
            setOutputParameter("quality_report_url", reportUrl);

            // 如果有详细的失败项列表，也透传出去
            if (resultJson.has("failed_expectations")) {
                setOutputParameter("failed_expectations",
                    resultJson.get("failed_expectations").toString());
            }

            // ---- 第五步：判断是否达到质量门槛 ----
            if (passRate >= taskParams.getFailureThreshold()) {
                // ✅ 质量检查通过
                setExitStatusCode(TaskConstants.EXIT_CODE_SUCCESS);
                logger.info("数据质量检查通过: pass_rate={} >= threshold={}",
                    passRate, taskParams.getFailureThreshold());
                callback.onSuccess();
            } else {
                // ❌ 质量检查未通过
                setExitStatusCode(TaskConstants.EXIT_CODE_FAILURE);
                String failMsg = String.format(
                    "数据质量门禁未通过: 通过率 %.2f%%  < 阈值 %.2f%%。"
                    + "请查看报告: %s",
                    passRate * 100,
                    taskParams.getFailureThreshold() * 100,
                    reportUrl
                );
                logger.warn(failMsg);
                callback.onFailure(failMsg);
            }

        } catch (Exception e) {
            logger.error("数据质量检查执行异常", e);
            setExitStatusCode(TaskConstants.EXIT_CODE_FAILURE);
            callback.onFailure("数据质量检查异常: " + e.getMessage());
        }
    }

    /**
     * 通过 HTTP POST 调用 Great Expectations 验证 API。
     * 请求体包含要验证的数据源、表名、日期和期望套件。
     */
    private String callDataQualityService() throws Exception {
        int timeoutMs = taskParams.getTimeoutSeconds() * 1000;

        RequestConfig requestConfig = RequestConfig.custom()
            .setConnectTimeout(5000)          // 建立连接超时 5 秒
            .setSocketTimeout(timeoutMs)      // 等待响应超时按参数配置
            .setConnectionRequestTimeout(5000)
            .build();

        try (CloseableHttpClient client = HttpClients.custom()
                .setDefaultRequestConfig(requestConfig)
                .build()) {

            HttpPost post = new HttpPost(taskParams.getDataQualityServiceUrl());
            post.setHeader("Content-Type", "application/json");
            post.setHeader("Accept", "application/json");

            // 构造请求体
            String body = String.format(
                "{\"suite\":\"%s\",\"datasource\":\"%s\",\"table\":\"%s\",\"date\":\"%s\"}",
                escapeJson(taskParams.getExpectationSuiteName()),
                escapeJson(taskParams.getDatasourceName()),
                escapeJson(taskParams.getTableName()),
                escapeJson(taskParams.getScheduleDate())
            );
            post.setEntity(new StringEntity(body, StandardCharsets.UTF_8));

            logger.debug("发送质量检查请求: POST {} body={}", 
                taskParams.getDataQualityServiceUrl(), body);

            // 执行请求并返回响应体字符串
            return client.execute(post, response -> {
                int statusCode = response.getStatusLine().getStatusCode();
                if (statusCode != 200) {
                    throw new RuntimeException(
                        "Great Expectations API 返回 HTTP " + statusCode);
                }
                return EntityUtils.toString(response.getEntity(), StandardCharsets.UTF_8);
            });
        }
    }

    /**
     * 对 JSON 字符串中的特殊字符做转义，防止注入。
     */
    private String escapeJson(String value) {
        return value == null ? "" : value
            .replace("\\", "\\\\")
            .replace("\"", "\\\"")
            .replace("\n", "\\n")
            .replace("\r", "\\r")
            .replace("\t", "\\t");
    }

    @Override
    public void cancel() {
        this.cancelled = true;
        logger.info("收到取消请求，任务将被终止: taskInstanceId={}", getTaskInstanceId());
        // 当前为同步 HTTP 调用，cancel() 依赖 handle() 中的 cancelled 标志判断。
        // 如果改为异步模式（如轮询式的 GE 长任务），需在此中断 HTTP 请求或清理资源。
    }

    @Override
    public TaskExecutionResult getTaskExecutionResult() {
        TaskExecutionResult result = new TaskExecutionResult();
        result.setTaskInstanceId(getTaskInstanceId());
        result.setProcessInstanceId(getProcessInstanceId());
        result.setStatus(getExitStatusCode());
        result.setOutputParameters(this.outputParameters);
        return result;
    }
}
```

### 3.4 实现 TaskChannel

`TaskChannel` 是 DS Worker 发现和创建 Task 实例的工厂接口。Worker 启动时扫描所有 SPI 注册的 TaskChannel，按 `getTaskPluginName()` 建立起"任务类型 → 工厂"的映射。前端下拉框里显示的插件名称就来源于此。

```java
package org.apache.dolphinscheduler.plugin.task.dataquality;

import org.apache.dolphinscheduler.plugin.task.api.AbstractTask;
import org.apache.dolphinscheduler.plugin.task.api.TaskChannel;
import org.apache.dolphinscheduler.plugin.task.api.TaskChannelContext;

/**
 * DATA_QUALITY 任务类型的 Channel 实现。
 * 
 * 实现要点：
 *   - getTaskPluginName() 返回的字符串是任务类型的唯一标识，
 *     它会出现在 DolphinScheduler UI 的任务类型下拉列表中
 *   - createTask() 每次创建一个新的 DataQualityTask 实例
 *   - isSingleton() 返回 false 表示每个任务实例独立创建
 */
public class DataQualityTaskChannel implements TaskChannel {

    @Override
    public AbstractTask createTask(TaskChannelContext context) {
        return new DataQualityTask(context);
    }

    @Override
    public void cancelTask(AbstractTask task) {
        task.cancel();
    }

    @Override
    public String getTaskPluginName() {
        return "DATA_QUALITY";   // ★ 这个字符串将出现在 DS UI 的任务类型下拉框中
    }

    @Override
    public boolean isSingleton() {
        return false;            // 每个任务实例独立创建新的 Task 对象
    }
}
```

### 3.5 创建 SPI 注册文件

Java SPI 的约定：在 `META-INF/services/` 目录下，创建一个以接口全限定名命名的文件，文件内容为实现类的全限定名，每行一个。

```bash
# 文件路径必须精确：
# src/main/resources/META-INF/services/org.apache.dolphinscheduler.plugin.task.api.TaskChannel

echo "org.apache.dolphinscheduler.plugin.task.dataquality.DataQualityTaskChannel" \
  > dolphinscheduler-task-dataquality/src/main/resources/META-INF/services/org.apache.dolphinscheduler.plugin.task.api.TaskChannel
```

### 3.6 注册到父级 POM

需要修改两个 POM 文件，将新模块纳入构建体系。

**Step A: 在 `dolphinscheduler-task-plugin/pom.xml` 中添加 `<module>`：**

```xml
<modules>
    <module>dolphinscheduler-task-shell</module>
    <module>dolphinscheduler-task-sql</module>
    <module>dolphinscheduler-task-spark</module>
    <module>dolphinscheduler-task-python</module>
    <!-- ……已有模块…… -->
    <module>dolphinscheduler-task-dataquality</module>   <!-- ★ 新增 -->
</modules>
```

**Step B: 在 `dolphinscheduler-task-all/pom.xml` 中添加 `<dependency>`：**

```xml
<dependency>
    <groupId>org.apache.dolphinscheduler</groupId>
    <artifactId>dolphinscheduler-task-dataquality</artifactId>
    <version>${project.version}</version>
</dependency>
```

### 3.7 构建与部署

```bash
# ====== 开发阶段：只编译当前模块 ======
./mvnw -pl dolphinscheduler-task-plugin/dolphinscheduler-task-dataquality -am clean package

# 验证 SPI 文件是否打进 JAR
jar -tf dolphinscheduler-task-plugin/dolphinscheduler-task-dataquality/target/dolphinscheduler-task-dataquality-*.jar \
  | grep "META-INF/services"

# ====== 发布阶段：全量构建 ======
./mvnw clean install -Prelease -DskipTests

# ====== 部署到已有集群 ======
# 生产环境通常是独立部署（只更新 Worker 的 lib），不需要重建整个分发包。
# 找到编译好的 JAR：
JAR_FILE=$(ls dolphinscheduler-task-plugin/dolphinscheduler-task-dataquality/target/dolphinscheduler-task-dataquality-*.jar)

# 逐个 Worker 节点，滚动部署：
scp "$JAR_FILE" worker-01:/opt/dolphinscheduler/worker-server/libs/
ssh worker-01 "cd /opt/dolphinscheduler && bin/dolphinscheduler-daemon.sh restart worker-server"
# 确认 worker-01 正常后再操作下一个节点

scp "$JAR_FILE" worker-02:/opt/dolphinscheduler/worker-server/libs/
ssh worker-02 "cd /opt/dolphinscheduler && bin/dolphinscheduler-daemon.sh restart worker-server"

# ====== 验证：查看 Worker 启动日志 ======
ssh worker-01 "tail -200 /opt/dolphinscheduler/worker-server/logs/dolphinscheduler-worker.log | grep 'Registering TaskPlugin'"
# 预期输出：Registering TaskPlugin: DATA_QUALITY
```

### 3.8 使用 Python SDK 创建工作流

插件部署完成后，可以通过 DolphinScheduler 的 Python SDK（[pydolphinscheduler](https://github.com/apache/dolphinscheduler-sdk-python)）在代码中定义工作流，实现质量门禁。

```python
from pydolphinscheduler.core.workflow import Workflow
from pydolphinscheduler.tasks.dataquality import DataQuality
from pydolphinscheduler.tasks.shell import Shell

with Workflow(
    name="sales_pipeline_with_quality_gate",
    project="engineering",
    description="销售数据管道——带质量门禁"
) as wf:

    # 步骤1：ETL 产出 dwd 表
    etl = Shell(
        name="etl_daily_sales",
        command="spark-submit /opt/etl/daily_sales_etl.py --date=${biz_date}"
    )

    # 步骤2：数据质量检查（闸门）
    quality_check = DataQuality(
        name="quality_check_sales",
        data_quality_service_url="http://great-expectations.damai.com/api/v1/validate",
        expectation_suite_name="sales_production_suite",
        datasource_name="hive_prod",
        table_name="dwd.daily_sales",
        schedule_date="${biz_date}",
        timeout_seconds=300,
        failure_threshold=0.95         # 95% 的期望必须通过
    )

    # 步骤3：下游 ML 训练（仅在质量通过后执行）
    ml_train = Shell(
        name="ml_training_pipeline",
        command="spark-submit /opt/ml/train_recommendation.py --date=${biz_date}"
    )

    # 质量失败时的告警分支
    alert_on_failure = Shell(
        name="quality_failed_alert",
        command="python /opt/scripts/send_dingtalk_alert.py "
                "'数据质量检查失败，表: dwd.daily_sales，请立即排查！'"
    )

    # 定义 DAG 依赖关系
    etl >> quality_check
    quality_check >> ml_train           # 成功路径：质量通过 → 训练
    quality_check >> alert_on_failure    # 失败路径：质量失败 → 告警

wf.submit()
```

### 3.9 常见问题排查

| 现象 | 原因 | 解决 |
|------|------|------|
| Worker 启动日志中找不到 `DATA_QUALITY` | SPI 文件路径或内容不对 | 确认 `META-INF/services/` 下的文件名与 `TaskChannel` 接口的全限定名完全一致；确认实现类为 `public` |
| 任务执行时报 `NullPointerException` | 参数解析失败 | 检查 `getTaskRequest().getTaskParams()` 返回的 JSON 字段名是否与 `@JsonProperty` 一致（注意大小写） |
| HTTP 调用超时 | 网络不通或 GE 服务响应慢 | 增大 `timeoutSeconds`；在 `RequestConfig` 中设置合理的 socket 超时；添加重试逻辑 |
| 生产环境 Worker 不停重启 | 新 JAR 与现有依赖冲突 | 检查 `mvn dependency:tree` 确保没有引入冲突版本的第三方库；使用 `provided` scope 避免类重复 |

### 3.10 生产化增强

上面实现的是基础版本。在生产环境中，还需要考虑以下增强：

```java
// 增强点一：微米指标上报
// 让 Prometheus 能采集质量检查的通过/失败率
registry.counter("ds.task.dataquality.executions",
    Tags.of("suite", taskParams.getExpectationSuiteName(),
            "result", passRate >= threshold ? "pass" : "fail"))
    .increment();

// 增强点二：HTTP 重试
// 网络抖动时自动重试，避免偶发失败
@Retryable(maxAttempts = 3, backoff = @Backoff(delay = 3000, multiplier = 2))
private String callDataQualityService() throws Exception { ... }

// 增强点三：详细结果透传
// 把 GE 返回的每个 expectation 的明细结果放到 outputParameters 中
// 供下游任务（如数据修复任务）精确知道是哪个字段的哪条规则失败了
setOutputParameter("quality_details", 
    resultJson.get("expectation_results").toString());
```

## 4. 项目总结

回顾这次自定义插件开发，我们实际上走通了一个标准化的**12 步插件生产流水线**：

> **需求梳理 → 接口研究 → 创建 Module → 编写 Params POJO → 实现 AbstractTask（handle/cancel/getResult） → 实现 TaskChannel → 创建 SPI 注册文件 → 注册到父 POM → 编译打包 → 部署到 Worker → 重启 Worker → 验证日志/运行测试任务**

这 12 步适用于 DolphinScheduler 下**任何自定义任务类型的开发**，无论是调用 HTTP API、启动容器、执行 AI 推理，还是触发外部系统的审批流，骨架不变，变的只是 `handle()` 方法里的业务代码。

三种常见的集成模式可以总结为：

1. **HTTP 驱动型**（本章模式）：Task 内部用 HTTP 客户端调用外部服务的 REST API，等待同步返回。适合响应时间 < 5 分钟的场景，如数据质量校验、模型推理、数据脱敏。

2. **进程驱动型**：Task 启动一个子进程执行外部可执行文件（如 Python 脚本、Shell 命令），进程结束后返回退出码。参考 DS 内置的 `ShellTask`、`PythonTask`，适合已有 CLI 工具需要纳管到调度的场景。

3. **SDK/JVM 驱动型**：Task 直接通过 Java SDK 调用外部框架（Druid、Flink、Spark），利用其 Java API 提交任务并轮询状态。参考 `SparkTask`、`FlinkTask`，适合与大数据计算引擎深度集成。

测试策略上，建议**三层金字塔**：

| 层 | 范围 | 工具 | 关注点 |
|----|------|------|--------|
| 单元测试 | `DataQualityTask.handle()` 的参数解析、阈值判断逻辑 | JUnit + Mockito，Mock 掉 HTTP 调用 | 业务逻辑正确性 |
| 集成测试 | 真实调用一个测试用的 Great Expectations 实例 | `@SpringBootTest` + Testcontainers 启动 GE 容器 | 网络调用、超时、异常处理 |
| E2E 测试 | 在 DS 上真正创建一个包含 DataQuality 任务的工作流并运行 | pydolphinscheduler SDK | 端到端链路：配置→调度→执行→回调 |

插件上线后的长期维护，需要关注三个点：**API 版本兼容**（GE 升级 API 后 Params 类要对应调整，但保持向后兼容）; **错误信息质量**（`callback.onFailure()` 的消息必须包含足够的排查线索——API URL、HTTP 状态码、响应体摘要，让运维不用翻 Worker 日志就能定位问题）; **灰度发布**（新插件先部署到一台 Worker 上观察 1~2 天，确认无异常后再全量铺开）。

> 📝 **思考题**
> 
> 1. 如果 Great Expectations 的 validation API 需要 20 分钟才能返回结果，当前的同步 HTTP 调用方式会有什么问题？你会如何改造 `DataQualityTask` 来支持这种长异步场景？（提示：参考 DS 的 `SparkTask`，它提交 spark-submit 后通过轮询 `yarn application -status` 来等待结果。）
> 
> 2. 在生产环境中，你发现 `DataQualityTask` 的 `cancel()` 方法被调用后，底层 HTTP 连接并没有真正中断，导致 Worker 上的线程资源被占用直到 Socket 超时。查阅 Apache HttpClient 文档，找到如何在另一个线程中真正中断一个进行中的 HTTP 请求，并给出代码改造方案。
