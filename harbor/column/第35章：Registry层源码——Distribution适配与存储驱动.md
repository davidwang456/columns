# 第35章：Registry 层源码——Distribution 适配与存储驱动

## 1 项目背景

某视频流媒体公司基于Harbor构建了多地域镜像分发体系——北京、上海、深圳三个机房各部署一套Harbor实例，底层共享一套自研的统一存储系统（代号"Everest"）。Everest兼容S3 API但在认证层面有两个特殊需求：第一，所有S3请求必须携带`X-Everest-Project-ID`头部（用于存储层的租户隔离和计费）；第二，上传大文件（>100MB）时必须使用Everest专属的多点分片上传优化接口（一次调用完成全部multipart upload的initialization、分片签名生成、completion），而非标准S3三步式流程（CreateMultipartUpload → UploadPart × N → CompleteMultipartUpload）以减少HTTP往返次数。

开发团队在对Harbor的Registry进行存储适配时，遇到了四个核心痛点：

**痛点一：CNCF Distribution的存储驱动接口设计哲学不透明。** `StorageDriver`接口定义看似简单（6个方法），但实际开发中团队发现这6个方法的调用频率和调用时机在完全不同的代码路径上——`GetContent`在主路径（pull镜像）上，要求低延迟、高并发；`Writer`在推送大文件（push镜像）上，要求流式写入和分片控制；`List`只在GC的mark阶段被触发——一次GC会调用数万次，但不需要低延迟。这些调用模式的差异意味着一个"一刀切"的存储驱动实现在性能上必然存在短板——但团队不清楚如何为不同方法定制不同的连接池配置。

**痛点二：Blob分块上传的底层实现与参数调优。** 推送一个多层镜像（如14层共2.8GB），Registry会为每一层执行独立的Blob上传流程：`POST /v2/.../blobs/uploads/`（创建会话）→ `PATCH`×N次（分块传输）→ `PUT`（完成确认）。团队发现当网络延迟为80ms时，默认的chunk_size（1MB）导致单个2GB layer需要执行2000次PATCH请求——仅HTTP往返开销就占用了160秒。但直接增大chunk_size到100MB又会导致单次PATCH超时概率增大。团队需要理解分块上传的配置参数在源码中的流转路径。

**痛点三：自定义存储驱动的错误传播机制。** 在测试自定义Everest驱动时，团队发现：当存储后端返回`503 Service Unavailable`时，Registry客户端收到的是通用的`500 Internal Server Error`——原始的503和Everest的`X-Request-ID`（用于后端排查的trace ID）在传播过程中丢失了。这导致运维无法快速定位是Registry的问题还是Everest的问题。团队需要理解Distribution框架中错误是如何从存储驱动层穿透HTTP Handler层到达客户端的过程。

**痛点四：Registry与Core的认证集成机制。** 团队的自研存储系统Everest使用Harbor Core签发的JWT作为存储层认证凭据。但JWT的有效期是15分钟——一个传输10GB镜像的操作可能持续20分钟。传输到一半时JWT过期，后续的PATCH请求被Everest拒绝——但Registry并不知道JWT已过期，一直重试直至Worker超时。团队需要理解Registry如何在Blob上传的整个生命周期中管理认证凭据的刷新。

---

## 2 项目设计——剧本式交锋对话

**第一回合：从"Registry到底是什么"开始**

**小胖**（盯着CNCF Distribution的GitHub仓库发懵）："大师，我一直以为Registry是Docker官方写的一整个项目——结果你告诉我Harbor的Registry是fork的CNCF Distribution？这跟Docker Hub用的registry是同一个东西吗？为什么Harbor不自己写一个，非要fork别人的？像我们公司技术总监说的——'不要重复造轮子'？"

**大师**（打开CNCF Distribution的架构图）:"小胖，你这个问题问得好。CNCF Distribution（原Docker Distribution）是OCI规范的标准实现——它定义了镜像如何被存储、寻址、传输。全球绝大多数镜像仓库（Docker Hub、GitHub Container Registry、GCR、ECR）都在不同程度上基于或兼容这套协议。Harbor选择基于Distribution而不是自研，不是因为懒——而是因为**镜像分发的互操作性**。你的开发团队用`docker pull`命令时，Docker CLI期望Registry严格遵循OCI Distribution Spec——如果你自研，你必须逐行实现200多页的规范文档，包括challenge-response认证、分块上传、内容寻址等复杂协议。Harbor的策略是在Distribution的稳定基础上，通过自定义的**Token鉴权**、**存储配额检查**和**GC协作**这三层'插件'来满足企业级需求——这其实就是设计模式里的**装饰器模式**：内核不变，能力增强。"

**小白**（快速翻阅`vendor/github.com/distribution/`）:"那我理解了——但我在看Distribution的Handler代码时发现一个奇怪的设计：`blob.go`中处理GET请求（下载Blob）和POST请求（上传Blob）用的是同一个函数`BlobDispatcher`——这跟典型的Go Web Handler（一个路径一个Handler函数）不太一样。为什么这么设计？"

**大师**:"这是Distribution对OCI Spec中'Blob Upload Session'协议的忠实映射。在OCI规范中，`/v2/<name>/blobs/uploads/`这个路径根据HTTP方法的不同有完全不同的语义——POST创建上传会话、GET列出正在进行的会话、PATCH上传分块、PUT完成上传、DELETE取消上传。5种操作共享同一个URL前缀但语义完全不同。用单独的Handler函数处理每个方法会让路由表爆炸，而且会让'同一个资源'的概念被割裂。`BlobDispatcher`内部做方法分发，类似一个mini-Router——这是一种**按资源聚合而非按操作拆分**的API设计哲学。"

---

**第二回合：深入存储驱动接口**

**小胖**（看`storagedriver.go`接口定义）:"大师，这个接口也太简单了——`GetContent`、`PutContent`、`Writer`、`Stat`、`Delete`、`List`，就这6个方法？但我想不通——`GetContent`返回`[]byte`，那读一个10GB的layer时是不是要把整个文件加载到内存？Go的内存不直接爆了？"

**大师**:"你看到了一个非常好的问题。`GetContent`的`[]byte`返回值确实危险——但Distribution在实践中**从不对大Blob使用`GetContent`**。查看源码你会发现：在`blob.go`的下载路径中，对于请求`GET /v2/<name>/blobs/<digest>`，Registry调用的不是`GetContent`，而是`driver.Reader(ctx, path, 0)`——这个方法返回一个`io.ReadCloser`，然后通过`http.ServeContent`流式传输给客户端。`GetContent`只在处理Manifest（通常几KB到几十KB）和配置Blob（通常几KB）时使用。这是一个'接口提供能力全集，调用方按需选取'的经典案例。"

**小白**:"那`Writer`方法的`append`参数是什么意思？分块上传时为什么需要append模式？"

**大师**（在白板上画出Blob上传的时序）:"这是分块上传的核心。回忆之前的时序图——客户端发送多次`PATCH`请求，每次携带一个chunk。Registry的处理流程是：第一次PATCH时`Writer(path, append=false)`创建一个新的FileWriter；后续PATCH时`Writer(path, append=true)`打开已有的FileWriter并追加数据。在S3驱动的底层，`append=false`对应`CreateMultipartUpload`，`append=true`对应`UploadPart`。这个`append`参数的语义是：false=覆盖写入（创建新会话），true=追加写入（继续已有会话）。如果不理解这个差异，你可能会在每次PATCH时创建一个新的multipart upload——导致最终有N个未完成的multipart upload被废弃，产生大量的存储孤儿数据。"

---

**第三回合：从标准S3到自定义Everest驱动的实战**

**小胖**（苦恼地）:"大师！我照着S3驱动抄了一个Everest驱动——能用`GetContent`读取manifest了，但一旦`docker push`超过10MB的layer，就报'write /docker/registry/v2/blobs/... : broker: EOF'。我看日志发现，`Writer`正常打开了，`Write`也调用了3次（说明客户端发了3个PATCH），但到`Commit`就报错。我用mitmproxy抓包发现——我的自定义驱动在`Commit`时调用的是`CompleteMultipartUpload`，但Everest的API文档说它的多点分片优化接口是`POST /_everest/fast_upload`，不接受AWS标准的`CompleteMultipartUpload`。我这应该怎么改？"

**大师**:"这是个很典型的问题——表现了'API兼容但语义不兼容'的陷阱。你的问题表面上是API不对，根因是**你对StorageDriver接口的理解跳了一层**。写一个存储驱动，不是简单地把S3驱动的函数名替换成Everest的API名。你需要理解每个Driver方法的**语义**而不是**实现**。`FileWriter.Commit()`的语义是：'我已经写完了所有数据，请将这些数据持久化并使其可被后续的`GetContent`访问'。在S3上，这等于`CompleteMultipartUpload`；但在Everest上，你应该在`FileWriter.Write()`方法中累积所有chunk，在`Commit()`中调用`POST /_everest/fast_upload`一次性提交。具体来说，你的自定义`everestWriter`需要一个内存buffer或临时文件来积累所有`Write()`的数据块。"

**小白**（若有所思）:"等等，按你的说法，那如果客户端上传一个10GB的layer，`FileWriter.Write()`会被调用几千次。全部buffer在内存里不是爆炸了吗？你是说用临时文件吗？"

**大师**:"完全正确。对于Everest的快速上传接口，你需要用临时文件来缓冲数据。但有个细节——你不能简单地把所有PATCH数据都追加到临时文件然后一次性发送。Everest的多点分片优化接口的HTTP body大小限制是5GB——超过5GB的镜像layer，你需要拆分成多个fast_upload请求。这就是为什么阅读源码中的`filesystem/driver.go`很重要——它展示了如何使用`os.File`作为Writer的底层存储，`Commit()`只需要一次`Rename`（原子操作）。对于Everest，你的`Commit()`应该读取临时文件并以流式方式发送到Everest的fast_upload endpoint。一个优化方案是：如果临时文件>5GB，降级到标准S3的multipart upload流程——这就是所谓的**自适应存储策略**。"

---

**第四回合：JWT过期与长连接管理**

**小胖**（突然想起）:"大师，还有个问题！我们用JWT做存储层认证——但推送大镜像时JWT会过期，导致后半段上传都失败。怎么解决？如果我直接延长JWT有效期到2小时——安全团队肯定不同意吧？"

**大师**:"这是个经典的'长期运行任务中的短期凭据'问题。解决思路不是延长JWT有效期，而是**在任务进行中续约凭据**。具体方案：在你的自定义Everest驱动中，不要固化JWT——而是在每次调用Everest API时动态获取。你可以在驱动初始化时传入一个`TokenProvider`接口（返回JWT的闭包），而不是传入一个静态的JWT字符串。每次`Writer.Write()`和`Commit()`调用时，调用`TokenProvider.Fetch()`获取最新的JWT。TokenProvider内部维护一个带过期缓冲的缓存——在JWT过期前5分钟自动向Core请求新的JWT。"

**小白**:"那如果我的`TokenProvider.Fetch()`调用失败了呢（比如Core暂时不可达）？writer的`Write()`应该返回error让框架重试，还是缓存在本地等Core恢复？"

**大师**:"取决于你的业务容忍度。推荐方案是：`TokenProvider.Fetch()`失败时，如果当前JWT还剩超过2分钟的寿命，则使用缓存的JWT继续；如果不足2分钟，返回一个特殊的`ErrTokenExpiring`错误。你的`everestWriter.Write()`捕获这个错误后，暂停写入、等待一个backoff后重试`Fetch()`。最关键的是——**不要在30分钟的镜像推送中的第29分钟因为无法续约Token而丢弃所有进度**。一个折中是：在Writer内部预留一个5分钟的'宽限期'，当Token临近过期时主动暂停上传、续约Token、然后从断点续传。"

---

## 3 项目实战

### 3.1 环境要求

| 组件 | 版本要求 | 用途 |
|------|---------|------|
| Go | ≥1.20 | 编译harbor-registry二进制 |
| Harbor Registry (vendor内) | CNCF Distribution v2.8+ Harbor定制版 | Registry主程序框架 |
| AWS SDK for Go | v1.x (vendor内) | S3兼容驱动的AWS SDK依赖 |
| Harbor Core | v2.8+ | 提供Token签发、配额回调 |
| 自研存储 (Everest) | 提供S3兼容API + `/_everest/fast_upload` | 自定义驱动的目标存储后端 |
| Docker CLI | ≥20.10 | 推送/拉取功能验证 |
| 测试脚本 | curl + jq + mitmproxy | API调试与流量抓包验证 |

### 3.2 步骤一：深入理解StorageDriver接口与调用链路

**目标**：追踪一次`docker push`如何从HTTP Handler层穿透到存储驱动层。

```go
// ========== 文件: vendor/github.com/distribution/registry/storage/driver/storagedriver.go ==========
// CNCF Distribution定义的存储驱动核心接口 — 所有存储后端必须实现这6个方法

// StorageDriver 定义了Registry与任何存储后端交互的抽象接口
// 设计理念: 接口面向"Registry需要的存储能力", 而非"特定存储系统的API"
type StorageDriver interface {
    // GetContent 获取文件的完整内容
    // ⚠️ 仅用于小文件(<1MB)如Manifest/Config, 大文件使用Reader方法
    GetContent(ctx context.Context, path string) ([]byte, error)

    // PutContent 写入文件完整内容(覆盖写入)
    // 用于写入Manifest和Config Blob — 这些对象不可变, 允许覆盖是为了处理并发竞态
    PutContent(ctx context.Context, path string, content []byte) error

    // Writer 返回一个FileWriter, 用于流式写入大文件
    // append=false: 创建新的写入会话 (对应Blob上传开始的POST)
    // append=true:  续写已有会话 (对应后续的PATCH)
    Writer(ctx context.Context, path string, append bool) (FileWriter, error)

    // Stat 获取文件元信息
    // 返回FileInfo: Path/Size/ModTime/IsDir
    Stat(ctx context.Context, path string) (FileInfo, error)

    // Delete 删除文件 (Blob删除 / GC清理)
    // 一级删除: 从file deletion table移除引用, 标记为可GC
    // 二级删除: GC worker调用此方法物理移除存储后端上的数据
    Delete(ctx context.Context, path string) error

    // List 列出指定路径下的所有文件和子目录 (GC mark阶段核心方法)
    // 一次GC会在blob路径上调用此方法数万次, 需要实现高效的扫描
    List(ctx context.Context, path string) ([]string, error)
}

// FileWriter 流式写入器接口 — Writer方法返回的对象
type FileWriter interface {
    // Write 写入数据块(分片)
    // 单次Write的大小由客户端PATCH请求的Content-Length决定
    // 多次Write调用之间的顺序和边界必须被严格遵守
    Write(p []byte) (int, error)

    // Close 关闭写入器, 释放底层资源
    Close() error

    // Commit 提交写入 — 将所有Write的数据持久化到存储后端
    // 调用时机: 客户端发送 PUT /v2/.../blobs/uploads/<uuid>?digest=sha256:xxx
    // Commit成功后, 该Blob可通过 GetContent/Reader 访问
    Commit() error

    // Cancel 取消写入 — 丢弃所有未提交的数据
    // 调用时机: 客户端发送 DELETE /v2/.../blobs/uploads/<uuid>
    Cancel() error
}
```

```go
// ========== 文件: vendor/github.com/distribution/registry/handlers/blob.go ==========
// BlobHandler 展示了从HTTP请求到StorageDriver调用的完整链路

func (bh *blobHandler) PutBlobUploadComplete(w http.ResponseWriter, r *http.Request) {
    // Step 1: 从URL中提取upload UUID和digest
    uuid := mux.Vars(r)["uuid"]
    digest, _ := digest.Parse(mux.Vars(r)["digest"])

    // Step 2: 从Service层获取对应的Blob Upload对象
    // upload对象内部持有了 StorageDriver.Writer() 返回的FileWriter
    upload, err := bh.BlobProvider.Upload(r.Context(), bh.Repository.Name(), uuid)
    if err != nil {
        bh.Errors.Push(v2.ErrorCodeBlobUploadUnknown, err)
        return
    }

    // Step 3: 调用FileWriter.Commit() — 将分块数据持久化
    // 这个方法内部会调用不同存储驱动的Commit实现:
    //   - filesystem驱动: os.Rename(tempFile, finalPath)
    //   - s3驱动:         s3.CompleteMultipartUpload(UploadId, Parts)
    //   - everest驱动:    http.Post("/_everest/fast_upload", body)
    desc, err := upload.Commit(r.Context(), digest)
    if err != nil {
        bh.Errors.Push(v2.ErrorCodeBlobUploadInvalid, err)
        return
    }

    // Step 4: 返回201 Created + 最终Blob的Location
    w.Header().Set("Docker-Content-Digest", desc.Digest.String())
    w.Header().Set("Location", bh.relativeURL("blobs", desc.Digest.String()))
    w.WriteHeader(http.StatusCreated)
}
```

### 3.3 步骤二：实现Everest自研存储驱动

**目标**：编写一个完整的存储驱动，支持Everest的`X-Everest-Project-ID`认证头和`/_everest/fast_upload`多点分片上传优化。

```go
// 文件: src/registry/storage/driver/everest/everest.go
package everest

import (
    "bytes"
    "context"
    "fmt"
    "io"
    "net/http"
    "os"
    "path/filepath"
    "time"

    "github.com/docker/distribution/registry/storage/driver"
    "github.com/docker/distribution/registry/storage/driver/base"
)

// 注册驱动到全局工厂 — init()在包加载时自动注册
// Registry启动时通过配置中的 storage.everest 段加载此驱动
func init() {
    driver.Register("everest", &driverFactory{})
}

type driverFactory struct{}

// Create 根据配置参数创建Everest存储驱动实例
func (f *driverFactory) Create(parameters map[string]interface{}) (driver.StorageDriver, error) {
    endpoint, ok := parameters["endpoint"].(string)
    if !ok || endpoint == "" {
        return nil, fmt.Errorf("everest: endpoint parameter is required")
    }
    projectID, ok := parameters["project_id"].(string)
    if !ok || projectID == "" {
        return nil, fmt.Errorf("everest: project_id parameter is required")
    }
    tokenProvider, ok := parameters["token_provider"].(string)
    if !ok {
        return nil, fmt.Errorf("everest: token_provider parameter is required")
    }

    rootDir := "/"
    if rd, ok := parameters["rootdirectory"].(string); ok {
        rootDir = rd
    }

    d := &everestDriver{
        endpoint:      endpoint,
        projectID:     projectID,
        tokenProvider: tokenProvider,
        rootDir:       rootDir,
        client: &http.Client{
            Timeout: 5 * time.Minute, // 长超时支持大文件传输
            Transport: &http.Transport{
                MaxIdleConns:        100,
                MaxIdleConnsPerHost: 20,
                IdleConnTimeout:     90 * time.Second,
            },
        },
    }
    return d, nil
}

// everestDriver 实现 StorageDriver 接口的自定义驱动
type everestDriver struct {
    endpoint      string
    projectID     string
    tokenProvider string
    rootDir       string
    client        *http.Client
}

// GetContent 读取小文件内容 — 用于Manifest和Config Blob
func (d *everestDriver) GetContent(ctx context.Context, path string) ([]byte, error) {
    fullPath := d.pathToKey(path)
    req, err := http.NewRequestWithContext(ctx, "GET", d.endpoint+"/objects/"+fullPath, nil)
    if err != nil {
        return nil, err
    }
    d.setAuthHeaders(req)

    resp, err := d.client.Do(req)
    if err != nil {
        return nil, fmt.Errorf("everest get %s: %w", path, err)
    }
    defer resp.Body.Close()

    if resp.StatusCode == http.StatusNotFound {
        return nil, driver.PathNotFoundError{Path: path}
    }
    if resp.StatusCode >= 400 {
        return nil, fmt.Errorf("everest returned HTTP %d for %s", resp.StatusCode, path)
    }

    return io.ReadAll(resp.Body)
}

// PutContent 写入小文件 — 覆盖写入
func (d *everestDriver) PutContent(ctx context.Context, path string, content []byte) error {
    fullPath := d.pathToKey(path)
    req, err := http.NewRequestWithContext(ctx, "PUT", d.endpoint+"/objects/"+fullPath, bytes.NewReader(content))
    if err != nil {
        return err
    }
    d.setAuthHeaders(req)
    req.Header.Set("Content-Type", "application/octet-stream")

    resp, err := d.client.Do(req)
    if err != nil {
        return fmt.Errorf("everest put %s: %w", path, err)
    }
    resp.Body.Close()
    if resp.StatusCode >= 400 {
        return fmt.Errorf("everest returned HTTP %d for %s", resp.StatusCode, path)
    }
    return nil
}

// Writer 创建流式写入器 — Blob分块上传的入口
func (d *everestDriver) Writer(ctx context.Context, path string, append bool) (driver.FileWriter, error) {
    fullPath := d.pathToKey(path)

    if append {
        // 追加模式 — 打开已有的写入器继续添加数据
        // 此处简化处理: 在实际实现中, 需要维护一个 uuid → writer 的映射表
        return &everestWriter{
            driver:   d,
            path:     fullPath,
            append:   true,
            buffer:   &bytes.Buffer{},
        }, nil
    }

    // 创建模式 — 初始化一个新的写入会话
    return &everestWriter{
        driver:   d,
        path:     fullPath,
        append:   false,
        buffer:   &bytes.Buffer{},
    }, nil
}

// Stat 获取文件元信息
func (d *everestDriver) Stat(ctx context.Context, path string) (driver.FileInfo, error) {
    fullPath := d.pathToKey(path)
    req, err := http.NewRequestWithContext(ctx, "HEAD", d.endpoint+"/objects/"+fullPath, nil)
    if err != nil {
        return nil, err
    }
    d.setAuthHeaders(req)

    resp, err := d.client.Do(req)
    if err != nil {
        return nil, err
    }
    resp.Body.Close()
    if resp.StatusCode == http.StatusNotFound {
        return nil, driver.PathNotFoundError{Path: path}
    }
    return driver.FileInfoInternal{
        FileInfoFields: driver.FileInfoFields{
            Path:  path,
            IsDir: resp.Header.Get("X-Everest-Type") == "directory",
        },
    }, nil
}

// Delete 删除文件 — GC回收时调用
func (d *everestDriver) Delete(ctx context.Context, path string) error {
    fullPath := d.pathToKey(path)
    req, err := http.NewRequestWithContext(ctx, "DELETE", d.endpoint+"/objects/"+fullPath, nil)
    if err != nil {
        return err
    }
    d.setAuthHeaders(req)
    resp, err := d.client.Do(req)
    if err != nil {
        return err
    }
    resp.Body.Close()
    return nil
}

// List 列出路径下的所有文件和子目录 — GC mark阶段密集调用
// ⚠️ 性能关键: GC每次会调用数千次, 必须高效实现
func (d *everestDriver) List(ctx context.Context, path string) ([]string, error) {
    fullPath := d.pathToKey(path)
    req, err := http.NewRequestWithContext(
        ctx, "GET",
        d.endpoint+"/objects?prefix="+fullPath+"&delimiter=/",
        nil,
    )
    if err != nil {
        return nil, err
    }
    d.setAuthHeaders(req)

    resp, err := d.client.Do(req)
    if err != nil {
        return nil, fmt.Errorf("everest list %s: %w", path, err)
    }
    defer resp.Body.Close()

    // 解析Everest的list响应 (Simplified假设返回JSON数组)
    var result struct {
        Keys []string `json:"keys"`
    }
    if err := json.NewDecoder(resp.Body).Decode(&result); err != nil {
        return nil, err
    }
    return result.Keys, nil
}

// setAuthHeaders 设置Everest要求的自定义认证头部
// 每次API调用前动态注入, 支持JWT动态续约
func (d *everestDriver) setAuthHeaders(req *http.Request) {
    req.Header.Set("X-Everest-Project-ID", d.projectID)
    req.Header.Set("Authorization", "Bearer "+d.tokenProvider) // 生产环境应使用TokenProvider动态获取
}

// pathToKey 将Distribution内部路径转换为Everest存储的Object Key
func (d *everestDriver) pathToKey(path string) string {
    return filepath.Join(d.rootDir, path)
}

// ========== everestWriter — 实现 FileWriter 接口 ==========
type everestWriter struct {
    driver *everestDriver
    path   string
    append bool
    buffer *bytes.Buffer // 简化: 生产环境应使用临时文件替代, 避免大文件OOM
    closed bool
}

func (w *everestWriter) Write(p []byte) (int, error) {
    if w.closed {
        return 0, fmt.Errorf("writer already closed")
    }
    return w.buffer.Write(p)
}

func (w *everestWriter) Close() error {
    if w.closed {
        return nil
    }
    w.closed = true
    return nil
}

// Commit 提交所有Write的数据到Everest存储
// 使用Everest专属的 /_everest/fast_upload 端点 — 一次请求完成上传
func (w *everestWriter) Commit() error {
    if err := w.Close(); err != nil {
        return err
    }

    // ⚠️ 生产环境优化: 对于超过5GB的buffer, 自动降级为标准S3 Multipart
    if w.buffer.Len() > 5*1024*1024*1024 {
        return w.commitMultipartFallback()
    }

    req, err := http.NewRequest(
        "POST",
        w.driver.endpoint+"/_everest/fast_upload?key="+w.path,
        w.buffer,
    )
    if err != nil {
        return fmt.Errorf("create fast_upload request: %w", err)
    }
    w.driver.setAuthHeaders(req)
    req.Header.Set("Content-Type", "application/octet-stream")
    req.Header.Set("X-Everest-Upload-Mode", "single")

    resp, err := w.driver.client.Do(req)
    if err != nil {
        return fmt.Errorf("everest fast_upload %s: %w", w.path, err)
    }
    defer resp.Body.Close()

    if resp.StatusCode >= 400 {
        body, _ := io.ReadAll(resp.Body)
        return fmt.Errorf("everest fast_upload failed: HTTP %d, body: %s", resp.StatusCode, string(body))
    }

    return nil
}

// commitMultipartFallback 大文件降级为标准S3 Multipart Upload
func (w *everestWriter) commitMultipartFallback() error {
    // 分块上传逻辑: CreateMultipart → UploadParts → CompleteMultipart
    // 此方法在处理超大镜像层(>5GB)时自动触发
    return fmt.Errorf("multipart fallback not implemented")
}

func (w *everestWriter) Cancel() error {
    w.buffer.Reset()
    return w.Close()
}
```

### 3.4 步骤三：追踪docker push时的全链路日志

**目标**：通过Registry日志理解Blob分块上传的每一步底层调用。

```bash
# Step 1: 开启Registry的Debug级别日志
docker exec harbor-registry sh -c "\
  sed -i 's/level: info/level: debug/' /etc/docker/registry/config.yml
"
docker restart harbor-registry

# Step 2: 以低chunk_size推送镜像，触发大量PATCH请求以便观察
# 在Registry配置中设置分块大小 (默认10MB → 改为1MB触发更频繁的分块)
# registry/config.yml:
#   storage:
#     maintenance:
#       uploadpurging:
#         enabled: true
#         age: 168h
#     blobstore:
#       chunksize: 1048576  # 1MB

# Step 3: 执行docker push并实时跟踪日志
# Terminal 1: 跟踪Registry日志
docker logs -f harbor-registry 2>&1 | grep -E "blob|upload|PUT|PATCH|POST"

# Terminal 2: 推送自制的多层镜像
docker build -t harbor.company.com/test/multi-layer:v1 .
docker push harbor.company.com/test/multi-layer:v1

# 预期日志输出（注释版）:
# POST /v2/test/multi-layer/blobs/uploads/           ← 创建上传会话, 获得UUID
# PATCH /v2/test/multi-layer/blobs/uploads/<uuid1>  ← Layer 1 chunk 0
# PATCH /v2/test/multi-layer/blobs/uploads/<uuid1>  ← Layer 1 chunk 1
# PUT /v2/test/multi-layer/blobs/uploads/<uuid1>?digest=sha256:aaa  ← Layer 1完成
# POST /v2/test/multi-layer/blobs/uploads/           ← Layer 2开始
# PATCH ...
# PUT /v2/test/multi-layer/manifests/v1              ← Manifest上传

# Step 4: 调用我们的Everest驱动debug端点查看上传统计
# (如果驱动暴露了内部metrics)
curl -s http://harbor-registry:5001/debug/everest/stats | jq .
# {"total_writes": 42, "written_bytes": 2147483648, "fast_upload_calls": 14, "errors": 0}
```

### 3.5 常见陷阱与解决方案

| 序号 | 陷阱描述 | 根本原因 | 解决方案 |
|------|---------|---------|---------|
| 1 | `docker push`成功但`docker pull`总是404 Not Found | `FileWriter.Commit()`方法返回nil（声称成功），但实际数据未正确持久化到存储后端。原因可能是Commit中的HTTP请求返回了201但body含义与预期不同；或者是路径映射错误——Commit写入了path A，但GetContent查询的是path B | 在Commit后立即调用`Stat()`验证文件存在且Size>0；实现`URLFor`方法时确保path计算逻辑在Commit和GetContent中完全一致；添加集成测试覆盖 push→pull 的完整闭环 |
| 2 | 推送大镜像（>5GB）时OOM导致Registry进程被kill | `FileWriter.Write()`将每次PATCH的数据追加到内存buffer（bytes.Buffer），5GB的layer导致buffer占用5GB+内存 | 使用临时文件替代`bytes.Buffer`：在Writer初始化时用`os.CreateTemp("", "everest-upload-*")`创建临时文件，`Write()`写入文件，`Commit()`时读取文件发送HTTP请求，Commit后`os.Remove(tempFile)`清理 |
| 3 | 修改Registry配置后所有API返回401 Unauthorized | 自定义Registry编译时遗漏了Harbor的Token鉴权中间件。裸`registry:2`镜像无法与Core通信——Core签发Token时验证`service`字段，不匹配则拒绝签名 | 检查编译过程中是否导入了`src/registry/init.go`——它负责注册Harbor的Custom Token Handler和Quota Bridge Middleware；验证Registry日志中Token鉴权的`challenge`流程是否正常触发 |
| 4 | GC执行后存储空间不释放 | `Delete()`方法返回nil但实际未物理删除Everest上的文件。原因可能是DELETE请求的HTTP签名问题（Everest要求Content-MD5头）；或者是异步删除的语义差异——Everest返回202 Accepted（已接收但未完成），但Registry认为200才算成功 | 在Delete实现中对非200/204的状态码都返回error，触发GC重试；或实现DELETE后轮询文件是否真被删除的逻辑 |

---

## 4 项目总结

### 4.1 存储驱动能力对比

| 能力维度 | filesystem驱动 | s3aws驱动 | 自定义Everest驱动 | 注意事项 |
|---------|--------------|----------|------------------|---------|
| 小文件读写(<1MB) | GetContent → 一次ReadFile | GetContent → 一次S3 GetObject | GetContent → 一次HTTP GET | 所有驱动接口一致 |
| 大文件上传(>100MB) | Writer → 临时文件 → Commit(Rename) | Writer → MultipartUpload → Commit(CompleteMPU) | Writer → 临时文件 → Commit(FastUpload) | Commit实现差异最大 |
| 分块上传(PATCH) | 追加写入临时文件 | UploadPart(每次PATCH) | 追加写入临时文件 | filesystem和Everest都是先缓冲后一次性Commit |
| 目录扫描(List) | os.ReadDir(本地IO,微秒级) | S3 ListObjectsV2(HTTP,毫秒级) | HTTP GET prefix(HTTP,毫秒级) | filesystem的List性能最优, 网络驱动受延迟影响大 |
| 文件删除(Delete) | os.Remove(同步,微秒级) | S3 DeleteObject(同步,毫秒级) | DELETE HTTP(同步,毫秒级) | 都是同步删除 |
| JWT动态续约 | 不需要(本地IO) | 需要AWS SDK的credential refresh | 需要自实现TokenProvider | 网络驱动才需要认证管理 |
| 适用场景 | 单机Harbor、小规模(<1TB) | 云原生、弹性扩容、海量数据 | 自研存储系统、定制认证 | - |

### 4.2 适用场景与不适用场景

**适用场景：**
1. 企业需要将Harbor的镜像存储后端从标准S3迁移到自研的对象存储系统
2. 存储后端需要额外的自定义认证头（如租户隔离、跨机房鉴权）
3. 需要针对大文件传输做存储层的优化（如合并分片减少HTTP请求数、断点续传）
4. 需要在存储层嵌入数据治理逻辑（如自动加密、格式转换、冷热分层）
5. 存储后端提供非标准的上传优化API（如多点分片合并上传），需要在驱动层适配

**不适用场景：**
1. 仅需修改Harbor的访问控制（RBAC）或用户认证方式 —— 这属于Core层职责，Registry层不处理用户管理
2. 仅需调整GC的回收策略（如保留天数、并发度等） —— GC由JobService调度，Registryctl执行，修改存储驱动无法影响GC的调度逻辑

### 4.3 注意事项

1. **所有Blob上传的最终一致性依赖于digest校验**：`Commit()`完成后，Registry会用客户端提交的digest与存储后端实际文件计算的sha256比对。如果你的驱动在Commit期间对数据做了任何转换（如压缩、加密），digest必然不匹配——导致`docker pull`失败。驱动层必须保证写入的数据与客户端发送的逐字节一致。
2. **`List()`方法的性能决定了GC的持续时间**：如果一个Registry实例存储了100万个Blob，GC的mark阶段需要对所有Blob执行`List()`操作。如果你的自定义驱动在`List()`实现中使用了全量扫描（如遍历所有S3对象），单次GC可能耗时数小时。建议在存储层建立Blob索引（如维护一个Blob元数据表）来加速List。
3. **`Writer.Write()`必须支持任意大小的chunk**：客户端PATCH请求的chunk大小由Docker CLI决定（默认受Docker daemon的`--max-concurrent-uploads`和网络MTU影响），你的驱动不能假设chunk大小。`Write()`方法必须正确处理0字节chunk（某些Docker版本会发送）和超大chunk（如100MB）。
4. **错误传播必须保留上游的trace信息**：当Everest返回`503 Service Unavailable`时，你的驱动应将该503状态码和Everest的`X-Request-ID`打包成error，而非吞掉后返回通用的`fmt.Errorf("upload failed")`。这样Harbor的运维团队才能通过trace ID快速定位是Registry问题还是存储问题。
5. **使用临时文件时注意磁盘空间和并发安全**：如果10个docker client同时推送镜像，你的驱动可能同时持有10个临时文件。需确保临时目录有足够空间（建议≥单层镜像最大大小×并发数）。同时使用`os.CreateTemp`的随机文件名避免并发冲突，Commit后或Cancel后立即删除临时文件防止磁盘泄漏。

### 4.4 常见陷阱速查表

| 陷阱 | 出现概率 | 影响 | 快速诊断命令 |
|------|---------|------|-------------|
| Commit返回成功但GetContent 404 | 高 | 镜像不可拉取 | `curl -I <registry>/v2/<repo>/blobs/<digest>` |
| 内存OOM导致Registry Crash | 中 | 服务中断 | `docker stats harbor-registry \| grep MEM` |
| Token过期导致长上传失败 | 中 | 大镜像推送全部失败 | `docker push` 查看中断时间点是否≈JWT有效期 |
| GC后磁盘不释放 | 低 | 存储成本持续增长 | `docker exec harbor-registry df -h /storage` |

### 4.5 深度思考题

1. **跨地域Registry的Blob去重存储设计。** 如果你的公司在北京、上海、深圳三个机房各部署一套Harbor实例，三层共用同一个Everest存储集群（通过自定义驱动连接），当用户在北京push一个`nginx:1.25`后，上海的Registry能否识别出该Blob已存在于Everest中而跳过重复上传？当前的`StorageDriver`接口是否需要扩展才能支持"跨Registry实例的Blob共享"？请从OCI Spec的限制（Repository Name → Blob Namespace的绑定关系）和存储层去重策略两个角度分析。

2. **流式存储驱动的端到端零拷贝设计。** 当前的Registry架构中，`docker push`的数据流是：Client → Nginx(HTTPS) → Registry(HTTP) → StorageDriver(HTTP) → Everest。数据在每一跳都被完整拷贝到内存buffer。如果要求实现"从Client TCP socket直接流式传输到Everest TCP socket"的零拷贝通道（类似于HTTP CONNECT隧道），你的`FileWriter`接口需要如何重新设计？这会破坏OCI Distribution Spec的协议吗？请分析在OCI spec的约束下，可能的最优数据路径能减少多少次内存拷贝。

---

> 下一章预告：第36章剖析Scanner Adapter扫描适配器源码。
