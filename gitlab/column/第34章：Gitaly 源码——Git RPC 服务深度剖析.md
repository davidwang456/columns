# 第34章：Gitaly 源码——Git RPC 服务深度剖析

## 1. 项目背景

> **业务场景**：某公司 GitLab 实例的运维团队接连遇到三次 Gitaly 相关故障。第一次，`git push` 频繁超时（3-5 秒的 push 操作等了 30 秒），查看 Gitaly 日志有大量 `UserFFBranch` RPC 超时。第二次，运维执行了 `gitlab-rake gitlab:git:gc` 想清理大仓库，结果 GC 操作阻塞了所有其他 Git 请求。第三次，一个 10GB 的大仓库在执行 `git clone` 时导致 Gitaly 节点的内存飙升到 32GB，触发 OOM kill。

每次故障后运维都只能重启 Gitaly 缓解，但不理解根因。他们知道 Gitaly 是 GitLab 的 Git 操作执行层，但不知道——Gitaly 内部有哪些 RPC 服务？每个 RPC 的资源消耗特征是什么？Gitaly 如何管理磁盘上的 Git 仓库？hooks 注入机制是怎么工作的？

**痛点放大**：Gitaly 是 GitLab 架构中最关键的存储引擎——它决定了一个 `git clone` 是 5 秒还是 50 秒，决定了一个 MR 的 diff 是瞬间显示还是转圈 30 秒。不理解 Gitaly 的内部工作原理，就无法对 GitLab 做任何有意义的性能调优。

## 2. 项目设计——剧本式交锋对话

**场景**：运维工位，三个人围着一台 Gitaly 服务器的 Grafana 面板，上面的 RPC 延迟曲线在上午 10 点准时飙升。

---

**运维小王**（指着曲线）："你们看——每天上午 10 点，`PackObjects` 和 `CommitDiff` 这两个 RPC 的延迟同时飙到 2 秒。但 CPU 和内存都很低，IO 也很低。这不对啊——没人 push 也没人创建 MR，为什么 Gitaly 突然忙起来了？"

**大师**："上午 10 点是团队 standup 结束的时间——所有人同时开始工作，打开 GitLab 看自己的 MR 列表。每个 MR 在渲染页面时都会调用 `CommitDiff` 来计算 diff 摘要。`PackObjects` 也是——有人在做 `git fetch` 更新本地仓库。这是典型的'雷鸣群体效应'（thundering herd）——不是操作本身慢，而是同时来了大量操作。"

**小胖**："那 Gitaly 不是有并发控制吗？不能排队吗？"

**大师**："Gitaly 确实有并发控制——可以通过 `gitaly['concurrency']` 配置每个 RPC 的最大并发数。但默认配置比较宽松，比如 `CommitDiff` 默认允许 10 个并发。10 个人同时打开 MR 列表没问题，但 100 个人同时打开就会排队。你可以通过限制并发数来保护 Gitaly 不被突发流量压垮——但代价是用户看到'正在加载'的时间会变长。"

**小胖**："那上次 GC 阻塞所有请求呢？我以为 GC 是在后台慢慢跑的。"

**大师**："`git gc` 是一个非常重的操作——它要遍历仓库中所有的对象，重新打包，压缩，写入新文件，删除旧文件。默认情况下 Gitaly 的 `GarbageCollect` RPC 没有并发限制——它可以同时处理多个 GC 请求。但这不是最危险的——最危险的是 `GarbageCollect` 和普通 RPC（如 `FetchRemote`）之间没有优先级隔离。技术映射——GC 就像在饭点打扫厨房，你可以打扫，但别在厨师炒菜的时候堵着灶台。"

**小白**："Gitaly 的 gRPC 服务接口有多少个？我想知道到底哪些操作是重 RPC、哪些是轻 RPC。"

**大师**："Gitaly 定义了约 12 个 gRPC Service、100+ 个 RPC 方法。按操作类型可以分为四类：仓库管理（CreateRepository、GarbageCollect、FetchRemote 等）——通常最重；提交查询（CommitDiff、ListCommits、FindCommits 等）——中等重量；引用操作（FindBranch、CreateBranch、DeleteRefs 等）——通常较轻；Blob 操作（GetBlob、GetTreeEntries 等）——轻量但频繁。理解每类的资源特征，你才能合理配置并发限制。"

**小胖**："Hooks 注入呢？为什么我不能直接在 Gitaly 上配置 pre-receive hook？"

**大师**："因为 GitLab 的 hooks 不只是简单的 shell 脚本——它们需要回调 Rails 的 internal API 来做权限验证。Git push 时，Gitaly 注入的 pre-receive hook 会通过 HTTP 请求问 Rails：'这个用户能不能往这个分支 push？'。如果 Rails 说不能（403），Gitaly 就拒绝 push。如果你直接在 Gitaly 上配置静态 hook 脚本，就绕过了 GitLab 的权限系统。技术映射——Gitaly 的 hooks 就像商场门禁：刷卡后门禁不是自己判断，而是打电话问总控室'这个人能进吗'。"

---

## 3. 项目实战

### 环境准备

| 工具 | 用途 |
|------|------|
| GitLab Omnibus 17.x（含 Gitaly） | 生产级 Gitaly 环境 |
| Gitaly 源码 | `git clone https://gitlab.com/gitlab-org/gitaly.git` (Go) |
| grpcurl | 直接调试 gRPC 接口 |
| Prometheus + Grafana | 监控 Gitaly 指标 |

### 分步实现

#### 步骤1：Gitaly 核心架构与 RPC 服务全景

**目标**：理解 Gitaly 的组件关系和所有 RPC 服务分类。

```
┌──────────────────────────────────────────────────────────────┐
│                     GitLab Rails（Ruby 客户端）                │
│  gitaly-ruby gem: lib/gitlab/git/repository.rb               │
│  → GitalyClient.call(storage, :commit_service, :commit_diff) │
└─────────────────────┬────────────────────────────────────────┘
                      │ gRPC (HTTP/2 + Protobuf)
                      ▼
┌──────────────────────────────────────────────────────────────┐
│                  Praefect（代理层，可选，HA 时使用）            │
│  → 读请求：任意 Secondary 或 Primary                          │
│  → 写请求：路由到 Primary + 同步给 Secondary（Quorum）         │
└─────────────────────┬────────────────────────────────────────┘
                      │
                      ▼
┌──────────────────────────────────────────────────────────────┐
│                   Gitaly Server（Go 服务端）                   │
│                                                              │
│  ┌─────────────────────────────────────────────────────┐     │
│  │ gRPC Services (12 services, ~100 RPCs)               │     │
│  ├─────────────────────────────────────────────────────┤     │
│  │ RepositoryService  - 仓库 CRUD + GC + FetchRemote    │     │
│  │ CommitService      - 提交查询 + diff                  │     │
│  │ DiffService        - 文件差异比较                      │     │
│  │ RefService         - 分支/标签操作                     │     │
│  │ OperationService   - 用户 Git 操作(push/merge/rebase) │     │
│  │ HookService        - Hooks 管理                       │     │
│  │ BlobService        - 文件内容读写                      │     │
│  │ ObjectPoolService  - 对象池（Fork 优化）               │     │
│  │ ConflictsService   - 合并冲突检测                      │     │
│  │ RemoteService      - 远程仓库交互                      │     │
│  │ ServerService      - 服务器状态                        │     │
│  │ SmartHTTP          - HTTP Git 传输                     │     │
│  └─────────────────────────────────────────────────────┘     │
│                                                              │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐       │
│  │   libgit2    │  │   git CLI    │  │  Hook Manager │       │
│  │  (C 库绑定)   │  │ (调用系统git) │  │  (注入hooks)  │       │
│  └──────────────┘  └──────────────┘  └──────────────┘       │
│                                                              │
│  ┌──────────────────────────────────────────────────┐       │
│  │  Storage Layer (internal/gitaly/storage/)         │       │
│  │  → @hashed 目录布局                                │       │
│  │  → 每个仓库 = 一个裸 Git 仓库目录                   │       │
│  └──────────────────────────────────────────────────┘       │
└──────────────────────────────────────────────────────────────┘
```

#### 步骤2：核心 RPC 接口（Protobuf 定义与调用分析）

**目标**：阅读 Gitaly 的 proto 文件，理解关键 RPC 的请求/响应结构。

```protobuf
// ====== 文件：gitaly/proto/repository.proto（简化）======

service RepositoryService {
  // 仓库 CRUD
  rpc CreateRepository(CreateRepositoryRequest) returns (CreateRepositoryResponse);
  rpc RemoveRepository(RemoveRepositoryRequest) returns (RemoveRepositoryResponse);

  // 对象同步
  rpc FetchRemote(FetchRemoteRequest) returns (FetchRemoteResponse);
  // ⭐ git clone / git fetch 时调用
  // 请求：远程 URL + 分支名
  // 响应：同步结果

  // 垃圾回收（⭐ 最重的操作之一）
  rpc GarbageCollect(GarbageCollectRequest) returns (GarbageCollectResponse);
  // 内部执行 git gc --auto

  // 打包对象（⭐ git clone 大仓库时的核心 RPC）
  rpc PackObjects(PackObjectsRequest) returns (stream PackObjectsResponse);
  // ⚠️ 使用 server-side streaming —— 分批传输数据
  // 请求：仓库引用 + 要发送的 commit 范围
  // 响应：流式返回打包后的 Git 对象

  // 仓库统计
  rpc RepositorySize(RepositorySizeRequest) returns (RepositorySizeResponse);
}

// ====== 文件：gitaly/proto/operations.proto（用户 Git 操作）======

service OperationService {
  // ⭐⭐⭐ 用户 git push 的核心处理 RPC
  rpc UserFFBranch(UserFFBranchRequest) returns (UserFFBranchResponse);
  // 处理 fast-forward 推送（常见于 main 分支的常规推送）
  // 请求：user, repository, branch, commit_sha
  // 内部：验证权限 → 更新 ref → 执行 post-receive hook

  rpc UserMergeBranch(UserMergeBranchRequest) returns (UserMergeBranchResponse);
  // 处理 MR 合并操作

  rpc UserRebaseConfirmable(UserRebaseConfirmableRequest) returns (UserRebaseConfirmableResponse);
  // 处理 rebase 操作（用于 Rebase and Merge 模式）
}

// ====== 文件：gitaly/proto/commit.proto ======

service CommitService {
  rpc ListCommits(ListCommitsRequest) returns (stream ListCommitsResponse);

  // ⭐ MR diff 计算的核心 RPC
  rpc CommitDiff(CommitDiffRequest) returns (stream CommitDiffResponse);
  // 请求：left_commit_id, right_commit_id, paths(可选), max_files, max_lines
  // 响应：流式返回每个文件的 diff

  rpc FindCommits(FindCommitsRequest) returns (stream FindCommitsResponse);
}
```

#### 步骤3：仓库存储模型与哈希布局

**目标**：理解 Gitaly 如何组织磁盘上的 Git 仓库。

```bash
# Gitaly 的仓库磁盘布局
/var/opt/gitlab/git-data/
└── repositories/
    └── @hashed/                          # GitLab 10.0+ 默认为哈希目录
        ├── 4b/
        │   └── 22/
        │       └── 4b227777d4dd1fc61c6f884f48641d02b4d121d3fd32860608/
        │           .git/                  # Git 裸仓库
        │           ├── HEAD
        │           ├── config
        │           ├── objects/           # ⭐ Git 对象存储（最大目录）
        │           │   ├── pack/          # 打包后的对象文件 (.pack)
        │           │   └── info/
        │           └── refs/
        │               ├── heads/         # 分支引用
        │               └── tags/          # 标签引用
        ├── a1/
        │   └── 7c/
        │       └── ...
        └── ...

# 哈希计算逻辑：
# SHA256(project_id.to_s) → 十六进制字符串
# 前 2 字符 = 第1级目录 (4b)
# 第 3-4 字符 = 第2级目录 (22)
# 剩余 = 第3级目录 (仓库路径)
# 目的：将数百万仓库均匀分散到文件系统层级，避免单目录文件过多
```

**Go 源码中的存储定位**：

```go
// 文件：internal/gitaly/storage/locator.go
type Locator interface {
  // 根据 storage_name 和 relative_path 定位仓库的完整磁盘路径
  GetRepoPath(storageName string, relativePath string) (string, error)

  // 获取仓库的 Git 备用对象目录路径
  GetObjectPoolPath(storageName string, relativePath string) (string, error)
}

// 实际实现（internal/gitaly/storage/storage_locator.go）
func (l *storageLocator) GetRepoPath(storageName, relativePath string) (string, error) {
  storage, ok := l.storages[storageName]
  if !ok {
    return "", fmt.Errorf("storage not found: %s", storageName)
  }
  // 拼接路径：/var/opt/gitlab/git-data/repositories/@hashed/ab/cd/xxx.git
  return filepath.Join(storage.Path, relativePath), nil
}
```

#### 步骤4：Git Hooks 注入机制——Gitaly 如何实现权限控制

**目标**：追踪 `git push` 时 hooks 注入的全流程。

```go
// ===== 第1步：Gitaly 收到 UserFFBranch RPC 请求 =====
// 文件：internal/gitaly/service/operations/user_ff_branch.go

func (s *Server) UserFFBranch(req *gitalypb.UserFFBranchRequest,
  stream gitalypb.OperationService_UserFFBranchServer) error {

  // 获取仓库路径
  repoPath, err := s.locator.GetRepoPath(req.Repository.StorageName,
    req.Repository.RelativePath)

  // ⭐ 第2步：在执行 Git 命令前设置 hooks 环境
  // 文件：internal/git/hooks/hooks.go
  hookEnv := buildHookEnvironment(req.Repository, req.User)

  // 注入环境变量，告诉 hook 脚本：
  //  - GL_ID: 当前用户的 GitLab 用户 ID
  //  - GL_USERNAME: 当前用户名
  //  - GL_PROTOCOL: ssh 或 http
  //  - GITALY_HOOKS_DIR: Gitaly 内的 hooks 脚本目录

  // ⭐ 第3步：执行 git update-ref（更新分支引用）
  // 在执行前，Git 会自动调用 pre-receive hook
  // → pre-receive hook 脚本（Go 编译的二进制）
  // → 通过 HTTP POST 调用 Rails 的 internal API:
  //    POST /api/v4/internal/allowed
  //    Body: { action: "git-receive-pack", ref: "refs/heads/main",
  //            oldrev: "abc123", newrev: "def456", user_id: 42 }

  // ⭐ 第4步：Rails 返回权限判断结果
  // 文件：lib/api/internal/base.rb（Rails 端）
  // post '/allowed' do
  //   result = Gitlab::GitAccess.new(user, project).check(action, changes)
  //   result.allowed? → 200 OK  /  result.denied? → 403 Forbidden
  // end

  // 如果 Rails 返回 403 → Gitaly 终止操作 → 客户端看到错误信息
  // 如果 Rails 返回 200 → 继续执行 git update-ref
}
```

#### 步骤5：用 grpcurl 直接调试 Gitaly

**目标**：不通过 Rails，直接用 grpcurl 测试 Gitaly RPC。

```bash
# 1. 安装 grpcurl
go install github.com/fullstorydev/grpcurl/cmd/grpcurl@latest

# 2. 列出所有 gRPC 服务
grpcurl -plaintext localhost:8075 list
# 输出：
# gitaly.RepositoryService
# gitaly.CommitService
# gitaly.OperationService
# ...

# 3. 查看 RepositoryService 的所有方法
grpcurl -plaintext localhost:8075 list gitaly.RepositoryService

# 4. 查看 CommitDiff 的请求/响应 protobuf 定义
grpcurl -plaintext localhost:8075 describe gitaly.CommitService.CommitDiff

# 5. 实际调用 RPC（需要认证 token）
GRPC_TOKEN=$(sudo grep -oP 'token = "\K[^"]+' /var/opt/gitlab/gitaly/config.toml | head -1)

grpcurl -plaintext \
  -H "authorization: Bearer $GRPC_TOKEN" \
  -d '{
    "repository": {
      "storage_name": "default",
      "relative_path": "@hashed/xx/xx/xxxxx.git"
    },
    "left_commit_id": "abc1234",
    "right_commit_id": "def5678"
  }' \
  localhost:8075 gitaly.CommitService/CommitDiff | head -100

# 6. 监控 Gitaly 指标（Prometheus）
curl -s localhost:9236/metrics | grep "gitaly_" | head -20
# 关键指标：
# gitaly_pack_objects_cache_hit_total          # PackObjects 缓存命中
# grpc_server_handled_total                    # gRPC 请求计数
# grpc_server_handling_seconds_bucket          # gRPC 耗时直方图
# gitaly_concurrency_limiting_in_progress      # 当前并发请求数
```

### 完整代码清单

- `gitaly/proto/repository.proto`、`operations.proto`、`commit.proto`：RPC 定义
- `internal/git/hooks/hooks.go`：Hooks 注入机制
- `internal/gitaly/storage/locator.go`：仓库磁盘定位
- grpcurl 调试命令集（步骤5）

### 测试验证

```bash
# 验证1：查看 Gitaly 服务状态
sudo gitlab-ctl status gitaly
sudo gitlab-ctl tail gitaly | head -20

# 验证2：获取 Gitaly 版本和配置
sudo /opt/gitlab/embedded/bin/gitaly --version
sudo cat /var/opt/gitlab/gitaly/config.toml | grep -A 5 "\[auth\]"

# 验证3：用 grpcurl 验证 RPC 连通性
grpcurl -plaintext localhost:8075 list | wc -l
# 应返回 10+ 个服务

# 验证4：模拟权限拒绝
# 在 Rails Console 中暂时将用户权限降级
sudo gitlab-rails runner "
  u = User.find_by(username: 'some-user')
  m = Project.find_by(path: 'some-project').team.member(u)
  m.update!(access_level: Gitlab::Access::GUEST)
"
# 然后尝试 git push → 应被 hooks 拒绝
```

## 4. 项目总结

### Gitaly RPC 分类与资源特征

| RPC 类别 | 代表方法 | CPU 密集度 | 内存占用 | IO 密集度 | 延迟风险 |
|---------|---------|-----------|---------|----------|---------|
| 仓库管理 | GarbageCollect, FetchRemote | 高 | 中 | 极高 | 高（GC 可跑数分钟） |
| 提交查询 | CommitDiff, ListCommits | 中 | 中 | 高 | 中（大仓库 >1s） |
| 对象传输 | PackObjects | 高 | 高（可到 GB 级） | 高 | 高（大仓库 >10s） |
| Blob 操作 | GetBlob, GetTreeEntries | 低 | 低 | 低 | 低（通常 <100ms） |
| 引用操作 | FindBranch, CreateBranch | 极低 | 极低 | 极低 | 极低（<10ms） |

### 适用场景

- **性能调优**：通过 Prometheus 指标定位慢 RPC，针对性优化并发限制
- **故障排查**：通过 grpcurl 直接测试 Gitaly RPC，排除 Rails 层问题
- **容量规划**：根据 RPC 的资源特征估算 Gitaly 节点的配置需求

### 注意事项

- **不要在生产 Gitaly 上随意执行 `git gc --aggressive`**：它会重写所有 pack 文件，可能需要数小时且严重消耗 IO
- **Gitaly 的 gRPC 端口（默认 8075）应仅内网可访问**：不要暴露到公网
- **PackObjectsCache 能显著加速 clone 但占用额外内存**：建议在 16GB+ 内存的 Gitaly 节点上启用

### 常见踩坑经验

1. **Gitaly RPC 超时但 Rails 没报错**：因为 Gitaly gRPC 客户端的超时和 Rails 请求超时是独立的。根因：gRPC 调用在 `lib/gitlab/git/diff.rb` 中没有设置 timeout。解决：在调用时设置 `deadline: 5.seconds`。
2. **git gc 导致所有仓库不可用**：因为 `GarbageCollect` RPC 在同一个 repo 上串行执行，且没有自动排队机制。根因：多个 GC 同时跑会抢占所有 IO。解决：配置 `gitaly['concurrency']` 限制 GC 并发数为 1。
3. **@hashed 目录下出现大量空目录**：项目被删除但磁盘上的仓库目录没被清理。根因：GitLab 删除项目是软删除（标记 `pending_delete`），仓库目录在 Sidekiq Worker 中异步清理。解决：检查 `project_destroy_worker` 的 Sidekiq 队列是否积压。

### 思考题

1. Gitaly 的 `PackObjects` RPC 使用 server-side streaming 模式传输数据。为什么不用 client-side streaming 或 unary 模式？server-side streaming 对于大仓库 clone 有何优势？
2. Gitaly hooks 的回调链路是：Gitaly → Rails Internal API → 数据库。如果 Rails 服务宕机，Gitaly 的 hooks 调用会失败——这会导致所有 git push 被拒绝吗？如何配置 Gitaly 在 Rails 不可用时的降级策略？

> 答案见附录 D。

### 推广计划提示

- **运维**：掌握 Gitaly 的存储模型和 RPC 分类后，磁盘规划、容量估算、故障定位都有了方法论
- **开发**：如果要优化 Gitaly 相关的功能（如 MR diff 加速），需要理解 gRPC streaming 和 PackObjectsCache
- **架构师**：Gitaly 的 RPC 设计（gRPC + Protobuf + 服务拆分）可以作为内部微服务设计的参考
