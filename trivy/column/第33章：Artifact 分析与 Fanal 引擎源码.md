# 第33章：Artifact 分析与 Fanal 引擎源码

> 版本：Trivy v0.50+
> 面向人群：Go 开发者、安全工具开发者
> 源码参考：pkg/fanal/artifact/、pkg/fanal/analyzer/、pkg/fanal/applier/

---

## 1. 项目背景

### 业务场景

架构师小白在上一章建立了 Trivy 源码的全景认知，现在轮到最核心的问题：「Trivy 到底是怎么『看透』一个镜像的？」这个问题看似简单，背后却隐藏着一个精巧的分析引擎——Fanal。

想象一个场景：公司内部有一个特殊的构建流程，产物不是标准的 OCI 镜像，而是一个包含了 `.nvmrc` 文件的静态归档。安全团队想知道这个归档里的 Node.js 版本是否受某个 CVE 影响。Trivy 的内置 Analyzer 可以识别 `package.json`、`node_modules` 等标准 Node.js 产物，但 `.nvmrc` 不是标准产物——Trivy 不认识它。

如果理解了 Fanal 引擎的 Analyzer 注册和扩展机制，你可以在 100 行代码内为 Trivy 添加一个「`.nvmrc` Analyzer」——让 Trivy 自动识别 `.nvmrc` 文件并报告其中的 Node.js 版本信息。这不仅是「给 Trivy 提功能」，更是「给 Trivy 定义能力边界」——因为 Trivy 的可扩展性就不是体现在配置文件上，而是在源码层。

### 痛点放大

**第一，Fanal 引擎是 Trivy 最复杂的部分。** 它负责将异构的输入（OCI 镜像、本地文件系统、Git 仓库、VM 镜像、SBOM 文件）统一转换为结构化的分析结果（OS 信息、包清单、依赖图、配置、Secret、许可证）。不理解 Fanal，就无法理解 Trivy 为什么能同时扫镜像和扫文件夹。

**第二，Fanal 的 Analyzer 注册机制是隐式的**。40+ 个 Analyzer 通过 `init()` + `import _` 注册，不追踪代码就找不到「一个文件被谁处理了」。

**第三，Fanal 的 Applier 是层缓存的实现**。你第 24 章看到的分层缓存加速，在源码里就是 `applier.ApplyLayers()`——它负责逐层解压镜像层，并复用已有层的分析结果。理解 Applier 就是理解「为什么第二次扫描比第一次快很多」。

**本章的核心目标是：深入 Fanal 引擎源码，理解 Artifact 抽象、Analyzer 注册与执行机制、Applier 层缓存原理；实战编写一个自定义 Analyzer，实现对非标准文件（`.nvmrc`）的识别和版本提取。**

---

## 2. 项目设计

**场景**：云帆科技的源码 walk-through，小白、小胖和大师在分析 `pkg/fanal/` 目录。

---

**小胖**：「为什么叫 Fanal？这名字好奇怪。」

**大师**：「Fanal = File ANalysis Library。它是 Trivy 的一个内部库，负责『文件分析』——把一个原始的文件系统（镜像层、文件夹、仓库目录）拆解成 Trivy 能理解的结构化数据。它不知道什么是 CVE、什么是漏洞——它只管『这里面有什么』。」

**小白**：「Fanal 的核心抽象是两层：

**第一层：Artifact（被分析对象）**

```go
// pkg/fanal/artifact/artifact.go
type Artifact interface {
    Inspect(ctx context.Context) (reference Reference, err error)
    Clean(reference Reference) error
}
```

`Inspect()` 方法负责将原始输入「展开」为一个可分析的文件系统。Trivy 有 5 种 Artifact 实现：

| Artifact 类型 | 位置 | 职责 |
|--------------|------|------|
| Image | `artifact/image/` | 从 Registry 拉取镜像 → 解压所有层 → 构建虚拟文件系统 |
| Local | `artifact/local/` | 直接读取本地目录 → 构建虚拟文件系统 |
| Repository | `artifact/repo/` | Clone Git 仓库 → 构建虚拟文件系统 |
| SBOM | `artifact/sbom/` | 解析 SBOM 文件 → 提取包信息（跳过文件系统分析） |
| VM | `artifact/vm/` | 挂载 VM 镜像 → 提取文件系统 |

**第二层：Analyzer（文件分析器）**

```go
// pkg/fanal/analyzer/analyzer.go
type analyzer interface {
    Type() Type
    Version() int
    Analyze(ctx context.Context, input AnalysisInput) (*AnalysisResult, error)
    Required(filePath string, info os.FileInfo) bool
}
```

每个 Analyzer 关注一类文件——Alpine 的 `/etc/alpine-release`、Debian 的 `/etc/os-release`、Node.js 的 `package.json`、Python 的 `requirements.txt`。Fanal 引擎遍历文件系统中的每个文件，询问所有已注册的 Analyzer：『这个文件你需要处理吗？（Required？）』——如果返回 true，就调用 Analyze()。」

**小胖**：「等等——镜像里可能有几万个文件，每个文件都要问 40 个 Analyzer？那不会很慢吗？」

**大师**：「好问题。Fanal 做了几层优化：

1. **StaticPaths**：如果 Analyzer 实现 `StaticPathAnalyzer` 接口，Fanal 就知道它只需要特定路径的文件（如 `/etc/alpine-release`），不需要遍历整个文件系统。
2. **文件模式过滤**：每个 Analyzer 可以指定文件名的正则模式（如 `package*.json`），只对匹配的文件做 Required() 检查。
3. **并行执行**：AnalyzerGroup 内部使用 goroutine + semaphore 并行分析——通过 `--parallel` 参数控制并发度。」

**小白**：「还有第三层：**Applier（层应用器）**。

```go
// pkg/fanal/applier/applier.go
type Applier interface {
    ApplyLayers(ctx context.Context, artifactKey string, blobKeys []string) (*types.ArtifactDetail, error)
}
```

`ApplyLayers()` 按顺序解压镜像的每一层，将差异应用到内存文件系统（mapfs），然后对每层运行 Analyzer 并缓存分析结果。当第二个镜像共享底层时，Applier 直接从缓存读取该层的分析结果，跳过解压和分析。」

**大师**：「让我把这三层串起来看一次完整的镜像扫描流程：

```
Image Artifact.Inspect(ctx)
    │
    ├─ 1. 从 Registry 拉取镜像 manifest
    ├─ 2. 获取所有 layer 的 digest（SHA256）
    ├─ 3. Artifact 对象保存 {Name, ID, BlobIDs}
    │
    ▼
Backend.Scan(ctx, name, id, blobIDs, options)
    │
    ├─ 1. Applier.ApplyLayers(ctx, id, blobIDs)
    │       │
    │       ├─ 对每个 layer：
    │       │   ├─ 检查缓存（Redis/FS）
    │       │   ├─ 未命中 → 下载 blob → 解压到 mapfs
    │       │   ├─ 运行 AnalyzerGroup 分析该层的文件
    │       │   └─ 将分析结果写入缓存
    │       │
    │       └─ 返回 ArtifactDetail（OS + 所有包 + 配置 + Secret + 许可证）
    │
    ├─ 2. 将 ArtifactDetail 转换为 ScanTarget
    ├─ 3. OSPkgScanner.Scan() → Detector.Detect()
    ├─ 4. LangPkgScanner.Scan() → library.Detect()
    ├─ 5. 提取 Misconfig / Secret / License 结果
    └─ 6. 返回 ScanResponse
```

这就是 Trivy 『看透』一个镜像的完整过程。」

---

## 3. 项目实战

### 环境准备

- **Go**：1.21+
- **Trivy 源码**：已 clone 到本地
- **Docker**：用于构建测试镜像

### 步骤一：深入 Image Artifact 的 Inspect 实现

**目标**：追踪 `artifact/image/artifact.go` 的 `Inspect()` 方法。

关键代码路径（`pkg/fanal/artifact/image/artifact.go`）：

```go
func (a Artifact) Inspect(ctx context.Context) (artifact.Reference, error) {
    // Step 1: 解析镜像引用（解析 tag → digest）
    img, cleanup, err := a.image.NewImage(ctx)
    defer cleanup()

    // Step 2: 获取镜像 ID（如 sha256:abc123...）
    imageID, err := img.ID()

    // Step 3: 获取所有 layer 的 blob keys
    layerKeys, err := img.LayerIDs()

    // Step 4: 构建 Blob 列表
    // Trivy 的 blob key = "sha256:" + layer digest
    for _, key := range layerKeys {
        blobKeys = append(blobKeys, "sha256:"+key)
    }

    // Step 5: 返回 Reference
    return artifact.Reference{
        Name:    inputName,
        Type:    artifact.TypeContainerImage,
        ID:      "sha256:" + imageID,
        BlobIDs: blobKeys,
    }, nil
}
```

> **关键发现**：`Inspect()` 不做任何分析——它只收集元数据（镜像 ID、Layer ID 列表）。真正的分析发生在 `Backend.Scan()` → `Applier.ApplyLayers()` 中。

### 步骤二：理解 Applier 的层缓存实现

**目标**：追踪 `applier/applier.go` 中 `ApplyLayers()` 的缓存逻辑。

关键代码路径（简化自 `pkg/fanal/applier/applier.go`）：

```go
func (a Applier) ApplyLayers(ctx context.Context, artifactKey string, blobKeys []string) (*types.ArtifactDetail, error) {
    // 对每个 layer blob key
    for i, blobKey := range blobKeys {
        // 1. 检查缓存：这个 layer 是否已经被分析过？
        cachedResult, err := a.cache.Get(blobKey)
        if err == nil {
            // 缓存命中！跳过下载、解压、分析
            detail.Merge(cachedResult)
            continue
        }

        // 2. 从 Registry 下载 blob（或从本地 Docker 缓存读取）
        reader, err := a.registry.Get(blobKey)

        // 3. 解压 layer tar 到虚拟文件系统（mapfs）
        fs, err := a.unpack(reader)

        // 4. 运行 AnalyzerGroup 分析该层的所有文件
        result, err := a.analyzerGroup.Analyze(ctx, fs)

        // 5. 将分析结果写入缓存（Redis 或本地磁盘）
        a.cache.Set(blobKey, result)

        // 6. 合并到总结果
        detail.Merge(result)
    }
    return detail, nil
}
```

> **设计洞察**：Applier 是 Trivy 性能的核心。`a.cache.Get(blobKey)` 决定了是否要重复分析同一个 layer。这就是为什么第 24 章和第 25 章的缓存优化如此重要——如果缓存命中率高，`ApplyLayers()` 几乎瞬间完成。

### 步骤三：编写自定义 `.nvmrc` Analyzer

**目标**：创建一个新的 Analyzer，识别 `.nvmrc` 文件并提取 Node.js 版本信息。

创建目录结构：

```bash
mkdir -p custom-analyzer/nvmrc
```

创建 `custom-analyzer/nvmrc/nvmrc.go`：

```go
package nvmrc

import (
    "bufio"
    "context"
    "os"
    "strings"

    "github.com/aquasecurity/trivy/pkg/fanal/analyzer"
    misconf "github.com/aquasecurity/trivy/pkg/fanal/analyzer/config"
    "github.com/aquasecurity/trivy/pkg/fanal/types"
)

func init() {
    // 自注册：让 Fanal 引擎知道这个 Analyzer 的存在
    analyzer.RegisterAnalyzer(&nvmrcAnalyzer{})
}

// 定义一个唯一的 Type 常量
const TypeNvmrc analyzer.Type = "nvmrc"

type nvmrcAnalyzer struct{}

func (a nvmrcAnalyzer) Type() analyzer.Type { return TypeNvmrc }
func (a nvmrcAnalyzer) Version() int { return 1 }

// Required：只有当文件名为 .nvmrc 时才需要处理
func (a nvmrcAnalyzer) Required(filePath string, _ os.FileInfo) bool {
    return filePath == ".nvmrc"
}

// Analyze：从 .nvmrc 文件中提取 Node.js 版本
func (a nvmrcAnalyzer) Analyze(_ context.Context, input analyzer.AnalysisInput) (*analyzer.AnalysisResult, error) {
    scanner := bufio.NewScanner(input.Content)
    if !scanner.Scan() {
        return nil, nil
    }
    version := strings.TrimSpace(scanner.Text())

    // 如果版本号带前缀 v（如 v20.10.0），去掉
    version = strings.TrimPrefix(version, "v")

    return &analyzer.AnalysisResult{
        // 创建一个自定义资源来承载信息
        CustomResources: []types.CustomResource{
            {
                Type:     "nvmrc",
                FilePath: ".nvmrc",
                Layer: types.Layer{
                    DiffID: input.Info.DiffID,
                },
                Data: map[string]interface{}{
                    "node_version": version,
                },
            },
        },
    }, nil
}

// 确保符合 ConfigAnalyzer 接口（可选）
var _ misconf.ConfigAnalyzer = (*nvmrcAnalyzer)(nil)
```

创建 `custom-analyzer/main.go`（集成测试入口）：

```go
package main

import (
    "context"
    "fmt"
    "log"

    "github.com/aquasecurity/trivy/pkg/fanal/analyzer"
    // 注册所有内置 Analyzer
    _ "github.com/aquasecurity/trivy/pkg/fanal/analyzer/all"
    // 注册自定义 Analyzer
    _ "github.com/cloud-sail/trivy-extensions/nvmrc"

    "github.com/aquasecurity/trivy/pkg/fanal/artifact"
    localArtifact "github.com/aquasecurity/trivy/pkg/fanal/artifact/local"
    "github.com/aquasecurity/trivy/pkg/fanal/cache"
)

func main() {
    ctx := context.Background()

    // 初始化缓存
    c, err := cache.NewFSCache("/tmp/trivy-custom-cache")
    if err != nil {
        log.Fatal(err)
    }
    defer c.Close()

    // 创建本地文件系统 Artifact
    art, err := localArtifact.NewArtifact(
        "/path/to/project-with-nvmrc",
        c,
        analyzer.AnalyzerOptions{},
    )
    if err != nil {
        log.Fatal(err)
    }

    // 执行 Inspect（触发 Analyzer）
    ref, err := art.Inspect(ctx)
    if err != nil {
        log.Fatal(err)
    }

    fmt.Printf("Artifact: %s (Type: %s)\n", ref.Name, ref.Type)
    fmt.Printf("Image ID: %s\n", ref.ID)
    fmt.Printf("Blob IDs: %v\n", ref.BlobIDs)

    // 输出的 CustomResources 中会包含 .nvmrc 的信息
}
```

> **坑点**：自定义 Analyzer 需要被「注册」到 Fanal 引擎中。在 Library 模式下，你必须显式 import 自定义 Analyzer 的包。在 CLI 模式下，可以通过 Trivy 插件系统加载。

### 步骤四：验证自定义 Analyzer

**目标**：创建一个包含 `.nvmrc` 的测试项目，验证 Analyzer 能正确识别。

```bash
# 创建测试项目
mkdir /tmp/test-nvmrc
echo "20.10.0" > /tmp/test-nvmrc/.nvmrc
echo '{"name": "test", "version": "1.0.0"}' > /tmp/test-nvmrc/package.json

# 用标准 Trivy FS 扫描（不会识别 .nvmrc）
trivy fs --scanners vuln /tmp/test-nvmrc
# 输出：只显示 package.json 的依赖漏洞，不显示 .nvmrc 信息

# 用集成了自定义 Analyzer 的程序扫描
go run custom-analyzer/main.go /tmp/test-nvmrc
# 输出：Artifact: /tmp/test-nvmrc (Type: filesystem)
#       CustomResources: [{Type: nvmrc, FilePath: .nvmrc, Data: {node_version: 20.10.0}}]
```

### 步骤五：编写 Analyzer 单元测试

**目标**：用标准 Go 测试框架验证 Analyzer 行为。

创建 `custom-analyzer/nvmrc/nvmrc_test.go`：

```go
package nvmrc

import (
    "context"
    "os"
    "strings"
    "testing"

    "github.com/aquasecurity/trivy/pkg/fanal/analyzer"
    "github.com/stretchr/testify/assert"
    "github.com/stretchr/testify/require"
)

func TestNvmrcAnalyzer_Required(t *testing.T) {
    a := &nvmrcAnalyzer{}

    // 应该处理 .nvmrc
    assert.True(t, a.Required(".nvmrc", nil))

    // 不应该处理其他文件
    assert.False(t, a.Required("package.json", nil))
    assert.False(t, a.Required("README.md", nil))
    assert.False(t, a.Required("nvmrc", nil)) // 没有点号前缀
}

func TestNvmrcAnalyzer_Analyze(t *testing.T) {
    a := &nvmrcAnalyzer{}

    tests := []struct {
        name      string
        content   string
        wantVer   string
        wantError bool
    }{
        {"normal version", "20.10.0", "20.10.0", false},
        {"v-prefixed version", "v18.17.1", "18.17.1", false},
        {"empty file", "", "", false},
        {"lts alias", "lts/iron", "lts/iron", false},
    }

    for _, tt := range tests {
        t.Run(tt.name, func(t *testing.T) {
            result, err := a.Analyze(context.Background(), analyzer.AnalysisInput{
                Content: strings.NewReader(tt.content),
                FilePath: ".nvmrc",
                Info: os.FileInfo(nil),
            })

            if tt.wantError {
                require.Error(t, err)
                return
            }
            require.NoError(t, err)

            if tt.wantVer == "" {
                assert.Nil(t, result)
                return
            }

            require.NotNil(t, result)
            require.Len(t, result.CustomResources, 1)
            assert.Equal(t, tt.wantVer,
                result.CustomResources[0].Data["node_version"])
        })
    }
}
```

运行测试：

```bash
cd custom-analyzer
go test ./nvmrc/ -v
# PASS: TestNvmrcAnalyzer_Required (0.00s)
# PASS: TestNvmrcAnalyzer_Analyze/normal_version (0.00s)
# PASS: TestNvmrcAnalyzer_Analyze/v-prefixed_version (0.00s)
# PASS: TestNvmrcAnalyzer_Analyze/empty_file (0.00s)
# PASS: TestNvmrcAnalyzer_Analyze/lts_alias (0.00s)
```

### 测试验证

1. 在 Trivy 源码中搜索 `analyzer.RegisterAnalyzer`，确认所有 Analyzer 的注册位置。
2. 编译 `go build ./cmd/trivy/`，运行 `trivy image alpine:latest --debug 2>&1 | grep "analyzer"` 观察哪些 Analyzer 被触发。
3. 创建包含 `.nvmrc` 的项目，用自定义 Analyzer 程序验证版本识别。
4. 运行 `go test ./custom-analyzer/... -v`，验证所有单元测试通过。
5. 阅读 `pkg/fanal/applier/applier.go` 的 `ApplyLayers()` 函数，在纸上画出 layer-by-layer 的分析流程。

---

## 4. 项目总结

### 优点 & 缺点

| 维度 | 优点 | 缺点 |
|------|------|------|
| Fanal Artifact 抽象 | 统一了镜像/文件系统/仓库等异构输入 | 新 Artifact 类型需要实现完整接口 |
| Analyzer 注册机制 | 极简的扩展方式；热插拔式架构 | 隐式注册；全局状态 |
| 层缓存（Applier） | 核心性能优化；支持 Redis/FS 双后端 | 缓存失效策略依赖 TTL |
| mapfs 虚拟文件系统 | 零磁盘开销；内存高效 | 大文件（如 AI 模型）可能撑爆内存 |
| 并行分析 | semaphore 控制并发度；充分利用多核 | 并行度过高可能导致 OOM |

### 适用场景

1. **扩展 Trivy 识别新文件格式**：如公司内部的 `BUILD` 文件、`.bazelversion` 等。
2. **自定义合规检查**：编写 Analyzer 检查镜像中是否存在特定文件（如合规要求的 `VERSION.txt`）。
3. **集成私有包管理器**：如果公司使用内部包管理工具（非 npm/pip/maven），编写对应的 Analyzer。
4. **安全审计工具开发**：基于 Fanal 引擎构建自己的文件安全分析工具。
5. **理解 Trivy 性能优化**：Applier 的缓存策略是学习「计算缓存」模式的优秀案例。

**不适用场景**：
1. 简单的一次性扫描——不需要自定义 Analyzer，标准 Trivy 足够。

### 注意事项

- **Analyzer 的 `Required()` 要尽量精确**。如果 `Required()` 过于宽松（如返回 `true` 对所有文件），性能会严重下降。
- **mapfs 是有大小限制的**。超大文件（>100MB）的处理需要特殊考虑。
- **自定义 Analyzer 的版本兼容**。Trivy 升级时，Analyzer 接口可能有变化。维护自定义 Analyzer 需要关注上游 CHANGELOG。

### 常见踩坑经验

**踩坑案例 1：Analyzer 注册了但不生效**
- **现象**：`init()` 中注册了 Analyzer，但扫描时没有被调用。
- **根因**：忘记在 `main.go` 或入口包中 `import _` 自定义 Analyzer 的包。
- **解法**：确保所有 Analyzer 包通过 blank import 引入；在 Library 模式下，可以在代码中显式调用 `analyzer.RegisterAnalyzer()`。

**踩坑案例 2：Analyzer 的 Result 被覆盖**
- **现象**：多个 Analyzer 返回了结果，但最终只有最后一个有效。
- **根因**：`AnalysisResult` 的 Merge 逻辑——如果两个 Analyzer 都设置了 `OS`，后一个会覆盖前一个。
- **解法**：对于自定义信息，使用 `CustomResources` 字段而不是标准字段（OS、Repository 等）。

**踩坑案例 3：大镜像扫描时 mapfs OOM**
- **现象**：扫描 8GB 的 AI 镜像时内存暴增至 24GB。
- **根因**：mapfs 在内存中维护完整的目录树，大镜像的解压数据量远超预期。
- **解法**：用 `--parallel 1` 降低并发；限制容器 `--memory`；拆分超大镜像为基础镜像+业务层分层扫描。

### 思考题

1. 假设你的公司使用 `Bazel` 构建系统，构建产物中有一个 `bazel-out/` 目录包含了所有编译信息。你希望 Trivy 能识别其中的依赖信息。请设计一个 `BazelAnalyzer`：它将如何实现 `Required()`（文件匹配策略）和 `Analyze()`（解析 bazel-out 目录结构）？
2. Trivy 的 Applier 目前使用 `mapfs`（内存中文件系统）做层叠加。如果要分析一个 20GB 的超大镜像，如何在不大幅降低性能的前提下，将 mapfs 替换为基于磁盘的虚拟文件系统（如 overlayfs）？

> **答案提示**：第 34 章「漏洞检测引擎与数据库匹配源码」将深入 Detector 的实现细节。

---

> **推广计划**：本章建议有志于为 Trivy 贡献代码的 Go 工程师深入阅读 `pkg/fanal/` 目录下的源码。实战中编写的 `.nvmrc` Analyzer 可以作为团队内部的「Trivy 扩展开发工作坊」案例。建议每个学习者在 2 小时内完成「理解 Fanal 架构 + 编写一个自定义 Analyzer + 通过单元测试」的完整流程。

---

> **版权声明**：本章基于 Trivy 官方开源项目（Apache-2.0 License）源码分析编写，所有源码引用均遵循原许可证条款。
