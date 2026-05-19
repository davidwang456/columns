# 第36章：自定义 Scanner 插件开发实战

> 版本：Trivy v0.50+
> 面向人群：Go 开发者、安全工具开发者、平台工程师
> 源码参考：pkg/plugin/

---

## 1. 项目背景

### 业务场景

云帆科技的架构师小白已经掌握了 Trivy 的源码核心——Fanal 引擎、Detector 体系、报告系统。但面对一个实际需求时，他发现这些能力还不够用：公司有一套内部维护的基础镜像基线规范，要求所有生产镜像必须包含特定的监控 Agent、特定的安全加固项（如 auditd 启用、chrony 对时）。这些检查不是标准 CVE、不是 IaC 配置错误、也不是 Secret 泄露——它是云帆科技独有的合规要求。

「能不能在 Trivy 的框架内扩展这些检查？」小白的同事提出了这个需求。答案是可以——通过 Trivy 的插件系统。但 Trivy 的插件机制与传统的「动态库加载」不同：它是基于子进程通信（stdin/stdout JSON 协议）的。这意味着插件可以用任何语言编写——Go、Python、Rust、Bash——只要它能读 stdin 写 stdout。

但团队的 Python 高手写的插件和 Go 高手写的插件如何统一管理？版本如何兼容？如何在 CI/CD 中自动安装？这些问题指向了 Trivy 插件系统的完整设计和实践。

### 痛点放大

**第一，内置能力有边界。** Trivy 的 Scanner 覆盖了漏洞、配置错误、密钥、许可证四大领域。但如果你的安全需求是「检查镜像中是否安装了特定版本的 dtrace」或「验证所有 JAR 文件是否包含 MANIFEST.MF」——这些高度定制化的需求不会被内置。

**第二，插件生态不成熟。** 相比于 Kubernetes、Terraform 的插件生态，Trivy 的社区插件数量有限。这意味着大多数企业需要自行开发插件，而开发插件的前提是理解 Trivy 的插件协议。

**第三，多语言团队协作困境。** 安全团队可能用 Python 写规则引擎，平台团队用 Go 写基础设施工具。Trivy 的 stdin/stdout JSON 协议使得跨语言协作成为可能，但需要有统一的开发框架和测试标准。

**本章的核心目标是：深入 Trivy 插件协议，实战开发一个基镜像基线合规检查插件（trivy-plugin-baseline），实现插件的安装、调试、版本管理与 CI/CD 集成。**

---

## 2. 项目设计

**场景**：云帆科技的插件开发 workshop，小胖（Python 熟手）、小白（Go 开发者）、大师在讨论插件方案。

---

**小胖**：「为什么不用 Python 直接写检查脚本？我 50 行 Python 就能检查镜像里有没有 auditd。为什么要搞一个插件？」

**小白**：「50 行 Python 的检查脚本能做一件事，但如果明天又要检查 chrony、后天又要检查 SELinux——你的脚本很快就会膨胀成 500 行的『检查脚本怪物』。Trivy 插件的好处是：它被 Trivy 当作『一等公民』——结果会合并到统一的扫描报告中，可以被 --severity 过滤、可以被 --exit-code 控制、可以被 Report Writer 输出到任何格式。」

**大师**：「技术映射：Trivy 插件就像手机 App Store 里的应用。手机操作系统提供了摄像头、GPS、通知等基础能力（Trivy 的漏洞扫描、配置检查），但你需要一个『计步器』或『条形码扫描器』（自定义检查）——这些操作系统不内置，但你可以通过 App Store 安装。Trivy 的 `plugin install` 就是 App Store。」

**小胖**：「那插件和 Trivy 之间怎么通信？不会是 RPC 或 gRPC 吧？」

**小白**：「比那更简单——stdin/stdout 加 JSON。Trivy 调用插件时：

1. 通过 stdin 发送一个 JSON 格式的 `RunOptions`（包含扫描目标的路径、配置参数）。
2. 插件执行检查逻辑。
3. 通过 stdout 返回一个 JSON 格式的 `types.Report`。
4. Trivy 读取 stdout，将插件的 Result 合并到总报告中。

协议极简——任何能读写 stdin/stdout 的语言都能实现插件。」

**大师**：「插件还需要一个 `plugin.yaml` 清单文件，告诉 Trivy 如何安装和执行它：

```yaml
name: baseline
version: 1.0.0
repository: github.com/cloud-sail/trivy-plugin-baseline
maintainer: Cloud Sail Platform Team
summary: Check baseline compliance of container images
description: |
  Verifies that container images meet Cloud Sail baseline:
  - Monitoring agent (cloudsail-agent) is installed
  - auditd is enabled
  - chrony/ntp is configured
  - Non-root user exists

# 支持的平台
platforms:
  - selector:
      os: linux
      arch: amd64
    uri: https://releases.cloud-sail.com/trivy-baseline/v1.0.0/linux-amd64.tar.gz
  - selector:
      os: darwin
      arch: arm64
    uri: https://releases.cloud-sail.com/trivy-baseline/v1.0.0/darwin-arm64.tar.gz

# 用法说明
usage: |
  trivy baseline [flags] IMAGE_OR_DIRECTORY
```

Trivy 的 plugin install baseline 会自动下载对应平台的二进制，放到 ~/.trivy/plugins/ 目录。」

---

## 3. 项目实战

### 环境准备

- **Trivy**：v0.50+
- **Go**：1.21+（用于编译插件）
- **Docker**：用于构建测试镜像

### 步骤一：理解 Trivy 插件协议

**目标**：理解 stdin/stdout 的 JSON 通信协议。

```go
// Trivy 调用插件时的输入（stdin）
type RunOptions struct {
    Args   []string          `json:"Args"`   // 命令行参数（如镜像名）
    Config PluginConfig      `json:"Config"` // 插件配置
}

// 插件返回的输出（stdout）
// 直接使用 types.Report 结构体
type Report struct {
    SchemaVersion int       `json:"SchemaVersion"`
    ArtifactName  string    `json:"ArtifactName"`
    Results       []Result  `json:"Results"`
}

type Result struct {
    Target            string                 `json:"Target"`
    Class             string                 `json:"Class"`     // "custom"
    Type              string                 `json:"Type"`      // "baseline"
    Misconfigurations []DetectedMisconfiguration `json:"Misconfigurations,omitempty"`
    CustomResources   []CustomResource       `json:"CustomResources,omitempty"`
}
```

最简插件模板：

```go
package main

import (
    "encoding/json"
    "os"

    "github.com/aquasecurity/trivy/pkg/types"
)

func main() {
    // Step 1: 从 stdin 读取 RunOptions
    var opts struct {
        Args []string `json:"Args"`
    }
    json.NewDecoder(os.Stdin).Decode(&opts)

    // Step 2: 执行自定义检查逻辑
    imagePath := opts.Args[0]
    results := runBaselineChecks(imagePath)

    // Step 3: 输出 types.Report 到 stdout
    report := types.Report{
        SchemaVersion: 2,
        ArtifactName:  imagePath,
    }
    for _, r := range results {
        report.Results = append(report.Results, types.Result{
            Target:           imagePath,
            Class:            types.ClassCustom,
            Type:             "baseline",
            Misconfigurations: r.Misconfigurations,
        })
    }

    encoder := json.NewEncoder(os.Stdout)
    encoder.Encode(report)
}
```

### 步骤二：开发基线合规检查插件

**目标**：实现 trivy-plugin-baseline，检查镜像是否符合公司基线。

创建 `trivy-plugin-baseline/main.go`：

```go
package main

import (
    "encoding/json"
    "fmt"
    "os"
    "os/exec"
    "path/filepath"
    "strings"

    "github.com/aquasecurity/trivy/pkg/types"
)

// BaselineCheck 单条基线检查
type BaselineCheck struct {
    ID          string
    Title       string
    Description string
    Severity    string
    CheckFn     func(imagePath string) (bool, string) // (pass, detail)
}

// 定义基线检查项
var baselineChecks = []BaselineCheck{
    {
        ID:          "CS-BSL-001",
        Title:       "Monitoring agent installed",
        Description: "All production images must contain the cloudsail-agent binary at /usr/local/bin/cloudsail-agent",
        Severity:    "HIGH",
        CheckFn:     checkMonitoringAgent,
    },
    {
        ID:          "CS-BSL-002",
        Title:       "Non-root user exists",
        Description: "Image must define a non-root user (UID >= 1000) in /etc/passwd",
        Severity:    "HIGH",
        CheckFn:     checkNonRootUser,
    },
    {
        ID:          "CS-BSL-003",
        Title:       "Audit daemon available",
        Description: "Image must contain auditd or auditctl binary for security auditing",
        Severity:    "MEDIUM",
        CheckFn:     checkAuditDaemon,
    },
    {
        ID:          "CS-BSL-004",
        Title:       "NTP client configured",
        Description: "Image must contain chronyd or ntpd for time synchronization",
        Severity:    "MEDIUM",
        CheckFn:     checkNTPClient,
    },
    {
        ID:          "CS-BSL-005",
        Title:       "No world-writable files",
        Description: "No files in /etc should have world-writable permissions (o+w)",
        Severity:    "CRITICAL",
        CheckFn:     checkWorldWritable,
    },
    {
        ID:          "CS-BSL-006",
        Title:       "Package manager cache cleaned",
        Description: "Package manager caches (/var/cache/apk, /var/cache/apt) should be cleaned to reduce image size and vuln surface",
        Severity:    "LOW",
        CheckFn:     checkPackageCacheCleaned,
    },
}

func main() {
    // 读取输入
    var opts struct {
        Args []string `json:"Args"`
    }
    if err := json.NewDecoder(os.Stdin).Decode(&opts); err != nil {
        fmt.Fprintf(os.Stderr, "Error parsing input: %v\n", err)
        os.Exit(1)
    }

    if len(opts.Args) == 0 {
        fmt.Fprintf(os.Stderr, "Usage: trivy baseline IMAGE_OR_DIRECTORY\n")
        os.Exit(1)
    }

    imagePath := opts.Args[0]
    misconfigs := runChecks(imagePath)

    // 构建输出
    report := types.Report{
        SchemaVersion: 2,
        ArtifactName:  imagePath,
        Results: []types.Result{
            {
                Target:           imagePath,
                Class:            types.ClassCustom,
                Type:             "baseline-compliance",
                Misconfigurations: misconfigs,
            },
        },
    }

    encoder := json.NewEncoder(os.Stdout)
    encoder.SetIndent("", "  ")
    if err := encoder.Encode(report); err != nil {
        fmt.Fprintf(os.Stderr, "Error encoding output: %v\n", err)
        os.Exit(1)
    }
}

func runChecks(imagePath string) []types.DetectedMisconfiguration {
    var findings []types.DetectedMisconfiguration

    for _, check := range baselineChecks {
        passed, detail := check.CheckFn(imagePath)

        status := types.StatusFailure
        if passed {
            status = types.StatusPassed
        }

        findings = append(findings, types.DetectedMisconfiguration{
            ID:          check.ID,
            AVDID:       check.ID,
            Type:        "Cloud Sail Baseline",
            Title:       check.Title,
            Description: check.Description,
            Severity:    check.Severity,
            Status:      status,
            Message:     fmt.Sprintf("[%s] %s", map[bool]string{true: "PASS", false: "FAIL"}[passed], detail),
            Resolution:  getResolution(check.ID),
        })
    }

    return findings
}

func checkMonitoringAgent(path string) (bool, string) {
    return fileExists(path, "usr/local/bin/cloudsail-agent"), "cloudsail-agent binary"
}

func checkNonRootUser(path string) (bool, string) {
    content, err := os.ReadFile(filepath.Join(path, "etc/passwd"))
    if err != nil {
        return false, "cannot read /etc/passwd"
    }
    for _, line := range strings.Split(string(content), "\n") {
        parts := strings.Split(line, ":")
        if len(parts) >= 4 && parts[2] != "0" {
            uid := parts[2]
            if uid >= "1000" {
                return true, fmt.Sprintf("found non-root user UID=%s", uid)
            }
        }
    }
    return false, "no non-root user found"
}

func checkAuditDaemon(path string) (bool, string) {
    for _, binary := range []string{"usr/sbin/auditd", "usr/sbin/auditctl", "sbin/auditd"} {
        if fileExists(path, binary) {
            return true, binary
        }
    }
    return false, "auditd/auditctl not found"
}

func checkNTPClient(path string) (bool, string) {
    for _, binary := range []string{
        "usr/sbin/chronyd", "usr/sbin/ntpd",
        "sbin/chronyd", "sbin/ntpd",
    } {
        if fileExists(path, binary) {
            return true, binary
        }
    }
    return false, "no NTP client found"
}

func checkWorldWritable(path string) (bool, string) {
    // 用 find 命令检查（限 /etc 目录）
    etcDir := filepath.Join(path, "etc")
    cmd := exec.Command("find", etcDir, "-perm", "-0002", "-type", "f")
    output, _ := cmd.Output()
    files := strings.TrimSpace(string(output))
    if files != "" {
        return false, fmt.Sprintf("world-writable files found: %s", files)
    }
    return true, "no world-writable files in /etc"
}

func checkPackageCacheCleaned(path string) (bool, string) {
    cacheDirs := []string{
        "var/cache/apk",
        "var/cache/apt",
        "var/cache/yum",
        "var/cache/dnf",
    }
    for _, dir := range cacheDirs {
        fullPath := filepath.Join(path, dir)
        if info, err := os.Stat(fullPath); err == nil && info.IsDir() {
            entries, _ := os.ReadDir(fullPath)
            if len(entries) > 0 {
                return false, fmt.Sprintf("package cache not cleaned in /%s (%d files)", dir, len(entries))
            }
        }
    }
    return true, "package manager caches cleaned"
}

func fileExists(base, relative string) bool {
    _, err := os.Stat(filepath.Join(base, relative))
    return err == nil
}

func getResolution(id string) string {
    resolutions := map[string]string{
        "CS-BSL-001": "Install cloudsail-agent in your Dockerfile: COPY cloudsail-agent /usr/local/bin/",
        "CS-BSL-002": "Add a non-root user: RUN adduser -D appuser && USER appuser",
        "CS-BSL-003": "Install auditd: RUN apk add audit",
        "CS-BSL-004": "Install chrony: RUN apk add chrony",
        "CS-BSL-005": "Fix permissions: RUN chmod -R go-w /etc",
        "CS-BSL-006": "Clean cache in Dockerfile: RUN apk cache clean && rm -rf /var/cache/apk/*",
    }
    return resolutions[id]
}
```

### 步骤三：创建插件清单文件

**目标**：编写 plugin.yaml，让 Trivy 能发现和安装插件。

创建 `trivy-plugin-baseline/plugin.yaml`：

```yaml
name: baseline
version: 1.0.0
repository: github.com/cloud-sail/trivy-plugin-baseline
maintainer: Cloud Sail Platform Team <platform@cloud-sail.com>
summary: Check baseline compliance of container images
description: |
  Verifies that container images meet the Cloud Sail production baseline:
  - Monitoring agent (cloudsail-agent) is installed
  - A non-root user (UID >= 1000) exists
  - auditd/auditctl is available
  - chronyd/ntpd is present
  - No world-writable files in /etc
  - Package manager caches are cleaned

platforms:
  - selector:
      os: linux
      arch: amd64
    uri: https://github.com/cloud-sail/trivy-plugin-baseline/releases/download/v1.0.0/trivy-baseline-linux-amd64.tar.gz
    bin: ./trivy-baseline

  - selector:
      os: linux
      arch: arm64
    uri: https://github.com/cloud-sail/trivy-plugin-baseline/releases/download/v1.0.0/trivy-baseline-linux-arm64.tar.gz
    bin: ./trivy-baseline

  - selector:
      os: darwin
      arch: amd64
    uri: https://github.com/cloud-sail/trivy-plugin-baseline/releases/download/v1.0.0/trivy-baseline-darwin-amd64.tar.gz
    bin: ./trivy-baseline

  - selector:
      os: darwin
      arch: arm64
    uri: https://github.com/cloud-sail/trivy-plugin-baseline/releases/download/v1.0.0/trivy-baseline-darwin-arm64.tar.gz
    bin: ./trivy-baseline

usage: |
  trivy baseline [flags] IMAGE_OR_DIRECTORY

  Examples:
    # Scan a local image tarball
    trivy baseline my-image.tar

    # Scan an extracted filesystem
    trivy baseline /tmp/image-rootfs/

    # Set minimum severity for exit code
    trivy baseline --severity HIGH,CRITICAL my-image.tar
```

### 步骤四：构建并安装插件

**目标**：编译插件、打包、安装到 Trivy。

```bash
# 编译插件
cd trivy-plugin-baseline
go build -o trivy-baseline .

# 打包
tar -czf trivy-baseline-linux-amd64.tar.gz trivy-baseline plugin.yaml

# 推送到 GitHub Release（或内部文件服务器）
# gh release create v1.0.0 trivy-baseline-linux-amd64.tar.gz

# 安装插件
trivy plugin install baseline github.com/cloud-sail/trivy-plugin-baseline

# 查看已安装的插件
trivy plugin list
# 输出:
# Installed plugins:
#   baseline v1.0.0  Check baseline compliance of container images

# 运行插件
docker save my-image:latest -o /tmp/my-image.tar
trivy baseline /tmp/my-image.tar

# 指定严重级别
trivy baseline --severity CRITICAL,HIGH --exit-code 1 /tmp/my-image.tar

# 输出为 JSON
trivy baseline --format json --output baseline-report.json /tmp/my-image.tar

# 卸载插件
trivy plugin uninstall baseline
```

### 步骤五：在 CI/CD 中集成插件

**目标**：在 GitHub Actions 中自动安装插件并执行基线检查。

```yaml
# .github/workflows/baseline-check.yml
name: Baseline Compliance Check

on:
  pull_request:
    paths:
      - 'Dockerfile'
      - 'docker/**'
  push:
    branches: [main]

jobs:
  baseline:
    runs-on: ubuntu-latest
    steps:
    - uses: actions/checkout@v4

    - name: Build image
      run: |
        docker build -t app:test .
        docker save app:test -o /tmp/app.tar

    - name: Install baseline plugin
      run: trivy plugin install baseline github.com/cloud-sail/trivy-plugin-baseline

    - name: Run baseline check
      run: |
        trivy baseline \
          --severity CRITICAL,HIGH \
          --exit-code 1 \
          --format json \
          --output baseline-report.json \
          /tmp/app.tar

    - name: Upload report
      uses: actions/upload-artifact@v4
      with:
        name: baseline-report
        path: baseline-report.json
```

### 测试验证

1. 编译插件并安装到 Trivy，运行 `trivy plugin list` 确认插件可见。
2. 创建一个不含 cloudsail-agent 的测试镜像，运行 `trivy baseline` 验证 CS-BSL-001 被触发。
3. 创建一个含 world-writable /etc 文件的镜像，验证 CS-BSL-005（CRITICAL）被触发且 exit-code 为 1。
4. 验证插件的 JSON 输出格式与标准 Trivy 报告格式一致（可被 convert 子命令二次处理）。
5. 编写一个 shell 脚本模拟插件的 stdin/stdout 协议，用 `echo '{"Args": ["/tmp/test"]}' | bash -c '...'` 验证协议兼容性。

---

## 4. 项目总结

### 优点 & 缺点

| 维度 | 优点 | 缺点 |
|------|------|------|
| stdin/stdout 协议 | 语言无关；极简接口 | 大报告时 JSON 序列化内存大 |
| plugin.yaml 清单 | 统一安装方式；平台自动匹配 | 离线安装需提前下载二进制 |
| 与 Trivy 报告统一 | 插件结果可被过滤、格式化、归档 | 插件必须严格遵循 types.Report 结构 |
| 独立二进制分发 | 零运行时依赖；可独立调试 | 需要为每个平台编译 |
| 版本管理 | 支持语义化版本和升级 | 需要维护 Release 流程 |

### 适用场景

1. **企业内部合规基线**：检查镜像/代码是否符合公司特有的安全规范。
2. **自定义漏洞扫描**：对接公司内部的漏洞数据库或威胁情报源。
3. **私有 Registry 元数据检查**：扫描 Dockerfile 是否符合公司 Registry 规范。
4. **多语言团队协作**：Go 写核心逻辑、Python 做数据分析和规则引擎。
5. **临时一次性检查**：团队可以快速写一个 Bash 脚本包装成插件，无需修改 Trivy 源码。

**不适用场景**：
1. 通用场景（Trivy 内置已覆盖）。
2. 需要修改 Trivy 核心行为的场景（如新增 Analyzer）——应走 Library 模式（第 39 章）。

### 注意事项

- **插件输出必须严格遵循 types.Report 格式**。如果 SchemaVersion 不对或字段缺失，Trivy 会静默丢弃结果。
- **插件执行是同步阻塞的**。复杂检查（如扫描大型镜像）可能需要数分钟，注意超时控制。
- **插件的 input 是镜像 tarball 或目录路径**——不是镜像名。Trivy 不会替插件拉取镜像，需要自己在插件中处理（或接受已经解压好的文件系统路径）。

### 常见踩坑经验

**踩坑案例 1：插件安装后无法执行**
- **现象**：`trivy baseline image.tar` 报错 command not found。
- **根因**：plugin.yaml 中的 bin 路径与打包时的目录结构不一致。
- **解法**：确保 plugin.yaml 中 bin 的路径是相对于 tar.gz 解压根目录的。

**踩坑案例 2：插件输出的 JSON 无法被 trivy convert 处理**
- **现象**：运行 trivy convert 时报 invalid report format。
- **根因**：SchemaVersion 字段缺失或值不是 2。
- **解法**：在 Report 中必须设置 SchemaVersion: 2。

**踩坑案例 3：插件在 macOS 上行为与 Linux 不同**
- **现象**：checkWorldWritable 在 macOS 上误报。
- **根因**：macOS 的文件系统权限模型与 Linux 有细微差异。
- **解法**：在 Docker 容器中测试插件；在 CI 中使用 Linux Runner。

### 思考题

1. 假设你要开发一个插件，需要访问 Trivy 的扫描数据库（trivy-db）来交叉引用漏洞信息。但 stdin/stdout 协议只传递了 Args 和 Config——没有传递 DB 路径。如何设计一个方案让插件能访问 trivy-db？
2. 如果公司的基线检查规则数量增长到 100+ 条，每条都要编译到插件二进制中。请设计一个「规则热加载」方案——规则存储在外部 YAML 文件中，插件运行时动态加载，无需重新编译。

> **答案提示**：第 39 章「Trivy 作为 Go Library 二次开发」提供了更高级的集成方式，可以直接调用 Trivy 的内部 API。

---

> **推广计划**：本章建议由平台团队和安全团队协作开发第一个插件。初期选择 5-6 条最重要的基线规则，在 1-2 个项目中试运行 2 周，确认无误后推广到所有生产项目。建议在团队内部建立「插件开发骨架仓库」——包含 Makefile、CI/CD 模板、测试框架，新人只需填充规则逻辑即可。

---

> **版权声明**：本章基于 Trivy 官方开源项目（Apache-2.0 License）编写，所有源码引用均遵循原许可证条款。
