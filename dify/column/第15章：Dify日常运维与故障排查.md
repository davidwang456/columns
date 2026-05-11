# 第15章：Dify 日常运维与故障排查

## 1. 项目背景

凌晨 2 点，运维小陈被报警电话吵醒："AI 客服回复不了消息了！所有用户都在骂！"小陈打开 Dify 的控制台——「应用正常运行」——没有报错。但用户就是收不到回复。慌了手脚的小陈开始挨个容器重启：docker restart api, worker, web, nginx... 折腾了 20 分钟，故障自己消失了。第二天复盘，他发现日志里其实有明确的报错——"Redis connection timeout"——只是因为时间窗口太短，他没来得及看到。

Dify 的日常运维看似简单（`docker ps` 看状态，`docker logs` 看日志），但真正出了问题，排查效率取决于你对服务拓扑和日志体系的理解。Dify 是典型的微服务架构——api、worker、db、redis、nginx、sandbox、weaviate——任何一个服务出问题都可能导致"AI 回复不了"。但具体是谁的问题？api 挂了？worker 队列堵了？Redis 连接满了？还是 Knowledge Base 向量数据库挂了？这需要一套系统化的排查思路。

本章模拟了 5 种生产中最常见的故障场景：模型调用失败、知识库检索为空、Workflow 执行超时、配置语法错误导致启动失败、磁盘空间不足。针对每个故障，给出"现象 → 定位 → 根因 → 解决"的 SOP（标准操作流程），让你在凌晨 2 点被叫醒时，能 3 分钟内定位问题。

## 2. 项目设计——剧本式交锋对话

**小胖**：（顶着黑眼圈）"大师，昨天晚上 Dify 挂了 20 分钟，我到处重启容器，最后不知道怎么就自己好了。我现在想想还是后怕——如果下次再挂，我还是不知道该怎么办。"

**大师**："你的做法叫'暴力重启'——这是最危险的操作。重启能解决临时状态问题（如内存泄漏），但如果你不先看日志，就等于在销毁罪证。正确的排查流程是：**先看 Log（日志）→ 再查 Status（状态）→ 最后 Restart（重启）**。"

**技术映射**：排查优先级 = 日志 > 状态 > 重启，保留现场是关键。

**小白**："Dify 这么多服务，日志分散在各个容器里，怎么看？"

**大师**："三层日志体系：
1. **容器日志**：`docker logs <容器名> --tail 100`。最常用，直接看每个服务输出了什么。
2. **应用日志**：Dify 内部的日志（access_log、error_log），可以在 api 容器的 `/app/api/logs/` 或环境变量指定的路径下找到。
3. **数据库日志**：PostgreSQL/Redis 的自身日志，需要进入容器查看。

快速排查口诀：用户报障 → 先 `docker logs docker-nginx-1 --tail 20`（看 nginx 有没有转发请求）→ 再 `docker logs docker-api-1 --tail 50`（看 api 有没有处理请求）→ 如果 api 报'Redis connection timeout'，那就 `docker logs docker-redis-1 --tail 20`。"

**小胖**："那有没有一个页面能看到所有服务是不是健康的？"

**大师**："三个方法：
1. `docker ps`：快速看容器状态，`Up` 就是活着，`Restarting` 就是快挂了
2. `curl http://localhost/health`：API 的健康检查端点，返回 `{"status": "ok"}`
3. `docker stats`：看 CPU 和内存使用率，哪个容器飙到 90%+ 哪个就有问题"

## 3. 项目实战

### 分步实现

#### 故障 1：模型调用失败（GPT 返回 401/429/Timeout）

**现象**：用户在聊天窗口发消息后，AI 一直显示"思考中..."，30 秒后返回"抱歉，系统出现错误"。

**排查步骤**：

```bash
# Step 1: 看 API 容器日志，定位错误类型
docker logs docker-api-1 --tail 50 | Select-String "Error|exception|fail"

# 典型错误 1：API Key 过期
# ERROR - openai.error.AuthenticationError: Incorrect API key provided
# → 解决：在 Dify 控制台更新 OpenAI 的 API Key

# 典型错误 2：Rate Limit (429)
# ERROR - openai.error.RateLimitError: Rate limit reached for requests
# → 解决：等待冷却（60 秒自动恢复）或增加 API Key 数量

# 典型错误 3：超时
# ERROR - requests.exceptions.Timeout: Connection timed out
# → 解决：检查网络代理配置，或增加模型调用超时时间

# Step 2: 验证模型连通性
docker exec docker-api-1 curl -v https://api.openai.com/v1/models \
  -H "Authorization: Bearer sk-xxx"

# 预期：HTTP 200 + 模型列表 JSON
# 如果不是 200：网络有问题（检查 Docker 网络的 DNS/代理）

# Step 3: 查看模型负载均衡状态
docker exec docker-redis-1 redis-cli KEYS "model_lb*"
# 检查是否有 Key 处于冷却（cooldown）状态
```

**根因分析矩阵**：

| 错误码 | 根因 | 排查命令 | 修复方案 |
|-------|------|---------|---------|
| 401 | Key 无效/过期 | `docker logs docker-api-1 \| grep 401` | 更新 Key |
| 429 | RPM/TPM 超限 | `docker logs docker-api-1 \| grep 429` | 等待或增加 Key |
| 500 | Provider 内部错误 | `docker logs docker-api-1 \| grep 500` | 联系 Provider 支持 |
| Timeout | 网络不通/防火墙 | `docker exec api curl -v <API URL>` | 配置代理/白名单 |

#### 故障 2：知识库检索结果为空

**现象**：文档已上传，索引进度 100%，但用户在 Chat App 中提问时回答不包含任何知识库内容。

**排查步骤**：

```bash
# Step 1: 检查索引进度
docker logs docker-worker-1 --tail 100 | Select-String "indexing|embedding"
# 预期：看到 "Document indexing completed"
# 异常：看到 "Error embedding" 或一直没有完成日志

# Step 2: 检查 Embedding 模型
docker exec docker-api-1 curl -X POST http://api:5001/console/api/workspaces/current/models/check-model-config \
  -H "Content-Type: application/json" \
  -d '{"provider":"openai","model":"text-embedding-3-small","model_type":"text-embedding"}'
# 预期：{"result": "success"}
# 异常：{"result": "error", "message": "..."}

# Step 3: 检查向量数据库
docker logs docker-weaviate-1 --tail 20
# 检查 Weaviate 是否正常运行

# Step 4: 在控制台做召回测试
# 进入知识库 → 召回测试 → 输入关键词 → 查看 Score
# 如果 Score 全是 0：向量数据库里没有数据，索引可能失败了
# 如果 Score 很低（< 0.3）：Embedding 模型可能不适合当前语言
# 如果 Score 正常但 Chat 中不显示：检查 System Prompt 中是否写了 {{#context#}}
```

**根因分析**：

| 现象 | 可能根因 | 修复方案 |
|------|---------|---------|
| 索引进度一直为 0 | Celery Worker 未启动或 Redis 连接失败 | `docker restart docker-worker-1` |
| 索引完成但检索 Score 全为 0 | Embedding API 调用失败但没报错 | 检查 Embedding Provider 配置 |
| Score 正常但回答不包含 | System Prompt 中没有 `{{#context#}}` | 添加占位符 |
| 部分文档检索不到 | 分段策略不当（关键词被切断了） | 调大分段长度或增加重叠 |

#### 故障 3：Workflow 执行超时/卡住

**现象**：用户发起 Workflow 执行后，画布上某节点一直显示"运行中"，超过 5 分钟没有变化。

**排查步骤**：

```bash
# Step 1: 查看 Workflow 执行日志
# 在 Dify 控制台 → 应用 → 日志 → 点击某个 Workflow Run → 查看详情
# 找到卡住的节点，看它是什么类型（LLM? HTTP? Code?）

# Step 2: LLM 节点卡住
docker logs docker-api-1 --tail 50 | Select-String "timeout|ModelManager"

# Step 3: HTTP 节点卡住
# 检查目标 URL 是否可达
docker exec docker-api-1 curl -v --max-time 10 "http://target-api/health"

# Step 4: Code 节点卡住
docker logs docker-sandbox-1 --tail 30
# 检查沙箱是否有错误

# Step 5: 强制终止卡住的 Run
# 目前 Dify 不提供强制终止 API，只能等超时或重启 Worker
docker restart docker-worker-1  # 慎用！
```

**Workflow 超时配置建议**：

| 节点类型 | 建议超时 | 风险 |
|---------|---------|-----|
| LLM | 60s | 长文本生成可能超时 |
| HTTP Request | 30s | 外部 API 不稳定 |
| Code | 15s | 死循环风险 |
| 知识库检索 | 10s | 大向量库可能慢 |

#### 故障 4：配置错误导致启动失败

**现象**：`docker compose up -d` 后，API 容器一直 `Restarting`。

**排查步骤**：

```bash
# Step 1: 看日志
docker logs docker-api-1 --tail 30

# 典型错误 1：数据库连接失败
# sqlalchemy.exc.OperationalError: could not connect to server: Connection refused
# → 解决：检查 DB_HOST/DB_PORT/DB_PASSWORD 是否正确

# 典型错误 2：Redis 连接失败
# redis.exceptions.ConnectionError: Error 111 connecting to redis:6379
# → 解决：检查 REDIS_HOST/REDIS_PORT 是否正确

# 典型错误 3：SECRET_KEY 格式错误
# ValueError: Fernet key must be 32 url-safe base64-encoded bytes
# → 解决：重新生成 SECRET_KEY

# Step 2: 验证数据库连接
docker exec docker-db-1 psql -U postgres -d dify -c "SELECT 1"
# 预期：返回 1

# Step 3: 验证 .env 文件编码
# Windows 下用记事本保存的 .env 可能是 UTF-8 BOM 编码
# 解决：用 VS Code 重新保存为 UTF-8（无 BOM）
```

#### 故障 5：磁盘空间不足

**现象**：Dify 运行几天后越来越慢，最后 API 完全不响应。

**排查步骤**：

```bash
# Step 1: 检查磁盘使用率
docker system df
# TYPE            TOTAL     ACTIVE    SIZE      RECLAIMABLE
# Images          15        12        8.5GB     1.2GB (14%)
# Containers      12        12        500MB     0B (0%)
# Local Volumes   8         8         25GB      0B (0%)
# → Volumes 占 25GB！需要清理

# Step 2: 检查 Docker 占用的磁盘空间
docker system df -v | Select-String "VOLUME|Local"

# Step 3: 清理无用的 Docker 资源
docker system prune -a --volumes  # 删除未使用的镜像、容器、网络、卷（谨慎！）

# Step 4: 查看具体哪些文件占用空间
docker exec docker-api-1 du -sh /app/api/storage/*
docker exec docker-db-1 du -sh /var/lib/postgresql/data/*

# Step 5: 配置日志轮转（防止日志文件撑满磁盘）
# 在 docker-compose.yaml 中为每个服务添加：
# logging:
#   driver: "json-file"
#   options:
#     max-size: "100m"
#     max-file: "3"
```

### 运维检查清单

制作一份日常巡检清单，每天执行：

```bash
#!/bin/bash
# dify-health-check.sh

echo "=== Dify 健康巡检 $(date) ==="

# 1. 容器状态
echo "[1/6] 容器状态:"
docker ps --format "  {{.Names}}: {{.Status}}" | grep docker

# 2. API 健康检查
echo "[2/6] API 健康:"
curl -s -o /dev/null -w "  HTTP %{http_code}\n" http://localhost/health

# 3. 磁盘使用率
echo "[3/6] 磁盘:"
df -h /var/lib/docker | tail -1 | awk '{print "  使用: "$5}'

# 4. 最近错误日志
echo "[4/6] 最近错误 (api):"
docker logs docker-api-1 --tail 20 2>&1 | grep -i "error\|exception" | tail -3

# 5. Worker 状态
echo "[5/6] Worker 状态:"
docker logs docker-worker-1 --tail 5 2>&1 | grep -i "celery@"

# 6. 数据库连接
echo "[6/6] 数据库:"
docker exec docker-db-1 pg_isready -U postgres 2>/dev/null && echo "  数据库正常" || echo "  数据库异常!"

echo "=== 巡检完成 ==="
```

### 测试验证

```bash
# 测试 1：模拟 API 不可用
# 临时停止 API 容器，验证健康检查能否检测到
docker stop docker-api-1
curl -s -o /dev/null -w "%{http_code}" http://localhost/health
# 预期：502 或 000（无响应）
docker start docker-api-1

# 测试 2：模拟 Redis 故障
# 临时停止 Redis，观察 Worker 日志变化
docker stop docker-redis-1
docker logs docker-worker-1 --tail 10
# 预期看到：Error connecting to redis
docker start docker-redis-1

# 测试 3：检查日志轮转配置
docker inspect docker-api-1 | Select-String "max-size|max-file"
```

## 4. 项目总结

### 优点与缺点

| 维度 | 优点 | 缺点 |
|------|------|------|
| **部署监控** | Docker 原生支持健康检查，docker ps 即可概览 | 无内置监控面板（需自行搭建 Prometheus + Grafana） |
| **日志体系** | docker logs 对所有服务统一，API 日志清晰 | 日志分散在多个容器，跨容器关联困难 |
| **故障自愈** | Docker restart policy 支持自动重启 | 无内置熔断/降级机制，需到中级篇自建 |
| **备份恢复** | PostgreSQL 支持 pg_dump 标准备份 | 向量数据库的备份方案不够成熟 |

### 适用场景

| 场景 | 运维重点 |
|------|---------|
| **单机部署** | docker-compose 管理，定时巡检，磁盘空间监控 |
| **小团队使用** | 加 Grafana 监控 + 飞书/钉钉告警 |
| **生产环境** | K8s 部署 + HPA 自动扩缩容 + 多 AZ 容灾（见第 30 章） |
| **开发测试** | 日志级别调到 DEBUG，方便代码追踪 |

### 注意事项

1. **不要在生产环境随便 `docker restart`**：先看日志再决定，盲目重启可能让间歇性问题变成持续性故障
2. **备份数据库**：定期 `pg_dump`，保留至少 7 天的备份
3. **磁盘空间预留**：向量数据库和日志文件是磁盘消耗大户，建议预留 30% 以上的空闲空间

### 常见踩坑经验

1. **坑：`docker compose down -v` 误删所有数据** → 根因：`-v` 参数会删除 volumes 中的数据。解决：生产环境永远不要加 `-v`，养成 `docker compose down` 不带参数的习惯
2. **坑：Celery Worker 内存泄漏导致 OOM** → 根因：长时间不重启 Worker，累计的内存占用超过容器限制。解决：配置 Celery 的 `--max-tasks-per-child` 参数，处理 N 个任务后自动重启 Worker
3. **坑：PostgreSQL 连接数耗尽** → 根因：默认 `max_connections=100`，高并发时不够用。解决：增加 `max_connections` 或在应用层加连接池（pgbouncer）

### 思考题

1. **进阶题**：如果 CEO 要求 Dify 平台的可用性达到 99.9%（单月停机时间不超过 43 分钟），你应该在架构上做哪些改进？（提示：从单点故障、备份恢复、监控告警三个维度思考）

2. **进阶题**：Dify 的 Redis 如果突然宕机，系统哪些功能会受影响？哪些功能仍然可用？（提示：分析各组件对 Redis 的依赖程度）

> **参考答案**：见附录 D

---

> **推广计划提示**：本章是基础篇的最后一章，也是运维团队的必修课。建议所有角色都至少读一遍"故障排查 SOP"，了解常见问题的现象和处理方式。学完本章后，你已具备单机部署 Dify 的完整运维能力。接下来进入中级篇，开始深入源码和架构。
