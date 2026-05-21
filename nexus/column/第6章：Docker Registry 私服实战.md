# 第6章：Docker Registry 私服实战

## 1. 项目背景

云鲸科技的运维组最近把后端服务全部容器化，12 个微服务每个都要打成 Docker 镜像。当前的做法是：CI 流水线在 Jenkins 节点上 `docker build`，然后 `docker push` 到 Docker Hub 的私有组织账户下。看起来一切正常，直到三件事接连发生：

第一件：某个周四下午，Docker Hub 对免费账户实施速率限制（每 6 小时 200 次 pull），当天正好是前端团队重构 day，全员执行 `docker-compose up -d`，结果一半人卡在 `Pulling... Waiting...`，构建环境瘫痪了三个小时。

第二件：周五凌晨线上紧急回滚，需要拉取上一个稳定版本的基础镜像 `nginx:1.24-alpine`，然而 Docker Hub 的 CDN 在亚太地区出现了间歇性故障，运维值班折腾了 40 分钟手动从其他节点 scp 镜像文件才完成回滚。

第三件：安全团队扫描发现某个已废弃的内部测试镜像 `cloudwhale/admin-panel:debug-v3` 在 Docker Hub 上被匿名拉取了 17 次——镜像中包含一个硬编码的测试用数据库连接字符串，虽然库已销毁，但凭证暴露本身就是安全事件。

这三件事指向同一个根因：**把企业容器镜像的交付链路依赖在公网 Docker Hub 上，既不可控加速，也无法安全审计**。Nexus 内置 Docker Registry 功能，可以像管理 Maven 和 npm 一样管理 Docker 镜像——通过 hosted 仓库存储内部镜像、proxy 仓库缓存 Docker Hub 镜像、group 仓库提供统一入口，将容器镜像纳入企业制品的完整供应链中。

## 2. 项目设计

炮哥和 Docker 组的运维"浩子"正在配置 Nexus Docker 仓库，已经折腾了一上午。

**浩子**："大师，我把 Docker hosted 仓库建好了，`docker login` 也成功了，但 `docker push` 直接报错：`http: server gave HTTP response to HTTPS client`。我明明用的就是 HTTP 呀？"

**大师**："这是 Docker daemon 的经典安全策略——Docker 默认只信任 HTTPS 的 registry。当你用 HTTP 访问时，必须把 registry 地址加入 Docker daemon 的不安全 registry 白名单。编辑 `/etc/docker/daemon.json`，加上 `"insecure-registries": ["localhost:5000"]`，然后 `systemctl restart docker`。"

> **技术映射**：Docker daemon 的 `insecure-registries` 是本地信任列表，仅用于开发/测试环境。生产环境必须配 TLS 证书，Nginx 反向代理 + Let's Encrypt 是常见方案。

**小胖**："那生产环境是不是一定要搞 HTTPS？搞个自签证书行不行？"

**大师**："可以但麻烦。自签证书需要分发到每台 Docker 主机的 `/etc/docker/certs.d/<registry-host>/ca.crt`，每新增一台节点就要拷贝一次，版本还不好管理。更推荐的做法是用公司统一 CA 签发的内部域名证书，或者用 Nginx 反向代理做 TLS termination。"

**小白**："Nexus 的 Docker 仓库和 Harbor、GitLab Container Registry 有什么区别？我们公司已经有 GitLab 了，直接用 GitLab 的 Registry 不行吗？"

**大师**："很好的对比。GitLab Container Registry 和你的代码仓库一体化，适合'每个项目一个 registry'的模式——比如 `gitlab.cloudwhale.com:5050/frontend/my-app`。Harbor 是专业的 Docker/OCI 制品仓库，原生支持镜像扫描、签名、跨地域复制和 chart 管理。Nexus 的优势在于**多格式统一**——你用同一个 Nexus 实例管 Maven、npm、Docker、Raw，不需要维护三套仓库系统。"

> **技术映射**：三者的定位差异：GitLab Registry = 代码旁的附属仓库；Harbor = 专业容器镜像工厂；Nexus = 多格式通用制品仓库。

**浩子**："那我 push 了一个镜像后，Nexus 内部是怎么存的呢？Docker 镜像有那么多层，它在 BlobStore 里是什么结构？"

**大师**："Docker 镜像由 manifest 和多层 layer 组成。在 Nexus 中，Docker 格式处理器会把这些层映射到 Component 和 Asset。简单说：一个 tag（如 `my-app:1.0`）对应一个 Component，它的 Assets 包括 manifest JSON 和每个 layer 的 Blob。Nexus 会做 layer 去重——如果两个镜像共享同一个基础层（比如都是 `FROM openjdk:17`），这一层只存一份。"

**小胖**："这就像自助餐的公共菜区——不管几个用户来拿，菜只炒一盘！"

**大师**："对，Docker 把这叫**内容寻址存储**——layer 的唯一标识是它的 SHA256 摘要。Nexus 充分利用了这个特性，在 BlobStore 层面做去重。"

> **技术映射**：Docker layer 的 Content-Digest（SHA256）与 Nexus Blob 的 ID 天然对齐，同一 layer 即使被多个镜像引用也只占用一份存储空间。

**小白**："那 proxy 仓库怎么处理 Docker Hub 的拉取？Docker Hub 的认证和限流机制很复杂。"

**大师**："Docker proxy 仓库可以配置 Docker Hub 的认证凭据——如果你有 Docker Hub 付费账户，填写 username/password 后，Nexus 内部的所有拉取都以你的付费账户身份执行，团队享受更高的速率限制。没配凭据的话就按匿名用户限额走。另外，`Docker Bearer Token Realm` 也要启用，这对 Docker 客户端的认证流程至关重要。"

**浩子**："group 仓库对于 Docker 有什么特殊之处？"

**大师**："Docker group 仓库的工作方式和 Maven/npm group 类似——客户端 `docker pull` 时，group 按成员顺序依次查找。但有个细节：Docker 客户端 pull 时需要完整的 `registry/repo:tag` 路径，所以你在 `docker pull` 时必须在镜像名前面带上 group 仓库的地址和端口，比如 `localhost:5001/my-app:1.0`。"

## 3. 项目实战

### 3.1 环境准备

- 已按第 2 章部署好 Nexus 实例
- Docker 20.10+（或 Podman）
- curl、jq
- Nexus 额外暴露 Docker 端口（5000 用于 hosted，5001 用于 group）

**修改 docker-compose.yml 增加端口**：

```yaml
# 在 docker-compose.yml 中添加 Docker 专用端口
ports:
  - "8081:8081"    # Web UI
  - "5000:5000"    # Docker hosted
  - "5001:5001"    # Docker group
```

```bash
# 重启 Nexus
docker compose down && docker compose up -d
```

### 3.2 分步实战

#### 步骤一：启用 Docker Realm 并创建仓库

**目标**：启用 Nexus 的 Docker 认证和仓库功能。

```bash
# 1. 启用 Docker Bearer Token Realm
curl -u admin:admin123 -X PUT \
  "http://localhost:8081/service/rest/v1/security/realms/active" \
  -H "Content-Type: application/json" \
  -d '["NexusAuthenticatingRealm", "NexusAuthorizingRealm", "NpmToken", "DockerToken"]'

# 2. 创建 Docker hosted 仓库（存放内部镜像）
curl -u admin:admin123 -X POST \
  "http://localhost:8081/service/rest/v1/repositories/docker/hosted" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "docker-hosted",
    "online": true,
    "storage": {
      "blobStoreName": "default",
      "strictContentTypeValidation": true,
      "writePolicy": "ALLOW"
    },
    "docker": {
      "v1Enabled": false,
      "forceBasicAuth": true,
      "httpPort": 5000
    }
  }'

# 3. 创建 Docker proxy 仓库（缓存 Docker Hub）
curl -u admin:admin123 -X POST \
  "http://localhost:8081/service/rest/v1/repositories/docker/proxy" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "docker-hub-proxy",
    "online": true,
    "storage": {
      "blobStoreName": "default",
      "strictContentTypeValidation": true
    },
    "proxy": {
      "remoteUrl": "https://registry-1.docker.io",
      "contentMaxAge": 1440,
      "metadataMaxAge": 1440
    },
    "docker": {
      "v1Enabled": false,
      "forceBasicAuth": true,
      "httpPort": null
    },
    "httpClient": {
      "blocked": false,
      "autoBlock": true,
      "connection": {
        "retries": 3,
        "timeout": 120
      }
    },
    "negativeCache": {
      "enabled": true,
      "timeToLive": 1440
    }
  }'

# 4. 创建 Docker group 仓库（统一入口）
curl -u admin:admin123 -X POST \
  "http://localhost:8081/service/rest/v1/repositories/docker/group" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "docker-public",
    "online": true,
    "storage": {
      "blobStoreName": "default",
      "strictContentTypeValidation": true
    },
    "group": {
      "memberNames": ["docker-hosted", "docker-hub-proxy"]
    },
    "docker": {
      "v1Enabled": false,
      "forceBasicAuth": true,
      "httpPort": 5001
    }
  }'
```

**参数说明**：
- `forceBasicAuth: true`：强制所有请求使用 Basic Auth（避免匿名下载内部镜像）
- `httpPort`：Nexus 对外暴露的 HTTP 端口，Docker 客户端连接此端口
- `v1Enabled: false`：禁用过时的 Docker Registry v1 协议，只用 v2

#### 步骤二：配置 Docker daemon 信任 HTTP registry

**目标**：让 Docker daemon 允许连接到 Nexus 的 HTTP 端口。

编辑 `/etc/docker/daemon.json`（Linux）或 Docker Desktop Settings（macOS/Windows）：

```json
{
  "insecure-registries": [
    "localhost:5000",
    "localhost:5001"
  ]
}
```

```bash
# Linux: 重启 Docker daemon
sudo systemctl restart docker

# 验证配置生效
docker info | grep -A 5 "Insecure Registries"
# 预期输出：
# Insecure Registries:
#  localhost:5000
#  localhost:5001
```

**如果使用 Docker Desktop（macOS/Windows）**：进入 Settings → Docker Engine，添加上述 JSON 配置，点 "Apply & Restart"。

#### 步骤三：构建并推送内部镜像

**目标**：构建一个简单的业务镜像并推送到 Nexus hosted 仓库。

```bash
# 创建测试镜像
mkdir -p ~/nexus-docker-demo && cd ~/nexus-docker-demo

cat > Dockerfile << 'EOF'
FROM alpine:3.19
LABEL maintainer="cloudwhale"
RUN echo "CloudWhale Demo App" > /app.txt
CMD ["cat", "/app.txt"]
EOF

# 构建镜像
docker build -t localhost:5000/cloudwhale-demo:1.0.0 .

# 预期输出：
# Successfully built xxx
# Successfully tagged localhost:5000/cloudwhale-demo:1.0.0

# 登录 Nexus Docker registry
docker login localhost:5000
# 输入用户名: admin，密码: admin123
# 预期输出: Login Succeeded

# 推送镜像到 hosted 仓库
docker push localhost:5000/cloudwhale-demo:1.0.0

# 预期输出：
# The push refers to repository [localhost:5000/cloudwhale-demo]
# 1.0.0: digest: sha256:xxxx... size: 528
```

**验证上传**：

```bash
# 通过 API 查看 Docker 组件
curl -u admin:admin123 \
  "http://localhost:8081/service/rest/v1/search?repository=docker-hosted&name=cloudwhale-demo" | jq .

# 预期输出：
# {
#   "items": [{
#     "name": "cloudwhale-demo",
#     "version": "1.0.0",
#     "format": "docker",
#     "repository": "docker-hosted"
#   }]
# }
```

#### 步骤四：通过 group 仓库拉取混合镜像

**目标**：用 group 地址统一拉取内部镜像和 Docker Hub 镜像。

```bash
# 登录 group 仓库
docker login localhost:5001

# 拉取内部镜像（通过 group，路由到 hosted 成员）
docker pull localhost:5001/cloudwhale-demo:1.0.0
# 预期输出: 1.0.0: Pulling from cloudwhale-demo ... Status: Downloaded

# 拉取 Docker Hub 公共镜像（通过 group，路由到 proxy 成员）
docker pull localhost:5001/library/nginx:1.25-alpine
# 预期输出: nginx:1.25-alpine: Pulling from library/nginx ... Status: Downloaded

# 第二次拉取同样的镜像（验证 proxy 缓存命中）
docker rmi localhost:5001/library/nginx:1.25-alpine
time docker pull localhost:5001/library/nginx:1.25-alpine
# 预期: 第二次拉取明显更快（从本地 Nexus 缓存读取，不走公网）
```

**运行结果**：docker-public group 仓库已成功代理了 Docker Hub 的 nginx 镜像，缓存到本地 BlobStore。后续所有开发者拉取同一镜像时直接从 Nexus 缓存获取，不再受 Docker Hub 限流影响。

#### 步骤五：镜像 multi-stage 构建与多 tag 管理

**目标**：构建一个多 tag 的 Spring Boot 应用镜像，理解 tag 复用机制。

```bash
# 以另一个 tag 推送同一个镜像（不产生新的 layer 存储）
docker tag localhost:5000/cloudwhale-demo:1.0.0 localhost:5000/cloudwhale-demo:latest
docker push localhost:5000/cloudwhale-demo:latest

# 验证两个 tag 指向同一个 digest（同一份 manifest）
curl -u admin:admin123 \
  "http://localhost:8081/service/rest/v1/search?repository=docker-hosted&name=cloudwhale-demo" | jq '.items[] | {version: .version}'

# 预期输出：1.0.0 和 latest 两个版本，但它们共享同一组 layer Blob
```

**验证 layer 去重**：

```bash
# 构建另一个使用相同基础镜像的应用
cat > Dockerfile2 << 'EOF'
FROM alpine:3.19
RUN echo "Another App" > /app.txt
CMD ["cat", "/app.txt"]
EOF

docker build -f Dockerfile2 -t localhost:5000/cloudwhale-demo:2.0.0 .
docker push localhost:5000/cloudwhale-demo:2.0.0

# 观察推送输出：alpine:3.19 的 layer 显示 "Layer already exists"
# 证明 Nexus 在层面做了去重，alpine 基础层只存一份
```

#### 步骤六（可选）：配置 Docker Hub 认证凭据使 proxy 享有更高限额

```bash
# 如果你有 Docker Hub 付费账户，配置 proxy 认证
curl -u admin:admin123 -X PUT \
  "http://localhost:8081/service/rest/v1/repositories/docker/proxy/docker-hub-proxy" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "docker-hub-proxy",
    "online": true,
    "storage": {"blobStoreName": "default", "strictContentTypeValidation": true},
    "proxy": {
      "remoteUrl": "https://registry-1.docker.io",
      "contentMaxAge": 1440,
      "metadataMaxAge": 1440
    },
    "docker": {"v1Enabled": false, "forceBasicAuth": true},
    "httpClient": {
      "blocked": false,
      "autoBlock": true,
      "authentication": {
        "type": "username",
        "username": "your-dockerhub-username",
        "password": "your-dockerhub-password"
      },
      "connection": {"retries": 3, "timeout": 120}
    }
  }'
```

### 3.3 常见坑点

| 坑点 | 现象 | 解决方法 |
|------|------|----------|
| `http: server gave HTTP response to HTTPS client` | push/pull 失败 | 配置 `insecure-registries`，或部署 TLS 证书 |
| `unknown blob` | push 成功但 pull 时某些 layer 404 | BlobStore 中资产被意外删除，需重建镜像索引 |
| 认证失败 `unauthorized` | login 成功但 push 403 / pull 401 | 检查 `forceBasicAuth` 是否开启，确认 Realm 中 `DockerToken` 已启用 |
| HTTPS 证书不信任 | `x509: certificate signed by unknown authority` | 将自签 CA 证书放入 `/etc/docker/certs.d/<host>/ca.crt` |
| 推送超大镜像超时 | 上传到一半连接断开 | 调大 proxy 的 connection timeout，或增加 Nexus JVM 直接内存 |

## 4. 项目总结

### 4.1 优缺点对比

| 维度 | Nexus Docker Registry | Docker Hub | Harbor |
|------|----------------------|-----------|--------|
| 内部镜像管理 | ✅ hosted 仓库 + 权限 | ⚠️ 需付费组织账户 | ✅ 原生支持 |
| 公网代理缓存 | ✅ proxy 仓库缓存 Docker Hub | ✅ 原生 | ✅ proxy cache 项目 |
| 镜像漏洞扫描 | ❌ OSS 版不支持 | ⚠️ Docker Scout（付费） | ✅ Trivy 内置 |
| 多格式统一管理 | ✅ Maven/npm/Docker 同一入口 | ❌ 仅 Docker | ⚠️ 支持 Helm Chart |
| 去重存储 | ✅ layer 级别自动去重 | ✅ 内容寻址 | ✅ 内容寻址 |
| OCI 兼容性 | ✅ 兼容 OCI 镜像 | ✅ | ✅ |
| 部署复杂度 | ⚠️ 需配 realm + 端口 | ✅ SaaS | ⚠️ 较重 |

### 4.2 适用场景

1. **中小团队的容器镜像私服**：Nexus 一站式管理，无需额外部署 Harbor
2. **Docker Hub 限流规避**：proxy 缓存常用基础镜像（如 nginx、openjdk、alpine），一次拉取全员复用
3. **多格式制品统一**：同时需要 Maven、npm、Docker 私服的团队，Nexus 一个实例全搞定
4. **内网隔离环境**：外网 Nexus 缓存所有需要的 Docker Hub 镜像后，导入内网 Nexus
5. **CI/CD 流水线加速**：构建节点从内网 Nexus 拉取依赖镜像，避免每次去公网

**不适用场景**：
1. 企业级容器镜像治理（镜像扫描、签名、CVE 管理）→ Harbor 更合适
2. 已有 Harbor 且团队满意——不引入第二个 Docker registry 增加维护成本

### 4.3 注意事项

- **生产环境必须用 HTTPS**：`insecure-registries` 仅用于开发环境，生产环境建议 Nginx 反向代理 + 公司统一 CA 签发的证书
- **DockerToken Realm 不可遗漏**：未启用此 Realm 时 Docker 客户端无法完成 Bearer Token 认证流程
- **Docker group 仓库的 `httpPort` 必须与 hosted 仓库不同**：如 hosted 用 5000，group 用 5001，否则端口冲突
- **清理 Docker 镜像的特殊性**：删除 tag 不等于释放空间，需执行 `Delete unused manifests` + `Compact BlobStore` 才能回收实际磁盘空间

### 4.4 常见踩坑经验

**故障一：Docker Desktop 重启后 insecure-registries 配置丢失**

某团队使用 Docker Desktop，在 UI 中配置了 `insecure-registries`，但系统重启后配置回退。根因：Docker Desktop 的配置文件路径为 `~/.docker/daemon.json`，部分版本升级后配置被重置。解决：备份 `daemon.json`，升级后检查并恢复。

**故障二：Docker Hub proxy 返回 403 rate limit exceeded**

使用匿名 proxy 缓存时，Nexus 作为匿名用户拉取 Docker Hub 触发限流。根因：未配置 Docker Hub 认证凭据。解决：在 proxy 仓库的 HTTP Client 中填入付费 Docker Hub 账户（即使是最低档的 Pro 计划也能显著提高限额）。

**故障三：本地 Docker 缓存与 Nexus 缓存不一致**

开发者本地的 `docker pull` 用了本地 Docker daemon 缓存，没有真正从 Nexus 拉取，导致"明明 Nexus 上删了，本地还能用"。根因：Docker daemon 有自己的 layer 缓存。解决：使用 `docker pull --no-cache` 强制跳过本地缓存，或在测试时先 `docker system prune -a` 清除本地镜像。

### 4.5 思考题

1. 如果一个镜像在 `docker-hosted` 和 `docker-hub-proxy` 中以**同名同 tag** 存在（例如 `library/nginx:1.25` 同时在 hosted 和 proxy 缓存中有），当通过 `docker-public` group 仓库 pull 时，实际拉取到的是哪个？为什么？
2. Docker 镜像清理时，`Delete unused manifests` 任务和 `Compact BlobStore` 任务分别做了什么？为什么只删 tag 不会释放磁盘空间？

（第5章思考题答案：1. 当 hosted 仓库排在 proxy 前面时，group 会先在 hosted 仓库中查找并命中，返回 hosted 中的版本；如果顺序反过来，则会返回 proxy 缓存中的版本。2. 迁移方案：第一步，在 Nexus 中创建 `npm-hosted` 仓库并配置好认证；第二步，将所有 `@company/*` 包的 publish 目标切换为 Nexus（修改 `publishConfig.registry`），同时保持 npmjs 上的旧包不变；第三步，逐步将 CI 中的 `npm install` 的 registry 指向 Nexus group；第四步，确认所有下游项目能正常从 Nexus 安装后，执行 `npm deprecate` 在 npmjs 上标记旧版本；第五步，观察期满后删除 npmjs 上的私有包。全程保持双 registry 可访问，确保回退路径。）

### 4.6 推广计划提示

- **运维部门**：重点掌握 TLS 证书配置、daemon.json 白名单管理、镜像清理策略
- **开发部门**：掌握 `docker login` + `docker push/pull` 的 Nexus 地址规则，理解 tag 和 layer 去重的意义
- **CI/CD 团队**：CI 节点中通过环境变量配置 `DOCKER_REGISTRY` 和 `DOCKER_AUTH`，避免硬编码 Nexus 地址
