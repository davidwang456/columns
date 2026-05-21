# 第14章：事务与管道：批量命令的正确姿势

## 1. 项目背景

运营后台要给一批用户补发积分。一次活动可能影响几千到几十万用户，如果应用逐条执行 `HINCRBY` 或 `ZINCRBY`，每条命令都要经历一次网络往返，耗时会被 RTT 放大。开发同学听说 Redis 有事务和 Pipeline，于是想把所有命令“打包执行”，但很快遇到新问题：Pipeline 是不是事务？事务里某条命令错了会不会自动回滚？账户余额并发更新能不能靠 Pipeline 保证安全？

这些概念混在一起很危险。Redis 事务关注命令队列和一次性执行，`WATCH` 可以做乐观锁；Pipeline 关注减少网络往返，提高批量吞吐；Lua 则适合把多步判断和修改做成原子动作。它们都能“批量处理”，但解决的是不同问题。

本章通过两个实战任务讲清楚边界：用 Pipeline 批量写入用户积分，观察网络往返优化；用 `WATCH`、`MULTI`、`EXEC` 实现账户余额的乐观锁更新；最后对比事务、Lua、Pipeline 的适用场景和错误处理方式。

## 2. 项目设计

小胖说：“我要给一万个人加积分，开个 Redis 事务不就行了？一把梭，失败还能回滚。”

小白提醒：“Redis 事务不是数据库事务。`MULTI` 后只是把命令排队，`EXEC` 时按顺序执行。命令执行失败通常不会自动回滚已经执行的命令。”

大师点头：“Redis 事务更像把一叠点菜单交给厨房：厨房按顺序做完，中间不会插入别人的菜，但如果某道菜材料不对，已经做好的菜不会倒回去。它保证队列执行的连续性，不提供关系数据库那种完整回滚。”

技术映射：`MULTI` 开始事务，命令进入队列，`EXEC` 执行队列，`DISCARD` 放弃队列。

小胖又问：“那 Pipeline 呢？我把一万条命令塞 Pipeline，是不是也不会被别人插队？”

小白说：“Pipeline 只是客户端一次发送多条命令，减少网络往返。服务端仍然按收到的命令顺序处理，但它不是事务语义，不能保证这一批命令中间没有其他客户端命令穿插。”

大师补充：“Pipeline 解决的是快不快，事务解决的是一组命令何时执行，Lua 解决的是多步业务逻辑原不原子。三者不要混用概念。”

技术映射：Pipeline 优化 RTT，适合批量写入、批量读取；事务和 Lua 才讨论原子性边界。

小胖继续追问：“余额扣减是不是用事务就行？先 `GET balance`，够了再 `DECRBY`。”

小白说：“并发下两个客户端都可能读到旧余额。需要 `WATCH account`，如果执行前 key 被别人改了，`EXEC` 返回空，客户端重试。”

大师总结：“`WATCH` 是乐观锁，适合冲突不高的 CAS 更新。冲突很高、逻辑短小的时候，用 Lua 更直接。余额这类关键链路通常还需要数据库作为最终事实，Redis 可以做辅助计数或限流。”

技术映射：`WATCH` 监视 key 版本变化，`EXEC` 前发现变化则事务失败。

## 3. 项目实战

### 3.1 MULTI 与 EXEC 基础

启动 Redis：

```bash
docker run --name redis-lab-14 -p 6379:6379 -d redis:8.6
docker exec -it redis-lab-14 redis-cli
```

执行事务：

```bash
MULTI
HINCRBY mall:point:user:1001 total 10
HINCRBY mall:point:user:1002 total 10
ZINCRBY mall:rank:point 10 user:1001
ZINCRBY mall:rank:point 10 user:1002
EXEC
```

查看结果：

```bash
HGETALL mall:point:user:1001
ZRANGE mall:rank:point 0 -1 WITHSCORES
```

如果在 `MULTI` 后发现参数准备错了，可以：

```bash
MULTI
SET mall:tmp:wrong 1
DISCARD
GET mall:tmp:wrong
```

`DISCARD` 会放弃尚未执行的队列。

### 3.2 事务错误处理

语法错误通常在入队阶段就会被发现，`EXEC` 不会执行队列。运行时错误则可能在 `EXEC` 结果中体现：

```bash
SET mall:point:user:bad "string-value"
MULTI
HINCRBY mall:point:user:1003 total 10
HINCRBY mall:point:user:bad total 10
HINCRBY mall:point:user:1004 total 10
EXEC
```

第二条可能返回 WRONGTYPE 错误，但前后两条仍可能成功。调用方必须逐项检查 `EXEC` 返回结果，不能只看有没有执行命令。

### 3.3 WATCH 乐观锁更新余额

先准备账户：

```bash
SET mall:account:user:1001 100
```

余额扣减伪代码：

```text
function deductBalance(userId, amount):
    key = "mall:account:user:" + userId
    for retry in 1..3:
        WATCH key
        balance = GET key
        if balance < amount:
            UNWATCH
            return "余额不足"

        MULTI
        DECRBY key amount
        EXEC
        if EXEC success:
            return "扣减成功"

        sleep(random small time)
    return "系统繁忙，请重试"
```

手动命令：

```bash
WATCH mall:account:user:1001
GET mall:account:user:1001
MULTI
DECRBY mall:account:user:1001 30
EXEC
GET mall:account:user:1001
```

如果在另一个客户端于 `WATCH` 后修改同一个 key：

```bash
INCRBY mall:account:user:1001 5
```

原客户端的 `EXEC` 会返回空，表示监视的 key 已被修改，事务未执行。

### 3.4 Pipeline 批量积分

Pipeline 不适合直接在 `redis-cli` 里完整展示，但业务流程很清楚：

```text
pipeline = redis.createPipeline()
for userId in userIds:
    pipeline.HINCRBY("mall:point:user:" + userId, "total", 10)
    pipeline.ZINCRBY("mall:rank:point", 10, "user:" + userId)
results = pipeline.execute()
for result in results:
    if result is error:
        recordFailure(result)
```

未使用 Pipeline：

```text
客户端 -> Redis: HINCRBY user1
Redis -> 客户端: OK
客户端 -> Redis: ZINCRBY user1
Redis -> 客户端: OK
... 重复很多次
```

使用 Pipeline：

```text
客户端 -> Redis: 一批 HINCRBY/ZINCRBY
Redis -> 客户端: 一批结果
```

批量大小要控制，比如每批 500 到 2000 条命令，避免客户端输出缓冲区过大，也避免单次响应占用太多内存。

### 3.5 三者选型对比

```text
只想减少网络往返：Pipeline
想排队执行一组命令：MULTI/EXEC
想判断后修改且必须原子：Lua
想并发下做 CAS：WATCH + MULTI/EXEC
```

积分补发多为批量吞吐问题，优先 Pipeline。余额扣减涉及判断和并发冲突，可以用 `WATCH` 或 Lua。秒杀库存这种冲突很高的场景，Lua 通常比客户端重试更直接。

### 3.6 常见坑

第一，把 Pipeline 当事务。Pipeline 中某条命令失败，不代表整批自动撤销。

第二，事务结果不逐项检查。`EXEC` 返回数组，每个元素都可能是正常结果或错误。

第三，`WATCH` 后业务逻辑太长。监视时间越长，冲突概率越高，重试越多。

第四，Pipeline 一次塞太多命令。吞吐提升不是无限的，批量过大可能造成内存峰值和延迟尖刺。

## 4. 项目总结

事务、管道、Lua 是 Redis 批量和原子场景的三把不同工具。Pipeline 解决网络往返，适合批量导入、批量更新、批量查询；事务提供命令队列和 `WATCH` 乐观锁，但不提供传统数据库式回滚；Lua 适合短小的服务端原子逻辑。真正的工程判断，是先问问题属于“性能”“并发冲突”还是“业务原子性”，再选工具。

适用场景：
- Pipeline：积分批量发放、缓存批量预热、批量读取用户状态。
- 事务：需要一组命令连续执行，且能接受无自动回滚语义。
- WATCH：低冲突 CAS 更新。
- Lua：高冲突、短逻辑、判断后修改。

不适用场景：
- 用 Redis 事务替代数据库事务管理订单支付。
- 用 Pipeline 包装需要强一致的复杂业务。
- 在事务或 Pipeline 中忽略单条命令错误。

思考题：
1. 为什么 Pipeline 能提升吞吐，却不能保证业务原子性？
2. 如果批量积分发放到一半客户端断开，如何设计幂等和补偿机制？

推广建议：开发团队在代码评审中明确标注使用 Pipeline、事务或 Lua 的原因；测试团队要覆盖单条失败、并发修改和批量过大场景；运维团队关注客户端输出缓冲区、慢查询和批量任务执行窗口。
