# 第36章：Scanner Adapter 扫描适配器源码

## 1 项目背景

某大型金融科技公司内部自研了一套"天鹰"容器漏洞扫描引擎，该引擎在CVE数据源的时效性和误报率控制上优于市面开源方案。公司已将Harbor作为统一的镜像仓库管理平台，现在需要将"天鹰"引擎无缝集成到Harbor中，让所有业务团队在推送镜像时自动触发扫描，并在Portal上直接查看扫描结果。

**痛点一：Scanner Adapter接口规范不透明。** Harbor要求扫描器实现特定的HTTP API规范，但官方文档对`metadata`端点中的`properties`字段、`capabilities`中的MIME type格式说明较为简略。团队在开发过程中频繁遇到Harbor Core调用扫描器时返回400错误——排查发现是`produces_mime_types`未包含Harbor期望的`application/vnd.scanner.adapter.vuln.report.harbor+json; version=1.0`，导致报告格式校验失败。

**痛点二：异步扫描的超时与重试机制不明确。** "天鹰"引擎针对大镜像（>8GB）扫描进行了深度优化，单层分析耗时约3分钟。但集成后发现Harbor频繁将扫描任务标记为`Error`。团队追踪日志发现：Harbor在调用`POST /scan`后默认等待30秒未收到报告就开始轮询，而自研引擎的`scan`端点错误地实现了**同步模式**——在`scan`接口内等待完整扫描结果才返回，导致Harbor认为扫描器无响应。

**痛点三：多扫描器协同与结果聚合。** 公司安全架构要求同时使用"天鹰"（OS层漏洞扫描）和Snyk（应用依赖扫描），两套引擎的报告格式和CVE评分体系不同。团队希望在Harbor中聚合展示两种结果，并支持配置"AND/OR"策略——只有两套扫描都通过（AND）才允许镜像被拉取。

**痛点四：Registry临时Token的生命周期管理。** Scanner Adapter在接收扫描请求时会拿到一个Registry临时授权Token，用于从Harbor Registry拉取镜像层进行分析。该Token默认30分钟过期，但大镜像的扫描+网络传输时间可能超过此窗口。团队需要设计Token续期或提前拉取的策略，避免"扫描到一半Token过期"的尴尬。

---

## 2 项目设计——剧本式交锋对话

*会议室白板上画满了Scanner Adapter的架构图，桌上一杯枸杞茶冒着热气。*

**小胖**：（瘫在椅子上刷手机）"大师，这个Scanner Adapter不就是个简单的HTTP服务吗？我给'天鹰'包一层REST API不就行了？我看也就三个接口——`metadata`、`scan`、`report`，一个下午就能搞定吧？"

**大师**：（放下茶杯，微微一笑）"小胖啊，你这就好比以为'送外卖就是骑车把饭送过去'——可是平台怎么知道你在哪？订单超时怎么处理？客户投诉怎么追溯？Scanner Adapter本质上是一个**协议适配层**，Harbor通过这套协议定义了扫描器必须遵守的契约。表面看是三个接口，下面藏着7种状态码、4种MIME type、以及一套完整的异步轮询机制。"

**小白**：（在白板上圈出`POST /scan`）"我注意到了一个问题：`scan`接口返回的是一个`{id: "xxx"}`，然后Harbor去轮询`/scan/{id}/report`。这意味着扫描必须是异步的。但如果扫描器内部是同步的——比如"天鹰"确实需要3分钟才能扫完一个8GB的镜像——这个异步模型怎么适配？扫描器要自己实现任务队列吗？"

**大师**：（站起身走到白板前）"问得好。这就像餐厅后厨——Harbor是服务员，扫描器是厨师。服务员给厨师下单（`POST /scan`），厨师不能让他傻站着等，得先回一句'明白了，先去忙别的'（返回202+ID），然后把单子挂墙上（入队）。Harbor过两分钟再探头问'好了没'（轮询`/report`），厨师说'再等会儿'（返回302），直到菜炒好了才端出来（200+报告JSON）。"

**小胖**："所以'天鹰'需要自己搞一个Job队列？那不得引入Redis或者RabbitMQ？太麻烦了吧！"

**大师**："不必。你看，Harbor只关心两件事：第一，你收到请求后立刻返回202+ID；第二，当你调用`/report`时，要么返回完整报告，要么返回302表示还没好。至于内部怎么实现，Go里一个goroutine+channel就搞定了，跟点外卖下单一样——你用手机下单（goroutine），订单号就是ID，完成时推送通知（channel写结果）。"

**小白**：（继续追问）"那`metadata`接口返回的`capabilities`里有个`produces_mime_types`字段，这个字段如果写错了会怎样？我看到有人写成了`application/vnd.security.vulnerability.report`，结果Harbor根本不承认扫描器的报告。"

**大师**："这就是协议契约的关键了。想象一下快递公司——你寄快递，快递员必须穿橙色制服、车上有Logo，否则客户不敢把包裹给他。同理，Harbor通过`produces_mime_types`来验证'你的报告格式我能读吗'。如果声明的类型和实际返回的不一致，就好比快递员穿着橙色制服，但递过来的包裹却是外卖盒——Harbor直接拒绝。MIME type本质上就是格式的身份证。"

**小胖**："还有一个细节——Registry的Token。Harbor传过来的`authorization`字段是个Bearer Token，但这个Token只有30分钟有效期。咱们扫大镜像的时候，光下载镜像层就要20多分钟，扫完可能就超时了，咋办？"

**大师**：（在`ScanRequest`结构体旁画了一个"机器人"）"这个设计其实很聪明——你拿到Token后不要马上去拉镜像做分析，而是先快速把镜像层**全部预拉取到本地**。Token只在拉取时校验一次，拉取完成后数据就在本地了，后续分析不需要Token。就像你去图书馆借书——借书证只在借阅那一刻需要，书到手了，回家看多久都行。"

**小胖**："那如果镜像有100个层，拉了50个层后Token过期了呢？"

**大师**："那说明你的Adapter实现有bug——Registry的Token校验是在每个HTTP请求的`Authorization`头中进行的，只要你持有有效Token发起的请求序列中，每次都用同一个Token，Registry不会在传输中途吊销它。但如果你先停20分钟再继续发请求，Token过期了，那就需要重新获取。最佳实践是：拿到Token后立刻用一个循环`PullBlob`把所有层拉完，不中断。就像你拿到24小时健身房的门禁卡——进门后你练多久都行，但如果你中途出去买夜宵，再回来可能就刷不开门了。"

**小白**：（站起来走到白板前画出流程）"我总结一下：第一，Adapter必须异步返回202+ID；第二，`produces_mime_types`必须与Harbor标准匹配；第三，Token要拿到后立刻用完；第四，`metadata`里的`harbor.scanner-adapter/scanner-type`必须设为正确的值。对吧？"

**大师**：（点头）"还有第五点，容易被忽略——`report`接口返回的`severity`字段，Harbor会用它与项目的CVE阻止策略做比较。如果扫描器返回了非标准severity（比如自定义的'Critical+'），Harbor无法识别，策略就形同虚设。severity必须是`Unknown`/`Low`/`Medium`/`High`/`Critical`之一。"

---

## 3 项目实战

### 3.1 环境要求

| 组件 | 版本要求 | 说明 |
|------|---------|------|
| Go | >= 1.20 | Scanner Adapter开发语言 |
| Harbor | >= v2.8.0 | 支持Scanner Adapter v1.0协议 |
| Docker | >= 20.10 | 用于构建和测试Adapter镜像 |
| curl/jq | 任意版本 | 用于API测试 |
| "天鹰"扫描引擎 | v2.3.1 | 内部分发的CLI扫描工具 |

### 3.2 步骤一：搭建Adapter骨架

**目标**：实现一个符合Harbor Scanner Adapter v1.0规范的HTTP服务框架。

```go
// main.go —— Scanner Adapter入口
package main

import (
    "context"
    "crypto/sha256"
    "encoding/json"
    "fmt"
    "log"
    "net/http"
    "os"
    "os/signal"
    "sync"
    "syscall"
    "time"

    "github.com/gorilla/mux"
)

// ServerConfig 从环境变量读取Adapter配置
type ServerConfig struct {
    ListenAddr    string        // 监听地址，默认 ":8080"
    TrivyPath     string        // 天鹰扫描引擎CLI路径
    ScanTimeout   time.Duration // 单次扫描超时
    RegistryCache string        // 镜像层本地缓存目录
}

func loadConfig() *ServerConfig {
    return &ServerConfig{
        ListenAddr:    getEnv("SCANNER_LISTEN_ADDR", ":8080"),
        TrivyPath:     getEnv("SCANNER_CLI_PATH", "/usr/local/bin/tianying"),
        ScanTimeout:   parseDuration(getEnv("SCANNER_TIMEOUT", "10m")),
        RegistryCache: getEnv("SCANNER_CACHE_DIR", "/tmp/scanner-cache"),
    }
}

func getEnv(key, fallback string) string {
    if v := os.Getenv(key); v != "" {
        return v
    }
    return fallback
}

func parseDuration(s string) time.Duration {
    d, _ := time.ParseDuration(s)
    return d
}

func main() {
    cfg := loadConfig()

    // 创建扫描任务管理器（自带goroutine池）
    tm := NewTaskManager(10, cfg)

    r := mux.NewRouter()
    r.HandleFunc("/api/v1/metadata", metadataHandler).Methods("GET")
    r.HandleFunc("/api/v1/scan", tm.scanHandler).Methods("POST")
    r.HandleFunc("/api/v1/scan/{request_id}/report", tm.reportHandler).Methods("GET")

    srv := &http.Server{
        Addr:         cfg.ListenAddr,
        Handler:      r,
        ReadTimeout:  15 * time.Second,
        WriteTimeout: 30 * time.Second,
    }

    // 优雅关闭
    go func() {
        sigCh := make(chan os.Signal, 1)
        signal.Notify(sigCh, syscall.SIGINT, syscall.SIGTERM)
        <-sigCh
        log.Println("[INFO] Shutting down scanner adapter...")
        ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
        defer cancel()
        srv.Shutdown(ctx)
        tm.Shutdown()
    }()

    log.Printf("[INFO] Scanner Adapter listening on %s", cfg.ListenAddr)
    if err := srv.ListenAndServe(); err != http.ErrServerClosed {
        log.Fatalf("[FATAL] %v", err)
    }
}
```

### 3.3 步骤二：实现metadata接口

**目标**：返回扫描器能力描述，包括支持的制品类型、输出报告格式、以及扫描器类型标记。

```go
// metadata.go —— 扫描器能力声明

// ScannerProperties 扫描器元数据
type ScannerProperties struct {
    HarborScannerType string `json:"harbor.scanner-adapter/scanner-type"`
    HarborRegistryURL string `json:"harbor.scanner-adapter/registry-url"`
    ScannerVersion    string `json:"harbor.scanner-adapter/scanner-version"`
}

// Capability 扫描能力声明
type Capability struct {
    ConsumesMimeTypes []string `json:"consumes_mime_types"`
    ProducesMimeTypes []string `json:"produces_mime_types"`
}

// ScannerMetadata 扫描器完整元数据
type ScannerMetadata struct {
    Scanner      ScannerInfo       `json:"scanner"`
    Capabilities []Capability       `json:"capabilities"`
    Properties   ScannerProperties  `json:"properties"`
}

func metadataHandler(w http.ResponseWriter, r *http.Request) {
    metadata := ScannerMetadata{
        Scanner: ScannerInfo{
            Name:    "TianYing",
            Vendor:  "FinTech-Security",
            Version: "2.3.1",
        },
        Capabilities: []Capability{
            {
                // 声明能处理的制品类型——Docker和OCI格式
                ConsumesMimeTypes: []string{
                    "application/vnd.docker.distribution.manifest.v2+json",
                    "application/vnd.oci.image.manifest.v1+json",
                    "application/vnd.docker.distribution.manifest.list.v2+json",
                    "application/vnd.oci.image.index.v1+json",
                },
                // 声明输出的报告格式——Harbor标准漏洞报告v1.0
                ProducesMimeTypes: []string{
                    "application/vnd.scanner.adapter.vuln.report.harbor+json; version=1.0",
                },
            },
        },
        Properties: ScannerProperties{
            HarborScannerType: "os", // 关键：标记为OS层扫描器
            HarborRegistryURL: "http://harbor-core:8080",
            ScannerVersion:    "2.3.1",
        },
    }

    w.Header().Set("Content-Type", "application/vnd.scanner.adapter.scanner.metadata+json; version=1.0")
    w.WriteHeader(http.StatusOK)
    json.NewEncoder(w).Encode(metadata)
}
```

### 3.4 步骤三：实现异步扫描核心（TaskManager）

**目标**：接收`POST /scan`请求后立即返回202+ID，在后台goroutine中执行扫描，并支持通过`GET /report`轮询结果。

```go
// task_manager.go —— 扫描任务调度核心

// ScanRequest Harbor发来的扫描请求
type ScanRequest struct {
    Registry struct {
        URL           string `json:"url"`
        Authorization string `json:"authorization"`
    } `json:"registry"`
    Artifact struct {
        Repository string `json:"repository"`
        Digest     string `json:"digest"`
        Tag        string `json:"tag"`
        MimeType   string `json:"mime_type"`
    } `json:"artifact"`
}

// ScanResponse POST /scan 的立即响应
type ScanResponse struct {
    ID string `json:"id"`
}

// ScanStatus 扫描任务状态
type ScanStatus string

const (
    StatusPending   ScanStatus = "pending"
    StatusRunning   ScanStatus = "running"
    StatusCompleted ScanStatus = "completed"
    StatusFailed    ScanStatus = "failed"
)

// ScanTask 单个扫描任务
type ScanTask struct {
    ID        string
    Status    ScanStatus
    Request   ScanRequest
    Report    *VulnerabilityReport
    Error     string
    CreatedAt time.Time
    DoneCh    chan struct{} // 完成信号
}

// TaskManager 扫描任务管理器
type TaskManager struct {
    mu       sync.RWMutex
    tasks    map[string]*ScanTask
    jobQueue chan *ScanTask
    cfg      *ServerConfig
}

func NewTaskManager(workers int, cfg *ServerConfig) *TaskManager {
    tm := &TaskManager{
        tasks:    make(map[string]*ScanTask),
        jobQueue: make(chan *ScanTask, 100),
        cfg:      cfg,
    }
    // 启动worker goroutine池
    for i := 0; i < workers; i++ {
        go tm.worker(i)
    }
    return tm
}

func (tm *TaskManager) Shutdown() {
    close(tm.jobQueue)
}

// scanHandler POST /api/v1/scan —— 异步接收，立即返回
func (tm *TaskManager) scanHandler(w http.ResponseWriter, r *http.Request) {
    var req ScanRequest
    if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
        http.Error(w, fmt.Sprintf("invalid request body: %v", err), http.StatusBadRequest)
        return
    }

    // 生成唯一请求ID
    taskID := generateTaskID(req)
    task := &ScanTask{
        ID:        taskID,
        Status:    StatusPending,
        Request:   req,
        CreatedAt: time.Now(),
        DoneCh:    make(chan struct{}),
    }

    tm.mu.Lock()
    tm.tasks[taskID] = task
    tm.mu.Unlock()

    // 投递给worker异步处理
    tm.jobQueue <- task

    log.Printf("[INFO] Scan request accepted: id=%s repo=%s digest=%s",
        taskID, req.Artifact.Repository, req.Artifact.Digest)

    w.Header().Set("Content-Type", "application/vnd.scanner.adapter.scan.response+json; version=1.0")
    w.WriteHeader(http.StatusAccepted) // 202 Accepted
    json.NewEncoder(w).Encode(ScanResponse{ID: taskID})
}

// reportHandler GET /api/v1/scan/{request_id}/report —— 轮询扫描结果
func (tm *TaskManager) reportHandler(w http.ResponseWriter, r *http.Request) {
    vars := mux.Vars(r)
    taskID := vars["request_id"]

    tm.mu.RLock()
    task, exists := tm.tasks[taskID]
    tm.mu.RUnlock()

    if !exists {
        http.Error(w, `{"error":"scan request not found"}`, http.StatusNotFound)
        return
    }

    switch task.Status {
    case StatusPending, StatusRunning:
        // 扫描未完成 → 返回302重定向到自身（告诉Harbor继续等待）
        w.Header().Set("Location", r.URL.String())
        w.Header().Set("Retry-After", "5")
        w.WriteHeader(http.StatusFound) // 302
    case StatusCompleted:
        w.Header().Set("Content-Type", "application/vnd.scanner.adapter.vuln.report.harbor+json; version=1.0")
        w.WriteHeader(http.StatusOK)
        json.NewEncoder(w).Encode(task.Report)
    case StatusFailed:
        http.Error(w, fmt.Sprintf(`{"error":"%s"}`, task.Error), http.StatusInternalServerError)
    }
}

// worker 后台扫描执行器
func (tm *TaskManager) worker(id int) {
    log.Printf("[INFO] Scan worker #%d started", id)
    for task := range tm.jobQueue {
        log.Printf("[DEBUG] Worker #%d processing task: %s", id, task.ID)

        tm.mu.Lock()
        task.Status = StatusRunning
        tm.mu.Unlock()

        report, err := tm.executeScan(task)

        tm.mu.Lock()
        if err != nil {
            task.Status = StatusFailed
            task.Error = err.Error()
            log.Printf("[ERROR] Task %s failed: %v", task.ID, err)
        } else {
            task.Status = StatusCompleted
            task.Report = report
            log.Printf("[INFO] Task %s completed: severity=%s vulns=%d",
                task.ID, report.Severity, len(report.Vulnerabilities))
        }
        tm.mu.Unlock()

        close(task.DoneCh)
    }
}

// executeScan 实际执行"天鹰"扫描
func (tm *TaskManager) executeScan(task *ScanTask) (*VulnerabilityReport, error) {
    // 步骤1：预拉取镜像层到本地缓存
    imagePath := fmt.Sprintf("%s/%s", tm.cfg.RegistryCache, task.ID)
    if err := tm.pullImageLayers(task.Request, imagePath); err != nil {
        return nil, fmt.Errorf("pull layers failed: %w", err)
    }

    // 步骤2：调用"天鹰"CLI执行扫描
    ctx, cancel := context.WithTimeout(context.Background(), tm.cfg.ScanTimeout)
    defer cancel()

    rawOutput, err := tm.runScanner(ctx, imagePath)
    if err != nil {
        return nil, fmt.Errorf("scanner execution failed: %w", err)
    }

    // 步骤3：将"天鹰"输出转换为Harbor标准报告格式
    report, err := tm.convertToHarborReport(rawOutput, task.Request)
    if err != nil {
        return nil, fmt.Errorf("report conversion failed: %w", err)
    }

    return report, nil
}

func generateTaskID(req ScanRequest) string {
    data := fmt.Sprintf("%s:%s:%d", req.Artifact.Digest, req.Artifact.Repository, time.Now().UnixNano())
    return fmt.Sprintf("%x", sha256.Sum256([]byte(data)))[:12]
}
```

### 3.5 步骤四：镜像层拉取与Token管理

**目标**：利用Harbor传入的Registry Token，快速拉取所有镜像层到本地，避免Token过期问题。

```go
// image_puller.go —— 镜像层拉取与缓存

func (tm *TaskManager) pullImageLayers(req ScanRequest, destPath string) error {
    registryURL := req.Registry.URL
    token := req.Registry.Authorization
    repo := req.Artifact.Repository
    tag := req.Artifact.Tag
    if tag == "" {
        tag = "latest"
    }

    client := &http.Client{Timeout: 30 * time.Second}

    // 步骤1：获取Manifest（获取所有层的digest列表）
    manifestURL := fmt.Sprintf("%s/v2/%s/manifests/%s", registryURL, repo, tag)
    manifestReq, _ := http.NewRequest("GET", manifestURL, nil)
    manifestReq.Header.Set("Authorization", token)
    manifestReq.Header.Set("Accept", "application/vnd.docker.distribution.manifest.v2+json, "+
        "application/vnd.oci.image.manifest.v1+json")

    resp, err := client.Do(manifestReq)
    if err != nil {
        return fmt.Errorf("fetch manifest failed: %w", err)
    }
    defer resp.Body.Close()

    if resp.StatusCode != http.StatusOK {
        return fmt.Errorf("registry returned %d for manifest: %s", resp.StatusCode, repo)
    }

    var manifest struct {
        Layers []struct {
            Digest string `json:"digest"`
            Size   int64  `json:"size"`
        } `json:"layers"`
    }
    if err := json.NewDecoder(resp.Body).Decode(&manifest); err != nil {
        return fmt.Errorf("decode manifest failed: %w", err)
    }

    log.Printf("[INFO] Pulling %d layers for %s:%s", len(manifest.Layers), repo, tag)

    // 步骤2：快速并行拉取所有层（Token必须在有效期内一次性使用）
    os.MkdirAll(destPath, 0755)

    var wg sync.WaitGroup
    errCh := make(chan error, len(manifest.Layers))

    for _, layer := range manifest.Layers {
        wg.Add(1)
        go func(layerDigest string, layerSize int64) {
            defer wg.Done()
            if err := tm.pullSingleLayer(client, registryURL, repo, layerDigest, token, destPath); err != nil {
                errCh <- fmt.Errorf("pull layer %s: %w", layerDigest[:12], err)
            }
        }(layer.Digest, layer.Size)
    }

    wg.Wait()
    close(errCh)

    // 检查是否有拉取失败
    for e := range errCh {
        if e != nil {
            return e
        }
    }

    log.Printf("[INFO] All layers pulled successfully: %s", destPath)
    return nil
}

func (tm *TaskManager) pullSingleLayer(client *http.Client, registryURL, repo, digest, token, destPath string) error {
    blobURL := fmt.Sprintf("%s/v2/%s/blobs/%s", registryURL, repo, digest)
    req, _ := http.NewRequest("GET", blobURL, nil)
    req.Header.Set("Authorization", token)

    resp, err := client.Do(req)
    if err != nil {
        return err
    }
    defer resp.Body.Close()

    // 写入本地缓存
    layerFile := fmt.Sprintf("%s/%s", destPath, digest[7:]) // 去掉 "sha256:" 前缀
    f, err := os.Create(layerFile)
    if err != nil {
        return err
    }
    defer f.Close()

    _, err = io.Copy(f, resp.Body)
    return err
}
```

### 3.6 步骤五：扫描结果格式转换

**目标**：将"天鹰"引擎的输出转换为Harbor标准的漏洞报告JSON格式。

```go
// report_converter.go —— "天鹰"输出 → Harbor标准报告

// TianYingVuln "天鹰"引擎的原始漏洞输出格式
type TianYingVuln struct {
    VulID       string `json:"vul_id"`
    Severity    string `json:"severity"`
    PackageName string `json:"pkg_name"`
    InstalledVer string `json:"installed_ver"`
    FixedVer    string `json:"fixed_ver"`
    Description string `json:"description"`
    CvssScore   float64 `json:"cvss_score"`
    CvssVector  string `json:"cvss_vector"`
    Links       []string `json:"links"`
}

type TianYingOutput struct {
    ImageName      string          `json:"image_name"`
    ImageDigest    string          `json:"image_digest"`
    ScanTimestamp  string          `json:"scan_timestamp"`
    Vulnerabilities []TianYingVuln `json:"vulnerabilities"`
}

// VulnerabilityReport Harbor标准漏洞报告格式
type VulnerabilityReport struct {
    GeneratedAt     string          `json:"generated_at"`
    Artifact        ArtifactInfo    `json:"artifact"`
    Scanner         ScannerInfo     `json:"scanner"`
    Severity        string          `json:"severity"`
    Vulnerabilities []Vulnerability `json:"vulnerabilities"`
}

type ArtifactInfo struct {
    Repository string `json:"repository"`
    Digest     string `json:"digest"`
    Tag        string `json:"tag"`
    MimeType   string `json:"mime_type"`
}

type ScannerInfo struct {
    Name    string `json:"name"`
    Vendor  string `json:"vendor"`
    Version string `json:"version"`
}

type Vulnerability struct {
    ID          string   `json:"id"`
    Severity    string   `json:"severity"`
    Package     string   `json:"package"`
    Version     string   `json:"version"`
    FixVersion  string   `json:"fix_version"`
    Description string   `json:"description"`
    Links       []string `json:"links"`
    Cvss        *CvssInfo `json:"cvss,omitempty"`
    CweIDs      []string `json:"cwe_ids,omitempty"`
}

type CvssInfo struct {
    Score  float64 `json:"score_v3"`
    Vector string  `json:"vector_v3"`
}

// normalizeSeverity 将"天鹰"自定义严重级别映射为Harbor标准值
var severityMap = map[string]string{
    "严重":    "Critical",
    "高危":    "High",
    "中危":    "Medium",
    "低危":    "Low",
    "忽略":    "Unknown",
    "CRITICAL": "Critical",
    "HIGH":     "High",
    "MEDIUM":   "Medium",
    "LOW":      "Low",
}

func (tm *TaskManager) convertToHarborReport(rawOutput *TianYingOutput, req ScanRequest) (*VulnerabilityReport, error) {
    vulns := make([]Vulnerability, 0, len(rawOutput.Vulnerabilities))
    maxSeverity := "Unknown"

    for _, tv := range rawOutput.Vulnerabilities {
        harborSev, ok := severityMap[tv.Severity]
        if !ok {
            harborSev = "Unknown" // 未知级别映射为Unknown，防止Harbor解析失败
        }

        // 追踪最高严重级别
        if sevLevel(harborSev) > sevLevel(maxSeverity) {
            maxSeverity = harborSev
        }

        vulns = append(vulns, Vulnerability{
            ID:          tv.VulID,
            Severity:    harborSev,
            Package:     tv.PackageName,
            Version:     tv.InstalledVer,
            FixVersion:  tv.FixedVer,
            Description: tv.Description,
            Links:       tv.Links,
            Cvss: &CvssInfo{
                Score:  tv.CvssScore,
                Vector: tv.CvssVector,
            },
        })
    }

    return &VulnerabilityReport{
        GeneratedAt: rawOutput.ScanTimestamp,
        Artifact: ArtifactInfo{
            Repository: req.Artifact.Repository,
            Digest:     req.Artifact.Digest,
            Tag:        req.Artifact.Tag,
            MimeType:   req.Artifact.MimeType,
        },
        Scanner: ScannerInfo{
            Name:    "TianYing",
            Vendor:  "FinTech-Security",
            Version: "2.3.1",
        },
        Severity:        maxSeverity,
        Vulnerabilities: vulns,
    }, nil
}

// sevLevel 严重级别数值化（用于比较）
func sevLevel(s string) int {
    switch s {
    case "Critical":
        return 4
    case "High":
        return 3
    case "Medium":
        return 2
    case "Low":
        return 1
    default:
        return 0
    }
}
```

### 3.7 步骤六：注册到Harbor并验证

**目标**：将Adapter注册为Harbor扫描器并分配给项目。

```bash
# 1. 注册扫描器 —— 向Harbor API声明Adapter的存在
curl -s -X POST -u admin:Harbor12345 \
  -H "Content-Type: application/json" \
  -d '{
    "name": "TianYing",
    "url": "http://scanner-adapter:8080",
    "auth": "",
    "access_credential": "",
    "skip_cert_verify": true,
    "use_internal_addr": false,
    "description": "FinTech internal vulnerability scanner"
  }' \
  "https://harbor.company.com/api/v2.0/scanners"

# 2. 验证metadata —— 确认Harbor能正确读取扫描器能力
curl -s "http://scanner-adapter:8080/api/v1/metadata" | jq .

# 3. 将扫描器设为项目默认——之后所有push自动触发扫描
SCANNER_UUID=$(curl -s -u admin:Harbor12345 \
  "https://harbor.company.com/api/v2.0/scanners" | jq -r '.[] | select(.name=="TianYing") | .uuid')

curl -s -X PUT -u admin:Harbor12345 \
  -H "Content-Type: application/json" \
  -d "{\"uuid\":\"$SCANNER_UUID\"}" \
  "https://harbor.company.com/api/v2.0/projects/order-platform/scanner"

echo "[OK] Scanner TianYing configured for project 'order-platform'"
```

### 3.8 常见陷阱与解决方案

**陷阱一：`scan`端点同步返回导致Harbor超时**

*现象*：Harbor日志频繁出现`scanner job timeout`，扫描任务状态始终为`Error`。

*根因*：Harbor对`POST /scan`有隐含的超时预期（约30秒），如果你的Adapter在`scan`处理函数中完成全部扫描工作再返回，Harbor会认为扫描器无响应。

*解决方案*：`scan`端点必须立即返回`202 Accepted` + `{id}`，扫描逻辑放到goroutine中异步执行。返回前只需要验证请求合法性，不需要做任何IO密集型操作。

**陷阱二：`harbor.scanner-adapter/scanner-type`缺失或值错误**

*现象*：扫描器注册成功、health check通过，但无法在Harbor Portal中将该扫描器设为项目默认。UI上扫描器列表为空或置灰。

*根因*：Harbor Core在加载扫描器列表时，会解析`metadata`中的`properties`字段，只有当`harbor.scanner-adapter/scanner-type`的值是`os`或`pluggable`时，才允许设为默认扫描器。值写法不正确或缺失会导致过滤逻辑跳过。

*解决方案*：确保`metadata`响应中`properties`包含：
```json
"properties": {
    "harbor.scanner-adapter/scanner-type": "os"
}
```

**陷阱三：漏洞Severity非标准值导致CVE策略失效**

*现象*：Harbor Portal显示扫描报告正常，但配置了"阻止High及以上CVE拉取"策略后，包含严重漏洞的镜像依然可以被拉取。

*根因*：Harbor的CVE阻止策略依赖`severity`字段与策略阈值比较。如果扫描器输出的severity使用了非标准值（如中文"高危"、"严重"），Harbor内部`severityLevel()`函数无法解析，默认返回`0`（即Unknown级别），导致策略永远不触发。

*解决方案*：在`report`接口返回前，将扫描器内部severity映射为Harbor标准值：`Unknown`/`Low`/`Medium`/`High`/`Critical`，参考上方`normalizeSeverity`实现。

**陷阱四：并发扫描导致Registry Token冲突**

*现象*：同一项目多个镜像同时触发扫描，部分扫描任务报`401 Unauthorized`，但Token尚未过期。

*根因*：Harbor为每个扫描请求生成独立的临时Token，Token绑定到特定的`artifact`。如果Adapter实现中将Token缓存并跨任务共享，可能使用了错误的Token拉取镜像。

*解决方案*：每个`ScanTask`独立持有自己的`Authorization` Token，不要在Adapter全局缓存或共享Token。

---

## 4 项目总结

### 4.1 架构对比：直接调用 vs Scanner Adapter

| 维度 | 直接调用扫描器CLI | Scanner Adapter协议 |
|------|------------------|-------------------|
| 集成方式 | Harbor源码硬编码调用 | 标准化HTTP API，热插拔 |
| 报告格式 | 各扫描器私有格式 | 统一Harbor标准JSON |
| 异步处理 | 需要自行实现轮询/回调 | 协议已定义202+302轮询 |
| 认证授权 | 自行管理Registry凭证 | Harbor自动传递临时Token |
| 多扫描器支持 | 代码级耦合 | 通过API注册，可并存多个 |
| Portal展示 | 需定制UI | 原生支持报告展示 |

### 4.2 适用场景

1. **企业内部自研扫描引擎接入**——通过Adapter包装，无需修改Harbor源码即可集成私有扫描能力。
2. **多引擎协同扫描**——同时注册Trivy+Clair+Snyk+自研引擎，在项目级别按需选择。
3. **扫描能力灰度发布**——开发新版本扫描器时，先在测试项目验证，再逐步切换到生产项目。
4. **扫描器供应商切换**——从Clair切换到Trivy时，只需注册新Adapter并修改项目默认扫描器，历史报告保留。
5. **合规审计场景**——金融/医疗行业需要特定合规扫描器（如等保扫描器），通过Adapter快速接入。

### 4.3 不适用场景

1. **需要实时阻断push流量的扫描**——Harbor的扫描是异步的，push完成后才扫描，无法在push过程中拦截。如需同步拦截，应考虑Docker Registry的`middleware`层。
2. **纯Windows容器镜像扫描**——当前Scanner Adapter协议主要面向Linux容器镜像，Windows镜像层格式差异较大，社区支持尚不完整。

### 4.4 注意事项

1. **Adapter必须是高可用的**：如果扫描器挂了，所有镜像的扫描请求都会失败（Harbor有重试机制但上限3次）。
2. **令牌时效性**：`scan`请求中的`Authorization` Token默认30分钟有效，Adapter应尽快完成镜像拉取，过期后需重新触发扫描。
3. **报告大小控制**：单份扫描报告JSON超过50MB时，Harbor Core的内存和PostgreSQL存储都会受压力——建议Adapter对超过1000个漏洞的报告做分页或截断。
4. **定期清理本地缓存**：Adapter拉取镜像层后缓存到本地磁盘，长期运行后磁盘可能被占满——建议实现LRU淘汰或定时清理机制。
5. **MIME type精确匹配**：`produces_mime_types`必须完全符合Harbor规范（包括`version=1.0`后缀），一个字符的差异都会导致Harbor无法解析报告。

### 4.5 常见陷阱速查表

| 陷阱 | 现象 | 根因 | 解决 |
|------|------|------|------|
| scan同步返回 | 任务超时变Error | 未立即返回202+ID | goroutine异步执行 |
| scanner-type缺失 | 无法设为默认扫描器 | properties缺关键字段 | 添加`harbor.scanner-adapter/scanner-type: "os"` |
| severity非标准 | CVE策略不生效 | 中文/自定义级别无法解析 | 映射为标准5级severity |
| Token共享 | 401 Unauthorized | 跨任务共用Token | 每个task独立持有Token |
| MIME type错误 | 报告无法解析 | produces_mime_types不匹配 | 严格使用Harbor规范格式 |

### 4.6 深度思考

**问题一**：如果要设计一个"聚合扫描适配器"——对外表现为一个扫描器，内部调度Trivy（OS层）和Snyk（应用层）两个子扫描器，并将两份报告合并为一份——合并策略应该如何设计？当两个子扫描器对同一个CVE给出不同的Severity时（Trivy判High、Snyk判Medium），取Max、Min还是保留两个？

**问题二**：Harbor的扫描器注册API（`POST /api/v2.0/scanners`）中有一个`skip_cert_verify`参数。如果攻击者通过中间人攻击伪造了一个扫描器，Harbor Core会信任该伪造扫描器的报告吗？安全设计上是否需要扫描器提供签名或认证机制？

---

> 下一章预告：第37章将深度剖析Harbor复制引擎的源码实现。
