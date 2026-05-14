# 第17章：TSDB存储引擎——Write-Ahead Log与Block机制

> 中级篇开篇。本章深入Prometheus TSDB的内部构造，详解WAL、Block、Compaction三大核心机制，是容量规划与故障恢复的基石。

---

## 一、项目背景

运维团队使用Prometheus监控已有三个月，一切运行平稳，直到某天数据中心意外断电——UPS只撑了五分钟，所有服务器瞬间宕机。来电后，老李第一时间重启Prometheus，却发现启动日志卡在了一行：

```
level=info msg="Replaying WAL, this may take a while"
```

这一"while"就是整整20分钟。更令人不安的是，重启后发现最近15分钟的监控数据全部丢失——恰好是断电前那段时间。老李赶紧检查数据目录`/prometheus/data/`，发现下面躺着几百个形如`01HXXXXXX`的文件夹，总共占用了300GB磁盘空间。他陷入了困惑：这些blocks到底是怎么组织的？WAL又是什么？Prometheus为什么不能像MySQL那样直接写磁盘？

这正是TSDB（Time Series Database）作为Prometheus核心引擎的独特之处。与关系型数据库不同，时序数据库需要应对的是一类高度特殊的工作负载：每秒数十万次写入、极少更新与删除、查询集中在时间范围上、数据天然按时间衰减。Prometheus的TSDB针对这些特征设计了WAL + Block + Compaction的三层架构，用"空间换时间"和"批量换吞吐"的思路，在写入性能、查询性能和存储成本之间取得了精心设计的平衡。

然而，多数运维人员对TSDB的内部机制一无所知。WAL（Write-Ahead Log）既要保证数据不丢，又不可避免地拖慢启动回放；Block是数据的实际存储单元，每两小时生成一个，日积月累可达数百个；Compaction（压缩合并）在后台默默运行，但某个凌晨可能突然吃掉所有CPU和I/O。只有真正理解这三者的协作关系，才能在容量规划、故障恢复和性能调优时做出正确判断。本章作为中级篇的开篇，将带你逐层拆解Prometheus存储引擎的内部构造。

---

## 二、剧本式交锋对话

**小胖**：（挠头）大师，我有个问题憋了很久。Prometheus自己搞了一套TSDB存储，为什么不用MySQL或者InfluxDB呢？MySQL这么多年了，稳定又成熟，还有现成的运维工具。

**大师**：（放下茶杯）你这个问题问得好。你想想，监控数据有什么特点？

**小胖**：嗯……每秒都在写，读得相对少，数据量大，而且老数据基本没人看，过一阵子就可以删了。

**大师**：这就是关键。MySQL的B-Tree结构是为"随机读写"优化的，更新、删除、事务样样都要兼顾。但时序数据是"追加写、按时间删、极少更新"。你用B-Tree存时序数据，每插入一行都可能触发页分裂，写放大严重，磁盘碎片越积越多。InfluxDB的早期版本确实用过LevelDB，但它和Prometheus的设计哲学不同——Prometheus追求的是极简部署，单机搞定，不依赖外部存储。

**小白**：那Prometheus的TSDB到底怎么存的？我看数据目录里有一堆叫block的文件夹，还有wal目录，这些是什么关系？

**大师**：这个架构，我用"库房管理"打个比方你就明白了。

（大师拿起白板笔开始画）

想象你是一个库房管理员，每天有源源不断的货物入库。你会怎么做？

**小胖**：先记录收货单，然后货物放到待上架区，有空了再搬到正式货架上。

**大师**：没错！Prometheus也是这样做的。**WAL就是收货单据**——每个样本数据到达时，Prometheus先把它追加写到WAL文件里，这是一种顺序写，速度极快，哪怕突然断电，数据也在WAL里丢不了。这跟数据库的redo log是一个道理。

**小白**：那Head Block呢？

**大师**：**Head Block就是待上架区**，存在内存里。数据一边写WAL，一边追加到Head Block的内存结构中。Head Block覆盖最近两小时的数据，还没满的时候都待在内存里，查询非常快，因为不需要读磁盘。

**小胖**：那两小时到了呢？

**大师**：Head Block满了就会"flush"，从内存刷到磁盘，变成一个**Persistent Block**，就像货物从待上架区搬上了正式货架。磁盘上的Block是不可变的（immutable），一旦写成就再也不会被修改——这也是时序数据"只追加不更新"特性的体现。

**小白**：我注意到每个block文件夹里有好几个东西：chunks/、index、meta.json、tombstones……这些做什么用的？

**大师**：（逐个点指）`chunks/`里面是压缩后的时序数据本身，Prometheus用了类似Facebook Gorilla论文的压缩算法，对时间戳和数值分别做了delta-of-delta和XOR压缩，压缩率能做到10倍以上。`index`是倒排索引，比如你要查`{job="nginx",instance="web-01"}`的所有指标，索引能帮你快速定位到对应的chunk，不用全表扫描。`meta.json`是元信息，记录了这个block的时间范围、包含哪些指标。`tombstones`呢，是墓碑标记——时序数据不能原地删除，只能在墓碑里记一笔"这个时间段的这条数据已删"。

**小胖**：那几百个小block不会越来越多吗？查询的时候岂不是要扫几百个文件？

**大师**：问到关键点了。这就引出了**Compaction——整理货架**。假设你的货架上堆满了小箱子，找东西很费劲，聪明的管理员会定期把相邻的小箱子合并成大箱子，这样货架更整齐，翻找也快。Prometheus的后台Compaction就是这样：它是Level-based的，把同一层的小block合成一个更大的block，比如三个2小时的block合出一个6小时的，三个6小时的再合出18小时的……这样block数量就控制住了，查询时跨block的合并次数也大大减少。

**小白**：那为什么偏偏是两小时一个block？不能一小时或者四小时吗？

**大师**：这是一个权衡。两小时是一个经验值。时间太短（比如30分钟），block碎片化严重，文件数量爆炸，查询要扫的元数据太多；时间太长（比如6小时），Head Block在内存中占用太大，万一进程crash，WAL回放的时间也会变长。两小时刚好在写入吞吐、内存占用和查询效率之间取得平衡。`--storage.tsdb.min-block-duration`和`--storage.tsdb.max-block-duration`可以调整，但一般不建议动。

**小胖**：那WAL文件会不会无限增长？数据都写到block里了，WAL还有用吗？

**大师**：好问题。WAL有**checkpoint机制**。当Head Block成功flush到磁盘后，Prometheus会创建一个checkpoint，把WAL中对应这段数据的记录标记为"已持久化"，后续就可以把旧的WAL segment文件安全删除了。你在日志里看到的`msg="WAL checkpoint complete"`就是这个过程。这样WAL始终保持在一个可控的大小，不会无限膨胀。

**小胖**：（若有所思）所以整体就是：数据来了先写WAL和内存Head → 两小时满了flush成磁盘Block → 后台Compaction合并小Block → 超过retention的老Block删掉……是这样吗？

**大师**：（赞许地点头）完全正确。这套WAL + LSM-Tree混合架构，就是Prometheus能单机扛住每秒百万样本写入的秘密。

---

## 三、项目实战

### 环境准备

- Prometheus已运行至少2小时，产生了持久化Block
- 安装`promtool`命令行工具（通常随Prometheus发行包附带）
- 有权限访问Prometheus数据目录

### 步骤1：探索TSDB数据目录结构

首先进入Prometheus数据目录，观察整体布局：

```bash
cd /prometheus/data/
ls -la
```

输出类似：

```
drwxr-xr-x  4 root root 4096 May 14 10:00 01HXXXXXXXXXXXXXXX/
drwxr-xr-x  4 root root 4096 May 14 08:00 01HYYYYYYYYYYYYYYY/
drwxr-xr-x  4 root root 4096 May 14 06:00 01HZZZZZZZZZZZZZZZ/
drwxr-xr-x  2 root root 4096 May 14 12:00 chunks_head/
drwxr-xr-x  2 root root 4096 May 14 12:00 wal/
-rw-r--r--  1 root root    0 May 14 00:00 queries.active
```

逐个理解：

- **`01HXXXXXX/`等文件夹**：每个是一个Persistent Block（持久化数据块），文件夹名是ULID编码（比UUID更利于排序），包含了该段时间内所有时序数据的压缩存储。
- **`chunks_head/`**：当前活跃的Head Block的数据在磁盘上的内存映射文件。Head Block虽然逻辑上是"内存"的，但Prometheus使用了mmap技术将chunk数据映射到磁盘，防止内存溢出的同时也能快速访问。
- **`wal/`**：Write-Ahead Log的存储目录，记录所有近期写入操作。
- **`queries.active`**：一个标记文件，记录当前是否有活跃查询，用于优雅关闭时判断。

进一步查看WAL目录：

```bash
ls -lh wal/
```

输出示例：

```
-rw-r--r-- 1 root root 128M May 14 11:55 00000001
-rw-r--r-- 1 root root 128M May 14 11:58 00000002
-rw-r--r-- 1 root root 128M May 14 12:01 00000003
-rw-r--r-- 1 root root  96M May 14 12:03 00000004
```

WAL由多个128MB的segment文件组成，新数据追加写入编号最大的文件，当segment写满128MB后自动切到下一个。这种分段设计既是性能优化（顺序写、方便删除老segment），也便于并行回放。

查看一个Block的内部结构：

```bash
ls -lh 01HXXXXXXXXXXXXXXX/
```

输出示例：

```
drwxr-xr-x 2 root root 4.0K May 14 08:00 chunks/
-rw-r--r-- 1 root root  25M May 14 08:00 index
-rw-r--r-- 1 root root  256 May 14 08:00 meta.json
-rw-r--r-- 1 root root    0 May 14 08:00 tombstones
```

| 组件 | 作用 |
|------|------|
| `chunks/` | 包含多个chunk文件，存储压缩后的时序样本数据（timestamp + value） |
| `index` | 倒排索引，记录"标签组合 → chunk引用"的映射关系，是查询性能的核心 |
| `meta.json` | Block元信息，包含时间范围（minTime/maxTime）、统计信息（numSamples, numSeries）和compaction层级 |
| `tombstones` | 墓碑文件，记录已删除的序列及时间范围，实现软删除 |

### 步骤2：使用promtool分析TSDB

`promtool tsdb list`可以一览所有block的概况：

```bash
promtool tsdb list /prometheus/data/
```

输出示例：

```
BLOCK ULID                  MIN TIME       MAX TIME       DURATION     NUM SAMPLES  NUM CHUNKS  NUM SERIES
01HXXXXXXXXXXXXXXX          2026-05-13T20:00:00Z  2026-05-13T22:00:00Z  2h0m0s    8562341      18423       4521
01HYYYYYYYYYYYYYYY          2026-05-13T18:00:00Z  2026-05-14T00:00:00Z  6h0m0s   24587123      50231      12450
```

注意第二个block的DURATION是6小时——它已经不是原始的2小时block了，而是经过Compaction合并后的产物。第一个2小时block会在后续compaction中被合并，最终消失。

`promtool tsdb analyze`提供更详细的诊断：

```bash
promtool tsdb analyze /prometheus/data/
```

摘录关键输出：

```
Block count: 147
Min time: 2026-04-29T12:00:00Z
Max time: 2026-05-14T12:00:00Z

Label names most involved in churning:
instance      185234
pod           120456
container      98432
```

解读：
- **Block count: 147**：15天retention、每2小时一个block，理论值=15×12=180，147在合理范围内（Compaction减少了数量）。
- **时间跨度**：确认是否符合retention设置。
- **Churning排行**：`instance`标签变化最频繁，说明你的监控目标数量多或经常变化（K8s Pod漂移、弹性伸缩等）。高churn会增加TSDB的索引压力，是扩容时需要重点关注的指标。

### 步骤3：观察WAL行为

查看WAL的当前大小和增长速度：

```bash
du -sh wal/
```

通常在几百MB到几GB之间，取决于两小时内写入的数据量。一个经验公式：

```
WAL大小 ≈ Head Block中的未压缩chunk数据量（通常为原始样本量的1-2倍）
```

观察WAL segment的变化：

```bash
# 间隔5秒连续查看，观察文件大小变化
watch -n 5 "ls -lh wal/ | tail -5"
```

你会看到当前segment文件持续增大（因为新数据在追加），而较老的segment保持不变。

查看Prometheus的WAL相关日志：

```bash
# 启动时
level=info msg="Replaying WAL, this may take a while" component=tsdb

# 运行中（触发checkpoint时）
level=info component=tsdb msg="WAL checkpoint complete" first=XXXX last=XXXX duration=3.2s
```

如果WAL回放时间过长（超过5分钟），说明你的数据量较大或磁盘I/O较慢。此时应检查：
- `--storage.tsdb.min-block-duration`是否设置过长（默认2h无需调整）
- WAL checkpoint间隔是否合理
- 磁盘是否为SSD（WAL回放涉及大量随机读）

### 步骤4：理解Block的生命周期

用一条时间线来梳理Block从诞生到消亡的完整过程：

```
T0        → Prometheus启动，打开或创建Head Block（内存）
T0 ~ T2h  → 样本持续写入Head Block（内存）+ 同步追加WAL
             此时查询可以覆盖"Head + 所有Persistent Block"
             
T2h       → Head Block满（到达max-block-duration的默认2小时）
            触发flush：将Head中的所有series和chunks持久化到磁盘
            生成新的Persistent Block（如01HAAAA/），同时创建新Head
            日志中可见：msg="compaction" group=0 （flush也视为一种compaction）
            
T2h ~ T8h → 后台Compaction持续运行
            同一Level的block达到一定数量后触发合并
            例：3个2h block → 1个6h block
            再往上：3个6h block → 1个18h block  （实际上限默认为max-block-duration的10%，即约12分钟，见下文说明）
            
T8h+      → 最老的block的maxTime超过了retention.time（默认15天）
            下次Compaction周期中被标记删除
            日志：msg="deleting obsolete block" ulid=01HZZZZ/
```

> 注：Prometheus TSDB的Compaction维度实际受`--storage.tsdb.max-block-duration`控制，默认值为retention.time的10%。例如retention.time=15d，则max-block-duration=36h，最终最大的compacted block不会超过36小时。

你可以通过`promtool tsdb list`直观验证这个过程：观察不同MIN TIME的block，老block的DURATION通常更大（已被合并），而最新的block仍然是2h的。

### 步骤5：配置Retention策略

Prometheus支持两种retention策略，可同时设置，以先达到条件者为准：

```bash
# 1. 按时间保留（默认15天）
prometheus --storage.tsdb.retention.time=30d

# 2. 按磁盘大小保留（最大100GB）
prometheus --storage.tsdb.retention.size=100GB

# 两者同时设置：哪个条件先触发就按哪个清理
prometheus --storage.tsdb.retention.time=30d --storage.tsdb.retention.size=100GB
```

验证存储占用与retention的一致性：

```bash
# 查看当前数据目录总大小
du -sh /prometheus/data/

# 确认最老block是否在retention范围内
promtool tsdb list /prometheus/data/ | head -20
# 检查MIN TIME列，最早的时间不应早于"当前时间 - retention.time"

# 通过Prometheus HTTP API确认（需Prometheus运行中）
curl http://localhost:9090/api/v1/status/tsdb | python3 -m json.tool
```

### 可能遇到的坑

**1. 突然断电后WAL回放慢**

现象：Prometheus启动后长时间卡在"Replaying WAL"。原因：WAL segment数量太多或磁盘性能不足。缓解：检查WAL目录，如果segment数量超过100个，说明数据刷盘速度跟不上写入速度。可考虑使用更快的SSD，或适当缩短`--storage.tsdb.min-block-duration`（不推荐低于1h）。

**2. Compaction时CPU/磁盘I/O飙升**

现象：每两小时左右出现一次CPU和I/O高峰，持续数分钟。这是正常的——Compaction需要对多个block的全量数据重新编码和压缩。如果影响业务，可通过`--storage.tsdb.max-block-duration`和调整compaction并发度来平滑负载。但大多数场景无需干预。

**3. Block数量过多导致查询慢**

正常情况下Compaction会自动收敛block数量。但如果因为Prometheus异常关闭、磁盘满等导致Compaction中断，可能出现数百个未合并的小block。此时查询会变慢（需要扫描更多block的index），解决方法是确保Prometheus有足够的时间和资源完成Compaction——启动后等待几个Compaction周期即可恢复。

**4. Retention清理不及时**

确认Prometheus日志中无`deleting obsolete block`相关的错误。最常见的原因是磁盘空间不足导致删除失败，或因文件权限问题无法删除block目录。`promtool tsdb list`中如果出现超过retention的block且持续存在，说明清理异常。

### 测试验证

验证你的理解：

```bash
# 1. 对比promtool输出和Prometheus API，确保一致
promtool tsdb list /prometheus/data/ | wc -l
curl -s http://localhost:9090/api/v1/status/tsdb | jq '.data.blockCount'

# 2. 确认最老block在retention内
# promtool tsdb list第一行（最老）的MIN TIME
# 当前时间 - retention.time 应早于该值（即老block在保留期内）

# 3. 统计Compaction效果
promtool tsdb list /prometheus/data/ | awk '{print $4}' | sort | uniq -c
# 可以直观看到不同duration的block数量分布
```

---

## 四、项目总结

### TSDB架构总览

Prometheus TSDB的数据写入与存储路径可以概括为：

```
样本写入 → WAL（预写日志，持久化保证）
          ↓（同步追加）
        Head Block（内存，覆盖最近2h）
          ↓（flush）
        Persistent Block（磁盘，不可变）
          ↓（compaction）
        合并后的大Block（减少碎片，优化查询）
          ↓（超过retention）
        标记删除，清理磁盘
```

这是一套典型的**LSM-Tree变体架构**：写入先到内存结构和日志，再批量刷到磁盘，后台异步合并。与B-Tree相比，它天然适合"大量追加写入、极少原地更新"的时序场景。

### 关键参数速查

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--storage.tsdb.retention.time` | 15d | 数据保留时长，超过则删除 |
| `--storage.tsdb.retention.size` | 0（禁用） | 数据目录最大占用，超过则删除最老block，与time取先到者 |
| `--storage.tsdb.max-block-duration` | retention的10% | 单个block的最大时长，控制Compaction后的block大小上限 |
| `--storage.tsdb.min-block-duration` | 2h | Head Block满后flush的最小等待时间，一般不建议修改 |

### 适用场景与配置建议

- **小规模（<100 targets）**：默认配置即可，retention可延长至30d或60d，磁盘压力不大。
- **中等规模（100~1000 targets）**：关注block数量和Compaction周期，配合`retention.size`防止磁盘写满。
- **大规模（>1000 targets）**：必须使用SSD，关注WAL吞吐和Compaction性能，考虑通过`--storage.tsdb.max-block-duration`控制合并后block的大小（太大导致单次Compaction耗时过长）。
- **查询密集型**：适当调大`--storage.tsdb.max-block-duration`让block更大，减少查询时需要扫描的block数量。

### 注意事项

1. **WAL与Block必须在同一文件系统**。TSDB使用原子rename操作将Head Block转为Persistent Block，跨文件系统的rename不是原子的，可能导致数据损坏。
2. **避免NFS/CIFS等网络存储**。网络延迟会严重拖慢WAL写入和索引查询，轻则性能下降，重则数据不一致。
3. **SSD优于HDD**。Compaction涉及大量随机读（从多个block读取数据重新编码），SSD的随机I/O性能远优于HDD。

### 常见踩坑案例

**案例一：retention.time设置太长导致磁盘满。** 某团队监控了500个节点、每秒约20万samples，retention.time设了90天。三个月后数据目录膨胀到1.8TB，磁盘满导致Prometheus无法启动。解决：加设`--storage.tsdb.retention.size=1TB`作为硬限制，比time更早生效。

**案例二：WAL目录与data目录分离导致性能问题。** 某运维将`--storage.tsdb.path`和`--storage.tsdb.wal-segment-size`指向不同挂载点，试图优化。结果每次Head Block flush时，跨文件系统的rename失败，block留在了中间状态，Compaction无法继续。结论：不要试图分离WAL和数据目录。

**案例三：Compaction卡住导致block堆积。** 某次Prometheus升级后，Compaction线程因为旧版本遗留的格式不兼容而反复失败，block数从50个增长到800个，查询延迟从毫秒级飙到秒级。解决：停止Prometheus，使用`promtool tsdb bench`检查可用的block，删除损坏的block后重建。

### 思考题

1. 如果Prometheus每秒写入10万samples，retention.time设置为15天，每个sample平均占用2字节（压缩后），请预估需要多少磁盘空间？考虑WAL、索引和元信息的额外开销，实际约需多少？

2. WAL和Block的协作关系类似于数据库中的哪种经典机制？Prometheus TSDB为什么不使用B-Tree作为底层存储结构，而是采用了类似LSM-Tree的方案？这两种结构在时序场景下的优劣分别是什么？
