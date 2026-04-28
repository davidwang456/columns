# 第23章：CEP复杂事件处理——金融风控场景

---

## 1. 项目背景

某支付平台需要实时检测三类风险行为：

1. **暴力破解**：同一用户在1分钟内登录失败超过5次
2. **盗刷检测**：同一用户30分钟内在3个以上不同城市消费
3. **洗钱模式**：同一账户在10分钟内收到来自N个不同账户的小额转账后立即大额转出

这些行为的共同特征是：**无法通过单条事件判定，必须观察一段时间内的"事件序列模式"**。

这就是**CEP（Complex Event Processing，复杂事件处理）** 的领域。Flink的CEP库允许你定义事件序列的模式（Pattern），当输入流中的事件序列匹配该模式时触发告警。

Flink CEP支持的模式关系：

```
A → B           顺序（A后面跟着B）
A OR B          多选一
A AND B         同时
A → B+          循环（B出现1次或多次）
A → B{2,5}      循环（B出现2到5次）
A → B where ...  条件过滤
```

---

## 2. 项目设计

> 场景：风控运营在群里说——"为什么用户登录失败5次没报警？你们不是说有实时检测吗？"

**小胖**：我在flink里统计了登录失败次数，超过5次就告警。但用户是1分钟内连续失败5次，1分钟过去后累积了5次的count才触发——这时候用户早就不试了，报警还有什么意义？

**大师**：你用的是窗口聚合，需要等窗口结束才输出结果。CEP的模型不同——它不是"先攒够数据再算"，而是**每来一条数据就检查"到目前为止的事件序列是否匹配模式"**。一旦匹配就立刻触发，不需要等窗口结束。

**技术映射：CEP = 事件序列在时间维度上的"正则匹配"。每个事件到达时，NFA自动机尝试将新事件与当前状态的状态转换条件匹配，匹配成功则推进状态。**

**小白**：那如果用户在第3次失败后停了，之前监测到的"2次失败"的状态还存在吗？会占用多少内存？

**大师**：CEP将每个key（如userId）的当前匹配状态保存在状态中。每个key的每个活跃模式都需要占用内存。如果同时追踪100万个userId，每个id有1个活跃模式，每个模式约100字节——总共约100MB，在RocksDB中完全可接受。

**技术映射：CEP状态 = NFA自动机的当前状态 + 已匹配的部分事件。每个key一个NFA实例。状态大小取决于模式复杂度和key的数量。**

---

## 3. 项目实战

### 环境准备

```xml
<dependency>
    <groupId>org.apache.flink</groupId>
    <artifactId>flink-cep</artifactId>
    <version>${flink.version}</version>
</dependency>
```

### 分步实现

#### 步骤1：全局规则——登录失败次数检测

**目标**：检测同一用户在1分钟内登录失败≥5次。

```java
package com.flink.column.chapter23;

import org.apache.flink.api.common.eventtime.WatermarkStrategy;
import org.apache.flink.cep.CEP;
import org.apache.flink.cep.PatternSelectFunction;
import org.apache.flink.cep.PatternStream;
import org.apache.flink.cep.pattern.Pattern;
import org.apache.flink.cep.pattern.conditions.SimpleCondition;
import org.apache.flink.streaming.api.datastream.DataStream;
import org.apache.flink.streaming.api.environment.StreamExecutionEnvironment;
import org.apache.flink.streaming.api.windowing.time.Time;
import java.time.Duration;
import java.util.List;
import java.util.Map;

/**
 * CEP风控：检测登录失败超过5次
 * 输入: <userId>,<eventType>,<ip>,<timestamp>
 * eventType: LOGIN_SUCCESS / LOGIN_FAIL / PAY / TRANSFER
 */
public class LoginFailCep {

    public static void main(String[] args) throws Exception {
        StreamExecutionEnvironment env = StreamExecutionEnvironment.getExecutionEnvironment();
        env.setParallelism(1);

        DataStream<String> source = env.socketTextStream("localhost", 9999);

        DataStream<LoginEvent> events = source
                .map(line -> {
                    String[] p = line.split(",");
                    return new LoginEvent(p[0], p[1], p[2], Long.parseLong(p[3]));
                })
                .assignTimestampsAndWatermarks(
                        WatermarkStrategy.<LoginEvent>forBoundedOutOfOrderness(
                                        Duration.ofSeconds(10))
                                .withTimestampAssigner((e, ts) -> e.timestamp)
                );

        // ========== 定义模式 ==========
        // 模式：连续的LOGIN_FAIL事件，在1分钟内出现至少5次
        Pattern<LoginEvent, LoginEvent> failPattern = Pattern
                .<LoginEvent>begin("first-fail")
                .where(new SimpleCondition<LoginEvent>() {
                    @Override
                    public boolean filter(LoginEvent event) {
                        return "LOGIN_FAIL".equals(event.eventType);
                    }
                })
                .times(5)                          // 至少5次
                .consecutive()                      // 严格连续（中间不能有成功）
                .within(Time.minutes(1));           // 1分钟内

        // ========== 应用模式 ==========
        PatternStream<LoginEvent> patternStream = CEP.pattern(
                events.keyBy(e -> e.userId),
                failPattern);

        // ========== 匹配结果 ==========
        DataStream<String> alerts = patternStream.select(
                new PatternSelectFunction<LoginEvent, String>() {
                    @Override
                    public String select(Map<String, List<LoginEvent>> pattern) {
                        List<LoginEvent> firstFails = pattern.get("first-fail");
                        LoginEvent first = firstFails.get(0);
                        LoginEvent last = firstFails.get(firstFails.size() - 1);
                        return String.format(
                                "[暴力破解告警] 用户=%s, 在%dms内连续失败%d次, IP=%s",
                                first.userId,
                                last.timestamp - first.timestamp,
                                firstFails.size(),
                                first.ip);
                    }
                });

        alerts.print();

        env.execute("Chapter23-LoginFailCep");
    }

    public static class LoginEvent {
        public String userId, eventType, ip;
        public long timestamp;
        public LoginEvent() {}
        public LoginEvent(String uid, String type, String ip, long ts) {
            this.userId = uid; this.eventType = type;
            this.ip = ip; this.timestamp = ts;
        }
        @Override
        public String toString() {
            return String.format("Login{user=%s, type=%s, ip=%s, time=%d}",
                    userId, eventType, ip, timestamp);
        }
    }
}
```

**测试数据**：

```
user1,LOGIN_FAIL,192.168.1.1,1000
user1,LOGIN_FAIL,192.168.1.1,2000
user1,LOGIN_SUCCESS,192.168.1.1,3000     # 成功了一次——打破了consecutive
user1,LOGIN_FAIL,192.168.1.1,4000
user1,LOGIN_FAIL,192.168.1.1,5000
user1,LOGIN_FAIL,192.168.1.1,6000
# 没有告警——因为中间有LOGIN_SUCCESS打破了连续性

user2,LOGIN_FAIL,10.0.0.1,10000
user2,LOGIN_FAIL,10.0.0.1,11000
user2,LOGIN_FAIL,10.0.0.1,12000
user2,LOGIN_FAIL,10.0.0.1,13000
user2,LOGIN_FAIL,10.0.0.1,14000
# [暴力破解告警] 用户=user2, 在4000ms内连续失败5次, IP=10.0.0.1
```

#### 步骤2：多城市消费检测

**目标**：同一用户在30分钟内，在≥3个不同城市消费。

```java
// 模式：依次出现不同城市的PAY事件（城市不同即可）
Pattern<LoginEvent, ?> cityPattern = Pattern
    .<LoginEvent>begin("first-pay")
    .where(e -> "PAY".equals(e.eventType))
    .next("second-pay")                    // 严格连续（下一个事件）
    .where(e -> "PAY".equals(e.eventType))
    .where(new SimpleCondition<LoginEvent>() {
        @Override
        public boolean filter(LoginEvent e) {
            // 需要与第一个事件的IP不同（简化：IP前2段不同==不同城市）
            return !e.ip.startsWith(getFirstEventIp());
        }
    })
    .next("third-pay")
    .where(e -> "PAY".equals(e.eventType))
    .where(e -> !e.ip.startsWith(getFirstEventIp())
             && !e.ip.startsWith(getSecondEventIp()))
    .within(Time.minutes(30));

// 注意：上面的getFirstEventIp()等需要在PatternSelectFunction中实现
// 生产场景更推荐使用迭代条件（IterativeCondition）实现跨事件的比较
```

#### 步骤3：使用IterativeCondition跨事件判断

**目标**：用IterativeCondition实现更复杂的跨事件条件。

```java
Pattern<LoginEvent, ?> pattern = Pattern
    .<LoginEvent>begin("events")
    .where(new IterativeCondition<LoginEvent>() {
        @Override
        public boolean filter(LoginEvent current, Context<LoginEvent> ctx) {
            // 检查当前事件与已匹配事件的关系
            // ctx.getEventsForPattern("events") 获取已匹配的事件列表
            for (LoginEvent matched : ctx.getEventsForPattern("events")) {
                if (matched.ip.equals(current.ip)) {
                    return false;  // IP重复——不要
                }
            }
            return true;
        }
    })
    .times(3)        // 3个不同IP的转账
    .within(Time.minutes(30));
```

#### 步骤4：SQL方式（Flink SQL中的MATCH_RECOGNIZE）

**目标**：使用SQL的MATCH_RECOGNIZE语法实现CEP，无需Java代码。

```sql
SELECT *
FROM login_events
MATCH_RECOGNIZE (
    PARTITION BY userId
    ORDER BY ts
    MEASURES
        FIRST(A.timestamp) AS start_time,
        LAST(Z.timestamp) AS end_time,
        COUNT(A.userId) AS fail_count
    ONE ROW PER MATCH
    PATTERN (A{5,}) WITHIN INTERVAL '1' MINUTE
    DEFINE
        A AS A.eventType = 'LOGIN_FAIL'
) AS fail_pattern
WHERE fail_count >= 5;
```

### 可能遇到的坑

1. **CEP模式的`consecutive()`和`next()`的行为混淆**
   - `consecutive()`：严格连续，中间不能有任何其他匹配条件的事件。用于"连续5次登录失败"
   - `next()`：下一个匹配，中间可以有不匹配条件的事件。用于"3个不同城市的消费，允许中间有登录事件"
   - `followedBy()`：宽松跟随，中间可以跳过不匹配的事件。最宽松

2. **CEP模式的重复次数过多导致NFA状态膨胀**
   - 根因：`.times(100)`会导致NFA有100个状态
   - 解方：限制重复次数（建议≤20次）；用`until()`提前终止

3. **CEP的`within`时间与Watermark配合问题**
   - 根因：`within`设置的时间在ProcessingTime下是Flink时钟，EventTime下是基于Watermark推进的。如果Watermark不推进，`within`不会触发超时
   - 解方：确保有Watermark策略；或使用`within` + `sideOutputLateData`捕获超时

---

## 4. 项目总结

### CEP模式语法速查

| 语法 | 含义 | 示例 |
|------|------|------|
| `begin("name")` | 模式开始 | 定义第一个事件 |
| `where(condition)` | 事件条件 | `e -> e.type == "FAIL"` |
| `times(n)` | 重复次数 | `.times(5)` 恰好5次 |
| `times(n, m)` | 重复范围 | `.times(2, 5)` 2-5次 |
| `consecutive()` | 严格连续 | 中间不能有事件 |
| `next()` | 严格下一个 | 下一个必须是这个条件 |
| `followedBy()` | 宽松跟随 | 中间可跳过 |
| `notNext()` | 否定下一个 | 下一个不能是 |
| `within(time)` | 时间约束 | 模式必须在时间内匹配 |

### CEP适用场景

- 金融风控：暴力破解、盗刷、洗钱模式、异常交易序列
- 运维监控：日志故障模式的连续出现、告警风暴检测
- IoT：传感器异常序列（温度持续升高→压力突变→设备告警）
- 用户行为：漏斗分析（浏览→加购→下单→支付）

### 注意事项
- CEP的Pattern定义必须在作业图构建时完成——不支持运行时动态修改
- 对于大规模（百万级key）的CEP，确保使用RocksDB State Backend
- `within()`最好小于等于Checkpoint间隔的2倍——否则模式匹配到一半时作业恢复可能会丢失状态

### 常见踩坑经验

**案例1：CEP模式匹配结果为空，但数据明显满足条件**
- 根因：`consecutive()`要求严格连续，中间一条不匹配就中断；应改为`times(5).allowCombinations()`（允许不连续的组合）
- 解方：根据业务选择`consecutive()` / `allowCombinations()` / 默认

**案例2：CEP的超时事件没有触发**
- 根因：CEP的超时依赖于`within()` + Timer。如果Timer没有注册或作业忙碌，超时不会触发
- 解方：使用`PatternStream.select(patternTimeoutFunction, patternSelectFunction)`同时处理超时和正常匹配

**案例3：CEP内存溢出——模式未关闭导致状态无限增长**
- 根因：`times(1).optional()`模式是"可选出现一次"，NFA会在"匹配"和"不匹配"两个状态间徘徊，状态得不到释放
- 解方：避免使用`optional()`，或确保模式有终止条件（`within`或`times(n)`的n足够大）

### 优点 & 缺点

| | Flink CEP（复杂事件处理） | 窗口聚合+条件过滤（简化方案） |
|------|-----------|-----------|
| **优点1** | 事件序列模式匹配，支持复杂时序逻辑 | 只能做简单计数/聚合，无序列语义 |
| **优点2** | 逐事件匹配，模式满足立即触发，不需等窗口结束 | 需等窗口结束才出结果，延迟大 |
| **优点3** | 支持连续/不连续/可选/否定等多种模式关系 | 无法表达序列关系 |
| **缺点1** | Pattern复杂度增加时NFA状态膨胀，内存开销大 | 实现简单，状态可控 |
| **缺点2** | 模式`within`与Watermark配合需精细调参 | 窗口触发时间明确，调参简单 |
| **缺点3** | `optional`等高级模式使用不当易OOM | 无OOM风险 |

### 适用场景

**典型场景**：
1. 金融风控——暴力破解检测、盗刷检测、洗钱模式识别
2. 运维监控——故障模式连续出现、告警风暴检测
3. IoT异常检测——传感器异常序列（温度骤升→压力突变）
4. 用户行为漏斗——浏览→加购→下单→支付完整转化分析

**不适用场景**：
1. 简单阈值告警——计数超过N即可，用窗口聚合+filter更简单
2. 模式固定且key数量极多（>1000万）——NFA状态开销过大

### 思考题

1. CEP模式和普通的窗口聚合有什么区别？例如"1分钟内登录失败5次"既可以用CEP也可以先用窗口count再做filter。在什么场景下必须用CEP？

2. `consecutive()`和`allowCombinations()`的区别是什么？假设模式是`begin("a").times(3)`，事件序列是`[A, B, A, A]`（A匹配、B不匹配）。consecutive模式下能匹配吗？allowCombinations模式下呢？

---

> **完整代码**：本章完整代码请参考附录或访问 https://github.com/flink-column/flink-practitioner  
> **思考题答案**：见附录文件 `appendix-answers.md`
