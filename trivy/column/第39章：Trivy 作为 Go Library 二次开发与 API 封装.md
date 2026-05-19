# 第39章：Trivy 作为 Go Library 二次开发与 API 封装

> 版本：Trivy v0.50+
> 面向人群：Go 开发者、平台工程师、后端工程师
> 源码参考：pkg/commands/、pkg/scanner/scan.go

---

## 1. 项目背景

### 业务场景

云帆科技的安全平台建设进入了新阶段——不再满足于「调用 Trivy CLI」，而是要「将 Trivy 内嵌到自研平台中」。平台架构师小白提出了一个设想：把 Trivy 的扫描能力封装成一个内部的 HTTP 微服务，前端 Dashboard 通过 RESTful API 调用后端扫描，就像调用一个普通的业务服务一样。

这个设想的优势很明显：不需要 fork 进程（零开销）、不需要解析 stdout（结构化返回）、可以对扫描流程做精细化的控制（自定义缓存策略、自定义过滤逻辑、自定义请求队列）。但挑战同样巨大——Trivy 的源码是为 CLI 设计的，不是为 Library 设计的。很多全局状态（如 Analyzer 注册表、DB 连接池）在并发调用时需要特别注意。

「我们需要的是一个『Trivy-as-a-Service』——开发者不需要安装 Trivy，不需要理解 CLI 参数，只需要一个 HTTP API。」CTO 设想了一个内部门户：「你上传一个 Dockerfile 或者指定一个镜像名，系统就能返回安全扫描报告。背后由 Trivy Library 驱动。」

### 痛点放大

**第一，CLI 模式不适合微服务化。** 每次 `docker run trivy image xxx` 都是一个完整的进程生命周期——fork、加载配置、初始化缓存、下载 DB、执行扫描、序列化结果、销毁进程。这种「一次性」模型在微服务场景下极其浪费。

**第二，并发安全需要额外关注。** Trivy 的 Analyzer 注册表是全局的。如果 Library 模式下同时处理两个扫描请求，全局状态可能导致数据竞争。需要参考 Trivy 的 Server 模式实现来理解并发控制。

**第三，资源隔离是必须的。** HTTP 服务可以同时接收 100 个扫描请求——如果全部立即执行，内存可能在 10 秒内被吞光。必须有请求级别的超时、取消、内存限制和队列控制。

**第四，gRPC 服务化支持多语言调用。** 如果前端是 Python/Node.js，不能直接调用 Go Library。需要通过 gRPC 暴露跨语言的服务接口。

**本章的核心目标是：将 Trivy 作为 Go Library 内嵌到 HTTP 和 gRPC 服务中，实现请求队列、并发控制、资源隔离和优雅关闭。**

---

## 2. 项目设计

**场景**：云帆科技的 Trivy 服务化讨论，小白（架构师）、小胖（后端）、大师在讨论设计。

---

**小胖**：「直接用 CLI 不香吗？`exec.Command("trivy", "image", img)` 一行代码搞定，何必这么复杂？」

**小白**：「fork 一个进程的开销远比你想象的大。Trivy 的初始化阶段包括解析 CLI 参数、加载配置文件、下载/缓存 DB、初始化所有 Analyzer——这个冷启动过程可能消耗 5-10 秒。如果你每秒要处理 10 个扫描请求，每个都要 5 秒冷启动——相当于 50 个并发进程，服务器直接垮掉。Library 模式下，初始化只做一次，后续请求直接复用——P99 延迟从 10 秒降到 1 秒。」

**大师**：「技术映射：CLI 模式就像『外卖』——每次都要叫一个骑手（fork 进程）从餐厅（启动 Trivy）取餐送到你家。Library 模式就像『自己在家做饭』——你有一个常设的厨房（常驻进程），想做就做，不需要等骑手。」

**小胖**：「那 Library 模式怎么处理并发？不会有共享状态冲突吗？」

**小白**：「参考 Trivy 的 Server 模式（pkg/commands/server/），它已经解决了这个问题。核心做法是：

1. **每个请求创建一个独立的 Scanner 实例**（ScanArtifact 是线程安全的）。
2. **数据库连接池（BoltDB 读多写少，适合并发读）**。
3. **缓存是共享的（Redis/FS 缓存天然线程安全）**。
4. **用 semaphore 控制最大并发数**——服务启动时创建一个带缓冲的 channel 作为信号量。

```go
// 并发控制信号量
concurrencyLimiter := make(chan struct{}, maxConcurrency)

func handleScan(w http.ResponseWriter, r *http.Request) {
    // 获取信号量（阻塞直到有空位）
    concurrencyLimiter <- struct{}{}
    defer func() { <-concurrencyLimiter }()

    // 执行扫描...
}
```

**大师**：「gRPC 服务化的优势在于：

1. **跨语言调用**：Python/Node.js/Java 客户端都可以通过相同的 Protobuf 定义调用扫描服务。
2. **流式响应**：大数据量扫描结果可以通过 gRPC Server Streaming 逐步返回，而非一次性 JSON 序列化。
3. **内置负载均衡**：gRPC 的客户端负载均衡可以自动分发请求到多个服务实例。
4. **强类型接口契约**：Protobuf 定义的 API 本身就是文档。」

---

## 3. 项目实战

### 环境准备

- **Go**：1.21+
- **Trivy 源码**：已 clone，理解 pkg/scanner/ 和 pkg/commands/server/ 的实现
- **protoc + protoc-gen-go-grpc**：用于 gRPC 代码生成

### 步骤一：编写 Trivy-as-a-Service HTTP 服务

**目标**：创建 HTTP 服务，暴露 /scan/image 和 /scan/fs 端点。

创建 `trivy-service/main.go`：

```go
package main

import (
    "context"
    "encoding/json"
    "fmt"
    "log"
    "net/http"
    "os"
    "os/signal"
    "syscall"
    "time"

    "github.com/aquasecurity/trivy/pkg/commands/artifact"
    "github.com/aquasecurity/trivy/pkg/flag"
    "github.com/aquasecurity/trivy/pkg/types"
)

// ScannerService Trivy 扫描服务
type ScannerService struct {
    concurrencyLimit chan struct{}
    server           *http.Server
}

func NewScannerService(addr string, maxConcurrency int) *ScannerService {
    s := &ScannerService{
        concurrencyLimit: make(chan struct{}, maxConcurrency),
    }

    mux := http.NewServeMux()
    mux.HandleFunc("/scan/image", s.handleScanImage)
    mux.HandleFunc("/scan/fs", s.handleScanFilesystem)
    mux.HandleFunc("/health", s.handleHealth)
    mux.HandleFunc("/metrics", s.handleMetrics)

    s.server = &http.Server{
        Addr:         addr,
        Handler:      mux,
        ReadTimeout:  15 * time.Second,
        WriteTimeout: 120 * time.Second, // 扫描可能较慢
        IdleTimeout:  60 * time.Second,
    }

    return s
}

func (s *ScannerService) handleScanImage(w http.ResponseWriter, r *http.Request) {
    // 并发控制：获取信号量
    select {
    case s.concurrencyLimit <- struct{}{}:
        defer func() { <-s.concurrencyLimit }()
    case <-r.Context().Done():
        http.Error(w, "request cancelled or timeout", http.StatusServiceUnavailable)
        return
    }

    // 解析请求
    var req struct {
        Image    string `json:"image"`
        Severity string `json:"severity,omitempty"`
    }
    if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
        http.Error(w, "invalid request body", http.StatusBadRequest)
        return
    }

    if req.Image == "" {
        http.Error(w, "image is required", http.StatusBadRequest)
        return
    }

    severity := req.Severity
    if severity == "" {
        severity = "HIGH,CRITICAL"
    }

    // 构建扫描选项
    opts := flag.Options{
        GlobalOptions: flag.GlobalOptions{
            Timeout: 10 * time.Minute,
        },
        ScanOptions: flag.ScanOptions{
            Target:   req.Image,
            Severity: severity,
        },
        ReportOptions: flag.ReportOptions{
            Format: "json",
        },
    }

    // 执行扫描
    ctx, cancel := context.WithTimeout(r.Context(), 10*time.Minute)
    defer cancel()

    report, err := s.scanImage(ctx, opts)
    if err != nil {
        log.Printf("Scan failed for %s: %v", req.Image, err)
        http.Error(w, fmt.Sprintf("scan failed: %v", err), http.StatusInternalServerError)
        return
    }

    // 返回结果
    w.Header().Set("Content-Type", "application/json")
    json.NewEncoder(w).Encode(report)
}

func (s *ScannerService) handleScanFilesystem(w http.ResponseWriter, r *http.Request) {
    select {
    case s.concurrencyLimit <- struct{}{}:
        defer func() { <-s.concurrencyLimit }()
    case <-r.Context().Done():
        http.Error(w, "request cancelled", http.StatusServiceUnavailable)
        return
    }

    var req struct {
        Path     string `json:"path"`
        Severity string `json:"severity,omitempty"`
    }
    if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
        http.Error(w, "invalid request body", http.StatusBadRequest)
        return
    }

    severity := req.Severity
    if severity == "" {
        severity = "HIGH,CRITICAL"
    }

    opts := flag.Options{
        GlobalOptions: flag.GlobalOptions{
            Timeout: 5 * time.Minute,
        },
        ScanOptions: flag.ScanOptions{
            Target:   req.Path,
            Severity: severity,
        },
        ReportOptions: flag.ReportOptions{
            Format: "json",
        },
    }

    ctx, cancel := context.WithTimeout(r.Context(), 5*time.Minute)
    defer cancel()

    report, err := s.scanFilesystem(ctx, opts)
    if err != nil {
        log.Printf("Filesystem scan failed for %s: %v", req.Path, err)
        http.Error(w, fmt.Sprintf("scan failed: %v", err), http.StatusInternalServerError)
        return
    }

    w.Header().Set("Content-Type", "application/json")
    json.NewEncoder(w).Encode(report)
}

func (s *ScannerService) handleHealth(w http.ResponseWriter, r *http.Request) {
    // 检查当前并发情况
    active := len(s.concurrencyLimit)
    capacity := cap(s.concurrencyLimit)

    w.Header().Set("Content-Type", "application/json")
    json.NewEncoder(w).Encode(map[string]interface{}{
        "status":               "healthy",
        "active_requests":      active,
        "max_requests":         capacity,
        "available_slots":      capacity - active,
    })
}

func (s *ScannerService) handleMetrics(w http.ResponseWriter, r *http.Request) {
    active := len(s.concurrencyLimit)
    capacity := cap(s.concurrencyLimit)

    w.Header().Set("Content-Type", "text/plain")
    fmt.Fprintf(w, "trivy_service_active_requests %d\n", active)
    fmt.Fprintf(w, "trivy_service_max_requests %d\n", capacity)
}

func (s *ScannerService) scanImage(ctx context.Context, opts flag.Options) (types.Report, error) {
    // 初始化 Scanner（复用 Trivy 的 artifact.Run 逻辑）
    // 注意：这里简化了实际的初始化过程
    // 生产级别应该只初始化一次，然后复用 Scanner 实例
    return artifact.Run(ctx, opts, artifact.TargetContainerImage)
}

func (s *ScannerService) scanFilesystem(ctx context.Context, opts flag.Options) (types.Report, error) {
    return artifact.Run(ctx, opts, artifact.TargetFilesystem)
}

func (s *ScannerService) Run() error {
    log.Printf("Starting Trivy Scan Service on %s (max concurrency: %d)",
        s.server.Addr, cap(s.concurrencyLimit))

    // 优雅关闭
    quit := make(chan os.Signal, 1)
    signal.Notify(quit, syscall.SIGINT, syscall.SIGTERM)

    go func() {
        <-quit
        log.Println("Shutting down...")
        ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
        defer cancel()
        s.server.Shutdown(ctx)
    }()

    if err := s.server.ListenAndServe(); err != nil && err != http.ErrServerClosed {
        return err
    }

    log.Println("Server stopped")
    return nil
}

func main() {
    maxConcurrency := 5 // 默认最大并发扫描数
    service := NewScannerService(":8080", maxConcurrency)

    if err := service.Run(); err != nil {
        log.Fatal(err)
    }
}
```

### 步骤二：编写 gRPC 服务定义

**目标**：定义扫描服务的 Protobuf 接口。

创建 `proto/scanner.proto`：

```protobuf
syntax = "proto3";

package scanner;

option go_package = "github.com/cloud-sail/trivy-service/proto/scanner";

service TrivyScanner {
    // 扫描容器镜像
    rpc ScanImage(ScanImageRequest) returns (ScanImageResponse);

    // 扫描本地文件系统
    rpc ScanFilesystem(ScanFilesystemRequest) returns (ScanFilesystemResponse);

    // 流式扫描大镜像（服务端流式返回）
    rpc ScanImageStream(ScanImageRequest) returns (stream ScanProgress);

    // 健康检查
    rpc HealthCheck(HealthCheckRequest) returns (HealthCheckResponse);
}

message ScanImageRequest {
    string image = 1;
    string severity = 2;  // "HIGH,CRITICAL"
    repeated string scanners = 3;  // ["vuln", "misconfig", "secret"]
    bool include_sbom = 4;
}

message ScanImageResponse {
    string artifact_name = 1;
    string artifact_type = 2;
    repeated Result results = 3;
    int32 critical_count = 4;
    int32 high_count = 5;
}

message ScanFilesystemRequest {
    string path = 1;
    string severity = 2;
    repeated string scanners = 3;
}

message ScanFilesystemResponse {
    string path = 1;
    repeated Result results = 2;
}

message Result {
    string target = 1;
    string class = 2;
    string type = 3;
    repeated Vulnerability vulnerabilities = 4;
    repeated Misconfiguration misconfigurations = 5;
}

message Vulnerability {
    string vulnerability_id = 1;
    string pkg_name = 2;
    string installed_version = 3;
    string fixed_version = 4;
    string severity = 5;
    string title = 6;
    string description = 7;
}

message Misconfiguration {
    string id = 1;
    string title = 2;
    string severity = 3;
    string description = 4;
    string resolution = 5;
}

message ScanProgress {
    string status = 1;       // "analyzing", "detecting", "done"
    string layer = 2;        // current layer digest
    int32 layers_total = 3;
    int32 layers_done = 4;
    int32 vulnerabilities_found = 5;
}

message HealthCheckRequest {}

message HealthCheckResponse {
    string status = 1;
    int32 active_requests = 2;
    int32 max_requests = 3;
    int64 uptime_seconds = 4;
}
```

生成 Go 代码：

```bash
protoc --go_out=. --go-grpc_out=. proto/scanner.proto
```

### 步骤三：实现 gRPC 服务端

**目标**：将 HTTP 服务的逻辑移植到 gRPC。

```go
// grpc-server/server.go
package main

import (
    "context"
    "io"
    "log"
    "net"
    "time"

    "github.com/aquasecurity/trivy/pkg/commands/artifact"
    "github.com/aquasecurity/trivy/pkg/flag"
    pb "github.com/cloud-sail/trivy-service/proto/scanner"
    "google.golang.org/grpc"
)

type scannerServer struct {
    pb.UnimplementedTrivyScannerServer
    concurrencyLimit chan struct{}
}

func (s *scannerServer) ScanImage(ctx context.Context, req *pb.ScanImageRequest) (*pb.ScanImageResponse, error) {
    // 并发控制
    select {
    case s.concurrencyLimit <- struct{}{}:
        defer func() { <-s.concurrencyLimit }()
    case <-ctx.Done():
        return nil, ctx.Err()
    }

    opts := flag.Options{
        GlobalOptions: flag.GlobalOptions{Timeout: 10 * time.Minute},
        ScanOptions: flag.ScanOptions{
            Target:   req.Image,
            Severity: req.Severity,
        },
        ReportOptions: flag.ReportOptions{Format: "json"},
    }

    report, err := artifact.Run(ctx, opts, artifact.TargetContainerImage)
    if err != nil {
        return nil, err
    }

    resp := &pb.ScanImageResponse{
        ArtifactName: report.ArtifactName,
        ArtifactType: string(report.ArtifactType),
    }

    for _, r := range report.Results {
        pbResult := &pb.Result{
            Target: r.Target,
            Class:  string(r.Class),
            Type:   r.Type,
        }
        for _, v := range r.Vulnerabilities {
            pbResult.Vulnerabilities = append(pbResult.Vulnerabilities, &pb.Vulnerability{
                VulnerabilityId:  v.VulnerabilityID,
                PkgName:          v.PkgName,
                InstalledVersion: v.InstalledVersion,
                FixedVersion:     v.FixedVersion,
                Severity:         v.Severity,
                Title:            v.Title,
                Description:      v.Description,
            })
            switch v.Severity {
            case "CRITICAL":
                resp.CriticalCount++
            case "HIGH":
                resp.HighCount++
            }
        }
        resp.Results = append(resp.Results, pbResult)
    }

    return resp, nil
}

// ScanImageStream 服务端流式返回扫描进度
func (s *scannerServer) ScanImageStream(req *pb.ScanImageRequest, stream pb.TrivyScanner_ScanImageStreamServer) error {
    // 发送开始进度
    stream.Send(&pb.ScanProgress{
        Status:       "analyzing",
        LayersTotal:  5,
        LayersDone:   0,
    })

    // 模拟逐层扫描过程
    for i := 1; i <= 5; i++ {
        time.Sleep(500 * time.Millisecond)
        stream.Send(&pb.ScanProgress{
            Status:      "analyzing",
            Layer:       fmt.Sprintf("sha256:layer-%d", i),
            LayersTotal: 5,
            LayersDone:  int32(i),
        })
    }

    // 发送完成进度
    stream.Send(&pb.ScanProgress{
        Status:               "done",
        LayersTotal:          5,
        LayersDone:           5,
        VulnerabilitiesFound: 3,
    })

    return nil
}

func (s *scannerServer) HealthCheck(ctx context.Context, req *pb.HealthCheckRequest) (*pb.HealthCheckResponse, error) {
    return &pb.HealthCheckResponse{
        Status:         "healthy",
        ActiveRequests: int32(len(s.concurrencyLimit)),
        MaxRequests:    int32(cap(s.concurrencyLimit)),
    }, nil
}

func main() {
    lis, err := net.Listen("tcp", ":50051")
    if err != nil {
        log.Fatal(err)
    }

    s := grpc.NewServer()
    pb.RegisterTrivyScannerServer(s, &scannerServer{
        concurrencyLimit: make(chan struct{}, 10),
    })

    log.Println("gRPC server listening on :50051")
    if err := s.Serve(lis); err != nil {
        log.Fatal(err)
    }
}
```

### 步骤四：配置 K8s 部署

**目标**：在 K8s 中部署 Trivy-as-a-Service，配置 HPA 自动扩缩。

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: trivy-scanner
spec:
  replicas: 3
  selector:
    matchLabels:
      app: trivy-scanner
  template:
    metadata:
      labels:
        app: trivy-scanner
    spec:
      containers:
      - name: scanner
        image: trivy-scanner:v1.0.0
        ports:
        - containerPort: 8080
          name: http
        - containerPort: 50051
          name: grpc
        env:
        - name: TRIVY_CACHE_DIR
          value: /cache
        - name: GOMAXPROCS
          value: "4"
        - name: GOMEMLIMIT
          value: "4096MiB"
        resources:
          requests:
            cpu: "2"
            memory: "2Gi"
          limits:
            cpu: "6"
            memory: "8Gi"
        volumeMounts:
        - name: cache
          mountPath: /cache
        livenessProbe:
          httpGet:
            path: /health
            port: 8080
          initialDelaySeconds: 30
          periodSeconds: 10
        readinessProbe:
          httpGet:
            path: /health
            port: 8080
          initialDelaySeconds: 5
          periodSeconds: 5
      volumes:
      - name: cache
        persistentVolumeClaim:
          claimName: trivy-cache-pvc
---
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: trivy-scanner-hpa
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: trivy-scanner
  minReplicas: 3
  maxReplicas: 20
  metrics:
  - type: Pods
    pods:
      metric:
        name: trivy_service_active_requests
      target:
        type: AverageValue
        averageValue: "3"
```

### 步骤五：编写 Python 客户端示例

**目标**：演示如何从 Python 调用 gRPC 扫描服务。

```python
# python-client/scan_client.py
import grpc
import scanner_pb2
import scanner_pb2_grpc


def scan_image(image_name, severity="HIGH,CRITICAL"):
    """调用 Trivy gRPC 扫描服务"""
    channel = grpc.insecure_channel("trivy-scanner:50051")
    stub = scanner_pb2_grpc.TrivyScannerStub(channel)

    request = scanner_pb2.ScanImageRequest(
        image=image_name,
        severity=severity,
        scanners=["vuln", "misconfig", "secret"],
    )

    try:
        response = stub.ScanImage(request, timeout=120)
        print(f"Scan result for {response.artifact_name}:")
        print(f"  CRITICAL: {response.critical_count}")
        print(f"  HIGH: {response.high_count}")
        return response
    except grpc.RpcError as e:
        print(f"Scan failed: {e}")
        raise


if __name__ == "__main__":
    scan_image("alpine:3.19")
```

### 测试验证

1. 启动 HTTP 服务，用 curl 调用 /health 验证服务存活和并发槽位。
2. 发送 POST /scan/image 扫描 alpine:latest，验证返回的 JSON 格式正确。
3. 发送 10 个并发请求（超过并发限制 5），验证第 6 个请求被阻塞直到前面完成，而非直接返回错误。
4. 启动 gRPC 服务，用 gRPC client 调用 ScanImageStream，验证流式进度更新。
5. 部署到 K8s，配置 HPA，压测 50 个并发请求，观察 HPA 自动从 3 副本扩展到 10+ 副本。

---

## 4. 项目总结

### 优点 & 缺点

| 维度 | 优点 | 缺点 |
|------|------|------|
| Library 模式 | 零进程开销；长连接复用 DB 连接池 | 全局状态需要额外并发控制 |
| HTTP API | 最简单的接入方式；curl 即可调试 | 文本序列化开销大 |
| gRPC | 跨语言；流式传输；强类型 | 需要维护 Protobuf 定义 |
| 并发控制 | semaphore 模式简单有效 | 无法区分不同类型请求的优先级 |
| K8s HPA | 按请求数自动扩缩 | 冷启动延迟（镜像拉取） |

### 适用场景

1. **内部安全平台后端**：前端 Dashboard → HTTP/gRPC API → Trivy Library。
2. **多语言微服务环境**：Python/Node.js 服务需要扫描能力，通过 gRPC 调用 Go 扫描服务。
3. **DevSecOps 工具链集成**：Jenkins/GitLab CI Runner 通过 REST API 触发扫描而非安装 Trivy CLI。
4. **扫描 SaaS 服务**：将 Trivy 包装成 SaaS 扫描引擎，按调用量计费。
5. **CI/CD 平台内置安全能力**：如 GitLab 将 Trivy 库集成到内置的 Container Scanning 功能。

**不适用场景**：
1. 单次/临时扫描——CLI 更快更方便。
2. 无 Go 开发能力的团队——维护 Library 封装需要 Go 基础。

### 注意事项

- **Library 初始化要做一次。** 不要在每次请求中重新初始化 Analyzer 和 DB 连接——在服务启动时初始化一次，后续请求复用。
- **内存限制要保守。** 单个 Trivy Library 实例的内存占用不可预测（取决于被扫描的镜像大小）。GOMEMLIMIT + Container memory limit 双重保险。
- **请求超时必须设置。** 没有超时控制的扫描可能永久阻塞（如 Registry 不可达）。每个请求必须有独立的 context.WithTimeout。
- **优雅关闭不能忽略正在进行的扫描。** 收到 SIGTERM 后，先停止接受新请求，等待所有进行中的扫描完成，最后释放资源。

### 常见踩坑经验

**踩坑案例 1：Library 模式下 Analyzer 注册表冲突**
- **现象**：并发请求导致 panic: concurrent map writes。
- **根因**：多个 goroutine 同时写入 Analyzer 注册表（某些 Analyzer 在第一次使用时懒加载）。
- **解法**：确保所有 Analyzer 在服务启动阶段（单线程）完成注册；避免在运行时调用 RegisterAnalyzer。

**踩坑案例 2：gRPC 服务的默认消息大小限制**
- **现象**：扫描大镜像返回的结果超过 4MB，gRPC 报错 resource exhausted。
- **根因**：gRPC 默认最大消息大小为 4MB。
- **解法**：在服务端和客户端同时设置：grpc.MaxRecvMsgSize(100*1024*1024)，grpc.MaxSendMsgSize(100*1024*1024)。

**踩坑案例 3：HPA 基于 trivy_service_active_requests 扩缩不准确**
- **现象**：请求少时 HPA 不缩容，请求多时缩不回来。
- **根因**：active_requests 是一个瞬时值，波动大。HPA 需要 smooth 的指标。
- **解法**：改为基于平均队列深度（过去 5 分钟均值）的指标；或将扫描任务改为异步队列模式（HTTP API 只做任务入队，Worker 异步处理）。

### 思考题

1. 如果要将 Trivy-as-a-Service 包装成 Serverless Function（如 AWS Lambda / KNative），如何应对「冷启动」问题？哪些初始化步骤可以预加载？哪些无法预加载？冷启动的延迟能否接受？
2. 在你的 gRPC 扫描服务中，如果某个请求扫描的镜像很大（8GB），占用大量内存导致其他请求变慢甚至 OOM。请设计一个「资源隔离」方案——大请求和小请求分开处理，互不影响。

> **答案提示**：第 40 章「从零构建企业级安全扫描平台」将整合 Library 模式、gRPC 服务和分布式调度，构建完整的自研安全平台。

---

> **推广计划**：本章建议由平台工程团队主导，后端工程师协助 API 设计。分三阶段：第一阶段（1 周）HTTP 服务 POC——跑通基本的 /scan/image 端点。第二阶段（2 周）gRPC 服务化——定义 Protobuf、实现流式传输、压测并发性能。第三阶段（2 周）K8s 部署 + HPA + 监控——实现生产级别的可用性和弹性伸缩。推广时先从内部工具的扫描需求开始，验证稳定性后再对全公司开放。

---

> **版权声明**：本章基于 Trivy 官方开源项目（Apache-2.0 License）编写，gRPC 为 Google 开源项目。所有源码引用均遵循原许可证条款。
