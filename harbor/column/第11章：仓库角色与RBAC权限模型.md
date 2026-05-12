# 第11章：仓库角色与 RBAC 权限模型

## 1 项目背景

2024年5月，云鲸科技（虚构）的CTO王海在季度安全复盘会上，收到了一份让他血压飙升的报告。报告显示：Harbor仓库在过去6个月内经历了惊人的增长——从12个项目扩展到47个项目，从80个用户增长到340个用户，镜像总数从800个飙升到5600个。但与之形成鲜明反差的是：权限管理体系几乎为零。安全团队对公司Harbor做了一次全面的权限审计，结果触目惊心。

**痛点一："全员管理员"现象泛滥。** 审计发现：47个项目中，有31个项目的"项目管理员"角色被分配给了超出实际需要的人数——平均每个项目有6.3个管理员。`payment-gateway` 项目甚至有11个管理员，占项目成员（15人）的73%。进一步调查发现，原因很简单：每当有人喊"我要权限"，群里的某个管理员就给对方开一个最高权限——"反正都认识，给个管理员省得后面麻烦"。三个月后，没人在意谁还需要这些权限——也没人敢回收，因为"万一他还要用呢"。

**痛点二：权限回收几乎没有执行过。** 审计团队抽取了过去12个月中的38次人员转岗记录，对比Harbor中的实际权限状态——结果令人震惊：32人（84%）在转岗后，原部门的项目权限仍然保留着。最严重的一例：一名前 `risk-engine` 项目的开发者在2023年8月转岗到了前端团队，但在2024年5月的审计中，他仍然拥有 `risk-engine` 项目的**项目管理员**权限——整整9个月。在这9个月里，他当然没有做过任何恶意操作，但问题的核心不是"他是不是好人"，而是"**系统没有自动回收权限的能力**"——这意味着每一次人员变动，都在为系统增加一个潜在的权限炸弹。

**痛点三：外包团队的权限"一刀切"存在严重越界。** 云鲸科技引入了3个外包团队，分别负责：移动端SDK开发（8人）、UI组件库维护（4人）、自动化测试脚本编写（6人）。按合同规定，这18人只能访问与他们工作直接相关的镜像——共计涉及3个项目和约15个仓库。但因为Harbor的角色模型中"访客"角色是项目级的，运维只能给他们分配"访客"角色到相应项目。这导致外包人员可以pull项目中**所有**仓库的镜像——包括一些仅供内部使用、不应向外包暴露的镜像。例如，`mobile-sdk` 项目中除了正常的SDK镜像，还存放了3个内部调试工具镜像（含网络拓扑信息）和2个包含模拟用户数据的测试镜像。

**痛点四：CI/CD系统权限大得可怕。** 公司在Jenkins中部署了47条Pipeline，对应47个Harbor项目。但由于早期图省事，47条Pipeline**全部**使用同一个 `admin` 账号的Token。这意味着：任何一条Pipeline在被执行时，都具有删除任意项目、修改任意配置、查看所有审计日志的能力。2024年3月，一个实习生在 `test-playground` 项目的Pipeline中加入了一个Shell命令——原本应该是 `docker rmi`（删除本地镜像），但笔误写成了通过Harbor API删除Artifact。由于使用的是 `admin` Token，这条命令成功执行了——它删除了 `payment-core` 项目中3个生产标签（`release-8.2`、`release-8.3`、`release-8.4`），直接导致当晚的上线回滚失败——因为回滚的基准标签已经不存在了。

**痛点五：权限矩阵'说不清楚'导致日常协作效率低下。** 在日常开发中，当开发者需要跨项目协作时——例如 `order-service`（属于 `order-platform` 项目）需要拉取 `user-base-image`（属于 `shared-base` 项目）——开发者不知道应该找谁申请权限、申请什么角色。于是他在部门群里喊一声"谁能给我开shared-base的pull权限？"——然后等待1-2小时直到有人响应。安全部门的统计显示：平均每个权限申请从提出到完成的耗时为**2.7小时**，其中80%的时间耗费在"寻找正确的审批人"和"确认应该分配什么角色"上。

这些痛点的根源是同一个：团队没有建立起基于RBAC模型的、系统性分层分级的权限管理机制。权限成为了"社交行为"而非"安全策略"。

---

## 2 项目设计——剧本式交锋对话

**场景：云鲸科技"Harbor权限治理"专项会议——CTO王海、平台架构师大师、安全工程师小白、以及被权限问题困扰已久的开发代表小胖。会议室投屏上滚动播放着权限审计报告中的"血淋淋"数据。**

**小胖**（把咖啡杯往桌上一放）："说实话，我觉得这份报告有点小题大做。权限又不是什么核按钮——同事之间给个权限怎么了？谁需要我给谁开就是了。外包也是人，看看镜像又能咋地？我们都是这么干了两年的，不也啥事没出？"

**大师**（站起来走到白板前，拿起红色马克笔）："小胖，我给你讲个真实发生过的故事。2019年，美国一家SaaS公司——CodeSpaces——倒闭了。不是因为产品不行，不是因为没客户。而是因为有人拿到了他们AWS账号的admin权限，删除了所有S3存储桶和EC2实例，包括所有备份。公司从收到攻击告警到数据全部消失——只用了12个小时。"

他停顿了一下："事后调查发现——那个admin权限是18个月前给一个临时合作的外部顾问开的。顾问走了，权限没回收。18个月后，有人用这个凭证实施了毁灭性操作。我们不认识CodeSpaces的创始人，但我们面临的是同样的风险——**未回收的权限，就是一颗不知道什么时候会爆炸的定时炸弹。**"

**技术映射**：Harbor的角色分为两个独立层级——**系统级（System Role）** 和**项目级（Project Role）**。系统级角色只有两种：System Admin（系统管理员，全局操作权限）和 System User（普通用户，无全局操作权限，仅能操作自己加入的项目）。项目级角色有五种：项目管理员（1）、维护者（3）、开发者（2）、访客（4）、受限访客（5）。一个用户可以拥有一个系统级角色 + 多个项目级角色（在不同项目中可以不同）。

**小白**（从笔记本电脑屏幕后抬起头，把一张权限矩阵图投影到屏幕上）："大师说得很对。我花了整个周末把Harbor五种项目角色的权限差异整理成了这张矩阵。但说实话——我最困惑的是'维护者'（role_id=3）和'开发者'（role_id=2）的区别。文档上说『维护者可以扫描和删除Artifact』——但开发者也可以啊？到底差在哪？"

**大师**（在矩阵图上画了两条红线）："你看——五者的核心差异其实只有两条分割线：**能不能管理成员**和**能不能修改项目配置**。"

```
角色              Push   Pull   Delete   Scan   Manage Members   Modify Config
项目管理员(1)      ✅     ✅      ✅       ✅         ✅               ✅
维护者(3)          ✅     ✅      ✅       ✅         ❌               ✅
开发者(2)          ✅     ✅      ✅       ✅         ❌               ❌
访客(4)            ❌     ✅      ❌       ❌         ❌               ❌
受限访客(5)        ❌    ✅(白名单) ❌       ❌         ❌               ❌
```

"区别就是两条线——第一条线是'人'：能不能添加/删除项目成员、能不能修改成员的角角色？第二条线是'配置'：能不能修改项目配额、CVE阻止策略、镜像代理策略？"

"开发者可以推送/拉取/删除/扫描镜像——日常开发足够了。但**不能添加成员**（防止随便拉人进来）和**不能改项目配置**（防止不小心关闭了CVE扫描策略）。维护者比开发者多了一条：**可以改项目配置**但不能管人——适合Senior开发或DevOps，他们需要调整项目的扫描策略、配额等，但不需要管理团队成员。"

**小胖**（挠头）："那如果一个开发者确实需要临时给别人开个权限呢——比如他生病了，需要找人帮他发布？难道每次都要找项目管理员？"

**大师**："对，就是要找项目管理员。这不是'麻烦'——这是**职责分离**。你想想，如果每个开发者都能随便添加成员，那今天加一个、明天加一个，三个月后项目的成员列表就是一团乱麻——就像我们现在的状态——没人知道谁有权限、谁该有权限。这就是权限失控的根本原因——**谁都能给权限，等于没人对权限负责**。项目管理员是项目权限的'守门人'——所有权限变更都必须经过他，审计时就有一个清晰的追溯链。"

**技术映射**：职责分离（Separation of Duties）是RBAC模型的核心原则之一。Harbor通过将"成员管理"权限只赋予项目管理员，实现了一个关键的安全约束：**操作者（开发者）和授权者（项目管理员）必须是不同的人**。这防止了开发者"自己给自己授权"绕过审批流程。

**小白**（若有所思）："那系统管理员和项目管理员之间是什么关系？系统管理员是不是自动具备所有项目的管理员权限？"

**大师**："这是一个非常关键但常被误解的设计。答案是否定的——**系统管理员不等于任何项目的成员**。"

他在白板上画了一个嵌套的维恩图：

```
  ┌────────────────────────────────────────────┐
  │          System Admin (系统管理员)           │
  │  ✅ 创建/删除项目                            │
  │  ✅ 管理所有用户（创建/禁用/删除）              │
  │  ✅ 配置系统参数（LDAP/OIDC/GC/配额默认值）     │
  │  ✅ 查看所有项目的审计日志                     │
  │  ❌ Push/Pull镜像（除非加入某个项目）           │
  │  ❌ 查看某个项目的成员列表（除非加入该项目）       │
  └────────────────────────────────────────────┘
                        │
                        │ 需要"显式加入"
                        ▼
  ┌────────────────────────────────────────────┐
  │          Project Admin (项目管理员)           │
  │  ✅ Push/Pull/Delete/Scan                   │
  │  ✅ 管理项目成员（添加/删除/修改角色）           │
  │  ✅ 修改项目配置                              │
  └────────────────────────────────────────────┘
```

"系统管理员是'Harbor平台的运维者'——他负责维护这个平台本身的健康运行。项目管理员是'项目的管理者'——他负责管理某个特定项目内的镜像和成员。两者职责不同，**互不包含**。"

"这个设计是非常有意的——如果SysAdmin自动拥有所有项目的所有权，那SysAdmin就成了单点权限炸弹——任何一个SysAdmin账号泄露，所有项目的镜像全部沦陷。而通过'显式加入'机制 ——SysAdmin要想操作某个项目，必须先把自己加为成员——这样审计日志中就会有一条清晰的记录：『2024-05-10 14:23:07, 系统管理员zhangsan将自己添加为order-platform项目的项目管理员』——为事后追溯留下了痕迹。"

**技术映射**：Harbor的SysAdmin权限是通过 `sysadmin_flag=true` 字段在数据库层面控制的。而项目级角色是存储在 `project_member` 关联表中——每个项目成员关系是一条独立记录。这意味着SysAdmin和Project Admin在数据库层面没有任何耦合——SysAdmin不自动具备任何项目的成员身份。这是一个有意的安全边界设计。

**小胖**（举手）："受限访客（role_id=5）和普通访客（role_id=4）呢？听起来像是'访客Pro Max'——限制版的？"

**大师**（笑了）："更像是'访客Lite'。普通访客能看到并拉取项目下**所有**仓库的镜像。受限访客只能看到**被管理员明确授权**的仓库——其他仓库对他而言是'透明的'，就像不存在一样。"

他举例说明：

```
项目 order-platform 包含5个仓库：
  ├── order-service       ← 对外包可见（被勾选）
  ├── inventory-service   ← 对外包可见（被勾选）
  ├── payment-adapter     ← 对外包隐藏 ❌
  ├── internal-tools      ← 对外包隐藏 ❌
  └── account-mock        ← 对外包隐藏 ❌

外包团队成员（受限访客）在Portal中看到的：
  order-platform/
  ├── order-service/
  └── inventory-service/
  
——另外3个仓库对他完全不可见，也无法通过目录遍历发现
```

**技术映射**：受限访客的仓库级白名单通过 `project_member` 表中的 `access_blob` 字段存储——它是一个JSON数组，记录了该成员被授权访问的仓库列表。Harbor Core在处理受限访客的List Repositories请求时，会在SQL查询中通过 `WHERE repository.name IN (...)` 过滤掉未授权的仓库。这个过滤发生在数据库查询层，确保了安全性。

**小白**（在笔记本上快速敲击了一阵，然后抬头）："我想探讨一个实际的复杂场景。我们公司有三个业务部门——订单组、支付组、用户组。每个组有自己的项目。但同时有一个 `shared-base` 项目——存放了所有组共用的基础镜像。那问题来了："

"（1）订单组需要从 `shared-base` 拉取基础镜像，但不应该能push进 `shared-base`。角色应该是什么？"

"（2）支付组有一个外包团队，外包人员只需要支付组中 `payment-sdk` 这一个仓库的pull权限。角色应该是什么？"

"（3）平台组的张三——他是 `shared-base` 的项目管理员，但他有时需要查看订单组镜像的扫描报告（但不应该能push/delete）。这种'跨项目的读权限'怎么实现？"

**大师**（眼中闪过赞许的光）："三个问题都切中要害。一个一个来。"

"（1）**订单组在 `shared-base` 项目中最合适的角色是'访客'（role_id=4）。** 访客可以pull项目中的所有镜像，但不能push、不能delete、不能改配置。如果将来需要更精细的控制（只允许pull某些基础镜像），可以用'受限访客'（role_id=5）。"

"（2）**外包人员在支付组中最合适的角色是'受限访客'（role_id=5）。** 管理员在Portal中勾选 `payment-sdk` 仓库——外包人员只能看到并拉取这个仓库，支付组中的其他仓库（如 `payment-core`、`payment-admin`）对他完全不可见。"

"（3）**张三作为 `shared-base` 的项目管理员，要在订单组中获得'只读+扫描'权限——最佳角色是'访客'（role_id=4）。** 访客可以pull所有镜像并查看扫描报告。但如果张三还需要在订单组中触发扫描（`scan` 操作），那需要'开发者'（role_id=2）及以上——访客和受限访客都没有scan权限。"

他在白板上总结：

```
用户在一个项目中的角色 = f(他需要在这个项目中做什么操作)

Pull 镜像 → 访客/受限访客 就够了
Pull + Push + Delete → 开发者
Pull + Push + Delete + 改配置 → 维护者
以上全部 + 管成员 → 项目管理员
```

**小胖**（突然激动）："我明白了！所以核心原则就是——**能不给就不给，能少给就少给**！对不对？就像自助餐——你能吃多少拿多少，别上来就端走整盘！"

**大师**（大笑）："小胖，你这个比喻是今天全场最佳。这就是**最小权限原则**（Principle of Least Privilege）——只给用户完成工作所必需的、最小范围的权限。不是不信任同事，而是不想让任何人的失误放大成灾难。"

---

## 3 项目实战

### 3.1 环境要求

| 组件 | 版本/配置要求 | 说明 |
|------|-------------|------|
| Harbor | v2.12 | 完整部署，Core + JobService正常运行 |
| 项目准备 | 至少创建3个测试项目 | `order-platform`、`payment-platform`、`shared-base` |
| 用户准备 | 至少5个本地测试用户 | 用于验证不同角色的权限边界 |
| curl + jq | 任意版本 | API测试工具 |
| Docker客户端 | 20.10+ | 用于测试push/pull权限 |

```bash
# 确认前置条件
# 1. 确认Harbor运行状态
curl -s -u admin:Harbor12345 https://harbor.yunjingkeji.com/api/v2.0/health | jq '.status'
# 预期：healthy

# 2. 确认测试项目存在
curl -s -u admin:Harbor12345 \
  "https://harbor.yunjingkeji.com/api/v2.0/projects?page_size=10" | \
  jq '.[].name'
```

### 3.2 步骤一：创建测试用户并理解系统角色

**目标：** 创建5个不同角色的测试用户，验证系统级角色（SysAdmin vs System User）的差异。

```bash
# 1. 批量创建测试用户
USERS=("alice-platform-lead:Alice@2024!:爱丽丝 (平台负责人)" 
       "bob-dev:Bob@2024!:鲍勃 (订单组开发)" 
       "charlie-senior:Charlie@2024!:查理 (支付组高级开发)" 
       "diana-qa:Diana@2024!:戴安娜 (QA测试)" 
       "eric-outsource:Eric@2024!:埃里克 (外包前端)")

for user_info in "${USERS[@]}"; do
  IFS=':' read -r username password realname <<< "$user_info"
  curl -X POST -u admin:Harbor12345 \
    -H "Content-Type: application/json" \
    -d "{\"username\":\"$username\",\"email\":\"${username}@yunjingkeji.com\",\"password\":\"$password\",\"realname\":\"$realname\"}" \
    https://harbor.yunjingkeji.com/api/v2.0/users
done

# 预期：5次 HTTP 201 Created
```

```bash
# 2. 赋予alice-platform-lead系统管理员权限
ALICE_ID=$(curl -s -u admin:Harbor12345 \
  "https://harbor.yunjingkeji.com/api/v2.0/users?username=alice-platform-lead" | \
  jq '.[0].user_id')

curl -X PUT -u admin:Harbor12345 \
  -H "Content-Type: application/json" \
  -d '{"sysadmin_flag":true}' \
  "https://harbor.yunjingkeji.com/api/v2.0/users/$ALICE_ID/sysadmin"

# 验证Alice现在是SysAdmin
curl -s -u alice-platform-lead:Alice@2024! \
  "https://harbor.yunjingkeji.com/api/v2.0/users" | \
  jq '.[] | select(.username=="alice-platform-lead") | .sysadmin_flag'
# 预期：true

# 3. 验证SysAdmin可以创建项目
curl -X POST -u alice-platform-lead:Alice@2024! \
  -H "Content-Type: application/json" \
  -d '{"project_name":"test-sysadmin-project","public":false,"storage_limit":10737418240}' \
  https://harbor.yunjingkeji.com/api/v2.0/projects
# 预期：HTTP 201 Created
```

### 3.3 步骤二：分配五级项目角色并验证权限边界

**目标：** 将不同用户以不同角色添加到 `order-platform` 项目，验证每种角色的权限边界。

```bash
# 1. 获取项目ID
ORDER_ID=$(curl -s -u admin:Harbor12345 \
  "https://harbor.yunjingkeji.com/api/v2.0/projects?name=order-platform" | \
  jq '.[0].project_id')
echo "order-platform project_id: $ORDER_ID"
```

```bash
# 2. 分配五种角色
# Bob → 项目管理员 (role_id=1)
curl -X POST -u admin:Harbor12345 \
  -H "Content-Type: application/json" \
  -d '{"role_id":1,"member_user":{"username":"bob-dev"}}' \
  "https://harbor.yunjingkeji.com/api/v2.0/projects/$ORDER_ID/members"

# Charlie → 维护者 (role_id=3)
curl -X POST -u admin:Harbor12345 \
  -H "Content-Type: application/json" \
  -d '{"role_id":3,"member_user":{"username":"charlie-senior"}}' \
  "https://harbor.yunjingkeji.com/api/v2.0/projects/$ORDER_ID/members"

# Diana → 开发者 (role_id=2)
curl -X POST -u admin:Harbor12345 \
  -H "Content-Type: application/json" \
  -d '{"role_id":2,"member_user":{"username":"diana-qa"}}' \
  "https://harbor.yunjingkeji.com/api/v2.0/projects/$ORDER_ID/members"

# Eric → 受限访客 (role_id=5)
curl -X POST -u admin:Harbor12345 \
  -H "Content-Type: application/json" \
  -d '{"role_id":5,"member_user":{"username":"eric-outsource"}}' \
  "https://harbor.yunjingkeji.com/api/v2.0/projects/$ORDER_ID/members"
```

```bash
# 3. 查看项目的完整成员列表
curl -s -u admin:Harbor12345 \
  "https://harbor.yunjingkeji.com/api/v2.0/projects/$ORDER_ID/members" | \
  jq '.[] | {username: .entity_name, role: .role_name, role_id: .role_id}'

# 预期输出：
# {"username":"bob-dev","role":"project admin","role_id":1}
# {"username":"charlie-senior","role":"maintainer","role_id":3}
# {"username":"diana-qa","role":"developer","role_id":2}
# {"username":"eric-outsource","role":"limited guest","role_id":5}
```

### 3.4 步骤三：验证角色权限矩阵——push/pull/delete/scan/member/config

**目标：** 逐一验证每种角色的每种操作，构建实测权限矩阵。

```bash
# === 测试1：Push权限 ===
# Diana (开发者) push → 应成功
echo "Diana@2024!" | docker login harbor.yunjingkeji.com -u diana-qa --password-stdin
echo "v1.0" > test.txt && docker build -t harbor.yunjingkeji.com/order-platform/test-app:v1.0 .
docker push harbor.yunjingkeji.com/order-platform/test-app:v1.0
# 预期：推送成功

# Eric (受限访客) push → 应失败
echo "Eric@2024!" | docker login harbor.yunjingkeji.com -u eric-outsource --password-stdin
docker push harbor.yunjingkeji.com/order-platform/test-app:v1.1
# 预期：403 Forbidden - "unauthorized: no permissions to push to this repository"

# === 测试2：Delete权限 ===
# Diana (开发者) delete → 应成功
curl -X DELETE -u diana-qa:Diana@2024! \
  "https://harbor.yunjingkeji.com/api/v2.0/projects/order-platform/repositories/test-app/artifacts/v1.0/tags/v1.0"
# 预期：HTTP 200

# Eric (受限访客) delete → 应失败
curl -X DELETE -u eric-outsource:Eric@2024! \
  "https://harbor.yunjingkeji.com/api/v2.0/projects/order-platform/repositories/test-app/artifacts/v1.1"
# 预期：HTTP 403 Forbidden

# === 测试3：管理成员权限 ===
# Bob (项目管理员) 添加成员 → 应成功
curl -X POST -u bob-dev:Bob@2024! \
  -H "Content-Type: application/json" \
  -d '{"role_id":2,"member_user":{"username":"diana-qa"}}' \
  "https://harbor.yunjingkeji.com/api/v2.0/projects/$ORDER_ID/members"
# 如果用户已存在则返回409，否则201

# Charlie (维护者) 添加成员 → 应失败
curl -X POST -u charlie-senior:Charlie@2024! \
  -H "Content-Type: application/json" \
  -d '{"role_id":4,"member_user":{"username":"eric-outsource"}}' \
  "https://harbor.yunjingkeji.com/api/v2.0/projects/$ORDER_ID/members"
# 预期：HTTP 403 Forbidden

# Diana (开发者) 查看成员列表 → 应失败
curl -s -u diana-qa:Diana@2024! \
  "https://harbor.yunjingkeji.com/api/v2.0/projects/$ORDER_ID/members"
# 预期：HTTP 403 Forbidden

# === 测试4：修改项目配置权限 ===
# Bob (项目管理员) 修改CVE策略 → 应成功
curl -X PUT -u bob-dev:Bob@2024! \
  -H "Content-Type: application/json" \
  -d '{"prevent_vul":true,"severity":"critical","scan_on_push":true}' \
  "https://harbor.yunjingkeji.com/api/v2.0/projects/$ORDER_ID/prevent-vulnerability"
# 预期：HTTP 200

# Charlie (维护者) 修改CVE策略 → 应成功（维护者可以改配置）
curl -X PUT -u charlie-senior:Charlie@2024! \
  -H "Content-Type: application/json" \
  -d '{"prevent_vul":true,"severity":"high","scan_on_push":true}' \
  "https://harbor.yunjingkeji.com/api/v2.0/projects/$ORDER_ID/prevent-vulnerability"
# 预期：HTTP 200

# Diana (开发者) 修改CVE策略 → 应失败
curl -X PUT -u diana-qa:Diana@2024! \
  -H "Content-Type: application/json" \
  -d '{"prevent_vul":false,"severity":"critical","scan_on_push":true}' \
  "https://harbor.yunjingkeji.com/api/v2.0/projects/$ORDER_ID/prevent-vulnerability"
# 预期：HTTP 403 Forbidden
```

### 3.5 步骤四：配置受限访客的仓库白名单

**目标：** 实践受限访客的仓库级访问控制——只允许访问指定的仓库。

```bash
# 1. 先确保order-platform项目下有多个仓库
# 推送3个测试仓库
for repo in order-service payment-adapter internal-tools; do
  echo "$repo v1.0" > test.txt
  docker build -t harbor.yunjingkeji.com/order-platform/$repo:v1.0 .
  docker push harbor.yunjingkeji.com/order-platform/$repo:v1.0
done
```

```bash
# 2. 设置Eric（受限访客）只允许访问 order-service 和 payment-adapter
# 注意：受限访客的仓库白名单通过Portal设置最方便
# Portal路径：项目 → 成员 → Eric → 编辑 → 勾选允许的仓库
# 
# 通过API设置（Harbor 2.10+支持）:
ERIC_ID=$(curl -s -u admin:Harbor12345 \
  "https://harbor.yunjingkeji.com/api/v2.0/users?username=eric-outsource" | \
  jq '.[0].user_id')

# 更新受限访客的仓库权限（通过更新成员角色的access字段）
curl -X PUT -u admin:Harbor12345 \
  -H "Content-Type: application/json" \
  -d "{
    \"role_id\": 5,
    \"member_user\": {
      \"user_id\": $ERIC_ID
    }
  }" \
  "https://harbor.yunjingkeji.com/api/v2.0/projects/$ORDER_ID/members/$ERIC_ID"
```

```bash
# 3. 验证：Eric登录后只能看到两个仓库
echo "Eric@2024!" | docker login harbor.yunjingkeji.com -u eric-outsource --password-stdin

# Eric pull order-service → 应成功
docker pull harbor.yunjingkeji.com/order-platform/order-service:v1.0
# 预期：拉取成功

# Eric pull internal-tools → 应失败
docker pull harbor.yunjingkeji.com/order-platform/internal-tools:v1.0
# 预期：Error: requested access to the resource is denied
# （因为 internal-tools 不在Eric的白名单中）
```

### 3.6 步骤五：实现跨项目权限管理——一个用户在多个项目中的不同角色

**目标：** 掌握用户在不同项目中承担不同角色的配置方法。

```bash
# 场景：Bob在 order-platform 是项目管理员，但在 shared-base 只需要pull基础镜像
SHARED_ID=$(curl -s -u admin:Harbor12345 \
  "https://harbor.yunjingkeji.com/api/v2.0/projects?name=shared-base" | \
  jq '.[0].project_id')

# Bob → shared-base 的访客（仅pull）
curl -X POST -u admin:Harbor12345 \
  -H "Content-Type: application/json" \
  -d '{"role_id":4,"member_user":{"username":"bob-dev"}}' \
  "https://harbor.yunjingkeji.com/api/v2.0/projects/$SHARED_ID/members"

# 验证：Bob在 order-platform 是管理员（能管理成员）
curl -s -u bob-dev:Bob@2024! \
  "https://harbor.yunjingkeji.com/api/v2.0/projects/$ORDER_ID/members" | \
  jq '.[].entity_name'
# 预期：返回成员列表（证明Bob是项目管理员）

# 验证：Bob在 shared-base 只是访客（不能查看成员列表）
curl -s -u bob-dev:Bob@2024! \
  "https://harbor.yunjingkeji.com/api/v2.0/projects/$SHARED_ID/members"
# 预期：HTTP 403 Forbidden（证明Bob只是访客，不能管理成员）

# 验证：Bob可以从 shared-base pull镜像
docker pull harbor.yunjingkeji.com/shared-base/java-base:v1.0
# 预期：拉取成功（访客有pull权限）
```

### 3.7 步骤六：权限变更完整流程——转岗自动化脚本

**目标：** 实现一个自动化脚本：输入用户名和源/目标项目，完成权限回收和重新分配。

```bash
#!/bin/bash
# 文件名: harbor_role_transfer.sh
# 用途: 用户转岗时自动化权限变更

USERNAME="${1:-charlie-senior}"
SOURCE_PROJECT="${2:-order-platform}"
TARGET_PROJECT="${3:-payment-platform}"
HARBOR_URL="https://harbor.yunjingkeji.com"
ADMIN_AUTH="admin:Harbor12345"

echo "=== Harbor角色转移: $USERNAME ==="
echo "   从: $SOURCE_PROJECT → 到: $TARGET_PROJECT"

# 1. 获取用户ID
USER_ID=$(curl -s -u "$ADMIN_AUTH" \
  "$HARBOR_URL/api/v2.0/users?username=$USERNAME" | jq '.[0].user_id')
echo "[1/5] 用户ID: $USER_ID"

# 2. 查询用户在源项目中的当前角色（审计快照）
SOURCE_ID=$(curl -s -u "$ADMIN_AUTH" \
  "$HARBOR_URL/api/v2.0/projects?name=$SOURCE_PROJECT" | jq '.[0].project_id')
OLD_ROLE=$(curl -s -u "$ADMIN_AUTH" \
  "$HARBOR_URL/api/v2.0/projects/$SOURCE_ID/members/$USER_ID" | \
  jq -r '.role_name')
echo "[2/5] 当前角色: $SOURCE_PROJECT → $OLD_ROLE"

# 3. 回收源项目权限
curl -X DELETE -u "$ADMIN_AUTH" \
  "$HARBOR_URL/api/v2.0/projects/$SOURCE_ID/members/$USER_ID"
echo ""
echo "[3/5] 已回收 $SOURCE_PROJECT 权限"

# 4. 分配目标项目权限（默认为开发者）
TARGET_ID=$(curl -s -u "$ADMIN_AUTH" \
  "$HARBOR_URL/api/v2.0/projects?name=$TARGET_PROJECT" | jq '.[0].project_id')
curl -X POST -u "$ADMIN_AUTH" \
  -H "Content-Type: application/json" \
  -d "{\"role_id\":2,\"member_user\":{\"user_id\":$USER_ID}}" \
  "$HARBOR_URL/api/v2.0/projects/$TARGET_ID/members"
echo ""
echo "[4/5] 已分配 $TARGET_PROJECT 开发者角色"

# 5. 验证：确认源项目已无权限
VERIFY=$(curl -s -u "$ADMIN_AUTH" \
  "$HARBOR_URL/api/v2.0/projects/$SOURCE_ID/members/$USER_ID" | \
  jq -r '.role_name // "NONE"')
echo "[5/5] 验证: $SOURCE_PROJECT → $VERIFY (预期: NONE)"
echo "=== 转移完成 ==="
```

### 3.8 步骤七：审计所有用户的跨项目权限全景

**目标：** 生成一份完整的"用户-项目-角色"矩阵报告，用于定期安全审计。

```bash
#!/bin/bash
# 权限审计脚本：输出所有用户的跨项目权限矩阵

HARBOR_URL="https://harbor.yunjingkeji.com"
ADMIN_AUTH="admin:Harbor12345"

echo "# Harbor 权限全景审计报告"
echo "## 生成时间: $(date '+%Y-%m-%d %H:%M:%S')"
echo ""
echo "| 用户名 | 项目名 | 角色 | 加入时间 |"
echo "|--------|--------|------|---------|"

# 遍历所有项目
for PROJ_ID in $(curl -s -u "$ADMIN_AUTH" \
  "$HARBOR_URL/api/v2.0/projects?page_size=50" | jq '.[].project_id'); do
  
  PROJ_NAME=$(curl -s -u "$ADMIN_AUTH" \
    "$HARBOR_URL/api/v2.0/projects/$PROJ_ID" | jq -r '.name')
  
  # 遍历项目中的所有成员
  curl -s -u "$ADMIN_AUTH" \
    "$HARBOR_URL/api/v2.0/projects/$PROJ_ID/members" | \
    jq -r ".[] | [.entity_name, \"$PROJ_NAME\", .role_name, .creation_time] | @tsv" | \
    while IFS=$'\t' read -r user proj role time; do
      echo "| $user | $proj | $role | $time |"
    done
done

echo ""
echo "---"
echo "*报告由自动化审计脚本生成*"
```

**预期输出片段：**
```
| 用户名 | 项目名 | 角色 | 加入时间 |
|--------|--------|------|---------|
| bob-dev | order-platform | project admin | 2024-05-01 |
| bob-dev | shared-base | guest | 2024-05-02 |
| charlie-senior | order-platform | maintainer | 2024-05-01 |
| charlie-senior | payment-platform | developer | 2024-05-05 |
| diana-qa | order-platform | developer | 2024-05-01 |
| eric-outsource | order-platform | limited guest | 2024-05-01 |
```

---

### 3.9 排坑指南

#### 坑1：将已有角色的用户重新添加时报错"用户已存在"

**现象：** 尝试用 `POST /projects/{id}/members` 将一个用户添加到项目中，返回 `HTTP 409 Conflict`，消息："the user is already a member of the project"。

**根因分析：** `POST` 是创建新成员关系的操作。如果一个用户已经在项目中（无论是什么角色），再次 `POST` 添加会触发唯一约束冲突。用户-项目的关系在数据库中是一对一的——一个用户在一个项目中只能有一个角色。

**解决方法：**
```bash
# 正确做法：先查询当前成员信息，再用 PUT 更新角色
USER_ID=5  # 要修改的用户ID

# 查询当前成员信息
curl -s -u admin:Harbor12345 \
  "https://harbor.yunjingkeji.com/api/v2.0/projects/$ORDER_ID/members/$USER_ID" | jq '.'

# 如果返回了成员信息，说明已存在——用PUT更新角色：
curl -X PUT -u admin:Harbor12345 \
  -H "Content-Type: application/json" \
  -d '{"role_id":3}' \
  "https://harbor.yunjingkeji.com/api/v2.0/projects/$ORDER_ID/members/$USER_ID"
# 预期：HTTP 200（角色从开发者更新为维护者）

# 如果返回404，说明不存在——用POST创建新成员：
curl -X POST -u admin:Harbor12345 \
  -H "Content-Type: application/json" \
  -d '{"role_id":2,"member_user":{"user_id":5}}' \
  "https://harbor.yunjingkeji.com/api/v2.0/projects/$ORDER_ID/members"
```

#### 坑2：受限访客在Portal中看不到任何仓库

**现象：** 给一个用户分配了受限访客（role_id=5）角色后，该用户在Portal中进入项目，仓库列表为空——甚至连应该能看到的仓库都没有。

**根因分析：** 受限访客的默认行为是"默认不授权任何仓库"。在管理员通过Portal或API显式勾选允许的仓库之前，受限访客对该项目下所有仓库的访问都是被拒绝的。这不是Bug——这正是"受限"的含义。

**解决方法：**
```
Portal操作路径（最直接的方式）：
1. 以项目管理员身份登录Harbor Portal
2. 进入目标项目 → 成员标签页
3. 找到受限访客用户 → 点击用户名旁的编辑（铅笔）图标
4. 在弹出的"仓库权限"对话框中勾选允许访问的仓库列表
5. 点击保存 → 受限访客立即可以看到勾选的仓库

API操作（适用于批量配置）：
# 当前Harbor API没有直接的"设置受限访客仓库白名单"端点
# 替代方案：通过修改成员记录来更新仓库访问列表
```

#### 坑3：系统管理员无法push镜像到项目

**现象：** 一个系统管理员尝试 `docker push` 到一个项目，返回 `403 Forbidden` 或 `unauthorized`。

**根因分析：** **系统管理员不等于任何项目的成员。** SysAdmin可以做系统级操作（创建项目、管理用户），但要操作项目内的镜像（push/pull/delete），必须显式地被添加为该项目的成员——哪怕角色是访客也行。

**解决方法：**
```bash
# 将系统管理员添加为目标项目的成员
SYSADMIN_USERNAME="alice-platform-lead"
SYSADMIN_ID=$(curl -s -u admin:Harbor12345 \
  "https://harbor.yunjingkeji.com/api/v2.0/users?username=$SYSADMIN_USERNAME" | \
  jq '.[0].user_id')

curl -X POST -u admin:Harbor12345 \
  -H "Content-Type: application/json" \
  -d "{\"role_id\":2,\"member_user\":{\"user_id\":$SYSADMIN_ID}}" \
  "https://harbor.yunjingkeji.com/api/v2.0/projects/$ORDER_ID/members"

# 此后SysAdmin就可以正常push到该项目了
```

#### 坑4：更新角色后旧权限缓存未刷新

**现象：** 通过API将一个用户的角色从"开发者"降级为"访客"后，该用户仍然能push镜像——持续了约5分钟才生效。

**根因分析：** Harbor Core的权限检查有一个内存中缓存层（基于Redis或不基于Redis的进程内缓存）。角色变更后，数据库中的记录已被更新，但Core的权限缓存可能还没有失效——缓存过期时间通常为5分钟。在这5分钟的窗口期内，旧角色仍然生效。

**解决方法：**
```bash
# 方案A：等待最多5分钟，缓存自动过期
# 方案B：重启Harbor Core强制清除缓存（不推荐，影响所有用户）
docker restart harbor-core
# 注意：重启期间所有API请求都会失败

# 方案C：如果在安全敏感的场景中（如回收离职员工的权限），
# 同时在LDAP/AD层面禁用账号——双重保险：
# docker login时会先验证LDAP/AD凭证，即使Harbor缓存未过期，
# LDAP/AD拒绝后登录会立即失败
```

---

## 4 项目总结

### 4.1 Harbor RBAC 权限矩阵完整版

| 操作 | 系统管理员 (SysAdmin) | 项目管理员 (1) | 维护者 (3) | 开发者 (2) | 访客 (4) | 受限访客 (5) |
|------|---------------------|---------------|-----------|-----------|---------|------------|
| **系统级操作** | | | | | | |
| 创建/删除项目 | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| 管理用户（创建/禁用/删除） | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| 配置系统参数（LDAP/OIDC/GC） | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| 查看所有项目 | ✅ | ❌（仅自己加入的） | ❌ | ❌ | ❌ | ❌ |
| 查看所有审计日志 | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| **项目级操作**（需加入项目） | | | | | | |
| Push 镜像 | ❌（未加项目时） | ✅ | ✅ | ✅ | ❌ | ❌ |
| Pull 镜像（公共项目） | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Pull 镜像（私有项目） | ❌（需加项目） | ✅ | ✅ | ✅ | ✅ | ✅（白名单仓库） |
| 删除镜像/Artifact | ❌（需加项目） | ✅ | ✅ | ✅ | ❌ | ❌ |
| 触发漏洞扫描 | ❌（需加项目） | ✅ | ✅ | ✅ | ❌ | ❌ |
| 查看扫描报告 | ❌（需加项目） | ✅ | ✅ | ✅ | ✅ | ✅ |
| 管理项目成员 | ❌（需加项目） | ✅ | ❌ | ❌ | ❌ | ❌ |
| 修改项目配置 | ❌（需加项目） | ✅ | ✅ | ❌ | ❌ | ❌ |
| 管理CVE白名单 | ❌（需加项目） | ✅ | ✅ | ❌ | ❌ | ❌ |
| 管理标签保留策略 | ❌（需加项目） | ✅ | ✅ | ❌ | ❌ | ❌ |
| 管理机器人账户 | ❌（需加项目） | ✅ | ✅ | ❌ | ❌ | ❌ |

### 4.2 角色分配策略——按组织角色推荐

| 组织角色 | 推荐Harbor系统角色 | 推荐项目角色（本团队项目） | 推荐项目角色（跨团队项目） | 说明 |
|---------|------------------|---------------------|---------------------|------|
| **平台运维工程师（1-2人）** | 系统管理员 | 项目管理员（所有项目） | — | 全局管理、紧急故障处理。但不参与日常Push/Pull |
| **部门Tech Lead** | 普通用户 | 项目管理员 | 访客 | 负责本部门项目的成员管理和配置。跨部门时只需要Pull |
| **高级开发/Senior SDE** | 普通用户 | 维护者 | 访客 | 可以调整本项目的扫描策略和配额，但不能随意添加成员 |
| **日常开发人员** | 普通用户 | 开发者 | 访客 | Push/Pull/Delete/Scan——日常所需的全部权限 |
| **QA/测试工程师** | 普通用户 | 访客 | 访客 | 仅需Pull镜像进行测试，不应该push或delete |
| **外包/外部合作人员** | 普通用户 | 受限访客 | 受限访客 | 只能访问被明确授权的仓库，且仅Pull |
| **CI/CD系统（Jenkins）** | —（机器人账户） | 开发者（机器人） | 访客（机器人） | 按项目创建独立的机器人账户 |

### 4.3 适用场景

- **多团队多项目权限隔离：** 大型组织中每个业务团队拥有1-3个Harbor项目。通过项目管理员 → 维护者 → 开发者 → 访客的四级分层，确保每个团队的镜像主权不受侵犯。跨团队协作时使用访客角色。
- **外包和合作伙伴的精细访问控制：** 使用受限访客（role_id=5）——外包人员只能看到并拉取被明确授权的仓库，无法发现项目中的其他敏感镜像。即使外包人员"探索性"地尝试遍历仓库，也无法发现隐藏的仓库。
- **人员入/转/离的权限自动化管理：** 结合LDAP/AD——入职时通过LDAP组自动导入用户并分配预设角色；转岗时运行权限变更脚本（本文步骤六）；离职时在AD中禁用账号即可同时阻断Harbor登录。
- **CI/CD安全凭证管理：** 每个CI流水线使用独立的机器人账户——一个Token泄露只影响一个项目。机器人账户只能通过API/CLI操作，无法登录Portal查看系统信息和审计日志。
- **权限合规审计：** 每季度运行权限全景审计脚本（本文步骤七），生成完整的"用户-项目-角色"矩阵——快速识别权限异常（如某个员工拥有超出预期的管理员角色、某个离职员工的账号仍未禁用等）。

### 4.4 不适用场景

- **2-5人的微型团队：** 如果团队规模极小且所有成员都在同一个项目内工作，五级角色的精细管理可能显得"过度设计"。此时建议简化配置——所有成员都设为"开发者"（role_id=2），技术Leader设为"项目管理员"（role_id=1）。
- **标签级别的权限控制需求：** Harbor的受限访客只能做到"仓库级"的白名单。如果需要在更细粒度（标签级）上控制访问——例如只允许外包人员拉取 `release-*` 标签，不允许拉取 `latest`——Harbor原生不支持。变通方案是：为不同的标签创建不同的仓库或项目，分别在受限访客中授权。

### 4.5 五项关键注意事项

1. **系统管理员是一个"重权限"角色——严格控制数量。** 建议SysAdmin账号不超过3个（含admin）。SysAdmin能创建/删除项目、管理所有用户、修改系统配置——任何一条操作失误都可能影响整个Harbor实例。建议为SysAdmin账号开启双因素认证（如果使用OIDC，可以在IDP层面配置MFA）。
2. **"开发者"角色默认可以删除镜像——敏感项目需额外配置。** 在项目配置中有一个选项叫"禁止开发者和访客删除镜像"（`prevent_artifact_deletion`）。对于生产镜像项目，强烈建议勾选此选项，只允许项目管理员和维护者删除镜像。
3. **角色变更后存在5分钟缓存窗口——敏感操作需配合LDAP/AD。** Core的权限缓存可能导致角色降级延迟生效。对于离职员工的权限回收，建议同时在LDAP/AD层面禁用账号——双重保险确保登录即被阻断。
4. **受限访客的仓库白名单需在角色分配后手动配置。** 仅分配受限访客角色（role_id=5）是不够的——角色分配后，管理员必须在Portal中勾选允许访问的仓库列表。如果忘记这一步，受限访客将看不到任何仓库。
5. **审计日志记录每一次角色变更——善用审计日志追溯。** 每次 `POST`/`PUT`/`DELETE` 项目成员的操作都会被记录在审计日志中，包括操作者、被操作者、时间、IP、角色变化。当发生权限事故时，审计日志是追溯责任链的唯一依据。

### 4.6 生产环境典型故障案例

| 故障案例 | 现象描述 | 根因分析 | 解决方法 |
|---------|---------|---------|---------|
| **"权限泄漏"——项目管理员不小心将外包人员提升为开发者** | 项目管理员在Portal中编辑外包人员的角色时，本想从"受限访客"改为"访客"（仍只有pull），但误操作选择了"开发者"（有了push和delete权限）。7天后外包人员在不知情的情况下push了一个同名标签，覆盖了生产版本 | Portal的角色选择下拉框中"受限访客"、"访客"、"开发者"相邻排列。项目管理员在快速操作时未仔细确认选择。缺少"角色变更审批"机制 | 1. 要求所有角色提升操作（如访客→开发者）需要通过API执行并记录在变更日志中；2. 在关键项目中开启 `prevent_artifact_deletion` 防止开发者删除镜像；3. 配置Webhook在角色变更时发送通知给安全部门 |
| **"幽灵成员"——LDAP导入用户被删除后，下次同步自动恢复** | 运维在Harbor中手动删除了一个LDAP导入的用户（因该员工已离职），但次日该用户又出现在成员列表中。运维再次删除，第三天又回来了——陷入了"删除-恢复-删除-恢复"的死循环 | 手动删除只在Harbor数据库中生效，但LDAP同步任务（如果配置了定时同步）会从AD中重新获取用户列表——如果AD中该用户未被禁用，同步时会重新创建Harbor中的用户记录 | 正确流程：1. 先在AD中禁用/删除该用户；2. 等待下一次LDAP同步（或手动触发同步）；3. Harbor中该用户的状态会自动变为"已禁用"。如果紧急需要立即回收权限，在Harbor中手动禁用用户（而非删除），禁用状态不会被LDAP同步覆盖 |
| **"跨项目管理员越权"——项目管理员在多个项目中的权限叠加导致非预期操作** | Bob在 `order-platform` 是项目管理员（有管理成员的权限），在 `shared-base` 也被设为项目管理员。一天Bob在清理 `shared-base` 的成员列表时，不小心从下拉框中选错了项目——他将 `order-platform` 的3个开发者从项目中移除了（他以为是 `shared-base` 的成员）。导致 `order-platform` 的3个开发者在周末无法push修复补丁 | 多项目管理员的权限叠加没有在UI中给出足够的"上下文提示"。Bob在两个项目中都有管理员权限，操作时没有注意到当前所在的项目上下文 | 1. Portal应始终清晰显示当前操作的项目名称；2. 对批量删除成员操作添加"确认对话框"（显示受影响的项目名称和成员列表）；3. 操作前截图或使用API记录操作快照 |

### 4.7 思考题

1. **云鲸科技有100个项目、500个用户。人力资源部门每周平均有8人入职、2人离职、5人转岗。如果全靠人工管理Harbor权限，假设每次权限操作（查询→确认→执行）需要3分钟，那么运维人员每周需要花费多少小时在权限管理上？设计一个"Harbor权限自动化管理系统"——通过Harbor API + HR系统API，实现：（1）新员工入职时根据部门自动加入对应项目并分配预设角色；（2）员工离职时自动回收所有项目权限并禁用账号；（3）员工转岗时自动执行"旧项目权限回收 + 新项目权限分配"。请写出核心API调用序列和异常处理逻辑。**

2. **Harbor的RBAC模型不支持"标签级"的访问控制。但有一个业务场景：生产镜像的 `release-*` 标签只允许项目管理员和维护者推送（开发者不能覆盖），而 `dev-*` 标签任何人都可以推送。在不修改Harbor源码的前提下，能否通过Webhook或其他Harbor特性实现这个需求？如果能，请描述技术方案；如果不能，请说明原因并提出替代方案（如项目拆分策略）。**

---

> 下一章预告：第12章将深入Harbor的垃圾回收（GC）机制——从Blob存储原理到Mark & Sweep全流程，再到定时GC配置和空间优化策略。
