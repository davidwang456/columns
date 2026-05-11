# 第19章：Celery 分布式任务队列深度解析

## 1. 项目背景

"我上传了一份 200 页的 PDF 到知识库，点击'保存并处理'后页面显示索引进度 0%。等了 30 分钟终于跳到 100%，中间我刷新了 5 次页面，每次都以为卡死了。"这是新手使用 Dify 知识库最常见的困惑。30 分钟里，Dify 的后台 Celery Worker 一直在拼命工作：提取 PDF 文本、切成 800 段、调用 Embedding API 生成 800 条 1536 维向量、逐条写入 Weaviate 向量数据库。

如果把这个过程放在 HTTP 请求里同步处理——让用户在浏览器前干等 30 分钟——那运维早就被投诉淹没了。Dify 使用 Celery（Python 生态最成熟的分布式任务队列）来处理所有耗时操作。但 Celery 不是简单的"扔到后台就不管了"——你需要理解三个核心问题：

1. **任务是如何从 API 进程到 Worker 进程的？**（Producer → Broker → Consumer 模式）
2. **多租户的任务如何隔离？**（租户独占队列 `tenant_self_*_task_queue`）
3. **Flask 请求上下文如何在 Worker 中可用？**（FlaskTask 包装器）

理解这三个问题，你才能排查"知识库索引卡住""Workflow 异步执行超时""定时任务没有触发"等生产故障。

## 2. 项目设计——剧本式交锋对话

**小胖**：（指着 Dify 控制台上"索引进度 0%"）"大师救急！我上传了个 PDF，半小时了索引进度还是 0%。是不是 Dify 出 bug 了？"

**大师**："你先 `docker ps` 看看 worker 容器是不是 Exited。"

**小胖**：（敲命令）"还真是！worker 容器状态是 Exited。我 `docker restart` 一下试试……好了！索引进度开始动了！但是大师，为什么要有 Worker？API 直接处理不行吗？"

**大师**："你想，如果 API 直接处理 PDF 索引——提取文字 30 秒 + 分段 2 秒 + 调 Embedding 为 800 段生成向量 80 秒 + 写入向量数据库 5 秒——加起来 117 秒。这意味着你的 HTTP 请求要等快 2 分钟才能返回。浏览器早就超时了，用户也以为卡死了。Celery 的解决方案是：API 收到请求后，把'索引这个文档'包装成一个任务，丢进 Redis 队列，立即返回'任务已创建'。后台 Worker 从队列取任务慢慢执行。"

**技术映射**：同步 vs 异步 = 用户等待（阻塞 117 秒）vs 后台执行（API 立即返回），核心是 Producer-Consumer 模式。

**小白**："那 Celery 的三个角色——Producer、Broker、Consumer——在 Dify 里各是什么？"

**大师**：
- **Producer（生产者）**：API 容器。用户在网页上点击'保存并处理'，API 收到请求后 `celery.send_task('document_indexing_task', args=[doc_id])`。
- **Broker（消息中间件）**：Redis。任务存在 Redis 的 List 里，等待 Worker 来取。类似外卖平台——订单（任务）在平台（Redis）等骑手（Worker）接单。
- **Consumer（消费者）**：Worker 容器。启动了一个 Celery worker 进程，不断 `BRPOP` Redis 队列，拿到任务后执行。"

**小胖**："那 Celery Beat 又是什么东西？我在 `docker-compose.yaml` 里还看到一个 worker_beat 容器。"

**大师**："Beat 是定时调度器——按时间表自动触发任务，不依赖用户操作。Dify 配了三个定时任务：
1. 每天凌晨 2 点：清理过期消息（`clean_messages`）
2. 每 1 分钟：检查有没有定时触发的 Workflow 该执行了（`poll_workflow_schedules`）
3. 每 15 分钟：检查 Marketplace 有没有插件需要更新（`check_upgradable_plugin`）

你可以在 `api/schedule/` 目录下看到所有定时任务的定义。"

**技术映射**：Celery Beat = Cron 服务，按 crontab 表达式定时触发任务。

**小白**："我注意到 Worker 里需要用 Flask 的 `current_app` 和数据库连接。但 Worker 是独立进程，没有 HTTP 请求上下文——它怎么拿到数据库连接？"

**大师**："这是 Dify 最精巧的设计之一：`FlaskTask` 包装器。看 `api/extensions/ext_celery.py` 的源码——Dify 定义了一个自定义 Task 基类，在 `__call__` 方法中用 `app.app_context()` 创建了一个 Flask 应用上下文。这样每个 Celery 任务执行时，都临时拥有了一个 Flask App Context——可以访问 `current_app.config`、使用数据库连接池。"

**技术映射**：FlaskTask = Flask App Context 注入 Celery Worker，让异步任务能使用 Flask 扩展。

## 3. 项目实战

### 环境准备

| 条件 | 说明 |
|------|------|
| Dify 已部署 | Worker 容器必须 running |
| Redis CLI | `docker exec -it docker-redis-1 redis-cli` |
| 测试文档 | 一份小 PDF（用于快速索引） |

### 分步实现

#### 步骤1：理解 Dify 的 Celery 配置（目标：读懂任务注册和调度）

```python
# api/extensions/ext_celery.py（简化）
from celery import Celery
from flask import Flask

def init_app(app: Flask) -> Celery:
    celery = Celery(
        app.import_name,
        broker=app.config['CELERY_BROKER_URL'],   # Redis URL
        backend=app.config['CELERY_BACKEND'],      # Redis（结果存储）
    )
    
    # ★ 关键设计：FlaskTask 上下文注入
    class FlaskTask(celery.Task):
        abstract = True
        def __call__(self, *args, **kwargs):
            # 每个任务执行前，创建 Flask 应用上下文
            with app.app_context():
                return super().__call__(*args, **kwargs)
    
    celery.Task = FlaskTask
    
    # ★ 定时任务（Beat Schedule）
    celery.conf.beat_schedule = {
        'clean_messages': {
            'task': 'schedule.clean_messages.clean_messages',
            'schedule': crontab(hour=2, minute=0),
        },
        'workflow_schedule_polling': {
            'task': 'schedule.workflow_schedule_task.poll_workflow_schedules',
            'schedule': timedelta(minutes=1),
        },
        'check_upgradable_plugin': {
            'task': 'schedule.check_upgradable_plugin_task.check_upgradable_plugin',
            'schedule': timedelta(minutes=15),
        },
    }
    return celery
```

#### 步骤2：监控 Celery 队列——实时观察任务流转（目标：可视化任务生命周期）

```bash
# 终端 1：监控 Worker 日志
docker logs docker-worker-1 -f --tail 10

# 终端 2：查看 Redis 队列
watch -n 2 'docker exec docker-redis-1 redis-cli LLEN celery'

# 终端 3：触发一个文档索引任务
# （在 Dify 控制台上传一个小文档）

# 观察终端 2：LLEN celery 从 1 变成 0（任务被 Worker 取走）
# 观察终端 1：Worker 日志显示：
#   [INFO] Task document_indexing_task[abc-123] received
#   [INFO] Extracting text from document.pdf
#   [INFO] Splitting into 45 chunks
#   [INFO] Generating embeddings... (45/45)
#   [INFO] Task document_indexing_task[abc-123] succeeded in 32.5s
```

#### 步骤3：租户隔离队列（目标：理解多租户公平调度）

Dify 使用租户级别的队列隔离，防止大租户的任务阻塞小租户：

```python
# api/core/rag/pipeline/ 中的队列隔离
# 不同租户有独立的任务队列
queue_name = f"tenant_self_indexing_task_queue:{tenant_id}"

# 租户 A 上传 100 个文档 → 进入 queue_a
# 租户 B 上传 5 个文档   → 进入 queue_b
# 即使 queue_a 排长队，Worker 轮询时仍会处理 queue_b 的任务
```

```bash
# 查看所有租户队列
docker exec docker-redis-1 redis-cli KEYS "tenant_self_*"

# 查看各队列长度
for key in $(docker exec docker-redis-1 redis-cli KEYS "tenant_self_*"); do
    echo "$key: $(docker exec docker-redis-1 redis-cli LLEN $key)"
done
```

### 测试验证

```bash
# 测试 1：验证 Worker 正常消费任务
docker exec docker-redis-1 redis-cli LLEN celery
# 正常值：0 或 < 5（少量积压）

# 测试 2：手动触发一个测试任务
docker exec docker-api-1 celery -A app.celery call schedule.check_upgradable_plugin_task.check_upgradable_plugin
# 然后在 Worker 日志中确认任务被接收

# 测试 3：检查 Beat 定时任务是否运行
docker logs docker-worker_beat-1 --tail 20 | Select-String "Scheduler|sending task"
```

## 4. 项目总结

### 优点与缺点

| 维度 | 优点 | 缺点 |
|------|------|------|
| **异步解耦** | API 快速返回，耗时任务后台处理 | 用户无法直接感知后台进度（需额外 SSE 推送） |
| **故障重试** | Celery 内置 `max_retries` 和指数退避 | 重试逻辑需要任务显式配置，不能全局生效 |
| **租户隔离** | 独立队列防止资源争抢 | 队列数量随租户增长，Redis 管理成本上升 |
| **FlaskTask** | Worker 可以访问数据库和缓存 | 上下文注入有轻微性能开销 |

### 适用场景

| 场景 | 推荐方案 |
|------|---------|
| 文档索引 | Celery 异步任务，WebSocket 推进度 |
| 定时清理 | Celery Beat + crontab |
| Workflow 长时间执行 | 异步 Workflow 任务 + 结果轮询 |
| 邮件发送 | Celery + 邮件模板 |

### 注意事项

1. **Worker 数量与并发**：Gevent pool 模式下 `concurrency=500`，500 个协程可以并发处理 500 个任务
2. **任务幂等性**：Celery 可能重复投递同一任务（"至少一次"语义），任务实现应当幂等
3. **结果存储**：Celery `backend` 默认是 Redis，大量结果堆积会占满 Redis 内存，建议设置 `result_expires`

### 常见踩坑经验

1. **坑：Worker 不消费任务，队列越来越长** → 根因：Worker 连接到错误的 Redis 实例，或 Worker 处于 `Exited` 状态。解决：`docker ps` 检查状态，`docker logs` 查看连接错误
2. **坑：Celery 任务报 `OperationalError: no such table`** → 根因：FlaskTask 上下文注入失败，SQLAlchemy 找不到数据库绑定。解决：检查 `init_app` 中 FlaskTask 是否被正确设置为 `celery.Task`
3. **坑：定时任务没有触发** → 根因：Beat 容器的时区配置错误（UTC vs Asia/Shanghai）。解决：在 `celery.conf.timezone` 中显式设置时区

### 思考题

1. **进阶题**：Celery Worker 在执行任务时崩溃（OOM），队列中已取出但未完成的任务会丢失吗？（提示：`acks_late` 参数和任务确认机制）

2. **进阶题**：Dify 的租户隔离队列能防止单个租户耗尽全部 Worker 资源吗？如果不能，应该如何改进？（提示：Worker 级别的优先级调度或资源配额）

> **参考答案**：见附录 D
