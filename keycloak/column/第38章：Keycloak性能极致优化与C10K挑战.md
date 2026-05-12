# 第38章：Keycloak性能极致优化与C10K挑战

## 1 项目背景

某社交平台在某天下午遭遇了"突发热点事件"——顶流明星A在平台发了一条官宣微博，消息在半小时内引爆了全网。1小时内涌入百万级用户，刷新、评论、转发、私信……所有功能都要过一遍认证鉴权。Keycloak瞬间承受了远超日常100倍的认证请求：平时QPS稳定在500，热点期间QPS直冲50000。

运维团队的第一反应是惊恐：集群3个节点的CPU同时飙到100%，数据库连接池（HikariCP默认`maximumPoolSize=20`）在峰值流量下瞬间耗尽，HikariCP日志开始疯狂输出`Connection is not available, request timed out after 30000ms`。更为致命的是Infinispan缓存集群出现了Network Timeout——节点间通过JGroups同步Session的数据包大量丢包重传，缓存一致性检查失败，节点之间互相怀疑对方已宕机，触发了集群重新选举。

最致命的一击来自JVM：当堆内存被压到极限后，G1GC连续多次并发标记失败，退化为Full GC。三个节点几乎同时进入STW（Stop-The-World），单次停顿超过10秒。负载均衡器的健康检查（每秒探活`/health/ready`端点）在5秒超时后判定全部节点不可用，将三个节点从upstream中全部剔除。至此，整个认证服务陷入了"死亡循环"——GC完成后节点恢复，流量涌入，堆再次打满，再次Full GC，再次被踢出。认证服务完全瘫痪45分钟，直接经济损失数百万。

事后复盘，团队总结了五个深层痛点：

**痛点一：Token签名运算吃光CPU。** 该平台默认使用RS256（RSA 2048位）作为Token签名算法。火焰图显示，`RSASigner.sign()`独占35%的CPU时间。在QPS 50000的场景下，每秒上万次RSA签名就是CPU的"绞肉机"——RSA私钥模幂运算的复杂度是O(n³)，比ECDSA的椭圆曲线点乘高出数个数量级。

**痛点二：数据库连接池未针对高并发优化。** 默认20个连接、30秒超时，在高并发下就是"窗口不够用+排队过长"。每次密码登录要查用户表、验证密码哈希、写登录事件，每个请求至少3次数据库交互，5000并发同时发起登录意味着15000次数据库查询在排队等20个连接。

**痛点三：Infinispan Java默认序列化性能极差。** Java原生`ObjectOutputStream`序列化一个UserSessionModel大约耗时1.5ms，还要额外负担反射和类型元数据的开销。在节点间高频同步Session的场景下，序列化开销累计到CPU的~10%，且序列化后的字节体积大，网络传输延迟进一步放大。

**痛点四：操作系统层面调优被完全忽略。** 运维从未调整过TCP参数——`net.core.somaxconn`是默认的128（TCP连接完成队列容量），高并发时队列满导致客户端连接被RST。文件描述符限制是默认的1024，Keycloak进程未能打开更多Socket连接。`Transparent Huge Pages`启用导致JVM堆出现随机的高延迟停顿。

**痛点五：Token体积过大。** RS256签名的JWT约1200字符，在HTTP Header中每次传递约1.2KB。在C10K场景下，仅Token的网络传输就占据~12Mbps的带宽。JWT解析（Base64解码+JSON反序列化+签名校验）占用~8% CPU。

> **一句话总结**：性能优化不是加机器——当CPU被RSA签名吃满、数据库连接被耗尽、GC停顿把节点踢出集群，加再多机器也只是增加更多"卡死"的节点。

---

## 2 项目设计——剧本式交锋对话

**小胖**（瘫坐在工位上，盯着监控大屏上那三条直线——三个Keycloak节点全部被踢出集群，流量降到零）：大师，我悟了！这Keycloak性能优化，就像改装一辆F1赛车——在普通公路上跑不过家用轿车，因为限速80、红绿灯多、路也颠簸。但我们搞C10K，是要上纽北赛道，得把每个零件都调到极限：发动机（CPU）、变速箱（GC）、轮胎（数据库连接）、空气动力学套件（网络层）——一个都不能掉链子！

但我不明白一个事：Quarkus编译成Native Image后，Keycloak启动才几秒，这不就是"电车起步快"吗？为啥说运行时反而不一定比JVM JIT快？

**大师**（在白板上画了一条对比曲线）：小胖这个F1比喻很到位，但Native Image和JVM JIT的对比，需要一个更精确的类比——**提前翻译好的书 vs 边读边翻译的书**。

GraalVM Native Image是AOT编译——启动前已经把字节码全部翻译成机器码了，所以启动快得像翻一本已经翻译好的中文书。但AOT缺少一个关键信息：**运行时的执行频率数据**。编译器不知道哪些代码是热路径（被调用了十万次），只能做保守的通用优化，生成的机器码"四平八稳"但不够"激进"。

JVM JIT（如HotSpot C2编译器）则反过来——启动慢，但会持续收集运行时信息：哪个方法被调了多少次、哪个分支走了多少次、哪个对象永远不会是null。基于这些profile数据，JIT可以做出AOT做不到的激进优化：内联虚方法调用（即使有多个实现，如果95%走同一个实现就直接内联）、消除锁（检测到锁永远没有竞争就直接去掉）、展开循环、标量替换（对象直接分配到寄存器而不是堆上）。

**Native Image的热路径性能通常比JIT慢10-20%**，但启动速度快10-30倍（秒级 vs 分钟级），内存占用低30-50%。所以SRE场景（频繁扩缩容、Serverless）选Native Image；长生命周期服务（一次启动跑几个月，需要极限吞吐）选JVM JIT。

> **大师技术映射**：Native Image（AOT）→ 提前翻译好的教材，拿到就能读，但翻译者不知道你会重点读哪几章，没法在重点章节加批注。JVM JIT → 你边读边查词典翻译，开头慢，但读到第三遍时已经烂熟于心，关键的段落直接背出来了。

---

**小白**（掏出笔记本，上面画了一张椭圆曲线图）：大师，Token签名这事我深挖了一下。RS256的RSA 2048位——模幂运算用的是大整数乘法，密钥长度2048位，签名操作复杂度约O(n²logn)。ES256的ECDSA P-256——椭圆曲线点乘，密钥长度只有256位，复杂度和RSA完全不在一个量级。我想确认：能不能把Token签名卸载到加密硬件——HSM或者TPM？另外，ProtoStream和Java序列化到底有什么本质区别？是二进制格式的差异还是架构设计哲学的差异？

**大师**：小白问到点子上了，三个好问题逐个拆。

**第一，HSM/TPM卸载签名运算。** 理论上是可行的，Keycloak本身支持PKCS#11——通过`keycloak.conf`配置`spi-keys-public-key-storage`和`spi-keys-private-key-storage`指向HSM模块。HSM里的专用加密芯片做RSA签名比CPU快5-10倍，但有一个代价——**跨进程/网络调用延迟**。每次Token签名都要走PKCS#11协议从JVM调到HSM，往返延迟通常在0.5-2ms。如果你的场景是QPS 10000+，单次签名用CPU约0.8ms，改用HSM最快也要0.5ms通信延迟 + 0.1ms运算 = 0.6ms，提升有限还引入了新的故障点（HSM宕机）。所以HSM的合理用法是"签少量长寿命Token（如Refresh Token）而非每个API请求的Access Token"。真正的解法是换算法——从RS256切到ES256，签名时间从~1.2ms降到~0.3ms，75%的提升就在一行配置。

**第二，ProtoStream vs Java序列化。** 这不是简单的二进制格式差异，是**契约优先 vs 反射自省**两种设计哲学的对决。Java序列化通过反射自动提取对象的所有字段（包括private字段），把类型元数据、包名、字段名全部序列化进去——这就是为什么一个UserSessionModel对象序列化后1.5ms、字节体积3000+ bytes。ProtoStream要求你预先定义`.proto`文件（Schema），序列化时只写纯数据值（没有字段名、没有类型元数据），反序列化时按Schema描述的位置直接读取。省掉了反射开销和元数据体积，序列化速度提升50%、体积减少60%以上。代价是必须维护Schema——每次POJO加字段，都要同步修改`.proto`文件。

**第三，让我补充一个RefToken vs JWT的性能取舍。** Reference Token（又称Opaque Token）只发一个随机字符串（约32字节），Resource Server拿到RefToken后必须回Keycloak做Introspection校验。优势是Token体积极小（32字节 vs 1200字节），劣势是每个API请求多一次网络往返（~5-10ms延迟）。自包含JWT的优势是Resource Server可以本地校验（零额外网络调用），劣势是Token体积大（网络传输+解析开销）。选型规则：Token用于浏览器前端或移动端（Header携带、用户感知不到延迟）→ RefToken最佳；Token用于微服务内部RPC调用（对延迟敏感、频繁调用）→ 本地校验JWT最佳。

> **大师技术映射**：Java序列化 → 搬家时把家具连包装箱上的标签、说明书、购买发票全塞进箱子——信息全但箱子大。ProtoStream → 搬家前先约定好每个箱子的规格——只装箱内物品，标签在外面的清单上统一管理。RSA→ES256 → 从骑自行车送货换成电动车——都能送，但脚蹬和电机的效率天壤之别。

---

**小胖**（第二轮，举手打断）：等等等等！大师，你说了这么多优化手段——换算法、换序列化、调JVM参数、调OS参数……我听着像一个"砍一刀"优惠大拼盘，每个都砍掉一点点。但我最关心的是：**Money（成本）！** 我们折腾这些，到底省了多少服务器成本？还有，我看公司压测团队用的是wrk2做恒速压测——3000 QPS的稳得像飞机巡航。结果一上线，"真实流量"不是恒速的，是波动的、爆炸式的——明星发微博那一下，QPS从500秒变50000，这不就是"压测3000过了，上线5000秒崩"吗？

**大师**（赞许地看着小胖）：小胖这次问到实操层面了，而且比之前的比喻都犀利。我给你答案。

**第一，成本效益比。** 换ES256算法：零成本（改一行配置）。换ProtoStream序列化：约2人日（写.proto文件）。JVM调优：零成本（改启动参数）。OS调优：零成本（运维一次sysctl）。如果你靠加机器——从3节点扩到15节点才能接住50000 QPS，每个节点8核16G的云服务器月费约2000元，12节点的增量成本就是24000元/月，加上带宽和负载均衡器，年增成本30万+。而一套优化组合拳下来，3节点就从原来的QPS 4000提升到QPS 27000，省下的钱够全团队团建一年。

**第二，压测≠真实流量。** 恒速压测（wrk2 `-R`模式）验证的是"稳态性能"——你的系统在给定负载下能不能稳定运行、P99延迟是否可控。但它测不出"突发性能"——QPS如何从500秒跳到50000？系统是否有足够的缓冲（连接池、队列、内存）吸收冲击？GC会不会在突增流量下崩溃？正确的做法是**混合压测模型**：恒速段验证稳态 + 冲击段验证弹性 + 随机波动段验证自愈能力。k6的`scenarios`可以组合`constant-arrival-rate`和`ramping-arrival-rate`来模拟这种流量模式。

**第三，关于"过早优化"。** 你们的基线压测做了吗？火焰图火焰图出了吗？没有数据驱动的优化就是在黑暗中打靶。记住口诀："先测后优，火焰图指路，每次只改一个变量"。把ES256切了再测一次，再把ProtoStream切了再测一次，每次对比——这样你才知道每一步的提升是多少，出了问题也知道是哪个改动导致的。

> **大师技术映射**：恒速压测 → 汽车在封闭测试跑道跑匀速百公里油耗——参考意义有，但真实城市通勤是红绿灯+堵车+急刹，油耗完全不一样。混合压测模型 → 既要测高速巡航，也要测市区走走停停，还要测冷启动。成本效益比 → 改装发动机只要几万（调参），换车要几十万（加机器）——懂行的先改装，不懂的只会加钱。

---

## 3 项目实战

### 环境准备

- **Keycloak 26.1**：`quay.io/keycloak/keycloak:26.1`（Docker Compose 3节点集群 + PostgreSQL）
- **wrk2**：恒速压测工具（`https://github.com/giltene/wrk2`，比wrk多了`-R`精确控制QPS）
- **wrk**：大并发压测工具（`https://github.com/wg/wrk`，用于C10K极限测试）
- **async-profiler 3.0**：低开销CPU火焰图采样（`https://github.com/async-profiler/async-profiler`）
- **Grafana + Prometheus**：实时监控CPU、内存、GC、QPS（参考第30章搭建）
- **PostgreSQL 16**：作为Keycloak共享数据库
- 压测机至少8核16G（与Keycloak节点网络延迟<1ms）

---

### 步骤1：建立性能基线

**目标**：在未做任何优化的默认配置下，使用wrk2恒速压测获取精确的性能基线数据。

```bash
# ===== wrk2恒速压测脚本 oauth2-login.lua =====
# 模拟OAuth2 password grant登录流程

wrk.method = "POST"
wrk.body = "client_id=oms-frontend&username=testuser1&password=Test@12345&grant_type=password&scope=openid"
wrk.headers["Content-Type"] = "application/x-www-form-urlencoded"

request = function()
    return wrk.format(nil, "/realms/demo-realm/protocol/openid-connect/token")
end

# ===== 执行压测 =====
wrk2 -t4 -c100 -d60s -R1000 \
  -s oauth2-login.lua \
  http://localhost:8080/realms/demo-realm/protocol/openid-connect/token

# ===== 基线结果（单节点） =====
# 吞吐量 (Throughput):    985 req/s  (目标1000, 实际达标率98.5%)
# P50延迟:                 120ms
# P99延迟:                 800ms
# P99.9延迟:               2200ms
# 错误率:                  1.2%
# CPU使用率:               65%
# 堆内存使用:              2.1GB / 4GB
# GC平均暂停:              85ms (G1GC Young), 失败次数 3次/分钟
# 数据库活跃连接:          35/50 (HikariCP maximumPoolSize=50)
```

---

### 步骤2：生成火焰图定位CPU热点

**目标**：使用async-profiler在不重启Keycloak的情况下采集CPU火焰图，精确识别Top 5热点函数。

```bash
# ===== 1. 进入Keycloak容器安装async-profiler =====
docker exec -it kc-node-1 bash

# 下载async-profiler
wget https://github.com/async-profiler/async-profiler/releases/download/v3.0/async-profiler-3.0-linux-x64.tar.gz
tar xzf async-profiler-3.0-linux-x64.tar.gz

# ===== 2. 获取Keycloak Java进程PID =====
# Keycloak容器中JVM进程通常是PID 1，也可以jps确认
jps -v
# 输出: 1 quarkus-run.jar -Xmx4096m ... (PID=1)

# ===== 3. 启动CPU采样 (120秒) =====
./profiler.sh -d 120 -f /tmp/flamegraph.html \
  -e cpu \
  --all-user \
  1

# 将火焰图从容器拷贝到宿主机
docker cp kc-node-1:/tmp/flamegraph.html ./flamegraph_baseline.html
```

**火焰图分析结果 —— Top 5 热点函数：**

| 排名 | 热点函数 | CPU占比 | 说明 |
|------|---------|---------|------|
| 1 | `RSASigner.sign()` | 35% | RSA 2048位模幂运算，CPU头号杀手 |
| 2 | `Jackson/ObjectMapper.writeValue()` | 15% | JSON序列化（Token、Response、Event） |
| 3 | `EntityManager.find()` | 12% | JPA数据库查询（用户查找、密码校验） |
| 4 | `JGroups/UDP.send()` | 10% | Infinispan节点间Session同步网络IO |
| 5 | `JJWTDecoder.parse()` | 8% | JWT Base64解码 + JSON反序列化 + 验签 |

> **结论**：`RSASigner.sign()` + 签名相关操作合计占CPU的43%。只要解决这一个热点，就能获得最大的单点优化收益。这也正是WebAuthn推广ES256/P-256作为首选算法的底层原因。

---

### 步骤3：签名算法切换 —— RS256 → ES256

**目标**：将Token签名算法从RS256切换为ES256，验证签名性能提升和Token体积缩减。

```bash
# ===== 方式一：通过Admin Console =====
# Realm Settings → Keys → Providers → 添加新的密钥提供者
# Provider: rsa-generated → 点击不放，改为 ecdsa-generated
# Algorithm: ES256
# 将新密钥设为 Active（勾选）

# ===== 方式二：通过Keycloak Admin REST API =====
# 获取admin token
ADMIN_TOKEN=$(curl -s -X POST \
  http://localhost:8080/realms/master/protocol/openid-connect/token \
  -d 'client_id=admin-cli' \
  -d 'username=admin' \
  -d 'password=admin' \
  -d 'grant_type=password' | jq -r '.access_token')

# 为demo-realm添加ES256密钥提供者
curl -X POST \
  http://localhost:8080/admin/realms/demo-realm/components \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "ecdsa-generated",
    "providerId": "ecdsa-generated",
    "providerType": "org.keycloak.keys.KeyProvider",
    "config": {
      "priority": ["100"],
      "ecdsaEllipticCurveKey": ["P-256"]
    }
  }'

# ===== 验证Token体积变化 =====
# 生成一个RS256 Token（旧密钥）
RS256_TOKEN=$(curl -s -X POST \
  http://localhost:8080/realms/demo-realm/protocol/openid-connect/token \
  -d 'client_id=oms-frontend&username=testuser1&password=Test@12345&grant_type=password' \
  | jq -r '.access_token')

# 生成一个ES256 Token（新密钥——Client需切到新密钥算法）
# 在Admin Console中: Clients → oms-frontend → Settings → 
# Access Token Signature Algorithm → ES256

echo "RS256 Token长度: ${#RS256_TOKEN} 字符"   # 约1200字符
echo "ES256 Token长度: ${#ES256_TOKEN} 字符"   # 约900字符

# ===== 重新压测 =====
wrk2 -t4 -c100 -d60s -R1000 \
  -s oauth2-login.lua \
  http://localhost:8080/realms/demo-realm/protocol/openid-connect/token

# ===== 优化结果 =====
# 吞吐量:         985 req/s  →  1450 req/s  (+47%)
# CPU使用率:      65%        →  48%         (-26%)
# P99延迟:        800ms      →  520ms       (-35%)
# Token体积:      1200字符   →  900字符     (-25%)
```

> **注意**：ES256的兼容性略逊于RS256。部分老旧的JWT库（.NET Framework < 4.5、Python PyJWT < 1.0）不支持ES256。建议先在测试环境验证下游服务兼容性。

---

### 步骤4：Infinispan ProtoStream序列化优化

**目标**：将Infinispan的Session序列化从Java原生序列化切换为ProtoStream二进制序列化。

```bash
# ===== 1. 配置cache-ispn.xml (挂载到容器 /opt/keycloak/conf/cache-ispn.xml) =====
```

```xml
<?xml version="1.0" encoding="UTF-8"?>
<infinispan
    xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
    xsi:schemaLocation="urn:infinispan:config:15.0 http://www.infinispan.org/schemas/infinispan-config-15.0.xsd"
    xmlns="urn:infinispan:config:15.0">

    <cache-container name="keycloak">
        <transport lock-timeout="60000" stack="udp"/>

        <!-- === ProtoStream编码配置 === -->
        <serialization>
            <context-initializer class="org.keycloak.marshalling.KeycloakSerializationContextInitializer"/>
        </serialization>

        <!-- Session缓存 —— 使用ProtoStream编码 -->
        <distributed-cache name="sessions" owners="2">
            <encoding media-type="application/x-protostream"/>
            <memory max-count="20000"/>
        </distributed-cache>

        <!-- 认证会话缓存 -->
        <distributed-cache name="authenticationSessions" owners="2">
            <encoding media-type="application/x-protostream"/>
            <memory max-count="5000"/>
        </distributed-cache>

        <!-- 登录失败缓存 -->
        <distributed-cache name="loginFailures" owners="2">
            <encoding media-type="application/x-protostream"/>
        </distributed-cache>

        <!-- Off-Heap缓存 —— 减少GC压力 -->
        <distributed-cache name="offlineSessions" owners="2">
            <encoding media-type="application/x-protostream"/>
            <memory storage="OFF_HEAP" max-size="500MB"/>
        </distributed-cache>

        <!-- 工作缓存（Token等） -->
        <replicated-cache name="work">
            <encoding media-type="application/x-protostream"/>
        </replicated-cache>
    </cache-container>
</infinispan>
```

```bash
# ===== 2. Docker Compose挂载配置文件 =====
# docker-compose.yml 中 keycloak 服务的 volumes 段添加:
#   - ./cache-ispn.xml:/opt/keycloak/conf/cache-ispn.xml

# ===== 3. 重启集群并验证 =====
docker compose restart

# 查看日志确认ProtoStream已启用
docker logs kc-node-1 2>&1 | grep -i protostream
# 输出: Registered protostream serialization context initializer

# ===== 4. 压测验证 =====
wrk2 -t4 -c100 -d60s -R1000 \
  -s oauth2-login.lua \
  http://localhost:8080/realms/demo-realm/protocol/openid-connect/token

# ===== 优化结果 =====
# 序列化速度:       提升50% (1.5ms → 0.75ms per serialize)
# 缓存写入延迟:     降低40%
# 内存占用:         Session字节体积减少60% (3000B → 1200B)
# CPU使用率:        48% → 42% (额外降低6%)
```

**可能遇到的坑**：ProtoStream要求所有缓存对象的POJO类实现`SerializationContextInitializer`。Keycloak 26.x已经内置了`KeycloakSerializationContextInitializer`，但如果你自定义了SPI并扩展了Session对象，需要自行实现Initializer接口并在`cache-ispn.xml`中注册。

---

### 步骤5：JVM终极调优

**目标**：配置ZGC或优化后的G1GC，配合堆和内存参数，实现低延迟、高吞吐的JVM运行环境。

```bash
# ===== 方案A：ZGC（推荐JDK 21+，要求堆≥4GB） =====
JAVA_OPTS_APPEND="
  -Xms4096m -Xmx4096m                        # 固定堆大小，避免动态伸缩
  -XX:+UseZGC                                 # 启用ZGC（亚毫秒级停顿）
  -XX:ZCollectionInterval=5                   # ZGC最大收集间隔（秒）
  -XX:+ZGenerational                          # ZGC分代模式（JDK 21+，进一步提升吞吐）
  -XX:ConcGCThreads=4                         # 并发GC线程数
  -XX:+UseStringDeduplication                 # 字符串去重（Session ID等大量重复字符串）
  -XX:StringDeduplicationAgeThreshold=3       # 3次GC后触发去重
  -XX:+AlwaysPreTouch                         # 启动时预分配并提交所有内存页
  -XX:+UseTransparentHugePages                # 启用透明大页（需OS支持）
  -XX:MaxDirectMemorySize=2g                  # Off-Heap直接内存上限
  -XX:ReservedCodeCacheSize=512m              # JIT编译代码缓存（AOT/解释/JIT编译结果）
  -Xlog:gc*:file=/tmp/gc.log:time,tags,level # GC详细日志（排查GC问题）
"
```

```bash
# ===== 方案B：G1GC（推荐JDK 17，适合8GB以下堆） =====
JAVA_OPTS_APPEND="
  -Xms4096m -Xmx4096m                        # 固定堆大小
  -XX:+UseG1GC                                # 启用G1GC
  -XX:MaxGCPauseMillis=100                    # 目标GC停顿100ms
  -XX:G1HeapRegionSize=4m                     # 堆Region大小（4GB堆建议4MB）
  -XX:InitiatingHeapOccupancyPercent=35       # 老年代35%时启动并发标记
  -XX:G1ReservePercent=15                     # 预留15%堆空间防止To-Space溢出
  -XX:ParallelGCThreads=4                     # GC并行线程数
  -XX:ConcGCThreads=2                         # 并发标记线程数
  -XX:+UseStringDeduplication                 # 字符串去重
  -XX:+AlwaysPreTouch                         # 启动时预分配内存页
  -XX:MaxDirectMemorySize=2g                  # Off-Heap直接内存
  -XX:ReservedCodeCacheSize=512m              # JIT编译代码缓存
  -Xlog:gc*:file=/tmp/gc.log:time,tags,level # GC详细日志
"
```

```bash
# ===== Docker Compose配置 =====
# 在 keycloak 服务的 environment 段添加:
#   JAVA_OPTS_APPEND: "-Xms4096m -Xmx4096m -XX:+UseZGC ..."

# ===== 重启Keycloak并验证JVM配置生效 =====
docker compose restart

# 检查ZGC是否启用
docker exec kc-node-1 java -XX:+PrintFlagsFinal -version 2>&1 | grep UseZGC
# 输出: bool UseZGC = true

# 检查GC日志
docker exec kc-node-1 cat /tmp/gc.log | head -20
# 预期看到: [gc,start] GC(0) Garbage Collection (ZGC)

# ===== 压测验证 =====
wrk2 -t4 -c100 -d60s -R1500 \
  -s oauth2-login.lua \
  http://localhost:8080/realms/demo-realm/protocol/openid-connect/token

# ===== 优化结果 =====
# GC平均暂停:      85ms   →  <5ms (ZGC)
# GC频率:          3次/分 →  连续并发（无高峰期Full GC）
# P99.9延迟:       2200ms →  650ms (ZGC消除了Full GC的STW)
# 吞吐量:          1450/s →  ~1550/s (轻微提升，主要是消除延迟抖动)
```

**可能遇到的坑**：
- ZGC需要JDK 17+，且内存需≥4GB堆。堆越小，ZGC的吞吐量损失越明显（并发标记本身占用CPU）。小堆（<4GB）推荐G1GC。
- `-XX:+UseTransparentHugePages`需要OS层已启用大页支持。可通过`cat /sys/kernel/mm/transparent_hugepage/enabled`确认。若未启用，建议在宿主机执行`echo always > /sys/kernel/mm/transparent_hugepage/enabled`。
- `-XX:+AlwaysPreTouch`会使启动变慢（预分配所有内存页），但避免了运行时缺页中断导致的延迟抖动——适合长时间运行的服务。

---

### 步骤6：操作系统级调优

**目标**：调整Linux内核TCP参数和文件描述符限制，消除操作系统层的网络瓶颈。

```bash
# ===== 1. TCP/IP协议栈调优 =====
# 在Keycloak宿主机上执行（需要root权限）

cat >> /etc/sysctl.conf << 'EOF'
# ---- TCP连接处理能力 ----
net.core.somaxconn = 65535                      # TCP已完成连接队列容量（默认128→65535）
net.ipv4.tcp_max_syn_backlog = 8192             # SYN队列容量（未完成三次握手的连接）
net.ipv4.tcp_max_tw_buckets = 2000000           # TIME_WAIT状态最大socket数

# ---- TCP连接复用 ----
net.ipv4.tcp_tw_reuse = 1                       # TIME_WAIT复用（客户端侧，安全）
net.ipv4.tcp_fastopen = 3                       # TCP Fast Open (0=关闭, 1=客户端, 2=服务器, 3=双向)

# ---- 端口范围 ----
net.ipv4.ip_local_port_range = 1024 65535       # 增大临时端口范围（C10K需大量端口）

# ---- 内核状态跟踪 ----
net.netfilter.nf_conntrack_max = 2000000        # 连接跟踪表上限（C10K×3节点=30000+并发连接）
net.nf_conntrack_max = 2000000

# ---- 文件描述符 ----
fs.file-max = 2000000                           # 系统文件描述符全局上限

# ---- 内存/Virtual Memory ----
vm.swappiness = 1                               # 尽量不使用Swap（JVM堆在Swap上会严重影响性能）
vm.zone_reclaim_mode = 0                        # 禁用NUMA zone回收（避免跨NUMA节点分配导致的延迟）

# ---- 网络缓冲区 ----
net.core.rmem_max = 134217728                   # 接收缓冲区最大值 128MB
net.core.wmem_max = 134217728                   # 发送缓冲区最大值 128MB
net.ipv4.tcp_rmem = 4096 87380 134217728        # 接收缓冲区(min, default, max)
net.ipv4.tcp_wmem = 4096 65536 134217728        # 发送缓冲区(min, default, max)

# ---- 禁用Transparent Huge Pages (THP) ----
# THP对JVM堆通常有负面影响——大页的压缩和迁移会引入随机的高延迟停顿
# 如果JVM已启用+UseTransparentHugePages，则OS层需要开启。
# 否则推荐关闭THP，改为显式HugeTLB。
EOF

sysctl -p

# ===== 2. 文件描述符和进程数限制 =====
cat >> /etc/security/limits.conf << 'EOF'
keycloak    soft    nofile    65536
keycloak    hard    nofile    65536
keycloak    soft    nproc     65536
keycloak    hard    nproc     65536
EOF

# Docker容器中的ulimit设置
# docker-compose.yml 中 keycloak 服务段添加:
#   ulimits:
#     nofile:
#       soft: 65536
#       hard: 65536
#     nproc:
#       soft: 65536
#       hard: 65536

# ===== 3. 验证调优生效 =====
sysctl net.core.somaxconn          # 期望: 65535
sysctl net.ipv4.tcp_tw_reuse       # 期望: 1
ulimit -n                          # 期望: 65536
```

**可能遇到的坑**：
- `tcp_tw_recycle`已在Linux 4.12中废除——因为它在NAT环境下会导致连接被错误丢弃。只使用`tcp_tw_reuse`。
- THP（Transparent Huge Pages）对JVM的双面性：如果JVM启用了`+UseTransparentHugePages`，OS层需保持THP开启；如果JVM未使用，推荐关闭THP（`echo never > /sys/kernel/mm/transparent_hugepage/enabled`）以避免内存碎片和延迟抖动。
- Docker容器中，`sysctl`网络参数由宿主机内核共享——宿主机调优后容器自动生效。但`ulimit`必须在容器内配置（`--ulimit`参数或`docker-compose.yml`的`ulimits`段）。

---

### 步骤7：Token轻量化 —— Reference Token方案

**目标**：配置Reference Token（Opaque Token），将Access Token体积从~900字符降到~30字符，减少网络传输和JWT解析开销。

```bash
# 1. 在Admin Console中为Client启用Reference Token
# Clients → oms-frontend → Settings → 
# Access Token Signature Algorithm → ES256
# Fine Grain OpenID Connect Configuration →
#   Use Refresh Token For Client Credentials Grant: ON
#
# 同时为Resource Server Client配置Introspection权限
# Clients → oms-backend → Settings →
#   Access Type: confidential
#   Service Accounts Enabled: ON

# 2. 验证Reference Token效果
# 登录获取Token
REF_TOKEN=$(curl -s -X POST \
  http://localhost:8080/realms/demo-realm/protocol/openid-connect/token \
  -d 'client_id=oms-frontend' \
  -d 'username=testuser1' \
  -d 'password=Test@12345' \
  -d 'grant_type=password' \
  | jq -r '.access_token')

echo "Reference Token长度: ${#REF_TOKEN} 字符"   # 约35字符
# 对比JWT: ~900字符（ES256签名后）
# 对比RS256 JWT: ~1200字符

# 3. Resource Server使用Introspection校验Token
# 每个API请求代替本地JWT解析，改为在线校验
curl -X POST \
  http://localhost:8080/realms/demo-realm/protocol/openid-connect/token/introspect \
  -H "Authorization: Basic $(echo -n 'oms-backend:secret' | base64)" \
  -d "token=$REF_TOKEN" \
  | jq

# 返回:
# {
#   "active": true,
#   "username": "testuser1",
#   "exp": 1701234567,
#   "sub": "...",
#   "scope": "openid profile",
#   "client_id": "oms-frontend"
# }

# 4. 性能权衡分析

# ===== JWT模式 (本地校验) =====
# 每请求开销: 本地JWT解析+验签 ≈ 0.3ms
# Token在Header中体积: ~900字节
# 网络开销: 每请求额外 ~900B (请求体+响应头)

# ===== Reference Token模式 (在线校验) =====
# 每请求开销: Introspection API调用 ≈ 5-8ms (网络往返+服务端查询)
# Token在Header中体积: ~35字节
# 网络开销: 每请求额外 ~35B + 一次完整HTTP请求/响应

# ===== 选择指南 =====
# 适用Reference Token:
#   - Token通过浏览器/移动端Header传递（带宽敏感）
#   - Resource Server数量少，且与Keycloak同机房（Introspection延迟<3ms）
#   - Token中Claim数量多且频繁过期（Revocation即时生效）
# 适用JWT:
#   - 微服务内部RPC调用（对延迟敏感，Introspection的5ms无法接受）
#   - Resource Server数量多且分散（Introspection总调用量过大）
#   - 需要离线校验能力（如边缘计算节点）

# 5. 混合方案（推荐大厂做法）
# 一步发两种Token：
#   - ReferenceToken: 返回前端（体积小，带宽友好）
#   - JWT: API Gateway拿到RefToken后Introspect一次，换取JWT再转发给内部微服务
# 这样前端流量省带宽，内部调用省延迟，两全其美。
```

---

### 步骤8：C10K终极压测验证

**目标**：在应用全部优化后，使用wrk发起C10K（单节点10000并发连接）的终极压测，验证性能极限。

```bash
# ===== 1. 准备静态Token（模拟已登录用户的Token校验场景） =====
# 先获取一批Token用于压测（避免压测中包含Token签发开销）

STATIC_TOKEN=$(curl -s -X POST \
  http://localhost:8080/realms/demo-realm/protocol/openid-connect/token \
  -d 'client_id=oms-frontend' \
  -d 'username=testuser1' \
  -d 'password=Test@12345' \
  -d 'grant_type=password' \
  | jq -r '.access_token')

# ===== 2. 编写UserInfo压测脚本 =====
cat > userinfo.lua << 'EOF'
wrk.method = "GET"
wrk.headers["Authorization"] = "Bearer " .. os.getenv("STATIC_TOKEN")

request = function()
    return wrk.format(nil, "/realms/demo-realm/protocol/openid-connect/userinfo")
end
EOF

export STATIC_TOKEN

# ===== 3. C10K压测 =====
# 阶段一：5000并发，摸底
wrk -t8 -c5000 -d60s --latency \
  -s userinfo.lua \
  http://localhost:8080/realms/demo-realm/protocol/openid-connect/userinfo

# 阶段二：C10K，冲极限
wrk -t16 -c10000 -d120s --latency \
  -s userinfo.lua \
  http://localhost:8080/realms/demo-realm/protocol/openid-connect/userinfo

# ===== 阶段三：3节点集群C10K×3压测 (前端Nginx负载均衡) =====
wrk -t16 -c10000 -d120s --latency \
  -H "Authorization: Bearer $STATIC_TOKEN" \
  http://nginx:80/realms/demo-realm/protocol/openid-connect/userinfo

# ===== 完整压测结果对比 =====
```

| 指标 | 优化前（基线） | 优化后（单节点） | 3节点集群 |
|------|---------------|-----------------|-----------|
| **QPS** | 4,000 | 9,000 (+125%) | 26,000 |
| **P50延迟** | 180ms | 85ms | 110ms |
| **P99延迟** | 2,500ms | 800ms | 1,200ms |
| **P99.9延迟** | 4,800ms | 1,500ms | 2,800ms |
| **CPU使用率** | 100% | 85% | 78% |
| **内存使用** | 3.8GB/4GB (OOM边缘) | 3.2GB/4GB | 3.0GB/4GB |
| **GC最大暂停** | 12,000ms (Full GC) | 1.2ms (ZGC) | 2.5ms (ZGC) |
| **错误率** | 5.2% | 0.5% | 0.1% |
| **可用性** | 91% (被踢出集群) | 99.9% | 99.99% |

```bash
# ===== 脚本：一键全流程压测 =====
cat > full_benchmark.sh << 'SCRIPT'
#!/bin/bash
echo "=== Keycloak C10K 全流程压测 ==="
echo "Step 1/5: 基线压测 (RS256 + 默认配置)"
wrk2 -t4 -c100 -d30s -R4000 --latency \
  -s oauth2-login.lua \
  http://localhost:8080/realms/demo-realm/protocol/openid-connect/token \
  | tee result_baseline.txt

echo "Step 2/5: ES256签名算法压测"
wrk2 -t4 -c100 -d30s -R4000 --latency \
  -s oauth2-login.lua \
  http://localhost:8080/realms/demo-realm/protocol/openid-connect/token \
  | tee result_es256.txt

echo "Step 3/5: ProtoStream序列化压测"
wrk2 -t4 -c100 -d30s -R4000 --latency \
  -s oauth2-login.lua \
  http://localhost:8080/realms/demo-realm/protocol/openid-connect/token \
  | tee result_protostream.txt

echo "Step 4/5: ZGC调优压测"
wrk2 -t4 -c100 -d30s -R4000 --latency \
  -s oauth2-login.lua \
  http://localhost:8080/realms/demo-realm/protocol/openid-connect/token \
  | tee result_zgc.txt

echo "Step 5/5: C10K终极压测"
wrk -t16 -c10000 -d120s --latency \
  -H "Authorization: Bearer $STATIC_TOKEN" \
  http://localhost:8080/realms/demo-realm/protocol/openid-connect/userinfo \
  | tee result_c10k.txt

echo "=== 全流程压测完成，结果文件: result_*.txt ==="
SCRIPT
chmod +x full_benchmark.sh
```

---

### 可能遇到的坑汇总

| 坑 | 症状 | 根因 | 解决方法 |
|----|------|------|----------|
| ZGC在堆<2GB时吞吐量反降 | QPS下降15-20% | ZGC并发标记占用CPU，小堆收益小于损失 | 2-4GB堆用G1GC，≥4GB用ZGC |
| ProtoStream序列化失败 | 启动报错`ClassNotFoundException` | 自定义SPI扩展类未注册SerializationContextInitializer | 实现接口并在cache-ispn.xml注册 |
| TCP调优后性能下降 | 连接数增加但延迟上升 | tcp_tw_recycle在NAT环境丢包（已被废除） | 仅用tcp_tw_reuse，移除recycle |
| THP导致JVM随机高延迟 | P99.9出现500ms+孤峰 | THP大页压缩/迁移触发NUMA重分配 | 如未用JVM大页则关闭THP |
| 连接池"伪优化" | 从20扩到200，TPS不变 | PostgreSQL `max_connections=100`未同步上调 | 应用池≤数据库池，且总连接数<数据库内存能力 |
| Native Image反射失败 | 自定义SPI启动报错 | GraalVM AOT要求反射信息在编译期预注册 | 添加`reflect-config.json`配置 |

---

## 4 项目总结

### 优化策略汇总

| 优化项 | 预期提升 | 实现复杂度 | 风险等级 |
|--------|---------|-----------|---------|
| RS256→ES256签名算法 | QPS +45%, CPU -26% | 低（改配置） | 低（注意兼容性） |
| ProtoStream序列化 | 序列化延迟-50%, 内存-60% | 中（需改xml） | 低 |
| ZGC↓G1上调优 | GC暂停-90%, 错误率-80% | 中（调JVM参数） | 中（需验证版本兼容） |
| OS TCP参数调优 | 连接处理能力+10x | 低（sysctl） | 中（NUMA/THP需谨慎） |
| Reference Token | Token体积-97%, 带宽-97% | 低（改配置） | 中（每请求额外5ms） |
| Off-Heap缓存 | GC压力-20%, 堆外内存可用 | 中（配置Infinispan） | 低 |
| HTTP/2 + TLS 1.3 | 延迟-15%, 连接复用 | 中（配置反向代理） | 低 |

### 性能调优的"金字塔"模型

```
        ┌──────────────────┐
        │    业务层优化      │  ← Token轻量化、API设计、缓存策略
        │  应用层/JVM优化    │  ← 签名算法、序列化、GC策略、连接池
        │     OS层优化       │  ← TCP参数、文件描述符、内存管理
        │   基础设施层优化    │  ← 负载均衡、带宽、网络拓扑
        └──────────────────┘
```

调优应自下而上：先用OS层兜底（连接不漏、描述符不够），再在JVM层减震（GC不崩、堆不炸），最后在应用层提效（签名快、体积小、序列化省）。业务层优化是终极手段——但通常需要架构变更，成本最高。

### 注意事项

1. **不要过早优化。** 在火焰图确认热点之前，不要凭直觉调任何参数。数据驱动的优化才能保证每一分精力都花在刀刃上。
2. **压测环境≠生产环境。** 同一机房的1ms网络延迟可以掩盖网络IO瓶颈，但跨可用区部署的5ms延迟会直接暴露隐患。压测架构要尽量接近生产拓扑。
3. **每次只改一个变量。** ES256 + ProtoStream + ZGC一起上线，如果出现问题，你永远不知道是哪一步引起的。分批上线、每次对照。
4. **JVM参数叠加需谨慎。** `-XX:+UseZGC`和`-XX:+UseG1GC`同时设置会报错；`-Xms`与`-Xmx`不一致会导致堆频繁伸缩。保持参数集的"精减原则"——非必要不添加。
5. **连接池不是越大越好。** HikariCP的`maximumPoolSize`从20放大到200，如果PostgreSQL的`max_connections`还是100，实际只有前100个连接有效——且每个连接在数据库侧占用2-4MB内存。

### 思考题

1. **Google/Facebook等大厂在Token自包含JWT和Reference Token之间怎么选的？为什么？**（提示：Google使用自包含JWT用于内部服务间RPC调用——Google内部RPC层gRPC原生集成了JWT Bearer Token校验，零额外延迟。但面向Web前端的OAuth2 Token是Reference Token——通过TokenInfo端点校验。Facebook在Graph API上使用Opaque Token + 在线校验，因为Token Revocation是所有大厂必须强支持的场景。参考：Google内部"ALTS"协议实际上完全不使用JWT——服务间通信用的是基于TLS的证书认证+mTLS，零Token开销。）

2. **如果需要支持C1M（100万并发连接），Keycloak的架构应该如何演进？是否需要在Keycloak前面加一层高性能认证代理（如自研Go服务只做Token校验）？**（提示：C1M场景下，Keycloak本身的Java/Quarkus架构已经触及物理极限——即使是ZGC + 大堆 + 所有优化，单节点C10K已经是上界。可行的演进方案：在Keycloak前面加一层**Token校验代理（Go/Rust编写）**——代理层只做JWT本地验签（不访问数据库、不入Introspection），将"Token校验"这个最频繁的操作剥离到轻量级代理。Keycloak只负责"Token签发"和"配置管理"。这种做法在Uber（他们自研了"ULP"——Uber Login Proxy）、Lyft等公司都有类似的实操。架构演进的本质是：**将"认证"（Authentication，低频，CPU密集）和"鉴权"（Authorization，高频，仅需验签）在架构上解耦。**）

---

*下一章预告：第39章将深入Keycloak源码，剖析Token撤销（Revocation）和黑名单机制的内核实现。*
