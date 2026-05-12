# 第29章：Proxy Cache——Harbor 作为 OCI 代理缓存

## 1 项目背景

某全球化电商公司的研发团队分布在上海、深圳两地，内网开发环境因安全合规要求与Internet物理隔离。开发团队每天需从Docker Hub、Quay.io、GHCR等公共Registry拉取大量基础镜像（`node:18-alpine`、`python:3.11-slim`、`nginx:1.25`等）。当前的"跳板机下载→U盘拷贝→内网`docker load`"流程已严重制约研发效率——平均每位开发者每周浪费约3小时在镜像搬运上。

**痛点一：Docker Hub限流导致CI/CD雪崩。** Docker Hub对匿名用户限制100 pulls/6小时，认证免费用户也仅200 pulls/6小时。该团队CI流水线每天触发300+次，每次构建拉取至少3个基础镜像。上午10点配额即耗尽，下午所有构建全部报`toomanyrequests: You have reached your pull rate limit`。每月至少发生2次因配额耗尽导致的发布阻塞事故，最长一次持续6小时——正值双十一大促前的关键迭代期，业务方压力巨大。

**痛点二：外网带宽被重复传输耗尽。** 公司出口带宽200Mbps，当50个K8s Node同时滚动更新时，每个Node需要拉取`python:3.11`（压缩后约350MB）。理论耗时为50×350MB÷200Mbps≈700秒，但实际因TCP拥塞控制和HTTP连接抢占，耗时超过40分钟——远超K8s Deployment的`progressDeadlineSeconds`（默认600秒），导致滚动更新超时失败并自动回滚。运维团队不得不在凌晨低峰期手动分批操作，人力和时间成本极高。

**痛点三：公共Registry不可用导致"断供"风险。** 2023年Docker Hub经历多次区域性DNS故障（亚太区中断达3小时），期间所有`FROM node:18`的构建步骤全部失败。更严重的是，2024年Quay.io存储后端故障导致部分历史版本tag返回404——CI系统中50+个微服务的构建流水线全部亮红。团队没有本地缓存降级方案，只能等待上游恢复，业务完全被动。

**痛点四：基础镜像版本漂移引发兼容性灾难。** 开发者习惯在Dockerfile中直接写`FROM node:latest`或`FROM python:3`。2024年4月Node.js官方将`node:latest`从Node 20切换到Node 22——其内置OpenSSL 3.x与团队遗留服务的`node-forge`库不兼容，导致50+服务的集成测试全红。排查"为什么昨天还能构建成功"消耗了整整一个上午，而修复所有Dockerfile中的`latest`引用又花了2天。

Harbor的Proxy Cache功能基于OCI Distribution Spec的**pull-through cache**机制，将外部Registry的镜像按需缓存到本地Harbor实例，实现**一次拉取、全员共享、离线可用**，从根本上解决上述四个痛点。

---

## 2 项目设计——剧本式交锋对话

**场景：平台工程团队周会，讨论内网镜像加速方案。小胖、小白和大师围坐在白板前。**

**小胖**（兴奋地拍桌子）："这问题简单！我写个Jenkins定时任务，每天凌晨2点把团队常用的20个基础镜像——`python:3.11`、`node:18-alpine`、`nginx:1.25`——全部`docker pull`下来然后`docker save -o`存到NAS共享目录。每个Node的containerd配置从NAS读取，完美！就像公司茶水间的零食柜——每周补货一次，大家自取，省时省力。"

**大师**（摇头）："小胖，你这'零食柜'方案有四个致命伤。**第一，需求不可预测。** 上周产品组突然要用`rust:1.77`做性能测试——你的列表里没有，他们还是得走外网。更糟的是这次拉取不会被你的方案缓存，下次其他组要用还得重新走外网。**第二，缓存新鲜度。** `node:18-alpine`这个tag是浮动的——上游随时推送安全补丁更新，而你的脚本写死tag名，对应的是哪个digest？你可能在缓存一个1月份已知有CVE-2024-XXXX漏洞的版本，而3月份修复版已经推送上去了。开发者满怀信心地pull你的缓存,殊不知拉回了一个带高危漏洞的镜像。**第三，存储和维护成本。** 你的脚本串行pull 20个镜像到Jenkins Node——20×平均500MB=10GB还行，但需求扩展到50+镜像后，谁来维护这个列表？新增一个镜像就要改脚本、加CR，运维负担越滚越大。**第四，单点瓶颈。** NAS通过NFS分发给50个Node——每个Node启动时从NFS读500MB镜像，50个并发连接下NFS的IOPS直接打满，NAS本身就成了新的瓶颈。"

"你要的不是**固定配餐**，而是**按需自助餐**——这正是Harbor Proxy Cache的设计哲学。第一个开发者需要时Harbor自动去上游拉取并缓存；第二个开发者直接走本地缓存；全程透明、零维护成本。"

**技术映射**：Harbor Proxy Cache实现的是OCI Distribution Spec中定义的pull-through cache模式。当客户端向Harbor发起`GET /v2/<name>/manifests/<tag>`请求时，Harbor首先检查本地存储——若manifest及关联blob已存在则直接返回（Cache Hit）；若不存在，Harbor以自身身份向上游Registry发起请求，将获取到的manifest和所有blob逐层下载到本地存储后再返回给客户端（Cache Miss + Fill）。客户端对代理的存在完全无感知。

**小白**（若有所思）："我理解按需缓存的好处。但缓存失效是计算机科学的经典难题——具体到这个场景：如果上游`python:3.11`推送了安全更新（tag名不变、digest变了），Harbor本地缓存怎么感知？是自动刷新还是永远不更新？如果永远不更新，那和安全扫描的联动怎么办？"

**大师**："直击要害。Harbor Proxy Cache的默认策略是**首次拉取后永久缓存、不主动更新**——这听起来反直觉，但符合代理缓存的定位。缓存同步有三种策略：

**策略一：定时过期（TTL）。** 创建Proxy Cache项目时可设置缓存过期时间（默认168小时=7天）。过期后下次pull会先发HEAD请求到上游检查digest——变了则重新拉取全量，没变则刷新TTL。就像冰箱里的牛奶——标了保质期，到期后你闻一下决定要不要换。

**策略二：手动同步。** 在Portal中点击"同步"按钮，或调用API强制重新拉取。适合"明确知道上游有更新"的场景——比如安全团队通报某个基础镜像有CVE修复。

**策略三：外部监控+自动触发。** Harbor P
roxy Cache不支持上游push事件回调，但你可以用Skopeo或Crane定期检查上游digest变化，发现差异则通过Harbor API删除本地缓存tag——下次pull自动触发重新拉取。"

**小胖**（追问）："那我pull了一个镜像，怎么判断是缓存命中的还是从上游新拉的？万一我以为拿到了最新版，实际是三个月前的缓存——上次就因为用了老旧基础镜像，被安全团队通报有高危漏洞。"

**大师**："三个方法。**一，看延迟**——首次pull是外网速度（30秒至数分钟），后续命中是局域网速度（<5秒）。**二，看审计日志**——`/api/v2.0/audit-logs`中每条操作记录了来源。**三，最可靠的是对比digest**："

```bash
# 本地缓存digest
curl -s -u admin:password \
  "https://harbor.company.com/api/v2.0/projects/proxy-dockerhub/repositories/library/python/artifacts/3.11-slim" \
  | jq -r '.digest'
# 输出：sha256:a1b2c3d4...

# 上游digest
crane digest docker.io/library/python:3.11-slim
# sha256:a1b2c3d4... → 一致，缓存最新
# sha256:e5f6g7h8... → 不一致，缓存已过期
```

**小白**（沉思后举手）："还有两个边缘场景。第一，私有镜像——我们公司在Docker Hub有私有组织的镜像，Proxy Cache能以认证身份代理拉取吗？拉下来之后在Harbor中的可见性如何？第二，多架构镜像（multi-arch manifest list）——比如`python:3.11`同时有amd64和arm64版本，缓存行为是怎样的？"

**大师**："好问题。**私有镜像**——创建上游Registry配置时提供`access_key`和`access_secret`（即Docker Hub的username + Access Token），Harbor就能以认证身份代理拉取。但要注意**安全边界**：缓存到Harbor后，该镜像的可见性由Proxy Cache项目权限决定——如果项目是公开的，所有能访问Harbor的人都能看到这个私有镜像的内容。所以如果代理私有镜像，务必把Proxy Cache项目设为**私有**并严格控制成员权限。建议为公开和私有镜像创建两个独立的Proxy Cache项目。

**多架构镜像**——Harbor会缓存完整的manifest list及所有平台对应的manifest和blob。amd64 Node发pull请求时，Harbor根据请求头中的平台信息返回amd64的manifest；arm64开发机则拿到arm64版本。就像自助餐厅同时供应中餐和西餐——顾客根据需求自取。代价是存储空间：一个4架构的镜像占用约单架构的4倍。如果团队只有amd64服务器，建议过滤掉不需要的架构以节省存储。"

---

## 3 项目实战

### 3.1 环境要求

| 组件 | 最低版本/规格 | 推荐版本/规格 | 说明 |
|------|-------------|-------------|------|
| Harbor | v2.1.0 | v2.10.0+ | Proxy Cache v2.1引入，v2.8+支持缓存过期策略和自动清理 |
| Docker Engine | 20.10+ | 24.0+ | 客户端需OCI Distribution Spec兼容 |
| containerd (K8s) | 1.5+ | 1.7+ | 需配置registry mirrors |
| 存储 | 50GB | 200GB+ SSD | 取决于缓存镜像数量和层大小，建议预留30%余量 |
| 内存 | 4GB | 8GB+ | Harbor Core额外约需1-2GB用于缓存元数据索引 |
| 网络（内网） | 1Gbps | 10Gbps | 缓存命中后的传输速度取决于内网带宽 |
| 网络（外网） | 50Mbps | 200Mbps+ | 首次拉取（Cache Miss）依赖外网带宽 |

### 3.2 步骤一：创建Proxy Cache项目

**目标**：在Harbor中创建专用的代理缓存项目，用于代理Docker Hub公开镜像。

```bash
# 创建名为 proxy-dockerhub 的代理缓存项目
curl -s -X POST -u "admin:Str0ng@Admin2024" \
  -H "Content-Type: application/json" \
  -d '{
    "project_name": "proxy-dockerhub",
    "public": true,
    "storage_limit": 536870912000,
    "metadata": {
      "enable_content_trust": "false",
      "auto_scan": "true",
      "severity": "high",
      "prevent_vul": "false",
      "reuse_sys_cve_allowlist": "true",
      "public": "true"
    }
  }' \
  "https://harbor.company.com/api/v2.0/projects"
```

**预期输出**：
```json
{
  "project_id": 7,
  "name": "proxy-dockerhub",
  "registry_id": null,
  "public": true,
  "storage_limit": 536870912000,
  "current_user_role_id": 1,
  "repo_count": 0
}
```

**关键参数说明**：
- `public: true`：设为公开只读，方便团队成员pull（仅Admin可管理）
- `storage_limit: 536870912000`（500GB）：配额限制，防止缓存无限膨胀
- `auto_scan: true`：开启自动漏洞扫描，确保缓存镜像的安全性
- `severity: high`：仅高危及以上漏洞触发告警

### 3.3 步骤二：配置上游Registry目标

**目标**：为Harbor配置需要代理的外部Registry。

```bash
# 方案A：Docker Hub（默认已内置，无需额外创建Registry即可直接代理）
# Docker Hub使用Harbor内置的默认Registry实例，创建项目后即可工作

# 方案B：配置Quay.io为额外上游（如需代理多个不同的公共Registry）
curl -s -X POST -u "admin:Str0ng@Admin2024" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "quay-proxy",
    "url": "https://quay.io",
    "type": "docker-registry",
    "credential": {
      "access_key": "",
      "access_secret": "",
      "type": "basic"
    },
    "insecure": false,
    "description": "Quay.io proxy cache upstream"
  }' \
  "https://harbor.company.com/api/v2.0/registries"
```

**预期输出**：
```json
{
  "id": 3,
  "name": "quay-proxy",
  "url": "https://quay.io",
  "type": "docker-registry",
  "status": "healthy",
  "creation_time": "2024-06-15T08:30:00Z"
}
```

**参数说明**：
- `insecure: false`：强制使用HTTPS（生产环境强烈推荐）；设为`true`则允许HTTP（仅限测试环境）
- `access_key / access_secret`：留空为匿名访问；代理私有仓库时需填写认证凭证
- 创建后在Harbor Portal → 系统管理 → Registry 中可点击"测试连接"验证连通性

### 3.4 步骤三：通过Proxy Cache拉取镜像并验证效果

**目标**：模拟开发者从Proxy Cache拉取镜像，对比首次和后续拉取的速度差异。

```bash
# === 测试1：首次拉取（Cache Miss → 从Docker Hub拉取并缓存）===
echo "=== 测试1：首次拉取（Cache Miss）==="
echo "开始时间: $(date '+%H:%M:%S')"
time docker pull harbor.company.com/proxy-dockerhub/library/python:3.11-slim

# === 测试2：二次拉取（Cache Hit → 直接从Harbor本地返回）===
echo ""
echo "=== 测试2：二次拉取（Cache Hit）==="
# 先删除本地镜像以模拟新Node
docker rmi harbor.company.com/proxy-dockerhub/library/python:3.11-slim
echo "开始时间: $(date '+%H:%M:%S')"
time docker pull harbor.company.com/proxy-dockerhub/library/python:3.11-slim

# === 测试3：验证缓存完整性 ===
echo ""
echo "=== 测试3：验证digest一致性 ==="
LOCAL_DIGEST=$(curl -s -u "admin:Str0ng@Admin2024" \
  "https://harbor.company.com/api/v2.0/projects/proxy-dockerhub/repositories/library/python/artifacts/3.11-slim" \
  | jq -r '.digest')
echo "Harbor缓存digest: $LOCAL_DIGEST"
```

**预期首次拉取输出（Cache Miss）**：
```
=== 测试1：首次拉取（Cache Miss）===
开始时间: 14:05:22
3.11-slim: Pulling from proxy-dockerhub/library/python
a1b2c3d4e5f6: Pull complete
f6e5d4c3b2a1: Pull complete
...
Digest: sha256:abc123def456789...
Status: Downloaded newer image for harbor.company.com/proxy-dockerhub/library/python:3.11-slim
real    1m42s          ← 取决于外网带宽和镜像大小
```

**预期二次拉取输出（Cache Hit）**：
```
=== 测试2：二次拉取（Cache Hit）===
开始时间: 14:07:08
3.11-slim: Pulling from proxy-dockerhub/library/python
Digest: sha256:abc123def456789...
Status: Downloaded newer image for harbor.company.com/proxy-dockerhub/library/python:3.11-slim
real    0m3s           ← 局域网速度，仅做manifest验证
```

### 3.5 步骤四：设置缓存过期与自动清理策略

**目标**：配置合理的缓存TTL和存储回收策略，防止存储无限膨胀。

```bash
# 1. 获取项目ID
PROJECT_ID=$(curl -s -u "admin:Str0ng@Admin2024" \
  "https://harbor.company.com/api/v2.0/projects?name=proxy-dockerhub" | jq '.[0].project_id')
echo "Project ID: $PROJECT_ID"

# 2. 创建标签保留策略——每个仓库仅保留最近推送的5个tag
curl -s -X POST -u "admin:Str0ng@Admin2024" \
  -H "Content-Type: application/json" \
  -d "{
    \"algorithm\": \"or\",
    \"rules\": [
      {
        \"template\": \"latestPushedK\",
        \"params\": {\"latestPushedK\": 5},
        \"tag_selectors\": [{\"kind\": \"doublestar\", \"pattern\": \"**\"}],
        \"scope_selectors\": {
          \"repository\": [{\"kind\": \"doublestar\", \"pattern\": \"**\"}]
        }
      }
    ],
    \"trigger\": {\"type\": \"manual\"},
    \"scope\": {\"ref\": $PROJECT_ID}
  }" \
  "https://harbor.company.com/api/v2.0/retentions"

# 3. 配置定时垃圾回收（每天凌晨3点自动执行）
curl -s -X PUT -u "admin:Str0ng@Admin2024" \
  -H "Content-Type: application/json" \
  -d '{
    "schedule": {
      "type": "Daily",
      "cron": "0 3 * * *"
    },
    "parameters": {
      "delete_untagged": true,
      "dry_run": false
    }
  }' \
  "https://harbor.company.com/api/v2.0/system/gc/schedule"
```

**预期输出**：
```json
{"schedule": {"type": "Daily", "cron": "0 3 * * *"}, "job_status": "Scheduled"}
```

**注意事项**：
- GC执行期间Harbor进入**只读模式**，不影响pull但暂停push——建议在凌晨低峰期执行
- GC仅清理未被任何manifest引用的孤立blob——被多个仓库共享的层不会被误删
- `delete_untagged: true`会清理所有未打tag的镜像，确保先跑保留策略再跑GC

### 3.6 步骤五：配置K8s/Containerd自动走缓存

**目标**：让K8s集群中的所有Node自动通过Harbor Proxy Cache拉取镜像，**无需修改任何K8s YAML**。

```toml
# /etc/containerd/config.toml
version = 2

[plugins."io.containerd.grpc.v1.cri".registry]
  [plugins."io.containerd.grpc.v1.cri".registry.mirrors]
    [plugins."io.containerd.grpc.v1.cri".registry.mirrors."docker.io"]
      endpoint = ["https://harbor.company.com/proxy-dockerhub"]
```

```bash
# 1. 将配置应用到所有Node（通过Ansible批量下发）
ansible all -m copy -a \
  "src=./containerd-config.toml dest=/etc/containerd/config.toml backup=yes"

# 2. 重启containerd使配置生效
ansible all -m systemd -a "name=containerd state=restarted"

# 3. 验证：K8s Pod中 image: python:3.11-slim 应当自动走Harbor缓存
kubectl run test-proxy-cache --image=python:3.11-slim --restart=Never -- \
  python3 -c "import sys; print('Proxy Cache works!', sys.version)"

# 4. 验证镜像来源
kubectl describe pod test-proxy-cache | grep -A3 "Pulled"
```

**预期输出**：
```
Pulled: Successfully pulled image "python:3.11-slim" in 3.2s
Normal  Pulled  3s  kubelet  Container image "python:3.11-slim" already present on machine
```
镜像拉取时间<5秒说明走了本地Harbor缓存，而非外网Docker Hub。

**关键行为说明**：
- K8s YAML中`image: python:3.11-slim`**完全不需要修改**——containerd自动按mirror规则重定向
- mirror配置仅影响`docker.io`域下的镜像，其他Registry的镜像不会被劫持
- 如果Harbor不可达，containerd会**自动回退**到直接访问`docker.io`（需配置`endpoint`的fallback）

### 3.7 步骤六：缓存命中率监控

**目标**：建立Proxy Cache的可观测性，量化缓存带来的带宽节省效果。

```bash
# Harbor暴露Prometheus Metrics在 /metrics 端点
curl -s "https://harbor.company.com/metrics" | grep "harbor_proxy"

# 关键Prometheus指标：
# harbor_proxy_cache_hit_total      —— 缓存命中总次数
# harbor_proxy_cache_miss_total     —— 缓存未命中总次数
# harbor_proxy_cache_fill_bytes_total —— 从上游拉取的总字节数
# harbor_proxy_cache_serve_bytes_total —— 从缓存返回的总字节数
```

**Grafana面板示例查询（PromQL）**：
```promql
# 缓存命中率（最近5分钟）
sum(rate(harbor_proxy_cache_hit_total[5m])) /
(sum(rate(harbor_proxy_cache_hit_total[5m])) + sum(rate(harbor_proxy_cache_miss_total[5m]))) * 100

# 带宽节省量（字节/秒）
rate(harbor_proxy_cache_serve_bytes_total[5m]) - rate(harbor_proxy_cache_fill_bytes_total[5m])
```

### 3.8 常见陷阱

**陷阱一：Docker Hub的`/library`前缀导致路径错乱**

**现象**：Docker Hub上镜像叫`python:3.11`，但在Harbor Proxy Cache中路径变成`proxy-dockerhub/library/python:3.11`，开发者使用不带`/library`前缀的路径时pull失败。

**根因**：Docker Hub的官方镜像（非组织镜像）统一存储在`library`命名空间下，Docker CLI自动补齐该前缀。Harbor Proxy Cache作为独立Registry，不执行这种自动补齐。

**解决方案**：
```bash
# ❌ 错误：缺少 /library 前缀
docker pull harbor.company.com/proxy-dockerhub/python:3.11-slim

# ✅ 正确：带 /library 前缀
docker pull harbor.company.com/proxy-dockerhub/library/python:3.11-slim
docker pull harbor.company.com/proxy-dockerhub/library/nginx:1.25

# ✅ 非官方组织镜像不需要 /library
docker pull harbor.company.com/proxy-dockerhub/bitnami/redis:7.2
```

在containerd mirror模式下，路径转换由containerd自动处理，不会遇到此问题。

**陷阱二：代理缓存项目存储空间暴涨**

**现象**：运行2周后Proxy Cache项目存储使用量突破500GB配额上限，新镜像无法缓存，所有Cache Miss的pull请求直接报错。

**根因**：开发团队和CI系统把使用过的所有镜像（包括大量一次性测试镜像、每日构建的`snapshot` tag、已被废弃的`alpha`版本）全部缓存了进来，且没有任何自动清理策略。

**解决方案**：
```bash
# 三重防护策略
# 1. 标签保留策略：每个仓库仅保留最近5个tag（已在3.5节配置）
# 2. 项目存储配额：Proxy Cache项目设置硬上限500GB，达到后拒绝新缓存
# 3. 定时GC：每天凌晨3点清理孤立blob（已在3.5节配置）
# 4. 可选：限制只缓存特定命名空间
#    在创建Proxy Cache时指定过滤器，只代理docker.io/library/*
```

**陷阱三：私有镜像缓存后的安全泄露风险**

**现象**：团队配置Proxy Cache代理了私有Docker Hub组织的镜像（`mycompany/private-service:v1`），但Proxy Cache项目设为公开——任何能访问Harbor的人都能pull到这个私有镜像，造成安全事件。

**根因**：Proxy Cache项目本身的权限设置覆盖了上游镜像的私有属性——缓存到Harbor后，可见性由**Harbor项目权限**而非上游Registry属性决定。

**解决方案**：
1. 代理私有镜像的Proxy Cache项目必须设为**私有**
2. 仅添加需要访问镜像的团队成员为项目成员（角色：访客）
3. 为公开镜像和私有镜像创建**两个独立的Proxy Cache项目**——例如`proxy-dockerhub-public`（公开）和`proxy-dockerhub-private`（私有）
4. 每季度审计Proxy Cache项目的成员列表，清理离职人员

**陷阱四：DNS解析失败导致所有缓存失效**

**现象**：内网DNS服务故障，Harbor无法解析`docker.io`域名。此时即使镜像已缓存，pull操作仍然失败——因为Harbor每次pull都会尝试连接上游Registry验证。

**根因**：Harbor Proxy Cache在收到pull请求后，如果缓存未过期，会发HEAD请求到上游验证manifest最新性（即使用`If-None-Match`条件请求）。DNS不通导致验证失败，即使缓存有效也无法返回。

**解决方案**：
```bash
# 在Harbor Core容器的hosts文件中预解析关键上游Registry
# docker-compose.yml中为core服务添加:
extra_hosts:
  - "registry-1.docker.io:3.216.168.183"
  - "index.docker.io:3.216.168.183"
  - "quay.io:52.200.156.44"
```
或配置Harbor使用内网DNS缓存服务器（如dnsmasq），提高DNS解析的可靠性。

---

## 4 项目总结

### 4.1 方案对比

| 维度 | Proxy Cache | Replication（复制） | 手动脚本pull | Registry Mirror |
|------|-----------|-------------------|------------|-----------------|
| 触发方式 | Pull时自动按需 | Push事件/定时全量 | 定时任务全量 | Pull时自动 |
| 缓存粒度 | 层级别去重 | 整个镜像复制 | 整个镜像存储 | 层级别去重 |
| 存储效率 | ⭐⭐⭐⭐⭐ | ⭐⭐⭐ | ⭐⭐ | ⭐⭐⭐⭐ |
| 上游新版本感知 | 手动TTL触发 | 事件驱动准实时 | 定时检查 | 跟随上游Registry |
| 运维成本 | ⭐（极低） | ⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐ |
| 上游支持范围 | 任意OCI Registry | 另一Harbor实例 | 任意Registry | 仅部分Registry |
| 离线可用性 | ✅ 缓存期内可用 | ✅ 同步后可用 | ✅ 已拉取的可用 | ✅ |

### 4.2 适用场景

| 场景 | 是否适用 | 说明 |
|------|---------|------|
| 内网环境加速公开基础镜像 | ✅ 强烈推荐 | 核心场景，按需缓存+全员共享，运维零负担 |
| 降低Docker Hub限流对CI/CD的影响 | ✅ 强烈推荐 | 500人团队从500次pull/6h降到1次pull/6h |
| K8s集群基础镜像加速 | ✅ 推荐 | 配合containerd mirror实现零侵入加速 |
| CI/CD构建流水线加速 | ✅ 推荐 | 所有构建节点共享同一份缓存 |
| 跨Region镜像分发 | ⚠️ 部分适用 | 需每Region单独部署Proxy Cache实例 |
| 高频更新的基础镜像（如nightly） | ⚠️ 谨慎使用 | 需配合短TTL（24小时），否则缓存永远过期 |
| 镜像长期归档和灾备 | ❌ 不适用 | 应使用Harbor Replication做全量同步 + 定时备份 |
| 完全离线环境（无外网） | ❌ 不适用 | 首次拉取仍需连接上游，应预先用Replication全量同步 |

### 4.3 注意事项

1. **项目权限管理**：Proxy Cache项目建议设为"公开（只读）"——仅Admin可管理，防止开发人员误push覆盖缓存镜像。
2. **缓存TTL设置**：根据基础镜像更新频率设置合理TTL——稳定基础镜像（如`node:18-alpine`）设7天；频繁更新的开发镜像设24小时。
3. **存储配额必设**：为Proxy Cache项目设置明确存储配额（建议200-500GB）并配置保留策略和定时GC，防止缓存无限膨胀。
4. **上游Registry白名单**：限制Harbor可代理的Registry范围，仅添加可信公共Registry（Docker Hub、Quay.io、GHCR等），防止被利用访问恶意镜像源。
5. **安全扫描联动**：开启Proxy Cache项目的自动漏洞扫描（`auto_scan: true`），确保缓存镜像不含已知高危漏洞，避免"缓存了漏洞"的安全隐患。

### 4.4 常见故障排查

| 故障现象 | 根因 | 解决方案 |
|---------|------|---------|
| `pull access denied` | Proxy Cache项目配额已满 | 扩容配额或执行GC清理过期缓存 |
| `manifest unknown` 反复出现 | 上游该tag已被删除（常见于nightly） | 通知使用者更换tag，该tag无法缓存 |
| 首次pull极慢（>5分钟） | 上游Registry地理位置远 | 非高峰期预热常用镜像，或增加外网带宽 |
| 缓存命中但pull仍有解压耗时 | 客户端本地缺少blob层的解压缓存 | 正常行为——解压/校验在客户端完成，非Harbor问题 |
| K8s mirror不生效 | containerd版本<1.5不支持mirror | 升级containerd至1.6+，或换用CRI-O |
| 所有pull失败+Harbor日志DNS报错 | DNS无法解析上游Registry域名 | 在Harbor hosts文件中预解析关键上游IP |

### 4.5 深度思考

1. **缓存一致性与安全更新的博弈**：假设生产环境要求基础镜像安全补丁在30分钟内部署到所有Pod。Proxy Cache的TTL最小粒度为1小时——这意味着即使TTL设为1小时，缓存中的过期镜像也可能被使用长达59分钟。如何设计一个"事件驱动+主动推送"的缓存刷新方案，在不需要上游Registry提供Push回调的前提下，实现分钟级缓存更新？

2. **成本模型优化**：假设团队每月从Docker Hub拉取80TB数据，Proxy Cache将命中率做到92%——即外网流量降至6.4TB/月。当前外网带宽成本¥0.8/GB，Harbor本地SSD存储成本¥500/TB/月。计算盈亏平衡点：如果命中率下降到多少百分比时，本地缓存的存储成本超过带宽节省？如果需要代理10个不同的公共Registry，是创建1个大Proxy Cache项目经济，还是10个独立项目？

---

> 下一章预告：第30章将探索Harbor的P2P镜像分发方案——Dragonfly/Nydus大规模集群镜像加速，解决1000+Node规模的带宽瓶颈。
