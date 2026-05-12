# 第18章：Infinispan分布式缓存深度实战

## 1 项目背景

某金融平台Keycloak集群在业务高峰期遭遇了一场"幽灵故障"——运营同事凌晨紧急为用户分配了新的风控审批角色，但直到第二天早上，该用户登录后看到的菜单仍然没有变化。运营怒气冲冲地在群里质问IT："我明明给了权限，为什么系统不生效？"与此同时，安全部门响起红色警报：一名已在下午4点被禁用的离职员工，直到晚上8点仍能正常调用内部API——因为他的Access Token虽然在5分钟后过期，但刷新令牌仍在有效期内，而缓存的授权信息没有被及时刷新，新的Token沿用了旧的权限数据。

深入排查后，问题根因全部指向Infinispan缓存层。第一层问题：**缓存域（Cache Regions）配置不当**。sessions、authenticationSessions、offlineSessions、authorization等不同缓存域被统一采用默认配置，没有根据各自的数据特性差异化设置。authorization缓存使用了默认的Distributed模式——这种模式要求数据变更时通知所有集群节点，而在金融平台上百节点的集群中，通知链路任何一环丢失（网络抖动、节点CPU过载），就会导致个别节点持有脏数据。

第二层问题：**actionToken缓存未跨节点同步**。密码重置邮件生成的actionToken默认存储在本地缓存中（Local模式），用户点击重置链接时，负载均衡可能将请求路由到不同的Keycloak节点，目标节点找不到该Token，返回"链接已过期"。这个随机性失效让运维排查了很久，一度怀疑是邮件服务丢链接。

第三层问题：**用户失效通知丢失**。管理员禁用用户后，Keycloak通过Invalidation机制向集群广播"该用户的authorization缓存需刷新"。但集群组播消息（基于JGroups）在跨越多个网络子网后被防火墙拦截，导致被禁用用户的缓存永远留在部分节点中——这就是"禁用后仍能访问"的根因。

更宏观的痛点还包括：TTL设置过大会导致前端看到的数据陈旧，过小则每次请求都要落库查询，数据库在高峰期被打出连接池耗尽告警；节点间缓存同步延迟导致的"幽灵数据"——同一用户刷新页面，时而有权限时而没有；Infinispan的序列化不兼容导致写入失败时，日志里只会出现一行含混的`CacheException`，开发者根本不知道是POJO的版本号不匹配。这些痛点若不在架构设计阶段前置解决，上线后就是一次次的紧急回滚。

---

## 2 项目设计——剧本式交锋对话

**小胖**（拖着小白走进茶水间，掏出手机画图）：大师！我昨天吃了一顿日料，突然理解缓存了。你看——日料后厨有个备菜台，师傅提前把常用食材切好摆盘放那儿：三文鱼片、金枪鱼丁、海胆军舰卷的配料。客人下单后，厨师直接从备菜台拿料上桌，不用每次从冷库取鱼现杀现切——这不就是缓存嘛！备菜台就是内存，冷库就是数据库。但我有个疑问，厨师有时候也抱怨备菜台的料和冷库的库存对不上，这不就是缓存不一致问题？Keycloak咋解决的？

**大师**（放下咖啡杯）：小胖你这顿饭吃得值，比喻精准。再往下深一层——冷库存放的是"黄金数据"（数据库中的真相），备菜台（缓存）里的每一盘食材都有有效期：切好的三文鱼超过3小时没卖出就要处理掉（TTL过期淘汰）；冷库断货了厨师就标记该菜"售罄"（缓存失效通知）；一个分店的厨师长换菜单了，总店通过内部电话通知所有分店更新菜单（集群缓存刷新）。但当总店电话打不通某个分店时，那个分店的菜单就还是旧的——这正是你们金融平台的authorization缓存问题。

**小白**（翻着手机上的Keycloak文档）：那Keycloak到底有哪些"备菜台"？我看到文档里列了一堆缓存域——sessions、authenticationSessions、offlineSessions、clientSessions、loginFailures、actionTokens、work、authorization——每个是干什么的？为什么有些用Local、有些用Distributed、还有Invalidation和Replicated？

**大师**（拉出白板画表）：

这就是Keycloak缓存架构最核心的设计。我先拆解八个核心缓存域，再讲四种拓扑模式。

**核心缓存域详解：**

| 缓存域 | 存储内容 | 数据特性 | 默认模式 |
|--------|---------|---------|---------|
| sessions | 用户会话对象（UserSession）及关联的ClientSession | 读写频繁、需跨节点共享（用户从节点1登录、下次请求可能到节点2） | Distributed |
| authenticationSessions | 认证中间态——用户输入用户名、输入密码、MFA验证等步骤的临时状态 | 生命周期短（默认5分钟）、需跨节点共享（认证过程可能跨请求路由） | Distributed |
| offlineSessions | 离线Token关联的会话数据（用户勾选"记住我"后） | 数据量大但访问低频、生命周期长（可达数月） | Distributed |
| clientSessions | 每个应用级别的会话元信息（登出URL、Token发放记录） | 与User Session强关联、数据量中等 | Distributed |
| loginFailures | 登录失败计数（防暴力破解） | 数据量小、要求高一致性（不能在节点间出现计数差异） | Local/Replicated |
| actionTokens | 密码重置、邮箱验证等一次性操作Token | 生命周期短（数分钟）、临时性强 | 建议Distributed |
| work | 任务调度中的工作项（导入用户、批量操作的后台任务） | 需分布式协调、防止重复执行 | Distributed |
| authorization | 用户权限数据（角色、组、权限映射） | 读取极高频、跨节点一致性要求高、但写入低频 | Local + Invalidation |

**四种拓扑模式对比：**

- **Local（本地模式）**：数据仅存在于本节点JVM堆内存中，不与其他节点通信。优点是零网络开销，缺点是一个节点修改数据其他节点完全不知道。仅适用于完全不关心一致性的场景——比如authorization缓存的"数据本体"存在各节点本地，但配合Invalidation机制保证变更时各节点主动淘汰旧数据。

- **Invalidation（失效模式）**：一个特殊缓存，不存储实际数据，只扮演"失效通知通道"的角色。当某节点修改了某缓存键时，它通过Infinispan集群消息告知其他节点"把这个键的缓存删掉"。本质上是一个**缓存失效广播器**，没有数据复制——也就是说每个节点有自己独立的缓存副本，失效通知只是让它们知道什么时候该舍弃旧数据。这就是authorization缓存的标配方案：读是本地读（零延迟），写时广播失效通知让所有节点淘汰旧数据。

- **Replicated（全节点复制模式）**：每个节点都持有完整的数据副本。读取延迟为零（本地即可服务），但写入必须同步到所有节点——节点越多写越慢（N-1次网络往返）。适合数据量小、读取极频繁、写入极少且要求高一致性的场景——loginFailures缓存的典型范式，因为暴力破解计数的节点间一致性是安全底线。

- **Distributed（分布式模式）**：数据被分片存储在集群的部分节点上（而非全部），每个键有固定数量的"owner"（所有者节点，通过一致性哈希确定）。`owners=2`意味着每份数据在集群中有两份副本，一份主、一份备。读取时先从本地检查（可能命中也可能需要远程读），写入时同步到所有owner。这是Keycloak的主力模式——sessions、authenticationSessions、offlineSessions默认都是此模式。

> **大师技术映射**：Local = 每家分店自己有份菜单（改了不通知别家）。Replicated = 全部门店共用同一本手册（总公司更新一版必须全国同步）。Distributed = 图书馆把书分散到不同书架（借某本书可能要去特定书架拿，但多本副本减少压力）。Invalidation = 总公司广播"旧版菜单作废"通知（不传菜单本身，只通知作废）。

**小胖**（第二轮）：那owners=2到底是什么意思？有人说"owners越多数据越安全"，有人说"owners越多越慢"——到底怎么取舍？还有L1缓存又是啥？

**大师**：owners指的是Infinispan为每个缓存键维护的数据副本数。`owners=2`即每个键数据存在于集群中的2个节点（通常是一致性哈希环上的首要owner和其下一节点）。写操作必须同步到所有owner才算成功，所以**owners每增加1，写入延迟近似乘以1.5~2（网络往返增加）**。默认值2是"单机故障容错+不过分拖慢写性能"的平衡点。如果你有10个节点且只是会话数据，owner=2足以——任一节点宕机，数据仍有备份可用。但如果把owner设为10（等于全副本），那就退化成了Replicated模式——写性能灾难。

L1缓存（L1 Cache）是Distributed模式下的本地加速层。当远程节点返回数据时，当前节点可以在本地保留一份短时间的缓存副本（默认生命周期60000ms），下次读取同一个键就不用再跨网络。这解决了"热点会话"问题——比如一个高活跃用户可能在同一节点上连续发起多个请求。但L1的存在也引入了一个短暂的"读一致性盲区"：在owner更新数据到L1副本到期之间，L1持有旧数据。

**缓存过期与Passivation（钝化）机制**：Infinispan的过期有两种策略——`lifespan`（自写入起N毫秒后过期，无论是否被访问）和`max-idle`（N毫秒未被访问则过期）。会话缓存通常用lifespan（基于会话Max时间），actionToken用较短lifespan（基于Token有效期）。Passivation是另一个维度——当Infinispan内存紧张时，将冷数据刷写到磁盘（如`/tmp/kc-offline-sessions`），释放内存给热数据。offlineSessions天然适合Passivation：一个"记住我"的离线会话可以休眠数周，偶尔被唤醒，长期占内存不划算。

**跨数据中心（Cross-Site）缓存**：当Keycloak部署在两个地理上独立的数据中心（DC1和DC2）时，Infinispan通过Relay（中继）机制实现跨站点数据同步。每个站点内部使用Distributed/Replicated模式，站点间通过专用的JGroups RELAY2协议在隔离的网络通道上同步——这样即使DC1整体宕机，DC2仍保有全部会话数据。但跨站点的网络延迟（数十到数百毫秒）意味着缓存一致性窗口比站点内大得多。

> **大师技术映射**：owners=2 = 一份文件复印两页（万一丢一页还有备份，但复印太多浪费时间）。L1 = 你在图书馆本地阅览架放了几本常看的书（不用每次跑到远处书架取）。Passivation = 长期不穿的冬装收到柜顶（腾出衣柜给当季衣物）。Cross-Site = 两个分店各有杂物间，每晚交换库存清单。

**小白**：那缓存监控怎么做？运维说他们看不到Infinispan内部状态。

**大师**：Infinispan暴露了大量JMX指标，通过JConsole或Prometheus + JMX Exporter可以采集。核心指标有三个维度：命中率（hit ratio = hits/(hits+misses)），低于90%说明缓存容量不够或TTL太短需调优；读写延迟（read/write avg time），Distributed模式下的写延迟异常高通常意味着owners太多或网络抖动；条目数和内存占用（numberOfEntries / memoryUsed），用于判断是否需要启用Passivation或增加堆内存。Keycloak 26还通过`/metrics`端点暴露了`vendor_statistics_cache`指标，可以直接接入Prometheus的指标采集。

---

## 3 项目实战

### 环境准备

| 组件 | 版本/说明 |
|------|----------|
| Keycloak集群 | 3节点（复用第17章环境），通过JGroups TCP发现或JDBC_Ping互联 |
| Docker Compose | 承载3节点Keycloak容器 |
| JMeter | 压力测试工具 |
| JConsole / Prometheus | 缓存监控工具 |
| 测试Realm | demo-realm（复用第2-3章环境） |

确保3个Keycloak节点已组成集群（检查日志中出现`Received new cluster view`信息），并确认JDBC_Ping表已正常创建。

### 步骤1：查看默认缓存配置

在Keycloak容器中查看Infinispan配置文件：

```bash
docker exec keycloak-1 cat /opt/keycloak/conf/cache-ispn.xml | head -100
```

关键输出片段分析：默认配置使用`distributed-cache`定义sessions域（owners=2），`invalidation-cache`定义actionTokens域，`local-cache`定义authorization域（配合work缓存域的invalidation同步）。可以看到，Keycloak的默认配置已经为各缓存域设置了合理的模式——**但这套配置是为50节点以下的中等规模集群设计的**，如果真的到了上百节点或跨数据中心，就必须自定义。

### 步骤2：自定义缓存配置

在实际生产环境中，需要根据数据量、访问模式、集群规模定制配置文件。创建自定义 `cache-ispn.xml`：

```xml
<infinispan
        xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
        xsi:schemaLocation="urn:infinispan:config:15.0 http://www.infinispan.org/schemas/infinispan-config-15.0.xsd"
        xmlns="urn:infinispan:config:15.0">

    <!-- 集群传输配置 -->
    <jgroups>
        <stack name="jdbc-ping-tcp" extends="tcp">
            <JDBC_PING connection_driver="org.postgresql.Driver"
                       connection_url="jdbc:postgresql://postgres:5432/keycloak"
                       connection_username="keycloak"
                       connection_password="keycloak"
                       initialize_sql="CREATE TABLE IF NOT EXISTS JGROUPSPING (own_addr VARCHAR(200) NOT NULL, cluster_name VARCHAR(200) NOT NULL, ping_data BYTEA, PRIMARY KEY (own_addr, cluster_name))"/>
            <MERGE3 min_interval="10000" max_interval="30000"/>
            <FD_SOCK/>
            <FD_ALL timeout="30000" interval="1000"/>
            <VERIFY_SUSPECT timeout="1500"/>
        </stack>
    </jgroups>

    <cache-container name="keycloak">
        <transport lock-timeout="60000" stack="jdbc-ping-tcp"/>

        <!-- 会话缓存：Distributed模式，owner=2降低写延迟 -->
        <distributed-cache name="sessions" owners="2"
            l1-lifespan="60000" remote-timeout="15000">
            <expiration lifespan="3600000"/>
        </distributed-cache>

        <!-- 认证会话：生命周期短，owner=2即可 -->
        <distributed-cache name="authenticationSessions" owners="2"
            remote-timeout="15000">
            <expiration lifespan="300000"/>
        </distributed-cache>

        <!-- 登录失败计数：Replicated模式保证所有节点计数一致 -->
        <replicated-cache name="loginFailures">
            <expiration lifespan="900000"/>
            <locking concurrency-level="1000" acquire-timeout="15000"/>
        </replicated-cache>

        <!-- 离线会话：数据量大、生命周期长、访问低频 → 启用Passivation -->
        <distributed-cache name="offlineSessions" owners="1"
            l1-lifespan="0" remote-timeout="15000">
            <expiration lifespan="2592000000"/>
            <memory max-count="100000"/>
            <persistence passivation="true">
                <file-store path="/opt/keycloak/data/kc-offline-sessions"/>
            </persistence>
        </distributed-cache>

        <!-- 授权缓存：Local + Invalidation广播失效 -->
        <local-cache name="authorization">
            <expiration max-idle="300000" lifespan="600000"/>
        </local-cache>

        <!-- work缓存：Replicated模式保证任务不重复调度 -->
        <replicated-cache name="work">
            <expiration lifespan="600000"/>
        </replicated-cache>

        <!-- actionTokens：Distributed保证跨节点令牌可寻 -->
        <distributed-cache name="actionTokens" owners="2"
            remote-timeout="15000">
            <expiration max-idle="300000" lifespan="300000"/>
        </distributed-cache>
    </cache-container>
</infinispan>
```

将自定义配置文件挂载到容器：

```yaml
# docker-compose.yml 中挂载缓存配置
services:
  keycloak-1:
    volumes:
      - ./cache-ispn.xml:/opt/keycloak/conf/cache-ispn.xml:ro
    environment:
      KC_CACHE: ispn
      KC_CACHE_STACK: jdbc-ping-tcp
```

配置解释：Distributed缓存的`remote-timeout`设置跨节点读取的超时时间，避免因网络抖动导致请求线程长时间阻塞。`l1-lifespan=0`（offlineSessions）表示禁用L1缓存，因为离线会话访问频率低，L1的收益不足以抵消一致性问题。`memory max-count`限制内存中的最大条目数，超过后触发Passivation将冷数据刷入文件存储。

### 步骤3：通过JMX监控缓存状态

启动Keycloak时开启JMX远程监控：

```yaml
environment:
  JAVA_OPTS_APPEND: >
    -Dcom.sun.management.jmxremote
    -Dcom.sun.management.jmxremote.port=9999
    -Dcom.sun.management.jmxremote.rmi.port=9999
    -Dcom.sun.management.jmxremote.authenticate=false
    -Dcom.sun.management.jmxremote.ssl=false
```

使用JConsole连接`localhost:9999`，在MBeans标签页下展开`jboss.infinispan` → `cache-container="keycloak"` → `cache="sessions"` 查看运行指标：

```
属性:
  numberOfEntries        = 1247        # 当前会话缓存条目数
  hitRatio               = 0.94        # 命中率94%
  readWriteRatio         = 5.3         # 读是写的5.3倍
  averageReadTime        = 0.8ms       # 单次读取平均0.8ms
  averageWriteTime       = 3.2ms       # 单次写入平均3.2ms（含跨节点复制）
  numberOfLocksAvailable = 10000       # 可用锁数量
```

通过Keycloak Metrics端点查看缓存指标：

```bash
# 访问指标端点
curl http://localhost:8080/metrics | grep vendor_statistics_cache

# 典型输出：
# vendor_statistics_cache_misses_total{cache="sessions",node="node_1"} 243
# vendor_statistics_cache_puts_total{cache="sessions",node="node_1"} 1876
# vendor_statistics_cache_hits_total{cache="sessions",node="node_1"} 9845
```

命中率计算：`9845 / (9845 + 243) = 97.6%`——非常高，说明L1缓存和owner配置合理。如果命中率低于85%，优先检查TTL是否过短、L1是否未启用、或集群是否因网络波动频繁重平衡。

### 步骤4：验证缓存行为

以下Python脚本验证三个典型场景：

```python
import requests
import time
import random

BASE_URLS = [
    "http://localhost:8080",
    "http://localhost:8081",
    "http://localhost:8082",
]
REALM = "demo-realm"

def get_token(node_url, username, password):
    """登录并获取Access Token"""
    resp = requests.post(
        f"{node_url}/realms/{REALM}/protocol/openid-connect/token",
        data={
            "client_id": "admin-cli",
            "username": username,
            "password": password,
            "grant_type": "password",
        },
    )
    return resp.json()["access_token"]

# 场景1：Session缓存在哪个节点？
print("=== 场景1：验证Session缓存分布 ===")
token1 = get_token(BASE_URLS[0], "testuser", "testpass")
# 用同一个Token在不同节点验证——Token中携带session_state
# 同一session_id的请求应该在sessions缓存的所有owner节点命中

# 场景2：禁用用户后authorization缓存是否刷新？
print("=== 场景2：验证Authorization缓存失效 ===")
admin_token = get_token(BASE_URLS[0], "admin", "admin")
# 1. 先在各节点预热authorization缓存（多次查询用户角色）
for url in BASE_URLS:
    for _ in range(5):
        requests.get(
            f"{url}/admin/realms/{REALM}/users/testuser/role-mappings/realm",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
# 2. 在节点1禁用用户
requests.put(
    f"{BASE_URLS[0]}/admin/realms/{REALM}/users/testuser",
    headers={"Authorization": f"Bearer {admin_token}"},
    json={"enabled": False},
)
# 3. 立即在节点2查询用户状态——Invalidation缓存已广播失效通知
time.sleep(0.5)
resp = requests.get(
    f"{BASE_URLS[1]}/admin/realms/{REALM}/users/testuser",
    headers={"Authorization": f"Bearer {admin_token}"},
)
user = resp.json()
assert user["enabled"] == False, "缓存未刷新！节点2仍读到旧数据"
print("节点2已正确读取到禁用状态，Invalidation机制生效")

# 场景3：集群重平衡验证
print("=== 场景3：节点宕机时缓存重平衡 ===")
# 停止keycloak-2 (docker stop keycloak-2)
# 观察日志：keycloak-1 和 keycloak-3 出现 "Rebalancing" 字样
# sessions缓存中原属keycloak-2的数据段（Segment）被重新分配到剩余节点
# 注意：owner=2时，keycloak-2持有的"首要owner"数据才会触发迁移；
# 如果keycloak-2只是"备份owner"，数据在主owner—keycloak-1上仍然可用，无需迁移
```

场景3的重平衡日志关键行：

```log
INFO  [org.infinispan.CLUSTER] (jgroups-124) ISPN000094: Received new cluster view for channel keycloak: [keycloak-1|2] (3) [keycloak-1, keycloak-2]
INFO  [org.infinispan.CLUSTER] (jgroups-124) ISPN000094: Received new cluster view for channel keycloak: [keycloak-1|3] (2) [keycloak-1, keycloak-3]
INFO  [org.infinispan.CLUSTER] ISPN000310: Starting cluster-wide rebalance for cache 'sessions'
INFO  [org.infinispan.CLUSTER] ISPN000336: Finished cluster-wide rebalance for cache 'sessions', topology id = 14
```

### 步骤5：缓存优化实战

Docker环境变量中的高级调优参数：

```yaml
environment:
  KC_CACHE: ispn
  KC_CACHE_STACK: jdbc-ping-tcp
  JAVA_OPTS_APPEND: >
    -Dcom.sun.management.jmxremote.port=9999
    -Dcom.sun.management.jmxremote.authenticate=false
    -Dcom.sun.management.jmxremote.ssl=false
    -Djgroups.udp.mcast_port=46655
    -Dinfinispan.transport.max_retries=5
    -Dinfinispan.transport.retry_timeout=30000

  # 自定义堆内存（Infinispan缓存消耗堆空间，需合理规划）
  # -Xms1g -Xmx2g
```

关键参数说明：`max_retries=5`——集群发现失败后的重试次数，生产环境应设为适度值（太小容易因临时网络问题导致节点永久脱离集群，太大会延长节点不可用窗口）。`retry_timeout=30000`——每次重试的间隔30s，总加入集群窗口约150s，期间节点不接受外部请求（处于STARTING状态）。

### 步骤6：缓存清理操作

在排查故障或做紧急修复时，可通过Admin API手动清除缓存：

```bash
# 获取Admin Token（注意：Realm为master）
ADMIN_TOKEN=$(curl -s -X POST http://localhost:8080/realms/master/protocol/openid-connect/token \
  -d "client_id=admin-cli&username=admin&password=admin&grant_type=password" | jq -r '.access_token')

# 清除特定用户的缓存（包括所有该用户在各节点的sessions/authorization缓存副本）
curl -X POST "http://localhost:8080/admin/realms/demo-realm/users/{USER_ID}/clear-cache" \
  -H "Authorization: Bearer $ADMIN_TOKEN"

# 清除整个Realm的缓存（慎用——会导致所有在线用户的下一次请求全部需要查库重建缓存）
curl -X POST "http://localhost:8080/admin/realms/demo-realm/clear-realm-cache" \
  -H "Authorization: Bearer $ADMIN_TOKEN"

# 清除特定缓存的指定键（仅限Admin Console或JMX操作）
# 用途：精准失效某个session而不影响其他用户的缓存
```

### 可能遇到的坑

1. **owners数量设置过大的性能陷阱**：将sessions缓存的owners从2提升到5，写入延迟从3ms飙升到15ms（需等待5个节点全部确认）。如果集群中有10个节点而owner=3，则写入仅涉及3个节点——但读取时需要一致性哈希查找owner位置。调优公式：`owners = min(3, ceil(集群节点数 / 3))`。

2. **Local缓存的数据修改不通知其他节点**：这是最隐蔽的陷阱。如果你误将authorization缓存改为纯Local模式（而不配合Invalidation），那么节点A上管理员改了用户角色，节点B仍然使用自己的旧缓存——直到缓存自然过期。在分布式环境下，任何需跨节点感知的变更都不能用纯Local。

3. **Passivation文件存储路径权限问题**：Docker容器默认以`keycloak`用户运行（UID 1000），若文件存储路径挂载的宿主机目录权限为`root:root`，Passivation写入时会报`Permission denied`。解决：`chown -R 1000:1000 /path/to/offline-sessions`，或在Docker Compose中指定`user: "1000:1000"`。

4. **JDBC_PING表清理策略**：使用JDBC_Ping作为集群发现机制时，每个节点在`JGROUPSPING`表中插入一条记录。如果节点未正常退出（如K8s Pod被直接Kill），表中会残留孤立记录，导致表无限增长。Keycloak自身不自动清理，需在数据库层设置定时任务清理超过1小时未更新的记录：

```sql
DELETE FROM JGROUPSPING 
WHERE ping_data IS NULL 
  OR EXTRACT(EPOCH FROM (NOW() - last_updated)) > 3600;
```

### 测试验证

用JMeter模拟100个并发用户30分钟内持续登录、访问受保护API、登出，对比优化前后的指标：

| 指标 | 优化前（默认配置） | 优化后（自定义配置） |
|------|-------------------|---------------------|
| 缓存命中率 | 88% | 96% |
| P99登录延迟 | 850ms | 340ms |
| 节点间同步延迟 | 50-200ms | 10-30ms |
| 重启后集群形成时间 | 45s | 18s |
| 离线会话内存占用 | 1.2GB | 180MB（Passivation起效） |

---

## 4 项目总结

### 优点 & 缺点：Infinispan vs Redis vs Hazelcast

| 维度 | Infinispan | Redis | Hazelcast |
|------|-----------|-------|-----------|
| 部署模式 | 内嵌JVM（与Keycloak进程一体） | 独立中间件（需单独部署Redis集群） | 内嵌或独立均可 |
| 运维复杂度 | 低（零额外服务，天然与Keycloak生命周期绑定） | 中（需维护Redis集群、哨兵、持久化策略） | 中（需维护Hazelcast集群） |
| 一致性模型 | 强一致性（分布式事务支持） | 最终一致性（主从异步复制） | 强一致性（CP子系统） |
| 跨数据中心 | 原生Cross-Site支持 | 需Redis Enterprise或自建同步 | 原生WAN复制 |
| 序列化 | JBoss Marshalling / ProtoStream（需关注版本兼容） | 文本协议（JSON/MessagePack） | 多种序列化选项 |
| 性能 | 内嵌优势（无网络跳转），但GC压力大 | 网络跳转增加延迟，但内存管理独立 | 内嵌时与Infinispan相当 |
| 配置灵活性 | 极高（XML/编程式），但学习曲线陡峭 | 简单直观 | 中等 |
| 社区生态 | Red Hat支撑，Keycloak默认捆绑 | 最大生态，运维知识广泛 | 较小但文档完善 |

Keycloak默认选择Infinispan而非Redis的核心原因：**架构紧耦合需求**。Keycloak启动时Infinispan作为内嵌库随进程一起启动，零外部依赖——这对于"企业内部部署"场景至关重要（很多传统企业不允许引入Redis等新组件）。此外，Infinispan的Cross-Site复制是Keycloak.X多数据中心方案的基石，而Redis的跨数据中心方案要么依赖商业版要么需要大量自建工作。

### 适用场景

- **会话共享**（sessions + authenticationSessions）：Distributed模式是标准答案，是Keycloak集群的命脉。
- **Token缓存**（offlineSessions）：Distributed + Passivation，长期存储离线会话但不同时期堆积内存。
- **失败计数同步**（loginFailures）：Replicated模式，数据量小但对一致性要求极高——攻击者若在节点间计数不同，可能穷举成功。
- **权限缓存**（authorization）：Local + Invalidation，高频读取走本地内存，写时广播失效通知——极致的读写分离。

### 注意事项

- **缓存一致性 vs 性能的权衡**：Distributed模式owner=1性能最好但无故障容错；owner=3容错强但写入变慢。金融行业通常owner=2，对一致性不够时可以提升至3但仅在数据量可控时。
- **owners数量设置原则**：不应超过集群节点数的50%——10节点集群中owner=5意味着每次写入走半个集群，写延迟不可接受。
- **Passivation启用条件**：仅当缓存条目数超过`memory max-count`且条目的访问频率低时才启用。高访问频率的缓存（如sessions）不应启用Passivation——频繁的磁盘IO会抵消缓存的性能收益。
- **L1 Lifespan设置**：建议设为TTL的10-20%。会话TTL为3600s，L1 lifespan=60s是合理值——1分钟内允许读到脏数据，但避免了每次都跨网络访问。

### 常见踩坑

- **序列化版本兼容**：Keycloak版本升级时，UserSessionEntity的序列化结构可能变化。如果不先清空缓存再升级，新版本节点尝试反序列化旧版本数据会抛出`ClassNotFoundException`。升级流程应包含：停止所有节点 → 清空Infinispan持久化目录 → 启动新版本。
- **脑裂时的数据冲突**：当网络分区导致集群分裂为两个子集群时，两边的节点各自维护一份缓存。分区恢复后，Infinispan的MERGE3协议会合并两个视图——但**数据层面的冲突由最后写入者胜出**（Last-Write-Wins）。这意味着在脑裂期间，被禁用用户可能在子集群B中仍然活跃。缓解方案：增加`<FD_ALL timeout> `使故障检测更灵敏，减少分区窗口。
- **缓存过期与数据库不一致**：缓存的TTL和数据库中实体的实际状态之间天然存在"滞后窗口"。例如：管理员在数据库中直接更改了用户的`enabled=false`（绕过了Keycloak），但缓存中仍有活跃session。只有在缓存过期或被Invalidation消息清理时，才会重新从数据库加载。**正确的做法是始终通过Keycloak Admin API操作而非直接改数据库**。

### 思考题

**Q1：如果使用Redis代替Infinispan作为Keycloak的缓存存储，需要如何改造？**

需要实现Keycloak的`InfinispanConnectionProvider` SPI接口，将所有缓存域的读写操作桥接到Redis。最大的挑战在于：Infinispan通过JGroups实现集群发现和节点间直接通信，而Redis是中心化的客户端-服务端模型。这意味着Invalidation机制需要改成Redis的Pub/Sub频道（一个节点发布键失效消息，其他节点订阅），数据分片逻辑需要改为Redis Cluster的槽位机制。此外，Redis缺乏Passivation（内存→磁盘）的内建能力，需自行实现或使用Redis的AOF/RDB持久化来替代。总体改造量约数千行代码，除非有强烈的"统一运维"需求，否则不推荐。

**Q2：为什么Keycloak默认选择Infinispan而不是Redis？**

核心原因有三：(1)零外部依赖——Keycloak作为独立部署的认证服务器，不应强制用户引入Redis基础设施；(2)内嵌架构性能——同一JVM内内存访问无网络开销；(3)Cross-Site原生支持——Infinispan的跨数据中心能力（RELAY2协议）是Keycloak.X多活部署的底层基础，Redis在这方面的方案要么昂贵（企业版）要么复杂（自建同步）。

**Q3：在K8s环境中，Pod频繁重启会导致Infinispan缓存频繁重平衡，如何优化？**

三管齐下：(1)使用JDBC_Ping（数据库表）或DNS_Ping替代组播发现——K8s中组播不可靠；(2)增加`stable-topology-timeout`降低对Pod变化的敏感度——避免因一次滚动更新就触发全集群重平衡；(3)将Keycloak Pod配置为StatefulSet（固定Pod名）配合适当的`terminationGracePeriodSeconds`（如60秒），让Pod在退出前优雅地通知集群并完成数据迁移——而不是被直接Kill留下数据缺口。
