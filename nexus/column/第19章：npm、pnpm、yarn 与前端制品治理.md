# 第19章：npm、pnpm、yarn 与前端制品治理

## 1. 项目背景

云鲸科技前端团队从统一使用 npm 逐步分化成了三派——老项目用 npm 7，新项目用 pnpm 8，还有一位坚持 yarn 1.22 的"古典派"开发者。表面上看，三种工具都能 `install`，但实际问题层出不穷：

前端组长阿玲的团队维护的 `@cloudwhale/ui-components` 组件库，在 npm 用户的项目中安装正常，但 pnpm 用户反映"组件库的 peerDependencies 解析有问题，安装了不需要的 React 版本"。CI 流水线中使用 `npm ci` 命令试图实现可复现构建，但某次构建节点网络波动，npm 从 Nexus proxy 拉取一个包的 metadata 成功、但 tarball 下载失败，导致 `package-lock.json` 中记录的 integrity 与实际文件不匹配，整个流水线失败。

更隐蔽的安全问题也浮现了：某第三方包的维护者将 `postinstall` 脚本注入了恶意代码，该包被 Nexus proxy 缓存后，所有团队成员在 `npm install` 时都在本地执行了这段脚本——而 CI 环境因为用了 `--ignore-scripts` 参数反而躲过了。安全团队质问："Nexus 能不能在缓存时就拦截这种恶意包？"

前端制品的治理难度不亚于 Maven——包的依赖树更深（平均 500+ 依赖）、版本碎片化更严重（semver 范围解析）、lockfile 的可复现性受 registry 地址影响、全团队的依赖安全取决于第一次 proxy 缓存时的远程内容。本章将深入 npm metadata/tarball 缓存机制，建立 lockfile 与 Nexus 的协作规范，制定 scope 包的权限隔离策略，并介绍供应链审计的基础思路。

## 2. 项目设计

阿玲、前端组的吴凡（pnpm 拥趸）和大师聚在一台 MacBook 前。

**阿玲**："大师，npm install 时，Nexus 到底缓存了什么？是整个 tarball 还是连 metadata 带包全缓存？"

**大师**："两样都缓存。npm 请求分两步——第一步 GET `/<package-name>` 获取 metadata JSON（包含所有版本、dist-tag、依赖声明），第二步 GET `/<package-name>/-/<package>-<version>.tgz` 下载 tarball。Nexus proxy 仓库缓存这两者——metadata JSON 和 tarball 文件。metadata 的缓存时间由 `metadataMaxAge` 控制，tarball 的缓存时间由 `contentMaxAge` 控制。"

**吴凡**："那为什么 pnpm install 有时会解析出不同的依赖版本？和 npm 差在哪？"

**大师**："pnpm 和 npm 对 peerDependencies 的处理策略不同。npm 7+ 会自动安装缺失的 peerDependencies，而 pnpm 严格检查——如果某个包声明了 `peerDependencies: {react: ^18}`，你的项目里没装 react 18，pnpm 直接报错。但这跟 Nexus 无关——Nexus 只是存储和返回包的 metadata 和 tarball，不参与依赖解析。peerDependencies 的问题是包管理器层面的。"

> **技术映射**：Nexus 对 npm/pnpm/yarn 是透明的 HTTP 仓库——三者的差异（node_modules 结构、peer 处理、lockfile 格式）在客户端侧，不在 Nexus 侧。

**小胖**："`package-lock.json` 里的 `resolved` 字段指向的地址很重要吗？我看到有的是 `registry.npmjs.org`，有的是 Nexus 地址。"

**大师**："极其重要。`resolved` 字段记录了每个包的实际下载地址。如果这个地址指向公网（`registry.npmjs.org`），即使你 `.npmrc` 里配了 Nexus registry——lockfile 里已经记录了直连地址，`npm ci` 会直接去公网下载，绕过 Nexus！正确的做法是：在配置好 Nexus registry 后，删除 `node_modules` 和 `package-lock.json`，重新 `npm install` 生成新的 lockfile——确保所有 `resolved` 都指向 Nexus。"

> **技术映射**：lockfile 的 `resolved` 字段是制品下载的"硬编码地址"，一旦写死就不会随 `.npmrc` 变化。新环境必须用 Nexus registry 重新生成 lockfile。

**阿玲**："那 pre-release 版本和 dist-tag 呢？我们有 `@cloudwhale/ui-components@next` 这个 dist-tag 指向测试版，Nexus 能支持吗？"

**大师**："完全支持。`npm dist-tag add @cloudwhale/ui-components@1.2.0-beta.1 next` 这个命令会更新 Nexus hosted 仓库中该包的 metadata 中的 `dist-tags` 字段。Nexus 忠实地存储和返回。`npm install @cloudwhale/ui-components@next` 会正确解析到 beta 版本。"

**小白**："安全问题！Nexus 能在缓存时扫描恶意包吗？"

**大师**："Nexus OSS 版不内置恶意包扫描。但你可以通过 Webhook + 外部扫描系统实现——每当有新包上传到 npm-hosted 或 npm-proxy 缓存了新包，触发 Webhook 通知安全扫描服务（如 Socket.dev、Snyk），扫描结果通过 API 标记问题包。更直接的做法是：在 CI 中使用 `npm audit` 命令，它基于 npm 的漏洞数据库做检查。"

## 3. 项目实战

### 3.1 环境准备

- 已部署 Nexus 实例，npm 仓库套件已创建
- Node.js 18+、npm 9+
- 可选安装 pnpm 和 yarn 用于对比测试

### 3.2 分步实战

#### 步骤一：Nexus npm metadata 缓存深度解析

**目标**：观察 Nexus 缓存 npm metadata 和 tarball 的实际行为。

```bash
NEXUS="http://localhost:8081"
AUTH="admin:admin123"

# 1. 查看 Nexus proxy 缓存中 lodash 的完整 metadata
curl -s http://localhost:8081/repository/npm-public/lodash | jq '{
  name: .name,
  "dist-tags": ."dist-tags",
  versions_count: (.versions | length),
  time_modified: .time.modified
}' | head -20

# 预期输出：
# {"name": "lodash", "dist-tags": {"latest": "4.17.21", ...}, "versions_count": 156, ...}

# 2. 查看 Nexus proxy 仓库的缓存配置
curl -s -u $AUTH "$NEXUS/service/rest/v1/repositories/npm-proxy" | \
  jq '{ 
    metadataMaxAge: .proxy.metadataMaxAge,
    contentMaxAge: .proxy.contentMaxAge,
    negativeCacheTtl: .negativeCache.timeToLive
  }'
```

#### 步骤二：lockfile 可复现性验证

**目标**：验证在 Nexus 环境下 lockfile 的可复现安装。

```bash
# === 验证 lockfile 可复现性实验 ===

mkdir -p /tmp/lockfile-test && cd /tmp/lockfile-test

# 步骤1：创建项目（确保 .npmrc 指向 Nexus）
cat > .npmrc << 'NRC'
registry=http://localhost:8081/repository/npm-public/
//localhost:8081/repository/npm-public/:_auth=YWRtaW46YWRtaW4xMjM=
NRC

cat > package.json << 'PKG'
{
  "name": "lockfile-test",
  "private": true,
  "dependencies": {
    "lodash": "4.17.21"
  }
}
PKG

# 步骤2：第一次安装（生成 lockfile）
rm -rf node_modules package-lock.json
npm install

echo "=== 检查 lockfile 中 resolved 字段 ==="
grep -c "registry.npmjs.org" package-lock.json && echo "⚠️ lockfile 中有公网地址！" || echo "✅ lockfile 全部指向 Nexus"

# 步骤3：验证 resolved 字段全部指向 Nexus
grep '"resolved"' package-lock.json | head -5
# 预期: 所有 URL 以 http://localhost:8081/repository/npm-public/ 开头

# 步骤4：模拟可复现构建
rm -rf node_modules
npm ci  # 严格按 lockfile 安装
echo "✅ npm ci 成功——lockfile 可复现安装通过"
```

**运行结果**：`npm ci` 使用 lockfile 中记录的 Nexus 地址精确安装，保证 CI 和生产环境的依赖完全一致。

#### 步骤三：pnpm/yarn 兼容性对比测试

**目标**：验证三种包管理器都能在 Nexus 下正常工作。

```bash
# === pnpm 测试 ===
mkdir -p /tmp/pnpm-test && cd /tmp/pnpm-test

cat > .npmrc << 'NRC'
registry=http://localhost:8081/repository/npm-public/
//localhost:8081/repository/npm-public/:_auth=YWRtaW46YWRtaW4xMjM=
NRC

cat > package.json << 'PKG'
{
  "name": "pnpm-test",
  "private": true,
  "dependencies": {
    "axios": "^1.6.0"
  }
}
PKG

# pnpm install（如果 pnpm 未安装：npm i -g pnpm）
pnpm install 2>&1 | tail -5

# 检查 pnpm-lock.yaml 中的 tarball 地址
grep "tarball" pnpm-lock.yaml | head -5
# 预期: 全部指向 Nexus

echo ""
echo "=== pnpm 测试完成 ==="
echo "✅ 检查 pnpm-lock.yaml 中所有 tarball URL 是否指向 Nexus"

# === yarn 测试 ===
cd /tmp && mkdir -p yarn-test && cd yarn-test

cat > .yarnrc.yml << 'YRC'
npmRegistryServer: "http://localhost:8081/repository/npm-public/"
YRC

cat > .npmrc << 'NRC'
//localhost:8081/repository/npm-public/:_auth=YWRtaW46YWRtaW4xMjM=
NRC

cat > package.json << 'PKG'
{
  "name": "yarn-test",
  "private": true,
  "dependencies": {
    "dayjs": "^1.11.0"
  }
}
PKG

yarn install 2>&1 | tail -5
echo "=== yarn 测试完成 ==="
```

#### 步骤四：scope 包权限隔离

**目标**：利用 Nexus 权限确保 `@cloudwhale/*` scope 只有前端团队能发布。

```bash
NEXUS="http://localhost:8081"
AUTH="admin:admin123"

# 创建 npm hosted 仓库的 scope 级发布权限
# 这个权限限制：只能在 npm-hosted 仓库中发布 @cloudwhale 前缀的包
curl -u $AUTH -X POST "$NEXUS/service/rest/v1/security/privileges" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "nx-repository-view-npm-npm-hosted-@cloudwhale-add",
    "description": "允许上传 @cloudwhale scope 包到 npm-hosted",
    "type": "repository-content-selector",
    "repository": "npm-hosted",
    "format": "npm",
    "actions": ["ADD"],
    "contentSelector": "npm-cloudwhale-scope"
  }'

# 注：OSS 版 Content Selector 功能有限——上述为理想方案
# 实际 OSS 版可用的方案：按仓库级别控制
echo "OSS 版的 scope 隔离方案：为 @cloudwhale scope 创建独立的 npm-hosted 仓库"
```

#### 步骤五：CI 中 lockfile 一致性的强制检查

**目标**：在 CI 流水线中确保 lockfile 的 resolved URL 不指向公网。

```bash
#!/bin/bash
# ci-lockfile-check.sh：CI 中检查 lockfile 的完整性

LOCKFILE="${1:-package-lock.json}"
NEXUS_HOST="${NEXUS_HOST:-localhost:8081}"

echo "=== Lockfile Nexus 地址检查 ==="

# 检查 lockfile 中是否包含公网 registry 地址
if grep -q "registry.npmjs.org" "$LOCKFILE" 2>/dev/null; then
    echo "❌ lockfile 中包含 registry.npmjs.org 地址！"
    echo "   这些依赖在 npm ci 时会绕过 Nexus 直接访问公网"
    echo "   修复: 删除 node_modules 和 lockfile，用 .npmrc 配置 Nexus registry 后重新 npm install"
    exit 1
fi

# 检查 lockfile 中是否引用了非预期的 registry
if ! grep -q "$NEXUS_HOST" "$LOCKFILE" 2>/dev/null; then
    echo "⚠️  lockfile 中未找到 $NEXUS_HOST 地址"
    echo "   请确认 .npmrc 中 registry 配置正确"
fi

echo "✅ lockfile 地址检查通过——所有依赖指向 Nexus"
```

```bash
chmod +x ci-lockfile-check.sh
./ci-lockfile-check.sh package-lock.json
```

### 3.3 常见坑点

| 坑点 | 现象 | 解决方法 |
|------|------|----------|
| lockfile 缓存公网地址 | CI 使用 `npm ci` 时绕过 Nexus 直连公网 | 删除 lockfile 重新生成 |
| npm proxy 缓存了 404 | 新发布的公共包在 Nexus 中搜不到 | 清理该包的 negative cache，或等待 `negativeCache.timeToLive` 到期 |
| scope 路径 `/` 编码问题 | 安装 `@scope/pkg` 报 404 | Nexus 正确处理 `@scope%2fpkg` 路径，客户端请求是正常的 |
| pnpm store 与 Nexus 缓存行为不一致 | pnpm 用户能装但 npm 用户失败 | pnpm 的全局 store 可能有缓存，清除 `pnpm store prune` |

## 4. 项目总结

### 4.1 包管理器差异速查

| 特性 | npm 7+ | pnpm 8+ | yarn 1.22 | Nexus 影响 |
|------|--------|---------|-----------|-----------|
| node_modules 结构 | 扁平化 | 硬链接 + .pnpm | 扁平化 | 无影响 |
| peerDependencies | 自动安装 | 严格检查 | 自动安装 | 无影响 |
| lockfile 格式 | package-lock.json v3 | pnpm-lock.yaml | yarn.lock | lockfile 需指向 Nexus |
| 可复现安装 | `npm ci` | `pnpm install --frozen-lockfile` | `yarn --frozen-lockfile` | Nexus 作为唯一源 |
| scope 认证 | 通过 registry 统配 | 同 npm | 同 npm | 需启用 NpmToken Realm |

### 4.2 适用场景

1. **多工具团队**：npm/pnpm/yarn 共存时统一 Nexus 入口
2. **CI 可复现构建**：通过 lockfile + Nexus 确保每次安装完全一致
3. **scope 权限隔离**：私有 scope 包只允许前端团队发布
4. **供应链安全**：锁定 Nexus 为唯一 registry 来源，监控异常的外部包请求

**不适用场景**：
1. 依赖 100% 来自公网的纯前端演示项目（没必要过 Nexus 一层代理）
2. 短期内没有私有 scope 包需求，且团队成员 < 5 人

### 4.3 常见踩坑经验

**故障一：lockfile 的 resolved 地址割裂导致 pnpm 和 npm 行为不一致**

某混合团队中，npm 用户生成的 `package-lock.json` 中 resolved 地址指向 Nexus，但 pnpm 用户的 `pnpm-lock.yaml` 中的 tarball 地址仍是 `registry.npmjs.org`。根因：pnpm 用户首次安装时 `.npmrc` 未配置 Nexus registry，生成了错误的 lockfile；后续改正 `.npmrc` 后执行 `pnpm install --frozen-lockfile` 仍使用旧地址。解决：统一要求所有开发者先确认 `.npmrc` 指向 Nexus，然后删除 `node_modules` 和 lockfile 后重新安装。CI 中增加 lockfile 地址检查脚本阻断不合规构建。

**故障二：proxy 缓存的 metadata 和 tarball 不同步**

`npm install lodash@4.17.21` 时 Nexus 返回的 metadata 包含了 `4.17.21` 版本，但请求 `lodash-4.17.21.tgz` 返回 404。根因：metadata 在 tarball 之前被缓存，而 tarball 由于网络中断未能完成缓存写入。Nexus 的缓存策略没有事务保证两个文件的原子性。解决：清理 Nexus proxy 中该包的 metadata 缓存（通过 Browse 页面删除对应目录），触发重新从远程完整拉取。

**故障三：企业网络代理导致 npm proxy 连接超时但无明确错误**

内网环境下 `npm install` 偶尔超时无响应，Nexus 日志中 proxy 请求显示 `SocketTimeoutException`。根因：公司网络代理（企业防火墙）对 npm registry 的 keep-alive 长连接有 60 秒超时限制，Nexus 的 `httpClient.connection.timeout` 默认也是 60 秒——两者几乎同时触发。解决：将 Nexus proxy 的 `connection.timeout` 设为 30 秒，配合 `retries: 3`，确保 Nexus 在代理超时前完成重试。

### 4.4 注意事项

- **lockfile 必须反映真实的 registry**：切换 registry 后务必删除旧 lockfile 重新生成
- **npm audit 的漏洞数据库来自 npmjs**：即使 Nexus 缓存在本地，audit 仍需联网查询
- **tarball integrity 校验失败**：说明 proxy 缓存的 tarball 已损坏——需清理对应缓存后重试
- **pnpm 的 `store-dir`**：CI 中应设置为临时目录，避免跨构建缓存污染

### 4.5 思考题

1. 前端团队发布了 `@cloudwhale/ui-components@1.0.0` 后，发现其中引用了一个有漏洞的依赖。现在需要紧急撤回该版本。在 Nexus npm hosted 仓库中，`npm unpublish` 和 `npm deprecate` 的行为有何不同？哪个更安全？
2. 如何利用 Nexus API 构建一个"前端依赖版本仪表盘"——即时展示公司所有前端项目中使用的依赖版本分布，自动识别过时版本和已知漏洞？

（第18章思考题答案：1. SNAPSHOT 发了 20 次后，在 hosted 仓库中只有 1 个 Component（`1.0-SNAPSHOT`），但下面有 20 个带不同时间戳版本的 Asset（`1.0-20250115.143210-1.jar` ... `1.0-20250120.090000-20.jar`）。每个 Asset 对应一个 Blob。Proxy 缓存的则是被请求时 metadata 中标记的"最新版本"——通常是最后一次 deploy 的那个时间戳版本。2. `external:*` 的匹配规则取决于 Maven Wagon 实现——它通过检查 repository URL 的 host 是否为 `localhost` 或 `127.0.0.1` 来判断是否为"本地"。测试环境 Nexus 部署在 localhost，被判定为"本地"不拦截；生产环境 Nexus 是独立的内部域名（nexus.internal），被判定为"external"拦截。结果是生产环境所有请求都走 mirror，但 mirror 指向的 group 可能不包含某些特殊仓库。）

### 4.6 推广计划提示

- **前端团队**：统一使用 Nexus registry 重新生成所有项目的 lockfile，纳入 CI 检查
- **安全团队**：将 `ci-lockfile-check.sh` 集成到 CI 流水线，阻止 lockfile 含公网地址的构建
- **架构组**：评估是否引入专门的 npm 供应链安全扫描工具（如 Socket.dev）与 Nexus Webhook 集成
