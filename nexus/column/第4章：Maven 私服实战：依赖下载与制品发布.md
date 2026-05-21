# 第4章：Maven 私服实战：依赖下载与制品发布

## 1. 项目背景

云鲸科技的 Java 组有 25 人，维护着 12 个微服务项目和 3 个公共基础库。近三个月，老王每周至少处理两次"依赖找不到"的报警，问题五花八门：有人在自己本地 `mvn install` 了一个 SNAPSHOT 版本，CI 节点上却编译失败；有人更新了 `common-utils` 的接口签名忘了改版本号，导致下游项目运行时 `NoSuchMethodError`；还有人把生产用的数据库密码写进了 jar 包里打了个 release，被发现时已在制品仓库躺了两周。

更棘手的是，公司安全合规要求"生产环境的所有依赖必须经过内部审核仓库，禁止直接从公网下载"。没有 Maven 私服时，做法是"审核通过后把 jar 拷贝到共享目录"——版本管理靠文件名，依赖传递靠人肉，每次审计都是噩梦。

Maven 私服是 Nexus 最经典、最成熟的场景。本章将以云鲸科技的 Spring Boot 微服务架构为例，从 `settings.xml` 配置讲起，带你走通 snapshots 发布、releases 发布、依赖下载、版本冲突排查的全链路，并建立一套可落地的企业 Maven 仓库命名与发布规范。

## 2. 项目设计

老王用投影展示了 Maven 项目的 `pom.xml`，脸上愁云惨淡。

**老王**："大师，我们现在有 `common-utils`、`common-security`、`common-data` 三个基础库，还有 12 个业务服务。每次基础库更新，我都要在群里喊'大家更新一下本地仓库'，然后一定是有人没更新，CI 挂了又找我。Nexus 能解决这个问题吗？"

**大师**："能，而且比你想象得更彻底。我们先理清 Maven 世界里三个核心文件各自做什么——`pom.xml`、`settings.xml`、`~/.m2/repository/`。"

**小胖**："我知道！pom.xml 是项目配置，settings.xml 是 Maven 全局配置，repository 是本地缓存。但这跟 Nexus 有什么关系？"

**大师**："关系大了。你现在的 settings.xml 大概率什么都没配，Maven 默认直连 Maven Central。加 Nexus 后，你要做三件事：一，配 mirror，把 Maven Central 的请求劫持到 Nexus group；二，配 server，告诉 Maven 上传制品的认证凭据；三，配 profile 或 repository（可选），精确控制依赖解析来源。"

> **技术映射**：mirror 是"旁路路由"——拦截所有去往 `*` 仓库的请求，重定向到 Nexus group 地址；server 是"门禁卡"——Maven 发布上传时自动携带的认证信息。

**小白**："说到 SNAPSHOT 和 RELEASE，我一直有个困惑——为什么 Maven 非要分这两个概念？git 分支不就能区分开发版和稳定版吗？"

**大师**："因为 Maven 依赖解析是**被动消费**，不是主动选择。你依赖了 `common-utils:1.0-SNAPSHOT`，和依赖 `common-utils:1.0`，Maven 的行为完全不同。SNAPSHOT 每次编译都会检查远程仓库是否有新版本（时间戳版本），自动下载最新；RELEASE 拉取一次就放本地缓存，除非手动删除或版本号变化，否则不再更新。这决定了——**开发联调期用 SNAPSHOT，正式发版用 RELEASE**。"

**老王**："所以我们的问题是：全体都在用 SNAPSHOT，但生产环境应该锁定 RELEASE 版本？"

**大师**："正是。但 RELEASE 还有一条铁律——**一旦发布，不可覆盖**。Nexus hosted 仓库的 `writePolicy: ALLOW_ONCE` 就是这个意思。你想改一个已发布的 1.0.0？不行，只能发布 1.0.1。这看起来是限制，实际上是保护——保护你的下游不会莫名其妙拿到不同的代码。"

> **技术映射**：SNAPSHOT = 允许覆盖 + 自动检查最新 + 时间戳版本号；RELEASE = 不可覆盖 + 缓存持久化 + 唯一版本号。二者的语义差异对应软件开发生命周期中的"不稳定态"和"稳定态"。

**小胖**："那 settings.xml 里的 mirrorOf 填 `*` 和填 `central` 有区别吗？"

**大师**："`mirrorOf=*` 表示拦截**所有**仓库请求，包括你自己在 pom.xml 里声明的其他远程仓库。`mirrorOf=central` 只拦截 Maven Central。新手建议用 `*`，简单粗暴，避免漏网之鱼。但如果你的项目需要同时从多个不同远程仓库拉取（比如既有 Maven Central 也有 JCenter），就要精细配置。"

**小白**："401 Unauthorized 和 403 Forbidden 呢？我经常搞混。"

**大师**："401 = 你没带认证信息或认证失败。403 = 你认证通过了，但你没权限。在 Nexus 场景下，401 通常是 settings.xml 里没配 server 或密码错了；403 通常是你的账号没有该仓库的写入权限。"

**老王**："那我把 settings.xml 提交到 git 仓库里是不是就一劳永逸了？"

**大师**（摇头）："绝对不要！settings.xml 里有明文密码。正确的做法有两种：一是把 settings.xml 放在每个人本地的 `~/.m2/` 下；二是在 CI 中用环境变量注入密码，配合 Maven 的 `settings-security.xml` 加密。云鲸的 CI（Jenkins）用凭据管理器注入，开发本地手动配——各管各的，密码永远不进代码仓库。"

## 3. 项目实战

### 3.1 环境准备

- 已按第 3 章创建好 `maven-releases`、`maven-snapshots`、`maven-central-proxy`、`maven-public` 四个仓库
- JDK 17+、Maven 3.8+
- curl

### 3.2 分步实战

#### 步骤一：配置 Maven settings.xml

**目标**：配置 mirror 指向 Nexus group，配置 server 用于发布认证。

编辑 `~/.m2/settings.xml`：

```xml
<?xml version="1.0" encoding="UTF-8"?>
<settings xmlns="http://maven.apache.org/SETTINGS/1.0.0"
          xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
          xsi:schemaLocation="http://maven.apache.org/SETTINGS/1.0.0
                              http://maven.apache.org/xsd/settings-1.0.0.xsd">
  <!-- 服务器认证信息（仅用于 deploy，不用于 download） -->
  <servers>
    <server>
      <id>nexus-releases</id>
      <username>admin</username>
      <password>admin123</password>
    </server>
    <server>
      <id>nexus-snapshots</id>
      <username>admin</username>
      <password>admin123</password>
    </server>
  </servers>

  <!-- Mirror：拦截所有仓库请求，重定向到 Nexus group -->
  <mirrors>
    <mirror>
      <id>nexus-public</id>
      <mirrorOf>*</mirrorOf>
      <url>http://localhost:8081/repository/maven-public/</url>
    </mirror>
  </mirrors>

  <!-- 可选：也配置一个 profile，明确依赖解析来源 -->
  <profiles>
    <profile>
      <id>nexus</id>
      <repositories>
        <repository>
          <id>nexus-public</id>
          <url>http://localhost:8081/repository/maven-public/</url>
          <releases><enabled>true</enabled></releases>
          <snapshots><enabled>true</enabled></snapshots>
        </repository>
      </repositories>
    </profile>
  </profiles>
  <activeProfiles>
    <activeProfile>nexus</activeProfile>
  </activeProfiles>
</settings>
```

#### 步骤二：创建一个示例 Spring Boot 组件

**目标**：构建一个可发布的 Maven 组件。

```bash
# 创建项目目录
mkdir -p ~/nexus-demo/common-utils && cd ~/nexus-demo/common-utils

# 编写 pom.xml
cat > pom.xml << 'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0"
         xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
         xsi:schemaLocation="http://maven.apache.org/POM/4.0.0
                             http://maven.apache.org/xsd/maven-4.0.0.xsd">
    <modelVersion>4.0.0</modelVersion>
    <groupId>com.cloudwhale</groupId>
    <artifactId>common-utils</artifactId>
    <version>1.0-SNAPSHOT</version>
    <packaging>jar</packaging>
    <name>CloudWhale Common Utils</name>

    <distributionManagement>
        <snapshotRepository>
            <id>nexus-snapshots</id>
            <url>http://localhost:8081/repository/maven-snapshots/</url>
        </snapshotRepository>
        <repository>
            <id>nexus-releases</id>
            <url>http://localhost:8081/repository/maven-releases/</url>
        </repository>
    </distributionManagement>
</project>
EOF

# 编写一个简单的工具类
mkdir -p src/main/java/com/cloudwhale/common

cat > src/main/java/com/cloudwhale/common/StringUtils.java << 'EOF'
package com.cloudwhale.common;

public class StringUtils {
    public static boolean isBlank(String str) {
        return str == null || str.trim().isEmpty();
    }

    public static String capitalize(String str) {
        if (isBlank(str)) return str;
        return str.substring(0, 1).toUpperCase() + str.substring(1);
    }
}
EOF
```

#### 步骤三：发布 SNAPSHOT 版本到 Nexus

**目标**：将 SNAPSHOT 版本 deploy 到 Nexus hosted 仓库。

```bash
cd ~/nexus-demo/common-utils

# 发布 SNAPSHOT（会自动生成时间戳版本号）
mvn deploy -s ~/.m2/settings.xml

# 预期输出关键行：
# [INFO] Uploading to nexus-snapshots:
#   http://localhost:8081/repository/maven-snapshots/com/cloudwhale/common-utils/1.0-SNAPSHOT/common-utils-1.0-20250115.143210-1.jar
# [INFO] BUILD SUCCESS
```

**运行结果**：
- pom.xml 中的 `<distributionManagement>` 告诉 Maven deploy 的目标地址
- settings.xml 中的 `<server><id>nexus-snapshots</id>` 提供了认证凭据
- Nexus 自动将 `1.0-SNAPSHOT` 展开为带时间戳的唯一版本 `1.0-20250115.143210-1`
- 同时上传了 jar、pom、以及 `maven-metadata.xml`

验证上传结果：

```bash
# 通过 API 查询组件
curl -u admin:admin123 \
  "http://localhost:8081/service/rest/v1/search?repository=maven-snapshots&name=common-utils" | jq .

# 预期输出：
# {
#   "items": [{
#     "group": "com.cloudwhale",
#     "name": "common-utils",
#     "version": "1.0-SNAPSHOT",
#     "assets": [...]
#   }]
# }
```

#### 步骤四：发布 RELEASE 版本到 Nexus

**目标**：将版本号改为 RELEASE 后正式发布。

```bash
cd ~/nexus-demo/common-utils

# 修改版本号为 RELEASE
mvn versions:set -DnewVersion=1.0.0

# 发布 RELEASE（注意：会自动跳过 SNAPSHOT repository，deploy 到 release repository）
mvn deploy -s ~/.m2/settings.xml

# 预期输出：
# [INFO] Uploading to nexus-releases:
#   http://localhost:8081/repository/maven-releases/com/cloudwhale/common-utils/1.0.0/common-utils-1.0.0.jar
# [INFO] BUILD SUCCESS
```

**运行结果**：RELEASE 版本成功发布。尝试再次 deploy 相同版本：

```bash
mvn deploy -s ~/.m2/settings.xml

# 预期输出：
# [ERROR] Failed to deploy artifacts: Could not transfer artifact ... 
#   Return code is: 400, ReasonPhrase: Repository "maven-releases" 
#   does not allow updating assets.
```

这就是 `writePolicy: ALLOW_ONCE` 的作用——RELEASE 不可覆盖，保护下游稳定性。

#### 步骤五：在另一个项目中消费已发布的制品

**目标**：创建下游消费者项目，依赖刚发布的 common-utils。

```bash
# 创建消费者项目
mkdir -p ~/nexus-demo/customer-service && cd ~/nexus-demo/customer-service

cat > pom.xml << 'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0"
         xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
         xsi:schemaLocation="http://maven.apache.org/POM/4.0.0
                             http://maven.apache.org/xsd/maven-4.0.0.xsd">
    <modelVersion>4.0.0</modelVersion>
    <groupId>com.cloudwhale</groupId>
    <artifactId>customer-service</artifactId>
    <version>1.0.0</version>

    <dependencies>
        <dependency>
            <groupId>com.cloudwhale</groupId>
            <artifactId>common-utils</artifactId>
            <version>1.0.0</version>
        </dependency>
    </dependencies>
</project>
EOF

# 编写引用代码
mkdir -p src/main/java/com/cloudwhale/customer

cat > src/main/java/com/cloudwhale/customer/CustomerApp.java << 'EOF'
package com.cloudwhale.customer;

import com.cloudwhale.common.StringUtils;

public class CustomerApp {
    public static void main(String[] args) {
        String name = "cloudwhale";
        System.out.println(StringUtils.capitalize(name)); // 输出: Cloudwhale
    }
}
EOF

# 编译测试（会从 Nexus 下载 common-utils:1.0.0）
mvn compile -s ~/.m2/settings.xml

# 预期输出：
# [INFO] Downloading from nexus-public: common-utils-1.0.0.jar
# [INFO] BUILD SUCCESS
```

**运行结果**：消费者项目成功通过 Nexus 下载了内部发布的 RELEASE 包，无需任何共享目录或手动拷贝。

#### 步骤六：验证 SNAPSHOT 自动更新机制

```bash
# 返回 common-utils，再次修改并 deploy
cd ~/nexus-demo/common-utils
mvn versions:set -DnewVersion=1.0-SNAPSHOT

# 修改代码（增加一个方法）
cat >> src/main/java/com/cloudwhale/common/StringUtils.java << 'EOF'

    public static boolean isNotBlank(String str) {
        return !isBlank(str);
    }
EOF

# 执行 deploy（SNAPSHOT 允许多次覆盖）
mvn deploy -s ~/.m2/settings.xml -DskipTests

# 回到 customer-service，强制更新 SNAPSHOT
cd ~/nexus-demo/customer-service
mvn versions:set -DnewVersion=1.0-SNAPSHOT
sed -i 's/<version>1.0.0<\/version>/<version>1.0-SNAPSHOT<\/version>/' pom.xml

# 使用 -U 参数强制检查 SNAPSHOT 更新
mvn compile -U -s ~/.m2/settings.xml

# 预期：编译成功，能够使用新增的 isNotBlank 方法
```

**运行结果**：`-U` 参数强制 Maven 检查远程仓库中所有 SNAPSHOT 依赖是否有更新。不加 `-U` 时，Maven 默认一天只检查一次 SNAPSHOT 更新（由 `settings.xml` 中的 `updatePolicy` 控制）。

### 3.3 常见坑点

| 坑点 | 现象 | 解决方法 |
|------|------|----------|
| SNAPSHOT 不更新 | 明明发了新 SNAPSHOT，但下游编译用的还是旧版本 | `mvn -U compile` 强制更新，或删除 `~/.m2/repository` 中对应目录 |
| 401 认证失败 | deploy 报 `401 Unauthorized` | 检查 settings.xml 中 `<server><id>` 是否与 pom.xml 中 `<distributionManagement>` 的 `<id>` 一致 |
| 400 不允许覆盖 | deploy RELEASE 报 `400` | 确认目标仓库 `writePolicy` 是否为 `ALLOW_ONCE`，如需覆盖请先通过 API 删除旧版本 |
| metadata 文件损坏 | Nexus 中能搜到包但 Maven 下载失败 | 在 Nexus 中执行 `Rebuild Maven repository metadata` 任务 |
| mirrorOf 配置不完整 | 依赖中包含来自非中央仓库的包，下载失败 | 确认 `mirrorOf` 的值覆盖了所有需要的仓库，常用 `*` 或 `external:*` |

## 4. 项目总结

### 4.1 优缺点对比

| 维度 | Nexus Maven 私服 | 本地 ~/.m2 拷贝 | 项目内 lib 目录 |
|------|------------------|-----------------|-----------------|
| 版本一致性 | ✅ 统一仓库，版本唯一 | ❌ 谁本地有就是谁的版本 | ❌ jar 文件无版本元数据 |
| 重复下载 | ✅ proxy 缓存，全队共享 | ❌ 每人独立下载 | ❌ 每个人都要上传到 git |
| 发布审核 | ✅ 权限 + 审计 | ❌ 无法审计 | ❌ 无法审计 |
| 依赖传递 | ✅ 自动解析 | ❌ 手动管理 | ❌ 手动拷贝 |
| 入门门槛 | ⚠️ 需理解 settings.xml 三要素 | ✅ 零配置 | ✅ 零配置 |
| git 仓库体积 | ✅ 不污染代码仓库 | ✅ 不影响 | ❌ 严重膨胀 |

### 4.2 适用场景

1. **Java 微服务架构**：多个服务共享基础 jar 包，通过私服统一分发
2. **安全合规要求**：所有依赖必须经过内部审核和扫描
3. **内网构建加速**：CI 节点不需要每次去公网下载 Maven 依赖
4. **多环境制品晋级**：dev-SNAPSHOT → test-candidate → prod-RELEASE
5. **开源组件漏洞治理**：当某个开源 jar 发现 CVE 时，通过私服快速定位使用者

**不适用场景**：
1. 单工程、无内部共享组件的场景（使用 `mvn install` 到本地即可）
2. 已全面转向 Gradle 且无 Maven 遗留——Gradle 私服配置在后续章节讲解

### 4.3 注意事项

- `server` 的 `id` 必须与 `distributionManagement` 中的 `id` **完全一致**，这是 Maven 凭据匹配的唯一标识
- release 版本发布后，不要修改 `distributionManagement` 的 URL，否则已发布的下游项目路径不会自动更新
- `maven-metadata.xml` 是 SNAPSHOT 时间戳版本解析的关键文件，如果该文件损坏，所有依赖该 SNAPSHOT 的下游构建都会失败
- 企业建议：每个团队的 hosted 仓库独立创建，公共基础组件有一个单独的 hosted 仓库，通过 group 控制可见性

### 4.4 常见踩坑经验

**故障一：SNAPSHOT 带时间戳的版本号令下游困惑**

某新人开发同学在排查 Bug 时发现运行中的 jar 叫 `common-utils-1.0-20250115.143210-1.jar`，但代码里声明的是 `1.0-SNAPSHOT`，以为版本号弄错了。根因：Maven 在 deploy SNAPSHOT 时会自动展开为带时间戳的唯一版本名，这是正常行为。告知团队后，在 README 中增加了说明。

**故障二：settings.xml 被多人覆盖导致配置丢失**

团队共用一个跳板机，settings.xml 存在共享目录，被人误改导致全员 deploy 失败。根因：没有个人隔离的 settings.xml。解决：要求每个人在自己的 `~/.m2/` 下维护 settings.xml，CI 通过环境变量注入。

**故障三：错误地将生产密码写入了 source.jar**

某团队在公共工具类中硬编码了数据库连接字符串，deploy 发布后该 jar 在生产环境被引用。安全扫描发现后紧急修复。根因：代码审查未覆盖硬编码敏感信息。解决：增加 CI 扫描规则（如 truffleHog），同时 Nexus 端通过 Webhook 在组件上传时触发自动化扫描。

### 4.5 思考题

1. 如果 `maven-public` group 中包含 `maven-releases` 和 `maven-snapshots` 两个 hosted 仓库，当客户端声明依赖 `common-utils:1.0.0` 时，Nexus 会同时查询两个 hosted 仓库吗？如果 `maven-snapshots` 仓库中碰巧也有一个 `1.0.0` 的包（虽然是快照仓库），会返回它吗？
2. 你正在设计一个零停机升级的基础库发布策略。有什么办法可以让下游项目在升级 `common-utils` 时不中断，同时又能快速回滚？（提示：版本号策略 + proxy 缓存）

（第3章思考题答案：1. 创建两个 hosted 仓库：`maven-public-shared`（共享基础包）和 `maven-teamA-releases`、`maven-teamB-releases`（各自的私有包）。每个团队的 group 仓库只包含 `maven-public-shared` + 自己的 hosted + proxy。如果有跨团队消费的需求，再创建一个更大的 group 包含所需成员。2. Proxy 仓库在响应客户端请求时，会向远程仓库发送条件请求（包含 `If-Modified-Since` 或 `If-None-Match` 头），远程返回 `304 Not Modified` 则使用本地缓存，返回 `200` 则更新缓存。所以即使 `contentMaxAge: -1`，文件变化时仍然能更新。）

### 4.6 推广计划提示

- **开发部门**：本章是 Java 开发者的必修课，特别是 settings.xml 的 mirror/server/distributionManagement 三元关系
- **CI/CD 团队**：重点关注 snapshot→release 的发布流水线和凭据管理
- **测试部门**：学会用 `mvn -U` 强制刷新 SNAPSHOT，以及通过 Nexus API 查看制品版本历史
