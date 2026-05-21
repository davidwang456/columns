# 第20章：Stream实战可靠消息消费组与幂等处理

## 1. 项目背景

订单系统里有很多“主流程之后”的动作：下单成功后发优惠券、通知仓库、推送短信、刷新用户积分、写运营报表。如果这些动作都放在下单接口里同步执行，一个短信服务超时就可能拖慢整个下单链路。早期我们可以用 List 做简单队列，也可以用 Pub/Sub 做广播，但 List 缺少完善的消费确认和重投机制，Pub/Sub 不保存消息，消费者不在线就会丢。

Redis Stream 提供了更接近消息队列的能力：消息有 ID，流可以持久保存，消费组能让多个消费者分工处理，Pending List 记录已投递但未确认的消息，`XCLAIM` 和 `XAUTOCLAIM` 可以把超时消息转给其他消费者。它不等于 Kafka，也不适合无限大规模日志平台，但很适合中小系统里的订单事件、异步任务、轻量审计和可靠通知。

本章实战目标：实现订单事件流，支持生产消息、创建消费组、多消费者读取、确认、查看积压、失败重试、死信处理和幂等落库。重点仍然是业务闭环：消息至少处理一次，因此消费者必须幂等。

## 2. 项目设计

小胖先说：“我以前用 `LPUSH`、`BRPOP` 做队列，也能异步处理，为啥还要 Stream？”

小白回答：“List 可以阻塞取消息，但消息被取走后如果消费者宕机，就不好知道这条消息到底处理没处理。Stream 有消费组和 Pending List，能看到谁拿了消息还没确认。”

大师写下第一条技术映射：“Stream = 可追加的消息流；Consumer Group = 一组消费者共同消费同一份流；Pending List = 已投递未确认的消息账本。可靠处理的核心不是不失败，而是失败后能发现、能重试、能幂等。”

小胖问：“那消费组是不是每个消费者都能收到全部消息？”

小白说：“同一个消费组内是竞争消费，一条消息通常分给一个消费者。不同消费组之间互不影响，比如订单组处理落库，积分组处理积分。”

大师补充：“技术映射：同一条 Stream 可以服务多个业务视角。`order-db-group` 关心创建订单，`coupon-group` 关心发券，`notify-group` 关心短信，它们各自维护消费进度。”

小胖继续问：“消息失败了怎么办？一直卡在 Pending 里？”

大师说：“消费者处理成功后必须 `XACK`。如果超时没 ACK，巡检任务用 `XPENDING` 查出来，再用 `XAUTOCLAIM` 转给健康消费者。多次失败的消息进入死信队列，等待人工或补偿程序。”

小白追问：“既然会重试，重复处理不可避免。比如发券不能发两张。”

大师点头：“所以幂等是 Stream 实战的底线。用 `eventId` 或业务唯一键建幂等表，先判断是否处理过，再执行业务动作。Redis 负责投递，业务负责幂等。”

## 3. 项目实战

启动 Redis：

```bash
docker run --name redis-stream-20 -p 6379:6379 -d redis:8.6
docker exec -it redis-stream-20 redis-cli
```

第一步，写入订单事件：

```bash
XADD mall:stream:order-events * eventId evt-1001 orderId 90001 userId 7 type ORDER_CREATED amount 299
XADD mall:stream:order-events * eventId evt-1002 orderId 90002 userId 8 type ORDER_CREATED amount 129
XRANGE mall:stream:order-events - +
```

消息 ID 默认由 Redis 生成，格式类似 `1740000000000-0`。业务上仍建议带 `eventId`，用于幂等。

第二步，创建消费组：

```bash
XGROUP CREATE mall:stream:order-events order-db-group 0 MKSTREAM
XGROUP CREATE mall:stream:order-events notify-group 0 MKSTREAM
```

如果组已存在会报错，实际程序应忽略 `BUSYGROUP`。

第三步，消费者读取消息：

```bash
XREADGROUP GROUP order-db-group c1 COUNT 10 BLOCK 2000 STREAMS mall:stream:order-events >
```

处理成功后确认：

```bash
XACK mall:stream:order-events order-db-group 1740000000000-0
```

第四步，查看积压和 Pending：

```bash
XLEN mall:stream:order-events
XINFO GROUPS mall:stream:order-events
XPENDING mall:stream:order-events order-db-group
XPENDING mall:stream:order-events order-db-group - + 10
```

第五步，超时消息转移。假设 `c1` 拿到消息后宕机，`c2` 可以自动认领空闲超过 60 秒的消息：

```bash
XAUTOCLAIM mall:stream:order-events order-db-group c2 60000 0-0 COUNT 10
```

处理仍失败时写入死信流：

```bash
XADD mall:stream:order-deadletter * source order-events eventId evt-1001 reason "db_timeout" retry 5
```

第六步，幂等消费伪代码：

```text
consume(message):
  id = message.streamId
  eventId = message.eventId
  if exists processed_event(eventId):
    XACK stream group id
    return

  try:
    begin transaction
    insert processed_event(eventId) with unique key
    apply business change, such as create order projection
    commit
    XACK stream group id
  catch duplicate_event:
    XACK stream group id
  catch temporary_exception:
    do not ack, wait retry
  catch permanent_exception:
    XADD deadletter ...
    XACK stream group id
```

第七步，控制 Stream 长度。订单事件不能无限增长，学习环境可用近似裁剪：

```bash
XADD mall:stream:order-events MAXLEN ~ 10000 * eventId evt-1003 orderId 90003 type ORDER_CREATED
XTRIM mall:stream:order-events MAXLEN ~ 10000
```

常见坑：不要忘记 `XACK`，否则 Pending 越堆越多；不要把 `>` 和 `0` 混用，`>` 表示读新消息，历史 Pending 要按 ID 读取或 claim；不要只依赖 Stream ID 做业务幂等，跨系统重放时业务 `eventId` 更稳；不要无限保留消息，内存会持续上涨；不要把 Stream 当 Kafka 替代品承载海量日志和长期回放。

## 4. 项目总结

Stream 给 Redis 增加了可靠消息流能力，尤其适合订单事件、异步任务和中小规模业务解耦。它比 List 更容易做确认、重试和消费者组，比 Pub/Sub 更可靠，但仍然需要业务自己处理幂等、死信和监控。

优点：命令体系完整，生产和消费都简单；消费组支持水平扩展；Pending List 让失败可见；同一 Stream 可以服务多个业务组。缺点：运维和容量管理比普通缓存复杂；消费者不 ACK 会积压；消息至少处理一次，需要幂等；超大规模场景不如专业消息队列。

适用场景包括订单后置任务、异步通知、轻量事件总线、失败可重试任务。不适合跨机房强一致消息、超长保留日志、复杂流计算和极高吞吐消息平台。

思考题：
1. 如果消费者已经完成数据库事务，但 `XACK` 前宕机，重试时如何避免重复落库？
2. 一个订单事件要同时通知仓库、积分和短信，应该用一个消费组还是多个消费组？

推广建议：开发团队封装生产者和消费者模板，强制 `eventId`、幂等表和死信流；测试团队模拟消费者宕机、重复投递和消息积压；运维团队监控 `XLEN`、`XPENDING`、消费者空闲时间和死信数量。可靠消息不是“不出错”，而是出错后每条消息都有账可查、有路可走。
