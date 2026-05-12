# 第29章：性能压测与JVM调优实战

## 1 项目背景

某电商平台计划2025年"618"大促上线新版统一认证中心，底层采用Keycloak 26.1。在上线前两周的全链路压测中，运维团队发现Keycloak在低并发下表现平稳（200 VUs时P99约600ms），但当并发爬升到5000时，情况急转直下——P99延迟飙到8秒，CPU持续100%，Nginx upstream大量返回502和504，用户登录页面频频超时。

运维团队的第一反应是"加资源"——将Keycloak节点从4核8G升级到16核32G。但升级后的压测结果令人大跌眼镜：QPS只从800提升到约1050，提升幅度仅30%，远低于线性扩展预期。16核CPU的使用率却依然在90%以上打转，32G内存中堆使用不到一半就频繁触发Full GC。钱花出去了，瓶颈纹丝不动。

深入排查后，真正的痛点逐一浮出水面：

**痛点一：数据库连接池默认值不堪重负。** Keycloak默认HikariCP最大连接数仅为20（旧版本甚至更小）。5000并发下，每个请求都需要与数据库交互（查用户、验密码、写事件），连接池瞬间耗尽，剩余请求在`HikariPool-1 - Connection is not available`的等待队列中排队，超时后抛出异常——这就是502的根源。盲目加大JVM内存并不能变出更多数据库连接。

**痛点二：JVM GC行为被完全忽视。** 运维从未调整过JVM参数，使用的是Keycloak容器的默认`-Xmx512m -Xms64m`。512MB堆在5000并发下远远不够，GC几乎不停地在做Full GC，每次停顿800-1200ms。G1GC虽然开启，但`MaxGCPauseMillis`等关键参数全是默认值，GC线程像无头苍蝇一样乱转。堆内存的动态伸缩（-Xms与-Xmx不一致）导致频繁的堆扩容和收缩，进一步拖累性能。

**痛点三：Token签名算法的CPU消耗被低估。** 该平台使用RS256（RSA 2048位）作为默认Token签名算法。在火焰图中，仅RSA签名运算就占用了约35%的CPU时间。RS256的安全性毋庸置疑，但其非对称加密的CPU开销是HS256的3-5倍，在5000并发登录场景下，每秒上千次RSA签名直接把CPU吃满。团队从未考虑过ES256（ECDSA）这个兼顾性能与安全的中间选项。

**痛点四：Infinispan缓存的序列化开销。** Keycloak集群节点间通过Infinispan同步Session和认证状态。默认使用Java原生序列化（`ObjectOutputStream`），每次序列化一个Session对象约消耗1-2ms CPU时间，在高并发下累计开销不可忽视。ProtoStream二进制序列化可将开销降低40-60%，但团队完全不知道这个选项存在。

**痛点五：压测方法不科学。** 之前的"压测"是用curl写了个shell脚本循环调登录接口，单次请求看延迟——这只能测通断，完全无法反映持续并发下的真实性能。没有阶梯式加压、没有多用户数据池、没有混合场景（登录+Token校验）、没有P95/P99统计。

> **一句话总结**：盲目加资源不能解决性能问题。不理解JVM GC行为、不调数据库连接池、不选对Token算法、不优化序列化——加再多CPU和内存都只是徒增账单。

---

## 2 项目设计——剧本式交锋对话

**小胖**（瘫在工位上，盯着Grafana监控大盘上那条一路飙升的红色P99曲线）：大师，这不科学啊！我们给Keycloak换上了16核32G的"顶配"，QPS才涨了30%，钱全打水漂了。这不就跟高速公路上堵车一样——我以为多修几条车道（加CPU）就能跑更快，结果收费站（瓶颈）还是只开了两个窗口，车全堵在缴费口了！

**大师**（指着监控大盘的DB连接池满指标）：你这个比喻开头开对了，但结论偏了。你加的是"高速公路两侧的绿化带"——堆内存变大，堆里能同时停更多车，但车还是得排队过同一个收费站（数据库连接池）。你要修的不是更多车道，是在收费站多开几个收费窗口（扩大连接池），再让收费员换ETC自动抬杆（缩短每辆车的缴费时间）。

> **大师技术映射**：堆内存 → 高速公路路面宽度，决定能同时容纳多少"在途请求"。数据库连接池 → 收费站窗口数，决定单位时间能完成多少"数据库交互"。CPU核心 → 收费员的处理速度。三个维度独立调优，一个维度的瓶颈会卡住全局吞吐。

---

**小白**（翻着《深入理解Java虚拟机》，在笔记本上画了一张JVM内存结构图）：大师，我把JVM参数捋了一遍。`-Xmx`和`-Xms`设成一样我能理解——避免堆动态伸缩。但G1GC和ZGC到底怎么选？我看JDK 17之后ZGC也很成熟了，号称亚毫秒级停顿。还有那个G1的`InitiatingHeapOccupancyPercent`，为什么默认45%而不是更高？提前触发并发标记不是浪费CPU吗？

**大师**：三个好问题，一个一个拆。

**第一，G1GC vs ZGC。** G1GC的设计哲学是"分代收集+分区"，适合4GB到32GB的堆，暂停时间通常在几十到几百毫秒，对吞吐量的影响控制在10%以内。ZGC的设计哲学是"全并发+染色指针"，即使32GB甚至1TB的堆也能保持在亚毫秒级停顿——但它对吞吐量的影响比G1GC大约5-10%。Keycloak的场景，如果你的堆在2-8GB之间且对延迟容忍度在200ms左右，G1GC是最佳性价比选择。如果你的场景是金融交易认证、要求P99 < 50ms且愿意牺牲一点吞吐量，就上ZGC。ZGC的额外代价是CPU使用率会更高（并发标记写屏障的开销），在CPU已经瓶颈的场景下反而雪上加霜。

**第二，InitiatingHeapOccupancyPercent（IHOP）为什么是45%。** 这个参数控制G1什么时候启动并发标记周期。设得太高（比如80%）意味着等到堆快满时才标记，标记速度跟不上分配速度——结果就是并发标记失败，退化为Full GC，暂停时间暴涨。设得太低（比如20%）意味着频繁触发标记周期，浪费CPU且拖慢吞吐。45%是一个合理平衡：当老年代占45%时开始标记，JDK会根据实际分配速率动态调整（`-XX:+G1UseAdaptiveIHOP`默认开启），实际生效值可能在35%-60%之间浮动。关键记住：IHOP设太高 = Full GC风险，设太低 = 吞吐量下降。

**第三，GC日志是性能调优的眼睛。** 不看GC日志调JVM，跟蒙着眼睛开车没区别。`-Xlog:gc*:file=/tmp/gc.log:time,tags,level`一开，你就知道每次GC的类型、耗时、回收量、触发原因。推荐用`gceasy.io`或`GCViewer`可视化分析——几分钟就能发现是Young GC太频繁（堆太小）还是Mixed GC回收效率低（老年代碎片化）。

> **大师技术映射**：G1GC → 商场每天定点清垃圾车（并发标记+分区回收），有规律、可控、但清一次要几分钟。ZGC → 商场雇了一群清洁工实时跟在顾客后面扫地（全并发），几乎不挡路但人力成本高。IHOP → 清垃圾的触发阈值——45%满就派人去清，再晚可能来不及倒垃圾。

---

**小胖**（第二轮，指着数据库监控面板上那条"连接池队列长度"曲线）：大师，数据库连接池这个坑我算是踩明白了。但你说最大连接数 = (核心数 × 2) + 有效磁盘数——这个公式我见过，为啥这么算？还有，Keycloak的HikariCP和PostgreSQL前面的pgBouncer是什么关系？两层连接池不会打起来吗？

**大师**：这个公式来自PostgreSQL社区的长期经验总结，是起跑线不是终点线。

`最大连接数 ≈ (CPU核心数 × 2) + 有效SSD磁盘数`。背后的逻辑是：PostgreSQL是进程模型（每个连接fork一个OS进程），CPU核心数决定了并行处理能力——一般一个CPU核心能高效处理2-3个并发连接。磁盘的IOPS决定了有多少连接可以同时等待IO完成。SSD的`effective_io_concurrency`默认是200，意味着一个查询可以同时发起200个预读请求。所以对于一台8核+2块SSD的机器：8×2+2=18个连接作为起点，然后在压测中逐步上调，观察TPS和P99延迟的拐点——拐点之后再加连接只会增加上下文切换开销，不再提升吞吐。

**小胖**：那HikariCP + pgBouncer两层连接池呢？

**大师**：不冲突，各管各的层。HikariCP在Keycloak应用进程内部——每个Keycloak节点维护自己的连接池，负责"我的请求线程如何快速拿到数据库连接"。pgBouncer在PostgreSQL前面——负责"成百上千个客户端连接怎么被聚合成少量PostgreSQL后端连接"。打个比方：HikariCP是每个部门的前台小妹，pgBouncer是整栋楼的物业总台。部门小妹帮你预约会议室（借连接），但实际打通电话的是物业总台统一的外线。HikariCP Max Pool Size建议设为20-40（应用侧高频复用），pgBouncer的`default_pool_size`设为25、`max_db_connections`不超过PostgreSQL的`max_connections`。

---

**小白**（掏出压测脚本的原型图）：大师，Token签名算法这个我深挖了一下。RS256（RSA+SHA256）、ES256（ECDSA P-256+SHA256）、HS256（HMAC+SHA256），三者的性能差多少？

**大师**（在白板上画了一张对比表）：

| 算法 | 类型 | 密钥长度 | 签名速度 | 验签速度 | 密钥共享 | 适用场景 |
|------|------|---------|---------|---------|---------|---------|
| RS256 | 非对称RSA | 2048位 | 慢（基准1x） | 快 | 不需要 | 传统OAuth2，兼容性最好 |
| ES256 | 非对称ECDSA | P-256 | 快（约3-5x RS256） | 快 | 不需要 | 新项目首选，安全等效128位对称密钥 |
| HS256 | 对称HMAC | 256位 | 极快（约10x RS256） | 极快 | 必须共享 | 内部微服务间通信，网关后 |

ES256用椭圆曲线P-256，数学上比RSA 2048更高效——用更短的密钥达到同等的安全强度。在Keycloak上，只需在Realm的Keys → Providers中将算法从`RS256`改为`ES256`并重新生成密钥对。实测签名TPS：RS256约500次/秒/核，ES256约2500次/秒/核。不过注意：ES256的兼容性略逊于RS256——部分老旧的JWT库（尤其.NET Framework 4.5以下）不支持ES256。

> **大师技术映射**：RS256 → 老式机械密码锁——笨重但家家都有，钥匙不对撬不开。ES256 → 指纹锁——轻巧、快、安全等级一样高，但部分老门框装不上去。HS256 → 夫妻共用的那一把家门钥匙——快是快，但谁拿到钥匙都能开门，适合"一家人"之间用。

---

## 3 项目实战

### 环境准备

- **Keycloak 26.1**：`quay.io/keycloak/keycloak:26.1`（Docker部署）
- **PostgreSQL 16**：作为Keycloak后端数据库
- **k6 v0.54+**：开源负载测试工具（`https://k6.io`，macOS `brew install k6`，Linux `apt install k6`）
- **async-profiler 3.0**：低开销CPU/内存采样分析工具（`https://github.com/async-profiler/async-profiler`）
- **Docker Compose v2.x**：编排Keycloak + PostgreSQL环境
- 至少8GB可用内存用于压测环境

---

### 步骤1：k6压测脚本编写

**目标**：编写一个模拟真实OAuth2密码登录+Token校验混合场景的k6脚本，支持阶梯式加压。

```javascript
// oauth2-login-test.js
import http from 'k6/http';
import { check, sleep, group } from 'k6';

const KEYCLOAK_URL = __ENV.KEYCLOAK_URL || 'http://localhost:8080';
const REALM = __ENV.REALM || 'demo-realm';
const CLIENT_ID = 'oms-frontend';
const CLIENT_SECRET = __ENV.CLIENT_SECRET || '';

// 模拟用户池 —— 生产环境应从CSV文件加载（k6支持 SharedArray + papaparse）
function randomUser() {
    const users = [];
    for (let i = 1; i <= 2000; i++) {
        users.push({ username: `testuser${i}`, password: 'Test@123' });
    }
    return users[Math.floor(Math.random() * users.length)];
}

export const options = {
    stages: [
        { duration: '1m', target: 100 },   // 爬升至100 VUs
        { duration: '3m', target: 500 },   // 爬升至500 VUs
        { duration: '5m', target: 1000 },  // 爬升至1000 VUs
        { duration: '5m', target: 1000 },  // 保持1000 VUs 5分钟
        { duration: '2m', target: 0 },     // 梯度下降至0
    ],
    thresholds: {
        'http_req_duration': ['p(95)<3000'],   // P95 < 3s
        'http_req_failed': ['rate<0.01'],       // 错误率 < 1%
        'http_req_duration{name:login}': ['p(95)<2000'],
    },
};

export default function () {
    const user = randomUser();

    // 场景1：密码登录（获取Token）
    group('login', () => {
        const loginRes = http.post(
            `${KEYCLOAK_URL}/realms/${REALM}/protocol/openid-connect/token`,
            {
                client_id: CLIENT_ID,
                username: user.username,
                password: user.password,
                grant_type: 'password',
                scope: 'openid',
            },
            { tags: { name: 'login' } }
        );

        const loginOk = check(loginRes, {
            'login status 200': (r) => r.status === 200,
            'has access_token': (r) => {
                try { return r.json('access_token') !== ''; } catch { return false; }
            },
        });

        if (!loginOk) return;

        const token = loginRes.json('access_token');

        // 场景2：Token校验（模拟Resource Server行为）
        if (token && CLIENT_SECRET) {
            group('token_introspect', () => {
                const introspectRes = http.post(
                    `${KEYCLOAK_URL}/realms/${REALM}/protocol/openid-connect/token/introspect`,
                    {
                        client_id: 'oms-backend',
                        client_secret: CLIENT_SECRET,
                        token: token,
                    },
                    { tags: { name: 'introspect' } }
                );

                check(introspectRes, {
                    'token active': (r) => {
                        try { return r.json('active') === true; } catch { return false; }
                    },
                });
            });
        }

        // 场景3：模拟用户信息获取（可选，更接近真实流量）
        if (token) {
            group('userinfo', () => {
                const userinfoRes = http.get(
                    `${KEYCLOAK_URL}/realms/${REALM}/protocol/openid-connect/userinfo`,
                    {
                        headers: { Authorization: `Bearer ${token}` },
                    },
                    { tags: { name: 'userinfo' } }
                );
                check(userinfoRes, { 'userinfo status 200': (r) => r.status === 200 });
            });
        }
    });

    sleep(Math.random() * 2 + 1); // 模拟真实用户思考间隔 1-3秒
}
```

---

### 步骤2：执行基线压测并收集结果

**目标**：在默认配置下运行压测，获取性能基线数据。

```bash
# 启动Keycloak（默认JVM参数）
docker compose up -d

# 等待Keycloak完全启动
sleep 30

# 运行压测，输出JSON结果
k6 run --out json=results.json oauth2-login-test.js

# 同时输出摘要报告
k6 run --summary-export=summary.json oauth2-login-test.js
```

**基线压测摘要（默认配置：-Xmx512m，连接池20，RS256）**：

```
     data_received..................: 45 MB
     data_sent......................: 12 MB
     http_req_duration.............: avg=850ms  min=120ms  med=620ms  p(90)=1800ms  p(95)=2800ms  p(99)=5500ms
     http_req_failed...............: 0.52%
     http_reqs.....................: 45000
     vus............................: 1000
     vus_max........................: 1000
     iterations....................: 12000
     checks........................: 92.3%

     // 分类延迟
     login_duration................: avg=1100ms  p(95)=3500ms  p(99)=6800ms
     introspect_duration..........: avg=420ms   p(95)=1100ms  p(99)=2500ms
```

---

### 步骤3：JVM启动参数调优

**目标**：针对Keycloak的工作负载特征调优JVM参数，降低GC暂停时间。

```bash
# 在启动Keycloak时追加以下JVM参数
JAVA_OPTS_APPEND="
  -Xms2048m -Xmx2048m                # 堆固定2GB，消除动态伸缩开销
  -XX:+UseG1GC                       # 使用G1垃圾回收器
  -XX:MaxGCPauseMillis=200           # GC暂停目标 < 200ms
  -XX:G1HeapRegionSize=8m            # G1区域大小（2GB堆=256个区域，合适）
  -XX:InitiatingHeapOccupancyPercent=45  # 老年代45%触发并发标记
  -XX:G1ReservePercent=10            # 预留10%堆空间用于晋升
  -XX:+ParallelRefProcEnabled        # 并行处理引用对象
  -XX:+UseStringDeduplication        # 字符串去重（Keycloak有大量重复Token）
  -XX:G1MixedGCCountTarget=8         # 每次Mixed GC回收8个分区
  -XX:+HeapDumpOnOutOfMemoryError    # OOM自动dump
  -XX:HeapDumpPath=/tmp/heapdump.hprof
  -XX:MetaspaceSize=256m             # 元空间初始（Keycloak类较多）
  -XX:MaxMetaspaceSize=512m          # 元空间上限
  -Xlog:gc*:file=/tmp/gc.log:time,tags,level:filecount=5,filesize=10m  # GC日志滚动
"
```

**Keycloak Quarkus版本配置方式**（在`conf/keycloak.conf`或环境变量中设置）：

```ini
# conf/keycloak.conf
http-enabled=true
hostname-strict=false
proxy-headers=xforwarded

# JVM参数在启动脚本中通过 JAVA_OPTS_APPEND 环境变量追加
```

**Docker Compose配置示例**：

```yaml
keycloak:
  image: quay.io/keycloak/keycloak:26.1
  environment:
    KC_BOOTSTRAP_ADMIN_USERNAME: admin
    KC_BOOTSTRAP_ADMIN_PASSWORD: admin123
    KC_DB: postgres
    KC_DB_URL: jdbc:postgresql://postgres:5432/keycloak
    KC_DB_USERNAME: keycloak
    KC_DB_PASSWORD: keycloak123
    JAVA_OPTS_APPEND: >
      -Xms2048m -Xmx2048m
      -XX:+UseG1GC
      -XX:MaxGCPauseMillis=200
      -XX:G1HeapRegionSize=8m
      -XX:InitiatingHeapOccupancyPercent=45
      -XX:+ParallelRefProcEnabled
      -XX:+UseStringDeduplication
      -XX:+HeapDumpOnOutOfMemoryError
      -XX:HeapDumpPath=/tmp/heapdump.hprof
      -XX:MetaspaceSize=256m
      -XX:MaxMetaspaceSize=512m
      -Xlog:gc*:file=/tmp/gc.log:time,tags,level
  command: start --optimized
  ulimits:
    nofile: 65535:65535
  deploy:
    resources:
      limits:
        cpus: '4'
        memory: 4G
```

**GC日志分析**（调优前后对比）：

```
# 调优前（默认 -Xms64m -Xmx512m）
[2025-01-15T10:32:15.123+0800] GC(128) Pause Full (Allocation Failure) 480M->450M(512M) 823.5ms
[2025-01-15T10:32:17.891+0800] GC(129) Pause Full (Allocation Failure) 460M->445M(512M) 912.3ms
→ Full GC 频繁、每次近1秒，用户请求直接超时

# 调优后（-Xms2048m -Xmx2048m + G1GC参数）
[2025-01-15T11:15:30.456+0800] GC(42) Pause Young (Normal) (G1 Evacuation Pause) 1200M->850M(2048M) 28.3ms
[2025-01-15T11:15:35.789+0800] GC(43) Pause Young (Concurrent Start) (G1 Humongous Allocation) 1400M->950M(2048M) 35.1ms
[2025-01-15T11:16:10.123+0800] GC(44) Pause Young (Mixed) (G1 Evacuation Pause) 1600M->700M(2048M) 85.2ms
→ Young GC < 50ms，Mixed GC < 100ms，零Full GC
```

---

### 步骤4：数据库连接池参数调优实验

**目标**：通过压测找到HikariCP的最优连接池大小。

```bash
# 实验：分别测试不同连接池大小的QPS和P99延迟
for pool_size in 10 20 30 50 80 100; do
    echo "=== Testing with pool_size=${pool_size} ==="
    export KC_DB_POOL_MAX_SIZE=$pool_size
    export KC_DB_POOL_MIN_SIZE=$((pool_size / 4))
    docker compose up -d
    sleep 30
    k6 run --duration 2m --vus 500 oauth2-login-test.js \
        --summary-export="summary-pool-${pool_size}.json"
    docker compose down
    sleep 10
done
```

**实验结果汇总**：

| 连接池大小 | QPS | P50延迟 | P99延迟 | 活跃连接数 | 备注 |
|-----------|-----|---------|---------|-----------|------|
| 10 | 450 | 1800ms | 3200ms | 10 (满) | 严重排队，大量超时 |
| 20 | 900 | 950ms | 2000ms | 20 (满) | 仍不足，HikariCP等待队列长 |
| 30 | 1200 | 600ms | 1200ms | 25 | 开始缓解 |
| 50 | 1500 | 380ms | 900ms | 40 | **性能拐点**，此时瓶颈移到CPU |
| 80 | 1550 | 360ms | 850ms | 55 | 边际收益递减 |
| 100 | 1580 | 355ms | 840ms | 58 | 几乎无提升，额外连接浪费内存 |

**结论**：对该环境（8核16G），50是连接池的"甜点值"——超过50后CPU成为新瓶颈，连接数增加不再提升QPS。最终配置：

```yaml
keycloak:
  environment:
    KC_DB_POOL_MAX_SIZE: 50
    KC_DB_POOL_MIN_SIZE: 10
    KC_DB_POOL_INITIAL_SIZE: 10
    KC_DB_POOL_MAX_WAIT: 10000      # 等待连接超时10秒
```

---

### 步骤5：生成火焰图定位热点函数

**目标**：使用async-profiler定位CPU热点函数，精准识别性能瓶颈。

```bash
# 方法一：使用async-profiler（推荐，开销 < 1%）
# 将async-profiler挂载到Docker容器
docker cp async-profiler-3.0-linux-x64.tar.gz keycloak-1:/tmp/
docker exec keycloak-1 tar -xzf /tmp/async-profiler-3.0-linux-x64.tar.gz -C /tmp/

# 启动CPU采样（60秒），生成火焰图
docker exec keycloak-1 /tmp/async-profiler-3.0-linux-x64/profiler.sh \
    -d 60 \
    -f /tmp/flamegraph-cpu.html \
    -e cpu \
    1   # PID=1 是容器主进程（Keycloak JVM）

# 方法二：使用JDK自带的JFR（Java Flight Recorder）
docker exec keycloak-1 jcmd 1 JFR.start \
    duration=60s \
    filename=/tmp/recording.jfr \
    settings=profile

# 从容器中取出火焰图或JFR文件
docker cp keycloak-1:/tmp/flamegraph-cpu.html ./analysis/
docker cp keycloak-1:/tmp/recording.jfr ./analysis/
```

**火焰图分析结果**（使用RS256签名算法时）：

```
热点函数栈采样（CPU时间占比）：
├── 35.2%  java.security.Signature$Delegate.engineSign()
│   └── RSA 2048位签名运算（Token生成时触发）
├── 20.1%  com.fasterxml.jackson.databind.ObjectMapper.writeValueAsString()
│   └── JSON序列化/反序列化（Token Payload、UserRepresentation）
├── 15.3%  org.hibernate.SQLQuery.executeQuery()
│   └── 数据库查询（查用户、查密码凭证、写事件）
├── 12.8%  org.infinispan.marshall.exts.*
│   └── Infinispan对象序列化（Java原生序列化）
├──  8.5%  org.keycloak.models.utils.KeycloakModelUtils
│   └── 模型转换、属性校验
└──  8.1%  其他（NIO、日志、网络IO）
```

**分析结论**：
1. **RSA签名是头号热点**（35%）→ 优先评估ES256替代方案
2. **JSON序列化（20%）** → 评估Jackson Afterburner或开启`USE_FAST_DOUBLE_WRITER`
3. **Infinispan序列化（13%）** → 从Java Serialization切换到ProtoStream

---

### 步骤6：优化后对比验证

**优化清单及执行效果**：

| 优化项 | 优化前 | 优化后 | 提升效果 |
|--------|--------|--------|---------|
| JVM参数（堆2G + G1GC调优） | GC暂停800-1200ms | GC暂停28-150ms | **GC暂停降低85%** |
| 数据库连接池（20→50） | QPS 800 | QPS 1500 | **QPS提升87%** |
| Token签名算法（RS256→ES256） | RSA签名35% CPU | ECDSA签名12% CPU | **CPU降低23%** |
| Infinispan序列化（Java→ProtoStream） | 序列化开销13% | 序列化开销7% | **序列化开销降低46%** |
| MetaspaceSize预设 | 频繁Metaspace扩容 | 一次分配到位 | 消除元空间GC |

**Infinispan ProtoStream序列化配置**（在`cache-ispn.xml`中启用）：

```xml
<!-- 在 <cache-container> 下配置 -->
<serialization marshaller="org.infinispan.commons.marshall.ProtoStreamMarshaller">
    <context-initializer class="org.keycloak.models.sessions.infinispan.initializer.KeycloakSessionInitializer"/>
</serialization>
```

**优化后全量压测结果**：

```bash
k6 run --vus 2000 --duration 5m oauth2-login-test.js
```

```
     data_received..................: 88 MB
     http_req_duration.............: avg=320ms  min=60ms  med=240ms  p(90)=680ms  p(95)=1100ms  p(99)=2500ms
     http_req_failed...............: 0.12%
     http_reqs.....................: 180000
     vus_max........................: 2000
     iterations....................: 48000
     checks........................: 98.7%

     // P99从5500ms → 2500ms（降低55%），错误率从0.52% → 0.12%
```

---

### 可能遇到的坑

**坑1：Docker环境中的压测受宿主机资源限制。** Docker Compose默认的CPU和内存limit可能远低于宿主机配置。在Linux上可以用`--cpus=4 --memory=4g`显式分配，在macOS上Docker Desktop的VM只分配了宿主机一部分资源（默认约50%）。务必检查`docker stats`确认Keycloak实际可用资源。

**坑2：Localhost压测忽略了网络延迟。** k6和Keycloak部署在同一台机器上，RTT几乎为0，QPS虚高。生产环境建议将k6运行在独立机器上，或使用`tc`（Traffic Control）工具模拟网络延迟：`tc qdisc add dev eth0 root netem delay 10ms`。

**坑3：password grant_type不是生产主流。** OAuth2密码模式已被标记为Deprecated，生产环境更多用Authorization Code Flow + PKCE。但作为性能基准测试，password grant是最简路径——每轮一次HTTP POST即可完成认证。要模拟真实流量，应在压测中混合Authorization Code（多次HTTP往返）。

**坑4：节点数增加不等于线性性能提升。** 从1节点到3节点，Infinispan Session同步会引入额外网络传输和序列化开销，实测3节点集群的QPS约为单节点的2.2-2.5倍，而非3倍。盲目横向扩展节点可能不会带来预期收益。

**坑5：压测数据预热期被忽略。** 第一次压测时JIT编译尚未完成、Infinispan缓存为空、数据库Buffer Pool冷启动，结果会显著差于稳态。务必在正式压测前跑一段"预热"（约5-10分钟，低并发），等JIT编译完毕、缓存预热后再取数据。

---

### 测试验证

**完整验证检查清单**：

```bash
# 1. 验证JVM参数是否生效
docker exec keycloak-1 java -XX:+PrintFlagsFinal -version 2>&1 | grep -E "MaxHeapSize|UseG1GC|MaxGCPauseMillis"

# 2. 验证连接池当前状态
# 在Keycloak管理控制台 → Server Info → Providers → 查看 datasource 的 activeCount / idleCount

# 3. 验证Token签名算法
curl -s "http://localhost:8080/realms/${REALM}/.well-known/openid-configuration" | jq '.id_token_signing_alg_values_supported'
# 预期输出: ["ES256"]

# 4. 验证Infinispan ProtoStream是否生效
# 查看Keycloak启动日志，搜索 "ProtoStream"
docker logs keycloak-1 2>&1 | grep -i protostream

# 5. 执行冒烟压测
k6 run --vus 50 --duration 30s oauth2-login-test.js
# 确保无错误后执行完整阶梯压测
```

---

## 4 项目总结

### 优点与缺点对比：Keycloak vs Auth0 vs 自建认证的性能维度

| 维度 | Keycloak | Auth0（SaaS） | 自建认证（Ory/Kratos等） |
|------|----------|---------------|------------------------|
| 性能调优自由度 | ***** 完全可控——JVM、DB、缓存、算法 | ** 仅限Rate Limit/Tier升级 | **** 取决于技术栈 |
| 部署成本 | ** 需自运维节点和数据库 | ***** 零运维、按量付费 | * 从头开发维护 |
| 水平扩展能力 | **** 支持集群，Infinispan同步有上限 | ***** 自动弹性伸缩 | *** 需自建 |
| Token签名性能 | *** RS256默认，可切ES256优化 | **** 可配置算法 | **** 可自由选择 |
| GC调优难度 | *** 需JVM经验 | N/A（无服务器） | 取决于语言 |
| 数据库连接池 | **** HikariCP成熟，双层池化 | N/A | 取决于实现 |
| 离线/私有部署 | ***** 完全本地化 | ** 仅Private Cloud | ***** 完全可控 |
| 性能监控深度 | ***** JMX + JFR + 火焰图全链路 | *** Dashboard有限的指标 | ** 需自建 |

### 适用场景

- **大规模用户认证（10万+ DAU）**：电商、社交平台等海量用户的统一登录入口，Keycloak经调优后单节点可支撑2000-3000 QPS登录请求。
- **高频率Token校验（100万+/min）**：微服务网关对每个API请求做Token Introspect，需要极低的校验延迟（P99 < 50ms）。
- **企业内网SSO（多系统集成）**：需要SAML、OIDC、LDAP多协议支持，且对性能有较高要求（万人同时在线）。
- **合规要求本地部署**：金融、政务等不允许认证数据上云，必须私有化部署并进行性能压测达标。
- **不适用场景**：团队无JVM运维经验（GC调优需要专业知识）；用户量 < 1000的初创项目（Keycloak的调优成本高于收益）；纯移动端App且不需要SSO（Firebase Auth等更轻量）。

### 性能调优Checklist

| 层级 | 检查项 | 建议值 | 验证方法 |
|------|--------|--------|---------|
| JVM | -Xms = -Xmx | 2GB-8GB（节点内存的50-70%） | `jcmd <pid> VM.flags` |
| JVM | GC选择 | G1GC（2-32GB堆）/ ZGC（低延迟） | `jstat -gc <pid> 1s` |
| JVM | GC暂停目标 | MaxGCPauseMillis=200 | GC日志分析 |
| 数据库 | HikariCP Max Size | 30-80（按公式计算后压测调优） | Keycloak Server Info |
| 数据库 | pgBouncer启用 | `pool_mode=transaction` | `SHOW POOLS;` |
| 缓存 | Infinispan序列化 | ProtoStream替代Java Serialization | 启动日志确认 |
| 签名 | Token算法 | ES256替代RS256（评估兼容性） | `.well-known/openid-configuration` |
| 系统 | 文件描述符上限 | ulimit -n 65535 | `ulimit -n` |
| 系统 | TCP内核参数 | `net.core.somaxconn=4096` | `sysctl net.core.somaxconn` |

### 常见踩坑经验

**故障一：压测脚本不真实导致数据虚高。** 某团队压测时所有VU使用同一账号登录——Keycloak的User Cache命中率接近100%，数据库几乎无压力，QPS虚高到3000。上线后真实多用户登录直接打回原形（600 QPS）。教训：压测用户池必须足够大（>1000个不同用户），才能模拟真实缓存未命中场景。

**故障二：GC日志被忽视导致线上雪崩。** 某次大促中Keycloak集群不定期出现10-15秒的STW（Stop The World）暂停，用户登录全部超时。事后查GC日志发现是G1的Humongous Allocation触发了Full GC——Infinispan的Session对象超过Region大小的50%被视为巨型对象，连续巨型对象分配导致堆碎片化。修复：调大`G1HeapRegionSize`到16m或控制单个Session对象大小。

**故障三：Token签名算法变更引发兼容性问题。** 某团队将RS256改为HS256以追求极致性能，但忽略了HS256是对称密钥——所有需要验签的服务（30+个微服务）都必须持有同一把共享密钥。一次密钥轮换时遗漏了3个老服务，直接导致大面积401认证失败。教训：签名算法选型必须权衡性能、安全、运维复杂度三要素。

### 思考题

1. **在100万并发场景下，Token签名成为最大瓶颈。是否可以将Token签名卸载到HSM（硬件安全模块）或专用的加密加速卡？Keycloak是否原生支持这样的架构？如果原生不支持，可以通过什么方式实现？**

   *提示：Keycloak的Token签名由`KeyWrapper`和`SignatureProvider` SPI处理——理论上可以自定义SPI实现将签名委托给外部HSM（通过PKCS#11或JCA/JCE Provider）。考虑延迟：一次HSM网络调用约1-3ms vs 本地RSA签名约2ms，批量卸载才有意义。也可以考虑在反向代理层（Nginx/Envoy）做Token校验的硬件卸载。*

2. **Keycloak基于Quarkus重新构建后支持Native编译。Native Image启动快、内存省，但运行时性能略低于JVM JIT（因为没有运行时优化）。在什么场景下应该选择Quarkus Native模式而不是传统JVM模式？如何在两者之间做性能对比测试？**

   *提示：Native Image适合Serverless/FaaS场景（启动时间敏感）、内存极度受限的边缘节点。传统JVM适合高吞吐+长时间运行的服务端（JIT的C2编译器能在运行数小时后达到峰值性能）。对比测试应包括启动时间、稳态QPS、内存占用、GC行为四个维度，各自跑30分钟以上的长稳压测。*

---

> **推广计划提示**：本章面向核心开发、运维工程师和架构师。性能压测需要测试团队配合编写和运行k6/JMeter脚本，JVM调优建议先在预发环境完成充分的对比实验后确定最优参数组合，再推广到生产。GC日志分析建议接入公司统一的可观测性平台（如Grafana Loki存储GC日志，配合GCEasy自动化分析）。下一章将深入Keycloak的监控与可观测性体系搭建。
