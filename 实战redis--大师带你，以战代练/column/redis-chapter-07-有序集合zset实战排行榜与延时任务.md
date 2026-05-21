# 第7章：有序集合 ZSet 实战：排行榜与延时任务

## 1. 项目背景

运营团队要给电商 App 加两个功能：一个是“618 积分冲榜”，用户下单、评价、分享都能获得积分，页面实时展示前 100 名和自己的排名；另一个是“订单 30 分钟未支付自动关闭”，要求到点后尽快处理，不能靠人工巡检。

如果全部用数据库实现，排行榜要频繁 `order by score desc limit`，高峰期每次积分变化都触发排序，数据库压力很快上来。延时任务如果用数据库定时扫描，也会遇到两个问题：扫描间隔太短影响数据库，间隔太长订单关闭不及时。很多团队一开始用 `status=pending and expire_time < now()` 轮询，最后发现慢查询、锁等待和补偿逻辑越来越重。

Redis ZSet 同时拥有“成员唯一”和“按 score 排序”两个能力。排行榜可以把用户 ID 作为 member，把积分作为 score；延时任务可以把订单 ID 作为 member，把执行时间戳作为 score。这样我们就能用一套结构解决实时排序和到期扫描。

## 2. 项目设计

小胖一听排行榜就兴奋：“这不就是游戏积分榜吗？谁分高谁站前面，Redis 帮我排好队。”

小白提醒：“分数相同怎么办？用户要看自己的排名，不能每次只查前 100。历史日榜、周榜也要保留，score 设计会影响很多细节。”

大师说：“ZSet 适合这种既要查分数、又要查排名、还要按范围取数据的场景。它的 member 唯一，score 是浮点数，Redis 内部会维护有序结构，所以 `ZREVRANGE`、`ZRANK`、`ZSCORE` 都很自然。”

技术映射：排行榜 key 可以是 `rank:game:20260430`，member 是用户 ID，score 是积分；取高分榜用 `ZREVRANGE`，查名次用 `ZREVRANK`。

小胖追问：“订单超时也能用排行榜？订单又不是比赛。”

大师画了一条时间轴：“延时队列只是另一种排序。score 不放积分，放执行时间戳。越早到期的任务排越前，消费者定时取 `score <= now` 的订单处理。这个模型比数据库全表扫轻很多。”

小白继续追问：“如果两个消费者同时扫到一个订单，会不会重复关闭？如果关闭失败怎么办？”

大师回答：“ZSet 只负责到期发现，不负责业务幂等。消费者要先 `ZRANGEBYSCORE` 找到到期任务，再用 `ZREM` 抢占删除，谁删除成功谁处理。数据库关闭订单时仍要带状态条件，例如 `where status='WAIT_PAY'`，这样重复消费也不会出错。”

技术映射：Redis 做调度索引，数据库做最终状态约束；ZSet 延时队列要配合幂等处理。

小胖又问：“热榜是不是也能用 ZSet？点击一次加一分？”

大师说：“可以，但 score 不一定等于单一分数。热榜常把浏览、点赞、评论、时间衰减合成一个分值。ZSet 不理解业务含义，只负责按照 score 排序。你的 score 设计越清晰，榜单越稳定。”

小白补充：“所以本章重点不是背命令，而是学会把排序需求映射成 score。”

## 3. 项目实战

启动 Redis：

```bash
docker run --name redis-lab-07 -p 6379:6379 -d redis:8.6
docker exec -it redis-lab-07 redis-cli
```

### 3.1 游戏积分排行榜

初始化榜单：

```bash
ZADD rank:game:20260430 1200 1001 980 1002 1680 1003 1680 1004
ZREVRANGE rank:game:20260430 0 9 WITHSCORES
```

用户 1002 完成任务，加 300 分：

```bash
ZINCRBY rank:game:20260430 300 1002
ZSCORE rank:game:20260430 1002
ZREVRANK rank:game:20260430 1002
```

`ZREVRANK` 返回从 0 开始的排名，业务展示时通常加 1：

```text
displayRank = ZREVRANK(key, uid) + 1
```

分页查询第 1 页，每页 3 人：

```bash
ZREVRANGE rank:game:20260430 0 2 WITHSCORES
```

如果需要低分到高分，用 `ZRANGE`；如果需要高分到低分，用 `ZREVRANGE` 或 Redis 新版本的 `ZRANGE key start stop REV WITHSCORES`。

业务伪代码：

```text
addScore(uid, delta):
  key = "rank:game:" + today()
  newScore = ZINCRBY key delta uid
  rank = ZREVRANK key uid
  return {score: newScore, rank: rank + 1}

topN(n):
  return ZREVRANGE rankKey 0 n-1 WITHSCORES
```

并列名次是常见产品问题。Redis 默认按 score 排序，score 相同再按 member 字典序排序。如果业务要求“相同积分同名次”，就不能直接把 `ZREVRANK + 1` 当最终名次，需要按 score 再统计高于该 score 的人数：

```bash
ZSCORE rank:game:20260430 1003
ZCOUNT rank:game:20260430 (1680 +inf
```

高于 1680 分的人数加 1，就是并列规则下的展示名次。

### 3.2 历史榜单和清理

日榜、周榜、总榜最好分 key：

```bash
ZINCRBY rank:game:daily:20260430 10 1001
ZINCRBY rank:game:weekly:2026W18 10 1001
ZINCRBY rank:game:total 10 1001
EXPIRE rank:game:daily:20260430 2592000
```

日榜保留 30 天，总榜不设置过期。不要把所有日期塞进一个 ZSet 再靠 member 拼接，否则查询和清理都会复杂。

### 3.3 延时任务

订单 9001 在当前时间 30 分钟后关闭。为了演示，可以用具体时间戳：

```bash
# 这里假设 1714470000 是订单应关闭的 Unix 秒级时间戳
ZADD delay:order:close 1714470000 9001
ZADD delay:order:close 1714470060 9002
```

消费者扫描到期任务：

```bash
ZRANGEBYSCORE delay:order:close -inf 1714470000 LIMIT 0 10
ZREM delay:order:close 9001
```

只有 `ZREM` 返回 1 的消费者才真正处理订单。伪代码如下：

```text
pollCloseOrder():
  now = currentUnixSeconds()
  ids = ZRANGEBYSCORE delayKey -inf now LIMIT 0 50
  for id in ids:
    removed = ZREM delayKey id
    if removed == 1:
      closeOrderIfStillWaitingPay(id)
```

`closeOrderIfStillWaitingPay` 必须在数据库侧做幂等：

```sql
update orders
set status = 'CLOSED'
where id = ? and status = 'WAIT_PAY';
```

如果业务处理失败，可以重新放回 ZSet，score 设置为下一次重试时间：

```bash
ZADD delay:order:close 1714470300 9001
```

### 3.4 常见坑

第一，score 是双精度浮点数，不适合无限拼接复杂信息。积分、时间戳通常没问题，但把多个字段强行编码进 score 要谨慎。

第二，分页越往后越慢的体验问题仍然存在。`ZREVRANGE key 100000 100020` 虽然比数据库排序轻，但深分页仍会消耗资源。排行榜一般只展示前几页和个人附近排名。

第三，延时队列不是可靠消息队列。Redis 重启、消费者崩溃、处理失败都要有补偿。对账任务仍需要从数据库兜底扫描异常订单。

第四，删除历史榜单要按 key 生命周期处理，不要用 `ZREMRANGEBYSCORE` 误删仍要保留的历史数据。

## 4. 项目总结

ZSet 的关键是 score 设计。积分榜、热榜、时间线、延时任务，本质上都是“按一个数排序”。Redis 提供了排名、范围查询、分数自增和按分数删除，让实时排序类需求落地很顺。

它的边界也很明确：ZSet 只维护排序索引，不替代业务状态机；延时队列要配合数据库幂等，排行榜要提前定义并列规则、历史保留策略和深分页边界。对实时展示、轻量调度、活动榜单来说，ZSet 是 Redis 最值得熟练掌握的数据结构之一。

本章思考题：

1. 如果热榜需要“新内容有时间加权，老内容逐渐降权”，score 应该如何设计和定期更新？
2. 延时任务消费者在 `ZREM` 成功后宕机，订单没有关闭，你会设计什么补偿机制？
