# 第23章：权限模型进阶：Content Selector 与细粒度授权

## 1. 项目背景

云鲸科技的组织架构发生了变化——交易中台和天枢平台两个事业部在组织上合并为"业务中台"，但技术栈和人员仍然独立运作。两个团队共用 `maven-trade-releases` 仓库发布各自的内部组件，交易团队负责订单和支付模块，天枢团队负责网关和配置模块。问题出在一个周五下午——天枢团队的新人把 `com.cloudwhale.order:order-api:1.0.0` 发布到了共享仓库，组名前缀和交易团队的规范完全一致，导致交易团队的 CI 拉取到了天枢的测试版本而非自己的正式版本。

更严重的是，安全审计发现天枢团队的 CI 机器人账号拥有 `maven-trade-releases` 的完整 `ADD` 权限——它不仅可以发布自己团队的包，还能覆盖（虽然 `ALLOW_ONCE` 阻止了覆盖）甚至浏览交易团队的所有包。"用仓库级别的权限隔离团队"在第 8 章有效的前提是**每个团队有独立的 hosted 仓库**。但当两个团队共享一个仓库时，仓库级别的 Privilege 就失效了——你需要的是**仓库内部的路径级权限控制**。

这正是 Content Selector（内容选择器）的战场——它允许在同一个仓库内，基于路径、格式、坐标等表达式创建"虚拟隔离区"。本章将深入 Content Selector 的 CSEL 表达式语法，实战实现"同一 Raw 仓库内按路径隔离三个团队的上传权限"、"同一 Maven 仓库内按 groupId 前缀隔离发布"、"基于时间范围和 IP 的临时访问控制"等进阶场景。

## 2. 项目设计

炮哥和安全总监在会议室讨论权限重构方案，大师在白板上画 CSEL 语法。

**安全总监**："现状是仓库级别权限太粗糙——一个人要么能上传整个仓库，要么完全不能上传。同一个仓库里交易团队的包要被天枢的人看见也就算了，问题是天枢 CI 也往里面写包，这迟早要出生产事故。"

**大师**："Content Selector 解决的就是这个——它用表达式语言（CSEL）定义一个'范围内的资源集合'，然后把 Privilege 绑定到这个范围上。举例来说，表达式 `format == "raw" && path =^ "/team-trade/"` 匹配的是所有路径以 `/team-trade/` 开头的 Raw 格式文件。给这个表达式创建一个 Privilege，再赋给交易团队的角色——他们就能上传 `/team-trade/` 下的文件，但碰不了 `/team-tianshu/` 下的东西。"

> **技术映射**：Content Selector = 仓库内部的 WHERE 子句。CSEL 表达式定义了"哪些 Asset 受此权限约束"。一个 Privilege 绑定一个 Content Selector + 一个 Action（READ/ADD/DELETE），精确到路径级别。

**小胖**："CSEL 能写多复杂？能不能写成 `group =^ "com.cloudwhale.trade" AND version =^ "1."` 这种？"

**大师**："CSEL 支持丰富的运算符——`==`（精确匹配）、`=^`（前缀匹配）、`$=`（后缀匹配）、`=~`（正则匹配）、`and`/`or`/`not` 逻辑组合。你说的 `group =^ "com.cloudwhale.trade" and version =^ "1."` 完全可行——匹配 groupId 以 `com.cloudwhale.trade` 开头且版本以 `1.` 开头的所有组件。"

**小白**："那 CSEL 的权限检查是在请求的就检查还是异步？如果我频繁修改 Content Selector 的定义，已经登录的用户会立即受影响吗？"

**大师**："权限检查是实时同步的——每次请求到达 Nexus 时，Shiro 都会即时评估当前有效的 Privilege 和 Content Selector。修改 Content Selector 后立刻生效，不需要用户重新登录。但有一个性能注意点——复杂的正则表达式在每次请求时都要被评估，如果仓库里有 10 万个组件且每个请求都扫描全部组件来匹配 CSEL，性能会受影响。"

> **技术映射**：CSEL 权限检查 = 请求时实时评估。表达式越复杂、仓库组件越多，性能开销越大。建议用前缀匹配（`=^`）代替正则匹配（`=~`）以提升性能。

**炮哥**："我听说 OSS 版的 Content Selector 和 PRO 版的有差异？"

**大师**："对。OSS 版支持基本的 Content Selector 功能和 REST API，但 UI 上的 Content Selector 管理页面和仓库级的 Content Selector 分配在某些版本中仅在 PRO 版可用。OSS 版需要通过 REST API 创建 Content Selector 和对应的 Privilege。功能本身可用，只是管理方式不同。"

**小胖**："临时账号怎么做？比如给外部顾问开一个只能访问特定路径、7 天后自动失效的账号。"

**大师**："Nexus 本身不支持用户自动过期——但你可以通过 API 脚本实现。创建一个'临时访问'角色，绑定限制性 Content Selector；创建用户并赋予该角色；在脚本中记录创建时间，用 cron 任务每天检查，过期的用户执行 `status: "disabled"`。"

## 3. 项目实战

### 3.1 环境准备

- 已部署 Nexus 实例（OSS 3.x）
- 已有 `raw-hosted` 仓库和测试文件
- curl、jq

### 3.2 分步实战

#### 步骤一：创建 Content Selector 并用 CSEL 表达式定义范围

**目标**：创建三个 Content Selector，分别限定三个团队的 Raw 仓库路径。

```bash
NEXUS="http://localhost:8081"
AUTH="admin:admin123"

# 1. Content Selector: 交易团队的 Raw 路径
curl -u $AUTH -X POST "$NEXUS/service/rest/v1/security/content-selectors" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "cs-raw-trade",
    "description": "限定 raw-hosted 中 /team-trade/ 路径",
    "expression": "format == \"raw\" and path =^ \"/team-trade/\""
  }'

# 2. Content Selector: 天枢团队的 Raw 路径
curl -u $AUTH -X POST "$NEXUS/service/rest/v1/security/content-selectors" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "cs-raw-tianshu",
    "description": "限定 raw-hosted 中 /team-tianshu/ 路径",
    "expression": "format == \"raw\" and path =^ \"/team-tianshu/\""
  }'

# 3. Content Selector: 共享区域的 Raw 路径（只读）
curl -u $AUTH -X POST "$NEXUS/service/rest/v1/security/content-selectors" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "cs-raw-shared",
    "description": "限定 raw-hosted 中 /shared/ 路径（全局只读）",
    "expression": "format == \"raw\" and path =^ \"/shared/\""
  }'

echo "=== Content Selector 创建完成 ==="
curl -s -u $AUTH "$NEXUS/service/rest/v1/security/content-selectors" | jq '.[].name'
```

#### 步骤二：创建基于 Content Selector 的 Privilege

**目标**：将 Content Selector 与读写操作绑定，创建路径级权限。

```bash
# 创建 Repository Content Selector 类型的 Privilege

# 交易团队：上传权限（ADD）
curl -u $AUTH -X POST "$NEXUS/service/rest/v1/security/privileges" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "priv-raw-trade-add",
    "description": "交易团队可在 raw-hosted 的 /team-trade/ 路径上传",
    "type": "repository-content-selector",
    "format": "raw",
    "repository": "raw-hosted",
    "actions": ["ADD", "READ"],
    "contentSelector": "cs-raw-trade"
  }'

# 天枢团队：上传权限
curl -u $AUTH -X POST "$NEXUS/service/rest/v1/security/privileges" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "priv-raw-tianshu-add",
    "description": "天枢团队可在 raw-hosted 的 /team-tianshu/ 路径上传",
    "type": "repository-content-selector",
    "format": "raw",
    "repository": "raw-hosted",
    "actions": ["ADD", "READ"],
    "contentSelector": "cs-raw-tianshu"
  }'

# 全局共享：只读权限
curl -u $AUTH -X POST "$NEXUS/service/rest/v1/security/privileges" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "priv-raw-shared-read",
    "description": "所有人可读 raw-hosted 的 /shared/ 路径",
    "type": "repository-content-selector",
    "format": "raw",
    "repository": "raw-hosted",
    "actions": ["READ"],
    "contentSelector": "cs-raw-shared"
  }'

echo "=== Privilege 创建完成 ==="
```

#### 步骤三：分配权限给团队角色并验证隔离

**目标**：创建交易和天枢的角色，验证越权上传被拒绝。

```bash
# 1. 创建交易团队的角色
curl -u $AUTH -X POST "$NEXUS/service/rest/v1/security/roles" \
  -H "Content-Type: application/json" \
  -d '{
    "id": "role-trade-upload",
    "name": "交易团队上传",
    "privileges": ["priv-raw-trade-add", "priv-raw-shared-read"],
    "roles": []
  }'

# 2. 创建天枢团队的角色
curl -u $AUTH -X POST "$NEXUS/service/rest/v1/security/roles" \
  -H "Content-Type: application/json" \
  -d '{
    "id": "role-tianshu-upload",
    "name": "天枢团队上传",
    "privileges": ["priv-raw-tianshu-add", "priv-raw-shared-read"],
    "roles": []
  }'

# 3. 创建测试用户
curl -u $AUTH -X POST "$NEXUS/service/rest/v1/security/users" \
  -H "Content-Type: application/json" \
  -d '{"userId":"user-trade","firstName":"Trade","lastName":"User","email":"trade@test.com","password":"Trade123!","status":"active","roles":["role-trade-upload"]}'

curl -u $AUTH -X POST "$NEXUS/service/rest/v1/security/users" \
  -H "Content-Type: application/json" \
  -d '{"userId":"user-tianshu","firstName":"Tianshu","lastName":"User","email":"tianshu@test.com","password":"Tianshu123!","status":"active","roles":["role-tianshu-upload"]}'

# 4. 验证权限隔离
echo "=== 权限隔离验证 ==="

# 交易用户上传到自己的路径 → 应该成功
echo "test-trade" > /tmp/trade-file.txt
HTTP=$(curl -s -o /dev/null -w "%{http_code}" -u user-trade:Trade123! -X PUT \
  "http://localhost:8081/repository/raw-hosted/team-trade/test.txt" --data-binary @/tmp/trade-file.txt)
echo "交易用户 → /team-trade/ : HTTP $HTTP (预期 201)"

# 交易用户上传到天枢的路径 → 应该 403
HTTP=$(curl -s -o /dev/null -w "%{http_code}" -u user-trade:Trade123! -X PUT \
  "http://localhost:8081/repository/raw-hosted/team-tianshu/test.txt" --data-binary @/tmp/trade-file.txt)
echo "交易用户 → /team-tianshu/ : HTTP $HTTP (预期 403)"

# 天枢用户上传到自己的路径 → 应该成功
echo "test-tianshu" > /tmp/tianshu-file.txt
HTTP=$(curl -s -o /dev/null -w "%{http_code}" -u user-tianshu:Tianshu123! -X PUT \
  "http://localhost:8081/repository/raw-hosted/team-tianshu/test.txt" --data-binary @/tmp/tianshu-file.txt)
echo "天枢用户 → /team-tianshu/ : HTTP $HTTP (预期 201)"

# 天枢用户上传到交易的路径 → 应该 403
HTTP=$(curl -s -o /dev/null -w "%{http_code}" -u user-tianshu:Tianshu123! -X PUT \
  "http://localhost:8081/repository/raw-hosted/team-trade/test.txt" --data-binary @/tmp/tianshu-file.txt)
echo "天枢用户 → /team-trade/ : HTTP $HTTP (预期 403)"
```

#### 步骤四：Maven 仓库的 groupId 级隔离

**目标**：在同一 Maven hosted 仓库中按 groupId 前缀隔离两个团队的发布。

```bash
# 创建 Content Selector: 交易团队的 groupId 前缀
curl -u $AUTH -X POST "$NEXUS/service/rest/v1/security/content-selectors" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "cs-maven-trade",
    "expression": "format == \"maven2\" and group =^ \"com.cloudwhale.trade\""
  }'

# 创建对应的 Privilege
curl -u $AUTH -X POST "$NEXUS/service/rest/v1/security/privileges" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "priv-maven-trade-add",
    "type": "repository-content-selector",
    "format": "maven2",
    "repository": "maven-releases",
    "actions": ["ADD", "READ"],
    "contentSelector": "cs-maven-trade"
  }'

echo "=== Maven groupId 级隔离已配置 ==="
echo "交易团队的 CI 账号只能发布 com.cloudwhale.trade.* 前缀的包"
```

#### 步骤五：权限调试与巡检脚本

**目标**：编写脚本检查所有 Privilege 和 Content Selector 的绑定关系。

```bash
#!/bin/bash
# audit-permissions.sh：Content Selector 权限审计
NEXUS="http://localhost:8081"
AUTH="admin:admin123"

echo "=== Content Selector 与 Privilege 审计 ==="
echo ""

echo "--- Content Selector 列表 ---"
curl -s -u $AUTH "$NEXUS/service/rest/v1/security/content-selectors" | \
  jq '.[] | {name, expression}'

echo ""
echo "--- Content Selector 类型 Privilege 列表 ---"
curl -s -u $AUTH "$NEXUS/service/rest/v1/security/privileges" | \
  jq '.[] | select(.type == "repository-content-selector") | {name, format, repository, actions, contentSelector}'

echo ""
echo "--- 角色-权限绑定关系 ---"
curl -s -u $AUTH "$NEXUS/service/rest/v1/security/roles" | \
  jq '.[] | {id: .id, privileges: .privileges}'
```

```bash
chmod +x audit-permissions.sh && ./audit-permissions.sh
```

### 3.3 常见坑点

| 坑点 | 现象 | 解决方法 |
|------|------|----------|
| CSEL 表达式拼写错误 | 创建成功但权限不生效 | 验证 format 名称与仓库格式一致（`maven2` 不是 `maven`，`raw` 不是 `raw2`） |
| `=^` 路径匹配未转义 | 斜杠 `/` 被视为语法分隔符 | 路径前缀用双引号括起来：`path =^ "/team-trade/"` |
| Content Selector 未绑定到 Privilege | 创建了 CS 和 Privilege 但没关联 | Privilege 的 `contentSelector` 字段必须与 CS 的 `name` 一致 |
| OSS 版 UI 不可见 | Web UI 中找不到 Content Selector 页面 | 通过 REST API 创建和管理（本章提供了完整 API 命令） |

## 4. 项目总结

### 4.1 CSEL 常用表达式速查

| 场景 | CSEL 表达式 |
|------|-----------|
| 按 Raw 路径前缀隔离 | `format == "raw" and path =^ "/team-trade/"` |
| 按 Maven groupId 前缀隔离 | `format == "maven2" and group =^ "com.cloudwhale.trade"` |
| 按 npm scope 隔离 | `format == "npm" and name =^ "@cloudwhale/"` |
| 按 Docker repo 名称隔离 | `format == "docker" and name =^ "cloudwhale-prod/"` |
| 多条件组合 | `path =^ "/prod/" and format != "raw"` |
| 排除特定版本 | `version != "1.0.0"` |

### 4.2 适用场景

1. **共享仓库内团队隔离**：多个团队共用一个 hosted 仓库，按路径/坐标隔离
2. **环境级权限**：同一仓库中 prod 路径只有运维能操作，dev 路径开发可自由上传
3. **外部顾问临时访问**：限定只能访问特定路径，时间到期后禁用账号
4. **合规地域限制**：按特定路径定义哪些包可以暴露给境外团队

**不适用场景**：
1. 团队仓库完全独立（各用各的 hosted）——仓库级权限就够了
2. 高度动态的权限变更（每分钟变化）——CSEL 评估有性能成本

### 4.3 注意事项

- **表达式性能**：正则 `=~` 比前缀 `=^` 慢得多，优先用前缀匹配
- **Delete 权限更要谨慎**：路径级 Delete 权限可能被滥用于删除非自己团队的文件
- **Content Selector 变更影响全局**：修改 CSEL 表达式会立即影响所有绑定该 Selector 的用户
- **OSS 版限制**：部分 UI 功能仅 PRO 版可用，但 API 完全可用

### 4.4 常见踩坑经验

**故障一：Content Selector 表达式改了但权限没有变化**

运维修改了 `cs-raw-trade` 的表达式从 `path =^ "/team-trade/"` 改为 `path =^ "/teams/trade/"`，但交易团队的用户仍能在旧路径下上传。根因：Content Selector 的 `name` 不变时，API 的 `PUT` 实际是更新操作，但变更后有短暂缓存。解决：等待 1 分钟后重试，或使用不同的 CS 名称创建新的并逐用户迁移。

**故障二：Privilege 的 `contentSelector` 字段拼写与 CS 名称不一致**

创建 Privilege 时填的 `contentSelector` 是 `"cs_raw_trade"`（下划线），实际 CS 名称是 `"cs-raw-trade"`（连字符）。创建成功（Nexus 不校验引用完整性）但权限永远不生效。解决：在脚本中通过 API 查询 CS 列表，用精确名称创建 Privilege。

**故障三：路径匹配时忽略了格式（format）条件**

运维创建了一个只含 `path =^ "/shared/"` 的 Content Selector，期望所有格式都受此约束。但 Nexus 的 Privilege 必须指定 `format`（仓库级），如果 Privilege 的 format 是 `raw`，而 path 指向的是 Maven 仓库，两者不匹配导致权限不生效。解决：Content Selector 表达式中必须包含 format 条件。

### 4.5 思考题

1. 如何实现"A 团队能上传但不能覆盖、B 团队能上传也能覆盖"的差异化权限？提示：结合 Content Selector + 两个不同的 Privilege（ADD 和 DELETE+ADD）
2. 你需要创建一个"安全审计专用"的只读账号，能够查看所有仓库的所有制品，但必须阻止其将制品下载到本地（只能看不能拿）。Nexus OSS 版能否实现？如果不能，有哪些变通方案？

（第22章思考题答案：1. SSD 分配：`blob-maven-snap`（热数据，SSD）、`blob-npm`（热数据，SSD）。HDD 分配：`blob-docker`（大文件，HDD）、`blob-ml-models`（低频，HDD）、`blob-maven-release`（温数据，HDD）。通过 Docker Compose 的 volumes 将不同 BlobStore 的 path 映射到不同磁盘上的目录。操作系统层面用 symlink 或 bind mount 指向实际磁盘路径。2. Docker layer 去重在 S3 BlobStore 上仍然有效——去重逻辑在 Nexus 的格式处理层，不在存储层。Push 共享 base layer 的新镜像时，Nexus 先计算 layer 的 SHA256 digest，检查 S3 BlobStore 中是否已存在同 digest 的 blob——如果存在，Nexus 不会上传重复的 layer bytes 到 S3，而是直接复用已有的 blob 引用。S3 省的是实际存储的空间和上传带宽。）

### 4.6 推广计划提示

- **安全部门**：审查 Content Selector 权限模型是否符合最小权限原则，排查是否存在仓库内越权路径
- **运维部门**：将 `audit-permissions.sh` 加入每周巡检计划
- **开发团队 Lead**：提交新仓库权限需求时需附带 Content Selector 表达式审核
