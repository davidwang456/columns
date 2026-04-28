# 附录：思考题答案

---

## 第1章

**1. keyBy之后才能做sum的原因？**
keyBy将数据按照key的哈希值分区到不同子任务，每个子任务独立维护该分区内所有key的状态。sum(1)操作依赖于keyed state——Flink需要知道"这个key的当前累计值是多少"，这要求数据在进入聚合算子前已经按key分组。如果不做keyBy就直接sum，Flink无法确定是对全量数据做全局聚合（类似`sum(1)`不加group by），还是每个元素各自独立——语义不明确。

**2. env.execute()在try-finally中能执行到finally吗？**
不能。`env.execute()`是阻塞调用，不会返回。它在Flink作业被cancel或异常退出时才会结束。如果正常cancel，execute()抛出CancellationException，finally可以执行。但如果kill -9强杀进程，finally不会执行。

---

## 第2章

**1. taskmanager.replicas=2, numberOfTaskSlots=2的Slot总数？**
WebUI显示2个TaskManager共4个Slot。`deploy.replicas=2`控制容器实例数（2个TM容器），`taskmanager.numberOfTaskSlots=2`控制每个TM的Slot数量。总Slot数 = 2 × 2 = 4。

**2. 内外两个监听器的Kafka配置？**
需要配置`KAFKA_ADVERTISED_LISTENERS`同时包含内外网地址，用不同端口或协议区分。例如：`PLAINTEXT://kafka:9092`（容器内通信）和`PLAINTEXT://localhost:9093`（宿主机通信）。

---

## 第3章

**1. keyBy后sum算子并行度=1会发生什么？**
所有key的数据全部路由到同一个子任务（分区0），形成单点瓶颈。公式`Math.floorMod(hash, parallelism)`中的parallelism是sum算子的并行度——设为1意味着所有key都取模1=0，全部进入分区0。

**2. flatMap=4, sum=4, Source=1的实际并行利用率？**
Task总数 = Source(1) + flatMap(4) + sum(4) + print(4) = 需要13个Slot。但Source生成的1条数据会通过rebalance分发到4个flatMap（每个收到约25%的数据）。实际load只用了flatMap的1个slot（数据从Source来），sum的4个slot全部用上，print的4个slot全部用上。浪费了flatMap的3个Slot（因为没有数据到达）。

---

## 第4章

**1. Kafka 3个分区、Flink Source并行度=6？**
Flink Kafka Source的并行度不能超过Kafka分区数。如果强行设为6（大于3），只有3个子任务能分配到分区，另外3个子任务空闲（没有可消费的分区）。Kafka Consumer的partition.assignment.strategy决定了分区分配方式（默认RangeAssignor）。

**2. 关闭Checkpoint后JDBC Sink丢了什么保证？**
丢失了端到端Exactly-Once保证。JDBC Sink的两阶段提交依赖Checkpoint的协调——没有Checkpoint，Sink不知道何时提交事务。关闭Checkpoint后：Source侧可能丢数据（没有Offset管理），Sink侧可能重复（每次重启从最新位置消费，但之前写入的数据没被清理），降级为At-Most-Once。

---

## 第5章

**1. Filter vs FlatMap过滤的性能和可读性区别？**
Filter语义更清晰——"通过条件判断保留/丢弃"，适合单一过滤条件。FlatMap适合"条件过滤+数据变换"的组合逻辑。性能上Filter略优（内部实现更轻量，没有Collector开销），但差距微乎其微。可读性上Filter更优（一个方法名就表达了意图）。

**2. RichMapFunction.open()中的Random实例线程安全吗？**
Flink的算子默认每个并行实例单线程执行（一个Task线程处理一个Slot）。但如果算子链中多个算子合并后共用一个线程，不同算子间可能会有共享的成员变量访问——不过这种情况下Random是每个实例独立的成员变量，没有跨线程共享，所以是线程安全的。

---

## 第6章

**1. 10万key，10亿数据，8并行度，倾斜分析？**
最热key分布在1个分区（单个key只能落在一个分区）。假设最热key占5千万次，该分区总数据量 ≈ 5千万 + 其余99999个key的均匀分布。其余7个分区平分剩余9.5亿条数据，每个约1.357亿条。最热分区负载约5千万，其余分区约1.357亿——实际上最热分区的负载更小！因为5千万仅占该分区总数据的一部分，其他7个分区承载了剩下9.5亿条。所以结论：单key倾斜不一定导致分区倾斜——分区内还有大量其他key。

**2. UvAggregator丢失newsId的改造？**
将累加器改为 `Tuple2<String, HashSet<String>>`，第一个字段存newsId。在`createAccumulator()`中返回`Tuple2.of("", new HashSet<>())`，`add()`时填充newsId和userId。或者在`ProcessWindowFunction`中调用`aggregate()`，让ProcessWindowFunction持有窗口的key信息。

---

## 第7章

**1. 空闲分区10秒无数据时Watermark的行为？**
如果设置了`withIdleness(Duration.ofSeconds(120))`，2分钟内无数据的分区被标记为空闲——它的Watermark不再参与全局最小值运算。但如果没设置idleness，该分区Watermark卡在初始值(Long.MIN_VALUE)，全局Watermark永远不推进，所有窗口都不触发。

**2. 检测用户关闭App的窗口类型？**
使用EventTime Session Window。gap设为"App正常关闭的平均间隔"（如5分钟）。用户关闭App时触发session end事件（发送一条'session_end'标记）。Session Window的gap确保"用户没有新行为一段时间后"，窗口自动闭合，触发聚合计算。

---

## 第8章

**1. 5min窗口30s步长的窗口跨度和数量？**
5min/30s = 10个重叠窗口。一个EventTime为15:02:00的事件，落入窗口：从14:58:00开始到15:03:00结束的10个窗口（每个跨度5分钟，每30s一个起始点）。如果步长=1s，同时存在的窗口数=5×60/1=300个——每个事件被复制300份，内存和CPU开销极大。

**2. 会话窗口的gap选择策略？**
最优gap = 业务"自然中断间隔"的P90/P95值。可以通过离线分析历史数据得到。动态gap方案：Flink支持`EventTimeSessionWindows.withDynamicGap()`，根据事件类型/用户等级动态设置不同gap——高级用户gap更短（高频），普通用户gap更长。

---

## 第9章

**1. 订单状态TTL配置策略？**
配置`StateTtlConfig.newBuilder(Time.days(7)).setUpdateType(OnCreateAndWrite)`：
- OnCreateAndWrite：只在状态创建和数据更新时延长TTL——适合"状态变化才延长"的场景
- OnReadAndWrite：每次读取时也延长TTL——适合"需要一直保留活跃状态"的场景
订单完成后7天自动删除状态，用OnCreateAndWrite即可（订单完成后不再有更新，7天后自动过期）。

**2. ListState实现最近N条的高效方式？**
使用`MapState<Long, String>`以时间戳为key，插入时put(timestamp, value)，查询时通过`keys()`获取所有时间戳排序后取最近N个。或使用环形缓冲区（固定长度数组+游标）配合OperatorState。但最简单的方式是用Redis List替代Flink State——`LPUSH + LTRIM`天然支持固定长度队列。

---

## 第10章

**1. Checkpoint间隔计算？**
实际间隔 = max(interval, minPause + averageDuration) = max(10000, 5000 + 8000) = max(10000, 13000) = 13000ms = 13秒。不会积压，因为间隔13秒 > Checkpoint耗时8秒，有5秒的空闲时间。

**2. 版本升级用Checkpoint还是Savepoint？**
用Savepoint。Checkpoint是自动管理生命周期且不保证跨版本兼容的。Savepoint由用户管理，可以长期保留。如果ValueState<Integer>改为ValueState<String>，从旧Savepoint恢复会报序列化错误——需要编写`StateMigration`逻辑或使用`TypeSerializerConfigSnapshot`。

---

## 第11章

**1. Random实例的线程安全性？**
Flink的算子默认单线程执行（一个并行实例一个线程），所以同一实例的map方法不会被多线程并发调用。Random在单线程下是安全的。但如果该算子和其他算子链式合并，同一个Task线程中多个算子共享同一个线程——但Random实例是每个子任务独立的成员变量，没有跨线程共享问题。

**2. 按黑名单类别过滤的State改造？**
将`MapState<String, Boolean>`改为`MapState<String, Integer>`，Integer表示黑名单等级（1-放行/2-记录/3-拦截）。在flatMap中用switch-case判断等级，执行不同的操作。等级可以在运行时通过SideInput或Broadcast State动态更新。

---

## 第12章

**1. SideOutput vs Filter + 双Sink的差异？**
Filter方案需要将相同数据发送到两个下游路径：先按条件判断走SinkA，SinkB要重复消费整条流再做反向判断。SideOutput在同一个算子中完成分流，数据只消费一次，效率更高。缺点是代码复杂度略高（需要定义OutputTag和后续getSideOutput）。

**2. 1个ProcessFunction vs 3个Filter的对比？**
1个ProcessFunction发3个SideOutput → 数据只处理一次，3次条件判断在同一次遍历中完成。3个Filter → 数据被消费3次，每次都要反序列化和遍历。前者可维护性更高（条件集中管理）、性能更好（数据只经过一次）。后者适合条件逻辑独立、且可能独立调整并行度的场景。

---

## 第13章

**1. 无窗口的GROUP BY状态增长？**
```sql
SELECT userId, COUNT(*) FROM login_events GROUP BY userId;
```
状态无限增长——每个新userId都在状态中新增一个条目。虽然Flink会自动管理状态，但没有TTL或窗口约束，状态最终会撑爆内存。Flink SQL会报警告"GroupBy on unbounded table without window"。

**2. Retraction模式下Kafka Topic的数据模式？**
INSERT(+I)、UPDATE_BEFORE(-U)、UPDATE_AFTER(+U)、DELETE(-D)混合。下游消费Kafka Topic的应用会看到中间状态的变更记录，而非最终结果。例如一个COUNT聚合从1变到2：先看到-I(1)，再看到+U(1)，最后+U(2)。消费端需要处理这些Changelog消息。

---

## 第14章

**1. 10个并行实例的IP缓存内存分析？**
每个子任务维护一个100000条IP缓存。如果实际IP分布均匀，总内存 ≈ 100000 × (平均IP串长度+城市名长度) × 10。约(15+10)×100000×10 ≈ 25MB。但如果90%的IP重复，每个子任务各自缓存同样的90%数据——浪费了22.5MB。可用集中式缓存（如Redis）替代，或使用Flink的广播状态在所有子任务间共享。

**2. 近似百分位数的低内存算法？**
TDigest算法——维护一组"质心"（centroid），每个质心代表一组相近数值的均值和权重。通过限制质心数量（如100个）控制内存使用。误差与数据分布有关，通常在0.5-2%之间。Apache DataSketches库提供了TDigest实现。

---

## 第15章

**1. 单指标故障隔离方案？**
一个Flink作业中所有指标共用一个Source——如果其中一个指标的处理逻辑有Bug抛出异常，默认整个作业重启。解方：① 每个指标独立成一个Flink作业，通过不同Consumer Group消费同一个Kafka Topic（资源隔离最好，但管理成本高） ② 使用SideOutput + try-catch，将故障指标的数据分流到死信队列，不影响其他指标。

**2. TopN截断的位置选择？**
① ProcessWindowFunction中截断：提前丢弃非Top10的数据，减少Redis写入。但窗口内仍要保留所有商品的数据（因为不知道哪些是Top10）。② Redis Sink中截断：所有数据写入Redis，Redis用ZREM + ZADD维护Top10——减少Flink侧运算，但增加Redis写入量。③ 外部排序服务：最灵活，但增加了系统复杂度。推荐方案②——Redis的Sorted Set天然支持TopN维护。

---

## 第16章

**1. 100节点集群的最大并发作业数？**
每个节点32GB/16核，TaskManager 16GB/8Slot。每个节点最多跑1个TM（16GB几乎用完节点内存），每个TM 8Slot。总可用Slot = 100 × 8 = 800。每个作业需要64个Slot（并行度64），最多同时运行⌊800/64⌋ = 12个作业。

**2. Application模式main方法中的本地配置文件？**
在Application模式下，main方法在YARN AM的JVM中执行——本地文件不会自动上传。解决方案：① 将配置文件打包到jar的resources目录 ② 使用`-D`参数传递配置 ③ 使用`yarn.ship-files`将本地文件随jar上传 ④ 配置文件存放在外部配置中心（Nacos/Appollo）。

---

## 第17章

**1. savepointTriggerNonce递增的作用？**
Operator通过savepointTriggerNonce的递增来检测是否需要触发新的Savepoint。回滚到旧版本：将镜像版本改为旧版本，递增savepointTriggerNonce（触发一次Savepoint），Operator会自动从Savepoint恢复旧版本代码。关键是initialSavepointPath可以指定使用哪个Savepoint。

**2. K8S优雅关闭与Flink Checkpoint的配合？**
K8S Pod Termination Grace Period（默认30秒）应该大于Flink一次Checkpoint的最大耗时。当Pod收到SIGTERM时，Flink的TaskManager会开始shutdown——此时如果正在做Checkpoint，必须在grace period内完成，否则数据可能丢失。建议：terminationGracePeriodSeconds ≥ checkpointTimeout × 2。

---

## 第18章

**1. 500GB状态但只有10GB堆内存的选择？**
只能选RocksDB State Backend。HashMap State Backend要求所有状态在JVM堆内——500GB远超10GB。RocksDB将状态存储在本地磁盘（LSM-Tree），通过Block Cache和MemTable在内存中缓存热数据，超出部分在磁盘上。

**2. 1000次增量Checkpoint的总大小？**
增量Checkpoint每次只上传"自上次Checkpoint以来变化的SST文件"。如果状态每天变化10%，第一次全量(500GB)，之后每次增量≈50GB。1000次后总大小 ≈ 500GB + 999×50GB ≈ 50TB（包含重复的SST版本）。RocksDB会自动清理，但HDFS上的Checkpoint文件不会——需要定期清理。

---

## 第19章

**1. Checkpoint失败的事务数据清理时机？**
Checkpoint失败意味着事务没有提交——Kafka中的事务数据不会被commit，也不会被read_committed模式的消费者看到。这些数据会在Kafka的`transaction.abort.timed.out.transaction.cleanup.interval.ms`后自动清理（默认1分钟）。

**2. 普通MySQL INSERT能做到Exactly-Once吗？**
不能。普通INSERT没有幂等性——相同数据写两次就是两条记录。改造方案：① 使用INSERT ... ON DUPLICATE KEY UPDATE（有主键的情况下） ② 使用事务性Sink + Checkpoint ③ 使用两阶段提交（Flink的JdbcSink已内置）。

---

## 第20章

**1. 3分区、60%/30%/10%数据的全局Watermark？**
全局Watermark = min(分区1的WM, 分区2的WM, 分区3的WM)。假设10秒的maxOutOfOrderness。分区3的数据延迟45秒——它的Watermark = 分区3收到的最大EventTime - 10秒，比其他分区小很多。全局Watermark被分区3拖慢。

**2. AllowedLateness=10秒的窗口触发次数？**
窗口首次在Watermark ≥ endTime时触发（第1次）。第5秒到达的迟到数据（还在AllowedLateness范围内）→ 第2次触发。第15秒到达的数据超过了AllowedLateness→ 进入侧输出流，不触发窗口。窗口共触发2次。

---

## 第21章

**1. between(0, 1小时)的精确上界匹配？**
默认包含上界——eventTime恰好等于orderTime+1小时的数据也会被匹配。调用`upperBoundExclusive()`后，上界变为开区间——刚好等于边界的数据不匹配。

**2. 一对多场景的Join处理？**
Window Join：如果两条流在一个窗口内出现一对多（一个订单多条支付），Window Join会输出多条匹配结果。Interval Join同理。两种Join都支持一对多，需要在结果端做去重或聚合。如果业务上需要"一个订单只匹配最新的支付记录"，需要在Join后做Top1处理。

---

## 第22章

**1. P99 RT=100ms时capacity的计算？**
理论最大吞吐 = capacity / avgRT = 100 / 0.01 = 10000条/秒。但如果P99=100ms意味着1%的请求耗时100ms——这些慢请求会占用capacity的时间更长，降低有效吞吐。优化：将capacity扩大20-30%（到120-130），对冲尾延迟的影响。

**2. AsyncIO与批量查询的关系？**
批量查询 = 攒一批N条数据，发一次批量API请求（N条结果一起返回）。AsyncIO = 每条数据独立发起请求。批量查询省掉了N-1次网络往返，在网络延迟高时优势明显。但批量查询增加了延迟（必须等N条到齐才发请求）。选型：外部服务支持批量API（如ES的_msearch）、RTT高（跨机房）→ 批量查询。外部服务只支持单条查询、延迟敏感 → AsyncIO。

---

## 第23章

**1. CEP vs 窗口count的区别？**
窗口count只能检测"某时间段内的累积值是否超过阈值"，无法检测事件序列的模式（如A→B→C的特定顺序）。CEP可以检测严格顺序、宽松顺序、否定模式等复杂条件。必须用CEP的场景：欺诈检测（"登录→加急转账→修改密码"的3步序列）、运维监控（"CPU高→内存高→磁盘满"的级联故障）。

**2. consecutive() vs allowCombinations()的区别？**
事件序列`[A, B, A, A]`（A匹配、B不匹配）。
- `consecutive()`模式`.times(3)要求3次严格连续的A`：匹配失败——因为B打断了连续性。
- `allowCombinations()`模式允许多个匹配从不同起点组合：可以匹配位置1,3,4的三个A。

---

## 第24章

**1. 纯CPU算子的反压源？**
算子A（纯CPU）显示反压HIGH，说明它的下游（算子B）处理慢——反压从下游向上游传导。算子A不是瓶颈源，而是被瓶颈下游反压的"受害者"。应该检查算子B（Sink或下一个变换）是否有外部IO阻塞或GC问题。

**2. 并行度从4增加到8，反压比0.8会变吗？**
如果瓶颈是CPU：增加并行度会降低每个子任务的CPU负载，反压比下降，吞吐提升（因为总CPU资源增加）。
如果瓶颈是Sink写入的MySQL：增加Flink并行度只是增加了对MySQL的并发写入压力——MySQL可能更慢了。反压比可能不变甚至恶化。瓶颈在外部系统时，需要提升外部系统的容量。

---

## 第25章

**1. Counter vs Meter？**
Counter是累计值，要算速率需要自己定期快照后做差值（current - last）/ interval。Meter内置了速率计算（通常用指数加权移动平均EWMA），直接提供`getRate()`方法。如果只需看"1分钟速率"，Meter更方便准确；如果需要精确的总量（如"到今天共处理了多少条"），Counter更合适。

**2. Checkpoint耗时从1分钟到5分钟的排查步骤？**
① 先看Metrics：Checkpoint耗时、对齐时间、状态大小——看哪个指标突增。② 再看RocksDB指标：num-running-compactions、write-stall——看是否磁盘IO压力。③ 看HDFS/S3的延迟：同时间段的HDFS写入延迟是否也升高。④ 看GC：老年代GC次数和时间——FGC会导致所有线程暂停。⑤ 最后看反压：反压严重时Barrier对齐慢，Checkpoint耗时增大。

---

## 第26章

**1. Redis维表QPS上限5万、Flink吞吐10万的处理？**
方法：① 增大LOOKUP JOIN的缓存——`lookup.cache.max-rows`从1万增大到10万，`lookup.cache.ttl`从60秒增大到300秒，缓存命中率提升到95%以上，实际QPS降到5000。② 对维表做读写分离——主库同步到只读副本，多个Flink作业分摊查询压力。③ 在Flink侧做预聚合——先keyBy用户ID做局部聚合，减少维表查询次数。

**2. ASC排序取末尾（BottomN）的区别？**
性能上：ASC取末尾和DESC取头部在算法上等价（都是排序取TopN）。逻辑上：DESC Top10 = 成交额最高的10个商品；ASC Top10 = 成交额最低的10个商品。如果需求是"尾部商品"（成交额最低），用ASC；如果是"头部商品"（成交额最高），用DESC更符合业务直觉。

---

## 第27章

**1. 两个作业共享相同UID的危险？**
Savepoint恢复时，Flink根据UID匹配状态。如果两个不同作业使用了相同的UID，从Savepoint恢复时Flink无法区分状态属于哪个作业——可能导致状态串号，A作业恢复了B作业的状态。UID必须在全局唯一。

**2. 并行度从16降到8的状态恢复注意？**
KeyedState支持并行度变更——savepoint/checkpoint中的key-group会在恢复时自动重新分配。OperatorState（非keyed，如Kafka Source的Offset）的并行度变更需要特殊处理——Kafka Source的新版Connector已经支持(ListState存储所有分区的Offset，恢复时按需分配)。但如果使用旧版SourceFunction，并行度降低可能丢失部分Source的状态（未分配的Offsets）。

---

## 第28章

**1. MySQL DELETE在Hudi中的映射？**
Flink CDC捕获到MySQL的DELETE事件后，Changelog Stream中的RowKind = DELETE。Hudi的`MERGE_ON_READ`表类型处理DELETE：写入一个删除标记（tombstone），查询时过滤掉被删除的记录。`COPY_ON_WRITE`表类型：重写整个parquet文件（不含被删除的记录），开销更大。

**2. 全量+增量切换时的数据一致性？**
Flink CDC的全量扫描使用**分段快照算法**：将表按主键分成多个chunk（如每10万行一个chunk），每个chunk在一个事务中读取，同时记录该事务的binlog位置。全量chunk读完后，从所有chunk中最小的binlog位置开始消费增量。如果一个chunk读完后到切换前有数据变更——这些变更已经记录在binlog中，增量阶段会再次读到，通过主键UPSERT覆盖旧值。保证了最终一致性。

---

## 第29章

**1. 算子链合并为1个JobVertex，并行度=10，5个Slot？**
ExecutionGraph中只有一个ExecutionVertex（一个JobVertex只有一个Task），但Task执行时内部包含Source+Map+Filter的链式逻辑。在5个Slot上部署：第1轮部署5个并行的Task，第2轮部署剩余5个——共10个Task线程分布在5个Slot上（每个Slot承载2个Task）。

**2. Region Failover恢复状态损坏的算子？**
Region Failover只重启受影响的Region（算子组），不包含Source——Source不会回退Offset。因此，非Source算子的状态损坏后，Region Failover只能从最近一次Checkpoint恢复该Region的状态（但状态本身可能已经损坏了）。实际上需要：① 从更早的Savepoint恢复整个作业（全量重启）② 或手动修复损坏的状态（通过State Processor API读取和修改Savepoint）。

---

## 第30章

**1. 同一算子链中A(CPU密集)和B(IO等待)的问题？**
A和B共享一个Task线程——如果A占满CPU，B的IO等待虽然不消耗CPU，但B的IO回调只能在A让出CPU时才能执行。这导致B的IO处理被延迟。解方：在A和B之间调用`disableChaining()`或`startNewChain()`，让A和B在不同线程中执行。

**2. 并行度64的网络内存计算？**
每个ResultPartition需要 `numChannels × (buffersPerChannel + floatingBuffersPerGate)` 个buffer，每个buffer默认32KB。假设上下游各64个并发子任务，`buffersPerChannel=2`，`floatingBuffersPerGate=8`。每个ResultPartition需要 64 × (2 + 8) = 640个buffer = 20MB。上下游各有64个并行，总共约64 × 20MB × 2 = 2.56GB。

---

## 第31章

**1. OnCreateAndWrite vs OnReadAndWrite的TTL差异？**
用户每天登录一次且读取状态：OnCreateAndWrite下，只有状态写入/更新时才延长TTL——读取操作不延长。第1天创建，第8天过期（即使每天读取）。OnReadAndWrite下，每次读取操作也延长TTL——第1天创建，第1天读取→TTL续到第8天，第2天读取→续到第9天，以此类推，只要每天读取就不会过期。

**2. RocksDB写放大因子为什么~10？**
Level Style Compaction：数据从L0→L1→...→L6，每层数据量是上一层的10倍(默认)。一条数据从写入到最终稳定在L6，平均经历10次Compaction（每次Compaction读写一次磁盘），所以写放大≈10。如果从7层减少到4层：写放大降低（~4），但读放大增加（因为每层的数据量更大，查询时需要搜索更多SST文件）。

---

## 第32章

**1. SplitEnumerator无法访问外部资源？**
JM仅通过RPC与TM通信，无法直接访问TM所在容器的本地资源。如果SplitEnumerator需要查数据库获取分片信息，应该在JM端内网可达的服务上做。方案：① 将分片信息写入ZK/etcd，Enumerator从ZK读取 ② Enumerator通过SourceEvent向Reader发送指令，Reader查询后回复 ③ Enumerator通过JM的内部服务（如HA服务）间接访问外部资源。

**2. prepareCommit() vs snapshotState()的区别？**
`snapshotState()`在Checkpoint时调用，返回的是"Writer当前的完整状态"（用于保存到持久化存储）。`prepareCommit()`在Checkpoint准备提交阶段调用，返回的是"待提交的数据列表"——这些数据需要在下游做原子提交。前者用于故障恢复，后者用于两阶段提交的Pre-commit阶段。

---

## 第33章

**1. FIRE vs FIRE_AND_PURGE的区别？**
FIRE：触发窗口函数输出结果，但不清空窗口buffer。第一次FIRE输出结果后buffer保留，第二次FIRE时窗口内还包含第一次触发前的所有数据——输出可能是累计值。FIRE_AND_PURGE：触发窗口函数后清空buffer。如果两次触发之间没有新数据，第二次触发结果为空。选择：累加场景（如总PV）用FIRE；独立窗口场景（如每分钟独立PV）用FIRE_AND_PURGE。

**2. 大窗口（7天）的State和Timer管理？**
窗口越大，同一时间活跃的窗口越少（可能只有1个），但窗口内的数据量巨大。State中存储7天的元素，RocksDB可以承载。Timer方面：EventTime窗口只注册一个窗口结束时间的Timer——数量与key数相同。100万key=100万Timer，TimerService可以管理。关键：窗口结束时一定要通过`clear()`清理State，否则State永驻。可以使用AllowedLateness + 延迟清理机制。

---

## 第34章

**1. fromDataStream() vs fromChangelogStream()的区别？**
`fromDataStream()`用于Append-only流（只有INSERT操作），底层假设数据只有新增。`fromChangelogStream()`用于有UPDATE/DELETE的流（RowKind包含-U和+U标记），底层支持Retraction。如果使用fromDataStream注册一个含UPDATE的流，Flink SQL中的聚合结果会不正确（聚合值不会回撤）。

**2. 自定义算子(HLL) vs SQL COUNT DISTINCT的性能对比？**
SQL COUNT DISTINCT + TUMBLE窗口：Flink内部维护一个HashSet做精确去重，每个窗口一个Set。每分钟状态大小 = UV数。大UV场景(百万级)，内存占用巨大。
自定义算子(HLL + 滑动窗口)：固定内存(每个并行约12KB)，误差~2%。吞吐方面：两者CPU开销接近，但HLL节省了大量序列化和状态读写开销。自定义算子可以快2-5倍（大UV场景）。

---

## 第35章

**1. saltFactor的选择策略？**
saltFactor = 预期最大倾斜比例 × 10。假设热key数据占总量10%、其余90%均匀分布——将热key打散到10份，每份占1%。saltFactor=10即可。但需要考虑第二阶段对性能的影响——saltFactor越大，stage2的key数量越多。建议saltFactor = max(10, 并行度/2)。

**2. 自定义Partitioner vs Rebalance？**
Rebalance是轮询（Round-Robin）——不考虑key，单纯将数据均匀分配到下游。自定义Partitioner可以根据key的hash（或自定义逻辑）将数据路由到指定分区。两阶段聚合 = 先Rebalance(通过加盐打散) → 局部聚合 → 再keyBy → 全局聚合。本质上是"先均匀分散再按key聚合"的两步策略。

---

## 第36章

**1. Active-Active双活模式下Kafka分区接管？**
如果每个机房消费一半分区（DC1: p0-p2, DC2: p3-p5）。DC1挂后，Flink作业重新提交，Kafka Consumer Group Rebalance会将p0-p2分配给DC2的消费者——前提是DC2的Flink作业有足够的消费者线程（并行度≥分区总数）。使用相同的Group ID即可触发Rebalance。

**2. 数据新鲜度指标的虚高/虚低？**
虚高：作业没有新数据时（如凌晨低峰），最新eventTime停留在一个旧值——当前时间减去旧值导致"延迟"增长，但实际上是正常情况。虚低：数据大量突发到达时，最新eventTime迅速追上当前时间，新鲜度短暂降低——但不是真正的延迟改善。解决方案：结合"是否有新数据"指标一起判断，或使用"数据新鲜度-P99"（排除了空闲期的干扰）。

---

## 第37章

**1. BERT模型的推理方式选择？**
BERT（500MB + 大量计算）不适合Embedded模式——加载500MB模型到每个Flink子任务会导致OOM，且GPU无法被Flink直接利用。推荐Remote模式：TF Serving部署在GPU节点，Flink通过AsyncIO或gRPC调用。延迟预算100ms，网络RTT≈5ms+推理耗时≈30ms，余量充足。

**2. 累积特征的数值处理？**
累积值（如总登录次数）会随时间无限增长，不适合直接作为模型特征。推荐做法：① 对数变换 `log(1 + count)`——压缩数值范围，使分布更接近正态 ② 归一化到[0,1]——除以"理论最大值"或历史P99值 ③ 分段特征——将count映射到"低/中/高"3个桶。推荐方案：对数变换+周期重置（每月重置一次）。

---

## 第38章

**1. 作业合并的优缺点？**
优点：① 减少JobManager内存消耗（N个作业→1个JobGraph） ② 统一的Checkpoint管理（一次快照保存所有特征状态） ③ 减少Kafka Source重复消费 ④ 数据共享更高效（无需外部存储中转）。
缺点：① 耦合——一个特征的上线/回滚影响所有特征 ② 资源隔离差——一个特征的Bug拖垮所有特征 ③ 运维复杂——全量发布 vs 灰度发布不可控。

**2. 实时特征血缘追踪系统的设计？**
埋点方案：① Source阶段——记录Topic名称、分区、Offset → 作为血缘起点 ② Transformation阶段——在每个ProcessFunction/MapFunction中，通过OperatorMetadata记录算子UID、输入/输出的State名称 ③ Sink阶段——记录写入的Redis key和存储格式。实现：通过自定义Operator的snapshotState()方法，将血缘元数据持久化到外部存储（如Elasticsearch）。查询时才可以通过"Redis key → 上游算子 → Kafka Topic"的链路追溯。
