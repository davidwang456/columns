本故事纯属虚构，如有雷同，纯属巧合。

> **版本说明**：镜像标签请显式写 **`redis:8.6`** 或当前稳定 8.x，勿裸用 `latest` 上生产却不登记。

---

## 卷〇潮式：Docker 一条命令，Redis 跟着容器漂

**大师**：Docker 是「**潮**」——环境可重复、版本可钉死。你给客户演示，**Compose** 里把端口、卷、健康检查写清，比截图一百张管用。

**小白**：一条命令走天下？

**大师**：演示可以；生产还要 **持久化卷、密码、资源限制、日志**。

---

## 最小 `docker run`

```bash
docker run -d --name redis86 -p 6379:6379 redis:8.6
docker exec -it redis86 redis-cli PING
```

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
```

**大师**：`redis.conf` 里记得 **bind、protected-mode、requirepass**；公网暴露 6379 等于**在码头摆银两**。

---

## 需要概率模块时

默认官方镜像是否包含全部模块能力，以**镜像说明**为准。若你要 **Bloom** 等 README 带 `*` 能力，往往要 **自建镜像**（`make BUILD_WITH_MODULES=yes`）或换官方提供的全量构建。详见 [卷〇-附录-模块与BUILD_WITH_MODULES对照表.md](卷〇-附录-模块与BUILD_WITH_MODULES对照表.md)。

---

## 收式

**小白**：弟子 healthcheck 绿了，心里也绿了。

**大师**：绿是好事。下一篇 [卷〇-03-新总诀式-Redis86能力地图.md](卷〇-03-新总诀式-Redis86能力地图.md)，把 **8.6** 新词一口气扫一遍。
