# 第 02 章 · 潮式：Docker 与 Compose

本故事纯属虚构，如有雷同，纯属巧合。

> **给新读者**：若你还没用过 Docker，可把它理解成「把应用和依赖打成一个**可搬运的标准包**」，本机或服务器上同一条命令就能起相同环境。与 [01 章](01-前传-Win11与WSL2尝鲜Redis86.md) 的 WSL 不冲突，很多人两者一起用。

> **版本说明**：镜像标签请显式写 **`redis:8.6`**（或小版本钉死如 `redis:8.6.0`），勿裸用 `latest` 上生产却不登记——**潮归潮，账要平**。

## 本话目标

- 用 **一条命令**拉起 Redis 容器，并解释 **端口映射**风险。  
- 写出一份**可给同事复现**的 `docker-compose.yml`（含数据卷与健康检查）。

## 项目背景

**贯穿设定**：仍用**某电商平台**作为假想业务；本章属于**环境与交付物**，尚未涉及具体键与数据类型。

**与本章关系**：后端仓库常用 `docker-compose.yml`：新人 `git pull && docker compose up` 即得 **Redis 8.6**（镜像标签钉死小版本）、数据进命名卷，删容器不丢开发数据；CI 同样 Compose 起依赖，减少环境漂移。你要掌握 **端口映射、卷、健康检查**——与「业务里用哪种 Redis 结构」是**先后顺序**：先让全员连上**同一版本、可复现**的实例，再写业务。

## 步步引导：先跑起来，再谈优雅

**大师**：若让你**一条命令**把 Redis 拉起来，你最先想到的会是什么？

**小白**：`docker pull redis` 之类……然后 `run` 一下？

**大师**：方向没错。你再往下想半步：**`redis` 这个名后面，要不要跟一个「带小版本号的标签」**？为什么有人宁愿写 `8.6.0`，有人却写 `latest`？

**小白**：……写具体版本，是不是以后好对账？

**大师**：正是。`latest` 像「总是最新鲜的河鱼」——三个月后再来，**未必还是同一条**。团队与 CI 通常要**可复现**，标签宜**写清**。

**小白**：教程里 `-p 6379:6379`，弟子照抄了。

**大师**：在笔记本上，这常常是捷径。你再替**公网机器**换位思考一下：端口映射出去之后，**谁会看见这个门**？

**小白**：……谁都能扫到？那得加密码、防火墙？

**大师**：心领神会。细节在 [26-安全-ACL与TLS.md](26-安全-ACL与TLS.md)；此处只记**一层意识**：**映射＝开门**，门内要有**规矩**。

**小白**：容器删了，数据还会在吗？

**大师**：你先回忆：数据写在**容器肚子里的可写层**，还是写在**宿主机挂进来的目录**？前者随容器走，后者常能留下。

**小白**：所以要 volume？

**大师**：生产几乎总是**要**。开发机也建议早习惯，免得「删容器＝删库」成为肌肉记忆。

**小白**：那 Compose 又多在哪一步？

**大师**：把它想成「**把 run 的长参数，变成一份可提交的文本**」。同事 `git pull && docker compose up`，屏幕更容易对齐；再加 **healthcheck**，流水线里**等真绿**再测，心更定。

## 小剧场：潮退见贝壳

你演示完说「我环境没问题」，同事一拉 `latest` 起不来——大师叹：**「潮水涨时人人会冲浪；潮退了，谁挂了 volume、谁钉了 digest，沙滩上看得一清二楚。」**

---

## Docker 一条命令，Redis 跟着容器漂

**大师**：Docker 是「**潮**」——环境可重复、版本可钉死、CI 与笔记本同款。你给客户演示，**Compose** 里把端口、卷、健康检查写清，比截图一百张管用；运维问「你当时到底跑的啥」，你甩出 `compose.yaml` 和镜像 digest，这叫**专业**。

**小白**：一条命令走天下？

**大师**：演示可以；生产还要 **持久化卷、密码、资源限制、日志、备份**。否则容器一删，数据跟着「潮退」，只剩沙滩上的贝壳——还是碎的。

**小白**：和上一章 WSL 啥关系？

**大师**：WSL 是**练功房**；Docker 是**可搬运的练功房**。很多团队本机 WSL2 + Docker Desktop，**镜像里跑 8.6**，宿主机改业务代码，**各干各的，互不耽误**。

---

## 最小 `docker run`（先爽一把）

```bash
docker run -d --name redis86 -p 6379:6379 redis:8.6
docker exec -it redis86 redis-cli PING
docker exec -it redis86 redis-cli INFO server | head -n 5
```

**大师**：`-p 6379:6379` 等于在自家大门上贴「银库在此」。**开发机**可以；**公网 VPS** 请配合密码、TLS、安全组，详见 [26-安全-ACL与TLS.md](26-安全-ACL与TLS.md)。

---

## `docker-compose.yml` 示例（开发向）

```yaml
services:
  redis:
    image: redis:8.6
    ports:
      - "6379:6379"
    volumes:
      - ./redis-data:/data
      - ./redis.conf:/usr/local/etc/redis/redis.conf
    command: ["redis-server", "/usr/local/etc/redis/redis.conf"]
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 3s
      retries: 3
    # 开发机可酌情加内存上限，避免 OOM 拖死整机
    # deploy:
    #   resources:
    #     limits:
    #       memory: 512m
```

**配套 `redis.conf` 片段（示例意图）**：

```text
bind 0.0.0.0
protected-mode yes
requirepass dev-only-change-me
appendonly yes
```

**大师**：`bind 0.0.0.0` 在容器里常见，因为要通过 bridge 访问；**宿主机防火墙**与 **requirepass** 才是外敌。公网暴露 6379 且无认证，等于**在码头摆银两**——安全面试里必考。

---

## 进阶几招（真项目会用）

| 场景 | 做法 |
|------|------|
| 数据要留 | 命名 volume 或绑定宿主机目录，别只靠容器可写层 |
| 主从/哨兵/集群 | 多 service、固定 hostname，读官方 Compose 范例与 [27-集群与高可用.md](27-集群与高可用.md) |
| 自定义模块镜像 | 多阶段构建：`make BUILD_WITH_MODULES=yes`，见 [04-编译秘笈-BUILD与模块对照表.md](04-编译秘笈-BUILD与模块对照表.md) |
| 排障 | `docker logs redis`、`redis-cli LATENCY DOCTOR` |

---

## 需要概率模块时

默认官方镜像是否包含全部模块能力，以**镜像说明**为准。若你要 **Bloom** 等 README 带 `*` 能力，往往要 **自建镜像**（`make BUILD_WITH_MODULES=yes`）或换官方提供的全量构建。详见 [04-编译秘笈-BUILD与模块对照表.md](04-编译秘笈-BUILD与模块对照表.md) 与 [12-概率模块-Bloom与伙伴.md](12-概率模块-Bloom与伙伴.md)。

**小白**：弟子 `BF.ADD` 在容器里报错……

**大师**：进容器 `MODULE LIST`。空列表不是世界错了，是**镜像故事没写那一页**。

---

## 动手试一试

1. 执行文首 `docker run` 三行，确认 `PING` 与 `INFO server` 正常。  
2. 自建目录，放入文中 `docker-compose.yml` 与最小 `redis.conf`，执行 `docker compose up -d`，再 `docker compose ps` 看 **health**。  
3. `docker compose logs -f redis` 开着一个窗口，另一个窗口 `redis-cli` 压几条命令，**对照日志**里是否出现告警（如禁用命令、大请求）。

## 实战锦囊

- 把 **镜像 digest** 或 **小版本号**写进团队 Wiki，排查时先对齐「是不是同一颗」。  
- 需要 **Bloom** 等模块而官方镜像没有 → 不要硬抄文档命令，回到 [04-编译秘笈-BUILD与模块对照表.md](04-编译秘笈-BUILD与模块对照表.md) 做镜像策略。  
- 与 [01 章](01-前传-Win11与WSL2尝鲜Redis86.md) 组合：**本机改源码用 WSL，交付演示用 Compose**。

---

## 本章小结（自查）

- [ ] 能解释为何钉死镜像标签  
- [ ] Compose 含 healthcheck 与数据卷  
- [ ] 知道默认镜像与「全模块自建」的分界线  

---

## 收式

**小白**：弟子 healthcheck 绿了，心里也绿了。

**大师**：绿是好事。下一章：[03-新总诀式-Redis86能力地图.md](03-新总诀式-Redis86能力地图.md)。
