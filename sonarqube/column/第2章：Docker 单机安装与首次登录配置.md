# 第2章：Docker 单机安装与首次登录配置

## 1. 项目背景

**业务场景**：一家 50 人规模的创业公司正在经历快速扩张，研发团队从 8 人增长到 35 人。CTO 在一次线上事故复盘后发现：近三个月 70% 的线上 Bug 来自代码审查遗漏——部分开发者的 IDE 根本没有配置静态检查工具。CTO 决定引入 SonarQube 作为统一的代码质量平台，但运维团队只有 2 人，且他们没有专门的数据库管理员。

面对这个场景，团队需要在 1 天内完成 SonarQube 的部署和基本配置。传统的安装方式需要手动安装 Java、配置 PostgreSQL、设置 Elasticsearch 参数、解决一堆依赖问题——对于一个 2 人运维团队来说，光环境调试就可能花掉整个星期。

**痛点放大**：没有 Docker 方案时，手动安装 SonarQube 面临的典型问题：

- **依赖地狱**：SonarQube 依赖特定版本的 Java（如 JDK 17），但服务器上可能运行着 JDK 8 的其他应用，版本冲突难以协调。
- **数据库配置繁琐**：需要手动创建数据库、用户、授权、配置 JDBC 连接，任何一个参数错误都会导致启动失败。
- **环境不可复制**：测试环境搭好后，到了生产环境又要重来一遍。配置漂移导致"测试环境没问题，生产环境跑不起来"的噩梦。
- **升级风险高**：版本升级时如果漏改一个参数，可能导致数据丢失或服务不可用。

Docker Compose 方案将所有这些依赖打包成可复现的"配方"，一条命令即可在任何有 Docker 的机器上拉起完整环境。

```
传统安装流程：
安装 JDK → 安装 PostgreSQL → 创建库/用户 → 下载 SonarQube → 
配置 sonar.properties → 配置系统参数 → 启动脚本 → 等待启动

Docker Compose 流程：
编写 docker-compose.yml → docker compose up -d → 等待即可
```

## 2. 项目设计

### 剧本式交锋对话

---

**小胖**（对着满屏的安装文档抓头发）："大师救命！我照着官网文档装了一上午，还没见到 SonarQube 的登录页面。先是 JDK 版本不对，然后数据库连不上，现在又说 Elasticsearch 什么 mmap 不够……这也太劝退了吧！"

**大师**："小胖，你这是在用'传统手艺'装软件。现在时髦的做法是用 Docker——一锅端的。就像你煮火锅，不用自己去菜市场买毛肚、切鸭血、调锅底，直接买一包'火锅全家桶'下锅就行。"

**小胖**："Docker 我知道，不就是轻量虚拟机吗？可是 SonarQube 这个全家桶里到底有几样东西啊？"

**小白**（正在终端敲命令）："我看了官方镜像的 Dockerfile，里面包含了 SonarQube Web 服务、内嵌的 Elasticsearch、还有 Compute Engine。但是数据库呢？PostgreSQL 也在同一个镜像里吗？"

**大师**："小白观察得仔细。SonarQube 官方镜像只包含应用本身和 Elasticsearch，不包含数据库。因为数据库是有状态的服务，生产环境中通常需要独立部署、独立备份。所以我们用 Docker Compose 编排两个容器：一个 sonarqube 容器，一个 postgres 容器。它们通过 Docker 的内部网络通信，你不需要暴露数据库端口到宿主机。"

**小胖**："那 docker-compose.yml 怎么写？是不是从网上找一篇博客抄过来就行？"

**大师**（摇头）："不建议随便抄。很多博客的配置文件已经过时——SonarQube 版本迭代很快，环境变量名、挂载路径、推荐参数经常变化。最好的方式是以官方文档为基准，再根据自己的场景调整。"

**小白**："说到参数，我看到很多地方提到 `vm.max_map_count`。为什么 SonarQube 需要修改这个内核参数？这个参数是干什么的？"

**大师**："这个参数控制一个进程可以拥有的内存映射区域的最大数量。Elasticsearch 大量使用 mmap 来映射索引文件——不是把整个文件读入内存，而是把文件映射到虚拟地址空间，用多少读多少。默认值 65530 对于 ES 来说太低了，官方推荐至少 262144。"

**小胖**："mmap……内存映射……这和直接读文件有什么区别？"

**大师**："好比喻——你有一本《新华字典》（索引文件）。直接读文件的方式是：每次查字都去图书馆借字典，查完还回去。mmap 的方式是：图书馆给你一张'通行证'，你可以随时进入图书馆查阅需要的页，图书馆帮你管理页面换入换出。mmap 利用了操作系统的页面缓存机制，比应用层自己管理缓存高效得多。"

**技术映射**：mmap 将文件映射到进程虚拟地址空间，访问映射区域时触发缺页中断由 OS 自动加载页面。Elasticsearch 利用 mmap 存储 Lucene 索引文件，避免 JVM 堆内缓存和 GC 压力。

**小胖**："那 docker-compose.yml 里为什么要用 volumes 挂载目录？不用行不行？"

**大师**："volumes 是容器的"外挂硬盘"。不用的话，删掉容器时数据就没了——包括扫描历史、配置、插件全丢。用了 volumes，数据持久化在宿主机上，容器坏了换一个新的，挂上同样的 volumes 就能恢复。"

**小白**："我在 `sonar.properties` 里看到一堆配置项：`sonar.jdbc.url`、`sonar.search.javaOpts`、`sonar.web.javaOpts`。docker-compose 里用环境变量覆盖它们，这个映射关系是怎样的？"

**大师**："SonarQube 的 Docker 镜像支持通过环境变量注入配置，规则是把 `sonar.properties` 中的属性名转换为环境变量名：把点号换成下划线，全部大写。例如：

- `sonar.jdbc.url` → `SONAR_JDBC_URL`
- `sonar.jdbc.username` → `SONAR_JDBC_USERNAME`
- `sonar.search.javaOpts` → `SONAR_SEARCH_JAVAOPTS`

但不是所有属性都支持，官方文档中有支持的环境变量清单。"

**小胖**："好吧，那我先照着你给的 docker-compose.yml 跑起来。跑起来后第一步应该干什么？"

**大师**："首次登录后有四件事必须做：改密码、改密码、改密码——重要的事说三遍。默认 admin/admin 是个公开的秘密。第二步是检查健康页确认所有组件正常。第三步是根据需求安装插件——社区版默认不包含某些语言的分析器。第四步是生成扫描用的 Token。"

---

## 3. 项目实战

### 3.1 环境准备

**依赖要求**：
- Docker Engine 20.10+
- Docker Compose v2+（命令为 `docker compose`，非 `docker-compose`）
- 可用磁盘空间 ≥ 10GB
- 可用内存 ≥ 4GB（推荐 8GB）

### 3.2 分步实现

**步骤 1：创建项目目录与配置文件**

```bash
mkdir -p ~/sonarqube-lab/{data,extensions,logs,postgresql}
cd ~/sonarqube-lab
```

创建 `docker-compose.yml`：

```yaml
services:
  sonarqube:
    image: sonarqube:10.7-community
    container_name: sonarqube-web
    depends_on:
      postgres:
        condition: service_healthy
    ports:
      - "9000:9000"
    environment:
      SONAR_JDBC_URL: jdbc:postgresql://postgres:5432/sonar
      SONAR_JDBC_USERNAME: sonar
      SONAR_JDBC_PASSWORD: SonarQube@2024
      SONAR_SEARCH_JAVAOPTS: "-Xms512m -Xmx1024m"
      SONAR_WEB_JAVAOPTS: "-Xms256m -Xmx512m"
    volumes:
      - ./data:/opt/sonarqube/data
      - ./extensions:/opt/sonarqube/extensions
      - ./logs:/opt/sonarqube/logs
    ulimits:
      nofile:
        soft: 65536
        hard: 65536
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:9000/api/system/health"]
      interval: 30s
      timeout: 10s
      retries: 10
      start_period: 120s

  postgres:
    image: postgres:15-alpine
    container_name: sonarqube-db
    environment:
      POSTGRES_USER: sonar
      POSTGRES_PASSWORD: SonarQube@2024
      POSTGRES_DB: sonar
    volumes:
      - ./postgresql:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U sonar -d sonar"]
      interval: 10s
      timeout: 5s
      retries: 5
```

**要点说明**：
- `condition: service_healthy` 确保 PostgreSQL 完全就绪后才启动 SonarQube
- `sonar.search.javaOpts` 控制内嵌 ES 的 JVM 堆内存，测试环境设为 512m-1GB
- `healthcheck` 配置了自动健康检测，`docker compose ps` 可看到健康状态

**步骤 2：调整系统参数**

**Linux/macOS：**

```bash
# 临时生效
sudo sysctl -w vm.max_map_count=262144

# 永久生效（Linux）
echo "vm.max_map_count=262144" | sudo tee -a /etc/sysctl.conf

# 永久生效（macOS + Docker Desktop）
# Docker Desktop → Settings → Resources → Advanced → 内存 ≥ 4GB
```

**Windows（Docker Desktop）：**

```powershell
# 在 PowerShell 中执行
wsl -d docker-desktop sysctl -w vm.max_map_count=262144

# 持久化：在 %USERPROFILE%\.wslconfig 中添加
# [wsl2]
# kernelCommandLine = "sysctl.vm.max_map_count=262144"
```

**步骤 3：启动服务**

```bash
docker compose up -d
```

**验证启动过程：**

```bash
# 实时查看日志
docker compose logs -f sonarqube

# 观察关键日志行：
# "Elasticsearch is up"    → ES 启动成功
# "Database connection OK"  → 数据库连接成功
# "SonarQube is operational" → 全部就绪
```

启动通常需要 2-5 分钟，取决于机器性能。

**步骤 4：首次登录**

浏览器访问 `http://localhost:9000`。

首次登录凭据：
- 用户名：`admin`
- 密码：`admin`

系统强制要求修改密码。输入新密码（如 `Sonar@2024Admin`）。

> **踩坑提示**：如果页面一直显示"SonarQube is starting"，检查日志：
> ```bash
> docker compose logs sonarqube | grep -i error
> ```
> 最常见的原因是：数据库连接失败、ES 启动失败（mmap 不足）、端口被占用。

**步骤 5：配置安全基线**

登录后依次进行以下安全配置：

**(1) 禁用强制认证重新加载**

进入 **Administration → General → Security**：
- Force user authentication 设置为 `true`
- 启用 "Force user to change password on first login"

**(2) 检查健康状态**

进入 **Administration → System → System Info**，确认以下组件均为绿色：

| 组件 | 说明 | 正常状态 |
|------|------|---------|
| Compute Engine | 后台计算引擎 | ✅ Up |
| Database | PostgreSQL 连接 | ✅ Up |
| Search | Elasticsearch | ✅ Up |
| Web | Web 服务 | ✅ Up |

也可以通过 API 验证：

```bash
curl -u admin:Sonar@2024Admin "http://localhost:9000/api/system/health"
```

预期输出：
```json
{"health": "GREEN", "causes": [], "nodes": [...]}
```

**(3) 生成扫描 Token**

进入 **My Account → Security**，在 "Generate Tokens" 区域输入 Token 名称（如 `local-scanner`），选择类型 "Project Analysis Token"（或 "Global Analysis Token"），点击 "Generate"。

**复制 Token 值并安全保存！** Token 格式类似：`squ_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx`

> **⚠ 安全警告**：Token 一旦关闭弹窗就再也看不到了。如果丢失，删除旧 Token 重新生成即可。

**(4) 安装语言插件**

进入 **Administration → Marketplace**：

- 搜索 "SonarJava"（Java 分析器，社区版自带）
- 搜索 "SonarJS"（JavaScript/TypeScript 分析器）
- 如需要其他语言：Python、Go、C# 等，搜索对应插件并安装

安装后需要重启 SonarQube：

```bash
docker compose restart sonarqube
```

### 3.3 验证部署

运行完整的健康检查脚本：

```bash
#!/bin/bash
echo "=== SonarQube 部署验证 ==="
echo ""

echo "1. 容器状态："
docker compose ps

echo ""
echo "2. 系统健康："
curl -s -u admin:Sonar@2024Admin "http://localhost:9000/api/system/health" | python3 -m json.tool

echo ""
echo "3. 插件列表（已安装）："
curl -s -u admin:Sonar@2024Admin "http://localhost:9000/api/plugins/installed" | python3 -m json.tool | grep '"name"'

echo ""
echo "4. 数据库连接状态："
curl -s -u admin:Sonar@2024Admin "http://localhost:9000/api/system/db_migration_status" | python3 -m json.tool
```

### 3.4 完整文件清单

本章所有配置文件已在文中给出。代码仓库地址（示例）：`https://github.com/example/sonarqube-lab`

目录结构：
```
sonarqube-lab/
├── docker-compose.yml
├── data/           # SonarQube 数据（自动生成）
├── extensions/     # 插件目录
├── logs/           # 日志目录
└── postgresql/     # PostgreSQL 数据
```

---

## 4. 项目总结

### 4.1 优点与缺点

| 维度 | Docker Compose 部署 | 传统手动部署 |
|------|-------------------|-------------|
| 部署速度 | 3 分钟（首次拉取镜像除外） | 30 分钟至 2 小时 |
| 环境一致性 | 配置文件即环境，100% 一致 | 依赖人工记忆，易漂移 |
| 升级难度 | 改镜像 tag + 重启 | 停止服务 → 解压新包 → 手动迁移配置 |
| 资源隔离 | 容器级隔离 | 无隔离，与宿主机共享 |
| 备份难度 | 备份 volumes 目录即可 | 需分别备份数据库和文件系统 |
| 性能损耗 | 极小（< 3%） | 无 |
| 调试难度 | 需要理解 Docker 网络和存储 | 直接查看本地进程和文件 |

### 4.2 适用场景

- **开发和测试环境**：Docker Compose 一拉即用，秒级销毁重建
- **小团队生产环境**（< 50 项目）：单机 Docker 足够应对
- **CI/CD 流水线中的临时实例**：用于集成测试
- **学习和演示**：零经验也能按文档跑起来

**不适用场景**：
- 大规模生产环境（> 500 项目，应考虑 Data Center 版或 K8s 部署）
- 对 IO 性能极度敏感的场景（裸机部署可获得更好的磁盘性能）

### 4.3 注意事项

1. **内存分配**：SonarQube 至少需要 2GB 内存分配给 ES，加上 Web 和 CE，总共至少 4GB。内存不足会导致启动失败或扫描超时。
2. **数据备份**：`data/` 目录和 `postgresql/` 目录必须一起备份，缺一不可。
3. **版本升级**：不能跳版本升级。例如从 9.9 → 10.7 需要先升级到 10.0（或遵循官方升级路径）。
4. **外部数据库**：测试环境用 Docker 内的 PostgreSQL 没问题，但生产环境建议使用外部高可用 PostgreSQL。

### 4.4 常见踩坑经验

**故障 1：容器反复重启，日志显示 `Elasticsearch exited with code 78`**

根因：`vm.max_map_count` 未设置或设置未生效。Docker Desktop 重启后该参数会丢失，需要设置持久化方案。

**故障 2：SonarQube 启动成功但无法登录，一直提示"未授权"**

根因：PostgreSQL 容器重启导致数据目录权限变更。解决：检查 `postgresql/` 目录权限，所有者应为主机的 `postgres` 用户对应 UID（通常是 999）。

**故障 3：扫描时 Scanner 报 `Connection refused`**

根因：Docker 端口映射问题。确认 `docker compose ps` 中 `9000:9000` 端口正常映射，且防火墙未拦截。

### 4.5 思考题

1. 如果需要在同一台宿主机上运行多个 SonarQube 实例（例如隔离不同团队），Docker Compose 方案需要做哪些调整？
2. `sonar.search.javaOpts` 和 `sonar.web.javaOpts` 分别控制什么组件的内存？如果测试环境只有 4GB 内存，你如何分配这三个组件的内存？

> **答案提示**：第1题考虑端口映射、volume 名称、容器名冲突。第2题见第26章性能调优部分。

---

> **推广计划提示**：运维团队应首先掌握本章的 Docker Compose 部署方式。建议将此 docker-compose.yml 提交到团队的配置仓库，作为"一键启动开发环境"的标准入口。开发环境、测试环境、生产环境使用不同的 compose 文件（或 .env 覆盖），保持部署方式统一。
