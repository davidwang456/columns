# 第5章：npm 私服实战：前端包缓存与发布

## 1. 项目背景

云鲸科技的前端组有 18 人，维护一个基于 React 的中台项目和三个 H5 移动端应用，所有项目共享一套 `@cloudwhale/ui-components` 组件库和 `@cloudwhale/utils` 工具包。目前团队的做法是：把组件库发布到 npm 公共 registry，前端项目直接从 npmjs.com 拉取。

三个月内出现了四次生产事故，每次都指向同一个根因——npm 依赖管理失控。第一次：轮播图组件作者离职前改了 `@cloudwhale/ui-components` 的代码但没有更新 changelog，移动端项目安装后轮播图直接白屏，回滚花了两小时。第二次：某个第三方依赖包 `left-pad` 的维护者从 npm 撤回了全部版本，导致 CI 构建全部失败。第三次：安全团队扫描发现 `@cloudwhale/utils` 在 npmjs 上被公开索引，虽然包本身不含密钥，但包的源码路径暴露了公司内网的 GitLab 域名。第四次：发布新版组件库时 CI 网络波动，`npm publish` 超时但 npmjs 已接收了一半数据，出现了"僵尸包"——metadata 显示存在但 tarball 404。

npm 生态的供应链脆弱性比 Maven 更甚——包撤回、依赖混淆（dependency confusion）、typohacking、lockfile 篡改等攻击手段层出不穷。把前端包纳入 Nexus 管理，不仅是加速 `npm install` 的工程需求，更是供应链安全的基本防线。本章将带你完成 npm hosted/proxy/group 仓库的创建，实现私有 scope 包的内部发布、公共依赖的本地缓存，以及 npm/pnpm/yarn 三款包管理器的统一接入。

## 2. 项目设计

大师盯着前端组长阿玲发来的 `package.json`，上面列出了 237 个依赖，其中 12 个是 `@cloudwhale/*` 的私有包。

**阿玲**："大师，我们 12 个私有包全在 npmjs 上，虽然 scope 是 `@cloudwhale` 没有其他人能发包，但每次 publish 都要走公网，构建也在公网拉依赖。最近安全部门已经要求前端必须整改了。Nexus 能替代 npmjs 吗？"

**大师**："不是替代，是分层管理。Nexus 的 npm 方案同样遵循 hosted + proxy + group 三位一体。私有包放 hosted 仓库，只有内部有上传权限；公共包走 proxy 缓存，一次下载全员复用。然后一个 group 把两者合并，前端同学只配一个 registry 地址——和 Java 组用的 Maven 方案一模一样。"

**小胖**："等等，那 `.npmrc` 里要配什么？我只知道 `registry=https://registry.npmjs.org` 这一条。"

**大师**："三条核心配置。第一，`registry` 指向你的 Nexus npm group 地址——所有包（公+私）都从这个入口拉。第二，`//nexus-server/repository/npm-public/:_authToken` 配置认证 token，没有它你的 publish/pull 私有包都会 401。第三，如果是 scope 包，可以单独配 `@cloudwhale:registry` 指向 hosted 仓库而非 group，这样其他 scope 包仍然走 group。"

> **技术映射**：`.npmrc` = Maven 的 `settings.xml`；`registry` = Maven 的 `mirror`；`_authToken` = Maven 的 `server/username/password`；scope-based registry = Maven 的 `repository` 声明。

**小白**："npm 的缓存机制和 Maven 完全不一样吧？Maven 是按坐标坐标下载后存本地 `.m2`，npm 是存在本地 cache 然后 link 到 `node_modules`。"

**大师**："对。npm 的依赖解析有两个层次：一是 registry 层（Nexus proxy 缓存），二是本地层（npm cache + node_modules）。Nexus proxy 仓库缓存的是 npm metadata 和 tarball，相当于你从淘宝镜像拉包——只不过这个镜像由你完全控制。npm 的 `package-lock.json` 锁定了解析后的 exact version，只要 lockfile 没变、registry 的 tarball 没变，install 就是可复现的。"

**阿玲**："那 pnpm 和 yarn 呢？我们团队有人用 pnpm。"

**大师**："好消息是，对 Nexus 来说，npm、pnpm、yarn 都是 HTTP 客户端——它们用同样的请求格式（`GET /@scope/pkg`、`GET /pkg/-/pkg-1.0.0.tgz`），Nexus 不关心客户端是谁。差异在于 lockfile 格式和本地缓存策略。实操上，你的 npm hosted 仓库只要配置正确，三款工具都能 publish 和 install。"

> **技术映射**：Nexus 的 npm 格式处理器只关心 registry 协议（CouchDB 风格的 npm registry API），与具体包管理器无关。

**小胖**："那 npm install 时，Nexus 怎么知道这个包是去 hosted 找还是 proxy 找？"

**大师**："好问题。和 Maven group 完全一样的逻辑——npm group 仓库按成员排列顺序依次查找。如果 `@cloudwhale/ui-components` 只在 npm-hosted 仓库中存在，proxy 仓库里没有也没关系，group 先在 hosted 里找到匹配的 metadata 就直接返回了。"

**小白**："Scope 包和普通包在 Nexus 里的处理有没有区别？"

**大师**："Nexus 不区分 scope 包和普通包——对 Nexus 来说，`@cloudwhale/utils` 和 `lodash` 都是组件（Component），只不过前者的名称里包含 `@scope/` 前缀。但在权限控制上，scope 是天然的隔离维度——你可以通过 Content Selector 做到 `@cloudwhale/*` 包只能特定团队上传和下载。"

**阿玲**："publish 的时候有没有什么坑？我们之前被 npmjs 上的包覆盖问题坑过——同一个版本号 publish 两次，npmjs 规定 72 小时内可以 unpublished，超出就不行了。"

**大师**："npm 的版本覆盖规则取决于 registry 实现。Nexus 的 npm hosted 仓库行为由 `writePolicy` 控制——默认 `ALLOW_ONCE`，即同名同版本不可重复 publish。这和 npmjs 的行为一致。如果你确实需要覆盖（比如测试环境），可以设为 `ALLOW`，但强烈不建议用于生产包。另外，`npm deprecate` 和 `npm unpublish` 在 Nexus 中的行为也与 npmjs 保持一致。"

## 3. 项目实战

### 3.1 环境准备

- 已按第 2 章部署好 Nexus 实例
- Node.js 18+、npm 9+（或 pnpm 8+ / yarn 1.22+）
- curl、jq

### 3.2 分步实战

#### 步骤一：创建 npm 标准仓库套件

**目标**：创建 npm hosted、proxy、group 三类仓库。

```bash
# 1. 创建 npm hosted 仓库（存放私有包）
curl -u admin:admin123 -X POST \
  "http://localhost:8081/service/rest/v1/repositories/npm/hosted" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "npm-hosted",
    "online": true,
    "storage": {
      "blobStoreName": "default",
      "strictContentTypeValidation": true,
      "writePolicy": "ALLOW_ONCE"
    }
  }'

# 2. 创建 npm proxy 仓库（缓存 npmjs 公共包）
curl -u admin:admin123 -X POST \
  "http://localhost:8081/service/rest/v1/repositories/npm/proxy" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "npm-proxy",
    "online": true,
    "storage": {
      "blobStoreName": "default",
      "strictContentTypeValidation": true
    },
    "proxy": {
      "remoteUrl": "https://registry.npmjs.org",
      "contentMaxAge": -1,
      "metadataMaxAge": 1440
    },
    "negativeCache": {
      "enabled": true,
      "timeToLive": 1440
    },
    "httpClient": {
      "blocked": false,
      "autoBlock": true,
      "connection": {
        "retries": 3,
        "timeout": 60
      }
    }
  }'

# 3. 创建 npm group 仓库（统一入口）
curl -u admin:admin123 -X POST \
  "http://localhost:8081/service/rest/v1/repositories/npm/group" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "npm-public",
    "online": true,
    "storage": {
      "blobStoreName": "default",
      "strictContentTypeValidation": true
    },
    "group": {
      "memberNames": [
        "npm-hosted",
        "npm-proxy"
      ]
    }
  }'
```

**运行结果**：三个 npm 仓库创建成功。在 Web UI 中可见 `npm-hosted`、`npm-proxy`、`npm-public` 三个条目。

#### 步骤二：配置 Nexus 的 npm Bearer Token Realm

**目标**：启用 npm 的 token 认证方式。

```bash
# 检查当前已启用的 realms
curl -u admin:admin123 \
  "http://localhost:8081/service/rest/v1/security/realms/active"

# 启用 npm Bearer Token Realm（添加 npm 认证支持）
curl -u admin:admin123 -X PUT \
  "http://localhost:8081/service/rest/v1/security/realms/active" \
  -H "Content-Type: application/json" \
  -d '["NexusAuthenticatingRealm", "NexusAuthorizingRealm", "NpmToken"]'
```

**运行结果**：NpmToken Realm 启用成功。没有这一步，npm 客户端使用 token 方式认证时会失败。

#### 步骤三：配置前端项目的 .npmrc

**目标**：配置前端项目的 npm 认证和 registry 地址。

创建 `~/.npmrc`（全局）或项目级 `.npmrc`：

```ini
# 默认 registry 指向 Nexus npm group（所有包的统一入口）
registry=http://localhost:8081/repository/npm-public/

# 认证 token（必须先在 Nexus Web UI 中生成 user token）
# 方法一：Base64 编码 username:password
//localhost:8081/repository/npm-public/:_auth=YWRtaW46YWRtaW4xMjM=

# 方法二：使用 Nexus User Token（推荐）
# //localhost:8081/repository/npm-public/:_authToken=NX_TOKEN

# 方法三：只对私有 scope 使用 hosted 仓库
# @cloudwhale:registry=http://localhost:8081/repository/npm-hosted/
```

**生成 Base64 认证字符串**：

```bash
# 生成 _auth 值
echo -n "admin:admin123" | base64
# 输出: YWRtaW46YWRtaW4xMjM=
```

**运行结果**：配置完成后，npm install 会通过 Nexus 拉取所有依赖。

#### 步骤四：创建并发布一个 scoped 私有包

**目标**：发布 `@cloudwhale/utils` 私有包到 Nexus hosted 仓库。

```bash
# 创建包目录
mkdir -p ~/nexus-npm-demo/utils && cd ~/nexus-npm-demo/utils

# 编写 package.json
cat > package.json << 'EOF'
{
  "name": "@cloudwhale/utils",
  "version": "1.0.0",
  "description": "CloudWhale shared utility functions",
  "main": "index.js",
  "publishConfig": {
    "registry": "http://localhost:8081/repository/npm-hosted/"
  },
  "scripts": {
    "test": "node test.js"
  }
}
EOF

# 编写入口文件
cat > index.js << 'EOF'
function formatDate(date, fmt = 'YYYY-MM-DD') {
  const d = new Date(date);
  const map = {
    YYYY: d.getFullYear(),
    MM: String(d.getMonth() + 1).padStart(2, '0'),
    DD: String(d.getDate()).padStart(2, '0'),
    HH: String(d.getHours()).padStart(2, '0'),
    mm: String(d.getMinutes()).padStart(2, '0'),
    ss: String(d.getSeconds()).padStart(2, '0')
  };
  return fmt.replace(/YYYY|MM|DD|HH|mm|ss/g, (m) => map[m]);
}

function debounce(fn, delay = 300) {
  let timer = null;
  return function (...args) {
    clearTimeout(timer);
    timer = setTimeout(() => fn.apply(this, args), delay);
  };
}

module.exports = { formatDate, debounce };
EOF

# 编写测试文件
cat > test.js << 'EOF'
const { formatDate, debounce } = require('./index');
const assert = require('assert');

const result = formatDate('2025-01-15');
assert.strictEqual(result, '2025-01-15');
console.log('✅ formatDate test passed');

const result2 = formatDate('2025-06-01 14:30:00', 'YYYY-MM-DD HH:mm:ss');
assert.strictEqual(result2, '2025-06-01 14:30:00');
console.log('✅ formatDate with time test passed');

console.log('All tests passed!');
EOF
```

**发布到 Nexus**：

```bash
cd ~/nexus-npm-demo/utils

# 运行测试
node test.js

# 发布（publishConfig.registry 已指向 npm-hosted）
npm publish

# 预期输出：
# npm notice 📦 @cloudwhale/utils@1.0.0
# npm notice === Tarball Contents ===
# npm notice 1.1kB index.js
# npm notice 412B  package.json
# npm notice 580B  test.js
# npm notice === Tarball Details ===
# npm notice name:    @cloudwhale/utils
# npm notice version: 1.0.0
# npm notice filename: cloudwhale-utils-1.0.0.tgz
# npm notice size:    1.2 kB
# npm notice unpacked: 2.1 kB
# + @cloudwhale/utils@1.0.0
```

**验证发布**：

```bash
# 通过 Nexus API 查询
curl -u admin:admin123 \
  "http://localhost:8081/service/rest/v1/search?repository=npm-hosted&name=utils" | jq .

# 预期输出：
# {
#   "items": [{
#     "group": null,
#     "name": "@cloudwhale/utils",
#     "version": "1.0.0",
#     "format": "npm",
#     "repository": "npm-hosted"
#   }]
# }
```

#### 步骤五：在另一个前端项目中安装私有包

**目标**：验证私有包可以通过 group 仓库正常安装。

```bash
# 创建消费者项目
mkdir -p ~/nexus-npm-demo/my-app && cd ~/nexus-npm-demo/my-app

cat > package.json << 'EOF'
{
  "name": "@cloudwhale/my-app",
  "version": "1.0.0",
  "private": true,
  "dependencies": {
    "@cloudwhale/utils": "1.0.0",
    "lodash": "^4.17.21"
  }
}
EOF

# 安装依赖（通过 group 仓库同时拉私有和公共包）
npm install

# 预期输出：
# added 2 packages in 2s
# 
# packages:
#   + @cloudwhale/utils@1.0.0
#   + lodash@4.17.21
```

**运行结果**：
1. `@cloudwhale/utils` 从 `npm-hosted` 仓库拉取（通过 group 路由）
2. `lodash` 从 `npm-proxy` 仓库拉取（第一次从 npmjs 远程下载并缓存，后续直接从缓存读取）
3. 生成的 `package-lock.json` 中所有 `resolved` 字段都指向 Nexus 地址

```bash
# 查看 lockfile 确认所有包都从 Nexus 拉取
grep "resolved" package-lock.json | head -5
# 预期输出：
# "resolved": "http://localhost:8081/repository/npm-public/@cloudwhale/utils/-/utils-1.0.0.tgz"
# "resolved": "http://localhost:8081/repository/npm-public/lodash/-/lodash-4.17.21.tgz"
```

#### 步骤六：发布新版本并验证版本管理

**目标**：发布 1.0.1 版本，验证 Nexus 的版本管理能力。

```bash
cd ~/nexus-npm-demo/utils

# 更新版本号
npm version patch
# 输出: v1.0.1

# 发布新版本
npm publish

# 查看版本历史
curl -u admin:admin123 \
  "http://localhost:8081/service/rest/v1/search?repository=npm-hosted&name=utils" | jq '.items[].version'
```

**运行结果**：Nexus 中 `@cloudwhale/utils` 同时显示 1.0.0 和 1.0.1 两个版本。在前端项目中执行 `npm install @cloudwhale/utils@1.0.0` 可精确安装旧版本。

#### 步骤七（可选）：使用 pnpm 和 yarn 验证兼容性

```bash
# pnpm 测试
cd ~/nexus-npm-demo/my-app
rm -rf node_modules package-lock.json
pnpm install
# 预期：正常安装，lockfile 中 resolved 指向 Nexus

# yarn 测试
rm -rf node_modules pnpm-lock.yaml
yarn install
# 预期：正常安装，yarn.lock 中 resolved 指向 Nexus
```

**运行结果**：三款包管理器均能正常通过 Nexus 安装依赖。Nexus 不感知客户端差异，只处理 HTTP 请求。

### 3.3 常见坑点

| 坑点 | 现象 | 解决方法 |
|------|------|----------|
| `_auth` vs `_authToken` | npm login 成功但 publish 报 401 | Nexus 需要启用 NpmToken Realm，否则 token 认证不生效 |
| scope 路径编码 | `@cloudwhale/utils` 在 URL 中变成 `@cloudwhale%2futils` | 这是 npm registry 协议的标准行为，Nexus 正确处理 |
| proxy 缓存未命中 | 公共包安装失败，返回 404 | 检查 npm-proxy 的 `remoteUrl` 是否为 `https://registry.npmjs.org`（注意 https 而非 http） |
| package-lock.json resolved 指向公网 | install 后 lockfile 仍指向 registry.npmjs.org | 确保 .npmrc 中的 `registry` 配置在 `npm install` 之前生效，建议先 `rm -rf node_modules package-lock.json` 再重装 |
| 同名包在 hosted 和 proxy 都存在 | 预期拉 hosted 里的私有包，却拉到了 proxy 缓存的同名公共包 | group 中 hosted 仓库必须在 proxy 前面 |

## 4. 项目总结

### 4.1 优缺点

| 维度 | Nexus npm 私服 | npmjs.com 公共 registry | Verdaccio（开源轻量 npm 私服） |
|------|---------------|----------------------|------------------------------|
| 私有包管理 | ✅ hosted 仓库，完整权限控制 | ⚠️ 需付费 npm org | ✅ 简单直接 |
| 公共包缓存 | ✅ proxy 缓存 + 净化（防撤回） | ✅ 原生访问 | ⚠️ 需配置 uplink |
| 多格式统一 | ✅ Maven/npm/Docker/Raw 一站管理 | ❌ 仅 npm | ❌ 仅 npm |
| 集群与 HA | ⚠️ OSS 版不支持，PRO 版支持 | ✅ SaaS 自带 | ❌ 不支持 |
| 上手难度 | ⚠️ 需理解仓库类型+NpmToken | ✅ 零配置 | ✅ 极低 |
| 可扩展性 | ✅ REST API + Webhook + 插件 | ❌ 受限于 npm API | ⚠️ 有限 |

### 4.2 适用场景

1. **企业私有 scope 包管理**：如 `@cloudwhale/*`，团队内部共享，无意外暴露于公网
2. **前端 CI 构建加速**：proxy 缓存后，`npm install` 时间从分钟级降到秒级
3. **供应链安全防护**：防止依赖撤回、依赖混淆、left-pad 类事件影响构建
4. **多团队前端项目共享**：UI 组件库、工具包、lint 配置统一通过 Nexus 分发
5. **内网审计与合规**：所有前端依赖经过内部仓库，可审计下载记录、版本分布

**不适用场景**：
1. 前端项目无私有包需求且 `npm install` 已经很快——增加 Nexux 多一层代理反而可能增加延迟
2. 团队已使用淘宝镜像/CNB 等国内加速源，且无私有包治理需求

### 4.3 注意事项

- **NpmToken Realm 必须启用**：否则 npm 的 token 认证方式完全不可用，所有 publish 和带认证的 install 都会失败
- **scope 的 `publishConfig.registry`**：建议在 `package.json` 中显式设置 `publishConfig.registry`，避免 publish 到错误的 registry
- **`npm unpublish` 和 `npm deprecate`**：Nexus 支持这两个命令，但 hosted 仓库的 `writePolicy: ALLOW_ONCE` 下 unpublish 可能失败
- **lockfile 的 `resolved` 字段**：确认在 CI 环境中生成的 lockfile 中所有 `resolved` 都指向 Nexus 地址，否则安装时会绕过代理
- **认证信息安全**：不要在代码仓库中提交 `.npmrc`（尤其是含 `_authToken` 的），CI 中应通过环境变量注入

### 4.4 常见踩坑经验

**故障一：npm install 随机失败，时而 404 时而成功**

前端团队在某次大版本更新后，CI 构建出现偶发性 `npm install` 失败——某个依赖包的 tarball 下载返回 404。排查发现：这个公共包的最新版本刚发布不到 5 分钟，npm-proxy 的 `metadataMaxAge: 1440`（24h）导致 Nexus 缓存中还是旧的 metadata，旧 metadata 中没有新版本的 tarball 引用。解决：临时调低 `metadataMaxAge` 到 1 分钟，执行 `npm install` 后恢复。

**故障二：scope `@cloudwhale/*` 包意外被代理到 npmjs 查询**

团队成员发现 `@cloudwhale/utils` 的安装请求被 Nexus 转发到了 npmjs.com（可以从 proxy 仓库的 remote 日志中看到）。根因：npm-public group 中 npm-hosted 排在 npm-proxy 后面，请求先命中了 proxy。解决：调整 group 成员顺序，npm-hosted 移至最前。

**故障三：pnpm 用户安装失败，npm 用户正常**

使用 pnpm 的开发者报告 `@cloudwhale/utils@1.0.0` 安装失败，但 npm 用户正常。根因：pnpm 使用了不同的 tarball 下载路径（特定于 pnpm 的 store 路径），Nexus 的 npm hosted 仓库正确返回但 pnpm 的本地 store 校验失败。解决：清除 pnpm store（`pnpm store prune`）后重新安装。

### 4.5 思考题

1. 如果一个 `@cloudwhale/ui-components` 包在 `npm-hosted` 中发布了 `1.0.0`，同时 `npm-proxy` 缓存的 npmjs 上恰好也有一个 `@cloudwhale/ui-components@1.0.0`（由离职员工在离职前泄露）。当 `npm-public` group 中 `npm-hosted` 排在前面时，客户端拉取到的是哪个版本？如果顺序反过来呢？
2. 你所在的公司准备将现有的 83 个 `@company/*` 私有包从 npmjs 迁移到 Nexus。设计一个零停机的迁移方案，确保迁移期间开发团队的 `npm install` 不受影响。

（第4章思考题答案：1. group 仓库按成员排列顺序依次查找，第一个命中就返回。所以 `maven-releases` 在前时会返回该仓库中的版本。但如果 `maven-snapshots` 中碰巧有版本号为 `1.0.0`（非 SNAPSHOT 后缀）的包，它不会被返回——因为 `maven-snapshots` 仓库的 `versionPolicy` 为 SNAPSHOT，解析阶段会被 Maven 过滤。2. 采用语义化版本 + 双版本并行发布：发布 1.0.0（旧 API）和 2.0.0（新 API）两个版本，下游项目逐步迁移。紧急回滚时可以通过 proxy 仓库的缓存保持旧版本可用。此外，使用 Maven 的 `version range` 声明依赖，如 `[1.0.0, 2.0.0)`，让 Maven 在范围内自动选择。）

### 4.6 推广计划提示

- **前端开发团队**：本章是前端团队的必修课，重点关注 `.npmrc` 配置和 scope 包管理
- **安全团队**：Nexus 是前端供应链安全的第一道防线（防撤回、防混淆、可审计），建议配合 Webhook 实现"新包上传自动安全扫描"
- **运维团队**：npm 包的 tarball 体积通常较小但数量庞大（几千个），关注 BlobStore 的小文件读写性能
- **CI/CD 团队**：重点验证 CI 环境中 `.npmrc` 的生成方式（环境变量 + 脚本模板），确保 lockfile 的 `resolved` 字段正确
