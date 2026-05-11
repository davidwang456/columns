# 第33章：RPC通信框架——Master与Worker的对话

> **关键词**：Netty、gRPC、Protobuf、TaskDispatchCommand、双工流、序列化、版本兼容、连接池、Heartbeat、RpcClient、extract模块

---

## 1. 项目背景

大麦的DolphinScheduler集群出现了一起令人头疼的"幽灵执行"事故——同一个Spark ETL任务在调度日志中显示执行了两次，两次都标记为失败，但翻开YARN的Application History却发现两次运行都输出了完整数据且退出码为0。运维团队被来回传唤了三天，翻遍了Master和Worker的日志，问题最终定位到一个极其隐蔽的根因：上周运维做了一次"滚动升级"——先把Master从3.1.0升到3.1.1，打算第二天再升Worker。就在这中间的一天里，Master节点重新编译了`dolphinscheduler-extract-master`模块（Protobuf定义包含新字段），而Worker节点仍在运行旧版本`dolphinscheduler-extract-worker`（旧proto文件）。发送侧Master用新schema序列化`TaskExecuteResultCommand`，其中新增的`end_time = 9`字段在接收侧Worker的旧schema里恰好是另一个字段的编号——反序列化时字段值对调，`status`字段的值被覆盖为0，Master将"未识别状态"默认解释为失败，于是触发重试机制。同样的数据错配在第二次往返中再次发生，同一个Spark任务就这样在生产环境被执行了两遍。

这次事故暴露了一个深刻的认知断层：团队用了大半年DolphinScheduler，却从未认真审视过Master和Worker之间到底是怎么"说话"的。表面上看，RPC就是"发一条消息、收一个回复"，但往下挖一层，问题扑面而来：消息用什么格式编码？发送方和接收方的消息定义如何保持一致？一条TCP连接能承载多少个并发调用？连接断了怎么发现？消息发送超时后是丢弃还是重试？RPC层的协议选型（gRPC+Protobuf+Netty）、消息序列化机制、连接池管理策略、心跳保活参数、超时与重试策略——这些通信基础设施，一旦版本错配或配置失当，故障是静默且灾难性的。DS的`dolphinscheduler-extract`模块系列正是为了解决消息契约一致性而设计的——每个服务对都有自己的extract子模块，所有通信双方共享同一套`.proto`文件，从根本上杜绝了"对端不知道消息长什么样"的问题。只有深入理解DS的RPC通信栈，才能回答"为什么发消息不会丢"以及"什么情况下会丢"。

---

## 2. 项目设计——剧本式交锋对话

白板前的三人刚从上一章Master源码的讨论中缓过神来，话题自然过渡到了"Master怎么把任务交给Worker"。

**小胖靠在椅子上，嘴里嚼着口香糖：**"RPC不就是'发消息'吗？HTTP POST一个JSON过去，`{taskId:123, type:'SPARK', params:'{...}'}`，Master收到后parse一下——跟普通的REST API没什么区别吧？三行代码搞定的事，为什么要扯到Netty、gRPC、Protobuf三层套娃？我写Spring Boot微服务都是`@RestController`一把梭。"

**小白侧过头，手指敲着桌面：**"那我问你四个问题——第一，DS在大促期间每秒要下发成百上千个TaskDispatch，JSON序列化一个Shell任务就850字节，如果换成二进制编码能压缩到多少？一个Master节点要维持50个Worker的连接，带宽省多少？第二，Worker执行完任务怎么告诉Master是成功还是失败？如果这条回执消息在公网上丢了或者TCP重传也失败了，Master是无限期傻等还是超时放弃？第三，上周运维只升了Master没升Worker，Protobuf新增的字段旧版Worker不认识——这是静默丢弃还是抛异常？第四，Master同时连着50个Worker，每个Worker维持多少条TCP长连接？某个Worker宕机后，Master多久能发现并把它从可用列表中剔除？"

**大师从保温杯里倒出一杯茶，缓缓开口：**"把REST over HTTP/1.1想象成传统的邮政信件——格式是人类可读的JSON（信封很大，每封信都要写上收件人地址），每一封信都需要邮递员单独跑一趟（每次POST新建一个TCP连接，三次握手+四次挥手开销巨大），信件丢了发件人毫无感知（HTTP 408超时才报错）。而gRPC+Protobuf+Netty这一套，是军用加密电报系统——消息用二进制密文编码（极小，是JSON体积的1/5），一条专用电报线路（HTTP/2单条TCP连接）可以同时传输成百上千封电报而互不阻塞（多路复用），发出去的每封电报都自带回执编号（响应流），收不到回执自动重发（可靠传输），而且线路断了控制中心能在10秒内发现（KeepAlive PING帧）。"

"小胖说的'HTTP POST JSON'在每天10个任务、1个Worker时完全够用，甚至还更简单。但DS的设计目标是：上百台Worker、每秒数千次任务分发、毫秒级调度延迟。在这种体量下，邮政系统效率是电报的几十分之一——这才是DS选择三层通信栈的根本原因，每一层都不可替代：**Netty**在最底层负责高效的Java NIO事件循环和零拷贝数据传输，单机可以维护数万条并发连接；**gRPC**在中间层提供HTTP/2多路复用和双向流能力，并且用`.proto`服务定义文件保证了所有通信方的接口契约完全一致；**Protobuf**在最顶层负责二进制序列化，把Java对象压缩成极小的字节序列，解析速度比JSON快两个数量级。三层各司其职，缺一层性能都会断崖下跌。"

**小白追问：**"你刚才说`.proto`文件保证接口契约一致——可大麦的事故不就是版本不一致造成的吗？Protobuf的向后兼容到底怎么保证的？"

**大师点头，在白板上写下"字段编号 = 契约"：**"这就是Protobuf设计最精妙也最危险的地方。Protobuf的向后兼容靠的不是字段名，而是字段编号。看这行——`int64 timeout = 8`——发送端和接收端不看`timeout`这个名字，只看`=8`这个编号。如果新版proto新增了`timeout = 8`，旧版Worker的proto文件里没有编号8，Worker在反序列化时就**静默忽略**这个字段——这是Protobuf的'未知字段忽略'机制，是特性而非Bug。但如果运维错误地把原来`status = 3`的编号改成了新字段的编号——比如把`int32 old_field = 3`删掉，新增`int64 timeout = 3`——那就完了。新版Master往编号3里写入一个时间戳（大整数），旧版Worker把编号3里的值当成状态码（小整数）来读——数据彻底错配，而且没有任何警告或异常。这就是大麦事故的本质：字段编号变了，但proto文件没有做到真正的'追加式'演进。"

**小胖挠头：**"那序列化性能差距到底有多大？JSON不就多几KB的事儿嘛？"

**大师拿起计算器：**"我们来算一笔账。一个Shell任务的`TaskDispatchCommand`，包含任务ID、类型、参数JSON、资源列表等——用JSON序列化大约850字节，每个字节都是可见字符，字段名`taskInstanceId`就占了16字节。用Protobuf同一条消息，字段名会变成1-8的数字标签，整数用Varint编码（小数字用1字节，大数字才用多字节），总大小约180字节。压缩比4.7倍。如果每秒1000次dispatch，JSON消耗6.8 Mbps带宽，Protobuf只要1.44 Mbps——一个Master节点就省下5.3 Mbps。更关键的是解析速度：JSON需要完整的词法分析器逐字符扫描、解析引号、处理转义；Protobuf直接把二进制字节按标签映射到内存字段，是JSON解析速度的20到100倍。"

**小白思考了一会儿：**"那还有一个场景——如果Worker执行Spark任务需要十分钟，Master发出dispatch后一直阻塞等回复吗？Master线程池会不会被这些长任务占满？"

**大师放下茶杯：**"这就是gRPC的`deadline`机制大显身手的地方。Master调用`.withDeadlineAfter(30, TimeUnit.SECONDS)`——这30秒不是任务的执行时限，而是'Worker必须在这个时间内给我一个初步响应'的通信超时。Worker收到dispatch后通常会立刻回复一个ACK（`status=1`表示已接收），而不是等十分钟后任务执行完了才回复。如果30秒内连ACK都没收到——`DEADLINE_EXCEEDED`——Master就认为Worker可能挂了，触发重试或故障转移。真正的任务执行结果，Worker会通过另一条RPC回调（`TaskEventCommand`）主动推给Master。这是典型的异步通信模式：dispatch是同步等ACK，结果上报是异步回调。"

---

## 3. 项目实战

### Step 1: 理解extract模块的RPC契约定义

DS的RPC消息定义集中在`dolphinscheduler-extract`模块族中。每个服务对共享一个extract子模块——只要Master和Worker依赖同一版本的extract jar包，消息契约就不可能不一致。

```protobuf
// dolphinscheduler-extract/dolphinscheduler-extract-master/src/main/proto/master_worker.proto
syntax = "proto3";
option java_package = "org.apache.dolphinscheduler.extract.master";
option java_multiple_files = true;

// Master分派任务给Worker——这是调度系统最核心的消息体
message TaskDispatchCommand {
  int64 task_instance_id = 1;        // 任务实例唯一ID
  int64 process_instance_id = 2;     // 所属工作流实例ID
  string task_type = 3;              // "SHELL" / "SPARK" / "SQL" / "PYTHON" ...
  string task_params = 4;            // JSON字符化的任务参数（兼容任意结构）
  repeated ResourceInfo resources = 5; // 需要下载的资源文件列表
  string worker_group = 6;           // 目标Worker分组名
  int32 priority = 7;                // 任务优先级（数值越大越优先）
  int64 timeout = 8;                 // 任务执行超时（秒）
}

// 可执行资源文件描述——Worker在执行前先从资源中心拉取
message ResourceInfo {
  string file_name = 1;              // 如 "clean.sh"
  string file_path = 2;              // 如 "resource://shell/clean.sh"
  int64 file_size = 3;               // 文件大小（字节），用于校验
}

// Worker向Master回传任务执行结果
message TaskExecuteResultCommand {
  int64 task_instance_id = 1;
  int64 process_instance_id = 2;
  int32 status = 3;                  // 1=Submit已提交执行, 2=Running执行中,
                                     // 3=Success成功, 4=Failure失败, 5=Kill被终止
  int32 exit_code = 4;               // 进程退出码（Shell任务时有效）
  string log_path = 5;               // 任务日志文件在Worker上的路径
  repeated string app_ids = 6;       // YARN/K8s的Application ID列表
  string error_message = 7;          // 失败时的错误信息
  int64 start_time = 8;              // 任务开始执行的时间戳
  int64 end_time = 9;                // 任务结束执行的时间戳
}

// Master通知Worker强制终止运行中的任务
message TaskKillCommand {
  int64 task_instance_id = 1;
  int64 process_instance_id = 2;
  string reason = 3;                 // 终止原因（如"工作流被手动停止"）
}

// Worker定时向Master汇报负载情况——用于负载均衡决策
message WorkerHeartbeatCommand {
  string host = 1;                   // Worker IP
  int32 port = 2;                    // Worker RPC端口
  int32 active_task_count = 3;       // 当前正在执行的任务数
  double cpu_usage = 4;              // CPU使用率（0.0~1.0）
  double memory_usage = 5;           // 内存使用率（0.0~1.0）
  double disk_usage = 6;             // 磁盘使用率（0.0~1.0）
}
```

### Step 2: 理解gRPC服务定义——这才是真正的"接口契约"

```protobuf
// 定义Worker暴露给Master的RPC服务接口
service WorkerService {
  // Unary RPC: 一发一收，Master同步等待Worker确认接收
  rpc DispatchTask(TaskDispatchCommand) returns (TaskExecuteResultCommand);
  
  // Unary RPC: 强制终止任务
  rpc KillTask(TaskKillCommand) returns (KillTaskResponse);
  
  // Unary RPC: 心跳上报
  rpc Heartbeat(WorkerHeartbeatCommand) returns (HeartbeatResponse);
}
```

Master作为gRPC客户端持有`WorkerServiceBlockingStub`（同步存根），Worker作为gRPC服务端实现`WorkerServiceImplBase`。值得注意的是，DS当前使用**Unary RPC**（一问一答）而非流式RPC——每次dispatch都是一次独立的HTTP/2请求-响应对。如果未来需要Worker推送实时进度（如"Spark任务进度70%"），就需要改为**Server Streaming RPC**——客户端发一次请求，服务端持续推送多条进度消息。

### Step 3: Master侧——创建gRPC Channel并发起任务分发

```java
public class TaskDispatcher {

    // 获取指向目标Worker的gRPC同步存根
    private WorkerServiceGrpc.WorkerServiceBlockingStub getWorkerStub(
        String host, int port) {

        // NettyChannelBuilder: 底层使用Netty而非OkHttp
        ManagedChannel channel = NettyChannelBuilder
            .forAddress(host, port)
            .usePlaintext()                            // 生产环境应启用TLS加密
            .keepAliveTime(30, TimeUnit.SECONDS)        // 每30秒发一次PING心跳
            .keepAliveTimeout(10, TimeUnit.SECONDS)     // 10秒没收到PING ACK视为断连
            .keepAliveWithoutCalls(true)                // 空闲时也保持心跳
            .maxInboundMessageSize(100 * 1024 * 1024)   // 最大接收消息100MB
            .build();

        return WorkerServiceGrpc.newBlockingStub(channel);
    }

    // 同步派发任务到Worker
    public TaskExecuteResultCommand dispatch(
        WorkerAddress worker, TaskDispatchCommand cmd) {

        try {
            return getWorkerStub(worker.getHost(), worker.getPort())
                .withDeadlineAfter(30, TimeUnit.SECONDS)  // 30秒通信超时
                .dispatchTask(cmd);

        } catch (StatusRuntimeException e) {
            Status.Code code = e.getStatus().getCode();
            if (code == Status.Code.DEADLINE_EXCEEDED) {
                // Worker 30秒内未响应——可能宕机或网络不通
                throw new DispatchTimeoutException("Worker未在deadline内响应", e);
            }
            if (code == Status.Code.UNAVAILABLE) {
                // 连不上Worker——端口未监听或主机不可达
                throw new WorkerUnreachableException("Worker不可达", e);
            }
            throw e;
        }
    }
}
```

`withDeadlineAfter(30秒)`是整个RPC链路的通信超时上限，不是任务执行时限。Worker收到dispatch后通常会先持久化任务记录并立刻回复ACK，然后再异步执行任务逻辑，因此30秒足够覆盖正常情况下的往返延迟。

### Step 4: Worker侧——接收RPC请求并执行任务

```java
// Worker上的RPC服务端实现
public class WorkerRpcServer extends WorkerServiceGrpc.WorkerServiceImplBase {

    @Override
    public void dispatchTask(
        TaskDispatchCommand request,
        StreamObserver<TaskExecuteResultCommand> responseObserver) {

        logger.info("收到Master任务分派: taskId={}, type={}, workerGroup={}",
            request.getTaskInstanceId(), request.getTaskType(), request.getWorkerGroup());

        TaskExecuteResultCommand result;
        try {
            // 委派给TaskExecuteProcessor执行真正的任务逻辑
            result = taskExecuteProcessor.process(request);
        } catch (Exception e) {
            // 任务执行异常——构建失败结果回传Master
            logger.error("任务执行异常: taskId={}", request.getTaskInstanceId(), e);
            result = TaskExecuteResultCommand.newBuilder()
                .setTaskInstanceId(request.getTaskInstanceId())
                .setProcessInstanceId(request.getProcessInstanceId())
                .setStatus(4)  // FAILURE
                .setErrorMessage(e.getMessage())
                .build();
        }

        // 将执行结果通过同一个StreamObserver回传给Master
        responseObserver.onNext(result);
        responseObserver.onCompleted();  // 关闭本次RPC流
    }
}
```

Worker RPC Server继承自Protobuf编译器自动生成的`WorkerServiceImplBase`。每个RPC调用在服务端都由独立的线程处理，因此Worker可以并发处理来自Master的多个dispatch请求。`responseObserver.onCompleted()`标志着这条gRPC流结束，Master端的阻塞Stub随即被唤醒并拿到结果。

### Step 5: Protobuf vs JSON序列化性能对比

```
Shell类型TaskDispatchCommand消息体（约200字节日志命令参数）：

JSON序列化:  ~850 bytes
  示例片段: {"taskInstanceId":12345,"processInstanceId":67890,
   "taskType":"SHELL","taskParams":"{\"script\":\"echo hello\"}",
   "resources":[...],"workerGroup":"default","priority":5,"timeout":3600}

Protobuf:    ~180 bytes
  二进制表示（十六进制）: 08 B9 60 10 92 13 01 1A 05 53 48 45 4C 4C ...

压缩比: 4.7倍
```

对于每秒1000次task dispatch的场景：
- JSON开销: 850 KB/s ≈ 6.8 Mbps
- Protobuf开销: 180 KB/s ≈ 1.44 Mbps  
- 每个Master节点带宽节省: 5.3 Mbps

更重要的是解析性能：JSON需要词法分析器逐字符读入、匹配双引号、处理Unicode转义、构建AST树；Protobuf直接按字段编号定位内存偏移量，使用Varint解码整数，属于零解析开销的反序列化——实测速度差距在20倍到100倍之间。在Master节点这种CPU密集型组件上，省下的每一毫秒解析时间都能分给更多的工作流编排。

### Step 6: 一次完整的RPC调用全链路追踪

```
时间线（Master侧 → Worker侧 → Master侧）：

00:00  Master.TaskDispatcher.dispatch(worker, cmd)
00:01  gRPC Stub → Protobuf序列化为字节数组（~180 bytes）
00:02  Netty → 封装为HTTP/2 DATA帧 → TCP发送到 Worker_IP:Worker_Port
00:05  Worker.Netty → 接收HTTP/2 DATA帧
00:06  Worker.gRPC Server → Protobuf反序列化 → TaskDispatchCommand对象
00:07  Worker.WorkerRpcServer.dispatchTask(request, responseObserver)
00:08  Worker.TaskExecuteProcessor → ShellTask.execute()
       （fork子进程执行shell脚本... 等待子进程结束...）
01:30  Worker.ShellTask → 子进程退出，收集exitCode/stdout/stderr
01:31  Worker → 构建TaskExecuteResultCommand → Protobuf序列化
01:32  Worker.Netty → 发送HTTP/2 DATA帧回Master
01:35  Master.Netty → 接收响应帧
01:36  Master.gRPC Stub → Protobuf反序列化 → TaskExecuteResultCommand
01:37  Master.WorkflowExecuteRunnable.handleTaskResult(result)
```

需要特别指出的是，在DS的实际实现中，Worker收到dispatch后并不会等到任务执行完毕才回复——而是先返回一个`status=SUBMIT`的ACK，然后在任务执行完成后再通过回调通知Master更新状态。上面的时间是简化版描述。

### Step 7: 带指数退避的RPC重试机制

```java
public TaskExecuteResultCommand dispatchWithRetry(
    WorkerAddress worker, TaskDispatchCommand cmd, int maxRetries) {

    for (int attempt = 1; attempt <= maxRetries; attempt++) {
        try {
            return dispatch(worker, cmd);
        } catch (WorkerUnreachableException e) {
            logger.warn("RPC重试 {}/{} 失败: worker={}, error={}",
                attempt, maxRetries, worker, e.getMessage());

            if (attempt == maxRetries) {
                // 所有重试均已耗尽——将任务标记为需要故障转移
                // MasterFailoverCoordinator会将它重新分配给其他Worker
                throw new AllWorkersFailedException(
                    "向Worker[" + worker + "]派发任务失败, 已重试" + maxRetries + "次", e);
            }

            // 指数退避: 第1次等2秒, 第2次等4秒, 第3次等8秒
            long backoffMs = (long) Math.pow(2, attempt) * 1000;
            Thread.sleep(backoffMs);
        }
    }
    throw new IllegalStateException("不可达");
}
```

指数退避策略是分布式系统应对瞬时故障的标准做法：如果只是网络短暂抖动，2秒后大概率恢复正常；如果Worker真的宕机了，等8秒后仍然失败就触发故障转移。避免在Worker恢复期间疯狂重试造成"重试风暴"。

### Step 8: 连接池的创建与死连接淘汰

```java
public class RpcClientManager {
    // 连接池: Worker地址 → gRPC Channel（线程安全）
    private final Map<String, ManagedChannel> channels = new ConcurrentHashMap<>();

    public ManagedChannel getChannel(String host, int port) {
        String key = host + ":" + port;
        return channels.computeIfAbsent(key, k ->
            NettyChannelBuilder.forAddress(host, port)
                .usePlaintext()
                .keepAliveTime(30, TimeUnit.SECONDS)        // PING间隔
                .keepAliveTimeout(10, TimeUnit.SECONDS)     // PING超时
                .keepAliveWithoutCalls(true)                // 静默期也PING
                .maxInboundMessageSize(50 * 1024 * 1024)
                .build()
        );
    }

    // Worker下线或心跳失败时清理死连接
    public void closeDeadChannel(String host, int port) {
        String key = host + ":" + port;
        ManagedChannel channel = channels.remove(key);
        if (channel != null) {
            channel.shutdown();   // 优雅关闭：不再接受新请求，等待已发送请求完成
        }
    }
}
```

连接池的几个关键设计决策：
- `computeIfAbsent`保证了同一个Worker只建立一条物理TCP连接——因为HTTP/2本身就在一条连接上支持多路复用，开多条连接反而浪费资源；
- `keepAliveWithoutCalls(true)`这一项至关重要：如果Worker处于空闲状态（没有任务分派），操作系统默认的TCP keepalive可能要等2小时才会检测到对端断开。gRPC的PING帧把这个检测时间缩短到了10秒，这是生产环境中RPC可靠性的核心保障；
- 池用`ConcurrentHashMap`而非普通`HashMap`，因为dispatch调用可能来自Master线程池的多个线程，需要线程安全的并发访问。

### Step 9: 通过拦截器实现RPC可观测性

```java
// gRPC Server端的拦截器——对Worker透明，Master和Worker都可以注册
public class RpcMetricsInterceptor implements ServerInterceptor {

    @Override
    public <ReqT, RespT> ServerCall.Listener<ReqT> interceptCall(
        ServerCall<ReqT, RespT> call,
        Metadata headers,
        ServerCallHandler<ReqT, RespT> next) {

        long start = System.currentTimeMillis();
        String methodName = call.getMethodDescriptor().getFullMethodName();

        return new ForwardingServerCallListener.SimpleForwardingServerCallListener<ReqT>(
            next.startCall(new ForwardingServerCall<ReqT, RespT>(call) {
                @Override
                public void close(Status status, Metadata trailers) {
                    long duration = System.currentTimeMillis() - start;
                    // 上报指标: 方法名 + 状态码 + 耗时
                    registry.timer("rpc.request.duration",
                        "method", methodName,
                        "status", status.getCode().name())
                        .record(duration, TimeUnit.MILLISECONDS);
                    super.close(status, trailers);
                }
            }, headers)) {};
    }
}
```

将拦截器注册到gRPC Server后，每一次RPC调用的耗时、方法名和返回状态都会被自动记录。接入Prometheus + Grafana后，团队可以绘制RPC延迟分位图（P50/P95/P99），快速识别出哪个Worker响应慢、哪个RPC方法耗时异常。这是生产环境RPC治理的第一步。

### Step 10: 常见RPC故障诊断速查表

| 故障现象 | 根因分析 | 排查工具与方向 |
|---------|---------|--------------|
| `DEADLINE_EXCEEDED` | Worker执行过慢、GC停顿或死锁 | `jstack`检查Worker线程状态，查看GC日志 |
| `UNAVAILABLE` | Worker进程已退出或端口未监听 | `netstat -tlnp`检查端口，`ping`检查网络 |
| 消息字段值异常（幽灵执行） | Protobuf版本不一致导致字段错位 | 对比Master/Worker的proto文件编号，`protoc --decode_raw`分析原始字节 |
| 连接数持续增长（句柄泄漏） | Channel未正确shutdown | `lsof -p <pid>`统计TCP连接数随时间曲线 |
| `RESOURCE_EXHAUSTED` | 消息体积超过`maxInboundMessageSize` | 检查是否是DataX等任务的JSON配置过大 |
| Worker已上线但Master不分配任务 | Worker心跳上报失败或分组名不匹配 | 检查ZK中Worker节点状态，检查`worker_group`配置 |

---

## 4. 项目总结

回望DS的RPC通信栈，三层架构的选型是工程权衡的结果，绝非简单的技术堆砌：

| 通信层 | 选用技术 | 核心职责 | 可替代技术 | 为什么不选替代方案 |
|--------|---------|---------|-----------|-----------------|
| 传输层 | Netty | Java NIO事件循环、零拷贝、高并发连接管理 | 原生Socket、Undertow | Netty的Epoll边缘触发+零拷贝是Java生态NIO的事实标准 |
| RPC框架 | gRPC | HTTP/2多路复用、双向流、强类型服务契约 | Dubbo、Thrift、Spring HTTP | Dubbo以Java为中心难以跨语言；Thrift生态不如gRPC活跃 |
| 序列化 | Protobuf | 二进制紧凑编码、IDL定义、向后兼容 | JSON、Kryo、Hessian、Avro | JSON体积大4倍+缺乏强Schema；Kryo的版本兼容性弱于Protobuf |

**同步调用 vs 异步回调**：DS中对任务分派（dispatch）使用同步Unary RPC+deadline控制，因为Master必须确认Worker已经接收了任务才能继续推进DAG状态机。但任务执行结果的回传是异步的——Worker完成执行后通过回调通知Master，不阻塞dispatch线程。理解这个同步/异步的边界，是排查"任务卡在DISPATCHED状态"类故障的前提。

**连接管理三条铁律**：（1）一个Worker地址只需一条Channel——利用HTTP/2多路复用，一条连接承载所有并发调用；（2）线上必须开启`keepAliveWithoutCalls(true)`——空闲连接也要PING，否则默认TCP keepalive 2小时的检测间隔在生产环境等于没检测；（3）Worker下线时严格执行`remove + shutdown`——只remove不shutdown导致句柄泄漏，只shutdown不remove导致后续请求走到已关闭的Channel抛异常。

**源自协议层的三个真实生产故障及解法**：
1. **字段编号冲突导致数据错配**：升级时新增字段误用了旧字段编号→新旧版本把同一个编号解释为不同类型。**铁律**：Protobuf字段编号只能追加，永不复用删除字段的编号。
2. **DEADLINE_EXCEEDED误杀长任务**：Spark任务执行30分钟，但dispatch deadline只设了30秒→Worker还没执行完就被Master判定超时。**解法**：dispatch只等ACK确认（秒级），不要等完整执行结果（分钟级）。
3. **连接池未清理致OOM**：Worker重启后IP变化，旧Channel留在池中，几百个僵尸连接耗尽Socket句柄。**解法**：心跳检测+死连接自动移除，结合`lsof`定期巡检。

**思考题**：
1. 如果需要对DS的RPC层做一个架构增强——Worker执行长时间Spark任务时，实时向Master推送执行进度百分比（如"已完成70%"），应该用gRPC的哪一类RPC模式？为什么不能用现有的Unary RPC？新的proto服务定义应该怎么写？
2. Protobuf的字段类型变更有一条危险操作：`int64`改`string`。如果某个字段存储的是Unix时间戳（秒），最初用`int64`表示，后来需要改为ISO 8601格式的`string`（如`"2025-01-15T10:30:00Z"`）。在保证向后兼容的前提下，应该如何操作？（提示：考虑新增字段而非修改已有字段）

---

*下一章预告：大麦团队将深入Worker源码，拆解TaskExecuteProcessor如何通过SPI插件机制路由到33+种任务类型的执行逻辑——从Shell任务的进程fork到Spark任务的YARN提交，完整还原Worker端的任务生命周期。*
