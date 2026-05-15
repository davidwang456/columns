# 第27章：Grafana + Pyroscope持续性能分析

## 1. 项目背景

"Go服务的内存从200MB慢慢涨到2GB，然后OOM挂掉。Prometheus显示内存使用率曲线一直在涨但不知道是哪个函数在吃内存。传统pprof需要手动触发采样——等我连上去的时候Pod已经重启了，现场没了。"

开发者小赵正在追查一个经典的内存泄漏问题。Prometheus图表能告诉他"内存在涨"，但无法告诉"为什么涨"——这需要代码级的性能剖析（Profiling）。传统Profiling工具（pprof、JFR、py-spy）都是手动触发、单次采样的模式，无法在内存泄漏发生后回溯历史。

持续性能分析（Continuous Profiling）是Grafana生态的第四支柱——Pyroscope应运而生。它持续采集应用的CPU/Memory/Alloc/Goroutine等Profile数据，以火焰图形式展示，支持历史回溯和多维度对比。这让"上周二那个版本发布后CPU涨了20%，具体是哪个函数？"这类问题有了确切答案。

本章将带你集成Pyroscope SDK到应用，在Grafana上构建"指标→Profile→代码"的根因分析链路。

## 2. 项目设计

**小胖**（盯着Prometheus的CPU曲线）：大师，应用CPU突然从20%涨到80%，Prometheus只能告诉我"涨了"，但我需要知道是哪个函数在吃CPU。pprof采样结果是静态的，能不能持续追踪？

**大师**：这就是Continuous Profiling的用武之地。Pyroscope的原理是：持续采集应用的性能数据（CPU Profile、Memory Allocation、Goroutine等），每隔一段时间（如15秒）生成一份Profile并上传到Pyroscope Server。所有历史Profile都可查询对比。

与Prometheus的差异：
- Prometheus 回答"CPU涨了多少"（数值）
- Pyroscope 回答"CPU被谁吃掉了"（函数级）

**小白**：Pyroscope具体怎么采集数据？对应用性能有多少影响？

**大师**：Pyroscope支持多种语言的SDK——Go、Java、Python、Node.js、.NET、Ruby等。以Go为例，Pyroscope SDK包装了标准的`runtime/pprof`，每隔固定时间采集一次。

性能开销极低：
- CPU Profiling: < 1% CPU开销（默认100Hz采样）
- Memory Profiling: < 0.5% 开销
- 网络传输: 压缩后的Profile通常< 100KB/次

**小胖**：那火焰图怎么看？我第一次看到时完全懵了。

**大师**：火焰图阅读口诀——"宽度代表占比，高度代表调用深度，纵向是调用链"。比如：

```
        main (100%)
       /    |    \
  handler  db    cache
  (40%)  (30%) (30%)
```

最宽的函数就是CPU消耗最大的函数。点击某个框可以放大，看它内部的子函数分布。

更实用的技巧是"对比视图"——选两个时间点，Pyroscope自动做Diff：红色是新增的CPU消耗，绿色是减少的。

**小白**：那和Grafana怎么联动？

**大师**：Pyroscope原生集成在Grafana中（10.4+）。在Grafana面板上：
1. Dashboard上看到CPU曲线异常上升→点击曲线上的异常点→"View profile"→跳转到Pyroscope火焰图
2. 在火焰图中的Span节点→点击"View in Traces"→跳转到Tempo查看对应的Trace

这就是Grafana四支柱的联动闭环。

**技术映射**：Pyroscope = 持续心电图（不停采集，随时回看历史），火焰图 = 消费账单（哪个函数"花钱"最多一目了然），Diff视图 = 前后对比照（版本A和版本B的CPU消耗差异）。

## 3. 项目实战

**环境准备**

在Docker Compose中添加Pyroscope：

```yaml
  pyroscope:
    image: grafana/pyroscope:1.6.0
    container_name: pyroscope
    ports:
      - "4040:4040"
    volumes:
      - pyroscope_data:/var/lib/pyroscope

volumes:
  pyroscope_data:
```

**步骤一：Go应用集成Pyroscope SDK**

```go
package main

import (
    "github.com/grafana/pyroscope-go"
    "net/http"
    "time"
)

func main() {
    _, err := pyroscope.Start(pyroscope.Config{
        ApplicationName: "order-service",
        ServerAddress:   "http://pyroscope:4040",
        
        // Profile类型
        ProfileTypes: []pyroscope.ProfileType{
            pyroscope.ProfileCPU,           // CPU
            pyroscope.ProfileAllocObjects,   // 内存分配对象
            pyroscope.ProfileAllocSpace,     // 内存分配空间
            pyroscope.ProfileInuseObjects,   // 使用中对象
            pyroscope.ProfileInuseSpace,     // 使用中空间
            pyroscope.ProfileGoroutines,     // Goroutine数量
            pyroscope.ProfileMutexCount,     // 互斥锁竞争
            pyroscope.ProfileBlockCount,     // 阻塞数量
        },
        
        // 标签（用于多维度过滤）
        Tags: map[string]string{
            "env":     "production",
            "version": "v3.4.0",
        },
        
        Logger: pyroscope.StandardLogger,
    })
    if err != nil {
        panic(err)
    }
    defer pyroscope.Stop()
    
    http.HandleFunc("/api/heavy", heavyHandler)
    http.ListenAndServe(":8080", nil)
}

func heavyHandler(w http.ResponseWriter, r *http.Request) {
    // 模拟CPU密集操作
    result := 0
    for i := 0; i < 10000000; i++ {
        result += i % 7
    }
    w.Write([]byte("done"))
}
```

**步骤二：Java应用集成**

Maven/Gradle添加依赖后：
```java
import io.pyroscope.javaagent.PyroscopeAgent;
import io.pyroscope.http.Format;

public class App {
    public static void main(String[] args) {
        PyroscopeAgent.start(
            new PyroscopeAgent.Options.Builder()
                .setApplicationName("payment-service")
                .setServerAddress("http://pyroscope:4040")
                .setFormat(Format.JFR)  // Java Flight Recorder
                .setLabels(Map.of("env", "production"))
                .build()
        );
        
        // 业务代码
        SpringApplication.run(App.class, args);
    }
}
```

**步骤三：Python应用集成**
```python
import pyroscope

pyroscope.configure(
    application_name="data-pipeline",
    server_address="http://pyroscope:4040",
    tags={"env": "production"},
    sample_rate=100,  # 每秒采样100次（CPU）
)
```

**步骤四：在Grafana中配置Pyroscope**

Grafana → Data Sources → Add → Pyroscope：

| 参数 | 值 |
|------|-----|
| URL | `http://pyroscope:4040` |
| Min time | 15s |

在Grafana Explore中选择Pyroscope → 选择Profile类型（CPU/Memory等）→ 选择应用和标签 → 查看火焰图。

火焰图操作：
- 点击函数块→放大
- 搜索框输入函数名→高亮
- 时间轴拖动→看历史变化
- Diff模式→选两个时间点对比

**步骤五：实战——追查内存泄漏**

场景：应用内存持续增长。

1. 在Pyroscope中选择"Memory Alloc Space" Profile
2. 时间范围选择"Last 7 days"
3. 在火焰图中寻找占比陡增的函数
4. 发现是`cache.NewLRUCache`分配的内存从100MB涨到800MB
5. 对比"v3.3.0"和"v3.4.0"两个版本：
   ```
   Diff视图显示：
   +45% cache.NewLRUCache      ← 新版本引入的泄漏
   +10% image.Decode           ← 图片处理逻辑变更
   -5%  database.Query         ← 优化掉了
   ```
6. 定位到代码`cache.go:45` → 修复 → 验证

**步骤六：Prometheus指标 + Pyroscope联动**

在Grafana Dashboard中，CPU使用率曲线旁添加Pyroscope链接。点击异常时间点的"View Profile"直接跳转到对应时间的火焰图。

**常见坑点**
1. **Pyroscope Server内存不够**：大量应用（100+）持续上传Profile数据，Pyroscope Server的内存可能不足（建议8GB+）。
2. **高吞吐应用不要全开Profile类型**：CPU+Memory+Alloc+Mutex+Bloom全开，对高频应用影响3-5% CPU。只开CPU和Memory即可。
3. **火焰图时间滞后**：Profile数据有10-15秒的延迟（采集+上传+处理），不是实时的。

**步骤七：实战案例——追查Go服务间歇性CPU飙升**

某订单服务每隔15分钟CPU从20%飙升到95%，持续30秒后恢复正常。Prometheus能看到波动但定位不到原因。

**1. Pyroscope持续采集分析**：

打开Pyroscope → 选择order-service → CPU Profile → 时间范围选"Last 1 hour"。

在火焰图中发现一个规律性出现的函数`gcBgMarkWorker`（Go GC标记阶段）占CPU的60%。进一步查看Alloc Profile，发现每15分钟有大量内存分配来自`report.GenerateCSV()`函数。

**2. 对比正常时段和异常时段**：

在Pyroscope中选择两个时间段做Diff：
```
+58% report.GenerateCSV        ← 异常时段新增
+15% gcBgMarkWorker            ← GC压力增大
-35% handler.ProcessOrder      ← 正常业务被挤占
```

**3. 定位到代码**：
```go
// 问题代码：定时任务每次生成CSV时加载全量数据到内存
func GenerateCSV() {
    orders := loadAllOrders()  // 120万条订单全load到内存
    csv := convertToCSV(orders) // 大量字符串拼接
    uploadToS3(csv)
}
```

**4. 修复后验证**：
```go
// 优化：流式处理，不用全量加载
func GenerateCSV() {
    rows := queryOrdersStream()  // 数据库流式游标
    csvWriter := csv.NewWriter(s3Writer)
    for rows.Next() {
        csvWriter.Write(rows.Scan())
    }
}
```

修复发布后，Pyroscope火焰图显示`report.GenerateCSV`的CPU占比从58%降到3%，GC暂停时间恢复正常。

**5. 建立持续性能基线的自动化**：
```bash
#!/bin/bash
# 每日对比今天和昨天的CPU Profile
pyroscope difftool \
  --left "now-24h" \
  --right "now" \
  --app "order-service" \
  --type cpu \
  --threshold 5 > diff-report.txt

# 如果某函数CPU增长超过10%，发送告警
HIGH_FUNCS=$(grep "+[1-9][0-9]%" diff-report.txt)
if [ -n "$HIGH_FUNCS" ]; then
    echo "Performance regression detected: $HIGH_FUNCS"
fi
```

## 4. 项目总结

**持续性能分析定位**

作为可观测性第四支柱，Pyroscope补充了"代码级"的盲区：
- Metrics：知道"涨了"
- Logs：知道"报错了"
- Traces：知道"哪个调用链慢了"
- Profiles：知道"哪行代码导致的"

**适用场景**
1. 内存泄漏追查：持续Memory Profile发现泄漏函数
2. 版本性能回归：新旧版本CPU Profile Diff对比
3. GC调优：Alloc Profile看对象分配热点
4. 协程泄漏：Goroutine Profile发现泄漏的Goroutine

**思考题**
1. Pyroscope采集的CPU Profile是采样数据（100Hz意味着每秒取100个栈快照）。如果某个函数执行只需要50微秒，采样能抓到吗？抓不到会有什么影响？
2. 如何设计一个自动化流程：当Prometheus检测到CPU使用率超过历史基线的3个标准差时，自动在Pyroscope中定位CPU消耗最高的函数并通知开发？
