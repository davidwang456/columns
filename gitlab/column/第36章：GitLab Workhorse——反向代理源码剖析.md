# 第36章：GitLab Workhorse——反向代理源码剖析

## 1. 项目背景

> **业务场景**：某公司 GitLab 实例在接收 1GB+ 大文件推送时，Puma 进程频繁超时和 OOM。运维排查发现，大文件在 `git push` 和附件上传时，数据流竟然要通过 Ruby on Rails 进程——Ruby 进程天生不适合处理大文件流，内存占用与文件大小成正比。运维在社区搜到 GitLab Workhorse 可以解决这个问题，但不理解 Workhorse 的原理——它到底是什么？在架构中处于什么位置？为什么能解决大文件问题？

另一个事件：运维想给 GitLab 加一个请求追踪功能——在 HTTP 响应头中注入一个 `X-Request-ID` 用于链路追踪。他知道这个逻辑应该在反向代理层做，但 GitLab 的架构中有 Nginx、Workhorse、Puma 三层代理——应该在哪一层加？

**痛点放大**：Workhorse 是 GitLab 架构中"最被低估但最重要"的组件。它是用 Go 编写的高性能反向代理，位于 Nginx 和 Rails 之间，负责处理大文件流式传输、请求预处理、鉴权代理、WebSocket 升级等职责。不理解 Workhorse，就无法理解 GitLab 架构中"为什么大文件上传不会拖垮 Rails"。

## 2. 项目设计——剧本式交锋对话

**场景**：运维团队在讨论一个 GitLab 架构优化方案时，对 Workhorse 的角色起了争论。

---

**运维小李**："我一直以为 Nginx 直接代理到 Rails——原来中间还有一个 Workhorse？为什么需要这一层？Nginx 不就能做反向代理吗？"

**大师**："Nginx 能做反向代理，但它是通用代理——它不理解 GitLab 的业务逻辑。Workhorse 能做的事情 Nginx 做不到——比如在代理前先问 Rails '这个请求是否合法'，然后根据 Rails 的指令决定如何转发。Workhorse 不是一个普通的反向代理，它是'智能代理'。"

**小胖**："智能在哪？举个具体例子呗。"

**大师**："比如一个 `git push` 请求。如果 Nginx 直接代理给 Rails，整个 1GB 的 push 数据都要经过 Ruby 进程——内存炸了。Workhorse 的做法是：先发一个轻量级的 pre-authorization 请求给 Rails（只包含请求头信息，没有 body），Rails 验证权限后返回指令——'允许，数据直接发给 Gitaly'。然后 Workhorse 就绕过 Rails，把大文件数据流式传输给 Gitaly 的 gRPC 端口。技术映射——Workhorse 就像机场的值机柜台：你先把护照给工作人员看一眼（pre-auth），然后行李直接走传送带去飞机（Gitaly），不需要工作人员帮你搬行李。"

**小白**："那 Workhorse 内部是怎么区分哪些请求需要 pre-auth，哪些不需要的？"

**大师**："Workhorse 查看请求的 URL 路径。它对不同类型的请求有不同的处理策略——API 请求直接代理给 Rails（小请求，不需要预处理）；Git 操作（`/repo.git/info/refs` 等）需要 pre-auth 然后决定后续路由；文件上传（`/uploads/`）需要 pre-auth 获取存储路径后直接写磁盘；WebSocket 升级（CI 的 Interactive Terminal）需要代理到正确的后端。所有的路由判断逻辑都在 Workhorse 的 Go 代码里，不依赖外部配置。"

**小胖**："那如果我想修改 Workhorse 的行为——比如加一个自定义的请求头——应该改哪个 Go 包？"

**大师**："Workhorse 的源码结构很清晰——核心在处理管道：`internal/upstream/` 负责与 Rails 的 pre-auth 通信和响应分发，`internal/gitaly/` 负责与 Gitaly 的 gRPC 通信，`internal/upload/` 负责文件上传的中间处理。要加请求头通常在 `internal/upstream/upstream.go` 的 `proxyToRails` 函数附近修改。但注意 Workhorse 是用 Go 写的——改完后需要重新编译。"

---

## 3. 项目实战

### 环境准备

| 工具 | 用途 |
|------|------|
| GitLab Workhorse 源码 | `git clone https://gitlab.com/gitlab-org/gitlab-workhorse.git` (Go) |
| Go 1.21+ | 编译 Workhorse |
| curl / Git 客户端 | 测试 Workhorse 处理的请求 |
| GitLab Omnibus | 查看 Workhorse 运行日志 |

### 分步实现

#### 步骤1：Workhorse 在架构中的位置

**目标**：理解 Workhorse 的请求分发逻辑和与各组件的关系。

```
                    ┌─────────────┐
  git push/pull     │   用户请求    │    API / Web UI
  (大文件传输)        └──────┬──────┘
                    ┌───────▼───────┐
                    │    Nginx      │  (端口 80/443)
                    └───────┬───────┘
                    ┌───────▼───────┐
                    │   Workhorse   │  (端口 8181 - 内部监听)
                    │   ⭐ Go 代理   │
                    └──┬───┬───┬───┘
         ┌─────────────┘   │   └─────────────┐
         ▼                 ▼                 ▼
   ┌──────────┐    ┌──────────┐     ┌──────────┐
   │  Rails   │    │  Gitaly  │     │  对象存储 │
   │ (鉴权)   │    │ (Git 操作)│     │ (上传文件)│
   └──────────┘    └──────────┘     └──────────┘
   ┌──────────┐
   │ PostgreSQL│
   └──────────┘
```

#### 步骤2：Workhorse 核心处理流程（Go 源码追踪）

**目标**：阅读 Workhorse 的核心分发逻辑，理解四种请求处理模式。

```go
// ====== 文件：internal/upstream/upstream.go（简化版）======

// Workhorse 的请求入口 —— 所有经过 Nginx 的请求都经过这里
func (u *upstream) ServeHTTP(w http.ResponseWriter, r *http.Request) {
    // ⭐ 第1步：发送 pre-authorization 请求到 Rails
    // 目标 URL: http://localhost:8080/api/v4/internal/allowed
    // 请求头包含原始请求的所有信息（URL, method, headers）
    // 但不包含请求体（body）—— 大文件不会发给 Rails
    preAuth, err := u.preAuthorize(r)

    if err != nil {
        http.Error(w, "Failed to authorize", http.StatusInternalServerError)
        return
    }

    // ⭐ 第2步：根据 Rails 的响应类型决定后续处理
    switch preAuth.ResponseType {
    case "proxy":
        // 模式1：普通请求 → 代理给 Rails Puma
        // 适用：API 请求、页面渲染
        u.proxyToRails(w, r)

    case "sendfile":
        // 模式2：文件下载 → Workhorse 直接发送文件（绕过 Rails）
        // Rails 返回文件路径，Workhorse 用 Go 的 http.ServeFile 发送
        http.ServeFile(w, r, preAuth.FilePath)

    case "accelerated_upload":
        // 模式3：文件上传加速 → Workhorse 直接写磁盘/对象存储
        // Rails 返回上传目标路径，Workhorse 将 multipart body 写入
        u.handleUpload(w, r, preAuth)

    case "git_receive_pack", "git_upload_pack":
        // 模式4：Git 操作 → Workhorse 代理给 Gitaly
        // 数据流：客户端 ↔ Nginx ↔ Workhorse ↔ Gitaly (gRPC)
        // Rails 只参与 pre-auth 阶段
        u.proxyToGitaly(w, r, preAuth)
    }
}

// ⭐ Pre-authorization 的实现
func (u *upstream) preAuthorize(r *http.Request) (*PreAuthResponse, error) {
    // 构造发往 Rails 的内部请求
    preAuthReq, _ := http.NewRequest("GET",
        "http://localhost:8080/api/v4/internal/allowed", nil)

    // 复制原始请求的关键信息
    preAuthReq.Header.Set("X-Original-URL", r.URL.String())
    preAuthReq.Header.Set("X-Original-Method", r.Method)

    // 发送请求并等待 Rails 响应
    resp, err := u.httpClient.Do(preAuthReq)
    // 解析 Rails 的 JSON 响应 → PreAuthResponse 结构体
    // 包含：ResponseType, FilePath, GitalyAddress 等
}
```

#### 步骤3：大文件上传加速机制

**目标**：理解 Workhorse 如何处理 multipart 文件上传而不消耗 Rails 内存。

```go
// ====== 文件：internal/upload/uploads.go（简化版）======

// Workhorse 的上传处理
func (s *Server) handleUpload(w http.ResponseWriter, r *http.Request, auth *PreAuthResponse) {
    // ⭐ auth 是从 Rails pre-auth 得到的上传指令
    // 包含：目标存储路径、对象存储凭证、文件大小限制

    // 1. 解析 multipart form
    //    但不同于 Rails——Workhorse 使用流式解析
    //    文件内容不会全部加载到内存中
    mr, err := r.MultipartReader()

    // 2. 流式写入目标
    //    每次从请求中读取一小块（如 32KB）
    //    立即写入目标存储（本地磁盘或 S3）
    //    逐块处理 → 内存占用恒定
    for {
        part, err := mr.NextPart()
        if err == io.EOF { break }

        writer, err := s.getUploadWriter(auth.UploadPath)
        // 3. 流式复制：客户端 → Workhorse → 存储
        io.CopyBuffer(writer, part, make([]byte, 32*1024))
    }

    // ⭐ 关键：Rails 在这个流程中只做了一件事——鉴权
    // 1GB 文件上传过程中，Rails 进程的内存占用 < 10MB
}
```

**验证 Workhorse 的上传加速**：

```bash
# 查看 Workhorse 是否正常运行
sudo gitlab-ctl status gitlab-workhorse
# 输出：run: gitlab-workhorse: (pid 1234) 3600s

# 查看 Workhorse 监听的端口
sudo netstat -tlnp | grep 8181
# 输出：tcp  0  0 127.0.0.1:8181  LISTEN  1234/gitlab-workhorse

# 测试文件上传（观察 Rails 内存）
# 在另一个终端监控 Puma 内存：
watch -n 1 "ps aux | grep puma | awk '{print \$6}'"
# 上传 1GB 文件时，Puma 内存应保持稳定（< 200MB）

# 对比：如果绕过 Workhorse 直接代理给 Rails
# Puma 内存会飙升到 1GB+
```

#### 步骤4：Git Push/Pull 流式代理

**目标**：理解 Workhorse 如何将 Git 流量旁路到 Gitaly。

```go
// ====== 文件：internal/gitaly/gitaly.go（简化版）======

// Git Push 的数据流
// 客户端 git push → Nginx (HTTP) → Workhorse → Gitaly (gRPC)
// Rails 只在 pre-auth 阶段了解"有人在 push"，不接触 push 数据

func (c *Client) StreamGitPush(w http.ResponseWriter, r *http.Request, auth *PreAuthResponse) {
    // 1. 建立到 Gitaly 的 gRPC 连接
    conn, err := grpc.Dial(auth.GitalyAddress)

    // 2. 创建双向流（bidirectional streaming）
    //    gRPC 的 streaming 模式支持大文件分块传输
    stream, err := gitalyClient.SmartHTTP_PostUploadPack(context.Background())

    // 3. 将客户端的 HTTP 请求体流式转发到 Gitaly
    //    逐块读取 → 逐块通过 gRPC stream 发送
    go func() {
        io.CopyBuffer(streamWriter, r.Body, make([]byte, 32*1024))
        stream.CloseSend()
    }()

    // 4. 将 Gitaly 的响应流式转发回客户端
    io.Copy(w, streamReader)
}
```

#### 步骤5：Workhorse 运维与日志分析

```bash
# ═══════ Workhorse 日志分析 ═══════

# 1. 查看 Workhorse 日志（排查 pre-auth 失败）
sudo gitlab-ctl tail gitlab-workhorse | grep -E "error|preAuthorize"
# 常见日志行：
# "preAuthorize: got status 403"
# → Rails 拒绝了权限（用户没有 push 权限）
# "preAuthorize: got status 500"
# → Rails 内部错误

# 2. 查看 Workhorse 的请求耗时
sudo gitlab-ctl tail gitlab-workhorse | grep "duration"
# 输出：duration=0.452s   ← 请求处理耗时

# 3. 查看请求分发统计
curl -s http://localhost:9229/metrics | grep "gitlab_workhorse"
# 关键指标：
# gitlab_workhorse_http_requests_total{type="proxy"}   # 代理给 Rails 的次数
# gitlab_workhorse_http_requests_total{type="sendfile"} # 直接发文件的次数
# gitlab_workhorse_gitaly_connections                   # 到 Gitaly 的连接数

# 4. 健康检查
curl http://localhost:8181/-/health
curl http://localhost:9229/-/readiness
```

### 完整代码清单

- `internal/upstream/upstream.go`：请求分发核心逻辑
- `internal/upload/uploads.go`：文件上传加速
- `internal/gitaly/gitaly.go`：Git 流式代理
- Workhorse 日志分析命令集

### 测试验证

```bash
# 验证1：Workhorse 正常运行
sudo gitlab-ctl status gitlab-workhorse
curl -s http://localhost:8181/-/health && echo "✅ Workhorse healthy"

# 验证2：pre-authorization 流程
# 在 Rails 日志中查看 pre-auth 请求
sudo grep "internal/allowed" /var/log/gitlab/puma/puma_stdout.log | tail -5

# 验证3：大文件上传不阻塞 Rails
# 启动一个 1GB 文件上传任务
# 同时观察 Puma 内存：watch -n 1 "ps aux | grep puma"
# Puma RSS 应保持稳定

# 验证4：Workhorse 到 Gitaly 的连接
curl -s http://localhost:9229/metrics | grep gitaly_connections
```

## 4. 项目总结

### Workhorse 四种处理模式对比

| 模式 | 触发场景 | Rails 参与度 | 数据流路径 | 典型耗时 |
|------|---------|------------|-----------|---------|
| `proxy` | API / Web 页面 | 全程参与 | 客户端→WH→Rails→WH→客户端 | 50-500ms |
| `sendfile` | 附件/Artifact 下载 | 仅鉴权 | 客户端→WH→磁盘→客户端 | 文件大小/网速 |
| `accelerated_upload` | 附件/Artifact 上传 | 仅鉴权 | 客户端→WH→磁盘 | 文件大小/网速 |
| `git_*` | git push/pull | 仅鉴权 | 客户端→WH→Gitaly(streaming) | 1-30s (取决于仓库大小) |

### 适用场景

- **理解 GitLab 架构**：Workhorse 是连接 Nginx、Rails、Gitaly 的"胶水层"
- **排查大文件传输问题**：git push/pull 超时、附件上传失败
- **自定义请求处理**：在 Workhorse 层添加请求头、认证逻辑等

### 注意事项

- **Workhorse 是静态编译的 Go 二进制**：修改源码需要重新编译并替换 `/opt/gitlab/embedded/bin/gitlab-workhorse`
- **Workhorse 的 pre-auth 请求必须成功**：如果 Rails 不可用，所有请求都会失败
- **Workhorse 的日志默认输出到 systemd journal**：`journalctl -u gitlab-workhorse`

### 常见踩坑经验

1. **Workhorse 一直返回 502**：Workhorse 无法连接到 Rails Puma。根因：Puma bind 在 Unix socket，Workhorse 配置的 TCP 端口不对。解决：检查 Workhorse 配置文件中的 `auth_socket` 路径。
2. **大文件上传到一半断开**：Workhorse 的请求超时太短。根因：`proxy_read_timeout` 默认 60 秒。解决：在 `/etc/gitlab/gitlab.rb` 中增加 `workhorse['proxy_headers_timeout'] = '5m'`。
3. **git push 被 Workhorse 拒绝但 Rails 日志没记录**：Workhorse 和 Rails 之间通信失败但错误日志在 Workhorse 端。根因：只看 Rails 日志漏了 Workhorse 的日志。解决：`sudo gitlab-ctl tail gitlab-workhorse` 同时查看。

### 思考题

1. Workhorse 的 pre-authorization 模式意味着 Rails 必须为每个请求做鉴权。如果 Rails 宕机，所有 Git 操作都会失败。如何设计一个降级方案，让 Git 读取操作（`git pull`、`git clone`）在 Rails 宕机时仍然可用？
2. Workhorse 目前不支持动态路由配置（所有路由规则硬编码在 Go 源码中）。如果要支持"按 URL 前缀动态选择后端"——比如 `gitlab.local/team-a/*` 路由到 Rails 集群 A，`gitlab.local/team-b/*` 路由到集群 B——应该如何修改 Workhorse 的架构？

> 答案见附录 D。

### 推广计划提示

- **运维**：Workhorse 日志是排查"请求没到达 Rails"类问题的第一现场
- **开发**：如果要给 GitLab 添加自定义的请求层功能（TraceID、限流、白名单），Workhorse 层是最合适的位置——它处理所有请求，且性能高
- **架构师**：Workhorse 的"pre-auth + 智能路由"模式可以借鉴到其他网关的设计中
