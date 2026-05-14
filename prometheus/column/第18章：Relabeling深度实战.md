# 第18章：Relabeling深度实战

> **前置阅读**：第11章《Prometheus采集配置实战》已介绍 relabeling 基础概念（keep/drop/replace），本章在此基础上深入所有 action 类型、三种 relabeling 的差异与高级场景。

---

## 一、项目背景

随着公司全面推进微服务化改造，Prometheus 需要监控的服务从最初的 10 个暴增至 200+。每个服务的开发团队都希望在自己的指标上打上自定义标签——team、service、version，以便在 Grafana 中按团队和服务维度拆分看板。运维团队很快发现了一个严重问题：如果不加控制，这些标签会让 Prometheus 的 time series 数量直接爆炸。光是 version 标签就有 100+ 个不同值——每次发版都不删除旧版本的指标，多版本叠加后基数呈指数级增长。运维监控组的 Prometheus 实例开始出现 OOM、查询超时、compaction 积压等一连串故障。

运维老王接手了这个烂摊子。摆在他面前的是一道多解题：第一，需要一种机制在 Prometheus **采集时**就过滤掉不需要的标签、裁剪高基数的标签值，而不是等到数据写入 TSDB 后再处理（那时已经晚了）。第二，200+ 个 target 已经超出了单实例 Prometheus 的处理能力，需要根据标签把采集任务**分片**分发到不同的 Prometheus 实例上，每个实例只负责一部分 target。第三，Kubernetes 集群里的 Pod annotation 和 label 非常多，需要一种自动映射机制，只保留真正有用的元数据标签，避免垃圾标签污染指标。

这三个需求的共同答案就是 **Relabeling**——Prometheus 最强大的配置特性之一，也是最容易踩坑的重灾区。`relabel_configs`（采集前修改 target 元数据）和 `metric_relabel_configs`（采集后修改指标标签）的作用时机截然不同，新手经常搞混。`keep`/`drop` 的过滤条件一旦写错，target 可能**静默消失**——Prometheus 不会报错，只会默默不采集，排查时才发现服务发现页面里目标不见了。`hashmod` 分片采集的配置逻辑如果不理解，可能导致数据重复采集或遗漏，直接影响告警的准确性。更隐蔽的还有 `write_relabel_configs`，它在 remote write 之前过滤指标，配合多租户场景实现指标路由，但很多人压根不知道它的存在。

本章将从三种 relabeling 的差异出发，逐一拆解全部 7 种 action 类型的语法和原理，并通过实验验证和分片实战，帮助读者彻底掌握这一核心配置能力。

---

## 二、剧本式交锋对话

**小胖**：大师，我昨天照着第 11 章的配置做 relabeling，结果把 target 搞丢了！Prometheus 也不报错，我排查了俩小时才发现是正则写反了。这 relabel_configs 和 metric_relabel_configs 到底有什么区别？为什么名字这么像，作用却不一样？

**大师**：这个困惑太常见了。你可以把一次 scrape 流程想象成两个阶段：**采集前**和**采集后**。`relabel_configs` 在 scrape 之前执行，它的操作对象是 target 的元数据标签——就是那些以 `__` 开头的内部标签，比如 `__address__`、`__scheme__`、`__metrics_path__` 等等。你可以在这一步修改 target 的连接地址、添加自定义标签、或者决定这个 target 要不要采集——用的就是 keep/drop action。

**小白**：所以 `relabel_configs` 操作的是 target 层面，那 `metric_relabel_configs` 呢？为什么还需要第二个 relabeling？

**大师**：`metric_relabel_configs` 在 scrape 完成之后、数据写入存储之前执行。它的操作对象是真正采集到的**指标数据**——也就是你从 `/metrics` 接口拉回来的那些 time series。用途完全不同：比如说某个 exporter 暴露了 `http_requests_total` 带了一个 `user_id` 标签，每个用户一条 time series，上万个用户就是上万个 time series。你想去掉这个 `user_id` 标签来降低基数——这种事只能在 `metric_relabel_configs` 里做，因为 `relabel_configs` 根本看不到指标数据。

**小胖**：原来如此！那 action 除了 keep 和 drop，还有哪些类型？我看别人配置里有 replace、labelmap 什么的……

**大师**：总共 7 种，我来逐一说明。最常用的是 **replace**，它把 `source_labels` 的值用正则 `regex` 匹配，然后按照 `replacement` 指定的模板写到 `target_label` 上。**keep** 是只保留匹配正则的 target 或指标，**drop** 则相反——丢弃匹配的。接下来是专门操作标签名的三个：**labelmap** 用正则匹配标签名，把捕获组内容映射为新标签名，最经典的场景就是把 Kubernetes 的 pod annotation 自动映射成 Prometheus label。**labeldrop** 直接按**标签名**删除匹配正则的标签——注意它不是匹配标签值，这跟 drop 完全不一样。**labelkeep** 反过来，按标签名保留匹配的，不匹配的全删。

**小白**：等等，drop 是匹配标签值来决定整条 time series 的去留，而 labeldrop 是匹配标签名来删除标签本身？这很容易搞混啊！

**大师**：对，这是个高频踩坑点。举个例子：你要删除 `user_id` 这个标签本身，减少标签维度，应该用 `labeldrop`，匹配的 regex 是标签名 `user_id`。你要丢弃所有带 `user_id=admin` 的指标数据，应该用 `drop`，regex 匹配的是标签值 `admin`。

**小胖**：那最后一种 hashmod 是什么？

**大师**：**hashmod** 是分片采集的核心。它不对标签值做字符串匹配，而是对 `source_labels` 拼接后的值计算 MD5 哈希，然后对 `modulus` 取模，结果写入 `target_label`。接着你用 `keep` 把期望的模值留下来，就能实现一致性哈希分片——同一个 target 永远落在同一个分片上。这里有个重要细节：`__` 开头的标签都是内部标签，在最终存储时会被自动移除——除非你设置了 `honor_labels: true`。所以 hashmod 写入临时标签如 `__tmp_hash` 是安全的，不会污染指标。

**小白**：如果在 `relabel_configs` 里用了 labeldrop 会怎样？target 的元数据标签也能按名删除吗？

**大师**：好问题。`relabel_configs` 支持所有 action 类型，但 labeldrop/labelkeep 在采集前阶段主要用于清理 `__meta_*` 标签——这些服务发现产生的元数据非常多，不清理会占用内存。而 `metric_relabel_configs` 里的 labeldrop 才是我们通常用来给指标瘦身的。另外提醒一点：relabel 规则是**按配置顺序**依次执行的，后面规则可能覆盖前面的结果。如果你先 replace 了一个标签，又在后面 keep 引用了这个标签，那是完全合法的——这种流水线式的组合才是 relabeling 的精髓。

---

## 三、项目实战

### 环境准备

- 一个运行中的 Prometheus 实例（本文以 2.45 版本为例）
- 至少 2 个 Node Exporter target 在 `node1:9100` 和 `node2:9100`
- Prometheus Web UI 和 API 可访问

### 步骤1：relabel_configs vs metric_relabel_configs 实验对比

先在 `prometheus.yml` 中添加两组实验配置，直观感受两者的差异。

**实验一：relabel_configs 修改 target 层面**

```yaml
scrape_configs:
  - job_name: 'node-experiment-1'
    static_configs:
      - targets: ['node1:9100', 'node2:9100']
    relabel_configs:
      - source_labels: [__address__]
        regex: '([^:]+):.*'
        target_label: 'hostname'
        replacement: '$1'
```

重启 Prometheus 后，进入 **Status → Service Discovery** 页面，可以看到 `node-experiment-1` 的每个 target 多了一个 `hostname` 标签，值分别为 `node1` 和 `node2`。这就是 relabel_configs 的典型效果——在采集前给 target 打上自定义元数据标签。此时查询 `up{job="node-experiment-1"}`，也能看到 hostname label 出现在指标中。

**实验二：metric_relabel_configs 修改指标层面**

```yaml
scrape_configs:
  - job_name: 'node-experiment-2'
    static_configs:
      - targets: ['node1:9100']
    metric_relabel_configs:
      - source_labels: [__name__]
        regex: 'node_network_.*'
        action: drop
```

重启后，查询 `{job="node-experiment-2"}`，所有 `node_network_*` 开头的指标全部消失——它们在写入存储之前就被丢弃了。注意：你去 **Service Discovery** 页面查看，这个 target 仍然是 "UP" 状态，因为 relabel_configs 层面没有做任何过滤。**metric_relabel_configs 的效果只能从指标维度验证，在 Service Discovery 页面看不见。**

### 步骤2：高基数标签裁剪实战

**场景A：删除高基数标签**

某个业务 exporter 暴露了带 `user_id` 标签的指标 `api_requests_total`，每个用户一条 time series，百万用户就是百万条 time series，Prometheus 直接 OOM。

```yaml
scrape_configs:
  - job_name: 'business-api'
    static_configs:
      - targets: ['api-server:8080']
    metric_relabel_configs:
      - source_labels: [user_id]
        regex: '.+'
        action: labeldrop
```

这里的 `labeldrop` 匹配的是**标签名** `user_id`，效果是直接删除这个标签。所有 `api_requests_total` 退化为单条 time series（按其它标签聚合），基数从百万级瞬间降至个位数。在 Prometheus Web UI 中执行 `count({__name__="api_requests_total"})` 即可对比裁剪前后的基数变化。

**场景B：裁剪标签值精度**

某网络设备 exporter 的 `source_ip` 标签包含完整 IP（如 `192.168.33.45`），不同 IP 太多导致基数过高。业务只需要网段维度：

```yaml
metric_relabel_configs:
  - source_labels: [source_ip]
    regex: '(\d+\.\d+)\..*'
    target_label: 'subnet'
    replacement: '$1'
  - source_labels: [source_ip]
    regex: '.+'
    action: labeldrop
```

先通过 replace 提取前两段写入新的 `subnet` 标签（如 `192.168`），再用 labeldrop 删除原始 `source_ip`。这样既保留了网段信息，又消除了 IP 带来的高基数。

### 步骤3：hashmod 分片采集

当单实例 Prometheus 处理不了 200+ 个 target 时，需要部署 3 个实例，每个负责约 1/3 的采集。同样的配置部署到 3 个实例，仅 `keep` 的 regex 不同：

```yaml
scrape_configs:
  - job_name: 'node-sharded'
    static_configs:
      - targets:
          - 'node1:9100'
          - 'node2:9100'
          - 'node3:9100'
          - 'node4:9100'
          - 'node5:9100'
          - 'node6:9100'
          # ... 共200个targets
    relabel_configs:
      - source_labels: [__address__]
        modulus: 3
        target_label: __tmp_hash
        action: hashmod
      - source_labels: [__tmp_hash]
        regex: '0'
        action: keep
```

三个实例配置如上，唯一的区别是**最后一个 keep rule 的 regex**：实例1 用 `'0'`、实例2 用 `'1'`、实例3 用 `'2'`。hashmod 计算 `MD5(__address__) % 3`，比如 `node1:9100` 的 hash 结果永远是 0，所以只会被实例1采集；`node2:9100` 的结果是 1，只能被实例2采集。以此类推。

**原理**：hashmod 使用 MD5 哈希保证**一致性**——同一个 `__address__` 的 hash 结果固定不变，不会在不同实例间漂移，确保每个 target 有且仅有一个实例在采集，既不重复也不遗漏。`__tmp_hash` 作为临时标签（双下划线开头），最终不会被写入存储。

**验证方法**：在每个实例的 Web UI 中执行 `count({job="node-sharded"})`，三个实例的指标数应大致为 1:1:1。也可通过 `up{job="node-sharded"}` 查看每个实例分配到了哪些 target。

### 步骤4：labelmap 自动映射 K8s 标签

在 Kubernetes 服务发现场景中，Pod 的 annotation 会以 `__meta_kubernetes_pod_annotation_<name>` 的形式出现。如果想把它们直接映射成 Prometheus label，一条 rule 搞定：

```yaml
relabel_configs:
  - action: labelmap
    regex: '__meta_kubernetes_pod_annotation_(.+)'
```

这条规则将匹配所有以 `__meta_kubernetes_pod_annotation_` 开头的标签名，把 `(.+)` 捕获的内容作为新的标签名，值不变。例如：`__meta_kubernetes_pod_annotation_team=backend` 自动变为 `team=backend`。如果只想映射特定的 annotation，可以收紧 regex：

```yaml
relabel_configs:
  - action: labelmap
    regex: '__meta_kubernetes_pod_annotation_(team|version|tier)'
```

### 步骤5：多租户场景——write_relabel_configs 按 namespace 路由

当多个租户共用采集层、但需写入不同的后端存储时，`write_relabel_configs` 登场。它在 remote write 请求发出之前执行，按租户标签过滤和路由：

```yaml
remote_write:
  - url: 'http://tenant-a-writer:19291/write'
    write_relabel_configs:
      - source_labels: [namespace]
        regex: 'tenant-a'
        action: keep
  - url: 'http://tenant-b-writer:19291/write'
    write_relabel_configs:
      - source_labels: [namespace]
        regex: 'tenant-b'
        action: keep
```

这样，`namespace=tenant-a` 的指标只写入租户 A 的 remote write 端点，租户 B 的指标只写入租户 B，实现逻辑隔离。

### 可能遇到的坑

1. **relabel 规则执行顺序**：规则按配置顺序依次执行，后一条可能覆盖前一条的修改。如果先 `labeldrop` 删了某个标签，后面再用它的值做 `keep` 就会匹配失败。
2. **hashmod 扩容问题**：当实例数从 3 扩到 4（modulus 从 3 变 4），几乎所有 target 的 hash 取模结果都变了——等同于全量重分配。设计分片时应预留余量，或使用一致性哈希环替代简单取模。
3. **labeldrop 和 drop 的语义混淆**：`labeldrop` 删除的是**标签名**匹配正则的标签；`drop` 是匹配**标签值**来决定整条 time series 的去留。这是第 1 大踩坑点。
4. **性能开销**：每个 sample 在 scrape 之后都要依次遍历 `metric_relabel_configs` 的所有规则。规则过多（如 50+ 条）会显著影响 scrape 性能，生产环境建议控制在 20 条以内。
5. **labelmap 过度映射**：如果不加限制地 `regex: '__meta_kubernetes_pod_label_(.+)'`，K8s 上几百个 label 都会被映射进来，造成标签污染。务必用 regex 精确匹配需要的标签名。

### 测试验证清单

| 验证项 | 方法 |
|--------|------|
| relabel_configs 标签变化 | Prometheus Web UI → Status → Service Discovery，查看 "Discovered Labels" vs "Target Labels" |
| metric_relabel_configs 过滤效果 | 在 Graph 页面执行 `{job="xxx"}` 检查指标数，对比开启/关闭前后的差异 |
| hashmod 分片均衡性 | 每个实例执行 `count({job="node-sharded"})`，三实例指标数应接近 1:1:1 |
| labeldrop 基数下降 | 使用 `count({__name__="your_metric"})` 或查看 TSDB Head Series 数量 |
| write_relabel_configs 路由 | 检查各 remote write 后端接收到的指标是否仅包含对应租户的数据 |
| debug 日志确认生效 | 设置 `--log.level=debug`，搜索 "relabel" 关键字观察规则匹配过程 |

---

## 四、项目总结

### 三种 Relabeling 对比

| 类型 | 执行时机 | 操作对象 | 典型场景 |
|------|---------|---------|---------|
| `relabel_configs` | scrape 之前 | target 元数据标签（`__address__`、`__meta_*` 等） | 修改 target 连接、添加 hostname、keep/drop target、hashmod 分片 |
| `metric_relabel_configs` | scrape 之后、写入 TSDB 之前 | 采集到的指标标签 | 过滤高基数标签、删除敏感标签、聚合前瘦身、指标按名 drop |
| `write_relabel_configs` | remote write 发送之前 | 待写入远程存储的指标标签 | 多租户路由、按 namespace 分发、远程存储前的最后过滤 |

**关键区别**：`relabel_configs` 的效果可在 Service Discovery 页面直观看到；`metric_relabel_configs` 只能从指标查询结果验证；`write_relabel_configs` 只影响 remote write，不影响本地 TSDB。

### Actions 全量速查表

| Action | 作用对象 | 语法要点 | 示例用途 |
|--------|---------|---------|---------|
| **replace** | 标签值 | `source_labels` + `regex` + `target_label` + `replacement` | 从 `__address__` 提取 hostname |
| **keep** | target/指标 | `source_labels` + `regex`（匹配则保留） | 只采集特定 job 或 namespace |
| **drop** | target/指标 | `source_labels` + `regex`（匹配则丢弃） | 剔除不需要的 target 或指标 |
| **labelmap** | 标签名 | `regex` 捕获标签名，自动映射 | K8s annotation → Prometheus label |
| **labeldrop** | 标签名 | `regex` 匹配标签名，匹配的删除 | 删除 `user_id` 等高基数标签 |
| **labelkeep** | 标签名 | `regex` 匹配标签名，不匹配的删除 | 只保留 `__name__` 和 `job` 标签 |
| **hashmod** | 标签值 | `source_labels` + `modulus` + `target_label` | 一致性哈希分片采集 |

### 适用场景归纳

- **多租户隔离**：`write_relabel_configs` 按 namespace 路由到不同后端
- **分片采集**：`hashmod` + `keep` 实现多实例均摊 200+ target
- **指标瘦身**：`metric_relabel_configs` + `labeldrop`/`drop` 裁剪高基数标签
- **标签规范化**：`labelmap` 自动映射、`replace` 重写标签值格式
- **K8s 元数据精简**：`labelkeep` 只保留必要的 pod annotation

### 注意事项

- relabel 规则总数不宜过多（推荐 <20 条），每条规则在 scrape 时都会遍历所有 sample
- hashmod 扩容（改变 modulus）会导致全量重分配，设计阶段应考虑未来扩展
- 生产环境务必在**测试环境**先验证所有 relabel 规则——keep/drop 写错一个正则就可能静默丢失数据
- `replacement` 中的 `$1` 引用必须对应 `regex` 中的捕获组，否则空值写入标签，排查困难

### 常见踩坑经验

**案例一**：keep/drop 正则在 `metric_relabel_configs` 中写反。管理员想丢弃 `node_network_*` 指标，却配成了 `action: keep, regex: 'node_network_.*'`——结果只保留了网络指标，其它全部丢弃。正确做法是 `action: drop`。

**案例二**：replacement 引用不存在的捕获组。配置了 `regex: '(\w+):\d+'` 却用 `$2` 引用——正则只有一个捕获组，`$2` 永远是空字符串，导致 target_label 被设为空值。

**案例三**：labeldrop 误删系统标签。用了 `regex: '.*'` 配合 labeldrop，把 `__name__` 也删了——所有指标失去 metric name，查询全部失败。labeldrop 的 regex 应精确匹配业务标签名，永远不要用 `'.*'`。

### 思考题

1. **如何用 relabel_configs 实现"只采集最近 5 分钟内有过新数据产生的 target"？这个需求能直接用 relabeling 做到吗？如果不能，应该用什么方案？**

2. **write_relabel_configs 和 metric_relabel_configs 都能过滤指标，什么时候该用前者、什么时候该用后者？如果一个指标既要写入本地 TSDB 又要 remote write 到两个不同租户的后端，该怎么配置？**

---

> **下章预告**：第19章将深入 Prometheus 的 Recording Rules 与 Alerting Rules，探究如何用 PromQL 预计算聚合指标来加速大范围查询，以及告警规则的编写最佳实践。
