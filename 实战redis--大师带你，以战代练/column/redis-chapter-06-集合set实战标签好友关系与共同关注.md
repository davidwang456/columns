# 第6章：集合 Set 实战：标签、好友关系与共同关注

## 1. 项目背景

电商 App 准备上线“兴趣社区”：用户可以关注达人、给自己打标签、屏蔽不喜欢的用户，还要在商品详情页展示“你和他共同关注了谁”。产品经理的第一版方案很直接：所有关系都放在 MySQL 表里，`user_follow` 存关注关系，`user_tag` 存标签，查询共同关注时用两次查询加程序内循环。

这个方案能跑，但一到活动期就露出问题。达人直播间里几十万人同时关注、取关，商品页又要实时计算共同关注；如果每次都查数据库再做集合运算，数据库会被大量关系查询拖慢。更麻烦的是“可能认识的人”：它要把好友的关注对象合并、去掉自己已关注的人、再过滤黑名单，SQL 会变得又长又难维护。

Redis Set 天然适合表达“唯一成员集合”。关注列表、粉丝列表、用户标签、权限集合、黑名单都可以建模成 Set。它不关心顺序，但擅长去重、判断是否存在、计算交集、并集和差集。本章我们用 Set 做一个社交关系小功能：支持关注、取关、共同关注、推荐好友和黑名单过滤。

## 2. 项目设计

小胖先开球：“共同关注不就是两个人的关注列表拿出来比一比吗？像我和小白都爱吃烤肉，交叉一下就知道共同爱好。”

小白接着问：“如果一个用户关注了一万个账号，热门达人有几百万粉丝，程序每次拉全量列表再循环，会不会把网络和应用内存打爆？”

大师点头：“所以我们不把集合运算搬到应用里。Redis Set 可以把关系放在服务端，`SINTER` 直接求共同关注，`SISMEMBER` 判断是否关注，`SCARD` 统计数量。关系是集合，交并差就是业务动作。”

技术映射：`followings:{uid}` 表示用户关注的人，`followers:{uid}` 表示关注该用户的人，`blacklist:{uid}` 表示黑名单，`tags:{uid}` 表示用户标签。

小胖又问：“那关注是不是只写一个集合就行？我关注大师，就往我的关注列表里加大师。”

小白摇头：“粉丝页也要查谁关注了大师。如果只存单向关系，查粉丝就得遍历所有用户。”

大师补充：“关注动作要双写两个 Set：`SADD followings:1001 2001`，同时 `SADD followers:2001 1001`。取关也要双删。Redis 单条命令是原子的，但多条命令之间不是事务，后面学 Lua 和事务会更严谨；本章先把建模思想跑通。”

技术映射：一个业务关系常常需要正向索引和反向索引，牺牲一点写入成本，换取高频读路径简单。

小胖继续追问：“推荐好友怎么做？把朋友关注的人都推给我？”

大师在白板写下三步：“先取我关注的用户，再取这些用户关注的人做并集，最后减掉我自己、已关注的人和黑名单。这个流程对应 `SUNION` 和 `SDIFF`。但要注意，`SUNION` 对大集合可能阻塞 Redis 主线程，生产上推荐用 `SSCAN` 分批、离线计算，或把候选集写入临时 key 后设置短 TTL。”

小白补了一句：“所以 Set 适合中小规模实时关系，大规模图关系还要考虑图数据库、搜索引擎或离线推荐系统。”

技术映射：Set 解决的是轻量关系运算，不是完整社交图推荐引擎。选型要看集合大小、实时性和精确度要求。

## 3. 项目实战

先启动实验 Redis：

```bash
docker run --name redis-lab-06 -p 6379:6379 -d redis:8.6
docker exec -it redis-lab-06 redis-cli
```

### 3.1 关注与取关

约定 key：

```bash
# 用户 1001 关注了 2001、2002、2003
SADD followings:1001 2001 2002 2003
SADD followers:2001 1001
SADD followers:2002 1001
SADD followers:2003 1001

# 用户 1002 关注了 2002、2003、2004
SADD followings:1002 2002 2003 2004
SADD followers:2002 1002
SADD followers:2003 1002
SADD followers:2004 1002
```

验证关系：

```bash
SISMEMBER followings:1001 2002
SCARD followings:1001
SMEMBERS followers:2002
```

取关时同时维护正反两个集合：

```bash
SREM followings:1001 2003
SREM followers:2003 1001
```

业务伪代码：

```text
follow(fromUid, toUid):
  if fromUid == toUid: return "不能关注自己"
  if SISMEMBER blacklist:fromUid toUid: return "已拉黑"
  SADD followings:fromUid toUid
  SADD followers:toUid fromUid
  return "ok"

unfollow(fromUid, toUid):
  SREM followings:fromUid toUid
  SREM followers:toUid fromUid
```

### 3.2 共同关注

用户 1001 和 1002 的共同关注：

```bash
SINTER followings:1001 followings:1002
SINTERCARD 2 followings:1001 followings:1002
```

预期返回 `2002`。如果想把结果缓存 30 秒，避免热门页面重复计算：

```bash
SINTERSTORE common:1001:1002 followings:1001 followings:1002
EXPIRE common:1001:1002 30
```

缓存共同关注要注意方向一致。`common:1001:1002` 和 `common:1002:1001` 是同一个业务问题，实际项目可以把较小 uid 放前面，避免重复 key。

### 3.3 标签和黑名单

Set 也适合用户标签和权限集合：

```bash
SADD tags:1001 "redis" "backend" "game"
SADD tags:1002 "redis" "ops" "movie"
SINTER tags:1001 tags:1002
```

黑名单过滤：

```bash
SADD blacklist:1001 2004
SISMEMBER blacklist:1001 2004
```

推荐好友候选：

```bash
# 1001 关注的 2001、2002 又关注了其他人
SADD followings:2001 3001 3002 3003
SADD followings:2002 3002 3004 2001

SUNIONSTORE recommend:candidate:1001 followings:2001 followings:2002
SADD self:1001 1001
SDIFF recommend:candidate:1001 followings:1001 blacklist:1001 self:1001
EXPIRE recommend:candidate:1001 60
```

实际服务中不要为每个用户长期保存 `self:{uid}`，可以在应用层过滤自己，或复用短期临时 key。

### 3.4 常见坑

第一，`SMEMBERS` 会一次性返回整个集合，大集合会占用网络和 Redis 主线程时间。后台巡检、导出任务应使用 `SSCAN followings:1001 0 COUNT 100` 分批读取。

第二，交并差命令虽然好用，但对多个大 Set 做 `SINTER`、`SUNION` 仍可能成为慢命令。热门用户的粉丝集合不要频繁做全量集合运算，推荐改成异步任务预计算。

第三，双写正反索引可能出现不一致。比如写入 `followings` 成功后服务宕机，`followers` 没写成功。生产上可以用 Lua 脚本、事务或消息补偿来处理。

第四，Set 没有顺序。如果业务要“按关注时间展示”，需要额外使用 ZSet，把关注时间作为 score。

## 4. 项目总结

Set 的核心价值是把“关系”变成“集合”。只要业务问题可以表达为是否存在、去重、交集、并集、差集，Set 往往比数据库临时计算更轻量。它适合用户标签、关注关系、权限集合、黑白名单、活动参与去重等场景。

但 Set 不是万能图数据库。它不擅长复杂路径查询，也不适合对超大集合频繁做全量交并差。生产中要关注集合基数、慢命令、网络返回量和正反索引一致性。小集合实时算，大集合分批扫，热门关系提前算，是比较稳妥的落地策略。

本章思考题：

1. 如果要实现“共同好友数量大于 3 才展示共同好友模块”，你会用 `SINTER` 还是 `SINTERCARD`？为什么？
2. 如果用户关注关系需要按时间倒序分页，Set 应该如何与 ZSet 配合？
