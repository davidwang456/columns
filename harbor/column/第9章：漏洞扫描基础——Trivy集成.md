# 第9章：漏洞扫描基础——Trivy 集成

## 1 项目背景

2024年3月的一个深夜，鑫汇支付（虚构）的安全总监李明被一条加密通讯唤醒："老板，我们被银监会通报了。"他猛地坐起身，打开安全平台——生产环境承载每日80亿流水、1200万笔交易的支付系统，刚刚被PCI-DSS合规扫描标记为"严重不合规"。通告显示：核心支付集群的237个容器镜像中，有89个存在Critical级别CVE，其中12个属于"可远程代码执行（RCE）"——任何一个被利用，攻击者就能在支付网关容器内执行任意命令，直接读取内存中的银行卡号和CVV码。

这已经是今年第三次合规通报。三周后就是年度审计复审，如果还是同样的结果，支付牌照将面临暂停风险——这意味着每天1.5亿的营收中断。李明需要在三周内，将Critical漏洞数量从89个降到0个。

**痛点一：漏洞发现严重滞后，扫描周期长达两周。** 鑫汇支付的镜像生产流程是：开发团队在Jenkins上构建镜像 → 推送到Harbor → Kubernetes自动部署到生产集群。整个链路中没有任何自动化安全检查。安全部门使用的是开源的OpenSCAP工具，由两名安全工程师每两周对生产环境做一次离线扫描——流程是：用SSH登录到K8s节点，逐台执行 `crictl pull` 拉取镜像，再在本地运行扫描器。237个镜像拉下来就要8个小时，扫描又要6个小时，生成报告还要半天。等报告出来，新一波镜像已经又部署上去了。安全部门始终在追赶开发部门的影子。

**痛点二：修复责任归属模糊，CVE修复周期无限拉长。** 安全部门每两周产出一份PDF格式的CVE报告（最近一期高达160页），通过邮件群发给50多名开发人员。报告里密密麻麻的CVE编号、CVSS评分、受影响包名，大部分开发者打开后只看一眼就归档了。两个典型案例：`order-service` 镜像中存在 `CVE-2023-38545`（curl SOCKS5堆溢出，CVSS 7.5），从首次报告到实际修复，跨越了整整7个Sprint（14周）。开发Leader的理由是："这个curl漏洞在基础镜像层的OpenJDK里，又不是我写的代码。"运维Leader反驳："基础镜像是你们Dockerfile里写的 `FROM`，你们选的镜像版本。"双方踢皮球，漏洞就"躺"在生产环境整整三个月。

**痛点三：CI/CD流水线零安全门禁，"能构建就能上线"。** 鑫汇支付的CI/CD流水线逻辑极其简单：Git Push → Jenkins Build → Docker Push → ArgoCD Sync → Production。中间没有任何安全检查节点。2024年1月，一个支付网关的Hotfix紧急修复了一个空指针异常，开发者在凌晨2点提交代码、凌晨2:05 Jenkins构建成功、凌晨2:08镜像推送到Harbor、凌晨2:10 ArgoCD自动同步到生产。没人注意到这个Hotfix使用的基础镜像 `openjdk:17.0.2` 包含了刚刚被公开的 `CVE-2024-20918`（Oracle Java SE 远程代码执行，CVSS 9.8）。这个漏洞在生产环境"裸奔"了整整6天，直到下一轮安全扫描才发现——而此时攻击者可能早已利用它建立了持久化后门。

**痛点四：基础镜像治理失控，50个业务镜像共享同一个有漏洞的基座。** 鑫汇支付有三个"黄金基础镜像"：`openjdk:17.0.2`（Java服务，32个业务镜像）、`python:3.9.5`（数据服务，11个业务镜像）、`node:16.14.0`（前端服务，9个业务镜像）。当 `openjdk:17.0.2` 被查出有3个Critical CVE和18个High CVE时，这意味着：不是修1个镜像，而是修32个镜像。基础镜像由平台组维护，但升级基础镜像版本可能导致业务服务的兼容性问题——`glibc` 版本变化可能导致某些JNI调用报错。平台组不敢轻易升级，业务组不愿意配合测试，基础镜像的CVE修复陷入僵局。

**痛点五：扫描器选型混乱，5个团队用5种不同的扫描器。** 安全部门用OpenSCAP，基础架构组用Clair，某个业务组自己买了Snyk License，DevOps组在Jenkins里集成了Trivy——但各扫各的，结果互不相同。同一个 `order-service:release-7.2` 镜像，OpenSCAP报告147个CVE，Clair报告89个，Trivy报告156个，Snyk报告203个——谁的结果是对的？没人能说清楚。更糟糕的是，Snyk的License费用每年28万，但只覆盖了3个项目的扫描。

Harbor的漏洞扫描系统——集成了Trivy作为默认扫描器——为上述所有痛点提供了一站式解决方案：Push时自动扫描、基于CVE级别的部署阻止策略、CVE白名单分级管理、可插拔的多扫描器框架、以及扫描结果集中存储在PostgreSQL中供审计查询。

---

## 2 项目设计——剧本式交锋对话

**场景：鑫汇支付安全整改专项会议——安全总监李明、平台架构师大师、运维小周、开发小胖、安全工程师小白围坐在会议室，投屏上显示着银监会的PCI-DSS通报函。**

**小胖**（一手拿着拿铁，一手指着投屏）："89个Critical CVE？这也太夸张了！我问个最直接的问题——我们现在用Harbor，我在GitHub上看到Trivy可以集成到Harbor里，每次push镜像的时候自动扫描一下，几秒钟就能出结果。那是不是只要把Trivy装上，这些漏洞就都能被拦住了？就像外卖到了门口有保安检查，不干净的菜不让进后厨？"

**大师**（推了推眼镜，在桌上放平一支笔）："小胖，你说的方向是对的——这正是安全左移的核心思路。但你想想：如果外卖保安发现菜里有一个烂叶子，是直接整盘倒掉，还是把烂叶子摘了继续吃？这就是我们要定好的策略。"

他转向所有人：

"Harbor + Trivy的基本工作流是这样的：开发者push镜像到Harbor → Harbor触发Trivy扫描镜像的每一层 → Trivy返回CVE报告 → Harbor根据我们预设的策略，决定'允许拉取'还是'阻止部署'。但有三个核心问题我们必须现在定下来：

1. **什么级别的漏洞应该阻止部署？** 只有Critical？还是Critical + High？加了Medium会不会太严格？
2. **基础镜像层的漏洞怎么处理？** `openjdk:17` 有3个Critical——但我们不能阻止所有32个Java服务的部署。
3. **万一Trivy自己挂了怎么办？** 是放行所有镜像（风险），还是阻止所有部署（业务中断）？"

**小胖**（放下咖啡杯）："第三个问题什么意思？Trivy又不是天天挂——挂了就等它修好呗？"

**小白**（一直安静做笔记，突然开口）："我理解大师的担忧。如果Trivy扫描服务不可用，新的Hotfix镜像在凌晨2点需要紧急部署。如果我们的策略是'没扫描结果就阻止'，那就等于——因为安全检查系统故障，导致支付系统的问题无法修复。这种'为了安全而牺牲业务连续性'的决策，CTO不会同意的。"

**大师**："小白抓到了关键矛盾。这就像机场安检——如果X光机突然坏了，你是让所有旅客直接登机（安全隐患），还是停飞所有航班（经济损失）？Harbor给了我们三种策略选择："

| 扫描器不可用时的策略 | 行为描述 | 适用场景 | 风险 |
|---|---|---|---|
| **阻止部署** | 扫描器故障 → 禁止所有新镜像拉取（返回403） | 金融监管、政务合规等高安全要求场景 | CI/CD全部阻塞，紧急发布失败 |
| **允许部署** | 扫描器故障 → 放行所有镜像（记录Warning日志） | 开发/测试环境，业务连续性优先级高于安全 | 安全门禁形同虚设，漏洞镜像可不受检查进入 |
| **复用上次扫描结果** | 扫描器故障 → 返回该镜像标签最近一次成功的扫描结果 | 生产环境均衡选择 | 新CVE可能在"复用窗口期"内被忽略 |

**技术映射**：Harbor Core API中的 `scanner_fallback` 字段控制此行为。当设置为 `true` 且实时扫描不可用时，Harbor会自动使用该Artifact上一次缓存的扫描报告。该设置位于项目级别配置中（`PUT /api/v2.0/projects/{id}/scanner-allowed`），兼顾了安全性与可用性。

**小胖**（又拿起咖啡）："OK，那说回基础镜像的问题——我们32个Java服务都 `FROM openjdk:17.0.2`。现在这个基础镜像有3个Critical CVE。如果我们设置'有Critical就阻止'的策略，那这32个服务全都没法部署了？这像是我家楼下只有一个送水站——水站的水有杂质，30户人家就都没水喝了？"

**大师**（笑了）："你这个比喻很到位。这正是容器安全的『金字塔问题』——底座的一个漏洞会影响上层所有镜像。Harbor的解决思路是**分层治理 + CVE白名单**。"

他在白板上画出三层结构：

```
         ┌──────────────────────┐
         │  应用层（业务代码引入的CVE）    │  ← 开发团队负责修复
         │  如：Spring框架漏洞、Log4j   │
         ├──────────────────────┤
         │  中间层（Dockerfile安装的依赖）  │  ← DevOps + 开发协作
         │  如：curl、wget、tzdata      │
         ├──────────────────────┤
         │  基础层（Base Image CVE）     │  ← 平台组负责升级/维护
         │  如：glibc、OpenSSL、libcrypto│
         └──────────────────────┘
```

"分层治理的核心是：**每层有明确的修复Owner，不在其他层浪费精力**。对于基础层的已知CVE（如 `CVE-2023-4586`，glibc低风险、暂无公开PoC），我们通过CVE白名单暂时豁免——因为这些CVE的修复在OpenJDK上游项目中，不是我们能控制的。但需要附加条件：白名单必须有到期时间、必须有审批记录。"

```yaml
# CVE白名单示例（项目级）
items:
  - cve_id: "CVE-2024-20918"
    reason: "OpenJDK基础镜像层，上游Oracle已发布17.0.10修复，平台组计划2周内升级"
    expires_at: "2024-04-15"
    approver: "liming@xinhuipay.com"
  - cve_id: "CVE-2023-38545"
    reason: "curl SOCKS5漏洞，当前业务未使用SOCKS5协议，攻击面不适用"
    expires_at: "2024-06-30"
    approver: "security@xinhuipay.com"
```

**技术映射**：Harbor支持两级CVE白名单——**系统级**（`System CVE Allowlist`，对所有项目全局生效，仅SysAdmin可管理）和**项目级**（`Project CVE Allowlist`，每个项目独立配置，Project Admin可管理）。白名单中的CVE不会被计入"阻止部署"的判断逻辑。但需要注意：白名单只影响部署阻止策略，不会从扫描报告中移除该CVE——审计时仍然能看到完整的漏洞列表。

**小白**（抬起头，眼神若有所思）："我研究了Trivy的内部机制，有几个细节想确认。第一，Trivy的CVE数据库有多大？每次扫描都要重新下载吗？第二，扫描一个2GB的镜像大概要多久？第三，Trivy和Harbor版本之间有兼容性问题吗？"

**大师**："三个非常好的工程问题。一个一个来。"

他翻开笔记本：

"CVE数据库方面：Trivy维护着一个综合性的漏洞数据库，聚合了NVD（美国国家漏洞数据库）、Red Hat Security Data、Debian Security Tracker、Alpine SecDB、GitHub Advisory Database等8个上游数据源。数据库文件大小约200-400MB（含所有Linux发行版和语言包的数据）。首次启动Trivy Adapter时会自动下载到容器内的 `/home/scanner/.cache/trivy/db/` 目录，之后每12小时增量更新一次（只下载变更数据，增量约2-10MB）。扫描时不需要重新下载——数据库已经在本地。"

"扫描速度方面：Trivy的扫描速度在同类工具中是第一梯队的。以2GB的Java镜像为例——"

| 镜像类型 | 大小 | 扫描耗时 | 内存峰值 | 备注 |
|---------|------|---------|---------|------|
| Alpine Java | 180MB | 3-8秒 | ~200MB | 层数少，包少 |
| Ubuntu Java | 800MB | 12-25秒 | ~400MB | dpkg包数据库大 |
| Python ML | 2.5GB | 30-60秒 | ~800MB | 大量pip包需解析 |
| Node.js全栈 | 1.2GB | 15-30秒 | ~350MB | node_modules遍历 |

"兼容性方面：Trivy Adapter的版本必须与Harbor Core的大版本匹配。Harbor 2.9对应Trivy Adapter 0.45+，Harbor 2.10-2.12对应Trivy Adapter 0.50+。不匹配会导致扫描报告格式解析失败——扫描状态显示Success，但报告内容为空。"

**技术映射**：Trivy Adapter是一个独立的容器组件（`goharbor/trivy-adapter-photon`），通过Harbor的Scanner Adapter API框架与Core通信。该框架定义了标准的扫描请求/响应格式（`application/vnd.scanner.adapter.vuln.report.harbor+json; version=1.0`），使得任何实现了该接口的扫描器都可以无缝接入Harbor——Clair、Anchore、Aqua等都是通过同一套API接入的。

**小胖**（拿起白板笔在"分层治理"图上画了一个大圈）："我有个想法——既然基础镜像的CVE可以用白名单豁免，那我们是不是可以搞一个『自动豁免』？比如：只要CVE在基础镜像层、且CVSS < 7.0、且没有公开PoC——就自动加入白名单？这样安全部门就不用一个个审核了。"

**小白**（立刻警惕）："不行！自动豁免有三个风险：第一，CVSS评分是会变化的——今天5.5，明天被发现有PoC后可能升级到9.8；第二，'没有公开PoC'这个判断标准不客观——可能PoC已经在暗网流传但我们不知道；第三，自动豁免会让开发者形成心理依赖：『反正基础镜像层漏洞会自动豁免，我不用关心Dockerfile里写的FROM版本』。"

**大师**（赞许地看着小白）："小白说出了安全审计的核心原则——**人的判断不能完全被自动化取代**。CVE白名单的本质不是'忽略漏洞'，而是『暂时接受风险 + 有计划的缓解措施』。每一个白名单条目都应该是：有人审批、有修复计划、有过期时间的。我建议的流程是："

```
1. Trivy自动扫描 → 生成CVE列表
2. 系统自动分类：基础层CVE / 应用层CVE
3. 基础层CVE → 自动通知平台组，要求14天内提供修复计划
4. 应用层CVE（High+）→ 自动通知业务组，阻止部署
5. 任何白名单添加 → 必须填写：理由、过期时间、审批人
6. 白名单过期前3天 → 自动发送提醒
7. 白名单过期 → 自动移除，CVE恢复阻止
```

**技术映射**：Harbor 2.8+ 支持CVE白名单的过期时间字段 `expires_at`。当白名单项过期后，Harbor会自动将其从豁免列表中移除（但不会自动删除记录——审计日志中仍可查到历史白名单）。遗憾的是，Harbor原生并不支持"过期前N天提醒"——这需要通过Webhook或定时脚本自行实现。

---

## 3 项目实战

### 3.1 环境要求

| 组件 | 版本/配置要求 | 说明 |
|------|-------------|------|
| Harbor | v2.12+ | Trivy Adapter从2.9开始内置，之前版本需单独部署 |
| Trivy Adapter | v0.50+ | 随Harbor安装包自带，位于 `docker-compose.yml` 中 |
| Docker / Podman | 20.10+ | 用于拉取和推送测试镜像 |
| curl + jq | 任意版本 | API测试工具 |
| 网络 | 可访问外网（首次下载CVE数据库） | 或已准备离线CVE数据库包 |
| 磁盘 | Trivy Adapter节点额外500MB+ | CVE数据库存储（`/home/scanner/.cache`） |
| 内存 | Trivy Adapter节点额外1GB+ | 大镜像扫描时内存峰值可能达800MB |

```bash
# 确认Trivy Adapter正在运行
docker compose -f /opt/harbor/docker-compose.yml ps | grep trivy
# 预期输出：trivy-adapter   Up 2 hours (healthy)

# 确认扫描器API可达
curl -s http://localhost:8080/api/v1/metadata
# 预期输出：{"version":"0.50.0","scanner":{"name":"Trivy","vendor":"Aqua Security",...}}
```

### 3.2 步骤一：注册Trivy扫描器到Harbor

**目标：** 确认Trivy扫描器已注册，并将其设置为所有新项目的默认扫描器。

```bash
# 1. 查看当前已注册的扫描器列表
curl -s -u admin:Harbor12345 \
  "https://harbor.xinhuipay.com/api/v2.0/scanners" | \
  jq '.[] | {name: .name, vendor: .vendor, version: .version, health: .health, is_default: .is_default}'
```

**预期输出：**
```json
{
  "name": "Trivy",
  "vendor": "Aqua Security",
  "version": "0.50.0",
  "health": "healthy",
  "is_default": false
}
```

```bash
# 2. 获取Trivy扫描器的UUID并设为默认
TRIVY_UUID=$(curl -s -u admin:Harbor12345 \
  "https://harbor.xinhuipay.com/api/v2.0/scanners" | \
  jq -r '.[0].uuid')

echo "Trivy UUID: $TRIVY_UUID"

# 3. 设为默认扫描器（新项目自动使用）
curl -X PATCH -u admin:Harbor12345 \
  -H "Content-Type: application/json" \
  -d "{\"is_default\": true}" \
  "https://harbor.xinhuipay.com/api/v2.0/scanners/$TRIVY_UUID"

# 响应：HTTP 200 OK
```

### 3.3 步骤二：配置项目级漏洞扫描策略

**目标：** 为 `payment-core` 项目配置：Push时自动扫描、Critical级漏洞阻止部署、High级漏洞仅报警不阻止。

```bash
# 1. 获取项目ID
PROJECT_ID=$(curl -s -u admin:Harbor12345 \
  "https://harbor.xinhuipay.com/api/v2.0/projects?name=payment-core" | \
  jq '.[0].project_id')
echo "Project ID: $PROJECT_ID"

# 2. 配置漏洞阻止策略
curl -X PUT -u admin:Harbor12345 \
  -H "Content-Type: application/json" \
  -d '{
    "prevent_vul": true,
    "severity": "critical",
    "scan_on_push": true
  }' \
  "https://harbor.xinhuipay.com/api/v2.0/projects/$PROJECT_ID/prevent-vulnerability"

# 预期输出：HTTP 200
```

**策略参数详解：**
- `scan_on_push: true` — 每次 `docker push` 完成后自动触发Trivy扫描（无需手动操作）
- `prevent_vul: true` — 当扫描结果中达到指定severity的CVE数量 > 0时，阻止镜像被拉取
- `severity: critical` — 仅Critical级触发阻止。可选值：`none`, `low`, `medium`, `high`, `critical`

```bash
# 3. 将Trivy扫描器绑定到当前项目
curl -X PUT -u admin:Harbor12345 \
  -H "Content-Type: application/json" \
  "https://harbor.xinhuipay.com/api/v2.0/projects/$PROJECT_ID/scanner"

# 4. 验证项目扫描配置
curl -s -u admin:Harbor12345 \
  "https://harbor.xinhuipay.com/api/v2.0/projects/$PROJECT_ID/scanner" | jq '.'
```

**预期输出：**
```json
{
  "uuid": "f47ac10b-58cc-4372-a567-0e02b2c3d479",
  "name": "Trivy",
  "vendor": "Aqua Security",
  "version": "0.50.0"
}
```

### 3.4 步骤三：触发首次漏洞扫描并解读报告

**目标：** 推送一个测试镜像，观察自动扫描过程，解读扫描报告的各项指标。

```bash
# 1. 推送测试镜像（使用已知包含CVE的老版本Python）
docker pull python:3.9.5
docker tag python:3.9.5 harbor.xinhuipay.com/payment-core/python-service:v1.0.0
docker push harbor.xinhuipay.com/payment-core/python-service:v1.0.0

# 输出：
# The push refers to repository [harbor.xinhuipay.com/payment-core/python-service]
# ...
# v1.0.0: digest: sha256:a1b2c3... size: 1234
```

```bash
# 2. 等待扫描完成并查看扫描总览（通常10-60秒）
sleep 15

curl -s -u admin:Harbor12345 \
  "https://harbor.xinhuipay.com/api/v2.0/projects/payment-core/repositories/python-service/artifacts" | \
  jq '.[0].scan_overview'
```

**预期输出：**
```json
{
  "application/vnd.scanner.adapter.vuln.report.harbor+json; version=1.0": {
    "report_id": "e8f3a9b1-c4d2-4a6e-b8f0-1c3d5e7f9a2b",
    "scan_status": "Success",
    "severity": "Critical",
    "duration": 12,
    "summary": {
      "total": 245,
      "fixable": 187,
      "summary": {
        "Critical": 3,
        "High": 18,
        "Medium": 67,
        "Low": 157
      }
    },
    "start_time": "2024-03-15T14:30:00Z",
    "end_time": "2024-03-15T14:30:12Z"
  }
}
```

**报告字段解读：**
| 字段 | 含义 |
|------|------|
| `scan_status` | Success（扫描完成）/ Error（扫描失败）/ Pending（排队中）/ Scanning（进行中） |
| `severity` | 所有CVE中的最高级别（据此判断是否触发阻止策略） |
| `total` | 发现的CVE总数 |
| `fixable` | 有已知修复版本的CVE数量（即升级包版本可修复） |
| `duration` | 扫描耗时（秒） |

```bash
# 3. 获取详细的CVE列表（前20条）
curl -s -u admin:Harbor12345 \
  -H "Accept: application/vnd.scanner.adapter.vuln.report.harbor+json; version=1.0" \
  "https://harbor.xinhuipay.com/api/v2.0/projects/payment-core/repositories/python-service/artifacts/v1.0.0/additions/vulnerabilities" | \
  jq '.[] | {id: .id, severity: .severity, package: .package, version: .version, fix: .fix_version}' | \
  head -60
```

**预期输出（片段）：**
```json
{"id":"CVE-2023-27043","severity":"High","package":"python3.9","version":"3.9.5-1","fix":"3.9.19"}
{"id":"CVE-2023-38545","severity":"High","package":"curl","version":"7.74.0-1","fix":"7.88.1"}
{"id":"CVE-2024-20918","severity":"Critical","package":"openjdk-17","version":"17.0.2","fix":"17.0.10"}
```

### 3.5 步骤四：验证CVE阻止策略

**目标：** 验证当镜像包含Critical CVE时，拉取操作被正确阻止。

```bash
# 尝试拉取被标记为Critical的镜像
docker pull harbor.xinhuipay.com/payment-core/python-service:v1.0.0
```

**预期错误输出：**
```
Error response from daemon: unknown: The image is not allowed to pull 
because it is blocked by vulnerability policy, please contact admin.
```

如果镜像拉取成功，说明阻止策略未生效——检查：
- 是否将扫描器正确分配给了项目（步骤二第3小步）
- 项目的 `prevent_vul` 是否设置为 `true`
- 扫描结果中的 `severity` 是否达到了配置的阻止级别

### 3.6 步骤五：创建和管理CVE白名单

**目标：** 对基础镜像层的已知CVE添加白名单豁免，允许紧急发布。

```bash
# 1. 查询项目当前CVE白名单
curl -s -u admin:Harbor12345 \
  "https://harbor.xinhuipay.com/api/v2.0/projects/$PROJECT_ID/cve-allowlist" | jq '.'

# 预期输出（空）：
# {"project_id": 5, "items": [], "creation_time": "...", "update_time": "..."}
```

```bash
# 2. 添加CVE白名单（含过期时间和备注）
curl -X PUT -u admin:Harbor12345 \
  -H "Content-Type: application/json" \
  -d '{
    "items": [
      {
        "cve_id": "CVE-2023-27043",
        "comment": "Python基础镜像层，上游Debian暂未发布修复，计划Q2升级到python:3.12",
        "expires_at": 1719878400
      },
      {
        "cve_id": "CVE-2023-38545",
        "comment": "curl SOCKS5漏洞——本服务未使用SOCKS5协议，实际攻击面为零",
        "expires_at": 1719878400
      }
    ]
  }' \
  "https://harbor.xinhuipay.com/api/v2.0/projects/$PROJECT_ID/cve-allowlist"

# 预期输出：HTTP 200
```

```bash
# 3. 验证白名单生效——再次拉取镜像应成功
docker pull harbor.xinhuipay.com/payment-core/python-service:v1.0.0
# 预期：拉取成功（白名单中的CVE不再计入阻止判断）
```

**重要提示：** CVE白名单只影响"是否阻止拉取"的判断逻辑。在Harbor Portal和API的扫描报告中，白名单中的CVE仍然会显示——它只是被标记为"已豁免"而非"已修复"。审计人员可以看到完整的CVE列表以及哪些被豁免了。

### 3.7 步骤六：定时重新扫描与批量扫描

**目标：** 对所有已有镜像执行重新扫描（因CVE数据库更新，可能需要重新评估旧镜像）。

```bash
# 1. 对项目中所有镜像触发重新扫描
curl -X POST -u admin:Harbor12345 \
  -H "Content-Type: application/json" \
  "https://harbor.xinhuipay.com/api/v2.0/projects/$PROJECT_ID/scanner/crawl"

# 预期输出：HTTP 200
# 说明：此API会遍历项目下所有仓库的所有Artifact，依次提交扫描任务到JobService队列
```

```bash
# 2. 查询扫描任务状态
curl -s -u admin:Harbor12345 \
  "https://harbor.xinhuipay.com/api/v2.0/projects/$PROJECT_ID/scanner/crawl" | jq '.'

# 3. 配置定时重新扫描（通过Harbor的系统调度器）
# 每天凌晨3:00重新扫描所有项目的所有镜像
# 注意：此功能需要Harbor 2.10+，通过配置文件设置
```

```bash
# 4. 获取项目的漏洞汇总统计（Dashboard数据）
curl -s -u admin:Harbor12345 \
  "https://harbor.xinhuipay.com/api/v2.0/projects/$PROJECT_ID/summary" | \
  jq '{total_artifacts: .artifact_count, scanned: .scanned_artifact_count, 
       critical: .quota.vulnerability_summary.Critical,
       high: .quota.vulnerability_summary.High}'
```

### 3.8 步骤七：Trivy扫描器健康监控与调优

**目标：** 确保Trivy扫描器长期稳定运行，监控资源使用和CVE数据库更新状态。

```bash
# 1. 查看Trivy Adapter资源使用
docker stats trivy-adapter --no-stream --format \
  "table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.MemPerc}}"
# 预期输出：
# NAME              CPU %     MEM USAGE / LIMIT     MEM %
# trivy-adapter     2.15%     450MiB / 2GiB         22.50%

# 2. 检查CVE数据库最后更新时间
docker exec trivy-adapter trivy --version
# Trivy Version: 0.50.0

docker exec trivy-adapter ls -la /home/scanner/.cache/trivy/db/
# 检查 trivy.db 和 metadata.json 的最后修改时间

# 3. 手动触发CVE数据库更新
docker exec trivy-adapter trivy image --download-db-only
# 如果内网隔离无法访问外网，见下方"坑1"的离线方案

# 4. 查看Trivy Adapter日志中的错误
docker logs trivy-adapter --tail 100 2>&1 | grep -iE "error|fail|timeout|panic"
```

---

### 3.9 排坑指南

#### 坑1：Trivy Adapter启动后持续重启（CrashLoopBackOff）

**现象：** `docker compose ps` 显示 `trivy-adapter` 状态为 `Restarting (1) 10 seconds ago`，反复重启。

**根因：** Trivy Adapter首次启动时需要从GitHub下载CVE数据库（约400MB），而内网环境可能无法直连 `https://ghcr.io` 和 `https://github.com`，导致下载超时（默认超时5分钟）。容器因启动超时被Docker判定为失败并重启，进入死循环。

**解决方法：**
```bash
# 方案A：在外网机器上下载离线数据库包，传输到内网
# 在外网机器执行：
docker run --rm -v /tmp/trivy-db:/root/.cache/aquasec aquasec/trivy \
  image --download-db-only
tar -czf trivy-db-offline.tar.gz -C /tmp/trivy-db .

# 将离线包传输到Harbor节点，解压并挂载
mkdir -p /opt/harbor/trivy-db-cache
tar -xzf trivy-db-offline.tar.gz -C /opt/harbor/trivy-db-cache

# 修改 docker-compose.yml，为trivy-adapter添加卷挂载：
# trivy-adapter:
#   volumes:
#     - /opt/harbor/trivy-db-cache:/home/scanner/.cache/trivy
#     - /opt/harbor/trivy-reports:/home/scanner/.cache/reports

docker compose -f /opt/harbor/docker-compose.yml up -d trivy-adapter
```

#### 坑2：大镜像扫描超时（扫描状态为Error）

**现象：** 推送一个超过2GB的镜像（如ML模型镜像）后，扫描状态始终为 `Pending` → 最终变为 `Error`，扫描报告为空。

**根因：** Trivy Adapter默认的扫描超时时间为5分钟（`SCANNER_TRIVY_TIMEOUT` 环境变量）。大型镜像的解压、层分析、包数据库解析可能超过这个阈值。尤其是包含大量Python包（`site-packages` 目录数十万文件）的镜像，遍历过程极慢。

**解决方法：**
```bash
# 方案A：增加Trivy扫描超时时间
# 在 docker-compose.yml 的 trivy-adapter 服务环境变量中添加：
# environment:
#   SCANNER_TRIVY_TIMEOUT: 900s        # 增加到15分钟
#   SCANNER_TRIVY_INSECURE: "false"
#   SCANNER_TRIVY_SKIP_UPDATE: "false"

# 重新创建容器
docker compose -f /opt/harbor/docker-compose.yml up -d trivy-adapter

# 方案B：跳过某些扫描耗时但业务不需要的目录
# 在harbor.yml中配置Trivy扫描忽略路径：
# trivy:
#   ignore_unfixed: true               # 跳过暂无修复版本的CVE
#   skip_files:
#     - "**/node_modules/**"
#     - "**/site-packages/tests/**"
#   timeout: 900s
```

#### 坑3：CVE白名单不生效——添加豁免后仍被阻止拉取

**现象：** 通过API或Portal添加了CVE白名单（如豁免了 `CVE-2024-20918`），但 `docker pull` 该镜像时仍然返回："The image is not allowed to pull because it is blocked by vulnerability policy."

**根因分析：** 这通常有三个可能原因：
1. **白名单是系统级的，但项目策略覆盖了系统白名单。** 如果项目级 `prevent_vul` 设置为 `true`，且项目有自己的CVE白名单，则项目级白名单优先于系统级白名单——系统级白名单中的CVE在该项目中不生效。
2. **白名单中的CVE ID与扫描器报告中的CVE ID不完全一致。** 例如白名单中写的是 `CVE-2024-20918`，但Trivy报告中的ID可能是 `CVE-2024-20918`（带特定发行版后缀，如 `RHSA-2024:1234`）。
3. **阻止策略的severity为 `none` 但prevent_vul为 `true`。** 这会导致**所有级别**（包括Low）的CVE都触发阻止——白名单只能豁免特定CVE，无法覆盖"全级别阻止"的逻辑。

**解决方法：**
```bash
# 验证方法：直接测试拉取（而非看Portal报告）
docker pull harbor.xinhuipay.com/payment-core/python-service:v1.0.0

# 如果拉取成功，说明白名单已生效。
# 如果拉取失败，检查以下内容：
# 1. 确认白名单是在正确的级别添加的
curl -s -u admin:Harbor12345 \
  "https://harbor.xinhuipay.com/api/v2.0/projects/$PROJECT_ID/cve-allowlist" | \
  jq '.items[] | select(.cve_id=="CVE-2024-20918")'

# 2. 确认扫描报告中的CVE ID完全一致
curl -s -u admin:Harbor12345 \
  -H "Accept: application/vnd.scanner.adapter.vuln.report.harbor+json; version=1.0" \
  "https://harbor.xinhuipay.com/api/v2.0/projects/payment-core/repositories/python-service/artifacts/v1.0.0/additions/vulnerabilities" | \
  jq '.[] | select(.id | startswith("CVE-2024-20918"))'

# 3. 如果CVE ID不一致，按扫描报告中的实际ID重新添加白名单
```

#### 坑4：扫描结果中"可修复CVE数量"始终为0

**现象：** 扫描报告显示 `total: 245, fixable: 0`——意味着所有245个CVE都是"暂无修复版本"的。但手动查询NVD发现大部分CVE实际有补丁。

**根因：** Trivy判断"可修复"的逻辑依赖发行版的安全公告（Security Advisory）。如果镜像使用的是较老的发行版（如Debian 10 Buster，已于2022年6月EOL），上游发行版已不再发布安全更新——即使NVD数据库中有CVE记录，Trivy也无法将CVE映射到具体的"修复版本号"。

**解决方法：**
```bash
# 升级基础镜像到仍受支持的发行版版本
# 例如：debian:buster (已EOL) → debian:bullseye 或 debian:bookworm

# 或者：使用Trivy的 --ignore-unfixed 标志配合分析
docker run --rm aquasec/trivy image \
  --ignore-unfixed \
  --severity HIGH,CRITICAL \
  python:3.9.5
```

---

## 4 项目总结

### 4.1 主流容器漏洞扫描器全方位对比

| 扫描器 | 语言 | CVE数据源 | Harbor集成方式 | 扫描速度(1GB镜像) | 内存消耗 | 误报率 | 开源协议 | 推荐场景 |
|--------|------|----------|--------------|------------------|----------|--------|---------|---------|
| **Trivy** | Go | NVD + RedHat + Debian + Alpine + GitHub Advisory + GitLab Advisory + Amazon + Ubuntu | ✅ 内置Adapter | 15-30秒 | ~400MB | 低（<3%） | Apache 2.0 | 通用、CI/CD集成、日常开发 |
| **Clair** | Go | Alpine + Debian + Ubuntu + Oracle + RedHat | ✅ 内置Adapter | 30-90秒 | ~600MB | 中等（~8%） | Apache 2.0 | RedHat/Quay生态、企业合规 |
| **Grype** | Go | NVD + GitHub Advisory + Wolfi | ❌ 需自定义Adapter | 10-25秒 | ~300MB | 中等（~5%） | Apache 2.0 | SBOM集成、轻量级扫描 |
| **Snyk** | TypeScript | 自有 + NVD + 社区 | ⚠️ 商业插件 | 20-40秒 | ~500MB | 低（<2%） | 商业License | 商业版、全链路安全、代码+镜像联合扫描 |
| **Aqua** | Go | 自有 + NVD + 厂商直通 | ⚠️ 商业插件 | 15-25秒 | ~400MB | 极低（<1%） | 商业License | 企业级安全合规、运行时保护联动 |
| **Anchore** | Python | NVD + 自有 + 发行版 | ⚠️ 商业/社区插件 | 45-120秒 | ~1GB | 中等（~7%） | AGPL/商业 | 策略即代码（Policy-as-Code） |
| **Prisma Cloud (Twistlock)** | — | 自有 + NVD + 情报 | ⚠️ 商业外部集成 | 20-40秒 | ~500MB | 低（<3%） | 商业License | 全栈云原生安全、运行时+静态联合分析 |

### 4.2 扫描策略按环境分级推荐

| 环境 | scan_on_push | prevent_vul | 阻止级别 | CVE白名单策略 | 扫描失败策略 | 审核频率 |
|------|-------------|-------------|---------|-------------|-------------|---------|
| **本地开发** | ❌ | ❌ | — | 不限制（开发者自行评估） | 允许部署 | 无 |
| **CI测试环境** | ✅ | ✅ | Critical | 自动加基础层Low级 | 允许部署 | 每周 |
| **预发布/Staging** | ✅ | ✅ | High + Critical | 需Tech Lead审批，7天过期 | 复用上次结果 | 实时 |
| **生产环境** | ✅ | ✅ | High + Critical | 需安全部门审批，14天过期，必须有修复计划 | 阻止部署 | 每次发布前 |
| **合规审计环境** | ✅ | ✅ | Medium + High + Critical | 不允许任何白名单（合规要求） | 阻止部署 | 每季度全面审计 |

### 4.3 适用场景

- **CI/CD流水线安全门禁：** Jenkins/GitLab CI在构建完成后推送镜像 → Trivy自动扫描 → 根据CVE级别决定是否允许K8s部署。安全左移的最直接落地方式。
- **PCI-DSS / 等保2.0合规审计：** 为每个生产镜像版本保留完整的扫描报告（JSON格式），证明"在部署前已通过漏洞检查"。扫描报告可作为合规审计证据提交。
- **基础镜像生命周期治理：** 定期（每周）扫描所有基础镜像的CVE趋势。当某个基础镜像的Critical CVE数量超过阈值时，自动通知平台组启动升级计划。
- **多团队镜像安全可视化：** Harbor Dashboard上的漏洞仪表盘让管理层直观了解整体安全态势——"我们的Critical CVE从上周的89个降到了12个"。
- **开源组件供应链安全：** Trivy不仅能扫描OS包漏洞，还能扫描Python（pip）、Node.js（npm）、Java（jar）、Go（modules）等语言生态的依赖包漏洞——覆盖整个软件供应链。

### 4.4 不适用场景

- **运行时入侵检测：** Trivy是静态镜像扫描器，不监控运行中容器的异常行为（如进程注入、文件篡改、网络异常连接）。运行时安全需要Falco、Aqua Runtime Protection等专用工具。
- **源码级SAST（静态应用安全测试）：** Trivy扫描的是已构建的镜像中的包和依赖，不分析源代码中的安全漏洞（如SQL注入、XSS）。源码分析需要SonarQube、Checkmarx等SAST工具。

### 4.5 五项关键注意事项

1. **CVE数据库存在更新延迟盲区。** Trivy默认每12小时更新一次数据库。但如果一个CVE在凌晨2:00被NVD发布，而Trivy在中午12:00才更新——这10小时内推送的镜像不会被检测到该CVE。对于金融/政务等高安全场景，建议缩短更新间隔到6小时（通过修改 `SCANNER_TRIVY_GITHUB_TOKEN` 提升API速率限制）。
2. **白名单不等于漏洞消失。** 将CVE加入白名单仅仅是"暂时接受风险"——它仍然存在于扫描报告中，外部审计时依然可见。必须为每个白名单条目绑定修复计划和过期时间，否则白名单将成为"安全债务"的藏身之处。
3. **Trivy扫描结果受发行版EOL影响。** 如果基础镜像使用的发行版已EOL（如Debian 10 EOL于2022年6月），该发行版的安全追踪器不会再有更新。Trivy报告的CVE列表中"可修复"一栏可能为0——但不代表镜像安全，只是上游不再发布修复信息。
4. **大镜像扫描的资源消耗不可忽视。** 一个3GB的机器学习镜像（含CUDA、cuDNN、PyTorch等）扫描时可能消耗1.2GB内存和80%的CPU。如果Harbor节点资源有限，同时来10个这样的扫描任务可能导致Trivy Adapter OOM。建议为Trivy Adapter设置资源限制，并控制并发扫描数。
5. **系统级CVE白名单需谨慎。** 系统级白名单对所有项目全局生效——如果添加了一个"有争议"的CVE白名单（如 `CVE-2023-44487` HTTP/2 Rapid Reset，影响范围极广），相当于为整个Harbor实例的所有项目"开了一个口子"。建议所有系统级白名单的添加操作至少需要2人审批。

### 4.6 生产环境典型故障案例

| 故障案例 | 现象描述 | 根因分析 | 解决方法 |
|---------|---------|---------|---------|
| **"午夜幽灵"——新推送镜像未触发扫描** | 开发者在凌晨部署了紧急Hotfix，但8小时后扫描状态仍为"未扫描"。安全部门次日发现该Hotfix包含一个CVSS 9.8的漏洞 | 项目未绑定扫描器（Trivy Adapter虽然在Harbor中注册了，但需要显式分配给每个项目）。`scan_on_push` 开启但无扫描器可用 | `POST /projects/{id}/scanner` 将Trivy绑定到项目，然后 `POST /projects/{id}/scanner/crawl` 对现有镜像补扫 |
| **"周末惊魂"——CVE数据库损坏导致所有扫描失败** | 周六凌晨Trivy Adapter尝试更新数据库时因网络波动导致下载不完整，数据库文件损坏。周六全天所有推送的镜像扫描状态均为Error | Trivy Adapter的数据库增量更新没有完整性校验机制。部分下载的 `trivy.db` 文件被保存，但文件头损坏，导致所有扫描请求返回500 | 删除容器内的 `/home/scanner/.cache/trivy/db/` 目录，重启Trivy Adapter触发全量重新下载。建议配置数据库文件的定期备份 |
| **"雪崩效应"——CVE白名单过期导致生产阻断** | 项目管理员在3个月前为 `CVE-2024-XXXX` 添加了白名单（过期时间90天），到期后白名单自动失效。恰好当天CI流水线推送了新版本的镜像——扫描检测到Critical CVE且白名单已过期 → 阻止部署 → 当天3个服务的发布全部失败 | CVE白名单的过期逻辑是"静默移除"——过期时不通知任何人，直接恢复阻止。项目管理员已经忘记了这个白名单的存在 | 1. 定期（每周）审计所有即将过期的白名单；2. 在CVE白名单过期前7天通过Webhook发送提醒通知；3. 如有必要，提前续期白名单（更新 `expires_at` 字段） |

### 4.7 思考题

1. **鑫汇支付的生产环境中，`openjdk:17.0.2` 有2个Critical CVE（上游Oracle已发布修复版本17.0.10）。但升级到17.0.10后，有一个使用了JNI的服务报 `UnsatisfiedLinkError`（因为 `libjvm.so` 版本变化）。在等待业务方适配的过程中（预计需要2个Sprint，4周），如何配置Harbor的扫描策略——既满足PCI-DSS合规要求（"生产镜像不允许有未修复的Critical CVE"），又不阻止其他31个正常服务的日常部署？请设计具体的CVE白名单策略和配套的审批流程。**

2. **Trivy的CVE数据库包含全球所有已知的Linux发行版、语言包、容器镜像的漏洞信息（约400MB）。但你的Harbor中只存放了基于Ubuntu和Python的镜像。是否可以通过"裁剪"Trivy数据库来减少更新耗时和存储占用？如果可行，设计一个自动化脚本：从Trivy DB中提取只与Ubuntu和Python相关的CVE记录，构建一个精简版数据库（预计50-80MB），并确保不影响扫描准确性。**

---

> 下一章预告：第10章将深入Harbor的用户管理与认证体系，包括LDAP/AD域集成、OIDC单点登录、机器人账户管理和多认证源共存的策略设计。
