# 第28章：Thanos——全局视图与长期存储

## 一、项目背景

某互联网公司在全球拥有8个数据中心，每个数据中心独立部署了一套Prometheus用于监控本区域的服务器、应用和网络设备。全球运维团队有一个长期以来的痛点：每当CTO要求查看"过去6个月所有数据中心的总QPS趋势"，运维工程师就得依次登录8个Grafana实例，逐个数据中心查询数据，再把8份数据手动汇总到Excel里。这个过程不仅繁琐低效，更致命的是——Prometheus本地存储默认只保留15天数据，6个月前的数据早已被删除，想查也查不到了。

CTO最终拍板："我们需要一套能看任意历史时间、能跨所有数据中心汇总查询的监控架构。"团队经过调研发现，Thanos正是为这两个问题而生——它不是在Prometheus之外另起炉灶，而是在每个Prometheus旁边部署一个轻量级的Sidecar组件，自动将数据上传到对象存储（如AWS S3或MinIO），再通过Querier组件对外提供统一的全局查询入口。

Thanos的核心价值可以用四个关键词概括：**长期存储**——Sidecar自动将Prometheus的TSDB block上传到对象存储，实现理论上的无限数据保留；**全局查询**——Querier通过Store API同时访问多个Prometheus Sidecar和Store Gateway，将分布在各地的数据聚合后返回；**自动去重**——当多个HA副本的Prometheus采集同一target时，Querier根据replica标签自动选取最新的那份数据；**降采样**——Compactor将历史数据从原始精度逐步粗化为5分钟粒度和1小时粒度，在大时间范围查询时用粗精度数据替代，既提升查询速度又降低存储成本。简而言之，Thanos是Prometheus的"增强插件"而非替代品，它以零侵入的Sidecar模式为Prometheus补齐了长期存储和全局查询两块短板。

## 二、剧本式交锋对话

**小胖**："大师，我们公司要在8个数据中心搭Thanos，我看了一圈文档，发现组件也太多了吧——Sidecar、Querier、Store Gateway、Compactor、Ruler、Receiver……这些组件到底分别干嘛的？能不能用一句人话讲清楚？"

**大师**："一句话总结：Sidecar负责把Prometheus的数据搬运到对象存储，Store Gateway负责从对象存储里读数据给Querier查，Querier就是一个统一的查询入口，你问它一个问题，它去问所有Sidecar和Store Gateway，然后把结果汇总去重后返回给你。Compactor是后台整理工，把小块合并成大块、生成降采样数据。Ruler是在Thanos层做全局告警评估的，避免你每套Prometheus都要配一份告警规则。Receiver是代替Prometheus Remote Write直接把数据写入Thanos，适用于那种不需要本地Prometheus的场景。"

**小白**："等等，Sidecar到底是怎么工作的？它是直接把Prometheus的内存数据读出来然后上传吗？"

**大师**："不是。Prometheus每2小时会将内存中的时序数据刷成一个TSDB block写入磁盘，Sidecar并不读Prometheus进程内存，而是监听Prometheus的TSDB数据目录——发现有新block产生，就把它上传到对象存储。这也是为什么Sidecar必须和Prometheus共享同一个TSDB volume，它在Docker里需要挂载Prometheus的`/prometheus`目录。另外Sidecar还提供Store API接口，Querier可以通过gRPC直接查Sidecar拿到Prometheus还没上传的最新数据，所以Querier查到的永远是'本地最新+云端历史'的完整数据。"

**小胖**："那我有个实际问题——我们生产环境每个数据中心都是2副本Prometheus做HA，两台Prometheus采集的target完全一样。Thanos Querier查的时候岂不是会返回两份相同的指标？它是怎么去重的？"

**大师**："好问题。这就是`--query.replica-label`参数的作用。你给每对HA Prometheus打上同一个external label，比如`replica="A"`和`replica="B"`。Querier收到两份相同指标时，会用一种叫做'Penalty Deduplication'的算法——它给每个副本打分，重复的数据加penalty，最终选择'冲突指标最少'的那份数据。简单说就是：两份数据中如果只有一台有当前时间点的最新数据，就选那台；如果两台都有，选延迟更低或更新时间更近的那台。但要注意，去重只在查询层发生——对象存储里两份副本的数据都会存着，这会多占存储空间。"

**小白**："那Store Gateway又是什么？数据不是已经存在对象存储里了吗，为什么不去直接查S3，还要多跑一个组件？"

**大师**："因为对象存储不是为时序实时查询设计的。直接查S3每个小block，给你一个跨3个月的时间范围查询，光打开文件就要几秒甚至几十秒，延迟不可接受。Store Gateway做的事情是：它启动时把对象存储中所有block的索引（index）下载到本地磁盘或内存里并建缓存，查询时先在本地索引中定位目标数据在哪个block的哪个位置，然后只读取需要的chunk。换句话说，Store Gateway是一个对象存储到Querier之间的'查询加速层'。另外，Store Gateway会感知Compactor生成的降采样数据——当查询时间范围很大时，它自动使用5m或1h粒度的粗精度数据，而不是逐条遍历原始raw数据。"

**小胖**："我对比过Thanos和Prometheus Federation方案。Federation也能做全局查询，好像还更简单？"

**大师**："Federation确实更简单，但它的本质是'下级Prometheus把聚合后的结果推给上级'——上级Prometheus能查到的只是下级预先算好的聚合数据，比如你只存了`avg(cpu_usage)`，就永远查不到单个实例的`cpu_usage`原始值。Thanos走的是另一条路：原始数据全量上传到对象存储，Querier查询时是直接查原始数据再做聚合。所以Federation适用于'我只需要看汇总趋势，不关心细节'的场景；Thanos适用于'我需要既能看汇总趋势，也能下钻到某台机器的原始数据'的场景。另外Federation是拉模型，上级Prometheus要存储所有下级的聚合数据，本质还是在本地磁盘存数据；而Thanos把数据存到了对象存储，理论上容量无限。"

## 三、项目实战

### 环境准备

本次实战使用Docker Compose搭建完整Thanos测试环境，包含以下组件：

- **MinIO**：模拟S3对象存储
- **Prometheus**：单实例数据源（生产环境每个DC一个）
- **Thanos Sidecar**：上传block到MinIO
- **Thanos Querier**：全局查询入口
- **Thanos Store Gateway**：读取历史数据
- **Thanos Compactor**：压缩与降采样

### 步骤1：启动MinIO对象存储

```yaml
# docker-compose.yml（节选）
services:
  minio:
    image: minio/minio:latest
    command: server /data --console-address ":9001"
    ports:
      - '9000:9000'
      - '9001:9001'
    environment:
      MINIO_ROOT_USER: thanos
      MINIO_ROOT_PASSWORD: thanos123
    volumes:
      - minio_data:/data
```

启动后通过MinIO客户端创建bucket：

```bash
docker exec minio mc alias set local http://localhost:9000 thanos thanos123
docker exec minio mc mb local/thanos-bucket
```

也可访问 `http://localhost:9001` 用Web UI手动创建bucket。

### 步骤2：启动Prometheus + Sidecar

Prometheus配置文件：

```yaml
# prometheus.yml
global:
  scrape_interval: 15s
  external_labels:
    replica: "dc1-a"

scrape_configs:
  - job_name: 'prometheus'
    static_configs:
      - targets: ['localhost:9090']
```

注意`external_labels`中的`replica`标签——它就是Querier去重时区分不同Prometheus副本的关键依据。

Docker Compose中的Prometheus和Sidecar定义：

```yaml
services:
  prometheus:
    image: prom/prometheus:latest
    volumes:
      - ./prometheus.yml:/etc/prometheus/prometheus.yml
      - prometheus_data:/prometheus
    command:
      - '--config.file=/etc/prometheus/prometheus.yml'
      - '--storage.tsdb.path=/prometheus'
      - '--storage.tsdb.retention.time=2d'
      - '--storage.tsdb.min-block-duration=15m'
      - '--storage.tsdb.max-block-duration=30m'

  thanos-sidecar:
    image: thanosio/thanos:latest
    command:
      - 'sidecar'
      - '--tsdb.path=/prometheus'
      - '--prometheus.url=http://prometheus:9090'
      - '--objstore.config-file=/etc/thanos/objstore.yml'
    volumes:
      - prometheus_data:/prometheus
      - ./objstore.yml:/etc/thanos/objstore.yml
    depends_on:
      - prometheus
```

两个关键参数说明：`--storage.tsdb.min-block-duration=15m` 让Prometheus每15分钟生成一个block（生产环境默认2小时即可，这里缩短是为了加快演示效果）；`--storage.tsdb.retention.time=2d` 将Prometheus本地保留时间缩短到2天，因为我们依赖Thanos侧的对象存储做长期保留。

对象存储配置文件：

```yaml
# objstore.yml
type: s3
config:
  bucket: thanos-bucket
  endpoint: minio:9000
  access_key: thanos
  secret_key: thanos123
  insecure: true
  region: us-east-1
```

**要点**：Sidecar和Prometheus必须共享`prometheus_data`这个volume，否则Sidecar无法访问Prometheus生成的TSDB block文件。

### 步骤3：启动Querier（全局查询入口）

```yaml
services:
  thanos-querier:
    image: thanosio/thanos:latest
    ports:
      - '10902:10902'
      - '10901:10901'
    command:
      - 'query'
      - '--http-address=0.0.0.0:10902'
      - '--grpc-address=0.0.0.0:10901'
      - '--query.replica-label=replica'
      - '--endpoint=thanos-sidecar:10901'
      - '--endpoint=thanos-store:10901'
```

`--endpoint` 指定Querier的数据源，可以是Sidecar（提供实时数据）、Store Gateway（提供历史数据），生产环境中多数据中心的Sidecar都需要在这里列出。`--query.replica-label=replica` 告诉Querier：当发现来自不同数据源但label集合相同的指标时，用`replica`标签来区分并做去重。

### 步骤4：启动Store Gateway（读历史数据）

```yaml
services:
  thanos-store:
    image: thanosio/thanos:latest
    ports:
      - '10912:10912'
    command:
      - 'store'
      - '--data-dir=/thanos-store-cache'
      - '--objstore.config-file=/etc/thanos/objstore.yml'
      - '--index-cache-size=500MB'
      - '--chunk-pool-size=2GB'
    volumes:
      - thanos_store_cache:/thanos-store-cache
      - ./objstore.yml:/etc/thanos/objstore.yml
```

Store Gateway在启动时会扫描对象存储中所有block，将索引下载到`--data-dir`指定的本地目录缓存。`--index-cache-size`控制索引缓存大小，越大查询越快但内存占用越高；`--chunk-pool-size`控制chunk读取缓存的池大小。对于生产环境中TB级别的对象存储数据，这两个参数的合理设置至关重要——通常建议index缓存设置为总block索引大小的50%-80%。

### 步骤5：启动Compactor（压缩+降采样）

```yaml
services:
  thanos-compact:
    image: thanosio/thanos:latest
    command:
      - 'compact'
      - '--data-dir=/thanos-compact'
      - '--objstore.config-file=/etc/thanos/objstore.yml'
      - '--retention.resolution-raw=30d'
      - '--retention.resolution-5m=180d'
      - '--retention.resolution-1h=3y'
      - '--compact.concurrency=4'
      - '--wait'
    volumes:
      - thanos_compact:/thanos-compact
      - ./objstore.yml:/etc/thanos/objstore.yml
```

降采样保留策略解释：原始精度（raw）数据保留30天；5分钟粒度数据保留180天（约6个月）；1小时粒度数据保留3年。Compactor定期扫描对象存储，将同一时间范围内来自不同Prometheus上传的多个小block合并成一个大block（减少查询时需要打开的文件数量），同时为合并后的block生成5m和1h的降采样数据。当用户通过Querier查询跨30天以上的时间范围时，Store Gateway会自动路由到5m粒度的降采样数据，大幅提升查询速度。

### 可能遇到的坑

1. **Sidecar不能访问Prometheus TSDB目录**：Docker中必须确保Sidecar和Prometheus挂载的是同一个volume（或同一个host目录），否则Sidecar找不到block文件。排查方法：`docker exec thanos-sidecar ls /prometheus` 看看有没有类似`01HXXXXXX`的block目录。

2. **Store Gateway首次启动极慢**：它需要扫描并下载对象存储中所有历史block的索引到本地缓存。如果已经积累了数月的数据，启动可能长达数小时。生产环境建议给`--index-cache-size`分配足够内存，并使用`--sync-block-duration`控制同步间隔。

3. **Compactor不能多实例并发**：除非配置了`--dedup.replica-label`和sharding参数做分片，否则多Compactor同时压缩同一时间范围的block会引发冲突。生产环境建议单实例运行，或用Thanos Ruler做分片调度。

4. **Querier去重仅限查询层**：去重只是查询时自动挑选最优副本返回，对象存储中HA双副本的数据会全部保留，相当于双倍的存储成本。如果对存储成本敏感，可以考虑单Prometheus采集或利用Compactor的`--dedup.replica-label`在压缩时物理去重。

### 测试验证

完成部署后执行以下验证步骤：

1. 访问 `http://localhost:10902/graph` 进入Thanos Querier的Web UI（界面和Prometheus原生UI几乎一致）。
2. 查询 `up`，确认能看到Prometheus自身的target状态。
3. 等待约30分钟后（取决于`min-block-duration`设置），检查MinIO bucket中是否已有block文件：
   ```bash
   docker exec minio mc ls local/thanos-bucket/
   ```
4. 数据积累后，查询 `up offset 3d` 验证能否读到历史数据（若不满3天可调整为更短的offset）。
5. 访问 `http://localhost:10902/stores` 查看当前Querier连接的Store端点状态，确认Sidecar和Store Gateway都处于healthy状态。

## 四、项目总结

### 架构全景图

整个Thanos架构可以概括为一条数据流线：**Prometheus + Sidecar → Object Storage ← Store Gateway → Querier ← Grafana/用户查询**。数据从Prometheus产生后，Sidecar将其搬运到对象存储，Compactor在后台整理压缩并生成降采样数据，Store Gateway充当对象存储到查询层之间的缓存加速器，Querier汇总所有数据源（Sidecar的实时数据 + Store Gateway的历史数据）并进行去重，最终对外暴露统一的Prometheus兼容查询接口。

### 核心组件速查表

| 组件 | 角色 | 默认端口 |
|------|------|----------|
| Sidecar | 监听Prometheus TSDB目录，上传block到对象存储，同时提供Store API | gRPC: 10901 |
| Querier | 全局查询入口，聚合多个Store端点的数据并去重 | HTTP: 10902, gRPC: 10901 |
| Store Gateway | 从对象存储读取历史block并提供Store API | gRPC: 10901 |
| Compactor | 合并block、生成降采样数据、清理过期数据 | 无对外端口 |
| Ruler | 全局告警/录制规则评估，结果写入对象存储 | HTTP: 10902 |
| Receiver | 接收Remote Write写入的数据并上传到对象存储 | gRPC: 10901 |

### Thanos vs Federation vs VictoriaMetrics 对比

| 维度 | Thanos | Federation | VictoriaMetrics |
|------|--------|------------|-----------------|
| 存储方式 | 对象存储（S3/MinIO/GCS） | 本地磁盘 | 本地磁盘 |
| 数据查询粒度 | 原始数据+降采样 | 只含聚合结果 | 原始数据+降采样 |
| 全局查询 | 天然支持（Querier） | 需手动搭建层次架构 | 需搭建集群版 |
| 侵入性 | Sidecar模式零侵入 | 需加federate scrape | 需替换Prometheus |
| 运维复杂度 | 中等（5-6个组件） | 低 | 低-中 |

### 适用与不适用场景

**适用场景**：全球多数据中心需要长期存储+全局查询；需要保留原始数据细节做任意维度下钻（而非只看聚合）；已有Prometheus部署不想推倒重来；HA采集需要自动去重。

**不适用场景**：监控规模不大（例如少于100万series），引入Thanos的额外组件得不偿失；对查询延迟要求极为苛刻（Thanos比直查Prometheus多一次网络跳转，P99延迟可能高出100ms+）；完全不需要长期存储（15天本地数据就够了）；团队没有对象存储运维经验（S3权限、分桶策略、成本控制都是新课题）。

### 常见踩坑经验

1. **Sidecar找不到Prometheus的tsdb.path**：排查思路是确认Docker volume挂载是否一致，尤其注意不要用相对路径。可以在Sidecar容器内执行`ls /prometheus`看是否能看到block目录。另外，如果Prometheus刚启动还没有产生block，Sidecar会处于等待状态，这是正常的。

2. **对象存储bucket权限配置错误**：常见症状是Sidecar日志中报403 Forbidden或access denied。MinIO中检查bucket的访问策略，确认access_key和secret_key正确；AWS S3中检查IAM角色是否具有s3:PutObject和s3:GetObject权限。

3. **Compactor内存不足导致压缩失败**：Compactor在合并大block时需要将源block的全部数据加载到内存中排序。如果单个block达到数十GB，Compactor可能OOM退出。解决方案：调大Docker容器内存限制，或增加`--compact.concurrency`来分批次处理，或通过`--selector.relabel-config`将不同租户的数据分开压缩。

### 思考题

1. **Thanos Querier如何知道一个查询应该从Sidecar读还是从Store Gateway读？**  
   提示：Querier会向所有已注册的Store端点（通过`--endpoint`指定的Sidecar和Store Gateway）并行发送查询请求。每个Store端点会根据自己持有的数据时间范围响应：Sidecar持有最近2小时内的最新数据，Store Gateway持有对象存储中的历史数据。Querier收集所有响应后，根据时间序列的label和时间戳进行合并去重，返回最终结果。它并不"选择"从哪读——它从所有端点读，然后合并。

2. **如果有3组HA的Prometheus（每组2副本）采集同一target，Thanos如何处理6份数据的去重？**  
   提示：假设3组Prometheus分别设置`replica="A"`和`replica="B"`（第1组）、`replica="C"`和`replica="D"`（第2组）、`replica="E"`和`replica="F"`（第3组）。如果Querier配置了`--query.replica-label=replica`，它会将这6份数据视为6个不同副本，用Penalty Deduplication选出综合质量最高的一份返回。但这里存在一个容易混淆的点：如果3组Prometheus采集的是同一target，那它们之间的根本区别不应该是`replica`标签（replica仅用于同组HA内部去重），而应该通过不同的`cluster`或`datacenter` external label来区分来源。正确的做法是：每组Prometheus设置`cluster="dc1"`等标签做区分，组内用`replica`做HA去重。Querier查询时保留`cluster`维度，在`replica`维度去重，这样每个数据中心最终只贡献一份最优数据。
