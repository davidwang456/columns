# 第2章：Windows、WSL2 与 Docker 环境搭建

## 1. 项目背景

Redis 学习最怕第一步就卡在环境上。团队成员使用的电脑不一致：有人是 Windows 11，有人习惯 WSL2，有人只会 Docker Desktop，还有同学直接下载 Windows 版旧 Redis。环境一乱，后面的命令演示、持久化路径、端口映射、配置文件挂载都会变成“我这里可以，你那里不行”。

本专栏强调实战为主，所以需要一个统一、可重复、可清理的实验环境。对 Windows 用户来说，推荐优先使用 Docker Desktop 或 WSL2 中的 Docker。这样做的好处是：Redis 版本可控，配置文件可挂载，数据目录可保留，后续搭建主从、Sentinel、Cluster、Prometheus 也能沿用同一套方式。

本章会完成一个最小但接近真实项目的 Redis 实验环境：Redis 服务启用密码、AOF 持久化、数据目录挂载，同时启动 RedisInsight 作为可视化工具。后续章节的验证码、购物车、排行榜、缓存、Stream、Cluster 都会基于这类环境继续扩展。

## 2. 项目设计

小胖说：“我以前直接双击一个 redis-server.exe 就能用，为什么还要折腾 Docker？”

小白追问：“如果每个人 Redis 版本不同，AOF 配置不同，数据目录位置不同，后面出现问题怎么复现？而且 Redis 8.x 的一些能力，旧版本根本没有。”

大师解释：“学习环境要满足三个要求：版本一致、配置透明、可重复创建。Docker 就像给 Redis 装进一个标准保温箱，无论你在 Windows 还是 Linux，只要镜像和配置一样，实验结果就更容易一致。”

技术映射：Docker 镜像固定 Redis 版本，Compose 固定启动参数、端口、数据卷和依赖服务。

小胖又问：“那 WSL2、Docker Desktop、Windows 原生到底选哪个？”

大师回答：“如果你只是跟着专栏练习，Docker Desktop 最省心；如果你已经习惯 Linux 命令，WSL2 更接近生产；Windows 原生 Redis 不建议作为主环境，因为版本、模块和生产一致性都容易出问题。”

技术映射：开发环境不追求花哨，追求与生产行为尽量一致。Redis 在 Linux 上运行最常见，容器化能降低 Windows 差异。

小白继续问：“密码、AOF、数据挂载这些一开始就配，会不会增加学习成本？”

大师说：“恰恰相反。越早接触这些基础配置，越不会把 Redis 当成临时玩具。密码对应安全边界，AOF 对应数据恢复，挂载对应容器重建后数据保留。这些都是后续生产化章节的基础。”

## 3. 项目实战

### 3.1 准备目录

建议在项目下准备如下目录结构：

```text
redis-lab/
  docker-compose.yml
  redis.conf
  data/
```

`data` 用来保存 Redis 持久化文件，`redis.conf` 用来显式管理配置。

### 3.2 编写 redis.conf

最小配置如下：

```conf
bind 0.0.0.0
port 6379
protected-mode yes
requirepass redis123

appendonly yes
appendfsync everysec

dir /data
loglevel notice
```

这里先不追求完整生产配置，只保留学习最必要的项目：监听地址、端口、密码、AOF 和数据目录。

### 3.3 编写 Docker Compose

```yaml
services:
  redis:
    image: redis:8.6
    container_name: redis-lab
    ports:
      - "6379:6379"
    volumes:
      - ./redis.conf:/usr/local/etc/redis/redis.conf
      - ./data:/data
    command: ["redis-server", "/usr/local/etc/redis/redis.conf"]

  redisinsight:
    image: redis/redisinsight:latest
    container_name: redisinsight-lab
    ports:
      - "5540:5540"
```

启动服务：

```bash
docker compose up -d
```

查看状态：

```bash
docker compose ps
```

### 3.4 连接 Redis

使用密码连接：

```bash
docker exec -it redis-lab redis-cli -a redis123
```

执行验证命令：

```bash
PING
SET lab:env docker
GET lab:env
CONFIG GET appendonly
```

预期结果：
- `PING` 返回 `PONG`。
- `GET lab:env` 返回 `docker`。
- `CONFIG GET appendonly` 显示 AOF 已开启。

### 3.5 使用 RedisInsight

浏览器访问：

```text
http://localhost:5540
```

连接参数：
- Host：`host.docker.internal` 或本机 IP，部分环境可使用 `localhost`。
- Port：`6379`。
- Username：留空。
- Password：`redis123`。

连接成功后，可以在界面里查看 key、执行命令、观察内存和数据库信息。

### 3.6 常见坑与解决

端口占用：如果 6379 已被占用，可以把 Compose 端口改成 `"6380:6379"`，客户端连接本机 6380。

认证失败：出现 `NOAUTH Authentication required`，说明客户端没有带密码；出现 `WRONGPASS`，说明密码不匹配。

挂载失败：Windows 路径包含中文或权限异常时，Docker 可能无法正常挂载。建议实验目录放在普通英文路径下。

AOF 文件找不到：Redis 容器内路径是 `/data`，宿主机对应 `./data`，不要在 Windows 上直接猜容器内部路径。

RedisInsight 连不上：优先检查 Redis 容器是否运行、端口是否映射、密码是否正确，以及防火墙是否拦截。

## 4. 项目总结

本章完成了后续所有实战的基础设施：一个可重复启动、可保留数据、可视化查看的 Redis 实验环境。

三种环境选择对比：

| 方式 | 优点 | 缺点 | 建议 |
|------|------|------|------|
| Docker Desktop | 版本统一，清理方便，适合 Compose 编排 | 首次安装稍重 | 专栏首选 |
| WSL2 | 接近 Linux 生产环境，命令体验好 | 需要理解 Linux 文件系统 | 进阶推荐 |
| Windows 原生 | 上手直观 | 版本和生产差异大 | 不建议作为主环境 |

适用场景：本地学习、团队培训、功能验证、故障演练和小型 Demo。不适合直接把本章配置用于生产，因为生产还需要 ACL、TLS、监控、备份、资源限制和高可用。

思考题：
1. 为什么学习环境也建议开启密码和 AOF？
2. 如果容器删除后数据仍要保留，哪些目录必须挂载？

推广建议：开发团队统一使用同一份 Compose；测试团队把环境启动写入自动化脚本；运维团队后续可基于这套结构扩展 Sentinel、Cluster 和监控组件。
