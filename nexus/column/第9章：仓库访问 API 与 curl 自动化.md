# 第9章：仓库访问 API 与 curl 自动化

## 1. 项目背景

云鲸科技的炮哥每天早上到公司第一件事就是手动登录 Nexus Web UI，检查三个东西：各仓库的容量有没有超过 80%、昨晚 CI 发布的包有没有上传成功、proxy 仓库的远程连接状态是不是绿灯。三步操作要切换四个页面、手动复制粘贴筛选条件，耗时 15 分钟——周末还不能断，因为周一早上如果发现仓库异常，整个研发团队一上午都在等修复。

更头疼的是，测试组每周五需要清理测试环境 SNAPSHOT 仓库中"超过 7 天且名称包含 test-"的过期组件，每次 80~120 个——手动一个 Browse → 点进目录 → 选中 → Delete → 确认，重复 80 多次，运维实习生小赵干完说手腕酸痛。

"能不能写个脚本自动搞定这些事？"炮哥在一次周会上提问。答案是：**Nexus 提供了一套完整的 REST API，几乎所有在 Web UI 上能做的事，API 都能做**。从仓库管理到组件查询，从资产下载到批量清理，从用户管理到任务调度——API 是将 Nexus 从"手动管理工具"升级为"自动化制品平台"的唯一通道。

本章将带你掌握 Nexus REST API 的使用模式，编写一组可复用的管理脚本，将重复性工作从"手工点击"变成"一行命令"。

## 2. 项目设计

炮哥在自己的工位上开着三个终端窗口，一边写 curl 命令一边抓狂。

**炮哥**："大师，我看 Nexus API 文档说可以用 Basic Auth，但我试了 `curl http://admin:admin123@localhost:8081/service/rest/v1/repositories`，怎么不返回数据？"

**大师**："Basic Auth 确实支持，但你的 curl 命令写法有问题——`http://user:pass@host/path` 这种 URL 嵌入写法在 curl 新版中已被废弃。正确写法是 `curl -u admin:admin123`。另外，Nexus 也支持 API Token 认证，更安全——你可以创建一个 Token 替代明文密码。"

> **技术映射**：Nexus REST API 支持两种认证方式：HTTP Basic Auth（-u user:pass）和 API Token（-H 'Authorization: Bearer NX_TOKEN'）。Token 可以独立吊销，推荐用于脚本和 CI。

**小胖**："那 API 返回的数据怎么处理？我看返回了一长串 JSON，眼睛都花了。"

**大师**："这就是 `jq` 的舞台。Nexus API 响应全部是 JSON 格式，`jq` 是命令行的 JSON 瑞士军刀。比如用 `jq '.[].name'` 只提取仓库名称列表，用 `jq '.items | length'` 统计组件数量。"

**小白**："API 有分页吗？如果仓库里有几千个组件，一次性返回会不会炸？"

**大师**："Nexus 的搜索 API 支持分页——通过 `continuationToken` 机制做游标分页。首次请求不传 token，响应末尾会有 `continuationToken` 字段；下次请求带上这个 token 就能获取下一页。这比传统的 offset/limit 分页更稳定——即使查询期间有新组件上传，也不会出现重复或遗漏。"

> **技术映射**：Nexus 使用游标（cursor-based）分页而非偏移量（offset-based）分页，基于 `continuationToken` 实现，保证结果一致性。

**炮哥**："那我这 80 多个过期组件怎么批量删？API 请求一个接一个发吗？"

**大师**："有两种方案。方案一是循环删除——搜索出符合条件的组件 ID 列表，逐条调用 DELETE `/service/rest/v1/components/{id}`。方案二是利用 Nexus 内置的 Cleanup Policy + Task——创建一条策略匹配合适的条件，关联到仓库，执行清理任务。方案一适合临时性、小批量操作；方案二适合常规化、大规模治理。"

**小胖**："那脚本里面的错误处理呢？网络断了怎么办？API 返回 500 怎么办？"

**大师**："工业级脚本必须处理好三件事：**超时、重试、幂等**。curl 用 `--connect-timeout 5 --max-time 30` 设超时，用 `--retry 3 --retry-delay 10` 做重试。幂等的意思是：重复执行同样的操作，结果一致且不产生副作用——GET 天然幂等，DELETE 第二次返回 404 也是幂等，但 POST 两次会导致重复创建。"

> **技术映射**：管理脚本的工程三原则——连接超时 + 指数退避重试 + 操作幂等性检查。

**小白**："我注意到 API 路径里有些是 `/v1/`，Nexus 有多个 API 版本吗？这些 API 的稳定性承诺是什么？"

**大师**："Nexus 当前主要 API 都在 `/service/rest/v1/` 下。Sonatype 对 API 的兼容性承诺有限——它们可能在版本升级时变化。最佳实践是不要对着 API 写死逻辑，而是封装成函数，API 变化时只改函数体。"

## 3. 项目实战

### 3.1 环境准备

- 已按第 2 章部署好 Nexus 实例
- 已按第 8 章创建好用户和权限
- curl、jq（jq 安装：`choco install jq` / `brew install jq` / `apt install jq`）

### 3.2 分步实战

#### 步骤一：API 探索——从 Web UI F12 到 curl 命令

**目标**：学会从浏览器开发者工具中发现 API 端点。

步骤如下：
1. 打开 Nexus Web UI（`http://localhost:8081`），按 F12 打开开发者工具
2. 切换到 Network 标签页，勾选 "XHR/Fetch" 过滤
3. 在 UI 中执行操作（如点击 "Repositories"）
4. 观察 Network 面板中发起的请求 URL、Method、Headers 和 Response
5. 右键请求 → Copy as cURL，粘贴到终端验证

```bash
# 示例：从 Web UI 中复制出的"仓库列表"请求
curl 'http://localhost:8081/service/rest/v1/repositories' \
  -H 'accept: application/json' \
  -u admin:admin123 | jq '.[].name'

# 预期输出（示例）：
# "maven-central-proxy"
# "maven-public"
# "maven-releases"
# "maven-snapshots"
# "npm-hosted"
# "npm-proxy"
# "npm-public"
# ...
```

#### 步骤二：编写仓库健康检查脚本

**目标**：自动检查所有仓库的在线状态和远程连接。

```bash
#!/bin/bash
# check-repo-health.sh：检查所有仓库状态
NEXUS="http://localhost:8081"
AUTH="admin:admin123"

echo "=== Nexus 仓库健康检查 ==="
echo "时间: $(date '+%Y-%m-%d %H:%M:%S')"
echo ""

# 获取所有仓库
REPOS=$(curl -s -u $AUTH "$NEXUS/service/rest/v1/repositories" | jq -r '.[].name')

OK=0; WARN=0; ERROR=0

for REPO in $REPOS; do
    # 获取仓库详情
    DETAIL=$(curl -s -u $AUTH "$NEXUS/service/rest/v1/repositories/$REPO")
    ONLINE=$(echo "$DETAIL" | jq -r '.online')
    FORMAT=$(echo "$DETAIL" | jq -r '.format')
    TYPE=$(echo "$DETAIL" | jq -r '.type')

    # 检查是否在线
    if [ "$ONLINE" != "true" ]; then
        echo "❌ [$FORMAT/$TYPE] $REPO 离线！"
        ((ERROR++))
        continue
    fi

    # 对 proxy 仓库检查远程连接状态
    if [ "$TYPE" = "proxy" ]; then
        STATUS=$(curl -s -u $AUTH \
          "$NEXUS/service/rest/v1/repositories/$REPO/health-check" | jq -r '.repositoryName // "无状态"')

        # proxy 仓库的远程可用性
        REMOTE_URL=$(echo "$DETAIL" | jq -r '.proxy.remoteUrl // "N/A"')
        AUTO_BLOCKED=$(echo "$DETAIL" | jq -r '.httpClient.autoBlock // false')

        if echo "$STATUS" | grep -qi "unavailable"; then
            echo "⚠️  [$FORMAT/proxy] $REPO -> $REMOTE_URL 不可达"
            ((WARN++))
        else
            echo "✅ [$FORMAT/proxy] $REPO -> $REMOTE_URL (正常)"
            ((OK++))
        fi
    else
        echo "✅ [$FORMAT/$TYPE] $REPO (在线)"
        ((OK++))
    fi
done

echo ""
echo "=== 检查完成: 正常=$OK  告警=$WARN  错误=$ERROR ==="

if [ $WARN -gt 0 ] || [ $ERROR -gt 0 ]; then
    exit 1
fi
```

```bash
chmod +x check-repo-health.sh && ./check-repo-health.sh
```

#### 步骤三：编写组件查询与批量下载脚本

**目标**：按时搜索组件，下载所有匹配的资产文件。

```bash
#!/bin/bash
# search-download.sh：搜索组件并批量下载资产
NEXUS="http://localhost:8081"
AUTH="admin:admin123"
REPO="${1:-maven-releases}"
KEYWORD="${2:-cloudwhale}"
OUTDIR="${3:-./downloads}"

mkdir -p "$OUTDIR"

echo "=== 搜索仓库 $REPO 中 '$KEYWORD' 相关的组件 ==="

# 分页搜索（处理 continuationToken）
TOKEN=""
PAGE=0

while true; do
    ((PAGE++))
    if [ -z "$TOKEN" ]; then
        RESP=$(curl -s -u $AUTH \
          "$NEXUS/service/rest/v1/search?repository=$REPO&name=$KEYWORD")
    else
        # URL-encode the continuationToken
        ENCODED_TOKEN=$(echo -n "$TOKEN" | jq -sRr @uri)
        RESP=$(curl -s -u $AUTH \
          "$NEXUS/service/rest/v1/search?repository=$REPO&name=$KEYWORD&continuationToken=$ENCODED_TOKEN")
    fi

    # 提取当前页的组件的全部 assets 的下载路径
    echo "$RESP" | jq -r '.items[].assets[].downloadUrl' | while read -r URL; do
        if [ -n "$URL" ]; then
            FILENAME=$(basename "$URL")
            echo "  下载: $FILENAME"
            curl -s -u $AUTH -o "$OUTDIR/$FILENAME" "$URL"
        fi
    done

    # 检查是否有下一页
    TOKEN=$(echo "$RESP" | jq -r '.continuationToken // empty')
    if [ -z "$TOKEN" ]; then
        break
    fi
    echo "--- 第 $PAGE 页完成，继续下一页 ---"
done

echo "=== 下载完成，文件保存在 $OUTDIR/ ==="
ls -lh "$OUTDIR/"
```

```bash
chmod +x search-download.sh
./search-download.sh maven-releases cloudwhale ./my-downloads
```

#### 步骤四：编写批量删除过期 SNAPSHOT 组件脚本

**目标**：自动清理测试环境中的过期制品。

```bash
#!/bin/bash
# cleanup-old-snapshots.sh：删除指定天数前的 SNAPSHOT 组件
NEXUS="http://localhost:8081"
AUTH="admin:admin123"
REPO="${1:-maven-snapshots}"
DAYS_OLD="${2:-7}"
DRY_RUN="${3:-true}"  # 默认试运行，设为 false 执行真实删除

echo "=== 清理仓库 $REPO 中 $DAYS_OLD 天前的 SNAPSHOT 组件 ==="
echo "模式: $([ "$DRY_RUN" = "true" ] && echo '试运行(Dry-Run)' || echo '真实删除')"

CUTOFF_DATE=$(date -d "$DAYS_OLD days ago" +%s 2>/dev/null || date -v-${DAYS_OLD}d +%s)
DELETED=0
SKIPPED=0
TOKEN=""

while true; do
    if [ -z "$TOKEN" ]; then
        RESP=$(curl -s -u $AUTH \
          "$NEXUS/service/rest/v1/search?repository=$REPO")
    else
        ENCODED_TOKEN=$(echo -n "$TOKEN" | jq -sRr @uri)
        RESP=$(curl -s -u $AUTH \
          "$NEXUS/service/rest/v1/search?repository=$REPO&continuationToken=$ENCODED_TOKEN")
    fi

    # 遍历每个组件
    ITEM_COUNT=$(echo "$RESP" | jq '.items | length')
    for ((i=0; i<ITEM_COUNT; i++)); do
        COMP_ID=$(echo "$RESP" | jq -r ".items[$i].id")
        COMP_NAME=$(echo "$RESP" | jq -r ".items[$i].name")
        COMP_VERSION=$(echo "$RESP" | jq -r ".items[$i].version")

        # 仅处理 SNAPSHOT 版本
        if [[ "$COMP_VERSION" != *"SNAPSHOT"* ]]; then
            ((SKIPPED++))
            continue
        fi

        # 获取组件的最后修改时间（取第一个 asset 的时间）
        LAST_MODIFIED=$(echo "$RESP" | jq -r ".items[$i].assets[0].lastModified")
        LAST_MODIFIED_TS=$(date -d "$LAST_MODIFIED" +%s 2>/dev/null || date -jf "%Y-%m-%dT%H:%M:%S" "$LAST_MODIFIED" +%s 2>/dev/null)

        if [ "$LAST_MODIFIED_TS" -lt "$CUTOFF_DATE" ]; then
            echo "  [过期] $COMP_NAME:$COMP_VERSION (修改于 $LAST_MODIFIED)"
            if [ "$DRY_RUN" != "true" ]; then
                HTTP_CODE=$(curl -s -u $AUTH -X DELETE \
                  -w "%{http_code}" -o /dev/null \
                  "$NEXUS/service/rest/v1/components/$COMP_ID")
                echo "    删除结果: HTTP $HTTP_CODE"
            fi
            ((DELETED++))
        else
            ((SKIPPED++))
        fi
    done

    TOKEN=$(echo "$RESP" | jq -r '.continuationToken // empty')
    if [ -z "$TOKEN" ]; then
        break
    fi
done

echo ""
echo "=== 清理统计 ==="
echo "待删除（过期）: $DELETED 个组件"
echo "保留（有效）:   $SKIPPED 个组件"
if [ "$DRY_RUN" = "true" ]; then
    echo "提示：这是试运行，未执行实际删除。执行真实删除请加参数 false"
fi
```

```bash
chmod +x cleanup-old-snapshots.sh
# 试运行
./cleanup-old-snapshots.sh maven-snapshots 7 true
# 确认无误后执行真实删除
# ./cleanup-old-snapshots.sh maven-snapshots 7 false
```

#### 步骤五：创建 API 通用工具函数库

**目标**：将常用 API 调用封装成可复用的 Shell 函数库。

```bash
#!/bin/bash
# nexus-api-lib.sh：Nexus REST API 通用函数库
# 使用方法：source nexus-api-lib.sh

export NEXUS_URL="${NEXUS_URL:-http://localhost:8081}"
export NEXUS_USER="${NEXUS_USER:-admin}"
export NEXUS_PASS="${NEXUS_PASS:-admin123}"

_curl() {
    curl -s -u "${NEXUS_USER}:${NEXUS_PASS}" \
         --connect-timeout 5 --max-time 30 \
         --retry 3 --retry-delay 5 \
         "$@"
}

# ---- 仓库管理 ----
nexus_list_repos() {
    _curl "$NEXUS_URL/service/rest/v1/repositories" | jq -r '.[].name'
}

nexus_get_repo() {
    local repo="$1"
    _curl "$NEXUS_URL/service/rest/v1/repositories/$repo" | jq .
}

nexus_create_maven_hosted() {
    local name="$1" version_policy="${2:-RELEASE}" write_policy="${3:-ALLOW_ONCE}"
    _curl -X POST "$NEXUS_URL/service/rest/v1/repositories/maven/hosted" \
      -H "Content-Type: application/json" \
      -d "{
        \"name\": \"$name\",
        \"online\": true,
        \"storage\": {\"blobStoreName\": \"default\", \"writePolicy\": \"$write_policy\"},
        \"maven\": {\"versionPolicy\": \"$version_policy\", \"layoutPolicy\": \"STRICT\"}
      }"
}

# ---- 组件管理 ----
nexus_search_components() {
    local repo="$1" keyword="${2:-}"
    local url="$NEXUS_URL/service/rest/v1/search?repository=$repo"
    [ -n "$keyword" ] && url="$url&name=$keyword"
    _curl "$url" | jq .
}

nexus_delete_component() {
    local component_id="$1"
    _curl -X DELETE -w "%{http_code}" -o /dev/null \
      "$NEXUS_URL/service/rest/v1/components/$component_id"
}

nexus_get_component_assets() {
    local component_id="$1"
    _curl "$NEXUS_URL/service/rest/v1/components/$component_id" | \
      jq '.assets[] | {id, path, downloadUrl, checksum}'
}

# ---- 健康检查 ----
nexus_status() {
    _curl "$NEXUS_URL/service/rest/v1/status" | jq .
}

nexus_writable() {
    _curl "$NEXUS_URL/service/rest/v1/status/writable" | jq .
}

# ---- 用户管理 ----
nexus_list_users() {
    _curl "$NEXUS_URL/service/rest/v1/security/users" | jq .
}

nexus_get_user() {
    _curl "$NEXUS_URL/service/rest/v1/security/users?userId=$1" | jq .
}

# 加载后提示
echo "Nexus API 函数库已加载。REPO: $NEXUS_URL"
```

**使用示例**：

```bash
source nexus-api-lib.sh

# 列出所有仓库
nexus_list_repos

# 查看状态
nexus_status

# 搜索组件
nexus_search_components maven-snapshots cloudwhale
```

#### 步骤六：测试验证

**目标**：通过一组测试命令验证所有 API 工具函数正常工作。

```bash
# 测试 1：状态检查
source nexus-api-lib.sh
RESULT=$(nexus_status)
echo "$RESULT" | jq -e '.status == "running"' && echo "✅ 状态检查通过" || echo "❌ 状态检查失败"

# 测试 2：仓库列表
COUNT=$(nexus_list_repos | wc -l)
echo "仓库总数: $COUNT"
[ "$COUNT" -gt 0 ] && echo "✅ 仓库列表通过" || echo "❌ 仓库列表失败"

# 测试 3：可写性检查
WRITABLE=$(nexus_writable | jq -r '.writable')
[ "$WRITABLE" = "true" ] && echo "✅ 可写检查通过" || echo "❌ 不可写"

# 测试 4：组件搜索
ITEMS=$(nexus_search_components maven-releases | jq '.items | length')
echo "maven-releases 中组件数: $ITEMS"
echo "✅ API 工具函数测试完成"
```

### 3.3 常见坑点

| 坑点 | 现象 | 解决方法 |
|------|------|----------|
| `continuationToken` URL 编码 | 分页第二页请求返回空或报错 | Token 可能含特殊字符（如 `+`、`=`），需要用 `jq -sRr @uri` 做 URL 编码 |
| `jq` 解析数字时显示科学计数法 | 大数字的 component ID 显示为 `1.23456e+12` | 使用 `jq -r '.items[].id'` 的 `-r` raw 模式避免数字格式化 |
| Basic Auth 在脚本中不安全 | 密码明文出现在进程列表（`ps aux` 可见） | 使用环境变量 + `-u "$USER:$PASS"` 或改用 API Token |
| DELETE 后查询仍可见 | 执行删除后组件仍然出现在搜索结果中 | 索引更新有短暂延迟，等待 3~5 秒后重试搜索 |
| API 返回 415 | POST/PUT 请求被拒 | 添加 `-H "Content-Type: application/json"` 头 |

## 4. 项目总结

### 4.1 常用 API 速查表

| 操作 | HTTP 方法 | 端点 | 说明 |
|------|----------|------|------|
| 仓库列表 | GET | `/service/rest/v1/repositories` | 所有仓库 |
| 仓库详情 | GET | `/service/rest/v1/repositories/{name}` | 单个仓库 |
| 创建仓库 | POST | `/service/rest/v1/repositories/{format}/{type}` | 含配置 JSON |
| 组件搜索 | GET | `/service/rest/v1/search?repository={r}&name={n}` | 分页支持 |
| 组件详情 | GET | `/service/rest/v1/components/{id}` | 含 assets 列表 |
| 删除组件 | DELETE | `/service/rest/v1/components/{id}` | 软删除 |
| 服务状态 | GET | `/service/rest/v1/status` | 运行状态 |
| 用户列表 | GET | `/service/rest/v1/security/users` | 所有用户 |
| 创建用户 | POST | `/service/rest/v1/security/users` | 含 JSON body |
| BlobStore 列表 | GET | `/service/rest/v1/blobstores` | 存储信息 |

### 4.2 适用场景

1. **日常运维巡检**：自动检查仓库状态、proxy 连通性、磁盘水位
2. **批量制品操作**：批量下载、批量删除、批量迁移过期组件
3. **CI/CD 集成**：在流水线中调用 API 发布制品、触发清理、验证上传结果
4. **环境初始化**：新 Nexus 实例一键创建仓库、用户、角色、清理策略
5. **合规审计脚本**：定期导出操作日志、用户列表、权限分配供安全团队审查

**不适用场景**：
1. 需要实时事件驱动的操作——应使用 Webhook 而非轮询 API
2. 需要 ACID 事务的多步操作——API 调用之间没有事务保证

### 4.3 注意事项

- **API 速率限制**：Nexus OSS 版没有官方速率限制，但高频率脚本应加 `sleep` 避免打满连接池
- **权限一致性**：脚本使用的账号权限必须覆盖所有调用的 API 端点，建议创建专用的 `robot-api-admin` 用户
- **API 版本兼容**：升级 Nexus 版本前后验证 API 脚本是否正常工作
- **密码安全**：永远不要在代码仓库中提交包含密码的脚本，使用环境变量或 Secret Manager

### 4.4 常见踩坑经验

**故障一：大页码的 continuationToken 超时**

某脚本遍历 50000 个组件，执行到 300 页后突然失败——continuationToken 过期。根因：token 有服务器端有效期，长时间遍历导致前几页的 token 超时。解决：将单次遍历改为按时间分段（如每天一次处理新增），避免一次遍历过多数据。

**故障二：curl 的 `--data-binary` 误用为 `-d`**

上传二进制文件时使用了 `-d @file` 而非 `--data-binary @file`，导致文件中换行符被 strip。根因：`-d` 会去除换行符并做 URL 编码，`--data-binary` 保留原始二进制。解决：上传文件类操作使用 `--data-binary @file`。

**故障三：`jq` 版本差异导致脚本不兼容**

macOS 自带的 jq 1.5 和 Linux apt 安装的 jq 1.6 在 `|=` 操作符行为上有差异，导致同一脚本在不同机器上输出不同。根因：脚本中使用了版本特定的语法。解决：在脚本开头检查 jq 版本，或在团队中统一 jq 版本。

### 4.5 思考题

1. 使用 Nexus REST API 实现一个"仓库镜像同步"脚本：将 maven-releases 仓库中所有 RELEASE 版本的组件复制到另一个 nexus 实例的对应仓库中。需要注意哪些潜在的幂等问题？
2. 你发现某个 nexus-api-lib.sh 工具函数在每天凌晨 3:00-3:05 之间调用时偶尔返回空结果，其他时间正常。可能是什么原因？如何排查和修复？

（第8章思考题答案：1. 基础的 Role + Privilege 只能做到仓库级别的权限隔离，无法实现"同一仓库内不同路径"的隔离。这个需求需要通过 Content Selector 实现——创建一个 CSEL 表达式如 `path =^ "/com/cloudwhale/team-a/"`，然后创建一个 Privilege 绑定此 Content Selector，再将该 Privilege 加入角色。2. 直接删除账号会导致审计日志中的用户引用变成孤立的 userId 字符串，失去关联信息。最佳实践：使用 `status: "disabled"` 禁用账号而非删除，这样既阻止登录，又保留审计日志的完整性。）

### 4.6 推广计划提示

- **运维部门**：本章是运维团队的必备技能，强烈建议将日常巡检操作全部脚本化并纳入 cron 定时任务
- **开发部门**：掌握 API 可加速本地开发——如"快速清空测试仓库"、"一键部署开发环境所有仓库"
- **测试部门**：测试框架中的 setup/teardown 可以利用 API 实现制品环境的自动准备和清理
