# 第39章：高可用与灾备架构设计——让Airflow永不停机

## 项目背景

凌晨3点17分，小胖的手机响了——不是闹钟，是PagerDuty的告警。他迷迷糊糊地拿起手机，屏幕上赫然显示："**Airflow Scheduler心跳丢失，已持续3分钟，当前所有DagRun已停止调度。**"

小胖猛地坐起来，远程连上运维跳板机，发现是Scheduler所在EC2实例的物理机宕机了——AWS正在进行计划外的底层硬件维护。而当前整个Airflow集群只有**一个Scheduler实例**。这意味着在EC2自动恢复（通常需要5-10分钟）之前，整个公司的数据管道、ETL任务、报表生成全部停滞。

更令人后怕的是，这只是"幸运的一次"——如果那天挂的不是Scheduler而是PostgreSQL主节点，恢复流程将远不止5-10分钟。因为没有配置自动故障切换，PG主节点挂掉需要人工介入：通知DBA → 登录服务器 → 检查WAL日志 → 手动将从节点提升为主 → 修改所有应用的连接字符串 → 重新部署。整个过程最快也要40分钟，而这40分钟内整个调度平台完全停摆。

第二天的事故复盘会上，CTO直接拍了桌子："一个Scheduler挂了，整个系统就瘫了？这是什么大数据调度平台！我们要的不是'大部分时候能用'，是**99.99%的可用性**——一年停机时间不超过52分钟！"

接着他环视一周，又补了一刀："你们知道如果每天凌晨3点到4点的ETL没跑完——交易部门拿不到前一天的盈亏数据，风控部门无法评估当日敞口，合规报表延迟提交被证监会约谈——这一小时的停机，公司的损失是多少吗？"

会议室一片沉默。大师在会议室后面安静地喝着茶，等所有人消化完这个问题的严重性，才缓缓开口："高可用不是加几台机器就完事的，它是一整套架构设计——从Scheduler到DB、从Broker到Webserver、从同城双活到异地灾备。今天这个事故，如果让我们来设计，至少有五层防护。而且，**除了架构设计，更重要的是验证**——不经过真实故障演练的HA方案，只是画在PPT上的美好愿望。"

## 项目设计

**小胖**（打开Architecture Diagram工具）："大师，您说的五层防护具体是哪些？"

**大师**（在屏幕上画出架构层次）："自底向上：

**第一层——数据库HA**：这是所有组件的'心脏'。PostgreSQL单点一旦挂了，所有Scheduler、Worker、Webserver全部停摆。推荐使用Patroni + etcd方案，3个PG节点（1主2从），etcd做分布式选主。Patroni会在Leader PG故障时自动将从节点提升为主，切换时间<30秒。

**第二层——Scheduler HA**：多Scheduler实例 + PG Advisory Lock自动选主。任何一个Scheduler挂了，其他Scheduler会通过`pg_try_advisory_lock`自动竞争成为新的'活跃Scheduler'。配置3个实例，每个部署在不同AZ（可用区），容忍任意1个AZ故障。

**第三层——Celery Broker HA**：这里有两种选择——Redis Sentinel（3节点，自动故障转移）或RabbitMQ Mirrored Queue（镜像队列，消息在所有节点间同步）。从运维复杂度看，Redis Sentinel更轻量；从消息可靠性看，RabbitMQ的持久化队列更安全。

**第四层——Webserver HA**：无状态服务，Nginx/ALB负载均衡 + 多实例部署 + Session共享（Redis Store）。每个AZ部署1-2个Webserver实例，确保前端API的连续性。

**第五层——异地灾备**：以上四层都在同一个Region的不同AZ中，这叫'同城双活'。但如果是整个Region故障（概率极低但不是零），还需要跨Region的备份方案——至少Metadata DB的离线备份 + 自动化恢复演练。"

**小胖**："五层防护说得容易，但每层都要花钱啊。要是CTO批不下来预算怎么办？"

**大师**："那我们从ROI的角度来分析。先算一笔账——每天凌晨的ETL停一个小时的业务损失是多少？用这个数字除以0.01%（也就是99.99%允许的年停机时间），你就知道'一年只允许停52分钟'意味着什么。然后按照这个标准反推每层的投入——你会发现，三节点Patroni集群的成本相比于一次PG主节点宕机导致的全线业务停滞，根本不算什么。

更重要的是——**并非每一层都需要100%完美的HA**。比如异地灾备，你可以先从'每天定时PG dump到跨Region S3'这种低成本方案开始（每月只需几美元），而非一开始就上跨Region的热备集群（每月几千美元）。这叫**分级建设**——先保证同城双活（RTO<30秒），再逐步完善异地灾备（RTO<4小时），最终达到跨Region分钟级切换。"

**小白**："那故障怎么演练？总不能真的把生产环境搞挂吧？"

**大师**："当然是在预发环境做啊。但关键是——**预发环境必须和生产环境拓扑一致**。如果生产是3个AZ、6个Webserver、Patroni + etcd，那预发也是同样的配置。然后我们按照故障演练计划，每个季度执行一次——模拟Scheduler crash、模拟PG主节点宕机、模拟Broker不可用——并记录恢复时间。如果某次演练发现RTO不达标（计划30秒实际用了2分钟），那就追查根因、修复、重新演练，直到达标为止。这就是混沌工程的基本思路。"

### 架构总览

```
                                    ┌──────────────┐
                                    │   Route 53   │
                                    │ DNS Failover │
                                    └──────┬───────┘
                                           │
                    ┌──────────────────────┼──────────────────────┐
                    │                      │                      │
            ┌───────▼───────┐     ┌───────▼───────┐     ┌───────▼───────┐
            │    AZ-1a       │     │    AZ-1b      │     │    AZ-1c      │
            │                │     │               │     │               │
            │ ┌───────────┐  │     │ ┌───────────┐ │     │ ┌───────────┐ │
            │ │ Scheduler │  │     │ │ Scheduler │ │     │ │ Scheduler │ │
            │ │  (Active) │  │     │ │ (Standby) │ │     │ │ (Standby) │ │
            │ └───────────┘  │     │ └───────────┘ │     │ └───────────┘ │
            │ ┌───────────┐  │     │ ┌───────────┐ │     │ ┌───────────┐ │
            │ │  PG Master│◄─┼─►───┼─┤PG Standby │ │◄───►│ │PG Standby │ │
            │ │ (Patroni) │  │     │ │ (Patroni) │ │     │ │ (Patroni) │ │
            │ └───────────┘  │     │ └───────────┘ │     │ └───────────┘ │
            │ ┌───────────┐  │     │ ┌───────────┐ │     │ ┌───────────┐ │
            │ │etcd Node1 │  │     │ │etcd Node2 │ │     │ │etcd Node3 │ │
            │ └───────────┘  │     │ └───────────┘ │     │ └───────────┘ │
            │ ┌───────────┐  │     │ ┌───────────┐ │     │ ┌───────────┐ │
            │ │ Redis     │  │     │ │ Redis     │ │     │ │ Redis     │ │
            │ │ Sentinel  │  │     │ │ Sentinel  │ │     │ │ Sentinel  │ │
            │ └───────────┘  │     │ └───────────┘ │     │ └───────────┘ │
            │ ┌───────────┐  │     │ ┌───────────┐ │     │ ┌───────────┐ │
            │ │ Webserver │  │     │ │ Webserver │ │     │ │ Webserver │ │
            │ │  (x2)     │  │     │ │  (x2)     │ │     │ │  (x2)     │ │
            │ └───────────┘  │     │ └───────────┘ │     │ └───────────┘ │
            │ ┌───────────┐  │     │ ┌───────────┐ │     │ ┌───────────┐ │
            │ │  Worker   │  │     │ │  Worker   │ │     │ │  Worker   │ │
            │ │ (K8s Pod) │  │     │ │ (K8s Pod) │ │     │ │ (K8s Pod) │ │
            │ └───────────┘  │     │ └───────────┘ │     │ └───────────┘ │
            └────────────────┘     └───────────────┘     └───────────────┘
```

## 项目实战

### Step 1：PostgreSQL Patroni 3节点高可用部署

Patroni是Zalando开源的PG高可用管理工具，通过etcd做分布式协调：

```yaml
# docker-compose-patroni.yml
version: "3.8"
services:
  etcd1:
    image: quay.io/coreos/etcd:v3.5
    command:
      - etcd
      - --name=etcd1
      - --initial-advertise-peer-urls=http://etcd1:2380
      - --listen-peer-urls=http://0.0.0.0:2380
      - --listen-client-urls=http://0.0.0.0:2379
      - --advertise-client-urls=http://etcd1:2379
      - --initial-cluster-token=airflow-patroni-etcd
      - --initial-cluster=etcd1=http://etcd1:2380,etcd2=http://etcd2:2380,etcd3=http://etcd3:2380
      - --initial-cluster-state=new

  patroni1:
    image: patroni:latest
    environment:
      PATRONI_NAME: patroni1
      PATRONI_SCOPE: airflow-cluster
      PATRONI_ETCD_HOSTS: etcd1:2379,etcd2:2379,etcd3:2379
      PATRONI_POSTGRESQL_LISTEN: 0.0.0.0:5432
      PATRONI_POSTGRESQL_CONNECT_ADDRESS: patroni1:5432
      PATRONI_RESTAPI_LISTEN: 0.0.0.0:8008
      PATRONI_POSTGRESQL_DATA_DIR: /data/postgres
      PATRONI_POSTGRESQL_PGPASS: /tmp/pgpass
      PATRONI_REPLICATION_USERNAME: replicator
      PATRONI_REPLICATION_PASSWORD: StrongReplPass123
      PATRONI_SUPERUSER_USERNAME: postgres
      PATRONI_SUPERUSER_PASSWORD: StrongPostgresPass123
    volumes:
      - patroni1_data:/data/postgres

patroni1_data:
```

Patroni自动管理PG的主从切换。验证切换能力：

```bash
# 查看当前集群状态
curl -s http://patroni1:8008/cluster | jq '{
  leader: .leader,
  members: [.members[] | {name: .name, role: .role, state: .state}]
}'

# 手工触发主从切换（演练用）
curl -X POST http://patroni1:8008/switchover \
  -d '{"leader": "patroni1", "candidate": "patroni2"}'

# 或者模拟主节点故障——直接停止Patroni1容器
docker stop patroni1
# 30秒内，etcd检测到Leader失联，自动将patroni2提升为新主
```

### Step 2：Scheduler多实例 + Advisory Lock自动选主

Scheduler的HA配置：

```ini
# airflow.cfg（每个Scheduler实例的配置）
[scheduler]
scheduler_id = scheduler-{{ INSTANCE_ID }}
max_tis_per_query = 512
scheduler_heartbeat_sec = 5

# 关键：允许多个Scheduler共存
scheduler.max_threads = 2
```

自动选主的原理在`airflow-core/src/airflow/jobs/scheduler_job_runner.py`中：

```python
def _execute(self):
    """Scheduler主循环"""
    self.log.info("Starting Scheduler Job Runner")

    # 尝试获取全局调度锁
    self._acquire_scheduler_lock()

    while not self.terminating:
        try:
            with create_session() as session:
                # 关键区——只有获得Advisory Lock的Scheduler才能进入
                lock_id = self._get_critical_section_lock_id()
                acquired = session.execute(
                    sa.text("SELECT pg_try_advisory_lock(:id)"), {"id": lock_id}
                ).scalar()

                if not acquired:
                    self.log.debug(
                        "Lock not acquired, another Scheduler is active. "
                        "ID: %s, Lock ID: %s",
                        self.id, lock_id,
                    )
                    time.sleep(self.heartrate)  # 等待下一个调度周期
                    continue

                try:
                    # 三个核心步骤
                    self._do_dag_parsing(session)
                    self._do_scheduling(session)
                    self._do_task_instance_queuing(session)
                    session.commit()
                finally:
                    session.execute(
                        sa.text("SELECT pg_advisory_unlock(:id)"), {"id": lock_id}
                    )
        except Exception:
            self.log.exception("Scheduler loop error")
            time.sleep(self.heartrate)

        time.sleep(self._processor_poll_interval)
```

### Step 3：Redis Sentinel Brokers HA

Redis Sentinel 为Celery Broker提供自动故障转移：

```bash
# sentinel.conf 核心配置
port 26379
sentinel monitor airflow-broker redis1 6379 2
sentinel down-after-milliseconds airflow-broker 5000
sentinel failover-timeout airflow-broker 15000
sentinel parallel-syncs airflow-broker 1
sentinel auth-pass airflow-broker SentinelAuthPass123
```

Celery Broker URL使用Sentinel协议：

```ini
# airflow.cfg
[executors.celery]
broker_url = sentinel://:SentinelAuthPass123@sentinel1:26379,sentinel2:26379,sentinel3:26379/mymaster/0
broker_transport_options = {
    "master_name": "airflow-broker",
    "sentinel_kwargs": {"password": "SentinelAuthPass123"}
}
result_backend_type = redis
result_backend = redis-sentinel://:SentinelAuthPass123@sentinel1:26379,sentinel2:26379,sentinel3:26379/mymaster/1
```

当Redis Master宕机时，Sentinel在5秒内检测到，15秒超时窗口内完成选主和切换。Celery客户端（Worker和Scheduler）会自动重新连接到新Master。

### Step 4：Webserver无状态化 + 负载均衡

Nginx配置示例：

```nginx
# /etc/nginx/conf.d/airflow.conf
upstream airflow_webservers {
    least_conn;
    server webserver-az1a:8080 max_fails=3 fail_timeout=30s;
    server webserver-az1b:8080 max_fails=3 fail_timeout=30s;
    server webserver-az1c:8080 max_fails=3 fail_timeout=30s;
    keepalive 32;
}

server {
    listen 80;
    server_name airflow.internal.example.com;

    location / {
        proxy_pass http://airflow_webservers;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_read_timeout 300s;
        proxy_connect_timeout 5s;
    }

    # 健康检查端点
    location /health {
        return 200 "OK";
        add_header Content-Type text/plain;
    }
}
```

Webserver的Session共享通过Redis实现：

```ini
[webserver]
session_backend = redis
session_backend_url = redis-sentinel://:SentinelAuthPass123@sentinel1:26379,sentinel2:26379,sentinel3:26379/mymaster/2
# 确保用户Login状态在多个Webserver实例间同步
```

### Step 5：异地灾备——跨Region备份与恢复

Metadata DB的异地备份策略：

```python
# 自动化备份Dag - backup_metadata_to_s3.py
from airflow import DAG
from airflow.operators.python import PythonOperator
from airflow.providers.amazon.aws.hooks.s3 import S3Hook
from datetime import datetime
import subprocess
import os

def backup_metadata_db(**context):
    """备份整个Metadata数据库到跨Region S3"""
    backup_file = f"/tmp/airflow_backup_{context['ds_nodash']}.sql.gz"
    db_url = os.environ["AIRFLOW__CORE__SQL_ALCHEMY_CONN"]

    # 1. pg_dump 完整备份
    subprocess.run([
        "pg_dump", "-d", db_url,
        "--no-owner", "--no-acl",
        "--compress=9",
        "-f", backup_file,
    ], check=True)

    # 2. 上传到异地S3桶（不同Region）
    s3_hook = S3Hook(aws_conn_id="aws_disaster_recovery")
    s3_hook.load_file(
        filename=backup_file,
        key=f"metadata/backups/{context['ds_nodash']}/airflow_metadata.sql.gz",
        bucket_name="airflow-dr-backup-ap-southeast-1",  # 另一个Region
        replace=True,
    )

    # 3. 清理本地临时文件
    os.remove(backup_file)
    return f"Backup uploaded to s3://airflow-dr-backup-ap-southeast-1/.../{context['ds_nodash']}"

with DAG(
    "disaster_recovery_backup",
    schedule="0 2 * * *",  # 每天凌晨2点
    start_date=datetime(2024, 1, 1),
    catchup=False,
    tags=["dr", "backup", "critical"],
):
    PythonOperator(
        task_id="backup_pg_to_s3_cross_region",
        python_callable=backup_metadata_db,
    )
```

### Step 6：故障演练——混沌工程注入

在预发环境部署与生产完全一致的3-AZ拓扑后，我们制定了一套标准的故障演练手册（Runbook），并按季度执行。每次演练都有明确的"成功标准"——不仅是系统自动恢复，还要验证监控告警是否在指定时间内触发、值班工程师能否在5分钟内打开正确的Runbook页面。

**Scenario 1：Scheduler主进程崩溃**

```bash
# 模拟：找到持有Advisory Lock的Scheduler并Kill
ACTIVE_SCHEDULER=$(kubectl get pods -n airflow -l component=scheduler -o json | \
  jq -r '.items[] | select(.status.phase=="Running") | .metadata.name' | head -1)
kubectl delete pod -n airflow "$ACTIVE_SCHEDULER" --grace-period=0 --force

# 预期结果：
# - 0-5s：PG检测到Advisory Lock被释放
# - 5-15s：备用Scheduler竞争获得新Lock
# - <30s：Dag调度恢复正常
# - 在此期间处于running状态的Task不受影响
```

**Scenario 2：PG主节点故障切换**

```bash
# 模拟：强制停止Patroni Leader
docker stop patroni1

# 监控切换过程
watch -n 1 'curl -s http://patroni2:8008/cluster | jq .leader'

# 预期结果：
# - 0-10s：etcd发现Leader失联
# - 10-25s：patroni2或patroni3被提升为新主
# - 25-30s：所有客户端（Scheduler/Worker/Web）重连成功
```

**Scenario 3：Redis Broker宕机**

```bash
# 模拟：停止Redis Master
redis-cli -h redis1 -p 6379 -a RedisMasterPass123 DEBUG SLEEP 30

# 预期结果：
# - 0-5s：Sentinel检测到Master不可达
# - 5-20s：Sentinel完成故障转移，提升一个新Master
# - Worker短暂重试后自动连接新Broker
# - 已在运行的任务继续完成，新任务在切换后正常入队
```

**Scenario 4：跨AZ网络分区（脑裂测试）**

这是最极端但也最考验HA设计的场景。我们通过iptables模拟AZ-1a与AZ-1b/AZ-1c之间的网络完全隔离：

```bash
# 在AZ-1a的所有节点上执行
iptables -A INPUT -s 10.1.0.0/16 -j DROP  # 阻断来自AZ-1b和AZ-1c的流量
iptables -A OUTPUT -d 10.1.0.0/16 -j DROP # 阻断发往AZ-1b和AZ-1c的流量
```

验证要点：
- etcd Raft协议自动检测到AZ-1a节点失联，在AZ-1b和AZ-1c之间完成leader选举（需2/3多数）
- Patroni检测到etcd多数派变化，AZ-1a中的PG Master被自动降级为只读
- AZ-1b或AZ-1c中的PG Standby被提升为新Master
- Nginx健康检查检测到AZ-1a的Webserver不可达，自动摘除
- Scheduler的Advisory Lock自动转移到新Master所在的AZ

恢复后验证（解除网络分区）：
- etcd集群自动恢复三节点一致性
- 旧Master（AZ-1a中的PG）自动成为新Master的Standby，通过`pg_rewind`同步差异数据
- 全网恢复正常运行，整个过程中已完成/运行中的任务状态没有丢失或重复

这次脑裂测试发现了一个关键问题：`pg_rewind`在差异数据量超过1GB时耗时超过60秒，影响了恢复速度。解决方案是调整`wal_keep_size`从1GB增加到4GB——确保短暂网络分区期间的WAL不会丢失，从而避免触发全量pg_rewind。

### Step 7：容量规划与成本优化

```python
# 容量预估模型
def capacity_planning(current_tasks_per_day, growth_rate_per_month, months=6):
    """
    基于Task增长曲线预估未来资源需求
    """
    projected = []
    tasks = current_tasks_per_day
    for month in range(1, months + 1):
        tasks *= (1 + growth_rate_per_month)
        # 假设每个Worker处理4个Task，每台Worker 2vCPU/4GB
        workers_needed = int(tasks * 1.3 / (24 * 4))  # 1.3倍冗余
        schedulers_needed = max(3, int(workers_needed / 50))
        db_iops_needed = int(tasks * 5)  # 每个Task约5次DB IO
        projection = {
            "month": month,
            "tasks_per_day": int(tasks),
            "workers": workers_needed,
            "schedulers": schedulers_needed,
            "db_iops": db_iops_needed,
        }
        projected.append(projection)
    return projected

# 示例输出
current = 100000  # 当前日均10万Task
growth = 0.15  # 月增长率15%
for p in capacity_planning(current, growth):
    print(f"第{p['month']}月: {p['tasks_per_day']:,} 任务/天, "
          f"{p['workers']} 个Worker, {p['schedulers']} 个Scheduler, "
          f"{p['db_iops']:,} IOPS")
```

成本优化分析——跨3个AZ的完整HA集群：

| 组件 | 规格 | 数量 | 按需价格/月 | 优化策略 | 优化后价格/月 |
|------|------|------|------------|----------|-------------|
| EC2 (Scheduler) | t3.large 2vCPU/8GB | 3 | $298 | 预留实例(1年) | $179 |
| EC2 (Webserver) | t3.medium 2vCPU/4GB | 6 | $355 | 预留实例(1年) | $213 |
| EKS Worker Nodes | m5.xlarge 4vCPU/16GB | 5-40 | $2,880 | Spot实例 + KEDA | $1,152 |
| RDS (主) | db.r5.xlarge 4vCPU/32GB | 1 | $508 | 预留实例(1年) | $356 |
| RDS (从) | db.r5.large 2vCPU/16GB | 2 | $508 | 预留实例(1年) | $356 |
| ElastiCache (Redis) | cache.r5.large | 3 | $377 | 预留实例(1年) | $264 |
| Network (跨AZ流量) | - | - | $120 | - | $120 |
| S3 (异地备份) | 100GB Standard | - | $2.4 | - | $2.4 |
| **总计** | | | **$5,048** | | **$2,642** |

优化后成本降低47.7%，关键是**Spot实例用于Worker**（可中断但不影响任务执行，Celery会自动重试）和**预留实例用于稳定组件**。

## 项目总结

高可用架构不是"加机器"，而是**系统性地消除每一个单点故障**。从PG Patroni的三节点到Scheduler的Advisory Lock自动选主，从Redis Sentinel的快速故障转移到异地S3的冷备恢复，每一层都需要经过真实故障演练的验证。小胖团队现在可以自信地说：在任意单个AZ故障、任意单个组件宕机的情况下，Airflow集群能在30秒内完成自动恢复——这才是生产级系统应有的韧性。

回顾这个项目，小胖总结了三条关键经验：

**第一条：HA不是一次性的架构设计，而是持续运营的过程。** 第一次故障演练时，PG的自动切换耗时长达8分钟（远超30秒的目标）。排查发现是`wal_keep_size`配置过小，导致standby节点在同步WAL时产生了lag。调整后，第二次演练降至45秒，第三次才达标28秒。没有一个参数是"一开始就能设对的"。

**第二条：监控是HA的眼睛，没有告警的故障等于没有发生。** 我们在每个组件的关键路径上埋了自定义Metrics：Scheduler的Advisory Lock获取成功率、Patroni的Replication Lag、Redis Sentinel的Master Change事件次数。这些指标通过Prometheus + Grafana可视化，并设置了分级告警（Warning → Critical → PagerDuty）。当真正遇到故障时，工程师不需要从头排查——仪表盘上直接就能看到哪个组件失联了。

**第三条：容量规划要前置到架构设计阶段。** 高可用意味着冗余——本来一台PG就够了，现在要三台；本来一个Scheduler就跑得动，现在要三个。这些冗余成本必须提前预算。6个月的Task增长预测曲线帮助我们说服了财务部——与其等到系统撑不住再紧急扩容（成本和风险双高），不如按预测提前预留资源（预留实例能省50%费用）。

最后，关于"99.99%可用性意味着什么"——一年52分钟停机时间，平均到每天就是8.5秒。这意味着每一次故障切换都在和时间赛跑。高可用架构的本质，就是**用冗余换取恢复速度，用演练确保切换的确定性**。

### 思考题

1. 如果使用RabbitMQ的Mirrored Queue替代Redis Sentinel作为Celery Broker，消息在所有节点间同步会导致性能损耗（吞吐量约下降30%）。在金融场景下，消息可靠性（不丢失任务）与吞吐量之间的平衡该如何取舍？请设计一种折中方案（如：高优先级Dag用Mirrored Queue，普通Dag用Redis Sentinel）。

2. 在跨AZ部署中，etcd集群采用Raft协议，要求超过半数节点存活才能选出Leader。如果AZ-1a整体故障（含etcd Node1和PG Master），剩下AZ-1b和AZ-1c仅2个etcd节点，无法形成Raft多数派。此时的Patroni会发生什么？如何设计才能使集群在仅剩2个AZ时仍能正常工作？
