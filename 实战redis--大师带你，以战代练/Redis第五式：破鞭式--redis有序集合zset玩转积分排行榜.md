本故事纯属虚构，如有雷同，纯属巧合。

> 十八般兵器
> 
> 刀 枪 剑 戟 斧 钺
> 
> 钩 叉 鞭 锏 锤 挝
> 
> 镋 棍 槊 棒 拐 流星
> 
> 破鞭式破解诸般钢鞭 、 点穴橛 、 拐子 、 峨眉刺 、 匕首 、 斧 、 铁牌 、八角槌、 铁椎 种种短兵刃。

**故事背景**

![](http://p6.toutiaoimg.com/large/pgc-image/5acacae8b1014d09a2c67da2d599d582)

《 封神演义》

> 打神鞭，是阐教至宝之一，形状为木鞭状。此鞭长三尺六寸五分，有二十一节，每一节有四道符印，共八十四道符印， 封神之战 时元始天尊给予 姜子牙 用以管理封神，封神过后 元始天尊 欲将打神鞭收回，但念其有功故而不收。

**Redis第五式：破鞭式**

**大师**：我们的用户信息表里，有一项信息是用户积分，现在如果想要快速的获取超过10w积分的用户，另外还想获取积分top 100的用户，如何做呢？

**小白**：大师，这个需求比较简单，第一个的话，在数据库使用select userid from userinfo where score>100000 limit start,end就行；第二个的话，在数据库使用 select userid from userinfo order by score desc limit 100.

**大师**：1000w条数据中查询会不会出现接

口性能问题？

**小白**：可以通过在数据表中在score字段添加索引，走索引查询。

**大师**：假设通过数据库查询，接口可以在300ms内返回，如果想要接口在100ms内返回，如何做呢？

**小白**：是不是可以预先将用户及其分数存到redis里，并按照score分数由高到低排序好？

**大师**：你的悟性很好，Redis有一个大招：有序集合可以帮你完成这项工作， 有序集合是每个元素都会关联一个double类型的分数，通过分数来为集合中的成员进行从小到大的排序。有序集合的成员是唯一的,但分数(score)却可以重复。

**小白**：大师，这个大招有哪些具体的招式呢？

**大师**：我先将招式一一道来，你需背熟后，详细琢磨。

> 序号 命令及描述
> 
> 1 ZADD key score1 member1 [score2 member2]
> 
> 向有序集合添加一个或多个成员，或者更新已存在成员的分数
> 
> 2 ZCARD key
> 
> 获取有序集合的成员数
> 
> 3 ZCOUNT key min max
> 
> 计算在有序集合中指定区间分数的成员数
> 
> 4 ZINCRBY key increment member
> 
> 有序集合中对指定成员的分数加上增量 increment
> 
> 5 ZINTERSTORE destination numkeys key [key ...]
> 
> 计算给定的一个或多个有序集的交集并将结果集存储在新的有序集合 key 中
> 
> 6 ZLEXCOUNT key min max
> 
> 在有序集合中计算指定字典区间内成员数量
> 
> 7 ZRANGE key start stop [WITHSCORES]
> 
> 通过索引区间返回有序集合成指定区间内的成员
> 
> 8 ZRANGEBYLEX key min max [LIMIT offset count]
> 
> 通过字典区间返回有序集合的成员
> 
> 9 ZRANGEBYSCORE key min max [WITHSCORES] [LIMIT]
> 
> 通过分数返回有序集合指定区间内的成员
> 
> 10 ZRANK key member
> 
> 返回有序集合中指定成员的索引
> 
> 11 ZREM key member [member ...]
> 
> 移除有序集合中的一个或多个成员
> 
> 12 ZREMRANGEBYLEX key min max
> 
> 移除有序集合中给定的字典区间的所有成员
> 
> 13 ZREMRANGEBYRANK key start stop
> 
> 移除有序集合中给定的排名区间的所有成员
> 
> 14 ZREMRANGEBYSCORE key min max
> 
> 移除有序集合中给定的分数区间的所有成员
> 
> 15 ZREVRANGE key start stop [WITHSCORES]
> 
> 返回有序集中指定区间内的成员，通过索引，分数从高到底
> 
> 16 ZREVRANGEBYSCORE key max min [WITHSCORES]
> 
> 返回有序集中指定分数区间内的成员，分数从高到低排序
> 
> 17 ZREVRANK key member
> 
> 返回有序集合中指定成员的排名，有序集成员按分数值递减(从大到小)排序
> 
> 18 ZSCORE key member
> 
> 返回有序集中，成员的分数值
> 
> 19 ZUNIONSTORE destination numkeys key [key ...]
> 
> 计算给定的一个或多个有序集的并集，并存储在新的 key 中
> 
> 20 ZSCAN key cursor [MATCH pattern] [COUNT count]
> 
> 迭代有序集合中的元素（包括元素成员和元素分值）

**小白**：大师，将数据库中userinfo表中userid和score字段预先导入到redis，数据量比较大，如何快速的导入呢?

**大师**：我们可以看到ZADD key score1 member1 [score2 member2]向有序集合添加一个或多个成员，或者更新已存在成员的分数，这个指令支持批量导入或者更新

**小白**：那怎么解决批量导入到redis时，用户积分发生了变化的情况呢？

**大师**：这个问题问的很好，生产上的导入导出都会存在数据一致性的问题，但也要具体业务具体分享。1. 我们可以根据数据库userinfo表的更新时间通过两个定时任务：1个1小时，1个五分钟来定时更新redis的数据；2.最好也在业务代码上把redis和数据库表的信息做双写 3.积分非业务的关键信息，允许适当的延迟，不用担心影响业务。

**小白**：大师，可以将redis作为数据库的前置库，只有当redis出现故障时才读取数据库。可以这样理解吗？

**大师**：你理解的很对，对许多实时性要求不少特别高的系统，都可以采用这样的策略。好了，今天就到这里了。

**小白**：谢谢大师，恭送大师！
