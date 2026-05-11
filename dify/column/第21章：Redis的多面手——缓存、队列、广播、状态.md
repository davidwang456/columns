# 第21章：Redis 的多面手——缓存、队列、广播、状态

## 1. 项目背景

"Redis 不就是个缓存吗？我们 Dify 的 Redis 容器才用了 30MB 内存，干脆去掉省点资源。"如果一个新人运维这么想并执行了，他会发现：Dify 立刻变成了"半身不遂"——所有 Celery 异步任务（文档索引、Workflow 后台执行、邮件发送）全面停摆；模型负载均衡的冷却机制失效（某个 Key 被限流后无法通知其他 Pod 切换）；多 Pod 部署时 Workflow 执行进度完全无法实时推送；同一个文档可能被两个 Worker 重复索引。

Redis 在 Dify 中不是"一个工具"，而是"一把瑞士军刀"——同时扮演五个截然不同、缺一不可的角色。这五个角色的分工是理解 Dify 分布式运行机制的钥匙。本章将逐一解剖每种角色的数据结构和业务场景，帮你建立"看 Redis Key 就能猜到业务行为"的诊断能力。

**场景一：模型突然不可用**。用户在 Chat App 中发消息，返回"抱歉，服务暂时不可用"。你打开 Redis 发现 `model_lb_cooldown:*` Key 有 3 个——说明所有 OpenAI Key 都被限流冷却了，没有可用凭据。这是冷却机制的设计局限：只有简单 TTL 冷却，没有"永远保留一个备用 Key"的策略。

**场景二：知识库索引任务堆积**。`docker logs worker` 没有任何错误，但新上传的文档就是不被处理。你检查 Redis 发现 `tenant_self_indexing_task_queue:tenant_a` 的队列长度高达 500——原来是租户 A 上传了大量文档，后面的租户 B 也在同一个队列里排队。

**场景三：多 Pod 部署后状态不一致**。K8s 部署了 3 个 API Pod，Pod A 处理了 Workflow 的某个节点，但 Pod B 上的前端 Socket.IO 连接收不到节点完成通知。根因：广播通道用的是 Redis Pub/Sub，而 Pub/Sub 不会持久化消息——订阅者必须在消息发送时在线。

## 2. 项目设计——剧本式交锋对话

**小胖**：（看着 `docker stats` 的输出，指着 Redis 那行）"大师，你看 Redis 容器 CPU 使用率 0.5%，内存才 30MB，几乎不消耗资源。Dify 到底用它来干啥？感觉去掉也没事吧？"

**大师**："千万别去！Redis 在 Dify 里是'隐性核心'——不用时你看不到它，一旦去掉，Dify 立刻到处报错。我给你画一张表：Redis 挂了之后，Celery Worker 收不到任务（文档不会索引、Workflow 异步执行失败）、模型负载均衡找不到冷却标记（请求可能全打到已被限流的 Key 上）、前端看不到 Workflow 实时进度。"

**技术映射**：Redis = Dify 的"实时中枢神经系统"，连接 API、Worker、前端三者的事件和数据。

**小胖**："那先讲最基础的——缓存。Dify 用 Redis 缓存了什么？不是已经有了 PostgreSQL 吗？"

**大师**："**Provider 凭据缓存**是最典型的场景。每个 API 请求都需要知道'当前租户配置的 OpenAI Key 是什么'。如果每次都查 PostgreSQL——SQL 解析、网络往返、ORM 序列化——大约 50ms。Redis 呢？一个 `GET provider_credentials:tenant_abc:openai`，0.3ms，快了 160 倍。而且 Dify 对凭据做了加密存储——存进 Redis 的不是明文 Key，是加密后的密文。即使 Redis 被攻破，Key 也不会泄露。"

**技术映射**：Redis 缓存 vs DB 查询 = 0.3ms vs 50ms = 160倍提速，且支持加密存储。

**小白**：（在白板上画了一个 Producer-Consumer 图）"消息队列——就是 Celery 把任务 LPUSH 到 Redis List，Worker BLPOP 取出来执行？但为什么不让 API 直接把任务给 Worker？还要经过 Redis？"

**大师**："因为 API 和 Worker 是解耦的——它们不在同一个进程里，甚至不在同一台机器上（K8s 部署时）。Redis 作为中间人（Broker）解耦了生产者和消费者。API 只管'扔任务到队列'然后立刻返回，Worker 什么时候取、怎么取、取完结果存哪里——API 完全不关心。这就是消息队列的核心价值：异步解耦。如果 API 同步等 Worker 执行完——一个 PDF 索引 30 分钟——用户的浏览器早就超时了。"

**技术映射**：消息队列 = 生产者-消费者解耦 + 削峰填谷，任务先落 Redis 队列再被 Worker 消费。

**小胖**："广播通道又是什么？和消息队列有啥区别？"

**大师**："消息队列是**点对点**——一个任务被一个 Worker 取走后就没了。广播是**一对多**——一个消息发给所有订阅者。Dify 的典型场景：Worker A 在后台执行 Workflow，每完成一个节点都要通知前端的 WebSocket 连接'节点完成啦'。但前端连的是 API Pod B 的 Socket.IO，不是 Worker A。怎么办？Worker A 把'节点完成'消息 PUBLISH 到 Redis 频道，所有订阅了这个频道的 API Pod 都收到消息，然后通过各自的 Socket.IO 推给前端。"

**技术映射**：Pub/Sub（发布订阅）= 一对多消息广播，适合实时通知而非任务分发。

**小白**："还有一个我不太明白——模型冷却标记为什么要存 Redis？存在 Python 变量里不行吗？"

**大师**："Python 变量是进程级的——Pod A 的变量 Pod B 看不到。而 Dify 在生产环境通常有 3+ 个 API Pod。Pod A 发现 OpenAI Key 1 被限流了（返回 429），往 Redis 设 `model_lb_cooldown:...key1`（TTL=60s）。Pod B 和 Pod C 在选 Key 时检查 Redis——发现 Key 1 在冷却中，自动跳过用 Key 2。如果存在 Python 变量里，Pod B 根本不知道 Key 1 被限流了，继续调用，再吃一个 429，用户就连续看到两次错误。"

**技术映射**：跨进程共享状态必须用外部存储（Redis/etcd/Zookeeper），不能用进程内变量。

## 3. 项目实战

### 环境准备

| 条件 | 说明 |
|------|------|
| Dify 已部署 | `docker ps` 确认 redis 容器 running |
| redis-cli 可用 | `docker exec -it docker-redis-1 redis-cli` |

### 分步实现

#### 步骤1：扫描 Redis 中的所有 Key 类型（目标：建立 Key 分类地图）

```bash
# 进入 Redis
docker exec -it docker-redis-1 redis-cli

# 安全扫描（用 SCAN 而非 KEYS，避免阻塞）
SCAN 0 MATCH * COUNT 50

# 你会看到以下几类 Key：
# 1. celery-task-meta-*              → Celery 任务执行结果（自动过期）
# 2. model_lb_index:*                → 模型轮询索引（当前轮到第几个 Key）
# 3. model_lb_cooldown:*             → 模型冷却标记（带 TTL 的临时 Key）
# 4. tenant_self_*_task_queue:*      → 租户隔离的 RAG 索引队列
# 5. provider_credentials:*          → Provider 凭据加密缓存
# 6. broadcast_channel:*             → 发布订阅频道（实时消息）

# 统计各类 Key 的数量
EVAL "local keys = redis.call('keys', 'model_lb_*'); return #keys" 0
EVAL "local keys = redis.call('keys', 'tenant_self_*'); return #keys" 0
EVAL "local keys = redis.call('keys', 'provider_credentials:*'); return #keys" 0
```

#### 步骤2：实时监控模型冷却机制（目标：观察冷却标记的完整生命周期）

这是最直观感受 Redis 价值的实验：

```bash
# 终端 1：实时监控冷却标记
watch -n 1 'docker exec docker-redis-1 redis-cli KEYS "model_lb_cooldown:*" 2>/dev/null'

# 终端 2：快速连续发送 15 条请求（触发 429 Rate Limit）
for i in $(seq 1 15); do
  curl -s -o /dev/null -X POST http://localhost/v1/chat-messages \
    -H "Authorization: Bearer app-xxx" \
    -d "{\"query\":\"test $i\",\"user\":\"loadtest\",\"response_mode\":\"blocking\"}" &
done
wait

# 观察终端 1：
# 15:00:00 → 出现 model_lb_cooldown:tenant_xxx:openai:llm:gpt-3.5-turbo:config_yyy
# 15:00:30 → TTL 剩余约 30 秒（用 TTL 命令查看）
# 15:01:00 → Key 自动删除（TTL 到期，冷却结束，该 Key 恢复可用）

# 终端 3：查看冷却 Key 的 TTL
docker exec docker-redis-1 redis-cli TTL "model_lb_cooldown:tenant_xxx:openai:llm:gpt-3.5-turbo:config_yyy"
# 输出：58 → 等待几秒 → 52 → TTL 递减中
```

**关键观察**：冷却标记的出现和消失完全由 Redis 的 `SETEX`（SET + EXPIRE）控制。60 秒一到，Key 自动删除，无需任何程序手动清理。这就是用 Redis 做临时状态存储的优雅之处——不需要额外的清理逻辑。

#### 步骤3：分析 Celery 队列——诊断任务积压问题

```bash
# 查看主要队列长度
docker exec docker-redis-1 redis-cli LLEN celery
# 正常值：0 或个位数（Worker 及时消费）
# 异常值：> 50（任务积压，Worker 可能挂了或处理太慢）

# 查看租户隔离队列
docker exec docker-redis-1 redis-cli KEYS "tenant_self_*" | while read key; do
  len=$(docker exec docker-redis-1 redis-cli LLEN "$key")
  echo "$key: $len 个待处理任务"
done

# 如果发现某队列严重积压：
# 1. 检查 Worker 是否 alive
docker logs docker-worker-1 --tail 10 | Select-String "celery@.*ready"
# 2. 查看 Worker 当前执行的任务
docker exec docker-api-1 celery -A app.celery inspect active
# 3. 考虑增加 Worker 数量或调整并发度
```

#### 步骤4：验证 Pub/Sub 广播机制（目标：理解跨服务实时通信）

```bash
# 终端 1：订阅所有 workflow 事件
docker exec -it docker-redis-1 redis-cli
PSUBSCRIBE "workflow_events:*"
# 保持终端 1 开着，等待消息

# 终端 2：在 Dify 控制台执行一个简单 Workflow
# 执行过程中，观察终端 1 的输出：
# 1) "pmessage"
# 2) "workflow_events:*"
# 3) "workflow_events:run_abc123"
# 4) "{\"event\": \"node_started\", \"node_id\": \"123\"}"
# ... （更多事件消息）

# 关键发现：终端 1 能实时收到消息 —— 这就是前端能展示"实时进度条"的原理
```

### 测试验证

```bash
# 综合测试：模拟 Redis 故障
# 1. 记录正常状态下的功能
curl -s http://localhost/v1/chat-messages -H "Authorization: Bearer app-xxx" \
  -d '{"query":"hi","user":"test","response_mode":"blocking"}' | Select-String "answer"

# 2. 停掉 Redis
docker stop docker-redis-1

# 3. 测试哪些功能仍可用，哪些不可用
# ✅ Chat API 仍可用（LLM 调用不经过 Redis）
curl -s http://localhost/v1/chat-messages -H "Authorization: Bearer app-xxx" \
  -d '{"query":"hi","user":"test","response_mode":"blocking"}'
# ❌ 知识库索引进度停止
# ❌ 模型冷却机制失效（多次 429 后报错）

# 4. 恢复 Redis
docker start docker-redis-1
# 等待 10 秒让各服务重连，所有功能恢复
```

## 4. 项目总结

### 五角色总览

| 角色 | Redis 数据结构 | Redis 命令 | 典型 TTL | 故障影响 |
|------|--------------|-----------|---------|---------|
| **缓存** | String (Key-Value) | GET/SET/EXPIRE | 5min | 查询延迟暴增（退回到 PostgreSQL） |
| **消息队列** | List | LPUSH/BRPOP | 任务消费后删除 | 异步任务全部暂停 |
| **发布订阅** | Pub/Sub Channel | PUBLISH/SUBSCRIBE | 实时，无持久化 | 进度通知中断 |
| **共享状态** | String + TTL | SETEX/GET/DEL | 60s | 冷却失效，限流雪崩 |
| **分布式锁** | String (SETNX) | SETNX/DEL | 30s | 文档可能重复索引 |

### 适用场景

| 场景 | 对应的 Redis 角色 |
|------|-----------------|
| 多个 API Pod 共享模型负载均衡状态 | 共享状态（冷却标记） |
| 后台任务异步处理（文档索引） | 消息队列（Celery） |
| Workflow 执行进度实时推前端 | 发布订阅 |
| 减少数据库查询压力 | 缓存（凭据/配置） |
| 防止同一文档被并发索引 | 分布式锁 |

### 注意事项

1. **KEYS 命令禁用生产环境**：`KEYS *` 会阻塞 Redis（单线程），生产环境用 `SCAN` 游标迭代
2. **缓存与数据库的一致性问题**：更新 Provider 配置后需主动失效 Redis 缓存（`DEL provider_credentials:*`），否则 5 分钟内新旧配置并存
3. **Pub/Sub 消息不持久化**：订阅者必须在线才能收到消息。关键事件建议改用 Redis Stream（Dify 当前版本未使用）
4. **内存淘汰策略**：确保 Redis 的 `maxmemory-policy` 不是 `noeviction`，否则内存满后所有写操作失败

### 常见踩坑经验

1. **坑：Redis 突然内存爆满，所有 SET 命令返回 OOM** → 根因：Celery 任务结果（`celery-task-meta-*`）堆积未过期。解决：配置 Celery `result_expires=3600`（1 小时自动清理），或手动 `redis-cli --scan --pattern "celery-task-meta-*" | xargs redis-cli DEL`
2. **坑：多个 API Pod 中模型冷却状态不一致** → 根因：某一 Pod 的 Redis 连接断开了，冷却检查走了本地缓存。解决：确保所有 Pod 共享同一个 Redis 实例（而非各自本地 Redis）
3. **坑：文档被索引了两次** → 根因：分布式锁 SETNX 的 TTL 太短（< 任务执行时间），锁过期后第二个 Worker 也拿到了锁。解决：确保锁的 TTL > 索引任务的最大执行时间（如 600s）

### 思考题

1. **进阶题**：如果 Redis 突然宕机 30 秒后恢复，Dify 的哪些功能可以自动恢复？哪些需要人工干预？（提示：分析每种 Redis 角色的持久化特性——哪些是纯内存、哪些有 RDB/AOF）

2. **进阶题**：Dify 的模型冷却机制用 `SETEX` 实现 60 秒固定冷却，这在生产中有两个问题：①如果所有 Key 同时冷却，没有备用可用 Key；②60 秒可能太长（其实 30 秒就恢复了）或太短（连续 429 仍需要更长冷却）。请设计一个改进的冷却算法。（提示：指数退避 + 至少保留一个可用 Key）

> **参考答案**：见附录 D

---

> **推广计划提示**：本章是中级篇 Redis 专题的核心。运维人员务必亲手完成步骤 2（冷却监控）和步骤 4（Pub/Sub 验证），理解 Redis 五角色是排查线上"莫名其妙"故障的基础。
