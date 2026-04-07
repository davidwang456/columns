本故事纯属虚构，如有雷同，纯属巧合。

> 破剑式:用以破解普天下各门各派的剑法.破剑式虽只一式，但其中于天下各门各派剑法要义兼收并蓄，虽说“无招”，却是以普天下剑法之招数为根基。

**故事背景**

剑术在中国武术运动中,占有极其重要的位置,千百年来,被称之“百兵之君”。自古，行侠者佩剑而行，文雅高尚者佩剑，将军统帅佩剑，由此可见剑是武术文化的精髓，是衡量功夫境界高深的尺码。“剑”代表是降妖伏魔的神物。“剑”代表着正气，代表着决心。中国的剑的历史源远流长，素有“兵器中的君子”之美称。

![](http://p9.toutiaoimg.com/large/dfic-imagehandler/7e155755-01b8-44a9-a7d8-9f25670555f3)

**Redis破剑式**

**大师**：昨天我们学习了REDIS独孤九剑的第一式：总决式，第一式式大纲，没有具体的招式，但贯穿整个独孤九剑，以后你会满满体会到的！今天我们学习独孤九剑的第二式：破剑式。

**大师**：既然是实战课程，那我们就开始实战吧。第一个实战项目是项目改造，目前用户信息存储在mysql数据库TUser表中的，数据量约为1000w，你来使用redis增加缓存，提升用户信息接口的性能。要考虑的要点：1.数据如何在redis存储？ 2.如何把mysql中的数据批量导入到redis？ 3.如何保证redis和mysql数据库的一致性？下午3点，来看你的方案吧。

**小白**：是，大师！那我可以从哪些点入手呢？

**大师**：要义有：

1.确定用户信息如何存储？使用破剑式，有哪些招式变化？

2.调查用户信息接口最近一周的使用情况，采用8-2原则，确定事情的重点和难点

3.接口改造如何保证redis和mysql是一致的？

**小白**：谢谢大师，我去准备，下午3点再向大师学习。

时间哒哒地流逝着，很快下午3点到了。小白也按时出现在大师面前。

**大师**：说一下你是怎么做的吧？

**小白**：根据大师你的提示，

我先了解了一下背熟了破剑式的口诀：

key的最大size是512MB，但key不能太大，也不能太小，要根据网络传输及排序的特性来确定大小；key要严格满足数据库的schema规范，定义key。口诀如下：

A few other rules about keys:

Very long keys are not a good idea. For instance a key of 1024 bytes is a bad idea not only memory-wise, but also because the lookup of the key in the dataset may require several costly key-comparisons. Even when the task at hand is to match the existence of a large value, hashing it (for example with SHA1) is a better idea, especially from the perspective of memory and bandwidth.

- Very short keys are often not a good idea. There is little point in writing "u1000flw" as a key if you can instead write "user:1000:followers". The latter is more readable and the added space is minor compared to the space used by the key object itself and the value object. While short keys will obviously consume a bit less memory, your job is to find the right balance.
- Try to stick with a schema. For instance "object-type:id" is a good idea, as in "user:1000". Dots or dashes are often used for multi-word fields, as in "comment:1234:reply.to" or "comment:1234:reply-to".
- The maximum allowed key size is 512 MB.

2.破剑式的招式也练熟悉了

主要有十八招：APPEND,DECR,DECRBY,GET,GETRANGE,GETSET,INCR,INCRBY,INCRBYFLOAT,MGET,MSET,MSETNX,PSETEX,SET,SETEX,SETNX,SETRANGE,STRLEN

3.了解了用户接口的调用情况，发现最近一周用户信息接口中86%的查询来自根据用户id，查询用户信息，新增，修改和删除频率低于1%，

4.redis和mysql数据一致性，也参考了网上的说法，查询的时候先读缓存再读数据库；修改的时候先修改数据再存到redis中；删除时先删除redis后删除数据库；新增时先数据库后redis。

**大师**：你做的很好，key的规范设计要严格按照数据库schema的规范来定义，这个很重要！

另外我补充几点：

1.mysql的用户信息表存入到redis，要考虑容量的问题。用户信息哪些需要存入redis，哪些不需要考虑redis，要不要分库，要不要分片？。

2.预先批量导出存量的redis的方式，MGET是一种方式，还可以看看pipeline方式，transaction方式等

3. 对于导出期间的增量的处理，可以使用一个定时任务，根据updatetime来定时更新redis库

4.从redis查询或者删除redis key的时候要考虑使用scan，而不要使用通配符

5.破剑式看着十八招，其实还有一些通用招式没有包含在里面如DEL,DUMP,EXISTS,EXPIRE,EXPIREAT,KEYS,MIGRATE,MOVE,OBJECT,PERSIST,PEXPIRE,PEXPIREAT,PTTL,RANDOMKEY,RENAME,RENAMENX,RESTORE,SCAN,SORT,TOUCH,TTL,TYPE,UNLINK,WAIT 你可以细细研究。

好了，今天就这里，你回去把完整的方案发出来吧。。
