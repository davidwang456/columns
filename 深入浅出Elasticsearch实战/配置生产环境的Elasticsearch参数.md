# 背景

     Elasticsearch 已经有了 很好的默认值，特别是涉及到性能相关的配置或者选项。有些 逻辑上的 配置在生产环境中是应该调整的。 这些调整可能会让你的工作更加轻松，又或者因为没办法设定一个默认值（它取决于你的集群布局）。

# Elasticsearch生产配置推荐

**指定集群/节点名字**

    Elasticsearch 默认启动的集群名字叫 elasticsearch 。你最好给你的生产环境的集群改个名字，改名字的目的很简单， 就是为了防止某人的笔记本电脑加入了集群这种意外。

    简单修改成 elasticsearch_production 会很省心。可以在你的 elasticsearch.yml 文件中修改：

```
cluster.name: elasticsearch_production
```

同样，最好也修改你的节点名字。就像你现在可能发现的那样， Elasticsearch 会在你的节点启动的时候随机给它指定一个名字。你可能会觉得这很有趣，但是当凌晨 3 点钟的时候， 你还在尝试回忆哪台物理机是 Tagak the Leopard Lord 的时候，你就不觉得有趣了。

更重要的是，这些名字是在启动的时候产生的，每次启动节点， 它都会得到一个新的名字。这会使日志变得很混乱，因为所有节点的名称都是不断变化的。

这可能会让你觉得厌烦，我们建议给每个节点设置一个有意义的、清楚的、描述性的名字，同样你可以在 elasticsearch.yml 中配置：

```
node.name: elasticsearch_005_data
```

**将数据、插件、日志文件指定到安装目录外**

默认情况下，Elasticsearch 会把插件、日志以及你最重要的数据放在安装目录下。这会带来不幸的事故， 如果你重新安装 Elasticsearch 的时候不小心把安装目录覆盖了。如果你不小心，你就可能把你的全部数据删掉了。不要笑，这种情况，我们见过很多次了。最好的选择就是把你的数据目录配置到安装目录以外的地方， 同样你也可以选择转移你的插件和日志目录。可以更改如下：

```
path.data: /path/to/data1,/path/to/data2 

# Path to log files:
path.logs: /path/to/logs

# Path to where plugins are installed:
path.plugins: /path/to/plugins
```

注意：你可以通过逗号分隔指定多个目录。

数据可以保存到多个不同的目录， 如果将每个目录分别挂载不同的硬盘，这可是一个简单且高效实现一个软磁盘阵列（ RAID 0 ）的办法。Elasticsearch 会自动把条带化（注：RAID 0 又称为 Stripe（条带化），在磁盘阵列中,数据是以条带的方式贯穿在磁盘阵列所有硬盘中的） 数据分隔到不同的目录，以便提高性能。

```
提示：
多个数据路径的安全性和性能
如同任何磁盘阵列（ RAID 0 ）的配置，只有单一的数据拷贝保存到硬盘驱动器。如果你失去了一个硬盘驱动器，你 肯定 会失去该计算机上的一部分数据。 运气好的话你的副本在集群的其他地方，可以用来恢复数据和最近的备份。

Elasticsearch 试图将全部的条带化分片放到单个驱动器来保证最小程度的数据丢失。这意味着 分片 0 将完全被放置在单个驱动器上。 Elasticsearch 没有一个条带化的分片跨越在多个驱动器，因为一个驱动器的损失会破坏整个分片。

这对性能产生的影响是：如果您添加多个驱动器来提高一个单独索引的性能，可能帮助不大，因为 大多数节点只有一个分片和这样一个积极的驱动器。多个数据路径只是帮助如果你有许多索引／分片在单个节点上。

多个数据路径是一个非常方便的功能，但到头来，Elasticsearch 并不是软磁盘阵列（ software RAID ）的软件。如果你需要更高级的、稳健的、灵活的配置， 我们建议你使用软磁盘阵列（ software RAID ）的软件，而不是多个数据路径的功能。
```

**堆内存:大小和交换**

Elasticsearch 默认安装后设置的堆内存是 1 GB。对于任何一个业务部署来说， 这个设置太小了。如果你正在使用这些默认堆内存配置，您的集群可能会出现问题。

这里有两种方式修改 Elasticsearch 的堆内存。最简单的一个方法就是指定 ES_HEAP_SIZE 环境变量。服务进程在启动时候会读取这个变量，并相应地设置堆的大小。 比如，你可以用下面的命令设置它：

export ES_HEAP_SIZE=10g

此外，你也可以通过命令行参数的形式，在程序启动的时候把内存大小传递给它，如果你觉得这样更简单的话：

./bin/elasticsearch -Xmx10g -Xms10g

确保堆内存最小值（ Xms ）与最大值（ Xmx ）的大小是相同的，防止程序在运行时改变堆内存大小， 这是一个很耗系统资源的过程。

通常来说，设置 ES_HEAP_SIZE 环境变量，比直接写 -Xmx -Xms 更好一点。

> 把你的内存的（少于）一半给 Lucene
> 
> 一个常见的问题是给 Elasticsearch 分配的内存 太 大了。假设你有一个 64 GB 内存的机器， 天啊，我要把 64 GB 内存全都给 Elasticsearch。因为越多越好啊！
> 
> 当然，内存对于 Elasticsearch 来说绝对是重要的，它可以被许多内存数据结构使用来提供更快的操作。但是说到这里， 还有另外一个内存消耗大户 非堆内存 （off-heap）：Lucene。
> 
> Lucene 被设计为可以利用操作系统底层机制来缓存内存数据结构。 Lucene 的段是分别存储到单个文件中的。因为段是不可变的，这些文件也都不会变化，这是对缓存友好的，同时操作系统也会把这些段文件缓存起来，以便更快的访问。
> 
> Lucene 的性能取决于和操作系统的相互作用。如果你把所有的内存都分配给 Elasticsearch 的堆内存，那将不会有剩余的内存交给 Lucene。 这将严重地影响全文检索的性能。
> 
> 标准的建议是把 50％ 的可用内存作为 Elasticsearch 的堆内存，保留剩下的 50％。当然它也不会被浪费，Lucene 会很乐意利用起余下的内存。
> 
> 如果你不需要对分词字符串做聚合计算（例如，不需要 fielddata ）可以考虑降低堆内存。堆内存越小，Elasticsearch（更快的 GC）和 Lucene（更多的内存用于缓存）的性能越好。

不要超过 32 GB！

这里有另外一个原因不分配大内存给 Elasticsearch。事实上， JVM 在内存小于 32 GB 的时候会采用一个内存对象指针压缩技术。

在 Java 中，所有的对象都分配在堆上，并通过一个指针进行引用。 普通对象指针（OOP）指向这些对象，通常为 CPU 字长 的大小：32 位或 64 位，取决于你的处理器。指针引用的就是这个 OOP 值的字节位置。

对于 32 位的系统，意味着堆内存大小最大为 4 GB。对于 64 位的系统， 可以使用更大的内存，但是 64 位的指针意味着更大的浪费，因为你的指针本身大了。更糟糕的是， 更大的指针在主内存和各级缓存（例如 LLC，L1 等）之间移动数据的时候，会占用更多的带宽。

Java 使用一个叫作 内存指针压缩（compressed oops）的技术来解决这个问题。 它的指针不再表示对象在内存中的精确位置，而是表示 偏移量 。这意味着 32 位的指针可以引用 40 亿个 对象 ， 而不是 40 亿个字节。最终， 也就是说堆内存增长到 32 GB 的物理内存，也可以用 32 位的指针表示。

一旦你越过那个神奇的 ~32 GB 的边界，指针就会切回普通对象的指针。 每个对象的指针都变长了，就会使用更多的 CPU 内存带宽，也就是说你实际上失去了更多的内存。事实上，当内存到达 40–50 GB 的时候，有效内存才相当于使用内存对象指针压缩技术时候的 32 GB 内存。

这段描述的意思就是说：即便你有足够的内存，也尽量不要 超过 32 GB。因为它浪费了内存，降低了 CPU 的性能，还要让 GC 应对大内存。

到底需要低于 32 GB多少，来设置我的 JVM？

遗憾的是，这需要看情况。确切的划分要根据 JVMs 和操作系统而定。 如果你想保证其安全可靠，设置堆内存为 31 GB 是一个安全的选择。 另外，你可以在你的 JVM 设置里添加 -XX:+PrintFlagsFinal 用来验证 JVM 的临界值， 并且检查 UseCompressedOops 的值是否为 true。对于你自己使用的 JVM 和操作系统，这将找到最合适的堆内存临界值。

例如，我们在一台安装 Java 1.7 的 MacOSX 上测试，可以看到指针压缩在被禁用之前，最大堆内存大约是在 32600 mb（~31.83 gb）：

```
$ JAVA_HOME=`/usr/libexec/java_home -v 1.7` java -Xmx32600m -XX:+PrintFlagsFinal 2> /dev/null | grep UseCompressedOops
     bool UseCompressedOops   := true
$ JAVA_HOME=`/usr/libexec/java_home -v 1.7` java -Xmx32766m -XX:+PrintFlagsFinal 2> /dev/null | grep UseCompressedOops
     bool UseCompressedOops   = false
```

相比之下，同一台机器安装 Java 1.8，可以看到指针压缩在被禁用之前，最大堆内存大约是在 32766 mb（~31.99 gb）：

```
$ JAVA_HOME=`/usr/libexec/java_home -v 1.8` java -Xmx32766m -XX:+PrintFlagsFinal 2> /dev/null | grep UseCompressedOops
     bool UseCompressedOops   := true
$ JAVA_HOME=`/usr/libexec/java_home -v 1.8` java -Xmx32767m -XX:+PrintFlagsFinal 2> /dev/null | grep UseCompressedOops
     bool UseCompressedOops   = false
```

这个例子告诉我们，影响内存指针压缩使用的临界值， 是会根据 JVM 的不同而变化的。 所以从其他地方获取的例子，需要谨慎使用，要确认检查操作系统配置和 JVM。

如果使用的是 Elasticsearch v2.2.0，启动日志其实会告诉你 JVM 是否正在使用内存指针压缩。 你会看到像这样的日志消息：

[2015-12-16 13:53:33,417][INFO ][env] [Illyana Rasputin] heap size [989.8mb], compressed ordinary object pointers [true]

这表明内存指针压缩正在被使用。如果没有，日志消息会显示 [false] 。  
**禁止swap**

这是显而易见的，但是还是有必要说得更清楚一点：内存交换 磁盘对服务器性能来说是 *致命* 的。想想看：一个内存操作必须能够被快速执行。

如果内存交换到磁盘上，一个 100 下一秒的操作可能变成 10 毫秒。 再想想那么多 10 微秒的操作时延累加起来。 不难看出 swapping 对于性能是多么可怕。

最好的办法就是在你的操作系统中完全禁用 swap。这样可以暂时禁用：

```
sudo swapoff -a
```

**最小主节点数**

minimum_master_nodes设定对你的集群的稳定极其重要。当你的集群中有两个 masters（注：主节点）的时候，这个配置有助于防止 脑裂 ，一种两个主节点同时存在于一个集群的现象。如果你的集群发生了脑裂，那么你的集群就会处在丢失数据的危险中，因为主节点被认为是这个集群的最高统治者，它决定了什么时候新的索引可以创建，分片是如何移动的等等。如果你有 两个 masters 节点， 你的数据的完整性将得不到保证，因为你有两个节点认为他们有集群的控制权。

这个配置就是告诉 Elasticsearch 当没有足够 master 候选节点的时候，就不要进行 master 节点选举，等 master 候选节点足够了才进行选举。此设置应该始终被配置为 master 候选节点的法定个数（大多数个）。法定个数就是 ( master 候选节点个数 / 2) + 1 。 这里有几个例子：

- 如果你有 10 个节点（能保存数据，同时能成为 master），法定数就是 6 。
- 如果你有 3 个候选 master 节点，和 100 个 data 节点，法定数就是 2 ，你只要数数那些可以做 master 的节点数就可以了。
- 如果你有两个节点，你遇到难题了。法定数当然是 2 ，但是这意味着如果有一个节点挂掉，你整个集群就不可用了。 设置成 1 可以保证集群的功能，但是就无法保证集群脑裂了，像这样的情况，你最好至少保证有 3 个节点。

你可以在你的 elasticsearch.yml 文件中这样配置：

```
discovery.zen.minimum_master_nodes: 2
```

但是由于 ELasticsearch 是动态的，你可以很容易的添加和删除节点， 但是这会改变这个法定个数。 你不得不修改每一个索引节点的配置并且重启你的整个集群只是为了让配置生效，这将是非常痛苦的一件事情。

基于这个原因， minimum_master_nodes （还有一些其它配置）允许通过 API 调用的方式动态进行配置。 当你的集群在线运行的时候，你可以这样修改配置：

```
PUT /_cluster/settings
{
    "persistent" : {
        "discovery.zen.minimum_master_nodes" : 2
    }
}
```

这将成为一个永久的配置，并且无论你配置项里配置的如何，这个将优先生效。当你添加和删除 master 节点的时候，你需要更改这个配置。

参考资料：

https://www.elastic.co/guide/cn/elasticsearch/guide/current/important-configuration-changes.html#important-configuration-changes
