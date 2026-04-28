# 第37章：ML/Flink——实时特征工程与在线推理

---

## 1. 项目背景

某推荐系统需要在用户点击或浏览时，实时计算特征向量并调用模型推理，返回推荐结果。传统做法是：用户行为 → 存储到HBase → 离线脚本批量特征计算 → 模型推理 → 存储结果。延迟通常在小时级别。

用Flink实现实时特征工程可以做到：
- 用户点击新闻 → 毫秒级更新用户的兴趣向量
- 新用户进入App → 实时计算冷启动特征
- 实时统计特征（最近5分钟的点击率、收藏率等）

---

## 2. 项目设计

> 场景：推荐算法工程师训练好了CTR模型，需要在线特征管道提供实时特征。小胖被问能不能做。

**大师**：实时特征工程本质上是"有状态的流处理"——每个用户的实时行为需要聚合为特征向量。

**技术映射：实时特征 = 时间窗口内的统计值 + 用户长期状态。Flink的State + Window正好是实时特征的天然计算引擎。**

**小白**：那模型推理怎么和Flink集成？在Flink算子中加载模型并推理吗？

**大师**：两种模式：

1. **Embedded（嵌入式）**：模型作为UDF加载到Flink算子中，在map/flatMap中直接推理。适合小模型（<100MB），如逻辑回归、决策树。
2. **Remote（远程调用）**：Flink通过RPC调用模型服务（如TF Serving、PMML Server）。适合大模型（深度学习），通过AsyncIO做异步调用。

**技术映射：Embedded推理 = 低延迟（微秒级）但内存占用高；Remote推理 = 高延迟（毫秒级）但模型容量大。**

---

## 3. 项目实战

### 分步实现

#### 步骤1：实时统计特征计算

**目标**：对每个用户实时计算最近5分钟的点击率。

```java
package com.flink.column.chapter37;

import org.apache.flink.api.common.eventtime.WatermarkStrategy;
import org.apache.flink.api.common.functions.RichMapFunction;
import org.apache.flink.api.common.state.ValueState;
import org.apache.flink.api.common.state.ValueStateDescriptor;
import org.apache.flink.configuration.Configuration;
import org.apache.flink.streaming.api.datastream.DataStream;
import org.apache.flink.streaming.api.environment.StreamExecutionEnvironment;
import org.apache.flink.streaming.api.windowing.assigners.SlidingEventTimeWindows;
import org.apache.flink.streaming.api.windowing.time.Time;
import java.time.Duration;

/**
 * 实时特征工程：计算每个用户的实时点击率(CTR)
 * 特征：最近5分钟点击量 / 最近5分钟曝光量
 */
public class FeatureEngineering {

    public static void main(String[] args) throws Exception {
        StreamExecutionEnvironment env = StreamExecutionEnvironment.getExecutionEnvironment();

        DataStream<UserEvent> events = env.socketTextStream("localhost", 9999)
                .map(line -> {
                    String[] p = line.split(",");
                    // 输入: userId,eventType(exposure/click),timestamp
                    return new UserEvent(p[0], p[1], Long.parseLong(p[2]));
                })
                .assignTimestampsAndWatermarks(
                        WatermarkStrategy.<UserEvent>forBoundedOutOfOrderness(
                                        Duration.ofSeconds(10))
                                .withTimestampAssigner((e, ts) -> e.timestamp)
                );

        // 计算每个用户最近5分钟的点击量
        DataStream<String> clickCounts = events
                .filter(e -> "click".equals(e.eventType))
                .keyBy(e -> e.userId)
                .window(SlidingEventTimeWindows.of(Time.minutes(5), Time.minutes(1)))
                .sum(1)  // 简化——实际应单独统计
                .map(e -> String.format("user=%s, clicks_5min=%d", e.f0, e.f1));

        // 计算CTR特征
        DataStream<String> ctrFeatures = events
                .keyBy(e -> e.userId)
                .map(new RichMapFunction<UserEvent, String>() {
                    private transient ValueState<Long> exposureCount;
                    private transient ValueState<Long> clickCount;

                    @Override
                    public void open(Configuration parameters) {
                        exposureCount = getRuntimeContext().getState(
                                new ValueStateDescriptor<>("exposure", Long.class));
                        clickCount = getRuntimeContext().getState(
                                new ValueStateDescriptor<>("click", Long.class));
                    }

                    @Override
                    public String map(UserEvent event) throws Exception {
                        Long expo = exposureCount.value();
                        Long click = clickCount.value();
                        if (expo == null) expo = 0L;
                        if (click == null) click = 0L;

                        if ("exposure".equals(event.eventType)) expo++;
                        if ("click".equals(event.eventType)) click++;

                        exposureCount.update(expo);
                        clickCount.update(click);

                        double ctr = expo > 0 ? (double) click / expo : 0.0;
                        return String.format("user=%s, ctr=%.4f, expo=%d, click=%d",
                                event.userId, ctr, expo, click);
                    }
                });

        ctrFeatures.print();

        env.execute("Chapter37-FeatureEngineering");
    }

    public static class UserEvent {
        public String userId, eventType;
        public long timestamp;
        public UserEvent() {}
        public UserEvent(String uid, String type, long ts) {
            this.userId = uid; this.eventType = type; this.timestamp = ts;
        }
    }
}
```

#### 步骤2：Embedded模型推理——加载ONNX模型

**目标**：在RichMapFunction中加载ONNX模型并做实时推理。

```java
package com.flink.column.chapter37.ml;

import org.apache.flink.api.common.functions.RichMapFunction;
import org.apache.flink.configuration.Configuration;
import ai.onnxruntime.*;
import java.util.Collections;

/**
 * Embedded ONNX模型推理
 * 模型路径通过构造器传入
 */
public class OnnxInferenceFunction extends RichMapFunction<FeatureVector, Double> {

    private final String modelPath;
    private transient OrtSession session;
    private transient OrtEnvironment env;

    public OnnxInferenceFunction(String modelPath) {
        this.modelPath = modelPath;
    }

    @Override
    public void open(Configuration parameters) {
        try {
            env = OrtEnvironment.getEnvironment();
            session = env.createSession(modelPath, new OrtSession.SessionOptions());
            logger.info("ONNX模型加载完成: {}, 输入节点: {}",
                    modelPath, session.getInputNames());
        } catch (OrtException e) {
            throw new RuntimeException("加载ONNX模型失败", e);
        }
    }

    @Override
    public Double map(FeatureVector features) throws Exception {
        // 1. 准备输入Tensor
        float[] inputArr = features.toFloatArray();
        OnnxTensor inputTensor = OnnxTensor.createTensor(env, inputArr,
                new long[]{1, inputArr.length});

        // 2. 运行推理
        OrtSession.Result result = session.run(
                Collections.singletonMap("input", inputTensor));

        // 3. 解析输出
        float[][] output = (float[][]) result.get(0).getValue();
        double score = output[0][0];  // 二分类模型的得分

        inputTensor.close();
        result.close();

        return score;
    }

    @Override
    public void close() {
        try {
            if (session != null) session.close();
        } catch (OrtException e) {
            // ignore
        }
    }
}
```

#### 步骤3：Remote模型推理——AsyncIO调用TF Serving

**目标**：通过AsyncIO异步调用远程模型服务，不阻塞Flink主线程。

```java
package com.flink.column.chapter37.ml;

import org.apache.flink.configuration.Configuration;
import org.apache.flink.streaming.api.functions.async.ResultFuture;
import org.apache.flink.streaming.api.functions.async.RichAsyncFunction;
import org.apache.http.client.methods.HttpPost;
import org.apache.http.entity.StringEntity;
import org.apache.http.impl.nio.client.CloseableHttpAsyncClient;
import org.apache.http.impl.nio.client.HttpAsyncClients;
import org.apache.http.util.EntityUtils;
import com.google.gson.JsonObject;
import java.util.Collections;
import java.util.concurrent.CompletableFuture;

/**
 * 异步调用TF Serving做在线推理
 */
public class TFServingAsyncFunction extends RichAsyncFunction<FeatureVector, Double> {

    private final String endpoint;
    private transient CloseableHttpAsyncClient httpClient;

    public TFServingAsyncFunction(String endpoint) {
        this.endpoint = endpoint;
    }

    @Override
    public void open(Configuration parameters) {
        httpClient = HttpAsyncClients.createDefault();
        httpClient.start();
    }

    @Override
    public void asyncInvoke(FeatureVector input, ResultFuture<Double> resultFuture) {
        HttpPost request = new HttpPost(endpoint);
        request.setHeader("Content-Type", "application/json");

        // 构造TF Serving请求体
        JsonObject body = new JsonObject();
        body.addProperty("instances", input.toJsonArray().toString());
        request.setEntity(new StringEntity(body.toString(), "UTF-8"));

        // 异步发送
        CompletableFuture<Double> future = new CompletableFuture<>();
        httpClient.execute(request, response -> {
            String responseBody = EntityUtils.toString(response.getEntity());
            double score = parseScore(responseBody);
            future.complete(score);
        });

        // 回调
        future.whenComplete((score, throwable) -> {
            if (throwable != null) {
                resultFuture.completeExceptionally(throwable);
            } else {
                resultFuture.complete(Collections.singleton(score));
            }
        });
    }

    private double parseScore(String responseBody) {
        // 解析TF Serving的JSON响应
        return 0.5;  // 简化
    }

    @Override
    public void close() throws Exception {
        if (httpClient != null) httpClient.close();
    }
}
```

#### 步骤4：特征存储到Redis供线上服务使用

**目标**：将计算好的特征写入Redis，供在线服务实时查询。

```java
// Sink到Redis（使用本章前面写的RedisSink）
DataStream<String> features = ...;  // 特征向量
features.addSink(new RichSinkFunction<String>() {
    private transient jedis.Jedis jedis;

    @Override
    public void open(Configuration parameters) {
        jedis = new jedis.Jedis("redis", 6379);
    }

    @Override
    public void invoke(String featureStr, Context context) {
        String[] parts = featureStr.split("=", 2);
        jedis.set("feature:" + parts[0], parts[1]);
        jedis.expire("feature:" + parts[0], 3600);  // TTL 1小时
    }

    @Override
    public void close() { if (jedis != null) jedis.close(); }
});
```

### 可能遇到的坑

1. **ONNX模型加载后Session不能被多线程共享？**
   - 根因：OrtSession可以多线程推理，但必须确保`env.createSession()`只调用一次
   - 解方：在open()中创建，单例

2. **Remote推理的Http连接池配置不当导致连接不够**
   - 根因：AsyncIO的并发度 > HTTP连接池的最大连接数
   - 解方：HttpAsyncClient设置`setMaxConnTotal(200)`；或增加模型服务的副本数

3. **特征计算时的State TTL设置不当导致特征陈旧**
   - 根因：用户长期不活跃，State中的特征数据还是一个月前的
   - 解方：合理的TTL（如7天）；配合用户活跃度标记做特征重置

---

## 4. 项目总结

### ML/Flink集成模式

| 模式 | 延迟 | 模型大小限制 | 适用场景 |
|------|------|-------------|---------|
| Embedded ONNX | 微秒级 | <100MB | CTR预估、逻辑回归 |
| Embedded PMML | 微秒级 | <500MB | XGBoost、随机森林 |
| Remote TF Serving | 毫秒级 | 不限 | 深度学习模型 |
| Remote PMML Server | 毫秒级 | 不限 | 复杂集成模型 |

### 实时特征工程Pipeline

```
Kafka (用户行为) → Flink
    ├── 时间窗口特征（最近5min点击率）
    ├── 累积统计特征（用户总曝光）
    ├── 序列特征（最近10条浏览序列）
    └── 交叉特征（点击品类分布）
        │
        ▼
    Redis (特征存储) → 在线服务查询
    PMML/ONNX (模型推理) → 推荐结果 → Kafka
```

### 注意事项
- Embedded模型推理时，模型的加载在open()中——不要放在map/eval方法中
- 特征数据写入Redis时一定设置TTL——避免无效特征长期占用
- 实时特征计算的并行度受Redis写入并发限制——注意连接池

### 优点 & 缺点

| | Flink实时特征工程 + ML推理 | 离线批处理特征计算 |
|------|-----------|-----------|
| **优点1** | 毫秒级特征更新，用户行为即到即算 | 分钟~小时级延迟 |
| **优点2** | 支持Embedded/Remote两种推理模式，灵活适配模型大小 | 模型加载无内存限制 |
| **优点3** | State + Window天然适合时间窗口特征计算 | 需手动实现窗口逻辑 |
| **缺点1** | Embedded大模型（>100MB）加载到每个TM，内存压力大 | 模型独立部署，无内存冲突 |
| **缺点2** | 模型版本更新需重启Flink作业 | 热加载模型，版本更新无感 |

### 适用场景

**典型场景**：
1. 实时推荐系统——用户行为实时计算特征，毫秒级CTR预估
2. 实时风控模型——交易发生时即时推理欺诈分数
3. 在线特征存储——将计算好的实时特征写入Redis供线上服务查询
4. 冷启动特征生成——新用户实时计算初始化特征

**不适用场景**：
1. 超大模型（>1GB）推理——Flink TM内存有限，应使用独立推理服务
2. 不需要实时的业务场景——离线批处理成本更低

### 思考题

1. Embedded推理（ONNX）和Remote推理（TF Serving）之间怎么选？如果模型是BERT（500MB），延迟要求100ms以内——应该用哪种方式？

2. 实时特征工程中，"累积统计特征"（如用户总登录次数）会随时间无限增长。怎么控制这个特征的值范围？直接存BigInt还是做归一化或对数变换？

---

> **完整代码**：本章完整代码请参考附录或访问 https://github.com/flink-column/flink-practitioner  
> **思考题答案**：见附录文件 `appendix-answers.md`
