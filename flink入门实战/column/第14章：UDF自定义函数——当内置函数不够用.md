# 第14章：UDF自定义函数——当内置函数不够用

---

## 1. 项目背景

Flink SQL内置了100+标准SQL函数：SUM、COUNT、AVG、TO_TIMESTAMP、REGEXP_EXTRACT等等。对于90%的场景，这些函数已经够用。

但总有剩下10%的场景需要"私人定制"：

- **地理围栏计算**：判断一个GPS坐标点是否在某个多边形区域内——需要实现Point-In-Polygon算法
- **IP归属地解析**：将客户端IP转换为省份/城市——需要加载IP库到算子内存
- **自定义聚合**：计算"过去1小时内每天首次登录的用户"——需要保留上一次登录时间
- **业务评分算法**：根据多个维度的评分加权计算最终信誉分——需要加载权重配置

这些需求无法用标准SQL函数表达。Flink提供了三种UDF（User-Defined Function）来覆盖所有场景：

- **ScalarFunction（标量函数）**：一行输入→一行输出，如 `my_ip_to_city(ip)`
- **TableFunction（表函数）**：一行输入→多行（表）输出，如 `my_split_keywords(text)`
- **AggregateFunction（聚合函数）**：多行输入→一行输出，如 `my_percentile(value, p)`

---

## 2. 项目设计

> 场景：BI小美需要一个新的SQL函数 `to_city(ip)`，在Flink SQL中直接调用。

**小胖**：这不就是查IP库吗？在SQL外面查完再写入Kafka不就好了？为什么要搞UDF？

**大师**：在外面预处理意味着多一次数据流转。如果今天要加 `to_city`，明天要加 `is_in_geo_fence`，后天要加 `calculate_score`——每加一个函数都要改ETL外层的Java代码，重新部署。UDF把函数注入到SQL运行时，BI分析师在SQL里直接调用，不需要打扰开发团队。**技术映射：UDF = 将业务逻辑注入SQL引擎，SQL执行时自动调用UDF方法。**

**小白**：UDF是怎么注册到Flink的？运行时性能怎么样？

**大师**：注册非常轻量：

```sql
CREATE TEMPORARY SYSTEM FUNCTION to_city AS 'com.myudf.IpToCityFunction';
```

注册后，所有Session中都可以直接调用。性能方面——UDF在Flink内部是逐行调用（每行数据到来时调用一次），所以UDF本身的性能至关重要。如果一次调用耗时1ms（如IP查询），每秒只能处理1000行。如果要提升吞吐，UDF内部必须做缓存和批量优化。

**技术映射：UDF性能瓶颈 = 函数本身的单次调用耗时。如果UDF内有网络IO（如远程调用），必须添加本地缓存，否则会拖垮整个作业吞吐。**

**小胖**：那AggregateFunction怎么写？比如我要一个"求第90百分位数"的自定义聚合。

**大师**：AggregateFunction需要定义一个**累加器（Accumulator）**——这是一个POJO，用于在聚合过程中保存中间结果。比如百分位数的累加器需要存储所有输入值的列表。

**技术映射：AggregateFunction三件套——① createAccumulator() 创建累加器 ② accumulate() 将输入加入累加器 ③ getResult() 输出最终结果。支持窗口的聚合还需要merge()方法合并不同分区的累加器。**

---

## 3. 项目实战

### 分步实现

#### 步骤1：ScalarFunction——IP归属地解析

**目标**：创建一个`to_city(ip: STRING): STRING`函数，将IP转换为城市名称。

```java
package com.flink.column.chapter14.udf;

import org.apache.flink.table.functions.FunctionContext;
import org.apache.flink.table.functions.ScalarFunction;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import java.io.BufferedReader;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.util.HashMap;
import java.util.Map;

/**
 * IP转城市：ScalarFunction
 * 使用内存中的IP段映射表（生产环境建议使用GeoIP2库）
 */
public class IpToCityFunction extends ScalarFunction {

    private static final Logger LOG = LoggerFactory.getLogger(IpToCityFunction.class);
    private transient Map<Long, String> ipMap;

    @Override
    public void open(FunctionContext context) {
        ipMap = new HashMap<>();
        // 模拟加载IP库（实际从外部文件加载）
        // 生产可加载 GeoLite2-City.mmdb 或自建IP段表
        loadIpDatabase();
        LOG.info("IP库加载完成，共{}条记录", ipMap.size());
    }

    /**
     * eval方法是ScalarFunction的入口——函数名固定为eval
     */
    public String eval(String ip) {
        if (ip == null || ip.isEmpty()) return "unknown";
        long ipNum = ipToLong(ip);
        // 找到对应的IP段
        String city = ipMap.get(ipNum);
        return city != null ? city : "unknown";
    }

    private long ipToLong(String ip) {
        String[] parts = ip.split("\\.");
        return (Long.parseLong(parts[0]) << 24) |
               (Long.parseLong(parts[1]) << 16) |
               (Long.parseLong(parts[2]) << 8)  |
               Long.parseLong(parts[3]);
    }

    private void loadIpDatabase() {
        // 示例数据：IP段→城市映射（实际从文件加载）
        ipMap.put(ipToLong("192.168.1.0"), "beijing");
        ipMap.put(ipToLong("10.0.0.0"), "shanghai");
        // 生产用GeoIP2或自建IP库
    }

    @Override
    public void close() {
        ipMap.clear();
        LOG.info("IP库已释放");
    }
}
```

**注册使用**：

```sql
CREATE TEMPORARY SYSTEM FUNCTION to_city AS 'com.flink.column.chapter14.udf.IpToCityFunction';

SELECT userId, to_city(clientIp) AS city, COUNT(*) AS pv
FROM page_views
GROUP BY userId, city;
```

#### 步骤2：ScalarFunction带缓存——减少重复计算

**目标**：为高基数字段添加缓存，避免相同的IP重复查库。

```java
public class IpToCityFunction extends ScalarFunction {
    private transient Cache<String, String> cache;

    @Override
    public void open(FunctionContext context) {
        cache = Caffeine.newBuilder()
                .maximumSize(100_000)
                .expireAfterWrite(1, TimeUnit.HOURS)
                .build();
        loadIpDatabase();
    }

    public String eval(String ip) {
        // 先查缓存
        String cached = cache.getIfPresent(ip);
        if (cached != null) return cached;

        // 缓存未命中，查IP库
        String city = lookupIp(ip);
        cache.put(ip, city);
        return city;
    }
}
```

> **注意**：Caffeine/Sod本地缓存需要作为编译期依赖打入fat jar。同时注意缓存大小——每个并行子任务都有自己的缓存实例，总内存 = 缓存大小 × 并行度。

#### 步骤3：TableFunction——将JSON字符串展开为多行

**目标**：创建`parse_json_array(jsonStr: STRING): ROW<key STRING, value STRING>`表函数，将JSON拆成多行。

```java
package com.flink.column.chapter14.udf;

import org.apache.flink.table.functions.TableFunction;
import org.apache.flink.types.Row;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * TableFunction：将JSON对象展开为多行（key, value）
 * 输入: {"a":"1","b":"2","c":"3"}
 * 输出: (a,1), (b,2), (c,3)
 */
public class JsonExplodeFunction extends TableFunction<Row> {

    private static final Logger LOG = LoggerFactory.getLogger(JsonExplodeFunction.class);

    // 方法名固定为eval
    public void eval(String jsonStr) {
        if (jsonStr == null || !jsonStr.startsWith("{")) return;

        try {
            String content = jsonStr.substring(1, jsonStr.length() - 1);
            for (String pair : content.split(",")) {
                String[] kv = pair.split(":", 2);
                if (kv.length == 2) {
                    String key = kv[0].trim().replace("\"", "");
                    String value = kv[1].trim().replace("\"", "");
                    collect(Row.of(key, value));
                }
            }
        } catch (Exception e) {
            LOG.warn("JSON解析失败: {}", jsonStr, e);
        }
    }
}
```

**注册使用**：

```sql
CREATE TEMPORARY SYSTEM FUNCTION json_explode AS 'com.flink.column.chapter14.udf.JsonExplodeFunction';

-- 使用 LATERAL TABLE 将TableFunction的每一行与原始行做交叉连接
SELECT userId, kv_key, kv_value
FROM page_views,
LATERAL TABLE(json_explode(properties)) AS T(kv_key, kv_value);
```

#### 步骤4：AggregateFunction——求百分位数

**目标**：实现自定义聚合函数 `percentile(value, p)`，返回输入值在p分位数上的值。

```java
package com.flink.column.chapter14.udf;

import org.apache.flink.table.functions.AggregateFunction;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;

/**
 * 百分位数聚合函数
 * 使用: SELECT percentile(amount, 0.5) AS median FROM orders GROUP BY category
 */
public class PercentileFunction extends AggregateFunction<Double, PercentileFunction.PercentileAccumulator> {

    // ========== 累加器：存储所有输入值和p参数 ==========
    public static class PercentileAccumulator {
        public List<Double> values = new ArrayList<>();
        public double p = 0.5;  // 默认中位数
    }

    @Override
    public PercentileAccumulator createAccumulator() {
        return new PercentileAccumulator();
    }

    // 累加方法
    public void accumulate(PercentileAccumulator acc, Double value, Double p) {
        acc.values.add(value);
        acc.p = p;
    }

    // 合并（用于并行窗口合并）
    public void merge(PercentileAccumulator acc, Iterable<PercentileAccumulator> it) {
        for (PercentileAccumulator other : it) {
            acc.values.addAll(other.values);
        }
    }

    @Override
    public Double getValue(PercentileAccumulator acc) {
        if (acc.values.isEmpty()) return 0.0;
        Collections.sort(acc.values);
        int index = (int) Math.ceil(acc.p * acc.values.size()) - 1;
        index = Math.max(0, Math.min(index, acc.values.size() - 1));
        return acc.values.get(index);
    }

    // 重置（用于窗口场景）
    public void resetAccumulator(PercentileAccumulator acc) {
        acc.values.clear();
    }
}
```

**注册使用**：

```sql
CREATE TEMPORARY SYSTEM FUNCTION percentile AS 'com.flink.column.chapter14.udf.PercentileFunction';

SELECT category, percentile(amount, 0.5) AS median_amount
FROM orders
GROUP BY category, TUMBLE(ts, INTERVAL '1' HOUR);
```

#### 步骤5：在Flink SQL中注册并调用UDF

**完整示例**：

```java
package com.flink.column.chapter14;

import org.apache.flink.streaming.api.environment.StreamExecutionEnvironment;
import org.apache.flink.table.api.bridge.java.StreamTableEnvironment;
import com.flink.column.chapter14.udf.IpToCityFunction;
import com.flink.column.chapter14.udf.PercentileFunction;

public class UDFRegistrationDemo {

    public static void main(String[] args) {
        StreamExecutionEnvironment env = StreamExecutionEnvironment.getExecutionEnvironment();
        StreamTableEnvironment tableEnv = StreamTableEnvironment.create(env);

        // 注册临时系统函数（全局可用）
        tableEnv.createTemporarySystemFunction("to_city", IpToCityFunction.class);
        tableEnv.createTemporarySystemFunction("percentile", PercentileFunction.class);

        // 现在可以在SQL中直接使用
        tableEnv.executeSql(
            "SELECT to_city(clientIp) AS city, COUNT(*) AS cnt " +
            "FROM page_views GROUP BY to_city(clientIp)"
        );
    }
}
```

### 可能遇到的坑

1. **UDF类找不到：ClassNotFoundException**
   - 根因：UDF类没有被包含在fat jar中（未被打包进shaded jar）
   - 解决：确保UDF类在`maven-shade-plugin`的includes范围内

2. **UDF中使用SimpleDateFormat线程不安全**
   - 根因：UDF的eval方法会被多线程并发调用（DataStream API环境）
   - 解方：eval中用局部变量（方法内new）或使用线程安全的DateTimeFormatter

3. **AggregateFunction的merge()不实现导致窗口聚合结果不正确**
   - 根因：Flink的并行窗口需要合并不同分区的累加器，merge不实现时合并为空
   - 解方：必须实现merge()方法

---

## 4. 项目总结

### 三种UDF对比

| 类型 | 输入→输出 | 典型示例 | 注册语法 |
|------|----------|---------|---------|
| ScalarFunction | 1行→1行 | to_city(ip) | `CREATE FUNCTION` |
| TableFunction | 1行→N行（展开） | json_explode(json) | `CREATE FUNCTION AS ...` |
| AggregateFunction | N行→1行 | percentile(v, p) | `CREATE AGGREGATE FUNCTION` |
| TableAggregateFunction | N行→M行 | topN函数 | `CREATE FUNCTION AS ...` |

### UDF生命周期

```java
open(FunctionContext ctx)  ← 初始化（加载资源、建缓存）
  │
  ▼
eval(...)                  ← 核心逻辑（每条数据 / 每个窗口调用）
  │
  ▼
close()                    ← 清理（关闭连接、释放资源）
```

### 注意事项
- UDF中**必须使用确定性实现**。如果你的UDF依赖随机数或当前时间，SQL优化器无法做缓存和常量折叠
- 不要在UDF中调用外部远程接口（如REST API）——一次调用可能阻塞数百毫秒，拖垮整个流水线
- 如果确实需要远程调用（如查Redis），使用AsyncIO（第22章）替代UDF

### 常见踩坑经验

**案例1：ScalarFunction中eval()方法抛出异常，整个作业崩溃**
- 根因：Flink SQL默认不吞UDF异常。eval中任何RuntimeException都会导致作业失败
- 解方：eval中使用try-catch包裹，捕获所有异常后返回默认值或null

**案例2：AggregateFunction的累加器序列化错误**
- 根因：累加器必须是POJO（可序列化），且需要有无参构造器
- 解方：确认累加器类是一个标准的POJO，所有字段都有getter/setter

**案例3：UDF中的本地缓存刷新的时机不可控**
- 根因：UDF没有"定时刷新"的回调。如果缓存过期了但UDF还在用旧数据
- 解方：使用Caffeine的`expireAfterWrite`做定时刷新；或配合广播状态实现运行时动态更新

### 优点 & 缺点

| | Flink UDF（Scalar/Table/Aggregate） | 应用层内联业务逻辑（预处理写入Kafka） |
|------|-----------|-----------|
| **优点1** | SQL中直接调用，BI分析师自助使用，无需开发介入 | 每次新增函数需改Java代码、编译、部署 |
| **优点2** | 注册即用，运行时动态加载，不影响线上作业 | 业务逻辑变更需整个ETL链路重新发布 |
| **优点3** | ScalarFunction/TableFunction/AggregateFunction三种类型覆盖全场景 | 内联逻辑表达能力受限于API |
| **优点4** | 支持open/close生命周期，可加载缓存和配置 | 无法复用已有的UDF生命周期管理 |
| **缺点1** | 逐行调用，性能敏感——UDF内一次远程调用拖垮全局 | 预处理可批量优化，能扛高吞吐 |
| **缺点2** | 调试困难——UDF内异常导致SQL查询失败 | 应用层代码可加日志和断点调试 |

### 适用场景

**典型场景**：
1. IP归属地解析——自定义ScalarFunction将IP转换为城市/省份
2. JSON嵌套字段展开——TableFunction将JSON数组拆为多行
3. 自定义聚合函数——Percentile、Median、Skewness等标准SQL不支持的聚合
4. 业务评分/规则计算——加载权重配置后对每条记录打分

**不适用场景**：
1. 高吞吐低延迟的简单字段变换——内置SQL函数可完成，无需自定义UDF
2. 需要跨记录状态的复杂逻辑——UDF是纯函数无状态，需用DataStream API的State

### 思考题

1. IpToCityFunction中用了Caffeine本地缓存。如果作业并行度=10，每个子任务维护一个独立的100000条IP缓存——总缓存占用的最大内存是多少？如果其中90%的IP是重复的（用户集中在少数城市），缓存命中率如何？可以用什么策略共享缓存？

2. PercentileFunction的累加器中存储了所有输入值的List，这在大量数据下会OOM。你能设计一种仅使用有限内存（如固定大小的"草图"结构）来近似计算百分位数的算法吗？（提示：GK算法 / TDigest）

---

> **完整代码**：本章完整代码请参考附录或访问 https://github.com/flink-column/flink-practitioner  
> **思考题答案**：见附录文件 `appendix-answers.md`
