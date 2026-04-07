本故事纯属虚构，如有雷同，纯属巧合。

> 「暴雨梨花钉」二十七枚银钉势急力猛，可称天下第一，每一射出，必定见血，昔日纵横 南荒 的一尘道长，都是死在这暗器下的。 此物扁平如匣，长七寸，厚三寸。上用 小篆 字体雕刻：「出必见血，空回不祥；急中之急，暗器之王」。发射之时，共二十七枚银针激射而出。
> 
> 破箭式破解诸般暗器 ，须得先学「听风辨器」之术，击开敌手发射来的种种暗器， 以敌手打来的暗器反射伤敌。

![](http://p6.toutiaoimg.com/large/pgc-image/eaf8bec50689441ea7f158034864cf11)

故事背景

> 古语云：明枪易躲，暗箭难防。 这件事情在《左传·隐公十一年》里有记载。那年夏天，五月里，郑庄公在宫前检阅部队，发派兵车。一位老将军颍〔yǐng〕考叔和一位青年将军公孙子都，为了争夺兵车吵了起来。颍考叔是一员勇将，他不服老，拉起兵车转身就跑；公孙子都向来瞧不起人，当然不肯相让，拔起长戟飞奔追去。等他追上大路，颍考叔早已不见人影了。公孙子都因此怀恨在心。
> 
> 到了秋天，七月间，郑庄公正式下令攻打许国。郑军逼近许国都城，攻城的时候，颍考叔奋勇当先，杀敌无数，爬上了城头。公孙子都眼看颍考叔就要立下大功，心里更加忌妒起来，便抽出箭来对准颍考叔就是一箭，只见这位勇敢的老将军一个跟斗摔了下来。另一位将军瑕叔盈还以为颍考叔是被许国兵杀死的，连忙拾起大旗，指挥士兵继续战斗，终于把城攻破。郑军全部入了城，许国的国君许庄公逃亡到了卫国。许国的土地于是并入了郑国的版图。

**Redis第八式：破箭式**

**大师**：常说“明枪易躲，暗箭难防”，在网络上常常遭遇网络攻击，黑客常常采用的攻击手段1.抓取用户信息，使用该用户信息伪造请求；2.伪造海量用户来尝试攻击。碰到这些情况，我们如何来阻断这些攻击呢？

**小白**：大师，前端可以通过图片验证码来做，后端通过参数校验，数据库查询来判断。

**大师**：是的，前端可以通过图片验证码来减少请求，但目前在验证码这块很难拦住黑客，毕竟验证码的破解度很高；如果请求全部打到后端做参数校验，数据库查询，对后台的机器性能和数据库都有比较大的压力，本次实战我们要学习的Redis第八式：破箭式，使用BitMap构建一个BloomFilter可以解决这个问题。

**小白**：大师，何谓破箭式？我查了Redis的招式，并没有看到Bitmap这式。

![](http://p6.toutiaoimg.com/large/pgc-image/6981103bf3bd4050976d7c2aa8512bd0)

**大师**：从秘笈中确实没有Bitmap这个招式，而是把它归到String招式里，但在介绍Redis的招式里却明确提到了bitmaps。

> Redis is an open source (BSD licensed), in-memory data structure store, used as a database, cache and message broker. It supports data structures such as strings, hashes, lists, sets, sorted sets with range queries, bitmaps, hyperloglogs, geospatial indexes with radius queries and streams. Redis has built-in replication, Lua scripting, LRU eviction, transactions and different levels of on-disk persistence, and provides high availability via Redis Sentinel and automatic partitioning with Redis Cluster.

**大师**：那我来一一讲解Bitmap的招式，不懂的你就提问吧

> 1.SETBIT key offset value
> 
> 时间复杂度：O(1)
> 
> 设置或者清空key的value(字符串)在offset处的bit值。
> 
> 那个位置的bit要么被设置，要么被清空，这个由value（只能是0或者1）来决定。当key不存在的时候，就创建一个新的字符串value。要确保这个字符串大到在offset处有bit值。参数offset需要大于等于0，并且小于232(限制bitmap大小为512)。当key对应的字符串增大的时候，新增的部分bit值都是设置为0。
> 
> 警告：当set最后一个bit(offset等于232-1)并且key还没有一个字符串value或者其value是个比较小的字符串时，Redis需要立即分配所有内存，这有可能会导致服务阻塞一会。在一台2010MacBook Pro上，offset为232-1（分配512MB）需要～300ms，offset为230-1(分配128MB)需要～80ms，offset为228-1（分配32MB）需要～30ms，offset为226-1（分配8MB）需要8ms。注意，一旦第一次内存分配完，后面对同一个key调用SETBIT就不会预先得到内存分配。
> 
> 2.GETBIT key offset
> 
> 时间复杂度：O(1)
> 
> 返回key对应的string在offset处的bit值 当offset超出了字符串长度的时候，这个字符串就被假定为由0比特填充的连续空间。当key不存在的时候，它就认为是一个空字符串，所以offset总是超出范围，然后value也被认为是由0比特填充的连续空间。到内存分配。
> 
> 3.BITCOUNT key [start end]
> 
> 时间复杂度：O(N)
> 
> 统计字符串被设置为1的bit数.
> 
> 一般情况下，给定的整个字符串都会被进行计数，通过指定额外的 start 或 end 参数，可以让计数只在特定的位上进行。
> 
> start 和 end 参数的设置和 GETRANGE 命令类似，都可以使用负数值：比如 -1 表示最后一个位，而 -2 表示倒数第二个位，以此类推。
> 
> 不存在的 key 被当成是空字符串来处理，因此对一个不存在的 key 进行 BITCOUNT 操作，结果为 0 。
> 
> 4. BITOP operation destkey key [key ...]
> 
> 时间复杂度：O(N)
> 
> 对一个或多个保存二进制位的字符串 key 进行位元操作，并将结果保存到 destkey 上。
> 
> BITOP 命令支持 AND 、 OR 、 NOT 、 XOR 这四种操作中的任意一种参数：
> 
> BITOP AND destkey srckey1 srckey2 srckey3 ... srckeyN ，对一个或多个 key 求逻辑并，并将结果保存到 destkey 。
> 
> BITOP OR destkey srckey1 srckey2 srckey3 ... srckeyN，对一个或多个 key 求逻辑或，并将结果保存到 destkey 。
> 
> BITOP XOR destkey srckey1 srckey2 srckey3 ... srckeyN，对一个或多个 key 求逻辑异或，并将结果保存到 destkey 。
> 
> BITOP NOT destkey srckey，对给定 key 求逻辑非，并将结果保存到 destkey 。
> 
> 除了 NOT 操作之外，其他操作都可以接受一个或多个 key 作为输入。
> 
> 执行结果将始终保持到destkey里面。
> 
> 5. BITPOS key bit [start] [end]
> 
> 时间复杂度：O(N)
> 
> 返回字符串里面第一个被设置为1或者0的bit位。
> 
> 返回一个位置，把字符串当做一个从左到右的字节数组，第一个符合条件的在位置0，其次在位置8，等等。
> 
> GETBIT 和 SETBIT 相似的也是操作字节位的命令。
> 
> 默认情况下整个字符串都会被检索一次，只有在指定start和end参数(指定start和end位是可行的)，该范围被解释为一个字节的范围，而不是一系列的位。所以start=0 并且 end=2是指前三个字节范围内查找。
> 
> 注意，返回的位的位置始终是从0开始的，即使使用了start来指定了一个开始字节也是这样。
> 
> 和GETRANGE命令一样，start和end也可以包含负值，负值将从字符串的末尾开始计算，-1是字符串的最后一个字节，-2是倒数第二个，等等。
> 
> 不存在的key将会被当做空字符串来处理。
> 
> 6. BITFIELD key [GET type offset] [SET type offset value] [INCRBY type offset increment] [OVERFLOW WRAP|SAT|FAIL]
> 
> 时间复杂度：O(1)
> 
> 本命令会把Redis字符串当作位数组，并能对变长位宽和任意未字节对齐的指定整型位域进行寻址。在实践中，可以使用该命令对一个有符号的5位整型数的1234位设置指定值，也可以对一个31位无符号整型数的4567位进行取值。类似地，在对指定的整数进行自增和自减操作，本命令可以提供有保证的、可配置的上溢和下溢处理操作。

> BITFIELD命令能操作多字节位域，它会执行一系列操作，并返回一个响应数组，在参数列表中每个响应数组匹配相应的操作。

**小白**：大师，那什么是BloomFilter布隆过滤器？为什么要使用Redis构建它呢？

**大师**：布隆过滤器原理就是一个对一个key进行k个hash算法获取k个值，在比特数组中将这k个值散列后设定为1，然后查的时候如果特定的这几个位置都为1，那么布隆过滤器判断该key存在。布隆过滤器可能会误判，如果它说不存在那肯定不存在，如果它说存在，那数据有可能实际不存在；Redis的bitmap只支持2^32大小，对应到内存也就是512MB，误判率万分之一，可以放下2亿左右的数据，性能高，空间占用率及小，省去了大量无效的数据库连接。

好，你先熟悉这些招式，下午三点再将我们的实战项目的设计将给我听。

**小白**：谢大师指点！下午3点准时听候你的教导。

时间哒哒地流逝着，很快下午3点到了。小白也按时出现在大师面前。

**大师**：说说你来阻止黑客的攻击的方案吧？

**小白**：大师，我搜集了BloomFilter相关的一些资料，发现实现的方式有多种：

1.java(或者其它语言)

![](http://p26.toutiaoimg.com/large/pgc-image/b5607ee739244b79a322d35c3ff24cb7)

使用了BitSet来实现，举例如下；

```
    public static void main(String[] args) {
 int [] array = new int [] {1,2,3,22,0,3,10,1000,56,8,9};
 BitSet bitSet = new BitSet();
 //将数组内容组bitmap
 for(int i=0;i<array.length;i++)
 {
 bitSet.set(array[i], true);
 }
 bitSet.set(10000, true);
 System.out.println(bitSet.size());

 }
```

或者使用Guava实现的

```
BloomFilter<Integer> filter = BloomFilter.create(
 Funnels.integerFunnel(),
 500,
 0.01);
filter.put(1);
filter.put(2);
filter.put(3);
assertThat(filter.mightContain(1)).isTrue();
assertThat(filter.mightContain(2)).isTrue();
assertThat(filter.mightContain(3)).isTrue();
assertThat(filter.mightContain(100)).isFalse();
```

2.redis 实现BloomFilter

BitMap

redis4.0 之后支持插件支持布隆过滤器

java自带的或者guava实现的BloomFilter针对于单机，Redis可以支持分布式环境的。

**大师**：使用的时候要注意下面几点：

1.使用pipeline去操作setbit

2.第一次设置的时候不管是java或者redis，从**最大值**开始设置。原因：第一次创建都需要立即分配所有内存，内存不够需要扩充，分配内存比较耗时，故用最大的，后面就不用再次分配内存了。

3.考虑到用户再持续增加，也可以预估一个时间段内，可能达到的用户id进行分配内存

好，今天就到这里，你回去后还需要好好完善你的方案。

**小白**：好的，恭送大师！
