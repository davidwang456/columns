# 第17章：分布式架构——多Executor部署

## 1. 项目背景

### 业务场景

数据平台组将Azkaban推广到全公司后，情况发生了变化：

- 原来只有20个ETL任务，现在增加到200个
- 原来凌晨2点触发一批任务，现在每小时都有任务触发
- 原来所有Job都是轻量Shell脚本，现在有Spark任务占用Executor线程长达1小时

单Executor模式下，一个Spark Job占用一个线程，后续所有Job都需要排队等待。凌晨2点触发的批处理，等到早上8点还没跑完。团队意识到：必须从单机版升级到多Executor分布式架构。

### 痛点放大

单Executor模式下的瓶颈：

1. **线程池耗尽**：默认20个线程，第21个Job只能排队等待
2. **单点故障**：Executor挂了，所有运行中的Job全部中断
3. **资源不隔离**：一个Spark Driver运行在Executor进程中，消耗大量内存导致其他Job OOM
4. **无法水平扩展**：加更多服务器也无法提升吞吐量

## 2. 项目设计——剧本式交锋对话

**小胖**（抓狂地看着执行排队列表）：大师，现在凌晨3点，有50个Job在排队等执行，Executor的20个线程全被Spark任务占着呢。单机版顶不住了！

**大师**：这就是单Executor模式的阿喀琉斯之踵——所有Job在一个JVM进程中运行，线程池就是它的物理极限。解决办法是升级到多Executor架构。

**小白**：多Executor架构怎么工作？是像微服务那样，Web Server和Executor分开部署吗？

**大师**：没错。Azkaban的分布式架构核心思想是——**Web Server负责调度和管理，Executor负责执行，两者通过网络通信**。

```
┌──────────────┐     ┌──────────────────────────────────┐
│   Nginx LB   │     │         MySQL (元数据库)          │
└──────┬───────┘     └──────────────────────────────────┘
       │                          │
  ┌────┴────┐               ┌────┴────────┐
  │         │               │             │
  ▼         ▼               ▼             ▼
┌──────┐ ┌──────┐     ┌──────────┐ ┌──────────┐
│Web-1 │ │Web-2 │     │Executor-1│ │Executor-2│  ...
│ 8081  │ │ 8081  │     │ 12321 │   │ 12321 │
└──┬───┘ └──┬───┘     └────┬─────┘ └────┬─────┘
   │        │               │            │
   └────────┴───────────────┴────────────┘
                    RPC通信
```

**小胖**：那Web Server怎么决定把Job发给哪个Executor？

**大师**：Azkaban使用"轮询 + 容量感知"的分配策略。Web Server会：
1. 维护所有Executor的注册信息（心跳检测）
2. 分配Flow时，选择当前负载最低的Executor
3. 如果Executor宕机或心跳超时，将其标记为不可用，已分配的Job重新调度

**小白**：那Job是怎么在Executor之间分发的？是Web发指令还是Executor主动拉取？

**大师**：Azkaban用的是**Push模式**——Web Server主动将Flow分配给Executor。具体流程是：

```
1. Web收到执行请求
2. Web查询所有健康的Executor列表
3. Web计算每个Executor的剩余容量（总线程数 - 当前运行Job数）
4. Web选择容量最大的Executor，RPC调用 executeFlow()
5. Executor下载Flow配置 → 启动JobRunner → 执行Job
6. Job执行期间，Executor定期上报状态给Web Server
```

这种模式的好处是Web Server能掌握全局调度视角，不足是如果RPC通信中断，Web Server无法感知Executor的真实负载。

**小胖**：那我需要准备几台机器？部署起来复杂吗？

**大师**：最小化部署至少需要3台机器：1台Web Server + 2台Executor + 1台MySQL。MySQL可以共用。生产中推荐：2台Web（Nginx负载均衡）+ 3台Executor + MySQL主从。

部署流程比较简单：编译出Web Server和Executor Server两个包，分别部署到不同机器，然后修改配置让它们互相发现。

### 技术映射总结

- **Web Server** = 总调度室（接收任务请求，分配给空闲的工人）
- **Executor** = 工人（接收指令，往车间走，完成任务后汇报）
- **心跳检测** = 打卡机（每隔一段时间，工人都要打卡证明自己在岗）
- **RPC通信** = 对讲机（调度室和工人之间的即时通信通道）

## 3. 项目实战

### 3.1 环境准备

| 组件 | 数量/版本 | 用途 |
|------|----------|------|
| Web Server | 1台 | 调度管理 |
| Executor Server | 2-3台 | Job执行 |
| MySQL | 5.7 | 元数据存储 |
| Nginx | 1.x | Web Server的LB（可选） |

### 3.2 分步实现

#### 步骤1：编译Web Server和Executor Server

**目标**：分别编译出Web和Executor的部署包。

```bash
# 编译Web Server
./gradlew :azkaban-web-server:build installDist -x test
# 产出: azkaban-web-server/build/install/azkaban-web-server/

# 编译Executor Server
./gradlew :azkaban-exec-server:build installDist -x test
# 产出: azkaban-exec-server/build/install/azkaban-exec-server/
```

#### 步骤2：配置MySQL数据库

**目标**：为多Executor模式准备共享的MySQL元数据库。

```sql
-- 创建数据库和用户
CREATE DATABASE azkaban DEFAULT CHARACTER SET utf8mb4;
CREATE USER 'azkaban'@'%' IDENTIFIED BY 'azkaban_prod_pass';
GRANT ALL PRIVILEGES ON azkaban.* TO 'azkaban'@'%';
FLUSH PRIVILEGES;

-- 初始化表结构（Azkaban启动时会自动创建，也可以手动导入）
-- source azkaban-db/sql/create-all-sql-0.1.0-SNAPSHOT.sql
```

#### 步骤3：配置Web Server

**web-server/conf/azkaban.properties**：

```properties
# ===== 基础配置 =====
azkaban.name=Azkaban-Prod
azkaban.label=My Azkaban Server
default.timezone.id=Asia/Shanghai
jetty.use.ssl=false
jetty.port=8081

# ===== 数据库配置（MySQL） =====
database.type=mysql
mysql.port=3306
mysql.host=192.168.1.50
mysql.database=azkaban
mysql.user=azkaban
mysql.password=azkaban_prod_pass
mysql.numconnections=100

# ===== 执行器配置 =====
azkaban.use.multiple.executors=true
azkaban.executorselector.filters=StaticRemainingFlowSize,CpuStatus
azkaban.executorselector.comparator.NumberOfAssignedFlowComparator=1
azkaban.executorselector.comparator.Memory=1
azkaban.executorselector.comparator.LastDispatched=1
azkaban.executorselector.comparator.CpuUsage=1

# ===== 邮箱配置 =====
mail.sender=azkaban-alert@company.com
mail.host=smtp.company.com
mail.port=25

# ===== Web Server自身配置 =====
web.resource.dir=./web/
azkaban.web.temp.dir=./temp/

# ===== 多Web实例（可选HA） =====
azkaban.should.verify.active.server=false

# ===== 用户管理 =====
user.manager.class=azkaban.user.XmlUserManager
user.manager.xml.file=conf/azkaban-users.xml

# ===== 执行器管理 =====
executor.port=12321
azkaban.executor.max.failurecount=3
azkaban.executor.ping.interval=30000
azkaban.executor.health.check.interval=60000
```

#### 步骤4：配置Executor Server

**exec-server/conf/azkaban.properties**：

```properties
# ===== 基础配置 =====
azkaban.name=Azkaban-Executor-1
default.timezone.id=Asia/Shanghai

# ===== 数据库配置（与Web相同） =====
database.type=mysql
mysql.port=3306
mysql.host=192.168.1.50
mysql.database=azkaban
mysql.user=azkaban
mysql.password=azkaban_prod_pass
mysql.numconnections=50

# ===== 执行器配置 =====
executor.port=12321
executor.max.threads=50
executor.flow.threads=30
executor.flow.max.running=30

# ===== JVM配置 =====
executor.jvm.args=-Xmx8g -Xms4g -XX:+UseG1GC -XX:MaxGCPauseMillis=200

# ===== 日志 =====
azkaban.log.retention.days=30

# ===== 插件配置 =====
azkaban.jobtype.plugin.dir=plugins/jobtypes
```

#### 步骤5：启动多Executor集群

**目标**：按正确顺序启动所有组件。

```bash
#!/bin/bash
# start_cluster.sh —— 集群启动脚本

echo "=== Azkaban 集群启动 ==="

# 1. 确保MySQL已启动
echo "[1/4] 检查MySQL..."
mysql -h 192.168.1.50 -u azkaban -p'azkaban_prod_pass' -e "SELECT 1" > /dev/null 2>&1
if [ $? -ne 0 ]; then
    echo "MySQL不可达！请先启动MySQL服务"
    exit 1
fi
echo "   ✓ MySQL正常"

# 2. 启动Executor Server（先启动Executor，Web再注册它们）
echo "[2/4] 启动Executor-1..."
ssh exec-01 "cd /opt/azkaban-exec && bin/start-exec.sh"
echo "[2/4] 启动Executor-2..."
ssh exec-02 "cd /opt/azkaban-exec && bin/start-exec.sh"

# 等待Executor注册
sleep 10

# 激活Executor（状态：NEW → ACTIVE）
echo "[2.5/4] 激活Executor..."
for EXEC_ID in $(curl -s "http://localhost:8081/executor?ajax=fetchallexecutors" \
  | python3 -c "import json,sys; [print(e['id']) for e in json.load(sys.stdin)['executors']]" ); do
    curl -X POST "http://localhost:8081/executor?ajax=activate&executorId=${EXEC_ID}"
    echo "  激活Executor ID: ${EXEC_ID}"
done

# 3. 启动Web Server
echo "[3/4] 启动Web Server..."
ssh web-01 "cd /opt/azkaban-web && bin/start-web.sh"

# 4. 验证集群状态
echo "[4/4] 验证集群状态..."
sleep 15
curl -s "http://web-01:8081/executor?ajax=fetchallexecutors" | python3 -c "
import json, sys
data = json.load(sys.stdin)
for e in data.get('executors', []):
    print(f\"Executor ID={e['id']} Status={e['status']} Host={e['host']}:{e['port']}\")
"

echo "=== 集群启动完成 ==="
```

#### 步骤6：验证负载分配

**目标**：观察Flow如何被分配到不同Executor。

```bash
#!/bin/bash
# verify_load_balance.sh

# 提交10个Flow，观察分配情况
for i in $(seq 1 10); do
    curl -s -b cookies.txt \
      -X POST "http://localhost:8081/executor?ajax=executeFlow" \
      --data "project=load_test&flow=test_flow_${i}"
done

# 查看各Executor负载
sleep 10
curl -s "http://localhost:8081/executor?ajax=fetchallexecutors" | python3 -c "
import json, sys
data = json.load(sys.stdin)
print('Executor 负载分布:')
for e in data.get('executors', []):
    print(f\"  Executor-{e['id']}: {e.get('runningFlows', 0)} running flows\")
"
```

#### 步骤7：Web Server高可用（可选）

**目标**：配置Nginx实现Web Server的负载均衡和HA。

```nginx
# nginx.conf
upstream azkaban_web {
    server web-01:8081 weight=1 max_fails=3 fail_timeout=30s;
    server web-02:8081 weight=1 max_fails=3 fail_timeout=30s;
    keepalive 32;
}

server {
    listen 80;
    server_name azkaban.company.com;

    location / {
        proxy_pass http://azkaban_web;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;

        # 长轮询超时设置
        proxy_read_timeout 300s;
        proxy_connect_timeout 10s;
    }
}
```

### 3.3 测试验证

```bash
#!/bin/bash
# verify_cluster.sh

echo "=== Azkaban集群验证 ==="

# 1. 检查所有Executor状态
echo "[Test 1] Executor状态检查..."
EXECUTORS=$(curl -s "http://localhost:8081/executor?ajax=fetchallexecutors")
ACTIVE_COUNT=$(echo "$EXECUTORS" | python3 -c "
import json,sys
data=json.load(sys.stdin)
active=[e for e in data.get('executors',[]) if e.get('status')=='ACTIVE']
print(len(active))
")
echo "  活跃Executor数: $ACTIVE_COUNT"
if [ "$ACTIVE_COUNT" -ge 2 ]; then
    echo "  [PASS] 至少2个Executor处于ACTIVE状态"
else
    echo "  [FAIL] 活跃Executor不足"
fi

# 2. 测试Job分发
echo "[Test 2] Job分发测试..."
for i in $(seq 1 5); do
    curl -s -b cookies.txt \
      -X POST "http://localhost:8081/executor?ajax=executeFlow" \
      --data "project=cluster_test&flow=test_${i}"
done
sleep 30
echo "  5个测试Flow已提交，请在Web界面查看分配情况"

# 3. 测试Executor故障转移
echo "[Test 3] Executor故障转移..."
# 手动停止一个Executor
# ssh exec-01 "cd /opt/azkaban-exec && bin/shutdown-exec.sh"
# 观察Web界面是否将该Executor标记为UNHEALTHY
# 观察已分配到此Executor的Flow是否重新分配

echo "=== 集群验证完成 ==="
```

## 4. 项目总结

### 架构模式对比

| 维度 | Solo Server | 多Executor | K8s + Azkaban |
|------|-----------|-----------|---------------|
| 部署复杂度 | ★☆☆ | ★★☆ | ★★★ |
| 高可用 | 无 | Web+Executor均可HA | 天然HA |
| 扩展性 | 垂直扩展 | 水平扩展 | 自动扩缩 |
| 资源隔离 | 无 | Yarn队列隔离 | Pod级别隔离 |
| 运维成本 | ★☆☆ | ★★☆ | ★★★ |

### 适用场景

- **适用**：Job数量>50的生产环境、需要资源隔离的多团队场景、需要高可用的核心数据平台
- **不适用**：个人开发测试、10人以下小团队的单项目环境

### 注意事项

- Executor的`executor.max.threads`不应超过机器的CPU核心数*2
- Web和Executor的MySQL连接池总和不能超过MySQL的`max_connections`
- Web和Executor服务器之间需要网络互通（尤其是12321端口）
- Nginx做Web Server负载均衡时必须使用`ip_hash`或`sticky session`（Azkaban的session有状态）

### 常见踩坑经验

1. **Executor注册后状态为UNHEALTHY**：通常是Executor→MySQL的连接失败。检查MySQL远程访问权限和防火墙。
2. **Web和Executor时区不一致**：Web用东八区，Executor用UTC，导致调度时间混乱。统一在`azkaban.properties`中设置`default.timezone.id`。
3. **Executor内存不均匀**：一个Executor被分配了大量Spark Driver任务，内存吃紧。解决：启用`azkaban.executorselector.filters=CpuStatus,StaticRemainingFlowSize`让Web智能选择Executor。

### 思考题

1. 如果有10个Executor，如何实现"分组调度"——让一组Executor专门处理电商团队的Job，另一组处理风控团队的Job？
2. Executor宕机后，它上面的正在运行的Flow如何迁移到其他Executor？请设计迁移方案。
