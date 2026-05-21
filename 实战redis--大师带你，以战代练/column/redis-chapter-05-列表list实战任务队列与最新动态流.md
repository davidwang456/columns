# 第5章：列表 List 实战：任务队列与最新动态流

## 1. 项目背景

订单系统有一个常见需求：用户下单后，主链路要尽快返回成功，但还需要发送短信、推送站内信、记录操作日志、通知积分系统。如果所有事情都同步完成，订单接口会被外部短信服务、通知服务和日志写入拖慢。一旦短信接口抖动，用户下单体验也会受到影响。

最直接的优化方式是引入异步任务队列。Redis List 提供了从左侧或右侧插入、弹出元素的能力，可以很轻量地实现队列和栈。对初级实战来说，List 是理解异步削峰的好入口：生产者把任务放进队列，消费者阻塞等待并处理任务，主链路不再被慢任务拖住。

同时，List 也适合保存“最新 N 条”数据，比如用户最近浏览记录、系统最新操作日志、商品最新评论摘要。通过 `LPUSH` 加 `LTRIM`，可以让列表始终保持固定长度。

本章会实现两个功能：订单异步通知队列，以及最新 100 条操作日志。

## 2. 项目设计

小胖说：“发短信就调用短信接口呗，为什么要先进队列？”

小白问：“如果短信服务慢了，订单接口是不是也慢？如果短信服务短暂不可用，订单还能不能成功？”

大师解释：“订单创建是核心链路，短信通知是旁路动作。核心链路应该尽量短，把可延迟的事情放进队列。就像餐厅先给你下单号，后厨慢慢做，不会让你站在收银台等所有菜都炒完。”

技术映射：`RPUSH queue value` 作为生产者入队，`BLPOP queue timeout` 作为消费者阻塞取任务。

小胖问：“List 能当消息队列，那还要 Kafka、RabbitMQ 干嘛？”

大师回答：“List 能做轻量队列，但可靠性能力有限。比如消费者取走任务后宕机，消息就丢了；没有消费组，也不适合复杂订阅和重放。Redis 后面有 Stream，Kafka 又更适合大规模日志和事件平台。List 的价值是简单、轻量、低成本。”

技术映射：List 队列适合简单异步任务，不适合作为强可靠消息系统。

小白继续追问：“那失败重试怎么做？”

大师说：“初级方案可以准备一个失败队列。消费者处理失败后，把任务写入 `queue:failed` 或带重试次数后重新入队。更严谨的方案需要处理中间状态、幂等和超时恢复，这会在 Stream 章节深入。”

## 3. 项目实战

### 3.1 订单通知队列

生产者写入任务：

```bash
RPUSH queue:order:notice "{\"orderId\":1001,\"userId\":2001,\"type\":\"sms\"}"
RPUSH queue:order:notice "{\"orderId\":1002,\"userId\":2002,\"type\":\"site_message\"}"
LLEN queue:order:notice
```

消费者阻塞读取：

```bash
BLPOP queue:order:notice 10
```

如果队列有元素，立即返回 key 和任务内容；如果没有元素，最多阻塞 10 秒。

### 3.2 Java 风格消费者伪代码

```java
while (running) {
    Task task = redis.blpop("queue:order:notice", Duration.ofSeconds(10));
    if (task == null) {
        continue;
    }

    try {
        noticeService.send(task);
    } catch (Exception e) {
        redis.rpush("queue:order:notice:failed", task.toJson());
    }
}
```

这个消费者模型非常直观，但要注意：`BLPOP` 取出任务后，如果进程宕机，任务不会自动回到队列。因此它适合可补偿、可重试、允许少量人工修复的轻量任务。

### 3.3 失败队列与重试次数

任务中增加重试次数：

```json
{"orderId":1001,"userId":2001,"type":"sms","retry":0}
```

处理失败时：
- retry 小于 3：递增 retry 后重新 `RPUSH` 回主队列。
- retry 大于等于 3：写入失败队列等待人工处理。

命令示例：

```bash
RPUSH queue:order:notice:failed "{\"orderId\":1001,\"reason\":\"sms timeout\"}"
LRANGE queue:order:notice:failed 0 10
```

### 3.4 最新 100 条操作日志

每次操作后写入日志：

```bash
LPUSH ops:latest "1001 创建订单"
LPUSH ops:latest "1002 支付成功"
LTRIM ops:latest 0 99
LRANGE ops:latest 0 9
```

`LPUSH` 把最新日志放在头部，`LTRIM` 保留前 100 条。这个组合适合首页动态、后台最近操作、用户最近访问记录。

### 3.5 队列与栈

队列模式：

```bash
RPUSH queue:a task1
RPUSH queue:a task2
LPOP queue:a
```

栈模式：

```bash
LPUSH stack:a task1
LPUSH stack:a task2
LPOP stack:a
```

队列先进先出，栈后进先出。业务上要先明确顺序语义，再选命令组合。

### 3.6 常见坑

第一，List 不适合无限增长。所有队列都必须有积压监控，比如 `LLEN queue:order:notice`。

第二，消费者处理必须幂等。短信可能重复发送，积分可能重复增加，这些都要靠业务唯一键控制。

第三，不要用 `LRANGE key 0 -1` 读取巨大队列，这会造成网络大响应和 Redis 阻塞。

## 4. 项目总结

List 是 Redis 中最容易拿来做“异步化”的数据结构。它能快速帮助系统从同步调用改造成生产者-消费者模型，也能保存最新 N 条动态数据。

优点：
- 命令简单，学习成本低。
- 支持阻塞弹出，消费者实现方便。
- 适合轻量任务队列和最新列表。
- `LPUSH + LTRIM` 能低成本维护固定长度列表。

缺点：
- 消息可靠性有限。
- 没有消费组和消息确认机制。
- 队列积压后需要额外监控和扩容策略。
- 大范围 `LRANGE` 有阻塞风险。

适用场景：短信通知、站内信、轻量异步任务、最近浏览记录、最新操作日志。不适合金融级消息、复杂多消费者订阅、强可靠事件流。

思考题：
1. 为什么消费者处理任务必须设计成幂等？
2. List 队列和 Stream 消费组最大的差异是什么？
