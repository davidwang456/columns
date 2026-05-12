# 第6章：Artifact 管理与标签策略

## 1 项目背景

**智联物流科技**在 Harbor 上线运行 18 个月后，运维总监老王在季度成本复盘会上看着 IT 基础设施的账单，眉头越皱越紧。

**痛点一：存储成本失控——SSD 上的"僵尸军团"。** 基础设施团队拉出 Harbor 的存储增长曲线：从上线初期的 120GB，到第 6 个月的 280GB，再到第 18 个月的 1.2TB。按照这个趋势，明年存储成本将突破 3.2 万元/月。但排查结果让所有人震惊——Harbor 中存储了 15 个项目的 880 个镜像仓库，总计超过 21000 个标签，其中 47% 的标签（约 9870 个）超过 6 个月未被任何人拉取过。最夸张的是 `logistics-data-service` 这个仓库，从 2024 年 3 月到 2025 年 3 月积累了 293 个标签，但实际只有最近的 3 个标签被 K8s 集群引用。那 290 个"僵尸标签"默默占用了 186GB 的 SSD 存储，其中包含多个 1.5GB+ 的完整 Java 运行时镜像层。

**痛点二：安全合规下的"幽灵镜像"。** 安全团队在 2025 年 Q2 的季度渗透测试中，通过 Trivy 扫描出 `route-optimization-engine:v2.1.0`（推送于 2024 年 11 月）包含了 3 个 Critical 级别漏洞：CVE-2024-21626（runc 容器逃逸）、CVE-2023-5363（OpenSSL 信息泄露）、以及一个供应链投毒依赖库。该镜像早在 2025 年 1 月就被标记为"已废弃"，K8s 集群中没有任何 Pod 使用它。但由于没有任何删除策略，任何知道镜像名称的开发人员仍然可以在测试环境中 `docker pull` 并运行它。安全团队要求"立即物理删除该镜像及其所有历史标签，并确保无法恢复"。但运维发现——删除标签只是解除了映射关系，Blob（镜像层数据）仍然在磁盘上，需要额外执行 Garbage Collection（GC）才能真正释放空间。

**痛点三：混合架构下的 Manifest List 分裂。** 团队引入 8 台 ARM64（华为鲲鹏 920）Node 来降低云成本后，`order-query-service:latest` 标签同时包含 AMD64 和 ARM64 两个架构的 Manifest List。问题是——CI 流水线在 x86 Runner 上构建的是 AMD64 版本，而运维为适配 ARM64 节点手动在 MacBook 上构建了 ARM64 版本，然后用 `docker manifest create` 合并。但某一次，CI 构建完成后更新了 Manifest List 中的 AMD64 引用，却没有重新构建 ARM64 版本，导致新的 Manifest List 中 ARM64 仍然指向一个 4 周前的旧版本。一线开发人员没人注意到——直到灰度发布时，ARM64 Pod 使用 4 周前的镜像版本出现生产故障。

**痛点四：CI/CD 垃圾标签海啸。** 智联物流的 GitLab CI 配置为每次 Git Push（任何分支）触发构建并推送 `build-<git-short-sha>-<timestamp>` 标签到 Harbor。30 人的开发团队，平均每人每天 push 8-12 次，一个月产生的标签数高达 7000 个。运维做了一个统计：7000 个 CI 标签中，93.7% 从未被任何环境部署过。这些标签不仅占用了 Harbor 数据库的 `artifact_tag` 表空间（标签查询性能从 50ms 退化到 3.2 秒），还导致 Harbor Portal 的仓库详情页面加载时间超过 15 秒。更糟的是，这些垃圾标签中混杂着安全漏洞镜像——漏洞扫描工具需要扫描所有标签，徒增安全扫描负担。

**痛点五——新增：标签覆盖引发的回滚事故。** 2025 年 3 月 12 日晚上 10:23，运维小刘在修复一个紧急 Bug 后，按常规流程将 `hotfix` 分支的构建产物标记为 `order-service:latest` 并推送。他以为 `latest` 只是临时验证用。结果第二天早上 8:15，所有 K8s 集群（生产环境）触发了一次自动滚动更新——因为集群中配置的是 `order-service:latest` 并且 `imagePullPolicy: Always`。一个未经过完整测试的 Hotfix 镜像就这样被部署到了 200+ 个 Pod 上，导致了著名的"3.13 物流大崩溃"事件——用户下单接口在高峰期 500 错误率飙升至 23%，持续 37 分钟，直接经济损失预估 82 万元。事后复盘：**如果 `latest` 标签设置了不可变性规则，这次凌晨的误推送会在第一步就被拒绝，根本不会进入生产环境。**

本章将从 Harbor v2.0+ 的核心抽象——**Artifact（制品）**出发，系统讲解标签不可变性规则、保留策略的精细配置、垃圾回收机制，以及多架构制品的管理方案。

---

## 2 项目设计——剧本式交锋对话

**场景：季度基础设施成本 Review 会，老王投影出 Harbor 存储账单，屏幕上的数字让会议室里一片寂静。**

**小胖**（看着投屏上的 1.2TB 数字，手里的奶茶差点掉地上）："1.2 个 T？！我们公司的 Docker 镜像到底是在存代码还是在存 4K 蓝光电影啊？这不就跟我们大学宿舍一样嘛——6 个人每人买了两箱方便面，但真正吃掉的是不是只有那几包老坛酸菜？"

**大师**："小胖你这个比方打得好。来，我们现场看看到底'吃'了哪些泡面、'囤'了哪些过期货。"

"在 Harbor 数据模型里，所有东西——不管是 Docker 镜像、Helm Chart、CNAB 包，还是 SBOM 文档——都叫 **Artifact（制品）**。每个 Artifact 是通过其唯一的内容摘要（digest, SHA256）来标识的，而标签（Tag）只是一个指向 digest 的可变指针。这就像图书馆——书的 ISBN（digest）是唯一的，但同一本书可以在不同的书架上被贴不同的分类标签（Tag）。"

**技术映射**：Harbor 的数据库中有两张关键表：`artifact` 表存储制品的核心信息（digest, media_type, size, push_time 等），`artifact_tag` 表存储标签与制品的多对多关系（tag, artifact_id）。一个 digest 可以有多个标签指向它。删除一个标签只是删除 `artifact_tag` 表中的一行，digest 引用的 Blob 数据在 GC 之前不会被物理删除。

**小胖**："等等——你是说删标签不等于删镜像？那跟我们之前理解的完全不一样啊！我们一直以为在 Portal 里点'删除标签'就算是清理完成了，敢情这只是在数据库里改了个状态？"

**大师**："没错。Harbor 的删除是**两阶段软删除**设计，这个设计其实很巧妙——就像文件系统先移入回收站再清空回收站——防止误删后无法恢复。但这个设计也让很多团队以为'删了标签空间就释放了'，结果半年后发现磁盘满了。"

`完整删除一个制品的三部曲：`

```
第一步：删除标签（Portal / API）
         │
         ▼
   artifact_tag 表中该行标记 deleted=true
         但 artifact 行和 Blob 数据仍然存在
         │
第二步：Artifact 变为无标签状态 → 手动删除 Artifact
         │
         ▼
   artifact 表标记 deleted=true
         但 Blob 数据仍然在文件系统中
         │
第三步：执行 Garbage Collection (GC)
         │
         ▼
   扫描所有 Blob，找到无 artifact 引用的 Blob → 物理删除文件
         磁盘空间真正释放
```

**小白**（若有所思地在白板上画了三个圆圈）："那如果第二步和第三步之间，有另一个项目或仓库的镜像引用了同一个 Blob 层呢？GC 会删掉共享的层吗？"

**大师**："小白这个问题问到了 GC 的核心。GC 采用**引用计数模型**——它不是简单地'删除所有标记 deleted 的制品对应的 Blob'，而是扫描全量 artifact 表，找出所有活跃的（deleted=false）的 artifact 引用了哪些 Blob，然后**只删除没有任何活跃引用的 Blob**。"

"这就像一座公寓楼里的共享热水器——即使 301 号房间退租了，302 号和 303 号还在用同一台热水器，物业不会因为它挂在 301 名下就把它拆了。"

**技术映射**：Harbor 的 GC 实现基于 Docker Registry 的 `mark` 和 `sweep` 算法：① Mark 阶段——扫描所有 non-deleted artifact，标记所有引用的 Blob；② Sweep 阶段——删除所有未被标记的 Blob 文件。如果 2 个项目共享同一个基础镜像层（如 `alpine:3.19`），只要至少一个项目还引用这个 Blob，GC 就不会删除它。

**小胖**："好，那我懂了删除机制。那不可变性规则又是啥？就是让标签不能被覆盖？这玩意儿真能防止老王的'3.13 大崩溃'吗？"

**大师**："不可变性规则就是给特定标签上锁——一旦某个标签匹配了不可变性规则，任何人（包括 Harbor 管理员！）都不能推送同名标签覆盖它。这就像你给重要文件加了只读属性——不是说你没权限删，而是系统层面就不允许写操作。"

"不可变性规则的配置非常灵活——你可以指定它所作用的仓库（repository pattern）和标签（tag pattern），组合出精确的防护策略："

```
不可变性规则示例：

┌──────────────────────────────────────────────────┐
│ 规则名称: 保护生产 release 标签                      │
│                                                    │
│ Repository 范围:  **  (所有仓库)                     │
│ Tag 匹配模式:    release-*                          │
│                                                    │
│ 效果: 任何人试图 docker push <任意repo>:release-*   │
│        都会被拒绝                                    │
│                                                    │
│ 拒绝日志示例:                                       │
│ denied: The tag "release-2.5" is immutable          │
│         and cannot be overwritten.                  │
└──────────────────────────────────────────────────┘
```

**小胖**："那 reserved 策略呢？我刚在文档里看到还有个'保留策略'——这跟不可变性有什么区别？"

**大师**："这两个经常被搞混，但作用完全不同。**不可变性**是'阻止你覆盖'（防御性质），而**保留策略**是'自动帮你清理旧标签'（清理性质）。"

"打个比方——不可变性是保险柜的锁（防破坏），保留策略是定期请保洁阿姨把过期的泡面扔掉（清垃圾）。"

`不可变性 vs 保留策略核心差异：`

| 维度 | 标签不可变性 | 标签保留策略 |
|------|------------|------------|
| 作用方向 | 防御（阻止 Push 覆盖） | 清理（自动删除旧标签） |
| 触发方式 | 实时（每次 Push 都检查） | 定时/手动（Cron 或 API 触发） |
| 最佳搭档 | `release-*`, `latest` 等生产标签 | `build-*`, `dev-*`, `feature-*` 等 CI 标签 |
| 释放空间 | 否（只是阻止写入） | 部分（删除标签映射，需配合 GC 释放空间） |
| 管理员能否绕过 | 否（必须先删除不可变性规则本身） | 是（可以手动执行或修改规则） |
| 风险 | 配太宽可能阻止正常 CI 推送 | 配太激进可能误删仍需要的旧版本 |

**小白**："保留策略里有个参数我一直没搞懂——`latestPushedN` 和 `latestActiveN` 到底什么区别？文档写得太抽象了。"

**大师**："这是最容易被误用的两个参数。来看实际场景："

**`latestPushedN=10`** —— 保留最近推送的 10 个制品（按 push_time 排序）：
- 适用场景：CI/CD 产生的快速迭代标签（`build-*`），只看'最近构建是什么'
- 风险：如果某个 3 周前的旧镜像被生产环境使用，但不在最近 10 个之内，会被清理

**`latestActiveN=10`** —— 保留最近被拉取的 10 个制品（按 pull_time 排序）：
- 适用场景：关注'哪些镜像实际被使用'的场景
- 依赖：必须开启 Harbor 的审计日志（audit log），否则 pull_time 数据缺失

"关键结论：**latestActiveN 更适合生产环境——即使镜像推送了很久，只要还在被拉取，就不会被清理。** 但前提是审计日志必须正常写入。"

**技术映射**：`latestActiveN` 依赖 Harbor 的审计日志系统。Harbor 的 Core 组件在处理每个 `/v2/<name>/manifests/<reference>` 的 GET 请求时（即 `docker pull` 或 `helm pull` 时），会生成一条类型为 `pull_artifact` 的审计日志记录，写入 `audit_log` 表。保留策略执行时查询 `audit_log` 中每个 artifact 最近一次被 pull 的时间。如果审计日志被定期清理或关闭，`latestActiveN` 退化为 `latestPushedN`。

**小胖**："那保留策略的执行是定时的？万一我在定时任务执行之前手动推了个新版，但旧版刚好在任务执行中被删，会发生什么？"

**大师**："保留策略的执行是**原子性**的——每个仓库独立执行，执行期间该仓库的标签删除操作是事务性的（同一个数据库事务），不会出现'一半被删一半保留'的中间状态。如果执行失败了，会被记录到 JobService 的任务日志中，你可以重试。"

"但要注意——保留策略执行时会产生写锁，如果此时有人正在 push 同仓库的新镜像，两者不会冲突——因为保留策略操作的是 `artifact_tag` 表，而 push 操作主要操作 `blob` 和 `artifact` 表，锁冲突概率很低。"

**小白**："还有一个我之前踩过的坑——我们为多架构镜像创建了 Manifest List，但后来运维删除了其中一个架构的子 Manifest（误以为它是'多余的'），导致 Manifest List 损坏。Harbor 在这一块有什么保护机制吗？"

**大师**："Harbor 在 Portal 中针对多架构制品（type=IMAGE 且有 references）做了特殊处理——当你删除一个 Manifest List 的标签时，Portal 会提示'该制品包含多个架构的子制品，删除此标签将同时删除所有子架构引用'。但在 API 层面，删除仍然是级联的——删除父 Manifest List 标签会同时取消所有子 Manifest 的关联。"

"**关键保护机制是标签不可变性规则**——如果 Manifest List 的标签匹配了不可变性规则，删除操作在第一步就被拒绝了。所以我建议：所有 Manifest List 的标签（尤其是 `latest` 和 `release-*`）都配上不可变性规则。"

---

## 3 项目实战

### 3.1 环境准备

| 组件 | 版本 | 说明 |
|------|------|------|
| Harbor | v2.12 | 已部署运行（HTTPS 已配置） |
| 项目 | order-platform | 已创建，作为测试项目 |
| 测试镜像 | hello-app（Docker Image） | 至少准备 20 个不同版本标签 |
| 命令行工具 | jq, curl, docker | 用于 API 交互和镜像构建 |
| 权限要求 | Harbor 系统管理员账户 | Admin 权限用于创建不可变性规则和 GC |

### 3.2 准备测试数据——模拟真实仓库的标签堆积

> **步骤目标**：批量生成 20+ 个带不同标签的测试镜像，模拟长期运行后 Harbor 仓库中积累的大量标签，用于后续验证不可变性规则和保留策略。

```bash
# ================================================================
# 循环构建并推送 20 个模拟历史 CI 构建版本
# ================================================================
for i in $(seq 1 20); do
  # 创建带版本信息的小工具脚本
  cat <<EOF > app.sh
#!/bin/sh
echo "=== Hello App v1.${i}.0 ==="
echo "Build Time: $(date -Iseconds)"
echo "Build Number: ${i}/20"
echo "Git SHA: $(echo $RANDOM | sha256sum | head -c 8)"
EOF
  chmod +x app.sh

  cat <<DOCKERFILE > Dockerfile
FROM alpine:3.19.1
LABEL com.zl-logistics.version="1.${i}.0"
LABEL com.zl-logistics.build-number="${i}"
COPY app.sh /app.sh
RUN chmod +x /app.sh
CMD ["/app.sh"]
DOCKERFILE

  docker build --no-cache \
    -t harbor.zl-logistics.com/order-platform/hello-app:v1.${i}.0 .
  docker push harbor.zl-logistics.com/order-platform/hello-app:v1.${i}.0
  echo "[${i}/20] Pushed v1.${i}.0"
done

# 推送几个 release 标签（模拟生产发布版本）
docker tag harbor.zl-logistics.com/order-platform/hello-app:v1.10.0 \
           harbor.zl-logistics.com/order-platform/hello-app:release-1.10
docker push harbor.zl-logistics.com/order-platform/hello-app:release-1.10

docker tag harbor.zl-logistics.com/order-platform/hello-app:v1.15.0 \
           harbor.zl-logistics.com/order-platform/hello-app:release-1.15
docker push harbor.zl-logistics.com/order-platform/hello-app:release-1.15

docker tag harbor.zl-logistics.com/order-platform/hello-app:v1.20.0 \
           harbor.zl-logistics.com/order-platform/hello-app:release-2.0
docker push harbor.zl-logistics.com/order-platform/hello-app:release-2.0

# 推送 latest 标签
docker tag harbor.zl-logistics.com/order-platform/hello-app:v1.20.0 \
           harbor.zl-logistics.com/order-platform/hello-app:latest
docker push harbor.zl-logistics.com/order-platform/hello-app:latest

# ================================================================
# 验证测试数据——查看当前仓库中的标签总数
# ================================================================
curl -s -u admin:Harbor12345 \
  "https://harbor.zl-logistics.com/api/v2.0/projects/order-platform/repositories/hello-app/artifacts?with_tag=true&page_size=50" | \
  jq '[.[].tags[].name] | length'

# 预期输出：24  (20 个 v1.x.0 + 3 个 release-* + 1 个 latest)

# 查看所有标签列表
curl -s -u admin:Harbor12345 \
  "https://harbor.zl-logistics.com/api/v2.0/projects/order-platform/repositories/hello-app/artifacts?with_tag=true" | \
  jq -r '.[].tags[].name' | sort -V
# 预期输出（按版本排序）：
# latest
# release-1.10
# release-1.15
# release-2.0
# v1.1.0
# v1.2.0
# ...
# v1.20.0
```

### 3.3 配置标签不可变性规则

**步骤一：通过 Portal 创建不可变性规则**

> **步骤目标**：在 order-platform 项目中创建不可变性规则，保护 `latest` 和 `release-*` 标签不被覆盖。

```
Portal 操作路径：
项目 → order-platform → Policy（策略）→ Tag Immutability（标签不可变性）→ Add Rule

规则 1（保护 latest 标签）：
  ┌──────────────────────────────────────┐
  │ Repository Filter:                   │
  │   ○ Repository: ** (匹配所有仓库)      │
  │   ○ Tag: latest                      │
  │   ☑ Enabled                          │
  │                                      │
  │ Description: 防止任何人覆盖 latest     │
  │              标签（3.13 事故的根本解）  │
  └──────────────────────────────────────┘

规则 2（保护 release 系列标签）：
  ┌──────────────────────────────────────┐
  │ Repository Filter:                   │
  │   ○ Repository: **                   │
  │   ○ Tag: release-*                   │
  │   ☑ Enabled                          │
  └──────────────────────────────────────┘
```

**步骤二：通过 Harbor API 创建不可变性规则**

> **步骤目标**：使用 API 批量创建不可变性规则，便于版本化管理（IaC）和批量配置。

```bash
# ================================================================
# 获取项目 ID
# ================================================================
PROJECT_ID=$(curl -s -u admin:Harbor12345 \
  "https://harbor.zl-logistics.com/api/v2.0/projects" | \
  jq -r '.[] | select(.name=="order-platform") | .project_id')
echo "Project ID: $PROJECT_ID"
# 预期输出：Project ID: 1

# ================================================================
# 创建规则 1：保护 latest 标签（所有仓库）
# ================================================================
curl -X POST \
  -u admin:Harbor12345 \
  -H "Content-Type: application/json" \
  -d '{
    "scope_selectors": {
      "repository": [{"kind": "doublestar", "pattern": "**", "decoration": "repoMatches"}]
    },
    "tag_selectors": [
      {"kind": "doublestar", "pattern": "latest", "decoration": "matches"}
    ],
    "enabled": true,
    "description": "保护 latest 标签不被覆盖 - 自 3.13 事故后的必修配置"
  }' \
  "https://harbor.zl-logistics.com/api/v2.0/projects/$PROJECT_ID/immutabletagrules"

# 预期输出：HTTP 201 Created

# ================================================================
# 创建规则 2：保护 release-* 标签（所有仓库）
# ================================================================
curl -X POST \
  -u admin:Harbor12345 \
  -H "Content-Type: application/json" \
  -d '{
    "scope_selectors": {
      "repository": [{"kind": "doublestar", "pattern": "**", "decoration": "repoMatches"}]
    },
    "tag_selectors": [
      {"kind": "doublestar", "pattern": "release-*", "decoration": "matches"}
    ],
    "enabled": true,
    "description": "保护所有 release 标签不被覆盖确保生产可回滚"
  }' \
  "https://harbor.zl-logistics.com/api/v2.0/projects/$PROJECT_ID/immutabletagrules"

# ================================================================
# 查看已创建的不可变性规则
# ================================================================
curl -s -u admin:Harbor12345 \
  "https://harbor.zl-logistics.com/api/v2.0/projects/$PROJECT_ID/immutabletagrules" | \
  jq '.[] | {id, tag_selectors, enabled, description}'
# 预期输出（摘要）：
# {
#   "id": 1,
#   "tag_selectors": [{"kind": "doublestar", "pattern": "latest"}],
#   "enabled": true,
#   "description": "保护 latest 标签不被覆盖..."
# }
# {
#   "id": 2,
#   "tag_selectors": [{"kind": "doublestar", "pattern": "release-*"}],
#   "enabled": true,
#   "description": "保护所有 release 标签不被覆盖..."
# }
```

**步骤三：验证不可变性规则生效——尝试覆盖受保护的标签**

> **步骤目标**：实际测试不可变性规则的拦截能力，确认标签确实无法被覆盖。

```bash
# ================================================================
# 场景一：尝试覆盖 latest 标签（预期失败）
# ================================================================
docker tag harbor.zl-logistics.com/order-platform/hello-app:v1.5.0 \
           harbor.zl-logistics.com/order-platform/hello-app:latest
docker push harbor.zl-logistics.com/order-platform/hello-app:latest

# 预期输出（推送被拒绝）：
# The push refers to repository [harbor.zl-logistics.com/order-platform/hello-app]
# ...
# denied: The tag "latest" is immutable and cannot be overwritten.

# ================================================================
# 场景二：尝试覆盖 release-1.10 标签（预期失败）
# ================================================================
docker tag harbor.zl-logistics.com/order-platform/hello-app:v1.18.0 \
           harbor.zl-logistics.com/order-platform/hello-app:release-1.10
docker push harbor.zl-logistics.com/order-platform/hello-app:release-1.10

# 预期输出（推送被拒绝）：
# denied: The tag "release-1.10" is immutable and cannot be overwritten.

# ================================================================
# 场景三：推送一个新的 v1.x.0 标签（预期成功——不在不可变性规则范围内）
# ================================================================
docker tag harbor.zl-logistics.com/order-platform/hello-app:v1.18.0 \
           harbor.zl-logistics.com/order-platform/hello-app:v1.21.0
docker push harbor.zl-logistics.com/order-platform/hello-app:v1.21.0

# 预期输出（推送成功）：
# v1.21.0: digest: sha256:... size: 942
```

### 3.4 配置标签保留策略——自动化清理 CI 历史标签

**步骤一：设计保留策略**

> **步骤目标**：为 CI 产生的 `v*` 历史构建标签创建自动保留策略——保留最近 10 个版本，但排除 `release-*` 和 `latest` 标签。

```
策略设计说明：
┌─────────────────────────────────────────────────────────┐
│ 保留策略：自动清理 CI 构建历史                               │
│                                                         │
│ 保留条件：latestPushedN = 10（保留最近推送的 10 个制品）      │
│ 仓库范围：** (order-platform 项目中的所有仓库)               │
│ 标签排除：latest, release-*（这些标签永远不删）               │
│ 执行计划：每天凌晨 03:00（低峰期）                           │
│                                                         │
│ 预期结果（当前 24 个标签）：                                │
│   保留：10 个最近推送版本 + 3 个 release + 1 个 latest    │
│   清理：11 个旧 v1.x.0 标签（v1.1.0 ~ v1.10.0）           │
│   总计剩余：约 14 个标签                                   │
└─────────────────────────────────────────────────────────┘
```

**步骤二：通过 API 创建保留策略**

> **步骤目标**：使用 Harbor 的 Retention API 以声明式方式创建保留策略。

```bash
# ================================================================
# 获取当前已存在的保留策略（避免重复创建）
# ================================================================
curl -s -u admin:Harbor12345 \
  "https://harbor.zl-logistics.com/api/v2.0/retentions" | \
  jq '.[] | {id, scope: .scope, rules: .rules}'

# ================================================================
# 创建新的保留策略
# ================================================================
curl -X POST \
  -u admin:Harbor12345 \
  -H "Content-Type: application/json" \
  -d '{
    "algorithm": "or",
    "rules": [
      {
        "disabled": false,
        "action": "retain",
        "scope_selectors": {
          "repository": [
            {
              "kind": "doublestar",
              "pattern": "**",
              "decoration": "repoMatches"
            }
          ]
        },
        "tag_selectors": [
          {
            "kind": "doublestar",
            "pattern": "**",
            "decoration": "matches"
          }
        ],
        "params": {
          "latestPushedN": 10
        },
        "template": "latestPushedN",
        "tag_exclude_selectors": [
          {
            "kind": "doublestar",
            "pattern": "latest",
            "decoration": "excludes"
          },
          {
            "kind": "doublestar",
            "pattern": "release-*",
            "decoration": "excludes"
          }
        ]
      }
    ],
    "trigger": {
      "kind": "Schedule",
      "settings": {
        "cron": "0 3 * * *"
      }
    },
    "scope": {
      "level": "project",
      "ref": '$PROJECT_ID'
    }
  }' \
  "https://harbor.zl-logistics.com/api/v2.0/retentions"

# 预期输出：HTTP 201 Created
# 返回体包含新创建的 retention ID
```

**步骤三：手动触发保留策略并查看执行状态**

> **步骤目标**：不等待凌晨定时任务，手动触发保留策略运行，实时查看执行结果。

```bash
# ================================================================
# 获取 Retention ID
# ================================================================
RETENTION_ID=$(curl -s -u admin:Harbor12345 \
  "https://harbor.zl-logistics.com/api/v2.0/retentions" | \
  jq -r ".[] | select(.scope.ref==$PROJECT_ID) | .id")
echo "Retention ID: $RETENTION_ID"

# ================================================================
# 手动触发一次 Retention 执行（立即执行，不等定时）
# ================================================================
curl -X POST \
  -u admin:Harbor12345 \
  -H "Content-Type: application/json" \
  -d '{"dry_run": false}' \
  "https://harbor.zl-logistics.com/api/v2.0/retentions/$RETENTION_ID/executions"

# 预期输出（包含 execution 信息）：
# {"id": 1, "status": "InProgress", ...}

# ================================================================
# 查询最近一次 Retention 执行的详细结果
# ================================================================
curl -s -u admin:Harbor12345 \
  "https://harbor.zl-logistics.com/api/v2.0/retentions/$RETENTION_ID/executions" | \
  jq '.[0] | {id, status, trigger, start_time, end_time, total, retained, deleted}'
# 预期输出（示例）：
# {
#   "id": 1,
#   "status": "Success",
#   "trigger": "manual",
#   "start_time": "2025-03-20T08:15:00Z",
#   "end_time": "2025-03-20T08:15:05Z",
#   "total": 24,
#   "retained": 14,
#   "deleted": 11
# }
```

**步骤四：验证保留策略的执行结果**

> **步骤目标**：对比保留策略执行前后的标签列表，确认预期的清理效果。

```bash
# ================================================================
# 查看清理后的标签列表
# ================================================================
curl -s -u admin:Harbor12345 \
  "https://harbor.zl-logistics.com/api/v2.0/projects/order-platform/repositories/hello-app/artifacts?with_tag=true" | \
  jq -r '.[].tags[].name' | sort -V

# 预期输出（清理后应剩余约 14 个标签）：
# latest                    ← 被 exclude 规则保护
# release-1.10              ← 被 exclude 规则保护
# release-1.15              ← 被 exclude 规则保护
# release-2.0               ← 被 exclude 规则保护
# v1.11.0                   ← 保留（最近推送的 10 个之一）
# v1.12.0
# v1.13.0
# v1.14.0
# v1.15.0
# v1.16.0
# v1.17.0
# v1.18.0
# v1.19.0
# v1.20.0                   ← 保留（最近推送的）
# v1.21.0                   ← 保留（最近推送的）
#
# (v1.1.0 ~ v1.10.0 共 10 个已被清理)

# ================================================================
# 验证被删除的标签确实无法拉取
# ================================================================
docker pull harbor.zl-logistics.com/order-platform/hello-app:v1.1.0
# 预期输出：
# Error response from daemon: manifest for
#   harbor.zl-logistics.com/order-platform/hello-app:v1.1.0 not found: manifest unknown

# ================================================================
# 但验证 release 版本仍可正常拉取
# ================================================================
docker pull harbor.zl-logistics.com/order-platform/hello-app:release-1.10
# 预期输出：正常拉取成功
```

### 3.5 完整废弃 Artifact 删除流程（含 GC）

**步骤一：删除标签 → 删除 Artifact → 执行 GC**

> **步骤目标**：按照标准的三步流程，彻底从 Harbor 中物理删除一个废弃镜像及其占用的磁盘空间。

```bash
# ================================================================
# Demo：彻底删废弃镜像 v1.21.0
# ================================================================

# 第 1 步：获取制品信息
REPO_NAME="hello-app"
ARTIFACT_REF="v1.21.0"
DIGEST=$(curl -s -u admin:Harbor12345 \
  "https://harbor.zl-logistics.com/api/v2.0/projects/order-platform/repositories/$REPO_NAME/artifacts?with_tag=true" | \
  jq -r ".[] | select(.tags != null) | select(.tags[].name==\"$ARTIFACT_REF\") | .digest")
echo "Delete target digest: $DIGEST"

# 第 2 步：删除标签映射
curl -X DELETE -u admin:Harbor12345 \
  "https://harbor.zl-logistics.com/api/v2.0/projects/order-platform/repositories/$REPO_NAME/artifacts/$ARTIFACT_REF/tags/$ARTIFACT_REF"
# 预期输出：（空响应体，HTTP 200 OK）

# 第 3 步：删除无标签的 Artifact
curl -X DELETE -u admin:Harbor12345 \
  "https://harbor.zl-logistics.com/api/v2.0/projects/order-platform/repositories/$REPO_NAME/artifacts/$DIGEST"
# 预期输出：HTTP 200 OK

# 第 4 步：查看 GC 历史，了解上次 GC 释放了多少空间
curl -s -u admin:Harbor12345 \
  "https://harbor.zl-logistics.com/api/v2.0/system/gc" | \
  jq '.[0] | {schedule_status: .schedule.status, last_job_status: .job_status, 
              freed_mb: (.gc_record.freed_size // 0 / 1048576 | floor)}'
# 预期输出：
# {
#   "schedule_status": "Manual",
#   "last_job_status": "finished",
#   "freed_mb": 2356
# }

# 第 5 步（可选）：立即触发 GC 手动执行
curl -X POST -u admin:Harbor12345 \
  -H "Content-Type: application/json" \
  -d '{"schedule":{"type":"Manual"}}' \
  "https://harbor.zl-logistics.com/api/v2.0/system/gc/schedule"

# 第 6 步：监控 GC 任务执行状态
curl -s -u admin:Harbor12345 \
  "https://harbor.zl-logistics.com/api/v2.0/system/gc" | \
  jq '.[0] | {status: .job_status, creation_time, update_time}'
# 预期输出（GC 执行中）：
# {"status": "running", "creation_time": "2025-03-20T09:00:00Z", ...}
# 预期输出（GC 执行完毕）：
# {"status": "finished", "creation_time": "2025-03-20T09:00:00Z", ...}
```

### 3.6 多架构 Artifact（Manifest List）的保护与删除

> **步骤目标**：展示如何处理多架构制品的删除——防止误删子 Manifest 导致 Manifest List 损坏。

```bash
# ================================================================
# 场景：查看 Manifest List 与其子 Artifact 的关联
# ================================================================
curl -s -u admin:Harbor12345 \
  "https://harbor.zl-logistics.com/api/v2.0/projects/order-platform/repositories/multiarch-demo/artifacts?with_tag=true&with_references=true" | \
  jq '.[] | {
    digest: (.digest[:20]),
    tags: [.tags[].name],
    type: .type,
    references: [(.references // []) | .[] | {
      child_digest: (.child_digest[:20]),
      platform: "\(.platform.os)/\(.platform.architecture)"
    }]
  }'

# 预期输出：
# {
#   "digest": "sha256:abc123456789...",
#   "tags": ["v1.0.0", "latest"],
#   "type": "IMAGE",
#   "references": [
#     { "child_digest": "sha256:e3b0c44...", "platform": "linux/amd64" },
#     { "child_digest": "sha256:d3b0738...", "platform": "linux/arm64" }
#   ]
# }

# 注意：portal 删除 Manifest List 标签时，Harbor 会自动级联删除所有子 Artifact 的关联
# 但如果子 Artifact 也被其他标签引用，则子 Artifact 不会被删除
```

### 3.7 可能遇到的坑

**坑1：保留策略执行后 `du -sh /data` 显示空间无变化**

| 维度 | 详情 |
|------|------|
| **现象** | Retention 执行日志显示 `deleted: 200`，成功清理了 200 个标签，但一周后执行 `du -sh /data/registry` 发现存储占用与之前完全一样 |
| **根因** | 保留策略操作的是 `artifact_tag` 表（标签与制品的映射关系），不接触 Blob 文件系统。标签删除只是 SQL 层面的 UPDATE/DELETE，文件系统上的 Blob 文件直到 GC 执行且确认无引用后才会被物理删除。此外，如果其他项目/仓库的镜像引用了相同的 Layer（如 Alpine 基础层），该 Blob 在 GC 时被标记为'仍有引用'而不删除 |
| **解决方法** | 保留策略执行后，必须在 Harbor Portal 中手动触发 GC 或设置定期 GC。推荐配置：保留策略每天凌晨 3:00 执行，GC 每周日凌晨 5:00 执行（错开以确保标签删除已生效）。`curl -X POST -u admin:Harbor12345 "https://harbor.zl-logistics.com/api/v2.0/system/gc/schedule" -d '{"schedule":{"type":"Weekly","cron":"0 5 * * 0"}}'` |

**坑2：不可变性规则过宽导致 CI 全阻塞**

| 维度 | 详情 |
|------|------|
| **现象** | 2025 年 4 月 1 日（周一）早上 10:00，所有团队的 CI Pipeline 突然全部失败，Jenkins 日志显示 `denied: The tag "build-*" is immutable and cannot be overwritten`。但 CI 配置中每次构建都会生成唯一的 `build-<sha>` 标签，不应该有覆盖行为 |
| **根因** | 有管理员在周末配置了一条 `Tag: **`（匹配所有标签）的不可变性规则，原本想保护所有标签，但不小心打开了'所有仓库、所有标签'的匹配范围。但更糟的是——因为不可变性规则本身也需要'管理员权限删除'，所以运维紧急响应时发现必须先删除这条规则本身才能恢复 CI |
| **解决方法** | ① 不可变性规则的 Tag 模式应该精准匹配生产标签：`release-*`、`latest`、`v*`（如果版本标签不需要覆盖），绝不能用 `**`；② CI 产生的标签应使用不同前缀（如 `build-*`、`ci-*`、`snapshot-*`），与生产标签命名空间分离；③ 将不可变性规则的创建纳入 IaC（Terraform/Harbor Provider）并走 PR Review 流程，避免周末手动变更 |

**坑3：GC 执行后磁盘空间几乎没减少**

| 维度 | 详情 |
|------|------|
| **现象** | 运维确信已通过 Retention 清理了 1500+ 个标签，GC 执行成功（`job_status: finished`），但 `freed_size` 仅为 120MB，与预期的释放量（约 8GB）相差两个数量级 |
| **根因** | 三种可能性：① 被清理的标签引用的 Blob 被同一 Harbor 中其他项目的镜像大量共享（最常见）；② 删除的是 Manifest List 的标签，但子 Manifest 未被 Retention 清理——子 Manifest 仍然引用着 Blob 层；③ Harbor 数据库中没有标记 `deleted=true` 的记录（Retention 只删除了 `artifact_tag` 表行，但 `artifact` 表行未被标记删除），GC 认为该 Artifact 仍活跃 |
| **解决方法** | ① 排查步骤：`curl -s -u admin:Harbor12345 "https://harbor.zl-logistics.com/api/v2.0/projects/order-platform/repositories/hello-app/artifacts?with_tag=false"` 查看无标签 Artifact；② 手动删除无标签 Artifact（或创建第二条 Retention 规则专门处理无标签制品）；③ 检查跨项目共享 layer：`SELECT COUNT(DISTINCT project_id) FROM artifact WHERE digest IN (SELECT digest_blob FROM artifact_blob WHERE blob_id=...)` |

**坑4：保留策略 `latestActiveN` 未生效但无报错**

| 维度 | 详情 |
|------|------|
| **现象** | 配置了 `latestActiveN=5` 的保留策略，期望保留最近被拉取的 5 个制品。但策略执行后，删除的是按 push 时间排序的最旧 15 个制品，而不是按 pull 时间排序 |
| **根因** | `latestActiveN` 依赖 Harbor 审计日志中的 `pull_artifact` 事件记录。如果审计日志（`audit_log` 表）被以下任一情况影响，它就无法准确工作：① 审计日志被定期清理（默认保留 90 天，但可配置，如果设置为 0 则审计日志完全不写入）；② Harbor Core 组件的日志级别被设置为 `ERROR` 或更高（跳过了审计日志写入）；③ 某些版本的 Harbor 在代理缓存场景下不记录 pull 审计事件 |
| **解决方法** | ① 验证审计日志功能是否正常：`curl -s -u admin:Harbor12345 "https://harbor.zl-logistics.com/api/v2.0/audit-logs?page_size=5" \| jq '.[] \| {op_time, operation, resource}'`；② 确认 `harbor.yml` 中 `audit_log.rotation_period` 不为空且合理；③ 如果审计日志确实不可用，改用 `latestPushedN` 或手动导出生产环境的实际引用标签列表作为白名单 |

---

## 4 项目总结

### 4.1 Artifact 核心概念关系对照

| 概念 | 数据库表 | 唯一标识 | 生命周期 | 删除方式 | 示例 | 典型大小 |
|------|---------|---------|---------|---------|------|---------|
| **Project** | `project` | `project_id` (PK) | 创建 → 操作 → 删除项目 | Portal / API 删除项目 | `order-platform` | 逻辑概念 |
| **Repository** | `repository` | `repository_id` (PK) | 随项目创建/删除 | 删除项目时级联 | `order-platform/hello-app` | 逻辑概念 |
| **Artifact** | `artifact` | `digest` (SHA256) | Push 创建 → 软删除 → GC 清除 | 先删标签→删 Artifact→GC | `sha256:abc123...` | 元数据 ~5KB |
| **Tag** | `artifact_tag` | `(artifact_id, tag)` 联合 | Push 创建 → 已删除 | Portal / API / Retention | `v1.0.0`, `latest` | 字符串 |
| **Blob** | `blob` | `digest` (SHA256) | Push 创建 → GC 物理删除 | 仅 GC | `sha256:def456...` | 通常 1MB~2GB |
| **Manifest** | `artifact.manifest_media_type` | 随 Artifact 存储 | 同 Artifact | 同 Artifact 删除流程 | JSON（config+l layers 引用） | 通常 ~5-50KB |

### 4.2 标签管理三大策略对比

| 策略类型 | 功能定位 | 触发机制 | 是否释放磁盘空间 | 管理员能否绕过 | 推荐应用对象 | 风险等级 |
|---------|---------|---------|---------------|-------------|------------|---------|
| **标签不可变性** | 防御——阻止覆盖生产标签 | 实时（每个 Push 都检查） | 否 | 否（必须先删除规则本身） | `latest`, `release-*`, `v*` | 低（配置精准则无副作用） |
| **标签保留策略** | 清理——自动删除旧标签 | 定时（Cron）/ 手动（API） | 部分（仅删标签映射） | 是（可随时修改/禁用） | `build-*`, `dev-*`, `feature-*` | 中（误配可能删除仍需要的标签） |
| **垃圾回收 (GC)** | 物理清理——释放磁盘空间 | 定时 / 手动触发 | **是**（唯一能释放空间的机制） | 是 | 所有无引用的 Blob | 中（执行时 Harbor 进入只读模式） |
| **手动删除 Artifact** | 精确清理——管理员指定删除 | API / Portal 手动 | 部分（需 GC 配合） | 是（项目管理员即可） | 废弃镜像、CVE 需物理清除的场景 | 低（精确操作） |

### 4.3 标签策略最佳实践矩阵

| 项目类型 | 不可变性规则 | 保留策略 (latestPushedN) | 保留策略排除标签 | GC 频率 | 审计日志要求 |
|---------|------------|----------------------|----------------|--------|------------|
| **核心业务生产项目** | `latest` + `release-*` + `v*` | N=20 | `latest`, `release-*` | 每周 GC | 开启，保留 90 天 |
| **CI/CD 快照项目** | 无（或仅 `latest`） | N=10 | `latest` | 每天 GC | 非必须 |
| **基础镜像/共享层项目** | `latest` + `<distro>-*` | N=5（长久稳定） | `latest` | 每月 GC | 建议开启 |
| **第三方代理缓存项目** | 无 | N=30（缓存多版本） | 无 | 每两周 GC | 非必须 |
| **安全合规项目（金融/医疗）** | 全部标签（`**`） | 从不自动删除 | 手动审核后删除 | 不自动 GC | 开启，永久保留 |

### 4.4 适用场景

- **CI/CD 自动化清理**：标签保留策略 `latestPushedN=10` + `tag_exclude: release-*, latest` 自动淘汰历史 CI 构建标签，7 天内可将 7000 个标签压缩到 150 个以内
- **生产环境防误覆盖**：标签不可变性保护 `release-*` 和 `latest` 标签，防止'凌晨 hotfix push 摧毁生产'事件（如 3.13 事故的预防）
- **安全合规物理删除**：含高危 CVE（CVSS ≥ 9.0）的镜像通过 API 精确删除整个 Artifact，然后执行 GC 物理清除所有 Layer，确保攻击者无法从 Registry 恢复
- **多架构制品治理**：通过不可变性规则保护 Manifest List 标签，防止子 Manifest 被独立删除导致索引损坏
- **存储成本优化**：定期 Retention + 每周 GC 的组合可将 Harbor 存储增长率从每月 80GB 压降到每月 15GB 以下
- **多环境镜像归档**：Staging/Prod 各环境的镜像通过不同的保留策略差异化处理——Prod 保留更多版本，Staging 快速清理

### 4.5 不适用场景

- **需要永久审计追溯的合规场景**（如 FDA 认证的医疗设备软件）：保留策略不适合——任何自动删除都不可接受。应该将 Harbor 的镜像通过复制规则（第 8 章）同步到一个长期归档的 S3 对象存储（开启对象锁定 Object Lock，WORM 模式），再在 Harbor 中删除
- **标签数量极少（< 50）且团队规模 < 5 人的早期项目**：引入保留策略的配置和维护成本高于手动清理。建议先用 Git Tag + CI 条件判断来管理哪些构建需要推送到 Harbor

### 4.6 注意事项

1. **保留策略的 Cron 表达式与 GC 的执行时间必须错开**：若保留策略和 GC 同时执行，可能导致 GC 扫描到一个 Artifact 的标签正在被 Retention 删除——出现竞态条件（race condition）。建议间隔 2 小时以上
2. **不可变性规则一旦创建，即使是 Harbor 管理员也无法覆盖被保护的标签**——唯一的方法是先删除不可变性规则本身。因此生产环境的不可变性规则创建应采用 IaC + PR Review 流程
3. **GC 执行期间 Harbor 进入只读模式**（约 15-60 分钟，取决于 Blob 数量）——这期间所有 push/pull 都会被挂起排队（不会失败，但会延迟），不建议在业务高峰期执行 GC
4. **删除 Artifact 是不可逆操作**——Harbor 的软删除设计提供 72 小时的"后悔期"（数据库标记 `deleted=true` 但数据仍在），但该时间窗口不可配置。如果需要在 72 小时后恢复，唯一的方法是回滚数据库备份
5. **`latestActiveN` 的正确使用前提是审计日志功能正常**——建议在配置前运行一周的审计日志完整性验证（抽样 100 条 `pull_artifact` 记录 vs 实际 Harbor 访问日志的一致性检查）

### 4.7 常见踩坑经验（真实生产故障案例）

| 故障案例 | 故障时间线 | 根因分析 | 解决方案 | 避免措施 |
|---------|----------|---------|---------|---------|
| **3.13 物流大崩溃（标签覆盖）** | 22:23 运维推送 hotfix 覆盖 `latest` → 次日 8:15 K8s 滚动更新启用新镜像 → 8:20 500 错误率飙升至 23% → 8:57 发现问题并回滚 → 37 分钟 downtime | `latest` 标签未受不可变性规则保护，`imagePullPolicy: Always` 在 K8s 全集群自动拉取未测试镜像 | 对 `latest` 和 `release-*` 加不可变性规则 | 所有生产项目必须配置不可变性规则（与 CI Pipeline 的合规检查集成） |
| **SSD 账单翻倍（保留策略未配 GC）** | 2024Q3 发现存储成本异常 → 排查发现 Retention 已运行 6 个月但 GC 从未执行 → 标签从 15000 降到 3000，但存储从 200GB 升到 800GB | Retention 仅删除标签映射，不释放 Blob 空间。运维团队不知道 GC 是独立步骤 | 立即执行 GC，释放约 520GB → 配置每周日 GC 定时任务 | GC 执行应纳入运维 On-Call 手册作为必读流程 |
| **Manifest List ARM64 指向旧版本** | 灰度发布时 ARM64 Pod 使用的是 4 周前的镜像版本，导致功能异常 → 排查发现 Manifest List 更新时只更新了 AMD64 子 Manifest 引用 | CI 构建流程中 Manifest List 的创建与 ARM64 构建解耦——当 AMD64 构建完成后尝试 `docker manifest create` 但 ARM64 构建尚未完成 | 改造 CI：两个架构并行构建 → 都成功后统一创建 Manifest List → 推送 | CI Pipeline 加固：Manifest List 的创建必须在所有架构构建成功后才执行 |

### 4.8 思考题

1. **一家跨国电商公司有 200 个微服务，每个微服务对应一个 Harbor 仓库。CI 为每个 Git 分支构建并推送一个标签。每月产生的标签数：200 仓库 × 平均 10 活跃分支 × 每天 5 次 Push × 30 天 = 30 万个标签/月。但实际只有 0.5% 的标签（约 1500 个）会被部署到生产环境。请设计一个分层的标签管理策略：① 哪些标签应该设置不可变性？② 保留策略应如何配置（按仓库类型分组）？③ GC 的执行频率应如何决定？④ 如何在不增加运维负担的情况下自动化这一策略？**

2. **假设 `latestActiveN=5` 策略配置在一个金融交易系统的 Harbor 仓库上。该仓库中有一个镜像 `trading-engine:v1.3.0`，推送于 2024 年 6 月，但因为它被一个长期运行的 DaemonSet 引用，Kubelet 每天拉取约 86400 次（每秒 1 次健康检查导致镜像重新拉取——这是 K8s 的一个配置错误）。该镜像在 `latestPushedN` 排序中位于 300 名以外（很早推送），但在 `latestActiveN` 排序中位于第 1 名（最近被拉取）。问：① 如果审计日志因磁盘满而丢失了最近 7 天的数据，latestActiveN 会如何处理这个镜像？② 这个场景暴露了 latestActiveN 的什么设计缺陷？③ 你会如何改进这个策略？**

