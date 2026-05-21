# 第3章：仓库类型入门：Hosted、Proxy、Group

## 1. 项目背景

云鲸科技的 Nexus 已经跑起来了，炮哥把登录地址发到了群里。Java 组的老王第一个跳出来："你们这仓库列表里一片空白，我 settings.xml 里 mirror 地址该配哪个？"前端组的阿玲紧随其后："我们 npm 想用公司私服，registry 填什么？"炮哥看着控制台一脸茫然——他只知道 Nexus 是个"网站"，至于里面要建什么东西、怎么建、有什么区别，完全没概念。

这正中 Nexus 新手最核心的困惑：仓库（Repository）不是只有一个地址就够了吗？为什么要分 hosted、proxy、group 三种类型？每种类型分别在什么场景下使用？三种仓库如何组合才能让开发者只配一个仓库地址、却同时能拉取内部包和公网包？

本章将用生鲜超市的比喻，彻底讲透 Nexus 仓库类型的设计哲学。你将亲手在 Nexus 中创建 hosted、proxy、group 三类仓库，并验证客户端只配置 group 一个地址就能覆盖内部发布和远程代理两种场景——这是所有后续格式实战（Maven、npm、Docker）的通用模板。

## 2. 项目设计

炮哥把 Nexus 仓库创建页面投屏到大屏幕上，列表里只有几个默认仓库。

**炮哥**："这 system 开头的仓库是干嘛的？里面的 maven-central 能不能直接用？"

**大师**："system 仓库是 Nexus 自带的，存储 Nexus 内部插件和配置。maven-central 是内置的 proxy 仓库，指向 Maven 中央仓库。可以直接用，但不建议——你后续需要调整缓存策略、关联清理策略时，修改内置仓库风险大。企业最佳实践：创建自己命名的仓库，保持可控。"

**小胖**（嚼着巧克力棒）："大师，创建仓库页面有三大类——hosted、proxy、group。这啥意思？我能不能只建一个就把所有事儿干了？"

**大师**："好，我们搞一个生鲜超市的比喻。假设云鲸科技楼下要开一个内部超市——"

**小胖**："这我喜欢！三个分类是不是对应超市的三种货架？"

**大师**："正是。**Hosted 仓库**就是超市的自营品牌——你自己生产、自己上架、自己定价，比如云鲸的招牌烤鸭（公司自研 jar 包）。别人买不到，只有你能供应。**Proxy 仓库**是进口商品代购柜——消费者想买法国奶酪（Maven Central 上的 guava），超市没有产，但可以去法国帮你进货，顺便多进一批放仓库里，下次别人再要就直接从仓库拿，不用再跑一趟法国。"

**小白**："那 Group 仓库呢？"

**大师**："**Group 仓库**是超市的一站式服务台——你只去一个窗口，告诉店员你要什么，店员会根据你的需求，先去自营区（hosted）找，找不到再去代购区（proxy）帮你调货。对消费者来说，他根本不需要知道商品是从哪个区来的，只需要记住一个服务台位置。"

> **技术映射**：hosted = 企业自建仓库（发布内部制品），proxy = 远程代理仓库（缓存外部制品），group = 组合仓库（聚合多个 hosted/proxy 对外暴露统一入口）。

**小白**："那如果同一个 group 里，两个 hosted 仓库都有同名同版本的包呢？比如 maven-releases 和 maven-thirdparty 都有 `my-lib:1.0.0`？"

**大师**："问得好。group 仓库按**成员排列顺序**依次查找，第一个命中就返回，后面直接忽略。所以排序极其关键——否则就会出现你发布的内测包没被用到、却拉到了旧版本的问题。原则是：**hosted 永远排在 proxy 前面**。同一个 group 中的多个 hosted 之间，核心包仓库排在扩展包仓库前面。"

**炮哥**："那仓库的格式（format）又是怎么回事？比如 Maven 和 npm 的 hosted 仓库内部结构一样吗？"

**大师**："仓库类型（hosted/proxy/group）是组织维度，格式（format）是内容维度。打个比方：仓库类型决定这个房间是自营/代购/服务台，格式决定房间里的货架适合放什么类型的商品——Maven 格式的货架能识别 pom.xml 和坐标，npm 格式的货架能识别 package.json 和 tarball。同一间自营房间，不能既放 Maven 又放 npm，必须在创建时选定格式。"

> **技术映射**：仓库类型 × 格式 = 6 种组合（hosted × Maven、proxy × Maven、group × Maven 等）。Nexus 通过 Format + Recipe + Facet 实现格式无关的仓库抽象。

**小胖**："那就是说，如果我既要存 Maven 包，又要存 npm 包，就要建两组仓库：`maven-hosted + maven-proxy + maven-group` 和 `npm-hosted + npm-proxy + npm-group`？"

**大师**："完全正确。每种格式的 hosted+proxy+group 是一个标准仓库套件。云鲸现在有 Java、Node、Docker、Raw 四条业务线，至少需要 4×3=12 个仓库。这就是为什么实际企业 Nexus 里仓库列表会很长——不是你配错了，是规模到了。"

**炮哥**："那 proxy 仓库的缓存策略怎么设？如果 Maven Central 某个包明天更新了，我今天的缓存什么时候会刷新？"

**大师**："这就是 proxy 仓库的两个关键参数：**Metadata Max Age** 和 **Content Max Age**。Metadata Max Age 控制元数据（如 maven-metadata.xml）的刷新频率，默认 1440 分钟（24 小时）。Content Max Age 控制实际文件（如 jar）的缓存时间，默认 -1（永不过期，直到远程文件变化）。实际中，snapshot 仓库的 Metadata Max Age 要设短一些（比如 5 分钟），release 仓库可以设长一些。"

## 3. 项目实战

### 3.1 环境准备

- 已按第 2 章部署好 Nexus 实例（http://localhost:8081）
- admin 账号已修改密码
- curl 命令行工具

### 3.2 分步实战：创建 Maven 标准仓库套件

#### 步骤一：创建 Maven Hosted 仓库（企业自建）

**目标**：创建用于存放内部发布制品的仓库。

```bash
# 创建 maven-releases（存放 RELEASE 版本）
curl -u admin:admin123 -X POST \
  "http://localhost:8081/service/rest/v1/repositories/maven/hosted" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "maven-releases",
    "online": true,
    "storage": {
      "blobStoreName": "default",
      "strictContentTypeValidation": true,
      "writePolicy": "ALLOW_ONCE"
    },
    "maven": {
      "versionPolicy": "RELEASE",
      "layoutPolicy": "STRICT"
    }
  }'

# 创建 maven-snapshots（存放 SNAPSHOT 版本）
curl -u admin:admin123 -X POST \
  "http://localhost:8081/service/rest/v1/repositories/maven/hosted" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "maven-snapshots",
    "online": true,
    "storage": {
      "blobStoreName": "default",
      "strictContentTypeValidation": true,
      "writePolicy": "ALLOW"
    },
    "maven": {
      "versionPolicy": "SNAPSHOT",
      "layoutPolicy": "STRICT"
    }
  }'
```

**运行结果**：两个 hosted 仓库创建成功，返回 HTTP 201。在 Web UI `Repository → Repositories` 中可以看到新增条目。

**参数说明**：
- `writePolicy: ALLOW_ONCE`：release 仓库禁止覆盖已有制品，一旦发布不可修改（制品不可变原则）
- `writePolicy: ALLOW`：snapshot 仓库允许覆盖，同一个 SNAPSHOT 版本可以多次发布
- `versionPolicy`：RELEASE 仓库拒绝 SNAPSHOT 版本上传，反之亦然

#### 步骤二：创建 Maven Proxy 仓库（远程代理缓存）

**目标**：创建代理 Maven Central 的缓存仓库，加速依赖下载。

```bash
# 创建 maven-central-proxy（代理 Maven Central）
curl -u admin:admin123 -X POST \
  "http://localhost:8081/service/rest/v1/repositories/maven/proxy" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "maven-central-proxy",
    "online": true,
    "storage": {
      "blobStoreName": "default",
      "strictContentTypeValidation": true
    },
    "proxy": {
      "remoteUrl": "https://repo1.maven.org/maven2/",
      "contentMaxAge": -1,
      "metadataMaxAge": 1440
    },
    "maven": {
      "versionPolicy": "RELEASE",
      "layoutPolicy": "PERMISSIVE"
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
        "timeout": 120
      }
    }
  }'
```

**运行结果**：Proxy 仓库创建成功，状态为 "Online - Remote Available"。在 Web UI 中可以看到 `Remote URL` 字段指向 Maven Central。

**关键参数解读**：
- `contentMaxAge: -1`：缓存内容永不过期，仅当远程文件变化时更新
- `metadataMaxAge: 1440`：元数据 24 小时后重新检查（`maven-metadata.xml`）
- `negativeCache`：远程返回 404 时，记录"此路径不存在"，1440 分钟内不再尝试
- `autoBlock: true`：远程仓库连续不可用时自动冻结，避免雪崩

#### 步骤三：创建 Maven Group 仓库（统一入口）

**目标**：将 hosted 和 proxy 聚合到一个 group，给客户端提供唯一入口。

```bash
# 创建 maven-public（group 仓库）
curl -u admin:admin123 -X POST \
  "http://localhost:8081/service/rest/v1/repositories/maven/group" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "maven-public",
    "online": true,
    "storage": {
      "blobStoreName": "default",
      "strictContentTypeValidation": true
    },
    "group": {
      "memberNames": [
        "maven-releases",
        "maven-snapshots",
        "maven-central-proxy"
      ]
    }
  }'
```

**运行结果**：Group 仓库创建成功。在 Web UI 中 `maven-public` 的成员列表显示三个仓库，排列顺序为 `maven-releases → maven-snapshots → maven-central-proxy`。

**顺序关键点**：hosted 仓库排在 proxy 仓库之前，确保内部版本优先被消费。

#### 步骤四：配置 Maven 客户端验证

**目标**：修改 `settings.xml` 只配 group 地址，验证依赖下载。

编辑 `~/.m2/settings.xml`（Windows: `%USERPROFILE%\.m2\settings.xml`）：

```xml
<?xml version="1.0" encoding="UTF-8"?>
<settings>
  <servers>
    <server>
      <id>nexus-public</id>
      <username>admin</username>
      <password>admin123</password>
    </server>
  </servers>
  <mirrors>
    <mirror>
      <id>nexus-public</id>
      <mirrorOf>*</mirrorOf>
      <url>http://localhost:8081/repository/maven-public/</url>
    </mirror>
  </mirrors>
</settings>
```

验证：

```bash
# 创建一个临时 Maven 项目测试下载
mkdir -p /tmp/test-nexus && cd /tmp/test-nexus
cat > pom.xml << 'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<project>
  <modelVersion>4.0.0</modelVersion>
  <groupId>com.example</groupId>
  <artifactId>test</artifactId>
  <version>1.0</version>
  <dependencies>
    <dependency>
      <groupId>com.google.guava</groupId>
      <artifactId>guava</artifactId>
      <version>31.1-jre</version>
    </dependency>
  </dependencies>
</project>
EOF

# 清除本地缓存，确保从 Nexus 拉取
rm -rf ~/.m2/repository/com/google/guava

# 执行编译，触发依赖下载
mvn compile -s ~/.m2/settings.xml

# 预期输出：
# [INFO] Downloading from nexus-public: http://localhost:8081/repository/maven-public/com/google/guava/guava/31.1-jre/guava-31.1-jre.pom
# [INFO] Downloaded from nexus-public: ...
# [INFO] BUILD SUCCESS
```

**运行结果**：Maven 通过 `maven-public` 这个 group 地址成功下载了 guava。第一次下载较慢（proxy 需从远程拉取），第二次再编译时瞬间完成（本地缓存命中）。这就是 proxy 仓库的核心价值——一次拉取，全员加速。

#### 步骤五：验证 group 仓库的查找优先级

```bash
# 在 Nexus Web UI 或 API 中查看 maven-public 的成员顺序
curl -u admin:admin123 \
  "http://localhost:8081/service/rest/v1/repositories/maven/group/maven-public" | jq '.group.memberNames'

# 预期输出：
# [
#   "maven-releases",
#   "maven-snapshots",
#   "maven-central-proxy"
# ]
```

### 3.3 常见坑点

| 坑点 | 现象 | 解决方法 |
|------|------|----------|
| Group 成员顺序错误 | 内部包被远程版本覆盖 | 将 hosted 仓库移至 proxy 前面 |
| Proxy 的 Remote URL 写错 | 所有远程依赖下载失败 | 检查 URL 结尾是否有 `/`（大部分需要），Maven Central 正确地址是 `https://repo1.maven.org/maven2/` |
| Hosted 的 writePolicy 选错 | RELEASE 仓库拒绝了 SNAPSHOT 上传 | 严格区分：RELEASE 仓库设 ALLOW_ONCE，SNAPSHOT 仓库设 ALLOW |
| 忘记选格式 | 创建了 Raw 仓库后才发现要存 Maven | 仓库格式不可变，只能删除重建 |
| 网络代理未配置 | 公司内网下 Proxy 仓库无法连接外网 | 在 `Administration → System → HTTP` 中配置企业网络代理 |

## 4. 项目总结

### 4.1 仓库类型速查表

| 类型 | 用途 | 典型生命周期 | 是否产生网络请求到外网 | 上传权限 | 下载权限 |
|------|------|-------------|----------------------|---------|---------|
| Hosted | 企业内部发布制品 | 开发→发布→归档 | 否 | 授权用户 | 授权用户 |
| Proxy | 缓存远程仓库制品 | 响应远程变化 | 是（按缓存策略） | 否 | 授权用户 |
| Group | 聚合多个仓库为单一入口 | 随成员仓库变化 | 取决于成员 | 否 | 授权用户 |

### 4.2 适用场景

1. **所有格式的标准初始化**：每个格式（Maven、npm、Docker、Raw 等）都应创建 hosted+proxy+group 套件
2. **开发环境加速**：Proxy 仓库缓存公网依赖后，团队内所有成员享受局域网速度
3. **内网隔离环境**：在可联网的 Nexus 上 proxy 缓存全部依赖，导出后导入内网 Nexus
4. **制品晋级路径**：dev-snapshots（hosted）→ test-releases（hosted）→ prod-releases（hosted），通过 group 控制可见性
5. **多团队共享 vs 隔离**：公共基础包放共享 hosted，团队私有包放各自 hosted，用 group 控制可见性

**不适用场景**：
1. 团队人数 < 3 且所有依赖直接拉公网毫秒级可达——收益太小
2. 仅需 Docker 镜像管理——直接使用 Harbor 更专业

### 4.3 注意事项

- Group 仓库中的成员数量不宜超过 20 个，过多会影响查找性能
- Proxy 仓库的 `autoBlock` 建议开启：远程仓库连续不可用时自动冻结，避免客户端请求堆积超时
- `negativeCache` 的 `timeToLive` 不要设太大：如果远程仓库确实新发布了一个包而之前返回了 404，缓存期结束前客户端永远拉不到
- 生产环境建议为每个 hosted 仓库创建独立的 BlobStore，便于磁盘容量管理和清理

### 4.4 常见踩坑经验

**故障一：Group 仓库包含已删除的成员仓库**

运维删除了一个旧的 `maven-legacy-hosted` 仓库，但忘记从 `maven-public` group 中移除引用，导致所有通过 group 的请求都报错。排查路径：查看 `maven-public` 成员列表，发现存在红色标记的"已删除"成员。解决：通过 API 更新 group 成员列表，移除无效引用。

**故障二：Proxy 缓存"毒化"**

某内部开发误将一个测试用的 SNAPSHOT 包发布到了 Maven Central（尽管不应该），Nexus proxy 缓存了这个版本。之后本地开发使用同名 RELEASE 版本时总被这个缓存版本覆盖。解决：手动在 Browse 页面删除该 proxy 缓存条目，触发重新拉取。

**故障三：Group 仓库 URL 路径不同于成员仓库 URL**

新手经常犯的错误：把 group 仓库 URL `http://nexus:8081/repository/maven-public/` 和 hosted 仓库 URL `http://nexus:8081/repository/maven-releases/` 搞混——前者用于下载（读），后者用于 deploy（写）。Maven 的 `distributionManagement` 应指向具体的 hosted 仓库，而 `mirror` 应指向 group 仓库。

### 4.5 思考题

1. 如果公司有 5 个 Java 项目团队，每个团队都需要发布内部包，但基础框架包（如 common-utils）希望所有团队共享。如何设计 hosted 仓库和 group 仓库来兼顾隔离与共享？
2. Proxy 仓库的 `contentMaxAge: -1` 意味着缓存永不过期，但为什么我们仍然能拉取到远程仓库的最新版本？（提示：远程文件的 Last-Modified 时间戳和 ETag）

（第2章思考题答案：1. 端口映射需不同（如 8081 和 9081），volume 目录需分隔（如 ./nexus-data-dev 和 ./nexus-data-test），容器名需不同。2. `"frozen": true` 表示 Nexus 进入只读模式（Read-Only Mode），通常由磁盘空间不足自动触发。管理员可通过 `Administration → System → Freeze` 手动冻结/解冻。冻结期间禁止所有写入操作，用于安全备份和数据恢复场景。）

### 4.6 推广计划提示

- **开发部门**：重点关注仓库标准套件的命名规范和客户端配置方法，每种新格式接入时能独立创建仓库套件
- **运维部门**：本章是仓库规划的起点，后续章节的所有自动化脚本都基于本章的仓库结构
- **测试部门**：理解 proxy 仓库的缓存延迟特性，排查"新版本下载不到"问题时需要知道如何手动刷新缓存
