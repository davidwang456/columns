# 第22章：Worker分组与多租户资源隔离

> **定位**：从"共享集群"到"物理隔离"——用Worker分组、租户、YARN队列三层防线，为大麦网四个部门构建独立且弹性的计算与数据隔离体系。
> **核心内容**：Worker分组标签配置、租户-Linux用户-队列三方映射、Master调度算法与分组匹配逻辑、HDFS数据目录权限隔离、YARN容量调度器队列配额、任务级与工作流级分组优先级。
> **实战目标**：为大麦网财务部、市场部、工程部、产品部搭建物理隔离的调度环境，实现"财务的工作流跑在财务的机器上，市场的任务读不到财务的HDFS数据，月底扎堆时财务队列有40%的集群算力保证"。

---

## 1. 项目背景

大麦网数据平台部支撑着四个业务线——财务部（财务核算、成本归集、税务报表）、市场部（用户画像、实时漏斗、广告投放效果归因）、工程部（数据管道、ETL清洗、基础维度表产出）和产品部（A/B实验分析、功能埋点数据计算）。四部门加起来每天有超过300条调度任务跑在同一个DolphinScheduler集群上，共享了6台Worker节点。

头三个月相安无事。直到上个月连续出了四起事故，才把"资源混跑"的隐患彻底暴露出来。第一起：财务部在月末发起了10个大型Spark聚合任务做利润结转，每个任务占用大量CPU，直接把6台Worker全部打满——市场部的实时广告点击率报表延迟了整整两个小时，运营投手对着过时的数据烧掉了八万块预算。第二起：市场部一个实习生在Python脚本里写了死循环`while True: os.system("dd if=/dev/zero of=/tmp/bomb bs=1M count=50000")`，直接撑爆了其中一台Worker的磁盘，而财务部的关键ETL任务刚好排到这台Worker上——全部失败。第三起：工程部在测试环境调试一个新数据管道，由于所有Worker节点都用相同的`dolphinscheduler` Linux用户身份执行任务，测试脚本误读了HDFS上`/data/finance/profit_summary`的生产数据，随即将一份错误的分析报告发到了部门邮件组——质量审计追溯时才发现数据源根本不是测试库。第四起：月底财务关键报表期间，YARN集群被市场和产品的准实时任务抢占，财务的Spark SQL任务在YARN队列里排了40分钟才分到Container——而此时已过了财报的提交窗口。

CTO在复盘会上下了死命令："部门之间的Worker必须物理隔离——财务和市场的任务不能跑在同一台物理机上。每个部门的HDFS数据目录必须做到权限不可见。而且，忙时财务必须保证有40%的集群算力兜底，不能被其他人抢占。"

——这恰好是Worker分组 + 租户 + YARN队列三层隔离体系的标准应用场景。

---

## 2. 项目设计——剧本式交锋对话

下午三点，小胖端着一桶炸鸡从茶水间溜达回来，看到大师和小白正在白板上画着什么。

**小胖**（往白板上一指）："这还不简单！给每个部门单独装一套DolphinScheduler不就行了？财务一套、市场一套、工程一套、产品一套，四套集群，彻底物理隔离！炸鸡都能分桶吃，集群为啥不能分套装？"

**小白**（放下手里的《分布式系统设计》，皱眉）："胖哥你是认真的吗？四套DS意味着四套Master、四套API Server、四套Alert Server、四个MySQL元数据库、四个ZK集群——光运维成本你算过吗？而且财务和市场共用的公共维度表谁来产出？工程部的基础管道产出的ODS层数据，财务和市场的报表都用得上——如果四套集群之间没有数据共享渠道，难道每个部门自己再跑一遍全链路ETL？"

**大师**从白板上拿起记号笔，在"四套集群"上打了个大大的叉："胖胖的思路有一点是对的——隔离。但用四套集群做隔离，相当于为了四家公司各盖一栋楼，每栋楼配一套独立的电梯、空调、门禁系统。而DS的多租户架构更像是**一栋写字楼里按楼层出租**——大家都在一栋楼里（共享Master、API、Alert），但每个公司（部门）有自己独立的办公室（Worker分组），办公室门上装着独立的门禁卡（租户权限）。公共区域比如大堂和电梯（Default Worker组）大家都能用，但财务部的核心资料锁在财务部办公室的保险柜里（HDFS权限隔离），市场部的人连门都进不去。"

**技术映射**：一套DolphinScheduler集群 = 一栋共享写字楼；Worker分组 = 各公司独占的楼层/办公室；租户 = 门禁卡系统（决定你能进哪些门、能打开哪些柜子）；YARN队列 = 大楼配电配额（每家保证基础供电，忙时共享剩余电力但不得超过上限）。

**小胖**（似懂非懂）："那我能不能让一台Worker同时属于两个分组？比如worker-01既属于Finance_WG，又属于Default？这样平时财务不用的时候，其他部门还能借用一下财务的机器——别浪费嘛！"

**大师**点头："这正是DS分组设计的巧妙之处。一个Worker可以在配置文件中声明多个分组标签——用英文逗号分隔就行。`worker.groups=default,finance`表示这台Worker既接收打上`default`标签的任务，也接收打上`finance`标签的任务。这就像共享会议室——财务部平时开着门，其他部门没位置开会时可以借用；但财务月结期间大门一关，只有财务的人能进。"

**小白**抓住关键点追问："那问题的核心就变成了——一个任务怎么知道它应该被派到哪个Worker分组？工作流里怎么指定分组？如果一个工作流设置了Finance_WG，但其中某个Spark任务需要用到带GPU的节点，能不能让这个任务单独覆盖成GPU_WG？"

**大师**在白板上画了一个任务派发决策树：

```
任务派发流程：
┌─────────────────────┐
│ 1. 读取任务配置     │
│ 是否指定了Worker组?  │
└──────┬──────────────┘
       │ 有 → 使用任务级Worker分组（最高优先级）
       │ 无 → 继续下一步
┌──────┴──────────────┐
│ 2. 读取工作流定义   │
│ 是否指定了Worker组?  │
└──────┬──────────────┘
       │ 有 → 使用工作流级Worker分组
       │ 无 → 继续下一步
┌──────┴──────────────┐
│ 3. 使用Default组    │
│ 包含所有Worker       │
└─────────────────────┘
```

"优先级：**任务级 > 工作流级 > 默认组**。默认组`default`是一个特殊的存在——所有Worker在启动时，如果没有显式声明任何分组，DS会自动将其归入`default`组。而且即使Worker显式声明了其他分组，也建议把`default`加上，确保它们能接收那些没有指定分组要求的工作流。"

**技术映射**：分组粒度的三级优先级就像公司内部的设备预约机制——你个人的优先级（任务级）最高，你所在部门的预约（工作流级）次之，公司公共服务（Default组）最低。

**小白**继续深挖："那Master具体是怎么做调度决策的？假设一个任务打上了`Finance_WG`标签，Master需要做哪几步操作？这里面有没有可能因为分组名写错而导致任务永远分配不出去？"

**大师**放下笔，拿出手机给小白看了一张流程图："Master调度核心逻辑分四步走。第一步，从任务定义里读Worker分组名，比如`Finance_WG`。第二步，从ZK的Worker注册信息里拉出所有打上了`finance`分组标签的Worker列表——注意这里有个关键细节，UI上创建的分组名`Finance_WG`只是一个显示名称，真正起作用的是Worker配置文件中`worker.groups`后面的标签值。如果配置里写的是`finance`但UI分组管理页面关联的是`Finance_WG`——那任务提交时会一直卡在'已提交'状态，因为系统找不到任何Worker的标签能匹配上这个分组名。第三步，在这群Worker中用负载均衡算法（默认是最少任务数优先）选出一台最空闲的Worker。第四步，通过RPC把任务执行命令下发给这台Worker——命令里包含了租户信息、队列信息、资源文件路径。"

**小胖**突然举手："等等！大师你刚才提到队列——是不是YARN那个队列？队列和Worker分组到底什么关系？我感觉它们混在一起了。"

**大师**换了一支红笔，在白板上画了一个三维坐标系：

```
         ┌──────────────────────────────────┐
         │  Z轴：租户（数据权限维度）         │
         │  finance_tenant → Linux:finance   │
         │  marketing_tenant → Linux:marketing│
         │  决定：以谁的身份读写HDFS/Hive     │
         ├──────────────────────────────────┤
    ┌────┤  Y轴：队列（资源配额维度）         │
    │    │  finance_queue → YARN root.finance │
    │    │  marketing_queue → YARN root.marketing│
    │    │  决定：任务能占用多少CPU/内存       │
    │    ├──────────────────────────────────┤
    │    │  X轴：Worker分组（物理节点维度）    │
    │    │  Finance_WG → worker-01/02/05      │
    │    │  Marketing_WG → worker-03/04       │
    │    │  决定：任务跑在哪台物理机上         │
    └────┴──────────────────────────────────┘
```

"三个维度完全正交——你可以把财务任务绑在Finance_WG上跑（X轴物理隔离），同时限制它只能用`root.finance`队列不超过60%的集群资源（Y轴资源隔离），并且以`finance_user`身份只在`/data/finance/`目录下读写数据（Z轴权限隔离）。这三层每一个都可以独立配置、自由组合。"

**小胖**最后问道："那如果市场营销那边的YARN队列满了，他们的任务会直接失败还是排队？"

**大师**："YARN容量调度器的策略是——如果在`root.marketing`队列的容量上限内（比如30%），新任务直接占用Container开始跑；如果队列已用到30%且集群整体有空闲资源，可以弹性借用最多到50%（`maximum-capacity`）；但如果此刻集群资源被其他队列占满，任务就会在YARN端排队等待——DS这边看到的状态是'正在运行'，但实际上Worker已经把任务提交给了YARN，YARN在排队。等过了一定期限还没分到Container，任务才会超时报错。这就是为什么建议在DS这边也要配上**任务超时告警**——任务一直处于'运行中'状态超过2小时就该触发通知了。"

---

## 3. 项目实战

### 环境准备

- DolphinScheduler 3.x 集群已部署，Master × 2、Worker × 5（worker-01 ~ worker-05）、API Server × 1
- 所有Worker节点操作系统均为CentOS 7+，已配置HDFS客户端
- YARN集群已开启容量调度器（Capacity Scheduler），ResourceManager Web UI可访问（默认端口8088）
- 所有Worker节点均配置了SSH免密并从Master节点可访问
- admin账号可登录DS Web UI

### 第1步：在Worker配置文件中设置分组标签

SSH登录到每台Worker节点，编辑Worker配置文件（根据DS版本不同，可能是`worker.properties`或`application.yaml`）。以`application.yaml`为例：

```yaml
# worker-01 /opt/dolphinscheduler/conf/application.yaml
worker:
  groups: default,finance
  # 解释：worker-01 同时服务于 Default 公共池和 Finance 部门专用池

# worker-02 /opt/dolphinscheduler/conf/application.yaml
worker:
  groups: default,finance

# worker-03 /opt/dolphinscheduler/conf/application.yaml
worker:
  groups: default,marketing

# worker-04 /opt/dolphinscheduler/conf/application.yaml
worker:
  groups: default,marketing

# worker-05 /opt/dolphinscheduler/conf/application.yaml
worker:
  groups: finance
  # 解释：worker-05 仅服务 Finance 部门，连 default 都不参与
  # 用途：大促/月结期间给财务部独占的计算节点
```

修改配置文件后，逐台重启Worker使分组配置生效：

```bash
# 在Master节点上执行滚动重启——先停一台，确认任务自动转移，再启动
ssh worker-01 "cd /opt/dolphinscheduler && sh bin/dolphinscheduler-daemon.sh stop-worker.sh"
# 等待30秒，确认监控中心该Worker已离线
ssh worker-01 "cd /opt/dolphinscheduler && sh bin/dolphinscheduler-daemon.sh start-worker.sh"
# 重复上述步骤完成 worker-02 到 worker-05
```

> **踩坑提醒**：修改Worker分组后必须重启Worker进程——分组标签在Worker启动时加载到内存并注册到ZK，运行期间不会热更新。若只改配置不重启，任务仍按旧分组规则派发。

### 第2步：在DS UI中创建Worker分组

使用admin登录Web UI，进入【安全中心】→【Worker分组管理】→【创建Worker分组】。

需要创建的分组与Worker标签的对应关系：

| UI分组名称 | 对应Worker配置标签 | 包含的Worker节点 | 用途说明 |
|-----------|-------------------|-----------------|---------|
| `Finance_WG` | `finance` | worker-01, worker-02, worker-05 | 财务部专用任务执行 |
| `Marketing_WG` | `marketing` | worker-03, worker-04 | 市场部专用任务执行 |
| `Default` | `default` | worker-01, worker-02, worker-03, worker-04 | 系统自动创建，无需手动管理 |

关键细节：UI中的"Worker分组名称"（如`Finance_WG`）仅作为管理面的显示名称，与实际标签匹配的是Worker配置文件中的`worker.groups`字段值（如`finance`）。两个名称可以不同，但强烈建议保持一致以减少沟通成本。

### 第3步：配置YARN容量调度器队列

在YARN ResourceManager所在节点的`capacity-scheduler.xml`中添加部门级队列配置：

```xml
<!-- $HADOOP_CONF_DIR/capacity-scheduler.xml -->
<configuration>
    <!-- root队列下的子队列定义 -->
    <property>
        <name>yarn.scheduler.capacity.root.queues</name>
        <value>default,finance,marketing</value>
    </property>

    <!-- 财务队列：保证40%容量，弹性上限60% -->
    <property>
        <name>yarn.scheduler.capacity.root.finance.capacity</name>
        <value>40</value>
    </property>
    <property>
        <name>yarn.scheduler.capacity.root.finance.maximum-capacity</name>
        <value>60</value>
    </property>

    <!-- 市场队列：保证30%容量，弹性上限50% -->
    <property>
        <name>yarn.scheduler.capacity.root.marketing.capacity</name>
        <value>30</value>
    </property>
    <property>
        <name>yarn.scheduler.capacity.root.marketing.maximum-capacity</name>
        <value>50</value>
    </property>

    <!-- 默认队列：保证30%容量（工程部、产品部共用） -->
    <property>
        <name>yarn.scheduler.capacity.root.default.capacity</name>
        <value>30</value>
    </property>
    <property>
        <name>yarn.scheduler.capacity.root.default.maximum-capacity</name>
        <value>100</value>
    </property>
</configuration>
```

刷新YARN队列配置（无需重启集群）：

```bash
yarn rmadmin -refreshQueues
# 验证队列生效
yarn queue -showacls root.finance
```

### 第4步：在DS中创建队列并映射YARN队列

进入【安全中心】→【队列管理】→【创建队列】：

| DS队列名称 | 队列值（YARN路径） | 描述 |
|-----------|-------------------|------|
| `finance_queue` | `root.finance` | 财务部YARN资源队列 |
| `marketing_queue` | `root.marketing` | 市场部YARN资源队列 |
| `default` | `root.default` | 默认队列（系统预置） |

DS队列管理页面中的"队列名称"是任务和工作流引用的标识，"队列值"才是真正传递给YARN ResourceManager的队列路径。两者可以不同，但务必确保"队列值"与YARN集群中的实际队列路径完全匹配——包括大小写。

### 第5步：创建租户并绑定Linux用户

首先在每台Worker节点上创建Linux系统用户（SSH登录到worker-01 ~ worker-05逐一执行）：

```bash
# 每台Worker节点均需执行以下命令
sudo useradd -m finance_etl
sudo useradd -m marketing_etl

# 验证用户创建成功
id finance_etl && id marketing_etl
# 输出应包含: uid=1001(finance_etl) gid=1001(finance_etl)
```

设置HDFS数据目录权限（在任意HDFS客户端节点，以hdfs超级用户执行）：

```bash
# 创建各部门专属数据目录
hdfs dfs -mkdir -p /data/finance
hdfs dfs -mkdir -p /data/marketing

# 设置目录所有权和权限——750表示owner可读写执行，同组可读执行，其他用户无任何权限
hdfs dfs -chown finance_etl:finance_group /data/finance
hdfs dfs -chmod 750 /data/finance

hdfs dfs -chown marketing_etl:marketing_group /data/marketing
hdfs dfs -chmod 750 /data/marketing

# 验证权限隔离
sudo -u finance_etl hdfs dfs -touchz /data/finance/test.txt   # 应成功
sudo -u marketing_etl hdfs dfs -cat /data/finance/test.txt    # 应报错: Permission denied
```

然后在DS UI中创建租户，进入【安全中心】→【租户管理】→【创建租户】：

| 租户名称 | Linux用户 | 队列 | 描述 |
|---------|----------|------|------|
| `finance_tenant` | `finance_etl` | `finance_queue` | 财务部租户 |
| `marketing_tenant` | `marketing_etl` | `marketing_queue` | 市场部租户 |

> **安全要诀**：创建租户前务必确认"Linux用户"已经在所有Worker节点上存在。如果某台Worker上缺少对应系统用户，任务被派发到该Worker后，`sudo -u finance_etl`会直接报`unknown user`错误，且该错误不会在DS UI的任务日志中明确提示"用户不存在"——只会显示笼统的"执行失败"。排查时需登录到任务所在的Worker节点，查看`/opt/dolphinscheduler/logs/`下的Worker日志定位具体原因。

### 第6步：创建工作流并绑定Worker分组与租户

以财务部"月度结账"工作流为例，在DS Web UI中创建：

```
工作流名称：monthly_close
Worker分组：Finance_WG       ← 所有任务默认跑在Finance组Worker上
租户：finance_tenant          ← 所有任务以finance_etl Linux用户身份执行
队列：finance_queue           ← 所有Spark/Flink任务提交到root.finance队列
```

市场部"营销活动分析"工作流配置：

```
工作流名称：campaign_analysis
Worker分组：Marketing_WG
租户：marketing_tenant
队列：marketing_queue
```

创建时将工作流上线并运行，验证隔离效果：

```bash
# 验证1：财务工作流的任务应只运行在 worker-01/02/05 上
# 在DS UI工作流实例详情页查看"执行主机"列

# 验证2：以finance_etl身份在Worker上执行 HDFS 读写测试
# 登录到 worker-01，模拟任务执行环境
sudo -u finance_etl hdfs dfs -put /tmp/daily_sales.csv /data/finance/
# 应成功

sudo -u finance_etl hdfs dfs -cat /data/marketing/campaign_log.csv
# 应返回: cat: Permission denied

# 验证3：同时提交10个财务Spark任务，观察YARN队列资源分配
yarn application -list | grep -c "root.finance"
# 所有财务任务在root.finance队列中，不挤占root.marketing的配额
```

### 第7步：任务级Worker分组覆盖配置

在一个Worker分组为`Default`的工作流中，某个Spark任务需要使用GPU计算资源：

```
工作流：data_pipeline
  ├── Shell任务：数据预处理（Worker分组：继承工作流 → Default）
  ├── SQL任务：聚合计算（Worker分组：继承工作流 → Default）
  └── Spark任务：深度学习模型推理 ★
       └── 任务级Worker分组：GPU_WG ← 覆盖工作流级别的Default分组
```

配置方法：在工作流设计器中，双击Spark任务节点 → 在"Worker分组"下拉框中选择`GPU_WG`（需提前在安全中心创建好）。此任务将无视工作流级别的`Default`分组设定，优先派发到打有`gpu`标签的Worker节点上执行。

### 第8步：动态新增Worker节点到现有分组

月结前夕，财务部需临时增加计算节点。新采购一台带GPU的服务器作为worker-06：

```bash
# 1. 安装JDK/HDFS客户端，创建finance_etl系统用户
sudo useradd -m finance_etl

# 2. 配置Worker分组标签——仅服务于财务部
# worker-06 /opt/dolphinscheduler/conf/application.yaml
worker:
  groups: finance
  # 不包含default标签，月结期间只跑财务任务

# 3. 启动Worker——自动向ZK注册
cd /opt/dolphinscheduler && sh bin/dolphinscheduler-daemon.sh start-worker.sh

# 4. 验证注册成功
# 在ZK中检查
zkCli.sh ls /dolphinscheduler/worker
# 应出现 worker-06 的临时节点

# 在DS UI监控中心确认 worker-06 状态为绿色"在线"
# 提交一个finance分组任务，确认能分配到worker-06
```

月结结束后，如需将worker-06释放给其他部门使用，修改`worker.groups=default,finance`并重启Worker即可。

### 第9步：监控Worker分组负载

```bash
# 通过DS API查询Worker列表及分组信息
curl -H "token: ${DS_ADMIN_TOKEN}" \
     http://api-server:12345/dolphinscheduler/monitor/workers

# 返回示例（简化）：
# [
#   {"host":"192.168.1.101","port":1234,"state":"RUNNING","workerGroup":"default,finance"},
#   {"host":"192.168.1.103","port":1234,"state":"RUNNING","workerGroup":"default,marketing"},
#   {"host":"192.168.1.105","port":1234,"state":"RUNNING","workerGroup":"finance"}
# ]

# 查看YARN队列资源使用情况
yarn queue -status root.finance
# 输出示例：
# Queue Name : root.finance
# Capacity : 40.0%
# Maximum Capacity : 60.0%
# Current Capacity : 35.2%   ← 当前占用
# Pending Containers : 3     ← 3个Container在排队等待
```

### 第10步：常见踩坑与排查指南

**坑1：Worker分组名不匹配导致任务永远"已提交"。** DS UI中创建分组名`Finance_WG`，Worker配置里写`worker.groups:finance`，但在UI分组管理中未将两者正确关联。任务提交后，Master查找`Finance_WG`分组下的Worker列表为空，任务卡在"已提交"状态不往下走。**排查**：进入Worker分组管理页面，确认分组内的Worker节点列表非空；进入监控中心确认Worker的心跳状态正常。

**坑2：忘记在所有Worker节点上创建Linux用户。** `finance_etl`存在于worker-01和worker-02上，但新增的worker-05上没有此用户。任务被调度到worker-05后报错退出。**解决**：维护一份"节点-用户"对照清单，新增Worker节点时通过Ansible脚本批量执行`useradd`，并在部署脚本中加入预检步骤——`id finance_etl || (echo "ERROR: user finance_etl not found" && exit 1)`。

**坑3：过度隔离导致资源浪费。** 产品部只有2条日常调度任务，却独占了一台Worker；而市场部6台Worker忙不过来时，产品部那台Worker一直空闲。**原则**：每个Worker组至少保证3台以上Worker（兼顾容错和负载均衡），对任务量少的部门合并到Default组加上队列配额做软隔离即可。

---

## 4. 项目总结

### 三层租户隔离对比总览

| 维度 | Worker分组 | YARN队列 | 租户 |
|------|-----------|---------|------|
| 隔离层面 | 物理节点 | 计算资源（CPU/内存） | 数据权限（HDFS/Hive/本地） |
| 配置位置 | Worker配置文件 + DS UI | YARN capacity-scheduler.xml + DS队列管理 | Linux useradd + DS租户管理 |
| 隔离粒度 | 部门/业务线级别 | 部门/项目级别 | 部门/用户级别 |
| 是否可动态调整 | 改配置+重启Worker生效 | `yarn rmadmin -refreshQueues`即时生效 | 新增租户即时生效，但需所有Worker上有对应用户 |
| 典型问题 | 分组名不匹配导致任务卡住 | 队列容量配置不合理导致饥饿 | 忘记某台Worker上创建Linux用户 |
| 适用规模 | 3台以上Worker/组 | 不限制 | 不限制 |

### 何时隔离 vs 何时共享

| 场景 | 推荐策略 | 原因 |
|------|---------|------|
| 财务核心任务 vs 其他部门 | **Worker分组物理隔离** | 数据敏感性极高，不能被其他部门的失控任务拖累 |
| 市场A/B分析 vs 产品埋点计算 | **共享Default组+YARN队列软隔离** | 非核心任务，物理隔离成本过高 |
| 临时性的大数据量回刷任务 | **任务级Worker组覆盖** | 只让这批任务跑在扩容节点上，不影响常驻任务 |
| 开发测试环境 vs 生产环境 | **完全物理隔离** | 推荐独立DS集群而非仅分组，防止误操作污染生产数据 |
| GPU深度学习任务 | **任务级GPU_WG覆盖** | GPU Worker成本高，通过分组集中管理而非每个部门配一台 |

### 容量规划速查

假设集群总Worker数 = N，各部门每日平均任务数 = T，单任务平均执行时间 = D分钟：

- 每组最少Worker数 = max(3, ceil(T × D / (每个Worker并发槽位数 × 60 × 忙时系数)))
- 忙时系数建议取值1.5～2.0
- YARN队列保证容量 ≥ 该部门任务峰值CPU/总集群CPU × 100%
- 弹性上限建议设为保证容量的1.5倍

### 适用场景

- **多部门共享同一DS集群**：需明确隔离计算与数据边界的中大型组织
- **头部数据任务需独占资源**：如财务月结、双11大屏推送等SLA要求高的场景
- **GPU等异构硬件需按需分配**：通过任务级分组覆盖将特殊任务路由到特定硬件池
- **开发/测试/预发环境共用物理集群**：通过Worker分组做节点级隔离，YARN队列做资源级隔离

### 不适用场景（反模式）

- **每组Worker少于3台**：一台宕机、一台维护，只剩一台扛全部任务——分组的容错收益被"资源碎片化"的损耗抵消。此时应共享Default组 + 队列配额做软隔离。
- **部门间数据100%共享且无权限顾虑**：如一个数据团队内部分为"ETL组"和"BI组"但数据完全互通——过度隔离只会增加管理和排查复杂度。

### 思考题

1. 市场部某天凌晨的任务因YARN队列资源紧张而在Worker上排队等待。此时Worker本身CPU空闲（因为任务还没分到Container），财务部的Worker组负载不高。请问：能否通过调整DS的调度策略，让Master在检测到目标Worker组的YARN队列满时将任务自动"溢出"到其他Worker组执行？如果可以实现，需要改动现有的哪几层配置？如果不可行，为什么？

2. 假设财务部需要新增一个Hive SQL定时报表，读取Hive表`ods.finance_daily`。这个Hive表的底层HDFS路径是`/warehouse/hive/ods.db/finance_daily`，owner为`hive:hadoop`，权限为`rwxr-xr-x`。而`finance_etl`用户属于`finance_group`组。请问：finance_etl能否通过Hive JDBC正常读取这张表？如果不行，需要在HDFS层还是Hive层做什么权限调整？请给出具体操作命令。

---

> **下一章预告**：第23章《告警升级与自动故障恢复》将讲解如何构建"任务失败→自动重试→重试仍失败→钉钉/企微告警→人工介入"的多级响应链路，并结合Worker心跳监控实现自动摘除故障节点——让凌晨三点的生产事故在你还未醒来时就被系统自动处理。

---

> **本章关键词**：Worker分组、多租户、资源隔离、YARN容量调度器、HDFS权限隔离、Master调度算法、任务级分组覆盖、物理节点隔离、算力配额、租户映射
