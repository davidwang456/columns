# 第8章：PostgreSQL Connector 实战

## 1. 项目背景

"你们后端组说 PostgreSQL 里的用户画像数据很重要，需要实时同步到推荐系统——"产品经理的第 N 个实时数据需求。已经用惯了 MySQL Connector 的小胖，信心满满地打开 PostgreSQL 的配置文件准备照搬经验。仅仅 20 分钟后，他就发现自己错了——PostgreSQL 的 CDC 机制和 MySQL 完全不同！没有 binlog，取而代之的是 WAL（Write-Ahead Log）+ 逻辑复制槽（Replication Slot）。MySQL 的 `binlog_format=ROW` 对应 PG 的 `wal_level=logical`，而且还需要在数据库中手动创建 Publication。

更重要的是，PG 的逻辑复制需要写入一个叫 `pgoutput` 的原生插件，如果版本不匹配，Connector 直接 FAILED。本章将带你从零开始掌握 PostgreSQL Connector，理解 WAL、Replication Slot、Publication 三者的关系，并跑通第一条 PG → Kafka 的 CDC 链路。

### 痛点放大

- **WAL 磁盘膨胀**：Replication Slot 就像一个"水位标记"，告诉 PostgreSQL "从这里开始还没消费的 WAL 不能删除"。如果 Connector 停止消费，WAL 会无限膨胀直到磁盘满
- **PG 版本兼容**：`pgoutput` 插件在 PG 10+ 才内置，PG 9.x 需要安装 `wal2json` 扩展
- **Schema 重建风险**：如果某张表被 DROP 后重建，Replication Slot 中的位点可能不可用，需要手动清理 Slot
- **DDL 处理差异**：PostgreSQL 的 DDL 是事务性的，和 MySQL 的事务外 DDL 不同，Debezium 对 DDL 的处理方式也不同

---

## 2. 项目设计——三人对话

**（下午茶时间，小胖端着一杯咖啡走过来）**

**小胖**："大师，我以为 Debezium 都是同一个套路，结果换到 PG 上就翻车了。PG 没有 binlog 这个东西，它到底怎么捕获变更的？"

**大师**："PG 用了完全不同的一套机制。打个比方——MySQL 的 binlog 像一本**流水账**，每做一笔交易就记一行。而 PG 的 WAL 像一份**日记本副本**——写数据之前，先把'我打算做什么'写在日记本里（WAL），然后再执行实际操作。如果数据库崩溃了，PG 可以用这份日记本来恢复数据。"

**小白**："那 Debezium 怎么从 WAL 里提取出有意义的数据变更呢？WAL 记录的是底层的物理页面变化，并不是我们需要的 row-level 变更。"

**大师**："这就是 `wal_level=logical` 的魔法。当 `wal_level=logical` 时，WAL 中会额外记录**逻辑变更信息**——不只是 'Page 5 的 byte 23 从 0x01 变成了 0x02'，而是 'orders 表的 id=100 这一行，status 从 pending 变成了 shipped'。`pgoutput` 插件负责将这些逻辑变更转换成可读的事件流。"

**小胖**："那 Replication Slot 又是什么？我看很多文章警告说 Slot 不管理好会导致磁盘爆掉。"

**大师**："Replication Slot 是 PG 用来保证'消费者不掉队'的机制。创建 Slot 之后，PG 会保留所有 Slot 位点之后的 WAL 日志，直到消费者确认'我已经读到某个位点了'。就相当于你从图书馆借了一套书，图书馆前台立了个'借出登记卡'——只要卡在，这些书就不会被处理掉。但如果你借了书一直不还，图书馆的存储空间（WAL 磁盘）就会爆满。"

**大师**："技术映射：Replication Slot = 图书馆的借出登记卡。Connector 每消费一些 WAL 记录，就会向 Slot 汇报 '我读到这里了'（advance LSN），PG 就会把已经读过的 WAL 清理掉。如果 Connector 故障停止消费，Slot 就卡住不动，PG 积压的 WAL 越来越多，最终磁盘写满。"

**小白**："那 Publication 呢？它和 Slot 的关系又是什么？"

**大师**："Publication 是 PG 10+ 的一个原生概念，它定义了'我要把哪些表的变更发布出去'。可以理解为——Publication 是'菜单'（定义要发布哪些表），Replication Slot 是'餐卡'（记录你吃到哪了），pgoutput 是'厨师'（负责做菜，也就是把 WAL 转为变更事件）。三者必须协同工作。"

```
PG CDC 的三角关系：
┌──────────┐    ┌─────────────┐    ┌──────────┐
│Publication│───▶│  pgoutput   │───▶│  Slot    │
│ (发布什么) │    │ (怎么转化)   │    │(消费位点) │
└──────────┘    └─────────────┘    └──────────┘
       │              │                  │
       ▼              ▼                  ▼
  CREATE          内置插件           pg_replication
  PUBLICATION     wal_level=        _slots 视图
                  logical
```

---

## 3. 项目实战

### 环境准备

在 docker-compose.yml 中增加 PostgreSQL 容器。

```bash
# 添加 PostgreSQL 到环境
cd ~/debezium-lab

# 在 docker-compose.yml 中添加以下 service：
cat >> docker-compose.yml << 'EOF'
  postgres:
    image: postgres:16
    container_name: postgres
    ports:
      - "5432:5432"
    environment:
      POSTGRES_USER: postgres
      POSTGRES_PASSWORD: postgres123
      POSTGRES_DB: user_profile
    command:
      - "postgres"
      - "-c"
      - "wal_level=logical"
      - "-c"
      - "max_replication_slots=5"
      - "-c"
      - "max_wal_senders=5"
    volumes:
      - ./pg-init:/docker-entrypoint-initdb.d
EOF

# 创建 PG 初始化脚本
mkdir -p pg-init
cat > pg-init/01-init.sql << 'EOF'
-- 创建应用用户和表
CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    username VARCHAR(100) NOT NULL,
    email VARCHAR(255),
    phone VARCHAR(20),
    preferences JSONB DEFAULT '{}',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

INSERT INTO users (username, email, phone, preferences) VALUES
('alice', 'alice@example.com', '13800001111', '{"theme":"dark","lang":"zh"}'),
('bob', 'bob@example.com', '13800002222', '{"theme":"light","lang":"en"}'),
('charlie', 'charlie@example.com', '13800003333', '{"theme":"dark","lang":"zh","notifications":true}');

-- 创建 debezium 专用账户
CREATE ROLE debezium_user WITH LOGIN PASSWORD 'dbz1234' REPLICATION;
GRANT CONNECT ON DATABASE user_profile TO debezium_user;
GRANT USAGE ON SCHEMA public TO debezium_user;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO debezium_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO debezium_user;

-- 创建 Publication
CREATE PUBLICATION dbz_publication FOR TABLE users;
EOF

# 重启环境
docker compose down && docker compose up -d
sleep 30
```

### 步骤1：下载 PostgreSQL Connector 插件

```bash
cd ~/debezium-lab/plugins

wget https://repo1.maven.org/maven2/io/debezium/debezium-connector-postgres/2.7.1.Final/debezium-connector-postgres-2.7.1.Final-plugin.tar.gz
tar -xzf debezium-connector-postgres-2.7.1.Final-plugin.tar.gz

# 重启 Kafka Connect 以加载新插件
docker restart connect
sleep 30

# 确认 PG Connector 已被加载
curl http://localhost:8083/connector-plugins | python3 -c "import sys,json;[print(p['class']) for p in json.load(sys.stdin) if 'postgres' in p['class']]"
# 预期输出：io.debezium.connector.postgres.PostgresConnector
```

### 步骤2：注册 PostgreSQL Connector

```bash
curl -X POST http://localhost:8083/connectors \
  -H "Content-Type: application/json" \
  -d '{
    "name": "pg-users-connector",
    "config": {
      "connector.class": "io.debezium.connector.postgres.PostgresConnector",
      "database.hostname": "postgres",
      "database.port": "5432",
      "database.user": "debezium_user",
      "database.password": "dbz1234",
      "database.dbname": "user_profile",
      "topic.prefix": "pg_users",
      "table.include.list": "public.users",
      "plugin.name": "pgoutput",
      "publication.name": "dbz_publication",
      "publication.autocreate.mode": "filtered",
      "slot.name": "debezium_users_slot",
      "snapshot.mode": "initial",
      "decimal.handling.mode": "double",
      "heartbeat.interval.ms": "10000"
    }
  }'
```

**PG Connector 特有参数解析**：

| 参数 | 作用 | 为什么这样配 |
|------|------|-------------|
| `database.dbname` | 数据库名 | PG 的 `hostname:port` 只是到实例，必须指定 dbname |
| `plugin.name=pgoutput` | 逻辑解码插件 | PG 10+ 推荐原生 pgoutput，性能最好 |
| `publication.name` | 逻辑发布集名称 | 对应 `CREATE PUBLICATION dbz_publication` |
| `publication.autocreate.mode=filtered` | 自动创建 Publication | 只包含 `table.include.list` 中指定的表 |
| `slot.name` | Replication Slot 名称 | 自定义名称，便于在 PG 中 `SELECT * FROM pg_replication_slots` 排查 |

### 步骤3：验证 PG CDC 数据流

```bash
# 验证快照数据
docker exec kafka kafka-console-consumer --bootstrap-server localhost:9092 \
  --topic pg_users.public.users --from-beginning --max-messages 3
# 预期：3 条 users 表的快照数据，op="r"

# 终端1：启动实时消费
docker exec kafka kafka-console-consumer --bootstrap-server localhost:9092 \
  --topic pg_users.public.users

# 终端2：在 PG 中执行 DML
docker exec -it postgres psql -U postgres -d user_profile

-- INSERT
INSERT INTO users (username, email, phone, preferences)
VALUES ('david', 'david@example.com', '13800004444', '{"theme":"auto","lang":"fr"}');

-- UPDATE
UPDATE users SET email='bob_new@example.com', preferences = preferences || '{"beta":true}'::jsonb
WHERE username = 'bob';

-- DELETE
DELETE FROM users WHERE username = 'david';

-- JSONB 字段的部分更新
UPDATE users SET preferences = jsonb_set(preferences, '{theme}', '"system"') WHERE username = 'alice';
\q
```

**终端1 预期输出**：每条 DML 操作对应一条 Kafka 消息，注意 JSONB 字段在 Change Event 中的格式——以字符串形式呈现，内容是 JSON 对象的字符串表示。

### 步骤4：监控 Replication Slot 状态（运维关键）

```bash
# 查看 PG 中的 Replication Slot
docker exec postgres psql -U postgres -d user_profile -c "
SELECT slot_name, plugin, slot_type, database, active, restart_lsn, confirmed_flush_lsn
FROM pg_replication_slots;
"
# 预期输出：
# slot_name: debezium_users_slot
# active: t (true = Connector 正在消费)
# restart_lsn: 位点值

# 如果 Connector 停止了，观察 active 变为 f，但 restart_lsn 不变
# 这意味着 WAL 没有被清理，磁盘可能正在膨胀！

# 查看 Publication 状态
docker exec postgres psql -U postgres -d user_profile -c "
SELECT * FROM pg_publication;
SELECT * FROM pg_publication_tables;
"
# 确认 dbz_publication 包含了 public.users
```

### 步骤5：验证 PG 的 Schema 变更处理

```bash
# 在 PG 中执行 DDL
docker exec postgres psql -U postgres -d user_profile << 'SQL'
-- 新增列
ALTER TABLE users ADD COLUMN age INT DEFAULT 0;

-- 插入一条带 age 的新数据
INSERT INTO users (username, email, age) VALUES ('eve', 'eve@example.com', 25);

-- 修改列默认值
ALTER TABLE users ALTER COLUMN age SET DEFAULT 18;
SQL

# 消费验证——观察新增 age 列后的事件 Schema 变化
docker exec kafka kafka-console-consumer --bootstrap-server localhost:9092 \
  --topic pg_users.public.users --max-messages 1
```

### 可能遇到的坑及解决方法

| 坑 | 现象 | 根因与解决 |
|----|------|-----------|
| `publication does not exist` | Connector FAILED | 手动创建 publication 或设置 `publication.autocreate.mode=filtered` |
| WAL 磁盘爆满 | PG 磁盘使用率 100% | Slot 未消费 → `SELECT pg_drop_replication_slot('slot_name')` 清理僵尸 Slot |
| `remaining connection slots are reserved` | 连接超限 | `max_wal_senders` 太小。至少设为 Connector 数 + 2 |
| JSONB 字段格式问题 | 下游反序列化失败 | JSONB 在 Change Event 中是字符串，需要下游二次解析 `JSON.parse()` |
| PG 大版本升级后 Slot 失效 | Connector 无法启动 | 大版本升级（PG 14 → 16）后 Slot 需要重建，先做全量快照恢复 |

---

## 4. 项目总结

### 优点 & 缺点（PostgreSQL Connector vs MySQL Connector）

| 维度 | PG Connector | MySQL Connector |
|------|-------------|----------------|
| 变更捕获机制 | WAL + Replication Slot | binlog（ROW format） |
| DDL 处理 | DDL 在事务内，自动追踪 | DDL 不在事务内，需 Schema History Topic |
| 锁影响 | 快照期间只持短暂 ShareLock | 快照期间可能全局读锁 |
| 插件依赖 | pgoutput（内置） | 无额外插件 |
| 版本兼容 | PG 10+ | MySQL 5.7+ |
| JSON/JSONB 支持 | 原生支持，Change Event 中为字符串 | JSON 为 TEXT 列 |
| 运维复杂度 | ★★★★☆（需管理 Slot） | ★★★☆☆（需管理 binlog 过期） |

### 适用场景

1. **用户画像实时同步**：PG 中用户表的 JSONB 偏好字段变更，实时更新推荐系统
2. **地理空间数据同步**：PG + PostGIS 的空间数据变更同步到下游（Debezium 原生支持 geometry 类型）
3. **多租户 SaaS**：PG 的 Schema 隔离（每个租户一个 Schema）+ Debezium 的 Topic 路由
4. **审计合规**：PG 的 WAL 级别日志是天然的审计数据源
5. **微服务数据解耦**：PG 的用户服务数据实时流转到订单/支付等下游服务

### 注意事项

- **WAL 膨胀是 PG Connector 的第一大运维风险**。建议监控 `pg_stat_replication` 视图，设置 WAL 磁盘用量告警（> 80%）
- **`max_replication_slots` 至少为 Connector 数 + 2**。2 个 buffer：1 个给 pg_basebackup，1 个给其他逻辑复制工具
- **`max_wal_senders` 至少等于 max_replication_slots**，每个 active Slot 占用一个 WAL sender 进程

### 思考题

1. 如果 PG 的 `max_wal_senders` 设置为 5，但已经有 3 个 active Replication Slot 和 2 个 pg_basebackup 连接，此时再新建一个 Debezium Connector 会怎样？应该怎么预防这种情况？

2. 在 PG 中，如果一个 Replication Slot 的 `active` 为 false 但 Connector 报告状态为 RUNNING，可能的原因是什么？如何利用 `pg_stat_replication` 视图排查？

**（第7章思考题答案）**

1. 使用 ContentBasedRouter 或两个独立的 RegexRouter transform：
```json
{
  "transforms": "route",
  "transforms.route.type": "org.apache.kafka.connect.transforms.RegexRouter",
  "transforms.route.regex": "prod\\.inventory\\.orders",
  "transforms.route.replacement": "topic.common",
  "transforms.route.regex2": "prod\\.inventory\\.payments",
  "transforms.route.replacement2": "topic.finance"
}
```

2. 如果 orders.id=100 和 payments.id=100 都在同一个 Topic 中，且下游按 id 做 Upsert 主键，会出现写入冲突：第二条记录（payments.id=100）会覆盖第一条（orders.id=100）。解决方案：① 使用复合主键 `{db}_{table}_{id}` 作为 Upsert 键；② 下游接收时基于 `source.db.table` 字段做路由，不同表写入不同目标表；③ 用 SMT 的 `ValueToKey` 提取 `source.db + source.table + id` 为分区键。

---

> **推广提示**：DBA 团队应将 PG 的 CDC 前置配置（wal_level=logical、max_replication_slots、Publication 创建）模板化为 Ansible Playbook，在新建 PG 实例时自动完成。运维团队应建立 WAL 膨胀告警规则，并通过 Grafana 大盘实时展示所有 Replication Slot 的位点延迟。
